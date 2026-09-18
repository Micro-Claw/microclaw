"""Tests for `microclaw serve` (design/15 v1).

`build_app` is exercised against a fake session — no Micro-Manager, no Anthropic
call — so these cover the properties that actually matter for a browser endpoint
wired to real hardware: one turn at a time, no cross-origin driving, no key
echoed back, and a refusal to bind beyond localhost without an opt-in.
"""
import asyncio
import builtins
import contextlib
import json
import os
import socket
import threading
import time
import types
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from microclaw import config, credentials, tools, updates, webserve
from microclaw.conversation import AuditLog, ConversationStore, load_history
from microclaw.tools_schema import TOOLS_CACHED
from microclaw.webserve import build_app, serve


def _stub_uvicorn_server(monkeypatch, events=None):
    import uvicorn
    seen = []

    class Server:
        def __init__(self, config):
            self.config = config
            self.should_exit = False
            seen.append(self)

        def run(self):
            if events is not None:
                events.append("run")

    monkeypatch.setattr(uvicorn, "Server", Server)
    return seen


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
        last_resolution=None,
        current_turn_id=None,
        audit_records=[],
        current_identity="loopback",
        # A fake session stands in for a normal one, so it carries the same
        # dispatch attributes. These are read directly rather than through a
        # `getattr` default, because the only safe default — the full hardware
        # registry — is the wrong answer for a setup session.
        mode=webserve.SessionMode.NORMAL,
        safety_config_path=Path("configured-safety.yaml"),
        tool_schemas=TOOLS_CACHED,
        tool_registry=tools.TOOL_REGISTRY,
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


def _managed_updates(tmp_path, monkeypatch, *, candidate_sha="a" * 40):
    state_path = tmp_path / updates.STATE_NAME
    updates.write_state({
        **updates.public_provenance("0" * 40),
        "last_attempt": 10,
        "last_success": {"checked_at": 10, "candidate": {
            "sha": candidate_sha, "subject": "Remote <subject>",
            "source": "public-head", "canonical_repo": updates.REPO,
            "warning": None,
        }},
    }, state_path)
    monkeypatch.setattr(updates, "state_path", lambda: state_path)
    return state_path


# ---- the page ----

def test_index_is_self_contained(client):
    """Served over HTTP, the page can't resolve transcript.css/js as siblings."""
    html = client.get("/").text
    assert 'href="transcript.css"' not in html
    assert 'src="transcript.js"' not in html
    assert "global.Transcript = {" in html      # transcript.js inlined
    assert "--tool-line:" in html               # transcript.css inlined


def test_update_status_reads_cache_without_calling_provider(session, tmp_path, monkeypatch):
    _managed_updates(tmp_path, monkeypatch)
    monkeypatch.setattr(updates, "discover_public", lambda *a, **k: pytest.fail("network provider called"))
    monkeypatch.setattr(updates, "discover_clone", lambda *a, **k: pytest.fail("network provider called"))
    response = TestClient(build_app(session)).get("/api/update")
    assert response.status_code == 200
    assert response.json()["candidate"]["subject"] == "Remote <subject>"


