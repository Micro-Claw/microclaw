import json
from unittest.mock import MagicMock, call

import numpy as np
import pytest

from microclaw.autofocus import AutofocusResult
from microclaw.safety import SafetyConstraints, SafetyGuard, SafetyViolation, StageConstraints
from microclaw.tools import (
    clear_position_list,
    delete_position,
    generate_and_save_hook,
    get_available_channels,
    get_device_property,
    get_device_property_info,
    get_exposure,
    get_full_device_state,
    get_hook_documentation,
    get_pixel_size,
    get_position_list,
    get_system_state,
    get_xy_position,
    get_z_position,
    go_to_position,
    list_device_properties,
    list_devices,
    list_hooks,
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
    snap_image,
    start_live_view,
    stop_live_view,
)


class TestSnapImage:
    def test_calls_snap(self, mock_ctrl, unconstrained_guard):
        result = snap_image(mock_ctrl, unconstrained_guard)
        mock_ctrl.studio.live().snap.assert_called_once_with(True)
        assert "status" in result

    def test_start_live_view(self, mock_ctrl, unconstrained_guard):
        result = start_live_view(mock_ctrl, unconstrained_guard)
        mock_ctrl.studio.live().set_live_mode_on.assert_called_with(True)
        assert "status" in result

    def test_stop_live_view(self, mock_ctrl, unconstrained_guard):
        result = stop_live_view(mock_ctrl, unconstrained_guard)
        mock_ctrl.studio.live().set_live_mode_on.assert_called_with(False)
        assert "status" in result


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
    def test_absolute_in_range(self, mock_ctrl, default_guard):
        result = move_stage_z(mock_ctrl, default_guard, z_um=100.0, absolute=True)
        mock_ctrl.core.set_position.assert_called_once_with(100.0)
        assert result["z_um"] == 100.0

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
        result = move_stage_z(mock_ctrl, default_guard, z_um=10.0, absolute=False)
        mock_ctrl.core.set_relative_position.assert_called_once_with(10.0)
        assert result["z_um"] == 60.0  # 50.0 (fixture) + 10.0

    def test_get_z_position(self, mock_ctrl, unconstrained_guard):
        result = get_z_position(mock_ctrl, unconstrained_guard)
        assert result["z_um"] == 50.0


class TestMoveStageXY:
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


class TestSetChannel:
    def test_allowed_channel(self, mock_ctrl, default_guard):
        set_channel(mock_ctrl, default_guard, preset="DAPI")
        mock_ctrl.core.set_config.assert_called_once_with("Channel", "DAPI")

    def test_forbidden_channel(self, mock_ctrl, default_guard):
        with pytest.raises(SafetyViolation, match="allowed list"):
            set_channel(mock_ctrl, default_guard, preset="GFP")

    def test_any_channel_when_unconstrained(self, mock_ctrl, unconstrained_guard):
        set_channel(mock_ctrl, unconstrained_guard, preset="GFP")  # no error

    def test_get_available_channels(self, mock_ctrl, unconstrained_guard):
        result = get_available_channels(mock_ctrl, unconstrained_guard)
        assert "DAPI" in result["channels"]


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
        monkeypatch.setattr("microclaw.tools.CONFIRM_FN", lambda s: False)
        with pytest.raises(SafetyViolation, match="declined"):
            set_device_property(mock_ctrl, self._laser_guard(),
                                device="Luxx638", property="Laser Operation Select",
                                value="On")
        mock_ctrl.core.set_property.assert_not_called()

    def test_illumination_enable_passes_when_confirmed(self, mock_ctrl, monkeypatch):
        monkeypatch.setattr("microclaw.tools.CONFIRM_FN", lambda s: True)
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
    def test_returns_state(self, mock_ctrl, unconstrained_guard):
        result = get_system_state(mock_ctrl, unconstrained_guard)
        assert "x_um" in result
        assert "y_um" in result
        assert "z_um" in result
        assert "exposure_ms" in result

    def test_handles_unavailable_stage(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_x_position.side_effect = Exception("Device not found")
        result = get_system_state(mock_ctrl, unconstrained_guard)
        assert result.get("xy_stage") == "unavailable"


class TestSnapAndAnalyze:
    def test_returns_dict_by_default(self, mock_ctrl, unconstrained_guard, monkeypatch):
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy",
            lambda ctrl: np.zeros((64, 64), dtype=np.uint16),
        )
        mock_ctrl.core.get_position.return_value = 50.0
        result = snap_and_analyze(mock_ctrl, unconstrained_guard)
        assert isinstance(result, dict)
        assert "focus_metric" in result
        assert "mean_intensity" in result
        assert "z_um" in result

    def test_returns_multimodal_when_requested(self, mock_ctrl, unconstrained_guard, monkeypatch):
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy",
            lambda ctrl: np.zeros((64, 64), dtype=np.uint16),
        )
        mock_ctrl.core.get_position.return_value = 50.0
        result = snap_and_analyze(mock_ctrl, unconstrained_guard, return_thumbnail=True)
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "image"
        payload = json.loads(result[0]["text"])
        assert "focus_metric" in payload
        assert "mean_intensity" in payload
        assert "z_um" in payload


