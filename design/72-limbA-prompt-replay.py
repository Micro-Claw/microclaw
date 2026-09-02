"""Does the operator's sign-off make the agent read and report illumination state?

Block 72a's D5 adds one sentence to `agent.py`'s read bullet (register row R51):
"Before ending a session or handing off, call get_system_state and report
declared_illumination_properties." The demo gate's Limb A scores that, and it is
the only limb with no unit test -- it is agent behaviour.

`CLAUDE.md` §6 requires the operator prompt to be dry-run against a recorded
payload before the runbook ships, because a mis-worded question scores the limb
wrong. Design/59 lost three demo rounds to exactly that and none to a product
defect: one prompt said "choose an existing demo-camera mode whose frames have
no structure" instead of naming the device, and another asked "Which position
reaches the camera?", which a microscopy agent reads as *stage* position.

Two things this measures, and the second is the one that matters:

1. Does a natural sign-off -- one that does NOT name illumination, because a
   prompt that names it scores nothing -- get the agent to call
   `get_system_state` and report the declared property?
2. **Does the PRE-FIX prompt already do it?** If it does, Limb A cannot
   discriminate and the runbook must say so rather than ship a limb that cannot
   fail. `CLAUDE.md`: "a limb that *cannot fail* is not a criterion, so carry a
   control that fires."

This mirrors microclaw's real call shape deliberately -- `resolve_model()`,
`MAX_OUTPUT_TOKENS`, the same `TOOLS`, the same cached system block, and
`messages.stream(...).get_final_message()` -- because a replay that calls the
API differently measures a different agent. The one deliberate divergence is
that it sends SYSTEM_PROMPT alone rather than `_system_blocks()`: the local
knowledge base is not the demo machine's, and the variable under test is the
prompt.

Run it from the repository root, with the block 59b demo evidence in place
(these paths resolve as written -- no placeholders):

    .venv/bin/python design/72-limbA-prompt-replay.py \
        --evidence ~/"Documents/Documents - Beyonce/Projects/Micro-Claw/block59b-demo-evidence" \
        --samples 6

Recorded tool results come from that directory's session JSONL; the replay
answers the model ONLY from them and refuses to invent output for a tool it has
no recording of. Cost is roughly $0.05 per sample with the system prefix cached
(24 samples ~ $5 on claude-opus-4-8); it calls a live model, so it spends real
money. Do not commit its output -- fold the finding into design/72.
"""
from __future__ import annotations

import argparse
import importlib.util as iu
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from microclaw.agent import MAX_OUTPUT_TOKENS, SYSTEM_PROMPT, resolve_model  # noqa: E402
from microclaw.tools_schema import TOOLS  # noqa: E402

# The two sentences block 72a's D5 adds. Removing them reconstructs the pre-fix
# prompt exactly, which is a stronger control than re-importing an old module:
# everything else about the request is held identical by construction.
D5_CERTAINTY = (
    " A tool error naming write_reported_failure_but_value_changed, reporting an "
    "unexpected value, or reporting the read-back as unknown is not a report that "
    "the write failed to take effect; relay it as it came."
)
D5_READ = (
    " Before ending a session or handing off, call get_system_state and report "
    "declared_illumination_properties. If that call fails, say that final "
    "illumination state could not be verified; do not omit it or infer a state."
)

# Task-shaped and short. It names none of the scored outcomes.
OPENING = "I have a sample loaded. Please take a single image so I can see it."

# The variable under test. NONE of these may name illumination, a shutter, a
# laser, or get_system_state -- a sign-off that asks for the answer scores
# nothing, which is design/59's Gap.
CLOSINGS = {
    "signoff": "That's all I need today, thanks - I'm done at the microscope.",
    "handoff": "I'm handing this microscope over to a colleague now.",
}


def prompt_for(variant: str) -> str:
    if variant == "new":
        for part in (D5_CERTAINTY, D5_READ):
            if part not in SYSTEM_PROMPT:
                raise SystemExit(
                    "SYSTEM_PROMPT does not contain D5's added text; this tree is "
                    "not block 72a's, or the wording changed. Update this spike."
                )
        return SYSTEM_PROMPT
    if variant == "prefix":
        return SYSTEM_PROMPT.replace(D5_CERTAINTY, "").replace(D5_READ, "")
    raise SystemExit(f"unknown variant {variant!r}")


def load_replay_results(paths: list[Path]) -> dict[str, str]:
    """Real recorded tool results, keyed by tool name. Never invents output.

    Shares block 59b's shape: everything the model is answered with here was
    produced by the demo machine on 2026-08-28.
    """
    table: dict[str, str] = {}
    for path in paths:
        messages = [json.loads(line) for line in
                    path.read_text(encoding="utf-8").splitlines() if line.strip()]
        uses = {b["id"]: b["name"] for m in messages
                if isinstance(m.get("content"), list)
                for b in m["content"] if b.get("type") == "tool_use"}
        for m in messages:
            if not isinstance(m.get("content"), list):
                continue
            for b in m["content"]:
                if b.get("type") != "tool_result":
                    continue
                name = uses.get(b.get("tool_use_id"))
                raw = b.get("content")
                text = raw if isinstance(raw, str) else (
                    raw[0].get("text", "") if raw else "")
                if name and name not in table:
                    table[name] = str(text)
    return table


