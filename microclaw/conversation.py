"""Durable conversation audit and bounded model context.

The audit is the record of what happened.  Model context is a disposable view
of that record: it may compact old, completed turns, but never changes or
removes audit records.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import datetime as _datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Iterable


REDACTED = "[REDACTED]"
DEFAULT_CONTEXT_HIGH_WATER_TOKENS = 120_000
DEFAULT_CONTEXT_LOW_WATER_TOKENS = 90_000
DEFAULT_HISTORY_PAGE_LIMIT = 100
MAX_HISTORY_PAGE_LIMIT = 500
MIN_RECENT_COMPLETE_TURNS = 2
CHECKPOINT_STRING_LIMIT = 256
CHECKPOINT_ELISION = "[...ELIDED {count} CHARS...]"

_API_KEY_RE = re.compile(r"\bsk-ant-[A-Za-z0-9_-]{12,}\b")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}")
_SECRET_KEYS = {
    "api_key", "anthropic_api_key", "authorization", "bearer_token",
    "remote_token", "pairing_code", "session_token",
}


def json_default(value: Any) -> Any:
    """Encode Anthropic SDK blocks without relying on SDK internals."""
    model_dump = getattr(type(value), "model_dump", None)
    return model_dump(value) if callable(model_dump) else str(value)


def jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=json_default))


def redact_credentials(value: Any, secrets: Iterable[str] = ()) -> Any:
    """Return a copy with credentials removed, leaving scientific data intact."""
    known = tuple(s for s in secrets if s)

    def redact(item: Any, key: str | None = None) -> Any:
        if key and key.lower() in _SECRET_KEYS and isinstance(item, str):
            return REDACTED
        if isinstance(item, dict):
            return {str(k): redact(v, str(k)) for k, v in item.items()}
        if isinstance(item, list):
            return [redact(v) for v in item]
        if isinstance(item, str):
            text = item
            for secret in known:
                text = text.replace(secret, REDACTED)
            text = _API_KEY_RE.sub(REDACTED, text)
            return _BEARER_RE.sub(lambda m: m.group(1) + REDACTED, text)
        return item

    return redact(jsonable(value))


@dataclass(frozen=True)
class HistoryLoad:
    messages: list[dict]
    warnings: list[str] = field(default_factory=list)


def load_history(path: str | os.PathLike[str]) -> HistoryLoad:
    """Load legacy JSON arrays or append-only JSONL.

    A torn final JSONL line is ignored with a warning.  Malformed complete or
    interior lines remain errors because silently skipping them would falsify
    the durable record.
    """
    source = Path(path)
    raw = source.read_text(encoding="utf-8")
    stripped = raw.lstrip()
    if not stripped:
        return HistoryLoad([])
    if stripped.startswith("["):
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("history JSON must be an array of messages")
        return HistoryLoad(data)

    lines = raw.splitlines(keepends=True)
    messages: list[dict] = []
    notices: list[str] = []
    for index, physical in enumerate(lines):
        line = physical.rstrip("\r\n")
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            is_torn_final = index == len(lines) - 1 and not physical.endswith(("\n", "\r"))
            if is_torn_final:
                notices.append(
                    f"Ignored an incomplete final JSONL record in {source.name}; "
                    "all complete records were recovered."
                )
                break
            raise ValueError(f"Malformed JSONL record {index + 1}: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"JSONL record {index + 1} is not a message object")
        messages.append(record)
    return HistoryLoad(messages, notices)


def atomic_write_text(path: str | os.PathLike[str], text: str) -> None:
    """Replace a text file atomically, using a same-directory temporary file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class AuditLog:
    """Append-only, credential-redacted JSONL transcript."""

    def __init__(self, path: str | os.PathLike[str] | None, *, enabled: bool = True,
                 secrets: Iterable[str] = ()):
        self.path = Path(path) if path is not None else None
        self.enabled = enabled and self.path is not None
        self.secrets = tuple(s for s in secrets if s)
        self.records: list[dict] = []
        self._lock = threading.Lock()

    def add_secret(self, secret: str | None) -> None:
        if secret and secret not in self.secrets:
            self.secrets = (*self.secrets, secret)

    def append(self, message: dict) -> dict:
        record = redact_credentials(message, self.secrets)
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            if self.enabled:
                assert self.path is not None
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8", newline="") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
            self.records.append(record)
        return record

    def page(self, cursor: str | None = None, limit: int = DEFAULT_HISTORY_PAGE_LIMIT) -> dict:
        limit = max(1, min(int(limit), MAX_HISTORY_PAGE_LIMIT))
        try:
            start = int(cursor) if cursor is not None else 0
        except (TypeError, ValueError) as exc:
            raise ValueError("cursor must be a non-negative integer") from exc
        if start < 0:
            raise ValueError("cursor must be a non-negative integer")
        items = self.records[start:start + limit]
        end = start + len(items)
        return {
            "items": items,
            "next_cursor": str(end) if end < len(self.records) else None,
            "total": len(self.records),
        }


