"""Block 14 Phase 4 measurement spike against a running Micro-Manager demo core.

This is deliberately a standalone rig probe, not executor implementation.  It
mutates the demo core, records what MM actually does, and restores a pre-run
snapshot.  Start MM's ZMQ server first and pass its port.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from pycromanager import Core


sys.stdout.reconfigure(line_buffering=True)
SCRATCH_GROUP = "microclaw_block14_phase4_scratch"
BAD_VALUE = "__MICROCLAW_INTENTIONAL_BAD_VALUE__"


def clean_exception(exc: BaseException) -> str:
    message = next(
        (line.strip() for line in str(exc).splitlines() if line.strip()),
        type(exc).__name__,
    )
    for prefix in ("java.lang.", "mmcorej.", "org.micromanager."):
        message = message.replace(prefix, "")
    return message


def vector(value: Any) -> list[str]:
    """Drain bridge vectors explicitly; StrVector is not Python-iterable."""
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value.get(i)) for i in range(int(value.size()))]


def call_or_field(obj: Any, names: tuple[str, ...]) -> tuple[Any, str]:
    errors: list[str] = []
    for name in names:
        try:
            member = getattr(obj, name)
            return (member() if callable(member) else member), name
        except Exception as exc:
            errors.append(f"{name}: {clean_exception(exc)}")
    raise RuntimeError("; ".join(errors))


def config_settings(config: Any) -> list[Any]:
    for size_name, get_name in (
        ("size", "get_setting"),
        ("size", "getSetting"),
        ("get_number_of_settings", "get_setting"),
    ):
        size = getattr(config, size_name, None)
        get = getattr(config, get_name, None)
        if callable(size) and callable(get):
            return [get(i) for i in range(int(size()))]
    raise RuntimeError(
        "get_config_data returned an unsupported bridge object: "
        f"{type(config).__name__}; dir={dir(config)!r}"
    )


def expand(core: Any, group: str, preset: str) -> dict[str, Any]:
    config = core.get_config_data(group, preset)
    settings = config_settings(config)
    rows = []
    for index, setting in enumerate(settings):
        device, device_source = call_or_field(
            setting, ("device", "device_label", "get_device_label", "getDeviceLabel")
        )
        prop, property_source = call_or_field(
            setting, ("property", "property_name", "get_property_name", "getPropertyName")
        )
        value, value_source = call_or_field(
            setting, ("value", "property_value", "get_property_value", "getPropertyValue")
        )
        rows.append({
            "index": index,
            "device": str(device),
            "property": str(prop),
            "value": str(value),
            "accessors": {
                "device": device_source,
                "property": property_source,
                "value": value_source,
            },
            "type": type(setting).__name__,
            "dir": sorted(str(name) for name in dir(setting)),
        })
    return {
        "config_type": type(config).__name__,
        "config_dir": sorted(str(name) for name in dir(config)),
        "settings": rows,
    }


class Evidence:
    def __init__(self, path: Path):
        self.path = path
        self.lines: list[str] = []
        self.data: dict[str, Any] = {
            "schema": "microclaw.block14.phase4.mm-apply-spike.v1",
            "started_unix_s": time.time(),
            "sections": {},
            "errors": [],
        }

    def line(self, value: str = "") -> None:
        print(value)
        self.lines.append(value)

    def section(self, title: str) -> None:
        self.line(f"\n=== {title} ===")

    def show(self, label: str, value: Any) -> None:
        self.line(f"{label}: {json.dumps(value, sort_keys=True, default=str)}")

    def write(self) -> None:
        self.data["finished_unix_s"] = time.time()
        blob = json.dumps(self.data, indent=2, sort_keys=True, default=str)
        text = "\n".join(self.lines) + "\n\n=== MACHINE_READABLE_JSON ===\n" + blob + "\n"
        self.path.write_text(text, encoding="utf-8")
        print("\n=== MACHINE_READABLE_JSON ===")
        print(blob)


def all_pairs(core: Any) -> list[tuple[str, str]]:
    pairs = []
    for device in vector(core.get_loaded_devices()):
        for prop in vector(core.get_device_property_names(device)):
            pairs.append((device, prop))
    return pairs


def snapshot(core: Any, pairs: list[tuple[str, str]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for device, prop in pairs:
        key = json.dumps([device, prop], separators=(",", ":"))
        try:
            result[key] = {"ok": True, "value": str(core.get_property(device, prop))}
        except Exception as exc:
            result[key] = {"ok": False, "error": clean_exception(exc)}
    return result


def snapshot_diff(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for key in sorted(set(left) | set(right)):
        if left.get(key) != right.get(key):
            out.append({"pair": json.loads(key), "left": left.get(key), "right": right.get(key)})
    return out


def is_read_only(core: Any, device: str, prop: str) -> bool:
    return bool(core.is_property_read_only(device, prop))


def restore(core: Any, before: dict[str, Any]) -> dict[str, Any]:
    attempts = []
    for key, old in before.items():
        if not old.get("ok"):
            continue
        device, prop = json.loads(key)
        try:
            now = str(core.get_property(device, prop))
            if now == old["value"]:
                continue
            if is_read_only(core, device, prop):
                attempts.append({"pair": [device, prop], "status": "changed-read-only", "current": now})
                continue
            core.set_property(device, prop, old["value"])
            if device != "Core":
                core.wait_for_device(device)
            attempts.append({"pair": [device, prop], "status": "restored", "from": now, "to": old["value"]})
        except Exception as exc:
            attempts.append({"pair": [device, prop], "status": "restore-error", "error": clean_exception(exc)})
    return {"attempts": attempts}


def config_state(core: Any) -> dict[str, Any]:
    try:
        return {"ok": True, "value": str(core.get_current_config("Channel"))}
    except Exception as exc:
        return {"ok": False, "error": clean_exception(exc)}


def busy_state(core: Any, devices: list[str]) -> dict[str, Any]:
    state: dict[str, Any] = {}
    try:
        state["system_busy"] = bool(core.system_busy())
    except Exception as exc:
        state["system_busy_error"] = clean_exception(exc)
    state["devices"] = {}
    for device in devices:
        if device == "Core":
            continue
        try:
            state["devices"][device] = bool(core.device_busy(device))
        except Exception as exc:
            state["devices"][device] = {"error": clean_exception(exc)}
    return state


def timed(fn: Callable[[], Any]) -> tuple[Any, float]:
    start = time.perf_counter()
    result = fn()
    return result, (time.perf_counter() - start) * 1000.0


def apply_loop(core: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    writes = []
    start = time.perf_counter()
    for row in rows:
        device, prop, requested = row["device"], row["property"], row["value"]
        one = time.perf_counter()
        try:
            core.set_property(device, prop, requested)
            set_ms = (time.perf_counter() - one) * 1000.0
            wait_ms = None
            if device != "Core":
                _, wait_ms = timed(lambda d=device: core.wait_for_device(d))
            actual = str(core.get_property(device, prop))
            writes.append({
                "index": row["index"], "device": device, "property": prop,
                "requested": requested, "read_back": actual,
                "exact": requested == actual, "set_ms": set_ms, "wait_ms": wait_ms,
            })
        except Exception as exc:
            writes.append({
                "index": row["index"], "device": device, "property": prop,
                "requested": requested, "error": clean_exception(exc),
            })
            raise RuntimeError(json.dumps(writes[-1], sort_keys=True)) from exc
    return {"total_ms": (time.perf_counter() - start) * 1000.0, "writes": writes}


def demo_guard(core: Any) -> dict[str, Any]:
    evidence = []
    found = False
    for device in vector(core.get_loaded_devices()):
        try:
            library = str(core.get_device_library(device))
            evidence.append({"device": device, "library": library})
            found = found or library == "DemoCamera"
        except Exception as exc:
            evidence.append({"device": device, "error": clean_exception(exc)})
    return {"demo_camera_detected": found, "devices": evidence}


def alternate(core: Any, device: str, prop: str, current: str) -> tuple[str | None, str | None]:
    try:
        allowed = vector(core.get_allowed_property_values(device, prop))
        return next((value for value in allowed if value != current), None), None
    except Exception as exc:
        return None, clean_exception(exc)


def scratch_triplet(
    core: Any, pairs: list[tuple[str, str]]
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    candidates = []
    diagnostics: list[dict[str, Any]] = []
    for device, prop in pairs:
        if device == "Core":
            continue
        try:
            if is_read_only(core, device, prop) or bool(core.is_property_pre_init(device, prop)):
                continue
            current = str(core.get_property(device, prop))
            other, allowed_error = alternate(core, device, prop, current)
            if allowed_error is not None:
                diagnostics.append({"pair": [device, prop], "allowed_values_error": allowed_error})
            if other is not None:
                candidates.append({"device": device, "property": prop, "before": current, "test": other})
        except Exception as exc:
            diagnostics.append({"pair": [device, prop], "candidate_error": clean_exception(exc)})
    if len(candidates) < 3:
        raise RuntimeError("Need three distinct reversible writable enumerated DemoCamera properties for scratch test")
    first, bad, third = candidates[:3]
    return [first, {**bad, "test": BAD_VALUE}, third], diagnostics


def delete_scratch(core: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"attempted": True}
    try:
        core.delete_config_group(SCRATCH_GROUP)
        result["delete_call"] = "delete_config_group"
    except Exception as group_exc:
        result["delete_config_group_error"] = clean_exception(group_exc)
        try:
            for preset in vector(core.get_available_configs(SCRATCH_GROUP)):
                core.delete_config(SCRATCH_GROUP, preset)
            core.delete_config_group(SCRATCH_GROUP)
            result["delete_call"] = "delete_config then delete_config_group"
        except Exception as exc:
            result["delete_error"] = clean_exception(exc)
    try:
        result["still_present"] = SCRATCH_GROUP in vector(core.get_available_config_groups())
    except Exception as exc:
        result["verification_error"] = clean_exception(exc)
    return result


def define_scratch(core: Any, preset: str, triplet: list[dict[str, str]]) -> None:
    for row in triplet:
        core.define_config(SCRATCH_GROUP, preset, row["device"], row["property"], row["test"])


def shutter_probe(core: Any, channel_rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"core_shutter_effects": []}
    for row in channel_rows:
        if row["device"] == "Core" and row["property"] == "Shutter":
            result["core_shutter_effects"].append(row)
    shutter_names = set()
    try:
        active = str(core.get_shutter_device())
        result["before_active_shutter"] = active
        if active:
            shutter_names.add(active)
    except Exception as exc:
        result["before_active_shutter_error"] = clean_exception(exc)
    for row in result["core_shutter_effects"]:
        if row["value"]:
            shutter_names.add(row["value"])
    def observe() -> dict[str, Any]:
        out: dict[str, Any] = {}
        try: out["active_shutter"] = str(core.get_shutter_device())
        except Exception as exc: out["active_shutter_error"] = clean_exception(exc)
        try: out["auto_shutter"] = str(core.get_property("Core", "AutoShutter"))
        except Exception as exc: out["auto_shutter_error"] = clean_exception(exc)
        out["open"] = {}
        for name in sorted(shutter_names):
            try: out["open"][name] = bool(core.get_shutter_open(name))
            except Exception as exc: out["open"][name] = {"error": clean_exception(exc)}
        return out
    result["before"] = observe()
    target = next((row["value"] for row in result["core_shutter_effects"] if row["value"]), None)
    if target is not None:
        try:
            core.set_property("Core", "Shutter", target)
            result["set_property"] = {"permitted": True, "target": target}
        except Exception as exc:
            result["set_property"] = {"permitted": False, "target": target, "error": clean_exception(exc)}
        result["after"] = observe()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument(
        "--allow-non-demo", action="store_true",
        help="override the DemoCamera hard stop for development only; NOT FOR M5",
    )
    args = parser.parse_args()
    ev = Evidence(args.evidence)
    core = Core(port=args.port)
    pre: dict[str, Any] | None = None
    pairs: list[tuple[str, str]] = []
    exit_code = 0
    try:
        ev.section("Safety gate and pre-run snapshot")
        guard = demo_guard(core)
        ev.data["demo_guard"] = guard
        ev.show("Demo detection", guard)
        if not guard["demo_camera_detected"] and not args.allow_non_demo:
            raise RuntimeError("HARD ABORT: no loaded device uses the DemoCamera adapter")
        if args.allow_non_demo:
            ev.line("WARNING: --allow-non-demo is active. This override is NOT FOR M5.")
        if SCRATCH_GROUP in vector(core.get_available_config_groups()):
            raise RuntimeError(f"HARD ABORT: scratch group {SCRATCH_GROUP!r} already exists")
        pairs = all_pairs(core)
        pre = snapshot(core, pairs)
        ev.data["pre_snapshot"] = pre
        ev.show("Property count", len(pairs))

        ev.section("Q1 Expansion shape and consecutive reads")
        presets = vector(core.get_available_configs("Channel"))
        expansions: dict[str, Any] = {}
        all_rows: list[dict[str, Any]] = []
        for preset in presets:
            first = expand(core, "Channel", preset)
            second = expand(core, "Channel", preset)
            equal = first["settings"] == second["settings"]
            expansions[preset] = {"first": first, "second": second, "consecutive_equal": equal}
            all_rows.extend(first["settings"])
            ev.show(preset, expansions[preset])
        ev.data["sections"]["expansion"] = expansions

        ev.section("Q2/Q4/Q5 Replay equivalence, waits, and read-back fidelity")
        replay_results: dict[str, Any] = {}
        for preset in presets:
            rows = expansions[preset]["first"]["settings"]
            restore(core, pre)
            before = snapshot(core, pairs)
            set_start = time.perf_counter()
            core.set_config("Channel", preset)
            set_ms = (time.perf_counter() - set_start) * 1000.0
            devices = sorted({row["device"] for row in rows})
            busy_after_return = busy_state(core, devices)
            _, wait_config_ms = timed(lambda p=preset: core.wait_for_config("Channel", p))
            busy_after_wait = busy_state(core, devices)
            after_set = snapshot(core, pairs)
            state_set = config_state(core)
            restore(core, before)
            replay = apply_loop(core, rows)
            after_replay = snapshot(core, pairs)
            state_replay = config_state(core)
            result = {
                "set_config_ms": set_ms,
                "busy_immediately_after_set_config": busy_after_return,
                "wait_for_config_ms": wait_config_ms,
                "busy_after_wait_for_config": busy_after_wait,
                "replay": replay,
                "current_config_after_set_config": state_set,
                "current_config_after_replay": state_replay,
                "set_config_change": snapshot_diff(before, after_set),
                "replay_change": snapshot_diff(before, after_replay),
                "end_state_diff_set_config_vs_replay": snapshot_diff(after_set, after_replay),
                "configuration_state_equal": state_set == state_replay,
            }
            replay_results[preset] = result
            ev.show(preset, result)
        ev.data["sections"]["replay_equivalence"] = replay_results

        ev.section("Q3 Core.Shutter semantics")
        shutter = shutter_probe(core, all_rows)
        ev.data["sections"]["core_shutter"] = shutter
        ev.show("Core.Shutter", shutter)

        ev.section("Q6 Partial failure and reversibility")
        restore(core, pre)
        triplet, candidate_diagnostics = scratch_triplet(core, pairs)
        partial: dict[str, Any] = {
            "triplet": triplet,
            "candidate_diagnostics": candidate_diagnostics,
        }
        define_scratch(core, "bad", triplet)
        partial["expanded"] = expand(core, SCRATCH_GROUP, "bad")
        before_bad = snapshot(core, pairs)
        try:
            core.set_config(SCRATCH_GROUP, "bad")
            partial["set_config"] = {"raised": False}
        except Exception as exc:
            partial["set_config"] = {"raised": True, "error": clean_exception(exc)}
        after_bad = snapshot(core, pairs)
        partial["set_config"]["state_diff"] = snapshot_diff(before_bad, after_bad)
        partial["set_config"]["restore"] = restore(core, before_bad)
        partial["set_config"]["post_restore_diff"] = snapshot_diff(before_bad, snapshot(core, pairs))
        loop_rows = partial["expanded"]["settings"]
        before_loop = snapshot(core, pairs)
        try:
            partial["property_loop"] = {"raised": False, "result": apply_loop(core, loop_rows)}
        except Exception as exc:
            partial["property_loop"] = {"raised": True, "error": clean_exception(exc)}
        after_loop = snapshot(core, pairs)
        partial["property_loop"]["state_diff"] = snapshot_diff(before_loop, after_loop)
        partial["property_loop"]["restore"] = restore(core, before_loop)
        partial["property_loop"]["post_restore_diff"] = snapshot_diff(before_loop, snapshot(core, pairs))
        ev.data["sections"]["partial_failure"] = partial
        ev.show("Partial failure", partial)

        ev.section("Q7 TOCTOU re-read")
        before_edit = expand(core, SCRATCH_GROUP, "bad")
        first = triplet[0]
        core.define_config(SCRATCH_GROUP, "bad", first["device"], first["property"], first["before"])
        after_edit = expand(core, SCRATCH_GROUP, "bad")
        toctou = {
            "before": before_edit,
            "after": after_edit,
            "reread_changed": before_edit["settings"] != after_edit["settings"],
        }
        ev.data["sections"]["toctou"] = toctou
        ev.show("Definition re-read", toctou)
    except Exception as exc:
        exit_code = 1
        failure = {"type": type(exc).__name__, "message": clean_exception(exc), "traceback": traceback.format_exc()}
        ev.data["errors"].append(failure)
        ev.section("FATAL")
        ev.show("Failure", failure)
    finally:
        ev.section("Cleanup and residue verification")
        try:
            scratch_cleanup = delete_scratch(core)
        except Exception as exc:
            scratch_cleanup = {"error": clean_exception(exc)}
        ev.data["scratch_cleanup"] = scratch_cleanup
        ev.show("Scratch cleanup", scratch_cleanup)
        if pre is not None:
            final_restore = restore(core, pre)
            final_snapshot = snapshot(core, pairs)
            residue = snapshot_diff(pre, final_snapshot)
            ev.data["final_restore"] = final_restore
            ev.data["final_residue"] = residue
            ev.show("Restore attempts", final_restore)
            ev.show("RESIDUE (must be empty)", residue)
            if residue:
                exit_code = 1
                ev.data["errors"].append({"type": "RestoreResidue", "message": f"{len(residue)} properties differ"})
        if scratch_cleanup.get("still_present") is not False:
            exit_code = 1
        ev.data["exit_code"] = exit_code
        ev.write()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
