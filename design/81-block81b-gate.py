#!/usr/bin/env python
"""Block 81b gate: per-field component counts and their detection evidence.

This gate is a program, not a runbook: every limb computes, so each reports
independently, one failure never hides the limbs behind it, and the script owns
its own log and exits nonzero. The only human step is separate and comes after
it -- looking at the annotated fields it writes.

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


@limb("C. 81 positions exhaust the artifact budget, disclose it, and still count")
def limb_c(archive, out):
    result = analyse(archive, out, MANY, "connected_components", "frames", "c-many",
                     axis_selection={"time": 0})
    if result["status"] != "completed":
        return "FAIL", f"a limit must not fail the measurement; status {result['status']}: {result['failure']}"
    annotations = result.get("annotations") or {}
    counted = len(result["observations"])
    if counted != 81:
        return "FAIL", f"expected 81 per-field observations, got {counted}"
    if annotations.get("written", 0) >= counted:
        return ("NOT EXERCISED",
                f"all {annotations.get('written')} annotations fit; the default budget "
                "did not bind, so the degrade path never ran")
    if not annotations.get("reason"):
        return "FAIL", f"annotations stopped at {annotations.get('written')} with no disclosed reason"
    return "PASS", (f"{counted} fields counted, {annotations['written']} annotated, "
                    f"disclosed reason: {annotations['reason']!r}")


@limb("D. the mosaic path now writes a detection overlay too (R43)")
def limb_d(archive, out):
    result = analyse(archive, out, BEADS, "connected_components", "stage_coordinate_mosaic",
                     "d-mosaic", axis_selection={"z": 0})
    if result["status"] != "completed":
        return "FAIL", f"status {result['status']}: {result['failure']}"
    overlays = [a for a in result["artifacts"]
                if Path(a["relative_path"]).name.startswith("components-")]
    if not overlays:
        return "FAIL", "no detection overlay artifact was written beside the mosaic"
    return "PASS", f"{len(overlays)} overlay artifact(s): {[a['relative_path'] for a in overlays]}"


@limb("E. every count is linked to the pixels it came from")
def limb_e(archive, out):
    """The evidence must be reachable *from the number*, not merely present."""
    manifest = Path(out) / "a-beads" / "analysis-manifest.json"
    if not manifest.exists():
        raise NotExercised("limb A did not produce a manifest")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    digests = {a["sha256"] for a in payload["artifacts"]}
    unlinked = [o.get("position") for o in payload["observations"]
                if o.get("artifact_sha256") not in digests]
    if unlinked:
        return "FAIL", f"observations whose artifact_sha256 names no written artifact: {unlinked}"
    return "PASS", f"all {len(payload['observations'])} observations link to a written artifact"


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

    for run in (limb_a, limb_b, limb_c, limb_d, limb_e, limb_f):
        run(args.archive, out)

    print("\n--- summary ---", flush=True)
    for name, verdict, _ in RESULTS:
        print(f"{verdict:>13}  {name}", flush=True)
    failed = [n for n, v, _ in RESULTS if v == "FAIL"]
    unexercised = [n for n, v, _ in RESULTS if v == "NOT EXERCISED"]
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
