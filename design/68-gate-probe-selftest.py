"""Run design/68's gate probe end to end against fakes, before a rig sees it.

Not a gate artifact and not part of the suite. `CLAUDE.md` requires it: a gate is
code, and handing an operator code nobody executed is the defect this workflow
keeps paying for. design/59a lost a whole demo trip to a `list()` over a Core
collection; design/60b's gate failed its one real limb because the gate's own
fake wrote the filename the glob expected.

**The fake is written from the hardware's behaviour, not from the probe's
expectations.** The load-bearing property here is that the stage is
ASYNCHRONOUS: `set_relative_xy_position` returns immediately, `device_busy`
answers False the whole time, and the measured position converges toward the
target over ~0.2 s of wall clock. A fake that lands instantly would let a probe
which reads once pass — and reading once is precisely the defect this block
exists to fix.

Discrimination, not a green line. Run every mode:

    python design/68-gate-probe-selftest.py                      # 0/A/B/D/E PASS, C NOT EXERCISED
    python design/68-gate-probe-selftest.py --shared-band        # limb B must FAIL
    python design/68-gate-probe-selftest.py --blocked-axis       # limb C must PASS (refusal fires)
    python design/68-gate-probe-selftest.py --untyped-failure    # limb C must FAIL (bare exception)
    python design/68-gate-probe-selftest.py --dead-stage         # limb 0 stands the rest down

Run it from inside the tree under test, or `import microclaw` resolves through
the editable install to whichever checkout `pip install -e .` last pointed at.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from microclaw import controller as move_controller
from microclaw.safety import (CameraConstraints, SafetyConstraints, SafetyGuard,
                              StageConstraints)
from tests.synthetic_optics import OPTICS_CASES, SyntheticOptics


class DoubleVector:
    """What MMCore hands back for an affine: NOT a Python iterable."""

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


class AsyncStageCore:
    """A camera and an XY stage that takes real time to arrive.

    `device_busy` answers False throughout, deliberately: *a device that is not
    busy is not a device that arrived*. A probe that trusts the busy flag, or
    reads position once after dispatch, sees the PRE-move coordinate here — the
    same thing block 56 measured on a real rig as a 22.85 um miss reported as a
    success.
    """

    ARRIVAL_S = 0.2

    def __init__(self, optics, affine_values, dead=False, blocked_axis=None,
                 untyped=False):
        self.optics = optics
        self.affine_values = affine_values
        self.dead = dead                 # commands do nothing at all
        self.blocked_axis = blocked_axis  # "x" or "y": that axis never moves
        self.untyped = untyped           # every read raises, as a down link does
        self._frame = None
        self.moves = []
        self._origin = (optics.pos["x"], optics.pos["y"])
        self._target = (optics.pos["x"], optics.pos["y"])
        self._started = time.monotonic()

    # --- the asynchronous part -----------------------------------------
    def _settle_to_now(self):
        if self.untyped:
            raise RuntimeError(
                "java.lang.Exception: Device XY: serial port not open")
        fraction = min(1.0, (time.monotonic() - self._started) / self.ARRIVAL_S)
        x = self._origin[0] + fraction * (self._target[0] - self._origin[0])
        y = self._origin[1] + fraction * (self._target[1] - self._origin[1])
        self.optics.pos["x"], self.optics.pos["y"] = x, y

    def _command(self, target_x, target_y):
        self.moves.append((target_x, target_y))
        if self.dead:
            return
        self._origin = (self.optics.pos["x"], self.optics.pos["y"])
        if self.blocked_axis == "x":
            target_x = self._origin[0]
        elif self.blocked_axis == "y":
            target_y = self._origin[1]
        self._target = (target_x, target_y)
        self._started = time.monotonic()

    def set_xy_position(self, x, y):
        self._command(float(x), float(y))

    def set_relative_xy_position(self, dx, dy):
        self._command(self.optics.pos["x"] + float(dx),
                      self.optics.pos["y"] + float(dy))

    def get_x_position(self):
        self._settle_to_now()
        return self.optics.pos["x"]

    def get_y_position(self):
        self._settle_to_now()
        return self.optics.pos["y"]

    def device_busy(self, _device):
        return False                      # never busy, and often not arrived

    def get_xy_stage_device(self):
        return "XY"

    def wait_for_device(self, device):
        return None

    # --- identity and camera -------------------------------------------
    def get_camera_device(self):
        return "Camera"

    def get_device_name(self, label):
        return "DemoCamera"

    def get_current_pixel_size_config(self):
        return "Res20x"

    def get_property(self, device, name):
        return "1" if (device, name) == ("Camera", "Binning") else "0"

    def get_roi(self):
        height, width = self.optics.shape
        return Rect(0, 0, width, height)

    def get_pixel_size_affine(self):
        return DoubleVector(self.affine_values)

    def get_pixel_size_um(self):
        return 0.5

    def snap_image(self):
        self._settle_to_now()
        self._frame = self.optics.snap()

    def get_tagged_image(self):
        height, width = self.optics.shape
        return types.SimpleNamespace(
            tags={"Width": width, "Height": height}, pix=self._frame)

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

    def __getattr__(self, name):
        return MagicMock()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shared-band", action="store_true",
                        help="band both axes off the PAIR displacement; limb B must FAIL")
    parser.add_argument("--blocked-axis", action="store_true",
                        help="Y never moves; limb C must PASS (its refusal fires)")
    parser.add_argument("--untyped-failure", action="store_true",
                        help="every bridge read raises; limb C must FAIL, not error")
    parser.add_argument("--dead-stage", action="store_true",
                        help="commands do nothing; limb 0 must stand the rest down")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    # A rig whose camera is rotated 90 degrees relative to the stage, so a limb
    # that confuses the axes cannot pass by symmetry.
    optics = SyntheticOptics(OPTICS_CASES["rot90"])
    a = -np.linalg.inv(optics.m_phys)
    affine_values = [a[0, 0], a[0, 1], 0.0, a[1, 0], a[1, 1], 0.0]

    core = AsyncStageCore(
        optics, affine_values,
        dead=args.dead_stage,
        blocked_axis="y" if args.blocked_axis else None,
        untyped=args.untyped_failure,
    )
    ctrl = MagicMock()
    ctrl.core = core
    ctrl.is_connected.return_value = True
    ctrl.studio.live().is_live_mode_on.return_value = False

    constraints = SafetyConstraints(
        stage=StageConstraints(x_min=-5000, x_max=5000, y_min=-5000, y_max=5000),
        camera=CameraConstraints(max_exposure_ms=1000.0),
    )
    parsed = types.SimpleNamespace(constraints=constraints)

    home = Path(tempfile.mkdtemp(prefix="gate68-selftest-"))
    import microclaw.knowledge_manager as knowledge_manager
    knowledge_manager.KNOWLEDGE_PATH = home / "knowledge.yaml"

    if args.shared_band:
        # The exact defect limb B exists to catch: one band for the pair, so a
        # held axis inherits the moving axis's slack.
        real_band = move_controller._stage_move_band

        def shared(target_um, start_um, band_policy, configured_band_um):
            band, source, kind, unverifiable = real_band(
                target_um, start_um, band_policy, configured_band_um)
            return (max(band, 20.0), source, kind, unverifiable)

        move_controller._stage_move_band = shared
        print("!! --shared-band: both axes now get the wider band\n")

    spec = importlib.util.spec_from_file_location(
        "gate68_probe", str(Path(__file__).with_name("68-gate-probe.py")))
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    control = args.blocked_axis or args.untyped_failure
    sys.argv = ["probe", "--out", args.out or str(home / "gate68")]
    if control:
        sys.argv += ["--control", "--limbs", "C"]
    import microclaw.authorization, microclaw.config, microclaw.controller
    microclaw.config.load_safety_config_or_exit = lambda path=None: parsed
    microclaw.controller.MicroscopeController = lambda **kw: ctrl
    microclaw.authorization.validate_live_rig = lambda *a, **k: None

    rc = probe.main()
    print(f"\nPROBE EXIT CODE: {rc}")
    print(f"evidence under: {home}")

    by_limb = {r["limb"]: r for r in probe.RESULTS}

    def require(name, status, why):
        got = by_limb.get(name)
        if got is None or got["status"] != status:
            print(f"\nSELFTEST FAIL: {name} is "
                  f"{'absent' if got is None else got['status']}, expected "
                  f"{status}. {why}")
            return False
        return True

    if args.dead_stage:
        ok = require("0_xy_stage_responds", "NOT EXERCISED",
                     "A stage that does not move must be caught by the "
                     "precondition, not diagnosed by the limbs downstream of it.")
        wrongly_failed = [r["limb"] for r in probe.RESULTS if r["status"] == "FAIL"]
        if wrongly_failed:
            print(f"\nSELFTEST FAIL: {wrongly_failed} reported FAIL on a stage "
                  "limb 0 already declared unresponsive. That is a confident, "
                  "wrong statement about the code.")
            return 1
        print("\nSELFTEST PASS: limb 0 caught the dead stage and the rest stood down."
              if ok else "")
        return 0 if ok else 1
    if args.untyped_failure:
        # This mode used to assert only FAIL, and passed for the WRONG reason:
        # the limb died in its own pre-read and never called move_stage_xy at
        # all. M2, 2026-08-31, produced exactly that and it looked like a
        # product defect. The assertion now requires the limb to have REACHED
        # the product -- the evidence file only exists if it did -- so a limb
        # that dies in its instrumentation can no longer satisfy this mode.
        evidence = Path(sys.argv[sys.argv.index("--out") + 1]) / "non_response_control.json"
        got = by_limb.get("C_non_response_control", {})
        if not evidence.exists():
            print(f"\nSELFTEST FAIL: {evidence.name} was never written, so limb C "
                  "never called move_stage_xy. Whatever it reported, it did not "
                  "run its mechanism -- that is NOT EXERCISED, never FAIL, and "
                  "never a pass.")
            return 1
        captured = json.loads(evidence.read_text(encoding="utf-8"))
        if captured.get("exception_class") != "XYStageMoveError":
            print(f"\nSELFTEST FAIL: a down link produced "
                  f"{captured.get('exception_class')}, not a typed "
                  "XYStageMoveError carrying the move contract.")
            return 1
        ok = require("C_non_response_control", "PASS",
                     "A link that fails every bridge call is the canonical "
                     "non-response: the product must still refuse by type.")
        if ok:
            print("\nSELFTEST PASS: a down link reached the product and was "
                  f"refused by type; start_um={captured['result'].get('start_um')} "
                  "(legitimately null -- the start is genuinely unknown).")
        return 0 if ok else 1
    if args.blocked_axis:
        ok = require("C_non_response_control", "PASS",
                     "A blocked axis must produce a typed XYStageMoveError that "
                     "names it. If this does not fire, the control cannot fire "
                     "on the rig either.")
        detail = by_limb.get("C_non_response_control", {}).get("detail", "")
        if ok and "'y'" not in detail and '"y"' not in detail:
            print(f"\nSELFTEST FAIL: the refusal did not name Y: {detail}")
            return 1
        print("\nSELFTEST PASS: a blocked Y axis refused, by type, naming Y."
              if ok else "")
        return 0 if ok else 1
    if args.shared_band:
        ok = require("B_per_axis_bands", "FAIL",
                     "A shared band is the one defect this limb exists to see. "
                     "If it stays green, the limb is decorative.")
        print("\nSELFTEST PASS: the gate discriminates — limb B caught the "
              "shared band." if ok else "")
        return 0 if ok else 1

    # Healthy fake: everything but the physical control must pass.
    ok = all([
        require("0_xy_stage_responds", "PASS", ""),
        require("A_move_settles", "PASS", ""),
        require("B_per_axis_bands", "PASS", ""),
        require("D_centring_sub_band", "PASS", ""),
        require("E_export_carries_contract", "PASS", ""),
        require("C_non_response_control", "NOT EXERCISED",
                "Without --control this limb must stand down, never pass."),
    ])
    if not ok:
        return 1
    print("\nSELFTEST PASS: every runnable limb passed against a healthy "
          "asynchronous stage, and the physical control stood down.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