_FAKE_AF_RESULT = AutofocusResult(
    best_z_um=50.0,
    metric_values=[0.1, 0.9, 0.1],
    z_positions=[49.0, 50.0, 51.0],
    settled=True,
)


def _patch_autofocus(monkeypatch):
    """Stub out the sweep functions and image helpers used by run_autofocus."""
    monkeypatch.setattr("microclaw.tools.coarse_then_fine_autofocus", lambda *a, **k: _FAKE_AF_RESULT)
    monkeypatch.setattr("microclaw.tools.sweep_autofocus", lambda *a, **k: _FAKE_AF_RESULT)
    monkeypatch.setattr("microclaw.tools.snap_to_numpy", lambda ctrl: np.zeros((64, 64), dtype=np.uint16))
    monkeypatch.setattr("microclaw.tools.make_thumbnail", lambda img: "")


class TestRunAutofocus:
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

    def test_live_stopped_and_restored_when_on(self, mock_ctrl, unconstrained_guard, monkeypatch):
        _patch_autofocus(monkeypatch)
        mock_ctrl.studio.live().is_live_mode_on.return_value = True
        live = mock_ctrl.studio.live()
        live.set_live_mode_on.reset_mock()

        run_autofocus(mock_ctrl, unconstrained_guard, z_range_um=10.0, z_step_um=1.0)

        calls = live.set_live_mode_on.call_args_list
        assert calls[0] == call(False), "live mode must be stopped before sweep"
        assert calls[1] == call(True), "live mode must be restored after sweep"

    def test_live_not_touched_when_off(self, mock_ctrl, unconstrained_guard, monkeypatch):
        _patch_autofocus(monkeypatch)
        mock_ctrl.studio.live().is_live_mode_on.return_value = False
        live = mock_ctrl.studio.live()
        live.set_live_mode_on.reset_mock()

        run_autofocus(mock_ctrl, unconstrained_guard, z_range_um=10.0, z_step_um=1.0)

        live.set_live_mode_on.assert_not_called()

    def test_live_restored_on_sweep_exception(self, mock_ctrl, unconstrained_guard, monkeypatch):
        monkeypatch.setattr(
            "microclaw.tools.coarse_then_fine_autofocus",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("hardware fault")),
        )
        mock_ctrl.studio.live().is_live_mode_on.return_value = True
        live = mock_ctrl.studio.live()
        live.set_live_mode_on.reset_mock()

        with pytest.raises(RuntimeError):
            run_autofocus(mock_ctrl, unconstrained_guard, z_range_um=10.0, z_step_um=1.0)

        live.set_live_mode_on.assert_called_with(True)


