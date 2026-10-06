"""Gate 84 — score one driven session against a local llama.cpp server.

design/84 puts a local OpenAI-compatible model behind the same agent loop
(84a) and sizes the conversation budget from the server (84b). This scores the
artifacts of one session driven on the machine that runs the server.

    .venv\\Scripts\\python.exe design\\84-gate.py --session . --base-url http://127.0.0.1:8080 --api-key testkey
    .venv\\Scripts\\python.exe design\\84-gate.py --session . --progress

`--progress` prints one line about the newest session and exits 0; it is the
meter the runbook watches between turns. Without it every limb runs.

Every limb is independent: one FAIL never hides the limbs after it. A limb that
could not run its mechanism reports NOT EXERCISED, which is never a pass, and
the script exits nonzero on either. REPORT lines are measurements, not grades:
design/79's lesson is that naming the winner turns attribution into
label-reading, so timing is printed with its derivation and not judged.

The budget limb asks the product, not this file, what the budget is: it calls
`agent.make_conversation_store` against the same server, so the scorer cannot
drift from the code it scores. That needs the server up while scoring, and its
key: llama.cpp with `--api-key` answers nothing but `/health` without one.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

LOCAL_FIELDS = ("prompt_n", "cache_n", "prompt_ms", "predicted_n", "predicted_ms",
                "reasoning_characters")


class Score:
    """One line per limb, no cascade, and NOT EXERCISED is never a pass."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def _add(self, verdict: str, name: str, detail: str) -> None:
        self.rows.append((verdict, name, detail))
        print(f"{verdict:<14} {name} - {detail}", flush=True)

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
              f"{'' if not bad else ' - ' + ', '.join(f'{v}: {n}' for v, n, _ in bad)}")
        return 1 if bad else 0


def find_session(target: Path) -> tuple[Path | None, Path | None]:
    """Accept a directory (newest session in it) or a stem; return (usage, history)."""
    if target.is_dir():
        usage = sorted(target.glob("*_microclaw_usage.jsonl"))
        if not usage:
            return None, None
        stem = str(usage[-1]).replace("_microclaw_usage.jsonl", "")
    else:
        stem = str(target)
        for suffix in ("_microclaw_usage.jsonl", "_microclaw_history.jsonl"):
            stem = stem.replace(suffix, "")
    usage_path = Path(stem + "_microclaw_usage.jsonl")
    history_path = Path(stem + "_microclaw_history.jsonl")
    return (usage_path if usage_path.exists() else None,
            history_path if history_path.exists() else None)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def blocks(message: dict) -> list[dict]:
    content = message.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def progress(usage_path: Path | None) -> int:
    if usage_path is None:
        print("no session yet (no *_microclaw_usage.jsonl here)")
        return 0
    records = read_jsonl(usage_path)
    if not records:
        print(f"{usage_path.name}: no model calls yet")
        return 0
    last = records[-1]
    print(f"{usage_path.name}: {len(records)} calls, estimated history "
          f"{last.get('estimated_tokens')} tokens, compactions {last.get('compaction_count')}, "
          f"last prompt {last.get('input_tokens')} tokens "
          f"({last.get('prompt_n')} processed, {last.get('cache_n')} cached, "
          f"{(last.get('prompt_ms') or 0) / 1000:.1f} s)")
    return 0


