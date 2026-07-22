from types import SimpleNamespace

import pytest

from microclaw.authorization import (
    RigAuthorizationError,
    authorize_channel,
    authorize_property_write,
    validate_live_rig,
)
from microclaw.safety import (
    ActuatorId,
    CameraConstraints,
    ParsedSafetyConfig,
    PluginConstraints,
    RangeEdge,
    RangePolicy,
    RigProfile,
    SafetyConstraints,
)


class Core:
    def __init__(self):
        self.xy = "XY"
        self.focus = "Z"
        self.camera = "Cam"
        self.presets = {}

    def get_xy_stage_device(self):
        return self.xy

    def get_focus_device(self):
        return self.focus

    def get_camera_device(self):
        return self.camera

    def get_available_configs(self, group):
        assert group == "Channel"
        return list(self.presets)

    def get_loaded_devices(self):
        return [self.xy, self.focus, self.camera, "ReadOnlySensor", "SecondZ"]

    def get_config_data(self, group, preset):
        assert group == "Channel"
        return self.presets[preset]


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
    plugin_motion=False, exposure=100.0,
):
    if ranges is None:
        ranges = {
            ActuatorId("core_xy", None, "stage-position", "x"): policy(),
            ActuatorId("core_xy", None, "stage-position", "y"): policy(),
            ActuatorId("core_focus", None, "stage-position", "z"): policy(0, 100),
        }
    constraints = SafetyConstraints(
        camera=CameraConstraints(exposure),
        allowed_channels=list(channels),
        plugins=PluginConstraints(allow_hardware_motion=plugin_motion),
    )
    return ParsedSafetyConfig(
        constraints,
        ranges,
        RigProfile(mode, frozenset(categorical), frozenset(excluded)),
    )


def test_complete_map_covers_dedicated_autofocus_and_acquisition_paths():
    report = validate_live_rig(Controller(), parsed())
    assert report.complete is True
    paths = {entry.path for entry in report.entries}
    assert {"dedicated-stage", "dedicated-exposure", "autofocus-and-acquisition",
            "acquisition"} <= paths
    inventory = {
        entry.device for entry in report.entries
        if entry.path == "connected-device-inventory"
    }
    assert inventory == {"ReadOnlySensor", "SecondZ"}


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


def test_open_reachable_edge_fails_only_in_guaranteed_mode():
    ranges = parsed().ranges.copy()
    ranges[ActuatorId("core_focus", None, "stage-position", "z")] = policy(0, None)
    with pytest.raises(RigAuthorizationError, match="open range edge"):
        validate_live_rig(Controller(), parsed(ranges=ranges))
    report = validate_live_rig(
        Controller(), parsed(ranges=ranges, mode="degraded_trusted_plugins")
    )
    assert report.complete is None and "suspended" in report.verdict


def test_live_core_and_named_identity_conflict_is_rejected():
    ranges = parsed().ranges.copy()
    ranges[ActuatorId("named", "Z", "stage-position", None)] = policy()
    with pytest.raises(RigAuthorizationError, match="Core/named"):
        validate_live_rig(Controller(), parsed(ranges=ranges))


def test_known_continuous_raw_property_cannot_be_declared_categorical():
    with pytest.raises(RigAuthorizationError, match="known continuous actuator"):
        validate_live_rig(Controller(), parsed(categorical={("Z", "Position")}))


def test_preset_path_still_catches_device_when_generic_path_is_not_allowed():
    core = Core()
    core.presets["Unsafe"] = [
        {"device": "SecondZ", "property": "Position", "value": "50"}
    ]
    with pytest.raises(RigAuthorizationError, match="SecondZ.Position is unclassified"):
        validate_live_rig(Controller(core), parsed(channels=["Unsafe"]))


def test_fully_reviewed_categorical_preset_is_authorized_and_runtime_gated():
    core = Core()
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
    with pytest.raises(RigAuthorizationError, match="excluded"):
        authorize_property_write(ctrl, "Wheel", "Speed")


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


def test_opaque_motion_plugin_requires_degraded_mode():
    with pytest.raises(RigAuthorizationError, match="Opaque hardware-motion"):
        validate_live_rig(Controller(), parsed(plugin_motion=True))
    report = validate_live_rig(
        Controller(), parsed(plugin_motion=True, mode="degraded_trusted_plugins")
    )
    assert report.complete is None
    assert any(entry.classification == "trusted_degraded" for entry in report.entries)


def test_cli_and_web_validate_before_prompt_or_session_exposure(monkeypatch):
    from microclaw import __main__ as cli
    from microclaw import webserve

    incomplete = parsed(ranges={})
    prompted = []
    monkeypatch.setattr(cli, "load_safety_config_or_exit", lambda path: incomplete)
    monkeypatch.setattr(cli, "MicroscopeController", lambda port, guard: Controller())
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
