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
    tenengrad,
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


def simulated_camera_frame(
    photons, *, seed=0, ad_offset=100.0, read_noise_e=1.5,
    electrons_per_count=0.5, qe=0.8, background_photons=1.0,
):
    """Generate a simple digital camera frame.

    The model applies photon shot noise, Gaussian sensor read noise, and a
    linear analogue-to-digital conversion.  It is intentionally only as
    detailed as these image-analysis tests require.
    """
    if not 0 <= qe <= 1:
        raise ValueError("qe must be between 0 and 1")
    if electrons_per_count <= 0:
        raise ValueError("electrons_per_count must be positive")
    if read_noise_e < 0 or background_photons < 0:
        raise ValueError("noise and background values must be non-negative")

    rng = np.random.default_rng(seed)
    photons = np.asarray(photons, dtype=np.float64)
    if np.any(photons < 0):
        raise ValueError("photons must be non-negative")

    mean_electrons = qe * (photons + background_photons)
    measured_electrons = rng.poisson(mean_electrons).astype(np.float64)
    measured_electrons += rng.normal(0.0, read_noise_e, photons.shape)
    counts = ad_offset + measured_electrons / electrons_per_count
    return np.clip(np.rint(counts), 0, np.iinfo(np.uint16).max).astype(np.uint16)


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

    def test_a_missing_lookup_name_is_not_called_hardware(self):
        from microclaw.errors import hint_for_error
        hint = hint_for_error(KeyError("No adapter named 'missing'"))
        assert "lookup error" in hint
        assert "not a hardware fault" in hint

    def test_a_dataset_path_that_is_a_file_says_so(self):
        """Block 43e's M5 gate, first call of the session: the agent passed the
        mosaic .tif as dataset_path and got the hardware hint on a tool that
        touches no hardware. NotADirectoryError is an OSError sibling, not a
        FileNotFoundError subclass, so it fell past the path branch."""
        from microclaw.errors import _HARDWARE_HINT, hint_for_error
        hint = hint_for_error(NotADirectoryError(20, "The directory name is invalid"))
        assert hint != _HARDWARE_HINT
        assert "hardware" not in hint
        assert "NDTiff dataset IS a directory" in hint

    def test_a_windows_directory_name_message_is_recognised_without_the_type(self):
        # WinError 267 reaches us through pycro-manager as a bare Exception on
        # some paths, exactly as the FileNotFoundError case above does.
        from microclaw.errors import _HARDWARE_HINT, hint_for_error
        hint = hint_for_error(Exception("[WinError 267] The directory name is invalid: 'x.tif'"))
        assert hint != _HARDWARE_HINT
        assert "directory" in hint

    def test_an_existing_output_directory_is_not_called_hardware(self):
        """Same gate, second call: offline analysis refuses to reuse an
        output_dir so a previous analysis is never overwritten. That is a
        deliberate refusal and must read as one."""
        from microclaw.errors import _HARDWARE_HINT, hint_for_error
        hint = hint_for_error(FileExistsError(17, "Cannot create a file when that file already exists"))
        assert hint != _HARDWARE_HINT
        assert "hardware" not in hint
        assert "output_dir" in hint

    def test_path_message_wins_over_keyerror_type(self):
        from microclaw.errors import hint_for_error
        hint = hint_for_error(KeyError("No such file or directory: adapter.json"))
        assert "path does not exist" in hint
        assert "lookup error" not in hint

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


def defocus_series(kind, gain=1.0, defocus=(0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0),
                   seed=11, shape=(284, 320), offset=180.0):
    """A Z sweep as the camera sees it: blur the SCENE, then detect it.

    Order matters, and it is the whole reason design/28 F2 reached the wrong
    conclusion. Blurring an already-noisy frame smooths the noise away with the
    signal, which flatters any high-pass metric. On a real rig, defocus happens
    in the optics and the shot/read noise is added afterwards by the detector,
    at a floor that does NOT blur — so a noise-amplifying metric stays pinned to
    that floor while the signal it is meant to measure disappears underneath it.

    Flux-conserving: `gaussian_filter` preserves the sum, so these frames differ
    only in how the same photons are distributed. Anything that reads them as
    "dimmer when defocused" is reading its own normaliser, not the sample.
    """
    from scipy.ndimage import gaussian_filter

    rng = np.random.default_rng(seed)
    if kind == "puncta":                       # sparse emitters on a dark field
        scene = np.zeros(shape)
        ys = rng.integers(8, shape[0] - 8, 140)
        xs = rng.integers(8, shape[1] - 8, 140)
        for y, x in zip(ys, xs):
            scene[y, x] += 9000.0
        scene = gaussian_filter(scene, 1.2)    # in-focus PSF
    elif kind == "extended":                   # cell-like structure, all lit
        scene = gaussian_filter(rng.normal(0, 1, shape), 6)
        scene = gaussian_filter(np.clip(scene, 0, None) * 3000, 1.2)
    else:
        raise ValueError(kind)

    frames = []
    for d in defocus:
        blurred = gaussian_filter(scene, d) if d > 0 else scene
        detected = rng.poisson((blurred + 2.0) * gain).astype(np.float64)
        frames.append(detected + offset + rng.normal(0, 2.0, shape))
    return frames


