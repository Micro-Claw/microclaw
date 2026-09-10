#!/usr/bin/env python
"""Block 81b gate: per-field component counts and their detection evidence.

This gate is a program, not a runbook: every limb computes, so each reports
independently, one failure never hides the limbs behind it, and the script owns
its own log and exits nonzero. It also renders one figure for a human to judge
-- the raw field with the reported bounding boxes drawn beside the objects,
which is the only way to check that a count corresponds to what is in the
image.

The product writes no annotation artifacts. It did, they were unreadable, and
they were removed (see design/81 and register row R43). The figure below is
the GATE's own rendering, for scoring, and is not something Microclaw ships.

It needs **no microscope**. Every limb runs over real saved acquisitions
already in the evidence archive, which between them supply the three things
block 81b's entry wanted from a demo machine: real saved coordinates, genuine
field overlap, and a real non-axis-aligned calibration. Measured 2026-09-10:

  dataset                        positions  overlap  basis
  38-composite-hooks-m5/gate_h1      5        35.9%  sheared (Hamamatsu)
  nestor/mt_scan_150um              81        14.1%  sheared (Hamamatsu)
  20260909_ZM_beads (the incident)   9         0.4%  rotated 90 deg (Andor)

Usage:

    .venv/bin/python design/81-block81b-gate.py --archive "<archive root>" \
        --out <evidence directory>

Every limb prints PASS, FAIL or NOT EXERCISED. NOT EXERCISED is never a pass:
it means the limb could not run its mechanism, and it is reported as such.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import traceback
from pathlib import Path

RESULTS: list[tuple[str, str, str]] = []
LOG_LINES: list[str] = []

# Three real acquisitions, each chosen for a property no fake supplies.
BEADS = "20260909_ZM_beads/beads_af_run2_1"          # the incident's own 3x3
OVERLAP = "38-composite-hooks-m5/gate_h1/gate_h1_1"  # 35.9% real field overlap
MANY = "nestor-06082026/microclaw_data/mt_scan_150um/mt150_1"  # 81 positions


def emit(name: str, verdict: str, detail: str) -> None:
    RESULTS.append((name, verdict, detail))
    line = f"[{verdict:>13}] {name}: {detail}"
    print(line, flush=True)
    LOG_LINES.append(line)


def limb(name):
    """Run one limb in isolation. An exception is that limb's FAIL, never the run's."""
    def decorate(fn):
        def run(*args, **kwargs):
            try:
                verdict, detail = fn(*args, **kwargs)
            except NotExercised as reason:
                emit(name, "NOT EXERCISED", str(reason))
                return
            except Exception:
                emit(name, "FAIL", "raised: " + traceback.format_exc(limit=3).strip().replace("\n", " | "))
                return
            emit(name, verdict, detail)
        return run
    return decorate


class NotExercised(Exception):
    pass


def analyse(archive, out, dataset, adapter, input_kind, name, **kwargs):
    """Run the real product entry point over a real saved dataset."""
    from microclaw.completed_dataset import run_analysis_on_saved_dataset
    from microclaw.safety import SafetyGuard, SafetyConstraints

    source = Path(archive) / dataset
    if not source.exists():
        raise NotExercised(f"archive has no {dataset}")
    guard = SafetyGuard(SafetyConstraints(workspace_dir=str(out)))
    target = Path(out) / name
    if target.exists():
        shutil.rmtree(target)
    return run_analysis_on_saved_dataset(
        guard, str(source), adapter, kwargs.pop("axis_selection", {}), input_kind,
        kwargs.pop("parameters", {}), str(target), **kwargs,
    )


@limb("A. per-field counts on the incident's own 3x3 bead grid")
def limb_a(archive, out):
    result = analyse(archive, out, BEADS, "connected_components", "frames", "a-beads",
                     axis_selection={"z": 0})
    if result["status"] != "completed":
        return "FAIL", f"status {result['status']}: {result['failure']}"
    counts = {o.get("position"): o["result"]["n_components"] for o in result["observations"]}
    if len(counts) != 9:
        return "FAIL", f"expected 9 per-position observations, got {len(counts)}: {sorted(counts)}"
    if any(o.get("position") is None for o in result["observations"]):
        return "FAIL", "an observation carries no position coordinate"
    return "PASS", f"9 fields, counts by saved position {json.dumps(counts, sort_keys=True)}"


@limb("B. the same signal in two overlapping fields is counted in both")
def limb_b(archive, out):
    result = analyse(archive, out, OVERLAP, "connected_components", "frames", "b-overlap",
                     axis_selection={"time": 0})
    if result["status"] != "completed":
        return "FAIL", f"status {result['status']}: {result['failure']}"
    observed = [(o.get("position"), o["result"]["n_components"]) for o in result["observations"]]
    total = sum(count for _, count in observed)
    # Two fields overlapping by ~36% of the canvas must share stage territory:
    # check that some component's centroid from one field lands inside another
    # field's recorded stage extent. That is the semantic D4(a) chose.
    boxes = {}
    for observation in result["observations"]:
        for component in observation["result"]["objects"]:
            box = component.get("bounding_box_stage_hull_um")
            if box is None:
                return "FAIL", "a source-frame component reported no stage hull"
            boxes.setdefault(observation.get("position"), []).append(
                (component["centroid_stage_um"], box))
    shared = 0
    for left, left_items in boxes.items():
        for right, right_items in boxes.items():
            if left >= right:
                continue
            for (centroid, _) in left_items:
                for (_, box) in right_items:
                    if (box["x_min"] <= centroid[0] <= box["x_max"]
                            and box["y_min"] <= centroid[1] <= box["y_max"]):
                        shared += 1
    if not result.get("count_semantics") or "not a unique object total" not in result["count_semantics"]:
        return "FAIL", "the result does not say the per-field sum is not a unique total"
    return ("PASS" if shared else "FAIL",
            f"{len(observed)} fields, per-field counts {observed}, sum {total}, "
            f"{shared} centroid(s) falling inside another field's stage hull "
            f"(a shared object counted in both fields is the expected finding)")


@limb("G. an unfiltered count discloses that it is made of single pixels")
def limb_g(archive, out):
    manifest = Path(out) / "a-beads" / "analysis-manifest.json"
    if not manifest.exists():
        raise NotExercised("limb A did not produce a manifest")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload["parameters"].get("min_area_um2"):
        raise NotExercised("limb A did not run with the default area filter")
    silent = []
    for observation in payload["observations"]:
        result = observation["result"]
        distribution = result.get("component_size_distribution") or {}
        singles = distribution.get("single_pixel_components", 0)
        total = distribution.get("n_components", 0)
        notes = result.get("review_notes") or []
        if total and singles * 2 > total and not any("min_area_um2" in n for n in notes):
            silent.append(observation.get("position"))
        if observation.get("status") != "observed":
            return "FAIL", f"{observation.get('position')} changed status to {observation.get('status')!r}"
    if silent:
        return "FAIL", f"singleton-dominated counts with no review note naming the remedy: {silent}"
    worst = max(payload["observations"],
                key=lambda o: (o["result"]["component_size_distribution"] or {}).get(
                    "single_pixel_fraction", 0))
    distribution = worst["result"]["component_size_distribution"]
    return "PASS", (
        f"every singleton-dominated field carries a note naming min_area_um2 and stays "
        f"'observed'; worst is {worst.get('position')} at "
        f"{distribution['single_pixel_components']}/{distribution['n_components']} "
        f"({distribution['single_pixel_fraction']:.0%}) single pixels, largest component "
        f"{distribution['n_pixels']['max']} px")


@limb("C. every selected field is counted, at 81 positions as well as 9")
def limb_c(archive, out):
    """Scale, without the artifact budget that used to be this limb's subject."""
    result = analyse(archive, out, MANY, "connected_components", "frames", "c-many",
                     axis_selection={"time": 0})
    if result["status"] != "completed":
        return "FAIL", f"status {result['status']}: {result['failure']}"
    counted = len(result["observations"])
    if counted != 81:
        return "FAIL", f"expected 81 per-field observations, got {counted}"
    if result.get("artifacts"):
        return "FAIL", ("a frames run wrote artifacts; the annotation feature was "
                        f"removed and should write none: {result['artifacts']}")
    positions = {o.get("position") for o in result["observations"]}
    return "PASS", (f"{counted} fields counted across {len(positions)} saved positions, "
                    "no artifacts written")


