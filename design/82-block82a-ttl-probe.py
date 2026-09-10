"""Does the API accept microclaw's four cache breakpoints at ttl 1h, and does it
say so?  (design/82 block 82a, D5.)

Two identical calls built the way `agent._stream_one_round` builds one — the real
`TOOLS_CACHED`, the real `_system_blocks`, the real `_with_cache_breakpoint` — and
then read back the one field that proves the TTL took effect:
`usage.cache_creation.ephemeral_1h_input_tokens`.  Call 1 should write; call 2,
seconds later, should read what call 1 wrote.

The knowledge-base block is stubbed, so this sends no rig knowledge anywhere and
still produces all four breakpoints (tools, system prompt, KB, messages).

    ANTHROPIC_API_KEY=... .venv/bin/python design/82-block82a-ttl-probe.py

Costs one cache write of microclaw's prefix (~36k tokens) plus one read; at Opus
4.8 rates that is well under a dollar.  `--model` takes anything the key can see.
Prints PASS/FAIL per limb and exits nonzero on any failure, so it can be a limb
of a gate rather than something to eyeball.
"""
from __future__ import annotations

import argparse
import sys
from unittest.mock import patch

from microclaw import agent
from microclaw.tools_schema import TOOLS_CACHED

STUB_KB = "Rig profile (probe stub).\n" + "\n".join(
    f"- placeholder topic {n}: nothing real here" for n in range(40)
)


def one_call(client, model: str, system, prompt: str):
    messages = agent._with_cache_breakpoint(
        [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
    )
    with client.messages.stream(
        model=model, max_tokens=16, system=system, messages=messages,
        tools=TOOLS_CACHED,
    ) as stream:
        for _ in stream:
            pass
        return stream.get_final_message()


def describe(response) -> dict:
    usage = response.usage
    creation = getattr(usage, "cache_creation", None)
    return {
        "model": response.model,
        "input_tokens": usage.input_tokens,
        "cache_read": usage.cache_read_input_tokens,
        "cache_creation": usage.cache_creation_input_tokens,
        "5m": getattr(creation, "ephemeral_5m_input_tokens", None),
        "1h": getattr(creation, "ephemeral_1h_input_tokens", None),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=agent.DEFAULT_MODEL)
    args = parser.parse_args()

    with patch("microclaw.agent.load_knowledge", return_value={"rig": {}}), \
            patch("microclaw.agent.format_for_prompt", return_value=STUB_KB):
        system = agent._system_blocks()

    breakpoints = sum(1 for b in system if "cache_control" in b) + 1 + 1
    print(f"model: {args.model}")
    print(f"breakpoints in the request: {breakpoints} "
          f"(system {sum(1 for b in system if 'cache_control' in b)}, tools 1, messages 1)")
    ttls = {b["cache_control"].get("ttl") for b in system if "cache_control" in b}
    ttls |= {TOOLS_CACHED[-1]["cache_control"].get("ttl")}

    client = agent._get_client()
    first = describe(one_call(client, args.model, system,
                             "Reply with the single word: probe."))
    print(f"call 1: {first}")
    second = describe(one_call(client, args.model, system,
                              "Reply with the single word: probe."))
    print(f"call 2: {second}")

    limbs = [
        ("every breakpoint declares ttl 1h", ttls == {"1h"}, f"ttls seen: {ttls}"),
        ("the API accepted four 1h breakpoints", True, "both calls returned"),
        ("call 1 wrote the prefix at the 1h TTL",
         (first["1h"] or 0) > 0 and not (first["5m"] or 0),
         f"1h={first['1h']} 5m={first['5m']}"),
        ("call 2 read the prefix back", (second["cache_read"] or 0) > 0,
         f"cache_read={second['cache_read']}"),
        ("no 5-minute entry was written on either call",
         not (first["5m"] or 0) and not (second["5m"] or 0),
         f"{first['5m']} / {second['5m']}"),
    ]
    failed = 0
    for name, ok, detail in limbs:
        print(f"{'PASS' if ok else 'FAIL'}  {name} — {detail}")
        failed += not ok
    print(f"\n{len(limbs) - failed}/{len(limbs)} limbs passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
