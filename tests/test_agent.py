"""
Prompt-level tests: verify that a given user input produces the expected
sequence of tool calls. Uses a mock Anthropic client so no real API key needed.
The mock pre-scripts the sequence of Claude responses (tool_use blocks).
"""
import json
import threading
import types
import anthropic
import httpx
import pytest
from unittest.mock import MagicMock, patch
from microclaw.agent import MAX_OUTPUT_TOKENS, SYSTEM_PROMPT, run_agent, run_agent_iter
from microclaw.safety import SafetyConstraints, SafetyGuard


class FakeStream:
    """Stand-in for `client.messages.stream(...)`: a context manager that
    iterates SDK-shaped stream events and hands back the final Message."""

    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for block in self._response.content:
            if getattr(block, "type", None) == "text":
                yield types.SimpleNamespace(
                    type="content_block_delta",
                    delta=types.SimpleNamespace(type="text_delta", text=block.text),
                )

    def get_final_message(self):
        return self._response


def make_mock_client(scripted_responses: list):
    """Return a mock client whose messages.stream() yields scripted_responses in order."""
    client = MagicMock()
    responses = iter(scripted_responses)
    client.messages.stream.side_effect = lambda **kw: FakeStream(next(responses))
    return client


def looping_mock_client(response):
    """A client that returns the same response forever (for the iteration cap)."""
    client = MagicMock()
    client.messages.stream.side_effect = lambda **kw: FakeStream(response)
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


