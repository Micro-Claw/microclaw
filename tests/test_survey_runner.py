"""design/24 — the generator-backed survey runner and the event_queue no-op.

`event_queue.put()` from a hook is silently discarded under EVERY microclaw
runner: the list runner's event source has already read the terminator by the
time any image is processed (spike A_1), and under the generator runner the
terminator is queued ahead of the hook's put (A_5). The supported path for a
hook that ADDS work is the injected candidates queue, drained by the generator
that `tools._acquire_survey_with_detector` hands to `_acquire_with_hooks`.

A regression here is silent — the run completes, the log shows hits, and the
added events were never imaged — which makes these the tests that matter most.
They drive pycro-manager's REAL EventQueue and a replica of its real consumer
thread (java_backend_acquisitions._run_acq_event_source), exactly as
design/24-event-queue-spike.py does.
"""
from __future__ import annotations

import os
import queue
import threading
import time

import pytest
from pycromanager.acquisition.acquisition_superclass import EventQueue

from microclaw.hooks import HookBase
from microclaw.safety import SafetyViolation
from microclaw.tools import (
    SurveyProgress,
    _acquire_survey_with_detector,
    _survey_event_stream,
)


# ── The fake event source ────────────────────────────────────────────────────

def _event_source_replica(event_queue, sent: list, left_get_loop: threading.Event):
    """_run_acq_event_source's control flow, minus the ZMQ socket.

    Faithful to the two lines that matter: get() blocks, and a None terminator
    ends the thread. Once None is popped the loop has left get() for good and
    nothing will ever read the queue again — the orphaning A_1/A_5 measure.
    """
    while True:
        events = event_queue.get(block=True)
        if events is None:
            left_get_loop.set()
            break
        sent.extend(events if isinstance(events, list) else [events])


class _FakeJavaAcq:
    def __init__(self):
        self._finished = threading.Event()

    def is_finished(self):
        return self._finished.is_set()


class _FakeAcq:
    """What the events-factory receives: the live acquisition's real event
    queue plus the Java-side is_finished() observable."""

    def __init__(self):
        self._event_queue = EventQueue()
        self._acq = _FakeJavaAcq()


def _survey(n: int) -> list[dict]:
    return [{"axes": {"position": f"tile_{i}"}} for i in range(n)]


def _followup(label: str) -> dict:
    """What a detector hook enqueues: a z-stack at a hit."""
    return {"axes": {"position": label, "z": 0}, "z": 50.0}


class _Rig:
    """The shipped generator between a replica event source and a fake camera.

    Images come back one dwell apart — long after the survey events were
    dispatched — which is the whole timing structure design/24 is about.
    """

    def __init__(self, n_tiles: int, max_idle_s: float):
        self.n_tiles = n_tiles
        self.acq = _FakeAcq()
        self.sent: list = []
        self.left_get_loop = threading.Event()
        self.candidates: queue.Queue = queue.Queue()
        self.progress = SurveyProgress(n_tiles)
        self.hook = HookBase()
        self.factory = _survey_event_stream(
            _survey(n_tiles), self.candidates, self.progress, max_idle_s, self.hook
        )

    def detector(self, hits: set, analysis_s: float = 0.0, mark_first: bool = False):
        """A detector hook's image_process_fn reduced to its enqueue protocol.

        mark_first=True is the natural WRONG ordering (acknowledge the frame,
        then analyze); the correct ordering puts the candidate before
        image_done(), so survey_complete() cannot go true mid-detection.
        """

        def per_image(label: str):
            if mark_first:
                self.progress.image_done()
                time.sleep(analysis_s)
                if label in hits:
                    self.candidates.put(_followup(label))
            else:
                time.sleep(analysis_s)
                if label in hits:
                    self.candidates.put(_followup(label))
                self.progress.image_done()

        return per_image

    def run(self, per_image, dwell_s: float = 0.02, camera_stalls_at: int | None = None,
            abort_after_s: float | None = None) -> "_Rig":
        gen = self.factory(self.acq)
        t_src = threading.Thread(
            target=_event_source_replica,
            args=(self.acq._event_queue, self.sent, self.left_get_loop),
            daemon=True,
        )

        def camera():
            for i in range(self.n_tiles):
                if camera_stalls_at is not None and i == camera_stalls_at:
                    return
                time.sleep(dwell_s)
                per_image(f"tile_{i}")

        t_cam = threading.Thread(target=camera, daemon=True)
        t_src.start()
        self.acq._event_queue.put(gen)    # acq.acquire(generator)
        self.acq._event_queue.put(None)   # __exit__ -> mark_finished(), queued behind it
        t_cam.start()

        if abort_after_s is not None:
            def abort():
                # Acquisition.abort()'s order: clear() the queue — deleting
                # mark_finished()'s terminator — THEN abort the Java side.
                self.acq._event_queue.clear()
                self.acq._acq._finished.set()
            threading.Timer(abort_after_s, abort).start()

        t_src.join(timeout=15.0)
        assert not t_src.is_alive(), \
            "event source never exited — Acquisition.__exit__ would hang forever"
        return self

    @property
    def followups(self) -> list:
        return [e for e in self.sent if e["axes"].get("z") is not None]

    @property
    def stalled(self) -> bool:
        return any(e.get("event") == "stalled" for e in self.hook._log)

    @property
    def aborted(self) -> bool:
        return any(e.get("event") == "aborted" for e in self.hook._log)


