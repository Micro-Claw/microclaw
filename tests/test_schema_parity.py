"""Cross-check the tool JSON schemas against the actual function signatures.

Catches parameter drift: a schema advertising a parameter the function doesn't
accept, or a required function parameter missing from the schema.
"""
import inspect

import pytest

from microclaw.tools import TOOL_REGISTRY
from microclaw.tools_schema import TOOLS

_SCHEMA_BY_NAME = {t["name"]: t for t in TOOLS}
_INJECTED = {"ctrl", "guard", "records"}  # supplied by execute_tool, never in schema


def test_registry_and_schema_cover_the_same_tools():
    assert set(TOOL_REGISTRY) == set(_SCHEMA_BY_NAME)


@pytest.mark.parametrize("name", sorted(TOOL_REGISTRY))
def test_schema_matches_signature(name):
    fn = TOOL_REGISTRY[name]
    schema = _SCHEMA_BY_NAME[name]
    params = {
        p.name: p
        for p in inspect.signature(fn).parameters.values()
        if p.name not in _INJECTED
        and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    }
    props = set(schema["input_schema"].get("properties", {}))
    required = set(schema["input_schema"].get("required", []))

    # No phantom schema properties — every advertised param is real.
    assert props <= set(params), (
        f"{name}: schema advertises params the function lacks: {props - set(params)}"
    )
    # Every function parameter with no default is a mandatory input; it must be
    # in the schema so the model knows to supply it.
    mandatory = {n for n, p in params.items() if p.default is inspect.Parameter.empty}
    assert mandatory <= props, (
        f"{name}: required params missing from schema: {mandatory - props}"
    )
    # schema 'required' must reference real properties.
    assert required <= props, (
        f"{name}: 'required' lists non-properties: {required - props}"
    )


@pytest.mark.parametrize("name", [
    "run_zstack", "run_timelapse", "run_adaptive_survey",
    "run_multiposition_acquisition",
])
def test_hook_capability_parameters_are_declared(name):
    props = _SCHEMA_BY_NAME[name]["input_schema"]["properties"]
    assert {"illumination_envelope", "artifact_limits"} <= set(props)
    description = props["illumination_envelope"]["description"]
    assert "Power-only" in description
    assert "shutter enable" in description
    assert "confirmed once before" in description


def test_adaptive_survey_declares_exposure_bounded_autofocus():
    props = _SCHEMA_BY_NAME["run_adaptive_survey"]["input_schema"]["properties"]
    budget = props["autofocus_budget"]
    assert set(budget["required"]) == {
        "max_exposures", "z_range_um", "z_step_um", "method", "settle_ms",
    }
    assert "sweep snaps plus refocused-tile re-exposures" in budget["description"]


def test_fixed_hook_action_plan_schema_teaches_the_discriminated_action_shape():
    for tool_name in ("run_timelapse", "run_zstack"):
        plan = _SCHEMA_BY_NAME[tool_name]["input_schema"]["properties"]["hook_action_plan"]
        entry = plan["items"]
        assert set(entry["required"]) == {"hook_event_index", "actions"}
        action = entry["properties"]["actions"]["items"]
        variants = action["oneOf"]
        assert [variant["properties"]["kind"]["enum"] for variant in variants] == [
            ["MoveNamedStage"], ["SetDeviceProperty"],
        ]
        assert [set(variant["required"]) for variant in variants] == [
            {"kind", "position_um"}, {"kind", "value"},
        ]
        assert all(variant["additionalProperties"] is False for variant in variants)


def test_run_autofocus_description_says_sweep_is_headless():
    description = _SCHEMA_BY_NAME["run_autofocus"]["description"]
    assert "sweep is headless" in description
    assert "live view is paused for its duration and restored afterwards" in description
    assert "viewer does not show the sweep as it happens" in description


def test_mosaic_and_multiposition_descriptions_agree_on_dataset_shape():
    mosaic = _SCHEMA_BY_NAME["build_stage_coordinate_mosaic"]["description"]
    multipos = _SCHEMA_BY_NAME["run_multiposition_acquisition"]["description"]
    autofocus = _SCHEMA_BY_NAME["run_multiposition_with_autofocus"]["description"]

    assert "ONE dataset" in mosaic and "hook_strategy" in mosaic
    assert "single dataset with a `position` axis" in multipos
    assert "Without hook_strategy" in multipos and "CANNOT" in multipos
    assert "one dataset per position" in autofocus and "CANNOT" in autofocus
