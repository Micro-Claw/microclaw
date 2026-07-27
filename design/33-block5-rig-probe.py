"""Block 5 rig gate: validate the map, then measure one dark acquisition ledger."""

import argparse
import json
import sys

from microclaw import tools
from microclaw.acquisition import plan_events
from microclaw.authorization import validate_live_rig
from microclaw.config import load_safety_config_or_exit
from microclaw.controller import MicroscopeController
from microclaw.safety import SafetyGuard


sys.stdout.reconfigure(line_buffering=True)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--config", required=True)
parser.add_argument("--save-dir", required=True)
parser.add_argument("--port", required=True, type=int)
parser.add_argument("--frames", type=int, default=2)
parser.add_argument("--exposure-ms", type=float, default=5.0)
args = parser.parse_args()
if args.frames <= 0 or args.exposure_ms <= 0:
    parser.error("--frames and --exposure-ms must be positive")

parsed = load_safety_config_or_exit(args.config)
guard = SafetyGuard(parsed.constraints)

# Check --save-dir against the configured workspace BEFORE connecting or
# planning. This exact mismatch wasted a demo run on 2026-07-27: the refusal
# otherwise surfaces only after MAP and PLAN have printed, which reads like a
# Block 5 failure when it is a config-path mistake. On a rig it would also
# waste the session.
try:
    guard.resolve_in_workspace(args.save_dir)
except Exception as exc:
    raise SystemExit(
        f"FAIL (before any hardware contact): --save-dir {args.save_dir!r} is not "
        f"inside the configured workspace_dir "
        f"{parsed.constraints.workspace_dir!r}.\n{exc}\n"
        "Fix workspace_dir in the config or pass a --save-dir inside it."
    )

ctrl = MicroscopeController(port=args.port, guard=guard)
if not ctrl.is_connected():
    raise SystemExit(f"FAIL: controller did not connect on port {args.port}")

report = validate_live_rig(ctrl, parsed, guard=guard)
tools.CONFIRM_FN = lambda summary, kind="action": True  # dark gate only
events = tools._build_acquisition_events(
    num_time_points=args.frames,
    time_interval_s=0,
    exposure_ms=args.exposure_ms,
)
plan = plan_events(ctrl, events, args.exposure_ms)
print("MAP", json.dumps({
    "verdict": report.verdict,
    "complete": report.complete,
    "dose_rows": sorted(
        entry.path for entry in report.entries
        if entry.capability == "acquisition-dose"
    ),
    "mda": [
        entry.classification for entry in report.entries
        if entry.path == "mmstudio-mda"
    ],
}, sort_keys=True))
print("PLAN", json.dumps({
    "frames": plan.frames,
    "exposure_ms_per_frame": plan.exposure_ms_per_frame,
    "estimated_duration_s": plan.estimated_duration_s,
    "estimated_bytes": plan.estimated_bytes,
    "illuminated_ms": plan.illuminated_ms,
}, sort_keys=True))

result = tools.execute_tool(
    "run_timelapse",
    {
        "n_frames": args.frames,
        "interval_s": 0,
        "save_dir": args.save_dir,
        "name": "block5_authorized",
        "exposure_ms": args.exposure_ms,
    },
    ctrl,
    guard,
)
ledger = tools._acquisition_ledger(ctrl)
print("RESULT", result)
print("LEDGER", json.dumps({
    "frames": ledger.frames,
    "bytes": ledger.bytes,
    "illuminated_ms": ledger.illuminated_ms,
}, sort_keys=True))
