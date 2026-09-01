import inspect
import json

import pytest

from microclaw import tools
from microclaw.errors import hint_for_error
from microclaw.tools_schema import _PROTOCOL_PARAMS_SCHEMA, TOOLS


_SCHEMA_BY_NAME = {tool["name"]: tool for tool in TOOLS}
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
    by_signature = {
        name
        for name, fn in tools.TOOL_REGISTRY.items()
        if "protocol_params" in inspect.signature(fn).parameters
    }
    assert by_signature == set(_TOP_LEVEL_PROTOCOL_TOOLS)

    schemas = [_protocol_schema(name) for name in _TOP_LEVEL_PROTOCOL_TOOLS]
    assert all(schema is _PROTOCOL_PARAMS_SCHEMA for schema in schemas)
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
    assert nested is not _PROTOCOL_PARAMS_SCHEMA
    for key in ("z_offset_start_um", "z_offset_end_um", "z_step_um"):
        assert key in acquire_on_hit["description"]
    assert "relative" in acquire_on_hit["description"]


_TOOLS_WITH_PROTOCOL_PARAMS = tuple(
    name
    for name, fn in tools.TOOL_REGISTRY.items()
    if "protocol_params" in inspect.signature(fn).parameters
)


@pytest.mark.parametrize("name", _TOOLS_WITH_PROTOCOL_PARAMS)
@pytest.mark.parametrize("key", _PROTOCOL_PARAM_KEYS)
def test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature(
    name, key, monkeypatch,
):
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)

    result = json.loads(tools.execute_tool(
        name, {key: 100}, object(), object(),
    ))

    assert "unexpected keyword argument" in result["error"]
    assert "protocol_params" in result["hint"]
    assert key in result["hint"]


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
