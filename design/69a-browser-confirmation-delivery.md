# Make browser confirmations recoverable

## Problem

During the first Zeiss session, a 700-frame acquisition entered Microclaw's
confirmation gate. The server denied it after the configured five-minute
deadline and the agent subsequently produced a complete explanation. Neither
the confirmation nor the completed response was visible to the operator, who
saw only `MicroClaw is working...` and restarted Microclaw.

This is a browser delivery/recovery defect. A confirmation is currently pushed
once as a `confirm_request` event on the POST response stream
(`webserve.py:461`). Although the server exposes the pending confirmation
through `GET /api/confirm` (`webserve.py:1004`), the browser uses that recovery
endpoint only when the page initially loads (`serve.html:796`). A stream that
stops delivering while the page remains open can therefore hide a safety
decision for its full deadline.

The confirmation gate itself failed safely: absence of approval became a
denial. The defect is that the operator was not shown the pending decision or
the completed outcome.

## Evidence and known answer

The confirmation audit records `declined:timeout`. The conversation history
then records both the acquisition refusal and the agent's response offering to
relaunch it. That sequence establishes that the server-side confirmation loop,
tool dispatch, following model round, and history write all returned. A
post-timeout backend hang is not consistent with the recorded history.

The current implementation explains how the visible failure can occur:

```text
turn thread       POST response stream        browser
    |-- confirm_request ------------------------X
    |        waits up to 300 s                  | still says "working"
    |-- confirm_resolved -----------------------X
    |-- tool_result / final text ---------------X
    |-- turn done ------------------------------X
```

`GET /api/confirm` is already the right independent source of truth; it simply
is not polled while a turn is active.

### An open, silent stream is the leading explanation, not a proven fact

This narrows the likely cause, and it changes what has to be built. A stream
that *ends or throws* already attempts recovery today: `runTurn` returns or
raises, and the composer handler calls `refresh()` on both paths
(`serve.html:646-658`), which re-reads the durable history through
`/api/history`. An ordinarily closed stream with a working refresh should have
shown the completed transcript.

The artifacts do **not** establish the browser's or transport's exact state.
The leading code-level explanation is that the stream remained open and silent
for the whole deadline, leaving `await reader.read()` unresolved. A failed or
hung recovery request, a browser-side fault, or another transport failure could
also produce the operator's report and must remain investigation candidates.
One thing the operator's report can narrow, if their recollection of the
wording and timing is exact: `MicroClaw is working…` is the *default*
`pending-text` (`serve.html:205`), while `showConfirm` overwrites it with
`Waiting for your confirmation.` (`serve.html:588`). This is consistent with
`confirm_request` never reaching `applyEvent`, but it does not prove that. The
browser could also have received both `confirm_request` and
`confirm_resolved`—restoring the default text—and then stalled before the tool
result and final response. The artifacts do not identify the last event the
browser applied.

That second branch is worth stating because it is the one polling does **not**
fix: if the banner appeared and the operator was not at the screen, only the
timeout disclosure tells them afterwards what happened and why the run refused.
The two decisions below cover different failures, and neither substitutes for
the other.

Two mechanisms can produce the open-and-silent shape:

- a connection dead at the socket without an observable close, where
  `await reader.read()` (`serve.html:488`) simply never resolves;
- a buffering intermediary holding the whole response (the app sets
  `X-Accel-Buffering: no` and runs no GZip middleware, but an untrusted proxy
  is bound by neither).

**On the deployment this session almost certainly used, the second is
unlikely.** `--host` defaults to `127.0.0.1` (`__main__.py:485`) and the desktop
shortcut passes neither `--host` nor `--allow-remote` (`shortcut.py:20-22`), and
`--behind-tls-proxy` requires `--allow-remote` (`webserve.py:1260`). Absent
evidence that this session was launched some other way, there was no
intermediary to buffer, which raises rather than lowers the value of the event
sequencing decided below: a loopback TCP stream that dies silently is not the
ordinary case, so the mechanism is genuinely unidentified.

**Microclaw cannot currently distinguish an open silent stream from a healthy
quiet stream**, because `events()` (`webserve.py:969`) yields nothing between
agent events and there is no keepalive. A long tool call is silent too. This is
why recovery must cover *silence* as well as end-of-stream.

## Investigation

### 1. Reproduce loss at each boundary

Drive the browser logic against a fake `fetch` and a fake stream that can
discard selected events, stall while remaining open, or close early:

1. Drop `confirm_request` only. The periodic recovery path must display the
   same confirmation before the deadline.
2. Deliver `confirm_request`, then drop `confirm_resolved`. Polling must remove
   the stale banner once the server has no pending confirmation.
3. Stall the stream with the turn thread still active. The UI must say that
   live updates were interrupted, continue checking confirmation state, and
   recover the completed transcript when the turn finishes.
4. Close the stream early. Preserve today's `refresh()` recovery.
5. Reload during a pending confirmation. Preserve the existing recovery
   behavior and ensure the confirmation ID is unchanged.
6. Allow the deadline to expire. The banner must visibly become denied/timed
   out, and the final refusal must appear without restarting.

Use an injected short confirmation deadline in tests; do not wait five real
minutes.

**There is no harness for this yet, and that is the first thing to build.**
All of the above lives in the inline `<script>` in `serve.html`, which has zero
test coverage — the only JS the suite executes is `transcript.js`, loaded as a
module under node (`tests/test_transcript_js.py`, skipped when node is absent).
Untested browser code is what produced this incident, and a design that
proposes tests it has no way to write will ship the same way. So: **move the
confirmation and recovery logic out of the inline script into a module
alongside `transcript.js`**, exporting pure state reconciliation over an
injected `fetch` and an injected clock, and drive limbs 1-6 under node. Keep
the DOM calls in `serve.html`; the reconciliation must not need a DOM.

Two rules from `CLAUDE.md` apply directly to that harness, because it will be
the only thing standing between this code and the rig: a fake that encodes our
assumption is not a test of it — write the stalled-stream fake so
`reader.read()` genuinely never resolves rather than resolving with nothing;
and a skipped test is not evidence, so record whether node was present when
the suite ran.

### 2. Exercise real buffering and disconnect behavior

Run the test through the supported browser/server path and, where applicable,
the documented TLS-terminating proxy. Verify that SSE frames arrive before the
turn ends and that disabling networking briefly does not strand a
confirmation. Unit tests around `Session.confirm()` alone cannot establish
browser delivery.

## Decision