def test_check_now_uses_the_background_checker_with_force(session, tmp_path, monkeypatch):
    _managed_updates(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(updates, "check_for_update", lambda **kwargs: calls.append(kwargs))
    assert TestClient(build_app(session)).post("/api/update/check").status_code == 200
    assert calls == [{"state_file": tmp_path / updates.STATE_NAME, "force": True}]


def test_check_now_is_rate_limited(session, tmp_path, monkeypatch):
    _managed_updates(tmp_path, monkeypatch)
    monkeypatch.setattr(updates, "check_for_update", lambda **kwargs: None)
    app = TestClient(build_app(session))
    assert [app.post("/api/update/check").status_code for _ in range(4)] == [200, 200, 200, 429]


def test_staging_passes_the_session_config_path(session, tmp_path, monkeypatch):
    _managed_updates(tmp_path, monkeypatch)
    chosen = tmp_path / "non-default.yaml"
    session.safety_config_path = chosen
    captured = []

    def materialize(state, candidate, source):
        (source / "scripts").mkdir(parents=True)
        return source

    monkeypatch.setattr(updates, "materialize_public", materialize)
    monkeypatch.setattr(webserve.shutil, "which", lambda name, **kw: "uv.exe")
    monkeypatch.setattr(
        updates, "stage_inactive_slot",
        lambda *args, **kwargs: captured.append(kwargs["config_path"]),
    )
    real_start = threading.Thread.start
    monkeypatch.setattr(
        threading.Thread, "start",
        lambda thread: thread.run() if thread.name == "microclaw-update-stage" else real_start(thread),
    )
    assert TestClient(build_app(session)).post("/api/update/stage").status_code == 202
    assert captured == [chosen]


@pytest.mark.parametrize("route, body", [
    ("/api/update/stage", None),
    ("/api/update/dismiss", {"action": "later", "commit": "a" * 40}),
])
def test_mutating_update_routes_refuse_corrupt_state(session, tmp_path, monkeypatch, route, body):
    path = tmp_path / updates.STATE_NAME
    path.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(updates, "state_path", lambda: path)
    assert TestClient(build_app(session)).post(route, json=body).status_code == 409


def test_update_banner_markup_and_local_browser_api_are_present(client):
    html = client.get("/").text
    assert 'class="banner hidden" id="update-banner"' in html
    assert 'apiFetch("/api/update")' in html
    assert "api.github.com" not in html
    assert "setInterval(refreshUpdate, 2000)" not in html
    assert "fast ? 2000 : 30000" in html


def test_second_staging_request_is_refused_not_queued(session, tmp_path, monkeypatch):
    _managed_updates(tmp_path, monkeypatch)
    real_start = threading.Thread.start
    monkeypatch.setattr(
        threading.Thread, "start",
        lambda self: None if self.name == "microclaw-update-stage" else real_start(self),
    )
    app = TestClient(build_app(session))
    assert app.post("/api/update/stage").status_code == 202
    assert app.post("/api/update/stage").status_code == 409


@pytest.mark.parametrize("busy", ["turn", "acquisition", "confirmation", "setup-write"])
def test_update_restart_refuses_each_non_idle_condition(session, tmp_path, monkeypatch, busy):
    _managed_updates(tmp_path, monkeypatch)
    (tmp_path / updates.PENDING_SLOT_NAME).write_text("b\n", encoding="ascii")
    monkeypatch.setattr(updates, "validate_launch_environment", lambda: (tmp_path, "a", "nonce"))
    if busy == "turn":
        session.lock._locked = True
    elif busy == "acquisition":
        from microclaw.acquisition import AcquisitionPlan

        class Controller:
            pass

        session.ctrl = Controller()
        ledger = tools._acquisition_ledger(session.ctrl)
        reservation = ledger.reserve(
            session.guard, AcquisitionPlan(1, 1.0, 0.001, 1)
        )
    elif busy == "confirmation":
        session.pending = object()
    else:
        session.ctrl = types.SimpleNamespace(_microclaw_setup_write_capability=types.SimpleNamespace(in_flight=True))
    response = TestClient(build_app(session)).post("/api/update/restart")
    assert response.status_code == 409
    assert busy.split("-")[0] in response.json()["detail"].lower()


def test_restart_without_server_handle_refuses_instead_of_claiming_shutdown(session, tmp_path, monkeypatch):
    _managed_updates(tmp_path, monkeypatch)
    (tmp_path / updates.PENDING_SLOT_NAME).write_text("b\n", encoding="ascii")
    monkeypatch.setattr(updates, "valid_pending_slot", lambda *args: "b")
    monkeypatch.setattr(updates, "installed_launcher_protocol", lambda root: 1)
    monkeypatch.setattr(updates, "validate_launch_environment", lambda: (tmp_path, "a", "nonce"))
    response = TestClient(build_app(session)).post("/api/update/restart")
    assert response.status_code == 409
    assert "restart later" in response.json()["detail"].lower()


def test_restart_writes_request_then_flag_then_requests_shutdown(session, tmp_path, monkeypatch):
    _managed_updates(tmp_path, monkeypatch)
    (tmp_path / updates.PENDING_SLOT_NAME).write_text("b\n", encoding="ascii")
    monkeypatch.setattr(updates, "valid_pending_slot", lambda *args: "b")
    monkeypatch.setattr(updates, "installed_launcher_protocol", lambda root: 1)
    monkeypatch.setattr(
        updates, "validate_launch_environment",
        lambda: (tmp_path, "a", "child_nonce_123456"),
    )
    events = []
    monkeypatch.setattr(
        updates, "write_restart_request",
        lambda root, nonce: events.append(("write", root, nonce)),
    )

    class Server:
        @property
        def should_exit(self):
            return False

        @should_exit.setter
        def should_exit(self, value):
            events.append(("shutdown", os.environ.get("MICROCLAW_UPDATE_RESTART"), value))

    app = build_app(session)
    app.state.uvicorn_server = Server()
    response = TestClient(app).post("/api/update/restart")
    assert response.status_code == 200
    assert response.json() == {"restart_requested": True}
    assert events == [
        ("write", tmp_path, "child_nonce_123456"),
        ("shutdown", "1", True),
    ]


@pytest.mark.parametrize("missing", [updates.LAUNCH_NONCE_ENV, updates.LAUNCHER_OWNED_ENV])
def test_automatic_restart_requires_each_launcher_identity_field(
    session, tmp_path, monkeypatch, missing,
):
    _managed_updates(tmp_path, monkeypatch)
    (tmp_path / updates.ACTIVE_SLOT_NAME).write_text("a\n", encoding="ascii")
    (tmp_path / updates.PENDING_SLOT_NAME).write_text("b\n", encoding="ascii")
    (tmp_path / updates.LAUNCHER_PROTOCOL_NAME).write_text("1\n", encoding="ascii")
    exe_a = tmp_path / "env-a" / "Scripts" / "python.exe"
    updates.write_slot_marker("a" * 40, 1, executable=exe_a)
    updates.write_slot_marker("b" * 40, 1, executable=tmp_path / "env-b" / "Scripts" / "python.exe")
    monkeypatch.setattr(updates.sys, "executable", str(exe_a))
    env = {
        updates.LAUNCHER_OWNED_ENV: "1", updates.LAUNCH_ROOT_ENV: str(tmp_path),
        updates.LAUNCH_SLOT_ENV: "a", updates.LAUNCH_NONCE_ENV: "child_nonce_123456",
        updates.LAUNCH_PROTOCOL_ENV: "1",
    }
    env.pop(missing)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(missing, raising=False)
    assert TestClient(build_app(session)).get("/api/update").json()["automatic_restart"] is False


def test_automatic_restart_rejects_real_pending_marker_needing_newer_protocol(
    session, tmp_path, monkeypatch,
):
    _managed_updates(tmp_path, monkeypatch)
    (tmp_path / updates.ACTIVE_SLOT_NAME).write_text("a\n", encoding="ascii")
    (tmp_path / updates.PENDING_SLOT_NAME).write_text("b\n", encoding="ascii")
    (tmp_path / updates.LAUNCHER_PROTOCOL_NAME).write_text("1\n", encoding="ascii")
    exe_a = tmp_path / "env-a" / "Scripts" / "python.exe"
    updates.write_slot_marker("a" * 40, 1, executable=exe_a)
    updates.write_slot_marker(
        "b" * 40, 2, executable=tmp_path / "env-b" / "Scripts" / "python.exe",
    )
    monkeypatch.setattr(updates.sys, "executable", str(exe_a))
    for key, value in {
        updates.LAUNCHER_OWNED_ENV: "1", updates.LAUNCH_ROOT_ENV: str(tmp_path),
        updates.LAUNCH_SLOT_ENV: "a", updates.LAUNCH_NONCE_ENV: "child_nonce_123456",
        updates.LAUNCH_PROTOCOL_ENV: "1",
    }.items():
        monkeypatch.setenv(key, value)
    assert TestClient(build_app(session)).get("/api/update").json()["automatic_restart"] is False



def test_staging_failure_is_recorded_even_after_an_earlier_refusal(session, tmp_path, monkeypatch):
    """A stale refusal must not silence a later, unrelated staging failure.

    Block 58e's third demo gate: the NotReady step legitimately refused commit
    X, and the next staging attempt of X then failed and recorded *nothing* --
    no build_error, no status -- because the handler read a refusal record for
    that sha back out of shared state and took it for this attempt's own.
    """
    sha = "a" * 40
    _managed_updates(tmp_path, monkeypatch, candidate_sha=sha)
    state = updates.load_state(tmp_path / updates.STATE_NAME)
    state["comparison_refused_commit"] = sha
    state["comparison_refusal_reason"] = "an earlier, legitimate refusal"
    updates.write_state(state, tmp_path / updates.STATE_NAME)
    monkeypatch.setattr(webserve.shutil, "which", lambda name, **kw: "uv.exe")
    monkeypatch.setattr(updates, "materialize_clone", lambda *a, **k: None)
    monkeypatch.setattr(updates, "materialize_public", lambda *a, **k: None)
    monkeypatch.setattr(updates, "stage_cached_candidate", lambda *a, **k: (_ for _ in ()).throw(
        updates.UpdateError("the update could not be built")))
    real_start = threading.Thread.start
    monkeypatch.setattr(
        threading.Thread, "start",
        lambda thread: thread.run() if thread.name == "microclaw-update-stage" else real_start(thread),
    )
    assert TestClient(build_app(session)).post("/api/update/stage").status_code == 202
    after = updates.load_state(tmp_path / updates.STATE_NAME)
    assert after["build_error"] == "the update could not be built"
    assert after["staging"] == {"status": "error", "commit": sha}


def test_a_comparison_refusal_keeps_its_own_reason(session, tmp_path, monkeypatch):
    """The typed refusal is the one failure that must NOT be overwritten."""
    sha = "a" * 40
    _managed_updates(tmp_path, monkeypatch, candidate_sha=sha)
    monkeypatch.setattr(webserve.shutil, "which", lambda name, **kw: "uv.exe")

    def refuse(*args, **kwargs):
        state = updates.load_state(tmp_path / updates.STATE_NAME)
        state["comparison_refused_commit"] = sha
        state["comparison_refusal_reason"] = "would downgrade a reviewed config"
        state["staging"] = {"status": "refused", "commit": sha}
        updates.write_state(state, tmp_path / updates.STATE_NAME)
        raise updates.ComparisonRefused("would downgrade a reviewed config")

    monkeypatch.setattr(updates, "stage_cached_candidate", refuse)
    real_start = threading.Thread.start
    monkeypatch.setattr(
        threading.Thread, "start",
        lambda thread: thread.run() if thread.name == "microclaw-update-stage" else real_start(thread),
    )
    assert TestClient(build_app(session)).post("/api/update/stage").status_code == 202
    after = updates.load_state(tmp_path / updates.STATE_NAME)
    assert after["staging"] == {"status": "refused", "commit": sha}
    assert after["comparison_refusal_reason"] == "would downgrade a reviewed config"
    assert "build_error" not in after

def test_later_and_skip_are_scoped_to_one_commit(session, tmp_path, monkeypatch):
    path = _managed_updates(tmp_path, monkeypatch)
    monkeypatch.setattr(webserve.time, "time", lambda: 100.0)
    app = TestClient(build_app(session))
    assert app.post("/api/update/dismiss", json={"action": "later", "commit": "a" * 40}).status_code == 200
    assert app.get("/api/update").json()["candidate"] is None
    state = updates.load_state(path)
    state["last_success"]["candidate"]["sha"] = "b" * 40
    updates.write_state(state, path)
    assert app.get("/api/update").json()["candidate"]["sha"] == "b" * 40
    assert app.post("/api/update/dismiss", json={"action": "skip", "commit": "b" * 40}).status_code == 200
    assert app.get("/api/update").json()["candidate"] is None


def test_update_endpoints_do_not_enter_agent_state(session, tmp_path, monkeypatch):
    path = _managed_updates(tmp_path, monkeypatch)
    session.store = ConversationStore(AuditLog(None, enabled=False))
    candidate = updates.load_state(path)["last_success"]["candidate"]
    monkeypatch.setattr(updates, "check_for_update", lambda **kwargs: None)
    monkeypatch.setattr(
        updates, "materialize_public",
        lambda state, selected, source: source.mkdir(parents=True) or source,
    )
    monkeypatch.setattr(webserve.shutil, "which", lambda name, **kw: "uv.exe")
    monkeypatch.setattr(updates, "stage_inactive_slot", lambda *args, **kwargs: None)
    (tmp_path / updates.PENDING_SLOT_NAME).write_text("b\n", encoding="ascii")
    monkeypatch.setattr(updates, "valid_pending_slot", lambda *args: "b")
    monkeypatch.setattr(updates, "installed_launcher_protocol", lambda root: 1)
    monkeypatch.setattr(
        updates, "validate_launch_environment",
        lambda: (tmp_path, "a", "child_nonce_123456"),
    )
    monkeypatch.setattr(updates, "write_restart_request", lambda *args: None)
    real_start = threading.Thread.start
    monkeypatch.setattr(
        threading.Thread, "start",
        lambda thread: thread.run() if thread.name == "microclaw-update-stage" else real_start(thread),
    )
    built = build_app(session)
    built.state.uvicorn_server = types.SimpleNamespace(should_exit=False)
    app = TestClient(built)
    # Captured before the cycle: `_durable_history` prefers the store, so an
    # assertion on it alone cannot see a row written straight to `history` —
    # which is the list `run_turn` sends to the model. Both are checked.
    before = list(session.history)
    assert app.post("/api/update/check").status_code == 200
    assert app.post("/api/update/stage").status_code == 202
    assert app.post("/api/update/dismiss", json={
        "action": "later", "commit": candidate["sha"],
    }).status_code == 200
    assert app.post("/api/update/restart").status_code == 200
    assert session.history == before
    assert not any("update" in name for name in tools.TOOL_REGISTRY)
    assert not any("update" in schema["name"] for schema in TOOLS_CACHED)
    assert all(fn.__module__ != updates.__name__ for fn in tools.TOOL_REGISTRY.values())
    assert all("updates" not in fn.__code__.co_names for fn in tools.TOOL_REGISTRY.values())
    history = webserve._durable_history(session)
    assert history == []
    serialized = json.dumps(session.store.audit.records)
    for forbidden in (candidate["sha"], candidate["subject"], "/api/update"):
        assert forbidden not in serialized


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
        {"type": "round_start", "iteration": 0, "seq": 1},
        {"type": "done", "reply": "channel set to DAPI", "seq": 2},
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


def test_event_sequences_restart_each_turn_and_do_not_mutate_callers(
    session, client, monkeypatch
):
    original = {"type": "round_start", "iteration": 0}

    def fake(*args, **kwargs):
        yield original
        yield {"type": "done"}

    monkeypatch.setattr(webserve, "run_agent_iter", fake)
    first = _events(client.post("/api/prompt", json={"message": "one"}))
    second = _events(client.post("/api/prompt", json={"message": "two"}))
    assert [event["seq"] for event in first] == [1, 2]
    assert [event["seq"] for event in second] == [1, 2]
    assert original == {"type": "round_start", "iteration": 0}


def test_sequence_delivery_order_includes_a_real_second_emitting_thread(
    session, client, monkeypatch
):
    helper_was_a_different_thread = []

    def fake(*args, **kwargs):
        yield {"type": "round_start", "source": "turn-before"}

        def helper():
            helper_was_a_different_thread.append(
                threading.current_thread() is not threading.main_thread()
            )
            session._emit({"type": "acquisition_progress", "source": "teardown"})

        thread = threading.Thread(target=helper, name="test-acq-teardown")
        thread.start()
        thread.join()
        yield {"type": "done", "source": "turn-after"}

    monkeypatch.setattr(webserve, "run_agent_iter", fake)
    events = _events(client.post("/api/prompt", json={"message": "go"}))
    assert helper_was_a_different_thread == [True]
    assert [(event["source"], event["seq"]) for event in events] == [
        ("turn-before", 1), ("teardown", 2), ("turn-after", 3),
    ]


def test_turn_done_is_unstamped_ends_by_identity_and_logging_is_payload_free(
    session, client, monkeypatch, capsys
):
    secret = "RECOGNISABLE-CONFIRM-SUMMARY"

    def fake(*args, **kwargs):
        for index in range(40):
            yield {"type": "text_delta", "text": f"token-{index}"}
        yield {"type": "confirm_request", "id": "c1", "summary": secret}
        yield {"type": "done", "reply": "finished"}

    monkeypatch.setattr(webserve, "run_agent_iter", fake)
    events = _events(client.post("/api/prompt", json={"message": "go"}))
    output = capsys.readouterr().out
    lines = [line for line in output.splitlines() if line.startswith("[microclaw turn ")]

    assert [event["seq"] for event in events] == list(range(1, 43))
    assert len(lines) == 3  # confirm_request, done, and the one turn summary
    assert not any(" seq " in line and line.endswith(" text_delta") for line in lines)
    assert "done: 42 events (40 text_delta), final seq 42" in lines[-1]
    assert secret not in output
    # Receiving exactly the yielded events proves the unstamped sentinel was
    # still recognized by identity and ended the generator rather than leaking.
    assert all(event.get("type") != str(webserve._TURN_DONE) for event in events)


def test_event_log_lines_flush_while_the_server_is_still_running(
    session, client, monkeypatch
):
    printed = []
    real_print = builtins.print

    def capture(*args, **kwargs):
        if args and str(args[0]).startswith("[microclaw turn "):
            printed.append((str(args[0]), kwargs.get("flush")))
        return real_print(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", capture)
    monkeypatch.setattr(webserve, "run_agent_iter", _agent_iter())
    client.post("/api/prompt", json={"message": "go"})

    assert len(printed) == 3  # round_start, done, and summary
    assert all(flush is True for _, flush in printed)


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
    assert session.last_resolution is None


def test_browser_renders_acquisition_progress_in_the_pending_status(client):
    html = client.get("/").text
    assert 'case "acquisition_progress"' in html
    assert "`frames ${accounted} / ${planned}`" in html


def test_browser_poll_lifetime_and_timeout_resolution_are_wired_once(client):
    html = client.get("/").text
    assert html.count("confirmationRecovery.startConfirmationRecovery();") == 1
    assert html.count("confirmationRecovery.startFromBoot();") == 1
    assert html.count("confirmationRecovery.stopConfirmationRecovery();") == 1
    assert 'hideConfirm(ev.id, ev.decision)' in html
    assert "Confirmation timed out and was declined" in html
    # Polling reconciles banner/grant state only; streamed transcript events
    # still pass through the one existing apply-and-paint loop.
    assert html.count("applyEvent(live, ev, state);") == 1
    assert html.count('console.warn("Microclaw stream silence; turn:"') == 1
    assert html.count('console.warn("Microclaw turn settled; turn:"') == 1
    assert "if (Number.isInteger(ev.seq)) lastAppliedSeq = ev.seq;" in html


def test_quiet_prompt_stream_keeps_one_getter_and_delivers_after_a_ping(
    session, monkeypatch
):
    monkeypatch.setattr(webserve, "KEEPALIVE_S", 0.01)
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("test-key", None))

    def delayed_agent(*args, **kwargs):
        time.sleep(0.015)
        yield {"type": "round_start"}

    monkeypatch.setattr(webserve, "run_agent_iter", delayed_agent)
    app = build_app(session)
    endpoint = next(
        route.endpoint for route in app.routes
        if getattr(route, "path", None) == "/api/prompt"
    )

    async def exercise():
        request = types.SimpleNamespace(state=types.SimpleNamespace(identity="loopback"))
        response = await endpoint(webserve.Prompt(message="go"), request)
        stream = response.body_iterator
        first = await anext(stream)
        chunks = [first]
        def has_round_start(chunk):
            return '"round_start"' in (
                chunk.decode() if isinstance(chunk, bytes) else chunk
            )
        while not any(has_round_start(chunk) for chunk in chunks):
            chunks.append(await anext(stream))
        await stream.aclose()
        await asyncio.sleep(0)
        queue_getters = [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and "Queue.get" in repr(task.get_coro())
        ]
        return chunks, queue_getters

    chunks, queue_getters = asyncio.run(exercise())
    assert chunks[0] == ": ping\n\n"
    assert sum('"round_start"' in (c.decode() if isinstance(c, bytes) else c)
               for c in chunks) == 1
    assert queue_getters == []


def test_browser_acquisition_grant_lookup_compares_structured_magnitude(session):
    small = {"frames": 500, "duration_s": 26.0, "illuminated_ms": 25000.0}
    grant = tools.SESSION_GRANTS.grant(
        "acquisition", "threshold", "500-frame plan", "loopback",
        grant_metadata=small,
    )
    smaller = {key: value / 2 for key, value in small.items()}
    assert session.confirm(
        "smaller", "acquisition", "threshold", grant_metadata=smaller
    ) is True
    assert session.audit_records[-1]["decision"] == f"auto-approved:{grant['id']}"
    larger = dict(small)
    larger["frames"] = 100000
    # No stream is bound, so reaching the prompt path is an observable decline;
    # an incorrect grant lookup would auto-approve it.
    assert session.confirm(
        "100,000-frame plan", "acquisition", "threshold", grant_metadata=larger
    ) is False
    assert session.audit_records[-1]["decision"] == "declined:no-stream"


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
    assert events[-1] == {"type": "confirm_resolved", "id": pid,
                          "turn_id": None, "decision": "approved"}


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
    assert session.last_resolution is None

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
    assert body["id"] == pid
    assert body["summary"] == "Save knowledge devices/X:\nX: {a: 1}"
    assert body["kind"] == "illumination"
    assert body["subject"] is None
    assert body["grantable"] is False
    assert body["grants"] == []
    assert body["running"] is False
    assert body["turn_id"] is None
    assert body["last_resolution"] is None
    assert 0 < body["remaining_s"] <= webserve.CONFIRM_TIMEOUT_S

    client.post("/api/confirm", json={"id": pid, "approve": False})
    thread.join(timeout=5)
    assert box["answer"] is False
    resolved = client.get("/api/confirm").json()
    assert resolved["grants"] == []
    assert resolved["last_resolution"] == {
        "id": pid, "turn_id": None, "decision": "declined",
    }


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
    monkeypatch.setattr(webserve, "CONFIRM_TIMEOUT_S", 0.1)
    session._emit = lambda event: None
    session.current_turn_id = "turn-timeout"
    assert session.confirm("Save knowledge x") is False
    assert session.pending is None
    assert session.last_resolution["turn_id"] == "turn-timeout"
    assert session.last_resolution["decision"] == "declined:timeout"


def test_audit_failure_publishes_no_authoritative_resolution(session, monkeypatch):
    pending = webserve._Pending("pending-id", "Proceed?", "knowledge", None)
    pending.reply.put((True, "browser"))
    monkeypatch.setattr(webserve, "_Pending", lambda *args, **kwargs: pending)
    events = []
    session._emit = events.append
    session.current_turn_id = "turn-1"

    def fail_audit(**kwargs):
        raise OSError("audit volume is read-only")

    session._audit_confirmation = fail_audit
    with pytest.raises(OSError, match="read-only"):
        session.confirm("Proceed?")

    assert session.last_resolution is None
    assert session.pending is None
    assert events[-1] == {"type": "confirm_resolved", "id": "pending-id"}


def test_missing_session_grant_publishes_no_resolution(session, monkeypatch):
    pending = webserve._Pending(
        "pending-id", "Enable illumination", "illumination", "enable"
    )
    pending.reply.put(("session", "browser"))
    monkeypatch.setattr(webserve, "_Pending", lambda *args, **kwargs: pending)
    events = []
    session._emit = events.append
    session.current_turn_id = "turn-1"

    with pytest.raises(RuntimeError, match="grant disappeared"):
        session.confirm("Enable illumination", "illumination", "enable")

    assert session.last_resolution is None
    assert session.pending is None
    assert events[-1] == {"type": "confirm_resolved", "id": "pending-id"}


def test_polling_pending_state_cannot_extend_or_answer_a_confirmation(
    session, client, monkeypatch
):
    now = [10.0]
    monkeypatch.setattr(webserve, "_monotonic", lambda: now[0])
    monkeypatch.setattr(webserve, "CONFIRM_TIMEOUT_S", 2.0)
    pending = webserve._Pending("c1", "Proceed?", "knowledge", None)
    session.pending = pending
    deadline = pending.deadline

    first = client.get("/api/confirm").json()
    now[0] = 11.25
    second = client.get("/api/confirm").json()

    assert first["remaining_s"] == 2.0
    assert second["remaining_s"] == 0.75
    assert pending.deadline == deadline
    assert pending.reply.empty()


def test_resolution_survives_turn_completion_and_clears_on_next_accepted_prompt(
    session, client, monkeypatch
):
    session.current_turn_id = "finished-turn"
    session.last_resolution = {
        "id": "c1", "turn_id": "finished-turn", "decision": "declined",
    }
    assert session.lock.locked() is False
    assert client.get("/api/confirm").json()["last_resolution"]["id"] == "c1"

    monkeypatch.setattr(webserve, "run_agent_iter", lambda *args, **kwargs: iter(()))
    response = client.post("/api/prompt", json={"message": "next"})
    assert response.status_code == 200
    assert response.headers["X-Microclaw-Turn-ID"] != "finished-turn"
    assert session.last_resolution is None


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
        mode=webserve.SessionMode.NORMAL,
    )
    monkeypatch.setattr(
        webserve, "build_session", lambda args, config_result=None: fake,
    )
    _stub_uvicorn_server(monkeypatch)
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
                model=None, save_history=False, no_update_check=False)
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


