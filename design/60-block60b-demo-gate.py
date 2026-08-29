"""Block 60b demo-machine gate: the disclosures, and a real 4 GiB crossing.

Every limb is a computation or an acquisition this program drives, so it ships
as a program rather than pasted PowerShell blocks: each limb reports
independently, a refusal in one cannot hide the rest, and the exit status is
nonzero if any limb did not pass (58a). Same rules as 60a's gate.

What needs real hardware here is narrow and specific. The disclosures are unit
tested; what is not is whether *this machine's* geometry produces a sane bound,
and -- the one thing no fake can answer -- whether NDTiff's rollover past 4 GiB
is clean on a camera that is not M2's. Either outcome is evidence: a clean
rollover shows the demo camera's storage does what M2's did not, and a
reproduction of the truncated notification is a much larger finding and goes
upstream.

The magnitude-bounded grant and the burst read-back are the operator's half and
live in the runbook's driven session; the grant limb here is the programmatic
control, so a broken lookup fails without a human.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(os.environ.get("MICROCLAW_TREE_UNDER_TEST") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))

from microclaw import tools
from microclaw.acquisition import AcquisitionPlan
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
        self.stream = stream
        self.file = path.open("w", encoding="utf-8")

    def write(self, data):
        # The gate owns its log: PowerShell 5.1's Start-Transcript does not
        # capture a native child process's stdout and came back empty twice
        # (58a). Keep the file UTF-8 and the console ASCII-safe.
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--save-root", type=Path, default=None,
                        help="Defaults to <workspace_dir>/block60b-gate, read from "
                             "the safety config. 52c: a placeholder left in a "
                             "literal command is a step that does not run, so the "
                             "runbook must not have to substitute a path.")
    parser.add_argument("--exposure-ms", type=float, default=10.0)
    parser.add_argument("--extra-frames", type=int, default=64,
                        help="Frames past the computed bound. Metadata makes the "
                             "real roll earlier, so any positive value crosses.")
    parser.add_argument("--safety-config", type=Path, default=default_safety_config())
    parser.add_argument("--output", type=Path, default=Path("block60b-demo-evidence"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, args.output / "gate.txt")

    safety_path = args.safety_config.resolve()
    try:
        safety_bytes = safety_path.read_bytes()
        safety_mtime_ns = safety_path.stat().st_mtime_ns
        safety_error = None
    except OSError as exc:
        safety_bytes = safety_mtime_ns = None
        safety_error = f"{type(exc).__name__}: {exc}"
    print(f"Safety document (read, never replaced): {safety_path} error={safety_error}")

    # ---- Phase 1: geometry, the bound, and disk. Before any exposure. --------
    setup_error = None
    disk_error = None
    geometry = {}
    plan_probe = {}
    run = {}
    progress = []
    try:
        ctrl = MicroscopeController(port=args.port)
        config = load_safety_config(safety_path)
        guard = SafetyGuard(config.constraints)
        if args.save_root is None:
            # workspace_dir is OPTIONAL in the product -- None means writes are
            # unconfined (safety.py resolve_output_path) -- so a gate that
            # demands one invents a precondition microclaw does not have. The
            # first demo run of this gate did exactly that and reported six
            # limbs NOT EXERCISED for a reason that says nothing about the code.
            workspace = getattr(config.constraints, "workspace_dir", None)
            args.save_root = Path(workspace or Path.cwd()) / "block60b-gate"
            source = "the safety config's workspace_dir" if workspace else \
                "the current directory (no workspace_dir configured, which is fine)"
            print(f"Save root defaulted from {source}: {args.save_root}")
        core = ctrl.core
        width = int(core.get_image_width())
        height = int(core.get_image_height())
        bpp = int(core.get_bytes_per_pixel())
        bytes_per_frame = width * height * bpp
        if bytes_per_frame <= 0:
            raise NotExercised(f"camera reports a zero-byte frame: {width}x{height}x{bpp}")
        bound = tools.NDTIFF_MAX_FILE_SIZE // bytes_per_frame
        frames = bound + max(1, args.extra_frames)
        geometry = {
            "camera": str(core.get_camera_device() or ""),
            "width": width, "height": height, "bytes_per_pixel": bpp,
            "bytes_per_frame": bytes_per_frame,
            "ndtiff_max_file_size": tools.NDTIFF_MAX_FILE_SIZE,
            "frame_bound_per_file": bound,
            "burst_frames": frames,
            "raw_bytes": frames * bytes_per_frame,
            "exposure_ms": args.exposure_ms,
            "estimated_burst_s": frames * args.exposure_ms / 1000.0,
        }
        print("Geometry: " + json.dumps(geometry, sort_keys=True))

        # Free disk is checked before anything runs. A machine that cannot hold
        # the dataset must reach the burst limbs as NOT EXERCISED, never FAIL:
        # that is a fact about this disk, not about the product.
        args.save_root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(str(args.save_root)).free
        needed = int(geometry["raw_bytes"] * 1.35)   # metadata measured at +24%
        geometry["free_bytes"] = free
        geometry["required_bytes"] = needed
        print(f"Disk at {args.save_root}: free {free/1e9:.2f} GB, "
              f"required about {needed/1e9:.2f} GB")
        if free < needed:
            disk_error = (f"only {free/1e9:.2f} GB free at {args.save_root}, "
                          f"about {needed/1e9:.2f} GB needed")

        # A program-shaped gate has no console to answer a confirmation on, and
        # _require_confirmation calls input(). Auto-approve, but record every
        # question -- an auto-approval nobody can read afterwards is F5 again.
        confirmations = []

        def gate_confirm(summary, kind="action", subject=None, **kwargs):
            confirmations.append({"kind": kind, "subject": subject,
                                  "summary": summary,
                                  "grant_metadata": kwargs.get("grant_metadata")})
            print(f"[gate] auto-approved {kind}/{subject}: {summary}")
            return True

        tools.CONFIRM_FN = gate_confirm

        # Limb 2's evidence: the disclosure decided from this machine's own
        # numbers, before the first exposure.
        def ask(plan):
            confirmations.clear()
            tools._authorize_acquisition(ctrl, guard, plan).close()
            return list(confirmations)

        over = AcquisitionPlan(bound + 1, args.exposure_ms,
                               (bound + 1) * args.exposure_ms / 1000.0,
                               (bound + 1) * bytes_per_frame)
        under = AcquisitionPlan(bound, args.exposure_ms,
                                bound * args.exposure_ms / 1000.0,
                                bound * bytes_per_frame)
        burst = AcquisitionPlan(2, args.exposure_ms, 2 * args.exposure_ms / 1000.0,
                                2 * bytes_per_frame, hardware_sequenced_burst=True)
        single = AcquisitionPlan(1, args.exposure_ms, args.exposure_ms / 1000.0,
                                 bytes_per_frame, hardware_sequenced_burst=True)
        plan_probe = {"over": ask(over), "under": ask(under),
                      "burst": ask(burst), "single": ask(single)}
        confirmations.clear()
    except NotExercised as exc:
        setup_error = str(exc)
    except Exception as exc:                        # noqa: BLE001 - reported
        setup_error = f"{type(exc).__name__}: {exc}"
        # 58e: when a gate fails for a reason its own artifacts cannot explain,
        # the next trip is spent finding out why. Keep the whole traceback.
        (args.output / "setup-error.txt").write_text(
            traceback.format_exc(), encoding="utf-8")

    # ---- Phase 2: actually cross 4 GiB. -------------------------------------
    if setup_error is None and disk_error is None:
        try:
            name = f"block60b_crossing_{int(time.time())}"
            sink_previous = getattr(tools._ACQUISITION_EVENT_CONTEXT, "sink", None)

            def collect(event):
                event = dict(event)
                event["at"] = time.monotonic()
                progress.append(event)

            tools._ACQUISITION_EVENT_CONTEXT.sink = collect
            started = time.monotonic()
            try:
                result = run_timelapse(
                    ctrl, guard, n_frames=geometry["burst_frames"], interval_s=0,
                    exposure_ms=args.exposure_ms, save_dir=str(args.save_root),
                    name=name,
                )
            finally:
                tools._ACQUISITION_EVENT_CONTEXT.sink = sink_previous
            returned = time.monotonic()
            dataset_path = result.get("dataset_path") if isinstance(result, dict) else None
            # `*_NDTiffStack*.tif`, not `NDTiffStack*.tif`: ndstorage prefixes
            # every stack with the dataset name when one is set, and microclaw
            # always sets one (ndtiff_dataset.py:184-196). controller.py:513
            # already spells it correctly; the first version of this gate did
            # not, matched nothing, and failed the one limb the rig trip was for
            # on a crossing that had in fact been clean.
            files = sorted(Path(dataset_path).glob("*NDTiffStack*.tif")) \
                if dataset_path and Path(dataset_path).is_dir() else []
            stacks = [p.name for p in files]
            sizes = {p.name: p.stat().st_size for p in files}
            run = {"result": result, "wall_s": returned - started,
                   "dataset_path": dataset_path, "stacks": stacks, "sizes": sizes,
                   "confirmations": confirmations}
            (args.output / "run.json").write_text(
                json.dumps({**run, "progress": progress}, indent=2,
                           sort_keys=True, default=str) + "\n", encoding="utf-8")
        except Exception as exc:                    # noqa: BLE001 - reported
            run = {"error": f"{type(exc).__name__}: {exc}"}
            (args.output / "burst-error.txt").write_text(
                traceback.format_exc(), encoding="utf-8")

    def require_setup():
        if setup_error:
            raise NotExercised(f"phase 1 never completed: {setup_error}")

    def require_burst():
        require_setup()
        if disk_error:
            raise NotExercised(disk_error)
        if "error" in run:
            raise AssertionError(run["error"])
        if not run:
            raise NotExercised("the burst never ran")

    # ---- Limbs --------------------------------------------------------------

    @limb("the tree under test carries block 60b",
          "the gate is scoring a build without the disclosures")
    def build_identity():
        missing = [n for n in ("NDTIFF_MAX_FILE_SIZE",) if not hasattr(tools, n)]
        assert not missing, f"missing from {ROOT}: {missing}"
        assert "hardware_sequenced_burst" in AcquisitionPlan.__dataclass_fields__, \
            f"AcquisitionPlan in {ROOT} has no burst marker"
        return (f"tools from {ROOT}; NDTiff limit "
                f"{tools.NDTIFF_MAX_FILE_SIZE} bytes")

    @limb("this machine's crossing bound is computed from its own geometry",
          "the bound is guessed rather than measured from the camera")
    def bound_from_geometry():
        require_setup()
        assert geometry["frame_bound_per_file"] > 0, geometry
        assert geometry["bytes_per_frame"] > 0, geometry
        return (f"{geometry['width']}x{geometry['height']}x"
                f"{geometry['bytes_per_pixel']}B = {geometry['bytes_per_frame']} B/frame; "
                f"at most {geometry['frame_bound_per_file']:,} frames per NDTiff file; "
                f"burst of {geometry['burst_frames']:,} frames writes "
                f"{geometry['raw_bytes']/1e9:.2f} GB raw")

    @limb("the D6 crossing disclosure fires just over the bound and not just under",
          "a guaranteed 4 GiB crossing is not disclosed, or a safe plan is")
    def d6_disclosure():
        require_setup()
        over = " ".join(c["summary"] for c in plan_probe["over"])
        under = " ".join(c["summary"] for c in plan_probe["under"])
        assert "4 GiB per-file limit" in over, f"no crossing disclosure over bound: {over!r}"
        assert f"{geometry['frame_bound_per_file']:,}" in over, over
        assert "segmenting" in over, over
        # The control: one frame fewer must not disclose. A limb that cannot
        # fail is not a criterion (58a).
        assert "4 GiB per-file limit" not in under, f"disclosed on a safe plan: {under!r}"
        return (f"{geometry['frame_bound_per_file']+1:,} frames disclosed, "
                f"{geometry['frame_bound_per_file']:,} did not")

    @limb("the D5 burst clause fires for a multi-frame burst and not a single frame",
          "un-interruptibility is not disclosed before a burst is authorized")
    def d5_burst_clause():
        require_setup()
        burst = " ".join(c["summary"] for c in plan_probe["burst"])
        single = " ".join(c["summary"] for c in plan_probe["single"])
        for phrase in ("hardware-sequenced burst", "Stop button", "engine abort",
                       "thousands of further exposures"):
            assert phrase in burst, f"missing {phrase!r} from {burst!r}"
        assert "hardware-sequenced burst" not in single, single
        return "2-frame burst disclosed; 1-frame plan did not"

    @limb("a session grant does not carry a larger plan",
          "the grant auto-approves a plan bigger than the one it was given for")
    def d5_grant_magnitude():
        require_setup()
        grants = tools.SessionGrants()
        small = {"frames": 500, "duration_s": 26.0, "illuminated_ms": 25000.0}
        record = grants.grant("acquisition", "threshold", "500-frame plan",
                              identity="gate", grant_metadata=small)
        assert grants.granted("acquisition", "threshold", small) is not None
        smaller = {k: v / 2 for k, v in small.items()}
        assert grants.granted("acquisition", "threshold", smaller) is not None
        outcomes = {}
        for field in ("frames", "duration_s", "illuminated_ms"):
            larger = dict(small)
            larger[field] += 1
            outcomes[field] = grants.granted("acquisition", "threshold", larger)
        assert all(v is None for v in outcomes.values()), outcomes
        return (f"grant {record['id']} admits a smaller plan and re-asks on each of "
                f"frames, duration_s, illuminated_ms independently")

    @limb("the burst actually crossed 4 GiB and NDTiff rolled to a second file",
          "the rollover truncates the dataset or the run does not complete")
    def crossing_is_clean():
        require_burst()
        result = run["result"]
        assert isinstance(result, dict), result
        assert result.get("acquisition") != "unterminated", result
        assert "error" not in result, result
        stacks = run["stacks"]
        assert len(stacks) >= 2, (
            f"only {stacks} under {run['dataset_path']}; the burst of "
            f"{geometry['burst_frames']:,} frames did not roll to a second file")
        total = sum(run["sizes"].values())
        raw = geometry["raw_bytes"]
        overhead = (total - raw) / geometry["burst_frames"]
        first = max(run["sizes"].values()) if run["sizes"] else 0
        assert first <= tools.NDTIFF_MAX_FILE_SIZE, (
            f"a stack of {first} bytes exceeds NDTiff's own {tools.NDTIFF_MAX_FILE_SIZE}")
        run["overhead_bytes_per_frame"] = overhead
        return (f"{len(stacks)} stacks {stacks}; largest {first/1e9:.3f} GB, under the "
                f"{tools.NDTIFF_MAX_FILE_SIZE/1e9:.3f} GB limit; per-frame overhead "
                f"{overhead:.0f} B ({100*overhead/(raw/geometry['burst_frames']):.1f}% "
                f"on top of pixels); run completed in {run['wall_s']:.1f} s")

    @limb("progress events arrived during the burst, rate-limited",
          "an 83-minute run is again indistinguishable from a hung one")
    def progress_observed():
        require_burst()
        events = [e for e in progress if e.get("type") == "acquisition_progress"]
        if not events:
            raise AssertionError("no acquisition_progress events reached the sink")
        counts = [e["frames_accounted"] for e in events]
        assert counts[0] == 1, counts[:3]
        assert counts[-1] == geometry["burst_frames"], (counts[-1],
                                                        geometry["burst_frames"])
        # More than first-and-last is the point: this is the half a frozen
        # clock cannot test, and it is what F4 was about.
        assert len(events) > 2, f"only {len(events)} events across {run['wall_s']:.1f} s"
        span = events[-1]["at"] - events[0]["at"]
        rate = len(events) / span if span > 0 else float("inf")
        assert rate <= 2.0, f"{rate:.2f} events/s is not rate-limited"
        assert events[-1].get("frames_planned") == geometry["burst_frames"], events[-1]
        run["progress_rate_per_s"] = rate
        run["progress_events"] = len(events)
        return (f"{len(events)} events over {span:.1f} s = {rate:.2f}/s; "
                f"first {counts[0]}, last {counts[-1]} of "
                f"{events[-1].get('frames_planned')}")

    @limb("the production safety document is untouched",
          "the gate rewrites the machine's safety config")
    def safety_untouched():
        if safety_error:
            raise NotExercised(f"no readable safety document at {safety_path}: {safety_error}")
        assert safety_path.read_bytes() == safety_bytes
        assert safety_path.stat().st_mtime_ns == safety_mtime_ns
        return f"bytes and mtime unchanged for {safety_path}"

    summary = {"results": RESULTS, "geometry": geometry,
               "setup_error": setup_error, "disk_error": disk_error,
               "confirmations": run.get("confirmations", []),
               "measured": {k: run.get(k) for k in
                            ("wall_s", "stacks", "sizes", "progress_events",
                             "progress_rate_per_s", "dataset_path",
                             "overhead_bytes_per_frame")}}
    (args.output / "results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")
    failed = [item for item in RESULTS if item["status"] != "PASS"]
    print("BLOCK 60b DEMO GATE " + ("FAILED" if failed else "PASSED"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
