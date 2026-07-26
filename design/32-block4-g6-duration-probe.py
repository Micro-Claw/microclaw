"""Block 4 G6: measured estimated-vs-actual duration for a multiposition run.

The planner estimates duration from exposure + scheduled start times only
(design/32 §2): readout, stage settling, autofocus, and filter switching are
excluded, so max_duration_s bounds a deliberately KNOWN-LOW figure. This probe
drives a real dark multiposition acquisition, times it, and reports the
per-frame overhead the estimate omits -- the number the post-merge design gate
records and Block 5 needs to size dose policy.

MOVES THE STAGE over a small grid around the current position. Dark (lasers
off, no shutter on M5), one frame per position. Pick a save-dir inside the
configured workspace and a region where a +/- (grid) x step_um move is safe.

  uv run python design/32-block4-g6-duration-probe.py --config <cfg> --save-dir <ws>\\g6 \\
      --positions 9 --step-um 50 --exposure-ms 100
"""
import argparse
import math
import sys
import time

from microclaw.config import load_safety_config_or_exit
from microclaw.safety import SafetyGuard
from microclaw.controller import MicroscopeController
from microclaw import tools

sys.stdout.reconfigure(line_buffering=True)

p = argparse.ArgumentParser()
p.add_argument("--config", required=True)
p.add_argument("--save-dir", required=True)
p.add_argument("--positions", type=int, default=9)
p.add_argument("--step-um", type=float, default=50.0)
p.add_argument("--exposure-ms", type=float, default=100.0)
p.add_argument("--return-to-start", action="store_true",
               help="Drive back to the starting XY when done.")
args = p.parse_args()

parsed = load_safety_config_or_exit(args.config)
guard = SafetyGuard(parsed.constraints)
ctrl = MicroscopeController(guard=guard)
tools.CONFIRM_FN = lambda summary, kind="action": True   # unattended; dark run only

x0 = float(ctrl.core.get_x_position())
y0 = float(ctrl.core.get_y_position())
print(f"start XY = ({x0:.1f}, {y0:.1f}); building {args.positions}-point grid "
      f"at {args.step_um} um spacing")

# Near-square snake grid centred on the start, so consecutive positions are one
# step apart (representative stage moves, not a single huge slew).
cols = max(1, int(math.ceil(math.sqrt(args.positions))))
grid = []
r = c = 0
for i in range(args.positions):
    row = i // cols
    col = i % cols
    if row % 2:                      # snake: reverse odd rows
        col = cols - 1 - col
    dx = (col - (cols - 1) / 2) * args.step_um
    dy = (row - ((args.positions - 1) // cols) / 2) * args.step_um
    grid.append({"name": f"g6_{i:02d}", "x_um": x0 + dx, "y_um": y0 + dy})

ctrl.core.set_exposure(args.exposure_ms)
frames = len(grid)
estimate_s = frames * args.exposure_ms / 1000.0   # what the planner reserves

t0 = time.monotonic()
result = tools.run_multiposition_acquisition(
    ctrl, guard, protocol="timelapse", save_dir=args.save_dir,
    positions=grid, name="g6_duration",
    protocol_params={"n_frames": 1, "interval_s": 0, "exposure_ms": args.exposure_ms},
)
actual_s = time.monotonic() - t0

n_ok = sum(1 for x in result.get("results", []) if "error" not in x)
print(f"\npositions={frames} exposure_ms={args.exposure_ms} completed={n_ok}/{frames}")
print(f"estimate_s (planner, exposure-only) = {estimate_s:.3f}")
print(f"actual_s   (wall clock)             = {actual_s:.3f}")
print(f"overhead_s (actual - estimate)      = {actual_s - estimate_s:.3f}")
print(f"overhead_per_frame_ms               = {(actual_s - estimate_s) * 1000 / frames:.2f}")
print(f"actual / estimate                   = {actual_s / estimate_s:.2f}x")
print("status:", result.get("status"))
for r in result.get("results", []):
    if "error" in r:
        print("  ERROR at", r.get("position"), "->", r["error"])

if args.return_to_start:
    ctrl.core.set_xy_position(x0, y0)
    print(f"returned to start XY = ({x0:.1f}, {y0:.1f})")
else:
    print("NOTE: stage left at the last grid point; re-centre if needed.")
