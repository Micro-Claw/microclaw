# design/24 — `event_queue.put()` from a hook is a silent no-op

`hook_docs.py:89` — the document Claude reads before writing any hook — tells it
this works:

```python
event_queue.put({"axes": {"time": next_t, "z": 0}, "exposure": 50})
```

It does not. Under `tools._acquire_with_hooks`, the runner every microclaw
acquisition goes through, that call appends to a queue no thread will ever read
again. It does not raise. It does not warn. It does nothing.

This is true **today**, for every hook the agent generates, independent of any
feature that might want it. It was found while designing design/26 (ML ROI
detection), which needs it; it is written up separately because it is not a
missing feature, it is a **live defect in what we tell the agent it can do.**

Measured by `design/24-event-queue-spike.py` (run it; stages A_1–A_8 and A_10
take a few seconds and no hardware — they drive pycro-manager's real
`EventQueue` and a replica of its real consumer thread. Stage A_9 runs only
when Micro-Manager is reachable on localhost:4827 — the **demo config** is
enough — and verifies the engine-side answers for real; it prints SKIPPED
otherwise. Two live-MM runs on 2026-07-15: the first passed the follow-up part
in full and hung in the abort part — exposing the terminator deadlock now
covered by Fix 2a/A_10 — and the second, after the fix, passed every stage
end-to-end. The spike exits non-zero if any stage that ran fails, so it
doubles as a regression check on the demo-config machine).

---

## Why it does nothing

`tools._acquire_with_hooks` (`tools.py:613`) is:

```python
with Acquisition(directory=save_dir, name=name, **hook_fn_kwargs) as acq:
    acq.acquire(events)
```

`acquire()` is non-blocking — it puts the event list on the queue and returns.
The `with` block then exits *immediately*, and `Acquisition.__exit__` calls
`mark_finished()`, which puts `None` on the same queue
(`acquisition_superclass.py:219`). The queue is now `[events, None]`.

pycro-manager's event-source thread (`java_backend_acquisitions.py:32`,
`_run_acq_event_source`) is a `while True: get()` loop that **breaks on `None`**.
It drains both items at memory speed — long before the camera has returned the
first frame — and sends `acquisition-end`. By the time `image_process_fn` runs for
image #1, *nothing is reading the queue.*

(Precisely: the thread is not *gone* at that moment. On `None` it sends
`acquisition-end` and then sits in `while not acq.is_finished():
block_until_events_finished(0.01)` until the engine drains what it was already
given. That changes nothing — it has left the `get()` loop for good, and the queue
is orphaned from the instant the terminator is popped.)

Spike A_1 replays exactly that lifecycle:

```
A_1  current runner:  acquire(events) -> __exit__ -> mark_finished()
     events reaching the engine : ['tile_0', 'tile_1', 'tile_2']
     source left the get() loop : True
     hook's follow-up delivered : False
     VERDICT: event_queue.put() from image_process_fn is a NO-OP here.
```

## Why nobody noticed

