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
    doc["skills"] = [{k: skill[k] for k in ("name", "description")} for skill in m["skills"]]
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
        if "desktop_probe" not in inspect.signature(s.Supervisor).parameters:
            options.pop("desktop_probe", None)
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
    end = time.perf_counter() + 8
    while time.perf_counter() < end:
        if expected in handle.record()["status"]:
            return
        assert not handle.wait(0.01), handle.record()
    pytest.fail("worker did not report ready")


def wait_path(path):
    end = time.perf_counter() + 8
    while time.perf_counter() < end:
        if path.exists() and path.stat().st_size:
            return
        time.sleep(0.01)
    pytest.fail(f"worker did not create {path}")


def heartbeat_stopped(path):
    # A kill can land between truncation and write; an empty final file is
    # still evidence that the heartbeat started and must remain unchanged.
    assert path.is_file()
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
        start = time.perf_counter()
        value = finished(submit(sup, tmp_path, "oversized"), "supervisor_failed", "message_too_large")
        _, peak = tracemalloc.get_traced_memory()
        assert peak < 8 * 1024 * 1024
        assert time.perf_counter() - start < 10
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
    start = time.perf_counter()
    handle = submit(sup, tmp_path, "never_read", operation="observe_dataset", padding="x" * 60000)
    assert time.perf_counter() - start < 0.5
    start = time.perf_counter()
    assert handle.notify_acquisition("unterminated", writer="unknown")
    assert handle.notify_writer_finished()
    assert time.perf_counter() - start < 0.5
    value = finished(handle, "supervisor_failed", "startup_deadline")
    assert value["exit_code"] != 0
    # A POSIX pipe may buffer the entire job without a reader; delivered
    # records mean written, never consumed by the worker.
    assert all(n["state"] in {"delivered", "undelivered"} for n in value["notifications"])
    heartbeat_stopped(tmp_path / "heartbeat.txt")


def test_job_only_reader_and_notification_after_exit(supervisors, tmp_path):
    handle = submit(supervisors(), tmp_path, operation="observe_dataset")
    finished(handle)
    start = time.perf_counter()
    assert not handle.notify_acquisition("completed", writer="finished")
    assert time.perf_counter() - start < 0.5
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
    start = time.perf_counter()
    value = finished(submit(sup, tmp_path, behaviour, heartbeat=str(path)), "supervisor_failed", reason)
    assert time.perf_counter() - start < 10
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
    start = time.perf_counter()
    overflow = submit(sup, tmp_path, "slow", interval=str(tmp_path / "overflow"))
    assert time.perf_counter() - start < 0.5
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
    start = time.perf_counter()
    one.close(timeout=8)
    assert time.perf_counter() - start < 9
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
            start = time.perf_counter()
            handle = dispatch()
            captured.append(handle)
            measured.append(time.perf_counter() - start)
            for _ in range(50):
                for fn in (lambda: handle.notify_acquisition("unterminated", writer="unknown"),
                           handle.notify_writer_finished):
                    start = time.perf_counter()
                    fn()
                    measured.append(time.perf_counter() - start)
                if saturated:
                    start = time.perf_counter()
                    captured.append(dispatch())
                    measured.append(time.perf_counter() - start)
                time.sleep(0.01)
        except BaseException as exc:
            failures.append(exc)
    start = time.perf_counter()
    thread = threading.Thread(target=capture)
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    assert not failures
    assert time.perf_counter() - start < 2
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
    value = finished(submit(supervisors(), tmp_path))
    assert value["priority"]["requested"] == expected_worker_priority()
    inherited = s._windows_priority() == 0x40 if os.name == "nt" else os.getpriority(os.PRIO_PROCESS, 0) >= 10
    assert value["priority"]["inherited"] is inherited
    argv, kw = seen[0]
    prefix = [] if os.name == "nt" or os.getpriority(os.PRIO_PROCESS, 0) >= 10 else ["nice", "-n", str(10 - os.getpriority(os.PRIO_PROCESS, 0))]
    assert argv == prefix + [sys.executable, "-u", "-m", "conformance_worker.runner"]
    assert "preexec_fn" not in kw
    assert kw["stdin"] == kw["stdout"] == kw["stderr"] == s.subprocess.PIPE
    assert not kw.get("shell", False)
    if os.name == "nt":
        assert kw["creationflags"] == 0x00000004 | 0x08000000 | (0 if s._windows_priority() == 0x40 else 0x4000)
    else:
        assert kw["start_new_session"] is True


def test_live_unterminated_then_late_writer(supervisors, tmp_path):
    handle = submit(supervisors(), tmp_path, source=release("executable"), operation="observe_dataset")
    wait_status(handle)
    assert handle.notify_acquisition("unterminated", writer="unknown")
    end = time.perf_counter() + 5
    while handle.record()["lifecycle"]["writer"] != "unknown":
        assert time.perf_counter() < end
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


def test_worker_stdin_closure_does_not_gate_analysis(supervisors, tmp_path):
    handle = submit(supervisors(shutdown_grace_s=0.3), tmp_path, "close_stdin",
                    operation="observe_dataset", sleep=2)
    wait_status(handle)
    assert handle.notify_acquisition("unterminated", writer="unknown")
    value = finished(handle)
    assert value["notifications"][0]["state"] == "undelivered"
    assert value["notifications"][0]["reason"] == "stdin_closed"


def test_launch_failure_is_a_record(supervisors, tmp_path):
    handle = supervisors().submit(release(), policy(), now=NOW, python=str(tmp_path / "absent-python"),
                                  operation="self_check", parameters={})
    if os.name != "nt" and os.getpriority(os.PRIO_PROCESS, 0) < 10:
        # nice launched successfully, then could not exec the absent interpreter.
        value = finished(handle, "supervisor_failed", "exit_without_terminal")
        assert value["exit_code"] != 0
        assert "absent-python" in value["stderr_tail"]
    else:
        value = finished(handle, "supervisor_failed", "launch_failed")
        assert value["exit_code"] is None


@pytest.mark.parametrize("deadline", [0, -1, float("nan"), float("inf"), "1"])
def test_deadline_refusals(supervisors, deadline):
    handle = supervisors().submit(release(), policy(), now=NOW, python=sys.executable,
                                  operation="self_check", parameters={}, deadline_s=deadline)
    value = finished(handle, "refused", "refused")
    assert value["failure"]["field"] == "deadline_s"


def test_notification_history_is_bounded(supervisors, tmp_path):
    handle = submit(supervisors(), tmp_path, "lifecycle", operation="observe_dataset")
    wait_status(handle)
    assert handle.notify_acquisition("unterminated", writer="unknown")
    latencies = []
    for _ in range(1000):
        start = time.perf_counter()
        assert not handle.notify_acquisition("unterminated", writer="unknown")
        latencies.append(time.perf_counter() - start)
    value = handle.record()
    assert len(value["notifications"]) == s.MAX_RECORDED_NOTIFICATIONS == 32
    assert value["notifications_dropped"] == 1001 - s.MAX_RECORDED_NOTIFICATIONS
    assert max(latencies) < 0.5
    assert all(entry["state"] == "refused" for entry in value["notifications"][1:])
    assert handle.notify_writer_finished()
    value = finished(handle)
    assert len(value["notifications"]) == 32
    assert value["notifications_dropped"] == 1002 - 32
    assert value["lifecycle"]["writer"] == "finished"


