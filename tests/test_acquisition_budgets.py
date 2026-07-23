from unittest.mock import MagicMock
import inspect
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


@pytest.mark.parametrize(
    ("field", "plan"),
    [
        ("confirm_above_frames", AcquisitionPlan(2, 1, 1, 2)),
        ("confirm_above_duration_s", AcquisitionPlan(1, 1, 2, 1)),
        ("confirm_above_bytes", AcquisitionPlan(1, 1, 1, 2)),
        ("confirm_above_illuminated_ms", AcquisitionPlan(1, 2, 1, 1)),
    ],
)
def test_each_confirmation_threshold_fires_above_and_not_below(
    monkeypatch, field, plan
):
    from microclaw import tools

    calls = []
    monkeypatch.setattr(
        tools, "CONFIRM_FN",
        lambda summary, kind="action": calls.append((summary, kind)) or True,
    )
    ctrl = MagicMock()
    monkeypatch.setattr(tools, "_ACQUISITION_LEDGERS", __import__("weakref").WeakKeyDictionary())
    tools._authorize_acquisition(ctrl, _guard(**{field: 1}), plan).close()
    assert len(calls) == 1
    assert calls[0][1] == "acquisition"

    calls.clear()
    at_threshold = AcquisitionPlan(1, 1, 1, 1)
    tools._authorize_acquisition(ctrl, _guard(**{field: 1}), at_threshold).close()
    assert calls == []


def test_declined_confirmation_rolls_back_reservation_fully(monkeypatch):
    from microclaw import tools

    ctrl = MagicMock()
    ledger = AcquisitionLedger()
    monkeypatch.setattr(tools, "_acquisition_ledger", lambda ignored: ledger)
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda *args, **kwargs: False)
    before = (ledger.frames, ledger.bytes, ledger.illuminated_ms,
              ledger._reserved_illuminated_ms)
    with pytest.raises(SafetyViolation, match="declined"):
        tools._authorize_acquisition(
            ctrl, _guard(confirm_above_frames=1), AcquisitionPlan(2, 1, 1, 2)
        )
    assert (ledger.frames, ledger.bytes, ledger.illuminated_ms,
            ledger._reserved_illuminated_ms) == before


def test_reservation_records_an_extra_completed_frame_without_raising():
    reservation = AcquisitionLedger().reserve(
        _guard(max_frames=1), AcquisitionPlan(1, 1, 1, 1)
    )
    assert reservation.commit_frame() is True
    assert reservation.commit_frame() is False
    assert reservation.has_overrun
    assert reservation.overrun_frames == 1


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


def test_list_feeder_completion_signal_has_no_fixed_sleep(monkeypatch, tmp_path):
    from microclaw import tools

    seen = []

    class FakeAcquisition:
        def __init__(self, **kwargs):
            self.process = kwargs["image_process_fn"]
            self._event_queue = queue.Queue()
            self._dataset_disk_location = str(tmp_path / "dataset")
            self._acq = MagicMock()
            self._acq.is_finished.return_value = False

        def __enter__(self):
            return self

        def acquire(self, events):
            for event in events:
                seen.append(event)
                self.process(object(), {}, self._event_queue)

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(tools, "Acquisition", FakeAcquisition)
    monkeypatch.setattr(
        tools.time, "sleep",
        lambda *_: pytest.fail("frame feeder used a fixed sleep tick"),
    )
    reservation = AcquisitionLedger().reserve(
        _guard(max_frames=3), AcquisitionPlan(3, 1, 1, 3)
    )
    tools._acquire_with_hooks(
        _guard(), str(tmp_path), "dataset",
        [{"axes": {"time": i}} for i in range(3)],
        reservation=reservation,
    )
    assert len(seen) == 3


@pytest.mark.parametrize(
    ("entry_point", "planning_edge", "reservation_edge"),
    [
        ("run_zstack", "plan_events", "_authorize_acquisition"),
        ("run_timelapse", "plan_events", "_authorize_acquisition"),
        (
            "run_multiposition_acquisition",
            "_plan_protocol_repetitions",
            "_authorize_acquisition",
        ),
        (
            "run_tile_acquisition",
            "run_multiposition_acquisition",
            "run_multiposition_acquisition",
        ),
        (
            "run_multiposition_with_autofocus",
            "_plan_protocol_repetitions",
            "_authorize_acquisition",
        ),
        (
            "run_adaptive_survey",
            "_acquire_survey_with_detector",
            "_acquire_survey_with_detector",
        ),
        ("run_mda", "AcquisitionPlan", "_authorize_acquisition"),
    ],
)
def test_every_acquisition_entry_point_has_a_planning_and_reservation_edge(
    entry_point, planning_edge, reservation_edge
):
    """Guard the complete public call graph, including deliberate delegation.

    The lower-level edges have behavioral tests above: authorization returns
    the Reservation consumed by the runner, and the runner accounts frames.
    This table prevents a public entry point from bypassing that choke point.
    """
    from microclaw import tools

    source = inspect.getsource(getattr(tools, entry_point))
    assert planning_edge in source
    assert reservation_edge in source
