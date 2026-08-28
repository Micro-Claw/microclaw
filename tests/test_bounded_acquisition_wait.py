import json
import inspect
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from microclaw import tools
from microclaw.acquisition import AcquisitionLedger, AcquisitionPlan


class BlockingAcquisition:
    release = threading.Event()
    instance = None

    def __init__(self, **kwargs):
        type(self).release.clear()
        type(self).instance = self
        self.kwargs = kwargs
        self._dataset_disk_location = "/data/run_1"
        self._exception = None

    def acquire(self, events):
        self.events = events

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.release.wait()


def _guard():
    guard = MagicMock()
    guard.resolve_in_workspace.side_effect = lambda path: path
    return guard


def _ctrl(camera=True):
    ctrl = MagicMock()
    ctrl.core.is_sequence_running.return_value = camera
    return ctrl


def _entry(fn):
    fn._microclaw_acquisition_entry_point = True
    return fn


def _call_acquire(ctrl, *args, **kwargs):
    parameters = inspect.signature(tools._acquire_with_hooks).parameters
    if next(iter(parameters)) == "ctrl":
        return tools._acquire_with_hooks(ctrl, *args, **kwargs)
    return tools._acquire_with_hooks(*args, ctrl=ctrl, **kwargs)


class SimulatedClock:
    def __init__(self, *, step=100.0, saved_at=(), release_at=None):
        self.now = 0.0
        self.step = step
        self.saved_at = list(saved_at)
        self.release_at = release_at

    def __call__(self):
        return self.now

    def join(self, waiter, _timeout):
        target = self.now + self.step
        while self.saved_at and self.saved_at[0] <= target:
            self.now = self.saved_at.pop(0)
            BlockingAcquisition.instance.kwargs["image_saved_fn"]({}, object())
        self.now = target
        if self.release_at is not None and self.now >= self.release_at:
            BlockingAcquisition.release.set()
        waiter.join(0.01)


def _install_clock(monkeypatch, clock):
    monkeypatch.setattr(tools, "_acquisition_monotonic", clock)
    monkeypatch.setattr(tools, "_join_acquisition_waiter", clock.join)


def test_fatal_engine_error_returns_complete_unterminated_result_and_waiter_owns_cleanup(
    monkeypatch,
):
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    monkeypatch.setattr(tools, "ERROR_TEARDOWN_GRACE_S", 0.02, raising=False)
    monkeypatch.setattr(tools, "_ACQUISITION_POLL_S", 0.002, raising=False)
    ctrl = _ctrl(True)
    reservation = AcquisitionLedger().reserve(
        MagicMock(), AcquisitionPlan(3, 1, 1, 3)
    )
    hook = MagicMock()

    @_entry
    def run(ctrl, guard):
        def events(acq):
            acq._exception = ValueError("bad notification")
            acq.kwargs["image_saved_fn"]({}, object())
            return [{"axes": {"time": 0}}]
        supervised = inspect.signature(tools._acquire_with_hooks).parameters
        kwargs = ({"plan": reservation.plan} if "plan" in supervised else {})
        return _call_acquire(
            ctrl, guard, "/data", "run", events, hook, reservation, **kwargs
        )

    started = time.monotonic()
    result = json.loads(tools.execute_tool("run", {}, ctrl, _guard(), {"run": run}))
    assert time.monotonic() - started < 0.5
    assert result == {
        "error": "The acquisition engine reported a fatal error and pycro-manager teardown did not complete within 0.02 s. Microclaw stopped waiting.",
        "acquisition": "unterminated",
        "engine_exception": "ValueError: bad notification",
        "dataset_path": "/data/run_1",
        "frames_planned": 3,
        "frames_accounted": 1,
        "camera_sequence_running": True,
        "teardown_running": True,
        "expired_bound": "error_grace",
        "ceiling_fallback": False,
        "hardware": result["hardware"],
        "next": result["next"],
    }
    assert reservation.ledger.in_flight is True
    hook.restore_named_stage.assert_not_called()
    BlockingAcquisition.release.set()


