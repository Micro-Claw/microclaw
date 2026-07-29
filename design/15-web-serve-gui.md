# 15 — `microclaw serve`: an interactive web GUI

Goal: let a user drive Microclaw from a browser chat window instead of the
terminal REPL, reusing the transcript renderer already built for
`microclaw view-history` (`microclaw/history_viewer.html`).

## TL;DR

Add a `microclaw serve` subcommand that runs a small **FastAPI + uvicorn** app,
bound to `127.0.0.1` only. It owns one live `MicroscopeController` + `SafetyGuard`
+ `history`, serves the existing viewer with a composer bar bolted on, and exposes
two endpoints: `GET /api/history` (initial load) and `POST /api/prompt` (run one
turn via `run_agent`, return the appended messages). Turns are **serialized** with
a lock — one microscope, one operator. Live token/tool streaming is a later
increment that needs `run_agent` refactored into a generator.

We compare this to a **Streamlit** implementation at the end. Recommendation:
**custom FastAPI**, because (a) we already own a nice transcript renderer we'd
throw away under Streamlit, (b) the ZMQ/Java bridge session is process-global and
maps awkwardly onto Streamlit's rerun model, and (c) it integrates as a first-class
`microclaw` subcommand rather than a separate `streamlit run` process.

---

## Why the current viewer can't just "become interactive"

`history_viewer.html` is opened over `file://` and only *reads* an embedded blob.
To send a prompt and get a reply we need a **process** that:

1. holds the live hardware session (`MicroscopeController` opens ZMQ + the Java
   bridge; reconnecting per request is slow and, per CLAUDE.md, risks pyjavaz
   static-class cache collisions if the process is torn down/rebuilt oddly), and
2. calls `run_agent(user_message, ctrl, guard, history, model=...)` →
   `(reply, updated_history)`, then persists via the existing `write_history`.

So the work is a thin web server around the *same* three objects `run_session`
already builds, plus a frontend that POSTs prompts instead of reading `input()`.

---

## Architecture

```
browser (chat UI)  ──HTTP──►  uvicorn / FastAPI (127.0.0.1)
   │  GET  /                    serves history_viewer.html + composer
   │  GET  /api/history         → full history (JSON)
   │  POST /api/prompt {msg}    → run_agent(...)  → appended messages
   └─────────────────────────►  one process, one session:
                                   MicroscopeController + SafetyGuard + history
                                   guarded by an asyncio.Lock (single operator)
```

Key decisions:

- **Localhost only, always.** This endpoint moves real hardware. Bind `127.0.0.1`
  and do not expose `--host 0.0.0.0` without a loud opt-in flag
  (`--allow-remote`) and a warning. A stray bind on a lab network = anyone can
  drive the stage.
- **Single session, serialized.** The controller and `history` are shared mutable
  state and the microscope is physically single-user. An `asyncio.Lock` around
  the agent call means a second prompt waits (or is rejected `409`) instead of
  interleaving tool calls on the hardware.
- **Don't block the event loop.** `run_agent` is synchronous and spends most of
  its time in Anthropic HTTP + ZMQ round-trips (design/13). Run it in a thread via
  `starlette.concurrency.run_in_threadpool` so uvicorn stays responsive.
- **Turn-at-a-time first.** `run_agent` returns only the final reply + full
  history. v1 returns the *slice* appended this turn and the UI re-renders. Live
  streaming (tool calls appearing as they resolve) is a follow-up (see below).

### Server stub (`microclaw/webserve.py`)

```python
import asyncio
import datetime
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from microclaw.agent import run_agent
from microclaw.controller import MicroscopeController
from microclaw.config import load_safety_config
from microclaw.safety import SafetyGuard
from microclaw.__main__ import write_history


class Session:
    """One live microscope + conversation, shared across requests."""

    def __init__(self, args):
        guard = SafetyGuard(load_safety_config(args.safety_config))
        self.ctrl = MicroscopeController(port=args.port, guard=guard)
        if not self.ctrl.is_connected():
            raise SystemExit("Could not connect to Micro-Manager. "
                             "Is the ZMQ server enabled in Tools → Options?")
        self.guard = guard
        self.model = args.model
        self.history: list[dict] = []
        self.history_fn = (f"{datetime.datetime.now():%Y%m%d_%H%M%S}"
                           "_microclaw_history.json")
        self.save = args.save_history
        self.lock = asyncio.Lock()  # one operator at a time


class Prompt(BaseModel):
    message: str


def build_app(session: Session) -> FastAPI:
    app = FastAPI(title="Microclaw")
    page = resources.files("microclaw").joinpath("serve.html").read_text()

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return page  # renderer talks to the API; no embedded blob

    @app.get("/api/history")
    async def get_history():
        return JSONResponse(session.history)

    @app.post("/api/prompt")
    async def post_prompt(p: Prompt):
        msg = p.message.strip()
        if not msg:
            raise HTTPException(400, "Empty message.")
        if session.lock.locked():
            raise HTTPException(409, "A turn is already in progress.")
        async with session.lock:
            start = len(session.history)
            # run_agent is sync + network-bound → offload to a thread
            reply, session.history = await run_in_threadpool(
                run_agent, msg, session.ctrl, session.guard,
                session.history, model=session.model,
            )
            write_history(session.history_fn, session.history, session.save)
            return JSONResponse({"reply": reply,
                                 "appended": session.history[start:]})

    return app
```

