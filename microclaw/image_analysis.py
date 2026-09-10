from __future__ import annotations
import base64
import io
import json
import time
from typing import NamedTuple

import numpy as np
from PIL import Image


class ImageStats(NamedTuple):
    focus_metric: float          # tenengrad — the number tools report
    focus_metric_valid: bool     # False when snr < min_snr: no signal to be sharp about
    background_level: float      # robust: median (the camera offset, Evolve512 ≈ 400)
    snr: float | None            # None when snr_valid is False
    snr_valid: bool
    snr_invalid_reason: str | None
    mean_intensity: float
    max_intensity: float
    min_intensity: float
    saturated_fraction: float
    signal_coverage: float       # pixels above background + min_snr * noise
    structure_coverage: float    # same threshold after a sigma=2 px blur
    signal_concentration: float  # brightest 1% share of above-background signal


#: Below this SNR, "how sharp is this field?" has no answer, because there is
#: nothing in the field to be sharp (design/25). An empty field is pinned at
#: SNR ≈ 2.58 by snr() below, and synthetic cells read 11–84, so 3.0 sits just
#: above the empty floor with room. STILL A PLACEHOLDER: design/23 F7 is explicit
#: that it must be measured on real frames against snr(), and it plausibly varies
#: by objective and by sample. If it needs per-rig values it belongs in
#: safety_config.yaml alongside the other rig facts.
UNCALIBRATED_MIN_SNR_FALLBACK = 3.1
# The least-clipped measured M5 smiley frame was 0.048% saturated; 0.01%
# catches it with ~5x margin while tolerating a smaller isolated-pixel tail.
MAX_SATURATED_FRACTION_FOR_SNR = 0.0001
#: Coverage tolerates far more clipping than snr does, because it is a fraction
#: and not a tail statistic: a clipped pixel is still legitimately above the
#: threshold, whereas a plateau lands p99.5 inside itself and breaks snr.
#: Bounded by measurement on both sides, and uncalibrated between them. Real
#: bead fields (`stitch_test_1`, six fields) run 0.017%-0.220% saturated and
#: are entirely usable — the SNR gate would refuse all six. The tiles that put
#: clipped frames at the top of a coverage ranking (2026-08-06 M5 raster) were
#: 4.0% and 19.8%. 1% sits in the ~18x gap between those regimes; it is not a
#: measured optimum, and if a rig needs its own value it belongs beside
#: analysis_min_snr in safety_config.yaml (design/43 F6, block 43g).
MAX_SATURATED_FRACTION_FOR_COVERAGE = 0.01


def snr_validity(
    image: np.ndarray, background: float, saturated_fraction: float, min_snr: float,
) -> tuple[bool, bool, str | None]:
    """State the single validity rule for bright-signal SNR and focus.

    Clipping above 0.01% invalidates both SNR and focus: plateau edges are not an
    honest Tenengrad measurement. A statistically strong negative-going tail
    (at least ``min_snr`` and 1.5x the positive tail) identifies dark structure
    on a bright background without caller declarations; that invalidates only
    the bright-on-dark SNR, because Tenengrad is polarity-insensitive. Otherwise
    the existing positive-tail SNR gate decides whether focus has signal. The
    magnitude clause keeps symmetric empty-field noise out of the polarity case.
    """
    if saturated_fraction > MAX_SATURATED_FRACTION_FOR_SNR:
        return False, False, (
            f"SNR and focus metric are invalid because {saturated_fraction:.4%} "
            "of pixels are saturated (limit 0.0100%); reduce exposure and re-check "
            "after focus moves."
        )
    img = image.astype(np.float64)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    mad = float(np.median(np.abs(img - np.median(img))))
    noise = 1.4826 * mad
    if noise <= 0:
        return True, False, None
    positive_snr = (float(np.percentile(img, 99.5)) - background) / noise
    negative_snr = (background - float(np.percentile(img, 0.5))) / noise
    if negative_snr >= min_snr and negative_snr > 1.5 * positive_snr:
        return False, True, (
            "SNR is not scored because this frame has strong negative-going "
            "contrast (dark structure on a bright background); the reported SNR "
            "metric assumes bright signal above a dark background. Focus remains valid."
        )
    return True, positive_snr >= min_snr, None


