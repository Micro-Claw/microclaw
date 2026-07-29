from types import SimpleNamespace

import pytest

from microclaw.authorization import RigAuthorizationError, validate_live_rig
from microclaw.safety import (
    AcquisitionConstraints, CameraConstraints, IlluminationConstraints,
    ParsedSafetyConfig, RigProfile, SafetyConfigError, SafetyConstraints,
    SafetyGuard, SafetyViolation, TypedActuatorId, TypedActuatorPolicy,
    TypedPowerProperty,
)


def _yaml(tmp_path, body):
    path = tmp_path / "safety.yaml"
    path.write_text(
        "schema_version: 2\nreviewed: true\n"
        "rig_profile:\n  mode: guaranteed\n  categorical_properties: []\n"
        "  excluded_properties: []\n" + body +
        "acquisition:\n  max_frames: 1\n  max_duration_s: 1\n  max_bytes: 1\n"
        "  max_illuminated_ms: 1\n  max_session_illuminated_ms: 1\n"
        "  confirm_above_frames: 1\n  confirm_above_duration_s: 1\n"
        "  confirm_above_bytes: 1\n  confirm_above_illuminated_ms: 1\n"
    )
    return ParsedSafetyConfig.from_yaml(str(path))


def test_registry_schema_and_native_conversion_boundaries(tmp_path):
    parsed = _yaml(tmp_path,
        "  typed_actuators:\n"
        "    - {device: Laser, property: Power, kind: illumination-power, units: native, full_scale: 75, minimum: 0, maximum: 40}\n")
    guard = SafetyGuard(parsed.constraints)
    guard.admit_typed_actuators(parsed.typed_actuators)
    guard.check_typed_actuator("Laser", "Power", "30")  # 40 percent exactly
    with pytest.raises(SafetyViolation, match="canonical value 40.0013"):
        guard.check_typed_actuator("Laser", "Power", "30.001")
    guard._c.illumination.power_properties = [
        TypedPowerProperty("Laser", "Power", units="native", full_scale=75)
    ]
    assert guard.illumination_to_percent("Laser", "Power", 30) == 40
    assert guard.illumination_from_percent("Laser", "Power", 40) == 30


@pytest.mark.parametrize("kind,units", [("velocity", "um"), ("absolute-position", "mm")])
def test_unknown_kind_and_unit_are_file_anchored_parse_errors(tmp_path, kind, units):
    with pytest.raises(SafetyConfigError) as exc:
        _yaml(tmp_path,
            f"  typed_actuators:\n    - {{device: Z, property: P, kind: {kind}, units: {units}, minimum: 0, maximum: 1}}\n")
    assert str(tmp_path / "safety.yaml") in str(exc.value)
    assert "unsupported" in str(exc.value)


def test_no_registry_remains_empty_and_opt_in(tmp_path):
    parsed = _yaml(tmp_path, "  typed_actuators: []\n")
    assert parsed.typed_actuators == {}
    assert parsed.constraints.allowed_properties == []


class LiveCore:
    def __init__(self, kind="StageDevice", prop="Position (um)", upper=200):
        self.kind, self.prop, self.upper = kind, prop, upper
        self.presets = {}
    def get_xy_stage_device(self): return ""
    def get_focus_device(self): return ""
    def get_camera_device(self): return ""
    def get_shutter_device(self): return ""
    def get_loaded_devices(self): return ["Z"]
    def get_device_type(self, device): return self.kind
    def get_device_property_names(self, device): return [self.prop]
    def is_property_read_only(self, device, prop): return False
    def get_allowed_property_values(self, device, prop): return []
    def get_property_type(self, device, prop): return "Float"
    def has_property_limits(self, device, prop): return True
    def get_property_lower_limit(self, device, prop): return 0
    def get_property_upper_limit(self, device, prop): return self.upper
    def get_available_configs(self, group): return list(self.presets)
    def get_config_data(self, group, preset): return self.presets[preset]


def _direct(core, *, categorical=(), typed=None, illumination=None):
    acquisition = AcquisitionConstraints(**{name: 1 for name in (
        "max_frames", "max_duration_s", "max_bytes", "max_illuminated_ms",
        "max_session_illuminated_ms", "confirm_above_frames",
        "confirm_above_duration_s", "confirm_above_bytes", "confirm_above_illuminated_ms")})
    constraints = SafetyConstraints(camera=CameraConstraints(1), acquisition=acquisition,
        allowed_channels=[], allowed_properties=[], illumination=illumination or IlluminationConstraints())
    parsed = ParsedSafetyConfig(constraints, {}, RigProfile("guaranteed", frozenset(categorical), frozenset()), typed or {})
    return SimpleNamespace(core=core), parsed


def test_second_z_or_driver_specific_focus_name_cannot_be_categorical():
    ctrl, parsed = _direct(LiveCore(), categorical={("Z", "Position (um)")})
    with pytest.raises(RigAuthorizationError, match="known continuous actuator"):
        validate_live_rig(ctrl, parsed)


def test_state_device_integer_state_with_limits_is_not_continuous():
    ctrl, parsed = _direct(LiveCore("StateDevice", "State", 5))
    report = validate_live_rig(ctrl, parsed)
    assert any(e.device == "Z" and e.property == "State" and e.source == "auto:state-device" for e in report.entries)


def test_declared_bound_outside_technical_range_is_refused():
    policy = TypedActuatorPolicy("absolute-position", "um", 0, 250)
    ctrl, parsed = _direct(LiveCore(), typed={TypedActuatorId("Z", "Position (um)"): policy})
    with pytest.raises(RigAuthorizationError, match="driver-reported technical range"):
        validate_live_rig(ctrl, parsed)


def test_preset_effect_on_typed_pair_is_classified_then_excluded():
    core = LiveCore()
    core.presets["Move"] = [{"device": "Z", "property": "Position (um)", "value": "10"}]
    policy = TypedActuatorPolicy("absolute-position", "um", 0, 100)
    ctrl, parsed = _direct(core, typed={TypedActuatorId("Z", "Position (um)"): policy})
    parsed.constraints.allowed_channels = ["Move"]
    with pytest.raises(RigAuthorizationError, match="typed continuous.*deferred channel-plan executor"):
        validate_live_rig(ctrl, parsed)


def test_m5_native_power_shape_requires_units_migration():
    illumination = IlluminationConstraints(
        power_properties=[TypedPowerProperty("Z", "Power (mW)")],
        max_power_percent=100, max_power_step_factor=2)
    ctrl, parsed = _direct(LiveCore("GenericDevice", "Power (mW)", 75), illumination=illumination)
    with pytest.raises(RigAuthorizationError, match="units: native.*full_scale.*75.0"):
        validate_live_rig(ctrl, parsed)