### Subcommand wiring (`__main__.py`)

```python
srv = sub.add_parser("serve", help="Serve the interactive web GUI (localhost).")
srv.add_argument("--host", default="127.0.0.1")   # keep localhost-only
srv.add_argument("--web-port", type=int, default=8000)
srv.add_argument("--allow-remote", action="store_true",
                 help="Bind beyond localhost. Exposes microscope control on the "
                      "network — only on a trusted, isolated LAN.")
# ...reuses the shared --safety-config / --port / --model / --save-history flags

# in main():
if args.command == "serve":
    import uvicorn
    from microclaw.webserve import Session, build_app
    if args.host != "127.0.0.1" and not args.allow_remote:
        sys.exit("Refusing to bind beyond localhost without --allow-remote.")
    session = Session(args)
    print(f"Microclaw GUI: http://{args.host}:{args.web_port}")
    uvicorn.run(build_app(session), host=args.host, port=args.web_port)
    return
```

### Frontend: reuse the renderer, add a composer

`history_viewer.html` already has `render(history)`, `toolCard`, `md`, etc. Factor
those pure-render functions into a shared `<script>` block (call it
`transcript.js`, inlined into both pages to stay CSP-/`file://`-friendly). Then
`serve.html` = the same document minus the drop zone, plus:

```html
<form id="composer">
  <textarea id="msg" placeholder="Tell Microclaw what to image…" rows="1"></textarea>
  <button id="send" type="submit">Send</button>
</form>
<script>
  async function boot() {
    render(await (await fetch("/api/history")).json());
  }
  document.getElementById("composer").addEventListener("submit", async (e) => {
    e.preventDefault();
    const box = document.getElementById("msg");
    const text = box.value.trim(); if (!text) return;
    box.value = ""; setBusy(true);            // disable input, show a spinner
    const res = await fetch("/api/prompt", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message: text}),
    });
    if (res.status === 409) { toast("A turn is already running."); }
    else { const {} = await res.json(); render(await (await fetch("/api/history")).json()); }
    setBusy(false);
  });
  boot();
</script>
```

Re-fetching `/api/history` after each turn is the simplest correct approach (the
server is the source of truth). Optimistic append is a later polish.

### Later: live streaming

Turn-at-a-time means the user stares at a spinner for the ~tens of seconds a
multi-tool turn takes (design/13). To stream, refactor the agent loop into a
generator that *yields* each block as it happens:

```python
def run_agent_iter(user_message, ctrl, guard, history, model=None):
    # yield {"type": "text", ...} / {"type": "tool_use", ...} /
    #       {"type": "tool_result", ...} as the loop produces them;
    # return updated history at the end.
```

then expose `GET /api/stream?message=...` as Server-Sent Events and append blocks
to the transcript live. Keep `run_agent` as a thin wrapper that drains the
generator, so the CLI and tests are unchanged. Deferred — v1 ships without it.

---

## Alternative: Streamlit

Streamlit would collapse most of the above into one script:

```python
# microclaw/streamlit_app.py  →  launched by `streamlit run`
import streamlit as st
from microclaw.agent import run_agent
from microclaw.controller import MicroscopeController
from microclaw.config import load_safety_config
from microclaw.safety import SafetyGuard

@st.cache_resource                      # build the session once, reuse across reruns
def get_session():
    guard = SafetyGuard(load_safety_config("safety_config.yaml"))
    ctrl = MicroscopeController(port=4827, guard=guard)
    return ctrl, guard

ctrl, guard = get_session()
if "history" not in st.session_state:
    st.session_state.history = []

st.title("Microclaw")
for m in st.session_state.history:       # our own render → generic bubbles
    render_message(m)                    # tool calls via st.expander(...)

if prompt := st.chat_input("Tell Microclaw what to image…"):
    with st.chat_message("user"):
        st.write(prompt)
    reply, st.session_state.history = run_agent(
        prompt, ctrl, guard, st.session_state.history)
    with st.chat_message("assistant"):
        st.write(reply)
```

