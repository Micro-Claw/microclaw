import threading
from types import SimpleNamespace

import pytest

from microclaw.authorization import (
    AuthorizationEntry, AuthorizationMap, ChannelPlanPartialApplicationError,
    ChannelPlanSafeStateError, RigAuthorizationError, _expansion_hash,
    execute_channel_plan,
)
from microclaw.tools import set_device_property
from microclaw.safety import (
    CameraConstraints, ForbiddenProperty, IlluminationConstraints,
    IlluminationProperty, SafetyConstraints, SafetyGuard, SafetyViolation, TypedActuatorId,
    TypedActuatorPolicy,
)


DEMO_CHANNEL_SHAPES = {
    "Cy5": [("Dichroic", "Label", "400DCLP"), ("Emission", "Label", "Chroma-HQ700"),
            ("Excitation", "Label", "Chroma-HQ570"), ("Core", "Shutter", "White Light Shutter")],
    "DAPI": [("Dichroic", "Label", "400DCLP"), ("Emission", "Label", "Chroma-HQ620"),
             ("Excitation", "Label", "Chroma-D360"), ("Core", "Shutter", "White Light Shutter")],
    "FITC": [("Dichroic", "Label", "Q505LP"), ("Emission", "Label", "Chroma-HQ535"),
             ("Excitation", "Label", "Chroma-HQ480"), ("Core", "Shutter", "White Light Shutter")],
    "Rhodamine": [("Dichroic", "Label", "Q585LP"), ("Emission", "Label", "Chroma-HQ700"),
                  ("Excitation", "Label", "Chroma-HQ570"), ("Core", "Shutter", "White Light Shutter")],
    "Channel-Multiband": [("Dichroic", "Label", "89402bs"),
                          ("Emission", "Label", "89402m"),
                          ("Excitation", "Label", "Empty"),
                          ("LED", "Label", "385nm"),
                          ("Core", "Shutter", "LED Shutter")],
}


class Core:
    def __init__(self, effects, values=None):
        self.effects = list(effects)
        self.values = dict(values or {(d, p): "old" for d, p, _ in effects})
        self.types, self.calls = {}, []
        self.fail_on = self.fail_rollback = None
        self.write_count = 0

    def get_config_data(self, group, preset):
        assert group == "Channel"
        return [{"device": d, "property": p, "value": v} for d, p, v in self.effects]
    def get_property(self, d, p): return self.values[(d, p)]
    def get_property_type(self, d, p): return self.types.get((d, p), "String")
    def set_property(self, d, p, v):
        self.write_count += 1
        self.calls.append(("set", d, p, str(v)))
        if self.fail_on == self.write_count: raise RuntimeError("injected write failure")
        if self.fail_rollback == (d, p, str(v)): raise RuntimeError("injected rollback failure")
        self.values[(d, p)] = str(v)
    def wait_for_device(self, d): self.calls.append(("wait", d))
    def get_camera_device(self): return "Camera"
    def get_focus_device(self): return "Z"
    def get_xy_stage_device(self): return "XY"
    def get_x_position(self): return float(self.values.get(("XY", "X"), 0))
    def get_y_position(self): return float(self.values.get(("XY", "Y"), 0))


def make_guard(*, categorical=(), shutters=(), exposure=100, typed=None):
    result = SafetyGuard(SafetyConstraints(
        camera=CameraConstraints(exposure), allowed_channels=["P"],
        allowed_properties=[ForbiddenProperty(d, p) for d, p in categorical],
        illumination=IlluminationConstraints(
            shutters=[IlluminationProperty(*item) for item in shutters],
            require_confirm_on_enable=True, max_power_percent=100,
            max_power_step_factor=3),
    ))
    result.admit_typed_actuators(typed or {})
    return result


def controller(core, classes, startup=None):
    effects = tuple(core.effects)
    report = AuthorizationMap(
        "guaranteed", "complete", True,
        entries=[AuthorizationEntry("channel-preset:P", kind, d, p)
                 for (d, p), kind in classes.items()],
        authorized_presets=frozenset({"P"}),
        channel_expansion_hashes={"P": startup or _expansion_hash(effects)},
    )
    return SimpleNamespace(core=core, authorization_map=report)