def test_setup_writer_capability_refuses_remote_even_when_remote_serve_is_allowed():
    with pytest.raises(SystemExit, match="only on a loopback bind"):
        serve(_args(
            setup_write_security_config=True, allow_remote=True,
            behind_tls_proxy=True,
        ))


def test_setup_writer_capability_refuses_explicit_safety_path_on_loopback():
    with pytest.raises(SystemExit, match="per-user default"):
        serve(_args(
            host="127.0.0.1", setup_write_security_config=True,
            safety_config="redirect.yaml",
        ))


def test_serve_without_a_safety_config_falls_back_to_the_per_user_default(tmp_path, monkeypatch):
    """No --safety-config is what a desktop shortcut passes (design/17 v3).

    It means the per-user profile, not another path and never "no limits". If
    that exact path is absent, serve builds the restricted, guardless setup
    session whose dispatcher makes every normal hardware tool unreachable.
    """
    missing = tmp_path / "safety_config.yaml"
    monkeypatch.setattr(config, "default_safety_config", lambda: missing)

    class Connected:
        core = object()

        def __init__(self, port, guard):
            assert guard is None

        def is_connected(self):
            return True

    monkeypatch.setattr(webserve, "MicroscopeController", Connected)
    monkeypatch.setattr(webserve, "enumerate_rig", lambda core: {"stages": []})
    monkeypatch.setattr(credentials, "load_api_key", lambda: (None, None))

    session = webserve.build_session(_args(
        host="127.0.0.1", safety_config=None,
    ))
    assert isinstance(session, webserve.SetupSession)
    assert session.guard is None
    assert session.tool_registry is webserve.SETUP_TOOL_REGISTRY
    for name in tools.TOOL_REGISTRY:
        refusal = json.loads(tools.execute_tool(
            name, {}, session.ctrl, session.guard, session.tool_registry,
            setup_mode=True,
        ))
        assert "setup mode" in refusal["error"]


