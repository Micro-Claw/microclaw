from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from microclaw.image_analysis import snap_to_numpy, tenengrad


@dataclass
class SweepResult:
    """One Z sweep. peak_interior means only "argmax not at a sweep edge" —
    it says nothing about whether a real peak exists (see contrast)."""

    z_positions: list[float]
    metric_values: list[float]
    best_z_um: float
    peak_interior: bool


@dataclass
class AutofocusResult:
    """Both passes of an autofocus, plus what actually happened to the stage.

    The old API returned only the fine SweepResult, so the coarse pass — the
    one that actually chose the focal plane and constrained the fine window to
    ±coarse_step around its choice — was invisible to the caller. In the
    amr_test session (design/14 §4) the coarse pass picked 40.2 µm when true
    focus was ~45.2 µm, and the model audited an 11-point fine curve it had no
    way of knowing was a consequence.
    """

    coarse: SweepResult
    fine: Optional[SweepResult]
    entry_z_um: float
    final_z_um: float
    converged: bool
    moved: bool
    reason: Optional[str]


# Minimum curve contrast below which the sweep is treated as structureless
# noise and the stage is NOT moved. The amr_test regression scores ~0.06;
# tune upward only against real curves.
MIN_CONTRAST = 0.15


def sweep_plane_count(z_start_um: float, z_end_um: float, z_step_um: float) -> int:
    """Return the exact number of camera snaps made by ``sweep_autofocus``."""
    return max(int(round(abs(z_end_um - z_start_um) / z_step_um)) + 1, 1)


def coarse_then_fine_plane_count(
    z_range_um: float, coarse_step_um: float, fine_step_um: float
) -> int:
    """Worst-case snaps for both autofocus passes.

    The fine window is at most two coarse steps wide and is clamped to the
    coarse window.  Planning the maximum is intentional: a flat coarse curve
    may stop before the fine pass and close the reservation under-spent.
    """
    coarse = sweep_plane_count(-z_range_um / 2, z_range_um / 2, coarse_step_um)
    fine_span = min(z_range_um, 2 * coarse_step_um)
    return coarse + sweep_plane_count(0, fine_span, fine_step_um)


def curve_contrast(metric_values) -> float:
    """Dynamic range of the metric curve relative to its typical level:
    (max - min) / median.

    A real focus curve varies by a large factor across the sweep; pure noise
    varies by its noise amplitude. The amr_test fine sweep ran 35668..37875 —
    contrast ~0.06, i.e. no structure at all — yet the old code reported
    settled=true, warning=null and moved the stage 6 µm onto noise.

    (design/14's appendix sketched (max - median)/(max - min) instead, but that
    statistic is ~0.5 for pure noise — the median of noise sits mid-range — so
    it cannot fail the design's own flat-curve regression test. The "~0.06"
    the design cites for the amr curve is the relative range, which is what
    this implements.)
    """
    m = np.asarray(metric_values, dtype=float)
    if m.size == 0:
        return 0.0
    med = float(np.median(m))
    span = float(m.max() - m.min())
    if span <= 0:
        return 0.0
    if med <= 0:
        return float("inf")
    return span / med


def sweep_autofocus(
    ctrl,
    z_start_um: float,
    z_end_um: float,
    z_step_um: float,
    settle_ms: int = 50,
    metric_fn: Callable[[np.ndarray], float] = tenengrad,
    move_to_best: bool = True,
) -> SweepResult:
    """Sweep Z and measure the focus metric; move to best Z only if move_to_best."""
    focus_device = ctrl.core.get_focus_device()
    # linspace, not arange: float arange accumulates error and can drop or
    # duplicate the endpoint.
    n = sweep_plane_count(z_start_um, z_end_um, z_step_um)
    z_positions = [float(z) for z in np.linspace(z_start_um, z_end_um, n)]
    metric_values = []

    for z in z_positions:
        ctrl.core.set_position(z)
        ctrl.core.wait_for_device(focus_device)
        if settle_ms > 0:
            time.sleep(settle_ms / 1000.0)
        metric_values.append(metric_fn(snap_to_numpy(ctrl)))

    best_idx = int(np.argmax(metric_values))
    best_z = z_positions[best_idx]
    if move_to_best:
        ctrl.core.set_position(best_z)
        ctrl.core.wait_for_device(focus_device)

    return SweepResult(
        z_positions=z_positions,
        metric_values=metric_values,
        best_z_um=best_z,
        peak_interior=0 < best_idx < len(z_positions) - 1,
    )


def _restore(ctrl, z: float) -> None:
    ctrl.core.set_position(z)
    ctrl.core.wait_for_device(ctrl.core.get_focus_device())


def _flat_reason(which: str, contrast: float, entry_z: float) -> str:
    return (
        f"{which} focus metric is flat (contrast {contrast:.2f} < "
        f"{MIN_CONTRAST}) — the sweep saw noise, not a focus peak. Z was NOT "
        f"moved (restored to {entry_z:.3f} µm). Increase signal (laser power / "
        f"exposure), restrict the metric region around structure when the field "
        f"is mostly background, or focus manually."
    )


def _edge_reason(which: str, sweep: SweepResult, entry_z: float) -> str:
    """Mirror of _flat_reason for a peak pinned at a sweep boundary (design/28 F1).

    A boundary argmax can mean that focus is outside the window, but can also
    come from a U-shaped or noise-dominated curve. It is insufficient evidence
    for convergence in every case, so do not diagnose a specific cause here.
    """
    return (
        f"{which} focus peak is at the edge of the searched Z range "
        f"(best {sweep.best_z_um:.3f} µm sits at a sweep boundary), so there is "
        f"no interior focus maximum and this is NOT convergence. Z was NOT "
        f"moved (restored to {entry_z:.3f} µm). Focus may be outside the window, "
        f"or the curve may be noise-dominated/non-unimodal; inspect the curve "
        f"and signal before widening or retrying."
    )


