import json
import hashlib
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from microclaw import __main__ as cli
from microclaw.config import (
    ConfigDiagnostic, ConfigValidationResult, validate_safety_config,
)
from microclaw.first_launch import (
    CONTACT_ACKNOWLEDGEMENT, InterviewTranscript, SetupRefusal, disconnect_core,
    _bounded_numeric_unit, _emission_role_default, _metadata_default, _on_off_proposal,
    _power_units_default, _proposed_text, interview, load_inventory, write_profile,
)
from microclaw.rig_inventory import INVENTORY_SCHEMA
from microclaw.rig_inventory import _camera_geometry

REAL_DEMO_INVENTORY = Path(__file__).parent / "fixtures" / "block4_demo_inventory_20260801.json"


def _inventory():
    def prop(
        name, *, current="OBSERVED", allowed=None, technical=False,
        read_only=False, pre_init=False,
    ):
        row = {
            "name": name, "current_value": current, "allowed_values": allowed or [],
            "read_only": read_only, "pre_init": pre_init, "has_limits": technical,
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
        {"label": "Camera", "device_type": "CameraDevice", "properties": [
            prop("ROI"), prop("Binning", allowed=["1", "2", "4", "8"]),
            prop("Exposure", technical=True), prop("Gain", technical=True),
            prop("Serial", read_only=True),
        ]},
        {"label": "XY", "device_type": "XYStageDevice", "properties": [prop("XPosition") ]},
        {"label": "Mystery", "device_type": "FutureDevice", "properties": [prop("Amplitude") ]},
    ]
    paths = [
        f'{d["label"]}.{p["name"]}' for d in devices for p in d["properties"]
        if p["read_only"] is False and p["pre_init"] is False
    ]
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
    # Bulk defaults/no revisits; bounded gain unit/default bounds; emission
    # role/on/off; power role/units; TTL
    # unresolved; core Z bounds;
    # grouped acquisition budgets (derived fields accept proposals); illumination cap and ratchet.
    return iter([
        "", "", "e-/ADU", "", "", "e", "OPERATOR_ON", "OPERATOR_OFF", "p", "p",
        "0", "200", "",
        "500", "100", "60", "50", "", "", "1000000",
        "25", "2",
    ])