@pytest.mark.parametrize("ready", [True, False], ids=["Session", "SetupSession"])
def test_build_session_validates_once_on_both_routes(monkeypatch, ready):
    result = types.SimpleNamespace(
        can_start_live_validation=ready,
        parsed="parsed" if ready else None,
        classification="ready" if ready else "missing",
        path=Path("missing.yaml"),
    )
    calls = []
    monkeypatch.setattr(
        webserve.config, "validate_safety_config",
        lambda path: calls.append(path) or result,
    )
    monkeypatch.setattr(webserve, "Session", lambda args, parsed: types.SimpleNamespace(kind="normal", value=parsed))
    monkeypatch.setattr(webserve, "SetupSession", lambda args, snapshot: types.SimpleNamespace(kind="setup", value=snapshot))
    session = webserve.build_session(_args(safety_config="snapshot.yaml"))
    assert len(calls) == 1
    assert session.kind == ("normal" if ready else "setup")
    assert session.safety_config_path == result.path


def test_build_session_uses_supplied_snapshot_after_file_is_replaced(tmp_path, monkeypatch):
    path = tmp_path / "safety.yaml"
    path.write_text("reviewed: true\n", encoding="utf-8")
    snapshot = types.SimpleNamespace(
        can_start_live_validation=True, parsed="snapshot constraints",
        classification="ready", path=path,
    )
    path.write_text("reviewed: false\n", encoding="utf-8")
    monkeypatch.setattr(
        webserve.config, "validate_safety_config",
        lambda path: pytest.fail("supplied snapshot was revalidated"),
    )
    monkeypatch.setattr(webserve, "Session", lambda args, parsed: types.SimpleNamespace(value=parsed))
    session = webserve.build_session(
        _args(safety_config=str(path)), config_result=snapshot,
    )
    assert session.value == "snapshot constraints"
    assert session.safety_config_path == path


