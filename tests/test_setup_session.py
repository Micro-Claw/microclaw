import json
import re
import types
from unittest.mock import MagicMock
import yaml
import pytest
from fastapi.testclient import TestClient

from microclaw import agent, setup_tools, tools, webserve
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


def test_setup_tools_never_enter_exportable_registry():
    assert webserve.SETUP_TOOL_NAMES.isdisjoint(tools.TOOL_REGISTRY)


def test_fabricated_hardware_call_is_rejected_before_function_lookup():
    registry = MagicMock()
    registry.__contains__.return_value = False
    result = json.loads(tools.execute_tool(
        "move_stage_xy", {}, object(), None, registry, setup_mode=True,
    ))
    assert "setup mode" in result["error"]
    registry.get.assert_not_called()


def test_setup_turn_sends_only_setup_schemas_and_refuses_fabricated_call(monkeypatch):
    client = MagicMock()
    client.messages.stream.return_value = _Stream()
    monkeypatch.setattr(agent, "_client", client)
    events = list(run_agent_iter(
        "move", object(), None, [], max_iterations=1,
        tool_schemas=webserve.SETUP_TOOL_SCHEMAS,
        tool_registry=webserve.SETUP_TOOL_REGISTRY,
        setup_mode=True,
    ))
    offered = {schema["name"] for schema in client.messages.stream.call_args.kwargs["tools"]}
    assert offered == webserve.SETUP_TOOL_NAMES
    system = "\n".join(block["text"] for block in client.messages.stream.call_args.kwargs["system"])
    assert RIG_INTERVIEW_PROMPT.splitlines()[0] not in system
    result = next(event for event in events if event["type"] == "tool_result")
    assert "setup mode" in result["content"]


def test_a_session_offering_nothing_sends_no_tools_key_at_all(monkeypatch):
    """Whether the Messages API accepts `tools: []` is undocumented and has
    never been tested here, so a session with no tools omits the parameter
    instead of sending an empty array. Setup mode had exactly this shape between
    48b and 48c; the guard stays because the cost of being wrong is a failed
    first turn in front of a novice."""
    client = MagicMock()
    client.messages.stream.return_value = _Stream()
    monkeypatch.setattr(agent, "_client", client)
    list(run_agent_iter(
        "hello", object(), None, [], max_iterations=1,
        tool_schemas=[], tool_registry={}, setup_mode=True,
    ))
    assert "tools" not in client.messages.stream.call_args.kwargs


def _inventory(*, xy="", focus="", named=()):
    devices = []
    if xy:
        devices.append({"label": xy, "device_type": "XYStageDevice"})
    if focus:
        devices.append({"label": focus, "device_type": "StageDevice"})
    devices.extend({"label": label, "device_type": "StageDevice"} for label in named)
    return {"facts": {"core_device_assignments": {"xy_stage": xy, "focus": focus},
                      "devices": devices}}


def _setup_ctrl(inventory):
    ctrl = MagicMock()
    ctrl._microclaw_setup_draft = setup_tools.SetupDraft(inventory)
    return ctrl


def test_stage_axis_shapes_cover_xy_focus_named_and_multiple_stages():
    assert [a["id"] for a in setup_tools.stage_axes(_inventory(xy="XY"))] == ["XY.x", "XY.y"]
    assert [a["id"] for a in setup_tools.stage_axes(_inventory(focus="Z"))] == ["Z.z"]
    axes = setup_tools.stage_axes(_inventory(xy="XY", focus="Z", named=("Piezo", "Filter Z")))
    assert [a["id"] for a in axes] == ["Filter Z", "Piezo", "XY.x", "XY.y", "Z.z"]


def test_list_axes_refreshes_inventory_only_when_explicit(monkeypatch):
    ctrl = _setup_ctrl(_inventory(focus="Z"))
    enumerate_rig = MagicMock(return_value=_inventory(focus="Z", named=("Piezo",)))
    monkeypatch.setattr(setup_tools, "enumerate_rig", enumerate_rig)
    assert [a["id"] for a in setup_tools.list_stage_axes(ctrl, None)["axes"]] == ["Z.z"]
    enumerate_rig.assert_not_called()
    refreshed = setup_tools.list_stage_axes(ctrl, None, refresh_inventory=True)
    enumerate_rig.assert_called_once_with(ctrl.core)
    assert [a["id"] for a in refreshed["axes"]] == ["Piezo", "Z.z"]


