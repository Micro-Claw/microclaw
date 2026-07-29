import ast
import hashlib
import json
from pathlib import Path

import pytest

import microclaw.__main__ as cli
from microclaw.config import load_safety_config
from microclaw.rig_inventory import (
    compare_reviewed_config,
    enumerate_rig,
    write_inventory_outputs,
)


class FakePropertyType:
    """Bridge-shaped SWIG enum proxy, not a convenience string."""

    def __init__(self, name="Float", ordinal=2):
        self.name = name
        self.ordinal = ordinal

    def to_string(self): return self.name
    def swig_value(self): return self.ordinal


class ReadOnlyRecordingCore:
    """Records every bridge attribute access; unknown calls fail by default."""

    def __init__(self, *, fail_names=False, fail_attribute=False, fail_writability=False, fail_pre_init=False, error_text="type unavailable"):
        self.accesses = []
        self.fail_names = fail_names
        self.fail_attribute = fail_attribute
        self.fail_writability = fail_writability
        self.fail_pre_init = fail_pre_init
        self.error_text = error_text

    def __getattribute__(self, name):
        if name.startswith("_") or name in {"accesses", "fail_names", "fail_attribute", "fail_writability", "fail_pre_init", "error_text"}:
            return object.__getattribute__(self, name)
        accesses = object.__getattribute__(self, "accesses")
        accesses.append(name)
        approved = {
            "get_camera_device", "get_focus_device", "get_xy_stage_device",
            "get_shutter_device", "get_auto_focus_device", "get_galvo_device",
            "get_image_processor_device", "get_slm_device", "get_loaded_devices",
            "get_device_type", "get_device_property_names", "get_property",
            "is_property_read_only", "is_property_pre_init",
            "get_allowed_property_values", "has_property_limits", "get_property_type",
            "get_property_lower_limit", "get_property_upper_limit", "get_state_labels",
            "get_device_library", "get_device_name", "get_device_description",
            "get_available_config_groups", "get_available_configs", "get_config_data",
            "get_version_info", "get_api_version_info",
        }
        if name not in approved:
            raise AssertionError(f"unapproved bridge attribute access: {name}")
        return object.__getattribute__(self, f"_{name}")

    def _get_camera_device(self): return "Camera"
    def _get_focus_device(self): return ""
    def _get_xy_stage_device(self): return ""
    def _get_shutter_device(self): return "Shutter"
    def _get_auto_focus_device(self): return ""
    def _get_galvo_device(self): return ""
    def _get_image_processor_device(self): return ""
    def _get_slm_device(self): return ""
    def _get_loaded_devices(self): return ["Wheel", "Laser"]
    def _get_device_type(self, device): return 4 if device == "Wheel" else 3
    def _get_device_property_names(self, device):
        if self.fail_names and device == "Wheel": raise RuntimeError("driver offline")
        return ["State"] if device == "Wheel" else ["Password", "Power %", "Emission"]
    def _get_property(self, device, prop):
        return {"State": "1", "Password": "hunter2", "Power %": "20", "Emission": "0"}[prop]
    def _is_property_read_only(self, device, prop):
        if self.fail_writability and prop == "Power %": raise RuntimeError("writability unavailable")
        return prop == "Password"
    def _is_property_pre_init(self, device, prop):
        if self.fail_pre_init and prop == "Power %": raise RuntimeError("pre-init unavailable")
        return False
    def _get_allowed_property_values(self, device, prop): return ["0", "1"] if prop in {"State", "Emission"} else []
    def _has_property_limits(self, device, prop): return prop in {"Power %", "Emission"}
    def _get_property_type(self, device, prop):
        if self.fail_attribute and prop == "Power %": raise RuntimeError(self.error_text)
        return FakePropertyType()
    def _get_property_lower_limit(self, device, prop): return 0
    def _get_property_upper_limit(self, device, prop): return 100 if prop == "Power %" else 1
    def _get_state_labels(self, device): return ["B", "A"]
    def _get_device_library(self, device): return "DemoCamera"
    def _get_device_name(self, device): return device
    def _get_device_description(self, device): return "Demo"
    def _get_available_config_groups(self): return ["Channel"]
    def _get_available_configs(self, group): return ["DAPI"]
    def _get_config_data(self, group, preset):
        return [{"device": "Wheel", "property": "State", "value": "1"}]
    def _get_version_info(self): return "MMCore 11"
    def _get_api_version_info(self): return "Device API 72"