def api_history_is_valid(messages):
    """The tool-use/result invariant enforced by the Messages API."""
    for index, message in enumerate(messages):
        if message["role"] != "assistant":
            continue
        tool_ids = {
            getattr(block, "id", None) or block.get("id")
            for block in message["content"]
            if (getattr(block, "type", None)
                or (isinstance(block, dict) and block.get("type"))) == "tool_use"
        }
        if not tool_ids:
            continue
        if index + 1 >= len(messages) or messages[index + 1]["role"] != "user":
            return False
        result_ids = {
            block["tool_use_id"] for block in messages[index + 1]["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        }
        if result_ids != tool_ids:
            return False
    return not messages or messages[-1]["role"] != "assistant" or not any(
        (getattr(block, "type", None)
         or (isinstance(block, dict) and block.get("type"))) == "tool_use"
        for block in messages[-1]["content"]
    )


def status_error(error_type, status):
    return error_type(
        f"HTTP {status}",
        response=httpx.Response(
            status, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        ),
        body=None,
    )


def connection_error(error_type=anthropic.APIConnectionError):
    return error_type(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )


@pytest.fixture
def guard():
    return SafetyGuard(SafetyConstraints())  # unconstrained for these tests


class TestSnapAndShowPrompt:
    """'Take a picture' → snap tool → text reply."""

    def test_tool_called_then_reply(self, mock_ctrl, guard):
        scripted = [
            tool_use_response("get_system_state", {}),
            text_response("I snapped an image — it's now showing in the MM viewer."),
        ]
        with patch("microclaw.agent._get_client", return_value=make_mock_client(scripted)):
            reply, _ = run_agent("Take a picture", mock_ctrl, guard)
        assert "snap" in reply.lower() or "image" in reply.lower()


class TestOperatorEstablishedStatePrompt:
    """design/37 F3: two incidents on M5, both state changes nobody asked for.

    The operator's 640 nm laser was disabled to "get the rig into a safe state"
    before a restart, and live view was started because it was "a habit I follow
    by default". These assert the clauses that catch each. Prompt text is only
    testable as text, so these are substring assertions (the pattern
    tests/test_position_list.py:213 already uses) — rewording the prompt means
    rewording these, deliberately.
    """

    def test_the_rig_state_is_the_operators_in_either_direction(self):
        # Catches the laser: "make safe" is named, and so is the reasoning that
        # licensed it — that the guard let the write through.
        assert "the rig's state is the operator's" in SYSTEM_PROMPT
        assert "do not change it in either direction unasked" in SYSTEM_PROMPT
        assert "never restore, tidy, or \"make safe\" on their behalf" in SYSTEM_PROMPT
        assert "not \"this is yours to do\"" in SYSTEM_PROMPT

    def test_the_viewer_is_covered_by_the_rule_not_by_an_exception(self):
        # Catches live view, and pins WHERE it is caught. The viewer is named
        # inside the general rule rather than appended as a special case: live
        # view was not state the operator established (it did not exist), so a
        # rule scoped to what they set up cannot reach it — nor the next
        # unrequested action after it.
        rule = next(
            line for line in SYSTEM_PROMPT.splitlines()
            if "the rig's state is the operator's" in line
        )
        assert "the viewer" in rule
        assert "anything they set up or can see" in rule
        # ...and the same line must keep bookkeeping unblocked, or every ROI
        # change made while carrying out a request starts asking permission.
        assert "Do NOT ask permission for reversible bookkeeping" in rule
        assert "that carries out what was asked" in rule

    def test_a_restart_does_not_license_shuttering_their_excitation(self):
        assert "A microclaw restart is software-only and by itself is neither" in SYSTEM_PROMPT
        assert "offer to restore it afterward" in SYSTEM_PROMPT

    def test_eye_safety_still_triggers_on_the_operators_word(self):
        # The rule must not require verifying that an interaction is real: the
        # operator's word is the only evidence available, and the failure mode
        # here is NOT shuttering. Phrase-matching ("I will now ...") is what
        # misfired and is gone; taking their word for it is not.
        assert "Shutter the excitation before manual or physical interaction with the rig" in SYSTEM_PROMPT
        assert "take their word for it, do not wait to verify it" in SYSTEM_PROMPT
        assert "do not look for a particular phrase" in SYSTEM_PROMPT

    def test_laser_entry_state_is_named_explicitly(self):
        rule = next(
            line for line in SYSTEM_PROMPT.splitlines()
            if "At the end of a task involving lasers" in line
        )
        assert "what microclaw turned on, microclaw turns off" in rule
        assert "what it found on, it leaves on" in rule


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
        assert client.messages.stream.call_args.kwargs["model"] == "my-model"


class TestTurnCap:
    def test_stops_after_max_iterations(self, mock_ctrl, guard):
        # Always return a tool_use → the loop would never end without the cap.
        client = looping_mock_client(tool_use_response("get_system_state", {}))
        with patch("microclaw.agent._get_client", return_value=client):
            reply, _ = run_agent("loop forever", mock_ctrl, guard, max_iterations=3)
        assert "Stopped after 3 tool rounds" in reply
        assert client.messages.stream.call_count == 3

    def test_bailout_says_continue_resumes(self, mock_ctrl, guard):
        client = looping_mock_client(tool_use_response("get_system_state", {}))
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
        sent = client.messages.stream.call_args.kwargs["messages"]
        last_block = sent[-1]["content"][-1]
        assert last_block["cache_control"] == {"type": "ephemeral"}

    def test_breakpoint_moves_to_latest_tool_result(self, mock_ctrl, guard):
        client = make_mock_client([
            tool_use_response("get_system_state", {}),
            text_response("done"),
        ])
        with patch("microclaw.agent._get_client", return_value=client):
            run_agent("snap", mock_ctrl, guard)
        # second request: last message is the tool_result round
        sent = client.messages.stream.call_args_list[1].kwargs["messages"]
        last_block = sent[-1]["content"][-1]
        assert last_block["type"] == "tool_result"
        assert last_block["cache_control"] == {"type": "ephemeral"}
        # and the first round's user message no longer carries a marker,
        # so markers never accumulate beyond the API's breakpoint budget
        assert "cache_control" not in json.dumps(sent[0]["content"])

    def test_returned_history_is_unmarked(self, mock_ctrl, guard):
        client = make_mock_client([
            tool_use_response("get_system_state", {}),
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


class TestRunAgentIter:
    """The generator `run_agent` is now a drain of (design/16 §2)."""

    def _drain(self, scripted, messages, ctrl, guard, **kw):
        with patch("microclaw.agent._get_client", return_value=make_mock_client(scripted)):
            return list(run_agent_iter("go", ctrl, guard, messages, **kw))

    def test_text_only_turn_streams_deltas_then_done(self, mock_ctrl, guard):
        events = self._drain([text_response("hello there")], [], mock_ctrl, guard)
        assert [e["type"] for e in events] == ["round_start", "text_delta", "done"]
        assert events[-1]["reply"] == "hello there"

    def test_tool_use_precedes_its_result(self, mock_ctrl, guard):
        scripted = [tool_use_response("get_system_state", {}, call_id="c1"),
                    text_response("done")]
        events = self._drain(scripted, [], mock_ctrl, guard)
        assert [e["type"] for e in events] == [
            "round_start", "tool_use", "tool_result",
            "round_start", "text_delta", "done",
        ]
        assert events[1]["id"] == "c1" == events[2]["tool_use_id"]

    @pytest.mark.parametrize(
        ("decision", "tool_payload"),
        [
            ("approved", {"status": "Channel set."}),
            ("declined", {"error": "Safety constraint prevented this action."}),
        ],
    )
    def test_tool_result_carries_confirmations_issued_during_dispatch(
        self, mock_ctrl, guard, decision, tool_payload
    ):
        records = []
        summary = "SELECT ILLUMINATION SHUTTER Core.Shutter = White Light Shutter"

        def execute(*args, **kwargs):
            records.append({
                "timestamp": "2026-08-03T12:00:00+00:00",
                "identity": "loopback",
                "confirmation_id": "confirm-1",
                "kind": "illumination",
                "decision": decision,
                "summary": summary,
            })
            return json.dumps(tool_payload)

        scripted = [tool_use_response("set_channel", {"preset": "DAPI"}),
                    text_response("done")]
        with patch("microclaw.agent.execute_tool", side_effect=execute):
            events = self._drain(
                scripted, [], mock_ctrl, guard, confirmation_records=records
            )

        result = json.loads(next(e for e in events if e["type"] == "tool_result")["content"])
        assert result["confirmations"] == [{
            "kind": "illumination",
            "decision": decision,
            "summary": summary,
        }]

    def test_it_appends_to_the_callers_list_in_place(self, mock_ctrl, guard):
        """serve passes session.history straight in, so a turn abandoned
        mid-flight still leaves its completed rounds where the server can save
        them. The list object must be the same one."""
        messages = [{"role": "user", "content": "earlier"}]
        original = messages
        self._drain([text_response("hi")], messages, mock_ctrl, guard)
        assert messages is original
        assert [m["role"] for m in messages] == ["user", "user", "assistant"]

    def test_closing_the_generator_mid_round_orphans_a_tool_use(self, mock_ctrl, guard):
        """Why webserve runs the turn to completion on its own thread.

        Close the generator between a tool_use and the tool_results message and
        the history keeps an assistant turn whose tool_use block has no matching
        tool_result — which the Messages API rejects on the *next* turn. Nothing
        in `serve` may abandon this generator; a disconnected browser must not.
        """
        scripted = [tool_use_response("get_system_state", {}, call_id="c1"),
                    text_response("done")]
        messages = []
        with patch("microclaw.agent._get_client", return_value=make_mock_client(scripted)):
            gen = run_agent_iter("go", mock_ctrl, guard, messages)
            for event in gen:
                if event["type"] == "tool_result":
                    break
            gen.close()
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[-1]["content"][0].type == "tool_use"   # unanswered

    def test_overload_exhausted_discards_the_turn(self, mock_ctrl, guard, monkeypatch):
        """Including the user message: `run_agent` today returns the history it
        was handed, untouched. This is the one place the generator *removes*
        from a list it does not own."""
        monkeypatch.setattr("microclaw.agent._RETRY_DELAYS", ())
        monkeypatch.setattr("microclaw.agent.time.sleep", lambda s: None)
        client = MagicMock()
        client.messages.stream.side_effect = anthropic._exceptions.OverloadedError(
            "overloaded", response=httpx.Response(529, request=httpx.Request("POST", "/")),
            body=None,
        )
        messages = [{"role": "user", "content": "earlier"}]
        with patch("microclaw.agent._get_client", return_value=client):
            events = list(run_agent_iter("go", mock_ctrl, guard, messages))
        assert events[-1]["type"] == "error"
        assert "retry" in events[-1]["message"].lower()
        assert messages == [{"role": "user", "content": "earlier"}]
        assert api_history_is_valid(messages)

    def test_overload_rollback_keeps_attempted_prompt_in_append_only_audit(
        self, mock_ctrl, guard, monkeypatch
    ):
        from microclaw.conversation import AuditLog, ConversationStore

        monkeypatch.setattr("microclaw.agent._RETRY_DELAYS", ())
        client = MagicMock()
        client.messages.stream.side_effect = anthropic._exceptions.OverloadedError(
            "overloaded", response=httpx.Response(529, request=httpx.Request("POST", "/")),
            body=None,
        )
        messages = []
        store = ConversationStore(AuditLog(None, enabled=False))
        with patch("microclaw.agent._get_client", return_value=client):
            list(run_agent_iter("attempted", mock_ctrl, guard, messages,
                                on_message=store.append))

        assert messages == []
        assert store.audit.records == [{"role": "user", "content": "attempted"}]

    def test_iteration_cap_keeps_the_turn(self, mock_ctrl, guard):
        client = looping_mock_client(tool_use_response("get_system_state", {}))
        messages = []
        with patch("microclaw.agent._get_client", return_value=client):
            events = list(run_agent_iter("go", mock_ctrl, guard, messages, max_iterations=2))
        assert events[-1]["type"] == "error"
        assert "continue" in events[-1]["message"].lower()
        assert len(messages) == 1 + 2 * 2   # user + 2×(assistant, tool_result)

    def test_max_tokens_is_a_named_recoverable_error(self, mock_ctrl, guard):
        response = MagicMock()
        response.stop_reason = "max_tokens"
        response.content = []
        events = self._drain([response], [], mock_ctrl, guard)
        assert events[-1]["type"] == "error"
        assert "cut off" in events[-1]["message"]
        assert "continue" in events[-1]["message"]

    def test_max_tokens_inside_tool_use_is_unwound_for_the_next_prompt(
        self, mock_ctrl, guard
    ):
        response = tool_use_response("get_system_state", {}, call_id="cut-off-call")
        response.stop_reason = "max_tokens"
        messages = []
        events = self._drain([response], messages, mock_ctrl, guard)

        assert events[-1]["type"] == "error"
        assert api_history_is_valid(messages)
        assert messages[-1]["content"] == [{
            "type": "tool_result",
            "tool_use_id": "cut-off-call",
            "is_error": True,
            "content": json.dumps({"error": "The model reply was cut off."}),
        }]

    def test_requests_allow_a_longer_model_reply(self, mock_ctrl, guard):
        client = make_mock_client([text_response("hi")])
        with patch("microclaw.agent._get_client", return_value=client):
            list(run_agent_iter("go", mock_ctrl, guard, []))
        assert MAX_OUTPUT_TOKENS == 8192
        assert client.messages.stream.call_args.kwargs["max_tokens"] == 8192


class TestAPIFailureSurvival:
    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(lambda: status_error(anthropic.RateLimitError, 429), id="429"),
            pytest.param(lambda: status_error(anthropic.InternalServerError, 500), id="500"),
            pytest.param(connection_error, id="connection"),
            pytest.param(lambda: connection_error(anthropic.APITimeoutError), id="timeout"),
        ],
    )
    def test_retryable_failure_retries_then_succeeds(
        self, failure, mock_ctrl, guard, monkeypatch
    ):
        monkeypatch.setattr("microclaw.agent._RETRY_DELAYS", (0.01,))
        monkeypatch.setattr("microclaw.agent.time.sleep", lambda seconds: None)
        client = MagicMock()
        client.messages.stream.side_effect = [failure(), FakeStream(text_response("done"))]
        messages = []

        with patch("microclaw.agent._get_client", return_value=client):
            events = list(run_agent_iter("go", mock_ctrl, guard, messages))

        assert client.messages.stream.call_count == 2
        assert [event["type"] for event in events].count("retry") == 1
        assert events[-1] == {"type": "done", "reply": "done"}
        assert api_history_is_valid(messages)

    def test_retry_after_is_honoured(self, mock_ctrl, guard, monkeypatch):
        monkeypatch.setattr("microclaw.agent._RETRY_DELAYS", (5,))
        sleeps = []
        monkeypatch.setattr("microclaw.agent.time.sleep", sleeps.append)
        response = httpx.Response(
            429,
            headers={"retry-after": "17"},
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
        )
        failure = anthropic.RateLimitError("slow down", response=response, body=None)
        client = MagicMock()
        client.messages.stream.side_effect = [failure, FakeStream(text_response("done"))]

        with patch("microclaw.agent._get_client", return_value=client):
            events = list(run_agent_iter("go", mock_ctrl, guard, []))

        assert sleeps == [17.0]
        retry = next(event for event in events if event["type"] == "retry")
        assert retry["delay"] == 17.0
        assert client.messages.stream.call_count == 2

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(lambda: status_error(anthropic.RateLimitError, 429), id="429"),
            pytest.param(lambda: status_error(anthropic.InternalServerError, 500), id="500"),
            pytest.param(lambda: status_error(anthropic._exceptions.OverloadedError, 529), id="529"),
            pytest.param(connection_error, id="connection"),
            pytest.param(lambda: connection_error(anthropic.APITimeoutError), id="timeout"),
            pytest.param(lambda: status_error(anthropic.AuthenticationError, 401), id="401"),
            pytest.param(lambda: status_error(anthropic.BadRequestError, 400), id="400"),
        ],
    )
    def test_every_terminal_api_failure_rolls_back_to_valid_history(
        self, failure, mock_ctrl, guard, monkeypatch
    ):
        monkeypatch.setattr("microclaw.agent._RETRY_DELAYS", ())
        client = MagicMock()
        client.messages.stream.side_effect = failure()
        messages = [{"role": "user", "content": "completed earlier turn"}]

        with patch("microclaw.agent._get_client", return_value=client):
            try:
                events = list(run_agent_iter("attempted", mock_ctrl, guard, messages))
            except (anthropic.AuthenticationError, anthropic.BadRequestError):
                events = []

        assert client.messages.stream.call_count == 1
        assert messages == [{"role": "user", "content": "completed earlier turn"}]
        assert api_history_is_valid(messages)
        if events:
            assert events[-1]["type"] == "error"

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(lambda: status_error(anthropic.AuthenticationError, 401), id="401"),
            pytest.param(lambda: status_error(anthropic.BadRequestError, 400), id="400"),
        ],
    )
    def test_auth_and_bad_request_fail_immediately_and_say_why(
        self, failure, mock_ctrl, guard, monkeypatch
    ):
        monkeypatch.setattr("microclaw.agent._RETRY_DELAYS", (5, 15, 30))
        client = MagicMock()
        error = failure()
        client.messages.stream.side_effect = error

        with patch("microclaw.agent._get_client", return_value=client), pytest.raises(
            type(error), match="HTTP"
        ):
            list(run_agent_iter("go", mock_ctrl, guard, []))

        assert client.messages.stream.call_count == 1


