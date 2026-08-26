#!/usr/bin/env python
"""Per-frame statistics of a saved NDTiff, computed without importing microclaw.

Block 55b's optical corroboration limb. The hook log records where the plan
*says* the axis went, and it is written by the code under test; this reads the
pixels the camera actually produced and is written by nobody involved. If the
three frames of a plan-only sweep are identical, the plan did not move anything
between exposures, whatever the log says -- that is the Nikon failure, and it is
the one thing a log cannot rule out about itself.

It imports only tifffile and numpy on purpose. Nothing here touches
Micro-Manager, no hardware moves, and it is safe to run at any time.

PowerShell:

    uv run python design\\55-frame-means.py "<dataset directory>"

It prints one line per frame and a verdict line. Exit 1 if every frame is
identical, which for a sweep is a FAIL and for a fixed-position run is expected
-- so read the verdict against what the run was supposed to do.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import tifffile


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: 55-frame-means.py <dataset directory or NDTiff .tif>")
        return 2
    target = Path(sys.argv[1])
    if target.is_dir():
        stacks = sorted(target.glob("*NDTiffStack*.tif"))
        if not stacks:
            print(f"FAIL  no *NDTiffStack*.tif under {target}")
            return 2
        target = stacks[0]
    print(f"dataset: {target}")
    means = []
    with tifffile.TiffFile(target) as handle:
        for index, page in enumerate(handle.pages):
            frame = page.asarray()
            means.append(float(frame.mean()))
            print(f"  frame {index}: mean={frame.mean():.3f} "
                  f"min={frame.min()} max={frame.max()} "
                  f"shape={frame.shape} {frame.dtype}")
    if len(means) < 2:
        print("only one frame; nothing to compare")
        return 0
    spread = max(means) - min(means)
    identical = spread == 0.0
    print("")
    print(f"frames: {len(means)}   mean spread: {spread:.6f}")
    print("ALL FRAMES IDENTICAL" if identical else "FRAMES DIFFER")
    return 1 if identical else 0


if __name__ == "__main__":
    sys.exit(main())
