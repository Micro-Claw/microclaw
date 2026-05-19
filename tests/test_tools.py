import json

import numpy as np
import pytest

from microclaw.safety import SafetyConstraints, SafetyGuard, SafetyViolation, StageConstraints
from microclaw.tools import (
    clear_position_list,
    delete_position,
    generate_and_save_hook,
    get_available_channels,
    get_device_property,
    get_exposure,
    get_position_list,
    get_system_state,
    get_xy_position,
    get_z_position,
    go_to_position,
    list_devices,
    list_hooks,
    mark_position,
    move_stage_xy,
    move_stage_z,
    read_hook_from_file,
    run_autofocus,
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
    def test_returns_multimodal(self, mock_ctrl, unconstrained_guard, monkeypatch):
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy",
            lambda ctrl: np.zeros((64, 64), dtype=np.uint16),
        )
        mock_ctrl.core.get_position.return_value = 50.0
        result = snap_and_analyze(mock_ctrl, unconstrained_guard)
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "image"

    def test_text_block_has_stats(self, mock_ctrl, unconstrained_guard, monkeypatch):
        monkeypatch.setattr(
            "microclaw.tools.snap_to_numpy",
            lambda ctrl: np.zeros((64, 64), dtype=np.uint16),
        )
        mock_ctrl.core.get_position.return_value = 50.0
        result = snap_and_analyze(mock_ctrl, unconstrained_guard)
        payload = json.loads(result[0]["text"])
        assert "focus_metric" in payload
        assert "mean_intensity" in payload
        assert "z_um" in payload


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

    def test_rejects_unsafe_code(self, mock_ctrl, unconstrained_guard):
        code = "eval('os.system(\"rm -rf /\")')"
        result = generate_and_save_hook(
            mock_ctrl, unconstrained_guard,
            name="bad_hook", code=code,
            description="Evil hook", source="claude_generated",
        )
        assert "error" in result
        assert "warnings" in result


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
