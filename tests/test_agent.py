"""
Prompt-level tests: verify that a given user input produces the expected
sequence of tool calls. Uses a mock Anthropic client so no real API key needed.
The mock pre-scripts the sequence of Claude responses (tool_use blocks).
"""
import json
from pathlib import Path
import threading
import types
import anthropic
import httpx
import pytest
from unittest.mock import MagicMock, patch
from microclaw.agent import (
    MAX_OUTPUT_TOKENS,
    MAX_RETRY_AFTER_SECONDS,
    SYSTEM_PROMPT,
    run_agent,
    run_agent_iter,
    _system_blocks,
)
from microclaw.safety import SafetyConstraints, SafetyGuard
from microclaw.skills import load_skill_text


class TestRigInterviewSystemBlock:
    @pytest.fixture(autouse=True)
    def _knowledge_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "microclaw.knowledge_manager.KNOWLEDGE_PATH", tmp_path / "knowledge.yaml"
        )

    def test_interview_names_only_remaining_topics_and_is_not_cached(self):
        from microclaw.knowledge_manager import RIG_TOPICS, save_entry
        for topic in RIG_TOPICS[:-1]:
            save_entry("rig", topic, {"description": "answered"})
        blocks = _system_blocks()
        interview = blocks[-1]
        assert "cache_control" not in interview
        assert RIG_TOPICS[-1] in interview["text"]
        for topic in RIG_TOPICS[:-1]:
            assert topic not in interview["text"]
        assert "Never block a task" in interview["text"]
        assert "Never re-ask a stored topic" in interview["text"]
        # Whitespace-normalized: the clause is the contract, its line wrap is not.
        flat = " ".join(interview["text"].split())
        assert "if this rig has an EMU plugin" in flat
        assert all("cache_control" in block for block in blocks[:-1])

    def test_interview_says_when_to_ask(self):
        """The demo gate's round-1 FAIL, pinned.

        The first shipped text described *how* to interview and never said
        *when*, so on the demo machine the block was in the prompt, the profile
        had five open topics, and the agent opened two sessions without asking
        anything. Its most forceful sentence was the negative one — never block
        a task — which is the only clause a request for work matches.
        """
        blocks = _system_blocks()
        flat = " ".join(blocks[-1]["text"].split())
        assert "first reply of this session" in flat
        assert "Interview the operator" in flat

    def test_interview_is_omitted_when_profile_is_complete(self):
        from microclaw.knowledge_manager import RIG_TOPICS, save_entry
        for topic in RIG_TOPICS:
            save_entry("rig", topic, {"description": "answered"})
        blocks = _system_blocks()
        assert len(blocks) == 2
        assert all("cache_control" in block for block in blocks)
        assert "Complete this rig's profile" not in "\n".join(
            block["text"] for block in blocks
        )


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
    response.model = "served-model"
    response.usage = sdk_usage()
    response.stop_reason = "tool_use"
    response.content = [block]
    return response


