"""Run the block 60a demo gate end to end against a bridge-shaped fake.

Not a gate artifact and not part of the suite. Its job is the one design/59
block 59a paid a rig trip to learn: find out whether the gate program *runs*
before an operator does. That trip died on `TypeError: 'mmcorej_StrVector'
object is not iterable` -- a `list()` over a Core collection, which works
against every MagicMock and fails on every rig -- so the fake here returns
size()/get(i) vectors whose `__iter__` raises.

It replaces exactly three things: the safety config, the controller, and
pycro-manager's `Acquisition`. `run_timelapse`, `_acquire_with_hooks`, the
teardown waiter, the supervisor loop and the gate's own limbs are all real. A
selftest that stubbed `_acquire_with_hooks` would test nothing here, because the
waiter is the subject.

Run it on BOTH trees; the point is discrimination, not a green line:

    cd <worktree> && python design/60-block60a-gate-selftest.py

    # fixed tree   -> SELFTEST PASS, exit 0
    # pre-60a tree -> SELFTEST FAIL, exit 1, on the build-identity limb

Discrimination is honest but narrow, and that is deliberate. The failure
design/60 is about is upstream and is not induced here or on the rig; the fake
in tests/test_bounded_acquisition_wait.py reproduces it exactly. What this
proves is that the gate executes against rig-shaped objects.
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

TREE = Path.cwd()
os.environ["MICROCLAW_TREE_UNDER_TEST"] = str(TREE)
sys.path.insert(0, str(TREE))

from microclaw import tools
from microclaw.safety import (AcquisitionConstraints, CameraConstraints,
                              SafetyConstraints, SafetyGuard, StageConstraints)

FRAMES = 12
EXPOSURE_MS = 5.0


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

    def get_camera_device(self): return "Camera"
    def get_image_width(self): return 512
    def get_image_height(self): return 512
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
    """A burst that is genuinely running while the probe thread polls."""

    core = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._dataset_disk_location = str(Path(kwargs["directory"]) / kwargs["name"])
        self._exception = None

    def __enter__(self): return self

    def acquire(self, events):
        with type(self).core._lock:
            type(self).core.sequence_running = True
        saved = self.kwargs.get("image_saved_fn")
        for index, _event in enumerate(events):
            time.sleep(EXPOSURE_MS / 1000.0)
            if saved is not None:
                saved({"time": index}, object())

    def __exit__(self, *_exc):
        # pycro-manager's __exit__ is mark_finished() then await_completion();
        # a real one takes real time, which is the number the gate reports.
        time.sleep(0.4)
        with type(self).core._lock:
            type(self).core.sequence_running = False
        return None


def main():
    core = FakeCore()
    FakeAcquisition.core = core
    ctrl = MagicMock()
    ctrl.core = core
    ctrl.is_connected.return_value = True
    # A MagicMock would answer hasattr() for the refusal flag and make the
    # no-refusal limb meaningless, so delete it the way a real controller has it.
    if hasattr(ctrl, "_microclaw_unterminated_acquisition"):
        del ctrl._microclaw_unterminated_acquisition

    workspace = Path(tempfile.mkdtemp(prefix="block60a-selftest-"))
    constraints = SafetyConstraints(
        stage=StageConstraints(x_min=-5000, x_max=5000, y_min=-5000, y_max=5000,
                               z_min=0.0, z_max=100.0),
        camera=CameraConstraints(max_exposure_ms=1000.0),
        acquisition=AcquisitionConstraints(),
        workspace_dir=str(workspace),
    )

    spec = importlib.util.spec_from_file_location(
        "gate60a", TREE / "design/60-block60a-demo-gate.py")
    gate = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(gate)

    gate.MicroscopeController = lambda **kw: ctrl
    gate.load_safety_config = lambda path: types.SimpleNamespace(constraints=constraints)
    gate.SafetyGuard = SafetyGuard
    gate.Dataset = lambda path: types.SimpleNamespace(path=path)
    gate._iter_present_coords = lambda dataset, fixed: (
        {"time": i} for i in range(FRAMES))
    tools.Acquisition = FakeAcquisition

    evidence = workspace / "evidence"
    safety_doc = workspace / "safety.yaml"
    safety_doc.write_text("workspace_dir: " + str(workspace) + "\n", encoding="utf-8")
    sys.argv = ["gate", "--save-root", str(workspace), "--frames", str(FRAMES),
                "--exposure-ms", str(EXPOSURE_MS), "--output", str(evidence),
                "--safety-config", str(safety_doc)]

    rc = gate.main()
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    results = json.loads((evidence / "results.json").read_text())
    print()
    for item in results["results"]:
        print(f"  {item['status']:<14} {item['name']} - {item['detail']}")
    print()
    print("measured:", json.dumps(results["measured"], sort_keys=True))
    print("confirmations recorded:", len(results.get("confirmations", [])))
    print("SELFTEST " + ("PASS" if rc == 0 else "FAIL") + f" (gate exit {rc})")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