def multi_tool_response(names, call_ids):
    """One assistant turn requesting several tools in parallel."""
    blocks = []
    for name, cid in zip(names, call_ids):
        b = MagicMock()
        b.type, b.name, b.input, b.id = "tool_use", name, {}, cid
        blocks.append(b)
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = blocks
    return response


class TestCancellation:
    """v4a Stop: cooperative, at round and tool boundaries only (design/16 §5)."""

    def test_cancel_before_the_first_round_makes_no_api_call(self, mock_ctrl, guard):
        cancel = threading.Event()
        cancel.set()
        client = make_mock_client([text_response("never sent")])
        messages = []
        with patch("microclaw.agent._get_client", return_value=client):
            events = list(run_agent_iter("go", mock_ctrl, guard, messages, cancel=cancel))
        assert [e["type"] for e in events] == ["cancelled"]
        assert client.messages.stream.call_count == 0

    def test_every_tool_use_gets_a_tool_result_when_stopped_mid_batch(self, mock_ctrl, guard):
        """The invariant that keeps the NEXT turn from 400ing.

        The model asked for three tools; the operator stopped after the first.
        The Messages API requires the following user message to answer every
        tool_use block in the assistant turn — so the two unrun ones get an
        is_error result rather than being dropped.
        """
        cancel = threading.Event()
        ids = ["c1", "c2", "c3"]
        scripted = [multi_tool_response(["get_system_state"] * 3, ids)]
        messages = []
        events = []
        with patch("microclaw.agent._get_client", return_value=make_mock_client(scripted)):
            for event in run_agent_iter("go", mock_ctrl, guard, messages, cancel=cancel):
                events.append(event)
                if event["type"] == "tool_result":
                    cancel.set()          # stop after the first tool returns

        assert events[-1]["type"] == "cancelled"

        asst, results = messages[-2], messages[-1]
        assert asst["role"] == "assistant" and results["role"] == "user"
        assert {b.id for b in asst["content"]} == set(ids)
        assert {b["tool_use_id"] for b in results["content"]} == set(ids)

        errored = [b for b in results["content"] if b.get("is_error")]
        assert {b["tool_use_id"] for b in errored} == {"c2", "c3"}
        for b in errored:
            assert "Cancelled by the operator" in b["content"]

    def test_the_first_tool_still_ran(self, mock_ctrl, guard):
        """Stop waits for the running tool; it does not unwind what it did."""
        cancel = threading.Event()
        scripted = [multi_tool_response(["get_system_state"] * 2, ["c1", "c2"])]
        messages = []
        with patch("microclaw.agent._get_client", return_value=make_mock_client(scripted)):
            for event in run_agent_iter("go", mock_ctrl, guard, messages, cancel=cancel):
                if event["type"] == "tool_result":
                    cancel.set()
        first = messages[-1]["content"][0]
        assert not first.get("is_error")
        assert "Cancelled" not in first["content"]

    def test_cancel_between_rounds_ends_the_turn_cleanly(self, mock_ctrl, guard):
        cancel = threading.Event()
        scripted = [tool_use_response("get_system_state", {}, call_id="c1"),
                    text_response("never reached")]
        messages = []
        events = []
        with patch("microclaw.agent._get_client", return_value=make_mock_client(scripted)):
            for event in run_agent_iter("go", mock_ctrl, guard, messages, cancel=cancel):
                events.append(event)
                if event["type"] == "tool_result":
                    cancel.set()   # set after round 0 completed its batch
        assert events[-1]["type"] == "cancelled"
        # round 0 completed in full: nothing to unwind, no synthesized errors
        assert [m["role"] for m in messages] == ["user", "assistant", "user"]
        assert not any(b.get("is_error") for b in messages[-1]["content"])

    def test_a_turn_with_no_cancel_event_is_unaffected(self, mock_ctrl, guard):
        events = list(_drain_iter([text_response("hi")], [], mock_ctrl, guard))
        assert [e["type"] for e in events] == ["round_start", "text_delta", "done"]


