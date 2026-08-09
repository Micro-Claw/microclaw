import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from microclaw.authorization import (
    AuthorizationEntry, AuthorizationMap, ChannelPlanError,
    ChannelPlanPartialApplicationError, ChannelPlanSafeStateError,
    RigAuthorizationError, _expansion_hash, execute_channel_plan,
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

    def get_available_configs(self, group):
        assert group == "Channel"
        return ["P"]
    def get_loaded_devices(self): return sorted({d for d, _, _ in self.effects})
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
    refreshes = []
    return SimpleNamespace(
        core=core,
        authorization_map=report,
        refresh_gui=lambda: refreshes.append("refresh"),
        refreshes=refreshes,
    )


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
    assert ctrl.refreshes == ["refresh"]


def test_four_write_plan_refreshes_once():
    effects = [(f"D{i}", "Label", f"new{i}") for i in range(4)]
    core, ctrl, guard = categorical_plan(effects)
    execute_channel_plan(ctrl, guard, "P")
    assert ctrl.refreshes == ["refresh"]


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


@pytest.mark.parametrize("failure,expected", [
    # The first write raising means nothing reached the device, so this is the
    # base class -- not a *partial* application of a plan that applied nothing.
    (1, ChannelPlanError),
    (2, ChannelPlanPartialApplicationError),
    (3, ChannelPlanPartialApplicationError),
])
def test_failure_after_each_position_rolls_back_and_stops(failure, expected):
    effects = [(f"D{i}", "Label", f"new{i}") for i in range(3)]
    originals = {(d, p): f"old{i}" for i, (d, p, _) in enumerate(effects)}
    core, ctrl, guard = categorical_plan(effects, originals)
    core.fail_on = failure
    with pytest.raises(expected, match="rolled_back") as caught:
        execute_channel_plan(ctrl, guard, "P")
    # Assert the exact rung of the ladder: `expected` alone would pass on a
    # subclass, which is the confusion this parametrisation exists to catch.
    assert isinstance(caught.value, ChannelPlanPartialApplicationError) == (failure > 1)
    assert core.values == originals
    assert all(("set", f"D{i}", "Label", f"new{i}") not in core.calls
               for i in range(failure, 3))


def test_first_write_rejected_twice_does_not_claim_an_unverified_safe_state():
    """M5 gate round 2, 2026-08-06, hit twice in four channel switches.

        ChannelPlanSafeStateError: Channel plan '640' stopped after 0/4 writes:
        Cannot set property "Laser 4: 1. Enable" to "0" [ ... Serial timeout
        occurred. (17) ]; applied=[]; attempted=['...Laser 4: 1. Enable'];
        rolled_back=[]; SAFE STATE NOT VERIFIED; rollback_failures=[...]

    The first write raised, so nothing reached the device. The rollback then
    tried to re-write that same property -- the command that had just timed out
    -- it timed out again, and the executor escalated to its loudest possible
    error about a plan in which nothing had changed. The rollback iterated
    `attempted`, which includes the write that raised.
    """
    effects = [(f"D{i}", "Label", "new") for i in range(4)]
    core, ctrl, guard = categorical_plan(
        effects, {(d, p): "old" for d, p, _ in effects})
    # The device refuses this property in both directions, as a dead link does.
    def refuse(d, p, v):
        core.calls.append(("set", d, p, str(v)))
        if d == "D0":
            raise RuntimeError('Cannot set property "Label": Serial timeout occurred. (17)')
        core.values[(d, p)] = str(v)
    core.set_property = refuse

    with pytest.raises(ChannelPlanError) as caught:
        execute_channel_plan(ctrl, guard, "P")

    message = str(caught.value)
    assert "SAFE STATE NOT VERIFIED" not in message
    assert not isinstance(caught.value, ChannelPlanPartialApplicationError)
    assert "NO WRITE REACHED THE DEVICE" in message
    assert "applied=[]" in message
    # It still reports the failed restore, just not as a safe-state failure.
    assert "could not be restored either" in message
    assert "Serial timeout" in message
    assert core.values == {(d, p): "old" for d, p, _ in effects}


def test_rollback_failure_after_writes_landed_still_reports_unverified_safe_state():
    """The other direction: two writes land, the third fails, rollback fails.

    This is the case ChannelPlanSafeStateError exists for -- a verified change
    is still on the rig and could not be undone -- and narrowing the class above
    must not weaken it.
    """
    effects = [(f"D{i}", "Label", "new") for i in range(3)]
    core, ctrl, guard = categorical_plan(
        effects, {(d, p): "old" for d, p, _ in effects})
    core.fail_on = 3                       # D0 and D1 land, D2 raises
    core.fail_rollback = ("D0", "Label", "old")

    with pytest.raises(ChannelPlanSafeStateError) as caught:
        execute_channel_plan(ctrl, guard, "P")

    message = str(caught.value)
    assert "SAFE STATE NOT VERIFIED" in message
    assert "stopped after 2/3 writes" in message
    assert "rollback_failures=['D0.Label" in message
    assert core.values[("D0", "Label")] == "new"   # the change that stayed


def test_failing_rollback_reports_unverified_safe_state():
    effects = [("A", "Label", "new"), ("B", "Label", "new")]
    core, ctrl, guard = categorical_plan(
        effects, {("A", "Label"): "old", ("B", "Label"): "old"})
    core.fail_on, core.fail_rollback = 2, ("A", "Label", "old")
    with pytest.raises(ChannelPlanSafeStateError, match="SAFE STATE NOT VERIFIED"):
        execute_channel_plan(ctrl, guard, "P")


@pytest.mark.parametrize(
    "failure,rollback_failure,expected",
    [
        (1, None, ChannelPlanError),
        (2, None, ChannelPlanPartialApplicationError),
        (2, ("A", "Label", "old"), ChannelPlanSafeStateError),
    ],
)
def test_rollback_refreshes_once_without_changing_exception(
    failure, rollback_failure, expected
):
    effects = [("A", "Label", "new"), ("B", "Label", "new")]
    core, ctrl, guard = categorical_plan(
        effects, {("A", "Label"): "old", ("B", "Label"): "old"}
    )
    core.fail_on = failure
    core.fail_rollback = rollback_failure
    refreshes = []

    def failing_refresh():
        refreshes.append("refresh")
        raise RuntimeError("paint failed")

    ctrl.refresh_gui = failing_refresh
    with pytest.raises(expected) as caught:
        execute_channel_plan(ctrl, guard, "P")
    assert type(caught.value) is expected
    assert refreshes == ["refresh"]


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
                         confirm_fn=lambda text, kind, **kw: confirmations.append((text, kind)) or True)
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


