"""Run the block 60b demo gate end to end against a bridge-shaped fake.

Not a gate artifact and not part of the suite. Its job is the one block 59a paid
a rig trip to learn: find out whether the gate program *runs* before an operator
does. That trip died on `TypeError: 'mmcorej_StrVector' object is not iterable`
-- a `list()` over a Core collection, which works against every MagicMock and
fails on every rig -- so the fake here returns size()/get(i) vectors whose
`__iter__` raises.

It replaces exactly three things: the safety config, the controller, and
pycro-manager's `Acquisition`. `run_timelapse`, `_authorize_acquisition`, the
disclosure text, the grant lookup and the progress emission are all real. The
fake camera is deliberately 2048x2048x2, so the computed bound is 512 frames and
a 576-frame burst crosses it in seconds -- the demo machine's own geometry will
give a much larger bound and that is the point of computing it there.

Run it on BOTH trees; the point is discrimination, not a green line:

    cd <worktree> && python design/60-block60b-gate-selftest.py
    MICROCLAW_TREE_UNDER_TEST=<pre-60b tree> python design/60-block60b-gate-selftest.py

    # 60b tree     -> SELFTEST PASS, exit 0
    # pre-60b tree -> SELFTEST FAIL, exit 1, on the build-identity limb

What it cannot establish is the one thing the rig is for: whether NDTiff's real
rollover past 4 GiB is clean. The fake writes the second stack itself.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

GATE_TREE = Path(__file__).resolve().parents[1]
TREE = Path(os.environ.get("MICROCLAW_TREE_UNDER_TEST") or GATE_TREE)
os.environ["MICROCLAW_TREE_UNDER_TEST"] = str(TREE)
sys.path.insert(0, str(TREE))

from microclaw import tools
from microclaw.safety import (AcquisitionConstraints, CameraConstraints,
                              SafetyConstraints, SafetyGuard, StageConstraints)

EXPOSURE_MS = 4.0
FRAME_SLEEP_S = 0.004


class StrVector:
    """A Core collection the way the bridge really hands one back.

    size()/get(i), and iteration raises. A MagicMock would not have caught
    block 59a's defect because it hands back Python-friendly objects.
    """

    def __init__(self, items):
        self._items = list(items)

    def size(self):
        return len(self._items)

    def get(self, index):
        return self._items[index]

    def __iter__(self):
        raise TypeError("'mmcorej_StrVector' object is not iterable")


class FakeCore:
    def __init__(self):
        self.exposure = EXPOSURE_MS
        self.sequence_running = False
        self._lock = threading.Lock()

    # 2048x2048x2 = 8,388,608 B/frame -> a bound of 512 frames.
    def get_camera_device(self): return "Camera"
    def get_image_width(self): return 2048
    def get_image_height(self): return 2048
    def get_bytes_per_pixel(self): return 2
    def get_exposure(self): return self.exposure
    def set_exposure(self, ms): self.exposure = float(ms)
    def get_loaded_devices(self): return StrVector(["Camera", "Z", "XY"])
    def get_available_config_groups(self): return StrVector([])
    def get_available_configs(self, group): return StrVector([])
    def get_available_pixel_size_configs(self): return StrVector([])
    def get_shutter_device(self): return ""

    def is_sequence_running(self, *_a):
        with self._lock:
            return self.sequence_running

    def __getattr__(self, name):
        return MagicMock()


class FakeAcquisition:
    """A burst that saves frames at a real cadence and rolls its own stack.

    The rollover is faked here on purpose: whether NDTiff's real one is clean is
    exactly what the demo machine is for, and a fake that answered it would be
    the gate scoring its own assumption.
    """

    core = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        directory = Path(kwargs["directory"]) / kwargs["name"]
        directory.mkdir(parents=True, exist_ok=True)
        self._dataset_disk_location = str(directory)
        self._exception = None

    def __enter__(self): return self

    def acquire(self, events):
        with type(self).core._lock:
            type(self).core.sequence_running = True
        saved = self.kwargs.get("image_saved_fn")
        directory = Path(self._dataset_disk_location)
        for index, _event in enumerate(events):
            time.sleep(FRAME_SLEEP_S)
            if saved is not None:
                saved({"time": index}, object())
        (directory / "NDTiffStack.tif").write_bytes(b"\0")
        (directory / "NDTiffStack_1.tif").write_bytes(b"\0")

    def __exit__(self, *_exc):
        time.sleep(0.2)
        with type(self).core._lock:
            type(self).core.sequence_running = False
        return None


def main():
    core = FakeCore()
    FakeAcquisition.core = core
    ctrl = MagicMock()
    ctrl.core = core
    ctrl.is_connected.return_value = True
    if hasattr(ctrl, "_microclaw_unterminated_acquisition"):
        del ctrl._microclaw_unterminated_acquisition

    workspace = Path(tempfile.mkdtemp(prefix="block60b-selftest-"))
    constraints = SafetyConstraints(
        stage=StageConstraints(x_min=-5000, x_max=5000, y_min=-5000, y_max=5000,
                               z_min=0.0, z_max=100.0),
        camera=CameraConstraints(max_exposure_ms=1000.0),
        acquisition=AcquisitionConstraints(),
        workspace_dir=str(workspace),
    )

    spec = importlib.util.spec_from_file_location(
        "gate60b", GATE_TREE / "design/60-block60b-demo-gate.py")
    gate = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(gate)

    gate.MicroscopeController = lambda **kw: ctrl
    gate.load_safety_config = lambda path: types.SimpleNamespace(constraints=constraints)
    gate.SafetyGuard = SafetyGuard
    tools.Acquisition = FakeAcquisition

    evidence = workspace / "evidence"
    safety_doc = workspace / "safety.yaml"
    safety_doc.write_text("workspace_dir: " + str(workspace) + "\n", encoding="utf-8")
    # No --save-root: the default path through the safety config is what the
    # runbook uses, so it is what the selftest must exercise.
    sys.argv = ["gate", "--exposure-ms", str(EXPOSURE_MS), "--extra-frames", "64",
                "--output", str(evidence), "--safety-config", str(safety_doc)]

    rc = gate.main()
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    results = json.loads((evidence / "results.json").read_text())
    print()
    print(f"tree under test: {TREE}")
    for item in results["results"]:
        print(f"  {item['status']:<14} {item['name']} - {item['detail']}")
    print()
    print("geometry:", json.dumps(results["geometry"], sort_keys=True))
    print("measured:", json.dumps(results["measured"], sort_keys=True))
    print("SELFTEST " + ("PASS" if rc == 0 else "FAIL") + f" (gate exit {rc})")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
