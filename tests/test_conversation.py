import json
import os

import pytest

from microclaw.conversation import (
    AuditLog,
    ConversationStore,
    REDACTED,
    atomic_write_text,
    estimate_tokens,
    load_history,
    prune_transcripts,
)


def _turn(index, *, size=800, image=False):
    result = {"value_um": 1.2345, "artifact": {
        "kind": "tiff", "path": f"run-{index}.tif", "sha256": f"hash-{index}",
    }}
    content = [{"type": "text", "text": json.dumps(result)}]
    if image:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": "IMAGE-BYTES-SECRET",
        }})
    return [
        {"role": "user", "content": f"Use protocol {index}"},
        {"role": "assistant", "content": [{
            "type": "tool_use", "id": f"call-{index}", "name": "snap_and_analyze",
            "input": {"exposure_ms": 12.5},
        }]},
        {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": f"call-{index}", "content": content,
        }]},
        {"role": "assistant", "content": [{"type": "text", "text": "done " + "x" * size}]},
    ]


def test_jsonl_recovers_complete_records_before_torn_final_line(tmp_path):
    path = tmp_path / "history.jsonl"
    first = {"role": "user", "content": "hello"}
    path.write_text(json.dumps(first) + "\n" + '{"role":"assistant"', encoding="utf-8")
    loaded = load_history(path)
    assert loaded.messages == [first]
    assert loaded.warnings and "incomplete final" in loaded.warnings[0].lower()


def test_legacy_array_history_still_loads_unchanged(tmp_path):
    path = tmp_path / "old_microclaw_history.json"
    messages = [{"role": "user", "content": "µm"}]
    path.write_text(json.dumps(messages), encoding="utf-8")
    assert load_history(path).messages == messages
    assert load_history(path).warnings == []


def test_audit_redacts_credentials_but_keeps_images_and_scientific_values(tmp_path):
    path = tmp_path / "audit.jsonl"
    secret = "operator-token-which-must-not-survive"
    audit = AuditLog(path)
    audit.add_secret(secret)  # runtime-minted bearer/pairing tokens use this path
    audit.append({
        "role": "user",
        "content": [{
            "type": "text", "text": f"Bearer {secret}; value=6.022e23",
        }, {
            "type": "image", "source": {"type": "base64", "data": "IMAGEBYTES"},
        }],
    })
    raw = path.read_text(encoding="utf-8")
    assert secret not in raw
    assert REDACTED in raw
    assert "6.022e23" in raw
    assert "IMAGEBYTES" in raw


def test_atomic_write_interruption_leaves_previous_file_intact(tmp_path, monkeypatch):
    path = tmp_path / "history.json"
    path.write_text("previous", encoding="utf-8")

    def interrupted(_source, _target):
        raise OSError("simulated crash before replace")

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError, match="simulated crash"):
        atomic_write_text(path, "replacement")
    assert path.read_text(encoding="utf-8") == "previous"


def test_retention_default_keeps_everything_and_explicit_age_prunes(tmp_path):
    old = tmp_path / "20000101_microclaw_history.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    os.utime(old, (1, 1))
    assert prune_transcripts(tmp_path, None, now=10_000_000) == []
    assert old.exists()
    assert prune_transcripts(tmp_path, 1, now=10_000_000) == [old]
    assert not old.exists()


def test_compaction_is_batched_stable_and_keeps_tool_pairs_together(tmp_path):
    secret = "remote-pairing-secret-value"
    history = _turn(0, image=True) + sum((_turn(i) for i in range(1, 5)), [])
    history[0]["content"] += " with " + secret
    store = ConversationStore(
        AuditLog(tmp_path / "audit.jsonl", secrets=[secret]), high_water_tokens=1200,
        low_water_tokens=500,
    )
    first = store.model_messages(history)
    assert store.compaction_count == 1
    checkpoint = first[0]
    assert "Historical provenance only" in checkpoint["content"][0]["text"]
    assert "IMAGE-BYTES-SECRET" not in checkpoint["content"][0]["text"]
    assert secret not in checkpoint["content"][0]["text"]

    # A small new turn stays below the high-water trigger, leaving the cacheable
    # checkpoint prefix byte-for-byte stable.
    history.extend(_turn(5, size=10))
    second = store.model_messages(history)
    assert store.compaction_count == 1
    assert second[0] == checkpoint

    uses = {b["id"] for m in second for b in (m.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "tool_use"}
    results = {b["tool_use_id"] for m in second for b in (m.get("content") or [])
               if isinstance(b, dict) and b.get("type") == "tool_result"}
    assert uses == results


def test_long_session_compacts_model_view_without_losing_audit(tmp_path):
    history = []
    store = ConversationStore(
        AuditLog(tmp_path / "long.jsonl"), high_water_tokens=1000,
        low_water_tokens=600,
    )
    for index in range(30):
        for message in _turn(index, size=200):
            history.append(message)
            store.append(message)
        store.model_messages(history)
    assert store.compaction_count > 0
    assert len(store.audit.records) == len(history) == 120
    assert len(load_history(tmp_path / "long.jsonl").messages) == 120
    assert estimate_tokens(store.model_messages(history)) < estimate_tokens(history)


def test_compaction_keeps_two_complete_recent_turns_when_budget_is_impossible():
    history = []
    for index in range(8):
        history.extend([
            {"role": "user", "content": f"turn {index}"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "x" * 1200},
            ]},
        ])
    store = ConversationStore(
        AuditLog(None, enabled=False), high_water_tokens=1500, low_water_tokens=800,
    )

    view = store.model_messages(history)

    assert view[1:] == history[-4:]
    assert estimate_tokens(view) > store.low_water_tokens


def test_tool_input_heavy_checkpoint_shrinks_history_meaningfully():
    history = []
    for index in range(12):
        history.extend([
            {"role": "user", "content": "decision " + "d" * 2000},
            {"role": "assistant", "content": [{
                "type": "tool_use", "id": f"call-{index}", "name": "save_hook",
                "input": {"script": "s" * 5000, "nested": {"notes": "n" * 3000}},
            }]},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": f"call-{index}", "content": "{}",
            }]},
            {"role": "assistant", "content": [{"type": "text", "text": "saved"}]},
        ])
    store = ConversationStore(
        AuditLog(None, enabled=False), high_water_tokens=1000, low_water_tokens=600,
    )

    view = store.model_messages(history)
    checkpoint_text = view[0]["content"][0]["text"]

    assert "ELIDED" in checkpoint_text
    assert estimate_tokens(view) < estimate_tokens(history) * 0.35


def test_structured_credential_named_payload_keeps_its_shape():
    from microclaw.conversation import redact_credentials

    value = {"authorization": {"mode": "categorical", "token": "public-value"}}
    assert redact_credentials(value) == value
