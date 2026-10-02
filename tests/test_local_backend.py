import json
from pathlib import Path
from types import SimpleNamespace

import httpx

import anthropic
from microclaw import agent, conversation, tools

FIXTURES = Path(__file__).parent / "fixtures" / "llamacpp"


def replay(name):
    head, body = (FIXTURES / f"{name}.http").read_bytes().split(b"\n\n", 1)
    status, headers = head.split(b"\n", 1)
    code = int(status.split()[1])
    return httpx.Response(code, headers=json.loads(headers), content=body)


def install(monkeypatch, name, *, extra=None, stream_chunks=False, vision=False):
    class Captured(list):
        timeouts = []
    captured = Captured()
    remaining = list(name if isinstance(name, list) else [name])
    def handler(request):
        if request.url.path.endswith("chat/completions"):
            captured.append(json.loads(request.content))
            captured.timeouts.append(request.extensions.get("timeout"))
            selected = remaining.pop(0)
            if isinstance(selected, httpx.Response): return selected
            response = replay(selected)
            if extra: response = extra(response)
            if stream_chunks:
                body = response.content
                class Chunks(httpx.SyncByteStream):
                    exhausted = False
                    def __iter__(self):
                        for offset in range(0, len(body), 2048):
                            yield body[offset:offset + 2048]
                        self.exhausted = True
                stream = Chunks()
                captured.append(stream)
                return httpx.Response(response.status_code, headers=response.headers, stream=stream)
            return response
        if request.url.path.endswith("/props"):
            prop = replay("get_props")
            if vision:
                prop = httpx.Response(prop.status_code, headers=prop.headers,
                                      content=prop.content.replace(b'"vision":false', b'"vision":true'))
            return prop
        if request.url.path.endswith("/models"):
            return replay("get_v1_models")
        return replay("stream_text_reply")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(agent, "_local_http", client)
    monkeypatch.setenv("MICROCLAW_LLM_BASE_URL", "http://recorded-server")
    monkeypatch.delenv("MICROCLAW_LLM_API_KEY", raising=False)
    agent._local_vision.clear()
    agent._local_models = None
    return captured


def test_recorded_stream_tool_translation_and_usage(monkeypatch):
    captured = install(monkeypatch, "stream_tool_calls")
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
    tools = [b for b in msg.content if b.type == "tool_use"]
    assert [(b.name, b.id, b.input) for b in tools] == [
        ("set_exposure", "ipQX5BPZX3xG7RuJKJYFu18DrcORALAD", {"ms": 50}),
        ("get_z_position", "AnLs73TL6WYtAeYSb0KB1R5MVeLjb1Ju", {}),
    ]
    reasoning = [e["text"] for e in events if e.get("type") == "text_delta"]
    assert "".join(reasoning) == ""
    assert not any("reasoning" in str(b.model_dump()) for b in msg.content)
    assert captured[0]["messages"][0] == {"role": "system", "content": "system"}
    assert seen[0]["prompt_n"] == 26 and seen[0]["cache_n"] == 33574
    assert seen[0]["reasoning_characters"] > 0


def test_recorded_text_and_length_finish_reasons(monkeypatch):
    for fixture, expected in (("stream_text_reply", "end_turn"), ("stream_length_cutoff", "max_tokens")):
        install(monkeypatch, fixture)
        gen = agent._stream_one_round([], [], "model", [])
        deltas = []
        while True:
            try:
                event = next(gen)
                if event["type"] == "text_delta": deltas.append(event["text"])
            except StopIteration as stop:
                assert stop.value.stop_reason == expected
                if fixture == "stream_text_reply":
                    content = "".join(b.text for b in stop.value.content if b.type == "text")
                    assert content and "".join(deltas) == content
                break


def test_props_and_models_are_recorded(monkeypatch):
    install(monkeypatch, "stream_text_reply")
    assert agent.known_models() == ["Qwen3.8-27B-UD-Q4_K_XL.gguf"]
    assert agent.resolve_model() == "Qwen3.8-27B-UD-Q4_K_XL.gguf"


