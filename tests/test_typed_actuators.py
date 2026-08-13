from types import SimpleNamespace

import pytest

from microclaw.authorization import (
    RigAuthorizationError, authorize_property_write, validate_live_rig,
)
from microclaw.safety import (
    AcquisitionConstraints, CameraConstraints, IlluminationConstraints,
    ActuatorId, ForbiddenProperty, ParsedSafetyConfig, RangeEdge, RangePolicy,
    PropertyAuthorization, SafetyConfigError, SafetyConstraints, StageConstraints,
    SafetyGuard, SafetyViolation, TypedActuatorId, TypedActuatorPolicy,
    TypedPowerProperty, IlluminationProperty,
)


def _yaml(tmp_path, body):
    path = tmp_path / "safety.yaml"
    path.write_text(
        "schema_version: 3\nreviewed: true\n"
        "property_authorization:\n  mode: guaranteed\n  allowed_categorical: []\n"
        "  denied: []\n" + body +
        "acquisition:\n  max_frames: 1\n  max_duration_s: 1\n  max_bytes: 1\n"
        "  max_illuminated_ms: 1\n  max_session_illuminated_ms: 1\n"
        "  confirm_above_frames: 1\n  confirm_above_duration_s: 1\n"
        "  confirm_above_bytes: 1\n  confirm_above_illuminated_ms: 1\n"
    , encoding="utf-8")
    return ParsedSafetyConfig.from_yaml(str(path))


def test_registry_schema_and_native_conversion_boundaries(tmp_path):
    parsed = _yaml(tmp_path,
        "  allowed_numeric:\n"
        "    - {device: Laser, property: Power, kind: illumination-power, units: native, full_scale: 75, minimum: 0, maximum: 40}\n")
    guard = SafetyGuard(parsed.constraints)
    guard.admit_typed_actuators(parsed.property_authorization.allowed_numeric)
    guard.check_typed_actuator("Laser", "Power", "30")  # 40 percent exactly
    with pytest.raises(SafetyViolation, match="canonical value 40.0013"):
        guard.check_typed_actuator("Laser", "Power", "30.001")
    guard._c.illumination.power_properties = [
        TypedPowerProperty("Laser", "Power", units="native", full_scale=75)
    ]
    assert guard.illumination_to_percent("Laser", "Power", 30) == 40
    assert guard.illumination_from_percent("Laser", "Power", 40) == 30


def test_bounded_numeric_clamps_edges_echoes_verbatim_units_and_rejects_full_scale(tmp_path):
    parsed = _yaml(tmp_path,
        "  allowed_numeric:\n"
        "    - {device: Camera, property: Gain, kind: bounded-numeric, units: e-/ADU, minimum: -5, maximum: 8}\n")
    policy = parsed.property_authorization.allowed_numeric[TypedActuatorId("Camera", "Gain")]
    assert policy.units == "e-/ADU"
    guard = SafetyGuard(parsed.constraints)
    guard.admit_typed_actuators(parsed.property_authorization.allowed_numeric)
    guard.check_typed_actuator("Camera", "Gain", "-5")
    guard.check_typed_actuator("Camera", "Gain", "8")
    for value in ("-5.001", "8.001"):
        with pytest.raises(SafetyViolation, match=r"e-/ADU"):
            guard.check_typed_actuator("Camera", "Gain", value)
    with pytest.raises(SafetyConfigError, match="full_scale.*only valid"):
        _yaml(tmp_path,
            "  allowed_numeric:\n"
            "    - {device: Camera, property: Gain, kind: bounded-numeric, units: e-/ADU, minimum: -5, maximum: 8, full_scale: 10}\n")


def test_camera_gain_bounded_numeric_does_not_enter_stage_or_dose_guards():
    core = LiveCore("CameraDevice", "Gain", 100, device="Camera")
    core.get_camera_device = lambda: "Camera"
    guard = SafetyGuard(SafetyConstraints(
        stage=StageConstraints(z_min=0, z_max=1),
        acquisition=AcquisitionConstraints(max_session_illuminated_ms=1),
        allowed_properties=[],
    ))
    guard.admit_typed_actuators({TypedActuatorId("Camera", "Gain"):
        TypedActuatorPolicy("bounded-numeric", "turns", 0, 100)})
    guard.check_device_property(core, "Camera", "Gain", "50")