def prune_transcripts(directory: str | os.PathLike[str], retention_days: int | None,
                      *, now: float | None = None) -> list[Path]:
    """Delete old JSONL transcripts only when an explicit age is supplied.

    ``None`` is the documented default and keeps everything.
    """
    if retention_days is None:
        return []
    if retention_days < 0:
        raise ValueError("history retention days must be non-negative")
    cutoff = (now if now is not None else _datetime.datetime.now().timestamp()) - retention_days * 86400
    removed: list[Path] = []
    for path in Path(directory).glob("*_microclaw_history.jsonl"):
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed.append(path)
    return removed


def estimate_tokens(messages: list[dict]) -> int:
    """Return a local deterministic token *estimate*, never an exact count."""
    # Four UTF-8 bytes per token is deliberately conservative for ASCII-heavy
    # JSON, plus fixed structural overhead per message.
    payload = json.dumps(jsonable(messages), ensure_ascii=False, separators=(",", ":"))
    return math.ceil(len(payload.encode("utf-8")) / 4) + 8 * len(messages)


def _block_type(block: Any) -> str | None:
    return getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)


def _complete_turn_ends(messages: list[dict]) -> list[int]:
    """Exclusive indexes after completed user-prompt → assistant end turns."""
    ends: list[int] = []
    in_turn = False
    for index, message in enumerate(messages):
        role = message.get("role") if isinstance(message, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        is_prompt = role == "user" and isinstance(content, str)
        if is_prompt:
            in_turn = True
        if role == "assistant" and in_turn and isinstance(content, list):
            if not any(_block_type(block) == "tool_use" for block in content):
                ends.append(index + 1)
                in_turn = False
    return ends


def _walk_pairs(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk_pairs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_pairs(child)


def _without_images(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("type") == "image":
            return "[image omitted]"
        if value.get("type") == "base64" and "data" in value:
            return {k: _without_images(v) for k, v in value.items() if k != "data"}
        return {k: _without_images(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_without_images(v) for v in value]
    return value


def _bounded_checkpoint_value(value: Any) -> Any:
    """Bound retained prose/tool inputs; the audit remains the complete record."""
    if isinstance(value, dict):
        return {k: _bounded_checkpoint_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_bounded_checkpoint_value(v) for v in value]
    if isinstance(value, str) and len(value) > CHECKPOINT_STRING_LIMIT:
        marker = CHECKPOINT_ELISION.format(count=len(value) - CHECKPOINT_STRING_LIMIT)
        return value[:CHECKPOINT_STRING_LIMIT] + marker
    return value


def _checkpoint(messages: list[dict], secrets: Iterable[str] = ()) -> dict:
    artifacts: list[dict] = []
    hashes: list[dict] = []
    decisions: list[str] = []
    actions: list[dict] = []
    seen_artifacts: set[tuple[str, str]] = set()

    plain = jsonable(messages)
    for message in plain:
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, str):
            decisions.append(_bounded_checkpoint_value(content))
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                actions.append({"tool": block.get("name"),
                                "input": _bounded_checkpoint_value(
                                    _without_images(block.get("input", {})))})
            if block.get("type") != "tool_result":
                continue
            payload = block.get("content")
            if isinstance(payload, list):
                payload = next((b.get("text") for b in payload
                                if isinstance(b, dict) and b.get("type") == "text"), None)
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    payload = None
            for key, value in _walk_pairs(payload):
                lower = key.lower()
                if lower == "artifact" and isinstance(value, dict):
                    identity = (str(value.get("kind", "")), str(value.get("path", "")))
                    if identity not in seen_artifacts:
                        artifacts.append(value)
                        seen_artifacts.add(identity)
                elif (lower == "hash" or lower.endswith("_hash") or lower == "sha256") \
                        and isinstance(value, str):
                    hashes.append({"field": key, "value": value})

    structured = {
        # The temporal disclaimer is load-bearing, not boilerplate. The first
        # live compaction run had the model answer "which values came from a
        # tool call THIS turn?" by listing two tools it had called four turns
        # earlier -- the same prompt answered correctly ("none of them") on an
        # uncompacted run. A bare list of tool names in context reads as current
        # activity, so the checkpoint has to say when these things happened.
        "checkpoint_contract": (
            "Summary of EARLIER turns that are no longer shown in full. "
            "Everything here already happened in previous turns and is NOT part "
            "of the current turn: do not report these actions or measurements as "
            "something you did just now, and do not answer a question about the "
            "current turn from this block. It is provenance only, not current "
            "hardware state -- re-read live hardware with the appropriate tool "
            "before any action."
        ),
        "compacted_message_count": len(messages),
        # Exact history remains in the audit. These bounded recent indexes plus
        # the digest keep the checkpoint itself from becoming a second,
        # unbounded transcript.
        "artifact_references_from_earlier_turns": artifacts[-200:],
        "hashes_from_earlier_turns": hashes[-200:],
        "user_decisions_in_earlier_turns": decisions[-100:],
        "completed_actions_in_earlier_turns": actions[-200:],
        "totals": {"artifacts": len(artifacts), "hashes": len(hashes),
                   "decisions": len(decisions), "actions": len(actions)},
        "excluded": ["image bytes", "credentials", "current hardware state"],
        "audit_digest_sha256": hashlib.sha256(
            json.dumps(plain, sort_keys=True, default=json_default).encode("utf-8")
        ).hexdigest(),
    }
    structured = redact_credentials(_without_images(structured), secrets)
    return {
        "role": "user",
        "content": [{"type": "text", "text": "Conversation checkpoint:\n" +
                     json.dumps(structured, ensure_ascii=False, sort_keys=True)}],
    }


class ConversationStore:
    """Full durable audit plus a hysteretic, whole-turn model-context view."""

    def __init__(self, audit: AuditLog, *,
                 high_water_tokens: int = DEFAULT_CONTEXT_HIGH_WATER_TOKENS,
                 low_water_tokens: int = DEFAULT_CONTEXT_LOW_WATER_TOKENS):
        if not 0 < low_water_tokens < high_water_tokens:
            raise ValueError("context low-water must be positive and below high-water")
        self.audit = audit
        self.high_water_tokens = high_water_tokens
        self.low_water_tokens = low_water_tokens
        self._cut = 0
        self._checkpoint: dict | None = None
        self.compaction_count = 0
        self.last_estimated_tokens = 0

    def append(self, message: dict) -> None:
        self.audit.append(message)

    def model_messages(self, full_history: list[dict]) -> list[dict]:
        current = ([self._checkpoint] if self._checkpoint else []) + full_history[self._cut:]
        self.last_estimated_tokens = estimate_tokens(current)
        if self.last_estimated_tokens <= self.high_water_tokens:
            return current

        complete_ends = _complete_turn_ends(full_history)
        # Never fold the two most recent complete turns into the checkpoint.
        # If that floor makes the target impossible, preserve the turns and let
        # the API report its real context limit, just as for an oversized
        # in-flight turn below.
        floor_boundary = (complete_ends[-MIN_RECENT_COMPLETE_TURNS - 1]
                          if len(complete_ends) > MIN_RECENT_COMPLETE_TURNS
                          else self._cut)
        ends = [end for end in complete_ends
                if self._cut < end <= floor_boundary]
        chosen = self._cut
        for end in ends:
            candidate_checkpoint = _checkpoint(full_history[:end], self.audit.secrets)
            candidate = [candidate_checkpoint, *full_history[end:]]
            chosen = end
            if estimate_tokens(candidate) <= self.low_water_tokens:
                break
        if chosen == self._cut:
            # The in-flight/recent turn alone is over budget. Splitting it would
            # break tool integrity, so leave it intact and let the API report its
            # real context limit.
            return current
        self._cut = chosen
        self._checkpoint = _checkpoint(full_history[:chosen], self.audit.secrets)
        self.compaction_count += 1
        compacted = [self._checkpoint, *full_history[chosen:]]
        self.last_estimated_tokens = estimate_tokens(compacted)
        return compacted