def test_read_positions_uses_each_core_route_without_writes():
    ctrl = _setup_ctrl(_inventory(xy="XY", focus="Z", named=("Piezo",)))
    ctrl.core.get_x_position.return_value = 1
    ctrl.core.get_y_position.return_value = 2
    ctrl.core.get_position.side_effect = lambda *args: 3 if not args else 4
    result = setup_tools.read_stage_positions(ctrl, None)
    assert {p["id"]: p["position_um"] for p in result["positions"]} == {
        "Piezo": 4, "XY.x": 1, "XY.y": 2, "Z.z": 3,
    }
    assert "safe limit" in result["operator_instruction"]
    assert "hardware limits" in result["operator_instruction"]
    assert not any(call[0].startswith("set_") for call in ctrl.core.method_calls)


def test_record_and_threshold_tools_are_in_memory_only(monkeypatch, tmp_path):
    ctrl = _setup_ctrl(_inventory(focus="Z"))
    before = set(tmp_path.iterdir())
    result = setup_tools.record_proposed_stage_bound(
        ctrl, None, axis_id="Z.z", endpoint="low", position_um=2.5,
    )
    setup_tools.set_proposed_acquisition_prompts(
        ctrl, None, confirm_above_frames=600, confirm_above_duration_s=1500,
    )
    assert result["recorded_in_memory"] is True
    assert set(tmp_path.iterdir()) == before
    ctrl.core.assert_not_called()
    assert ctrl.core.method_calls == []


def test_review_reports_missing_axis_and_defaults_are_only_proposals():
    ctrl = _setup_ctrl(_inventory(xy="XY"))
    draft = ctrl._microclaw_setup_draft
    draft.bounds["XY.x"] = {"low": -1, "high": 1}
    review = setup_tools.review_security_config(ctrl, None)
    assert review["complete"] is False
    assert review["missing_axes"] == [{"axis": "XY.y", "endpoints": ["low", "high"]}]
    assert "XY.y" in review["summary"]
    assert review["thresholds"]["confirm_above_frames"] is None
    assert review["thresholds"]["confirm_above_duration_s"] is None
    assert review["thresholds"]["proposed_confirm_above_frames"] == 500
    assert review["thresholds"]["proposed_confirm_above_duration_s"] == 1200


def test_review_shows_the_destination_and_the_exact_bytes_once_complete(
    monkeypatch, tmp_path,
):
    """M5 gate, 2026-08-13: asked to show the path and YAML before writing, the
    model correctly answered that no tool available to it reported either — the
    confirmation dialog showed them, but only after the write was under way.
    Review is where the operator reads the draft, so it carries both."""
    target = tmp_path / "safety_config.yaml"
    monkeypatch.setattr(setup_tools.paths, "default_safety_config", lambda: target)
    ctrl = _setup_ctrl(_inventory(focus="Z"))
    setup_tools.record_proposed_stage_bound(
        ctrl, None, axis_id="Z.z", endpoint="low", position_um=0)
    incomplete = setup_tools.review_security_config(ctrl, None)
    assert incomplete["destination"] == str(target)
    assert "rendered_yaml" not in incomplete      # nothing to render yet

    setup_tools.record_proposed_stage_bound(
        ctrl, None, axis_id="Z.z", endpoint="high", position_um=10)
    setup_tools.set_proposed_acquisition_prompts(
        ctrl, None, confirm_above_frames=500, confirm_above_duration_s=1200)
    review = setup_tools.review_security_config(ctrl, None)
    monkeypatch.setattr(setup_tools.tools, "CONFIRM_FN", lambda *a, **k: True)
    setup_tools.write_security_config(ctrl, None)
    # What review promised is byte-identical to what the writer published.
    assert review["rendered_yaml"] == target.read_text(encoding="utf-8")
    assert review["written_to_disk"] is False


