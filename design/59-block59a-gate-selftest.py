"""Drive the block 59a demo gate end to end against a BRIDGE-SHAPED fake core.

Not a gate artifact and not part of the suite. It is how the coordinator finds
out whether a gate works before an operator spends a trip on it -- the same
instrument as `design/55-gate-probe-selftest.py`, with one correction that block
59a paid for.

**A MagicMock is not a bridge.** design/55's selftest replaces the controller
with a MagicMock, which hands back Python-friendly objects, and it would not
have caught the defect this file exists for: `mmcorej_StrVector` exposes
`size()`/`get(i)` and raises `TypeError` on iteration, so `list(...)` over a
Core collection works against every fake in the suite and fails on every rig.
The fake below returns bridge-shaped vectors deliberately, and its `__iter__`
raises the exact message the demo machine produced on 2026-08-28.

Run it on BOTH trees. The point is discrimination, not a green line:

    cd <fixed worktree>   && python3 <this file>   # expect SELFTEST PASS, exit 0
    cd <pre-fix tree>     && python3 <this file>   # expect SELFTEST FAIL

Run it from inside the tree under test: `import microclaw` otherwise resolves
through the editable install to whichever checkout `pip install -e .` last
pointed at, which is how design/55's first run silently scored the wrong tree.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

TREE = Path.cwd()
sys.path.insert(0, str(TREE))

GATE = TREE / "design/59-block59a-demo-gate.py"


class StrVector:
    """What the bridge returns: size()/get(i), and NOT iterable."""

    def __init__(self, items):
        self._items = [str(item) for item in items]

    def size(self):
        return len(self._items)

    def get(self, index):
        return self._items[index]

    def __iter__(self):
        raise TypeError("'mmcorej_StrVector' object is not iterable")

    def __len__(self):
        raise TypeError("object of type 'mmcorej_StrVector' has no len()")


class DoubleVector(StrVector):
    def __init__(self, items):
        self._items = [float(item) for item in items]

    def get(self, index):
        return self._items[index]

    def __iter__(self):
        raise TypeError("'mmcorej_DoubleVector' object is not iterable")


class Setting:
    def __init__(self, device, prop, value):
        self._values = (device, prop, value)

    def get_device_label(self):
        return self._values[0]

    def get_property_name(self):
        return self._values[1]

    def get_property_value(self):
        return self._values[2]


class Configuration:
    def __init__(self, rules):
        self._rules = [Setting(*rule) for rule in rules]

    def size(self):
        return len(self._rules)

    def get_setting(self, index):
        return self._rules[index]


# Shaped like the stock demo configuration, which is what the gate will meet.
# Device names live here and in design/, never in microclaw/.
STATE_DEVICES = {
    "Objective": ("Nikon 10X S Fluor",
                  ["Nikon 10X S Fluor", "Nikon 20X Plan Fluor ELWD",
                   "Objective-2", "Nikon 40X Plan Fluor ELWD",
                   "Objective-4", "Objective-5"]),
    "Path": ("State-0", ["State-0", "State-1", "State-2"]),
    "Dichroic": ("400DCLP", ["400DCLP", "Q505LP", "Q585LP"]),
    "Emission": ("Chroma-HQ700", ["Chroma-HQ700", "Chroma-HQ535", "Chroma-HQ620"]),
    "Excitation": ("Chroma-D360", ["Chroma-D360", "Chroma-HQ480", "Chroma-HQ570"]),
    "Shutter": ("Closed", ["Open", "Closed"]),
}
PIXEL_CONFIGS = {
    "Res10x": [("Objective", "Label", "Nikon 10X S Fluor")],
    "Res20x": [("Objective", "Label", "Nikon 20X Plan Fluor ELWD")],
    "Res40x": [("Objective", "Label", "Nikon 40X Plan Fluor ELWD")],
}
DEVICE_TYPES = {name: 4 for name in STATE_DEVICES}          # StateDevice
DEVICE_TYPES.update({"Camera": 2, "XY": 6, "Z": 5, "Autofocus": 9, "Core": 0})


class FakeCore:
    """Every collection is bridge-shaped. Unknown calls fall through to a mock."""

    def __init__(self):
        self.labels = {name: value[0] for name, value in STATE_DEVICES.items()}
        self.writes = []
        self._mock = MagicMock()

    # -- collections: the whole point of this file --------------------------
    def get_loaded_devices(self):
        return StrVector(list(DEVICE_TYPES))

    def get_available_pixel_size_configs(self):
        return StrVector(list(PIXEL_CONFIGS))

    def get_allowed_property_values(self, device, prop):
        if device in STATE_DEVICES and prop == "Label":
            return StrVector(STATE_DEVICES[device][1])
        return StrVector([])

    def get_device_property_names(self, device):
        if device in STATE_DEVICES:
            return StrVector(["Label", "State", "Description"])
        if device == "Autofocus":
            return StrVector(["Status", "Description"])
        return StrVector(["Description"])

    def get_pixel_size_affine_by_id(self, config):
        return DoubleVector([0.5, 0.0, 0.0, 0.5, 0.0, 0.0])

    def get_available_config_groups(self):
        return StrVector([])

    # -- scalars ------------------------------------------------------------
    def get_device_type(self, device):
        return DEVICE_TYPES.get(device, 1)

    def get_property(self, device, prop):
        if device in self.labels and prop == "Label":
            return self.labels[device]
        if device == "Autofocus" and prop == "Status":
            return "Off"
        return "0"

    def set_property(self, device, prop, value):
        self.writes.append((device, prop, str(value)))
        if device in self.labels and prop == "Label":
            self.labels[device] = str(value)

    def is_property_read_only(self, device, prop):
        return device == "Autofocus" and prop == "Status"

    def get_pixel_size_config_data(self, config):
        return Configuration(PIXEL_CONFIGS[config])

    def get_pixel_size_um_by_id(self, config):
        return {"Res10x": 1.0, "Res20x": 0.5, "Res40x": 0.25}[config]

    def get_current_pixel_size_config(self):
        for name, rules in PIXEL_CONFIGS.items():
            if all(self.labels.get(d) == v for d, p, v in rules):
                return name
        return ""

    def get_pixel_size_um(self):
        active = self.get_current_pixel_size_config()
        return self.get_pixel_size_um_by_id(active) if active else 0.0

    def get_shutter_device(self):
        return "Shutter"

    def get_shutter_open(self):
        return False

    def get_auto_shutter(self):
        return True

    def get_auto_focus_device(self):
        return "Autofocus"

    def is_continuous_focus_enabled(self):
        return False

    def get_camera_device(self):
        return "Camera"

    def get_device_name(self, label):
        return "DemoCamera"

    def get_xy_stage_device(self):
        return "XY"

    def get_focus_device(self):
        return "Z"

    def get_x_position(self):
        return 0.0

    def get_y_position(self):
        return 0.0

    def get_position(self, device=None):
        return 50.0

    def get_exposure(self):
        return 10.0

    def wait_for_device(self, device):
        return None

    def has_property_limits(self, device, prop):
        return False

    def __getattr__(self, name):
        return getattr(self._mock, name)


def load_gate():
    spec = importlib.util.spec_from_file_location("gate59a", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    if not GATE.exists():
        print(f"SELFTEST FAIL: no gate program at {GATE}")
        return 1
    gate = load_gate()
    core = FakeCore()

    # A Micro-Manager installation shaped like the demo machine's: a real
    # directory that `_looks_like_mm_dir` accepts, with no EMU profile in it.
    # Without this, live-rig authorization refuses -- correctly -- to conclude
    # that an installation it cannot read is non-EMU, and every limb reports
    # NOT EXERCISED for a reason that is about the fixture and not the gate.
    mm_dir = Path(tempfile.mkdtemp(prefix="selftest59a-mm-"))
    (mm_dir / "mmplugins").mkdir()
    import microclaw.emu_manager as emu_manager
    # Never write the operator's real MM-path cache from a selftest. The rule
    # a gate obeys -- do not leave production state pointing into your own
    # fixture -- binds the instrument that checks the gate too.
    emu_manager.save_mm_app_dir = lambda *args, **kwargs: None

    # The controller is the only thing replaced. validate_live_rig, the
    # inventory, get_system_state and every limb are the real ones.
    #
    # NOT a MagicMock. `core` must be a PROPERTY over `_core`, because the gate
    # measures bridge calls by installing a counting wrapper as `ctrl._core` and
    # reading it back through `ctrl.core`. A mock with a plain `core` attribute
    # silently keeps handing out the unwrapped core, and the cost limb reports
    # 0 calls for both invocations while every other limb passes. The two cache
    # slots start as real `None` for the same reason: `getattr(ctrl, name, None)`
    # against a mock returns a truthy mock and poisons the retention check.
    class FakeController:
        def __init__(self):
            self._core = core
            self._state_device_inventory = None
            self._pixel_size_config_inventory = None
            self.studio = MagicMock()
            self.studio.live.return_value.is_live_mode_on.return_value = False

        @property
        def core(self):
            return self._core

        def get_mm_app_dir(self):
            return str(mm_dir)

        def is_connected(self):
            return True

        def refresh_gui(self):
            return None

    controller = FakeController()
    gate.MicroscopeController = lambda port=None, **kwargs: controller

    out = Path(tempfile.mkdtemp(prefix="selftest59a-")) / "evidence"
    safety = out.parent / "production-safety.yaml"
    safety.parent.mkdir(parents=True, exist_ok=True)
    safety.write_text("schema_version: 3\nreviewed: true\n", encoding="utf-8")

    sys.argv = ["gate", "--output", str(out), "--active-safety-config", str(safety)]
    real_stdout = sys.stdout
    try:
        status = gate.main()
    finally:
        sys.stdout, sys.stderr = real_stdout, sys.__stderr__

    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    print(f"\n--- selftest scoring ({out}) ---")
    for item in results["results"]:
        print(f"  {item['status']:<14} {item['name']}")
    print(f"  cost: {results['cost']}")
    print(f"  device writes made by the gate: {core.writes}")

    bad = [item for item in results["results"] if item["status"] != "PASS"]
    # The gate leaves the rig as it found it: every write is paired with its
    # restoration, and the final label equals the entry label.
    left_moved = {device: value for device, value in
                  ((name, core.labels[name]) for name in STATE_DEVICES)
                  if value != STATE_DEVICES[device][0]}
    if left_moved:
        print(f"SELFTEST FAIL: gate left devices moved: {left_moved}")
        return 1
    if bad:
        print(f"SELFTEST FAIL: {len(bad)} limb(s) not PASS; gate exit {status}")
        return 1
    if status != 0:
        print(f"SELFTEST FAIL: every limb passed but the gate exited {status}")
        return 1
    print("SELFTEST PASS: every limb passed against a bridge-shaped fake")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
