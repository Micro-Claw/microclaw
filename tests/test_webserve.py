"""Tests for `microclaw serve` (design/15 v1).

`build_app` is exercised against a fake session — no Micro-Manager, no Anthropic
call — so these cover the properties that actually matter for a browser endpoint
wired to real hardware: one turn at a time, no cross-origin driving, no key
echoed back, and a refusal to bind beyond localhost without an opt-in.
"""
import contextlib
import json
import socket
import threading
import time
import types

import pytest

from fastapi.testclient import TestClient

from microclaw import config, credentials, tools, webserve
from microclaw.conversation import AuditLog, ConversationStore, load_history
from microclaw.webserve import build_app, serve


class _FakeLock:
    """asyncio.Lock() binds to the loop that first awaits it; TestClient runs
    its own. A stub keeps `locked()` under the test's control."""

    def __init__(self, locked=False):
        self._locked = locked

    def locked(self):
        return self._locked

    async def acquire(self):
        self._locked = True
        return True

    def release(self):
        self._locked = False

    async def __aenter__(self):
        await self.acquire()

    async def __aexit__(self, *exc):
        self.release()


def _guard(workspace_dir=None):
    from microclaw.safety import SafetyConstraints, SafetyGuard

    return SafetyGuard(SafetyConstraints(workspace_dir=workspace_dir))


def _history_declaring(*paths, kind="tiff"):
    """A history in which a tool declared each `path` as an artifact."""
    return [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}",
             "content": json.dumps({"status": "done",
                                    "artifact": {"kind": kind, "path": p}})}
            for i, p in enumerate(paths)
        ]}
    ]


@pytest.fixture
def session():
    tools.SESSION_GRANTS.clear()
    s = types.SimpleNamespace(
        ctrl=object(),
        guard=_guard(),
        model=None,
        history=[],
        history_fn="unused.json",
        save=False,          # never write a history file from a test
        editable=True,
        lock=_FakeLock(),
        cancel=threading.Event(),
        _emit=None,
        pending=None,
        audit_records=[],
        current_identity="loopback",
    )
    # Bind the real confirmation and audit methods so the fake routes exercise
    # exactly the same decision-to-row path as a live Session.
    s._audit_confirmation = webserve.Session._audit_confirmation.__get__(s)
    s.confirm = webserve.Session.confirm.__get__(s)
    yield s
    tools.SESSION_GRANTS.clear()


@pytest.fixture
def client(session, monkeypatch):
    # A key is present unless a test says otherwise.
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("sk-ant-secret-AA8f", "env"))
    return TestClient(build_app(session))


@pytest.fixture
def stored_session(session):
    session.store = ConversationStore(AuditLog(None, enabled=False))
    return session


@pytest.fixture
def stored_client(stored_session, monkeypatch):
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("sk-ant-secret-AA8f", "env"))
    return TestClient(build_app(stored_session))


def _agent_iter(reply="ok", tool_calls=()):
    """A stand-in for run_agent_iter: appends to `messages` in place, as the
    real one does, and yields the event sequence a turn produces."""

    def fake(msg, ctrl, guard, messages, model=None, context_provider=None,
             on_message=None, **kw):
        def append(message):
            messages.append(message)
            if on_message is not None:
                on_message(message)

        append({"role": "user", "content": msg})
        if context_provider is not None:
            context_provider(messages)
        yield {"type": "round_start", "iteration": 0}
        for i, (name, tool_input) in enumerate(tool_calls):
            tid = f"t{i}"
            append({"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": name, "input": tool_input}]})
            yield {"type": "tool_use", "id": tid, "name": name, "input": tool_input}
            append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": "{}"}]})
            yield {"type": "tool_result", "tool_use_id": tid, "content": "{}",
                   "is_error": False}
        append({"role": "assistant", "content": [{"type": "text", "text": reply}]})
        yield {"type": "done", "reply": reply}

    return fake


def _events(res):
    """Parse an SSE response body into the list of event dicts it carried."""
    out = []
    for frame in res.text.split("\n\n"):
        for line in frame.split("\n"):
            if line.startswith("data:"):
                out.append(json.loads(line[5:]))
    return out


def _settle(session, timeout=2.0):
    """Wait for the turn thread to finish and drop the lock."""
    deadline = time.monotonic() + timeout
    while session.lock.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    return not session.lock.locked()


# ---- the page ----

def test_index_is_self_contained(client):
    """Served over HTTP, the page can't resolve transcript.css/js as siblings."""
    html = client.get("/").text
    assert 'href="transcript.css"' not in html
    assert 'src="transcript.js"' not in html
    assert "global.Transcript = {" in html      # transcript.js inlined
    assert "--tool-line:" in html               # transcript.css inlined


def test_index_links_the_favicon(client):
    """The icon is binary, so it is a route rather than an inlined asset."""
    assert '<link rel="icon" type="image/x-icon" href="/favicon.ico">' in client.get("/").text


def test_favicon_is_served(client):
    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/x-icon"
    assert r.content[:4] == b"\x00\x00\x01\x00"      # ICO magic


# ---- history ----

def test_history_starts_empty(client):
    assert client.get("/api/history").json() == {
        "items": [], "next_cursor": None, "total": 0,
    }


def test_history_serialises_sdk_content_blocks(session, client):
    """Assistant turns hold Anthropic SDK objects, not dicts."""
    class Block:
        def model_dump(self):
            return {"type": "text", "text": "hi"}

    session.history = [{"role": "assistant", "content": [Block()]}]
    assert client.get("/api/history").json() == {
        "items": [
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}
        ],
        "next_cursor": None,
        "total": 1,
    }


