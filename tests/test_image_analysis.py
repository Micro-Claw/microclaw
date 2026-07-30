import base64
import io
import time
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image

from microclaw import image_analysis
from microclaw.image_analysis import (
    UNCALIBRATED_MIN_SNR_FALLBACK,
    compute_stats,
    detect_features,
    focus_invalid_warning,
    laplacian_variance,
    make_thumbnail,
    normalized_laplacian_variance,
    preview_window_open,
    resolve_min_snr,
    snap_to_numpy,
    snap_to_numpy_displayed,
    snr,
)


def synthetic_puncta(shape=(128, 128), spots=((40, 50), (80, 90), (20, 100)),
                     amp=5000.0, sigma=2.0, bg=400.0, read_noise=0.0, seed=0):
    """Sparse gaussian puncta on a camera-offset background (Evolve512-like).

    read_noise defaults to 0 (a perfectly flat background) for the deterministic
    detection tests. Pass read_noise>0 for anything that measures SNR: the shared
    snr() estimates the noise floor from the median absolute deviation, and a
    noiseless background has MAD 0 (a degenerate case that never occurs on a real
    detector), which pins SNR at 0 regardless of signal.
    """
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    img = np.full(shape, bg, dtype=np.float32)
    for y, x in spots:
        img += amp * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * sigma ** 2))
    if read_noise > 0:
        img = img + np.random.default_rng(seed).normal(0, read_noise, shape).astype(np.float32)
    return np.clip(img, 0, None).astype(np.uint16)


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


#: Sentinel for a non-null DisplayWindow. get_display() returns None for Java
#: null, so anything else at all means "a Preview window exists".
_WINDOW = object()


def _studio_ctrl(pixels: np.ndarray, n_comp: int = 1, displays=(_WINDOW,)):
    """Mock controller whose studio.live().snap(True) yields one image with the
    (V1-verified) accessors: get_width/get_height/get_bytes_per_pixel/
    get_num_components/get_raw_pixels — raw pixels arrive as a numpy array.

    `displays` is the sequence get_display() returns, one per call, the last
    value repeating. Default is a warm rig (a Preview window already exists).
    Pass (None, ..., _WINDOW) for the design/18 cold snap.
    """
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
    live = ctrl.studio.live()
    live.snap.return_value = images

    remaining = list(displays)
    calls: list[str] = []          # interleaved log, so ordering is assertable

    def _get_display():
        value = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        calls.append("get_display->" + ("None" if value is None else "window"))
        return value

    live.get_display.side_effect = _get_display
    live.display_image.side_effect = lambda im: calls.append("display_image")
    ctrl._img = img
    ctrl._calls = calls
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


