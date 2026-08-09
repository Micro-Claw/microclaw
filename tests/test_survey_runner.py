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


class TestUntrustedAdapterRunnerWiring:
    def _positions(self):
        return [
            {"name": "tile_0", "x_um": 0.0, "y_um": 0.0},
            {"name": "tile_1", "x_um": 1.0, "y_um": 1.0},
            {"name": "tile_2", "x_um": 2.0, "y_um": 2.0},
        ]

    def test_adaptive_runner_keeps_state_parent_side_and_dispatches_actions(
        self, mock_ctrl, unconstrained_guard, tmp_path, monkeypatch
    ):
        from microclaw import tools
        from microclaw.hook_decisions import (
            ContinueSurvey, HookResult, StopSurvey, UntrustedHookAdapter,
        )

        class Decisions:
            def __init__(self):
                self.calls = 0

            def analyze_frame(self, image, metadata):
                self.calls += 1
                action = ContinueSurvey() if self.calls == 1 else StopSurvey()
                return HookResult({"call": self.calls}, [action])

        monkeypatch.setattr(tools, "Acquisition", _FakeAcquisition)
        adapter = UntrustedHookAdapter(Decisions(), str(tmp_path / "hook.json"))
        progress = SurveyProgress(3)
        tools._acquire_survey_with_detector(
            mock_ctrl, unconstrained_guard, self._positions(), str(tmp_path), "survey",
            hook=adapter, progress=progress, candidates=queue.Queue(), adaptive=True,
            num_time_points=1, time_interval_s=0,
        )

        assert not hasattr(adapter, "candidates")
        assert not hasattr(adapter, "survey_events")
        stream = _FakeAcquisition.last.acquired
        first = next(stream)
        assert first["axes"]["position"] == "tile_0"

        process = _FakeAcquisition.last.kwargs["image_process_fn"]
        process(object(), {"PositionName": "tile_0"}, object())
        second = next(stream)
        assert second["axes"]["position"] == "tile_1"
        process(object(), {"PositionName": "tile_1"}, object())
        with pytest.raises(StopIteration):
            next(stream)
        assert progress.n_done == 2
        assert progress.stopped_early

    def test_nonadaptive_runner_refuses_untrusted_hook_before_acquisition(
        self, mock_ctrl, unconstrained_guard, tmp_path, monkeypatch
    ):
        from microclaw import tools
        from microclaw.hook_decisions import UntrustedHookAdapter

        class Legacy:
            def image_process_fn(self, image, metadata, event_queue):
                return image, metadata

        monkeypatch.setattr(tools, "Acquisition", _FakeAcquisition)
        _FakeAcquisition.last = None
        with pytest.raises(ValueError, match="non-adaptive"):
            tools._acquire_survey_with_detector(
                mock_ctrl, unconstrained_guard, self._positions(), str(tmp_path),
                "survey", hook=UntrustedHookAdapter(Legacy()),
                progress=SurveyProgress(3), candidates=queue.Queue(), adaptive=False,
                num_time_points=1, time_interval_s=0,
            )
        assert _FakeAcquisition.last is None

    def test_adaptive_runner_refuses_a_legacy_saved_hook_that_cannot_propose(
        self, mock_ctrl, unconstrained_guard, tmp_path, monkeypatch
    ):
        """A legacy saved hook can never advance the survey past the seed: it
        has no channel for ContinueSurvey now that the runner state is
        parent-side. Left to run it idles out max_idle_s and logs "stalled",
        reporting a structural impossibility as a hardware symptom. Refuse it
        before any tile is exposed."""
        from microclaw import tools
        from microclaw.hook_decisions import UntrustedHookAdapter

        class Legacy:
            def image_process_fn(self, image, metadata, event_queue):
                return image, metadata

        monkeypatch.setattr(tools, "Acquisition", _FakeAcquisition)
        _FakeAcquisition.last = None
        with pytest.raises(ValueError, match="analyze_frame"):
            tools._acquire_survey_with_detector(
                mock_ctrl, unconstrained_guard, self._positions(), str(tmp_path),
                "survey", hook=UntrustedHookAdapter(Legacy()),
                progress=SurveyProgress(3), candidates=queue.Queue(), adaptive=True,
                num_time_points=1, time_interval_s=0,
            )
        assert _FakeAcquisition.last is None


