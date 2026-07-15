"""design/24 — spike: can a hook enqueue an acquisition from image_process_fn?

hook_docs.py tells every hook Claude generates that it can:

    event_queue.put({"axes": {"time": t}, "exposure": 50})

Under tools._acquire_with_hooks it cannot. A_1 replays the exact event-queue
lifecycle that runner creates, using pycro-manager's real EventQueue class and a
replica of its real consumer thread (_run_acq_event_source), and shows the put()
is a silent no-op. A_2 replays the same lifecycle with the fix — and it drives
the ACTUAL generator the design proposes to ship, not a sketch of it, because the
first draft of that generator had a bug the sketch could not have caught (A_3).
A_4 checks it always terminates. A_5 closes the loophole the first draft of the
FIX 1 doc text would have left open: event_queue.put() is a no-op under the
generator runner too — the fix is a DIFFERENT queue, not a different runner.
A_6 measures the alternative pycro-manager itself documents (hold the
Acquisition open, submit follow-ups via acquire()): it works, and the cost is
Fix 2a's entire stopping-state machinery reimplemented on the caller's thread.
A_7 closes the loop the fixed-length cameras above hide: a follow-up frame goes
through image_process_fn too, so an unguarded detector re-detects its own
follow-ups — self-sustaining or silently truncated depending only on timing.
A_8 makes the put()-before-image_done() ordering measurable instead of
timing-lucky: a detector that marks progress before it finishes analyzing
loses a last-tile hit, deterministically. A_9 is the gated real-engine stage:
it runs only if Micro-Manager is reachable on localhost:4827 (the demo config
is enough — no rig needed) and answers what no replica can: does AcqEngJ
accept a brand-new position label mid-acquisition, does NDTiff write it into
the same dataset, is event_queue.put() dropped on the REAL queue, and can the
generator observe an abort via acq._acq.is_finished() — the same observable
the real event source polls — instead of burning max_idle_s.

A_10 replays what A_9's FIRST run on a Windows rig found the hard way — a
deadlock that hung the terminal past Ctrl+C: Acquisition.abort() clears the
event queue, deleting mark_finished()'s already-queued None terminator, so
when the generator exits, pycro-manager's event thread blocks forever in a
get() nothing will ever feed, and __exit__ joins that thread forever. The fix
(the generator owns the terminator: event_queue.put(None) in a finally) is in
make_event_stream and exercised against the real engine by A_9's abort part.

A_1–A_8 and A_10 need only pycro-manager importable: no hardware, no network,
no ~/.microclaw. A_9 talks to a local Micro-Manager if one is running and
writes its datasets to a temp dir it deletes; otherwise it reports SKIPPED.

Exits non-zero if any stage that ran fails its verdict (a skipped A_9 is not a
failure), so the Windows/demo-config machine can use it as a regression check.

Run:  python design/24-event-queue-spike.py
"""
from __future__ import annotations

import queue
import sys
import threading
import time

SEP = "=" * 78


# ---------------------------------------------------------------------------
# The event_queue lifecycle
# ---------------------------------------------------------------------------
#
# tools._acquire_with_hooks is:
#
#     with Acquisition(..., image_process_fn=hook.image_process_fn) as acq:
#         acq.acquire(events)
#
# acquire() is non-blocking: it puts the event list on the queue and returns.
# The `with` block then exits *immediately*, and Acquisition.__exit__ calls
# mark_finished(), which puts None on the same queue. So by the time the FIRST
# image comes back from the camera, the queue already holds a terminator behind
# the events.
#
# pycro-manager's event-source thread (java_backend_acquisitions.py,
# _run_acq_event_source) is a `while True: get()` loop that **breaks on None**.
# Below is that loop, reduced to its control flow, consuming the real
# EventQueue. The question A_1 asks is what happens to an event a hook puts on
# the queue after that break.


def _event_source_replica(event_queue, sent: list, left_get_loop: threading.Event) -> None:
    """_run_acq_event_source's control flow, minus the ZMQ socket.

    Faithful to the two lines that matter:
        events = event_queue.get(block=True)   # blocks
        if events is None: break               # terminator ends the thread

    One nuance the real loop has and this does not: on None it first sends
    {"special": "acquisition-end"} and then sits in
        while not acq.is_finished(): acq.block_until_events_finished(0.01)
    so the thread is not *gone* the instant it sees the terminator — it lingers
    until the engine drains what it was already given. That changes nothing
    about the verdict below: once None is popped, the loop has left get() for
    good and NOTHING will ever read the queue again.
    """
    while True:
        events = event_queue.get(block=True)
        if events is None:
            left_get_loop.set()      # never calls get() again; queue is now orphaned
            break
        sent.extend(events if isinstance(events, list) else [events])


def _survey_events(n: int) -> list[dict]:
    return [{"axes": {"position": f"tile_{i}"}} for i in range(n)]


def _followup_event(label: str) -> dict:
    """What a detector hook wants to enqueue: a z-stack at a hit."""
    return {"axes": {"position": label, "z": 0}, "z": 50.0}


# --- the machinery the design proposes -------------------------------------


class SurveyProgress:
    """How many survey images have come back. Read by the event thread, written
    by the processor thread — so it is a real cross-thread object, not a counter.

    This does not exist in microclaw today; the generator runner needs it, and it
    is the one genuinely new primitive the fix introduces.
    """

    def __init__(self, n_survey: int) -> None:
        self._n_survey = n_survey
        self._lock = threading.Lock()
        self._done = 0

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


