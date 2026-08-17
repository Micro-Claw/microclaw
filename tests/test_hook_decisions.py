from __future__ import annotations

import json
import queue
from unittest.mock import MagicMock

import numpy as np
import pytest

from microclaw.hook_decisions import (
    AcquireAt, ContinueSurvey, DiscardFrame, EmitArtifact, HookResult,
    MoveNamedStage, MoveStage, SetDeviceProperty,
    RequestAutofocus, SetExposure, SetIlluminationPower, StopSurvey,
    CompositeHook, UntrustedHookAdapter, parse_action,
)
from microclaw.hooks import HookBase
from microclaw.safety import SafetyViolation
from microclaw.safety import (
    ForbiddenProperty, IlluminationConstraints, IlluminationProperty,
    SafetyConstraints, SafetyGuard,
)
from microclaw.tools import SurveyProgress


def _axes_plan(*entries):
    return {
        tuple(sorted(axes.items())): (index, actions)
        for index, (axes, actions) in enumerate(entries)
    }


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -float("inf")])
def test_move_named_stage_refuses_non_finite_and_boolean_positions(value):
    from microclaw.hook_decisions import parse_action
    with pytest.raises(ValueError, match="finite number"):
        parse_action({"kind": "MoveNamedStage", "position_um": value})


def test_move_named_stage_parses_typed_and_exact_dict_forms():
    from microclaw.hook_decisions import parse_action
    assert parse_action(MoveNamedStage(12.5)) == MoveNamedStage(12.5)
    assert parse_action({"kind": "MoveNamedStage", "position_um": 12.5}) == MoveNamedStage(12.5)
    with pytest.raises(ValueError, match="unexpected fields"):
        parse_action({"kind": "MoveNamedStage", "position_um": 12.5, "device": "stage"})


def test_named_stage_plan_dispatch_records_overshoot_and_keeps_index_out_of_axes():
    core = MagicMock()
    core.get_position.return_value = 21299
    guard = MagicMock()
    adapter = UntrustedHookAdapter(object())
    adapter.configure_named_stage(
        core=core, guard=guard, device="fixture-stage", min_um=20000,
        max_um=21294, max_writes=1, initial_value=21000, restore="leave",
        action_plan=_axes_plan(({"time": 0, "position": "p7"},
                                (MoveNamedStage(21294),))),
    )
    event = {
        "axes": {"time": 0, "position": "p7"}, "x": 1.25, "y": -2.5,
    }
    returned = adapter.pre_hardware_hook_fn(event)
    guard.check_named_stage.assert_called_once_with("fixture-stage", 21294.0)
    core.set_position.assert_called_once_with("fixture-stage", 21294.0)
    core.wait_for_device.assert_called_once_with("fixture-stage")
    assert returned["named_stage_achieved_um"] == 21299
    assert "hook_event_index" not in returned["axes"]
    assert adapter._log[-1]["achieved_um"] == 21299
    assert {key: adapter._log[-1][key] for key in ("position", "x_um", "y_um")} == {
        "position": "p7", "x_um": 1.25, "y_um": -2.5,
    }


@pytest.mark.parametrize("as_batch", [False, True])
def test_named_stage_plan_accepts_one_event_in_either_callback_shape(as_batch):
    core, guard = MagicMock(), MagicMock()
    core.get_position.return_value = 12
    adapter = UntrustedHookAdapter(object())
    adapter.configure_named_stage(
        core=core, guard=guard, device="fixture-stage", min_um=0, max_um=20,
        max_writes=1, initial_value=5, restore="leave",
        action_plan=_axes_plan(({"time": 0}, (MoveNamedStage(12),))),
    )
    event = {"axes": {"time": 0}}
    supplied = [event] if as_batch else event
    returned = adapter.pre_hardware_hook_fn(supplied)
    assert returned is supplied
    assert returned[0] is event if as_batch else returned is event
    core.set_position.assert_called_once_with("fixture-stage", 12.0)


@pytest.mark.parametrize("actions", [
    ((MoveNamedStage(10),), (MoveNamedStage(20),)),
    ((), ()),
])
def test_named_stage_plan_refuses_sequenced_batch_before_any_write(actions):
    core, guard = MagicMock(), MagicMock()
    adapter = UntrustedHookAdapter(object())
    adapter.configure_named_stage(
        core=core, guard=guard, device="fixture-stage", min_um=0, max_um=20,
        max_writes=2, initial_value=5, restore="leave",
        action_plan={0: actions[0], 1: actions[1]},
    )
    events = [
        {"axes": {"time": 0}},
        {"axes": {"time": 1}},
    ]
    with pytest.raises(RuntimeError, match="nonzero interval_s"):
        adapter.pre_hardware_hook_fn(events)
    core.set_position.assert_not_called()
    assert adapter._log[-1]["decision"] == "refused"
    assert "hardware-sequenced burst" in adapter._log[-1]["reason"]


@pytest.mark.parametrize("target", [10, 20])
def test_named_stage_envelope_boundaries_are_inclusive(target):
    core, guard = MagicMock(), MagicMock()
    core.get_position.return_value = target
    adapter = UntrustedHookAdapter(object())
    adapter.configure_named_stage(
        core=core, guard=guard, device="fixture-stage", min_um=10, max_um=20,
        max_writes=1, initial_value=15, restore="leave",
        action_plan=_axes_plan(({}, (MoveNamedStage(target),))),
    )
    adapter.pre_hardware_hook_fn({"axes": {}})
    core.set_position.assert_called_once_with("fixture-stage", float(target))


@pytest.mark.parametrize("target", [9.999999999, 20.000000001])
def test_named_stage_envelope_outside_boundary_refuses_without_write(target):
    core, guard = MagicMock(), MagicMock()
    adapter = UntrustedHookAdapter(object())
    adapter.configure_named_stage(
        core=core, guard=guard, device="fixture-stage", min_um=10, max_um=20,
        max_writes=1, initial_value=15, restore="leave",
        action_plan=_axes_plan(({}, (MoveNamedStage(target),))),
    )
    with pytest.raises(RuntimeError, match="outside authorized interval"):
        adapter.pre_hardware_hook_fn({"axes": {}})
    core.set_position.assert_not_called()


def test_analysis_returned_named_stage_action_is_refused_without_write():
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({}, (MoveNamedStage(17),))
    adapter = UntrustedHookAdapter(Hook())
    adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    assert "hook_action_plan" in adapter._log[-1]["reason"]


def test_analysis_move_cannot_replace_next_frames_preinstalled_move():
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({}, (MoveNamedStage(99),))

    core, guard = MagicMock(), MagicMock()
    core.get_position.side_effect = [10, 20]
    adapter = UntrustedHookAdapter(Hook())
    adapter.configure_named_stage(
        core=core, guard=guard, device="fixture-stage", min_um=0, max_um=100,
        max_writes=2, initial_value=5, restore="leave",
        action_plan=_axes_plan(
            ({"time": 0}, (MoveNamedStage(10),)),
            ({"time": 1}, (MoveNamedStage(20),)),
        ),
    )
    adapter.pre_hardware_hook_fn({"axes": {"time": 0}})
    adapter.image_process_fn(np.zeros((1, 1)), {"Axes": {"time": 0}}, object())
    adapter.pre_hardware_hook_fn({"axes": {"time": 1}})
    assert [call.args[1] for call in core.set_position.call_args_list] == [10.0, 20.0]
    assert any("hook_action_plan" in record.get("reason", "") for record in adapter._log)


