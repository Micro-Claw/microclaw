"""Block 82a demo gate — score one driven session's usage sidecar.

design/82's F0 says no microclaw session has ever recorded what it cost. 82a
adds `*_microclaw_usage.jsonl` (D1) and moves all four cache breakpoints to the
one-hour TTL (D5). This scores the artifacts of a session that was driven long
enough to compact at least once.

    uv run python design\\82-block82a-gate.py --session <history-stem-or-dir>

Every limb is independent: one FAIL never hides the limbs after it. A limb that
could not run its mechanism reports NOT EXERCISED, which is never a pass, and
the script exits nonzero on either.

Two limbs deliberately only *report*. The point of D1 is to measure, and
design/79's lesson is that naming the winner turns attribution into
label-reading — so the estimator residual and the output-token rate are printed
with their derivations and not graded. What *is* graded is everything with a
mechanism behind it: one record per API call, every write at the 1h TTL, and no
cache miss that a compaction or the session start does not explain.

Why that last one matters: it is the whole $18-29 cold-boundary term. Before
82a every turn boundary risked a 5-minute expiry; with `ttl: "1h"` a session
shorter than an hour should show no unexplained miss at all.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

TTL_SECONDS = 3600
FIELDS = ("timestamp", "model", "iteration", "stop_reason", "input_tokens",
          "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
          "cache_creation_5m_input_tokens", "cache_creation_1h_input_tokens",
          "estimated_tokens", "compaction_count")


class Score:
    """One line per limb, no cascade, and NOT EXERCISED is never a pass."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def _add(self, verdict: str, name: str, detail: str) -> None:
        self.rows.append((verdict, name, detail))
        print(f"{verdict:<14} {name} — {detail}", flush=True)

    def check(self, name: str, ok: bool, detail: str) -> None:
        self._add("PASS" if ok else "FAIL", name, detail)

    def skip(self, name: str, detail: str) -> None:
        self._add("NOT EXERCISED", name, detail)

    def report(self, name: str, detail: str) -> None:
        self._add("REPORT", name, detail)

    def exit_code(self) -> int:
        bad = [r for r in self.rows if r[0] in ("FAIL", "NOT EXERCISED")]
        passed = sum(1 for r in self.rows if r[0] == "PASS")
        graded = sum(1 for r in self.rows if r[0] != "REPORT")
        print(f"\n{passed}/{graded} graded limbs passed"
              f"{'' if not bad else ' — ' + ', '.join(f'{v}: {n}' for v, n, _ in bad)}")
        return 1 if bad else 0


def find_session(target: Path) -> tuple[Path | None, Path | None]:
    """Accept a directory, a history path, or a stem; return (usage, history)."""
    if target.is_dir():
        usage = sorted(target.glob("*_microclaw_usage.jsonl"))
        history = sorted(target.glob("*_microclaw_history.jsonl"))
        return (usage[-1] if usage else None, history[-1] if history else None)
    text = str(target)
    for suffix in ("_microclaw_usage.jsonl", "_microclaw_history.jsonl"):
        text = text.replace(suffix, "")
    stem = Path(text)
    usage = stem.with_name(stem.name + "_microclaw_usage.jsonl")
    history = stem.with_name(stem.name + "_microclaw_history.jsonl")
    return (usage if usage.exists() else None, history if history.exists() else None)


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def assistant_text_chars(history: list[dict]) -> tuple[int, int]:
    """(assistant messages, characters of assistant text) in the audit."""
    messages = 0
    chars = 0
    for message in history:
        if message.get("role") != "assistant":
            continue
        messages += 1
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    chars += len(block.get("text") or "")
                elif isinstance(block, dict) and block.get("type") == "tool_use":
                    chars += len(json.dumps(block.get("input") or {}))
    return messages, chars


def seconds_between(earlier: str, later: str) -> float:
    return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds()


