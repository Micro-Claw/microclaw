import numpy as np
import pytest
from unittest.mock import MagicMock

from microclaw.autofocus import (
    MIN_CONTRAST,
    curve_contrast,
    coarse_then_fine_autofocus,
    single_sweep_autofocus,
    sweep_autofocus,
)


def make_ctrl_with_focus_at(best_z: float, width: int = 64, flat: bool = False):
    """Mock controller whose images are sharpest at best_z.

    Defocus is simulated as gaussian BLUR of a fixed scene (photons get
    redistributed), not as an amplitude change — the normalized focus metric
    is deliberately insensitive to brightness (design/14 §10).

    flat=True yields pure noise regardless of Z — the amr_test regression
    (design/14 §4), where a faint field produced a structureless metric curve.
    """
    from scipy.ndimage import gaussian_filter

    rng = np.random.default_rng(7)
    scene = (rng.random((width, width)) * 60000).astype(np.float32)

    core = MagicMock()
    core.get_focus_device.return_value = "DStage"
    current_z = [50.0]
    core.set_position.side_effect = lambda z: current_z.__setitem__(0, z)
    core.get_position.side_effect = lambda: current_z[0]

    def get_tagged_image():
        z = current_z[0]
        if flat:
            # Constant-statistics noise: the metric varies only by sampling.
            pixels = (np.random.rand(width, width) * 3000 + 30000).astype(np.uint16)
        else:
            sigma = 0.5 + abs(z - best_z)      # blur grows away from focus
            pixels = gaussian_filter(scene, sigma=sigma).astype(np.uint16)
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


class TestSweep:
    def test_sweep_finds_correct_z(self):
        ctrl = make_ctrl_with_focus_at(52.0)
        result = sweep_autofocus(ctrl, 45.0, 55.0, 1.0, settle_ms=0)
        assert abs(result.best_z_um - 52.0) <= 1.0

    def test_peak_interior_when_peak_interior(self):
        ctrl = make_ctrl_with_focus_at(50.0)
        result = sweep_autofocus(ctrl, 45.0, 55.0, 1.0, settle_ms=0)
        assert result.peak_interior

    def test_not_peak_interior_when_peak_at_boundary(self):
        # best_z is below the sweep start → metric is highest at first step
        ctrl = make_ctrl_with_focus_at(44.0)
        result = sweep_autofocus(ctrl, 45.0, 55.0, 1.0, settle_ms=0)
        assert not result.peak_interior

    def test_sweep_returns_correct_structure(self):
        ctrl = make_ctrl_with_focus_at(50.0)
        result = sweep_autofocus(ctrl, 48.0, 52.0, 1.0, settle_ms=0)
        assert len(result.z_positions) == len(result.metric_values)
        assert result.best_z_um in result.z_positions

    def test_move_to_best_false_leaves_stage_at_sweep_end(self):
        ctrl = make_ctrl_with_focus_at(50.0)
        sweep_autofocus(ctrl, 48.0, 52.0, 1.0, settle_ms=0, move_to_best=False)
        assert ctrl.core.get_position() == pytest.approx(52.0)

    def test_linspace_covers_both_endpoints(self):
        ctrl = make_ctrl_with_focus_at(50.0)
        result = sweep_autofocus(ctrl, 45.0, 55.0, 0.5, settle_ms=0)
        assert result.z_positions[0] == pytest.approx(45.0)
        assert result.z_positions[-1] == pytest.approx(55.0)
        assert len(result.z_positions) == 21


class TestCurveContrast:
    def test_amr_test_flat_curve_scores_below_threshold(self):
        # The regression curve: 35668..37875 at constant focus. The old code
        # said settled=true, warning=null and moved the stage 6 um.
        rng = np.random.default_rng(0)
        curve = list(rng.uniform(35668, 37875, size=11))
        assert curve_contrast(curve) < MIN_CONTRAST

    def test_real_focus_curve_scores_above_threshold(self):
        z = np.linspace(-5, 5, 11)
        curve = list(20000 * np.exp(-(z ** 2) / 2) + 1000)
        assert curve_contrast(curve) > MIN_CONTRAST

    def test_empty_and_constant_curves_are_zero(self):
        assert curve_contrast([]) == 0.0
        assert curve_contrast([5.0, 5.0, 5.0]) == 0.0