@pytest.mark.parametrize("as_batch", [False, True])
@pytest.mark.parametrize("bad_event, consume_first", [
    ({"axes": {"time": 1}}, False),
    ({"axes": {"time": 0}}, True),
])
def test_missing_or_consumed_plan_axes_aborts_before_move_or_exposure(
    bad_event, consume_first, as_batch
):
    core, guard = MagicMock(), MagicMock()
    adapter = UntrustedHookAdapter(object())
    # Index 0 carries a real move, so the consumed-index limb proves the plan is
    # not replayed rather than merely that an empty entry writes nothing.
    adapter.configure_named_stage(
        core=core, guard=guard, device="fixture-stage", min_um=0, max_um=100,
        max_writes=4, initial_value=5, restore="leave",
        action_plan=_axes_plan(({"time": 0}, (MoveNamedStage(42),))),
    )
    first_pass_writes = 0
    if consume_first:
        adapter.pre_hardware_hook_fn({"axes": {"time": 0}})
        first_pass_writes = core.set_position.call_count
        assert first_pass_writes == 1
    with pytest.raises(RuntimeError, match=r"unconsumed axes.*time"):
        adapter.pre_hardware_hook_fn([bad_event] if as_batch else bad_event)
    # The refused callback returns before any write, so the count is unchanged.
    assert core.set_position.call_count == first_pass_writes


def test_named_stage_entry_restoration_uses_same_guard_move_wait_readback():
    core, guard = MagicMock(), MagicMock()
    core.get_position.side_effect = [12, 15]
    adapter = UntrustedHookAdapter(object())
    adapter.configure_named_stage(
        core=core, guard=guard, device="fixture-stage", min_um=10, max_um=20,
        max_writes=1, initial_value=12, restore="entry", action_plan={0: ()},
    )
    report = adapter.restore_named_stage()
    guard.check_named_stage.assert_called_once_with("fixture-stage", 12.0)
    core.set_position.assert_called_once_with("fixture-stage", 12.0)
    core.wait_for_device.assert_called_once_with("fixture-stage")
    assert report == {"policy": "entry", "entry_um": 12,
                      "last_known_um": 12.0, "restored": True}


def test_named_stage_failed_restoration_is_loud_and_not_success():
    core, guard = MagicMock(), MagicMock()
    core.set_position.side_effect = RuntimeError("bridge down")
    adapter = UntrustedHookAdapter(object())
    adapter.configure_named_stage(
        core=core, guard=guard, device="fixture-stage", min_um=10, max_um=20,
        max_writes=1, initial_value=12, restore="entry", action_plan={0: ()},
    )
    with pytest.raises(RuntimeError, match="bridge down"):
        adapter.restore_named_stage()
    assert adapter._log[-1]["decision"] == "failed"
    assert adapter._log[-1]["restoration"] is True


def test_preexposure_failure_reports_partial_path_frames_and_last_state(
    monkeypatch, tmp_path
):
    from microclaw import tools

    core, stage_guard = MagicMock(), MagicMock()
    adapter = UntrustedHookAdapter(object(), str(tmp_path / "hook.json"))
    adapter.configure_named_stage(
        core=core, guard=stage_guard, device="fixture-stage",
        min_um=10, max_um=20, max_writes=1, initial_value=15,
        restore="leave", action_plan={0: (MoveNamedStage(21),)},
    )

    class FakeAcquisition:
        def __init__(self, **kwargs):
            self.pre_hardware = kwargs["pre_hardware_hook_fn"]
            self._dataset_disk_location = str(tmp_path / "data_1")
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def acquire(self, events):
            self.pre_hardware(events[0])

    monkeypatch.setattr(tools, "Acquisition", FakeAcquisition)
    guard = MagicMock()
    guard.resolve_in_workspace.side_effect = lambda path: path
    reservation = MagicMock(completed_frames=2)
    with pytest.raises(tools._HookedAcquisitionFailure) as caught:
        tools._acquire_with_hooks(
            guard, str(tmp_path), "data",
            [{"axes": {"time": 0}, "hook_event_index": 0}], adapter,
            reservation=reservation,
        )
    result = tools._hooked_failure_result(caught.value, str(tmp_path / "hook.json"))
    assert result["dataset_path"] == str(tmp_path / "data_1")
    assert result["frames_exposed"] == 2
    assert result["last_hardware_state"] == {
        "device": "fixture-stage", "position_um": 15,
    }
    core.set_position.assert_not_called()

    # Same failure with no reservation: the survey runner passes a hook with
    # reservation=None whenever `adaptive` is false, so this path is real. It
    # must still report the dataset and the last known position, and must not
    # invent a frame count it never measured.
    adapter._fixed_plan_context["consumed"] = set()
    with pytest.raises(tools._HookedAcquisitionFailure) as unreserved:
        tools._acquire_with_hooks(
            guard, str(tmp_path), "data",
            [{"axes": {"time": 0}, "hook_event_index": 0}], adapter,
            reservation=None,
        )
    unreserved_result = tools._hooked_failure_result(unreserved.value, None)
    assert unreserved_result["dataset_path"] == str(tmp_path / "data_1")
    assert "frames_exposed" not in unreserved_result
    assert unreserved_result["last_hardware_state"] == {
        "device": "fixture-stage", "position_um": 15,
    }


def test_restorations_are_independent_and_mixed_failure_reports_both_states(
    monkeypatch, tmp_path
):
    from microclaw import tools

    class Hook:
        _named_stage_context = {"device": "Stage", "last_known": 12.5}
        _property_context = {
            "device": "Wheel", "property": "State", "last_known": "B",
        }
        def restore_named_stage(self):
            raise RuntimeError("stage stuck")
        def restore_property(self):
            self.property_restored = True
            return {"restored": True}

    class Acquisition:
        def __init__(self, **_kwargs):
            self._dataset_disk_location = str(tmp_path / "data_1")
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def acquire(self, _events): pass

    monkeypatch.setattr(tools, "Acquisition", Acquisition)
    hook = Hook()
    with pytest.raises(tools._HookedAcquisitionFailure) as caught:
        tools._acquire_with_hooks(
            MagicMock(), str(tmp_path), "data", [], hook, reservation=None,
        )
    assert hook.property_restored is True
    assert "named-stage restoration failed: stage stuck" in str(caught.value)
    assert caught.value.last_hardware_state == {
        "named_stage": {"device": "Stage", "position_um": 12.5},
        "property": {"device": "Wheel", "property": "State", "value": "B"},
    }


def test_property_only_restoration_failure_is_named_as_property(monkeypatch, tmp_path):
    from microclaw import tools

    class Hook:
        _named_stage_context = None
        _property_context = {
            "device": "Wheel", "property": "State", "last_known": "B",
        }
        def restore_property(self): raise RuntimeError("wheel stuck")

    class Acquisition:
        def __init__(self, **_kwargs):
            self._dataset_disk_location = str(tmp_path / "data_1")
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def acquire(self, _events): pass

    monkeypatch.setattr(tools, "Acquisition", Acquisition)
    with pytest.raises(tools._HookedAcquisitionFailure) as caught:
        tools._acquire_with_hooks(
            MagicMock(), str(tmp_path), "data", [], Hook(), reservation=None,
        )
    assert "property restoration failed: wheel stuck" in str(caught.value)
    assert "named-stage" not in str(caught.value)


