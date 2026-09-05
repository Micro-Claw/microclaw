from dataclasses import replace
import json
import inspect
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from microclaw import tools
from microclaw.acquisition import AcquisitionLedger, AcquisitionPlan
from microclaw.conversation import AuditLog


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
    kwargs.setdefault("policy", tools.DEFAULT)
    return tools._acquire_with_hooks(*args, ctrl=ctrl, **kwargs)


class SimulatedClock:
    def __init__(self, *, step=100.0, saved_at=(), release_at=None, limit=None):
        self.now = 0.0
        self.step = step
        self.saved_at = list(saved_at)
        self.release_at = release_at
        self.limit = limit

    def __call__(self):
        return self.now

    def join(self, waiter, _timeout):
        target = self.now + self.step
        if self.limit is not None and target > self.limit:
            BlockingAcquisition.release.set()
            waiter.join(1)
            raise AssertionError("supervisor exceeded test delivery ceiling")
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
        "phase": "acquiring_or_notifying",
        "diagnostic_persisted": False,
        "data": result["data"],
        "engine_exception": "ValueError: bad notification",
        "dataset_path": "/data/run_1",
        "frames_planned": 3,
        "frames_accounted": 1,
        "camera_sequence_running": True,
        "teardown_running": True,
        "expired_bound": "error_grace",
        "bound_s": 0.02,
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
    assert tools._runtime_ceiling_s(plan, policy=tools.DEFAULT) == (850, False, "plan_plus_300_s")
    assert _call_acquire(_ctrl(False), _guard(), "/data", "run", [], plan=plan) == "/data/run_1"
    assert clock.now >= 1200  # the 850 s unamended ceiling was crossed


def test_minutes_between_frames_self_calibrate_beyond_the_floor(monkeypatch):
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    clock = SimulatedClock(saved_at=[250, 500], release_at=1500)
    _install_clock(monkeypatch, clock)
    plan = AcquisitionPlan(2, 1, 0, 2)
    assert tools._stall_quiet_s(250, policy=tools.DEFAULT) == (1250, "observed_gap")
    assert tools._stall_quiet_s(250, policy=tools.DEFAULT)[0] > tools.DEFAULT.quiet_floor_s
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
    assert tools._stall_quiet_s(100, policy=tools.DEFAULT) == (900, "quiet_floor")
    monkeypatch.setattr(tools, "DEFAULT", replace(tools.DEFAULT, quiet_floor_s=400))
    assert tools._stall_quiet_s(100, policy=tools.DEFAULT) == (500, "observed_gap")
    monkeypatch.setattr(tools, "STALL_GAP_MULTIPLIER", 3)
    assert tools._stall_quiet_s(100, policy=tools.DEFAULT) == (400, "quiet_floor")


def test_runtime_and_fallback_ceiling_expire_without_frames(monkeypatch):
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    monkeypatch.setattr(tools, "DEFAULT", replace(tools.DEFAULT, quiet_floor_s=30))
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
    ceiling, fallback, term = tools._runtime_ceiling_s(plan, policy=tools.DEFAULT)
    assert plan.estimated_duration_s == pytest.approx(16 * 60 * 60 + 0.1)
    assert ceiling == pytest.approx(plan.estimated_duration_s * 1.5)
    assert fallback is False
    assert term == "plan_times_1_5"
    assert tools._stall_quiet_s(8 * 60 * 60, policy=tools.DEFAULT) == (40 * 60 * 60, "observed_gap")


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


def test_cli_diagnostic_still_reaches_stderr_when_writer_is_installed(tmp_path, capsys):
    from microclaw.conversation import AcquisitionDiagnosticWriter

    writer = AcquisitionDiagnosticWriter(AuditLog(tmp_path / "acq.jsonl"))
    with tools._acquisition_diagnostic_context({"diagnostic_writer": writer}):
        tools._emit_acquisition_diagnostic({
            "type": "acquisition_progress", "frames_accounted": 1,
        })
    writer.close()
    assert "acquisition_progress" in capsys.readouterr().err


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
    class AdvancingClock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            self.now += 0.04
            return self.now

    monkeypatch.setattr(tools, "_acquisition_monotonic", AdvancingClock())
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
    counts = [event["frames_accounted"] for event in received]
    assert counts[0] == 1
    assert counts[-1] == 100
    assert 4 <= len(counts) <= 6
    assert len(counts[1:-1]) >= 2
    assert all(later - earlier >= 20 for earlier, later in zip(counts, counts[1:-1]))
    assert len(counts) < len(events) / 10
    assert all(event["type"] == "acquisition_progress" for event in received)