def resolve_min_snr(
    *, explicit: float | None = None, configured: float | None = None
) -> tuple[float, str]:
    """Resolve the gate without reading configuration or global process state.

    Tool/hook boundaries supply the current rig setting. Keeping this function
    pure lets offline analysis choose and record the same precedence explicitly.
    Calibration artifacts are resolved by their caller and passed as ``explicit``.
    """
    if explicit is not None:
        return float(explicit), "explicit"
    if configured is not None:
        return float(configured), "rig_config"
    return UNCALIBRATED_MIN_SNR_FALLBACK, "package_default_uncalibrated"


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


def coverage_stats(
    image: np.ndarray, background: float, noise: float, min_snr: float,
) -> tuple[float, float, float]:
    """How much of the field has signal, and how evenly it is spread.

    ``snr`` answers "is the brightest thing here well above noise?" -- a tail
    statistic that one bright corner satisfies (design/43 F6). These answer
    "how much of this field is sample?", which is the question a survey asks.
    ``structure_coverage`` is deliberately blur-then-threshold: an out-of-focus
    cell is spread and dim, so it can fail a per-pixel test while still being
    obviously present (design/43 F5). It keeps the original background (the
    same value reported in ``ImageStats``) but re-estimates noise after the blur.
    Blurring suppresses camera noise, and retaining the unblurred noise estimate
    makes the threshold blind to exactly that diffuse structure. Re-estimating
    the blurred background as well would introduce a second background whose
    median can move into signal when sample fills most of the field.

    ``signal_concentration`` is the share of positive, above-background signal
    held by the brightest 1% of pixels. A value near one identifies the bright
    corner that can dominate SNR without filling the field. Its 1% window is
    matched to the 0.5% tail ``snr`` takes p99.5 over, which is the band where
    snr can be fooled at all -- so a bright region much larger than 1% of the
    frame reads progressively lower (measured: a corner at 0.9% of the frame
    reads 0.98, at 1.6% reads 0.63, at 3.5% reads 0.29). Read a low value as
    "not a small bright patch", never as "spread evenly".

    Shot noise makes the noise floor signal-dependent. The frame-wide MAD
    estimates noise at background; when sample fills much of the field, signal
    and its shot noise inflate the MAD and raise the threshold. Rig calibration
    must therefore cover both sparse and sample-filled fields. A real camera
    frame has a sensor pedestal and read noise, so MAD is not zero; this function
    does not claim to handle zero-padded mosaics, whose uncovered pixels corrupt
    the frame-wide background and noise estimates shared by all ``ImageStats``.
    """
    from scipy.ndimage import gaussian_filter

    img = image.astype(np.float64)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    threshold = float(background) + float(min_snr) * float(noise)
    signal_coverage = float(np.mean(img > threshold))
    blurred = gaussian_filter(img, sigma=2.0)
    blurred_noise = 1.4826 * float(
        np.median(np.abs(blurred - np.median(blurred)))
    )
    structure_coverage = float(np.mean(
        blurred > float(background) + float(min_snr) * blurred_noise
    ))

    positive_signal = np.maximum(img - float(background), 0.0).ravel()
    total_signal = float(np.sum(positive_signal))
    if total_signal <= 0:
        concentration = 0.0
    else:
        brightest_count = max(1, int(np.ceil(positive_signal.size * 0.01)))
        concentration = float(
            np.sum(np.partition(positive_signal, -brightest_count)[-brightest_count:])
            / total_signal
        )
    return signal_coverage, structure_coverage, concentration