def _drain_iter(scripted, messages, ctrl, guard, **kw):
    with patch("microclaw.agent._get_client", return_value=make_mock_client(scripted)):
        return list(run_agent_iter("go", ctrl, guard, messages, **kw))


class TestBadModel:
    def test_a_rejected_model_is_an_error_event_not_a_500(self, mock_ctrl, guard):
        """A free-text picker can hold an id the API doesn't know."""
        client = MagicMock()
        client.messages.stream.side_effect = anthropic.NotFoundError(
            "model: nope", response=httpx.Response(404, request=httpx.Request("POST", "/")),
            body=None,
        )
        messages = [{"role": "user", "content": "earlier"}]
        with patch("microclaw.agent._get_client", return_value=client):
            events = list(run_agent_iter("go", mock_ctrl, guard, messages, model="nope"))
        assert events[-1]["type"] == "error"
        assert "nope" in events[-1]["message"]
        # nothing ran, so the prompt is not left sitting unanswered in history
        assert messages == [{"role": "user", "content": "earlier"}]

    def test_rejected_model_rollback_keeps_attempted_prompt_in_audit(
        self, mock_ctrl, guard
    ):
        from microclaw.conversation import AuditLog, ConversationStore

        client = MagicMock()
        client.messages.stream.side_effect = anthropic.NotFoundError(
            "model: nope", response=httpx.Response(404, request=httpx.Request("POST", "/")),
            body=None,
        )
        messages = []
        store = ConversationStore(AuditLog(None, enabled=False))
        with patch("microclaw.agent._get_client", return_value=client):
            list(run_agent_iter("attempted", mock_ctrl, guard, messages, model="nope",
                                on_message=store.append))

        assert messages == []
        assert store.audit.records == [{"role": "user", "content": "attempted"}]