def test_hookless_timelapse_emitter_remains_a_bare_acquisition_context():
    source = tools.run_timelapse._microclaw_emitter({
        "n_frames": 2, "interval_s": 0, "save_dir": "/data",
    })
    assert "with Acquisition(" in source
    assert "Thread" not in source and "await_completion" not in source


def test_saved_frame_callback_never_calls_or_waits_for_audit_log(monkeypatch, tmp_path):
    from microclaw.conversation import AcquisitionDiagnosticWriter
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)
    entered = threading.Event()
    release = threading.Event()
    append_threads = []

    class SlowFsyncAudit(AuditLog):
        def append(self, message):
            append_threads.append(threading.current_thread().name)
            entered.set()
            release.wait()
            return super().append(message)

    class BurstAcquisition:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._dataset_disk_location = "/data/burst"
            self._exception = None

        def acquire(self, events):
            for _ in events:
                self.kwargs["image_saved_fn"]({}, object())

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(tools, "Acquisition", BurstAcquisition)
    writer = AcquisitionDiagnosticWriter(SlowFsyncAudit(tmp_path / "acq.jsonl"))
    done = threading.Event()

    def drive():
        tools.execute_tool(
            "run", {}, _ctrl(False), _guard(),
            {"run": _entry(lambda ctrl, guard: _call_acquire(
                ctrl, guard, "/data", "burst", [{}] * 100,
                plan=AcquisitionPlan(100, 1, 1, 100),
            ))},
            acquisition_diagnostic_writer=writer,
            acquisition_session_id="session",
            tool_call_id="tool-1",
        )
        done.set()

    caller = threading.Thread(target=drive, name="tool-caller", daemon=True)
    caller.start()
    try:
        assert entered.wait(1)
        assert done.wait(0.5), "saved-frame callbacks waited for the blocked fsync"
        assert append_threads == ["microclaw-acquisition-diagnostics"]
    finally:
        release.set()
        caller.join(1)
        writer.close()


def test_complete_acquisition_records_ordered_lifecycle_and_correlation(monkeypatch, tmp_path):
    from microclaw.conversation import AcquisitionDiagnosticWriter
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)
    class CompleteAcquisition:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._dataset_disk_location = "/data/complete"
            self._exception = None

        def acquire(self, events):
            for _ in events:
                self.kwargs["image_saved_fn"]({}, object())

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(tools, "Acquisition", CompleteAcquisition)
    audit = AuditLog(tmp_path / "acq.jsonl")
    writer = AcquisitionDiagnosticWriter(audit)

    @_entry
    def run(ctrl, guard):
        return _call_acquire(
            ctrl, guard, "/data", "complete", [{}, {}],
            plan=AcquisitionPlan(2, 5, 0.01, 2048),
        )

    tools.execute_tool(
        "run", {}, _ctrl(False), _guard(), {"run": run},
        acquisition_diagnostic_writer=writer,
        acquisition_session_id="20260904_microclaw_history",
        tool_call_id="toolu_abc",
    )
    writer.close()
    lifecycle = [record for record in audit.records if record["type"] != "acquisition_progress"]
    assert [record["type"] for record in lifecycle] == [
        "acquisition_construction", "acquisition_event_submission",
        "acquisition_first_frame_accounted",
        "acquisition_planned_final_frame_accounted",
        "acquisition_mark_finished", "acquisition_teardown_completion",
    ]
    assert all(record["session_id"] == "20260904_microclaw_history" for record in lifecycle)
    assert all(record["tool_call_id"] == "toolu_abc" for record in lifecycle)
    assert [record["timestamp"] for record in lifecycle] == sorted(
        record["timestamp"] for record in lifecycle
    )
    construction = lifecycle[0]
    assert construction["estimated_bytes"] == 2048
    assert construction["active_bound_term"] == "plan_plus_300_s"


