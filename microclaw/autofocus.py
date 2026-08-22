from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from microclaw.image_analysis import snap_to_numpy, tenengrad
from microclaw.controller import (
    STAGE_MOVE_POLL_S, StageMoveError, settle_stage_move,
    stage_move_dispatch_failure,
)


@dataclass
class SweepResult:
    """One Z sweep. peak_interior means only "argmax not at a sweep edge" —
    it says nothing about whether a real peak exists (see contrast)."""

    z_positions: list[float]
    metric_values: list[float | str]
    best_z_um: float
    peak_interior: bool
    measured_z_positions: list[float]
    unsettled_indices: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class FocusProbe:
    """One reading per plane, plus how a curve selects and admits a plane."""

    read: Callable[[], float | str | tuple[float | str, bool]]
    choose: Callable[[list], int]
    admit: Callable[[list], Optional[str]]
    exposures_per_plane: int
    describe: str
    in_focus_values: frozenset[str] = frozenset()


@dataclass
class AutofocusResult:
    """Both passes of an autofocus, plus what actually happened to the stage.

    The old API returned only the fine SweepResult, so the coarse pass — the
    one that actually chose the focal plane and constrained the fine window to
    ±coarse_step around its choice — was invisible to the caller. In the
    amr_test session (design/14 §4) the coarse pass picked 40.2 µm when true
    focus was ~45.2 µm, and the model audited an 11-point fine curve it had no
    way of knowing was a consequence.
    """

    coarse: SweepResult
    fine: Optional[SweepResult]
    entry_z_um: float
    final_z_um: float
    converged: bool
    moved: bool
    reason: Optional[str]


# Minimum curve contrast below which the sweep is treated as structureless
# noise and the stage is NOT moved. The amr_test regression scores ~0.06;
# tune upward only against real curves.
MIN_CONTRAST = 0.15
N_REF = 1024 * 1024
MIN_BAND_PLANES = 3
PROPERTY_PROBE_MIN_DWELL_S = 0.5
PROPERTY_READ_MIN_STABLE_S = 0.1


def longest_true_run(flags) -> tuple[int, int]:
    """Return (start, length) of the longest contiguous true run."""
    best_start = best_length = start = length = 0
    for index, flag in enumerate(flags):
        if flag:
            if length == 0:
                start = index
            length += 1
            if length > best_length:
                best_start, best_length = start, length
        else:
            length = 0
    return best_start, best_length


def _strings(values) -> list[str]:
    """Read either a bridge StrVector or an ordinary Python iterable."""
    if hasattr(values, "size") and hasattr(values, "get"):
        return [str(values.get(index)) for index in range(values.size())]
    return [str(value) for value in values]


def _band_admit(readings, in_focus_values, step_um, lo_um, hi_um):
    flags = [reading in in_focus_values for reading in readings]
    start, length = longest_true_run(flags)
    if len(set(readings)) == 1 and readings and not flags[0]:
        return (
            f"Every plane between {lo_um} and {hi_um} um returned the constant "
            f"reading {readings[0]!r} ({len(readings)} planes, {step_um} um "
            f"step), and none of {sorted(in_focus_values)} was seen. Either "
            "this window does not reach the band at all, or the sensor cannot "
            "evaluate focus here — an optical element out of the path reads "
            "constant too. Widen the window before suspecting the hardware."
        )
    if length == 0:
        return (
            f"No plane between {lo_um} and {hi_um} um read one of "
            f"{sorted(in_focus_values)} ({len(readings)} planes, {step_um} um "
            f"step); observed {sorted(set(readings))}. The focus is outside "
            "this window, or the step is coarser "
            "than the lock's capture range. This sweep costs no exposures — "
            "widen it or halve the step."
        )
    if length < MIN_BAND_PLANES:
        return (
            f"Only {length} of {len(readings)} planes read in-range, which is "
            f"not a band. Either the {step_um} um step is comparable to this "
            "lock's capture range, or the reading is intermittent. Re-run with "
            "a smaller step around the hit."
        )
    if sum(flags) > length:
        return (
            f"The in-range planes are not contiguous ({sum(flags)} in range, "
            f"longest run {length}), so this is not one focal plane. Two causes "
            "look identical here: the window really does cross two reflecting "
            "surfaces, or the sensor is slower than the per-plane dwell and the "
            "stray planes are it still reporting an earlier one. Compare "
            "property_dwell_ms against this device's own settling time, and "
            "re-run with a larger settle_ms to tell them apart."
        )
    if start == 0 or start + length == len(flags):
        return (
            "The in-range band touches a sweep boundary, so the band is not "
            "bracketed and its centre is not known. Widen the window and retry."
        )
    return None