class TestFirstSnapRepush:
    """design/18: a cold snap(True) intermittently leaves MM's brand-new Preview
    window on its "Waiting for Image..." placeholder. Re-pushing the image we
    already hold repaints it, at no exposure."""

    PIXELS = np.arange(20, dtype=np.uint16).reshape(4, 5)

    def test_warm_snap_does_not_repush(self):
        # A window already exists: snap(True) paints it, as it always has.
        # Re-pushing every snap would be pointless bridge traffic.
        ctrl = _studio_ctrl(self.PIXELS, displays=(_WINDOW,))
        snap_to_numpy_displayed(ctrl)
        ctrl.studio.live().display_image.assert_not_called()

    def test_cold_snap_repushes_the_held_image(self):
        ctrl = _studio_ctrl(self.PIXELS, displays=(None, _WINDOW))
        snap_to_numpy_displayed(ctrl)
        # The SAME image object — a second snap() would be a second exposure.
        ctrl.studio.live().display_image.assert_called_once_with(ctrl._img)
        ctrl.studio.live().snap.assert_called_once_with(True)

    def test_cold_snap_waits_for_the_window_before_repushing(self):
        # The one variant with rig evidence behind it: push AFTER the window
        # exists. Pushing immediately was never tested against a stuck window.
        ctrl = _studio_ctrl(self.PIXELS, displays=(None, None, None, _WINDOW))
        snap_to_numpy_displayed(ctrl)
        assert ctrl._calls == [
            "get_display->None",        # the pre-snap cold read
            "get_display->None",        # polling: window not up yet
            "get_display->None",
            "get_display->window",      # window appeared
            "display_image",            # ...only now do we push
        ]

    def test_cold_snap_repushes_even_if_the_window_never_appears(self, monkeypatch):
        # Bounded wait: a window that never arrives must not hang a snap. We
        # push anyway — displayImage() creates the display if there is none,
        # which is exactly what snap(True) would have done.
        monkeypatch.setattr(image_analysis, "_DISPLAY_WAIT_S", 0.05)
        monkeypatch.setattr(image_analysis, "_DISPLAY_POLL_S", 0.01)
        ctrl = _studio_ctrl(self.PIXELS, displays=(None,))
        started = time.monotonic()
        arr = snap_to_numpy_displayed(ctrl)
        elapsed = time.monotonic() - started
        ctrl.studio.live().display_image.assert_called_once_with(ctrl._img)
        assert elapsed < 1.0, "the wait must be bounded"
        np.testing.assert_array_equal(arr, self.PIXELS)

    def test_pixels_survive_the_repush(self):
        # The re-push must not disturb what we return to the caller.
        ctrl = _studio_ctrl(self.PIXELS, displays=(None, _WINDOW))
        np.testing.assert_array_equal(snap_to_numpy_displayed(ctrl), self.PIXELS)


class TestPreviewWindowOpen:
    def test_true_when_a_window_exists(self):
        ctrl = MagicMock()
        ctrl.studio.live().get_display.return_value = _WINDOW
        assert preview_window_open(ctrl) is True

    def test_false_on_java_null(self):
        # pyjavaz maps Java null to None (verified on the rig, design/18).
        ctrl = MagicMock()
        ctrl.studio.live().get_display.return_value = None
        assert preview_window_open(ctrl) is False


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


class TestHintForError:
    """Every non-safety error used to carry the same "may be a hardware error"
    hint. On the 2026-07-10 run that hint decorated a missing log directory,
    pointing the model at the stage while the fault was a filesystem path."""

    def test_a_hook_writing_to_a_missing_directory_is_not_called_hardware(self):
        # Verbatim from the run: pycro-manager re-raises the hook's
        # FileNotFoundError as a bare Exception, so the class is gone.
        from microclaw.errors import hint_for_error
        exc = Exception(
            "exception in image processor: [Errno 2] No such file or "
            r"directory: 'C:\Users\rieslab\.microclaw\logs\pixel_std_grid.json'"
        )
        hint = hint_for_error(exc)
        assert "hardware" not in hint
        assert "path" in hint
        # The acquisition already ran; the model must not read this as a no-op.
        assert "partial dataset" in hint

    def test_a_real_filenotfounderror_is_recognised_by_type(self):
        from microclaw.errors import hint_for_error
        hint = hint_for_error(FileNotFoundError(2, "No such file or directory"))
        assert "path does not exist" in hint
        assert "hardware" not in hint

    def test_bad_tool_arguments_say_so(self):
        from microclaw.errors import hint_for_error
        hint = hint_for_error(TypeError("unexpected keyword argument 'centre_x_um'"))
        assert "argument error" in hint
        assert "not a hardware fault" in hint

    def test_a_dead_bridge_points_at_micro_manager(self):
        from microclaw.errors import hint_for_error
        assert "port 4827" in hint_for_error(ConnectionError("connection refused"))

    def test_rig_authorization_refusal_is_not_called_hardware(self):
        from microclaw.authorization import RigAuthorizationError
        from microclaw.errors import _HARDWARE_HINT, hint_for_error
        hint = hint_for_error(RigAuthorizationError("operator declined"))
        assert hint != _HARDWARE_HINT
        assert "authorization decision" in hint
        assert "Retrying the identical call will fail identically" in hint

    def test_partial_channel_plan_points_at_the_recorded_pair_lists(self):
        from microclaw.authorization import ChannelPlanPartialApplicationError
        from microclaw.errors import hint_for_error
        hint = hint_for_error(ChannelPlanPartialApplicationError("applied=[]"))
        assert "applied, attempted, and rolled-back" in hint
        assert "do not blindly retry" in hint

    def test_failed_channel_plan_rollback_requires_operator_attention(self):
        from microclaw.authorization import ChannelPlanSafeStateError
        from microclaw.errors import hint_for_error
        hint = hint_for_error(ChannelPlanSafeStateError("SAFE STATE NOT VERIFIED"))
        assert "state is unverified" in hint
        assert "surface this error to the operator" in hint
        assert "do not continue" in hint

    def test_an_unrecognised_error_still_gets_the_hardware_hint(self):
        from microclaw.errors import hint_for_error
        hint = hint_for_error(Exception("Stage XY reported an unknown fault"))
        assert "hardware error" in hint