def test_complete_requires_all_bounds_and_both_thresholds():
    ctrl = _setup_ctrl(_inventory(focus="Z"))
    setup_tools.record_proposed_stage_bound(ctrl, None, axis_id="Z.z", endpoint="low", position_um=0)
    setup_tools.record_proposed_stage_bound(ctrl, None, axis_id="Z.z", endpoint="high", position_um=10)
    assert not setup_tools.review_security_config(ctrl, None)["complete"]
    setup_tools.set_proposed_acquisition_prompts(
        ctrl, None, confirm_above_frames=500, confirm_above_duration_s=1200,
    )
    assert setup_tools.review_security_config(ctrl, None)["complete"]


def test_write_security_config_without_capability_is_refused():
    ctrl = _setup_ctrl(_inventory(focus="Z"))
    ctrl._microclaw_setup_write_capability = setup_tools.SetupWriteCapability()
    result = json.loads(tools.execute_tool(
        "write_security_config", {}, ctrl, None,
        webserve.SETUP_TOOL_REGISTRY, setup_mode=True,
    ))
    assert "not enabled" in result["error"]


def _complete_draft(ctrl, bounds):
    for axis_id, (low, high) in bounds.items():
        setup_tools.record_proposed_stage_bound(
            ctrl, None, axis_id=axis_id, endpoint="low", position_um=low,
        )
        setup_tools.record_proposed_stage_bound(
            ctrl, None, axis_id=axis_id, endpoint="high", position_um=high,
        )
    setup_tools.set_proposed_acquisition_prompts(
        ctrl, None, confirm_above_frames=500, confirm_above_duration_s=1200,
    )


def test_writer_round_trips_m5_shape_confirms_exact_text_and_refuses_replay(
    monkeypatch, tmp_path,
):
    inventory = _inventory(
        xy="SmarAct 2D", focus="PIZStage",
        named=("SmarAct 1D", "Thorlabs ELL17/ELL20", "Thorlabs ELL20"),
    )
    ctrl = _setup_ctrl(inventory)
    ctrl._microclaw_setup_write_capability = setup_tools.SetupWriteCapability(True)
    bounds = {
        "SmarAct 2D.x": (-12000.5, 12000.25),
        "SmarAct 2D.y": (-8000, 8000),
        "PIZStage.z": (100, 7800),
        "SmarAct 1D": (0, 200),
        "Thorlabs ELL17/ELL20": (-10, 10),
        "Thorlabs ELL20": (1, 19),
    }
    _complete_draft(ctrl, bounds)
    target = tmp_path / "safety_config.yaml"
    monkeypatch.setattr(setup_tools.paths, "default_safety_config", lambda: target)
    prompts = []
    monkeypatch.setattr(
        setup_tools.tools, "CONFIRM_FN",
        lambda prompt, **kwargs: prompts.append(prompt) or True,
    )

    result = setup_tools.write_security_config(ctrl, None)
    assert result == {
        "restart_required": True, "path": str(target),
        "message": setup_tools.RESTART_MESSAGE, "reviewed": True,
        "declared_stage_ranges": 6,
    }
    rendered = target.read_text(encoding="utf-8")
    assert str(target) in prompts[0]
    assert rendered in prompts[0]
    loaded = yaml.safe_load(rendered)
    parsed = setup_tools.ParsedSafetyConfig.from_yaml(str(target))
    assert loaded["reviewed"] is True
    assert loaded["stage"] == {
        "x_min": -12000.5, "x_max": 12000.25,
        "y_min": -8000.0, "y_max": 8000.0,
        "z_min": 100.0, "z_max": 7800.0,
    }
    # The operator reads this document before approving it, so the axes are
    # ordered x, y, z rather than by device label — a draft sorted by id puts
    # `PIZStage.z` above `SmarAct 2D.x`.
    assert list(loaded["stage"]) == [
        "x_min", "x_max", "y_min", "y_max", "z_min", "z_max",
    ]
    assert loaded["named_stages"] == [
        {"device": "SmarAct 1D", "min_um": 0.0, "max_um": 200.0},
        {"device": "Thorlabs ELL17/ELL20", "min_um": -10.0, "max_um": 10.0},
        {"device": "Thorlabs ELL20", "min_um": 1.0, "max_um": 19.0},
    ]
    assert len(parsed.ranges) == 6
    with pytest.raises(setup_tools.SetupRefusal, match="already written.*restarted"):
        setup_tools.write_security_config(ctrl, None)


