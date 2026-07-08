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
    """Laplacian variance focus metric. Higher = sharper."""
    from scipy.ndimage import laplace
    return float(np.var(laplace(image.astype(np.float64))))


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