def test_history_pages_with_cursor_and_does_not_narrow_artifact_scan(session, client):
    session.history = _history_declaring("first.tif", "second.tif", "third.tif")
    # Add ordinary records so the declaration-containing record is not special
    # merely because it is the whole first page.
    session.history.extend([
        {"role": "assistant", "content": [{"type": "text", "text": "one"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
    ])
    first = client.get("/api/history?limit=2").json()
    assert first["total"] == 3
    assert len(first["items"]) == 2
    assert first["next_cursor"] == "2"
    last = client.get("/api/history?limit=2&cursor=2").json()
    assert len(last["items"]) == 1
    assert last["next_cursor"] is None
    assert webserve._declared_artifacts(webserve._durable_history(session)) == {
        "first.tif", "second.tif", "third.tif",
    }


def test_store_backed_history_pages_over_audit_records(stored_session, stored_client):
    for index in range(5):
        stored_session.store.append({"role": "user", "content": f"record {index}"})
    stored_session.history = [{"role": "user", "content": "compatibility decoy"}]

    first = stored_client.get("/api/history?limit=2").json()
    second = stored_client.get("/api/history?limit=2&cursor=2").json()

    assert first == {"items": stored_session.store.audit.records[:2],
                     "next_cursor": "2", "total": 5}
    assert second == {"items": stored_session.store.audit.records[2:4],
                      "next_cursor": "4", "total": 5}


@pytest.mark.parametrize("query", ["cursor=-1", "cursor=nope", "limit=0"])
def test_history_rejects_invalid_paging(query, client):
    assert client.get("/api/history?" + query).status_code == 400


# ---- prompts ----

def test_prompt_streams_events_and_appends_to_history(session, client, monkeypatch):
    monkeypatch.setattr(webserve, "run_agent_iter", _agent_iter("channel set to DAPI"))

    res = client.post("/api/prompt", json={"message": "  set DAPI  "})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert _events(res) == [
        {"type": "round_start", "iteration": 0},
        {"type": "done", "reply": "channel set to DAPI"},
    ]

    # The stripped prompt is what reached the agent, and history is the server's.
    assert session.history[0] == {"role": "user", "content": "set DAPI"}
    assert client.get("/api/history").json()["items"] == session.history


def test_store_backed_prompt_uses_context_provider_and_populates_audit(
    stored_session, stored_client, monkeypatch
):
    monkeypatch.setattr(webserve, "run_agent_iter", _agent_iter("audited"))

    assert stored_client.post("/api/prompt", json={"message": "remember"}).status_code == 200
    assert _settle(stored_session)
    assert stored_session.store.last_estimated_tokens > 0
    assert stored_session.store.audit.records == stored_session.history
    assert [m["role"] for m in stored_session.store.audit.records] == ["user", "assistant"]


def test_tool_lifecycle_reaches_the_browser_one_event_at_a_time(session, client, monkeypatch):
    """The point of the stream: tool cards land as the tools run, not in a lump
    at the end of a 52-second survey (design/16 §1)."""
    monkeypatch.setattr(webserve, "run_agent_iter",
                        _agent_iter("done", tool_calls=[("move_stage_xy", {"x_um": 10})]))

    types_ = [e["type"] for e in _events(client.post("/api/prompt", json={"message": "go"}))]
    assert types_ == ["round_start", "tool_use", "tool_result", "done"]


def test_prompt_passes_the_session_model_through(session, client, monkeypatch):
    seen = {}

    def fake(msg, ctrl, guard, messages, model=None, **kw):
        seen.update(ctrl=ctrl, guard=guard, model=model, messages=messages)
        yield {"type": "done", "reply": "ok"}

    session.model = "claude-opus-4-8"
    monkeypatch.setattr(webserve, "run_agent_iter", fake)
    client.post("/api/prompt", json={"message": "hi"})
    assert seen["ctrl"] is session.ctrl
    assert seen["guard"] is session.guard
    assert seen["model"] == "claude-opus-4-8"
    # The generator gets the session's own list, not a copy — a turn abandoned
    # mid-flight must still leave its completed rounds in the history.
    assert seen["messages"] is session.history


def test_the_lock_is_released_once_the_stream_ends(session, client, monkeypatch):
    monkeypatch.setattr(webserve, "run_agent_iter", _agent_iter())
    client.post("/api/prompt", json={"message": "snap"})
    assert _settle(session), "a finished turn left the session locked"
    assert client.post("/api/prompt", json={"message": "again"}).status_code == 200


def test_an_abandoned_stream_still_finishes_the_turn_and_frees_the_session(
    session, client, monkeypatch
):
    """The stage has already moved. A closed tab must not abort the turn, wedge
    the session at 409, or lose the rounds that already ran."""
    monkeypatch.setattr(webserve, "run_agent_iter",
                        _agent_iter("done", tool_calls=[("snap_image", {})]))

    with client.stream("POST", "/api/prompt", json={"message": "snap"}) as res:
        assert res.status_code == 200
        next(res.iter_lines())          # read one frame, then walk away

    assert _settle(session), "an abandoned stream left the session locked"
    # the whole turn ran, not just the round the client saw
    assert [m["role"] for m in session.history] == ["user", "assistant", "user", "assistant"]
    assert client.post("/api/prompt", json={"message": "again"}).status_code == 200


def test_an_agent_crash_surfaces_as_an_error_event_not_a_dead_stream(
    session, client, monkeypatch
):
    def boom(msg, ctrl, guard, messages, model=None, **kw):
        yield {"type": "round_start", "iteration": 0}
        raise RuntimeError("bridge went away")

    monkeypatch.setattr(webserve, "run_agent_iter", boom)
    events = _events(client.post("/api/prompt", json={"message": "snap"}))
    assert events[-1]["type"] == "error"
    assert "bridge went away" in events[-1]["message"]
    assert _settle(session), "a crashed turn left the session locked"


def test_empty_prompt_is_rejected(client):
    assert client.post("/api/prompt", json={"message": "   "}).status_code == 400


def test_prompt_without_a_key_is_rejected(client, monkeypatch):
    monkeypatch.setattr(credentials, "load_api_key", lambda: (None, None))
    res = client.post("/api/prompt", json={"message": "snap"})
    assert res.status_code == 400
    assert "API key" in res.json()["detail"]


def test_second_turn_is_refused_while_one_runs(session, client):
    """One microscope, one operator: a concurrent prompt gets 409, not
    interleaved tool calls on the hardware."""
    session.lock = _FakeLock(locked=True)
    res = client.post("/api/prompt", json={"message": "snap"})
    assert res.status_code == 409


def test_history_is_written_after_a_turn(session, monkeypatch, tmp_path):
    monkeypatch.setattr(webserve, "run_agent_iter", _agent_iter())
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("k", "env"))
    session.save = True
    session.history_fn = str(tmp_path / "h.json")

    TestClient(build_app(session)).post("/api/prompt", json={"message": "snap"})
    assert _settle(session)
    assert json.loads((tmp_path / "h.json").read_text(encoding="utf-8"))[0]["content"] == "snap"


# ---- stop (v4a) ----

def test_stop_with_no_turn_running_is_a_409(client, session):
    assert client.post("/api/stop").status_code == 409
    assert not session.cancel.is_set()


def test_stop_sets_the_cancel_event_the_agent_polls(session, client):
    session.lock = _FakeLock(locked=True)
    assert client.post("/api/stop").json() == {"stopping": True}
    assert session.cancel.is_set()


def test_a_prompt_clears_a_stale_cancel_flag(session, client, monkeypatch):
    """A Stop from the previous turn must not kill the next one before it starts."""
    monkeypatch.setattr(webserve, "run_agent_iter", _agent_iter())
    session.cancel.set()
    assert client.post("/api/prompt", json={"message": "snap"}).status_code == 200
    assert not session.cancel.is_set()


def test_the_cancel_event_is_handed_to_the_agent(session, client, monkeypatch):
    seen = {}

    def fake(msg, ctrl, guard, messages, model=None, cancel=None, **kw):
        seen["cancel"] = cancel
        yield {"type": "done", "reply": "ok"}

    monkeypatch.setattr(webserve, "run_agent_iter", fake)
    client.post("/api/prompt", json={"message": "hi"})
    assert seen["cancel"] is session.cancel


def test_stop_is_not_a_tool_the_agent_can_call():
    """A UI endpoint, not a tool — the same rule the safety-config editor sets.
    The agent must not be able to stop itself, or to reach any /api endpoint."""
    from microclaw.tools import TOOL_REGISTRY

    assert not any("stop" == name or name.startswith("api_") for name in TOOL_REGISTRY)
    assert "halt" not in TOOL_REGISTRY   # v4b is cancelled, not deferred


# ---- confirmations (design/21 F1) ----

def _start_confirm(session, summary="Save knowledge devices/X:\nX: {a: 1}",
                   kind="knowledge", subject=None):
    """Run session.confirm on a thread, as a tool on the turn thread would.

    Returns once the confirm is pending (or the thread already returned), with
    the thread, the events it emitted, and a box the answer lands in.
    """
    events = []
    session._emit = events.append
    box = {}
    thread = threading.Thread(
        target=lambda: box.update(answer=session.confirm(summary, kind, subject))
    )
    thread.start()
    # Wait for the confirm_request *event*, not for session.pending: confirm()
    # sets pending first and emits second (so a browser can never see an event
    # whose id is not yet answerable), which leaves a window where pending is
    # set and `events` is still empty. Waiting on pending lost that race on the
    # lab machine (2026-07-10 run); event-emitted implies pending-set.
    deadline = time.monotonic() + 5
    while not events and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.005)
    return thread, events, box


@pytest.fixture
def fast_confirm_poll(monkeypatch):
    """test_webserve has no slow tests and should keep none."""
    monkeypatch.setattr(webserve, "CONFIRM_POLL_S", 0.01)


def test_a_confirm_with_no_stream_bound_denies(session):
    # A confirmation that cannot reach the operator must never become a yes.
    assert session._emit is None
    assert session.confirm("Save knowledge x") is False


def test_confirmation_audit_is_durable_jsonl(session, tmp_path):
    path = tmp_path / "confirmations.jsonl"
    session.confirmation_audit = AuditLog(path)
    assert session.confirm("Enable illumination", "illumination") is False
    records = load_history(path).messages
    assert len(records) == 1
    assert records[0]["kind"] == "illumination"
    assert records[0]["decision"] == "declined:no-stream"
    assert records[0]["summary"] == "Enable illumination"


def test_confirmation_audit_summary_uses_secret_redaction(session, tmp_path):
    path = tmp_path / "confirmations.jsonl"
    session.confirmation_audit = AuditLog(path)
    webserve._add_audit_secret(session, "operator-secret")

    assert session.confirm("Save operator-secret", "knowledge") is False

    assert load_history(path).messages[0]["summary"] == "Save [REDACTED]"
    # The same redacted copy must be what the model sees and what stdout
    # prints. audit_records is handed to run_agent_iter and becomes the
    # `confirmations` block in a tool result, and the serve process's stdout is
    # captured into rig evidence bundles -- so an unredacted record here would
    # leak in two directions while the JSONL looked clean.
    assert session.audit_records[-1]["summary"] == "Save [REDACTED]"


def test_the_browser_can_approve_a_pending_confirm(session, client, fast_confirm_poll):
    thread, events, box = _start_confirm(session)
    pid = session.pending.id
    assert events[0] == {"type": "confirm_request", "id": pid,
                         "summary": "Save knowledge devices/X:\nX: {a: 1}",
                         "kind": "knowledge", "subject": None,
                         "grantable": False}

    assert client.post("/api/confirm",
                       json={"id": pid, "approve": True}).status_code == 200
    thread.join(timeout=5)
    assert box["answer"] is True
    assert session.pending is None
    assert events[-1] == {"type": "confirm_resolved", "id": pid}


def test_the_browser_can_decline_a_pending_confirm(session, client, fast_confirm_poll):
    thread, _, box = _start_confirm(session)
    client.post("/api/confirm", json={"id": session.pending.id, "approve": False})
    thread.join(timeout=5)
    assert box["answer"] is False


def test_browser_session_grant_auto_audits_and_revoke_restores_prompting(
    session, client, fast_confirm_poll, tmp_path
):
    path = tmp_path / "confirmations.jsonl"
    session.confirmation_audit = AuditLog(path)
    thread, _, box = _start_confirm(
        session, "enable 488", kind="illumination", subject="enable"
    )
    pid = session.pending.id
    approval = client.post(
        "/api/confirm", json={"id": pid, "approve": "session"}
    )
    assert approval.status_code == 200
    # The POST that disables future prompts returns the chip state itself; the
    # UI does not race a later poll against the turn thread.
    assert len(approval.json()["grants"]) == 1
    thread.join(timeout=5)
    assert box["answer"] is True
    grant = tools.SESSION_GRANTS.active()[0]
    lifecycle = load_history(path).messages[:2]
    assert lifecycle[0]["decision"] == f"granted:{grant['id']}"
    assert lifecycle[1]["decision"] == f"approved:session:{grant['id']}"

    assert session.confirm("enable 561", "illumination", "enable") is True
    auto = session.audit_records[-1]
    assert auto["decision"] == f"auto-approved:{grant['id']}"
    assert auto["grant_id"] == grant["id"]
    assert load_history(path).messages[-1] == auto

    response = client.post(
        "/api/confirm", json={"id": grant["id"], "approve": "revoke"}
    )
    assert response.status_code == 200
    assert response.json()["grants"] == []
    revoke = load_history(path).messages[-1]
    assert revoke["decision"] == f"revoked:{grant['id']}"
    assert revoke["grant_id"] == grant["id"]
    thread, _, box = _start_confirm(
        session, "enable 488", kind="illumination", subject="enable"
    )
    assert session.pending is not None
    client.post("/api/confirm", json={"id": session.pending.id, "approve": False})
    thread.join(timeout=5)
    assert box["answer"] is False


def test_session_grant_creation_is_audited_even_if_the_turn_is_stopped(
    session, client, tmp_path
):
    path = tmp_path / "confirmations.jsonl"
    session.confirmation_audit = AuditLog(path)
    session.cancel.set()
    # Model the endpoint-visible window after a confirmation became pending.
    # No turn-thread decision is needed to prove the lifecycle row is owned by
    # the POST that creates the standing grant.
    session.pending = webserve._Pending(
        "pending-id", "enable 488", "illumination", "enable"
    )

    response = client.post(
        "/api/confirm", json={"id": "pending-id", "approve": "session"}
    )

    assert response.status_code == 200
    grant = tools.SESSION_GRANTS.active()[0]
    records = load_history(path).messages
    assert [record["decision"] for record in records] == [f"granted:{grant['id']}"]
    assert records[0]["grant_id"] == grant["id"]


def test_browser_page_exposes_session_approval_and_persistent_revoke_controls(client):
    page = client.get("/").text
    assert 'id="confirm-session"' in page
    assert 'id="grant-chips"' in page
    assert 'approve: "revoke"' in page


def test_auto_approval_audits_acting_operator_and_grant_author_separately(session):
    grant = tools.SESSION_GRANTS.grant(
        "illumination", "enable", "enable 488", identity="grant-author"
    )
    session.current_identity = "acting-operator"

    assert session.confirm("enable 561", "illumination", "enable") is True

    record = session.audit_records[-1]
    assert record["identity"] == "acting-operator"
    assert record["grant_identity"] == "grant-author"
    assert record["grant_id"] == grant["id"]


def test_a_stale_confirm_id_is_a_409(session, client, fast_confirm_poll):
    # A banner left over from a previous confirm must not answer this one.
    thread, _, box = _start_confirm(session)
    pid = session.pending.id
    res = client.post("/api/confirm", json={"id": "stale-id", "approve": True})
    assert res.status_code == 409
    assert session.pending is not None      # still waiting on the right answer

    client.post("/api/confirm", json={"id": pid, "approve": False})
    thread.join(timeout=5)
    assert box["answer"] is False


def test_confirm_with_nothing_pending_is_a_409(client):
    assert client.post("/api/confirm",
                       json={"id": "anything", "approve": True}).status_code == 409


def test_get_confirm_resurfaces_a_pending_banner(session, client, fast_confirm_poll):
    """confirm_request is delivered exactly once, on a stream a page reload
    destroys. GET /api/confirm is how the reloaded page finds the banner
    again instead of stranding the turn until the deadline."""
    thread, _, box = _start_confirm(session, kind="illumination")
    pid = session.pending.id

    body = client.get("/api/confirm").json()
    assert body == {"id": pid, "summary": "Save knowledge devices/X:\nX: {a: 1}",
                    "kind": "illumination", "subject": None,
                    "grantable": False, "grants": []}

    client.post("/api/confirm", json={"id": pid, "approve": False})
    thread.join(timeout=5)
    assert box["answer"] is False
    assert client.get("/api/confirm").json() == {"grants": []}


def test_stop_during_a_pending_confirm_denies(session, client, fast_confirm_poll):
    session.lock = _FakeLock(locked=True)   # a turn is running
    thread, events, box = _start_confirm(session)

    assert client.post("/api/stop").json() == {"stopping": True}
    thread.join(timeout=5)
    assert box["answer"] is False
    assert session.pending is None
    assert events[-1]["type"] == "confirm_resolved"


def test_an_unanswered_confirm_denies_at_the_deadline(session, monkeypatch):
    # An injected clock, not a sleep: the first read anchors the deadline, every
    # later read is already past it.
    ticks = iter([0.0])
    monkeypatch.setattr(webserve, "_monotonic", lambda: next(ticks, 1e9))
    session._emit = lambda event: None
    assert session.confirm("Save knowledge x") is False
    assert session.pending is None


def test_run_turn_binds_the_emit_channel_for_confirmations(session, client, monkeypatch):
    """session.confirm routes through whichever stream the running turn owns;
    outside a turn there is no stream and confirm() must default-deny."""
    seen = {}

    def fake(msg, ctrl, guard, messages, model=None, **kw):
        seen["emit_bound"] = session._emit is not None
        seen["confirmation_records"] = kw["confirmation_records"]
        yield {"type": "done", "reply": "ok"}

    monkeypatch.setattr(webserve, "run_agent_iter", fake)
    client.post("/api/prompt", json={"message": "hi"})
    assert seen["emit_bound"] is True
    assert seen["confirmation_records"] is session.audit_records
    assert _settle(session)
    assert session._emit is None            # cleared in run_turn's finally


def test_serve_wires_confirm_and_flushes_startup_banner(monkeypatch):
    """Assert the connection, not a stub: the injection point existed, was
    documented, and was never connected to anything (design/21 F1). A test
    that only stubs CONFIRM_FN cannot notice that."""
    import uvicorn

    from microclaw import tools

    # Register restoration of the module global serve() is about to overwrite.
    monkeypatch.setattr(tools, "CONFIRM_FN", tools.CONFIRM_FN)
    fake = types.SimpleNamespace(
        confirm=lambda summary, kind="action": False,
        history_fn="unused.json", history=[], save=False,
        guard=_guard(), ctrl=types.SimpleNamespace(core=None),
    )
    monkeypatch.setattr(webserve, "Session", lambda args: fake)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    printed = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append((a, k)))

    serve(_args(host="127.0.0.1"))
    assert tools.CONFIRM_FN is fake.confirm
    banner = next(item for item in printed if "Microclaw GUI:" in item[0][0])
    assert banner[1].get("flush") is True


