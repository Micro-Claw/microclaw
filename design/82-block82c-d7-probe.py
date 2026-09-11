"""What would D7's append-only checkpoint actually cost? (design/82, block 82c.)

D7 proposes that `ConversationStore.model_messages` stop rebuilding its
checkpoint from the whole elided history and instead append one block per
compaction, leaving earlier blocks byte-identical so the cache prefix survives.
The notebook called it "nearly free" if one thing were acceptable: the
`[-200:]` / `[-100:]` caps stop being global, so artifact and hash
de-duplication happens within a segment rather than across the session.

This probe answers both halves against a real archived session, by building the
checkpoints both ways with the **real** `_checkpoint` and the **real** cut
points that `ConversationStore` chose.

    python3 design/82-block82c-d7-probe.py <a *_microclaw_history.jsonl>

Measured on the 404-call session of 2026-09-08 (810 messages, 29 compactions):

    current:  ONE block,    86,201 bytes =  40,060 est tokens
    D7:        29 blocks,  195,325 bytes =  90,870 est tokens
                          +109,124 bytes = +50,810 est tokens, on every call

    actions carried:   current 200 of 367      D7 367 of 367
    hashes:            current 200 of 204      D7 204 of 204
    decisions:                  95 of  95          95 of  95
    artifacts:                  37 of  37          37 of  37

So the question the notebook asked was the wrong one. **The caps are not the
problem** — no segment came near one (the largest held 69 actions against a
limit of 200) and no artifact appeared in two segments, so D7 would carry *more*
provenance than today, not less: today's single block silently drops 167
actions.

**The size is the problem, and it is structural.** Each segment repeats the
contract text, its own digest and its own totals, and the segments together are
uncapped. You cannot fix that by capping them, because trimming an older segment
changes its bytes and destroys exactly the cache stability D7 exists to buy: an
immutable prefix cannot be globally bounded. Against that, 82a measured that a
compaction already keeps the ~50k static tools+system head, and 82b cut the peak
context from 208k to 134k — so D7 is paying ~50k tokens a call to save a rewrite
that is smaller and cheaper than F6 claimed.

**Decision: D7 is dropped** (2026-09-11). This probe is the evidence; re-run it
on another long session if anyone wants to reopen it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from microclaw.conversation import AuditLog, ConversationStore, _checkpoint, estimate_tokens

CAPPED = [("completed_actions_in_earlier_turns", "actions"),
          ("hashes_from_earlier_turns", "hashes"),
          ("user_decisions_in_earlier_turns", "decisions"),
          ("artifact_references_from_earlier_turns", "artifacts")]


def body(block: dict) -> dict:
    return json.loads(block["content"][0]["text"].split("\n", 1)[1])


def size(block: dict) -> int:
    return len(block["content"][0]["text"].encode("utf-8"))


def real_cuts(messages: list[dict]) -> list[int]:
    """The cut points production really chose, not a guess at them."""
    store = ConversationStore(AuditLog(None, enabled=False))
    cuts = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        before = store.compaction_count
        store.model_messages(messages[:index])
        if store.compaction_count != before:
            cuts.append(store._cut)
    return cuts


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        print(f"usage: {sys.argv[0]} <*_microclaw_history.jsonl>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    messages = [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]
    cuts = real_cuts(messages)
    print(f"{path.name}: {len(messages)} messages, {len(cuts)} compactions")
    if not cuts:
        print("this session never compacted, so it cannot say anything about D7")
        return 1

    current = _checkpoint(messages[:cuts[-1]])
    segments = []
    previous = 0
    for cut in cuts:
        segments.append(_checkpoint(messages[previous:cut]))
        previous = cut

    now, then = estimate_tokens([current]), estimate_tokens(segments)
    print(f"\ncurrent: ONE checkpoint block, {size(current):>8} bytes = {now:>6} est tokens")
    print(f"D7:    {len(segments):>3} checkpoint blocks, {sum(map(size, segments)):>8} bytes "
          f"= {then:>6} est tokens")
    print(f"       D7 costs {then - now:+} est tokens on every call after the first "
          f"compaction")

    print("\nprovenance carried into the model's context:")
    head = body(current)
    for key, label in CAPPED:
        kept = sum(len(body(s)[key]) for s in segments)
        total = sum(body(s)["totals"][label] for s in segments)
        print(f"  {label:<10} current keeps {len(head[key]):>3} of {head['totals'][label]:>3}"
              f"   D7 keeps {kept:>3} of {total:>3}")

    biggest = max((max(len(body(s)[k]) for k, _ in CAPPED), n)
                  for n, s in enumerate(segments, 1))
    print(f"\nlargest single-segment list: {biggest[0]} entries (segment {biggest[1]}), "
          "against caps of 200/200/200/100 — the caps never bind per segment, which is "
          "why D7 carries more provenance and not less")
    return 0


if __name__ == "__main__":
    sys.exit(main())
