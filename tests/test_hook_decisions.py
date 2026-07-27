from __future__ import annotations

import json
import queue

import numpy as np
import pytest

from microclaw.hook_decisions import (
    AcquireAt, ContinueSurvey, HookResult, MoveStage, RequestAutofocus,
    SetExposure, StopSurvey, UntrustedHookAdapter,
)
from microclaw.safety import SafetyViolation
from microclaw.tools import SurveyProgress


def _events(n=3):
    return [
        {"axes": {"position": f"p{i}"}, "x": float(i), "y": float(i + 1)}
        for i in range(n)
    ]


class _Guard:
    def __init__(self, unsafe=False):
        self.unsafe = unsafe

    def check_xy(self, x, y):
        if self.unsafe and x == 2:
            raise SafetyViolation("unsafe x")

    def check_z(self, z):
        pass


def _adapter(action, tmp_path, *, n=3, unsafe=False):
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({"score": 1}, (action,))

    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    candidates = queue.Queue()
    progress = SurveyProgress(n)
    adapter.configure_adaptive(events=_events(n), candidates=candidates,
                               progress=progress, guard=_Guard(unsafe), max_events=n)
    return adapter, candidates, progress


@pytest.mark.parametrize("action", [MoveStage(x_um=1), SetExposure(exposure_ms=5),
                                    RequestAutofocus()])
def test_valid_but_unsupported_actions_are_refused_and_attributable(action, tmp_path):
    adapter, candidates, progress = _adapter(action, tmp_path)
    adapter.image_process_fn(np.zeros((2, 2)), {"PositionName": "p0"}, object())
    assert candidates.empty()
    assert progress.n_done == 1
    record = adapter._log[-1]
    assert record["decision"] == "refused"
    assert record["action"]["kind"] == action.kind
    assert "unsupported" in record["reason"]


def test_continue_dispatches_next_planned_tile(tmp_path):
    adapter, candidates, progress = _adapter(ContinueSurvey(), tmp_path)
    adapter.image_process_fn(np.zeros((2, 2)), {"PositionName": "p0"}, object())
    assert candidates.get_nowait()["axes"]["position"] == "p1"
    assert adapter._log[-1]["decision"] == "accepted"


def test_continue_walked_to_end_matches_planned_frame_count(tmp_path):
    adapter, candidates, progress = _adapter(ContinueSurvey(), tmp_path)
    image = np.zeros((2, 2))
    for i in range(3):
        adapter.image_process_fn(image, {"PositionName": f"p{i}"}, object())
    assert progress.n_done == 3
    assert candidates.qsize() == 2  # seed + two accepted continuations = 3 frames
    assert progress.stopped_early is False


def test_acquire_at_dispatches_only_named_planned_tile(tmp_path):
    adapter, candidates, _ = _adapter(AcquireAt("p0"), tmp_path)
    adapter.image_process_fn(np.zeros((2, 2)), {"PositionName": "p1"}, object())
    assert candidates.get_nowait()["axes"]["position"] == "p0"
    assert adapter._log[-1]["decision"] == "accepted"


def test_stop_survey_ends_cleanly_and_is_audited(tmp_path):
    adapter, candidates, progress = _adapter(StopSurvey(), tmp_path)
    returned = adapter.image_process_fn(
        np.zeros((2, 2)), {"PositionName": "p0"}, object()
    )
    assert returned is not None and candidates.empty()
    assert progress.stopped_early and progress.n_done == 1
    assert adapter._log[-1]["decision"] == "accepted"


def test_acquire_at_over_reservation_is_refused(tmp_path):
    adapter, candidates, _ = _adapter(AcquireAt("p0"), tmp_path, n=1)
    adapter.image_process_fn(np.zeros((2, 2)), {"PositionName": "p0"}, object())
    assert candidates.empty()
    assert "reservation" in adapter._log[-1]["reason"]


def test_guard_violation_is_refused_and_logged(tmp_path):
    adapter, candidates, _ = _adapter(AcquireAt("p2"), tmp_path, unsafe=True)
    adapter.image_process_fn(np.zeros((2, 2)), {"PositionName": "p0"}, object())
    assert candidates.empty()
    assert "SafetyGuard" in adapter._log[-1]["reason"]
    assert "unsafe x" in adapter._log[-1]["reason"]


@pytest.mark.parametrize("action", [
    {"kind": "NoSuchAction"},
    {"kind": "AcquireAt"},
    {"kind": "StopSurvey", "surprise": True},
])
def test_unknown_or_malformed_action_fails_before_dispatch(action, tmp_path):
    adapter, candidates, progress = _adapter(action, tmp_path)
    with pytest.raises((TypeError, ValueError)):
        adapter.image_process_fn(np.zeros((2, 2)), {}, object())
    assert candidates.empty() and progress.n_done == 0
    assert adapter._log[-1]["event"] == "hook_failure"


def test_non_json_measurements_fail_closed(tmp_path):
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({"bad": float("nan")})

    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    with pytest.raises(ValueError):
        adapter.image_process_fn(np.zeros((2, 2)), {}, object())
    assert adapter._log[-1]["event"] == "hook_failure"


def test_legacy_event_queue_access_raises_and_parent_logs(tmp_path):
    class Legacy:
        def image_process_fn(self, image, metadata, event_queue):
            event_queue.put({"hardware": "event"})

    path = tmp_path / "hook.json"
    adapter = UntrustedHookAdapter(Legacy(), str(path))
    with pytest.raises(RuntimeError, match="cannot access"):
        adapter.image_process_fn(np.zeros((2, 2)), {}, object())
    assert json.loads(path.read_text())[-1]["event"] == "hook_failure"


def test_legacy_image_metadata_return_is_preserved(tmp_path):
    image, metadata = np.zeros((2, 2)), {"k": "v"}

    class Legacy:
        def image_process_fn(self, image, metadata, event_queue):
            return image, metadata

    returned = UntrustedHookAdapter(Legacy()).image_process_fn(image, metadata, object())
    assert returned[0] is image and returned[1] is metadata


def test_resolved_saved_hook_gets_neither_ctrl_nor_guard(monkeypatch):
    from microclaw import hook_manager
    from microclaw.tools import _resolve_hook

    received = {}

    class Saved:
        def __init__(self, ctrl=None, guard=None):
            received.update(ctrl=ctrl, guard=guard)

        def analyze_frame(self, image, metadata):
            return None

    monkeypatch.setattr(hook_manager, "list_saved_hooks", lambda: {"saved": {}})
    monkeypatch.setattr(hook_manager, "load_hook_class", lambda name: Saved)
    adapter = _resolve_hook(object(), object(), "saved", {}, None)
    assert isinstance(adapter, UntrustedHookAdapter)
    assert received == {"ctrl": None, "guard": None}
