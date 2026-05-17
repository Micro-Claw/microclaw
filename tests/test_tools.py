import pytest
from microclaw.tools import (
    set_exposure, get_exposure,
    move_stage_z, get_z_position,
    move_stage_xy, get_xy_position,
    set_channel, get_available_channels,
    set_device_property, get_device_property,
    list_devices, get_system_state,
    snap_image, start_live_view, stop_live_view,
)
from microclaw.safety import SafetyViolation


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