@pytest.mark.parametrize("section", [
    "  shutters:\n    - {device: Camera, property: Gain}\n",
    "  power_properties:\n    - {device: Camera, property: Gain, units: percent}\n",
])
def test_bounded_numeric_illumination_alias_is_offline_parse_refusal(tmp_path, section):
    path = tmp_path / "safety.yaml"
    path.write_text(
        "schema_version: 3\nreviewed: true\n"
        "property_authorization:\n  mode: guaranteed\n  allowed_categorical: []\n  denied: []\n"
        "  allowed_numeric:\n    - {device: Camera, property: Gain, kind: bounded-numeric, units: dB, minimum: 0, maximum: 10}\n"
        "illumination:\n" + section +
        "acquisition: {max_frames: 1, max_duration_s: 1, max_bytes: 1, max_illuminated_ms: 1, confirm_above_frames: 1, confirm_above_duration_s: 1, confirm_above_illuminated_ms: 1}\n"
    , encoding="utf-8")
    with pytest.raises(SafetyConfigError, match="aliases a declared illumination capability"):
        ParsedSafetyConfig.from_yaml(str(path))


@pytest.mark.parametrize("kind,units", [("velocity", "um"), ("absolute-position", "mm")])
def test_unknown_kind_and_unit_are_file_anchored_parse_errors(tmp_path, kind, units):
    with pytest.raises(SafetyConfigError) as exc:
        _yaml(tmp_path,
            f"  allowed_numeric:\n    - {{device: Z, property: P, kind: {kind}, units: {units}, minimum: 0, maximum: 1}}\n")
    assert str(tmp_path / "safety.yaml") in str(exc.value)
    assert "unsupported" in str(exc.value)


def test_no_registry_remains_empty_and_opt_in(tmp_path):
    parsed = _yaml(tmp_path, "  allowed_numeric: []\n")
    assert parsed.property_authorization.allowed_numeric == {}
    assert parsed.constraints.allowed_properties == []


class LiveCore:
    def __init__(self, kind="StageDevice", prop="Position (um)", upper=200, focus="",
                 *, prop_type="Float", pre_init=False, limits=True, device="Z"):
        self.kind, self.prop, self.upper = kind, prop, upper
        self.focus = focus
        self.device = device
        self.prop_type, self.pre_init, self.limits = prop_type, pre_init, limits
        self.loaded_calls = 0
        self.presets = {}
    def get_xy_stage_device(self): return ""
    def get_focus_device(self): return self.focus
    def get_camera_device(self): return ""
    def get_shutter_device(self): return ""
    def get_loaded_devices(self):
        self.loaded_calls += 1
        return [self.device]
    def get_device_type(self, device): return self.kind
    def get_device_property_names(self, device): return [self.prop]
    def is_property_pre_init(self, device, prop): return self.pre_init
    def is_property_read_only(self, device, prop): return False
    def get_allowed_property_values(self, device, prop): return []
    def get_property_type(self, device, prop): return self.prop_type
    def has_property_limits(self, device, prop): return self.limits
    def get_property_lower_limit(self, device, prop): return 0
    def get_property_upper_limit(self, device, prop): return self.upper
    def get_available_configs(self, group): return list(self.presets)
    def get_config_data(self, group, preset): return self.presets[preset]
    def set_property(self, device, prop, value): self.last_write = (device, prop, value)


def _direct(core, *, categorical=(), excluded=(), forbidden=(), typed=None,
            illumination=None, stage=None, ranges=None):
    acquisition = AcquisitionConstraints(**{name: 1 for name in (
        "max_frames", "max_duration_s", "max_bytes", "max_illuminated_ms",
        "max_session_illuminated_ms", "confirm_above_frames",
        "confirm_above_duration_s", "confirm_above_bytes", "confirm_above_illuminated_ms")})
    constraints = SafetyConstraints(stage=stage or StageConstraints(), camera=CameraConstraints(1),
        acquisition=acquisition, allowed_channels=[], allowed_properties=[],
        forbidden_properties=list(forbidden), illumination=illumination or IlluminationConstraints())
    parsed = ParsedSafetyConfig(constraints, ranges or {},
        PropertyAuthorization("guaranteed", frozenset(categorical), typed or {}, frozenset(excluded)),
        frozenset({"property_authorization", "illumination", "camera", "channels",
                   "acquisition", "stage"}))
    return SimpleNamespace(core=core), parsed


def test_second_z_or_driver_specific_focus_name_cannot_be_categorical():
    ctrl, parsed = _direct(LiveCore(), categorical={("Z", "Position (um)")})
    report = validate_live_rig(ctrl, parsed)
    assert report.diagnostics and not report.diagnostics[0].blocking
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "Z", "Position (um)")


