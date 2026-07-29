"""Block 9 landmark check: does the mosaic place overlapping content consistently?

Read-only. Takes no exposure, moves no stage, opens no hardware connection.

The mosaic tool is deterministic and self-consistent by construction, which is
exactly why a green test suite cannot tell us the placement is *correct*. This
script supplies the missing evidence without turning placement into
registration: it renders each tile separately through the SAME geometry, finds
pairs whose placed footprints overlap, and cross-correlates the two renderings
inside that overlap.

If the resolved affine and our coordinate convention are right, the same
physical feature lands in the same output cell from either tile, so the residual
shift is ~0. A transposed, mirrored, or 90-degree-wrong affine leaves tile
centres correct (they come from stage XY, not the affine) while rotating each
tile's content, so overlaps disagree by a large, systematic amount. That makes
this a real falsification test rather than a consistency tautology.

What it CANNOT settle: a mosaic that is internally perfect but globally flipped
relative to the specimen. Every tile would be wrong identically and every
overlap would still agree. Only a human comparing a recognisable asymmetric
feature against the sample can rule that out -- hence the PNG this writes.

Usage:
    python design/29-block9-landmark-check.py <dataset> <cal.json> [--axis time=0]

<cal.json> is a calibration artifact, or any mosaic manifest this tool wrote
(both are accepted). To rebuild M2's from the measured Res1 values -- the
knowledge base does not hold it, and nothing in the repo does either:

    import json
    from microclaw.calibration import (
        StageCameraAffine, canonical_affine_payload, affine_payload_hash)
    a = StageCameraAffine(0.0, 0.127, -0.127, 0.0, "obj", 1, 0.127)
    json.dump({"payload": canonical_affine_payload(a),
               "payload_sha256": affine_payload_hash(a),
               "camera_device": "Andor",
               "camera_model": "| iXon Ultra | DU897_BV | 8172 |",
               "roi": [0, 0, 512, 512]},          # calibrated at full frame
              open("res1.json", "w"))

The ROI is deliberately the calibration's, not the dataset's; a difference is
recorded rather than refused (see design/29's 2026-07-29 section).

Fixture paths on this machine, for whoever picks this up:
    run_a_2      OneDrive-Personal/Microclaw/microclaw-json-histories/run-a/run_a_2
    M5 2500-tile ~/Documents/Documents - Beyonce/Projects/Micro-Claw/scan488_900_1
    spiral       ~/Documents/.../260720_PD_testMicroClaw_M2  (unusable: no intended XY)
Copy before reading; treat the originals as read-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from ndstorage import Dataset
from skimage.registration import phase_cross_correlation

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from microclaw.calibration import StageCameraAffine, canonical_affine_payload  # noqa: E402
from microclaw.dataset_mosaic import MosaicGeometry, assemble_stage_coordinate_mosaic  # noqa: E402


def _parse_axis(values: list[str]) -> dict:
    selection = {}
    for item in values or []:
        key, _, raw = item.partition("=")
        try:
            selection[key] = int(raw)
        except ValueError:
            selection[key] = raw
    return selection


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset")
    parser.add_argument("calibration", help="artifact JSON holding the affine payload")
    parser.add_argument("--axis", action="append", default=[],
                        help="fix one non-position axis, e.g. --axis time=0")
    parser.add_argument("--min-overlap-px", type=int, default=400)
    parser.add_argument("--out", default="landmark_check")
    args = parser.parse_args()

    selection = _parse_axis(args.axis)
    dataset = Dataset(args.dataset)
    payload = json.loads(Path(args.calibration).read_text(encoding="utf-8"))
    payload = payload.get("manifest_payload", payload)
    payload = payload.get("calibration_identity", payload)
    affine = StageCameraAffine(**canonical_affine_payload(payload["payload"]))
    geometry = MosaicGeometry(affine, affine.pixel_size_um)
    print(f"affine pixel->stage 2x2 : [[{affine.a}, {affine.b}], [{affine.c}, {affine.d}]]")

    positions = sorted(dataset.axes["position"], key=str)
    tiles = []
    for position in positions:
        coords = dict(selection, position=position)
        if not dataset.has_image(**coords):
            continue
        metadata = dataset.read_metadata(**coords)
        if "XPosition_um_Intended" not in metadata:
            print("FAIL: dataset carries no intended XY; nothing to check.")
            return 2
        tiles.append((position,
                      dataset.read_image(**coords),
                      float(metadata["XPosition_um_Intended"]),
                      float(metadata["YPosition_um_Intended"])))
    print(f"tiles                   : {len(tiles)}")

    # One shared canvas geometry for every rendering, so output cells are
    # directly comparable between tiles.
    every = [(image, x, y) for _, image, x, y in tiles]
    full = assemble_stage_coordinate_mosaic(every, geometry)
    print(f"mosaic                  : {full['mosaic'].shape}  "
          f"coverage {full['coverage_mask'].mean():.4f}")

    class OneTile:
        """Render one tile onto the FULL canvas, so output cells stay comparable.

        Every tile is still fed, which keeps the canvas bounds and origin
        identical to the full mosaic -- rendering a tile alone would give it its
        own origin, and a sub-cell difference between two origins would show up
        as exactly the residual this script is trying to measure.

        The others are fed as zeros, and the kept tile is fed LAST so that
        'later frames overwrite earlier' repaints its whole footprint. Feeding
        it in its original position instead lets a later zero tile blank the
        overlap region, which is precisely the region under test.
        """

        def __init__(self, keep: int):
            self.order = [k for k in range(len(every)) if k != keep] + [keep]
            self.keep = keep

        def __iter__(self):
            for index in self.order:
                image, x, y = every[index]
                yield ((image if index == self.keep else np.zeros_like(image)), x, y)

    rendered = []
    for index in range(len(tiles)):
        result = assemble_stage_coordinate_mosaic(OneTile(index), geometry)
        rendered.append(result["mosaic"])

    residuals = []
    for i in range(len(tiles)):
        for j in range(i + 1, len(tiles)):
            both = (rendered[i] > 0) & (rendered[j] > 0)
            count = int(np.count_nonzero(both))
            if count < args.min_overlap_px:
                continue
            rows, cols = np.nonzero(both)
            box = (slice(rows.min(), rows.max() + 1), slice(cols.min(), cols.max() + 1))
            a = rendered[i][box].astype(np.float64)
            b = rendered[j][box].astype(np.float64)
            if a.std() < 1e-9 or b.std() < 1e-9:
                continue  # featureless overlap measures nothing
            shift, _, _ = phase_cross_correlation(a, b, upsample_factor=10)
            # Classify by the intended stage displacement between the pair.
            # design/29 measured X cleanly on run_a_2 and could not resolve Y at
            # all, so a residual that is small along one stage axis and large
            # along the other is the expected signature of stage error, not of a
            # wrong transform. Reporting one blended median hides that.
            step_x = tiles[j][2] - tiles[i][2]
            step_y = tiles[j][3] - tiles[i][3]
            if abs(step_y) < 1e-6:
                axis = "stage-X"
            elif abs(step_x) < 1e-6:
                axis = "stage-Y"
            else:
                axis = "diagonal"
            residuals.append((tiles[i][0], tiles[j][0], count,
                              float(shift[0]), float(shift[1]), axis))

    if not residuals:
        print("\nNo overlapping, feature-bearing tile pairs found.")
        print("This dataset cannot answer the landmark question: acquire a grid")
        print("whose step is smaller than the field of view, on structured sample.")
        return 2

    print(f"\noverlapping pairs       : {len(residuals)}")
    print(f"{'tile A':<22}{'tile B':<22}{'px':>8}{'d_row':>9}{'d_col':>9}  axis")
    for a, b, count, drow, dcol, axis in residuals[:20]:
        print(f"{str(a):<22}{str(b):<22}{count:>8}{drow:>9.2f}{dcol:>9.2f}  {axis}")
    if len(residuals) > 20:
        print(f"... {len(residuals) - 20} more")

    print("\nresidual |shift| px, by intended stage displacement:")
    for axis in ("stage-X", "stage-Y", "diagonal"):
        group = [r for r in residuals if r[5] == axis]
        if not group:
            continue
        magnitudes = np.hypot([r[3] for r in group], [r[4] for r in group])
        print(f"  {axis:<10} n={len(group):<4} median {np.median(magnitudes):7.2f}  "
              f"p90 {np.percentile(magnitudes, 90):7.2f}  max {magnitudes.max():7.2f}")

    print("""
Reading this. A wrong affine is a property of the TRANSFORM and cannot be
axis-selective: it would inflate every group together, because each tile's
content is rotated identically while tile centres come from stage XY. A residual
that is small along one stage axis and large along the other is therefore
evidence of STAGE ERROR -- intended XY not matching achieved XY -- which
design/29 4 already states this mosaic cannot correct and does not model.

So: judge the transform by the group with the smallest, most consistent
residual, and treat a large group as a measurement of that axis's positioning
error. Only if EVERY group is large is the transform itself in question.""")

    try:
        from PIL import Image
        canvas = full["mosaic"].astype(np.float64)
        lo, hi = np.percentile(canvas[full["coverage_mask"]], [1, 99.5])
        scaled = np.clip((canvas - lo) / max(hi - lo, 1e-9), 0, 1)
        Image.fromarray((scaled * 255).astype(np.uint8)).save(f"{args.out}.png")
        print(f"\nwrote {args.out}.png -- look at this for GLOBAL orientation, which")
        print("no overlap residual can catch: is a recognisable asymmetric feature")
        print("oriented as it is on the microscope, or mirrored/rotated as a whole?")
    except ImportError:
        print("\n(Pillow not installed; skipped the PNG.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
