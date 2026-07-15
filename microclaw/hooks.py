from __future__ import annotations
import json
import time
from pathlib import Path

import numpy as np

from microclaw.image_analysis import normalized_laplacian_variance, snap_to_numpy
from microclaw.safety import SafetyViolation


def _frame_index(metadata: dict):
    """Frame/time index for a per-frame log entry.

    The live rig stamps FrameIndex/Frame (design/23 F2 metadata dump), NOT a
    "time" key; the acq-engine port carries the same index at Axes["time"]. Read
    the real keys first and fall back to the legacy "time" so the port/offline
    paths keep working. metadata.get("time") alone silently returned None on the
    rig — the wrong-key-name failure class F2 is closing.
    """
    for key in ("FrameIndex", "Frame"):
        if metadata.get(key) is not None:
            return metadata[key]
    axes = metadata.get("Axes") or {}
    if axes.get("time") is not None:
        return axes["time"]
    return metadata.get("time")


class HookBase:
    """All hooks write a summary log so Claude can read results afterward."""

    def __init__(self, log_path: str | None = None):
        self.log_path = log_path
        self._log: list[dict] = []

    def _write_log(self) -> None:
        """Rewrite the whole file from `self._log`: one hook instance per log_path.

        A caller that constructs a fresh hook per position and points them all at
        one log_path gets the last position's results only — each instance starts
        with an empty `_log` and truncates its predecessor (design/19 Fix 3).
        Hand multi-position events to a single hook instead; see
        `tools._acquire_positions_with_hook`.
        """
        if self.log_path:
            Path(self.log_path).write_text(json.dumps(self._log, indent=2), encoding="utf-8")

    @staticmethod
    def where(metadata: dict) -> dict:
        """Where this image was taken, from its own metadata. Never raises.

        A multi-position acquisition stamps PositionName / XPosition_um_Intended /
        YPosition_um_Intended; a Z-stack stamps ZPosition_um_Intended. Neither
        stamps the other's keys (the acq-engine gates each on the event carrying
        that coordinate), so every read is a .get() and a missing key means "this
        acquisition has no such axis", not "wrong name". This is the fix for the
        design/23 F2 bug where hook_docs claimed XPosition_um_Intended did not
        exist; it does, for every multi-position grid. Verified without hardware in
        design/23-hook-metadata-coords-spike.py and on the live bridge.
        """
        axes = metadata.get("Axes") or {}
        out: dict = {"position": metadata.get("PositionName", axes.get("position"))}
        for key, field in (("XPosition_um_Intended", "x_um"),
                           ("YPosition_um_Intended", "y_um"),
                           ("ZPosition_um_Intended", "z_um")):
            value = metadata.get(key)
            if value is not None:
                out[field] = round(float(value), 3)
        return out

    @staticmethod
    def where_event(event: dict) -> dict:
        """Same shape as where(), for pre/post-hardware hooks that get an event
        (which carries absolute x/y/z and an axes dict) rather than image metadata.

        Keeps "where was this" to a single spelling across the two hook shapes —
        the thing design/23 F2 was written to prevent a third of.
        """
        axes = event.get("axes") or {}
        out: dict = {"position": axes.get("position")}
        for key, field in (("x", "x_um"), ("y", "y_um"), ("z", "z_um")):
            value = event.get(key)
            if value is not None:
                out[field] = round(float(value), 3)
        return out

    def log(self, metadata: dict, **fields) -> None:
        """Append ONE self-describing entry: where the image was + what the hook
        measured, then persist.

        Every hook routes its per-image record through here instead of appending
        to self._log directly, so no hook log can ever again say "filament_r2_c2,
        SNR 73" without saying where r2_c2 was (design/23 Episode A).
        """
        self._log.append({**self.where(metadata), **fields})
        self._write_log()

    def log_event(self, event: dict, **fields) -> None:
        """log() for the pre/post-hardware hook shape (see where_event)."""
        self._log.append({**self.where_event(event), **fields})
        self._write_log()

    def get_summary(self) -> list[dict]:
        return self._log


