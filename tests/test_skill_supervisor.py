"""v1 protocol and process conformance, using real stdlib-only workers."""
from copy import deepcopy
import inspect
import json
import os
from pathlib import Path
import sys
import threading
import time
import tracemalloc

import pytest

from microclaw import skill_packages as p
from microclaw import skill_supervisor as s
from microclaw.tools import _recorded_outcome
from tests.test_skill_packages import FIXTURES, NOW, intake, policy, record, refuses, sign


def release(kind="conformance"):
    if kind == "executable":
        return {key: value for key, value in record().items() if key not in {"enabled", "verified"}}
    root = FIXTURES / kind
    m = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    doc = intake()
    for key in doc.keys() & m.keys():
        doc[key] = deepcopy(m[key])
    return dict(manifest=m, intake=sign(doc), release_dir=root)


def assert_record(value):
    assert _recorded_outcome({"analysis": value}) is None

    def walk(obj, path=()):
        if path == ("result", "output"):
            return
        if isinstance(obj, dict):
            assert "error" not in obj
            for key, item in obj.items():
                walk(item, path + (key,))
        elif isinstance(obj, list):
            for item in obj:
                walk(item, path + ("[]",))
    walk(value)
    phases = value["duration_breakdown"]
    assert phases["accounted_s"] >= 0
    assert sum(phases[k] for k in ("queued_s", "startup_s", "running_s", "shutdown_s")) == pytest.approx(phases["accounted_s"])


@pytest.fixture
def supervisors():
    instances = []
    handles = []

    def factory(**kwargs):
        options = dict(startup_deadline_s=3, self_check_deadline_s=5, shutdown_grace_s=0.5)
        options.update(kwargs)
        sup = s.Supervisor(**options)
        original = sup.submit

        def tracked(*args, **kwargs):
            handle = original(*args, **kwargs)
            handles.append(handle)
            return handle
        sup.submit = tracked
        instances.append(sup)
        return sup
    yield factory
    for sup in instances:
        sup.close(timeout=10)
        assert all(not thread.is_alive() for thread in sup._threads)
    for handle in handles:
        assert handle.wait(1)
        assert_record(handle.record())


def submit(sup, tmp_path, behaviour="success", *, operation="self_check", source=None, **params):
    paths = {} if operation == "self_check" else dict(dataset=tmp_path, output_dir=tmp_path)
    return sup.submit(source or release(), policy(), now=NOW, python=sys.executable,
                      operation=operation, parameters=dict(behaviour=behaviour, **params), **paths)


def finished(handle, state="succeeded", reason=None):
    assert handle.wait(12), handle.record()
    value = handle.record()
    assert value["state"] == state, value
    if reason:
        assert value["failure"]["reason"] == reason, value
    assert_record(value)
    return value


def wait_status(handle, expected="ready"):
    end = time.monotonic() + 8
    while time.monotonic() < end:
        if expected in handle.record()["status"]:
            return
        assert not handle.wait(0.01), handle.record()
    pytest.fail("worker did not report ready")


def wait_path(path):
    end = time.monotonic() + 8
    while time.monotonic() < end:
        if path.exists() and path.stat().st_size:
            return
        time.sleep(0.01)
    pytest.fail(f"worker did not create {path}")


def heartbeat_stopped(path):
    wait_path(path)
    before = path.read_bytes()
    time.sleep(0.25)
    assert path.read_bytes() == before


def test_reference_self_check_and_analysis(supervisors, tmp_path):
    sup = supervisors()
    value = sup.self_check(release("executable"), policy(), now=NOW, python=sys.executable, timeout=5)
    assert value["state"] == "succeeded"
    assert value["exit_code"] == 0
    assert value["failure"] is None
    handle = submit(sup, tmp_path, source=release("executable"), operation="observe_dataset")
    assert handle.notify_acquisition("completed", writer="finished")
    value = finished(handle)
    assert value["artifacts"][0]["validity"] == "final"
    assert value["result"]["input_complete"] is True
    assert (tmp_path / "observation.txt").read_bytes() == b"dataset writer finished\n"
    assert not any(name == "fixture_worker" or name.startswith("fixture_worker.") for name in sys.modules)
    value["result"]["output"]["observed"] = False
    assert handle.record()["result"]["output"]["observed"] is True


