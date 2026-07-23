"""Block 4 G1 diagnosis: attribute the branch's per-frame overhead.

Runs the same dark timelapse three ways and prints where the time goes.
  mode=asis   branch as shipped, with is_finished() timed
  mode=wide   lookahead raised so the feeder never blocks (isolates the
              per-event is_finished() cost from the gating cost)
  mode=nopoll lookahead raised AND is_finished() stubbed cheap (should
              approach main if the bridge poll is the cost)

Usage:
  python g1_diagnose.py <config> <save_dir> <mode> [n_frames] [exposure_ms]
"""
import sys, time
from microclaw.config import load_safety_config_or_exit
from microclaw.safety import SafetyGuard
from microclaw.controller import MicroscopeController
from microclaw import tools

CONFIG, SAVE_DIR, MODE = sys.argv[1], sys.argv[2], sys.argv[3]
N_FRAMES = int(sys.argv[4]) if len(sys.argv) > 4 else 200
EXPOSURE_MS = float(sys.argv[5]) if len(sys.argv) > 5 else 5.0

parsed = load_safety_config_or_exit(CONFIG)
guard = SafetyGuard(parsed.constraints)
ctrl = MicroscopeController(guard=guard)
tools.CONFIRM_FN = lambda summary, kind="action": True   # unattended; dark run

if MODE in ("wide", "nopoll"):
    tools._ACQUISITION_EVENT_LOOKAHEAD = 10 ** 9   # feeder never blocks

stats = {"calls": 0, "total_s": 0.0, "max_s": 0.0}
real_acquisition = tools.Acquisition


def capturing(*args, **kwargs):
    acq = real_acquisition(*args, **kwargs)
    inner = getattr(acq, "_acq", None)
    if inner is None:
        print("WARNING: acq._acq missing; is_finished not instrumented")
        return acq
    real_is_finished = inner.is_finished

    def timed_is_finished():
        if MODE == "nopoll":
            return False                      # skip the bridge entirely
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
    except Exception as e:
        print(f"WARNING: could not patch is_finished ({e})")
    return acq


tools.Acquisition = capturing

t0 = time.monotonic()
result = tools.run_timelapse(
    ctrl, guard, n_frames=N_FRAMES, interval_s=0,
    save_dir=SAVE_DIR, name=f"g1_diag_{MODE}", exposure_ms=EXPOSURE_MS,
)
elapsed = time.monotonic() - t0
print(f"mode={MODE} frames={N_FRAMES} exposure_ms={EXPOSURE_MS}")
print(f"elapsed_s={elapsed:.3f} frames_per_s={N_FRAMES/elapsed:.2f} "
      f"per_frame_ms={elapsed*1000/N_FRAMES:.2f}")
print(f"is_finished calls={stats['calls']} total_s={stats['total_s']:.3f} "
      f"mean_ms={(stats['total_s']*1000/stats['calls']) if stats['calls'] else 0:.2f} "
      f"max_ms={stats['max_s']*1000:.2f}")
print(f"is_finished share of elapsed = "
      f"{(stats['total_s']/elapsed*100) if elapsed else 0:.1f}%")
print(result)