def categorical_plan(effects, values=None):
    core = Core(effects, values)
    pairs = [(d, p) for d, p, _ in effects]
    return core, controller(core, {pair: "reviewed_categorical_property" for pair in pairs}), make_guard(categorical=pairs)


def test_categorical_order_wait_and_exact_verification():
    core, ctrl, guard = categorical_plan(
        [("Wheel", "Label", "DAPI"), ("Path", "State", "1")])
    out = execute_channel_plan(ctrl, guard, "P")
    assert out["writes"] == 2 and not out["expansion_drift"]
    assert core.calls == [("set", "Wheel", "Label", "DAPI"), ("wait", "Wheel"),
                          ("set", "Path", "State", "1"), ("wait", "Path")]


def test_auto_state_device_categorical_route():
    core = Core([("Wheel", "Label", "DAPI")])
    guard = make_guard()
    guard.admit_auto_classified({("Wheel", "Label")})
    ctrl = controller(core, {("Wheel", "Label"): "reviewed_categorical_property"})
    execute_channel_plan(ctrl, guard, "P")


def test_float_reformat_is_equal_and_wrong_value_fails():
    core = Core([("Camera", "Exposure", "10")], {("Camera", "Exposure"): "5.0000"})
    core.types[("Camera", "Exposure")] = "Float"
    raw_set = core.set_property
    def formatted(d, p, v): raw_set(d, p, v); core.values[(d, p)] = f"{float(v):.4f}"
    core.set_property = formatted
    ctrl = controller(core, {("Camera", "Exposure"): "built_in_typed_capability"})
    execute_channel_plan(ctrl, make_guard(exposure=20), "P")
    def wrong(d, p, v): raw_set(d, p, v); core.values[(d, p)] = "11.0000"
    core.set_property = wrong
    with pytest.raises(ChannelPlanPartialApplicationError, match="Read-back"):
        execute_channel_plan(ctrl, make_guard(exposure=20), "P")


@pytest.mark.parametrize("failure", [1, 2, 3])
def test_failure_after_each_position_rolls_back_and_stops(failure):
    effects = [(f"D{i}", "Label", f"new{i}") for i in range(3)]
    originals = {(d, p): f"old{i}" for i, (d, p, _) in enumerate(effects)}
    core, ctrl, guard = categorical_plan(effects, originals)
    core.fail_on = failure
    with pytest.raises(ChannelPlanPartialApplicationError, match="rolled_back"):
        execute_channel_plan(ctrl, guard, "P")
    assert core.values == originals
    assert all(("set", f"D{i}", "Label", f"new{i}") not in core.calls
               for i in range(failure, 3))


def test_failing_rollback_reports_unverified_safe_state():
    effects = [("A", "Label", "new"), ("B", "Label", "new")]
    core, ctrl, guard = categorical_plan(
        effects, {("A", "Label"): "old", ("B", "Label"): "old"})
    core.fail_on, core.fail_rollback = 2, ("A", "Label", "old")
    with pytest.raises(ChannelPlanSafeStateError, match="SAFE STATE NOT VERIFIED"):
        execute_channel_plan(ctrl, guard, "P")


def test_cancellation_between_writes_rolls_back():
    effects = [("A", "Label", "new"), ("B", "Label", "new")]
    core, ctrl, guard = categorical_plan(
        effects, {("A", "Label"): "old", ("B", "Label"): "old"})
    event, wait = threading.Event(), core.wait_for_device
    def cancel_after_wait(device): wait(device); event.set()
    core.wait_for_device = cancel_after_wait
    with pytest.raises(ChannelPlanPartialApplicationError, match="cancelled between writes"):
        execute_channel_plan(ctrl, guard, "P", cancel=event)
    assert core.values == {("A", "Label"): "old", ("B", "Label"): "old"}


