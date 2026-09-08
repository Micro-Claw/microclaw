import queue
from types import SimpleNamespace

import numpy as np
import pytest


def _adapter(decisions):
    from microclaw.hook_decisions import HookResult, UntrustedHookAdapter

    class Hook:
        def __init__(self):
            self.index = 0

        def analyze_frame(self, image, metadata):
            value = decisions[min(self.index, len(decisions) - 1)]
            self.index += 1
            if isinstance(value, BaseException):
                raise value
            return value if isinstance(value, HookResult) else HookResult({}, value)

    return UntrustedHookAdapter(Hook())


def _configured(decisions, cap=4):
    from microclaw.tools import SurveyProgress

    adapter = _adapter(decisions)
    candidates = queue.Queue()
    progress = SurveyProgress(cap)
    seed = {"axes": {"time": 0}}
    adapter.configure_adaptive(
        events=[seed], candidates=candidates, progress=progress,
        guard=SimpleNamespace(), max_events=cap,
        successor=lambda index: {"axes": {"time": index}},
        require_routing_decision=True,
    )
    return adapter, candidates, progress


def _frame(adapter, index):
    return adapter.image_process_fn(
        np.zeros((1, 1), dtype=np.uint16), {"Axes": {"time": index}}, None
    )


def test_1_2_seed_then_each_continue_publishes_one_dense_successor():
    from microclaw.hook_decisions import ContinueAcquisition
    from microclaw.tools import _survey_event_stream

    adapter, candidates, progress = _configured([(ContinueAcquisition(),)] * 4)
    event_queue = queue.Queue()
    acq = SimpleNamespace(
        _event_queue=event_queue,
        _acq=SimpleNamespace(is_finished=lambda: False),
    )
    stream = _survey_event_stream(
        [{"axes": {"time": 0}}], candidates, progress, .1, adapter,
        adaptive=True, max_events=4,
    )(acq)
    assert next(stream)["axes"] == {"time": 0}
    assert candidates.empty(), "only the seed exists before its image decision"
    for index in range(3):
        _frame(adapter, index)
        assert candidates.qsize() == 1
        assert next(stream)["axes"] == {"time": index + 1}
    stream.close()
    assert event_queue.get_nowait() is None


def test_3_property_is_registered_then_applied_and_read_back_before_publication():
    from microclaw.hook_decisions import ContinueAcquisition, SetDeviceProperty

    adapter, candidates, _ = _configured([
        (SetDeviceProperty("B"), ContinueAcquisition()),
    ])
    timeline = []

    class Core:
        value = "A"
        def set_property(self, device, prop, value):
            timeline.append(("set", value)); self.value = value
        def wait_for_device(self, device): timeline.append(("wait", device))
        def get_property(self, device, prop):
            timeline.append(("read", self.value)); return self.value
        def get_property_type(self, device, prop): return "String"

    ctrl = SimpleNamespace(core=Core(), refresh_gui=lambda: timeline.append(("gui",)))
    guard = SimpleNamespace(
        check_device_property=lambda *a, **k: None,
        check_illumination=lambda *a, **k: None,
    )
    adapter.configure_property(
        ctrl=ctrl, guard=guard,
        device="Wheel", property="State", allowed_values=("A", "B"),
        min_value=None, max_value=None, max_writes=1, initial_value="A",
        restore="leave", action_plan=None,
    )
    _frame(adapter, 0)
    event = candidates.get_nowait()
    assert timeline == [], "image callback only registers; it makes no bridge call"
    adapter.pre_hardware_hook_fn(event)
    assert ("gui",) not in timeline, "no GUI refresh between the write and its frame"
    assert timeline == [("set", "B"), ("wait", "Wheel"), ("read", "B")]


def test_4_stop_closes_without_publishing_or_waiting():
    from microclaw.hook_decisions import StopAcquisition

    adapter, candidates, progress = _configured([(StopAcquisition(),)], cap=200000)
    _frame(adapter, 0)
    assert candidates.empty()
    assert progress.survey_complete()
    assert progress.stop_reason == "hook_stop"


