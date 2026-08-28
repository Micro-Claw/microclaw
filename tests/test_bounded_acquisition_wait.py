import json
import inspect
import threading
import time
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
        kwargs = ({"plan": reservation.plan, "ctrl": ctrl}
                  if "plan" in supervised else {})
        return tools._acquire_with_hooks(
            guard, "/data", "run", events, hook, reservation, **kwargs
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


def test_runtime_ceiling_requires_a_quiet_saved_frame_counter(monkeypatch):
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    monkeypatch.setattr(tools, "STALL_QUIET_S", 0.04)
    monkeypatch.setattr(tools, "_ACQUISITION_POLL_S", 0.002)
    ctrl = _ctrl(False)
    done = {}

    def run():
        done["path"] = tools._acquire_with_hooks(
            _guard(), "/data", "run", [],
            plan=AcquisitionPlan(1, 1, -299.99, 1), ctrl=ctrl,
        )

    foreground = threading.Thread(target=run)
    foreground.start()
    deadline = time.monotonic() + 0.15
    while BlockingAcquisition.instance is None and time.monotonic() < deadline:
        time.sleep(0.001)
    saved = BlockingAcquisition.instance.kwargs["image_saved_fn"]
    for _ in range(5):
        time.sleep(0.015)
        saved({}, object())
    assert foreground.is_alive()
    BlockingAcquisition.release.set()
    foreground.join(0.3)
    assert done["path"] == "/data/run_1"


def test_runtime_and_fallback_ceiling_expire_without_frames(monkeypatch):
    monkeypatch.setattr(tools, "Acquisition", BlockingAcquisition)
    monkeypatch.setattr(tools, "STALL_QUIET_S", 0.0, raising=False)
    monkeypatch.setattr(tools, "FALLBACK_RUNTIME_CEILING_S", 0.02, raising=False)
    monkeypatch.setattr(tools, "_ACQUISITION_POLL_S", 0.002, raising=False)
    kwargs = ({"ctrl": _ctrl(False)}
              if "ctrl" in inspect.signature(tools._acquire_with_hooks).parameters
              else {})
    expected = getattr(tools, "AcquisitionUnterminated", Exception)
    with pytest.raises(expected) as caught:
        tools._acquire_with_hooks(_guard(), "/data", "run", [], **kwargs)
    assert caught.value.expired_bound == "runtime_ceiling"
    assert caught.value.fallback_ceiling is True
    assert tools._unterminated_result(caught.value)["ceiling_fallback"] is True
    BlockingAcquisition.release.set()


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


def test_browser_acquisition_sink_drops_when_no_turn_is_bound():
    from microclaw.webserve import Session
    session = object.__new__(Session)
    session._emit = None
    session.emit_acquisition_event({"type": "acquisition_diagnostic"})


def test_hookless_timelapse_emitter_remains_a_bare_acquisition_context():
    source = tools.run_timelapse._microclaw_emitter({
        "n_frames": 2, "interval_s": 0, "save_dir": "/data",
    })
    assert "with Acquisition(" in source
    assert "Thread" not in source and "await_completion" not in source