def test_drift_is_reported_but_fresh_unsafe_expansion_is_refused():
    core, ctrl, guard = categorical_plan([("Wheel", "Label", "new")])
    ctrl.authorization_map.channel_expansion_hashes["P"] = "0" * 64
    assert execute_channel_plan(ctrl, guard, "P")["expansion_drift"] is True
    core.effects = [("Unknown", "Power", "100")]
    core.values[("Unknown", "Power")] = "0"
    with pytest.raises(RigAuthorizationError, match="unclassified") as exc:
        execute_channel_plan(ctrl, guard, "P")
    assert "property_authorization.allowed_categorical" in str(exc.value)
    assert "property_authorization.allowed_numeric" in str(exc.value)


def test_typed_continuous_and_stage_routing():
    identity = TypedActuatorId("Piezo", "Position (um)")
    typed = {identity: TypedActuatorPolicy("absolute-position", "um", 0, 20, None)}
    effects = [("Piezo", "Position (um)", "10"), ("Z", "Position", "50")]
    core = Core(effects, {(d, p): "1" for d, p, _ in effects})
    core.types = {(d, p): "Float" for d, p, _ in effects}
    ctrl = controller(core, {(d, p): "typed_continuous_actuator" for d, p, _ in effects})
    execute_channel_plan(ctrl, make_guard(typed=typed), "P")


def test_illumination_and_shutter_retarget_each_confirm():
    effects = [("Core", "Shutter", "LED Shutter"), ("LED", "Enable", "1")]
    core = Core(effects)
    ctrl = controller(core, {(d, p): "built_in_typed_capability" for d, p, _ in effects})
    guard = make_guard(shutters=[("LED Shutter", "State", "1", "0"),
                                 ("LED", "Enable", "1", "0")])
    confirmations = []
    execute_channel_plan(ctrl, guard, "P",
                         confirm_fn=lambda text, kind: confirmations.append((text, kind)) or True)
    assert len(confirmations) == 2 and {kind for _, kind in confirmations} == {"illumination"}


def test_illumination_power_routes_through_cap_and_ratchet():
    core = Core([("Laser", "Power", "20")], {("Laser", "Power"): "10"})
    core.types[("Laser", "Power")] = "Float"
    guard = make_guard()
    guard._c.illumination.power_properties = [ForbiddenProperty("Laser", "Power")]
    ctrl = controller(core, {("Laser", "Power"): "built_in_typed_capability"})
    execute_channel_plan(ctrl, guard, "P")
    core.effects = [("Laser", "Power", "80")]
    with pytest.raises(SafetyViolation, match="ratchet"):
        execute_channel_plan(ctrl, guard, "P")


@pytest.mark.parametrize("name,effects", DEMO_CHANNEL_SHAPES.items())
def test_demo_channel_shapes_are_nonvacuous_fixtures(name, effects):
    assert len(effects) >= 2 and all(len(effect) == 3 for effect in effects), name


@pytest.mark.parametrize(
    "device,prop,value",
    [("Core", "Shutter", "LED Shutter"), ("Camera", "Exposure", "10")],
)
def test_preset_entries_do_not_authorize_raw_property_writes(device, prop, value):
    core = Core([(device, prop, value)])
    ctrl = SimpleNamespace(
        core=core,
        authorization_map=AuthorizationMap(
            "guaranteed", "complete", True,
            entries=[AuthorizationEntry(
                "channel-preset:FITC", "built_in_typed_capability", device, prop
            )],
        ),
    )
    # Deliberately make the guard permissive for this exact pair. The refusal
    # must come from the map, proving neither half of the two-gate design is
    # load-bearing alone.
    guard = make_guard(categorical=[(device, prop)])
    with pytest.raises(RigAuthorizationError, match="excluded from the authorization map"):
        set_device_property(ctrl, guard, device, prop, value)
    assert core.calls == []


def test_other_core_properties_are_excluded():
    core = Core([("Core", "Camera", "Other")])
    ctrl = controller(core, {("Core", "Camera"): "built_in_typed_capability"})
    with pytest.raises(RigAuthorizationError, match="Core.Camera"):
        execute_channel_plan(ctrl, make_guard(), "P")
