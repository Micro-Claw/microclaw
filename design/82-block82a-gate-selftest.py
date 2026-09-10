"""Selftest for `design/82-block82a-gate.py` — run it before the operator does.

A gate is code, and handing an operator code nobody executed is the defect this
workflow keeps paying for. Two disciplines here:

* **The payload is not written by hand.** It is produced by driving the real
  `run_agent_iter` with the real `ConversationStore` and the real `AuditLog`,
  at water marks low enough to force a genuine compaction. So the record shape
  under test is `agent.py`'s, not the scorer author's idea of it — if D1's
  fields drift, this fails.
* **Every graded limb has a mutant that breaks exactly it.** A limb that cannot
  fail is not a criterion, so each mutation asserts both that its own limb
  turned FAIL (or NOT EXERCISED) *and* that the gate exits nonzero.

    uv run python -m pytest -q design\\82-block82a-gate-selftest.py
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from anthropic.types import CacheCreation, TextBlock, Usage

from microclaw.agent import run_agent_iter
from microclaw.conversation import AuditLog, ConversationStore
from microclaw.safety import SafetyConstraints, SafetyGuard

_spec = importlib.util.spec_from_file_location(
    "block82a_gate", Path(__file__).with_name("82-block82a-gate.py"))
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

PREFIX = 50_000  # microclaw's fixed tools+system prefix, measured at 49,901.


class _Stream:
    """`client.messages.stream(...)`: iterates text deltas, hands back a Message."""

    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(())

    def get_final_message(self):
        return self._response


def _reply(text: str, usage: Usage):
    # A real SDK block, not a MagicMock: the audit encodes assistant content
    # through `json_default` -> `type(value).model_dump`, so a stand-in without
    # `model_dump` is stringified and the history stops looking like a real
    # one. Checked against an archived session (2026-08-18): assistant content
    # is a list of dicts whose `type` is "text" or "tool_use".
    block = TextBlock(type="text", text=text)
    response = MagicMock()
    response.model = "claude-opus-4-8"
    response.stop_reason = "end_turn"
    response.content = [block]
    response.usage = usage
    return response


def drive_session(directory: Path, turns: int = 14) -> tuple[Path, Path]:
    """One real session: real store, real audit, a fake API that bills like the
    documented cache does — a rewritten prefix after a compaction, a read
    otherwise."""
    stem = directory / "20260910_120000_000000"
    history_path = Path(f"{stem}_microclaw_history.jsonl")
    usage_path = Path(f"{stem}_microclaw_usage.jsonl")
    # Water marks chosen so the session compacts twice with warm turns either
    # side of each. A fixture that compacts on every turn has no ordinary round
    # left, and the cold-boundary limb then has nothing to be wrong about.
    store = ConversationStore(AuditLog(history_path),
                              high_water_tokens=4_000, low_water_tokens=2_500)
    usage_audit = AuditLog(usage_path)
    guard = SafetyGuard(SafetyConstraints())
    seen = {"compactions": 0}

    def next_usage() -> Usage:
        history_tokens = store.last_estimated_tokens or 0
        if store.compaction_count > seen["compactions"]:
            seen["compactions"] = store.compaction_count
            written = PREFIX + int(history_tokens * 1.4)
            return Usage(input_tokens=4, output_tokens=90,
                         cache_read_input_tokens=0,
                         cache_creation_input_tokens=written,
                         cache_creation=CacheCreation(
                             ephemeral_5m_input_tokens=0,
                             ephemeral_1h_input_tokens=written))
        grown = 900
        return Usage(input_tokens=4, output_tokens=90,
                     cache_read_input_tokens=PREFIX + int(history_tokens * 1.4) - grown,
                     cache_creation_input_tokens=grown,
                     cache_creation=CacheCreation(ephemeral_5m_input_tokens=0,
                                                  ephemeral_1h_input_tokens=grown))

    client = MagicMock()
    client.messages.stream.side_effect = lambda **kw: _Stream(
        _reply("Here is what the hook log says. " + "x" * 2_000, next_usage()))

    def usage_sink(record):
        usage_audit.append({**record,
                            "estimated_tokens": store.last_estimated_tokens,
                            "compaction_count": store.compaction_count})

    history: list[dict] = []
    with patch("microclaw.agent._get_client", return_value=client):
        for turn in range(turns):
            list(run_agent_iter(f"turn {turn}: read the hook log " + "y" * 600,
                                MagicMock(), guard, history,
                                context_provider=store.model_messages,
                                on_message=store.append, usage_sink=usage_sink))
    assert store.compaction_count >= 2, "the fixture must compact more than once"
    counts = [json.loads(line)["compaction_count"] for line in
              usage_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(counts[i] == counts[i - 1] for i in range(1, len(counts))), (
        "the fixture compacted on every turn, so it has no ordinary warm round")
    return usage_path, history_path


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    return drive_session(tmp_path_factory.mktemp("session"))


def verdicts(capsys, usage_path, history_path) -> tuple[int, dict[str, str]]:
    code = gate.score(usage_path, history_path)
    rows = {}
    for line in capsys.readouterr().out.splitlines():
        for verdict in ("NOT EXERCISED", "PASS", "FAIL", "REPORT"):
            if line.startswith(verdict):
                rows[line[len(verdict):].split(" — ")[0].strip()] = verdict
                break
    return code, rows


def mutated(tmp_path, usage_path, history_path, change) -> tuple[Path, Path]:
    """Copy the session, apply one change to the usage records, return the copy."""
    records = [json.loads(line) for line in
               usage_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    changed = change(records)
    records = changed if changed is not None else records
    out = tmp_path / usage_path.name
    out.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    copy_history = tmp_path / history_path.name
    copy_history.write_text(history_path.read_text(encoding="utf-8"), encoding="utf-8")
    return out, copy_history


def first_compaction(records: list[dict]) -> int:
    return next(i for i in range(1, len(records))
                if records[i]["compaction_count"] > records[i - 1]["compaction_count"])


def test_a_clean_session_passes_every_graded_limb(session, capsys):
    code, rows = verdicts(capsys, *session)
    assert code == 0, rows
    assert "FAIL" not in rows.values() and "NOT EXERCISED" not in rows.values(), rows
    assert rows["the session compacted at least once"] == "PASS"
    assert rows["every cache write used the 1h TTL"] == "PASS"
    assert rows["one record per API call"] == "PASS"
    assert rows["no cache miss the session start or a compaction cannot explain"] == "PASS"


def test_a_missing_sidecar_is_not_exercised(tmp_path, capsys):
    code, rows = verdicts(capsys, None, None)
    assert code == 1
    assert rows["the session wrote a usage sidecar"] == "NOT EXERCISED"


@pytest.mark.parametrize("limb,change", [
    ("every record carries every field",
     lambda rs: [{k: v for k, v in r.items() if k != "stop_reason"} for r in rs]),
    ("no field is null on live traffic",
     lambda rs: [{**r, "output_tokens": None} if i == 1 else r
                 for i, r in enumerate(rs)]),
    ("the served model is recorded",
     lambda rs: [{**r, "model": None} for r in rs]),
    ("every cache write used the 1h TTL",
     lambda rs: [{**r, "cache_creation_5m_input_tokens": r["cache_creation_input_tokens"],
                  "cache_creation_1h_input_tokens": 0} if i == 2 else r
                 for i, r in enumerate(rs)]),
    ("one record per API call", lambda rs: rs[:-1]),
    ("a compaction invalidates the prefix",
     lambda rs: [{**r, "cache_read_input_tokens": 4242}
                 if i == first_compaction(rs) else r for i, r in enumerate(rs)]),
    ("and pays to rewrite it",
     lambda rs: [{**r, "cache_creation_input_tokens": 0,
                  "cache_creation_1h_input_tokens": 0}
                 if i == first_compaction(rs) else r for i, r in enumerate(rs)]),
    ("no cache miss the session start or a compaction cannot explain",
     lambda rs: [{**r, "cache_read_input_tokens": 0}
                 if i == first_compaction(rs) + 1 else r for i, r in enumerate(rs)]),
])
def test_each_limb_has_a_mutation_that_breaks_only_it(session, tmp_path, capsys,
                                                      limb, change):
    paths = mutated(tmp_path, *session, change)
    code, rows = verdicts(capsys, *paths)
    assert code == 1, rows
    assert rows[limb] == "FAIL", rows
    others = {name: v for name, v in rows.items()
              if name != limb and v in ("FAIL", "NOT EXERCISED")}
    # "every record carries every field" and "no field is null" both read the
    # same keys, so a dropped key legitimately trips the null limb too.
    assert not others or set(others) <= {"no field is null on live traffic"}, others


def test_a_session_that_never_compacted_is_not_exercised(session, tmp_path, capsys):
    paths = mutated(tmp_path, *session, lambda rs: [{**r, "compaction_count": 0}
                                                    for r in rs])
    code, rows = verdicts(capsys, *paths)
    assert code == 1
    assert rows["the session compacted at least once"] == "NOT EXERCISED"


def test_a_usage_figure_in_the_history_is_caught(session, tmp_path, capsys):
    usage_path, history_path = session
    records = [json.loads(line) for line in
               usage_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    biggest = max(r["cache_read_input_tokens"] for r in records)
    leaky = tmp_path / history_path.name
    leaky.write_text(
        history_path.read_text(encoding="utf-8")
        + json.dumps({"role": "assistant",
                      "content": [{"type": "text",
                                   "text": f"this turn read {biggest} cached tokens"}]})
        + "\n", encoding="utf-8")
    code, rows = verdicts(capsys, usage_path, leaky)
    assert code == 1
    assert rows["no usage figure reached the model-visible history"] == "FAIL"


def test_the_reported_limbs_are_never_graded(session, capsys):
    _, rows = verdicts(capsys, *session)
    assert rows["output tokens vs the assistant text they produced"] == "REPORT"
    assert rows["the store's estimate vs the tokens actually billed"] == "REPORT"