@pytest.mark.parametrize("change,field", [("self_check", "operations"),
    ("input_schema", "operations[0].input_schema.type"),
    ("output_schema", "operations[0].output_schema.type")])
def test_unsupported_operations_never_launch(supervisors, monkeypatch, tmp_path, change, field):
    sup = supervisors()
    calls = []
    monkeypatch.setattr(s.subprocess, "Popen", lambda *a, **kw: calls.append(kw))
    value = release()
    if change == "self_check":
        value["manifest"]["operations"].pop(0)
    else:
        value["manifest"]["operations"][0][change]["type"] = "array"
    result = finished(submit(sup, tmp_path, source=value), "refused", "refused")
    assert result["failure"]["field"] == field
    assert calls == []


def job(operation="self_check"):
    value = dict(protocol=p.ANALYSIS_PROTOCOL, type="job", job_id="a" * 32,
                 release={key: intake()[key] for key in ("publisher", "package_id", "version", "artifact_digest")},
                 operation=operation, parameters={})
    if operation != "self_check":
        value.update(input=dict(dataset=str(FIXTURES.resolve())), output_dir=str(FIXTURES.resolve()))
    return value


@pytest.mark.parametrize("key", ["controller", "guard", "hook", "hook_object", "token", "port", "hardware_server_url", "frames", "unknown"])
def test_job_has_no_authority_fields(key):
    value = job()
    value[key] = "forbidden"
    refuses(key, p.validate_job, value)
    assert key not in inspect.signature(s.Supervisor.submit).parameters
    assert all(x.kind != inspect.Parameter.VAR_KEYWORD for x in inspect.signature(s.Supervisor.submit).parameters.values())


@pytest.mark.parametrize("kind", ["self_check", "observe_dataset"])
def test_job_required_closed_objects(kind):
    value = job(kind)
    for key in value:
        changed = deepcopy(value)
        del changed[key]
        refuses(key, p.validate_job, changed)
    for name in ("release", "input"):
        if name not in value:
            continue
        for key in value[name]:
            changed = deepcopy(value)
            del changed[name][key]
            refuses(name + "." + key, p.validate_job, changed)
        changed = deepcopy(value)
        changed[name]["unknown"] = True
        refuses(name + ".unknown", p.validate_job, changed)


def test_job_json_parameters_and_self_check_paths():
    value = job()
    value["parameters"] = []
    refuses("parameters", p.validate_job, value)
    for invalid in ({"x": float("nan")}, {"x": object()}, {1: "nonstring"}, {"x": (1, 2)}):
        value["parameters"] = invalid
        refuses("message", p.validate_job, value)
    value = job()
    value["input"] = {"dataset": "relative"}
    refuses("input", p.validate_job, value)
    value = job("observe_dataset")
    value["input"]["dataset"] = "relative"
    refuses("input.dataset", p.validate_job, value)


@pytest.mark.parametrize("behaviour,state,reason,field", [
    ("split", "succeeded", None, None), ("boundary", "succeeded", None, None),
    ("over_boundary", "supervisor_failed", "message_too_large", None),
    ("partial_line", "supervisor_failed", "partial_line", None),
    ("invalid_utf8", "supervisor_failed", "invalid_utf8", None),
    ("non_json", "supervisor_failed", "invalid_json", None),
    ("non_object", "supervisor_failed", "protocol_violation", "message"),
    ("wrong_protocol", "supervisor_failed", "protocol_violation", "protocol"),
    ("missing_protocol", "supervisor_failed", "protocol_violation", "protocol"),
    ("wrong_job", "supervisor_failed", "protocol_violation", "job_id"),
    ("unknown_type", "supervisor_failed", "protocol_violation", "type"),
    ("schema", "supervisor_failed", "protocol_violation", "frames"),
])
def test_framing_real_pipes(supervisors, tmp_path, behaviour, state, reason, field):
    value = finished(submit(supervisors(), tmp_path, behaviour), state, reason)
    if field:
        assert value["failure"]["field"] == field