def _stable_read(core, device, prop, dwell_s, samples=3, poll_s=None,
                 min_stable_s=PROPERTY_READ_MIN_STABLE_S):
    """Return a value only after dwell plus a time-spanning stable sample run."""
    if poll_s is None:
        poll_s = STAGE_MOVE_POLL_S
    dwell_s = max(float(dwell_s), 0.0)
    if dwell_s:
        time.sleep(dwell_s)
    poll_s = min(float(poll_s), min_stable_s / max(samples - 1, 1))
    started = time.monotonic()
    last = str(core.get_property(device, prop))
    read_cost = time.monotonic() - started
    first_equal_at = time.monotonic()
    # The budget starts AFTER the first read and is sized for the reads it has
    # to contain. A slow bridge round trip is not evidence that the value moved:
    # arming the deadline before the first read made read latency eat the
    # stability window, and a constant, correct reading was then reported
    # UNSETTLED once the round trip passed ~30 ms -- which refuses the whole
    # sweep. This is block 56's lesson in the other direction: a slow read is
    # not an unstable reading, just as a device that is not busy has not
    # necessarily arrived. Raising settle_ms widens this too, which is the knob
    # the operator already has.
    deadline = first_equal_at + max(
        min_stable_s * 2, poll_s * samples, dwell_s, read_cost * (samples + 2)
    )
    count = 1
    while time.monotonic() < deadline:
        now = time.monotonic()
        if count >= samples and now - first_equal_at >= min_stable_s:
            return last, True
        if poll_s > 0:
            time.sleep(min(poll_s, max(deadline - time.monotonic(), 0.0)))
        value = str(core.get_property(device, prop))
        if value == last:
            count += 1
        else:
            last, count = value, 1
            first_equal_at = time.monotonic()
    return last, False


def image_probe(ctrl, metric_fn, region, min_contrast) -> FocusProbe:
    def read():
        return metric_fn(snap_to_numpy(ctrl))
    def choose(readings):
        return int(np.argmax(readings))
    def admit(readings):
        contrast = curve_contrast(readings)
        if contrast < min_contrast:
            return (
                f"focus metric is flat (contrast {contrast:.2f} < "
                f"{min_contrast:.2f}) — the sweep saw noise, not a focus peak."
            )
        return None
    return FocusProbe(
        read=read, choose=choose, admit=admit,
        exposures_per_plane=1,
        describe=f"max tenengrad over {region or 'full frame'}",
    )


def property_probe(core, device, prop, in_focus_values=None, *,
                   step_um=1.0, lo_um=0.0, hi_um=0.0,
                   dwell_s=0.05) -> FocusProbe:
    allowed = _strings(core.get_allowed_property_values(device, prop))
    values = [str(value) for value in (in_focus_values or [])]
    if allowed and not values:
        raise ValueError(
            f"{device}.{prop} reports one of {sorted(allowed)}. Name which "
            "of those mean in-focus (in_focus_values)."
        )
    if values:
        if allowed:
            unknown = sorted(set(values) - set(allowed))
            if unknown:
                raise ValueError(
                    f"{device}.{prop} never reports {unknown}; it reports one of "
                    f"{sorted(allowed)}."
                )
        admitted = frozenset(values)
        def read():
            return _stable_read(core, device, prop, dwell_s)
        def choose(readings):
            start, length = longest_true_run(
                [reading in admitted for reading in readings]
            )
            return start + length // 2
        def admit(readings):
            return _band_admit(readings, admitted, step_um, lo_um, hi_um)
        return FocusProbe(
            read=read, choose=choose, admit=admit,
            exposures_per_plane=0,
            describe=f"centre of {device}.{prop} in-range band",
            in_focus_values=admitted,
        )
    def read_number():
        return float(core.get_property(device, prop))
    def choose_number(readings):
        return int(np.argmax(readings))
    def admit_number(readings):
        del readings
        return None
    return FocusProbe(
        read=read_number, choose=choose_number, admit=admit_number,
        exposures_per_plane=0,
        describe=f"maximum numeric {device}.{prop}",
    )


def contrast_threshold(n_pixels: int) -> float:
    """Scale the flat-curve guard for a metric averaged over ``n_pixels``."""
    return max(MIN_CONTRAST, MIN_CONTRAST * np.sqrt(N_REF / n_pixels))


def sweep_plane_count(z_start_um: float, z_end_um: float, z_step_um: float) -> int:
    """Return the exact number of camera snaps made by ``sweep_autofocus``."""
    return max(int(round(abs(z_end_um - z_start_um) / z_step_um)) + 1, 1)