def test_request_translation_includes_checkpoint_and_all_d2_rows(monkeypatch):
    captured = install(monkeypatch, "stream_text_reply")
    store = conversation.ConversationStore(conversation.AuditLog(None, enabled=False),
                                          high_water_tokens=300, low_water_tokens=100)
    history = []
    for index in range(8):
        history.extend([
            {"role": "user", "content": f"remember checkpoint {index} " + "x" * 300},
            {"role": "assistant", "content": [{"type": "text", "text": "reply " + "y" * 100}]},
        ])
    messages = store.model_messages(history)
    assert store.compaction_count == 1 and messages[0]["content"][0]["text"].startswith("Conversation checkpoint:")
    messages.extend([{ "role": "assistant", "content": [
        {"type": "text", "text": "working"},
        {"type": "tool_use", "id": "a", "name": "alpha", "input": {"x": 1}},
        {"type": "tool_use", "id": "b", "name": "beta", "input": {}},
    ]}, {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "a", "is_error": True, "content": "bad"},
        {"type": "tool_result", "tool_use_id": "b", "content": [
            {"type": "text", "text": "ok"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}},
        ]},
    ]}])
    schemas = [{"name": "alpha", "description": "A", "input_schema": {"type": "object"}, "cache_control": {"type": "ephemeral"}}]
    gen = agent._stream_one_round(messages, [{"type": "text", "text": "sys1", "cache_control": {"type": "ephemeral"}}, {"type": "text", "text": "sys2"}], "m", schemas)
    for _ in gen: pass
    body = captured[0]
    assert body["messages"][0] == {"role": "system", "content": "sys1\n\nsys2"}
    assert body["messages"][1]["content"].startswith("Conversation checkpoint:")
    assistant = next(m for m in body["messages"] if m.get("tool_calls"))
    assert assistant == {"role": "assistant", "content": "working", "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "alpha", "arguments": '{"x":1}'}},
        {"id": "b", "type": "function", "function": {"name": "beta", "arguments": "{}"}},
    ]}
    tools_wire = [m for m in body["messages"] if m["role"] == "tool"]
    assert tools_wire[-2]["content"] == "Error: bad"
    assert tools_wire[-1]["content"].endswith('{"image_omitted": "this model does not accept images"}')
    assert body["tools"][0]["function"]["parameters"] == {"type": "object"}
    assert "cache_control" not in str(body)


def test_local_stream_yields_before_stream_exhaustion(monkeypatch):
    captured = install(monkeypatch, "stream_text_reply", stream_chunks=True)
    gen = agent._stream_one_round([], [], "m", [])
    event = next(gen)
    assert event["type"] == "text_delta"
    assert captured[1].exhausted is False
    assert captured.timeouts[0]["connect"] == 5.0
    assert captured.timeouts[0]["read"] is None
    gen.close()


def test_retry_after_503_then_tool_calls(monkeypatch):
    response = httpx.Response(503, headers={"retry-after": "1"}, json={"error": {"message": "busy"}})
    install(monkeypatch, [response, "stream_tool_calls"])
    monkeypatch.setattr(agent.time, "sleep", lambda _: None)
    gen = agent._stream_one_round([], [], "m", [])
    assert next(gen) == {"type": "retry", "delay": 5, "attempt": 1}
    while True:
        try: next(gen)
        except StopIteration as stop:
            assert stop.value.stop_reason == "tool_use"
            break


def test_429_retry_after_over_cap_returns_local_error(monkeypatch):
    response = httpx.Response(429, headers={"retry-after": "61"}, json={"error": {"message": "busy"}})
    install(monkeypatch, response)
    monkeypatch.setattr(agent.time, "sleep", lambda _: None)
    from microclaw.agent import run_agent_iter
    events = list(run_agent_iter("hello", None, None, [], model="m", tool_schemas=[]))
    assert events[-1]["type"] == "error"
    assert "http://recorded-server" in events[-1]["message"]


def test_vision_images_follow_all_tool_messages(monkeypatch):
    # /props is minimally edited from the recorded text-only response: vision false → true.
    captured = install(monkeypatch, "stream_text_reply", vision=True)
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "alpha", "input": {}},
            {"type": "tool_use", "id": "b", "name": "beta", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "ok"},
            {"type": "tool_result", "tool_use_id": "b", "content": [
                {"type": "text", "text": "image result"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}},
            ]},
        ]},
    ]
    for _ in agent._stream_one_round(messages, [], "m", []): pass
    wire = captured[0]["messages"]
    assert [m["role"] for m in wire[-3:]] == ["tool", "tool", "user"]
    assert wire[-1]["content"] == [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]


def test_empty_ids_are_generated_and_missing_finish_is_not_end_turn(monkeypatch):
    body = replay("stream_tool_calls").content
    body = body.replace(b"ipQX5BPZX3xG7RuJKJYFu18DrcORALAD", b"")
    body = body.replace(b"AnLs73TL6WYtAeYSb0KB1R5MVeLjb1Ju", b"")
    body = b"\n".join(line for line in body.split(b"\n") if b'"finish_reason":"tool_calls"' not in line)
    edited = httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)
    install(monkeypatch, edited)
    gen = agent._stream_one_round([], [], "m", [])
    while True:
        try: next(gen)
        except StopIteration as stop:
            msg = stop.value
            break
    ids = [b.id for b in msg.content if b.type == "tool_use"]
    assert len(ids) == 2 and len(set(ids)) == 2 and all(ids)
    assert msg.stop_reason is None
    install(monkeypatch, edited)
    from microclaw.agent import run_agent_iter
    events = list(run_agent_iter("hello", None, None, [], model="m", tool_schemas=[]))
    assert events[-1]["type"] == "error" and "Unexpected stop reason: None" in events[-1]["message"]


