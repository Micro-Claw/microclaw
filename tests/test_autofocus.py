import numpy as np
import time

import pytest
from unittest.mock import MagicMock
import microclaw.autofocus as autofocus
from microclaw.controller import StageMoveError

from microclaw.autofocus import (
    MIN_CONTRAST,
    SweepResult,
    coarse_then_fine_plane_count,
    curve_contrast,
    coarse_then_fine_autofocus,
    single_sweep_autofocus,
    sweep_autofocus,
    sweep_plane_count,
)


class StrVector:
    """Bridge-shaped string vector: deliberately not Python-iterable."""

    def __init__(self, values):
        self._values = list(values)

    def size(self):
        return len(self._values)

    def get(self, index):
        return self._values[index]


def test_image_probe_refactor_characterizes_whole_autofocus_result(monkeypatch):
    """Main's plane choice is pinned; only P1's refusal prose may change."""
    ctrl = MagicMock()
    ctrl.core.get_focus_device.return_value = "Z"
    position = [50.0]
    ctrl.core.get_position.side_effect = lambda *_args: position[0]
    ctrl.core.set_position.side_effect = lambda z: position.__setitem__(0, float(z))
    ctrl.core.device_busy.return_value = False
    ctrl.core.wait_for_device.return_value = None
    values = {49.0: 5.0, 50.0: 4.0, 51.0: 3.0}
    monkeypatch.setattr(autofocus, "snap_to_numpy",
                        lambda _ctrl: np.array([[values[position[0]]]]))

    actual = single_sweep_autofocus(
        ctrl, 2.0, 1.0, settle_ms=0,
        metric_fn=lambda image: float(image[0, 0]), min_contrast=0.15,
    )
    sweep = SweepResult(
        [49.0, 50.0, 51.0], [5.0, 4.0, 3.0], 49.0, False,
        [49.0, 50.0, 51.0], arrival_unverifiable_indices=[0, 1, 2],
    )
    expected = autofocus.AutofocusResult(
        sweep, None, 50.0, 50.0, False, False,
        "Sweep focus peak is at the edge of the searched Z range (best 49.000 µm "
        "sits at a sweep boundary), so there is no interior focus maximum and "
        "this is NOT convergence. The reported best_z_um is the argmax of a "
        "curve that failed its gate, not a focus estimate to move to. Z was NOT "
        "moved (restored to 50.000 µm). Focus may be outside the window, or the "
        "curve may be noise-dominated/non-unimodal; inspect the curve and signal "
        "before widening or retrying.",
    )
    assert actual == expected


@pytest.mark.parametrize("readings, expected", [
    (["out", "blind", "out"], "No plane"),
    (["out", "in", "in", "out"], "not a band"),
    (["in", "in", "in", "out", "in", "in", "in"], "not contiguous"),
])
def test_band_admit_refuses_each_invalid_band(readings, expected):
    reason = autofocus._band_admit(
        readings, {"in"}, 1.0, 0.0, len(readings) - 1.0
    )
    assert expected in reason


def test_band_admit_no_plane_lists_distinct_observed_values():
    reason = autofocus._band_admit(
        ["Out of focus search range", "Within range of focus search"],
        {"Within focus search range"}, 5.0, 0.0, 5.0,
    )
    assert "observed" in reason
    assert "Out of focus search range" in reason
    assert "Within range of focus search" in reason


def test_band_admit_calls_identical_out_of_range_reading_constant():
    reason = autofocus._band_admit(["blind"] * 5, {"in"}, 1.0, 0.0, 4.0)
    assert "constant reading 'blind'" in reason
    assert "No plane" not in reason
    # A window that simply misses the band ALSO reads constant, and on this rig
    # that is the common case: the 2026-08-22 session searched two windows that
    # never contained the answer. Asserting a blind sensor there sends the
    # operator to the turret instead of widening the window, which is the
    # cheaper check and the likelier cause.
    assert "0.0" in reason and "4.0" in reason
    assert "does not reach the band" in reason
    assert "Widen the window before suspecting the hardware" in reason