@limb("D. the same physical object gets the same stage coordinate from two fields")
def limb_d(archive, out):
    """The real test of D4(a)'s geometry, and the one a count alone cannot give.

    If the recorded affine, the frame-centre convention and the intended stage
    coordinates compose correctly, one bead seen from two overlapping fields
    must land at one stage position. This is measured on a SHEARED calibration,
    where a sign or a transpose would show up immediately.
    """
    import numpy as np

    result = analyse(archive, out, OVERLAP, "connected_components", "frames", "d-agree",
                     axis_selection={"time": 0}, parameters={"min_area_um2": 0.5})
    if result["status"] != "completed":
        return "FAIL", f"status {result['status']}: {result['failure']}"
    by_field = {o.get("position"): np.array(
        [c["centroid_stage_um"] for c in o["result"]["objects"]] or [[np.nan, np.nan]])
        for o in result["observations"]}
    separations = []
    names = sorted(by_field)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            P, Q = by_field[left], by_field[right]
            if np.isnan(P).any() or np.isnan(Q).any():
                continue
            distance = np.linalg.norm(P[:, None, :] - Q[None, :, :], axis=2)
            separations += [d for d in distance.min(axis=1) if d < 1.0]
    if len(separations) < 3:
        return ("NOT EXERCISED",
                f"only {len(separations)} cross-field object match(es) under 1 um; "
                "this dataset's overlap did not put enough objects in two fields")
    separations = np.array(separations)
    pixel_um = 0.10557372682605012      # this dataset's recorded calibration
    median_px = float(np.median(separations) / pixel_um)
    # A transpose or a sign error puts this in the tens of pixels or worse.
    # Anything at or below a few pixels says the geometry composes; it does not
    # certify the stage's own positioning accuracy, which is not ours.
    if median_px > 10:
        return "FAIL", (f"{len(separations)} matched pairs disagree by a median of "
                        f"{np.median(separations) * 1000:.0f} nm ({median_px:.1f} px): "
                        "the stage geometry does not compose")
    return "PASS", (
        f"{len(separations)} matched pairs, median {np.median(separations) * 1000:.0f} nm "
        f"({median_px:.2f} px), max {separations.max() * 1000:.0f} nm. Note the residual "
        "is NOT attributed: stage positioning against the recorded intended position, "
        "a calibration error and drift all look like this from one dataset")