def _args(tmp_path, **overrides):
    values = {
        "out": tmp_path / "profile.yaml", "force": False,
        "inventory": None, "mm_config": None, "evidence_out": tmp_path / "evidence",
        "port": 4827,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _answer_real_interview(prompts, *, bulk=True):
    def ask(prompt):
        prompts.append(prompt)
        if "Accept all MM-derived" in prompt:
            return "" if bulk else "n"
        if "names to revisit" in prompt:
            return ""
        if "unit string" in prompt:
            return "" if "proposal:" in prompt else "e-/ADU"
        if prompt.startswith("Illumination candidate"):
            return "e"
        if "ON value" in prompt:
            return "ON"
        if "OFF value" in prompt:
            return "OFF"
        if "minimum" in prompt:
            return "0"
        if "maximum" in prompt:
            return "100"
        if prompt.startswith("Writable property") or prompt.startswith("Preset "):
            return ""
        return "1"
    return ask


def test_interview_copies_only_identifiers_and_explicit_answers(tmp_path):
    answers = _answers()
    output = []
    config, notes = interview(_inventory(), ask=lambda _: next(answers), say=output.append)
    assert config["reviewed"] is False
    assert config["rig_profile"]["categorical_properties"] == [
        {"device": "Camera", "property": "Binning"},
    ]
    assert config["rig_profile"]["typed_actuators"] == [{
        "device": "Camera", "property": "Gain", "kind": "bounded-numeric",
        "units": "e-/ADU", "minimum": 2440.0, "maximum": 2450.0,
    }]
    assert config["illumination"]["shutters"][0]["on_value"] == "OPERATOR_ON"
    assert {x["property"] for x in config["rig_profile"]["excluded_properties"]} >= {
        "State0", "State", "ROI", "XPosition", "Amplitude",
    }
    rendered = yaml.safe_dump(config)
    assert "OBSERVED" not in rendered
    assert config["camera"]["max_exposure_ms"] == 2450
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
    assert loaded["rig_profile"]["categorical_properties"] == [
        {"device": "Camera", "property": "Binning"},
    ]
    assert loaded["reviewed"] is False


def test_absent_channel_group_emits_explicit_empty_allowlist_and_review_note(tmp_path):
    answers = _answers()
    output = []
    config, notes = interview(_inventory(), ask=lambda _: next(answers), say=output.append)
    assert config["channels"] == {"allowed": []}
    assert "allowed" in config["channels"]  # omitted means all live presets are allowed
    assert any("has no 'Channel' configuration group" in line for line in output)
    assert any("omitting the key would authorize every live Channel preset" in note for note in notes)


def test_only_channel_group_presets_are_allowed_and_other_groups_are_not_silent():
    inventory = _inventory()
    inventory["facts"]["configuration_groups"] = [
        {"name": "Auxiliary", "presets": [{"name": "Shared", "effects": []}]},
        {"name": "Channel", "presets": [{"name": "Shared", "effects": []}]},
        {"name": "Secondary", "presets": [{"name": "Shared", "effects": []}]},
    ]
    answers = _answers()
    output = []
    config, notes = interview(inventory, ask=lambda _: next(answers), say=output.append)
    assert config["channels"] == {"allowed": ["Shared"]}
    assert any("configuration group 'Auxiliary'" in note for note in notes)
    assert any("configuration group 'Secondary'" in note for note in notes)
    assert sum("Channel.Shared [preset allowed" in line for line in output) == 1


def test_empty_channel_group_explains_empty_allowlist():
    inventory = _inventory()
    inventory["facts"]["configuration_groups"] = [{"name": "Channel", "presets": []}]
    answers = _answers()
    output = []
    config, notes = interview(inventory, ask=lambda _: next(answers), say=output.append)
    assert config["channels"] == {"allowed": []}
    assert any("'Channel' configuration group is empty" in line for line in output)
    assert any("authorize no channel presets" in note for note in notes)


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


@pytest.mark.parametrize("scope,field", [
    ("core", "loaded_devices"),
    ("device:Laser", "device_type"),
    ("device:Laser", "property_names"),
    ("property:Laser.Emission", "read_only"),
    ("property:Laser.Emission", "pre_init"),
    ("property:Laser.Emission", "allowed_values"),
    ("property:Laser.Power %", "has_limits"),
    ("property:Laser.Power %", "reported_type"),
    ("config_group:Channel", "presets"),
    ("preset:Channel.DAPI", "setting_0_property"),
])
def test_classification_failure_stops_before_any_question(scope, field):
    inventory = _inventory()
    inventory["facts"]["enumeration_failures"] = [
        {"scope": scope, "field": field, "error": "offline"}
    ]
    with pytest.raises(SetupRefusal, match="No profile was generated"):
        interview(inventory, ask=lambda _: pytest.fail("interview must not start"))


def test_observational_failures_become_header_review_notes(tmp_path):
    inventory = _inventory()
    inventory["facts"]["enumeration_failures"] = [
        {"scope": "property:Laser.Power %", "field": "current_value", "error": "offline"},
        {"scope": "property:Laser.Power %", "field": "lower_limit", "error": "offline"},
        {"scope": "device:Laser", "field": "adapter_name", "error": "offline"},
        {"scope": "core_assignments", "field": "galvo", "error": "offline"},
    ]
    answers = _answers()
    config, notes = interview(inventory, ask=lambda _: next(answers), say=lambda _: None)
    assert len([note for note in notes if note.startswith("ENUMERATION REVIEW NOTE:")]) == 4
    path = tmp_path / "profile.yaml"
    write_profile(config, notes, path)
    text = path.read_text(encoding="utf-8")
    for coordinate in (
        "property:Laser.Power % / current_value",
        "property:Laser.Power % / lower_limit",
        "device:Laser / adapter_name",
        "core_assignments / galvo",
    ):
        assert coordinate in text


def test_blank_and_bad_numbers_refuse_in_phase5_wording():
    answers_list = list(_answers())
    answers_list[10:10] = ["", "bad"]
    answers = iter(answers_list)
    output = []
    interview(_inventory(), ask=lambda _: next(answers), say=output.append)
    assert any(line.startswith("SETUP REFUSAL:") for line in output)
    assert any("No driver-reported or example limit" in line for line in output)


def test_every_hazardous_field_still_refuses_blank():
    inventory = _inventory()
    inventory["facts"]["core_device_assignments"]["xy_stage"] = "XY"
    seen = set()
    prompts = []
    output = []

    def valid(prompt):
        if prompt.startswith("Illumination candidate Laser.Emission"):
            return "e"
        if prompt.startswith("Illumination candidate Laser.Power"):
            return "p"
        if "Representation units" in prompt:
            return "p"
        if "ON value" in prompt:
            return "ON"
        if "OFF value" in prompt:
            return "OFF"
        if "minimum" in prompt:
            return "0"
        if "maximum" in prompt:
            return "100"
        return "1"

    def ask(prompt):
        prompts.append(prompt)
        if "Accept all MM-derived" in prompt or "names to revisit" in prompt:
            return ""
        no_default = "there is no default" in prompt or "required; no default" in prompt
        if no_default and prompt not in seen:
            seen.add(prompt)
            return ""
        return valid(prompt)

    interview(inventory, ask=ask, say=output.append)
    required = [
        "Illumination candidate Laser.Emission", "Illumination candidate Laser.Power",
        "ON value", "OFF value", "XY stage XY x travel", "XY stage XY y travel",
        "focus stage Z z travel", "maximum camera exposure", "hard maximum frames",
        "hard maximum acquisition duration", "hard maximum raw payload bytes",
        "total shutter-open time in one acquisition",
        "human-confirmation threshold frames", "human-confirmation threshold duration",
        "human-confirmation threshold total shutter-open time",
        "maximum illumination power", "maximum consecutive power step factor",
    ]
    # Fields that now carry an Enter-acceptable proposal. A blank is an accepted
    # answer there, so the prompt appears once and produces no refusal; every
    # other field must re-ask, which means it appears at least twice.
    defaulted = {
        "maximum camera exposure", "total shutter-open time in one acquisition",
        # Defaulted deliberately after the 2026-08-01 demo run: a blank-refusing
        # dose question the operator could not interpret was answered 5e15,
        # disabling the only confirmation measuring light.
        "human-confirmation threshold total shutter-open time",
    }
    for fragment in required:
        assert sum(fragment in prompt for prompt in prompts) >= (
            1 if fragment in defaulted else 2
        ), fragment
    # Assert the intent rather than a coincidence: every field still expected to
    # refuse a blank produced a refusal.
    assert len([line for line in output if line.startswith("SETUP REFUSAL:")]) >= (
        len(required) - len(defaulted)
    )


@pytest.mark.parametrize("prop", [
    "Camera", "Focus", "XYStage", "Shutter", "AutoFocus", "Galvo",
    "ImageProcessor", "SLM", "ChannelGroup", "Initialize",
])
def test_core_device_assignments_are_never_writable(prop):
    """M5 offers four devices for Core.Focus; a write re-aims the reviewed z bounds."""
    item = {
        "device": "Core", "property": prop, "device_type": "CoreDevice",
        "state_labels": [],
        "record": {
            "name": prop, "current_value": "", "allowed_values": ["", "PIZStage"],
            "read_only": False, "pre_init": False, "has_limits": False,
            "reported_type": "String",
        },
    }
    role, evidence, bounds = _metadata_default(item)
    assert role == "x"
    assert "device-assignment" in evidence
    assert bounds is None


def test_core_autoshutter_is_not_swept_up_as_structural():
    """It is a real illumination control and keeps its own classification path."""
    item = {
        "device": "Core", "property": "AutoShutter", "device_type": "CoreDevice",
        "state_labels": [],
        "record": {
            "name": "AutoShutter", "current_value": "1", "allowed_values": ["0", "1"],
            "read_only": False, "pre_init": False, "has_limits": False,
            "reported_type": "Integer",
        },
    }
    assert _metadata_default(item)[0] == "c"


def _with_state_device(label, *, illuminating=False):
    """An inventory carrying one StateDevice whose State reports no allowed values."""
    inventory = _inventory()
    device = {
        "label": label, "device_type": "StateDevice",
        "state_labels": ["Filter-1", "Filter-2", "Filter-3"],
        "properties": [
            {"name": "State", "current_value": "0", "allowed_values": [],
             "read_only": False, "pre_init": False, "has_limits": False,
             "reported_type": "Integer"},
        ],
    }
    if illuminating:
        device["properties"].append({
            "name": "Laser 1: 2. Emission", "current_value": "0", "allowed_values": [],
            "read_only": False, "pre_init": False, "has_limits": True,
            "reported_type": "Integer",
            "technical_range": {"lower": 0.0, "upper": 1.0, "source": "driver_reported"},
        })
        inventory["heuristic_candidates"]["illumination_enable_properties"].append({
            "path": f"{label}.Laser 1: 2. Emission", "device": label,
            "property": "Laser 1: 2. Emission", "device_type": "StateDevice",
        })
    inventory["facts"]["devices"].append(device)
    inventory["heuristic_candidates"]["unclassified_writable_properties"].extend(
        f'{label}.{p["name"]}' for p in device["properties"]
    )
    return inventory


def test_state_device_position_is_categorical_from_its_state_labels():
    """M5's filter wheels: MM publishes allowed values for Label, never for State."""
    output = []
    answers = iter(["", ""] + list(_answers())[2:])
    config, _ = interview(
        _with_state_device("Thorlabs Filter Wheel"),
        ask=lambda _: next(answers), say=output.append,
    )
    assert {"device": "Thorlabs Filter Wheel", "property": "State"} in (
        config["rig_profile"]["categorical_properties"]
    )
    assert {"device": "Thorlabs Filter Wheel", "property": "State"} not in (
        config["rig_profile"]["excluded_properties"]
    )


def test_state_device_position_on_an_illuminating_device_fails_closed_silently():
    """M5's laser engine: placeholder labels answer nothing, so do not ask."""
    prompts, output = [], []

    def ask(prompt):
        prompts.append(prompt)
        if "Accept all MM-derived" in prompt or "names to revisit" in prompt:
            return ""
        if prompt.startswith("Illumination candidate"):
            return "x"
        if "minimum" in prompt:
            return "0"
        if "no default" in prompt or "there is no default" in prompt:
            return "100"
        return ""

    config, notes = interview(
        _with_state_device("iChrome-MLE-TCP", illuminating=True), ask=ask, say=output.append,
    )
    assert not [p for p in prompts if "iChrome-MLE-TCP.State" in p], "must not ask"
    assert {"device": "iChrome-MLE-TCP", "property": "State"} in (
        config["rig_profile"]["excluded_properties"]
    )
    assert any("ILLUMINATING-DEVICE POSITION EXCLUDED" in note for note in notes)
    assert any("Revisit it by exact name" in line for line in output)


def test_camera_gain_survives_its_own_shutter_being_an_illumination_candidate():
    """M2's shape: an Andor that surfaces its own shutters and still needs Gain.

    The illuminating-device rule exists for a laser engine whose numerics
    redefine the declared emission envelope. A camera surfaces illumination
    candidates too — its internal and external shutters — but its numerics
    cannot gate light at the sample. Applying the rule there excluded
    `Andor.Gain` on M2, which is the entire point of this block.
    """
    inventory = _inventory()
    camera = next(d for d in inventory["facts"]["devices"] if d["label"] == "Camera")
    camera["properties"].append({
        "name": "Shutter (Internal)", "current_value": "Closed",
        "allowed_values": ["Auto", "Closed", "Open"],
        "read_only": False, "pre_init": False, "has_limits": False,
        "reported_type": "String",
    })
    candidates = inventory["heuristic_candidates"]
    candidates["illumination_enable_properties"].append({
        "path": "Camera.Shutter (Internal)", "device": "Camera",
        "property": "Shutter (Internal)", "device_type": "CameraDevice",
    })
    candidates["unclassified_writable_properties"].append("Camera.Shutter (Internal)")

    prompts = []
    config, _ = interview(
        inventory, ask=_answer_real_interview(prompts), say=lambda _: None,
    )
    typed = {
        (t["device"], t["property"]) for t in config["rig_profile"]["typed_actuators"]
        if t["kind"] == "bounded-numeric"
    }
    assert ("Camera", "Gain") in typed
    excluded = {
        (e["device"], e["property"])
        for e in config["rig_profile"]["excluded_properties"]
    }
    assert ("Camera", "Gain") not in excluded


def test_real_m5_laser_engine_bounded_numerics_follow_inventory_illumination_set():
    """Pin the M5 mode switches without relying on device or property-name rules."""
    inventory = _inventory()
    engine = next(d for d in inventory["facts"]["devices"] if d["label"] == "Laser")
    engine["label"] = "iChrome-MLE-TCP"
    for candidate_group in ("illumination_enable_properties", "suspected_continuous_actuators"):
        for candidate in inventory["heuristic_candidates"][candidate_group]:
            if candidate["device"] == "Laser":
                candidate["device"] = engine["label"]
                candidate["path"] = engine["label"] + "." + candidate["property"]
    writable = inventory["heuristic_candidates"]["unclassified_writable_properties"]
    writable[:] = [
        engine["label"] + path[len("Laser"):] if path.startswith("Laser.") else path
        for path in writable
    ]

    mode_properties = [
        "All: 4. TTL High Active", "All: 5. TTL Master Mode", "All: 6. Analog Mode",
        *(f"Laser {laser}: {field}" for laser in range(1, 5)
          for field in ("4. Use TTL", "5. Analog Mode")),
    ]
    for prop in mode_properties:
        engine["properties"].append({
            "name": prop, "current_value": "0", "allowed_values": [],
            "read_only": False, "pre_init": False, "has_limits": True,
            "reported_type": "Integer",
            "technical_range": {"lower": 0.0, "upper": 1.0, "source": "driver_reported"},
        })
        writable.append(f"{engine['label']}.{prop}")

    trigger = {
        "label": "Laser Trigger", "device_type": "GenericDevice", "properties": [],
    }
    for index in range(4):
        prop = f"Duration{index} (us)"
        trigger["properties"].append({
            "name": prop, "current_value": "1", "allowed_values": [],
            "read_only": False, "pre_init": False, "has_limits": True,
            "reported_type": "Integer",
            "technical_range": {"lower": 1.0, "upper": 100.0, "source": "driver_reported"},
        })
        writable.append(f"Laser Trigger.{prop}")
    inventory["facts"]["devices"].append(trigger)

    prompts, output = [], []
    config, notes = interview(
        inventory, ask=_answer_real_interview(prompts), say=output.append,
    )
    declared = {(row["device"], row["property"]) for row in config["rig_profile"]["typed_actuators"]}
    excluded = {(row["device"], row["property"]) for row in config["rig_profile"]["excluded_properties"]}
    assert {(engine["label"], prop) for prop in mode_properties} <= excluded
    assert not ({(engine["label"], prop) for prop in mode_properties} & declared)
    assert {
        ("Laser Trigger", f"Duration{index} (us)", "us") for index in range(4)
    } <= {
        (row["device"], row["property"], row["units"])
        for row in config["rig_profile"]["typed_actuators"]
    }
    assert sum("ILLUMINATING-DEVICE BOUNDED NUMERIC EXCLUDED" in note for note in notes) == 11
    assert sum("Revisit it by exact name" in line for line in output) >= 11
    assert not any(any(prop in prompt for prop in mode_properties) for prompt in prompts)


def test_metadata_proposal_glossary_defaults_and_bulk_revisit():
    answers = iter(["", "Camera.Binning", "x"] + list(_answers())[2:])
    output = []
    config, _ = interview(_inventory(), ask=lambda _: next(answers), say=output.append)
    assert any("PROPERTY CLASSIFICATION GLOSSARY" in line for line in output)
    assert any(
        "Camera.Binning [categorical: MM reports allowed values 1, 2, 4, 8]" in line
        for line in output
    )
    assert {x["property"] for x in config["rig_profile"]["excluded_properties"]} >= {"Binning"}


@pytest.mark.parametrize(("record", "role", "evidence", "bounds"), [
    ({"read_only": True, "pre_init": False}, "x", "read-only", None),
    ({"read_only": False, "pre_init": True}, "x", "pre-init-only", None),
    ({
        "read_only": False, "pre_init": False, "allowed_values": ["A", "B"],
    }, "c", "allowed values A, B", None),
    ({
        "read_only": False, "pre_init": False, "allowed_values": [],
        "has_limits": True, "reported_type": "Float",
        "technical_range": {"lower": -5.0, "upper": 8.0},
    }, "n", "property unit 'native'", (-5.0, 8.0)),
    ({
        "read_only": False, "pre_init": False, "allowed_values": [],
        "has_limits": False, "reported_type": "String",
    }, "x", "no discrete value domain or numeric limits", None),
])
def test_each_mm_metadata_shape_has_a_fail_closed_default(record, role, evidence, bounds):
    actual_role, actual_evidence, actual_bounds = _metadata_default({"record": record})
    assert actual_role == role
    assert evidence in actual_evidence
    assert actual_bounds == bounds


@pytest.mark.parametrize(("device", "prop", "unit"), [
    ("Camera", "Gain", "native"),
    ("Laser Trigger", "Duration0 (us)", "us"),
    ("Laser Trigger", "Sequence0", "native"),
    ("Servos", "Position3", "native"),
    ("PWM", "Position0", "native"),
    ("Camera", "OUTPUT TRIGGER DELAY[0]", "native"),
    ("Camera", "BUFFER ROWBYTES", "native"),
    ("Operator Assigned Label", "EMGain", "native"),
    ("Operator Assigned Label", "Gain (EM)", "EM"),
    ("Operator Assigned Label", "Pre-Amp Gain", "native"),
])
def test_bounded_numeric_unit_proposals_use_name_suffix_or_native(device, prop, unit):
    assert _bounded_numeric_unit({"device": device, "property": prop}) == unit


def test_stage_position_is_excluded_before_unitless_numeric_defaults_to_native():
    record = {
        "read_only": False, "pre_init": False, "allowed_values": [],
        "has_limits": True, "reported_type": "Integer",
        "technical_range": {"lower": 0.0, "upper": 28000.0},
    }
    role, reason, bounds = _metadata_default({
        "device": "Aux", "property": "Position (um)",
        "device_type": "StageDevice", "record": record,
    })
    assert (role, bounds) == ("x", None)
    assert "stage position" in reason

    role, reason, bounds = _metadata_default({
        "device": "Camera", "property": "BUFFER ROWBYTES",
        "device_type": "CameraDevice", "record": record,
    })
    assert (role, bounds) == ("n", (0.0, 28000.0))
    assert "property unit 'native'" in reason


@pytest.mark.parametrize("camera_label", ["Camera", "HamamatsuHam_DCAM"])
def test_camera_exposure_is_owned_by_alias_policy_before_numeric_unit_default(camera_label):
    inventory = _inventory()
    camera = next(
        device for device in inventory["facts"]["devices"]
        if device["label"] == "Camera"
    )
    camera["label"] = camera_label
    inventory["facts"]["core_device_assignments"]["camera"] = camera_label
    candidates = inventory["heuristic_candidates"]["unclassified_writable_properties"]
    for index, candidate in enumerate(candidates):
        if candidate.startswith("Camera."):
            candidates[index] = camera_label + candidate[len("Camera"):]

    output = []
    config, _ = interview(
        inventory, ask=_answer_real_interview([]), say=output.append,
    )
    path = f"{camera_label}.Exposure"
    assert any(
        line == f"{path} [dedicated policy: camera.max_exposure_ms; not duplicated in rig_profile]"
        for line in output
    )
    assert not any(
        row["device"] == camera_label and row["property"] == "Exposure"
        for row in config["rig_profile"]["typed_actuators"]
    )


def test_real_demo_inventory_bulk_pass_emits_bounded_numeric_defaults():
    inventory = json.loads(REAL_DEMO_INVENTORY.read_text(encoding="utf-8"))
    prompts = []
    config, _ = interview(
        inventory, ask=_answer_real_interview(prompts), say=lambda _: None,
    )
    assert len(prompts) == 70
    assert "make 54 ordinary properties writable" in prompts[0]
    assert "defaults proposed as excluded remain excluded" in prompts[0]
    assert any("hard maximum raw payload bytes" in prompt for prompt in prompts)
    assert not any("confirmation threshold raw bytes" in prompt for prompt in prompts)
    assert not any(prompt.startswith("Preset ") for prompt in prompts)
    assert {
        key: len(config["rig_profile"][key])
        for key in ("categorical_properties", "typed_actuators", "excluded_properties")
    } == {
        # Six moved from excluded to categorical when StateDevice positions began
        # reading their state labels: Dichroic, Emission, Excitation, LED,
        # Objective and Path .State — every one a selector whose .Label was
        # already categorical. Unitless bounded numerics then moved from
        # excluded to typed bounded-numeric. Finally the ten Core
        # device-assignment properties left excluded_properties entirely: they
        # are still unwritable (guaranteed mode is an allowlist, so silence
        # denies), but an *explicit* entry shadowed authorization.py's rule
        # permitting a channel preset to retarget Core.Shutter to a declared
        # illumination shutter, which refused the demo rig's four fluorescence
        # channels at startup. Core.TimeoutMs stays — it is excluded for having
        # no value domain, not for being structural.
        "categorical_properties": 38,
        "typed_actuators": 16,
        "excluded_properties": 12,
    }
    assert {
        (row["device"], row["property"], row["units"])
        for row in config["rig_profile"]["typed_actuators"]
    } >= {("Camera", "Gain", "native")}
    assert not any(
        row["device"] == "Camera" and row["property"] == "Exposure"
        for row in config["rig_profile"]["typed_actuators"]
    )
    assert config["channels"]["allowed"] == ["Cy5", "DAPI", "FITC", "Rhodamine"]


def test_illumination_numeric_domain_proposes_on_off_and_audits_override():
    inventory = _inventory()
    emission = next(
        prop for device in inventory["facts"]["devices"] if device["label"] == "Laser"
        for prop in device["properties"] if prop["name"] == "Emission"
    )
    emission["allowed_values"] = ["0", "1"]
    prompts, output = [], []

    def ask(prompt):
        prompts.append(prompt)
        if "Accept all MM-derived" in prompt or "names to revisit" in prompt:
            return ""
        if prompt.startswith("Illumination candidate Laser.Emission"):
            return "e"
        if "ON value for Laser.Emission" in prompt:
            return "0"  # intentionally invert the proposal
        if "OFF value for Laser.Emission" in prompt:
            return "1"
        if prompt.startswith("Illumination candidate Laser.Power"):
            return "x"
        if "minimum" in prompt: return "0"
        if "maximum" in prompt: return "100"
        if "proposed:" in prompt: return ""
        return "1"

    config, _ = interview(inventory, ask=ask, say=output.append)
    shutter = config["illumination"]["shutters"][0]
    assert shutter == {"device": "Laser", "property": "Emission", "on_value": "0", "off_value": "1"}
    assert any("OPERATOR OVERRIDE: Laser.Emission ON value = '0' (proposed '1')" in line for line in output)
    assert any("OPERATOR OVERRIDE: Laser.Emission OFF value = '1' (proposed '0')" in line for line in output)
    assert "default e; press Enter to accept" in next(
        p for p in prompts if p.startswith("Illumination candidate Laser.Emission")
    )


def test_illumination_value_proposal_acceptance_is_audited():
    output = []
    assert _proposed_text(
        "Exact ON", "1", lambda prompt: "", output.append, audit_name="Lamp.State ON value",
    ) == "1"
    assert output == ["PROPOSAL ACCEPTED: Lamp.State ON value = '1'."]


@pytest.mark.parametrize(("values", "expected"), [
    (["Closed", "Open"], ("Open", "Closed")),
    (["Off", "On", "Auto"], None),
    (["Maybe", "Unknown"], None),
])
def test_on_off_proposal_uses_vocabulary_only_when_unambiguous(values, expected):
    assert _on_off_proposal({"record": {"allowed_values": values}}) == expected


def test_real_ichrome_integer_range_proposes_string_one_and_zero():
    item = {"property": "All: 1. Enable", "record": {
        "allowed_values": [], "has_limits": True, "reported_type": "Integer",
        "technical_range": {"lower": 0.0, "upper": 1.0},
    }}
    assert _on_off_proposal(item) == ("1", "0")
    assert _emission_role_default(item) == "e"


@pytest.mark.parametrize(("item", "expected"), [
    ({"property": "Thorlabs ELL6.State", "record": {"allowed_values": ["0", "1"]}}, None),
    ({"property": "Power (mW)", "record": {
        "allowed_values": [], "has_limits": True, "reported_type": "Float",
        "technical_range": {"lower": 0.0, "upper": 75.0},
    }}, None),
])
def test_emission_default_excludes_state_and_continuous_power(item, expected):
    assert _emission_role_default(item) is expected


@pytest.mark.parametrize(("prop", "expected"), [
    ("Fine A (%)", "p"), ("Power (mW)", "n"),
    ("Laser Power [mW]", "n"), ("Laser Power", None),
    # M5's real iChrome activation line: a bare trailing percent.
    ("Laser 4: 3. Level %", "p"),
])
def test_power_unit_default_comes_from_trailing_unit(prop, expected):
    assert _power_units_default(prop) == expected


def test_reclassified_illumination_candidate_uses_ordinary_metadata_flow():
    inventory = _inventory()
    emission = next(
        prop for device in inventory["facts"]["devices"] if device["label"] == "Laser"
        for prop in device["properties"] if prop["name"] == "Emission"
    )
    emission["allowed_values"] = ["0", "1"]
    prompts, output = [], []

    def ask(prompt):
        prompts.append(prompt)
        if "Accept all MM-derived" in prompt or "names to revisit" in prompt:
            return ""
        if prompt.startswith("Illumination candidate Laser.Emission"):
            return "o"
        if prompt.startswith("Illumination candidate Laser.Power"):
            return "x"
        if "minimum" in prompt: return "0"
        if "maximum" in prompt: return "100"
        if "proposed:" in prompt: return ""
        return "1"

    config, notes = interview(inventory, ask=ask, say=output.append)
    assert {"device": "Laser", "property": "Emission"} in config["rig_profile"]["categorical_properties"]
    assert not config["illumination"]["shutters"]
    assert any("OPERATOR RECLASSIFICATION: Laser.Emission" in line for line in output)
    assert any("on operator instruction" in note for note in notes)


def test_native_full_scale_range_default_and_override_are_audited():
    inventory = _inventory()
    output = []

    def ask(prompt):
        if "Accept all MM-derived" in prompt or "names to revisit" in prompt: return ""
        if prompt.startswith("Illumination candidate Laser.Emission"): return "x"
        if prompt.startswith("Illumination candidate Laser.Power"): return "p"
        if "Representation units" in prompt: return "n"
        if "native full scale" in prompt: return "75"
        if "minimum" in prompt: return "0"
        if "maximum" in prompt: return "100"
        if "proposed:" in prompt: return ""
        return "1"

    config, _ = interview(inventory, ask=ask, say=output.append)
    assert config["illumination"]["power_properties"] == [{
        "device": "Laser", "property": "Power %", "units": "native", "full_scale": 75,
    }]
    assert any("OPERATOR OVERRIDE:" in line and "proposed 2450" in line for line in output)
    assert any("not a minimum; 0 is always writable" in line for line in output)


def test_native_full_scale_without_range_keeps_no_default_question():
    inventory = _inventory()
    power = next(
        prop for device in inventory["facts"]["devices"] if device["label"] == "Laser"
        for prop in device["properties"] if prop["name"] == "Power %"
    )
    power.update(has_limits=False, reported_type="String")
    power.pop("technical_range", None)
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        if "Accept all MM-derived" in prompt or "names to revisit" in prompt: return ""
        if prompt.startswith("Illumination candidate Laser.Emission"): return "x"
        if prompt.startswith("Illumination candidate Laser.Power"): return "p"
        if "Representation units" in prompt: return "n"
        if "native full scale" in prompt: return "75"
        if "minimum" in prompt: return "0"
        if "maximum" in prompt: return "100"
        if "proposed:" in prompt: return ""
        return "1"

    interview(inventory, ask=ask, say=lambda _: None)
    full_scale = next(prompt for prompt in prompts if "native full scale" in prompt)
    assert "required; no default" in full_scale


def test_stage_driver_ranges_are_offered_per_axis():
    inventory = _inventory()
    inventory["facts"]["core_device_assignments"]["xy_stage"] = "XY"
    xy = next(device for device in inventory["facts"]["devices"] if device["label"] == "XY")
    xy["properties"][0].update(
        has_limits=True, reported_type="Float",
        technical_range={"lower": -10, "upper": 20},
    )
    xy["properties"].append({
        "name": "YPosition", "current_value": "0", "allowed_values": [],
        "read_only": False, "pre_init": False, "has_limits": True,
        "reported_type": "Float", "technical_range": {"lower": -30, "upper": 40},
    })
    inventory["facts"]["devices"].append({
        "label": "Z", "device_type": "StageDevice", "state_labels": [],
        "properties": [{
            "name": "Position", "current_value": "0", "allowed_values": [],
            "read_only": False, "pre_init": False, "has_limits": True,
            "reported_type": "Float", "technical_range": {"lower": 1, "upper": 99},
        }],
    })
    prompts = []
    base = _answer_real_interview(prompts)
    def ask(prompt):
        if "MM driver technical range" in prompt:
            prompts.append(prompt)
            return ""
        return base(prompt)
    output = []
    config, _ = interview(inventory, ask=ask, say=output.append)
    assert config["stage"] == {
        "x_min": -10, "x_max": 20, "y_min": -30, "y_max": 40,
        "z_min": 1, "z_max": 99,
    }
    assert sum("MM driver technical range" in prompt for prompt in prompts) == 8
    assert sum(line.startswith("PROPOSAL ACCEPTED: Human-reviewed") for line in output) == 8


def test_named_stage_bounds_are_authored_from_captured_inventory():
    """The added device is derived from demo's captured Z record, not invented metadata."""
    inventory = json.loads(REAL_DEMO_INVENTORY.read_text(encoding="utf-8"))
    focus = next(
        copy.deepcopy(device) for device in inventory["facts"]["devices"]
        if device["label"] == inventory["facts"]["core_device_assignments"]["focus"]
    )
    focus["label"] = "Aux Z"
    position = next(prop for prop in focus["properties"] if prop["name"] == "Position")
    position.update(
        has_limits=True, reported_type="Float",
        technical_range={"lower": -125.0, "upper": 875.0},
    )
    inventory["facts"]["devices"].append(focus)
    prompts, output = [], []
    base = _answer_real_interview(prompts)
    def ask(prompt):
        if "named stage Aux Z" in prompt and "MM driver technical range" in prompt:
            prompts.append(prompt)
            return ""
        return base(prompt)
    config, _ = interview(
        inventory, ask=ask, say=output.append
    )
    assert config["named_stages"] == [{
        "device": "Aux Z", "min_um": -125.0, "max_um": 875.0,
    }]
    assert not any(
        item["device"] == inventory["facts"]["core_device_assignments"]["focus"]
        for item in config["named_stages"]
    )
    named_prompts = [prompt for prompt in prompts if "named stage Aux Z" in prompt]
    assert len(named_prompts) == 2
    assert all("MM driver technical range" in prompt for prompt in named_prompts)
    assert sum(
        line.startswith("PROPOSAL ACCEPTED: Human-reviewed")
        and "named stage Aux Z" in line for line in output
    ) == 2


def test_named_stage_without_driver_range_requires_typed_bounds():
    inventory = json.loads(REAL_DEMO_INVENTORY.read_text(encoding="utf-8"))
    focus = next(
        copy.deepcopy(device) for device in inventory["facts"]["devices"]
        if device["label"] == inventory["facts"]["core_device_assignments"]["focus"]
    )
    focus["label"] = "Aux Z"
    inventory["facts"]["devices"].append(focus)
    prompts, output = [], []
    config, _ = interview(
        inventory, ask=_answer_real_interview(prompts), say=output.append
    )
    assert config["named_stages"] == [{
        "device": "Aux Z", "min_um": 0.0, "max_um": 100.0,
    }]
    named_prompts = [prompt for prompt in prompts if "named stage Aux Z" in prompt]
    assert len(named_prompts) == 2
    assert all("MM driver technical range" not in prompt for prompt in named_prompts)
    assert any(
        "no travel limits for named stage device Aux Z" in line for line in output
    )


def test_non_core_xy_stage_is_explicitly_excluded():
    inventory = json.loads(REAL_DEMO_INVENTORY.read_text(encoding="utf-8"))
    xy = next(
        copy.deepcopy(device) for device in inventory["facts"]["devices"]
        if device["label"] == inventory["facts"]["core_device_assignments"]["xy_stage"]
    )
    xy["label"] = "Aux XY"
    inventory["facts"]["devices"].append(xy)
    output = []
    config, notes = interview(
        inventory, ask=_answer_real_interview([]), say=output.append
    )
    assert config["named_stages"] == []
    assert any(
        "XY stage device Aux XY is not the core XY stage" in line
        and "named_stages represents only a single axis" in line
        for line in output
    )
    assert any("UNSUPPORTED NON-CORE XY STAGE: Aux XY" in note for note in notes)


def test_real_demo_limit_sources_exposure_default_and_budget_order():
    inventory = json.loads(REAL_DEMO_INVENTORY.read_text(encoding="utf-8"))
    prompts, output = [], []
    interview(inventory, ask=_answer_real_interview(prompts), say=output.append)
    assert any("no X travel limits for XY stage device XY" in line for line in output)
    assert any("no Y travel limits for XY stage device XY" in line for line in output)
    assert any("no Z travel limits for focus stage device Z" in line for line in output)
    exposure = next(prompt for prompt in prompts if "maximum camera exposure" in prompt)
    assert "proposed: 10000" in exposure
    assert any(
        line.startswith("OPERATOR OVERRIDE: Human-reviewed maximum camera exposure for Camera")
        for line in output
    )
    ordered = [
        "maximum camera exposure", "hard maximum frames", "confirmation threshold frames",
        "hard maximum acquisition duration", "confirmation threshold duration",
        "hard maximum total shutter-open time", "confirmation threshold total shutter-open time",
        "hard maximum raw payload bytes",
    ]
    positions = [next(i for i, prompt in enumerate(prompts) if text in prompt) for text in ordered]
    assert positions == sorted(positions)


def test_real_demo_geometry_from_producer_removes_byte_question():
    inventory = json.loads(REAL_DEMO_INVENTORY.read_text(encoding="utf-8"))

    class GeometryCore:
        def get_image_width(self): return 512
        def get_image_height(self): return 512
        def get_bytes_per_pixel(self): return 2
        def get_image_bit_depth(self): return 16
        def get_roi(self): return (0, 0, 512, 512)

    devices = copy.deepcopy(inventory["facts"]["devices"])
    camera = inventory["facts"]["core_device_assignments"]["camera"]
    inventory["facts"]["camera_geometry"] = _camera_geometry(GeometryCore(), camera, devices, [])
    prompts, output = [], []
    fallback = _answer_real_interview(prompts)
    def accept_proposals(prompt):
        if "proposed:" in prompt:
            prompts.append(prompt)
            return ""
        return fallback(prompt)
    config, notes = interview(inventory, ask=accept_proposals, say=output.append)
    assert len(prompts) == 69
    assert not any("hard maximum raw payload bytes" in prompt for prompt in prompts)
    assert config["acquisition"]["max_bytes"] == 512 * 512 * 2 * config["acquisition"]["max_frames"]
    assert "confirm_above_bytes" not in config["acquisition"]
    assert "max_session_illuminated_ms" not in config["acquisition"]
    assert any("512 × 512 pixels × 2 bytes/pixel" in line for line in output)
    assert not any("BYTE CONFIRMATION INERT" in note for note in notes)


def test_byte_cap_uses_unbinned_full_frame_geometry_and_names_pixel_depth():
    inventory = json.loads(REAL_DEMO_INVENTORY.read_text(encoding="utf-8"))
    inventory["facts"]["camera_geometry"] = {
        "device": "Camera", "image_width": 512, "image_height": 512,
        "bytes_per_pixel": 2, "image_bit_depth": 16,
        "roi": [0, 0, 512, 512], "binning": 4,
        "unbinned_full_frame_pixels": {"width": 2048, "height": 2048},
    }
    prompts, output = [], []
    base = _answer_real_interview(prompts)
    config, _ = interview(inventory, ask=base, say=output.append)
    expected = 2048 * 2048 * 2 * config["acquisition"]["max_frames"]
    assert config["acquisition"]["max_bytes"] == expected
    arithmetic = next(line for line in output if line.startswith("DERIVED hard maximum raw payload bytes"))
    assert "2048 × 2048 pixels" in arithmetic
    assert "unbinned full-frame dimensions" in arithmetic
    assert "pixel-type depth 16 bits" in arithmetic


def test_byte_cap_falls_back_to_current_dimensions_when_binning_unknown():
    inventory = json.loads(REAL_DEMO_INVENTORY.read_text(encoding="utf-8"))
    inventory["facts"]["camera_geometry"] = {
        "device": "Camera", "image_width": 300, "image_height": 200,
        "bytes_per_pixel": 4, "image_bit_depth": 32,
        "roi": [0, 0, 300, 200], "binning": None,
        "unbinned_full_frame_pixels": None,
    }
    prompts, output = [], []
    config, _ = interview(inventory, ask=_answer_real_interview(prompts), say=output.append)
    assert config["acquisition"]["max_bytes"] == 300 * 200 * 4 * config["acquisition"]["max_frames"]
    arithmetic = next(line for line in output if line.startswith("DERIVED hard maximum raw payload bytes"))
    assert "current binned dimensions because binning is unknown" in arithmetic


def test_bulk_accept_and_individual_review_produce_same_real_demo_profile():
    inventory = json.loads(REAL_DEMO_INVENTORY.read_text(encoding="utf-8"))
    individual_prompts = []
    bulk, _ = interview(
        inventory, ask=_answer_real_interview([]), say=lambda _: None,
    )
    individual, _ = interview(
        inventory, ask=_answer_real_interview(individual_prompts, bulk=False), say=lambda _: None,
    )
    assert bulk == individual
    assert not any("names to revisit" in prompt for prompt in individual_prompts)


def test_bounded_numeric_default_records_operator_unit_verbatim():
    supplied_units = []
    prompts = []

    inventory = _inventory()
    inventory["facts"]["configuration_groups"] = [{
        "name": "Channel", "presets": [{
            "name": "TouchesGain",
            "effects": [{"device": "Camera", "property": "Gain", "value": "3"}],
        }],
    }]

    def ask(prompt):
        prompts.append(prompt)
        if "Accept all MM-derived" in prompt:
            return ""
        if "names to revisit" in prompt:
            return "Camera.Gain"
        if prompt.startswith("Writable property Camera.Gain"):
            return "n"
        if prompt.startswith("Operator-confirmed unit string for Camera.Gain"):
            supplied_units.append("e-/ADU")
            return "e-/ADU"
        if "MM driver technical range" in prompt:
            return ""
        if prompt.startswith("Illumination candidate Laser.Emission"):
            return "e"
        if prompt.startswith("Illumination candidate Laser.Power"):
            return "p"
        if "Representation units" in prompt:
            return "p"
        if "ON value" in prompt:
            return "ON"
        if "OFF value" in prompt:
            return "OFF"
        if "minimum" in prompt:
            return "0"
        if "maximum" in prompt:
            return "200"
        if prompt.startswith("Preset Channel.TouchesGain"):
            return ""
        return "1"

    config, _ = interview(inventory, ask=ask, say=lambda _: None)
    assert config["rig_profile"]["typed_actuators"] == [{
        "device": "Camera", "property": "Gain", "kind": "bounded-numeric",
        "units": "e-/ADU", "minimum": 2440.0, "maximum": 2450.0,
    }]
    assert supplied_units == ["e-/ADU"]
    assert all(
        row["units"] in supplied_units
        for row in config["rig_profile"]["typed_actuators"]
    )
    assert any(
        prompt.startswith("Preset Channel.TouchesGain") and "TYPED-PROPERTY COLLISION" in prompt
        for prompt in prompts
    )


def test_revisit_typo_reprompts_with_near_match():
    answers = iter(["Camera.Binnin", "Camera.Binning"])
    output = []

    def ask(prompt):
        if "Accept all MM-derived" in prompt:
            return ""
        if "names to revisit" in prompt:
            return next(answers)
        if prompt.startswith("Writable property Camera.Binning"):
            return ""
        return _answer_real_interview([])(prompt)

    interview(_inventory(), ask=ask, say=output.append)
    assert any(
        line.startswith("SETUP REFUSAL: Revisit name") and "Camera.Binning" in line
        for line in output
    )


def test_disconnect_releases_core_and_bridge(monkeypatch):
    events = []
    core = type("Core", (), {"_close": lambda self: events.append("core")})()
    bridge = type("Bridge", (), {"close": lambda self: events.append("bridge")})()
    ref = lambda: bridge
    from pyjavaz.bridge import Bridge
    monkeypatch.setitem(Bridge._cached_bridges_by_port, 9988, ref)
    disconnect_core(core, 9988)
    assert events == ["core", "bridge"]


def test_transcript_flushes_identity_inventory_and_each_exchange(monkeypatch, tmp_path):
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_bytes(b'{"schema":"example"}\n')
    path = tmp_path / "first-launch-transcript.txt"
    transcript = InterviewTranscript(path)
    transcript.identify_inventory(inventory_path)
    transcript.say("SETUP DEFERRAL: example")
    monkeypatch.setattr("builtins.input", lambda prompt: "verbatim answer")
    assert transcript.ask("Verbatim prompt: ") == "verbatim answer"
    transcript.outcome("FINAL OUTCOME: stopped")
    transcript.close()

    text = path.read_text(encoding="utf-8")
    assert "UTC timestamp:" in text
    assert "Microclaw version:" in text
    assert "Microclaw commit:" in text
    assert f"Inventory path: {inventory_path.resolve()}" in text
    assert f"Inventory sha256: {hashlib.sha256(inventory_path.read_bytes()).hexdigest()}" in text
    assert "SETUP DEFERRAL: example\nVerbatim prompt: verbatim answer\nFINAL OUTCOME: stopped" in text


def test_transcript_keeps_last_prompt_when_input_aborts(monkeypatch, tmp_path):
    path = tmp_path / "partial.txt"
    transcript = InterviewTranscript(path)
    monkeypatch.setattr(
        "builtins.input", lambda prompt: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        transcript.ask("Prompt before abort: ")
    transcript.close()
    assert path.read_text(encoding="utf-8").endswith("Prompt before abort: ")


def test_generated_profile_uses_shared_validator(monkeypatch, tmp_path):
    seen = []
    expected = ConfigValidationResult(
        tmp_path / "temporary", object(), False,
        (ConfigDiagnostic("review", "review it", True),),
    )
    monkeypatch.setattr(
        "microclaw.first_launch.validate_safety_config",
        lambda path: seen.append(path) or expected,
    )
    target = tmp_path / "draft.yaml"
    result = write_profile({"reviewed": True}, [], target)
    assert len(seen) == 1 and seen[0] != target and seen[0].parent == target.parent
    assert result.path == target
    assert yaml.safe_load(target.read_text())["reviewed"] is False


def test_rejected_temporary_profile_leaves_no_target_or_temporary(monkeypatch, tmp_path):
    target = tmp_path / "draft.yaml"
    target.write_text("stale rejected draft", encoding="utf-8")
    rejected = ConfigValidationResult(
        tmp_path / "temporary", None, False,
        (ConfigDiagnostic("schema", "invalid generated document", True),),
    )
    monkeypatch.setattr(
        "microclaw.first_launch.validate_safety_config", lambda path: rejected,
    )
    with pytest.raises(SetupRefusal, match=r"no file was written at .*draft.yaml"):
        write_profile({"reviewed": True}, [], target)
    assert not target.exists()
    assert list(tmp_path.glob(".draft.yaml.*.tmp")) == []


def test_live_contact_warning_and_exact_acknowledgement_precede_core(monkeypatch, tmp_path, capsys):
    constructed = []

    def core(*, port):
        output = capsys.readouterr().out
        assert "Enumeration is hardware contact" in output
        assert "no agent- or tool-directed hardware action" in output
        constructed.append(port)
        return SimpleNamespace(get_version_info=lambda: "MMCore", _close=lambda: None)

    monkeypatch.setattr("pycromanager.Core", core)
    monkeypatch.setattr("builtins.input", lambda prompt: CONTACT_ACKNOWLEDGEMENT)
    monkeypatch.setattr("microclaw.rig_inventory.enumerate_rig", lambda core, mm_config: _inventory())
    def write_outputs(inv, out):
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        inventory_path = out / "inventory.json"
        inventory_path.write_text(json.dumps(inv), encoding="utf-8")
        return inventory_path, out / "review.md"

    monkeypatch.setattr("microclaw.rig_inventory.write_inventory_outputs", write_outputs)
    answers = _answers()
    monkeypatch.setattr(
        "microclaw.first_launch.interview",
        lambda inv, **kwargs: interview(inv, ask=lambda _: next(answers), say=lambda _: None),
    )
    cli.first_launch_setup(_args(tmp_path))
    assert constructed == [4827]


def test_wrong_contact_acknowledgement_exits_without_constructing_core(monkeypatch, tmp_path):
    monkeypatch.setattr("pycromanager.Core", lambda **kwargs: pytest.fail("Core must not be constructed"))
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    with pytest.raises(SystemExit, match="Exited without connecting"):
        cli.first_launch_setup(_args(tmp_path))
    transcript_path, = (tmp_path / "evidence").glob("first-launch-transcript-*.txt")
    transcript = transcript_path.read_text(encoding="utf-8")
    assert "type exactly" in transcript
    assert "yes" in transcript
    assert "SETUP REFUSAL: Hardware-contact acknowledgement did not match" in transcript


def test_existing_output_refusal_preserves_previous_transcript(tmp_path):
    target = tmp_path / "existing.yaml"
    target.write_text("reviewed work\n", encoding="utf-8")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    prior = evidence / "first-launch-transcript-prior.txt"
    prior.write_text("PRIOR RUN\n", encoding="utf-8")
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(_inventory()), encoding="utf-8")

    with pytest.raises(SystemExit, match="already exists"):
        cli.first_launch_setup(_args(
            tmp_path, out=target, evidence_out=evidence, inventory=inventory_path,
        ))
    assert prior.read_text(encoding="utf-8") == "PRIOR RUN\n"
    assert list(evidence.iterdir()) == [prior]


def test_bad_evidence_directory_is_a_setup_refusal(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "microclaw.first_launch.new_interview_transcript",
        lambda path: (_ for _ in ()).throw(OSError("read-only evidence path")),
    )
    with pytest.raises(SystemExit, match="SETUP REFUSAL: Could not create interview evidence"):
        cli.first_launch_setup(_args(tmp_path, inventory=tmp_path / "unused.json"))


def test_eof_mid_interview_leaves_readable_partial_transcript(monkeypatch, tmp_path):
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(_inventory()), encoding="utf-8")
    answers = iter([""])

    def abort_after_bulk(prompt):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", abort_after_bulk)
    with pytest.raises(SystemExit, match="interview ended"):
        cli.first_launch_setup(_args(tmp_path, inventory=inventory_path))

    transcript_path, = (tmp_path / "evidence").glob("first-launch-transcript-*.txt")
    text = transcript_path.read_text(encoding="utf-8")
    assert "Accept all MM-derived defaults" in text
    assert "Exact property or preset names to revisit" in text
    assert text.endswith(
        "SETUP REFUSAL: The interview ended before every decision was answered. "
        "No profile was generated or loaded; rerun setup to start a complete interview.\n"
    )


