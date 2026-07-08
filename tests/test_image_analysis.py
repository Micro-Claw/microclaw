import base64
import io
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image

from microclaw.image_analysis import (
    compute_stats,
    detect_features,
    laplacian_variance,
    make_thumbnail,
    snap_to_numpy,
    snap_to_numpy_displayed,
)


def synthetic_puncta(shape=(128, 128), spots=((40, 50), (80, 90), (20, 100)),
                     amp=5000.0, sigma=2.0, bg=400.0):
    """Sparse gaussian puncta on a camera-offset background (Evolve512-like)."""
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    img = np.full(shape, bg, dtype=np.float32)
    for y, x in spots:
        img += amp * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * sigma ** 2))
    return img.astype(np.uint16)


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


def _studio_ctrl(pixels: np.ndarray, n_comp: int = 1):
    """Mock controller whose studio.live().snap(True) yields one image with the
    (V1-verified) accessors: get_width/get_height/get_bytes_per_pixel/
    get_num_components/get_raw_pixels — raw pixels arrive as a numpy array."""
    ctrl = MagicMock()
    img = MagicMock()
    h, w = pixels.shape[:2]
    img.get_width.return_value = w
    img.get_height.return_value = h
    img.get_bytes_per_pixel.return_value = pixels.dtype.itemsize * n_comp
    img.get_num_components.return_value = n_comp
    img.get_raw_pixels.return_value = pixels.ravel()
    images = MagicMock()
    images.get.return_value = img
    ctrl.studio.live().snap.return_value = images
    return ctrl


class TestSnapToNumpyDisplayed:
    def test_16bit_mono(self):
        pixels = np.arange(20, dtype=np.uint16).reshape(4, 5)
        ctrl = _studio_ctrl(pixels)
        arr = snap_to_numpy_displayed(ctrl)
        ctrl.studio.live().snap.assert_called_once_with(True)
        assert arr.dtype == np.uint16
        assert arr.shape == (4, 5)
        np.testing.assert_array_equal(arr, pixels)

    def test_same_shape_as_core_path(self):
        # The displayed path and the headless path must agree on geometry.
        pixels = np.random.randint(0, 65535, (8, 8), dtype=np.uint16)
        displayed = snap_to_numpy_displayed(_studio_ctrl(pixels))
        assert displayed.shape == pixels.shape


def test_humanize_java_error_translates_sequence_acquisition():
    from microclaw.errors import humanize_java_error
    exc = Exception(
        "java.lang.Exception: This operation can not be executed while "
        "sequence acquisition is running.\n  mmcorej.MMCoreJJNI.CMMCore_snapImage(...)\n"
        + "\n".join(f"  at frame{i}" for i in range(14))
    )
    msg = humanize_java_error(exc)
    assert "Live view" in msg
    assert "\n" not in msg  # 16-line stack trace never reaches the model


def test_humanize_java_error_trims_unknown_to_first_line():
    from microclaw.errors import humanize_java_error
    msg = humanize_java_error(Exception("boom\n  at deep.stack.frame"))
    assert msg == "boom"


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


class TestDetectFeatures:
    def test_counts_sparse_puncta(self):
        img = synthetic_puncta()
        result = detect_features(img)
        assert result["n_spots"] == 3
        assert result["background_level"] == pytest.approx(400.0, abs=5)
        assert result["snr"] > 3

    def test_centroid_matches_single_spot(self):
        img = synthetic_puncta(spots=((90, 30),))
        result = detect_features(img)
        cx, cy = result["centroid_xy_px"]
        assert cx == pytest.approx(30, abs=2)
        assert cy == pytest.approx(90, abs=2)

    def test_offset_from_center_is_signed(self):
        # Spot in the lower-left quadrant of a 128x128 field: x < 64, y > 64.
        img = synthetic_puncta(spots=((100, 20),))
        off_x, off_y = detect_features(img)["offset_from_center_px"]
        assert off_x == pytest.approx(20 - 64, abs=2)
        assert off_y == pytest.approx(100 - 64, abs=2)

    def test_centered_spot_has_near_zero_offset(self):
        img = synthetic_puncta(spots=((64, 64),))
        off_x, off_y = detect_features(img)["offset_from_center_px"]
        assert abs(off_x) < 2 and abs(off_y) < 2

    def test_empty_field_reports_no_signal(self):
        img = np.full((64, 64), 400, dtype=np.uint16)
        result = detect_features(img)
        assert result["n_spots"] == 0
        assert result["centroid_xy_px"] is None
        assert result["offset_from_center_px"] is None

    def test_deterministic_across_calls(self):
        # The whole point: the same field must read the same way every time
        # (amr_test read one field three contradictory ways).
        img = synthetic_puncta()
        assert detect_features(img) == detect_features(img)


def test_make_thumbnail_multichannel():
    # (H, W, C) colour frame reduces to a grayscale thumbnail without crashing.
    img = np.random.randint(0, 255, (128, 128, 4), dtype=np.uint8)
    b64 = make_thumbnail(img, max_size=64)
    pil = Image.open(io.BytesIO(base64.standard_b64decode(b64)))
    assert pil.mode == "L"
    assert max(pil.size) == 64
