"""Acquisition planning and conservative per-session budget accounting."""

from __future__ import annotations

from dataclasses import dataclass
import math
import threading

from .safety import SafetyGuard, SafetyViolation


@dataclass(frozen=True)
class AcquisitionPlan:
    frames: int
    exposure_ms_per_frame: float
    estimated_duration_s: float
    estimated_bytes: int

    @property
    def illuminated_ms(self) -> float:
        return self.frames * self.exposure_ms_per_frame


class AcquisitionLedger:
    """Session ledger. Reservations prevent concurrent plans overcommitting."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.illuminated_ms = 0.0
        self.frames = 0
        self.bytes = 0
        self._reserved_illuminated_ms = 0.0

    def reserve(self, guard: SafetyGuard, plan: AcquisitionPlan) -> "Reservation":
        with self._lock:
            guard.check_acquisition(
                frames=plan.frames,
                duration_s=plan.estimated_duration_s,
                bytes_=plan.estimated_bytes,
                illuminated_ms=plan.illuminated_ms,
                session_illuminated_ms=(
                    self.illuminated_ms + self._reserved_illuminated_ms
                ),
            )
            self._reserved_illuminated_ms += plan.illuminated_ms
        return Reservation(self, plan)


class Reservation:
    def __init__(self, ledger: AcquisitionLedger, plan: AcquisitionPlan) -> None:
        self.ledger = ledger
        self.plan = plan
        self.completed_frames = 0
        self._closed = False

    def commit_frame(self) -> None:
        with self.ledger._lock:
            if self._closed or self.completed_frames >= self.plan.frames:
                raise SafetyViolation(
                    "Acquisition produced more frames than its reservation."
                )
            self.completed_frames += 1
            fraction = 1 / self.plan.frames
            self.ledger.frames += 1
            self.ledger.bytes += math.ceil(self.plan.estimated_bytes * fraction)
            self.ledger.illuminated_ms += self.plan.exposure_ms_per_frame

    def close(self) -> None:
        with self.ledger._lock:
            if not self._closed:
                self.ledger._reserved_illuminated_ms -= self.plan.illuminated_ms
                self._closed = True

    def __enter__(self) -> "Reservation":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def plan_events(ctrl, events: list, exposure_ms: float | None = None) -> AcquisitionPlan:
    frames = len(events)
    if frames <= 0:
        raise SafetyViolation("Acquisition plan must contain at least one frame.")
    exposure = (
        float(exposure_ms)
        if exposure_ms is not None
        else float(ctrl.core.get_exposure())
    )
    width = int(ctrl.core.get_image_width())
    height = int(ctrl.core.get_image_height())
    bpp = int(ctrl.core.get_bytes_per_pixel())
    if width <= 0 or height <= 0 or bpp <= 0:
        raise SafetyViolation("Camera geometry is unavailable; acquisition is unplannable.")
    # Event min_start_time is the strongest known lower bound on wall time.
    last_start = max(
        (float(e.get("min_start_time", 0.0)) for e in events if isinstance(e, dict)),
        default=0.0,
    )
    duration = max(frames * exposure / 1000.0, last_start + exposure / 1000.0)
    return AcquisitionPlan(frames, exposure, duration, frames * width * height * bpp)