@limb("E. render the field and its detections for a human to judge")
def limb_e(archive, out):
    """Not scoreable by this program. It writes the figure and says where it is.

    A count that agrees with itself is not a count that found the objects. The
    only check for that is an eye on the pixels, so this limb always reports
    NOT EXERCISED -- it is a deliverable, not a verdict, and the run is not
    complete until a person has answered the question in the runbook.
    """
    import numpy as np
    from PIL import Image
    from ndstorage import Dataset

    source = Path(archive) / BEADS
    if not source.exists():
        raise NotExercised(f"archive has no {BEADS}")
    result = analyse(archive, out, BEADS, "connected_components", "frames", "e-figure",
                     axis_selection={"z": 0}, parameters={"min_area_um2": 0.2})
    if result["status"] != "completed":
        return "FAIL", f"status {result['status']}: {result['failure']}"
    dataset = Dataset(str(source))
    written = []
    for observation in result["observations"]:
        position = observation.get("position")
        raw = np.asarray(dataset.read_image(position=position, z=0)).astype(float)
        # Stretch for the BACKGROUND, not the peak. open_artifact's percentile
        # stretch is set by the brightest bead and renders this field 98.9%
        # black; that is a real defect in a shared display path and it is why
        # this figure is built here rather than read back from an artifact.
        background = float(np.median(raw))
        noise = 1.4826 * float(np.median(np.abs(raw - background)))
        span = np.clip((raw - (background - 3 * noise)) / max(23 * noise, 1e-9), 0, 1)
        rgb = np.dstack([span, span, span])
        for component in observation["result"]["objects"]:
            x0, y0, x1, y1 = component["bounding_box_px"]
            x0, y0 = max(0, x0 - 3), max(0, y0 - 3)
            x1 = min(raw.shape[1] - 1, x1 + 2)
            y1 = min(raw.shape[0] - 1, y1 + 2)
            for rows, cols in ((slice(y0, y1 + 1), [x0, x1]), ([y0, y1], slice(x0, x1 + 1))):
                rgb[rows, cols, 0] = 1.0
                rgb[rows, cols, 1] = 0.15
                rgb[rows, cols, 2] = 0.15
        path = Path(out) / f"figure-{position}.png"
        Image.fromarray((rgb * 255).astype(np.uint8)).resize(
            (raw.shape[1] * 5, raw.shape[0] * 5), Image.NEAREST).save(path)
        written.append((position, len(observation["result"]["objects"])))
    return ("NOT EXERCISED",
            f"wrote {len(written)} figures to {out} as figure-<position>.png "
            f"(counts {written}). A person must answer the runbook's question about "
            "these; this program cannot.")


