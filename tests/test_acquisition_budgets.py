import ast
from unittest.mock import MagicMock
import inspect

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
        lambda summary, kind="action", subject=None: calls.append(
            (summary, kind, subject)
        ) or True,
    )
    ctrl = MagicMock()
    monkeypatch.setattr(tools, "_ACQUISITION_LEDGERS", __import__("weakref").WeakKeyDictionary())
    tools._authorize_acquisition(ctrl, _guard(**{field: 1}), plan).close()
    assert len(calls) == 1
    assert calls[0][1] == "acquisition"
    assert calls[0][2] == "threshold"

    calls.clear()
    at_threshold = AcquisitionPlan(1, 1, 1, 1)
    tools._authorize_acquisition(ctrl, _guard(**{field: 1}), at_threshold).close()
    assert calls == []


def test_deprecated_confirm_above_bytes_does_not_gate_a_plan(monkeypatch):
    from microclaw import tools
    calls = []
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda *a, **k: calls.append(a) or True)
    ctrl = MagicMock()
    tools._authorize_acquisition(
        ctrl, _guard(confirm_above_bytes=1), AcquisitionPlan(1, 1, 1, 2)
    ).close()
    assert calls == []


def test_session_brake_refusal_explains_restart_reset():
    with pytest.raises(SafetyViolation, match=r"runaway-loop brake.*restarting.*resets"):
        _guard(max_session_illuminated_ms=1).check_acquisition(
            frames=1, duration_s=1, bytes_=1, illuminated_ms=2,
            session_illuminated_ms=0,
        )


def test_declined_confirmation_rolls_back_reservation_fully(monkeypatch):
    from microclaw import tools

    ctrl = MagicMock()
    ledger = AcquisitionLedger()
    monkeypatch.setattr(tools, "_acquisition_ledger", lambda ignored: ledger)
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda *args, **kwargs: False)
    before = (ledger.frames, ledger.bytes, ledger.illuminated_ms,
              ledger._reserved_illuminated_ms)
    with pytest.raises(SafetyViolation, match="declined") as exc:
        tools._authorize_acquisition(
            ctrl, _guard(confirm_above_frames=1), AcquisitionPlan(2, 1, 1, 2)
        )
    assert (ledger.frames, ledger.bytes, ledger.illuminated_ms,
            ledger._reserved_illuminated_ms) == before
    # The decline must be attributable — distinct from a limit refusal, which
    # names a max_* field (design/32 Block 4 G3: an operator decline surfaced
    # as a bare message the agent could not tell apart from a limit hit).
    message = str(exc.value)
    assert "confirmation" in message
    assert "max_" not in message


def test_reservation_records_an_extra_completed_frame_without_raising():
    reservation = AcquisitionLedger().reserve(
        _guard(max_frames=1), AcquisitionPlan(1, 1, 1, 1)
    )
    assert reservation.commit_frame() is True
    assert reservation.commit_frame() is False
    assert reservation.has_overrun
    assert reservation.overrun_frames == 1


def test_reservation_uses_saved_callback_without_installing_pixel_hook(
    monkeypatch, tmp_path
):
    from microclaw import tools

    received_kwargs = {}
    supplied_events = [
        {"axes": {"time": 0}},
        {"axes": {"time": 1}},
    ]

    class FakeAcquisition:
        def __init__(self, **kwargs):
            received_kwargs.update(kwargs)
            self.saved = kwargs["image_saved_fn"]
            self._dataset_disk_location = str(tmp_path / "dataset")

        def __enter__(self):
            return self

        def acquire(self, events):
            assert isinstance(events, list)
            assert events is supplied_events
            for event in events:
                self.saved(event.get("axes", {}), object())

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(tools, "Acquisition", FakeAcquisition)
    reservation = AcquisitionLedger().reserve(
        _guard(max_frames=2), AcquisitionPlan(2, 1, 1, 2)
    )
    tools._acquire_with_hooks(
        _guard(), str(tmp_path), "dataset",
        supplied_events,
        reservation=reservation,
    )
    assert "image_process_fn" not in received_kwargs
    assert reservation.completed_frames == 2
    assert reservation.ledger.frames == 2


def test_list_acquisition_passes_the_list_directly_to_acquire(monkeypatch, tmp_path):
    from microclaw import tools

    supplied_events = [{"axes": {"time": i}} for i in range(3)]
    received = {}

    class FakeAcquisition:
        def __init__(self, **kwargs):
            received["kwargs"] = kwargs
            self._dataset_disk_location = str(tmp_path / "dataset")

        def __enter__(self):
            return self

        def acquire(self, events):
            received["events"] = events

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(tools, "Acquisition", FakeAcquisition)
    # A reservation MUST be supplied: the removed feeder only converted the
    # list to a generator when one was present, so a reservation-free call
    # passes this assertion even with the feeder still in place.
    reservation = AcquisitionLedger().reserve(
        _guard(max_frames=3), AcquisitionPlan(3, 1, 1, 3)
    )
    tools._acquire_with_hooks(
        _guard(), str(tmp_path), "dataset", supplied_events,
        reservation=reservation,
    )
    assert isinstance(received["events"], list)
    assert received["events"] is supplied_events
    # Accounting still rides along on the free saved-image callback.
    assert "image_saved_fn" in received["kwargs"]
    assert "image_process_fn" not in received["kwargs"]


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
        ("run_adaptive_zstack", "plan_events", "_authorize_acquisition"),
        ("run_adaptive_timelapse", "plan_events", "_authorize_acquisition"),
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
    fn = tools.TOOL_REGISTRY[entry_point]
    assert fn is getattr(tools, entry_point)
    assert fn._microclaw_acquisition_entry_point is True


def test_planner_reachable_public_tools_cannot_bypass_map_registration():
    """Mechanically derive registry tools that reach the planner or ledger."""
    from microclaw import tools

    functions = {
        name: fn for name, fn in inspect.getmembers(tools, inspect.isfunction)
        if fn.__module__ == tools.__name__
    }
    calls = {}
    for name, fn in functions.items():
        tree = ast.parse(inspect.getsource(fn))
        calls[name] = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

    sinks = {"plan_events", "_authorize_acquisition", "_acquisition_ledger"}

    def reaches_planner(name, seen=frozenset()):
        if name in seen:
            return False
        direct = calls.get(name, set())
        return bool(direct & sinks) or any(
            reaches_planner(callee, seen | {name})
            for callee in direct & functions.keys()
        )

    candidates = {
        name for name in tools.TOOL_REGISTRY if reaches_planner(name)
    }
    marked = {
        name for name, fn in tools.TOOL_REGISTRY.items()
        if getattr(fn, "_microclaw_acquisition_entry_point", False)
    }
    assert candidates == marked
    assert candidates - {"run_mda"} == {
        "run_zstack", "run_timelapse", "run_multiposition_acquisition",
        "run_tile_acquisition", "run_multiposition_with_autofocus",
        "run_adaptive_zstack", "run_adaptive_timelapse", "run_adaptive_survey",
    }