def test_timeout_diagnostic_records_bound_term_and_camera_state(monkeypatch, tmp_path):
    from microclaw.conversation import AcquisitionDiagnosticWriter
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    _install_clock(monkeypatch, SimulatedClock(step=1000))
    audit = AuditLog(tmp_path / "acq.jsonl")
    writer = AcquisitionDiagnosticWriter(audit)
    plan = AcquisitionPlan(1, 1, 1, 123)
    with pytest.raises(tools.AcquisitionUnterminated):
        with tools._acquisition_diagnostic_context({
            "diagnostic_writer": writer, "session_id": "s", "tool_call_id": "t",
        }):
            _call_acquire(_ctrl(True), _guard(), "/data", "timeout", [], plan=plan)
    writer.close()
    timeout = next(record for record in audit.records if record["type"] == "acquisition_timeout")
    assert timeout["active_bound"] == "runtime_ceiling"
    assert timeout["active_bound_term"] == "plan_plus_300_s"
    assert timeout["quiet_bound_term"] == "quiet_floor"
    assert timeout["camera_sequence_running"] is True
    BlockingAcquisition.release.set()


@pytest.mark.parametrize("save", [True, False])
def test_cli_acquisition_file_is_history_sibling_and_honors_save_flag(
    monkeypatch, tmp_path, save,
):
    from microclaw import __main__ as cli, credentials, updates

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "load_safety_config_or_exit", lambda _: SimpleNamespace(
        constraints=MagicMock()
    ))
    monkeypatch.setattr(cli, "SafetyGuard", lambda _: _guard())
    ctrl = _ctrl(False)
    ctrl.is_connected.return_value = True
    monkeypatch.setattr(cli, "MicroscopeController", lambda **_: ctrl)
    monkeypatch.setattr(cli, "validate_live_rig", lambda *_a, **_k: None)
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("key", "env"))
    monkeypatch.setattr("microclaw.agent.set_api_key", lambda *_: None)
    monkeypatch.setattr(updates, "start_due_check", lambda *_: None)
    monkeypatch.setattr(updates, "terminal_update_notice", lambda: (None, None))
    inputs = iter(["go", "exit"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

    def fake_agent(_message, _ctrl, _guard, history, **kwargs):
        kwargs["acquisition_diagnostic_writer"].submit({
            "type": "probe", "session_id": kwargs["acquisition_session_id"],
            "tool_call_id": "tool-cli",
        }, lifecycle=True)
        return "ok", history

    monkeypatch.setattr(cli, "run_agent", fake_agent)
    cli.run_session(SimpleNamespace(
        safety_config=None, port=1, save_history=save,
        history_retention_days=None, profile=False, model=None,
        no_update_check=True,
    ))
    files = list(tmp_path.glob("*_microclaw_acquisitions.jsonl"))
    assert bool(files) is save
    if save:
        record = json.loads(files[0].read_text(encoding="utf-8").strip())
        assert record["tool_call_id"] == "tool-cli"
        assert record["session_id"] == files[0].name.replace(
            "_acquisitions.jsonl", "_history"
        )


@pytest.mark.parametrize("save", [True, False])
def test_web_session_wires_correlated_acquisition_file(monkeypatch, tmp_path, save):
    from fastapi.testclient import TestClient
    from microclaw import credentials, webserve
    from microclaw.webserve import Session, SessionMode, build_app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("key", "env"))
    session = object.__new__(Session)
    Session._initialize(session, SimpleNamespace(
        model=None, save_history=save, history_retention_days=None,
        host="127.0.0.1",
    ))
    session.ctrl = _ctrl(False)
    session.guard = _guard()
    session.mode = SessionMode.NORMAL
    session.tool_schemas = []
    session.tool_registry = {}

    def fake_agent_iter(_msg, ctrl, guard, _history, _model, **kwargs):
        @_entry
        def probe(ctrl, guard):
            tools._emit_acquisition_diagnostic(
                {"type": "probe"}, lifecycle=True, publish=False,
            )
            return {"ok": True}

        tools.execute_tool(
            "probe", {}, ctrl, guard, {"probe": probe},
            acquisition_diagnostic_writer=kwargs["acquisition_diagnostic_writer"],
            acquisition_session_id=kwargs["acquisition_session_id"],
            tool_call_id="tool-web",
        )
        yield {"type": "done", "reply": "ok"}

    monkeypatch.setattr(webserve, "run_agent_iter", fake_agent_iter)
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)
    with TestClient(build_app(session)) as client:
        assert client.post("/api/prompt", json={"message": "probe"}).status_code == 200

    files = list(tmp_path.glob("*_microclaw_acquisitions.jsonl"))
    assert bool(files) is save
    if save:
        record = json.loads(files[0].read_text(encoding="utf-8").strip())
        assert record["tool_call_id"] == "tool-web"
        assert record["session_id"] == session.acquisition_session_id
        assert files[0].name.replace(
            "_acquisitions.jsonl", "_history.jsonl"
        ) == session.history_fn