# ---- model (v4c) ----

@pytest.fixture
def no_model_fetch(monkeypatch):
    """GET /api/model calls known_models(), which would hit the real API."""
    monkeypatch.setattr(webserve, "known_models", lambda: ["claude-opus-4-8", "claude-haiku-4-5-20251001"])


def test_get_model_reports_the_resolved_model_and_suggestions(client, no_model_fetch, monkeypatch):
    monkeypatch.delenv("MICROCLAW_MODEL", raising=False)
    body = client.get("/api/model").json()
    assert body["model"] == body["default"]        # session.model is None
    assert "claude-opus-4-8" in body["available"]
    assert body["editable"] is True


def test_post_model_sets_it_for_the_session(session, client, no_model_fetch):
    assert client.post("/api/model", json={"model": " my-model "}).json() == {"model": "my-model"}
    assert session.model == "my-model"


def test_model_cannot_be_swapped_mid_turn(session, client, no_model_fetch):
    """Prompt caching is scoped per model; swapping mid-turn is also a different
    model answering half a conversation."""
    session.lock = _FakeLock(locked=True)
    assert client.post("/api/model", json={"model": "other"}).status_code == 409
    assert session.model is None


def test_empty_model_is_rejected(client, no_model_fetch):
    assert client.post("/api/model", json={"model": "  "}).status_code == 400


