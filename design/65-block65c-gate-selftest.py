"""Drive block 65c's computed gate against either implementation tree.

Run from inside the block-65c worktree so this file is the reviewed gate while
``--tree`` selects which product it imports:

    python design/65-block65c-gate-selftest.py --tree . --expect implemented
    python design/65-block65c-gate-selftest.py --tree ../microclaw --expect prechange

The second command must discriminate: the four new-contract limbs must fail on
main before 65c, while the existing-route regression limb still passes.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", required=True, help="product checkout to import")
    parser.add_argument("--expect", required=True, choices=("implemented", "prechange"))
    args = parser.parse_args()
    tree = Path(args.tree).resolve()
    gate = Path(__file__).with_name("65-block65c-gate.py").resolve()
    out = Path(tempfile.mkdtemp(prefix=f"gate65c-{args.expect}-"))
    env = os.environ.copy()
    env["MICROCLAW_GATE_TREE"] = str(tree)
    env["PYTHONPATH"] = str(tree)
    run = subprocess.run(
        [sys.executable, str(gate), "--out", str(out)],
        cwd=tree, env=env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )
    print(run.stdout)
    result_path = out / "gate65c_results.json"
    if not result_path.exists():
        print(f"SELFTEST FAIL: probe wrote no {result_path.name}; exit={run.returncode}")
        return 1
    rows = json.loads(result_path.read_text(encoding="utf-8"))
    statuses = {row["limb"]: row["status"] for row in rows}
    print(f"TREE: {tree}\nPROBE EXIT CODE: {run.returncode}\nSTATUSES: {statuses}")

    if args.expect == "implemented":
        if run.returncode != 0 or any(value != "PASS" for value in statuses.values()):
            print("SELFTEST FAIL: accepted implementation did not pass every computed limb")
            return 1
        print("SELFTEST PASS: accepted implementation passed all computed limbs.")
        return 0

    required_failures = {
        "A_adaptive_export", "B_rule_not_trace", "C_contract_preflight",
        "D_retired_vocabulary", "E_shape_refusals",
    }
    missed = sorted(name for name in required_failures if statuses.get(name) != "FAIL")
    if run.returncode == 0 or missed:
        print(f"SELFTEST FAIL: pre-change tree did not discriminate in {missed}")
        return 1
    if statuses.get("F_existing_routes") != "PASS":
        print("SELFTEST FAIL: the regression control failed on the pre-change tree; "
              "that is not evidence for the new route")
        return 1
    print("SELFTEST PASS: pre-change tree failed every new-contract limb while "
          "the existing-route control still passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
