import inspect
import json

import pytest

from microclaw import tools
from microclaw.errors import hint_for_error
from microclaw import tools_schema


_SCHEMA_BY_NAME = {tool["name"]: tool for tool in tools_schema.TOOLS}
_PROTOCOL_PARAM_KEYS = (
    "exposure_ms",
    "channel",
    "n_frames",
    "interval_s",
    "laser_slot",
    "z_start_um",
    "z_end_um",
    "z_step_um",
)
_TOP_LEVEL_PROTOCOL_TOOLS = (
    "run_tile_acquisition",
    "run_multiposition_acquisition",
    "run_multiposition_with_autofocus",
    "run_adaptive_survey",
)


def _protocol_schema(name):
    return _SCHEMA_BY_NAME[name]["input_schema"]["properties"]["protocol_params"]


def test_top_level_protocol_params_publish_one_complete_shared_schema():
    shared_schema = tools_schema._PROTOCOL_PARAMS_SCHEMA
    by_signature = {
        name
        for name, fn in tools.TOOL_REGISTRY.items()
        if "protocol_params" in inspect.signature(fn).parameters
    }
    assert by_signature == set(_TOP_LEVEL_PROTOCOL_TOOLS)

    schemas = [_protocol_schema(name) for name in _TOP_LEVEL_PROTOCOL_TOOLS]
    assert all(schema is shared_schema for schema in schemas)
    for schema in schemas:
        assert "exposure_ms" in schema["properties"]
        assert "laser_slot" in schema["properties"]
        assert "never at the top level" in schema["description"]
        assert "timelapse: {n_frames, interval_s" in schema["description"]
        assert "zstack: {z_start_um, z_end_um, z_step_um" in schema["description"]

    acquire_on_hit = _SCHEMA_BY_NAME["run_adaptive_survey"]["input_schema"][
        "properties"
    ]["acquire_on_hit"]
    nested = acquire_on_hit["properties"]["protocol_params"]
    assert nested is not shared_schema
    for key in ("z_offset_start_um", "z_offset_end_um", "z_step_um"):
        assert key in acquire_on_hit["description"]
    assert "relative" in acquire_on_hit["description"]


_TOOLS_WITH_PROTOCOL_PARAMS = tuple(
    name
    for name, fn in tools.TOOL_REGISTRY.items()
    if "protocol_params" in inspect.signature(fn).parameters
)
_REQUIRED_VALUE = {
    "rows": 1,
    "cols": 1,
    "step_um": 1,
    "z_range_um": 1,
    "z_step_um": 1,
    "protocol": "timelapse",
    "save_dir": "/tmp/microclaw-protocol-hint-test",
    "hook_strategy": "test_hook",
}


def _required_tool_input(fn):
    return {
        parameter.name: _REQUIRED_VALUE[parameter.name]
        for parameter in inspect.signature(fn).parameters.values()
        if parameter.name not in {"ctrl", "guard"}
        and parameter.default is inspect.Parameter.empty
    }


_UNEXPECTED_PROTOCOL_PARAM_CASES = tuple(
    (name, key)
    for name in _TOOLS_WITH_PROTOCOL_PARAMS
    for key in _PROTOCOL_PARAM_KEYS
    if key not in inspect.signature(tools.TOOL_REGISTRY[name]).parameters
)


@pytest.mark.parametrize(("name", "key"), _UNEXPECTED_PROTOCOL_PARAM_CASES)
def test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature(
    name, key, monkeypatch,
):
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)
    tool_input = _required_tool_input(tools.TOOL_REGISTRY[name])
    tool_input[key] = 100

    result = json.loads(tools.execute_tool(
        name, tool_input, object(), object(),
    ))

    assert "unexpected keyword argument" in result["error"]
    assert "protocol_params" in result["hint"]
    assert key in result["hint"]


def test_autofocus_z_step_is_a_real_top_level_parameter_not_a_nested_hint(
    monkeypatch,
):
    name = "run_multiposition_with_autofocus"
    fn = tools.TOOL_REGISTRY[name]
    tool_input = _required_tool_input(fn)
    assert "z_step_um" in inspect.signature(fn).parameters
    inspect.signature(fn).bind(object(), object(), **tool_input)
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)

    result = json.loads(tools.execute_tool(
        name, tool_input, object(), object(),
    ))

    assert "unexpected keyword argument 'z_step_um'" not in result.get("error", "")
    assert "protocol_params" not in result.get("hint", "")


@pytest.mark.parametrize(
    ("name", "key"),
    (("run_zstack", "n_frames"), ("run_timelapse", "z_start_um")),
)
def test_execute_tool_keeps_generic_hint_for_tools_without_protocol_params(
    name, key, monkeypatch,
):
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)
    exc = TypeError(f"{name}() got an unexpected keyword argument '{key}'")

    result = json.loads(tools.execute_tool(
        name, {key: 1}, object(), object(),
    ))

    assert result["hint"] == hint_for_error(exc)
    assert "protocol_params" not in result["hint"]


def test_execute_tool_keeps_generic_hint_for_inner_forwarding_error():
    def outer_tool(ctrl, guard, protocol_params=None):
        raise TypeError(
            "run_zstack() got an unexpected keyword argument 'n_frames'"
        )

    exc = TypeError("run_zstack() got an unexpected keyword argument 'n_frames'")
    result = json.loads(tools.execute_tool(
        "outer_tool",
        {"protocol_params": {"n_frames": 1}},
        object(),
        object(),
        registry={"outer_tool": outer_tool},
    ))

    assert result["hint"] == hint_for_error(exc)
    assert "protocol_params" not in result["hint"]
