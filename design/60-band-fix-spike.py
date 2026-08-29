"""Demo-machine spike: does the 4 GiB disclosure have a blind band, and does
measuring per-frame metadata close it?

The shipped disclosure tests `n > MAX_FILE_SIZE // (w*h*bpp)` -- the RAW pixel
bound, `N_raw`. A file also holds per-frame metadata and an IFD, so it fills at
`N_true = MAX_FILE_SIZE // (w*h*bpp + m)`. Every run with `N_true < n <= N_raw`
crosses a 4 GiB boundary **and is never disclosed**. The band's width is exactly
`m / (w*h*bpp)`.

`design/60-metadata-size-survey.py` measured `m` over 732 archived datasets:

* **Within** a dataset it is essentially constant -- median spread 0.00%, 90th
  percentile 0.05%, worst 0.33%, none above 5%.
* **Between** rigs it moves 3.8x (4,112 to 15,553 B) and tracks the *config*,
  not the ROI: M2 is ~14,150 B at both 150x150 and 512x512; M5 is ~15,400 B
  across nine ROIs.

So `m` is a per-rig constant that must be **measured, not modelled** -- which is
what the candidate fix does. The bands that follow are real: M2 at 150x150 is
31.9% wide (72,358 real vs 95,443 disclosed), M5 at 196x184 is 21.6%.

This spike proves the defect and validates the fix on hardware **without
changing product code**: it computes the candidate bound inline. The demo band
is only ~0.8% wide (8,127..8,192), which makes it a tight but honest test -- if
the defect shows up there it certainly shows up on M2.

    uv run python design\\60-band-fix-spike.py > band-spike-console.txt 2>&1

Writes ~4.3 GB. Delete the datasets afterwards; paths are in results.json.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(os.environ.get("MICROCLAW_TREE_UNDER_TEST") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))

from ndstorage.ndtiff_file import ENTRIES_PER_IFD
from ndstorage.ndtiff_index import read_ndtiff_index

from microclaw import tools
from microclaw.config import load_safety_config
from microclaw.controller import MicroscopeController
from microclaw.paths import default_safety_config
from microclaw.safety import SafetyGuard
from microclaw.tools import run_timelapse

RESULTS = []


class NotExercised(Exception):
    """This machine could not exercise the limb; never a pass."""


class Tee:
    def __init__(self, stream, path):
        self.stream, self.file = stream, path.open("w", encoding="utf-8")

    def write(self, data):
        self.stream.write(data.encode("ascii", "backslashreplace").decode("ascii"))
        self.file.write(data)
        return len(data)

    def flush(self):
        self.stream.flush()
        self.file.flush()


def limb(name, fails_if):
    def decorate(fn):
        try:
            detail, status = fn() or "", "PASS"
        except NotExercised as exc:
            detail, status = str(exc), "NOT EXERCISED"
        except Exception as exc:                    # noqa: BLE001 - reported
            detail, status = f"{type(exc).__name__}: {exc}", "FAIL"
            traceback.print_exc()
        RESULTS.append({"name": name, "status": status, "detail": detail,
                        "fails_if": fails_if})
        print(f"{status}: {name} - {detail}")
        return fn
    return decorate


#: The writer's own admission test, ndtiff_file.py:80-90. Not fitted to data:
#: `md_length + IFD_size + bytes_per_pixels + extra_padding + file.tell()`.
IFD_SIZE = ENTRIES_PER_IFD * 12 + 4 + 16
EXTRA_PADDING = 5_000_000


def bounds(raw_bytes_per_frame: int, m: float) -> dict[str, int]:
    """Three candidate bounds on frames per NDTiff file.

    `raw` is what ships today. `md` adds the measured per-frame metadata.
    `admission` is the writer's own test solved for n, and is the only one that
    accounts for the 5 MB reserve the writer keeps -- which is worth ~9 frames
    on the demo camera and is why `md` alone is still a little optimistic.
    """
    MAX = tools.NDTIFF_MAX_FILE_SIZE
    return {
        "raw": MAX // raw_bytes_per_frame,
        "md": MAX // int(raw_bytes_per_frame + m),
        "admission": (MAX - EXTRA_PADDING) // int(raw_bytes_per_frame + m + IFD_SIZE),
    }


def measure_m(dataset_path: Path) -> tuple[float, int, float]:
    """Per-frame metadata from a dataset's own index: (mean, n, spread)."""
    index = read_ndtiff_index((dataset_path / "NDTiff.index").read_bytes(), verbose=False)
    if not index:
        raise NotExercised(f"no index entries under {dataset_path}")
    mds = [e.metadata_length for e in index.values()]
    mean = statistics.mean(mds)
    return mean, len(mds), (max(mds) - min(mds)) / mean


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--save-root", type=Path, default=None)
    parser.add_argument("--exposure-ms", type=float, default=10.0)
    parser.add_argument("--calibration-frames", type=int, default=32)
    parser.add_argument("--safety-config", type=Path, default=default_safety_config())
    parser.add_argument("--output", type=Path, default=Path("band-spike-evidence"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, args.output / "spike.txt")

    # The arithmetic is checked against the survey's measured rigs before any
    # hardware is touched, so a broken formula cannot reach an acquisition.
    for label, (w, h, bpp, m, want) in {
        "M2 150x150": (150, 150, 2, 14357, {"raw": 95443, "md": 72358}),
        "demo 512x512": (512, 512, 2, 4234, {"raw": 8192, "md": 8126}),
    }.items():
        got = bounds(w * h * bpp, m)
        for key, value in want.items():
            assert got[key] == value, f"{label} {key}: {got[key]} != {value}"
    # The demo machine rolled at frame 8,114 on two independent 60b gate runs.
    # The admission bound must land on that, within a frame; if it does not,
    # this spike is measuring the wrong thing and says so before acquiring.
    demo = bounds(512 * 512 * 2, 4234)["admission"]
    assert abs(demo - 8114) <= 2, f"admission bound {demo} misses the observed 8,114"
    print(f"bounds() reproduces the survey's rigs; admission bound {demo} "
          f"matches the observed roll at 8,114\n")

    setup_error = None
    state = {}
    try:
        ctrl = MicroscopeController(port=args.port)
        config = load_safety_config(args.safety_config.resolve())
        guard = SafetyGuard(config.constraints)
        if args.save_root is None:
            workspace = getattr(config.constraints, "workspace_dir", None)
            args.save_root = Path(workspace or Path.cwd()) / "band-spike"
        args.save_root.mkdir(parents=True, exist_ok=True)
        core = ctrl.core
        w, h = int(core.get_image_width()), int(core.get_image_height())
        bpp = int(core.get_bytes_per_pixel())
        raw = w * h * bpp

        confirmations = []

        def gate_confirm(summary, kind="action", subject=None, **kwargs):
            confirmations.append({"kind": kind, "subject": subject, "summary": summary})
            print(f"[spike] auto-approved {kind}/{subject}: {summary[:120]}...")
            return True

        tools.CONFIRM_FN = gate_confirm

        # 1. Calibrate m from a dataset this machine writes right now. This is
        #    what the product fix would do: measure, never model.
        cal_name = f"band_calibration_{int(time.time())}"
        cal = run_timelapse(ctrl, guard, n_frames=args.calibration_frames,
                            interval_s=0, exposure_ms=args.exposure_ms,
                            save_dir=str(args.save_root), name=cal_name)
        m, n_cal, spread = measure_m(Path(cal["dataset_path"]))
        b = bounds(raw, m)
        n_raw, n_true = b["raw"], b["admission"]
        state.update(w=w, h=h, bpp=bpp, raw=raw, m=m, n_cal=n_cal, spread=spread,
                     n_raw=n_raw, n_true=n_true, bounds=b,
                     band_pct=100 * (n_raw / n_true - 1),
                     calibration_dataset=cal["dataset_path"])
        print("\n" + json.dumps(state, indent=1, default=str) + "\n")

        if n_true >= n_raw:
            raise NotExercised(
                f"this geometry has no band (N_true {n_true} >= N_raw {n_raw})")

        # 2. Pick a frame count INSIDE the band and check disk before running.
        n_test = (n_true + n_raw) // 2 + 1
        state["n_test"] = n_test
        need = int(n_test * (raw + m) * 1.1)
        free = shutil.disk_usage(str(args.save_root)).free
        state["required_bytes"], state["free_bytes"] = need, free
        if free < need:
            raise NotExercised(f"needs {need/1e9:.1f} GB, {free/1e9:.1f} GB free")

        # 3. What does the SHIPPED disclosure say about n_test? Ask the real
        #    authorizer, not a reimplementation of it.
        from microclaw.acquisition import AcquisitionPlan
        confirmations.clear()
        plan = AcquisitionPlan(n_test, args.exposure_ms,
                               n_test * args.exposure_ms / 1000.0, n_test * raw,
                               hardware_sequenced_burst=True)
        tools._authorize_acquisition(ctrl, guard, plan).close()
        state["shipped_summaries"] = [c["summary"] for c in confirmations]

        # 4. Run it and see whether the file actually rolls.
        run_name = f"band_probe_{int(time.time())}"
        confirmations.clear()
        started = time.monotonic()
        result = run_timelapse(ctrl, guard, n_frames=n_test, interval_s=0,
                               exposure_ms=args.exposure_ms,
                               save_dir=str(args.save_root), name=run_name)
        state["wall_s"] = time.monotonic() - started
        state["result"] = result
        path = Path(result["dataset_path"])
        files = sorted(path.glob("*NDTiffStack*.tif"))
        state["stacks"] = [p.name for p in files]
        state["sizes"] = {p.name: p.stat().st_size for p in files}
        index = read_ndtiff_index((path / "NDTiff.index").read_bytes(), verbose=False)
        state["frames_indexed"] = len(index)
        # Where it ACTUALLY rolled: the number of entries naming the first file.
        from collections import Counter
        counts = Counter(e.filename for e in index.values())
        if len(counts) > 1:
            state["roll_at"] = counts[sorted(counts)[0]]
            state["frames_per_file"] = {k: counts[k] for k in sorted(counts)}
        (args.output / "run.json").write_text(
            json.dumps(state, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8")
    except NotExercised as exc:
        setup_error = str(exc)
    except Exception as exc:                        # noqa: BLE001 - reported
        setup_error = f"{type(exc).__name__}: {exc}"
        (args.output / "setup-error.txt").write_text(traceback.format_exc(),
                                                     encoding="utf-8")

    def require(*keys):
        if setup_error:
            raise NotExercised(setup_error)
        missing = [k for k in keys if k not in state]
        if missing:
            raise NotExercised(f"never reached: {missing}")

    @limb("m is stable enough within one dataset to calibrate from",
          "per-frame metadata varies too much for a measured bound to mean anything")
    def m_is_stable():
        require("m", "spread")
        assert state["spread"] < 0.05, f"spread {100*state['spread']:.2f}% over {state['n_cal']} frames"
        return (f"m = {state['m']:,.0f} B/frame over {state['n_cal']} frames, "
                f"spread {100*state['spread']:.2f}% (survey: 732 datasets, worst 0.33%)")

    @limb("this geometry has a band, computed from measured m",
          "there is nothing to test on this camera")
    def band_exists():
        require("n_raw", "n_true", "n_test")
        assert state["n_true"] < state["n_test"] <= state["n_raw"], state
        return (f"{state['w']}x{state['h']}x{state['bpp']}B: N_true {state['n_true']:,} "
                f"< N_raw {state['n_raw']:,}, band {state['band_pct']:.1f}% wide; "
                f"testing n = {state['n_test']:,}")

    @limb("the SHIPPED disclosure stays silent for a run inside the band",
          "there is no defect here after all -- which would be good news")
    def shipped_is_silent():
        require("shipped_summaries")
        joined = " ".join(state["shipped_summaries"])
        assert "4 GiB per-file limit" not in joined, (
            "the shipped disclosure already fires inside the band: " + joined[:400])
        return (f"n = {state['n_test']:,} > N_true {state['n_true']:,} and no crossing "
                f"disclosure was produced ({len(state['shipped_summaries'])} clause(s), "
                f"none naming the 4 GiB limit)")

    @limb("and the file really does roll, so the silence is wrong",
          "the band is arithmetic only and no boundary is actually crossed")
    def really_rolls():
        require("stacks", "frames_indexed")
        assert len(state["stacks"]) >= 2, (
            f"n = {state['n_test']:,} produced {state['stacks']} -- no rollover, so "
            f"N_true is too pessimistic on this rig")
        assert state["frames_indexed"] == state["n_test"], (
            state["frames_indexed"], state["n_test"])
        largest = max(state["sizes"].values())
        return (f"{len(state['stacks'])} stacks, largest {largest/1e9:.3f} GB, "
                f"{state['frames_indexed']:,}/{state['n_test']:,} frames indexed, "
                f"{state['wall_s']:.0f} s")

    @limb("the candidate fixed bound would have disclosed it",
          "measuring m does not close the band")
    def fix_would_fire():
        require("n_test", "n_true")
        assert state["n_test"] > state["n_true"], state
        # The fix is exactly this comparison in place of the raw one.
        observed = state.get("roll_at")
        detail = (f"n {state['n_test']:,} > measured bound {state['n_true']:,} -> "
                  f"discloses, where the raw bound {state['n_raw']:,} does not; "
                  f"candidates {state['bounds']}")
        if observed:
            # The decisive comparison: which formula predicts where it rolled?
            errors = {k: v - observed for k, v in state["bounds"].items()}
            detail += f"; observed roll at {observed:,}, errors {errors}"
            assert abs(errors["admission"]) <= max(2, 0.001 * observed), errors
        return detail

    summary = {"results": RESULTS, "state": state, "setup_error": setup_error}
    (args.output / "results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")
    failed = [r for r in RESULTS if r["status"] != "PASS"]
    print("BAND FIX SPIKE " + ("FAILED" if failed else "PASSED"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