# ── EMU-sourced channels: a rig with no "Channel" config group (design/41 F6) ──
#
# The evidence replayed here is the captured M5 EMU configuration, not an
# invented fixture: an invented one has previously manufactured a fake defect
# and hidden the real one. Its laser slots run OPPOSITE to the iChrome's own
# channel numbering -- slot 3 is named "640" and its enable is
# `Laser 1: 1. Enable` -- which is the off-by-one this block exists to prevent.

M5_CONFIG = Path(__file__).parent / "fixtures" / "m5-config.uicfg"
M5_DEVICES = ["iChrome-MLE-TCP", "Laser Trigger", "Thorlabs Filter Wheel",
              "Thorlabs ELL6", "PIZStage"]
M5_ENABLE = {slot: f"Laser {4 - slot}: 1. Enable" for slot in range(4)}
M5_NAME = {0: "405", 1: "488", 2: "561", 3: "640"}


@pytest.fixture(autouse=True)
def _isolate_emu_locator(tmp_path, monkeypatch):
    """No test may reach the host's Micro-Manager install or ~/.microclaw."""
    from microclaw import emu_manager

    monkeypatch.setattr(emu_manager, "_MICROCLAW_DIR", tmp_path / ".microclaw")
    monkeypatch.setattr(emu_manager, "_EMU_CACHE", tmp_path / ".microclaw" / "emu.json")
    monkeypatch.setattr(emu_manager, "_candidate_mm_dirs", lambda: [])


class EmuCore(Core):
    """A rig with no Channel preset to drive, and M5's device inventory."""

    def get_available_configs(self, group):
        assert group == "Channel"
        return []

    def get_loaded_devices(self):
        return list(M5_DEVICES)


def emu_rig(tmp_path, *, config_text=None, values=None):
    mm = tmp_path / "Micro-Manager-2.0"
    (mm / "EMU").mkdir(parents=True)
    (mm / "mmplugins").mkdir()
    (mm / "EMU" / "config.uicfg").write_text(
        M5_CONFIG.read_text(encoding="utf-8") if config_text is None else config_text
    , encoding="utf-8")
    core = EmuCore([], values or {
        ("iChrome-MLE-TCP", prop): "0" for prop in M5_ENABLE.values()
    })
    refreshes = []
    ctrl = SimpleNamespace(
        core=core,
        authorization_map=None,
        refresh_gui=lambda: refreshes.append("refresh"),
        refreshes=refreshes,
    )
    ctrl.get_mm_app_dir = lambda: str(mm)
    return core, ctrl, mm


