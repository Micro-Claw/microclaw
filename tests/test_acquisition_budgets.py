import ast
from unittest.mock import MagicMock
import inspect

import contextlib

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


@contextlib.contextmanager
def _disclosures(monkeypatch):
    """Collect disclosure events. Clauses inform through the sink, never gate."""
    from microclaw import tools
    seen = []
    monkeypatch.setattr(tools._ACQUISITION_EVENT_CONTEXT, "sink", seen.append,
                        raising=False)
    yield seen


def _clause_text(events):
    return " ".join(" ".join(e["clauses"]) for e in events
                    if e.get("type") == "acquisition_disclosure")


@pytest.mark.parametrize(("tool_name", "shape"), [
    ("run_timelapse", {"n_frames": 2, "interval_s": 0}),
    ("run_zstack", {"z_start_um": 0, "z_end_um": 1, "z_step_um": 1}),
])
def test_reserved_per_position_protocol_refuses_nested_hook_before_hardware(
    tool_name, shape
):
    from microclaw import tools

    ctrl = MagicMock()
    guard = MagicMock()
    guard.resolve_in_workspace.return_value = "/safe"
    with pytest.raises(ValueError, match=r"run_multiposition_acquisition\(hook_strategy="):
        getattr(tools, tool_name)(
            ctrl, guard, save_dir="requested", hook_strategy="focus_feedback",
            _reservation=MagicMock(), **shape,
        )
    ctrl.core.assert_not_called()
    assert not ctrl.core.method_calls


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
def test_each_confirmation_threshold_fires_at_boundary_and_not_below(
    monkeypatch, field, plan
):
    from microclaw import tools

    calls = []
    monkeypatch.setattr(
        tools, "CONFIRM_FN",
        lambda summary, kind="action", subject=None, **kwargs: calls.append(
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
    below = {
        "confirm_above_frames": AcquisitionPlan(0, 1, 1, 1),
        "confirm_above_duration_s": AcquisitionPlan(1, 1, 0, 1),
        "confirm_above_illuminated_ms": AcquisitionPlan(1, 0, 1, 1),
    }[field]
    tools._authorize_acquisition(ctrl, _guard(**{field: 1}), below).close()
    assert calls == []


@pytest.mark.parametrize(("value", "asks"), [(499, False), (500, True), (501, True)])
def test_frame_confirmation_boundary(monkeypatch, value, asks):
    from microclaw import tools
    calls = []
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda *a, **k: calls.append(a[0]) or True)
    tools._authorize_acquisition(
        MagicMock(), _guard(confirm_above_frames=500),
        AcquisitionPlan(value, 1, 1, value),
    ).close()
    assert bool(calls) is asks


@pytest.mark.parametrize(("value", "asks"), [(1199, False), (1200, True), (1201, True)])
def test_duration_confirmation_boundary(monkeypatch, value, asks):
    from microclaw import tools
    calls = []
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda *a, **k: calls.append(a[0]) or True)
    tools._authorize_acquisition(
        MagicMock(), _guard(confirm_above_duration_s=1200),
        AcquisitionPlan(1, 1, value, 1),
    ).close()
    assert bool(calls) is asks


def test_one_confirmation_names_both_reasons(monkeypatch):
    from microclaw import tools
    calls = []
    monkeypatch.setattr(tools, "CONFIRM_FN", lambda *a, **k: calls.append(a[0]) or True)
    tools._authorize_acquisition(
        MagicMock(),
        _guard(confirm_above_frames=500, confirm_above_duration_s=1200),
        AcquisitionPlan(500, 1, 1200, 500),
    ).close()
    assert len(calls) == 1
    assert "500 frames" in calls[0]
    assert "20 minutes" in calls[0]


def test_ndtiff_crossing_disclosure_fires_at_the_admission_bound(monkeypatch):
    from microclaw import tools

    calls = []
    monkeypatch.setattr(tools, "CONFIRM_FN",
                        lambda summary, **kwargs: calls.append(summary) or True)
    ctrl = MagicMock()
    bytes_per_frame = 512 * 512 * 2
    frame_bound = tools._ndtiff_frame_bound(bytes_per_frame)
    assert frame_bound == 7937
    crossing = AcquisitionPlan(
        frame_bound + 1, 1, 1, (frame_bound + 1) * bytes_per_frame
    )
    with _disclosures(monkeypatch) as events:
        tools._authorize_acquisition(ctrl, _guard(), crossing).close()
    # "as early as", never "before": the bound allows generously for metadata, so
    # the real roll can land after it.
    assert "roll to a second file as early as frame 7,937" in _clause_text(events)
    # ...and it informed without blocking: no threshold was crossed.
    assert calls == []
    with _disclosures(monkeypatch) as under_events:
        just_under = AcquisitionPlan(frame_bound, 1, 1, frame_bound * bytes_per_frame)
        tools._authorize_acquisition(ctrl, _guard(), just_under).close()
    assert _clause_text(under_events) == ""
    assert calls == []


