"""Measure whether a live model loads the SMLM skill before proposing parameters.

This is evidence-gathering, not a test. It calls a live model, costs API tokens,
and its output must not be committed. Fold the measured finding into design/61.

The model's decision to call ``load_skill`` is the behaviour under study. A
suite mock cannot establish it because scripting that tool call supplies the
decision by construction. This spike therefore presents an operator-worded
opening to the real ``SYSTEM_PROMPT`` and ``TOOLS``, loops until the model loads
a skill or proposes acquisition parameters, and prints every trajectory. A run
that reaches neither outcome measured nothing; it is not a negative result.

Run from the repository root when the coordinator chooses to spend API credit:

    python3 design/61-skill-routing-spike.py --samples 8
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


OPENING = "I need to run dSTORM on this sample — what acquisition parameters should I use?"

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


def classify_blocks(blocks) -> tuple[str | None, list[str]]:
    """Score block order within a turn as well as order between turns."""
    trail: list[str] = []
    for block in blocks:
        if block.type == "text":
            trail.append("say")
            if proposes_parameters(block.text):
                return "PROPOSED_FIRST", trail
        elif block.type == "tool_use":
            name = block.name
            skill_name = block.input.get("name") if isinstance(block.input, dict) else None
            trail.append(f"{name}({skill_name})" if name == "load_skill" else name)
            if name == "load_skill":
                return (
                    "LOADED_BEFORE" if skill_name == "smlm" else "WRONG_SKILL",
                    trail,
                )
    return None, trail


def tool_result(block) -> str:
    """Answer only the knowledge read; hardware calls have no fixture here."""
    if block.name == "load_skill":
        name = block.input.get("name") if isinstance(block.input, dict) else None
        try:
            return json.dumps({"name": name, "documentation": load_skill_text(name)})
        except (TypeError, ValueError) as exc:
            return json.dumps({"error": str(exc)})
    return json.dumps({
        "error": f"{block.name} has no microscope result in this routing spike"
    })


def run_one(client, model: str, max_turns: int) -> tuple[str, list[str]]:
    messages = [{"role": "user", "content": OPENING}]
    trajectory: list[str] = []
    for _ in range(max_turns):
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=TOOLS,
        )
        verdict, turn_trail = classify_blocks(response.content)
        trajectory.append("+".join(turn_trail) if turn_trail else "empty")
        if verdict is not None:
            return verdict, trajectory
        if response.stop_reason != "tool_use":
            return "NO_DECISION", trajectory
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
    return "NO_DECISION", trajectory


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
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()
    if args.samples < 1 or args.max_turns < 1:
        parser.error("--samples and --max-turns must be positive")
    if not (os.environ.get("ANTHROPIC_API_KEY") or _keyring_key()):
        print("No API key. Set ANTHROPIC_API_KEY or store one as microclaw does.")
        return 2

    import anthropic
    model = resolve_model(args.model)
    client = anthropic.Anthropic()
    counts: Counter[str] = Counter()
    trails: Counter[str] = Counter()
    print(f"model={model} samples={args.samples} max_turns={args.max_turns}")
    print(f"opening={OPENING!r}\n")
    for sample in range(1, args.samples + 1):
        verdict, trajectory = run_one(client, model, args.max_turns)
        trail = " -> ".join(trajectory)
        counts[verdict] += 1
        trails[trail] += 1
        print(f"sample {sample}/{args.samples}: {verdict}: {trail}")

    print()
    for verdict in ("LOADED_BEFORE", "PROPOSED_FIRST", "WRONG_SKILL", "NO_DECISION"):
        print(f"{verdict}: {counts[verdict]}/{args.samples}")
    decided = args.samples - counts["NO_DECISION"]
    print(f"decisions measured: {decided}/{args.samples}")
    print(f"SMLM loaded before parameters: {counts['LOADED_BEFORE']}/{args.samples}")
    for trail, count in trails.most_common():
        print(f"trajectory {count}/{args.samples}: {trail}")
    if counts["NO_DECISION"]:
        print("NO_DECISION measured nothing for those samples; do not read them "
              "as evidence for or against routing. Increase --max-turns if needed.")
    print("Do not commit this output; fold the measured finding into design/61.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