FIELD_TYPES = [
    ("puncta", 1.0), ("puncta", 0.1), ("extended", 1.0), ("extended", 0.05),
]


class TestFocusMetricPeaksAtFocus:
    """design/36 — the M5 session (2026-08-04) and the Nestor session before it.

    Both swept Z, both got a curve that was LOWEST at the operator's own manual
    best focus and climbed monotonically toward the sweep edges, and both
    therefore reported no convergence and refused to move. On M5 the widened
    ±20 µm sweep read 25.9 at its far edge against 1.95 at true focus.

    These are the tests design/28 F2 said were missing: they establish the
    premise (the old metric really does invert) on the same frames that show
    the replacement is right-signed, instead of asserting only the second half.
    """

    @pytest.mark.parametrize("kind,gain", FIELD_TYPES)
    def test_tenengrad_is_maximised_at_focus(self, kind, gain):
        scores = [tenengrad(f) for f in defocus_series(kind, gain)]
        assert scores[0] == max(scores), f"{kind}/{gain}: peak at defocus, not focus"

    @pytest.mark.parametrize("kind,gain", FIELD_TYPES)
    def test_tenengrad_falls_away_from_focus(self, kind, gain):
        # Not merely a peak: a usable objective for a sweep that starts blurred.
        scores = [tenengrad(f) for f in defocus_series(kind, gain)]
        assert scores[0] > scores[2] > scores[-1]

    @pytest.mark.parametrize("kind,gain", FIELD_TYPES[1:])
    def test_the_old_metric_is_maximised_at_MAXIMUM_defocus(self, kind, gain):
        # The defect, on the same frames. Three of the four field types put the
        # old metric's argmax at the blurriest frame in the series — which is
        # exactly what walked the M5 sweep to its boundary.
        scores = [normalized_laplacian_variance(f) for f in defocus_series(kind, gain)]
        assert scores[-1] == max(scores)

    def test_the_old_metric_is_u_shaped_even_where_its_argmax_is_right(self):
        # The one field type where the old metric's argmax survives (bright
        # sparse puncta) still fails in practice: the curve turns around and
        # climbs again, so a sweep whose window misses true focus — the M5 case,
        # where the coarse step was 2.5 µm — reads the far edge as best.
        scores = [normalized_laplacian_variance(f) for f in defocus_series("puncta", 1.0)]
        trough = int(np.argmin(scores))
        assert 0 < trough < len(scores) - 1
        assert scores[-1] > scores[trough] * 3

    def test_tenengrad_is_not_fooled_by_a_brighter_but_blurrier_field(self):
        # The failure the old normaliser existed to prevent (design/14 §10),
        # checked against the metric that replaced it. Illumination is NOT
        # normalised away, so this only holds within a sweep's fixed
        # illumination — which is why run_autofocus holds it fixed and the
        # payload stamps metric_valid_for.
        focused, blurred = defocus_series("puncta", 1.0, defocus=(0.0, 6.0))
        assert tenengrad(focused) > tenengrad(blurred)

    def test_polarity_insensitive(self):
        # Dark structure on a bright field scores like its inverse.
        focused, blurred = defocus_series("puncta", 1.0, defocus=(0.0, 6.0))
        assert tenengrad(60000 - focused) > tenengrad(60000 - blurred)

    def test_flat_image_is_zero(self):
        assert tenengrad(np.zeros((64, 64))) == 0.0
        assert tenengrad(np.full((64, 64), 400.0)) == 0.0

    def test_a_constant_pedestal_does_not_change_the_score(self):
        # No background subtraction is needed or wanted: a uniform offset (the
        # camera pedestal, or a uniform haze) differentiates away exactly.
        img = synthetic_puncta(read_noise=8.0).astype(np.float64)
        assert tenengrad(img + 5000.0) == pytest.approx(tenengrad(img))

    def test_frame_size_does_not_enter(self):
        # A per-pixel mean, so a crop of statistically similar content scores
        # the same. (Content still matters — cropping onto a bright feature is
        # a real change, which is what metric_valid_for's roi key is for.)
        img = defocus_series("extended", 1.0, defocus=(0.0,))[0]
        assert tenengrad(img[40:240, 60:260]) == pytest.approx(tenengrad(img), rel=0.25)


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

    def test_empty_field_no_longer_outranks_a_cell(self):
        # design/25 was the empty field's metric reading HIGHER than the cell's,
        # so ranking tiles by focus_metric picked the emptiest one. design/36
        # removed the cause: the inflation came from dividing by a contrast term
        # that collapses when there is nothing in the field. Tenengrad has no
        # such denominator, so an empty field now scores at its noise floor and
        # ranks BELOW the cell — the validity flag is no longer the only thing
        # standing between the agent and the wrong tile.
        rng = np.random.default_rng(0)
        empty = (400 + rng.normal(0, 10, (256, 256))).astype(np.uint16)
        cell = synthetic_puncta(shape=(256, 256), read_noise=8.0)
        assert compute_stats(empty).focus_metric < compute_stats(cell).focus_metric
        assert compute_stats(empty).focus_metric_valid is False
        assert compute_stats(cell).focus_metric_valid is True

    def test_the_old_metric_is_what_inverted_that_ranking(self):
        # Guards the claim above: the same two fields, scored the old way, rank
        # the wrong way round. If this ever stops failing to rank correctly, the
        # design/36 narrative is wrong and should be re-derived, not patched.
        rng = np.random.default_rng(0)
        empty = (400 + rng.normal(0, 10, (256, 256))).astype(np.uint16)
        cell = synthetic_puncta(shape=(256, 256), read_noise=8.0)
        assert normalized_laplacian_variance(empty) > normalized_laplacian_variance(cell)

    def test_background_level_is_reported(self):
        stats = compute_stats(np.full((64, 64), 400, dtype=np.uint16))
        assert stats.background_level == pytest.approx(400.0, abs=1)

    def test_warning_names_the_snr_and_the_hazard(self):
        msg = focus_invalid_warning(2.4)
        assert "2.4" in msg and "noise floor" in msg