def emu_guard(*, slots=range(4), on="1", off="0"):
    return make_guard(shutters=[
        ("iChrome-MLE-TCP", M5_ENABLE[slot], on, off) for slot in slots
    ])


def emu_controller(ctrl, channels, guard):
    """Attach the map validate_live_rig would have produced for these channels."""
    from microclaw.authorization import CHANNEL_SOURCE_EMU_LASER_MAP, _channel_source

    source = _channel_source(ctrl, guard.is_illumination_enable)
    ctrl.authorization_map = AuthorizationMap(
        "guaranteed", "complete", True,
        entries=[AuthorizationEntry("channel-preset:x", "built_in_typed_capability",
                                    "iChrome-MLE-TCP", prop)
                 for prop in M5_ENABLE.values()],
        authorized_presets=frozenset(channels),
        channel_expansion_hashes={
            name: _expansion_hash(source.effects[name]) for name in channels
        },
        channel_source=CHANNEL_SOURCE_EMU_LASER_MAP,
    )
    return ctrl


def test_emu_source_names_channels_and_targets_the_reversed_slot(tmp_path):
    """Slot 3 is "640" and its enable is `Laser 1: 1. Enable`, not `Laser 3`.

    Asserted as an exact device/property pair, not a write count: a count would
    pass with every laser in the rack armed in the wrong order.
    """
    from microclaw.authorization import CHANNEL_SOURCE_EMU_LASER_MAP, _channel_source

    _core, ctrl, _mm = emu_rig(tmp_path)
    source = _channel_source(ctrl, emu_guard().is_illumination_enable)

    assert source.kind == CHANNEL_SOURCE_EMU_LASER_MAP
    assert source.names == ("405", "488", "561", "640")
    for slot, name in M5_NAME.items():
        assert source.effects[name][-1] == (
            "iChrome-MLE-TCP", M5_ENABLE[slot], "1"
        ), f"channel {name} armed the wrong line"
    # And nothing else: the emission filter is deliberately untouched.
    assert {device for effects in source.effects.values()
            for device, _p, _v in effects} == {"iChrome-MLE-TCP"}


def test_emu_plan_turns_every_other_named_laser_off_before_arming_one(tmp_path):
    core, ctrl, _mm = emu_rig(tmp_path)
    guard = emu_guard()
    ctrl = emu_controller(ctrl, {"640"}, guard)
    core.values[("iChrome-MLE-TCP", M5_ENABLE[2])] = "1"   # 561 currently on

    out = execute_channel_plan(ctrl, guard, "640", confirm_fn=lambda text, kind, **kw: True)

    writes = [(call[2], call[3]) for call in core.calls if call[0] == "set"]
    assert writes[-1] == (M5_ENABLE[3], "1")
    assert all(value == "0" for _p, value in writes[:-1])
    assert out["channel_source"] == "emu-laser-map"
    assert core.values[("iChrome-MLE-TCP", M5_ENABLE[2])] == "0"


def test_emu_switch_confirms_the_enable_and_not_the_disables(tmp_path):
    """The hand-written sequence raised exactly one illumination confirmation.

    check_illumination gates any value that is not the declared off_value, so
    the plan's off-writes are silent and its single on-write is not. A
    plan-driven switch must not lose a confirmation the hand sequence raised,
    and must not add one it did not.
    """
    core, ctrl, _mm = emu_rig(tmp_path)
    guard = emu_guard()
    ctrl = emu_controller(ctrl, {"561"}, guard)
    confirmations = []

    execute_channel_plan(ctrl, guard, "561", confirm_fn=lambda text, kind, **kw:
                         confirmations.append((text, kind)) or True)

    assert len(confirmations) == 1
    text, kind = confirmations[0]
    assert kind == "illumination"
    assert M5_ENABLE[2] in text and "'1'" in text
    assert core.values[("iChrome-MLE-TCP", M5_ENABLE[2])] == "1"


def test_declined_confirmation_leaves_no_laser_armed(tmp_path):
    core, ctrl, _mm = emu_rig(tmp_path)
    guard = emu_guard()
    ctrl = emu_controller(ctrl, {"640"}, guard)
    with pytest.raises(SafetyViolation, match="declined"):
        execute_channel_plan(ctrl, guard, "640", confirm_fn=lambda text, kind, **kw: False)
    assert core.calls == []


