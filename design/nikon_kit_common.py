"""Shared validation, identity, output, restoration, and motion safety for Nikon probes."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
import time
import io
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

EXPECTED = {"focus": "TIZDrive", "autofocus": "TIPFSStatus"}
DEVICES = ("TIZDrive", "TIPFSStatus", "TIPFSOffset")


class Refusal(RuntimeError):
    """A plain-language, operator-actionable refusal."""


def strings(value: Any) -> list[str]:
    try:
        return [str(x) for x in value]
    except TypeError:
        out, index = [], 0
        while True:
            try:
                out.append(str(value.get(index)))
            except Exception:
                return out
            index += 1


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finite(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("must be a finite number")
    return number


def positive(value: str) -> float:
    number = finite(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--rig-id", required=True, help="short rig identifier (letters, digits, ._-)")
    parser.add_argument("--config-path", required=True, type=Path,
                        help="exact loaded Micro-Manager .cfg file")


def validate_local_arguments(args: argparse.Namespace) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.rig_id):
        raise Refusal("REFUSED: --rig-id may contain only letters, digits, dot, underscore, and hyphen.")
    if not args.config_path.is_file():
        raise Refusal(f"REFUSED: --config-path is not a readable file: {args.config_path}")
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise Refusal(f"REFUSED: cannot create --output-dir {args.output_dir}: {error}") from None


def connect() -> Any:
    try:
        from pycromanager import Core
        # pyjavaz otherwise prints a background-thread traceback before Core()
        # reports the useful connection error. The operator needs one message.
        with redirect_stderr(io.StringIO()):
            return Core()
    except Exception as error:
        raise Refusal("REFUSED: cannot connect to Micro-Manager. Start Micro-Manager and its "
                      f"pycromanager/ZMQ server, then retry. Detail: {error}") from None


def validate_core(core: Any) -> dict[str, str]:
    try:
        loaded = strings(core.get_loaded_devices())
        focus = str(core.get_focus_device())
        autofocus = str(core.get_auto_focus_device())
    except Exception as error:
        raise Refusal("REFUSED: the Micro-Manager connection did not answer core identity "
                      f"queries. Detail: {error}") from None
    missing = [item for item in DEVICES if item not in loaded]
    if missing:
        raise Refusal("REFUSED: expected device(s) are not loaded: " + ", ".join(missing) + ".")
    if focus != EXPECTED["focus"]:
        raise Refusal(f"REFUSED: Core.Focus is {focus!r}; it must be 'TIZDrive'.")
    if autofocus != EXPECTED["autofocus"]:
        raise Refusal(f"REFUSED: Core.AutoFocus is {autofocus!r}; it must be 'TIPFSStatus'.")
    return {"focus": focus, "autofocus": autofocus, "offset": "TIPFSOffset"}


def safe_call(core: Any, method: str) -> Any:
    try:
        return getattr(core, method)()
    except Exception as error:
        return {"unavailable": str(error)}


def device_identity(core: Any, device: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for prop in ("AdapterName", "Description", "DeviceName", "FirmwareVersion", "Version"):
        try:
            if core.has_property(device, prop):
                result[prop] = str(core.get_property(device, prop))
        except Exception as error:
            result[prop] = {"read_error": str(error)}
    try:
        result["device_library"] = str(core.get_device_library(device))
    except Exception as error:
        result["device_library"] = {"unavailable": str(error)}
    return result


def identify(core: Any, script: Path, common: Path, args: argparse.Namespace,
             assignments: dict[str, str], stamp: str) -> dict[str, Any]:
    return {
        "utc_timestamp": stamp,
        "micro_manager": {"version": safe_call(core, "get_version_info"),
                          "api_version": safe_call(core, "get_api_version_info")},
        "device_assignments": assignments,
        "devices": {name: device_identity(core, name) for name in DEVICES},
        "loaded_config": {"path": str(args.config_path.resolve()),
                          "sha256": sha256(args.config_path),
                          "source": "operator-declared --config-path"},
        "script": {"path": script.name, "sha256": sha256(script)},
        "common": {"path": common.name, "sha256": sha256(common)},
        "rig_id": args.rig_id,
    }


def snapshot(core: Any, include_offset: bool = False) -> dict[str, Any]:
    state = {"pfs_state": str(core.get_property("TIPFSStatus", "State")),
             "pfs_status": str(core.get_property("TIPFSStatus", "Status")),
             "z_um": float(core.get_position("TIZDrive"))}
    try:
        state["continuous_focus_enabled"] = bool(core.is_continuous_focus_enabled())
    except Exception as error:
        state["continuous_focus_enabled_error"] = str(error)
    if include_offset:
        state["offset"] = float(core.get_position("TIPFSOffset"))
    return state


def restore_pfs(core: Any, initial: dict[str, Any]) -> dict[str, Any]:
    target = initial["pfs_state"]
    result = {"contract": "restore initial TIPFSStatus.State", "target": target, "ok": False}
    try:
        core.set_property("TIPFSStatus", "State", target)
        core.wait_for_device("TIPFSStatus")
        result["readback"] = str(core.get_property("TIPFSStatus", "State"))
        result["ok"] = result["readback"] == target
        if not result["ok"]:
            result["error"] = "read-back differs from snapshot"
    except Exception as error:
        result["error"] = str(error)
    return result


def force_pfs_off(core: Any, _initial: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {"contract": "force TIPFSStatus.State Off", "target": "Off", "ok": False}
    try:
        core.set_property("TIPFSStatus", "State", "Off")
        core.wait_for_device("TIPFSStatus")
        result["readback"] = str(core.get_property("TIPFSStatus", "State"))
        result["ok"] = result["readback"] == "Off"
        if not result["ok"]:
            result["error"] = "read-back is not Off"
    except Exception as error:
        result["error"] = str(error)
    return result


def observation(core: Any, started: float) -> dict[str, Any]:
    return {"elapsed_s": round(time.monotonic() - started, 6),
            "z_um": float(core.get_position("TIZDrive")),
            "state": str(core.get_property("TIPFSStatus", "State")),
            "status": str(core.get_property("TIPFSStatus", "Status")),
            "continuous_focus_enabled": _continuous(core, "enabled"),
            "continuous_focus_locked": _continuous(core, "locked")}


def _continuous(core: Any, which: str) -> Any:
    try:
        method = "is_continuous_focus_enabled" if which == "enabled" else "is_continuous_focus_locked"
        return bool(getattr(core, method)())
    except Exception as error:
        return {"unavailable": str(error)}


class MotionGuard:
    def __init__(self, core: Any, ceiling: float, confirmed_ceiling: float,
                 min_z: float, max_step: float, travel_budget: float):
        if ceiling > confirmed_ceiling:
            raise Refusal(f"REFUSED: --z-ceiling {ceiling} is above operator-confirmed "
                          f"--confirmed-z-ceiling {confirmed_ceiling}.")
        if min_z > ceiling:
            raise Refusal("REFUSED: --z-minimum is above --z-ceiling.")
        self.core, self.minimum, self.ceiling = core, min_z, ceiling
        self.max_step, self.budget, self.travel = max_step, travel_budget, 0.0

    def check_position(self, z: float) -> None:
        if not self.minimum <= z <= self.ceiling:
            raise Refusal(f"Z ENVELOPE BREACH — ABORTED: measured TIZDrive {z} um is "
                          f"outside the confirmed envelope [{self.minimum}, "
                          f"{self.ceiling}] um.")

    def approve(self, current: float, target: float) -> None:
        self.check_position(current)
        self.check_position(target)
        distance = abs(target - current)
        if distance > self.max_step:
            raise Refusal(f"REFUSED: planned step {distance} um exceeds --max-step {self.max_step} um.")
        if self.travel + distance > self.budget:
            raise Refusal("REFUSED: planned move would exceed --travel-budget.")
        self.travel += distance


def atomic_outputs(output_dir: Path, stem: str, payload: dict[str, Any], lines: list[str]) -> tuple[Path, Path]:
    json_path, text_path = output_dir / f"{stem}.json", output_dir / f"{stem}.txt"
    _atomic(json_path, json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    _atomic(text_path, "\n".join(lines) + "\n")
    return json_path, text_path


def _atomic(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def run_probe(probe: str, script: Path, args: argparse.Namespace,
              body: Callable[[Any, dict[str, Any], list[str]], None],
              cleanup: Callable[[Any, dict[str, Any]], dict[str, Any]] | None,
              include_offset: bool = False) -> int:
    core = None
    payload: dict[str, Any] = {"probe": probe, "completed": False}
    log = [f"Probe: {probe}"]
    stamp = utc_stamp()
    try:
        validate_local_arguments(args)
        core = connect()
        assignments = validate_core(core)
        payload["identity"] = identify(core, script, Path(__file__), args, assignments, stamp)
        payload["initial_state"] = snapshot(core, include_offset)
        body(core, payload, log)
        payload["completed"] = True
    except (Refusal, KeyboardInterrupt) as error:
        message = str(error) if isinstance(error, Refusal) else "ABORTED: operator interrupt received."
        payload["error"] = message
        log.append(message)
        print(message)
    except Exception as error:
        message = f"ABORTED: unexpected probe error: {type(error).__name__}: {error}"
        payload["error"] = message
        log.append(message)
        print(message)
    finally:
        if core is not None and "initial_state" in payload:
            payload["restoration"] = cleanup(core, payload["initial_state"]) if cleanup else {
                "contract": "read-only; no state changed", "ok": True}
            if not payload["restoration"].get("ok"):
                warning = "RESTORATION FAILED: " + str(payload["restoration"])
                payload["restoration_failure"] = warning
                log.append(warning)
                print(warning)
        if "identity" in payload:
            stem = f"{probe}-{stamp}-{args.rig_id}"
            try:
                paths = atomic_outputs(args.output_dir, stem, payload, log)
                print(f"Wrote {paths[0].name} and {paths[1].name}")
            except Exception as error:
                print(f"ABORTED: could not write output files atomically: {error}")
                return 2
    return 0 if payload.get("completed") and payload.get("restoration", {}).get("ok") else 2
