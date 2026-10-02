import json
import os
from pathlib import Path

import httpx

from microclaw import agent, conversation

FIXTURES = Path(__file__).parent / "fixtures" / "llamacpp"


def recorded(name):
    head, body = (FIXTURES / f"{name}.http").read_bytes().split(b"\n\n", 1)
    status, headers = head.split(b"\n", 1)
    return httpx.Response(int(status.split()[1]), headers=json.loads(headers), content=body)


def test_local_watermarks_use_props_and_tokenize_fixtures(monkeypatch, capsys):
    def handler(request):
        if request.url.path.endswith("/props"):
            return recorded("get_props")
        if request.url.path.endswith("/tokenize"):
            return recorded("post_tokenize")
        raise AssertionError(request.url)
    monkeypatch.setenv("MICROCLAW_LLM_BASE_URL", "http://recorded-server")
    monkeypatch.setattr(agent, "_local_http", httpx.Client(transport=httpx.MockTransport(handler)))
    store = agent.make_conversation_store(conversation.AuditLog(None, enabled=False))
    assert store.high_water_tokens == 131072 - 8 - agent.MAX_OUTPUT_TOKENS - 4096
    assert store.low_water_tokens == int(store.high_water_tokens * .75)
    assert "n_ctx=131072" in capsys.readouterr().out


def test_local_watermarks_fallback_when_server_reports_neither(monkeypatch, capsys):
    def handler(request):
        if request.url.path.endswith("/props"):
            response = recorded("get_props")
            return httpx.Response(response.status_code, headers=response.headers,
                                  content=response.content.replace(b',"n_ctx":131072', b''))
        if request.url.path.endswith("/models"):
            response = recorded("get_v1_models")
            return httpx.Response(response.status_code, headers=response.headers,
                                  content=response.content.replace(b',"n_ctx":131072', b''))
        return httpx.Response(404, json={})
    monkeypatch.setenv("MICROCLAW_LLM_BASE_URL", "http://unreported-server")
    monkeypatch.setattr(agent, "_local_http", httpx.Client(transport=httpx.MockTransport(handler)))
    store = agent.make_conversation_store(conversation.AuditLog(None, enabled=False))
    assert 32768 - 8192 - 4096 - store.high_water_tokens > 0
    assert store.low_water_tokens == int(store.high_water_tokens * .75)
    assert "n_ctx=32768 (default; server did not report n_ctx)" in capsys.readouterr().out


def test_anthropic_store_retains_120k_90k_watermarks(monkeypatch):
    monkeypatch.delenv("MICROCLAW_LLM_BASE_URL", raising=False)
    store = agent.make_conversation_store(conversation.AuditLog(None, enabled=False))
    assert (store.high_water_tokens, store.low_water_tokens) == (120_000, 90_000)


def test_local_text_estimate_omits_tool_result_image_bytes():
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": "A" * 120_000}}
    history = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x",
                                                 "content": [image]}]}]
    text_store = conversation.ConversationStore(conversation.AuditLog(None, enabled=False),
                                                high_water_tokens=100_000,
                                                low_water_tokens=70_000, text_only=True)
    normal_store = conversation.ConversationStore(conversation.AuditLog(None, enabled=False),
                                                  high_water_tokens=100_000,
                                                  low_water_tokens=70_000)
    text_store.model_messages(history)
    normal_store.model_messages(history)
    assert normal_store.last_estimated_tokens - text_store.last_estimated_tokens > 40_000


def test_local_request_body_prefix_is_stable_across_rounds():
    system = [{"type": "text", "text": "stable system"}]
    tools = [{"name": "alpha", "description": "A", "input_schema": {"type": "object"}}]
    earlier = [{"role": "user", "content": "first"},
               {"role": "assistant", "content": [{"type": "text", "text": "reply"}]}]
    first = agent._local_request_body(earlier, system, tools, "m", False)
    second = agent._local_request_body(earlier + [{"role": "user", "content": "next"}],
                                       system, tools, "m", False)
    left = json.dumps(first, separators=(",", ":"))
    right = json.dumps(second, separators=(",", ":"))
    shared = os.path.commonprefix([left, right])
    assert '"role":"system","content":"stable system"' in shared
    assert json.dumps(first["tools"][0], separators=(",", ":")) in shared
    for message in first["messages"]:
        assert json.dumps(message, separators=(",", ":")) in shared


def test_compaction_event_precedes_local_request(monkeypatch):
    requested = []
    def handler(request):
        if request.url.path.endswith("/props"):
            return recorded("get_props")
        if request.url.path.endswith("/chat/completions"):
            requested.append(True)
            return recorded("stream_text_reply")
        return httpx.Response(404, json={})
    monkeypatch.setenv("MICROCLAW_LLM_BASE_URL", "http://recorded-server")
    monkeypatch.setattr(agent, "_local_http", httpx.Client(transport=httpx.MockTransport(handler)))
    store = conversation.ConversationStore(conversation.AuditLog(None, enabled=False),
                                           high_water_tokens=80, low_water_tokens=40)
    history = []
    for index in range(7):
        history.extend([
            {"role": "user", "content": f"question {index} " + "x" * 100},
            {"role": "assistant", "content": [{"type": "text", "text": "answer " + "y" * 100}]},
        ])
    from microclaw.agent import run_agent_iter
    events = run_agent_iter("new", None, None, history, model="m", tool_schemas=[],
                            context_provider=store.model_messages)
    compaction = next(event for event in events if event["type"] == "compaction")
    assert compaction["estimated_tokens"] is not None
    assert not requested
    list(events)
    assert requested == [True]
