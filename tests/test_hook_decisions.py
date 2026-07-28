from __future__ import annotations

import json
import queue

import numpy as np
import pytest

from microclaw.hook_decisions import (
    AcquireAt, ContinueSurvey, HookResult, MoveStage, RequestAutofocus,
    SetExposure, StopSurvey, UntrustedHookAdapter,
)
from microclaw.hooks import HookBase
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


def test_action_list_is_normalized_but_other_iterables_are_rejected():
    result = HookResult({"score": 1}, [ContinueSurvey()])
    assert result.actions == (ContinueSurvey(),)
    with pytest.raises(TypeError, match="list or tuple"):
        HookResult({"score": 1}, "ContinueSurvey")
    with pytest.raises(TypeError, match="list or tuple"):
        HookResult({"score": 1}, (a for a in [ContinueSurvey()]))


def test_parent_writes_design26_observation_envelope_and_coordinates(tmp_path):
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult(
                {"score": 0.75}, [], analyzer="demo", analyzer_version="1.2",
                parameters={"threshold": 0.5}, artifact_sha256="abc",
                status="provisional",
            )

    path = tmp_path / "hook.json"
    adapter = UntrustedHookAdapter(Hook(), str(path))
    metadata = {
        "PositionName": "tile_0", "XPosition_um_Intended": 12.5,
        "YPosition_um_Intended": -3.25, "ZPosition_um_Intended": 7.0,
    }
    adapter.image_process_fn(np.ones((2, 2)), metadata, object())
    record = json.loads(path.read_text())[-1]
    assert {k: record[k] for k in ("position", "x_um", "y_um", "z_um")} == {
        "position": "tile_0", "x_um": 12.5, "y_um": -3.25, "z_um": 7.0,
    }
    assert record["schema"] == "microclaw.analysis-observation/v1"
    assert record["status"] == "provisional"
    assert record["analyzer"] == "demo"
    assert record["analyzer_version"] == "1.2"
    assert record["parameters"] == {"threshold": 0.5}
    assert record["artifact_sha256"] == "abc"
    assert record["result"] == {"score": 0.75}


def test_untrusted_hook_cannot_self_assert_observed_and_failure_is_logged(tmp_path):
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({}, status="observed")

    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    with pytest.raises(ValueError, match="not self-assertable"):
        adapter.image_process_fn(np.zeros((2, 2)), {}, object())
    assert "not self-assertable" in adapter._log[-1]["reason"]


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


@pytest.mark.parametrize("returned,outcome", [(None, "discarded"), ("keep", "retained")])
def test_legacy_parent_writes_one_frame_record_with_coordinates(
    returned, outcome, tmp_path
):
    class LegacyGenerated(HookBase):
        def image_process_fn(self, image, metadata, event_queue):
            self.log(metadata, mean=float(np.mean(image)))
            return returned

    path = tmp_path / "legacy.json"
    hook = LegacyGenerated()
    adapter = UntrustedHookAdapter(hook, str(path))
    metadata = {
        "PositionName": "tile_0", "XPosition_um_Intended": 12.5,
        "YPosition_um_Intended": -3.25,
    }
    assert adapter.image_process_fn(np.ones((4, 4)), metadata, object()) == returned
    assert json.loads(path.read_text()) == [{
        "position": "tile_0", "x_um": 12.5, "y_um": -3.25,
        "event": "legacy_hook_frame", "outcome": outcome,
    }]
    assert hook._log[0]["mean"] == 1.0  # parent did not copy this self-authored record


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


def test_absent_and_ambiguous_labels_are_refused_with_distinct_reasons(tmp_path):
    """The refusal record is the only account of why a tile was not acquired,
    so 'no such label' and 'that label is ambiguous' must not read alike."""
    adapter, candidates, _ = _adapter(AcquireAt("nope"), tmp_path)
    adapter.image_process_fn(np.zeros((2, 2)), {}, object())
    assert "no planned survey position is labelled" in adapter._log[-1]["reason"]

    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({}, (AcquireAt("dup"),))

    duplicated = [{"axes": {"position": "dup"}, "x": 0.0, "y": 0.0},
                  {"axes": {"position": "dup"}, "x": 1.0, "y": 0.0}]
    ambiguous = UntrustedHookAdapter(Hook(), str(tmp_path / "amb.json"))
    ambiguous.configure_adaptive(events=duplicated, candidates=queue.Queue(),
                                 progress=SurveyProgress(2), guard=_Guard(),
                                 max_events=2)
    ambiguous.image_process_fn(np.zeros((2, 2)), {}, object())
    assert "matches 2 planned positions" in ambiguous._log[-1]["reason"]
    assert candidates.empty()


@pytest.mark.parametrize("code_shape", ["hookbase_subclass", "names_log_path"])
def test_a_saved_hook_that_keeps_its_own_log_is_refused_off_rig(code_shape, monkeypatch):
    """Off-rig cover for the refusal the 2026-07-27 demo gate exposed.

    The integration version needs headless_mm and so is skipped without
    Micro-Manager — which is precisely why the silent-loss case survived two
    review passes. This one runs everywhere."""
    from microclaw import hook_manager
    from microclaw.hooks import HookBase
    from microclaw.tools import _resolve_hook

    if code_shape == "hookbase_subclass":
        class Saved(HookBase):
            def image_process_fn(self, image, metadata, event_queue):
                return image, metadata
    else:
        class Saved:                       # no HookBase, but claims a log path
            def __init__(self, log_path=None):
                self.log_path = log_path

            def image_process_fn(self, image, metadata, event_queue):
                return image, metadata

    monkeypatch.setattr(hook_manager, "list_saved_hooks", lambda: {"saved": {}})
    monkeypatch.setattr(hook_manager, "load_hook_class", lambda name: Saved)
    with pytest.raises(ValueError, match="writes its own log"):
        _resolve_hook(object(), object(), "saved", {}, "/ws/log.json")


def test_a_trusted_builtin_may_still_keep_its_own_log(tmp_path, monkeypatch):
    """The refusal is scoped to untrusted provenance. SNRObservationHook takes
    log_path and subclasses HookBase, and must keep doing so."""
    from microclaw.tools import _resolve_hook

    hook = _resolve_hook(object(), None, "snr_observer", {"min_snr": 3.0},
                         str(tmp_path / "trusted.json"))
    assert hook.log_path == str(tmp_path / "trusted.json")
    assert not isinstance(hook, UntrustedHookAdapter)