def test_real_run_timelapse_returns_unterminated_result(monkeypatch):
    class FatalAcquisition(BlockingAcquisition):
        def acquire(self, events):
            super().acquire(events)
            self._exception = RuntimeError("engine died")

    monkeypatch.setattr(tools, "Acquisition", FatalAcquisition)
    monkeypatch.setattr(tools, "ERROR_TEARDOWN_GRACE_S", 0.02)
    monkeypatch.setattr(tools, "_ACQUISITION_POLL_S", 0.002)
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)
    monkeypatch.setattr(tools, "_build_acquisition_events", lambda **_: [{"axes": {"time": 0}}])
    reservation = AcquisitionLedger().reserve(
        MagicMock(), AcquisitionPlan(1, 1, 1, 1)
    )
    monkeypatch.setattr(tools, "_authorize_acquisition", lambda *_: reservation)
    ctrl = _ctrl(True)
    result = json.loads(tools.execute_tool(
        "run_timelapse",
        {"n_frames": 1, "interval_s": 0, "save_dir": "/data"},
        ctrl, _guard(),
    ))
    assert result["acquisition"] == "unterminated"
    assert result["engine_exception"] == "RuntimeError: engine died"
    assert result["frames_planned"] == 1
    assert reservation.ledger.in_flight is True
    BlockingAcquisition.release.set()


def test_normal_completion_restoration_failure_is_folded_on_waiter(monkeypatch):
    class PromptAcquisition(BlockingAcquisition):
        def __exit__(self, *_exc):
            return None

    class Hook:
        _named_stage_context = {"device": "Z", "last_known": 12.0}
        _property_context = None
        def restore_named_stage(self):
            assert threading.current_thread().name == "microclaw-acq-teardown"
            raise RuntimeError("restore stuck")

    monkeypatch.setattr(tools, "Acquisition", PromptAcquisition)
    with pytest.raises(tools._HookedAcquisitionFailure) as caught:
        _call_acquire(_ctrl(False), _guard(), "/data", "run", [], Hook())
    assert "named-stage restoration failed: restore stuck" in str(caught.value)
    assert caught.value.last_hardware_state == {"device": "Z", "position_um": 12.0}


def test_survey_handler_does_not_close_waiter_owned_reservations(monkeypatch):
    search = AcquisitionLedger().reserve(MagicMock(), AcquisitionPlan(1, 1, 1, 1))
    acquire = AcquisitionLedger().reserve(MagicMock(), AcquisitionPlan(1, 1, 1, 1))
    reservations = iter([search, acquire])
    monkeypatch.setattr(tools, "_authorize_acquisition", lambda *_: next(reservations))
    monkeypatch.setattr(tools, "_build_acquisition_events", lambda **_: [{"axes": {"position": 0}}])
    monkeypatch.setattr(tools, "_configure_hook_capabilities", lambda *a, **k: None)
    monkeypatch.setattr(tools, "_plan_with_hook_dose", lambda plan, hook: plan)
    monkeypatch.setattr(
        tools, "_acquire_with_hooks",
        lambda *a, **k: (_ for _ in ()).throw(tools.AcquisitionUnterminated(
            dataset_path="/data/run", frames_planned=1, frames_accounted=0,
            engine_exception=None, camera_sequence_running=False,
            teardown_running=True, expired_bound="runtime_ceiling", bound_s=1,
            fallback_ceiling=False,
        )),
    )
    with pytest.raises(tools.AcquisitionUnterminated):
        tools._acquire_survey_with_detector(
            _ctrl(False), _guard(), [{"name": "p0", "x_um": 0, "y_um": 0}],
            "/data", "run", hook=MagicMock(), progress=tools.SurveyProgress(1),
            candidates=__import__("queue").Queue(), adaptive=True,
            acquire_plan=AcquisitionPlan(1, 1, 1, 1),
        )
    assert search.ledger.in_flight is True
    assert acquire.ledger.in_flight is True


