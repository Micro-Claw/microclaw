"""Run block 77a's replay instrument against fakes, before it is pointed at a model.

A gate is code, and handing over code nobody executed is the defect this
workflow keeps paying for. This makes no API call and needs no session file: it
builds a session-shaped recording, drives the instrument with a scripted client,
and checks the parts a `--dry-run` does not reach.

The load-bearing check is the last one. design/59 lost a whole rig trip to a gate
whose fake could not tell the two trees apart, so this resolves `load_skill`
against **both** trees and refuses to pass unless the stale one hands the model
`design/26` and the fixed one does not. If that fails, the instrument is not
measuring the variable it claims to.

    python design/77-block77a-replay-selftest.py --stale <checkout> --fixed <checkout>

Omit `--stale`/`--fixed` to skip only that check; everything else still runs.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
replay = __import__("77-block77a-replay".replace("-", "_")) if False else None

# The module's filename is not an identifier, so load it by path.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "block77a_replay", HERE / "77-block77a-replay.py")
replay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay)

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f" -- {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def fake_session() -> list[dict]:
    """A recording shaped like microclaw's history, not like our caller.

    Written from the JSONL's own shape: a plain-string opening user turn, an
    assistant turn carrying `tool_use` blocks, and a user turn whose content is a
    list of `tool_result` blocks keyed by `tool_use_id`.
    """
    return [
        {"role": "user", "content": "Find filamentous MT fields and tell me "
                                    "where kinesins are walking."},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Orienting."},
            {"type": "tool_use", "id": "u1", "name": "get_system_state",
             "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "u1",
             "content": '{"x_um": -570.5, "exposure_ms": 100}'}]},
        {"role": "user", "content": "5 seconds continuous, 100 ms."},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "u2",
             "name": "run_analysis_on_saved_dataset",
             "input": {"adapter": "frame_statistics"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "u2",
             "content": '{"status": "completed", "observations": [{"snr": 3.01}]}'}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Reading the reference."},
            {"type": "tool_use", "id": "u3", "name": "load_skill",
             "input": {"name": "hook-authoring"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "u3",
             "content": "STALE SKILL TEXT"}]},
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stale", type=Path)
    ap.add_argument("--fixed", type=Path)
    args = ap.parse_args()

    session = fake_session()

    # --- fixtures ------------------------------------------------------
    table = replay.recorded_results(session)
    check("recorded results are keyed by tool name",
          set(table) == {"get_system_state", "run_analysis_on_saved_dataset",
                         "load_skill"}, str(sorted(table)))
    check("the opening message is the operator's own first turn",
          replay.first_user_message(session).startswith("Find filamentous"))
    check("later operator turns are carried verbatim, opening excluded",
          replay.operator_replies(session) == ["5 seconds continuous, 100 ms."])

    # --- arm B's fixture -----------------------------------------------
    live = {"load_skill": "FIXED SKILL TEXT"}
    pruned = replay.arm_b_messages(session, live, full_history=False)
    check("pruned arm B ends on the live skill result",
          pruned[-1]["content"][0]["content"] == "FIXED SKILL TEXT")
    check("pruned arm B carries the successful offline run the fixture rests on",
          any("completed" in str(m.get("content")) for m in pruned))

    full = replay.arm_b_messages(session, live, full_history=True)
    check("full arm B stops at the session's own load_skill call, found by "
          "searching for it rather than by line number",
          full[-2]["content"][-1]["name"] == "load_skill"
          and full[-2]["content"][-1]["id"] == "replay_skill")
    check("full arm B never shows the model the stale recorded skill text",
          "STALE SKILL TEXT" not in json.dumps(full))

    # A session that never loaded the skill must stop the gate, not be scored.
    without = [m for m in session if "u3" not in json.dumps(m.get("content"))]
    try:
        replay.arm_b_messages(without, live, full_history=True)
        check("a session with no load_skill call refuses", False)
    except SystemExit as exc:
        check("a session with no load_skill call refuses", True, str(exc)[:60])

    # --- the driver ----------------------------------------------------
    client = replay.ScriptedClient([
        [{"type": "tool_use", "id": "a", "name": "get_system_state", "input": {}},
         {"type": "tool_use", "id": "b", "name": "get_focus_lock_state",
          "input": {}}],
        [{"type": "text", "text": "Plan: attach a hook_strategy observer, then "
                                  "an analyze_completed_dataset adapter."}],
    ])
    result = replay.run_sample(client, "m", "sys", [],
                               [{"role": "user", "content": "go"}], table, live,
                               max_turns=4, replies=[])
    check("a recorded tool is answered from the recording",
          json.loads(client.sent[1]["messages"][-1]["content"][0]["content"])
          ["x_um"] == -570.5)
    check("an unrecorded tool is counted, not silently answered",
          result["unrecorded"] == {"get_focus_lock_state": 1}, str(result["unrecorded"]))
    check("both deliverables in one turn score BOTH_ACCOUNTED",
          result["verdict"] == "BOTH_ACCOUNTED", result["verdict"])

    # A model that stops early must receive the operator's next real message.
    client = replay.ScriptedClient([
        [{"type": "text", "text": "How long per field?"}],
        [{"type": "text", "text": "Understood."}],
    ])
    replay.run_sample(client, "m", "sys", [], [{"role": "user", "content": "go"}],
                      table, live, max_turns=4,
                      replies=replay.operator_replies(session))
    check("a question is answered with the operator's own next words",
          client.sent[-1]["messages"][-1]["content"] == "5 seconds continuous, 100 ms.")

    # --- the scorer ----------------------------------------------------
    cases = {
        "DEV_REFERENCE": "the orchestrator is a design/26 proposal",
        "DECLARED_UNAVAILABLE": "custom offline analysis is not yet implemented here",
        "OFFLINE_ONLY": "I will write an analyze_completed_dataset adapter",
        "ATTACH_ONLY": "I will pass hook_strategy to the timelapse",
        "NEITHER": "I will acquire the movies and look at them",
    }
    for expected, said in cases.items():
        got = replay.verdict(replay.score(said, []))
        check(f"scorer: {expected}", got == expected, got)
    check("a stale claim wins over an offline verb mentioned alongside it",
          replay.verdict(replay.score(
              "analyze_completed_dataset is a design/26 proposal", [])) == "DEV_REFERENCE")

    # --- the variable actually varies ----------------------------------
    if args.stale and args.fixed:
        texts = {}
        for label, tree in (("stale", args.stale), ("fixed", args.fixed)):
            out = subprocess.run(
                [sys.executable, "-c",
                 "import sys, json; sys.path.insert(0, sys.argv[1]); "
                 "from microclaw import tools; "
                 "print(json.dumps(tools.load_skill(None, None, 'hook-authoring')))",
                 str(tree)],
                capture_output=True, text=True, cwd=str(tree))
            if out.returncode != 0:
                check(f"{label} tree loads its own skill", False, out.stderr[-200:])
                continue
            texts[label] = json.loads(out.stdout)["documentation"]
        check("the control tree hands the model a development reference",
              "design/26" in texts.get("stale", ""))
        check("the tree under test does not",
              "design/" not in texts.get("fixed", "x design/"))
        check("the two trees are not the same text",
              texts.get("stale") != texts.get("fixed"))
    else:
        print("SKIP  tree discrimination (pass --stale and --fixed to run it)")

    print(f"\n{len(FAILURES)} failed" if FAILURES else "\nall checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