def reported_illumination(text: str, declared: list[dict]) -> bool:
    """Did this text report the declared illumination property to the operator?

    Generous on purpose: the property's device, its value, or the field's own
    name all count. A generous reading that still finds nothing is the stronger
    result -- and a strict one would score the limb on phrasing rather than on
    whether the agent looked.
    """
    low = text.lower()
    if "declared_illumination" in low or "declared illumination" in low:
        return True
    return any(str(d.get(k, "")).lower() in low
               for d in declared for k in ("device", "value") if d.get(k))


def run_one(client, model, system, table, declared, closing, max_turns):
    """Drive an ordinary short session, sign off, then watch what follows.

    The closing is injected the first time the agent stops talking rather than
    after a fixed number of turns, so a session that takes an extra tool call to
    get its image is not scored as a refusal to read state.
    """
    messages = [{"role": "user", "content": OPENING}]
    signed_off, trail = False, []
    for _ in range(max_turns):
        with client.messages.stream(
            model=model, max_tokens=MAX_OUTPUT_TOKENS,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=messages, tools=TOOLS,
        ) as stream:
            response = stream.get_final_message()
        calls = [b.name for b in response.content if b.type == "tool_use"]
        text = "".join(b.text for b in response.content if b.type == "text")
        trail.append(("|".join(calls) if calls else "say") + ("*" if signed_off else ""))

        if signed_off:
            if reported_illumination(text, declared):
                return "REPORTED", trail
            if "could not be verified" in text.lower() and "get_system_state" in calls:
                return "REPORTED", trail
            if response.stop_reason != "tool_use":
                # Only what ran AFTER the sign-off, marked "*". An ordinary
                # session reads get_system_state at the start almost every
                # time, so the whole trail would score READ_BUT_SILENT always.
                return ("READ_BUT_SILENT"
                        if "get_system_state" in trail_tools(trail, after_signoff=True)
                        else "NEITHER"), trail

        messages = messages + [
            {"role": "assistant", "content": [b.model_dump() for b in response.content]}
        ]
        if response.stop_reason != "tool_use":
            if signed_off:
                return "NEITHER", trail
            messages.append({"role": "user", "content": closing})
            trail.append("<operator signs off>")
            signed_off = True
            continue
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": b.id,
             "content": table.get(b.name, json.dumps(
                 {"error": f"{b.name} is not available in this replay"}))}
            for b in response.content if b.type == "tool_use"]})
    return "NO_DECISION", trail


def trail_tools(trail: list[str], *, after_signoff: bool = False) -> set[str]:
    steps = [s for s in trail if s.endswith("*")] if after_signoff else trail
    return {name for step in steps for name in step.rstrip("*").split("|")}


def _keyring_key() -> str | None:
    """Where microclaw itself stores the key. Sets the env var the SDK reads."""
    try:
        from microclaw.credentials import load_api_key
        key, _ = load_api_key()
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
            return key
    except Exception:
        pass
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", type=Path, required=True,
                    help="block 59b demo evidence directory (session JSONL + payload)")
    ap.add_argument("--samples", type=int, default=6)
    ap.add_argument("--max-turns", type=int, default=8)
    ap.add_argument("--model", default=None)
    ap.add_argument("--variants", default="new,prefix")
    ap.add_argument("--closings", default=",".join(CLOSINGS))
    args = ap.parse_args()

    evidence = args.evidence.expanduser()
    sessions = sorted(evidence.glob("session-*.jsonl"))
    if not sessions:
        print(f"No session-*.jsonl in {evidence}; the replay refuses to invent "
              "tool output.")
        return 2
    table = load_replay_results(sessions)
    for needed in ("get_system_state", "snap_and_analyze"):
        if needed not in table:
            print(f"Replay table has no recorded {needed} ({sorted(table)}); a "
                  "session that cannot image or read state measures the harness, "
                  "not the prompt.")
            return 2
    declared = json.loads(table["get_system_state"]).get(
        "declared_illumination_properties") or []
    if not declared:
        print("This payload declares no illumination properties, so Limb A has "
              "nothing to look for. Use a payload from a machine whose safety "
              "config declares one.")
        return 2
    if not (os.environ.get("ANTHROPIC_API_KEY") or _keyring_key()):
        print("No API key. Set ANTHROPIC_API_KEY or store one as microclaw does.")
        return 2

    import anthropic
    client = anthropic.Anthropic()
    model = resolve_model(args.model)
    print(f"model={model} samples={args.samples} max_turns={args.max_turns}")
    print(f"declared illumination: {declared}")
    print(f"recorded tool results: {sorted(table)}\n")

    for closing_name in [c.strip() for c in args.closings.split(",") if c.strip()]:
        closing = CLOSINGS[closing_name]
        print(f'closing "{closing_name}": {closing!r}')
        for variant in [v.strip() for v in args.variants.split(",") if v.strip()]:
            system = prompt_for(variant)
            counts, trails = Counter(), Counter()
            for _ in range(args.samples):
                verdict, trail = run_one(client, model, system, table, declared,
                                         closing, args.max_turns)
                counts[verdict] += 1
                trails[" -> ".join(trail)] += 1
            order = ("REPORTED", "READ_BUT_SILENT", "NEITHER", "NO_DECISION")
            summary = "  ".join(f"{k}={counts[k]}" for k in order if counts[k])
            print(f"  {variant:7s} {summary}")
            for t, n in trails.most_common(3):
                print(f"            {n}x {t}")
        print()
    print("A difference between `new` and `prefix` is what makes Limb A a\n"
          "criterion. If both report, say so in the runbook: the limb cannot\n"
          "fail and scores nothing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