### Poll confirmation state for the lifetime of every active turn

Treat stream delivery as a latency optimization, not the sole delivery
mechanism. While a turn is active, poll `GET /api/confirm` at a short interval
(one second is adequate). Reconcile the banner from server state:

- a returned pending ID displays or updates the banner;
- no pending ID hides any stale banner;
- an ID change replaces the old banner;
- grants are reconciled at the same time.

Start polling before consuming the POST response body and stop it only after
the turn has visibly settled. Use one in-flight request at a time and suspend
polling while the document is hidden, resuming immediately on visibility
change (browsers throttle background timers to roughly once a minute anyway).

Three things the reconciler must get right:

- **A 401 stops the poll; it does not back off into one.** In remote mode a
  failed auth costs a slot in a 10-per-60-s per-client limiter
  (`RATE_MAX_FAILURES`, `webserve.py:109`, applied at `webserve.py:622`). A
  1 Hz poll against an expired paired session exhausts that budget in ten
  seconds, and the 429 that follows then blocks `POST /api/confirm` — the
  recovery path would lock the operator out of the very approval it surfaced.
  On 401: stop polling, say the session needs re-pairing, leave any visible
  banner up. This is a hazard the new polling would *introduce* under
  `--allow-remote`, not a candidate cause of the Zeiss incident: on loopback the
  middleware sets `identity = "loopback"` and returns before any limiter runs
  (`webserve.py:589-592`).
- **Other poll failures must not hide an already-visible confirmation.**
  Display a connection warning after repeated failures and retry with bounded
  backoff.
- **Do not rebuild the grant chips on every tick.** `showGrants`
  (`serve.html:545`) calls `replaceChildren`, so a 1 Hz unconditional call
  destroys and recreates every Revoke button once a second, under the
  operator's cursor. Reconcile against the rendered grant IDs and rebuild only
  on change.

Use a guarded recursive timeout rather than `setInterval`: intervals overlap
when a request takes longer than a tick, contradicting the one-request-at-a-time
rule. Stopping is purely local. In particular, the 401 branch must not call a
stop function that performs one final reconciliation, or it creates an
unbounded chain of new 401 requests.

Browser-side stub:

```javascript
// Module scope, beside `let confirmId = null;` (serve.html:543). The script runs
// under "use strict" (serve.html:221), so each of these is declared once, here,
// and the later stubs assume it.
let recoveryActive = false, pollInFlight = false, pollTimer = null;
let activeTurnAbort = null, currentTurnId = null, recoverySettled = false;
let streamSilenceDetected = false, silenceTimer = null;

async function reconcileConfirmation() {
  const res = await apiFetch("/api/confirm");
  if (res.status === 401) {
    stopConfirmationRecovery();             // local state only; no final fetch
    needsPairing();
    return;
  }
  if (!res.ok) { notePollFailure(); return; }
  const pending = await res.json();
  reconcileGrants(pending.grants);          // no-op when unchanged
  if (pending.id) {
    if (confirmId !== pending.id) showConfirm(pending);
  } else if (confirmId) {
    hideConfirm(confirmId);
  }
  return pending;                            // carries `running`, see below
}

function startConfirmationRecovery() {
  if (recoveryActive) return;
  recoveryActive = true;
  void pollConfirmation();
}

function stopConfirmationRecovery() {
  recoveryActive = false;
  window.clearTimeout(pollTimer);
  pollTimer = null;
}

async function pollConfirmation() {
  window.clearTimeout(pollTimer);           // this chain owns the timer
  pollTimer = null;
  if (!recoveryActive || pollInFlight) return;
  if (document.visibilityState !== "visible") {
    pollTimer = window.setTimeout(pollConfirmation, 1000);
    return;
  }
  pollInFlight = true;
  try {
    await reconcileConfirmation();
  } finally {
    pollInFlight = false;
    if (recoveryActive) {
      pollTimer = window.setTimeout(pollConfirmation, 1000);
    }
  }
}
```

On `visibilitychange` to visible, invoke `pollConfirmation()` immediately. It
clears the pending timer itself rather than trusting each caller to, so a
handler that forgets cannot start a second chain — `pollInFlight` prevents
concurrent *requests*, not duplicate chains.

### Make the stream's silence observable, and recover the outcome

Recovery keyed only to end-of-stream would not cover the leading failure
hypothesis. Cover silence as well, on both sides:

1. **Server: keepalive.** In `events()` (`webserve.py:969`), bound the queue
   read and yield an SSE comment (`: ping\n\n`) every ~10 s of quiet. This also
   gives a buffering intermediary something to reveal itself with.

   **Do not wrap `queue.get()` in `asyncio.wait_for`.** That cancels the getter
   on every quiet tick, and whether a cancelled `Queue.get` can lose an item
   delivered in the same instant is a question nobody should have to answer
   about `confirm_request` — the event this whole design exists to deliver.
   Hold one getter task across iterations and time out the *wait*, so nothing
   is ever cancelled:

```python
        async def events():
            pending_get = asyncio.ensure_future(queue.get())
            try:
                while True:
                    done, _ = await asyncio.wait({pending_get}, timeout=KEEPALIVE_S)
                    if not done:
                        yield ": ping\n\n"          # comment frame, no data:
                        continue
                    event = pending_get.result()
                    if event is _TURN_DONE:
                        return
                    pending_get = asyncio.ensure_future(queue.get())
                    yield _sse(event)
            finally:
                pending_get.cancel()
```

   The `finally` matters: a client that disconnects closes this generator, and
   an un-cancelled getter would be left holding a reference to the queue.
2. **Client: observe complete keepalive frames.** `sseEvents`
   (`serve.html:484-500`) currently discards comment lines. That is correct for
   transcript events, but a silence detector outside the parser cannot then
   see a ping. Give the parser a frame callback and reset the semantic-delivery
   deadline when a complete SSE frame is assembled, including a comment frame,
   not merely when arbitrary bytes arrive. A partial chunk proves transport
   activity but not delivery of a usable confirmation; an intermediary that
   dribbles partial bytes must not suppress recovery indefinitely. A separate
   raw-byte timestamp may be logged for diagnosis, but it does not control the
   confirmation recovery timer.
3. **Client: silence timeout.** Track the last complete frame — event *or*
   keepalive. After ~3 missed keepalive intervals with the turn still active,
   replace `MicroClaw is working…` with `Live updates interrupted; checking
   Microclaw…`, keep confirmation polling running, and let the poll carry the
   turn's liveness. Arm it at turn start as well as on every frame: a stream
   silent from its first byte never fires the frame callback, so a detector
   armed only by frames would never notice the very failure it exists for.

