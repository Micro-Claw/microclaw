import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from microclaw.authorization import (
    AuthorizationEntry,
    AuthorizationMap,
    RigAuthorizationError,
    authorize_path,
    authorize_channel,
    authorize_property_write,
    validate_live_rig,
)
from microclaw.safety import (
    AcquisitionConstraints,
    ActuatorId,
    CameraConstraints,
    ForbiddenProperty,
    IlluminationConstraints,
    IlluminationProperty,
    ParsedSafetyConfig,
    PluginConstraints,
    RangeEdge,
    RangePolicy,
    PropertyAuthorization,
    SafetyConstraints,
    SafetyGuard,
    SafetyViolation,
)


@pytest.fixture(autouse=True)
def isolate_authorization_emu_locator(tmp_path, monkeypatch):
    from microclaw import emu_manager

    cache_dir = tmp_path / ".microclaw"
    monkeypatch.setattr(emu_manager, "_MICROCLAW_DIR", cache_dir)
    monkeypatch.setattr(emu_manager, "_EMU_CACHE", cache_dir / "emu.json")
    monkeypatch.setattr(emu_manager, "_candidate_mm_dirs", lambda: [])


class Core:
    def __init__(self):
        self.xy = "XY"
        self.focus = "Z"
        self.camera = "Cam"
        self.presets = {}
        self.preset_errors = {}
        self.loaded_extra = []
        # Live device typing for the design/33 StateDevice auto-classification.
        # Default GenericDevice: nothing auto-classifies unless a test says so.
        self.shutter = ""
        self.device_types = {}
        self.device_properties = {}

    def _raise_or_return(self, value):
        if isinstance(value, Exception):
            raise value
        return value

    def get_shutter_device(self):
        return self._raise_or_return(self.shutter)

    def get_device_type(self, label):
        return self._raise_or_return(self.device_types.get(label, "GenericDevice"))

    def get_device_property_names(self, label):
        return self._raise_or_return(
            self.device_properties.get(label, ["Label", "State"])
        )

    def is_property_pre_init(self, device, prop):
        return False

    def is_property_read_only(self, device, prop):
        return False

    def get_allowed_property_values(self, device, prop):
        return ["0", "1"]

    def get_xy_stage_device(self):
        return self.xy

    def get_focus_device(self):
        return self.focus

    def get_camera_device(self):
        return self.camera

    def get_available_configs(self, group):
        assert group == "Channel"
        return list(self.presets)

    def get_available_config_groups(self):
        return ["Channel"]

    def get_loaded_devices(self):
        return [
            self.xy, self.focus, self.camera, "ReadOnlySensor", "SecondZ",
            *self.loaded_extra,
        ]

    def get_config_data(self, group, preset):
        assert group == "Channel"
        if preset in self.preset_errors:
            raise self.preset_errors[preset]
        return self.presets[preset]

    def get_property(self, device, prop):
        return "5.0"


class Controller:
    def __init__(self, core=None):
        self.core = core or Core()

    def is_connected(self):
        return True

def edge(value):
    return RangeEdge(value, None if value is not None else "reviewed open edge")


def policy(low=-10.0, high=10.0):
    return RangePolicy(edge(low), edge(high))


def parsed(
    *, ranges=None, categorical=(), excluded=(), channels=(), mode="guaranteed",
    plugin_motion=False, exposure=100.0, illumination=None, forbidden=(),
    acquisition=None,
):
    if ranges is None:
        ranges = {
            ActuatorId("core_xy", None, "stage-position", "x"): policy(),
            ActuatorId("core_xy", None, "stage-position", "y"): policy(),
            ActuatorId("core_focus", None, "stage-position", "z"): policy(0, 100),
        }
    constraints = SafetyConstraints(
        camera=CameraConstraints(exposure),
        acquisition=acquisition or AcquisitionConstraints(
            max_frames=10000,
            max_duration_s=3600,
            max_bytes=50_000_000_000,
            max_illuminated_ms=600_000,
            max_session_illuminated_ms=1_800_000,
            confirm_above_frames=500,
            confirm_above_duration_s=300,
            confirm_above_bytes=5_000_000_000,
            confirm_above_illuminated_ms=60_000,
        ),
        allowed_channels=list(channels),
        allowed_properties=[
            ForbiddenProperty(device, prop) for device, prop in categorical
        ],
        forbidden_properties=[
            ForbiddenProperty(device, prop) for device, prop in forbidden
        ],
        plugins=PluginConstraints(allow_hardware_motion=plugin_motion),
        illumination=illumination or IlluminationConstraints(),
    )
    return ParsedSafetyConfig(
        constraints,
        ranges,
        PropertyAuthorization(mode, frozenset(categorical), denied=frozenset(excluded)),
        frozenset({"stage", "camera", "acquisition", "channels", "plugins",
                   "illumination", "property_authorization"}),
    )


def test_complete_map_covers_dedicated_autofocus_and_acquisition_paths():
    report = validate_live_rig(Controller(), parsed())
    assert report.complete is True
    paths = {entry.path for entry in report.entries}
    assert {"dedicated-stage", "dedicated-exposure", "autofocus-and-acquisition"} <= paths
    assert not any(
        entry.path == "acquisition" and entry.capability == "exposure"
        for entry in report.entries
    )
    assert {
        entry.path for entry in report.entries
        if entry.capability == "acquisition-dose"
        and entry.path.startswith("acquisition-tool:")
    } == {
        "acquisition-tool:run_zstack",
        "acquisition-tool:run_timelapse",
        "acquisition-tool:run_multiposition_acquisition",
        "acquisition-tool:run_tile_acquisition",
        "acquisition-tool:run_multiposition_with_autofocus",
        "acquisition-tool:run_zstack",
        "acquisition-tool:run_timelapse",
        "acquisition-tool:run_adaptive_survey",
    }
    inventory = {
        entry.device for entry in report.entries
        if entry.path == "connected-device-inventory"
    }
    assert inventory == {"ReadOnlySensor", "SecondZ"}


@pytest.mark.parametrize(
    "missing",
    ["confirm_above_frames", "confirm_above_duration_s"],
)
def test_missing_or_partial_direct_dose_policy_fails_closed(missing):
    values = {
        "max_frames": 10000,
        "max_duration_s": 3600,
        "max_bytes": 50_000_000_000,
        "max_illuminated_ms": 600_000,
        "max_session_illuminated_ms": 1_800_000,
        "confirm_above_frames": 500,
        "confirm_above_duration_s": 300,
        "confirm_above_bytes": 5_000_000_000,
        "confirm_above_illuminated_ms": 60_000,
    }
    values[missing] = None
    with pytest.raises(RigAuthorizationError, match=rf"acquisition\.{missing}"):
        validate_live_rig(
            Controller(), parsed(acquisition=AcquisitionConstraints(**values))
        )


def test_optional_dose_caps_are_unrestricted():
    report = validate_live_rig(
        Controller(),
        parsed(
            mode="degraded_trusted_plugins",
            acquisition=AcquisitionConstraints(
                confirm_above_frames=500, confirm_above_duration_s=300
            ),
        ),
    )
    assert report.complete is None
    assert "suspended" in report.verdict
    assert any(
        entry.path == "acquisition-policy:max_duration_s"
        and entry.classification == "unrestricted"
        for entry in report.entries
    )
    tool_entries = [
        entry for entry in report.entries
        if entry.path.startswith("acquisition-tool:")
    ]
    assert len(tool_entries) == 6
    assert {entry.classification for entry in tool_entries} == {
        "built_in_typed_capability"
    }


def test_acquisition_report_names_all_policies_and_honest_session_scope():
    report = validate_live_rig(Controller(), parsed())
    policies = {
        entry.path: entry.detail for entry in report.entries
        if entry.path.startswith("acquisition-policy:")
    }
    assert len(policies) == 7
    assert "acquisition-policy:max_session_illuminated_ms" not in policies
    assert "acquisition-policy:confirm_above_bytes" not in policies


