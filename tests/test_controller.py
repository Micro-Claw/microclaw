"""Controller-level unit tests (no live Micro-Manager).

MicroscopeController.__init__ opens ZMQ connections, so these construct the
object without __init__ and inject mock Core/Studio, then exercise the pure
position-store / guarded-seam logic.
"""
from unittest.mock import MagicMock

import pytest

from microclaw.controller import MicroscopeController
from microclaw.safety import (
    SafetyConstraints,
    SafetyGuard,
    SafetyViolation,
    StageConstraints,
)


def make_controller(guard=None):
    ctrl = MicroscopeController.__new__(MicroscopeController)
    ctrl._port = 4827
    ctrl._core = MagicMock()
    ctrl._core.get_xy_stage_device.return_value = "DXYStage"
    ctrl._core.get_focus_device.return_value = "DStage"
    ctrl._studio = MagicMock()
    ctrl._plugins = None
    ctrl._guard = guard
    ctrl._positions = []
    return ctrl


def bounded_guard():
    return SafetyGuard(
        SafetyConstraints(stage=StageConstraints(z_min=0, z_max=200,
                                                 x_min=-500, x_max=500,
                                                 y_min=-500, y_max=500))
    )


class TestGuardedSeam:
    def test_set_z_guarded(self):
        ctrl = make_controller(guard=bounded_guard())
        with pytest.raises(SafetyViolation):
            ctrl.set_z(999.0)
        ctrl._core.set_position.assert_not_called()

    def test_set_z_in_range_moves(self):
        ctrl = make_controller(guard=bounded_guard())
        ctrl.set_z(100.0)
        ctrl._core.set_position.assert_called_once_with(100.0)

    def test_set_xy_guarded(self):
        ctrl = make_controller(guard=bounded_guard())
        with pytest.raises(SafetyViolation):
            ctrl.set_xy(9999.0, 0.0)
        ctrl._core.set_xy_position.assert_not_called()

    def test_no_guard_does_not_block(self):
        ctrl = make_controller(guard=None)
        ctrl.set_z(999.0)  # no guard → no check
        ctrl._core.set_position.assert_called_once_with(999.0)


class TestGoToPositionZOnly:
    def test_z_only_skips_xy(self):
        ctrl = make_controller()
        ctrl._positions = [{"name": "Zonly", "z_um": 42.0}]
        ctrl.go_to_position("Zonly")
        ctrl._core.set_xy_position.assert_not_called()
        ctrl._core.set_position.assert_called_once_with(42.0)

    def test_xy_and_z_both_set(self):
        ctrl = make_controller()
        ctrl._positions = [{"name": "P", "x_um": 1.0, "y_um": 2.0, "z_um": 3.0}]
        ctrl.go_to_position("P")
        ctrl._core.set_xy_position.assert_called_once_with(1.0, 2.0)
        ctrl._core.set_position.assert_called_once_with(3.0)


class TestLoadPositionListFileValidation:
    def test_malformed_file_raises(self, tmp_path):
        ctrl = make_controller()
        ctrl._positions = [{"name": "keep", "x_um": 0.0, "y_um": 0.0}]
        f = tmp_path / "bad.json"
        f.write_text('[{"nope": 1}]')  # no name, no coords
        with pytest.raises(ValueError, match="not a valid"):
            ctrl.load_position_list(str(f))
        # store unchanged
        assert ctrl._positions == [{"name": "keep", "x_um": 0.0, "y_um": 0.0}]

    def test_non_list_raises(self, tmp_path):
        ctrl = make_controller()
        f = tmp_path / "bad.json"
        f.write_text('{"name": "x", "x_um": 0, "y_um": 0}')
        with pytest.raises(ValueError):
            ctrl.load_position_list(str(f))

    def test_z_only_entry_valid(self, tmp_path):
        ctrl = make_controller()
        f = tmp_path / "zonly.json"
        f.write_text('[{"name": "Z", "z_um": 12.0}]')
        ctrl.load_position_list(str(f))
        assert ctrl._positions == [{"name": "Z", "z_um": 12.0}]

    def test_save_load_roundtrip(self, tmp_path):
        ctrl = make_controller()
        ctrl._positions = [
            {"name": "A", "x_um": 1.0, "y_um": 2.0, "z_um": 3.0},
            {"name": "B", "z_um": 9.0},
        ]
        f = tmp_path / "pos.json"
        ctrl.save_position_list(str(f))
        ctrl2 = make_controller()
        ctrl2.load_position_list(str(f))
        assert ctrl2._positions == ctrl._positions