def test_existing_inventory_shows_honesty_text_without_contact_ack(monkeypatch, tmp_path, capsys):
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(_inventory()), encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("contact acknowledgement must not be requested"))
    answers = _answers()
    monkeypatch.setattr(
        "microclaw.first_launch.interview",
        lambda inv, **kwargs: interview(inv, ask=lambda _: next(answers), say=lambda _: None),
    )
    cli.first_launch_setup(_args(tmp_path, inventory=inventory_path))
    output = capsys.readouterr().out
    assert "Enumeration is hardware contact" in output
    assert "normal restart" in output
    assert "no hardware connection was opened" in output


@pytest.mark.parametrize("failure,expected,unexpected", [
    ("connection", "Could not connect to the already-running Micro-Manager Core", "config None"),
    ("enumeration", "Read-only rig enumeration failed", "config None"),
])
def test_bridge_and_enumeration_errors_are_not_mislabeled(
    monkeypatch, tmp_path, failure, expected, unexpected,
):
    monkeypatch.setattr("builtins.input", lambda prompt: CONTACT_ACKNOWLEDGEMENT)
    if failure == "connection":
        monkeypatch.setattr("pycromanager.Core", lambda **kwargs: (_ for _ in ()).throw(OSError("bridge down")))
    else:
        monkeypatch.setattr(
            "pycromanager.Core",
            lambda **kwargs: SimpleNamespace(get_version_info=lambda: "MMCore", _close=lambda: None),
        )
        monkeypatch.setattr(
            "microclaw.rig_inventory.enumerate_rig",
            lambda core, mm_config: (_ for _ in ()).throw(OSError("driver sweep failed")),
        )
    with pytest.raises(SystemExit) as exc:
        cli.first_launch_setup(_args(tmp_path))
    assert expected in str(exc.value)
    assert unexpected not in str(exc.value)


def test_unreadable_mm_config_has_scoped_message_before_core(monkeypatch, tmp_path):
    missing = tmp_path / "missing.cfg"
    monkeypatch.setattr("pycromanager.Core", lambda **kwargs: pytest.fail("Core must not be constructed"))
    with pytest.raises(SystemExit) as exc:
        cli.first_launch_setup(_args(tmp_path, mm_config=missing))
    assert f"Could not read Micro-Manager config {missing}" in str(exc.value)
