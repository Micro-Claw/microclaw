import json
import types
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from microclaw import agent, tools, webserve
from microclaw.__main__ import report_declared_illumination_on_exit
from microclaw.agent import RIG_INTERVIEW_PROMPT, run_agent_iter
from microclaw.tools_schema import TOOLS_CACHED


def _response(name="move_stage_xy"):
    block = types.SimpleNamespace(type="tool_use", id="call-1", name=name, input={})
    return types.SimpleNamespace(content=[block], stop_reason="tool_use")


class _Stream:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(())

    def get_final_message(self):
        return _response()


def test_setup_dispatcher_rejects_the_whole_normal_registry():
    for name in tools.TOOL_REGISTRY:
        result = json.loads(tools.execute_tool(
            name, {}, object(), None, webserve.SETUP_TOOL_REGISTRY,
            setup_mode=True,
        ))
        assert "setup mode" in result["error"]


def test_fabricated_hardware_call_is_rejected_before_function_lookup():
    registry = MagicMock()
    registry.__contains__.return_value = False
    result = json.loads(tools.execute_tool(
        "move_stage_xy", {}, object(), None, registry, setup_mode=True,
    ))
    assert "setup mode" in result["error"]
    registry.get.assert_not_called()


def test_setup_turn_sends_no_normal_schemas_and_refuses_fabricated_call(monkeypatch):
    client = MagicMock()
    client.messages.stream.return_value = _Stream()
    monkeypatch.setattr(agent, "_client", client)
    events = list(run_agent_iter(
        "move", object(), None, [], max_iterations=1,
        tool_schemas=webserve.SETUP_TOOL_SCHEMAS,
        tool_registry=webserve.SETUP_TOOL_REGISTRY,
        setup_mode=True,
    ))
    # A setup session offers nothing, so the request carries no `tools` at all
    # rather than an empty array — the turn must not depend on whether the API
    # accepts `tools: []`.
    assert "tools" not in client.messages.stream.call_args.kwargs
    result = next(event for event in events if event["type"] == "tool_result")
    assert "setup mode" in result["content"]


def test_setup_system_blocks_omit_rig_interview_and_normal_blocks_include_it(
    monkeypatch,
):
    monkeypatch.setattr(agent, "load_knowledge", lambda: {})
    monkeypatch.setattr(agent, "rig_profile_gaps", lambda knowledge: ["stage_layout"])
    setup = "\n".join(block["text"] for block in agent._system_blocks(setup_mode=True))
    normal = "\n".join(block["text"] for block in agent._system_blocks())
    assert RIG_INTERVIEW_PROMPT.splitlines()[0] not in setup
    assert RIG_INTERVIEW_PROMPT.splitlines()[0] in normal


def test_exit_report_is_a_noop_without_a_guard():
    core = MagicMock()
    report_declared_illumination_on_exit(None, core)
    core.assert_not_called()


def test_normal_turn_keeps_the_live_schema_and_registry(monkeypatch):
    called = []

    def normal_tool(ctrl, guard, **kwargs):
        called.append((ctrl, guard))
        return {"ok": True}

    registry = {"normal_tool": normal_tool}
    response = types.SimpleNamespace(
        content=[types.SimpleNamespace(
            type="tool_use", id="call-1", name="normal_tool", input={}
        )],
        stop_reason="tool_use",
    )

    class NormalStream(_Stream):
        def get_final_message(self):
            return response

    client = MagicMock()
    client.messages.stream.return_value = NormalStream()
    monkeypatch.setattr(agent, "_client", client)
    ctrl, guard = object(), object()
    list(run_agent_iter(
        "go", ctrl, guard, [], max_iterations=1,
        tool_schemas=TOOLS_CACHED, tool_registry=registry,
    ))
    assert client.messages.stream.call_args.kwargs["tools"] is TOOLS_CACHED
    assert called == [(ctrl, guard)]


def test_setup_first_message_and_banner_are_exact(monkeypatch, tmp_path):
    class Controller:
        core = object()

        def __init__(self, port, guard):
            assert guard is None

        def is_connected(self):
            return True

    monkeypatch.setattr(webserve, "MicroscopeController", Controller)
    monkeypatch.setattr(webserve, "enumerate_rig", lambda core: {"stages": []})
    monkeypatch.setattr(webserve.credentials, "load_api_key", lambda: (None, None))
    args = types.SimpleNamespace(
        safety_config=str(tmp_path / "missing.yaml"), port=1, model=None,
        save_history=False, host="127.0.0.1", history_retention_days=None,
    )
    session = webserve.build_session(args)
    assert session.mode is webserve.SessionMode.SETUP
    assert session.history == [{
        "role": "assistant", "content": webserve.SETUP_FIRST_MESSAGE,
    }]
    page = TestClient(webserve.build_app(session)).get("/").text
    assert 'class="banner" id="setup-banner"' in page
    assert "Setup mode — hardware control locked" in page
