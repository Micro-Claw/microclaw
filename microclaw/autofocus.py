from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from microclaw.image_analysis import laplacian_variance, snap_to_numpy


@dataclass
class AutofocusResult:
    best_z_um: float
    metric_values: list[float]
    z_positions: list[float]
    settled: bool  # True if peak is not at the boundary of the sweep


def sweep_autofocus(
    ctrl,
    z_start_um: float,
    z_end_um: float,
    z_step_um: float,
    settle_ms: int = 50,
    metric_fn: Callable[[np.ndarray], float] = laplacian_variance,
) -> AutofocusResult:
    """Sweep Z, measure focus metric at each step, move to best Z."""
    focus_device = ctrl.core.get_focus_device()
    z_positions = list(np.arange(z_start_um, z_end_um + z_step_um / 2, z_step_um))
    metric_values = []

    for z in z_positions:
        ctrl.core.set_position(z)
        ctrl.core.wait_for_device(focus_device)
        if settle_ms > 0:
            time.sleep(settle_ms / 1000.0)
        metric_values.append(metric_fn(snap_to_numpy(ctrl)))

    best_idx = int(np.argmax(metric_values))
    best_z = z_positions[best_idx]
    ctrl.core.set_position(best_z)
    ctrl.core.wait_for_device(focus_device)

    return AutofocusResult(
        best_z_um=best_z,
        metric_values=metric_values,
        z_positions=z_positions,
        settled=0 < best_idx < len(z_positions) - 1,
    )


def coarse_then_fine_autofocus(
    ctrl,
    z_range_um: float,
    coarse_step_um: float,
    fine_step_um: float,
    settle_ms: int = 50,
) -> AutofocusResult:
    """Two-pass autofocus: coarse sweep then fine sweep around the coarse peak.

    The fine window is clamped to the coarse window (current_z ± z_range/2) so a
    coarse peak sitting at the boundary can't push the fine sweep past the range
    the caller guarded with check_z.
    """
    current_z = ctrl.core.get_position()
    lo_bound = current_z - z_range_um / 2
    hi_bound = current_z + z_range_um / 2
    coarse = sweep_autofocus(ctrl, lo_bound, hi_bound, coarse_step_um, settle_ms)
    lo = max(coarse.best_z_um - coarse_step_um, lo_bound)
    hi = min(coarse.best_z_um + coarse_step_um, hi_bound)
    return sweep_autofocus(ctrl, lo, hi, fine_step_um, settle_ms)
