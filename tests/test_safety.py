from unittest.mock import MagicMock

import os
from pathlib import Path

import pytest
import yaml
from microclaw.safety import (
    ActuatorId,
    BUILTIN_TYPED_CAPABILITIES,
    CameraConstraints,
    ForbiddenProperty,
    IlluminationConstraints,
    IlluminationProperty,
    NamedStageLimits,
    PluginConstraints,
    ParsedSafetyConfig,
    PropertyAuthorization,
    SafetyConstraints,
    SafetyConfigError,
    SafetyGuard,
    SafetyViolation,
    StageConstraints,
    TypedActuatorId,
    TypedActuatorPolicy,
)


def _core(x=0.0, y=0.0):
    core = MagicMock()
    core.get_focus_device.return_value = "DStage"
    core.get_camera_device.return_value = "DCam"
    core.get_xy_stage_device.return_value = "DXYStage"
    core.get_x_position.return_value = x
    core.get_y_position.return_value = y
    return core


def _parse(path):
    """Give pre-schema parser tests the now-mandatory reviewed metadata."""
    path = Path(path)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if document is None:
        document = {}
    if isinstance(document, dict):
        document.setdefault("schema_version", 3)
        document.setdefault("reviewed", True)
        legacy_allowed = document.pop("allowed_properties", None)
        if "forbidden_properties" in document and legacy_allowed is None:
            mode = "degraded_trusted_plugins"
            categorical = None
        else:
            mode = "guaranteed"
            categorical = legacy_allowed or []
        profile = {"mode": mode, "denied": []}
        if categorical is not None:
            profile["allowed_categorical"] = categorical
        document.setdefault(
            "property_authorization",
            profile,
        )
        document.setdefault("acquisition", {
            "max_frames": 10000,
            "max_duration_s": 3600,
            "max_bytes": 50000000000,
            "max_illuminated_ms": 600000,
            "max_session_illuminated_ms": 1800000,
            "confirm_above_frames": 500,
            "confirm_above_duration_s": 300,
            "confirm_above_bytes": 5000000000,
            "confirm_above_illuminated_ms": 60000,
        })
        for section in ("camera", "analysis", "channels"):
            if document.get(section) == {}:
                document.pop(section)
        stage = document.get("stage")
        if isinstance(stage, dict):
            for axis in ("x", "y", "z"):
                low, high = f"{axis}_min", f"{axis}_max"
                if low in stage and high not in stage:
                    stage[high] = {"unbounded": True, "reason": "legacy test fixture"}
                elif high in stage and low not in stage:
                    stage[low] = {"unbounded": True, "reason": "legacy test fixture"}
        for item in document.get("named_stages", []):
            if "min_um" in item and "max_um" not in item:
                item["max_um"] = {"unbounded": True, "reason": "legacy test fixture"}
            elif "max_um" in item and "min_um" not in item:
                item["min_um"] = {"unbounded": True, "reason": "legacy test fixture"}
        path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return ParsedSafetyConfig.from_yaml(str(path)).constraints


@pytest.fixture
def guard():
    c = SafetyConstraints(
        stage=StageConstraints(x_min=-500, x_max=500, y_min=-500, y_max=500,
                               z_min=10, z_max=150),
    )
    return SafetyGuard(c)


class TestBoundaryExact:
    """Values at exactly the boundary must pass; one unit outside must fail."""

    def test_x_at_max(self, guard):
        guard.check_xy(500.0, 0.0)  # exactly at limit — should not raise

    def test_x_beyond_max(self, guard):
        with pytest.raises(SafetyViolation):
            guard.check_xy(500.001, 0.0)

    def test_z_at_min(self, guard):
        guard.check_z(10.0)  # exactly at limit

    def test_z_below_min(self, guard):
        with pytest.raises(SafetyViolation):
            guard.check_z(9.999)

    def test_z_at_max(self, guard):
        guard.check_z(150.0)

    def test_z_beyond_max(self, guard):
        with pytest.raises(SafetyViolation):
            guard.check_z(150.001)


class TestNoConstraints:
    """Unconstrained guard should allow anything."""

    def test_extreme_z(self):
        guard = SafetyGuard(SafetyConstraints())
        guard.check_z(1_000_000.0)  # no exception

    def test_all_channels(self):
        guard = SafetyGuard(SafetyConstraints())
        guard.check_channel("AnythingAtAll")