def test_mda_remains_excluded_and_is_not_admitted_by_dose_policy():
    report = validate_live_rig(Controller(), parsed())
    mda = [entry for entry in report.entries if entry.path == "mmstudio-mda"]
    assert len(mda) == 1 and mda[0].classification == "excluded"
    assert not any(entry.path == "acquisition-tool:run_mda" for entry in report.entries)


def test_camera_roi_is_a_built_in_geometry_capability_beside_exposure():
    report = validate_live_rig(Controller(), parsed())
    roi = [entry for entry in report.entries if entry.path == "camera-roi"]
    assert len(roi) == 1
    assert roi[0].classification == "built_in_typed_capability"
    assert roi[0].capability == "camera-roi"
    assert roi[0].device == "Cam"
    assert "dose" not in (roi[0].detail or "")


def test_code_level_exclusion_says_config_cannot_repair_it():
    ctrl = Controller()
    ctrl.authorization_map = AuthorizationMap(
        mode="guaranteed", verdict="complete", complete=True,
        entries=[AuthorizationEntry(
            path="mmstudio-mda", classification="excluded",
            detail="GUI-owned effects cannot be enumerated",
        )],
    )
    with pytest.raises(RigAuthorizationError) as caught:
        authorize_path(ctrl, "mmstudio-mda")
    message = str(caught.value)
    assert "changing the rig's safety config cannot permit it" in message
    assert "GUI-owned effects cannot be enumerated" in message


def test_missing_camera_roi_entry_names_absent_camera():
    core = Core()
    core.camera = ""
    ctrl = Controller(core)
    ctrl.authorization_map = AuthorizationMap(
        mode="guaranteed", verdict="complete", complete=True,
    )
    with pytest.raises(RigAuthorizationError) as caught:
        authorize_path(ctrl, "camera-roi")
    message = str(caught.value)
    assert "Micro-Manager has no camera configured" in message
    assert "completeness error" not in message


@pytest.mark.parametrize(
    "ranges",
    [
        {},
        {
            ActuatorId("core_xy", None, "stage-position", "x"): policy(),
            ActuatorId("core_xy", None, "stage-position", "y"): policy(),
        },
    ],
)
def test_reachable_core_axes_without_ranges_fail_closed(ranges):
    with pytest.raises(RigAuthorizationError, match="no declared range policy"):
        validate_live_rig(Controller(), parsed(ranges=ranges))


def test_open_reachable_edge_always_fails():
    ranges = parsed().ranges.copy()
    ranges[ActuatorId("core_focus", None, "stage-position", "z")] = policy(0, None)
    with pytest.raises(RigAuthorizationError, match="open range edge"):
        validate_live_rig(Controller(), parsed(ranges=ranges))
    with pytest.raises(RigAuthorizationError, match="open range edge"):
        validate_live_rig(
            Controller(), parsed(ranges=ranges, mode="degraded_trusted_plugins")
        )


def test_live_core_and_named_identity_conflict_is_rejected():
    ranges = parsed().ranges.copy()
    ranges[ActuatorId("named", "Z", "stage-position", None)] = policy()
    with pytest.raises(RigAuthorizationError, match="Core/named"):
        validate_live_rig(Controller(), parsed(ranges=ranges))


def test_known_continuous_raw_property_is_demoted_and_write_stays_refused(capsys):
    core = Core()
    core.device_properties["Z"] = ["Position"]
    ctrl = Controller(core)
    config = parsed(categorical={("Z", "Position")})
    guard = SafetyGuard(config.constraints)
    guard.check_property("Z", "Position")  # admitted by parsed declaration
    report = validate_live_rig(
        ctrl, config, guard=guard
    )
    assert report.complete is True
    assert [(item.kind, item.blocking) for item in report.diagnostics] == [
        ("live_check", False)
    ]
    message = report.diagnostics[0].message
    assert "Z.Position is a known continuous actuator" in message
    assert "property_authorization.allowed_numeric" in message
    assert "property_authorization.denied" in message
    assert any(
        entry.device == "Z" and entry.property == "Position"
        and entry.classification == "excluded"
        for entry in report.entries
    )
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "Z", "Position")
    with pytest.raises(SafetyViolation, match="demoted by live authorization"):
        guard.check_property("Z", "Position")
    stderr = capsys.readouterr().err
    assert "AUTHORIZATION CLAIMS DEMOTED" in stderr
    assert "Z.Position" in stderr


def test_preset_path_still_catches_device_when_generic_path_is_not_allowed():
    core = Core()
    core.presets["Unsafe"] = [
        {"device": "SecondZ", "property": "Position", "value": "50"}
    ]
    with pytest.raises(RigAuthorizationError, match="SecondZ.Position is unclassified"):
        validate_live_rig(Controller(core), parsed(channels=["Unsafe"]))


def test_missing_allowed_presets_are_demoted_and_not_authorized(capsys):
    core = Core()
    core.presets["DAPI"] = []
    ctrl = Controller(core)
    report = validate_live_rig(
        ctrl, parsed(channels=["DAPI", "TRITC", "Brightfield"]),
    )
    message = report.diagnostics[0].message
    assert (
        'channels.allowed lists preset(s) not present in the "Channel" group: '
        "'TRITC', 'Brightfield'."
    ) in message
    assert "mmcorej" not in message
    assert "java.lang" not in message
    assert report.authorized_presets == {"DAPI"}
    assert set(report.excluded_presets) == {"TRITC", "Brightfield"}
    for preset in ("TRITC", "Brightfield"):
        with pytest.raises(RigAuthorizationError, match="absent") as exc:
            authorize_channel(ctrl, preset)
        assert "property_authorization.allowed_categorical" in str(exc.value)
        assert "property_authorization.allowed_numeric" in str(exc.value)
    assert "AUTHORIZATION CLAIMS DEMOTED" in capsys.readouterr().err


def test_missing_allowed_presets_on_rig_without_channel_group_get_honest_advice():
    # The source is chosen by whether the group holds a preset, not by whether
    # the group is listed: Core here has none, and no other channel source.
    core = Core()
    report = validate_live_rig(Controller(core), parsed(channels=["MissingPreset"]))
    message = report.diagnostics[0].message
    assert "has no Micro-Manager Channel group" in message
    assert "add each preset" not in message.lower()
    assert "explicit empty list" in message
    assert "has no Micro-Manager Channel group" in report.excluded_presets["MissingPreset"][0]


def test_present_but_unreadable_preset_has_one_sanitized_reason():
    core = Core()
    core.presets["Broken"] = []
    core.preset_errors["Broken"] = RuntimeError(
        "java.lang.RuntimeException: adapter refused preset\n"
        "\tat mmcorej.CMMCore.getConfigData(CMMCore.java:123)"
    )
    with pytest.raises(RigAuthorizationError) as exc:
        validate_live_rig(Controller(core), parsed(channels=["Broken"]))
    message = str(exc.value)
    assert "RuntimeException: adapter refused preset" in message
    assert "CMMCore.java" not in message
    assert "mmcorej" not in message
    assert "java.lang" not in message


def test_fully_reviewed_categorical_preset_is_authorized_and_runtime_gated():
    core = Core()
    core.loaded_extra = ["Wheel"]
    core.presets["DAPI"] = [
        {"device": "Wheel", "property": "Label", "value": "DAPI"}
    ]
    ctrl = Controller(core)
    validate_live_rig(
        ctrl,
        parsed(channels=["DAPI"], categorical={("Wheel", "Label")}),
    )
    authorize_channel(ctrl, "DAPI")
    authorize_property_write(ctrl, "Wheel", "Label")
    with pytest.raises(RigAuthorizationError, match="excluded") as exc:
        authorize_property_write(ctrl, "Wheel", "Speed")
    assert "property_authorization.allowed_categorical" in str(exc.value)
    assert "property_authorization.allowed_numeric" in str(exc.value)


def test_excluded_preset_effect_fails_and_is_not_runtime_authorized():
    core = Core()
    core.presets["DAPI"] = [
        {"device": "Laser", "property": "Enable", "value": "1"}
    ]
    ctrl = Controller(core)
    with pytest.raises(RigAuthorizationError, match="Laser.Enable is excluded"):
        validate_live_rig(
            ctrl,
            parsed(channels=["DAPI"], excluded={("Laser", "Enable")}),
        )


