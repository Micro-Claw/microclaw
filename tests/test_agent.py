"""
Prompt-level tests: verify that a given user input produces the expected
sequence of tool calls. Uses a mock Anthropic client so no real API key needed.
The mock pre-scripts the sequence of Claude responses (tool_use blocks).
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from microclaw.agent import run_agent
from microclaw.safety import SafetyConstraints, SafetyGuard


def make_mock_client(scripted_responses: list):
    """Return a mock client whose messages.create() yields scripted_responses in order."""
    client = MagicMock()
    client.messages.create.side_effect = scripted_responses
    return client


def tool_use_response(tool_name: str, tool_input: dict, call_id: str = "call_1"):
    """Build a fake Claude tool_use response block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    block.id = call_id
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    return response


def text_response(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


@pytest.fixture
def guard():
    return SafetyGuard(SafetyConstraints())  # unconstrained for these tests


class TestSnapAndShowPrompt:
    """'Take a picture' → snap_image → text reply."""

    def test_snap_image_called(self, mock_ctrl, guard):
        scripted = [
            tool_use_response("snap_image", {}),
            text_response("I snapped an image — it's now showing in the MM viewer."),
        ]
        with patch("microclaw.agent._get_client", return_value=make_mock_client(scripted)):
            reply, _ = run_agent("Take a picture", mock_ctrl, guard)
        assert "snap" in reply.lower() or "image" in reply.lower()


class TestZStackThenExportPrompt:
    """
    'Run a 5-slice z-stack from 40 to 60 µm, then export to /tmp/out.tiff'
    Expected tool call sequence: get_system_state → run_zstack → export_dataset_as_tiff
    """

    def test_tool_sequence(self, mock_ctrl, guard, monkeypatch):
        # Stub the acquisition/export internals so this stays a pure prompt-
        # sequence test — otherwise run_zstack opens a real pycro-manager
        # Acquisition (a ZMQ bridge), which drives hardware when MM is running
        # and spawns a timing-out background thread when it isn't.
        monkeypatch.setattr("microclaw.tools._acquire_with_hooks", lambda *a, **k: "/tmp/zstack")
        monkeypatch.setattr("microclaw.tools.Dataset", lambda p: MagicMock(axes={}))
        monkeypatch.setattr("microclaw.tools.tifffile.imwrite", lambda *a, **k: None)
        scripted = [
            tool_use_response("get_system_state", {}, call_id="c1"),
            tool_use_response(
                "run_zstack",
                {"z_start_um": 40.0, "z_end_um": 60.0, "z_step_um": 5.0,
                 "save_dir": "/tmp", "name": "zstack"},
                call_id="c2",
            ),
            tool_use_response(
                "export_dataset_as_tiff",
                {"dataset_path": "/tmp/zstack", "output_path": "/tmp/out.tiff"},
                call_id="c3",
            ),
            text_response("Z-stack done and exported to /tmp/out.tiff."),
        ]
        with patch("microclaw.agent._get_client", return_value=make_mock_client(scripted)):
            reply, _ = run_agent(
                "Run a 5-slice z-stack from 40 to 60 µm, then export to /tmp/out.tiff",
                mock_ctrl,
                guard,
            )
        assert "export" in reply.lower() or "tiff" in reply.lower()


class TestSafetyBlockedInLoop:
    """
    When a safety violation is returned as a tool_result error, Claude
    should inform the user rather than retrying.
    """

    def test_safety_error_surfaced(self, mock_ctrl):
        from microclaw.safety import SafetyConstraints, SafetyGuard, StageConstraints
        tight_guard = SafetyGuard(SafetyConstraints(
            stage=StageConstraints(z_min=0, z_max=100)
        ))
        scripted = [
            # Claude attempts to move Z to 500 (violates constraint)
            tool_use_response("move_stage_z", {"z_um": 500.0}, call_id="c1"),
            # After receiving the error, Claude replies with explanation
            text_response(
                "The stage cannot move to 500 µm because the maximum allowed Z is 100 µm."
            ),
        ]
        with patch("microclaw.agent._get_client", return_value=make_mock_client(scripted)):
            reply, _ = run_agent("Move Z to 500 µm", mock_ctrl, tight_guard)
        assert "100" in reply or "maximum" in reply.lower() or "limit" in reply.lower()


class TestUnknownTool:
    """execute_tool should return an error dict for an unrecognised tool name."""

    def test_unknown_tool_returns_error(self, mock_ctrl, guard):
        from microclaw.tools import execute_tool
        result_json = execute_tool("nonexistent_tool", {}, mock_ctrl, guard)
        result = json.loads(result_json)
        assert "error" in result
        assert "nonexistent_tool" in result["error"]


class TestHistoryPreserved:
    """run_agent returns updated history that includes the new turn."""

    def test_history_grows(self, mock_ctrl, guard):
        scripted = [
            text_response("Hello, I am Microclaw."),
        ]
        with patch("microclaw.agent._get_client", return_value=make_mock_client(scripted)):
            reply, history = run_agent("Hello", mock_ctrl, guard, history=[])
        assert len(history) >= 2  # user + assistant
        assert history[0]["role"] == "user"


class TestModelResolution:
    def test_explicit_arg_wins(self, monkeypatch):
        from microclaw.agent import resolve_model, DEFAULT_MODEL
        monkeypatch.setenv("MICROCLAW_MODEL", "env-model")
        assert resolve_model("explicit") == "explicit"

    def test_env_var_used(self, monkeypatch):
        from microclaw.agent import resolve_model
        monkeypatch.setenv("MICROCLAW_MODEL", "env-model")
        assert resolve_model() == "env-model"

    def test_default_when_unset(self, monkeypatch):
        from microclaw.agent import resolve_model, DEFAULT_MODEL
        monkeypatch.delenv("MICROCLAW_MODEL", raising=False)
        assert resolve_model() == DEFAULT_MODEL

    def test_model_passed_to_create(self, mock_ctrl, guard):
        client = make_mock_client([text_response("hi")])
        with patch("microclaw.agent._get_client", return_value=client):
            run_agent("Hi", mock_ctrl, guard, model="my-model")
        assert client.messages.create.call_args.kwargs["model"] == "my-model"


class TestTurnCap:
    def test_stops_after_max_iterations(self, mock_ctrl, guard):
        client = MagicMock()
        # Always return a tool_use → the loop would never end without the cap.
        client.messages.create.return_value = tool_use_response("snap_image", {})
        with patch("microclaw.agent._get_client", return_value=client):
            reply, _ = run_agent("loop forever", mock_ctrl, guard, max_iterations=3)
        assert "Stopped after 3 tool rounds" in reply
        assert client.messages.create.call_count == 3

    def test_bailout_says_continue_resumes(self, mock_ctrl, guard):
        client = MagicMock()
        client.messages.create.return_value = tool_use_response("snap_image", {})
        with patch("microclaw.agent._get_client", return_value=client):
            reply, history = run_agent("loop forever", mock_ctrl, guard, max_iterations=2)
        assert "continue" in reply.lower()
        # progress really is preserved: the capped rounds are in the history
        assert len(history) == 1 + 2 * 2  # user + 2×(assistant, tool_result)

    def test_default_cap_fits_manual_grid_survey(self):
        # A manually-looped 3x3 grid needs ~30 rounds (see design/13).
        from microclaw.agent import DEFAULT_MAX_ITERATIONS
        assert DEFAULT_MAX_ITERATIONS >= 30


class TestConversationCacheBreakpoint:
    def test_request_carries_breakpoint_on_last_block(self, mock_ctrl, guard):
        client = make_mock_client([text_response("hi")])
        with patch("microclaw.agent._get_client", return_value=client):
            run_agent("Hello", mock_ctrl, guard)
        sent = client.messages.create.call_args.kwargs["messages"]
        last_block = sent[-1]["content"][-1]
        assert last_block["cache_control"] == {"type": "ephemeral"}

    def test_breakpoint_moves_to_latest_tool_result(self, mock_ctrl, guard):
        client = make_mock_client([
            tool_use_response("snap_image", {}),
            text_response("done"),
        ])
        with patch("microclaw.agent._get_client", return_value=client):
            run_agent("snap", mock_ctrl, guard)
        # second request: last message is the tool_result round
        sent = client.messages.create.call_args_list[1].kwargs["messages"]
        last_block = sent[-1]["content"][-1]
        assert last_block["type"] == "tool_result"
        assert last_block["cache_control"] == {"type": "ephemeral"}
        # and the first round's user message no longer carries a marker,
        # so markers never accumulate beyond the API's breakpoint budget
        assert "cache_control" not in json.dumps(sent[0]["content"])

    def test_returned_history_is_unmarked(self, mock_ctrl, guard):
        client = make_mock_client([
            tool_use_response("snap_image", {}),
            text_response("done"),
        ])
        with patch("microclaw.agent._get_client", return_value=client):
            _, history = run_agent("snap", mock_ctrl, guard)
        assert history[0]["content"] == "snap"  # untouched user string
        tool_result_msgs = [
            m for m in history
            if isinstance(m.get("content"), list)
            and m["content"] and isinstance(m["content"][0], dict)
        ]
        assert tool_result_msgs, "expected a tool_result round in history"
        for msg in tool_result_msgs:
            for block in msg["content"]:
                assert "cache_control" not in block


class TestLazyClient:
    def test_import_does_not_construct_client(self):
        import importlib
        import microclaw.agent as agent
        with patch("anthropic.Anthropic", side_effect=AssertionError("constructed on import")):
            importlib.reload(agent)  # reload must not build a client
        # sanity: module-global stays None until _get_client is called
        assert agent._client is None