def test_outgoing_exact_byte_boundary():
    value = job()
    value["parameters"] = {"padding": ""}
    size = len(p.encode_message(value))
    value["parameters"]["padding"] = "x" * (p.MAX_MESSAGE_BYTES - size)
    assert len(p.encode_message(p.validate_job(value))) == p.MAX_MESSAGE_BYTES
    value["parameters"]["padding"] += "x"
    refuses("message", p.validate_job, value)


def test_unterminated_flood_is_bounded_and_killed(supervisors, tmp_path):
    sup = supervisors()
    tracemalloc.start()
    try:
        start = time.monotonic()
        value = finished(submit(sup, tmp_path, "oversized"), "supervisor_failed", "message_too_large")
        _, peak = tracemalloc.get_traced_memory()
        assert peak < 8 * 1024 * 1024
        assert time.monotonic() - start < 10
        assert value["exit_code"] != 0
    finally:
        tracemalloc.stop()


def test_status_and_stderr_flood(supervisors, tmp_path):
    count = 30000
    sup = supervisors(self_check_deadline_s=15)
    handle = submit(sup, tmp_path, "flood", count=count)
    assert handle.wait(20)
    value = finished(handle)
    assert len(value["status"]) == s.MAX_RETAINED_STATUS
    assert value["status_dropped"] == count + 1 - s.MAX_RETAINED_STATUS
    assert value["status"][0] == str(count - s.MAX_RETAINED_STATUS)
    assert value["status"][-1] == str(count - 1)
    assert value["stderr_tail"] == "x" * (s.MAX_STDERR_BYTES - 4) + "TAIL"


def test_nonreader_large_job_and_notifications_never_block(supervisors, tmp_path):
    (tmp_path / "before-job.txt").write_text("never_read", encoding="utf-8")
    sup = supervisors(startup_deadline_s=3)
    start = time.monotonic()
    handle = submit(sup, tmp_path, "never_read", operation="observe_dataset", padding="x" * 60000)
    assert time.monotonic() - start < 0.5
    start = time.monotonic()
    assert handle.notify_acquisition("unterminated", writer="unknown")
    assert handle.notify_writer_finished()
    assert time.monotonic() - start < 0.5
    value = finished(handle, "supervisor_failed", "startup_deadline")
    assert value["exit_code"] != 0
    # A POSIX pipe may buffer the entire job without a reader; delivered
    # records mean written, never consumed by the worker.
    assert all(n["state"] in {"delivered", "undelivered"} for n in value["notifications"])
    heartbeat_stopped(tmp_path / "heartbeat.txt")


def test_job_only_reader_and_notification_after_exit(supervisors, tmp_path):
    handle = submit(supervisors(), tmp_path, operation="observe_dataset")
    finished(handle)
    start = time.monotonic()
    assert not handle.notify_acquisition("completed", writer="finished")
    assert time.monotonic() - start < 0.5
    assert handle.record()["notifications"][-1]["state"] == "undelivered"


@pytest.mark.parametrize("behaviour,retained", [("crash", True), ("changed", False)])
def test_crash_retains_only_pinned_artifacts(supervisors, tmp_path, behaviour, retained):
    value = finished(submit(supervisors(), tmp_path, behaviour, operation="observe_dataset"),
                     "supervisor_failed", "exit_without_terminal")
    assert bool(value["artifacts"]) is retained
    if retained:
        assert value["artifacts"][0]["validity"] == "partial"
    else:
        assert value["rejected_artifacts"][0]["reason"] == "sha256"
        assert (tmp_path / "sample.txt").read_bytes() == b"changed bytes"


@pytest.mark.parametrize("behaviour,reason", [("startup_hang", "startup_deadline"),
    ("mid_hang", "self_check_deadline"), ("terminal_hang", "shutdown_deadline")])
def test_deadlines_kill_heartbeat_tree(supervisors, tmp_path, behaviour, reason):
    path = tmp_path / "heartbeat.txt"
    sup = supervisors(startup_deadline_s=3, self_check_deadline_s=4, shutdown_grace_s=1.5)
    start = time.monotonic()
    value = finished(submit(sup, tmp_path, behaviour, heartbeat=str(path)), "supervisor_failed", reason)
    assert time.monotonic() - start < 10
    assert value["exit_code"] != 0
    heartbeat_stopped(path)