class TestCoverageStats:
    def test_noisy_camera_frame_separates_empty_from_structured(self):
        photons = np.zeros((256, 256))
        structured_photons = photons.copy()
        structured_photons[64:192, 88:168] = 30
        structured_photons[80:96, 100:116] = 1000
        empty = compute_stats(simulated_camera_frame(photons, seed=10))
        structured = compute_stats(simulated_camera_frame(structured_photons, seed=10))

        assert empty.signal_coverage < 0.01
        assert empty.structure_coverage < 0.005
        assert structured.signal_coverage > 0.1
        assert structured.structure_coverage > 0.1
        assert structured.signal_concentration > empty.signal_concentration + 0.3

    def test_blur_changes_pixel_coverage_but_preserves_structure_extent(self):
        """F5: diffuse material below the pixel gate separates from glass."""
        diffuse_photons = np.zeros((256, 256))
        diffuse_photons[64:192, 88:168] = 3
        out_of_focus = simulated_camera_frame(diffuse_photons, seed=12)
        empty = simulated_camera_frame(np.zeros_like(diffuse_photons), seed=12)

        field_stats = compute_stats(out_of_focus)
        empty_stats = compute_stats(empty)
        assert field_stats.snr < UNCALIBRATED_MIN_SNR_FALLBACK
        assert field_stats.signal_coverage < 0.01
        assert field_stats.structure_coverage > 0.1
        assert empty_stats.structure_coverage < 0.005

    def test_bright_corner_is_concentrated_but_spread_signal_is_not(self):
        """F6's failure: the corner must WIN on snr, and extent must catch it.

        The corner is 24x24 = 0.88% of the frame, deliberately wider than the
        0.5% tail p99.5 is taken over. Narrower than that the corner does not
        define p99.5, snr prefers the spread field unaided, and this test passes
        without reproducing the failure it exists for: measured on this fixture,
        a 16x16 corner reads snr 4.05 against the spread field's 239.0, and
        20x20 reads 3411. That is F6's "1% of the frame is enough to define
        p99.5" from the other side.
        """
        total_photons = 10000 * 16 * 16
        corner_photons = np.zeros((256, 256))
        corner_photons[8:32, 8:32] = total_photons / (24 * 24)
        spread_photons = np.zeros((256, 256))
        spread_photons[::4, ::4] = total_photons / (64 * 64)  # same total photons

        corner_stats = compute_stats(simulated_camera_frame(corner_photons, seed=4))
        spread_stats = compute_stats(simulated_camera_frame(spread_photons, seed=4))
        # F6: snr ranks one bright corner above a field that is full of sample.
        assert corner_stats.snr > spread_stats.snr
        assert corner_stats.signal_coverage < spread_stats.signal_coverage
        # ...and concentration is what says the corner is not a full field.
        assert corner_stats.signal_concentration == pytest.approx(1.0, abs=0.05)
        assert spread_stats.signal_concentration < 0.2

    def test_empty_camera_frame_has_near_zero_coverage(self):
        stats = compute_stats(simulated_camera_frame(np.zeros((64, 64)), seed=20))
        assert stats.signal_coverage < 0.01
        assert stats.structure_coverage < 0.005


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


