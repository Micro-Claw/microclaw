"""Read-only and in-memory tools for the restricted security setup session."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from microclaw.rig_inventory import enumerate_rig


PROPOSED_CONFIRM_FRAMES = 500
PROPOSED_CONFIRM_DURATION_S = 1200


@dataclass
class SetupDraft:
    inventory: dict
    axes: list[dict] = field(default_factory=list)
    bounds: dict[str, dict[str, float]] = field(default_factory=dict)
    confirm_above_frames: int | None = None
    confirm_above_duration_s: float | None = None

    def __post_init__(self) -> None:
        self.axes = stage_axes(self.inventory)

    def status(self) -> dict:
        rows = []
        missing = []
        for axis in self.axes:
            endpoints = self.bounds.get(axis["id"], {})
            absent = [name for name in ("low", "high") if name not in endpoints]
            if absent:
                missing.append({"axis": axis["id"], "endpoints": absent})
            rows.append({**axis, "low": endpoints.get("low"), "high": endpoints.get("high")})
        threshold_missing = []
        if self.confirm_above_frames is None:
            threshold_missing.append("confirm_above_frames")
        if self.confirm_above_duration_s is None:
            threshold_missing.append("confirm_above_duration_s")
        return {
            "complete": not missing and not threshold_missing,
            "axes": rows,
            "missing_axes": missing,
            "thresholds": {
                "confirm_above_frames": self.confirm_above_frames,
                "confirm_above_duration_s": self.confirm_above_duration_s,
                "proposed_confirm_above_frames": PROPOSED_CONFIRM_FRAMES,
                "proposed_confirm_above_duration_s": PROPOSED_CONFIRM_DURATION_S,
            },
            "missing_thresholds": threshold_missing,
        }


def stage_axes(inventory: dict) -> list[dict]:
    facts = inventory.get("facts", inventory)
    assignments = facts.get("core_device_assignments", {})
    devices = facts.get("devices", [])
    result = []
    xy = assignments.get("xy_stage") or ""
    focus = assignments.get("focus") or ""
    if xy:
        result.extend([
            {"id": f"{xy}.x", "device": xy, "axis": "x", "role": "core_xy"},
            {"id": f"{xy}.y", "device": xy, "axis": "y", "role": "core_xy"},
        ])
    if focus:
        result.append({"id": f"{focus}.z", "device": focus, "axis": "z", "role": "core_focus"})
    assigned = {xy, focus, ""}
    for device in devices:
        label = device.get("label")
        kind = device.get("device_type")
        if label in assigned:
            continue
        if kind == "StageDevice":
            result.append({"id": label, "device": label, "axis": "position", "role": "named_stage"})
        elif kind == "XYStageDevice":
            result.extend([
                {"id": f"{label}.x", "device": label, "axis": "x", "role": "named_xy_stage"},
                {"id": f"{label}.y", "device": label, "axis": "y", "role": "named_xy_stage"},
            ])
    return sorted(result, key=lambda item: item["id"])


def _state(ctrl) -> SetupDraft:
    return ctrl._microclaw_setup_draft


def list_stage_axes(ctrl, guard, *, refresh_inventory: bool = False):
    """List live stage axes; refresh is explicit because enumeration is hardware contact."""
    draft = _state(ctrl)
    if refresh_inventory:
        draft.inventory = enumerate_rig(ctrl.core)
        draft.axes = stage_axes(draft.inventory)
    return draft.status()


def read_stage_positions(ctrl, guard):
    draft = _state(ctrl)
    core = ctrl.core
    positions = []
    for axis in draft.axes:
        if axis["role"] == "core_xy":
            value = core.get_x_position() if axis["axis"] == "x" else core.get_y_position()
        elif axis["role"] == "core_focus":
            value = core.get_position()
        elif axis["role"] == "named_stage":
            value = core.get_position(axis["device"])
        else:
            value = (core.get_x_position(axis["device"]) if axis["axis"] == "x"
                     else core.get_y_position(axis["device"]))
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"Stage axis {axis['id']} returned a non-finite position")
        positions.append({**axis, "position_um": value})
    return {
        "positions": positions,
        "operator_instruction": (
            "These are current positions, not hardware limits. A hardware limit is "
            "the mechanism's technical boundary; a safe limit is the operator-chosen "
            "working boundary. Move each axis in Micro-Manager to its safe low or safe high limit, read "
            "again, echo the proposed safe range, and obtain approval before recording it."
        ),
    }


def record_proposed_stage_bound(ctrl, guard, *, axis_id: str, endpoint: str,
                                position_um: float):
    draft = _state(ctrl)
    if axis_id not in {axis["id"] for axis in draft.axes}:
        raise ValueError(f"Unknown live stage axis: {axis_id}")
    if endpoint not in {"low", "high"}:
        raise ValueError("endpoint must be 'low' or 'high'; unbounded is not available")
    value = float(position_um)
    if not math.isfinite(value):
        raise ValueError("safe limit must be finite")
    proposed = dict(draft.bounds.get(axis_id, {}))
    proposed[endpoint] = value
    if "low" in proposed and "high" in proposed and proposed["low"] >= proposed["high"]:
        raise ValueError("safe low limit must be less than safe high limit")
    draft.bounds[axis_id] = proposed
    return {
        "recorded_in_memory": True, "axis": axis_id, "endpoint": endpoint,
        "position_um": value,
        "message": (
            f"Recorded proposed safe {endpoint} limit {value:g} um for {axis_id} in memory only. "
            "This is an operator-chosen safe limit, not a hardware limit."
        ),
    }


def set_proposed_acquisition_prompts(ctrl, guard, *, confirm_above_frames: int,
                                     confirm_above_duration_s: float):
    frames = int(confirm_above_frames)
    duration = float(confirm_above_duration_s)
    if frames <= 0 or not math.isfinite(duration) or duration <= 0:
        raise ValueError("confirmation thresholds must be finite and greater than zero")
    draft = _state(ctrl)
    draft.confirm_above_frames = frames
    draft.confirm_above_duration_s = duration
    return {
        "recorded_in_memory": True,
        "confirm_above_frames": frames,
        "confirm_above_duration_s": duration,
        "message": "Recorded operator-approved acquisition warning thresholds in memory only.",
    }


def review_security_config(ctrl, guard):
    status = _state(ctrl).status()
    if status["complete"]:
        summary = "Draft complete: every live stage axis has finite safe low and high limits and both thresholds are set."
    else:
        axes = ", ".join(item["axis"] for item in status["missing_axes"]) or "none"
        thresholds = ", ".join(status["missing_thresholds"]) or "none"
        summary = f"Draft incomplete. Missing stage endpoints by axis: {axes}. Missing thresholds: {thresholds}."
    return {**status, "summary": summary, "written_to_disk": False}


SETUP_TOOL_REGISTRY = {
    "list_stage_axes": list_stage_axes,
    "read_stage_positions": read_stage_positions,
    "record_proposed_stage_bound": record_proposed_stage_bound,
    "set_proposed_acquisition_prompts": set_proposed_acquisition_prompts,
    "review_security_config": review_security_config,
}

SETUP_TOOL_SCHEMAS = [
    {"name": "list_stage_axes", "description": "List discovered stage axes and draft completeness. Optionally refresh the read-only rig inventory.", "input_schema": {"type": "object", "properties": {"refresh_inventory": {"type": "boolean"}}, "additionalProperties": False}},
    {"name": "read_stage_positions", "description": "Read current positions for every discovered stage axis without moving hardware.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "record_proposed_stage_bound", "description": "After operator approval, record one finite operator-chosen safe endpoint in the in-memory draft. This is not a hardware limit and does not move hardware.", "input_schema": {"type": "object", "properties": {"axis_id": {"type": "string"}, "endpoint": {"type": "string", "enum": ["low", "high"]}, "position_um": {"type": "number"}}, "required": ["axis_id", "endpoint", "position_um"], "additionalProperties": False}},
    {"name": "set_proposed_acquisition_prompts", "description": "Record operator-approved large-acquisition warning thresholds in memory. Propose 500 frames and 1200 seconds, but let the operator change them.", "input_schema": {"type": "object", "properties": {"confirm_above_frames": {"type": "integer", "minimum": 1}, "confirm_above_duration_s": {"type": "number", "exclusiveMinimum": 0}}, "required": ["confirm_above_frames", "confirm_above_duration_s"], "additionalProperties": False}},
    {"name": "review_security_config", "description": "Render the in-memory draft, completeness, and missing endpoints for operator review. Writes nothing.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
]