def focus_invalid_warning(
    snr_value: float, min_snr: float = UNCALIBRATED_MIN_SNR_FALLBACK
) -> str:
    """The words a tool surfaces when the focus metric is not a measurement.

    Returning a bare focus_metric float on an empty field is what let the Nestor
    agent rank the emptiest tile on the grid as the sharpest (design/25). This
    tells the agent, in the payload it reads, that the number is not comparable.

    design/36 removed the inflation itself: an empty field now scores at its
    noise floor, below any field with structure, because the metric no longer
    divides by a contrast term that collapses when there is nothing there. The
    gate stays anyway — a field with no signal has no sharpness to report, and
    the number that comes back is a reading of the camera's noise, not of the
    sample. It is a validity flag now, not a trap door.
    """
    return (
        f"SNR {snr_value:.2f} < {min_snr} — no signal in this field, so it has no "
        f"sharpness to measure. This focus_metric is a reading of the camera "
        f"noise floor; do NOT compare it against fields that do have signal."
    )


def laplacian_variance(image: np.ndarray) -> float:
    """Raw Laplacian variance. Higher = sharper — but it also scales with
    photon count and with whatever happens to be in the crop, so it is NOT
    comparable across illumination/ROI/exposure changes. Prefer
    normalized_laplacian_variance for anything the model will compare."""
    from scipy.ndimage import laplace
    return float(np.var(laplace(image.astype(np.float64))))


def normalized_laplacian_variance(
    image: np.ndarray, background: float | None = None
) -> float:
    """DEPRECATED AS A FOCUS METRIC — it is minimised at focus on real frames.

    var(laplace(I - bg)) / mean(|I - bg|)². Kept exported because saved hooks
    may import it, and because the M5 and Nestor sweep curves are only readable
    against the function that produced them. Nothing in microclaw scores focus
    with it any more; use `tenengrad`.

    Why it inverts (design/36, reproduced offline):

      * The numerator is a bare Laplacian, the most noise-amplifying high-pass
        there is: white noise of std σ contributes a constant 20σ² to it. On any
        field whose detail is broader than a pixel, that constant dominates, so
        the numerator is pinned at the noise floor and carries no focus signal.
      * The denominator is a CONTRAST measure, and contrast falls with defocus:
        `bg` is the per-frame median, so as the sample blurs, the spread light
        lifts the median to meet it and mean(|I - bg|) collapses.

    A pinned numerator over a collapsing denominator is a metric that RISES with
    defocus. Both live sweeps show it: on M5 the curve bottomed out (1.95) at
    the operator's manual best focus and climbed to 25.9 twenty µm away.

    design/28 F2 declined to call this a sign bug, on the strength of a single
    noiseless, isolated, flux-conserving PSF — the one case where the numerator
    is not noise-limited, and the only case in which this function is correctly
    signed. Add a camera noise floor, or make the structure extended rather than
    point-like, and it inverts (`design/36-focus-metric-spike.py`).

    The original amr_test complaint (design/14 §10) — that raw Laplacian
    variance climbs 2.5× with laser power and 3.4× with an ROI crop at constant
    focus — was real. But no per-frame normalisation can fix it: separating the
    camera offset from out-of-focus haze is impossible from one frame, so every
    available normaliser is itself focus-dependent and inverts the metric.
    Comparability is a property of the comparison, not of the pixels, and it is
    declared instead — see `tenengrad` and the `metric_valid_for` block that
    tools report alongside it.
    """
    from scipy.ndimage import laplace

    img = image.astype(np.float64)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    bg = float(np.median(img)) if background is None else background
    sig = img - bg
    mean = float(np.mean(np.abs(sig)))
    if mean <= 0:
        return 0.0
    return float(np.var(laplace(sig)) / mean ** 2)


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
    noise = 1.4826 * float(np.median(np.abs(img - bg)))
    raw_snr = snr(image, background=bg)
    saturated_fraction = float(np.sum(image >= bit_max) / image.size)
    snr_valid, focus_metric_valid, snr_invalid_reason = snr_validity(
        image, bg, saturated_fraction, min_snr
    )
    reported_snr = round(raw_snr, 2) if snr_valid else None
    signal_coverage, structure_coverage, signal_concentration = coverage_stats(
        image, bg, noise, min_snr
    )
    return ImageStats(
        focus_metric=tenengrad(image),
        focus_metric_valid=focus_metric_valid,
        background_level=round(bg, 1),
        snr=reported_snr,
        snr_valid=snr_valid,
        snr_invalid_reason=snr_invalid_reason,
        mean_intensity=float(np.mean(image)),
        max_intensity=float(np.max(image)),
        min_intensity=float(np.min(image)),
        saturated_fraction=saturated_fraction,
        signal_coverage=signal_coverage,
        structure_coverage=structure_coverage,
        signal_concentration=signal_concentration,
    )