class FreshPropertyTypeCore(ReadOnlyRecordingCore):
    def _get_property_type(self, device, prop):
        return object()


class MissingAdapterNameCore(ReadOnlyRecordingCore):
    def __getattribute__(self, name):
        if name == "get_device_name":
            accesses = object.__getattribute__(self, "accesses")
            accesses.append(name)
            raise AttributeError("get_device_name")
        return super().__getattribute__(name)


def test_whole_enumeration_is_query_only_and_unknown_access_fails():
    core = ReadOnlyRecordingCore()
    inventory = enumerate_rig(core)
    assert core.accesses
    assert all(name.startswith(("get_", "is_", "has_")) for name in core.accesses)
    assert inventory["facts"]["devices"][1]["state_labels"] == ["A", "B"]
    try:
        core.set_property
    except AssertionError as exc:
        assert "unapproved" in str(exc)
    else:
        raise AssertionError("new mutation access was not rejected")


def test_partial_failures_continue_and_are_per_scope():
    inventory = enumerate_rig(ReadOnlyRecordingCore(fail_names=True, fail_attribute=True))
    devices = {d["label"]: d for d in inventory["facts"]["devices"]}
    assert devices["Wheel"]["properties"] == []
    power = next(p for p in devices["Laser"]["properties"] if p["name"] == "Power %")
    assert power["reported_type"] is None
    assert power["technical_range"] == {"lower": 0.0, "upper": 100.0, "source": "driver_reported"}
    assert len(inventory["facts"]["enumeration_failures"]) == 2


def test_deterministic_order_hash_and_credential_redaction(tmp_path):
    one = enumerate_rig(ReadOnlyRecordingCore())
    two = enumerate_rig(ReadOnlyRecordingCore())
    assert one == two
    assert one["live_inventory_fingerprint"] == two["live_inventory_fingerprint"]
    raw = json.dumps(one)
    assert "hunter2" not in raw
    assert "<redacted>" in raw
    assert one["mm_config"] is None


def test_new_nonprimitive_property_type_objects_do_not_change_fingerprint():
    one = enumerate_rig(FreshPropertyTypeCore())
    two = enumerate_rig(FreshPropertyTypeCore())
    assert one["live_inventory_fingerprint"] == two["live_inventory_fingerprint"]
    failures = one["facts"]["enumeration_failures"]
    assert any(
        item["field"] == "reported_type"
        and item["error"] == "non-primitive result type: object"
        for item in failures
    )
    assert all(
        prop["reported_type"] is None
        for device in one["facts"]["devices"]
        for prop in device["properties"]
    )


def test_missing_real_core_method_is_recorded_without_repr():
    inventory = enumerate_rig(MissingAdapterNameCore())
    for device in inventory["facts"]["devices"]:
        assert device["adapter"]["name"] is None
    failures = [
        item for item in inventory["facts"]["enumeration_failures"]
        if item["field"] == "adapter_name"
    ]
    assert len(failures) == len(inventory["facts"]["devices"])
    assert all(item["error"] == "get_device_name" for item in failures)
    assert "object at 0x" not in json.dumps(inventory)