# ── event_queue.put() reaches the engine under NEITHER runner ────────────────

class TestEventQueuePutIsANoOp:
    def test_the_list_runner_drops_a_hooks_put(self):
        """A_1: acquire(events) -> __exit__ -> mark_finished(). The source
        thread drains [events, None] at memory speed; the first image — and so
        the first image_process_fn call — lands after the terminator."""
        q, sent, left = EventQueue(), [], threading.Event()
        t = threading.Thread(target=_event_source_replica, args=(q, sent, left),
                             daemon=True)
        t.start()
        q.put(_survey(3))                 # acq.acquire(events)
        q.put(None)                       # __exit__ -> mark_finished()
        assert left.wait(timeout=5.0), "source never consumed the terminator"

        q.put(_followup("tile_1"))        # the hook, as hook_docs used to say
        t.join(timeout=5.0)
        assert [e["axes"]["position"] for e in sent] == ["tile_0", "tile_1", "tile_2"]
        assert not any(e["axes"].get("z") is not None for e in sent), \
            "event_queue.put() from a hook must be shown a no-op under the list runner"

    def test_the_generator_runner_drops_a_hooks_put_too(self):
        """A_5: the hook still receives the real event_queue under the
        generator runner, but its put lands BEHIND mark_finished()'s
        terminator and is orphaned when the generator exhausts. The fix is a
        different QUEUE, not a different runner."""
        rig = _Rig(n_tiles=3, max_idle_s=2.0)

        def per_image(label):
            if label == "tile_1":
                rig.acq._event_queue.put(_followup(label))   # hook_docs' old promise
            rig.progress.image_done()

        rig.run(per_image, dwell_s=0.05)
        assert not rig.followups, \
            "event_queue.put() must be a no-op under the generator runner too"

    def test_a_candidate_reaches_the_engine_in_scan(self):
        """A_2: the same follow-up, put on the injected candidates queue, is
        delivered to the engine while the scan is still running."""
        rig = _Rig(n_tiles=3, max_idle_s=2.0)
        rig.run(rig.detector(hits={"tile_1"}), dwell_s=0.05)
        labels = [e["axes"]["position"] for e in rig.sent]
        assert labels == ["tile_0", "tile_1", "tile_2", "tile_1"], \
            "the survey plus the follow-up the hook asked for"
        assert rig.followups and not rig.stalled


# ── the idle watchdog (Fix 2a) ───────────────────────────────────────────────

