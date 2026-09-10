"""Design/81: required autofocus must prevent the planned exposure."""
from types import SimpleNamespace

import pytest

from microclaw.hooks import AutofocusHook
from microclaw.safety import SafetyViolation


@pytest.mark.parametrize("failure", ["runtime_bounds", "nonconverged", "motion"])
def test_required_autofocus_raises_instead_of_returning_exposable_event(failure):
    def check(z):
        if z < 0:
            raise SafetyViolation("below minimum")
    hook = AutofocusHook(SimpleNamespace(core=SimpleNamespace(get_position=lambda: 1)),
                         SimpleNamespace(check_z=check), 4 if failure == "runtime_bounds" else 1, .25)
    def focus(*args, **kwargs):
        if failure == "motion":
            raise RuntimeError("motion failed")
        return SimpleNamespace(final_z_um=1, converged=False, reason="flat curve",
                               coarse=None, fine=None, final_commanded_z_um=1)
    hook._autofocus_fn = focus
    event = {"axes": {"position": "field_B", "time": 0}}
    with pytest.raises(SafetyViolation, match="field_B"):
        # Returning either the event or None permits a camera exposure over the bridge.
        hook.post_hardware_hook_fn(event)


@pytest.mark.parametrize("boundary", ["window", "guard"])
@pytest.mark.parametrize("backend", ["live", "standalone"])
@pytest.mark.parametrize("route", ["direct", "single", "coarse", "fine"])
def test_selected_measured_coordinate_outside_window_is_never_dispatched(monkeypatch, route, backend, boundary, hooked_engine, tmp_path):
    from microclaw import autofocus as af
    namespace = None
    if backend == "standalone":
        from tests.test_session_script_export import Guard, call
        result = tools.export_session_script(None, Guard(tmp_path), "targets.py", [call(
            "run_multiposition_acquisition", {
                "positions": [{"name": "a", "x_um": 0, "y_um": 0, "z_um": 3}],
                "protocol": "timelapse", "protocol_params": {"n_frames": 1, "interval_s": 0},
                "hook_strategy": "autofocus_per_position",
                "hook_params": {"z_range_um": 4, "z_step_um": .25, "settle_ms": 0},
                "save_dir": str(tmp_path),
            })])
        assert result["complete"], result
        namespace = hooked_engine.execute((tmp_path / "targets.py").read_text(encoding="utf-8"))
        af = SimpleNamespace(**namespace)
    def patch(name, value):
        monkeypatch.setattr(af, name, value)
        if namespace is not None:
            monkeypatch.setitem(namespace, name, value)
    forbidden = 2. if boundary == "window" else .5
    commands = []
    core = SimpleNamespace(get_position=lambda: 0., get_focus_device=lambda: "Z",
                           set_position=lambda z: commands.append(z))
    ctrl = SimpleNamespace(core=core)
    if boundary == "guard":
        def check(z):
            if z > .25:
                raise (namespace["SafetyViolation"] if namespace is not None else SafetyViolation)(
                    "selected target exceeds guard")
        ctrl._guard = SimpleNamespace(check_z=check, stage_move_tolerance=lambda *a, **k: None)
    patch("read_stage_start_position", lambda *a: 0.)
    patch("settle_stage_move", lambda *a: {
        "measured_um": forbidden if a[2] == 0 else a[2], "arrival_unverifiable": False})
    probe = af.FocusProbe(lambda: 100. if commands[-1] == 0 else 1.,
                         lambda readings: 1, lambda *a: None, 0, "test")
    if route == "direct":
        result = af.sweep_autofocus(ctrl, -1, 1, 1, settle_ms=0, probe=probe)
    else:
        calls = []
        def sweep(*args, **kwargs):
            calls.append(1)
            selected = 0. if route == "fine" and len(calls) == 1 else forbidden
            return af.SweepResult([-1., 0., 1.], [1., 100., 1.], selected,
                                  True, [-1., selected, 1.])
        patch("sweep_autofocus", sweep)
        result = (af.single_sweep_autofocus(ctrl, 2, 1, probe=probe) if route == "single"
                  else af.coarse_then_fine_autofocus(ctrl, 2, 1, 1, probe=probe))
    assert forbidden not in commands, "forbidden selected reading was dispatched"
    if route != "direct":
        assert not result.converged
    else:
        assert result.refusal_reason