def test_a_slow_bridge_read_is_not_an_unstable_reading():
    """A constant, correct value must settle however slow the round trip is.

    Measured on this branch before the fix: the deadline was armed BEFORE the
    first read, so read latency was charged against the stability window and a
    healthy device reading one constant value was reported unsettled once the
    round trip passed ~30 ms. An unsettled plane refuses the whole sweep, so a
    busy bridge made the tool refuse for no reason.
    """
    class SlowCore:
        def __init__(self, rtt_s):
            self.rtt = rtt_s
        def get_property(self, _device, _prop):
            time.sleep(self.rtt)
            return "Locked in focus"

    for rtt in (0.001, 0.03, 0.06, 0.1):
        value, settled = autofocus._stable_read(
            SlowCore(rtt), "TIPFSStatus", "Status", dwell_s=0.0
        )
        assert value == "Locked in focus"
        assert settled is True, f"constant reading reported unsettled at {rtt}s"


def test_split_band_offers_the_lagging_sensor_cause_not_only_two_surfaces():
    """A sweep that outruns its sensor is indistinguishable from two surfaces.

    Verified against a fake whose status trails the stage: at a lag longer than
    the per-plane dwell the stray in-range planes sit at the START of the sweep
    and are the sensor still reporting the pre-sweep plane. The reading is
    identical to a genuine coverslip-and-sample pair, so this refusal must not
    assert the physical cause -- it must name both and say which measurement
    separates them.
    """
    reason = autofocus._band_admit(
        ["in", "in", "in", "out", "in", "in", "in"], {"in"}, 1.0, 0.0, 6.0
    )
    assert "not contiguous" in reason
    assert "two reflecting surfaces" in reason
    assert "property_dwell_ms" in reason and "settle_ms" in reason
    assert not reason.endswith(" ")


