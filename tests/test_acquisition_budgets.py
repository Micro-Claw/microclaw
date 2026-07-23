from unittest.mock import MagicMock
import queue

import pytest

from microclaw.acquisition import AcquisitionLedger, AcquisitionPlan, plan_events
from microclaw.safety import (
    AcquisitionConstraints,
    SafetyConstraints,
    SafetyGuard,
    SafetyViolation,
)


def _guard(**limits):
    return SafetyGuard(
        SafetyConstraints(acquisition=AcquisitionConstraints(**limits))
    )


def test_plan_uses_camera_geometry_and_time_axis():
    ctrl = MagicMock()
    ctrl.core.get_exposure.return_value = 10
    ctrl.core.get_image_width.return_value = 20
    ctrl.core.get_image_height.return_value = 30
    ctrl.core.get_bytes_per_pixel.return_value = 2
    plan = plan_events(
        ctrl,
        [{"min_start_time": 0}, {"min_start_time": 2}],
    )
    assert plan == AcquisitionPlan(2, 10, 2.01, 2400)
    assert plan.illuminated_ms == 20


def test_unavailable_camera_geometry_is_unplannable():
    ctrl = MagicMock()
    ctrl.core.get_exposure.return_value = 10
    ctrl.core.get_image_width.return_value = 0
    ctrl.core.get_image_height.return_value = 30
    ctrl.core.get_bytes_per_pixel.return_value = 2
    with pytest.raises(SafetyViolation, match="unplannable"):
        plan_events(ctrl, [{}])


def test_hard_limits_and_cumulative_session_limit():
    guard = _guard(max_frames=2, max_session_illuminated_ms=15)
    ledger = AcquisitionLedger()
    with pytest.raises(SafetyViolation, match="max_frames"):
        ledger.reserve(guard, AcquisitionPlan(3, 1, 1, 3))
    reservation = ledger.reserve(guard, AcquisitionPlan(1, 10, 1, 1))
    reservation.commit_frame()
    reservation.close()
    with pytest.raises(SafetyViolation, match="max_session"):
        ledger.reserve(guard, AcquisitionPlan(1, 10, 1, 1))


def test_partial_completion_commits_actual_and_releases_the_rest():
    guard = _guard(max_session_illuminated_ms=25)
    ledger = AcquisitionLedger()
    reservation = ledger.reserve(guard, AcquisitionPlan(2, 10, 1, 200))
    reservation.commit_frame()
    reservation.close()
    assert ledger.frames == 1
    assert ledger.bytes == 100
    assert ledger.illuminated_ms == 10
    # Rollback released the unused 10 ms, so a 15 ms follow-up still fits.
    ledger.reserve(guard, AcquisitionPlan(1, 15, 1, 1)).close()


def test_reservation_refuses_an_extra_completed_frame():
    reservation = AcquisitionLedger().reserve(
        _guard(max_frames=1), AcquisitionPlan(1, 1, 1, 1)
    )
    reservation.commit_frame()
    with pytest.raises(SafetyViolation, match="more frames"):
        reservation.commit_frame()


def test_list_feeder_stops_after_abort_and_replaces_terminator(monkeypatch, tmp_path):
    from microclaw import tools

    seen = []
    terminators = queue.Queue()

    class FakeAcquisition:
        def __init__(self, **kwargs):
            self.process = kwargs["image_process_fn"]
            self._event_queue = terminators
            self._dataset_disk_location = str(tmp_path / "dataset")
            self._acq = MagicMock()
            self._acq.is_finished.return_value = False

        def __enter__(self):
            return self

        def acquire(self, events):
            for event in events:
                seen.append(event)
                self.process(object(), {}, self._event_queue)
                # Simulate an external abort after the in-flight frame returned.
                self._acq.is_finished.return_value = True

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(tools, "Acquisition", FakeAcquisition)
    reservation = AcquisitionLedger().reserve(
        _guard(max_frames=3), AcquisitionPlan(3, 1, 1, 3)
    )
    tools._acquire_with_hooks(
        _guard(), str(tmp_path), "dataset", [{}, {}, {}],
        reservation=reservation,
    )
    assert len(seen) == 1  # the frame in flight completes; no later event is fed
    assert reservation.completed_frames == 1
    assert terminators.get_nowait() is None