def test_serve_hoists_one_snapshot_into_build_session(monkeypatch, tmp_path):
    import uvicorn

    path = tmp_path / "safety.yaml"
    snapshot = object()
    validations = []
    received = []
    fake = types.SimpleNamespace(
        confirm=lambda *args, **kwargs: False,
        guard=_guard(), ctrl=types.SimpleNamespace(core=None),
    )
    monkeypatch.setattr(
        webserve.config, "validate_safety_config",
        lambda supplied: validations.append(supplied) or snapshot,
    )
    monkeypatch.setattr(
        webserve, "build_session",
        lambda args, config_result=None: received.append(config_result) or fake,
    )
    app = types.SimpleNamespace(state=types.SimpleNamespace())
    monkeypatch.setattr(webserve, "build_app", lambda *args, **kwargs: app)
    servers = _stub_uvicorn_server(monkeypatch)

    webserve.serve(_args(
        host="127.0.0.1", safety_config=str(path), no_browser=True,
    ))

    assert validations == [path]
    assert received == [snapshot]
    assert app.state.uvicorn_server is servers[0]


def test_serve_writes_launcher_health_immediately_before_build_session(monkeypatch, tmp_path):
    import uvicorn
    from microclaw import updates

    events = []
    fake = types.SimpleNamespace(confirm=lambda *a, **k: False, guard=_guard(),
                                 ctrl=types.SimpleNamespace(core=None))
    monkeypatch.setattr(webserve.config, "validate_safety_config",
                        lambda path: events.append("validate") or object())
    monkeypatch.setattr(updates, "write_launcher_health",
                        lambda: events.append("health"))
    monkeypatch.setattr(webserve, "build_session",
                        lambda *a, **k: events.append("build") or fake)
    monkeypatch.setattr(
        webserve, "build_app",
        lambda *a, **k: types.SimpleNamespace(state=types.SimpleNamespace()),
    )
    _stub_uvicorn_server(monkeypatch)
    webserve.serve(_args(host="127.0.0.1", safety_config=str(tmp_path / "x"),
                         no_browser=True))
    assert events == ["validate", "health", "build"]


def test_serve_runs_update_check_without_blocking_and_honors_opt_out(monkeypatch, tmp_path):
    import uvicorn

    started = threading.Event()
    release = threading.Event()
    calls = []
    fake = types.SimpleNamespace(confirm=lambda *a, **k: False, guard=_guard(),
                                 ctrl=types.SimpleNamespace(core=None))

    def check(**kwargs):
        calls.append(kwargs)
        started.set()
        release.wait(timeout=5)

    monkeypatch.setattr(updates, "check_for_update", check)
    monkeypatch.setattr(webserve.config, "validate_safety_config", lambda path: object())

    def build(*args, **kwargs):
        assert started.wait(timeout=1), "startup check thread never ran"
        return fake

    monkeypatch.setattr(webserve, "build_session", build)
    monkeypatch.setattr(
        webserve, "build_app",
        lambda *a, **k: types.SimpleNamespace(state=types.SimpleNamespace()),
    )
    _stub_uvicorn_server(monkeypatch)
    webserve.serve(_args(host="127.0.0.1", safety_config=str(tmp_path / "x")))
    assert not release.is_set(), "serve waited for the background check"
    release.set()
    assert calls == [{"no_update_check": False}]

    started.clear()
    calls.clear()
    monkeypatch.setattr(webserve, "build_session", lambda *a, **k: fake)
    webserve.serve(_args(
        host="127.0.0.1", safety_config=str(tmp_path / "x"), no_update_check=True,
    ))
    assert calls == []


@pytest.mark.parametrize("no_update_check,env_disabled", [
    (False, False), (True, False), (False, True),
])
def test_serve_never_reads_stdin_for_update_prompt(
    monkeypatch, tmp_path, no_update_check, env_disabled,
):
    if env_disabled:
        monkeypatch.setenv("MICROCLAW_UPDATE_CHECK", "0")
    fake = types.SimpleNamespace(confirm=lambda *a, **k: False, guard=_guard(),
                                 ctrl=types.SimpleNamespace(core=None))
    monkeypatch.setattr(webserve.config, "validate_safety_config", lambda path: object())
    monkeypatch.setattr(webserve, "build_session", lambda *a, **k: fake)
    monkeypatch.setattr(
        webserve, "build_app",
        lambda *a, **k: types.SimpleNamespace(state=types.SimpleNamespace()),
    )
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("serve prompted"))
    _stub_uvicorn_server(monkeypatch)
    webserve.serve(_args(
        host="127.0.0.1", safety_config=str(tmp_path / "x"), no_browser=True,
        no_update_check=no_update_check,
    ))


