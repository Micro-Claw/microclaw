from __future__ import annotations
import base64
import io
import time
from typing import NamedTuple

import numpy as np
from PIL import Image


class ImageStats(NamedTuple):
    focus_metric: float          # normalized_laplacian_variance — the number tools report
    focus_metric_valid: bool     # False when snr < min_snr: no signal to be sharp about
    background_level: float      # robust: median (the camera offset, Evolve512 ≈ 400)
    snr: float                   # the shared snr() definition below
    mean_intensity: float
    max_intensity: float
    min_intensity: float
    saturated_fraction: float


#: Below this SNR, "how sharp is this field?" has no answer, because there is
#: nothing in the field to be sharp (design/25). An empty field is pinned at
#: SNR ≈ 2.58 by snr() below, and synthetic cells read 11–84, so 3.0 sits just
#: above the empty floor with room. STILL A PLACEHOLDER: design/23 F7 is explicit
#: that it must be measured on real frames against snr(), and it plausibly varies
#: by objective and by sample. If it needs per-rig values it belongs in
#: safety_config.yaml alongside the other rig facts.
UNCALIBRATED_MIN_SNR_FALLBACK = 3.1


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


def focus_invalid_warning(
    snr_value: float, min_snr: float = UNCALIBRATED_MIN_SNR_FALLBACK
) -> str:
    """The words a tool surfaces when the focus metric is not a measurement.

    Returning a bare focus_metric float on an empty field is what let the Nestor
    agent rank the emptiest tile on the grid as the sharpest (design/25). This
    tells the agent, in the payload it reads, that the number is not comparable.
    """
    return (
        f"SNR {snr_value:.2f} < {min_snr} — no signal in this field, so it has no "
        f"sharpness to measure. Do NOT compare this focus_metric against other "
        f"fields; an empty field inflates the metric rather than deflating it."
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
    """var(laplace(I - bg)) / mean(|I - bg|)² — scale-free w.r.t. illumination.

    Raw var(laplace(I)) fired both of its failure modes in amr_test at
    CONSTANT focus (design/14 §10):

        full frame, 1% laser  →  8602
        full frame, 10% laser → 21142     2.5× from laser power alone
        200×200 ROI, 5% laser → 29107     3.4× from cropping alone

    and the model read the rising number as improving image quality. The
    camera offset (background) is subtracted first — the median unless given.
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


def compute_stats(
    image: np.ndarray,
    min_snr: float = UNCALIBRATED_MIN_SNR_FALLBACK,
) -> ImageStats:
    """Per-image statistics, including the NORMALIZED focus metric and its gate.

    focus_metric is now normalized_laplacian_variance, not the raw
    laplacian_variance — both tool payload sites already overrode the old raw
    value with the normalized one (design/14 §10) because the raw number was not
    comparable, so computing it here means one computation, one validity flag, and
    no dead field to mistake for the live one (design/23 F7). The raw
    laplacian_variance stays exported for anyone who wants it.

    focus_metric_valid is False below min_snr: an empty field inflates the metric
    rather than deflating it (design/25), so its "sharpness" is not a measurement.
    """
    bit_max = float(np.iinfo(image.dtype).max) if np.issubdtype(image.dtype, np.integer) else 1.0
    img = image.astype(np.float64)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    bg = float(np.median(img))           # computed once, shared by snr and the metric
    s = snr(image, background=bg)
    return ImageStats(
        focus_metric=normalized_laplacian_variance(image, background=bg),
        focus_metric_valid=s >= min_snr,
        background_level=round(bg, 1),
        snr=round(s, 2),
        mean_intensity=float(np.mean(image)),
        max_intensity=float(np.max(image)),
        min_intensity=float(np.min(image)),
        saturated_fraction=float(np.sum(image >= bit_max) / image.size),
    )


def make_thumbnail(
    image: np.ndarray,
    max_size: int = 512,
    percentile_low: float = 2.0,
    percentile_high: float = 99.8,
) -> str:
    """Percentile-normalised, resized PNG thumbnail, returned as base64.

    A multichannel (H, W, C) image is reduced to luminance (channel mean) so the
    grayscale thumbnail path works regardless of camera type; the numerical
    metrics still see the full-resolution colour array upstream.
    """
    img = image.astype(np.float32)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    p_lo, p_hi = np.percentile(img, percentile_low), np.percentile(img, percentile_high)
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
    """Blob-detect puncta and return an intensity-weighted centroid — numbers,
    not a picture (design/14 §9).

    In amr_test the model read the same field three ways across three snaps
    ("well-centered" / "just looks like noise" / "biased toward upper-right")
    because it was doing spatial statistics by eye on a thumbnail. This answers
    "is the feature centred?" deterministically and identically every time.
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
            "background_level": round(bg, 1),
            "snr": 0.0,
        }

    blobs = blob_log(
        sig / peak, min_sigma=min_sigma, max_sigma=max_sigma, threshold=threshold_rel
    )
    cy, cx = ndimage.center_of_mass(sig)
    off_x, off_y = cx - w / 2, cy - h / 2
    return {
        "n_spots": int(len(blobs)),
        "centroid_xy_px": [round(float(cx), 1), round(float(cy), 1)],
        "offset_from_center_px": [round(float(off_x), 1), round(float(off_y), 1)],
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
