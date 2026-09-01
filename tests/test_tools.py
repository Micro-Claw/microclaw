import json
import math
import os
import re
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, call

import numpy as np
import pytest

from tests.synthetic_optics import OPTICS_CASES, SyntheticOptics

from microclaw import tools
from microclaw.autofocus import AutofocusResult, SweepResult, curve_contrast
from microclaw.safety import (
    AnalysisConstraints, IlluminationConstraints, IlluminationProperty,
    NamedStageLimits, SafetyConstraints, SafetyGuard, SafetyViolation,
    StageConstraints,
)
from microclaw.tools import (
    clear_position_list,
    delete_position,
    generate_and_save_hook,
    get_available_channels,
    get_device_property,
    get_device_property_info,
    get_exposure,
    get_full_device_state,
    get_pixel_size,
    get_position_list,
    get_system_state,
    get_xy_position,
    get_z_position,
    go_to_position,
    list_device_properties,
    list_devices,
    list_hooks,
    load_skill,
    mark_position,
    move_stage_xy,
    move_stage_z,
    read_hook_from_file,
    run_autofocus,
    run_multiposition_acquisition,
    run_multiposition_with_autofocus,
    run_tile_acquisition,
    set_channel,
    set_device_property,
    set_exposure,
    snap_and_analyze,
    start_live_view,
    stop_live_view,
    shutter_declared_illumination,
)


class StrVector:
    """Bridge-shaped string vector: deliberately not Python-iterable."""

    def __init__(self, values):
        self._values = list(values)

    def size(self):
        return len(self._values)

    def get(self, index):
        return self._values[index]

    def __iter__(self):
        raise TypeError("mmcorej_StrVector is not iterable")


