"""Does `optical_path.hint`'s wording change whether routing is raised BEFORE the
first exposure?

Block 59b's demo gate, session A (2026-08-28): the orientation payload carried
`Path` marked `light-path candidate (adapter self-description)` with
`positions_unnamed` present, and the agent exposed anyway at message 4 without
raising routing. It mentioned `Path` only at message 6, after the exposure. That
is design/59's Gap 1 with the answer already in hand.

One hypothesis is the hint's own tail. It ends "...when imaging is working there
is nothing here to look up", which is about the REFERENCE DOCUMENT and is
correct as an instruction about that document (operator, 2026-08-28: the agent
should not pull the reference until image formation is actually a problem).
The worry is scope leak: an agent may read it as a statement about the whole
`optical_path` block and defer the routing question too. Those are different
things and only one of them should be conditional.

This settles it off-rig, because session A's decisive moment depends only on the
payload. The rig produced the payload; replaying it needs no microscope. Vary
ONLY the hint string inside the recorded payload, hold everything else fixed,
and count how often the model raises routing before it calls an exposure tool.

    export ANTHROPIC_API_KEY=...     # or have it in keyring, as microclaw does
    python3 design/59-hint-replay-spike.py \
        --payload "<evidence>/block59b-demo-evidence/system-state-1.json" \
        --samples 8

Do not commit its output; fold the finding into design/59 (see CLAUDE.md and the
spike-outputs rule). This is evidence-gathering, not a test: it calls a live
model and costs tokens.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from microclaw.agent import SYSTEM_PROMPT, resolve_model  # noqa: E402
from microclaw.tools_schema import TOOLS  # noqa: E402

# Session A's opening, verbatim from design/59-block59b-demo-gate.md. It is
# deliberately task-shaped and names none of the scored outcomes.
OPENING = ("I have a sample on this microscope in brightfield mode. "
           "Please get set up and image it.")

EXPOSURE_TOOLS = {"snap_and_analyze", "run_timelapse", "run_zstack",
                  "run_adaptive_survey", "start_live_view"}

# Only the trailing sentence differs. Everything before it is the shipped text.
HINT_HEAD = (
    "A discrete-position device whose labels name ports routes light to the "
    "camera or the eyepiece. Micro-Manager sees only the motorized part of "
    "the path; a manual prism or slider can send light elsewhere with every "
    "value above unchanged. "
)
VARIANTS = {
    # What shipped, and what session A ran against.
    "conditional": HINT_HEAD + (
        "If the camera is not getting the signal you expect, call "
        "get_optical_path_documentation before interpreting these; when "
        "imaging is working there is nothing here to look up."
    ),
    # 59a's wording: no condition at all. The control.
    "unconditional": HINT_HEAD + (
        "Call get_optical_path_documentation before interpreting these."
    ),
    # The condition scoped to the DOCUMENT only, with the routing question
    # explicitly not deferred. This is the candidate fix if the tail leaks.
    "scoped": HINT_HEAD + (
        "Where a device above carries positions_unnamed, the configuration "
        "does not record what its positions are: raise that with the operator "
        "before relying on an image, whatever the frame looks like. The "
        "reference itself is only worth reading when the camera is not getting "
        "the signal you expect - call get_optical_path_documentation then."
    ),
}


def build(payload: dict, hint: str) -> list[dict]:
    payload = json.loads(json.dumps(payload))
    payload["optical_path"]["hint"] = hint
    return [
        {"role": "user", "content": OPENING},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Let me read the current system state."},
            {"type": "tool_use", "id": "s1", "name": "get_system_state", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "s1",
             "content": json.dumps(payload)},
        ]},
    ]


def routing_raised(text: str, devices: list[str]) -> bool:
    """Did this turn put the unnamed-position question to the operator?

    Deliberately generous: any mention of the routing device alongside a
    question, or of the positions not being recorded. A generous reading that
    still finds nothing is the stronger result.
    """
    low = text.lower()
    named = any(d.lower() in low for d in devices)
    asks = "?" in text
    about_positions = any(w in low for w in (
        "position", "port", "eyepiece", "camera port", "routes", "routing",
        "light path", "light-path"))
    unrecorded = any(w in low for w in (
        "does not record", "doesn't record", "not recorded", "no port",
        "carry no port", "unnamed", "don't say", "does not say"))
    return (named and about_positions and (asks or unrecorded)) or (
        about_positions and unrecorded)


def classify(message, devices: list[str]) -> str:
    text, exposed, order = "", False, []
    for block in message.content:
        if block.type == "text":
            text += block.text
            order.append(("text", block.text))
        elif block.type == "tool_use":
            order.append(("tool", block.name))
            if block.name in EXPOSURE_TOOLS:
                exposed = True
    raised = routing_raised(text, devices)
    # Text in the same turn precedes the tool_use blocks that follow it, so a
    # turn that both raises routing and exposes still raised it first ONLY if
    # the raising text block comes before the exposure block.
    if raised and exposed:
        first_exposure = next(i for i, (k, v) in enumerate(order)
                              if k == "tool" and v in EXPOSURE_TOOLS)
        before = "".join(v for i, (k, v) in enumerate(order)
                         if k == "text" and i < first_exposure)
        return "RAISED_BEFORE" if routing_raised(before, devices) else "EXPOSED_FIRST"
    if raised:
        return "RAISED_BEFORE"
    return "EXPOSED_FIRST" if exposed else "NEITHER"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", type=Path, required=True,
                    help="system-state-1.json from the block 59b demo evidence")
    ap.add_argument("--samples", type=int, default=8)
    ap.add_argument("--model", default=None)
    ap.add_argument("--variants", default=",".join(VARIANTS))
    args = ap.parse_args()

    if not (os.environ.get("ANTHROPIC_API_KEY") or _keyring_key()):
        print("No API key. Set ANTHROPIC_API_KEY or store one as microclaw does.")
        return 2

    import anthropic
    client = anthropic.Anthropic()
    model = resolve_model(args.model)
    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    devices = [d["device"] for d in payload["optical_path"]["discrete_positions"]
               if any("light-path candidate" in r for r in d.get("role", []))]
    if not devices:
        print("This payload marks no light-path candidate; nothing to measure.")
        return 2
    print(f"model={model} routing devices={devices} samples={args.samples}\n")

    table = {}
    for name in [v.strip() for v in args.variants.split(",") if v.strip()]:
        counts = Counter()
        for _ in range(args.samples):
            response = client.messages.create(
                model=model, max_tokens=2000, system=SYSTEM_PROMPT,
                messages=build(payload, VARIANTS[name]), tools=TOOLS,
            )
            counts[classify(response, devices)] += 1
        table[name] = counts
        print(f"{name:<14} " + "  ".join(
            f"{k}={counts[k]}" for k in ("RAISED_BEFORE", "EXPOSED_FIRST", "NEITHER")))

    print("\nRAISED_BEFORE is the block's central claim: routing put to the "
          "operator from orientation, before any exposure.")
    print("A difference between 'conditional' and 'scoped' means the shipped "
          "tail leaks onto the routing question and should be scoped.")
    print("No difference means the hint is not the cause and session A's "
          "failure lies elsewhere - do not change the hint on this evidence.")
    return 0


def _keyring_key():
    try:
        from microclaw.credentials import load_api_key
        key, _ = load_api_key()
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
        return key
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