def _events(n=3):
    return [
        {"axes": {"position": f"p{i}"}, "x": float(i), "y": float(i + 1)}
        for i in range(n)
    ]


def test_composite_observers_share_original_pixels_discard_wins_and_log_is_attributed(
    tmp_path,
):
    class Observer:
        def __init__(self, name, discard=False):
            self.name, self.discard, self.seen = name, discard, []

        def analyze_frame(self, image, _metadata):
            self.seen.append(int(image[0, 0]))
            image[0, 0] = 999
            actions = [EmitArtifact(f"{self.name}.bin", b"x")]
            if self.discard:
                actions.append(DiscardFrame())
            return HookResult({"name": self.name}, actions)

    first, second = Observer("first"), Observer("second", discard=True)
    composite = CompositeHook([
        ("first_hook", UntrustedHookAdapter(first)),
        ("second_hook", UntrustedHookAdapter(second)),
    ], str(tmp_path / "hook.json"))
    composite.configure_artifacts(
        target_dir=tmp_path / "artifacts", max_artifact_bytes=2,
        max_count=2, max_total_bytes=2,
    )
    returned = composite.image_process_fn(
        np.array([[7]], dtype=np.uint16), {"Axes": {}}, object()
    )

    assert returned is None
    assert first.seen == second.seen == [7]
    assert [p.name for p in (tmp_path / "artifacts").iterdir()] == ["first.bin"]
    records = json.loads((tmp_path / "hook.json").read_text(encoding="utf-8"))
    assert {record["hook_strategy"] for record in records} == {
        "first_hook", "second_hook"
    }
    refused = [r for r in records if r.get("decision") == "refused"]
    assert "one artifact for this frame" in refused[0]["reason"]


def test_single_hook_can_emit_one_artifact_on_each_of_five_frames(tmp_path):
    class PerFrameEmitter:
        def __init__(self):
            self.frame = 0

        def analyze_frame(self, _image, _metadata):
            filename = f"frame-{self.frame}.bin"
            self.frame += 1
            return HookResult({}, (EmitArtifact(filename, b"x"),))

    adapter = UntrustedHookAdapter(PerFrameEmitter())
    artifact_dir = tmp_path / "artifacts"
    adapter.configure_artifacts(
        target_dir=artifact_dir, max_artifact_bytes=1,
        max_count=5, max_total_bytes=5,
    )
    for frame in range(5):
        assert adapter.image_process_fn(
            np.zeros((1, 1), dtype=np.uint8), {"Axes": {"time": frame}}, object()
        ) is not None

    assert sorted(path.name for path in artifact_dir.iterdir()) == [
        f"frame-{frame}.bin" for frame in range(5)
    ]


def test_composite_post_hardware_chains_and_rejects_none():
    class Add:
        def __init__(self, amount): self.amount = amount
        def post_hardware_hook_fn(self, event):
            return {**event, "value": event.get("value", 0) + self.amount}

    assert CompositeHook([("a", Add(2)), ("b", Add(3))], None).post_hardware_hook_fn(
        {"value": 1}
    )["value"] == 6

    class Broken:
        def post_hardware_hook_fn(self, _event): return None

    with pytest.raises(RuntimeError, match="must return the event"):
        CompositeHook([("broken", Broken())], None).post_hardware_hook_fn({})


def test_composite_post_hardware_preserves_sequenced_list_shape():
    class Add:
        def post_hardware_hook_fn(self, event):
            event["value"] = event.get("value", 0) + 1
            return event

    events = [{"value": 1}, {"value": 4}]
    returned = CompositeHook([("add", Add())], None).post_hardware_hook_fn(events)
    assert returned is events
    assert [event["value"] for event in events] == [2, 5]


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


def _configure_refocus(adapter, candidates, progress, *, guard=None,
                       max_exposures=4, lock=None):
    ctrl = type("Ctrl", (), {"core": type("Core", (), {
        "get_position": lambda self: 10.0,
    })()})()
    adapter.configure_autofocus(
        ctrl=ctrl, guard=guard or _Guard(), max_exposures=max_exposures,
        z_range_um=2.0, z_step_um=1.0, method="single_sweep", settle_ms=0,
        sweep_exposures=3, focus_lock_check=(lambda: lock or {"engaged": False}),
    )
    adapter.configure_adaptive(events=_events(2), candidates=candidates,
                               progress=progress, guard=guard or _Guard(),
                               max_events=2 + adapter.planned_refocus_reexposures())


def test_refocus_requeues_one_second_look_and_then_refuses_same_tile(tmp_path, monkeypatch):
    from microclaw.autofocus import AutofocusResult, SweepResult
    import microclaw.tools as tools

    seen = []
    class Hook:
        def analyze_frame(self, _image, metadata):
            seen.append(metadata["microclaw_refocused"])
            return HookResult({}, (RequestAutofocus(),))

    sweep = SweepResult([9, 10, 11], [1, 2, 1], 10, True)
    monkeypatch.setattr(tools, "_run_autofocus_passes", lambda *a: AutofocusResult(
        sweep, None, 10, 10, True, False, None
    ))
    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    candidates, progress = queue.Queue(), SurveyProgress(2)
    _configure_refocus(adapter, candidates, progress)
    metadata = {"PositionName": "p0", "XPosition_um_Intended": 0.0,
                "YPosition_um_Intended": 0.0}
    adapter.image_process_fn(np.zeros((2, 2)), metadata, object())
    refocused = candidates.get_nowait()
    assert refocused["axes"] == {"position": "p0", "refocus": 1}
    adapter.image_process_fn(np.zeros((2, 2)), metadata, object())
    assert seen == [False, True]
    assert adapter._log[-1]["reason"] == "this tile has already been refocused"


def test_actions_after_refocus_are_counted_and_refused_not_dropped(tmp_path, monkeypatch):
    from microclaw.autofocus import AutofocusResult, SweepResult
    import microclaw.tools as tools

    class Hook:
        def analyze_frame(self, _image, _metadata):
            return HookResult({}, (RequestAutofocus(), ContinueSurvey()))

    sweep = SweepResult([9, 10, 11], [1, 2, 1], 10, True)
    monkeypatch.setattr(tools, "_run_autofocus_passes", lambda *a: AutofocusResult(
        sweep, None, 10, 10, True, False, None
    ))
    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    candidates, progress = queue.Queue(), SurveyProgress(3)
    adapter.configure_autofocus(
        ctrl=type("Ctrl", (), {"core": type("Core", (), {
            "get_position": lambda self: 10.0,
        })()})(), guard=_Guard(), max_exposures=8, z_range_um=2,
        z_step_um=1, method="single_sweep", settle_ms=0, sweep_exposures=3,
    )
    adapter.configure_adaptive(events=_events(3), candidates=candidates,
                               progress=progress, guard=_Guard(), max_events=5)
    adapter.image_process_fn(
        np.zeros((2, 2)),
        {"PositionName": "p0", "XPosition_um_Intended": 0.0,
         "YPosition_um_Intended": 0.0}, object(),
    )
    assert [candidates.get_nowait()["axes"]["position"]] == ["p0"]
    assert adapter.action_counts == {"RequestAutofocus": 1, "ContinueSurvey": 1}
    refused = adapter._log[-1]
    assert refused["action"]["kind"] == "ContinueSurvey"
    assert refused["decision"] == "refused"
    assert refused["reason"] == "not dispatched until the refocused tile is judged"