### Comparison

| Dimension | FastAPI (proposed) | Streamlit |
|---|---|---|
| Time to first prototype | Medium — write a bit of JS/HTML | **Fast** — `st.chat_input`/`st.chat_message` out of the box |
| New dependencies | `fastapi`, `uvicorn` (light, ASGI) | `streamlit` (heavy: tornado, altair, pyarrow…) |
| Reuses our viewer | **Yes** — same renderer, styling, collapsible tool cards | No — rebuild rendering in Streamlit widgets; tool cards become `st.expander` |
| Subcommand UX | **`microclaw serve`** (native) | `streamlit run …` (separate entrypoint; `serve` would shell out to it) |
| Execution model vs. our state | Explicit, long-lived objects; natural fit | Whole script **reruns** each interaction; the ZMQ/Java session must survive via `@st.cache_resource`; awkward w/ pyjavaz global caches (CLAUDE.md) |
| Concurrency / hardware safety | Explicit `asyncio.Lock`; localhost bind controlled by us | Streamlit serializes within a session but multi-tab/multi-user story is fuzzier; we'd still gate hardware ourselves |
| Localhost binding | We control it (`127.0.0.1` default, opt-in remote) | `--server.address` flag; defaults localhost, but it's *their* server |
| Streaming | Needs SSE + generator refactor (but full control) | `st.write_stream` is ergonomic **if** we expose a generator |
| Styling / polish | Full control (already themed, light/dark) | Constrained to Streamlit's look unless fighting it with CSS hacks |

### Verdict

**FastAPI.** The deciding factors are the sunk investment in a themed transcript
renderer we'd discard under Streamlit, the cleaner `microclaw serve` integration,
and the fact that our session is a long-lived, process-global hardware bridge that
sits uneasily on Streamlit's rerun-the-script model. Streamlit is the right call
for a throwaway internal demo or if we had *no* renderer yet and wanted a chat UI
in an afternoon — it's genuinely faster to stand up — but here it trades away
control and reuse for speed we don't need.

---

## Proposed increments

1. **v1 — turn-at-a-time, thumbnails, API key.** `serve.html` (renderer factored
   out of the viewer + composer), `webserve.py` (`Session` + endpoints + lock),
   `serve` subcommand, `fastapi`/`uvicorn` deps, localhost-only guard. Plus the
   two additions below, both of which are small and carry no hardware risk.
   Manual smoke test against a running MM; unit-test `build_app` with a fake
   session (monkeypatch `run_agent`) via `fastapi.testclient` — no hardware needed.
2. **v2 — safety-config editor.** Its own increment: it is the one feature here
   that can *widen* hardware limits from a browser (see below).
3. **v3 — streaming.** `run_agent_iter` generator, SSE endpoint, live block
   append, busy/typing indicator.
4. **v4 — polish.** Stop/interrupt a running turn, model picker, surface
   `save_position_list` artifacts inline.

### v1a — render thumbnails in the transcript

This is a **viewer bug fix, not a serve feature**, and it should land with the
renderer factoring regardless of whether `serve` ships.

`snap_and_analyze(return_thumbnail=True)` already returns a two-block list — a
text block and an `image` block carrying a base64 PNG (`tools.py`) — and
`run_agent` drops that list verbatim into the `tool_result` content
(`agent.py`). **The thumbnail is therefore already in `history` and already in
every saved history JSON.** Nothing server-side has to change.

What's missing is rendering: `toolCard` pipes `result.content` through
`fmtJSON`, so an image block currently renders as a screenful of base64 inside a
`<pre>`. Teach the shared renderer to walk an array `content`:

```js
// in toolCard(), replacing the single fmtJSON(result.content) call
function renderResult(content) {
  if (!Array.isArray(content)) return '<pre class="json result">' + fmtJSON(content) + '</pre>';
  return content.map(b =>
    b.type === "image"
      ? '<img class="thumb" alt="snap thumbnail" src="data:' +
          esc(b.source.media_type) + ';base64,' + esc(b.source.data) + '">'
      : '<pre class="json result">' + fmtJSON(b.text) + '</pre>'
  ).join("");
}
```

Benefits `microclaw view-history` immediately, and `serve` inherits it for free.