def make_event_stream(survey_events, candidates, progress, max_idle_s, on_timeout,
                      acq_finished=None, on_acq_finished=None, event_queue=None):
    """The generator that holds pycro-manager's event source open. THE FIX.

    It must satisfy three things at once, and the third is the subtle one:

      1. Yield the survey, then stay alive feeding follow-up events as the
         detector finds them — that is what keeps _run_acq_event_source parked
         in next(gen) instead of reading mark_finished()'s None.
      2. Terminate when the survey is done and nothing is pending, or
         Acquisition.__exit__ never returns.
      3. Its watchdog must measure IDLENESS, not elapsed time. See A_3: a
         total-duration cap silently re-creates the very bug this fix exists to
         remove, because the survey events are DISPATCHED in milliseconds and
         then take minutes to actually execute.

    `acq_finished` is the answer to the abort edge the design doc names: on an
    abort, images just stop arriving, and without it the generator's only exit
    is the stall watchdog — max_idle_s late and logging the wrong word. The
    real event source polls exactly this observable before every send
    (`acquisition._acq.is_finished()`, java_backend_acquisitions.py), so the
    generator may poll it too. A_9 measures both the prompt exit and what one
    poll costs over a bridge that serializes every round trip.

    `event_queue` — pass the acquisition's REAL event queue, and the generator
    puts the None terminator on it itself, on every exit path. This is not
    defensive: Acquisition.abort() calls event_queue.clear(), which deletes
    mark_finished()'s already-queued None, and under a generator runner that
    leaves the event source thread blocked forever in a get() nothing will
    ever feed again — and __exit__ joins that thread forever, uninterruptibly.
    A_10 replays the deadlock (found by A_9's first run on a Windows rig,
    which hung past Ctrl+C); the finally below is the fix.
    """

    def event_stream():
        try:
            yield from survey_events

            # The survey is now submitted, not finished: the event source pushed
            # all N events at socket speed and the camera has barely started.
            # Everything below is spent waiting for images to come back.
            last_activity = time.monotonic()
            last_count = progress.n_done

            while True:
                try:
                    event = candidates.get(timeout=0.05)
                except queue.Empty:
                    pass
                else:
                    last_activity = time.monotonic()
                    yield event
                    continue

                # Nothing pending. Are we done? Note BOTH conditions:
                # survey_complete alone would race the hook, which puts a
                # candidate *before* it marks the image done (see
                # _hook_image_process_fn) — so an empty queue plus a complete
                # survey is the only safe stopping state.
                if progress.survey_complete() and candidates.empty():
                    return

                # Aborted / finished from outside? Exit NOW, with the right
                # word, instead of stalling for max_idle_s and logging
                # "stalled" when the truth is "aborted".
                if acq_finished is not None and acq_finished():
                    if on_acq_finished is not None:
                        on_acq_finished()
                    return

                # Progress since we last looked means the rig is alive; reset
                # the watchdog. It fires only on a genuine STALL — an image
                # that never comes back — never merely because the survey is
                # long.
                n = progress.n_done
                if n != last_count:
                    last_count, last_activity = n, time.monotonic()

                if time.monotonic() - last_activity > max_idle_s:
                    on_timeout(max_idle_s)      # loud in the log, not silent
                    return
        finally:
            # The generator OWNS the terminator. mark_finished()'s None cannot
            # be trusted to still exist by the time this generator exhausts:
            # abort() clears the queue, terminator included (A_10). An extra
            # None on the normal path is consumed by nobody and harms nothing.
            if event_queue is not None:
                event_queue.put(None)

    return event_stream


def make_event_stream_total_cap(survey_events, candidates, progress, max_wait_s, on_timeout):
    """The FIRST draft of the generator, kept only so A_3 can fail it.

    Identical to make_event_stream except the deadline is absolute: it starts
    ticking when the survey is *submitted*. Looks right, reads right, and drops
    every detection made after max_wait_s of a survey that simply takes longer
    than that to run.
    """

    def event_stream():
        yield from survey_events
        deadline = time.monotonic() + max_wait_s
        while time.monotonic() < deadline:
            try:
                event = candidates.get(timeout=0.05)
            except queue.Empty:
                if progress.survey_complete() and candidates.empty():
                    return
            else:
                yield event
        on_timeout(max_wait_s)

    return event_stream


def _hook_image_process_fn(label, hits, candidates, progress):
    """The detector hook's image_process_fn, reduced to its enqueue ordering.

    The put() MUST happen before image_done(). If a hit on the LAST survey tile
    marked progress first, the generator could observe survey_complete() over an
    empty candidate queue and return before the candidate ever landed — losing
    exactly the detection the whole feature exists to catch.
    """
    if label in hits:
        candidates.put(_followup_event(label))
    progress.image_done()


def _detect_then_mark(analysis_s):
    """The CORRECT hook ordering, with the detector's real cost made visible:
    analyze (which takes time), put the candidate, and only THEN mark the image
    done. survey_complete() cannot go true while a detection is in flight."""

    def fn(label, hits, candidates, progress):
        time.sleep(analysis_s)              # the detector actually working
        if label in hits:
            candidates.put(_followup_event(label))
        progress.image_done()

    return fn


def _mark_then_detect(analysis_s):
    """The natural WRONG ordering: acknowledge the frame first ("I received
    it"), analyze after. Looks harmless; A_8 shows it losing a last-tile hit."""

    def fn(label, hits, candidates, progress):
        progress.image_done()
        time.sleep(analysis_s)
        if label in hits:
            candidates.put(_followup_event(label))

    return fn