def score(usage_path: Path | None, history_path: Path | None) -> int:
    s = Score()

    if usage_path is None:
        s.skip("the session wrote a usage sidecar",
               "no *_microclaw_usage.jsonl beside the history — D1 did not run here")
        return s.exit_code()
    records = read_jsonl(usage_path)
    s.check("the session wrote a usage sidecar", bool(records),
            f"{usage_path.name}: {len(records)} records")
    if not records:
        return s.exit_code()

    # --- the record itself -------------------------------------------------
    missing = {f for r in records for f in FIELDS if f not in r}
    s.check("every record carries every field", not missing,
            "all present" if not missing else f"absent: {sorted(missing)}")

    nulls = sorted({f for r in records for f in FIELDS
                    if f in r and r[f] is None})
    s.check("no field is null on live traffic", not nulls,
            "none null" if not nulls else f"null somewhere: {nulls}")

    models = sorted({r.get("model") for r in records})
    s.check("the served model is recorded", all(isinstance(m, str) and m for m in models),
            f"model(s): {models}")

    # --- D5: the one-hour TTL ---------------------------------------------
    writes = [r for r in records if (r.get("cache_creation_input_tokens") or 0) > 0]
    if not writes:
        s.skip("every cache write used the 1h TTL",
               "no record wrote to the cache at all — the prefix never cached, "
               "which is itself a defect to chase, not a pass")
    else:
        wrong = [r for r in writes
                 if (r.get("cache_creation_1h_input_tokens") or 0)
                 != (r.get("cache_creation_input_tokens") or 0)
                 or (r.get("cache_creation_5m_input_tokens") or 0) != 0]
        total_1h = sum(r.get("cache_creation_1h_input_tokens") or 0 for r in writes)
        total_5m = sum(r.get("cache_creation_5m_input_tokens") or 0 for r in writes)
        s.check("every cache write used the 1h TTL", not wrong,
                f"{len(writes)} writes, {total_1h} tokens at 1h, {total_5m} at 5m"
                + ("" if not wrong else f"; {len(wrong)} record(s) disagree"))

    # --- one record per API call ------------------------------------------
    if history_path is None:
        s.skip("one record per API call",
               "no *_microclaw_history.jsonl to compare against")
        assistant, assistant_chars = 0, 0
        history: list[dict] = []
    else:
        history = read_jsonl(history_path)
        assistant, assistant_chars = assistant_text_chars(history)
        s.check("one record per API call", assistant == len(records),
                f"{len(records)} usage records vs {assistant} assistant messages "
                f"in {history_path.name}")

    # --- compaction, and what it does to the cache ------------------------
    compactions = [i for i in range(1, len(records))
                   if (records[i].get("compaction_count") or 0)
                   > (records[i - 1].get("compaction_count") or 0)]
    if not compactions:
        s.skip("the session compacted at least once",
               f"compaction_count never rose (last: "
               f"{records[-1].get('compaction_count')}) — drive a longer session; "
               "the limbs below have no subject")
    else:
        s.check("the session compacted at least once", True,
                f"{len(compactions)} compaction(s), at record(s) {compactions}")
        cold = [i for i in compactions
                if (records[i].get("cache_read_input_tokens") or 0) == 0]
        s.check("a compaction invalidates the prefix", len(cold) == len(compactions),
                f"{len(cold)}/{len(compactions)} post-compaction records read 0 "
                "cached tokens" + ("" if len(cold) == len(compactions) else
                                   f"; read on the others: "
                                   f"{[records[i]['cache_read_input_tokens'] for i in compactions if i not in cold]}"))
        rewrites = [records[i].get("cache_creation_input_tokens") or 0
                    for i in compactions]
        s.check("and pays to rewrite it", all(v > 0 for v in rewrites),
                f"rewritten tokens per compaction: {rewrites}")

    # --- D5's payoff: is any miss unexplained? ----------------------------
    unexplained = []
    for i, record in enumerate(records):
        if (record.get("cache_read_input_tokens") or 0) > 0:
            continue
        if i == 0 or i in compactions:
            continue
        gap = seconds_between(records[i - 1]["timestamp"], record["timestamp"])
        if gap < TTL_SECONDS:
            unexplained.append((i, round(gap, 1)))
    span = (seconds_between(records[0]["timestamp"], records[-1]["timestamp"])
            if len(records) > 1 else 0.0)
    s.check("no cache miss the session start or a compaction cannot explain",
            not unexplained,
            f"session spans {span / 60:.1f} min; no unexplained miss"
            if not unexplained else
            f"{len(unexplained)} miss(es) after a gap shorter than the 1h TTL: "
            f"{unexplained}")

    # --- no cost anywhere the model can see it ----------------------------
    if history:
        blob = json.dumps(history)
        leaked = [f for f in ("cache_read_input_tokens", "cache_creation_1h_input_tokens",
                              "estimated_tokens", "compaction_count") if f in blob]
        biggest = max((r.get("cache_read_input_tokens") or 0) for r in records)
        if biggest and str(biggest) in blob:
            leaked.append(f"the value {biggest}")
        s.check("no usage figure reached the model-visible history", not leaked,
                "clean" if not leaked else f"found: {leaked}")

    # --- reported, never graded -------------------------------------------
    if assistant_chars:
        out = sum(r.get("output_tokens") or 0 for r in records)
        s.report("output tokens vs the assistant text they produced",
                 f"{out} output tokens for {assistant_chars} chars of assistant "
                 f"text and tool arguments = {assistant_chars / max(out, 1):.2f} chars/token")

    pairs = [(r["estimated_tokens"],
              (r.get("cache_read_input_tokens") or 0)
              + (r.get("cache_creation_input_tokens") or 0)
              + (r.get("input_tokens") or 0))
             for r in records if r.get("estimated_tokens")]
    if pairs:
        ratios = [real / est for est, real in pairs]
        residuals = [real - est for est, real in pairs]
        s.report("the store's estimate vs the tokens actually billed",
                 f"real/estimated over {len(pairs)} calls: "
                 f"min {min(ratios):.2f}, median {sorted(ratios)[len(ratios) // 2]:.2f}, "
                 f"max {max(ratios):.2f}; absolute residual median "
                 f"{sorted(residuals)[len(residuals) // 2]} tokens. The estimate "
                 "covers the history only, so this carries both F4's chars/token "
                 "error and the fixed tools+system prefix; it is not one number.")

    return s.exit_code()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True,
                        help="Directory holding the session files, or the history path/stem.")
    args = parser.parse_args()
    usage_path, history_path = find_session(Path(args.session))
    print(f"usage:   {usage_path}")
    print(f"history: {history_path}\n")
    return score(usage_path, history_path)


if __name__ == "__main__":
    sys.exit(main())