def test_closed_stdin_pending_and_later_notifications(supervisors, tmp_path, monkeypatch):
    # Hold the first failed write briefly so a second notification is definitely
    # pending when BrokenPipeError is observed. The worker is a real subprocess.
    writing, proceed = threading.Event(), threading.Event()
    original = s._write_pipe

    def write(pipe, line):
        if json.loads(line)["type"] != "job":
            writing.set()
            assert proceed.wait(5)
        return original(pipe, line)
    monkeypatch.setattr(s, "_write_pipe", write)
    handle = submit(supervisors(shutdown_grace_s=0.3), tmp_path, "close_stdin",
                    operation="observe_dataset", sleep=3)
    try:
        wait_status(handle)
        assert handle.notify_acquisition("unterminated", writer="unknown")
        assert writing.wait(5)
        assert handle.notify_writer_finished()
    finally:
        proceed.set()
    end = time.perf_counter() + 5
    while handle.record()["notifications"][1]["state"] == "pending":
        assert time.perf_counter() < end
        time.sleep(0.01)
    start = time.perf_counter()
    assert not handle.cancel()
    assert not handle.notify_acquisition("unterminated", writer="unknown")
    assert time.perf_counter() - start < 0.5
    value = finished(handle)
    assert len(value["notifications"]) == 4
    assert all(entry["state"] == "undelivered" and entry["reason"] == "stdin_closed"
               for entry in value["notifications"])
    assert value["lifecycle"] == dict(acquisition=None, writer=None)


@pytest.mark.parametrize("pipe_name", ["stdout", "stderr", "stdin"])
def test_unexpected_pipe_exception_fails_and_kills_tree(supervisors, tmp_path, monkeypatch, pipe_name):
    detail = f"{pipe_name} injected bug"
    if pipe_name == "stdout":
        def validate(*args, **kwargs):
            raise KeyError(detail)
        monkeypatch.setattr(p, "validate_worker_message", validate)
    elif pipe_name == "stderr":
        class BrokenTail(bytearray):
            def extend(self, chunk):
                # nice can warn before the worker (and heartbeat) has started.
                if heartbeat.exists():
                    raise KeyError(detail)
                super().extend(chunk)
        original = s.JobHandle._drain_stderr

        def drain(handle, pipe):
            handle._stderr = BrokenTail()
            original(handle, pipe)
        monkeypatch.setattr(s.JobHandle, "_drain_stderr", drain)
    else:
        original = s._write_pipe

        def write(pipe, line):
            if json.loads(line)["type"] != "job":
                raise KeyError(detail)
            return original(pipe, line)
        monkeypatch.setattr(s, "_write_pipe", write)
    heartbeat = tmp_path / "heartbeat.txt"
    handle = submit(supervisors(), tmp_path, "mid_hang", operation="observe_dataset", heartbeat=str(heartbeat))
    if pipe_name == "stdin":
        wait_status(handle)
        assert handle.notify_acquisition("unterminated", writer="unknown")
    start = time.perf_counter()
    value = finished(handle, "supervisor_failed", pipe_name + "_failed")
    assert time.perf_counter() - start < 10
    assert value["failure"]["detail"] == str(KeyError(detail))
    assert value["exit_code"] != 0
    heartbeat_stopped(heartbeat)
    if pipe_name == "stdin":
        assert value["notifications"][0]["state"] == "undelivered"
        assert value["notifications"][0]["reason"] == "stdin_failed"


def observe(sup, tmp_path, parameters=None):
    dataset, output = tmp_path / 'dataset', tmp_path / 'output'
    dataset.mkdir(exist_ok=True)
    output.mkdir(exist_ok=True)
    handle = sup.submit(release('executable'), policy(), now=NOW, python=sys.executable,
                        operation='observe_dataset', parameters=parameters or {},
                        dataset=dataset, output_dir=output)
    wait_path(output / 'observation.txt')
    assert (output / 'observation.txt').read_bytes() == b'observing dataset\n'
    return handle, dataset, output


def wait_observation(handle, frames, *, offset=None, size=None):
    prefix = f'frames_read={frames}; index_offset='
    expected = f'{prefix}{offset}; index_size={size}' if offset is not None else None
    end = time.monotonic() + 8
    while time.monotonic() < end:
        statuses = handle.record()['status']
        if any(value == expected if expected else value.startswith(prefix) for value in statuses):
            return
        assert not handle.wait(0.01), handle.record()
    pytest.fail(f'no observation of {expected or prefix}: {handle.record()}')


def write_frame(writer, index, *, sixteen=False):
    import numpy as np
    pixels = np.full((32, 48), index,
                     dtype=np.uint16 if sixteen else np.uint8)
    writer.put_image({'time': index}, pixels, {})
    writer._index_file.flush()
    return pixels.nbytes


@pytest.mark.parametrize('subdir', ['', 'Full resolution'])
@pytest.mark.parametrize('loaded', [False, True])
def test_observer_growing_real_ndtiff_partial_index_and_rollover(supervisors, tmp_path, monkeypatch, subdir, loaded):
    from ndstorage import NDTiffDataset
    handle, dataset, output = observe(supervisors(), tmp_path,
        {'cpu_threads': 2, 'max_s': 60} if loaded else {})
    if loaded:
        wait_status(handle, 'load running: 2')
    directory = dataset / subdir
    directory.mkdir(exist_ok=True)
    writer = NDTiffDataset(str(directory), writable=True)
    writer.initialize({})
    try:
        total = write_frame(writer, 0)
        wait_observation(handle, 1)
        index_path = directory / 'NDTiff.index'
        offset = index_path.stat().st_size
        # Real ndstorage writes the next entry through this file object. Hold
        # its second half until the subprocess has observed the partial tail.
        stream = writer._index_file
        class SplitIndex:
            def write(self, data):
                split = len(data) // 2
                stream.write(data[:split])
                stream.flush()
                wait_observation(handle, 1, offset=offset, size=offset + split)
                stream.write(data[split:])
            def flush(self):
                stream.flush()
        writer._index_file = SplitIndex()
        # Exercise the real rollover branch without allocating a 4 GiB TIFF.
        monkeypatch.setattr(writer.current_writer, 'has_space_to_write', lambda *a: False)
        total += write_frame(writer, 1, sixteen=True)
        writer._index_file = stream
        wait_observation(handle, 2)
        total += write_frame(writer, 2)
        writer.finish()
        assert len(list(directory.glob('*.tif'))) == 2
        # Exercise both accepted writer-finished lifecycle shapes.
        if subdir:
            assert handle.notify_acquisition('unterminated', writer='unknown')
            assert handle.notify_writer_finished()
        else:
            assert handle.notify_acquisition('completed', writer='finished')
        record = finished(handle)
        evidence = record['result']['output']
        assert evidence['observed'] is True
        assert evidence['frames_read'] == 3
        assert evidence['bytes_read'] == total
        assert evidence['read_errors'] == 0
        assert 'last_read_error' not in evidence
        assert evidence['index_path'] == (Path(subdir) / 'NDTiff.index').as_posix()
        assert evidence['poll_interval_s'] == 0.03
        assert (output / 'observation.txt').read_bytes() == b'dataset writer finished\n'
        if loaded:
            assert_load(evidence, 'writer')
            assert evidence['frames_indexed_at_load_start'] == 0
        else:
            assert 'cpu_threads' not in evidence
    finally:
        if writer._index_file is not None and not hasattr(writer._index_file, 'close'):
            writer._index_file = stream
        writer.finish()


def assert_load(evidence, reason):
    assert evidence['cpu_threads'] == 2  # Reported after both hash threads rendezvous.
    assert evidence['load_stopped_by'] == reason
    assert evidence['load_process_cpu_s'] >= 0
    assert evidence['load_wall_s'] >= 0
    assert evidence['load_cpu_ratio'] == pytest.approx(
        evidence['load_process_cpu_s'] / evidence['load_wall_s'])