class TestLiveView:
    def test_start_live_view(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.studio.live().is_live_mode_on.return_value = True
        result = start_live_view(mock_ctrl, unconstrained_guard)
        mock_ctrl.studio.live().set_live_mode_on.assert_called_with(True)
        assert "status" in result

    def test_stop_live_view(self, mock_ctrl, unconstrained_guard):
        result = stop_live_view(mock_ctrl, unconstrained_guard)
        mock_ctrl.studio.live().set_live_mode_on.assert_called_with(False)
        assert "status" in result


class TestLiveViewReadiness:
    """design/37 F4: `start_live_view` must not claim a stream it has not seen.

    SCOPE, because the original F4 write-up got this wrong and this class was
    built against it: these tests do NOT reproduce the M5 incident. On M5 the
    start succeeded — `snap_and_analyze` reported "paused for the snap, then
    restored", a branch that only runs when `_pause_live` saw live ON — and it
    was `_pause_live`'s unchecked restore that failed. `javap` on MMJ_.jar 2.0.3
    confirms `setLiveModeOn` sets `isLiveOn_` and calls `startLiveMode()`
    synchronously, so a lagging flag is not a thing in this build.

    What these tests DO cover is real and separate: MM's own failure path
    (`startLiveMode` catching a sequence-start exception and calling
    `setLiveModeOn(false)`) leaves the flag false with no error raised to us, so
    a tool that returns "Live view started." without looking is lying by
    construction. The delay in the fake below stands in for any state that is
    not true immediately after the call — that failure path, or a bridge/MM
        variant that does lag. The separate restore path is covered below by
        checking CMMCore's sequence state, as design/37 F4 requires.
    """

    class DelayedStartLive:
        """A start whose flag is not true immediately after the call.

        Not a model of the M5 sequence (see the class docstring) — a model of
        "the tool must observe, not assume."
        """

        def __init__(self, stale_reads=2):
            self.stale_reads = stale_reads
            self.pending_start = False
            self.started_once = False
            self.is_on = False

        def set_live_mode_on(self, on):
            if on and not self.started_once:
                self.pending_start = True
            else:
                self.pending_start = False
                self.is_on = on

        def is_live_mode_on(self):
            if self.pending_start:
                if self.stale_reads:
                    self.stale_reads -= 1
                    return False
                self.pending_start = False
                self.started_once = True
                self.is_on = True
            return self.is_on

        def snap(self):
            # A snap taken while the start has not taken effect loses it. This
            # is the hazard the wait removes; it is NOT what happened on M5,
            # where the start had already taken effect.
            if self.pending_start:
                self.pending_start = False
                self.is_on = False

    @staticmethod
    def _ctrl_with_live(live):
        ctrl = MagicMock()
        ctrl.studio.live.return_value = live
        return ctrl

    @staticmethod
    def _snap(ctrl):
        with tools._pause_live(ctrl):
            ctrl.studio.live().snap()

    def test_wait_prevents_following_snap_from_losing_delayed_start(
        self, unconstrained_guard, monkeypatch
    ):
        monkeypatch.setattr(tools.time, "sleep", lambda _seconds: None)

        # Counterfactual: the old implementation returned immediately. Its
        # following snap saw stale false, declined to restore live, and left it off.
        old_live = self.DelayedStartLive()
        old_ctrl = self._ctrl_with_live(old_live)
        old_live.set_live_mode_on(True)
        self._snap(old_ctrl)
        assert old_live.is_live_mode_on() is False

        live = self.DelayedStartLive()
        ctrl = self._ctrl_with_live(live)
        result = start_live_view(ctrl, unconstrained_guard)
        self._snap(ctrl)

        assert result == {"status": "Live view started."}
        assert live.is_live_mode_on() is True

    def test_timeout_does_not_claim_stream_is_running(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        live = mock_ctrl.studio.live()
        live.is_live_mode_on.return_value = False
        clock = iter((10.0, 12.0))
        monkeypatch.setattr(tools.time, "monotonic", lambda: next(clock))

        result = start_live_view(mock_ctrl, unconstrained_guard)

        assert "error" in result
        assert "not running" in result["error"]
        assert "started" not in result.get("status", "").lower()


class TestGetCurrentDatetime:
    def test_every_field_renders_one_reading_of_the_clock(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        # Frozen, because the interesting failure is a field derived from a
        # *second* now() call: against the live clock that only disagrees when
        # the test happens to straddle a tick.
        fixed = datetime(2026, 8, 20, 17, 26, 32, tzinfo=timezone(timedelta(hours=2)))

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed if tz is None else fixed.astimezone(tz)

        monkeypatch.setattr(tools, "datetime", FrozenDatetime)
        result = tools.get_current_datetime(mock_ctrl, unconstrained_guard)
        assert result["local_iso"] == "2026-08-20T17:26:32+02:00"
        assert result["date"] == "2026-08-20"
        assert result["time"] == "17:26:32"
        assert result["compact"] == "20260820_172632"
        assert result["utc_offset"] == "+0200"
        assert result["utc_iso"] == "2026-08-20T15:26:32+00:00"

    def test_compact_is_filename_safe(self, mock_ctrl, unconstrained_guard):
        # This is the field the description tells the model to put in a folder
        # name, so a colon or a space in it is the defect.
        compact = tools.get_current_datetime(mock_ctrl, unconstrained_guard)["compact"]
        assert re.fullmatch(r"\d{8}_\d{6}", compact)

    def test_is_aware_and_carries_its_offset(self, mock_ctrl, unconstrained_guard):
        result = tools.get_current_datetime(mock_ctrl, unconstrained_guard)
        assert datetime.fromisoformat(result["local_iso"]).tzinfo is not None
        assert re.fullmatch(r"[+-]\d{4}", result["utc_offset"])


class TestGetPixelSize:
    def test_returns_calibrated_value(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_pixel_size_um.return_value = 0.108
        result = get_pixel_size(mock_ctrl, unconstrained_guard)
        assert result["pixel_size_um"] == pytest.approx(0.108)
        assert "warning" not in result

    def test_zero_returns_warning(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_pixel_size_um.return_value = 0.0
        result = get_pixel_size(mock_ctrl, unconstrained_guard)
        assert result["pixel_size_um"] == 0.0
        assert "warning" in result
        assert "calibration" in result["warning"].lower()

    def test_calls_core_with_no_args(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_pixel_size_um.return_value = 0.065
        get_pixel_size(mock_ctrl, unconstrained_guard)
        mock_ctrl.core.get_pixel_size_um.assert_called_once_with()


class TestSetExposure:
    def test_valid_exposure(self, mock_ctrl, default_guard):
        result = set_exposure(mock_ctrl, default_guard, ms=100.0)
        mock_ctrl.core.set_exposure.assert_called_once_with(100.0)
        assert result["status"]

    def test_exceeds_max(self, mock_ctrl, default_guard):
        with pytest.raises(SafetyViolation, match="2000"):
            set_exposure(mock_ctrl, default_guard, ms=3000.0)

    def test_get_exposure(self, mock_ctrl, unconstrained_guard):
        result = get_exposure(mock_ctrl, unconstrained_guard)
        assert result["exposure_ms"] == 100.0


class TestMoveStageZ:
    def test_idle_before_motion_never_passes_at_old_position(self, default_guard, monkeypatch):
        from microclaw import controller
        core = MagicMock()
        core.get_focus_device.return_value = "DStage"
        core.get_position.return_value = 50.0
        core.device_busy.return_value = False
        ctrl = MagicMock(core=core)
        monkeypatch.setattr(controller, "STAGE_MOVE_TIMEOUT_S", 0.0, raising=False)
        with pytest.raises(RuntimeError) as caught:
            move_stage_z(ctrl, default_guard, z_um=100.0)
        assert type(caught.value).__name__ == "StageMoveError"
        assert caught.value.result["measured_um"] == 50.0
        assert caught.value.result["within_tolerance"] is False

    def test_absolute_in_range(self, mock_ctrl, default_guard):
        mock_ctrl.core.get_position.return_value = 100.0
        result = move_stage_z(mock_ctrl, default_guard, z_um=100.0, absolute=True)
        mock_ctrl.core.set_position.assert_called_once_with(100.0)
        assert result["requested_um"] == 100.0
        assert result["measured_um"] == 100.0
        assert result["within_tolerance"] is True

    def test_absolute_below_min(self, mock_ctrl, default_guard):
        with pytest.raises(SafetyViolation, match="0"):
            move_stage_z(mock_ctrl, default_guard, z_um=-5.0, absolute=True)

    def test_absolute_above_max(self, mock_ctrl, default_guard):
        with pytest.raises(SafetyViolation, match="200"):
            move_stage_z(mock_ctrl, default_guard, z_um=250.0, absolute=True)

    def test_relative_resolves_to_absolute_before_check(self, mock_ctrl, default_guard):
        # Current Z is 50.0 (from fixture). Relative +200 → target 250 → exceeds max.
        with pytest.raises(SafetyViolation):
            move_stage_z(mock_ctrl, default_guard, z_um=200.0, absolute=False)

    def test_relative_in_range(self, mock_ctrl, default_guard):
        # Unbounded on purpose: a counted list asserts how many times the settle
        # loop polls, which is wall-clock dependent and differs by platform.
        mock_ctrl.core.get_position.side_effect = _positions(50.0, 60.0)
        result = move_stage_z(mock_ctrl, default_guard, z_um=10.0, absolute=False)
        mock_ctrl.core.set_relative_position.assert_called_once_with(10.0)
        assert result["measured_um"] == 60.0

    def test_get_z_position(self, mock_ctrl, unconstrained_guard):
        result = get_z_position(mock_ctrl, unconstrained_guard)
        assert result["z_um"] == 50.0

    def test_get_z_position_reports_bounds_without_moving(self, mock_ctrl):
        guard = SafetyGuard(SafetyConstraints(
            stage=StageConstraints(z_min=0.0, z_max=40.0)))
        result = get_z_position(mock_ctrl, guard)
        assert result["out_of_bounds"] == [
            "Z=50.0 µm exceeds the maximum allowed (40.0 µm)."
        ]
        mock_ctrl.core.set_position.assert_not_called()


class TestMoveStageXY:
    @pytest.mark.parametrize("late_axis", ["x", "y"])
    def test_not_busy_stage_arriving_on_a_later_poll_succeeds(
        self, late_axis, unconstrained_guard
    ):
        from itertools import chain, repeat
        from microclaw import controller

        core = MagicMock()
        core.get_xy_stage_device.return_value = "XY"
        core.device_busy.return_value = False
        # The late axis must still be out of band AFTER the stability window
        # could first have been satisfied, or this test cannot fail: a loop that
        # ignored the band entirely and merely collected
        # STAGE_MOVE_REQUIRED_SAMPLES readings would return the same value.
        # Verified by mutation -- with an arrival on the third poll, deleting
        # the band check outright leaves this test green.
        late = chain([0.0, 50.0, 62.0, 71.0, 78.0, 84.0, 89.0], repeat(99.0))
        immediate = chain([0.0], repeat(19.0))
        if late_axis == "x":
            core.get_x_position.side_effect = lambda: next(late)
            core.get_y_position.side_effect = lambda: next(immediate)
        else:
            core.get_x_position.side_effect = lambda: next(immediate)
            core.get_y_position.side_effect = lambda: next(late)
        ctrl = MagicMock(core=core)

        target = (100.0, 20.0) if late_axis == "x" else (20.0, 100.0)
        result = move_stage_xy(ctrl, unconstrained_guard, *target)

        assert result["measured_um"] == (
            [99.0, 19.0] if late_axis == "x" else [19.0, 99.0]
        )
        late_reader = core.get_x_position if late_axis == "x" else core.get_y_position
        assert late_reader.call_count > controller.STAGE_MOVE_REQUIRED_SAMPLES
        assert core.device_busy.call_count >= controller.STAGE_MOVE_REQUIRED_SAMPLES

    def test_idle_short_of_target_is_measured_and_refused(self, unconstrained_guard,
                                                          monkeypatch):
        from microclaw import controller
        core = MagicMock()
        core.get_xy_stage_device.return_value = "XY"
        core.device_busy.return_value = False
        core.get_x_position.side_effect = [0.0, 80.0]
        core.get_y_position.side_effect = [0.0, 0.0]
        ctrl = MagicMock(core=core)
        monkeypatch.setattr(controller, "STAGE_MOVE_TIMEOUT_S", 0.0)

        with pytest.raises(controller.XYStageMoveError) as caught:
            move_stage_xy(ctrl, unconstrained_guard, 100.0, 0.0)

        assert core.device_busy.call_count == 1
        assert core.get_x_position.call_count == 2
        assert caught.value.result["measured_um"] == [80.0, 0.0]
        assert caught.value.result["requested_um"] == [100.0, 0.0]

    def test_large_x_move_does_not_expand_held_y_band(self, unconstrained_guard,
                                                      monkeypatch):
        from microclaw import controller
        core = MagicMock()
        core.get_xy_stage_device.return_value = "XY"
        core.device_busy.return_value = False
        core.get_x_position.side_effect = [0.0, 200.0]
        core.get_y_position.side_effect = [0.0, 4.0]
        ctrl = MagicMock(core=core)
        monkeypatch.setattr(controller, "STAGE_MOVE_TIMEOUT_S", 0.0)
        monkeypatch.setattr(controller, "STAGE_MOVE_REQUIRED_SAMPLES", 1)
        monkeypatch.setattr(controller, "STAGE_MOVE_STABILITY_WINDOW_S", 0.0)

        with pytest.raises(controller.XYStageMoveError) as caught:
            move_stage_xy(ctrl, unconstrained_guard, 200.0, 0.0)

        assert caught.value.axes == ["y"]
        assert caught.value.result["x_tolerance_um"] == 20.0
        assert caught.value.result["y_tolerance_um"] == 2.0

    def test_one_stationary_axis_is_named_even_when_other_arrives(
        self, unconstrained_guard, monkeypatch
    ):
        from microclaw import controller
        core = MagicMock()
        core.get_xy_stage_device.return_value = "XY"
        core.device_busy.return_value = False
        core.get_x_position.side_effect = [0.0, 20.0]
        core.get_y_position.side_effect = [0.0, 0.0]
        ctrl = MagicMock(core=core)
        monkeypatch.setattr(controller, "STAGE_MOVE_TIMEOUT_S", 0.0)

        with pytest.raises(controller.XYStageMoveError) as caught:
            move_stage_xy(ctrl, unconstrained_guard, 20.0, 20.0)

        assert caught.value.axes == ["y"]
        assert "Y started 0.0 um" in str(caught.value)

    def test_relative_down_link_is_typed_and_never_dispatches(
        self, unconstrained_guard
    ):
        from microclaw.controller import XYStageMoveError
        core = MagicMock()
        core.get_xy_stage_device.return_value = "XY"
        core.get_x_position.side_effect = RuntimeError("link down")
        core.get_y_position.side_effect = RuntimeError("link down")
        core.device_busy.side_effect = RuntimeError("link down")
        ctrl = MagicMock(core=core)

        with pytest.raises(XYStageMoveError) as caught:
            move_stage_xy(ctrl, unconstrained_guard, 5.0, 6.0, absolute=False)

        assert caught.value.result["start_um"] is None
        assert caught.value.result["requested_um"] is None
        assert caught.value.result["measured_um"] is None
        core.set_relative_xy_position.assert_not_called()

    def test_configured_xy_bands_are_independent_and_claim_accuracy(
        self, mock_ctrl
    ):
        guard = SafetyGuard(SafetyConstraints(stage=StageConstraints(
            x_min=-100, x_max=100, y_min=-100, y_max=100,
            x_move_tolerance_um=0.25, y_move_tolerance_um=1.75,
        )))
        result = move_stage_xy(mock_ctrl, guard, 10.0, 10.0)
        assert result["x_tolerance_um"] == 0.25
        assert result["y_tolerance_um"] == 1.75
        assert result["x_band_source"] == result["y_band_source"] == "configured"
        assert result["verification_kind"] == "configured_accuracy"

    def test_in_range(self, mock_ctrl, default_guard):
        move_stage_xy(mock_ctrl, default_guard, x_um=100.0, y_um=-50.0)
        mock_ctrl.core.set_xy_position.assert_called_once_with(100.0, -50.0)

    def test_x_exceeds_max(self, mock_ctrl, default_guard):
        with pytest.raises(SafetyViolation, match="1000"):
            move_stage_xy(mock_ctrl, default_guard, x_um=1500.0, y_um=0.0)

    def test_y_below_min(self, mock_ctrl, default_guard):
        with pytest.raises(SafetyViolation, match="1000"):
            move_stage_xy(mock_ctrl, default_guard, x_um=0.0, y_um=-1500.0)

    def test_relative_resolves_absolute_first(self, mock_ctrl, default_guard):
        # Current X=0, Y=0. Relative (2000, 0) → absolute (2000, 0) → x exceeds max.
        with pytest.raises(SafetyViolation):
            move_stage_xy(mock_ctrl, default_guard, x_um=2000.0, y_um=0.0, absolute=False)

    def test_relative_in_range(self, mock_ctrl, default_guard):
        result = move_stage_xy(mock_ctrl, default_guard, x_um=50.0, y_um=50.0, absolute=False)
        mock_ctrl.core.set_relative_xy_position.assert_called_once_with(50.0, 50.0)
        assert result["x_um"] == 50.0
        assert result["y_um"] == 50.0

    def test_get_xy_position(self, mock_ctrl, unconstrained_guard):
        result = get_xy_position(mock_ctrl, unconstrained_guard)
        assert result["x_um"] == 0.0
        assert result["y_um"] == 0.0

    def test_get_xy_position_reports_bounds_without_moving(self, mock_ctrl):
        mock_ctrl.core.xy_position.update(y=12.5)
        guard = SafetyGuard(SafetyConstraints(
            stage=StageConstraints(x_min=-100, x_max=100, y_min=-100, y_max=10.0)))
        result = get_xy_position(mock_ctrl, guard)
        assert result["out_of_bounds"] == [
            "Y=12.5 µm exceeds the maximum allowed (10.0 µm)."
        ]
        mock_ctrl.core.set_xy_position.assert_not_called()

    def test_settling_error_surfaced(self, mock_ctrl, unconstrained_guard):
        # amr_test carried a 1.1 µm unrequested X excursion nothing surfaced.
        # The DEVICE chooses the landing, not the caller: this is a stage that
        # lands 1.1 µm past its X target and 0.1 µm short in Y, both inside the
        # response floor, so the move succeeds and still reports the excursion.
        mock_ctrl.core.set_xy_position.side_effect = (
            lambda x, y: mock_ctrl.core.xy_position.update(x=x + 1.1, y=y - 0.1))
        result = move_stage_xy(mock_ctrl, unconstrained_guard, x_um=100.0, y_um=200.0)
        assert result["achieved_um"] == [101.1, 199.9]
        assert result["error_um"] == [pytest.approx(1.1), pytest.approx(-0.1)]
        assert result["within_tolerance"] is True
        assert result["x_arrival_residual_um"] == pytest.approx(1.1)
        assert result["y_arrival_residual_um"] == pytest.approx(0.1)


def test_center_feature_accepts_and_reports_sub_band_correction(
    mock_ctrl, unconstrained_guard, monkeypatch
):
    affine = types.SimpleNamespace(
        a=1.0, b=0.0, c=0.0, d=1.0,
        px_to_um=lambda x, y: (float(x), float(y)),
    )
    monkeypatch.setattr(tools, "_resolve_current_affine", lambda _ctrl: (affine, {}))
    readings = iter([
        {"brightest_feature_offset_px": [1.0, 0.0]},
        {"brightest_feature_offset_px": [0.0, 0.0]},
    ])
    monkeypatch.setattr(tools, "find_features", lambda *_args, **_kwargs: next(readings))

    result = tools.center_feature(
        mock_ctrl, unconstrained_guard, max_iter=2, tol_px=0.1
    )

    assert result["centered"] is True
    assert result["arrival_unverifiable_corrections"] == [0]
    mock_ctrl.core.set_relative_xy_position.assert_called_once_with(1.0, 0.0)


@pytest.mark.parametrize("caller", [
    "move_stage_xy", "calibrate_stage_to_camera", "center_feature",
    "go_to_position", "run_multiposition_acquisition", "run_tile_acquisition",
])
def test_xy_move_failure_reaches_execute_tool_without_a_followup_move(
    caller, mock_ctrl, unconstrained_guard, monkeypatch, tmp_path
):
    from microclaw.controller import XYStageMoveError

    failure = XYStageMoveError({
        "start_um": [0.0, 0.0], "requested_um": [20.0, 20.0],
        "measured_um": [0.0, 0.0], "x_tolerance_um": 2.0,
        "y_tolerance_um": 2.0, "x_band_source": "floor",
        "y_band_source": "floor", "elapsed_s": 0.0,
        "last_device_status": "idle",
    }, ["x", "y"])
    moves = MagicMock(side_effect=failure)
    inputs = {}

    if caller == "move_stage_xy":
        from microclaw import controller
        moves = mock_ctrl.core.set_xy_position = MagicMock()
        monkeypatch.setattr(controller, "STAGE_MOVE_TIMEOUT_S", 0.0)
        inputs = {"x_um": 20.0, "y_um": 20.0}
    elif caller == "calibrate_stage_to_camera":
        monkeypatch.setattr(tools, "move_stage_xy", moves)
        monkeypatch.setattr(tools, "snap_to_numpy", lambda _ctrl: np.ones((8, 8)))
        inputs = {"step_um": 20.0, "pixel_size_hint_um": 1.0}
    elif caller == "center_feature":
        affine = types.SimpleNamespace(
            a=1.0, b=0.0, c=0.0, d=1.0, px_to_um=lambda x, y: (x, y)
        )
        monkeypatch.setattr(tools, "_resolve_current_affine", lambda _ctrl: (affine, {}))
        monkeypatch.setattr(tools, "find_features", lambda *_args, **_kwargs: {
            "brightest_feature_offset_px": [10.0, 0.0]
        })
        monkeypatch.setattr(tools, "move_stage_xy", moves)
    elif caller == "go_to_position":
        mock_ctrl.go_to_position = moves
        mock_ctrl.inspect_current_position_list.return_value = tools.PositionProjection(
            [{"name": "p", "x_um": 20.0, "y_um": 20.0}], [], []
        )
        inputs = {"name": "p"}
    else:
        mock_ctrl.core.set_xy_position = moves
        inputs = {
            "protocol": "snap", "positions": [
                {"name": "p1", "x_um": 20.0, "y_um": 20.0},
                {"name": "p2", "x_um": 30.0, "y_um": 30.0},
            ],
        }
        if caller == "run_tile_acquisition":
            inputs = {"protocol": "snap", "rows": 1, "cols": 2,
                      "step_um": 10.0, "return_to_center": False}

    payload = json.loads(tools.execute_tool(
        caller, inputs, mock_ctrl, unconstrained_guard
    ))

    assert payload["error"].startswith("XYStageMoveError:")
    assert moves.call_count == 1


class TestCalibrateStageToCamera:
    def test_identity_read_failure_refuses_to_save_calibration(
        self, mock_ctrl, unconstrained_guard, monkeypatch, tmp_path
    ):
        from microclaw import knowledge_manager
        from microclaw.tools import calibrate_stage_to_camera

        monkeypatch.setattr(
            knowledge_manager, "KNOWLEDGE_PATH", tmp_path / "knowledge.yaml"
        )
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy",
            lambda ctrl: np.ones((128, 128), dtype=np.float32),
        )
        monkeypatch.setattr(
            "skimage.registration.phase_cross_correlation",
            MagicMock(side_effect=[
                (np.array([0.0, 40.0]), 0, 0),
                (np.array([40.0, 0.0]), 0, 0),
            ]),
        )
        monkeypatch.setattr(
            "microclaw.tools._diagnose_calibration_shift", lambda *args: None
        )
        mock_ctrl.core.get_camera_device.return_value = "Cam"
        mock_ctrl.core.get_device_name.side_effect = RuntimeError("adapter unavailable")
        live = mock_ctrl.studio.live()
        live.is_live_mode_on.return_value = True
        live.set_live_mode_on.reset_mock()

        result = calibrate_stage_to_camera(
            mock_ctrl, unconstrained_guard, step_um=20.0
        )

        assert "measured but not saved" in result["error"]
        assert "adapter unavailable" in result["error"]
        assert not knowledge_manager.KNOWLEDGE_PATH.exists()
        assert live.set_live_mode_on.call_args_list == [call(False)]
        assert result["live_view_restore"]["left_off"] is True
        assert "start_live_view" in result["live_view_restore"]["reason"]

    def test_recovers_pixel_size_and_restores_stage(
        self, mock_ctrl, unconstrained_guard, monkeypatch, tmp_path
    ):
        from microclaw.tools import calibrate_stage_to_camera
        monkeypatch.setattr(
            "microclaw.knowledge_manager.KNOWLEDGE_PATH", tmp_path / "knowledge.yaml"
        )
        rng = np.random.default_rng(42)
        scene = rng.random((128, 128)).astype(np.float32)
        px = 0.5  # µm per pixel in the simulated optics
        pos = {"x": 0.0, "y": 0.0}
        mock_ctrl.core.get_x_position.side_effect = lambda: pos["x"]
        mock_ctrl.core.get_y_position.side_effect = lambda: pos["y"]
        mock_ctrl.core.set_relative_xy_position.side_effect = (
            lambda dx, dy: (pos.__setitem__("x", pos["x"] + dx),
                            pos.__setitem__("y", pos["y"] + dy))
        )
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy",
            lambda ctrl: np.roll(
                scene,
                (int(round(pos["y"] / px)), int(round(pos["x"] / px))),
                axis=(0, 1),
            ),
        )
        live = mock_ctrl.studio.live()
        live.is_live_mode_on.return_value = True
        live.set_live_mode_on.reset_mock()
        result = calibrate_stage_to_camera(mock_ctrl, unconstrained_guard, step_um=20.0)
        assert "error" not in result
        assert result["pixel_size_um"] == pytest.approx(px, rel=0.05)
        assert result["n_snaps"] == 4
        assert result["calibration_ref"]["kind"] == "knowledge_version"
        assert "_sha256_" in result["calibration_ref"]["key"]
        assert pos == {"x": 0.0, "y": 0.0}, "stage must return to its start"
        assert live.set_live_mode_on.call_args_list == [call(False)]
        assert result["live_view_restore"]["requested"] is False
        assert result["live_view_restore"]["left_off"] is True

    def test_featureless_field_returns_error_not_garbage(
        self, mock_ctrl, unconstrained_guard, monkeypatch, tmp_path
    ):
        from microclaw.tools import calibrate_stage_to_camera
        monkeypatch.setattr(
            "microclaw.knowledge_manager.KNOWLEDGE_PATH", tmp_path / "knowledge.yaml"
        )
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy",
            lambda ctrl: np.zeros((64, 64), dtype=np.float32),
        )
        result = calibrate_stage_to_camera(mock_ctrl, unconstrained_guard)
        assert "error" in result

    def test_step_larger_than_fov_names_the_real_cause(
        self, mock_ctrl, unconstrained_guard, monkeypatch, tmp_path
    ):
        """design/28 F4: a 20 µm step on a small ROI translated the scene out of
        frame. The error must say the step is too LARGE for the FOV, not blame a
        featureless field / too-small step."""
        from microclaw.tools import calibrate_stage_to_camera
        monkeypatch.setattr(
            "microclaw.knowledge_manager.KNOWLEDGE_PATH", tmp_path / "knowledge.yaml"
        )
        rng = np.random.default_rng(3)
        scene = rng.random((64, 64)).astype(np.float32)   # 64 px, contrasty
        monkeypatch.setattr("microclaw.tools.snap_to_numpy", lambda ctrl: scene)
        # 20 µm at 0.5 µm/px = 40 px, > 1/3 of a 64 px frame → no overlap.
        result = calibrate_stage_to_camera(
            mock_ctrl, unconstrained_guard, step_um=20.0, pixel_size_hint_um=0.5
        )
        assert "error" in result
        assert "too large" in result["error"]

    def test_step_auto_scales_to_fov_and_succeeds(
        self, mock_ctrl, unconstrained_guard, monkeypatch, tmp_path
    ):
        """With a pixel-size hint and no explicit step, the step is scaled to ¼
        of the FOV so the two snaps overlap and calibration succeeds."""
        from microclaw.tools import calibrate_stage_to_camera
        monkeypatch.setattr(
            "microclaw.knowledge_manager.KNOWLEDGE_PATH", tmp_path / "knowledge.yaml"
        )
        rng = np.random.default_rng(4)
        scene = rng.random((64, 64)).astype(np.float32)
        px = 0.5
        pos = {"x": 0.0, "y": 0.0}
        mock_ctrl.core.get_x_position.side_effect = lambda: pos["x"]
        mock_ctrl.core.get_y_position.side_effect = lambda: pos["y"]
        mock_ctrl.core.set_relative_xy_position.side_effect = (
            lambda dx, dy: (pos.__setitem__("x", pos["x"] + dx),
                            pos.__setitem__("y", pos["y"] + dy))
        )
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy",
            lambda ctrl: np.roll(
                scene,
                (int(round(pos["y"] / px)), int(round(pos["x"] / px))),
                axis=(0, 1),
            ),
        )
        result = calibrate_stage_to_camera(
            mock_ctrl, unconstrained_guard, pixel_size_hint_um=px
        )
        assert "error" not in result
        assert result["step_um"] == pytest.approx(0.25 * px * 64)   # 8 µm
        assert result["pixel_size_um"] == pytest.approx(px, rel=0.05)
        assert pos == {"x": 0.0, "y": 0.0}


class _FakeDeviceType:
    def __init__(self, name, ordinal):
        self._name, self._ordinal = name, ordinal

    def to_string(self):
        return self._name

    def swig_value(self):
        return self._ordinal


class TestNamedStages:
    """design/14 §6: address any stage by label, guarded per device, fail-closed."""

    _TYPES = {
        "DCam": _FakeDeviceType("CameraDevice", 2),
        "DXYStage": _FakeDeviceType("XYStageDevice", 6),
        "DStage": _FakeDeviceType("StageDevice", 5),
        "TIRF Stage": _FakeDeviceType("StageDevice", 5),
    }

    @pytest.fixture
    def stage_ctrl(self, mock_ctrl):
        mock_ctrl.core.get_loaded_devices.return_value = list(self._TYPES)
        mock_ctrl.core.get_device_type.side_effect = lambda d: self._TYPES[d]
        mock_ctrl.core.get_focus_device.return_value = "DStage"
        mock_ctrl.core.get_xy_stage_device.return_value = "DXYStage"
        return mock_ctrl

    @pytest.fixture
    def stage_guard(self):
        from microclaw.safety import NamedStageLimits
        return SafetyGuard(SafetyConstraints(
            named_stages=[NamedStageLimits("TIRF Stage", -3000.0, 3000.0)]
        ))

    def test_list_stages_classifies_and_flags_focus(self, stage_ctrl, unconstrained_guard):
        from microclaw.tools import list_stages
        result = list_stages(stage_ctrl, unconstrained_guard)
        assert result["focus_device"] == "DStage"
        assert result["single_axis_stages"] == ["DStage", "TIRF Stage"]
        assert result["other_single_axis"] == ["TIRF Stage"]
        assert result["xy_stages"] == ["DXYStage"]

    def test_get_stage_position(self, stage_ctrl, unconstrained_guard):
        from microclaw.tools import get_stage_position
        stage_ctrl.core.get_position.return_value = 123.4567
        result = get_stage_position(stage_ctrl, unconstrained_guard, device="TIRF Stage")
        stage_ctrl.core.get_position.assert_called_with("TIRF Stage")
        assert result["position_um"] == 123.4567

    def test_move_reports_requested_vs_achieved(self, stage_ctrl, stage_guard):
        from microclaw.tools import move_named_stage
        # Settling error is real on this rig and was previously invisible.
        stage_ctrl.core.get_position.side_effect = _positions(100.0, 200.0)
        result = move_named_stage(stage_ctrl, stage_guard, device="TIRF Stage", um=200.0)
        stage_ctrl.core.set_position.assert_called_once_with("TIRF Stage", 200.0)
        stage_ctrl.core.device_busy.assert_called_with("TIRF Stage")
        assert result["requested_um"] == 200.0
        assert result["measured_um"] == 200.0
        assert result["within_tolerance"] is True

    def test_hard_floor_is_typed_failure(self, stage_ctrl, stage_guard, monkeypatch):
        from microclaw import controller
        from microclaw.tools import move_named_stage
        stage_ctrl.core.get_position.return_value = 27.85
        stage_ctrl.core.device_busy.return_value = False
        monkeypatch.setattr(controller, "STAGE_MOVE_TIMEOUT_S", 0.0, raising=False)
        with pytest.raises(RuntimeError) as caught:
            move_named_stage(stage_ctrl, stage_guard, device="TIRF Stage", um=5.0)
        assert type(caught.value).__name__ == "StageMoveError"
        assert caught.value.result["requested_um"] == 5.0
        assert caught.value.result["measured_um"] == 27.85
        assert caught.value.result["within_tolerance"] is False
        assert "elapsed_s" in caught.value.result
        assert caught.value.result["last_device_status"] == "idle"

    def test_driver_hard_limit_exception_is_measured_typed_failure(self, stage_ctrl, stage_guard):
        from microclaw.controller import StageMoveError
        from microclaw.tools import move_named_stage
        stage_ctrl.core.get_position.return_value = 27.85
        stage_ctrl.core.set_position.side_effect = RuntimeError("device limit")
        with pytest.raises(StageMoveError) as caught:
            move_named_stage(stage_ctrl, stage_guard, device="TIRF Stage", um=5.0)
        assert caught.value.result["measured_um"] == 27.85
        assert caught.value.result["last_device_status"].startswith("dispatch_error")

    def test_real_1_1_um_settling_miss_is_typed_failure(self, stage_ctrl, stage_guard, monkeypatch):
        from microclaw import controller
        from microclaw.controller import StageMoveError
        from microclaw.tools import move_named_stage
        # Settling error is real on this rig and was previously invisible.
        # Two reads: one start (which also resolves a relative target) and one
        # settle sample.
        stage_ctrl.core.get_position.side_effect = [100.0, 201.1]
        monkeypatch.setattr(controller, "STAGE_MOVE_TIMEOUT_S", 0.0)
        with pytest.raises(StageMoveError) as caught:
            move_named_stage(stage_ctrl, stage_guard, device="TIRF Stage", um=200.0)
        assert caught.value.result["measured_um"] == 201.1

    def test_relative_move_resolves_absolute_before_check(self, stage_ctrl, stage_guard):
        from microclaw.tools import move_named_stage
        stage_ctrl.core.get_position.side_effect = [2900.0]
        with pytest.raises(SafetyViolation, match="maximum"):
            move_named_stage(stage_ctrl, stage_guard, device="TIRF Stage",
                             um=200.0, absolute=False)
        stage_ctrl.core.set_position.assert_not_called()

    def test_unconfigured_stage_fails_closed(self, stage_ctrl, stage_guard):
        from microclaw.tools import move_named_stage
        with pytest.raises(SafetyViolation, match="No limits configured"):
            move_named_stage(stage_ctrl, stage_guard, device="DStage", um=10.0)
        stage_ctrl.core.set_position.assert_not_called()


class TestSetChannel:
    def test_mapless_legacy_controller_delegates_to_set_config(self, mock_ctrl, default_guard):
        mock_ctrl.authorization_map = None
        set_channel(mock_ctrl, default_guard, preset="DAPI")
        mock_ctrl.core.set_config.assert_called_once_with("Channel", "DAPI")
        mock_ctrl.core.wait_for_config.assert_called_once_with("Channel", "DAPI")
        mock_ctrl.refresh_gui.assert_called_once_with()

    def test_forbidden_channel(self, mock_ctrl, default_guard):
        with pytest.raises(SafetyViolation, match="allowed list"):
            set_channel(mock_ctrl, default_guard, preset="GFP")

    def test_any_channel_when_unconstrained(self, mock_ctrl, unconstrained_guard):
        set_channel(mock_ctrl, unconstrained_guard, preset="GFP")  # no error

    def test_get_available_channels(self, mock_ctrl, unconstrained_guard):
        result = get_available_channels(mock_ctrl, unconstrained_guard)
        assert "DAPI" in result["channels"]
        assert "config group" in result["source"]

    @pytest.mark.parametrize("allowed,expected", [
        ([], []),                       # the exact M5 gate session
        (["DAPI"], ["DAPI"]),
    ])
    def test_allowlist_restricted_channels_are_reported_as_such(
        self, mock_ctrl, allowed, expected
    ):
        """M5 gate, 2026-08-06. A session ran with `channels.allowed: []`, was
        told it had four channels, picked one, and was refused. The allowlist is
        unchanged and still the only authority; the report simply stops offering
        what this session cannot use, while `channels` still says what the rig
        has so no rig reality is hidden.
        """
        from microclaw.safety import SafetyConstraints, SafetyGuard

        guard = SafetyGuard(SafetyConstraints(allowed_channels=allowed))
        result = get_available_channels(mock_ctrl, guard)
        assert result["channels"] == ["DAPI", "FITC", "Cy5"]
        assert result["authorized"] == expected
        for refused in set(result["channels"]) - set(expected):
            with pytest.raises(SafetyViolation, match="allowed list"):
                set_channel(mock_ctrl, guard, preset=refused)

    def test_unrestricted_session_reports_no_authorized_subset(
        self, mock_ctrl, unconstrained_guard
    ):
        assert "authorized" not in get_available_channels(
            mock_ctrl, unconstrained_guard)

    def test_channel_less_rig_says_so_instead_of_a_bare_empty_list(
        self, mock_ctrl, unconstrained_guard
    ):
        """An empty list on its own reads as "no channels"; it must say why."""
        mock_ctrl.core.get_available_configs.return_value = []
        result = get_available_channels(mock_ctrl, unconstrained_guard)
        assert result["channels"] == []
        assert "offers no channels" in result["source"]

    def test_acquisition_channel_axis_is_refused_when_presets_cannot_drive_it(
        self, mock_ctrl, unconstrained_guard
    ):
        """design/41 F6: `channel=` becomes channel_group="Channel" events.

        On a rig with no preset there, that would image every plane on
        whichever line happened to be on. Refuse, and name the way through.
        """
        from microclaw.tools import run_zstack

        mock_ctrl.core.get_available_configs.return_value = []
        with pytest.raises(SafetyViolation, match="cannot drive a channel axis") as caught:
            run_zstack(mock_ctrl, unconstrained_guard, z_start_um=0, z_end_um=1,
                       z_step_um=1, save_dir="d", channel="640")
        assert "set_channel first" in str(caught.value)

    def test_acquisition_channel_axis_still_runs_on_a_preset_rig(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw import tools

        monkeypatch.setattr(tools, "_build_acquisition_events", lambda **k: ["event"])
        monkeypatch.setattr(tools, "_authorize_acquisition", lambda *a, **k: None)
        monkeypatch.setattr(tools, "_acquire_with_hooks", lambda *a, **k: "/data/x")
        monkeypatch.setattr(tools, "_reservation_report", lambda r: {})
        out = tools.run_zstack(mock_ctrl, unconstrained_guard, z_start_um=0,
                               z_end_um=1, z_step_um=1, save_dir="d", channel="DAPI")
        assert out["dataset_path"] == "/data/x"


class TestSetDeviceProperty:
    def test_forbidden_property(self, mock_ctrl):
        from microclaw.safety import SafetyConstraints, SafetyGuard, ForbiddenProperty
        constraints = SafetyConstraints(
            forbidden_properties=[ForbiddenProperty(device="Core", property="Initialize")]
        )
        guard = SafetyGuard(constraints)
        with pytest.raises(SafetyViolation, match="forbidden"):
            set_device_property(mock_ctrl, guard, device="Core", property="Initialize", value="1")

    def test_allowed_property(self, mock_ctrl, unconstrained_guard):
        set_device_property(mock_ctrl, unconstrained_guard,
                            device="DCam", property="Gain", value="0")
        mock_ctrl.core.set_property.assert_called_once_with("DCam", "Gain", "0")
        mock_ctrl.refresh_gui.assert_called_once_with()

    def test_odd_stage_property_refused_even_for_in_bounds_numeric_value(
        self, mock_ctrl, unconstrained_guard
    ):
        from microclaw.authorization import AuthorizationMap, RigAuthorizationError
        mock_ctrl.authorization_map = AuthorizationMap(
            "degraded_trusted_plugins", "degraded", None,
            property_writes_unrestricted=True,
            bounded_stage_devices=frozenset({"DStage"}),
        )
        with pytest.raises(RigAuthorizationError) as exc:
            set_device_property(
                mock_ctrl, unconstrained_guard,
                device="DStage", property="Odd PositionZ Property", value="50",
            )
        message = str(exc.value)
        assert "move_stage_xy" in message and "move_stage_z" in message
        mock_ctrl.core.set_property.assert_not_called()

    def test_get_device_property(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_property.return_value = "42"
        result = get_device_property(mock_ctrl, unconstrained_guard,
                                     device="DCam", property="Gain")
        assert result["value"] == "42"
        assert result["device"] == "DCam"

    def test_raw_focus_position_over_max_blocked(self, mock_ctrl, default_guard):
        # A raw Position write to the focus device re-applies check_z, so an
        # out-of-range value is refused and set_property is never called.
        mock_ctrl.core.get_camera_device.return_value = "DCam"
        with pytest.raises(SafetyViolation):
            set_device_property(mock_ctrl, default_guard,
                                device="DStage", property="Position", value="999999")
        mock_ctrl.core.set_property.assert_not_called()

    def test_raw_camera_exposure_over_max_blocked(self, mock_ctrl, default_guard):
        mock_ctrl.core.get_camera_device.return_value = "DCam"
        with pytest.raises(SafetyViolation):
            set_device_property(mock_ctrl, default_guard,
                                device="DCam", property="Exposure", value="60000")
        mock_ctrl.core.set_property.assert_not_called()

    def _laser_guard(self):
        from microclaw.safety import IlluminationConstraints, IlluminationProperty
        return SafetyGuard(SafetyConstraints(
            illumination=IlluminationConstraints(
                shutters=[IlluminationProperty("Luxx638", "Laser Operation Select")]
            )
        ))

    def test_illumination_enable_blocked_when_declined(self, mock_ctrl, monkeypatch):
        # The gate must run through tools.CONFIRM_FN — in code, not the prompt.
        monkeypatch.setattr(
            "microclaw.tools.CONFIRM_FN", lambda s, kind="action", subject=None: False
        )
        with pytest.raises(SafetyViolation, match="declined"):
            set_device_property(mock_ctrl, self._laser_guard(),
                                device="Luxx638", property="Laser Operation Select",
                                value="On")
        mock_ctrl.core.set_property.assert_not_called()

    def test_illumination_enable_passes_when_confirmed(self, mock_ctrl, monkeypatch):
        monkeypatch.setattr(
            "microclaw.tools.CONFIRM_FN", lambda s, kind="action", subject=None: True
        )
        set_device_property(mock_ctrl, self._laser_guard(),
                            device="Luxx638", property="Laser Operation Select",
                            value="On")
        mock_ctrl.core.set_property.assert_called_once_with(
            "Luxx638", "Laser Operation Select", "On")


class TestListDevices:
    def test_returns_device_list(self, mock_ctrl, unconstrained_guard):
        result = list_devices(mock_ctrl, unconstrained_guard)
        assert "DCam" in result["devices"]
        assert "DXYStage" in result["devices"]


class TestGetSystemState:
    # "no EMU rig" is the suite-wide default: conftest primes the session cache
    # to None so nothing here reaches the host's MM install. The laser tests
    # below opt in by patching _cached_emu_properties themselves.

    def test_returns_state(self, mock_ctrl, unconstrained_guard):
        result = get_system_state(mock_ctrl, unconstrained_guard)
        assert "x_um" in result
        assert "y_um" in result
        assert "z_um" in result
        assert "exposure_ms" in result
        assert "declared_illumination_properties" not in result

    def test_missing_stage_bounds_are_reported_without_raising(self, mock_ctrl):
        result = get_system_state(mock_ctrl, SafetyGuard(SafetyConstraints()))
        assert result["out_of_bounds"] == [
            "No X bounds configured for the core stage. Add stage.x_min and "
            "stage.x_max before Microclaw may move it.",
            "No Z bounds configured for the core stage. Add stage.z_min and "
            "stage.z_max before Microclaw may move it.",
        ]

    def test_reports_declared_illumination_values_without_judging_them(self, mock_ctrl):
        guard = SafetyGuard(SafetyConstraints(illumination=IlluminationConstraints(
            shutters=[
                IlluminationProperty("Aggregate", "Enable", off_value="0"),
                IlluminationProperty("Aggregate", "Gate", off_value="0"),
                IlluminationProperty("Source 2", "Enable", off_value="0"),
                IlluminationProperty("Source 3", "Enable", off_value="0"),
                IlluminationProperty("Source 4", "Enable", off_value="0"),
            ]
        )))
        values = {
            ("Aggregate", "Enable"): "0", ("Aggregate", "Gate"): "1",
            ("Source 2", "Enable"): "0", ("Source 3", "Enable"): "0",
            ("Source 4", "Enable"): "0",
        }
        mock_ctrl.core.get_property.side_effect = lambda d, p: values[(d, p)]
        result = get_system_state(mock_ctrl, guard)
        assert [item["value"] for item in result["declared_illumination_properties"]] == [
            "0", "1", "0", "0", "0"
        ]
        assert "warning" not in result
        assert "refusal" not in result

    def test_handles_unavailable_stage(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_x_position.side_effect = Exception("Device not found")
        result = get_system_state(mock_ctrl, unconstrained_guard)
        assert result.get("xy_stage") == "unavailable"
        mock_ctrl.core.set_xy_position.assert_not_called()
        mock_ctrl.core.set_position.assert_not_called()

    def test_reports_xy_out_of_bounds_with_value_and_limit_without_moving(
        self, mock_ctrl
    ):
        mock_ctrl.core.xy_position.update(y=12.5)
        guard = SafetyGuard(SafetyConstraints(
            stage=StageConstraints(x_min=-100, x_max=100, y_min=-100, y_max=10.0,
                                   z_min=0, z_max=100)))
        result = get_system_state(mock_ctrl, guard)
        assert result["out_of_bounds"] == [
            "Y=12.5 µm exceeds the maximum allowed (10.0 µm)."
        ]
        mock_ctrl.core.set_xy_position.assert_not_called()
        mock_ctrl.core.set_position.assert_not_called()

    def test_in_bounds_omits_but_unset_limit_reports_out_of_bounds(self, mock_ctrl):
        mock_ctrl.core.xy_position.update(y=12.5)
        in_bounds = SafetyGuard(SafetyConstraints(
            stage=StageConstraints(x_min=-100, x_max=100, y_min=-100, y_max=20.0,
                                   z_min=0, z_max=100)))
        unset = SafetyGuard(SafetyConstraints(
            stage=StageConstraints(x_min=-100, x_max=100, y_min=-100, y_max=None,
                                   z_min=0, z_max=100)))
        assert "out_of_bounds" not in get_system_state(mock_ctrl, in_bounds)
        assert get_system_state(mock_ctrl, unset)["out_of_bounds"] == [
            "No Y bounds configured for the core stage. Add stage.y_min and "
            "stage.y_max before Microclaw may move it."
        ]

    def test_reports_z_out_of_bounds_independently(self, mock_ctrl):
        guard = SafetyGuard(SafetyConstraints(
            stage=StageConstraints(x_min=-100, x_max=100, y_min=-100, y_max=100,
                                   z_min=0, z_max=40.0)))
        result = get_system_state(mock_ctrl, guard)
        assert result["out_of_bounds"] == [
            "Z=50.0 µm exceeds the maximum allowed (40.0 µm)."
        ]
        mock_ctrl.core.set_position.assert_not_called()

    def test_reads_only_declared_named_stages_and_reports_bounds(self, mock_ctrl):
        guard = SafetyGuard(SafetyConstraints(
            stage=StageConstraints(x_min=-100, x_max=100, y_min=-100, y_max=100,
                                   z_min=0, z_max=100),
            named_stages=[NamedStageLimits("TIRF Stage", -10.0, 10.0)],
        ))
        mock_ctrl.core.get_position.side_effect = lambda *args: (
            12.5 if args == ("TIRF Stage",) else 50.0
        )
        result = get_system_state(mock_ctrl, guard)
        assert result["named_stages"] == {"TIRF Stage": 12.5}
        assert result["out_of_bounds"] == [
            "TIRF Stage=12.50 µm exceeds the maximum allowed (10.00 µm)."
        ]
        assert mock_ctrl.core.get_loaded_devices.call_count == 0
        mock_ctrl.core.set_position.assert_not_called()
        mock_ctrl.core.set_xy_position.assert_not_called()

    def test_reports_only_declared_stage_move_tolerances_without_extra_reads(self, mock_ctrl):
        guard = SafetyGuard(SafetyConstraints(
            stage=StageConstraints(x_min=-100, x_max=100, y_min=-100, y_max=100,
                                   z_min=0, z_max=100, z_move_tolerance_um=0.37),
            named_stages=[NamedStageLimits("A", -10, 10, 1.3),
                          NamedStageLimits("B", -10, 10)],
        ))
        mock_ctrl.core.get_position.side_effect = lambda *args: 0.0
        result = get_system_state(mock_ctrl, guard)
        assert result["z_move_tolerance_um"] == 0.37
        assert result["named_stage_move_tolerances_um"] == {"A": 1.3}
        assert set(result["named_stages"]) == {"A", "B"}

    def test_reports_shutter_and_lasers(self, mock_ctrl, unconstrained_guard):
        # design/20 S4: the agent signed off "no lasers were involved" from a
        # payload with no illumination field at all.
        mock_ctrl.core.get_shutter_device.return_value = "DShutter"
        mock_ctrl.core.get_shutter_open.return_value = False
        mock_ctrl.core.get_auto_shutter.return_value = True
        result = get_system_state(mock_ctrl, unconstrained_guard)
        assert result["shutter"] == {
            "device": "DShutter", "open": False, "auto": True,
            "open_during_exposure":
                "unknown (autoshutter opens the shutter for each exposure)",
        }
        assert "lasers" in result

    def test_autoshutter_must_not_yield_a_bare_closed_during_exposure(
        self, mock_ctrl, unconstrained_guard
    ):
        # design/21 S2: with autoshutter on, MM opens the shutter per exposure,
        # so the resting open:false says nothing about the light path during
        # the snap — and the agent built a closed-shutter theory on it twice.
        # Not `False` (a fact it wasn't) and not `True` either (a present read
        # standing in for a past event, the same move pointed the other way).
        mock_ctrl.core.get_shutter_device.return_value = "DShutter"
        mock_ctrl.core.get_shutter_open.return_value = False
        mock_ctrl.core.get_auto_shutter.return_value = True
        shutter = get_system_state(mock_ctrl, unconstrained_guard)["shutter"]
        assert shutter["open_during_exposure"] is not False
        assert shutter["open_during_exposure"] is not True
        assert "autoshutter" in shutter["open_during_exposure"]

    def test_a_manual_shutters_resting_state_is_its_exposure_state(
        self, mock_ctrl, unconstrained_guard
    ):
        mock_ctrl.core.get_shutter_device.return_value = "DShutter"
        mock_ctrl.core.get_auto_shutter.return_value = False
        mock_ctrl.core.get_shutter_open.return_value = False
        shutter = get_system_state(mock_ctrl, unconstrained_guard)["shutter"]
        assert shutter["open_during_exposure"] is False  # really shut

        mock_ctrl.core.get_shutter_open.return_value = True
        shutter = get_system_state(mock_ctrl, unconstrained_guard)["shutter"]
        assert shutter["open_during_exposure"] is True   # held open

    def test_illumination_fields_are_present_even_when_unknowable(
        self, mock_ctrl, unconstrained_guard
    ):
        # An omitted key is what the agent filled from imagination. "unknown" is
        # a fact it can report; a missing field is an invitation.
        mock_ctrl.core.get_shutter_device.side_effect = Exception("no such device")
        result = get_system_state(mock_ctrl, unconstrained_guard)
        assert result["shutter"] == "unknown"
        assert result["lasers"] == tools._NO_LASER_MAP

    def test_no_shutter_device_is_not_the_same_as_shutter_closed(
        self, mock_ctrl, unconstrained_guard
    ):
        # Knowing there is no shutter is not knowing the light is off.
        mock_ctrl.core.get_shutter_device.return_value = ""
        result = get_system_state(mock_ctrl, unconstrained_guard)
        assert result["shutter"] == "no shutter device configured"
        assert result["shutter"] is not False

    def test_a_partly_readable_shutter_marks_only_the_unreadable_part(
        self, mock_ctrl, unconstrained_guard
    ):
        mock_ctrl.core.get_shutter_device.return_value = "DShutter"
        mock_ctrl.core.get_shutter_open.return_value = True
        mock_ctrl.core.get_auto_shutter.side_effect = Exception("unsupported")
        result = get_system_state(mock_ctrl, unconstrained_guard)
        assert result["shutter"] == {
            "device": "DShutter", "open": True, "auto": "unknown",
            # auto unreadable: whether the resting read means anything is
            # unknowable, so the exposure answer is too — never inferred from
            # `open` alone.
            "open_during_exposure": "unknown",
        }

    def test_names_the_camera_label_and_adapter(self, mock_ctrl, unconstrained_guard):
        # design/21 F3: the session was about a camera and the state block
        # never named it, so the agent took the user's word for the hardware.
        # The label is whatever the config author typed; the adapter is the
        # hardware, and what F4's observed_on condition keys on.
        mock_ctrl.core.get_camera_device.return_value = "Camera"
        mock_ctrl.core.get_device_name.return_value = "DCam"
        result = get_system_state(mock_ctrl, unconstrained_guard)
        assert result["camera"] == {"label": "Camera", "adapter": "DCam"}
        mock_ctrl.core.get_device_name.assert_called_once_with("Camera")

    def test_an_unreadable_camera_is_unknown_not_absent(
        self, mock_ctrl, unconstrained_guard
    ):
        mock_ctrl.core.get_camera_device.side_effect = Exception("no core camera")
        result = get_system_state(mock_ctrl, unconstrained_guard)
        assert result["camera"] == "unknown"

    def test_a_camera_whose_adapter_read_fails_is_unknown_not_half_reported(
        self, mock_ctrl, unconstrained_guard
    ):
        # The half-failure: the label reads, get_device_name raises. A bare
        # label could still be mistaken for a verified identity; "unknown" says
        # plainly that nothing here can anchor an observed_on condition.
        mock_ctrl.core.get_camera_device.return_value = "Camera"
        mock_ctrl.core.get_device_name.side_effect = Exception("bridge error")
        result = get_system_state(mock_ctrl, unconstrained_guard)
        assert result["camera"] == "unknown"

    def test_an_empty_camera_label_reads_unknown(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_camera_device.return_value = ""
        result = get_system_state(mock_ctrl, unconstrained_guard)
        assert result["camera"] == {"label": "unknown", "adapter": "unknown"}
        mock_ctrl.core.get_device_name.assert_not_called()

    def test_laser_slots_are_read_through_the_emu_map(self, mock_ctrl,
                                                      unconstrained_guard, monkeypatch):
        monkeypatch.setattr(tools, "_cached_emu_properties", lambda ctrl: ({"stub": {}}, {}))
        monkeypatch.setattr(
            "microclaw.emu_manager.build_emu_map",
            lambda props, params: {"lasers": {
                3: {"name": "640",
                    "enable": {"device": "Laser3", "property": "On"},
                    "power_pct": {"device": "Laser3", "property": "Power"}},
                1: {"enable": {"device": "Laser1", "property": "On"}},
            }},
        )
        mock_ctrl.core.get_property.side_effect = lambda dev, prop: {
            ("Laser3", "On"): "1", ("Laser3", "Power"): "40", ("Laser1", "On"): "0",
        }[(dev, prop)]
        result = get_system_state(mock_ctrl, unconstrained_guard)
        # Keyed by slot index, never by device order (design/14 §1).
        assert result["lasers"] == {
            1: {"enabled": "0"},
            3: {"name": "640", "enabled": "1", "power_pct": "40"},
        }

    def test_a_slot_with_no_enable_or_power_line_is_unknown_not_absent(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        # Seen on the rig: build_emu_map yields a slot carrying only trigger
        # lines. There is nothing to read for it, and saying nothing about a
        # laser slot is the failure this fix exists to prevent.
        monkeypatch.setattr(tools, "_cached_emu_properties", lambda ctrl: ({"stub": {}}, {}))
        monkeypatch.setattr(
            "microclaw.emu_manager.build_emu_map",
            lambda props, params: {"lasers": {0: {"trigger_mode": {"device": "T", "property": "M"}}}},
        )
        result = get_system_state(mock_ctrl, unconstrained_guard)
        assert result["lasers"] == {0: "unknown"}
        mock_ctrl.core.get_property.assert_not_called()

    def test_an_unreadable_laser_line_says_so(self, mock_ctrl, unconstrained_guard,
                                              monkeypatch):
        monkeypatch.setattr(tools, "_cached_emu_properties", lambda ctrl: ({"stub": {}}, {}))
        monkeypatch.setattr(
            "microclaw.emu_manager.build_emu_map",
            lambda props, params: {"lasers": {2: {"enable": {"device": "L2", "property": "On"}}}},
        )
        mock_ctrl.core.get_property.side_effect = Exception("bridge error")
        result = get_system_state(mock_ctrl, unconstrained_guard)
        assert result["lasers"] == {2: {"enabled": "unknown"}}


def test_explicit_shutter_tool_drives_every_declaration_to_off_value(mock_ctrl):
    guard = SafetyGuard(SafetyConstraints(illumination=IlluminationConstraints(
        shutters=[
            IlluminationProperty("Source", "Enable", off_value="0"),
            IlluminationProperty("Aggregate", "Gate", off_value="closed"),
        ]
    )))
    mock_ctrl.core.get_property.side_effect = ["1", "armed"]
    result = shutter_declared_illumination(mock_ctrl, guard)
    assert result["attempted"] == ["Source.Enable", "Aggregate.Gate"]
    assert result["shuttered"] == [
        ("Source", "Enable", "0"), ("Aggregate", "Gate", "closed")
    ]
    assert mock_ctrl.core.set_property.call_args_list == [
        call("Source", "Enable", "0"), call("Aggregate", "Gate", "closed")
    ]


def test_explicit_shutter_tool_reports_only_successful_write_triples(mock_ctrl):
    guard = SafetyGuard(SafetyConstraints(illumination=IlluminationConstraints(
        shutters=[
            IlluminationProperty("Source", "Enable", off_value="0"),
            IlluminationProperty("Broken", "Gate", off_value="closed"),
        ]
    )))
    mock_ctrl.core.get_property.side_effect = ["1", "armed"]
    mock_ctrl.core.set_property.side_effect = [None, RuntimeError("unplugged")]

    result = shutter_declared_illumination(mock_ctrl, guard)

    assert result["shuttered"] == [("Source", "Enable", "0")]
    assert mock_ctrl.core.set_property.call_args_list == [
        call("Source", "Enable", "0"), call("Broken", "Gate", "closed")
    ]


class TestSnapAndAnalyze:
    @pytest.fixture(autouse=True)
    def _patch_snaps(self, monkeypatch):
        self.displayed_calls = []
        self.headless_calls = []
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy_displayed",
            lambda ctrl: self.displayed_calls.append(1)
            or np.zeros((64, 64), dtype=np.uint16),
        )
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy",
            lambda ctrl: self.headless_calls.append(1)
            or np.zeros((64, 64), dtype=np.uint16),
        )

    def test_returns_dict_by_default(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_position.return_value = 50.0
        result = snap_and_analyze(mock_ctrl, unconstrained_guard)
        assert isinstance(result, dict)
        assert "focus_metric" in result
        assert "mean_intensity" in result
        assert "z_um" in result

    def test_displays_by_default(self, mock_ctrl, unconstrained_guard):
        result = snap_and_analyze(mock_ctrl, unconstrained_guard)
        assert self.displayed_calls and not self.headless_calls
        assert result["displayed_in_mm_viewer"] is True

    def test_headless_when_display_false(self, mock_ctrl, unconstrained_guard):
        result = snap_and_analyze(mock_ctrl, unconstrained_guard, display=False)
        assert self.headless_calls and not self.displayed_calls
        assert result["displayed_in_mm_viewer"] is False

    def test_display_false_never_asks_mm_for_a_window(self, mock_ctrl,
                                                      unconstrained_guard):
        # display=False is headless by definition; probing the viewer would be
        # a pointless bridge round trip.
        snap_and_analyze(mock_ctrl, unconstrained_guard, display=False)
        mock_ctrl.studio.live().get_display.assert_not_called()

    def test_reports_not_displayed_when_mm_has_no_viewer(self, mock_ctrl,
                                                         unconstrained_guard):
        # design/18 regression. This field used to echo the `display` parameter,
        # so it read True while MM's Preview window sat on "Waiting for
        # Image..." — and agent.py tells the model to trust it ("never claim an
        # image is on screen unless it is true"). It must observe, not assume.
        mock_ctrl.studio.live().get_display.return_value = None
        result = snap_and_analyze(mock_ctrl, unconstrained_guard)
        assert result["displayed_in_mm_viewer"] is False

    def test_live_paused_and_restored(self, mock_ctrl, unconstrained_guard):
        # The amr_test crash: snapping under live view. snap(True) under live
        # wedges the bridge (V1), so live MUST be off before the snap.
        mock_ctrl.studio.live().is_live_mode_on.return_value = True
        live = mock_ctrl.studio.live()
        live.set_live_mode_on.reset_mock()
        result = snap_and_analyze(mock_ctrl, unconstrained_guard)
        calls = live.set_live_mode_on.call_args_list
        assert calls[0] == call(False), "live must be stopped before the snap"
        assert calls[-1] == call(True), "live must be restored after the snap"
        assert result["live_view"] == "paused for the snap; camera sequence restart verified"
        assert result["live_view_restore"]["sequence_running"] is True
        mock_ctrl.core.is_sequence_running.assert_called()

    def test_live_restore_does_not_claim_success_without_camera_sequence(
            self, mock_ctrl, unconstrained_guard, monkeypatch):
        mock_ctrl.studio.live().is_live_mode_on.return_value = True
        mock_ctrl.core.is_sequence_running.return_value = False
        monkeypatch.setattr(tools, "_LIVE_MODE_WAIT_S", 0)
        result = snap_and_analyze(mock_ctrl, unconstrained_guard)
        assert result["live_view"] == (
            "paused for the snap; camera sequence restart not verified"
        )
        assert result["live_view_restore"]["sequence_running"] is False
        assert "did not report" in result["live_view_restore"]["warning"]

    def test_live_untouched_when_off(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.studio.live().is_live_mode_on.return_value = False
        live = mock_ctrl.studio.live()
        live.set_live_mode_on.reset_mock()
        snap_and_analyze(mock_ctrl, unconstrained_guard)
        live.set_live_mode_on.assert_not_called()

    def test_metric_is_stamped_with_comparability_key(self, mock_ctrl, unconstrained_guard):
        # A bare float invites cross-setting comparisons (design/14 §10).
        result = snap_and_analyze(mock_ctrl, unconstrained_guard)
        # "_gated": focus_metric now travels with focus_metric_valid + snr (design/25).
        assert result["focus_metric_kind"] == "tenengrad_gated"
        assert set(result["metric_valid_for"]) == {
            "roi", "exposure_ms", "binning", "region"
        }
        assert result["metric_valid_for"]["region"] is None
        for metric in ("signal_coverage", "structure_coverage",
                       "signal_concentration"):
            assert result[metric] == round(result[metric], 6)

    def test_region_applies_to_every_statistic_and_metric_stamp(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        image = np.full((16, 16), 10, dtype=np.uint16)
        image[4:8, 2:6] = np.arange(16, dtype=np.uint16).reshape(4, 4) + 100
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy_displayed", lambda ctrl: image
        )

        result = snap_and_analyze(
            mock_ctrl, unconstrained_guard, region=[2, 4, 4, 4]
        )

        assert result["mean_intensity"] == pytest.approx(107.5)
        assert result["min_intensity"] == 100.0
        assert result["max_intensity"] == 115.0
        assert result["metric_valid_for"]["region"] == [2, 4, 4, 4]

    def test_drawn_region_resolves_once_and_payload_echoes_literal(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        image = np.arange(64 * 64, dtype=np.uint16).reshape(64, 64)
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy_displayed", lambda ctrl: image
        )
        mock_ctrl.drawn_region.return_value = [2, 4, 4, 4]

        result = snap_and_analyze(
            mock_ctrl, unconstrained_guard, region="drawn"
        )

        mock_ctrl.drawn_region.assert_called_once_with()
        assert result["mean_intensity"] == pytest.approx(
            image[4:8, 2:6].mean()
        )
        assert result["metric_valid_for"]["region"] == [2, 4, 4, 4]

    def test_region_arrives_as_a_json_string_from_the_model(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """A literal region the model stringified is still a literal region.

        Measured on the Nikon, 2026-08-19 (54c gate Step 3): the agent sent
        `"region": "[726, 591, 174, 171]"` four times in a row -- three of them
        after the operator explicitly asked for an array -- and every call was
        refused as malformed. 54b's schema declared `type: "array"` and the same
        literal call worked on three earlier trips; 54c replaced it with a
        `oneOf` carrying no top-level type, and the model started quoting.

        The schema is fixed alongside this, but the schema is a request, not a
        guarantee: a faithful JSON array of four integers is unambiguous however
        it arrives, so parse it. Anything else still refuses.
        """
        image = np.arange(64 * 64, dtype=np.uint16).reshape(64, 64)
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy_displayed", lambda ctrl: image
        )
        mock_ctrl.core.get_image_width.return_value = 64
        mock_ctrl.core.get_image_height.return_value = 64

        result = snap_and_analyze(
            mock_ctrl, unconstrained_guard, region="[2, 4, 4, 4]"
        )

        assert result["metric_valid_for"]["region"] == [2, 4, 4, 4]
        assert result["mean_intensity"] == pytest.approx(
            image[4:8, 2:6].mean(), abs=0.05
        )

    @pytest.mark.parametrize("region", [
        "[2, 4, 4]", "[2, 4, 4, 4, 4]", "[2.5, 4, 4, 4]", "2, 4, 4, 4",
        "not a region", "[]",
    ])
    def test_a_string_that_is_not_four_integers_still_refuses(
        self, mock_ctrl, unconstrained_guard, region
    ):
        result = snap_and_analyze(
            mock_ctrl, unconstrained_guard, region=region
        )
        assert "error" in result

    def test_region_is_rechecked_against_the_frame_that_came_back(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """The pre-snap check reads the camera; the crop lands on the array.

        Those are two different frames whenever a second client changes binning
        or the ROI in between — and the user owns the session, so that is
        ordinary. A bare slice truncates instead of raising, so the metric came
        back measured over 24x24 pixels while metric_valid_for still named the
        40x40 box that was asked for: the silently clamped region design/54
        exists to remove, with the stamp asserting it had not happened.
        run_autofocus re-checks per frame for exactly this reason
        (_run_autofocus_passes' metric_fn); the snap path must too.
        """
        frame = np.tile(np.arange(64, dtype=np.uint16), (64, 1))
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy_displayed", lambda ctrl: frame
        )
        # The fixture's camera reports 1024x1024, so this box passes the
        # pre-snap check and cannot survive the crop.
        mock_ctrl.drawn_region.return_value = [40, 40, 40, 40]

        result = snap_and_analyze(
            mock_ctrl, unconstrained_guard, region="drawn"
        )

        assert "[40, 40, 40, 40]" in result["error"]
        assert "[64, 64]" in result["error"]
        assert "metric_valid_for" not in result

    def test_metric_gate_comes_from_rig_config(self, mock_ctrl):
        guard = SafetyGuard(SafetyConstraints(
            analysis=AnalysisConstraints(min_snr=999.0)
        ))
        result = snap_and_analyze(mock_ctrl, guard)
        assert result["min_snr"] == 999.0
        assert result["min_snr_source"] == "rig_config"
        assert result["focus_metric_valid"] is False
        assert "999" in result["warning"]

    def test_saturated_snap_payload_exposes_snr_invalidity_reason(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        image = np.arange(10000, dtype=np.uint16).reshape(100, 100)
        image.flat[:5] = np.iinfo(np.uint16).max
        monkeypatch.setattr(tools, "snap_to_numpy_displayed", lambda ctrl: image)
        result = snap_and_analyze(mock_ctrl, unconstrained_guard)
        assert result["snr"] is None
        assert result["snr_valid"] is False
        assert "saturated" in result["snr_invalid_reason"]
        assert result["warning"] == result["snr_invalid_reason"]

    def test_sub_threshold_clipping_is_still_visible_in_the_report(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        # M5 rig gate, 2026-08-06: at 60 ms the frame clipped (max_intensity
        # 65535) while saturated_fraction printed 0.0, because the payload
        # rounded to 4 places and the validity gate fires at 1e-4. The display
        # resolution equalled the decision threshold, so the operator could not
        # see where they stood relative to it -- the exact confusion design/41 F4
        # set out to remove. The gate itself is right: a handful of ceiling
        # pixels must not invalidate a p99.5-based SNR.
        image = np.full((200, 200), 300, dtype=np.uint16)   # 40000 px
        image[0, 0] = np.iinfo(np.uint16).max               # 1 px -> 2.5e-5
        monkeypatch.setattr(tools, "snap_to_numpy_displayed", lambda ctrl: image)
        result = snap_and_analyze(mock_ctrl, unconstrained_guard)

        assert result["max_intensity"] == 65535.0
        assert result["snr_valid"] is True          # below the 1e-4 gate
        # The number must not collapse to zero next to a clipped max_intensity.
        assert result["saturated_fraction"] > 0
        assert result["saturated_fraction"] == pytest.approx(2.5e-05)

    def test_zero_pixel_size_carries_warning(self, mock_ctrl, unconstrained_guard):
        # The model asked about pixel size once and had forgotten 20 messages
        # later — the warning must ride along on every snap (design/14 §8).
        mock_ctrl.core.get_pixel_size_um.return_value = 0.0
        result = snap_and_analyze(mock_ctrl, unconstrained_guard)
        assert "pixel-size" in result["warning"].lower() or "pixel size" in result["warning"].lower()

    def test_returns_multimodal_when_requested(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_position.return_value = 50.0
        result = snap_and_analyze(mock_ctrl, unconstrained_guard, return_thumbnail=True)
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "image"
        payload = json.loads(result[0]["text"])
        assert "focus_metric" in payload
        assert "mean_intensity" in payload
        assert "z_um" in payload

    def test_reports_the_true_pixel_minimum(self, mock_ctrl, unconstrained_guard,
                                            monkeypatch):
        # design/19 F1. compute_stats has always had min_intensity; the payload
        # dropped it, so the agent reported find_features' modal background
        # (662) in a column headed "Min" when the real floor was 142. A frame
        # whose minimum, mean and background estimate all differ is the only
        # kind that catches this.
        image = np.full((64, 64), 700, dtype=np.uint16)
        image[0, 0] = 142
        image[32, 32] = 1182
        monkeypatch.setattr("microclaw.tools.snap_to_numpy_displayed", lambda ctrl: image)
        result = snap_and_analyze(mock_ctrl, unconstrained_guard)
        assert result["min_intensity"] == 142.0
        assert result["max_intensity"] == 1182.0
        assert result["min_intensity"] != result["mean_intensity"]


def _positions(before, after):
    """One pre-move read, then the settled position for as long as it is asked.

    A fixed list here counts the settle loop's polls, and that count is wall
    clock dependent: settle_stage_move wants STAGE_MOVE_REQUIRED_SAMPLES
    in-tolerance reads spanning STAGE_MOVE_STABILITY_WINDOW_S, so a list sized
    to the minimum passes wherever sleep overshoots and StopIterations wherever
    it does not. test_move_reports_requested_vs_achieved failed exactly that way
    on the Nikon's Windows/Python 3.12 run (block 54bde gate, 2026-08-19) while
    green on macOS. The behaviour under test is what gets reported, never how
    many times it looked.
    """
    from itertools import chain, repeat
    values = chain([before], repeat(after))
    return lambda *_args, **_kwargs: next(values)


_FAKE_SWEEP = SweepResult(
    z_positions=[49.0, 50.0, 51.0],
    metric_values=[0.1, 0.9, 0.1],
    best_z_um=50.0,
    peak_interior=True,
    measured_z_positions=[49.0, 50.0, 51.0],
)

_FAKE_AF_RESULT = AutofocusResult(
    coarse=_FAKE_SWEEP,
    fine=_FAKE_SWEEP,
    entry_z_um=50.0,
    final_z_um=50.0,
    converged=True,
    moved=True,
    reason=None,
)


def _patch_autofocus(monkeypatch):
    """Stub out the sweep functions and image helpers used by run_autofocus."""
    monkeypatch.setattr("microclaw.tools.coarse_then_fine_autofocus", lambda *a, **k: _FAKE_AF_RESULT)
    monkeypatch.setattr("microclaw.tools.single_sweep_autofocus", lambda *a, **k: _FAKE_AF_RESULT)
    monkeypatch.setattr("microclaw.tools.snap_to_numpy", lambda ctrl: np.zeros((64, 64), dtype=np.uint16))
    # image_content is where the rendering happens now, and it calls
    # make_thumbnail in its own module — patch it there.
    monkeypatch.setattr(
        "microclaw.image_analysis.make_thumbnail", lambda img, **kwargs: ""
    )


class TestRunAutofocus:
    def test_probe_dwell_default_follows_the_stopping_rule(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """No dwell when stopping early; a dwell when mapping the band.

        Nikon, 56ab gate 2026-08-23. Sweeping 2200-2800 at 5 um with
        stop_when_found false: dwell 0 read the band as {2655, 2660} and REFUSED
        it as too few planes, dwell 500 read {2650, 2655, 2660} and converged.
        The 2650 plane is real. Band mapping is decided by its edge planes, which
        are exactly the ones a lagging property reports wrongly; stopping at the
        first in-range plane is not, because a late read lands one plane deeper
        into the band and engaging the lock confirms it at zero dose.

        So the default follows the mode, and an explicit dwell_ms still wins in
        either. Asserting the ABSENCE of waiting is the point of the first case.
        """
        position = [2.0]
        mock_ctrl.core.get_position.side_effect = lambda *_a: position[0]
        mock_ctrl.core.set_position.side_effect = lambda z: position.__setitem__(0, float(z))
        mock_ctrl.core.device_busy.return_value = False
        mock_ctrl.core.get_allowed_property_values.return_value = StrVector([])
        mock_ctrl.core.get_property.side_effect = lambda *_a: (
            "in" if 1.0 <= position[0] <= 3.0 else "out"
        )
        sleeps = []
        monkeypatch.setattr(
            "microclaw.autofocus.time", types.SimpleNamespace(sleep=sleeps.append)
        )
        common = dict(
            z_range_um=4.0, z_step_um=1.0, method="sweep",
            settle_ms=0, return_thumbnail=False,
        )
        base = {"device": "lock", "property": "status", "in_focus_values": ["in"]}

        early = run_autofocus(mock_ctrl, unconstrained_guard, **common, probe=dict(base))
        assert sleeps == []
        assert early["property_dwell_ms"] == 0

        position[0] = 2.0
        sleeps.clear()
        mapped = run_autofocus(mock_ctrl, unconstrained_guard, **common,
                               probe={**base, "stop_when_found": False})
        assert sleeps == [0.5] * 5
        assert mapped["property_dwell_ms"] == 500

        position[0] = 2.0
        sleeps.clear()
        overridden = run_autofocus(mock_ctrl, unconstrained_guard, **common,
                                   probe={**base, "stop_when_found": False,
                                          "dwell_ms": 0})
        assert sleeps == []
        assert overridden["property_dwell_ms"] == 0
        assert overridden["coarse"]["readings"] == mapped["coarse"]["readings"]

        position[0] = 2.0
        sleeps.clear()
        run_autofocus(mock_ctrl, unconstrained_guard, **common,
                      probe={**base, "dwell_ms": 500})
        # Two, not five: the early stop lands on the second plane, so an
        # explicit dwell is paid only for the planes actually read.
        assert sleeps == [0.5] * 2

    def test_explicit_window_sweeps_only_that_window(
        self, mock_ctrl, unconstrained_guard
    ):
        position = [50.0]
        read_positions = []
        mock_ctrl.core.get_position.side_effect = lambda *_a: position[0]
        mock_ctrl.core.set_position.side_effect = lambda z: position.__setitem__(0, float(z))
        mock_ctrl.core.device_busy.return_value = False
        mock_ctrl.core.get_allowed_property_values.return_value = StrVector([])
        def read(*_args):
            read_positions.append(position[0])
            return "in" if 61.0 <= position[0] <= 63.0 else "out"
        mock_ctrl.core.get_property.side_effect = read

        result = run_autofocus(
            mock_ctrl, unconstrained_guard, z_min_um=60.0, z_max_um=64.0,
            z_step_um=1.0, method="sweep", settle_ms=0,
            return_thumbnail=False,
            probe={"device": "lock", "property": "status",
                   "in_focus_values": ["in"], "stop_when_found": False},
        )
        assert result["coarse"]["z_positions"] == [60.0, 61.0, 62.0, 63.0, 64.0]
        assert read_positions[0] == 60.0
        assert min(read_positions) == 60.0

    def test_range_and_explicit_window_refuse_before_motion(
        self, mock_ctrl, unconstrained_guard
    ):
        result = run_autofocus(
            mock_ctrl, unconstrained_guard, z_range_um=4.0,
            z_min_um=60.0, z_max_um=64.0, z_step_um=1.0, method="sweep",
        )
        assert "z_range_um" in result["error"]
        assert "z_min_um" in result["error"] and "z_max_um" in result["error"]
        mock_ctrl.core.set_position.assert_not_called()

    def test_stop_when_found_with_numeric_probe_refuses_before_motion(
        self, mock_ctrl, unconstrained_guard
    ):
        result = run_autofocus(
            mock_ctrl, unconstrained_guard, 4.0, 1.0, method="sweep",
            probe={"device": "PFS", "property": "Offset",
                   "stop_when_found": True},
        )
        assert "stop_when_found" in result["error"]
        assert "numeric" in result["error"]
        mock_ctrl.core.set_position.assert_not_called()

    def test_property_probe_refuses_default_coarse_then_fine_before_motion(
        self, mock_ctrl, unconstrained_guard
    ):
        result = run_autofocus(
            mock_ctrl, unconstrained_guard, 90.0, 1.0,
            probe={"device": "lock", "property": "status",
                   "in_focus_values": ["in"]},
        )
        assert "method='sweep'" in result["error"]
        assert "spends no exposures" in result["error"]
        mock_ctrl.core.set_position.assert_not_called()

    @pytest.mark.parametrize("region", [[10, 10, 64, 64], "drawn"])
    def test_property_probe_refuses_metric_region_instead_of_ignoring_it(
        self, mock_ctrl, unconstrained_guard, region
    ):
        result = run_autofocus(
            mock_ctrl, unconstrained_guard, 4.0, 1.0, method="sweep",
            region=region,
            probe={"device": "lock", "property": "status",
                   "in_focus_values": ["in"]},
        )
        assert "property" in result["error"] and "region does not apply" in result["error"]
        mock_ctrl.drawn_region.assert_not_called()
        mock_ctrl.core.set_position.assert_not_called()

    def test_quoted_json_probe_is_schema_reachable_and_runs_zero_exposure_sweep(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw.tools_schema import TOOLS
        schema = next(tool for tool in TOOLS if tool["name"] == "run_autofocus")
        probe_schema = schema["input_schema"]["properties"]["probe"]
        assert probe_schema["type"] == "object"
        assert probe_schema["required"] == ["device", "property"]

        position = [51.0]
        mock_ctrl.core.get_position.side_effect = lambda *_args: position[0]
        mock_ctrl.core.set_position.side_effect = lambda z: position.__setitem__(0, float(z))
        mock_ctrl.core.device_busy.return_value = False
        mock_ctrl.core.get_allowed_property_values.return_value = StrVector(["out", "in"])
        mock_ctrl.core.get_property.side_effect = lambda *_args: (
            "in" if 50.0 <= position[0] <= 52.0 else "out"
        )
        monkeypatch.setattr("microclaw.autofocus.STAGE_MOVE_POLL_S", 0)
        result = run_autofocus(
            mock_ctrl, unconstrained_guard, 4.0, 1.0, method="sweep",
            settle_ms=5, return_thumbnail=True,
            probe='{"device":"lock","property":"status","in_focus_values":["in"]}',
        )

        assert result["converged"] is True
        assert result["final_z_um"] == 50.0
        assert result["coarse"]["readings"] == ["out", "in"]
        assert result["coarse"]["in_range"] == [False, True]
        assert result["stopped_early"] is True
        assert result["planes_read"] == 2
        assert result["planes_planned"] == 5
        assert "metric_curve" not in result["coarse"]
        assert result["exposures_spent"] == 0
        assert result["property_dwell_ms"] == 0
        assert "suppressed" in result["thumbnail_suppressed"]
        mock_ctrl.core.snap_image.assert_not_called()

    def test_image_exposures_spent_includes_final_metric_thumbnail_snap(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        _patch_autofocus(monkeypatch)
        result = run_autofocus(
            mock_ctrl, unconstrained_guard, 10.0, 1.0, return_thumbnail=True
        )
        payload = json.loads(result[0]["text"])
        swept = len(_FAKE_AF_RESULT.coarse.z_positions) + len(
            _FAKE_AF_RESULT.fine.z_positions
        )
        assert payload["exposures_spent"] == swept + 1

    def test_early_stop_payload_never_contradicts_its_own_table(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """peak_interior must not claim "interior" about the last row read.

        An early-stopped sweep stops because it found the target, so the chosen
        plane is always the final row of the returned table. peak_interior was
        computed against the PLANNED plane count, so the payload reported True
        while its own z_positions said the plane sat at the end -- a field
        disagreeing with the table beside it, which is how block 52a's defect
        was found.
        """
        position = [51.0]
        mock_ctrl.core.get_position.side_effect = lambda *_a: position[0]
        mock_ctrl.core.set_position.side_effect = (
            lambda z: position.__setitem__(0, float(z))
        )
        mock_ctrl.core.device_busy.return_value = False
        mock_ctrl.core.get_allowed_property_values.return_value = []
        mock_ctrl.core.get_property.side_effect = lambda *_a: (
            "in" if position[0] >= 53.0 else "out"
        )
        monkeypatch.setattr("microclaw.autofocus.STAGE_MOVE_POLL_S", 0)
        result = run_autofocus(
            mock_ctrl, unconstrained_guard, 20.0, 1.0, method="sweep",
            settle_ms=5, return_thumbnail=False,
            probe={"device": "lock", "property": "status",
                   "in_focus_values": ["in"]},
        )
        coarse = result["coarse"]
        assert coarse["stopped_early"] is True
        assert coarse["planes_read"] < coarse["planes_planned"]
        assert "peak_interior" not in coarse
        assert "peak_interior does not apply" in coarse["stopping_rule"]

    @pytest.mark.parametrize("allowed, values, message", [
        (["out", "in"], None, "Name which"),
        (["out", "in"], ["typo"], "never reports"),
    ])
    def test_property_probe_value_errors_are_tool_payloads_before_motion(
        self, mock_ctrl, unconstrained_guard, allowed, values, message
    ):
        mock_ctrl.core.get_allowed_property_values.return_value = StrVector(allowed)
        spec = {"device": "lock", "property": "status"}
        if values is not None:
            spec["in_focus_values"] = values
        mock_ctrl.core.set_position.reset_mock()

        result = run_autofocus(
            mock_ctrl, unconstrained_guard, 4.0, 1.0, method="sweep",
            return_thumbnail=False, probe=spec,
        )

        assert message in result["error"]
        mock_ctrl.core.set_position.assert_not_called()

    def test_drawn_region_resolves_once_and_sets_scaled_threshold(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        _patch_autofocus(monkeypatch)
        mock_ctrl.drawn_region.return_value = [2, 3, 8, 4]
        counts = []
        real_threshold = tools.contrast_threshold
        monkeypatch.setattr(
            tools, "contrast_threshold",
            lambda count: counts.append(count) or real_threshold(count),
        )

        result = run_autofocus(
            mock_ctrl, unconstrained_guard, 2.0, 1.0, method="sweep",
            return_thumbnail=False, region="drawn",
        )

        mock_ctrl.drawn_region.assert_called_once_with()
        assert result["region"] == [2, 3, 8, 4]
        assert result["criterion"] == "max tenengrad over [2, 3, 8, 4]"
        assert counts and set(counts) == {32}

    @pytest.mark.parametrize(
        "box, frame, message_bits",
        [
            ([60, 2, 8, 4], (64, 32), ("[60, 2, 8, 4]", "[64, 32]")),
            ([2, 3, 1, 4], (64, 32), ("[2, 3, 1, 4]", "greater than 1")),
        ],
    )
    def test_drawn_stale_or_degenerate_box_refuses_before_exposure(
        self, mock_ctrl, unconstrained_guard, monkeypatch, box, frame, message_bits
    ):
        mock_ctrl.drawn_region.return_value = box
        mock_ctrl.core.get_image_width.return_value = frame[0]
        mock_ctrl.core.get_image_height.return_value = frame[1]
        sweep = MagicMock()
        monkeypatch.setattr("microclaw.tools.single_sweep_autofocus", sweep)

        result = run_autofocus(
            mock_ctrl, unconstrained_guard, 2.0, 1.0, method="sweep",
            return_thumbnail=False, region="drawn",
        )

        assert all(bit in result["error"] for bit in message_bits)
        sweep.assert_not_called()

    @pytest.mark.parametrize(
        "message", [
            "Region 'drawn' cannot be read because no Preview display is reachable. "
            "Open Preview, draw a selection, and try again.",
            "Region 'drawn' has no selection. Draw a selection on the Preview "
            "window and try again.",
        ],
    )
    def test_drawn_reader_refusal_is_returned_before_exposure(
        self, mock_ctrl, unconstrained_guard, monkeypatch, message
    ):
        mock_ctrl.drawn_region.side_effect = ValueError(message)
        sweep = MagicMock()
        monkeypatch.setattr("microclaw.tools.single_sweep_autofocus", sweep)

        result = run_autofocus(
            mock_ctrl, unconstrained_guard, 2.0, 1.0, method="sweep",
            return_thumbnail=False, region="drawn",
        )

        assert result == {"error": message}
        assert "drawn" in result["error"]
        sweep.assert_not_called()

    def test_small_region_pure_noise_does_not_converge_or_move(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        size = 20
        rng = np.random.default_rng(73)
        frames = [
            np.clip(rng.normal(1000, 25, (size, size)), 0, 65535).astype(np.uint16)
            for _ in range(5)
        ]
        metrics = [tools.tenengrad(frame) for frame in frames]
        assert curve_contrast(metrics) > 0.15
        assert 0 < int(np.argmax(metrics)) < len(metrics) - 1

        mock_ctrl.core.get_image_width.return_value = size
        mock_ctrl.core.get_image_height.return_value = size
        current_z = [50.0]
        mock_ctrl.core.get_position.side_effect = lambda *_args: current_z[0]
        monkeypatch.setattr("microclaw.controller.STAGE_MOVE_POLL_S", 0)
        monkeypatch.setattr("microclaw.controller.STAGE_MOVE_STABILITY_WINDOW_S", 0)
        mock_ctrl.core.set_position.side_effect = lambda z: current_z.__setitem__(0, z)
        frame_iter = iter(frames)
        monkeypatch.setattr(
            "microclaw.autofocus.snap_to_numpy", lambda _ctrl: next(frame_iter)
        )

        result = run_autofocus(
            mock_ctrl, unconstrained_guard, z_range_um=4.0, z_step_um=1.0,
            method="sweep", settle_ms=0, return_thumbnail=False,
            region=[0, 0, size, size],
        )

        assert current_z[0] == 50.0
        assert result["converged"] is False
        assert result["moved"] is False
        assert result["final_z_um"] == 50.0
        assert result["coarse"]["min_contrast"] > result["coarse"]["contrast"]

    def test_small_camera_roi_pure_noise_does_not_converge_or_move(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """The guard must not care HOW the metric frame got small.

        A cropped camera ROI averages the metric over just as few pixels as a
        software region does, so it reaches the same noise floor. Passing N_REF
        for the regionless case kept the threshold at 0.15 however small the
        sensor frame was, and a 32x32 crop converged on pure noise 43% of the
        time — the same defect as a small region, reached by MM's own ROI
        button instead.
        """
        size = 20
        rng = np.random.default_rng(73)
        frames = [
            np.clip(rng.normal(1000, 25, (size, size)), 0, 65535).astype(np.uint16)
            for _ in range(5)
        ]
        metrics = [tools.tenengrad(frame) for frame in frames]
        assert curve_contrast(metrics) > 0.15
        assert 0 < int(np.argmax(metrics)) < len(metrics) - 1

        mock_ctrl.core.get_image_width.return_value = size
        mock_ctrl.core.get_image_height.return_value = size
        current_z = [50.0]
        mock_ctrl.core.get_position.side_effect = lambda *_args: current_z[0]
        monkeypatch.setattr("microclaw.controller.STAGE_MOVE_POLL_S", 0)
        monkeypatch.setattr("microclaw.controller.STAGE_MOVE_STABILITY_WINDOW_S", 0)
        mock_ctrl.core.set_position.side_effect = lambda z: current_z.__setitem__(0, z)
        frame_iter = iter(frames)
        monkeypatch.setattr(
            "microclaw.autofocus.snap_to_numpy", lambda _ctrl: next(frame_iter)
        )

        result = run_autofocus(
            mock_ctrl, unconstrained_guard, z_range_um=4.0, z_step_um=1.0,
            method="sweep", settle_ms=0, return_thumbnail=False,
        )                                          # NO region — the crop is the camera's

        assert current_z[0] == 50.0
        assert result["converged"] is False
        assert result["moved"] is False
        assert result["final_z_um"] == 50.0
        assert result["coarse"]["min_contrast"] > result["coarse"]["contrast"]

    def test_region_curve_has_more_contrast_than_diluted_full_frame(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        mock_ctrl.core.get_image_width.return_value = 64
        mock_ctrl.core.get_image_height.return_value = 64
        current_z = [50.0]

        def set_position(z):
            current_z[0] = float(z)

        mock_ctrl.core.set_position.side_effect = set_position
        mock_ctrl.core.get_position.side_effect = lambda *_args: current_z[0]
        monkeypatch.setattr("microclaw.controller.STAGE_MOVE_POLL_S", 0)
        monkeypatch.setattr("microclaw.controller.STAGE_MOVE_STABILITY_WINDOW_S", 0)
        checker = (np.indices((16, 16)).sum(axis=0) % 2).astype(np.float64)
        background = np.tile(np.arange(64) % 2, (64, 1)).astype(np.float64) * 30

        def frame(_ctrl):
            image = background.copy()
            amplitude = {49.0: 10, 50.0: 100, 51.0: 10}[current_z[0]]
            image[:16, :16] = checker * amplitude
            return image

        monkeypatch.setattr("microclaw.autofocus.snap_to_numpy", frame)
        common = dict(
            z_range_um=2.0, z_step_um=1.0, method="sweep", settle_ms=0,
            return_thumbnail=False,
        )
        full = run_autofocus(mock_ctrl, unconstrained_guard, **common)
        region = run_autofocus(
            mock_ctrl, unconstrained_guard, region=[0, 0, 16, 16], **common
        )

        assert region["coarse"]["contrast"] > full["coarse"]["contrast"]

    @pytest.mark.parametrize("region, expected", [
        ([1, 2, 3], "[1, 2, 3]"),
        ([1, 2, 3.5, 4], "3.5"),
        ([-1, 2, 3, 4], "-1"),
        ([1, 2, 1, 4], "[1, 2, 1, 4]"),
        ([60, 2, 8, 4], "[64, 32]"),
    ])
    def test_invalid_region_refuses_before_the_sweep(
        self, mock_ctrl, unconstrained_guard, monkeypatch, region, expected
    ):
        mock_ctrl.core.get_image_width.return_value = 64
        mock_ctrl.core.get_image_height.return_value = 32
        sweep = MagicMock()
        monkeypatch.setattr("microclaw.tools.single_sweep_autofocus", sweep)

        result = run_autofocus(
            mock_ctrl, unconstrained_guard, 2.0, 1.0, method="sweep",
            region=region,
        )

        assert "error" in result
        assert repr(region) in result["error"] or expected in result["error"]
        assert expected in result["error"]
        sweep.assert_not_called()
    def test_z_boundary_check_below(self, mock_ctrl, default_guard):
        # current Z=5, range=20 → sweep goes to -5 which is below z_min=0
        mock_ctrl.core.get_position.return_value = 5.0
        with pytest.raises(SafetyViolation):
            run_autofocus(mock_ctrl, default_guard, z_range_um=20.0, z_step_um=1.0)

    def test_z_boundary_check_above(self, mock_ctrl, default_guard):
        # current Z=195, range=20 → sweep goes to 205 which is above z_max=200
        mock_ctrl.core.get_position.return_value = 195.0
        with pytest.raises(SafetyViolation):
            run_autofocus(mock_ctrl, default_guard, z_range_um=20.0, z_step_um=1.0)

    def test_live_stopped_and_left_off_when_on(self, mock_ctrl, unconstrained_guard, monkeypatch):
        _patch_autofocus(monkeypatch)
        mock_ctrl.studio.live().is_live_mode_on.return_value = True
        live = mock_ctrl.studio.live()
        live.set_live_mode_on.reset_mock()

        run_autofocus(mock_ctrl, unconstrained_guard, z_range_um=10.0, z_step_um=1.0)

        calls = live.set_live_mode_on.call_args_list
        assert calls[0] == call(False), "live mode must be stopped before sweep"
        assert call(True) not in calls, "an autofocus run must not restart live"

    def test_live_not_touched_when_off(self, mock_ctrl, unconstrained_guard, monkeypatch):
        _patch_autofocus(monkeypatch)
        mock_ctrl.studio.live().is_live_mode_on.return_value = False
        live = mock_ctrl.studio.live()
        live.set_live_mode_on.reset_mock()

        run_autofocus(mock_ctrl, unconstrained_guard, z_range_um=10.0, z_step_um=1.0)

        live.set_live_mode_on.assert_not_called()

    def test_small_metric_values_survive_rounding(self, mock_ctrl, unconstrained_guard, monkeypatch):
        """Regression: the normalized metric lives at 1e-2..1e-4.

        A fixed round(v, 2) — carried over from the raw metric's ~1e4 scale —
        collapsed a real focus curve to [0.0, 0.0, ...] on the demo camera,
        while `contrast` still reported a peak. Instrumentation must not lie.
        """
        tiny = SweepResult(
            z_positions=[49.0, 50.0, 51.0],
            metric_values=[0.0031234, 0.0245678, 0.0009876],
            best_z_um=50.0,
            peak_interior=True,
            measured_z_positions=[49.0, 50.0, 51.0],
        )
        monkeypatch.setattr(
            "microclaw.tools.coarse_then_fine_autofocus",
            lambda *a, **k: AutofocusResult(tiny, tiny, 50.0, 50.0, True, True, None),
        )
        result = run_autofocus(mock_ctrl, unconstrained_guard, z_range_um=10.0,
                               z_step_um=1.0, return_thumbnail=False)
        curve = result["coarse"]["metric_curve"]
        assert all(v > 0 for v in curve), f"curve annihilated by rounding: {curve}"
        assert curve[1] == pytest.approx(0.02457, rel=1e-3)

    def test_payload_reports_both_passes(self, mock_ctrl, unconstrained_guard, monkeypatch):
        _patch_autofocus(monkeypatch)
        result = run_autofocus(mock_ctrl, unconstrained_guard,
                               z_range_um=10.0, z_step_um=1.0, return_thumbnail=False)
        assert result["converged"] is True
        assert result["moved"] is True
        assert result["entry_z_um"] == 50.0
        assert result["coarse"]["metric_curve"] == [0.1, 0.9, 0.1]
        assert result["fine"]["peak_interior"] is True
        assert set(result["coarse"]) == {
            "z_positions", "measured_z_positions", "metric_curve", "best_z_um",
            "peak_interior", "contrast", "arrival_unverifiable_count",
            "arrival_unverifiable_planes"
        }
        assert "region" not in result

    def test_live_paused_across_the_sweep_and_left_off(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        # design/37 F5 asserted in behaviour, not only in the description. The
        # schema promises the operator that live view is paused for the sweep;
        # a promise in prose that no test pins to
        # the code is how F5 happened in the first place.
        # Observed from INSIDE the sweep. Asserting on the call list afterwards
        # looks equivalent and is not: run_autofocus bounces live a second time
        # for the thumbnail snap, so an after-the-fact assertion passes even
        # with the sweep's _pause_live deleted. Verified by mutation.
        live = mock_ctrl.studio.live()
        live.is_live_mode_on.return_value = True
        live.set_live_mode_on.reset_mock()
        during: list[list] = []

        def _sweep(*args, **kwargs):
            during.append(list(live.set_live_mode_on.call_args_list))
            return _FAKE_AF_RESULT

        monkeypatch.setattr("microclaw.tools.coarse_then_fine_autofocus", _sweep)
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy",
            lambda ctrl: np.zeros((64, 64), dtype=np.uint16),
        )
        monkeypatch.setattr(
            "microclaw.image_analysis.make_thumbnail", lambda img, **kwargs: ""
        )

        run_autofocus(mock_ctrl, unconstrained_guard, z_range_um=10.0, z_step_um=1.0)

        assert during, "the sweep never ran"
        assert during[0] and during[0][-1] == call(False), \
            "live must already be stopped when the sweep runs"
        assert call(True) not in live.set_live_mode_on.call_args_list, \
            "an autofocus run must leave live off after the sweep"

    def test_the_sweep_never_touches_the_viewer(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        # The other half of the same promise: "the viewer does not show the
        # sweep as it happens". snap_to_numpy_displayed is the only path that
        # would paint it, so switching to it must fail here rather than turn the
        # schema description into a lie the model keeps repeating.
        _patch_autofocus(monkeypatch)

        def _forbidden(ctrl):
            raise AssertionError(
                "run_autofocus used the DISPLAYED snap path; the schema tells "
                "the operator the sweep is headless"
            )

        monkeypatch.setattr("microclaw.tools.snap_to_numpy_displayed", _forbidden)
        run_autofocus(mock_ctrl, unconstrained_guard, z_range_um=10.0, z_step_um=1.0)

    def test_nonconverged_payload_says_stage_not_moved(self, mock_ctrl, unconstrained_guard, monkeypatch):
        flat = AutofocusResult(
            coarse=SweepResult([45.0, 50.0, 55.0], [1.0, 1.1, 1.05], 55.0, False,
                               [45.0, 50.0, 55.0]),
            fine=None, entry_z_um=50.0, final_z_um=50.0,
            converged=False, moved=False, reason="Coarse focus metric is flat",
        )
        monkeypatch.setattr("microclaw.tools.coarse_then_fine_autofocus", lambda *a, **k: flat)
        result = run_autofocus(mock_ctrl, unconstrained_guard,
                               z_range_um=10.0, z_step_um=1.0, return_thumbnail=False)
        assert result["converged"] is False
        assert result["moved"] is False
        assert "flat" in result["reason"]
        assert result["fine"] is None

    def test_live_left_off_on_sweep_exception(self, mock_ctrl, unconstrained_guard, monkeypatch):
        monkeypatch.setattr(
            "microclaw.tools.coarse_then_fine_autofocus",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("hardware fault")),
        )
        mock_ctrl.studio.live().is_live_mode_on.return_value = True
        live = mock_ctrl.studio.live()
        live.set_live_mode_on.reset_mock()

        with pytest.raises(RuntimeError):
            run_autofocus(mock_ctrl, unconstrained_guard, z_range_um=10.0, z_step_um=1.0)

        assert live.set_live_mode_on.call_args_list == [call(False)]


class TestRunMultipositionWithAutofocus:
    @pytest.fixture
    def positions(self):
        return [{"name": "P1", "x_um": 0.0, "y_um": 0.0, "z_um": 50.0}]

    @pytest.fixture
    def patched_ctrl(self, mock_ctrl, positions, tmp_path):
        from microclaw.controller import PositionProjection
        mock_ctrl.get_positions.return_value = positions
        mock_ctrl.inspect_current_position_list.return_value = PositionProjection(
            positions, positions, []
        )
        mock_ctrl.core.get_position.return_value = 50.0
        return mock_ctrl

    def test_raw_positions_forward_without_marking_and_return_deprecation(
            self, patched_ctrl, unconstrained_guard, tmp_path, monkeypatch):
        forwarded = []
        monkeypatch.setattr(
            "microclaw.tools.run_multiposition_acquisition",
            lambda *args, **kwargs: (
                forwarded.append(kwargs),
                {"dataset_path": "/ws/one-dataset", "tiles": [
                    {"position": "raw", "x_um": 3.0, "y_um": 4.0, "z_um": 50.0}
                ]},
            )[1],
        )
        result = run_multiposition_with_autofocus(
            patched_ctrl, unconstrained_guard,
            positions=[{"name": "raw", "x_um": 3.0, "y_um": 4.0, "z_um": 50.0}],
            z_range_um=10.0, z_step_um=1.0, protocol="timelapse",
            save_dir=str(tmp_path), protocol_params={"n_frames": 1, "interval_s": 0},
        )
        assert result["status"].startswith("1/1")
        assert "one dataset with a position axis" in result["deprecation"]
        assert forwarded[0]["positions"][0]["name"] == "raw"
        assert forwarded[0]["hook_strategy"] == "autofocus_per_position"
        patched_ctrl.add_position.assert_not_called()
        patched_ctrl.go_to_position.assert_not_called()

    @pytest.mark.parametrize("omitted", ["z_range_um", "z_step_um", "protocol", "save_dir"])
    def test_missing_required_argument_refuses_before_forward(
        self, patched_ctrl, unconstrained_guard, tmp_path, monkeypatch, omitted
    ):
        forward = MagicMock()
        monkeypatch.setattr("microclaw.tools.run_multiposition_acquisition", forward)
        kwargs = dict(position_names=["P1"], z_range_um=10.0, z_step_um=1.0,
                      protocol="timelapse", save_dir=str(tmp_path))
        kwargs[omitted] = None
        result = run_multiposition_with_autofocus(
            patched_ctrl, unconstrained_guard, **kwargs
        )
        assert omitted in result["error"]
        forward.assert_not_called()

    @pytest.mark.parametrize(
        ("override", "message"),
        [({"protocol": "snap"}, "display-only"),
         ({"autofocus_method": "sweep"}, "coarse_then_fine")],
    )
    def test_unsupported_legacy_modes_refuse_before_forward(
        self, patched_ctrl, unconstrained_guard, tmp_path, monkeypatch,
        override, message,
    ):
        forward = MagicMock()
        monkeypatch.setattr("microclaw.tools.run_multiposition_acquisition", forward)
        kwargs = dict(position_names=["P1"], z_range_um=10.0, z_step_um=1.0,
                      protocol="timelapse", save_dir=str(tmp_path))
        kwargs.update(override)
        result = run_multiposition_with_autofocus(
            patched_ctrl, unconstrained_guard, **kwargs
        )
        assert message in result["error"]
        forward.assert_not_called()

    def test_wrapper_resolves_save_dir_before_forward(
        self, patched_ctrl, unconstrained_guard, tmp_path, monkeypatch
    ):
        resolved = str(tmp_path.resolve())
        forward = MagicMock(return_value={"dataset_path": "/ws/ds", "tiles": []})
        monkeypatch.setattr(
            "microclaw.tools.run_multiposition_acquisition", forward
        )
        run_multiposition_with_autofocus(
            patched_ctrl, unconstrained_guard, position_names=["P1"],
            z_range_um=10.0, z_step_um=1.0, protocol="timelapse",
            save_dir=str(tmp_path), protocol_params={"n_frames": 1, "interval_s": 0},
        )
        assert forward.call_args.kwargs["save_dir"] == resolved

    def test_stored_z_out_of_bounds_refused(self, mock_ctrl, default_guard, tmp_path, monkeypatch):
        from microclaw.controller import PositionProjection
        # default_guard z_max=200; a stored Z beyond it must not move the stage.
        positions = [
            {"name": "P1", "x_um": 0.0, "y_um": 0.0, "z_um": 999.0}
        ]
        mock_ctrl.inspect_current_position_list.return_value = PositionProjection(
            positions, positions, []
        )
        monkeypatch.setattr("microclaw.tools.coarse_then_fine_autofocus", lambda *a, **k: _FAKE_AF_RESULT)
        result = run_multiposition_with_autofocus(
            mock_ctrl, default_guard,
            position_names=["P1"],
            z_range_um=10.0, z_step_um=1.0,
            protocol="timelapse", save_dir=str(tmp_path),
        )
        assert result["position_list_conflict"]["issues"][0]["code"] == "unsafe_coordinate"
        mock_ctrl.go_to_position.assert_not_called()


_TILE_IMAGE = np.full((16, 16), 700, dtype=np.uint16)
_TILE_IMAGE[0, 0] = 142      # a floor well below the mean, as on the rig
_TILE_IMAGE[8, 8] = 1182


@pytest.fixture
def fake_snap(monkeypatch):
    """Stand in for the camera, but still fire live().snap(True).

    The snap protocol reads its pixels through snap_to_numpy_displayed, which
    wraps that call — so the fake must make it too, or the exposure-count
    assertions below would pass while measuring nothing.
    """
    def _snap(ctrl):
        ctrl.studio.live().snap(True)
        return _TILE_IMAGE
    monkeypatch.setattr("microclaw.tools.snap_to_numpy_displayed", _snap)
    return _TILE_IMAGE


class TestTileGridCenter:
    """The grid used to center on wherever the stage happened to be, and to end
    on its last tile. Two "do the same thing again" runs therefore surveyed two
    different regions, offset by half a grid — invisible on a uniform sample, and
    completely invisible on the demo camera, which returns one frame regardless
    of position. A 2026-07-10 run scanned three disjoint regions and tabulated
    them as one repeated measurement."""

    @pytest.fixture(autouse=True)
    def _snap(self, fake_snap):
        pass

    @pytest.fixture
    def tracking_ctrl(self, mock_ctrl):
        """A stage that remembers where it was driven, as a real one does."""
        pos = {"x": 256.0, "y": 256.0}
        mock_ctrl.core.get_x_position.side_effect = lambda: pos["x"]
        mock_ctrl.core.get_y_position.side_effect = lambda: pos["y"]

        def _move(x, y):
            pos["x"], pos["y"] = x, y

        mock_ctrl.core.set_xy_position.side_effect = _move   # per-tile moves
        mock_ctrl.set_xy.side_effect = _move                 # the return move
        return mock_ctrl

    def _tiles(self, result):
        return [(r["x_um"], r["y_um"]) for r in result["results"]]

    def test_repeating_a_default_centered_grid_scans_the_same_tiles(
        self, tracking_ctrl, unconstrained_guard
    ):
        kwargs = dict(rows=3, cols=3, step_um=256.0, protocol="snap", name="grid")
        first = run_tile_acquisition(tracking_ctrl, unconstrained_guard, **kwargs)
        second = run_tile_acquisition(tracking_ctrl, unconstrained_guard, **kwargs)
        assert self._tiles(first) == self._tiles(second), (
            "the second grid walked off the first — this is the drift bug"
        )
        assert first["grid_center_x_um"] == second["grid_center_x_um"] == 256.0

    def test_the_stage_ends_on_the_center_not_the_last_tile(
        self, tracking_ctrl, unconstrained_guard
    ):
        run_tile_acquisition(
            tracking_ctrl, unconstrained_guard,
            rows=3, cols=3, step_um=256.0, protocol="snap",
        )
        tracking_ctrl.set_xy.assert_called_once_with(256.0, 256.0)
        assert tracking_ctrl.core.get_x_position() == 256.0
        assert tracking_ctrl.core.get_y_position() == 256.0

    def test_an_explicit_center_pins_the_grid_regardless_of_stage_position(
        self, tracking_ctrl, unconstrained_guard
    ):
        # Reproducing an earlier scan: the stage is parked somewhere else entirely.
        tracking_ctrl.core.set_xy_position(9000.0, 9000.0)
        result = run_tile_acquisition(
            tracking_ctrl, unconstrained_guard,
            rows=3, cols=3, step_um=100.0, protocol="snap",
            center_x_um=500.0, center_y_um=600.0,
        )
        assert result["grid_center_x_um"] == 500.0
        assert result["grid_center_y_um"] == 600.0
        assert (500.0, 600.0) in self._tiles(result), "center tile of an odd grid"
        assert self._tiles(result)[0] == (400.0, 500.0)
        tracking_ctrl.set_xy.assert_called_once_with(500.0, 600.0)

    def test_grid_center_source_reports_default_explicit_and_partial_truthfully(
        self, tracking_ctrl, unconstrained_guard
    ):
        default = run_tile_acquisition(
            tracking_ctrl, unconstrained_guard,
            rows=1, cols=1, step_um=100.0, protocol="snap",
        )
        explicit = run_tile_acquisition(
            tracking_ctrl, unconstrained_guard,
            rows=1, cols=1, step_um=100.0, protocol="snap",
            center_x_um=500.0, center_y_um=600.0,
        )
        partial = run_tile_acquisition(
            tracking_ctrl, unconstrained_guard,
            rows=1, cols=1, step_um=100.0, protocol="snap",
            center_x_um=700.0,
        )
        assert default["grid_center_source"] == "current_stage_position"
        assert explicit["grid_center_source"] == "explicit"
        assert partial["grid_center_source"] == "partially_explicit"

    def test_an_out_of_bounds_supplied_center_is_refused_before_any_motion(
        self, tracking_ctrl, default_guard
    ):
        # An even-sided grid puts the center between tiles, so checking the tiles
        # does not check it. Refuse before the first move, not on the way home.
        with pytest.raises(SafetyViolation):
            run_tile_acquisition(
                tracking_ctrl, default_guard,
                rows=2, cols=2, step_um=10.0, protocol="snap",
                center_x_um=1e9, center_y_um=1e9,
            )
        tracking_ctrl.core.set_xy_position.assert_not_called()
        tracking_ctrl.set_xy.assert_not_called()

    def test_return_to_center_can_be_declined(self, tracking_ctrl, unconstrained_guard):
        run_tile_acquisition(
            tracking_ctrl, unconstrained_guard,
            rows=2, cols=2, step_um=100.0, protocol="snap",
            return_to_center=False,
        )
        tracking_ctrl.set_xy.assert_not_called()
        assert tracking_ctrl.core.get_x_position() == 306.0, "left on the last tile"


class TestTileAcquisitionMarkPositions:
    @pytest.fixture(autouse=True)
    def _snap(self, fake_snap):
        pass

    @pytest.fixture
    def centered_ctrl(self, mock_ctrl):
        # Park the stage; the fake moves when it is written to, so pinning the
        # reads instead would model a stage that never responds.
        mock_ctrl.core.xy_position.update(x=256.0, y=256.0)
        return mock_ctrl

    def test_snap_grid_marks_positions_without_save_dir(self, centered_ctrl, unconstrained_guard):
        result = run_tile_acquisition(
            centered_ctrl, unconstrained_guard,
            rows=3, cols=3, step_um=256.0,
            protocol="snap", name="grid", mark_positions=True,
        )
        assert result["status"] == "9/9 positions completed."
        assert centered_ctrl.add_position.call_count == 9
        assert centered_ctrl.studio.live().snap.call_count == 9
        assert all(r.get("marked") for r in result["results"])

    def test_tile_labels_are_name_prefixed(self, centered_ctrl, unconstrained_guard):
        run_tile_acquisition(
            centered_ctrl, unconstrained_guard,
            rows=2, cols=2, step_um=100.0,
            protocol="snap", name="grid", mark_positions=True,
        )
        labels = [c.args[0] for c in centered_ctrl.add_position.call_args_list]
        assert labels == ["grid_r0_c0", "grid_r0_c1", "grid_r1_c0", "grid_r1_c1"]

    def test_no_marking_by_default(self, centered_ctrl, unconstrained_guard):
        result = run_tile_acquisition(
            centered_ctrl, unconstrained_guard,
            rows=2, cols=2, step_um=100.0, protocol="snap",
        )
        assert result["status"] == "4/4 positions completed."
        centered_ctrl.add_position.assert_not_called()
        assert all("marked" not in r for r in result["results"])

    def test_marked_position_includes_z_when_given(self, mock_ctrl, unconstrained_guard):
        run_multiposition_acquisition(
            mock_ctrl, unconstrained_guard,
            protocol="snap",
            positions=[{"name": "P1", "x_um": 1.0, "y_um": 2.0, "z_um": 3.0}],
            mark_positions=True,
        )
        mock_ctrl.add_position.assert_called_once_with("P1", 1.0, 2.0, 3.0)

    def test_save_dir_required_for_saving_protocols(self, mock_ctrl, unconstrained_guard):
        result = run_multiposition_acquisition(
            mock_ctrl, unconstrained_guard,
            protocol="zstack",
            positions=[{"name": "P1", "x_um": 0.0, "y_um": 0.0}],
        )
        assert "save_dir is required" in result["error"]

    def test_snap_does_not_create_save_dirs(self, mock_ctrl, unconstrained_guard, tmp_path):
        save_dir = tmp_path / "snaps"
        run_multiposition_acquisition(
            mock_ctrl, unconstrained_guard,
            protocol="snap",
            save_dir=str(save_dir),
            positions=[{"name": "P1", "x_um": 0.0, "y_um": 0.0}],
        )
        assert not save_dir.exists()

    def test_snap_grid_returns_stats_per_tile(self, centered_ctrl, unconstrained_guard):
        # design/20 F1. The snap branch fired the camera and dropped the pixels,
        # so "scan a grid and tell me the max and min at each point" — the
        # literal prompt, twice — had no tool that answered it.
        result = run_tile_acquisition(
            centered_ctrl, unconstrained_guard, rows=2, cols=2, step_um=100.0,
            protocol="snap",
        )
        assert len(result["results"]) == 4
        for tile in result["results"]:
            assert tile["min_intensity"] == 142.0
            assert tile["max_intensity"] == 1182.0
            assert tile["mean_intensity"] == pytest.approx(700, abs=5)
            assert tile["focus_metric"] >= 0.0
            assert tile["saved"] is False

    def test_snap_grid_gates_focus_metric_on_signal(self, centered_ctrl,
                                                    unconstrained_guard, monkeypatch):
        # design/25: this per-tile branch is the one that broke the Nestor run —
        # ranking these focus_metric floats against each other walked the stage to
        # the emptiest field on the grid. An empty tile must carry
        # focus_metric_valid False and a warning, so the number can't be ranked
        # out of context. Empty (no signal) vs a punctate cell, same grid call.
        rng = np.random.default_rng(0)
        empty = (400 + rng.normal(0, 10, (64, 64))).astype(np.uint16)
        monkeypatch.setattr("microclaw.tools.snap_to_numpy_displayed", lambda ctrl: empty)
        tile = run_tile_acquisition(
            centered_ctrl, unconstrained_guard, rows=1, cols=1, step_um=1.0,
            protocol="snap",
        )["results"][0]
        assert tile["focus_metric_valid"] is False
        assert "warning" in tile and "noise floor" in tile["warning"]
        assert "snr" in tile

    def test_snap_grid_uses_the_same_rig_gate(self, centered_ctrl, monkeypatch):
        guard = SafetyGuard(SafetyConstraints(
            stage=StageConstraints(x_min=0, x_max=512, y_min=0, y_max=512),
            analysis=AnalysisConstraints(min_snr=999.0),
        ))
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy_displayed",
            lambda ctrl: np.full((32, 32), 400, dtype=np.uint16),
        )
        tile = run_tile_acquisition(
            centered_ctrl, guard, rows=1, cols=1, step_um=1.0, protocol="snap"
        )["results"][0]
        assert tile["min_snr"] == 999.0
        assert tile["min_snr_source"] == "rig_config"

    def test_every_row_carries_its_own_coordinates(self, centered_ctrl,
                                                   unconstrained_guard):
        # The agent filled X/Y columns from its own call ordering rather than
        # from any tool result (design/19 F3, design/20 S1). Now the row has them.
        result = run_tile_acquisition(
            centered_ctrl, unconstrained_guard, rows=1, cols=2, step_um=100.0,
            protocol="snap", name="grid",
        )
        assert [(r["position"], r["x_um"], r["y_um"]) for r in result["results"]] == [
            ("grid_r0_c0", 206.0, 256.0),
            ("grid_r0_c1", 306.0, 256.0),
        ]

    def test_error_rows_carry_coordinates_too(self, centered_ctrl, default_guard):
        # An error row without coordinates is a row the agent will fill in itself.
        result = run_tile_acquisition(
            centered_ctrl, default_guard, rows=1, cols=3, step_um=2000.0,
            protocol="snap",
        )
        errors = [r for r in result["results"] if "error" in r]
        assert errors
        assert all("x_um" in r and "y_um" in r for r in errors)

    def test_the_stats_cost_no_extra_exposure(self, centered_ctrl, unconstrained_guard):
        # snap(True) already returns the pixels; reading them must not re-fire.
        run_tile_acquisition(
            centered_ctrl, unconstrained_guard, rows=2, cols=2, step_um=100.0,
            protocol="snap",
        )
        assert centered_ctrl.studio.live().snap.call_count == 4

    def test_the_metric_stamp_is_hoisted_to_the_grid_not_repeated(
        self, centered_ctrl, unconstrained_guard
    ):
        # Every tile shares one ROI/exposure/binning. Per-tile stamps would be
        # four copies of one fact; a bare metric with no stamp anywhere invites
        # the cross-setting comparison design/14 §10 warns about.
        result = run_tile_acquisition(
            centered_ctrl, unconstrained_guard, rows=2, cols=2, step_um=100.0,
            protocol="snap",
        )
        assert result["focus_metric_kind"] == "tenengrad_gated"
        assert "metric_valid_for" in result
        for tile in result["results"]:
            assert "metric_valid_for" not in tile
            assert "focus_metric" in tile

    def test_a_failed_snap_grid_carries_no_stamp(self, centered_ctrl, unconstrained_guard,
                                                 monkeypatch):
        # The stamp describes measurements. With none taken it would describe
        # nothing, and a stamp beside zero results reads as if it did.
        def _boom(ctrl):
            raise RuntimeError("camera offline")
        monkeypatch.setattr("microclaw.tools.snap_to_numpy_displayed", _boom)
        result = run_tile_acquisition(
            centered_ctrl, unconstrained_guard, rows=1, cols=2, step_um=100.0,
            protocol="snap",
        )
        assert result["status"] == "0/2 positions completed."
        assert "metric_valid_for" not in result

    def test_out_of_bounds_tile_reported_not_moved(self, centered_ctrl, default_guard):
        # default_guard: |x|,|y| <= 1000; a 3x3 grid with step 2000 exceeds it.
        result = run_tile_acquisition(
            centered_ctrl, default_guard,
            rows=1, cols=3, step_um=2000.0,
            protocol="snap", mark_positions=True,
        )
        errors = [r for r in result["results"] if "error" in r]
        assert errors, "out-of-bounds tiles must surface as per-position errors"
        assert centered_ctrl.add_position.call_count < 3


class _RecordingHook:
    """Stands in for a generated per-image hook: logs whatever axes it's given."""

    instances: list = []

    def __init__(self, log_path=None):
        self.log_path = log_path
        self._log = []
        _RecordingHook.instances.append(self)

    def image_process_fn(self, image, metadata, event_queue):
        self._log.append({"position": metadata["Axes"].get("position")})
        return image, metadata

    def get_summary(self):
        return self._log


class TestHookedGridAcquisition:
    """design/19 F2/F3: a grid with a hook is ONE acquisition over all positions,
    not N degenerate single-plane z-stacks. The agent spelled a 3x3 grid as nine
    run_zstack calls with z_start == z_end, because no grid tool took a
    hook — nine datasets and nine logs to recover nine numbers."""

    @pytest.fixture
    def centered_ctrl(self, mock_ctrl):
        mock_ctrl.core.xy_position.update(x=0.0, y=0.0)
        return mock_ctrl

    @pytest.fixture
    def captured(self, monkeypatch):
        from microclaw import tools
        from microclaw.hooks import PRECODED_HOOK_REGISTRY

        _RecordingHook.instances = []
        monkeypatch.setitem(PRECODED_HOOK_REGISTRY, "recording", _RecordingHook)
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

    def test_one_acquisition_spans_the_whole_grid(self, centered_ctrl,
                                                  unconstrained_guard, captured,
                                                  tmp_path):
        # A real directory, because run_tile_acquisition now creates the log's
        # parent. os.path.join over its parts, not "/": the guard normalises the
        # path it echoes back, and on Windows that means backslashes. A
        # hardcoded "/ws/log.json" asserts the platform, not the round trip.
        log_path = os.path.join(str(tmp_path), "logs", "log.json")
        result = run_tile_acquisition(
            centered_ctrl, unconstrained_guard, rows=3, cols=3, step_um=256.0,
            protocol="timelapse", save_dir="/ws", name="grid",
            protocol_params={"n_frames": 1, "interval_s": 0},
            hook_strategy="recording", log_path=log_path,
        )
        assert len(captured) == 1, "a grid is one Acquisition, not nine"
        assert len(_RecordingHook.instances) == 1, "one hook instance, one log"
        labels = [e["axes"]["position"] for e in captured[0]["events"]]
        assert len(labels) == 9 and len(set(labels)) == 9
        assert labels[0] == "grid_r0_c0" and labels[-1] == "grid_r2_c2"
        assert result["log_path"] == log_path
        # "Adaptive acquisition complete." over a grid gave the agent no way to
        # confirm every tile fired without opening the log.
        assert result["positions"] == 9
        assert "9 position(s)" in result["status"]

    def test_a_log_path_in_a_missing_directory_is_created_not_raised(
        self, centered_ctrl, unconstrained_guard, captured, tmp_path
    ):
        # The hook opens its log on the FIRST FRAME, so a missing parent used to
        # surface as FileNotFoundError inside the image processor — after the
        # stage had walked the grid and a dataset was on disk. The run was spent
        # by the time the error arrived. save_dir is created up front; so is this.
        log_path = os.path.join(str(tmp_path), "nope", "deeper", "log.json")
        result = run_tile_acquisition(
            centered_ctrl, unconstrained_guard, rows=2, cols=2, step_um=100.0,
            protocol="timelapse", save_dir="/ws", name="grid",
            protocol_params={"n_frames": 1, "interval_s": 0},
            hook_strategy="recording", log_path=log_path,
        )
        assert os.path.isdir(os.path.dirname(log_path))
        assert result["log_path"] == log_path

    @pytest.mark.parametrize("fail_at", [1, 5])
    def test_failure_returns_resolved_dataset_and_log_paths(
        self, centered_ctrl, unconstrained_guard, monkeypatch, tmp_path, fail_at
    ):
        from microclaw import tools
        from microclaw.hooks import PRECODED_HOOK_REGISTRY

        monkeypatch.setitem(PRECODED_HOOK_REGISTRY, "recording", _RecordingHook)
        resolved_dir = tmp_path / "grid_2"
        resolved_dir.mkdir()
        resolved_path = str(resolved_dir)

        class FailingAcquisition:
            def __init__(self, **kwargs):
                self._dataset_disk_location = resolved_path

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def acquire(self, events):
                for frame, _event in enumerate(events, start=1):
                    if frame == fail_at:
                        raise RuntimeError(f"forced failure at frame {frame}")

        monkeypatch.setattr(tools, "Acquisition", FailingAcquisition)
        log_path = str(tmp_path / "grid-hook.json")
        positions = [
            {"name": f"p{i}", "x_um": float(i), "y_um": 0.0}
            for i in range(5)
        ]
        result = run_multiposition_acquisition(
            centered_ctrl, unconstrained_guard, protocol="timelapse",
            save_dir=str(tmp_path), name="grid", positions=positions,
            protocol_params={"n_frames": 1, "interval_s": 0},
            hook_strategy="recording", log_path=log_path,
        )

        assert result["error"] == f"forced failure at frame {fail_at}"
        assert result["dataset_path"] == resolved_path
        assert result["artifact"]["path"] == resolved_path
        assert result["log_path"] == log_path
        # Returning a dict bypasses the tool wrapper's hint_for_error, so the
        # "already exposed" warning has to travel in the payload itself.
        assert "stage has already moved" in result["hint"]

    @pytest.mark.parametrize("live_on", [True, False])
    def test_composed_acquisition_leaves_live_off_and_reports_how_to_restart(
        self, centered_ctrl, unconstrained_guard, monkeypatch, tmp_path, live_on
    ):
        from microclaw import tools
        from microclaw.hooks import PRECODED_HOOK_REGISTRY

        monkeypatch.setitem(PRECODED_HOOK_REGISTRY, "recording", _RecordingHook)

        class SuccessfulAcquisition:
            def __init__(self, **_kwargs):
                self._dataset_disk_location = str(tmp_path / "grid")

            def __enter__(self): return self
            def __exit__(self, *_exc): return False
            def acquire(self, _events): return None

        monkeypatch.setattr(tools, "Acquisition", SuccessfulAcquisition)
        live = centered_ctrl.studio.live()
        live.is_live_mode_on.return_value = live_on
        live.set_live_mode_on.reset_mock()

        result = run_multiposition_acquisition(
            centered_ctrl, unconstrained_guard, protocol="timelapse",
            save_dir=str(tmp_path), positions=[{"name": "p0", "x_um": 0, "y_um": 0}],
            protocol_params={"n_frames": 1, "interval_s": 0},
            hook_strategy="recording",
        )

        assert "error" not in result
        if live_on:
            assert live.set_live_mode_on.call_args_list == [call(False)]
            assert result["live_view_restore"]["requested"] is False
            assert result["live_view_restore"]["left_off"] is True
            assert "start_live_view" in result["live_view_restore"]["reason"]
        else:
            live.set_live_mode_on.assert_not_called()
            assert "live_view_restore" not in result

    def test_composed_path_leaves_live_off_when_acquisition_raises(
        self, centered_ctrl, unconstrained_guard, monkeypatch, tmp_path
    ):
        from microclaw import tools
        from microclaw.hooks import PRECODED_HOOK_REGISTRY

        monkeypatch.setitem(PRECODED_HOOK_REGISTRY, "recording", _RecordingHook)

        class FailingAcquisition:
            def __init__(self, **_kwargs):
                self._dataset_disk_location = str(tmp_path / "grid")

            def __enter__(self): return self
            def __exit__(self, *_exc): return False
            def acquire(self, _events): raise RuntimeError("forced acquisition failure")

        monkeypatch.setattr(tools, "Acquisition", FailingAcquisition)
        live = centered_ctrl.studio.live()
        live.is_live_mode_on.return_value = True
        live.set_live_mode_on.reset_mock()

        result = run_multiposition_acquisition(
            centered_ctrl, unconstrained_guard, protocol="timelapse",
            save_dir=str(tmp_path), positions=[{"name": "p0", "x_um": 0, "y_um": 0}],
            protocol_params={"n_frames": 1, "interval_s": 0},
            hook_strategy="recording",
        )

        assert result["error"] == "forced acquisition failure"
        assert live.set_live_mode_on.call_args_list == [call(False)]
        assert result["live_view_restore"]["left_off"] is True

    def test_autofocus_and_observer_compose_end_to_end_in_one_acquisition(
        self, centered_ctrl, unconstrained_guard, monkeypatch, tmp_path
    ):
        from microclaw import tools

        monkeypatch.setattr(
            "microclaw.autofocus.coarse_then_fine_autofocus",
            lambda *_args, **_kwargs: _FAKE_AF_RESULT,
        )
        acquisitions = []

        class DrivingAcquisition:
            def __init__(self, **kwargs):
                self.callbacks = kwargs
                self._dataset_disk_location = str(tmp_path / "composed-dataset")
                acquisitions.append(self)

            def __enter__(self): return self
            def __exit__(self, *_exc): return False

            def acquire(self, events):
                for event in events:
                    event = self.callbacks["post_hardware_hook_fn"](event)
                    metadata = {"Axes": event["axes"],
                                "PositionName": event["axes"]["position"]}
                    returned = self.callbacks["image_process_fn"](
                        np.full((4, 4), 500, dtype=np.uint16), metadata, object()
                    )
                    if returned is not None:
                        self.callbacks["image_saved_fn"](event["axes"], object())

        monkeypatch.setattr(tools, "Acquisition", DrivingAcquisition)
        log_path = str(tmp_path / "composed-log.json")
        result = run_multiposition_acquisition(
            centered_ctrl, unconstrained_guard, protocol="timelapse",
            save_dir=str(tmp_path), name="composed",
            positions=[
                {"name": "p0", "x_um": 0.0, "y_um": 0.0},
                {"name": "p1", "x_um": 1.0, "y_um": 0.0},
            ],
            protocol_params={"n_frames": 1, "interval_s": 0},
            hook_strategy=["autofocus_per_position", "snr_observer"],
            hook_params=[{"z_range_um": 10.0, "z_step_um": 1.0}, {}],
            log_path=log_path,
        )

        assert len(acquisitions) == 1
        assert result["dataset_path"] == str(tmp_path / "composed-dataset")
        assert result["positions_completed"] == 2
        # Per event: 1 stored frame + 3 coarse + 11 worst-case fine planes.
        assert result["reservation_frames_planned"] == 30
        assert result["hook_extra_exposures_planned"] == 28
        log = json.loads(Path(log_path).read_text(encoding="utf-8"))
        by_hook = {entry["hook_strategy"] for entry in log}
        assert by_hook == {"autofocus_per_position", "snr_observer"}
        assert sum("best_z_um" in entry for entry in log) == 2
        assert sum(entry.get("analyzer") == "microclaw.image_analysis.compute_stats"
                   for entry in log) == 2

    def test_composed_emitter_without_budget_refuses_before_any_exposure(
        self, centered_ctrl, unconstrained_guard, monkeypatch, tmp_path
    ):
        from microclaw import hook_manager, tools

        monkeypatch.setattr(hook_manager, "HOOKS_DIR", tmp_path / "hooks")
        monkeypatch.setattr(hook_manager, "MANIFEST", tmp_path / "manifest.json")
        source = (
            "from microclaw.hook_decisions import EmitArtifact, HookResult\n"
            "class Stitcher:\n"
            " def analyze_frame(self, image, metadata):\n"
            "  return HookResult({}, (EmitArtifact(filename='mosaic.tif', payload=image),))\n"
        )
        hook_manager.save_hook(
            "plus_mosaic_stitcher", source, "fixture", source="claude_generated"
        )
        acquire = MagicMock()
        monkeypatch.setattr(tools, "_acquire_with_hooks", acquire)
        centered_ctrl.core.snap_image.reset_mock()

        result = run_multiposition_acquisition(
            centered_ctrl, unconstrained_guard, protocol="timelapse",
            save_dir=str(tmp_path), name="composed",
            positions=[{"name": "p0", "x_um": 0.0, "y_um": 0.0}],
            protocol_params={"n_frames": 1, "interval_s": 0},
            hook_strategy=["autofocus_per_position", "plus_mosaic_stitcher"],
            hook_params=[{"z_range_um": 10.0, "z_step_um": 1.0}, {}],
        )

        assert "no artifact_limits budget" in result["error"]
        assert "before any exposure" in result["error"]
        assert "plus_mosaic_stitcher" in result["error"]
        acquire.assert_not_called()
        centered_ctrl.core.snap_image.assert_not_called()

    def test_the_hook_log_keys_to_positions_across_the_grid(
        self, centered_ctrl, unconstrained_guard, captured
    ):
        # F3: every entry in the agent's nine logs read position_index: null,
        # x_um: null, y_um: null — it guessed "XPosition_um_Intended", a key
        # that was never there. A real multi-position event carries the label.
        run_tile_acquisition(
            centered_ctrl, unconstrained_guard, rows=2, cols=2, step_um=100.0,
            protocol="timelapse", save_dir="/ws", name="grid",
            protocol_params={"n_frames": 1, "interval_s": 0},
            hook_strategy="recording",
        )
        hook = captured[0]["hook"]
        for event in captured[0]["events"]:
            hook.image_process_fn(np.zeros((4, 4)), {"Axes": event["axes"]}, None)
        logged = [entry["position"] for entry in hook.get_summary()]
        assert logged == ["grid_r0_c0", "grid_r0_c1", "grid_r1_c0", "grid_r1_c1"]
        assert not any(p is None for p in logged)

    def test_zstack_shape_composes_with_positions(self, centered_ctrl,
                                                  unconstrained_guard, captured):
        run_tile_acquisition(
            centered_ctrl, unconstrained_guard, rows=1, cols=2, step_um=100.0,
            protocol="zstack", save_dir="/ws",
            protocol_params={"z_start_um": 10.0, "z_end_um": 12.0, "z_step_um": 1.0},
            hook_strategy="recording",
        )
        events = captured[0]["events"]
        assert len(events) == 6, "2 positions x 3 z planes in one event list"
        assert {e["z"] for e in events} == {10, 11, 12}, "z_start_um is absolute"

    def test_a_zstack_range_stays_absolute_when_positions_carry_z(
        self, mock_ctrl, unconstrained_guard, captured
    ):
        # multi_d_acquisition_events reads z_start/z_end as offsets RELATIVE to
        # each point when handed xyz_positions. The unhooked loop sweeps the
        # absolute range, so the hooked path must too — otherwise the same
        # protocol_params mean different planes depending on hook_strategy.
        run_multiposition_acquisition(
            mock_ctrl, unconstrained_guard, protocol="zstack", save_dir="/ws",
            positions=[{"name": "P1", "x_um": 0.0, "y_um": 0.0, "z_um": 80.0}],
            protocol_params={"z_start_um": 10.0, "z_end_um": 12.0, "z_step_um": 1.0},
            hook_strategy="recording",
        )
        assert {e["z"] for e in captured[0]["events"]} == {10, 11, 12}

    def test_an_out_of_bounds_z_range_refuses_before_the_acquisition(
        self, mock_ctrl, default_guard, captured
    ):
        # default_guard: 0 <= z <= 200. run_zstack guards its range; so must this.
        with pytest.raises(SafetyViolation):
            run_multiposition_acquisition(
                mock_ctrl, default_guard, protocol="zstack", save_dir="/ws",
                positions=[{"name": "P1", "x_um": 0.0, "y_um": 0.0}],
                protocol_params={"z_start_um": 0.0, "z_end_um": 900.0,
                                 "z_step_um": 1.0},
                hook_strategy="recording",
            )
        assert not captured

    def test_out_of_bounds_tile_refuses_before_the_acquisition(
        self, centered_ctrl, default_guard, captured
    ):
        # pycro-manager drives the stage, so there is no per-move guard call.
        # Every point is checked up front: the refusal must not arrive on tile
        # 7 of 9 with the objective already out over the sample.
        with pytest.raises(SafetyViolation):
            run_tile_acquisition(
                centered_ctrl, default_guard, rows=1, cols=3, step_um=2000.0,
                protocol="timelapse", save_dir="/ws",
                protocol_params={"n_frames": 1, "interval_s": 0},
                hook_strategy="recording",
            )
        assert not captured, "no Acquisition may be constructed after a refusal"

    def test_hooked_snap_is_a_hard_error_not_a_stack_trace(
        self, centered_ctrl, unconstrained_guard, captured
    ):
        result = run_tile_acquisition(
            centered_ctrl, unconstrained_guard, rows=2, cols=2, step_um=100.0,
            protocol="snap", hook_strategy="recording",
        )
        assert "display-only" in result["error"]
        assert "n_frames" in result["error"], "the error must name the fix"
        assert not captured
        centered_ctrl.studio.live().snap.assert_not_called()

    def test_positions_are_marked_before_the_acquisition(
        self, centered_ctrl, unconstrained_guard, captured
    ):
        # The visit loop is gone, so marking can no longer ride along with it.
        run_tile_acquisition(
            centered_ctrl, unconstrained_guard, rows=2, cols=2, step_um=100.0,
            protocol="timelapse", save_dir="/ws", name="grid",
            protocol_params={"n_frames": 1, "interval_s": 0},
            hook_strategy="recording", mark_positions=True,
        )
        labels = [c.args[0] for c in centered_ctrl.add_position.call_args_list]
        assert labels == ["grid_r0_c0", "grid_r0_c1", "grid_r1_c0", "grid_r1_c1"]

    def test_explicit_z_becomes_an_xyz_position(self, mock_ctrl,
                                                unconstrained_guard, captured):
        run_multiposition_acquisition(
            mock_ctrl, unconstrained_guard, protocol="timelapse", save_dir="/ws",
            positions=[{"name": "P1", "x_um": 1.0, "y_um": 2.0, "z_um": 3.0}],
            protocol_params={"n_frames": 1, "interval_s": 0},
            hook_strategy="recording",
        )
        event = captured[0]["events"][0]
        assert (event["x"], event["y"], event["z"]) == (1.0, 2.0, 3.0)

    def test_unknown_hook_strategy_returns_an_error(self, centered_ctrl,
                                                    unconstrained_guard, captured):
        result = run_tile_acquisition(
            centered_ctrl, unconstrained_guard, rows=1, cols=1, step_um=1.0,
            protocol="timelapse", save_dir="/ws",
            protocol_params={"n_frames": 1, "interval_s": 0},
            hook_strategy="no_such_hook",
        )
        assert "Unknown hook strategy" in result["error"]
        assert not captured

    def test_hooked_branch_echoes_per_tile_coordinates(
        self, centered_ctrl, unconstrained_guard, captured
    ):
        # design/23 F1 / Episode A: the hooked branch used to drop the exact
        # per-tile coordinates it computed, so "where was tile r2_c1?" had no
        # answer short of re-imaging the grid. Now it echoes a `tiles` key.
        result = run_tile_acquisition(
            centered_ctrl, unconstrained_guard, rows=3, cols=3, step_um=100.0,
            protocol="timelapse", save_dir="/ws", name="grid",
            protocol_params={"n_frames": 1, "interval_s": 0},
            hook_strategy="recording",
        )
        tiles = result["tiles"]
        assert len(tiles) == 9
        assert [t["position"] for t in tiles][:2] == ["grid_r0_c0", "grid_r0_c1"]
        # Center is (0, 0), 3x3 at 100 µm → corners at ±100.
        corner = tiles[0]
        assert (corner["x_um"], corner["y_um"]) == (-100.0, -100.0)

    def test_hooked_and_unhooked_agree_on_where_they_went(
        self, centered_ctrl, unconstrained_guard, captured, fake_snap
    ):
        # The assertion worth having (design/23 F1): the hooked `tiles` and the
        # unhooked per-row coordinates describe the SAME ground for one grid.
        grid = dict(rows=2, cols=3, step_um=100.0, name="grid")
        hooked = run_tile_acquisition(
            centered_ctrl, unconstrained_guard, protocol="timelapse", save_dir="/ws",
            protocol_params={"n_frames": 1, "interval_s": 0},
            hook_strategy="recording", **grid,
        )
        unhooked = run_tile_acquisition(
            centered_ctrl, unconstrained_guard, protocol="snap", **grid,
        )
        hooked_xy = {(t["position"], t["x_um"], t["y_um"]) for t in hooked["tiles"]}
        unhooked_xy = {(r["position"], r["x_um"], r["y_um"]) for r in unhooked["results"]}
        assert hooked_xy == unhooked_xy

    def test_unhooked_tiles_keep_the_per_position_loop(self, centered_ctrl,
                                                       unconstrained_guard, captured,
                                                       fake_snap):
        # Option A's behaviour is what today's users have; only hook_strategy
        # switches to the single-Acquisition path.
        result = run_tile_acquisition(
            centered_ctrl, unconstrained_guard, rows=2, cols=2, step_um=100.0,
            protocol="snap",
        )
        assert result["status"] == "4/4 positions completed."
        assert centered_ctrl.studio.live().snap.call_count == 4
        assert not captured


class TestRunTimelapseExposure:
    def test_sets_exposure_when_no_channel(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw.tools import run_timelapse
        monkeypatch.setattr("microclaw.tools._acquire_with_hooks", lambda *a, **k: "/tmp/ds")
        run_timelapse(mock_ctrl, unconstrained_guard, n_frames=1, interval_s=0.0,
                      save_dir="/tmp", exposure_ms=50.0)
        mock_ctrl.core.set_exposure.assert_called_once_with(50.0)

    def test_no_exposure_write_when_channel_given(self, mock_ctrl, default_guard, monkeypatch):
        from microclaw.tools import run_timelapse
        monkeypatch.setattr("microclaw.tools._acquire_with_hooks", lambda *a, **k: "/tmp/ds")
        run_timelapse(mock_ctrl, default_guard, n_frames=1, interval_s=0.0,
                      save_dir="/tmp", channel="DAPI", exposure_ms=50.0)
        mock_ctrl.core.set_exposure.assert_not_called()


def _puncta_image(spot_yx=(80, 30), shape=(128, 128)):
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    img = np.full(shape, 400, dtype=np.float32)
    img += 5000 * np.exp(-((yy - spot_yx[0]) ** 2 + (xx - spot_yx[1]) ** 2) / 8.0)
    return img.astype(np.uint16)


class TestFindFeatures:
    def test_operator_started_live_is_restored_and_sequence_verified(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw.tools import find_features
        monkeypatch.setattr("microclaw.tools.snap_to_numpy", lambda ctrl: _puncta_image())
        monkeypatch.setattr("microclaw.tools._resolve_current_affine", lambda ctrl: (None, {}))
        live = mock_ctrl.studio.live()
        live.is_live_mode_on.return_value = True
        live.set_live_mode_on.reset_mock()
        mock_ctrl.core.is_sequence_running.return_value = True

        result = find_features(mock_ctrl, unconstrained_guard)

        assert live.set_live_mode_on.call_args_list == [call(False), call(True)]
        assert result["live_view_restore"]["requested"] is True
        assert result["live_view_restore"]["sequence_running"] is True
        mock_ctrl.core.is_sequence_running.assert_called()

    def test_reports_um_offsets_when_calibrated(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw.calibration import StageCameraAffine
        from microclaw.tools import find_features
        monkeypatch.setattr("microclaw.tools.snap_to_numpy", lambda ctrl: _puncta_image())
        monkeypatch.setattr(
            "microclaw.tools._resolve_current_affine",
            lambda ctrl: (StageCameraAffine(0.5, 0.0, 0.0, 0.5, "obj", 1, 0.5), {}),
        )
        mock_ctrl.core.get_pixel_size_um.return_value = 0.5
        result = find_features(mock_ctrl, unconstrained_guard)
        # The µm figure is a stage MOVE that centres the brightest punctum, not
        # a distance and not the aggregate centroid (design/64).
        assert "offset_from_center_um" not in result
        brightest = result["brightest_feature_offset_px"]
        assert result["centering_move_um"] == [
            pytest.approx(brightest[0] * 0.5, abs=0.1),
            pytest.approx(brightest[1] * 0.5, abs=0.1),
        ]
        assert "spot_density_per_um2" in result

    def test_notes_missing_calibration(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw.tools import find_features
        monkeypatch.setattr("microclaw.tools.snap_to_numpy", lambda ctrl: _puncta_image())
        monkeypatch.setattr("microclaw.tools._resolve_current_affine", lambda ctrl: (None, {}))
        mock_ctrl.core.get_pixel_size_um.return_value = 0.0
        result = find_features(mock_ctrl, unconstrained_guard)
        assert "offset_from_center_um" not in result
        assert "calibrate_stage_to_camera" in result["note"]
        assert "Puncta detector" in result["detector_scope"]
        assert "filamentous" in result["detector_scope"]


class TestCenterFeature:
    def test_refuses_without_calibration(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw.tools import center_feature
        monkeypatch.setattr("microclaw.tools._resolve_current_affine", lambda ctrl: (None, {}))
        result = center_feature(mock_ctrl, unconstrained_guard)
        assert "calibrate_stage_to_camera" in result["error"]
        assert "residual_offset_um" not in result

    # test_converges_on_synthetic_scene was DELETED by design/64, not repaired.
    # It hand-injected StageCameraAffine(-px, 0, 0, -px) — the negation of what
    # calibrate_stage_to_camera actually produces — against a scene model that
    # moved the opposite way from the calibration test's own. Two wrongs
    # cancelled, so it stayed green for a tool that moved the wrong way on every
    # real rig. Its replacement is TestCentringAgainstItsOwnCalibration, which
    # calibrates and centres against ONE model and never writes an affine by hand.

    def test_empty_field_errors(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw.calibration import StageCameraAffine
        from microclaw.tools import center_feature
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy",
            lambda ctrl: np.full((64, 64), 400, dtype=np.uint16),
        )
        monkeypatch.setattr(
            "microclaw.tools._resolve_current_affine",
            lambda ctrl: (StageCameraAffine(0.5, 0.0, 0.0, 0.5, "obj", 1, 0.5), {}),
        )
        result = center_feature(mock_ctrl, unconstrained_guard)
        assert "nothing to centre" in result["error"].lower()
        # An empty field and a structured one without puncta are different
        # diagnoses; see test_structure_without_puncta_moves_nothing.
        assert "No detected feature" not in result["error"]
        mock_ctrl.core.set_relative_xy_position.assert_not_called()


class TestCentringAgainstItsOwnCalibration:
    """design/64: calibrate and centre against the SAME optical model.

    Every test here runs the real `calibrate_stage_to_camera`, lets it save, and
    lets `center_feature` load what it saved. None hand-writes an affine —
    a hand-written affine is exactly where the sign error hid.
    """

    @pytest.fixture(autouse=True)
    def _knowledge(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "microclaw.knowledge_manager.KNOWLEDGE_PATH", tmp_path / "knowledge.yaml"
        )

    @staticmethod
    def calibrate(optics, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw.tools import calibrate_stage_to_camera
        optics.drive(mock_ctrl, monkeypatch)
        mock_ctrl.core.get_camera_device.return_value = "Cam"
        mock_ctrl.core.get_device_name.return_value = "CamModel"
        roi = MagicMock()
        roi.x, roi.y, roi.width, roi.height = 0, 0, optics.shape[1], optics.shape[0]
        mock_ctrl.core.get_roi.return_value = roi
        result = calibrate_stage_to_camera(mock_ctrl, unconstrained_guard, step_um=8.0)
        assert "error" not in result, result
        assert optics.pos == {"x": 0.0, "y": 0.0}, "calibration must restore the stage"
        return result

    @pytest.mark.parametrize("case", sorted(OPTICS_CASES))
    def test_calibration_then_centring_converges(
        self, case, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw.tools import center_feature
        optics = SyntheticOptics(OPTICS_CASES[case])
        self.calibrate(optics, mock_ctrl, unconstrained_guard, monkeypatch)

        before = math.hypot(*optics.punctum_offset_px())
        assert before > 20.0, "the punctum must start well off centre"
        result = center_feature(mock_ctrl, unconstrained_guard, max_iter=4, tol_px=3.0)

        assert result["centered"] is True, (case, result)
        assert math.hypot(*optics.punctum_offset_px()) < before, case

    @pytest.mark.parametrize("case", sorted(OPTICS_CASES))
    def test_first_commanded_move_is_the_unnegated_affine(
        self, case, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """The regression assertion for the defect itself.

        Convergence alone does not pin the sign: a wrong-signed affine applied
        with a wrong sign also converges, which is precisely how this shipped.
        This compares the commanded move against the affine calibration SAVED.
        """
        from microclaw.tools import _resolve_current_affine, center_feature
        optics = SyntheticOptics(OPTICS_CASES[case])
        self.calibrate(optics, mock_ctrl, unconstrained_guard, monkeypatch)
        saved = _resolve_current_affine(mock_ctrl)[0]
        assert saved is not None, "calibration did not reach the knowledge base"
        expected = saved.px_to_um(*optics.punctum_offset_px())

        mock_ctrl.core.set_relative_xy_position.reset_mock()
        center_feature(mock_ctrl, unconstrained_guard, max_iter=1, tol_px=3.0)
        commanded = mock_ctrl.core.set_relative_xy_position.call_args_list[0].args

        assert commanded[0] == pytest.approx(expected[0], abs=0.7), case
        assert commanded[1] == pytest.approx(expected[1], abs=0.7), case
        assert not (commanded[0] == pytest.approx(-expected[0], abs=0.7)
                    and commanded[1] == pytest.approx(-expected[1], abs=0.7)), case

    def test_one_correction_reduces_the_residual(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """Acceptance criterion: every successful correction shrinks the residual.

        Asserted over a SINGLE iteration, so a loop that overshoots and recovers
        cannot satisfy it by accident.
        """
        from microclaw.tools import center_feature
        optics = SyntheticOptics(OPTICS_CASES["rot90"])
        self.calibrate(optics, mock_ctrl, unconstrained_guard, monkeypatch)
        before = math.hypot(*optics.punctum_offset_px())
        center_feature(mock_ctrl, unconstrained_guard, max_iter=1, tol_px=0.01)
        assert math.hypot(*optics.punctum_offset_px()) < before

    def test_centres_the_brighter_punctum_not_the_aggregate_centroid(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw.tools import center_feature
        optics = SyntheticOptics(
            OPTICS_CASES["aligned"], punctum=(40.0, 40.0), amplitude=9000.0,
            second_punctum=(120.0, 120.0), second_amplitude=1500.0,
        )
        self.calibrate(optics, mock_ctrl, unconstrained_guard, monkeypatch)
        result = center_feature(mock_ctrl, unconstrained_guard, max_iter=4, tol_px=3.0)

        assert result["centered"] is True, result
        bright = math.hypot(*optics.punctum_offset_px())
        dim = math.hypot(*optics.punctum_offset_px(optics.second_punctum))
        assert bright < 4.5, bright
        assert dim > 40.0, "the dim punctum must NOT have been centred"

    def test_structure_without_puncta_moves_nothing(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """A gradient is signal above background and is not a centring target."""
        from microclaw.tools import center_feature
        optics = SyntheticOptics(OPTICS_CASES["aligned"])
        self.calibrate(optics, mock_ctrl, unconstrained_guard, monkeypatch)
        ramp = np.tile(
            np.linspace(400, 3000, 160, dtype=np.float32), (160, 1)
        ).astype(np.uint16)
        monkeypatch.setattr("microclaw.tools.snap_to_numpy", lambda ctrl: ramp)
        mock_ctrl.core.set_relative_xy_position.reset_mock()

        result = center_feature(mock_ctrl, unconstrained_guard)

        mock_ctrl.core.set_relative_xy_position.assert_not_called()
        assert "No detected feature to centre" in result.get("error", "")
        assert "nothing to centre" not in result["error"], "wrong diagnosis"
        assert result["n_spots"] == 0

    def test_equal_puncta_select_deterministically(self):
        """Required test 7, at the detector: an exact tie must not flip.

        Noiseless on purpose — with texture underneath, two puncta of equal
        amplitude do not actually tie, and the test would be measuring the
        texture rather than the tie break.
        """
        from microclaw.image_analysis import detect_features
        yy, xx = np.mgrid[0:160, 0:160]
        image = np.full((160, 160), 400.0, dtype=np.float32)
        for cy, cx in ((50.0, 60.0), (110.0, 60.0)):
            image = image + 5000.0 * np.exp(
                -((yy - cy) ** 2 + (xx - cx) ** 2) / 8.0
            )
        image = image.astype(np.uint16)

        chosen = {tuple(detect_features(image)["brightest_feature_xy_px"])
                  for _ in range(5)}

        assert len(chosen) == 1, chosen
        # Deterministic AND the documented rule: lowest (y, x) wins a tie.
        assert chosen.pop()[1] == pytest.approx(50.0, abs=1.5)


    def test_every_correction_is_bounds_checked(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """design/64 required test 8. There was nothing here to "keep": the guard
        was passed through and never asserted on, so a refusal escaping as an
        unhandled SafetyViolation would have gone unnoticed."""
        from microclaw.safety import (
            SafetyConstraints, SafetyGuard, SafetyViolation, StageConstraints,
        )
        from microclaw.tools import center_feature
        optics = SyntheticOptics(OPTICS_CASES["aligned"])
        self.calibrate(optics, mock_ctrl, unconstrained_guard, monkeypatch)
        # A box far too small to hold the correction the loop is about to make.
        tight = SafetyGuard(SafetyConstraints(stage=StageConstraints(
            x_min=-0.5, x_max=0.5, y_min=-0.5, y_max=0.5)))

        with pytest.raises(SafetyViolation):
            center_feature(mock_ctrl, tight)

        assert optics.pos == {"x": 0.0, "y": 0.0}, "a refused move must not happen"

    def test_failure_to_converge_is_finite_and_honest(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """A loop that cannot reach tolerance must stop and say so, not spin."""
        from microclaw.tools import center_feature
        # A stage that quantizes to 2 µm cannot place a punctum inside a
        # sub-pixel tolerance, which is a rig property (design/29), not a rigged
        # test: the loop must stop at max_iter and report where it got to.
        optics = SyntheticOptics(OPTICS_CASES["aligned"], quantum_um=2.0)
        self.calibrate(optics, mock_ctrl, unconstrained_guard, monkeypatch)
        result = center_feature(mock_ctrl, unconstrained_guard,
                                max_iter=2, tol_px=0.05)

        assert result["centered"] is False
        assert result["iterations"] == 2
        assert result["residual_px"] is not None
        assert len(result["residuals_px"]) == result["iterations"] + 1
        assert result["residuals_px"][-1] == pytest.approx(
            math.hypot(*result["residual_px"])
        )
        assert "stage" in result["hint"].lower()
        assert "smallest commanded correction" in result["hint"].lower()
        assert f'{result["smallest_correction_um"]:.2f}' in result["hint"]
        assert "raising tol_px" in result["hint"]
        assert "rerun calibrate_stage_to_camera" not in result["hint"]
        # max_iter corrections, and not one more.
        assert len(mock_ctrl.core.set_relative_xy_position.call_args_list) == 2 + 4

    def test_wrong_affine_earns_the_stale_calibration_hint(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw.calibration import StageCameraAffine
        from microclaw.tools import center_feature
        optics = SyntheticOptics(OPTICS_CASES["aligned"], quantum_um=2.0)
        optics.drive(mock_ctrl, monkeypatch)
        wrong = StageCameraAffine(0.5, 0.0, 0.0, 0.5, "obj", 1, 0.5)
        monkeypatch.setattr(
            "microclaw.tools._resolve_current_affine", lambda ctrl: (wrong, {})
        )

        result = center_feature(mock_ctrl, unconstrained_guard, max_iter=2, tol_px=0.05)

        assert result["residuals_px"][-1] > result["residuals_px"][0]
        assert "rerun calibrate_stage_to_camera" in result["hint"]
        assert "raising tol_px" not in result["hint"]

    def test_still_improving_at_max_iter_blames_neither_floor_nor_calibration(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw.calibration import StageCameraAffine
        from microclaw.tools import _resolve_current_affine, center_feature
        optics = SyntheticOptics(OPTICS_CASES["aligned"], quantum_um=0.01)
        self.calibrate(optics, mock_ctrl, unconstrained_guard, monkeypatch)
        affine = _resolve_current_affine(mock_ctrl)[0]
        slow = StageCameraAffine(
            affine.a / 2, affine.b / 2, affine.c / 2, affine.d / 2,
            affine.objective, affine.binning, affine.pixel_size_um,
        )
        monkeypatch.setattr(
            "microclaw.tools._resolve_current_affine", lambda ctrl: (slow, {})
        )

        result = center_feature(mock_ctrl, unconstrained_guard, max_iter=1, tol_px=0.05)

        assert result["residuals_px"][-1] < result["residuals_px"][0]
        assert "raise max_iter" in result["hint"]
        assert "stage" not in result["hint"].lower()
        assert "calibrat" not in result["hint"].lower()

    @pytest.mark.parametrize("case", ["aligned", "rot90_flip"])
    def test_residual_offset_um_uses_the_resolved_affine(
        self, case, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw.tools import _resolve_current_affine, center_feature
        optics = SyntheticOptics(OPTICS_CASES[case], quantum_um=2.0)
        self.calibrate(optics, mock_ctrl, unconstrained_guard, monkeypatch)
        affine = _resolve_current_affine(mock_ctrl)[0]

        result = center_feature(mock_ctrl, unconstrained_guard, max_iter=2, tol_px=0.05)

        correction = affine.px_to_um(*result["residual_px"])
        assert result["residual_offset_um"] == pytest.approx(math.hypot(*correction))

    def test_residual_history_matches_iterations_on_centered_path(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw.tools import center_feature
        optics = SyntheticOptics(OPTICS_CASES["aligned"])
        self.calibrate(optics, mock_ctrl, unconstrained_guard, monkeypatch)

        result = center_feature(mock_ctrl, unconstrained_guard, max_iter=4, tol_px=3.0)

        assert result["centered"] is True
        assert len(result["residuals_px"]) == result["iterations"] + 1
        assert result["residuals_px"][-1] == pytest.approx(
            math.hypot(*result["residual_px"])
        )


class TestMicroManagerIsTheCalibrationAuthority:
    """design/64: MM's PixelSizeAffine wins, and is adopted into the KB.

    The convention question is settled and load-bearing: MM's affine is the SAME
    map as ours (feature offset → the stage move that centres it), so it is
    adopted with no sign change. That equivalence is derived from MM's own
    consumer path — `CenterAndDragListener` passes the negated offset to
    `XYNavigator.moveSampleOnDisplayPixels`, whose `toStageSpace` negates again
    — and it is what `test_adopted_mm_affine_centres_the_feature` proves end to
    end rather than restating.
    """

    @pytest.fixture(autouse=True)
    def _knowledge(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "microclaw.knowledge_manager.KNOWLEDGE_PATH", tmp_path / "knowledge.yaml"
        )

    @staticmethod
    def publish(mock_ctrl, values):
        """Give the mock core an MM affine, as a NON-iterable Core collection.

        `list()` over one of these works against every naive fake and raises on
        every rig (design/59a lost a whole demo trip to exactly that), so the
        fake refuses iteration and offers size()/get(i) like the real thing.
        """
        class StrVector:
            def __init__(self, items): self._items = list(items)
            def size(self): return len(self._items)
            def get(self, index): return self._items[index]
            def __iter__(self): raise TypeError(
                "'mmcorej_StrVector' object is not iterable"
            )
        mock_ctrl.core.get_pixel_size_affine.return_value = StrVector(values)

    def test_mm_affine_is_adopted_into_the_knowledge_base(self, mock_ctrl):
        from microclaw.calibration import MM_AFFINE_SOURCE, load_affine_entry
        from microclaw.tools import _current_binning, _current_objective, _resolve_current_affine
        self.publish(mock_ctrl, ["0.2", "0.0", "0.0", "0.0", "0.3", "0.0"])

        affine, report = _resolve_current_affine(mock_ctrl)

        assert (affine.a, affine.b, affine.c, affine.d) == (0.2, 0.0, 0.0, 0.3)
        assert report["calibration_source"] == MM_AFFINE_SOURCE
        assert report["adopted_from_micro_manager"] is True
        # It is in the knowledge base now, which is what center_feature reads.
        stored, identity = load_affine_entry(
            _current_objective(mock_ctrl) or "", _current_binning(mock_ctrl)
        )
        assert (stored.a, stored.d) == (0.2, 0.3)
        assert identity["source"] == MM_AFFINE_SOURCE

    def test_adoption_is_idempotent(self, mock_ctrl):
        from microclaw.tools import _resolve_current_affine
        self.publish(mock_ctrl, ["0.2", "0.0", "0.0", "0.0", "0.3", "0.0"])
        assert _resolve_current_affine(mock_ctrl)[1]["adopted_from_micro_manager"]
        assert "adopted_from_micro_manager" not in _resolve_current_affine(mock_ctrl)[1]

    def test_a_recalibrated_mm_affine_replaces_the_adopted_one(self, mock_ctrl):
        from microclaw.tools import _resolve_current_affine
        self.publish(mock_ctrl, ["0.2", "0.0", "0.0", "0.0", "0.3", "0.0"])
        _resolve_current_affine(mock_ctrl)
        self.publish(mock_ctrl, ["0.25", "0.0", "0.0", "0.0", "0.25", "0.0"])

        affine, report = _resolve_current_affine(mock_ctrl)

        assert affine.a == 0.25
        assert report["adopted_from_micro_manager"] is True

    def test_our_own_measurement_overrides_mm_and_says_so(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """The escape hatch: MM is the authority until we deliberately measure."""
        from microclaw.calibration import MEASURED_AFFINE_SOURCE
        from microclaw.tools import _resolve_current_affine
        optics = SyntheticOptics(OPTICS_CASES["rot90"])
        TestCentringAgainstItsOwnCalibration.calibrate(
            optics, mock_ctrl, unconstrained_guard, monkeypatch
        )
        self.publish(mock_ctrl, ["0.2", "0.0", "0.0", "0.0", "0.3", "0.0"])

        affine, report = _resolve_current_affine(mock_ctrl)

        assert report["calibration_source"] == MEASURED_AFFINE_SOURCE
        assert affine.a != 0.2
        differs = report["micro_manager_affine_differs"]
        assert differs["micro_manager"] == [0.2, 0.0, 0.0, 0.3]
        assert "Rerun it" in differs["reason"]

    def test_a_sentinel_mm_affine_is_not_adopted(self, mock_ctrl):
        from microclaw.tools import _resolve_current_affine
        self.publish(mock_ctrl, ["0.0"] * 6)
        affine, report = _resolve_current_affine(mock_ctrl)
        assert affine is None
        assert report["calibration_source"] is None

    def test_manual_simple_signature_is_reported_not_refused(self, mock_ctrl):
        """design/29: MM's Manual-Simple calibrator never measures a scale."""
        from microclaw.tools import _resolve_current_affine
        # Res1 exactly as design/29 read it off M2's .cfg, signed zero included.
        self.publish(mock_ctrl, ["-0.0", "0.127", "0.0", "-0.127", "0.0", "0.0"])

        affine, report = _resolve_current_affine(mock_ctrl)

        assert affine is not None, "a scale-only doubt must not block centring"
        assert "Manual-Simple" in report["calibration_note"]
        assert "anisotropic by 14%" in report["calibration_note"]

    def test_a_genuinely_measured_mm_affine_gets_no_scale_warning(self, mock_ctrl):
        from microclaw.tools import _resolve_current_affine
        self.publish(mock_ctrl, ["0.1225", "0.004", "0.0", "-0.003", "0.1071", "0.0"])
        assert "calibration_note" not in _resolve_current_affine(mock_ctrl)[1]

    def test_our_calibration_agrees_with_mm_convention(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """The independent oracle the shared-model tests cannot be.

        A code review noted, correctly, that flipping the convention in BOTH
        `solve_affine` and `center_feature` leaves every convergence test green:
        the two minus signs cancel and the fake has no opinion about which
        convention is stored. It matters anyway, because MM's affine is adopted
        into the SAME knowledge-base slot — so the invariant with teeth is that
        what we measure equals what MM would publish for the same optics, and
        that is what this pins.
        """
        from microclaw.tools import _resolve_current_affine
        optics = SyntheticOptics(OPTICS_CASES["rot90"])
        TestCentringAgainstItsOwnCalibration.calibrate(
            optics, mock_ctrl, unconstrained_guard, monkeypatch
        )
        measured, _ = _resolve_current_affine(mock_ctrl)

        # What MM publishes for this rig, derived from MM's semantics only: to
        # centre a feature at offset r the stage must move A·r, and this model
        # says the scene moves m_phys·s for a stage move s. So A = -m_phys^-1.
        expected = -np.linalg.inv(optics.m_phys)

        assert measured.a == pytest.approx(expected[0, 0], abs=0.02)
        assert measured.b == pytest.approx(expected[0, 1], abs=0.02)
        assert measured.c == pytest.approx(expected[1, 0], abs=0.02)
        assert measured.d == pytest.approx(expected[1, 1], abs=0.02)

    def test_a_camera_swap_invalidates_the_cached_calibration(self, mock_ctrl):
        """(objective, binning) does not identify a camera."""
        from microclaw.tools import _resolve_current_affine
        mock_ctrl.core.get_camera_device.return_value = "Andor"
        mock_ctrl.core.get_device_name.return_value = "iXon"
        roi = MagicMock()
        roi.x, roi.y, roi.width, roi.height = 0, 0, 512, 512
        mock_ctrl.core.get_roi.return_value = roi
        self.publish(mock_ctrl, ["0.2", "0.0", "0.0", "0.0", "0.3", "0.0"])
        assert _resolve_current_affine(mock_ctrl)[0] is not None

        # Same objective and binning, different camera, and MM now publishes
        # nothing — the cache must not answer for the new sensor.
        mock_ctrl.core.get_device_name.return_value = "Hamamatsu ORCA"
        self.publish(mock_ctrl, ["0.0"] * 6)

        affine, report = _resolve_current_affine(mock_ctrl)

        assert affine is None
        assert "different camera" in report["cached_calibration_rejected"]

    def test_a_cropped_roi_does_not_invalidate_the_calibration(self, mock_ctrl):
        """Cropping changes where the centre is, not the pixel-to-stage map."""
        from microclaw.tools import _resolve_current_affine
        mock_ctrl.core.get_camera_device.return_value = "Andor"
        mock_ctrl.core.get_device_name.return_value = "iXon"
        roi = MagicMock()
        roi.x, roi.y, roi.width, roi.height = 0, 0, 512, 512
        mock_ctrl.core.get_roi.return_value = roi
        self.publish(mock_ctrl, ["0.2", "0.0", "0.0", "0.0", "0.3", "0.0"])
        _resolve_current_affine(mock_ctrl)

        cropped = MagicMock()
        cropped.x, cropped.y, cropped.width, cropped.height = 100, 100, 64, 64
        mock_ctrl.core.get_roi.return_value = cropped
        self.publish(mock_ctrl, ["0.0"] * 6)

        affine, report = _resolve_current_affine(mock_ctrl)

        assert affine is not None, "an ROI crop must not discard the calibration"
        assert "cached_calibration_rejected" not in report

    def test_an_unwritable_knowledge_base_does_not_cost_the_mm_affine(
        self, mock_ctrl, monkeypatch
    ):
        """MM is the live authority; caching it is a convenience, not a gate."""
        from microclaw import tools
        from microclaw.calibration import MM_AFFINE_SOURCE
        self.publish(mock_ctrl, ["0.2", "0.0", "0.0", "0.0", "0.3", "0.0"])
        monkeypatch.setattr(
            "microclaw.calibration.save_affine",
            MagicMock(side_effect=OSError("read-only file system")),
        )

        affine, report = tools._resolve_current_affine(mock_ctrl)

        assert affine is not None and affine.a == 0.2
        assert report["calibration_source"] == MM_AFFINE_SOURCE
        assert "read-only file system" in report["calibration_not_cached"]
        assert report["adopted_from_micro_manager"] is False

    def test_an_optical_path_change_mid_loop_stops_the_centring(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """The user owns the session and microclaw is not the only client."""
        from microclaw import tools
        optics = SyntheticOptics(OPTICS_CASES["aligned"])
        TestCentringAgainstItsOwnCalibration.calibrate(
            optics, mock_ctrl, unconstrained_guard, monkeypatch
        )
        entry, _ = tools._resolve_current_affine(mock_ctrl)
        rotated = type(entry)(0.0, -entry.a, entry.a, 0.0, entry.objective,
                              entry.binning, entry.pixel_size_um)
        calls = {"n": 0}

        def turret_turns(ctrl):
            calls["n"] += 1
            return (entry if calls["n"] <= 1 else rotated), {}

        monkeypatch.setattr(tools, "_resolve_current_affine", turret_turns)
        mock_ctrl.core.set_relative_xy_position.reset_mock()

        result = tools.center_feature(mock_ctrl, unconstrained_guard, max_iter=3)

        assert result["centered"] is False
        assert "calibration changed during centring" in result["error"]
        mock_ctrl.core.set_relative_xy_position.assert_not_called()

    def test_adopted_mm_affine_centres_the_feature(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """The convention claim, end to end, with no calibrate_stage_to_camera.

        If MM's affine were the opposite convention this diverges instead, the
        same way the pre-fix tool did.
        """
        from microclaw.tools import center_feature
        optics = SyntheticOptics(OPTICS_CASES["flip_y"]).drive(mock_ctrl, monkeypatch)
        # flip_y optics: a +1 µm stage X move shifts the scene +2 px in X and a
        # +1 µm Y move shifts it -2 px in Y, so the centring map is diag(-0.5, +0.5).
        self.publish(mock_ctrl, ["-0.5", "0.0", "0.0", "0.0", "0.5", "0.0"])
        before = math.hypot(*optics.punctum_offset_px())

        result = center_feature(mock_ctrl, unconstrained_guard, max_iter=4, tol_px=3.0)

        assert result["centered"] is True, result
        assert math.hypot(*optics.punctum_offset_px()) < before


class TestFocusLock:
    """design/14 §5: the lock is readable, and a sweep must not fight it."""

    PROPS = {
        "Z stage focus locking": {
            "device": "PIZStage", "property": "External sensor",
            "mm_property_string": "PIZStage-External sensor",
            "on": "1", "off": "0",
        },
        "QPD X": {"device": "Analog Input", "property": "AnalogInput0",
                  "mm_property_string": "Analog Input-AnalogInput0"},
    }

    def _emu(self, monkeypatch, props=None):
        from microclaw import tools
        monkeypatch.setattr(
            tools, "_cached_emu_properties",
            lambda ctrl: (self.PROPS if props is None else props, {}),
        )

    def test_reports_engaged_with_qpd(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw.tools import get_focus_lock_state
        self._emu(monkeypatch)
        mock_ctrl.core.get_property.return_value = "1"
        result = get_focus_lock_state(mock_ctrl, unconstrained_guard)
        assert result["engaged"] is True
        assert result["property"] == "PIZStage.External sensor"
        assert result["qpd"] == {"x": "1"}

    def test_reports_disengaged(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw.tools import get_focus_lock_state
        self._emu(monkeypatch)
        mock_ctrl.core.get_property.return_value = "0"
        assert get_focus_lock_state(mock_ctrl, unconstrained_guard)["engaged"] is False

    def test_non_emu_rig_returns_null_not_false(self, mock_ctrl, unconstrained_guard, monkeypatch):
        # engaged=None means "unknown"; False would be a false reassurance.
        from microclaw.tools import get_focus_lock_state
        self._emu(monkeypatch, props={})
        result = get_focus_lock_state(mock_ctrl, unconstrained_guard)
        assert result["engaged"] is None
        assert "reason" in result

    def test_non_emu_autofocus_device_reports_lock_and_blocks_sweep(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw.tools import get_focus_lock_state
        self._emu(monkeypatch, props={})
        mock_ctrl.core.get_auto_focus_device.return_value = "HardwareAF"
        mock_ctrl.core.is_continuous_focus_enabled.return_value = True
        mock_ctrl.core.get_device_property_names.return_value = []
        state = get_focus_lock_state(mock_ctrl, unconstrained_guard)
        assert state["engaged"] is True
        assert state["property"] == "continuous focus device HardwareAF"
        assert state["device"] == "HardwareAF"
        sweep = MagicMock()
        monkeypatch.setattr("microclaw.tools.coarse_then_fine_autofocus", sweep)
        result = run_autofocus(
            mock_ctrl, unconstrained_guard, z_range_um=10.0, z_step_um=1.0
        )
        assert "Focus lock is engaged" in result["error"]
        sweep.assert_not_called()

    @pytest.mark.parametrize("rig, names, values, readonly, expected_pick", [
        # Nikon Ti, measured 2026-08-22.
        ("Ti", ["FullFocusTimeoutMs", "Name", "State", "Status"],
         {"FullFocusTimeoutMs": "5000", "Name": "TIPFSStatus", "State": "Off",
          "Status": "Out of focus search range"},
         {"Status", "Name"}, "Status"),
        # Nikon Ti2-E / Andor Dragonfly, measured 2026-08-23. Different device,
        # different property, different values -- and a "PFS Status" that is a
        # 16-bit string nobody should probe sitting next to the useful one.
        ("Dragonfly",
         ["DichroicMirrorInserted", "FocusMaintenance", "LEDIntensity",
          "PFS Status", "PFS in Range"],
         {"DichroicMirrorInserted": "1", "FocusMaintenance": "On",
          "LEDIntensity": "3", "PFS Status": "0000001100001010",
          "PFS in Range": "In Range"},
         {"PFS Status", "PFS in Range"}, "PFS in Range"),
    ])
    def test_focus_lock_state_names_the_properties_a_probe_could_read(
        self, mock_ctrl, unconstrained_guard, monkeypatch,
        rig, names, values, readonly, expected_pick,
    ):
        """Naming the device but not the property is what sent it to the camera.

        On both rigs the model had the lock device and still reached for an
        image sweep -- on the Dragonfly the operator had to ask "why not do a
        PFS search?". Finding the property took list_device_properties plus a
        get_device_property_info per candidate, and on a cold session it was
        cheaper to give up. The values are what disambiguate, and no rule about
        names could: the two rigs share none.
        """
        from microclaw.tools import get_focus_lock_state
        self._emu(monkeypatch, props={})
        mock_ctrl.core.get_auto_focus_device.return_value = "PFSDEV"
        mock_ctrl.core.is_continuous_focus_enabled.return_value = False
        mock_ctrl.core.get_device_property_names.return_value = names
        mock_ctrl.core.is_property_read_only.side_effect = (
            lambda _d, prop: prop in readonly
        )
        mock_ctrl.core.get_property.side_effect = lambda _d, prop: values[prop]

        state = get_focus_lock_state(mock_ctrl, unconstrained_guard)

        assert state["device"] == "PFSDEV"
        assert set(state["status_properties"]) == readonly
        assert state["status_properties"][expected_pick] == values[expected_pick]
        assert "run_autofocus" in state["probe_hint"]
        # Writable properties are not probe candidates and must not be offered.
        assert all(name not in state["status_properties"]
                   for name in names if name not in readonly)

    @pytest.mark.parametrize(
        "device,emu_props,names,values,readonly,expected_device",
        [
            # Ti: design/34-nikon-pfs-tizdrive-findings.md:48-49.
            ("TIPFSStatus", {}, StrVector(["Status"]),
             {"Status": "Out of focus search range"}, {"Status"}, "TIPFSStatus"),
            # Ti2-E / Andor Dragonfly: design/56-the-focus-metric-need-not-be-an-image.md:785-797.
            ("PFS", {}, StrVector(["PFS Status", "PFS in Range"]),
             {"PFS Status": "0000001100001010", "PFS in Range": "In Range"},
             {"PFS Status", "PFS in Range"}, "PFS"),
            # ASI CRISP: design/06. The property deliberately contains "PFS";
            # this limb rejects identifying a lock by substring-scanning properties.
            ("", {"Z stage focus locking": {
                "device": "CRISP", "property": "PFS CRISP State",
                "mm_property_string": "CRISP-PFS CRISP State", "on": "In Focus", "off": "Idle",
            }}, StrVector([]), {}, set(), "CRISP"),
            # An ordinary rig with no configured autofocus device remains a silent no-op.
            ("", {}, StrVector([]), {}, set(), None),
        ],
        ids=["tipfsstatus", "pfs", "crisp", "no-device"],
    )
    def test_focus_lock_discriminator_from_rig_payload(
        self, mock_ctrl, unconstrained_guard, monkeypatch,
        device, emu_props, names, values, readonly, expected_device,
    ):
        """The identity routing depends on, replayed from four rig payloads.

        This is a discriminator fixture, not a routing test. A model chooses
        whether to load `nikon-pfs`; nothing here observes that choice, and a
        scripted mock that supplied it would only assert a property of the mock
        (design/61, block 61a's vacuous routing test). What this asserts is that
        the identity a router needs is present and correct on four measured
        rigs.

        Two limbs cannot fail for block 61b, deliberately, and must not be
        counted as its evidence: `tipfsstatus` and `pfs` both take the non-EMU
        branch, which already returned `device` before this block, and
        `no-device` guards an already-correct silent no-op. Only `crisp`
        exercises item 4's new EMU key -- verified red on the pre-change tree
        with `KeyError: 'device'`. The two Nikon limbs are cross-rig guards:
        they keep routing from collapsing onto one literal device name.
        """
        from microclaw.tools import get_focus_lock_state

        self._emu(monkeypatch, props=emu_props)
        mock_ctrl.core.get_auto_focus_device.return_value = device
        mock_ctrl.core.is_continuous_focus_enabled.return_value = False
        mock_ctrl.core.get_device_property_names.return_value = names
        mock_ctrl.core.is_property_read_only.side_effect = (
            lambda _device, prop: prop in readonly
        )
        mock_ctrl.core.get_property.side_effect = (
            lambda _device, prop: values.get(prop, "In Focus")
        )

        result = get_focus_lock_state(mock_ctrl, unconstrained_guard)

        if expected_device is None:
            assert "device" not in result
            # No identification question on an ordinary rig: the invariant is
            # a silent no-op where no autofocus device is configured. Word
            # boundaries, because a bare "ask" substring also matches "task".
            rendered = json.dumps(result).lower()
            for word in ("question", "unknown", "operator", "identify", "ask"):
                assert not re.search(rf"\b{word}", rendered), word
        else:
            assert result["device"] == expected_device

    def test_set_focus_lock_writes_on_value(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw.tools import set_focus_lock
        self._emu(monkeypatch)
        # Gate 2 remains independent: the guard shape produced by a minimal
        # schema-3 profile admits this ordinary property without learning about
        # authorization-map typed entries.
        result = set_focus_lock(mock_ctrl, unconstrained_guard, enabled=True)
        mock_ctrl.core.set_property.assert_called_once_with(
            "PIZStage", "External sensor", "1")
        mock_ctrl.refresh_gui.assert_called_once_with()
        assert result["engaged"] is True

    def test_set_focus_lock_writes_off_value(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw.tools import set_focus_lock
        self._emu(monkeypatch)
        set_focus_lock(mock_ctrl, unconstrained_guard, enabled=False)
        mock_ctrl.core.set_property.assert_called_once_with(
            "PIZStage", "External sensor", "0")
        mock_ctrl.refresh_gui.assert_called_once_with()

    def test_set_focus_lock_admits_typed_pair_but_refuses_raw_write_on_bounded_stage(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw.authorization import (
            AuthorizationEntry,
            AuthorizationMap,
            RigAuthorizationError,
            authorize_property_write,
        )
        from microclaw.tools import set_focus_lock
        self._emu(monkeypatch)
        mock_ctrl.authorization_map = AuthorizationMap(
            "degraded_trusted_plugins", "degraded", None,
            entries=[AuthorizationEntry(
                path="generic-property",
                classification="built_in_typed_capability",
                device="PIZStage",
                property="External sensor",
                capability="focus-lock",
            )],
            property_writes_unrestricted=True,
            bounded_stage_devices=frozenset({"PIZStage"}),
        )
        result = set_focus_lock(mock_ctrl, unconstrained_guard, enabled=True)
        mock_ctrl.core.set_property.assert_called_once_with(
            "PIZStage", "External sensor", "1"
        )
        mock_ctrl.refresh_gui.assert_called_once_with()
        assert result["engaged"] is True
        with pytest.raises(RigAuthorizationError, match="move_stage_z"):
            authorize_property_write(mock_ctrl, "PIZStage", "Position")

    def test_autofocus_refuses_while_lock_engaged(self, mock_ctrl, unconstrained_guard, monkeypatch):
        # The sweep would be actively opposed by the piezo servo loop.
        self._emu(monkeypatch)
        mock_ctrl.core.get_property.return_value = "1"
        called = []
        monkeypatch.setattr("microclaw.tools.coarse_then_fine_autofocus",
                            lambda *a, **k: called.append(1))
        result = run_autofocus(mock_ctrl, unconstrained_guard,
                               z_range_um=10.0, z_step_um=1.0)
        assert "Focus lock is engaged" in result["error"]
        assert not called, "no sweep may run against an engaged lock"

    def test_autofocus_runs_when_lock_disengaged(self, mock_ctrl, unconstrained_guard, monkeypatch):
        self._emu(monkeypatch)
        mock_ctrl.core.get_property.return_value = "0"
        _patch_autofocus(monkeypatch)
        result = run_autofocus(mock_ctrl, unconstrained_guard, z_range_um=10.0,
                               z_step_um=1.0, return_thumbnail=False)
        assert result["converged"] is True

    def test_autofocus_runs_on_non_emu_rig(self, mock_ctrl, unconstrained_guard, monkeypatch):
        self._emu(monkeypatch, props={})
        _patch_autofocus(monkeypatch)
        result = run_autofocus(mock_ctrl, unconstrained_guard, z_range_um=10.0,
                               z_step_um=1.0, return_thumbnail=False)
        assert result["converged"] is True


class TestTimelapseTriggerPreflight:
    """design/14 §1: refuse an SMLM acquisition whose excitation is gated off."""

    PROPS = {
        "Laser 3 enable": {"device": "Luxx638", "property": "Laser Operation Select",
                           "mm_property_string": "Luxx638-Laser Operation Select"},
        "Laser trigger 3 mode": {"device": "Laser Trigger", "property": "Mode3",
                                 "mm_property_string": "Laser Trigger-Mode3"},
        "Laser trigger 3 sequence": {"device": "Laser Trigger", "property": "Sequence3",
                                     "mm_property_string": "Laser Trigger-Sequence3"},
    }

    def _setup(self, mock_ctrl, monkeypatch, mode="4 - Follow", sequence="65535"):
        from microclaw import tools
        monkeypatch.setattr(tools, "_cached_emu_properties", lambda ctrl: (self.PROPS, {}))
        monkeypatch.setattr(tools, "CONFIRM_FN", lambda *args, **kwargs: True)
        monkeypatch.setattr("microclaw.tools._acquire_with_hooks", lambda *a, **k: "/tmp/ds")
        values = {("Laser Trigger", "Mode3"): mode,
                  ("Laser Trigger", "Sequence3"): sequence}
        mock_ctrl.core.get_property.side_effect = lambda d, p: values[(d, p)]

    def test_gated_off_trigger_refused(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw.tools import run_timelapse
        self._setup(mock_ctrl, monkeypatch, mode="0 - Off")
        with pytest.raises(SafetyViolation, match="trigger line is not armed"):
            run_timelapse(mock_ctrl, unconstrained_guard, n_frames=100, interval_s=0,
                          save_dir="/tmp", laser_slot=3)

    def test_zero_sequence_refused(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw.tools import run_timelapse
        self._setup(mock_ctrl, monkeypatch, sequence="0")
        with pytest.raises(SafetyViolation, match="sequence"):
            run_timelapse(mock_ctrl, unconstrained_guard, n_frames=100, interval_s=0,
                          save_dir="/tmp", laser_slot=3)

    def test_firing_trigger_passes(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw.tools import run_timelapse
        self._setup(mock_ctrl, monkeypatch)
        result = run_timelapse(mock_ctrl, unconstrained_guard, n_frames=100, interval_s=0,
                               save_dir="/tmp", laser_slot=3)
        assert result["status"] == "Timelapse complete."
        assert result["inter_frame_gap_summary"]["count"] == 0
        assert result["trigger_preflight"] == {
            "guarantee": "trigger line is armed",
            "checked": [
                {"kind": "trigger mode", "device": "Laser Trigger",
                 "property": "Mode3", "value": "4 - Follow"},
                {"kind": "trigger sequence", "device": "Laser Trigger",
                 "property": "Sequence3", "value": "65535"},
            ],
            "not_verified": [
                "device-level enables", "illumination properties", "emission path"
            ],
        }
        assert "declared_illumination_properties" not in result

    def test_unknown_slot_refused(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw.tools import run_timelapse
        self._setup(mock_ctrl, monkeypatch)
        with pytest.raises(SafetyViolation, match="slot"):
            run_timelapse(mock_ctrl, unconstrained_guard, n_frames=1, interval_s=0,
                          save_dir="/tmp", laser_slot=7)

    def test_non_emu_rig_skips_preflight(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw import tools
        monkeypatch.setattr(tools, "_cached_emu_properties", lambda ctrl: (None, {}))
        monkeypatch.setattr("microclaw.tools._acquire_with_hooks", lambda *a, **k: "/tmp/ds")
        result = tools.run_timelapse(mock_ctrl, unconstrained_guard, n_frames=1,
                                     interval_s=0, save_dir="/tmp", laser_slot=3)
        assert result["status"] == "Timelapse complete."
        assert result["trigger_preflight"]["guarantee"] == (
            "no trigger-line verification available"
        )

    def test_no_laser_slot_means_no_preflight(self, mock_ctrl, unconstrained_guard, monkeypatch):
        monkeypatch.setattr("microclaw.tools._acquire_with_hooks", lambda *a, **k: "/tmp/ds")
        from microclaw.tools import run_timelapse
        result = run_timelapse(mock_ctrl, unconstrained_guard, n_frames=1, interval_s=0,
                               save_dir="/tmp")
        assert result["status"] == "Timelapse complete."

    def test_gated_off_trigger_refused_before_a_hook_is_resolved(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """The pre-flight is upstream of every hook step, and must stay there.

        Block 43j folded hook resolution into this function; the pre-flight
        refusal must still arrive before `_prepare_log_path`, `_resolve_hook` or
        any capability configuration runs. M5 gated the armed limb with a hook
        attached (200 frames, 200 log records); this pins the refusing limb,
        which the rig round did not reach.
        """
        from microclaw import tools
        self._setup(mock_ctrl, monkeypatch, mode="0 - Off")
        reached = []
        monkeypatch.setattr(tools, "_prepare_log_path",
                            lambda *a, **k: reached.append("log_path"))
        monkeypatch.setattr(tools, "_resolve_hook",
                            lambda *a, **k: reached.append("resolve"))
        with pytest.raises(SafetyViolation, match="trigger line is not armed"):
            tools.run_timelapse(mock_ctrl, unconstrained_guard, n_frames=100,
                                interval_s=0, save_dir="/tmp", laser_slot=3,
                                hook_strategy="snr_observer")
        assert reached == []


class TestExportDatasetAllAxes:
    def test_it_creates_the_output_directory_rather_than_failing(
            self, mock_ctrl, unconstrained_guard, tmp_path, monkeypatch):
        """Nine M5 exports failed because the parent folder did not exist.

        Every other writing tool makes its parent, and the generic path hint
        tells the reader microclaw does -- so the failure named three causes
        that were all wrong. Exporting frames is how images reach a classifier
        for training, so this sits on the retraining path.
        """
        class FakeDataset:
            axes = {}

            def __init__(self, path):
                pass

            def has_image(self, **kw):
                return True

            def read_image(self, **kw):
                return np.zeros((4, 4), dtype=np.uint16)

        monkeypatch.setattr("microclaw.tools.Dataset", FakeDataset)
        monkeypatch.setattr("microclaw.tools.tifffile.imwrite",
                            lambda path, stack, **k: Path(path).write_bytes(b"tiff"))
        target = tmp_path / "made" / "by" / "the" / "tool" / "o.tif"
        assert not target.parent.exists()
        result = tools.export_dataset_as_tiff(
            mock_ctrl, unconstrained_guard,
            dataset_path="ds", output_path=str(target),
        )
        assert target.exists()
        assert result["artifact"]["path"] == str(target)

    def test_present_coord_helper_uses_strings_sparse_axes_and_selection(self):
        from microclaw.tools import _iter_present_coords

        class FakeDataset:
            axes = {"position": ["run_a_r0_c3", "run_a_r2_c1"], "time": [4, 9]}

            def has_image(self, **coords):
                return (coords["position"], coords["time"]) in {
                    ("run_a_r0_c3", 4), ("run_a_r2_c1", 9)
                }

        assert list(_iter_present_coords(FakeDataset(), {"time": 9})) == [
            {"position": "run_a_r2_c1", "time": 9}
        ]
        with pytest.raises(ValueError, match="not present"):
            list(_iter_present_coords(FakeDataset(), {"time": 0}))

    def test_iterates_full_axis_product(self, mock_ctrl, unconstrained_guard, monkeypatch, tmp_path):
        from microclaw import tools

        class FakeDataset:
            axes = {"z": [0, 1, 2], "channel": [0, 1]}

            def __init__(self, path):
                pass

            def has_image(self, **kw):
                return True

            def read_image(self, **kw):
                return np.zeros((4, 4), dtype=np.uint16)

        captured = {}
        monkeypatch.setattr("microclaw.tools.Dataset", FakeDataset)
        monkeypatch.setattr(
            "microclaw.tools.tifffile.imwrite",
            lambda p, stack, **k: captured.update(shape=stack.shape),
        )
        result = tools.export_dataset_as_tiff(
            mock_ctrl, unconstrained_guard,
            dataset_path="ds", output_path=str(tmp_path / "o.tif"),
        )
        # z (3) × channel (2) × H (4) × W (4) — no axis silently dropped
        assert captured["shape"] == (3, 2, 4, 4)
        assert result["axes"] == ["z", "channel"]
        assert result["artifact"] == {"kind": "tiff", "path": str(tmp_path / "o.tif")}

    def test_axes_keyed_hook_events_export_only_the_acquired_frame_count(
        self, mock_ctrl, unconstrained_guard, monkeypatch, tmp_path
    ):
        from microclaw import tools
        from microclaw.hook_decisions import UntrustedHookAdapter

        events = [{"axes": {"time": index}} for index in range(3)]
        adapter = UntrustedHookAdapter(object())
        adapter.configure_named_stage(
            core=MagicMock(), guard=MagicMock(), device="fixture-stage",
            min_um=0, max_um=1, max_writes=1, initial_value=0,
            restore="leave", action_plan={
                (("time", index),): (index, ()) for index in range(3)
            },
        )
        for event in events:
            adapter.pre_hardware_hook_fn(event)
            assert set(event["axes"]) == {"time"}

        class FakeDataset:
            axes = {"time": [event["axes"]["time"] for event in events]}
            def __init__(self, path): pass
            def has_image(self, **kwargs): return True
            def read_image(self, **kwargs):
                return np.full((2, 2), kwargs["time"], dtype=np.uint16)

        captured = {}
        monkeypatch.setattr(tools, "Dataset", FakeDataset)
        monkeypatch.setattr(
            tools.tifffile, "imwrite",
            lambda path, stack, **kwargs: captured.update(stack=stack),
        )
        result = tools.export_dataset_as_tiff(
            mock_ctrl, unconstrained_guard, "indexed-dataset",
            str(tmp_path / "indexed.tif"),
        )
        assert "error" not in result
        assert captured["stack"].shape == (3, 2, 2)

    def test_sparse_multiposition_uses_real_coords(
        self, mock_ctrl, unconstrained_guard, monkeypatch, tmp_path
    ):
        """design/28 F3: a one-frame-per-position grid has non-contiguous
        `position` coordinates and is NOT a dense hypercube. The old range(len)
        exporter did read_image(position=0..len-1) and raised KeyError; the fix
        must iterate the ACTUAL coords and skip absent cells via has_image."""
        from microclaw import tools

        # Positions labelled 5 and 9 (not 0,1) — one frame each, no z/channel.
        stored = {(5,): np.full((4, 4), 5, np.uint16),
                  (9,): np.full((4, 4), 9, np.uint16)}

        class FakeDataset:
            axes = {"position": [5, 9]}

            def __init__(self, path):
                pass

            def has_image(self, **kw):
                return (kw["position"],) in stored

            def read_image(self, **kw):
                key = (kw["position"],)
                if key not in stored:                 # the old bug: KeyError here
                    raise KeyError("position")
                return stored[key]

        captured = {}
        monkeypatch.setattr("microclaw.tools.Dataset", FakeDataset)
        monkeypatch.setattr(
            "microclaw.tools.tifffile.imwrite",
            lambda p, stack, **k: captured.update(shape=stack.shape, stack=stack),
        )
        result = tools.export_dataset_as_tiff(
            mock_ctrl, unconstrained_guard,
            dataset_path="ds", output_path=str(tmp_path / "o.tif"),
        )
        assert "error" not in result
        assert captured["shape"] == (2, 4, 4)         # position (2) × H × W
        # Real coordinates were read, not indices 0/1.
        assert captured["stack"][0].max() == 5
        assert captured["stack"][1].max() == 9

    def test_sparse_hypercube_fills_missing_with_zeros(
        self, mock_ctrl, unconstrained_guard, monkeypatch, tmp_path
    ):
        """A (position × channel) grid where one cell was never acquired must
        still export as a rectangular hyperstack, the gap zero-filled."""
        from microclaw import tools

        present = {(0, 0), (0, 1), (1, 0)}            # (1, 1) is missing

        class FakeDataset:
            axes = {"position": [0, 1], "channel": [0, 1]}

            def __init__(self, path):
                pass

            def has_image(self, **kw):
                return (kw["position"], kw["channel"]) in present

            def read_image(self, **kw):
                return np.ones((4, 4), np.uint16)

        captured = {}
        monkeypatch.setattr("microclaw.tools.Dataset", FakeDataset)
        monkeypatch.setattr(
            "microclaw.tools.tifffile.imwrite",
            lambda p, stack, **k: captured.update(shape=stack.shape, stack=stack),
        )
        result = tools.export_dataset_as_tiff(
            mock_ctrl, unconstrained_guard,
            dataset_path="ds", output_path=str(tmp_path / "o.tif"),
        )
        assert "error" not in result
        assert captured["shape"] == (2, 2, 4, 4)
        assert captured["stack"][1, 1].sum() == 0     # missing cell zero-filled
        assert captured["stack"][0, 0].sum() == 16    # present cell intact


class TestAcquisitionsRespectTheWorkspace:
    """A configured workspace confines what an acquisition writes, not just what
    the export tool reads. Without this the guard is one-sided: a z-stack saved
    outside the workspace can never be exported, and the operator only finds out
    after the objective has swept the range."""

    @pytest.fixture
    def ws_guard(self, tmp_path):
        return SafetyGuard(SafetyConstraints(
            stage=StageConstraints(z_min=-100, z_max=100),
            workspace_dir=str(tmp_path / "ws"),
        ))

    def test_zstack_outside_the_workspace_is_refused(self, mock_ctrl, ws_guard, monkeypatch):
        from microclaw import tools

        monkeypatch.setattr(tools, "_acquire_with_hooks",
                            lambda *a, **k: pytest.fail("acquisition should not start"))
        with pytest.raises(SafetyViolation, match="escapes"):
            tools.run_zstack(mock_ctrl, ws_guard, z_start_um=0, z_end_um=10,
                             z_step_um=1, save_dir="/somewhere/else")

    def test_the_refusal_lands_before_any_hardware_moves(self, mock_ctrl, ws_guard, monkeypatch):
        """The whole point: a check that runs after the irreversible part is the
        wrong check. set_exposure must not have been called."""
        from microclaw import tools

        monkeypatch.setattr(tools, "_acquire_with_hooks",
                            lambda *a, **k: pytest.fail("acquisition should not start"))
        with pytest.raises(SafetyViolation):
            tools.run_zstack(mock_ctrl, ws_guard, z_start_um=0, z_end_um=10,
                             z_step_um=1, save_dir="/somewhere/else", exposure_ms=50)
        mock_ctrl.core.set_exposure.assert_not_called()

    def test_zstack_inside_the_workspace_proceeds(self, mock_ctrl, ws_guard, tmp_path, monkeypatch):
        from microclaw import tools

        seen = {}
        monkeypatch.setattr(tools, "_acquire_with_hooks",
                            lambda guard, save_dir, *a, **k: seen.setdefault("dir", save_dir))
        tools.run_zstack(mock_ctrl, ws_guard, z_start_um=0, z_end_um=10, z_step_um=1,
                         save_dir=str(tmp_path / "ws" / "run1"))
        assert seen["dir"] == str(tmp_path / "ws" / "run1")

    def test_an_unset_workspace_still_saves_anywhere(self, mock_ctrl, unconstrained_guard, monkeypatch):
        """The default. Confinement is opt-in; nobody is forced into a sandbox
        to use microclaw. Resolved (abspath, design/21 F6), never refused —
        built with os.sep so the test measures confinement, not the platform."""
        from microclaw import tools

        seen = {}
        monkeypatch.setattr(tools, "_acquire_with_hooks",
                            lambda guard, save_dir, *a, **k: seen.setdefault("dir", save_dir))
        anywhere = os.path.join(os.sep, "anywhere", "at", "all")
        tools.run_zstack(mock_ctrl, unconstrained_guard, z_start_um=0, z_end_um=10,
                         z_step_um=1, save_dir=anywhere)
        assert seen["dir"] == os.path.abspath(anywhere)

    def test_the_acquisition_runner_enforces_it_even_if_a_caller_forgets(
        self, mock_ctrl, ws_guard
    ):
        """_acquire_with_hooks is the single filesystem choke point, so a future
        acquisition tool cannot escape by omitting the up-front resolve."""
        from microclaw import tools

        with pytest.raises(SafetyViolation, match="escapes"):
            tools._acquire_with_hooks(ws_guard, "/somewhere/else", "n", [], ctrl=mock_ctrl)

    def test_an_adaptive_hook_log_is_confined_too(self, mock_ctrl, ws_guard, monkeypatch):
        """Same bug one layer down: the hook writes the log itself, unguarded,
        while read_hook_log refuses to read it back."""
        from microclaw import tools

        monkeypatch.setattr(tools, "_acquire_with_hooks",
                            lambda *a, **k: pytest.fail("acquisition should not start"))
        with pytest.raises(SafetyViolation, match="escapes"):
            tools.run_zstack(
                mock_ctrl, ws_guard, z_start_um=0, z_end_um=10, z_step_um=1,
                save_dir="/somewhere/else", hook_strategy="autofocus_per_position",
                log_path="/somewhere/else/log.json",
            )

    def test_load_position_list_reads_outside_workspace(self, mock_ctrl, ws_guard):
        from microclaw import tools

        mock_ctrl.prepare_position_list.side_effect = FileNotFoundError
        with pytest.raises(FileNotFoundError):
            tools.load_position_list(mock_ctrl, ws_guard, path="/somewhere/else/p.pos")
        mock_ctrl.prepare_position_list.assert_called_once_with(
            os.path.abspath("/somewhere/else/p.pos")
        )


class TestArtifactDeclarations:
    """Tools that write a file say so structurally, so the transcript renderer
    can offer a download without regexing paths out of prose (design/16 §8)."""

    def test_save_position_list_declares_its_file(self, mock_ctrl, unconstrained_guard, tmp_path):
        from microclaw import tools

        path = str(tmp_path / "p.json")
        result = tools.save_position_list(mock_ctrl, unconstrained_guard, path=path)
        final_path = path + ".pos"
        assert result["artifact"] == {"kind": "position_list", "path": final_path}
        mock_ctrl.save_position_list.assert_called_once_with(final_path)

    def test_read_hook_log_declares_the_log(self, mock_ctrl, unconstrained_guard, tmp_path):
        from microclaw import tools

        log = tmp_path / "hook.json"
        log.write_text('[{"frame": 0}]', encoding="utf-8")
        result = tools.read_hook_log(mock_ctrl, unconstrained_guard, log_path=str(log))
        assert result["artifact"] == {"kind": "hook_log", "path": str(log)}

    def test_a_missing_hook_log_declares_nothing(self, mock_ctrl, unconstrained_guard, tmp_path):
        from microclaw import tools

        result = tools.read_hook_log(
            mock_ctrl, unconstrained_guard, log_path=str(tmp_path / "gone.json")
        )
        assert "error" in result and "artifact" not in result

    def test_read_hook_log_reads_outside_configured_workspace(self, mock_ctrl, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        log = tmp_path / "outside.json"
        log.write_text('[{"frame": 0}]', encoding="utf-8")
        guard = SafetyGuard(SafetyConstraints(workspace_dir=str(workspace)))
        assert tools.read_hook_log(mock_ctrl, guard, str(log))["entry_count"] == 1


class TestRunAOfflineTools:
    def _log(self, tmp_path, entries):
        from microclaw.hooks import HookBase, analysis_observation_record
        written = []
        for entry in entries:
            if entry.get("schema") == "microclaw.analysis-observation/v1":
                written.append(entry)
                continue
            metadata = {
                "PositionName": entry["position"],
                "XPosition_um_Intended": entry["x_um"],
                "YPosition_um_Intended": entry["y_um"],
                **({"ZPosition_um_Intended": entry["z_um"]}
                   if entry.get("z_um") is not None else {}),
            }
            written.append({
                **HookBase.where(metadata),
                **analysis_observation_record(
                    analyzer="test", analyzer_version="1", result=entry["result"]
                ),
            })
        path = tmp_path / "hook.json"
        path.write_text(json.dumps(written), encoding="utf-8")
        return str(path)

    def test_rank_hook_log_reports_all_invalid_rows_without_ranking_them(
        self, mock_ctrl, unconstrained_guard, tmp_path
    ):
        records = [
            {"position": "a", "x_um": 1, "y_um": 2, "result": {
                "snr": None, "snr_valid": False,
                "snr_invalid_reason": "saturated", "saturated_fraction": 0.001,
            }},
            {"position": "b", "x_um": 3, "y_um": 4, "result": {
                "snr": None, "snr_valid": False,
                "snr_invalid_reason": "saturated", "saturated_fraction": 0.002,
            }},
        ]
        result = tools.rank_hook_log(
            mock_ctrl, unconstrained_guard, self._log(tmp_path, records)
        )
        assert result["ranking"] == []
        assert [row["position"] for row in result["invalid_rows"]] == ["a", "b"]
        assert result["invalid_entry_count"] == 2
        assert "incomplete" in result["warning"]

    def test_rank_hook_log_ranks_valid_rows_and_lists_invalid_rows(
        self, mock_ctrl, unconstrained_guard, tmp_path
    ):
        records = [
            {"position": "valid", "x_um": 1, "y_um": 2,
             "result": {"snr": 8, "snr_valid": True}},
            {"position": "clipped", "x_um": 3, "y_um": 4, "result": {
                "snr": None, "snr_valid": False,
                "snr_invalid_reason": "saturated", "saturated_fraction": 0.001,
            }},
        ]
        result = tools.rank_hook_log(
            mock_ctrl, unconstrained_guard, self._log(tmp_path, records)
        )
        assert [row["position"] for row in result["ranking"]] == ["valid"]
        assert [row["position"] for row in result["invalid_rows"]] == ["clipped"]

    def test_rank_hook_log_sorts_metric_then_label(self, mock_ctrl, unconstrained_guard,
                                                   tmp_path):
        records = [
            {"position": "b", "x_um": 2, "y_um": 0,
             "result": {"snr": 4, "focus_metric_valid": True,
                        "saturated_fraction": 0}},
            {"position": "c", "x_um": 3, "y_um": 0,
             "result": {"snr": 9, "focus_metric_valid": True,
                        "saturated_fraction": 0}},
            {"position": "a", "x_um": 1, "y_um": 0,
             "result": {"snr": 4, "focus_metric_valid": True,
                        "saturated_fraction": 0}},
        ]
        result = tools.rank_hook_log(mock_ctrl, unconstrained_guard,
                                     self._log(tmp_path, records), budgets=[1, 3])
        assert [r["position"] for r in result["ranking"]] == ["c", "a", "b"]
        assert [r["rank"] for r in result["budget_views"]["3"]] == [1, 2, 3]

    def test_rank_hook_log_prefers_coverage_with_threshold_provenance(
        self, mock_ctrl, unconstrained_guard, tmp_path
    ):
        records = [{
            "schema": "microclaw.analysis-observation/v1", "position": "field",
            "x_um": 1, "y_um": 2,
            "parameters": {"min_snr": 3.1,
                           "min_snr_source": "package_default_uncalibrated"},
            "result": {"signal_coverage": 0.25},
        }]
        result = tools.rank_hook_log(
            mock_ctrl, unconstrained_guard, self._log(tmp_path, records),
            metric="signal_coverage",
        )
        assert result["ranking"][0]["min_snr"] == 3.1
        assert result["ranking"][0]["min_snr_source"] == "package_default_uncalibrated"
        assert result["ranking"][0]["signal_coverage_valid"] is None
        assert "deliberately have no validity flag" in result["metric_validity"]

    def test_rank_hook_log_refuses_to_rank_a_clipped_frame_by_coverage(
        self, mock_ctrl, unconstrained_guard, tmp_path
    ):
        """A clipped frame cannot say how much of the field is sample.

        Measured on the 2026-08-06 M5 raster: ranking its 324 tiles by
        signal_coverage put two saturated tiles (4.0% and 19.8% of pixels at
        full scale) in the top two slots, and both were frames snr had already
        refused to score. Coverage has no validity flag of its own, so the
        saturation gate has to be applied here (design/43 F6, block 43g).

        The limit is coverage's own and is deliberately far looser than snr's.
        Real bead fields (`stitch_test_1`) run 0.017%-0.220% saturated because a
        few bead centres hit full well; snr's 0.01% gate would refuse all six,
        and they are entirely usable. `beads` below is the least-clipped of them
        and must stay rankable.
        """
        records = [
            {"schema": "microclaw.analysis-observation/v1", "position": "clipped",
             "x_um": 1, "y_um": 2,
             "result": {"signal_coverage": 0.229, "saturated_fraction": 0.0404}},
            {"schema": "microclaw.analysis-observation/v1", "position": "clean",
             "x_um": 3, "y_um": 4,
             "result": {"signal_coverage": 0.148, "saturated_fraction": 0.0}},
            {"schema": "microclaw.analysis-observation/v1", "position": "beads",
             "x_um": 5, "y_um": 6,
             "result": {"signal_coverage": 0.0958, "saturated_fraction": 0.0002}},
        ]
        result = tools.rank_hook_log(
            mock_ctrl, unconstrained_guard, self._log(tmp_path, records),
            metric="signal_coverage",
        )
        assert [r["position"] for r in result["ranking"]] == ["clean", "beads"]
        assert [r["position"] for r in result["invalid_rows"]] == ["clipped"]
        assert "saturated" in result["invalid_rows"][0]["invalid_reason"]
        assert result["ranked_entry_count"] == 2

    def test_rank_hook_log_ranks_a_clipped_frame_when_the_metric_is_snr(
        self, mock_ctrl, unconstrained_guard, tmp_path
    ):
        """The saturation gate is coverage's, not a new rule for every metric.

        snr already refuses a clipped frame upstream in snr_validity, so a log
        whose producer scored one anyway must keep ranking the way it did.
        """
        records = [
            {"schema": "microclaw.analysis-observation/v1", "position": "clipped",
             "x_um": 1, "y_um": 2,
             "result": {"snr": 9.0, "saturated_fraction": 0.0404}},
        ]
        result = tools.rank_hook_log(
            mock_ctrl, unconstrained_guard, self._log(tmp_path, records), metric="snr",
        )
        assert [r["position"] for r in result["ranking"]] == ["clipped"]

    def test_rank_hook_log_explains_that_old_log_predates_coverage(
        self, mock_ctrl, unconstrained_guard, tmp_path
    ):
        records = [{"position": "old", "x_um": 1, "y_um": 2,
                    "result": {"snr": 5}}]
        result = tools.rank_hook_log(
            mock_ctrl, unconstrained_guard, self._log(tmp_path, records),
            metric="signal_coverage",
        )
        assert "predates the signal_coverage statistic" in result["error"]

    def test_rank_hook_log_rejects_duplicates(self, mock_ctrl, unconstrained_guard,
                                               tmp_path):
        record = {"position": "a", "x_um": 1, "y_um": 2,
                  "result": {"snr": 1}}
        result = tools.rank_hook_log(mock_ctrl, unconstrained_guard,
                                     self._log(tmp_path, [record, record]))
        assert "Duplicate position" in result["error"]

    def test_rank_hook_log_verifies_saved_labels_and_coordinates(
        self, mock_ctrl, unconstrained_guard, tmp_path
    ):
        from microclaw.controller import PositionProjection
        records = [
            {"position": "top", "x_um": 1, "y_um": 2,
             "result": {"snr": 9}},
            {"position": "low", "x_um": 3, "y_um": 4,
             "result": {"snr": 2}},
        ]
        positions = tmp_path / "positions.pos"
        positions.write_text("native fixture placeholder", encoding="utf-8")
        native = [{"name": "top", "x_um": 99, "y_um": 2}]
        mock_ctrl.project_position_list_file.return_value = PositionProjection(
            [], native, []
        )
        result = tools.rank_hook_log(
            mock_ctrl, unconstrained_guard, self._log(tmp_path, records),
            position_list_path=str(positions),
        )
        verification = result["position_list_verification"]
        assert verification["label_match"] is True
        assert verification["coordinate_matches"] == [False]
        assert verification["matches_ranking_prefix"] is False

    def test_rank_hook_log_tolerates_legacy_rounding_and_checks_axis_presence(
        self, mock_ctrl, unconstrained_guard, tmp_path
    ):
        from microclaw.controller import PositionProjection
        records = [{
            "position": "top", "x_um": 1.234, "y_um": 2.346,
            "result": {"snr": 9},
        }]
        positions = tmp_path / "positions.pos"
        positions.write_text("native fixture placeholder", encoding="utf-8")
        native = [{"name": "top", "x_um": 1.23449, "y_um": 2.34551}]
        mock_ctrl.project_position_list_file.return_value = PositionProjection(
            [], native, []
        )
        result = tools.rank_hook_log(
            mock_ctrl, unconstrained_guard, self._log(tmp_path, records),
            position_list_path=str(positions),
        )
        assert result["position_list_verification"]["coordinate_matches"] == [True]

        # A saved position may ADD an axis the ranking lacks. Run A marks its
        # top-k with a focus Z the operator supplies, while a fixed-Z survey
        # stamps no ZPosition_um_Intended and so its records carry no z_um.
        # Treating that as a mismatch reported M5's correct k=2 save as a
        # coordinate mismatch with X and Y agreeing exactly (R1, 2026-07-28).
        mock_ctrl.project_position_list_file.return_value = PositionProjection(
            [], [{**native[0], "z_um": 5.0}], []
        )
        result = tools.rank_hook_log(
            mock_ctrl, unconstrained_guard, self._log(tmp_path, records),
            position_list_path=str(positions),
        )
        verification = result["position_list_verification"]
        assert verification["coordinate_matches"] == [True]
        assert verification["matches_ranking_prefix"] is True
        assert verification["axes_added_when_saved"] == [["z_um"]]

    def test_a_saved_position_that_drops_a_ranked_axis_is_still_a_mismatch(
        self, mock_ctrl, unconstrained_guard, tmp_path
    ):
        """The permissive direction is one-way. An axis the ranking carries and
        the saved list lost means the selection is not what was ranked."""
        from microclaw.controller import PositionProjection
        records = [{
            "position": "top", "x_um": 1.0, "y_um": 2.0, "z_um": 3.0,
            "result": {"snr": 9},
        }]
        positions = tmp_path / "positions.pos"
        positions.write_text("native fixture placeholder", encoding="utf-8")
        mock_ctrl.project_position_list_file.return_value = PositionProjection(
            [], [{"name": "top", "x_um": 1.0, "y_um": 2.0}], []
        )
        result = tools.rank_hook_log(
            mock_ctrl, unconstrained_guard, self._log(tmp_path, records),
            position_list_path=str(positions),
        )
        verification = result["position_list_verification"]
        assert verification["coordinate_matches"] == [False]
        assert verification["matches_ranking_prefix"] is False

    def test_validate_positions_names_the_limit_hit_but_never_clips(self, mock_ctrl):
        # Renamed from ..._does_not_move_or_expose by block 50b. design/26
        # coupled "never expose guard limits" to "never clip" on the theory that
        # an agent which cannot see the limits cannot clip to them; it does not
        # hold, since every move refusal already names the limit it hit, and the
        # silence cost the M5 session of 2026-08-12 (design/50 Problem 2).
        # What survives is narrower and is both halves of this test: name the
        # limit the rejected position hit, never dump the limits table, never
        # clip.
        guard = SafetyGuard(SafetyConstraints(
            stage=StageConstraints(x_min=0, x_max=10, y_min=0, y_max=10,
                                   z_min=0, z_max=5)))
        result = tools.validate_positions(mock_ctrl, guard, [
            {"name": "ok", "x_um": 2, "y_um": 3, "z_um": 4},
            {"name": "bad", "x_um": 20, "y_um": 3, "z_um": 4},
        ])
        assert [p["name"] for p in result["accepted"]] == ["ok"]
        assert [p["name"] for p in result["rejected"]] == ["bad"]
        assert result["rejected"][0]["reason"] == (
            "X=20.0 µm exceeds the maximum allowed (10.0 µm)."
        )
        # The limit that was hit is disclosed; the envelope is not. This
        # position never hit Y or Z, so neither axis may be named anywhere in
        # the payload -- that, and not the absence of the digits, is what
        # "never dump the limits table" means.
        assert "Y=" not in json.dumps(result)
        assert "Z=" not in json.dumps(result)
        assert result["clipped"] == 0
        assert "limits" not in result
        assert [p["x_um"] for p in result["accepted"]] == [2]   # unclipped
        mock_ctrl.set_xy.assert_not_called()
        mock_ctrl.studio.live().snap.assert_not_called()

    def test_validate_positions_reports_z_guard_message(self, mock_ctrl):
        guard = SafetyGuard(SafetyConstraints(
            stage=StageConstraints(x_min=-10, x_max=10, y_min=-10, y_max=10,
                                   z_min=0, z_max=5)))
        result = tools.validate_positions(mock_ctrl, guard, [
            {"name": "bad-z", "x_um": 2, "y_um": 3, "z_um": 8},
        ])
        assert result["rejected"] == [{
            "name": "bad-z",
            "reason": "Z=8.0 µm exceeds the maximum allowed (5.0 µm).",
        }]
        mock_ctrl.core.set_position.assert_not_called()
        mock_ctrl.core.set_xy_position.assert_not_called()

    def test_inspect_artifacts_hashes_recursively(self, mock_ctrl, unconstrained_guard,
                                                  tmp_path):
        (tmp_path / "d").mkdir()
        (tmp_path / "d" / "a.txt").write_text("abc", encoding="utf-8")
        result = tools.inspect_artifacts(mock_ctrl, unconstrained_guard,
                                         [str(tmp_path / "d")])
        assert result["artifact_count"] == 1
        assert result["artifacts"][0]["sha256"] == (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )

    def test_inspect_artifacts_refuses_before_hashing_over_file_limit(
            self, mock_ctrl, unconstrained_guard, tmp_path):
        (tmp_path / "a").write_bytes(b"a")
        (tmp_path / "b").write_bytes(b"b")
        result = tools.inspect_artifacts(
            mock_ctrl, unconstrained_guard, [str(tmp_path)], max_files=1
        )
        assert "reached max_files=1" in result["error"]
        assert result["survey_totals"] == {"file_count": 1, "total_bytes": 1}
        assert result["survey"][0]["direct_file_count"] == 2

    def test_inspect_artifacts_refuses_before_hashing_over_byte_limit(
            self, mock_ctrl, unconstrained_guard, tmp_path):
        (tmp_path / "a").write_bytes(b"abc")
        result = tools.inspect_artifacts(
            mock_ctrl, unconstrained_guard, [str(tmp_path)], max_total_bytes=2
        )
        assert "max_total_bytes=2" in result["error"]
        assert result["survey"][0]["direct_bytes"] == 3

    def test_inspect_artifacts_listing_only_skips_hash_and_byte_read_limit(
            self, mock_ctrl, unconstrained_guard, tmp_path):
        (tmp_path / "dataset").mkdir()
        (tmp_path / "dataset" / "NDTiff.index").write_bytes(b"index")
        result = tools.inspect_artifacts(
            mock_ctrl, unconstrained_guard, [str(tmp_path)],
            hash=False, max_total_bytes=1,
        )
        assert result["hashes_computed"] is False
        assert "sha256" not in result["artifacts"][0]
        dataset_row = next(
            row for row in result["survey"] if row["path"] == str(tmp_path / "dataset")
        )
        assert dataset_row == {
            "path": str(tmp_path / "dataset"), "depth": 1,
            "direct_file_count": 1, "direct_bytes": 5,
        }

    def test_compare_revisit_frames_recovers_translation(self, mock_ctrl,
                                                          unconstrained_guard,
                                                          tmp_path, monkeypatch):
        from scipy.ndimage import shift
        rng = np.random.default_rng(7)
        source = rng.normal(size=(64, 64)).astype(np.float32)
        revisit = shift(source, (2.25, -1.5), mode="constant", cval=0)
        a, b = tmp_path / "a.tif", tmp_path / "b.tif"
        tools.tifffile.imwrite(a, source)
        tools.tifffile.imwrite(b, revisit)
        monkeypatch.setattr(tools, "_resolve_current_affine", lambda ctrl: (None, {}))
        result = tools.compare_revisit_frames(
            mock_ctrl, unconstrained_guard, str(a), str(b),
            [{"position": "p", "source_index": 0, "revisit_index": 0}],
            min_correlation=0.8,
        )
        row = result["comparisons"][0]
        assert row["shift_to_apply_to_revisit_dy_dx_px"] == pytest.approx(
            [-2.25, 1.5], abs=0.15
        )
        assert row["registration_valid"] is True
        assert row["realign_move_um"] is None

    def test_calibrate_snr_requires_replicates_and_writes_artifact(
        self, mock_ctrl, unconstrained_guard, tmp_path
    ):
        def log(name, values):
            path = tmp_path / name
            path.write_text(json.dumps([{"result": {"snr": v}} for v in values]), encoding="utf-8")
            return str(path)
        dark = [log("d1.json", [3.0]), log("d2.json", [3.1])]
        lit = [log("l1.json", [20.0]), log("l2.json", [25.0])]
        out = tmp_path / "cal.json"
        result = tools.calibrate_snr_threshold(
            mock_ctrl, unconstrained_guard, dark, lit, str(out),
            {"objective": "20x", "camera": "Andor", "roi": [0, 0, 64, 64],
             "binning": 1, "exposure_ms": 100, "channel": "561"},
        )
        assert result["recommended_min_snr"] == pytest.approx(11.55)
        assert result["artifact"]["path"] == str(out)

    def test_offline_read_inputs_may_be_outside_configured_workspace(
        self, mock_ctrl, tmp_path, monkeypatch
    ):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        guard = SafetyGuard(SafetyConstraints(workspace_dir=str(workspace)))

        artifact = tmp_path / "outside.txt"
        artifact.write_text("outside", encoding="utf-8")
        assert tools.inspect_artifacts(mock_ctrl, guard, [str(artifact)])["artifact_count"] == 1

        hook_log = tmp_path / "outside-hook.json"
        hook_log.write_text(Path(self._log(tmp_path, [{
            "position": "p0", "x_um": 1, "y_um": 2, "result": {"snr": 9}
        }])).read_text(encoding="utf-8"), encoding="utf-8")
        assert tools.rank_hook_log(mock_ctrl, guard, str(hook_log))["entry_count"] == 1
        position_list = tmp_path / "outside.pos"
        position_list.write_text("{}", encoding="utf-8")
        projection = MagicMock(native_entries=[], issues=[])
        mock_ctrl.project_position_list_file.return_value = projection
        ranked = tools.rank_hook_log(
            mock_ctrl, guard, str(hook_log), position_list_path=str(position_list)
        )
        assert ranked["position_list_verification"]["path"] == str(position_list)

        class FakeDataset:
            axes = {}
            def __init__(self, path):
                assert path == str(tmp_path / "outside-dataset")
            def has_image(self, **kwargs):
                return True
            def read_image(self, **kwargs):
                return np.zeros((2, 2), dtype=np.uint16)

        monkeypatch.setattr(tools, "Dataset", FakeDataset)
        monkeypatch.setattr(tools.tifffile, "imwrite", lambda *args, **kwargs: None)
        exported = tools.export_dataset_as_tiff(
            mock_ctrl, guard, str(tmp_path / "outside-dataset"),
            str(workspace / "export.tif"),
        )
        assert "error" not in exported

        def snr_log(name, value):
            path = tmp_path / name
            path.write_text(json.dumps([{"result": {"snr": value}}]), encoding="utf-8")
            return str(path)
        calibrated = tools.calibrate_snr_threshold(
            mock_ctrl, guard,
            [snr_log("dark1.json", 2), snr_log("dark2.json", 3)],
            [snr_log("lit1.json", 10), snr_log("lit2.json", 11)],
            str(workspace / "calibration.json"),
            {"objective": "20x", "camera": "cam", "roi": [0, 0, 2, 2],
             "binning": 1, "exposure_ms": 10, "channel": "DAPI"},
        )
        assert calibrated["recommended_min_snr"] == 6.5

    def test_compare_revisit_reads_both_tiffs_outside_workspace(
        self, mock_ctrl, tmp_path, monkeypatch
    ):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        guard = SafetyGuard(SafetyConstraints(workspace_dir=str(workspace)))
        rng = np.random.default_rng(3)
        image = rng.normal(size=(32, 32)).astype(np.float32)
        source, revisit = tmp_path / "source.tif", tmp_path / "revisit.tif"
        tools.tifffile.imwrite(source, image)
        tools.tifffile.imwrite(revisit, image)
        monkeypatch.setattr(tools, "_resolve_current_affine", lambda ctrl: (None, {}))
        result = tools.compare_revisit_frames(
            mock_ctrl, guard, str(source), str(revisit),
            [{"position": "p", "source_index": 0, "revisit_index": 0}],
        )
        assert len(result["comparisons"]) == 1


class TestMarkPosition:
    def test_saves_position(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.xy_position.update(x=100.0, y=200.0)
        mock_ctrl.core.get_position.return_value = 50.0
        result = mark_position(mock_ctrl, unconstrained_guard, name="test_pos")
        mock_ctrl.add_position.assert_called_once_with("test_pos", 100.0, 200.0, 50.0)
        assert result["x_um"] == 100.0
        assert result["y_um"] == 200.0
        assert result["z_um"] == 50.0

    def test_xy_safety_check(self, mock_ctrl):
        guard = SafetyGuard(SafetyConstraints(stage=StageConstraints(x_max=100.0)))
        mock_ctrl.core.xy_position.update(x=200.0, y=0.0)
        mock_ctrl.core.get_position.return_value = 50.0
        with pytest.raises(SafetyViolation):
            mark_position(mock_ctrl, guard, name="out_of_bounds")

    def test_without_z(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.xy_position.update(x=10.0, y=20.0)
        result = mark_position(mock_ctrl, unconstrained_guard, name="no_z", include_z=False)
        assert "z_um" not in result
        mock_ctrl.add_position.assert_called_once_with("no_z", 10.0, 20.0, None)

    def test_supplied_coordinates_record_without_moving_or_imaging(
        self, mock_ctrl, unconstrained_guard
    ):
        # design/23 F3 / Episode B: a known coordinate goes into the list with no
        # stage read, no move, no exposure. The stage accessors must not be touched.
        result = mark_position(
            mock_ctrl, unconstrained_guard, name="cell_07",
            x_um=1234.5, y_um=-678.9, z_um=42.0,
        )
        mock_ctrl.add_position.assert_called_once_with("cell_07", 1234.5, -678.9, 42.0)
        mock_ctrl.core.get_x_position.assert_not_called()
        mock_ctrl.core.get_y_position.assert_not_called()
        mock_ctrl.studio.live().snap.assert_not_called()
        assert result["imaged"] is False
        assert result["stage_moved"] is False
        assert (result["x_um"], result["y_um"], result["z_um"]) == (1234.5, -678.9, 42.0)

    def test_current_position_reports_stage_moved_false_but_read(
        self, mock_ctrl, unconstrained_guard
    ):
        # Marking the current position still images nothing (stage_moved is about
        # whether a supplied coord bypassed a move; the current-pos form never moves
        # either, but it does READ the stage).
        mock_ctrl.core.get_x_position.return_value = 5.0
        mock_ctrl.core.get_y_position.return_value = 6.0
        mock_ctrl.core.get_position.return_value = 7.0
        result = mark_position(mock_ctrl, unconstrained_guard, name="here")
        assert result["imaged"] is False
        assert result["stage_moved"] is True

    def test_supplied_coordinates_are_guarded_before_writing(self, mock_ctrl):
        guard = SafetyGuard(SafetyConstraints(stage=StageConstraints(x_max=100.0)))
        with pytest.raises(SafetyViolation):
            mark_position(mock_ctrl, guard, name="too_far", x_um=999.0, y_um=0.0)
        mock_ctrl.add_position.assert_not_called()

    def test_x_without_y_is_an_error(self, mock_ctrl, unconstrained_guard):
        result = mark_position(mock_ctrl, unconstrained_guard, name="half", x_um=1.0)
        assert "error" in result
        mock_ctrl.add_position.assert_not_called()


class TestLoadPositionListValidation:
    def test_out_of_bounds_entry_returns_conflict_without_commit(self, mock_ctrl, default_guard):
        from microclaw.controller import PreparedPositionList, PositionProjection
        from microclaw.tools import load_position_list
        stored = [
            {"name": "Good", "x_um": 0.0, "y_um": 0.0, "z_um": 50.0},
            {"name": "BadZ", "x_um": 0.0, "y_um": 0.0, "z_um": 999.0},
        ]
        projection = PositionProjection(stored, stored, [])
        mock_ctrl.prepare_position_list.return_value = PreparedPositionList(
            object(), projection, "x.pos", "abc"
        )

        result = load_position_list(mock_ctrl, default_guard, path="x.pos")
        issues = result["position_list_conflict"]["issues"]
        assert [(i["code"], i["label"]) for i in issues] == [
            ("unsafe_coordinate", "BadZ")
        ]
        mock_ctrl.commit_position_list.assert_not_called()

    def test_z_only_entry_commits(self, mock_ctrl, default_guard):
        from microclaw.controller import PreparedPositionList, PositionProjection
        from microclaw.tools import load_position_list
        stored = [{"name": "Zonly", "z_um": 50.0}]
        projection = PositionProjection(stored, stored, [])
        prepared = PreparedPositionList(object(), projection, "x.pos", "abc")
        mock_ctrl.prepare_position_list.return_value = prepared
        result = load_position_list(mock_ctrl, default_guard, path="x.pos")
        assert result["count"] == 1
        assert "rejected" not in result
        mock_ctrl.commit_position_list.assert_called_once_with(prepared)

    def test_confirmed_file_resolution_rechecks_hash_and_removes_candidate_index(
        self, mock_ctrl, default_guard, monkeypatch
    ):
        from microclaw.controller import PreparedPositionList, PositionProjection
        from microclaw.tools import load_position_list
        bad = PositionProjection([], [{"index": 0, "name": "Bad"}], [{
            "code": "unsupported_only", "index": 0, "label": "Bad",
            "details": "unsupported", "allowed_resolutions": ["remove_entry"],
        }])
        candidate = MagicMock()
        candidate.get_number_of_positions.return_value = 1
        prepared = PreparedPositionList(candidate, bad, "x.pos", "reviewed-hash")
        mock_ctrl.prepare_position_list.return_value = prepared
        clean = PositionProjection([], [], [])
        mock_ctrl.project_position_list.return_value = clean
        monkeypatch.setattr("microclaw.tools.CONFIRM_FN", lambda summary: True)

        result = load_position_list(
            mock_ctrl, default_guard, path="x.pos",
            remove_conflicting_indexes=[0], expected_content_hash="reviewed-hash",
        )

        candidate.remove_position.assert_called_once_with(0)
        mock_ctrl.commit_position_list.assert_called_once()
        assert result["count"] == 0

    def test_file_resolution_refuses_changed_hash(self, mock_ctrl, default_guard):
        from microclaw.controller import PreparedPositionList, PositionProjection
        from microclaw.tools import load_position_list
        prepared = PreparedPositionList(object(), PositionProjection([], [], []),
                                        "x.pos", "new-hash")
        mock_ctrl.prepare_position_list.return_value = prepared
        result = load_position_list(
            mock_ctrl, default_guard, path="x.pos",
            remove_conflicting_indexes=[0], expected_content_hash="old-hash",
        )
        assert result["position_list_conflict"]["issues"][0]["code"] == (
            "file_changed_since_review"
        )
        mock_ctrl.commit_position_list.assert_not_called()


class TestGetPositionList:
    def test_returns_positions(self, mock_ctrl, unconstrained_guard):
        from microclaw.controller import PositionProjection
        positions = [{"name": "Pos1", "x_um": 0.0, "y_um": 0.0}]
        projection = PositionProjection(positions, positions, [])
        mock_ctrl.inspect_current_position_list.return_value = projection
        result = get_position_list(mock_ctrl, unconstrained_guard)
        assert result["count"] == 1
        assert result["positions"][0]["name"] == "Pos1"
        mock_ctrl.set_position_projection.assert_called_once()


class TestGoToPosition:
    def test_moves_to_existing(self, mock_ctrl, unconstrained_guard):
        from microclaw.controller import PositionProjection
        positions = [
            {"name": "Pos1", "x_um": 100.0, "y_um": 200.0, "z_um": 50.0}
        ]
        mock_ctrl.inspect_current_position_list.return_value = PositionProjection(
            positions, positions, []
        )
        result = go_to_position(mock_ctrl, unconstrained_guard, name="Pos1")
        mock_ctrl.go_to_position.assert_called_once_with("Pos1")
        assert result["status"] == "Moved to 'Pos1'."

    def test_error_for_missing(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.get_positions.return_value = []
        result = go_to_position(mock_ctrl, unconstrained_guard, name="Ghost")
        assert "error" in result

    def test_safety_check_xy(self, mock_ctrl):
        from microclaw.controller import PositionProjection
        guard = SafetyGuard(SafetyConstraints(stage=StageConstraints(x_max=50.0)))
        positions = [
            {"name": "Far", "x_um": 200.0, "y_um": 0.0}
        ]
        mock_ctrl.inspect_current_position_list.return_value = PositionProjection(
            positions, positions, []
        )
        result = go_to_position(mock_ctrl, guard, name="Far")
        assert result["position_list_conflict"]["issues"][0]["code"] == "unsafe_coordinate"
        mock_ctrl.go_to_position.assert_not_called()

    def test_explicit_preserve_unsupported_allows_valid_target(
        self, mock_ctrl, unconstrained_guard
    ):
        from microclaw.controller import PositionProjection
        positions = [{"name": "Pos1", "x_um": 1.0, "y_um": 2.0}]
        issues = [{
            "code": "unsupported_only", "index": 1, "label": "OtherRig",
            "details": "unsupported", "allowed_resolutions": ["preserve_and_omit"],
        }]
        mock_ctrl.inspect_current_position_list.return_value = PositionProjection(
            positions, positions + [{"name": "OtherRig"}], issues
        )
        refused = go_to_position(mock_ctrl, unconstrained_guard, name="Pos1")
        assert "position_list_conflict" in refused
        accepted = go_to_position(
            mock_ctrl, unconstrained_guard, name="Pos1", preserve_unsupported=True
        )
        assert accepted["status"] == "Moved to 'Pos1'."


class TestDeletePosition:
    def test_calls_remove(self, mock_ctrl, unconstrained_guard):
        result = delete_position(mock_ctrl, unconstrained_guard, name="Pos1")
        mock_ctrl.remove_position.assert_called_once_with("Pos1")
        assert "deleted" in result["status"]


class TestClearPositionList:
    def test_calls_clear(self, mock_ctrl, unconstrained_guard):
        result = clear_position_list(mock_ctrl, unconstrained_guard)
        mock_ctrl.clear_positions.assert_called_once()
        assert "cleared" in result["status"]


class TestListHooks:
    def test_includes_precoded(self, mock_ctrl, unconstrained_guard):
        result = list_hooks(mock_ctrl, unconstrained_guard)
        assert "autofocus_per_position" in result["precoded"]
        assert "focus_feedback" in result["precoded"]
        assert "intensity_adaptive" in result["precoded"]
        assert "position_filter" in result["precoded"]
        assert "snr_observer" in result["precoded"]
        assert "saved" in result


class TestGenerateAndSaveHook:
    def test_saves_valid_hook(self, mock_ctrl, unconstrained_guard, tmp_path, monkeypatch):
        monkeypatch.setattr("microclaw.hook_manager.HOOKS_DIR", tmp_path)
        monkeypatch.setattr("microclaw.hook_manager.MANIFEST", tmp_path / "manifest.json")
        code = "class H:\n    def image_process_fn(self, img, meta, q): return img, meta\n"
        result = generate_and_save_hook(
            mock_ctrl, unconstrained_guard,
            name="my_hook", code=code,
            description="Test hook", source="claude_generated",
        )
        assert "saved" in result["status"]

    def test_refuses_rig_hook_with_missing_decision_imports_before_saving(
        self, mock_ctrl, unconstrained_guard, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("microclaw.hook_manager.HOOKS_DIR", tmp_path)
        monkeypatch.setattr("microclaw.hook_manager.MANIFEST", tmp_path / "manifest.json")
        code = (
            "class block45_one_tile:\n"
            "    def analyze_frame(self, image, metadata):\n"
            "        return HookResult(measurements={'gate': 'block45'}, "
            "actions=(StopAcquisition(),))\n"
        )

        result = generate_and_save_hook(
            mock_ctrl, unconstrained_guard, name="block45_one_tile", code=code,
            description="rig reproduction", source="claude_generated",
        )

        assert result["error"] == "Hook failed static preflight; it was not saved."
        assert result["contract_errors"] == [
            "HookResult is called but is not imported or defined. Add: "
            "from microclaw.hook_decisions import HookResult",
            "StopAcquisition is called but is not imported or defined. Add: "
            "from microclaw.hook_decisions import StopAcquisition",
        ]
        assert not (tmp_path / "block45_one_tile.py").exists()
        assert not (tmp_path / "manifest.json").exists()

    @pytest.mark.parametrize("source_reason", ["hookbase", "log_path"])
    def test_refuses_source_the_runner_would_refuse_before_writing(
        self, source_reason, mock_ctrl, unconstrained_guard, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("microclaw.hook_manager.HOOKS_DIR", tmp_path)
        monkeypatch.setattr("microclaw.hook_manager.MANIFEST", tmp_path / "manifest.json")
        base = "(HookBase)" if source_reason == "hookbase" else ""
        import_line = "from microclaw.hooks import HookBase\n" if base else ""
        init = "    def __init__(self, log_path=None): pass\n" if source_reason == "log_path" else ""
        code = (
            f"{import_line}class H{base}:\n{init}"
            "    def analyze_frame(self, image, metadata): return None\n"
        )
        result = generate_and_save_hook(
            mock_ctrl, unconstrained_guard, name="dead", code=code,
            description="Cannot resolve", source="claude_generated",
        )

        assert result["error"] == "Hook would be refused at run time; it was not saved."
        refusal = result["resolve_refusal"]
        assert refusal["would_refuse"] is True
        assert refusal["reasons"]
        assert refusal["remedy"]["insufficient_for"] == refusal["reasons"]
        assert "source has to change" in refusal["remedy"]["note"]
        assert not (tmp_path / "dead.py").exists()

    def test_declines_when_lint_flags_and_user_says_no(self, mock_ctrl, unconstrained_guard, monkeypatch):
        monkeypatch.setattr("microclaw.tools.CONFIRM_FN", lambda summary, kind="action": False)
        code = "eval('os.system(\"rm -rf /\")')"
        result = generate_and_save_hook(
            mock_ctrl, unconstrained_guard,
            name="bad_hook", code=code,
            description="Evil hook", source="claude_generated",
        )
        assert "error" in result
        assert result["warnings"]

    def test_saves_flagged_hook_after_confirmation(self, mock_ctrl, unconstrained_guard, tmp_path, monkeypatch):
        # A benign hook that writes its own log via open() trips the lint but is
        # saveable once the user confirms.
        monkeypatch.setattr("microclaw.hook_manager.HOOKS_DIR", tmp_path)
        monkeypatch.setattr("microclaw.hook_manager.MANIFEST", tmp_path / "manifest.json")
        monkeypatch.setattr("microclaw.tools.CONFIRM_FN", lambda summary, kind="action": True)
        code = (
            "class H:\n"
            "    def image_process_fn(self, img, meta, q):\n"
            "        open('/tmp/hooklog', 'a').write('x')\n"
            "        return img, meta\n"
        )
        result = generate_and_save_hook(
            mock_ctrl, unconstrained_guard,
            name="logging_hook", code=code,
            description="Writes a log", source="user_provided",
        )
        assert "saved" in result["status"]
        assert result["warnings"]  # lint still surfaced them


class TestReadHookFromFile:
    def test_reads_valid_file(self, mock_ctrl, unconstrained_guard, tmp_path):
        hook_file = tmp_path / "good.py"
        code = "class H:\n    def image_process_fn(self, img, meta, q): return img, meta\n"
        hook_file.write_text(code, encoding="utf-8")
        result = read_hook_from_file(mock_ctrl, unconstrained_guard, path=str(hook_file))
        assert result["code"] == code
        assert result["warnings"] == []

    def test_error_for_missing_file(self, mock_ctrl, unconstrained_guard):
        result = read_hook_from_file(mock_ctrl, unconstrained_guard, path="/no/such/file.py")
        assert "error" in result

    def test_reads_outside_configured_workspace(self, mock_ctrl, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        hook_file = tmp_path / "outside.py"
        hook_file.write_text(
            "class H:\n    def image_process_fn(self, img, meta, q): return img, meta\n"
        , encoding="utf-8")
        guard = SafetyGuard(SafetyConstraints(workspace_dir=str(workspace)))
        assert read_hook_from_file(mock_ctrl, guard, str(hook_file))["path"] == str(hook_file)


class TestSaveKnowledgeConfirmation:
    def test_session_c_position_map_shape_without_discriminator_is_refused_with_shape(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw import tools
        mock_ctrl._state_device_inventory = {"devices": [{
            "device": "Path", "adapter": "DLightPath",
            "allowed": ["State-0", "State-1", "State-2"],
        }]}
        monkeypatch.setattr(tools, "CONFIRM_FN",
                            lambda *a, **k: pytest.fail("confirmation must not run"))
        result = tools.save_knowledge(mock_ctrl, unconstrained_guard, "devices",
                                      "Path_light_path_selector", {
            "description": "Motorized light-path selector",
            "device": "Path",
            "property": "Label",
            "position_map": {"State-0": "eyepiece", "State-1": "left camera",
                             "State-2": "right camera"},
            "observed_on": "DCam",
        })
        assert "missing the structured discriminator" in result["error"]
        assert "'kind': 'optical_path_position_map'" in result["error"]
        assert "'device': <StateDevice config label>" in result["error"]
        assert "'positions':" in result["error"]
        assert "Do not send observed_on" in result["error"]

    def test_ordinary_note_about_same_state_device_still_saves(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw import tools
        mock_ctrl._state_device_inventory = {"devices": [{
            "device": "Path", "adapter": "DLightPath",
            "allowed": ["State-0", "State-1", "State-2"],
        }]}
        prompted, saved = [], []
        monkeypatch.setattr(tools, "CONFIRM_FN",
                            lambda summary, kind="action": prompted.append(summary) or True)
        monkeypatch.setattr("microclaw.knowledge_manager.save_entry",
                            lambda *args: saved.append(args))
        value = {"description": "Selector detent is stiff", "device": "Path",
                 "observed_on": "DCam", "caveat": {"service": "inspect annually"}}
        result = tools.save_knowledge(mock_ctrl, unconstrained_guard,
                                      "devices", "path_note", value)
        assert result["value"] == value
        assert prompted and saved == [("devices", "path_note", value)]

    @pytest.mark.parametrize("inventory", [None, "absent"])
    def test_structured_map_refuses_unbuilt_inventory_with_actual_cause(
        self, mock_ctrl, unconstrained_guard, monkeypatch, inventory
    ):
        from microclaw import tools
        if inventory is None:
            mock_ctrl._state_device_inventory = None
        else:
            del mock_ctrl._state_device_inventory
        monkeypatch.setattr(tools, "CONFIRM_FN",
                            lambda *a, **k: pytest.fail("confirmation must not run"))
        result = tools.save_knowledge(mock_ctrl, unconstrained_guard, "devices", "path", {
            "kind": "optical_path_position_map", "device": "Path",
            "positions": {"State-0": "camera"},
        })
        assert "inventory is unavailable" in result["error"]
        assert "validation did not build it" in result["error"]

    def test_structured_position_map_resolves_identity_before_confirmation(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw import tools
        mock_ctrl._state_device_inventory = {"devices": [{
            "device": "Path", "adapter": "RouteAdapter",
            "adapter_description": "rewordable prose", "allowed": ["State-0", "State-1"],
        }]}
        mock_ctrl.core.get_camera_device.return_value = "Camera"
        mock_ctrl.core.get_device_name.return_value = "CamAdapter"
        prompted, saved = [], []
        monkeypatch.setattr(tools, "CONFIRM_FN",
                            lambda summary, kind="action": prompted.append(summary) or True)
        monkeypatch.setattr("microclaw.knowledge_manager.save_entry",
                            lambda *args: saved.append(args))
        value = {"kind": "optical_path_position_map", "device": "Path",
                 "positions": {"State-0": "camera", "State-1": "eyepieces"}}
        result = tools.save_knowledge(mock_ctrl, unconstrained_guard, "devices", "path", value)
        expected = {"camera_adapter": "CamAdapter", "device": "Path",
                    "adapter": "RouteAdapter", "allowed": ["State-0", "State-1"]}
        assert result["value"]["observed_on"] == expected
        assert saved[0][2]["observed_on"] == expected
        assert "adapter_description" not in saved[0][2]["observed_on"]
        assert "CamAdapter" in prompted[0] and "State-1" in prompted[0]

    def test_wrong_caller_supplied_position_map_identity_is_not_prompted_or_saved(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw import tools
        mock_ctrl._state_device_inventory = {"devices": [{
            "device": "Path", "adapter": "LiveAdapter", "allowed": ["State-0"],
        }]}
        mock_ctrl.core.get_camera_device.return_value = "Camera"
        mock_ctrl.core.get_device_name.return_value = "CamAdapter"
        monkeypatch.setattr(tools, "CONFIRM_FN",
                            lambda *a, **k: pytest.fail("confirmation must not run"))
        monkeypatch.setattr("microclaw.knowledge_manager.save_entry",
                            lambda *a: pytest.fail("save must not run"))
        result = tools.save_knowledge(mock_ctrl, unconstrained_guard, "devices", "path", {
            "kind": "optical_path_position_map", "device": "Path",
            "positions": {"State-0": "camera"},
            "observed_on": {"camera_adapter": "CamAdapter", "device": "Path",
                            "adapter": "PlausibleButWrong", "allowed": ["State-0"]},
        })
        assert "differs" in result["error"]

    @pytest.mark.parametrize(("inventory", "field"), [
        ({"devices": []}, "Path"),
        ({"devices": [{"device": "Path", "adapter": "unknown", "allowed": ["A"]}]},
         "adapter"),
        ({"devices": [{"device": "Path", "adapter": "Route", "allowed": "unknown"}]},
         "allowed"),
    ])
    def test_structured_map_refusals_name_missing_or_unreadable_identity_before_gate(
        self, mock_ctrl, unconstrained_guard, monkeypatch, inventory, field
    ):
        from microclaw import tools
        mock_ctrl._state_device_inventory = inventory
        mock_ctrl.core.get_camera_device.return_value = "Camera"
        mock_ctrl.core.get_device_name.return_value = "CamAdapter"
        monkeypatch.setattr(tools, "CONFIRM_FN",
                            lambda *a, **k: pytest.fail("confirmation must not run"))
        result = tools.save_knowledge(mock_ctrl, unconstrained_guard, "devices", "path", {
            "kind": "optical_path_position_map", "device": "Path", "positions": {"A": "camera"},
        })
        assert field in result["error"]

    def test_structured_map_rejects_position_key_outside_exact_allowed_labels(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw import tools
        mock_ctrl._state_device_inventory = {"devices": [{
            "device": "Path", "adapter": "Route", "allowed": ["State-0"],
        }]}
        mock_ctrl.core.get_camera_device.return_value = "Camera"
        mock_ctrl.core.get_device_name.return_value = "CamAdapter"
        monkeypatch.setattr(tools, "CONFIRM_FN",
                            lambda *a, **k: pytest.fail("confirmation must not run"))
        result = tools.save_knowledge(mock_ctrl, unconstrained_guard, "devices", "path", {
            "kind": "optical_path_position_map", "device": "Path",
            "positions": {"State-9": "camera"},
        })
        assert "State-9" in result["error"] and "allowed-label" in result["error"]

    def test_declines_and_does_not_write(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw import tools
        calls = []
        monkeypatch.setattr(tools, "CONFIRM_FN", lambda summary, kind="action": False)
        monkeypatch.setattr(
            "microclaw.knowledge_manager.save_entry",
            lambda *a, **k: calls.append(a),
        )
        result = tools.save_knowledge(
            mock_ctrl, unconstrained_guard,
            category="devices", key="X",
            value={"description": "y", "observed_on": "DCam"},
        )
        assert "declined" in result["error"].lower()
        assert calls == []  # save_entry never reached


    def test_rank_hook_log_skips_interleaved_runner_actions(
        self, mock_ctrl, unconstrained_guard, tmp_path
    ):
        from microclaw.hook_decisions import ContinueAcquisition, UntrustedHookAdapter
        from microclaw.hooks import HookBase, analysis_observation_record
        metadata = {
            "PositionName": "p0", "XPosition_um_Intended": 1,
            "YPosition_um_Intended": 2,
        }
        adapter = UntrustedHookAdapter(object())
        adapter._record(
            metadata, event="hook_action", action={"kind": ContinueAcquisition().kind},
            decision="accepted",
        )
        observation = {
            **HookBase.where(metadata),
            **analysis_observation_record(
                analyzer="test", analyzer_version="1", result={"snr": 4}
            ),
        }
        path = tmp_path / "hook.json"
        path.write_text(json.dumps([*adapter._log, observation]), encoding="utf-8")
        result = tools.rank_hook_log(mock_ctrl, unconstrained_guard, str(path))
        assert result["entry_count"] == 1
        assert result["ranking"][0]["position"] == "p0"

    def test_position_preflight_names_conflicts_as_pre_existing(
        self, mock_ctrl, unconstrained_guard
    ):
        from microclaw.controller import PositionProjection
        mock_ctrl.inspect_current_position_list.return_value = PositionProjection(
            [], [], [{"code": "duplicate_label", "index": 0, "label": "old"}]
        )
        _, conflict = tools._preflight_native_positions(mock_ctrl, unconstrained_guard)
        detail = conflict["position_list_conflict"]
        assert "pre-existing" in detail["source"]
        assert "not evaluated or written" in detail["hint"]

    def test_zero_calibration_shift_names_stationary_pattern_before_step_size(self):
        reason = tools._diagnose_calibration_shift(
            np.array([0.0, 0.0]), (512, 512), 20.0, None
        )
        assert "stationary feature" in reason
        assert "before changing step_um" in reason

    def test_failed_hooked_marking_rolls_back_only_this_calls_grid(
        self, mock_ctrl, unconstrained_guard, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            tools, "_acquire_positions_with_hook", lambda *args, **kwargs: {"error": "camera"}
        )
        result = run_multiposition_acquisition(
            mock_ctrl, unconstrained_guard, protocol="timelapse",
            save_dir=str(tmp_path), positions=[
                {"name": "new0", "x_um": 1, "y_um": 2},
                {"name": "new1", "x_um": 3, "y_um": 4},
            ], protocol_params={"n_frames": 1, "interval_s": 0},
            mark_positions=True, hook_strategy="snr_observer",
        )
        assert [call.args[0] for call in mock_ctrl.remove_position.call_args_list] == [
            "new1", "new0"
        ]
        assert result["position_list_rollback"]["complete"] is True

    def test_unsafe_hooked_grid_is_checked_before_any_position_is_marked(
        self, mock_ctrl, default_guard, tmp_path
    ):
        with pytest.raises(SafetyViolation):
            run_multiposition_acquisition(
                mock_ctrl, default_guard, protocol="timelapse", save_dir=str(tmp_path),
                positions=[{"name": "unsafe", "x_um": 5000, "y_um": 0}],
                protocol_params={"n_frames": 1, "interval_s": 0},
                mark_positions=True, hook_strategy="snr_observer",
            )
        mock_ctrl.add_position.assert_not_called()

    def test_post_mark_safety_refusal_still_rolls_back(
        self, mock_ctrl, unconstrained_guard, monkeypatch, tmp_path
    ):
        def refuse(*args, **kwargs):
            raise SafetyViolation("dose refused")
        monkeypatch.setattr(tools, "_acquire_positions_with_hook", refuse)
        result = run_multiposition_acquisition(
            mock_ctrl, unconstrained_guard, protocol="timelapse", save_dir=str(tmp_path),
            positions=[{"name": "new", "x_um": 1, "y_um": 2}],
            protocol_params={"n_frames": 1, "interval_s": 0},
            mark_positions=True, hook_strategy="snr_observer",
        )
        mock_ctrl.remove_position.assert_called_once_with("new")
        assert result["position_list_rollback"]["complete"] is True

    def test_saves_after_confirmation(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw import tools
        calls = []
        monkeypatch.setattr(tools, "CONFIRM_FN", lambda summary, kind="action": True)
        monkeypatch.setattr(
            "microclaw.knowledge_manager.save_entry",
            lambda *a, **k: calls.append(a),
        )
        result = tools.save_knowledge(
            mock_ctrl, unconstrained_guard,
            category="devices", key="X",
            value={"description": "y", "observed_on": "DCam"},
        )
        assert "status" in result
        assert calls  # save_entry reached

    def test_a_devices_entry_without_observed_on_is_refused(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        # design/21 F4: an entry that can suppress an alarm must name the
        # hardware it was observed on. Refused before the human gate — there is
        # nothing worth confirming.
        from microclaw import tools
        monkeypatch.setattr(
            tools, "CONFIRM_FN",
            lambda s, kind="action": pytest.fail("the gate ran on a refused entry"),
        )
        result = tools.save_knowledge(
            mock_ctrl, unconstrained_guard,
            category="devices", key="MM_demo_camera", value={"description": "y"},
        )
        assert "observed_on" in result["error"]
        assert "adapter" in result["error"]  # says where to get it

    def test_other_categories_do_not_require_observed_on(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw import tools
        monkeypatch.setattr(tools, "CONFIRM_FN", lambda s, kind="action": True)
        monkeypatch.setattr(
            "microclaw.knowledge_manager.save_entry", lambda *a, **k: None
        )
        result = tools.save_knowledge(
            mock_ctrl, unconstrained_guard,
            category="samples", key="HeLa", value={"description": "y"},
        )
        assert "status" in result

    def test_a_rig_entry_does_not_require_observed_on(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw import tools
        monkeypatch.setattr(tools, "CONFIRM_FN", lambda s, kind="action": True)
        saved = []
        monkeypatch.setattr(
            "microclaw.knowledge_manager.save_entry",
            lambda *args, **kwargs: saved.append(args),
        )
        result = tools.save_knowledge(
            mock_ctrl, unconstrained_guard, category="rig",
            key="illuminated_field", value={"deliberate": True},
        )
        assert "status" in result
        assert saved

    def test_rig_save_reports_topics_still_open(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        from microclaw import tools
        from microclaw.knowledge_manager import RIG_TOPICS
        monkeypatch.setattr(tools, "CONFIRM_FN", lambda s, kind="action": True)
        result = tools.save_knowledge(
            mock_ctrl, unconstrained_guard, category="rig",
            key="illuminated_field", value={"deliberate": True},
        )
        assert result["remaining_rig_profile_topics"] == [
            t for t in RIG_TOPICS if t != "illuminated_field"
        ]

    def test_a_rig_key_that_is_not_a_topic_is_refused_before_the_prompt(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        """The demo gate's Step 5 defect, pinned.

        Asked to store a deliberate crop, the agent invented the key
        `saved_roi`. The save succeeded, closed no topic, and left get_roi
        without the fact it needed — and the agent then recited
        illuminated_field as still open without connecting the two. rig/ is the
        profile, so its keys are the profile's topics.
        """
        from microclaw import tools
        from microclaw.knowledge_manager import RIG_TOPICS
        prompted, saved = [], []
        monkeypatch.setattr(
            tools, "CONFIRM_FN",
            lambda s, kind="action": prompted.append(kind) or True,
        )
        monkeypatch.setattr(
            "microclaw.knowledge_manager.save_entry",
            lambda *args, **kwargs: saved.append(args),
        )
        result = tools.save_knowledge(
            mock_ctrl, unconstrained_guard, category="rig",
            key="saved_roi", value={"x": 113, "y": 165},
        )
        assert "saved_roi" in result["error"]
        assert result["rig_topics"] == list(RIG_TOPICS)
        for topic in RIG_TOPICS:
            assert topic in result["error"]
        assert not saved      # nothing was written
        assert not prompted   # and the operator was not asked about a doomed save

    def test_a_rig_key_saved_before_the_refusal_can_still_be_deleted(
        self, mock_ctrl, unconstrained_guard
    ):
        from microclaw import tools
        from microclaw.knowledge_manager import save_entry
        save_entry("rig", "saved_roi", {"x": 113})
        result = tools.delete_knowledge(
            mock_ctrl, unconstrained_guard, category="rig", key="saved_roi",
        )
        assert "status" in result

    def test_the_gate_receives_the_knowledge_kind(
        self, mock_ctrl, unconstrained_guard, monkeypatch
    ):
        # The frontend branches on `kind`, never on a prose prefix (design/21 F1).
        from microclaw import tools
        seen = {}
        monkeypatch.setattr(
            tools, "CONFIRM_FN",
            lambda s, kind="action": seen.update(kind=kind) or False,
        )
        tools.save_knowledge(
            mock_ctrl, unconstrained_guard,
            category="samples", key="X", value={"description": "y"},
        )
        assert seen["kind"] == "knowledge"


class TestRoiRigKnowledge:
    @pytest.fixture(autouse=True)
    def _knowledge_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "microclaw.knowledge_manager.KNOWLEDGE_PATH", tmp_path / "knowledge.yaml"
        )

    @pytest.mark.parametrize("tool_name", ["get_roi", "set_roi", "clear_roi"])
    def test_roi_results_have_no_illuminated_field_when_none_is_stored(
        self, tool_name, mock_ctrl, unconstrained_guard
    ):
        roi = types.SimpleNamespace(x=1, y=2, width=3, height=4)
        mock_ctrl.core.get_roi.return_value = roi
        mock_ctrl.core.get_camera_device.return_value = "Camera"
        mock_ctrl.core.get_image_width.return_value = 512
        mock_ctrl.core.get_image_height.return_value = 256
        mock_ctrl.studio.live().is_live_mode_on.return_value = False
        if tool_name == "get_roi":
            result = tools.get_roi(mock_ctrl, unconstrained_guard)
            assert result == {"x": 1, "y": 2, "width": 3, "height": 4}
        elif tool_name == "set_roi":
            result = tools.set_roi(mock_ctrl, unconstrained_guard, 1, 2, 3, 4)
        else:
            result = tools.clear_roi(mock_ctrl, unconstrained_guard)
        assert "illuminated_field" not in result

    @pytest.mark.parametrize("tool_name", ["get_roi", "set_roi", "clear_roi"])
    def test_roi_results_include_stored_illuminated_field(
        self, tool_name, mock_ctrl, unconstrained_guard
    ):
        from microclaw.knowledge_manager import save_entry
        field = {"deliberate": True, "note": "only this region is lit"}
        save_entry("rig", "illuminated_field", field)
        mock_ctrl.core.get_roi.return_value = types.SimpleNamespace(
            x=1, y=2, width=3, height=4
        )
        mock_ctrl.core.get_camera_device.return_value = "Camera"
        mock_ctrl.core.get_image_width.return_value = 512
        mock_ctrl.core.get_image_height.return_value = 256
        mock_ctrl.studio.live().is_live_mode_on.return_value = False
        if tool_name == "get_roi":
            result = tools.get_roi(mock_ctrl, unconstrained_guard)
        elif tool_name == "set_roi":
            result = tools.set_roi(mock_ctrl, unconstrained_guard, 1, 2, 3, 4)
        else:
            result = tools.clear_roi(mock_ctrl, unconstrained_guard)
        assert result["illuminated_field"] == field

    def test_get_roi_ignores_a_hand_edited_scalar_rig_profile(
        self, mock_ctrl, unconstrained_guard
    ):
        from microclaw import knowledge_manager
        knowledge_manager.KNOWLEDGE_PATH.write_text(
            "rig: illuminated_field is a deliberate crop\n", encoding="utf-8"
        )
        mock_ctrl.core.get_roi.return_value = types.SimpleNamespace(
            x=1, y=2, width=3, height=4
        )
        assert tools.get_roi(mock_ctrl, unconstrained_guard) == {
            "x": 1, "y": 2, "width": 3, "height": 4,
        }

    def test_set_roi_guards_nonsense_but_not_camera_specific_bounds(
        self, mock_ctrl, unconstrained_guard
    ):
        mock_ctrl.core.get_roi.return_value = types.SimpleNamespace(
            x=10, y=20, width=100, height=80
        )
        mock_ctrl.core.get_camera_device.return_value = "Camera"
        mock_ctrl.studio.live().is_live_mode_on.return_value = False
        tools.set_roi(mock_ctrl, unconstrained_guard, 0, 0, 50, 50)
        mock_ctrl.core.set_roi.assert_called_once_with(0, 0, 50, 50)
        mock_ctrl.core.get_roi.assert_not_called()

        mock_ctrl.core.set_roi.reset_mock()
        for args in ((-1, 0, 10, 10), (0, 0, 0, 10), (True, 0, 10, 10)):
            with pytest.raises(SafetyViolation):
                tools.set_roi(mock_ctrl, unconstrained_guard, *args)
        mock_ctrl.core.set_roi.assert_not_called()

    def test_set_roi_accepts_a_positive_crop_inside_current_camera_geometry(
        self, mock_ctrl, unconstrained_guard
    ):
        mock_ctrl.core.get_roi.return_value = types.SimpleNamespace(
            x=0, y=0, width=512, height=256
        )
        mock_ctrl.core.get_camera_device.return_value = "Camera"
        mock_ctrl.studio.live().is_live_mode_on.return_value = False
        tools.set_roi(mock_ctrl, unconstrained_guard, 10, 20, 100, 80)
        mock_ctrl.core.set_roi.assert_called_once_with(10, 20, 100, 80)

class TestListDeviceProperties:
    def _make_sv(self, items):
        from unittest.mock import MagicMock
        sv = MagicMock()
        sv.size.return_value = len(items)
        sv.get.side_effect = list(items)
        return sv

    def test_returns_property_names(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_device_property_names.return_value = self._make_sv(["State", "Label"])
        result = list_device_properties(mock_ctrl, unconstrained_guard, device="Arduino-Switch")
        assert result["device"] == "Arduino-Switch"
        assert result["properties"] == ["State", "Label"]
        assert result["count"] == 2

    def test_empty_device(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_device_property_names.return_value = self._make_sv([])
        result = list_device_properties(mock_ctrl, unconstrained_guard, device="Dummy")
        assert result["properties"] == []
        assert result["count"] == 0

    def test_calls_core_with_device_name(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_device_property_names.return_value = self._make_sv([])
        list_device_properties(mock_ctrl, unconstrained_guard, device="MyDevice")
        mock_ctrl.core.get_device_property_names.assert_called_once_with("MyDevice")


class TestGetDevicePropertyInfo:
    def _make_sv(self, items):
        from unittest.mock import MagicMock
        sv = MagicMock()
        sv.size.return_value = len(items)
        sv.get.side_effect = list(items)
        return sv

    def _setup_mock(self, mock_ctrl, *, read_only=False, pre_init=False,
                    prop_type="Integer", allowed=(), has_limits=False,
                    lower=None, upper=None, current="0"):
        mock_ctrl.core.is_property_read_only.return_value = read_only
        mock_ctrl.core.is_property_pre_init.return_value = pre_init
        mock_ctrl.core.get_property_type.return_value = prop_type
        mock_ctrl.core.get_allowed_property_values.return_value = self._make_sv(allowed)
        mock_ctrl.core.has_property_limits.return_value = has_limits
        mock_ctrl.core.get_property_lower_limit.return_value = lower
        mock_ctrl.core.get_property_upper_limit.return_value = upper
        mock_ctrl.core.get_property.return_value = current

    def test_integer_with_limits(self, mock_ctrl, unconstrained_guard):
        self._setup_mock(mock_ctrl, prop_type="Integer", has_limits=True, lower=0.0, upper=7.0)
        result = get_device_property_info(
            mock_ctrl, unconstrained_guard, device="Arduino-Switch", property="State"
        )
        assert result["device"] == "Arduino-Switch"
        assert result["property"] == "State"
        assert result["type"] == "Integer"
        assert result["read_only"] is False
        assert result["pre_init"] is False
        assert result["allowed_values"] is None
        assert result["lower_limit"] == 0.0
        assert result["upper_limit"] == 7.0
        assert result["current_value"] == "0"

    def test_enum_property(self, mock_ctrl, unconstrained_guard):
        self._setup_mock(mock_ctrl, prop_type="String", allowed=["On", "Off"], current="On")
        result = get_device_property_info(
            mock_ctrl, unconstrained_guard, device="Shutter", property="State"
        )
        assert result["allowed_values"] == ["On", "Off"]
        assert result["lower_limit"] is None
        assert result["upper_limit"] is None

    def test_read_only_property(self, mock_ctrl, unconstrained_guard):
        self._setup_mock(mock_ctrl, read_only=True, prop_type="String", current="DemoCam")
        result = get_device_property_info(
            mock_ctrl, unconstrained_guard, device="Core", property="Camera"
        )
        assert result["read_only"] is True

    def test_pre_init_property(self, mock_ctrl, unconstrained_guard):
        self._setup_mock(mock_ctrl, pre_init=True, prop_type="String")
        result = get_device_property_info(
            mock_ctrl, unconstrained_guard, device="Camera", property="Port"
        )
        assert result["pre_init"] is True

    def test_float_no_limits(self, mock_ctrl, unconstrained_guard):
        self._setup_mock(mock_ctrl, prop_type="Float", current="1.5")
        result = get_device_property_info(
            mock_ctrl, unconstrained_guard, device="Camera", property="Gain"
        )
        assert result["type"] == "Float"
        assert result["lower_limit"] is None
        assert result["upper_limit"] is None

    def test_no_limits_query_when_has_limits_false(self, mock_ctrl, unconstrained_guard):
        self._setup_mock(mock_ctrl, prop_type="Integer", has_limits=False)
        get_device_property_info(
            mock_ctrl, unconstrained_guard, device="Dev", property="Prop"
        )
        mock_ctrl.core.get_property_lower_limit.assert_not_called()
        mock_ctrl.core.get_property_upper_limit.assert_not_called()

    class _EnumProxy:
        """pyjavaz-shaped enum shadow: to_string()/swig_value(), useless repr."""

        def __init__(self, name, ordinal):
            self._name, self._ordinal = name, ordinal

        def to_string(self):
            return self._name

        def swig_value(self):
            return self._ordinal

        def __repr__(self):
            return "<pyjavaz...mmcorej_PropertyType object at 0x000001B6FFB4DFD0>"

    def test_zmq_proxy_type_resolved_by_name(self, mock_ctrl, unconstrained_guard):
        self._setup_mock(mock_ctrl, prop_type=self._EnumProxy("Float", 2))
        result = get_device_property_info(
            mock_ctrl, unconstrained_guard, device="Camera", property="Gain"
        )
        assert result["type"] == "Float"

    def test_zmq_proxy_type_resolved_by_ordinal(self, mock_ctrl, unconstrained_guard):
        class OrdinalOnly:
            def swig_value(self):
                return 3
        self._setup_mock(mock_ctrl, prop_type=OrdinalOnly())
        result = get_device_property_info(
            mock_ctrl, unconstrained_guard, device="Dev", property="Prop"
        )
        assert result["type"] == "Integer"

    def test_type_never_leaks_a_heap_address(self, mock_ctrl, unconstrained_guard):
        # The design/14 §11 regression: an opaque proxy must yield an enum name
        # or "Unknown", never a sliced repr with a memory address.
        class OpaqueProxy:
            def __repr__(self):
                return "<pyjavaz...mmcorej_PropertyType object at 0x000001B6FFB4DFD0>"
        self._setup_mock(mock_ctrl, prop_type=OpaqueProxy())
        result = get_device_property_info(
            mock_ctrl, unconstrained_guard, device="Dev", property="Prop"
        )
        assert result["type"] in {"Undef", "String", "Float", "Integer", "Unknown"}
        assert "0x" not in result["type"]


class TestLoadSkillDocumentation:
    """The generic loader preserves the deep protocol and hook references."""

    def test_returns_the_packaged_protocol(self, mock_ctrl, unconstrained_guard):
        result = load_skill(mock_ctrl, unconstrained_guard, "dna-paint")
        doc = result["documentation"]
        assert isinstance(doc, str)
        # Read through importlib.resources from package data, so this also
        # proves the .md is reachable the way an installed wheel reaches it.
        assert "# DNA-PAINT Experiment Protocol" in doc

    def test_covers_the_whole_protocol(self, mock_ctrl, unconstrained_guard):
        doc = load_skill(mock_ctrl, unconstrained_guard, "dna-paint")["documentation"]
        for term in (
            # kinetics — the part that lets a parameter be derived, not quoted
            "τ_b = 1/k_off", "k_on", "Imager/docking strand design",
            # buffers and the scavenger
            "Buffer B+", "PCA", "Trolox",
            # imaging
            "Imaging parameters", "kW/cm²", "TIRF",
            # the bench half the user asked to keep
            "Design & fold DNA origami", "Purify", "Immobilization on glass",
            # and the exit to external software
            "Picasso: Localize",
        ):
            assert term in doc, f"protocol lost its {term!r} content"

    def test_is_registered_and_emits_nothing(self):
        assert tools.TOOL_REGISTRY["load_skill"] is load_skill
        # An undecorated tool plants a raise RuntimeError in every exported
        # script that recorded it; a documentation read emits nothing.
        assert load_skill._microclaw_emits_nothing is True

    def test_carries_no_rig_identity(self, mock_ctrl, unconstrained_guard):
        """Rig facts belong in gate docs and rig profiles, never in microclaw/.

        The source document was written for one stand and named it throughout —
        its 2 W laser, its EMCCD model, its focus system. Those became
        capability statements when it moved into the package. Only the cited
        reference instrument survives, inside the citation that carries it.
        """
        doc = load_skill(mock_ctrl, unconstrained_guard, "dna-paint")["documentation"]
        for rig_fact in ("Nikon Ti1", "iXON", "2 W", "MPI"):
            assert rig_fact not in doc, f"rig identity {rig_fact!r} in a packaged doc"


class TestSmlmAndDnaPaintDocsAgree:
    """Two documents cover DNA-PAINT; the summary must not contradict the depth.

    Before this change SMLM_REFERENCE recommended 0.1-1 nM imager in
    PBS + 500 mM NaCl while the protocol says 100 pM-10 nM starting near 5 nM in
    a Mg-based buffer — a 5x disagreement on the most consequential knob in the
    technique, in two tool results the same session can read minutes apart.
    """

    def test_smlm_reference_routes_to_the_deep_protocol(self):
        from microclaw.skills import load_skill_text
        SMLM_REFERENCE = load_skill_text("smlm")
        assert SMLM_REFERENCE.count('load_skill(name="dna-paint")') >= 2

    def test_smlm_reference_no_longer_carries_the_superseded_figures(self):
        from microclaw.skills import load_skill_text
        SMLM_REFERENCE = load_skill_text("smlm")
        for superseded in ("0.1–1 nM", "PBS + 500 mM NaCl"):
            assert superseded not in SMLM_REFERENCE, (
                f"{superseded!r} is the figure the protocol supersedes"
            )

    def test_both_documents_state_the_same_starting_frame_count(self):
        from microclaw.skills import load_skill_text
        SMLM_REFERENCE = load_skill_text("smlm")
        assert "7,500" in SMLM_REFERENCE
        assert "7,500" in load_skill_text("dna-paint")


class TestHookAuthoringSkill:
    def test_returns_nonempty_string(self, mock_ctrl, unconstrained_guard):
        result = load_skill(mock_ctrl, unconstrained_guard, "hook-authoring")
        assert isinstance(result["documentation"], str)
        assert len(result["documentation"]) > 0

    def test_covers_key_concepts(self, mock_ctrl, unconstrained_guard):
        result = load_skill(mock_ctrl, unconstrained_guard, "hook-authoring")
        doc = result["documentation"]
        for term in (
            "image_process_fn", "post_hardware_hook_fn", "HookBase", "event_queue",
            "What existing workflow", "show me one example",
            "What should the microscope do", "NOT a questionnaire",
        ):
            assert term in doc

    def test_capability_table_carries_adaptive_time_series_contract(self):
        from microclaw.skills import load_skill_text

        doc = load_skill_text("hook-authoring")
        flat = " ".join(doc.split())
        assert "adaptive time series:" in doc
        assert "run_timelapse(n_frames=None" in doc
        assert "max_frames=..., hook_strategy=...)" in doc
        assert "spatial survey:" in doc
        assert "fixed-plan hooked acquisitions" in doc
        assert "pinned registry source must reference" in flat
        assert "resolution refuses before acquisition" in flat
        assert "every image must produce exactly one routing decision" in flat
        assert "does not idle until `max_idle_s`" in flat
        assert "run_adaptive_survey" in doc


class TestGetFullDeviceState:
    def _make_sv(self, items):
        from unittest.mock import MagicMock
        sv = MagicMock()
        sv.size.return_value = len(items)
        sv.get.side_effect = list(items)
        return sv

    def test_returns_all_properties(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_device_property_names.return_value = self._make_sv(["Gain", "Binning"])
        mock_ctrl.core.get_property.side_effect = ["1", "1"]
        result = get_full_device_state(mock_ctrl, unconstrained_guard, device="Camera")
        assert result["device"] == "Camera"
        assert result["state"] == {"Gain": "1", "Binning": "1"}

    def test_error_on_one_property_does_not_abort(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_device_property_names.return_value = self._make_sv(["Good", "Bad"])
        mock_ctrl.core.get_property.side_effect = ["ok", RuntimeError("read error")]
        result = get_full_device_state(mock_ctrl, unconstrained_guard, device="Camera")
        assert result["state"]["Good"] == "ok"
        assert "error" in result["state"]["Bad"]

    def test_empty_device_returns_empty_state(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_device_property_names.return_value = self._make_sv([])
        result = get_full_device_state(mock_ctrl, unconstrained_guard, device="Dummy")
        assert result["state"] == {}


# ---------------------------------------------------------------------------
# EMU semantic power, MMStudio Album, and MDA (design/30)
# ---------------------------------------------------------------------------

def _design30_emu_power(monkeypatch):
    props = {
        "Laser 2 power percentage": {
            "mm_property_string": "PWM-Position0",
            "device": "PWM",
            "property": "Position0",
            "slope": "0.3",
            "offset": "0.0",
        }
    }
    monkeypatch.setattr(tools, "_cached_emu_properties", lambda ctrl: (props, {}))


def test_emu_calibration_uses_slope_in_documented_direction(
    mock_ctrl, unconstrained_guard, monkeypatch
):
    _design30_emu_power(monkeypatch)
    result = tools.verify_emu_laser_power_calibration(
        mock_ctrl, unconstrained_guard, 2,
        [{"percent": 1, "raw_value": 0}, {"percent": 10, "raw_value": 3}],
    )
    assert result["verified"] is True


def test_emu_write_reports_unrepresentable_one_percent(
    mock_ctrl, unconstrained_guard, monkeypatch
):
    _design30_emu_power(monkeypatch)
    tools.verify_emu_laser_power_calibration(
        mock_ctrl, unconstrained_guard, 2,
        [{"percent": 1, "raw_value": 0}, {"percent": 10, "raw_value": 3}],
    )
    monkeypatch.setattr(tools, "get_device_property_info", lambda *args, **kwargs: {
        "read_only": False, "type": "Integer", "lower_limit": 0, "upper_limit": 100,
    })
    mock_ctrl.core.get_property.return_value = "0"
    result = tools.set_emu_laser_power_percentage(mock_ctrl, unconstrained_guard, 2, 1)
    mock_ctrl.core.set_property.assert_called_once_with("PWM", "Position0", "0")
    mock_ctrl.refresh_gui.assert_called_once_with()
    assert result["effective_percent"] == 0
    assert result["representable"] is False
    assert result["min_nonzero_percent"] == pytest.approx(10 / 3)
    assert result["device"] == "PWM"
    assert result["property"] == "Position0"


def test_emu_write_refuses_unverified_calibration(
    mock_ctrl, unconstrained_guard, monkeypatch
):
    _design30_emu_power(monkeypatch)
    result = tools.set_emu_laser_power_percentage(mock_ctrl, unconstrained_guard, 2, 1)
    assert "unverified" in result["error"].lower()
    mock_ctrl.core.set_property.assert_not_called()


def test_emu_power_raw_write_refuses_when_device_is_bounded_stage(
    mock_ctrl, unconstrained_guard, monkeypatch
):
    from microclaw.authorization import AuthorizationMap, RigAuthorizationError
    _design30_emu_power(monkeypatch)
    tools.verify_emu_laser_power_calibration(
        mock_ctrl, unconstrained_guard, 2,
        [{"percent": 1, "raw_value": 0}, {"percent": 10, "raw_value": 3}],
    )
    monkeypatch.setattr(tools, "get_device_property_info", lambda *args, **kwargs: {
        "read_only": False, "type": "Integer", "lower_limit": 0, "upper_limit": 100,
    })
    mock_ctrl.authorization_map = AuthorizationMap(
        "degraded_trusted_plugins", "degraded", None,
        property_writes_unrestricted=True,
        bounded_stage_devices=frozenset({"PWM"}),
    )
    with pytest.raises(RigAuthorizationError, match="move_stage"):
        tools.set_emu_laser_power_percentage(
            mock_ctrl, unconstrained_guard, 2, 1
        )
    mock_ctrl.core.set_property.assert_not_called()


def test_snap_to_album_uses_proven_java_collection_path(
    mock_ctrl, unconstrained_guard
):
    images = MagicMock(name="java_array_list")
    manager = mock_ctrl.studio.acquisitions()
    album = mock_ctrl.studio.album()
    manager.snap.return_value = images
    album.add_images.return_value = True
    album.get_datastore.return_value = None
    mock_ctrl.core.get_exposure.return_value = 10
    result = tools.snap_to_album(mock_ctrl, unconstrained_guard)
    album.add_images.assert_called_once_with(images)
    assert result["created_new_album"] is True
    assert result["album_exists"] is False


def test_read_mda_settings_reads_arraylist_slices_and_channel_methods():
    """Pin the measured bridge accessors (design/32 Block 4 G5 probe).

    slices()/channels() return a java.util.ArrayList that is NOT iterable over
    the bridge — read by size()/get(i). On ChannelSpec, useChannel is a field
    but exposure is a METHOD. A regression to `for v in list` or
    `float(spec.exposure)` makes these fields '<unreadable: TypeError>' again.
    """
    class FakeArrayList:
        def __init__(self, items):
            self._items = items
        def size(self):
            return len(self._items)
        def get(self, i):
            return self._items[i]
        def __iter__(self):
            raise TypeError("bridge ArrayList is not directly iterable")

    class FakeChannelSpec:
        def __init__(self, use_channel, exposure_ms):
            self.useChannel = use_channel      # public field
            self._exposure = exposure_ms
        def exposure(self):                    # method, not a field
            return self._exposure

    settings = MagicMock()
    for name in tools._MDA_SCALARS:
        getattr(settings, name).return_value = None
    settings.use_slices.return_value = True
    settings.use_channels.return_value = True
    settings.slices.return_value = FakeArrayList([1, 0.8, -1.0])
    settings.channels.return_value = FakeArrayList(
        [FakeChannelSpec(True, 10), FakeChannelSpec(False, 50)]
    )

    out = tools._read_mda_settings(settings)

    assert out["slices"] == [1.0, 0.8, -1.0]
    assert out["channels"] == [
        {"use_channel": True, "exposure_ms": 10.0},
        {"use_channel": False, "exposure_ms": 50.0},
    ]


def test_mda_requires_unchanged_preview_and_confirmation(
    mock_ctrl, unconstrained_guard, monkeypatch
):
    settings = MagicMock()
    for name in tools._MDA_SCALARS:
        getattr(settings, name).return_value = False if name.startswith("use_") else None
    settings.save.return_value = False
    manager = mock_ctrl.studio.acquisitions()
    manager.get_acquisition_settings.return_value = settings
    store = MagicMock()
    store.get_num_images.return_value = 1
    manager.run_acquisition.return_value = store
    preview = tools.get_mda_settings(mock_ctrl, unconstrained_guard)
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda *args, **kwargs: True)
    result = tools.run_mda(mock_ctrl, unconstrained_guard, preview["preview_token"])
    manager.run_acquisition.assert_called_once_with()
    assert result["datastore"]["image_count"] == 1


def test_mda_refuses_a_stale_token_when_settings_changed(
    mock_ctrl, unconstrained_guard, monkeypatch
):
    """Pin the staleness refusal — unobservable on M5, where the authorization
    map excludes the mmstudio-mda path and refuses before this check (design/32
    Block 4 G5). The live token proved sensitive to slice/channel changes; this
    guards the run-side consumption of that sensitivity."""
    settings = MagicMock()
    for name in tools._MDA_SCALARS:
        getattr(settings, name).return_value = False if name.startswith("use_") else None
    settings.save.return_value = False
    settings.num_frames.return_value = 3
    manager = mock_ctrl.studio.acquisitions()
    manager.get_acquisition_settings.return_value = settings
    preview = tools.get_mda_settings(mock_ctrl, unconstrained_guard)
    # Operator changes a setting in the GUI after previewing.
    settings.num_frames.return_value = 5
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda *args, **kwargs: True)
    result = tools.run_mda(mock_ctrl, unconstrained_guard, preview["preview_token"])
    assert "changed" in result.get("error", "").lower()
    manager.run_acquisition.assert_not_called()