def test_time_lagging_property_read_waits_for_current_plane(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(autofocus.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        autofocus.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    class LaggingCore:
        def __init__(self):
            self.position = 51.0
            self.previous_position = 51.0
            self.moved_at = 0.0
        def get_focus_device(self): return "Z"
        def get_position(self, _device=None): return self.position
        def set_position(self, z):
            self.previous_position = self.position
            self.position = float(z)
            self.moved_at = clock[0]
        def wait_for_device(self, _device): pass
        def device_busy(self, _device): return False
        def get_allowed_property_values(self, _device, _prop):
            return StrVector(["out", "in"])
        def get_property(self, _device, _prop):
            observed = (self.previous_position
                        if clock[0] - self.moved_at < 0.2 else self.position)
            return "in" if 50.0 <= observed <= 52.0 else "out"

    core = LaggingCore()
    ctrl = MagicMock(core=core)
    probe = autofocus.property_probe(
        core, "lock", "status", ["in"], step_um=1.0,
        lo_um=49.0, hi_um=53.0, dwell_s=0.2,
    )
    result = single_sweep_autofocus(ctrl, 4.0, 1.0, settle_ms=0, probe=probe)
    assert result.converged is True
    assert result.coarse.metric_values == ["out", "in"]
    assert result.coarse.unsettled_indices == []


def test_in_focus_values_declare_non_enumerating_property_categorical():
    class Core:
        def get_allowed_property_values(self, _device, _prop):
            return StrVector([])
        def get_property(self, _device, _prop):
            return "Within range of focus search"

    probe = autofocus.property_probe(
        Core(), "TIPFSStatus", "Status", ["Within range of focus search"]
    )
    assert probe.in_focus_values == frozenset({"Within range of focus search"})
    assert probe.exposures_per_plane == 0


def test_categorical_probe_stops_at_first_in_range_plane(monkeypatch):
    positions = [100.0]
    property_reads = []

    class Core:
        def get_focus_device(self): return "Z"
        def get_position(self, _device=None): return positions[0]
        def set_position(self, z): positions.__setitem__(0, float(z))
        def wait_for_device(self, _device): pass
        def device_busy(self, _device): return False
        def get_allowed_property_values(self, _device, _prop):
            return StrVector([])
        def get_property(self, _device, _prop):
            property_reads.append(positions[0])
            return "in" if positions[0] >= 8.0 else "out"

    core = Core()
    monkeypatch.setattr(
        autofocus, "_stable_read",
        lambda core, device, prop, dwell_s: (core.get_property(device, prop), True),
    )
    probe = autofocus.property_probe(
        core, "lock", "status", ["in"], step_um=1.0,
        lo_um=0.0, hi_um=200.0, dwell_s=0,
    )
    result = single_sweep_autofocus(
        MagicMock(core=core), 200.0, 1.0, settle_ms=0, probe=probe,
    )

    assert property_reads == list(range(9))
    assert positions[0] == 8.0
    assert result.converged is True
    assert result.coarse.stopped_early is True
    assert len(result.coarse.metric_values) == 9
    assert result.coarse.planes_planned == 201


def test_categorical_probe_false_preserves_full_band_centre(monkeypatch):
    positions = [100.0]

    class Core:
        def get_focus_device(self): return "Z"
        def get_position(self, _device=None): return positions[0]
        def set_position(self, z): positions.__setitem__(0, float(z))
        def wait_for_device(self, _device): pass
        def device_busy(self, _device): return False
        def get_allowed_property_values(self, _device, _prop): return StrVector([])
        def get_property(self, _device, _prop):
            return "in" if 8.0 <= positions[0] <= 36.0 else "out"

    core = Core()
    monkeypatch.setattr(
        autofocus, "_stable_read",
        lambda core, device, prop, dwell_s: (core.get_property(device, prop), True),
    )
    probe = autofocus.property_probe(
        core, "lock", "status", ["in"], step_um=1.0,
        lo_um=0.0, hi_um=200.0, dwell_s=0, stop_when_found=False,
    )
    result = single_sweep_autofocus(
        MagicMock(core=core), 200.0, 1.0, settle_ms=0, probe=probe,
    )

    assert len(result.coarse.metric_values) == 201
    assert result.coarse.best_z_um == 22.0
    assert result.final_z_um == 22.0
    assert result.reason is None
    assert result.coarse.stopped_early is False


def test_stop_when_found_exhaustion_keeps_constant_reading_refusal(monkeypatch):
    positions = [2.0]

    class Core:
        def get_focus_device(self): return "Z"
        def get_position(self, _device=None): return positions[0]
        def set_position(self, z): positions.__setitem__(0, float(z))
        def wait_for_device(self, _device): pass
        def device_busy(self, _device): return False
        def get_allowed_property_values(self, _device, _prop): return StrVector([])
        def get_property(self, _device, _prop): return "blind"

    core = Core()
    monkeypatch.setattr(
        autofocus, "_stable_read",
        lambda core, device, prop, dwell_s: (core.get_property(device, prop), True),
    )
    probe = autofocus.property_probe(
        core, "lock", "status", ["in"], step_um=1.0,
        lo_um=0.0, hi_um=4.0, dwell_s=0,
    )
    result = single_sweep_autofocus(
        MagicMock(core=core), 4.0, 1.0, settle_ms=0, probe=probe,
    )

    assert result.converged is False
    assert "constant reading 'blind'" in result.reason
    assert result.coarse.stopped_early is False
    assert result.coarse.planes_planned == 5


def test_band_touching_either_window_edge_is_not_bracketed():
    for readings in (["in", "in", "in", "out"],
                     ["out", "in", "in", "in"],
                     ["in", "in", "in", "in"]):
        reason = autofocus._band_admit(
            readings, {"in"}, 1.0, 0.0, len(readings) - 1.0
        )
        assert "not bracketed" in reason


@pytest.fixture(autouse=True)
def fast_stage_settle(monkeypatch):
    """Keep unit fakes fast while preserving repeated measured read-backs."""
    monkeypatch.setattr("microclaw.controller.STAGE_MOVE_POLL_S", 0)
    monkeypatch.setattr("microclaw.controller.STAGE_MOVE_STABILITY_WINDOW_S", 0)


def test_planned_plane_count_uses_sweep_arithmetic_and_worst_case_fine_pass():
    assert sweep_plane_count(40, 60, 2.5) == 9
    assert coarse_then_fine_plane_count(20, 2.5, 0.5) == 20


def make_ctrl_with_focus_at(best_z: float, width: int = 64, flat: bool = False):
    """Mock controller whose images are sharpest at best_z.

    Defocus is simulated as gaussian BLUR of a fixed scene (photons get
    redistributed), not as an amplitude change — the normalized focus metric
    is deliberately insensitive to brightness (design/14 §10).

    flat=True yields pure noise regardless of Z — the amr_test regression
    (design/14 §4), where a faint field produced a structureless metric curve.
    """
    from scipy.ndimage import gaussian_filter

    rng = np.random.default_rng(7)
    scene = (rng.random((width, width)) * 60000).astype(np.float32)

    core = MagicMock()
    core.get_focus_device.return_value = "DStage"
    current_z = [50.0]
    core.set_position.side_effect = lambda z: current_z.__setitem__(0, z)
    core.get_position.side_effect = lambda *_args: current_z[0]

    def get_tagged_image():
        z = current_z[0]
        if flat:
            # Constant-statistics noise: the metric varies only by sampling.
            pixels = (np.random.rand(width, width) * 3000 + 30000).astype(np.uint16)
        else:
            sigma = 0.5 + abs(z - best_z)      # blur grows away from focus
            pixels = gaussian_filter(scene, sigma=sigma).astype(np.uint16)
        tagged = MagicMock()
        tagged.pix = pixels.tobytes()
        tagged.tags = {"Width": width, "Height": width}
        return tagged

    core.get_tagged_image.side_effect = get_tagged_image
    core.get_bytes_per_pixel.return_value = 2      # 16-bit mono simulated frames
    core.get_number_of_components.return_value = 1
    core.snap_image = MagicMock()
    core.wait_for_image_synced = MagicMock()
    core.wait_for_device = MagicMock()
    ctrl = MagicMock()
    ctrl.core = core
    return ctrl


class TestSweep:
    def test_sweep_snaps_only_after_delayed_stage_arrival(self, monkeypatch):
        class DelayedCore:
            def __init__(self):
                self.position = 50.0
                self.target = 50.0
                self.polls = 0

            def get_focus_device(self): return "Z"
            def set_position(self, z):
                self.target = float(z)
                self.polls = 0
            def wait_for_device(self, _device): pass
            def device_busy(self, _device): return self.polls < 2
            def get_position(self, _device=None):
                self.polls += 1
                if self.polls > 2:
                    self.position = self.target
                return self.position

        core = DelayedCore()
        ctrl = MagicMock(core=core)
        monkeypatch.setattr(autofocus, "snap_to_numpy", lambda _ctrl: np.array([[core.position]]))
        result = sweep_autofocus(
            ctrl, 49.0, 51.0, 1.0, settle_ms=0,
            metric_fn=lambda image: float(image[0, 0]), move_to_best=False,
        )

        assert result.metric_values == [49.0, 50.0, 51.0]
        assert result.measured_z_positions == [49.0, 50.0, 51.0]

    def test_mid_sweep_error_restores_without_masking_failed_restore(self, monkeypatch):
        ctrl = MagicMock()
        ctrl.core.get_position.return_value = 50.0
        original = StageMoveError({
            "start_um": 50.0, "requested_um": 49.0, "measured_um": 48.0,
            "arrival_residual_um": 1.0, "tolerance_um": 0.5,
            "band_policy": "relative", "band_source": "configured",
            "arrival_unverifiable": False,
            "verification_kind": "configured_accuracy",
            "within_tolerance": False, "elapsed_s": 10.0,
            "last_device_status": "busy",
        })
        restore = StageMoveError({
            "start_um": 48.0, "requested_um": 50.0, "measured_um": 48.0,
            "arrival_residual_um": 2.0, "tolerance_um": 0.5,
            "band_policy": "floor", "band_source": "configured",
            "arrival_unverifiable": False,
            "verification_kind": "configured_accuracy",
            "within_tolerance": False, "elapsed_s": 10.0,
            "last_device_status": "idle",
        })
        calls = []

        def fail_then_restore(_ctrl, target, *args, **kwargs):
            calls.append(target)
            if len(calls) == 1:
                raise original
            raise restore

        monkeypatch.setattr(autofocus, "sweep_autofocus", fail_then_restore)
        monkeypatch.setattr(autofocus, "_restore", fail_then_restore)

        with pytest.raises(StageMoveError) as caught:
            single_sweep_autofocus(ctrl, 2.0, 1.0, settle_ms=0)

        assert caught.value is original
        assert calls == [49.0, 50.0]
        assert any("restore" in note.lower() and str(restore) in note
                   for note in caught.value.__notes__)
    def test_refusal_prose_reports_the_measured_restore_not_the_request(self):
        """The reason string is what a microscopist reads; it must not fabricate.

        final_z_um became the measured settled position, but _flat_reason and
        _edge_reason kept printing the requested entry Z, so one result object
        carried two different numbers for the same physical quantity and the
        prose one was the invented one -- the defect this block exists to
        remove, reintroduced on the path with the most human readers.
        """
        class OffsetCore:
            """Arrives 0.2 um past target: inside tolerance, not at the request."""
            def __init__(self): self.position, self.target, self.polls = 50.0, 50.0, 0
            def get_focus_device(self): return "Z"
            def set_position(self, z): self.target, self.polls = float(z), 0
            def wait_for_device(self, _device): pass
            def device_busy(self, _device): return self.polls < 2
            def get_position(self, _device=None):
                self.polls += 1
                if self.polls > 2:
                    self.position = self.target + 0.2
                return self.position

        core = OffsetCore()
        ctrl = MagicMock(core=core)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(autofocus, "snap_to_numpy",
                       lambda _ctrl: np.array([[core.position]]))
            flat = autofocus.single_sweep_autofocus(
                ctrl, 2.0, 1.0, settle_ms=0,
                metric_fn=lambda image: float(image[0, 0]),
            )

        assert flat.moved is False
        assert flat.final_z_um == 50.2
        assert f"{flat.final_z_um:.3f}" in flat.reason
        assert "50.000" not in flat.reason

    def test_sweep_finds_correct_z(self):
        ctrl = make_ctrl_with_focus_at(52.0)
        result = sweep_autofocus(ctrl, 45.0, 55.0, 1.0, settle_ms=0)
        assert abs(result.best_z_um - 52.0) <= 1.0

    def test_peak_interior_when_peak_interior(self):
        ctrl = make_ctrl_with_focus_at(50.0)
        result = sweep_autofocus(ctrl, 45.0, 55.0, 1.0, settle_ms=0)
        assert result.peak_interior

    def test_not_peak_interior_when_peak_at_boundary(self):
        # best_z is below the sweep start → metric is highest at first step
        ctrl = make_ctrl_with_focus_at(44.0)
        result = sweep_autofocus(ctrl, 45.0, 55.0, 1.0, settle_ms=0)
        assert not result.peak_interior

    def test_sweep_returns_correct_structure(self):
        ctrl = make_ctrl_with_focus_at(50.0)
        result = sweep_autofocus(ctrl, 48.0, 52.0, 1.0, settle_ms=0)
        assert len(result.z_positions) == len(result.metric_values)
        assert result.best_z_um in result.z_positions

    def test_move_to_best_false_leaves_stage_at_sweep_end(self):
        ctrl = make_ctrl_with_focus_at(50.0)
        sweep_autofocus(ctrl, 48.0, 52.0, 1.0, settle_ms=0, move_to_best=False)
        assert ctrl.core.get_position() == pytest.approx(52.0)

    def test_linspace_covers_both_endpoints(self):
        ctrl = make_ctrl_with_focus_at(50.0)
        result = sweep_autofocus(ctrl, 45.0, 55.0, 0.5, settle_ms=0)
        assert result.z_positions[0] == pytest.approx(45.0)
        assert result.z_positions[-1] == pytest.approx(55.0)
        assert len(result.z_positions) == 21


class TestCurveContrast:
    def test_amr_test_flat_curve_scores_below_threshold(self):
        # The regression curve: 35668..37875 at constant focus. The old code
        # said settled=true, warning=null and moved the stage 6 um.
        rng = np.random.default_rng(0)
        curve = list(rng.uniform(35668, 37875, size=11))
        assert curve_contrast(curve) < MIN_CONTRAST

    def test_real_focus_curve_scores_above_threshold(self):
        z = np.linspace(-5, 5, 11)
        curve = list(20000 * np.exp(-(z ** 2) / 2) + 1000)
        assert curve_contrast(curve) > MIN_CONTRAST

    def test_empty_and_constant_curves_are_zero(self):
        assert curve_contrast([]) == 0.0
        assert curve_contrast([5.0, 5.0, 5.0]) == 0.0

    def test_full_frame_threshold_and_flat_reason_are_unchanged(self):
        threshold = autofocus.contrast_threshold(autofocus.N_REF)
        assert threshold == MIN_CONTRAST
        reason = autofocus._flat_reason("Sweep", 0.12, threshold, 50.0)
        assert "contrast 0.12 < 0.15" in reason

    def test_regions_larger_than_reference_never_loosen_threshold(self):
        assert autofocus.contrast_threshold(2048 * 2048) == MIN_CONTRAST

    def test_flat_reason_reports_the_threshold_that_was_compared(self):
        # The 54b gate's own box. A power-of-two region divides N_REF exactly
        # and hides this; a real drawn box does not.
        threshold = autofocus.contrast_threshold(160 * 244)
        reason = autofocus._flat_reason("Sweep", 0.12, threshold, 50.0)
        printed = reason.split(" < ", 1)[1].split(")", 1)[0]
        assert float(printed) == pytest.approx(threshold, abs=0.005)
        # A biologist reads this at the microscope. The contrast beside it is
        # formatted to two decimals; an unrounded float here printed
        # "contrast 0.04 < 0.7773852769717593" on the 54b gate's own numbers.
        assert len(printed.split(".")[1]) <= 2, printed


class TestCoarseThenFine:
    def test_converges_and_reports_both_passes(self):
        ctrl = make_ctrl_with_focus_at(50.0)
        result = coarse_then_fine_autofocus(ctrl, z_range_um=10.0, coarse_step_um=2.0,
                                            fine_step_um=0.5, settle_ms=0)
        assert result.converged and result.moved
        assert result.reason is None
        assert result.coarse.z_positions and result.fine.z_positions
        assert abs(result.final_z_um - 50.0) <= 0.5
        assert ctrl.core.get_position() == pytest.approx(result.final_z_um)

    def test_coarse_window_contains_entry_z(self):
        ctrl = make_ctrl_with_focus_at(50.0)
        result = coarse_then_fine_autofocus(ctrl, z_range_um=20.0, coarse_step_um=2.5,
                                            fine_step_um=0.5, settle_ms=0)
        zs = result.coarse.z_positions
        # The old fine-only result failed this: an 11-point window at
        # 37.7..42.7 um from entry_z=45.2 (design/14 §4).
        assert min(zs) <= result.entry_z_um <= max(zs)

    def test_flat_metric_does_not_move_the_stage(self):
        """The amr_test regression: a structureless curve must not move Z."""
        ctrl = make_ctrl_with_focus_at(50.0, flat=True)
        result = coarse_then_fine_autofocus(ctrl, z_range_um=20.0, coarse_step_um=2.5,
                                            fine_step_um=0.5, settle_ms=0)
        assert result.converged is False
        assert result.moved is False
        assert "flat" in result.reason
        assert result.final_z_um == pytest.approx(50.0)
        assert ctrl.core.get_position() == pytest.approx(50.0)  # restored

    def test_edge_peak_does_not_converge_or_move(self):
        """design/28 F1: a peak pinned at the sweep boundary means the true focus
        is OUTSIDE the window. That is NOT convergence — the stage must not move,
        even though the (monotonic) curve has plenty of contrast."""
        ctrl = make_ctrl_with_focus_at(best_z=62.0)   # focus well above 50±5
        result = coarse_then_fine_autofocus(ctrl, z_range_um=10.0, coarse_step_um=2.0,
                                            fine_step_um=0.5, settle_ms=0)
        assert result.converged is False
        assert result.moved is False
        assert not result.fine.peak_interior
        assert "edge" in result.reason
        assert result.final_z_um == pytest.approx(50.0)
        assert ctrl.core.get_position() == pytest.approx(50.0)  # restored

    def test_fine_sweep_clamped_to_guarded_window(self):
        # Coarse peak sits at the top boundary of the range. The fine sweep must not
        # step past current_z ± z_range/2 (the window the caller guarded).
        current_z = 50.0
        z_range = 10.0
        ctrl = make_ctrl_with_focus_at(best_z=60.0)  # peak above the window
        result = coarse_then_fine_autofocus(ctrl, z_range_um=z_range, coarse_step_um=2.0,
                                            fine_step_um=0.5, settle_ms=0)
        lo, hi = current_z - z_range / 2, current_z + z_range / 2
        for sweep in (result.coarse, result.fine):
            if sweep is not None:
                assert all(lo - 1e-9 <= z <= hi + 1e-9 for z in sweep.z_positions)


def make_rig_like_ctrl(best_z: float, um_per_sigma: float = 0.6, width: int = 160):
    """As above, but the noise is added AFTER the blur, by the detector.

    `make_ctrl_with_focus_at` blurs a noisy scene, which smooths the noise away
    along with the signal and flatters any high-pass metric. A camera cannot do
    that: the optics blur, then the sensor adds shot and read noise at a floor
    that stays put. That difference is the whole of design/36 — it is why the
    old metric passed every test in this file while inverting on two rigs.

    The scene here is sparse fluorescent puncta on a dark field, the sample type
    both failing sessions were imaging.
    """
    from scipy.ndimage import gaussian_filter

    rng = np.random.default_rng(5)
    scene = np.zeros((width, width))
    for y, x in zip(rng.integers(8, width - 8, 70), rng.integers(8, width - 8, 70)):
        scene[y, x] += 9000.0
    scene = gaussian_filter(scene, 1.2)          # in-focus PSF

    core = MagicMock()
    core.get_focus_device.return_value = "DStage"
    current_z = [50.0]
    core.set_position.side_effect = lambda z: current_z.__setitem__(0, z)
    core.get_position.side_effect = lambda *_args: current_z[0]

    def get_tagged_image():
        defocus = abs(current_z[0] - best_z) / um_per_sigma
        blurred = gaussian_filter(scene, defocus) if defocus > 0 else scene
        detected = rng.poisson(blurred + 2.0) + 180.0 + rng.normal(0, 2.0, scene.shape)
        tagged = MagicMock()
        tagged.pix = np.clip(detected, 0, 65535).astype(np.uint16).tobytes()
        tagged.tags = {"Width": width, "Height": width}
        return tagged

    core.get_tagged_image.side_effect = get_tagged_image
    core.get_bytes_per_pixel.return_value = 2
    core.get_number_of_components.return_value = 1
    core.snap_image = MagicMock()
    core.wait_for_device = MagicMock()
    ctrl = MagicMock()
    ctrl.core = core
    return ctrl


class TestAgainstADetectorNoiseFloor:
    """design/36 — the M5 (2026-08-04) and Nestor sessions, in a test.

    Both reported non-convergence with the peak pinned at a sweep boundary,
    having walked the curve AWAY from the operator's verified manual focus. The
    stage was correctly left alone both times (design/28 F1 did its job), so
    nothing was damaged — but autofocus was unusable, and widening the range
    made it worse, because a wider window reaches more defocus to score higher.
    """

    def test_sweep_finds_focus_not_the_boundary(self):
        ctrl = make_rig_like_ctrl(best_z=52.0)
        result = sweep_autofocus(ctrl, 45.0, 60.0, 1.0, settle_ms=0, move_to_best=False)
        assert abs(result.best_z_um - 52.0) <= 1.0
        assert result.peak_interior

    def test_two_pass_converges_and_moves_to_focus(self):
        ctrl = make_rig_like_ctrl(best_z=53.0)
        result = coarse_then_fine_autofocus(
            ctrl, z_range_um=20.0, coarse_step_um=2.5, fine_step_um=0.5, settle_ms=0
        )
        assert result.converged and result.moved
        assert abs(result.final_z_um - 53.0) <= 1.0

    def test_widening_the_range_still_converges(self):
        # The M5 escalation: the operator widened 20 µm to 40 µm and the old
        # metric climbed further uphill. A right-signed metric is monotone in
        # the useful direction, so a wider window costs frames, not correctness.
        ctrl = make_rig_like_ctrl(best_z=53.0)
        result = coarse_then_fine_autofocus(
            ctrl, z_range_um=40.0, coarse_step_um=2.5, fine_step_um=0.5, settle_ms=0
        )
        assert result.converged and result.moved
        assert abs(result.final_z_um - 53.0) <= 1.5

    def test_the_old_metric_walks_to_the_boundary_on_the_same_rig(self):
        # The defect itself, reproduced end-to-end: same simulated microscope,
        # same sweep, previous metric — the M5 result, including the refusal to
        # move that made it visible instead of silently destructive.
        from microclaw.image_analysis import normalized_laplacian_variance

        ctrl = make_rig_like_ctrl(best_z=53.0)
        result = coarse_then_fine_autofocus(
            ctrl, z_range_um=20.0, coarse_step_um=2.5, fine_step_um=0.5, settle_ms=0,
            metric_fn=normalized_laplacian_variance,
        )
        assert result.converged is False
        assert result.coarse.peak_interior is False
        assert abs(result.coarse.best_z_um - 53.0) > 5.0
        assert ctrl.core.get_position() == pytest.approx(50.0)


class TestSingleSweepAutofocus:
    def test_converges_on_real_peak(self):
        ctrl = make_ctrl_with_focus_at(52.0)
        result = single_sweep_autofocus(ctrl, z_range_um=10.0, z_step_um=1.0, settle_ms=0)
        assert result.converged and result.moved
        assert abs(result.final_z_um - 52.0) <= 1.0
        assert result.fine is None

    def test_flat_metric_restores_entry_z(self):
        ctrl = make_ctrl_with_focus_at(50.0, flat=True)
        result = single_sweep_autofocus(ctrl, z_range_um=10.0, z_step_um=1.0, settle_ms=0)
        assert result.converged is False
        assert ctrl.core.get_position() == pytest.approx(50.0)

    def test_edge_peak_does_not_converge_or_move(self):
        # design/28 F1: peak at the boundary is not convergence for the one-pass
        # variant either.
        ctrl = make_ctrl_with_focus_at(best_z=62.0)
        result = single_sweep_autofocus(ctrl, z_range_um=10.0, z_step_um=1.0, settle_ms=0)
        assert result.converged is False
        assert result.moved is False
        assert "edge" in result.reason
        assert ctrl.core.get_position() == pytest.approx(50.0)