def test_emu_plan_rolls_back_through_the_same_executor(tmp_path):
    core, ctrl, _mm = emu_rig(tmp_path)
    guard = emu_guard()
    ctrl = emu_controller(ctrl, {"640"}, guard)
    core.values[("iChrome-MLE-TCP", M5_ENABLE[0])] = "1"
    core.fail_on = 2

    with pytest.raises(ChannelPlanPartialApplicationError) as caught:
        execute_channel_plan(ctrl, guard, "640", confirm_fn=lambda text, kind, **kw: True)

    assert "rolled_back" in str(caught.value)
    # The one write that landed was undone; the target line never came on.
    assert core.values[("iChrome-MLE-TCP", M5_ENABLE[0])] == "1"
    assert core.values[("iChrome-MLE-TCP", M5_ENABLE[3])] == "0"


def test_undeclared_emu_enable_is_not_offered_as_a_channel(tmp_path):
    _core, ctrl, _mm = emu_rig(tmp_path)
    from microclaw.authorization import _channel_source

    source = _channel_source(
        ctrl, emu_guard(slots=[2, 3]).is_illumination_enable
    )
    assert source.names == ("561", "640")
    assert "405" in source.unavailable and "488" in source.unavailable
    assert "illumination.shutters" in source.unavailable["405"][0]
    # The two it does offer never write the two it refused.
    assert all(prop in (M5_ENABLE[2], M5_ENABLE[3])
               for effects in source.effects.values() for _d, prop, _v in effects)


def _m5_config_with(params):
    raw = json.loads(M5_CONFIG.read_text(encoding="utf-8"))
    raw["pluginConfigurations"][0]["parameters"] = params
    return json.dumps(raw)


@pytest.mark.parametrize("params,label,fragment", [
    ({}, "EMU laser slot 3", "no configured name"),
    ({"Laser 3 - Name": "640", "Laser trigger 3 - Name": "405"},
     "EMU laser slot 3", "cannot be named"),
    ({"Laser 2 - Name": "640", "Laser 3 - Name": "640"}, "640", "ambiguous"),
])
def test_unnameable_slots_refuse_with_a_reason(tmp_path, params, label, fragment):
    """Never "Laser 3", never "slot 2" -- an unresolvable name is a refusal."""
    from microclaw.authorization import _channel_source

    _core, ctrl, _mm = emu_rig(tmp_path, config_text=_m5_config_with(params))
    source = _channel_source(ctrl, emu_guard().is_illumination_enable)
    assert label not in source.names
    assert fragment in " ".join(source.unavailable[label])


def test_unreadable_emu_config_reports_instead_of_looking_channel_less(tmp_path):
    from microclaw.authorization import CHANNEL_SOURCE_NONE, _channel_source

    _core, ctrl, mm = emu_rig(tmp_path)
    (mm / "EMU" / "config.uicfg").write_text("not json", encoding="utf-8")
    source = _channel_source(ctrl, emu_guard().is_illumination_enable)
    assert source.kind == CHANNEL_SOURCE_NONE and source.names == ()
    assert any("Could not resolve EMU" in problem for problem in source.problems)


def test_rig_with_no_group_and_no_emu_map_behaves_as_before(tmp_path):
    """The standing constraint: a non-EMU rig must not gain EMU-flavoured advice."""
    from microclaw.authorization import CHANNEL_SOURCE_NONE, _channel_source

    core = EmuCore([], {})
    source = _channel_source(SimpleNamespace(core=core), emu_guard().is_illumination_enable)
    assert source.kind == CHANNEL_SOURCE_NONE
    assert source.names == () and source.unavailable == {} and source.problems == ()
    assert "EMU" not in source.describe()


def test_config_group_rig_never_reads_the_emu_configuration(tmp_path):
    """The demo-rig non-regression gate, encoded: presets win and EMU is not read."""
    from microclaw import emu_manager
    from microclaw.authorization import CHANNEL_SOURCE_CONFIG_GROUP, _channel_source

    core, ctrl, _mm = emu_rig(tmp_path)
    core.get_available_configs = lambda group: ["DAPI", "FITC"]
    reads = []
    original = emu_manager.read_emu_config
    emu_manager.read_emu_config = lambda *a, **k: reads.append(a) or original(*a, **k)
    try:
        source = _channel_source(ctrl, emu_guard().is_illumination_enable)
    finally:
        emu_manager.read_emu_config = original
    assert source.kind == CHANNEL_SOURCE_CONFIG_GROUP
    assert source.names == ("DAPI", "FITC")
    assert reads == []