def test_opaque_motion_plugin_is_unrestricted_when_enabled():
    report = validate_live_rig(Controller(), parsed(plugin_motion=True))
    assert any(entry.classification == "trusted_degraded" for entry in report.entries)


def test_minimal_document_authorization_leaves_properties_and_channels_unrestricted():
    minimal = parsed(mode="degraded_trusted_plugins")
    minimal = ParsedSafetyConfig(
        minimal.constraints,
        minimal.ranges,
        minimal.property_authorization,
        frozenset({"schema_version", "reviewed", "stage", "acquisition"}),
    )
    ctrl = Controller()
    validate_live_rig(ctrl, minimal)
    authorize_property_write(ctrl, "OldLaser", "Enable")
    authorize_channel(ctrl, "Any channel")


def test_motion_plugin_no_longer_requires_a_second_setting():
    # The M5 session (2026-08-04): the operator set allow_hardware_motion, was
    # refused at startup with a rule and no remedy, and had no way to learn from
    # the message that a second line was required. A refusal that does not say
    # what to do next costs a restart per guess, with the rig connected.
    report = validate_live_rig(Controller(), parsed(plugin_motion=True))
    assert any(entry.path == "opaque-hardware-motion-plugin" for entry in report.entries)


def illumination_policy(*, maximum=30.0, step=3.0):
    return IlluminationConstraints(
        shutters=[
            IlluminationProperty("Laser", "Enable", on_value="On", off_value="Off")
        ],
        power_properties=[ForbiddenProperty("Laser", "Power")],
        max_power_percent=maximum,
        max_power_step_factor=step,
    )


def emu_controller(tmp_path, raw_properties):
    """Representative off-rig live-installation fixture (demo has no EMU)."""
    mm_dir = tmp_path / "Micro-Manager-2.0"
    config_dir = mm_dir / "EMU"
    config_dir.mkdir(parents=True)
    (mm_dir / "plugins").mkdir()
    (config_dir / "config.uicfg").write_text(json.dumps({
        "defaultConfigurationName": "test",
        "pluginConfigurations": [{
            "configurationName": "test",
            "pluginName": "htSMLM",
            "properties": raw_properties,
        }],
    }), encoding="utf-8")
    core = Core()
    ctrl = Controller(core)
    ctrl.get_mm_app_dir = lambda: str(mm_dir)
    return ctrl


def configure_emu_fallback(monkeypatch, tmp_path, raw_properties):
    from microclaw import emu_manager

    fallback = emu_controller(tmp_path / "fallback", raw_properties).get_mm_app_dir()
    cache = tmp_path / "emu-cache.json"
    cache.write_text(json.dumps({"mm_app_dir": fallback}), encoding="utf-8")
    monkeypatch.setattr(emu_manager, "_EMU_CACHE", cache)
    monkeypatch.setattr(emu_manager, "_candidate_mm_dirs", lambda: [])
    return fallback


def test_guaranteed_refuses_undeclared_semantic_emu_enable_actionably(tmp_path):
    ctrl = emu_controller(tmp_path, {
        "Laser 2 enable": "Luxx638-Laser Operation Select",
    })
    ctrl.core.loaded_extra = ["Luxx638"]

    with pytest.raises(RigAuthorizationError) as caught:
        validate_live_rig(ctrl, parsed())

    message = str(caught.value)
    assert "Luxx638" in message
    assert "Laser Operation Select" in message
    assert "constraints.illumination.shutters" in message
    assert "illumination:\n    shutters:" in message
    assert "no values were inferred" in message


def test_exact_declared_emu_enable_uses_existing_illumination_protections(tmp_path):
    ctrl = emu_controller(tmp_path, {
        "Laser 2 enable": "Luxx638-Laser Operation Select",
        "Laser 2 enable - On value": "Armed",
        "Laser 2 enable - Off value": "Safe",
    })
    ctrl.core.loaded_extra = ["Luxx638"]
    illumination = IlluminationConstraints(shutters=[IlluminationProperty(
        "Luxx638", "Laser Operation Select", on_value="Armed", off_value="Safe"
    )])
    config = parsed(illumination=illumination)
    guard = SafetyGuard(config.constraints)

    report = validate_live_rig(ctrl, config, guard=guard)
    assert report.complete is True
    entry = next(e for e in report.entries if e.device == "Luxx638"
                 and e.property == "Laser Operation Select")
    assert entry.path == "dedicated-illumination"
    with pytest.raises(SafetyViolation, match="declined"):
        guard.check_illumination(
            ctrl.core, "Luxx638", "Laser Operation Select", "Armed"
        )
    ctrl.core.set_property = lambda device, prop, value: setattr(
        ctrl.core, "last_write", (device, prop, value)
    )
    assert guard.shutter_all(ctrl.core) == ["Luxx638.Laser Operation Select"]
    assert ctrl.core.last_write == ("Luxx638", "Laser Operation Select", "Safe")


def test_degraded_warns_without_authorizing_undeclared_emu_enable(tmp_path, capsys):
    ctrl = emu_controller(tmp_path, {"Laser 0 enable": "Laser-A-Enable"})
    ctrl.core.loaded_extra = ["Laser-A"]
    report = validate_live_rig(
        ctrl, parsed(mode="degraded_trusted_plugins")
    )
    warning = capsys.readouterr().err
    assert report.complete is None
    assert "Laser-A.Enable" in warning
    assert "constraints.illumination.shutters" in warning
    assert "Completeness guarantee is suspended" in warning
    assert not any(e.device == "Laser-A" and e.property == "Enable"
                   and e.path == "dedicated-illumination" for e in report.entries)


def test_every_semantic_emu_laser_slot_is_checked(tmp_path):
    ctrl = emu_controller(tmp_path, {
        "Laser 0 enable": "Laser-A-Enable",
        "Laser 3 enable": "Laser-B-Gate",
    })
    ctrl.core.loaded_extra = ["Laser-A", "Laser-B"]
    with pytest.raises(RigAuthorizationError) as caught:
        validate_live_rig(ctrl, parsed(illumination=IlluminationConstraints(
            shutters=[IlluminationProperty("Laser-A", "Enable")]
        )))
    message = str(caught.value)
    assert "Laser-B.Gate" in message
    assert "Laser-A.Enable" not in message


def test_unresolved_semantic_emu_enable_fails_without_false_pair_claim(tmp_path):
    ctrl = emu_controller(tmp_path, {
        "Laser 1 enable": "Unknown-Laser-Enable",
    })
    with pytest.raises(RigAuthorizationError) as caught:
        validate_live_rig(ctrl, parsed())
    message = str(caught.value)
    assert "'Laser 1 enable' is allocated" in message
    assert "could not be resolved" in message
    assert "Unknown.Laser-Enable" not in message


def test_malformed_emu_config_fails_safely_in_guaranteed_mode(tmp_path):
    ctrl = emu_controller(tmp_path, {})
    config_path = tmp_path / "Micro-Manager-2.0" / "EMU" / "config.uicfg"
    config_path.write_text("not json", encoding="utf-8")
    with pytest.raises(RigAuthorizationError, match="Could not resolve EMU semantic"):
        validate_live_rig(ctrl, parsed())


def test_none_live_probe_checks_fallback_emu_and_fails_on_provenance(
    tmp_path, monkeypatch
):
    configure_emu_fallback(
        monkeypatch, tmp_path, {"Laser 4 enable": "Laser-A-Enable"}
    )
    ctrl = Controller()
    ctrl.get_mm_app_dir = lambda: None
    ctrl.core.loaded_extra = ["Laser-A"]
    with pytest.raises(RigAuthorizationError) as caught:
        validate_live_rig(ctrl, parsed())
    message = str(caught.value)
    assert "Laser-A.Enable" in message
    assert "cache fallback" in message
    assert "cannot prove it belongs to the connected JVM" in message


