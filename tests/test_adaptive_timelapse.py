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
    assert timeline[:3] == [("set", "B"), ("wait", "Wheel"), ("gui",)]
    assert timeline[3:] == [("read", "B"), ("read", "B")]


def test_4_stop_closes_without_publishing_or_waiting():
    from microclaw.hook_decisions import StopAcquisition

    adapter, candidates, progress = _configured([(StopAcquisition(),)], cap=200000)
    _frame(adapter, 0)
    assert candidates.empty()
    assert progress.survey_complete()


def test_5_cap_closes_handoff_even_when_hook_always_continues():
    from microclaw.hook_decisions import ContinueAcquisition

    adapter, candidates, _ = _configured([(ContinueAcquisition(),)] * 5, cap=2)
    _frame(adapter, 0)
    assert candidates.get_nowait()["axes"] == {"time": 1}
    adapter.close_adaptive_handoff()
    _frame(adapter, 1)
    assert candidates.empty()


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
    assert not any(row.get("event") == "stalled" for row in adapter._log)


def test_7_adaptive_stream_structurally_submits_one_event_at_a_time():
    """No interval_s=0 refusal: the candidate queue contains at most one event."""
    from microclaw.hook_decisions import ContinueAcquisition

    adapter, candidates, _ = _configured([(ContinueAcquisition(),)] * 3)
    for index in range(3):
        _frame(adapter, index)
        assert candidates.qsize() == 1
        candidates.get_nowait()


def test_9_successor_axes_are_dense_and_unique():
    from microclaw.hook_decisions import ContinueAcquisition

    adapter, candidates, _ = _configured([(ContinueAcquisition(),)] * 5, cap=5)
    axes = [{"time": 0}]
    for index in range(4):
        _frame(adapter, index)
        axes.append(candidates.get_nowait()["axes"])
    assert axes == [{"time": i} for i in range(5)]
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


def test_13_cap_plan_and_fallback_runtime_are_separate(monkeypatch, mock_ctrl,
                                                        unconstrained_guard, tmp_path):
    from microclaw import tools
    from microclaw.hook_decisions import ContinueAcquisition

    adapter = _adapter([(ContinueAcquisition(),)])
    monkeypatch.setattr(tools, "_resolve_hook", lambda *a, **k: adapter)
    captured = {}

    def runner(*args, **kwargs):
        captured.update(kwargs)
        plan = kwargs["accounting_plan"]
        return {"status": "ok", "frames_planned": plan.frames,
                "frames_acquired": 0, "frames_exposed": 0}

    monkeypatch.setattr(tools, "_acquire_survey_with_detector", runner)
    result = tools.run_timelapse(
        mock_ctrl, unconstrained_guard, n_frames=None, max_frames=37,
        interval_s=0, save_dir=str(tmp_path), hook_strategy="saved",
        exposure_ms=5,
    )
    assert result["frames_planned"] == 37
    assert captured["accounting_plan"].frames == 37
    assert captured["accounting_plan"].illuminated_ms == 185
    assert captured["runtime_plan"] is None
    assert tools._runtime_ceiling_s(captured["runtime_plan"]) == (
        tools.FALLBACK_RUNTIME_CEILING_S, True
    )


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


def test_measured_gap_distribution_reaches_progress_sink_and_result_hook(monkeypatch):
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
    events = [{"axes": {"time": i}} for i in range(3)]
    plan = AcquisitionPlan(3, 1, 1, 3)
    received = []
    prior = getattr(tools._ACQUISITION_EVENT_CONTEXT, "sink", None)
    tools._ACQUISITION_EVENT_CONTEXT.sink = received.append
    try:
        tools._acquire_with_hooks(
            SimpleNamespace(resolve_in_workspace=lambda p: p), "/data", "cadence",
            events, hook, ctrl=SimpleNamespace(core=SimpleNamespace()), plan=plan,
            runtime_plan=plan,
        )
    finally:
        tools._ACQUISITION_EVENT_CONTEXT.sink = prior
    assert len(hook._measured_inter_frame_gaps_s) == 2
    assert received[-1]["inter_frame_gaps_s"] == hook._measured_inter_frame_gaps_s
    assert received[-1]["largest_gap_s"] == max(hook._measured_inter_frame_gaps_s)
