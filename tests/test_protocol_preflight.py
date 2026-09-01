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
        ("zstack", {"z_start_um": 0, "z_end_um": 2}, "missing 'z_step_um'"),
        ("zstack", {**ZSTACK, "n_frames": 2},
         "timelapse parameters do not apply to a zstack"),
        ("timelapse", {**TIMELAPSE, "z_start_um": 0},
         "zstack parameters do not apply to a timelapse"),
        ("zstack", {**ZSTACK, "laser_slot": 1}, "incompatible key 'laser_slot'"),
        ("timelapse", {**TIMELAPSE, "max_frames": 2}, "incompatible key 'max_frames'"),
        ("snap", {"exposure_ms": 5}, "'snap' contains incompatible key 'exposure_ms'"),
    ],
)
def test_protocol_preflight_reports_missing_and_incompatible_keys(
    protocol, params, message
):
    with pytest.raises((KeyError, ValueError), match=message):
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
    ctrl.core.get_x_position.side_effect = lambda: effects("get_x_position")
    ctrl.core.get_y_position.side_effect = lambda: effects("get_y_position")
    ctrl.core.set_xy_position.side_effect = lambda *_: effects("set_xy_position")
    ctrl.core.set_exposure.side_effect = lambda *_: effects("set_exposure")
    ctrl.add_position.side_effect = lambda *_: effects("add_position")
    guard.resolve_in_workspace.side_effect = lambda path: effects("resolve") or path
    monkeypatch.setattr(tools, "_preflight_native_positions",
                        lambda *_a, **_k: effects("position_preflight"))
    monkeypatch.setattr(tools, "_plan_protocol_repetitions",
                        lambda *_a, **_k: effects("planning"))
    monkeypatch.setattr(tools, "run_multiposition_acquisition",
                        MagicMock(side_effect=lambda *_a, **_k: effects("forward")),
                        ) if name == "run_multiposition_with_autofocus" else None

    result = _call_outer(name, ctrl, guard, {"n_frames": 2}, hooked, tmp_path)

    assert "missing 'interval_s'" in result["error"]
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


def test_shared_schema_publishes_snap_as_parameterless():
    assert "snap takes no protocol_params" in tools_schema._PROTOCOL_PARAMS_SCHEMA[
        "description"
    ]