@pytest.mark.parametrize('stop', ['cancel', 'max_s'])
def test_observer_load_stops_without_writer(supervisors, tmp_path, stop):
    from ndstorage import NDTiffDataset
    dataset = tmp_path / 'dataset'
    dataset.mkdir()
    writer = NDTiffDataset(str(dataset), writable=True)
    writer.initialize({})
    total = write_frame(writer, 0, sixteen=True)
    try:
        handle, _, output = observe(supervisors(), tmp_path,
            {'cpu_threads': 2, 'max_s': 0.05 if stop == 'max_s' else 60})
        wait_status(handle, 'load running: 2')
        wait_observation(handle, 1)
        if stop == 'max_s':
            wait_status(handle, 'load stopped: max_s')
            assert not handle.wait(0)  # Only load ends; the observer still tails.
            total += write_frame(writer, 1)
            wait_observation(handle, 2)
        assert handle.cancel()
        record = finished(handle, 'cancelled')
        evidence = record['result']['output']
        assert_load(evidence, stop)
        assert evidence['frames_indexed_at_load_start'] == 1
        assert evidence['frames_read'] == (2 if stop == 'max_s' else 1)
        assert evidence['bytes_read'] == total
        assert record['result']['input_complete'] is False
        assert record['artifacts'][0]['validity'] == 'partial'
        assert (output / 'observation.txt').read_bytes() == b'observing dataset\n'
    finally:
        writer.finish()


def expected_worker_priority():
    return (0x40 if s._windows_priority() == 0x40 else 0x4000) if os.name == 'nt' else max(os.getpriority(os.PRIO_PROCESS, 0), 10)


def assert_launch_priority(value):
    expected = expected_worker_priority()
    if value == expected:
        return
    assert os.name != 'nt'
    # Independent real probe: GNU/BSD nice may warn and still exec successfully.
    probe = s.subprocess.run(['nice', '-n', str(max(0, 10 - os.getpriority(os.PRIO_PROCESS, 0))),
        sys.executable, '-c', 'import os; print(os.getpriority(os.PRIO_PROCESS, 0))'],
        stdin=s.subprocess.DEVNULL, capture_output=True, text=True)
    assert 'Operation not permitted' in probe.stderr
    assert value == int(probe.stdout) == os.getpriority(os.PRIO_PROCESS, 0)


def test_observer_priority_read_back(supervisors, tmp_path):
    handle, _, _ = observe(supervisors(), tmp_path)
    # Wait for monitor evidence, not merely the pipe reader's ready message.
    for _ in range(800):
        if handle.record()['priority']['at_start'] is not None:
            break
        assert not handle.wait(0.01)
    start = handle.record()['priority']['at_start']
    assert_launch_priority(start)
    assert handle.notify_acquisition('completed', writer='finished')
    value = finished(handle)
    evidence = value['result']['output']['priority']
    assert evidence == dict(requested='inherit', applied=True, read_back=start, reason=None)
    end = value['priority']['at_end']
    assert end == start or (end is None and value['priority']['reason']['at_end'] in (
        'process exited before priority read', 'message arrived after priority monitoring ended'))


def test_self_check_priority(supervisors, tmp_path):
    value = finished(submit(supervisors(), tmp_path, source=release('executable')))
    assert_launch_priority(value['result']['output']['priority']['read_back'])
    assert value['priority']['requested'] == expected_worker_priority()
    assert isinstance(value['priority']['inherited'], bool)


@pytest.mark.parametrize('failed', [False, True])
def test_priority_exactly_two_monitor_reads(supervisors, tmp_path, monkeypatch, failed):
    reads = []
    original = s._read_priority
    def read(process, job=None):
        reads.append(threading.current_thread().name)
        return (None, 'injected read refusal', 0) if failed else original(process, job)
    monkeypatch.setattr(s, '_read_priority', read)
    handle, _, _ = observe(supervisors(), tmp_path)
    assert handle.notify_acquisition('completed', writer='finished')
    value = finished(handle)
    assert len(reads) == 2
    assert all(not name.startswith('skill-pipe-') and name != threading.current_thread().name for name in reads)
    if failed:
        assert value['priority']['at_start'] is value['priority']['at_end'] is None
        assert value['priority']['reason'] == dict(at_start='injected read refusal', at_end='injected read refusal')


@pytest.mark.skipif(os.name == "nt", reason="POSIX cannot read a reaped process")
def test_priority_exited_read():
    from types import SimpleNamespace
    assert s._read_priority(SimpleNamespace(poll=lambda: 0)) == (None, 'process exited before priority read', 0)


@pytest.mark.parametrize('parent', [15, 0x4000, 0x40, 0x20])
def test_priority_in_parent_subprocess(tmp_path, parent):
    if (parent == 15) != (os.name != 'nt'):
        pytest.skip('platform-specific parent priority')
    script = """
import json, os, sys
from pathlib import Path
from tests.test_skill_supervisor import release, policy, NOW, finished
from microclaw.skill_supervisor import Supervisor
sup = Supervisor()
try:
    handle = sup.submit(release('executable'), policy(), now=NOW, python=sys.executable,
                        operation='self_check', parameters={})
    print(json.dumps(finished(handle)))
finally:
    sup.close()
"""
    argv = [sys.executable, '-c', script]
    kw = {}
    if os.name == 'nt':
        kw['creationflags'] = parent
    else:
        argv = ['nice', '-n', str(max(0, 15 - os.getpriority(os.PRIO_PROCESS, 0))), *argv]
    probe = s.subprocess.run(argv, stdin=s.subprocess.DEVNULL, capture_output=True, text=True, **kw)
    assert probe.returncode == 0, probe.stderr
    value = json.loads(probe.stdout)
    actual = value['result']['output']['priority']['read_back']
    if os.name == 'nt':
        assert value['priority']['requested'] == (0x40 if parent == 0x40 else 0x4000)
        assert value['priority']['inherited'] is (parent == 0x40)
    elif 'Operation not permitted' not in probe.stderr:
        assert value['priority']['requested'] == max(15, os.getpriority(os.PRIO_PROCESS, 0))
        assert value['priority']['inherited'] is True
    if os.name != 'nt' and 'Operation not permitted' in probe.stderr:
        assert_launch_priority(actual)
    else:
        assert actual == (max(15, os.getpriority(os.PRIO_PROCESS, 0)) if os.name != 'nt' else 0x40 if parent == 0x40 else 0x4000)


@pytest.mark.parametrize('parameters', [{'cpu_threads': 2}, {'cpu_threads': 2, 'max_s': 0}])
def test_observer_worker_enforces_bounds_schema_cannot_express(supervisors, tmp_path, parameters):
    handle = supervisors().submit(release('executable'), policy(), now=NOW, python=sys.executable,
        operation='observe_dataset', parameters=parameters, dataset=tmp_path, output_dir=tmp_path)
    record = finished(handle, 'failed', 'worker_failed')
    assert 'max_s' in record['result']['failure']['message']
    assert not (tmp_path / 'observation.txt').exists()


def test_observer_bad_entry_does_not_hide_following_frames(supervisors, tmp_path):
    from ndstorage import NDTiffDataset
    dataset = tmp_path / 'dataset'
    dataset.mkdir()
    writer = NDTiffDataset(str(dataset), writable=True)
    writer.initialize({})
    write_frame(writer, 0)
    # Preserve real ndstorage's record framing but corrupt this record's axes.
    index = dataset / 'NDTiff.index'
    with index.open('r+b') as stream:
        stream.seek(4)
        stream.write(b'!')
    total = write_frame(writer, 1, sixteen=True)
    writer.finish()
    handle, _, _ = observe(supervisors(), tmp_path)
    assert handle.notify_acquisition('completed', writer='finished')
    evidence = finished(handle)['result']['output']
    assert evidence['frames_read'] == 1
    assert evidence['bytes_read'] == total
    assert evidence['read_errors'] == 1
    assert evidence['last_read_error']