def test_acquire_on_hit_handler_does_not_close_waiter_owned_reservation(monkeypatch):
    from microclaw.hook_decisions import UntrustedHookAdapter

    reservation = AcquisitionLedger().reserve(MagicMock(), AcquisitionPlan(1, 1, 1, 1))
    adapter = UntrustedHookAdapter(object())
    monkeypatch.setattr(tools, "_resolve_hook", lambda *a, **k: adapter)
    monkeypatch.setattr(tools, "_prepare_log_path", lambda *a, **k: None)
    monkeypatch.setattr(tools, "_plan_protocol_repetitions", lambda *a, **k: reservation.plan)
    monkeypatch.setattr(tools, "_channel_effects_for_later_phase", lambda *a, **k: {})
    monkeypatch.setattr(tools, "_set_channel_for_composite", lambda *a, **k: {})
    monkeypatch.setattr(tools, "_build_acquisition_events", lambda **k: [{"axes": {}}])

    def survey(*args, **kwargs):
        kwargs["acquire_hits"].append({"name": "hit", "x_um": 0, "y_um": 0, "z_um": 0})
        return {
            "status": "survey complete", "dataset_path": "/data/survey",
            "_acquire_reservation": reservation,
        }

    monkeypatch.setattr(tools, "_acquire_survey_with_detector", survey)
    monkeypatch.setattr(
        tools, "_acquire_with_hooks",
        lambda *a, **k: (_ for _ in ()).throw(tools.AcquisitionUnterminated(
            dataset_path="/data/acquire", frames_planned=1, frames_accounted=0,
            engine_exception=None, camera_sequence_running=False,
            teardown_running=True, expired_bound="runtime_ceiling", bound_s=1,
            fallback_ceiling=False,
        )),
    )
    with pytest.raises(tools.AcquisitionUnterminated) as caught:
        tools.run_adaptive_survey(
            _ctrl(False), _guard(), protocol="timelapse", save_dir="/data",
            hook_strategy="saved", positions=[{"name": "p0", "x_um": 0, "y_um": 0}],
            protocol_params={"n_frames": 1, "interval_s": 0, "channel": "DAPI"},
            acquire_on_hit={
                "channel": "DAPI", "protocol": "timelapse",
                "protocol_params": {"n_frames": 1, "interval_s": 0}, "max_hits": 1,
            },
        )
    assert reservation.ledger.in_flight is True
    # D3a: the survey phase finished before the acquire phase expired, so its
    # dataset is named and called readable. The path is the one the survey call
    # returned, never one discovered on disk.
    assert caught.value.positions_completed == [
        {"phase": "survey", "dataset_path": "/data/survey",
         "status": "survey complete"}
    ]
    report = tools._unterminated_result(caught.value)
    assert report["dataset_path"] == "/data/acquire"
    assert report["positions_completed"] == [
        {"phase": "survey", "dataset_path": "/data/survey",
         "status": "survey complete"}
    ]
    assert "readable now: /data/survey" in report["next"][0]


SUPERVISED_TOOL_NAMES = [
    name for name, fn in tools.TOOL_REGISTRY.items()
    if getattr(fn, "_microclaw_acquisition_entry_point", False) and name != "run_mda"
]

# The entry points whose acquisition is a per-position loop, so an expiry at
# position 2 leaves position 1 finished on disk. run_tile_acquisition and
# run_multiposition_with_autofocus both delegate to
# run_multiposition_acquisition, but the autofocus wrapper always forwards a
# hook_strategy, which runs ONE Acquisition spanning every position -- there is
# no completed sibling dataset for it to name.
LOOPING_COMPOSITES = {"run_multiposition_acquisition", "run_tile_acquisition"}