def test_bogus_live_probe_checks_fallback_emu(tmp_path, monkeypatch):
    configure_emu_fallback(
        monkeypatch, tmp_path, {"Laser 1 enable": "Laser-B-Gate"}
    )
    bogus = tmp_path / "not-mm"
    bogus.mkdir()
    ctrl = Controller()
    ctrl.get_mm_app_dir = lambda: str(bogus)
    ctrl.core.loaded_extra = ["Laser-B"]
    with pytest.raises(RigAuthorizationError) as caught:
        validate_live_rig(ctrl, parsed())
    message = str(caught.value)
    assert "Laser-B.Gate" in message
    assert "live Micro-Manager path was invalid" in message


def test_validated_live_non_emu_installation_is_accepted(tmp_path):
    mm_dir = tmp_path / "Micro-Manager-Demo"
    (mm_dir / "plugins").mkdir(parents=True)
    (mm_dir / "plugins" / "Emu.jar").write_text("", encoding="utf-8")
    ctrl = Controller()
    ctrl.get_mm_app_dir = lambda: str(mm_dir)
    report = validate_live_rig(ctrl, parsed())
    assert report.complete is True
    assert report.diagnostics == ()


def test_round_1_demo_shape_starts_with_all_claims_demoted(
    tmp_path, capsys
):
    core = Core()
    core.loaded_extra = ["Camera"]
    core.camera = "Camera"
    continuous = {
        ("Camera", "Exposure"), ("Camera", "Gain"), ("Camera", "Offset"),
        ("Camera", "ReadoutTime"), ("Camera", "StripeWidth"),
        *(("Camera", f"TestProperty{index}") for index in range(1, 7)),
        ("XY", "Velocity"), ("Z", "Position"),
    }
    core.device_properties = {
        "Camera": sorted(prop for device, prop in continuous if device == "Camera"),
        "XY": sorted(prop for device, prop in continuous if device == "XY"),
        "Z": sorted(prop for device, prop in continuous if device == "Z"),
    }
    core.get_allowed_property_values = lambda device, prop: []
    core.get_property_type = lambda device, prop: "Float"
    core.has_property_limits = lambda device, prop: (device, prop) in continuous
    core.get_property_lower_limit = lambda device, prop: 0.0
    core.get_property_upper_limit = lambda device, prop: 100.0
    ctrl = Controller(core)
    mm_dir = tmp_path / "Micro-Manager-Demo"
    (mm_dir / "plugins").mkdir(parents=True)
    (mm_dir / "plugins" / "Emu.jar").write_text("", encoding="utf-8")
    ctrl.get_mm_app_dir = lambda: str(mm_dir)
    config = parsed(
        categorical=continuous,
        channels=["10X", "Camera-left", "HighRes"],
    )
    guard = SafetyGuard(config.constraints)

    report = validate_live_rig(ctrl, config, guard=guard)

    assert report.complete is True
    continuous_diagnostics = [
        item for item in report.diagnostics
        if "is a known continuous actuator" in item.message
    ]
    assert len(continuous_diagnostics) == len(continuous)
    assert len(report.diagnostics) == len(continuous) + 1
    assert all(not item.blocking for item in report.diagnostics)
    for device, prop in continuous:
        assert any(
            f"{device}.{prop} is a known continuous actuator" in item.message
            for item in continuous_diagnostics
        )
        assert any(
            entry.device == device and entry.property == prop
            and entry.classification == "excluded"
            for entry in report.entries
        )
        with pytest.raises(RigAuthorizationError, match="excluded"):
            authorize_property_write(ctrl, device, prop)
        with pytest.raises(SafetyViolation, match="demoted by live authorization"):
            guard.check_property(device, prop)
    assert report.authorized_presets == set()
    assert set(report.excluded_presets) == {"10X", "Camera-left", "HighRes"}
    assert any(
        'channels.allowed lists preset(s) not present in the "Channel" group'
        in item.message for item in report.diagnostics
    )
    stderr = capsys.readouterr().err
    assert all(
        f"{device}.{prop} is a known continuous actuator" in stderr
        for device, prop in continuous
    )
    assert "EMU" not in stderr


def test_declared_unloaded_device_demotes_via_device_absence(capsys):
    ctrl = Controller()
    report = validate_live_rig(
        ctrl, parsed(categorical={("NotLoaded", "Mode")})
    )
    assert report.complete is True
    assert len(report.diagnostics) == 1
    assert "declared device 'NotLoaded' is not loaded" in report.diagnostics[0].message
    with pytest.raises(RigAuthorizationError, match="excluded") as exc:
        authorize_property_write(ctrl, "NotLoaded", "Mode")
    assert "property_authorization.allowed_categorical" in str(exc.value)
    assert "declared device 'NotLoaded' is not loaded" in capsys.readouterr().err


def test_declared_absent_property_demotes_via_property_absence(capsys):
    core = Core()
    core.loaded_extra = ["Selector"]
    core.device_properties["Selector"] = ["State"]
    ctrl = Controller(core)
    config = parsed(categorical={("Selector", "Mode")})
    guard = SafetyGuard(config.constraints)
    report = validate_live_rig(ctrl, config, guard=guard)
    assert report.complete is True
    assert len(report.diagnostics) == 1
    assert "property Selector.Mode is not present" in report.diagnostics[0].message
    assert "property_authorization.allowed_categorical" in report.diagnostics[0].message
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "Selector", "Mode")
    with pytest.raises(SafetyViolation, match="demoted by live authorization"):
        guard.check_property("Selector", "Mode")
    assert "property Selector.Mode is not present" in capsys.readouterr().err


def test_absent_excluded_property_demotes_via_property_absence(capsys):
    ctrl = Controller()
    report = validate_live_rig(
        ctrl, parsed(excluded={("ReadOnlySensor", "NotAProperty")})
    )
    assert report.complete is True
    assert len(report.diagnostics) == 1
    assert "property ReadOnlySensor.NotAProperty is not present" in (
        report.diagnostics[0].message
    )
    assert "property_authorization.denied" in report.diagnostics[0].message
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "ReadOnlySensor", "NotAProperty")
    assert "property ReadOnlySensor.NotAProperty is not present" in (
        capsys.readouterr().err
    )


def test_guaranteed_mode_introspection_failure_still_refuses_process():
    core = Core()
    core.loaded_extra = ["Selector"]
    core.device_properties["Selector"] = ["Mode"]
    core.get_allowed_property_values = lambda device, prop: (_ for _ in ()).throw(
        RuntimeError("inventory unavailable")
    )
    with pytest.raises(RigAuthorizationError) as caught:
        validate_live_rig(
            Controller(core), parsed(categorical={("Selector", "Mode")})
        )
    assert "Could not introspect" in str(caught.value)
    assert any(item.blocking for item in caught.value.diagnostics)


def test_discovery_uncertainty_fails_closed_in_guaranteed_mode(
    tmp_path, monkeypatch
):
    from microclaw import emu_manager

    monkeypatch.setattr(emu_manager, "_EMU_CACHE", tmp_path / "missing-cache")
    monkeypatch.setattr(emu_manager, "_candidate_mm_dirs", lambda: [])
    ctrl = Controller()
    ctrl.get_mm_app_dir = lambda: None
    with pytest.raises(RigAuthorizationError, match="Could not establish the live"):
        validate_live_rig(ctrl, parsed())


def test_discovery_uncertainty_warns_in_degraded_mode(
    tmp_path, monkeypatch, capsys
):
    from microclaw import emu_manager

    monkeypatch.setattr(emu_manager, "_EMU_CACHE", tmp_path / "missing-cache")
    monkeypatch.setattr(emu_manager, "_candidate_mm_dirs", lambda: [])
    ctrl = Controller()
    ctrl.get_mm_app_dir = lambda: None
    report = validate_live_rig(ctrl, parsed(mode="degraded_trusted_plugins"))
    warning = capsys.readouterr().err
    assert report.complete is None
    assert "Could not establish the live Micro-Manager installation" in warning
    assert "Completeness guarantee is suspended" in warning