def test_observer_empty_cancel_keeps_original_partial_artifact(supervisors, tmp_path):
    handle, _, output = observe(supervisors(), tmp_path)
    assert handle.cancel()
    record = finished(handle, 'cancelled')
    evidence = record['result']['output']
    assert evidence['frames_read'] == evidence['bytes_read'] == evidence['read_errors'] == 0
    assert evidence['index_path'] == 'NDTiff.index'
    assert 'cpu_threads' not in evidence
    assert record['result']['input_complete'] is False
    assert (output / 'observation.txt').read_bytes() == b'observing dataset\n'


@pytest.mark.parametrize("bad_messages", [False, True], ids=["clean", "malformed"])
def test_observer_stdlib_only_and_bad_lifecycle_message_is_local(tmp_path, bad_messages):
    import subprocess
    from ndstorage import NDTiffDataset
    dataset = tmp_path / 'dataset'
    dataset.mkdir()
    writer = NDTiffDataset(str(dataset), writable=True)
    writer.initialize({})
    total = write_frame(writer, 0, sixteen=True)
    writer.finish()
    value = job('observe_dataset')
    value.update(input={'dataset': str(dataset)}, output_dir=str(tmp_path))
    end = dict(protocol=p.ANALYSIS_PROTOCOL, type='writer', job_id=value['job_id'], state='finished')
    result = subprocess.run([
        sys.executable, '-I', '-S', str(FIXTURES / 'executable' / 'fixture_worker' / 'runner.py')],
        input=p.encode_message(value) + (b'{broken\n[]\n' if bad_messages else b'') + p.encode_message(end),
        capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    messages = [json.loads(line) for line in result.stdout.splitlines()]
    terminal = messages[-1]
    assert terminal['state'] == 'succeeded'
    assert terminal['output']['frames_read'] == 1
    assert terminal['output']['bytes_read'] == total
    assert sum(m['type'] == 'result' for m in messages) == 1
    assert sum(m.get('message', '').startswith('ignored lifecycle') for m in messages) == (2 if bad_messages else 0)


def priority_rendezvous_release(tmp_path, *, hold_end=True):
    # Rendezvous only in this copied test worker: let the monitor sample ready
    # before the real fixture's normal control executes, and retain it at result.
    import hashlib
    import shutil
    root = tmp_path / 'release'
    shutil.copytree(FIXTURES / 'executable', root)
    gate = tmp_path / 'start-sampled'
    end_gate = tmp_path / 'end-sampled'
    runner = root / 'fixture_worker' / 'runner.py'
    source = runner.read_text(encoding='utf-8')
    line = '    output["priority"] = priority(params.get("priority", "inherit"))'
    assert line in source
    source = source.replace(line,
        f'    while not Path({str(gate)!r}).exists():\n'
        '        time.sleep(0.001)\n' + line)
    if hold_end:
        source = source.replace('emit("result", **fields)',
            'emit("result", **fields)\n'
            f'        while not Path({str(end_gate)!r}).exists():\n'
            '            time.sleep(0.001)')
    runner.write_text(source, encoding='utf-8')
    rel = release('executable')
    rel['release_dir'] = root
    for asset in rel['manifest']['assets']:
        asset['sha256'] = hashlib.sha256((root / asset['path']).read_bytes()).hexdigest()
    return rel, gate, end_gate


def test_normal_control_priority_change(supervisors, tmp_path, monkeypatch):
    rel, gate, end_gate = priority_rendezvous_release(tmp_path)
    original = s._read_priority
    reads = []
    def read(process, job=None):
        value = original(process, job)
        reads.append(value)
        (gate if len(reads) == 1 else end_gate).write_text('sampled', encoding='utf-8')
        return value
    monkeypatch.setattr(s, '_read_priority', read)
    handle = supervisors().submit(rel, policy(), now=NOW, python=sys.executable,
        operation='observe_dataset', parameters={'priority': 'normal'}, dataset=tmp_path, output_dir=tmp_path)
    wait_path(tmp_path / 'observation.txt')
    assert handle.notify_acquisition('completed', writer='finished')
    value = finished(handle)
    evidence = value['result']['output']['priority']
    start, end = value['priority']['at_start'], value['priority']['at_end']
    assert_launch_priority(start)
    assert end == evidence['read_back']
    assert len(reads) == 2
    if evidence['applied']:
        assert end == (0x20 if os.name == 'nt' else 0)
        if os.name == 'nt' or start != 0:
            assert start != end
        else:
            assert_launch_priority(start)  # Independently prove launch lowering was denied.
    else:
        assert os.name != 'nt' and evidence['reason']
        probe = s.subprocess.run(['nice', '-n', str(max(0, start - os.getpriority(os.PRIO_PROCESS, 0))),
            sys.executable, '-c', 'import os; os.setpriority(os.PRIO_PROCESS, 0, 0)'],
            stdin=s.subprocess.DEVNULL, capture_output=True, text=True)
        assert probe.returncode != 0 and ('not permitted' in probe.stderr or 'Permission denied' in probe.stderr)
        assert end == start


def test_priority_os_read_failure_is_nonfatal(supervisors, tmp_path, monkeypatch):
    if os.name == 'nt':
        def read(job, pid):
            raise OSError('priority access refused')
        monkeypatch.setattr(s._WindowsJob, '_pid_priority', read)
    else:
        original = s.os.getpriority
        def read(which, pid):
            if pid:
                raise OSError('priority access refused')
            return original(which, pid)
        monkeypatch.setattr(s.os, 'getpriority', read)
    handle, _, _ = observe(supervisors(), tmp_path)
    # Keep the real worker alive until the monitor has attempted the OS read.
    for _ in range(800):
        if 'at_start' in handle.record()['priority']['reason']:
            break
        assert not handle.wait(0.01)
    assert handle.notify_acquisition('completed', writer='finished')
    value = finished(handle)
    assert value['priority']['at_start'] is None
    assert 'priority access refused' in value['priority']['reason']['at_start']
    assert value['priority']['at_end'] is None
    assert any(reason in value['priority']['reason']['at_end'] for reason in (
        'priority access refused', 'process exited before priority read',
        'message arrived after priority monitoring ended', 'job has no processes (processes exited)'))


@pytest.mark.parametrize('messages', ['success', 'no_terminal'])
def test_cleanup_labels_unsampled_priority_without_reads_or_extra_joins(
        supervisors, tmp_path, monkeypatch, messages):
    cleanup = threading.Event()
    original_stdout = s.JobHandle._stdout
    original_join = threading.Thread.join
    joins, reads = [], []

    def stdout(handle, pipe):
        assert cleanup.wait(10)
        original_stdout(handle, pipe)

    def join(thread, *args, **kwargs):
        if thread.name.startswith('skill-pipe-'):
            joins.append(thread.ident)
            cleanup.set()
        return original_join(thread, *args, **kwargs)

    def read(process, job=None):
        reads.append(process.pid)
        return None, 'unexpected priority read', 0

    monkeypatch.setattr(s.JobHandle, '_stdout', stdout)
    monkeypatch.setattr(threading.Thread, 'join', join)
    monkeypatch.setattr(s, '_read_priority', read)
    handle = submit(supervisors(), tmp_path, 'no_terminal' if messages == 'no_terminal' else 'success')
    value = finished(handle, 'succeeded' if messages == 'success' else 'supervisor_failed')
    assert reads == []
    assert len(joins) == len(set(joins)) == 3
    priority = value['priority']
    assert priority['at_start'] is priority['at_end'] is None
    assert priority['reason'] == dict(
        at_start='worker message never arrived' if messages == 'no_terminal' else
                 'message arrived after priority monitoring ended',
        at_end='message arrived after priority monitoring ended' if messages == 'success' else
               'worker message never arrived')


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object priority read-back')
@pytest.mark.parametrize('read_after_exit', [False, True], ids=['ordinary', 'exited-job'])
def test_windows_observer_terminal_priority(supervisors, tmp_path, monkeypatch, read_after_exit):
    rel, gate, end_gate = priority_rendezvous_release(tmp_path)
    original = s._read_priority
    reads = []

    def read(process, job=None):
        if read_after_exit and reads:
            # Query the still-open Job Object after its last process has exited.
            end_gate.write_text('exit before read', encoding='utf-8')
            process.wait(timeout=5)
            assert process.returncode == 0
        result = original(process, job)
        reads.append(result)
        (gate if len(reads) == 1 else end_gate).write_text('sampled', encoding='utf-8')
        return result

    monkeypatch.setattr(s, '_read_priority', read)
    handle = supervisors().submit(rel, policy(), now=NOW, python=sys.executable,
        operation='observe_dataset', parameters={}, dataset=tmp_path, output_dir=tmp_path)
    wait_path(tmp_path / 'observation.txt')
    assert handle.notify_acquisition('completed', writer='finished')
    value = finished(handle)
    priority = value['priority']
    assert priority['at_start'] == value['result']['output']['priority']['read_back']
    assert priority['processes_read']['at_start'] >= 1
    assert priority['at_end'] == priority['at_start']
    assert priority['processes_read']['at_end'] >= 1
    assert priority['reason'] == {}
    assert len(reads) == 2


def venv_priority_record(tmp_path, python, requested, after_exit=False):
    """Run in a NORMAL-class test subprocess so host inheritance cannot mask the rule."""
    rel, gate, end_gate = priority_rendezvous_release(tmp_path, hold_end=not after_exit)
    original = s._read_priority
    reads = []

    def read(process, job=None):
        if after_exit and reads:
            process.wait(timeout=5)
            deadline = time.monotonic() + 5
            while job._process_ids()[0]:
                assert time.monotonic() < deadline, 'worker job still has live processes'
                time.sleep(0.001)
            assert process.returncode == 0
        value = original(process, job)
        reads.append(value)
        if len(reads) == 1:
            gate.write_text('sampled', encoding='utf-8')
        elif not after_exit:
            end_gate.write_text('sampled', encoding='utf-8')
        return value

    sup = s.Supervisor(startup_deadline_s=3, shutdown_grace_s=0.5)
    s._read_priority = read
    try:
        handle = sup.submit(rel, policy(), now=NOW, python=python,
            operation='observe_dataset', parameters={'priority': 'normal'} if requested == 'normal' else {},
            dataset=tmp_path, output_dir=tmp_path)
        wait_path(tmp_path / 'observation.txt')
        assert handle.notify_acquisition('completed', writer='finished')
        value = finished(handle)
        assert len(reads) == 2
        return value
    finally:
        sup.close(timeout=10)
        s._read_priority = original


@pytest.mark.skipif(os.name != 'nt', reason='Windows stdlib venv launcher')
@pytest.mark.parametrize('requested', ['inherit', 'normal'])
@pytest.mark.parametrize('after_exit', [False, True], ids=['held-at-result', 'exited-tree'])
def test_windows_venv_launcher_priority(tmp_path, requested, after_exit):
    venv = tmp_path / 'venv'
    created = s.subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)],
                              stdin=s.subprocess.DEVNULL, capture_output=True, text=True)
    assert created.returncode == 0, created.stderr
    script = """
import json, sys
from pathlib import Path
from tests.test_skill_supervisor import venv_priority_record
print(json.dumps(venv_priority_record(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4] == 'True')))
"""
    run = s.subprocess.run([sys.executable, '-c', script, str(tmp_path),
                           str(venv / 'Scripts' / 'python.exe'), requested, str(after_exit)],
                          creationflags=0x20, stdin=s.subprocess.DEVNULL, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    value = json.loads(run.stdout)
    priority = value['priority']
    fixture = value['result']['output']['priority']
    assert priority['requested'] == priority['at_start'] == 0x4000
    assert priority['inherited'] is False
    assert priority['processes_read']['at_start'] > 1  # Launcher plus the real interpreter.
    assert priority['processes_read']['at_end'] > 1
    assert priority['at_end'] == fixture['read_back'] == (0x20 if requested == 'normal' else 0x4000)
    assert fixture['applied'] is True


def windows_priority_job(pids, priorities, *, query_error=None):
    """Native-layout fake: exercise class-3 buffer decoding and each handle's lifetime."""
    import ctypes as c
    from types import SimpleNamespace
    last_error = [0]
    calls = dict(queries=0, opened=[], closed=[])

    def win_error(code):
        exc = OSError(f'Windows error {code}')
        exc.winerror = code
        return exc

    class Kernel:
        def QueryInformationJobObject(self, handle, kind, pointer, size, returned):
            assert handle == 123 and kind == 3 and returned is None
            info = pointer._obj
            assert size == c.sizeof(info)
            assert len(info.ProcessIdList) == s.MAX_JOB_PRIORITY_PROCESSES
            calls['queries'] += 1
            if query_error:
                last_error[0] = query_error
                return 0
            info.NumberOfAssignedProcesses = len(pids)
            info.NumberOfProcessIdsInList = min(len(pids), s.MAX_JOB_PRIORITY_PROCESSES)
            for index, pid in enumerate(pids[:s.MAX_JOB_PRIORITY_PROCESSES]):
                info.ProcessIdList[index] = pid
            last_error[0] = 234 if len(pids) > s.MAX_JOB_PRIORITY_PROCESSES else 0
            return int(last_error[0] == 0)

        def OpenProcess(self, access, inherit, pid):
            assert access == 0x1000 and inherit is False
            calls['opened'].append(pid)
            if pid not in priorities:
                last_error[0] = 87
                return 0
            return pid

        def GetPriorityClass(self, handle):
            return priorities[handle]

        def CloseHandle(self, handle):
            calls['closed'].append(handle)
            return 1

    job = s._WindowsJob.__new__(s._WindowsJob)
    job.handle = 123
    job._priority_handles = {}
    job.kernel = Kernel()
    job.c = SimpleNamespace(**{name: getattr(c, name) for name in (
        'Structure', 'c_uint32', 'c_size_t', 'sizeof', 'byref')},
        get_last_error=lambda: last_error[0], WinError=win_error)
    return job, calls


@pytest.mark.parametrize('classes,expected', [
    ([0x4000, 0x20], 0x20), ([0x20, 0x80], 0x80),
    ([0x40, 0x4000, 0x20, 0x8000, 0x80, 0x100], 0x100)])
def test_windows_job_priority_uses_scheduling_rank(classes, expected):
    priorities = dict(enumerate(classes, start=1))
    job, calls = windows_priority_job(list(priorities), priorities)
    assert job.read_priority() == (expected, None, len(classes))
    assert calls['queries'] == 1
    assert calls['opened'] == list(priorities) and calls['closed'] == []
    job.close()
    assert calls['closed'] == list(priorities) + [123]


def test_windows_job_priority_truncates_without_retry():
    pids = list(range(1, s.MAX_JOB_PRIORITY_PROCESSES + 3))
    job, calls = windows_priority_job(pids, {pid: 0x4000 for pid in pids})
    value, reason, count = job.read_priority()
    assert value == 0x4000 and count == s.MAX_JOB_PRIORITY_PROCESSES
    assert 'truncated' in reason and str(len(pids)) in reason
    assert calls['queries'] == 1
    assert calls['opened'] == pids[:s.MAX_JOB_PRIORITY_PROCESSES] and calls['closed'] == []
    job.close()
    assert calls['closed'] == pids[:s.MAX_JOB_PRIORITY_PROCESSES] + [123]


def test_windows_job_priority_skips_vanished_pid():
    job, calls = windows_priority_job([1, 2], {2: 0x20})
    assert job.read_priority() == (0x20, 'pid 1 exited before priority read', 1)
    assert calls['opened'] == [1, 2] and calls['closed'] == []
    job.close()
    assert calls['closed'] == [2, 123]


@pytest.mark.parametrize('pids', [[], [1]])
def test_windows_job_priority_no_readable_processes(pids):
    job, calls = windows_priority_job(pids, {})
    value, reason, count = job.read_priority()
    assert value is None and count == 0
    assert ('no readable processes' if pids else 'no processes') in reason
    assert calls['closed'] == []


def test_windows_job_priority_query_failure_is_nonfatal():
    job, calls = windows_priority_job([], {}, query_error=5)
    # _read_priority owns the best-effort boundary; exercise its Windows branch
    # without changing os.name globally (which would affect pathlib on POSIX).
    from types import SimpleNamespace
    from unittest.mock import patch
    with patch.object(s, 'os', SimpleNamespace(name='nt')):
        assert s._read_priority(None, job) == (None, 'Windows error 5', 0)
    assert calls['queries'] == 1 and calls['opened'] == []


def test_windows_job_priority_retains_exited_and_new_members():
    pids, priorities = [1, 2], {1: 0x4000, 2: 0x4000}
    job, calls = windows_priority_job(pids, priorities)
    assert job.read_priority() == (0x4000, None, 2)
    priorities[2] = 0x20
    pids[:] = [3]
    priorities[3] = 0x80
    assert job.read_priority() == (0x80, None, 3)
    pids.clear()
    assert job.read_priority() == (0x80, None, 3)
    assert calls['opened'] == [1, 2, 3] and calls['closed'] == []
    job.close()
    job.close()  # Cleanup is idempotent.
    assert calls['closed'] == [1, 2, 3, 123]


def test_windows_job_priority_retained_handles_stay_bounded():
    pids = list(range(1, s.MAX_JOB_PRIORITY_PROCESSES + 1))
    priorities = {pid: 0x4000 for pid in pids}
    job, calls = windows_priority_job(pids, priorities)
    assert job.read_priority() == (0x4000, None, s.MAX_JOB_PRIORITY_PROCESSES)
    pids[:] = [1000]
    priorities[1000] = 0x20
    value, reason, count = job.read_priority()
    assert value == 0x4000 and count == s.MAX_JOB_PRIORITY_PROCESSES
    assert 'handles truncated' in reason and '1 new members omitted' in reason
    assert len(calls['opened']) == s.MAX_JOB_PRIORITY_PROCESSES
    job.close()
    assert len(calls['closed']) == s.MAX_JOB_PRIORITY_PROCESSES + 1


def test_windows_job_priority_cleanup_attempts_every_handle_on_failure():
    job, calls = windows_priority_job([1, 2], {1: 0x4000, 2: 0x20})
    assert job.read_priority() == (0x20, None, 2)
    close = job.kernel.CloseHandle
    def failing_close(handle):
        close(handle)
        return handle != 1
    job.kernel.CloseHandle = failing_close
    with pytest.raises(OSError):
        job.close()
    assert calls['closed'] == [1, 2, 123]
    assert job.handle is None and job._priority_handles == {}


def wait_until(predicate):
    end = time.monotonic() + 8
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def window_job(sup, tmp_path, behaviour='window_hang', **params):
    return submit(sup, tmp_path, behaviour, operation='window_worker', **params)


def test_84a_window_handoff_releases_worker_and_freezes_evidence(supervisors, tmp_path):
    sup = supervisors(max_workers=1, desktop_probe=lambda: True, max_stderr_bytes=128)
    h = window_job(sup, tmp_path, 'window_flood')
    original = finished(h)
    entry = sup._windows[h.job_id]
    assert entry[1].poll() is None
    assert original['window_retained'] is True
    assert original['exit_code'] is None
    assert original['artifacts'][0]['sha256'] == __import__('hashlib').sha256(b'partial bytes').hexdigest()
    second = submit(sup, tmp_path)
    finished(second)
    assert entry[1].poll() is None  # Observed progress with first process alive.
    wait_until(lambda: sup.window_diagnostics(h.job_id)['discarded_stdout_bytes'] > 200000)
    wait_until(lambda: 'WINDOW TAIL' in sup.window_diagnostics(h.job_id)['stderr_tail'])
    diagnostic = sup.window_diagnostics(h.job_id)
    assert len(diagnostic['stderr_tail']) <= 128
    assert h.record() == original
    assert h.notify_writer_finished() is False
    assert h.record() == original
    assert sup.list_open_windows() == [dict(package='fixture-lab/conformance-fixture',
        operation='window_worker', job_id=h.job_id, dataset=str(tmp_path), opened_at=entry[4])]
    assert sup.close_window(h.job_id) == {"state": "closed"}
    wait_until(lambda: not sup.list_open_windows())
    assert h.record() == original


def test_84a_window_nonzero_is_only_diagnostic(supervisors, tmp_path):
    sup = supervisors(desktop_probe=lambda: True)
    h = window_job(sup, tmp_path, 'window_nonzero', sleep=0.5)
    original = finished(h)
    entry = sup._windows[h.job_id]
    assert entry[1].poll() is None
    assert original['artifacts'] and original['exit_code'] is None
    wait_until(lambda: not sup.list_open_windows())
    assert entry[0]._diagnostics['exit_code'] == 3
    assert h.record() == original


def test_84a_window_parent_exit_kills_pipe_child(supervisors, tmp_path):
    sup = supervisors(desktop_probe=lambda: True)
    heartbeat = tmp_path / 'heartbeat.txt'
    h = window_job(sup, tmp_path, 'window_child', heartbeat=str(heartbeat))
    original = finished(h)
    assert original['window_retained'] is True
    wait_until(lambda: not sup._window_reservations)
    heartbeat_stopped(heartbeat)
    assert h.record() == original


def test_84a_window_close_and_shutdown_race(supervisors, tmp_path, monkeypatch):
    sup = supervisors(desktop_probe=lambda: True)
    h = window_job(sup, tmp_path)
    finished(h)
    process = sup._windows[h.job_id][1]
    sup.close()
    wait_until(lambda: process.poll() is not None and not sup._window_reservations)
    racing = supervisors(desktop_probe=lambda: True)
    hashing, resume = threading.Event(), threading.Event()
    retain = racing._retain_artifacts
    def pause(*args):
        hashing.set()
        assert resume.wait(5)
        return retain(*args)
    monkeypatch.setattr(racing, '_retain_artifacts', pause)
    active = window_job(racing, tmp_path)
    assert hashing.wait(5)
    closer = threading.Thread(target=racing.close)
    closer.start()
    assert racing._closing.wait(3)
    resume.set()
    closer.join(8)
    assert not closer.is_alive()
    finished(active, 'supervisor_failed', 'supervisor_closed')
    assert not racing.list_open_windows() and not racing._window_reservations


def test_84a_window_reservations_concurrent_and_queued_cancel(supervisors, tmp_path):
    sup = supervisors(max_workers=1, max_queued=16, desktop_probe=lambda: True)
    blocker = submit(sup, tmp_path, 'mid_hang', heartbeat=str(tmp_path/'hb'))
    wait_status(blocker)
    assert not getattr(sup, '_windows', {})
    handles = []
    lock = threading.Lock()
    def admission():
        h = window_job(sup, tmp_path)
        with lock:
            handles.append(h)
    threads = [threading.Thread(target=admission) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    admitted = [h for h in handles if not h.wait(0)]
    assert len(admitted) == 4
    assert len(sup._window_reservations) == 4
    for h in handles:
        if h not in admitted:
            value = finished(h, 'refused')
            assert 'open windows:' in value['failure']['detail'] and 'pending reservations:' in value['failure']['detail']
    for h in admitted:
        assert h.cancel()
        finished(h, 'cancelled')
    assert not sup._window_reservations
    replacement = window_job(sup, tmp_path)
    assert not replacement.wait(0)
    replacement.cancel()
    sup.close()


@pytest.mark.parametrize('path', ['queue_full', 'launch_failed', 'desktop', 'startup_hang', 'ignore_cancel'])
def test_84a_window_reservation_cleanup(supervisors, tmp_path, path, monkeypatch):
    sup = supervisors(max_workers=1, max_queued=1, desktop_probe=lambda: path != 'desktop', startup_deadline_s=0.2)
    if path == 'queue_full':
        blocker = submit(sup, tmp_path, 'mid_hang', heartbeat=str(tmp_path/'hb'))
        wait_status(blocker)
        queued = submit(sup, tmp_path)
        h = window_job(sup, tmp_path)
        finished(h, 'dispatch_failed', 'queue_full')
        queued.cancel()
    elif path == 'launch_failed':
        def cannot_launch(*args, **kwargs):
            raise OSError('injected launch failure')
        monkeypatch.setattr(s.subprocess, 'Popen', cannot_launch)
        h = sup.submit(release(), policy(), now=NOW, python=str(tmp_path/'missing'),
                       operation='window_worker', parameters={}, dataset=tmp_path, output_dir=tmp_path)
        finished(h, 'supervisor_failed', 'launch_failed')
    elif path == 'desktop':
        h = window_job(sup, tmp_path)
        finished(h, 'supervisor_failed', 'desktop_unavailable')
    else:
        h = window_job(sup, tmp_path, path, heartbeat=str(tmp_path/'hb'))
        if path == 'ignore_cancel':
            wait_status(h)
            h.cancel()
            finished(h, 'supervisor_failed', 'shutdown_deadline')
        else:
            finished(h, 'supervisor_failed', 'startup_deadline')
        heartbeat_stopped(tmp_path/'hb')
    wait_until(lambda: not sup._window_reservations)


def fixture_window(sup, tmp_path):
    dataset, output = tmp_path/'dataset', tmp_path/'output'
    dataset.mkdir(); output.mkdir()
    h = sup.submit(release('executable'), policy(), now=NOW, python=sys.executable,
                   operation='fixture_window', parameters=dict(display_backend='headless', close_file='close.txt'),
                   dataset=dataset, output_dir=output)
    wait_status(h)
    return h, dataset, output


def displayed(output, frames):
    try:
        return json.loads((output/'display.json').read_text(encoding='utf-8'))['frames_displayed'] == frames
    except (OSError, ValueError):
        return False


def test_84a_fixture_live_pause_final_drain_and_eof(supervisors, tmp_path):
    from ndstorage import NDTiffDataset
    sup = supervisors(desktop_probe=lambda: True)
    h, dataset, output = fixture_window(sup, tmp_path)
    writer = NDTiffDataset(str(dataset), writable=True)
    writer.initialize({})
    try:
        write_frame(writer, 0)
        wait_until(lambda: displayed(output, 1))
        assert not h.wait(0) and not sup.list_open_windows()
        time.sleep(0.2)  # > six observer polls and twenty display ticks.
        assert not h.wait(0)
        write_frame(writer, 1)
        wait_until(lambda: displayed(output, 2))
        write_frame(writer, 2)
        writer.finish()
        h.notify_acquisition('completed', writer='unknown')
        assert not h.wait(0.1)
        h.notify_writer_finished()
        value = finished(h)
        assert value['result']['input_complete'] is True
        assert value['result']['output']['frames_read'] == 3
        assert value['artifacts'] and not value['rejected_artifacts']
        process = sup._windows[h.job_id][1]
        time.sleep(0.2)
        assert process.poll() is None  # stdin EOF after result leaves display alive.
        assert displayed(output, 3)
    finally:
        writer.close()


def test_84a_fixture_close_before_result_is_cancelled(supervisors, tmp_path):
    from ndstorage import NDTiffDataset
    sup = supervisors(desktop_probe=lambda: True)
    h, dataset, output = fixture_window(sup, tmp_path)
    writer = NDTiffDataset(str(dataset), writable=True)
    writer.initialize({})
    try:
        write_frame(writer, 0)
        wait_until(lambda: displayed(output, 1))
        assert not sup.list_open_windows()
        (output/'close.txt').touch()
        value = finished(h, 'cancelled')
        assert value['result']['input_complete'] is False
        assert value['artifacts'][0]['validity'] == 'partial'
        assert value['result']['output']['frames_read'] == 1
    finally:
        writer.finish(); writer.close()


@pytest.mark.parametrize('environment,expected', [({}, False), ({'DISPLAY': ''}, False),
    ({'DISPLAY': ':1'}, True), ({'WAYLAND_DISPLAY': 'wayland-0'}, True)])
def test_84a_linux_desktop_preflight(environment, expected):
    assert s.interactive_desktop(platform='linux', environ=environment) is expected


def test_84a_windows_session_zero_preflight(monkeypatch):
    import ctypes
    class Session:
        argtypes = restype = None
        def __call__(self, pid, target):
            target._obj.value = self.value
            return True
    fn = Session()
    class Kernel:
        ProcessIdToSessionId = fn
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **k: Kernel(), raising=False)
    fn.value = 0
    assert not s.interactive_desktop(platform='win32')
    fn.value = 1
    assert s.interactive_desktop(platform='win32')


def test_84a_stdout_buffer_switch_at_terminal(supervisors):
    import io
    sup = supervisors(desktop_probe=lambda: True)
    h = s.JobHandle(sup)
    h._opens_window = True
    h._record['operation'] = 'window_worker'
    terminal = p.encode_message(dict(protocol=p.ANALYSIS_PROTOCOL, type='result', job_id=h.job_id,
                                     state='succeeded', output={}, artifacts=[], input_complete=True))
    garbage = b'\xff\n' + b'x' * 200000 + b'\n' + terminal
    h._stdout(io.BytesIO(terminal + garbage))
    assert h._violation is None
    assert h._diagnostics['discarded_stdout_bytes'] == len(garbage)
    assert not h._status
    with h._lock:
        h._finish('succeeded')
    original = h.record()
    h._drain_stderr(io.BytesIO(b'z' * 200000))
    assert h.record() == original
    assert len(h._window_stderr) <= sup.max_stderr_bytes


def test_84a_open_windows_count_once_and_duplicate_id_refused(supervisors, tmp_path):
    sup = supervisors(max_workers=1, desktop_probe=lambda: True)
    handles = [window_job(sup, tmp_path) for _ in range(4)]
    for h in handles:
        finished(h)
    assert len(sup.list_open_windows()) == 4
    refused = window_job(sup, tmp_path)
    value = finished(refused, 'refused')
    assert 'window limit 4' in value['failure']['detail']
    for h in handles:
        assert h.job_id in value['failure']['detail']
    duplicate = sup.submit(release(), policy(), now=NOW, python=sys.executable,
                           operation='window_worker', parameters={}, dataset=tmp_path,
                           output_dir=tmp_path, job_id=handles[0].job_id)
    assert finished(duplicate, 'refused')['failure']['field'] == 'job_id'
    assert sup.close_window(handles[0].job_id) == {"state": "closed"}
    wait_until(lambda: len(sup._window_reservations) == 3)
    replacement = window_job(sup, tmp_path)
    finished(replacement)
    assert len(sup.list_open_windows()) == 4


def test_84a_window_prehandoff_hash_failure_kills_tree(supervisors, tmp_path, monkeypatch):
    sup = supervisors(desktop_probe=lambda: True)
    processes = []
    popen = s.subprocess.Popen
    def capture(*a, **kw):
        process = popen(*a, **kw)
        processes.append(process)
        return process
    monkeypatch.setattr(s.subprocess, 'Popen', capture)
    def fail(*args):
        raise RuntimeError('injected prehandoff failure')
    monkeypatch.setattr(sup, '_retain_artifacts', fail)
    h = window_job(sup, tmp_path)
    value = finished(h, 'supervisor_failed', 'launch_failed')
    assert 'window_retained' not in value
    wait_until(lambda: not sup._window_reservations)
    assert processes[0].poll() is not None
    assert not sup.list_open_windows()


def test_84a_window_result_frees_slot_before_process_exit(supervisors, tmp_path):
    sup = supervisors(max_workers=1, desktop_probe=lambda: True)
    h = window_job(sup, tmp_path)
    assert h.wait(2)
    value = h.record()
    assert value['state'] == 'succeeded', value['failure']
    process = sup._windows[h.job_id][1]
    second = submit(sup, tmp_path)
    finished(second)
    assert process.poll() is None
    assert value == h.record()


@pytest.mark.parametrize('state', ['failed', 'cancelled'])
def test_84a_all_worker_terminals_can_handoff(supervisors, tmp_path, state):
    sup = supervisors(desktop_probe=lambda: True)
    h = window_job(sup, tmp_path, 'failed' if state == 'failed' else 'cancel')
    if state == 'cancelled':
        wait_status(h)
        h.cancel()
    value = finished(h, state)
    assert value['window_retained'] is True and value['exit_code'] is None
    if state == 'failed':
        assert value['failure']['reason'] == 'worker_failed'
    else:
        assert value['artifacts'][0]['validity'] == 'partial'
        assert value['result']['input_complete'] is False


def test_84a_revision_close_continues_after_window_kill_failure(supervisors, tmp_path, monkeypatch):
    sup = supervisors(desktop_probe=lambda: True)
    first, second = window_job(sup, tmp_path), window_job(sup, tmp_path)
    finished(first); finished(second)
    processes = [sup._windows[h.job_id][1] for h in (first, second)]
    kill = s._kill_tree
    def one_failure(process, job):
        if process is processes[0] and threading.current_thread() is threading.main_thread():
            raise OSError('injected window kill failure')
        return kill(process, job)
    with monkeypatch.context() as patch:
        patch.setattr(s, '_kill_tree', one_failure)
        try:
            sup.close(timeout=0.3)
            assert processes[1].poll() is not None
            assert 'injected window kill failure' in str(first._diagnostics['cleanup_failures'])
        finally:
            kill(processes[0], None)
    wait_until(lambda: not sup._windows)


def test_84a_revision_close_window_racing_reaper_returns(supervisors, tmp_path, monkeypatch):
    sup = supervisors(desktop_probe=lambda: True)
    reaping, resume = threading.Event(), threading.Event()
    kill = s._kill_tree
    def pause_reaper(process, job):
        if threading.current_thread().name.startswith('skill-window-'):
            reaping.set()
            assert resume.wait(5)
        else:
            raise OSError('tree already being reaped')
        return kill(process, job)
    monkeypatch.setattr(s, '_kill_tree', pause_reaper)
    h = window_job(sup, tmp_path, 'window_nonzero', sleep=0.2)
    finished(h)
    assert reaping.wait(5)
    try:
        assert sup.close_window(h.job_id) == {"state": "already_closed"}
    finally:
        resume.set()
    wait_until(lambda: not sup._windows)


def test_84a_revision_immediate_nonzero_waits_for_terminal_parser(supervisors, tmp_path, monkeypatch):
    stdout = s.JobHandle._stdout
    def delayed(self, pipe):
        time.sleep(0.3)  # Exceeds the old 0.1 s post-exit join.
        stdout(self, pipe)
    monkeypatch.setattr(s.JobHandle, '_stdout', delayed)
    sup = supervisors(desktop_probe=lambda: True)
    h = window_job(sup, tmp_path, 'window_nonzero', sleep=0)
    value = finished(h)
    assert value['window_retained'] is True and value['exit_code'] is None
    wait_until(lambda: h._window_done.is_set())
    assert h._diagnostics['exit_code'] == 3


def test_84a_revision_stderr_drain_keeps_bytes_without_decoding(supervisors):
    import io
    class Raw(bytearray):
        conversions = 0
        def __bytes__(self):
            self.conversions += 1
            raise AssertionError('drain must not convert/decode the accumulated buffer')
    sup = supervisors(desktop_probe=lambda: True)
    h = s.JobHandle(sup)
    h._discard_stdout = True
    h._window_stderr = Raw()
    h._drain_stderr(io.BytesIO(b'x' * 200000))
    assert h._window_stderr.conversions == 0
    assert len(h._window_stderr) == sup.max_stderr_bytes


def test_84a_revision_handoff_labels_unsampled_priority(supervisors, tmp_path, monkeypatch):
    read = s._read_priority
    def wait_for_result(process, job):
        time.sleep(0.5)  # Result arrives after sample_priority's arrival snapshot.
        return read(process, job)
    monkeypatch.setattr(s, '_read_priority', wait_for_result)
    sup = supervisors(desktop_probe=lambda: True)
    h = window_job(sup, tmp_path, 'slow', sleep=0.3)
    value = finished(h)
    assert value['priority']['at_end'] is None
    assert value['priority']['reason']['at_end'] == 'message arrived after priority monitoring ended'


def test_84b_fixture_close_running_cancels_without_kill(supervisors, tmp_path, monkeypatch):
    from ndstorage import NDTiffDataset
    sup = supervisors(desktop_probe=lambda: True)
    h, dataset, output = fixture_window(sup, tmp_path)
    writer = NDTiffDataset(str(dataset), writable=True)
    writer.initialize({})
    original = s._kill_tree
    calls = []
    def kill(*args):
        calls.append(bool(h._record.get('window_retained')))
        return original(*args)
    monkeypatch.setattr(s, '_kill_tree', kill)
    try:
        write_frame(writer, 0)
        wait_until(lambda: displayed(output, 1))
        assert not sup.list_open_windows()
        assert sup.close_window(h.job_id) == {"state": "stopping"}
        value = finished(h, 'cancelled')
        assert value['result']['input_complete'] is False
        assert value['result']['output']['frames_read'] == 1
        assert value['artifacts'] and all(a['validity'] == 'partial' for a in value['artifacts'])
        assert value['window_retained'] is True
        # Reaper cleanup can follow handoff; only a kill before handoff is forbidden.
        assert all(calls)
    finally:
        writer.finish(); writer.close()


def test_84b_fixture_close_handoff_kills(supervisors, tmp_path, monkeypatch):
    sup = supervisors(desktop_probe=lambda: True)
    h, dataset, output = fixture_window(sup, tmp_path)
    h.notify_acquisition('completed', writer='finished')
    finished(h)
    assert sup.list_open_windows()[0]['job_id'] == h.job_id
    calls = []
    original = s._kill_tree
    def kill(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(s, '_kill_tree', kill)
    assert sup.close_window(h.job_id) == {"state": "closed"}
    assert calls
    wait_until(lambda: not sup.list_open_windows())
    assert sup.close_window(h.job_id) == {"state": "already_closed"}


def test_84b_fixture_close_kill_failure_reports_reason(supervisors, tmp_path, monkeypatch):
    sup = supervisors(desktop_probe=lambda: True)
    h, dataset, output = fixture_window(sup, tmp_path)
    h.notify_acquisition('completed', writer='finished')
    finished(h)
    def fail(*args):
        raise OSError('84b injected kill failure')
    with monkeypatch.context() as patch:
        patch.setattr(s, '_kill_tree', fail)
        for _ in range(9):
            assert sup.close_window(h.job_id) == {"state": "close_failed", "reason": "84b injected kill failure"}
        diagnostics = sup.window_diagnostics(h.job_id)
        assert 'close_failure_count' not in diagnostics
        assert len(diagnostics['cleanup_failures']) == 8
        assert diagnostics['cleanup_failures'][-1] == 'window kill: 84b injected kill failure'
        assert sup.list_open_windows()[0]['job_id'] == h.job_id
    assert sup.close_window(h.job_id) == {"state": "closed"}