@pytest.mark.parametrize("name", SUPERVISED_TOOL_NAMES)
def test_every_supervised_entry_propagates_unterminated_without_continuing(
    name, monkeypatch, tmp_path,
):
    class FatalCountingAcquisition(BlockingAcquisition):
        constructions = 0
        def __init__(self, **kwargs):
            type(self).constructions += 1
            self.number = type(self).constructions
            super().__init__(**kwargs)
            self._dataset_disk_location = f"/data/run_{self.number}"
            self._event_queue = MagicMock()
            self._acq = MagicMock()
            self._acq.is_finished.return_value = False
        def acquire(self, events):
            super().acquire(events)
            if not self.completes:
                self._exception = RuntimeError("engine died")
        def __exit__(self, *_exc):
            # The looping composites finish their first position, so the report
            # has a completed dataset to name (D3a); every other entry point
            # runs one Acquisition and dies in it.
            if self.completes:
                return None
            return super().__exit__(*_exc)
        @property
        def completes(self):
            return name in LOOPING_COMPOSITES and self.number == 1

    monkeypatch.setattr(tools, "Acquisition", FatalCountingAcquisition)
    monkeypatch.setattr(tools, "ERROR_TEARDOWN_GRACE_S", 0.01)
    monkeypatch.setattr(tools, "_ACQUISITION_POLL_S", 0.001)
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)
    monkeypatch.setattr(tools, "_build_acquisition_events", lambda **_: [{"axes": {}}])
    monkeypatch.setattr(tools, "_configure_hook_capabilities", lambda *a, **k: None)
    monkeypatch.setattr(tools, "_plan_with_hook_dose", lambda plan, hook: plan)
    hook = SimpleNamespace()
    monkeypatch.setattr(tools, "_resolve_hook", lambda *a, **k: hook)
    monkeypatch.setattr(tools, "_resolve_hooks", lambda *a, **k: hook)
    monkeypatch.setattr(tools, "_prepare_log_path", lambda *a, **k: None)
    monkeypatch.setattr(tools, "_wait", lambda *a, **k: None)

    reservations = []
    def authorize(_ctrl, _guard, plan):
        reservation = AcquisitionLedger().reserve(MagicMock(), plan)
        reservations.append(reservation)
        return reservation
    monkeypatch.setattr(tools, "_authorize_acquisition", authorize)

    # Three positions, not two: expiry lands on the second, so the count below
    # distinguishes "the loop stopped" from "the loop ran out of positions".
    positions = [
        {"name": "p0", "x_um": 0, "y_um": 0},
        {"name": "p1", "x_um": 1, "y_um": 1},
        {"name": "p2", "x_um": 2, "y_um": 2},
    ]
    common = {"save_dir": str(tmp_path)}
    inputs = {
        "run_zstack": {
            **common, "z_start_um": 0, "z_end_um": 1, "z_step_um": 1,
        },
        "run_timelapse": {
            **common, "n_frames": 1, "interval_s": 0,
        },
        "run_multiposition_acquisition": {
            **common, "protocol": "timelapse", "positions": positions,
            "protocol_params": {"n_frames": 1, "interval_s": 0},
        },
        "run_tile_acquisition": {
            **common, "rows": 1, "cols": 3, "step_um": 1,
            "protocol": "timelapse",
            "protocol_params": {"n_frames": 1, "interval_s": 0},
        },
        "run_multiposition_with_autofocus": {
            **common, "positions": positions, "z_range_um": 2, "z_step_um": 1,
            "protocol": "timelapse",
            "protocol_params": {"n_frames": 1, "interval_s": 0},
        },
        "run_adaptive_survey": {
            **common, "protocol": "timelapse", "hook_strategy": "probe",
            "positions": positions,
            "protocol_params": {"n_frames": 1, "interval_s": 0},
        },
    }
    assert set(SUPERVISED_TOOL_NAMES) == set(inputs)
    ctrl = _ctrl(True)
    ctrl.core.get_image_width.return_value = 1
    ctrl.core.get_image_height.return_value = 1
    ctrl.core.get_bytes_per_pixel.return_value = 1
    ctrl.core.get_exposure.return_value = 1
    ctrl.core.get_x_position.return_value = 0
    ctrl.core.get_y_position.return_value = 0

    result = json.loads(tools.execute_tool(name, inputs[name], ctrl, _guard()))
    assert result.get("acquisition") == "unterminated", result
    looping = name in LOOPING_COMPOSITES
    assert result["dataset_path"] == ("/data/run_2" if looping else "/data/run_1")
    assert "frames_accounted" in result
    if looping:
        # D3a: the first position finished, so its dataset is named and called
        # readable. The failed acquisition's own path is run_2, above.
        assert result["positions_completed"] == [{
            "position": ("p0" if name == "run_multiposition_acquisition"
                         else "tile_r0_c0"),
            "dataset_path": "/data/run_1",
            "status": "Timelapse complete.",
            "x_um": (0.0 if name == "run_multiposition_acquisition" else -1.0),
            "y_um": 0.0,
        }]
        assert "readable now: /data/run_1" in result["next"][0]
    else:
        # A single acquisition finished nothing, and an empty list would be a
        # claim rather than a silence, so the key is absent entirely.
        assert "positions_completed" not in result
    assert reservations and reservations[0].ledger.in_flight is True
    assert FatalCountingAcquisition.constructions == (2 if looping else 1)
    BlockingAcquisition.release.set()