def _run_generator_acquisition(EventQueue, factory, n_tiles, hits, dwell_s,
                               cap_s, camera_stalls_at=None,
                               process_fn=_hook_image_process_fn):
    """Drive the replica event source with a simulated camera + detector hook.

    Returns (labels_reaching_engine, followups_delivered, timeouts_fired).
    """
    q, sent, left_get_loop = EventQueue(), [], threading.Event()
    candidates: queue.Queue = queue.Queue()
    progress = SurveyProgress(n_tiles)
    timeouts: list = []

    stream = factory(_survey_events(n_tiles), candidates, progress, cap_s, timeouts.append)

    def camera():
        """Images come back one dwell apart, long after the events were sent."""
        for i in range(n_tiles):
            if camera_stalls_at is not None and i == camera_stalls_at:
                return                       # an image that never arrives
            time.sleep(dwell_s)
            process_fn(f"tile_{i}", hits, candidates, progress)

    t_src = threading.Thread(target=_event_source_replica, args=(q, sent, left_get_loop))
    t_cam = threading.Thread(target=camera, daemon=True)
    t_src.start()

    q.put(stream())                   # acq.acquire(generator)
    q.put(None)                       # __exit__ -> mark_finished(); waits behind the gen
    t_cam.start()

    t_src.join(timeout=30.0)
    followups = [e for e in sent if e["axes"].get("z") is not None]
    return [e["axes"]["position"] for e in sent], followups, timeouts


def a1_current_runner(EventQueue) -> bool:
    """Replay _acquire_with_hooks as written. Does the hook's event get through?"""
    print("\nA_1  current runner:  acquire(events) -> __exit__ -> mark_finished()")
    q, sent, left_get_loop = EventQueue(), [], threading.Event()
    t = threading.Thread(target=_event_source_replica, args=(q, sent, left_get_loop))
    t.start()

    q.put(_survey_events(3))          # acq.acquire(events)
    q.put(None)                       # __exit__ -> mark_finished()
    left_get_loop.wait(timeout=2.0)   # the source thread reads both and leaves get()

    # The camera is slower than the queue. The first image — and so the first
    # image_process_fn call — lands here, after the terminator.
    q.put(_followup_event("tile_1"))  # hook: "tile_1 is a hit, image it properly"
    t.join(timeout=2.0)

    print(f"     events reaching the engine : {[e['axes']['position'] for e in sent]}")
    print(f"     source left the get() loop : {left_get_loop.is_set()}")
    delivered = any(e["axes"].get("z") is not None for e in sent)
    print(f"     hook's follow-up delivered : {delivered}")
    print("     VERDICT: event_queue.put() from image_process_fn is a NO-OP here."
          if not delivered else "     VERDICT: delivered (unexpected — recheck).")
    return delivered


def a2_generator_runner(EventQueue) -> bool:
    """The fix: acquire() a generator, so the source thread stays alive.

    EventQueue.get() expands a generator in place and only reads the next queue
    item — the None — once the generator raises StopIteration. So a generator
    that yields the survey and then *blocks* on the detector's candidate queue
    keeps the event source alive for the whole scan, and the terminator waits
    its turn behind it.

    This drives make_event_stream — the generator the design actually proposes.
    """
    print("\nA_2  generator runner:  acquire(gen) — gen holds the source thread open")
    labels, followups, timeouts = _run_generator_acquisition(
        EventQueue, make_event_stream, n_tiles=3, hits={"tile_1"},
        dwell_s=0.05, cap_s=2.0,
    )
    print(f"     events reaching the engine : {labels}")
    delivered = bool(followups)
    print(f"     hook's follow-up delivered : {delivered}")
    print(f"     watchdog fired             : {bool(timeouts)}  (should be False)")
    print("     VERDICT: the follow-up z-stack reaches the engine, in-scan."
          if delivered and not timeouts else "     VERDICT: not delivered (recheck).")
    return delivered and not timeouts


def a3_watchdog_must_measure_idleness(EventQueue) -> bool:
    """The regression A_2 alone cannot catch, and the reason it exists.

    A survey is DISPATCHED at socket speed and EXECUTED at camera speed: 12
    tiles go onto the wire in microseconds and then take 12 dwells to come back.
    A cap that starts when the survey is submitted is therefore a cap on the
    whole scan, not a stall detector — and a real 400-tile survey at a 300–500 ms
    dwell blows a 120 s default long before it ends.

    When it blows, the generator returns, StopIteration lets mark_finished()'s
    None through, acquisition-end goes out, and every later detection is dropped
    on the floor. That is the silent no-op of A_1 wearing a different mask,
    reintroduced by A_1's own fix. Same survey, same late hit, two watchdogs:
    """
    print("\nA_3  a long survey with a LATE detection (hit on the last tile)")
    # 12 tiles x 0.1 s dwell = ~1.2 s of scanning. Cap: 0.4 s — i.e. the survey
    # is simply longer than the cap, which is the normal case, not an error.
    kw = dict(n_tiles=12, hits={"tile_11"}, dwell_s=0.1, cap_s=0.4)

    labels, followups, timeouts = _run_generator_acquisition(
        EventQueue, make_event_stream_total_cap, **kw)
    bad = not followups
    print(f"     total-duration cap  -> follow-up delivered: {bool(followups)}"
          f"   timed out: {bool(timeouts)}")

    labels, followups, timeouts = _run_generator_acquisition(
        EventQueue, make_event_stream, **kw)
    good = bool(followups) and not timeouts
    print(f"     idle watchdog       -> follow-up delivered: {bool(followups)}"
          f"   timed out: {bool(timeouts)}")

    print("     VERDICT: the cap must measure IDLENESS. A wall-clock cap drops"
          "\n              late detections silently — the A_1 bug all over again."
          if bad and good else
          "     VERDICT: inconclusive — recheck (expected: cap drops it, watchdog keeps it).")
    return bad and good


