from types import SimpleNamespace

import pytest

from microclaw.authorization import (
    AuthorizationEntry,
    AuthorizationMap,
    ChannelPlanPartialApplicationError,
    RigAuthorizationError,
    _expansion_hash,
)
from microclaw.safety import (
    ForbiddenProperty,
    IlluminationConstraints,
    IlluminationProperty,
    SafetyConstraints,
    SafetyGuard,
    SafetyViolation,
)
from microclaw.tools import list_config_groups, set_config_preset


class BridgeStrings:
    def __init__(self, values):
        self.values = values

    def size(self):
        return len(self.values)

    def get(self, index):
        return self.values[index]


class ConfigCore:
    def __init__(self):
        self.groups = {"Camera": ["Fast", "Precise"], "Broken": None, "Channel": ["DAPI"]}
        self.current = {"Camera": "Fast", "Broken": "", "Channel": ""}
        self.effects = {
            ("Camera", "Fast"): [("Cam", "Mode", "Fast"), ("Cam", "Gain", "7")],
            ("Camera", "Precise"): [("Cam", "Mode", "Precise")],
            ("Channel", "DAPI"): [("Wheel", "Label", "DAPI")],
        }
        self.values = {("Cam", "Mode"): "old", ("Cam", "Gain"): "1", ("Wheel", "Label"): "old"}
        self.calls = []
        self.fail_on = None
        self.write_count = 0

    def get_available_config_groups(self):
        return BridgeStrings(list(self.groups))

    def get_available_configs(self, group):
        if self.groups[group] is None:
            raise RuntimeError("preset enumeration failed")
        return BridgeStrings(self.groups[group])

    def get_current_config(self, group):
        return self.current[group]

    def get_config_data(self, group, preset):
        return [{"device": d, "property": p, "value": v}
                for d, p, v in self.effects[(group, preset)]]

    def get_property(self, device, prop):
        return self.values[(device, prop)]

    def get_property_type(self, device, prop):
        return "String"

    def set_property(self, device, prop, value):
        self.write_count += 1
        self.calls.append(("set", device, prop, str(value)))
        if self.fail_on == self.write_count:
            raise RuntimeError("injected write failure")
        self.values[(device, prop)] = str(value)

    def wait_for_device(self, device):
        self.calls.append(("wait", device))

    def get_camera_device(self): return "OtherCamera"
    def get_focus_device(self): return "Z"
    def get_xy_stage_device(self): return "XY"


def config_controller(core, classes):
    effects = core.effects[("Camera", "Fast")]
    report = AuthorizationMap(
        "guaranteed", "complete", True,
        entries=[AuthorizationEntry("config-preset:Camera:Fast", kind, d, p)
                 for (d, p), kind in classes.items()],
        authorized_presets=frozenset(),
        channel_expansion_hashes={},
    )
    refreshes = []
    return SimpleNamespace(core=core, authorization_map=report,
                           refresh_gui=lambda: refreshes.append("refresh"),
                           refreshes=refreshes)


def categorical_guard(*pairs, shutters=()):
    return SafetyGuard(SafetyConstraints(
        allowed_properties=[ForbiddenProperty(*pair) for pair in pairs],
        illumination=IlluminationConstraints(
            shutters=[IlluminationProperty(*item) for item in shutters],
            require_confirm_on_enable=True,
        ),
    ))


def test_list_config_groups_reports_groups_active_unknown_and_group_failure():
    core = ConfigCore()
    result = list_config_groups(SimpleNamespace(core=core), categorical_guard())

    groups = {item["name"]: item for item in result["groups"]}
    assert groups["Camera"] == {
        "name": "Camera", "presets": ["Fast", "Precise"], "active_preset": "Fast"
    }
    assert groups["Channel"]["active_preset"] is None
    assert groups["Broken"]["presets"] == []
    assert "preset enumeration failed" in groups["Broken"]["error"]


def test_list_config_groups_describes_one_preset_in_one_call():
    core = ConfigCore()
    result = list_config_groups(
        SimpleNamespace(core=core), categorical_guard(), group="Camera", preset="Fast"
    )
    assert result["settings"] == [
        {"device": "Cam", "property": "Mode", "value": "Fast"},
        {"device": "Cam", "property": "Gain", "value": "7"},
    ]


def test_unknown_preset_name_is_not_reported_as_a_hardware_fault():
    # Demo gate, 2026-08-14, verbatim: asking for "20x" when the preset is "20X"
    # produced this Java error, and both the message and the hint pointed at the
    # hardware -- "device busy, stage at limit, device not found" -- for a
    # casing typo. errors.py's own rule is that a hint naming the wrong
    # subsystem is worse than none. Message and hint are asserted together
    # because fixing only the message leaves the two contradicting each other.
    from microclaw.errors import hint_for_error, humanize_java_error

    exc = Exception(
        'java.lang.Exception: Configuration group "Objective" or its preset '
        '"20x" does not exist'
    )
    message, hint = humanize_java_error(exc), hint_for_error(exc)
    assert "case-sensitive" in message and "list_config_groups" in message
    assert "naming error" in hint and "case-sensitive" in hint
    for text in (message, hint):
        assert "stage at limit" not in text
        assert "device busy" not in text


