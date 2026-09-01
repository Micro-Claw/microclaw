import json
from unittest.mock import MagicMock

import pytest

from microclaw import tools
from microclaw import tools_schema


TIMELAPSE = {"n_frames": 2, "interval_s": 0, "channel": "DAPI",
             "exposure_ms": 5, "laser_slot": 1}
ZSTACK = {"z_start_um": 0, "z_end_um": 2, "z_step_um": 1,
          "channel": "DAPI", "exposure_ms": 5}


@pytest.mark.parametrize(
    ("protocol", "params", "shape"),
    [
        ("timelapse", TIMELAPSE,
         {"num_time_points": 2, "time_interval_s": 0}),
        ("zstack", ZSTACK, {"z_start": 0, "z_end": 2, "z_step": 1}),
        ("snap", {}, {}),
    ],
)
def test_protocol_preflight_accepts_exact_documented_shapes(protocol, params, shape):
    assert tools._protocol_shape_kwargs(protocol, params) == shape


@pytest.mark.parametrize(
    ("protocol", "params", "message"),
    [
        ("timelapse", {"n_frames": 2}, "missing 'interval_s'"),
        ("timelapse", {"interval_s": 0}, "missing 'n_frames'"),
        ("timelapse", {}, "missing 'n_frames', 'interval_s'"),
        ("zstack", {"z_start_um": 0, "z_end_um": 2}, "missing 'z_step_um'"),
        ("zstack", {"z_start_um": 0}, "missing 'z_end_um', 'z_step_um'"),
        ("zstack", {**ZSTACK, "n_frames": 2},
         "timelapse parameters do not apply to a zstack"),
        ("timelapse", {**TIMELAPSE, "z_start_um": 0},
         "zstack parameters do not apply to a timelapse"),
        ("zstack", {**ZSTACK, "laser_slot": 1},
         "incompatible key 'laser_slot'; timelapse parameters"),
        ("zstack", {**ZSTACK, "max_frames": 2},
         "incompatible key 'max_frames'; timelapse parameters"),
        ("zstack", {**ZSTACK, "n_frames": 2, "interval_s": 0, "laser_slot": 1},
         "incompatible keys 'interval_s', 'laser_slot', 'n_frames'; timelapse"),
        ("timelapse", {**TIMELAPSE, "z_start_um": 0, "z_end_um": 2,
                       "z_step_um": 1},
         "incompatible keys 'z_end_um', 'z_start_um', 'z_step_um'; zstack"),
        ("timelapse", {**TIMELAPSE, "max_frames": 2}, "incompatible key 'max_frames'"),
        ("snap", {"exposure_ms": 5}, "'snap' contains incompatible key 'exposure_ms'"),
    ],
)
def test_protocol_preflight_reports_missing_and_incompatible_keys(
    protocol, params, message
):
    with pytest.raises(ValueError, match=message):
        tools._protocol_shape_kwargs(protocol, params)


@pytest.mark.parametrize("key", ("hook_strategy", *tools.HOOK_CAPABILITY_ARGS))
def test_protocol_preflight_keeps_hook_capability_refusal(key):
    with pytest.raises(ValueError, match="cannot carry per-run hook capabilities"):
        tools._protocol_shape_kwargs("timelapse", {**TIMELAPSE, key: "supplied"})


def _call_outer(name, ctrl, guard, params, hooked, tmp_path):
    common = {"protocol": "timelapse", "protocol_params": params}
    if name == "run_tile_acquisition":
        return tools.run_tile_acquisition(
            ctrl, guard, rows=1, cols=1, step_um=1, save_dir=str(tmp_path),
            hook_strategy="probe" if hooked else None, **common,
        )
    if name == "run_multiposition_acquisition":
        return tools.run_multiposition_acquisition(
            ctrl, guard, positions=[{"name": "p", "x_um": 0, "y_um": 0}],
            save_dir=str(tmp_path), hook_strategy="probe" if hooked else None,
            **common,
        )
    if name == "run_multiposition_with_autofocus":
        return tools.run_multiposition_with_autofocus(
            ctrl, guard, positions=[{"name": "p", "x_um": 0, "y_um": 0}],
            z_range_um=2, z_step_um=1, save_dir=str(tmp_path), **common,
        )
    return tools.run_adaptive_survey(
        ctrl, guard, positions=[{"name": "p", "x_um": 0, "y_um": 0}],
        save_dir=str(tmp_path), hook_strategy="probe", **common,
    )