@pytest.mark.parametrize("saved", [False, True])
def test_short_fixed_timeout_survives_missing_notification(monkeypatch, saved):
    from microclaw.conversation import DIAGNOSTIC_FLUSH_GRACE_S

    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)
    confirm = MagicMock(side_effect=AssertionError("supervision must not confirm"))
    monkeypatch.setattr(tools, "CONFIRM_FN", confirm)
    plan = AcquisitionPlan(1, 50, 0.05, 2)
    monkeypatch.setattr(tools, "plan_events", lambda *a, **k: plan)
    monkeypatch.setattr(tools, "_build_acquisition_events", lambda **k: [{}])
    reservation = AcquisitionLedger().reserve(MagicMock(), plan)
    monkeypatch.setattr(tools, "_authorize_acquisition", lambda *a: reservation)
    clock = SimulatedClock(step=tools._ACQUISITION_POLL_S,
                           saved_at=[0.28] if saved else [],
                           limit=max(tools.SHORT_FIXED.quiet_floor_s + 0.28,
                                     tools.SHORT_FIXED.runtime_slack_s + 0.05)
                           + tools._ACQUISITION_POLL_S + tools.CAMERA_STATE_PROBE_GRACE_S
                           + DIAGNOSTIC_FLUSH_GRACE_S + 1)
    _install_clock(monkeypatch, clock)
    received = []
    ctrl = _ctrl(False)
    try:
        result = json.loads(tools.execute_tool(
            "run_timelapse", {"n_frames": 1, "interval_s": 0, "save_dir": "/data"},
            ctrl, _guard(), acquisition_event_sink=received.append,
        ))
    finally:
        BlockingAcquisition.release.set()
        flag = getattr(ctrl, "_microclaw_unterminated_acquisition", None)
        if isinstance(flag, dict):
            flag["waiter"].join(1)
    confirm.assert_not_called()
    policy = tools.SHORT_FIXED
    expiry = max(plan.estimated_duration_s * 1.5,
                 plan.estimated_duration_s + policy.runtime_slack_s,
                 (0.28 if saved else 0) + policy.quiet_floor_s)
    ceiling = (expiry + tools._ACQUISITION_POLL_S + tools.CAMERA_STATE_PROBE_GRACE_S
               + DIAGNOSTIC_FLUSH_GRACE_S)
    assert expiry <= clock.now <= ceiling + 0.1
    assert result["acquisition"] == "unterminated", result
    assert result["frames_accounted"] == int(saved)
    assert result["frames_planned"] == 1
    assert result["expired_bound"] == "short_fixed_runtime"
    # The neutral runtime wording names no number, so the bound has to be a
    # field or the operator never learns what expired.
    assert result["bound_s"] == pytest.approx(
        max(plan.estimated_duration_s * 1.5,
            plan.estimated_duration_s + policy.runtime_slack_s)
    )
    assert result["phase"] == ("finalizing" if saved else "acquiring_or_notifying")
    assert result["error"] == "The acquisition did not terminate within its supervised runtime bound."
    assert f"{int(saved)} of 1 planned frames accounted" in result["data"]
    assert "/data/run_1" in result["data"] and "may be unterminated" in result["data"]
    assert "saved" not in result["data"]
    assert result["camera_sequence_running"] is False
    assert result["teardown_running"] is True
    timeout = next(e for e in received if e["type"] == "acquisition_timeout")
    assert timeout["phase"] == result["phase"]
    assert timeout["active_bound_term"] == f"plan_plus_{policy.runtime_slack_s:g}_s"


