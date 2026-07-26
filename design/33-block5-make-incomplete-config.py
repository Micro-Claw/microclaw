"""Write an incomplete Block 5 rig-gate config without modifying the source."""

import argparse
from pathlib import Path

import yaml


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
parser.add_argument(
    "--remove",
    default="confirm_above_illuminated_ms",
    help="One acquisition policy field to remove",
)
args = parser.parse_args()

source = args.source.resolve()
output = args.output.resolve()
if source == output:
    parser.error("--output must differ from --source; the reviewed config is immutable")

document = yaml.safe_load(source.read_text(encoding="utf-8"))
acquisition = document.get("acquisition") if isinstance(document, dict) else None
if not isinstance(acquisition, dict):
    parser.error("source has no acquisition mapping")
if args.remove not in acquisition:
    parser.error(f"acquisition.{args.remove} is not present in source")

del acquisition[args.remove]
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(
    yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
    encoding="utf-8",
)
print(f"wrote {output}; removed acquisition.{args.remove}; source unchanged: {source}")
