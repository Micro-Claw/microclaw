#!/usr/bin/env python
"""Halt a gate step whose premise is a camera ROI the operator must set by hand.

Block 54d's 2026-08-19 Nikon gate failed silently for want of this. Its Step 5
required a ~32x32 camera crop, described in prose beside the step rather than in
the step's command block. The crop was never made, `get_roi` read 1024x1024, the
`min_contrast` key the step existed to observe was correctly absent -- and the
step was still reported as a pass. Nothing in the typed agent prompt could fail
when the precondition was skipped.

So the precondition gets a command that either passes or exits nonzero, and the
runbook puts it in front of the step. It also prints the threshold the step
expects, computed here from the design/54 formula rather than read back from
microclaw, so the number being checked is not produced by the code under test.

BY DEFAULT NOTHING HERE MOVES A STAGE, OPENS A SHUTTER, OR FIRES A CAMERA.
It is one `core.get_roi()` read. It writes nothing to Micro-Manager on any exit
path (feedback_no_state_change_on_exit).

PowerShell:

    python design\\54-roi-precondition.py --expect cropped
    Write-Host "precondition exit code (expected 0):" $LASTEXITCODE

    python design\\54-roi-precondition.py --expect full
    Write-Host "precondition exit code (expected 0):" $LASTEXITCODE

The thresholds are gate policy, not rig facts: "cropped" is at most 256x256
worth of pixels and "full" is at least 512x512 worth, which separates any
plausible small crop from any plausible full sensor without naming a camera.
"""
from __future__ import annotations

import argparse
import math
import sys

N_REF = 1024 * 1024          # design/54 54d: fixed, never the live sensor
MIN_CONTRAST = 0.15
CROPPED_MAX_PIXELS = 256 * 256
FULL_MIN_PIXELS = 512 * 512


def expected_min_contrast(n_pixels: int) -> float:
    """The design/54 threshold, written out rather than imported from microclaw.

    Importing `autofocus.contrast_threshold` would check the payload against the
    same code that produced it. This is the independent copy.
    """
    return max(MIN_CONTRAST, MIN_CONTRAST * math.sqrt(N_REF / n_pixels))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect", required=True, choices=("cropped", "full"),
                        help="what the camera ROI must already be")
    parser.add_argument("--region", type=int, nargs=4, metavar=("X", "Y", "W", "H"),
                        help="also print the expected threshold for this software "
                             "region, and check it fits the current frame")
    parser.add_argument("--port", type=int, default=4827)
    args = parser.parse_args()

    try:
        from pycromanager import Core
        core = Core(port=args.port)
        roi = core.get_roi()
        x, y = int(roi.x), int(roi.y)
        width, height = int(roi.width), int(roi.height)
    except Exception as exc:
        print(f"STOP: could not read the camera ROI over the bridge: "
              f"{type(exc).__name__}: {exc}")
        print("Micro-Manager must be open with the pycro-manager ZMQ server on.")
        return 2

    n_pixels = width * height
    print(f"camera ROI: x={x} y={y} width={width} height={height} "
          f"({n_pixels} px)")
    print(f"expected coarse.min_contrast for this frame: "
          f"{expected_min_contrast(n_pixels):.3f}")

    if args.region is not None:
        rx, ry, rw, rh = args.region
        if rw <= 0 or rh <= 0 or rx < 0 or ry < 0:
            print(f"STOP: region {args.region} is degenerate.")
            return 1
        if rx + rw > width or ry + rh > height:
            print(f"STOP: region {args.region} does not fit frame "
                  f"[{width}, {height}]. These are the numbers the step will "
                  f"pass to run_autofocus; fix them before running it.")
            return 1
        print(f"region {[rx, ry, rw, rh]}: {rw * rh} px")
        print(f"expected coarse.min_contrast for this region: "
              f"{expected_min_contrast(rw * rh):.3f}")

    if args.expect == "cropped":
        if n_pixels > CROPPED_MAX_PIXELS:
            print(f"STOP: this step needs a CROPPED camera ROI and the sensor is "
                  f"reading {width}x{height}.")
            print("In Micro-Manager: draw a small rectangle in the Preview "
                  "(aim for roughly 32x32) and click the ROI (crop) button.")
            print("Then re-run this command. Do NOT run the step until it "
                  "prints PRECONDITION MET.")
            return 1
        print("PRECONDITION MET: the camera is cropped.")
        return 0

    if n_pixels < FULL_MIN_PIXELS:
        print(f"STOP: this step needs the FULL frame and the sensor is reading "
              f"{width}x{height}.")
        print("In Micro-Manager: click the clear-ROI button to restore the full "
              "frame, then re-run this command.")
        return 1
    print("PRECONDITION MET: the camera is at full frame.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
