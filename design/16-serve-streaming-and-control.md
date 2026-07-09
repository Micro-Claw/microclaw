# 16 — `microclaw serve` v3 + v4: streaming, interrupt, model, artifacts

Builds on design/15, which shipped v1 (turn-at-a-time GUI), v1a (thumbnails in
the transcript) and v1b (API key from the browser). v2 (the safety-config editor)
is still unimplemented and is **not** a prerequisite for anything here — it
touches the guard; this document touches the agent loop.

## TL;DR

**v3** turns `run_agent` inside out into a generator, `run_agent_iter`, that
yields one event per thing that happens — a text delta, a tool call, a tool
result. `run_agent` becomes a four-line drain of that generator, so the CLI and
every existing test are untouched. The browser gets those events over
Server-Sent Events **on the POST**, not on a GET, and renders them as they land.

**v4** is what the generator makes cheap: a **Stop** button (cooperative cancel
at round and tool boundaries), a **model picker**, and **artifact links** for the
files tools write. A fourth item — an always-live **hardware halt** that stops the
stage and then shutters illumination — was specified, spiked twice, and is now
**cancelled**: pyjavaz holds one lock across every bridge round trip, so a halt
request *queues behind the tool call it means to interrupt* instead of preempting
it (§6). It would have looked like a working kill switch and done nothing. The
other three are safe to build now.

Ship v3 and v4 together. They are not independent: you cannot cancel a turn you
cannot observe, and the interrupt path forces a correctness question — what
happens to a `tool_use` block whose tool never ran — that is better answered
while the generator is being written than bolted on after.

---

## 1. Where the latency actually is

design/13 measured a 3×3 grid survey at 52 s: **25 sequential `messages.create`
calls at ~2.0 s each**, with 94.6 % of wall time blocked on
`api.anthropic.com`. Micro-Manager did not appear in the top 20 profile entries.

That number decides what streaming is *for* here. Token-by-token text is the
smallest part of the win — most of those 25 rounds emit a `tool_use` block and
almost no prose. What the operator actually wants to watch is **the tool cards
appearing one at a time**: `move_stage_xy` landing, then `mark_position`, then
`snap_image`, each with its result, while the stage is physically moving in
front of them. Today they get a spinner for 52 s and then the whole transcript
at once.

So the event stream must carry tool lifecycle, not just text. Token deltas come
along for free — `client.messages.stream()` gives both from one call — so we take
them, but they are not the reason to do this.

---

## 2. The generator refactor (`agent.py`)

### Event vocabulary

One dict per event, JSON-encodable, `type` discriminated:

| `type` | Fields | Emitted when |
|---|---|---|
| `round_start` | `iteration` | Each pass through the loop, before the API call |
| `text_delta` | `text` | Each `text_delta` from the SDK stream |
| `tool_use` | `id`, `name`, `input` | The model's assistant turn is complete and a tool is about to run |
| `tool_result` | `tool_use_id`, `content`, `is_error` | That tool returned (or raised — `execute_tool` never raises) |
| `retry` | `delay`, `attempt` | HTTP 529, before sleeping |
| `cancelled` | `reason` | The operator hit Stop and the loop is unwinding |
| `done` | `reply` | Terminal. Carries the same string `run_agent` returns today |
| `error` | `message` | Terminal. Overload exhausted, iteration cap hit, unexpected `stop_reason` |

Note `tool_use` fires *after* the assistant turn completes, not on the SDK's
`content_block_start`. Partial tool input JSON is not useful to render and the
tool has not run yet; waiting until we have the parsed `input` costs nothing and
means the event carries the same dict the tool card already knows how to draw.

### History is mutated in place

**Does this break `write_history`?** No — because `run_agent` copies before it
delegates (`messages = list(history or [])`), so `_repl` still receives a fresh
list, still does `history[:] = new_history`, and `run_session`'s `finally` still
writes the list it has held all along. `write_history`'s `json_default` /
`model_dump()` path is untouched: the messages contain the same SDK block objects
they always did.

Three consequences are worth stating rather than discovering:

* **The REPL keeps its current crash semantics.** If a turn raises, `_repl` never
  reaches `history[:] = new_history`, so the `finally` writes the history as of
  the *last completed* turn and the crashed turn's tool calls are lost from the
  file — even though they really happened on the hardware. That is today's
  behaviour, and copying preserves it exactly.
* **`serve` does better, because it passes `session.history` straight to
  `run_agent_iter`.** A turn that crashes or is abandoned mid-flight leaves its
  completed rounds in the list, and the `finally` in §3 writes them. This is the
  asymmetry that motivates the whole design, and it argues for a small follow-on:
  **`run_session` should call `run_agent_iter` directly too**, passing its live
  `history` list. It gets crash-preserving history and streamed terminal output
  for about six lines, and `run_agent` is then only used by tests. Out of scope
  here, worth doing next.
* **`del messages[start:]` on overload now deletes from the caller's list** in the
  `serve` path. That is correct — it reproduces `run_agent`'s current "discard the
  turn" behaviour — but it is the one place the generator *removes* from a list it
  does not own, so it gets a test (§9).

One more thing the in-place list changes: `GET /api/history` no longer holds the
lock against a running turn, so it can now observe a **mid-turn snapshot** — an
assistant message whose `tool_use` block has no matching `tool_result` yet.
`_jsonable` serializes it fine, and `render()` already draws that as a tool card
reading "(no result recorded)". This is harmless, and it is what makes a
reconnecting or second tab show something sensible instead of nothing.

`run_agent` currently builds `messages = list(history or [])` and returns the new
list. The generator instead **appends to the caller's list as it goes**. This is
the design decision the whole SSE transport rests on:

* A browser that closes its tab mid-turn must not lose the turn. The stage has
  already moved; the acquisition is already on disk. `session.history` has to
  reflect that whether or not anyone was listening.
* Python swallows a generator's `return` value into `StopIteration.value`, and
  `starlette.concurrency.iterate_in_threadpool` — the thing that bridges our
  synchronous generator onto the event loop — discards it. There is no clean way
  to get a final `messages` list *out* of a threadpooled generator. Mutating the
  list the caller already holds sidesteps that entirely.