```javascript
const KEEPALIVE_S = 10, SILENCE_INTERVALS = 3;

function armSilenceTimer() {          // any complete frame, `: ping` included
  window.clearTimeout(silenceTimer);
  silenceTimer = window.setTimeout(
    noteStreamSilence, KEEPALIVE_S * SILENCE_INTERVALS * 1000);
}

function clearSilenceTimer() {
  window.clearTimeout(silenceTimer);
  silenceTimer = null;
}
```
4. **On the same signal, recover the transcript.** When the poll reports the
   turn no longer running, `refresh()` and settle. Do not tear down the stream
   reader; if it recovers, its events are reconciled against the refreshed
   history as they are today.

Parser stub:

```javascript
async function* sseEvents(body, onFrame = () => {}) {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += value;
    let i;
    while ((i = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      onFrame();                            // complete `: ping` counts
      for (const line of frame.split("\n")) {
        if (line.startsWith("data:")) yield JSON.parse(line.slice(5));
      }
    }
  }
}
```

**Report liveness on the existing endpoint, not a new one.** Add `running` to
`GET /api/confirm`'s payload rather than adding `/api/turn-status`:

The endpoint has two `return JSONResponse(...)` paths today
(`webserve.py:1004-1019`); there is no `payload` to add a key to. Collapse them,
so a new field cannot be added to one branch and forgotten in the other:

```python
    @app.get("/api/confirm")
    async def get_confirm():
        p = session.pending
        payload = {
            "grants": tools.SESSION_GRANTS.active(),
            "running": session.lock.locked(),
            "last_resolution": session.last_resolution,
        }
        if p is not None:
            payload.update(
                id=p.id, summary=p.summary, kind=p.kind, subject=p.subject,
                grantable=tools.SessionGrants.is_grantable(p.kind, p.subject),
                remaining_s=max(0.0, p.deadline - _monotonic()),
            )
        return JSONResponse(payload)
```

`p.deadline` does not exist yet: `confirm()` computes the deadline inside the
`try` (`webserve.py:465`), after `_Pending` is constructed (`webserve.py:458`).
Set it on `p` at construction instead. `Session._initialize` also needs
`self.last_resolution = None` and `self.current_turn_id = None` — both
`Session` and `SetupSession` route through it, so one place covers both.

That is one field on a route the client is already polling once a second, and
it needs no second request, no second auth surface, and no revision counter.
The ordering is already correct for it: `store.append` runs per message during
the turn, and `session.lock.release` is scheduled only after `_TURN_DONE`
(`webserve.py:964-965`), so `running: false` implies the durable history is
complete — `refresh()` after that observation cannot race the last write. A
`history_revision` counter on `ConversationStore` would be a new layer earning
nothing.

Do not start a second recovery polling loop: confirmation recovery already
owns the sole in-flight poll. Let its successful reconciliation settle a
silence-detected turn:

```javascript
function noteStreamSilence() {
  streamSilenceDetected = true;
  $("pending-text").textContent =
    "Live updates interrupted; checking Microclaw…";
}

function settleIfRecovered(state) {
  if (streamSilenceDetected && !state.running) {
    streamSilenceDetected = false;
    recoverySettled = true;
    stopConfirmationRecovery();
    activeTurnAbort?.abort(); // runTurn treats this accepted turn as sent
  }
}

// Called by reconcileConfirmation after applying confirmation/grant state:
settleIfRecovered(pending);
```

Note it does not call `refresh()` itself. Aborting hands the turn back to the
one exit path, which already refreshes and clears the busy state; doing both
would repaint twice and split the teardown across two places.

**Abort the stalled fetch when recovery settles, or the page stays busy
forever.** This is the half that makes "without restarting" true. `runTurn`'s
`for await` (`serve.html:631`) is parked on a `reader.read()` that never
resolves, so the composer handler never returns and `setBusy(false)`
(`serve.html:443-451`) never runs: `send` and `msg` stay disabled and the
spinner stays up, even after `refresh()` has painted the recovered transcript.
The operator would be left looking at a finished turn they still cannot reply
to — the same dead end, one screen later. There is no `AbortController` in
`serve.html` today.

So give the POST a controller, abort it from the settle path, and let the
existing composer teardown run. However, accepted and streamed-to-completion
are different states. If the abort escapes `runTurn`, the composer's `sent`
variable remains false and it restores the submitted text into the message
box, inviting an accidental duplicate hardware request. Catch the deliberate
abort inside `runTurn` and return `true`: the POST was accepted and the server
finished the turn even though this response body did not.

```javascript
async function runTurn(text) {
  recoverySettled = false;
  const turnAbort = new AbortController();
  activeTurnAbort = turnAbort;
  const res = await apiFetch("/api/prompt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: text }),
    signal: turnAbort.signal,
  });
  if (!res.ok) {                       // keep today's toast, 409 included
    toast(res.status === 409 ? "A turn is already running." : await detail(res));
    activeTurnAbort = null;
    return false;
  }
  currentTurnId = res.headers.get("X-Microclaw-Turn-ID");
  const live = history.concat([{ role: "user", content: text }]);
  const state = { asst: null };
  paint(live, true);
  startConfirmationRecovery();         // before the body is consumed
  armSilenceTimer();                   // a stream silent from byte 0 must arm it
  try {
    for await (const ev of sseEvents(res.body, armSilenceTimer)) {
      applyEvent(live, ev, state);
      schedulePaint(live);
    }
  } catch (err) {
    if (!(err.name === "AbortError" && recoverySettled)) throw err;
    return true;                       // accepted; the server finished the turn
  } finally {
    activeTurnAbort = null;
    stopConfirmationRecovery();        // otherwise the 1 Hz poll outlives the turn
    clearSilenceTimer();
  }
  return true;
}
```

`live`, `state` and `paint(live, true)` are today's lines, not additions — the
`for await` body needs them. `startConfirmationRecovery` and
`stopConfirmationRecovery` have no other call site: without the pair above the
poll never starts, and without the `finally` it runs at 1 Hz forever after every
turn.

Only the recovery path sets `recoverySettled` before aborting. A user-initiated
network abort or unrelated `AbortError` must not be mislabeled as a completed
turn.

Prefer this over duplicating teardown in the settle path — one exit. It is not
covered by limb 4, though: closing the stream ends `sseEvents` normally, while
the recovered abort returns from a `catch`. Limb 3 owns it, and needs to assert
both that the composer is usable and that the prompt was not handed back.