def server_budget(base_url: str, api_key: str | None) -> tuple[int, int, int] | str:
    """(high, low, n_ctx) as the product computes them for this server, or a reason.

    The scorer reads `n_ctx` itself first. If it cannot (a 401 without the key,
    an unreachable server), the product would be computing a fallback budget,
    and grading the session against a fallback is grading nothing.
    """
    import httpx
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        from microclaw import agent
        response = agent._get_local_http().get(base_url.rstrip("/") + "/props",
                                               headers=headers, timeout=30)
        response.raise_for_status()
        n_ctx = response.json()["default_generation_settings"]["n_ctx"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return f"the scorer could not read the server's n_ctx: {type(exc).__name__}: {exc}"
    os.environ["MICROCLAW_LLM_BASE_URL"] = base_url
    if api_key:
        os.environ["MICROCLAW_LLM_API_KEY"] = api_key
    try:
        from microclaw.conversation import AuditLog
        store = agent.make_conversation_store(AuditLog("gate84-unused", enabled=False))
    except Exception as exc:  # the limb reports it; it must not stop the others
        return f"make_conversation_store failed: {type(exc).__name__}: {exc}"
    return store.high_water_tokens, store.low_water_tokens, n_ctx


def score(usage_path: Path | None, history_path: Path | None, base_url: str | None,
          api_key: str | None = None) -> int:
    s = Score()
    if usage_path is None or history_path is None:
        s.skip("the session wrote its history and usage files",
               f"usage={usage_path}, history={history_path}")
        return s.exit_code()
    records = read_jsonl(usage_path)
    history = read_jsonl(history_path)
    s.check("the session wrote its history and usage files", bool(records),
            f"{usage_path.name}: {len(records)} model calls; "
            f"{history_path.name}: {len(history)} messages")
    if not records:
        return s.exit_code()

    # --- 84a: the calls went to the local server -----------------------------
    # prompt_ms and cache_n exist only on the local path (llama.cpp `timings`);
    # an Anthropic call cannot produce them, so this is the backend's signature.
    models = sorted({str(r.get("model")) for r in records})
    local = [r for r in records if all(r.get(f) is not None for f in LOCAL_FIELDS)]
    s.check("every model call went through the local backend",
            len(local) == len(records) and not any(m.startswith("claude") for m in models),
            f"{len(local)}/{len(records)} records carry llama.cpp timings; model(s) {models}")

    assistant = [m for m in history if m.get("role") == "assistant"]
    s.check("one usage record per model reply", len(assistant) == len(records),
            f"{len(records)} usage records vs {len(assistant)} assistant messages")

    from microclaw.tools import TOOL_REGISTRY
    calls = [b for m in assistant for b in blocks(m) if b.get("type") == "tool_use"]
    unknown = sorted({b.get("name") for b in calls if b.get("name") not in TOOL_REGISTRY})
    unparsed = [b for b in calls if "_microclaw_parse_error" in (b.get("input") or {})]
    if not calls:
        s.skip("every tool call names a registered tool", "the session made no tool calls")
    else:
        s.check("every tool call names a registered tool", not unknown,
                f"{len(calls)} calls, {len(set(b.get('name') for b in calls))} distinct"
                + (f"; unknown: {unknown}" if unknown else ""))
    s.report("tool calls whose arguments did not parse",
             f"{len(unparsed)} of {len(calls)}"
             + (f": {[b.get('name') for b in unparsed]}" if unparsed else ""))

    # --- 84b: the budget comes from the server -------------------------------
    peak = max(r.get("input_tokens") or 0 for r in records)
    compactions = [i for i in range(1, len(records))
                   if (records[i].get("compaction_count") or 0)
                   > (records[i - 1].get("compaction_count") or 0)]
    # Three questions, graded separately (gate 84 round 1, F2: one limb that
    # skipped compaction records passed a checkpoint committed above high-water).
    FIT = "every request fit the server's context"
    D9 = "no compaction was committed above high-water"
    if base_url is None:
        for name in (FIT, D9):
            s.skip(name, "no --base-url given, so the server's budget was not read")
    else:
        budget = server_budget(base_url, api_key)
        if isinstance(budget, str):
            for name in (FIT, D9):
                s.skip(name, budget)
        else:
            high, low, n_ctx = budget
            from microclaw.agent import MAX_OUTPUT_TOKENS
            # The real limit: the server refuses a prompt that leaves no room for
            # the reply. Everything else is the store's policy.
            too_big = [(i, r.get("input_tokens")) for i, r in enumerate(records)
                       if (r.get("input_tokens") or 0) + MAX_OUTPUT_TOKENS > n_ctx]
            s.check(FIT, not too_big,
                    f"server n_ctx {n_ctx}; peak prompt {peak} + {MAX_OUTPUT_TOKENS} reply "
                    f"reserve = {peak + MAX_OUTPUT_TOKENS}"
                    + (f"; over at (record, prompt) {too_big}" if too_big else ""))
            # D9 (design/84 84c): a fold that cannot reach high-water is not
            # committed. The record of the compacting call carries the estimate
            # of the view it sent, so a committed compaction above high is visible.
            bad = [(i, records[i].get("estimated_tokens")) for i in compactions
                   if (records[i].get("estimated_tokens") or 0) > high]
            if not compactions:
                s.skip(D9, "the session never compacted, so no commit could be judged")
            else:
                s.check(D9, not bad,
                        f"high {high}, low {low}; estimate after each compaction "
                        f"{[records[i].get('estimated_tokens') for i in compactions]}"
                        + (f"; committed above high at (record, estimate) {bad}" if bad else ""))
            # D9 deliberately sends an over-threshold view when no fold would help;
            # that is policy, so it is reported, not graded.
            above = [i for i, r in enumerate(records)
                     if (r.get("estimated_tokens") or 0) > high and i not in compactions]
            s.report("requests sent above high-water without compacting",
                     f"{len(above)}" + (f" at record(s) {above}" if above else ""))

    if not compactions:
        s.skip("the session compacted at least once",
               f"compaction_count stayed {records[-1].get('compaction_count')}; peak prompt "
               f"{peak} tokens - drive more hook-log reads (runbook step 4)")
    else:
        s.check("the session compacted at least once", True,
                f"{len(compactions)} compaction(s), at record(s) {compactions}")
        for i in compactions:
            r = records[i]
            s.report(f"the round after compaction {records[i].get('compaction_count')}",
                     f"record {i}: prompt {r.get('input_tokens')} tokens, "
                     f"{r.get('prompt_n')} processed in {(r.get('prompt_ms') or 0) / 1000:.1f} s, "
                     f"{r.get('cache_n')} reused; estimate after compaction "
                     f"{r.get('estimated_tokens')}")

    # --- D7: the prefix is reused between rounds -----------------------------
    # Within a turn, round k+1's prompt is round k's prompt plus the reply and the
    # tool results, so llama.cpp should reuse nearly all of round k. A compaction
    # rewrites the history part, so those rounds are reported above, not graded.
    pairs = [(records[i - 1], records[i]) for i in range(1, len(records))
             if records[i].get("turn_id") == records[i - 1].get("turn_id")
             and i not in compactions]
    if not pairs:
        s.skip("each later round reused the previous round's prompt",
               "no turn had two rounds without a compaction between them")
    else:
        # 64 tokens of slack: the assistant generation prompt and the turn's
        # closing markers are re-rendered (measured 4-56 on 2026-10-02).
        missed = [(a.get("input_tokens"), b.get("cache_n")) for a, b in pairs
                  if (b.get("cache_n") or 0) < (a.get("input_tokens") or 0) - 64]
        s.check("each later round reused the previous round's prompt", not missed,
                f"{len(pairs) - len(missed)}/{len(pairs)} rounds reused all but <= 64 tokens"
                + (f"; misses (previous prompt, reused): {missed[:5]}" if missed else ""))

    # --- 84a D4: an image the text-only server never sees ---------------------
    # The model call that follows an image result is the one whose estimate would
    # carry the thumbnail. Records align with assistant messages in order, so
    # count assistant messages to find it. Only that call is examined: a hook-log
    # read legitimately adds ~19k tokens, so a session-wide "largest rise" would
    # fail every real run.
    after_image = []
    replies_so_far = 0
    for message in history:
        if message.get("role") == "assistant":
            replies_so_far += 1
        elif any(b.get("type") == "tool_result" and isinstance(b.get("content"), list)
                 and any(isinstance(p, dict) and p.get("type") == "image" for p in b["content"])
                 for b in blocks(message)):
            after_image.append(replies_so_far)
    rises = [(i, (records[i].get("estimated_tokens") or 0)
              - (records[i - 1].get("estimated_tokens") or 0))
             for i in after_image if 0 < i < len(records) and i not in compactions]
    if not rises:
        s.skip("a thumbnail did not inflate the history estimate",
               "no tool result carried an image followed by a model call without a "
               "compaction - runbook turn 2 asks for one")
    else:
        # A thumbnail counted as text adds 20k-120k tokens (design/70 R145); the
        # snap's own JSON payload is about a thousand.
        worst = max(rise for _, rise in rises)
        s.check("a thumbnail did not inflate the history estimate", worst < 5000,
                f"{len(rises)} call(s) after an image result; estimate rose by "
                f"{[rise for _, rise in rises]} tokens")

    # --- timing, reported -----------------------------------------------------
    prompt_s = [(r.get("prompt_ms") or 0) / 1000 for r in records]
    gen = [r["predicted_n"] / (r["predicted_ms"] / 1000) for r in records
           if r.get("predicted_n") and r.get("predicted_ms")]
    reasoning = [r.get("reasoning_characters") or 0 for r in records]
    s.report("prompt processing per call",
             f"median {statistics.median(prompt_s):.2f} s, max {max(prompt_s):.1f} s, "
             f"first call {prompt_s[0]:.1f} s; peak prompt {peak} tokens")
    if gen:
        s.report("generation rate", f"median {statistics.median(gen):.1f} tokens/s over "
                 f"{len(gen)} calls")
    s.report("reasoning text per call (not shown, not stored)",
             f"median {statistics.median(reasoning):.0f} chars, max {max(reasoning)}")
    return s.exit_code()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session", default=".", help="folder (newest session) or a session stem")
    parser.add_argument("--base-url", default=None, help="the llama.cpp server, for the budget limb")
    parser.add_argument("--api-key", default=None, help="the server's --api-key, if it has one")
    parser.add_argument("--progress", action="store_true", help="print one meter line and exit")
    args = parser.parse_args()
    usage_path, history_path = find_session(Path(args.session))
    if args.progress:
        return progress(usage_path)
    return score(usage_path, history_path, args.base_url, args.api_key)


if __name__ == "__main__":
    sys.exit(main())