class TestRunMultipositionWithAutofocus:
    @pytest.fixture
    def positions(self):
        return [{"name": "P1", "x_um": 0.0, "y_um": 0.0, "z_um": 50.0}]

    @pytest.fixture
    def patched_ctrl(self, mock_ctrl, positions, tmp_path):
        mock_ctrl.get_positions.return_value = positions
        mock_ctrl.core.get_position.return_value = 50.0
        return mock_ctrl

    def _run(self, ctrl, guard, tmp_path, monkeypatch):
        monkeypatch.setattr("microclaw.tools.coarse_then_fine_autofocus", lambda *a, **k: _FAKE_AF_RESULT)
        monkeypatch.setattr("microclaw.tools.run_timelapse", lambda *a, **k: {"status": "ok"})
        return run_multiposition_with_autofocus(
            ctrl, guard,
            position_names=["P1"],
            z_range_um=10.0,
            z_step_um=1.0,
            protocol="timelapse",
            save_dir=str(tmp_path),
            protocol_params={"n_frames": 1, "interval_ms": 0},
        )

    def test_live_stopped_and_restored_when_on(self, patched_ctrl, unconstrained_guard, tmp_path, monkeypatch):
        patched_ctrl.studio.live().is_live_mode_on.return_value = True
        live = patched_ctrl.studio.live()
        live.set_live_mode_on.reset_mock()

        self._run(patched_ctrl, unconstrained_guard, tmp_path, monkeypatch)

        calls = live.set_live_mode_on.call_args_list
        assert calls[0] == call(False), "live mode must be stopped before loop"
        assert calls[-1] == call(True), "live mode must be restored after loop"

    def test_live_not_touched_when_off(self, patched_ctrl, unconstrained_guard, tmp_path, monkeypatch):
        patched_ctrl.studio.live().is_live_mode_on.return_value = False
        live = patched_ctrl.studio.live()
        live.set_live_mode_on.reset_mock()

        self._run(patched_ctrl, unconstrained_guard, tmp_path, monkeypatch)

        live.set_live_mode_on.assert_not_called()

    def test_live_restored_when_autofocus_raises(self, patched_ctrl, unconstrained_guard, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "microclaw.tools.coarse_then_fine_autofocus",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stage error")),
        )
        patched_ctrl.studio.live().is_live_mode_on.return_value = True
        live = patched_ctrl.studio.live()
        live.set_live_mode_on.reset_mock()

        with pytest.raises(RuntimeError):
            run_multiposition_with_autofocus(
                patched_ctrl, unconstrained_guard,
                position_names=["P1"],
                z_range_um=10.0,
                z_step_um=1.0,
                protocol="timelapse",
                save_dir=str(tmp_path),
            )

        live.set_live_mode_on.assert_called_with(True)

    def test_stored_z_out_of_bounds_refused(self, mock_ctrl, default_guard, tmp_path, monkeypatch):
        # default_guard z_max=200; a stored Z beyond it must not move the stage.
        mock_ctrl.get_positions.return_value = [
            {"name": "P1", "x_um": 0.0, "y_um": 0.0, "z_um": 999.0}
        ]
        monkeypatch.setattr("microclaw.tools.coarse_then_fine_autofocus", lambda *a, **k: _FAKE_AF_RESULT)
        result = run_multiposition_with_autofocus(
            mock_ctrl, default_guard,
            position_names=["P1"],
            z_range_um=10.0, z_step_um=1.0,
            protocol="timelapse", save_dir=str(tmp_path),
        )
        assert "out of bounds" in result["results"][0]["error"]
        mock_ctrl.go_to_position.assert_not_called()


class TestTileAcquisitionMarkPositions:
    @pytest.fixture
    def centered_ctrl(self, mock_ctrl):
        mock_ctrl.core.get_x_position.return_value = 256.0
        mock_ctrl.core.get_y_position.return_value = 256.0
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


class TestExportDatasetAllAxes:
    def test_iterates_full_axis_product(self, mock_ctrl, unconstrained_guard, monkeypatch, tmp_path):
        from microclaw import tools

        class FakeDataset:
            axes = {"z": [0, 1, 2], "channel": [0, 1]}

            def __init__(self, path):
                pass

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