Do not automatically resubmit the prompt. The original worker deliberately
continues after a client disconnect (`webserve.py:911`), and replaying the POST
could duplicate hardware actions. It would also 409 against the still-held
session lock. Aborting the client's fetch does not stop the turn, which is the
documented behaviour and the reason this is safe.

### Number every emitted event, so the next occurrence is diagnosable

Ships with this block (operator decision, 2026-09-01). The mechanism behind this
incident is still unidentified, and recovery that works does not explain what
failed: without this, a recurrence is diagnosed exactly as poorly as this one
was. Four questions have to become separable — event never emitted; emitted but
absent at the browser; received but rejected by rendering; stream ended or
stalled before `_TURN_DONE`.

**Stamp on the event loop, not on the emitting thread.** `emit`
(`webserve.py:904-908`) is reached from at least two threads: the turn thread,
and block 60a's `microclaw-acq-teardown` waiter, which reinstalls the sink and
emits through it (`tools.py:4293-4322`). A counter incremented in `emit` would be
a cross-thread counter, and `next(itertools.count())` being atomic is a CPython
implementation detail, not a contract this codebase should lean on. Assign the
number inside the `call_soon_threadsafe` callback instead — the loop is
single-threaded by definition, needs no lock, and the resulting order *is* the
delivery order, so a gap in what the browser applied means a loss between the
loop and the browser rather than a stamping artifact:

```python
        seq = 0                           # per turn, touched only on the loop

        def emit(event):
            def deliver():
                nonlocal seq
                if event is _TURN_DONE:   # sentinel object: it has no keys
                    queue.put_nowait(event)
                    return
                seq += 1
                queue.put_nowait({**event, "seq": seq})   # never mutate the caller's dict
                note_emitted(event.get("type"), seq)      # log; deltas counted, not printed
            try:
                loop.call_soon_threadsafe(deliver)
            except RuntimeError:
                pass  # Ctrl-C closed the loop mid-turn; serve()'s finally saves.
```

A `nonlocal` counter rather than `itertools.count`, because `itertools` is not
imported in `webserve.py` and this needs no import. Note that inside
`post_prompt`, `queue` is the local `asyncio.Queue` shadowing the stdlib module
(`webserve.py:901`) — already true today, but it is the kind of thing a paste
gets wrong.

`_TURN_DONE` is an `object()` compared by identity in `events()`
(`webserve.py:974`), so it must pass through unstamped; the turn's final count is
recorded in the summary below instead. Reuse the turn ID already being added for
`last_resolution` — do not mint a second identifier for the same turn.

**Log the events, not the tokens.** Every event carries a `seq`, but the log
records only non-`text_delta` events individually, plus one per-turn summary
line. `text_delta` is emitted per streaming chunk (`agent.py:667`), so a line
each would put thousands of lines per turn on the console the operator is
watching — and the diagnostic question is never which token went missing:

```text
[microclaw turn 6f2a1c] seq 12 confirm_request
[microclaw turn 6f2a1c] seq 13 confirm_resolved
[microclaw turn 6f2a1c] done: 418 events (391 text_delta), final seq 418
```

**Type, turn, and sequence only — never payloads.** Not merely for volume:
`AuditLog.add_secret` redaction applies to audit records, so a printed event
payload bypasses it completely, and `confirm_request` carries the operator-facing
summary while tool results can carry anything a tool read. The type is the entire
diagnostic value here.

**No new log file and no new endpoint.** This goes to stdout, beside the
confirmation audit (`webserve.py:401`) and the late-acquisition-event fallback
(`webserve.py:408-413`), which is what the operator already sends. The browser
keeps its last applied `seq` and writes it to the devtools console when silence
is detected and when a turn settles — one `console.warn`, no reporting endpoint,
no client state posted back to the server.

That leaves the comparison to whoever reads a report, which is deliberate: the
recovery above must not depend on any of this, and nothing here may change what
the browser does with an event. An unknown key is already ignored by `applyEvent`
(`serve.html:503-537`).

### Show the deadline

`_Pending` does not currently record its deadline — `confirm()` computes it as
a local at `webserve.py:465`. Store it on `_Pending` and return `remaining_s`
with pending confirmation state, then show a countdown in the banner.

On expiry, say `Confirmation timed out and was declined` rather than reverting
to a generic working spinner — which is what `hideConfirm` does today
(`serve.html:598`). **Polling pending state alone cannot supply that message**:
the turn thread clears `session.pending` before emitting `confirm_resolved`
(`webserve.py:490`), so a timed-out confirmation and one answered from another
browser both read as "no pending ID". A client countdown is not authoritative
either: background timer throttling, clock skew, or approval from another
browser near the deadline could make it falsely report a timeout.

Carry the authoritative decision in `confirm_resolved`, and retain a small
bounded recently-resolved record on `Session` so polling can recover the same
answer when that stream event was missed.

Both consumers must then render it. `applyEvent`'s `confirm_resolved` case calls
`hideConfirm(ev.id)` (`serve.html:536`), and `hideConfirm` restores the default
spinner text (`serve.html:598`) — so on a **working** stream a timeout would
still read `MicroClaw is working…`, which is the original complaint. Give
`hideConfirm` the decision and let it render the timeout wording; the poll's
`last_resolution` path then reuses the same function rather than a second one.

Two implementation details decide whether this works:

- **Record it on `_Pending`, and promote it in the `finally`.** `decision` is
  not in scope where `confirm_resolved` is emitted (`webserve.py:489-491`) —
  every outcome returns from inside the `try`, through the one `decided()`
  funnel. Have `decided()` stash the string on `p`; the `finally` then has both.
- **That placement is also the correct filter.** The auto-approved-by-grant and
  `declined:no-stream` paths return before any `_Pending` exists
  (`webserve.py:446-457`), and the browser never saw a banner for either. Keying
  off `p` excludes them for free; recording inside `decided()` alone would
  surface a session-grant auto-approval as a resolution message for a banner
  that never existed.

**Scope the record to a turn, not to a clock.** Give every accepted prompt a
turn ID, return it in the streaming response headers, and attach it to the
resolution. Retain one resolution through completion and clear it when the
next turn is accepted—not when the resolving turn ends. Clearing at turn end
would defeat recovery by a browser whose polling connection returns only after
completion. The client displays a resolution only when its turn ID and
confirmation ID match the active/recovering turn, then dedupes by resolution
ID. A stale entry from the preceding turn is therefore neither re-shown nor
misattributed.

