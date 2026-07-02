import base64
import io
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image

from microclaw.image_analysis import (
    compute_stats,
    laplacian_variance,
    make_thumbnail,
    snap_to_numpy,
)


def _tagged_ctrl(pix: bytes, w: int, h: int, bpp: int, n_comp: int):
    """Mock controller whose camera reports the given pixel geometry."""
    ctrl = MagicMock()
    tagged = MagicMock()
    tagged.pix = pix
    tagged.tags = {"Width": w, "Height": h}
    ctrl.core.get_tagged_image.return_value = tagged
    ctrl.core.get_bytes_per_pixel.return_value = bpp
    ctrl.core.get_number_of_components.return_value = n_comp
    return ctrl


class TestSnapToNumpy:
    def test_8bit_mono(self):
        h, w = 4, 5
        pix = np.arange(h * w, dtype=np.uint8).tobytes()
        ctrl = _tagged_ctrl(pix, w, h, bpp=1, n_comp=1)
        arr = snap_to_numpy(ctrl)
        assert arr.dtype == np.uint8
        assert arr.shape == (h, w)
        assert arr[0, 0] == 0 and arr[-1, -1] == h * w - 1

    def test_16bit_mono(self):
        h, w = 4, 5
        pix = np.arange(h * w, dtype=np.uint16).tobytes()
        ctrl = _tagged_ctrl(pix, w, h, bpp=2, n_comp=1)
        arr = snap_to_numpy(ctrl)
        assert arr.dtype == np.uint16
        assert arr.shape == (h, w)

    def test_rgb32(self):
        h, w = 3, 2
        pix = np.arange(h * w * 4, dtype=np.uint8).tobytes()
        ctrl = _tagged_ctrl(pix, w, h, bpp=4, n_comp=4)
        arr = snap_to_numpy(ctrl)
        assert arr.dtype == np.uint8
        assert arr.shape == (h, w, 4)

    def test_unsupported_bpp_raises(self):
        ctrl = _tagged_ctrl(b"\x00\x00\x00", 1, 1, bpp=3, n_comp=1)
        with pytest.raises(ValueError, match="bytes-per-pixel"):
            snap_to_numpy(ctrl)

    def test_8bit_saturation_reported(self):
        # The whole point of the fix: an all-255 8-bit frame is fully saturated,
        # which the old forced-uint16 path under-reported.
        h, w = 8, 8
        pix = np.full(h * w, 255, dtype=np.uint8).tobytes()
        ctrl = _tagged_ctrl(pix, w, h, bpp=1, n_comp=1)
        arr = snap_to_numpy(ctrl)
        assert compute_stats(arr).saturated_fraction == 1.0


def test_laplacian_variance_sharp_vs_blurry():
    from scipy.ndimage import gaussian_filter

    sharp = np.random.randint(0, 65535, (256, 256), dtype=np.uint16)
    blurry = gaussian_filter(sharp.astype(np.float32), sigma=5).astype(np.uint16)
    assert laplacian_variance(sharp) > laplacian_variance(blurry)


def test_make_thumbnail_returns_valid_png():
    img = np.random.randint(0, 65535, (512, 512), dtype=np.uint16)
    b64 = make_thumbnail(img, max_size=128)
    pil = Image.open(io.BytesIO(base64.standard_b64decode(b64)))
    assert max(pil.size) == 128


def test_thumbnail_preserves_aspect_ratio():
    img = np.zeros((1024, 2048), dtype=np.uint16)
    b64 = make_thumbnail(img, max_size=256)
    pil = Image.open(io.BytesIO(base64.standard_b64decode(b64)))
    assert max(pil.size) == 256


def test_compute_stats_saturated():
    img = np.full((64, 64), 65535, dtype=np.uint16)
    assert compute_stats(img).saturated_fraction == 1.0


def test_compute_stats_not_saturated():
    img = np.full((64, 64), 1000, dtype=np.uint16)
    stats = compute_stats(img)
    assert stats.saturated_fraction == 0.0
    assert stats.mean_intensity == pytest.approx(1000.0)


def test_make_thumbnail_flat_image():
    # Flat image: p_lo == p_hi, should not crash
    img = np.zeros((64, 64), dtype=np.uint16)
    b64 = make_thumbnail(img, max_size=32)
    assert len(b64) > 0


def test_make_thumbnail_multichannel():
    # (H, W, C) colour frame reduces to a grayscale thumbnail without crashing.
    img = np.random.randint(0, 255, (128, 128, 4), dtype=np.uint8)
    b64 = make_thumbnail(img, max_size=64)
    pil = Image.open(io.BytesIO(base64.standard_b64decode(b64)))
    assert pil.mode == "L"
    assert max(pil.size) == 64