def test_state_device_integer_state_with_limits_is_not_continuous():
    ctrl, parsed = _direct(LiveCore("StateDevice", "State", 5))
    report = validate_live_rig(ctrl, parsed)
    assert any(e.device == "Z" and e.property == "State" and e.source == "auto:state-device" for e in report.entries)


def test_declared_bound_outside_technical_range_is_refused():
    policy = TypedActuatorPolicy("absolute-position", "um", 0, 250)
    ctrl, parsed = _direct(LiveCore(), typed={TypedActuatorId("Z", "Position (um)"): policy})
    with pytest.raises(RigAuthorizationError, match="driver-reported technical range"):
        validate_live_rig(ctrl, parsed)


def test_bounded_numeric_declared_bound_outside_driver_range_is_refused():
    policy = TypedActuatorPolicy("bounded-numeric", "e-/ADU", 0, 250)
    ctrl, parsed = _direct(LiveCore(), typed={TypedActuatorId("Z", "Position (um)"): policy})
    with pytest.raises(RigAuthorizationError, match="driver-reported technical range"):
        validate_live_rig(ctrl, parsed)


def test_camera_exposure_cannot_be_declared_bounded_numeric():
    core = LiveCore("CameraDevice", "Exposure", 100, device="Camera")
    core.get_camera_device = lambda: "Camera"
    policy = TypedActuatorPolicy("bounded-numeric", "ms", 0, 50)
    ctrl, parsed = _direct(core, typed={TypedActuatorId("Camera", "Exposure"): policy})
    with pytest.raises(RigAuthorizationError, match="aliases a built-in.*exposure"):
        validate_live_rig(ctrl, parsed)


@pytest.mark.parametrize("kind,prop", [
    ("StageDevice", "Position (um)"),
    ("XYStageDevice", "XPosition"),
])
def test_stage_position_cannot_be_declared_bounded_numeric(kind, prop):
    core = LiveCore(kind, prop, 100, device="AuxStage")
    policy = TypedActuatorPolicy("bounded-numeric", "um", 0, 50)
    ctrl, parsed = _direct(core, typed={TypedActuatorId("AuxStage", prop): policy})
    with pytest.raises(RigAuthorizationError, match="stage motion must retain"):
        validate_live_rig(ctrl, parsed)


def test_preset_effect_on_typed_pair_is_authorized_for_executor():
    core = LiveCore()
    core.presets["Move"] = [{"device": "Z", "property": "Position (um)", "value": "10"}]
    policy = TypedActuatorPolicy("absolute-position", "um", 0, 100)
    ctrl, parsed = _direct(core, typed={TypedActuatorId("Z", "Position (um)"): policy})
    parsed.constraints.allowed_channels = ["Move"]
    report = validate_live_rig(ctrl, parsed)
    assert report.authorized_presets == {"Move"}
    assert any(
        entry.path == "channel-preset:Move"
        and entry.classification == "typed_continuous_actuator"
        for entry in report.entries
    )


def test_m5_native_power_shape_requires_units_migration():
    illumination = IlluminationConstraints(
        power_properties=[TypedPowerProperty("Z", "Power (mW)")],
        max_power_percent=100, max_power_step_factor=2)
    ctrl, parsed = _direct(LiveCore("GenericDevice", "Power (mW)", 75), illumination=illumination)
    with pytest.raises(RigAuthorizationError, match="units: native.*full_scale.*75.0"):
        validate_live_rig(ctrl, parsed)


@pytest.mark.parametrize("excluded_kind", ["forbidden", "profile"])
def test_typed_declaration_conflicts_with_explicit_exclusion(excluded_kind):
    pair = TypedActuatorId("Z", "Position (um)")
    kwargs = ({"forbidden": [ForbiddenProperty("Z", "Position (um)")]}
              if excluded_kind == "forbidden" else {"excluded": {("Z", "Position (um)")}})
    ctrl, parsed = _direct(LiveCore(), typed={pair: TypedActuatorPolicy(
        "absolute-position", "um", 0, 100)}, **kwargs)
    with pytest.raises(RigAuthorizationError, match="typed-continuous and explicitly excluded"):
        validate_live_rig(ctrl, parsed)