def test_converged_refocus_plane_is_adopted_by_later_timelapse_tiles(
    tmp_path, monkeypatch
):
    from microclaw.autofocus import AutofocusResult, SweepResult
    import microclaw.tools as tools

    seen = []
    class Core:
        z = 10.0
        def get_position(self): return self.z
    core = Core()
    class Hook:
        def analyze_frame(self, _image, metadata):
            seen.append((metadata["PositionName"], metadata["microclaw_refocused"]))
            return HookResult({}, (
                ContinueSurvey() if metadata["microclaw_refocused"]
                else RequestAutofocus(),
            ))

    sweep = SweepResult([9, 10, 11], [1, 2, 3], 12, True)
    def focus(*_args):
        core.z = 12.0
        return AutofocusResult(sweep, None, 10, 12, True, True, None)
    monkeypatch.setattr(tools, "_run_autofocus_passes", focus)
    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    candidates, progress = queue.Queue(), SurveyProgress(2)
    adapter.configure_autofocus(
        ctrl=type("Ctrl", (), {"core": core})(), guard=_Guard(), max_exposures=4,
        z_range_um=2, z_step_um=1, method="single_sweep", settle_ms=0,
        sweep_exposures=3,
    )
    adapter.configure_adaptive(events=_events(2), candidates=candidates,
                               progress=progress, guard=_Guard(), max_events=3)
    p0 = {"PositionName": "p0", "XPosition_um_Intended": 0.0,
          "YPosition_um_Intended": 0.0}
    adapter.image_process_fn(np.zeros((2, 2)), p0, object())
    refocused = candidates.get_nowait()
    assert "z" not in refocused
    adapter.image_process_fn(np.zeros((2, 2)), p0, object())
    next_tile = candidates.get_nowait()
    assert next_tile["axes"]["position"] == "p1"
    assert "z" not in next_tile
    assert core.z == 12.0
    assert seen == [("p0", False), ("p0", True)]


@pytest.mark.parametrize("case, expected", [
    ("exhausted", "authorized autofocus exposure budget exhausted"),
    ("guard", "SafetyGuard refused autofocus sweep"),
    ("lock", "focus lock is engaged; autofocus sweep refused"),
])
def test_refocus_refusal_paths_have_distinct_reasons(case, expected, tmp_path):
    class Hook:
        def analyze_frame(self, _image, _metadata):
            return HookResult({}, (RequestAutofocus(),))
    class ZGuard(_Guard):
        def check_z(self, _z):
            if case == "guard":
                raise SafetyViolation("unsafe z")

    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    candidates, progress = queue.Queue(), SurveyProgress(2)
    _configure_refocus(
        adapter, candidates, progress, guard=ZGuard(),
        max_exposures=3 if case == "exhausted" else 4,
        lock={"engaged": case == "lock"},
    )
    adapter.image_process_fn(
        np.zeros((2, 2)),
        {"PositionName": "p0", "XPosition_um_Intended": 0.0,
         "YPosition_um_Intended": 0.0}, object(),
    )
    assert candidates.empty()
    assert adapter._log[-1]["reason"].startswith(expected)


def test_nonconverging_refocus_is_recorded_without_requeue_or_retry(tmp_path, monkeypatch):
    from microclaw.autofocus import AutofocusResult, SweepResult
    import microclaw.tools as tools

    class Hook:
        def analyze_frame(self, _image, _metadata):
            return HookResult({}, (RequestAutofocus(),))

    sweep = SweepResult([9, 10, 11], [1, 1, 1], 9, False)
    calls = []
    monkeypatch.setattr(tools, "_run_autofocus_passes", lambda *a: (
        calls.append(a) or AutofocusResult(sweep, None, 10, 10, False, False, "flat")
    ))
    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    candidates, progress = queue.Queue(), SurveyProgress(2)
    _configure_refocus(adapter, candidates, progress, max_exposures=8)
    metadata = {"PositionName": "p0", "XPosition_um_Intended": 0.0,
                "YPosition_um_Intended": 0.0}
    adapter.image_process_fn(np.zeros((2, 2)), metadata, object())
    adapter.image_process_fn(np.zeros((2, 2)), metadata, object())
    assert len(calls) == 1
    assert candidates.empty()
    accepted = next(r for r in adapter._log if r.get("decision") == "accepted")
    assert accepted["reason"] == "autofocus ran and did not converge; Z restored"
    assert accepted["autofocus"]["reason"] == "flat"


def test_autofocus_budget_widens_dose_reservation_by_its_maximum():
    from microclaw.acquisition import AcquisitionPlan
    from microclaw.tools import _plan_with_hook_dose

    adapter = UntrustedHookAdapter(type("Hook", (), {})())
    adapter.configure_autofocus(
        ctrl=object(), guard=_Guard(), max_exposures=4, z_range_um=2,
        z_step_um=1, method="single_sweep", settle_ms=0, sweep_exposures=3,
    )
    base = AcquisitionPlan(frames=2, exposure_ms_per_frame=5,
                           estimated_duration_s=.01, estimated_bytes=100)
    widened = _plan_with_hook_dose(base, adapter)
    assert widened.frames == 6          # two planned + all four authorized extras
    assert adapter.planned_refocus_reexposures() == 1

    adapter.configure_autofocus(
        ctrl=object(), guard=_Guard(), max_exposures=3, z_range_um=2,
        z_step_um=1, method="single_sweep", settle_ms=0, sweep_exposures=3,
    )
    assert _plan_with_hook_dose(base, adapter).frames == 5
    assert adapter.planned_refocus_reexposures() == 0  # sweep alone buys no re-exposure


def test_continue_dispatches_next_planned_tile(tmp_path):
    adapter, candidates, progress = _adapter(ContinueSurvey(), tmp_path)
    adapter.image_process_fn(np.zeros((2, 2)), {"PositionName": "p0"}, object())
    assert candidates.get_nowait()["axes"]["position"] == "p1"
    assert adapter._log[-1]["decision"] == "accepted"


def test_continue_is_an_accepted_noop_under_a_fixed_plan(tmp_path):
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({}, (ContinueSurvey(),))

    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    adapter.image_process_fn(np.zeros((2, 2)), {"PositionName": "p0"}, object())
    record = adapter._log[-1]
    assert record["decision"] == "accepted"
    assert "noop" in record["reason"]


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


