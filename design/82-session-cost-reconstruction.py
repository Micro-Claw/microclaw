"""Reconstruct what a set of saved microclaw sessions cost to run.

The archived sessions predate records of `response.usage` (design/82 F0), so cost has to be
reconstructed from the message histories. This script is the instrument that
produced every table in `design/82-the-bill-is-the-context-resent.md`, and it
is block 82b's acceptance gate: run it before and after a payload change and
compare the floors.

The exact invocation behind the design doc's tables, run 2026-09-09 against the
`nestor-expensive-sessions` archive — `tiktoken` is not a project dependency, so
install it into a scratch environment and put that on `PYTHONPATH`:

    uv pip install --python "$(command -v python3)" --target /tmp/tk tiktoken
    PYTHONPATH=/tmp/tk python3 design/82-session-cost-reconstruction.py \
        "<archive>/nestor-expensive-sessions" --cache-ttl 5m --kb-tokens 10000 --report 89.27 13.08

which prints floor $75.04 and a residual of $27.31 — 31 of 137 turn boundaries,
22%. Without `--kb-tokens` the floor is $70.88 and the per-line attribution is
the doc's top-five table exactly. The doc's *per-day* reconciliation (23%/24%)
groups sessions 1-4 against $89.27 and session 5 against $13.08 separately,
which this script does not do for you: it takes one directory.

It reads `*_microclaw_history.jsonl` and nothing else, writes nothing, and
touches no hardware. `--report` takes the console's per-day figures (UTC, less
non-session use of the key) in the same order the sessions fall across the day
boundary, and prints the reconciliation table.

Cache writes cost $6.25/MTok for 5m (1.25x base input), reconstructing the
pre-82a archive, or $10.00/MTok for 1h (2x), pricing the post-82a tree.
The default follows the real tree breakpoints; mixed TTLs refuse. Window
defaults likewise come from the shipped ConversationStore constants.

WHAT IT ASSUMES, ALL OF IT LOAD-BEARING

* Each assistant message in a history is exactly one API call, and the context
  that call carried is `ConversationStore.model_messages(history[:i])` — the
  real compaction path, imported, never reimplemented here.
* `cl100k_base` via tiktoken as a *proxy* tokenizer if importable, else
  bytes/CHARS_PER_TOKEN. It is not Claude's tokenizer either way, which is why
  `--scale` exists and why the reconciliation is a table rather than a number.
* Opus 4.8 pricing, `agent.py`'s DEFAULT_MODEL. The model a session actually ran
  on is not recorded (F0), so this cannot be checked.
* The floor assumes the prompt cache was warm on every call except the first and
  those following a compaction. Real cold turn boundaries appear as the residual
  against `--report`; the script cannot see them, because nothing recorded them.

The rig knowledge base is prepended to `system` on every call and is not in the
histories; pass `--kb-tokens` to include it.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from microclaw import agent, tools_schema
from microclaw.conversation import (AuditLog, ConversationStore,
    DEFAULT_CONTEXT_HIGH_WATER_TOKENS, DEFAULT_CONTEXT_LOW_WATER_TOKENS)
from microclaw.tools_schema import TOOLS

# $/MTok, claude-opus-4-8. Documented cache-write multipliers of base input.
PRICE_IN, PRICE_OUT, PRICE_READ = 5.00, 25.00, 0.50
WRITE_MULTIPLIERS = {"5m": 1.25, "1h": 2.0}


def tree_cache_ttl() -> str:
    """Read every cache marker returned by the production request builders."""
    ttls = []

    def collect(value):
        if isinstance(value, dict):
            if "cache_control" in value:
                ttls.append(value["cache_control"].get("ttl", "5m"))
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(agent._system_blocks())
    collect(agent._with_cache_breakpoint([{"role": "user", "content": "probe"}]))
    collect(tools_schema.TOOLS_CACHED)
    if len(set(ttls)) != 1:
        raise ValueError(f"tree cache breakpoints disagree: {ttls}")
    if ttls[0] not in WRITE_MULTIPLIERS:
        raise ValueError(f"unsupported tree cache TTL: {ttls[0]}")
    return ttls[0]

# Measured on real session histories (design/82 F4). One global ratio is wrong:
# schema and prose tokenize at 4.2-4.8 chars/token, dense numeric tool-result
# JSON at 2.54-2.89. Using either for both is how the estimator got F4 wrong.
CHARS_PER_TOKEN = {"history": 2.9, "schema": 4.2}
TOOL_BLOCK_OVERHEAD = 20   # per-tool structural tokens the schema JSON omits


def _tokenizer():
    """Return count(text, kind) -> tokens. `kind` selects a measured ratio for
    the fallback and is ignored once a real tokenizer is available."""
    try:
        import tiktoken
    except ImportError:
        print("# tiktoken absent: falling back to measured chars/token per content "
              "kind\n# (%s). `uv pip install tiktoken` into a scratch environment "
              "for the\n# tokenizer-proxy counts the design doc quotes."
              % CHARS_PER_TOKEN, file=sys.stderr)
        return lambda text, kind: int(
            len(text.encode("utf-8")) / CHARS_PER_TOKEN[kind])
    encoding = tiktoken.get_encoding("cl100k_base")
    return lambda text, kind: len(encoding.encode(text))


def _dumps(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class Counter:
    """Token counts, memoized — a 404-call session re-reads the same blocks."""

    def __init__(self, count_text, scale: float):
        self._count = count_text
        self._scale = scale
        self._cache: dict = {}

    def __call__(self, value, kind: str = "history") -> int:
        text = _dumps(value)
        key = (kind, len(text), text[:120], text[-40:])
        if key not in self._cache:
            self._cache[key] = self._count(text, kind)
        return int(self._cache[key] * self._scale)


def tool_name_index(messages: list[dict]) -> dict[str, str]:
    """tool_use_id -> tool name, so a result can be attributed to its tool."""
    names = {}
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                names[block["id"]] = block["name"]
    return names


def block_label(block, role: str, names: dict[str, str]) -> str:
    if not isinstance(block, dict):
        return "assistant prose"
    kind = block.get("type")
    if kind == "tool_result":
        return "result: " + names.get(block.get("tool_use_id"), "?")
    if kind == "tool_use":
        return "call arguments: " + block["name"]
    if kind == "text":
        if block.get("text", "").startswith("Conversation checkpoint:"):
            return "[compaction checkpoint]"
        return "assistant prose" if role == "assistant" else "user prompts"
    return str(kind)


def _shipped_drops() -> tuple[str, ...]:
    """The keys 82b's D2 removes from the tool result, read off the shipped code.

    Not retyped from the design doc: `inspect.getsource` of the function that
    actually returns the result, so this instrument cannot quietly disagree with
    what shipped. The same discipline `export_session_script` uses for inlining.
    """
    import inspect
    from microclaw import completed_dataset
    source = inspect.getsource(completed_dataset.run_analysis_on_saved_dataset)
    drops = tuple(k for k in ("scientific_payload", "parameters")
                  if f'"{k}"' in source.split("manifest_path.write_text")[-1])
    if not drops:
        raise SystemExit(
            "--as-if-82b: the shipped run_analysis_on_saved_dataset does not drop "
            "scientific_payload or parameters from its result. Either 82b is not "
            "in this tree, or it landed differently and this projection is stale.")
    return drops


def _as_if_82b(messages: list[dict], names: dict[str, str]) -> list[dict]:
    """Rewrite archived tool results into the shape 82b's code produces.

    The archive holds the results the *old* code returned, so re-running this
    script against a changed tree measures D4 alone — the payload decisions
    cannot show up on their own. This applies them to the recorded payloads.

    `read_hook_log` is not projected: the real, shipped tool is called against a
    temporary log holding the archived entries, so what is priced is what the
    code now returns. D2 is a projection of the shipped key filter, checked
    against the shipped source by `_shipped_drops`.
    """
    import tempfile
    from microclaw import tools as microclaw_tools

    drops = _shipped_drops()

    class _PassThroughGuard:
        """Path resolution only; the file is one this function just wrote."""

        def resolve_readable_path(self, path):
            return path

    def rewrite(payload: str, tool: str) -> str:
        try:
            value = json.loads(payload)
        except (TypeError, ValueError):
            return payload
        if not isinstance(value, dict):
            return payload
        if tool == "read_hook_log" and isinstance(value.get("entries"), list):
            with tempfile.TemporaryDirectory() as directory:
                log = os.path.join(directory, "hook.log")
                with open(log, "w", encoding="utf-8") as stream:
                    json.dump(value["entries"], stream)
                fresh = microclaw_tools.read_hook_log(None, _PassThroughGuard(), log)
            fresh["log_path"] = value.get("log_path", fresh["log_path"])
            fresh["artifact"] = value.get("artifact", fresh.get("artifact"))
            return _dumps(fresh)
        if tool == "run_analysis_on_saved_dataset" and "observations" in value:
            slim = {k: v for k, v in value.items() if k not in drops}
            if "parameters" in drops and "parameters" in value:
                slim["parameters_sha256"] = "0" * 64
            return _dumps(slim)
        return payload

    out = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            out.append(message)
            continue
        blocks = []
        for block in content:
            if (isinstance(block, dict) and block.get("type") == "tool_result"
                    and isinstance(block.get("content"), str)):
                tool = names.get(block.get("tool_use_id"), "")
                blocks.append({**block, "content": rewrite(block["content"], tool)})
            else:
                blocks.append(block)
        out.append({**message, "content": blocks})
    return out


def replay(path: str, count: Counter, base: dict[str, int], as_if_82b: bool = False, *,
           cache_ttl: str | None = None,
           high_water: int = DEFAULT_CONTEXT_HIGH_WATER_TOKENS,
           low_water: int = DEFAULT_CONTEXT_LOW_WATER_TOKENS):
    """Price one session, attributing every priced token to what it was.

    Returns (row, attribution). A call is billed as a full-prefix cache write on
    the first call and after each compaction — those are the only invalidations
    the history can prove. Everything else is a warm read plus its delta.
    """
    write_rate = PRICE_IN * WRITE_MULTIPLIERS[cache_ttl or tree_cache_ttl()]
    messages = [json.loads(line) for line in open(path) if line.strip()]
    names = tool_name_index(messages)
    if as_if_82b:
        # Before pricing, so a slimmer result also moves *when* compaction fires.
        messages = _as_if_82b(messages, names)
    store = ConversationStore(AuditLog(None, enabled=False),
                              high_water_tokens=high_water, low_water_tokens=low_water)
    attribution: collections.Counter = collections.Counter()

    base_tokens = sum(base.values())
    read = write = 0
    previous = None
    compactions = 0
    contexts: list[int] = []
    invalidated = 0

    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        context = store.model_messages(messages[:index])
        compacted = store.compaction_count != compactions
        compactions = store.compaction_count
        size = base_tokens + sum(count(m["content"]) + 8 for m in context)
        contexts.append(size)

        cold = previous is None or compacted
        if cold:
            write += size
            invalidated += 1
        else:
            read += min(previous, size)
            write += max(0, size - previous)
        previous = size

        rate = write_rate if cold else PRICE_READ
        for label, tokens in base.items():
            attribution[label] += tokens * rate / 1e6
        for entry in context:
            content = entry["content"]
            blocks = content if isinstance(content, list) else [
                {"type": "text", "text": content}]
            for block in blocks:
                label = block_label(block, entry.get("role", ""), names)
                attribution[label] += count(block) * rate / 1e6

    output = sum(count(m["content"]) for m in messages
                 if m.get("role") == "assistant")
    attribution["[output tokens generated]"] += output * PRICE_OUT / 1e6
    cost = read * PRICE_READ / 1e6 + write * write_rate / 1e6 + output * PRICE_OUT / 1e6

    turns = sum(1 for m in messages
                if m.get("role") == "user" and isinstance(m.get("content"), str))
    row = {
        "session": os.path.basename(path)[:22],
        "calls": len(contexts), "turns": turns, "compactions": compactions,
        "invalidations": invalidated,
        "avg_context": sum(contexts) // max(1, len(contexts)),
        "max_context": max(contexts, default=0),
        "read": read, "write": write, "output": output, "cost": cost,
    }
    return row, attribution


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", help="directory of *_microclaw_history.jsonl")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="multiply every token count; the proxy tokenizer's "
                             "error against Claude's is the one free parameter")
    parser.add_argument("--kb-tokens", type=int, default=0,
                        help="rig knowledge base tokens, added to every call")
    parser.add_argument("--as-if-82b", action="store_true",
                        help="re-price the archived sessions as if block 82b's "
                             "payload decisions had produced them: read_hook_log "
                             "through the real shipped tool, D2's key drop as a "
                             "projection checked against the shipped source. "
                             "Without this, a re-run measures D4 alone, because "
                             "the archive holds the results the old code returned.")
    parser.add_argument("--report", type=float, nargs="*", default=[],
                        help="console per-day totals (UTC, less non-session use), "
                             "in session order, for the reconciliation")
    parser.add_argument("--cache-ttl", choices=WRITE_MULTIPLIERS)
    parser.add_argument("--high-water", type=int, default=DEFAULT_CONTEXT_HIGH_WATER_TOKENS)
    parser.add_argument("--low-water", type=int, default=DEFAULT_CONTEXT_LOW_WATER_TOKENS)
    args = parser.parse_args()
    try:
        shipped_ttl = tree_cache_ttl()
    except ValueError as exc:
        parser.error(str(exc))
    args.cache_ttl = args.cache_ttl or shipped_ttl
    if not 0 < args.low_water < args.high_water:
        parser.error("context low-water must be positive and below high-water")
    write_rate = PRICE_IN * WRITE_MULTIPLIERS[args.cache_ttl]
    settings = (f"TTL {args.cache_ttl}; cache write ${write_rate:.2f}/MTok; "
                f"window {args.high_water}/{args.low_water}")

    count = Counter(_tokenizer(), args.scale)
    base = {
        f"[{len(TOOLS)} tool schemas]":
            count(TOOLS, "schema") + TOOL_BLOCK_OVERHEAD * len(TOOLS),
        "[system prompt]": count(agent.SYSTEM_PROMPT, "schema"),
    }
    if args.kb_tokens:
        base["[rig knowledge base]"] = args.kb_tokens

    paths = sorted(glob.glob(os.path.join(args.directory, "*_microclaw_history.jsonl")))
    if not paths:
        print(f"no *_microclaw_history.jsonl under {args.directory}", file=sys.stderr)
        return 1

    print(f"model claude-opus-4-8 at ${PRICE_IN}/${PRICE_OUT} per MTok; "
          f"{settings}; cache read ${PRICE_READ}/MTok")
    print("base per call: %d tokens (%s), scale %s\n" % (
        sum(base.values()),
        ", ".join(f"{k.strip('[]')} {v}" for k, v in base.items()), args.scale))

    header = ("session", "calls", "turns", "comp", "avgctx", "maxctx",
              "readM", "writeM", "outk", "floor $")
    print("%-24s%6s%6s%6s%8s%8s%8s%8s%7s%9s" % header)
    total = collections.Counter()
    rows = []
    for path in paths:
        row, attribution = replay(path, count, base, args.as_if_82b,
                                  cache_ttl=args.cache_ttl, high_water=args.high_water,
                                  low_water=args.low_water)
        rows.append(row)
        total.update(attribution)
        print("%-24s%6d%6d%6d%7dk%7dk%8.1f%8.2f%7d%9.2f" % (
            row["session"], row["calls"], row["turns"], row["compactions"],
            row["avg_context"] // 1000, row["max_context"] // 1000,
            row["read"] / 1e6, row["write"] / 1e6, row["output"] // 1000,
            row["cost"]))

    floor = sum(r["cost"] for r in rows)
    reads = sum(r["read"] for r in rows) * PRICE_READ / 1e6
    writes = sum(r["write"] for r in rows) * write_rate / 1e6
    outputs = sum(r["output"] for r in rows) * PRICE_OUT / 1e6
    print(f"TOTAL / summary — {settings}")
    print("%-24s%6d%6d%6d%8s%8s%8.1f%8.2f%7d%9.2f" % (
        "TOTAL", sum(r["calls"] for r in rows), sum(r["turns"] for r in rows),
        sum(r["compactions"] for r in rows), "", "",
        sum(r["read"] for r in rows) / 1e6, sum(r["write"] for r in rows) / 1e6,
        sum(r["output"] for r in rows) // 1000, floor))
    print(f"\ncache read ${reads:.2f} ({100 * reads / floor:.0f}%)  "
          f"cache write ${writes:.2f} ({100 * writes / floor:.0f}%)  "
          f"output ${outputs:.2f} ({100 * outputs / floor:.0f}%)")

    invalidations = sum(r["invalidations"] for r in rows)
    print(f"{invalidations} full-prefix invalidations (compactions + session starts): "
          f"each pays {write_rate / PRICE_READ:g}x for its whole context")

    print(f"\nwhat the priced context was, as a share of the floor — {settings}:")
    attributed = sum(total.values())
    for label, dollars in total.most_common(12):
        print(f"  {dollars:7.2f}  {100 * dollars / attributed:5.1f}%  {label}")

    if args.report:
        print(f"\nreconciliation — {settings}\n"
              "the floor assumes a warm cache; the residual is "
              "\nwhat cold turn boundaries and anything unmodelled cost:")
        if len(args.report) != len(rows) and len(args.report) != 1:
            print(f"  ({len(args.report)} reported totals for {len(rows)} sessions: "
                  "grouping them is the caller's call, see design/82)")
        reported = sum(args.report)
        residual = reported - floor
        turns = sum(r["turns"] for r in rows)
        per_cold = (sum(r["avg_context"] * r["turns"] for r in rows) / max(1, turns)
                    * (write_rate - PRICE_READ) / 1e6)
        print(f"  reported ${reported:.2f}  floor ${floor:.2f}  "
              f"residual ${residual:.2f} ({100 * residual / reported:.0f}% of the bill)")
        print(f"  at ${per_cold:.2f} per cold boundary that is "
              f"{residual / per_cold:.0f} of {turns} turn boundaries "
              f"({100 * residual / per_cold / turns:.0f}%)")
        print("  re-run with --scale to see how the tokenizer's error moves it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