Every hook in `hooks.py` either **mutates hardware** (`IntensityAdaptiveHook`,
`FocusFeedbackHook`, `AutofocusHook`, `MMAutofocusPluginHook`) or **drops images**
(`PositionFilterHook` and `MMPluginHook` return `None`). Rejecting work needs no
event queue. **Adding** work is the one thing nothing in the tree has ever tried —
so the adaptive half of the adaptive-acquisition machinery has never once been
exercised, despite being promised since the first research doc
(`design/01-research.md:68`: *"The agent can generate new acquisition events … in
response to what it is seeing, enabling fully adaptive protocols."*).

## Why it matters more than a missing feature

`hook_docs.py` is *the agent's spec for hooks.* So the failure mode is not "hooks
can't do X." It is:

> The agent writes a hook that calls `event_queue.put(...)`, exactly as documented.
> The acquisition completes normally. The log says seven ROIs were found. **No ROI
> was ever imaged.** Nothing errored.

A silent, sourced-looking success — design/21's failure with a different mask on.
The hook did what the docs said; the docs were wrong; and the run *looks* like it
worked.

---

## Why not pycro-manager's own adaptive-acquisition pattern?

pycro-manager documents an adaptive pattern of its own
(<https://pycro-manager.readthedocs.io/en/latest/adaptive_acq.html>): hold the
`Acquisition` open, `future = acq.submit(event)`, analyze via
`future.await_image_saved(axes)`, submit more. Two questions fall out — would
`acq.submit()` work instead of `event_queue.put()`, and why prefer the
generator? Both deserve measured answers, because that page is *sourced*, and
design/21 says sourced is not the same as right.

**`submit()` does not exist here.** The pinned pycro-manager (1.0.2) has no
`submit()` — the "latest" docs track a newer API surface. Its 1.0.2 equivalent
is `acquire()` itself: callable repeatedly, returns the same
`AcquisitionFuture`, and internally is just a `put()` on the same event queue
(`acquisition_superclass.py:279`). So the real question is whether a *second*
`acquire()` reaches the engine — and it does, **iff the caller is still inside
the `with` block**, because then the put lands *ahead of* `mark_finished()`'s
terminator instead of behind it. Spike A_6 measures exactly this and the
follow-up is delivered. It even fails loudly once the acquisition has finished
(`AcqAlreadyCompleteException`, `acquisition_superclass.py:247`) — a genuine
point in its favor over the raw queue, which just orphans.

**But "still inside the `with` block" is this document's entire problem.**
Something must decide when `__exit__` may finally run, and that decision is
Fix 2a in full: the `survey_complete()`-and-nothing-in-flight stopping state,
the idleness-not-wall-clock watchdog, the put-before-`image_done` ordering.
A_6 cannot run without reimplementing that loop inline on the caller's thread
— the machinery is the completion problem itself, not a generator artifact.
The two designs move the same loop to different threads. Given equal
machinery, the generator wins on three microclaw-specific grounds:

1. **`_acquire_with_hooks` stays the single runner.** It already accepts a
   generator (Fix 2: the diff is a type hint). The held-open pattern needs a
   second runner restructured around a wait loop between `acquire()` and
   `__exit__`.
2. **The hook would need the `Acquisition` object.** Hooks are built by
   `_resolve_hook` *before* the `Acquisition` exists inside
   `_acquire_with_hooks`, so `acq` cannot be injected the way `ctrl`/`guard`
   are. And hooks are agent-written code, hash-pinned in the manifest: handing
   them `acq` hands them `abort()`, `mark_finished()`, and `get_dataset()`.
   The candidates queue is the minimal surface — a hook can add work and
   nothing else — and the generator is one chokepoint where a cap on derived
   events can be enforced centrally instead of trusted to every hook.
3. **Detection must not migrate out of hooks.** The documented pattern's full
   form — a main-thread loop that awaits each image, analyzes, submits — moves
   the analysis out of `image_process_fn` entirely. In microclaw, detection
   code in a hook is registered, consented-to, and logged (`read_hook_log`); a
   per-experiment analysis loop on the runner's thread would be a second home
   for agent-written analysis outside the manifest. It would also wait for
   images to reach *disk* (`await_image_saved`) when the hook already gets
   pixels in-process on the processor thread.

If a future pin ships `submit()`, nothing changes: `submit()` is the same
queue put behind the same terminator discipline, and the completion machinery
remains ours to write.

---

## Fix 1 — stop promising it. Do this now.

Independent of everything below, and worth landing on its own: `hook_docs.py`
must not hand the agent an API that silently does nothing.

And the correction has to be total, because the obvious partial correction is
itself wrong. The first draft of this section proposed marking `event_queue`
as **conditional on the runner** — discarded under the list runner, legal under
Fix 2's generator runner. It is not legal there either. pycro-manager hands the
**real** `EventQueue` to any 3-argument `image_process_fn`
(`acquisition_superclass.py:354`), so hooks keep receiving it under the
generator runner — but by the time any image is processed, that queue already
holds `mark_finished()`'s terminator behind the generator being expanded. A
hook's `put(event)` lands **behind the `None`** and is orphaned the moment the
generator exhausts and the terminator is popped. Spike A_5 measures it: the
same `put()` that A_1 shows dropped under the list runner is dropped under the
generator runner too.

So the section must say: **never call `event_queue.put(...)` — it is silently
discarded under every microclaw runner.** Under `_acquire_survey_with_detector`
(Fix 2) the supported path is the injected candidates queue, and the doc should
say so and point there.

`hook_docs.py:98-99` makes a second promise with the same defect:
`event_queue.put(None)` "ends the acquisition early." Same orphaning — the
hook's `None` queues behind the terminator that is already there, so it ends
nothing, under either runner. Delete that promise too. If a hook ever needs to
stop a survey early — it found what it came for — that is its own mechanism (a
sentinel on the candidates queue, or a stop flag the generator checks each
pass), not a `put(None)`.

A hook that needs to add work and cannot must **fail loudly**, not quietly log a
success.

## Fix 2 — hand `acquire()` a generator

`EventQueue.get()` expands a **generator** in place and only reads the next queue
item — the `None` — once the generator raises `StopIteration`
(`acquisition_superclass.py:50`). And `Acquisition.acquire()` already accepts a
generator (`acquisition_superclass.py:256`).

So a generator that yields the survey events and then *blocks*, feeding follow-up
events as the hook finds them, holds the event source open for the whole scan;
`mark_finished()`'s terminator waits its turn behind it; and `__exit__` blocks in
`await_completion()` until the generator is done. Spike A_2:

```
A_2  generator runner:  acquire(gen) — gen holds the source thread open
     events reaching the engine : ['tile_0', 'tile_1', 'tile_2', 'tile_1']
     hook's follow-up delivered : True
```

`tile_1` appears twice: once as a survey tile, then again as the follow-up the
hook asked for. That is the feature.

**`_acquire_with_hooks` needs one small change — and this section's first
version claimed it needed none, which the rig disproved.** `acq.acquire()` does
accept a generator as-is, so the original claim was "the whole diff is a type
hint." But the generator must also **own the acquisition's terminator** (the
abort deadlock, Fix 2a below and spike A_10), and it can only do that holding
the acquisition's real event queue — which does not exist until
`_acquire_with_hooks` constructs the `Acquisition`. So `events` becomes "a
list, or a factory called with the live event queue":

```python
with Acquisition(directory=save_dir, name=name, show_display=True, **hook_fn_kwargs) as acq:
    if callable(events):
        events = events(acq._event_queue)
    acq.acquire(events)
```

Still small; no longer nothing. As in design/19: *the reuse is already sitting
in `_acquire_with_hooks`; what is missing is a caller that hands it a
generator* — plus, now, the queue that generator must be able to terminate.

The hook still must not `put()` to pycro-manager's queue — that is a no-op under
this runner too (Fix 1, spike A_5). It puts to **our** candidate queue, which the
generator drains — injected the same way `_resolve_hook` injects `ctrl`/`guard`.

## Fix 2a — and the generator's watchdog must measure *idleness*, not elapsed time

The generator must never block forever, or `__exit__` never returns. The obvious
way to write that is a deadline:

```python
yield from survey_events
deadline = time.monotonic() + max_wait_s     # WRONG
while time.monotonic() < deadline:
    ...
```

That is a bug, and **it is this document's own bug wearing a different mask.**

**The survey is dispatched at socket speed and executed at camera speed.**
`yield from survey_events` hands all N events to the event-source thread in
microseconds; the scan then takes N × (exposure + settle) to actually happen. So a
deadline that starts when the survey is *submitted* is a cap on the entire
acquisition, not a stall detector — and a 400-tile survey at a 300–500 ms dwell
blows a 120 s default by minutes.

When the cap blows, the generator returns, `StopIteration` lets `mark_finished()`'s
`None` through, `acquisition-end` goes out, and every event the hook adds after that
moment is dropped on the floor. Silently. The run completes, the log shows hits, and
the late ones were never imaged — which is *precisely the failure this document
exists to remove, reintroduced by its own fix.* Spike A_3 runs the same 12-tile
survey with a late hit through both watchdogs:

```
A_3  a long survey with a LATE detection (hit on the last tile)
     total-duration cap  -> follow-up delivered: False   timed out: True
     idle watchdog       -> follow-up delivered: True    timed out: False
```

So the watchdog resets on **activity** — a candidate arriving, or the count of
returned survey images advancing — and fires only when nothing has happened for
`max_idle_s`. **A long survey is not an anomaly; an image that never comes back is.**

Two smaller things in the same generator, both load-bearing:

* **The stopping state is `survey_complete() and candidates.empty()`**, not
  `survey_complete()` alone. The hook `put()`s a candidate *before* it marks the
  image done, so those two conditions together are the only moment at which no
  detection can still be in flight.
* **That ordering inside the hook is therefore not cosmetic.** If a hit on the
  *last* survey tile marked progress first, the generator could observe a complete
  survey over a momentarily-empty queue and return before the candidate landed —
  losing exactly the event the feature exists to catch. And "could" undersells
  it: the race window is the detector's own analysis time, so it is *normally
  open*. Spike A_8 gives the detector a realistic analysis cost (longer than the
  generator's poll) and the reversed ordering — mark done, then analyze, then
  put — drops the last-tile hit **deterministically**, with no timeout and no
  error. The rule is: `image_done()` comes after detect *and* put, always.

And two things only the **closed loop** settles — spike A_7's camera, unlike
A_2's, feeds every engine event back through the hook, the way the real engine
does, so the hook sees the frames its own follow-ups produce:

* **A hook must refuse to analyze frames whose label is not in the survey
  set.** A follow-up images the same scene that triggered it, so an unguarded
  detector re-fires on its own follow-up — and what happens next depends only
  on timing, which is worse than a clean failure. If frames return faster than
  the generator's candidate poll, each detection images itself into another
  detection and the acquisition is **self-sustaining** until something external
  stops it (A_7 measures one real hit becoming 22 follow-ups before the cap).
  Slower, and the generator sees a complete survey over an empty queue, exits,
  and the second generation is orphaned behind the terminator — a silent drop.
  Either way, wrong; the label guard is mandatory.
* **`image_done()` on a derived frame is harmless, though it looks like an
  early-exit bug.** The suspicion: derived frames inflate `n_done` to
  `n_survey` while survey tiles are still pending, so `survey_complete()` goes
  true early and late survey detections are orphaned. FIFO makes that state
  unreachable: the survey is fully dispatched before the first candidate is
  even yielded, so every follow-up frame returns *after* every survey frame,
  and the counter can only overshoot once `survey_complete()` is already
  legitimately true. A_7 prints the overshoot (`n_done=4` against `n_survey=3`)
  happening strictly after completion, changing nothing. The label guard above
  should skip derived frames before counting anyway — this just means a hook
  that forgets loses nothing.

Spike A_4 covers the two ways it can still hang — a survey that finds nothing (no
candidate ever arrives) and a camera that stops returning images halfway — and
confirms it terminates on both, and says which.

---

## The code

Two things: a cross-thread progress counter (genuinely new — nothing in microclaw
tracks acquisition progress today), and a generator-backed runner beside
`_acquire_with_hooks`.

```python
class SurveyProgress:
    """How many survey images have come back.

    Written by the processor thread (the hook), read by the event thread (the
    generator) — so it is a real cross-thread object, not a counter.
    """

    def __init__(self, n_survey: int) -> None:
        self._n_survey, self._lock, self._done = n_survey, threading.Lock(), 0

    def image_done(self) -> None:
        with self._lock:
            self._done += 1

    @property
    def n_done(self) -> int:
        with self._lock:
            return self._done

    def survey_complete(self) -> bool:
        with self._lock:
            return self._done >= self._n_survey


def _acquire_survey_with_detector(ctrl, guard, positions, save_dir, name, hook,
                                  progress, candidates, max_idle_s=60.0,
                                  **shape_kwargs) -> dict:
    """A survey whose event stream stays OPEN, so a hook can extend it.

    The watchdog measures IDLENESS, not elapsed time: the survey is DISPATCHED at
    socket speed and EXECUTED at camera speed, so a deadline started at submission
    is a cap on the whole scan and silently drops every late event once it blows
    (design/24 Fix 2a — a real bug in the first draft; spike A_3 is its regression
    test).
    """
    survey_events = _build_acquisition_events(
        position_labels=[p["name"] for p in positions],
        xy_positions=[(p["x_um"], p["y_um"]) for p in positions],
        **shape_kwargs,
    )

    def event_stream(event_queue):
        # event_queue is the acquisition's REAL queue, injected by
        # _acquire_with_hooks (the factory call in Fix 2).
        try:
            yield from survey_events        # dispatched in microseconds...
            last_activity = time.monotonic()  # ...executed over the next minutes
            last_count = progress.n_done

            while True:
                try:
                    event = candidates.get(timeout=0.1)
                except queue.Empty:
                    pass
                else:
                    last_activity = time.monotonic()
                    yield event
                    continue

                # BOTH conditions: the hook put()s a candidate before it marks
                # the image done, so an empty queue over a complete survey is
                # the only state in which no detection can still be in flight.
                if progress.survey_complete() and candidates.empty():
                    return

                n = progress.n_done         # images still arriving => rig alive
                if n != last_count:
                    last_count, last_activity = n, time.monotonic()

                if time.monotonic() - last_activity > max_idle_s:
                    hook.note_stalled(max_idle_s)   # loud in the log, not silent
                    return
        finally:
            # The generator OWNS the terminator (Fix 2a, spike A_10):
            # Acquisition.abort() clears the queue, mark_finished()'s None
            # included, and a generator that trusts that None deadlocks
            # __exit__ forever on any abort. Runs on every exit path.
            event_queue.put(None)

    dataset_path = _acquire_with_hooks(guard, save_dir, name, event_stream, hook)
    return _adaptive_result(dataset_path, hook.log_path, ...)
```

`max_idle_s` is a **stall detector**, so it is sized against *one* tile's
(exposure + settle + processing), not against the length of the scan — and
against the **slowest** thing a tile can legitimately do: a tile that runs a
hardware-autofocus hook (`MMAutofocusPluginHook`) can take far longer than
exposure + settle, and a watchdog that fires on a legal autofocus is the A_3
bug at a smaller scale. A survey that runs for an hour is normal; sixty seconds
with no image is not.

**Abort is not an edge; it is a trap, and the first rig run walked into it.**
`Acquisition.abort()` (`acquisition_superclass.py`) calls
`event_queue.clear()` before aborting the Java side — and clear() deletes
everything, **including `mark_finished()`'s `None` terminator**, which under
this runner is always still queued behind the generator, because the `with`
block exits (and so `mark_finished()` runs) at submission time. abort()'s own
comment assumes the event thread will "check the status of the acquisition" —
but the thread only checks status after `get()` *returns*, and the clear just
guaranteed `get()` never returns: whenever the generator exits, by **any**
path, `EventQueue.get()` swallows the `StopIteration` and blocks forever on a
queue nothing will ever feed. `await_completion()` then joins that thread with
no timeout, so `__exit__` never returns — and on Windows that join is not
interruptible by Ctrl+C, so the terminal is dead too. Not theory: A_9's abort
part did exactly this on the rig (2026-07-15), and closing MM did not unstick
it, because the wait is on a Python queue, not a socket. The list runner
survives the same clear() only by timing — its terminator is normally consumed
microseconds after being queued, long before any abort; the generator runner
turns that race into a certainty. Spike A_10 replays the deadlock and its fix:
**the generator owns the terminator** — `finally: event_queue.put(None)`, run
on every exit path — which is why the generator must be built holding the
acquisition's real event queue (the factory change in Fix 2). An extra `None`
on the normal path is read by nobody and harms nothing.

With the deadlock fixed, the remaining abort question is promptness and the
log word: images stop arriving, and a generator with no other signal only
finds out via the watchdog — `max_idle_s` late, writing "stalled" when the
truth is "aborted." The right signal exists, and it is not ours to invent:
`_run_acq_event_source` itself checks `acquisition._acq.is_finished()` before
**every** event it sends (`java_backend_acquisitions.py` — a divergence from
the spike's replica, which omits it). The generator can poll the same
observable and exit promptly with the right log word; the spike's
`make_event_stream` takes it as an optional `acq_finished` callable, and A_9's
abort part measures it against the real engine — both the prompt exit after an
`acq.abort()` and the per-poll price, because each `is_finished()` is a bridge
round trip and pyjavaz serializes every round trip under one lock, so the
event-thread poll contends with the hook's own `ctrl` calls on the processor
thread. Measured (rig, localhost, 2026-07-15): the mean round trip is below
the print's 0.1 ms resolution over 10 calls — at the generator's 0.05 s poll
interval that is a duty cycle around 0.1%, negligible — and the abort itself
was observed via `is_finished()` with `__exit__` back **0.3 s** after
`abort()`, against the 20 s the watchdog alone would have taken. Still not
worth blocking Fix 1 on.

Any hook that enqueues must also **guard the events it derives** — they bypass
`_run_protocol_at`, so nothing else will: `guard.check_xy` / `check_z` on every
event before it goes on `candidates`, and a cap (`max_events`) enforced in the hook
and logged when hit.

## Tests

A regression here is silent, which makes these the tests that matter most.

* `event_queue.put()` from a hook reaches the engine under **neither** runner —
  the list runner's source has shut down (A_1); the generator runner's queue has
  the terminator queued ahead of it (A_5). A put to the **candidates** queue
  under the generator runner does reach the engine (A_2). Unit tests against a
  fake event source.
* **An event added on the LAST tile of a survey that runs far longer than
  `max_idle_s` still reaches the engine.** (Spike A_3.) The one that matters most:
  the first draft of the generator failed it *and looked right*.
* The watchdog does **not** fire merely because a survey is long — only when no
  image has come back for `max_idle_s`.
* The generator terminates when the survey completes with **zero** hits — the
  deadlock case; `__exit__` must return.
* The generator terminates when the camera stalls mid-survey, and says so in the
  log rather than hanging. (Spike A_4 covers both termination paths.)
* The generator puts `None` on the acquisition's event queue on **every** exit
  path — completion, stall, abort. `Acquisition.abort()` deletes
  `mark_finished()`'s terminator via `event_queue.clear()`, and a generator that
  trusts that terminator leaves pycro-manager's event thread blocked forever in
  `get()` and `__exit__` joined to it forever, uninterruptibly. (Spike A_10 —
  found when A_9's abort part hung a rig terminal past Ctrl+C.)
* The hook `put()`s a candidate **before** calling `progress.image_done()`. Assert
  on the *ordering*, not just the outcome — the outcome is timing-dependent and
  would pass by luck. (Spike A_8 removes the luck for its measured demonstration
  by giving the detector an analysis cost longer than the generator's poll; the
  unit test should assert the ordering directly.)
* A follow-up frame (a position label not in the survey set) is **not** analyzed
  — otherwise a detection re-detects itself, and (spike A_7) timing alone decides
  between a self-sustaining acquisition and a silent drop of every
  second-generation detection.
* An out-of-bounds derived position is refused by the guard and **not** enqueued;
  the acquisition continues.
* `hook_docs.py` documents `event_queue.put()` — events *and* the early-stop
  `put(None)` — as **never** available, not conditionally available (Fix 1).
  Assert on the doc text — it is the agent's spec, and it is what was wrong.

## What this does not do

* **The engine-side answers are in — measured on a live MM (Windows rig,
  MM 2.0.3 / demo config, 2026-07-15) — except one.** Stages A_1–A_8 and A_10
  prove the *queue protocol* at the Python level, using pycro-manager's real
  `EventQueue` and a replica of its consumer loop; a first draft of this
  section said the rest "needs the rig," which overstated it — the demo config
  was enough. A_9's follow-up part passed everything on its first run: 4/4
  survey frames; `roi_0`, a position label AcqEngJ had never seen, accepted
  mid-acquisition and present in the NDTiff dataset; the `event_queue.put()`
  "poison" label absent; `__exit__` back in 0.7 s; no watchdog. **The design's
  central open question is answered yes.** The same run's abort part then hung
  the terminal past Ctrl+C — the run's most valuable measurement: it exposed
  the upstream abort()/terminator deadlock now written up in Fix 2a and
  replayed by A_10, and the terminator-ownership fix is in the generator. The
  rerun (same rig, later that day) closed the loop: **all stages passed
  end-to-end**, abort included — abort observed via `is_finished()`, `__exit__`
  back 0.3 s after `abort()`, per-poll bridge cost under 0.1 ms. Nothing in
  this design still awaits a measurement; what remains is implementation
  (Fix 1 now, Fix 2 when design/26 wants it), and the routine caveat that the
  runner's first run against real hardware is still a first run.
* **It does not decide what should enqueue events.** That is design/26's problem.
  This doc only establishes that the enqueue *works*, that the current runner makes
  it a no-op, and that the fix has a watchdog bug waiting for whoever writes it.
