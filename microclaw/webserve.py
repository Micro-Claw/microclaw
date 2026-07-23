"""`microclaw serve` — the interactive web GUI (design/15).

One process owns one live `MicroscopeController` + `SafetyGuard` + history, the
same three objects the terminal REPL builds. The browser POSTs a prompt and the
server streams that turn's events back on the POST response as Server-Sent
Events (design/16 §3); `/api/history` stays the source of truth and reconciles
the page once the stream ends.

Three properties are load-bearing, because this endpoint moves real hardware:

* **Localhost only.** Binding beyond 127.0.0.1 requires `--allow-remote`; on a
  lab network a stray bind means anyone can drive the stage.
* **One operator.** The controller and history are shared mutable state and the
  microscope is physically single-user, so a turn holds `session.lock` and a
  second prompt is refused (409) rather than interleaved.
* **State-changing endpoints stay on POST.** Streaming a turn from a GET would
  let any page the operator has open drive the stage with an `<img src=...>`:
  a GET is not preflighted and carries no `Origin` for the middleware to refuse.
  That is why this is POST-SSE rather than an `EventSource`.
"""
import asyncio
import datetime
import json
import queue
import socket
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from microclaw import credentials
from microclaw.authorization import RigAuthorizationError, validate_live_rig
from microclaw.agent import (
    DEFAULT_MODEL,
    known_models,
    resolve_model,
    run_agent_iter,
    set_api_key,
)
from microclaw.assets import icon_bytes, load_page
from microclaw.config import load_safety_config_or_exit
from microclaw.controller import MicroscopeController
from microclaw.safety import SafetyGuard, SafetyViolation

# Loopback names. Anything else needs --allow-remote.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Sentinel: the turn thread is finished and the SSE stream should end.
_TURN_DONE = object()

# A confirmation nobody answers must never become a yes. Module attributes,
# read at call time, so tests can exercise the deadline with an injected clock
# and a short poll rather than a sleep.
CONFIRM_TIMEOUT_S = 300.0
CONFIRM_POLL_S = 0.5
_monotonic = time.monotonic


class _Pending:
    """One confirmation waiting on the operator, readable by /api/confirm."""

    def __init__(self, id: str, summary: str, kind: str):
        self.id = id
        self.summary = summary
        self.kind = kind
        # threading queue, not asyncio: confirm() blocks on the turn thread
        # while /api/confirm answers from the event loop.
        self.reply: queue.Queue = queue.Queue(maxsize=1)


class Prompt(BaseModel):
    message: str


class Confirm(BaseModel):
    id: str
    approve: bool


class Key(BaseModel):
    key: str
    persist: bool = True


class Model(BaseModel):
    model: str


def _jsonable(history: list[dict]) -> list[dict]:
    """History holds Anthropic SDK content blocks, which json can't encode.

    Round-trip through the same `default` hook `write_history` uses, so the API
    and the saved JSON agree block for block.
    """
    from microclaw.__main__ import json_default

    return json.loads(json.dumps(history, default=json_default))


def _declared_artifacts(history: list[dict]) -> set[str]:
    """Every path a tool in this session declared as an artifact.

    The allowlist behind `/api/artifact`. Tools that write a file return
    `{"artifact": {"kind": ..., "path": ...}}`; those dicts are built by
    microclaw's own code from the path the tool actually wrote, so the model
    cannot name a file here that no tool produced.
    """
    paths: set[str] = set()
    for message in history:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                continue
            payload = block.get("content")
            if isinstance(payload, list):
                # Image-returning tools send [text block, image block]; the JSON
                # payload, if any, is in the text block.
                payload = next(
                    (b.get("text") for b in payload
                     if isinstance(b, dict) and b.get("type") == "text"),
                    None,
                )
            if not isinstance(payload, str):
                continue
            try:
                result = json.loads(payload)
            except ValueError:
                continue
            artifact = result.get("artifact") if isinstance(result, dict) else None
            if isinstance(artifact, dict) and isinstance(artifact.get("path"), str):
                paths.add(artifact["path"])
    return paths