def test_completed_positions_are_projected_not_copied_so_the_report_survives():
    """A child result carries live objects; the report must still serialize.

    _unterminated_result is called from execute_tool's `except` clause and its
    return is json.dumps'd there, so anything non-serializable in the payload
    raises out of a function documented as never raising -- and the operator
    loses the whole unterminated report, dataset path included. Copying the
    child dict did exactly that: _acquire_survey_with_detector puts a live
    Reservation into a result under _acquire_reservation.
    """
    reservation = AcquisitionLedger().reserve(MagicMock(), AcquisitionPlan(1, 1, 1, 1))
    exc = tools.AcquisitionUnterminated(
        dataset_path="/data/p1", frames_planned=2, frames_accounted=1,
        engine_exception=None, camera_sequence_running=False,
        teardown_running=True, expired_bound="runtime_ceiling", bound_s=1,
        fallback_ceiling=False,
        positions_completed=[{
            "position": "p0", "x_um": 0.0, "y_um": 1.5,
            "status": "Timelapse complete.", "dataset_path": "/data/p0",
            "_acquire_reservation": reservation,
            "declared_illumination_properties": MagicMock(),
        }],
    )
    payload = json.loads(json.dumps(tools._unterminated_result(exc)))
    assert payload["positions_completed"] == [{
        "position": "p0", "dataset_path": "/data/p0",
        "status": "Timelapse complete.", "x_um": 0.0, "y_um": 1.5,
    }]
    assert "readable now: /data/p0" in payload["next"][0]
    assert payload["dataset_path"] == "/data/p1"


def test_unterminated_result_tolerates_every_partial_payload_it_can_receive():
    """It runs on the error path, so no input may make it raise."""
    def _exc(positions_completed):
        return tools.AcquisitionUnterminated(
            dataset_path="/data/run", frames_planned=None, frames_accounted=0,
            engine_exception=None, camera_sequence_running=None,
            teardown_running=True, expired_bound="error_grace", bound_s=90,
            fallback_ceiling=True, positions_completed=positions_completed,
        )
    for payload in (None, [], [{}], [None], ["not a dict"],
                    [{"dataset_path": None, "x_um": "not a number"}]):
        result = tools._unterminated_result(_exc(payload))
        assert "positions_completed" not in result, payload
        json.dumps(result)
    # A record with nothing but a path is still worth reporting.
    result = tools._unterminated_result(_exc([{"dataset_path": "/data/p0"}]))
    assert result["positions_completed"] == [{"dataset_path": "/data/p0"}]
    assert result["next"][0].startswith("1 position finished before this failure. "
                                        "That dataset is")
    result = tools._unterminated_result(_exc([{"dataset_path": "/data/p0"},
                                              {"dataset_path": "/data/p1"}]))
    assert result["next"][0].startswith("2 positions finished before this failure. "
                                        "Those datasets are")
    assert result["next"][0].endswith("readable now: /data/p0, /data/p1")