def test_hook_stop_wins_when_the_same_frame_also_reaches_the_cap():
    from microclaw.hook_decisions import ContinueAcquisition, StopAcquisition
    from microclaw.tools import _survey_event_stream

    adapter, candidates, progress = _configured(
        [(ContinueAcquisition(),), (StopAcquisition(),)], cap=2,
    )
    acq = SimpleNamespace(
        _event_queue=queue.Queue(),
        _acq=SimpleNamespace(is_finished=lambda: False),
    )
    stream = _survey_event_stream(
        [{"axes": {"time": 0}}], candidates, progress, .1, adapter,
        adaptive=True, max_events=2,
    )(acq)
    next(stream)
    _frame(adapter, 0)
    next(stream)  # records cap_reached as the final authorized event is yielded
    _frame(adapter, 1)  # the later explicit decision must win in the result source
    assert progress.stop_reason == "hook_stop"
    stream.close()


def test_5_cap_closes_handoff_even_when_hook_always_continues():
    from microclaw.hook_decisions import ContinueAcquisition
    from microclaw.tools import _survey_event_stream

    adapter, candidates, progress = _configured([(ContinueAcquisition(),)] * 5, cap=2)
    acq = SimpleNamespace(
        _event_queue=queue.Queue(),
        _acq=SimpleNamespace(is_finished=lambda: False),
    )
    stream = _survey_event_stream(
        [{"axes": {"time": 0}}], candidates, progress, .1, adapter,
        adaptive=True, max_events=2,
    )(acq)
    assert next(stream)["axes"] == {"time": 0}
    _frame(adapter, 0)
    assert next(stream)["axes"] == {"time": 1}
    assert progress.stop_reason == "cap_reached"
    _frame(adapter, 1)
    assert candidates.empty()
    assert adapter._log[-1]["reason"] == (
        "outside committed reservation: all planned frame slots are already dispatched"
    )
    stream.close()


@pytest.mark.parametrize("proposal", [
    (),
    pytest.param("none", id="none"),
    pytest.param("duplicate", id="duplicate"),
    pytest.param(({"kind": "ContinueAcquisition", "extra": 1},), id="malformed"),
])
def test_6_missing_and_malformed_decisions_fail_closed_on_first_image(proposal):
    from microclaw.hook_decisions import ContinueAcquisition, HookResult

    if proposal == "duplicate":
        value = (ContinueAcquisition(), ContinueAcquisition())
    else:
        value = HookResult({}) if proposal == "none" else proposal
    adapter, candidates, progress = _configured([value])
    _frame(adapter, 0)
    assert candidates.empty()
    assert progress.survey_complete()
    assert progress.stop_reason == "routing_refusal"
    assert not any(row.get("event") == "stalled" for row in adapter._log)


def test_6_late_decision_after_handoff_close_is_refused():
    from microclaw.hook_decisions import ContinueAcquisition

    adapter, candidates, _ = _configured([(ContinueAcquisition(),)])
    adapter.close_adaptive_handoff()
    _frame(adapter, 0)
    assert candidates.empty()
    assert adapter._log[-1]["decision"] == "refused"
    assert adapter._log[-1]["reason"] == (
        "adaptive handoff is closed after the final authorized event"
    )


def test_4_successor_guards_coordinates_it_actually_carries():
    from microclaw.hook_decisions import ContinueAcquisition
    from microclaw.tools import SurveyProgress

    checked = []
    adapter = _adapter([(ContinueAcquisition(),)])
    candidates = queue.Queue()
    adapter.configure_adaptive(
        events=[{"axes": {"time": 0}}], candidates=candidates,
        progress=SurveyProgress(2),
        guard=SimpleNamespace(check_z=checked.append), max_events=2,
        successor=lambda index: {"axes": {"time": index}, "z": 12.5},
        require_routing_decision=True,
    )
    _frame(adapter, 0)
    assert checked == [12.5]
    assert candidates.get_nowait()["z"] == 12.5