def test_stale_fallback_cannot_override_different_valid_live_installation(
    tmp_path, monkeypatch
):
    configure_emu_fallback(
        monkeypatch, tmp_path, {"Laser 0 enable": "Stale-Laser-Enable"}
    )
    live = tmp_path / "current" / "Micro-Manager-Demo"
    (live / "plugins").mkdir(parents=True)
    ctrl = Controller()
    ctrl.get_mm_app_dir = lambda: str(live)
    report = validate_live_rig(ctrl, parsed())
    assert report.complete is True
    assert not any(entry.device == "Stale-Laser" for entry in report.entries)


def test_structurally_malformed_emu_config_cannot_look_like_empty_map(tmp_path):
    ctrl = emu_controller(tmp_path, {})
    config_path = tmp_path / "Micro-Manager-2.0" / "EMU" / "config.uicfg"
    config_path.write_text(json.dumps({
        "defaultConfigurationName": "broken",
        "pluginConfigurations": [{
            "configurationName": "broken",
            "pluginName": "htSMLM",
            "properties": [],
        }],
    }), encoding="utf-8")
    with pytest.raises(RigAuthorizationError, match="properties must be a mapping"):
        validate_live_rig(ctrl, parsed())


def test_valid_emu_config_with_no_laser_enables_is_accepted(tmp_path):
    ctrl = emu_controller(tmp_path, {"Filter wheel position": "Wheel-State"})
    ctrl.core.loaded_extra = ["Wheel"]
    assert validate_live_rig(ctrl, parsed()).complete is True


@pytest.mark.parametrize(
    ("maximum", "step", "message"),
    [
        (None, 3.0, "max_power_percent"),
        (30.0, None, "max_power_step_factor"),
        (101.0, 3.0, "within 0..100"),
        (30.0, 0.5, "at least 1"),
    ],
)
def test_reachable_illumination_power_requires_complete_policy(
    maximum, step, message
):
    with pytest.raises(RigAuthorizationError, match=message):
        validate_live_rig(
            Controller(),
            parsed(illumination=illumination_policy(maximum=maximum, step=step)),
        )


def test_bounded_shutter_and_power_writes_pass_map_and_typed_guard():
    ctrl = Controller()
    ctrl.core.loaded_extra = ["Laser"]
    config = parsed(illumination=illumination_policy())
    report = validate_live_rig(ctrl, config)
    illumination_entries = {
        (entry.device, entry.property)
        for entry in report.entries
        if entry.path == "dedicated-illumination"
        and entry.classification == "built_in_typed_capability"
    }
    assert illumination_entries == {("Laser", "Enable"), ("Laser", "Power")}
    assert not any(
        entry.path == "connected-device-inventory" and entry.device == "Laser"
        for entry in report.entries
    )

    guard = SafetyGuard(config.constraints)
    authorize_property_write(ctrl, "Laser", "Enable")
    guard.check_device_property(ctrl.core, "Laser", "Enable", "On")
    guard.check_illumination(
        ctrl.core, "Laser", "Enable", "On",
            confirm_fn=lambda summary, kind="action", subject=None: (
                kind == "illumination" and subject == "enable"
            ),
    )
    authorize_property_write(ctrl, "Laser", "Power")
    guard.check_device_property(ctrl.core, "Laser", "Power", "10")
    guard.check_illumination(ctrl.core, "Laser", "Power", "10")


def test_unclassified_and_over_limit_illumination_writes_fail():
    ctrl = Controller()
    config = parsed(illumination=illumination_policy())
    validate_live_rig(ctrl, config)
    guard = SafetyGuard(config.constraints)
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "Laser", "UnknownPower")
    authorize_property_write(ctrl, "Laser", "Power")
    with pytest.raises(SafetyViolation, match="max_power_percent"):
        guard.check_illumination(ctrl.core, "Laser", "Power", "50")


def test_illumination_preset_is_authorized_for_channel_plan_executor():
    core = Core()
    core.presets["LaserOn"] = [
        {"device": "Laser", "property": "Enable", "value": "On"}
    ]
    report = validate_live_rig(
        Controller(core),
        parsed(channels=["LaserOn"], illumination=illumination_policy()),
    )
    assert report.authorized_presets == {"LaserOn"}
    assert report.channel_expansion_hashes["LaserOn"]


def test_cli_and_web_validate_before_prompt_or_session_exposure(monkeypatch):
    from microclaw import __main__ as cli
    from microclaw import webserve
    from microclaw import credentials

    incomplete = parsed(ranges={})
    prompted = []
    monkeypatch.setattr(cli, "load_safety_config_or_exit", lambda path: incomplete)
    monkeypatch.setattr(cli, "MicroscopeController", lambda port, guard: Controller())
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("k", "env"))
    monkeypatch.setattr("builtins.input", lambda prompt: prompted.append(prompt))
    args = SimpleNamespace(safety_config=None, port=1, save_history=False)
    with pytest.raises(SystemExit, match="Live rig authorization failed"):
        cli.run_session(args)
    assert prompted == []

    monkeypatch.setattr(webserve, "load_safety_config_or_exit", lambda path: incomplete)
    monkeypatch.setattr(webserve, "MicroscopeController", lambda port, guard: Controller())
    with pytest.raises(SystemExit, match="Live rig authorization failed"):
        webserve.Session(SimpleNamespace(
            safety_config=None, port=1, model=None, save_history=False,
            host="127.0.0.1",
        ))


def test_cli_and_web_fail_before_exposure_on_partial_direct_dose_policy(monkeypatch):
    from microclaw import __main__ as cli
    from microclaw import webserve
    from microclaw import credentials

    incomplete = parsed(acquisition=AcquisitionConstraints(confirm_above_frames=500))
    prompted = []
    monkeypatch.setattr(cli, "load_safety_config_or_exit", lambda path: incomplete)
    monkeypatch.setattr(cli, "MicroscopeController", lambda port, guard: Controller())
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("k", "env"))
    monkeypatch.setattr("builtins.input", lambda prompt: prompted.append(prompt))
    with pytest.raises(SystemExit, match="acquisition.confirm_above_duration_s"):
        cli.run_session(SimpleNamespace(safety_config=None, port=1, save_history=False))
    assert prompted == []

    monkeypatch.setattr(webserve, "load_safety_config_or_exit", lambda path: incomplete)
    monkeypatch.setattr(webserve, "MicroscopeController", lambda port, guard: Controller())
    with pytest.raises(SystemExit, match="acquisition.confirm_above_duration_s"):
        webserve.Session(SimpleNamespace(
            safety_config=None, port=1, model=None, save_history=False,
            host="127.0.0.1",
        ))


@pytest.mark.parametrize(("source", "key", "shown"), [
    ("env", "sk-ant-from-env-1234", "…1234 (from env)"),
    ("keyring", "sk-ant-from-store-5678", "…5678 (from keyring)"),
])
def test_repl_resolves_api_key_through_shared_loader(monkeypatch, capsys, source, key, shown):
    from microclaw import __main__ as cli
    from microclaw import agent, credentials

    installed = []
    monkeypatch.setattr(cli, "load_safety_config_or_exit", lambda path: parsed())
    monkeypatch.setattr(cli, "MicroscopeController", lambda port, guard: Controller())
    monkeypatch.setattr(credentials, "load_api_key", lambda: (key, source))
    monkeypatch.setattr(agent, "set_api_key", installed.append)
    monkeypatch.setattr(cli, "_repl", lambda *args: None)
    cli.run_session(SimpleNamespace(
        safety_config=None, port=1, save_history=False, history_retention_days=None,
    ))
    assert installed == [key]
    assert f"Anthropic API key: {shown}" in capsys.readouterr().out