def test_writer_decline_preserves_draft_and_writes_nothing(monkeypatch, tmp_path):
    ctrl = _setup_ctrl(_inventory(focus="Z"))
    ctrl._microclaw_setup_write_capability = setup_tools.SetupWriteCapability(True)
    _complete_draft(ctrl, {"Z.z": (1, 2)})
    target = tmp_path / "safety_config.yaml"
    monkeypatch.setattr(setup_tools.paths, "default_safety_config", lambda: target)
    monkeypatch.setattr(setup_tools.tools, "CONFIRM_FN", lambda *a, **k: False)
    before = dict(ctrl._microclaw_setup_draft.bounds)
    with pytest.raises(setup_tools.SetupRefusal, match="operator declined"):
        setup_tools.write_security_config(ctrl, None)
    assert not target.exists()
    assert ctrl._microclaw_setup_draft.bounds == before
    assert ctrl._microclaw_setup_write_capability.consumed is False


def test_writer_refuses_existing_target_without_touching_it(monkeypatch, tmp_path):
    ctrl = _setup_ctrl(_inventory(focus="Z"))
    ctrl._microclaw_setup_write_capability = setup_tools.SetupWriteCapability(True)
    _complete_draft(ctrl, {"Z.z": (1, 2)})
    target = tmp_path / "safety_config.yaml"
    target.write_text("keep me", encoding="utf-8")
    monkeypatch.setattr(setup_tools.paths, "default_safety_config", lambda: target)
    confirm = MagicMock()
    monkeypatch.setattr(setup_tools.tools, "CONFIRM_FN", confirm)
    # `match` is a regex, and a Windows path is not one: `C:\Users\...` carries
    # `\U`, which fails to compile. This test passed on macOS and failed on M5
    # (block 48d gate, 2026-08-13) — the rig is where every gate runs pytest.
    with pytest.raises(setup_tools.SetupRefusal, match=re.escape(str(target))):
        setup_tools.write_security_config(ctrl, None)
    assert target.read_text(encoding="utf-8") == "keep me"
    confirm.assert_not_called()


@pytest.mark.parametrize("missing", ["axis", "threshold"])
def test_writer_reuses_draft_completeness_refusal(monkeypatch, tmp_path, missing):
    ctrl = _setup_ctrl(_inventory(focus="Z"))
    ctrl._microclaw_setup_write_capability = setup_tools.SetupWriteCapability(True)
    if missing == "axis":
        setup_tools.set_proposed_acquisition_prompts(
            ctrl, None, confirm_above_frames=500, confirm_above_duration_s=1200,
        )
    else:
        setup_tools.record_proposed_stage_bound(
            ctrl, None, axis_id="Z.z", endpoint="low", position_um=1,
        )
        setup_tools.record_proposed_stage_bound(
            ctrl, None, axis_id="Z.z", endpoint="high", position_um=2,
        )
    target = tmp_path / "safety_config.yaml"
    monkeypatch.setattr(setup_tools.paths, "default_safety_config", lambda: target)
    with pytest.raises(setup_tools.SetupRefusal, match="draft is incomplete"):
        setup_tools.write_security_config(ctrl, None)
    assert not target.exists()


def test_named_xy_stage_refuses_and_names_device(monkeypatch, tmp_path):
    inventory = _inventory(focus="Z")
    inventory["facts"]["devices"].append(
        {"label": "Second XY", "device_type": "XYStageDevice"}
    )
    ctrl = _setup_ctrl(inventory)
    ctrl._microclaw_setup_write_capability = setup_tools.SetupWriteCapability(True)
    _complete_draft(ctrl, {"Z.z": (1, 2), "Second XY.x": (3, 4), "Second XY.y": (5, 6)})
    target = tmp_path / "safety_config.yaml"
    monkeypatch.setattr(setup_tools.paths, "default_safety_config", lambda: target)
    with pytest.raises(setup_tools.SetupRefusal, match="Second XY") as refusal:
        setup_tools.write_security_config(ctrl, None)
    assert "cannot express per-axis bounds" in str(refusal.value)
    assert not target.exists()