def coarse_then_fine_plane_count(
    z_range_um: float, coarse_step_um: float, fine_step_um: float
) -> int:
    """Worst-case snaps for both autofocus passes.

    The fine window is at most two coarse steps wide and is clamped to the
    coarse window.  Planning the maximum is intentional: a flat coarse curve
    may stop before the fine pass and close the reservation under-spent.
    """
    coarse = sweep_plane_count(-z_range_um / 2, z_range_um / 2, coarse_step_um)
    fine_span = min(z_range_um, 2 * coarse_step_um)
    return coarse + sweep_plane_count(0, fine_span, fine_step_um)


def curve_contrast(metric_values) -> float:
    """Dynamic range of the metric curve relative to its typical level:
    (max - min) / median.

    A real focus curve varies by a large factor across the sweep; pure noise
    varies by its noise amplitude. The amr_test fine sweep ran 35668..37875 —
    contrast ~0.06, i.e. no structure at all — yet the old code reported
    settled=true, warning=null and moved the stage 6 µm onto noise.

    (design/14's appendix sketched (max - median)/(max - min) instead, but that
    statistic is ~0.5 for pure noise — the median of noise sits mid-range — so
    it cannot fail the design's own flat-curve regression test. The "~0.06"
    the design cites for the amr curve is the relative range, which is what
    this implements.)
    """
    m = np.asarray(metric_values, dtype=float)
    if m.size == 0:
        return 0.0
    med = float(np.median(m))
    span = float(m.max() - m.min())
    if span <= 0:
        return 0.0
    if med <= 0:
        return float("inf")
    return span / med


def sweep_autofocus(
    ctrl,
    z_start_um: float,
    z_end_um: float,
    z_step_um: float,
    settle_ms: int = 50,
    metric_fn: Callable[[np.ndarray], float] = tenengrad,
    move_to_best: bool = True,
    probe: FocusProbe | None = None,
) -> SweepResult:
    """Sweep Z and measure the focus metric; move to best Z only if move_to_best."""
    focus_device = ctrl.core.get_focus_device()
    # linspace, not arange: float arange accumulates error and can drop or
    # duplicate the endpoint.
    n = sweep_plane_count(z_start_um, z_end_um, z_step_um)
    z_positions = [float(z) for z in np.linspace(z_start_um, z_end_um, n)]
    if probe is None:
        probe = image_probe(ctrl, metric_fn, None, MIN_CONTRAST)
    metric_values = []
    measured_z_positions = []
    unsettled_indices = []

    for z in z_positions:
        try:
            ctrl.core.set_position(z)
        except Exception as exc:
            raise stage_move_dispatch_failure(
                ctrl.core, focus_device, z, exc
            ) from exc
        settled = settle_stage_move(ctrl.core, focus_device, z)
        measured_z_positions.append(float(settled["measured_um"]))
        if settle_ms > 0 and probe.exposures_per_plane:
            time.sleep(settle_ms / 1000.0)
        reading = probe.read()
        if isinstance(reading, tuple):
            reading, stable = reading
            if not stable:
                unsettled_indices.append(len(metric_values))
        metric_values.append(reading)

    best_idx = probe.choose(metric_values)
    best_z = measured_z_positions[best_idx]
    if move_to_best:
        try:
            ctrl.core.set_position(best_z)
        except Exception as exc:
            raise stage_move_dispatch_failure(
                ctrl.core, focus_device, best_z, exc
            ) from exc
        best_z = float(settle_stage_move(
            ctrl.core, focus_device, best_z
        )["measured_um"])

    return SweepResult(
        z_positions=z_positions,
        metric_values=metric_values,
        best_z_um=best_z,
        peak_interior=0 < best_idx < len(z_positions) - 1,
        measured_z_positions=measured_z_positions,
        unsettled_indices=unsettled_indices,
    )


def _restore(ctrl, z: float) -> dict:
    focus_device = ctrl.core.get_focus_device()
    try:
        ctrl.core.set_position(z)
    except Exception as exc:
        raise stage_move_dispatch_failure(
            ctrl.core, focus_device, z, exc
        ) from exc
    return settle_stage_move(ctrl.core, focus_device, z)


def _flat_reason(
    which: str, contrast: float, min_contrast: float, restored_z: float
) -> str:
    # restored_z is the MEASURED settled position, never the requested one: this
    # string is the part a microscopist actually reads, and it must not disagree
    # with final_z_um in the same result.
    return _refusal(
        f"{which} focus metric is flat (contrast {contrast:.2f} < "
        f"{min_contrast:.2f}) — the sweep saw noise, not a focus peak.",
        restored_z,
        " The reported best_z_um is "
        "the argmax of a curve that failed its gate, not a focus estimate to "
        "move to. Increase signal (laser power / "
        f"exposure), restrict the metric region around structure when the field "
        f"is mostly background, or focus manually.",
    )


