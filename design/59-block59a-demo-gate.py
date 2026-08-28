"""Block 59a demo-machine gate for the generic orientation payload."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from microclaw.authorization import _build_state_device_inventory, _strings, validate_live_rig
from microclaw.calibration import _config_mismatches
from microclaw.config import load_safety_config
from microclaw.controller import MicroscopeController
from microclaw.paths import default_safety_config
from microclaw.safety import SafetyGuard
from microclaw.tools import get_system_state

RESULTS = []


class NotExercised(Exception):
    """This machine could not exercise the limb; never a pass."""


class Tee:
    def __init__(self, stream, path):
        self.stream = stream
        self.file = path.open("w", encoding="utf-8")

    def write(self, data):
        # Windows PowerShell 5.1 consoles commonly use a legacy code page.
        # Keep the evidence UTF-8 while making native-child stdout ASCII-safe.
        self.stream.write(data.encode("ascii", "backslashreplace").decode("ascii"))
        self.file.write(data)
        return len(data)

    def flush(self):
        self.stream.flush()
        self.file.flush()


def limb(name, fails_if):
    def decorate(fn):
        try:
            detail, status = fn() or "", "PASS"
        except NotExercised as exc:
            detail, status = str(exc), "NOT EXERCISED"
        except Exception as exc:
            detail, status = f"{type(exc).__name__}: {exc}", "FAIL"
            traceback.print_exc()
        RESULTS.append({"name": name, "status": status, "detail": detail,
                        "fails_if": fails_if})
        print(f"{status}: {name} - {detail}")
        return fn
    return decorate


class CountingCore:
    def __init__(self, core):
        self._core = core
        self.calls = 0

    def __getattr__(self, name):
        value = getattr(self._core, name)
        if not callable(value):
            return value

        def counted(*args, **kwargs):
            self.calls += 1
            return value(*args, **kwargs)
        return counted


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--active-safety-config", type=Path,
                        default=default_safety_config())
    parser.add_argument("--output", type=Path,
                        default=Path("block59a-demo-evidence"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, args.output / "gate.txt")

    active_path = args.active_safety_config.resolve()
    template_path = (ROOT / "design/59-block59a-demo-safety-config.yaml").resolve()
    # Read, never require. A machine that keeps its safety document elsewhere
    # still has every other limb to run; dying here would spend the trip on the
    # one limb that is about this gate rather than about the feature.
    try:
        active_bytes = active_path.read_bytes()
        active_mtime_ns = active_path.stat().st_mtime_ns
        active_error = None
        print(f"Active safety document (not installed or replaced): {active_path} sha256={sha256(active_path)}")
    except OSError as exc:
        active_bytes = active_mtime_ns = None
        active_error = f"{type(exc).__name__}: {exc}"
        print(f"Active safety document unreadable at {active_path}: {active_error}")

    setup_error = None
    generated_path = args.output / "generated-safety-config.yaml"
    try:
        # Inside the try: a Micro-Manager whose ZMQ server was never enabled is
        # the likeliest first-run failure on the rig, and it must arrive as a
        # named reason on every limb rather than as a traceback with no
        # results.json behind it.
        ctrl = MicroscopeController(port=args.port)
        counted = CountingCore(ctrl.core)
        ctrl._core = counted
        loaded = _strings(counted.get_loaded_devices())
        raw_inventory = _build_state_device_inventory(counted, loaded)
        discovered_config_names = _strings(counted.get_available_pixel_size_configs())
        discovered_configs = _config_mismatches(ctrl)
        autofocus_device = str(counted.get_auto_focus_device() or "")
        (args.output / "discovery.json").write_text(json.dumps({
            "state_device_inventory": raw_inventory,
            "pixel_size_config_names": discovered_config_names,
            "pixel_size_configs": discovered_configs,
            "autofocus_device": autofocus_device or None,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        candidates = [item for item in raw_inventory["devices"]
                      if isinstance(item.get("allowed"), list) and len(item["allowed"]) >= 2]
        if len(candidates) < 2:
            raise NotExercised("fewer than two StateDevices have two readable allowed labels")
        dependency_devices = {
            rule["device"] for config in discovered_configs for rule in config.get("rules", [])
            if "device" in rule
        }
        candidates.sort(key=lambda item: (item["device"] in dependency_devices, item["device"]))
        illumination_device, ruled_device = candidates[:2]
        template = yaml.safe_load(template_path.read_text(encoding="utf-8"))
        template["property_authorization"]["denied"] = [{
            "device": ruled_device["device"], "property": "Label",
        }]
        template["illumination"]["shutters"] = [{
            "device": illumination_device["device"], "property": "Label",
            "on_value": str(illumination_device["allowed"][1]),
            "off_value": str(illumination_device["allowed"][0]),
        }]
        generated_path.write_text(yaml.safe_dump(template, sort_keys=False), encoding="utf-8")
        gate_config = load_safety_config(generated_path)
        gate_guard = SafetyGuard(gate_config.constraints)
        validate_live_rig(ctrl, gate_config, guard=gate_guard)
        before = counted.calls
        started = time.perf_counter()
        first = get_system_state(ctrl, gate_guard)
        first_s = time.perf_counter() - started
        first_calls = counted.calls - before
        before = counted.calls
        started = time.perf_counter()
        second = get_system_state(ctrl, gate_guard)
        second_s = time.perf_counter() - started
        second_calls = counted.calls - before
    except Exception as exc:
        setup_error = f"{type(exc).__name__}: {exc}"
        # 58e: when a gate fails for a reason its own artifacts cannot explain,
        # the next trip is spent finding out why. Keep the whole traceback.
        (args.output / "setup-error.txt").write_text(
            traceback.format_exc(), encoding="utf-8"
        )
        first = second = {}
        first_s = second_s = 0.0
        first_calls = second_calls = 0
    (args.output / "system-state-1.json").write_text(
        json.dumps(first, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.output / "system-state-2.json").write_text(
        json.dumps(second, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    positions = first.get("optical_path", {}).get("discrete_positions", [])
    by_device = {item.get("device"): item for item in positions}
    configs = first.get("objective", {}).get("available_configs", [])
    focus = first.get("focus", {})

    @limb("inventory confirmed", "StateDevices, labels/allowed values, configs, or autofocus are assumed rather than reported")
    def inventory_confirmed():
        if setup_error: raise NotExercised(f"orientation call could not run: {setup_error}")
        if not positions: raise NotExercised("this machine reports no StateDevice")
        if not discovered_config_names:
            raise NotExercised("direct Core discovery reports no pixel-size configs")
        assert isinstance(configs, list), configs
        assert len(configs) == len(discovered_config_names)
        if not autofocus_device or not focus.get("device"):
            raise NotExercised("this machine reports no autofocus device")
        return json.dumps({"state_devices": positions, "pixel_size_configs": configs,
                           "autofocus_device": focus.get("device")}, sort_keys=True)

    @limb("all orientation keys", "one of optical_path, objective, or focus is absent")
    def keys_present():
        if setup_error: raise NotExercised(f"orientation call could not run: {setup_error}")
        assert {"optical_path", "objective", "focus"} <= set(first)
        # 59b reaches its reference tool from this hint and from nowhere else.
        assert isinstance(first["optical_path"], dict), first["optical_path"]
        assert first["optical_path"].get("hint", "").strip()

    @limb("pixel-config discovery agrees with payload", "bridge-vector conversion silently empties available_configs")
    def config_count_agrees():
        if setup_error: raise NotExercised(f"orientation call could not run: {setup_error}")
        if not discovered_config_names:
            raise NotExercised("direct discovery reports no pixel-size configurations")
        assert isinstance(configs, list), configs
        assert len(configs) == len(discovered_config_names), (
            len(configs), len(discovered_config_names)
        )
        return f"discovery={len(discovered_config_names)} payload={len(configs)}"

    @limb("port vocabulary marks without filtering", "an unmarked discrete device vanishes")
    def marks_not_filters():
        if setup_error: raise NotExercised(f"orientation call could not run: {setup_error}")
        unmarked = [item for item in positions if "light-path candidate" not in item.get("role", [])]
        if not unmarked: raise NotExercised("this machine has no unmarked StateDevice")
        return "unmarked devices retained: " + ", ".join(item["device"] for item in unmarked)

    @limb("safety-ruled devices remain visible", "the read inventory inherits write authorization exclusions")
    def ruled_visible():
        if setup_error: raise NotExercised(f"orientation call could not run: {setup_error}")
        required = {illumination_device["device"], ruled_device["device"]}
        missing = sorted(required - set(by_device))
        if missing: raise NotExercised("configured demo devices absent: " + ", ".join(missing))
        return (f"{illumination_device['device']} is illumination-declared and "
                f"{ruled_device['device']} is denied; both are present")

    @limb("multi-key dependencies preserved", "a pixel-size config dependency is dropped")
    def dependencies_preserved():
        if setup_error: raise NotExercised(f"orientation call could not run: {setup_error}")
        if not discovered_config_names:
            raise NotExercised("direct discovery reports no pixel-size configurations")
        assert isinstance(configs, list), configs
        assert len(configs) == len(discovered_config_names), (
            len(configs), len(discovered_config_names)
        )
        dependency_counts = [len(item.get("dependencies", [])) for item in configs]
        if max(dependency_counts, default=0) < 1:
            raise AssertionError(
                f"{len(configs)} pixel-size configurations exist but none reports a dependency"
            )
        assert all("measured objective" not in str(item).lower() for item in configs)
        return f"dependency counts per config: {dependency_counts}"

    @limb("retained-cache cost", "the second call is not cheaper or the first exceeds 1.5 s")
    def retained_cost():
        if setup_error: raise NotExercised(f"orientation call could not run: {setup_error}")
        assert second_calls < first_calls, (first_calls, second_calls)
        assert first_s <= 1.5, first_s
        return (f"first={first_calls} bridge calls/{first_s:.3f}s; "
                f"second={second_calls} bridge calls/{second_s:.3f}s")

    @limb("payload stable in shape", "the cached second call loses orientation structure")
    def stable_shape():
        if setup_error: raise NotExercised(f"orientation call could not run: {setup_error}")
        assert set(first) == set(second)
        assert len(positions) == len(second["optical_path"]["discrete_positions"])

    @limb("unmatched objective diagnosis follows live motion", "a moved dependency stays frozen or is promoted to a measured objective")
    def unmatched_objective():
        if setup_error: raise NotExercised(f"orientation call could not run: {setup_error}")
        dependency = None
        for config in configs:
            for rule in config.get("dependencies", []):
                device = rule.get("device")
                item = by_device.get(device)
                if rule.get("property") != "Label" or not item:
                    continue
                if item.get("label") == "unknown":
                    continue
                expected = {
                    candidate.get("expected")
                    for available in configs for candidate in available.get("dependencies", [])
                    if candidate.get("device") == device and candidate.get("property") == "Label"
                }
                target = next((value for value in item.get("allowed", []) if value not in expected), None)
                if target is not None:
                    dependency = device, item["label"], str(target)
                    break
            if dependency: break
        if dependency is None:
            raise NotExercised("no StateDevice Label dependency has an allowed non-matching value")
        device, entry, target = dependency
        try:
            counted.set_property(device, "Label", target)
            counted.wait_for_device(device)
            moved = get_system_state(ctrl, gate_guard)
            moved_item = next(item for item in moved["optical_path"]["discrete_positions"]
                              if item["device"] == device)
            moved_rules = [rule for config in moved["objective"]["available_configs"]
                           for rule in config["dependencies"]
                           if rule.get("device") == device and rule.get("property") == "Label"]
            assert moved_item["label"] == target
            assert moved_rules and all(rule["live"] == target and not rule["matches"]
                                       for rule in moved_rules)
            assert moved["objective"]["pixel_size_config"] is None
            assert "does not know which objective" in moved["objective"]["reason"]
            (args.output / "system-state-nonmatching.json").write_text(
                json.dumps(moved, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            return f"moved {device}.Label from {entry!r} to unmatched {target!r}"
        finally:
            counted.set_property(device, "Label", entry)
            counted.wait_for_device(device)

    @limb("Core shutter exclusion is named", "the Core shutter appears among discrete devices or is silently omitted")
    def shutter_excluded():
        if setup_error: raise NotExercised(f"orientation call could not run: {setup_error}")
        shutter = str(counted.get_shutter_device() or "")
        if not shutter: raise NotExercised("this machine has no Core shutter")
        assert shutter not in by_device
        assert first["optical_path"].get("shutter_exclusion", {}).get("device") == shutter
        return f"excluded Core shutter {shutter}"

    @limb("production safety document untouched", "the gate changes production safety bytes/mtime or writes generated safety outside evidence")
    def safety_untouched():
        if active_error:
            raise NotExercised(
                f"no readable production safety document at {active_path}: {active_error}"
            )
        assert active_path.read_bytes() == active_bytes
        assert active_path.stat().st_mtime_ns == active_mtime_ns
        assert generated_path.parent.resolve() == args.output.resolve()
        return f"bytes and mtime unchanged for {active_path}; generated config stayed in evidence"

    summary = {"results": RESULTS, "cost": {
        "first_bridge_calls": first_calls, "first_wall_s": first_s,
        "second_bridge_calls": second_calls, "second_wall_s": second_s,
    }}
    (args.output / "results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    failed = [item for item in RESULTS if item["status"] != "PASS"]
    print("BLOCK 59a DEMO GATE " + ("FAILED" if failed else "PASSED"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