def test_describing_one_preset_does_not_also_re_walk_every_group():
    # Demo gate, 2026-08-14: asking for one preset's membership returned the
    # whole listing too, re-walking every group over a serialized bridge -- 13
    # round trips on that rig to answer a three-field question. The two shapes
    # are exclusive; this asserts the round trips are not taken, not merely that
    # the key is absent.
    core = ConfigCore()
    walked = []
    core.get_available_config_groups = lambda: walked.append("enumerated") or []
    result = list_config_groups(
        SimpleNamespace(core=core), categorical_guard(), group="Camera", preset="Fast"
    )
    assert walked == []
    assert "groups" not in result
    assert result["group"] == "Camera" and result["preset"] == "Fast"


def test_non_channel_preset_applies_and_verifies_every_effect():
    core = ConfigCore()
    pairs = (("Cam", "Mode"), ("Cam", "Gain"))
    ctrl = config_controller(core, {pair: "reviewed_categorical_property" for pair in pairs})
    result = set_config_preset(ctrl, categorical_guard(*pairs), "Camera", "Fast")
    assert result["writes"] == 2
    assert result["config_group"] == "Camera"
    assert core.values[("Cam", "Mode")] == "Fast"
    assert core.values[("Cam", "Gain")] == "7"


def test_non_channel_preset_rolls_back_in_reverse_on_write_failure():
    core = ConfigCore()
    pairs = (("Cam", "Mode"), ("Cam", "Gain"))
    ctrl = config_controller(core, {pair: "reviewed_categorical_property" for pair in pairs})
    core.fail_on = 2
    with pytest.raises(ChannelPlanPartialApplicationError):
        set_config_preset(ctrl, categorical_guard(*pairs), "Camera", "Fast")
    sets = [call for call in core.calls if call[0] == "set"]
    # This fake raises before assigning, so the diagnostic read observes the
    # saved original; that positive observation makes a Gain restore unnecessary.
    assert sets == [
        ("set", "Cam", "Mode", "Fast"),
        ("set", "Cam", "Gain", "7"),
        ("set", "Cam", "Mode", "old"),
    ]
    assert sets.count(("set", "Cam", "Gain", "1")) == 0
    assert core.values[("Cam", "Mode")] == "old"
    assert core.values[("Cam", "Gain")] == "1"


def test_illumination_preset_is_gated_on_a_session_with_no_authorization_map(
    monkeypatch,
):
    # Review round 1: set_config_preset had a map-less delegation branch copied
    # from set_channel, which called core.set_config directly. Measured on this
    # exact fixture, it applied a laser enable with ZERO confirmations and
    # returned success. The branch is gone; the executor is the only route and
    # gates identically with no map attached. Written failing-first against the
    # original implementation, where the confirmation list came back empty.
    core = ConfigCore()
    core.effects[("Danger", "AllOn")] = [("Laser", "Enable", "1")]
    core.values[("Laser", "Enable")] = "0"
    ctrl = config_controller(core, {("Laser", "Enable"): "built_in_typed_capability"})
    ctrl.authorization_map = None          # the session this branch existed for
    guard = categorical_guard(shutters=[("Laser", "Enable", "1", "0")])
    confirmations = []
    monkeypatch.setattr(
        "microclaw.tools.CONFIRM_FN",
        lambda text, kind, **kw: confirmations.append(kind) or False,
    )
    with pytest.raises(SafetyViolation, match="declined"):
        set_config_preset(ctrl, guard, "Danger", "AllOn")
    assert confirmations == ["illumination"]
    assert core.calls == []
    assert core.values[("Laser", "Enable")] == "0"


def test_non_channel_illumination_decline_applies_nothing(monkeypatch):
    core = ConfigCore()
    core.effects[("Camera", "Fast")] = [("Laser", "Enable", "1")]
    core.values[("Laser", "Enable")] = "0"
    ctrl = config_controller(core, {("Laser", "Enable"): "built_in_typed_capability"})
    guard = categorical_guard(shutters=[("Laser", "Enable", "1", "0")])
    confirmations = []
    monkeypatch.setattr(
        "microclaw.tools.CONFIRM_FN",
        lambda text, kind, **kw: confirmations.append(kind) or False,
    )
    with pytest.raises(SafetyViolation, match="declined"):
        set_config_preset(ctrl, guard, "Camera", "Fast")
    assert confirmations == ["illumination"]
    assert core.calls == []
    assert core.values[("Laser", "Enable")] == "0"


@pytest.mark.parametrize("classification", [None, "excluded"])
def test_non_channel_unclassified_or_excluded_effect_names_property(classification):
    core = ConfigCore()
    classes = {} if classification is None else {("Cam", "Mode"): classification}
    ctrl = config_controller(core, classes)
    with pytest.raises(RigAuthorizationError, match=r"Cam\.Mode"):
        set_config_preset(ctrl, categorical_guard(("Cam", "Mode")), "Camera", "Fast")
    assert core.calls == []