def test_real_loader_rejection_removes_temporary_and_target(monkeypatch, tmp_path):
    ctrl = _setup_ctrl(_inventory(focus="Z"))
    ctrl._microclaw_setup_write_capability = setup_tools.SetupWriteCapability(True)
    _complete_draft(ctrl, {"Z.z": (1, 2)})
    target = tmp_path / "safety_config.yaml"
    monkeypatch.setattr(setup_tools.paths, "default_safety_config", lambda: target)
    monkeypatch.setattr(
        setup_tools.ParsedSafetyConfig, "from_yaml",
        MagicMock(side_effect=setup_tools.SafetyConfigError("malformed")),
    )
    confirm = MagicMock()
    monkeypatch.setattr(setup_tools.tools, "CONFIRM_FN", confirm)
    with pytest.raises(setup_tools.SetupRefusal, match="real security-config loader"):
        setup_tools.write_security_config(ctrl, None)
    assert not target.exists()
    assert not list(tmp_path.glob(".safety_config.yaml.*.tmp"))
    confirm.assert_not_called()


def test_setup_system_blocks_omit_rig_interview_and_normal_blocks_include_it(
    monkeypatch,
):
    monkeypatch.setattr(agent, "load_knowledge", lambda: {})
    monkeypatch.setattr(agent, "rig_profile_gaps", lambda knowledge: ["stage_layout"])
    setup = "\n".join(block["text"] for block in agent._system_blocks(setup_mode=True))
    normal = "\n".join(block["text"] for block in agent._system_blocks())
    assert RIG_INTERVIEW_PROMPT.splitlines()[0] not in setup
    assert RIG_INTERVIEW_PROMPT.splitlines()[0] in normal


def test_stored_rig_topics_are_not_asked_again(monkeypatch):
    monkeypatch.setattr(agent, "load_knowledge", lambda: {"rig": {"stage_layout": "stored"}})
    monkeypatch.setattr(agent, "rig_profile_gaps", lambda knowledge: [])
    normal = "\n".join(block["text"] for block in agent._system_blocks())
    assert RIG_INTERVIEW_PROMPT.splitlines()[0] not in normal


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
    assert "write_security_config" not in {
        schema["name"] for schema in session.tool_schemas
    }
    assert session.history == [{
        "role": "assistant", "content": webserve.SETUP_FIRST_MESSAGE,
    }]
    page = TestClient(webserve.build_app(session)).get("/").text
    assert 'class="banner" id="setup-banner"' in page
    assert "Setup mode — hardware control locked" in page
    assert 'id="setup-checklist"' in page
    status = TestClient(webserve.build_app(session)).get("/api/setup-status").json()
    assert status["thresholds"]["proposed_confirm_above_frames"] == 500
    assert status["thresholds"]["proposed_confirm_above_duration_s"] == 1200


def test_keyless_setup_constructs_and_only_browser_gate_blocks_turns(monkeypatch, tmp_path):
    class Controller:
        core = object()

        def __init__(self, port, guard):
            assert guard is None

        def is_connected(self):
            return True

    key = {"value": None}
    monkeypatch.setattr(webserve, "MicroscopeController", Controller)
    monkeypatch.setattr(webserve, "enumerate_rig", lambda core: {"stages": []})
    monkeypatch.setattr(
        webserve.credentials, "load_api_key",
        lambda: (key["value"], "env" if key["value"] else None),
    )
    monkeypatch.setattr(
        webserve, "set_api_key", lambda value: key.__setitem__("value", value),
    )
    args = types.SimpleNamespace(
        safety_config=None, port=1, model=None, save_history=False,
        host="127.0.0.1", history_retention_days=None,
        setup_write_security_config=True,
    )
    monkeypatch.setattr(webserve.config, "default_safety_config", lambda: tmp_path / "missing.yaml")
    session = webserve.build_session(args)
    assert isinstance(session, webserve.SetupSession)
    assert session.editable is True
    assert session.history == [{
        "role": "assistant", "content": webserve.SETUP_FIRST_MESSAGE,
    }]
    client = TestClient(webserve.build_app(session))
    assert client.get("/api/key").json()["has_key"] is False
    refused = client.post("/api/prompt", json={"message": "begin setup"})
    assert refused.status_code == 400
    assert "No Anthropic API key" in refused.json()["detail"]
    saved = client.post("/api/key", json={"key": "sk-ant-test", "persist": False})
    assert saved.status_code == 200
    assert saved.json()["has_key"] is True