A time-based retention window would be a knob nobody can justify. Invalidate
at the next-turn transition instead. This also makes page reload behavior
deterministic: the endpoint can report the preceding completed turn's
resolution, but a client without a matching recovering turn does not display
it.

`decided()` is a single `return self._audit_confirmation(...)`
(`webserve.py:434-443`), so hooking in after the append needs a temporary:

```python
# Before defining decided(), so its closure over p is bound on every path:
p = None

def decided(decision, decided_by=identity, grant_id=None, grant_identity=None):
    recorded = self._audit_confirmation(...)      # unchanged arguments
    if p is not None:                             # only when a banner exists
        p.decision = decision
    return recorded

# In the finally, where pending is cleared. An unexpected exception before a
# successful audit has no authoritative decision to retain.
decision = getattr(p, "decision", None)
resolution = {"id": p.id}
if decision is not None:
    resolution.update(turn_id=self.current_turn_id, decision=decision)
    self.last_resolution = dict(resolution)
self.pending = None
emit({"type": "confirm_resolved", **resolution})

# GET /api/confirm, alongside `running`; retained until the next prompt starts.
payload["last_resolution"] = session.last_resolution
```

At the beginning of `POST /api/prompt`, after the request has been accepted and
the lock acquired, assign a new turn ID and clear `last_resolution`. Include
that ID as `X-Microclaw-Turn-ID` on the `StreamingResponse`.

**Report it on `GET /api/confirm` as well, or the reload path cannot match
anything.** A response header reaches only the client that issued the POST. A
page reloaded mid-turn — limb 5, and the one recovery route that already works
today — never sees it, so its `currentTurnId` is null and a turn-ID match would
reject the very resolution it needs. Add `turn_id: session.current_turn_id`
beside `running`, and have a client with no turn ID of its own adopt the one the
server reports. The client
reconciles `last_resolution` by both turn and confirmation ID, and remembers
the last resolution it displayed so a one-second poll does not repeat the
message. The countdown remains informational only and must never approve,
deny, or extend a request.

## Using the stubs

They are a starting point, not a patch. Three are close to pasteable —
`sseEvents`, `runTurn` and `get_confirm` above — and the rest are contracts.
Six names appear in the stubs with no implementation, and each is a real
decision rather than boilerplate:

- `reconcileGrants(grants)` — the point of the change. Diff against the
  rendered grant IDs and call the existing `showGrants` (`serve.html:545`) only
  on a change; unconditional at 1 Hz it replaces every Revoke button under the
  cursor.
- `noteStreamSilence()` — sets `streamSilenceDetected` and the pending text;
  must be idempotent, since the poll can settle a turn while it is queued, and
  must not overwrite `Waiting for your confirmation.` while a banner is up.
  Telling an operator about connectivity on top of the approval they are being
  asked for inverts the priority the banner exists to assert.
- `notePollFailure()` — counts consecutive failures, warns after a few, backs
  off. Must never hide a visible banner.
- `needsPairing()` — remote-mode only; says the session needs re-pairing and
  leaves any banner up.
- `currentTurnId` — declared in the `runTurn` stub, but nothing yet *reads* it.
  The turn-ID match that makes `last_resolution` safe to display is specified in
  prose and has no code here; write it with the reconciler.
- The countdown itself, from `remaining_s`.

Two server-side pieces likewise have prose and no stub: the keepalive timeout
inside `events()` (`webserve.py:969-978`), and assigning `current_turn_id` /
clearing `last_resolution` in `post_prompt` with the ID echoed as
`X-Microclaw-Turn-ID` on the `StreamingResponse` (`webserve.py:980-984`, which
already passes a `headers` dict).

One integration point is not a stub at all: boot already fetches
`/api/confirm` once and renders from it (`serve.html:794-798`). Route that
through the same reconciler instead of leaving two paths that can show a
banner, or limb 5 tests one path while the operator hits the other.

And the harness comes first (§1). Every stub above is currently unreachable by
any test in this repo.

## Tests

- A missed `confirm_request` is recovered through polling and can be approved.
- A missed `confirm_resolved` cannot leave a stale banner.
- A stream that stalls while open reports interrupted live updates, and does so
  from silence rather than from end-of-stream.
- Keepalive frames are emitted during a quiet turn and are ignored by the event
  parser without touching the transcript, while a **complete** comment frame
  resets the semantic-delivery clock and partial bytes do not.
- A timeout visibly reports denial and the final transcript is recovered.
- Approval or decline from another browser cannot be mislabeled as a timeout by
  a throttled local countdown; displayed resolution comes from server state.
- A session-grant auto-approval, which raises no banner, reports no resolution.
- A broken or stalled POST stream never causes prompt resubmission.
- A page reload and an in-place stream failure both recover the same pending ID.
- Polling cannot approve a confirmation and cannot extend its deadline.
- A 401 stops polling instead of retrying, and does not consume the auth
  failure budget that `POST /api/confirm` needs.
- Slow polls never overlap, and stopping after a 401 performs no final fetch.
- After a silence-detected turn settles, the composer is re-enabled and the
  spinner is gone — asserted on `send.disabled`, not only on the transcript —
  the deliberate abort raises no error toast, and the submitted prompt is not
  restored into the composer.
- An `AbortError` that recovery did not cause is **not** reported as a completed
  turn: it propagates, the prompt comes back, and no false success is painted.
- Auto-approved/no-stream paths cannot touch an unbound `_Pending`, and produce
  no banner resolution.
- A reloaded page with no turn ID of its own adopts the server's and still
  matches the resolution for the turn it rejoined.
- A resolution remains recoverable after its turn completes, is matched by turn
  and confirmation ID, and is cleared when the next prompt is accepted.
- Sequence numbers are monotonic and gapless within a turn, restart at 1 on the
  next prompt, and are assigned in delivery order when two threads emit
  concurrently — driven with a real second thread, not one emitter called twice.
- `_TURN_DONE` passes through unstamped and still ends the stream by identity.
- Stamping does not mutate the dict the caller passed, and an event carrying an
  unknown key renders exactly as it does today.
- The log records one line per non-delta event plus a turn summary, and no
  `text_delta` lines: asserted on a turn with many deltas, so a regression to
  per-token logging fails rather than merely looking noisy.
- No event payload reaches the log — asserted on a `confirm_request` whose
  summary contains a recognisable string.