class TestFromYaml:
    _ACQUISITION = (
        "acquisition:\n"
        "  max_frames: 10000\n"
        "  max_duration_s: 3600\n"
        "  max_bytes: 50000000000\n"
        "  max_illuminated_ms: 600000\n"
        "  max_session_illuminated_ms: 1800000\n"
        "  confirm_above_frames: 500\n"
        "  confirm_above_duration_s: 300\n"
        "  confirm_above_bytes: 5000000000\n"
        "  confirm_above_illuminated_ms: 60000\n"
    )
    _PROFILE = (
        "property_authorization:\n"
        "  mode: guaranteed\n"
        "  allowed_categorical: []\n"
        "  denied: []\n"
        + _ACQUISITION
    )

    def test_schema_version_is_mandatory_and_old_versions_are_clear(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text("reviewed: true\n", encoding="utf-8")
        with pytest.raises(SafetyConfigError, match="microclaw serve"):
            ParsedSafetyConfig.from_yaml(str(cfg))
        cfg.write_text("schema_version: 2\nreviewed: true\n", encoding="utf-8")
        with pytest.raises(SafetyConfigError, match="Rename.*microclaw serve"):
            ParsedSafetyConfig.from_yaml(str(cfg))

    def test_new_property_authorization_shape_parses_and_enforces_guaranteed_mode(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "schema_version: 3\nreviewed: true\n"
            "property_authorization:\n"
            "  mode: guaranteed\n"
            "  allowed_categorical: []\n"
            "  allowed_numeric:\n"
            "    - {device: Camera, property: Gain, kind: bounded-numeric, units: dB, minimum: 0, maximum: 10}\n"
            "  denied: []\n"
            + self._ACQUISITION
        , encoding="utf-8")
        parsed = ParsedSafetyConfig.from_yaml(str(cfg))
        assert parsed.property_authorization.mode == "guaranteed"
        assert parsed.property_authorization.allowed_numeric[
            TypedActuatorId("Camera", "Gain")
        ] == TypedActuatorPolicy("bounded-numeric", "dB", 0.0, 10.0)

        cfg.write_text(
            "schema_version: 3\nreviewed: true\n"
            "property_authorization: {mode: guaranteed, denied: []}\n"
            + self._ACQUISITION
        , encoding="utf-8")
        with pytest.raises(SafetyConfigError, match="property_authorization.allowed_categorical"):
            ParsedSafetyConfig.from_yaml(str(cfg))

    def test_shipped_example_parses_through_the_strict_schema(self):
        """The packaged example must survive strict validation (design/32 1b).

        Regression: `analysis.min_snr` is documented optional ("Omit to retain
        the visibly uncalibrated package fallback"), so the example ships with it
        commented out. Requiring it broke startup for every rig that omits it.
        Read the example the way an installed wheel does, not via __file__.
        """
        from importlib.resources import files

        example = files("microclaw").joinpath("safety_config.example.yaml")
        parsed = ParsedSafetyConfig.from_yaml(str(example))
        assert parsed.constraints.analysis.min_snr is None
        assert parsed.constraints.allowed_properties is None
        assert parsed.constraints.forbidden_properties == []
        assert parsed.property_authorization.denied == frozenset()
        assert set(parsed.ranges) == {
            ActuatorId("core_xy", None, "stage-position", "x"),
            ActuatorId("core_xy", None, "stage-position", "y"),
            ActuatorId("core_focus", None, "stage-position", "z"),
            ActuatorId("named", "FictionalPiezoZ", "stage-position", None),
        }

    def test_analysis_section_without_min_snr_is_accepted(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text("schema_version: 3\nreviewed: true\n" + self._PROFILE + "analysis:\n", encoding="utf-8")
        parsed = ParsedSafetyConfig.from_yaml(str(cfg))
        assert parsed.constraints.analysis.min_snr is None

    def test_ordered_edges_have_structured_core_identities(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "schema_version: 3\nreviewed: true\n"
            "property_authorization:\n"
            "  mode: guaranteed\n"
            "  allowed_categorical: []\n"
            "  denied: []\n"
            "stage: {x_min: -10, x_max: 10, z_min: 0, z_max: 200}\n"
            + self._ACQUISITION
        , encoding="utf-8")
        parsed = ParsedSafetyConfig.from_yaml(str(cfg))
        x = parsed.ranges[ActuatorId("core_xy", None, "stage-position", "x")]
        z = parsed.ranges[ActuatorId("core_focus", None, "stage-position", "z")]
        assert (x.minimum.bound, x.maximum.bound) == (-10.0, 10.0)
        assert (z.minimum.bound, z.maximum.bound) == (0.0, 200.0)
        assert parsed.constraints.stage.x_min == x.minimum.bound
        assert parsed.property_authorization == PropertyAuthorization(
            "guaranteed",
            frozenset(),
        )
        assert BUILTIN_TYPED_CAPABILITIES == frozenset(
            {"stage-position", "exposure", "camera-roi", "illumination", "acquisition-dose"}
        )

    def test_unbounded_reasons_survive_for_core_and_named_ranges(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "schema_version: 3\nreviewed: true\n"
            "property_authorization:\n"
            "  mode: guaranteed\n"
            "  allowed_categorical: []\n"
            "  denied: []\n"
            "stage:\n"
            "  x_min: -10\n"
            "  x_max: {unbounded: true, reason: travel is mechanically stopped}\n"
            "named_stages:\n"
            "  - device: TIRF\n"
            "    min_um: {unbounded: true, reason: controller enforces lower edge}\n"
            "    max_um: {unbounded: true, reason: controller enforces upper edge}\n"
            + self._ACQUISITION
        , encoding="utf-8")
        parsed = ParsedSafetyConfig.from_yaml(str(cfg))
        assert {edge.unbounded_reason for policy in parsed.ranges.values()
                for edge in (policy.minimum, policy.maximum) if edge.bound is None} == {
            "travel is mechanically stopped",
            "controller enforces lower edge",
            "controller enforces upper edge",
        }
        assert parsed.constraints.stage.x_max is None
        assert parsed.constraints.named_stages == [NamedStageLimits("TIRF", None, None)]

    def test_guaranteed_mode_requires_allowed_categorical(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "schema_version: 3\nreviewed: true\n"
            "forbidden_properties: [{device: Core, property: Initialize}]\n"
            "property_authorization: {mode: guaranteed, denied: []}\n"
        , encoding="utf-8")
        with pytest.raises(SafetyConfigError, match="denylist-only configs must migrate"):
            ParsedSafetyConfig.from_yaml(str(cfg))

    def test_degraded_trusted_plugin_mode_is_explicit_and_allows_denylist(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "schema_version: 3\nreviewed: true\n"
            "forbidden_properties: [{device: Core, property: Initialize}]\n"
            "property_authorization:\n"
            "  mode: degraded_trusted_plugins\n"
            "  denied:\n"
            "    - {device: Core, property: Initialize}\n"
            + self._ACQUISITION
        , encoding="utf-8")
        parsed = ParsedSafetyConfig.from_yaml(str(cfg))
        assert parsed.property_authorization.mode == "degraded_trusted_plugins"
        assert parsed.constraints.allowed_properties is None

    def test_mode_defaults_to_guaranteed_and_rejects_unknown_value(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "schema_version: 3\nreviewed: true\n"
            "property_authorization: {allowed_categorical: [], denied: []}\n"
            + self._ACQUISITION
        , encoding="utf-8")
        assert ParsedSafetyConfig.from_yaml(str(cfg)).property_authorization.mode == "guaranteed"
        cfg.write_text(
            "schema_version: 3\nreviewed: true\n"
            "property_authorization: {mode: trusted, allowed_categorical: [], denied: []}\n"
            + self._ACQUISITION
        , encoding="utf-8")
        with pytest.raises(SafetyConfigError, match="degraded_trusted_plugins"):
            ParsedSafetyConfig.from_yaml(str(cfg))

    def test_categorical_authorization_cannot_also_be_excluded(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "schema_version: 3\nreviewed: true\n"
            "property_authorization:\n"
            "  mode: guaranteed\n"
            "  allowed_categorical:\n"
            "    - {device: DCam, property: Binning}\n"
            "    - {device: DCam, property: Binning}\n"
            "  denied: [{device: DCam, property: Binning}]\n"
        , encoding="utf-8")
        with pytest.raises(SafetyConfigError) as exc:
            ParsedSafetyConfig.from_yaml(str(cfg))
        message = str(exc.value)
        assert "duplicate device/property pair" in message
        assert "cannot be both categorically authorized and excluded" in message

    def test_missing_counterpart_and_other_errors_are_aggregated(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "schema_version: 3\nreviewed: true\n"
            "stage: {x_min: 0, typo: 2}\n"
            "camera: {max_exposure_ms: -1}\n"
        , encoding="utf-8")
        with pytest.raises(SafetyConfigError) as exc:
            ParsedSafetyConfig.from_yaml(str(cfg))
        message = str(exc.value)
        assert str(cfg) in message
        assert "stage.typo" in message
        assert "missing 'x_max'" in message
        assert "greater than zero" in message

    def test_rejects_duplicate_device_property_pairs(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "schema_version: 3\nreviewed: true\n"
            "forbidden_properties:\n"
            "  - {device: Core, property: Initialize}\n"
            "  - {device: Core, property: Initialize}\n"
        , encoding="utf-8")
        with pytest.raises(SafetyConfigError, match="duplicate device/property"):
            ParsedSafetyConfig.from_yaml(str(cfg))

    @pytest.mark.parametrize(
        "edge",
        [
            "{unbounded: false, reason: no}",
            "{unbounded: true}",
            "{unbounded: true, reason: ''}",
            "{unbounded: true, reason: ok, typo: true}",
        ],
    )
    def test_rejects_malformed_or_unreasoned_unbounded_edge(self, tmp_path, edge):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "schema_version: 3\nreviewed: true\n"
            f"stage:\n  x_min: 0\n  x_max: {edge}\n"
        , encoding="utf-8")
        with pytest.raises(SafetyConfigError):
            ParsedSafetyConfig.from_yaml(str(cfg))

    def test_loads_yaml(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "stage:\n  z_min: 5.0\n  z_max: 100.0\n"
            "camera:\n  max_exposure_ms: 500\n"
        , encoding="utf-8")
        constraints = _parse(str(cfg))
        guard = SafetyGuard(constraints)
        with pytest.raises(SafetyViolation):
            guard.check_z(0.0)
        with pytest.raises(SafetyViolation):
            guard.check_exposure(600.0)

    def test_empty_yaml_gives_no_constraints(self, tmp_path):
        cfg = tmp_path / "empty.yaml"
        cfg.write_text("", encoding="utf-8")
        constraints = _parse(str(cfg))
        guard = SafetyGuard(constraints)
        guard.check_z(999999.0)  # no exception

    def test_forbidden_property_loaded(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "forbidden_properties:\n"
            "  - device: Core\n"
            "    property: Initialize\n"
        , encoding="utf-8")
        constraints = _parse(str(cfg))
        guard = SafetyGuard(constraints)
        with pytest.raises(SafetyViolation, match="forbidden"):
            guard.check_property("Core", "Initialize")

    def test_channels_loaded(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text("channels:\n  allowed: [DAPI, FITC]\n", encoding="utf-8")
        constraints = _parse(str(cfg))
        guard = SafetyGuard(constraints)
        guard.check_channel("DAPI")  # no exception
        with pytest.raises(SafetyViolation):
            guard.check_channel("GFP")

    def test_plugins_loaded(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "plugins:\n"
            "  blocked:\n"
            "    - org.example.KnownBadPlugin\n"
            "  allow_hardware_motion: true\n"
        , encoding="utf-8")
        constraints = _parse(str(cfg))
        assert constraints.plugins.blocked == ["org.example.KnownBadPlugin"]
        assert constraints.plugins.allow_hardware_motion is True

    def test_plugins_default_when_absent(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text("stage:\n  z_min: 0.0\n", encoding="utf-8")
        constraints = _parse(str(cfg))
        assert constraints.plugins.blocked == []
        assert constraints.plugins.allow_hardware_motion is True

    def test_omitted_optional_sections_restrict_nothing_at_guard(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "schema_version: 3\nreviewed: true\n"
            "stage: {z_min: 0, z_max: 100}\n"
            "acquisition: {confirm_above_frames: 500, "
            "confirm_above_duration_s: 1200}\n",
            encoding="utf-8",
        )
        parsed = ParsedSafetyConfig.from_yaml(str(cfg))
        guard = SafetyGuard(parsed.constraints)
        guard.check_exposure(1_000_000)
        guard.check_channel("Any channel")
        guard.check_plugin_motion("any.plugin")
        guard.check_device_property(_core(), "OldLaser", "Enable", "On")

    @pytest.mark.parametrize("document", ["[]\n", "a scalar\n"])
    def test_rejects_non_mapping_root_with_filename(self, tmp_path, document):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(document, encoding="utf-8")
        with pytest.raises(SafetyConfigError) as exc:
            _parse(str(cfg))
        assert str(cfg) in str(exc.value)
        assert "document root" in str(exc.value)

    def test_unknown_keys_are_file_anchored_and_aggregated(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text("stagee: {}\nstage: {x_mim: 0}\ncamera: {exposure: 5}\n", encoding="utf-8")
        with pytest.raises(SafetyConfigError) as exc:
            _parse(str(cfg))
        message = str(exc.value)
        assert str(cfg) in message
        assert "stagee: unknown top-level key" in message
        assert "stage.x_mim: unknown key" in message
        assert "camera.exposure: unknown key" in message

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("stage.x_min", True),
            ("stage.x_max", "many"),
            ("stage.y_min", float("nan")),
            ("stage.y_max", float("inf")),
            ("stage.z_min", float("-inf")),
            ("stage.z_max", False),
            ("camera.max_exposure_ms", "slow"),
            ("analysis.min_snr", float("nan")),
            ("illumination.max_power_percent", float("inf")),
            ("illumination.max_power_step_factor", True),
            ("named_stages[0].min_um", float("nan")),
            ("named_stages[0].max_um", "far"),
        ],
    )
    def test_rejects_invalid_value_in_every_numeric_field(
        self, tmp_path, field, value
    ):
        document = {
            "stage": {}, "camera": {}, "analysis": {}, "illumination": {},
            "named_stages": [{"device": "Z"}],
        }
        if field.startswith("named_stages"):
            document["named_stages"][0][field.rsplit(".", 1)[1]] = value
        else:
            section, key = field.split(".")
            document[section][key] = value
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(yaml.safe_dump(document), encoding="utf-8")
        with pytest.raises(SafetyConfigError, match="finite number") as exc:
            _parse(str(cfg))
        assert field in str(exc.value)

    @pytest.mark.parametrize(
        "document",
        [
            {"stage": {"x_min": 2, "x_max": 1}},
            {"stage": {"y_min": 2, "y_max": 2}},
            {"stage": {"z_min": 2, "z_max": 1}},
            {"named_stages": [{"device": "Z", "min_um": 2, "max_um": 1}]},
        ],
    )
    def test_rejects_unordered_bounds(self, tmp_path, document):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(yaml.safe_dump(document), encoding="utf-8")
        with pytest.raises(SafetyConfigError, match="minimum must be less"):
            _parse(str(cfg))

    @pytest.mark.parametrize("value", [0, -0.1])
    def test_rejects_non_positive_max_exposure(self, tmp_path, value):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(yaml.safe_dump({"camera": {"max_exposure_ms": value}}), encoding="utf-8")
        with pytest.raises(SafetyConfigError, match="greater than zero"):
            _parse(str(cfg))


class TestFiniteRuntimeGuards:
    @pytest.mark.parametrize(
        "call",
        [
            lambda guard: guard.check_xy(float("nan"), 0),
            lambda guard: guard.check_xy(0, float("inf")),
            lambda guard: guard.check_z(float("nan")),
            lambda guard: guard.check_exposure(float("nan")),
            lambda guard: guard.check_named_stage("Z", float("nan")),
        ],
    )
    def test_public_numeric_guards_reject_non_finite_values(self, call):
        guard = SafetyGuard(SafetyConstraints(
            named_stages=[NamedStageLimits("Z", 0, 10)]
        ))
        with pytest.raises(SafetyViolation, match="finite number"):
            call(guard)

    def test_hardware_value_read_during_xy_check_rejects_nan(self):
        guard = SafetyGuard(SafetyConstraints())
        with pytest.raises(SafetyViolation, match="finite number"):
            guard.check_device_property(
                _core(y=float("nan")), "DXYStage", "X", "1"
            )

    def test_guarded_raw_property_rejects_non_numeric_value(self):
        guard = SafetyGuard(SafetyConstraints())
        with pytest.raises(SafetyViolation, match="finite number"):
            guard.check_device_property(_core(), "DStage", "Position", "unknown")

    def test_programmatic_non_finite_limit_fails_closed(self):
        guard = SafetyGuard(SafetyConstraints(
            stage=StageConstraints(z_max=float("nan"))
        ))
        with pytest.raises(SafetyViolation, match="Configured stage.z_max"):
            guard.check_z(1)

    def test_illumination_current_hardware_value_rejects_nan(self):
        guard = _laser_guard()
        core = _core()
        core.get_property.return_value = "nan"
        with pytest.raises(SafetyViolation, match="finite number"):
            guard.check_illumination(
                core, "Luxx638", "Laser Power Set-point Select [%]", "2"
            )

    def test_illumination_current_hardware_value_rejects_non_number(self):
        guard = _laser_guard()
        core = _core()
        core.get_property.return_value = "unknown"
        with pytest.raises(SafetyViolation, match="finite number"):
            guard.check_illumination(
                core, "Luxx638", "Laser Power Set-point Select [%]", "2"
            )


class TestCheckDeviceProperty:
    """Raw set_property writes re-apply the numeric guards on guarded axes."""

    @pytest.fixture
    def guard(self):
        c = SafetyConstraints(
            stage=StageConstraints(x_min=-500, x_max=500, y_min=-500, y_max=500,
                                   z_min=0, z_max=200),
            camera=CameraConstraints(max_exposure_ms=5000),
        )
        return SafetyGuard(c)

    def test_focus_position_over_max_blocked(self, guard):
        with pytest.raises(SafetyViolation):
            guard.check_device_property(_core(), "DStage", "Position", "999999")

    def test_focus_position_in_range_ok(self, guard):
        guard.check_device_property(_core(), "DStage", "Position", "100")

    def test_camera_exposure_over_max_blocked(self, guard):
        with pytest.raises(SafetyViolation):
            guard.check_device_property(_core(), "DCam", "Exposure", "60000")

    def test_xy_axis_over_max_blocked(self, guard):
        # X write past x_max, other axis read from the core (in range)
        with pytest.raises(SafetyViolation):
            guard.check_device_property(_core(), "DXYStage", "X", "9999")

    def test_non_numeric_property_only_denylist(self, guard):
        # A benign label write on a guarded device is fine (denylist empty).
        guard.check_device_property(_core(), "DStage", "Label", "State-1")

    def test_unrelated_device_passes_numeric_gate(self, guard):
        # A numeric write to a device that is not focus/cam/xy isn't re-guarded.
        guard.check_device_property(_core(), "DWheel", "Position", "999999")

    def test_denylist_still_applies(self):
        guard = SafetyGuard(
            SafetyConstraints(
                forbidden_properties=[ForbiddenProperty("Core", "Initialize")]
            )
        )
        with pytest.raises(SafetyViolation, match="forbidden"):
            guard.check_device_property(_core(), "Core", "Initialize", "1")


class TestAllowlistMode:
    def test_allowlisted_pair_passes(self):
        guard = SafetyGuard(
            SafetyConstraints(
                allowed_properties=[ForbiddenProperty("DCam", "Binning")]
            )
        )
        guard.check_property("DCam", "Binning")  # no exception

    def test_unlisted_pair_refused(self):
        guard = SafetyGuard(
            SafetyConstraints(
                allowed_properties=[ForbiddenProperty("DCam", "Binning")]
            )
        )
        with pytest.raises(SafetyViolation, match="not in the allowed_properties"):
            guard.check_property("DStage", "Position")

    def test_allowlist_overrides_denylist(self):
        # When allowed_properties is set, forbidden_properties is ignored and
        # only the allowlist decides.
        guard = SafetyGuard(
            SafetyConstraints(
                allowed_properties=[ForbiddenProperty("DCam", "Binning")],
                forbidden_properties=[ForbiddenProperty("DCam", "Binning")],
            )
        )
        guard.check_property("DCam", "Binning")  # allowed wins

    def test_auto_classified_pair_is_admitted_but_the_denylist_still_wins(self):
        # design/33 fast-follow: the live map hands the guard the StateDevice
        # positions it auto-classified, so a raw write clears both gates. It is
        # additive -- the declared allowlist and the denylist are untouched.
        guard = SafetyGuard(
            SafetyConstraints(
                allowed_properties=[ForbiddenProperty("DCam", "Binning")],
                forbidden_properties=[ForbiddenProperty("Wheel", "State")],
            )
        )
        with pytest.raises(SafetyViolation, match="not in the allowed_properties"):
            guard.check_property("Wheel", "Label")
        guard.admit_auto_classified({("Wheel", "Label"), ("Wheel", "State")})
        guard.check_property("Wheel", "Label")   # no exception
        guard.check_property("DCam", "Binning")  # declared pair unaffected
        with pytest.raises(SafetyViolation, match="forbidden"):
            guard.check_property("Wheel", "State")
        with pytest.raises(SafetyViolation, match="not in the allowed_properties"):
            guard.check_property("Wheel", "Speed")

    def test_no_pairs_are_auto_classified_until_a_live_rig_says_so(self):
        guard = SafetyGuard(
            SafetyConstraints(allowed_properties=[ForbiddenProperty("DCam", "Binning")])
        )
        with pytest.raises(SafetyViolation, match="not in the allowed_properties"):
            guard.check_property("Wheel", "Label")

    def test_from_yaml_loads_allowed_properties(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "property_authorization:\n"
            "  mode: guaranteed\n"
            "  allowed_categorical:\n"
            "    - {device: DCam, property: Binning}\n"
            "  denied: []\n"
        , encoding="utf-8")
        constraints = _parse(str(cfg))
        assert constraints.allowed_properties == [ForbiddenProperty("DCam", "Binning")]
        parsed = ParsedSafetyConfig.from_yaml(str(cfg))
        assert parsed.property_authorization.allowed_categorical == frozenset(
            {("DCam", "Binning")}
        )
        assert parsed.property_authorization.denied == frozenset()
        guard = SafetyGuard(constraints)
        with pytest.raises(SafetyViolation):
            guard.check_property("DStage", "Position")


class TestWorkspaceSandbox:
    def test_local_reads_ignore_a_configured_workspace(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside" / "input.json"
        guard = SafetyGuard(SafetyConstraints(workspace_dir=str(workspace)))
        assert guard.resolve_readable_path(str(outside)) == os.path.abspath(outside)

    def test_local_reads_are_unchanged_when_workspace_is_unset(self, tmp_path):
        path = tmp_path / "input.json"
        guard = SafetyGuard(SafetyConstraints())
        assert guard.resolve_readable_path(str(path)) == guard.resolve_in_workspace(str(path))

    def test_unconfigured_confines_nothing(self):
        guard = SafetyGuard(SafetyConstraints())  # workspace_dir None
        # Build with os.sep: a literal "/anywhere/..." is already-normalised on
        # POSIX and normalises to backslashes on Windows, so hardcoding it would
        # assert the platform rather than the confinement. abspath, not the
        # input itself: on Windows an os.sep-rooted path is drive-relative and
        # anchoring it IS the fix (design/21 F6) — this asserts no confinement,
        # not no resolution.
        outside = os.path.join(os.sep, "anywhere", "at", "all.json")
        assert guard.resolve_in_workspace(outside) == os.path.abspath(outside)

    def test_an_unconfined_path_is_still_normalised(self):
        # A model that over-escapes a Windows path hands us doubled separators.
        # Windows collapses the repeats; POSIX does not. abspath keeps all of
        # normpath's respelling, so the design/19 separator cases still hold —
        # with anchored expectations (design/21 F6).
        # Not a *leading* double separator: POSIX gives that one a meaning of
        # its own and normpath rightly preserves it.
        guard = SafetyGuard(SafetyConstraints())
        assert guard.resolve_in_workspace(f"{os.sep}ws{os.sep*2}log.json") == (
            os.path.abspath(f"{os.sep}ws{os.sep}log.json")
        )
        assert guard.resolve_in_workspace(f"{os.sep}ws{os.sep}.{os.sep}log.json") == (
            os.path.abspath(f"{os.sep}ws{os.sep}log.json")
        )

    def test_an_unconfined_path_is_anchored_not_just_respelled(self):
        # normpath was lexical: it left '/tmp/x' drive-relative on Windows and a
        # relative path floating with whoever resolves it later, so the fix
        # design/19 built on it ran and could not have worked (design/21 F6).
        # The only spelling that matches the OS's resolution is the OS's
        # resolution.
        guard = SafetyGuard(SafetyConstraints())
        rooted = os.path.join(os.sep, "tmp", "grid_pixel_std.json")
        assert guard.resolve_in_workspace(rooted) == os.path.abspath(rooted)
        relative = os.path.join("results", "log.json")
        assert guard.resolve_in_workspace(relative) == os.path.abspath(relative)
        assert os.path.isabs(guard.resolve_in_workspace(relative))

    def test_a_filesystem_root_workspace_does_not_reject_everything(self):
        """realpath('/') already ends in a separator, so the containment check
        must not append another — `//` is a prefix of nothing, and the sandbox
        would fail closed on every path while reporting a traversal escape."""
        guard = SafetyGuard(SafetyConstraints(workspace_dir=os.sep))
        target = os.path.join(os.sep, "tmp", "x.json")
        # realpath, so /tmp -> /private/tmp on macOS; the point is it resolves.
        assert guard.resolve_in_workspace(target) == os.path.realpath(target)

    def test_a_sibling_of_the_root_name_is_still_refused(self, tmp_path):
        """/data must not admit /database."""
        root = tmp_path / "data"
        root.mkdir()
        (tmp_path / "database").mkdir()
        guard = SafetyGuard(SafetyConstraints(workspace_dir=str(root)))
        with pytest.raises(SafetyViolation, match="escapes"):
            guard.resolve_in_workspace(str(tmp_path / "database" / "x.json"))

    def test_path_inside_root_resolves(self, tmp_path):
        guard = SafetyGuard(SafetyConstraints(workspace_dir=str(tmp_path)))
        resolved = guard.resolve_in_workspace("sub/data.json")
        assert resolved == str((tmp_path / "sub" / "data.json").resolve())

    def test_dotdot_escape_refused(self, tmp_path):
        guard = SafetyGuard(SafetyConstraints(workspace_dir=str(tmp_path)))
        with pytest.raises(SafetyViolation, match="escapes"):
            guard.resolve_in_workspace("../secrets.json")

    def test_absolute_outside_refused(self, tmp_path):
        guard = SafetyGuard(SafetyConstraints(workspace_dir=str(tmp_path)))
        with pytest.raises(SafetyViolation, match="escapes"):
            guard.resolve_in_workspace("/etc/passwd")

    def test_symlink_escape_refused(self, tmp_path):
        import os
        outside = tmp_path.parent / "outside_target"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "link"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError) as e:
            # Windows without Developer Mode / admin can't create symlinks
            # (WinError 1314). The realpath guard still resolves symlinks at
            # runtime; we just can't set one up to exercise it here.
            pytest.skip(f"symlink creation not permitted on this platform: {e}")
        guard = SafetyGuard(SafetyConstraints(workspace_dir=str(tmp_path)))
        with pytest.raises(SafetyViolation, match="escapes"):
            guard.resolve_in_workspace("link/data.json")

    def test_from_yaml_loads_workspace_dir(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(f"workspace_dir: {tmp_path}\n", encoding="utf-8")
        constraints = _parse(str(cfg))
        assert constraints.workspace_dir == str(tmp_path)


def _components(resolved: str) -> tuple[str, ...]:
    """Path components of a resolved path, both separators, either platform."""
    return Path(resolved).parts


class TestHomeExpansion:
    """`~` is expanded, then confined — never carried through as a segment.

    Block 41b's M5 gate asked for `~/microclaw_data/multipos_3sites` and got
    `C:\\Users\\ries\\AppData\\Local\\microclaw\\~\\microclaw_data\\...`: a
    literal directory named `~` under the workspace root, because nothing in
    the package called expanduser and `~/x` is not absolute.

    Every assertion here checks the *whole* resolved string and that no
    component equals `~`. A prefix-only assertion would have passed on the
    defect — the defective path started with the workspace root too.
    """

    @pytest.fixture
    def home(self, tmp_path, monkeypatch):
        """A fake home directory, so nothing here depends on the real one."""
        home = tmp_path / "home" / "ries"
        home.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))          # POSIX
        monkeypatch.setenv("USERPROFILE", str(home))   # Windows
        return home

    def test_tilde_expands_when_no_workspace_is_configured(self, home):
        guard = SafetyGuard(SafetyConstraints())  # workspace_dir None
        expected = os.path.abspath(home / "data" / "run.json")
        for resolved in (
            guard.resolve_in_workspace("~/data/run.json"),
            guard.resolve_readable_path("~/data/run.json"),
        ):
            assert resolved == expected
            assert "~" not in _components(resolved)

    def test_the_operators_rig_path_lands_under_the_real_home(self, home):
        """The literal 41b case: forward slashes, on a machine whose separator
        may not be one. abspath respells; what must not survive is the `~`."""
        guard = SafetyGuard(SafetyConstraints())
        resolved = guard.resolve_in_workspace("~/microclaw_data/multipos_3sites")
        assert resolved == os.path.abspath(
            home / "microclaw_data" / "multipos_3sites"
        )
        assert "~" not in _components(resolved)
        assert resolved.startswith(os.path.abspath(home))

    def test_tilde_escaping_the_workspace_is_refused_naming_root_and_expansion(
        self, tmp_path, home
    ):
        """The defect wrote silently into root/~/...; a refusal is the fix, and
        it has to say what the `~` became or 'escapes' is unreadable."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        guard = SafetyGuard(SafetyConstraints(workspace_dir=str(workspace)))
        with pytest.raises(SafetyViolation) as exc:
            guard.resolve_in_workspace("~/microclaw_data/multipos_3sites")
        message = str(exc.value)
        assert "escapes the configured workspace directory" in message
        assert os.path.realpath(workspace) in message
        assert "expanded to" in message
        assert str(home) in message

    def test_tilde_inside_the_workspace_resolves(self, tmp_path, home):
        guard = SafetyGuard(SafetyConstraints(workspace_dir=str(tmp_path)))
        resolved = guard.resolve_in_workspace("~/microclaw_data/run")
        assert resolved == os.path.realpath(home / "microclaw_data" / "run")
        assert "~" not in _components(resolved)

    def test_local_reads_expand_but_stay_unconfined(self, tmp_path, home):
        """resolve_readable_path is deliberately unconfined and gets exactly the
        same normalisation: expansion is normalisation, not confinement."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        guard = SafetyGuard(SafetyConstraints(workspace_dir=str(workspace)))
        resolved = guard.resolve_readable_path("~/inputs/calibration.json")
        assert resolved == os.path.abspath(home / "inputs" / "calibration.json")
        assert "~" not in _components(resolved)

    def test_a_bare_tilde_is_the_home_directory(self, home):
        guard = SafetyGuard(SafetyConstraints())
        for resolved in (
            guard.resolve_in_workspace("~"),
            guard.resolve_readable_path("~"),
        ):
            assert resolved == os.path.abspath(home)
            assert "~" not in _components(resolved)

    def test_a_tilde_that_is_not_a_prefix_is_not_mangled(self, home):
        """Only a leading `~` means a home directory. `a/~b/c` is a path."""
        guard = SafetyGuard(SafetyConstraints())
        for raw in (
            os.path.join("a", "~b", "c"),
            os.path.join(os.sep, "data", "~tmp", "x.json"),
            os.path.join("a", "~", "c"),
        ):
            for resolved in (
                guard.resolve_in_workspace(raw),
                guard.resolve_readable_path(raw),
            ):
                assert resolved == os.path.abspath(raw)
                assert str(home) not in resolved

    def test_a_non_prefix_tilde_survives_inside_a_workspace(self, tmp_path, home):
        """A confined write to a directory the operator really did name `~b`."""
        guard = SafetyGuard(SafetyConstraints(workspace_dir=str(tmp_path)))
        resolved = guard.resolve_in_workspace(os.path.join("a", "~b", "c.json"))
        assert resolved == os.path.realpath(tmp_path / "a" / "~b" / "c.json")
        assert "~b" in _components(resolved)

    def test_an_unexpandable_tilde_is_refused_not_passed_through(
        self, tmp_path, monkeypatch
    ):
        """os.path.expanduser returns its input unchanged when it cannot resolve
        — on Windows, no USERPROFILE and no HOMEDRIVE+HOMEPATH. Passing that
        through is the original defect with a different cause, so refuse."""
        monkeypatch.setattr(os.path, "expanduser", lambda p: p)
        for guard in (
            SafetyGuard(SafetyConstraints()),
            SafetyGuard(SafetyConstraints(workspace_dir=str(tmp_path))),
        ):
            for resolve in (guard.resolve_in_workspace, guard.resolve_readable_path):
                with pytest.raises(SafetyViolation, match="no home directory"):
                    resolve("~/microclaw_data/run")

    @pytest.mark.skipif(
        os.name == "nt",
        reason="ntpath fabricates ~user from USERPROFILE's parent rather than failing",
    )
    def test_an_unknown_tilde_user_is_refused(self, home):
        guard = SafetyGuard(SafetyConstraints())
        for resolve in (guard.resolve_in_workspace, guard.resolve_readable_path):
            with pytest.raises(SafetyViolation, match="no home directory"):
                resolve("~no_such_user_microclaw/data")

    def test_a_workspace_root_written_with_a_tilde_is_expanded(self, home):
        """A config saying `workspace_dir: ~/data` confined everything to a `~`
        directory under the cwd — the same defect one level up."""
        guard = SafetyGuard(SafetyConstraints(workspace_dir="~/data"))
        resolved = guard.resolve_in_workspace("run/log.json")
        assert resolved == os.path.realpath(home / "data" / "run" / "log.json")
        assert "~" not in _components(resolved)


def _laser_guard(**overrides) -> SafetyGuard:
    ill = IlluminationConstraints(
        shutters=[
            IlluminationProperty(
                device="Luxx638", property="Laser Operation Select",
                on_value="On", off_value="Off",
            )
        ],
        power_properties=[
            ForbiddenProperty("Luxx638", "Laser Power Set-point Select [%]")
        ],
        max_power_percent=30.0,
        max_power_step_factor=3.0,
        **overrides,
    )
    return SafetyGuard(SafetyConstraints(illumination=ill))


class TestIlluminationGate:
    """design/14 §3: a Class-3B laser must not enable without a human 'y'."""

    def test_enable_without_confirm_fn_refused(self):
        guard = _laser_guard()
        with pytest.raises(SafetyViolation, match="declined"):
            guard.check_illumination(
                _core(), "Luxx638", "Laser Operation Select", "On"
            )

    def test_enable_declined_by_user_refused(self):
        guard = _laser_guard()
        with pytest.raises(SafetyViolation, match="declined"):
            guard.check_illumination(
                _core(), "Luxx638", "Laser Operation Select", "On",
                confirm_fn=lambda s, kind="action", subject=None: False,
            )

    def test_enable_confirmed_passes(self):
        guard = _laser_guard()
        guard.check_illumination(
            _core(), "Luxx638", "Laser Operation Select", "On",
            confirm_fn=lambda s, kind="action", subject=None: True,
        )

    def test_confirm_receives_the_illumination_kind(self):
        # design/21 F1: illumination renders differently in the browser. The
        # kind travels as an argument, not a prose prefix the frontend would
        # have to string-match against text safety.py is free to reword.
        guard = _laser_guard()
        seen = {}
        guard.check_illumination(
            _core(), "Luxx638", "Laser Operation Select", "On",
            confirm_fn=lambda s, kind="action", subject=None: seen.update(
                kind=kind, subject=subject
            ) or True,
        )
        assert seen["kind"] == "illumination"
        assert seen["subject"] == "enable"

    def test_disable_never_needs_confirmation(self):
        guard = _laser_guard()
        guard.check_illumination(
            _core(), "Luxx638", "Laser Operation Select", "Off"
        )  # no confirm_fn supplied, still fine

    def test_third_shutter_value_without_confirm_fn_is_refused(self):
        guard = _laser_guard()
        with pytest.raises(SafetyViolation, match="declined"):
            guard.check_illumination(
                _core(), "Luxx638", "Laser Operation Select", "Auto"
            )

    def test_third_shutter_value_confirmed_passes(self):
        guard = _laser_guard()
        guard.check_illumination(
            _core(), "Luxx638", "Laser Operation Select", "Auto",
            confirm_fn=lambda s, kind="action", subject=None: True,
        )

    def test_confirm_not_required_when_flag_off(self):
        guard = _laser_guard(require_confirm_on_enable=False)
        guard.check_illumination(
            _core(), "Luxx638", "Laser Operation Select", "On"
        )

    def test_unrelated_property_untouched(self):
        guard = _laser_guard()
        guard.check_illumination(_core(), "DCam", "Gain", "5")

    def test_power_above_cap_refused(self):
        guard = _laser_guard()
        core = _core()
        core.get_property.return_value = "10.0"
        with pytest.raises(SafetyViolation, match="max_power_percent"):
            guard.check_illumination(
                core, "Luxx638", "Laser Power Set-point Select [%]", "50.0"
            )

    def test_power_ratchet_refuses_25x_jump(self):
        # The amr_test escalation: 1% -> 25% in one write is 25x > 3x.
        guard = _laser_guard()
        core = _core()
        core.get_property.return_value = "1.0"
        with pytest.raises(SafetyViolation, match="ratchet"):
            guard.check_illumination(
                core, "Luxx638", "Laser Power Set-point Select [%]", "25.0"
            )

    def test_power_gradual_increase_allowed(self):
        guard = _laser_guard()
        core = _core()
        core.get_property.return_value = "5.0"
        guard.check_illumination(
            core, "Luxx638", "Laser Power Set-point Select [%]", "10.0"
        )

    def test_power_decrease_always_allowed(self):
        guard = _laser_guard()
        core = _core()
        core.get_property.return_value = "25.0"
        guard.check_illumination(
            core, "Luxx638", "Laser Power Set-point Select [%]", "1.0"
        )

    def test_shutter_all_drives_off_values(self):
        guard = _laser_guard()
        core = _core()
        done = guard.shutter_all(core)
        core.set_property.assert_called_once_with(
            "Luxx638", "Laser Operation Select", "Off"
        )
        assert done == ["Luxx638.Laser Operation Select"]

    def test_shutter_all_swallows_hardware_errors(self):
        guard = _laser_guard()
        core = _core()
        core.set_property.side_effect = RuntimeError("device unplugged")
        assert guard.shutter_all(core) == []  # must not raise

    def test_declared_illumination_state_reports_values_and_read_failures(self):
        guard = SafetyGuard(SafetyConstraints(illumination=IlluminationConstraints(
            shutters=[
                IlluminationProperty("Source", "Enable", off_value="0"),
                IlluminationProperty("Aggregate", "Gate", off_value="closed"),
            ]
        )))
        core = _core()
        def read(device, prop):
            if device == "Aggregate":
                raise RuntimeError("unavailable")
            return "1"
        core.get_property.side_effect = read
        assert guard.declared_illumination_state(core) == [
            {"device": "Source", "property": "Enable", "off_value": "0", "value": "1"},
            {"device": "Aggregate", "property": "Gate", "off_value": "closed",
             "error": "RuntimeError: unavailable"},
        ]

    def test_from_yaml_loads_illumination(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "illumination:\n"
            "  require_confirm_on_enable: true\n"
            "  max_power_percent: 30.0\n"
            "  max_power_step_factor: 3.0\n"
            "  shutters:\n"
            "    - {device: Luxx638, property: Laser Operation Select}\n"
            "  power_properties:\n"
            "    - {device: Luxx638, property: 'Laser Power Set-point Select [%]'}\n"
        , encoding="utf-8")
        c = _parse(str(cfg))
        assert c.illumination.max_power_percent == 30.0
        assert c.illumination.shutters[0].device == "Luxx638"
        assert c.illumination.shutters[0].on_value == "On"  # default
        assert c.illumination.power_properties[0].property == (
            "Laser Power Set-point Select [%]"
        )

    def test_defaults_when_absent(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text("stage:\n  z_min: 0.0\n", encoding="utf-8")
        c = _parse(str(cfg))
        assert c.illumination.shutters == []
        assert c.illumination.require_confirm_on_enable is False


class TestNamedStageLimits:
    def test_no_entry_fails_closed(self):
        guard = SafetyGuard(SafetyConstraints())
        with pytest.raises(SafetyViolation, match="No limits configured"):
            guard.check_named_stage("TIRF Stage", 100.0)

    def test_within_limits_passes(self):
        guard = SafetyGuard(SafetyConstraints(
            named_stages=[NamedStageLimits("TIRF Stage", -3000.0, 3000.0)]
        ))
        guard.check_named_stage("TIRF Stage", 2999.0)

    def test_beyond_max_refused(self):
        guard = SafetyGuard(SafetyConstraints(
            named_stages=[NamedStageLimits("TIRF Stage", -3000.0, 3000.0)]
        ))
        with pytest.raises(SafetyViolation, match="maximum"):
            guard.check_named_stage("TIRF Stage", 3000.5)

    def test_below_min_refused(self):
        guard = SafetyGuard(SafetyConstraints(
            named_stages=[NamedStageLimits("PIZStage", 0.0, 200.0)]
        ))
        with pytest.raises(SafetyViolation, match="below"):
            guard.check_named_stage("PIZStage", -1.0)

    def test_from_yaml_loads_named_stages(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "named_stages:\n"
            "  - {device: PIZStage, min_um: 0.0, max_um: 200.0}\n"
        , encoding="utf-8")
        c = _parse(str(cfg))
        assert c.named_stages == [NamedStageLimits("PIZStage", 0.0, 200.0)]


class TestPluginGates:
    def test_analyzer_allowed_by_default(self):
        guard = SafetyGuard(SafetyConstraints())
        guard.check_plugin("org.lab.QualityScorePlugin")  # no exception

    def test_blocked_analyzer_denied(self):
        guard = SafetyGuard(
            SafetyConstraints(plugins=PluginConstraints(blocked=["org.bad.Plugin"]))
        )
        with pytest.raises(SafetyViolation, match="blocked"):
            guard.check_plugin("org.bad.Plugin")

    def test_motion_denied_by_default(self):
        guard = SafetyGuard(SafetyConstraints())
        with pytest.raises(SafetyViolation, match="allow_hardware_motion"):
            guard.check_plugin_motion("autofocus:<active>")

    def test_motion_allowed_when_flag_set(self):
        guard = SafetyGuard(
            SafetyConstraints(plugins=PluginConstraints(allow_hardware_motion=True))
        )
        guard.check_plugin_motion("autofocus:<active>")  # no exception

    def test_motion_still_respects_blocklist(self):
        guard = SafetyGuard(
            SafetyConstraints(
                plugins=PluginConstraints(
                    blocked=["org.bad.Focus"], allow_hardware_motion=True
                )
            )
        )
        with pytest.raises(SafetyViolation, match="blocked"):
            guard.check_plugin_motion("org.bad.Focus")