def connected_components(
    image: np.ndarray,
    pixel_size_um: float | None = None,
    origin_um: tuple[float, float] | list[float] | None = None,
    min_area_um2: float = 0.0,
    max_area_um2: float | None = None,
    min_snr: float = UNCALIBRATED_MIN_SNR_FALLBACK,
    *, basis_um=None, covered_mask=None, return_labels: bool = False,
) -> dict | tuple[dict, np.ndarray]:
    """Measure contiguous signal above the robust SNR floor in stage space.

    This deliberately stops at geometry: a component is contiguous thresholded
    signal, not a cell or any other biological classification.  Mosaic pixels
    equal to zero are uncovered canvas by default. Pass an explicit boolean
    covered_mask to select the observed population (all True for source frames).
    basis_um maps (column, row) to stage displacement; pixel_size_um preserves
    the scalar square-basis contract. A missing origin with a general basis
    reports pixel geometry and calibrated area without inventing stage XY.
    return_labels additionally returns the exact segmentation used for counting;
    callers select retained component ids from objects after area filtering.
    The ordinary dictionary result (including the scalar mosaic path) is unchanged.
    """
    from scipy import ndimage

    if basis_um is not None and pixel_size_um is not None:
        raise ValueError("Supply either basis_um or pixel_size_um, not both")
    if not isinstance(return_labels, bool):
        raise ValueError("return_labels must be a boolean")
    if basis_um is None:
        if pixel_size_um is None or not np.isfinite(pixel_size_um) or pixel_size_um <= 0:
            raise ValueError("pixel_size_um must be finite and positive")
    else:
        basis_um = np.asarray(basis_um, dtype=float)
        if basis_um.shape != (2, 2) or not np.all(np.isfinite(basis_um)):
            raise ValueError("basis_um must be a finite 2x2 stage basis")
        determinant = basis_um[0, 0] * basis_um[1, 1] - basis_um[0, 1] * basis_um[1, 0]
        if not np.isfinite(determinant) or determinant == 0:
            raise ValueError("basis_um must be nonsingular")
    if not np.isfinite(min_snr) or min_snr < 0:
        raise ValueError("min_snr must be finite and non-negative")
    if min_area_um2 < 0 or not np.isfinite(min_area_um2):
        raise ValueError("min_area_um2 must be finite and non-negative")
    if max_area_um2 is not None and (
        not np.isfinite(max_area_um2) or max_area_um2 < min_area_um2
    ):
        raise ValueError("max_area_um2 must be finite and at least min_area_um2")

    img = image.astype(np.float64)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    if img.ndim != 2:
        raise ValueError("connected_components requires a 2-D image")
    covered = img != 0 if covered_mask is None else np.asarray(covered_mask)
    if covered.shape != img.shape or covered.dtype != np.dtype(bool):
        raise ValueError("covered_mask must be a boolean array matching the image")
    values = img[covered]
    if values.size == 0:
        result = {"threshold": 0.0, "n_components": 0, "objects": []}
        return (result, np.zeros(img.shape, dtype=np.int32)) if return_labels else result
    background = float(np.median(values))
    # This cannot reuse snr()/snr_validity(): they intentionally measure the
    # full frame, while mosaic canvas zeros are not observations and must be
    # excluded from this adapter's background/noise population.
    noise = 1.4826 * float(np.median(np.abs(values - background)))
    threshold = background + float(min_snr) * noise
    labels, _ = ndimage.label(covered & (img > threshold))

    pixel_area = float(pixel_size_um) ** 2 if basis_um is None else abs(float(determinant))
    if origin_um is not None:
        if len(origin_um) != 2 or not all(np.isfinite(value) for value in origin_um):
            raise ValueError("origin_um must contain two finite stage coordinates")
        origin_x, origin_y = (float(origin_um[0]), float(origin_um[1]))
    elif basis_um is None:
        raise ValueError("origin_um must contain two finite stage coordinates")
    objects = []
    for label_id, bounds in enumerate(ndimage.find_objects(labels), start=1):
        if bounds is None:
            continue
        rows, cols = np.nonzero(labels == label_id)
        area_um2 = float(rows.size * pixel_area)
        if area_um2 < min_area_um2 or (
            max_area_um2 is not None and area_um2 > max_area_um2
        ):
            continue
        row_min, row_max = int(rows.min()), int(rows.max()) + 1
        col_min, col_max = int(cols.min()), int(cols.max()) + 1
        centroid_row, centroid_col = ndimage.center_of_mass(labels == label_id)
        if basis_um is not None:
            component = {
                "label": int(label_id), "n_pixels": int(rows.size),
                "area_um2": area_um2,
                "centroid_px": [float(centroid_col), float(centroid_row)],
                "bounding_box_px": [col_min, row_min, col_max, row_max],
            }
            if origin_um is not None:
                stage_x = origin_x + basis_um[0, 0] * cols + basis_um[0, 1] * rows
                stage_y = origin_y + basis_um[1, 0] * cols + basis_um[1, 1] * rows
                component["centroid_stage_um"] = [
                    origin_x + basis_um[0, 0] * float(centroid_col) + basis_um[0, 1] * float(centroid_row),
                    origin_y + basis_um[1, 0] * float(centroid_col) + basis_um[1, 1] * float(centroid_row),
                ]
                component["bounding_box_stage_hull_um"] = {
                    "x_min": float(stage_x.min()), "x_max": float(stage_x.max()),
                    "y_min": float(stage_y.min()), "y_max": float(stage_y.max()),
                }
            objects.append(component)
            continue
        objects.append({
            "label": int(label_id),
            "area_um2": area_um2,
            "centroid_stage_um": [
                origin_x + float(centroid_col) * pixel_size_um,
                origin_y + float(centroid_row) * pixel_size_um,
            ],
            "bounding_box_stage_um": {
                # Mosaic origin is the centre of pixel (0, 0); bounding boxes
                # describe the outer pixel edges in continuous stage space.
                "x_min": origin_x + (col_min - 0.5) * pixel_size_um,
                "y_min": origin_y + (row_min - 0.5) * pixel_size_um,
                "x_max": origin_x + (col_max - 0.5) * pixel_size_um,
                "y_max": origin_y + (row_max - 0.5) * pixel_size_um,
            },
            "bounding_box_px": [col_min, row_min, col_max, row_max],
        })
    result = {
        "threshold": threshold,
        "background_level": background,
        "noise_mad_sigma": noise,
        "n_components": len(objects),
        "objects": objects,
    }

    return (result, labels) if return_labels else result


