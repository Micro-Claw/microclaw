"""Block 14 Phase 4 executor gate for stock MMConfig_demo.cfg.

This probe imports microclaw deliberately.  It exercises the deterministic rig
limbs of G7--G9 and G11; it never snaps an image or opens a shutter.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from microclaw import tools
from microclaw.authorization import RigAuthorizationError, validate_live_rig
from microclaw.config import load_safety_config_or_exit
from microclaw.controller import MicroscopeController
from microclaw.safety import SafetyGuard, SafetyViolation


CHANNEL = "Channel"
PRESETS = ("DAPI", "FITC", "Rhodamine")
G8_EXTRA = ("Camera", "AllowMultiROI")


def vector(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    try:
        return [str(item) for item in value]
    except TypeError:
        return [str(value.get(i)) for i in range(int(value.size()))]


def clean(exc: BaseException) -> str:
    return " ".join(str(exc).split()) or type(exc).__name__


def expand(core: Any, preset: str) -> list[list[str]]:
    config = core.get_config_data(CHANNEL, preset)
    rows = []
    for index in range(int(config.size())):
        setting = config.get_setting(index)
        rows.append([
            str(setting.get_device_label()),
            str(setting.get_property_name()),
            str(setting.get_property_value()),
        ])
    return rows


def all_pairs(core: Any) -> list[tuple[str, str]]:
    pairs = []
    for device in vector(core.get_loaded_devices()):
        for prop in vector(core.get_device_property_names(device)):
            pairs.append((device, prop))
    for prop in ("Shutter", "AutoShutter", "ChannelGroup"):
        try:
            core.get_property("Core", prop)
        except Exception:
            continue
        pairs.append(("Core", prop))
    return sorted(set(pairs))


def snapshot(core: Any, pairs: list[tuple[str, str]]) -> dict[str, Any]:
    out = {}
    for device, prop in pairs:
        key = json.dumps([device, prop], separators=(",", ":"))
        try:
            out[key] = {"ok": True, "value": str(core.get_property(device, prop))}
        except Exception as exc:
            out[key] = {"ok": False, "error": clean(exc)}
    return out


def diff(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"pair": json.loads(key), "before": left.get(key), "after": right.get(key)}
        for key in sorted(set(left) | set(right)) if left.get(key) != right.get(key)
    ]


def demo_evidence(core: Any) -> dict[str, Any]:
    devices = []
    present = False
    for device in vector(core.get_loaded_devices()):
        library = str(core.get_device_library(device))
        devices.append({"device": device, "library": library})
        present = present or library == "DemoCamera"
    return {"present": present, "devices": devices}


class RecordingCore:
    """Transparent core shim that makes set_config use impossible to miss."""

    def __init__(self, core: Any):
        self.raw = core
        self.sequence: list[dict[str, Any]] = []
        self.set_config_violations: list[dict[str, Any]] = []
        self.recording = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.raw, name)

    def set_config(self, *args: Any, **kwargs: Any) -> None:
        violation = {"args": [str(x) for x in args], "kwargs": kwargs}
        self.set_config_violations.append(violation)
        raise RuntimeError("GATE VIOLATION: executor called ctrl.core.set_config")

    def set_property(self, device: str, prop: str, value: Any) -> Any:
        if self.recording:
            self.sequence.append({"operation": "set", "device": str(device),
                                  "property": str(prop), "value": str(value)})
        return self.raw.set_property(device, prop, value)

    def wait_for_device(self, device: str) -> Any:
        if self.recording:
            self.sequence.append({"operation": "wait", "device": str(device)})
        return self.raw.wait_for_device(device)

    def get_property(self, device: str, prop: str) -> Any:
        value = self.raw.get_property(device, prop)
        if self.recording:
            self.sequence.append({"operation": "read", "device": str(device),
                                  "property": str(prop), "value": str(value)})
        return value


def redefine(raw: Any, preset: str, rows: list[list[str]]) -> None:
    raw.delete_config(CHANNEL, preset)
    for device, prop, value in rows:
        raw.define_config(CHANNEL, preset, device, prop, value)


def restore_definition(raw: Any, preset: str, original: list[list[str]]) -> None:
    try:
        redefine(raw, preset, original)
        restored = expand(raw, preset)
    except Exception as exc:
        raise RuntimeError(
            f"RESTORE FAILED for {CHANNEL}/{preset}: {clean(exc)}. Stop using the rig; "
            "reload stock MMConfig_demo.cfg in the MM GUI before another run."
        ) from exc
    if restored != original:
        raise RuntimeError(
            f"RESTORE MISMATCH for {CHANNEL}/{preset}: expected {original!r}, got "
            f"{restored!r}. Stop using the rig; reload stock MMConfig_demo.cfg in "
            "the MM GUI before another run."
        )


def apply(ctrl: MicroscopeController, guard: SafetyGuard, recorder: RecordingCore,
          pairs: list[tuple[str, str]], preset: str, confirm: bool) -> dict[str, Any]:
    before = snapshot(recorder.raw, pairs)
    recorder.sequence = []
    tools.CONFIRM_FN = lambda summary, kind="action": confirm
    error = None
    result = None
    recorder.recording = True
    try:
        result = tools.set_channel(ctrl, guard, preset)
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": clean(exc)}
    finally:
        recorder.recording = False
    after = snapshot(recorder.raw, pairs)
    verifications = []
    for index, step in enumerate(recorder.sequence):
        if step["operation"] != "set":
            continue
        read_back = next((later["value"] for later in recorder.sequence[index + 1:]
                          if later.get("operation") == "read"
                          and later.get("device") == step["device"]
                          and later.get("property") == step["property"]), None)
        verifications.append({"device": step["device"], "property": step["property"],
                              "requested": step["value"], "read_back": read_back})
    return {
        "preset": preset, "confirmation": confirm, "result": result, "error": error,
        "before": before, "after": after, "structural_diff": diff(before, after),
        "sequence": list(recorder.sequence), "effect_verifications": verifications,
        "current_config": str(recorder.raw.get_current_config(CHANNEL)),
    }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def restore_properties(core: Any, before: dict[str, Any]) -> list[dict[str, Any]]:
    attempts = []
    now = snapshot(core, [tuple(json.loads(key)) for key in before])
    for change in reversed(diff(before, now)):
        device, prop = change["pair"]
        old = change["before"]
        if not old or not old.get("ok"):
            continue
        try:
            core.set_property(device, prop, old["value"])
            if device != "Core":
                core.wait_for_device(device)
            actual = str(core.get_property(device, prop))
            attempts.append({"pair": [device, prop], "requested": old["value"],
                             "read_back": actual, "ok": actual == old["value"]})
        except Exception as exc:
            attempts.append({"pair": [device, prop], "ok": False, "error": clean(exc)})
    return attempts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()

    report: dict[str, Any] = {"errors": [], "sections": {}, "exit_code": 1}
    lines = ["BLOCK 14 PHASE 4 EXECUTOR PROBE"]
    raw = None
    pre = None
    pairs: list[tuple[str, str]] = []
    originals: dict[str, list[list[str]]] = {}
    recorder = None
    try:
        parsed = load_safety_config_or_exit(args.config)
        guard = SafetyGuard(parsed.constraints)
        ctrl = MicroscopeController(port=args.port, guard=guard)
        require(ctrl.is_connected(), f"controller did not connect on port {args.port}")
        raw = ctrl.core
        demo = demo_evidence(raw)
        report["demo_guard"] = demo
        require(demo["present"], "HARD ABORT: no loaded device uses the DemoCamera adapter")
        authorization_map = validate_live_rig(ctrl, parsed, guard=guard)
        report["authorization_map"] = authorization_map.to_dict()
        pairs = all_pairs(raw)
        pre = snapshot(raw, pairs)
        report["pre_run_snapshot"] = pre
        originals = {preset: expand(raw, preset) for preset in PRESETS}

        recorder = RecordingCore(raw)
        ctrl._core = recorder

        # Run this first: any ordinary Channel apply selects White Light Shutter
        # and would make the retarget assertion vacuous.
        lines.append("\nG11 — non-vacuous shutter retarget")
        active = str(raw.get_shutter_device())
        require(active == "LED Shutter",
                "G11 HARD ABORT: active shutter is not 'LED Shutter'. Set LED Shutter "
                "as the active shutter in the MM GUI before starting the probe.")
        declined = apply(ctrl, guard, recorder, pairs, "FITC", False)
        require(declined["error"] is not None and not declined["structural_diff"] and
                not any(step["operation"] == "set" for step in declined["sequence"]),
                "G11 declined confirmation did not refuse before mutation")
        accepted = apply(ctrl, guard, recorder, pairs, "FITC", True)
        shutters = {name: bool(raw.get_shutter_open(name))
                    for name in ("LED Shutter", "White Light Shutter")}
        require(str(raw.get_shutter_device()) == "White Light Shutter",
                "G11 accepted apply did not retarget the active shutter")
        require(not any(shutters.values()), f"G11 left a shutter open: {shutters}")
        core_reads = [step for step in accepted["sequence"] if step.get("operation") == "read"
                      and step.get("device") == "Core" and step.get("property") == "Shutter"]
        require(core_reads and core_reads[-1]["value"] == "White Light Shutter",
                "G11 lacks verified Core.Shutter read-back")
        raw.set_property("Core", "Shutter", active)
        require(str(raw.get_shutter_device()) == active, "G11 failed to restore active shutter")
        report["sections"]["G11"] = {"original_active_shutter": active,
            "declined": declined, "accepted": accepted, "shutters_after_accept": shutters,
            "restored_active_shutter": str(raw.get_shutter_device())}
        lines.append("PASS — decline was inert; accept retargeted while both shutters stayed closed.")

        lines.append("\nG7 — authorized captured apply")
        g7 = [apply(ctrl, guard, recorder, pairs, preset, True) for preset in PRESETS]
        for item in g7:
            require(item["error"] is None, f"G7 {item['preset']} failed: {item['error']}")
            require(item["result"]["writes"] > 0, f"G7 {item['preset']} made no writes")
            require(item["result"]["expansion_drift"] is False,
                    f"G7 {item['preset']} unexpectedly drifted")
            require(item["result"]["startup_expansion_sha256"] ==
                    item["result"]["applied_expansion_sha256"], "G7 hashes differ")
            require(item["current_config"] == item["preset"], "G7 Channel bookkeeping mismatch")
        report["sections"]["G7"] = g7
        lines.append("PASS — DAPI, FITC, and Rhodamine applied by ordered replay.")

        lines.append("\nG8 — refusals before mutation")
        absent = apply(ctrl, guard, recorder, pairs, "NOT_ALLOWED_BY_PROFILE", True)
        require(absent["error"] is not None and absent["error"]["type"] == SafetyViolation.__name__,
                "G8 absent preset did not raise SafetyViolation")
        require(not absent["structural_diff"] and not any(
            step["operation"] == "set" for step in absent["sequence"]),
            "G8 absent preset mutated state")
        original = originals["DAPI"]
        extra_old = str(raw.get_property(*G8_EXTRA))
        try:
            raw.define_config(CHANNEL, "DAPI", *G8_EXTRA, extra_old)
            injected = apply(ctrl, guard, recorder, pairs, "DAPI", True)
            require(injected["error"] is not None and
                    injected["error"]["type"] == RigAuthorizationError.__name__,
                    "G8 undeclared effect did not raise named RigAuthorizationError")
            require(not injected["structural_diff"] and not any(
                step["operation"] == "set" for step in injected["sequence"]),
                "G8 undeclared effect wrote before refusal")
        finally:
            restore_definition(raw, "DAPI", original)
        report["sections"]["G8"] = {"absent_preset": absent,
                                            "undeclared_effect": injected,
                                            "restored_expansion": expand(raw, "DAPI")}
        lines.append("PASS — both unsafe plans refused with zero writes.")

        lines.append("\nG9 — live definition drift")
        original = originals["DAPI"]
        changed = [row[:] for row in original]
        target = next(row for row in changed if row[0] != "Core")
        allowed = vector(raw.get_allowed_property_values(target[0], target[1]))
        replacement = next((value for value in allowed if value != target[2]), None)
        require(replacement is not None, f"G9 found no alternate for {target[0]}.{target[1]}")
        target[2] = replacement
        try:
            redefine(raw, "DAPI", changed)
            drift = apply(ctrl, guard, recorder, pairs, "DAPI", True)
            require(drift["error"] is None, f"G9 apply failed: {drift['error']}")
            require(drift["result"]["expansion_drift"] is True, "G9 did not report drift")
            require(drift["result"]["startup_expansion_sha256"] !=
                    drift["result"]["applied_expansion_sha256"], "G9 hashes are equal")
            require(str(raw.get_property(target[0], target[1])) == replacement,
                    "G9 did not apply the freshly captured categorical value")
        finally:
            restore_definition(raw, "DAPI", original)
        report["sections"]["G9"] = {"changed_effect": target, "apply": drift,
                                            "restored_expansion": expand(raw, "DAPI")}
        lines.append("PASS — fresh authorized value applied and drift proved by unequal hashes.")

        require(not recorder.set_config_violations,
                f"set_config gate violations: {recorder.set_config_violations}")
        report["exit_code"] = 0
    except Exception as exc:
        report["errors"].append({"type": type(exc).__name__, "message": clean(exc)})
        lines.append(f"\nFAIL — {type(exc).__name__}: {clean(exc)}")
    finally:
        if raw is not None and pre is not None:
            report["restore_attempts"] = restore_properties(raw, pre)
            final = snapshot(raw, pairs)
            report["final_snapshot"] = final
            report["residue"] = diff(pre, final)
            if report["residue"]:
                report["errors"].append({"type": "ResidueError",
                                         "message": "full property snapshot was not restored"})
                report["exit_code"] = 1
        if recorder is not None:
            report["set_config_violations"] = recorder.set_config_violations
            if recorder.set_config_violations:
                report["exit_code"] = 1
        if report["errors"]:
            report["exit_code"] = 1

    lines.append(f"\nRESIDUE (must be empty): {json.dumps(report.get('residue', []), sort_keys=True)}")
    lines.append("\n=== MACHINE_READABLE_JSON ===")
    lines.append(json.dumps(report, indent=2, sort_keys=True, default=str))
    text = "\n".join(lines) + "\n"
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(text, encoding="utf-8")
    print(text, end="")
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
