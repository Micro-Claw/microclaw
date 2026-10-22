"""Block 84e check: one line per model call of the newest session in a folder.

    .venv\\Scripts\\python.exe design\\84-block84e-check.py --session .

Prints, per call, the reasoning level recorded for it, the tokens the server
processed and reused, and the hidden-reasoning length, then the distinct levels
seen. A session that switched levels shows each switch as a change in the
`effort` column, and the call after a switch to a level not used before as a
large `processed` count. Reading only: it never contacts the server.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session", default=".", help="folder holding *_microclaw_usage.jsonl")
    args = parser.parse_args()
    target = Path(args.session)
    files = sorted(target.glob("*_microclaw_usage.jsonl")) if target.is_dir() else [target]
    if not files or not files[-1].exists():
        print(f"NOT EXERCISED - no *_microclaw_usage.jsonl in {target}")
        return 1
    path = files[-1]
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    print(f"{path.name}: {len(records)} model calls")
    if records and "reasoning_effort" not in records[0]:
        print("NOT EXERCISED - these records carry no reasoning_effort field: "
              "the session ran on a tree without block 84e")
        return 1
    print(f"{'call':>4}  {'effort':<16} {'processed':>9} {'reused':>7} {'reasoning':>9}")
    for index, record in enumerate(records):
        effort = record.get("reasoning_effort") or "server default"
        print(f"{index:>4}  {effort:<16} {record.get('prompt_n') or 0:>9} "
              f"{record.get('cache_n') or 0:>7} {record.get('reasoning_characters') or 0:>9}")
    seen = []
    for record in records:
        effort = record.get("reasoning_effort") or "server default"
        if not seen or seen[-1] != effort:
            seen.append(effort)
    print("levels in order:", " -> ".join(seen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