def test_bridge_failure_after_health_leaves_nonce_marker(monkeypatch, tmp_path):
    from microclaw import updates

    exe = tmp_path / "env-a" / "Scripts" / "python.exe"
    updates.write_slot_marker("a" * 40, 1, executable=exe)
    env = {updates.LAUNCHER_OWNED_ENV: "1", updates.LAUNCH_ROOT_ENV: str(tmp_path),
           updates.LAUNCH_SLOT_ENV: "a", updates.LAUNCH_NONCE_ENV: "nonce_abcdefghijkl",
           updates.LAUNCH_PROTOCOL_ENV: "1"}
    write_health = updates.write_launcher_health
    monkeypatch.setattr(webserve.config, "validate_safety_config", lambda path: object())
    monkeypatch.setattr(updates, "write_launcher_health",
                        lambda: write_health(env, executable=exe))
    monkeypatch.setattr(webserve, "build_session",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bridge closed")))
    with pytest.raises(RuntimeError, match="bridge closed"):
        webserve.serve(_args(host="127.0.0.1", safety_config=str(tmp_path / "x"),
                             no_browser=True))
    assert (tmp_path / updates.HEALTH_NAME).read_text(encoding="ascii").strip() == "nonce_abcdefghijkl"


def test_serve_opens_restricted_setup_for_an_unreviewed_config(tmp_path, monkeypatch, capsys):
    """An unreviewed file is never policy and setup never replaces it."""
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
    original = cfg.read_bytes()

    class Connected:
        core = object()

        def __init__(self, port, guard):
            assert guard is None

        def is_connected(self):
            return True

    monkeypatch.setattr(webserve, "MicroscopeController", Connected)
    monkeypatch.setattr(webserve, "enumerate_rig", lambda core: {"stages": []})
    monkeypatch.setattr(credentials, "load_api_key", lambda: (None, None))
    monkeypatch.setattr(config, "default_safety_config", lambda: cfg)
    session = webserve.build_session(_args(
        host="127.0.0.1", safety_config=None,
        setup_write_security_config=True,
    ))
    assert isinstance(session, webserve.SetupSession)
    assert session.guard is None
    assert session.tool_registry is webserve.SETUP_TOOL_REGISTRY
    assert cfg.read_bytes() == original
    assert str(cfg) in session.history[0]["content"]
    assert str(cfg) in capsys.readouterr().out
    for name in tools.TOOL_REGISTRY:
        refusal = json.loads(tools.execute_tool(
            name, {}, session.ctrl, session.guard, session.tool_registry,
            setup_mode=True,
        ))
        assert "setup mode" in refusal["error"]


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
    ("get", "/api/update", None, 200),
    ("post", "/api/update/check", None, 200),
    ("post", "/api/update/stage", None, 409),
    ("post", "/api/update/restart", None, 409),
    ("post", "/api/update/dismiss", {"action": "later", "commit": "a" * 40}, 409),
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


def test_one_unauthorized_confirmation_poll_leaves_post_confirm_available(
    session, remote, fast_confirm_poll
):
    client, _ = remote
    thread, _, box = _start_confirm(session)
    pid = session.pending.id

    assert client.get("/api/confirm", headers=TLS).status_code == 401
    approved = client.post(
        "/api/confirm", headers=_bearer(), json={"id": pid, "approve": True}
    )

    assert approved.status_code == 200
    thread.join(timeout=5)
    assert box["answer"] is True


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
    monkeypatch.setattr(
        webserve, "build_session", lambda args, config_result=None: fake,
    )
    monkeypatch.setattr(
        webserve, "build_app",
        lambda *args, **kwargs: types.SimpleNamespace(state=types.SimpleNamespace()),
    )
    import uvicorn
    _stub_uvicorn_server(monkeypatch)
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


def test_update_status_does_not_offer_the_installed_commit(session, tmp_path, monkeypatch):
    """The banner reads the same day-old cache the REPL notice does."""
    _managed_updates(tmp_path, monkeypatch, candidate_sha="0" * 40)  # == installed_commit
    assert TestClient(build_app(session)).get("/api/update").json()["candidate"] is None


def test_staging_refuses_the_commit_already_installed(session, tmp_path, monkeypatch):
    """Staging the running commit rebuilds the other slot at the same SHA and
    re-arms the same banner: the update loop the demo machines were stuck in."""
    _managed_updates(tmp_path, monkeypatch, candidate_sha="0" * 40)
    monkeypatch.setattr(
        updates, "stage_cached_candidate",
        lambda *a, **k: pytest.fail("staged a commit that is already installed"),
    )
    assert TestClient(build_app(session)).post("/api/update/stage").status_code == 409


def test_installing_an_update_clears_the_banner_that_offered_it(session, tmp_path, monkeypatch):
    """The whole reported cycle, composed: stage -> restart -> banner gone.

    Every unit of this was green while the machines looped.  `activate_pending`
    reconciled `installed_commit` correctly, `update_status` projected the cache
    faithfully, and nothing in between compared the two -- so the update
    installed and then offered itself again, and staging that offer rebuilt the
    other slot at the same SHA.  This drives the sequence an operator performs.
    """
    installed, newer = "0" * 40, "a" * 40
    state_path = _managed_updates(tmp_path, monkeypatch, candidate_sha=newer)
    state = updates.load_state(state_path)
    state["next_check"] = time.time() + updates.CHECK_INTERVAL_SECONDS
    updates.write_state(state, state_path)
    (tmp_path / updates.LAUNCHER_PROTOCOL_NAME).write_text("1\n", encoding="ascii")
    (tmp_path / updates.ACTIVE_SLOT_NAME).write_text("a\n", encoding="ascii")
    for slot, commit in (("a", installed), ("b", newer)):
        updates.write_slot_marker(
            commit, 1, executable=tmp_path / f"env-{slot}" / "Scripts" / "python.exe",
        )
    # What stage_inactive_slot publishes on success.
    (tmp_path / updates.PENDING_SLOT_NAME).write_text("b\n", encoding="ascii")
    state = updates.load_state(state_path)
    state["discovery"] = {"status": "candidate",
                          "message": f"A newer main commit is available: {newer}."}
    state["staging"] = {"status": "staged", "commit": newer}
    updates.write_state(state, state_path)

    app = TestClient(build_app(session))
    staged = app.get("/api/update").json()
    assert staged["pending_staged"] is True
    assert staged["candidate"]["sha"] == newer

    # "Restart now": the launcher consumes pending through the same call.
    assert updates.activate_pending(tmp_path, 1) == ("b", "a")

    after = app.get("/api/update").json()
    assert after["pending_staged"] is False
    assert after["candidate"] is None
    assert updates.terminal_update_notice(state_file=state_path) == (None, None)
    # And the interval must not hold the machine on a day-old answer, nor the
    # diagnostic keep naming the commit that is now running.
    settled = updates.load_state(state_path)
    assert "next_check" not in settled
    assert "discovery" not in settled


def test_check_for_updates_control_is_reachable_from_the_page(client):
    """The route had a rate limiter, two tests and no caller for its whole life.

    It sits in the header rather than the update banner, because the banner is
    hidden precisely when there is no candidate -- which is when a user wants
    to check.  Without it the only way to retire the 24-hour interval is to
    rerun install.bat.
    """
    html = client.get("/").text
    assert 'id="update-check"' in html
    assert 'apiFetch("/api/update/check", { method: "POST" })' in html
    assert "state.check_error" in html
    assert "Microclaw is up to date." in html


def test_check_control_is_hidden_on_an_unmanaged_install(session, tmp_path, monkeypatch):
    """`managed` is false when there is no state file; offering the button then
    would promise a check that `check_for_update` returns None from."""
    monkeypatch.setattr(updates, "state_path", lambda: tmp_path / updates.STATE_NAME)
    assert TestClient(build_app(session)).get("/api/update").json()["managed"] is False
    html = TestClient(build_app(session)).get("/").text
    assert 'classList.toggle("hidden", !(state && state.managed))' in html


def test_a_stale_build_error_does_not_report_a_failed_check(session, tmp_path, monkeypatch):
    """`build_error` outlives the staging that set it, until the next success.

    Merged into one field it would tell a user whose check just succeeded that
    the check failed, naming a build they may have run days ago.
    """
    state_path = _managed_updates(tmp_path, monkeypatch)
    state = updates.load_state(state_path)
    state["last_error"] = None
    state["build_error"] = "the update could not be built"
    updates.write_state(state, state_path)
    payload = TestClient(build_app(session)).get("/api/update").json()
    assert payload["check_error"] is None
    assert payload["last_error"] == "the update could not be built"


def test_missing_safety_config_says_so_instead_of_silently_opening_setup(
    tmp_path, monkeypatch, capsys
):
    """A --safety-config that does not exist must name itself.

    Measured on the demo machine 2026-08-28: the operator passed a path whose
    directory had not been generated yet and saw only "Connecting to
    Micro-Manager in setup mode...". validate_safety_config had already produced
    "No safety config at <path>"; build_session printed a reason only for the
    "blocked" classification, and "missing" is a separate one.
    """
    from types import SimpleNamespace

    from microclaw import webserve

    absent = tmp_path / "not-generated" / "generated-safety-config.yaml"
    monkeypatch.setattr(webserve, "SetupSession", lambda args, result: SimpleNamespace())
    args = SimpleNamespace(safety_config=str(absent), port=4827)
    webserve.build_session(args)
    out = capsys.readouterr().out
    assert str(absent) in out
    assert "--safety-config" in out


@pytest.mark.parametrize("with_store", [False, True])
@pytest.mark.parametrize("save", [False, True])
def test_usage_browser_sidecar(session, client, monkeypatch, tmp_path, with_store, save):
    monkeypatch.chdir(tmp_path)
    args = types.SimpleNamespace(model=None, save_history=save, host="127.0.0.1")
    webserve.Session._initialize(session, args)
    if with_store:
        session.store.last_estimated_tokens = 161234
        session.store.compaction_count = 4
    else:
        del session.store
    expected_path = Path(session.history_fn.replace("_history.jsonl", "_usage.jsonl"))
    assert session.usage_audit.path == expected_path
    def fake(*args, **kwargs):
        kwargs["usage_sink"]({"input_tokens": 12})
        yield {"type": "done", "reply": "done"}
    monkeypatch.setattr(webserve, "run_agent_iter", fake)
    response = client.post("/api/prompt", json={"message": "go"})
    assert _settle(session)
    assert [e["type"] for e in _events(response)] == ["done"]
    record, = session.usage_audit.records
    assert record["estimated_tokens"] == (161234 if with_store else None)
    assert record["compaction_count"] == (4 if with_store else None)
    assert record["turn_id"]
    assert expected_path.exists() is save
    if save:
        assert json.loads(expected_path.read_text(encoding="utf-8")) == record


@pytest.fixture
def extension_client(session, monkeypatch, tmp_path):
    from microclaw import extensions
    session.ctrl = types.SimpleNamespace()
    session.lock = asyncio.Lock()  # Exercise the real primitive, not _FakeLock.
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("fixture", "env"))
    monkeypatch.setattr(extensions, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(extensions, "ready", lambda name: False)
    with TestClient(build_app(session)) as client:
        yield client


def wait_extension(client, predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        state = client.get("/api/extensions").json()
        if predicate(state):
            return state
        time.sleep(.01)
    pytest.fail(f"extension state did not reach expected observation: {state}")


@pytest.mark.parametrize("name", ["unknown", "ilastik; rm -rf /", "h5py"])
def test_extension_unknown_endpoint_never_spawns(extension_client, monkeypatch, name):
    import subprocess
    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: spawned.append(a))
    response = extension_client.post("/api/extensions/install", json={"name": name})
    assert response.status_code == 400, response.text
    assert spawned == [], "refused name reached uv"


def test_extension_body_only_name(extension_client):
    response = extension_client.post("/api/extensions/install", json={"name": "ilastik", "requirements": ["evil"]})
    assert response.status_code == 400


@pytest.mark.parametrize("condition", ["turn", "acquisition", "confirmation", "setup-write", "staging"])
def test_extension_five_inflight_conditions(extension_client, session, monkeypatch, tmp_path, condition):
    from microclaw import extensions
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(extensions, "install", lambda *a, **k: pytest.fail("install crossed in-flight guard"))
    if condition == "turn":
        extension_client.portal.call(session.lock.acquire)
    elif condition == "acquisition":
        monkeypatch.setattr(tools, "_existing_acquisition_ledger", lambda ctrl: types.SimpleNamespace(in_flight=True))
    elif condition == "confirmation":
        session.pending = object()
    elif condition == "setup-write":
        session.ctrl._microclaw_setup_write_capability = types.SimpleNamespace(in_flight=True)
    else:
        _managed_updates(tmp_path, monkeypatch)
        def stage(*a, **kw):
            started.set()
            assert release.wait(8)
        monkeypatch.setattr(updates, "stage_cached_candidate", stage)
        assert extension_client.post("/api/update/stage").status_code == 202
        assert started.wait(3)
    try:
        response = extension_client.post("/api/extensions/install", json={"name": "ilastik"})
        assert response.status_code == 409, response.text
    finally:
        release.set()
        if condition == "turn":
            extension_client.portal.call(session.lock.release)


@pytest.mark.parametrize("fail", [False, True])
def test_extension_holds_admission_and_releases(extension_client, session, monkeypatch, tmp_path, fail):
    from microclaw import extensions
    started, release = threading.Event(), threading.Event()
    _managed_updates(tmp_path, monkeypatch)
    def install(*a, **kw):
        started.set()
        assert release.wait(8)
        if fail:
            raise extensions.ExtensionInstallError("fixture install failed")
        return {"message": "ready", "added": []}
    monkeypatch.setattr(extensions, "install", install)
    entries = []
    monkeypatch.setattr(tools, "_acquire_with_hooks", lambda *a, **k: entries.append(1))
    def agent(*a, **kw):
        tools._acquire_with_hooks()
        yield {"type": "text", "text": "done"}
    monkeypatch.setattr(webserve, "run_agent_iter", agent)
    monkeypatch.setattr(updates, "stage_cached_candidate", lambda *a, **k: pytest.fail("staging entered during install"))
    monkeypatch.setattr(updates, "write_restart_request", lambda *a, **k: pytest.fail("restart entered during install"))
    assert extension_client.post("/api/extensions/install", json={"name": "ilastik"}).status_code == 202
    assert started.wait(3)
    try:
        for route, body in [("/api/prompt", {"message": "acquire"}), ("/api/update/stage", None),
                            ("/api/update/restart", None), ("/api/extensions/install", {"name": "ilastik"})]:
            response = extension_client.post(route, json=body)
            assert entries == [], "acquisition chokepoint entered during install"
            assert response.status_code == 409, f"{route}: {response.text}"
        assert entries == [], "acquisition chokepoint entered during install"
    finally:
        release.set()
    wait_extension(extension_client, lambda s: not s["job"]["running"])
    assert extension_client.post("/api/prompt", json={"message": "acquire"}).status_code == 200
    assert entries == [1], "control turn did not reach the acquisition chokepoint"
    assert extension_client.post("/api/extensions/install", json={"name": "ilastik"}).status_code == 202
    wait_extension(extension_client, lambda s: not s["job"]["running"])


def test_extension_chokepoint_refusal_executed():
    ctrl = types.SimpleNamespace(_microclaw_extension_install={"running": True})
    with pytest.raises(RuntimeError, match="Extension install admission: acquisition refused"):
        tools._acquire_with_hooks(None, "unused", "unused", [], ctrl=ctrl, policy=None)


def test_extension_concurrent_turn_start(extension_client, session, monkeypatch):
    from microclaw import extensions
    barrier = threading.Barrier(3)
    entered, release = threading.Event(), threading.Event()
    admitted = []
    results = {}
    def install(*a, **kw):
        admitted.append("install")
        entered.set()
        assert release.wait(8)
        return {"message": "ready", "added": []}
    def agent(*a, **kw):
        admitted.append("turn")
        entered.set()
        assert release.wait(8)
        yield {"type": "text", "text": "done"}
    monkeypatch.setattr(extensions, "install", install)
    monkeypatch.setattr(webserve, "run_agent_iter", agent)
    def request(kind, route, body):
        barrier.wait()
        results[kind] = extension_client.post(route, json=body).status_code
    workers = [threading.Thread(target=request, args=("install", "/api/extensions/install", {"name": "ilastik"})),
               threading.Thread(target=request, args=("turn", "/api/prompt", {"message": "go"}))]
    for worker in workers:
        worker.start()
    barrier.wait()
    try:
        assert entered.wait(3)
        deadline = time.monotonic() + 3
        while 409 not in results.values() and time.monotonic() < deadline:
            time.sleep(.01)
        assert 409 in results.values(), "concurrent conflicting operation was not refused"
        assert len(admitted) == 1, admitted
    finally:
        release.set()
        for worker in workers:
            worker.join(5)
    wait_extension(extension_client, lambda s: not s["job"]["running"])


def test_extension_thread_start_failure_releases(extension_client, monkeypatch):
    from microclaw import extensions
    real_start = threading.Thread.start
    def start(thread):
        if thread.name == "microclaw-extension-install":
            raise RuntimeError("cannot start installer thread")
        return real_start(thread)
    monkeypatch.setattr(threading.Thread, "start", start)
    with pytest.raises(RuntimeError, match="cannot start installer thread"):
        extension_client.post("/api/extensions/install", json={"name": "ilastik"})
    assert not extension_client.get("/api/extensions").json()["job"]["running"]
    monkeypatch.setattr(threading.Thread, "start", real_start)
    monkeypatch.setattr(extensions, "install", lambda *a, **k: {"message": "ready", "added": []})
    assert extension_client.post("/api/extensions/install", json={"name": "ilastik"}).status_code == 202
    wait_extension(extension_client, lambda s: not s["job"]["running"])


@pytest.mark.parametrize("failed", [False, True])
def test_extension_progress_streams_stderr_before_exit(extension_client, monkeypatch, tmp_path, failed):
    import subprocess
    import sys
    from microclaw import extensions
    from tests.test_extensions import UNCACHED, CONFLICT
    monkeypatch.setattr(updates, "locate_uv", lambda: "fixture-uv")
    lines = UNCACHED.splitlines()
    script = tmp_path / "stream.py"
    script.write_text(
        "import sys,time\nfrom pathlib import Path\n"
        f"root=Path({str(tmp_path)!r})\n"
        f"lines={lines!r}\n"
        "assert sys.argv[1:3] == ['pip','install']\n"
        "for i,line in enumerate(lines):\n"
        " while not (root / ('release-'+str(i))).exists(): time.sleep(.005)\n"
        " print(line,file=sys.stderr,flush=True)\n"
        " (root / ('sent-'+str(i))).touch()\n"
        "while not (root / 'exit').exists(): time.sleep(.005)\n"
        f"sys.stderr.write({CONFLICT!r} if {failed!r} else '')\n"
        f"sys.exit({1 if failed else 0})\n", encoding="utf-8")
    real_popen = subprocess.Popen
    processes = []
    def popen(argv, **kw):
        if argv[0] == "fixture-uv":
            assert kw["stdin"] == subprocess.DEVNULL
            argv = [sys.executable, str(script), *argv[1:]]
            process = real_popen(argv, **kw)
            processes.append(process)
            return process
        return real_popen(argv, **kw)
    monkeypatch.setattr(subprocess, "Popen", popen)
    enumerating, enumerated = threading.Event(), threading.Event()
    real_pins = extensions._pins
    def pins(*args):
        enumerating.set()
        assert enumerated.wait(8)
        return real_pins(*args)
    monkeypatch.setattr(extensions, "_pins", pins)
    verifying, verified = threading.Event(), threading.Event()
    def verify(name):
        verifying.set()
        assert verified.wait(8)
    monkeypatch.setattr(extensions, "_verify", verify)
    assert extension_client.post("/api/extensions/install", json={"name": "ilastik"}).status_code == 202
    try:
        assert enumerating.wait(3)
        state = extension_client.get("/api/extensions").json()
        assert state["job"]["phase"] == "checking environment"
        enumerated.set()
        state = wait_extension(extension_client, lambda s: s["job"].get("phase") == "running package installer")
        expected = "running package installer"
        for i, line in enumerate(lines):
            (tmp_path / f"release-{i}").touch()
            deadline = time.monotonic() + 3
            while not (tmp_path / f"sent-{i}").exists() and time.monotonic() < deadline:
                time.sleep(.005)
            assert (tmp_path / f"sent-{i}").exists()
            expected = ["running package installer", "Resolution complete", "Downloading numpy",
                        "Downloading h5py", "Downloaded h5py", "Downloaded numpy",
                        "Package preparation complete", "Package installation complete",
                        "Package installation complete", "Package installation complete"][i]
            state = wait_extension(extension_client, lambda s: s["job"].get("phase") == expected)
            assert state["job"]["running"] is True
            assert state["job"]["phase"] == expected
            assert processes[0].poll() is None, "phase was published only after exit"
        (tmp_path / "exit").touch()
        if not failed:
            assert verifying.wait(3)
            state = extension_client.get("/api/extensions").json()
            assert state["job"]["phase"] == "verifying"
            monkeypatch.setattr(extensions, "ready", lambda name: True)
            verified.set()
        state = wait_extension(extension_client, lambda s: not s["job"]["running"])
        assert bool(state["job"].get("error")) is failed
        if failed:
            assert "numpy>=2.6 and numpy==2.5.3" in state["job"]["error"]
    finally:
        enumerated.set()
        verified.set()
        for i in range(len(lines)):
            (tmp_path / f"release-{i}").touch()
        (tmp_path / "exit").touch()
        for process in processes:
            process.wait(timeout=5)


def test_extension_rate_limit(extension_client, monkeypatch):
    from microclaw import extensions
    monkeypatch.setattr(extensions, "install", lambda *a, **k: {"message": "ready", "added": []})
    for _ in range(webserve.RATE_MAX_UPDATE_CHECKS):
        assert extension_client.post("/api/extensions/install", json={"name": "ilastik"}).status_code == 202
        wait_extension(extension_client, lambda s: not s["job"]["running"])
    response = extension_client.post("/api/extensions/install", json={"name": "ilastik"})
    assert response.status_code == 429
    assert not extension_client.get("/api/extensions").json()["job"]["running"]


def test_extension_restart_reservation_blocks_install(extension_client, session, monkeypatch, tmp_path):
    _managed_updates(tmp_path, monkeypatch)
    monkeypatch.setattr(updates, "valid_pending_slot", lambda *a: "b")
    monkeypatch.setattr(updates, "installed_launcher_protocol", lambda root: 1)
    monkeypatch.setattr(updates, "validate_launch_environment", lambda: (tmp_path, "a", "nonce"))
    monkeypatch.setattr(updates, "write_restart_request", lambda *a: None)
    extension_client.app.state.uvicorn_server = types.SimpleNamespace(should_exit=False)
    assert extension_client.post("/api/update/restart").status_code == 200
    response = extension_client.post("/api/extensions/install", json={"name": "ilastik"})
    assert response.status_code == 409 and "restart" in response.text


def test_extension_catalog_failure_releases(extension_client, monkeypatch):
    from microclaw import extensions
    real = extensions.available
    monkeypatch.setattr(extensions, "available", lambda: (_ for _ in ()).throw(RuntimeError("catalog failed")))
    with pytest.raises(RuntimeError, match="catalog failed"):
        extension_client.post("/api/extensions/install", json={"name": "ilastik"})
    monkeypatch.setattr(extensions, "available", real)
    assert not extension_client.get("/api/extensions").json()["job"]["running"]


def test_extension_chokepoint_accepts_unreserved_mock_controller():
    from unittest.mock import MagicMock
    class ReachedWorkspace(Exception):
        pass
    guard = MagicMock()
    guard.resolve_in_workspace.side_effect = ReachedWorkspace("passed admission")
    with pytest.raises(ReachedWorkspace, match="passed admission"):
        tools._acquire_with_hooks(guard, "unused", "unused", [], ctrl=MagicMock(), policy=None)


def test_prompt_waits_without_holding_job_lock(extension_client, session, monkeypatch):
    import inspect
    route = next(r for r in extension_client.app.routes if r.path == "/api/prompt")
    job_lock = inspect.getclosurevars(route.endpoint).nonlocals["update_job_lock"]
    class Suspends(asyncio.Lock):
        async def acquire(self):
            assert not job_lock.locked(), "asyncio acquisition holds the threading lock"
            await asyncio.sleep(0)
            return await super().acquire()
    session.lock = Suspends()
    monkeypatch.setattr(webserve, "run_agent_iter", _agent_iter())
    assert extension_client.post("/api/prompt", json={"message": "go"}).status_code == 200


def test_extension_poll_caches_readiness_off_loop(extension_client, monkeypatch):
    from microclaw import extensions
    original = extensions.available
    calls = []
    def available():
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            pytest.fail("readiness runs on the event loop")
        calls.append(1)
        return original()
    monkeypatch.setattr(extensions, "available", available)
    for _ in range(3):
        assert extension_client.get("/api/extensions").status_code == 200
    assert calls == [1], "polling repeats readiness discovery"


def test_extension_refuses_reserved_turn_while_async_lock_waits(extension_client, session, monkeypatch):
    from microclaw import extensions
    entered = threading.Event()
    grant = asyncio.Event()
    class Waits(asyncio.Lock):
        async def acquire(self):
            entered.set()
            await grant.wait()
            return await super().acquire()
    session.lock = Waits()
    monkeypatch.setattr(webserve, "run_agent_iter", _agent_iter())
    monkeypatch.setattr(extensions, "install", lambda *a, **k: {"message": "ready", "added": []})
    responses = []
    worker = threading.Thread(target=lambda: responses.append(extension_client.post("/api/prompt", json={"message": "go"})))
    worker.start()
    try:
        assert entered.wait(3)
        assert not session.lock.locked()
        response = extension_client.post("/api/extensions/install", json={"name": "ilastik"})
        assert response.status_code == 409, "install ignored the pending turn reservation"
    finally:
        extension_client.portal.call(grant.set)
        worker.join(5)
    assert responses[0].status_code == 200
    assert extension_client.post("/api/extensions/install", json={"name": "ilastik"}).status_code == 202
    wait_extension(extension_client, lambda s: not s["job"]["running"])


def test_extension_initial_discovery_does_not_block_other_requests(extension_client, monkeypatch):
    from microclaw import extensions
    entered, release = threading.Event(), threading.Event()
    original = extensions.available
    def slow():
        entered.set()
        assert release.wait(5)
        return original()
    monkeypatch.setattr(extensions, "available", slow)
    responses = []
    worker = threading.Thread(target=lambda: responses.append(extension_client.get("/api/extensions")))
    worker.start()
    try:
        assert entered.wait(3)
        assert extension_client.get("/api/key").status_code == 200
    finally:
        release.set()
        worker.join(5)
    assert responses[0].status_code == 200


def test_extension_rate_limit_never_publishes_admission(extension_client, session, monkeypatch):
    monkeypatch.setattr(webserve._RateLimiter, "allow", lambda *a: False)
    assert extension_client.post("/api/extensions/install", json={"name": "ilastik"}).status_code == 429
    assert not hasattr(session.ctrl, "_microclaw_extension_install"), "rate-limited request published admission"


def test_extension_failed_shutdown_publication_releases(extension_client, monkeypatch, tmp_path):
    from microclaw import extensions
    _managed_updates(tmp_path, monkeypatch)
    monkeypatch.setattr(updates, "valid_pending_slot", lambda *a: "b")
    monkeypatch.setattr(updates, "installed_launcher_protocol", lambda root: 1)
    monkeypatch.setattr(updates, "validate_launch_environment", lambda: (tmp_path, "a", "nonce"))
    monkeypatch.setattr(updates, "write_restart_request", lambda *a: None)
    class BrokenServer:
        @property
        def should_exit(self):
            return False
        @should_exit.setter
        def should_exit(self, value):
            raise RuntimeError("shutdown publication failed")
    extension_client.app.state.uvicorn_server = BrokenServer()
    with pytest.raises(RuntimeError, match="shutdown publication failed"):
        extension_client.post("/api/update/restart")
    monkeypatch.setattr(extensions, "install", lambda *a, **k: {"message": "ready", "added": []})
    assert extension_client.post("/api/extensions/install", json={"name": "ilastik"}).status_code == 202
    wait_extension(extension_client, lambda s: not s["job"]["running"])


def test_cancelled_prompt_releases_pending_admission(extension_client, session, monkeypatch):
    from microclaw import extensions
    class Cancelled(asyncio.Lock):
        async def acquire(self):
            await asyncio.sleep(0)
            raise asyncio.CancelledError()
    session.lock = Cancelled()
    prompt = next(r.endpoint for r in extension_client.app.routes if r.path == "/api/prompt")
    async def cancelled():
        with pytest.raises(asyncio.CancelledError):
            await prompt(webserve.Prompt(message="go"), None)
    extension_client.portal.call(cancelled)
    monkeypatch.setattr(extensions, "install", lambda *a, **k: {"message": "ready", "added": []})
    assert extension_client.post("/api/extensions/install", json={"name": "ilastik"}).status_code == 202
    wait_extension(extension_client, lambda s: not s["job"]["running"])
