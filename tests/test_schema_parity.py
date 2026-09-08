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


@pytest.mark.parametrize("name", [
    "run_autofocus", "get_focus_lock_state", "set_focus_lock",
])
def test_focus_lock_schema_anchors_name_which_lock_and_how_to_identify_it(name):
    """A presence regression, not a routing test -- but it has to check the
    discriminator, not just the skill name.

    The first version of these anchors read "on a rig with this kind of
    hardware lock, call load_skill(name='nikon-pfs')", and the antecedent of
    "this kind" was a generic "hardware focus lock". Read plainly that tells an
    agent on an ASI CRISP rig to load the Nikon skill -- which is precisely
    what the CRISP limb of the discriminator fixture exists to prevent, one
    level down. No suite test can catch that: the discriminator tests what the
    tool returns, not how a model reads a description. So assert here that each
    anchor names *which* lock and identifies it by the returned device value.
    """
    description = _SCHEMA_BY_NAME[name]["description"]
    assert "nikon-pfs" in description
    assert "Perfect Focus" in description
    anchor = next(s for s in description.split(". ") if "nikon-pfs" in s)
    assert "device" in anchor


def test_save_knowledge_value_teaches_optical_path_map_shape_at_point_of_use():
    description = _SCHEMA_BY_NAME["save_knowledge"]["input_schema"]["properties"]["value"]["description"]
    assert "'kind': 'optical_path_position_map'" in description
    assert "'device': <StateDevice config label>" in description
    assert "'positions': {<exact state label>: <operator meaning>" in description
    assert "do not send observed_on" in description


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


def test_image_metric_reason_has_its_own_audit_rule():
    parameter = _SCHEMA_BY_NAME["run_autofocus"]["input_schema"]["properties"][
        "image_metric_reason"
    ]
    assert parameter["type"] == "string"
    assert "non-empty" in parameter["description"]
    assert "operator asked" in parameter["description"]
    assert "probe reported no band" in parameter["description"]
    assert "caller assertion" in parameter["description"]
    assert "not an instrument measurement" in parameter["description"]


def test_mosaic_and_multiposition_descriptions_agree_on_dataset_shape():
    mosaic = _SCHEMA_BY_NAME["build_stage_coordinate_mosaic"]["description"]
    multipos = _SCHEMA_BY_NAME["run_multiposition_acquisition"]["description"]
    autofocus = _SCHEMA_BY_NAME["run_multiposition_with_autofocus"]["description"]

    assert "ONE dataset" in mosaic and "hook_strategy" in mosaic
    order = _SCHEMA_BY_NAME["run_multiposition_acquisition"]["input_schema"]["properties"]["acquisition_order"]["description"]
    assert "Hooked interval_s=0 shares a position-axis dataset" in order
    assert "Without hook_strategy" in multipos and "CANNOT" in multipos
    assert "one dataset per position" in autofocus and "CANNOT" in autofocus


@pytest.mark.parametrize("name", ["snap_and_analyze", "run_autofocus"])
def test_region_declares_both_forms_at_the_top_level(name):
    """`region` takes an array or the string "drawn", and the model must see both.

    54c first expressed this as `oneOf: [{type: array}, {const: "drawn"}]`,
    which carries no top-level "type". On the Nikon (2026-08-19) the model then
    sent the array as a quoted string four calls running, three of them after
    the operator asked for an array in plain words, and every one was refused as
    malformed -- 54b's literal-region capability was unreachable through the
    agent while its unit tests, which call the function with a real list, stayed
    green. Only a schema shape a model honours makes the tool callable.
    """
    schema = _SCHEMA_BY_NAME[name]["input_schema"]["properties"]["region"]
    assert schema["type"] == ["array", "string"]
    assert "oneOf" not in schema and "anyOf" not in schema
    # The array shape survives for the literal form.
    assert schema["items"] == {"type": "integer"}
    assert schema["minItems"] == schema["maxItems"] == 4
    # And the description names both forms, since the type alone cannot say
    # which strings are legal.
    assert "drawn" in schema["description"]
    assert "ARRAY" in schema["description"]


@pytest.mark.parametrize("name", sorted(_SCHEMA_BY_NAME))
def test_no_tool_schema_uses_a_top_level_combinator(name):
    """The Messages API rejects oneOf/allOf/anyOf at the top of input_schema.

    Not a style rule and not a per-tool problem: the request carries every tool,
    so ONE offending schema returns

        tools.36.custom.input_schema: input_schema does not support oneOf,
        allOf, or anyOf at the top level

    and NO tools load — microclaw cannot start a session at all. Block 56b
    expressed "either z_range_um or z_min_um/z_max_um" as a top-level oneOf; the
    whole suite stayed green because nothing checked the schemas against the
    API's own structural rules, and it was found by an operator on the first
    prompt of a rig gate.

    Express an either/or in the descriptions and enforce it with the tool's own
    refusals, which can say why. See run_autofocus.
    """
    schema = _SCHEMA_BY_NAME[name]["input_schema"]
    for combinator in ("oneOf", "allOf", "anyOf"):
        assert combinator not in schema, (
            f"{name}.input_schema has a top-level {combinator}; the Messages API "
            "rejects the entire request, so no tool loads"
        )
    assert schema.get("type") == "object", (
        f"{name}.input_schema must declare type 'object' at the top level"
    )