def test_adaptive_budget_exhaustion_stops_cleanly_reports_and_terminates():
    acq = _FakeAcq()
    candidates = queue.Queue()
    candidates.put(_followup("extra"))
    progress = SurveyProgress(1)
    hook = HookBase()
    stream = _survey_event_stream(
        _survey(1), candidates, progress, 1.0, hook,
        adaptive=True, max_events=1,
    )(acq)

    # No SafetyViolation crosses the event-source boundary.
    assert list(stream) == [_survey(1)[0]]
    assert progress.stopped_early
    assert progress.exhausted_budget
    assert hook.get_summary() == [{
        "event": "budget_exhausted", "max_events": 1, "overrun_frames": 0,
    }]
    assert acq._event_queue.get_nowait() is None


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
            lambda guard, save_dir, name, events, hook=None, **kwargs: (
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
        # The path must be named by its TOOL: rig run 20260716_140329 read the
        # private function's name here and had no way to call it.
        assert "run_adaptive_survey" in HOOK_REFERENCE
        assert "_acquire_survey_with_detector" not in HOOK_REFERENCE
        assert "candidates" in HOOK_REFERENCE
        # The load-bearing hook-side protocol is spelled out where the agent
        # will read it: label guard, guarded derived events, put-before-done.
        assert "guard.check_xy" in HOOK_REFERENCE
        assert "BEFORE progress.image_done()" in HOOK_REFERENCE


# ── design/27 Fix 1: return-None is NEVER a skip, and the docs must say so ───

class TestHookDocsReturnNoneIsNotASkip:
    """hook_docs.py is the agent's spec and it is what was wrong (the founding
    rig trace: an agent-written hook, following the docs, promised "no
    bleaching past the trigger" and delivered five ghost exposures). The
    correction must be total — one backend, and on it the promise is dead."""

    def test_the_old_skip_promises_are_gone(self):
        from microclaw.hook_docs import HOOK_REFERENCE
        flat = " ".join(HOOK_REFERENCE.split())   # a line wrap must not hide a promise
        assert "Return None to skip" not in flat
        assert "skip image capture for this event" not in flat
        assert "skip this event entirely" not in flat
        assert "hardware never moves" not in flat
        # The processor's half-truth: the discard is real, the drop is fiction.
        assert "drop all remaining events" not in flat
        assert "skip this position" not in flat
        # The autofocus plugin's fictional enforcement arm:
        assert "the capture is skipped and the acquisition stops" not in flat

    def test_return_none_is_documented_as_a_ghost_exposure(self):
        from microclaw.hook_docs import HOOK_REFERENCE
        assert "NEVER return None" in HOOK_REFERENCE
        assert "ghost exposure" in HOOK_REFERENCE
        assert "STILL FIRES THE CAMERA" in HOOK_REFERENCE

    def test_the_processor_discard_is_documented_as_discard_only(self):
        from microclaw.hook_docs import HOOK_REFERENCE
        flat = " ".join(HOOK_REFERENCE.split())   # line wraps must not hide text
        assert "discards the image and NOTHING else" in flat
        assert "keeps the frame out of the dataset" in flat

    def test_the_honest_levers_are_documented(self):
        from microclaw.hook_docs import HOOK_REFERENCE
        flat = " ".join(HOOK_REFERENCE.split())
        # Raise = abort everything, loudly:
        assert "acquisition.abort(e)" in flat
        # ...and the skip use case's real answer, stop = don't submit,
        # named by the tool that drives it, not the private flag behind it:
        assert "run_adaptive_survey" in flat
        assert "progress.done_early()" in flat
        assert "stopping is simply NOT SUBMITTING" in flat

    def test_the_autofocus_plugin_section_now_promises_the_raise(self):
        from microclaw.hook_docs import HOOK_REFERENCE
        assert "RAISES SafetyViolation" in HOOK_REFERENCE


# ── design/27 Fix 4: SurveyProgress.done_early() ─────────────────────────────

class TestSurveyProgressDoneEarly:
    """An early stop never reaches n_survey by counting — the remaining tiles
    were never submitted — so the hook needs an explicit done signal."""

    def test_done_early_completes_the_survey_below_n_survey(self):
        progress = SurveyProgress(9)
        for _ in range(4):
            progress.image_done()
        assert not progress.survey_complete()
        progress.done_early()
        assert progress.survey_complete()
        assert progress.n_done == 4          # the count is untouched — 4 of 9

    def test_counting_to_n_survey_still_completes_without_the_signal(self):
        progress = SurveyProgress(2)
        progress.image_done()
        progress.image_done()
        assert progress.survey_complete()

    def test_the_stream_drains_candidates_before_exiting_on_done_early(self):
        """The exit condition is survey_complete() AND candidates.empty(): a
        candidate put before the done signal must still be delivered, exactly
        as a last-tile hit must (design/24 A_8, inherited)."""
        acq = _FakeAcq()
        candidates: queue.Queue = queue.Queue()
        progress = SurveyProgress(5)
        factory = _survey_event_stream(
            _survey(5), candidates, progress, 2.0, HookBase(), adaptive=True
        )
        gen = factory(acq)
        assert next(gen)["axes"]["position"] == "tile_0"   # the seed, and ONLY it
        candidates.put(_survey(5)[1])
        progress.done_early()
        progress.image_done()
        assert next(gen)["axes"]["position"] == "tile_1", \
            "a candidate already queued at the stop must not be dropped"
        with pytest.raises(StopIteration):
            next(gen)


# ── design/27 Fix 4: the adaptive stream — stop by NOT submitting ────────────

class _AdaptiveRig:
    """B_8's closed loop against the SHIPPED stream: the camera returns one
    frame per event actually sent, the hook walks the tile list one
    candidates.put() per decision, and stopping is not submitting."""

    def __init__(self, n_tiles: int, max_idle_s: float, stop_after: int | None = None):
        self.tiles = _survey(n_tiles)
        self.stop_after = stop_after
        self.acq = _FakeAcq()
        self.sent: list = []
        self.left_get_loop = threading.Event()
        self.candidates: queue.Queue = queue.Queue()
        self.progress = SurveyProgress(n_tiles)
        self.hook = HookBase()
        self.dispatched_when_processed: list[int] = []
        self.factory = _survey_event_stream(
            self.tiles, self.candidates, self.progress, max_idle_s, self.hook,
            adaptive=True,
        )

    def per_frame(self, event):
        # Recorded BEFORE the decision: how many events had been dispatched
        # when this frame was scored. Under one-at-a-time submission that is
        # exactly the frame's own ordinal — tile N+1 must not exist yet.
        self.dispatched_when_processed.append(len(self.sent))
        n = len(self.dispatched_when_processed)   # frames processed so far
        if self.stop_after is not None and n >= self.stop_after:
            self.progress.done_early()             # the next tile never exists
        elif n < len(self.tiles):
            self.candidates.put(self.tiles[n])     # decide, THEN submit
        self.progress.image_done()

    def run(self, dwell_s: float = 0.01, camera_stalls_at: int | None = None,
            abort_after_s: float | None = None) -> "_AdaptiveRig":
        gen = self.factory(self.acq)
        t_src = threading.Thread(
            target=_event_source_replica,
            args=(self.acq._event_queue, self.sent, self.left_get_loop),
            daemon=True,
        )

        def camera():
            idx = 0
            while True:
                if camera_stalls_at is not None and idx == camera_stalls_at:
                    return
                if idx < len(self.sent):
                    event = self.sent[idx]
                    idx += 1
                    time.sleep(dwell_s)
                    self.per_frame(event)
                elif not t_src.is_alive():
                    return
                else:
                    time.sleep(0.002)

        t_cam = threading.Thread(target=camera, daemon=True)
        t_src.start()
        self.acq._event_queue.put(gen)    # acq.acquire(generator)
        self.acq._event_queue.put(None)   # __exit__ -> mark_finished()
        t_cam.start()

        if abort_after_s is not None:
            def abort():
                self.acq._event_queue.clear()
                self.acq._acq._finished.set()
            threading.Timer(abort_after_s, abort).start()

        t_src.join(timeout=15.0)
        assert not t_src.is_alive(), \
            "event source never exited — Acquisition.__exit__ would hang forever"
        t_cam.join(timeout=5.0)
        return self

    @property
    def sent_labels(self) -> list:
        return [e["axes"]["position"] for e in self.sent]

    @property
    def stalled(self) -> bool:
        return any(e.get("event") == "stalled" for e in self.hook._log)

    @property
    def aborted(self) -> bool:
        return any(e.get("event") == "aborted" for e in self.hook._log)


class TestAdaptiveStream:
    def test_tile_n_plus_1_is_never_dispatched_before_frame_n_is_scored(self):
        """Nothing beyond the seed is pre-dispatched; every dispatch is a
        per-frame decision. dispatched_when_processed[N] == N+1 says frame N
        was scored while tiles 0..N were the ONLY events in existence."""
        rig = _AdaptiveRig(n_tiles=5, max_idle_s=2.0).run()
        assert rig.sent_labels == [f"tile_{i}" for i in range(5)]
        assert rig.dispatched_when_processed == [1, 2, 3, 4, 5], \
            "a tile was dispatched before the previous frame was scored"
        assert rig.left_get_loop.is_set() and not rig.stalled

    def test_stop_by_not_submitting_ends_cleanly_with_exactly_the_submitted_tiles(self):
        """The founding scenario (stop at tile 4 of a 9-tile grid) through the
        shipped stream: no cancel, no None, no abort — the remaining five
        tiles simply never exist, so nothing can expose them."""
        rig = _AdaptiveRig(n_tiles=9, max_idle_s=2.0, stop_after=4).run()
        assert rig.sent_labels == ["tile_0", "tile_1", "tile_2", "tile_3"], \
            "an event past the stop was dispatched — the ghost-exposure surface, reopened"
        assert rig.left_get_loop.is_set(), "the terminator must land on an early stop"
        assert not rig.stalled and not rig.aborted
        assert rig.progress.survey_complete() and rig.progress.n_done == 4

    def test_the_terminator_lands_when_the_camera_stalls_mid_walk(self):
        rig = _AdaptiveRig(n_tiles=6, max_idle_s=0.3).run(camera_stalls_at=2)
        assert rig.left_get_loop.is_set() and rig.stalled

    def test_the_terminator_lands_on_an_abort_mid_walk(self):
        rig = _AdaptiveRig(n_tiles=6, max_idle_s=10.0).run(
            camera_stalls_at=0, abort_after_s=0.3
        )
        assert rig.left_get_loop.is_set()
        assert rig.aborted and not rig.stalled


class TestAcquireSurveyWithDetectorAdaptive:
    def _positions(self, n=3):
        return [{"name": f"tile_{i}", "x_um": 10.0 * i, "y_um": 0.0} for i in range(n)]

    def test_the_adaptive_runner_hands_the_hook_the_tile_list(
        self, mock_ctrl, unconstrained_guard, tmp_path, monkeypatch
    ):
        """The tile list becomes state the hook walks: the runner builds the
        events (channel/exposure shape included) and shares them through
        hook.survey_events; the stream pre-dispatches only the seed."""
        from microclaw import tools

        captured = {}
        monkeypatch.setattr(
            tools, "_acquire_with_hooks",
            lambda guard, save_dir, name, events, hook=None, **kwargs: (
                captured.update(events=events, hook=hook), "/ws/ds")[1],
        )
        hook = HookBase()
        _acquire_survey_with_detector(
            mock_ctrl, unconstrained_guard, self._positions(), str(tmp_path), "survey",
            hook=hook, progress=SurveyProgress(3), candidates=queue.Queue(),
            adaptive=True, num_time_points=1, time_interval_s=0,
        )
        assert hook.survey_events is not None and len(hook.survey_events) == 3
        assert hook.survey_events[0]["axes"]["position"] == "tile_0"

        gen = captured["events"](_FakeAcq())
        assert next(gen)["axes"]["position"] == "tile_0"   # the seed
        gen.close()

    def test_the_batched_runner_does_not_set_survey_events(
        self, mock_ctrl, unconstrained_guard, tmp_path, monkeypatch
    ):
        from microclaw import tools
        monkeypatch.setattr(tools, "_acquire_with_hooks", lambda *a, **k: "/ws/ds")
        hook = HookBase()
        _acquire_survey_with_detector(
            mock_ctrl, unconstrained_guard, self._positions(), str(tmp_path), "survey",
            hook=hook, progress=SurveyProgress(3), candidates=queue.Queue(),
            num_time_points=1, time_interval_s=0,
        )
        assert hook.survey_events is None, \
            "pre-dispatch mode must not imply the hook walks the tile list"


# ── design/27: run_adaptive_survey — the tool that drives the adaptive runner ─

class _ProbeHook(HookBase):
    """Stands in for a hook_strategy-loaded adaptive hook: records nothing,
    exists so the tests can inspect what the runner handed the instance."""

    instances: list = []

    def __init__(self, log_path=None):
        super().__init__(log_path)
        _ProbeHook.instances.append(self)


class TestRunAdaptiveSurvey:
    """Rig run 20260716_140329: a correct adaptive hook had no tool to drive
    it — run_multiposition_acquisition dispatches the batched runner, and
    _acquire_survey_with_detector had no caller outside this test suite. The
    hook raised at frame 1 (the built-in safe failure), one wasted exposure
    and a stranded stage later. run_adaptive_survey is the missing caller."""

    def _positions(self, n=3):
        # Decreasing x: the reverse-scan case the rig run needed — order must
        # be the caller's list order, never re-sorted into raster order.
        return [{"name": f"tile_{i}", "x_um": 50.0 * (n - i), "y_um": 0.0}
                for i in range(n)]

    @pytest.fixture
    def captured(self, monkeypatch):
        from microclaw import tools
        from microclaw.hooks import PRECODED_HOOK_REGISTRY

        _ProbeHook.instances = []
        monkeypatch.setitem(PRECODED_HOOK_REGISTRY, "probe", _ProbeHook)
        calls = []
        monkeypatch.setattr(
            tools, "_acquire_with_hooks",
            lambda guard, save_dir, name, events, hook=None, **kwargs: (
                calls.append({"save_dir": save_dir, "name": name,
                              "events": events, "hook": hook}),
                "/ws/ds",
            )[1],
        )
        return calls

    def test_the_hook_receives_the_full_adaptive_contract(
        self, mock_ctrl, unconstrained_guard, captured, tmp_path
    ):
        from microclaw.tools import run_adaptive_survey

        assert HookBase().candidates is None and HookBase().progress is None, \
            "the attributes must read as 'wrong runner' everywhere else"

        result = run_adaptive_survey(
            mock_ctrl, unconstrained_guard, protocol="timelapse",
            save_dir=str(tmp_path), hook_strategy="probe",
            positions=self._positions(),
            protocol_params={"n_frames": 1, "interval_s": 0},
        )
        assert "error" not in result
        assert len(_ProbeHook.instances) == 1
        hook = _ProbeHook.instances[0]
        # The three objects a saved hook cannot close over, as attributes:
        assert isinstance(hook.candidates, queue.Queue)
        assert isinstance(hook.progress, SurveyProgress)
        labels = [e["axes"]["position"] for e in hook.survey_events]
        assert labels == ["tile_0", "tile_1", "tile_2"], \
            "survey order is the caller's list order (reverse scans depend on it)"
        # The runner got a generator factory, not a pre-built batch:
        assert callable(captured[0]["events"])

    def test_the_result_reports_what_ran_not_what_was_planned(
        self, mock_ctrl, unconstrained_guard, captured, tmp_path
    ):
        """'Hooked acquisition complete across 9 position(s).' is the sentence
        that made 5 ghost exposures read as a clean early stop. The counter
        the hook itself drove is the only honest source."""
        from microclaw.tools import run_adaptive_survey

        result = run_adaptive_survey(
            mock_ctrl, unconstrained_guard, protocol="timelapse",
            save_dir=str(tmp_path), hook_strategy="probe",
            positions=self._positions(),
            protocol_params={"n_frames": 1, "interval_s": 0},
        )
        # The mocked acquisition processed no frames — the report must say so.
        assert result["frames_acquired"] == 0
        assert result["stopped_early"] is False
        assert "0 frame(s) acquired from a 3-tile plan" in result["status"]
        assert "positions" not in result, "the ambiguous count is retired"
        assert [t["position"] for t in result["tiles_planned"]] == \
            ["tile_0", "tile_1", "tile_2"]
        assert result["tiles_planned"][0]["x_um"] == 150.0

    def test_precoded_hook_omits_unobservable_actions_and_names_missing_log(
        self, mock_ctrl, unconstrained_guard, captured, tmp_path
    ):
        """Precoded hooks steer directly; parent-dispatch counts do not exist."""
        from microclaw.tools import run_adaptive_survey

        result = run_adaptive_survey(
            mock_ctrl, unconstrained_guard, protocol="timelapse",
            save_dir=str(tmp_path), hook_strategy="probe",
            positions=self._positions(),
            protocol_params={"n_frames": 1, "interval_s": 0},
        )
        assert "hook_actions" not in result
        assert "log_path" not in result
        assert result["hint"] == (
            "stopped_early describes the hook's control decisions, not what was found. "
            "No per-tile log was written for this run, so there is nothing to read back."
        )

    def test_result_counts_actions_observed_by_the_real_parent_dispatch(
        self, mock_ctrl, unconstrained_guard, monkeypatch, tmp_path
    ):
        import numpy as np
        from microclaw import tools
        from microclaw.hook_decisions import (
            ContinueSurvey, HookResult, UntrustedHookAdapter,
        )

        class ContinueHook:
            def analyze_frame(self, image, metadata):
                return HookResult({"would_keep": True}, (ContinueSurvey(),))

        adapter = UntrustedHookAdapter(ContinueHook(), str(tmp_path / "hook.json"))
        monkeypatch.setattr(tools, "_resolve_hook", lambda *_args, **_kwargs: adapter)

        def acquire(_guard, _save_dir, _name, _events, hook=None, **_kwargs):
            for position in range(3):
                hook.image_process_fn(
                    np.zeros((2, 2), dtype=np.uint16),
                    {"Axes": {"position": position}}, object(),
                )
            return "/ws/ds"

        monkeypatch.setattr(tools, "_acquire_with_hooks", acquire)
        result = tools.run_adaptive_survey(
            mock_ctrl, unconstrained_guard, protocol="timelapse",
            save_dir=str(tmp_path), hook_strategy="saved",
            positions=self._positions(),
            log_path=str(tmp_path / "hook.json"),
            protocol_params={"n_frames": 1, "interval_s": 0},
        )
        assert result["hook_actions"] == {"ContinueSurvey": 3, "StopSurvey": 0}
        assert result["frames_acquired"] == 3
        assert "control decisions, not what was found" in result["hint"]
        assert "read_hook_log" in result["hint"]

    def test_a_saved_hook_that_dispatches_nothing_omits_the_counts(
        self, mock_ctrl, unconstrained_guard, monkeypatch, tmp_path
    ):
        """An observation-only saved hook has no decisions, not zero decisions.

        HookResult.actions defaults to (), so a saved hook may legitimately
        record measurements and propose nothing. Its adapter then holds an empty
        count dict — which is not the same fact as "the hook chose Continue zero
        times", and reporting it as {"ContinueSurvey": 0} would recreate F8 in
        the very field added to retire it.
        """
        import numpy as np
        from microclaw import tools
        from microclaw.hook_decisions import HookResult, UntrustedHookAdapter

        class MeasureOnlyHook:
            def analyze_frame(self, image, metadata):
                return HookResult({"snr": 12.0})

        adapter = UntrustedHookAdapter(MeasureOnlyHook(), str(tmp_path / "hook.json"))
        monkeypatch.setattr(tools, "_resolve_hook", lambda *_args, **_kwargs: adapter)

        def acquire(_guard, _save_dir, _name, _events, hook=None, **_kwargs):
            for position in range(3):
                hook.image_process_fn(
                    np.zeros((2, 2), dtype=np.uint16),
                    {"Axes": {"position": position}}, object(),
                )
            return "/ws/ds"

        monkeypatch.setattr(tools, "_acquire_with_hooks", acquire)
        result = tools.run_adaptive_survey(
            mock_ctrl, unconstrained_guard, protocol="timelapse",
            save_dir=str(tmp_path), hook_strategy="saved",
            positions=self._positions(),
            log_path=str(tmp_path / "hook.json"),
            protocol_params={"n_frames": 1, "interval_s": 0},
        )
        assert "hook_actions" not in result
        assert result["frames_acquired"] == 3

    def test_position_names_resolve_from_the_mm_list(
        self, mock_ctrl, unconstrained_guard, captured, tmp_path
    ):
        from microclaw.controller import PositionProjection
        from microclaw.tools import run_adaptive_survey

        positions = [
            {"name": "a", "x_um": 1.0, "y_um": 2.0},
            {"name": "b", "x_um": 3.0, "y_um": 4.0},
        ]
        mock_ctrl.inspect_current_position_list.return_value = PositionProjection(
            positions, positions, []
        )
        result = run_adaptive_survey(
            mock_ctrl, unconstrained_guard, protocol="timelapse",
            save_dir=str(tmp_path), hook_strategy="probe",
            position_names=["b", "a"],
            protocol_params={"n_frames": 1, "interval_s": 0},
        )
        hook = _ProbeHook.instances[0]
        labels = [e["axes"]["position"] for e in hook.survey_events]
        assert labels == ["b", "a"], "named positions keep the caller's order too"
        assert "error" not in result

        missing = run_adaptive_survey(
            mock_ctrl, unconstrained_guard, protocol="timelapse",
            save_dir=str(tmp_path), hook_strategy="probe",
            position_names=["a", "nope"],
            protocol_params={"n_frames": 1, "interval_s": 0},
        )
        assert "nope" in missing["error"]

    def test_the_error_paths_refuse_before_any_hardware_shape_is_built(
        self, mock_ctrl, unconstrained_guard, captured, tmp_path
    ):
        from microclaw.tools import run_adaptive_survey

        common = dict(save_dir=str(tmp_path), hook_strategy="probe",
                      protocol_params={"n_frames": 1, "interval_s": 0})
        both = run_adaptive_survey(
            mock_ctrl, unconstrained_guard, protocol="timelapse",
            positions=self._positions(), position_names=["a"], **common)
        assert "not both" in both["error"]
        neither = run_adaptive_survey(
            mock_ctrl, unconstrained_guard, protocol="timelapse", **common)
        assert "error" in neither
        snap = run_adaptive_survey(
            mock_ctrl, unconstrained_guard, protocol="snap",
            positions=self._positions(), save_dir=str(tmp_path),
            hook_strategy="probe")
        assert "display-only" in snap["error"]
        unknown = run_adaptive_survey(
            mock_ctrl, unconstrained_guard, protocol="timelapse",
            positions=self._positions(), save_dir=str(tmp_path),
            hook_strategy="no_such_hook",
            protocol_params={"n_frames": 1, "interval_s": 0})
        assert "list_hooks" in unknown["error"]
        assert not captured, "every refusal above must precede the acquisition"