def test_the_band_the_raw_pixel_bound_missed_is_now_disclosed(monkeypatch):
    """The defect measured on the demo machine, 2026-08-29.

    512x512x16-bit, 8,154 frames: under the raw pixel bound of 8,192, so nothing
    warned -- and the run rolled to a second file at frame 8,114 anyway. Every
    frame count in that band crossed 4 GiB undisclosed. On M2's 150x150 ROI the
    same band is 31.9% wide, which is why this matters for SMLM.
    """
    from microclaw import tools

    calls = []
    monkeypatch.setattr(tools, "CONFIRM_FN",
                        lambda summary, **kwargs: calls.append(summary) or True)
    bytes_per_frame = 512 * 512 * 2
    raw_bound = tools.NDTIFF_MAX_FILE_SIZE // bytes_per_frame
    n = 8154
    assert n <= raw_bound, "the raw bound would have caught this; not the band"
    plan = AcquisitionPlan(n, 10, n * 0.01, n * bytes_per_frame)
    with _disclosures(monkeypatch) as events:
        tools._authorize_acquisition(MagicMock(), _guard(), plan).close()
    assert "4 GiB per-file limit" in _clause_text(events)
    assert calls == [], "disclosing a crossing must not block the run"


@pytest.mark.parametrize(
    ("w", "h", "bpp"),
    [(150, 150, 2), (196, 184, 2), (512, 512, 2), (1024, 1024, 2),
     (2304, 2304, 2), (2048, 2048, 2)],
)
def test_the_bound_is_never_later_than_the_raw_pixel_bound(w, h, bpp):
    """Overshooting the metadata allowance may warn early; it must never warn late.

    A frame always costs more than its pixels, so a bound above the raw one would
    let a guaranteed crossing through undisclosed -- the defect this replaced.
    """
    from microclaw import tools

    raw = w * h * bpp
    assert tools._ndtiff_frame_bound(raw) <= tools.NDTIFF_MAX_FILE_SIZE // raw


def test_the_bound_reproduces_the_two_crossings_we_have_observed():
    """Unfitted: the writer's own admission test with a conservative allowance.

    Both numbers come from parsed NDTiff.index files, not from a model.
    """
    from microclaw import tools

    demo = tools._ndtiff_frame_bound(512 * 512 * 2)
    m2 = tools._ndtiff_frame_bound(150 * 150 * 2)
    assert demo <= 8114, f"demo rolled at 8,114; bound {demo} is late"
    assert m2 <= 72056, f"M2 rolled at 72,056; bound {m2} is late"
    # ...and not so early as to be useless: within 3% of the real crossing.
    assert demo >= 0.97 * 8114, demo
    assert m2 >= 0.97 * 72056, m2


def test_ndtiff_disclosure_never_claims_rollover_before_frame_zero(monkeypatch):
    from microclaw import tools

    calls = []
    monkeypatch.setattr(tools, "CONFIRM_FN",
                        lambda summary, **kwargs: calls.append(summary) or True)
    plan = AcquisitionPlan(1, 1, 1, tools.NDTIFF_MAX_FILE_SIZE + 1)
    with _disclosures(monkeypatch) as events:
        tools._authorize_acquisition(MagicMock(), _guard(), plan).close()
    text = _clause_text(events)
    assert "before the first frame is complete" in text
    assert "before frame 0" not in text
    assert calls == []


@pytest.mark.parametrize(
    ("n_frames", "interval_s", "appears"),
    [(2, 0, True), (2, 1, False), (1, 0, False)],
)
def test_run_timelapse_arguments_drive_burst_disclosure(
    monkeypatch, tmp_path, n_frames, interval_s, appears,
):
    from microclaw import tools

    ctrl = MagicMock()
    ctrl.core.get_exposure.return_value = 10
    ctrl.core.get_image_width.return_value = 16
    ctrl.core.get_image_height.return_value = 16
    ctrl.core.get_bytes_per_pixel.return_value = 2
    calls = []
    monkeypatch.setattr(tools, "CONFIRM_FN",
                        lambda summary, **kwargs: calls.append(summary) or True)
    monkeypatch.setattr(tools, "_acquire_with_hooks", lambda *a, **k: "/data/run")
    with _disclosures(monkeypatch) as events:
        tools.run_timelapse(
            ctrl, _guard(), n_frames=n_frames, interval_s=interval_s,
            save_dir=str(tmp_path),
        )
    assert ("hardware-sequenced burst" in _clause_text(events)) is appears
    assert calls == [], "a burst disclosure must not block the run"


