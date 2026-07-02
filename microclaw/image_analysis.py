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


def snap_to_numpy(ctrl) -> np.ndarray:
    """Snap and return a NumPy array via pycro-manager's tagged image API.

    Derives the dtype from the camera's bytes-per-pixel and component count
    rather than assuming 16-bit mono. Component count is decisive: an RGB32
    frame is 4×uint8 (BGRA), not 1×uint32, so multichannel frames return an
    (H, W, C) array of the per-component dtype.
    """
    ctrl.core.snap_image()
    tagged = ctrl.core.get_tagged_image()
    w, h = int(tagged.tags["Width"]), int(tagged.tags["Height"])
    bpp = int(ctrl.core.get_bytes_per_pixel())
    n_comp = int(ctrl.core.get_number_of_components())
    if n_comp > 1:                                   # e.g. RGB32 = 4 × uint8
        comp_dtype = {1: np.uint8, 2: np.uint16}.get(bpp // n_comp)
        if comp_dtype is None:
            raise ValueError(
                f"Unsupported bytes-per-component: {bpp}/{n_comp}"
            )
        return np.frombuffer(tagged.pix, dtype=comp_dtype).reshape(h, w, n_comp)
    dtype = {1: np.uint8, 2: np.uint16, 4: np.uint32}.get(bpp)
    if dtype is None:
        raise ValueError(f"Unsupported bytes-per-pixel: {bpp}")
    return np.frombuffer(tagged.pix, dtype=dtype).reshape(h, w)
