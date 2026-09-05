"""Block 60a demo-machine gate: the bounded teardown wait, measured on hardware.

Every limb here is a computation or an acquisition this program drives, so it
ships as a program rather than pasted PowerShell blocks: each limb reports
independently, a refusal in one cannot hide the rest, and the exit status is
nonzero if any limb did not pass (58a).

What this gate can and cannot establish. The failure design/60 is about --
pycro-manager's notification thread dying inside a hardware-sequenced burst --
is upstream, is not reproducible on demand, and is already reproduced exactly by
the blocking fake in tests/test_bounded_acquisition_wait.py. Do not add a limb
that pretends to induce it. What needs real hardware is narrower and is all
here: that D3's camera probe can answer while a burst is in flight, that the
threaded teardown does not regress an ordinary run, and what pycro-manager's
teardown actually costs on a real dataset -- the number D1's constants were
chosen without.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(os.environ.get("MICROCLAW_TREE_UNDER_TEST") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))

from ndstorage import Dataset

from microclaw import tools
from microclaw.config import load_safety_config
from microclaw.controller import MicroscopeController
from microclaw.paths import default_safety_config
from microclaw.safety import SafetyGuard
from microclaw.tools import _iter_present_coords, run_timelapse

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
        except Exception as exc:
            detail, status = f"{type(exc).__name__}: {exc}", "FAIL"
            traceback.print_exc()
        RESULTS.append({"name": name, "status": status, "detail": detail,
                        "fails_if": fails_if})
        print(f"{status}: {name} - {detail}")
        return fn
    return decorate


class CameraProbe(threading.Thread):
    """Poll the product's own camera-state read while an acquisition runs.

    Deliberately `tools._camera_sequence_running`, not a hand-rolled twin: the
    claim under test is about the function D3 calls at expiry. A hand-copied
    copy that drifts would measure something else.

    Daemon, because the thing being measured is whether this call can be
    answered at all while the engine holds the burst. If pyjavaz's per-round-trip
    lock blocks it until the burst ends, the gate must report that as a finding
    rather than hang: that is exactly the outcome D3 cannot survive.
    """

    def __init__(self, ctrl, period_s=0.2):
        super().__init__(name="gate-camera-probe", daemon=True)
        self.ctrl = ctrl
        self.period_s = period_s
        self.stop = threading.Event()
        self.samples = []

    def run(self):
        while not self.stop.is_set():
            t0 = time.monotonic()
            try:
                value = tools._camera_sequence_running(self.ctrl)
                error = None
            except BaseException as exc:            # noqa: BLE001 - reported
                value, error = None, f"{type(exc).__name__}: {exc}"
            t1 = time.monotonic()
            self.samples.append({"t0": t0, "t1": t1, "latency_s": t1 - t0,
                                 "value": value, "error": error})
            self.stop.wait(self.period_s)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--save-root", type=Path, required=True,
                        help="A directory inside the machine's configured workspace.")
    parser.add_argument("--frames", type=int, default=600)
    parser.add_argument("--exposure-ms", type=float, default=20.0)
    parser.add_argument("--safety-config", type=Path, default=default_safety_config())
    parser.add_argument("--output", type=Path, default=Path("block60a-demo-evidence"))
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

    setup_error = None
    run = {}
    probe = None
    try:
        # Inside the try: a Micro-Manager whose ZMQ server was never enabled is
        # the likeliest first-run failure, and it must arrive as a named reason
        # on every limb rather than a traceback with no results.json behind it.
        ctrl = MicroscopeController(port=args.port)
        config = load_safety_config(safety_path)
        guard = SafetyGuard(config.constraints)
        core = ctrl.core
        camera = str(core.get_camera_device() or "")
        geometry = {
            "camera": camera,
            "width": int(core.get_image_width()),
            "height": int(core.get_image_height()),
            "bytes_per_pixel": int(core.get_bytes_per_pixel()),
        }
        geometry["bytes_per_frame"] = (
            geometry["width"] * geometry["height"] * geometry["bytes_per_pixel"]
        )
        print("Geometry: " + json.dumps(geometry, sort_keys=True))

        # A program-shaped gate has no console to answer a confirmation on:
        # `_require_confirmation` calls input(), so a plan over this machine's
        # thresholds would hang here with no output. Auto-approve, but record
        # every question into the evidence -- an auto-approval nobody can read
        # afterwards is design/60 F5's session grant all over again.
        confirmations = []

        def gate_confirm(summary, kind="action", subject=None):
            confirmations.append({"kind": kind, "subject": subject,
                                  "summary": summary})
            print(f"[gate] auto-approved {kind}/{subject}: {summary}")
            return True

        tools.CONFIRM_FN = gate_confirm

        name = f"block60a_burst_{int(time.time())}"
        started = time.monotonic()
        probe = CameraProbe(ctrl)
        probe.start()
        result = run_timelapse(
            ctrl, guard, n_frames=args.frames, interval_s=0,
            exposure_ms=args.exposure_ms, save_dir=str(args.save_root), name=name,
        )
        returned = time.monotonic()
        probe.stop.set()
        probe.join(timeout=10.0)
        run = {
            "result": result,
            "started": started,
            "returned": returned,
            "wall_s": returned - started,
            "probe_alive_after_run": probe.is_alive(),
            "samples": probe.samples,
            "confirmations": confirmations,
        }
        (args.output / "run.json").write_text(
            json.dumps(run, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:                        # noqa: BLE001 - reported
        setup_error = f"{type(exc).__name__}: {exc}"
        # 58e: when a gate fails for a reason its own artifacts cannot explain,
        # the next trip is spent finding out why. Keep the whole traceback.
        (args.output / "setup-error.txt").write_text(
            traceback.format_exc(), encoding="utf-8"
        )
        geometry = {}

    def require_run():
        if setup_error:
            raise NotExercised(f"the acquisition never ran: {setup_error}")

    @limb("the tree under test carries block 60a",
          "the gate is scoring a build without the bounded wait")
    def build_identity():
        # Rewritten 2026-09-05: this limb had been dead since block 75a, which
        # made _runtime_ceiling_s return a third `term` element, and block 75b
        # then moved the quiet floor onto AcquisitionSupervisionPolicy. Neither
        # block noticed, because a committed gate script gets no review pass and
        # nothing in the suite imports one. Score it against the current names.
        missing = [n for n in ("AcquisitionUnterminated", "_runtime_ceiling_s",
                               "_stall_quiet_s", "DEFAULT", "SHORT_FIXED",
                               "ERROR_TEARDOWN_GRACE_S", "_completed_position_record")
                   if not hasattr(tools, n)]
        assert not missing, f"missing from {ROOT}: {missing}"
        ceiling, fallback, term = tools._runtime_ceiling_s(None, tools.DEFAULT)
        assert fallback and ceiling == tools.FALLBACK_RUNTIME_CEILING_S
        return (f"tools from {ROOT}; grace {tools.ERROR_TEARDOWN_GRACE_S:g}s, "
                f"quiet floor {tools.DEFAULT.quiet_floor_s:g}s, "
                f"fallback ceiling {ceiling:g}s ({term})")

    @limb("an ordinary burst completes through the threaded teardown",
          "the waiter never joins, or the tool reports unterminated on a healthy run")
    def ordinary_run():
        require_run()
        result = run["result"]
        assert result.get("acquisition") != "unterminated", result
        assert "error" not in result, result
        assert result.get("dataset_path"), result
        return (f"{args.frames} frames in {run['wall_s']:.1f}s -> "
                f"{result['dataset_path']}")

    @limb("every planned frame reached disk",
          "the run reports success while the dataset is short")
    def frames_on_disk():
        require_run()
        path = run["result"]["dataset_path"]
        coords = list(_iter_present_coords(Dataset(path), {}))
        run["frames_on_disk"] = len(coords)
        assert len(coords) == args.frames, f"{len(coords)} of {args.frames} on disk"
        return f"{len(coords)} frames present at {path}"

    @limb("the camera probe answers while the burst is in flight",
          "D3's camera_sequence_running cannot be read at expiry")
    def probe_answers_mid_burst():
        require_run()
        during = [s for s in run["samples"]
                  if s["t0"] > run["started"] and s["t1"] < run["returned"]]
        run["samples_during"] = len(during)
        assert not run["probe_alive_after_run"], (
            "the probe thread was still blocked after the run returned: pyjavaz "
            "serialised it behind the burst, so D3 cannot report camera state"
        )
        if not during:
            # Not a product statement: the run was too short for this poll
            # period to sample it. A limb that fails on the gate's own timing
            # teaches nothing, and NOT EXERCISED is never a pass -- raise
            # --frames or --exposure-ms until the burst lasts a few seconds.
            raise NotExercised(
                f"the run lasted {run['wall_s']:.2f}s, too short for a "
                f"{probe.period_s:g}s poll to land inside it; increase --frames"
            )
        worst = max(s["latency_s"] for s in during)
        errors = [s["error"] for s in during if s["error"]]
        assert not errors, f"probe errors: {errors[:3]}"
        running = [s for s in during if s["value"] is True]
        run["samples_running_true"] = len(running)
        # True is the interesting case and the one D3's report depends on, but
        # a demo camera that finishes each exposure between samples can answer
        # False honestly. Say which happened; do not fail on it.
        return (f"{len(during)} samples inside the run, {len(running)} read True, "
                f"worst latency {worst * 1000:.0f} ms")

    @limb("teardown is far below the bounds D1 chose",
          "the error grace or the ceiling is near this machine's real teardown cost")
    def teardown_cost():
        require_run()
        exposures_s = args.frames * args.exposure_ms / 1000.0
        overhead_s = run["wall_s"] - exposures_s
        run["overhead_s"] = overhead_s
        # An upper bound on teardown: it also carries readout and submission.
        # Reported as a bound, not as a measurement of __exit__ alone.
        assert overhead_s < tools.ERROR_TEARDOWN_GRACE_S, (
            f"non-exposure time {overhead_s:.1f}s is not below the "
            f"{tools.ERROR_TEARDOWN_GRACE_S:g}s error grace"
        )
        return (f"{run['wall_s']:.1f}s wall - {exposures_s:.1f}s exposures = "
                f"{overhead_s:.1f}s upper bound on teardown, against a "
                f"{tools.ERROR_TEARDOWN_GRACE_S:g}s grace")

    @limb("the runtime ceiling was never close to firing",
          "a healthy run approaches the ceiling and would expire on a slower machine")
    def ceiling_headroom():
        require_run()
        events = tools._build_acquisition_events(
            channel=None, exposure_ms=args.exposure_ms,
            num_time_points=args.frames, time_interval_s=0,
        )
        plan = tools.plan_events(ctrl, events, args.exposure_ms)
        ceiling, fallback, _term = tools._runtime_ceiling_s(plan, tools.DEFAULT)
        assert not fallback
        run["ceiling_s"] = ceiling
        assert run["wall_s"] < ceiling, f"{run['wall_s']:.1f}s reached the {ceiling:.1f}s ceiling"
        return (f"wall {run['wall_s']:.1f}s against a {ceiling:.1f}s ceiling "
                f"({run['wall_s'] / ceiling:.1%} of it); plan estimated "
                f"{plan.estimated_duration_s:.1f}s")

    @limb("no unterminated acquisition was left behind",
          "a healthy run sets the session refusal flag")
    def no_refusal_flag():
        require_run()
        pending = getattr(ctrl, "_microclaw_unterminated_acquisition", None)
        assert pending is None, f"refusal flag left set: {pending}"
        return "no session refusal flag after a healthy run"

    @limb("the production safety document is untouched",
          "the gate rewrites the machine's safety config")
    def safety_untouched():
        if safety_error:
            raise NotExercised(f"no readable safety document at {safety_path}: {safety_error}")
        assert safety_path.read_bytes() == safety_bytes
        assert safety_path.stat().st_mtime_ns == safety_mtime_ns
        return f"bytes and mtime unchanged for {safety_path}"

    summary = {"results": RESULTS, "geometry": geometry, "setup_error": setup_error,
               "confirmations": run.get("confirmations", []),
               "measured": {k: run.get(k) for k in
                            ("wall_s", "overhead_s", "ceiling_s", "frames_on_disk",
                             "samples_during", "samples_running_true",
                             "probe_alive_after_run")}}
    (args.output / "results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    failed = [item for item in RESULTS if item["status"] != "PASS"]
    print("BLOCK 60a DEMO GATE " + ("FAILED" if failed else "PASSED"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
