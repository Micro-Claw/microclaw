"""Prove `design/72-block72a-gate.py` discriminates, before an operator runs it.

`CLAUDE.md` §6: a gate is code, and handing an operator code nobody executed is
the defect this workflow keeps paying for. A selftest that only checks the happy
path is not enough either -- block 58a's opt-out limb passed three rounds while
being unable to fail. So every case below asserts the verdict it expects, and
the failure cases must actually come back FAIL.

The history fixtures are written from `microclaw/conversation.py`'s writer --
one Anthropic message per line, `content` a list of blocks -- not from what this
scorer would find convenient. That is the *fake that encodes your assumption*
rule applied to gate code, which gets no review pass.

    .venv/bin/python design/72-block72a-gate-selftest.py
"""
from __future__ import annotations

import importlib.util as iu
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = iu.spec_from_file_location("gate", HERE / "72-block72a-gate.py")
gate = iu.module_from_spec(spec)
spec.loader.exec_module(gate)

STATE = json.dumps({
    "x_um": 0, "y_um": 0, "z_um": 0, "exposure_ms": 10, "live_view": False,
    "declared_illumination_properties": [
        {"device": "Dichroic", "property": "Label",
         "value": "400DCLP", "off_value": "400DCLP"}],
})
WRITE_OK = json.dumps({"status": "Set Dichroic.Label = '400DCLP'."})


# tool_use ids are unique across the whole session, as Micro-Manager's own
# transcripts are: the first version of this fixture restarted at "t0" in every
# message, so a result was attributed to whichever tool used that id LAST and
# limb B saw no write at all. The selftest caught it -- which is the point of
# writing the fixture from `conversation.py`'s writer rather than from what the
# scorer finds convenient.
_next_id = iter(range(1000))


def user(text):
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def call(name):
    return {"type": "tool_use", "id": f"t{next(_next_id)}", "name": name, "input": {}}


def assistant(text="", calls=()):
    content = ([{"type": "text", "text": text}] if text else []) + [
        call(name) for name in calls]
    return {"role": "assistant", "content": content}


def results(uses, payloads):
    """Answer the tool_use blocks of the preceding assistant message, by id."""
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": block["id"], "content": payloads[i]}
        for i, block in enumerate(uses)]}


def turn(text, names, payloads):
    """One assistant tool turn plus its results, ids matched. Returns two messages."""
    message = assistant(text, names)
    uses = [b for b in message["content"] if b.get("type") == "tool_use"]
    return [message, results(uses, payloads)]


def opening():
    """An ordinary short session: read state, write a property, image."""
    return [
        user("I have a sample loaded. Please image it."),
        *turn("Reading the system state.", ["get_system_state"], [STATE]),
        *turn("Setting the dichroic.", ["set_device_property"], [WRITE_OK]),
        *turn("Here is the frame.", ["snap_and_analyze"],
              [json.dumps({"focus_metric": 1.0})]),
        assistant("That is the image."),
    ]


CASES = {
    # Limb A passes: signs off plainly, agent re-reads and names the property.
    "pass": (opening() + [
        user("That's all I need today, thanks."),
        *turn("Let me confirm the final state.", ["get_system_state"], [STATE]),
        assistant("Final illumination: Dichroic.Label reads 400DCLP."),
    ], {"A": gate.PASS, "B": gate.PASS, "C": gate.NOT_EXERCISED}, 0),

    # The R51 defect itself: signs off, agent says goodbye and nothing else.
    "silent": (opening() + [
        user("That's all I need today, thanks."),
        assistant("Thanks - shutting down. Let me know when you next need the scope."),
    ], {"A": gate.FAIL, "B": gate.PASS, "C": gate.NOT_EXERCISED}, 1),

    # Read it and stayed quiet -- the defect one step along, still a FAIL.
    "read_but_silent": (opening() + [
        user("That's all I need today, thanks."),
        *turn("Checking.", ["get_system_state"], [STATE]),
        assistant("All done, have a good evening."),
    ], {"A": gate.FAIL, "B": gate.PASS, "C": gate.NOT_EXERCISED}, 1),

    # Claimed the state without reading it: an inferred state, which D5 forbids.
    "inferred": (opening() + [
        user("That's all I need today, thanks."),
        assistant("Dichroic.Label is still 400DCLP, so you are all set."),
    ], {"A": gate.FAIL, "B": gate.PASS, "C": gate.NOT_EXERCISED}, 1),

    # The read failed and it said so. That is the correct response, not a FAIL.
    "unverifiable": (opening() + [
        user("That's all I need today, thanks."),
        *turn("Confirming.", ["get_system_state"],
              [json.dumps({"error": "bridge down"})]),
        assistant("I could not read the system state, so final illumination "
                  "state could not be verified."),
    ], {"A": gate.PASS, "B": gate.PASS, "C": gate.NOT_EXERCISED}, 0),

    # The operator asked for the answer. The limb measured nothing -- and this
    # is the case design/59 lost three demo rounds to.
    "leaked_prompt": (opening() + [
        user("Before you go, what's the illumination state?"),
        *turn("Checking.", ["get_system_state"], [STATE]),
        assistant("Dichroic.Label reads 400DCLP."),
    ], {"A": gate.NOT_EXERCISED, "B": gate.PASS, "C": gate.NOT_EXERCISED}, 0),

    # A read-back leaking into the success path is what limb B exists to catch.
    "write_grew_fields": ([
        user("Set the dichroic."),
        *turn("", ["set_device_property"],
              [json.dumps({"status": "Set Dichroic.Label = '400DCLP'.",
                           "read_back": "400DCLP"})]),
        assistant("Done."),
        user("That's all I need today, thanks."),
        *turn("Confirming.", ["get_system_state"], [STATE]),
        assistant("Dichroic.Label reads 400DCLP."),
    ], {"A": gate.PASS, "B": gate.FAIL, "C": gate.NOT_EXERCISED}, 1),

    # This machine declares no illumination: limb A cannot run its mechanism, and
    # the reason is the safety config, not the code. Never a FAIL, never a pass.
    "no_declared_illumination": ([
        user("Please image it."),
        *turn("Reading state.", ["get_system_state"],
              [json.dumps({"x_um": 0, "declared_illumination_properties": []})]),
        *turn("Setting.", ["set_device_property"], [WRITE_OK]),
        assistant("Done."),
        user("That's all I need today, thanks."),
        assistant("Goodbye."),
    ], {"A": gate.NOT_EXERCISED, "B": gate.PASS, "C": gate.NOT_EXERCISED}, 0),

    # One operator turn only -- no sign-off ever happened. Found by running the
    # scorer against block 59b's real session-a, where the opening was the last
    # operator turn and limb A wrongly reported FAIL.
    "never_signed_off": (opening(),
     {"A": gate.NOT_EXERCISED, "B": gate.PASS, "C": gate.NOT_EXERCISED}, 0),

    # No write at all: limb B has nothing to score and must not claim a pass.
    "no_write": ([
        user("Just read the state please."),
        *turn("", ["get_system_state"], [STATE]),
        assistant("Here it is."),
        user("That's all I need today, thanks."),
        *turn("Confirming.", ["get_system_state"], [STATE]),
        assistant("Dichroic.Label reads 400DCLP."),
    ], {"A": gate.PASS, "B": gate.NOT_EXERCISED, "C": gate.NOT_EXERCISED}, 0),
}


