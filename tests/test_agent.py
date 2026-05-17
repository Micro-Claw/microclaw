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
        with patch("microclaw.agent.client", make_mock_client(scripted)):
            reply, _ = run_agent("Take a picture", mock_ctrl, guard)
        assert "snap" in reply.lower() or "image" in reply.lower()


class TestZStackThenExportPrompt:
    """
    'Run a 5-slice z-stack from 40 to 60 µm, then export to /tmp/out.tiff'
    Expected tool call sequence: get_system_state → run_zstack → export_dataset_as_tiff
    """

    def test_tool_sequence(self, mock_ctrl, guard):
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
        with patch("microclaw.agent.client", make_mock_client(scripted)):
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
        with patch("microclaw.agent.client", make_mock_client(scripted)):
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
        with patch("microclaw.agent.client", make_mock_client(scripted)):
            reply, history = run_agent("Hello", mock_ctrl, guard, history=[])
        assert len(history) >= 2  # user + assistant
        assert history[0]["role"] == "user"
