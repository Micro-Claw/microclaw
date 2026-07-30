"""Block 14 Phase 4 measurement spike against a running Micro-Manager demo core.

This is deliberately a standalone rig probe, not executor implementation.  It
mutates the demo core, records what MM actually does, and restores a pre-run
snapshot.  Start MM's ZMQ server first and pass its port.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from pycromanager import Core


sys.stdout.reconfigure(line_buffering=True)
SCRATCH_GROUP = "microclaw_block14_phase4_scratch"
NUMERIC_SCRATCH_GROUP = "microclaw_block14_phase4_numeric_scratch"
BAD_VALUE = "__MICROCLAW_INTENTIONAL_BAD_VALUE__"


class NonPrimitiveBridgeResult(TypeError):
    pass


def primitive(value: Any) -> str | int | float | bool | None:
    """Admit only stable JSON leaves returned by the bridge."""
    if type(value) in (str, int, float, bool) or value is None:
        return value
    raise NonPrimitiveBridgeResult(
        f"non-primitive bridge result type: {type(value).__name__}"
    )


def primitive_bool(value: Any, call: str) -> bool:
    result = primitive(value)
    if type(result) is not bool:
        raise NonPrimitiveBridgeResult(
            f"{call} returned {type(result).__name__}, expected bool"
        )
    return result


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


def expand(
    core: Any, group: str, preset: str, surface: dict[str, Any]
) -> dict[str, Any]:
    config = core.get_config_data(group, preset)
    settings = config_settings(config)
    if "config" not in surface:
        surface["config"] = {
            "observed_at": {"group": group, "preset": preset},
            "type": type(config).__name__,
            "members": sorted(str(name) for name in dir(config)),
        }
    rows = []
    for index, setting in enumerate(settings):
        if "setting" not in surface:
            surface["setting"] = {
                "observed_at": {"group": group, "preset": preset, "index": index},
                "type": type(setting).__name__,
                "members": sorted(str(name) for name in dir(setting)),
            }
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
            "surface_ref": "sections.expansion_surface.setting",
        })
    return {
        "config_type": type(config).__name__,
        "config_surface_ref": "sections.expansion_surface.config",
        "settings": rows,
    }


class Evidence:
    def __init__(self, path: Path):
        self.path = path
        self.lines: list[str] = []
        self.data: dict[str, Any] = {
            "schema": "microclaw.block14.phase4.mm-apply-spike.v3",
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
    return primitive_bool(
        core.is_property_read_only(device, prop),
        f"is_property_read_only({device!r}, {prop!r})",
    )


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
        state["system_busy"] = primitive_bool(core.system_busy(), "system_busy()")
    except Exception as exc:
        if isinstance(exc, NonPrimitiveBridgeResult):
            raise
        state["system_busy_error"] = clean_exception(exc)
    state["devices"] = {}
    for device in devices:
        if device == "Core":
            continue
        try:
            state["devices"][device] = primitive_bool(
                core.device_busy(device), f"device_busy({device!r})"
            )
        except Exception as exc:
            if isinstance(exc, NonPrimitiveBridgeResult):
                raise
            state["devices"][device] = {"error": clean_exception(exc)}
    return state


def timed(fn: Callable[[], Any]) -> tuple[Any, float]:
    start = time.perf_counter()
    result = fn()
    return result, (time.perf_counter() - start) * 1000.0


def capability_check(core: Any) -> dict[str, Any]:
    """Confirm new bridge surfaces without invoking mutating methods."""
    result: dict[str, Any] = {"safe_calls": {}, "mutating_methods": {}}
    groups_raw = core.get_available_config_groups()
    groups = vector(groups_raw)
    result["safe_calls"]["get_available_config_groups"] = {
        "return_type": type(groups_raw).__name__,
        "value": groups,
        "item_types": sorted({type(item).__name__ for item in groups}),
    }
    current = primitive(core.get_current_config("Channel"))
    result["safe_calls"]["get_current_config"] = {
        "return_type": type(current).__name__, "value": current,
    }
    system = primitive_bool(core.system_busy(), "system_busy()")
    result["safe_calls"]["system_busy"] = {
        "return_type": type(system).__name__, "value": system,
    }
    devices = [item for item in vector(core.get_loaded_devices()) if item != "Core"]
    if not devices:
        raise RuntimeError("Q0 needs one non-Core device for device_busy")
    device = devices[0]
    device_result = primitive_bool(core.device_busy(device), f"device_busy({device!r})")
    result["safe_calls"]["device_busy"] = {
        "device": device,
        "return_type": type(device_result).__name__,
        "value": device_result,
    }
    for name in ("define_config", "delete_config", "delete_config_group"):
        member = getattr(core, name, None)
        if not callable(member):
            raise RuntimeError(f"Q0 bridge capability missing callable {name}")
        result["mutating_methods"][name] = {
            "present": True,
            "python_type": type(member).__name__,
            "invoked": False,
            "reason": "mutation deferred until after the pre-run snapshot",
        }
    return result


def round_trip_baseline(
    core: Any, pair: tuple[str, str], count: int = 20
) -> dict[str, Any]:
    device, prop = pair
    samples = []
    value_types = set()
    for _ in range(count):
        value, elapsed = timed(lambda: core.get_property(device, prop))
        primitive_value = primitive(value)
        value_types.add(type(primitive_value).__name__)
        samples.append(elapsed)
    return {
        "call": "get_property",
        "pair": [device, prop],
        "n": count,
        "return_types": sorted(value_types),
        "samples_ms": samples,
        "min_ms": min(samples),
        "median_ms": statistics.median(samples),
    }


def row_readbacks(rows: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for row in rows:
        key = json.dumps([row["device"], row["property"]], separators=(",", ":"))
        observed = state.get(key)
        item = {
            "index": row["index"], "device": row["device"],
            "property": row["property"], "requested": row["value"],
            "snapshot_result": observed,
        }
        if observed is not None and observed.get("ok"):
            item["read_back"] = observed["value"]
            item["exact"] = row["value"] == observed["value"]
        results.append(item)
    return results


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
            if is_read_only(core, device, prop) or primitive_bool(
                core.is_property_pre_init(device, prop),
                f"is_property_pre_init({device!r}, {prop!r})",
            ):
                continue
            current = str(core.get_property(device, prop))
            other, allowed_error = alternate(core, device, prop, current)
            if allowed_error is not None:
                diagnostics.append({"pair": [device, prop], "allowed_values_error": allowed_error})
            if other is not None:
                candidates.append({"device": device, "property": prop, "before": current, "test": other})
        except Exception as exc:
            if isinstance(exc, NonPrimitiveBridgeResult):
                raise
            diagnostics.append({"pair": [device, prop], "candidate_error": clean_exception(exc)})
    selected = []
    selected_devices = set()
    for candidate in candidates:
        if candidate["device"] not in selected_devices:
            selected.append(candidate)
            selected_devices.add(candidate["device"])
        if len(selected) == 3:
            break
    if len(selected) < 3:
        raise RuntimeError(
            "Scratch partial-failure test requires reversible writable enumerated "
            "properties on three distinct devices; found devices "
            f"{sorted(selected_devices)!r}"
        )
    first, bad, third = selected
    return [first, {**bad, "test": BAD_VALUE}, third], diagnostics


def delete_scratch(core: Any, group: str) -> dict[str, Any]:
    result: dict[str, Any] = {"attempted": True}
    try:
        core.delete_config_group(group)
        result["delete_call"] = "delete_config_group"
    except Exception as group_exc:
        result["delete_config_group_error"] = clean_exception(group_exc)
        try:
            for preset in vector(core.get_available_configs(group)):
                core.delete_config(group, preset)
            core.delete_config_group(group)
            result["delete_call"] = "delete_config then delete_config_group"
        except Exception as exc:
            result["delete_error"] = clean_exception(exc)
    try:
        result["still_present"] = group in vector(core.get_available_config_groups())
    except Exception as exc:
        result["verification_error"] = clean_exception(exc)
    return result


def define_scratch(core: Any, preset: str, triplet: list[dict[str, str]]) -> None:
    for row in triplet:
        core.define_config(SCRATCH_GROUP, preset, row["device"], row["property"], row["test"])


def shutter_names(core: Any, channel_rows: list[dict[str, Any]]) -> list[str]:
    names = set()
    active = str(core.get_shutter_device())
    if active:
        names.add(active)
    for row in channel_rows:
        if row["device"] == "Core" and row["property"] == "Shutter" and row["value"]:
            names.add(row["value"])
    return sorted(names)


def loaded_shutter_names(core: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """Identify loaded shutters solely from MM's reported device type."""
    names = []
    classifications = []
    for device in vector(core.get_loaded_devices()):
        try:
            device_type = str(core.get_device_type(device))
            is_shutter = device_type == "ShutterDevice"
            classifications.append({
                "device": device,
                "device_type": device_type,
                "is_shutter": is_shutter,
            })
            if is_shutter:
                names.append(device)
        except Exception as exc:
            classifications.append({
                "device": device,
                "device_type_error": clean_exception(exc),
                "is_shutter": False,
            })
    return sorted(names), classifications