def test_7_adaptive_stream_structurally_submits_one_event_at_a_time():
    """Parent dispatch publishes at most one candidate for each image decision.

    Whether the engine can batch a singly submitted event is an M2 gate limb,
    not something this fake settles.
    """
    from microclaw.hook_decisions import ContinueAcquisition

    adapter, candidates, _ = _configured([(ContinueAcquisition(),)] * 3)
    for index in range(3):
        _frame(adapter, index)
        assert candidates.qsize() == 1
        candidates.get_nowait()


def test_9_product_successor_preserves_seed_axis_set_and_dense_time(
    monkeypatch, mock_ctrl, unconstrained_guard, tmp_path
):
    from microclaw import tools
    from microclaw.hook_decisions import ContinueAcquisition

    adapter = _adapter([(ContinueAcquisition(),)])
    monkeypatch.setattr(tools, "_resolve_hook", lambda *a, **k: adapter)
    captured = {}
    monkeypatch.setattr(
        tools, "_acquire_survey_with_detector",
        lambda *a, **k: captured.update(k) or {"status": "captured"},
    )
    tools.run_timelapse(
        mock_ctrl, unconstrained_guard, n_frames=None, max_frames=5,
        interval_s=0, save_dir=str(tmp_path), hook_strategy="saved",
        channel="DAPI",
    )
    seed = captured["adaptive_events"][0]
    successor = captured["adaptive_successor"]
    events = [seed] + [successor(i) for i in range(1, 5)]
    axes = [event["axes"] for event in events]
    assert all(set(axis) == set(axes[0]) == {"time", "channel"} for axis in axes)
    assert [axis["channel"] for axis in axes] == ["DAPI"] * 5
    assert [axis["time"] for axis in axes] == list(range(5))
    assert len({tuple(a.items()) for a in axes}) == 5


def test_12_shape_refusals_precede_paths_events_and_hardware(monkeypatch):
    from microclaw import tools

    touched = []
    guard = SimpleNamespace(resolve_in_workspace=lambda p: touched.append("path"))
    monkeypatch.setattr(tools, "_build_acquisition_events",
                        lambda **k: touched.append("events"))
    common = dict(ctrl=object(), guard=guard, interval_s=0, save_dir="x")
    with pytest.raises(ValueError, match="exactly one"):
        tools.run_timelapse(n_frames=None, **common)
    with pytest.raises(ValueError, match="requires hook_strategy"):
        tools.run_timelapse(n_frames=None, max_frames=3, **common)
    with pytest.raises(ValueError, match="incompatible"):
        tools.run_timelapse(n_frames=None, max_frames=3, hook_strategy="h",
                            hook_action_plan=[], **common)
    assert touched == []