def a4_termination(EventQueue) -> bool:
    """The generator must always end, or Acquisition.__exit__ never returns.

    Two ways it can hang, both tested: a survey that finds NOTHING (no candidate
    ever arrives, so a bare candidates.get() would block forever), and a camera
    that stops returning images halfway (survey_complete() never becomes true).
    """
    print("\nA_4  termination — __exit__ must not hang")
    t0 = time.monotonic()
    labels, followups, timeouts = _run_generator_acquisition(
        EventQueue, make_event_stream, n_tiles=4, hits=set(), dwell_s=0.02, cap_s=1.0)
    clean = len(labels) == 4 and not followups and not timeouts
    print(f"     zero hits          : ended in {time.monotonic()-t0:.2f}s, "
          f"{len(labels)} survey events, watchdog fired: {bool(timeouts)}  (should be False)")

    t0 = time.monotonic()
    labels, followups, timeouts = _run_generator_acquisition(
        EventQueue, make_event_stream, n_tiles=8, hits=set(), dwell_s=0.02,
        cap_s=0.3, camera_stalls_at=4)
    stalled = bool(timeouts)
    print(f"     camera stalls at 4 : ended in {time.monotonic()-t0:.2f}s, "
          f"watchdog fired: {stalled}  (should be True)")
    print("     VERDICT: terminates on an empty survey AND on a stall, and says which."
          if clean and stalled else "     VERDICT: did not terminate as expected — recheck.")
    return clean and stalled


def a5_put_is_a_noop_under_the_generator_runner_too(EventQueue) -> bool:
    """Fix 1's doc text must not say "works under a generator event source".

    Under the generator runner the hook still RECEIVES the real event_queue —
    pycro-manager passes it to any 3-arg image_process_fn
    (acquisition_superclass.py:354, _call_image_process_fn) — but a put() to it
    lands BEHIND mark_finished()'s terminator: the deque is [generator, None]
    from the moment __exit__ runs, so the hook's event is orphaned the moment
    the generator exhausts and the None is popped. The only queue that reaches
    the engine is OURS (candidates, A_2). Never the runner; always the queue.
    """
    print("\nA_5  event_queue.put() under the GENERATOR runner — still a no-op")
    q, sent, left_get_loop = EventQueue(), [], threading.Event()
    candidates: queue.Queue = queue.Queue()
    progress = SurveyProgress(3)
    stream = make_event_stream(_survey_events(3), candidates, progress, 2.0,
                               lambda s: None)

    def camera():
        for i in range(3):
            time.sleep(0.05)
            if i == 1:
                q.put(_followup_event("tile_1"))   # what hook_docs.py:93 says to do
            progress.image_done()

    t_src = threading.Thread(target=_event_source_replica, args=(q, sent, left_get_loop))
    t_cam = threading.Thread(target=camera, daemon=True)
    t_src.start()

    q.put(stream())                   # acq.acquire(generator)
    q.put(None)                       # __exit__ -> mark_finished()
    t_cam.start()
    t_src.join(timeout=10.0)

    delivered = any(e["axes"].get("z") is not None for e in sent)
    print(f"     events reaching the engine : {[e['axes']['position'] for e in sent]}")
    print(f"     hook's put() delivered     : {delivered}")
    print("     VERDICT: put() to pycro-manager's queue is a no-op under BOTH runners;"
          "\n              the supported path is the injected candidates queue (A_2)."
          if not delivered else "     VERDICT: delivered (unexpected — recheck).")
    return not delivered