def observe_shutters(core: Any, names: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        out["active_shutter"] = str(core.get_shutter_device())
    except Exception as exc:
        out["active_shutter_error"] = clean_exception(exc)
    try:
        out["auto_shutter"] = str(core.get_property("Core", "AutoShutter"))
    except Exception as exc:
        out["auto_shutter_error"] = clean_exception(exc)
    out["open"] = {}
    for name in names:
        out["open"][name] = primitive_bool(
            core.get_shutter_open(name), f"get_shutter_open({name!r})"
        )
    return out


def core_effect_probe(
    core: Any,
    channel_rows: list[dict[str, Any]],
    before: dict[str, Any],
    names: list[str],
) -> dict[str, Any]:
    distinct: dict[tuple[str, str], dict[str, Any]] = {}
    for row in channel_rows:
        if row["device"] == "Core":
            distinct.setdefault((row["property"], row["value"]), row)
    result: dict[str, Any] = {"effects": [], "shutter_names": names}
    for row in distinct.values():
        restore(core, before)
        item = {
            "index": row["index"],
            "property": row["property"],
            "requested": row["value"],
            "before_value": str(core.get_property("Core", row["property"])),
            "shutters_before": observe_shutters(core, names),
        }
        try:
            core.set_property("Core", row["property"], row["value"])
            item["permitted"] = True
            item["read_back"] = str(core.get_property("Core", row["property"]))
        except Exception as exc:
            item["permitted"] = False
            item["error"] = clean_exception(exc)
        item["shutters_after"] = observe_shutters(core, names)
        result["effects"].append(item)
    restore(core, before)
    return result


def shutter_retarget_probe(core: Any, before: dict[str, Any]) -> dict[str, Any]:
    names, classifications = loaded_shutter_names(core)
    original = str(core.get_shutter_device())
    result: dict[str, Any] = {
        "enumeration_method": "get_device_type(device) == 'ShutterDevice'",
        "shutter_devices": [
            {"device": name, "device_type": "ShutterDevice"} for name in names
        ],
        "loaded_device_classifications": classifications,
        "original_active_shutter": original,
        "targets": [],
    }
    if original not in names:
        raise RuntimeError(f"Active shutter {original!r} was not enumerated as a shutter")
    for target in names:
        if target == original:
            continue
        restore(core, before)
        item = {"target": target, "before": observe_shutters(core, names)}
        try:
            core.set_property("Core", "Shutter", target)
            item["permitted"] = True
        except Exception as exc:
            item["permitted"] = False
            item["error"] = clean_exception(exc)
        item["after"] = observe_shutters(core, names)
        item["previously_active_open_after"] = item["after"]["open"][original]
        result["targets"].append(item)
    result["restore"] = restore(core, before)
    result["restore_observation"] = observe_shutters(core, names)
    result["restore_verified"] = result["restore_observation"]["active_shutter"] == original
    if not result["restore_verified"]:
        raise RuntimeError("Failed to restore the original active shutter")
    return result


def numeric_readback_probe(
    core: Any, before: dict[str, Any], surface: dict[str, Any]
) -> dict[str, Any]:
    requested = "10"
    preset = "float"
    core.define_config(NUMERIC_SCRATCH_GROUP, preset, "Camera", "Exposure", requested)
    rows = expand(core, NUMERIC_SCRATCH_GROUP, preset, surface)["settings"]
    if len(rows) != 1:
        raise RuntimeError(f"Numeric scratch expansion has {len(rows)} settings, expected 1")
    restore(core, before)
    core.set_config(NUMERIC_SCRATCH_GROUP, preset)
    set_config_read_back = str(core.get_property("Camera", "Exposure"))
    restore(core, before)
    replay = apply_loop(core, rows)
    replay_read_back = str(core.get_property("Camera", "Exposure"))
    restore(core, before)
    return {
        "group": NUMERIC_SCRATCH_GROUP,
        "preset": preset,
        "effect": {"device": "Camera", "property": "Exposure", "requested": requested},
        "expanded": rows,
        "set_config": {
            "requested": requested,
            "read_back": set_config_read_back,
            "exact": requested == set_config_read_back,
        },
        "ordered_replay": {
            "requested": requested,
            "read_back": replay_read_back,
            "exact": requested == replay_read_back,
            "details": replay,
        },
    }


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
    scratch_may_exist = False
    numeric_scratch_may_exist = False
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
        ev.section("Q0 New bridge capability check (read-only)")
        capabilities = capability_check(core)
        ev.data["sections"]["capabilities"] = capabilities
        ev.show("Bridge capabilities", capabilities)
        existing_groups = capabilities["safe_calls"]["get_available_config_groups"]["value"]
        for scratch_group in (SCRATCH_GROUP, NUMERIC_SCRATCH_GROUP):
            if scratch_group in existing_groups:
                raise RuntimeError(f"HARD ABORT: scratch group {scratch_group!r} already exists")
        pairs = all_pairs(core)
        pre = snapshot(core, pairs)
        ev.data["pre_snapshot"] = pre
        ev.show("Property count", len(pairs))

        ev.section("Q0 Bridge round-trip timing baseline")
        readable_pair = next(
            (json.loads(key) for key, value in pre.items() if value.get("ok")),
            None,
        )
        if readable_pair is None:
            raise RuntimeError("No readable property available for round-trip baseline")
        baseline = round_trip_baseline(core, tuple(readable_pair))
        ev.data["sections"]["round_trip_baseline"] = baseline
        ev.show("20-call baseline", baseline)

        ev.section("Q1 Expansion shape and consecutive reads")
        expansion_surface: dict[str, Any] = {}
        presets = vector(core.get_available_configs("Channel"))
        expansions: dict[str, Any] = {}
        all_rows: list[dict[str, Any]] = []
        for preset in presets:
            first = expand(core, "Channel", preset, expansion_surface)
            second = expand(core, "Channel", preset, expansion_surface)
            equal = first["settings"] == second["settings"]
            expansions[preset] = {"first": first, "second": second, "consecutive_equal": equal}
            all_rows.extend(first["settings"])
            ev.show(preset, expansions[preset])
        ev.data["sections"]["expansion"] = expansions
        ev.data["sections"]["expansion_surface"] = expansion_surface
        ev.show("First observed expansion object surfaces", expansion_surface)

        ev.section("Q1 Read-only expansion of every config group")
        all_group_expansions: dict[str, Any] = {}
        for group in existing_groups:
            group_result: dict[str, Any] = {"presets": {}}
            try:
                group_presets = vector(core.get_available_configs(group))
                group_result["enumeration"] = {"ok": True, "names": group_presets}
            except Exception as exc:
                group_result["enumeration"] = {
                    "ok": False, "error": clean_exception(exc),
                }
                all_group_expansions[group] = group_result
                ev.show(group, group_result)
                continue
            for preset in group_presets:
                try:
                    group_result["presets"][preset] = {
                        "ok": True,
                        "expansion": expand(core, group, preset, expansion_surface),
                    }
                except Exception as exc:
                    group_result["presets"][preset] = {
                        "ok": False, "error": clean_exception(exc),
                    }
            all_group_expansions[group] = group_result
            ev.show(group, group_result)
        ev.data["sections"]["all_group_expansion"] = all_group_expansions

        ev.section("Q2/Q4/Q5 Replay equivalence, waits, and read-back fidelity")
        replay_results: dict[str, Any] = {}
        for preset in presets:
            rows = expansions[preset]["first"]["settings"]
            restore(core, pre)
            before = snapshot(core, pairs)
            names = shutter_names(core, all_rows)
            shutters_before_set = observe_shutters(core, names)
            set_start = time.perf_counter()
            core.set_config("Channel", preset)
            set_ms = (time.perf_counter() - set_start) * 1000.0
            shutters_after_set = observe_shutters(core, names)
            devices = sorted({row["device"] for row in rows})
            busy_after_return = busy_state(core, devices)
            _, wait_config_ms = timed(lambda p=preset: core.wait_for_config("Channel", p))
            busy_after_wait = busy_state(core, devices)
            shutters_after_wait = observe_shutters(core, names)
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
                "shutters_before_set_config": shutters_before_set,
                "shutters_immediately_after_set_config": shutters_after_set,
                "shutters_after_wait_for_config": shutters_after_wait,
                "set_config_readbacks": row_readbacks(rows, after_set),
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

        ev.section("Q3 All Core.* pseudo-device effects")
        restore(core, pre)
        names = shutter_names(core, all_rows)
        core_effects = core_effect_probe(core, all_rows, pre, names)
        ev.data["sections"]["core_effects"] = core_effects
        ev.show("Core effects", core_effects)

        ev.section("Q3b Core.Shutter retargeting without exposure or direct shutter writes")
        restore(core, pre)
        shutter_retarget = shutter_retarget_probe(core, pre)
        ev.data["sections"]["shutter_retarget"] = shutter_retarget
        ev.show("Shutter retarget", shutter_retarget)

        ev.section("Q5b Numeric read-back fidelity")
        restore(core, pre)
        numeric_scratch_may_exist = True
        numeric_readback = numeric_readback_probe(core, pre, expansion_surface)
        ev.data["sections"]["numeric_readback"] = numeric_readback
        ev.show("Numeric read-back", numeric_readback)

        ev.section("Q6 Partial failure and reversibility")
        restore(core, pre)
        triplet, candidate_diagnostics = scratch_triplet(core, pairs)
        partial: dict[str, Any] = {
            "triplet": triplet,
            "candidate_diagnostics": candidate_diagnostics,
        }
        scratch_may_exist = True
        define_scratch(core, "bad", triplet)
        partial["expanded"] = expand(core, SCRATCH_GROUP, "bad", expansion_surface)
        expanded_rows = partial["expanded"]["settings"]
        if (
            len(expanded_rows) != 3
            or len({row["device"] for row in expanded_rows}) != 3
            or expanded_rows[1]["value"] != BAD_VALUE
        ):
            raise RuntimeError(
                "Scratch expansion did not preserve the required three-device "
                "order with the intentional bad value at position 2: "
                + json.dumps(expanded_rows, sort_keys=True)
            )
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
        loop_rows = expanded_rows
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
        before_edit = expand(core, SCRATCH_GROUP, "bad", expansion_surface)
        first = triplet[0]
        core.define_config(SCRATCH_GROUP, "bad", first["device"], first["property"], first["before"])
        after_edit = expand(core, SCRATCH_GROUP, "bad", expansion_surface)
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
        if scratch_may_exist:
            try:
                scratch_cleanup = delete_scratch(core, SCRATCH_GROUP)
            except Exception as exc:
                scratch_cleanup = {"error": clean_exception(exc)}
        else:
            scratch_cleanup = {
                "attempted": False,
                "still_present": False,
                "reason": "probe did not begin scratch-group definition",
            }
        ev.data["scratch_cleanup"] = scratch_cleanup
        ev.show("Scratch cleanup", scratch_cleanup)
        if numeric_scratch_may_exist:
            try:
                numeric_scratch_cleanup = delete_scratch(core, NUMERIC_SCRATCH_GROUP)
            except Exception as exc:
                numeric_scratch_cleanup = {"error": clean_exception(exc)}
        else:
            numeric_scratch_cleanup = {
                "attempted": False,
                "still_present": False,
                "reason": "probe did not begin numeric scratch-group definition",
            }
        ev.data["numeric_scratch_cleanup"] = numeric_scratch_cleanup
        ev.show("Numeric scratch cleanup", numeric_scratch_cleanup)
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
        if numeric_scratch_cleanup.get("still_present") is not False:
            exit_code = 1
        ev.data["exit_code"] = exit_code
        ev.write()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