def test_deferred_acquire_at_records_current_z_deduplicates_and_caps(tmp_path):
    actions = iter((AcquireAt("p0"), AcquireAt("p0"), AcquireAt("p1")))

    class Hook:
        def analyze_frame(self, _image, _metadata):
            return HookResult({}, (next(actions),))

    hits = []
    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    candidates, progress = queue.Queue(), SurveyProgress(3)
    adapter.configure_adaptive(
        events=_events(), candidates=candidates, progress=progress, guard=_Guard(),
        max_events=3, acquire_hits=hits, max_hits=1, read_z=lambda: 12.5,
    )
    for label in ("p0", "p0", "p1"):
        adapter.image_process_fn(np.zeros((2, 2)), {"PositionName": label}, object())

    assert candidates.empty(), "deferred hits never enter the search event source"
    assert [{k: v for k, v in hits[0].items() if k != "_key"}] == [{
        "name": "p0", "x_um": 0.0, "y_um": 1.0, "z_um": 12.5,
    }]
    reasons = [record["reason"] for record in adapter._log
               if record.get("event") == "hook_action"]
    assert reasons == [
        "planned tile recorded for acquire phase",
        "planned tile is already recorded for acquire phase",
        "acquire phase max_hits exhausted",
    ]


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
def test_unknown_or_malformed_action_is_refused_before_dispatch(action, tmp_path):
    adapter, candidates, progress = _adapter(action, tmp_path)
    returned = adapter.image_process_fn(np.zeros((2, 2)), {}, object())
    assert returned is not None
    assert candidates.empty() and progress.n_done == 1
    assert adapter._log[-1]["event"] == "hook_action"
    assert adapter._log[-1]["decision"] == "refused"


def test_non_json_measurements_fail_closed(tmp_path):
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({"bad": float("nan")})

    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    with pytest.raises(ValueError):
        adapter.image_process_fn(np.zeros((2, 2)), {}, object())
    assert adapter._log[-1]["event"] == "hook_failure"


def _illumination_adapter(action, tmp_path, *, ceiling=20, writes=2,
                          initial=5, configured=30, factor=2):
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({}, (action,))
    core = type("Core", (), {})()
    core.writes = []
    core.set_property = lambda device, prop, value: core.writes.append(
        (device, prop, value)
    )
    guard = SafetyGuard(SafetyConstraints(illumination=IlluminationConstraints(
        power_properties=[ForbiddenProperty("Laser", "Power")],
        max_power_percent=configured, max_power_step_factor=factor,
    )))
    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    adapter.configure_illumination(
        core=core, guard=guard, device="Laser", property="Power",
        max_power_percent=ceiling, max_writes=writes, initial_value=initial,
    )
    return adapter, core


def test_illumination_without_envelope_is_refused(tmp_path):
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({}, (SetIlluminationPower(2),))
    adapter = UntrustedHookAdapter(Hook())
    adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    assert adapter._log[-1]["reason"] == "no illumination envelope was authorized for this run"


def test_illumination_envelope_ceiling_is_attributable(tmp_path):
    adapter, core = _illumination_adapter(SetIlluminationPower(21), tmp_path)
    adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    assert not core.writes
    assert adapter._log[-1]["reason"] == "proposal exceeds authorized envelope ceiling"


def test_illumination_step_uses_last_parent_value(tmp_path):
    adapter, core = _illumination_adapter(SetIlluminationPower(11), tmp_path)
    adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    assert not core.writes
    assert "SafetyGuard refused illumination" in adapter._log[-1]["reason"]
    assert "per-write ratchet" in adapter._log[-1]["reason"]


def test_illumination_write_budget_is_attributable(tmp_path):
    class Hook:
        def __init__(self): self.values = iter((6, 7))
        def analyze_frame(self, image, metadata):
            return HookResult({}, (SetIlluminationPower(next(self.values)),))
    core = MagicMock()
    guard = SafetyGuard(SafetyConstraints(illumination=IlluminationConstraints(
        power_properties=[ForbiddenProperty("Laser", "Power")],
        max_power_percent=20, max_power_step_factor=2,
    )))
    adapter = UntrustedHookAdapter(Hook())
    adapter.configure_illumination(
        core=core, guard=guard, device="Laser", property="Power",
        max_power_percent=20, max_writes=1, initial_value=5,
    )
    image = np.zeros((1, 1))
    adapter.image_process_fn(image, {}, object())
    adapter.image_process_fn(image, {}, object())
    assert core.set_property.call_count == 1
    assert adapter._log[-1]["reason"] == "authorized illumination write budget exhausted"


def test_exhausted_budget_still_allows_wind_down_and_zero(tmp_path):
    class Hook:
        def __init__(self): self.values = iter((6, 4, 0))
        def analyze_frame(self, image, metadata):
            return HookResult({}, (SetIlluminationPower(next(self.values)),))
    core = MagicMock()
    guard = SafetyGuard(SafetyConstraints(illumination=IlluminationConstraints(
        power_properties=[ForbiddenProperty("Laser", "Power")],
        max_power_percent=20, max_power_step_factor=2,
    )))
    adapter = UntrustedHookAdapter(Hook())
    adapter.configure_illumination(
        core=core, guard=guard, device="Laser", property="Power",
        max_power_percent=20, max_writes=1, initial_value=5,
    )
    for _ in range(3):
        adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    assert [call.args[2] for call in core.set_property.call_args_list] == ["6.0", "4.0", "0.0"]
    assert adapter._illumination_context["remaining"] == 0
    assert all(r.get("decision") == "accepted" for r in adapter._log if "decision" in r)


def test_increase_after_wind_down_is_charged_normally(tmp_path):
    class Hook:
        def __init__(self): self.values = iter((4, 5, 6))
        def analyze_frame(self, image, metadata):
            return HookResult({}, (SetIlluminationPower(next(self.values)),))
    core = MagicMock()
    guard = SafetyGuard(SafetyConstraints(illumination=IlluminationConstraints(
        power_properties=[ForbiddenProperty("Laser", "Power")],
        max_power_percent=20, max_power_step_factor=2,
    )))
    adapter = UntrustedHookAdapter(Hook())
    adapter.configure_illumination(
        core=core, guard=guard, device="Laser", property="Power",
        max_power_percent=20, max_writes=1, initial_value=5,
    )
    for _ in range(3):
        adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    assert [call.args[2] for call in core.set_property.call_args_list] == ["4.0", "5.0"]
    assert adapter._log[-1]["reason"] == "authorized illumination write budget exhausted"


def test_parent_device_write_failure_has_own_record(tmp_path):
    adapter, core = _illumination_adapter(SetIlluminationPower(6), tmp_path)
    def fail(*args): raise RuntimeError("bridge down")
    core.set_property = fail
    adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    assert adapter._log[-1]["event"] == "illumination_write_failure"
    assert adapter._log[-1]["decision"] == "failed"
    assert "bridge down" in adapter._log[-1]["reason"]
    assert adapter._log[-1]["baseline_stale"] is True
    assert not any(r.get("event") == "hook_failure" for r in adapter._log)