def test_laplacian_variance_sharp_vs_blurry():
    from scipy.ndimage import gaussian_filter

    sharp = np.random.randint(0, 65535, (256, 256), dtype=np.uint16)
    blurry = gaussian_filter(sharp.astype(np.float32), sigma=5).astype(np.uint16)
    assert laplacian_variance(sharp) > laplacian_variance(blurry)


class TestNormalizedLaplacianVariance:
    def test_invariant_to_illumination_scaling(self):
        """The amr_test regression (§10): 5× brighter must not read as sharper."""
        img = synthetic_puncta(bg=400).astype(np.float64)
        bright = (img - 400) * 5 + 400
        assert normalized_laplacian_variance(bright) == pytest.approx(
            normalized_laplacian_variance(img), rel=0.05
        )

    def test_raw_metric_is_not_invariant(self):
        # Documents WHY the normalized variant exists.
        img = synthetic_puncta(bg=400).astype(np.float64)
        bright = (img - 400) * 5 + 400
        assert laplacian_variance(bright) > 5 * laplacian_variance(img)

    def test_sharp_beats_blurry(self):
        from scipy.ndimage import gaussian_filter
        rng = np.random.default_rng(3)
        sharp = (rng.random((256, 256)) * 60000).astype(np.float32)
        blurry = gaussian_filter(sharp, sigma=5)
        assert normalized_laplacian_variance(sharp) > normalized_laplacian_variance(blurry)

    def test_bright_punctum_on_dark_background_peaks_at_focus(self):
        """design/28 F2 correction: Laplacian variance is polarity-insensitive.

        A flux-conserving fluorescent PSF must score highest when it is tightest,
        not invert merely because the emitter is bright on a dark background.
        """
        sigmas = [1.5, 2.0, 3.0, 4.0, 6.0]
        images = [
            synthetic_puncta(
                spots=((64, 64),), amp=8000.0 / sigma**2, sigma=sigma,
                bg=400.0, read_noise=0.0,
            )
            for sigma in sigmas
        ]
        scores = [normalized_laplacian_variance(image) for image in images]
        assert scores[0] == max(scores)
        assert scores == sorted(scores, reverse=True)

    def test_flat_image_is_zero(self):
        assert normalized_laplacian_variance(np.zeros((64, 64))) == 0.0
        assert normalized_laplacian_variance(np.full((64, 64), 400.0)) == 0.0