@pytest.mark.parametrize("route", ["exit", "ctrl_c", "exception"])
def test_every_repl_exit_route_preserves_declared_illumination(
    monkeypatch, capsys, route
):
    from microclaw import __main__ as cli
    from microclaw import credentials

    class StatefulCore(Core):
        def __init__(self):
            super().__init__()
            self.values = {("Source", "Enable"): "1", ("Aggregate", "Gate"): "armed"}
            self.writes = []

        def get_property(self, device, prop):
            return self.values[(device, prop)]

        def set_property(self, device, prop, value):
            self.writes.append((device, prop, value))
            self.values[(device, prop)] = value

    core = StatefulCore()
    illumination = IlluminationConstraints(shutters=[
        IlluminationProperty("Source", "Enable", off_value="0"),
        IlluminationProperty("Aggregate", "Gate", off_value="closed"),
    ])
    monkeypatch.setattr(cli, "load_safety_config_or_exit",
                        lambda path: parsed(illumination=illumination))
    monkeypatch.setattr(cli, "MicroscopeController", lambda port, guard: Controller(core))
    monkeypatch.setattr(cli, "validate_live_rig", lambda *args, **kwargs: None)
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("key", "env"))
    monkeypatch.setattr("microclaw.agent.set_api_key", lambda key: None)

    if route == "exit":
        monkeypatch.setattr("builtins.input", lambda prompt: "exit")
        monkeypatch.setattr(cli, "run_agent", lambda *a, **k: pytest.fail("agent ran"))
    elif route == "ctrl_c":
        def interrupted(prompt):
            raise KeyboardInterrupt
        monkeypatch.setattr("builtins.input", interrupted)
        monkeypatch.setattr(cli, "run_agent", lambda *a, **k: pytest.fail("agent ran"))
    else:
        monkeypatch.setattr("builtins.input", lambda prompt: "acquire")
        def failed_agent(*args, **kwargs):
            raise RuntimeError("agent failed")
        monkeypatch.setattr(cli, "run_agent", failed_agent)
    before = dict(core.values)
    args = SimpleNamespace(
        safety_config=None, port=1, save_history=False,
        history_retention_days=None, profile=False, model=None,
    )
    if route == "exception":
        with pytest.raises(RuntimeError, match="agent failed"):
            cli.run_session(args)
    else:
        cli.run_session(args)

    assert core.values == before
    assert core.writes == []
    output = capsys.readouterr().out
    assert "Source.Enable = '1'" in output
    assert "Aggregate.Gate = 'armed'" in output


def test_exit_report_names_read_failure_and_makes_no_writes(capsys):
    from microclaw import __main__ as cli

    guard = SafetyGuard(SafetyConstraints(illumination=IlluminationConstraints(
        shutters=[IlluminationProperty("Source", "Enable", off_value="0")]
    )))
    core = Core()
    core.get_property = lambda *args: (_ for _ in ()).throw(RuntimeError("bridge down"))
    core.set_property = lambda *args: pytest.fail("exit report wrote hardware")
    cli.report_declared_illumination_on_exit(guard, core)
    assert "EXIT ILLUMINATION READ FAILED: Source.Enable" in capsys.readouterr().out


def test_repl_refuses_missing_api_key_before_repl(monkeypatch):
    from microclaw import __main__ as cli
    from microclaw import credentials

    monkeypatch.setattr(cli, "load_safety_config_or_exit", lambda path: parsed())
    constructed = []
    monkeypatch.setattr(
        cli, "MicroscopeController",
        lambda port, guard: constructed.append((port, guard)) or Controller(),
    )
    monkeypatch.setattr(credentials, "load_api_key", lambda: (None, None))
    monkeypatch.setattr(cli, "_repl", lambda *args: pytest.fail("REPL exposed"))
    with pytest.raises(SystemExit, match="environment variable, the system keyring, and the Microclaw credential file"):
        cli.run_session(SimpleNamespace(safety_config=None, port=1, save_history=False))
    assert constructed == []


def test_read_only_enumeration_prints_map_without_agent_or_repl(monkeypatch):
    from microclaw import __main__ as cli

    output = []
    monkeypatch.setattr(cli, "load_safety_config_or_exit", lambda path: parsed())
    monkeypatch.setattr(cli, "MicroscopeController", lambda port, guard: Controller())
    monkeypatch.setattr(cli, "run_agent", lambda *a, **k: pytest.fail("agent exposed"))
    monkeypatch.setattr(cli, "_repl", lambda *a, **k: pytest.fail("REPL exposed"))
    monkeypatch.setattr("builtins.print", lambda *a, **k: output.append(" ".join(map(str, a))))
    cli.print_authorization_map(SimpleNamespace(safety_config=None, port=1))
    rendered = "\n".join(output)
    assert '"verdict": "complete"' in rendered
    assert '"dedicated-stage"' in rendered


def test_authorization_map_cli_preserves_actionable_refusal(monkeypatch):
    from microclaw import __main__ as cli

    refusal = (
        "Property write Laser.Enable was refused. Declare it under "
        "`illumination.shutters` in the top-level `illumination` section."
    )
    monkeypatch.setattr(cli, "load_safety_config_or_exit", lambda path: parsed())
    monkeypatch.setattr(cli, "MicroscopeController", lambda port, guard: Controller())
    monkeypatch.setattr(
        cli, "validate_live_rig",
        lambda *a, **k: (_ for _ in ()).throw(RigAuthorizationError(refusal)),
    )
    with pytest.raises(SystemExit) as exc:
        cli.print_authorization_map(SimpleNamespace(safety_config=None, port=1))
    assert str(exc.value) == refusal


# --- design/33 fast-follow: MM StateDevice auto-classification -----------------
#
# A filter wheel / slider / turret is a discrete device; requiring a
# allowed_categorical declaration for each one was disproportionate (the M5
# authoring exposed the friction). Auto-classification is additive and is driven
# ONLY by the MM device type plus the reviewed config -- never by device names.


def state_device_core(**types):
    """Core whose named devices carry the given MM device types."""
    core = Core()
    core.device_types.update(types)
    core.loaded_extra = list(types)
    return core


def categorical_entries(report):
    return {
        (entry.device, entry.property): entry
        for entry in report.entries
        if entry.path == "generic-property"
        and entry.classification == "reviewed_categorical_property"
    }


def test_state_device_auto_classifies_its_discrete_position():
    core = state_device_core(FilterWheel="StateDevice")
    report = validate_live_rig(Controller(core), parsed())
    entries = categorical_entries(report)
    assert set(entries) == {("FilterWheel", "Label"), ("FilterWheel", "State")}
    for entry in entries.values():
        assert entry.source == "auto:state-device"
        assert "StateDevice" in entry.detail
    # The rig check reads this out of `microclaw ... authorization-map` JSON.
    assert {
        (item["device"], item["property"])
        for item in report.to_dict()["entries"]
        if item["source"] == "auto:state-device"
    } == {("FilterWheel", "Label"), ("FilterWheel", "State")}
    # Auto-classification covers the discrete position only, not the whole device.
    core.device_properties["FilterWheel"] = ["Label", "State", "Speed", "Delay"]
    ctrl = Controller(core)
    report = validate_live_rig(ctrl, parsed())
    assert set(categorical_entries(report)) == {
        ("FilterWheel", "Label"), ("FilterWheel", "State")
    }
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "FilterWheel", "Speed")


def test_state_device_auto_classification_survives_the_ordinal_form():
    """MM may answer with the enum ordinal rather than a name (4 = StateDevice)."""
    core = state_device_core(Turret=4)
    report = validate_live_rig(Controller(core), parsed())
    assert ("Turret", "Label") in categorical_entries(report)


def test_shutter_device_is_never_auto_classified():
    """The shutter carve-out, pinned by MM device type -- not by the name."""
    core = state_device_core(FilterWheel="StateDevice", Blocker="ShutterDevice")
    report = validate_live_rig(Controller(core), parsed())
    assert set(categorical_entries(report)) == {
        ("FilterWheel", "Label"), ("FilterWheel", "State")
    }
    ctrl = Controller(core)
    validate_live_rig(ctrl, parsed())
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "Blocker", "State")
    assert any(
        entry.path == "connected-device-inventory" and entry.device == "Blocker"
        for entry in report.entries
    )


