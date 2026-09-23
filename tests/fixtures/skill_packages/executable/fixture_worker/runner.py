"""Reference v1 worker: self-check and lifecycle observation, without hardware."""
import hashlib
import json
from pathlib import Path
import sys

PROTOCOL = "microclaw.analysis.v1"


def main():
    job = json.loads(sys.stdin.buffer.readline(65536))
    if job["protocol"] != PROTOCOL or job["type"] != "job":
        raise ValueError("expected v1 job")

    def emit(kind, **fields):
        print(json.dumps(dict(protocol=PROTOCOL, type=kind, job_id=job["job_id"], **fields)), flush=True)

    def result(state, artifacts, complete):
        fields = dict(state=state, output={"observed": True}, artifacts=artifacts)
        if job["operation"] != "self_check":
            fields["input_complete"] = complete
        emit("result", **fields)

    emit("status", message="ready")
    if job["operation"] == "self_check":
        result("succeeded", [], True)
        return
    if job["operation"] != "observe_dataset":
        raise ValueError("unknown operation")
    path = Path(job["output_dir"]) / "observation.txt"

    def artifact(validity, content):
        path.write_bytes(content)
        return dict(path=path.name, sha256=hashlib.sha256(content).hexdigest(), validity=validity)

    descriptor = artifact("partial", b"observing dataset\n")
    emit("artifact", artifact=descriptor)
    for line in sys.stdin.buffer:
        message = json.loads(line)
        if message["protocol"] != PROTOCOL or message["job_id"] != job["job_id"]:
            raise ValueError("wrong lifecycle identity")
        emit("status", message=json.dumps(message, separators=(",", ":")))
        if message["type"] == "cancel":
            result("cancelled", [descriptor], False)
            return
        if message["type"] == "writer" or (message["type"] == "acquisition" and
                                             message["outcome"] == "completed" and message["writer"] == "finished"):
            descriptor = artifact("final", b"dataset writer finished\n")
            result("succeeded", [descriptor], True)
            return
    result("cancelled", [descriptor], False)


if __name__ == "__main__":
    main()