def test_setup_session_offers_writer_only_with_explicit_capability(monkeypatch, tmp_path):
    class Controller:
        core = object()

        def __init__(self, port, guard):
            pass

        def is_connected(self):
            return True

    monkeypatch.setattr(webserve, "MicroscopeController", Controller)
    monkeypatch.setattr(webserve, "enumerate_rig", lambda core: {"stages": []})
    monkeypatch.setattr(webserve.credentials, "load_api_key", lambda: (None, None))
    args = types.SimpleNamespace(
        safety_config=None, port=1, model=None, save_history=False,
        host="127.0.0.1", history_retention_days=None,
        setup_write_security_config=True,
    )
    monkeypatch.setattr(webserve.config, "default_safety_config", lambda: tmp_path / "missing.yaml")
    session = webserve.build_session(args)
    assert {schema["name"] for schema in session.tool_schemas} == webserve.SETUP_TOOL_NAMES
    assert session.ctrl._microclaw_setup_write_capability.enabled is True


def test_valid_upgrade_builds_normal_session_without_write_authority(monkeypatch, tmp_path):
    path = tmp_path / "safety_config.yaml"
    path.write_text("valid", encoding="utf-8")
    validation = types.SimpleNamespace(can_start_live_validation=True, parsed="parsed")
    monkeypatch.setattr(webserve.config, "validate_safety_config", lambda p: validation)
    seen = []

    class Normal:
        def __init__(self, args, parsed_safety):
            assert parsed_safety == "parsed"
            seen.append(args.setup_write_security_config)

    monkeypatch.setattr(webserve, "Session", Normal)
    args = types.SimpleNamespace(
        safety_config=str(path), setup_write_security_config=False,
    )
    session = webserve.build_session(args)
    assert isinstance(session, Normal)
    assert seen == [False]


def test_setup_refusal_hint_does_not_send_the_model_to_the_schema():
    """M5 gate, 2026-08-14. The replay refusal came back with the ValueError
    hint — "a tool was called with a missing, extra, or wrong-typed parameter.
    Re-read the tool schema" — because SetupRefusal subclasses ValueError. The
    one remedy that cannot work is a different set of arguments."""
    from microclaw.errors import hint_for_error

    hint = hint_for_error(setup_tools.SetupRefusal(
        "SETUP REFUSAL: The security config is already written and Microclaw "
        "must be restarted."
    ))
    assert "restart" in hint.lower()
    # It may mention parameters in order to rule them out; what it must not do
    # is send the model back to the schema to fix a call that was well-formed.
    assert "re-read the tool schema" not in hint.lower()
    assert hint != hint_for_error(ValueError("some ordinary argument mistake"))


def test_restart_message_says_how_to_end_the_setup_server():
    """The installer starts setup as a foreground server. Without this the
    operator saved their bounds, closed the browser, and was left with a running
    window and nothing anywhere saying how to stop it (48e acceptance run)."""
    message = setup_tools.RESTART_MESSAGE.lower()
    assert "ctrl+c" in message
    assert "desktop icon" in message


def test_the_seeded_first_message_is_a_shape_the_transcript_renders():
    """It is delivered to the browser through /api/history and rendered client
    side, so a shape the renderer skips is a blank page on first launch."""
    from microclaw.webserve import SETUP_FIRST_MESSAGE

    assert isinstance(SETUP_FIRST_MESSAGE, str) and SETUP_FIRST_MESSAGE.strip()
