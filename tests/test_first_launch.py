import json
from pathlib import Path

import pytest
import yaml

from microclaw.config import validate_safety_config
from microclaw.first_launch import (
    SetupRefusal, disconnect_core, interview, load_inventory, write_profile,
)
from microclaw.rig_inventory import INVENTORY_SCHEMA


def _inventory():
    def prop(name, *, current="OBSERVED", allowed=None, technical=False):
        row = {
            "name": name, "current_value": current, "allowed_values": allowed or [],
            "read_only": False, "pre_init": False, "has_limits": technical,
            "reported_type": "Float",
        }
        if technical:
            row["technical_range"] = {"lower": 2440.0, "upper": 2450.0, "source": "driver_reported"}
        return row

    devices = [
        {"label": "Laser", "device_type": "GenericDevice", "properties": [
            prop("Emission", allowed=["OBSERVED_OFF", "OBSERVED_ON"]),
            prop("Power %", current="37", technical=True),
        ]},
        {"label": "TTL", "device_type": "GenericDevice", "properties": [prop("State0")]},
        {"label": "TIPFSStatus", "device_type": "AutoFocusDevice", "properties": [prop("State", allowed=["Off", "On"])]},
        {"label": "Camera", "device_type": "CameraDevice", "properties": [prop("ROI") ]},
        {"label": "XY", "device_type": "XYStageDevice", "properties": [prop("XPosition") ]},
        {"label": "Mystery", "device_type": "FutureDevice", "properties": [prop("Amplitude") ]},
    ]
    paths = [f'{d["label"]}.{p["name"]}' for d in devices for p in d["properties"]]
    return {
        "schema": INVENTORY_SCHEMA,
        "mm_config": None,
        "live_inventory_fingerprint": {"algorithm": "sha256", "value": "structural"},
        "facts": {
            "core_identity": {},
            "core_device_assignments": {
                "camera": "Camera", "focus": "Z", "xy_stage": "", "shutter": "",
                "autofocus": "TIPFSStatus", "galvo": "", "image_processor": "", "slm": "",
            },
            "devices": devices, "configuration_groups": [], "enumeration_failures": [],
        },
        "heuristic_candidates": {
            "suspected_continuous_actuators": [{
                "path": "Laser.Power %", "device": "Laser", "property": "Power %",
                "device_type": "GenericDevice", "rejected_non_emitting": False,
            }],
            "illumination_enable_properties": [{
                "path": "Laser.Emission", "device": "Laser", "property": "Emission",
                "device_type": "GenericDevice",
            }],
            "illumination_power_enable_groups": [],
            "possible_duplicate_power_representations": [],
            "unclassified_writable_properties": paths,
            "properties_with_unknown_writability": [],
        },
        "human_decisions": {"source": None, "comparison": None},
    }


def _answers():
    # Emission role/on/off; power role/units; TTL unresolved; core Z bounds;
    # nine explicit acquisition budgets; illumination cap and ratchet.
    return iter([
        "e", "OPERATOR_ON", "OPERATOR_OFF", "p", "p", "u",
        "0", "200", "500",
        "100", "60", "1000000", "5000", "10000", "50", "30", "500000", "1000",
        "25", "2",
    ])


def test_interview_copies_only_identifiers_and_explicit_answers(tmp_path):
    answers = _answers()
    output = []
    config, notes = interview(_inventory(), ask=lambda _: next(answers), say=output.append)
    assert config["reviewed"] is False
    assert config["rig_profile"]["categorical_properties"] == []
    assert config["illumination"]["shutters"][0]["on_value"] == "OPERATOR_ON"
    assert {x["property"] for x in config["rig_profile"]["excluded_properties"]} >= {
        "State0", "State", "ROI", "XPosition", "Amplitude",
    }
    rendered = yaml.safe_dump(config)
    assert "OBSERVED" not in rendered
    assert "2440" not in rendered and "2450" not in rendered
    assert any("PFS-offset workflows remain unsupported" in note for note in notes)
    assert any("ambiguous XY" in line for line in output)
    result = write_profile(config, notes, tmp_path / "profile.yaml")
    assert result.parsed is not None
    assert [(x.kind, x.blocking) for x in result.diagnostics] == [
        ("review", True), ("live_check", False),
    ]


def test_empty_guaranteed_categorical_key_is_emitted(tmp_path):
    answers = _answers()
    config, notes = interview(_inventory(), ask=lambda _: next(answers), say=lambda _: None)
    path = tmp_path / "profile.yaml"
    write_profile(config, notes, path)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded["rig_profile"]["mode"] == "guaranteed"
    assert loaded["rig_profile"]["categorical_properties"] == []
    assert loaded["reviewed"] is False


def test_inventory_regions_and_producer_version_fail_closed(tmp_path):
    inventory = _inventory()
    inventory["schema"] = "microclaw.rig-inventory/v999"
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")
    with pytest.raises(SetupRefusal, match="unsupported rig inventory schema"):
        load_inventory(path)
    inventory["schema"] = INVENTORY_SCHEMA
    del inventory["human_decisions"]
    path.write_text(json.dumps(inventory), encoding="utf-8")
    with pytest.raises(SetupRefusal, match="human_decisions"):
        load_inventory(path)


def test_unenumerable_effect_stops_before_any_question():
    inventory = _inventory()
    inventory["facts"]["enumeration_failures"] = [
        {"scope": "property:Laser.Emission", "field": "read_only", "error": "offline"}
    ]
    with pytest.raises(SetupRefusal, match="No profile was generated"):
        interview(inventory, ask=lambda _: pytest.fail("interview must not start"))


def test_blank_and_bad_numbers_refuse_in_phase5_wording():
    answers = iter(["", "e", "", "ON", "OFF", "p", "p", "u", "bad", "0", "200"] + ["1"] * 12)
    output = []
    interview(_inventory(), ask=lambda _: next(answers), say=output.append)
    assert any(line.startswith("SETUP REFUSAL:") for line in output)
    assert any("No driver-reported or example limit" in line for line in output)


def test_disconnect_releases_core_and_bridge(monkeypatch):
    events = []
    core = type("Core", (), {"_close": lambda self: events.append("core")})()
    bridge = type("Bridge", (), {"close": lambda self: events.append("bridge")})()
    ref = lambda: bridge
    from pyjavaz.bridge import Bridge
    monkeypatch.setitem(Bridge._cached_bridges_by_port, 9988, ref)
    disconnect_core(core, 9988)
    assert events == ["core", "bridge"]


def test_generated_profile_uses_shared_validator(monkeypatch, tmp_path):
    seen = []
    expected = validate_safety_config(tmp_path / "missing")
    monkeypatch.setattr("microclaw.first_launch.validate_safety_config", lambda path: seen.append(path) or expected)
    write_profile({"reviewed": True}, [], tmp_path / "draft.yaml")
    assert seen == [tmp_path / "draft.yaml"]
    assert yaml.safe_load((tmp_path / "draft.yaml").read_text())["reviewed"] is False