def test_failed_write_rereads_true_baseline_before_ratchet(tmp_path):
    class Hook:
        def __init__(self): self.values = iter((25, 60))
        def analyze_frame(self, image, metadata):
            return HookResult({}, (SetIlluminationPower(next(self.values)),))
    core = MagicMock()
    core.get_property.return_value = "25.0000"
    core.set_property.side_effect = RuntimeError("Serial timeout occurred. (17)")
    guard = SafetyGuard(SafetyConstraints(illumination=IlluminationConstraints(
        power_properties=[ForbiddenProperty("Laser", "Power")],
        max_power_percent=100, max_power_step_factor=2,
    )))
    adapter = UntrustedHookAdapter(Hook())
    adapter.configure_illumination(
        core=core, guard=guard, device="Laser", property="Power",
        max_power_percent=100, max_writes=1, initial_value=0,
    )
    image = np.zeros((1, 1))
    adapter.image_process_fn(image, {}, object())
    adapter.image_process_fn(image, {}, object())

    core.get_property.assert_called_once_with("Laser", "Power")
    assert core.set_property.call_count == 1
    assert adapter._illumination_context["remaining"] == 1
    assert adapter._log[-2] == {
        "position": None,
        "event": "illumination_baseline_reread", "decision": "succeeded",
        "value_percent": 25.0, "baseline_stale": False,
    }
    assert "per-write ratchet" in adapter._log[-1]["reason"]


def test_failed_baseline_reread_refuses_with_distinct_reason(tmp_path):
    class Hook:
        def __init__(self): self.values = iter((6, 7))
        def analyze_frame(self, image, metadata):
            return HookResult({}, (SetIlluminationPower(next(self.values)),))
    adapter, core = _illumination_adapter(None, tmp_path)
    adapter.hook = Hook()
    core.set_property = MagicMock(side_effect=RuntimeError("write timeout"))
    core.get_property = MagicMock(side_effect=RuntimeError("read timeout"))
    image = np.zeros((1, 1))
    adapter.image_process_fn(image, {}, object())
    adapter.image_process_fn(image, {}, object())

    assert core.set_property.call_count == 1
    assert adapter._illumination_context["baseline_stale"] is True
    assert adapter._log[-1]["decision"] == "refused"
    assert adapter._log[-1]["reason"] == (
        "illumination baseline could not be re-established: read timeout"
    )


def test_healthy_illumination_write_does_not_reread(tmp_path):
    adapter, core = _illumination_adapter(SetIlluminationPower(6), tmp_path)
    core.get_property = MagicMock()
    adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    core.get_property.assert_not_called()


def test_config_ceiling_still_wins_if_parent_context_is_wrongly_wide(tmp_path):
    adapter, core = _illumination_adapter(
        SetIlluminationPower(31), tmp_path, ceiling=50, configured=30, factor=100,
    )
    adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    assert not core.writes
    assert "SafetyGuard refused illumination" in adapter._log[-1]["reason"]
    assert "max_power_percent" in adapter._log[-1]["reason"]


def test_shutter_enable_is_not_in_closed_union():
    with pytest.raises(ValueError, match="Unknown hook action"):
        from microclaw.hook_decisions import parse_action
        parse_action({"kind": "SetIlluminationShutter", "value": "On"})


@pytest.mark.parametrize("payload, expected", [
    ({"type": "MoveNamedStage", "position_um": 12}, "kind"),
    ({"kind": "MoveNamedStage", "params": {"position_um": 12}}, "position_um"),
])
def test_move_named_stage_wrong_shapes_name_the_accepted_shape(payload, expected):
    from microclaw.hook_decisions import parse_action
    with pytest.raises(ValueError) as caught:
        parse_action(payload)
    message = str(caught.value)
    assert "MoveNamedStage" in message
    assert "kind" in message
    assert expected in message


def test_discard_records_observation_and_discards_pixels(tmp_path):
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({"score": 0}, (DiscardFrame(),))
    adapter = UntrustedHookAdapter(Hook())
    assert adapter.image_process_fn(np.zeros((1, 1)), {}, object()) is None
    assert adapter._log[0]["schema"] == "microclaw.analysis-observation/v1"
    assert adapter._log[-1]["event"] == "legacy_hook_frame"
    assert adapter._log[-1]["outcome"] == "discarded"


@pytest.mark.parametrize("filename", ["../x", "/abs/x", "a/b", "", "a\\b"])
def test_artifact_escape_names_are_refused(filename, tmp_path):
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({}, (EmitArtifact(filename, b"x"),))
    adapter = UntrustedHookAdapter(Hook())
    adapter.configure_artifacts(target_dir=tmp_path, max_artifact_bytes=10,
                                max_count=2, max_total_bytes=10)
    adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    assert "bare filename" in adapter._log[-1]["reason"]


def test_artifact_limits_and_collision_are_distinct(tmp_path):
    class Hook:
        def __init__(self): self.payload = b"123"
        def analyze_frame(self, image, metadata):
            return HookResult({}, (EmitArtifact("x.bin", self.payload),))
    hook = Hook()
    adapter = UntrustedHookAdapter(hook)
    adapter.configure_artifacts(target_dir=tmp_path, max_artifact_bytes=2,
                                max_count=2, max_total_bytes=4)
    adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    assert "artifact size 3 bytes" in adapter._log[-1]["reason"]
    assert "limit 2 bytes" in adapter._log[-1]["reason"]
    hook.payload = b"12"
    adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    assert "collides" in adapter._log[-1]["reason"]


def test_artifact_per_run_total_is_refused(tmp_path):
    class Hook:
        def __init__(self): self.i = 0
        def analyze_frame(self, image, metadata):
            self.i += 1
            return HookResult({}, (EmitArtifact(f"{self.i}.bin", b"123"),))
    adapter = UntrustedHookAdapter(Hook())
    adapter.configure_artifacts(target_dir=tmp_path, max_artifact_bytes=3,
                                max_count=3, max_total_bytes=5)
    adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    assert "total-bytes" in adapter._log[-1]["reason"]


def test_artifact_count_limit_is_refused_distinctly(tmp_path):
    class Hook:
        def __init__(self): self.i = 0
        def analyze_frame(self, image, metadata):
            self.i += 1
            return HookResult({}, (EmitArtifact(f"{self.i}.bin", b"x"),))
    adapter = UntrustedHookAdapter(Hook())
    adapter.configure_artifacts(target_dir=tmp_path, max_artifact_bytes=2,
                                max_count=1, max_total_bytes=10)
    adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    assert adapter._log[-1]["reason"] == "artifact count limit exhausted"


def test_only_one_artifact_may_be_proposed_per_frame(tmp_path):
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({}, (
                EmitArtifact("a.bin", b"a"), EmitArtifact("b.bin", b"b"),
            ))
    target = tmp_path / "artifacts"
    adapter = UntrustedHookAdapter(Hook())
    adapter.configure_artifacts(target_dir=target, max_artifact_bytes=2,
                                max_count=2, max_total_bytes=4)
    with pytest.raises(ValueError, match="at most one EmitArtifact"):
        adapter.image_process_fn(np.zeros((1, 1)), {}, object())
    assert not target.exists()


def test_action_list_is_normalized_but_other_iterables_are_rejected():
    result = HookResult({"score": 1}, [ContinueSurvey()])
    assert result.actions == (ContinueSurvey(),)
    with pytest.raises(TypeError, match="list or tuple"):
        HookResult({"score": 1}, "ContinueSurvey")
    with pytest.raises(TypeError, match="list or tuple"):
        HookResult({"score": 1}, (a for a in [ContinueSurvey()]))


