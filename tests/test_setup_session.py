import json
import types
from unittest.mock import MagicMock
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
    assert offered == webserve.SETUP_TOOL_NAMES - {"write_security_config"}
    system = "\n".join(block["text"] for block in client.messages.stream.call_args.kwargs["system"])
    assert RIG_INTERVIEW_PROMPT.splitlines()[0] not in system
    result = next(event for event in events if event["type"] == "tool_result")
    assert "setup mode" in result["content"]


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


def test_complete_requires_all_bounds_and_both_thresholds():
    ctrl = _setup_ctrl(_inventory(focus="Z"))
    setup_tools.record_proposed_stage_bound(ctrl, None, axis_id="Z.z", endpoint="low", position_um=0)
    setup_tools.record_proposed_stage_bound(ctrl, None, axis_id="Z.z", endpoint="high", position_um=10)
    assert not setup_tools.review_security_config(ctrl, None)["complete"]
    setup_tools.set_proposed_acquisition_prompts(
        ctrl, None, confirm_above_frames=500, confirm_above_duration_s=1200,
    )
    assert setup_tools.review_security_config(ctrl, None)["complete"]


def test_write_security_config_remains_refused():
    result = json.loads(tools.execute_tool(
        "write_security_config", {}, object(), None,
        webserve.SETUP_TOOL_REGISTRY, setup_mode=True,
    ))
    assert "setup mode" in result["error"]


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