def test_supervisor_call_sites_are_enumerated_and_explicit(monkeypatch):
    import ast
    from pathlib import Path

    tree = ast.parse(Path(tools.__file__).read_text(encoding="utf-8"))
    calls = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_acquire_with_hooks"):
            owner = next(f.name for f in tree.body if isinstance(f, ast.FunctionDef)
                         and f.lineno <= node.lineno <= f.end_lineno)
            calls.setdefault(owner, []).append(
                ast.unparse(next(k.value for k in node.keywords if k.arg == "policy")))
    assert calls == {
        "run_zstack": ["DEFAULT"], "run_timelapse": ["policy"],
        "_acquire_positions_with_hook": ["DEFAULT"],
        "_acquire_survey_with_detector": ["DEFAULT"],
        "run_adaptive_survey": ["DEFAULT"],
    }
    monkeypatch.setattr(tools, "Acquisition", MagicMock(side_effect=AssertionError("missing policy reached construction")))
    with pytest.raises(TypeError, match="policy"):
        tools._acquire_with_hooks(_guard(), "/data", "missing", [], ctrl=_ctrl())


@pytest.mark.parametrize("shape", ["single", "multiple", "hook", "plan", "composite", "factory", "adaptive", "positions"])
def test_callers_select_policy_after_event_and_hook_construction(monkeypatch, shape):
    events = [{}] * (2 if shape == "multiple" else 1)
    if shape == "factory":
        events = lambda acq: iter([{}])
    monkeypatch.setattr(tools, "_build_acquisition_events", lambda **k: events)
    plan = AcquisitionPlan(1, 1, 0.001, 1)
    monkeypatch.setattr(tools, "plan_events", lambda *a, **k: plan)
    monkeypatch.setattr(tools, "_authorize_acquisition", lambda *a: MagicMock())
    monkeypatch.setattr(tools, "_resolve_hook", lambda *a, **k: MagicMock())
    monkeypatch.setattr(tools, "_prepare_log_path", lambda *a, **k: None)
    monkeypatch.setattr(tools, "_configure_hook_capabilities", lambda *a, **k: None)
    monkeypatch.setattr(tools, "_plan_with_hook_dose", lambda p, h: p)
    selected = []
    class ReachedSupervisor(Exception): pass
    def acquire(*a, **k):
        selected.append(k["policy"])
        raise ReachedSupervisor
    monkeypatch.setattr(tools, "_acquire_with_hooks", acquire)
    kwargs = {}
    if shape in ("hook", "adaptive"):
        kwargs["hook_strategy"] = "probe"
    if shape == "plan":
        kwargs["hook_action_plan"] = []
    if shape == "composite":
        kwargs["_reservation"] = MagicMock()
    if shape == "adaptive":
        kwargs["max_frames"] = 1
    with pytest.raises(ReachedSupervisor):
        if shape == "positions":
            tools._acquire_positions_with_hook(
                _ctrl(False), _guard(),
                [{"name": f"p{i}", "x_um": i, "y_um": i} for i in range(1000)],
                "/data", "positions", hook_strategy="probe", num_time_points=1,
            )
        tools.run_timelapse(_ctrl(False), _guard(),
                            n_frames=None if shape == "adaptive" else len(events) if isinstance(events, list) else 1,
                            interval_s=1, save_dir="/data", **kwargs)
    assert selected == [tools.SHORT_FIXED if shape == "single" else tools.DEFAULT]


@pytest.mark.parametrize("terminal", [None, 2])
def test_phase_transition_is_policy_owned_and_bypasses_cadence(monkeypatch, terminal):
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    clock = SimulatedClock(step=0.1, saved_at=[0.1, 0.2], release_at=0.3)
    _install_clock(monkeypatch, clock)
    # Terminal promise deliberately differs from the cap, so neither the cap
    # nor the pre-existing planned-final emit can accidentally satisfy this.
    policy = replace(tools.DEFAULT, terminal_frames=terminal)
    received = []
    with tools._acquisition_diagnostic_context({"sink": received.append}):
        _call_acquire(_ctrl(False), _guard(), "/data", "phase", [],
                      plan=AcquisitionPlan(3 if terminal else 2, 1, 0.003, 3), policy=policy)
    progress = [e for e in received if e["type"] == "acquisition_progress"]
    assert [e["phase"] for e in progress] == (["acquiring", "finalizing"] if terminal else ["acquiring", "acquiring"])
    for event in progress:
        assert event["active_bound_s"] == 0.003 + policy.runtime_slack_s
        assert event["active_bound_term"] == f"plan_plus_{policy.runtime_slack_s:g}_s"
        assert event["dataset_path"] == "/data/run_1"


