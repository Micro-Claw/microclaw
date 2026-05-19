from __future__ import annotations
import json
import time
from pathlib import Path

import numpy as np

from microclaw.image_analysis import laplacian_variance, snap_to_numpy


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
            "best_z_um": round(result.best_z_um, 3),
            "settled": result.settled,
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
        metric = laplacian_variance(image)
        if self.reference_metric is None:
            self.reference_metric = metric
            return image, metadata

        if metric < self.reference_metric * self.threshold:
            focus_device = self.ctrl.core.get_focus_device()
            for _ in range(self.max_jogs):
                current_z = self.ctrl.core.get_position()
                try:
                    self.guard.check_z(current_z + self.z_step)
                    self.ctrl.core.set_position(current_z + self.z_step)
                    self.ctrl.core.wait_for_device(focus_device)
                    new_image = snap_to_numpy(self.ctrl)
                    new_metric = laplacian_variance(new_image)
                    if new_metric >= self.reference_metric * self.threshold:
                        self.reference_metric = new_metric
                        break
                except Exception:
                    break
            self._log.append({"frame": metadata.get("time"), "focus_correction": True})
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
                self._write_log()
            except Exception:
                pass
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


PRECODED_HOOK_REGISTRY: dict[str, type] = {
    "autofocus_per_position": AutofocusHook,
    "focus_feedback": FocusFeedbackHook,
    "intensity_adaptive": IntensityAdaptiveHook,
    "position_filter": PositionFilterHook,
}

# To add a new analysis plugin, define a class that inherits from HookBase,
# implement image_process_fn, and register it here. No other code changes needed.