def _refusal(prefix: str, restored_z: float, suffix: str = "") -> str:
    """Compose every autofocus refusal with one measured-restore sentence."""
    return (
        f"{prefix} Z was NOT moved (restored to {restored_z:.3f} µm).{suffix}"
    )


def _edge_reason(which: str, sweep: SweepResult, restored_z: float) -> str:
    """Mirror of _flat_reason for a peak pinned at a sweep boundary (design/28 F1).

    A boundary argmax can mean that focus is outside the window, but can also
    come from a U-shaped or noise-dominated curve. It is insufficient evidence
    for convergence in every case, so do not diagnose a specific cause here.
    """
    return _refusal(
        f"{which} focus peak is at the edge of the searched Z range "
        f"(best {sweep.best_z_um:.3f} µm sits at a sweep boundary), so there is "
        f"no interior focus maximum and this is NOT convergence. The reported "
        "best_z_um is the argmax of a curve that failed its gate, not a focus "
        "estimate to move to.",
        restored_z,
        " Focus may be outside the window, "
        f"or the curve may be noise-dominated/non-unimodal; inspect the curve "
        f"and signal before widening or retrying.",
    )


def coarse_then_fine_autofocus(
    ctrl,
    z_range_um: float,
    coarse_step_um: float,
    fine_step_um: float,
    settle_ms: int = 50,
    metric_fn: Callable[[np.ndarray], float] = tenengrad,
    min_contrast: float = MIN_CONTRAST,
    probe: FocusProbe | None = None,
) -> AutofocusResult:
    """Two-pass autofocus that reports BOTH passes and restores Z on a flat curve.

    The fine window is clamped to the coarse window (entry_z ± z_range/2) so a
    coarse peak sitting at the boundary can't push the fine sweep past the
    range the caller guarded with check_z. A pass whose metric curve has no
    structure (see curve_contrast) aborts the autofocus WITHOUT moving Z —
    moving hardware onto the argmax of noise is worse than doing nothing. A fine
    peak pinned at a sweep boundary is likewise NOT convergence (design/28 F1),
    so the stage is left where it was without guessing why the curve failed.

    The metric is `tenengrad` (design/36), which is maximised at focus and is
    polarity-insensitive, so bright-on-dark and dark-on-bright structure both
    score. It is NOT illumination-invariant — a sweep must therefore hold
    illumination, exposure, ROI and binning fixed, which it does. It replaced
    normalized_laplacian_variance, whose contrast normaliser made it a MINIMUM at
    focus on real fields: on M5 that sent a widened sweep monotonically uphill to
    69.9 µm while the operator's own best focus, at 49.9, scored lowest of all.

    Callers may inject a different callable for controlled experiments;
    autofocus does not guess a metric from one frame.
    """
    entry_z = float(ctrl.core.get_position())
    active_probe = probe or image_probe(ctrl, metric_fn, None, min_contrast)
    lo_bound = entry_z - z_range_um / 2
    hi_bound = entry_z + z_range_um / 2

    try:
        coarse = sweep_autofocus(
            ctrl, lo_bound, hi_bound, coarse_step_um, settle_ms,
            metric_fn=metric_fn, move_to_best=False, probe=active_probe,
        )
    except StageMoveError as move_exc:
        try:
            _restore(ctrl, entry_z)
        except Exception as restore_exc:
            move_exc.add_note(f"Autofocus restore also failed: {restore_exc}")
        raise
    coarse_contrast = (curve_contrast(coarse.metric_values)
                       if active_probe.exposures_per_plane else None)
    cause = active_probe.admit(coarse.metric_values)
    if coarse.unsettled_indices:
        cause = (f"{len(coarse.unsettled_indices)} plane readings did not settle; "
                 "an unsettled sweep cannot converge.")
    if cause:
        restored = _restore(ctrl, entry_z)
        return AutofocusResult(
            coarse=coarse, fine=None, entry_z_um=entry_z,
            final_z_um=float(restored["measured_um"]),
            converged=False, moved=False,
            reason=(_flat_reason("Coarse", coarse_contrast, min_contrast,
                                 float(restored["measured_um"]))
                    if active_probe.exposures_per_plane else
                    _refusal(
                        f"Coarse {cause[0].lower() + cause[1:]}",
                        float(restored["measured_um"]),
                    )),
        )

    lo = max(coarse.best_z_um - coarse_step_um, lo_bound)
    hi = min(coarse.best_z_um + coarse_step_um, hi_bound)
    try:
        fine = sweep_autofocus(
            ctrl, lo, hi, fine_step_um, settle_ms,
            metric_fn=metric_fn, move_to_best=False, probe=active_probe,
        )
    except StageMoveError as move_exc:
        try:
            _restore(ctrl, entry_z)
        except Exception as restore_exc:
            move_exc.add_note(f"Autofocus restore also failed: {restore_exc}")
        raise
    fine_contrast = (curve_contrast(fine.metric_values)
                     if active_probe.exposures_per_plane else None)
    cause = active_probe.admit(fine.metric_values)
    if fine.unsettled_indices:
        cause = (f"{len(fine.unsettled_indices)} plane readings did not settle; "
                 "an unsettled sweep cannot converge.")
    if cause:
        restored = _restore(ctrl, entry_z)
        return AutofocusResult(
            coarse=coarse, fine=fine, entry_z_um=entry_z,
            final_z_um=float(restored["measured_um"]),
            converged=False, moved=False,
            reason=(_flat_reason("Fine", fine_contrast, min_contrast,
                                 float(restored["measured_um"]))
                    if active_probe.exposures_per_plane else
                    _refusal(
                        f"Fine {cause[0].lower() + cause[1:]}",
                        float(restored["measured_um"]),
                    )),
        )

    if not fine.peak_interior:
        restored = _restore(ctrl, entry_z)
        return AutofocusResult(
            coarse=coarse, fine=fine, entry_z_um=entry_z,
            final_z_um=float(restored["measured_um"]),
            converged=False, moved=False,
            reason=_edge_reason("Fine", fine, float(restored["measured_um"])),
        )

    settled = _restore(ctrl, fine.best_z_um)
    return AutofocusResult(
        coarse=coarse, fine=fine, entry_z_um=entry_z,
        final_z_um=float(settled["measured_um"]), converged=True, moved=True,
        reason=None,
    )