Caveat worth knowing: base64 PNGs bloat the history JSON (a 512 px thumbnail is
a few hundred KB of text per snap). That is already true today — this change only
makes the cost visible. If it bites, strip image blocks in `write_history` and
keep them in the live session only; that's a separate decision.

### v1b — set and persist the API key from the UI

Possible, and it makes the first-run story *better* than the CLI's: `run_agent`
builds its client lazily, so `serve` can boot with no key at all, show a banner,
and collect one.

One refactor is required. `agent._get_client()` caches the client in a module
global (`_client`), so setting a key must reset that global — putting
`ANTHROPIC_API_KEY` in `os.environ` after the first call does nothing. Add:

```python
def set_api_key(key: str) -> None:
    """Set the key and drop the cached client so the next call rebuilds it."""
    global _client
    os.environ["ANTHROPIC_API_KEY"] = key
    _client = None
```

Endpoints: `GET /api/key` → `{"has_key": bool, "suffix": "…AA8f"}` and
`POST /api/key {key}`. Rules:

- **Never echo the key back.** Return a masked suffix only, enough for the user
  to confirm *which* key is set.
- **Persist outside the repo**, never in `safety_config.yaml` — that file is
  meant to be readable and checked in as an example. See storage below.
- Loading order: explicit env var > keyring > stored file > unset (banner).

> **As built, this order applies to `serve` only — the interactive CLI ignores
> stored keys.** Found during the design/32 Block 15 demo gate (2026-07-29).
> `credentials.load_api_key()` is called from `webserve.py` alone;
> `__main__.run_session` — the `microclaw` REPL — never calls it, so it sees only
> `ANTHROPIC_API_KEY` in the environment. A key the operator entered in the
> browser is stored in the keyring and then invisible to the REPL, which fails
> with `TypeError: Could not resolve authentication method` and no hint that a
> usable key exists a few lines of code away. The gate's own spike hit this and
> had to replicate `serve`'s lookup by hand
> (`design/32-block15-compaction-live-spike.py`).
>
> Not a Block 15 defect — pre-existing, and not fixed there. The fix is small
> (call `load_api_key()` + `set_api_key()` in `run_session`, as `Session.__init__`
> does) but it changes what a REPL session will authenticate with, so it belongs
> on its own branch rather than riding along in an unrelated block.

#### Where to store it

Prefer **`keyring`**, which brokers to the OS credential store — macOS Keychain,
Windows Credential Manager, Linux Secret Service. This matters because Microclaw
runs on a Windows lab machine, and the obvious POSIX answer is wrong there:
`os.chmod(path, 0o600)` on Windows only toggles the read-only attribute, it does
not write an ACL, so a `0600` config file remains readable by every account on
the box. "chmod 600" is a reflex that buys nothing where this actually deploys.

