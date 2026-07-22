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

from microclaw import config, credentials, webserve
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
    )
    # Session.confirm only reads _emit/pending/cancel, so binding the real
    # method makes the fake route confirmations exactly as the real one does.
    s.confirm = webserve.Session.confirm.__get__(s)
    return s


@pytest.fixture
def client(session, monkeypatch):
    # A key is present unless a test says otherwise.
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("sk-ant-secret-AA8f", "env"))
    return TestClient(build_app(session))


def _agent_iter(reply="ok", tool_calls=()):
    """A stand-in for run_agent_iter: appends to `messages` in place, as the
    real one does, and yields the event sequence a turn produces."""

    def fake(msg, ctrl, guard, messages, model=None, **kw):
        messages.append({"role": "user", "content": msg})
        yield {"type": "round_start", "iteration": 0}
        for i, (name, tool_input) in enumerate(tool_calls):
            tid = f"t{i}"
            messages.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": name, "input": tool_input}]})
            yield {"type": "tool_use", "id": tid, "name": name, "input": tool_input}
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": "{}"}]})
            yield {"type": "tool_result", "tool_use_id": tid, "content": "{}",
                   "is_error": False}
        messages.append({"role": "assistant", "content": [{"type": "text", "text": reply}]})
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
    assert client.get("/api/history").json() == []


def test_history_serialises_sdk_content_blocks(session, client):
    """Assistant turns hold Anthropic SDK objects, not dicts."""
    class Block:
        def model_dump(self):
            return {"type": "text", "text": "hi"}

    session.history = [{"role": "assistant", "content": [Block()]}]
    assert client.get("/api/history").json() == [
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}
    ]


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
    assert client.get("/api/history").json() == session.history


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
                   kind="knowledge"):
    """Run session.confirm on a thread, as a tool on the turn thread would.

    Returns once the confirm is pending (or the thread already returned), with
    the thread, the events it emitted, and a box the answer lands in.
    """
    events = []
    session._emit = events.append
    box = {}
    thread = threading.Thread(
        target=lambda: box.update(answer=session.confirm(summary, kind))
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


def test_the_browser_can_approve_a_pending_confirm(session, client, fast_confirm_poll):
    thread, events, box = _start_confirm(session)
    pid = session.pending.id
    assert events[0] == {"type": "confirm_request", "id": pid,
                         "summary": "Save knowledge devices/X:\nX: {a: 1}",
                         "kind": "knowledge"}

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
                    "kind": "illumination"}

    client.post("/api/confirm", json={"id": pid, "approve": False})
    thread.join(timeout=5)
    assert box["answer"] is False
    assert client.get("/api/confirm").json() == {}


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
        yield {"type": "done", "reply": "ok"}

    monkeypatch.setattr(webserve, "run_agent_iter", fake)
    client.post("/api/prompt", json={"message": "hi"})
    assert seen["emit_bound"] is True
    assert _settle(session)
    assert session._emit is None            # cleared in run_turn's finally


def test_serve_wires_the_confirm_seam_to_the_session(monkeypatch):
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

    serve(_args(host="127.0.0.1"))
    assert tools.CONFIRM_FN is fake.confirm


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
                safety_config="x.yaml", port=4827, model=None, save_history=False)
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

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    try:
        thread = webserve._open_when_ready(
            "127.0.0.1", port, f"http://127.0.0.1:{port}"
        )
        time.sleep(0.3)
        assert opened == []          # bound, but not accepting yet

        sock.listen(1)
        # join, not a fixed poll budget: a refused connect costs a retransmit
        # timeout on Windows, so one in-flight attempt can outlast a couple of
        # seconds of sleeping. This deadlocked nothing — it just made the test
        # a race against the scheduler, which it lost on a loaded rig.
        thread.join(timeout=20)
        assert not thread.is_alive(), "the poll thread never noticed the listen()"
    finally:
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

    It means "the file `microclaw init` wrote", not "no limits". Absent, serve
    must refuse and point at `microclaw init` rather than start unguarded.
    """
    missing = tmp_path / "safety_config.yaml"
    monkeypatch.setattr(config, "default_safety_config", lambda: missing)
    with pytest.raises(SystemExit, match="microclaw init"):
        serve(_args(host="127.0.0.1", safety_config=None))


def test_serve_refuses_an_unreviewed_safety_config(tmp_path):
    """The gate a double-click cannot get past without a human editing a line."""
    cfg = tmp_path / "safety_config.yaml"
    cfg.write_text(
        "schema_version: 1\nreviewed: false\n"
        "stage: {x_min: -1.0, x_max: 1.0}\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="has not been reviewed"):
        serve(_args(host="127.0.0.1", safety_config=str(cfg)))