def test_malformed_emit_artifact_is_refused_without_aborting_frame(tmp_path):
    class Hook:
        def analyze_frame(self, image, metadata):
            return HookResult({}, (EmitArtifact(b"payload", "result.bin"),))

    adapter = UntrustedHookAdapter(Hook(), log_path=str(tmp_path / "hook.json"))
    image = np.zeros((2, 2), dtype=np.uint16)
    returned = adapter.image_process_fn(image, {"Axes": {"position": "p"}}, object())

    assert returned[0] is image
    assert adapter._log[-1]["decision"] == "refused"
    assert "filename must be a string" in adapter._log[-1]["reason"]


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
    record = json.loads(path.read_text(encoding="utf-8"))[-1]
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
    assert json.loads(path.read_text(encoding="utf-8"))[-1]["event"] == "hook_failure"


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
    assert json.loads(path.read_text(encoding="utf-8")) == [{
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


def test_composed_observers_cannot_reach_each_other_through_nested_metadata():
    """Isolation covers metadata's nesting, not just the pixel buffer.

    A shallow dict(metadata) left metadata["Axes"] shared, so one hook could
    rewrite the next hook's position — and the parent's, since HookBase.where
    reads the same key when it attributes the log record.
    """
    class Mutator:
        def image_process_fn(self, image, metadata, _queue):
            metadata["Axes"]["position"] = "HIJACKED"
            image[0, 0] = 999
            return image, metadata

    class Victim:
        def __init__(self):
            self.seen = None

        def image_process_fn(self, image, metadata, _queue):
            self.seen = (metadata["Axes"]["position"], int(image[0, 0]))
            return image, metadata

    victim = Victim()
    composite = CompositeHook([("mutator", Mutator()), ("victim", victim)], None)
    metadata = {"Axes": {"position": "p0"}}
    composite.image_process_fn(np.zeros((2, 2), dtype=int), metadata, object())

    assert victim.seen == ("p0", 0), "an observer saw another observer's edits"
    assert metadata == {"Axes": {"position": "p0"}}, "the parent's metadata was mutated"


def test_refocus_axis_is_dense_so_first_looks_stay_enumerable(tmp_path, monkeypatch):
    """M5, 2026-08-11: the second look stopped overwriting the first and the
    first looks became unreachable instead.

    NDTiff keys every frame by its exact axis set. With refocus=1 on the second
    look and no such key on the first, `dataset.axes["refocus"]` reads [1], and
    any reader enumerating the Cartesian product of the axes generates only
    refocus=1 cells. `export_dataset_as_tiff` does exactly that: the M5 dataset
    held 4 real frames and exported as 1 real frame plus 2 zeros. Stamping
    refocus=0 on the plan makes the axis dense, so both looks enumerate.
    """
    from microclaw.autofocus import AutofocusResult, SweepResult
    import microclaw.tools as tools

    class Hook:
        def analyze_frame(self, _image, metadata):
            return HookResult({}, () if metadata["microclaw_refocused"]
                              else (RequestAutofocus(),))

    sweep = SweepResult([9, 10, 11], [1, 2, 1], 10, True)
    monkeypatch.setattr(tools, "_run_autofocus_passes", lambda *a: AutofocusResult(
        sweep, None, 10, 10, True, False, None
    ))
    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    candidates, progress = queue.Queue(), SurveyProgress(3)
    planned = _events(3)
    adapter.configure_autofocus(
        ctrl=type("Ctrl", (), {"core": type("Core", (), {
            "get_position": lambda self: 10.0,
        })()})(), guard=_Guard(), max_exposures=8, z_range_um=2,
        z_step_um=1, method="single_sweep", settle_ms=0, sweep_exposures=3,
    )
    adapter.configure_adaptive(events=planned, candidates=candidates,
                               progress=progress, guard=_Guard(), max_events=5)

    # The plan itself now carries the axis, so the stream yields it too.
    assert [e["axes"]["refocus"] for e in planned] == [0, 0, 0]

    adapter.image_process_fn(
        np.zeros((2, 2)),
        {"PositionName": "p0", "XPosition_um_Intended": 0.0,
         "YPosition_um_Intended": 0.0}, object(),
    )
    second = candidates.get_nowait()
    assert second["axes"] == {"position": "p0", "refocus": 1}

    # Both values are present across the frames a reader would index, which is
    # the property that was actually missing on the rig.
    assert {e["axes"]["refocus"] for e in planned} | {second["axes"]["refocus"]} == {0, 1}


def test_survey_without_autofocus_budget_keeps_its_axes_untouched():
    """An unauthorized run must produce exactly the dataset shape it did before."""
    adapter = UntrustedHookAdapter(type("Hook", (), {})())
    planned = _events(2)
    adapter.configure_adaptive(events=planned, candidates=queue.Queue(),
                               progress=SurveyProgress(2), guard=_Guard(),
                               max_events=2)
    assert [e["axes"] for e in planned] == [{"position": "p0"}, {"position": "p1"}]


def test_a_finished_plan_is_reported_as_finished_not_as_a_dose_cap(tmp_path, monkeypatch):
    """M5 2026-08-11 round 3: the last tile's ContinueSurvey said the wrong thing.

    An authorized refocus widens max_events by the re-exposures it may take, so a
    three-tile survey that spent its one re-exposure satisfies BOTH the
    reservation check and the cursor check at the last tile. The reservation
    message won, and "outside committed reservation" reads as a dose cap when the
    survey had simply run out of tiles.
    """
    from microclaw.autofocus import AutofocusResult, SweepResult
    import microclaw.tools as tools

    class Hook:
        def analyze_frame(self, _image, metadata):
            # Only the first tile asks for a refocus; the last one just tries to
            # advance, which is where the wrong message appeared.
            if metadata["PositionName"] == "p0" and not metadata["microclaw_refocused"]:
                return HookResult({}, (RequestAutofocus(),))
            return HookResult({}, (ContinueSurvey(),))

    sweep = SweepResult([9, 10, 11], [1, 2, 1], 10, True)
    monkeypatch.setattr(tools, "_run_autofocus_passes", lambda *a: AutofocusResult(
        sweep, None, 10, 10, True, False, None
    ))
    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    candidates, progress = queue.Queue(), SurveyProgress(2)
    adapter.configure_autofocus(
        ctrl=type("Ctrl", (), {"core": type("Core", (), {
            "get_position": lambda self: 10.0,
        })()})(), guard=_Guard(), max_exposures=4, z_range_um=2,
        z_step_um=1, method="single_sweep", settle_ms=0, sweep_exposures=3,
    )
    events = _events(2)
    adapter.configure_adaptive(events=events, candidates=candidates,
                               progress=progress, guard=_Guard(),
                               max_events=2 + adapter.planned_refocus_reexposures())
    md = {"PositionName": "p0", "XPosition_um_Intended": 0.0,
          "YPosition_um_Intended": 0.0}
    adapter.image_process_fn(np.zeros((2, 2)), md, object())      # refocus p0
    adapter.image_process_fn(np.zeros((2, 2)), md, object())      # second look -> p1
    md1 = {"PositionName": "p1", "XPosition_um_Intended": 1.0,
           "YPosition_um_Intended": 2.0}
    adapter.image_process_fn(np.zeros((2, 2)), md1, object())     # p1: nothing left

    assert adapter._log[-1]["decision"] == "refused"
    assert adapter._log[-1]["reason"] == "planned survey cursor is already at the end"


def test_a_refused_refocus_still_lets_the_survey_advance(tmp_path):
    """The stall trap from M5 round 3, and the pattern hook_docs now prescribes.

    A budget below one sweep refuses the refocus. A hook that also proposed a
    route must still be routed, or the survey idles out max_idle_s and dies —
    which cost a three-tile run after two tiles.
    """
    class Hook:
        def analyze_frame(self, _image, _metadata):
            return HookResult({}, (RequestAutofocus(), ContinueSurvey()))

    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    candidates, progress = queue.Queue(), SurveyProgress(3)
    adapter.configure_autofocus(
        ctrl=type("Ctrl", (), {"core": type("Core", (), {
            "get_position": lambda self: 10.0,
        })()})(), guard=_Guard(), max_exposures=2, z_range_um=2,
        z_step_um=1, method="single_sweep", settle_ms=0, sweep_exposures=3,
    )
    adapter.configure_adaptive(events=_events(3), candidates=candidates,
                               progress=progress, guard=_Guard(), max_events=3)
    adapter.image_process_fn(
        np.zeros((2, 2)),
        {"PositionName": "p0", "XPosition_um_Intended": 0.0,
         "YPosition_um_Intended": 0.0}, object())

    assert candidates.get_nowait()["axes"]["position"] == "p1", (
        "a refused refocus must not swallow the routing action behind it"
    )
    kinds = [(r["action"]["kind"], r["decision"]) for r in adapter._log
             if r.get("event") == "hook_action"]
    assert kinds == [("RequestAutofocus", "refused"), ("ContinueSurvey", "accepted")]


def test_composite_batch_threads_child_results_and_keeps_the_none_guard():
    """A sequenced batch must behave exactly as the same events would one by one.

    Both halves regressed together when the list branch discarded what the
    recursive call returned: a child's replacement event was dropped, and the
    None guard -- which exists because None becomes an empty event that STILL
    FIRES THE CAMERA -- stopped firing for batches only.
    """
    class Replacer:
        def post_hardware_hook_fn(self, event):
            return {**event, "z": 42.0}

    composite = CompositeHook([("replacer", Replacer())], None)
    batch = [{"axes": {"time": 0}}, {"axes": {"time": 1}}]
    returned = composite.post_hardware_hook_fn(batch)
    assert returned is batch
    assert [item["z"] for item in returned] == [42.0, 42.0]

    class Nuller:
        def post_hardware_hook_fn(self, event):
            return None

    nulling = CompositeHook([("nuller", Nuller())], None)
    with pytest.raises(RuntimeError, match="returned None"):
        nulling.post_hardware_hook_fn([{"axes": {"time": 0}}])


def test_set_device_property_parse_requires_string_value_and_exact_shape():
    assert parse_action({"kind": "SetDeviceProperty", "value": "10.0000"}) == SetDeviceProperty("10.0000")
    for malformed in (
        {"kind": "SetDeviceProperty", "value": 10},
        {"kind": "SetDeviceProperty", "value": "10", "device": "Stage"},
        {"kind": "SetDeviceProperty", "value": "10", "property": "Position"},
    ):
        with pytest.raises(ValueError, match="Malformed SetDeviceProperty"):
            parse_action(malformed)


def test_property_actions_apply_and_wait_before_the_separate_verify_pass():
    calls = []

    class Core:
        value = "0"
        def set_property(self, device, prop, value):
            calls.append(("set", value)); self.value = value
        def wait_for_device(self, device): calls.append(("wait", self.value))
        def get_property(self, device, prop): calls.append(("get", self.value)); return self.value
        def get_property_type(self, device, prop): return "Float"
        def set_position(self, device, value): calls.append(("stage-set", value))
        def get_position(self, device): calls.append(("stage-get", device)); return 5

    class Ctrl:
        core = Core()
        authorization_map = None
        def refresh_gui(self): calls.append(("refresh", self.core.value))

    guard = MagicMock()
    adapter = UntrustedHookAdapter(object())
    plan = _axes_plan(({}, (SetDeviceProperty("1"), MoveNamedStage(5))))
    adapter.configure_property(
        ctrl=Ctrl(), guard=guard, device="Camera", property="Gain",
        allowed_values=None, min_value=0, max_value=10, max_writes=1,
        initial_value="0", restore="leave", action_plan=plan,
    )
    adapter.configure_named_stage(
        core=adapter._property_context["core"], guard=guard, device="Stage",
        min_um=0, max_um=10, max_writes=1, initial_value=0,
        restore="leave", action_plan=plan,
    )
    adapter.pre_hardware_hook_fn({"axes": {}})
    assert calls[:3] == [
        ("set", "1"), ("wait", "1"), ("refresh", "1"),
    ]
    assert calls.index(("get", "1")) > calls.index(("stage-get", "Stage"))


def test_property_write_exception_stops_before_next_write_and_before_verify():
    calls = []
    core = MagicMock()
    core.set_property.side_effect = RuntimeError("bridge down")
    core.get_property_type.return_value = "String"
    ctrl = MagicMock(core=core, authorization_map=None)
    adapter = UntrustedHookAdapter(object())
    plan = _axes_plan(({}, (SetDeviceProperty("B"), MoveNamedStage(1))))
    adapter.configure_property(
        ctrl=ctrl, guard=MagicMock(), device="Wheel", property="State",
        allowed_values=("A", "B"), min_value=None, max_value=None,
        max_writes=2, initial_value="A", restore="leave", action_plan=plan,
    )
    adapter.configure_named_stage(
        core=core, guard=MagicMock(), device="Stage", min_um=0, max_um=2,
        max_writes=1, initial_value=0, restore="leave", action_plan=plan,
    )
    with pytest.raises(RuntimeError, match="bridge down"):
        adapter.pre_hardware_hook_fn({"axes": {}})
    assert core.set_property.call_count == 1
    core.set_position.assert_not_called()
    core.get_property.assert_not_called()


def test_property_action_cannot_self_approve_illumination_enable():
    core = MagicMock()
    core.get_focus_device.return_value = "Z"
    core.get_camera_device.return_value = "Camera"
    core.get_xy_stage_device.return_value = "XY"
    ctrl = MagicMock(core=core, authorization_map=None)
    guard = SafetyGuard(SafetyConstraints(illumination=IlluminationConstraints(
        shutters=[IlluminationProperty("Laser", "Enable", "1", "0")],
    )))
    plan = _axes_plan(({}, (SetDeviceProperty("1"),)))
    adapter = UntrustedHookAdapter(object())
    adapter.configure_property(
        ctrl=ctrl, guard=guard, device="Laser", property="Enable",
        allowed_values=("0", "1"), min_value=None, max_value=None,
        max_writes=1, initial_value="0", restore="leave", action_plan=plan,
    )

    with pytest.raises(RuntimeError, match="declined to enable illumination"):
        adapter.pre_hardware_hook_fn({"axes": {}})
    core.set_property.assert_not_called()