class AutofocusHook(HookBase):
    """Form B autofocus: runs a Z sweep before each image in the acquisition.

    Used as a post_hardware_hook_fn inside an Acquisition context. After hardware
    moves to the event's XY (and nominal Z), this hook sweeps Z and updates the
    focus device to the sharpest plane before the camera fires.
    """

    def __init__(
        self,
        ctrl,
        guard,
        z_range_um: float,
        z_step_um: float,
        settle_ms: int = 50,
        log_path: str | None = None,
    ):
        super().__init__(log_path)
        from microclaw.autofocus import coarse_then_fine_autofocus
        self.ctrl = ctrl
        self.guard = guard
        self.z_range_um = z_range_um
        self.z_step_um = z_step_um
        self.settle_ms = settle_ms
        self._autofocus_fn = coarse_then_fine_autofocus

    def post_hardware_hook_fn(self, event: dict) -> dict:
        """Called after hardware moves to event position, before image capture."""
        current_z = self.ctrl.core.get_position()
        z_start = current_z - self.z_range_um / 2
        z_end = current_z + self.z_range_um / 2
        try:
            self.guard.check_z(z_start)
            self.guard.check_z(z_end)
        except Exception as e:
            self.log_event(event, autofocus="skipped", reason=str(e))
            return event

        coarse_step = max(self.z_step_um * 5, 1.0)
        result = self._autofocus_fn(
            self.ctrl, self.z_range_um, coarse_step, self.z_step_um, self.settle_ms
        )
        self.log_event(
            event,
            best_z_um=round(result.final_z_um, 3),
            converged=result.converged,
            **({"warning": result.reason} if not result.converged else {}),
        )
        return event


class FocusFeedbackHook(HookBase):
    """Corrects Z drift per frame during timelapse using Laplacian variance."""

    def __init__(
        self,
        ctrl,
        guard,
        threshold_fraction: float = 0.85,
        z_step_um: float = 0.5,
        max_jogs: int = 5,
        log_path: str | None = None,
    ):
        super().__init__(log_path)
        self.ctrl = ctrl
        self.guard = guard
        self.threshold = threshold_fraction
        self.z_step = z_step_um
        self.max_jogs = max_jogs
        self.reference_metric: float | None = None

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue):
        # Normalized metric (design/14 §10): photobleaching dims the frames
        # over a timelapse, and a raw Laplacian variance would read that
        # intensity loss as focus loss and jog Z for no reason.
        metric = normalized_laplacian_variance(image)
        if self.reference_metric is None:
            self.reference_metric = metric
            return image, metadata

        if metric < self.reference_metric * self.threshold:
            focus_device = self.ctrl.core.get_focus_device()
            jogs, corrected, outcome, reason = 0, False, "no_improvement", None
            for _ in range(self.max_jogs):
                current_z = self.ctrl.core.get_position()
                try:
                    self.guard.check_z(current_z + self.z_step)
                    self.ctrl.core.set_position(current_z + self.z_step)
                    self.ctrl.core.wait_for_device(focus_device)
                    jogs += 1
                    new_metric = normalized_laplacian_variance(snap_to_numpy(self.ctrl))
                    if new_metric >= self.reference_metric * self.threshold:
                        self.reference_metric = new_metric
                        corrected, outcome = True, "recovered"
                        break
                except SafetyViolation as e:
                    # A guard rejection means the correction hit a limit — it is
                    # NOT "corrected". Record it; the operator's only signal is
                    # this log line (a swallowed violation never reaches
                    # execute_tool's handler from an acquisition thread).
                    outcome, reason = "blocked_by_guard", str(e)
                    break
                except Exception as e:
                    outcome, reason = "hardware_error", str(e)
                    break
            fields = {"frame": _frame_index(metadata), "focus_correction": corrected,
                      "jogs": jogs, "outcome": outcome}
            if reason:
                fields["reason"] = reason
            self.log(metadata, **fields)   # exactly one entry per triggering frame
        return image, metadata


class IntensityAdaptiveHook(HookBase):
    """Adjusts exposure per frame to keep mean intensity near a target."""

    def __init__(
        self,
        ctrl,
        guard,
        target_mean: float,
        tolerance: float = 0.1,
        min_exposure_ms: float = 1.0,
        max_exposure_ms: float = 1000.0,
        log_path: str | None = None,
    ):
        super().__init__(log_path)
        self.ctrl = ctrl
        self.guard = guard
        self.target_mean = target_mean
        self.tolerance = tolerance
        self.min_exp = min_exposure_ms
        self.max_exp = max_exposure_ms

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue):
        mean = float(np.mean(image))
        if abs(mean - self.target_mean) / self.target_mean > self.tolerance:
            ratio = self.target_mean / max(mean, 1.0)
            new_exp = float(
                np.clip(self.ctrl.core.get_exposure() * ratio, self.min_exp, self.max_exp)
            )
            frame = _frame_index(metadata)
            try:
                self.guard.check_exposure(new_exp)
                self.ctrl.core.set_exposure(new_exp)
                self.log(metadata, frame=frame, new_exposure_ms=round(new_exp, 1))
            except SafetyViolation as e:
                # Guard rejection: expected, but never silent — this log line is
                # the operator's only signal from an acquisition thread.
                self.log(metadata, frame=frame, exposure_change="blocked", reason=str(e))
            except Exception as e:
                self.log(metadata, frame=frame, exposure_change="error", reason=str(e))
        return image, metadata