def test_success_also_kills_descendants_holding_pipes(supervisors, tmp_path):
    path = tmp_path / "heartbeat.txt"
    finished(submit(supervisors(), tmp_path, "heartbeat_success", heartbeat=str(path)))
    heartbeat_stopped(path)


@pytest.mark.parametrize("behaviour,reason", [("second_terminal", "message_after_terminal"),
    ("after_terminal", "message_after_terminal"), ("no_terminal", "exit_without_terminal"),
    ("nonzero", "nonzero_exit")])
def test_exactly_one_terminal_and_clean_exit(supervisors, tmp_path, behaviour, reason):
    value = finished(submit(supervisors(), tmp_path, behaviour, operation="observe_dataset"),
                     "supervisor_failed", reason)
    if behaviour != "no_terminal":
        assert value["artifacts"][0]["validity"] == "partial"


def test_cancel_preserves_partial_artifact(supervisors, tmp_path):
    handle = submit(supervisors(), tmp_path, "cancel", operation="observe_dataset")
    wait_status(handle)
    assert handle.cancel()
    value = finished(handle, "cancelled")
    assert value["result"]["input_complete"] is False
    assert value["artifacts"][0]["validity"] == "partial"
    assert value["notifications"][0]["state"] == "delivered"


def test_ignored_cancel_kills_tree(supervisors, tmp_path):
    path = tmp_path / "heartbeat.txt"
    handle = submit(supervisors(), tmp_path, "ignore_cancel", heartbeat=str(path))
    wait_status(handle)
    wait_path(path)
    assert handle.cancel()
    finished(handle, "supervisor_failed", "shutdown_deadline")
    heartbeat_stopped(path)


def test_queued_cancel_never_launches(supervisors, tmp_path):
    sup = supervisors(max_workers=1)
    blocker = submit(sup, tmp_path, "slow", sleep=1)
    wait_status(blocker)
    log = str(tmp_path / "never")
    handle = submit(sup, tmp_path, "slow", interval=log)
    assert handle.record()["state"] == "queued"
    assert handle.cancel()
    finished(handle, "cancelled")
    finished(blocker)
    assert not Path(log + ".start").exists()


def test_concurrency_and_overflow(supervisors, tmp_path):
    sup = supervisors(max_workers=2, max_queued=4)
    handles = []
    for i in range(2):
        handles.append(submit(sup, tmp_path, "slow", sleep=1.5, interval=str(tmp_path / str(i))))
    for handle in handles:
        wait_status(handle)
    for i in range(2, 6):
        handles.append(submit(sup, tmp_path, "slow", sleep=0.3, interval=str(tmp_path / str(i))))
    start = time.monotonic()
    overflow = submit(sup, tmp_path, "slow", interval=str(tmp_path / "overflow"))
    assert time.monotonic() - start < 0.5
    finished(overflow, "dispatch_failed", "queue_full")
    for handle in handles:
        finished(handle)
    events = []
    for i in range(6):
        events.extend([(float((tmp_path / f"{i}.start").read_text(encoding="utf-8")), 1),
                       (float((tmp_path / f"{i}.stop").read_text(encoding="utf-8")), -1)])
    live = peak = 0
    for _, delta in sorted(events):
        live += delta
        peak = max(peak, live)
    assert peak == 2 and live == 0
    assert not (tmp_path / "overflow.start").exists()


def test_late_writer_completion_and_queued_order(supervisors, tmp_path):
    sup = supervisors(max_workers=1)
    blocker = submit(sup, tmp_path, "slow", sleep=0.5)
    wait_status(blocker)
    handle = submit(sup, tmp_path, "lifecycle", operation="observe_dataset")
    assert handle.record()["state"] == "queued"
    assert not handle.notify_acquisition("unterminated", writer="finished")
    assert handle.notify_acquisition("unterminated", writer="unknown")
    assert handle.notify_writer_finished()
    assert not handle.notify_writer_finished()
    finished(blocker)
    value = finished(handle)
    assert value["lifecycle"] == dict(acquisition="unterminated", writer="finished")
    messages = [json.loads(x) for x in value["status"][1:]]
    assert [x["type"] for x in messages] == ["acquisition", "writer"]
    assert [x["state"] for x in value["notifications"]] == ["refused", "delivered", "delivered", "refused"]
    assert "frames" not in json.dumps(value)