def test_typed_denylist_still_wins_through_set_device_property():
    from microclaw.tools import set_device_property

    core = LiveCore()
    ctrl = SimpleNamespace(core=core, studio=SimpleNamespace(
        app=lambda: SimpleNamespace(refresh_gui=lambda: None)))
    guard = SafetyGuard(SafetyConstraints(
        forbidden_properties=[ForbiddenProperty("Z", "Position (um)")]))
    guard.admit_typed_actuators({TypedActuatorId("Z", "Position (um)"):
        TypedActuatorPolicy("absolute-position", "um", 0, 100)})
    with pytest.raises(SafetyViolation, match="forbidden by safety config"):
        set_device_property(ctrl, guard, "Z", "Position (um)", "50")
    assert not hasattr(core, "last_write")


def test_core_focus_typed_bounds_may_not_widen_declared_axis():
    ranges = {ActuatorId("core_focus", None, "stage-position", "z"):
        RangePolicy(RangeEdge(0, None), RangeEdge(200, None))}
    policy = TypedActuatorPolicy("absolute-position", "um", 0, 5000)
    ctrl, parsed = _direct(LiveCore(upper=5000, focus="Z"), typed={
        TypedActuatorId("Z", "Position (um)"): policy},
        stage=StageConstraints(z_min=0, z_max=200), ranges=ranges)
    with pytest.raises(RigAuthorizationError, match=r"0\.\.5000 um widen.*0\.\.200 um"):
        validate_live_rig(ctrl, parsed)


def test_differently_named_typed_focus_write_still_uses_builtin_z_guard():
    core = LiveCore(upper=5000, focus="Z")
    guard = SafetyGuard(SafetyConstraints(
        stage=StageConstraints(z_min=0, z_max=200), allowed_properties=[]))
    guard.admit_typed_actuators({TypedActuatorId("Z", "Position (um)"):
        TypedActuatorPolicy("absolute-position", "um", 0, 5000)})
    with pytest.raises(SafetyViolation, match=r"Z=4000\.0.*maximum allowed \(200\.0"):
        guard.check_device_property(core, "Z", "Position (um)", "4000")


def test_rejected_config_does_not_install_typed_registry():
    policy = TypedActuatorPolicy("absolute-position", "um", 0, 250)
    ctrl, parsed = _direct(LiveCore(), typed={
        TypedActuatorId("Z", "Position (um)"): policy})
    guard = SafetyGuard(parsed.constraints)
    with pytest.raises(RigAuthorizationError, match="driver-reported technical range"):
        validate_live_rig(ctrl, parsed, guard)
    assert guard._typed_actuators == {}


def test_percent_power_is_refused_when_driver_range_is_not_percent():
    illumination = IlluminationConstraints(
        power_properties=[TypedPowerProperty("Z", "Power (mW)", units="percent")],
        max_power_percent=100, max_power_step_factor=2)
    ctrl, parsed = _direct(LiveCore("GenericDevice", "Power (mW)", 75),
                           illumination=illumination)
    with pytest.raises(RigAuthorizationError, match=r"units: percent.*units: native.*75\.0"):
        validate_live_rig(ctrl, parsed)


@pytest.mark.parametrize("kind", ["GalvoDevice", "SignalIODevice"])
def test_numeric_actuating_device_property_cannot_be_categorical(kind):
    ctrl, parsed = _direct(LiveCore(kind, "Amplitude", 10), categorical={("Z", "Amplitude")})
    report = validate_live_rig(ctrl, parsed)
    assert "known continuous actuator" in report.diagnostics[0].message
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "Z", "Amplitude")


def test_m5_generic_laser_power_cannot_be_declared_categorical():
    core = LiveCore("GenericDevice", "Power (mW)", 75, prop_type="Float",
                    device="iBeamSmartCW-1")
    ctrl, parsed = _direct(core, categorical={("iBeamSmartCW-1", "Power (mW)")})
    report = validate_live_rig(ctrl, parsed)
    assert "iBeamSmartCW-1.Power (mW)" in report.diagnostics[0].message
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "iBeamSmartCW-1", "Power (mW)")


def test_m5_generic_pre_init_configuration_property_remains_declarable():
    core = LiveCore("GenericDevice", "Number of PWM", 5,
                    prop_type="Integer", pre_init=True, device="PWM")
    ctrl, parsed = _direct(core, categorical={("PWM", "Number of PWM")})
    report = validate_live_rig(ctrl, parsed)
    assert any(e.device == "PWM" and e.property == "Number of PWM"
               and e.classification == "reviewed_categorical_property"
               for e in report.entries)


