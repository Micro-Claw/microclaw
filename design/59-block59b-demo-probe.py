"""Block 59b demo-machine mechanical probe: one run, no agent."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("MICROCLAW_TREE_UNDER_TEST", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT))

# Reuse, do not fork, 59a's gate framework.
_spec = importlib.util.spec_from_file_location("block59a_gate", ROOT / "design/59-block59a-demo-gate.py")
_gate59a = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_gate59a)
NotExercised, Tee, limb, sha256 = (_gate59a.NotExercised, _gate59a.Tee,
                                  _gate59a.limb, _gate59a.sha256)
RESULTS = _gate59a.RESULTS

from microclaw.authorization import _build_state_device_inventory, _strings, validate_live_rig
from microclaw.calibration import _config_mismatches
from microclaw.config import load_safety_config
from microclaw.controller import MicroscopeController
from microclaw.knowledge_manager import KNOWLEDGE_PATH
from microclaw.paths import default_safety_config
from microclaw.safety import SafetyGuard
from microclaw.tools import get_system_state


class CountingCore(_gate59a.CountingCore):
    def __init__(self, core):
        super().__init__(core)
        self.by_method = {}

    def __getattr__(self, name):
        value = getattr(self._core, name)
        if not callable(value):
            return value
        def counted(*args, **kwargs):
            self.calls += 1
            self.by_method[name] = self.by_method.get(name, 0) + 1
            return value(*args, **kwargs)
        return counted


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
                    encoding="utf-8")


def main():
    RESULTS.clear()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--active-safety-config", type=Path, default=default_safety_config())
    parser.add_argument("--output", type=Path, default=Path("block59b-demo-evidence"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, args.output / "gate.txt")

    active = args.active_safety_config.resolve()
    template = (ROOT / "design/59-block59a-demo-safety-config.yaml").resolve()
    try:
        active_bytes, active_mtime = active.read_bytes(), active.stat().st_mtime_ns
        active_error = None
    except OSError as exc:
        active_bytes = active_mtime = None
        active_error = f"{type(exc).__name__}: {exc}"
    knowledge_existed = KNOWLEDGE_PATH.exists()
    knowledge_bytes = KNOWLEDGE_PATH.read_bytes() if knowledge_existed else None
    knowledge_mtime = KNOWLEDGE_PATH.stat().st_mtime_ns if knowledge_existed else None
    setup_error = None
    entry_labels, before_allowed, before_allowed_bytes, restored = {}, {}, b"", {}
    validation = first_cost = second_cost = (0, 0.0)
    first = {}
    routing = objective = None
    generated = args.output / "generated-safety-config.yaml"
    try:
        ctrl = MicroscopeController(port=args.port)
        counted = CountingCore(ctrl.core)
        ctrl._core = counted
        loaded = _strings(counted.get_loaded_devices())
        raw = _build_state_device_inventory(counted, loaded)
        configs = _config_mismatches(ctrl)
        dependency_devices = {rule["device"] for cfg in configs for rule in cfg.get("rules", [])}
        candidates = [x for x in raw["devices"] if isinstance(x.get("allowed"), list) and len(x["allowed"]) > 1]
        routing_candidates = [x for x in candidates if re.search(
            r"light\s*path", f"{x.get('adapter', '')} {x.get('adapter_description', '')}", re.I)]
        routing = routing_candidates[0] if routing_candidates else None
        objective = next((x for x in candidates if x["device"] in dependency_devices and
                          any(v not in {r.get("expected") for c in configs for r in c.get("rules", [])
                                       if r.get("device") == x["device"] and r.get("property") == "Label"}
                              for v in x["allowed"])), None)
        special_devices = {x["device"] for x in (routing, objective) if x is not None}
        non_special = [x for x in candidates if x["device"] not in special_devices]
        if len(non_special) < 2: raise NotExercised("fewer than two non-routing StateDevices for safety controls")
        illumination, ruled = non_special[:2]
        config = yaml.safe_load(template.read_text(encoding="utf-8"))
        config["property_authorization"]["denied"] = [{"device": ruled["device"], "property": "Label"}]
        config["illumination"]["shutters"] = [{
            "device": illumination["device"], "property": "Label",
            "on_value": str(illumination["allowed"][1]), "off_value": str(illumination["allowed"][0]),
        }]
        generated.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        parsed = load_safety_config(generated); guard = SafetyGuard(parsed.constraints)
        entry_labels = {x["device"]: str(counted.get_property(x["device"], "Label")) for x in raw["devices"]}
        before_allowed = {x["device"]: list(x["allowed"]) if isinstance(x["allowed"], list) else x["allowed"]
                          for x in raw["devices"]}
        before_allowed_bytes = json.dumps(
            before_allowed, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        before_calls = counted.calls; before_methods = dict(counted.by_method); started = time.perf_counter()
        validate_live_rig(ctrl, parsed, guard=guard)
        validation = counted.calls - before_calls, time.perf_counter() - started
        validation_adapter_reads = sum(counted.by_method.get(k, 0) - before_methods.get(k, 0)
                                       for k in ("get_device_name", "get_device_description"))
        before_calls = counted.calls; started = time.perf_counter()
        first = get_system_state(ctrl, guard)
        first_cost = counted.calls - before_calls, time.perf_counter() - started
        before_calls = counted.calls; started = time.perf_counter()
        second = get_system_state(ctrl, guard)
        second_cost = counted.calls - before_calls, time.perf_counter() - started
        discovery = {"inventory": raw, "routing_device": routing, "objective_device": objective,
                     "illumination_declared": illumination["device"], "explicitly_ruled": ruled["device"]}
        _write_json(args.output / "discovery.json", discovery)
        _write_json(args.output / "system-state-1.json", first)
        _write_json(args.output / "system-state-2.json", second)
    except Exception as exc:
        setup_error = f"{type(exc).__name__}: {exc}"
        (args.output / "setup-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raw, configs, second, validation_adapter_reads = {"devices": []}, [], {}, 0

    @limb("inventory confirmed", "inventory, routing adapter, objective dependency, or controls are assumed")
    def inventory_limb():
        if setup_error: raise NotExercised(setup_error)
        assert raw["devices"]
        if routing is None:
            raise NotExercised("inventory readable but no adapter-identified StateDevice light path")
        return json.dumps({"devices": [x["device"] for x in raw["devices"]],
                           "routing": routing["device"],
                           "objective": objective["device"] if objective else None})

    @limb("marking not filtering", "a StateDevice vanishes or a non-routing device is marked")
    def marking_limb():
        if setup_error: raise NotExercised(setup_error)
        positions = first["optical_path"]["discrete_positions"]
        assert {x["device"] for x in positions} == {x["device"] for x in raw["devices"]}
        route = next((x for x in positions if routing and x["device"] == routing["device"]), None)
        if route is not None:
            assert route["role"] == ["light-path candidate (adapter self-description)"]
            assert "positions_unnamed" in route
        assert all(not x["role"] for x in positions if (route is None or x["device"] != route["device"])
                   and not any(r.startswith("pixel-size-config dependency") for r in x["role"]))
        assert all(x["allowed"] == before_allowed[x["device"]] for x in positions)
        route_detail = f"routing={routing['device']} adapter-only" if route else "no routing device discovered"
        return f"{len(positions)} StateDevices retained; {route_detail}"

    @limb("objective unknown", "an unmatched objective state is reported as known/default or dependencies freeze")
    def objective_limb():
        if setup_error: raise NotExercised(setup_error)
        if objective is None: raise NotExercised("no objective dependency has an unmatched allowed label")
        expected = {r.get("expected") for c in configs for r in c.get("rules", [])
                    if r.get("device") == objective["device"] and r.get("property") == "Label"}
        target = next(v for v in objective["allowed"] if v not in expected)
        counted.set_property(objective["device"], "Label", target); counted.wait_for_device(objective["device"])
        moved = get_system_state(ctrl, guard); _write_json(args.output / "system-state-objective-unmatched.json", moved)
        obj = moved["objective"]
        rules = [r for c in obj["available_configs"] for r in c["dependencies"]
                 if r.get("device") == objective["device"] and r.get("property") == "Label"]
        assert obj["pixel_size_config"] is None and obj["pixel_size_um"] == 0.0
        assert rules and all(r["live"] == target and not r["matches"] for r in rules)
        assert "default" not in json.dumps(moved).lower()
        return f"{objective['device']} unmatched at {target!r}"

    @limb("identity control", "wrong identity matches, corrected identity misses, or knowledge restore fails")
    def identity_limb():
        if setup_error: raise NotExercised(setup_error)
        if routing is None: raise NotExercised("no adapter-identified StateDevice light path")
        route_live = next(x for x in get_system_state(ctrl, guard)["optical_path"]["discrete_positions"]
                          if x["device"] == routing["device"])
        condition = {"camera_adapter": first["camera"]["adapter"], "device": routing["device"],
                     "adapter": routing["adapter"], "allowed": routing["allowed"]}
        base = {"kind": "optical_path_position_map", "device": routing["device"],
                "positions": {routing["allowed"][0]: "operator test meaning"}}
        try:
            counted.set_property(routing["device"], "Label", routing["allowed"][1])
            counted.wait_for_device(routing["device"])
            wrong = {**base, "observed_on": {**condition, "adapter": condition["adapter"] + "-wrong"}}
            KNOWLEDGE_PATH.parent.mkdir(parents=True, exist_ok=True)
            KNOWLEDGE_PATH.write_text(yaml.safe_dump({"devices": {"gate-map": wrong}}), encoding="utf-8")
            assert "position_map" not in next(x for x in get_system_state(ctrl, guard)["optical_path"]["discrete_positions"]
                                              if x["device"] == routing["device"])
            correct = {**base, "observed_on": condition}
            KNOWLEDGE_PATH.write_text(yaml.safe_dump({"devices": {"gate-map": correct}}), encoding="utf-8")
            matched = next(x for x in get_system_state(ctrl, guard)["optical_path"]["discrete_positions"]
                           if x["device"] == routing["device"])
            assert matched["position_map"] == base["positions"]
            return f"wrong adapter rejected; corrected identity matched {routing['device']}"
        finally:
            if knowledge_existed:
                KNOWLEDGE_PATH.write_bytes(knowledge_bytes)
                os.utime(KNOWLEDGE_PATH, ns=(knowledge_mtime, knowledge_mtime))
                assert KNOWLEDGE_PATH.read_bytes() == knowledge_bytes
                assert KNOWLEDGE_PATH.stat().st_mtime_ns == knowledge_mtime
            elif KNOWLEDGE_PATH.exists():
                KNOWLEDGE_PATH.unlink()
            assert KNOWLEDGE_PATH.exists() == knowledge_existed

    @limb("three-phase cost", "validation adapter cost is hidden or second orientation is not cheaper")
    def cost_limb():
        if setup_error: raise NotExercised(setup_error)
        n = len(raw["devices"])
        assert validation_adapter_reads == 2 * n, (validation_adapter_reads, n)
        assert second_cost[0] < first_cost[0], (first_cost, second_cost)
        return (f"validation={validation[0]} calls/{validation[1]*1000:.1f}ms, adapter delta="
                f"{validation_adapter_reads}=2*{n}; first={first_cost[0]} calls/{first_cost[1]*1000:.1f}ms "
                f"(baseline 39/36ms); second={second_cost[0]} calls/{second_cost[1]*1000:.1f}ms "
                f"(baseline 27/8ms; off-rig 30->20)")

    # Always restore both moved devices, then read them and all allowed values back.
    try:
        if not setup_error:
            for item in (x for x in (objective, routing) if x is not None):
                counted.set_property(item["device"], "Label", entry_labels[item["device"]])
                counted.wait_for_device(item["device"])
            restored = {x["device"]: str(counted.get_property(x["device"], "Label")) for x in raw["devices"]}
            after_allowed = {x["device"]: list(_strings(counted.get_allowed_property_values(x["device"], "Label")))
                             for x in raw["devices"]}
            after_allowed_bytes = json.dumps(
                after_allowed, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
    except Exception as exc:
        restored = {"error": f"{type(exc).__name__}: {exc}"}; after_allowed = {}; after_allowed_bytes = b""

    @limb("restored and no durable writes", "labels/allowed values or production safety bytes/mtime changed")
    def restore_limb():
        if setup_error: raise NotExercised(setup_error)
        assert restored == entry_labels, (restored, entry_labels)
        assert after_allowed_bytes == before_allowed_bytes, (after_allowed, before_allowed)
        if active_error: raise NotExercised(f"production safety document unreadable: {active_error}")
        assert active.read_bytes() == active_bytes and active.stat().st_mtime_ns == active_mtime
        assert not list(args.output.glob("*.cfg"))
        return "moved labels read back; allowed bytes equivalent; safety bytes/mtime unchanged; no .cfg"

    summary = {"results": RESULTS, "cost": {"validation_calls": validation[0],
        "validation_wall_ms": validation[1]*1000, "validation_adapter_reads": validation_adapter_reads,
        "first_calls": first_cost[0], "first_wall_ms": first_cost[1]*1000,
        "second_calls": second_cost[0], "second_wall_ms": second_cost[1]*1000},
        "restored": restored}
    _write_json(args.output / "results.json", summary)
    failed = [x for x in RESULTS if x["status"] != "PASS"]
    print("BLOCK 59b DEMO PROBE " + ("FAILED" if failed else "PASSED"))
    return 1 if failed else 0


if __name__ == "__main__": raise SystemExit(main())
