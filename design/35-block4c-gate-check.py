"""Mechanical checks for the Block 4c rig gate (Windows-friendly)."""

import argparse
import json
from pathlib import Path

import yaml


def check_profile(path: Path, expected_device: str) -> bool:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    stages = document.get("named_stages")
    matches = [item for item in stages or [] if item.get("device") == expected_device]
    valid = (
        isinstance(stages, list)
        and bool(stages)
        and len(matches) == 1
        and isinstance(matches[0].get("min_um"), (int, float))
        and isinstance(matches[0].get("max_um"), (int, float))
        and matches[0]["min_um"] <= matches[0]["max_um"]
    )
    print("NAMED_STAGES NONEMPTY:", isinstance(stages, list) and bool(stages))
    print("EXPECTED DEVICE HAS FINITE ORDERED BOUNDS:", valid)
    print("NAMED_STAGES:", stages)
    return valid


def content_blocks(records: list[dict]) -> list[dict]:
    return [
        block
        for record in records
        if isinstance(record.get("content"), list)
        for block in record["content"]
        if isinstance(block, dict)
    ]


def check_history(path: Path, device: str, inside: float, outside: float) -> bool:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    blocks = content_blocks(records)
    calls = [
        block for block in blocks
        if block.get("type") == "tool_use"
        and block.get("name") == "move_named_stage"
        and block.get("input", {}).get("device") == device
    ]
    inside_calls = [c for c in calls if c.get("input", {}).get("um") == inside]
    outside_calls = [c for c in calls if c.get("input", {}).get("um") == outside]
    results = {
        block.get("tool_use_id"): block for block in blocks
        if block.get("type") == "tool_result"
    }
    inside_result = results.get(inside_calls[-1].get("id")) if inside_calls else None
    inside_ok = inside_result is not None and not inside_result.get("is_error", False)
    outside_result = results.get(outside_calls[-1].get("id"), {}) if outside_calls else {}
    outside_refused = bool(outside_calls) and (
        outside_result.get("is_error", False)
        or "safety constraint" in str(outside_result.get("content", "")).casefold()
        or "outside" in str(outside_result.get("content", "")).casefold()
    )
    print("IN-RANGE MOVE CALLED AND SUCCEEDED:", inside_ok)
    print("OUT-OF-RANGE MOVE CALLED AND REFUSED:", outside_refused)
    return inside_ok and outside_refused


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    profile = subparsers.add_parser("profile")
    profile.add_argument("path", type=Path)
    profile.add_argument("device")
    history = subparsers.add_parser("history")
    history.add_argument("path", type=Path)
    history.add_argument("device")
    history.add_argument("inside", type=float)
    history.add_argument("outside", type=float)
    args = parser.parse_args()
    if args.command == "profile":
        return 0 if check_profile(args.path, args.device) else 1
    return 0 if check_history(
        args.path, args.device, args.inside, args.outside
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
