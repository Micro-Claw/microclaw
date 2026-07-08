from __future__ import annotations
import json
import time
from pathlib import Path

import numpy as np

from microclaw.image_analysis import normalized_laplacian_variance, snap_to_numpy
from microclaw.safety import SafetyViolation


class HookBase:
    """All hooks write a summary log so Claude can read results afterward."""

    def __init__(self, log_path: str | None = None):
        self.log_path = log_path
        self._log: list[dict] = []

    def _write_log(self) -> None:
        if self.log_path:
            Path(self.log_path).write_text(json.dumps(self._log, indent=2))

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
            self._log.append({"event": event, "autofocus": "skipped", "reason": str(e)})
            self._write_log()
            return event

        coarse_step = max(self.z_step_um * 5, 1.0)
        result = self._autofocus_fn(
            self.ctrl, self.z_range_um, coarse_step, self.z_step_um, self.settle_ms
        )
        self._log.append({
            "position": event.get("axes", {}),
            "best_z_um": round(result.final_z_um, 3),
            "converged": result.converged,
            **({"warning": result.reason} if not result.converged else {}),
        })
        self._write_log()
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
            entry = {"frame": metadata.get("time"), "focus_correction": corrected,
                     "jogs": jogs, "outcome": outcome}
            if reason:
                entry["reason"] = reason
            self._log.append(entry)      # exactly one entry per triggering frame
            self._write_log()
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
            try:
                self.guard.check_exposure(new_exp)
                self.ctrl.core.set_exposure(new_exp)
                self._log.append(
                    {"frame": metadata.get("time"), "new_exposure_ms": round(new_exp, 1)}
                )
            except SafetyViolation as e:
                # Guard rejection: expected, but never silent — this log line is
                # the operator's only signal from an acquisition thread.
                self._log.append(
                    {"frame": metadata.get("time"), "exposure_change": "blocked",
                     "reason": str(e)}
                )
            except Exception as e:
                self._log.append(
                    {"frame": metadata.get("time"), "exposure_change": "error",
                     "reason": str(e)}
                )
            self._write_log()
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
            pos = metadata.get("position_index", -1)
            if pos not in self.rejected:
                self.rejected.append(pos)
                self._log.append(
                    {
                        "position_index": pos,
                        "mean_intensity": round(mean, 1),
                        "action": "rejected",
                    }
                )
                self._write_log()
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
            self._log.append({"frame": metadata.get("time"), "plugin_error": str(e)})
            self._write_log()
            return image, metadata             # fail open: never lose data on bug
        keep = self.reject_below is None or score >= self.reject_below
        self._log.append({"frame": metadata.get("time"),
                          "plugin": self.classpath, "score": score, "kept": keep})
        self._write_log()
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
            self._log.append({"axes": event.get("axes", {}),
                              "autofocus": "skipped", "reason": str(e)})
            self._write_log()
            return event
        # PASSIVE guard: assert on the result; if unsafe, skip capture and stop —
        # do NOT re-drive Z (that would fight the plugin's own safety controller).
        try:
            self.guard.check_z(new_z)
        except Exception as e:
            self._log.append({"axes": event.get("axes", {}),
                              "autofocus": "unsafe_abort", "unsafe_z": new_z,
                              "reason": str(e)})
            self._write_log()
            return None                              # skip this capture; signal stop
        self._log.append({"axes": event.get("axes", {}), "best_z_um": round(new_z, 3),
                          "plugin": self.plugin_name})
        self._write_log()
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