def coarse_then_fine_autofocus(
    ctrl,
    z_range_um: float,
    coarse_step_um: float,
    fine_step_um: float,
    settle_ms: int = 50,
    metric_fn: Callable[[np.ndarray], float] = tenengrad,
    min_contrast: float = MIN_CONTRAST,
) -> AutofocusResult:
    """Two-pass autofocus that reports BOTH passes and restores Z on a flat curve.

    The fine window is clamped to the coarse window (entry_z ± z_range/2) so a
    coarse peak sitting at the boundary can't push the fine sweep past the
    range the caller guarded with check_z. A pass whose metric curve has no
    structure (see curve_contrast) aborts the autofocus WITHOUT moving Z —
    moving hardware onto the argmax of noise is worse than doing nothing. A fine
    peak pinned at a sweep boundary is likewise NOT convergence (design/28 F1),
    so the stage is left where it was without guessing why the curve failed.

    The metric is `tenengrad` (design/36), which is maximised at focus and is
    polarity-insensitive, so bright-on-dark and dark-on-bright structure both
    score. It is NOT illumination-invariant — a sweep must therefore hold
    illumination, exposure, ROI and binning fixed, which it does. It replaced
    normalized_laplacian_variance, whose contrast normaliser made it a MINIMUM at
    focus on real fields: on M5 that sent a widened sweep monotonically uphill to
    69.9 µm while the operator's own best focus, at 49.9, scored lowest of all.

    Callers may inject a different callable for controlled experiments;
    autofocus does not guess a metric from one frame.
    """
    entry_z = float(ctrl.core.get_position())
    lo_bound = entry_z - z_range_um / 2
    hi_bound = entry_z + z_range_um / 2

    coarse = sweep_autofocus(
        ctrl, lo_bound, hi_bound, coarse_step_um, settle_ms,
        metric_fn=metric_fn, move_to_best=False,
    )
    coarse_contrast = curve_contrast(coarse.metric_values)
    if coarse_contrast < min_contrast:
        _restore(ctrl, entry_z)
        return AutofocusResult(
            coarse=coarse, fine=None, entry_z_um=entry_z, final_z_um=entry_z,
            converged=False, moved=False,
            reason=_flat_reason("Coarse", coarse_contrast, entry_z),
        )

    lo = max(coarse.best_z_um - coarse_step_um, lo_bound)
    hi = min(coarse.best_z_um + coarse_step_um, hi_bound)
    fine = sweep_autofocus(
        ctrl, lo, hi, fine_step_um, settle_ms,
        metric_fn=metric_fn, move_to_best=False,
    )
    fine_contrast = curve_contrast(fine.metric_values)
    if fine_contrast < min_contrast:
        _restore(ctrl, entry_z)
        return AutofocusResult(
            coarse=coarse, fine=fine, entry_z_um=entry_z, final_z_um=entry_z,
            converged=False, moved=False,
            reason=_flat_reason("Fine", fine_contrast, entry_z),
        )

    if not fine.peak_interior:
        _restore(ctrl, entry_z)
        return AutofocusResult(
            coarse=coarse, fine=fine, entry_z_um=entry_z, final_z_um=entry_z,
            converged=False, moved=False,
            reason=_edge_reason("Fine", fine, entry_z),
        )

    _restore(ctrl, fine.best_z_um)
    return AutofocusResult(
        coarse=coarse, fine=fine, entry_z_um=entry_z,
        final_z_um=fine.best_z_um, converged=True, moved=True, reason=None,
    )


def single_sweep_autofocus(
    ctrl,
    z_range_um: float,
    z_step_um: float,
    settle_ms: int = 50,
    metric_fn: Callable[[np.ndarray], float] = tenengrad,
    min_contrast: float = MIN_CONTRAST,
) -> AutofocusResult:
    """One-pass autofocus with the same contrast gate and result shape as
    coarse_then_fine_autofocus (the single pass is reported as `coarse`).

    Like the two-pass variant it refuses to move on a flat curve OR on a peak
    pinned at a sweep boundary (design/28 F1).
    """
    entry_z = float(ctrl.core.get_position())
    sweep = sweep_autofocus(
        ctrl, entry_z - z_range_um / 2, entry_z + z_range_um / 2, z_step_um,
        settle_ms, metric_fn=metric_fn, move_to_best=False,
    )
    contrast = curve_contrast(sweep.metric_values)
    if contrast < min_contrast:
        _restore(ctrl, entry_z)
        return AutofocusResult(
            coarse=sweep, fine=None, entry_z_um=entry_z, final_z_um=entry_z,
            converged=False, moved=False,
            reason=_flat_reason("Sweep", contrast, entry_z),
        )
    if not sweep.peak_interior:
        _restore(ctrl, entry_z)
        return AutofocusResult(
            coarse=sweep, fine=None, entry_z_um=entry_z, final_z_um=entry_z,
            converged=False, moved=False,
            reason=_edge_reason("Sweep", sweep, entry_z),
        )
    _restore(ctrl, sweep.best_z_um)
    return AutofocusResult(
        coarse=sweep, fine=None, entry_z_um=entry_z,
        final_z_um=sweep.best_z_um, converged=True, moved=True, reason=None,
    )
