from __future__ import annotations

from types import SimpleNamespace

import pytest

from microclaw import authorization
from microclaw.safety import SafetyConstraints, SafetyGuard, StageConstraints
from microclaw.tools import get_system_state


class Setting:
    def __init__(self, device, prop, value):
        self.values = device, prop, value

    def get_device_label(self): return self.values[0]
    def get_property_name(self): return self.values[1]
    def get_property_value(self): return self.values[2]


class ConfigData:
    def __init__(self, rules): self.rules = [Setting(*rule) for rule in rules]
    def size(self): return len(self.rules)
    def get_setting(self, index): return self.rules[index]


class OpticalCore:
    def __init__(self, devices=(), configs=None, *, shutter=""):
        self.devices = dict(devices)
        self.configs = configs or {}
        self.shutter = shutter
        self.calls = 0
        self.allowed_failures = set()
        self.label_failures = set()
        self.autofocus = ""
        self.engaged = False
        self.property_calls = []

    def _call(self): self.calls += 1
    def get_loaded_devices(self): self._call(); return list(self.devices)
    def get_device_type(self, device): self._call(); return 4
    def get_shutter_device(self):
        self._call()
        if isinstance(self.shutter, Exception): raise self.shutter
        return self.shutter
    def get_shutter_open(self): self._call(); return False
    def get_auto_shutter(self): self._call(); return False
    def get_allowed_property_values(self, device, prop):
        self._call()
        if device in self.allowed_failures: raise RuntimeError(f"allowed read failed for {device}")
        return self.devices[device][1]
    def get_property(self, device, prop):
        self._call()
        self.property_calls.append((device, prop))
        if device in self.label_failures: raise RuntimeError(f"label read failed for {device}")
        return self.devices[device][0]
    def get_available_pixel_size_configs(self): self._call(); return list(self.configs)
    def get_pixel_size_config_data(self, config): self._call(); return ConfigData(self.configs[config])
    def get_pixel_size_um_by_id(self, config): self._call(); return 1.0
    def get_pixel_size_affine_by_id(self, config): self._call(); return [1, 0, 0, 1, 0, 0]
    def get_current_pixel_size_config(self): self._call(); return ""
    def get_pixel_size_um(self): self._call(); return 0.0
    def get_auto_focus_device(self): self._call(); return self.autofocus
    def is_continuous_focus_enabled(self): self._call(); return self.engaged
    def get_device_property_names(self, device): self._call(); return ["Status"]
    def is_property_read_only(self, device, prop): self._call(); return True
    def get_x_position(self): self._call(); return 0.0
    def get_y_position(self): self._call(); return 0.0
    def get_position(self): self._call(); return 0.0
    def get_exposure(self): self._call(); return 10.0
    def get_camera_device(self): self._call(); return ""


class Studio:
    def live(self): return self
    def is_live_mode_on(self): return False


GUARD = SafetyGuard(SafetyConstraints(stage=StageConstraints(
    x_min=-1, x_max=1, y_min=-1, y_max=1, z_min=-1, z_max=1,
)))


def state(core):
    ctrl = SimpleNamespace(core=core, studio=Studio(), get_mm_app_dir=None)
    ctrl._state_device_inventory = authorization._build_state_device_inventory(
        core, core.get_loaded_devices()
    )
    return get_system_state(ctrl, GUARD), ctrl


def test_minimal_rig_reports_all_three_orientation_fields():
    payload, _ = state(OpticalCore())
    assert payload["optical_path"]["discrete_positions"] == []
    assert payload["objective"]["pixel_size_config"] is None
    assert "does not know" in payload["objective"]["reason"]
    assert payload["focus"] == {
        "engaged": None, "reason": "No hardware autofocus device is configured."
    }


def test_ti_shaped_fake_reports_path_dependency_and_focus_without_claiming_objective():
    core = OpticalCore({
        "TINosePiece": ("4-Unknown", [f"{n}-Unknown" for n in range(1, 7)]),
        "TILightPath": ("2-Left100", ["1-Eye100", "2-Left100", "3-Right100", "4-Left80"]),
    }, {"Res60x": [("TINosePiece", "Label", "4-60xOil")]})
    core.autofocus = "TIPFSStatus"
    core.devices["TIPFSStatus"] = ("Out of focus search range", [])
    payload, _ = state(core)
    assert payload["objective"]["available_configs"][0]["dependencies"][0]["device"] == "TINosePiece"
    assert "measured objective" not in str(payload).lower()
    assert payload["focus"]["status_properties"]["Status"] == "Out of focus search range"
    positions = {item["device"]: item for item in payload["optical_path"]["discrete_positions"]}
    assert positions["TILightPath"]["role"] == ["light-path candidate"]
    assert positions["TINosePiece"]["role"] == [
        "pixel-size-config dependency (not proof of objective)"
    ]