def a6_the_adaptive_pattern_alternative(EventQueue) -> bool:
    """The alternative the doc must answer: pycro-manager's OWN adaptive pattern.

    https://pycro-manager.readthedocs.io/en/latest/adaptive_acq.html says: keep
    the Acquisition open and submit follow-ups with acq.submit(event). Our
    pinned 1.0.2 has no submit() — that page tracks a newer API — but
    acquire() is the same operation there: callable repeatedly, returns an
    AcquisitionFuture, and is internally just a put() on this same event queue
    (acquisition_superclass.py:279). So the pattern reduces to: the follow-up
    put lands AHEAD of mark_finished()'s terminator, because the caller has not
    exited the `with` block yet.

    That works, and this case measures it. The cost is in what "has not exited
    the with block yet" requires: the caller must decide when __exit__ may
    finally run, and that decision is exactly Fix 2a — the survey_complete()-
    and-nothing-in-flight stopping state, the idleness watchdog, the
    put-before-image_done ordering — reimplemented inline on the caller's
    thread (see the wait loop below, which this case cannot work without).
    The machinery is the completion problem itself, not a generator artifact;
    the two designs differ only in WHERE it lives and in what the hook must
    hold (the whole Acquisition object vs. an injected candidates queue).
    """
    print("\nA_6  the pycro-manager 'adaptive acq' pattern — hold the Acquisition open")
    q, sent, left_get_loop = EventQueue(), [], threading.Event()
    progress = SurveyProgress(3)

    def hook_image_process_fn(label):
        # The hook holds `acq` and calls acq.acquire(event) — i.e. a put() on
        # this queue, ahead of the terminator. Ordering still matters: the put
        # must precede image_done(), or a hit on the LAST tile races the
        # caller's survey_complete() check exactly as in A_2's generator.
        if label == "tile_1":
            q.put(_followup_event("tile_1"))
        progress.image_done()

    def camera():
        for i in range(3):
            time.sleep(0.05)
            hook_image_process_fn(f"tile_{i}")

    t_src = threading.Thread(target=_event_source_replica, args=(q, sent, left_get_loop))
    t_cam = threading.Thread(target=camera, daemon=True)
    t_src.start()

    q.put(_survey_events(3))          # acq.acquire(survey) — with block held OPEN
    t_cam.start()

    # ...and here is the cost: the caller now owns the stopping problem. This
    # loop is Fix 2a's stopping state + idle watchdog, relocated. Delete it and
    # either __exit__ runs immediately (A_1 all over again) or never runs.
    last_activity, last_count = time.monotonic(), progress.n_done
    while not progress.survey_complete():
        time.sleep(0.02)
        n = progress.n_done
        if n != last_count:
            last_count, last_activity = n, time.monotonic()
        if time.monotonic() - last_activity > 2.0:
            break                     # the idle watchdog, abbreviated
    q.put(None)                       # only NOW may __exit__ -> mark_finished()

    t_src.join(timeout=10.0)
    delivered = any(e["axes"].get("z") is not None for e in sent)
    print(f"     events reaching the engine : {[e['axes']['position'] for e in sent]}")
    print(f"     hook's follow-up delivered : {delivered}")
    print("     VERDICT: works — because the caller reimplements Fix 2a's stopping"
          "\n              state inline. Same machinery, different thread, and the hook"
          "\n              must hold the whole Acquisition (abort(), mark_finished(), ...)."
          if delivered else "     VERDICT: not delivered (unexpected — recheck).")
    return delivered


def _run_closed_loop(EventQueue, guard_labels: bool, cap_events: int,
                     dwell_s: float = 0.005):
    """A_7's driver: every event that reaches the engine comes back as a FRAME
    through the hook. The fixed-length cameras above cannot represent this —
    they image the survey and stop — but it is what the real engine does: a
    follow-up event is executed, its image goes through image_process_fn like
    any other, and the detector sees its own follow-up.

    Detection is CONTENT-based, as real detection is: the follow-up images the
    same scene that triggered it, so an unguarded detector re-fires on it.
    `cap_events` plays the operator pulling the plug, so the unguarded case
    still terminates and can be measured.

    Returns (followup_labels_reaching_engine, n_done, n_survey).
    """
    q, sent, left_get_loop = EventQueue(), [], threading.Event()
    candidates: queue.Queue = queue.Queue()
    scene = {"tile_0": "empty", "tile_1": "cell", "tile_2": "empty"}
    survey_labels = set(scene)
    progress = SurveyProgress(len(scene))
    survey = [{"axes": {"position": lbl}, "content": scene[lbl]} for lbl in scene]
    n_derived = [0]

    def hook_fn(event):
        label = event["axes"]["position"]
        analyze = not guard_labels or label in survey_labels   # THE guard
        if analyze and event.get("content") == "cell" and len(sent) < cap_events:
            # The follow-up frames the same scene, so its content is the same —
            # which is exactly why re-analyzing it re-detects it.
            candidates.put({"axes": {"position": f"roi_{n_derived[0]}"},
                            "z": 50.0, "content": event["content"]})
            n_derived[0] += 1
        progress.image_done()       # counts derived frames too — see the note

    def camera():
        idx = 0
        while True:
            if idx < len(sent):
                event = sent[idx]
                idx += 1
                time.sleep(dwell_s)
                hook_fn(event)
            elif not t_src.is_alive():
                return
            else:
                time.sleep(0.002)

    t_src = threading.Thread(target=_event_source_replica, args=(q, sent, left_get_loop))
    t_cam = threading.Thread(target=camera, daemon=True)
    stream = make_event_stream(survey, candidates, progress, 2.0, lambda s: None)
    t_src.start()
    q.put(stream())                   # acq.acquire(generator)
    q.put(None)                       # __exit__ -> mark_finished()
    t_cam.start()
    t_src.join(timeout=30.0)
    t_cam.join(timeout=5.0)

    followups = [e["axes"]["position"] for e in sent
                 if e["axes"]["position"].startswith("roi_")]
    return followups, progress.n_done, len(scene)