def test_13_cap_accounting_and_measured_runtime_allowance_are_separate(
    monkeypatch, mock_ctrl, unconstrained_guard, tmp_path,
):
    from microclaw import tools
    from microclaw.hook_decisions import ContinueAcquisition

    adapter = _adapter([(ContinueAcquisition(),)] * 5)
    monkeypatch.setattr(tools, "_resolve_hook", lambda *a, **k: adapter)
    mock_ctrl.core.get_image_width.return_value = 10
    mock_ctrl.core.get_image_height.return_value = 20
    mock_ctrl.core.get_bytes_per_pixel.return_value = 2
    captured_plans, runtime_inputs, progress_events = [], [], []

    class Reservation:
        def __init__(self, plan):
            self.plan, self.completed_frames, self.overrun_frames = plan, 0, 0
            self.closed = 0
        @property
        def has_overrun(self): return self.overrun_frames > 0
        def commit_frame(self):
            if self.completed_frames >= self.plan.frames:
                self.overrun_frames += 1
                return False
            self.completed_frames += 1
            return True
        def close(self): self.closed += 1

    def authorize(_ctrl, _guard, plan):
        captured_plans.append(plan)
        return Reservation(plan)

    class Acquisition:
        _dataset_disk_location = "/data/adaptive"
        _exception = None
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._event_queue = queue.Queue()
            self._acq = SimpleNamespace(is_finished=lambda: False)
        def acquire(self, events):
            for event in events:
                callback = self.kwargs.get("pre_hardware_hook_fn")
                if callback: callback(event)
                self.kwargs["image_process_fn"](
                    np.zeros((1, 1), dtype=np.uint16),
                    {"Axes": dict(event["axes"])}, None,
                )
                self.kwargs["image_saved_fn"](event["axes"], None)
        def __exit__(self, *args): pass

    real_ceiling = tools._runtime_ceiling_s
    monkeypatch.setattr(tools, "_authorize_acquisition", authorize)
    monkeypatch.setattr(tools, "Acquisition", Acquisition)
    monkeypatch.setattr(
        tools, "_runtime_ceiling_s",
        lambda plan, policy: runtime_inputs.append(plan) or real_ceiling(plan, policy),
    )
    prior = getattr(tools._ACQUISITION_EVENT_CONTEXT, "sink", None)
    tools._ACQUISITION_EVENT_CONTEXT.sink = progress_events.append
    try:
        result = tools.run_timelapse(
            mock_ctrl, unconstrained_guard, n_frames=None, max_frames=37,
            interval_s=0, save_dir=str(tmp_path), hook_strategy="saved",
            exposure_ms=5,
        )
    finally:
        tools._ACQUISITION_EVENT_CONTEXT.sink = prior
    assert result["frames_planned"] == 37
    assert result["frames_acquired"] == result["frames_exposed"] == 37
    assert result["stop_reason"] == "cap_reached"
    assert len(captured_plans) == 1
    assert captured_plans[0].frames == 37
    assert captured_plans[0].illuminated_ms == 185
    assert captured_plans[0].estimated_bytes == 37 * 10 * 20 * 2
    assert len(runtime_inputs) == 2
    runtime_plan = runtime_inputs[0]
    assert runtime_inputs[1] is runtime_plan
    assert runtime_plan is not captured_plans[0]
    assert runtime_plan.frames == 37
    assert runtime_plan.exposure_ms_per_frame == 5
    assert runtime_plan.estimated_bytes == captured_plans[0].estimated_bytes
    assert runtime_plan.estimated_duration_s == pytest.approx(18.685)
    assert runtime_plan.software_allowance_s_per_frame == 0.5
    assert "n=1 from M2" in runtime_plan.software_allowance_evidence
    assert real_ceiling(runtime_plan, tools.DEFAULT) == (
        pytest.approx(318.685), False, "plan_plus_300_s",
    )
    assert captured_plans[0].software_allowance_s_per_frame == 0
    assert captured_plans[0].software_allowance_evidence is None
    assert result["runtime_bound_plan"]["estimated_duration_s"] == pytest.approx(18.685)
    assert result["runtime_bound_plan"]["runtime_ceiling_s"] == pytest.approx(318.685)
    assert result["runtime_bound_plan"]["fallback_ceiling"] is False
    assert "n=1 from M2" in result["runtime_bound_plan"]["software_allowance_evidence"]
    assert progress_events[-1]["frames_planned"] == 37


def test_adaptive_runtime_allowance_reproduces_m2_100k_frame_sanity_check():
    from microclaw import tools
    from microclaw.acquisition import AcquisitionPlan

    accounting = AcquisitionPlan(
        frames=100_000, exposure_ms_per_frame=50,
        estimated_duration_s=5_000, estimated_bytes=1,
    )
    runtime = tools._adaptive_timelapse_runtime_plan(accounting, interval_s=0)
    assert accounting.estimated_duration_s == 5_000
    assert accounting.software_allowance_s_per_frame == 0
    assert runtime.estimated_duration_s == 55_000
    assert tools._runtime_ceiling_s(runtime, policy=tools.DEFAULT) == (82_500, False, "plan_times_1_5")


