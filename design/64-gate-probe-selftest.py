"""Run design/64's gate probe end to end against fakes, before a rig sees it.

Not a gate artifact and not part of the suite. `CLAUDE.md` requires it: a gate is
code, and handing an operator code nobody executed is the defect this workflow
keeps paying for. design/59a lost a whole demo trip to a `list()` over a Core
collection, which works against every naive fake and raises on every rig.

**The fake is written from the bridge's behaviour, not from the probe's
expectations** — that is the design/60b lesson (a gate's own fake wrote the
filename its glob expected, so a perfect run scored as a failure). Concretely:
every Core collection here refuses `__iter__` and answers `size()`/`get(i)`, and
the optics come from `tests/synthetic_optics.py`, which knows only how the scene
moves when the stage does and has no opinion about affine signs.

Run it on BOTH trees. The point is discrimination, not a green line:

    python design/64-gate-probe-selftest.py               # expect PROBE PASS, exit 0
    python design/64-gate-probe-selftest.py --flip-sign   # expect limb F FAIL, ratio ~2.0

`--flip-sign` restores the exact defect design/64 fixed, in memory only. If it
does not turn limb F red, the gate cannot see the bug it exists to catch and
must not be shipped.

Run it from inside the tree under test, or `import microclaw` resolves through
the editable install to whichever checkout `pip install -e .` last pointed at.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from microclaw import tools
from microclaw.safety import (CameraConstraints, SafetyConstraints, SafetyGuard,
                              StageConstraints)
from tests.synthetic_optics import OPTICS_CASES, SyntheticOptics


class DoubleVector:
    """What MMCore hands back for an affine: NOT a Python iterable.

    `list(vector)` and `for v in vector` both raise on a rig. Reproducing that
    is the whole point of this class — a MagicMock would hand back
    Python-friendly objects and the probe would pass here and die there.
    """

    def __init__(self, values):
        self._values = list(values)

    def size(self):
        return len(self._values)

    def get(self, index):
        return self._values[index]

    def __iter__(self):
        raise TypeError("'mmcorej_DoubleVector' object is not iterable")


class Rect:
    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height


class FakeCore:
    """A camera, an XY stage and a pixel-size config, shaped like the bridge."""

    def __init__(self, optics, affine_values):
        self.optics = optics
        self.affine_values = affine_values
        self._frame = None
        self.moves = []

    # --- identity -------------------------------------------------------
    def get_camera_device(self):
        return "Camera"

    def get_device_name(self, label):
        return "DemoCamera"

    def get_current_pixel_size_config(self):
        return "Res20x"

    def get_property(self, device, name):
        if (device, name) == ("Camera", "Binning"):
            return "1"
        return "0"

    def get_roi(self):
        height, width = self.optics.shape
        return Rect(0, 0, width, height)

    def get_pixel_size_affine(self):
        return DoubleVector(self.affine_values)

    def get_pixel_size_um(self):
        return 0.5

    # --- camera ---------------------------------------------------------
    def snap_image(self):
        self._frame = self.optics.snap()

    def get_tagged_image(self):
        height, width = self.optics.shape
        return types.SimpleNamespace(
            tags={"Width": width, "Height": height}, pix=self._frame
        )

    def get_bytes_per_pixel(self):
        return 2

    def get_number_of_components(self):
        return 1

    def get_image_width(self):
        return self.optics.shape[1]

    def get_image_height(self):
        return self.optics.shape[0]

    def is_sequence_running(self):
        return False

    # --- stage ----------------------------------------------------------
    def get_x_position(self):
        return self.optics.pos["x"]

    def get_y_position(self):
        return self.optics.pos["y"]

    def set_relative_xy_position(self, dx, dy):
        self.moves.append((dx, dy))
        self.optics.move(dx, dy)

    def get_xy_stage_device(self):
        return "XY"

    def wait_for_device(self, device):
        return None

    def __getattr__(self, name):
        return MagicMock()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--flip-sign", action="store_true",
                        help="restore design/64's defect; limb F must go red")
    parser.add_argument("--demo-camera", action="store_true",
                        help="same image every snap, as the demo machine does; "
                             "limb 0 must catch it and F/F2 must NOT report FAIL")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    # A rig whose camera is rotated 90 degrees relative to the stage, so a limb
    # that confuses the axes cannot pass by symmetry.
    optics = SyntheticOptics(OPTICS_CASES["rot90"])
    # What MM would publish for these optics: to centre a feature at offset r
    # the stage must move A.r, and the scene moves m_phys.s for a stage move s,
    # so A = -inv(m_phys). Derived from MM's semantics, never copied from ours.
    a = -np.linalg.inv(optics.m_phys)
    affine_values = [a[0, 0], a[0, 1], 0.0, a[1, 0], a[1, 1], 0.0]

    core = FakeCore(optics, affine_values)
    if args.demo_camera:
        # The demo machine hands back the SAME image regardless of where the
        # stage is (operator, 2026-08-30). Not noise, not a rotating pattern —
        # byte-identical. Nothing about the stage reaches the pixels.
        frozen = optics.snap()
        core.snap_image = lambda: None
        core.get_tagged_image = lambda: types.SimpleNamespace(
            tags={"Width": optics.shape[1], "Height": optics.shape[0]}, pix=frozen
        )
        print("!! --demo-camera: every snap returns one frozen frame\n")
    ctrl = MagicMock()
    ctrl.core = core
    ctrl.is_connected.return_value = True
    ctrl.studio.live().is_live_mode_on.return_value = False

    constraints = SafetyConstraints(
        stage=StageConstraints(x_min=-5000, x_max=5000, y_min=-5000, y_max=5000),
        camera=CameraConstraints(max_exposure_ms=1000.0),
    )
    parsed = types.SimpleNamespace(constraints=constraints)

    home = Path(tempfile.mkdtemp(prefix="gate64-selftest-"))
    import microclaw.knowledge_manager as knowledge_manager
    knowledge_manager.KNOWLEDGE_PATH = home / "knowledge.yaml"

    if args.flip_sign:
        real_move = tools.move_stage_xy
        tools.move_stage_xy = (
            lambda c, g, x, y, **kw: real_move(c, g, -x, -y, **kw)
        )
        print("!! --flip-sign: design/64's defect is restored in memory\n")

    spec = importlib.util.spec_from_file_location(
        "gate64_probe", str(Path(__file__).with_name("64-gate-probe.py"))
    )
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    sys.argv = ["probe", "--out", args.out or str(home / "gate64")]
    probe.load_safety_config_or_exit = lambda path: parsed
    probe.MicroscopeController = lambda **kw: ctrl
    probe.validate_live_rig = lambda *a, **k: None
    # The probe imports these inside main(); patch the modules it resolves from.
    import microclaw.authorization, microclaw.config, microclaw.controller
    microclaw.config.load_safety_config_or_exit = lambda path=None: parsed
    microclaw.controller.MicroscopeController = lambda **kw: ctrl
    microclaw.authorization.validate_live_rig = lambda *a, **k: None

    rc = probe.main()
    print(f"\nPROBE EXIT CODE: {rc}")
    print(f"stage moves commanded: {[(round(x, 2), round(y, 2)) for x, y in core.moves]}")
    print(f"evidence under: {home}")

    by_limb = {r["limb"]: r for r in probe.RESULTS}
    limb_f = by_limb.get("F_one_correction")
    if args.demo_camera:
        coupling = by_limb.get("0_frames_follow_the_stage")
        centring = [by_limb.get("F_one_correction"), by_limb.get("F2_convergence")]
        if coupling is None or coupling["status"] != "NOT EXERCISED":
            print("\nSELFTEST FAIL: limb 0 did not catch a camera that ignores "
                  "the stage.")
            return 1
        if any(r is None or r["status"] == "FAIL" for r in centring):
            print("\nSELFTEST FAIL: a centring limb reported FAIL on a camera "
                  "that cannot answer. That is a false diagnosis about hardware.")
            return 1
        print("\nSELFTEST PASS: limb 0 caught the frozen camera and the centring "
              "limbs stood down instead of blaming the stage.")
        return 0
    if args.flip_sign:
        if limb_f is None or limb_f["status"] != "FAIL":
            print("\nSELFTEST FAIL: the sign defect did not turn limb F red. "
                  "This gate cannot see the bug it exists to catch.")
            return 1
        print("\nSELFTEST PASS: the gate discriminates — limb F caught the flip.")
        return 0
    if rc != 0:
        print("\nSELFTEST FAIL: the probe did not pass against a healthy fake.")
        return 1
    print("\nSELFTEST PASS: every limb ran and passed against a healthy fake.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