@pytest.mark.parametrize("shape", ["degenerate_incident", "two_plane", "no_event_z"])
def test_unsafe_nominal_reach_constructs_zero_acquisitions(tmp_path, monkeypatch, shape):
    _assert_nominal_refusal(tmp_path, monkeypatch, shape)


def _assert_nominal_refusal(tmp_path, monkeypatch, shape, z_range=20, reason="below minimum"):
    from unittest.mock import MagicMock
    from microclaw import tools
    from microclaw.acquisition import AcquisitionPlan, AcquisitionLedger
    ctrl, guard = MagicMock(), MagicMock()
    ctrl.core.get_position.return_value = 1.
    guard.resolve_in_workspace.side_effect = lambda path: path
    def check(z):
        if z < 0:
            raise SafetyViolation("below minimum")
    guard.check_z.side_effect = check
    monkeypatch.setattr(tools, "_configure_hook_capabilities", lambda *a, **k: None)
    monkeypatch.setattr(tools, "plan_events", lambda *a, **k: AcquisitionPlan(2, 1, 1, 1))
    monkeypatch.setattr(tools, "_authorize_acquisition",
                        lambda ctrl, guard, plan: AcquisitionLedger().reserve(guard, plan))
    acquisition = MagicMock(side_effect=AssertionError("Acquisition constructed"))
    monkeypatch.setattr(tools, "Acquisition", acquisition)
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *a: None)
    params = ({"z_start_um": 0, "z_end_um": 0, "z_step_um": 1}
              if shape == "degenerate_incident" else
              {"z_start_um": 1, "z_end_um": 2, "z_step_um": 1}
              if shape == "two_plane" else {"n_frames": 1, "interval_s": 0})
    import json
    result = json.loads(tools.execute_tool("run_multiposition_acquisition", {
        "positions": [{"name": f"r{r}_c{c}", "x_um": c, "y_um": r}
                      for r in range(3) for c in range(3)],
        "protocol": "timelapse" if shape == "no_event_z" else "zstack",
        "protocol_params": params, "save_dir": str(tmp_path),
        "hook_strategy": "autofocus_per_position",
        "hook_params": {"z_range_um": z_range, "z_step_um": 1},
    }, ctrl, guard))
    assert acquisition.call_count == 0, result
    assert "error" in result, result
    if shape != "degenerate_incident":
        assert reason in result["error"]


@pytest.mark.parametrize("reset", [False, True])
def test_reach_composes_callbacks_and_inherits_possible_feedback_jogs(reset):
    from microclaw.hooks import planned_hook_z_reach, FocusFeedbackHook
    from microclaw.hook_decisions import CompositeHook
    checks = []
    guard = SimpleNamespace(check_z=checks.append)
    af = AutofocusHook(None, guard, 4, 1)
    feedback = FocusFeedbackHook(None, guard, z_step_um=1, max_jogs=3)
    hook = CompositeHook([("feedback", feedback), ("focus", af)], None)
    events = [{"z": 10}, {"z": 10} if reset else {}]
    result = planned_hook_z_reach(hook, events, 100, guard)
    # Post hardware autofocus precedes the image callback even when listed second.
    assert checks[:4] == [8, 12, 8, 15]
    assert result["exit_z_interval_um"] == ((8, 15) if reset else (6, 20))
    assert result["checked_hooks"] == ["0:feedback", "1:focus"]


