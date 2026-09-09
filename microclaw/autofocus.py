from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from microclaw.image_analysis import snap_to_numpy, tenengrad
from microclaw.controller import (
    STAGE_MOVE_POLL_S, StageMoveError, read_stage_start_position,
    settle_stage_move, stage_move_dispatch_failure,
)


@dataclass
class SweepResult:
    """One Z sweep. peak_interior means only "argmax not at a sweep edge" —
    it says nothing about whether a real peak exists (see contrast)."""

    selected_commanded_z_um: Optional[float] = field(default=None, compare=False, kw_only=True)
    selected_measured_z_um: Optional[float] = field(default=None, compare=False, kw_only=True)
    sweep_window_um: Optional[list[float]] = field(default=None, compare=False, kw_only=True)
    final_commanded_z_um: Optional[float] = field(default=None, compare=False, kw_only=True)
    final_readback_z_um: Optional[float] = field(default=None, compare=False, kw_only=True)
    refusal_reason: Optional[str] = field(default=None, compare=False, kw_only=True)
    z_positions: list[float]
    metric_values: list[float | str]
    best_z_um: float
    peak_interior: bool
    measured_z_positions: list[float]
    unsettled_indices: list[int] = field(default_factory=list)
    arrival_unverifiable_indices: list[int] = field(default_factory=list)
    stopped_early: bool = field(default=False, compare=False)
    planes_planned: int = field(default=0, compare=False)
    target_found: bool = field(default=False, compare=False)
    #: The declared Core-focus band this sweep's probe moves were verified
    #: against, or None where the axis has no declared value and the package
    #: response rule applied. Recorded rather than looked up again later: the
    #: standalone exporter copies the band out of the run's record, and a
    #: safety config edited between the run and the export must not silently
    #: change what the emitted script verifies (design/66, "Standalone export").
    configured_move_tolerance_um: Optional[float] = field(default=None,
                                                          compare=False)


@dataclass(frozen=True)
class FocusProbe:
    """One reading per plane, plus how a curve selects and admits a plane."""

    read: Callable[[], float | str | tuple[float | str, bool]]
    #: Resolved dwell actually used, so the payload never has to re-derive it.
    choose: Callable[[list], int]
    admit: Callable[[list, list[float]], Optional[str]]
    exposures_per_plane: int
    describe: str
    in_focus_values: frozenset[str] = frozenset()
    stop_when_found: bool = False
    dwell_s: float = 0.0


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
    final_commanded_z_um: Optional[float] = field(default=None, compare=False)


# Minimum curve contrast below which the sweep is treated as structureless
# noise and the stage is NOT moved. The amr_test regression scores ~0.06;
# tune upward only against real curves.
MIN_CONTRAST = 0.15
N_REF = 1024 * 1024
MIN_BAND_PLANES = 3
# Dwell before reading a status property, when the caller does not choose one.
# It follows the STOPPING RULE, because that is where the Nikon gate of
# 2026-08-23 showed the dwell does and does not matter. Sweeping 2200-2800 at
# 5 um, dwell 0 read the band as {2655, 2660} and REFUSED it as too few planes;
# dwell 500 ms read {2650, 2655, 2660} and converged. The 2650 plane is real and
# the fast read missed it, at a cost of ~30 s over 121 planes.
#
# Stopping at the first in-range plane tolerates that: a late read lands one
# plane deeper INTO the band, and engaging the lock confirms it at zero dose.
# Mapping the band does not: its refusals are decided by the planes at the
# edges, which are exactly the ones a lagging property reports wrongly.
#
# The 200 Hz PFS sampling rate does not license a zero dwell here. That is the
# servo's own loop; the reading travels through Micro-Manager's adapter, which
# polls on its own cadence. A device's internal rate is not its property's
# update rate.
PROPERTY_PROBE_MIN_DWELL_S = 0
PROPERTY_PROBE_BAND_DWELL_S = 0.5


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


def _value_spans(readings, measured_z_positions, in_focus_values):
    """Contiguous equal readings with measured endpoints and inclusive indices."""
    spans = []
    for index, value in enumerate(readings):
        if index == 0 or value != readings[index - 1]:
            spans.append({
                "value": value,
                "z_um": [measured_z_positions[index]] * 2,
                "planes": [index, index],
                "in_range": value in in_focus_values,
            })
        else:
            spans[-1]["z_um"][1] = measured_z_positions[index]
            spans[-1]["planes"][1] = index
    return spans