def test_post_model_is_refused_when_bound_beyond_localhost(session, no_model_fetch, monkeypatch):
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("k", "env"))
    session.editable = False
    res = TestClient(build_app(session)).post("/api/model", json={"model": "x"})
    assert res.status_code == 403


# ---- artifacts (v4d) ----

def test_a_file_no_tool_declared_is_refused(session, client, tmp_path):
    """Capability, not location: the allowlist is what this session's tools
    wrote, so an undeclared file is refused even though it plainly exists and no
    workspace is configured."""
    secret = tmp_path / "id_rsa"
    secret.write_text("PRIVATE KEY", encoding="utf-8")

    res = client.get("/api/artifact", params={"path": str(secret)})
    assert res.status_code == 403
    assert "Not an artifact" in res.json()["detail"]


def test_a_declared_artifact_downloads_with_no_workspace_configured(session, client, tmp_path):
    """The operator saves data wherever they like; the download still works."""
    data = tmp_path / "zstack_1.tiff"
    data.write_text("II*\0fake tiff", encoding="utf-8")
    session.history = _history_declaring(str(data))   # session.guard has no workspace root

    res = client.get("/api/artifact", params={"path": str(data)})
    assert res.status_code == 200
    assert res.text == "II*\0fake tiff"
    # Never a sniffed type: an artifact that happens to be HTML, served inline
    # from this origin, is script execution against the endpoint driving the stage.
    assert res.headers["content-type"] == "application/octet-stream"
    assert res.headers["content-disposition"] == 'attachment; filename="zstack_1.tiff"'