def single_sweep_autofocus(
    ctrl,
    z_range_um: float,
    z_step_um: float,
    settle_ms: int = 50,
    metric_fn: Callable[[np.ndarray], float] = tenengrad,
    min_contrast: float = MIN_CONTRAST,
    probe: FocusProbe | None = None,
) -> AutofocusResult:
    """One-pass autofocus with the same contrast gate and result shape as
    coarse_then_fine_autofocus (the single pass is reported as `coarse`).

    Like the two-pass variant it refuses to move on a flat curve OR on a peak
    pinned at a sweep boundary (design/28 F1).
    """
    entry_z = float(ctrl.core.get_position())
    active_probe = probe or image_probe(ctrl, metric_fn, None, min_contrast)
    try:
        sweep = sweep_autofocus(
            ctrl, entry_z - z_range_um / 2, entry_z + z_range_um / 2, z_step_um,
            settle_ms, metric_fn=metric_fn, move_to_best=False,
            probe=active_probe,
        )
    except StageMoveError as move_exc:
        try:
            _restore(ctrl, entry_z)
        except Exception as restore_exc:
            move_exc.add_note(f"Autofocus restore also failed: {restore_exc}")
        raise
    contrast = (curve_contrast(sweep.metric_values)
                if active_probe.exposures_per_plane else None)
    cause = active_probe.admit(sweep.metric_values)
    if sweep.unsettled_indices:
        cause = (f"{len(sweep.unsettled_indices)} plane readings did not settle; "
                 "an unsettled sweep cannot converge.")
    if cause:
        restored = _restore(ctrl, entry_z)
        return AutofocusResult(
            coarse=sweep, fine=None, entry_z_um=entry_z,
            final_z_um=float(restored["measured_um"]),
            converged=False, moved=False,
            reason=(_flat_reason("Sweep", contrast, min_contrast,
                                 float(restored["measured_um"]))
                    if active_probe.exposures_per_plane else
                    _refusal(
                        f"Sweep {cause[0].lower() + cause[1:]}",
                        float(restored["measured_um"]),
                    )),
        )
    if not sweep.peak_interior:
        restored = _restore(ctrl, entry_z)
        return AutofocusResult(
            coarse=sweep, fine=None, entry_z_um=entry_z,
            final_z_um=float(restored["measured_um"]),
            converged=False, moved=False,
            reason=_edge_reason("Sweep", sweep, float(restored["measured_um"])),
        )
    settled = _restore(ctrl, sweep.best_z_um)
    return AutofocusResult(
        coarse=sweep, fine=None, entry_z_um=entry_z,
        final_z_um=float(settled["measured_um"]), converged=True, moved=True,
        reason=None,
    )