def test_position_dominated_run_keeps_producing_past_ceiling_and_completes(monkeypatch):
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    clock = SimulatedClock(saved_at=range(120, 1081, 120), release_at=1200)
    _install_clock(monkeypatch, clock)
    plan = AcquisitionPlan(5500, 100, 550, 5500)
    assert tools._runtime_ceiling_s(plan) == (850, False)
    assert _call_acquire(_ctrl(False), _guard(), "/data", "run", [], plan=plan) == "/data/run_1"
    assert clock.now >= 1200  # the 850 s unamended ceiling was crossed


def test_minutes_between_frames_self_calibrate_beyond_the_floor(monkeypatch):
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    clock = SimulatedClock(saved_at=[250, 500], release_at=1500)
    _install_clock(monkeypatch, clock)
    plan = AcquisitionPlan(2, 1, 0, 2)
    assert tools._stall_quiet_s(250) == 1250
    assert tools._stall_quiet_s(250) > tools.STALL_QUIET_FLOOR_S
    assert _call_acquire(_ctrl(False), _guard(), "/data", "run", [], plan=plan) == "/data/run_1"


def test_stall_fires_only_after_ceiling_and_observed_gap_window(monkeypatch):
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)
    clock = SimulatedClock(saved_at=[100, 350])
    _install_clock(monkeypatch, clock)

    @_entry
    def run(ctrl, guard):
        return _call_acquire(
            ctrl, guard, "/data", "run", [],
            plan=AcquisitionPlan(2, 1, 0, 2),
        )

    result = json.loads(tools.execute_tool("run", {}, _ctrl(False), _guard(), {"run": run}))
    assert result["acquisition"] == "unterminated"
    assert result["expired_bound"] == "runtime_ceiling"
    assert result["frames_accounted"] == 2
    assert clock.now == 1600  # last frame 350 + 5 * observed 250 s gap
    BlockingAcquisition.release.set()


def test_floor_and_gap_multiplier_are_each_load_bearing(monkeypatch):
    assert tools._stall_quiet_s(100) == 900
    monkeypatch.setattr(tools, "STALL_QUIET_FLOOR_S", 400)
    assert tools._stall_quiet_s(100) == 500
    monkeypatch.setattr(tools, "STALL_GAP_MULTIPLIER", 3)
    assert tools._stall_quiet_s(100) == 400


def test_runtime_and_fallback_ceiling_expire_without_frames(monkeypatch):
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    monkeypatch.setattr(tools, "STALL_QUIET_FLOOR_S", 30, raising=False)
    monkeypatch.setattr(tools, "FALLBACK_RUNTIME_CEILING_S", 20, raising=False)
    clock = SimulatedClock(step=10)
    if hasattr(tools, "_acquisition_monotonic"):
        _install_clock(monkeypatch, clock)
    expected = getattr(tools, "AcquisitionUnterminated", Exception)
    with pytest.raises(expected) as caught:
        _call_acquire(_ctrl(False), _guard(), "/data", "run", [])
    assert caught.value.expired_bound == "runtime_ceiling"
    assert caught.value.fallback_ceiling is True
    assert tools._unterminated_result(caught.value)["ceiling_fallback"] is True
    BlockingAcquisition.release.set()


def test_interval_driven_plan_ceiling_tracks_real_duration():
    ctrl = MagicMock()
    ctrl.core.get_image_width.return_value = 1
    ctrl.core.get_image_height.return_value = 1
    ctrl.core.get_bytes_per_pixel.return_value = 2
    events = [
        {"min_start_time": 0},
        {"min_start_time": 8 * 60 * 60},
        {"min_start_time": 16 * 60 * 60},
    ]
    plan = tools.plan_events(ctrl, events, exposure_ms=100)
    ceiling, fallback = tools._runtime_ceiling_s(plan)
    assert plan.estimated_duration_s == pytest.approx(16 * 60 * 60 + 0.1)
    assert ceiling == pytest.approx(plan.estimated_duration_s * 1.5)
    assert fallback is False
    assert tools._stall_quiet_s(8 * 60 * 60) == 40 * 60 * 60