@pytest.mark.parametrize("name", [
    "run_tile_acquisition", "run_multiposition_acquisition",
    "run_multiposition_with_autofocus", "run_adaptive_survey",
])
@pytest.mark.parametrize("hooked", [False, True])
def test_each_outer_boundary_refuses_before_any_effect(
    name, hooked, tmp_path, monkeypatch
):
    ctrl = MagicMock()
    guard = MagicMock()
    effects = MagicMock()
    monkeypatch.setattr(tools, "_preflight_native_positions",
                        lambda *_a, **_k: effects("position_preflight"))
    monkeypatch.setattr(tools, "_plan_protocol_repetitions",
                        lambda *_a, **_k: effects("planning"))
    if name == "run_multiposition_with_autofocus":
        monkeypatch.setattr(
            tools, "run_multiposition_acquisition",
            MagicMock(side_effect=lambda *_a, **_k: effects("forward")),
        )

    result = _call_outer(name, ctrl, guard, {"n_frames": 2}, hooked, tmp_path)

    assert "missing 'interval_s'" in result["error"]
    assert ctrl.mock_calls == []
    assert guard.mock_calls == []
    effects.assert_not_called()


def test_snap_route_reaches_preflight_before_default_tile_center(tmp_path):
    ctrl, guard = MagicMock(), MagicMock()
    result = tools.run_tile_acquisition(
        ctrl, guard, rows=1, cols=1, step_um=1, protocol="snap",
        protocol_params={"exposure_ms": 5}, save_dir=str(tmp_path),
    )
    assert "'snap' contains incompatible key 'exposure_ms'" in result["error"]
    ctrl.core.get_x_position.assert_not_called()
    ctrl.core.get_y_position.assert_not_called()


@pytest.mark.parametrize(("params", "expected"), [
    ({"exposure_ms": 5}, "contains incompatible key 'exposure_ms'"),
    ({"n_frames": 1, "interval_s": 0},
     "contains incompatible keys 'interval_s', 'n_frames'"),
])
def test_snap_multiposition_route_reaches_preflight(params, expected, monkeypatch):
    """Acceptance test 6 names the plain multiposition planning path, which is
    guarded by `if protocol != "snap"` and so reached the helper on no path at
    all before this block. The tile route above covers the other one."""
    ctrl, guard = MagicMock(), MagicMock()
    seen = []
    original = tools._protocol_shape_kwargs

    def record(protocol, supplied):
        seen.append(protocol)
        return original(protocol, supplied)

    monkeypatch.setattr(tools, "_protocol_shape_kwargs", record)
    result = tools.run_multiposition_acquisition(
        ctrl, guard, protocol="snap",
        positions=[{"name": "p", "x_um": 0, "y_um": 0}],
        protocol_params=params,
    )

    assert expected in result["error"]
    assert seen == ["snap"]
    assert ctrl.mock_calls == []
    assert guard.mock_calls == []


def test_execute_tool_refuses_forwarding_case_before_stage_or_camera(monkeypatch):
    ctrl, guard = MagicMock(), MagicMock()
    run_zstack = MagicMock()
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)
    monkeypatch.setattr(tools, "run_zstack", run_zstack)
    result = json.loads(tools.execute_tool(
        "run_multiposition_acquisition",
        {"protocol": "zstack", "save_dir": "/tmp/preflight",
         "positions": [{"name": "p", "x_um": 0, "y_um": 0}],
         "protocol_params": {"z_start_um": 0, "z_end_um": 2,
                             "z_step_um": 1, "n_frames": 1}},
        ctrl, guard,
    ))
    assert result["error"] == (
        "protocol_params for 'zstack' contains incompatible key 'n_frames'; "
        "timelapse parameters do not apply to a zstack."
    )
    run_zstack.assert_not_called()
    ctrl.core.get_x_position.assert_not_called()
    ctrl.core.set_xy_position.assert_not_called()
    ctrl.core.set_exposure.assert_not_called()