- Grant chips are not rebuilt on a tick that changed nothing.
- Confirmation and status state remain inaccessible without the session's
  browser authentication.
- Normal uninterrupted streaming retains its present behavior and does not
  duplicate transcript blocks.

## Gate

Almost everything here is settleable off-rig, and should be — that is the point
of §1's harness. Two things are not, and they are what the gate is for:

- **A real keepalive over a real browser.** One driven session on the demo
  machine, a forced confirmation (a frame count above the configured threshold),
  and the browser's network disabled for ~60 s while the turn runs. Score it from
  the artifacts: the confirmation audit's decision, the banner the operator saw
  and when, and whether the composer was usable afterwards without a reload.
- **That the operator sees what the design claims they see.** The wording under
  each state — pending, interrupted, timed out — is the deliverable. Nobody but a
  person at the screen can score it.

Save the server console log **and** the browser devtools console from the same
session. The disconnect limb is scored by comparing the emitted sequence numbers
against the browser's last applied one; with only one of the two, the limb
reports nothing.

No new rig capability is needed, so the demo machine is sufficient and neither
M5 nor M2 is required.

**Collect both halves of the sequence evidence.** The server's turn summary is
in the console log; the browser's last applied sequence is in the devtools
console. A report with only one of them cannot distinguish "never emitted" from
"absent at the browser", which is the whole reason the sequencing ships. The
gate step must say to save both, or it will come back with one.

## Blocks

Coordinated from 2026-09-01. **design/69a owns its own blocks and ledger**, like
design/48 through design/62 and design/65;
`design/35-usability-and-pfs-checklist.md` points here and does not track these
rows. The ten steps in `CLAUDE.md` §"The block workflow" govern; where this
section and that one disagree, that one wins.

**This is demo-machine work end to end.** No M5, no M2, no Nikon. §"Gate" says
so and the operator reaffirmed it at assignment (2026-09-01): almost everything
here settles off-rig under node and pytest, and what is left is one driven
session in a real browser on the demo machine. That session scores all three
blocks at once rather than one gate per block — the operator's time is the
budget, and there is nothing microscope-specific to observe. Do not book
instrument time for any of this.

**Strictly sequential.** All three blocks touch `serve.html` and `webserve.py`;
69a-2's silence detector consumes 69a-1's reconciler, and 69a-3's gate scores
the whole design. One worktree, one runner, one block at a time.

### 69a-1 — the recovery module, the poll, and the server state it reads

§"Poll confirmation state for the lifetime of every active turn", §"Show the
deadline", and the `get_confirm` stub. Covers investigation limbs 1, 2, 5 and 6.

Two commits, in this order, because the diff is otherwise unreadable:

1. **The harness, with no behaviour change.** Move the confirmation, grant and
   boot-recovery reconciliation out of `serve.html`'s inline `<script>` into a
   new `recovery.js` beside `transcript.js`, exporting pure reconciliation over
   an injected `fetch` and an injected clock, with the DOM calls left in
   `serve.html` as injected callbacks. Drive it under node in
   `tests/test_recovery_js.py`, following `tests/test_transcript_js.py`'s
   pattern exactly — `global.window = {}`, `require`, and
   `pytest.mark.skipif(shutil.which("node") is None)`. Route boot's existing
   `/api/confirm` fetch (`serve.html:794-798`) through the same reconciler in
   this commit; two paths that can raise a banner is the thing §"Using the
   stubs" warns about.
2. **The poll.** `startConfirmationRecovery` / `stopConfirmationRecovery` /
   `pollConfirmation` / `reconcileConfirmation` per the stubs, called from
   `runTurn` and its `finally`; 1 Hz, one request in flight, suspended while the
   document is hidden and resumed immediately on `visibilitychange`; a 401
   stops the chain and performs no final fetch; other failures back off without
   hiding a visible banner; `reconcileGrants` diffs against rendered grant IDs
   and calls `showGrants` only on a change.

Server side, same block: `_Pending.deadline` set at construction, `p.decision`
stashed by `decided()` and promoted in the `finally`, `Session.last_resolution`
and `Session.current_turn_id` initialised in `_initialize` (which both `Session`
and `SetupSession` route through), the turn ID assigned and `last_resolution`
cleared at the top of `post_prompt` and echoed as `X-Microclaw-Turn-ID`, and
`GET /api/confirm` collapsed to one `payload` carrying `grants`, `running`,
`turn_id`, `last_resolution` and — when pending — `remaining_s`. The banner
countdown and `hideConfirm`'s timeout wording ship here too: on a healthy
stream, `confirm_resolved` carries the decision and `hideConfirm` renders it, so
a timeout stops reading `MicroClaw is working…`.

### 69a-2 — keepalive, silence, and the recovered abort

§"Make the stream's silence observable, and recover the outcome" and §"Abort the
stalled fetch when recovery settles". Covers limbs 3 and 4.

Server: the keepalive inside `events()`, with **one getter task held across
iterations** and the *wait* timed out — not `asyncio.wait_for` around
`queue.get()` — plus the `finally` that cancels it when the client disconnects.
Client: `sseEvents` gains a complete-frame callback that a `: ping` comment
fires and partial bytes do not; the silence timer is armed at turn start as well
as on every frame; `noteStreamSilence` is idempotent and must not talk over
`Waiting for your confirmation.`; `settleIfRecovered` settles a silence-detected
turn when the poll reports `running: false`, and aborts the POST through an
`AbortController` so the composer's one exit path runs. `runTurn` catches its
own deliberate abort and returns `true`, so the prompt is not handed back.

Limb 3 owns the recovered abort — assert `send.disabled` and the composer
contents, not only the transcript — and an `AbortError` recovery did not cause
must still propagate.

### 69a-3 — sequence numbers, the log, and the gate

§"Number every emitted event, so the next occurrence is diagnosable".

The counter is a `nonlocal` assigned **inside the `call_soon_threadsafe`
callback**, never in `emit`, because block 60a's `microclaw-acq-teardown` waiter
reaches `emit` from a second thread. `_TURN_DONE` passes through unstamped and
still ends the stream by identity; the caller's dict is never mutated. The log
records one line per non-`text_delta` event plus one turn summary, and carries
type, turn and sequence only — never a payload, because `AuditLog.add_secret`
redaction does not reach a `print`. The browser writes its last applied `seq` to
the devtools console on silence and on settle, and nowhere else: no endpoint, no
client state posted back.