def test_lifecycle_refusals_and_bounded_pending(supervisors, tmp_path):
    sup = supervisors(max_workers=1, max_pending_notifications=1)
    blocker = submit(sup, tmp_path, "slow", sleep=0.5)
    wait_status(blocker)
    assert not blocker.notify_acquisition("completed", writer="finished")
    queued = submit(sup, tmp_path, "lifecycle", operation="observe_dataset")
    assert not queued.notify_writer_finished()
    assert queued.notify_acquisition("unterminated", writer="unknown")
    assert not queued.notify_writer_finished()
    assert queued.record()["notifications"][-1]["reason"] == "queue_full"
    assert queued.cancel()
    finished(queued, "cancelled")
    finished(blocker)


def test_lifecycle_has_no_frame_count():
    message = dict(protocol=p.ANALYSIS_PROTOCOL, type="acquisition", job_id="a" * 32,
                   outcome="completed", writer="finished", frames=4)
    refuses("frames", p.validate_notification, message, job_id="a" * 32, operation="observe_dataset")


def test_publisher_output_is_verbatim_and_invisible(supervisors, tmp_path):
    value = finished(submit(supervisors(), tmp_path, "output_error"))
    assert value["result"]["output"] == {"error": "measurement residual"}
    assert_record(value)
    finished(submit(supervisors(), tmp_path, "failed"), "failed", "worker_failed")