def test_core_shutter_is_never_auto_classified_even_when_typed_state_device():
    """PINNED MECHANICAL SHUTTER TEST.

    A mechanical shutter wired as a state device (a two-position slider that
    gates light) must stay on the illumination gate. It is refused because it is
    Core.Shutter, with no name matching anywhere.
    """
    core = state_device_core(Slider="StateDevice", FilterWheel="StateDevice")
    core.shutter = "Slider"
    ctrl = Controller(core)
    report = validate_live_rig(ctrl, parsed())
    assert ("FilterWheel", "Label") in categorical_entries(report)
    assert ("Slider", "Label") not in categorical_entries(report)
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "Slider", "Label")

    # Same carve-out for a state device the operator reviewed as illumination.
    core.shutter = ""
    ctrl = Controller(core)
    validate_live_rig(
        ctrl,
        parsed(illumination=IlluminationConstraints(
            shutters=[IlluminationProperty("Slider", "Label", "Open", "Closed")],
        )),
    )
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "Slider", "State")


def test_explicit_categorical_declarations_still_work_and_stay_distinguishable():
    core = state_device_core(FilterWheel="StateDevice")
    core.loaded_extra = ["FilterWheel", "iChrome"]
    ctrl = Controller(core)
    report = validate_live_rig(ctrl, parsed(categorical={("iChrome", "Label")}))
    entries = categorical_entries(report)
    assert entries[("iChrome", "Label")].source == "declared"
    assert entries[("FilterWheel", "Label")].source == "auto:state-device"
    authorize_property_write(ctrl, "iChrome", "Label")
    authorize_property_write(ctrl, "FilterWheel", "Label")
    # A declared pair on a StateDevice is not duplicated as auto -- and after
    # the M5 finding it also takes the device's OTHER position property off the
    # table (see test_declaring_one_position_property_rules_out_the_other).
    report = validate_live_rig(ctrl, parsed(categorical={("FilterWheel", "Label")}))
    declared = [
        entry for entry in report.entries
        if (entry.device, entry.property) == ("FilterWheel", "Label")
        and entry.path == "generic-property"
    ]
    assert [entry.source for entry in declared] == ["declared"]
    assert ("FilterWheel", "State") not in categorical_entries(report)


def test_non_state_devices_are_unaffected_by_auto_classification():
    core = state_device_core(
        Sensor="GenericDevice", Hub="HubDevice", Port="SerialDevice",
    )
    ctrl = Controller(core)
    report = validate_live_rig(ctrl, parsed())
    assert categorical_entries(report) == {}
    for device in ("Sensor", "Hub", "Port", "SecondZ", "ReadOnlySensor"):
        with pytest.raises(RigAuthorizationError, match="excluded"):
            authorize_property_write(ctrl, device, "Label")


@pytest.mark.parametrize(
    "break_it",
    [
        lambda core: core.device_types.__setitem__("FilterWheel", RuntimeError("no type")),
        lambda core: core.device_properties.__setitem__("FilterWheel", RuntimeError("no props")),
        lambda core: setattr(core, "shutter", RuntimeError("no core shutter")),
    ],
)
def test_unreadable_device_facts_fail_closed(break_it):
    core = state_device_core(FilterWheel="StateDevice")
    break_it(core)
    ctrl = Controller(core)
    report = validate_live_rig(ctrl, parsed())
    assert categorical_entries(report) == {}
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "FilterWheel", "Label")


def test_excluded_and_forbidden_pairs_still_win_over_auto_classification():
    core = state_device_core(FilterWheel="StateDevice")
    ctrl = Controller(core)
    config = parsed(
        excluded={("FilterWheel", "State")},
        forbidden={("FilterWheel", "Label")},
    )
    guard = SafetyGuard(config.constraints)
    report = validate_live_rig(ctrl, config, guard=guard)
    assert categorical_entries(report) == {}
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "FilterWheel", "State")
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "FilterWheel", "Label")
    with pytest.raises(SafetyViolation):
        guard.check_property("FilterWheel", "Label")


def test_runtime_allowlist_admits_auto_classified_write_and_refuses_excluded():
    """Both gates on the raw-write path: the map AND the guard's allowlist."""
    core = state_device_core(FilterWheel="StateDevice", Piezo="StageDevice")
    ctrl = Controller(core)
    config = parsed(excluded={("Piezo", "Position")})
    guard = SafetyGuard(config.constraints)
    validate_live_rig(ctrl, config, guard=guard)

    authorize_property_write(ctrl, "FilterWheel", "Label")
    guard.check_device_property(ctrl.core, "FilterWheel", "Label", "DAPI")

    for device, prop in (("Piezo", "Position"), ("FilterWheel", "Speed")):
        with pytest.raises(RigAuthorizationError, match="excluded"):
            authorize_property_write(ctrl, device, prop)
        with pytest.raises(SafetyViolation, match="allowed_properties"):
            guard.check_property(device, prop)

    # No guard handed over (read-only enumeration) => the guard never widens.
    fresh = SafetyGuard(config.constraints)
    validate_live_rig(Controller(core), config)
    with pytest.raises(SafetyViolation, match="allowed_properties"):
        fresh.check_property("FilterWheel", "Label")


def test_auto_classified_preset_is_authorized_without_a_declaration():
    core = state_device_core(FilterWheel="StateDevice")
    core.presets["DAPI"] = [
        {"device": "FilterWheel", "property": "Label", "value": "DAPI"}
    ]
    ctrl = Controller(core)
    report = validate_live_rig(ctrl, parsed(channels=["DAPI"]))
    assert report.complete is True
    authorize_channel(ctrl, "DAPI")
    preset_entry = next(
        entry for entry in report.entries if entry.path == "channel-preset:DAPI"
    )
    assert preset_entry.classification == "reviewed_categorical_property"
    assert preset_entry.source == "auto:state-device"


# --- M5 rig finding (2026-07-23): auto-classification fills vacuums only -------
#
# The operator's unmodified M5 config declared iChrome-MLE-TCP.Label, with a
# comment saying they were not sure whether the driver's write property was
# Label or State. Auto-classification then handed them State on a Toptica laser
# engine, with no config change and against an explicit narrowing. A ruling on
# either position property now takes the whole discrete position off the table.


def test_declaring_one_position_property_does_not_auto_admit_the_other_m5():
    """M5 regression: iChrome-MLE-TCP.Label declared must NOT yield State."""
    core = state_device_core(**{"iChrome-MLE-TCP": "StateDevice"})
    ctrl = Controller(core)
    config = parsed(categorical={("iChrome-MLE-TCP", "Label")})
    guard = SafetyGuard(config.constraints)
    report = validate_live_rig(ctrl, config, guard=guard)

    entries = categorical_entries(report)
    assert set(entries) == {("iChrome-MLE-TCP", "Label")}
    assert entries[("iChrome-MLE-TCP", "Label")].source == "declared"

    authorize_property_write(ctrl, "iChrome-MLE-TCP", "Label")
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "iChrome-MLE-TCP", "State")
    with pytest.raises(SafetyViolation, match="allowed_properties"):
        guard.check_property("iChrome-MLE-TCP", "State")


@pytest.mark.parametrize("declared", ["Label", "State"])
@pytest.mark.parametrize("key", ["categorical", "excluded"])
def test_declaring_one_position_property_rules_out_the_other(declared, key):
    """Symmetric in both properties and in both kinds of ruling."""
    other = "State" if declared == "Label" else "Label"
    core = state_device_core(Selector="StateDevice")
    ctrl = Controller(core)
    report = validate_live_rig(ctrl, parsed(**{key: {("Selector", declared)}}))
    assert ("Selector", other) not in categorical_entries(report)
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "Selector", other)


def test_a_forbidden_position_property_also_rules_out_the_other():
    core = state_device_core(Selector="StateDevice")
    ctrl = Controller(core)
    report = validate_live_rig(ctrl, parsed(forbidden={("Selector", "State")}))
    assert categorical_entries(report) == {}
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "Selector", "Label")


