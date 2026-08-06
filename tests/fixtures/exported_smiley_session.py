from __future__ import annotations
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, NamedTuple, Optional
import numpy as np
from pycromanager import Acquisition, Core, multi_d_acquisition_events


UNCALIBRATED_MIN_SNR_FALLBACK = 3.1

class ImageStats(NamedTuple):
    focus_metric: float          # tenengrad — the number tools report
    focus_metric_valid: bool     # False when snr < min_snr: no signal to be sharp about
    background_level: float      # robust: median (the camera offset, Evolve512 ≈ 400)
    snr: float                   # the shared snr() definition below
    mean_intensity: float
    max_intensity: float
    min_intensity: float
    saturated_fraction: float

def _reshape_pixels(pix, w: int, h: int, bpp: int, n_comp: int) -> np.ndarray:
    """Shape raw pixels into (H, W) or (H, W, C) with the correct dtype.

    Derives the dtype from bytes-per-pixel and component count rather than
    assuming 16-bit mono. Component count is decisive: an RGB32 frame is
    4×uint8 (BGRA), not 1×uint32. Accepts raw bytes (core path) or an
    already-typed ndarray (studio path — get_raw_pixels() arrives as numpy).
    """
    if n_comp > 1:                                   # e.g. RGB32 = 4 × uint8
        comp_dtype = {1: np.uint8, 2: np.uint16}.get(bpp // n_comp)
        if comp_dtype is None:
            raise ValueError(
                f"Unsupported bytes-per-component: {bpp}/{n_comp}"
            )
        arr = (
            np.frombuffer(pix, dtype=comp_dtype)
            if isinstance(pix, (bytes, bytearray))
            else np.asarray(pix, dtype=comp_dtype)
        )
        return arr.reshape(h, w, n_comp)
    dtype = {1: np.uint8, 2: np.uint16, 4: np.uint32}.get(bpp)
    if dtype is None:
        raise ValueError(f"Unsupported bytes-per-pixel: {bpp}")
    arr = (
        np.frombuffer(pix, dtype=dtype)
        if isinstance(pix, (bytes, bytearray))
        else np.asarray(pix, dtype=dtype)
    )
    return arr.reshape(h, w)

def snap_to_numpy(ctrl) -> np.ndarray:
    """Headless snap via the core's tagged image API — does NOT touch the viewer.

    Use this for sweeps (autofocus) where repainting the viewer 20 times is
    churn. For an image the user should see, use snap_to_numpy_displayed.
    Throws "sequence acquisition is running" if live view is on; wrap the call
    in tools._pause_live.
    """
    ctrl.core.snap_image()
    tagged = ctrl.core.get_tagged_image()
    w, h = int(tagged.tags["Width"]), int(tagged.tags["Height"])
    bpp = int(ctrl.core.get_bytes_per_pixel())
    n_comp = int(ctrl.core.get_number_of_components())
    return _reshape_pixels(tagged.pix, w, h, bpp, n_comp)

def snr(image: np.ndarray, background: float | None = None) -> float:
    """Signal over the noise floor: (p99.5 - bg) / (1.4826 * MAD).

    Robust at both ends: a percentile rather than max() so one hot pixel is not
    "signal", and MAD rather than std() so the noise estimate is not inflated by
    the signal it is meant to be measuring against.

    The ONE definition (design/23 F7). detect_features calls this rather than
    keeping its own peak/std — two different quantities both named `snr`, returned
    by different tools, is the design/20 failure class exactly.
    """
    img = image.astype(np.float64)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    bg = float(np.median(img)) if background is None else background
    mad = float(np.median(np.abs(img - np.median(img))))
    noise = 1.4826 * mad
    if noise <= 0:                  # flat frame (all-zero, or saturated everywhere)
        return 0.0
    return float((np.percentile(img, 99.5) - bg) / noise)

def tenengrad(image: np.ndarray) -> float:
    """mean(|∇I|²) over Sobel gradients — THE focus metric (design/36).

    Higher = sharper, and it is maximised at focus on every field type tested:
    sparse puncta and extended structure, bright and 10–20× dimmer, where the
    normalised Laplacian is maximised at maximum defocus on three of those four.

    Two properties earn it the job over the metric it replaces:

      * The Sobel kernel smooths across the differencing axis, so the noise
        floor does not swamp broad structure the way a bare Laplacian does.
      * There is no normaliser, so there is no focus-dependent denominator to
        fight the numerator. A constant added to every pixel (camera offset, a
        uniform haze, a background pedestal) differentiates away.

    It is polarity-insensitive (the gradients are squared), so dark-on-bright
    scores like bright-on-dark, and it is a per-pixel mean, so frame size does
    not enter.

    WHAT IT IS NOT: illumination-invariant. It scales roughly with the square of
    photon count, so it is comparable only among frames sharing ROI, exposure,
    binning and illumination. That is exactly the domain a Z sweep holds fixed.
    Tools reporting this number carry a `metric_valid_for` block naming that
    domain; do not compare across it. design/14 §10 tried to buy comparability
    with a normaliser instead, and bought an inverted metric — see
    `normalized_laplacian_variance`.
    """
    from scipy.ndimage import sobel

    img = image.astype(np.float64)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    gy = sobel(img, axis=0)
    gx = sobel(img, axis=1)
    return float(np.mean(gx * gx + gy * gy))

def compute_stats(
    image: np.ndarray,
    min_snr: float = UNCALIBRATED_MIN_SNR_FALLBACK,
) -> ImageStats:
    """Per-image statistics, including the focus metric and its validity gate.

    focus_metric is `tenengrad` (design/36). It was normalized_laplacian_variance
    until two live sessions showed that metric bottoming out at the operator's
    manual best focus; the normaliser that was meant to make it comparable across
    illumination is what inverted it. Computing the one metric here means one
    computation, one validity flag, and no dead field to mistake for the live one
    (design/23 F7); the older metrics stay exported but score nothing.

    The number scales with photon count, so it is comparable only among frames
    sharing ROI, exposure, binning and illumination. Tool payloads say so in
    their `metric_valid_for` block; keep that block truthful wherever this is
    reported.

    focus_metric_valid is False below min_snr: a field with no signal has no
    sharpness to measure (design/25).
    """
    bit_max = float(np.iinfo(image.dtype).max) if np.issubdtype(image.dtype, np.integer) else 1.0
    img = image.astype(np.float64)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    bg = float(np.median(img))           # computed once, shared by snr and the metric
    s = snr(image, background=bg)
    return ImageStats(
        focus_metric=tenengrad(image),
        focus_metric_valid=s >= min_snr,
        background_level=round(bg, 1),
        snr=round(s, 2),
        mean_intensity=float(np.mean(image)),
        max_intensity=float(np.max(image)),
        min_intensity=float(np.min(image)),
        saturated_fraction=float(np.sum(image >= bit_max) / image.size),
    )

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

MIN_CONTRAST = 0.15

def sweep_plane_count(z_start_um: float, z_end_um: float, z_step_um: float) -> int:
    """Return the exact number of camera snaps made by ``sweep_autofocus``."""
    return max(int(round(abs(z_end_um - z_start_um) / z_step_um)) + 1, 1)

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
        f"exposure), autofocus on the full frame rather than a small ROI, or "
        f"focus manually."
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

def _run_autofocus_passes(
    ctrl: MicroscopeController,
    z_range_um: float,
    z_step_um: float,
    method: str,
    settle_ms: int,
) -> AutofocusResult:
    if method == "coarse_then_fine":
        return coarse_then_fine_autofocus(
            ctrl, z_range_um, max(z_step_um * 5, 1.0), z_step_um, settle_ms,
        )
    return single_sweep_autofocus(ctrl, z_range_um, z_step_um, settle_ms)

core = Core()
mm = SimpleNamespace(core=core)

# RECORDED TOOL: get_system_state
# No hardware-routine effect.

# RECORDED TOOL: check_emu_installed
# No hardware-routine effect.

# RECORDED TOOL: get_emu_configuration
# No hardware-routine effect.

# RECORDED TOOL: get_emu_laser_map
# No hardware-routine effect.

# RECORDED TOOL: get_available_channels
# No hardware-routine effect.

# RECORDED TOOL: get_device_property
# No hardware-routine effect.

# RECORDED TOOL: get_device_property
# No hardware-routine effect.

# RECORDED TOOL: get_roi
# No hardware-routine effect.

# RECORDED TOOL: get_pixel_size
# No hardware-routine effect.

# RECORDED TOOL: get_focus_lock_state
# No hardware-routine effect.

# RECORDED TOOL: get_xy_position
# No hardware-routine effect.

# RECORDED TOOL: set_device_property
core.set_property('Thorlabs Filter Wheel', 'State', '3')

# RECORDED TOOL: move_stage_xy
core.set_xy_position(757.4, -7232.4)

# RECORDED TOOL: get_device_property
# No hardware-routine effect.

# RECORDED TOOL: get_device_property
# No hardware-routine effect.

# RECORDED TOOL: start_live_view
# No hardware-routine effect.

# RECORDED TOOL: snap_and_analyze
image = snap_to_numpy(mm)
stats = compute_stats(image, min_snr=3.1)

# RECORDED TOOL: run_autofocus
autofocus_result = _run_autofocus_passes(mm, 20, 0.5, 'coarse_then_fine', 50)

# RECORDED TOOL: set_focus_lock
core.set_property('PIZStage', 'External sensor', '1')

# RECORDED TOOL: list_hooks
# No hardware-routine effect.

# RECORDED TOOL: describe_hook
# No hardware-routine effect.

# RECORDED TOOL: describe_hook
# No hardware-routine effect.

# RECORDED TOOL: validate_positions
# No hardware-routine effect.

# RECORDED TOOL: run_multiposition_acquisition
events = multi_d_acquisition_events(**{'num_time_points': 1, 'time_interval_s': 0, 'xyz_positions': [(757.4, -7232.4, 44.376), (782.4, -7232.4, 44.376), (832.4, -7232.4, 44.376), (857.4, -7232.4, 44.376), (757.4, -7307.4, 44.376), (782.4, -7332.4, 44.376), (807.4, -7332.4, 44.376), (832.4, -7332.4, 44.376), (857.4, -7307.4, 44.376)], 'position_labels': ['L_eye_a', 'L_eye_b', 'R_eye_a', 'R_eye_b', 'mouth_L', 'mouth_1', 'mouth_2', 'mouth_3', 'mouth_R']})
with Acquisition(directory='C:\\\\Users\\\\ries\\\\microclaw_data\\\\smiley\\\\ch640', name='smiley_640') as acq:
    acq.acquire(events)

# RECORDED TOOL: set_device_property
core.set_property('Thorlabs Filter Wheel', 'State', '1')

# RECORDED TOOL: set_device_property
core.set_property('iChrome-MLE-TCP', 'Laser 2: 1. Enable', '1')

# RECORDED TOOL: set_device_property
core.set_property('iChrome-MLE-TCP', 'Laser 1: 1. Enable', '0')

# RECORDED TOOL: get_device_property
# No hardware-routine effect.

# RECORDED TOOL: get_device_property
# No hardware-routine effect.

# RECORDED TOOL: run_multiposition_acquisition
events = multi_d_acquisition_events(**{'num_time_points': 1, 'time_interval_s': 0, 'xyz_positions': [(757.4, -7232.4, 44.376), (782.4, -7232.4, 44.376), (832.4, -7232.4, 44.376), (857.4, -7232.4, 44.376), (757.4, -7307.4, 44.376), (782.4, -7332.4, 44.376), (807.4, -7332.4, 44.376), (832.4, -7332.4, 44.376), (857.4, -7307.4, 44.376)], 'position_labels': ['L_eye_a', 'L_eye_b', 'R_eye_a', 'R_eye_b', 'mouth_L', 'mouth_1', 'mouth_2', 'mouth_3', 'mouth_R']})
with Acquisition(directory='C:\\\\Users\\\\ries\\\\microclaw_data\\\\smiley\\\\ch561', name='smiley_561') as acq:
    acq.acquire(events)

# RECORDED TOOL: read_hook_log
# No hardware-routine effect.

# RECORDED TOOL: read_hook_log
# No hardware-routine effect.

# RECORDED TOOL: build_stage_coordinate_mosaic
# NOT EMITTED: build_stage_coordinate_mosaic — offline mosaic dependencies transitively require the package calibration module, so inlining would not be standalone
raise RuntimeError('NOT EMITTED: build_stage_coordinate_mosaic — offline mosaic dependencies transitively require the package calibration module, so inlining would not be standalone')

# RECORDED TOOL: build_stage_coordinate_mosaic
# NOT EMITTED: build_stage_coordinate_mosaic — offline mosaic dependencies transitively require the package calibration module, so inlining would not be standalone
raise RuntimeError('NOT EMITTED: build_stage_coordinate_mosaic — offline mosaic dependencies transitively require the package calibration module, so inlining would not be standalone')

# RECORDED TOOL: build_stage_coordinate_mosaic
# NOT EMITTED: build_stage_coordinate_mosaic — offline mosaic dependencies transitively require the package calibration module, so inlining would not be standalone
raise RuntimeError('NOT EMITTED: build_stage_coordinate_mosaic — offline mosaic dependencies transitively require the package calibration module, so inlining would not be standalone')

# RECORDED TOOL: inspect_artifacts
# No hardware-routine effect.

# RECORDED TOOL: set_focus_lock
core.set_property('PIZStage', 'External sensor', '0')

# RECORDED TOOL: set_device_property
core.set_property('iChrome-MLE-TCP', 'Laser 2: 1. Enable', '0')

# RECORDED TOOL: get_system_state
# No hardware-routine effect.