**The gate ships in this block** and scores 69a-1, 69a-2 and 69a-3 together.
Runbook on the branch as `design/69a-gate.md` per step 4, with its computed
scorer as `design/69a-gate.py` and a bridge-shaped self-test as
`design/69a-gate-selftest.py`, following design/60 through design/63. The
wording limbs — what the operator sees under pending, interrupted and timed-out
— are the half only a person at the screen can score and stay prose steps; the
sequence comparison is arithmetic over two saved logs and belongs in the
scorer, run independently per limb, exiting nonzero, owning its own log file.
The gate step must say to save **both** the server console and the browser
devtools console: with one of them the disconnect limb reports nothing.

### What the coordinator pinned before assignment (2026-09-01)

Read against `main` at `9f18133`. These are facts the stubs assume and do not
state.

- **A new `.js` file is not free.** `assets.py` inlines exactly two assets by
  fixed tag and **raises** when a tag is missing (`assets.py:58`), and
  `load_page` is called for `history_viewer.html` as well as `serve.html`. A
  `recovery.js` tag exists only in `serve.html`, so the inline table has to
  become page-aware rather than gain a third unconditional row.
  `tests/test_history_viewer.py:120` asserts the current shape and moves with
  it. `pyproject.toml`'s `[tool.setuptools.package-data]` lists the four
  bundled assets by name; a fifth that is not listed is missing from every
  wheel and every installed machine, and the demo machine is an installed
  machine.
- **`recovery.js` must load the way `transcript.js` does** — a plain script
  attaching to `window`, not an ES module — because the inliner wraps it in
  `<script>…</script>` and `serve.html` runs under `"use strict"` in one IIFE
  scope. `tests/test_transcript_js.py` is the working example of driving it
  under node.
- **`CONFIRM_TIMEOUT_S` is module-level** (`webserve.py:99`), so the short
  deadline the tests need is a monkeypatch, not a new argument.
- **Both session classes route through `Session._initialize`**
  (`webserve.py:336`, called again at `:523` by `SetupSession`), so
  `last_resolution` and `current_turn_id` need one initialisation site.
- **Node is present on the coordinator's machine** (v25.2.1). Record whether it
  was present when the suite ran: a skipped node test is not evidence, and this
  block's entire off-rig case rests on those tests.

## Out of scope

- Changing acquisition confirmation thresholds.
- Changing the default-deny confirmation policy.
- Interrupting or retrying hardware calls.
- General redesign of the conversation protocol beyond the recovery state
  required here.

## Review notes (2026-09-01, off-rig, against `main`)

Diagnosis and the polling decision confirmed against the code. Changes made
above, in two review passes:

1. An open, silent stream is the leading explanation, not an established fact.
   An ordinarily ended stream already recovers via the composer's `refresh()`,
   so recovery must also cover silence. Hence the keepalive and client silence
   timeout while retaining the other failure modes as investigation targets.
2. `/api/turn-status` and `ConversationStore.revision` dropped in favour of one
   `running` field on `GET /api/confirm`.
3. A 401 must stop the poll — the remote-mode auth limiter would otherwise turn
   recovery into a lockout.
4. The timeout message needs a stated mechanism; polling cannot distinguish
   timeout from resolution.
5. `showGrants` cannot be called unconditionally at 1 Hz.
6. The tests listed were unwritable as the code stands: `serve.html`'s inline
   script has no harness. Extracting the reconciler is now the first task, not
   an afterthought.
7. Follow-up review corrected three implementation details: keepalive comments
   must reset silence through a parser activity callback; polling must neither
   overlap nor recurse through its 401 stop path; and timeout wording must come
   from a server-retained resolution rather than a throttled browser countdown.
   All three accepted; the resolution record is the better call, and the
   countdown-only inference it replaced was mine.
8. Third pass: recovering the transcript is not enough — the stalled fetch must
   be aborted or the composer stays disabled behind a finished turn. The
   resolution record is scoped to the turn and promoted from `_Pending`, which
   removes an unspecified retention window and filters the two paths that never
   raise a banner. This pass proposed resetting silence per `read()`; the next
   pass corrected that to complete-frame delivery.
9. Fourth pass corrected the remaining boundary errors: a recovered abort now
   returns an accepted result so the prompt is not restored; `_Pending` is
   initialized before the decision closure and written conditionally; a
   turn-ID-scoped resolution survives turn completion until the next prompt;
   and only a complete SSE frame resets semantic silence. The earlier
   last-event inference was also softened because `confirm_resolved` could have
   arrived before a later stream stall.

10. Fifth pass answered "can the implementer paste these?" — no, and fixed the
   three that most looked as if they could. The `runTurn` stub had dropped
   `live`/`state`/`paint` (a `ReferenceError` on the first event), swallowed the
   409 toast, and assigned two undeclared names under `"use strict"`; nothing
   called `startConfirmationRecovery`, and nothing stopped the poll on a normal
   turn, so it would have run at 1 Hz forever. The silence detector had no code
   and, armed only by frames, would not have fired on a stream silent from byte
   0. The `get_confirm` stub added a key to a `payload` the real two-return
   endpoint does not have. The remaining six names are now listed as contracts
   rather than left to look like boilerplate.

11. Sixth pass audited the fourth pass's own changes. Pasting all five JS stubs
   into one scope failed outright — `recoverySettled` was declared twice, once by
   the fifth pass — so declarations are now consolidated in the first block and
   the composition is checked, not assumed. Four design gaps came out of the same
   read: a header-only turn ID is invisible to the reloaded page that needs it
   most; `hideConfirm` would still show `MicroClaw is working…` after a timeout
   on a *healthy* stream, which is the original complaint; the recovered abort is
   not covered by limb 4 as claimed; and the silence message would talk over a
   live confirmation prompt.

12. Also added on that pass: the keepalive must not cancel a queue getter every
   quiet tick, a gate (demo machine, real browser, real disconnect — nothing
   rig-specific), and the observation that the per-event sequencing had never
   been decided in or out of scope.

13. Per-event sequencing is now decided in, with the operator's agreement, and
   written as a decision rather than an investigation sketch. Designing it turned
   up two things the sketch had assumed away: `emit` is reached from the
   teardown waiter as well as the turn thread, so the counter belongs on the
   event loop rather than in `emit`; and `text_delta` is per streaming chunk, so
   "log every event" would put thousands of lines per turn on the operator's
   console. Payload-free logging is now justified by redaction, not only volume.