def _sse(event: dict) -> str:
    """One agent event as an SSE frame.

    `json.dumps` never emits a raw newline, so a single `data:` line is safe.
    """
    from microclaw.__main__ import json_default

    return f"data: {json.dumps(event, default=json_default)}\n\n"


class Session:
    """One live microscope + conversation, shared across requests."""

    def __init__(self, args):
        self.parsed_safety = load_safety_config_or_exit(args.safety_config)
        guard = SafetyGuard(self.parsed_safety.constraints)
        print("Connecting to Micro-Manager...")
        ctrl = MicroscopeController(port=args.port, guard=guard)
        if not ctrl.is_connected():
            sys.exit(
                "Could not connect to Micro-Manager. "
                "Is the ZMQ server enabled in Tools → Options?"
            )
        try:
            validate_live_rig(ctrl, self.parsed_safety, guard=guard)
        except RigAuthorizationError as exc:
            sys.exit(str(exc))
        self.ctrl = ctrl
        self.guard = guard
        self.model = args.model
        self.history: list[dict] = []
        self.history_fn = (
            f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_microclaw_history.json"
        )
        self.save = args.save_history
        # Editing credentials from a browser is offered only on loopback, the
        # same rule design/15 sets for the safety-config editor (v2).
        self.editable = args.host in LOCAL_HOSTS
        self.lock = asyncio.Lock()  # one operator at a time
        # Set by POST /api/stop, polled by run_agent_iter at round and tool
        # boundaries. threading.Event, not asyncio: the turn runs on a thread.
        self.cancel = threading.Event()
        # The browser confirmation gate (design/21 F1). _emit is set per turn
        # by run_turn; pending is read by GET /api/confirm so a page reload can
        # re-surface a banner the one-shot stream already delivered.
        self._emit = None
        self.pending: _Pending | None = None

        # env > keyring > file; a key found in a store is pushed into the
        # environment now so the first turn doesn't have to look for it.
        key, source = credentials.load_api_key()
        if key:
            set_api_key(key)
            print(f"Anthropic API key: {credentials.mask(key)} (from {source})")
        else:
            print("No Anthropic API key found — set one from the browser.")

    def confirm(self, summary: str, kind: str = "action") -> bool:
        """Route a confirmation to the browser. Runs on the turn thread.

        Installed as tools.CONFIRM_FN by serve(), because the operator of a
        browser session is looking at the browser — a blocking input() on the
        serve process's stdin waits on a console nobody is watching
        (design/21 F1). Default deny, three ways: no stream bound, the Stop
        button, or the deadline. stdout keeps the summary and the decision —
        under --allow-remote it is the only record the person standing at the
        microscope can see.
        """
        emit = self._emit
        if emit is None:
            return False                                   # no stream: deny
        p = _Pending(uuid.uuid4().hex, summary, kind)
        self.pending = p
        print(f"\n[microclaw] Confirmation required ({kind}):\n{summary}")
        emit({"type": "confirm_request", "id": p.id,
              "summary": summary, "kind": kind})
        try:
            deadline = _monotonic() + CONFIRM_TIMEOUT_S
            while _monotonic() < deadline:
                if self.cancel.is_set():
                    print("[microclaw] Turn stopped; confirmation declined.")
                    return False                           # Stop button: deny
                try:
                    answer = bool(p.reply.get(timeout=CONFIRM_POLL_S))
                except queue.Empty:
                    continue
                print(f"[microclaw] {'Approved' if answer else 'Declined'}"
                      f" from browser.")
                return answer
            print("[microclaw] Confirmation timed out; declined.")
            return False                                   # deadline: deny
        finally:
            self.pending = None
            emit({"type": "confirm_resolved", "id": p.id})


