import json
from pathlib import Path

import httpx

from microclaw import agent

FIXTURES = Path(__file__).parent / "fixtures" / "llamacpp"


def replay(name):
    head, body = (FIXTURES / f"{name}.http").read_bytes().split(b"\n\n", 1)
    status, headers = head.split(b"\n", 1)
    code = int(status.split()[1])
    return httpx.Response(code, headers=json.loads(headers), content=body)


def install(monkeypatch, name):
    def handler(request):
        if request.url.path.endswith("/props"):
            return replay("get_props")
        if request.url.path.endswith("/models"):
            return replay("get_v1_models")
        return replay(name)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(agent, "_local_http", client)
    monkeypatch.setenv("MICROCLAW_LLM_BASE_URL", "http://recorded-server")
    monkeypatch.delenv("MICROCLAW_LLM_API_KEY", raising=False)
    agent._local_vision.clear()
    agent._local_models = None


def test_recorded_stream_tool_translation_and_usage(monkeypatch):
    install(monkeypatch, "stream_tool_calls")
    seen = []
    gen = agent._stream_one_round([], [{"type": "text", "text": "system"}], "model", [
        {"name": "a", "description": "A", "input_schema": {"type": "object"}}
    ], usage_sink=seen.append)
    events = []
    while True:
        try: events.append(next(gen))
        except StopIteration as stop:
            msg = stop.value; break
    assert msg.stop_reason == "tool_use"
    assert len([b for b in msg.content if b.type == "tool_use"]) == 2
    assert not any("The" == e.get("text") for e in events)
    assert seen[0]["prompt_n"] == 26 and seen[0]["cache_n"] == 33574


def test_recorded_text_and_length_finish_reasons(monkeypatch):
    for fixture, expected in (("stream_text_reply", "end_turn"), ("stream_length_cutoff", "max_tokens")):
        install(monkeypatch, fixture)
        gen = agent._stream_one_round([], [], "model", [])
        while True:
            try: next(gen)
            except StopIteration as stop:
                assert stop.value.stop_reason == expected
                break


def test_props_and_models_are_recorded(monkeypatch):
    install(monkeypatch, "stream_text_reply")
    assert agent.known_models() == ["Qwen3.8-27B-UD-Q4_K_XL.gguf"]
    assert agent.resolve_model() == "Qwen3.8-27B-UD-Q4_K_XL.gguf"
