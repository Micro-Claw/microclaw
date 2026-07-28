"""One-command peak-RSS and deterministic-output probe for Block 9.

Example:
  /usr/bin/time -l python design/29-block9-rss-probe.py DATASET OUTPUT.tif CALIBRATION.json --axis time=0
"""
import argparse
import json
import resource

from microclaw.config import load_safety_config_or_exit
from microclaw.controller import MicroscopeController
from microclaw.safety import SafetyGuard
from microclaw.tools import build_stage_coordinate_mosaic


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("dataset_path")
parser.add_argument("output_path")
parser.add_argument("calibration_artifact")
parser.add_argument("--config", required=True)
parser.add_argument("--axis", action="append", default=[], metavar="NAME=JSON_VALUE")
parser.add_argument("--output-pixel-size-um", type=float)
args = parser.parse_args()

selection = {}
for assignment in args.axis:
    name, raw = assignment.split("=", 1)
    selection[name] = json.loads(raw)
parsed = load_safety_config_or_exit(args.config)
guard = SafetyGuard(parsed.constraints)
ctrl = MicroscopeController(guard=guard)
result = build_stage_coordinate_mosaic(
    ctrl, guard, args.dataset_path, args.output_path, selection,
    {"kind": "artifact", "path": args.calibration_artifact},
    args.output_pixel_size_um,
)
print(json.dumps({
    "pixel_sha256": result["pixel_sha256"],
    "shape": result["shape"],
    "peak_rss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
}, sort_keys=True))