def build_app(session) -> FastAPI:
    from microclaw.__main__ import write_history

    app = FastAPI(title="Microclaw")
    page = load_page("serve.html")
    icon = icon_bytes()

    @app.middleware("http")
    async def block_cross_origin(request: Request, call_next):
        """Any page the operator visits can POST to a localhost server.

        Browsers preflight a cross-origin JSON POST and we send no CORS headers,
        so those are already refused — but a form post with a simple content
        type is not preflighted. Reject anything carrying a foreign Origin so a
        stray tab cannot drive the microscope.
        """
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin request refused."}, status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return page

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        # Read once at startup, not per request; the file cannot change while
        # the process lives. `image/x-icon` is what every browser expects here.
        return Response(icon, media_type="image/x-icon")

    @app.get("/api/history")
    async def get_history():
        return JSONResponse(_jsonable(session.history))

    @app.post("/api/prompt")
    async def post_prompt(p: Prompt):
        msg = p.message.strip()
        if not msg:
            raise HTTPException(400, "Empty message.")
        if credentials.load_api_key()[0] is None:
            raise HTTPException(400, "No Anthropic API key is set.")
        if session.lock.locked():
            raise HTTPException(409, "A turn is already in progress.")
        # Acquired here, not inside events(): the response body is not iterated
        # until after this handler returns, so a lock taken there would leave a
        # window in which a second prompt passes the check above.
        await session.lock.acquire()
        session.cancel.clear()

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def emit(event):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                pass  # Ctrl-C closed the loop mid-turn; serve()'s finally saves.

        def run_turn():
            """The whole turn, on a thread of its own.

            Deliberately NOT `iterate_in_threadpool`: that closes the generator
            when the client disconnects, raising GeneratorExit at whichever yield
            the turn had reached. Stop there mid-round and the history keeps an
            assistant message whose tool_use block has no tool_result — which the
            Messages API rejects on the *next* prompt, from a history that looks
            fine in the viewer. The stage has already moved; finish the turn.

            run_agent_iter is synchronous and blocks on Anthropic HTTP and ZMQ
            round-trips, so it must stay off the event loop regardless.
            """
            # Session.confirm routes through whichever stream the running turn
            # owns; only one turn runs at a time (session.lock), so there is no
            # second emit to confuse.
            session._emit = emit
            try:
                for event in run_agent_iter(
                    msg, session.ctrl, session.guard, session.history, session.model,
                    cancel=session.cancel,
                ):
                    emit(event)
            except Exception as e:  # noqa: BLE001 — the stream is the only channel
                emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
            finally:
                session._emit = None
                # session.history holds the completed rounds either way —
                # run_agent_iter appends to it in place.
                try:
                    write_history(session.history_fn, session.history, session.save)
                except Exception as e:  # noqa: BLE001
                    print(f"[microclaw] Could not write history: {e}", file=sys.stderr)
                emit(_TURN_DONE)
                loop.call_soon_threadsafe(session.lock.release)

        threading.Thread(target=run_turn, name="microclaw-turn", daemon=True).start()

        async def events():
            # A client that vanishes leaves this generator closed and the worker
            # thread running; it finishes the turn, saves, and releases the lock.
            while True:
                event = await queue.get()
                if event is _TURN_DONE:
                    return
                yield _sse(event)

        # No compression middleware on this app: GZipMiddleware buffers the
        # stream and the events all arrive at the end, in one lump.
        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/stop")
    async def post_stop():
        """Ask the running turn to stop at the next round or tool boundary.

        Cooperative, and honest about it: `execute_tool` blocks in Java and there
        is no interrupting `run_timelapse` halfway. The button says "Stop after
        the current step" for that reason.

        A UI endpoint, never a tool — the agent must not be able to call it, and
        it is not in TOOL_REGISTRY. There is deliberately no hardware-halt
        companion: pyjavaz serializes every bridge call, so a halt would queue
        behind the tool call it means to interrupt (design/16 §6).
        """
        if not session.lock.locked():
            raise HTTPException(409, "No turn is running.")
        session.cancel.set()
        return JSONResponse({"stopping": True})

    @app.get("/api/confirm")
    async def get_confirm():
        """The pending confirmation, if any — or {}.

        Not optional: confirm_request is delivered exactly once, over a stream
        a page reload destroys. Without this, a refresh at the wrong moment
        strands the turn until the deadline. serve.html fetches it on load.
        """
        p = session.pending
        if p is None:
            return JSONResponse({})
        return JSONResponse({"id": p.id, "summary": p.summary, "kind": p.kind})

    @app.post("/api/confirm")
    async def post_confirm(c: Confirm):
        """Answer the pending confirmation.

        Runs on the event loop, so it cannot be blocked by the turn thread that
        is waiting on it. Matches on id: a stale banner from a previous confirm
        must not answer the current one.
        """
        p = session.pending
        if p is None or p.id != c.id:
            raise HTTPException(409, "No confirmation with this id is pending.")
        p.reply.put(c.approve)
        return JSONResponse({"resolved": True})

    @app.get("/api/model")
    async def get_model():
        # known_models() is a blocking HTTP call the first time it is asked, and
        # this endpoint runs on page load — off the event loop.
        return JSONResponse({
            "model": resolve_model(session.model),
            "default": DEFAULT_MODEL,
            "available": await run_in_threadpool(known_models),
            "editable": session.editable,
        })

    @app.post("/api/model")
    async def post_model(m: Model):
        if not session.editable:
            raise HTTPException(
                403, "Bound beyond localhost: set --model or MICROCLAW_MODEL and restart."
            )
        name = m.model.strip()
        if not name:
            raise HTTPException(400, "Empty model id.")
        if session.lock.locked():
            raise HTTPException(409, "A turn is in progress.")
        # Never swap models mid-turn. Session-only: --model and MICROCLAW_MODEL
        # stay the durable knobs.
        async with session.lock:
            session.model = name
        return JSONResponse({"model": resolve_model(session.model)})

    @app.get("/api/artifact")
    async def get_artifact(path: str):
        """Download a file a tool wrote, and nothing else.

        The allowlist is *capability-based, not location-based*: a path is
        servable iff some tool in this session declared it as an artifact. Those
        declarations are built by our own code from the path the tool actually
        wrote, live in `session.history`, and the model cannot forge one.

        This is tighter than the directory sandbox it replaces. A workspace root
        would let the browser fetch any file beneath it, including ones no tool
        ever touched; an exact match lets it fetch only what microclaw just
        wrote. And it needs no configuration, so downloading an artifact never
        constrains where the operator may save data.
        """
        if not session.editable:  # loopback only
            raise HTTPException(403, "Not available when bound beyond localhost.")
        if path not in _declared_artifacts(session.history):
            raise HTTPException(403, "Not an artifact produced by this session.")
        try:
            # A configured workspace still applies — this endpoint may not be a
            # way around it — but it is no longer what authorises the download.
            resolved = session.guard.resolve_in_workspace(path)
        except SafetyViolation as e:
            raise HTTPException(403, str(e))
        if not Path(resolved).is_file():
            raise HTTPException(404, "No such artifact.")
        name = Path(resolved).name
        # Always octet-stream + attachment, never a sniffed type: an artifact
        # that happens to be HTML, served inline from http://127.0.0.1:8000, is
        # same-origin script execution against the endpoint driving the stage.
        return FileResponse(
            resolved,
            media_type="application/octet-stream",
            filename=name,
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/api/key")
    async def get_key():
        key, source = credentials.load_api_key()
        return JSONResponse(
            {
                "has_key": key is not None,
                "suffix": credentials.mask(key) if key else None,
                "source": source,
                "editable": session.editable,
            }
        )

    @app.post("/api/key")
    async def post_key(k: Key):
        if not session.editable:
            raise HTTPException(
                403, "Bound beyond localhost: set ANTHROPIC_API_KEY and restart."
            )
        key = k.key.strip()
        if not key:
            raise HTTPException(400, "Empty key.")
        # Overwrites whatever was set, so a key can be swapped mid-session.
        set_api_key(key)
        stored_in = stored_at = None
        stale_store = False
        if k.persist:
            stored_in, stored_at = credentials.store_api_key(key)
        else:
            # A key held for this process only doesn't displace an older one in
            # the credential store — that one comes back on the next start.
            stored, _ = credentials.load_stored_key()
            stale_store = stored is not None and stored != key
        # Never echo the key: a suffix is enough to confirm which one is set.
        return JSONResponse(
            {
                "has_key": True,
                "suffix": credentials.mask(key),
                "source": "env",
                "editable": True,
                "stored_in": stored_in,
                "stored_at": stored_at,
                "stale_store": stale_store,
            }
        )

    return app


def _open_when_ready(
    host: str, port: int, url: str, timeout: float = 15.0
) -> threading.Thread:
    """Open `url` in a browser once the server is accepting connections.

    uvicorn.run() blocks, and a browser fired before the socket is listening
    lands on a connection-refused page. Poll the port from a daemon thread
    instead of hooking the ASGI lifespan, so a browser that never opens (headless
    box, no BROWSER) can't wedge the server.

    Returns the daemon thread. serve() ignores it; tests join it, because a
    poll thread cannot be observed by sleeping for a fixed interval and hoping
    it was scheduled — under load on a busy box, it is not.
    """

    def wait():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            return  # never came up; the traceback uvicorn prints is the real story
        try:
            webbrowser.open(url)
        except Exception:
            pass  # no browser here — the URL is already printed

    thread = threading.Thread(target=wait, daemon=True)
    thread.start()
    return thread


def serve(args):
    """Entry point for the `serve` subcommand."""
    if args.host not in LOCAL_HOSTS and not args.allow_remote:
        sys.exit(
            f"Refusing to bind {args.host}: this endpoint moves real hardware. "
            "Pass --allow-remote if you truly mean to expose it."
        )
    import uvicorn

    from microclaw import tools
    from microclaw.__main__ import write_history

    session = Session(args)
    # Route every in-code confirmation gate (save_knowledge, hook save, the
    # illumination enable) to the browser, where the operator is. Installed
    # once, not per turn: all three callsites read the module global at call
    # time, so a single assignment covers them (design/21 F1). The CLI keeps
    # the stdin default — a terminal is present there by definition.
    tools.CONFIRM_FN = session.confirm
    app = build_app(session)

    if args.allow_remote and args.host not in LOCAL_HOSTS:
        print(
            f"\n!! Microclaw is reachable at http://{args.host}:{args.web_port} — "
            "anyone who can reach this port can drive the microscope.\n"
        )
    url = f"http://{args.host}:{args.web_port}"
    print(f"Microclaw GUI: {url}  (Ctrl-C to stop)")
    if not args.no_browser:
        # A wildcard bind is not an address a browser (or Windows' connect())
        # can reach; the loopback the server is also listening on is.
        visit = "127.0.0.1" if args.host == "0.0.0.0" else args.host
        _open_when_ready(visit, args.web_port, f"http://{visit}:{args.web_port}")

    # Mirrors run_session: every exit path — Ctrl-C, a crash in a turn — writes
    # the history and shutters known illumination (design/14 §3).
    try:
        uvicorn.run(app, host=args.host, port=args.web_port, log_level="warning")
    finally:
        write_history(session.history_fn, session.history, session.save)
        shuttered = session.guard.shutter_all(session.ctrl.core)
        if shuttered:
            print(f"[microclaw] Illumination off: {', '.join(shuttered)}")