class TestIdleWatchdog:
    def test_a_late_hit_on_a_survey_longer_than_max_idle_s_is_delivered(self):
        """A_3, the one that matters most: the survey is DISPATCHED at socket
        speed and EXECUTED at camera speed, so max_idle_s must cap idleness,
        not the scan. This survey runs ~0.6 s against a 0.25 s watchdog; the
        hit is on the LAST tile. A total-duration cap drops it silently."""
        rig = _Rig(n_tiles=12, max_idle_s=0.25)
        rig.run(rig.detector(hits={"tile_11"}), dwell_s=0.05)
        assert rig.followups, "a late detection was silently dropped — the A_3 bug"
        assert not rig.stalled, \
            "the watchdog fired on a survey that was merely long, not stalled"

    def test_the_generator_terminates_on_a_survey_with_zero_hits(self):
        """A_4: no candidate ever arrives; a bare candidates.get() would block
        forever and __exit__ would never return."""
        rig = _Rig(n_tiles=4, max_idle_s=1.0)
        rig.run(rig.detector(hits=set()), dwell_s=0.02)
        assert rig.left_get_loop.is_set()
        assert not rig.followups and not rig.stalled

    def test_the_generator_terminates_and_says_stalled_when_the_camera_stops(self):
        """A_4: an image that never comes back is the anomaly the watchdog
        exists for — and it must say so in the log, not hang."""
        rig = _Rig(n_tiles=8, max_idle_s=0.3)
        t0 = time.monotonic()
        rig.run(rig.detector(hits=set()), dwell_s=0.02, camera_stalls_at=4)
        assert rig.stalled, "a stall must be loud in the log, not silent"
        assert time.monotonic() - t0 < 10.0


# ── abort: the terminator and the log word (Fix 2a / A_10, A_9) ──────────────

class TestAbort:
    def test_abort_does_not_deadlock_and_logs_the_right_word(self):
        """Acquisition.abort() clears the event queue, mark_finished()'s None
        included — under this runner that terminator is ALWAYS still queued
        behind the generator. A generator that trusted it would leave the
        event source blocked forever in get() and __exit__ joined to it
        forever (the A_9 rig hang). The generator owns the terminator, and it
        observes the abort via the same is_finished() observable the real
        event source polls — promptly, and logging "aborted", not "stalled"."""
        rig = _Rig(n_tiles=3, max_idle_s=10.0)
        t0 = time.monotonic()
        # camera_stalls_at=0: no image ever returns, so the only exits are the
        # watchdog (10 s — must not be the one taken) or the abort poll.
        rig.run(rig.detector(hits=set()), camera_stalls_at=0, abort_after_s=0.3)
        elapsed = time.monotonic() - t0
        assert rig.left_get_loop.is_set(), \
            "the generator must put its own None: abort() wiped mark_finished()'s"
        assert elapsed < 5.0, f"abort observed {elapsed:.1f}s in — watchdog-late, not prompt"
        assert rig.aborted and not rig.stalled, \
            'an abort must be logged as "aborted", not misreported as a stall'

    # The abort case above covers the third exit path; completion and stall:
    def test_the_terminator_is_put_on_completion(self):
        rig = _Rig(n_tiles=2, max_idle_s=1.0)
        rig.run(rig.detector(hits=set()), dwell_s=0.01)
        assert rig.left_get_loop.is_set()

    def test_the_terminator_is_put_on_a_stall(self):
        rig = _Rig(n_tiles=4, max_idle_s=0.2)
        rig.run(rig.detector(hits=set()), dwell_s=0.01, camera_stalls_at=1)
        assert rig.left_get_loop.is_set() and rig.stalled


# ── the hook-side protocol the generator depends on (A_7, A_8) ───────────────

class TestEnqueueOrdering:
    """put() before image_done() is load-bearing, not cosmetic. The detector is
    given a real analysis cost (0.3 s, far longer than the generator's poll),
    so the wrong ordering loses a last-tile hit deterministically, not by
    timing luck (spike A_8)."""

    def test_detect_then_mark_keeps_a_last_tile_hit(self):
        rig = _Rig(n_tiles=3, max_idle_s=2.0)
        rig.run(rig.detector(hits={"tile_2"}, analysis_s=0.3), dwell_s=0.02)
        assert rig.followups and not rig.stalled

    def test_mark_then_detect_loses_it_silently(self):
        rig = _Rig(n_tiles=3, max_idle_s=2.0)
        rig.run(rig.detector(hits={"tile_2"}, analysis_s=0.3, mark_first=True),
                dwell_s=0.02)
        assert not rig.followups and not rig.stalled, \
            ("expected the reversed ordering to lose the hit via a 'clean' exit — "
             "if this fails, the generator's stopping state changed")