def test_property_type_bridge_conversion_prefers_name_then_ordinal():
    class Named:
        def to_string(self): return "Float"
        def swig_value(self): raise AssertionError("name should win")
    class Ordinal:
        def swig_value(self): return 3
    named = ReadOnlyRecordingCore()
    named._get_property_type = lambda device, prop: Named()
    ordinal = ReadOnlyRecordingCore()
    ordinal._get_property_type = lambda device, prop: Ordinal()
    assert enumerate_rig(named)["facts"]["devices"][0]["properties"][0]["reported_type"] == "Float"
    assert enumerate_rig(ordinal)["facts"]["devices"][0]["properties"][0]["reported_type"] == "Integer"


def test_mm_config_path_is_outside_fingerprint(tmp_path):
    a, b = tmp_path / "a.cfg", tmp_path / "b.cfg"
    a.write_text("same", encoding="utf-8")
    b.write_text("same", encoding="utf-8")
    one = enumerate_rig(ReadOnlyRecordingCore(), mm_config=a)
    two = enumerate_rig(ReadOnlyRecordingCore(), mm_config=b)
    assert one["mm_config"]["sha256"] == hashlib.sha256(b"same").hexdigest()
    assert one["live_inventory_fingerprint"] == two["live_inventory_fingerprint"]


def _demo_config(tmp_path):
    source = Path("design/33-block5-demo-safety-config.yaml").read_text(encoding="utf-8")
    path = tmp_path / "demo-safety.yaml"
    path.write_text(source.replace("/REPLACE/with/a/real/directory", str(tmp_path)), encoding="utf-8")
    return load_safety_config(path)


def _demo_config_with_wheel_ruling(tmp_path):
    source = Path("design/33-block5-demo-safety-config.yaml").read_text(encoding="utf-8")
    source = source.replace(
        "categorical_properties: []",
        "categorical_properties:\n    - {device: Wheel, property: Label}",
    )
    path = tmp_path / "demo-safety-wheel-ruling.yaml"
    path.write_text(source.replace("/REPLACE/with/a/real/directory", str(tmp_path)), encoding="utf-8")
    return load_safety_config(path)


def test_outputs_are_confined_and_comparison_uses_real_reviewed_config(tmp_path):
    inventory = enumerate_rig(ReadOnlyRecordingCore())
    compare_reviewed_config(inventory, _demo_config(tmp_path), "reviewed.yaml", ReadOnlyRecordingCore())
    comparison = inventory["human_decisions"]["comparison"]
    assert "Laser.Power %" in comparison["live_paths_missing_from_reviewed_config"]
    assert "Laser.Password" not in comparison["live_paths_missing_from_reviewed_config"]
    assert "Wheel.State" not in comparison["live_paths_missing_from_reviewed_config"]
    out = tmp_path / "only-here"
    paths = write_inventory_outputs(inventory, out)
    assert {p.parent for p in paths} == {out}
    assert sorted(p.name for p in out.iterdir()) == ["inventory.json", "review.md"]
    assert not (tmp_path / "inventory.json").exists()
    assert "not safety decisions" in paths[1].read_text(encoding="utf-8")


def test_state_position_ruling_preserves_vacuum_filling_semantics(tmp_path):
    inventory = enumerate_rig(ReadOnlyRecordingCore())
    core = ReadOnlyRecordingCore()
    compare_reviewed_config(
        inventory, _demo_config_with_wheel_ruling(tmp_path), "reviewed.yaml", core
    )
    comparison = inventory["human_decisions"]["comparison"]
    assert "Wheel.Label" in comparison["declared_paths_missing_from_live_rig"]
    assert "Wheel.State" in comparison["live_paths_missing_from_reviewed_config"]


@pytest.mark.parametrize("failure", ["read_only", "pre_init"])
def test_unknown_writability_is_explicit_and_not_a_candidate(tmp_path, failure):
    inventory = enumerate_rig(ReadOnlyRecordingCore(
        fail_writability=failure == "read_only",
        fail_pre_init=failure == "pre_init",
    ))
    candidates = inventory["heuristic_candidates"]
    assert candidates["properties_with_unknown_writability"] == ["Laser.Power %"]
    assert "Laser.Power %" not in candidates["unclassified_writable_properties"]
    review = write_inventory_outputs(inventory, tmp_path)[1].read_text(encoding="utf-8")
    assert "## Properties with unknown writability" in review
    assert "Laser.Power %" in review