* It matches `_repl`, which already does `history[:] = new_history` for the same
  reason (so `run_session`'s `finally` block sees the latest turns).

`run_agent` keeps its exact signature and semantics by copying first:

```python
def run_agent(user_message, ctrl, guard, history=None, model=None,
              max_iterations=DEFAULT_MAX_ITERATIONS):
    """Run one user turn. Returns (assistant_text_reply, updated_history).

    A thin drain of run_agent_iter — every behaviour lives there, so the CLI,
    the tests and the streaming endpoint cannot diverge.
    """
    messages = list(history or [])          # copy: callers still get a fresh list
    reply = ""
    for event in run_agent_iter(user_message, ctrl, guard, messages, model,
                                max_iterations):
        if event["type"] in ("done", "error"):
            reply = event.get("reply") or event["message"]
    return reply, messages
```

### The loop

```python
def run_agent_iter(user_message, ctrl, guard, messages, model=None,
                   max_iterations=DEFAULT_MAX_ITERATIONS, cancel=None):
    """Run one user turn, yielding an event per thing that happens.

    APPENDS TO `messages` IN PLACE. The caller keeps ownership; a consumer that
    disconnects mid-turn still leaves the completed rounds in the caller's list.

    `cancel` is an optional threading.Event. It is polled at round boundaries and
    before each tool dispatch — never mid-tool. See design/16 §6.
    """
    model = resolve_model(model)
    start = len(messages)
    messages.append({"role": "user", "content": user_message})

    system_blocks = _system_blocks()          # extracted from run_agent, unchanged

    for iteration in range(max_iterations):
        if _cancelled(cancel):
            yield from _unwind_cancel(messages, start)
            return
        yield {"type": "round_start", "iteration": iteration}

        try:
            response = yield from _stream_one_round(messages, system_blocks, model)
        except _Overloaded:
            del messages[start:]              # discard the turn, as run_agent does today
            yield {"type": "error", "message":
                   "The Anthropic API is currently overloaded (HTTP 529). "
                   "Please try again in a few minutes."}
            return

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            yield {"type": "done", "reply": text}
            return

        if response.stop_reason != "tool_use":
            yield {"type": "error",
                   "message": f"[Unexpected stop reason: {response.stop_reason}]"}
            return

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            yield {"type": "tool_use", "id": block.id, "name": block.name,
                   "input": block.input}
            if _cancelled(cancel):
                # Every tool_use block in this assistant turn MUST get a matching
                # tool_result or the next request 400s. Synthesize one. §6.
                result = json.dumps({"error": "Cancelled by the operator."})
                is_error = True
            else:
                result = execute_tool(block.name, block.input, ctrl, guard)
                is_error = False
            tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                 "content": result})
            yield {"type": "tool_result", "tool_use_id": block.id,
                   "content": result, "is_error": is_error}
        messages.append({"role": "user", "content": tool_results})

    yield {"type": "error", "message":
           f"Stopped after {max_iterations} tool rounds without completing. ..."}
```

The overload retry moves into `_stream_one_round`, which is where the SDK call
now lives. Streaming does not change when 529 surfaces — `messages.stream()`
issues the request on `__enter__`, so `OverloadedError` still comes out of the
same place, and the existing `(5, 15, 30)` backoff is preserved verbatim:

```python
def _stream_one_round(messages, system_blocks, model):
    """One model call, streamed. Yields text_delta events; returns the final
    Message. Raises _Overloaded when the retries are spent."""
    for attempt, delay in enumerate([0, *_RETRY_DELAYS]):
        if delay:
            yield {"type": "retry", "delay": delay, "attempt": attempt}
            time.sleep(delay)
        try:
            with _get_client().messages.stream(
                model=model, max_tokens=4096, system=system_blocks,
                tools=TOOLS_CACHED, messages=_with_cache_breakpoint(messages),
            ) as stream:
                for event in stream:
                    if (event.type == "content_block_delta"
                            and event.delta.type == "text_delta"):
                        yield {"type": "text_delta", "text": event.delta.text}
                return stream.get_final_message()
        except anthropic._exceptions.OverloadedError:
            if attempt == len(_RETRY_DELAYS):
                raise _Overloaded
```

Two things to note. `messages.stream()` accumulates the message for you, so
`get_final_message()` returns exactly the object `messages.create()` returned
before — `.content`, `.stop_reason`, SDK block objects and all. Nothing
downstream of the call site changes, including `write_history`'s `model_dump()`
serialization. And breaking out of the `for event in stream` loop (which is how
cancellation aborts a half-generated response) closes the HTTP connection on
`__exit__`; there is no separate abort call to make.

---

## 3. Transporting the events: POST-SSE, not `EventSource`

design/15 proposed `GET /api/stream?message=...` consumed by `EventSource`. **Do
not do this.** It is a stage-driving CSRF hole.

`webserve.build_app` already refuses foreign-`Origin` requests, and that is
sufficient today because every state-changing endpoint is a JSON POST — which
browsers preflight cross-origin. But a **GET is not preflighted and, from most
elements, carries no `Origin` header at all**. Any page the operator has open in
another tab can write:

```html
<img src="http://127.0.0.1:8000/api/stream?message=set+laser+power+to+100">
```

The browser issues a same-machine GET with no `Origin`, our middleware waves it
through, and the microscope runs a turn. The attacker cannot *read* the response
— that is what CORS protects — but they never needed to.

The rule this enforces: **state-changing endpoints stay on POST with a JSON
content type.** Streaming therefore has to happen on the POST response. That
rules out `EventSource` (GET-only, no request body, no custom headers) and means
reading the body with `fetch` + a `ReadableStream` reader. The wire format can
still be SSE; we just parse it ourselves, in about fifteen lines.

### Server

```python
from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool

def _sse(event: dict) -> str:
    # json.dumps never emits a raw newline, so one data: line per event is safe.
    return f"data: {json.dumps(event, default=json_default)}\n\n"

@app.post("/api/prompt")
async def post_prompt(p: Prompt):
    msg = p.message.strip()
    if not msg:
        raise HTTPException(400, "Empty message.")
    if credentials.load_api_key()[0] is None:
        raise HTTPException(400, "No Anthropic API key is set.")
    if session.lock.locked():
        raise HTTPException(409, "A turn is already in progress.")

    async def events():
        # Held for the whole stream: one microscope, one operator. Released in
        # `finally` so a client that vanishes mid-turn cannot wedge the session.
        async with session.lock:
            session.cancel.clear()
            try:
                gen = run_agent_iter(msg, session.ctrl, session.guard,
                                     session.history, session.model,
                                     cancel=session.cancel)
                # run_agent_iter is synchronous and blocks on HTTP + ZMQ; off the
                # event loop, or uvicorn stops answering /api/stop.
                async for event in iterate_in_threadpool(gen):
                    yield _sse(event)
            finally:
                # Runs on client disconnect too. The turn's completed rounds are
                # already in session.history — the generator mutates it in place.
                write_history(session.history_fn, session.history, session.save)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store",
                                      "X-Accel-Buffering": "no"})
```

Three details that will otherwise bite:

* **`iterate_in_threadpool` is the whole reason the in-place mutation matters.**
  It hands each yielded item to the loop and drops the generator's return value.
  Nothing comes back except events.
* **The lock must be released on disconnect.** `StreamingResponse` closes the
  async generator when the client goes away, which raises `GeneratorExit` at the
  `yield` — the `async with` and the `finally` both run. Without them, one closed
  tab leaves the session refusing every subsequent prompt with 409.
* **A disconnect does not stop the turn.** The threadpooled generator keeps
  running to completion (the stage is mid-move; killing it would be worse than
  finishing it), it just has nobody to yield to. It ends, `finally` writes the
  history, and the next `GET /api/history` reconciles the page. Stopping a turn
  is an explicit act — §6 — not a side effect of closing a laptop lid.

### Correction, found while implementing: `iterate_in_threadpool` *does* stop the turn

The third bullet is wrong about the mechanism, and the sketch above would ship
the bug §5 spends a page warning about. `iterate_in_threadpool` holds the
synchronous generator in a local; when the client disconnects, `StreamingResponse`
closes the async generator, that frame unwinds, the last reference to the sync
generator drops, and CPython closes it — raising `GeneratorExit` **inside
`run_agent_iter`, at whichever `yield` the turn had reached.**

If that yield is the `tool_use` event, the turn dies between the model asking for
a tool and the `tool_results` message being appended. `session.history` keeps an
assistant turn whose `tool_use` block has no matching `tool_result` — a
conversation the Messages API rejects with a 400 on the *next* prompt, from a
history that looks perfectly fine in the viewer. Exactly the orphaned-`tool_use`
trap of §5, arrived at from the other direction, and triggered by closing a tab
rather than by pressing Stop.

So the turn runs on **a thread of its own**, not on the threadpool, and the
events reach the event loop through an `asyncio.Queue`:

```python
loop, queue = asyncio.get_running_loop(), asyncio.Queue()
emit = lambda ev: loop.call_soon_threadsafe(queue.put_nowait, ev)

def run_turn():
    try:
        for event in run_agent_iter(msg, session.ctrl, session.guard,
                                    session.history, session.model):
            emit(event)
    except Exception as e:
        emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
    finally:
        write_history(session.history_fn, session.history, session.save)
        emit(_TURN_DONE)
        loop.call_soon_threadsafe(session.lock.release)

threading.Thread(target=run_turn, name="microclaw-turn", daemon=True).start()

async def events():
    while (event := await queue.get()) is not _TURN_DONE:
        yield _sse(event)
```

Now a vanished client leaves `events()` closed and the worker untouched: it
finishes the turn, writes the history, and releases the lock. Which is what the
bullet promised. Two knock-on points:

* **The lock is acquired in the handler, before `StreamingResponse` is
  returned** — not inside `events()`. The response body is not iterated until
  after the handler returns, so a lock taken there leaves a window in which a
  second `POST /api/prompt` sails past the `locked()` check and a second turn
  starts on the same microscope. It must also be released via
  `call_soon_threadsafe`: `asyncio.Lock` is not thread-safe.
* **Nothing may close `run_agent_iter` early.** That is now an invariant of the
  module, and it has a test (`test_closing_the_generator_mid_round_orphans_a_tool_use`)
  that pins the failure so the next person meets it as a red assertion rather
  than as a 400 on a real rig. When v4a lands, `_unwind_cancel` is what makes an
  early exit legal — via the `cancel` event, not via `close()`.

Do not add compression middleware to this app; a `GZipMiddleware` will buffer the
stream and the events arrive in one lump at the end.

### Client

The existing `paint()` already re-renders the whole transcript from an array of
Anthropic messages. Rather than write a second, incremental renderer that can
drift from `Transcript.render`, keep a **client-side mirror of `history`**, apply
each event to it, and re-render. Transcripts are tens of messages; this is free,
and it means there is exactly one function that knows how a tool card looks.

```js
async function runTurn(text) {
  const res = await fetch("/api/prompt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: text }),
  });
  if (!res.ok) { toast(await detail(res)); return; }

  const live = history.concat([{ role: "user", content: text }]);
  let asst = null;                       // the assistant message being built

  for await (const ev of sseEvents(res.body)) {
    switch (ev.type) {
      case "round_start":
        asst = { role: "assistant", content: [] };
        live.push(asst);
        break;
      case "text_delta": {
        const last = asst.content[asst.content.length - 1];
        if (last && last.type === "text") last.text += ev.text;
        else asst.content.push({ type: "text", text: ev.text });
        break;
      }
      case "tool_use":
        asst.content.push({ type: "tool_use", id: ev.id, name: ev.name, input: ev.input });
        break;
      case "tool_result":
        // A tool_result-only user message: exactly the shape render() folds into
        // the matching tool card.
        live.push({ role: "user", content: [{ type: "tool_result",
          tool_use_id: ev.tool_use_id, content: ev.content, is_error: ev.is_error }] });
        break;
      case "retry":  toast(`API overloaded — retrying in ${ev.delay}s…`); break;
      case "cancelled": toast(ev.reason); break;
      case "error":  toast(ev.message); break;
    }
    paint(live);
  }
  await refresh();   // the server is still the source of truth; reconcile once
}

// Minimal SSE reader: split on blank lines, take the data: payload.
async function* sseEvents(body) {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += value;
    let i;
    while ((i = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, i); buf = buf.slice(i + 2);
      for (const line of frame.split("\n"))
        if (line.startsWith("data:")) yield JSON.parse(line.slice(5));
    }
  }
}
```

`transcript.js` needs one change: `toolCard` currently renders
`(no result recorded)` when `result === undefined`, which is right for a saved
history but wrong for a tool that is *running right now*. Add a third state:

```js
// toolCard(block, result, live)
if (result !== undefined) { /* ... unchanged ... */ }
else if (live) body.innerHTML += '<div class="kv-label">Result</div>' +
                                 '<div class="tool-pending"><span class="spin"></span>running…</div>';
else            body.innerHTML += '<div class="kv-label">Result</div>' +
                                 '<pre class="json">(no result recorded)</pre>';
```

`render(history, tx, {live})` threads the flag through. `view-history` passes
nothing and behaves exactly as it does today — the distinction is "am I looking
at a turn in flight", which only `serve` ever is.

**One observer.** A second browser tab does not see the running turn; it sees the
last `/api/history` snapshot until the turn ends. Fixing that means an
`asyncio.Queue` fan-out with per-subscriber backpressure, and the lock already
guarantees there is only one *operator*. Deferred; note it in the UI copy.

**Image blocks.** A `snap_and_analyze` result carries a few hundred KB of base64
PNG, and it goes down the SSE stream verbatim. Over loopback that is a
non-issue. If it ever isn't, the fix is to elide `image` blocks from the
`tool_result` event and let the trailing `refresh()` supply them — the renderer
already handles both shapes.

---

## 4. What `run_agent`'s callers see

Nothing. `run_agent(msg, ctrl, guard, history, model)` still returns
`(reply, new_list)`; `_repl` still does `history[:] = new_history`;
`tests/test_agent.py` and `tests/test_webserve.py`'s `_run_agent` fake still
apply. The one visible change is that the REPL can now print text as it streams,
which is a two-line follow-on and explicitly out of scope for this document.

The behaviour that must be preserved test-first, because it is easy to break in
the rewrite:

* Overload-exhausted **discards the turn** — including the user message.
  `run_agent` today returns `list(history or [])`. With in-place mutation that
  becomes `del messages[start:]`, and a test must assert the user message is gone.
* The iteration cap **keeps** the turn (the "say 'continue'" message depends on
  it) and returns the progress-preserved string.
* `_with_cache_breakpoint` copies rather than mutates. It must keep doing so —
  the messages list is now long-lived across the stream.

---

## 5. v4a — Stop

### Cooperative, and honest about it

`execute_tool` runs on a threadpool thread and blocks in Java. There is no
interrupting `run_timelapse` halfway. A Stop button that claims otherwise is
worse than none, because the operator will believe it.

So: a `threading.Event` on the session, checked at exactly two points —
the top of each round, and immediately before each tool dispatch within a batch.
Never mid-tool. The button's label is **"Stop after the current step"** and the
toast on click says which step it is waiting on.

```python
@app.post("/api/stop")
async def post_stop():
    if not session.lock.locked():
        raise HTTPException(409, "No turn is running.")
    session.cancel.set()
    return JSONResponse({"stopping": True})
```

This is a **UI endpoint, not a tool.** The agent must not be able to call it, the
same rule design/15 §v2 sets for the safety-config editor. It is not in
`TOOL_REGISTRY` and never will be.

### The correctness trap: orphaned `tool_use` blocks

This is the part that is easy to get wrong and impossible to notice until the
*next* turn. The Messages API requires that **every `tool_use` block in an
assistant turn has a matching `tool_result` in the following user message.** If
the operator stops a turn where the model requested three parallel tools and we
only ran the first, and we append a user message with one `tool_result`, then the
next prompt sends a malformed conversation and gets a 400 — from a history that
looks perfectly fine in the viewer.

So cancellation cannot simply `return`. It has to finish the bookkeeping:

```python
def _unwind_cancel(messages, start):
    """Leave `messages` in a state the API will accept on the next turn.

    An assistant turn ending in tool_use blocks is only valid if the next user
    message answers every one of them. On cancel we answer the unrun ones with an
    error result rather than dropping them.
    """
    last = messages[-1] if messages else None
    if last and last["role"] == "assistant":
        pending = [b for b in last["content"] if getattr(b, "type", None) == "tool_use"]
        if pending:
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                 "content": json.dumps({"error": "Cancelled by the operator."})}
                for b in pending
            ]})
    yield {"type": "cancelled",
           "reason": "Stopped by the operator. The last step was completed; "
                     "nothing further was run."}
```

The same synthesis happens inline in the loop for a partially-executed batch
(see the `_cancelled(cancel)` branch in §2) — the results list gets an error
entry per skipped block, and the loop exits after appending it.

The model sees `is_error` results reading "Cancelled by the operator," which is
exactly what we want it to know when the operator types "continue".

### What Stop does *not* do

A cancelled turn can leave a laser on and a stage mid-travel. Stop waits for the
current tool to finish, so it neither shutters nor halts — deliberately.
Auto-shuttering on cancel would drop the excitation mid-SMLM acquisition, which
is its own kind of surprise, and the system prompt already instructs the model to
confirm lasers off at task end. Surface the state instead: the `cancelled` toast
reads "the last step completed — check illumination and stage position," and the
transcript makes the last executed tool obvious.

Halting hardware *now*, without waiting for the tool to return, is a different
feature with a different risk profile. §6.

---

## 6. v4b — Hardware kill switch (CANCELLED — see "Verdict" below)

> **Resolution.** pyjavaz serializes every bridge call behind one lock, so a halt
> request queues behind the tool call it is trying to interrupt rather than
> preempting it. `/api/halt` cannot work. This section is kept in full because
> the reasoning that got here — and the two spike runs that nearly gave the wrong
> answer twice — are the useful part.

Stop is cooperative: it waits for the running tool. The tempting companion is an
always-live button that reaches past the agent loop and halts hardware
immediately. This is the one thing in either increment I don't think we should
build on the strength of reasoning alone — and the ordering matters more than it
first appears.

### Stage before illumination

The obvious version of this button is "illumination off" — `shutter_all` already
exists, exercised on every teardown path, and it would be five lines. But
**illumination is the recoverable failure.** A laser left on bleaches a sample
and endangers eyes; both are bad, both are bounded. **A stage that keeps moving
drives the objective into the coverslip.** That destroys the objective, the
sample, and possibly the stage — thousands of dollars, and unlike a bleached
field it is not recoverable by preparing another slide. If exactly one kill
switch ships, it halts motion. If both ship, a press halts motion *first*, then
shutters.

So the endpoint is `POST /api/halt`, not `/api/illumination/off`:

```python
def halt(ctrl, guard) -> dict:
    """Stop motion, then illumination. Never raises — a failure on one device
    must not prevent the others from being tried."""
    stopped, failed = [], []
    core = ctrl.core
    devices = [core.get_xy_stage_device(), core.get_focus_device()]
    # named_stages, like workspace_dir in §8, is currently private on guard._c;
    # both want a real accessor rather than a reach-in.
    devices += [s.device for s in guard.named_stages]      # TIRF steering, etc.
    for label in devices:
        try:
            core.stop(label)                # CMMCore::stop(xyOrZStageLabel)
            stopped.append(label)
        except Exception as e:
            failed.append({"device": label, "error": str(e)})
    return {"stopped": stopped, "failed": failed,
            "shuttered": guard.shutter_all(core)}
```

### Three reasons this is not a five-line change

**1. `core.stop()` is optional in the device layer.** `CMMCore::stop()` is
documented as stopping the XY or focus stage motors, with the caveat that *not
all stages support it*. A device adapter that doesn't implement Stop may throw,
or may return successfully having done nothing. **A kill switch that silently
does nothing is worse than no kill switch**, because the operator will reach for
it instead of the hardware. Run 1 established only that `stop('XY')` and
`stop('Z')` do not *raise* on this rig — which distinguishes "no Stop at all"
from "Stop that might work", and nothing more. Before this button is enabled for
a given rig,
`core.stop()` must be probed against every device in that list and the result
recorded — and the UI must grey out, with an explanation, any device that failed
the probe. That probe belongs in `safety_config.yaml` territory, not in a
best-effort `try/except`.

**2. It has to interleave with a call already in flight.** This is the same
concurrency question the shutter button raises, but sharper. The whole point is
to call `core.stop()` from the event loop's threadpool *while* `move_stage_xy` is
blocked in `core.wait_for_device()` on the tool thread, holding the same `Core`.
design/11b Spike C established that the pycro-manager bridge is **not
thread-affine** — a hook on an acquisition worker thread can call
`ctrl.core.get_position()` and it works. That is a weaker claim than this needs.
"Thread A may use the bridge" does not imply "threads A and B may have
*overlapping* request/reply round-trips on it." A ZMQ REQ socket has strict
send/recv alternation; two concurrent callers can interleave and read each
other's replies. Whether pyjavaz gives each thread its own socket, serializes
internally, or does neither, I cannot settle from the code in this repo. If it
does neither, the failure mode is a silently mismatched reply on a hardware
call — the worst class of bug this project can have, and a spectacular one to
introduce in the name of safety.

But run 2 (below) surfaced the *other* horn of that fork, which this section
originally missed. **If pyjavaz serializes instead — one shared socket behind a
lock — the halt never desyncs anything, and is equally useless**: it blocks until
the in-flight tool call finishes, then fires at an idle stage. A button that
cannot preempt is not a kill switch. Both branches of "how does pyjavaz handle
concurrency" therefore kill `/api/halt`; only genuinely concurrent, correctly
demultiplexed calls save it.

**3. Software cannot beat physics, and the UI must not imply it can.** The path
is browser → uvicorn → threadpool → ZMQ → Java → serial → controller. That is
tens to hundreds of milliseconds on a good day. A fast stage covers real distance
in that time — run 1 measured this rig crossing 200 µm in under 200 ms, fast
enough that a 0.2 s software delay missed the entire move. The rewritten spike
therefore times the travel first and refuses to proceed below ~150 ms, because a
move shorter than the halt's own latency cannot be halted by anything we build. The actual protections against a crash, in the order they matter:
correct `z_min`/`z_max` and XY bounds in `safety_config.yaml` (checked *before*
every move by `check_xy` / `check_z`, which is why an in-bounds move that hits the
objective means the config is wrong, not that the button was too slow); the stage
controller's own limit switches; and a physical E-stop or motor disable within
arm's reach of the operator. **This button is a convenience for aborting a long,
slow, wrong move — not a safety device.** Label it "Halt", never "Emergency
Stop", and say so in the tooltip. Do not let its existence become an argument for
loosening anything in the guard.

### Run 1 — voided, and why

The first spike run reported `FAIL=2 PASS=3`. **Both failures were bugs in the
spike, not facts about the rig, and the run answered neither question.** Recording
it because the failure mode is instructive and easy to repeat.

* **Test 3's "reply desync" was a bad assertion.** The check was
  `isinstance(v, float)`. The bridge JSON-encodes an exact 456.0 µm position as
  the *integer* `456`, so a correct reply was reported as a crossed one. The halt
  thread's `get_version_info()` came back a proper version string and the
  post-halt call was healthy — there was never any evidence of a desync.
* **Test 2's "stop() did not halt the move" measured nothing.** The spike slept a
  guessed 0.2 s before calling `stop()`. The stage covered 200 µm in less than
  that, so `stop()` hit an *idle* stage. The spike's own error text flagged the
  ambiguity, but it printed the two numbers that would have resolved it
  (`move_wall_s`, `device_busy` before the stop) only on the success paths.
* **Therefore Test 3 was void too, and silently.** If the move finished before the
  halt thread fired, the two threads never had calls in flight at once. The
  overlap the spike exists to test never happened, and nothing in the output said
  so.

Two lessons, both now enforced in the spike. **Never trigger on a guessed
delay** — measure the move, then fire on `device_busy()`. And **a test that
failed to test the thing must report `INCONC`, not `PASS` or `FAIL`**: a green
Test 3 on that run would have been worse than the red one, because we would have
believed it.

One incidental signal from run 1 worth keeping: the readback after a pure-X move
was `y = 256.005`, i.e. 5 nm of noise. That is real hardware, not the demo
config. A stage that crosses 200 µm in under 200 ms is simply fast.

### Run 2 — a real signal the spike could not read

Run 2 fixed both bugs and came back `INCONC=2`. The move was 2000 µm in
**213.8 ms** (9.35 mm/s) — well clear of the 150 ms floor, so this stage *is*
long enough to halt. And yet `device_busy('XY')` was **never True from the halt
thread**, across a 427 ms poll window on a 214 ms move. The stage travelled the
full 1999.99 µm.

That is not nothing. Something consumed the entire window, and there are exactly
two explanations — which point in opposite directions:

**(A) The bridge serialized the halt thread.** Its first call —
`get_version_info()`, issued before the poll loop — blocked behind the main
thread's in-flight `set_xy_position` / `wait_for_device` and did not return until
the move was over. By the time it polled, the stage was idle.

**(B) `device_busy()` never reports True on this adapter.** The halt thread ran
concurrently the whole time, polling a signal that is always False.

**If (A), `/api/halt` is dead on arrival** — and not because of a desync risk,
which is the failure mode §6 was written to worry about. It is worse and duller
than that: a halt request would *queue behind the very tool call it is trying to
interrupt*. It could never preempt a move, only be delivered after it. That makes
it exactly the cooperative Stop of §5 wearing a scarier label, which is the one
thing this document has said from the start we must not ship.

If (B), the trigger is broken and the concurrency question is still open.

Run 2 recorded nothing that separates them, because it never timed its own calls.
But we do not need a third run to decide: **the answer is (A), and it is readable
in pyjavaz's source.**

### Resolved from source: the bridge serializes every call

`pyjavaz/bridge.py`:

```python
self._communication_lock = threading.Lock()          # Bridge.__init__, ~line 300

def send_and_receive(self, message, timeout=None, give_up_condition=None):
    """Send a message over the main socket"""
    with self._communication_lock:                    # ~line 410
        ...                                           # every return is inside
```

Every Java method call — `_JavaObjectShadow._send_and_receive` → `Bridge.
send_and_receive` — takes that lock and holds it for the **entire** request/reply
exchange. There is one `Bridge` per port (`Bridge._cached_bridges_by_port`, the
same cache CLAUDE.md and design/12 warn about), so the lock is effectively
process-wide.

The consequence is decisive. `core.wait_for_device(xy_label)` is a *single Java
call that blocks in Java until the device stops moving*. For its whole 214 ms it
holds `_communication_lock`. Any other thread calling anything on the core —
`get_version_info()`, `device_busy()`, `stop()` — blocks at the `with` statement
until the move finishes. That is precisely run 2's observation: the halt thread's
first call did not return until the stage was already parked, so `device_busy()`
was never going to be True and `stop()` was always going to hit an idle stage.

This also explains why design/11b Spike C passed. A hook calling `ctrl.core` from
an acquisition worker thread works fine — those calls are *sequential* with the
main thread's, never overlapping. "Not thread-affine" was true and, exactly as
§6 suspected, not the property a kill switch needs.

### Verdict: never build `/api/halt`

**A halt request cannot preempt an in-flight tool call on this architecture. It
queues behind it.** The button would block for the duration of the very move it
is meant to interrupt, then shutter and stop an idle stage — and it would do this
*silently*, presenting as a working control. That is the exact failure §6 opened
by naming: a kill switch the operator reaches for instead of the hardware, that
does nothing.

There is no fix short of a second bridge on a second port (a whole parallel
`Core`, with its own connection to the same Java process and no guarantee the
MMCore device layer tolerates the concurrent access either), or moving the halt
into the Java side entirely. Neither is worth it for a button whose honest job
description is "abort a long slow wrong move" — a job the cooperative Stop of §5
already does, at the next tool boundary, without pretending to be an E-stop.

So: **`/api/halt` is cancelled.** v4b is closed, not deferred. The answer to "the
stage must halt *now*" is, and remains, the hardware E-stop, the stage
controller's limit switches, and correct bounds in `safety_config.yaml` checked
*before* the move by `check_xy` / `check_z`. The `finally` in `serve()` still
shutters illumination on exit. Ship §5's Stop, and label it Stop.

**A general rule for `serve` falls out of this**, worth writing down before
someone rediscovers it the hard way: *no HTTP endpoint may touch `ctrl.core`
while a turn is running.* It will not race — the lock makes that impossible — it
will simply **block until the turn's current tool call completes**, holding a
threadpool worker and appearing to hang. Every endpoint in v3/v4 respects this by
construction: `/api/history`, `/api/prompt`, `/api/stop`, `/api/model`,
`/api/key` and `/api/artifact` touch the history, a `threading.Event`, the
filesystem, or the credential store — never the core. The teardown `shutter_all`
in `serve()` is safe because uvicorn has already stopped. Keep it that way.

### What the spike is still for

The concurrency question is answered, so run 3 is no longer discovery — it is
confirmation, and it is cheap. Worth running once to (a) see the serialization in
the timing numbers rather than only in the source, and (b) settle whether
`core.stop()` on this rig's adapter halts a move *at all*, which is the one fact
`/api/halt`'s cancellation leaves unresolved and which a future in-loop halt (a
`cancel` check between the tool's own sub-steps, inside `move_stage_xy`) would
need. Do not gate anything on it.

### Proposal

`design/16-halt-concurrency-spike.py` (rewritten after run 1), to be run manually
on the Windows lab machine with a real stage. It now measures before it acts:

0. **Time the move.** Run the travel once, unhalted, and report its duration.
   If a full-travel move completes in less than ~150 ms, stop: a halt has to
   cross browser → uvicorn → threadpool → ZMQ → Java → serial, and it cannot
   catch this stage. **That is a finding, not a test failure** — it says a
   software halt on this rig is theatre, and it settles §6 without any further
   testing.

1. Probe `core.stop(label)` on the XY device, the focus device, and each
   `named_stages` entry while **idle** — does the adapter even accept the call?
   *Run 1 answered this much: `stop('XY')` and `stop('Z')` both return without
   raising.* That is necessary and nowhere near sufficient; an adapter can accept
   Stop and ignore it, which is step 3b.
2. **Baseline an idle bridge round trip** (median of 20 `get_version_info()`
   calls), so a blocked call is recognizable when we see one. And check
   `device_busy()` *single-threaded* — issue the move, poll from the same thread —
   before any test is allowed to depend on it.
3. Start the long move on the main thread. From a second thread:
   * **3a — time when its first bridge call returns**, relative to the move's
     start. `≈ baseline` ⇒ concurrent. `≈ move duration` ⇒ serialized, and
     `/api/halt` is over. This is the decisive test; 3b and 3c are downstream.
   * **3b — call `core.stop(xy_label)`** on `device_busy()` if step 2 proved it
     usable, else on a timer at 30 % of the *measured* move. Assert the travel was
     cut short and `wait_for_device` unblocked.
   * **3c — assert both threads got correctly-shaped, non-interleaved replies.**
     Vacuous if 3a says serialized; a serialized bridge cannot cross replies.
4. Repeat with a running acquisition instead of a stage move, which is the case
   Spike C covered for *reads* but not for concurrent *writes*.

Every step reports `INCONC` rather than `PASS`/`FAIL` when its premise did not
hold. Run 2 exists as a reminder of why: it is far easier to build a test that
cannot fail than one that can answer.

Fold the findings back into this document before writing the endpoint.

*(Written before the source resolved this. Kept because the reasoning stands, and
because the conclusion it reached — "don't build it on reasoning alone" — is what
sent us to look.)* Ship Stop and the rest of v4; the physical E-stop and the
guard's bounds checks remain the answer to "the stage must halt *now*", and the
`finally` in `serve()` still shutters on Ctrl-C. An `INCONC` run leaves
`/api/halt` exactly as unbuilt as a `FAIL` does.

One consequence worth noting either way: a stopped move makes `wait_for_device`
return early, and `move_stage_xy` already reports requested vs. achieved position
with an `error_um`. A halted move therefore surfaces to the model as a large
`error_um` on the tool result rather than as silence — the system prompt already
tells it to flag anything above ~1 µm. That is the right behaviour and it comes
for free.

---

### Implementation note: where the cancel check lands

`_unwind_cancel` is defensive, not load-bearing. The round-boundary check runs
immediately after a `tool_results` user message was appended, so there is never a
pending assistant turn to answer there. The synthesis that matters is the inline
one: a batch is stopped between tools, and the *sticky* `stopped` flag makes every
remaining block in that batch take the error branch. Both paths keep the
`tool_use` / `tool_result` sets equal.

Worth being precise about what the operator gets, because the button's promise
depends on it. The check sits **before each tool dispatch**, so a tool already
running when Stop is pressed always finishes. Press Stop while tool 1 of 3 is
mid-move and tools 2 and 3 are answered with the cancel error. Press it while
tool 2 is *already* dispatched and tool 2 finishes too, and only tool 3 is
skipped. There is no arrangement in which a running tool is abandoned, which is
the whole point — and it means the toast must say "the last step completed",
not "stopped".

## 7. v4c — Model picker

`resolve_model` already implements `explicit > $MICROCLAW_MODEL > DEFAULT_MODEL`,
and `Session.model` is just `args.model`. The picker writes that one field.

```python
class Model(BaseModel):
    model: str

@app.get("/api/model")
async def get_model():
    return JSONResponse({"model": resolve_model(session.model),
                         "default": DEFAULT_MODEL,
                         "available": _known_models()})

@app.post("/api/model")
async def post_model(m: Model):
    if session.lock.locked():
        raise HTTPException(409, "A turn is in progress.")
    async with session.lock:            # never swap models mid-turn
        session.model = m.model.strip()
    return JSONResponse({"model": resolve_model(session.model)})
```

`_known_models()` calls `client.models.list()` once and caches the ids for the
process; the field stays free-text, because a model released after this cache was
populated must still be typable. A bad id surfaces as a `NotFoundError` on the
next turn — which `execute_tool` does not catch, so it propagates out of
`run_agent_iter` as a 500 on the stream. That is worth one `except
anthropic.NotFoundError` in `_stream_one_round` yielding a clean `error` event.

**Say the cache cost in the UI.** Prompt caching is scoped per model. Switching
mid-conversation invalidates the entire prefix — every cached breakpoint that
`_with_cache_breakpoint` and the two system blocks have been accumulating — and
the next turn re-pays the whole conversation at full input price. That is a real
cost on a 25-round survey. The picker shows: *"Switching models re-sends this
conversation uncached."*

Session-only. Not persisted; `--model` and `MICROCLAW_MODEL` remain the durable
knobs.

---

## 8. v4d — Artifacts in the transcript

`save_position_list` returns `{"status": "Position list saved to /path/x.json"}`.
`export_dataset_as_tiff` and `read_hook_log` are the same shape — a human
sentence with a path buried in it. Regexing paths out of prose in the renderer is
a bad idea that will work for six months and then match a filename in an error
message.

Instead, have the tools say so structurally. A key the model ignores and the
renderer picks up. Shipped on `save_position_list` (`kind: position_list`),
`export_dataset_as_tiff` (`kind: tiff`) and `read_hook_log` (`kind: hook_log`).
The acquisition tools' `dataset_path` is deliberately *not* an artifact: an
NDTiff dataset is a directory, and `FileResponse` cannot serve one.

```python
def save_position_list(ctrl, guard, path: str) -> dict:
    path = guard.resolve_in_workspace(path)
    ctrl.save_position_list(path)
    return {"status": f"Position list saved to {path}.",
            "artifact": {"kind": "position_list", "path": path}}
```

The renderer draws a chip under the tool card linking to
`/api/artifact?path=...`. In `view-history` (a `file://` page with no server)
the chip renders as inert text — check for `location.protocol === "http:"`.

**The path reaches an HTML attribute, and `esc()` is not enough for that.** It
escapes `< > &` and leaves quotes alone, so a path ending `" onclick="alert(1)`
hangs an event handler on the anchor — the same trap `dataURL` already
sidesteps for `media_type`, rediscovered. `href` is safe via
`encodeURIComponent`; `title` needed a new `escAttr`. A history JSON can come
from anywhere, including a file dropped on the viewer.

Serving files from the browser is new attack surface, so the endpoint is narrow.

### Authorise per-file, not per-directory

The first version of this gated the endpoint on `workspace_dir`, reasoning that
without *some* configured root `resolve_in_workspace` is a no-op and the endpoint
becomes an arbitrary file read for anything that can reach loopback. The
reasoning was right; the instrument was wrong, and it shipped. A directory
sandbox authorises **every file beneath the root**, including ones no tool ever
touched — and it forced a lab to opt into a filesystem sandbox purely in order to
click a download link.

That coupling had teeth. A rig configured `workspace_dir: C:\Users\rieslab\
.microclaw` (microclaw's own state directory — hooks, `knowledge.yaml`) to get
artifact chips, then acquired a z-stack to `D:\`. `run_zstack` did **not** pass
`save_dir` through the guard, so the write succeeded; `export_dataset_as_tiff`
does, so it refused to read the dataset it had just been handed. The guard was
one-sided, and requiring `workspace_dir` for artifacts is what made anyone
notice. (That asymmetry is now closed — see below — but the fix here is the
decoupling, not the confinement.)

The tools already say what they produced. That declaration *is* the capability:

```python
@app.get("/api/artifact")
async def get_artifact(path: str):
    if not session.editable:                       # loopback only
        raise HTTPException(403, "Not available when bound beyond localhost.")
    if path not in _declared_artifacts(session.history):
        raise HTTPException(403, "Not an artifact produced by this session.")
    try:
        # A configured workspace still applies; it is no longer what authorises.
        resolved = session.guard.resolve_in_workspace(path)
    except SafetyViolation as e:
        raise HTTPException(403, str(e))
    if not Path(resolved).is_file():
        raise HTTPException(404, "No such artifact.")
    return FileResponse(resolved, media_type="application/octet-stream", ...)
```

`_declared_artifacts` walks `session.history` and collects every
`artifact.path` from the tool results. Those dicts are built by microclaw's own
code from the path the tool actually wrote — the model cannot name a file here
that no tool produced. Exact match, so there is no traversal question: a path
either matches a declaration or it does not. Strictly tighter than the root it
replaces, and it needs no configuration, so **downloading an artifact never
constrains where the operator may save data.**

Two further deliberate choices. **Always `application/octet-stream` +
`attachment`**, never a sniffed type: an artifact that happens to be HTML, served
inline from `http://127.0.0.1:8000`, is same-origin script execution against the
endpoint that drives the microscope. And the endpoint **still** calls
`resolve_in_workspace`, so a lab that did configure a sandbox does not find this
route around it.

Exfiltration is bounded by what the tools already do. `read_hook_log(log_path)`
takes a model-chosen path and returns the file's contents in the transcript
regardless; the artifact chip adds no reach. `export_dataset_as_tiff` writes to
its `output_path`, so a hostile `output_path` clobbers a file rather than
exposing one. `workspace_dir` remains the answer for a lab that wants those two
bounded — it is simply no longer mandatory.

### The guard was one-sided: acquisitions wrote outside the workspace

Confirmed on the rig once `workspace_dir` stopped being mandatory, so the test
could be run for its own sake. With `workspace_dir: D:\microtest`:

```
run_zstack(save_dir="D:\")        -> {"status": "Z-stack complete.",
                                      "dataset_path": "D:\zstack_3"}
export_dataset_as_tiff("D:\zstack_3")
                                  -> {"error": "... Path 'D:\zstack_3' escapes
                                      the configured workspace (D:\microtest)."}
```

The write escaped; the read did not. Recovery cost a **second full acquisition** —
eleven planes, another sweep of the focus drive — because the first had already
committed data where the export could never reach it. *A check that runs after
the irreversible part is the wrong check.*

So `save_dir` now goes through `resolve_in_workspace`, at two points with
different jobs:

* **`_acquire_with_hooks`** is the single place an acquisition touches the
  filesystem, so resolving there means no dataset can escape a configured
  workspace even if a future acquisition tool forgets. It has a test.
* **The top of each public acquisition tool**, so the refusal lands before
  `set_exposure` and before the stage moves. Resolving twice is idempotent.

Two more instances of the same shape, fixed with it. `run_adaptive_*` passes
`log_path` to a hook that writes the file itself, unguarded, while
`read_hook_log` is guarded — an adaptive run could write a log microclaw would
then refuse to read back. And `load_position_list` was an unguarded read sitting
next to a guarded `save_position_list`.

**With `workspace_dir` unset — the default — none of this changes anything.**
Confinement is opt-in, and a lab that opted in did not mean "except acquisitions."

### `workspace_dir` at a filesystem root rejects everything

Found while working out what to tell that rig to configure instead. The
containment check was `resolved.startswith(root + os.sep)`, and `os.path.realpath`
of `/` or `D:\` already ends in a separator — so the prefix became `//`, which is
a prefix of nothing. `workspace_dir: /` refused `/tmp/x.json` with a message
reading "escapes the configured workspace directory (/)". Fixed with
`root.rstrip(os.sep) + os.sep`, which still refuses `/database` under a `/data`
root.

---

## 9. Tests

All of it runs without hardware or an Anthropic key, in the style of
`tests/test_webserve.py` (fake session, monkeypatched `run_agent_iter`).

**`run_agent_iter` (`tests/test_agent.py`)** — fake client whose `stream()` is a
context manager over a scripted event list.

- Emits `round_start` → `text_delta`* → `done` for a text-only turn.
- Emits `tool_use` then `tool_result` in order, and appends both to `messages`.
- Mutates the caller's `messages` in place; the same list object comes back out.
- Overload-exhausted leaves `messages` **unchanged** (the user message is gone).
- Iteration cap leaves `messages` populated and yields the resume message.
- `run_agent` drains it and returns `(reply, new_list)` identical to before —
  keep the existing `test_agent.py` assertions green, unmodified.

**Cancellation** — the one that catches the 400.

- `cancel` set before round 0: no API call is made, `cancelled` is the only event.
- `cancel` set between tools in a 3-tool batch: `messages[-1]` is a user message
  with **three** `tool_result` blocks, two of them `is_error`, and their
  `tool_use_id`s are exactly the three `tool_use` ids from `messages[-2]`.
  Assert set equality — that invariant is the whole point.
- `POST /api/stop` with no turn running → 409.
- `/api/stop` is not in `TOOL_REGISTRY` (a `test_schema_parity`-style guard).

**Transport (`tests/test_webserve.py`)**

- `POST /api/prompt` returns `text/event-stream` and the frames parse as JSON.
- The lock is released after the stream is consumed *and* after it is abandoned
  (`TestClient` supports `stream=True`; close it early, then assert a second
  prompt gets 200 rather than 409).
- History is written once the stream completes, and after a client disconnect.
- `GET /api/artifact` → 403 when `workspace_dir` is None; 403 on a traversal
  path; `Content-Disposition: attachment` on the happy path.
- `POST /api/model` during a turn → 409.

**`transcript.js` (`tests/test_transcript_js.py`)** — the existing file already
asserts structural properties of the asset; extend it to check the three-state
`toolCard` and that `serve.html` contains no literal end-script tag in the new
SSE reader.

---

## 10. Increments

1. ~~**v3a — the generator.**~~ **Shipped.** `run_agent_iter` + `run_agent` as a
   drain. The existing suite passed with only the mock swapped from
   `messages.create` to `messages.stream`.
2. ~~**v3b — the stream.**~~ **Shipped**, with the `iterate_in_threadpool`
   correction in §3. Confirmed on a real rig.
3. ~~**v4a — Stop.**~~ **Shipped.** `cancel` event, `_unwind_cancel`,
   `/api/stop`, button. The orphaned-`tool_use` invariant is tested both as a
   unit (three-tool batch, set equality on the ids) and end-to-end (the turn
   after a Stop is a conversation the API accepts).
4. ~~**v4c + v4d — model picker, artifacts.**~~ **Shipped.** `known_models()` is
   fetched once per process and off the event loop — it is a blocking HTTP call
   and `/api/model` runs on page load. `esc` → `escAttr` for the artifact chip's
   `title` (§8).
5. ~~**v4b — hardware halt.**~~ **Cancelled** (§6): pyjavaz serializes bridge
   calls, so the halt can never preempt a running tool call. Stop is a complete
   feature without it. Run the spike once more if you want the serialization
   confirmed in timing numbers, but nothing is gated on it.

Out of scope, in rough order of how much I want them: multi-tab fan-out over an
`asyncio.Queue`; streaming in the terminal REPL (two lines once §2 lands);
`--effort` / adaptive thinking, which is a change to what we ask the model for
rather than how we watch it answer; and design/15's v2 safety-config editor,
which is orthogonal and still open.