def component_size_review(
    objects,
    pixel_area_um2: float,
    min_area_um2: float,
    *,
    annotation_ink_fraction: float | None = None,
) -> tuple[dict, list[str]]:
    """Disclose the size spread of a component count, and flag a count of noise.

    connected_components is geometrically right and, with no area filter, every
    noise excursion above the robust threshold is a component: measured on a
    real bead field, 224 components of which 204 were single pixels, against
    four objects.  The number is honest and useless as an answer, so the size
    distribution travels with it and a review note names the parameter that
    fixes it.  Information, never a refusal and never a status change -- small
    components can be real, and a zero count on an empty field is a result.
    """
    sizes = sorted(int(component["n_pixels"]) for component in objects)
    total = len(sizes)
    singles = sum(1 for size in sizes if size == 1)
    fraction = round(singles / total, 4) if total else 0.0
    distribution = {
        "n_components": total,
        "single_pixel_components": singles,
        "single_pixel_fraction": fraction,
        # Every component area is n_pixels x pixel_area_um2, so the spread is
        # reported once in pixels rather than repeated per component (R115).
        "n_pixels": {
            "min": sizes[0] if total else None,
            "median": float(np.median(sizes)) if total else None,
            "max": sizes[-1] if total else None,
        },
        "pixel_area_um2": float(pixel_area_um2),
    }
    notes: list[str] = []
    if total and singles * 2 > total:
        note = (
            f"{singles} of {total} counted components ({fraction:.0%}) are one pixel, the "
            "smallest a component can be: an area filter that admits single pixels counts "
            "every noise excursion above the threshold, so this number measures the noise "
            "floor as much as it measures objects. min_area_um2 is the smallest component "
            f"area counted; it is {min_area_um2:g} µm² here, which admits single pixels "
            f"({pixel_area_um2:.6g} µm² each). Set it to the smallest object area you mean "
            "to count and measure again. This is a review note about what the number "
            "counts, not an invalid count: small components can be real."
        )
        if annotation_ink_fraction is not None:
            note += (
                f" The annotation for this field marks {annotation_ink_fraction:.0%} of its "
                "pixels, so individual objects may not be separable in that evidence image."
            )
        notes.append(note)
    return distribution, notes