def test_driver_error_text_is_not_part_of_fingerprint():
    one = enumerate_rig(ReadOnlyRecordingCore(fail_attribute=True, error_text="handle 123"))
    two = enumerate_rig(ReadOnlyRecordingCore(fail_attribute=True, error_text="handle 987"))
    assert one["facts"]["enumeration_failures"] != two["facts"]["enumeration_failures"]
    assert one["live_inventory_fingerprint"] == two["live_inventory_fingerprint"]


def test_review_calls_out_failure_repeated_on_every_device(tmp_path):
    inventory = enumerate_rig(MissingAdapterNameCore())
    review = write_inventory_outputs(inventory, tmp_path)[1].read_text(encoding="utf-8")
    assert "## Systemic enumeration failures" in review
    assert "adapter_name: identical failure on every device (2/2): get_device_name" in review


def test_inspect_rig_bootstraps_without_safety_config(monkeypatch, tmp_path):
    core = ReadOnlyRecordingCore()
    controller = type("Controller", (), {"core": core, "is_connected": lambda self: True})()
    monkeypatch.setattr(cli, "MicroscopeController", lambda port: controller)
    args = type("Args", (), {"port": 4827, "safety_config": None, "mm_config": None, "out": tmp_path})()
    cli.inspect_rig(args)
    assert (tmp_path / "inventory.json").exists()


def test_inspect_rig_refuses_unreadable_mm_config(monkeypatch, tmp_path):
    core = ReadOnlyRecordingCore()
    controller = type("Controller", (), {"core": core, "is_connected": lambda self: True})()
    monkeypatch.setattr(cli, "MicroscopeController", lambda port: controller)
    args = type("Args", (), {"port": 4827, "safety_config": None, "mm_config": tmp_path / "missing.cfg", "out": tmp_path / "out"})()
    with pytest.raises(SystemExit, match="Could not read Micro-Manager config"):
        cli.inspect_rig(args)


def _calls_in_function(source, function_name):
    function = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
    }


def test_inventory_core_calls_are_pinned_to_javap_cmmcore_fixture():
    source = Path("microclaw/rig_inventory.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "core"
    }
    calls |= {
        node.args[1].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "core"
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    }
    # The assignment query names are intentionally data-driven.
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "enumerate_rig")
    calls |= {
        item.value
        for node in ast.walk(function)
        if isinstance(node, ast.Tuple)
        for pair in node.elts
        if isinstance(pair, ast.Tuple) and len(pair.elts) == 2
        for item in pair.elts[1:]
        if isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value.startswith("get_")
    }
    java_methods = set(Path("tests/fixtures/mmcorej-cmmcore-2.0.3-methods.txt").read_text().splitlines())
    normalize = lambda name: name.replace("_", "").lower()
    missing = {name for name in calls if normalize(name) not in {normalize(java) for java in java_methods}}
    assert not missing


def test_inspect_rig_ast_boundary_has_only_reviewed_construction_calls():
    source = Path("microclaw/__main__.py").read_text(encoding="utf-8")
    assert _calls_in_function(source, "inspect_rig") == {
        "Path", "MicroscopeController", "compare_reviewed_config", "enumerate_rig",
        "exit", "is_connected", "load_safety_config", "print", "str",
        "write_inventory_outputs",
    }


def test_inspect_rig_ast_tripwire_detects_a_new_runtime_surface():
    source = """
def inspect_rig(args):
    inventory = enumerate_rig(args.core)
    build_app(args)
    return inventory
"""
    approved = {"enumerate_rig"}
    assert _calls_in_function(source, "inspect_rig") != approved