class TestKnownModels:
    def test_ids_are_fetched_once_and_cached(self, monkeypatch):
        import microclaw.agent as agent
        monkeypatch.setattr(agent, "_known_models", None)
        client = MagicMock()
        client.models.list.return_value.data = [
            types.SimpleNamespace(id="claude-opus-4-8"),
            types.SimpleNamespace(id="claude-haiku-4-5-20251001"),
        ]
        with patch("microclaw.agent._get_client", return_value=client):
            assert agent.known_models() == ["claude-opus-4-8", "claude-haiku-4-5-20251001"]
            agent.known_models()
        assert client.models.list.call_count == 1

    def test_an_unreachable_api_means_no_suggestions_not_an_error(self, monkeypatch):
        import microclaw.agent as agent
        monkeypatch.setattr(agent, "_known_models", None)
        with patch("microclaw.agent._get_client", side_effect=RuntimeError("no key")):
            assert agent.known_models() == []

    def test_setting_a_key_drops_the_cache(self, monkeypatch):
        import microclaw.agent as agent
        monkeypatch.setattr(agent, "_known_models", ["stale"])
        monkeypatch.setenv("ANTHROPIC_API_KEY", "old")
        agent.set_api_key("sk-ant-new")
        assert agent._known_models is None


class TestLazyClient:
    def test_import_does_not_construct_client(self):
        import importlib
        import microclaw.agent as agent
        with patch("anthropic.Anthropic", side_effect=AssertionError("constructed on import")):
            importlib.reload(agent)  # reload must not build a client
        # sanity: module-global stays None until _get_client is called
        assert agent._client is None