def text_response(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.model = "served-model"
    response.usage = sdk_usage()
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
        # The old wording ("...and by itself is neither") was an anaphor pointing
        # back across an intervening sentence to two triggers, one of which was
        # later deleted; it survives here stated positively instead.
        assert (
            "Closing or restarting MicroClaw is software-only: it is not physical "
            "interaction and not a reason to change anything"
        ) in SYSTEM_PROMPT
        assert "offer to restore it afterward" in SYSTEM_PROMPT
        # Operator ruling: microclaw writes nothing to MM on any exit path. A
        # prompt that tells the agent to shutter on close reverses that, and it
        # is unactionable anyway — microclaw is usually killed without notice.
        assert "MicroClaw writes nothing to the microscope on the way out" in SYSTEM_PROMPT
        assert "Shutter the camera when MicroClaw closes" not in SYSTEM_PROMPT

    def test_eye_safety_still_triggers_on_the_operators_word(self):
        # The rule must not require verifying that an interaction is real: the
        # operator's word is the only evidence available, and the failure mode
        # here is NOT shuttering. Phrase-matching ("I will now ...") is what
        # misfired and is gone; taking their word for it is not.
        assert "Before physical interaction with the microscope" in SYSTEM_PROMPT
        assert "Their word that they are about to touch it is enough on its own" in SYSTEM_PROMPT
        # What must stop is whatever is putting light on the SAMPLE. On a
        # camera-triggered rig that is the running camera, not the laser enable.
        assert "stop what is putting light on the sample" in SYSTEM_PROMPT

    def test_laser_entry_state_is_named_explicitly(self):
        rule = next(
            line for line in SYSTEM_PROMPT.splitlines()
            if "At the end of a task involving lasers" in line
        )
        assert "what MicroClaw turned on, MicroClaw turns off" in rule
        assert "what it found on, it leaves on" in rule


class TestLiveDoseAndTiffPrompt:
    def test_live_view_is_not_started_just_for_agent_visibility(self):
        assert "so the user can see what you are doing" not in SYSTEM_PROMPT
        assert "Live view is for the microscopist's eyes, not yours" in SYSTEM_PROMPT
        assert "(a focus sweep, a navigation)" not in SYSTEM_PROMPT
        assert "worth watching (e.g. searching for a cell)" in SYSTEM_PROMPT
        assert "camera_triggers_lasers: true" in SYSTEM_PROMPT
        assert "Never leave live view running after an acquisition finishes" in SYSTEM_PROMPT

    def test_datasets_are_opened_not_converted(self):
        """The export_dataset_as_tiff guidance was removed DELIBERATELY.

        NDTiff writes plain TIFFs that any TIFF reader opens, so offering a
        conversion implies a problem that does not exist. The tool still exists
        in tools.py for the cases a user names; the prompt no longer volunteers
        it. Do not restore the ThunderSTORM/SMAP/Picasso/DECODE bullet from git
        history — what replaced it is the rule asserted here.
        """
        assert "open the file — do not convert " in SYSTEM_PROMPT
        assert "channels as planes rather than reconstructing named channel axes" in SYSTEM_PROMPT
        assert "export_dataset_as_tiff" not in SYSTEM_PROMPT


class TestLaserCleanupPrompt:
    """MicroClaw cleans up after itself, and only after itself.

    The rule this replaced was "shutter the excitation whenever the camera is
    not running", which is unconditional and therefore reaches the operator's
    own laser — an operator aligning with their 640 on and the camera off
    satisfies it, which is the design/37 F3 incident verbatim. The replacement
    splits on WHO turned it on, and carries two exceptions the old rule had no
    room for.
    """

    def test_a_laser_you_enabled_is_yours_to_turn_off(self):
        assert "A laser you enabled is yours to turn off" in SYSTEM_PROMPT
        assert "Excitation the operator had on when you arrived is theirs" in SYSTEM_PROMPT
        # The unconditional form must not come back: it contradicts both the
        # yours/theirs split here and "never restore, tidy, or 'make safe'".
        assert "Shutter the excitation whenever the camera is not running" not in SYSTEM_PROMPT

    def test_camera_triggered_rigs_do_not_cycle_the_laser(self):
        # On a camera-triggered rig the camera gates emission, so a laser left
        # on between exposures is not reaching the sample and switching it per
        # snap buys nothing. This must stay scoped to the profile key — on every
        # other rig the laser really is on and really must be turned off.
        rule = next(
            line for line in SYSTEM_PROMPT.splitlines()
            if "A laser you enabled is yours to turn off" in line
        )
        assert "`camera_triggers_lasers: true`" in rule
        assert "a laser left on between exposures is not incident on the sample" in rule
        assert "rather than cycling it yourself" in rule

    def test_a_snap_sequence_keeps_the_laser_on_until_it_ends(self):
        # Laser startup dominates a step-and-snap loop, so the laser stays on
        # across the sequence...
        rule = next(
            line for line in SYSTEM_PROMPT.splitlines()
            if "A laser you enabled is yours to turn off" in line
        )
        assert "a focus sweep, stepping the stage and calling `snap_and_analyze`" in rule
        assert "leave it on for the whole sequence" in rule
        assert "laser startup is slow enough to dominate the loop" in rule
        # ...and the licence must never outlive the sequence that earned it.
        assert "turn it off once the sequence is finished" in rule


class TestVerifyBeforeAssertingPrompt:
    def test_hardware_claims_are_re_verified_not_recalled(self):
        assert (
            "Before making any assertions about hardware states, call "
            "get_system_state to verify"
        ) in SYSTEM_PROMPT
        # The two cases where memory is least trustworthy are named, because a
        # bare "verify" rule gets satisfied by a reading from twenty turns ago.
        assert "been a long time since the last interaction" in SYSTEM_PROMPT
        assert "says something that disagrees with your memory" in SYSTEM_PROMPT

    def test_a_blank_frame_is_not_automatically_the_laser(self):
        # Guards the reflex of re-enabling illumination as the first response to
        # a dark frame, when the cause is at least as often optical or postional.
        assert "a blank or empty frame may not result from the laser" in SYSTEM_PROMPT
        assert "improperly set filter" in SYSTEM_PROMPT
        assert "wrong part of the sample" in SYSTEM_PROMPT
        assert "objective out of focus" in SYSTEM_PROMPT


class TestFocusLockPromptAndNikonPfsSkill:
    """Rig facts, so they are pinned as text rather than trusted to survive.

    The whole section rendered as a single line until the literals were fixed,
    which is exactly the kind of loss no other test could see.
    """

    def test_a_hardware_focus_lock_is_offered_before_an_image_sweep(self):
        """Nikon 2026-08-23: asked to find focus, it proposed an image sweep.

        The operator had to ask "can you use the Nikon PFS system on here? Why
        did you not propose using this?" -- the PFS guidance sat in the
        rig-specific section while the generic Autofocus section said only
        "run_autofocus is for interactive focus requests". get_focus_lock_state
        names the configured autofocus device on ANY rig now, so the check is
        generic and belongs where the model reads it.
        """
        assert "BEFORE proposing an image-based sweep" in SYSTEM_PROMPT
        assert "get_focus_lock_state" in SYSTEM_PROMPT
        assert "Do not wait to be asked" in SYSTEM_PROMPT

    def test_focus_lock_ordering_invariant_is_vendor_neutral(self):
        sentence = next(
            line for line in SYSTEM_PROMPT.splitlines()
            if "before any operation that engages or adjusts a hardware focus lock" in line.lower()
        )
        assert "get_focus_lock_state" in sentence
        assert all(word not in sentence for word in ("Nikon", "PFS", "TIPFS"))

    def test_focus_lock_is_named_as_a_dedicated_tool_operation(self):
        assert "(stage, channel, exposure, focus lock)" in SYSTEM_PROMPT

    def test_nikon_procedure_is_maintained_in_the_skill(self):
        assert "Do BOTH of these every time you engage the lock" in load_skill_text("nikon-pfs")

    def test_the_lock_validation_steps_are_steps_not_caveats(self):
        # Same session: it flagged the "locked too high" risk in its plan and
        # then did not carry out either check until asked.
        skill = load_skill_text("nikon-pfs")
        assert "Do BOTH of these every time you engage the " in skill
        assert "not as caveats you mention and skip" in skill

    def test_pfs_status_reads_regardless_of_whether_the_lock_is_engaged(self):
        skill = load_skill_text("nikon-pfs")
        assert "TIPFSStatus-Status tells you if you are focusing" in skill
        assert "regardless of whether or not the PFS is on" in skill
        assert "use run_autofocus with its property probe" in skill
        assert "Just move the Z stage and check this property" not in skill

    def test_a_lock_found_too_high_is_diagnosed_by_moving_xy(self):
        # A PFS can lock on a coverslip the objective has pushed up at an angle;
        # the tell is that the lock drops after a small lateral move.
        skill = load_skill_text("nikon-pfs")
        assert "pushed the coverslip up at an angle" in skill
        assert "jog the stage a little bit" in skill
        assert "see if the PFS stays on" in skill

    def test_offset_range_is_given_per_immersion_medium(self):
        skill = load_skill_text("nikon-pfs")
        assert "PFS offset range is around 10 micrometers for oil immersion" in skill
        assert "20 micrometers for water immersion" in skill
        assert "100 micrometers or more for dry" in skill
        assert "decrease with increasing numerical aperture" in skill


class TestKnowledgeBaseUpkeepPrompt:
    def test_a_correction_is_offered_to_the_knowledge_base(self):
        assert (
            "If a user corrects a mistake, offer to remember the behavior and the "
            "fix in the knowledge base"
        ) in SYSTEM_PROMPT

    def test_stale_stored_state_is_updated_not_just_noticed(self):
        assert "differs significantly in state from what is in the knowledge base" in SYSTEM_PROMPT
        assert "immediately update the knowledge base to this new state" in SYSTEM_PROMPT

    def test_a_new_strategy_is_offered_to_the_knowledge_base(self):
        assert (
            "If you perform a new strategy during an imaging session, offer to save "
            "it to the knowledge base"
        ) in SYSTEM_PROMPT
        assert "Save early and often" in SYSTEM_PROMPT


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
        assert last_block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

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
        assert last_block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
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

    def test_scripted_load_skill_dispatch_returns_smlm_body(self, mock_ctrl, guard):
        # This establishes dispatch and loop integration, not routing. The
        # model's choice to call load_skill is the routing behaviour, and a
        # scripted mock necessarily supplies that choice. Live-model routing
        # evidence is gathered by design/61-skill-routing-spike.py instead.
        payload = json.loads(
            (Path(__file__).parent / "fixtures" / "smlm_skill_dispatch.json").read_text(
                encoding="utf-8"
            )
        )
        tool_round, text_round = payload["assistant_rounds"]
        scripted = [
            tool_use_response(
                tool_round["name"], tool_round["input"], call_id=tool_round["id"]
            ),
            text_response(text_round["text"]),
        ]
        with patch("microclaw.agent._get_client", return_value=make_mock_client(scripted)):
            events = list(run_agent_iter(payload["operator"], mock_ctrl, guard, []))

        use = next(event for event in events if event["type"] == "tool_use")
        result = next(event for event in events if event["type"] == "tool_result")
        assert use["name"] == "load_skill"
        assert use["input"] == {"name": "smlm"}
        assert "# Single-Molecule Localization Microscopy" in result["content"]
        assert events[-1]["type"] == "done"

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

    def test_tool_dispatch_injects_the_live_append_only_record(self, mock_ctrl, guard):
        messages = []
        seen = {}

        def execute(*args, records=None, **kwargs):
            seen["records"] = records
            return json.dumps({"status": "ok"})

        scripted = [tool_use_response("get_xy_position", {}), text_response("done")]
        with patch("microclaw.agent.execute_tool", side_effect=execute):
            self._drain(scripted, messages, mock_ctrl, guard)

        assert seen["records"] is messages
        assert messages[1]["content"][0].name == "get_xy_position"

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
        response.model = "served-model"
        response.usage = sdk_usage()
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

    def test_unexpected_stop_reason_is_an_error_event(self, mock_ctrl, guard):
        response = tool_use_response("get_system_state", {}, call_id="refused-call")
        response.stop_reason = "refusal"
        messages = []

        events = self._drain([response], messages, mock_ctrl, guard)

        assert events[-1] == {
            "type": "error",
            "message": "[Unexpected stop reason: refusal]",
        }
        assert api_history_is_valid(messages)
        assert json.loads(messages[-1]["content"][0]["content"]) == {
            "error": "The model stopped with reason: refusal."
        }


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

    def test_retry_after_past_cap_fails_without_sleeping(
        self, mock_ctrl, guard, monkeypatch
    ):
        sleeps = []
        monkeypatch.setattr("microclaw.agent.time.sleep", sleeps.append)
        response = httpx.Response(
            429,
            headers={"retry-after": "3600"},
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
        )
        failure = anthropic.RateLimitError("slow down", response=response, body=None)
        client = MagicMock()
        client.messages.stream.side_effect = failure
        messages = []

        with patch("microclaw.agent._get_client", return_value=client):
            events = list(run_agent_iter("go", mock_ctrl, guard, messages))

        assert MAX_RETRY_AFTER_SECONDS == 60
        assert sleeps == []
        assert client.messages.stream.call_count == 1
        assert events[-1]["type"] == "error"
        assert "3600 seconds" in events[-1]["message"]
        assert "too long" in events[-1]["message"]
        assert api_history_is_valid(messages)

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
    response.model = "served-model"
    response.usage = sdk_usage()
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


class TestBuiltInOfflineAdaptersAreDiscoverable:
    """The M5 gate for block 43e found the built-ins shipped but unreachable.

    Asked three times whether positions belonged to the same cell, the agent
    guessed the adapter name 'frame_stats', read a refusal that named both real
    built-ins, and then abandoned offline analysis for a mosaic it looked at by
    eye. It never called connected_components. The names existed only inside an
    error message, and the tool description said "reviewed, hash-pinned", which
    describes the saved path alone.

    So both texts must name every built-in. Deriving the expectation from
    BUILTIN_ADAPTERS rather than listing it here means a third built-in fails
    this test until it is announced somewhere the model reads before erroring.
    """

    def _schema(self):
        from microclaw.tools_schema import TOOLS
        return next(t for t in TOOLS if t["name"] == "run_analysis_on_saved_dataset")

    def test_every_builtin_is_named_in_the_tool_description(self):
        from microclaw.completed_dataset import BUILTIN_ADAPTERS
        description = self._schema()["description"]
        for name in BUILTIN_ADAPTERS:
            assert name in description, f"{name} is invisible until the model errors"

    def test_every_builtin_is_named_in_the_system_prompt(self):
        from microclaw.completed_dataset import BUILTIN_ADAPTERS
        for name in BUILTIN_ADAPTERS:
            assert name in SYSTEM_PROMPT

    def test_the_description_does_not_gate_builtins_behind_review(self):
        # The old text was "Run one reviewed, hash-pinned offline adapter",
        # which is true of saved adapters and false of these two.
        description = self._schema()["description"]
        assert "no review" in description or "need no review" in description


def test_unknown_status_strings_are_not_a_reason_to_hand_step_z():
    """Nikon 56ab gate, 2026-08-23: it offered PFS, then hand-walked Z anyway.

    Its own account: "I told myself I needed to discover the in-range string
    before the probe could stop on it." That is the loop design/56 exists to
    delete, and it is unnecessary -- a wrong guess refuses with every value the
    sweep observed, which is exactly how the operator's own mistyped
    `Within range of focus` call recovered the right spelling later in the same
    session. Nothing had told the model that.
    """
    assert "is NOT a reason to step Z" in SYSTEM_PROMPT
    assert "lists every value the sweep actually observed" in SYSTEM_PROMPT


def test_the_lock_state_payload_is_enough_to_build_the_probe():
    """Dragonfly 2026-08-23: it had the device and still proposed an image sweep.

    Naming the device was never enough — the probe needs a property, and finding
    one cost several exploratory calls. get_focus_lock_state now returns the
    readable properties with their values, so the prompt points at that payload
    instead of at an exploration.
    """
    assert "readable status" in SYSTEM_PROMPT
    assert "do " in SYSTEM_PROMPT and "not go exploring with list_device_properties" in SYSTEM_PROMPT
    assert "a bitfield or a number is not it" in SYSTEM_PROMPT


def test_deliverable_routes_are_an_acquisition_planning_rule():
    """81 F4: ordinary quantities must trigger routing outside the code-writing ladder."""
    sections = SYSTEM_PROMPT.split("\n\n")
    planning = next(section for section in sections if "list every deliverable" in section)
    writing = next(section for section in sections if section.startswith("Writing code"))
    assert planning is not writing and planning.startswith("Deliverables and routes"), (
        "Deliverable routing must be its own top-level planning section, outside Writing code"
    )
    assert SYSTEM_PROMPT.index("Guidelines:") < SYSTEM_PROMPT.index(planning) < SYSTEM_PROMPT.index("Reporting —")
    assert "deliverable" not in writing
    for anchor in ("first exposure", "every deliverable", "tool or adapter", "count the beads",
                   "how many cells", "which fields have X", "list_hooks()",
                   "run_analysis_on_saved_dataset", "contract and dependencies",
                   "same message as the acquisition plan", "no named route",
                   "live observer's accumulated verdict", "retrospectively annotated",
                   "Do not re-expose"):
        assert anchor in planning
    assert "script merely written to disk is not executed analysis" in writing


def test_connected_components_prompt_names_original_frame_route():
    """81 F10: the prompt must not hide the per-frame capability shipped in 81b."""
    bullet = next(line for line in SYSTEM_PROMPT.splitlines() if line.startswith("- Standard measurements"))
    assert "'connected_components' (input_kind='stage_coordinate_mosaic')" not in bullet, (
        "Stale mosaic-only connected_components parenthetical hides the original-frame route"
    )
    components, statistics = bullet.split("'frame_statistics'", 1)
    assert "connected_components" in components
    assert "input_kind='frames'" in components, "connected_components needs its own frames route"
    assert "each original saved frame separately" in components
    assert "per-position" in components
    assert '"count what is in each field"' in components
    assert "reported as a component count" in components
    assert "answer rests on a measurement rather than on your reading of a picture" in components
    assert "opening the mosaic so the user can see it" in bullet
    assert "as well, not instead" in bullet
    assert "never present that plain mosaic as evidence of what was counted" in bullet
    assert "input_kind='stage_coordinate_mosaic'" in components
    assert "resampled" in components and "overwrite" in components and "not a per-field count" in components
    assert "input_kind='frames'" in statistics


def test_count_disclosure_names_resolve_against_the_adapter_result():
    """81 D5: prompt field names must survive adapter changes on independently green branches."""
    import re
    import numpy as np
    from microclaw.calibration import StageCameraAffine
    from microclaw.completed_dataset import ConnectedComponents

    adapter = ConnectedComponents(min_snr=3, min_snr_source="test")
    adapter.affine = StageCameraAffine(1, 0, 0, 1, "objective", 1, 1)
    result = adapter._analyze_source_frame(np.zeros((16, 16)), {}, None)["result"]
    disclosure = next(line for line in SYSTEM_PROMPT.splitlines() if "relay the adapter's disclosure" in line)
    frame_clause, manifest_clause = disclosure.split("and follow its", 1)
    fields = re.findall(r"`([a-z_]+)`", frame_clause)
    assert fields, "The reporting rule must name the frame disclosure fields"
    assert set(fields) <= result.keys(), f"Prompt names absent frame disclosure fields: {set(fields) - result.keys()}"
    reference, target = re.findall(r"`([a-z_]+)`", manifest_clause)
    assert result[reference] == target
    assert getattr(adapter, target), "The frame's semantics reference must resolve to adapter disclosure"
    # Every emitted review/statistics/refusal disclosure must also be relayed.
    emitted = {key for key in result if key.endswith(("_distribution", "_notes", "_statistics", "_refusal"))}
    assert emitted <= set(fields)
    assert "`stage_geometry_refusal` when it is not null" in frame_clause


def test_numeric_reporting_does_not_invent_detection_evidence_or_reject_empty_fields():
    """81 D5: unseen numbers are not validated; absent rendering and weak signal cannot become gates."""
    import re

    reporting = SYSTEM_PROMPT.split("Reporting —", 1)[1].split("\n\n", 1)[0]
    for anchor in ("per-field numbers", "not validation", "early confirmation",
                   "skip a report", "component counts, never bead, cell or object counts",
                   "including for the built-in", "brighter spot", "segmentation cannot separate",
                   "review information, not a rejection rule", "valid zero count",
                   "no built-in detection-evidence image exists", "plain mosaic or thumbnail",
                   "custom adapter", "open_artifact"):
        assert anchor in reporting
    # 81b removed the renderer; R121 makes even the raw sparse-field thumbnail
    # unreadable. Guard likely replacement wording, not just the deleted sentence.
    for sentence in re.split(r"(?<=[.!?])\s+|\n", SYSTEM_PROMPT.lower()):
        if re.search(r"\b(show|render|display|open|present|draw)\b", sentence):
            assert not re.search(
                r"detected[- ]object (?:overlay|annotation|evidence)|component outlines|"
                r"numbered detections|detection[- ]evidence (?:image|artifact)|"
                r"(?:detection|component) (?:overlay|annotation)", sentence
            ) or re.search(r"\b(no|not|never|unavailable)\b", sentence), sentence
        if re.search(r"\b(reject|invalidate|refuse|discard)\b", sentence) and "count" in sentence:
            assert not re.search(r"intensity|low.signal|focus.metric", sentence) or re.search(
                r"\b(no|not|never|neither)\b", sentence
            ), sentence


def test_hook_save_prompt_distinguishes_model_duty_from_conditional_code_gate():
    """81 F7: a lint-clean save has no blocking code prompt to substitute for source review."""
    import re

    assert not re.search(r"Confirmation for save_knowledge and hook saves is .*enforced in code", SYSTEM_PROMPT)
    rule = next(line for line in SYSTEM_PROMPT.splitlines() if line.startswith("- Never save or run a hook"))
    assert "full source" in rule and "explicit confirmation" in rule and "every hook save" in rule
    assert "only when advisory lint has findings" in rule
    assert "lint-clean hook saves without that prompt" in rule
    assert "save_knowledge always asks for confirmation in code before writing" in rule


def test_offers_require_tools_and_cleanup_stays_with_the_operator():
    """81 D6(b): missing deletion capability must not become an offer to add it."""
    reporting = SYSTEM_PROMPT.split("Reporting —", 1)[1].split("\n\n", 1)[0]
    for anchor in ("Offer only what a tool can do", "name the tool", "do not offer it",
                   "remove, overwrite or move", "inspect_artifacts", "removal is theirs",
                   "deletes data and will not get one", "do not propose adding one"):
        assert anchor in reporting


def sdk_usage():
    from anthropic.types import Usage, CacheCreation
    return Usage(input_tokens=12, output_tokens=412,
                 cache_read_input_tokens=160000, cache_creation_input_tokens=90,
                 cache_creation=CacheCreation(ephemeral_5m_input_tokens=20,
                                              ephemeral_1h_input_tokens=70))


@pytest.mark.parametrize("stop", ["end_turn", "tool_use", "max_tokens"])
def test_usage_each_response(stop, mock_ctrl, guard):
    from datetime import datetime
    response = text_response("done")
    response.stop_reason = stop
    client = make_mock_client([response, text_response("next")])
    records = []
    with patch("microclaw.agent._get_client", return_value=client):
        events = list(run_agent_iter("go", mock_ctrl, guard, [], usage_sink=records.append))
    assert len(records) == client.messages.stream.call_count == (2 if stop == "tool_use" else 1)
    assert [r["iteration"] for r in records] == list(range(len(records)))
    record = dict(records[0])
    assert datetime.fromisoformat(record.pop("timestamp")).utcoffset().total_seconds() == 0
    assert record == dict(model="served-model", iteration=0, stop_reason=stop,
                          input_tokens=12, output_tokens=412,
                          cache_read_input_tokens=160000, cache_creation_input_tokens=90,
                          cache_creation_5m_input_tokens=20, cache_creation_1h_input_tokens=70)
    assert not any(e["type"] == "usage" for e in events)


@pytest.mark.parametrize("outcome", ["retry", "spent", "bad_model"])
def test_usage_retry_accounting(outcome, mock_ctrl, guard, monkeypatch):
    monkeypatch.setattr("microclaw.agent._RETRY_DELAYS", (0,))
    client = MagicMock()
    failure = connection_error()
    client.messages.stream.side_effect = (
        [failure, FakeStream(text_response("done"))] if outcome == "retry" else
        [status_error(anthropic.NotFoundError, 404)] if outcome == "bad_model" else
        [failure, failure])
    records = []
    with patch("microclaw.agent._get_client", return_value=client):
        events = list(run_agent_iter("go", mock_ctrl, guard, [], usage_sink=records.append))
    assert len(records) == (1 if outcome == "retry" else 0)
    assert events[-1]["type"] == ("done" if outcome == "retry" else "error")


def test_usage_sink_failure_preserves_turn(mock_ctrl, guard, capsys):
    def broken(record):
        raise OSError("disk full")
    with patch("microclaw.agent._get_client", return_value=make_mock_client([text_response("done")])):
        reply, history = run_agent("go", mock_ctrl, guard, usage_sink=broken)
    assert reply == "done"
    assert len(history) == 2
    assert "Could not record usage: disk full" in capsys.readouterr().err


@pytest.mark.parametrize("missing", [True, False])
def test_usage_missing_fields(missing, mock_ctrl, guard):
    response = text_response("done")
    if missing:
        del response.usage
    else:
        from anthropic.types import Usage
        response.usage = Usage(input_tokens=1, output_tokens=2)
    records = []
    with patch("microclaw.agent._get_client", return_value=make_mock_client([response])):
        run_agent("go", mock_ctrl, guard, usage_sink=records.append)
    assert len(records) == 1
    assert records[0]["input_tokens"] == (None if missing else 1)
    for field in ("cache_read_input_tokens", "cache_creation_input_tokens",
                  "cache_creation_5m_input_tokens", "cache_creation_1h_input_tokens"):
        assert records[0][field] is None


@pytest.mark.parametrize("setup", [False, True])
def test_usage_cache_ttl_request(setup, mock_ctrl, guard, monkeypatch):
    monkeypatch.setattr("microclaw.agent.load_knowledge", lambda: {})
    monkeypatch.setattr("microclaw.agent.format_for_prompt", lambda knowledge: "knowledge")
    client = make_mock_client([text_response("done")])
    with patch("microclaw.agent._get_client", return_value=client):
        list(run_agent_iter("go", mock_ctrl, guard, [], setup_mode=setup,
                            **({"tool_schemas": []} if setup else {})))
    request = client.messages.stream.call_args.kwargs
    expected = {"type": "ephemeral", "ttl": "1h"}
    assert [b["cache_control"] for b in request["system"] if "cache_control" in b] == [expected, expected]
    assert request["messages"][-1]["content"][-1]["cache_control"] == expected
    if setup:
        assert "tools" not in request
        assert "cache_control" not in request["system"][-1]
    else:
        assert [t["cache_control"] for t in request["tools"] if "cache_control" in t] == [expected]


@pytest.mark.parametrize("profile", [False, True])
def test_usage_cli_repl_forwards_sink(profile, mock_ctrl, guard, monkeypatch):
    from microclaw import __main__ as cli
    from microclaw.conversation import AuditLog, ConversationStore
    sink = lambda record: None
    calls = []
    def runner(*args, **kwargs):
        calls.append(kwargs)
        return "done", []
    monkeypatch.setattr(cli, "run_agent", runner)
    answers = iter(["go", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    cli._repl(types.SimpleNamespace(profile=profile, model=None), mock_ctrl, guard, [],
              ConversationStore(AuditLog(None)), usage_sink=sink)
    assert calls[0]["usage_sink"] is sink


@pytest.mark.parametrize("save", [False, True])
def test_usage_cli_sidecar(save, mock_ctrl, guard, monkeypatch, tmp_path):
    from microclaw import __main__ as cli
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("microclaw.updates.start_due_check", lambda *a: None)
    monkeypatch.setattr("microclaw.updates.terminal_update_notice", lambda: (None, None))
    monkeypatch.setattr(cli, "load_safety_config_or_exit",
                        lambda path: types.SimpleNamespace(constraints=SafetyConstraints()))
    monkeypatch.setattr(cli.credentials, "load_api_key", lambda: ("test-key", "test"))
    monkeypatch.setattr("microclaw.agent.set_api_key", lambda key: None)
    monkeypatch.setattr(cli, "MicroscopeController", lambda **kw: mock_ctrl)
    monkeypatch.setattr(cli, "validate_live_rig", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "report_declared_illumination_on_exit", lambda *a: None)
    observed = []
    def repl(args, ctrl, guard, history, store, *rest, usage_sink):
        store.last_estimated_tokens = 12345
        store.compaction_count = 3
        usage_sink({"model": "served-model"})
        observed.append(store.audit.path)
    monkeypatch.setattr(cli, "_repl", repl)
    cli.run_session(types.SimpleNamespace(safety_config=None, port=4827, save_history=save))
    path = Path(str(observed[0]).replace("_history.jsonl", "_usage.jsonl"))
    assert path.exists() is save
    if save:
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "model": "served-model", "estimated_tokens": 12345, "compaction_count": 3}
    else:
        assert not list(tmp_path.glob("*_usage.jsonl"))