@pytest.mark.parametrize("camera", [True, False])
def test_measured_camera_state_selects_distinct_recovery_text(camera):
    exc = tools.AcquisitionUnterminated(
        dataset_path="/d", frames_planned=1, frames_accounted=0,
        engine_exception=None, camera_sequence_running=camera,
        teardown_running=True, expired_bound="runtime_ceiling", bound_s=1,
        fallback_ceiling=False,
    )
    result = tools._unterminated_result(exc)
    assert ("sequence was still running" in result["hardware"]) == camera
    assert ("measured idle" in result["hardware"]) == (not camera)
    assert result["next"]


@pytest.mark.parametrize(
    "name",
    [name for name, fn in tools.TOOL_REGISTRY.items()
     if getattr(fn, "_microclaw_acquisition_entry_point", False)],
)
def test_every_marked_acquisition_entry_point_is_refused_while_teardown_lives(name):
    ctrl = _ctrl(False)
    waiter = MagicMock()
    waiter.is_alive.return_value = True
    ctrl._microclaw_unterminated_acquisition = {"waiter": waiter}
    result = json.loads(tools.execute_tool(name, {}, ctrl, _guard()))
    assert result["acquisition"] == "refused"


def test_acquisition_refusal_lifts_only_when_camera_and_teardown_are_clear(monkeypatch):
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)
    @_entry
    def run(ctrl, guard):
        return {"ok": True}
    ctrl = _ctrl(False)
    waiter = MagicMock()
    waiter.is_alive.return_value = False
    ctrl._microclaw_unterminated_acquisition = {"waiter": waiter}
    assert json.loads(tools.execute_tool("run", {}, ctrl, _guard(), {"run": run})) == {"ok": True}
    assert not hasattr(ctrl, "_microclaw_unterminated_acquisition")


def test_browser_acquisition_sink_records_to_stderr_when_no_turn_is_bound(capsys):
    from microclaw.webserve import Session
    session = object.__new__(Session)
    session._emit = None
    session.emit_acquisition_event({"type": "acquisition_diagnostic"})
    assert "acquisition_diagnostic" in capsys.readouterr().err


def test_reservationless_progress_is_rate_limited_and_delivered_through_sink(
    monkeypatch,
):
    class CallbackAcquisition:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._dataset_disk_location = "/data/progress"
            self._exception = None

        def acquire(self, events):
            for _ in events:
                self.kwargs["image_saved_fn"]({}, object())

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(tools, "Acquisition", CallbackAcquisition)
    monkeypatch.setattr(tools, "_acquisition_monotonic", lambda: 10.0)
    events = [{"axes": {"time": i}} for i in range(100)]
    plan = AcquisitionPlan(100, 1, 0.1, 100)
    received = []
    previous = getattr(tools._ACQUISITION_EVENT_CONTEXT, "sink", None)
    tools._ACQUISITION_EVENT_CONTEXT.sink = received.append
    try:
        result = _call_acquire(
            _ctrl(False), _guard(), "/data", "progress", events, None,
            reservation=None, plan=plan,
        )
    finally:
        tools._ACQUISITION_EVENT_CONTEXT.sink = previous
    assert result == "/data/progress"
    assert [event["frames_accounted"] for event in received] == [1, 100]
    assert all(event["type"] == "acquisition_progress" for event in received)


def test_hookless_timelapse_emitter_remains_a_bare_acquisition_context():
    source = tools.run_timelapse._microclaw_emitter({
        "n_frames": 2, "interval_s": 0, "save_dir": "/data",
    })
    assert "with Acquisition(" in source
    assert "Thread" not in source and "await_completion" not in source