def image_content(
    payload: dict,
    image: np.ndarray,
    *,
    max_size: int = 512,
    mask: np.ndarray | None = None,
) -> list[dict]:
    """The text+image content block a tool returns when it renders pixels.

    One definition, so that "what a rendered result looks like" is decided in a
    single place. snap_and_analyze, run_autofocus and open_artifact are its
    callers; a fourth hand-built copy of this pair is a defect, not a
    convenience.
    """
    return [
        {"type": "text", "text": json.dumps(payload)},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": make_thumbnail(image, max_size=max_size, mask=mask),
            },
        },
    ]


def make_thumbnail(
    image: np.ndarray,
    max_size: int = 512,
    percentile_low: float = 2.0,
    percentile_high: float = 99.8,
    mask: np.ndarray | None = None,
) -> str:
    """Percentile-normalised, resized PNG thumbnail, returned as base64.

    A multichannel (H, W, C) image is reduced to luminance (channel mean) so the
    grayscale thumbnail path works regardless of camera type; the numerical
    metrics still see the full-resolution colour array upstream.

    `mask` restricts which pixels the percentile stretch is measured over, and
    must match the 2-D image the stretch sees. A stage-coordinate mosaic is
    mostly uncovered zeros — the one measured here was 65% — and stretching over
    all pixels puts the real signal in the bottom ~1% of the ramp, so what comes
    back is a black rectangle. The rendered image is still the whole array; only
    the black point and white point are measured from the masked pixels.
    """
    img = image.astype(np.float32)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    values = img if mask is None else img[mask]
    if values.size == 0:
        values = img          # an all-masked-out image: stretch over everything
    p_lo, p_hi = np.percentile(values, percentile_low), np.percentile(values, percentile_high)
    if p_hi > p_lo:
        img = (img - p_lo) / (p_hi - p_lo)
    img_8bit = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    pil_img = Image.fromarray(img_8bit, mode="L")
    pil_img.thumbnail((max_size, max_size), Image.LANCZOS)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def detect_features(
    image: np.ndarray,
    min_sigma: float = 1.0,
    max_sigma: float = 4.0,
    threshold_rel: float = 0.15,
) -> dict:
    """Blob-detect puncta and return both a field centroid and a centring target
    — numbers, not a picture (design/14 §9).

    In amr_test the model read the same field three ways across three snaps
    ("well-centered" / "just looks like noise" / "biased toward upper-right")
    because it was doing spatial statistics by eye on a thumbnail. This answers
    "is the feature centred?" deterministically and identically every time.

    **Two different questions, two different answers, and confusing them was a
    defect (design/64).** `centroid_xy_px` / `offset_from_center_px` are the
    intensity-weighted centre of mass of ALL positive signal: a field statistic,
    which in a frame with several objects, debris, or a gradient need not lie on
    any detected feature at all — and which is non-null even when `n_spots` is
    zero. `brightest_feature_xy_px` / `brightest_feature_offset_px` are the
    single brightest detected punctum, which is what "centre the feature" means
    and what `center_feature` steers by. They are None when nothing was
    detected, because positive background structure is not a motion target.

    The brightest blob is scored by background-subtracted signal inside its own
    LoG footprint (radius sigma·sqrt(2)), ties broken by (y, x) so the choice is
    deterministic on a symmetric field.
    """
    from skimage.feature import blob_log
    from scipy import ndimage

    img = image.astype(np.float32)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    bg = float(np.median(img))                 # camera offset (Evolve512 ≈ 400)
    sig = np.clip(img - bg, 0, None)
    peak = float(sig.max())
    h, w = sig.shape
    if peak <= 0:
        return {
            "n_spots": 0,
            "centroid_xy_px": None,
            "offset_from_center_px": None,
            "brightest_feature_xy_px": None,
            "brightest_feature_offset_px": None,
            "background_level": round(bg, 1),
            "snr": 0.0,
        }

    blobs = blob_log(
        sig / peak, min_sigma=min_sigma, max_sigma=max_sigma, threshold=threshold_rel
    )
    cy, cx = ndimage.center_of_mass(sig)
    off_x, off_y = cx - w / 2, cy - h / 2

    best = None
    for blob_y, blob_x, blob_sigma in blobs:
        radius = max(1.0, float(blob_sigma) * 2.0 ** 0.5)
        y0, y1 = max(0, int(blob_y - radius)), min(h, int(blob_y + radius) + 1)
        x0, x1 = max(0, int(blob_x - radius)), min(w, int(blob_x + radius) + 1)
        rows, cols = np.ogrid[y0:y1, x0:x1]
        footprint = (rows - blob_y) ** 2 + (cols - blob_x) ** 2 <= radius ** 2
        score = float(sig[y0:y1, x0:x1][footprint].sum())
        # Ties break by (y, x) so a symmetric field always picks the same blob.
        ranking = (-score, float(blob_y), float(blob_x))
        if best is None or ranking < best[0]:
            best = (ranking, float(blob_x), float(blob_y))
    brightest_xy = None if best is None else [round(best[1], 1), round(best[2], 1)]
    brightest_off = None if best is None else [
        round(best[1] - w / 2, 1), round(best[2] - h / 2, 1)
    ]

    return {
        "n_spots": int(len(blobs)),
        "centroid_xy_px": [round(float(cx), 1), round(float(cy), 1)],
        "offset_from_center_px": [round(float(off_x), 1), round(float(off_y), 1)],
        "brightest_feature_xy_px": brightest_xy,
        "brightest_feature_offset_px": brightest_off,
        "background_level": round(bg, 1),
        # The ONE snr definition (design/23 F7): (p99.5-bg)/(1.4826·MAD), NOT the
        # old peak/std. peak/std and this are different scales — leaving two
        # functions both named `snr` is the design/20 comparability failure.
        "snr": round(snr(img, background=bg), 2),
    }


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