def test_demo_shaped_fake_uses_the_same_generic_payload():
    core = OpticalCore({"Objective": ("State-1", ["State-0", "State-1"]),
                        "Path": ("State-0", ["State-0", "State-1"]),
                        "Autofocus": ("Ready", [])},
                       {name: [("Objective", "Label", value)] for name, value in
                        (("Res10x", "State-0"), ("Res20x", "State-1"), ("Res40x", "State-2"))})
    core.autofocus = "Autofocus"
    payload, _ = state(core)
    assert len(payload["objective"]["available_configs"]) == 3
    assert {item["device"] for item in payload["optical_path"]["discrete_positions"]} == {
        "Autofocus", "Objective", "Path"
    }


def test_second_call_uses_retained_inventory_and_config_walk():
    core = OpticalCore({"Wheel": ("A", ["A", "B"])}, {"Res": [("Wheel", "Label", "A")]})
    ctrl = SimpleNamespace(core=core, studio=Studio(), get_mm_app_dir=None)
    ctrl._state_device_inventory = authorization._build_state_device_inventory(
        core, core.get_loaded_devices()
    )
    core.calls = 0
    payload = get_system_state(ctrl, GUARD)
    first = core.calls
    get_system_state(ctrl, GUARD)
    second = core.calls - first
    assert payload["optical_path"]["discrete_positions"]
    assert second < first


def test_port_vocabulary_marks_but_never_filters():
    payload, _ = state(OpticalCore({"Opaque": ("Alpha", ["Alpha", "Beta"])}))
    assert payload["optical_path"]["discrete_positions"] == [
        {"device": "Opaque", "allowed": ["Alpha", "Beta"], "label": "Alpha", "role": []}
    ]


def test_dependency_live_value_moves_with_discrete_label_without_a_duplicate_read():
    core = OpticalCore({"Objective": ("10x", ["10x", "60x"])},
                       {"Res10x": [("Objective", "Label", "10x")]})
    first, ctrl = state(core)
    first_rule = first["objective"]["available_configs"][0]["dependencies"][0]
    assert first_rule["live"] == "10x"
    assert first_rule["matches"] is True
    core.devices["Objective"] = ("60x", ["10x", "60x"])
    core.property_calls.clear()
    second = get_system_state(ctrl, GUARD)
    second_position = second["optical_path"]["discrete_positions"][0]
    second_rule = second["objective"]["available_configs"][0]["dependencies"][0]
    assert second_position["label"] == second_rule["live"] == "60x"
    assert second_rule["matches"] is False
    assert core.property_calls.count(("Objective", "Label")) == 1


def test_multikey_pixel_config_preserves_every_dependency_without_measuring_objective():
    core = OpticalCore({"Objective": ("10x", ["10x"]), "Camera": ("2", ["1", "2"])},
                       {"Res10": [("Objective", "Label", "10x"), ("Camera", "Binning", "2")]})
    payload, _ = state(core)
    deps = payload["objective"]["available_configs"][0]["dependencies"]
    assert [(item["device"], item["property"]) for item in deps] == [
        ("Objective", "Label"), ("Camera", "Binning")
    ]
    assert "no dependency by itself proves" in payload["objective"]["reason"]


@pytest.mark.parametrize("shutter", [RuntimeError("shutter unavailable"), ""])
def test_unreadable_or_empty_core_shutter_never_hides_state_devices(shutter):
    payload, _ = state(OpticalCore({"Wheel": ("A", ["A", "B"])}, shutter=shutter))
    assert [item["device"] for item in payload["optical_path"]["discrete_positions"]] == ["Wheel"]
    if isinstance(shutter, Exception):
        assert payload["optical_path"]["shutter_exclusion"] == "unknown"
        assert "shutter unavailable" in payload["optical_path"]["shutter_exclusion_error"]
    else:
        assert "shutter_exclusion" not in payload["optical_path"]


def test_configured_core_shutter_is_excluded_and_named():
    payload, _ = state(OpticalCore({
        "Wheel": ("A", ["A", "B"]), "Blocker": ("Closed", ["Open", "Closed"]),
    }, shutter="Blocker"))
    assert [item["device"] for item in payload["optical_path"]["discrete_positions"]] == ["Wheel"]
    assert payload["optical_path"]["shutter_exclusion"] == {
        "device": "Blocker", "reason": "Core shutter is reported separately",
    }


def test_per_device_read_failures_preserve_entries_and_errors():
    core = OpticalCore({"AllowedBroken": ("A", ["A"]), "LabelBroken": ("B", ["B"])})
    core.allowed_failures.add("AllowedBroken")
    core.label_failures.add("LabelBroken")
    payload, _ = state(core)
    entries = {item["device"]: item for item in payload["optical_path"]["discrete_positions"]}
    assert entries["AllowedBroken"]["allowed"] == "unknown"
    assert "allowed read failed" in entries["AllowedBroken"]["allowed_error"]
    assert entries["LabelBroken"]["label"] == "unknown"
    assert "label read failed" in entries["LabelBroken"]["label_error"]