def a7_re_detection_feedback(EventQueue) -> bool:
    """Follow-up frames go through image_process_fn too. What happens then?

    Unguarded, the outcome is TIMING-dependent, which is worse than a clean
    failure: if a frame's round trip beats the generator's candidate poll, each
    detection images itself into another detection and the acquisition is
    self-sustaining until something external stops it; if it doesn't, the
    generator sees survey_complete() over an empty queue, exits, and the second
    generation is orphaned behind the terminator — a silent drop. Either way
    the hook must refuse to analyze frames whose label is not in the survey
    set. This drives the fast branch (frames return well inside the 0.05 s
    poll), so the runaway is what gets measured.

    Two measured side-notes fall out of the guarded run:
      * image_done() on a derived frame is HARMLESS: events execute FIFO, so
        the survey is fully dispatched before the first candidate is yielded,
        and every follow-up frame returns after every survey frame. n_done
        overshoots n_survey after the fact; nothing observes the overshoot.
      * The generator may exit before the follow-up frame is even acquired —
        the event already reached the engine, so delivery does not depend on
        the generator outliving it.
    """
    print("\nA_7  the closed loop: a follow-up frame is re-processed by the hook")
    followups, n_done, n_survey = _run_closed_loop(EventQueue, guard_labels=False,
                                                   cap_events=25)
    runaway = len(followups) >= 3
    print(f"     unguarded detector  -> follow-ups spawned: {len(followups)} from ONE"
          f" real hit (stopped only by the external cap)")

    followups, n_done, n_survey = _run_closed_loop(EventQueue, guard_labels=True,
                                                   cap_events=25)
    guarded = followups == ["roi_0"]
    print(f"     survey-label guard  -> follow-ups spawned: {len(followups)}  (exactly the hit)")
    print(f"     n_done={n_done} vs n_survey={n_survey}: the derived frame was counted"
          f" AFTER the survey\n     completed (FIFO), so counting it changes nothing.")
    print("     VERDICT: without the guard, one detection becomes a self-sustaining"
          "\n              acquisition (or, timing depending, silent gen-2 drops)."
          if runaway and guarded else
          "     VERDICT: inconclusive — recheck (expected: unguarded runs away, guarded = 1).")
    return runaway and guarded


def a8_enqueue_ordering(EventQueue) -> bool:
    """put() before image_done() — measured, not asserted.

    The design doc's test list says to assert on the ORDERING because the
    outcome is timing-dependent and would pass by luck. Here the luck is
    removed: the detector is given a real analysis cost (0.3 s, far longer than
    the generator's 0.05 s poll), so a hook that marks the last survey image
    done BEFORE it finishes analyzing hands the generator a complete survey
    over an empty queue while the detection is still running. The generator
    exits; the candidate lands behind the terminator; the hit on the last tile
    — the one the feature exists to catch — is silently lost. Same scenario,
    correct ordering: delivered.
    """
    print("\nA_8  enqueue ordering: image_done() must come AFTER detect + put()")
    kw = dict(n_tiles=3, hits={"tile_2"}, dwell_s=0.02, cap_s=2.0)

    labels, followups, timeouts = _run_generator_acquisition(
        EventQueue, make_event_stream, process_fn=_mark_then_detect(0.3), **kw)
    bad = not followups and not timeouts
    print(f"     mark, then detect   -> follow-up delivered: {bool(followups)}"
          f"   (exited via 'clean' completion — silent loss)")

    labels, followups, timeouts = _run_generator_acquisition(
        EventQueue, make_event_stream, process_fn=_detect_then_mark(0.3), **kw)
    good = bool(followups) and not timeouts
    print(f"     detect, then mark   -> follow-up delivered: {bool(followups)}")
    print("     VERDICT: the ordering is load-bearing — reversed, a last-tile hit is"
          "\n              dropped with no timeout and no error, deterministically."
          if bad and good else
          "     VERDICT: inconclusive — recheck (expected: reversed drops, correct keeps).")
    return bad and good


