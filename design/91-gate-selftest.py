"""Selftest for design/91-gate.py: every graded limb discriminates.

    uv run python -m pytest -q design\\91-gate-selftest.py

A session is produced by the real `run_agent_iter`, the real
`ConversationStore` and the real `AuditLog`, against llama.cpp responses
recorded from a real server (`tests/fixtures/llamacpp/`) replayed through
`httpx.MockTransport`, so the product's own request building and SSE parsing
run. The usage sink adds what `webserve`'s does (estimate, compaction count,
turn id). Each graded limb is then run on a mutant that breaks exactly that
limb, and must not pass. The thumbnail is a real `make_thumbnail` of a noise
frame, because a smooth image encodes to almost nothing and would hide the
defect the limb exists for (design/70 R145).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from microclaw import agent
from microclaw.conversation import AuditLog, ConversationStore
from microclaw.image_analysis import make_thumbnail

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "llamacpp"
BASE = "http://gate91-recorded"
KEY = "testkey"

spec = importlib.util.spec_from_file_location("gate91", Path(__file__).with_name("91-gate.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def replay(name: str) -> httpx.Response:
    head, body = (FIXTURES / f"{name}.http").read_bytes().split(b"\n\n", 1)
    status, headers = head.split(b"\n", 1)
    return httpx.Response(int(status.split()[1]), headers=json.loads(headers), content=body)


def install_server(monkeypatch) -> None:
    chats = iter(["stream_tool_calls", "stream_text_reply"] * 50)

    def handler(request: httpx.Request) -> httpx.Response:
        # The real server with --api-key answers only /health without the key
        # (measured 2026-10-02: /props, /v1/models, /tokenize, /apply-template
        # all 401), so the fake refuses the same way.
        if request.headers.get("authorization") != f"Bearer {KEY}":
            return replay("error_bad_key")
        path = request.url.path
        if path.endswith("/props"):
            return replay("get_props")
        if path.endswith("/v1/models"):
            return replay("get_v1_models")
        if path.endswith("/apply-template"):
            return replay("post_apply_template")
        if path.endswith("/tokenize"):
            return replay("post_tokenize")
        return replay(next(chats))

    monkeypatch.setattr(agent, "_local_http", httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(agent, "_local_vision", {})
    monkeypatch.setattr(agent, "_local_models", None)
    monkeypatch.setenv("MICROCLAW_LLM_BASE_URL", BASE)
    monkeypatch.setenv("MICROCLAW_LLM_API_KEY", KEY)


def noise_thumbnail_result() -> list[dict]:
    frame = np.random.default_rng(0).integers(100, 4000, (2048, 2048), dtype=np.uint16)
    return [
        {"type": "text", "text": json.dumps({"z_um": 12.5})},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": make_thumbnail(frame, max_size=512)}},
    ]


# The recorded tool-call stream asks for set_exposure and get_z_position. Both
# are real registered names, so the scorer's registry limb sees real names;
# their implementations here are fakes, and get_z_position returns a thumbnail.
REGISTRY = {
    "set_exposure": lambda ctrl, guard, ms: {"exposure_ms": ms},
    "get_z_position": lambda ctrl, guard: noise_thumbnail_result(),
}


def drive(tmp_path: Path, *, turns: int, high: int, low: int, text_only: bool) -> Path:
    stem = tmp_path / "20261002_120000_000000"
    history_audit = AuditLog(str(stem) + "_microclaw_history.jsonl")
    usage_audit = AuditLog(str(stem) + "_microclaw_usage.jsonl")
    store = ConversationStore(history_audit, high_water_tokens=high,
                              low_water_tokens=low, text_only=text_only)
    history: list[dict] = []
    for turn in range(turns):
        def sink(record, turn=turn):
            usage_audit.append({**record, "estimated_tokens": store.last_estimated_tokens,
                                "compaction_count": store.compaction_count,
                                "turn_id": f"turn-{turn}"})
        for _ in agent.run_agent_iter(
            f"turn {turn}", None, None, history, model="recorded",
            context_provider=store.model_messages, on_message=store.append,
            tool_schemas=[], tool_registry=REGISTRY, usage_sink=sink,
        ):
            pass
    return stem


def verdicts(capsys, stem: Path, base_url: str | None = BASE,
             api_key: str | None = KEY) -> dict[str, str]:
    usage, history = gate.find_session(stem)
    gate.score(usage, history, base_url, api_key)
    out = capsys.readouterr().out
    rows = {}
    for line in out.splitlines():
        for verdict in ("NOT EXERCISED", "PASS", "FAIL", "REPORT"):
            if line.startswith(verdict):
                rows[line[len(verdict):].strip().split(" - ")[0]] = verdict
                break
    return rows


def rewrite(stem: Path, suffix: str, edit) -> None:
    path = Path(str(stem) + suffix)
    rows = gate.read_jsonl(path)
    edit(rows)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


LOCAL = "every model call went through the local backend"
ONE = "one usage record per model reply"
NAMES = "every tool call names a registered tool"
FIT = "every request fit the server's context"
D9 = "no compaction was committed above high-water"
COMPACTED = "the session compacted at least once"
PREFIX = "each later round reused the previous round's prompt"
THUMB = "a thumbnail did not inflate the history estimate"
GRADED = (LOCAL, ONE, NAMES, FIT, D9, COMPACTED, PREFIX, THUMB)


@pytest.fixture
def good(tmp_path, monkeypatch):
    install_server(monkeypatch)
    # high 2500 / low 1500, 10 turns: each turn adds ~430 estimated tokens and
    # a fold must clear high-water to commit (design/91 D9), so the checkpoint
    # plus the two retained turns has to fit; measured, this commits twice
    # (estimates 1502, 1858). At 1200 / 600 no fold ever clears high-water.
    return drive(tmp_path, turns=10, high=2500, low=1500, text_only=True)


def test_the_real_session_passes_every_graded_limb(good, capsys):
    rows = verdicts(capsys, good)
    assert {name: rows.get(name) for name in GRADED} == {name: "PASS" for name in GRADED}


def test_anthropic_shaped_records_fail_the_backend_limb(good, capsys):
    rewrite(good, "_microclaw_usage.jsonl",
            lambda rows: [r.pop("prompt_ms") for r in rows])
    assert verdicts(capsys, good)[LOCAL] == "FAIL"


def test_a_missing_usage_record_fails_the_count_limb(good, capsys):
    rewrite(good, "_microclaw_usage.jsonl", lambda rows: rows.pop())
    assert verdicts(capsys, good)[ONE] == "FAIL"


def test_an_unregistered_tool_name_fails_the_registry_limb(good, capsys):
    def rename(rows):
        for message in rows:
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    block["name"] = "set_exposur"
                    return
    rewrite(good, "_microclaw_history.jsonl", rename)
    assert verdicts(capsys, good)[NAMES] == "FAIL"


def test_a_prompt_with_no_room_for_the_reply_fails_the_fit_limb(good, capsys):
    # The recorded server reports n_ctx 131072; 125,000 + the 8,192 reply
    # reserve does not fit.
    rewrite(good, "_microclaw_usage.jsonl",
            lambda rows: rows[0].update(input_tokens=125_000))
    assert verdicts(capsys, good)[FIT] == "FAIL"


def test_a_compaction_committed_above_high_water_fails_the_d9_limb(good, capsys):
    # Gate 91 round 1's shape: the compacting call's estimate is still above the
    # high-water the product derives from the server (84,920 there; here the
    # recorded server's tokenize fixture makes it larger, so use n_ctx itself).
    def above(rows):
        for i in range(1, len(rows)):
            if rows[i]["compaction_count"] > rows[i - 1]["compaction_count"]:
                rows[i]["estimated_tokens"] = 131_072
                return
    rewrite(good, "_microclaw_usage.jsonl", above)
    assert verdicts(capsys, good)[D9] == "FAIL"


def test_no_base_url_is_not_exercised_not_passed(good, capsys):
    rows = verdicts(capsys, good, base_url=None)
    assert (rows[FIT], rows[D9]) == ("NOT EXERCISED", "NOT EXERCISED")


def test_no_key_for_a_keyed_server_is_not_exercised_not_passed(good, capsys):
    # Without the key the server answers 401 and the product falls back to a
    # guessed budget; the limb must not grade the session against that guess.
    rows = verdicts(capsys, good, api_key=None)
    assert (rows[FIT], rows[D9]) == ("NOT EXERCISED", "NOT EXERCISED")


def test_a_session_that_never_compacts_is_not_exercised(tmp_path, monkeypatch, capsys):
    install_server(monkeypatch)
    stem = drive(tmp_path, turns=3, high=10_000_000, low=5_000_000, text_only=True)
    assert verdicts(capsys, stem)[COMPACTED] == "NOT EXERCISED"


def test_a_lost_prefix_fails_the_reuse_limb(good, capsys):
    def cold(rows):
        for i in range(1, len(rows)):
            if rows[i]["turn_id"] == rows[i - 1]["turn_id"] \
                    and rows[i]["compaction_count"] == rows[i - 1]["compaction_count"]:
                rows[i]["cache_n"] = 0
                return
    rewrite(good, "_microclaw_usage.jsonl", cold)
    assert verdicts(capsys, good)[PREFIX] == "FAIL"


def test_a_thumbnail_counted_as_text_fails_the_image_limb(tmp_path, monkeypatch, capsys):
    # The mutant is the product defect itself: the Anthropic-path estimate,
    # which counts base64. Huge watermarks so no compaction hides the call.
    install_server(monkeypatch)
    stem = drive(tmp_path, turns=2, high=10_000_000, low=5_000_000, text_only=False)
    assert verdicts(capsys, stem)[THUMB] == "FAIL"


def test_the_same_session_with_the_fix_passes_the_image_limb(tmp_path, monkeypatch, capsys):
    install_server(monkeypatch)
    stem = drive(tmp_path, turns=2, high=10_000_000, low=5_000_000, text_only=True)
    assert verdicts(capsys, stem)[THUMB] == "PASS"


def test_progress_reads_the_newest_session(good, capsys):
    usage, _ = gate.find_session(good.parent)
    assert gate.progress(usage) == 0
    assert "compactions" in capsys.readouterr().out
