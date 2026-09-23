"""Real subprocess adversaries selected by job parameters; stdlib only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

PROTOCOL = "microclaw.analysis.v1"


def heartbeat(path):
    while True:
        Path(path).write_text(str(time.perf_counter_ns()), encoding="utf-8")
        time.sleep(0.05)


def main():
    # These modes must take effect before the initial job is read. Tests select
    # them by a file in the analysis cwd (the job cannot instruct a non-reader).
    mode = Path("before-job.txt")
    if mode.exists():
        behaviour = mode.read_text(encoding="utf-8")
        spawn_heartbeat(str(Path.cwd() / "heartbeat.txt"))
        if behaviour == "never_read":
            time.sleep(60)
            return
        time.sleep(3)
    job = json.loads(sys.stdin.buffer.readline(65537))
    params = job["parameters"]
    behaviour = params.get("behaviour", "success")
    log = params.get("interval")
    if log:
        Path(log + ".start").write_text(str(time.perf_counter()), encoding="utf-8")

    def message(kind, **fields):
        return dict(protocol=PROTOCOL, type=kind, job_id=job["job_id"], **fields)

    def emit(kind, **fields):
        raw(message(kind, **fields))

    def raw(value):
        sys.stdout.buffer.write((json.dumps(value, separators=(",", ":")) + "\n").encode())
        sys.stdout.buffer.flush()

    artifacts = []
    if job["operation"] != "self_check" and behaviour in {
        "crash", "changed", "latest", "terminal_empty", "second_terminal", "after_terminal", "nonzero", "cancel",
        "artifact_link", "artifact_missing", "artifact_directory", "artifact_hardlink"
    }:
        path = Path(job["output_dir"]) / "sample.txt"
        path.write_bytes(b"partial bytes")
        artifacts = [dict(path=path.name, sha256=hashlib.sha256(b"partial bytes").hexdigest(), validity="final")]
        emit("artifact", artifact=artifacts[0])
        if behaviour == "changed":
            path.write_bytes(b"changed bytes")
        if behaviour == "artifact_link":
            path.unlink()
            path.symlink_to(params["target"])
        if behaviour == "latest":
            path.write_bytes(b"latest bytes")
            artifacts[0]["sha256"] = hashlib.sha256(b"latest bytes").hexdigest()
            emit("artifact", artifact=artifacts[0])
        if behaviour == "terminal_empty":
            artifacts = []
        if behaviour in {"artifact_missing", "artifact_directory"}:
            path.unlink()
            if behaviour == "artifact_directory":
                path.mkdir()
        if behaviour == "artifact_hardlink":
            os.link(path, path.with_suffix(".link"))

    def result(state="succeeded", output=None):
        fields = dict(state=state, output=output or {}, artifacts=artifacts)
        if job["operation"] != "self_check":
            fields["input_complete"] = state == "succeeded"
        if state == "failed":
            fields["failure"] = dict(message="worker failure")
        return message("result", **fields)

    if behaviour in {"startup_hang", "mid_hang", "terminal_hang", "ignore_cancel", "heartbeat_success"}:
        spawn_heartbeat(params["heartbeat"])
    if behaviour == "startup_hang":
        time.sleep(60)
        return
    if behaviour == "partial_line":
        sys.stdout.buffer.write(b'{"protocol":')
        return
    if behaviour == "oversized":
        while True:
            sys.stdout.buffer.write(b"x" * 4096)
            sys.stdout.buffer.flush()
    if behaviour in {"invalid_utf8", "non_json", "non_object"}:
        sys.stdout.buffer.write({"invalid_utf8": b"\xff\n", "non_json": b"not json\n", "non_object": b"[]\n"}[behaviour])
        return
    if behaviour in {"wrong_protocol", "missing_protocol", "wrong_job", "unknown_type", "schema"}:
        value = message("status", message="hi")
        if behaviour == "wrong_protocol":
            value["protocol"] = "other"
        elif behaviour == "missing_protocol":
            del value["protocol"]
        elif behaviour == "wrong_job":
            value["job_id"] = "0" * 32
        elif behaviour == "unknown_type":
            value["type"] = "unknown"
        else:
            value["frames"] = 1
        raw(value)
        return
    if behaviour in {"boundary", "over_boundary"}:
        value = result(output={"padding": ""})
        line = json.dumps(value, separators=(",", ":"))
        value["output"]["padding"] = "x" * (65536 - len(line.encode()) - 1 + (behaviour == "over_boundary"))
        raw(value)
        return
    if behaviour == "split":
        line = (json.dumps(result()) + "\n").encode()
        sys.stdout.buffer.write(line[:20])
        sys.stdout.buffer.flush()
        time.sleep(0.1)
        sys.stdout.buffer.write(line[20:])
        return
    if behaviour in {"crash", "changed", "latest", "no_terminal"}:
        sys.exit(7 if behaviour != "no_terminal" else 0)
    if behaviour == "close_stdin":
        os.close(sys.stdin.fileno())
    emit("status", message="ready")
    if behaviour == "many_artifacts":
        for i in range(257):
            emit("artifact", artifact=dict(path=f"{i}.txt", sha256="0" * 64, validity="partial"))
    if behaviour == "close_stdin":
        time.sleep(params.get("sleep", 2))
    if behaviour == "flood":
        for i in range(params.get("count", 30000)):
            emit("status", message=str(i))
        sys.stderr.buffer.write(b"x" * 200000 + b"TAIL")
        sys.stderr.buffer.flush()
    if behaviour in {"mid_hang", "ignore_cancel"}:
        sys.stderr.write("observer diagnostic\n")
        sys.stderr.flush()
        time.sleep(60)
        return
    if behaviour == "cancel":
        for line in sys.stdin.buffer:
            if json.loads(line)["type"] == "cancel":
                raw(result("cancelled"))
                return
    if behaviour == "slow":
        time.sleep(params.get("sleep", 0.5))
    if behaviour == "lifecycle":
        for line in sys.stdin.buffer:
            value = json.loads(line)
            emit("status", message=json.dumps(value, separators=(",", ":")))
            if value["type"] == "writer":
                break
    output = {"error": "measurement residual"} if behaviour == "output_error" else {}
    if behaviour == "environment":
        output = dict(environment=dict(os.environ), isatty=sys.stdin.isatty(), cwd=os.getcwd())
    raw(result("failed" if behaviour == "failed" else "succeeded", output))
    if behaviour == "second_terminal":
        raw(result())
    elif behaviour == "after_terminal":
        emit("status", message="too late")
    elif behaviour == "nonzero":
        sys.exit(3)
    elif behaviour == "terminal_hang":
        time.sleep(60)
    elif behaviour == "heartbeat_success":
        time.sleep(0.2)
    if log:
        Path(log + ".stop").write_text(str(time.perf_counter()), encoding="utf-8")


def spawn_heartbeat(path):
    subprocess.Popen([sys.executable, "-u", "-m", "conformance_worker.runner", "heartbeat", path],
                     stdin=subprocess.DEVNULL)  # Deliberately inherits stdout/stderr.
    deadline = time.perf_counter() + 5
    while not Path(path).exists():
        if time.perf_counter() > deadline:
            raise RuntimeError("heartbeat child did not start")
        time.sleep(0.01)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        heartbeat(sys.argv[2])
    else:
        main()
