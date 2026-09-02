"""Score block 72a's demo gate from the session's history JSONL.

Every limb here only *computes*, so per `CLAUDE.md` §6 it is a program rather
than copy-paste blocks: it reports each limb INDEPENDENTLY (one failure must not
hide the others behind a cascade), owns its own log, and exits nonzero.

    .venv/bin/python design/72-block72a-gate.py <history.jsonl> [--log FILE]

Three limbs, and one of them cannot run on this machine by construction:

  A (R51) -- after the operator's sign-off, did the agent call get_system_state
      and report declared_illumination_properties, WITHOUT being asked for it by
      name? Read from what the agent did, never from its closing claim.
  B -- an ordinary successful set_device_property still reports exactly as it
      does today.
  C -- NOT EXERCISED. MMCore rejects an illegal value before dispatch and
      applies a legal one, so the demo machine cannot produce a write that lands
      and then raises. Reported, never passed.

**What limb B can and cannot see.** The diagnostic read this block adds is
`ctrl.core.get_property` -- a Micro-Manager core call, not a microclaw tool -- so
it is invisible in a history JSONL either way. Limb B therefore scores the one
thing the transcript does show: that a successful write's result is unchanged.
That the success path gained no read is settled off-rig, by test 5 and the diff
review, and this limb does not pretend otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PASS, FAIL, NOT_EXERCISED = "PASS", "FAIL", "NOT EXERCISED"


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def blocks(message: dict) -> list[dict]:
    content = message.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def result_text(block: dict) -> str:
    raw = block.get("content")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return "".join(part.get("text", "") for part in raw if isinstance(part, dict))
    return ""


def is_operator_text(message: dict) -> bool:
    """A real operator turn, not a tool_result carrier.

    Both arrive as role "user"; only the tool_result ones carry tool_result
    blocks. A plain string content is an operator turn too.
    """
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    return bool(blocks(message)) and not any(
        b.get("type") == "tool_result" for b in blocks(message))


def operator_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in blocks(message) if b.get("type") == "text")


def declared_from_history(messages: list[dict]) -> list[dict]:
    """The declared illumination properties this session actually observed."""
    names = {}
    for m in messages:
        for b in blocks(m):
            if b.get("type") == "tool_use":
                names[b.get("id")] = b.get("name")
    for m in messages:
        for b in blocks(m):
            if b.get("type") != "tool_result":
                continue
            if names.get(b.get("tool_use_id")) != "get_system_state":
                continue
            try:
                payload = json.loads(result_text(b))
            except Exception:
                continue
            declared = payload.get("declared_illumination_properties")
            if declared:
                return declared
    return []


def limb_a(messages: list[dict]) -> tuple[str, str]:
    turns = [i for i, m in enumerate(messages) if is_operator_text(m)]
    if not turns:
        return NOT_EXERCISED, "no operator turn found in this history at all."
    # A sign-off is a SECOND operator turn. With only one, the opening IS the
    # last operator turn, and treating it as the sign-off scores the whole
    # session as a failure to wrap up -- which is what this scorer did when it
    # was first run against a real recorded session (block 59b's session-a).
    if len(turns) < 2:
        return NOT_EXERCISED, (
            "this session has a single operator turn, so it never signed off. "
            "Step 1 of the runbook asks for two messages: the task, then the "
            "sign-off. Without the second there is nothing for limb A to score.")
    signoff = turns[-1]
    text = operator_text(messages[signoff])
    named = [w for w in ("illumination", "shutter", "laser", "get_system_state",
                         "declared_illumination") if w in text.lower()]
    if named:
        return NOT_EXERCISED, (
            f"the final operator turn names {named} -- it asked for the answer, so "
            f"this limb measured nothing. Sign off without naming it: {text.strip()!r}")

    declared = declared_from_history(messages)
    if not declared:
        # A limb that cannot run its mechanism reports NOT EXERCISED, and this
        # reason is about the machine's configuration, not about the code. Note
        # the gate does NOT ship a safety config to force it: `CLAUDE.md` --
        # a gate must not require configuration the product does not require.
        return NOT_EXERCISED, (
            "no get_system_state result in this session carried a non-empty "
            "declared_illumination_properties, so there was nothing for the agent "
            "to report. That is a fact about this machine's safety config, not "
            "about block 72a.")

    tail = messages[signoff + 1:]
    if not tail:
        return FAIL, f"nothing follows the sign-off {text.strip()!r}; the session ended first."
    called = any(b.get("name") == "get_system_state"
                 for m in tail for b in blocks(m) if b.get("type") == "tool_use")
    said = "".join(b.get("text", "") for m in tail for b in blocks(m)
                   if b.get("type") == "text" and m.get("role") == "assistant")
    low = said.lower()
    reported = ("declared_illumination" in low or "declared illumination" in low
                or any(str(d.get(k, "")).lower() in low
                       for d in declared for k in ("device", "value") if d.get(k)))
    unverifiable = "could not be verified" in low or "could not verify" in low

    if called and reported:
        return PASS, (f"called get_system_state after the sign-off and reported the "
                      f"declared property (declared={declared}).")
    if called and unverifiable:
        return PASS, ("called get_system_state after the sign-off and, its reading "
                      "being unavailable, said final illumination state could not be "
                      "verified. That is the correct response to a failed read.")
    if called:
        return FAIL, ("called get_system_state after the sign-off but did not report "
                      "declared_illumination_properties to the operator -- reading it "
                      "and staying silent is the R51 defect one step along.")
    if reported:
        return FAIL, ("mentioned the declared property without calling "
                      "get_system_state after the sign-off: that is an inferred "
                      "state, which D5 forbids.")
    return FAIL, (f"after the sign-off {text.strip()!r} it neither called "
                  f"get_system_state nor reported illumination state.")


def limb_a_controlled(messages: list[dict],
                      control: list[dict] | None) -> tuple[str, str]:
    """Limb A, with the pre-fix arm as its control.

    `CLAUDE.md`: "a limb that *cannot fail* is not a criterion, so carry a
    control that fires." The control arm is the same two operator messages run
    on `main`, whose prompt has none of D5's added text. If that arm reports the
    illumination state too, this limb does not discriminate -- the behaviour is
    present but D5's line is not shown to have caused it -- and that is NOT
    EXERCISED, not a pass.
    """
    verdict, detail = limb_a(messages)
    if control is None:
        return verdict, detail + (
            "  [no control arm supplied: run the pre-fix session too (--control) "
            "or this limb cannot show that it is able to fail]")
    control_verdict, control_detail = limb_a(control)
    if verdict != PASS:
        return verdict, detail + f"  [control arm: {control_verdict}]"
    if control_verdict == PASS:
        return NOT_EXERCISED, (
            "BOTH arms reported the illumination state. The branch behaves "
            "correctly, but the pre-fix prompt did too, so this limb does not "
            "discriminate and measures nothing about D5's added line. Not a "
            f"pass. Control arm said: {control_detail}")
    if control_verdict == NOT_EXERCISED:
        return NOT_EXERCISED, (
            f"the branch arm passed, but the control arm could not run its "
            f"mechanism, so the comparison is void: {control_detail}")
    return PASS, (
        detail + "  [control arm on the pre-fix prompt did NOT report it: "
        f"{control_detail}  -- so the limb is able to fail. Note n=1 per arm: "
        "this shows the limb discriminates, not how large the effect is.]")


def limb_b(messages: list[dict]) -> tuple[str, str]:
    names = {}
    for m in messages:
        for b in blocks(m):
            if b.get("type") == "tool_use":
                names[b.get("id")] = b.get("name")
    seen = []
    for m in messages:
        for b in blocks(m):
            if b.get("type") != "tool_result":
                continue
            if names.get(b.get("tool_use_id")) != "set_device_property":
                continue
            try:
                payload = json.loads(result_text(b))
            except Exception:
                seen.append(("unparseable", result_text(b)[:120]))
                continue
            seen.append(("parsed", payload))
    if not seen:
        return NOT_EXERCISED, ("this session made no set_device_property call, so an "
                              "ordinary successful write was never observed.")
    for kind, payload in seen:
        if kind != "parsed":
            return FAIL, f"a set_device_property result did not parse as JSON: {payload!r}"
        if "error" in payload:
            continue  # a refused or failed write is not this limb's subject
        if sorted(payload) != ["status"]:
            return FAIL, (f"a successful write reported extra fields {sorted(payload)}; "
                          "today it reports status alone, and this limb exists to catch "
                          "a read-back leaking into the success path.")
        if not str(payload["status"]).startswith("Set "):
            return FAIL, f"a successful write's status changed wording: {payload['status']!r}"
    successes = [p for k, p in seen if k == "parsed" and "error" not in p]
    if not successes:
        return NOT_EXERCISED, (f"{len(seen)} set_device_property call(s), all of which "
                               "errored or were refused; no ordinary success to score.")
    return PASS, (f"{len(successes)} successful set_device_property call(s), each "
                  "reporting status alone and unchanged in wording.")


def limb_c() -> tuple[str, str]:
    return NOT_EXERCISED, (
        "the landed-then-raised write cannot be produced on this machine: MMCore "
        "rejects an illegal value before dispatch and applies a legal one. Closed "
        "off-rig by tests 1-14 and by the two M5 observations (design/38 G7.a, "
        "Block 2 G4). This is not a pass and must never be recorded as one.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("history", type=Path)
    ap.add_argument("--control", type=Path, default=None,
                    help="history JSONL from the same two operator messages run "
                         "on the PRE-FIX prompt (main). Without it, limb A cannot "
                         "show that it is able to fail.")
    ap.add_argument("--log", type=Path, default=Path("block72a-score.log"))
    args = ap.parse_args()

    lines: list[str] = []

    def say(text: str = "") -> None:
        lines.append(text)
        print(text)

    if not args.history.exists():
        say(f"FAIL  history file not found: {args.history}")
        args.log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 2
    messages = load(args.history)
    say(f"BLOCK 72a DEMO GATE -- {args.history} ({len(messages)} messages)")
    control = None
    if args.control is not None:
        if not args.control.exists():
            say(f"FAIL  control history not found: {args.control}")
            args.log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return 2
        control = load(args.control)
        say(f"control arm (pre-fix prompt) -- {args.control} "
            f"({len(control)} messages)")
    say()

    # Each limb is computed independently: one FAIL must not hide the others.
    results = [("A (R51 session-end illumination read)",
                *limb_a_controlled(messages, control)),
               ("B (ordinary successful write unchanged)", *limb_b(messages)),
               ("C (landed-then-raised)", *limb_c())]
    for name, verdict, detail in results:
        say(f"{verdict:14s} limb {name}")
        say(f"               {detail}")
        say()

    failed = [n for n, v, _ in results if v == FAIL]
    unexercised = [n for n, v, _ in results if v == NOT_EXERCISED]
    say(f"FAILED: {failed or 'none'}")
    say(f"NOT EXERCISED: {unexercised or 'none'} -- never scored as passes")
    args.log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {args.log}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