def _run_closed_loop(guard_labels: bool, cap_events: int) -> tuple[list, "_Rig"]:
    """A_7's driver: every event reaching the engine comes back as a FRAME
    through the hook — the way the real engine behaves — so the detector sees
    the frames its own follow-ups produce. Detection is content-based: the
    follow-up images the same scene that triggered it."""
    scene = {"tile_0": "empty", "tile_1": "cell", "tile_2": "empty"}
    survey_labels = set(scene)
    survey = [{"axes": {"position": lbl}, "content": scene[lbl]} for lbl in scene]

    acq = _FakeAcq()
    sent: list = []
    left = threading.Event()
    candidates: queue.Queue = queue.Queue()
    progress = SurveyProgress(len(scene))
    hook = HookBase()
    n_derived = [0]

    def per_frame(event):
        label = event["axes"]["position"]
        analyze = not guard_labels or label in survey_labels   # THE guard
        if analyze and event.get("content") == "cell" and len(sent) < cap_events:
            candidates.put({"axes": {"position": f"roi_{n_derived[0]}"},
                            "z": 50.0, "content": event["content"]})
            n_derived[0] += 1
        progress.image_done()

    factory = _survey_event_stream(survey, candidates, progress, 2.0, hook)
    t_src = threading.Thread(target=_event_source_replica,
                             args=(acq._event_queue, sent, left), daemon=True)

    def camera():
        idx = 0
        while True:
            if idx < len(sent):
                event = sent[idx]
                idx += 1
                time.sleep(0.005)
                per_frame(event)
            elif not t_src.is_alive():
                return
            else:
                time.sleep(0.002)

    t_cam = threading.Thread(target=camera, daemon=True)
    t_src.start()
    acq._event_queue.put(factory(acq))
    acq._event_queue.put(None)
    t_cam.start()
    t_src.join(timeout=15.0)
    assert not t_src.is_alive()
    t_cam.join(timeout=5.0)
    followups = [e["axes"]["position"] for e in sent
                 if e["axes"]["position"].startswith("roi_")]
    return followups, progress


class TestReDetectionGuard:
    """A_7: a follow-up frame goes through image_process_fn too. Unguarded, one
    real hit re-detects itself into a self-sustaining acquisition (or, timing
    depending, a silent drop of every second-generation event). The hook must
    refuse to analyze frames whose label is not in the survey set."""

    def test_an_unguarded_detector_images_itself_forever(self):
        followups, _ = _run_closed_loop(guard_labels=False, cap_events=25)
        assert len(followups) >= 3, \
            "one hit should have snowballed until the external cap pulled the plug"

    def test_the_survey_label_guard_yields_exactly_the_hit(self):
        followups, progress = _run_closed_loop(guard_labels=True, cap_events=25)
        assert followups == ["roi_0"]
        # image_done() on the derived frame overshoots n_done past n_survey —
        # AFTER completion (FIFO), so it changes nothing. Harmless by measure.
        assert progress.n_done == 4 and progress.survey_complete()


# ── the runner: _acquire_survey_with_detector / _acquire_with_hooks ──────────

class _FakeAcquisition:
    """Stands in for pycromanager.Acquisition inside _acquire_with_hooks."""

    last: "_FakeAcquisition | None" = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._event_queue = EventQueue()
        self._acq = _FakeJavaAcq()
        self.acquired = None
        _FakeAcquisition.last = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def acquire(self, events):
        self.acquired = events


class TestAcquireWithHooksFactory:
    """Fix 2: `events` may be a factory called with the LIVE acquisition, so
    the generator it returns holds the real event queue (terminator ownership)
    and the real is_finished() observable (prompt aborts)."""

    def test_a_callable_events_is_called_with_the_live_acquisition(
        self, unconstrained_guard, tmp_path, monkeypatch
    ):
        from microclaw import tools
        monkeypatch.setattr(tools, "Acquisition", _FakeAcquisition)

        seen = {}
        marker = iter(())

        def factory(acq):
            seen["acq"] = acq
            return marker

        tools._acquire_with_hooks(unconstrained_guard, str(tmp_path), "n", factory)
        assert seen["acq"] is _FakeAcquisition.last
        assert _FakeAcquisition.last.acquired is marker

    def test_a_plain_event_list_is_acquired_unchanged(
        self, unconstrained_guard, tmp_path, monkeypatch
    ):
        from microclaw import tools
        monkeypatch.setattr(tools, "Acquisition", _FakeAcquisition)

        events = _survey(3)
        tools._acquire_with_hooks(unconstrained_guard, str(tmp_path), "n", events)
        assert _FakeAcquisition.last.acquired is events