def test_decision_contract_preflight_uses_pinned_source_scan(tmp_path, monkeypatch):
    import microclaw.hook_manager as manager

    hooks = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks)
    monkeypatch.setattr(manager, "MANIFEST", hooks / "manifest.json")
    manager.save_hook(
        "logger",
        "class Logger:\n    def analyze_frame(self, image, metadata):\n"
        "        return None\n",
        "logger", "user_provided",
    )
    with pytest.raises(ValueError, match="pinned hook source"):
        manager.load_hook_class("logger", require_acquisition_decision=True)


def test_adaptive_artifact_budget_refusal_matches_fixed_error_shape(
    monkeypatch, mock_ctrl, unconstrained_guard, tmp_path
):
    from microclaw import tools
    from microclaw.hook_decisions import ContinueAcquisition

    adapter = _adapter([(ContinueAcquisition(),)])
    adapter.can_emit_artifacts = True
    monkeypatch.setattr(tools, "_resolve_hook", lambda *a, **k: adapter)
    result = tools.run_timelapse(
        mock_ctrl, unconstrained_guard, n_frames=None, max_frames=2,
        interval_s=0, save_dir=str(tmp_path), hook_strategy="saved",
    )
    assert set(result) == {"error"}
    assert "artifact_limits" in result["error"]


def test_survey_result_does_not_gain_timelapse_accounting_or_cadence_keys(
    monkeypatch, mock_ctrl, unconstrained_guard, tmp_path
):
    from microclaw import tools
    from microclaw.hook_decisions import ContinueAcquisition

    class Reservation:
        has_overrun = False
        overrun_frames = 0
        completed_frames = 0
        def __init__(self, plan): self.plan = plan
        def close(self): pass

    adapter = _adapter([(ContinueAcquisition(),)])
    monkeypatch.setattr(tools, "_authorize_acquisition",
                        lambda _c, _g, plan: Reservation(plan))
    monkeypatch.setattr(tools, "_acquire_with_hooks",
                        lambda *a, **k: "/data/survey")
    result = tools._acquire_survey_with_detector(
        mock_ctrl, unconstrained_guard,
        [{"name": "p0", "x_um": 1.0, "y_um": 2.0}],
        str(tmp_path), "survey", adapter, tools.SurveyProgress(1), queue.Queue(),
        adaptive=True, num_time_points=1, time_interval_s=0,
    )
    assert not ({"frames_planned", "frames_acquired", "frames_exposed",
                 "inter_frame_gap_summary"} & set(result))


def test_fail_closed_first_image_is_an_error_result_with_routing_reason(
    monkeypatch, mock_ctrl, unconstrained_guard, tmp_path
):
    from microclaw import tools

    adapter = _adapter([()])
    monkeypatch.setattr(tools, "_resolve_hook", lambda *a, **k: adapter)

    class Reservation:
        has_overrun = False
        overrun_frames = 0
        def __init__(self, plan): self.plan, self.completed_frames = plan, 0
        def commit_frame(self): self.completed_frames += 1; return True
        def close(self): pass

    monkeypatch.setattr(tools, "_authorize_acquisition",
                        lambda _c, _g, plan: Reservation(plan))
    def acquire(_g, _d, _n, _events, hook, reservation, **kwargs):
        hook.image_process_fn(
            np.zeros((1, 1), dtype=np.uint16), {"Axes": {"time": 0}}, None
        )
        reservation.commit_frame()
        return "/data/fail-closed"
    monkeypatch.setattr(tools, "_acquire_with_hooks", acquire)
    result = tools.run_timelapse(
        mock_ctrl, unconstrained_guard, n_frames=None, max_frames=10,
        interval_s=0, save_dir=str(tmp_path), hook_strategy="saved",
    )
    assert result["stop_reason"] == "routing_refusal"
    assert result["status"] == "Adaptive acquisition failed closed."
    assert "error" in result
    assert result["frames_acquired"] == result["frames_exposed"] == 1