def test_hook_dose_reconstruction_preserves_burst_disclosure(
    monkeypatch, tmp_path,
):
    from microclaw import tools

    class DoseHook:
        def planned_extra_exposures(self):
            return 1

        def planned_extra_exposures_per_event(self):
            return 0

    ctrl = MagicMock()
    ctrl.core.get_exposure.return_value = 10
    ctrl.core.get_image_width.return_value = 16
    ctrl.core.get_image_height.return_value = 16
    ctrl.core.get_bytes_per_pixel.return_value = 2
    calls = []
    monkeypatch.setattr(tools, "_resolve_hook", lambda *a, **k: DoseHook())
    monkeypatch.setattr(tools, "CONFIRM_FN",
                        lambda summary, **kwargs: calls.append(summary) or True)
    monkeypatch.setattr(tools, "_acquire_with_hooks", lambda *a, **k: "/data/run")
    with _disclosures(monkeypatch) as events:
        tools.run_timelapse(
            ctrl, _guard(), n_frames=2, interval_s=0, save_dir=str(tmp_path),
            hook_strategy="dose_hook",
        )
    assert "hardware-sequenced burst" in _clause_text(events)


@pytest.mark.parametrize(
    ("plan", "appears"),
    [
        (AcquisitionPlan(2, 10, 0.02, 2, hardware_sequenced_burst=True), True),
        (AcquisitionPlan(2, 10, 0.02, 2), False),
        (AcquisitionPlan(1, 10, 0.01, 1, hardware_sequenced_burst=True), False),
    ],
)
def test_hardware_burst_disclosure_is_selective(monkeypatch, plan, appears):
    from microclaw import tools

    calls = []
    monkeypatch.setattr(tools, "CONFIRM_FN",
                        lambda summary, **kwargs: calls.append(summary) or True)
    with _disclosures(monkeypatch) as events:
        tools._authorize_acquisition(MagicMock(), _guard(), plan).close()
    text = _clause_text(events)
    assert bool(text) is appears
    if appears:
        for phrase in ("Stop button", "engine abort",
                       "thousands of further exposures", "about"):
            assert phrase in text
    # Never a gate: no threshold was crossed by any of these plans.
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
        reservation=reservation, ctrl=MagicMock(),
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
        reservation=reservation, ctrl=MagicMock(),
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
        ("run_zstack", "plan_events", "_authorize_acquisition"),
        ("run_timelapse", "plan_events", "_authorize_acquisition"),
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
        "run_zstack", "run_timelapse", "run_adaptive_survey",
    }


def test_an_estimate_is_not_reported_to_six_significant_figures():
    """M5's 21-minute plan asked "about 21.0008 minutes" (48e acceptance run,
    2026-08-14) — %g precision on a number the same sentence calls approximate."""
    from microclaw.tools import _format_duration

    assert _format_duration(1260.05) == "about 21 minutes"
    assert _format_duration(60) == "about 1 minute"
    assert _format_duration(90) == "about 1.5 minutes"
    assert _format_duration(45) == "about 45 seconds"


def test_a_disclosure_alone_never_blocks_but_annotates_a_real_confirmation(
    monkeypatch, tmp_path,
):
    """The regression block 60b shipped: clauses must not create a confirmation.

    A 2-frame zero-interval burst became a blocking approval nobody asked for.
    D6 is explicit -- "segmenting is documentation, not a limit" -- and D5 says
    _authorize_acquisition *adds a clause*, to a prompt a threshold triggered.
    """
    from microclaw import tools

    calls = []
    monkeypatch.setattr(tools, "CONFIRM_FN",
                        lambda summary, **kwargs: calls.append(summary) or True)
    burst = AcquisitionPlan(2, 10, 0.02, 2, hardware_sequenced_burst=True)

    # No threshold: discloses through the sink, asks nothing.
    with _disclosures(monkeypatch) as events:
        tools._authorize_acquisition(MagicMock(), _guard(), burst).close()
    assert "hardware-sequenced burst" in _clause_text(events)
    assert calls == []

    # A threshold fires: one confirmation, carrying the clause.
    with _disclosures(monkeypatch):
        tools._authorize_acquisition(
            MagicMock(), _guard(confirm_above_frames=1), burst
        ).close()
    assert len(calls) == 1
    assert "hardware-sequenced burst" in calls[0]
    assert "2 frames" in calls[0]