def test_short_policy_observed_gap_widens_window(monkeypatch):
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    policy = tools.SHORT_FIXED
    gap = policy.quiet_floor_s / 2
    clock = SimulatedClock(step=0.1, saved_at=[0.1, 0.1 + gap],
                           release_at=0.1 + gap + policy.quiet_floor_s + 0.5)
    _install_clock(monkeypatch, clock)
    assert tools._stall_quiet_s(gap, policy) == (tools.STALL_GAP_MULTIPLIER * gap, "observed_gap")
    try:
        result = _call_acquire(_ctrl(False), _guard(), "/data", "slow", [],
                               plan=AcquisitionPlan(2, 1, 0.002, 2), policy=policy)
    finally:
        BlockingAcquisition.release.set()
    assert result == "/data/run_1"


@pytest.mark.parametrize("error_first", [False, True])
def test_error_after_terminal_frame_keeps_earliest_bound(monkeypatch, error_first):
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    policy = tools.SHORT_FIXED
    plan = AcquisitionPlan(1, 1, 0.001, 1)
    clock = SimulatedClock(step=0.1)
    _install_clock(monkeypatch, clock)
    monkeypatch.setattr(tools, "ERROR_TEARDOWN_GRACE_S",
                        policy.runtime_slack_s / 2 if error_first else policy.runtime_slack_s * 3)
    def events(acq):
        acq.kwargs["image_saved_fn"]({}, None)
        acq._exception = ValueError("notification failed after frame")
        return [{}]
    try:
        with pytest.raises(tools.AcquisitionUnterminated) as caught:
            _call_acquire(_ctrl(False), _guard(), "/data", "error", events,
                          plan=plan, policy=policy)
    finally:
        BlockingAcquisition.release.set()
    result = tools._unterminated_result(caught.value)
    assert result["expired_bound"] == ("error_grace" if error_first else policy.bound_name)
    assert result["phase"] == "finalizing"
    assert result["engine_exception"] == "ValueError: notification failed after frame"
    expected = min(tools.ERROR_TEARDOWN_GRACE_S,
                   max(plan.estimated_duration_s + policy.runtime_slack_s, policy.quiet_floor_s))
    assert expected <= clock.now <= expected + tools._ACQUISITION_POLL_S + 0.1


@pytest.mark.parametrize("late", [False, True])
def test_short_waiter_completion_owns_cleanup_once_and_refusal_lifetime(monkeypatch, late):
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)
    policy = tools.SHORT_FIXED
    bound = max(policy.quiet_floor_s, policy.runtime_slack_s + 0.001)
    clock = SimulatedClock(step=0.1, release_at=None if late else bound - 0.2)
    _install_clock(monkeypatch, clock)
    ctrl = _ctrl(False)
    del ctrl._microclaw_unterminated_acquisition
    reservation = MagicMock()
    reservation.plan = AcquisitionPlan(1, 1, 0.001, 1)
    run = _entry(lambda ctrl, guard: {"available": True})
    try:
        if late:
            with pytest.raises(tools.AcquisitionUnterminated):
                _call_acquire(ctrl, _guard(), "/data", "late", [], reservation=reservation,
                              close_reservation=False, plan=reservation.plan, policy=policy)
            reservation.close.assert_not_called()
            refused = json.loads(tools.execute_tool("run", {}, ctrl, _guard(), {"run": run}))
            assert refused["acquisition"] == "refused"
        else:
            assert _call_acquire(ctrl, _guard(), "/data", "normal", [], reservation=reservation,
                                 plan=reservation.plan, policy=policy) == "/data/run_1"
            assert not hasattr(ctrl, "_microclaw_unterminated_acquisition")
    finally:
        BlockingAcquisition.release.set()
        flag = getattr(ctrl, "_microclaw_unterminated_acquisition", None)
        if isinstance(flag, dict): flag["waiter"].join(1)
    reservation.close.assert_called_once()
    assert json.loads(tools.execute_tool("run", {}, ctrl, _guard(), {"run": run})) == {"available": True}
    assert not hasattr(ctrl, "_microclaw_unterminated_acquisition")


