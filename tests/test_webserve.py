"""Tests for `microclaw serve` (design/15 v1).

`build_app` is exercised against a fake session — no Micro-Manager, no Anthropic
call — so these cover the properties that actually matter for a browser endpoint
wired to real hardware: one turn at a time, no cross-origin driving, no key
echoed back, and a refusal to bind beyond localhost without an opt-in.
"""
import contextlib
import json
import socket
import time
import types

import pytest

from fastapi.testclient import TestClient

from microclaw import credentials, webserve
from microclaw.webserve import build_app, serve


class _FakeLock:
    """asyncio.Lock() binds to the loop that first awaits it; TestClient runs
    its own. A stub keeps `locked()` under the test's control."""

    def __init__(self, locked=False):
        self._locked = locked

    def locked(self):
        return self._locked

    async def __aenter__(self):
        self._locked = True

    async def __aexit__(self, *exc):
        self._locked = False


@pytest.fixture
def session():
    return types.SimpleNamespace(
        ctrl=object(),
        guard=object(),
        model=None,
        history=[],
        history_fn="unused.json",
        save=False,          # never write a history file from a test
        editable=True,
        lock=_FakeLock(),
    )


@pytest.fixture
def client(session, monkeypatch):
    # A key is present unless a test says otherwise.
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("sk-ant-secret-AA8f", "env"))
    return TestClient(build_app(session))


def _run_agent(reply="ok", tool_calls=()):
    def fake(msg, ctrl, guard, history, model=None, **kw):
        new = list(history) + [{"role": "user", "content": msg}]
        new.append({"role": "assistant", "content": [{"type": "text", "text": reply}]})
        return reply, new
    return fake


# ---- the page ----

def test_index_is_self_contained(client):
    """Served over HTTP, the page can't resolve transcript.css/js as siblings."""
    html = client.get("/").text
    assert 'href="transcript.css"' not in html
    assert 'src="transcript.js"' not in html
    assert "global.Transcript = {" in html      # transcript.js inlined
    assert "--tool-line:" in html               # transcript.css inlined


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

def test_prompt_runs_a_turn_and_appends_to_history(session, client, monkeypatch):
    monkeypatch.setattr(webserve, "run_agent", _run_agent("channel set to DAPI"))

    res = client.post("/api/prompt", json={"message": "  set DAPI  "})
    assert res.status_code == 200
    assert res.json() == {"reply": "channel set to DAPI"}

    # The stripped prompt is what reached the agent, and history is the server's.
    assert session.history[0] == {"role": "user", "content": "set DAPI"}
    assert client.get("/api/history").json() == session.history


def test_prompt_passes_the_session_model_through(session, client, monkeypatch):
    seen = {}

    def fake(msg, ctrl, guard, history, model=None, **kw):
        seen.update(ctrl=ctrl, guard=guard, model=model)
        return "ok", list(history)

    session.model = "claude-opus-4-8"
    monkeypatch.setattr(webserve, "run_agent", fake)
    client.post("/api/prompt", json={"message": "hi"})
    assert seen == {"ctrl": session.ctrl, "guard": session.guard, "model": "claude-opus-4-8"}


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
    monkeypatch.setattr(webserve, "run_agent", _run_agent())
    monkeypatch.setattr(credentials, "load_api_key", lambda: ("k", "env"))
    session.save = True
    session.history_fn = str(tmp_path / "h.json")

    TestClient(build_app(session)).post("/api/prompt", json={"message": "snap"})
    assert json.loads((tmp_path / "h.json").read_text(encoding="utf-8"))[0]["content"] == "snap"


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
    monkeypatch.setattr(webserve, "run_agent", _run_agent())
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
        webserve._open_when_ready("127.0.0.1", port, f"http://127.0.0.1:{port}")
        time.sleep(0.3)
        assert opened == []          # bound, but not accepting yet

        sock.listen(1)
        for _ in range(50):
            if opened:
                break
            time.sleep(0.05)
    finally:
        sock.close()
    assert opened == [f"http://127.0.0.1:{port}"]


def test_browser_opener_gives_up_instead_of_hanging(monkeypatch):
    """A server that never comes up must not leave a thread spinning forever."""
    opened = []
    monkeypatch.setattr(webserve.webbrowser, "open", opened.append)
    with _free_port() as port:
        webserve._open_when_ready("127.0.0.1", port, "http://unused", timeout=0.2)
    time.sleep(0.6)
    assert opened == []


def test_serve_refuses_a_non_local_bind_without_allow_remote():
    with pytest.raises(SystemExit, match="Refusing to bind"):
        serve(_args())


def test_serve_requires_a_safety_config():
    with pytest.raises(SystemExit, match="--safety-config"):
        serve(_args(host="127.0.0.1", safety_config=None))