def test_a_ruling_on_a_non_position_property_does_not_disable_the_device():
    """Position-scoped, not device-scoped: excluding Speed keeps the wheel."""
    core = state_device_core(FilterWheel="StateDevice")
    core.device_properties["FilterWheel"] = ["Label", "State", "Speed"]
    ctrl = Controller(core)
    report = validate_live_rig(ctrl, parsed(excluded={("FilterWheel", "Speed")}))
    assert set(categorical_entries(report)) == {
        ("FilterWheel", "Label"), ("FilterWheel", "State")
    }
    authorize_property_write(ctrl, "FilterWheel", "State")


def test_a_ruling_on_one_device_leaves_other_state_devices_auto_classified():
    """The block's purpose survives: undeclared wheels/ELL6 still auto-classify."""
    core = state_device_core(**{
        "iChrome-MLE-TCP": "StateDevice",
        "Thorlabs Filter Wheel": "StateDevice",
        "ELL6": "StateDevice",
    })
    ctrl = Controller(core)
    report = validate_live_rig(
        ctrl, parsed(categorical={("iChrome-MLE-TCP", "Label")})
    )
    assert set(categorical_entries(report)) == {
        ("iChrome-MLE-TCP", "Label"),
        ("Thorlabs Filter Wheel", "Label"), ("Thorlabs Filter Wheel", "State"),
        ("ELL6", "Label"), ("ELL6", "State"),
    }
    authorize_property_write(ctrl, "Thorlabs Filter Wheel", "State")
    authorize_property_write(ctrl, "ELL6", "State")


def test_preset_may_retarget_core_shutter_to_a_declared_shutter_device():
    """The demo rig's four fluorescence channels, which Block 4b's G1 caught.

    `Cy5`/`DAPI`/`FITC`/`Rhodamine` each set `Core.Shutter = 'White Light
    Shutter'`, and `authorization.py` has a purpose-built rule permitting that
    when the selected device is itself a declared illumination shutter — the
    gate still covers whatever the preset switches to. First-launch setup was
    writing an *explicit* `Core.Shutter` exclusion, which is tested first and
    therefore shadowed the rule, refusing startup outright.
    """
    core = Core()
    core.loaded_extra = ["White Light Shutter"]
    core.presets["Cy5"] = [
        {"device": "Core", "property": "Shutter", "value": "White Light Shutter"}
    ]
    illumination = IlluminationConstraints(shutters=[IlluminationProperty(
        "White Light Shutter", "State", on_value="1", off_value="0"
    )])
    config = parsed(channels=["Cy5"], illumination=illumination)
    report = validate_live_rig(Controller(core), config, guard=SafetyGuard(config.constraints))
    assert report.authorized_presets == {"Cy5"}

    # An explicit exclusion is what broke it, and it must still refuse a
    # retarget to a device that is NOT a declared shutter.
    core.presets["Bad"] = [
        {"device": "Core", "property": "Shutter", "value": "Undeclared Shutter"}
    ]
    with pytest.raises(RigAuthorizationError, match="core-device retarget is excluded"):
        validate_live_rig(
            Controller(core),
            parsed(channels=["Bad"], illumination=illumination),
        )


# ── channels from the EMU laser map, on a rig with no Channel group (41c) ────

M5_CONFIG = Path(__file__).parent / "fixtures" / "m5-config.uicfg"
M5_LASER_DEVICES = ["iChrome-MLE-TCP", "Laser Trigger", "Thorlabs Filter Wheel",
                    "Thorlabs ELL6", "PIZStage"]
# EMU slot -> the iChrome's own channel number, which runs the OTHER WAY.
M5_ENABLE = {slot: f"Laser {4 - slot}: 1. Enable" for slot in range(4)}


def m5_emu_controller(tmp_path, parameters=None):
    """The captured M5 configuration on a rig whose Channel group is empty."""
    mm_dir = tmp_path / "Micro-Manager-2.0"
    (mm_dir / "EMU").mkdir(parents=True)
    (mm_dir / "plugins").mkdir()
    raw = json.loads(M5_CONFIG.read_text(encoding="utf-8"))
    if parameters is not None:
        raw["pluginConfigurations"][0]["parameters"] = parameters
    (mm_dir / "EMU" / "config.uicfg").write_text(json.dumps(raw), encoding="utf-8")
    ctrl = Controller(Core())
    ctrl.core.loaded_extra = list(M5_LASER_DEVICES)
    ctrl.get_mm_app_dir = lambda: str(mm_dir)
    return ctrl


def m5_illumination(slots=range(4)):
    return IlluminationConstraints(shutters=[
        IlluminationProperty("iChrome-MLE-TCP", M5_ENABLE[slot], "1", "0")
        for slot in slots
    ])


def test_emu_named_slots_are_authorized_channels_with_the_right_enable(tmp_path):
    ctrl = m5_emu_controller(tmp_path)
    report = validate_live_rig(
        ctrl, parsed(channels=["640", "561"], illumination=m5_illumination())
    )
    assert report.channel_source == "emu-laser-map"
    assert report.authorized_presets == {"640", "561"}
    authorize_channel(ctrl, "640")
    # Exactly one entry per channel turns a line ON, and it is the reversed
    # pair -- slot 3 is "640" and its enable is `Laser 1`, not `Laser 3`.
    armed = {
        preset: [(e.device, e.property) for e in report.entries
                 if e.path == f"channel-preset:{preset}" and e.detail == "value='1'"]
        for preset in ("640", "561")
    }
    assert armed == {
        "640": [("iChrome-MLE-TCP", M5_ENABLE[3])],
        "561": [("iChrome-MLE-TCP", M5_ENABLE[2])],
    }


def test_emu_channel_effect_that_is_not_declared_illumination_is_refused(tmp_path):
    """Same gate, same message shape as a preset-sourced effect."""
    ctrl = m5_emu_controller(tmp_path)
    with pytest.raises(RigAuthorizationError) as caught:
        validate_live_rig(
            ctrl, parsed(channels=["640"], illumination=m5_illumination([3]))
        )
    message = str(caught.value)
    assert "constraints.illumination.shutters" in message
    assert M5_ENABLE[0] in message


def test_channels_allowed_advice_on_an_emu_rig_names_the_real_remedy(tmp_path):
    """The pre-41c diagnostic told an EMU operator to delete a real channel.

    "This rig has no Micro-Manager Channel group; remove these non-channel
    claims" is wrong once the rig's channels come from its EMU laser map.
    """
    ctrl = m5_emu_controller(tmp_path)
    report = validate_live_rig(
        ctrl, parsed(channels=["640", "Cy5"], illumination=m5_illumination())
    )
    message = report.diagnostics[0].message
    assert "has no Micro-Manager Channel group" not in message
    assert "EMU laser map" in message and "'640'" in message
    assert report.authorized_presets == {"640"}
    assert "EMU laser map" in report.excluded_presets["Cy5"][0]
    with pytest.raises(RigAuthorizationError, match="EMU laser map"):
        authorize_channel(ctrl, "Cy5")


def test_slot_the_rig_refused_to_name_stays_visible_with_its_reason(tmp_path):
    ctrl = m5_emu_controller(tmp_path, parameters={
        "Laser 0 - Name": "405", "Laser 1 - Name": "488", "Laser 2 - Name": "561",
    })
    report = validate_live_rig(
        ctrl, parsed(channels=["561"], illumination=m5_illumination())
    )
    assert report.authorized_presets == {"561"}
    reason = report.excluded_presets["EMU laser slot 3"][0]
    assert "no configured name" in reason and "Laser 3 - Name" in reason
    assert not any(name.startswith("Laser ") for name in report.authorized_presets)


def test_unreadable_emu_config_refuses_the_session_rather_than_offering_none(tmp_path):
    ctrl = m5_emu_controller(tmp_path)
    (tmp_path / "Micro-Manager-2.0" / "EMU" / "config.uicfg").write_text("not json", encoding="utf-8")
    with pytest.raises(RigAuthorizationError, match="Could not resolve EMU semantic"):
        validate_live_rig(ctrl, parsed(channels=["640"], illumination=m5_illumination()))