def a9_real_engine() -> bool | None:
    """The gated stage: the fix against the REAL engine. Demo config, no rig.

    Everything above drives a replica of _run_acq_event_source. The real loop
    differs in two ways the replica cannot capture — it sends over a ZMQ
    socket, and it checks `acquisition._acq.is_finished()` before EVERY send —
    and beyond it sit the questions the design doc defers: does AcqEngJ accept
    a position label it has never seen, mid-acquisition, and does NDTiff write
    that frame into the same dataset? None of that needs the rig: Micro-Manager
    with MMConfig_demo (DemoCamera + demo stages) runs the real engine.

    So, iff MM is reachable on localhost:4827, this stage measures:
      1. the generator holds the REAL event source open; __exit__ returns;
      2. a follow-up event with a NEW position label ("roi_0") lands in the
         same NDTiff dataset — the design doc's "one open question";
      3. event_queue.put() from a real 3-arg image_process_fn is dropped
         (A_5's replica verdict, confirmed on the real queue: "poison" must
         not appear in the dataset);
      4. on abort, the generator polling acq._acq.is_finished() — the same
         observable the real event source polls — exits promptly and logs the
         right word, instead of burning max_idle_s; and what one poll costs,
         given pyjavaz serializes every bridge round trip.
    """
    print("\nA_9  the real engine (Micro-Manager, demo config is enough) — gated")
    import socket
    try:
        socket.create_connection(("127.0.0.1", 4827), timeout=1.0).close()
    except OSError:
        print("     SKIPPED — no Micro-Manager on localhost:4827."
              "\n     Run this spike on a machine with MM open (demo config suffices)"
              "\n     to answer the engine-side questions the replica cannot.")
        return None

    import shutil
    import tempfile
    import traceback

    from pycromanager import Acquisition

    save_dir = tempfile.mkdtemp(prefix="microclaw_design24_a9_")
    datasets = []
    ok = True
    try:
        # -- parts 1-3: survey + in-scan follow-up with a NEW position label --
        n_tiles = 4
        survey_labels = {f"tile_{i}" for i in range(n_tiles)}
        survey = [{"axes": {"position": f"tile_{i}"}, "x": 50.0 * i, "y": 0.0}
                  for i in range(n_tiles)]
        candidates: queue.Queue = queue.Queue()
        progress = SurveyProgress(n_tiles)
        timeouts: list = []

        def image_process_fn(image, metadata, event_queue):
            label = metadata.get("Axes", {}).get("position")
            if label not in survey_labels:
                return image, metadata          # never re-process a follow-up (A_7)
            if label == "tile_1":
                candidates.put({"axes": {"position": "roi_0"}, "x": 75.0, "y": 25.0})
                # what hook_docs.py documents — must NOT appear in the dataset
                event_queue.put({"axes": {"position": "poison"}, "x": 0.0, "y": 0.0})
            progress.image_done()               # after detect + put (A_8)
            return image, metadata

        t0 = time.monotonic()
        with Acquisition(directory=save_dir, name="a9_followup", show_display=False,
                         image_process_fn=image_process_fn) as acq:
            # Built inside the `with` so the generator can hold the REAL event
            # queue and own its terminator (A_10) — mandatory, not defensive.
            stream = make_event_stream(survey, candidates, progress, 15.0,
                                       timeouts.append,
                                       event_queue=acq._event_queue)
            acq.acquire(stream())
        exit_s = time.monotonic() - t0
        dataset = acq.get_dataset()
        datasets.append(dataset)
        seen = {c.get("position") for c in dataset.get_image_coordinates_list()}

        missing = survey_labels - seen
        got_roi = "roi_0" in seen
        got_poison = "poison" in seen
        print(f"     survey frames in dataset    : {n_tiles - len(missing)}/{n_tiles}")
        print(f"     new label 'roi_0' present   : {got_roi}   (enqueued mid-acquisition)")
        print(f"     event_queue 'poison' absent : {not got_poison}   (A_5 on the real queue)")
        print(f"     __exit__ returned after     : {exit_s:.1f}s   watchdog fired: {bool(timeouts)}")
        ok = not missing and got_roi and not got_poison and not timeouts

        # -- part 4: abort observability -------------------------------------
        # This part hung forever on its first run (Windows rig, 2026-07-15) and
        # took the terminal with it: abort() clears the event queue, deleting
        # mark_finished()'s already-queued terminator, so when the generator
        # exited, pycro-manager's event thread blocked forever in get() and
        # __exit__ joined it forever. A_10 replays that deadlock; the
        # event_queue= argument below (terminator ownership) is the fix. The
        # progress prints exist so any future hang names its blocking line.
        survey2 = [{"axes": {"position": f"t{i}"}, "x": 10.0 * i, "y": 0.0,
                    "exposure": 100} for i in range(40)]
        candidates2: queue.Queue = queue.Queue()
        progress2 = SurveyProgress(len(survey2))   # no hook: never advances
        stalled, finished, abort_at = [], [], []

        print("     [abort test] starting 40-tile acquisition; abort at t+1.5s ...")
        with Acquisition(directory=save_dir, name="a9_abort",
                         show_display=False) as acq2:
            round_trips = []
            for _ in range(10):
                t = time.monotonic()
                acq2._acq.is_finished()
                round_trips.append(time.monotonic() - t)
            stream2 = make_event_stream(
                survey2, candidates2, progress2, max_idle_s=20.0,
                on_timeout=stalled.append,
                acq_finished=lambda: acq2._acq.is_finished(),
                on_acq_finished=lambda: finished.append(True),
                event_queue=acq2._event_queue)
            acq2.acquire(stream2())
            threading.Timer(1.5, lambda: (abort_at.append(time.monotonic()),
                                          acq2.abort())).start()
            print("     [abort test] waiting for __exit__ (hung here before the fix) ...")
        abort_latency = time.monotonic() - abort_at[0] if abort_at else float("nan")

        via = "is_finished()" if finished else ("stall watchdog" if stalled else "unknown")
        # µs, not ms: on localhost a bridge round trip is tens of µs, and the
        # first rig run's ms-resolution print could not tell that from zero.
        print(f"     is_finished() round trip    : "
              f"{1e6 * sum(round_trips) / len(round_trips):.0f} us mean of "
              f"{len(round_trips)}  (the per-poll bridge cost)")
        print(f"     abort observed via          : {via}")
        print(f"     __exit__ after abort in     : {abort_latency:.1f}s"
              f"   (the watchdog alone would have taken 20s)")
        ok = ok and bool(finished) and abort_latency < 10.0

        print("     VERDICT: the engine accepts mid-acquisition labels, NDTiff stores"
              "\n              them, put() is dropped for real, and aborts are observable."
              if ok else "     VERDICT: engine-side behavior differs — see above.")
    except Exception:
        traceback.print_exc()
        print("     VERDICT: errored against the real engine — see traceback.")
        ok = False
    finally:
        for d in datasets:
            try:
                d.close()
            except Exception:
                pass
        shutil.rmtree(save_dir, ignore_errors=True)
    return ok