@limb("F. tile placements carry saved identity, from the dataset not a position list")
def limb_f(archive, out):
    from microclaw.tools import build_stage_coordinate_mosaic
    from microclaw.safety import SafetyGuard, SafetyConstraints
    source = Path(archive) / BEADS
    if not source.exists():
        raise NotExercised(f"archive has no {BEADS}")
    guard = SafetyGuard(SafetyConstraints(workspace_dir=str(out)))
    target = Path(out) / "f-placements"
    target.mkdir(parents=True, exist_ok=True)
    result = build_stage_coordinate_mosaic(
        None, guard, str(source), str(target / "mosaic.tiff"), {"z": 0},
    )
    placements = result.get("tile_placements")
    if not placements:
        return "FAIL", "the manifest carries no tile_placements"
    missing = [p for p in placements if not p.get("coordinate") or p.get("intended_xy_um") is None]
    if missing:
        return "FAIL", f"{len(missing)} placement(s) lack saved coordinate or intended XY"
    named = [p for p in placements if p.get("position_name")]
    return "PASS", (f"{len(placements)} placements, {len(named)} carrying a saved PositionName, "
                    f"first: {json.dumps(placements[0], sort_keys=True)[:220]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True,
                        help="evidence archive root holding the three real datasets")
    parser.add_argument("--out", required=True, help="directory for this run's evidence")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for run in (limb_a, limb_b, limb_c, limb_d, limb_e, limb_f, limb_g):
        run(args.archive, out)

    print("\n--- summary ---", flush=True)
    for name, verdict, _ in RESULTS:
        print(f"{verdict:>13}  {name}", flush=True)
    failed = [n for n, v, _ in RESULTS if v == "FAIL"]
    # Limb E always reports NOT EXERCISED by design -- it writes a figure for a
    # person and cannot score it. Every other NOT EXERCISED is a limb that could
    # not run its mechanism, and that is never a pass.
    unexercised = [n for n, v, _ in RESULTS if v == "NOT EXERCISED" and not n.startswith("E.")]
    verdict = "BLOCK 81b GATE PASSED" if not failed and not unexercised else "BLOCK 81b GATE INCOMPLETE"
    if failed:
        verdict = "BLOCK 81b GATE FAILED"
    print(verdict, flush=True)
    LOG_LINES.append(verdict)
    (out / "gate-log.txt").write_text("\n".join(LOG_LINES) + "\n", encoding="utf-8")
    (out / "gate-results.json").write_text(
        json.dumps([{"limb": n, "verdict": v, "detail": d} for n, v, d in RESULTS], indent=2),
        encoding="utf-8")
    return 1 if (failed or unexercised) else 0


if __name__ == "__main__":
    sys.exit(main())