def test_clipping_invalidates_snr_instead_of_reporting_a_large_value():
    image = np.arange(10000, dtype=np.uint16).reshape(100, 100)
    image.flat[:5] = np.iinfo(np.uint16).max
    stats = compute_stats(image)
    assert stats.saturated_fraction == pytest.approx(0.0005)
    assert stats.snr is None
    assert stats.snr_valid is False
    assert stats.focus_metric_valid is False
    assert "saturated" in stats.snr_invalid_reason


def test_negative_going_structure_refuses_snr_but_keeps_focus_valid():
    rng = np.random.default_rng(9)
    image = (4000 + rng.normal(0, 8, (128, 128))).astype(np.uint16)
    image[40:88, 40:88] = 500
    stats = compute_stats(image)
    assert stats.snr is None
    assert stats.snr_valid is False
    assert stats.focus_metric_valid is True
    assert "negative-going" in stats.snr_invalid_reason


def test_component_scalar_output_is_bit_identical_to_pre81b():
    import json
    from microclaw.image_analysis import connected_components
    image = np.fromfunction(lambda r, c: 10 + (r+c) % 2, (12, 12)).astype(np.uint16)
    image[1:3, 1:3] = 100
    image[7:9, 8:11] = 120
    # Captured by executing 57652fd's function; JSON equality pins every float
    # representation as well as every existing key, without tolerance.
    expected = '{"threshold": 15.4478, "background_level": 11.0, "noise_mad_sigma": 1.4826, "n_components": 2, "objects": [{"label": 1, "area_um2": 0.064516, "centroid_stage_um": [1.4405000000000001, -7.3095], "bounding_box_stage_um": {"x_min": 1.3135, "y_min": -7.4365, "x_max": 1.5675, "y_max": -7.1825}, "bounding_box_px": [1, 1, 3, 3]}, {"label": 2, "area_um2": 0.096774, "centroid_stage_um": [2.393, -6.5475], "bounding_box_stage_um": {"x_min": 2.2025, "y_min": -6.6745, "x_max": 2.5835, "y_max": -6.4205000000000005}, "bounding_box_px": [8, 7, 11, 9]}]}'
    assert json.dumps(connected_components(image, .127, [1.25, -7.5], min_snr=3)) == expected


@pytest.mark.parametrize('basis, area, centroid', [
    ([[0, .127], [-.127, 0]], .032258, [10.254, 19.6825]),
    ([[1, .3], [.2, 1]], 1.88, [13.1, 22.5]),
])
def test_component_general_basis_geometry(basis, area, centroid):
    from microclaw.image_analysis import connected_components
    image = np.zeros((8, 8), dtype=np.uint16)
    image[2, 2:4] = 10
    result = connected_components(image, basis_um=basis, origin_um=[10, 20],
                                  covered_mask=np.ones(image.shape, bool))
    assert result['background_level'] == result['noise_mad_sigma'] == result['threshold'] == 0
    assert result['n_components'] == 1
    component = result['objects'][0]
    assert component['area_um2'] == area
    np.testing.assert_allclose(component['centroid_stage_um'], centroid)
    assert component['centroid_px'] == [2.5, 2.0]
    assert component['n_pixels'] == 2
    assert component['bounding_box_px'] == [2, 2, 4, 3]
    assert 'bounding_box_stage_um' not in component
    assert 'bounding_box_stage_hull_um' in component


def test_source_zero_population_changes_component_count():
    from microclaw.image_analysis import connected_components
    image = np.zeros((16, 16), np.uint16)
    image[8, 8] = 100
    source = connected_components(image, basis_um=[[0, .127], [-.127, 0]],
                                  origin_um=[0, 0], covered_mask=np.ones(image.shape, bool))
    mosaic = connected_components(image, .127, [0, 0])
    assert source['n_components'] == 1 and mosaic['n_components'] == 0
    assert source['background_level'] == source['threshold'] == 0
    assert mosaic['background_level'] == mosaic['threshold'] == 100


def test_component_refuses_two_bases():
    from microclaw.image_analysis import connected_components
    with pytest.raises(ValueError, match='Supply either basis_um or pixel_size_um, not both'):
        connected_components(np.ones((3, 3)), 1., [0, 0], basis_um=[[1., 0], [0, 1.]])