def snap_to_numpy(ctrl, exposure_callback=None) -> np.ndarray:
    """Headless snap via the core's tagged image API — does NOT touch the viewer.

    Use this for sweeps (autofocus) where repainting the viewer 20 times is
    churn. For an image the user should see, use snap_to_numpy_displayed.
    Throws "sequence acquisition is running" if live view is on; wrap the call
    in tools._pause_live.
    """
    ctrl.core.snap_image()
    if exposure_callback is not None:
        exposure_callback()
    tagged = ctrl.core.get_tagged_image()
    w, h = int(tagged.tags["Width"]), int(tagged.tags["Height"])
    bpp = int(ctrl.core.get_bytes_per_pixel())
    n_comp = int(ctrl.core.get_number_of_components())
    return _reshape_pixels(tagged.pix, w, h, bpp, n_comp)


#: How long to wait for MM to construct the Preview window after a cold snap,
#: and how often to ask. Measured on the rig: it appeared within 1-30 ms
#: (design/18), so 2 s is pure headroom for a loaded EDT.
_DISPLAY_WAIT_S = 2.0
_DISPLAY_POLL_S = 0.02


def preview_window_open(ctrl) -> bool:
    """Whether MM currently has a snap/live Preview window.

    SnapLiveManager.getDisplay() is a pure accessor — it returns the window, or
    null if there is none or it has been closed, and never creates one. Java
    null arrives as None.

    This says a window EXISTS. It does not say your image is painted into it:
    design/18 caught a run with a non-null display still showing the "Waiting
    for Image..." placeholder. No MM API reports the canvas swap.
    """
    return ctrl.studio.live().get_display() is not None