def a10_abort_wipes_the_terminator(EventQueue) -> bool:
    """Why A_9's first run on the rig hung forever, past Ctrl+C. Measured.

    `Acquisition.abort()` (acquisition_superclass.py) does
    `event_queue.clear()` — and clear() deletes EVERYTHING, including
    mark_finished()'s None terminator, which under a generator runner is
    always still queued behind the generator, because `with` exits (and so
    mark_finished() runs) at submission time. abort()'s own comment says the
    event thread "should know shut itself down by checking the status of the
    acquisition" — but the thread only checks status after get() RETURNS, and
    abort just guaranteed get() never returns: when the generator exits (by
    is_finished poll, by stall watchdog, by any path at all), EventQueue.get()
    swallows the StopIteration and moves on to a BLOCKING Queue.get() on a
    queue nothing will ever feed again. The event thread blocks forever;
    await_completion()'s finally joins it with no timeout; the main thread
    blocks forever; and on Windows a join is not interruptible by Ctrl+C.
    Closing MM does not help — the wait is on a Python queue, not a socket.

    Under the LIST runner the same clear() is nearly harmless only by timing:
    the terminator is normally consumed microseconds after it is queued, long
    before any abort. The generator runner turns that race into a certainty.

    The fix: the generator owns the terminator — a finally that put()s None to
    the real event queue on every exit path (make_event_stream's event_queue
    argument). This stage replays the deadlock with the replica source thread
    and the real EventQueue.clear(), then replays it with the fix.
    """
    print("\nA_10 abort() wipes the terminator — the A_9 rig hang, replayed and fixed")

    def run(own_terminator: bool) -> bool:
        q, sent, left_get_loop = EventQueue(), [], threading.Event()
        candidates: queue.Queue = queue.Queue()
        progress = SurveyProgress(1)      # one tile that will never come back
        aborted = threading.Event()
        stream = make_event_stream(
            _survey_events(1), candidates, progress, 5.0, lambda s: None,
            acq_finished=aborted.is_set,
            event_queue=q if own_terminator else None)
        # daemon: in the broken case this thread is stuck forever by design,
        # and it must not keep the spike process alive at exit.
        t_src = threading.Thread(target=_event_source_replica,
                                 args=(q, sent, left_get_loop), daemon=True)
        t_src.start()
        q.put(stream())               # acq.acquire(generator)
        q.put(None)                   # __exit__ -> mark_finished(); queued behind gen
        time.sleep(0.2)               # survey dispatched; generator now waiting

        q.clear()                     # Acquisition.abort(): event_queue.clear() ...
        aborted.set()                 # ... then _acq.abort(); is_finished() -> True

        t_src.join(timeout=2.0)      # stands in for await_completion's untimed join
        return not t_src.is_alive()   # True = event thread exited; join returns

    hung = not run(own_terminator=False)
    print(f"     trust mark_finished()'s None -> event thread exits: {not hung}"
          f"   ({'stuck in get() forever — __exit__ never returns' if hung else 'unexpected'})")
    fixed = run(own_terminator=True)
    print(f"     generator owns the terminator -> event thread exits: {fixed}")
    print("     VERDICT: abort() + generator + already-run mark_finished() deadlocks"
          "\n              deterministically; the generator must put its own None."
          if hung and fixed else
          "     VERDICT: inconclusive — recheck (expected: hangs without, exits with).")
    return hung and fixed


def main() -> int:
    print(SEP)
    print("Can a hook enqueue an acquisition from image_process_fn?")
    print(SEP)
    try:
        from pycromanager.acquisition.acquisition_superclass import EventQueue
    except ImportError:
        print("  pycro-manager not importable — nothing to run.")
        return 1
    a1 = a1_current_runner(EventQueue)
    a2 = a2_generator_runner(EventQueue)
    a3 = a3_watchdog_must_measure_idleness(EventQueue)
    a4 = a4_termination(EventQueue)
    a5 = a5_put_is_a_noop_under_the_generator_runner_too(EventQueue)
    a6 = a6_the_adaptive_pattern_alternative(EventQueue)
    a7 = a7_re_detection_feedback(EventQueue)
    a8 = a8_enqueue_ordering(EventQueue)
    a10 = a10_abort_wipes_the_terminator(EventQueue)
    a9 = a9_real_engine()
    print(f"\n  SUMMARY  event_queue.put(): list runner "
          f"{'works' if a1 else 'DROPPED'}, generator runner "
          f"{'DROPPED' if a5 else 'works'};  candidates queue: "
          f"{'works' if a2 else 'DROPPED'}")
    print(f"           idle watchdog needed: {'CONFIRMED' if a3 else 'not shown'};  "
          f"always terminates: {'yes' if a4 else 'NO — would hang __exit__'}")
    print(f"           held-open acquire() (upstream adaptive pattern): "
          f"{'works, same machinery relocated' if a6 else 'DROPPED (unexpected)'}")
    print(f"           re-detection guard needed: {'CONFIRMED' if a7 else 'not shown'};  "
          f"enqueue ordering load-bearing: {'CONFIRMED' if a8 else 'not shown'}")
    print(f"           abort() wipes the terminator, generator must own it: "
          f"{'CONFIRMED' if a10 else 'not shown'}")
    if a9 is None:
        print("           real engine (A_9): SKIPPED — no MM on localhost:4827; the"
              "\n           engine-side answers still need a run on the demo-config machine.")
    else:
        print(f"           real engine (A_9): "
              f"{'CONFIRMED end-to-end' if a9 else 'FAILED — see stage output'}")

    # A_1 and A_5 PASS by demonstrating the drop; the others by delivering.
    passed = {"A_1": not a1, "A_2": a2, "A_3": a3, "A_4": a4, "A_5": a5,
              "A_6": a6, "A_7": a7, "A_8": a8, "A_10": a10}
    if a9 is not None:
        passed["A_9"] = a9
    failed = [name for name, p in passed.items() if not p]
    if failed:
        print(f"\n  FAILED STAGES: {', '.join(failed)}")
    print("\n" + SEP)
    print("Findings are written up in design/24-event-queue-put-is-a-no-op.md.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