def test_m5_ttl_state_false_positive_is_demoted_and_still_fail_closed():
    core = LiveCore("GenericDevice", "State0", 0,
                    prop_type="Integer", limits=False, device="TTL")
    ctrl, parsed = _direct(core, categorical={("TTL", "State0")})
    report = validate_live_rig(ctrl, parsed)
    assert "TTL.State0 is a known continuous actuator" in report.diagnostics[0].message
    assert "property_authorization.denied" in report.diagnostics[0].message
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "TTL", "State0")


def test_declared_categorical_introspection_failure_is_fatal_in_guaranteed_mode():
    core = LiveCore("GenericDevice", "Power (mW)", 75,
                    device="iBeamSmartCW-1")
    def fail_pre_init(device, prop):
        raise RuntimeError("pre-init read failed")
    core.is_property_pre_init = fail_pre_init
    ctrl, parsed = _direct(core, categorical={("iBeamSmartCW-1", "Power (mW)")})
    with pytest.raises(RigAuthorizationError) as exc:
        validate_live_rig(ctrl, parsed)
    assert "Could not introspect declared categorical property iBeamSmartCW-1.Power (mW)" in str(exc.value)
    assert "pre-init read failed" in str(exc.value)


def test_unmodified_m5_state_device_declarations_remain_categorical():
    declarations = {
        ("Thorlabs Filter Wheel", "Label"),
        ("Thorlabs Filter Wheel", "State"),
        ("Thorlabs Filter Wheel-1", "Label"),
        ("Thorlabs Filter Wheel-1", "State"),
        ("Thorlabs ELL6", "Label"),
        ("Thorlabs ELL6", "State"),
        ("iChrome-MLE-TCP", "Label"),
    }
    core = LiveCore("StateDevice", "unused")
    core.get_loaded_devices = lambda: sorted({device for device, _ in declarations})
    core.get_device_type = lambda device: "StateDevice"
    core.get_device_property_names = lambda device: [
        prop for declared_device, prop in declarations if declared_device == device
    ]
    ctrl, parsed = _direct(core, categorical=declarations)
    report = validate_live_rig(ctrl, parsed)
    admitted = {(e.device, e.property) for e in report.entries
                if e.classification == "reviewed_categorical_property"
                and e.source == "declared"}
    assert admitted == declarations


def test_loaded_device_inventory_is_reused_for_all_typed_entries():
    core = LiveCore()
    typed = {
        TypedActuatorId("Z", "Position (um)"): TypedActuatorPolicy(
            "absolute-position", "um", 0, 100),
        TypedActuatorId("Z", "Other"): TypedActuatorPolicy(
            "absolute-position", "um", 0, 100),
    }
    ctrl, parsed = _direct(core, typed=typed)
    with pytest.raises(RigAuthorizationError, match="Other.*does not exist"):
        validate_live_rig(ctrl, parsed)
    assert core.loaded_calls == 1


def test_property_info_reports_the_declared_bound_not_only_the_driver_range(tmp_path):
    """Block 4b's G1 showed the agent advising from the driver range alone.

    A reviewed bound exists precisely so it can be tighter than the hardware's.
    If introspection reports only `lower_limit`/`upper_limit`, a caller plans
    against authority the guard will refuse.
    """
    from microclaw.tools import get_device_property_info

    parsed = _yaml(tmp_path,
        "  allowed_numeric:\n"
        "    - {device: Camera, property: Gain, kind: bounded-numeric, units: native, minimum: 0, maximum: 4}\n")
    guard = SafetyGuard(parsed.constraints)
    guard.admit_typed_actuators(parsed.property_authorization.allowed_numeric)

    core = SimpleNamespace(
        is_property_read_only=lambda d, p: False,
        is_property_pre_init=lambda d, p: False,
        get_property_type=lambda d, p: "Float",
        get_allowed_property_values=lambda d, p: SimpleNamespace(size=lambda: 0),
        has_property_limits=lambda d, p: True,
        get_property_lower_limit=lambda d, p: -5.0,
        get_property_upper_limit=lambda d, p: 8.0,
        get_property=lambda d, p: "0",
    )
    ctrl = SimpleNamespace(core=core)

    info = get_device_property_info(ctrl, guard, "Camera", "Gain")
    assert info["declared_policy"] == {
        "kind": "bounded-numeric", "units": "native",
        "minimum": 0.0, "maximum": 4.0,
    }
    # The driver range is still reported, and is deliberately wider here.
    assert (info["lower_limit"], info["upper_limit"]) == (-5.0, 8.0)

    assert "declared_policy" not in get_device_property_info(
        ctrl, guard, "Camera", "Binning"
    )