class TestAcquireSurveyWithDetector:
    def _positions(self, n=3):
        return [{"name": f"tile_{i}", "x_um": 10.0 * i, "y_um": 0.0} for i in range(n)]

    def test_an_out_of_bounds_survey_position_is_refused_before_any_acquisition(
        self, mock_ctrl, default_guard, monkeypatch
    ):
        from microclaw import tools
        monkeypatch.setattr(tools, "_acquire_with_hooks",
                            lambda *a, **k: pytest.fail("acquisition should not start"))
        positions = self._positions() + [{"name": "bad", "x_um": 5000.0, "y_um": 0.0}]
        with pytest.raises(SafetyViolation):
            _acquire_survey_with_detector(
                mock_ctrl, default_guard, positions, "/ws", "survey",
                hook=HookBase(), progress=SurveyProgress(4), candidates=queue.Queue(),
                num_time_points=1, time_interval_s=0,
            )

    def test_the_runner_hands_acquire_with_hooks_a_factory_not_a_list(
        self, mock_ctrl, unconstrained_guard, tmp_path, monkeypatch
    ):
        from microclaw import tools

        captured = {}
        monkeypatch.setattr(
            tools, "_acquire_with_hooks",
            lambda guard, save_dir, name, events, hook=None: (
                captured.update(events=events, hook=hook), "/ws/ds")[1],
        )
        log_path = os.path.join(str(tmp_path), "log.json")
        hook = HookBase(log_path=log_path)
        result = _acquire_survey_with_detector(
            mock_ctrl, unconstrained_guard, self._positions(), str(tmp_path), "survey",
            hook=hook, progress=SurveyProgress(3), candidates=queue.Queue(),
            num_time_points=1, time_interval_s=0,
        )
        assert callable(captured["events"]), \
            "the survey runner must pass the factory, not a pre-built event list"
        assert captured["hook"] is hook
        # The factory, given a live acquisition, yields the survey then blocks
        # on candidates — check just the first survey event comes through.
        gen = captured["events"](_FakeAcq())
        first = next(gen)
        assert first["axes"]["position"] == "tile_0"
        gen.close()
        assert result["positions"] == 3
        assert "3 position(s)" in result["status"]
        assert result["log_path"] == log_path


# ── Fix 1: the docs must not promise what silently does nothing ──────────────

class TestHookDocsStopPromisingEventQueuePut:
    """hook_docs.py is the agent's spec for hooks; it is what was wrong. The
    correction must be total: put() is NEVER available — not conditional on
    the runner — and the early-stop put(None) promise is gone too."""

    def test_the_old_promises_are_gone(self):
        from microclaw.hook_docs import HOOK_REFERENCE
        assert "Push new events" not in HOOK_REFERENCE
        assert "End the acquisition early" not in HOOK_REFERENCE
        assert "Signal that the acquisition should end early" not in HOOK_REFERENCE

    def test_put_is_documented_as_never_available(self):
        from microclaw.hook_docs import HOOK_REFERENCE
        assert "NEVER call event_queue.put()" in HOOK_REFERENCE
        assert "SILENT NO-OP" in HOOK_REFERENCE
        # ...and not conditionally available:
        assert "under every microclaw runner" in HOOK_REFERENCE

    def test_the_early_stop_put_none_is_documented_as_dead_too(self):
        from microclaw.hook_docs import HOOK_REFERENCE
        assert "event_queue.put(None) does NOT end the acquisition early" in HOOK_REFERENCE

    def test_the_docs_point_at_the_supported_path(self):
        from microclaw.hook_docs import HOOK_REFERENCE
        assert "_acquire_survey_with_detector" in HOOK_REFERENCE
        assert "candidates" in HOOK_REFERENCE
        # The load-bearing hook-side protocol is spelled out where the agent
        # will read it: label guard, guarded derived events, put-before-done.
        assert "guard.check_xy" in HOOK_REFERENCE
        assert "BEFORE progress.image_done()" in HOOK_REFERENCE
