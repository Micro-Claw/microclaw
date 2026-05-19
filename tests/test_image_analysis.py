import base64
import io

import numpy as np
import pytest
from PIL import Image

from microclaw.image_analysis import compute_stats, laplacian_variance, make_thumbnail


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