def test_8_typed_acquisition_failure_reaches_the_tool_boundary(
    monkeypatch, mock_ctrl, unconstrained_guard, tmp_path
):
    from microclaw import tools
    from microclaw.hook_decisions import ContinueAcquisition

    adapter = _adapter([(ContinueAcquisition(),)])
    monkeypatch.setattr(tools, "_resolve_hook", lambda *a, **k: adapter)
    failure = tools.AcquisitionUnterminated(
        dataset_path="/partial", frames_planned=3, frames_accounted=1,
        engine_exception=RuntimeError("engine"), camera_sequence_running=False,
        teardown_running=True, expired_bound="runtime_ceiling", bound_s=86400,
        fallback_ceiling=True,
    )
    monkeypatch.setattr(
        tools, "_acquire_survey_with_detector",
        lambda *a, **k: (_ for _ in ()).throw(failure),
    )
    with pytest.raises(tools.AcquisitionUnterminated) as caught:
        tools.run_timelapse(
            mock_ctrl, unconstrained_guard, n_frames=None, max_frames=3,
            interval_s=0, save_dir=str(tmp_path), hook_strategy="saved",
        )
    assert caught.value is failure


def test_10_export_executes_the_successor_program_one_event_at_a_time(
    tmp_path, monkeypatch
):
    import re
    import microclaw.hook_manager as manager
    from microclaw.hook_manager import save_hook
    from tests.test_session_script_export import call, export

    hooks = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks)
    monkeypatch.setattr(manager, "MANIFEST", hooks / "manifest.json")
    save_hook(
        "adaptive_time",
        "from microclaw.hook_decisions import ContinueAcquisition, HookResult, StopAcquisition\n"
        "class AdaptiveTime:\n"
        "    def analyze_frame(self, image, metadata):\n"
        "        i = metadata['Axes']['time']\n"
        "        return HookResult({}, (ContinueAcquisition(),) if i < 2 else (StopAcquisition(),))\n",
        "adaptive time", "user_provided",
    )
    _, result, source = export(tmp_path, [call("run_timelapse", {
        "n_frames": None, "max_frames": 5, "interval_s": 0,
        "save_dir": "session", "name": "adaptive-time",
        "hook_strategy": "adaptive_time",
    })])
    assert result["emitted_calls"] == 1
    assert "def _successor(index):" in source
    assert "num_time_points': None" not in source
    seen = []

    class Core:
        pass

    class Acquisition:
        def __init__(self, **kwargs):
            self.hooks = kwargs
            self._event_queue = queue.Queue()
            self._acq = SimpleNamespace(is_finished=lambda: False)
            self.events = None
        def __enter__(self): return self
        def acquire(self, events): self.events = events
        def __exit__(self, *args):
            for event in self.events:
                seen.append(dict(event["axes"]))
                callback = self.hooks.get("pre_hardware_hook_fn")
                if callback: callback(event)
                self.hooks["image_process_fn"](
                    np.zeros((1, 1), dtype=np.uint16),
                    {"Axes": dict(event["axes"])}, None,
                )

    runnable = re.sub(r"^from pycromanager import .*$", "", source, flags=re.M)
    exec(compile(runnable, "adaptive.py", "exec"), {
        "__file__": str(tmp_path / "adaptive.py"), "Core": Core,
        "Acquisition": Acquisition,
        "multi_d_acquisition_events": lambda **k: [{"axes": {"time": 0}}],
    })
    assert seen == [{"time": 0}, {"time": 1}, {"time": 2}]


