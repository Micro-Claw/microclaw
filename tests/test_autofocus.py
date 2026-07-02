import numpy as np
import pytest
from unittest.mock import MagicMock

from microclaw.autofocus import sweep_autofocus, coarse_then_fine_autofocus


def make_ctrl_with_focus_at(best_z: float, width: int = 64):
    """Mock controller whose images are sharpest at best_z."""
    core = MagicMock()
    core.get_focus_device.return_value = "DStage"
    current_z = [50.0]
    core.set_position.side_effect = lambda z: current_z.__setitem__(0, z)
    core.get_position.side_effect = lambda: current_z[0]

    def get_tagged_image():
        z = current_z[0]
        sharpness = np.exp(-((z - best_z) ** 2) / (2 * 3.0 ** 2))
        pixels = (np.random.rand(width, width) * sharpness * 65535).clip(0, 65535).astype(np.uint16)
        tagged = MagicMock()
        tagged.pix = pixels.tobytes()
        tagged.tags = {"Width": width, "Height": width}
        return tagged

    core.get_tagged_image.side_effect = get_tagged_image
    core.get_bytes_per_pixel.return_value = 2      # 16-bit mono simulated frames
    core.get_number_of_components.return_value = 1
    core.snap_image = MagicMock()
    core.wait_for_image_synced = MagicMock()
    core.wait_for_device = MagicMock()
    ctrl = MagicMock()
    ctrl.core = core
    return ctrl


def test_sweep_finds_correct_z():
    ctrl = make_ctrl_with_focus_at(52.0)
    result = sweep_autofocus(ctrl, 45.0, 55.0, 1.0, settle_ms=0)
    assert abs(result.best_z_um - 52.0) <= 1.0


def test_sweep_settled_when_peak_interior():
    ctrl = make_ctrl_with_focus_at(50.0)
    result = sweep_autofocus(ctrl, 45.0, 55.0, 1.0, settle_ms=0)
    assert result.settled


def test_sweep_not_settled_when_peak_at_boundary():
    # best_z is below the sweep start → metric is highest at first step
    ctrl = make_ctrl_with_focus_at(44.0)
    result = sweep_autofocus(ctrl, 45.0, 55.0, 1.0, settle_ms=0)
    assert not result.settled


def test_sweep_returns_correct_structure():
    ctrl = make_ctrl_with_focus_at(50.0)
    result = sweep_autofocus(ctrl, 48.0, 52.0, 1.0, settle_ms=0)
    assert len(result.z_positions) == len(result.metric_values)
    assert result.best_z_um in result.z_positions


def test_coarse_then_fine_returns_result():
    ctrl = make_ctrl_with_focus_at(50.0)
    result = coarse_then_fine_autofocus(ctrl, z_range_um=10.0, coarse_step_um=2.0,
                                        fine_step_um=0.5, settle_ms=0)
    assert isinstance(result.best_z_um, float)
    assert isinstance(result.settled, bool)
