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
- **That the operator sees what the design claims they see.** The wording is the
  deliverable, and nobody but a person at the screen can score it. This sentence
  said "pending, interrupted, timed out" when it was written. **There are now
  five states, and the gate must exercise them as a priority order, not as a
  list** — the block-69a-2 defect was not a wrong string, it was a correct string
  the resolver never reached:

  | tier | text |
  | --- | --- |
  | 1 confirmation | `Waiting for your confirmation.`, then `… Ns remaining.` once the poll starts |
  | 2 resolution disclosure | `Confirmation timed out and was declined` |
  | 3 silence | `Live updates interrupted; checking Microclaw…` |
  | 4 progress | `frames N / M` |
  | 5 default | `MicroClaw is working…` |

  A limb that reaches a state with nothing below it competing proves almost
  nothing. The one that matters is a turn which has **already emitted acquisition
  progress** before the confirmation times out, because that is where the
  disclosure was masked, and it is the incident's own shape.

Save the server console log **and** the browser devtools console from the same
session. The disconnect limb is scored by comparing the emitted sequence numbers
against the browser's last applied one; with only one of the two, the limb
reports nothing.

No new rig capability is needed, so the demo machine is sufficient and neither
M5 nor M2 is required.

**One gate session scores all three blocks.** 69a-1 and 69a-2 merged without
gates of their own — the same call design/65 made for 65a and 65b — so this
session is the only place the poll, the keepalive, the abort and the sequencing
are seen working together by a person. Sizing it as if it covered only 69a-3
would leave the other two ungated on `main`.

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
| 69a-2 | `design69a/keepalive-and-abort` (deleted) | `60ae1b3` (2026-09-01) | `26cc47b` + `142b90f` (`main` merged mid-block, see below) + `c2cfdab` (review round 1, one product finding and two coordinator corrections). Suite **2782 / 99 / 2**, coordinator-run, baseline + 6; node **v25.2.1** | scored with 69a-3 | `2bcb136` |
| 69a-3 | `design69a/event-sequencing` | `8d65084` (2026-09-01) | `0adbcd2` + `4f4443f` + `0a2090e` (review 1) + `691700f` (review 2, the gate's defect) + coordinator runbook/test corrections through `2716497`. Suite **2788 / 99 / 2**, coordinator-run; selftest **11/11**, coordinator-run on both trees | **round 1** — 3 PASS, 1 product defect (`691700f`). **round 2** — reload poll confirmed from the HAR; product defect (reloaded page never entered the busy presentation) fixed at `3a6318d`; three gate defects. **round 3** — every product claim measured: 30 keepalive pings, reload recovery, adopted presentation, and the timeout disclosure *and* settle refresh landing on a page that did not start the turn. **round 4** — computed gate **PASSED**; its one FAIL was the scorer treating a mid-turn reload as a delivery failure. Limb 4 closed off-rig after four split turns. Selftest **17/17** | see below |

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

### What block 69a-2 cost, and what it proved

**The coordinator's own ruling was the block's one product defect.**
§"A gap 69a-2 found before it started" ranked
`confirmation > silence > progress > default` and did not rank the *resolved*
timeout wording, so the implementation put it in the bottom tier — reasonably,
and it said so rather than hiding the choice. `pendingProgress` is cleared only
at turn start, so once any acquisition in a turn has emitted progress, a later
confirmation that timed out was masked by a frozen frame count. Driving the
implementation's own resolver:

```
stale progress + timeout -> "frames 700 / 700"
silence + timeout        -> "Live updates interrupted; checking Microclaw…"
timeout alone            -> "Confirmation timed out and was declined"
```

The first line is this incident's own shape — a 700-frame acquisition whose
refusal the operator never saw — reproduced by the fix for it. The reasoning
that put silence above progress (*a number that stopped updating is a stale
reading presented as a live one*) applies one tier further down, and the
disclosure is stronger still: it is the authoritative outcome of a decision the
operator is owed. **Corrected ruling:
`confirmation > resolution disclosure > silence > progress > default`**, with
its own slot rather than an overloaded `pendingDefault`.

**A ruling written in a coordination note is not a ruling the runner can read.**
The worktree was created from `60ae1b3`; the ruling was committed afterwards as
`0ce9705`; the prompt then cited `0ce9705` as the branch point. It was false —
`git merge-base --is-ancestor 0ce9705 HEAD` in that worktree — and the runner
reported the section as nonexistent rather than pretending to have read it. It
implemented the ruling correctly only because the prompt carried the table
inline. `CLAUDE.md` step 1 already says a worktree sees committed history; the
missing half is that the worktree must be created **from** the commit carrying
them, which `--is-ancestor` will answer in one line.

**The runner declined a widening and was right.** Asked to consider clearing
`pendingProgress` when an acquisition finishes, it reported that no
acquisition-finished event exists and refused to invent completion semantics.
Checked: the emitted acquisition events are `acquisition_diagnostic`,
`acquisition_disclosure` and `acquisition_progress`, and the browser switches on
none that signal completion. The tier fix removes the harm without the
invention. **Whether a `tool_result` for an acquisition tool should clear the
progress slot is a real question this block deliberately left open.**

**The harness is the block's most valuable artifact.** `run_browser_turn`
extracts the live `setBusy`, `runTurn` and composer-submit source out of
`serve.html` and executes it under node against a `read()` that **genuinely
never resolves** — not one that resolves with nothing, which is a different
failure and not the incident. That is what makes limb 3 evidence rather than
assertion, and it is the first time this project has executed `serve.html`'s
own control flow in a test.

### What block 69a-3 cost, and what it proved

**Both review findings were in the gate, not the product** — the half that gets
no review pass and runs unattended in front of an operator.

**Limb 8 would have failed on the rig for behaviour that is deliberate.** The
runbook seeds a recognizable marker into a confirmation summary, and
`Session._audit_confirmation` **prints that summary to stdout by design**;
`confirm()`'s docstring says why — under `--allow-remote` it is the only record
the person standing at the microscope can see. The scorer tested the marker
against the whole log, so the gate would have returned FAIL after a session that
cost a five-minute timeout wait, and the failure would have said nothing true.
Limb 8's real claim is narrower: no event payload reaches the **event** log.

**And the gate's own selftest could not catch it.** Its artifact came from a turn
that raises no confirmation, so no audit line ever existed, and the L8-can-fail
check injected the marker artificially — *a fake that encodes your assumption is
not a test of it*, inside the instrument written to enforce that rule. The fix
was to generate the artifact through the real `Session.confirm` decline path.
**Verified by mutation**: reverting the scorer to the whole-log check flips the
selftest's "gate passes on real product output" from `ok` to `FAIL`, so it now
discriminates on the defect it previously slept through.

**The server console log was empty for the entire session.** Python
block-buffers stdout when it is not a terminal and none of this block's log lines
flushed — measured, **0 bytes** reach the redirected file while the process runs.
Every computed limb therefore hung on Ctrl-C shutting down cleanly through a
PowerShell pipeline, with no way for the operator to notice mid-session. That is
58e's empty transcript again. Fixed with `flush=True` (a flushed write also
flushes output queued before it, so ordering with the unflushed audit line is
preserved) plus a live capture check in the runbook **before** the five-minute
limb, because a setup failure found after the expensive limb costs the session.