def test_local_http_errors_become_agent_error_events(monkeypatch):
    from microclaw.agent import run_agent_iter
    for name, excerpt in (("error_context_exceeded", "131072"), ("error_bad_key", "MICROCLAW_LLM_API_KEY")):
        install(monkeypatch, replay(name))
        history = []
        events = list(run_agent_iter("hello", None, None, history, model="m", tool_schemas=[]))
        assert events[-1]["type"] == "error" and excerpt in events[-1]["message"]
        if name == "error_context_exceeded":
            assert "140052" in events[-1]["message"]
        assert history == []


def test_serve_accepts_llm_url_before_or_after_subcommand(monkeypatch):
    from microclaw import __main__ as cli, webserve
    monkeypatch.setenv("MICROCLAW_LLM_BASE_URL", "")
    seen = []
    monkeypatch.setattr(webserve, "serve", lambda args: seen.append(args.llm_base_url))
    monkeypatch.setattr("microclaw.shortcut.pause_on_exit", lambda: None)
    for argv in (
        ["microclaw", "--llm-base-url", "http://one", "serve"],
        ["microclaw", "serve", "--llm-base-url", "http://two"],
    ):
        monkeypatch.setattr("sys.argv", argv)
        cli.main()
    assert seen == ["http://one", "http://two"]


def test_local_http_client_is_lazy(monkeypatch):
    monkeypatch.setattr(agent, "_local_http", None)
    assert agent._local_http is None
    client = agent._get_local_http()
    assert isinstance(client, httpx.Client)
    client.close()
    monkeypatch.setattr(agent, "_local_http", None)


def test_export_is_identical_for_anthropic_and_local_replies(monkeypatch, tmp_path):
    install(monkeypatch, "stream_tool_calls")
    local_gen = agent._stream_one_round([], [], "m", [])
    while True:
        try: next(local_gen)
        except StopIteration as stop:
            local_message = stop.value
            break
    response = anthropic.types.Message.model_validate({
        "id": "fake", "type": "message", "role": "assistant", "model": "m",
        "content": [
            {"type": "tool_use", "id": "ipQX5BPZX3xG7RuJKJYFu18DrcORALAD", "name": "set_exposure", "input": {"ms": 50}},
            {"type": "tool_use", "id": "AnLs73TL6WYtAeYSb0KB1R5MVeLjb1Ju", "name": "get_z_position", "input": {}},
        ], "stop_reason": "tool_use", "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    })
    class FakeStream:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def __iter__(self): return iter(())
        def get_final_message(self): return response
    fake_client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: FakeStream()))
    monkeypatch.setattr(agent, "_local_vision", {})
    monkeypatch.delenv("MICROCLAW_LLM_BASE_URL", raising=False)
    monkeypatch.setattr(agent, "_get_client", lambda: fake_client)
    anth_gen = agent._stream_one_round([], [], "m", [])
    while True:
        try: next(anth_gen)
        except StopIteration as stop:
            anthropic_message = stop.value
            break

    def history(message):
        blocks = [b.model_dump() for b in message.content]
        results = [{"type": "tool_result", "tool_use_id": b["id"],
                    "content": json.dumps({"ok": True})} for b in blocks]
        return [{"role": "assistant", "content": blocks}, {"role": "user", "content": results}]

    class Guard:
        def __init__(self, root):
            from types import SimpleNamespace
            self.root = Path(root)
            self._c = SimpleNamespace(stage=SimpleNamespace(x_min=-1000, x_max=1000,
                y_min=-1000, y_max=1000, z_min=-1000, z_max=1000),
                camera=SimpleNamespace(max_exposure_ms=None))
        @property
        def analysis_min_snr(self): return None
        def resolve_in_workspace(self, path): return str(self.root / path)

    outputs = []
    for index, message in enumerate((anthropic_message, local_message)):
        path = f"script{index}.py"
        tools.export_session_script(None, Guard(tmp_path), path, history(message))
        outputs.append((tmp_path / path).read_text())
    assert outputs[0] == outputs[1]

    bad_body = replay("stream_tool_calls").content.replace(b'"arguments":"0"', b'"arguments":"?"')
    bad = httpx.Response(200, headers={"content-type": "text/event-stream"}, content=bad_body)
    install(monkeypatch, bad)
    bad_gen = agent._stream_one_round([], [], "m", [])
    while True:
        try: next(bad_gen)
        except StopIteration as stop:
            malformed = stop.value
            break
    bad_history = history(malformed)
    failed = next(b for b in malformed.content if b.type == "tool_use" and "_microclaw_parse_error" in b.input)
    bad_history[1]["content"] = [{"type": "tool_result", "tool_use_id": failed.id,
                                  "is_error": True, "content": json.dumps({"error": failed.input["_microclaw_parse_error"]})}]
    tools.export_session_script(None, Guard(tmp_path), "bad.py", bad_history)
    bad_script = (tmp_path / "bad.py").read_text()
    assert "core.set_exposure" not in bad_script
    assert "SKIPPED: set_exposure" in bad_script