class TestSnr:
    """design/23 F7: one robust snr() definition, shared by compute_stats and
    detect_features. (p99.5 - bg) / (1.4826 * MAD)."""

    def test_empty_field_is_pinned_low(self):
        # Pure read noise: SNR is an amplitude-independent constant near 2.58
        # (design/25 check 4), well below the fallback so the gate catches it.
        rng = np.random.default_rng(0)
        empty = (400 + rng.normal(0, 10, (256, 256))).astype(np.uint16)
        assert snr(empty) < UNCALIBRATED_MIN_SNR_FALLBACK
        # Amplitude-independent: doubling the noise does not change the ratio.
        louder = (400 + rng.normal(0, 40, (256, 256))).astype(np.uint16)
        assert snr(louder) == pytest.approx(snr(empty), abs=0.6)

    def test_cell_is_well_above_the_floor(self):
        img = synthetic_puncta(read_noise=8.0)
        assert snr(img) > UNCALIBRATED_MIN_SNR_FALLBACK

    def test_flat_frame_is_zero_not_infinite(self):
        # MAD 0 (a degenerate synthetic case): guarded, not a divide-by-zero.
        assert snr(np.full((64, 64), 400.0)) == 0.0

    def test_one_hot_pixel_is_not_signal(self):
        # A percentile, not max(): a single hot pixel must not read as signal.
        rng = np.random.default_rng(1)
        frame = (400 + rng.normal(0, 10, (256, 256))).astype(np.float64)
        frame[100, 100] = 60000
        assert snr(frame) < UNCALIBRATED_MIN_SNR_FALLBACK

    def test_detect_features_uses_the_shared_definition(self):
        img = synthetic_puncta(read_noise=8.0)
        bg = float(np.median(img.astype(np.float64)))
        assert detect_features(img)["snr"] == pytest.approx(
            round(snr(img.astype(np.float32), background=bg), 2), abs=0.1
        )

    def test_gate_resolution_is_pure_and_has_explicit_precedence(self):
        assert resolve_min_snr(explicit=8, configured=7) == (8.0, "explicit")
        assert resolve_min_snr(configured=7) == (7.0, "rig_config")
        assert resolve_min_snr() == (
            UNCALIBRATED_MIN_SNR_FALLBACK, "package_default_uncalibrated"
        )


class TestFocusMetricGate:
    """design/25: on an empty field the focus metric inflates, so it is gated on
    SNR. compute_stats reports focus_metric_valid False when there is no signal."""

    def test_empty_field_is_invalid(self):
        rng = np.random.default_rng(0)
        empty = (400 + rng.normal(0, 10, (256, 256))).astype(np.uint16)
        stats = compute_stats(empty)
        assert stats.focus_metric_valid is False
        assert stats.snr < UNCALIBRATED_MIN_SNR_FALLBACK

    def test_cell_is_valid(self):
        stats = compute_stats(synthetic_puncta(read_noise=8.0))
        assert stats.focus_metric_valid is True
        assert stats.snr >= UNCALIBRATED_MIN_SNR_FALLBACK

    def test_empty_field_outranks_a_cell_on_the_raw_metric(self):
        # The bug (design/25): the empty field's normalized metric is HIGHER than
        # the cell's, so ranking by focus_metric alone picks the empty tile. The
        # validity flag is the only thing that distinguishes them.
        rng = np.random.default_rng(0)
        empty = (400 + rng.normal(0, 10, (256, 256))).astype(np.uint16)
        cell = synthetic_puncta(shape=(256, 256), read_noise=8.0)
        assert compute_stats(empty).focus_metric > compute_stats(cell).focus_metric
        assert compute_stats(empty).focus_metric_valid is False
        assert compute_stats(cell).focus_metric_valid is True

    def test_background_level_is_reported(self):
        stats = compute_stats(np.full((64, 64), 400, dtype=np.uint16))
        assert stats.background_level == pytest.approx(400.0, abs=1)

    def test_warning_names_the_snr_and_the_hazard(self):
        msg = focus_invalid_warning(2.4)
        assert "2.4" in msg and "inflate" in msg


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
        # read_noise>0 so the noise floor (MAD) is well-defined — SNR needs it.
        img = synthetic_puncta(read_noise=8.0)
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