def test_acquire_on_hit_timelapse_requires_interval_before_search_frame(
    tmp_path, monkeypatch
):
    from microclaw.hook_decisions import HookResult, UntrustedHookAdapter

    class Hook:
        def analyze_frame(self, _image, _metadata):
            return HookResult({})

    ctrl, guard = MagicMock(), MagicMock()
    guard.resolve_in_workspace.side_effect = lambda path: path
    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    monkeypatch.setattr(tools, "_resolve_hook", lambda *_a, **_k: adapter)
    acquire = MagicMock()
    monkeypatch.setattr(tools, "_acquire_survey_with_detector", acquire)

    result = tools.run_adaptive_survey(
        ctrl, guard, protocol="timelapse", save_dir=str(tmp_path),
        hook_strategy="saved", positions=[{"name": "p", "x_um": 0, "y_um": 0}],
        protocol_params={"n_frames": 1, "interval_s": 0, "channel": "DAPI"},
        acquire_on_hit={
            "channel": "FITC", "protocol": "timelapse", "max_hits": 1,
            "protocol_params": {"n_frames": 2, "exposure_ms": 5},
        },
    )

    assert "missing 'interval_s'" in result["error"]
    acquire.assert_not_called()
    ctrl.core.set_exposure.assert_not_called()


def _run_acquire_on_hit_zstack(tmp_path, monkeypatch, acquire_params):
    from microclaw.acquisition import AcquisitionPlan
    from microclaw.hook_decisions import HookResult, UntrustedHookAdapter

    class Hook:
        def analyze_frame(self, _image, _metadata):
            return HookResult({})

    ctrl, guard = MagicMock(), MagicMock()
    guard.resolve_in_workspace.side_effect = lambda path: path
    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    monkeypatch.setattr(tools, "_resolve_hook", lambda *_a, **_k: adapter)
    monkeypatch.setattr(
        tools, "plan_events", lambda *_a, **_k: AcquisitionPlan(3, 5, 1, 1)
    )
    acquire = MagicMock(return_value={"error": "survey stopped by test"})
    monkeypatch.setattr(tools, "_acquire_survey_with_detector", acquire)
    monkeypatch.setattr(
        tools, "_channel_effects_for_later_phase", lambda *_a, **_k: {}
    )
    seen = []
    original = tools._protocol_shape_kwargs

    def record_shape(protocol, params):
        seen.append((protocol, dict(params)))
        return original(protocol, params)

    monkeypatch.setattr(tools, "_protocol_shape_kwargs", record_shape)
    result = tools.run_adaptive_survey(
        ctrl, guard, protocol="timelapse", save_dir=str(tmp_path),
        hook_strategy="saved", positions=[{"name": "p", "x_um": 0, "y_um": 0}],
        protocol_params={"n_frames": 1, "interval_s": 0, "channel": "DAPI"},
        acquire_on_hit={
            "channel": "FITC", "protocol": "zstack", "max_hits": 2,
            "protocol_params": acquire_params,
        },
    )
    return result, acquire, seen


def test_acquire_on_hit_zstack_translates_offsets_and_still_plans(
    tmp_path, monkeypatch
):
    result, acquire, seen = _run_acquire_on_hit_zstack(
        tmp_path, monkeypatch,
        {"z_offset_start_um": -1, "z_offset_end_um": 1,
         "z_step_um": 0.5, "exposure_ms": 5},
    )

    assert result["error"] == "survey stopped by test"
    assert ("zstack", {"z_start_um": -1, "z_end_um": 1,
                       "z_step_um": 0.5, "exposure_ms": 5}) in seen
    acquire.assert_called_once()
    assert acquire.call_args.kwargs["acquire_plan"].frames == 6


@pytest.mark.parametrize(
    ("params", "message", "reaches_shared_helper"),
    [
        ({"z_start_um": -1, "z_end_um": 1, "z_step_um": 0.5},
         "refuses absolute z_start_um/z_end_um", False),
        ({"z_offset_start_um": -1, "z_step_um": 0.5},
         "acquire_on_hit.protocol_params for 'zstack' is missing 'z_offset_end_um'",
         False),
        ({"z_offset_start_um": -1, "z_offset_end_um": 1, "z_step_um": 0.5,
          "n_frames": 2},
         "timelapse parameters do not apply to a zstack", True),
    ],
)
def test_acquire_on_hit_zstack_refusals_stay_on_their_own_boundaries(
    params, message, reaches_shared_helper, tmp_path, monkeypatch
):
    result, acquire, seen = _run_acquire_on_hit_zstack(
        tmp_path, monkeypatch, params
    )

    assert message in result["error"]
    assert any(protocol == "zstack" for protocol, _params in seen) is reaches_shared_helper
    acquire.assert_not_called()


def test_shared_schema_publishes_snap_as_parameterless():
    assert "snap takes no protocol_params" in tools_schema._PROTOCOL_PARAMS_SCHEMA[
        "description"
    ]
