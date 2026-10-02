import json
import math
from pathlib import Path

import httpx

from microclaw import agent, conversation

FIXTURES = Path(__file__).parent / "fixtures" / "llamacpp"


def recorded(name):
    head, body = (FIXTURES / f"{name}.http").read_bytes().split(b"\n\n", 1)
    status, headers = head.split(b"\n", 1)
    return httpx.Response(int(status.split()[1]), headers=json.loads(headers), content=body)


def test_local_watermarks_use_props_and_tokenize_fixtures(monkeypatch, capsys):
    applied = []
    tokenized = []
    agent._local_vision.clear()
    def handler(request):
        if request.url.path.endswith("/props"):
            return recorded("get_props")
        if request.url.path.endswith("/apply-template"):
            applied.append(json.loads(request.content))
            return recorded("post_apply_template")
        if request.url.path.endswith("/tokenize"):
            tokenized.append(json.loads(request.content))
            return recorded("post_tokenize")
        raise AssertionError(request.url)
    monkeypatch.setenv("MICROCLAW_LLM_BASE_URL", "http://recorded-server")
    monkeypatch.setattr(agent, "_local_http", httpx.Client(transport=httpx.MockTransport(handler)))
    store = agent.make_conversation_store(conversation.AuditLog(None, enabled=False))
    assert store.high_water_tokens == 131072 - 8 - agent.MAX_OUTPUT_TOKENS - 4096
    assert store.low_water_tokens == int(store.high_water_tokens * .75)
    assert applied[0]["messages"][0]["role"] == "system"
    assert applied[0]["messages"][-1] == {"role": "user", "content": "hi"}
    assert applied[0]["tools"] == agent._local_request_body(
        [{"role": "user", "content": "hi"}], agent._system_blocks(),
        agent.TOOLS_CACHED, "budget", False,
    )["tools"]
    assert tokenized == [{"content": recorded("post_apply_template").json()["prompt"]}]
    assert "n_ctx=131072" in capsys.readouterr().out


def test_local_watermarks_fallback_when_server_reports_neither(monkeypatch, capsys):
    applied = []
    agent._local_vision.clear()
    def handler(request):
        if request.url.path.endswith("/props"):
            response = recorded("get_props")
            return httpx.Response(response.status_code, headers=response.headers,
                                  content=response.content.replace(b',"n_ctx":131072', b''))
        if request.url.path.endswith("/models"):
            response = recorded("get_v1_models")
            return httpx.Response(response.status_code, headers=response.headers,
                                  content=response.content.replace(b',"n_ctx":131072', b''))
        if request.url.path.endswith("/apply-template"):
            applied.append(json.loads(request.content))
            return httpx.Response(404, json={})
        return httpx.Response(404, json={})
    monkeypatch.setenv("MICROCLAW_LLM_BASE_URL", "http://unreported-server")
    monkeypatch.setattr(agent, "_local_http", httpx.Client(transport=httpx.MockTransport(handler)))
    store = agent.make_conversation_store(conversation.AuditLog(None, enabled=False))
    fixed_fallback = math.ceil(len(json.dumps(applied[0], ensure_ascii=False,
                                              separators=(",", ":")).encode("utf-8")) / 2.36)
    # The fallback estimate leaves nothing for history at the 32768 default, so
    # the announced provisional floor applies (0ecbbcc, L4) instead of a clamp
    # to 2 that would compact every round.
    computed = 32768 - fixed_fallback - 8192 - 4096
    assert computed < agent.LOCAL_CONTEXT_UNPROBED_HIGH_WATER
    assert store.high_water_tokens == agent.LOCAL_CONTEXT_UNPROBED_HIGH_WATER
    assert store.low_water_tokens == int(store.high_water_tokens * .75)
    out = capsys.readouterr().out
    assert "n_ctx=32768 (default; server did not report n_ctx)" in out
    assert "provisional 4096-token history floor" in out


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


def test_local_store_image_counting_follows_shared_vision_probe(monkeypatch):
    for vision in (False, True):
        agent._local_vision.clear()
        def handler(request):
            if request.url.path.endswith("/props"):
                response = recorded("get_props")
                if vision:
                    return httpx.Response(response.status_code, headers=response.headers,
                                          content=response.content.replace(
                                              b'"vision":false', b'"vision":true'))
                return response
            if request.url.path.endswith("/apply-template"):
                return recorded("post_apply_template")
            if request.url.path.endswith("/tokenize"):
                return recorded("post_tokenize")
            return httpx.Response(404, json={})
        monkeypatch.setenv("MICROCLAW_LLM_BASE_URL", "http://recorded-server")
        monkeypatch.setattr(agent, "_local_http", httpx.Client(transport=httpx.MockTransport(handler)))
        store = agent.make_conversation_store(conversation.AuditLog(None, enabled=False))
        assert store.text_only is (not vision)
        assert agent._local_vision["http://recorded-server"] is vision
        history = [{"role": "user", "content": [{"type": "tool_result",
                    "tool_use_id": "image", "content": [{"type": "image",
                    "source": {"type": "base64", "media_type": "image/png",
                               "data": "A" * 1000}}]}]}]
        store.model_messages(history)
        assert store.last_estimated_tokens == conversation.estimate_tokens(
            history, text_only=not vision
        )


def test_local_request_body_prefix_is_stable_across_rounds():
    system = [{"type": "text", "text": "stable system"}]
    tools = [{"name": "alpha", "description": "A", "input_schema": {"type": "object"}}]
    round_n = [{"role": "user", "content": "first"},
               {"role": "assistant", "content": [
                   {"type": "text", "text": "calling alpha"},
                   {"type": "tool_use", "id": "call-1", "name": "alpha",
                    "input": {"z": 2, "a": 1}},
               ]},
               {"role": "user", "content": [{"type": "tool_result",
                                                "tool_use_id": "call-1",
                                                "content": "done"}]}]
    round_n_plus_one = round_n + [{"role": "user", "content": "next"}]
    first = agent._local_request_body(round_n, system, tools, "m", False)
    second = agent._local_request_body(round_n_plus_one, system, tools, "m", False)
    assert second["messages"][:len(first["messages"])] == first["messages"]
    assert second["tools"] == first["tools"]


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
    context_provider = store.model_messages
    assert context_provider.__self__ is store
    events = run_agent_iter("new", None, None, history, model="m", tool_schemas=[],
                            context_provider=context_provider)
    compaction = next(event for event in events if event["type"] == "compaction")
    assert compaction["estimated_tokens"] is not None
    assert not requested
    list(events)
    assert requested == [True]