def test_store_backed_artifact_survives_compaction_out_of_model_view(
    stored_session, stored_client, tmp_path
):
    data = tmp_path / "old.tiff"
    data.write_text("old pixels", encoding="utf-8")
    history = [
        {"role": "user", "content": "make artifact"},
        _history_declaring(str(data))[0],
        {"role": "assistant", "content": [{"type": "text", "text": "made it"}]},
    ]
    for index in range(5):
        history.extend([
            {"role": "user", "content": f"later {index}"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "x" * 1000},
            ]},
        ])
    stored_session.store = ConversationStore(
        AuditLog(None, enabled=False), high_water_tokens=1000, low_water_tokens=600,
    )
    stored_session.history = history
    for message in history:
        stored_session.store.append(message)
    model_view = stored_session.store.model_messages(history)
    assert history[1] not in model_view

    response = stored_client.get("/api/artifact", params={"path": str(data)})

    assert response.status_code == 200
    assert response.text == "old pixels"


def test_a_sibling_of_a_declared_artifact_is_still_refused(session, client, tmp_path):
    """A directory sandbox would have served this. An exact match does not."""
    (tmp_path / "ok.json").write_text("{}", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    session.history = _history_declaring(str(tmp_path / "ok.json"))

    assert client.get("/api/artifact",
                      params={"path": str(tmp_path / "secret.txt")}).status_code == 403


def test_a_configured_workspace_still_applies_to_a_declared_artifact(session, client, tmp_path):
    """The endpoint may not become a way around a workspace a lab did configure."""
    (tmp_path / "ws").mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    session.guard = _guard(str(tmp_path / "ws"))
    session.history = _history_declaring(str(outside))

    res = client.get("/api/artifact", params={"path": str(outside)})
    assert res.status_code == 403
    assert "escapes" in res.json()["detail"]


def test_a_declared_artifact_that_was_deleted_is_a_404(session, client, tmp_path):
    session.history = _history_declaring(str(tmp_path / "gone.json"))
    assert client.get("/api/artifact",
                      params={"path": str(tmp_path / "gone.json")}).status_code == 404


def test_artifact_is_refused_when_bound_beyond_localhost(session, tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("k", "env"))
    path = tmp_path / "x.json"
    path.write_text("{}", encoding="utf-8")
    session.history = _history_declaring(str(path))
    session.editable = False
    res = TestClient(build_app(session)).get("/api/artifact", params={"path": str(path)})
    assert res.status_code == 403


class TestDeclaredArtifacts:
    def test_it_reads_the_paths_tools_declared(self):
        assert webserve._declared_artifacts(
            _history_declaring("/a/x.tif", "/b/y.json")
        ) == {"/a/x.tif", "/b/y.json"}

    def test_a_path_merely_mentioned_in_a_result_is_not_an_artifact(self):
        history = [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t0",
             "content": json.dumps({"status": "Saved to /etc/passwd."})}]}]
        assert webserve._declared_artifacts(history) == set()

    def test_it_survives_non_json_and_image_block_results(self):
        history = [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t0", "content": "not json"},
            {"type": "tool_result", "tool_use_id": "t1", "content": [
                {"type": "text", "text": json.dumps(
                    {"artifact": {"kind": "tiff", "path": "/a/x.tif"}})},
                {"type": "image", "source": {"type": "base64", "data": "AA"}},
            ]},
        ]}]
        assert webserve._declared_artifacts(history) == {"/a/x.tif"}

    def test_assistant_turns_holding_sdk_blocks_do_not_crash_it(self):
        class Block:  # an SDK content block, not a dict
            type = "tool_use"

        assert webserve._declared_artifacts(
            [{"role": "assistant", "content": [Block()]},
             {"role": "user", "content": "a plain string"}]
        ) == set()


# ---- origin ----

def test_cross_origin_prompt_is_refused(client):
    """A page the operator has open in another tab must not drive the stage."""
    res = client.post(
        "/api/prompt",
        json={"message": "snap"},
        headers={"Origin": "https://evil.example"},
    )
    assert res.status_code == 403


def test_same_origin_prompt_is_allowed(client, monkeypatch):
    monkeypatch.setattr(webserve, "run_agent_iter", _agent_iter())
    res = client.post(
        "/api/prompt",
        json={"message": "snap"},
        headers={"Origin": "http://testserver"},
    )
    assert res.status_code == 200


# ---- api key ----