def test_worker_environment_and_cwd(supervisors, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROCLAW_TEST_SECRET", "must not reach worker")
    monkeypatch.setenv("PYTHONHOME", "invalid")
    monkeypatch.setenv("PYTHONSTARTUP", "invalid")
    value = finished(submit(supervisors(), tmp_path, "environment"))
    output = value["result"]["output"]
    assert not any(k.startswith("MICROCLAW_") for k in output["environment"])
    assert "PYTHONHOME" not in output["environment"]
    assert "PYTHONSTARTUP" not in output["environment"]
    assert output["environment"]["PYTHONPATH"] == str((FIXTURES / "conformance").resolve())
    assert output["environment"]["PYTHONUNBUFFERED"] == "1"
    assert output["environment"]["PYTHONIOENCODING"] == "utf-8"
    assert output["isatty"] is False
    assert output["cwd"] != str(FIXTURES / "conformance")
    assert not Path(output["cwd"]).exists()


def test_instances_are_independent_and_close_is_bounded(supervisors, tmp_path):
    one, two = supervisors(), supervisors()
    heartbeat = tmp_path / "heartbeat.txt"
    running = submit(one, tmp_path, "mid_hang", heartbeat=str(heartbeat))
    wait_status(running)
    wait_path(heartbeat)
    start = time.monotonic()
    one.close(timeout=8)
    assert time.monotonic() - start < 9
    finished(running, "supervisor_failed", "supervisor_closed")
    heartbeat_stopped(heartbeat)
    finished(submit(two, tmp_path))
    finished(submit(one, tmp_path), "dispatch_failed", "closed")


@pytest.mark.parametrize("saturated", [False, True])
def test_capture_thread_never_waits_or_does_worker_io(supervisors, tmp_path, monkeypatch, saturated):
    source, trust = release(), policy()
    (tmp_path / "before-job.txt").write_text("slow", encoding="utf-8")
    sup = supervisors(max_workers=1, max_queued=1, startup_deadline_s=6)
    calls = {key: [] for key in ("launch", "write", "kill", "verify", "assets", "read")}
    for obj, name, bucket in ((s.subprocess, "Popen", "launch"), (s, "_write_pipe", "write"),
                              (s, "_kill_tree", "kill"), (p, "_verify_signature", "verify"),
                              (p, "verify_release_assets", "assets"), (Path, "open", "read")):
        original = getattr(obj, name)

        def wrapper(*args, _original=original, _bucket=bucket, **kwargs):
            calls[_bucket].append(threading.get_ident())
            return _original(*args, **kwargs)
        monkeypatch.setattr(obj, name, wrapper)

    def dispatch():
        return sup.submit(source, trust, now=NOW, python=sys.executable, operation="observe_dataset",
                          parameters={"behaviour": "lifecycle"}, dataset=tmp_path, output_dir=tmp_path)

    blockers = []
    if saturated:
        blockers.append(dispatch())
        wait_path(tmp_path / "heartbeat.txt")
        blockers.append(dispatch())
        assert blockers[-1].record()["state"] == "queued"
    before_launch = len(calls["launch"])
    before_verify = len(calls["verify"])
    measured, captured, failures, ids = [], [], [], []

    def capture():
        ids.append(threading.get_ident())
        try:
            start = time.monotonic()
            handle = dispatch()
            captured.append(handle)
            measured.append(time.monotonic() - start)
            for _ in range(50):
                for fn in (lambda: handle.notify_acquisition("unterminated", writer="unknown"),
                           handle.notify_writer_finished):
                    start = time.monotonic()
                    fn()
                    measured.append(time.monotonic() - start)
                if saturated:
                    start = time.monotonic()
                    captured.append(dispatch())
                    measured.append(time.monotonic() - start)
                time.sleep(0.01)
        except BaseException as exc:
            failures.append(exc)
    start = time.monotonic()
    thread = threading.Thread(target=capture)
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    assert not failures
    assert time.monotonic() - start < 2
    assert max(measured) < 0.5
    assert len(calls["verify"]) - before_verify == len(captured)
    if saturated:
        assert len(calls["launch"]) == before_launch
        for handle in captured:
            finished(handle, "dispatch_failed", "queue_full")
        for handle in blockers:
            handle.cancel()
    else:
        finished(captured[0])
    sup.close(timeout=8)
    for kind in ("launch", "write", "kill", "assets", "read"):
        assert ids[0] not in calls[kind], (kind, calls)
    assert len(calls["assets"]) == len(calls["launch"])
    assert len(calls["read"]) == len(calls["launch"]) * len(source["manifest"]["assets"])
    assert calls["launch"] and calls["write"] and calls["kill"]


def test_assets_checked_off_thread_before_launch(supervisors, tmp_path, monkeypatch):
    value = release()
    value["manifest"]["assets"][0]["sha256"] = "f" * 64
    launched = []
    monkeypatch.setattr(s.subprocess, "Popen", lambda *args, **kw: launched.append(kw))
    outcome = finished(submit(supervisors(), tmp_path, source=value), "supervisor_failed", "asset_refused")
    assert outcome["failure"]["field"] == "assets[0].sha256"
    assert not launched


def test_analysis_optional_deadline(supervisors, tmp_path):
    sup = supervisors(startup_deadline_s=3, self_check_deadline_s=0.2)
    # The self-check ceiling must not become an analysis ceiling.
    finished(submit(sup, tmp_path, "slow", operation="observe_dataset", sleep=0.5))
    source = release()
    handle = sup.submit(source, policy(), now=NOW, python=sys.executable,
                        operation="observe_dataset", parameters={"behaviour": "slow", "sleep": 60},
                        dataset=tmp_path, output_dir=tmp_path, deadline_s=0.5)
    finished(handle, "supervisor_failed", "deadline")


def test_analysis_artifact_symlink_is_rejected(supervisors, tmp_path):
    target = tmp_path / "original.txt"
    target.write_bytes(b"partial bytes")
    probe = tmp_path / "link-probe"
    try:
        probe.symlink_to(target)
        probe.unlink()
    except OSError:
        pytest.skip("host does not permit symlinks")
    value = finished(submit(supervisors(), tmp_path, "artifact_link", operation="observe_dataset", target=str(target)))
    assert not value["artifacts"]
    assert value["rejected_artifacts"][0]["reason"] == "path"
    assert (tmp_path / "sample.txt").is_symlink()
    assert target.read_bytes() == b"partial bytes"


def stdout(kind="result", operation="observe_dataset"):
    value = dict(protocol=p.ANALYSIS_PROTOCOL, type=kind, job_id="a" * 32)
    if kind == "status":
        value["message"] = "hello"
    elif kind == "artifact":
        value["artifact"] = dict(path="result.txt", sha256="0" * 64, validity="partial")
    else:
        value.update(state="succeeded", output={}, artifacts=[])
        if operation != "self_check":
            value["input_complete"] = True
    return value


@pytest.mark.parametrize("kind", ["status", "artifact", "result"])
def test_stdout_closed_required_keys(kind):
    value = stdout(kind)
    for key in [*value, "unknown"]:
        changed = deepcopy(value)
        if key == "unknown":
            changed[key] = True
        else:
            del changed[key]
        refuses(key, p.validate_worker_message, changed, job_id="a" * 32, operation="observe_dataset")


@pytest.mark.parametrize("field,value", [("state", "complete"), ("output", []),
    ("artifacts", {}), ("artifacts", [{}] * (p.MAX_ARTIFACTS + 1)), ("input_complete", 1)])
def test_result_refusal_fields(field, value):
    message = stdout()
    message[field] = value
    refuses(field, p.validate_worker_message, message, job_id="a" * 32, operation="observe_dataset")


@pytest.mark.parametrize("field,value", [("path", "../outside"), ("path", "/absolute"),
    ("path", "C:/absolute"), ("sha256", "A" * 64), ("validity", "complete"), ("unknown", 1)])
def test_artifact_refusal_fields(field, value):
    message = stdout("artifact")
    message["artifact"][field] = value
    refuses("artifact." + field, p.validate_worker_message, message, job_id="a" * 32, operation="observe_dataset")


def test_result_failure_and_status_limits():
    value = stdout("status")
    value["message"] = "x" * p.MAX_STATUS_MESSAGE_LENGTH
    p.validate_worker_message(value, job_id="a" * 32, operation="self_check")
    value["message"] += "x"
    refuses("message", p.validate_worker_message, value, job_id="a" * 32, operation="self_check")
    value = stdout()
    value["state"] = "failed"
    refuses("failure", p.validate_worker_message, value, job_id="a" * 32, operation="observe_dataset")
    value["failure"] = {"message": "x" * p.MAX_FAILURE_MESSAGE_LENGTH}
    p.validate_worker_message(value, job_id="a" * 32, operation="observe_dataset")
    for text in ("x" * (p.MAX_FAILURE_MESSAGE_LENGTH + 1), "trace\nback"):
        value["failure"]["message"] = text
        refuses("failure.message", p.validate_worker_message, value, job_id="a" * 32, operation="observe_dataset")
    value["state"] = "succeeded"
    refuses("failure", p.validate_worker_message, value, job_id="a" * 32, operation="observe_dataset")


def test_self_check_has_no_artifacts_or_input_complete():
    value = stdout(operation="self_check")
    value["artifacts"] = [stdout("artifact")["artifact"]]
    refuses("artifacts", p.validate_worker_message, value, job_id="a" * 32, operation="self_check")
    refuses("artifact", p.validate_worker_message, stdout("artifact"), job_id="a" * 32, operation="self_check")
    refuses("input_complete", p.validate_worker_message, stdout(), job_id="a" * 32, operation="self_check")


def test_stdin_stdout_explicit_launch_arguments(supervisors, tmp_path, monkeypatch):
    original = s.subprocess.Popen
    seen = []

    def launch(argv, **kw):
        seen.append((argv, kw))
        return original(argv, **kw)
    monkeypatch.setattr(s.subprocess, "Popen", launch)
    finished(submit(supervisors(), tmp_path))
    argv, kw = seen[0]
    assert argv == [sys.executable, "-u", "-m", "conformance_worker.runner"]
    assert kw["stdin"] == kw["stdout"] == kw["stderr"] == s.subprocess.PIPE
    assert not kw.get("shell", False)
    if os.name == "nt":
        assert kw["creationflags"] == 0x00000004 | 0x08000000
    else:
        assert kw["start_new_session"] is True


def test_live_unterminated_then_late_writer(supervisors, tmp_path):
    handle = submit(supervisors(), tmp_path, source=release("executable"), operation="observe_dataset")
    wait_status(handle)
    assert handle.notify_acquisition("unterminated", writer="unknown")
    end = time.monotonic() + 5
    while handle.record()["lifecycle"]["writer"] != "unknown":
        assert time.monotonic() < end
        assert not handle.wait(0.01)
    assert handle.record()["lifecycle"] == dict(acquisition="unterminated", writer="unknown")
    assert not handle.wait(0.1)
    assert handle.notify_writer_finished()
    value = finished(handle)
    assert value["lifecycle"] == dict(acquisition="unterminated", writer="finished")
    assert value["artifacts"][0]["validity"] == "final"


@pytest.mark.parametrize("value", [None, [], {}, "bad", "A" * 32])
def test_job_id_refusals(value):
    message = job()
    message["job_id"] = value
    refuses("job_id", p.validate_job, message)


@pytest.mark.parametrize("kind,field,value", [("status", "type", []),
    ("result", "state", {}), ("artifact", "validity", [])])
def test_malformed_types_are_field_refusals(kind, field, value):
    message = stdout(kind)
    if kind == "artifact":
        message["artifact"][field] = value
        field = "artifact." + field
    else:
        message[field] = value
    refuses(field, p.validate_worker_message, message, job_id="a" * 32, operation="observe_dataset")


def test_malformed_submit_never_raises(supervisors):
    sup = supervisors()
    for value in (None, {}, {"manifest": None}):
        handle = sup.submit(value, None, now=NOW, python=sys.executable, operation="self_check", parameters={})
        finished(handle, "refused", "refused")


def test_latest_artifact_and_terminal_list_are_authoritative(supervisors, tmp_path):
    sup = supervisors()
    value = finished(submit(sup, tmp_path, "latest", operation="observe_dataset"),
                     "supervisor_failed", "exit_without_terminal")
    assert len(value["artifacts"]) == 1
    assert not value["rejected_artifacts"]
    assert value["artifacts"][0]["validity"] == "partial"
    assert (tmp_path / "sample.txt").read_bytes() == b"latest bytes"
    value = finished(submit(sup, tmp_path, "terminal_empty", operation="observe_dataset"))
    assert value["artifacts"] == []
    assert (tmp_path / "sample.txt").read_bytes() == b"partial bytes"


@pytest.mark.parametrize("behaviour", ["artifact_missing", "artifact_directory", "artifact_hardlink"])
def test_artifacts_must_be_regular_unlinked_files(supervisors, tmp_path, behaviour):
    value = finished(submit(supervisors(), tmp_path, behaviour, operation="observe_dataset"))
    assert not value["artifacts"]
    assert value["rejected_artifacts"][0]["reason"] == "path"


def test_artifact_message_count_is_bounded(supervisors, tmp_path):
    value = finished(submit(supervisors(), tmp_path, "many_artifacts", operation="observe_dataset"),
                     "supervisor_failed", "too_many_artifacts")
    assert len(value["rejected_artifacts"]) == p.MAX_ARTIFACTS


def test_stdin_closure_starts_shutdown_grace(supervisors, tmp_path):
    handle = submit(supervisors(), tmp_path, "close_stdin", operation="observe_dataset")
    wait_status(handle)
    assert handle.notify_acquisition("unterminated", writer="unknown")
    finished(handle, "supervisor_failed", "shutdown_deadline")
    assert handle.record()["notifications"][0]["state"] == "undelivered"


def test_launch_failure_is_a_record(supervisors, tmp_path):
    handle = supervisors().submit(release(), policy(), now=NOW, python=str(tmp_path / "absent-python"),
                                  operation="self_check", parameters={})
    value = finished(handle, "supervisor_failed", "launch_failed")
    assert value["exit_code"] is None


@pytest.mark.parametrize("deadline", [0, -1, float("nan"), float("inf"), "1"])
def test_deadline_refusals(supervisors, deadline):
    handle = supervisors().submit(release(), policy(), now=NOW, python=sys.executable,
                                  operation="self_check", parameters={}, deadline_s=deadline)
    value = finished(handle, "refused", "refused")
    assert value["failure"]["field"] == "deadline_s"