The deployment question is answered as far as the code can answer it: the normal
run is loopback with no proxy and no remote auth, so proxy buffering is demoted
and the 401 hazard is something the fix introduces rather than something that
happened. Still not verified: the audit and history artifacts from the Zeiss
session itself, and any confirmation that this session was in fact launched the
normal way. **The mechanism remains unidentified**, which is the honest state —
the recovery above is designed to hold whatever it turns out to be, and the
event sequencing is what would name it next time.

## Run ledger

design/69a is coordinated and owns its own blocks and ledger, like design/48
through design/62 and design/65. `design/35-usability-and-pfs-checklist.md`
points here and does not track these rows.

Baseline before the first block: `main` `9f18133`, coordinator-run suite
**2756 passed / 99 skipped / 2 warnings** (2026-09-01, `uv run pytest -q`).
Node **v25.2.1** present on the coordinator's machine, so no JS test skipped
for its absence — record that again on every block, because this design's
entire off-rig case rests on tests that skip silently without it.

| block | branch | start | implementation | gate | merge |
| --- | --- | --- | --- | --- | --- |
| 69a-1 | `design69a/recovery-poll` (deleted) | `b3e23c0` (2026-09-01) | `42c3c8b` (harness) + `6a44fdc` (poll; amended after review round 1, four findings). Suite **2776 / 99 / 2**, coordinator-run in the worktree, baseline + 20; node **v25.2.1** present, no JS test skipped | scored with 69a-3 | `fe32d6d` |
| 69a-2 | `design69a/keepalive-and-abort` | `60ae1b3` (2026-09-01) | | scored with 69a-3 | |
| 69a-3 | `design69a/event-sequencing` | | | **demo machine, one driven session**, scores all three blocks | |

### What block 69a-1 cost, and what it proved

**Review round 1 returned four defects and every one failed on the pre-fix tree
for its stated reason.** The load-bearing one reproduced the incident's own
shape: `reconcileConfirmation` compared `resolution.id === confirmId`
unconditionally, so a **reloaded page — where `confirmId` is null — could never
match**, and the turn-ID adoption written for exactly that case was unreachable.
Driven against the runner's own helper, a reloaded page produced `[]`: no calls,
no banner, no explanation. An operator who comes back to the screen is told
nothing, which is what happened at the Zeiss microscope.

**The test named for that path did not exercise it.** It asserted
`reconcile(state, confirm_id="c1")` — a page with the banner already up, not a
reload. `CLAUDE.md`'s *a fixture that cannot reach the code is not coverage of
it*, in a block whose whole premise is that untested browser code caused the
incident. Probing the scenario by hand is what found it; reading the assertion
would not have.

**The second defect is worth keeping for its evidence.** `decided()` stashed the
decision *before* `_audit_confirmation` returned, and `AuditLog.append` does
`mkdir`/`open`/`write`/`fsync` (`conversation.py:153-163`). On the pre-fix tree
the new test prints it exactly:
`assert {'id': 'pending-id', 'turn_id': 'turn-1', 'decision': 'approved'} is None`
— an approval published to the browser for a confirmation the audit log never
recorded.

**Two process findings, both the coordinator's:**

- **The runner prompt prescribed a command that had never been run.** It said
  `uv run pytest`; a fresh worktree has no `.venv`, so the first turn reached
  for PyPI inside a no-network sandbox and stopped. That is `CLAUDE.md`'s *a
  literal command must be established, never guessed* — written for rig
  runbooks, and just as true of a runner prompt. The fix was to provision the
  worktree's `.venv`, **run** `.venv/bin/python -m pytest -q` first, and hand
  over the established command along with the benign `mmpycorex` SyntaxWarning
  it prints. Provision the worktree before writing the prompt, not after.
- **Codex's automatic approval reviewer is live on CLI 0.152.0**, three versions
  past what the `codex-runner` skill documents. It refused the runner's attempt
  to retry the failing command with escalation, citing the prompt's own
  no-network instruction — it enforced a coordinator constraint against the
  agent's workaround. `--strict-config` also accepted 0.152.0's config on the
  revise path. Both halves of the version-drift question are answered
  positively; the skill's note can stop being a caveat.

**What the runner did not deliver, twice asked:** the six contract decisions
from §"Using the stubs" and anything found wrong in the design. `result.md`
carried commit SHAs and a suite count both rounds. Not worth a third turn — the
coordinator reads the diff anyway — but a runner report that answers only the
mechanical questions is a report that has told you nothing you could not have
counted yourself.

### A gap 69a-2 found before it started: `pending-text` has four writers

The design speaks of replacing `MicroClaw is working…` as though that element
had one other state. It now has four, and three of them are live on `main`:

| writer | text |
| --- | --- |
| `hideConfirm` / default | `MicroClaw is working…` |
| `showConfirm` (`serve.html:612`) | `Waiting for your confirmation.` |
| 69a-1's countdown (`:559`) | `Waiting for your confirmation. Ns remaining.`, rewritten at 1 Hz |
| design/60b's `acquisition_progress` (`:532`) | `frames N / M`, per progress event |

69a-2 adds a fifth, `Live updates interrupted; checking Microclaw…`. The design
states one ordering rule — the silence message must not talk over a live
confirmation, because *telling an operator about connectivity on top of the
approval they are being asked for inverts the priority the banner exists to
assert*. It does not rule on the progress counter, which did not exist when that
sentence was written.

**Decided at assignment: confirmation > silence > progress > default.** Silence
outranks progress because a progress number that stopped updating is worse than
no number — it is a stale reading presented as a live one, which is this
document's own complaint about `MicroClaw is working…`. Progress cannot arrive
*during* silence anyway, since it travels on the stream that went quiet; the
ordering matters when the two race at a recovery boundary.

Four unsynchronised assignments to one element is the actual defect. Resolving
them in one place is folding, not a layer.

### Coordination log

- **2026-09-01, assignment.** Blocks cut three ways above; the design's own
  ordering ("the harness comes first") decides 69a-1's two commits. Confirmed
  with the operator that this is demo-machine work throughout and that one gate
  session at the end covers all three blocks rather than three sessions.
  Codex runner unavailable until 19:25 CEST; 69a-1 is queued behind a watcher
  rather than implemented inline, per step 2.
- **2026-09-01, 69a-1 merged** at `fe32d6d`, branch and worktree deleted local
  and on `origin`. One start turn (lost to the environment defect above), two
  revision turns. The 19:25 usage window did not open on the second: the first
  attempt fired at 19:25:02 and was refused with *try again at 7:25 PM*, so the
  launcher retries on a usage-limit error and stops for the coordinator on any
  other failure.