def _band_admit(readings, measured_z_positions, in_focus_values, step_um, lo_um, hi_um):
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
        # One entry per distinct value keeps intermittent sensors readable.
        extents = {}
        for span in _value_spans(readings, measured_z_positions, in_focus_values):
            value = span["value"]
            lo, hi = sorted(span["z_um"])
            if value not in extents:
                extents[value] = [lo, hi, 1]
            else:
                extent = extents[value]
                extent[0] = min(extent[0], lo)
                extent[1] = max(extent[1], hi)
                extent[2] += 1
        observed = [
            f"{value!r} at [{lo}, {hi}] um ({runs} run{'s' if runs != 1 else ''})"
            for value, (lo, hi, runs) in extents.items()
        ]
        return (
            f"No plane between {lo_um} and {hi_um} um read one of "
            f"{sorted(in_focus_values)} ({len(readings)} planes, {step_um} um "
            f"step); observed {'; '.join(observed)}. The focus is outside "
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


def _stable_read(core, device, prop, dwell_s, samples=3):
    """Return a value after dwell and consecutive agreeing bridge reads."""
    dwell_s = max(float(dwell_s), 0.0)
    if dwell_s:
        time.sleep(dwell_s)
    last = str(core.get_property(device, prop))
    count = 1
    # Bound a changing property without using elapsed time. Bridge latency is
    # not instability: every bridge call gets the same chance to contribute,
    # however long it takes. A stable property costs exactly `samples` calls.
    for _ in range(samples * 3 - 1):
        value = str(core.get_property(device, prop))
        if value == last:
            count += 1
            if count >= samples:
                return last, True
        else:
            last, count = value, 1
    return last, False


def image_probe(ctrl, metric_fn, region, min_contrast, exposure_callback=None) -> FocusProbe:
    def read():
        image = (snap_to_numpy(ctrl) if exposure_callback is None else
                 snap_to_numpy(ctrl, exposure_callback=exposure_callback))
        return metric_fn(image)
    def choose(readings):
        return int(np.argmax(readings))
    def admit(readings, measured_z_positions):
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
                   dwell_s=None, stop_when_found=True) -> FocusProbe:
    if dwell_s is None:
        dwell_s = (PROPERTY_PROBE_MIN_DWELL_S if stop_when_found
                   else PROPERTY_PROBE_BAND_DWELL_S)
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
            if stop_when_found:
                return next(
                    (index for index, reading in enumerate(readings)
                     if reading in admitted),
                    0,
                )
            start, length = longest_true_run(
                [reading in admitted for reading in readings]
            )
            return start + length // 2
        def admit(readings, measured_z_positions):
            return _band_admit(readings, measured_z_positions, admitted, step_um, lo_um, hi_um)
        return FocusProbe(
            read=read, choose=choose, admit=admit,
            exposures_per_plane=0,
            describe=(
                f"first {device}.{prop} in-range plane"
                if stop_when_found else
                f"centre of {device}.{prop} in-range band"
            ),
            in_focus_values=admitted,
            stop_when_found=stop_when_found,
            dwell_s=dwell_s,
        )
    def read_number():
        return float(core.get_property(device, prop))
    def choose_number(readings):
        return int(np.argmax(readings))
    def admit_number(readings, measured_z_positions):
        del readings
        return None
    return FocusProbe(
        read=read_number, choose=choose_number, admit=admit_number,
        exposures_per_plane=0,
        describe=f"maximum numeric {device}.{prop}",
        dwell_s=dwell_s,
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


def _selected_target_reason(ctrl, target, lo, hi):
    if not np.isfinite(target) or not lo <= target <= hi:
        return f"Selected measured Z {target} um is outside sweep window [{lo}, {hi}] um"
    guard = getattr(ctrl, "_guard", None)
    if guard is not None:
        try:
            guard.check_z(target)
        except Exception as exc:
            return f"Selected measured Z {target} um is not allowed: {exc}"
    return None


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
    entry_z = float(ctrl.core.get_position())
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
    arrival_unverifiable_indices = []
    guard = getattr(ctrl, "_guard", None)
    configured = (guard.stage_move_tolerance(focus_device, core_focus=True)
                  if guard is not None else None)

    for z in z_positions:
        start_um = read_stage_start_position(
            ctrl.core, focus_device, z, "relative", configured
        )
        try:
            ctrl.core.set_position(z)
        except Exception as exc:
            raise stage_move_dispatch_failure(
                ctrl.core, focus_device, z, start_um, "relative", configured, exc
            ) from exc
        settled = settle_stage_move(
            ctrl.core, focus_device, z, start_um, "relative", configured
        )
        if settled["arrival_unverifiable"]:
            arrival_unverifiable_indices.append(len(metric_values))
        measured_z_positions.append(float(settled["measured_um"]))
        if settle_ms > 0 and probe.exposures_per_plane:
            time.sleep(settle_ms / 1000.0)
        reading = probe.read()
        stable = True
        if isinstance(reading, tuple):
            reading, stable = reading
            if not stable:
                unsettled_indices.append(len(metric_values))
        metric_values.append(reading)
        if (probe.stop_when_found and stable and
                reading in probe.in_focus_values):
            break

    best_idx = probe.choose(metric_values)
    best_z = measured_z_positions[best_idx]
    refusal = _selected_target_reason(ctrl, best_z, z_start_um, z_end_um)
    final_commanded = final_readback = None
    if move_to_best:
        final_commanded = entry_z if refusal else best_z
        if refusal:
            final_readback = float(_restore(ctrl, entry_z, refusal)["measured_um"])
        else:
            start_um = read_stage_start_position(
                ctrl.core, focus_device, best_z, "relative", configured)
            try:
                ctrl.core.set_position(best_z)
            except Exception as exc:
                raise stage_move_dispatch_failure(
                    ctrl.core, focus_device, best_z, start_um, "relative", configured, exc
                ) from exc
            final_readback = float(settle_stage_move(
                ctrl.core, focus_device, best_z, start_um, "relative", configured
            )["measured_um"])

    return SweepResult(
        selected_commanded_z_um=z_positions[best_idx],
        selected_measured_z_um=best_z,
        sweep_window_um=[z_start_um, z_end_um],
        final_commanded_z_um=final_commanded,
        final_readback_z_um=final_readback,
        refusal_reason=refusal,
        z_positions=z_positions[:len(metric_values)],
        metric_values=metric_values,
        best_z_um=best_z,
        peak_interior=0 < best_idx < len(z_positions) - 1,
        measured_z_positions=measured_z_positions,
        unsettled_indices=unsettled_indices,
        arrival_unverifiable_indices=arrival_unverifiable_indices,
        configured_move_tolerance_um=configured,
        stopped_early=len(metric_values) < n,
        planes_planned=n,
        target_found=(probe.stop_when_found and
                      metric_values[best_idx] in probe.in_focus_values),
    )


def _restore(ctrl, z: float, original_failure: str | None = None) -> dict:
    try:
        focus_device = ctrl.core.get_focus_device()
        guard = getattr(ctrl, "_guard", None)
        if guard is not None:
            guard.check_z(z)
        configured = (guard.stage_move_tolerance(focus_device, core_focus=True)
                      if guard is not None else None)
        start_um = read_stage_start_position(
            ctrl.core, focus_device, z, "floor", configured
        )
        try:
            ctrl.core.set_position(z)
        except Exception as exc:
            raise stage_move_dispatch_failure(
                ctrl.core, focus_device, z, start_um, "floor", configured, exc
            ) from exc
        return settle_stage_move(
            ctrl.core, focus_device, z, start_um, "floor", configured
        )
    except Exception as exc:
        if original_failure:
            raise RuntimeError(f"{original_failure}; autofocus restoration also failed: {exc}") from exc
        raise


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
    z_min_um: float | None = None,
    z_max_um: float | None = None,
    exposure_callback=None,
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
    active_probe = probe or image_probe(ctrl, metric_fn, None, min_contrast, exposure_callback)
    lo_bound = entry_z - z_range_um / 2 if z_min_um is None else z_min_um
    hi_bound = entry_z + z_range_um / 2 if z_max_um is None else z_max_um

    try:
        coarse = sweep_autofocus(
            ctrl, lo_bound, hi_bound, coarse_step_um, settle_ms,
            metric_fn=metric_fn, move_to_best=False, probe=active_probe,
        )
    except Exception as move_exc:
        try:
            _restore(ctrl, entry_z)
        except Exception as restore_exc:
            move_exc.add_note(f"Autofocus restore also failed: {restore_exc}")
        raise
    window = coarse.sweep_window_um or [min(coarse.z_positions), max(coarse.z_positions)]
    target_reason = _selected_target_reason(ctrl, coarse.best_z_um, *window)
    if target_reason:
        restored = _restore(ctrl, entry_z, target_reason)
        return AutofocusResult(
            coarse=coarse, fine=None, entry_z_um=entry_z,
            final_z_um=float(restored["measured_um"]), converged=False,
            moved=False, reason=_refusal(target_reason, float(restored["measured_um"])),
            final_commanded_z_um=entry_z,
        )
    coarse_contrast = (curve_contrast(coarse.metric_values)
                       if active_probe.exposures_per_plane else None)
    cause = active_probe.admit(coarse.metric_values, coarse.measured_z_positions)
    if coarse.unsettled_indices:
        cause = (f"{len(coarse.unsettled_indices)} plane readings did not settle; "
                 "an unsettled sweep cannot converge.")
    if cause:
        restored = _restore(ctrl, entry_z, cause)
        return AutofocusResult(
            coarse=coarse, fine=None, entry_z_um=entry_z,
            final_z_um=float(restored["measured_um"]),
            converged=False, moved=False, final_commanded_z_um=entry_z,
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
    except Exception as move_exc:
        try:
            _restore(ctrl, entry_z)
        except Exception as restore_exc:
            move_exc.add_note(f"Autofocus restore also failed: {restore_exc}")
        raise
    window = fine.sweep_window_um or [min(fine.z_positions), max(fine.z_positions)]
    target_reason = _selected_target_reason(ctrl, fine.best_z_um, *window)
    if target_reason:
        restored = _restore(ctrl, entry_z, target_reason)
        return AutofocusResult(
            coarse=coarse, fine=fine, entry_z_um=entry_z,
            final_z_um=float(restored["measured_um"]), converged=False,
            moved=False, reason=_refusal(target_reason, float(restored["measured_um"])),
            final_commanded_z_um=entry_z,
        )
    fine_contrast = (curve_contrast(fine.metric_values)
                     if active_probe.exposures_per_plane else None)
    cause = active_probe.admit(fine.metric_values, fine.measured_z_positions)
    if fine.unsettled_indices:
        cause = (f"{len(fine.unsettled_indices)} plane readings did not settle; "
                 "an unsettled sweep cannot converge.")
    if cause:
        restored = _restore(ctrl, entry_z, cause)
        return AutofocusResult(
            coarse=coarse, fine=fine, entry_z_um=entry_z,
            final_z_um=float(restored["measured_um"]),
            converged=False, moved=False, final_commanded_z_um=entry_z,
            reason=(_flat_reason("Fine", fine_contrast, min_contrast,
                                 float(restored["measured_um"]))
                    if active_probe.exposures_per_plane else
                    _refusal(
                        f"Fine {cause[0].lower() + cause[1:]}",
                        float(restored["measured_um"]),
                    )),
        )

    if not fine.peak_interior:
        restored = _restore(ctrl, entry_z, _edge_reason("Fine", fine, entry_z))
        return AutofocusResult(
            coarse=coarse, fine=fine, entry_z_um=entry_z,
            final_z_um=float(restored["measured_um"]),
            converged=False, moved=False, final_commanded_z_um=entry_z,
            reason=_edge_reason("Fine", fine, float(restored["measured_um"])),
        )

    try:
        settled = _restore(ctrl, fine.best_z_um)
    except Exception as move_exc:
        try:
            _restore(ctrl, entry_z)
        except Exception as restore_exc:
            move_exc.add_note(f"Autofocus restore also failed: {restore_exc}")
        raise
    return AutofocusResult(
        coarse=coarse, fine=fine, entry_z_um=entry_z,
        final_z_um=float(settled["measured_um"]), converged=True, moved=True,
        reason=None, final_commanded_z_um=fine.best_z_um,
    )


def single_sweep_autofocus(
    ctrl,
    z_range_um: float,
    z_step_um: float,
    settle_ms: int = 50,
    metric_fn: Callable[[np.ndarray], float] = tenengrad,
    min_contrast: float = MIN_CONTRAST,
    probe: FocusProbe | None = None,
    z_min_um: float | None = None,
    z_max_um: float | None = None,
    exposure_callback=None,
) -> AutofocusResult:
    """One-pass autofocus with the same contrast gate and result shape as
    coarse_then_fine_autofocus (the single pass is reported as `coarse`).

    Like the two-pass variant it refuses to move on a flat curve OR on a peak
    pinned at a sweep boundary (design/28 F1).
    """
    entry_z = float(ctrl.core.get_position())
    active_probe = probe or image_probe(ctrl, metric_fn, None, min_contrast, exposure_callback)
    try:
        sweep = sweep_autofocus(
            ctrl,
            entry_z - z_range_um / 2 if z_min_um is None else z_min_um,
            entry_z + z_range_um / 2 if z_max_um is None else z_max_um,
            z_step_um,
            settle_ms, metric_fn=metric_fn, move_to_best=False,
            probe=active_probe,
        )
    except Exception as move_exc:
        try:
            _restore(ctrl, entry_z)
        except Exception as restore_exc:
            move_exc.add_note(f"Autofocus restore also failed: {restore_exc}")
        raise
    window = sweep.sweep_window_um or [min(sweep.z_positions), max(sweep.z_positions)]
    target_reason = _selected_target_reason(ctrl, sweep.best_z_um, *window)
    if target_reason:
        restored = _restore(ctrl, entry_z, target_reason)
        return AutofocusResult(
            coarse=sweep, fine=None, entry_z_um=entry_z,
            final_z_um=float(restored["measured_um"]), converged=False,
            moved=False, reason=_refusal(target_reason, float(restored["measured_um"])),
            final_commanded_z_um=entry_z,
        )
    contrast = (curve_contrast(sweep.metric_values)
                if active_probe.exposures_per_plane else None)
    cause = active_probe.admit(sweep.metric_values, sweep.measured_z_positions)
    if sweep.target_found:
        cause = None
    if sweep.unsettled_indices:
        cause = (f"{len(sweep.unsettled_indices)} plane readings did not settle; "
                 "an unsettled sweep cannot converge.")
    if cause:
        restored = _restore(ctrl, entry_z, cause)
        return AutofocusResult(
            coarse=sweep, fine=None, entry_z_um=entry_z,
            final_z_um=float(restored["measured_um"]),
            converged=False, moved=False, final_commanded_z_um=entry_z,
            reason=(_flat_reason("Sweep", contrast, min_contrast,
                                 float(restored["measured_um"]))
                    if active_probe.exposures_per_plane else
                    _refusal(
                        f"Sweep {cause[0].lower() + cause[1:]}",
                        float(restored["measured_um"]),
                    )),
        )
    if sweep.target_found:
        return AutofocusResult(
            coarse=sweep, fine=None, entry_z_um=entry_z,
            final_z_um=sweep.best_z_um, converged=True, moved=True,
            reason=None, final_commanded_z_um=sweep.selected_commanded_z_um,
        )
    if not sweep.peak_interior:
        restored = _restore(ctrl, entry_z, _edge_reason("Sweep", sweep, entry_z))
        return AutofocusResult(
            coarse=sweep, fine=None, entry_z_um=entry_z,
            final_z_um=float(restored["measured_um"]),
            converged=False, moved=False, final_commanded_z_um=entry_z,
            reason=_edge_reason("Sweep", sweep, float(restored["measured_um"])),
        )
    try:
        settled = _restore(ctrl, sweep.best_z_um)
    except Exception as move_exc:
        try:
            _restore(ctrl, entry_z)
        except Exception as restore_exc:
            move_exc.add_note(f"Autofocus restore also failed: {restore_exc}")
        raise
    return AutofocusResult(
        coarse=sweep, fine=None, entry_z_um=entry_z,
        final_z_um=float(settled["measured_um"]), converged=True, moved=True,
        reason=None, final_commanded_z_um=sweep.best_z_um,
    )
