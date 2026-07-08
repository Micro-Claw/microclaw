from __future__ import annotations
import base64
import io
from typing import NamedTuple

import numpy as np
from PIL import Image


class ImageStats(NamedTuple):
    focus_metric: float
    mean_intensity: float
    max_intensity: float
    min_intensity: float
    saturated_fraction: float


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


def compute_stats(image: np.ndarray) -> ImageStats:
    bit_max = float(np.iinfo(image.dtype).max) if np.issubdtype(image.dtype, np.integer) else 1.0
    return ImageStats(
        focus_metric=laplacian_variance(image),
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
        "snr": round(peak / (float(sig.std()) or 1.0), 2),
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


def snap_to_numpy_displayed(ctrl) -> np.ndarray:
    """Snap via studio.live().snap(True): displays in the MM viewer AND returns
    the pixels — one exposure, not two (the sample bleaches).

    WARNING (design/14 V1): live().snap(True) called while live mode is ON does
    not throw — it never returns, and because pyjavaz holds one communication
    lock per port, the whole process wedges. The caller MUST stop live mode
    first (tools._pause_live); never probe by calling.
    """
    images = ctrl.studio.live().snap(True)           # java.util.ArrayList
    img = images.get(0)
    w, h = int(img.get_width()), int(img.get_height())
    bpp = int(img.get_bytes_per_pixel())
    n_comp = int(img.get_num_components())           # NOT get_number_of_components
    return _reshape_pixels(img.get_raw_pixels(), w, h, bpp, n_comp)