class TestMarkPosition:
    def test_saves_position(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_x_position.return_value = 100.0
        mock_ctrl.core.get_y_position.return_value = 200.0
        mock_ctrl.core.get_position.return_value = 50.0
        result = mark_position(mock_ctrl, unconstrained_guard, name="test_pos")
        mock_ctrl.add_position.assert_called_once_with("test_pos", 100.0, 200.0, 50.0)
        assert result["x_um"] == 100.0
        assert result["y_um"] == 200.0
        assert result["z_um"] == 50.0

    def test_xy_safety_check(self, mock_ctrl):
        guard = SafetyGuard(SafetyConstraints(stage=StageConstraints(x_max=100.0)))
        mock_ctrl.core.get_x_position.return_value = 200.0
        mock_ctrl.core.get_y_position.return_value = 0.0
        mock_ctrl.core.get_position.return_value = 50.0
        with pytest.raises(SafetyViolation):
            mark_position(mock_ctrl, guard, name="out_of_bounds")

    def test_without_z(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.get_x_position.return_value = 10.0
        mock_ctrl.core.get_y_position.return_value = 20.0
        result = mark_position(mock_ctrl, unconstrained_guard, name="no_z", include_z=False)
        assert "z_um" not in result
        mock_ctrl.add_position.assert_called_once_with("no_z", 10.0, 20.0, None)


class TestLoadPositionListValidation:
    def test_out_of_bounds_entry_rejected(self, mock_ctrl, default_guard):
        from microclaw.tools import load_position_list
        stored = [
            {"name": "Good", "x_um": 0.0, "y_um": 0.0, "z_um": 50.0},
            {"name": "BadZ", "x_um": 0.0, "y_um": 0.0, "z_um": 999.0},
        ]
        mock_ctrl.get_positions.side_effect = lambda: [
            p for p in stored if p["name"] not in removed
        ]
        removed: set = set()
        mock_ctrl.remove_position.side_effect = lambda name: removed.add(name)

        result = load_position_list(mock_ctrl, default_guard, path="x.json")
        assert [r["name"] for r in result["rejected"]] == ["BadZ"]
        mock_ctrl.remove_position.assert_called_once_with("BadZ")
        assert result["count"] == 1

    def test_z_only_entry_survives(self, mock_ctrl, default_guard):
        from microclaw.tools import load_position_list
        mock_ctrl.get_positions.return_value = [{"name": "Zonly", "z_um": 50.0}]
        result = load_position_list(mock_ctrl, default_guard, path="x.json")
        assert result["rejected"] == []
        mock_ctrl.remove_position.assert_not_called()


class TestGetPositionList:
    def test_returns_positions(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.get_positions.return_value = [
            {"name": "Pos1", "x_um": 0.0, "y_um": 0.0}
        ]
        result = get_position_list(mock_ctrl, unconstrained_guard)
        assert result["count"] == 1
        assert result["positions"][0]["name"] == "Pos1"


class TestGoToPosition:
    def test_moves_to_existing(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.get_positions.return_value = [
            {"name": "Pos1", "x_um": 100.0, "y_um": 200.0, "z_um": 50.0}
        ]
        result = go_to_position(mock_ctrl, unconstrained_guard, name="Pos1")
        mock_ctrl.go_to_position.assert_called_once_with("Pos1")
        assert result["status"] == "Moved to 'Pos1'."

    def test_error_for_missing(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.get_positions.return_value = []
        result = go_to_position(mock_ctrl, unconstrained_guard, name="Ghost")
        assert "error" in result

    def test_safety_check_xy(self, mock_ctrl):
        guard = SafetyGuard(SafetyConstraints(stage=StageConstraints(x_max=50.0)))
        mock_ctrl.get_positions.return_value = [
            {"name": "Far", "x_um": 200.0, "y_um": 0.0}
        ]
        with pytest.raises(SafetyViolation):
            go_to_position(mock_ctrl, guard, name="Far")


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

    def test_declines_when_lint_flags_and_user_says_no(self, mock_ctrl, unconstrained_guard, monkeypatch):
        monkeypatch.setattr("microclaw.tools.CONFIRM_FN", lambda summary: False)
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
        monkeypatch.setattr("microclaw.tools.CONFIRM_FN", lambda summary: True)
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
        hook_file.write_text(code)
        result = read_hook_from_file(mock_ctrl, unconstrained_guard, path=str(hook_file))
        assert result["code"] == code
        assert result["warnings"] == []

    def test_error_for_missing_file(self, mock_ctrl, unconstrained_guard):
        result = read_hook_from_file(mock_ctrl, unconstrained_guard, path="/no/such/file.py")
        assert "error" in result


class TestSaveKnowledgeConfirmation:
    def test_declines_and_does_not_write(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw import tools
        calls = []
        monkeypatch.setattr(tools, "CONFIRM_FN", lambda summary: False)
        monkeypatch.setattr(
            "microclaw.knowledge_manager.save_entry",
            lambda *a, **k: calls.append(a),
        )
        result = tools.save_knowledge(
            mock_ctrl, unconstrained_guard,
            category="devices", key="X", value={"description": "y"},
        )
        assert "declined" in result["error"].lower()
        assert calls == []  # save_entry never reached

    def test_saves_after_confirmation(self, mock_ctrl, unconstrained_guard, monkeypatch):
        from microclaw import tools
        calls = []
        monkeypatch.setattr(tools, "CONFIRM_FN", lambda summary: True)
        monkeypatch.setattr(
            "microclaw.knowledge_manager.save_entry",
            lambda *a, **k: calls.append(a),
        )
        result = tools.save_knowledge(
            mock_ctrl, unconstrained_guard,
            category="devices", key="X", value={"description": "y"},
        )
        assert "status" in result
        assert calls  # save_entry reached


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


class TestGetHookDocumentation:
    def test_returns_nonempty_string(self, mock_ctrl, unconstrained_guard):
        result = get_hook_documentation(mock_ctrl, unconstrained_guard)
        assert isinstance(result["documentation"], str)
        assert len(result["documentation"]) > 0

    def test_covers_key_concepts(self, mock_ctrl, unconstrained_guard):
        result = get_hook_documentation(mock_ctrl, unconstrained_guard)
        doc = result["documentation"]
        for term in ("image_process_fn", "post_hardware_hook_fn", "HookBase", "event_queue"):
            assert term in doc


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