def test_reach_absent_contract_is_not_reported_checked_and_reads_nothing():
    from microclaw.hooks import planned_hook_z_reach
    def unexpected():
        raise AssertionError("unnecessary entry read")
    result = planned_hook_z_reach(object(), [], unexpected, None)
    assert result["checked_hooks"] == []
    assert result["unchecked_hooks"]


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_reach_refuses_invalid_parameters(value, tmp_path, monkeypatch):
    _assert_nominal_refusal(tmp_path, monkeypatch, "no_event_z", z_range=value,
                            reason="finite and non-negative")


def test_empty_event_list_still_checks_entry_window(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_build_acquisition_events", lambda **kwargs: [])
    _assert_nominal_refusal(tmp_path, monkeypatch, "no_event_z")


@pytest.mark.parametrize("rows,counts,positions", [
    ([{"position": "a", "autofocus": "skipped", "reason": "outside"}] * 2,
     {"skipped": 2}, 1),
    ([{"position": "a", "best_z_um": 2, "converged": True}], {"converged": 1}, 1),
    ([{"position": "a", "best_z_um": 2}], {"unknown": 1}, 1),
    ([{"position": "a", "converged": True}, {"position": "b", "converged": False},
      {"position": "a", "autofocus": "skipped"}],
     {"converged": 1, "non_converged": 1, "skipped": 1}, 2),
])
def test_historical_outcomes_distinguish_events_positions_and_unknown(rows, counts, positions):
    from microclaw.tools import _autofocus_outcomes
    result = _autofocus_outcomes(rows)
    assert result["event_count"] == len(rows)
    assert result["unique_position_count"] == positions
    assert {k: v for k, v in result["counts"].items() if v} == counts
    if counts == {"skipped": len(rows)}:
        assert "autofocus was not performed" in result["status"]


from microclaw import tools


@pytest.mark.parametrize("name,followup", [(name, followup)
    for name, fn in tools.TOOL_REGISTRY.items()
    if getattr(fn, "_microclaw_acquisition_entry_point", False) and name != "run_mda"
    for followup in ([False, True] if name == "run_adaptive_survey" else [False])])
@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_required_failure_survives_every_acquisition_boundary(name, followup, cleanup_failure, tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from microclaw.acquisition import AcquisitionLedger
    import json
    ctrl, guard = MagicMock(), MagicMock()
    guard.resolve_in_workspace.side_effect = lambda path: path
    ctrl.core.get_position.return_value = 10.
    ctrl.set_xy.return_value = {}
    monkeypatch.setattr(tools, "_single_run_timing", lambda **kwargs: {"strategy": "no_time_axis"})
    for method in ("get_image_width", "get_image_height", "get_bytes_per_pixel", "get_exposure"):
        getattr(ctrl.core, method).return_value = 1
    ctrl.core.is_sequence_running.return_value = False
    ctrl.core.get_x_position.return_value = ctrl.core.get_y_position.return_value = 0.
    hook = AutofocusHook(ctrl, guard, 2, 1)
    from microclaw.autofocus import SweepResult
    hook._autofocus_fn = lambda *a, **k: SimpleNamespace(
        converged=False, final_z_um=10., reason="flat field", coarse=SweepResult([9., 10., 11.], [1., 1., 1.], 9., False, [9., 10., 11.]),
        fine=None, final_commanded_z_um=10.)
    monkeypatch.setattr(tools, "_resolve_hook", lambda *a, **k: hook)
    monkeypatch.setattr(tools, "_resolve_hooks", lambda *a, **k: hook)
    monkeypatch.setattr(tools, "_configure_hook_capabilities", lambda *a, **k: None)
    monkeypatch.setattr(tools, "_prepare_log_path", lambda *a, **k: None)
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *a: None)
    reservations = []
    def reserve(ctrl, guard, plan):
        reservation = AcquisitionLedger().reserve(guard, plan)
        reservations.append(reservation)
        return reservation
    monkeypatch.setattr(tools, "_authorize_acquisition", reserve)
    typed_failures = []
    report_failure = tools._hooked_failure_result
    def report_typed_failure(exc, log_path):
        typed_failures.append(exc.__cause__)
        return report_failure(exc, log_path)
    monkeypatch.setattr(tools, "_hooked_failure_result", report_typed_failure)
    constructed = []
    exposed = []
    class Backend:
        _exception = None
        _dataset_disk_location = str(tmp_path / "partial")
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._event_queue = MagicMock()
            constructed.append(self)
        def acquire(self, events):
            self.events = events
        def __exit__(self, *exc):
            event = {"axes": {"position": "field_B"}}
            callback = self.kwargs.get("post_hardware_hook_fn")
            returned = callback(event) if callback is not None else event
            exposed.append(returned or {})  # pyjavaz turns None into an exposable empty event
            return None
    class Acquisition:
        def __new__(cls, **kwargs):
            return Backend(**kwargs)
    monkeypatch.setattr(tools, "Acquisition", Acquisition)
    positions = [{"name": f"p{i}", "x_um": i, "y_um": 0} for i in range(3)]
    common = {"save_dir": str(tmp_path), "hook_strategy": "autofocus_per_position"}
    protocol = {"protocol": "timelapse", "protocol_params": {"n_frames": 1, "interval_s": 1}}
    inputs = {
        "run_zstack": {**common, "z_start_um": 10, "z_end_um": 11, "z_step_um": 1},
        "run_timelapse": {**common, "n_frames": 2, "interval_s": 1},
        "run_multiposition_acquisition": {**common, **protocol, "positions": positions},
        "run_tile_acquisition": {**common, **protocol, "rows": 1, "cols": 3, "step_um": 1},
        "run_multiposition_with_autofocus": {"save_dir": str(tmp_path), **protocol,
            "positions": positions, "z_range_um": 2, "z_step_um": 1},
        "run_adaptive_survey": {**common, **protocol, "positions": positions},
    }
    if followup:
        from microclaw.hook_decisions import UntrustedHookAdapter
        required_focus = hook
        hook = UntrustedHookAdapter(SimpleNamespace(analyze_frame=lambda image, metadata: None))
        hook.post_hardware_hook_fn = required_focus.post_hardware_hook_fn
        inputs[name]["hook_strategy"] = "saved_focus"
        inputs[name]["protocol_params"]["channel"] = "DAPI"
        inputs[name]["acquire_on_hit"] = {"channel": "DAPI", "protocol": "timelapse",
            "protocol_params": {"n_frames": 1, "interval_s": 0}, "max_hits": 1}
        monkeypatch.setattr(tools, "_check_acquisition_channel", lambda *a, **k: None)
        monkeypatch.setattr(tools, "_set_channel_for_composite", lambda *a, **k: {})
        original_survey = tools._acquire_survey_with_detector
        def survey_with_prior_hit(*args, **kwargs):
            result = original_survey(*args, **kwargs)
            # An earlier field's detector hit remains available when a later
            # required hook fails; drive the real failure and follow-up runner.
            kwargs["acquire_hits"].append({"name": "earlier", "x_um": 0, "y_um": 0, "z_um": 10})
            return result
        monkeypatch.setattr(tools, "_acquire_survey_with_detector", survey_with_prior_hit)
    if cleanup_failure:
        hook.restore_property = MagicMock(side_effect=RuntimeError("cleanup failed"))
    result = json.loads(tools.execute_tool(name, inputs[name], ctrl, guard))
    assert len(constructed) == 1, result
    assert exposed == [], "planned image exposed after required autofocus failure"
    assert typed_failures and all(isinstance(exc, SafetyViolation) for exc in typed_failures)
    assert "error" in result, "autofocus returned an exposable event instead of stopping"
    assert "field_B" in result["error"] and "flat field" in result["error"], result
    if cleanup_failure:
        assert "cleanup failed" in result["error"]
    assert all(not r.ledger.in_flight for r in reservations)


from tests.test_session_script_export import hooked_engine


@pytest.mark.parametrize("failure", ["runtime_drift", "partial_sweep"])
def test_supervised_failure_preserves_saved_fields_and_partial_hook_dose(
    hooked_engine, tmp_path, failure,
):
    engine = hooked_engine
    engine.guard._c.stage.z_min = 0
    engine.guard._c.stage.z_max = 6
    ctrl = SimpleNamespace(core=engine.core, _guard=engine.guard)
    original_move = engine.core.set_position
    original_read = engine.core.get_tagged_image
    def move(z):
        original_move(z)
        if failure == "runtime_drift" and engine.core.xy[0] == 10:
            engine.core.z = 6
    def read():
        if failure == "partial_sweep" and engine.core.xy[0] == 10:
            raise RuntimeError("image read failed after snap")
        return original_read()
    engine.core.set_position = move
    engine.core.get_tagged_image = read
    result = tools._acquire_positions_with_hook(
        ctrl, engine.guard,
        [{"name": f"field_{i}", "x_um": i * 10, "y_um": 0, "z_um": 3}
         for i in range(3)], str(tmp_path), "partial", "autofocus_per_position", {"strategy": "no_time_axis"},
        hook_params={"z_range_um": 4, "z_step_um": .25, "settle_ms": 0},
        num_time_points=1, time_interval_s=0,
    )
    assert len(engine.core.captures) == 1, "planned image exposed after required autofocus failure"
    assert "field_1" in result["error"]
    assert len(engine.backends) == 1
    assert len(engine.core.captures) == result["frames_acquired"] == 1
    assert result["fields_with_saved_frames"] == ["field_0"]
    assert result["hook_exposures_observed"] == len(engine.core.probes)
    assert all(capture[0]["position"] == "field_0" for capture in engine.core.captures)
    if failure == "partial_sweep":
        assert "image read failed after snap" in result["error"]
        assert engine.core.z == 3  # checked restoration despite image-read failure


def test_incomplete_delivery_does_not_claim_submitted_frames_saved(hooked_engine, tmp_path, monkeypatch):
    engine = hooked_engine
    ctrl = SimpleNamespace(core=engine.core, _guard=engine.guard)
    class DiscardSecond:
        def image_process_fn(self, image, metadata, queue):
            return (image, metadata) if metadata["Axes"]["time"] == 0 else None
    monkeypatch.setattr(tools, "_resolve_hooks", lambda *a, **k: DiscardSecond())
    result = tools._acquire_positions_with_hook(
        ctrl, engine.guard, [{"name": "a", "x_um": 0, "y_um": 0}],
        str(tmp_path), "incomplete", "discard_second", {"strategy": "no_time_axis"},
        num_time_points=2, time_interval_s=.1, acquisition_order="time_then_position",
    )
    movie = result
    assert movie["frames_acquired"] == 1
    assert movie["frames_planned"] == 2
    assert movie["positions_completed"] == 0
    assert movie["hook_exposures_observed"] is None


def test_target_refusal_remains_named_when_restoration_fails(monkeypatch):
    from microclaw import autofocus as af
    ctrl = SimpleNamespace(core=SimpleNamespace(get_position=lambda: 0., get_focus_device=lambda: "Z"))
    monkeypatch.setattr(af, "sweep_autofocus", lambda *a, **k:
        af.SweepResult([-1., 0., 1.], [1., 100., 1.], 2., True, [-1., 2., 1.]))
    monkeypatch.setattr(af, "read_stage_start_position", lambda *a: 1.)
    def fail(z):
        raise RuntimeError("restore motor failed")
    ctrl.core.set_position = fail
    with pytest.raises(Exception, match="Selected measured Z.*outside sweep window.*restoration also failed"):
        af.single_sweep_autofocus(ctrl, 2, 1)
