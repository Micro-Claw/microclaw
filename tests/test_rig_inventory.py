import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from microclaw.rig_inventory import (
    compare_reviewed_config,
    enumerate_rig,
    write_inventory_outputs,
)


class ReadOnlyRecordingCore:
    """Records every bridge attribute access; unknown calls fail by default."""

    def __init__(self, *, fail_names=False, fail_attribute=False):
        self.accesses = []
        self.fail_names = fail_names
        self.fail_attribute = fail_attribute

    def __getattribute__(self, name):
        if name.startswith("_") or name in {"accesses", "fail_names", "fail_attribute"}:
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
            "get_device_library", "get_device_adapter_name", "get_device_description",
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
    def _is_property_read_only(self, device, prop): return prop == "Password"
    def _is_property_pre_init(self, device, prop): return False
    def _get_allowed_property_values(self, device, prop): return ["0", "1"] if prop in {"State", "Emission"} else []
    def _has_property_limits(self, device, prop): return prop in {"Power %", "Emission"}
    def _get_property_type(self, device, prop):
        if self.fail_attribute and prop == "Power %": raise RuntimeError("type unavailable")
        return "Float"
    def _get_property_lower_limit(self, device, prop): return 0
    def _get_property_upper_limit(self, device, prop): return 100 if prop == "Power %" else 1
    def _get_state_labels(self, device): return ["B", "A"]
    def _get_device_library(self, device): return "DemoCamera"
    def _get_device_adapter_name(self, device): return device
    def _get_device_description(self, device): return "Demo"
    def _get_available_config_groups(self): return ["Channel"]
    def _get_available_configs(self, group): return ["DAPI"]
    def _get_config_data(self, group, preset):
        return [{"device": "Wheel", "property": "State", "value": "1"}]
    def _get_version_info(self): return "MMCore 11"
    def _get_api_version_info(self): return "Device API 72"


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


def test_mm_config_path_is_outside_fingerprint(tmp_path):
    a, b = tmp_path / "a.cfg", tmp_path / "b.cfg"
    a.write_text("same", encoding="utf-8")
    b.write_text("same", encoding="utf-8")
    one = enumerate_rig(ReadOnlyRecordingCore(), mm_config=a)
    two = enumerate_rig(ReadOnlyRecordingCore(), mm_config=b)
    assert one["mm_config"]["sha256"] == hashlib.sha256(b"same").hexdigest()
    assert one["live_inventory_fingerprint"] == two["live_inventory_fingerprint"]


def test_outputs_are_confined_and_comparison_reports_both_directions(tmp_path):
    inventory = enumerate_rig(ReadOnlyRecordingCore())
    parsed = SimpleNamespace(
        rig_profile=SimpleNamespace(
            categorical_properties={("Missing", "State")}, excluded_properties=set()
        ),
        constraints=SimpleNamespace(
            forbidden_properties=[],
            illumination=SimpleNamespace(shutters=[], power_properties=[]),
        ),
    )
    compare_reviewed_config(inventory, parsed, "reviewed.yaml")
    comparison = inventory["human_decisions"]["comparison"]
    assert comparison["declared_paths_missing_from_live_rig"] == ["Missing.State"]
    assert "Laser.Power %" in comparison["live_paths_missing_from_reviewed_config"]
    out = tmp_path / "only-here"
    paths = write_inventory_outputs(inventory, out)
    assert {p.parent for p in paths} == {out}
    assert sorted(p.name for p in out.iterdir()) == ["inventory.json", "review.md"]
    assert not (tmp_path / "inventory.json").exists()
    assert "not safety decisions" in paths[1].read_text(encoding="utf-8")
