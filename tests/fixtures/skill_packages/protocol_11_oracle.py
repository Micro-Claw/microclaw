"""Frozen 8264727 worker validator for byte regression only."""
from copy import deepcopy
from microclaw.skill_packages import (
    _wire, _mapping, _text, _json_object, _list, validate_artifact, encode_message,
    MAX_STATUS_MESSAGE_LENGTH, MAX_ARTIFACTS, MAX_FAILURE_MESSAGE_LENGTH, PackageRefusal)

def validate_worker_message(message, *, job_id, operation):
    """Validate stdout without touching files; output is opaque publisher JSON."""
    _wire(message, {"status", "artifact", "result"}, job_id)
    kind = message["type"]
    extra = {"status": {"message"}, "artifact": {"artifact"},
             "result": {"state", "output", "artifacts"}}[kind]
    if kind == "result":
        if message.get("state") == "failed":
            extra |= {"failure"}
        if operation != "self_check":
            extra |= {"input_complete"}
    _mapping(message, "", {"protocol", "type", "job_id"} | extra)
    if kind == "status":
        _text(message["message"], "message", MAX_STATUS_MESSAGE_LENGTH)
    elif kind == "artifact":
        if operation == "self_check":
            raise PackageRefusal("artifact", "self_check has no artifacts")
        validate_artifact(message["artifact"])
    else:
        if message["state"] not in ("succeeded", "failed", "cancelled"):
            raise PackageRefusal("state", "unknown terminal state")
        _json_object(message["output"], "output")
        _list(message["artifacts"], "artifacts", MAX_ARTIFACTS, empty=True)
        if operation == "self_check" and message["artifacts"]:
            raise PackageRefusal("artifacts", "self_check has no artifacts")
        for i, descriptor in enumerate(message["artifacts"]):
            validate_artifact(descriptor, f"artifacts[{i}]")
        if message["state"] == "failed":
            _mapping(message["failure"], "failure", {"message"})
            _text(message["failure"]["message"], "failure.message", MAX_FAILURE_MESSAGE_LENGTH)
        if operation != "self_check" and type(message["input_complete"]) is not bool:
            raise PackageRefusal("input_complete", "expected boolean")
    encode_message(message)
    return deepcopy(message)
