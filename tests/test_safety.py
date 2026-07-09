from unittest.mock import MagicMock

import pytest
from microclaw.safety import (
    CameraConstraints,
    ForbiddenProperty,
    IlluminationConstraints,
    IlluminationProperty,
    NamedStageLimits,
    PluginConstraints,
    SafetyConstraints,
    SafetyGuard,
    SafetyViolation,
    StageConstraints,
)


def _core(x=0.0, y=0.0):
    core = MagicMock()
    core.get_focus_device.return_value = "DStage"
    core.get_camera_device.return_value = "DCam"
    core.get_xy_stage_device.return_value = "DXYStage"
    core.get_x_position.return_value = x
    core.get_y_position.return_value = y
    return core


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
    def test_loads_yaml(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "stage:\n  z_min: 5.0\n  z_max: 100.0\n"
            "camera:\n  max_exposure_ms: 500\n"
        )
        constraints = SafetyConstraints.from_yaml(str(cfg))
        guard = SafetyGuard(constraints)
        with pytest.raises(SafetyViolation):
            guard.check_z(0.0)
        with pytest.raises(SafetyViolation):
            guard.check_exposure(600.0)

    def test_empty_yaml_gives_no_constraints(self, tmp_path):
        cfg = tmp_path / "empty.yaml"
        cfg.write_text("")
        constraints = SafetyConstraints.from_yaml(str(cfg))
        guard = SafetyGuard(constraints)
        guard.check_z(999999.0)  # no exception

    def test_forbidden_property_loaded(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "forbidden_properties:\n"
            "  - device: Core\n"
            "    property: Initialize\n"
        )
        constraints = SafetyConstraints.from_yaml(str(cfg))
        guard = SafetyGuard(constraints)
        with pytest.raises(SafetyViolation, match="forbidden"):
            guard.check_property("Core", "Initialize")

    def test_channels_loaded(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text("channels:\n  allowed: [DAPI, FITC]\n")
        constraints = SafetyConstraints.from_yaml(str(cfg))
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
        )
        constraints = SafetyConstraints.from_yaml(str(cfg))
        assert constraints.plugins.blocked == ["org.example.KnownBadPlugin"]
        assert constraints.plugins.allow_hardware_motion is True

    def test_plugins_default_when_absent(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text("stage:\n  z_min: 0.0\n")
        constraints = SafetyConstraints.from_yaml(str(cfg))
        assert constraints.plugins.blocked == []
        assert constraints.plugins.allow_hardware_motion is False


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

    def test_from_yaml_loads_allowed_properties(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "allowed_properties:\n"
            "  - device: DCam\n"
            "    property: Binning\n"
        )
        constraints = SafetyConstraints.from_yaml(str(cfg))
        assert constraints.allowed_properties == [ForbiddenProperty("DCam", "Binning")]
        guard = SafetyGuard(constraints)
        with pytest.raises(SafetyViolation):
            guard.check_property("DStage", "Position")


class TestWorkspaceSandbox:
    def test_unconfigured_returns_path_unchanged(self):
        guard = SafetyGuard(SafetyConstraints())  # workspace_dir None
        assert guard.resolve_in_workspace("/anywhere/at/all.json") == "/anywhere/at/all.json"

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
        cfg.write_text(f"workspace_dir: {tmp_path}\n")
        constraints = SafetyConstraints.from_yaml(str(cfg))
        assert constraints.workspace_dir == str(tmp_path)


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
                confirm_fn=lambda s: False,
            )

    def test_enable_confirmed_passes(self):
        guard = _laser_guard()
        guard.check_illumination(
            _core(), "Luxx638", "Laser Operation Select", "On",
            confirm_fn=lambda s: True,
        )

    def test_disable_never_needs_confirmation(self):
        guard = _laser_guard()
        guard.check_illumination(
            _core(), "Luxx638", "Laser Operation Select", "Off"
        )  # no confirm_fn supplied, still fine

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
        )
        c = SafetyConstraints.from_yaml(str(cfg))
        assert c.illumination.max_power_percent == 30.0
        assert c.illumination.shutters[0].device == "Luxx638"
        assert c.illumination.shutters[0].on_value == "On"  # default
        assert c.illumination.power_properties[0].property == (
            "Laser Power Set-point Select [%]"
        )

    def test_defaults_when_absent(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text("stage:\n  z_min: 0.0\n")
        c = SafetyConstraints.from_yaml(str(cfg))
        assert c.illumination.shutters == []
        assert c.illumination.require_confirm_on_enable is True


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
        )
        c = SafetyConstraints.from_yaml(str(cfg))
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
