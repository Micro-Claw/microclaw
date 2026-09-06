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
        # Two calls to one tool on different datasets, because that is the real
        # recording's shape and the shape that broke arm B's fixture live: the
        # first is an unrelated near-blank check, the second is the kinesin one
        # the fixture actually rests on.
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "u2a",
             "name": "run_analysis_on_saved_dataset",
             "input": {"dataset_path": "D:/tirf_006um_1",
                       "adapter": "frame_statistics"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "u2a",
             "content": '{"status": "completed", "observations": [{"snr": 2.47}]}'}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "u2",
             "name": "run_analysis_on_saved_dataset",
             "input": {"dataset_path": "D:/kinesin_640/mt_kin_1_r5c10",
                       "adapter": "frame_statistics"}}]},
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
    # The live gate handed the model a payload from the wrong dataset, which it
    # noticed and argued with instead of deciding. Name-keyed lookup is not
    # enough when one tool is called on several inputs.
    # The payload is a JSON *string* inside the message, so its quotes are
    # escaped once more by this dump -- match on the values, not on the quoting.
    dumped = json.dumps(pruned)
    check("pruned arm B takes the KINESIN result, not the first one recorded",
          "3.01" in dumped and "2.47" not in dumped)
    no_kinesin = [m for m in session if "mt_kin" not in json.dumps(m.get("content"))]
    try:
        replay.arm_b_messages(no_kinesin, live, full_history=False)
        check("a session with no kinesin analysis refuses the pruned fixture", False)
    except SystemExit as exc:
        check("a session with no kinesin analysis refuses the pruned fixture",
              True, str(exc)[:52])

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
    check("a plan naming both the attached observer and the offline verb scores "
          "BOTH_ACCOUNTED -- D4's 'account separately for the overlay'",
          result["verdict"] == "BOTH_ACCOUNTED", result["verdict"])
    # Measured on 14 live samples: the control named this tool once in eight
    # while declining to use it, so it cannot be the criterion.
    check("naming run_analysis_on_saved_dataset alone is NOT a plan",
          replay.verdict(replay.score(
              "run_analysis_on_saved_dataset is where that would go", []))
          == "NO_ADAPTER")
    check("a generate_and_save_hook call is reported as authoring",
          replay.score("", ["generate_and_save_hook"])["authored"]
          == ["generate_and_save_hook"])

    # The gate's first live control run scored four cut-off samples as NEITHER,
    # which read as a real null. A sample that never stopped calling tools has
    # not answered anything.
    looping = replay.ScriptedClient([
        [{"type": "tool_use", "id": "x", "name": "get_system_state", "input": {}}]
        for _ in range(3)])
    cut = replay.run_sample(looping, "m", "sys", [],
                            [{"role": "user", "content": "go"}], table, live,
                            max_turns=2, replies=[])
    check("a sample cut off mid-tool-loop is NO_DECISION, not NEITHER",
          cut["verdict"] == "NO_DECISION" and cut["finished"] is False,
          cut["verdict"])

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
        "PLANS_ADAPTER": "I will write an analyze_completed_dataset adapter",
        "ATTACH_ONLY": "I will pass hook_strategy to the timelapse",
        # Naming the verb is not authoring it -- eight control samples named
        # run_analysis_on_saved_dataset while delivering a standalone script.
        "NO_ADAPTER": "run_analysis_on_saved_dataset would be the place for this",
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