def snap_to_numpy_displayed(ctrl) -> np.ndarray:
    """Snap via studio.live().snap(True): displays in the MM viewer AND returns
    the pixels — one exposure, not two (the sample bleaches).

    WARNING (design/14 V1): live().snap(True) called while live mode is ON does
    not throw — it never returns, and because pyjavaz holds one communication
    lock per port, the whole process wedges. The caller MUST stop live mode
    first (tools._pause_live); never probe by calling.

    The re-push (design/18): when no Preview window exists yet, snap(True)
    intermittently leaves MM's brand-new window on its "Waiting for Image..."
    placeholder — the biologist sees no image, and the agent used to claim they
    did. SnapLiveManager.displayImage() only queues work onto the Swing thread,
    so snap(True) can hand the pixels back before the window has finished
    constructing, and that window can miss the one new-image event it was sent.
    Pushing the image we ALREADY HOLD into the finished window repaints it and
    costs no exposure. Observed on the rig in roughly one cold snap in six; a
    wait-then-push repaired every stuck window it was tried on.
    """
    live = ctrl.studio.live()
    # Only the first snap of a session races. Read this BEFORE snapping: after
    # snap(True) the window exists either way, and the tell is gone.
    was_cold = live.get_display() is None

    images = live.snap(True)                         # java.util.ArrayList
    img = images.get(0)

    if was_cold:
        deadline = time.monotonic() + _DISPLAY_WAIT_S
        while live.get_display() is None and time.monotonic() < deadline:
            time.sleep(_DISPLAY_POLL_S)
        # Harmless if the window painted on its own; the fix when it did not.
        # (Re-pushing without the wait was never tested against a stuck window
        # — the bug refused to reproduce in six tries — so we do not rely on it.)
        live.display_image(img)

    w, h = int(img.get_width()), int(img.get_height())
    bpp = int(img.get_bytes_per_pixel())
    n_comp = int(img.get_num_components())           # NOT get_number_of_components
    return _reshape_pixels(img.get_raw_pixels(), w, h, bpp, n_comp)
