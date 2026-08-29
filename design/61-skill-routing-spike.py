"""Measure whether a live model loads the SMLM skill before proposing parameters.

This is evidence-gathering, not a test. It calls a live model, costs API tokens,
and its output must not be committed. Fold the measured finding into design/61.

The model's decision to call ``load_skill`` is the behaviour under study. A
suite mock cannot establish it because scripting that tool call supplies the
decision by construction. This spike therefore presents an operator-worded
opening to the real ``SYSTEM_PROMPT`` and ``TOOLS``, loops until the model loads
a skill or proposes acquisition parameters, and prints every trajectory. A run
that reaches neither outcome measured nothing; it is not a negative result.

Two arms, because block 61a's demo round raised a wording hypothesis it could
not settle. Arm A is the routing rule as shipped -- "Load the relevant skill
before running its specialized workflow" -- which fires on *running* a workflow.
Arm B is a candidate replacement that is trigger-worded, the way the SMLM
paragraph 61a deleted used to be: it enumerates the words an operator actually
says. **Arm B is a candidate, not shipped**, and nothing here changes the
product; a measurement is what buys that change, per the design.

`get_system_state` is answered from `61-block61a-system-state.json`, which is
the demo machine's OWN recorded result from the 2026-08-29 session, not an
invented rig. That is deliberate and it matters: in that session the agent read
this exact payload, saw the demo camera, and answered that the rig cannot do
dSTORM instead of routing anywhere. Holding it fixed across both arms holds the
confound fixed too, so a difference between arms is attributable to the wording
rather than to the rig. It also asks the question the session actually posed --
would trigger-wording have reached the skill even here?

A run therefore measures the wording effect UNDER that confound. It does not
measure how often routing works on a capable SMLM rig, and must not be reported
as if it did.

Sizing: design/59 measured two runs of the SAME wording at 5/8 then 15/16, so
eight samples cannot separate a wording effect from noise. The default is 16 per
arm and even that is modest; report both numerators and denominators, always.

Run from the repository root when the coordinator chooses to spend API credit:

    python3 design/61-skill-routing-spike.py --arm both --samples 16

`--dry-run` prints both system prompts' routing lines and makes no API call.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from microclaw.agent import SYSTEM_PROMPT, resolve_model  # noqa: E402
from microclaw.skills import load_skill_text  # noqa: E402
from microclaw.tools_schema import TOOLS  # noqa: E402


# Verbatim from the 2026-08-29 demo session (design/61 gate round 1).
OPENING = "I need to run dSTORM on this sample. What acquisition parameters should I use?"

SHIPPED_RULE = "Load the relevant skill before running its specialized workflow.\n"

# Candidate wording only. Modelled on the paragraph 61a deleted, which named the
# operator's own words and said "first".
TRIGGER_RULE = (
    "Load the relevant skill before running its specialized workflow. When the "
    "user asks about a task a skill covers - SMLM, super-resolution, dSTORM, "
    "PALM, PAINT, DNA-PAINT, single-molecule localization, writing a hook, an "
    "optical path, htSMLM or EMU, or a Nikon focus lock - load that skill FIRST, "
    "before answering, even if you also have to tell them something about the "
    "rig.\n"
)

RECORDED_STATE = json.loads(
    (Path(__file__).with_name("61-block61a-system-state.json")).read_text(encoding="utf-8")
)


def system_prompt_for(arm: str) -> str:
    """Arm A is the shipped prompt; arm B swaps only the routing sentence."""
    if arm == "shipped":
        return SYSTEM_PROMPT
    if SHIPPED_RULE not in SYSTEM_PROMPT:
        raise SystemExit(
            "The shipped routing rule is not in SYSTEM_PROMPT verbatim; this "
            "spike would otherwise silently compare an arm against itself."
        )
    return SYSTEM_PROMPT.replace(SHIPPED_RULE, TRIGGER_RULE)

_PARAMETER_SUBJECT = re.compile(
    r"\b(exposure|frame count|frames?|laser power|power density|interval|tirf|channel)\b",
    re.IGNORECASE,
)
_PARAMETER_VALUE = re.compile(
    r"(?:\b\d+(?:\.\d+)?\s*(?:ms|s|frames?|%|kw/cm(?:²|2))\b|"
    r"\b(?:recommend|start with|set)\b)",
    re.IGNORECASE,
)


def proposes_parameters(text: str) -> bool:
    """Recognise an actual recommendation, not merely a request for details."""
    return bool(_PARAMETER_SUBJECT.search(text) and _PARAMETER_VALUE.search(text))


SHOW_TEXT = False
USAGE = {"input": 0, "output": 0, "calls": 0}

# Opus 4.8, checked 2026-08-29 against the claude-api skill's model table.
# The first estimate for this run used $15/$75 from memory and was 3x too high.
PRICE_IN_PER_M, PRICE_OUT_PER_M = 5.00, 25.00


def scan_turn(blocks) -> list[str]:
    """What this turn did, in order. No judgement here."""
    trail: list[str] = []
    for block in blocks:
        if block.type == "text":
            trail.append("say")
            if SHOW_TEXT:
                print("    [text] " + " ".join(block.text.split())[:600])
        elif block.type == "tool_use":
            if block.name == "load_skill":
                skill = block.input.get("name") if isinstance(block.input, dict) else None
                trail.append(f"load_skill({skill})")
            else:
                trail.append(block.name)
    return trail


def verdict_for(trail: list[str], finished: bool) -> str:
    """Did the session reach the SMLM skill before it finished answering?

    Deliberately free of any text classification. The first version of this
    spike scored a PROPOSED_FIRST verdict with a regex over the assistant's
    prose; a one-sample validation run showed it firing on a stray "TIRF" and
    "20 ms" inside a message whose actual content was a REFUSAL to give numbers
    until the real hardware was known. That metric would have measured "the
    message contained a parameter word and a number", not routing, and 32
    samples of it would have looked like data.

    The routing question is binary and needs no NLP: was load_skill(smlm)
    called before the session finished. Everything else is an annotation, and
    every trajectory is printed so a reader can check this rather than trust it.
    """
    loads = [step for step in trail if step.startswith("load_skill(")]
    if "load_skill(smlm)" in loads:
        return "LOADED_SMLM"
    if not finished:
        return "NO_DECISION"      # ran out of turns; measured nothing
    if loads:
        return "LOADED_OTHER_SKILL"
    return "NEVER_LOADED"


def tool_result(block) -> str:
    """Answer only the knowledge read; hardware calls have no fixture here."""
    if block.name == "load_skill":
        name = block.input.get("name") if isinstance(block.input, dict) else None
        try:
            return json.dumps({"name": name, "documentation": load_skill_text(name)})
        except (TypeError, ValueError) as exc:
            return json.dumps({"error": str(exc)})
    if block.name == "get_system_state":
        return json.dumps(RECORDED_STATE)
    return json.dumps({
        "error": f"{block.name} has no microscope result in this routing spike"
    })


def run_one(client, model: str, max_turns: int, system: str) -> tuple[str, list[str]]:
    """Drive one session until the model stops calling tools, then judge it.

    It runs to the END of the answer rather than stopping at the first
    interesting block: an agent that gathers state, says something, and only
    then loads the skill has still routed, and cutting the loop early would
    score that as a miss.
    """
    messages = [{"role": "user", "content": OPENING}]
    trail: list[str] = []
    for _ in range(max_turns):
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            system=system,
            messages=messages,
            tools=TOOLS,
        )
        USAGE["calls"] += 1
        USAGE["input"] += response.usage.input_tokens
        USAGE["output"] += response.usage.output_tokens
        trail += scan_turn(response.content)
        if response.stop_reason != "tool_use":
            return verdict_for(trail, finished=True), trail
        messages += [
            {"role": "assistant", "content": [b.model_dump() for b in response.content]},
            {"role": "user", "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": tool_result(block),
                }
                for block in response.content if block.type == "tool_use"
            ]},
        ]
    return verdict_for(trail, finished=False), trail


def _keyring_key():
    try:
        from microclaw.credentials import load_api_key
        key, _ = load_api_key()
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
        return key
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=16,
                        help="Per arm. design/59 saw 5/8 then 15/16 for the same "
                             "wording, so 8 cannot separate an effect from noise.")
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--model", default=None)
    parser.add_argument("--arm", choices=("shipped", "trigger", "both"), default="both")
    parser.add_argument("--show-text", action="store_true",
                        help="Print each text block and what triggered a "
                             "PROPOSED_FIRST classification. For validating the "
                             "classifier before spending a full run.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print each arm's routing line and exit. No API call.")
    args = parser.parse_args()
    if args.samples < 1 or args.max_turns < 1:
        parser.error("--samples and --max-turns must be positive")

    global SHOW_TEXT
    SHOW_TEXT = args.show_text
    arms = ("shipped", "trigger") if args.arm == "both" else (args.arm,)
    prompts = {arm: system_prompt_for(arm) for arm in arms}

    if args.dry_run:
        for arm in arms:
            text = prompts[arm]
            rule = TRIGGER_RULE if arm == "trigger" else SHIPPED_RULE
            print(f"=== arm {arm}: prompt {len(text)} chars")
            print(f"    routing line present: {rule.strip() in text}")
            print(f"    {rule.strip()}")
        if len(arms) == 2 and prompts["shipped"] == prompts["trigger"]:
            print("BOTH ARMS ARE IDENTICAL - this would compare an arm with itself")
            return 1
        print(f"\nopening: {OPENING!r}")
        print(f"get_system_state answered from the demo machine's own recording "
              f"({len(json.dumps(RECORDED_STATE))} chars)")
        print("dry run: no API call made")
        return 0

    if not (os.environ.get("ANTHROPIC_API_KEY") or _keyring_key()):
        print("No API key. Set ANTHROPIC_API_KEY or store one as microclaw does.")
        return 2

    import anthropic
    model = resolve_model(args.model)
    client = anthropic.Anthropic()
    counts = {arm: Counter() for arm in arms}
    trails = {arm: Counter() for arm in arms}
    print(f"model={model} samples={args.samples}/arm max_turns={args.max_turns}")
    print(f"opening={OPENING!r}")
    print(f"arms={list(arms)}\n")

    # Interleaved, not arm-by-arm: a provider-side change partway through a long
    # run would otherwise land entirely on one arm and read as a wording effect.
    for sample in range(1, args.samples + 1):
        for arm in arms:
            verdict, trajectory = run_one(client, model, args.max_turns, prompts[arm])
            trail = " -> ".join(trajectory)
            counts[arm][verdict] += 1
            trails[arm][trail] += 1
            print(f"sample {sample}/{args.samples} [{arm}]: {verdict}: {trail}")

    print()
    for arm in arms:
        decided = args.samples - counts[arm]["NO_DECISION"]
        print(f"--- arm {arm} (n={args.samples})")
        for verdict in ("LOADED_SMLM", "NEVER_LOADED", "LOADED_OTHER_SKILL", "NO_DECISION"):
            print(f"    {verdict}: {counts[arm][verdict]}/{args.samples}")
        print(f"    decisions measured: {decided}/{args.samples}")
        print(f"    SMLM skill reached: "
              f"{counts[arm]['LOADED_SMLM']}/{args.samples}")
        for trail, count in trails[arm].most_common(4):
            print(f"    trajectory {count}/{args.samples}: {trail}")
        if counts[arm]["NO_DECISION"]:
            print(f"    NOTE: {counts[arm]['NO_DECISION']} sample(s) measured "
                  f"NOTHING; they are not evidence either way.")

    if len(arms) == 2:
        a = counts["shipped"]["LOADED_SMLM"]
        b = counts["trigger"]["LOADED_SMLM"]
        print(f"\nshipped {a}/{args.samples} vs trigger {b}/{args.samples} "
              f"(difference {b - a:+d})")
        print("Both numerators are over the SAME recorded demo-rig payload, so "
              "the rig confound is held fixed and does not explain a difference "
              "between arms. It does mean neither number is a routing rate for a "
              "capable SMLM rig.")
        print(f"n={args.samples} per arm. design/59 measured the SAME wording at "
              f"5/8 then 15/16 across two runs; do not read a modest difference "
              f"as an effect.")
    cost = (USAGE["input"] * PRICE_IN_PER_M
            + USAGE["output"] * PRICE_OUT_PER_M) / 1_000_000
    print(f"\nmeasured spend: {USAGE['calls']} API calls, "
          f"{USAGE['input']:,} input + {USAGE['output']:,} output tokens "
          f"= ${cost:.2f} at ${PRICE_IN_PER_M}/${PRICE_OUT_PER_M} per M (Opus 4.8)")
    print("Do not commit this output; fold the measured finding into design/61.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