class PositionFilterHook(HookBase):
    """Rejects positions where mean intensity is below a threshold.

    Returns None to discard the image; pycro-manager drops the remaining
    events for that position.
    """

    def __init__(self, min_mean_intensity: float = 100.0, log_path: str | None = None):
        super().__init__(log_path)
        self.min_mean = min_mean_intensity
        self.rejected: list = []

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue):
        mean = float(np.mean(image))
        if mean < self.min_mean:
            # Dedup on the stamped position identity, not metadata["position_index"]:
            # the live rig has no such key, so the old read collapsed every
            # rejection under -1 and stopped logging which positions it dropped
            # after the first (design/23 F2). self.where() reads PositionName.
            pos = self.where(metadata).get("position")
            if pos not in self.rejected:
                self.rejected.append(pos)
                self.log(metadata, mean_intensity=round(mean, 1), action="rejected")
            return None
        return image, metadata


class MMPluginHook(HookBase):
    """Delegate per-image analysis to an installed Micro-Manager plugin.

    The plugin is treated as an ANALYZER: it receives a scalar/feature derived
    from the image and returns a value microclaw uses for a guarded, Python-side
    decision (e.g. keep/skip). The plugin must NOT be relied on to move hardware
    here — use MMAutofocusPluginHook for that.

    Only a scalar crosses the bridge (np.mean of the image); the full image never
    leaves Python. See design/09 "Composing plugins in a reusable hook".
    """

    def __init__(self, ctrl, guard, classpath: str, method: str = "analyze",
                 reject_below: float | None = None, log_path: str | None = None):
        super().__init__(log_path)
        self.ctrl = ctrl
        self.guard = guard
        guard.check_plugin(classpath)          # runtime blocklist check
        self.classpath = classpath
        self.method = method
        self.reject_below = reject_below
        self._plugin = ctrl.plugins.get_object(classpath)

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue):
        feature = float(np.mean(image))        # keep marshalling trivial/scalar
        try:
            score = float(getattr(self._plugin, self.method)(feature))
        except Exception as e:
            self.log(metadata, frame=_frame_index(metadata), plugin_error=str(e))
            return image, metadata             # fail open: never lose data on bug
        keep = self.reject_below is None or score >= self.reject_below
        self.log(metadata, frame=_frame_index(metadata),
                 plugin=self.classpath, score=score, kept=keep)
        return (image, metadata) if keep else None


class MMAutofocusPluginHook(HookBase):
    """Run an installed MM autofocus plugin before each capture, guarded.

    Drop-in alternative to the pure-Python AutofocusHook: same post_hardware slot,
    but focusing is delegated to the lab's validated MM autofocus plugin. The
    plugin owns the motion; microclaw only guards the *result* passively (never
    re-drives Z, which would fight the plugin's own safety controller).
    """

    def __init__(self, ctrl, guard, plugin_name: str | None = None,
                 log_path: str | None = None):
        super().__init__(log_path)
        self.ctrl = ctrl
        self.guard = guard
        self.plugin_name = plugin_name
        # Hardware-motion plugin: gate on the global motion flag, not a blocklist.
        guard.check_plugin_motion(f"autofocus:{plugin_name or '<active>'}")
        self._af = ctrl.plugins.get_autofocus_method(plugin_name)

    def post_hardware_hook_fn(self, event: dict):
        try:
            new_z = float(self._af.full_focus())     # plugin owns the motion
        except Exception as e:
            self.log_event(event, autofocus="skipped", reason=str(e))
            return event
        # PASSIVE guard: assert on the result; if unsafe, skip capture and stop —
        # do NOT re-drive Z (that would fight the plugin's own safety controller).
        try:
            self.guard.check_z(new_z)
        except Exception as e:
            self.log_event(event, autofocus="unsafe_abort", unsafe_z=new_z,
                           reason=str(e))
            return None                              # skip this capture; signal stop
        self.log_event(event, best_z_um=round(new_z, 3), plugin=self.plugin_name)
        return event


PRECODED_HOOK_REGISTRY: dict[str, type] = {
    "autofocus_per_position": AutofocusHook,
    "focus_feedback": FocusFeedbackHook,
    "intensity_adaptive": IntensityAdaptiveHook,
    "position_filter": PositionFilterHook,
    "mm_plugin_analyzer": MMPluginHook,
    "autofocus_mm_plugin": MMAutofocusPluginHook,
}

# To add a new analysis plugin, define a class that inherits from HookBase,
# implement image_process_fn, and register it here. No other code changes needed.