def test_hung_camera_probe_is_bounded_after_pending_and_exception(monkeypatch):
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    _install_clock(monkeypatch, SimulatedClock(step=1))
    ctrl = _ctrl(False)
    release = threading.Event()
    observed = []
    original = tools.AcquisitionUnterminated.__init__
    def init(self, **kwargs):
        original(self, **kwargs)
        observed.append(self)
    monkeypatch.setattr(tools.AcquisitionUnterminated, "__init__", init)
    def probe():
        observed.append(isinstance(ctrl._microclaw_unterminated_acquisition, dict))
        release.wait(2 * tools.CAMERA_STATE_PROBE_GRACE_S + 1)
        return False
    ctrl.core.is_sequence_running.side_effect = probe
    started = time.monotonic()
    try:
        with pytest.raises(tools.AcquisitionUnterminated) as caught:
            _call_acquire(ctrl, _guard(), "/data", "probe", [],
                          plan=AcquisitionPlan(1, 1, 0.001, 1), policy=tools.SHORT_FIXED)
    finally:
        release.set()
        BlockingAcquisition.release.set()
    elapsed = time.monotonic() - started
    assert isinstance(observed[0], tools.AcquisitionUnterminated)
    assert observed[1] is True
    assert elapsed <= tools.CAMERA_STATE_PROBE_GRACE_S + 0.5
    assert tools._unterminated_result(caught.value)["camera_sequence_running"] is None


@pytest.mark.parametrize("blocked", [False, True])
def test_timeout_diagnostic_acknowledgement_precedes_result(monkeypatch, tmp_path, capsys, blocked):
    from microclaw.conversation import AcquisitionDiagnosticWriter, DIAGNOSTIC_FLUSH_GRACE_S
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    _install_clock(monkeypatch, SimulatedClock(step=1))
    release = threading.Event()
    class Audit(AuditLog):
        def append(self, message):
            if blocked:
                release.wait(2 * DIAGNOSTIC_FLUSH_GRACE_S + 1)
            elif message["type"] == "acquisition_timeout":
                # Make a missing acknowledge=True observably return before fsync.
                time.sleep(0.05)
            return super().append(message)
    path = tmp_path / "acq.jsonl"
    writer = AcquisitionDiagnosticWriter(Audit(path))
    received = []
    started = time.monotonic()
    try:
        with tools._acquisition_diagnostic_context({"diagnostic_writer": writer, "sink": received.append}):
            with pytest.raises(tools.AcquisitionUnterminated) as caught:
                _call_acquire(_ctrl(False), _guard(), "/data", "ack", [],
                              plan=AcquisitionPlan(1, 1, 0.001, 1), policy=tools.SHORT_FIXED)
        elapsed = time.monotonic() - started
        on_disk = path.read_text(encoding="utf-8") if path.exists() else ""
        stderr = capsys.readouterr().err
    finally:
        release.set()
        BlockingAcquisition.release.set()
        writer.close()
    assert tools._unterminated_result(caught.value)["diagnostic_persisted"] is (not blocked)
    assert elapsed <= DIAGNOSTIC_FLUSH_GRACE_S + 0.5
    if blocked:
        assert "acquisition_timeout" in stderr
        assert any(e["type"] == "acquisition_timeout" for e in received)
        assert "acquisition_timeout" not in on_disk
    else:
        assert any(json.loads(line)["type"] == "acquisition_timeout"
                   for line in on_disk.splitlines())


def test_expired_bound_uses_explicit_policy_name(monkeypatch):
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    _install_clock(monkeypatch, SimulatedClock(step=1))
    policy = replace(tools.SHORT_FIXED, bound_name="measured_test_bound")
    try:
        with pytest.raises(tools.AcquisitionUnterminated) as caught:
            _call_acquire(_ctrl(False), _guard(), "/data", "name", [],
                          plan=AcquisitionPlan(1, 1, 0.001, 1), policy=policy)
    finally:
        BlockingAcquisition.release.set()
    assert caught.value.expired_bound == "measured_test_bound"