**Two things the coordinator checked rather than assumed**, both of which look
like passes when they are not: the runbook's capture-check regex was run against
output the product actually produced (4 matching lines — a regex that matches
nothing reads exactly like a passing check, which is 52c's defect); and
`emit_progress` was read to confirm it fires on the first frame *and* the final
planned frame, so the runbook's 2-frame acquisition genuinely sets the competing
`frames 2 / 2` that makes limb 4 a control which can fail.

**The runbook's pin was one commit too early** — `0adbcd2`, which predates the
scorer fix, so a checkout at `4f4443f` would have satisfied it while running the
broken limb 8. Repinned to `0a2090e`, keeping the `--is-ancestor` form so a
later runbook amendment still satisfies it.

### Demo gate round 1, 2026-09-01 — scored from the artifacts

Evidence: `~/Documents/Documents - Beyonce/Projects/Micro-Claw/block69a-evidence`
(confirmation audit, history JSONL, `computed-score.log`, a DevTools screenshot,
and a **0-byte** `server-console.log`). Run from `design69a/event-sequencing`
before the capture corrections landed.

| limb | verdict | evidence |
| --- | --- | --- |
| 1 pending banner + countdown | **PASS** | operator observed both states |
| 2 silence | **NOT EXERCISED** | see below — not a failure of the product |
| 3 composer after settle | not reported | |
| 4 timeout outranks stale progress | **PASS** | history `[5]` runs the 2-frame acquisition *before* the 100k request at `[9]`, so `frames 2 / 2` was genuinely competing; audit records `declined:timeout` at 19:59:24 |
| 5 reload recovers the same pending ID | **PASS** | screenshot shows the full banner restored after a reload |
| 6, 7 sequence arithmetic | **NOT EXERCISED** | no server log, no console log |
| 8 payload-free logging | **falsely PASSED** | scorer defect, below |

**The gate found a real product defect, and it is this incident's own shape.**
`startConfirmationRecovery()` has one call site, inside `runTurn`. Boot calls
`reconcileConfirmation()` **once** and never starts the loop, so a page reloaded
while a confirmation is pending shows the banner and is then inert — no
countdown, no stale-banner removal, and **no timeout disclosure**, because
`last_resolution` is only ever fetched by the poll. The operator sits in front of
a live banner, the confirmation expires, and the page tells them nothing.

The screenshot is the evidence: after the reload the Network panel shows the boot
sequence and then exactly **one** `confirm` request, not one per second; the ~200
requests over 3.54 min are the polling from before the reload. **2786 tests, a
discriminating selftest and two review rounds all missed it**, because
§"Tests" asks that a reload recover the same pending ID — which it does — and
nothing asserts the reloaded page keeps polling. Revision 1 made a reloaded page
able to *match* a resolution; nothing starts the loop that would fetch one.

**Limb 2 measured nothing, and the tell was the countdown.** The operator set
Firefox's Network throttling to Offline for 60 s and the countdown **kept
updating** — and the countdown is poll-driven, so `127.0.0.1` was still being
served. **Firefox's throttling does not apply to loopback.** Keepalives kept
arriving and silence correctly never fired. Split for round 2: keepalive
*arrival* over a real browser stays on the rig, where a real HTTP stack is the
point; the 30 s detector is settled off-rig by `run_browser_turn`, whose
`read()` genuinely never resolves.

**L8 passed on an empty log**, which is the gate breaking the rule it exists to
enforce. Its checks are negative assertions — no `text_delta` line, no payload
marker — and an empty file satisfies both. A limb that cannot fail is not a
criterion (block 58a). L6 and L7 must be audited for the same shape.

**Four operator round trips went to step 0 alone**, none of them to the product.
That cost is written up generically in `CLAUDE.md` step 6; the short version is
that every fact needed was already in `design/` and the runbook was written from
this design instead.

### Demo gate round 2, 2026-09-01 — scored from the artifacts

Evidence: `~/Documents/Documents - Beyonce/Projects/Micro-Claw/block69a-evidence-round2`
— a **5,343-byte** `server-console.log` (round 1's was 0), a 1.2 MB HAR, the
confirmation audit, the history JSONL, `computed-score.log`, and a DevTools
screenshot. Run from `design69a/event-sequencing` at `2716497`.

**`691700f` works, and the HAR proves it without an operator judgement.** The
page reloaded at 22:37:32.248; after the boot sequence, `GET /api/confirm` runs
continuously at a 1.02 s median for the remaining 19 s of the capture — 20 polls,
the same pending ID `dd5760d7…` before and after, each response carrying
`remaining_s: 276.4`. Round 1's screenshot showed exactly **one** confirm request
after boot. That regression is closed and limb 5 is the standing check for it.

| limb | verdict | evidence |
| --- | --- | --- |
| 1 banner + countdown | operator observation owed | the server served `remaining_s` at 1 Hz across the whole pending window |
| 2a `: ping` frames | **NOT EXERCISED** | the only captured stream was aborted by the reload 7 s into its quiet window — under the 10 s keepalive. Zero pings is arithmetic, not a failure. The earlier turn's `POST` was never captured: the HAR recording began 12 s after that submit |
| 2b silence detector | settled off-rig | `tests/test_recovery_js.py` 20/20, node **v25.2.1**, coordinator-run |
| 3 composer after settle | operator observation owed | |
| 4 timeout outranks stale progress | **NOT EXERCISED** | turn `79b414f1` emitted no `acquisition_progress` at all; the 2-frame run was the *previous* turn (`086e59ac`) and `runTurn` clears `pendingProgress` at turn start. The prompt left the interval unstated, Microclaw asked, the operator answered `0s interval` — and that reply split the turn |
| 5 reload | **PASS**, both halves | same ID, and the poll continues (above) |
| 6 sequence arithmetic | **PASS** | 2 turns, final seq equals emitted-event count in both |
| 7 browser last-applied seq | **NOT EXERCISED** | no console export was returned |
| 8 payload-free logging | **vacuous PASS** | `PAYLOAD_69A_GATE_SECRET` occurs **0 times** in the whole session: the seeding submit was skipped, so the negative assertion had nothing to be negative about |

**The gate found the defect underneath the one it found in round 1, and it is
the incident.** `#pending` is `class="hidden"` in the markup (`serve.html:205`)
and only `setBusy(true)` unhides it (`:446`) — called from exactly one place, the
composer's submit handler (`:719`). A reloaded page never runs it. So the poll
that `691700f` correctly started renders the countdown, the silence message and
`hideConfirm`'s `Confirmation timed out and was declined` into a **hidden
element**; there is no Stop button; the composer stays enabled while a turn is
live; and when the turn ends nothing calls `refresh()`, so the agent's reply
never appears. §"Make the stream's silence observable" item 4 requires that
refresh, but `settleIfRecovered` gates it on `streamSilenceDetected`
(`recovery.js:72`), which a reloaded page never sets — it has no stream to go
silent. An operator who comes back to the screen sees a banner, gets no
countdown, is told nothing when it expires, and never sees the answer. That is
the Zeiss session, reproduced by the blocks written to fix it.

**Why 2,788 tests missed it.** `browser_turn_snippets()`
(`tests/test_recovery_js.py:377`) extracts `setBusy`, the recovery wiring block,
`runTurn` and the submit handler out of `serve.html` — and **not the boot IIFE**,
which *is* the reload path. Every reload test drives `Recovery` directly with
stub callbacks, so `showRemaining` lands in a JS array and no fixture can observe
that the element it targets is hidden. *A fixture that cannot reach the code is
not coverage of it*, in the block whose premise is that untested browser code
caused the incident — the same sentence round 1 earned, one layer down.

**Three defects in the gate, all the coordinator's, all fixed before round 3:**

- **L8 still could not fail.** Round 1 taught that an empty log satisfies a
  negative assertion; round 2 showed that a marker which never entered the
  session does too. The scorer now reports NOT EXERCISED unless the marker
  reaches the server log at all — which `Session._audit_confirmation` prints
  deliberately — and the runbook's capture check greps for it while the server
  is still alive. Third occurrence of this shape in one gate.
- **L7 required a `stream silence` line the machine cannot produce.** Keepalives
  are 10 s (`KEEPALIVE_S`) and the detector is 30 s, and limb 2b moved the
  detector off-rig — so the limb would have reported NOT EXERCISED forever. It
  now requires the `turn settled` record and validates any silence record that
  *is* present. **The selftest slept through it because its fake browser log
  wrote the silence line by hand**: a fake encoding the assumption, inside the
  instrument written to enforce that rule, for the second block running. The
  fake is now the demo machine's shape, with a separate control proving a
  captured silence record is still checked.
- **Limb 4's prompt did not hold both acquisitions in one turn.** Fixed by
  pinning the interval in the prompt so nothing is asked, stating why the single
  turn is the whole control, and telling the operator to re-submit if Microclaw
  asks anyway.

Selftest after the corrections: **13/13**, coordinator-run on both trees, every
control firing for its stated reason. Re-scoring round 2's own artifacts with the
corrected scorer turns limb 8 from `PASS` into
`NOT EXERCISED: the marker never entered this session, so its absence proves
nothing` — the discrimination demonstrated on real evidence, not only on a
fixture.

### What a page reloaded mid-turn must present

**Operator decision, 2026-09-01: the reloaded page adopts the running turn in
full.** When boot's `GET /api/confirm` reports `running: true`, the page enters
the same presentation state the tab that started the turn would be in —
`setBusy(true)`: spinner and status row visible, Stop available, composer
disabled. When the poll reports `running: false`, it `refresh()`es the transcript
and releases the composer.

The rejected alternative was to unhide the status row alone. It was rejected
because the composer being live during a running turn invites a submit the
server can only answer with a 409, and because Stop is a plain `POST` that works
without a stream — a page that can show the operator a five-minute wait but not
offer to end it is the wrong half of the incident to fix.

Three things the implementation must get right, each of which is a way this can
go wrong rather than a restatement of the decision:

- **`settleIfRecovered` cannot be the release path.** It returns early unless
  `streamSilenceDetected`, and a reloaded page never sets it. The adopted turn's
  release is keyed on `running` going false, on its own.
- **Releasing must be idempotent and must not fight a real turn.** A page that
  adopted a turn and then submits a new prompt has two owners of `busy`; the
  adoption must not call `setBusy(false)` under a turn the page is itself
  running.
- **`refresh()` on settle is the outcome delivery, not a nicety.** It is the
  half of the incident where the completed response never reached the operator,
  and limb 5d is the only thing that watches it happen.

Implemented at `3a6318d`. `settleIfRecovered` became the single settlement path
for both arrival modes — its predicate takes `streamSilenceDetected` *or*
`adoptedTurn` as recovery ownership — rather than growing a reload-only twin,
and `adoptedTurn` is cleared before its async callback so a repeat reconcile is
idempotent and a later page-owned turn cannot be unlocked by stale adoption
state. A 401 releases the adoption **without** a refresh, which is unauthorized
on that path. Verified by the coordinator: the new test fails on `a4a16b8` by
assertion — `pendingHidden: true, stopHidden: true` while
`pendingText: "Waiting for your confirmation. 42s remaining."` is present, and
`refreshes` never leaving the boot-only value of 1 — which is the defect stated
exactly, not a missing helper. Suite **2789 / 99 / 2**, baseline + 1.

**One thing this leaves open, raised by the implementer and not widened into the
block.** After a 401 the adoption is released, so a recovered confirmation banner
can sit above a re-enabled composer while every request needs re-pairing, and the
notice is a toast rather than a persistent banner. It looks actionable and is
not. This is unreachable on loopback — the middleware returns `identity =
"loopback"` before any auth runs — so no demo gate can see it; it belongs to
whichever design next touches `--allow-remote` pairing.

### Demo gate round 3, 2026-09-01 — scored from the artifacts

Evidence: `~/Documents/Documents - Beyonce/Projects/Micro-Claw/block69a-evidence-round3`
— a 6,516-byte `server-console.log`, a 2.4 MB HAR, two confirmation audits, the
history JSONL, `computed-score.log`, and a screenshot. Run from
`design69a/event-sequencing` at the reload-adoption fix.

**The block's whole purpose is now demonstrated on the rig, and all of it from
artifacts rather than judgement.**

- **Keepalives arrive.** The 100k confirmation's `POST /api/prompt` ran 318.6 s
  and its response body carries **30 `: ping` frames** — one per 10 s across the
  entire blocked wait, exactly `KEEPALIVE_S`. Limb 2a, which had been NOT
  EXERCISED twice.
- **The reload recovers the same decision.** Pending `a880759b…` on turn
  `82e138c0…` before the reload at 23:24:55.315 and after it, `remaining_s`
  273.6 → 273.1, then 221 further polls.
- **The reloaded page shows the turn.** The operator watched the countdown keep
  running and the composer stay disabled. The countdown is decisive on its own:
  `showRemaining` writes into `#pending-text`, *inside* the `#pending` element
  that round 2 left hidden, so a visible countdown is a visible status row.
- **The outcome reaches a page that did not start the turn** — the half of the
  Zeiss incident nothing had ever measured. The trace is exact:

| time | event |
| --- | --- |
| 23:29:28.070 | last poll with the confirmation still pending |
| 23:29:28.902 | server declines on the deadline (`declined:timeout`) |
| 23:29:29.095 | first poll after it: `id: null`, `last_resolution: declined:timeout/a880759b`, `running: true` — the reloaded page renders `Confirmation timed out and was declined` |
| 23:29:30–37 | nine more polls while the agent composes its reply |
| 23:29:38.357 | poll returns `running: false` |
| 23:29:38.362 | **`GET /api/history`** — the adopted page's own `refresh()`, five milliseconds later |

  The server log closes that turn at `done: 30 events … final seq 30`. An
  operator who walked away and came back was shown the refusal and then the
  answer, with no manual reload.

| limb | verdict | evidence |
| --- | --- | --- |
| 1 banner + countdown | **PASS** | operator |
| 2a `: ping` | **PASS** | 30 frames at a 10 s cadence, from the HAR |
| 2b silence detector | settled off-rig | |
| 3 composer after settle | **PASS** on its first half | the operator submitted the next prompt 13 s after the previous turn's settle refresh, so the composer was usable and the spinner gone; whether the prompt text was restored was not reported for this path |
| 4 timeout outranks stale progress | **NOT EXERCISED**, third round | the step-1 turn split again — Microclaw stopped and asked, and the operator answered `1` — so its timeout turn carried no progress of its own; the step-2 turn had both, but a reload gives the fresh page an empty `pendingProgress`, so nothing was competing there either |
| 5a same pending ID | **PASS** | identical id and turn either side |
| 5b poll continues | **PASS** | 221 polls after the reload |
| 5c reloaded page shows the turn | **PASS** on the load-bearing half | countdown visible, composer disabled; Stop's visibility was not reported and is the same `setBusy` statement (`serve.html:446-447`) |
| 5d outcome reaches that page | **PASS** | the trace above |
| 6 sequence arithmetic | **PASS** | 3 turns |
| 7 browser last-applied seq | **NOT EXERCISED**, second round | no console export |
| 8 payload-free logging | **NOT EXERCISED**, honestly this time | the marker was skipped again — and the corrected scorer said so instead of passing. The round-2 fix proving itself on the rig |

**Three instrument failures remain, and not one of them is a product failure.**

- **Limb 4 cannot be held by prompt wording.** Three rounds, three splits. The
  runbook now carries a literal pre-check that reads the last `confirm_request`
  turn out of the server log and reports `LIMB 4 ARMED` or `SPLIT TURN` *before*
  the operator commits five minutes. Run against rounds 2 and 3's real logs it
  reports `SPLIT TURN` for each step-1 turn and `LIMB 4 ARMED` for each step-2
  turn, so it discriminates instead of matching nothing.
- **Limb 7's console capture was a browser-UI guess three times over** — the
  export menu item, then the keyboard route that replaced it. Round 3 came back
  with the **Network** panel's context menu, which offers HAR items and no
  console export at all; the operator then reported that `Ctrl+A` does not
  select console output and that filtering on `last applied seq` matches
  nothing. The leading explanation for the last of those is the Console's
  **`Warnings` filter chip**: the line is a `console.warn`, and it is hidden
  whenever that chip is off. `CLAUDE.md` already says to name the browser; the
  missing half is that naming it is not the same as establishing that the UI
  path exists in it, and a runbook cannot keep paying rig rounds to find out.

  **Asking what the limb actually needed shrank it to almost nothing.** The HAR
  already carries every `data:` frame of every `POST /api/prompt` with its
  `seq`, and `lastAppliedSeq` is by construction the last `seq` pulled from that
  stream — so round 3's HAR alone establishes 33 of 33 and 24 of 24 numbered
  events received with no gaps, both ending on `done`. (The reloaded turn shows
  14 of 30 ending at `confirm_request`, the reload aborting the stream, with the
  remaining 16 delivered by the poll and `refresh()` — the recovery, visible in
  the artifact.) The console line's residual claim is narrow but is the point of
  the block: that the page's own apply loop got there **and can say so during a
  future incident**. So the runbook now asks for a drag-selected line or simply
  a **screenshot of the Console panel**, scored by hand, and says that a missing
  line with `Warnings` lit is a finding about the diagnostic rather than an
  operator failure.
- **Limb 8's marker was skipped twice.** It was a paragraph of prose buried
  after the DevTools setup. It is now its own numbered step, `0b`, with its
  check immediately after it.

**And one runbook defect the operator found by its symptom:**
`Stop-Process -Id $Server.Id` stops `uv`, not the microclaw process `uv run`
starts as its child, so the server kept running and kept `server-console.log`
open — the evidence folder could not be zipped until the console window was
closed by hand. Step 3 now kills the tree with `taskkill /PID $Server.Id /T /F`
and verifies the parent is gone. The mechanism is inferred from the symptom
rather than proven on the machine, so the runbook also names the fallback that
did work and asks for it to be reported if it is still needed.

**Two observations recorded rather than actioned.**

*Firefox restored the previous prompt into the message box across the reload.*
That is the browser's own form restoration, and the box was disabled, so nothing
could be submitted from it. **Not a defect**, and deliberately not fixed:
clearing a field the browser restored would be Microclaw overriding the
operator's own browser, and an operator who had typed something before reloading
would lose it. It is worth knowing that after the turn settles the box is
re-enabled still holding a prompt that already ran.

*The poll went quiet for 57.9 s in the middle of the reloaded page's wait*
(23:25:25 → 23:26:23), while the 30 s `/api/update` timer kept firing. That is
`pollConfirmation`'s deliberate suspension while the document is hidden — the
operator was in the other PowerShell window — and the design asks for it
explicitly. The countdown froze for that minute and resumed on return. Nothing
to change; worth knowing that a hidden tab does not tick.

### Demo gate round 4, 2026-09-02 — the gate passes

Evidence: `~/Documents/Documents - Beyonce/Projects/Micro-Claw/block69a-evidence-round4`
— an 8-turn `server-console.log`, a 1.9 MB HAR, two confirmation audits, the
history JSONL, **the console capture at last**, and `computed-score.log`. The
`Warnings` chip was the answer: with it lit the `turn settled` lines were there
all along.

**The computed gate reported one FAIL, and the FAIL was mine.** L7 said
`turn 995951c2: settled at 3, server finished at 15`. The HAR settles it: that
turn's stream started 07:22:53 and was **cut at 07:24:18.207, the operator's
reload**, at `max_seq 3` — precisely the number the browser reported. A page
navigated away from mid-turn runs `runTurn`'s `finally` on its way out and logs
whatever it had applied. **Reloading mid-turn is the workflow this entire design
exists to support, and its own gate was scoring one as a delivery failure.**

L7 now reports a short settle instead of failing it, and keeps a control that can
still fail: at least one turn must be delivered end to end, no settle may claim a
seq the server never emitted. On round 4's artifacts that reads
`1 turn(s) delivered end to end (f9228a68@10/10); 1 settled short, consistent
with a mid-turn reload (995951c2@3/15)`.

**L8 was skipped for the third time, so the step was deleted instead.** The
marker submit had now been missed in rounds 2, 3 and 4. A gate step skipped three
times is a gate step that should not exist: the limb seeds itself from the
session's own confirmation summaries, which `Session._audit_confirmation` prints
deliberately and which *are* the payload that must not reach an event line. On
round 4 that is non-vacuous evidence rather than a marker's absence —
**100 `text_delta` events across 8 turns produced no event line**, and neither of
the two payload tokens drawn from the session's own confirmations reached one.

| limb | verdict | evidence |
| --- | --- | --- |
| 2a `: ping` | **PASS** again | 29 frames across the 322 s confirmation on turn `f5077dcc` |
| 4 timeout outranks stale progress | **CLOSED off-rig** — see below | fourth consecutive split turn |
| 6 sequence arithmetic | **PASS** | 8 turns |
| 7 browser last-applied sequence | **PASS** | one turn end to end, one short settle explained by the HAR |
| 8 payload-free logging | **PASS** | 100 text_deltas, 0 event lines; 2 payload tokens, 0 leaks |

Limbs 1, 2b, 3, 5a–5d were established in round 3 and were not re-run.

**Limb 4 is closed off-rig, and the honest statement is that no rig session ever
produced the state it needed.** Four rounds, four split turns — the pre-check
added after round 3 reports `SPLIT TURN` for every one of the four
`confirm_request` turns across rounds 2–4. The turn boundary is the model's
choice and no wording held it. The tier order it would have observed is settled
by `test_pending_text_priority_includes_authoritative_resolution_disclosure`,
which extracts the **real resolver source out of `serve.html`** and drives it:
its first case is exactly this limb's claim — `frames 700 / 700` competing with
`Confirmation timed out and was declined`, disclosure wins — and it also asserts
that `pending-text` has exactly one writer. That is a genuine settlement, not a
shrug, but it is off-rig and this row says so rather than implying a rig
observation exists.

**One product change came out of round 4, at the operator's request.** Firefox
restores the textarea across a reload — including the value the page had already
cleared at submit — so an adopted page came back holding a prompt that had
already run, and handed it back live when the turn settled. Round 3 recorded this
as benign; the operator reaffirmed it should not happen, which is theirs to
decide. `adoptRunningTurn` now clears the composer, **only on adoption**: a
reload with no turn in flight leaves whatever the operator had typed alone. The
boot test asserts both halves and was watched failing on the pre-fix tree, where
the adopted page reports `msgValue: "stale restored prompt"`.

**Two findings the operator raised that are not this block's**, both now rows in
`design/35`'s carried-forward register: the agent tells operators that a nonzero
`interval_s` makes an acquisition stoppable, which is false — Microclaw cannot
stop anything mid-tool and whatever stopping exists is Micro-Manager's, equally
available at a 0 s interval; and the 100k disclosure *offers* to segment a run
that NDTiff already rolls by itself, which dresses ordinary storage behaviour as
a decision the operator must make.

**The instrument cost this gate four rounds and the product cost it two.** Two
product defects (the reload poll, then the reload presentation) against six gate
defects: a limb that could not fail, a limb that could not run, a prompt that
could not hold its own control, an operator step skipped three times, a browser
UI path guessed three times, and finally a limb that scored the block's own
central workflow as a failure. Every one of them was written by the coordinator
and reviewed by nobody, which is `CLAUDE.md`'s standing point about gate code,
now with a number attached.

### Closing state

**The gate passed in round 4** and the branch merged to `main`. All three blocks
— 69a-1, 69a-2, 69a-3 — plus two step-7 fixes on the same branch (the reload
poll and the reload presentation) are on `main`; `design69a/event-sequencing` is
deleted locally and on `origin`.

**What is settled on the rig**, all of it scored from artifacts:

- keepalives arrive on a blocked stream — 30 frames over 318.6 s in round 3, 29
  over 322 s in round 4, both at the 10 s cadence `KEEPALIVE_S` sets;
- a reload mid-confirmation recovers the same pending decision and keeps polling
  it at 1 Hz;
- the reloaded page presents the running turn — countdown visible, composer
  disabled;
- the timeout disclosure **and** the completed reply reach that page without a
  manual reload (round 3's 23:29:28.902 → 23:29:29.095 → 23:29:38.362 trace);
- event numbering is internally consistent over 8 turns, the browser reports how
  far it got, and no payload reaches an event line.

**What is not settled on the rig, stated plainly:**

- **Limb 4**, the timeout disclosure outranking a stale progress count. Four
  rounds, four split turns; the tier order is settled by an off-rig test that
  drives `serve.html`'s own resolver source, and no rig session ever produced the
  competing state.
- **Limb 2b**, the 30 s silence detector, settled off-rig by design — no browser
  control on this loopback deployment can create an open-but-silent transport.
- **The composer clear** from round 4 shipped with off-rig coverage only: a test
  that executes `serve.html`'s real boot path, watched failing before the fix.
  It is a one-line clear on the adoption path; the next reload an operator does
  will show it.
- **The `--allow-remote` 401 path**, where a released adoption leaves a
  confirmation banner above a live composer with only a toast about re-pairing.
  Unreachable on loopback, so no demo gate can see it; it belongs to whichever
  design next touches pairing.

**Two findings this gate surfaced that belong to other work** are rows in
`design/35`'s carried-forward register, added 2026-09-02: the agent's false claim
that a nonzero `interval_s` makes an acquisition stoppable, and its offer to
segment an acquisition that NDTiff already rolls by itself.

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
- **2026-09-01, 69a-2 merged** at `2bcb136`, branch and worktree deleted local
  and on `origin`. One start turn, one revision. Three coordinator defects in
  two blocks, all the same family — telling a runner about something it cannot
  reach: a suite command never run in its worktree (69a-1), and a design section
  committed after its worktree was cut (69a-2). Both are now in `CLAUDE.md`
  step 2.
- **2026-09-01, 69a-3 implemented and pushed, not merged.** `dfb3d88` is on
  `origin/design69a/event-sequencing`. Unlike 69a-1 and 69a-2 this block owns a
  gate, so it stays on its branch until the demo-machine session passes —
  workflow steps 5 through 8. **The session scores all three blocks**, and it is
  the only place the poll, the keepalive, the abort and the sequencing are seen
  working together by a person.
- **What the operator needs to know before booking it:** it costs a real
  five-minute wait at limb 4, because `CONFIRM_TIMEOUT_S` has no configuration
  path and shortening it would gate a path the product does not have; and it
  requires **both** the server console log and the browser devtools console, or
  limb 7 reports NOT EXERCISED, which is the entire reason sequencing ships.

- **2026-09-02, gate passed in round 4 and design/69a closed.** Four demo-machine
  rounds: two product defects and six gate defects. The instrument cost more
  rounds than the product did, and every gate defect was written by the
  coordinator and reviewed by nobody. Two of them are new shapes worth carrying:
  a limb that scored the block's own central workflow — a mid-turn reload — as a
  failure, and an operator setup step that was skipped three times before being
  deleted in favour of seeding the check from the session itself.
