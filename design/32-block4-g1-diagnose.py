"""Block 4 G1 diagnosis: attribute the branch's per-frame overhead.

Runs the same dark timelapse in one of three modes and reports where the time
goes, so a regression is measured rather than guessed at.

  asis    branch as shipped, timing every is_finished() bridge call
  wide    look-ahead raised so the feeder never blocks; isolates the per-event
          bridge poll from the gating cost
  nopoll  look-ahead raised AND is_finished() stubbed cheap; if this matches
          main, the poll is the cost

Every argument is named and validated: an earlier positional version silently
shifted a missing mode into the frame count and produced three identical runs.
"""
import argparse
import sys
import time

from microclaw.config import load_safety_config_or_exit
from microclaw.safety import SafetyGuard
from microclaw.controller import MicroscopeController
from microclaw import tools

MODES = ("asis", "wide", "nopoll")

parser = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--config", required=True, help="Path to the reviewed safety config.")
parser.add_argument("--save-dir", required=True, help="Dataset directory (inside workspace_dir).")
parser.add_argument("--mode", required=True, choices=MODES, help="Which variant to run.")
parser.add_argument("--frames", type=int, default=200, help="Frame count (default 200).")
parser.add_argument("--exposure-ms", type=float, default=5.0, help="Exposure ms (default 5).")
args = parser.parse_args()

if args.frames < 50:
    print(f"WARNING: {args.frames} frames is too few to measure; startup dominates. "
          f"Use at least 50, ideally 200.", file=sys.stderr)

parsed = load_safety_config_or_exit(args.config)
guard = SafetyGuard(parsed.constraints)
ctrl = MicroscopeController(guard=guard)
tools.CONFIRM_FN = lambda summary, kind="action": True   # unattended; dark run only

baseline_lookahead = tools._ACQUISITION_EVENT_LOOKAHEAD
if args.mode in ("wide", "nopoll"):
    tools._ACQUISITION_EVENT_LOOKAHEAD = 10 ** 9         # feeder never blocks

stats = {"calls": 0, "total_s": 0.0, "max_s": 0.0, "patched": False}
real_acquisition = tools.Acquisition


def capturing(*a, **kw):
    acq = real_acquisition(*a, **kw)
    inner = getattr(acq, "_acq", None)
    if inner is None:
        print("WARNING: acq._acq missing; is_finished NOT instrumented", file=sys.stderr)
        return acq
    real_is_finished = inner.is_finished

    def timed_is_finished():
        if args.mode == "nopoll":
            return False                                  # skip the bridge entirely
        t = time.monotonic()
        try:
            return real_is_finished()
        finally:
            dt = time.monotonic() - t
            stats["calls"] += 1
            stats["total_s"] += dt
            stats["max_s"] = max(stats["max_s"], dt)

    try:
        inner.is_finished = timed_is_finished
        stats["patched"] = True
    except Exception as e:
        print(f"WARNING: could not patch is_finished ({e})", file=sys.stderr)
    return acq


tools.Acquisition = capturing

t0 = time.monotonic()
result = tools.run_timelapse(
    ctrl, guard, n_frames=args.frames, interval_s=0,
    save_dir=args.save_dir, name=f"g1_diag_{args.mode}",
    exposure_ms=args.exposure_ms,
)
elapsed = time.monotonic() - t0

print(f"mode={args.mode} frames={args.frames} exposure_ms={args.exposure_ms}")
print(f"lookahead_in_effect={tools._ACQUISITION_EVENT_LOOKAHEAD} "
      f"(branch default {baseline_lookahead})")
print(f"elapsed_s={elapsed:.3f} frames_per_s={args.frames/elapsed:.2f} "
      f"per_frame_ms={elapsed*1000/args.frames:.2f}")
if args.mode == "nopoll":
    print("is_finished: stubbed (no bridge calls made)")
elif not stats["patched"]:
    print("is_finished: NOT INSTRUMENTED -- this run cannot attribute poll cost")
else:
    mean_ms = stats["total_s"] * 1000 / stats["calls"] if stats["calls"] else 0.0
    print(f"is_finished calls={stats['calls']} "
          f"calls_per_frame={stats['calls']/args.frames:.2f} "
          f"total_s={stats['total_s']:.3f} mean_ms={mean_ms:.2f} "
          f"max_ms={stats['max_s']*1000:.2f}")
    print(f"is_finished share of elapsed = {stats['total_s']/elapsed*100:.1f}%")
print(result)