class TestCoarseThenFine:
    def test_converges_and_reports_both_passes(self):
        ctrl = make_ctrl_with_focus_at(50.0)
        result = coarse_then_fine_autofocus(ctrl, z_range_um=10.0, coarse_step_um=2.0,
                                            fine_step_um=0.5, settle_ms=0)
        assert result.converged and result.moved
        assert result.reason is None
        assert result.coarse.z_positions and result.fine.z_positions
        assert abs(result.final_z_um - 50.0) <= 0.5
        assert ctrl.core.get_position() == pytest.approx(result.final_z_um)

    def test_coarse_window_contains_entry_z(self):
        ctrl = make_ctrl_with_focus_at(50.0)
        result = coarse_then_fine_autofocus(ctrl, z_range_um=20.0, coarse_step_um=2.5,
                                            fine_step_um=0.5, settle_ms=0)
        zs = result.coarse.z_positions
        # The old fine-only result failed this: an 11-point window at
        # 37.7..42.7 um from entry_z=45.2 (design/14 §4).
        assert min(zs) <= result.entry_z_um <= max(zs)

    def test_flat_metric_does_not_move_the_stage(self):
        """The amr_test regression: a structureless curve must not move Z."""
        ctrl = make_ctrl_with_focus_at(50.0, flat=True)
        result = coarse_then_fine_autofocus(ctrl, z_range_um=20.0, coarse_step_um=2.5,
                                            fine_step_um=0.5, settle_ms=0)
        assert result.converged is False
        assert result.moved is False
        assert "flat" in result.reason
        assert result.final_z_um == pytest.approx(50.0)
        assert ctrl.core.get_position() == pytest.approx(50.0)  # restored

    def test_edge_peak_does_not_converge_or_move(self):
        """design/28 F1: a peak pinned at the sweep boundary means the true focus
        is OUTSIDE the window. That is NOT convergence — the stage must not move,
        even though the (monotonic) curve has plenty of contrast."""
        ctrl = make_ctrl_with_focus_at(best_z=62.0)   # focus well above 50±5
        result = coarse_then_fine_autofocus(ctrl, z_range_um=10.0, coarse_step_um=2.0,
                                            fine_step_um=0.5, settle_ms=0)
        assert result.converged is False
        assert result.moved is False
        assert not result.fine.peak_interior
        assert "edge" in result.reason
        assert result.final_z_um == pytest.approx(50.0)
        assert ctrl.core.get_position() == pytest.approx(50.0)  # restored

    def test_fine_sweep_clamped_to_guarded_window(self):
        # Coarse peak sits at the top boundary of the range. The fine sweep must not
        # step past current_z ± z_range/2 (the window the caller guarded).
        current_z = 50.0
        z_range = 10.0
        ctrl = make_ctrl_with_focus_at(best_z=60.0)  # peak above the window
        result = coarse_then_fine_autofocus(ctrl, z_range_um=z_range, coarse_step_um=2.0,
                                            fine_step_um=0.5, settle_ms=0)
        lo, hi = current_z - z_range / 2, current_z + z_range / 2
        for sweep in (result.coarse, result.fine):
            if sweep is not None:
                assert all(lo - 1e-9 <= z <= hi + 1e-9 for z in sweep.z_positions)


class TestSingleSweepAutofocus:
    def test_converges_on_real_peak(self):
        ctrl = make_ctrl_with_focus_at(52.0)
        result = single_sweep_autofocus(ctrl, z_range_um=10.0, z_step_um=1.0, settle_ms=0)
        assert result.converged and result.moved
        assert abs(result.final_z_um - 52.0) <= 1.0
        assert result.fine is None

    def test_flat_metric_restores_entry_z(self):
        ctrl = make_ctrl_with_focus_at(50.0, flat=True)
        result = single_sweep_autofocus(ctrl, z_range_um=10.0, z_step_um=1.0, settle_ms=0)
        assert result.converged is False
        assert ctrl.core.get_position() == pytest.approx(50.0)

    def test_edge_peak_does_not_converge_or_move(self):
        # design/28 F1: peak at the boundary is not convergence for the one-pass
        # variant either.
        ctrl = make_ctrl_with_focus_at(best_z=62.0)
        result = single_sweep_autofocus(ctrl, z_range_um=10.0, z_step_um=1.0, settle_ms=0)
        assert result.converged is False
        assert result.moved is False
        assert "edge" in result.reason
        assert ctrl.core.get_position() == pytest.approx(50.0)


class TestAutoMetricSelection:
    def test_auto_metric_resolves_to_a_callable(self):
        # AUTO_METRIC snaps the entry field and picks a concrete metric; a
        # textured mock scene resolves to normalized_laplacian_variance and
        # focuses normally.
        from microclaw.autofocus import AUTO_METRIC
        from microclaw.image_analysis import normalized_laplacian_variance

        ctrl = make_ctrl_with_focus_at(52.0)
        result = single_sweep_autofocus(
            ctrl, z_range_um=10.0, z_step_um=1.0, settle_ms=0, metric_fn=AUTO_METRIC
        )
        assert result.converged
        assert abs(result.final_z_um - 52.0) <= 1.0

    def test_invalid_metric_fn_raises(self):
        ctrl = make_ctrl_with_focus_at(50.0)
        with pytest.raises(ValueError, match="metric_fn"):
            single_sweep_autofocus(
                ctrl, z_range_um=10.0, z_step_um=1.0, settle_ms=0, metric_fn="nope"
            )
