from typing import NamedTuple
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

core = Core()

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
# NOT EMITTED: start_live_view
raise RuntimeError('NOT EMITTED: start_live_view')

# RECORDED TOOL: snap_and_analyze
# NOT EMITTED: snap_and_analyze
raise RuntimeError('NOT EMITTED: snap_and_analyze')

# RECORDED TOOL: run_autofocus
# NOT EMITTED: run_autofocus
raise RuntimeError('NOT EMITTED: run_autofocus')

# RECORDED TOOL: set_focus_lock
# NOT EMITTED: set_focus_lock
raise RuntimeError('NOT EMITTED: set_focus_lock')

# RECORDED TOOL: list_hooks
# No hardware-routine effect.

# RECORDED TOOL: describe_hook
# No hardware-routine effect.

# RECORDED TOOL: describe_hook
# No hardware-routine effect.

# RECORDED TOOL: validate_positions
# No hardware-routine effect.

# RECORDED TOOL: run_multiposition_acquisition
# NOT EMITTED: run_multiposition_acquisition
raise RuntimeError('NOT EMITTED: run_multiposition_acquisition')

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
# NOT EMITTED: run_multiposition_acquisition
raise RuntimeError('NOT EMITTED: run_multiposition_acquisition')

# RECORDED TOOL: read_hook_log
# No hardware-routine effect.

# RECORDED TOOL: read_hook_log
# No hardware-routine effect.

# RECORDED TOOL: build_stage_coordinate_mosaic
# NOT EMITTED: build_stage_coordinate_mosaic
raise RuntimeError('NOT EMITTED: build_stage_coordinate_mosaic')

# RECORDED TOOL: build_stage_coordinate_mosaic
# NOT EMITTED: build_stage_coordinate_mosaic
raise RuntimeError('NOT EMITTED: build_stage_coordinate_mosaic')

# RECORDED TOOL: build_stage_coordinate_mosaic
# NOT EMITTED: build_stage_coordinate_mosaic
raise RuntimeError('NOT EMITTED: build_stage_coordinate_mosaic')

# RECORDED TOOL: inspect_artifacts
# No hardware-routine effect.

# RECORDED TOOL: set_focus_lock
# NOT EMITTED: set_focus_lock
raise RuntimeError('NOT EMITTED: set_focus_lock')

# RECORDED TOOL: set_device_property
core.set_property('iChrome-MLE-TCP', 'Laser 2: 1. Enable', '0')

# RECORDED TOOL: get_system_state
# No hardware-routine effect.