def test_11_existing_export_routes_remain_distinct(tmp_path, monkeypatch):
    import microclaw.hook_manager as manager
    from microclaw.hook_manager import save_hook
    from tests.test_session_script_export import call, export

    _, _, plain = export(tmp_path, [call("run_timelapse", {
        "n_frames": 2, "interval_s": 0, "save_dir": "session",
    })])
    assert "acq.acquire(events)" in plain
    assert "_survey_event_stream" not in plain

    hooks = tmp_path / "hooks"
    monkeypatch.setattr(manager, "HOOKS_DIR", hooks)
    monkeypatch.setattr(manager, "MANIFEST", hooks / "manifest.json")
    save_hook(
        "logger", "class Logger:\n"
        "    def analyze_frame(self, image, metadata):\n        return None\n",
        "logger", "user_provided",
    )
    _, _, fixed_hooked = export(tmp_path, [call("run_timelapse", {
        "n_frames": 2, "interval_s": 1, "save_dir": "session",
        "hook_strategy": "logger",
    })])
    assert "acq.acquire(events)" in fixed_hooked
    assert "def _successor(index):" not in fixed_hooked


def test_measured_gap_distribution_is_bounded_in_progress_and_caller_state(monkeypatch):
    from microclaw import tools
    from microclaw.acquisition import AcquisitionPlan

    class Acquisition:
        _dataset_disk_location = "/data/cadence"
        _exception = None
        def __init__(self, **kwargs): self.kwargs = kwargs
        def acquire(self, events):
            for event in events: self.kwargs["image_saved_fn"](event["axes"], None)
        def __exit__(self, *args): pass

    ticks = iter([0, 0, 0.1, 0.4, 0.9, 1.0, 1.1, 1.2, 1.3])
    monkeypatch.setattr(tools, "Acquisition", Acquisition)
    monkeypatch.setattr(tools, "_acquisition_monotonic", lambda: next(ticks))
    hook = SimpleNamespace()
    cadence = tools._new_gap_summary()
    events = [{"axes": {"time": i}} for i in range(3)]
    plan = AcquisitionPlan(3, 1, 1, 3)
    received = []
    prior = getattr(tools._ACQUISITION_EVENT_CONTEXT, "sink", None)
    tools._ACQUISITION_EVENT_CONTEXT.sink = received.append
    try:
        tools._acquire_with_hooks(
            SimpleNamespace(resolve_in_workspace=lambda p: p), "/data", "cadence",
            events, hook, ctrl=SimpleNamespace(core=SimpleNamespace()), plan=plan,
            runtime_plan=plan, cadence_summary=cadence,
            policy=tools.DEFAULT,
        )
    finally:
        tools._ACQUISITION_EVENT_CONTEXT.sink = prior
    summary = received[-1]["inter_frame_gap_summary"]
    assert summary == tools._gap_summary_payload(cadence)
    assert summary["count"] == 2
    assert set(summary) == {"count", "min_s", "mean_s", "median_le_s", "p95_le_s",
                            "max_s", "histogram"}
    # The allowance design/65 §Teardown asks M2 for is sized off exact numbers;
    # the two percentiles are only the bin upper bound and say so in their
    # names, so mean_s must be the exact arithmetic mean of the two gaps.
    assert summary["mean_s"] == pytest.approx(
        (summary["min_s"] + summary["max_s"]) / 2, rel=1e-9
    )
    assert len(summary["histogram"]) == len(tools._GAP_HISTOGRAM_UPPER_S)
    assert len(summary["histogram"]) == 23
    assert [row["upper_s"] for row in summary["histogram"][8:14]] == [
        0.25, 0.3, 0.35, 0.4, 0.45, 0.5,
    ]
    assert not hasattr(hook, "_measured_inter_frame_gaps_s")


def test_gap_histogram_resolves_the_measured_m2_cadence_regime():
    from microclaw import tools

    summary = tools._new_gap_summary()
    # M2 measured 99 gaps between 0.219 and 0.344 s. Keep a distribution in
    # that same regime so collapsing 0.2 -> 0.5 makes both bounds fail here.
    for gap in [0.219] * 49 + [0.25] * 45 + [0.344] * 5:
        tools._record_gap(summary, gap)
    payload = tools._gap_summary_payload(summary)
    assert payload["count"] == 99
    assert payload["median_le_s"] == 0.25
    assert payload["p95_le_s"] == 0.35
    assert payload["min_s"] == 0.219
    assert payload["max_s"] == 0.344