def test_get_key_reports_a_suffix_never_the_key(client):
    body = client.get("/api/key").json()
    assert body["has_key"] is True
    assert body["suffix"] == "…AA8f"
    assert "sk-ant-secret-AA8f" not in json.dumps(body)


def test_get_key_when_unset(client, monkeypatch):
    monkeypatch.setattr(credentials, "load_api_key", lambda: (None, None))
    body = client.get("/api/key").json()
    assert body == {"has_key": False, "suffix": None, "source": None, "editable": True}


def test_post_key_sets_and_persists(client, monkeypatch):
    stored = {}
    monkeypatch.setattr(webserve, "set_api_key", lambda k: stored.update(live=k))
    monkeypatch.setattr(credentials, "store_api_key", lambda k: ("keyring", None))

    body = client.post("/api/key", json={"key": " sk-ant-xyzXYZW ", "persist": True}).json()
    assert stored["live"] == "sk-ant-xyzXYZW"
    assert body["stored_in"] == "keyring"
    assert body["suffix"] == "…XYZW"
    assert "sk-ant-xyzXYZW" not in json.dumps(body)


def test_post_key_can_replace_a_key_already_set(client, monkeypatch):
    """The chip in the header reopens the banner; the endpoint overwrites."""
    stored = {}
    monkeypatch.setattr(webserve, "set_api_key", lambda k: stored.update(live=k))
    monkeypatch.setattr(credentials, "store_api_key", lambda k: ("keyring", None))

    body = client.post("/api/key", json={"key": "sk-ant-NEWKEY9", "persist": True}).json()
    assert stored["live"] == "sk-ant-NEWKEY9"
    assert body["suffix"] == "…KEY9"


def test_post_key_without_persist_does_not_store(client, monkeypatch):
    monkeypatch.setattr(webserve, "set_api_key", lambda k: None)
    monkeypatch.setattr(credentials, "store_api_key", lambda k: pytest.fail("persisted"))
    monkeypatch.setattr(credentials, "load_stored_key", lambda: (None, None))

    body = client.post("/api/key", json={"key": "sk-ant-1234", "persist": False}).json()
    assert body["stored_in"] is None
    assert body["stale_store"] is False


def test_a_session_only_key_reports_the_stale_store(client, monkeypatch):
    """Setting a key for this process doesn't displace an older persisted one,
    which would silently come back on the next start. Say so."""
    monkeypatch.setattr(webserve, "set_api_key", lambda k: None)
    monkeypatch.setattr(credentials, "load_stored_key", lambda: ("sk-ant-OLD", "keyring"))

    body = client.post("/api/key", json={"key": "sk-ant-1234", "persist": False}).json()
    assert body["stale_store"] is True


def test_no_stale_warning_when_the_store_already_holds_this_key(client, monkeypatch):
    monkeypatch.setattr(webserve, "set_api_key", lambda k: None)
    monkeypatch.setattr(credentials, "load_stored_key", lambda: ("sk-ant-1234", "keyring"))

    body = client.post("/api/key", json={"key": "sk-ant-1234", "persist": False}).json()
    assert body["stale_store"] is False


def test_post_key_is_refused_when_bound_beyond_localhost(session, monkeypatch):
    """Remote hardware control is a loud opt-in; remote credential editing is
    not on offer at all."""
    monkeypatch.setattr(credentials, "load_api_key", lambda: (None, None))
    session.editable = False
    res = TestClient(build_app(session)).post("/api/key", json={"key": "sk-ant-1234"})
    assert res.status_code == 403


# ---- binding ----