`keyring` can be awkward on a headless or locked-down machine, so keep a fallback
to a user-level config file (`~/.config/microclaw/config.toml`,
`%APPDATA%\microclaw\` on Windows) — but describe that file in the UI as
*convenience, not protection*, and `chmod` it on POSIX where that means something.

#### Why not `python-dotenv`?

It solves the other half of the problem. `load_dotenv()` **reads** an existing
`.env` into `os.environ` at startup; v1b needs to **write** a secret submitted at
runtime and have a live process notice. Dotenv does not help with the part that
actually makes the new key take effect — `_client = None` — because
`anthropic.Anthropic()` reads the environment exactly once, at construction, and
`_get_client()` has already cached that client by the time the form is submitted.

Two further mismatches:

- `.env` is resolved **relative to the working directory**. An operator running
  `microclaw serve` from a different data folder each day would find the key
  present in one and absent in the next — the opposite of "store it for later".
  A user-level store follows the user, not the cwd.
- `dotenv.set_key()` writes with default permissions and never chmods, so the
  security work is unchanged; we'd carry a dependency and still hand-roll it.

Where it *would* earn its keep is a repo-root `.env` for local development, so
contributors need not export a variable in every shell. That is a
developer-workflow convenience, not part of this feature, and it is one line at
startup if we ever want it.

### v2 — editing `safety_config.yaml` from the viewer

Possible, but it earns its own increment because it is the only feature in this
document that lets a browser form **widen the limits on real hardware**. The
value of `safety_config.yaml` is that it is a deliberate, out-of-band artifact
somebody edited on purpose; a GUI edit box erodes that by default, so the
guardrails below are the feature, not decoration.

Two mechanical problems, both in the existing code:

- **The guard is aliased and cannot be swapped.** `MicroscopeController` captures
  it at construction (`ctrl._guard`, used in `check_xy` / `check_z`) *and*
  `execute_tool` receives it separately. Rebinding `session.guard` would leave
  the controller enforcing the old limits. So constraints must be replaced **in
  place**, via a real method rather than poking `guard._c`:

  ```python
  class SafetyGuard:
      def replace_constraints(self, c: SafetyConstraints) -> None:
          self._c = c
  ```

- **`SafetyConstraints.from_yaml` takes a path, not a string.** Validate a
  proposed edit by round-tripping it through a temp file, or add a
  `from_yaml_str` classmethod and have `from_yaml` call it.

Guardrails:

- **Not a tool.** These are UI endpoints. The agent must never be able to call
  them — it does not get to widen its own limits.
- **403 when not bound to localhost.** If `--allow-remote` is in effect, config
  editing is disabled outright. Remote hardware control is already a loud opt-in;
  remote *limit* editing is not on offer at all.
- **Validate before apply.** Parse the submitted YAML into `SafetyConstraints`
  first; reject with the parse error and leave the running guard untouched.
- **Back up, then confirm.** Write `safety_config.yaml.bak` before saving, and
  require an explicit confirmation step in the UI showing a diff of what changes.
- **Take `session.lock`.** Never reload constraints mid-turn, while tool calls
  are in flight against the old limits.

```
GET  /api/safety-config   → {"yaml": "...", "path": "...", "editable": bool}
POST /api/safety-config   → validate → .bak → write → guard.replace_constraints()
```

---

## Aside: what if we had *no* viewer yet?

The verdict above leans partly on sunk cost — we already own a themed renderer.
Strip that away and ask the greenfield question honestly: **on a blank repo, is
Streamlit the better choice?** For getting a *working* chat GUI in front of a
microscope operator: **yes, probably.** The reuse argument is the FastAPI column's
strongest card, and without it the balance shifts.

What still holds up on the merits, independent of the existing viewer:

- **Streamlit is dramatically less code to a usable UI.** `st.chat_input` +
  `st.chat_message` + `st.expander` for tool calls gives you a scrollable
  transcript with collapsible tool details in ~40 lines and zero HTML/CSS/JS.
  Reproducing that from scratch in FastAPI is a real frontend chore. If we hadn't
  built the renderer, we'd be *choosing* to take on that chore.
- **`st.write_stream` makes token/tool streaming nearly free** — it consumes a
  generator directly. Our v2 streaming story (the awkward part of the FastAPI
  plan: generator refactor *plus* hand-rolled SSE *plus* client-side append) is
  largely handled by the framework. Given that streaming is where most of Microclaw's
  perceived latency lives (design/13), that's a meaningful pull toward Streamlit.
- **One file, one `run` command, no ASGI wiring.** Lower ongoing maintenance for a
  small research tool with no dedicated frontend owner.

What would still worry me even greenfield — these are *not* reuse arguments:

- **The hardware session vs. the rerun model.** Streamlit re-executes the whole
  script on every interaction. A long-lived ZMQ + Java bridge that must persist
  across those reruns leans entirely on `@st.cache_resource`, and the pyjavaz
  static-class cache is process-global and order-sensitive (CLAUDE.md, design/12).
  That's not fatal — `@st.cache_resource` is exactly the intended escape hatch —
  but it's a sharp edge under the rug, and hardware bugs are expensive to chase.
- **Safety ergonomics.** We hard-gate on `127.0.0.1` and an explicit
  `--allow-remote`. Under Streamlit the bind is *its* server config
  (`--server.address`); enforcing our own refusal-to-expose policy means wrapping
  or documenting around the framework rather than owning the socket.
- **Subcommand integration.** `microclaw serve` shelling out to `streamlit run`
  is workable but always feels bolted on (arg forwarding, process lifecycle,
  Ctrl-C handling).

**Greenfield verdict:** if Microclaw had no viewer and the priority were "a
working, streaming chat GUI operators like, soon," I'd genuinely reach for
Streamlit and accept the `@st.cache_resource` sharp edge — the code savings and
free streaming outweigh the frameworks-owns-the-server friction for a research
tool. I'd switch to FastAPI the moment we wanted (a) tight control over hardware
exposure/binding as a safety property, (b) a bespoke UI beyond Streamlit's idiom,
or (c) clean packaging as a single `microclaw` subcommand. In *this* repo we
already have the renderer and we care about all three, so FastAPI wins — but that
conclusion is specific to where we are, not a general knock on Streamlit.