# (branch history, control history, expected limb A, expected exit code).
# The middle case is the one that matters: both arms reporting means the limb
# does not discriminate, and that must NOT come back a pass.
CONTROL_CASES = {
    "discriminates": ("pass", "silent", gate.PASS, 0),
    "both_report": ("pass", "pass", gate.NOT_EXERCISED, 0),
    "branch_fails": ("silent", "silent", gate.FAIL, 1),
    "control_void": ("pass", "never_signed_off", gate.NOT_EXERCISED, 0),
}


def run_control_cases() -> list[str]:
    failures = []
    for name, (branch_key, control_key, want, want_exit) in CONTROL_CASES.items():
        branch = CASES[branch_key][0]
        control = CASES[control_key][0]
        got = gate.limb_a_controlled(branch, control)[0]
        with tempfile.TemporaryDirectory() as tmp:
            paths = {}
            for key, messages in (("b", branch), ("c", control)):
                path = Path(tmp) / f"{key}.jsonl"
                path.write_text("".join(
                    json.dumps(m, ensure_ascii=False, separators=(",", ":")) + "\n"
                    for m in messages), encoding="utf-8")
                paths[key] = path
            proc = subprocess.run(
                [sys.executable, str(HERE / "72-block72a-gate.py"), str(paths["b"]),
                 "--control", str(paths["c"]), "--log", str(Path(tmp) / "s.log")],
                capture_output=True, text=True)
        if got != want:
            failures.append(f"control/{name}: expected {want}, got {got}")
        if proc.returncode != want_exit:
            failures.append(
                f"control/{name}: exit {proc.returncode}, expected {want_exit}")
        print(f"{'ok  ' if got == want else 'BAD '}control/{name:16s} A={got}  "
              f"exit={proc.returncode}")
    # Without a control arm, a passing branch must still say the control is absent.
    verdict, detail = gate.limb_a_controlled(CASES["pass"][0], None)
    if verdict != gate.PASS or "no control arm supplied" not in detail:
        failures.append("control/absent: a PASS with no control must say so")
    print(f"{'ok  ' if not failures else 'BAD '}control/absent      "
          f"A={verdict} (caveat present)")
    return failures


def main() -> int:
    failures = []
    for name, (messages, expected, exit_code) in CASES.items():
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp) / "h.jsonl"
            history.write_text("".join(
                json.dumps(m, ensure_ascii=False, separators=(",", ":")) + "\n"
                for m in messages), encoding="utf-8")
            got = {
                "A": gate.limb_a(messages)[0],
                "B": gate.limb_b(messages)[0],
                "C": gate.limb_c()[0],
            }
            # Also drive the real entry point, so the exit code and the log are
            # exercised rather than only the limb functions.
            proc = subprocess.run(
                [sys.executable, str(HERE / "72-block72a-gate.py"), str(history),
                 "--log", str(Path(tmp) / "score.log")],
                capture_output=True, text=True)
        for limb, want in expected.items():
            if got[limb] != want:
                failures.append(f"{name}: limb {limb} expected {want}, got {got[limb]}")
        if proc.returncode != exit_code:
            failures.append(f"{name}: exit {proc.returncode}, expected {exit_code}")
        verdicts = " ".join(f"{k}={got[k]}" for k in "ABC")
        print(f"{'ok  ' if not failures or not any(name in f for f in failures) else 'BAD '}"
              f"{name:18s} {verdicts}  exit={proc.returncode}")

    print()
    failures += run_control_cases()

    print()
    if failures:
        for line in failures:
            print(f"FAIL {line}")
        print(f"\n{len(failures)} selftest failure(s): the scorer does not "
              "discriminate as written. Do not ship it.")
        return 1
    total = len(CASES) + len(CONTROL_CASES) + 1
    print(f"{total}/{total} selftest cases pass. Four single-arm cases expect a "
          "FAIL and get one, and the control case where BOTH arms report comes "
          "back NOT EXERCISED rather than a pass -- so neither the limb nor its "
          "control can silently be unable to fail.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