def _args(**kw):
    base = dict(host="0.0.0.0", web_port=8000, allow_remote=False, no_browser=True,
                behind_tls_proxy=False, safety_config="x.yaml", port=4827,
                model=None, save_history=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


@contextlib.contextmanager
def _free_port():
    """A port nothing is listening on."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    yield port


def test_browser_opens_only_once_the_port_accepts(monkeypatch):
    """A browser fired before uvicorn is listening lands on connection-refused."""
    opened = []
    monkeypatch.setattr(webserve.webbrowser, "open", opened.append)

    # The worker's deadline must measure its own polling work, not time it was
    # starved by a loaded suite. Advancing this clock only from the worker's
    # sleep makes scheduler delay irrelevant without widening the timeout.
    elapsed = 0.0
    lock = threading.Lock()

    def clock():
        with lock:
            return elapsed

    def worker_sleep(seconds):
        nonlocal elapsed
        time.sleep(seconds)
        with lock:
            elapsed += seconds

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    # Defined before the try so the finally cannot raise NameError over the top
    # of a real assertion failure earlier in the block.
    stop_accept = threading.Event()
    acceptor = None
    try:
        thread = webserve._open_when_ready(
            "127.0.0.1", port, f"http://127.0.0.1:{port}",
            clock=clock, sleep=worker_sleep,
        )
        time.sleep(0.3)
        assert opened == []          # bound, but not accepting yet

        sock.listen(1)
        # ACCEPT, as uvicorn does. A listener that never accepts is not a model
        # of the server this polls for: its backlog fills after the first
        # connection, and every later connect then blocks for the full
        # create_connection timeout instead of completing. Measured on the demo
        # machine 2026-08-12 with design/35-webserve-flake-probe.py: under load,
        # 1 round in 40 spent 20.19 s of wall time to advance the worker's own
        # clock by only 2.3 s of its 15 s budget — stuck in connect, nowhere
        # near its deadline. Accepting removes the stall; the same probe run
        # with --accept is clean.

        def accept_loop():
            sock.settimeout(0.1)
            while not stop_accept.is_set():
                try:
                    conn, _ = sock.accept()
                except Exception:
                    continue
                conn.close()

        acceptor = threading.Thread(target=accept_loop, daemon=True)
        acceptor.start()
        # join, not a fixed poll budget: a poll thread cannot be observed by
        # sleeping for an interval and hoping it was scheduled.
        thread.join(timeout=20)
        assert not thread.is_alive(), "the poll thread never noticed the listen()"
    finally:
        stop_accept.set()
        if acceptor is not None:
            acceptor.join(timeout=2)   # join before close, as test_bridge_check does
        sock.close()
    assert opened == [f"http://127.0.0.1:{port}"]


def test_browser_opener_gives_up_instead_of_hanging(monkeypatch):
    """A server that never comes up must not leave a thread spinning forever."""
    opened = []
    monkeypatch.setattr(webserve.webbrowser, "open", opened.append)
    with _free_port() as port:
        thread = webserve._open_when_ready(
            "127.0.0.1", port, "http://unused", timeout=0.2
        )
    thread.join(timeout=10)
    assert not thread.is_alive(), "the opener outlived its own timeout"
    assert opened == []


def test_serve_refuses_a_non_local_bind_without_allow_remote():
    with pytest.raises(SystemExit, match="Refusing to bind"):
        serve(_args())


def test_serve_without_a_safety_config_falls_back_to_the_per_user_default(tmp_path, monkeypatch):
    """No --safety-config is what a desktop shortcut passes (design/17 v3).

    It means the reviewed per-user profile, not "no limits". Absent, serve must
    refuse and point at first-launch setup rather than start unguarded.
    """
    missing = tmp_path / "safety_config.yaml"
    monkeypatch.setattr(config, "default_safety_config", lambda: missing)
    with pytest.raises(SystemExit, match="microclaw serve"):
        serve(_args(host="127.0.0.1", safety_config=None))


def test_serve_refuses_an_unreviewed_safety_config(tmp_path):
    """The gate a double-click cannot get past without a human editing a line."""
    cfg = tmp_path / "safety_config.yaml"
    cfg.write_text(
        "schema_version: 3\nreviewed: false\n"
        "property_authorization: {mode: guaranteed, allowed_categorical: [], denied: []}\n"
        "stage: {x_min: -1.0, x_max: 1.0}\n"
        "acquisition: {max_frames: 10000, max_duration_s: 3600, max_bytes: 50000000000, "
        "max_illuminated_ms: 600000, max_session_illuminated_ms: 1800000, "
        "confirm_above_frames: 500, confirm_above_duration_s: 300, "
        "confirm_above_bytes: 5000000000, confirm_above_illuminated_ms: 60000}\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="has not been reviewed"):
        serve(_args(host="127.0.0.1", safety_config=str(cfg)))


# ---- remote authentication (design/32 Finding 3) ----

REMOTE_TOKEN = "t" * 32
TLS = {"X-Forwarded-Proto": "https"}


@pytest.fixture
def remote(session, monkeypatch):
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("sk-ant-test", "env"))
    monkeypatch.setattr(webserve, "known_models", lambda: [])
    session.editable = False
    state = webserve.RemoteAuth(REMOTE_TOKEN)
    app = build_app(session, remote=True, api_token=REMOTE_TOKEN,
                    behind_tls_proxy=True, auth_state=state)
    return TestClient(app, base_url="https://testserver"), state


def _bearer():
    return {**TLS, "Authorization": f"Bearer {REMOTE_TOKEN}"}


def _paired_client(app_client, state):
    code = state.mint_code()
    assert app_client.post("/api/pair", headers=TLS, json={"code": code}).status_code == 200
    return app_client


@pytest.mark.parametrize(("method", "path", "body", "accepted"), [
    ("get", "/api/history", None, 200),
    ("post", "/api/prompt", {"message": " "}, 400),
    ("post", "/api/stop", None, 409),
    ("get", "/api/confirm", None, 200),
    ("post", "/api/confirm", {"id": "none", "approve": False}, 409),
    ("get", "/api/model", None, 200),
    ("post", "/api/model", {"model": "x"}, 403),
    ("get", "/api/key", None, 200),
    ("post", "/api/key", {"key": "x"}, 403),
    ("get", "/api/artifact?path=none", None, 403),
])
def test_every_remote_api_route_accepts_bearer_and_cookie(
    remote, method, path, body, accepted
):
    client, state = remote
    def call(headers):
        return client.request(method.upper(), path, headers=headers, json=body)
    assert call(TLS).status_code == 401
    assert call(_bearer()).status_code == accepted
    _paired_client(client, state)
    assert call(TLS).status_code == accepted


def test_public_page_and_favicon_load_before_pairing(remote):
    client, _ = remote
    assert client.get("/", headers=TLS).status_code == 200
    assert client.get("/favicon.ico", headers=TLS).status_code == 200


def test_missing_origin_does_not_bypass_auth_and_auth_does_not_replace_csrf(remote):
    client, _ = remote
    assert client.get("/api/history", headers=TLS).status_code == 401
    assert client.get("/api/history", headers=_bearer()).status_code == 200
    forged = {**_bearer(), "Origin": "https://evil.example"}
    assert client.get("/api/history", headers=forged).status_code == 403


def test_expected_proxy_origin_is_accepted_for_bearer_and_cookie(remote):
    client, state = remote
    origin = {**_bearer(), "Origin": "https://testserver"}
    # Empty prompt reaches the endpoint's own 400, proving both middleware
    # gates accepted the state-changing request.
    assert client.post(
        "/api/prompt", headers=origin, json={"message": " "}
    ).status_code == 400
    _paired_client(client, state)
    assert client.post(
        "/api/prompt", headers={**TLS, "Origin": "https://testserver"},
        json={"message": " "},
    ).status_code == 400


def test_expected_proxy_origin_with_public_nondefault_port_is_accepted(
    session, monkeypatch
):
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("k", "env"))
    state = webserve.RemoteAuth(REMOTE_TOKEN)
    client = TestClient(
        build_app(session, remote=True, api_token=REMOTE_TOKEN,
                  behind_tls_proxy=True, auth_state=state),
        base_url="https://lab.example.org:8443",
    )
    headers = {**_bearer(), "Origin": "https://lab.example.org:8443"}
    assert client.post(
        "/api/prompt", headers=headers, json={"message": " "}
    ).status_code == 400


def test_authorization_bearer_none_is_refused(remote):
    client, _ = remote
    assert client.get("/api/history", headers={**TLS, "Authorization": "Bearer None"}).status_code == 401


def test_wrong_same_length_bearer_is_refused_and_correct_one_accepted(remote):
    client, _ = remote
    wrong = {**TLS, "Authorization": "Bearer " + "x" * len(REMOTE_TOKEN)}
    assert client.get("/api/history", headers=wrong).status_code == 401
    assert client.get("/api/history", headers=_bearer()).status_code == 200


def test_pairing_cookie_attributes_replay_expiry_and_server_isolation(session, monkeypatch):
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("k", "env"))
    now = [100.0]
    monkeypatch.setattr(webserve, "_monotonic", lambda: now[0])
    one = webserve.RemoteAuth(REMOTE_TOKEN)
    two = webserve.RemoteAuth(REMOTE_TOKEN)
    c1 = TestClient(build_app(session, remote=True, api_token=REMOTE_TOKEN,
                              behind_tls_proxy=True, auth_state=one), base_url="https://one")
    code = one.mint_code()
    r = c1.post("/api/pair", headers=TLS, json={"code": code})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    for attribute in ("httponly", "samesite=strict", "secure", "path=/"):
        assert attribute in cookie
    assert f"max-age={webserve.SESSION_TTL_S}" in cookie
    assert "expires=" in cookie
    assert c1.post("/api/pair", headers=TLS, json={"code": code}).status_code == 401
    expired = one.mint_code()
    now[0] += webserve.PAIR_TTL_S + 1
    assert c1.post("/api/pair", headers=TLS, json={"code": expired}).status_code == 401
    foreign = two.mint_code()
    assert c1.post("/api/pair", headers=TLS, json={"code": foreign}).status_code == 401


def test_pair_code_endpoint_is_bearer_only(remote):
    client, state = remote
    _paired_client(client, state)
    client.headers.update(TLS)
    assert client.post("/api/pair/code").status_code == 401
    r = client.post("/api/pair/code", headers=_bearer())
    assert r.status_code == 200
    assert isinstance(r.json()["code"], str)


def test_auth_failure_rate_limit_trips_then_recovers(remote, monkeypatch):
    client, _ = remote
    now = [10.0]
    monkeypatch.setattr(webserve, "_monotonic", lambda: now[0])
    for _ in range(webserve.RATE_MAX_FAILURES):
        assert client.get("/api/history", headers=TLS).status_code == 401
    assert client.get("/api/history", headers=TLS).status_code == 429
    now[0] += webserve.RATE_WINDOW_S
    assert client.get("/api/history", headers=TLS).status_code == 401


def test_pair_attempt_rate_limit_trips_then_recovers(remote, monkeypatch):
    client, _ = remote
    now = [20.0]
    monkeypatch.setattr(webserve, "_monotonic", lambda: now[0])
    for _ in range(webserve.RATE_MAX_PAIR_ATTEMPTS):
        assert client.post("/api/pair", headers=TLS, json={"code": "wrong"}).status_code == 401
    assert client.post("/api/pair", headers=TLS, json={"code": "wrong"}).status_code == 429
    now[0] += webserve.RATE_WINDOW_S
    assert client.post("/api/pair", headers=TLS, json={"code": "wrong"}).status_code == 401


def test_prompt_and_global_json_body_limits_return_413(remote):
    client, _ = remote
    assert client.post("/api/prompt", headers=_bearer(),
                       content=b"x" * (webserve.PROMPT_BODY_LIMIT + 1)).status_code == 413
    assert client.post("/api/confirm", headers=_bearer(),
                       content=b"x" * (webserve.GLOBAL_JSON_LIMIT + 1)).status_code == 413


def test_loopback_mode_requires_no_auth_or_proxy(session, monkeypatch):
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("k", "env"))
    local = TestClient(build_app(session))
    assert local.get("/api/history").status_code == 200
    assert local.get("/api/key").status_code == 200
    assert local.post("/api/pair", json={"code": "anything"}).status_code == 404


def test_remote_request_without_forwarded_https_is_refused(remote):
    client, _ = remote
    assert client.get("/").status_code == 403


def test_serve_refuses_remote_cleartext_and_invalid_proxy_combinations(monkeypatch):
    with pytest.raises(SystemExit, match="cleartext"):
        serve(_args(allow_remote=True, behind_tls_proxy=False))
    with pytest.raises(SystemExit, match="requires --allow-remote"):
        serve(_args(host="127.0.0.1", behind_tls_proxy=True))
    monkeypatch.setenv("MICROCLAW_REMOTE_TOKEN", "short")
    with pytest.raises(SystemExit, match="at least 32"):
        serve(_args(allow_remote=True, behind_tls_proxy=True))


def _stub_serve_runtime(monkeypatch):
    fake = types.SimpleNamespace(
        history=[], history_fn="unused.json", save=False,
        confirm=lambda *args: False,
        guard=types.SimpleNamespace(
            shutter_all=lambda core: [],
            declared_illumination_state=lambda core: [],
        ),
        ctrl=types.SimpleNamespace(core=object()),
    )
    monkeypatch.setattr(webserve, "Session", lambda args: fake)
    monkeypatch.setattr(webserve, "build_app", lambda *args, **kwargs: object())
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    return fake


def test_remote_startup_prints_real_bind_token_and_pair_code_without_opening(
    monkeypatch, capsys
):
    _stub_serve_runtime(monkeypatch)
    token = "operator-token-which-is-at-least-32-characters"
    monkeypatch.setenv("MICROCLAW_REMOTE_TOKEN", token)
    monkeypatch.setattr(webserve.secrets, "token_urlsafe", lambda size: "pair-code")
    opened = []
    monkeypatch.setattr(webserve, "_open_when_ready", lambda *args: opened.append(args))

    serve(_args(allow_remote=True, behind_tls_proxy=True, no_browser=False))

    output = capsys.readouterr().out
    assert "Listening (cleartext) on http://0.0.0.0:8000" in output
    assert token in output
    assert "/#pair=pair-code" in output
    assert "https://0.0.0.0:8000" not in output
    assert "operator-supplied TLS proxy URL" in output
    assert opened == []


def test_loopback_startup_still_auto_opens_its_http_url(monkeypatch):
    _stub_serve_runtime(monkeypatch)
    opened = []
    monkeypatch.setattr(webserve, "_open_when_ready", lambda *args: opened.append(args))

    serve(_args(host="127.0.0.1", no_browser=False))

    assert opened == [("127.0.0.1", 8000, "http://127.0.0.1:8000")]


def test_web_exit_reads_declared_illumination_without_shuttering(monkeypatch):
    fake = _stub_serve_runtime(monkeypatch)
    reads = []
    writes = []
    fake.guard.declared_illumination_state = lambda core: reads.append(core) or []
    fake.guard.shutter_all = lambda core: writes.append(core)
    serve(_args(host="127.0.0.1", no_browser=True))
    assert reads == [fake.ctrl.core]
    assert writes == []


def test_remote_confirmation_audit_carries_identity(session, remote, fast_confirm_poll):
    client, _ = remote
    thread, _, box = _start_confirm(session, kind="illumination")
    pid = session.pending.id
    assert client.post("/api/confirm", headers=_bearer(),
                       json={"id": pid, "approve": True}).status_code == 200
    thread.join(timeout=5)
    assert box["answer"] is True
    assert session.audit_records[-1]["identity"] == "bearer"
    assert session.audit_records[-1]["confirmation_id"] == pid
    assert session.audit_records[-1]["kind"] == "illumination"
    assert session.audit_records[-1]["decision"] == "approved"
    assert session.audit_records[-1]["timestamp"]
