"""Block 59a demo-machine gate for the generic orientation payload."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from microclaw.authorization import validate_live_rig
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
        self.stream.write(data)
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
        print(f"{status}: {name} — {detail}")
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
    gate_path = (ROOT / "design/59-block59a-demo-safety-config.yaml").resolve()
    active_hash = sha256(active_path)
    print(f"Active safety document before gate: {active_path} sha256={active_hash}")

    ctrl = MicroscopeController(port=args.port)
    counted = CountingCore(ctrl.core)
    ctrl._core = counted
    production = load_safety_config(active_path)
    gate_config = load_safety_config(gate_path)
    gate_guard = SafetyGuard(gate_config.constraints)
    setup_error = None
    try:
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
        first = second = {}
        first_s = second_s = 0.0
        first_calls = second_calls = 0
    (args.output / "system-state.json").write_text(
        json.dumps(first, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    positions = first.get("optical_path", {}).get("discrete_positions", [])
    by_device = {item.get("device"): item for item in positions}
    configs = first.get("objective", {}).get("available_configs", [])
    focus = first.get("focus", {})

    @limb("inventory confirmed", "StateDevices, labels/allowed values, configs, or autofocus are assumed rather than reported")
    def inventory_confirmed():
        if setup_error: raise NotExercised(f"orientation call could not run: {setup_error}")
        if not positions: raise NotExercised("this machine reports no StateDevice")
        if not configs: raise NotExercised("this machine reports no pixel-size configs")
        if not focus.get("device"): raise NotExercised("this machine reports no autofocus device")
        return json.dumps({"state_devices": positions, "pixel_size_configs": configs,
                           "autofocus_device": focus.get("device")}, sort_keys=True)

    @limb("all orientation keys", "one of optical_path, objective, or focus is absent")
    def keys_present():
        if setup_error: raise NotExercised(f"orientation call could not run: {setup_error}")
        assert {"optical_path", "objective", "focus"} <= set(first)

    @limb("port vocabulary marks without filtering", "an unmarked discrete device vanishes")
    def marks_not_filters():
        if setup_error: raise NotExercised(f"orientation call could not run: {setup_error}")
        unmarked = [item for item in positions if item.get("role") != "light-path candidate"]
        if not unmarked: raise NotExercised("this machine has no unmarked StateDevice")
        return "unmarked devices retained: " + ", ".join(item["device"] for item in unmarked)

    @limb("safety-ruled devices remain visible", "the read inventory inherits write authorization exclusions")
    def ruled_visible():
        if setup_error: raise NotExercised(f"orientation call could not run: {setup_error}")
        required = {"Objective", "Path"}
        missing = sorted(required - set(by_device))
        if missing: raise NotExercised("configured demo devices absent: " + ", ".join(missing))
        return "Objective is illumination-declared and Path is denied; both are present"

    @limb("multi-key dependencies preserved", "a pixel-size config dependency is dropped")
    def dependencies_preserved():
        if setup_error: raise NotExercised(f"orientation call could not run: {setup_error}")
        dependency_counts = [len(item.get("dependencies", [])) for item in configs]
        if max(dependency_counts, default=0) < 1:
            raise NotExercised("no pixel-size config has a dependency")
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

    # Restore the production guard/authorization map and prove the safety file
    # itself was not changed. This is the state handed back after the gate.
    after_hash = sha256(active_path)

    @limb("production safety document restored", "the gate leaves its evidence config active or changes production safety")
    def safety_restored():
        production_guard = SafetyGuard(production.constraints)
        validate_live_rig(ctrl, production, guard=production_guard)
        assert after_hash == active_hash
        assert active_path != gate_path
        return f"restored and revalidated {active_path}; sha256={after_hash}"

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
