from __future__ import annotations
import ast
import inspect
import hashlib
import itertools
import json
import logging
import math
import queue
import os
import tempfile
import textwrap
import threading
import time
import weakref
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager, ExitStack
from pathlib import Path
from typing import Any, Callable

import numpy as np
import tifffile
from pycromanager import Acquisition, multi_d_acquisition_events
from ndstorage import Dataset

from microclaw.autofocus import (
    MIN_CONTRAST,
    PROPERTY_PROBE_MIN_DWELL_S,
    AutofocusResult,
    FocusProbe,
    coarse_then_fine_plane_count,
    coarse_then_fine_autofocus,
    curve_contrast,
    contrast_threshold,
    image_probe,
    property_probe,
    single_sweep_autofocus,
    sweep_plane_count,
)
from microclaw.controller import (
    MicroscopeController,
    PositionListConflict,
    PositionProjection,
    dataset_stack_files,
    settle_stage_move,
    stage_move_dispatch_failure,
)
from microclaw import controller as move_controller
from microclaw.errors import hint_for_error, humanize_java_error
from microclaw.image_analysis import (
    MAX_SATURATED_FRACTION_FOR_COVERAGE,
    ImageStats,
    compute_stats,
    detect_features,
    focus_invalid_warning,
    image_content,
    snap_to_numpy,
    preview_window_open,
    resolve_min_snr,
    snap_to_numpy_displayed,
    tenengrad,
)
from microclaw.paths import TEXT_SUFFIXES, open_in_editor
from microclaw.safety import SafetyGuard, SafetyViolation
from microclaw.acquisition import AcquisitionLedger, AcquisitionPlan, Reservation, plan_events
from microclaw.calibration import resolve_calibration
from microclaw.dataset_mosaic import MosaicFrameShape, MosaicGeometry, assemble_stage_coordinate_mosaic

logger = logging.getLogger(__name__)

#: Every run argument a hook -- or, under 55b, a plan-only coordinator -- must
#: be present to carry. Canonical on purpose: parameterized tests drive every
#: name through every refusal path, so a capability added here but not at a
#: refusal site is a loud failure rather than another silent hole.
#: Add a new capability HERE FIRST. The matrix iterates this tuple, so a
#: capability that never lands here generates no case at all and is silently
#: unguarded -- the one hole the tests cannot close for you.
HOOK_CAPABILITY_ARGS = (
    "illumination_envelope", "artifact_limits", "named_stage_envelope",
    "property_envelope", "hook_action_plan",
)


def emits(renderer: Callable[[dict[str, Any]], str]):
    """Attach a source renderer to the tool whose call it reproduces."""
    def decorate(fn):
        fn._microclaw_emitter = renderer
        return fn
    return decorate


def emits_nothing(fn):
    """Mark a tool whose recorded call has no hardware-routine effect."""
    fn._microclaw_emits_nothing = True
    return fn


def refuses(reason: str):
    """Attach the architectural reason a tool cannot emit itself."""
    def decorate(fn):
        fn._microclaw_refusal_reason = reason
        return fn
    return decorate


class CannotEmit(RuntimeError):
    """A tool knows that its recorded call has no standalone representation."""


class RecordedParams(dict):
    """Recorded input with the matching append-only tool result attached."""

    def __init__(self, params: dict, result: dict | None = None):
        super().__init__(params)
        self.result = result or {}


def _emit_acquisition(
    shape: dict[str, Any], params: dict[str, Any], default_name: str
) -> str:
    """Render the pycro-manager primitive used by the adjacent acquisition tools."""
    event_args = dict(shape)
    channel = params.get("channel")
    exposure = params.get("exposure_ms")
    if channel:
        from microclaw.authorization import CHANNEL_CONFIG_GROUP
        event_args.update(channel_group=CHANNEL_CONFIG_GROUP, channels=[channel])
        if exposure is not None:
            event_args["channel_exposures_ms"] = [exposure]
    prefix = ""
    if not channel and exposure is not None:
        prefix = f"core.set_exposure({exposure!r})\n"
    return (
        prefix
        + f"events = multi_d_acquisition_events(**{event_args!r})\n"
        + f"with Acquisition(directory=str(_HERE), name={params.get('name', default_name)!r}) as acq:\n"
        + "    acq.acquire(events)"
    )


def _emit_snap_and_analyze(params: RecordedParams) -> str:
    min_snr = params.result.get(
        "min_snr", __import__("microclaw.image_analysis", fromlist=[
            "UNCALIBRATED_MIN_SNR_FALLBACK"
        ]).UNCALIBRATED_MIN_SNR_FALLBACK,
    )
    region = params.get("region")
    if region == "drawn":
        region = (params.result.get("metric_valid_for") or {}).get("region")
        if region is None:
            raise CannotEmit(
                "the recorded drawn-region call has no resolved region"
            )
    crop = ""
    if region is not None:
        x, y, w, h = region
        # The guard travels with the crop for the same reason the autofocus
        # closure carries one: a bare slice truncates silently on a frame the
        # region does not fit, and the standalone script has no validator.
        crop = (
            f"if image.shape[0] < {y + h} or image.shape[1] < {x + w}:\n"
            f"    raise RuntimeError(\n"
            f"        f\"Region {region} does not fit frame \"\n"
            f"        f\"[{{image.shape[1]}}, {{image.shape[0]}}].\"\n"
            f"    )\n"
            f"image = image[{y}:{y + h}, {x}:{x + w}]\n"
        )
    return (
        "image = snap_to_numpy(mm)\n"
        + crop
        + f"stats = compute_stats(image, min_snr={min_snr!r})"
    )


def _emit_autofocus(params: RecordedParams) -> str:
    signature = inspect.signature(run_autofocus)
    method = params.get("method", signature.parameters["method"].default)
    settle = params.get("settle_ms", signature.parameters["settle_ms"].default)
    region = params.get("region", signature.parameters["region"].default)
    probe = params.get("probe", signature.parameters["probe"].default)
    z_min = params.get("z_min_um")
    z_max = params.get("z_max_um")
    if region == "drawn":
        region = params.result.get("region")
        if region is None:
            raise CannotEmit(
                "the recorded drawn-region call has no resolved region"
            )
    z_range = params.get("z_range_um")
    z_step = params["z_step_um"]
    if z_min is None:
        lo_line = f"_autofocus_lo = _autofocus_entry_z - {z_range!r} / 2"
        hi_line = f"_autofocus_hi = _autofocus_entry_z + {z_range!r} / 2"
    else:
        lo_line = f"_autofocus_lo = {z_min!r}"
        hi_line = f"_autofocus_hi = {z_max!r}"
    if probe is not None:
        if isinstance(probe, str):
            probe = json.loads(probe)
        device = probe["device"]
        prop = probe["property"]
        values = probe.get("in_focus_values")
        stop_when_found = probe.get("stop_when_found", values is not None)
        dwell_ms = probe.get("dwell_ms")
        extra_args = (
            f", z_min_um={z_min!r}, z_max_um={z_max!r}, dwell_ms={dwell_ms!r}"
            if z_min is not None or dwell_ms is not None else ""
        )
        return "\n".join([
            "_autofocus_entry_z = float(core.get_position())",
            lo_line,
            hi_line,
            f"_autofocus_in_focus_values = {values!r}",
            "_autofocus_probe = property_probe(",
            f"    core, {device!r}, {prop!r}, {values!r},",
            f"    step_um={z_step!r}, lo_um=_autofocus_lo, hi_um=_autofocus_hi,",
            f"    dwell_s={None if dwell_ms is None else dwell_ms / 1000.0!r},",
            f"    stop_when_found={stop_when_found!r},",
            ")",
            "print('AUTOFOCUS ENVELOPE')",
            "print(f'Sweep Z: {_autofocus_lo} to {_autofocus_hi} um; '",
            f"      f'step: {z_step!r} um; criterion: {{_autofocus_probe.describe}}; '",
            f"      f'in_focus_values: {{_autofocus_in_focus_values!r}}; '",
            f"      'stopping_rule: {'first in-focus plane' if stop_when_found else 'full sweep and band centre'}')",
            "autofocus_result = _run_autofocus_passes("
            f"mm, {z_range!r}, {z_step!r}, {method!r}, {settle!r}, None, "
            f"{device!r}, {prop!r}, {values!r}, {stop_when_found!r}{extra_args})",
            "print('AUTOFOCUS OUTCOME')",
            "print(f'moved: {autofocus_result.moved}; measured final Z: '",
            "      f'{autofocus_result.final_z_um}')",
        ])
    extra_args = (f", z_min_um={z_min!r}, z_max_um={z_max!r}"
                  if z_min is not None else "")
    return "\n".join([
        "_autofocus_entry_z = float(core.get_position())",
        lo_line,
        hi_line,
        f"_autofocus_region = {region!r}",
        "_autofocus_min_contrast = contrast_threshold("
        "_metric_pixel_count(mm, _autofocus_region))",
        "print('AUTOFOCUS ENVELOPE')",
        "print(f'Sweep Z: {_autofocus_lo} to {_autofocus_hi} um; region: '",
        "      f'{_autofocus_region!r}; min_contrast: {_autofocus_min_contrast}')",
        "autofocus_result = _run_autofocus_passes("
        f"mm, {z_range!r}, {z_step!r}, {method!r}, {settle!r}, "
        f"{region!r}{extra_args})",
        "print('AUTOFOCUS OUTCOME')",
        "print(f'moved: {autofocus_result.moved}; measured final Z: '",
        "      f'{autofocus_result.final_z_um}')",
    ])


def _emit_go_to_position(params: RecordedParams) -> str:
    result = params.result
    if "x_um" not in result or "y_um" not in result:
        raise CannotEmit("the recorded result has no resolved XY coordinates")
    lines = [f"core.set_xy_position({result['x_um']!r}, {result['y_um']!r})"]
    if result.get("z_um") is not None:
        lines.append(f"core.set_position({result['z_um']!r})")
    return "\n".join(lines)


def _emit_multiposition(params: RecordedParams) -> str:
    hook = params.get("hook_strategy")
    omitted_hook_comment = ""
    if hook:
        from microclaw.hooks import PRECODED_HOOK_REGISTRY
        if isinstance(hook, list):
            raise CannotEmit(
                "composed hooks: the fixed-plan exporter cannot inline their "
                "adapters and observation logs"
            )
        hook_cls = PRECODED_HOOK_REGISTRY.get(hook) if isinstance(hook, str) else None
        if hook_cls is None:
            raise CannotEmit(
                f"saved or unknown hook ({hook!r}): the fixed-plan exporter cannot "
                "inline its adapter and observation log"
            )
        if not getattr(hook_cls, "_observation_only", False):
            raise CannotEmit(
                f"hooked acquisition ({hook!r}): inlining HookBase would import microclaw safety and hook decisions"
            )
        omitted_hook_comment = (
            f"# OBSERVATION HOOK NOT ATTACHED: {hook!r}; this standalone script "
            "reproduces imaging only and does not reproduce its measurements or hook log.\n"
        )
    positions = params.get("positions")
    if positions is None:
        if params.get("_position_resolution_error"):
            raise CannotEmit(params["_position_resolution_error"])
        recorded_positions = params.result.get("results", [])
        if not recorded_positions or any(
            "x_um" not in item or "y_um" not in item
            for item in recorded_positions
        ):
            raise CannotEmit("the record contains no resolved position coordinates")
        positions = [{
            "name": item.get("name", item.get("position")),
            "x_um": item["x_um"],
            "y_um": item["y_um"],
            **({"z_um": item["z_um"]} if item.get("z_um") is not None else {}),
        } for item in recorded_positions]
    if not positions:
        raise CannotEmit("the record contains no resolved position coordinates")
    if any(position.get("name") is None for position in positions):
        raise CannotEmit("the record contains a resolved position without a label")
    protocol = params["protocol"]
    protocol_params = dict(params.get("protocol_params") or {})
    if hook:
        if protocol == "timelapse":
            if any(position.get("z_um") is None for position in positions):
                raise CannotEmit(
                    "observation-only hooked timelapse has positions without recorded Z"
                )
            shape = {
                "num_time_points": protocol_params["n_frames"],
                "time_interval_s": protocol_params.get("interval_s", 0),
            }
        elif protocol == "zstack":
            shape = {
                "z_start": protocol_params["z_start_um"],
                "z_end": protocol_params["z_end_um"],
                "z_step": protocol_params["z_step_um"],
            }
        else:
            raise CannotEmit(f"unknown recorded hooked protocol {protocol!r}")
        sweeps_z = protocol == "zstack"
        if sweeps_z:
            shape["xy_positions"] = [
                (position["x_um"], position["y_um"]) for position in positions
            ]
        else:
            shape["xyz_positions"] = [
                (position["x_um"], position["y_um"], position["z_um"])
                for position in positions
            ]
        shape["position_labels"] = [position["name"] for position in positions]
        channel = protocol_params.get("channel")
        exposure = protocol_params.get("exposure_ms")
        if channel:
            from microclaw.authorization import CHANNEL_CONFIG_GROUP
            shape.update(channel_group=CHANNEL_CONFIG_GROUP, channels=[channel])
            if exposure is not None:
                shape["channel_exposures_ms"] = [exposure]
        prefix = f"core.set_exposure({exposure!r})\n" if exposure is not None and not channel else ""
        return (
            omitted_hook_comment + prefix
            + f"events = multi_d_acquisition_events(**{shape!r})\n"
            + "with Acquisition(directory=str(_HERE), "
            f"name={params.get('name', 'multipos')!r}) as acq:\n"
            + "    acq.acquire(events)"
        )
    lines = [f"for position in {positions!r}:",
             "    core.set_xy_position(position['x_um'], position['y_um'])",
             "    if position.get('z_um') is not None:",
             "        core.set_position(position['z_um'])"]
    if protocol == "snap":
        min_snr = params.result.get(
            "min_snr", __import__("microclaw.image_analysis", fromlist=[
                "UNCALIBRATED_MIN_SNR_FALLBACK"
            ]).UNCALIBRATED_MIN_SNR_FALLBACK,
        )
        lines.extend([
            "    image = snap_to_numpy(mm)",
            f"    stats = compute_stats(image, min_snr={min_snr!r})",
        ])
        return "\n".join(lines)
    if protocol == "timelapse":
        shape = {
            "num_time_points": protocol_params["n_frames"],
            "time_interval_s": protocol_params["interval_s"],
        }
    elif protocol == "zstack":
        shape = {
            "z_start": protocol_params["z_start_um"],
            "z_end": protocol_params["z_end_um"],
            "z_step": protocol_params["z_step_um"],
        }
    else:
        raise CannotEmit(f"unknown recorded multiposition protocol {protocol!r}")
    event_args = dict(shape)
    channel = protocol_params.get("channel")
    exposure = protocol_params.get("exposure_ms")
    if channel:
        from microclaw.authorization import CHANNEL_CONFIG_GROUP
        event_args.update(channel_group=CHANNEL_CONFIG_GROUP, channels=[channel])
        if exposure is not None:
            event_args["channel_exposures_ms"] = [exposure]
    elif exposure is not None:
        lines.append(f"    core.set_exposure({exposure!r})")
    lines.extend([
        f"    events = multi_d_acquisition_events(**{event_args!r})",
        "    with Acquisition(directory=str(_HERE / position['name']), "
        "name=position['name']) as acq:",
        "        acq.acquire(events)",
    ])
    return "\n".join(lines)


def _emit_tile(params: RecordedParams) -> str:
    center_x = params.get("center_x_um", params.result.get("grid_center_x_um"))
    center_y = params.get("center_y_um", params.result.get("grid_center_y_um"))
    if center_x is None or center_y is None:
        raise CannotEmit("the record contains no resolved tile-grid center")
    positions = []
    for row in range(params["rows"]):
        for col in range(params["cols"]):
            positions.append({
                "name": f"{params.get('name', 'tile')}_r{row}_c{col}",
                "x_um": center_x - (params["cols"] - 1) / 2 * params["step_um"]
                + col * params["step_um"],
                "y_um": center_y - (params["rows"] - 1) / 2 * params["step_um"]
                + row * params["step_um"],
            })
    forwarded = RecordedParams({**params, "positions": positions}, params.result)
    return _emit_multiposition(forwarded)


def _emit_focus_lock(params: RecordedParams) -> str:
    result = params.result
    if "property" not in result or "value" not in result:
        raise CannotEmit("the recorded result has no resolved focus-lock property/value")
    # Two routes, like set_channel's: the EMU map writes a device property, and
    # the generic path calls MMCore's own continuous-focus switch. This emitter
    # knew only the first, so the moment block 56a taught set_focus_lock to work
    # on a non-EMU rig, every session that used it exported a script that died
    # three lines after a correct autofocus -- measured on the Nikon, 56ab gate,
    # 2026-08-23. CLAUDE.md: a new capability is not finished until it can appear
    # in an exported script, and this is the fourth block to learn it.
    if result.get("continuous_focus_device"):
        return f"core.enable_continuous_focus({bool(result['value'])!r})"
    device, separator, prop = result["property"].partition(".")
    if not separator:
        raise CannotEmit("the recorded focus-lock property has no device prefix")
    return f"core.set_property({device!r}, {prop!r}, {result['value']!r})"


def _recorded_tool_calls(records: list[dict]) -> list[tuple[str, RecordedParams]]:
    """Read tool calls from the append-only Anthropic conversation record."""
    results = {}
    for message in records:
        content = message.get("content", []) if isinstance(message, dict) else []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            value = block.get("content")
            if isinstance(value, list):
                value = next((part.get("text") for part in value
                              if isinstance(part, dict) and part.get("type") == "text"), None)
            try:
                parsed = json.loads(value) if isinstance(value, str) else None
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                results[str(block.get("tool_use_id"))] = parsed
    calls = []
    for message in records:
        if message.get("role") != "assistant":
            continue
        content = message.get("content", [])
        for block in content if isinstance(content, list) else []:
            kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if kind != "tool_use":
                continue
            name = block.get("name") if isinstance(block, dict) else block.name
            params = block.get("input", {}) if isinstance(block, dict) else block.input
            block_id = block.get("id") if isinstance(block, dict) else block.id
            recorded_params = RecordedParams(dict(params), results.get(str(block_id)))
            recorded_params["_tool_use_id"] = str(block_id)
            calls.append((str(name), recorded_params))
    return calls


def _recorded_outcome(result: dict | None) -> tuple[str, str] | None:
    """How much of a recorded call completed: ``("nothing"|"partial", reason)``.

    Read structurally, never from prose. Two signals, and they are the two the
    record actually carries:

    - a top-level ``error``, which is how `execute_tool` reports both a refusal
      and a raised exception;
    - per-item ``error`` entries inside a top-level list, which is how a
      part-completed acquisition reports itself (`results`, one entry per
      position). The ``status`` line -- "0/3 positions completed." -- is a
      symptom of that, not the source, and is deliberately not parsed.

    Only the exact key ``error`` counts. ``error_um`` is a *measurement* that a
    successful move reports, and matching it would flag working steps.

    **The two answers are not the same defect, and must not get the same
    treatment** (demo gate rounds 1 and 2, 2026-08-06):

    - ``"nothing"`` -- the session did nothing here, so the faithful thing for
      the script to do is also nothing. The exporter emits a comment and
      *continues*. That is not a reconstruction, it is exact. Refusing here was
      round 2's defect: a rejected call sat mid-session and the raise made the
      acquisition that *did* run unreachable, so the script contributed zero
      acquisitions. Failed calls are ordinary -- the M5 gate session had three --
      so refusing on them makes the export useless on real sessions.
    - ``"partial"`` -- something happened that cannot be faithfully reproduced.
      Replaying the whole step re-images what completed; replaying only what
      worked silently changes the routine, and the record cannot say whether the
      operator wanted the failed item retried or dropped. Both are
      reconstructions, so this keeps the hard refusal, like the offline mosaic
      and adaptive runs.

    ``"nothing"`` is *not* a claim that no hardware moved -- a tool can fail
    after moving a stage. The guarantee is narrower and is the safer of the two
    errors: nothing the session recorded as completed is skipped, and nothing
    that failed is retried.
    """
    if not isinstance(result, dict):
        return None
    if "error" in result:
        return "nothing", f"the recorded call did not succeed: {result['error']}"
    for key, value in result.items():
        if not isinstance(value, list):
            continue
        detail = [
            f"{item.get('position', f'{key}[{index}]')!r}: {item['error']}"
            for index, item in enumerate(value)
            if isinstance(item, dict) and "error" in item
        ]
        if not detail:
            continue
        named = "; ".join(detail[:3]) + (
            f"; and {len(detail) - 3} more" if len(detail) > 3 else ""
        )
        if len(detail) == len(value):
            return "nothing", (
                f"none of the {len(value)} recorded {key} entries completed "
                f"({named})"
            )
        return "partial", (
            f"{len(detail)} of {len(value)} recorded {key} entries did not "
            f"complete ({named}), so replaying this step would not reproduce the "
            "run -- it would re-image what did complete and attempt again what "
            "did not"
        )
    return None


def _position_from_result(name: str, result: dict) -> dict | None:
    """Return a complete position delta, or None when the result is insufficient."""
    if "x_um" not in result or "y_um" not in result:
        return None
    return {
        "name": name, "x_um": result["x_um"], "y_um": result["y_um"],
        **({"z_um": result["z_um"]} if result.get("z_um") is not None else {}),
    }


def _position_snapshot(result: dict, key: str) -> dict[str, dict] | None:
    """Parse a complete, unambiguous position snapshot from a recorded result."""
    positions = result.get(key)
    if not isinstance(positions, list):
        return None
    snapshot: dict[str, dict] = {}
    for item in positions:
        if not isinstance(item, dict):
            return None
        name = item.get("name", item.get("position"))
        position = _position_from_result(name, item) if isinstance(name, str) else None
        if position is None or (name in snapshot and snapshot[name] != position):
            return None
        snapshot[name] = position
    return snapshot


def _resolve_recorded_position_names(
    recorded: list[tuple[str, RecordedParams]],
) -> None:
    """Walk mutable position-list history and couple named runs to known state."""
    # State is partial by design: it holds what the record determines, which may
    # be less than MM's whole list. Resolution is per name, so an unknown name
    # refuses while a known one emits.
    state: dict[str, dict] | None = None
    for name, params in recorded:
        result = params.result
        if name == "get_position_list":
            state = _position_snapshot(result, "positions")
            if result.get("position_list_conflict"):
                state = None
        elif name == "validate_positions":
            state = (_position_snapshot(result, "accepted")
                     if result.get("rejected") == [] else None)
        elif name == "mark_position":
            position_name = params.get("name")
            delta = (_position_from_result(position_name, result)
                     if isinstance(position_name, str) else None)
            if delta is None or "marked" not in str(result.get("status", "")).lower():
                state = None
            else:
                # add_position replaces by label, so a successful mark is
                # authoritative for that name whatever came before it — it can
                # seed state from nothing and can supersede a known value.
                state = {} if state is None else state
                state[position_name] = delta
        elif name == "delete_position":
            status = str(result.get("status", "")).lower()
            if "deleted" in status and isinstance(params.get("name"), str):
                if state is not None:
                    state.pop(params["name"], None)
            elif "cancelled" not in status and result.get("position_list_conflict") is None:
                state = None
        elif name == "clear_position_list":
            status = str(result.get("status", "")).lower()
            if "cleared" in status:
                state = {}
            elif "cancelled" not in status and result.get("position_list_conflict") is None:
                state = None
        elif name in {"load_position_list", "import_mm_positions"}:
            status = str(result.get("status", "")).lower()
            if ("loaded" in status or "imported" in status
                    or ("cancelled" not in status
                        and result.get("position_list_conflict") is None)):
                # Their results omit coordinates, so only a later snapshot or
                # mark can make subsequent named-position resolution trustworthy.
                state = None

        if (name not in {"run_multiposition_acquisition", "run_adaptive_survey"}
                or params.get("positions") is not None):
            continue
        requested = params.get("position_names")
        if not isinstance(requested, list):
            continue
        failure = next((item for item in requested
                        if not isinstance(item, str)
                        or state is None or item not in state), None)
        if failure is not None:
            params["_position_resolution_error"] = (
                f"could not resolve named position {failure!r} unambiguously "
                "from the recorded position-list state"
            )
        else:
            params["positions"] = [dict(state[item]) for item in requested]


def _analysis_source(*, include_autofocus: bool = False) -> str:
    """Return exact source for the pure-numpy analysis used by exported routines."""
    from microclaw import autofocus, image_analysis
    parts = [
        "UNCALIBRATED_MIN_SNR_FALLBACK = "
        f"{image_analysis.UNCALIBRATED_MIN_SNR_FALLBACK!r}\n",
        "MAX_SATURATED_FRACTION_FOR_SNR = "
        f"{image_analysis.MAX_SATURATED_FRACTION_FOR_SNR!r}\n",
        inspect.getsource(image_analysis.ImageStats),
    ]
    # Every helper compute_stats reaches, not a hand-picked list. Block 13 added
    # snr_validity() and the emitted scripts kept passing their own tests while
    # raising NameError on a rig: the two branches were green apart and broken
    # together. test_emitted_analysis_defines_every_name_it_uses is the guard.
    for fn in (
        image_analysis._reshape_pixels, image_analysis.snap_to_numpy,
        image_analysis.snr, image_analysis.tenengrad,
        image_analysis.snr_validity, image_analysis.resolve_min_snr,
        image_analysis.coverage_stats,
        image_analysis.compute_stats,
    ):
        parts.append(inspect.getsource(fn))
    if include_autofocus:
        parts.extend([
            inspect.getsource(autofocus.SweepResult),
            inspect.getsource(autofocus.FocusProbe),
            inspect.getsource(autofocus.AutofocusResult),
            f"MIN_CONTRAST = {autofocus.MIN_CONTRAST!r}\n",
            f"N_REF = {autofocus.N_REF!r}\n",
            f"MIN_BAND_PLANES = {autofocus.MIN_BAND_PLANES!r}\n",
            "PROPERTY_PROBE_MIN_DWELL_S = "
            f"{autofocus.PROPERTY_PROBE_MIN_DWELL_S!r}\n",
            "PROPERTY_PROBE_BAND_DWELL_S = "
            f"{autofocus.PROPERTY_PROBE_BAND_DWELL_S!r}\n",
        ])
        for fn in (
            autofocus.longest_true_run, autofocus._strings, autofocus._band_admit,
            autofocus._stable_read, autofocus.image_probe,
            autofocus.property_probe,
            autofocus.sweep_plane_count, autofocus.coarse_then_fine_plane_count,
            autofocus.curve_contrast, autofocus.contrast_threshold,
            autofocus.sweep_autofocus, autofocus._restore,
            autofocus._refusal,
            autofocus._flat_reason, autofocus._edge_reason,
            autofocus.coarse_then_fine_autofocus,
            autofocus.single_sweep_autofocus,
        ):
            parts.append(inspect.getsource(fn))
        parts.append(inspect.getsource(_metric_pixel_count))
        parts.append(inspect.getsource(_run_autofocus_passes))
    return "\n".join(parts)


def _source_bound_names(source: str) -> set[str]:
    """Names bound by a standalone block, excluding package-local imports."""
    tree = ast.parse(source)
    names = {node.name for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    for statement in tree.body:
        targets = (
            statement.targets if isinstance(statement, ast.Assign)
            else [statement.target] if isinstance(statement, ast.AnnAssign)
            else []
        )
        names.update(node.id for target in targets for node in ast.walk(target)
                     if isinstance(node, ast.Name))
    names.update(
        alias.asname or alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and not ((isinstance(node, ast.ImportFrom)
                  and (node.module or "").startswith("microclaw"))
                 or (isinstance(node, ast.Import)
                     and any(a.name.startswith("microclaw") for a in node.names)))
        for alias in node.names
    )
    return names


def _without_microclaw_imports(source: str, available: set[str]) -> str:
    """Remove redundant package imports without rewriting inspected logic."""
    tree = ast.parse(source)
    removals: list[tuple[int, int]] = []
    required: set[str] = set()
    for node in ast.walk(tree):
        package_import = (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("microclaw")
        ) or (
            isinstance(node, ast.Import)
            and any(alias.name.startswith("microclaw") for alias in node.names)
        )
        if not package_import:
            continue
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*" or (alias.asname and alias.asname != alias.name):
                    raise CannotEmit(
                        "package import cannot be replaced by an inlined symbol: "
                        + (alias.asname or alias.name)
                    )
                required.add(alias.name)
        else:
            required.update(alias.asname or alias.name.split(".")[0]
                            for alias in node.names)
        removals.append((node.lineno, node.end_lineno or node.lineno))
    missing = sorted(required - available)
    if missing:
        raise CannotEmit(
            "package import binds symbol(s) not defined by the standalone script: "
            + ", ".join(missing)
        )
    lines = source.splitlines(keepends=True)
    removed = {line for start, end in removals for line in range(start, end + 1)}
    stripped = "".join(line for number, line in enumerate(lines, 1)
                       if number not in removed)
    try:
        ast.parse(stripped)
        return stripped
    except SyntaxError:
        pass
    # The import was the only statement of its block, so deleting it left the
    # block empty. A saved hook written to be portable is exactly where this
    # appears -- `try: from microclaw... except ImportError: <fallback>` -- and
    # `pass` is the semantically correct residue: the name IS bound at module
    # level in the emitted script, so the import "succeeded" and the fallback
    # must not run. Deleted at module level, kept as `pass` inside a block.
    kept = []
    for number, line in enumerate(lines, 1):
        if number not in removed:
            kept.append(line)
            continue
        if number in {start for start, _end in removals}:
            indent = line[:len(line) - len(line.lstrip())]
            if indent:
                kept.append(f"{indent}pass\n")
    patched = "".join(kept)
    try:
        ast.parse(patched)
    except SyntaxError as exc:
        raise CannotEmit(
            "removing the package import left source that does not parse: "
            f"{exc.msg}"
        ) from exc
    return patched


def _channel_verification_source() -> str:
    """Return exact source for the read-back check the channel executor ran.

    Inlined for the same reason the analysis is: the emitted verification must
    *be* the executor's, not a paraphrase of it. A paraphrase was written first
    and was wrong -- a bare string compare fails a write that succeeded, because
    `_verify_property` compares a Float property numerically and Micro-Manager
    reformats one ("10" reads back "10.0000", measured; design/33 Phase 4). The
    type is established the way the executor establishes it, by asking the core,
    so `_property_type_name`'s bridge-shape handling travels with it.
    """
    from microclaw import authorization

    return "\n".join(inspect.getsource(item) for item in (
        authorization.ChannelPlanError,
        authorization._property_type_name,
        authorization._verify_property,
    ))


def _adaptive_runner_source() -> str:
    """Return the exact adaptive decision machinery used by the live runner."""
    from microclaw import __version__, hook_decisions, hooks

    decision_items = (
        hook_decisions.MoveStage, hook_decisions.AcquireAt,
        hook_decisions.SetExposure, hook_decisions.ContinueSurvey,
        hook_decisions.StopSurvey, hook_decisions.RequestAutofocus,
        hook_decisions.SetIlluminationPower, hook_decisions.MoveNamedStage,
        hook_decisions.SetDeviceProperty,
        hook_decisions.EmitArtifact,
        hook_decisions.DiscardFrame, hook_decisions.HookResult,
    )
    parts = [f"__version__ = {__version__!r}\n", _stage_move_contract_source()]
    parts.extend(inspect.getsource(item) for item in decision_items)
    parts.extend([
        "HookAction = (MoveStage | AcquireAt | SetExposure | ContinueSurvey | "
        "StopSurvey | RequestAutofocus | SetIlluminationPower | MoveNamedStage | SetDeviceProperty | EmitArtifact | "
        "DiscardFrame)\n",
        "_ACTION_TYPES = {cls.__dataclass_fields__['kind'].default: cls for cls in "
        "(MoveStage, AcquireAt, SetExposure, ContinueSurvey, StopSurvey, "
        "RequestAutofocus, SetIlluminationPower, MoveNamedStage, SetDeviceProperty, EmitArtifact, DiscardFrame)}\n",
    ])
    parts.extend([
        inspect.getsource(hook_decisions.parse_action),
        inspect.getsource(hook_decisions.write_hook_artifact),
        inspect.getsource(hook_decisions.DeniedEventQueue),
        inspect.getsource(hook_decisions.UntrustedHookAdapter),
        inspect.getsource(hooks.analysis_observation_record),
        inspect.getsource(hooks.write_analysis_observation),
        inspect.getsource(hooks._frame_index),
        inspect.getsource(hooks.HookBase),
        inspect.getsource(SurveyProgress),
        f"_CANDIDATE_POLL_S = {_CANDIDATE_POLL_S!r}\n",
        inspect.getsource(_note_budget_exhausted),
        inspect.getsource(_survey_event_stream),
    ])
    source = "\n".join(parts)
    available = _source_bound_names(source)
    available.update(_source_bound_names(_analysis_source(include_autofocus=True)))
    available.update({"_finite_number_text", "_verify_property", "authorize_property_write"})
    # inspect.getsource supplies the logic. Only package-import lines are
    # deleted because their names are already inlined into this module.
    return _without_microclaw_imports(source, available)


def _export_guard_source(limits: dict[str, Any]) -> str:
    """Render the acquisition-time motion bounds as a small literal guard."""
    return f'''# These are the limits recorded at export time; editing this dict edits the limits.
# Seed-plan XY/Z events are checked here before acquisition; any additional
# hardware action implemented inside a precoded hook remains that hook's responsibility.
_LIMITS = {limits!r}
class SafetyViolation(Exception):
    pass

def _finite_number_text(value, label):
    try: number = float(value)
    except (TypeError, ValueError): raise SafetyViolation(f"{{label}} must be numeric")
    if not math.isfinite(number): raise SafetyViolation(f"{{label}} must be finite")
    return number

def authorize_property_write(ctrl, device, prop):
    # The recorded live run already passed its authorization map, and the
    # emitted guard below pins the exact approved device/property pair so no
    # other pair is reachable through this standalone action path.
    return None

class _RecordedSafetyGuard:
    def _bounded(self, value, low, high, label):
        value = float(value)
        if not math.isfinite(value):
            raise SafetyViolation(f"{{label}} must be finite")
        if low is not None and value < low:
            raise SafetyViolation(f"{{label}}={{value}} is below recorded minimum {{low}}")
        if high is not None and value > high:
            raise SafetyViolation(f"{{label}}={{value}} exceeds recorded maximum {{high}}")
    def _require_stage_bounds(self, axis):
        low, high = _LIMITS.get(f"{{axis.lower()}}_um") or (None, None)
        if low is None or high is None:
            raise SafetyViolation(f"{{axis}} bounds are incomplete in the recorded stage envelope")
    def check_xy(self, x, y):
        self._require_stage_bounds("X")
        self._require_stage_bounds("Y")
        self._bounded(x, _LIMITS["x_um"][0], _LIMITS["x_um"][1], "X")
        self._bounded(y, _LIMITS["y_um"][0], _LIMITS["y_um"][1], "Y")
    def check_z(self, z):
        self._require_stage_bounds("Z")
        self._bounded(z, _LIMITS["z_um"][0], _LIMITS["z_um"][1], "Z")
    def check_exposure(self, exposure_ms):
        self._bounded(exposure_ms, 0.0, _LIMITS["exposure_ms"][1], "Exposure")
    def check_named_stage(self, device, position_um):
        envelope = globals().get("_NAMED_STAGE_ENVELOPE")
        if envelope is None or device != envelope["device"]:
            raise SafetyViolation(f"Named stage {{device!r}} has no recorded envelope")
        self._bounded(position_um, envelope["min_um"], envelope["max_um"],
                      f"Named stage {{device}}")
    def check_device_property(self, core, device, prop, value, *, approved_envelope=False):
        envelope = globals().get("_PROPERTY_ENVELOPE")
        if envelope is None or device != envelope["device"] or prop != envelope["property"]:
            raise SafetyViolation(f"Property {{device}}.{{prop}} has no recorded envelope")
        if "allowed_values" in envelope:
            if value not in envelope["allowed_values"]:
                raise SafetyViolation("Property value is outside the recorded values")
        else:
            self._bounded(value, envelope["min"], envelope["max"],
                          f"Property {{device}}.{{prop}}")
    def check_illumination(self, core, device, prop, value, **kwargs):
        return None
    @property
    def analysis_min_snr(self):
        return _LIMITS.get("analysis_min_snr")

guard = _RecordedSafetyGuard()
'''


def _next_available_log_path(path: Path) -> str:
    """Keep every standalone run log beside this script without collisions."""
    if not path.exists():
        return str(path)
    for number in itertools.count(2):
        candidate = path.with_name(f"{path.stem}_{number}{path.suffix}")
        if not candidate.exists():
            return str(candidate)


def _portable_log_path_source() -> str:
    return inspect.getsource(_next_available_log_path)


def _adaptive_hook_export(params: RecordedParams) -> tuple[str, str, bool]:
    """Return exact hook source, constructor expression, and saved-hook flag."""
    strategy = params.get("hook_strategy")
    if not strategy and params.get("hook_action_plan") is not None:
        return "", "UntrustedHookAdapter(object(), log_path=_log_path)", True
    if isinstance(strategy, list):
        raise CannotEmit("adaptive hook composition is not supported by the standalone runner")
    if not isinstance(strategy, str) or not strategy:
        raise CannotEmit("the record contains no single hook strategy")
    if strategy in {"mm_plugin_analyzer", "autofocus_mm_plugin"}:
        raise CannotEmit(
            f"hook {strategy!r} requires Micro-Manager plugin capabilities through "
            "the Microclaw controller and has no standalone equivalent"
        )
    params_expr = repr(dict(params.get("hook_params") or {}))
    from microclaw.hooks import PRECODED_HOOK_REGISTRY
    if strategy in PRECODED_HOOK_REGISTRY:
        cls = PRECODED_HOOK_REGISTRY[strategy]
        source = inspect.getsource(cls)
        available = _source_bound_names(_adaptive_runner_source())
        available.update(_source_bound_names(_analysis_source(include_autofocus=True)))
        available.update(_source_bound_names(source))
        source = _without_microclaw_imports(source, available)
        signature = inspect.signature(cls.__init__)
        injected = []
        if "ctrl" in signature.parameters:
            injected.append("'ctrl': mm")
        if "guard" in signature.parameters:
            injected.append("'guard': guard")
        extras = (", " + ", ".join(injected)) if injected else ""
        constructor = (
            f"{cls.__name__}(**{{**{params_expr}, 'log_path': _log_path{extras}}})"
        )
        # The five emittable built-ins share these exact bases/helpers. Analysis
        # and autofocus functions are supplied by the existing inline path.
        return source, constructor, False

    from microclaw.hook_manager import describe_saved_hook
    description = describe_saved_hook(strategy)
    if description.get("error"):
        raise CannotEmit(f"saved hook source is unavailable: {description['error']}")
    reasons = description.get("resolve_refusal", {}).get("reasons", [])
    if reasons:
        raise CannotEmit("saved hook source is not exportable: " + "; ".join(reasons))
    provenance = description["provenance"]
    path = Path(provenance["path"])
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CannotEmit(f"saved hook source is unavailable: {exc}") from exc
    source = (
        f"# Saved hook {strategy!r}; manifest sha256: "
        f"{provenance.get('manifest_sha256')}\n" + source
    )
    available = _source_bound_names(_adaptive_runner_source())
    available.update(_source_bound_names(_analysis_source(include_autofocus=True)))
    available.update(_source_bound_names(source))
    source = _without_microclaw_imports(source, available)
    cls_name = description["class_name"]
    constructor = (
        f"UntrustedHookAdapter({cls_name}(**{params_expr}), log_path=_log_path)"
    )
    return source, constructor, True


def _emitted_acquisition_with_restoration(
    acquisition_lines: list[str], *, restore_hardware: bool
) -> list[str]:
    """Wrap emitted acquisition source with the live runner's restoration rules."""
    if not restore_hardware:
        return acquisition_lines
    return [
        "# Restoration runs once, after the acquisition context exits, on both",
        "# the success and the failure path -- acquire() only submits, and M5",
        "# proved the failure path matters: a serial fault mid-run left the axis",
        "# parked where an emitted 'entry' policy had promised to return it.",
        "_restoration_attempted = {'named-stage': False, 'property': False}",
        "def _restore_hardware():",
        "    failures = []",
        "    for label, method_name, result_name in (",
        "        ('named-stage', 'restore_named_stage', '_named_stage_restoration'),",
        "        ('property', 'restore_property', '_property_restoration'),",
        "    ):",
        "        method = getattr(hook, method_name, None)",
        "        if _restoration_attempted[label] or not callable(method):",
        "            continue",
        "        _restoration_attempted[label] = True",
        "        try:",
        "            setattr(hook, result_name, method())",
        "        except Exception as restore_exc:",
        "            failures.append(f'{label} restoration failed: {restore_exc}')",
        "    return failures",
        "try:",
        *["    " + line for line in acquisition_lines],
        "except Exception as acquisition_exc:",
        "    _restoration_failures = _restore_hardware()",
        "    if _restoration_failures:",
        "        raise RuntimeError(f\"{acquisition_exc}; {'; '.join(_restoration_failures)}\") from acquisition_exc",
        "    raise",
        "_restoration_failures = _restore_hardware()",
        "if _restoration_failures:",
        "    raise RuntimeError('; '.join(_restoration_failures))",
    ]


def _emit_adaptive(params: RecordedParams, kind: str, default_name: str = "adaptive") -> str:
    # default_name is the emitting TOOL's own default, not a constant. It was
    # "adaptive" for both twins, so a hardcoded fallback agreed with them by
    # coincidence; after block 43j folded them into run_timelapse/run_zstack the
    # defaults are "timelapse"/"zstack", and a hooked run that named no dataset
    # would have been emitted as `name='adaptive'` while the same tool's hookless
    # branch emitted the right one. A standalone script that writes a differently
    # named dataset is the reproduce-the-run comparison 43h's gate rests on,
    # broken silently.
    if params.get("illumination_envelope") is not None:
        raise CannotEmit(
            "the authorized illumination envelope depends on rig-configured raw-value "
            "conversions that are not recorded as exportable literals"
        )
    if params.get("artifact_limits") is not None:
        raise CannotEmit(
            "the authorized hook artifact budget is not represented by the "
            "standalone adaptive runner"
        )
    hook_source, constructor, saved = _adaptive_hook_export(params)
    named_stage_envelope = params.get("named_stage_envelope")
    property_envelope = params.get("property_envelope")
    hook_action_plan = params.get("hook_action_plan")
    if kind == "survey" and hook_action_plan is not None:
        raise CannotEmit("run_adaptive_survey rejects hook_action_plan; decisions select events at runtime")
    if (named_stage_envelope is not None or property_envelope is not None or
            hook_action_plan is not None) and not saved:
        raise CannotEmit("hardware envelopes and hook_action_plan apply only to saved hooks")
    autofocus_budget = params.get("autofocus_budget")
    autofocus_sweep_exposures = 0
    autofocus_reexposures = 0
    if autofocus_budget is not None:
        if kind != "survey" or not saved:
            raise CannotEmit("autofocus_budget applies only to a saved adaptive survey hook")
        if autofocus_budget["method"] == "coarse_then_fine":
            autofocus_sweep_exposures = coarse_then_fine_plane_count(
                autofocus_budget["z_range_um"],
                max(autofocus_budget["z_step_um"] * 5, 1.0),
                autofocus_budget["z_step_um"],
            )
        else:
            autofocus_sweep_exposures = sweep_plane_count(
                -autofocus_budget["z_range_um"] / 2,
                autofocus_budget["z_range_um"] / 2,
                autofocus_budget["z_step_um"],
            )
        autofocus_reexposures = (
            autofocus_budget["max_exposures"] // (autofocus_sweep_exposures + 1)
        )
    # A survey with no authorized refocus must emit exactly the script it emitted
    # before this capability existed -- "+ 0" everywhere would be noise in every
    # unrelated export.
    _plus_reexposures = f" + {autofocus_reexposures!r}" if autofocus_reexposures else ""
    # Refuse here rather than at the top of export_session_script: a refusal
    # inside the loop becomes a `# NOT EMITTED` step in an otherwise complete
    # script, which is what every other CannotEmit does. Raising up front threw
    # away the whole export -- every unrelated call included -- over one
    # unemittable step. And no default: an unbounded fallback would emit a
    # script whose header claims recorded limits while checking nothing, which
    # is the defect this refusal exists to prevent.
    limits = params.get("_export_safety_limits")
    if not limits:
        raise CannotEmit(params.get("_export_safety_limits_error")
                         or "the record carries no export-time safety limits")
    recorded_log = params.get("log_path") or params.result.get("log_path")
    log_name = Path(recorded_log).name if recorded_log else None
    common = [
        _export_guard_source(limits), hook_source,
        (f"_log_path = _next_available_log_path(_HERE / {log_name!r})"
         if log_name else "_log_path = None"),
        f"hook = {constructor}",
    ]
    from microclaw.authorization import CHANNEL_CONFIG_GROUP
    channel = params.get("channel")
    shape: dict[str, Any]
    if kind == "zstack":
        shape = {"z_start": params["z_start_um"], "z_end": params["z_end_um"],
                 "z_step": params["z_step_um"]}
        common.extend([
            f"guard.check_z({params['z_start_um']!r})",
            f"guard.check_z({params['z_end_um']!r})",
        ])
    elif kind == "timelapse":
        shape = {"num_time_points": params["n_frames"],
                 "time_interval_s": params["interval_s"]}
    else:
        positions = params.get("positions")
        if positions is None:
            # The run itself recorded the coordinates it resolved, so a name
            # this exporter cannot re-derive is not a dead end. Same
            # result-derived route _emit_multiposition takes at :162-173, and
            # the M5 gate of 2026-08-11 is why it exists: a session that marked
            # and validated its positions still failed name resolution, the
            # whole export came back `emitted_calls: 0`, and the agent hand-wrote
            # an acquisition script to fill the gap -- which is the fabrication
            # path this exporter exists to remove.
            #
            # tiles_planned rounds to 3 dp. That is a nanometre against a stage
            # that steps in tens of nanometres at best, so it is recorded here
            # rather than treated as a reason to refuse.
            planned = params.result.get("tiles_planned")
            if planned and all(
                isinstance(tile, dict) and tile.get("position") is not None
                and tile.get("x_um") is not None and tile.get("y_um") is not None
                for tile in planned
            ):
                positions = [{"name": tile["position"], "x_um": tile["x_um"],
                              "y_um": tile["y_um"]} for tile in planned]
            else:
                raise CannotEmit(params.get(
                    "_position_resolution_error",
                    "the record contains no resolved adaptive survey seed positions",
                ))
        if not positions or any(p.get("name") is None or p.get("x_um") is None
                                or p.get("y_um") is None for p in positions):
            raise CannotEmit("the record contains an incomplete adaptive survey seed position")
        protocol = params.get("protocol")
        pp = dict(params.get("protocol_params") or {})
        acquire_on_hit = params.get("acquire_on_hit")
        if acquire_on_hit is not None:
            if not saved:
                raise CannotEmit(
                    "acquire_on_hit requires a saved/generated hook with typed AcquireAt actions"
                )
            effects = params.result.get("channel_effects") or {}
            search_effect = effects.get("search")
            acquire_effect = effects.get("acquire")
            if not search_effect:
                raise CannotEmit(
                    "the executed search phase has no recorded executable channel effects"
                )
            if not acquire_effect and params.result.get("acquire_phase_ran"):
                raise CannotEmit(
                    "the executed acquire phase has no recorded executable channel effects"
                )
            if not acquire_effect:
                raise CannotEmit(
                    "the unexecuted acquire phase has no recorded intended channel effects"
                )
            common.append(_emit_recorded_channel_effects(
                search_effect, pp.get("channel"), label="search channel"
            ))
        if protocol == "timelapse":
            shape = {"num_time_points": pp["n_frames"],
                     "time_interval_s": pp.get("interval_s", 0)}
        elif protocol == "zstack":
            shape = {"z_start": pp["z_start_um"], "z_end": pp["z_end_um"],
                     "z_step": pp["z_step_um"]}
        else:
            raise CannotEmit(f"unknown recorded adaptive survey protocol {protocol!r}")
        shape["xy_positions"] = [(p["x_um"], p["y_um"]) for p in positions]
        shape["position_labels"] = [p["name"] for p in positions]
        common.extend(
            f"guard.check_xy({p['x_um']!r}, {p['y_um']!r})" for p in positions
        )
        if protocol == "zstack":
            common.extend([
                f"guard.check_z({pp['z_start_um']!r})",
                f"guard.check_z({pp['z_end_um']!r})",
            ])
        if pp.get("exposure_ms") is not None:
            common.append(f"guard.check_exposure({pp['exposure_ms']!r})")
        if pp.get("channel") and acquire_on_hit is None:
            shape["channel_group"] = CHANNEL_CONFIG_GROUP
            shape["channels"] = [pp["channel"]]
            if pp.get("exposure_ms") is not None:
                shape["channel_exposures_ms"] = [pp["exposure_ms"]]
        elif pp.get("exposure_ms") is not None:
            common.append(f"core.set_exposure({pp['exposure_ms']!r})")
        adaptive_configuration = (
            f"hook.configure_adaptive(events=events, candidates=candidates, "
            f"progress=progress, guard=guard, max_events=len(events){_plus_reexposures}, "
            f"acquire_hits=hits, max_hits={acquire_on_hit['max_hits']!r}, "
            "read_z=core.get_position)"
            if acquire_on_hit is not None and saved else
            f"hook.configure_adaptive(events=events, candidates=candidates, "
            f"progress=progress, guard=guard, max_events=len(events){_plus_reexposures})"
        )
        if acquire_on_hit is not None:
            common.append("hits = []")
        common.extend([
            f"events = multi_d_acquisition_events(**{{k: v for k, v in {shape!r}.items() if v is not None}})",
            *( [f"_NAMED_STAGE_ENVELOPE = {named_stage_envelope!r}",
                "print('HOOK HARDWARE CONTROL FOR THIS RUN -- bounds enforced below')",
                "print(f\"Named stage: {_NAMED_STAGE_ENVELOPE['device']}; approved interval {_NAMED_STAGE_ENVELOPE['min_um']}-{_NAMED_STAGE_ENVELOPE['max_um']} um; maximum writes {_NAMED_STAGE_ENVELOPE['max_writes']}; restore {_NAMED_STAGE_ENVELOPE['restore']!r}\")",
                "hook.configure_named_stage(core=core, guard=guard, device=_NAMED_STAGE_ENVELOPE['device'], min_um=float(_NAMED_STAGE_ENVELOPE['min_um']), max_um=float(_NAMED_STAGE_ENVELOPE['max_um']), max_writes=_NAMED_STAGE_ENVELOPE['max_writes'], initial_value=float(core.get_position(_NAMED_STAGE_ENVELOPE['device'])), restore=_NAMED_STAGE_ENVELOPE['restore'], action_plan=None)"]
               if named_stage_envelope is not None else [] ),
            *( [f"_PROPERTY_ENVELOPE = {property_envelope!r}",
                "print('HOOK HARDWARE CONTROL FOR THIS RUN -- bounds enforced below')",
                "_property_bound = (repr(_PROPERTY_ENVELOPE['allowed_values']) if 'allowed_values' in _PROPERTY_ENVELOPE else f\"{_PROPERTY_ENVELOPE['min']}-{_PROPERTY_ENVELOPE['max']}\")",
                "print(f\"Property: {_PROPERTY_ENVELOPE['device']}.{_PROPERTY_ENVELOPE['property']}; approved {_property_bound}; maximum writes {_PROPERTY_ENVELOPE['max_writes']}; restore {_PROPERTY_ENVELOPE['restore']!r}\")",
                "_property_values = tuple(_PROPERTY_ENVELOPE['allowed_values']) if 'allowed_values' in _PROPERTY_ENVELOPE else None",
                "hook.configure_property(ctrl=mm, guard=guard, device=_PROPERTY_ENVELOPE['device'], property=_PROPERTY_ENVELOPE['property'], allowed_values=_property_values, min_value=_PROPERTY_ENVELOPE.get('min'), max_value=_PROPERTY_ENVELOPE.get('max'), max_writes=_PROPERTY_ENVELOPE['max_writes'], initial_value=str(core.get_property(_PROPERTY_ENVELOPE['device'], _PROPERTY_ENVELOPE['property'])), restore=_PROPERTY_ENVELOPE['restore'], action_plan=None)"]
               if property_envelope is not None else [] ),
            "candidates = queue.Queue()",
            "progress = SurveyProgress(len(events))",
            *( (["# Standalone scripts cannot query focus-lock state; disengage the lock before running.", f"hook.configure_autofocus(ctrl=mm, guard=guard, focus_lock_check=None, **{autofocus_budget!r}, sweep_exposures={autofocus_sweep_exposures!r})"] if autofocus_budget is not None else []) if saved else [] ),
            *( [adaptive_configuration] if saved else ["hook.survey_events = events", "hook.candidates = candidates", "hook.progress = progress"] ),
            f"event_source = _survey_event_stream(events, candidates, progress, {params.get('max_idle_s', 60.0)!r}, hook, adaptive=True, max_events=len(events){_plus_reexposures})",
            "_hook_callbacks = {name: callback for name, callback in {"
            "'image_process_fn': getattr(hook, 'image_process_fn', None), "
            "'pre_hardware_hook_fn': getattr(hook, 'pre_hardware_hook_fn', None), "
            "'post_hardware_hook_fn': getattr(hook, 'post_hardware_hook_fn', None)"
            "}.items() if callback is not None}",
            *_emitted_acquisition_with_restoration([
                f"with Acquisition(directory=str(_HERE), name={params.get('name', 'survey')!r}, show_display=True, **_hook_callbacks) as acq:",
                "    acq.acquire(event_source(acq))",
            ], restore_hardware=(named_stage_envelope is not None or
                                 property_envelope is not None)),
        ])
        if acquire_on_hit is not None:
            ap = dict(acquire_on_hit["protocol_params"])
            acquire_shape_lines = []
            if acquire_on_hit["protocol"] == "timelapse":
                acquire_shape_lines = [
                    f"    _shape = {{'num_time_points': {ap['n_frames']!r}, "
                    f"'time_interval_s': {ap.get('interval_s', 0)!r}}}",
                    "    guard.check_z(hit['z_um'])",
                ]
            elif acquire_on_hit["protocol"] == "zstack":
                acquire_shape_lines = [
                    f"    _z_start = hit['z_um'] + {ap['z_offset_start_um']!r}",
                    f"    _z_end = hit['z_um'] + {ap['z_offset_end_um']!r}",
                    "    guard.check_z(_z_start)", "    guard.check_z(_z_end)",
                    f"    _shape = {{'z_start': _z_start, 'z_end': _z_end, "
                    f"'z_step': {ap['z_step_um']!r}}}",
                ]
            else:
                raise CannotEmit("unknown acquire_on_hit protocol")
            common.extend([
                f"if len(hits) > {acquire_on_hit['max_hits']!r}: raise SafetyViolation('acquire hit cap exceeded')",
                *( [f"guard.check_exposure({ap['exposure_ms']!r})"]
                   if ap.get("exposure_ms") is not None else [] ),
                "if hits:\n" + textwrap.indent(_emit_recorded_channel_effects(
                    acquire_effect, acquire_on_hit["channel"], label="acquire channel"
                ), "    "),
                "acquire_events = []",
                "for hit in hits:",
                "    guard.check_xy(hit['x_um'], hit['y_um'])",
                *acquire_shape_lines,
                "    _events = multi_d_acquisition_events(xy_positions=[(hit['x_um'], hit['y_um'])], position_labels=[hit['name']], **_shape)",
                *( ["    for _event in _events: _event['z'] = hit['z_um']"]
                   if acquire_on_hit["protocol"] == "timelapse" else [] ),
                "    acquire_events.extend(_events)",
                *( [f"core.set_exposure({ap['exposure_ms']!r})"]
                   if ap.get("exposure_ms") is not None else [] ),
                "if acquire_events:",
                f"    with Acquisition(directory=str(_HERE), name={(params.get('name', 'survey') + '_acquire')!r}, show_display=True) as acq:",
                "        acq.acquire(acquire_events)",
            ])
        return "\n\n".join(common)
    exposure_ms = params.get("exposure_ms")
    if exposure_ms is not None:
        common.append(f"guard.check_exposure({exposure_ms!r})")
    if channel:
        shape.update(channel_group=CHANNEL_CONFIG_GROUP, channels=[channel])
        if exposure_ms is not None:
            shape["channel_exposures_ms"] = [exposure_ms]
    elif exposure_ms is not None:
        common.append(f"core.set_exposure({exposure_ms!r})")
    # laser_slot verifies the rig's trigger pre-flight; it is not a hardware
    # action and therefore intentionally emits no standalone script line.
    common.extend([
        f"events = multi_d_acquisition_events(**{shape!r})",
        *( [f"_NAMED_STAGE_ENVELOPE = {named_stage_envelope!r}",
            f"hook_action_plan = {hook_action_plan!r}",
            "_axes_plan = {}",
            "for _entry in hook_action_plan:",
            "    _index = _entry['hook_event_index']",
            "    _signature = hook.axes_signature(events[_index])",
            "    if _signature in _axes_plan: raise ValueError(f'generated events have duplicate axes signature {dict(_signature)!r}')",
            "    _axes_plan[_signature] = (_index, tuple(parse_action(action) for action in _entry['actions']))",
            "print('HOOK HARDWARE CONTROL FOR THIS RUN -- bounds enforced below')",
            "print(f\"Named stage: {_NAMED_STAGE_ENVELOPE['device']}; approved interval {_NAMED_STAGE_ENVELOPE['min_um']}-{_NAMED_STAGE_ENVELOPE['max_um']} um; maximum writes {_NAMED_STAGE_ENVELOPE['max_writes']}; restore {_NAMED_STAGE_ENVELOPE['restore']!r}\")",
            f"hook.configure_named_stage(core=core, guard=guard, device={named_stage_envelope['device']!r}, min_um={float(named_stage_envelope['min_um'])!r}, max_um={float(named_stage_envelope['max_um'])!r}, max_writes={named_stage_envelope['max_writes']!r}, initial_value=float(core.get_position({named_stage_envelope['device']!r})), restore={named_stage_envelope['restore']!r}, action_plan=_axes_plan)"]
           if named_stage_envelope is not None else [] ),
        *( [f"_PROPERTY_ENVELOPE = {property_envelope!r}",
            *( [f"hook_action_plan = {hook_action_plan!r}",
                "_axes_plan = {}",
                "for _entry in hook_action_plan:",
                "    _index = _entry['hook_event_index']",
                "    _signature = hook.axes_signature(events[_index])",
                "    if _signature in _axes_plan: raise ValueError(f'generated events have duplicate axes signature {dict(_signature)!r}')",
                "    _axes_plan[_signature] = (_index, tuple(parse_action(action) for action in _entry['actions']))"]
               if named_stage_envelope is None else [] ),
            "print('HOOK HARDWARE CONTROL FOR THIS RUN -- bounds enforced below')",
            "_property_bound = (repr(_PROPERTY_ENVELOPE['allowed_values']) if 'allowed_values' in _PROPERTY_ENVELOPE else f\"{_PROPERTY_ENVELOPE['min']}-{_PROPERTY_ENVELOPE['max']}\")",
            "print(f\"Property: {_PROPERTY_ENVELOPE['device']}.{_PROPERTY_ENVELOPE['property']}; approved {_property_bound}; maximum writes {_PROPERTY_ENVELOPE['max_writes']}; restore {_PROPERTY_ENVELOPE['restore']!r}\")",
            "_property_values = tuple(_PROPERTY_ENVELOPE['allowed_values']) if 'allowed_values' in _PROPERTY_ENVELOPE else None",
            "hook.configure_property(ctrl=mm, guard=guard, device=_PROPERTY_ENVELOPE['device'], property=_PROPERTY_ENVELOPE['property'], allowed_values=_property_values, min_value=_PROPERTY_ENVELOPE.get('min'), max_value=_PROPERTY_ENVELOPE.get('max'), max_writes=_PROPERTY_ENVELOPE['max_writes'], initial_value=str(core.get_property(_PROPERTY_ENVELOPE['device'], _PROPERTY_ENVELOPE['property'])), restore=_PROPERTY_ENVELOPE['restore'], action_plan=_axes_plan)"]
           if property_envelope is not None else [] ),
        "_hook_callbacks = {name: callback for name, callback in {"
        "'image_process_fn': getattr(hook, 'image_process_fn', None), "
        "'pre_hardware_hook_fn': getattr(hook, 'pre_hardware_hook_fn', None), "
        "'post_hardware_hook_fn': getattr(hook, 'post_hardware_hook_fn', None)"
        "}.items() if callback is not None}",
        *_emitted_acquisition_with_restoration([
            f"with Acquisition(directory=str(_HERE), name={params.get('name', default_name)!r}, show_display=True, **_hook_callbacks) as acq:",
            "    acq.acquire(events)",
        ], restore_hardware=(named_stage_envelope is not None or
                             property_envelope is not None)),
        # Print what the acquisition reports, or say it is unknown. The obvious
        # fallback -- _HERE / name -- is a path that usually does NOT exist,
        # because pycro-manager resolves collisions by appending _1, _2. Sending
        # an operator to a plausible wrong directory is worse than telling them
        # the location could not be read.
        "print('Dataset:', getattr(acq, '_dataset_disk_location', None) or "
        "'<location not reported by this acquisition>')",
    ])
    return "\n\n".join(common)


def _emit_zstack(params: RecordedParams) -> str:
    if (params.get("hook_strategy") or
            params.get("hook_action_plan") is not None):
        return _emit_adaptive(params, "zstack", "zstack")
    return _emit_acquisition({
        "z_start": params["z_start_um"], "z_end": params["z_end_um"],
        "z_step": params["z_step_um"],
    }, params, "zstack")


def _emit_timelapse(params: RecordedParams) -> str:
    if (params.get("hook_strategy") or
            params.get("hook_action_plan") is not None):
        return _emit_adaptive(params, "timelapse", "timelapse")
    return _emit_acquisition({
        "num_time_points": params["n_frames"],
        "time_interval_s": params["interval_s"],
    }, params, "timelapse")


def _emit_adaptive_survey(params: RecordedParams) -> str:
    return _emit_adaptive(params, "survey")


def _write_text_output(path: str, text: str, *, overwrite: bool) -> None:
    """Shared resolved-path writer for exported and operator-authored text."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"
    try:
        with target.open(mode, encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError:
        raise FileExistsError(
            f"Refusing to overwrite: file already exists: {target}"
        ) from None


@emits_nothing
def get_current_datetime(
    ctrl: MicroscopeController,
    guard: SafetyGuard | None,
) -> dict:
    """Report the machine's current local date and time.

    The model has no clock of its own, so anything date-stamped — a dataset
    name, a folder, a note in the knowledge base — otherwise gets a guessed
    date. `compact` matches the stamp the history files already use, so it is
    the one to reach for when naming a directory.
    """
    now = datetime.now().astimezone()
    return {
        "local_iso": now.isoformat(timespec="seconds"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "compact": now.strftime("%Y%m%d_%H%M%S"),
        "timezone": now.tzname(),
        "utc_offset": now.strftime("%z"),
        "utc_iso": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
    }


@emits_nothing
def write_text_file(
    ctrl: MicroscopeController,
    guard: SafetyGuard | None,
    path: str,
    text: str,
) -> dict:
    """Write operator-requested text through the normal confirmed path boundary."""
    resolved = guard.resolve_in_workspace(path)
    _write_text_output(resolved, text, overwrite=False)
    return {
        "status": "Text file written.",
        "output_path": str(resolved),
        "artifact": {
            "kind": "python" if Path(resolved).suffix.lower() == ".py" else "text",
            "path": str(resolved),
        },
    }


@emits_nothing
def export_session_script(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    output_path: str,
    records: list[dict],
    tool_use_ids: list[str] | None = None,
) -> dict:
    """Compile recorded calls to a standalone pycro-manager script."""
    path = guard.resolve_in_workspace(output_path)
    recorded = _recorded_tool_calls(records)
    _resolve_recorded_position_names(recorded)
    known_ids = {params["_tool_use_id"] for _, params in recorded}
    selected_ids = set(tool_use_ids) if tool_use_ids is not None else None
    unknown_ids = sorted((selected_ids or set()) - known_ids)
    if unknown_ids:
        raise ValueError("unknown tool_use id(s): " + ", ".join(unknown_ids))
    included = [
        (name, params) for name, params in recorded
        if selected_ids is None or params["_tool_use_id"] in selected_ids
    ]
    adaptive_used = any(
        name.startswith("run_adaptive_") or (
            name in {"run_timelapse", "run_zstack"} and (
                params.get("hook_strategy") or
                params.get("hook_action_plan") is not None
            )
        )
        for name, params in included
    )
    analysis_used = adaptive_used or any(
        name in {"snap_and_analyze", "run_autofocus"}
        or (name in {"run_multiposition_acquisition", "run_tile_acquisition"}
            and params.get("protocol") == "snap")
        for name, params in included
    )
    autofocus_used = adaptive_used or any(
        name == "run_autofocus" or params.get("hook_strategy") == "autofocus_per_position"
        for name, params in included
    )
    safety_limits = safety_limits_error = None
    if adaptive_used:
        # Read strictly: a renamed field must not degrade to "no limits", which
        # would emit a script whose header claims recorded bounds while its seed
        # check accepts anything. Carried to the renderer as a reason rather than
        # raised here, so one unemittable step refuses on its own line.
        try:
            constraints = guard._c
            stage = constraints.stage
            camera = constraints.camera
            safety_limits = {
                "x_um": (stage.x_min, stage.x_max),
                "y_um": (stage.y_min, stage.y_max),
                "z_um": (stage.z_min, stage.z_max),
                "exposure_ms": (0.0, camera.max_exposure_ms),
                "analysis_min_snr": guard.analysis_min_snr,
            }
            for axis in ("x", "y", "z"):
                pair = safety_limits[f"{axis}_um"]
                if any(edge is None or not math.isfinite(float(edge)) for edge in pair):
                    safety_limits = None
                    safety_limits_error = (
                        f"recorded stage bounds are incomplete for {axis.upper()}; "
                        f"both {axis}_min and {axis}_max are required"
                    )
                    break
        except (AttributeError, TypeError, ValueError) as exc:
            safety_limits = None
            safety_limits_error = (
                "adaptive export safety constraints are unavailable or have an "
                f"unsupported shape: {exc}"
            )
    for name, params in recorded:
        if name.startswith("run_adaptive_") or (
            name in {"run_timelapse", "run_zstack"} and (
                params.get("hook_strategy") or
                params.get("hook_action_plan") is not None
            )
        ):
            params["_export_safety_limits"] = safety_limits
            params["_export_safety_limits_error"] = safety_limits_error
    # Every one of these is reached only by the adaptive block: hashlib/io/json
    # by the hook artifact and log writers, queue by the candidate stream,
    # threading by SurveyProgress, asdict and
    # datetime by the decision dataclasses and the observation envelope, logging
    # by _note_budget_exhausted. Conditional for the same reason the analysis and
    # channel blocks are: a snap-only session was carrying ten unused imports and
    # an unused logger into the script the operator keeps (demo gate, 2026-08-10).
    adaptive_imports = [
        "import hashlib", "import io", "import itertools", "import json", "import logging",
        "import queue", "import threading",
        "from datetime import datetime, timezone",
    ] if adaptive_used else []
    selection_warning = (
        "Hardware state is order- and history-dependent: excluded setup calls "
        "such as channel, ROI, and stage changes may be required by kept "
        "acquisitions. Dependencies were not inferred; review every SKIPPED step."
    )
    body_lines: list[str] = []
    emitted = 0
    emitted_ids: list[str] = []

    def one_line(reason: object) -> str:
        """Fold a recorded message onto one line so it stays inside its comment.

        A Micro-Manager bridge exception carries a multi-line Java stack trace.
        Interpolated raw, only its first line got the `#` and every frame after
        it was emitted as bare Python -- a SyntaxError that refused the *whole*
        session's export, not just the failed step. Measured on M5 2026-08-17,
        where a serial timeout on `Thorlabs ELL17/ELL20` made the session
        unexportable and the agent hand-wrote a script instead, which is the
        exact failure design/52 exists to remove. The paired `raise` below was
        never affected: it interpolates with `!r`, which escapes the newlines.
        """
        return " ".join(str(reason).split())

    def refuse(tool: str, reason: str) -> None:
        """One shape for every refusal: a comment, then a step that cannot run."""
        body_lines.append(f"# NOT EMITTED: {tool} — {one_line(reason)}")
        body_lines.append(f"raise RuntimeError({('NOT EMITTED: ' + tool + ' — ' + reason)!r})")

    for name, params in recorded:
        if name == "export_session_script":
            continue
        fn = TOOL_REGISTRY.get(name)
        renderer = getattr(fn, "_microclaw_emitter", None)
        body_lines.append("")
        body_lines.append(f"# RECORDED TOOL: {name}")
        if selected_ids is not None and params["_tool_use_id"] not in selected_ids:
            body_lines.append(
                f"# SKIPPED: {name} — excluded by tool_use id selection "
                f"({params['_tool_use_id']})"
            )
            continue
        if fn is not None and getattr(fn, "_microclaw_emits_nothing", False):
            body_lines.append("# No hardware-routine effect.")
            continue
        if renderer is None:
            refuse(name, getattr(
                fn, "_microclaw_refusal_reason",
                "no standalone emitter has been implemented for this tool",
            ))
            continue
        # Checked after the tool-level refusals so those keep their own wording,
        # and before the renderer so a call that did not succeed can never be
        # rendered as one that did. A call that completed *nothing* is skipped
        # and the script carries on: doing nothing is the exact reproduction of
        # a step that did nothing, and halting there would strand every later
        # step that really ran.
        outcome = _recorded_outcome(params.result)
        if outcome is not None:
            completed, reason = outcome
            if completed == "partial":
                refuse(name, reason)
            else:
                body_lines.append(f"# SKIPPED: {name} — {one_line(reason)}")
                body_lines.append(
                    "# The session completed nothing here, so neither does this script."
                )
            continue
        try:
            rendered = renderer(params)
        except CannotEmit as exc:
            refuse(name, str(exc))
            continue
        body_lines.extend(rendered.splitlines())
        emitted += 1
        emitted_ids.append(params["_tool_use_id"])
    body_text = "\n".join(body_lines)
    # Inline helpers based on the program the emitters actually produced. This
    # keeps nested/composite emitters from having to duplicate a tool-name or
    # recorded-result predicate here when they start using a shared helper.
    channel_writes = adaptive_used or "_verify_property(" in body_text
    # The adaptive runner source already carries the settlement contract, so
    # gate on its absence to keep exactly one definition in every script.
    stage_moves = not adaptive_used and (
        autofocus_used or "settle_stage_move(" in body_text
        or "stage_move_dispatch_failure(" in body_text
    )
    lines = [
        "from __future__ import annotations",
        *([f"# WARNING: {selection_warning}"] if selected_ids is not None else []),
        "import math",
        "import time",
        *adaptive_imports,
        f"from dataclasses import {'asdict, dataclass, field' if adaptive_used else 'dataclass, field'}",
        "from pathlib import Path",
        "from types import SimpleNamespace",
        "from typing import Any, Callable, NamedTuple, Optional",
        "import numpy as np",
        "from pycromanager import Acquisition, Core, multi_d_acquisition_events",
        "",
        *(["", _analysis_source(include_autofocus=autofocus_used).rstrip()]
          if analysis_used else []),
        *(["", _portable_log_path_source().rstrip()] if adaptive_used else []),
        *(["", _adaptive_runner_source().rstrip()] if adaptive_used else []),
        *(["", _stage_move_contract_source().rstrip()] if stage_moves else []),
        *(["", _channel_verification_source().rstrip()] if channel_writes else []),
        "",
        "core = Core()",
        # A property write calls ctrl.refresh_gui(); the stand-in used to be a
        # bare SimpleNamespace, so the emitted script died on its FIRST property
        # write with AttributeError -- measured on M5, 2026-08-17, after the
        # script had compiled and been grepped clean. Reproduce the live
        # behaviour rather than stubbing it: an EMU rig genuinely needs the
        # repaint (design/43b), and it is best-effort and never raises, exactly
        # as MicroscopeController.refresh_gui.
        *(["def _refresh_gui():",
           "    # Best-effort repaint, as MicroscopeController.refresh_gui does:",
           "    # an EMU rig genuinely needs it (design/43b), and a GUI failure",
           "    # must never turn a successful hardware write into a failed one.",
           "    # Imported here rather than at the top so a headless run, or a",
           "    # Micro-Manager without Studio, degrades to a no-op.",
           "    try:",
           "        from pycromanager import Studio",
           "        Studio().app().refresh_gui_from_cache()",
           "    except Exception:",
           "        pass",
           "",
           "mm = SimpleNamespace(core=core, refresh_gui=_refresh_gui)"]
          if adaptive_used else ["mm = SimpleNamespace(core=core)"]),
        *(["logger = logging.getLogger(__name__)"] if adaptive_used else []),
        *body_lines,
    ]
    if any("_HERE" in line for line in lines):
        lines.insert(lines.index("core = Core()"),
                     "_HERE = Path(__file__).resolve().parent")
    source = "\n".join(lines) + "\n"
    # Never hand over a file that cannot be parsed. Every refusal in this
    # exporter is a comment plus a loud raise *inside* valid Python, so a
    # SyntaxError means an emitter produced something malformed -- and the
    # operator would only find out when they ran it, which is the failure this
    # whole block exists to remove. Cheap, and it guards every emitter at once
    # rather than the one that happened to break (2026-08-10).
    try:
        ast.parse(source)
    except SyntaxError as exc:
        raise CannotEmit(
            f"the exporter produced source that does not parse at line "
            f"{exc.lineno}: {exc.msg}. This is an emitter defect; no script was "
            "written."
        ) from exc
    _write_text_output(path, source, overwrite=True)
    result = {
        "status": "Session script exported.",
        "output_path": str(path),
        "emitted_calls": emitted,
        "emitted_tool_use_ids": emitted_ids,
        "recorded_calls": [
            {"tool_use_id": params["_tool_use_id"], "tool": name}
            for name, params in recorded if name != "export_session_script"
        ],
        "artifact": {"kind": "python", "path": str(path)},
    }
    if selected_ids is not None:
        result["selection_warning"] = selection_warning
    return result


class SessionGrants:
    """Narrow confirmation subjects approved for this process lifetime only.

    A kind is not itself a question.  In particular, an illumination-enable
    grant must not also authorize Core.Shutter retargeting or an unattended
    hook power envelope, and an acquisition-threshold grant must not authorize
    MMStudio's opaque current MDA.  Keep those call sites subject-less so they
    remain one-shot confirmations.

    The enable subject cannot infer the operator's natural-language intent. If
    an agent enables a source while trying to turn it off, a matching grant will
    approve that write; the distinguishable audit row is the only backstop.
    Grants therefore remove repeated decisions, not any SafetyGuard check or
    refusal, and are deliberately process memory rather than persisted state.
    The terminal's ``grants`` command can revoke only between turns; browser
    operators can revoke while a turn is running.
    """

    GRANTABLE = frozenset({"illumination", "acquisition"})
    _SUBJECTS = {
        "illumination": frozenset({"enable"}),
        "acquisition": frozenset({"threshold"}),
    }

    def __init__(self) -> None:
        self._granted: dict[tuple[str, str], dict[str, str]] = {}

    def granted(self, kind: str, subject: str | None) -> dict[str, str] | None:
        if subject is None:
            return None
        return self._granted.get((kind, subject))

    @classmethod
    def is_grantable(cls, kind: str, subject: str | None) -> bool:
        """Whether this exact confirmation question may receive a grant."""
        return subject in cls._SUBJECTS.get(kind, ())

    def grant(
        self, kind: str, subject: str | None, summary: str, identity: str
    ) -> dict[str, str]:
        if kind not in self.GRANTABLE:
            raise ValueError(
                f"{kind!r} confirmations cannot be granted for a session; "
                "they gate self-modification, not workflow."
            )
        if not self.is_grantable(kind, subject):
            raise ValueError(
                f"{kind!r} confirmation subject {subject!r} is not session-grantable."
            )
        record = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "subject": subject,
            "identity": identity,
            "granted_at": datetime.now(timezone.utc).isoformat(),
            "granted_on": summary,
        }
        self._granted[(kind, subject)] = record
        return record

    def revoke(self, grant_id: str) -> dict[str, str] | None:
        for key, record in tuple(self._granted.items()):
            if record["id"] == grant_id:
                del self._granted[key]
                return record
        return None

    def active(self) -> list[dict[str, str]]:
        return list(self._granted.values())

    def clear(self) -> None:
        self._granted.clear()


SESSION_GRANTS = SessionGrants()
# The CLI installs its confirmations JSONL append method here.  The browser has
# an identity-aware audit path in Session.confirm, so it leaves this unset.
CONFIRM_AUDIT_FN: Callable[[dict[str, str]], Any] | None = None


def _stdin_decision_record(
    summary: str, kind: str, subject: str | None, decision: str,
    *, grant_id: str | None = None,
) -> None:
    if CONFIRM_AUDIT_FN is None:
        return
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "identity": "stdin",
        "confirmation_id": uuid.uuid4().hex,
        "kind": kind,
        "decision": decision,
        "summary": summary,
    }
    if subject is not None:
        record["subject"] = subject
    if grant_id is not None:
        record["grant_id"] = grant_id
    CONFIRM_AUDIT_FN(record)


def _require_confirmation(
    summary: str, kind: str = "action", subject: str | None = None
) -> bool:
    """Blocking stdin confirmation for actions that persist model-writable content.

    Prints the exact action and requires an explicit yes, unless the exact
    ``kind``/``subject`` pair has a session grant.
    """
    grant = SESSION_GRANTS.granted(kind, subject)
    if grant is not None:
        print(f"\n[microclaw] Auto-approved under session grant {grant['id']}:\n{summary}")
        _stdin_decision_record(
            summary, kind, subject, f"auto-approved:{grant['id']}",
            grant_id=grant["id"],
        )
        return True
    print(f"\n[microclaw] Confirmation required:\n{summary}")
    grantable = SessionGrants.is_grantable(kind, subject)
    prompt = "Proceed? [y/N, or s for this session] " if grantable else "Proceed? [y/N] "
    answer = input(prompt).strip().lower()
    if answer in {"s", "session"} and grantable:
        grant = SESSION_GRANTS.grant(kind, subject, summary, identity="stdin")
        _stdin_decision_record(
            summary, kind, subject, f"approved:session:{grant['id']}",
            grant_id=grant["id"],
        )
        return True
    approved = answer in {"y", "yes"}
    _stdin_decision_record(summary, kind, subject, "approved" if approved else "declined")
    return approved


# The confirmation gate lives in code (not just the system prompt) so a
# self-modification (save_knowledge, hook save) can't happen without a human
# yes. Injectable so tests can stub it and a non-CLI frontend can supply its
# own — `microclaw serve` installs Session.confirm here so the gate reaches the
# browser the operator is actually looking at (design/21 F1). Every callsite
# must read this module global at call time; importing it by value into another
# module would silently disconnect the browser gate.
CONFIRM_FN = _require_confirmation
_ACQUISITION_LEDGERS: "weakref.WeakKeyDictionary[Any, AcquisitionLedger]" = (
    weakref.WeakKeyDictionary()
)


def _acquisition_entry_point(fn):
    """Mark a public tool whose effects must pass the dose planner/ledger."""
    fn._microclaw_acquisition_entry_point = True
    return fn


class _HookArtifactBudgetError(ValueError):
    """A hook can emit artifacts but the run authorized no artifact budget.

    Its own type so the acquisition entry points can convert *this* refusal to a
    result dict without also swallowing the malformed-argument ValueErrors
    _configure_hook_capabilities raises, which must keep propagating so the tool
    wrapper can attach its "re-read the schema" hint.
    """


class _HookedAcquisitionFailure(RuntimeError):
    """A hook failed after pycro-manager resolved the dataset directory."""

    def __init__(self, error: Exception, dataset_path: str,
                 *, frames_exposed: int | None = None,
                 last_hardware_state: dict[str, Any] | None = None) -> None:
        super().__init__(str(error))
        self.dataset_path = dataset_path
        self.frames_exposed = frames_exposed
        self.last_hardware_state = last_hardware_state


def _hooked_failure_result(exc: _HookedAcquisitionFailure, log_path: str | None) -> dict:
    """Report a mid-acquisition hook failure without hiding what was written.

    design/38 F7: session A lost a complete dataset because the failure result
    named no path. The agent guessed `<save_dir>/<name>`, missed the collision
    suffix pycro-manager had already applied (`plus_A` vs `plus_A_1`), and swept
    a whole drive looking for it.

    Returning a dict rather than raising means the tool wrapper's
    `hint_for_error` never runs, so the "already exposed" warning it would have
    added is carried here instead. Dropping it would trade one silent failure
    for another.
    """
    result = {
        "error": str(exc),
        "dataset_path": exc.dataset_path,
        "artifact": {"kind": "dataset", "path": exc.dataset_path},
        "hint": (
            "The hook raised mid-acquisition. The stage has already moved and "
            "the frames acquired before the failure are saved at dataset_path — "
            "read what is there before re-acquiring, and do not treat the run as "
            "untouched."
        ),
    }
    if log_path:
        result["log_path"] = log_path
    if exc.frames_exposed is not None:
        result["frames_exposed"] = exc.frames_exposed
    if exc.last_hardware_state is not None:
        result["last_hardware_state"] = exc.last_hardware_state
    return result


def _acquisition_ledger(ctrl) -> AcquisitionLedger:
    try:
        ledger = _ACQUISITION_LEDGERS.get(ctrl)
        if ledger is None:
            ledger = AcquisitionLedger()
            _ACQUISITION_LEDGERS[ctrl] = ledger
        return ledger
    except TypeError:
        # Some controller test doubles are not weak-referenceable.
        ledger = getattr(ctrl, "_microclaw_acquisition_ledger", None)
        if not isinstance(ledger, AcquisitionLedger):
            ledger = AcquisitionLedger()
            setattr(ctrl, "_microclaw_acquisition_ledger", ledger)
        return ledger


def _existing_acquisition_ledger(ctrl) -> AcquisitionLedger | None:
    """Read the production weak-map or test-double fallback without creating one."""
    try:
        return _ACQUISITION_LEDGERS.get(ctrl)
    except TypeError:
        ledger = getattr(ctrl, "_microclaw_acquisition_ledger", None)
        return ledger if isinstance(ledger, AcquisitionLedger) else None


def _format_duration(seconds: float) -> str:
    """Render an estimated duration the way an operator reads a clock.

    Every duration here is an estimate, so every rendering says "about" —
    phrasing that switched on whether the number divided evenly read as though
    a round 20 minutes were the more certain figure.

    Precision is trimmed for the same reason. `%g` carries six significant
    figures, so M5's 21-minute plan asked "about 21.0008 minutes" (block 48e
    acceptance run, 2026-08-14): six digits of precision on a number introduced
    as approximate, in the one sentence an operator reads before committing
    twenty minutes of rig time.
    """
    value, unit = (seconds, "second") if seconds < 60 else (seconds / 60, "minute")
    value = round(value, 1)
    if value == int(value):
        value = int(value)
    return f"about {value} {unit}{'' if value == 1 else 's'}"


def _authorize_acquisition(
    ctrl, guard: SafetyGuard, plan: AcquisitionPlan, *, confirm: bool = True
) -> Reservation:
    reservation = _acquisition_ledger(ctrl).reserve(guard, plan)
    c = guard.acquisition_confirmation_thresholds
    reasons = [
        reason for reason, value, threshold in (
            (f"{plan.frames} frames", plan.frames, c.confirm_above_frames),
            (_format_duration(plan.estimated_duration_s), plan.estimated_duration_s,
             c.confirm_above_duration_s),
            (f"{plan.illuminated_ms:g} ms illuminated time", plan.illuminated_ms,
             c.confirm_above_illuminated_ms),
        ) if threshold is not None and value >= threshold
    ]
    if confirm and reasons and not CONFIRM_FN(
        "This acquisition will take " + " and ".join(reasons)
        + ". It may use substantial disk space or time. Continue?",
        kind="acquisition",
        subject="threshold",
    ):
        reservation.close()
        # Name that this was a human confirmation decline, not a limit refusal.
        # The two are otherwise indistinguishable to the agent and the audit
        # log — an M5 operator's intentional decline surfaced as a bare
        # "declined" the agent could not attribute (design/32 Block 4 G3).
        raise SafetyViolation(
            "Acquisition plan declined at confirmation "
            "(operator refused the reserved plan)."
        )
    return reservation


def _reservation_report(reservation: Reservation) -> dict[str, Any]:
    """Public result fields for accounting findings; empty on an exact run."""
    if not reservation.has_overrun:
        return {}
    return {
        "budget_overrun": True,
        "overrun_frames": reservation.overrun_frames,
        "frames_reserved": reservation.plan.frames,
        "frames_accounted": reservation.completed_frames,
    }


def _note_budget_exhausted(
    hook: Any | None, max_events: int, *, overrun_frames: int = 0
) -> None:
    """Report loudly without allowing reporting failures onto engine threads."""
    try:
        note = getattr(hook, "note_budget_exhausted", None)
        if callable(note):
            note(max_events, overrun_frames=overrun_frames)
            return
    except Exception:
        logger.exception("Hook failed while recording acquisition budget exhaustion")
    logger.error(
        "Acquisition budget exhausted: max_events=%s, overrun_frames=%s",
        max_events, overrun_frames,
    )


def _str_vector(sv) -> list[str]:
    """Convert a pycro-manager mmcorej_StrVector (or plain iterable) to a Python list."""
    if hasattr(sv, "size"):
        return [str(sv.get(i)) for i in range(sv.size())]
    return [str(x) for x in sv]


def _wait(ctrl: MicroscopeController, device: str | None = None) -> None:
    if device:
        ctrl.core.wait_for_device(device)
    else:
        ctrl.core.wait_for_system()


# --- Camera ---

@contextmanager
def _pause_live(ctrl: MicroscopeController, *, restore: bool = True):
    """Stop live mode for a camera op; restore it only if this was a borrow.

    core.snap_image() throws "sequence acquisition is running" if live mode is
    on, and studio.live().snap(True) is worse — it never returns and wedges the
    single-lock ZMQ bridge (design/14 V1). Every snap path must run inside
    this. Use restore=False for frame-producing runs: on a rig where the camera
    trigger fires the lasers, restoring live after a run keeps exposing the
    sample. Yields a mutable observation record. Restore success is checked
    against CMMCore's actual camera sequence, not MM Studio's live-mode flag.
    """
    live = ctrl.studio.live()
    was_on = bool(live.is_live_mode_on())
    if was_on:
        live.set_live_mode_on(False)
    state: dict[str, Any] = {
        "was_on": was_on,
        "restored": restore and was_on,
        "restore_observed": None,
    }
    try:
        yield state
    finally:
        if was_on and restore:
            live.set_live_mode_on(True)
            deadline = time.monotonic() + _LIVE_MODE_WAIT_S
            while True:
                try:
                    running = bool(ctrl.core.is_sequence_running())
                except Exception as exc:
                    state["restore_error"] = f"Could not read camera sequence state: {exc}"
                    break
                if running:
                    state["restore_observed"] = True
                    break
                if time.monotonic() >= deadline:
                    state["restore_observed"] = False
                    state["restore_error"] = (
                        "Live-mode flag was restored but CMMCore did not report a "
                        "running camera sequence."
                    )
                    break
                time.sleep(_LIVE_MODE_POLL_S)


def _live_restore_report(state: dict) -> dict | None:
    if not state.get("was_on"):
        return None
    if not state.get("restored"):
        return {
            "requested": False,
            "left_off": True,
            "reason": (
                "Live view was running when this acquisition started and was "
                "left off, so the camera is not exposing the sample after the "
                "run. Restart it with start_live_view if you want it back."
            ),
        }
    return {
        "requested": True,
        "sequence_running": state.get("restore_observed"),
        **({"warning": state["restore_error"]} if state.get("restore_error") else {}),
    }


# MM 2.0.3 updates its nominal live flag synchronously, but a failed start is
# reported only by changing that flag back to false. Allow headroom for bridge
# or MM-version latency while making that silent failure visible to the caller.
_LIVE_MODE_WAIT_S = 2.0
_LIVE_MODE_POLL_S = 0.02


@emits_nothing
def start_live_view(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    live = ctrl.studio.live()
    live.set_live_mode_on(True)
    deadline = time.monotonic() + _LIVE_MODE_WAIT_S
    while not bool(live.is_live_mode_on()):
        if time.monotonic() >= deadline:
            return {
                "error": (
                    "Live view did not start within "
                    f"{_LIVE_MODE_WAIT_S:g} seconds; live mode is not running."
                )
            }
        # pyjavaz serializes bridge calls; yield between probes so this poll
        # cannot monopolize its single communication lock.
        time.sleep(_LIVE_MODE_POLL_S)
    return {"status": "Live view started."}


@emits_nothing
def stop_live_view(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    ctrl.studio.live().set_live_mode_on(False)
    return {"status": "Live view stopped."}


@emits(lambda p: f"core.set_exposure({p['ms']!r})")
def set_exposure(ctrl: MicroscopeController, guard: SafetyGuard, ms: float) -> dict:
    guard.check_exposure(ms)
    ctrl.core.set_exposure(ms)
    return {"status": f"Exposure set to {ms} ms."}


@emits_nothing
def get_exposure(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    ms = ctrl.core.get_exposure()
    return {"exposure_ms": ms}


@emits_nothing
def get_pixel_size(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    um = float(ctrl.core.get_pixel_size_um())
    result: dict = {"pixel_size_um": um}
    if um == 0.0:
        result["warning"] = (
            "Pixel size is 0.0 — no pixel size calibration is configured in "
            "Micro-Manager. Set one up via Tools → Pixel Size Calibration."
        )
    return result


# --- ROI ---

def _bounce_live_if_on(ctrl: MicroscopeController) -> bool:
    """Restart live mode if it was running. Returns True if it was restarted."""
    live = ctrl.studio.live()
    if live.is_live_mode_on():
        live.set_live_mode_on(False)
        live.set_live_mode_on(True)
        return True
    return False


@emits_nothing
def get_roi(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    roi = ctrl.core.get_roi()
    return _with_illuminated_field({
        "x": int(roi.x),
        "y": int(roi.y),
        "width": int(roi.width),
        "height": int(roi.height),
    })


def _with_illuminated_field(result: dict) -> dict:
    """Attach a stored rig crop fact at every ROI decision point."""
    from microclaw.knowledge_manager import load_knowledge
    rig = load_knowledge().get("rig") or {}
    if not isinstance(rig, dict):
        return result
    if "illuminated_field" in rig:
        result["illuminated_field"] = rig["illuminated_field"]
    return result


# The guard and the authorization map are microclaw policy, not hardware effect,
# so neither is emitted: a standalone script carries the write the rig actually
# performed. Geometry the adapter would reject still fails there, on the adapter.
@emits(lambda p:
       f"core.set_roi({p['x']!r}, {p['y']!r}, {p['width']!r}, {p['height']!r})")
def set_roi(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    x: int,
    y: int,
    width: int,
    height: int,
) -> dict:
    from microclaw.authorization import authorize_path
    authorize_path(ctrl, "camera-roi")
    guard.check_roi(x, y, width, height)
    ctrl.core.set_roi(x, y, width, height)
    _wait(ctrl, ctrl.core.get_camera_device())
    live_restarted = _bounce_live_if_on(ctrl)
    result: dict = {"status": "ROI set.", "x": x, "y": y, "width": width, "height": height}
    if live_restarted:
        result["live_view"] = "restarted"
    return _with_illuminated_field(result)


@emits(lambda p: "core.clear_roi()")
def clear_roi(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    from microclaw.authorization import authorize_path
    authorize_path(ctrl, "camera-roi")
    ctrl.core.clear_roi()
    _wait(ctrl, ctrl.core.get_camera_device())
    live_restarted = _bounce_live_if_on(ctrl)
    w = int(ctrl.core.get_image_width())
    h = int(ctrl.core.get_image_height())
    result: dict = {"status": "ROI cleared (full frame).", "width": w, "height": h}
    if live_restarted:
        result["live_view"] = "restarted"
    return _with_illuminated_field(result)


# --- XY Stage ---

def _bounds_violation(check: Callable[..., None], *args: Any) -> str | None:
    """Return the guard's own refusal for a coordinate already read, else None.

    A report, never a gate. Every caller has already read the position and is
    describing it, so this must not turn a read into an error: `get_system_state`
    is what a session calls to orient itself, and the one thing it may never do
    is fail. A guard defect is therefore dropped rather than raised.

    `SafetyViolation` is the answer being asked for, and that deliberately
    includes the one `_finite_number` raises for a non-finite *configured*
    limit — a broken `stage.y_max` is worth surfacing as a bounds problem rather
    than being silently swallowed, which is the failure mode this whole block
    exists to remove.
    """
    try:
        check(*args)
    except SafetyViolation as exc:
        return str(exc)
    except Exception:
        return None
    return None


@emits_nothing
def get_xy_position(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    x = ctrl.core.get_x_position()
    y = ctrl.core.get_y_position()
    result = {"x_um": round(x, 3), "y_um": round(y, 3)}
    violation = _bounds_violation(guard.check_xy, x, y)
    if violation:
        result["out_of_bounds"] = [violation]
    return result


@emits(lambda p: (
    f"core.set_xy_position({p['x_um']!r}, {p['y_um']!r})"
    if p.get("absolute", True) else
    f"core.set_relative_xy_position({p['x_um']!r}, {p['y_um']!r})"
))
def move_stage_xy(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    x_um: float,
    y_um: float,
    absolute: bool = True,
) -> dict:
    if absolute:
        target_x, target_y = x_um, y_um
    else:
        current_x = ctrl.core.get_x_position()
        current_y = ctrl.core.get_y_position()
        target_x = current_x + x_um
        target_y = current_y + y_um

    guard.check_xy(target_x, target_y)

    if absolute:
        ctrl.core.set_xy_position(target_x, target_y)
    else:
        ctrl.core.set_relative_xy_position(x_um, y_um)

    _wait(ctrl, ctrl.core.get_xy_stage_device())
    # Report requested vs achieved: in amr_test a Y move carried a 1.1 µm
    # unrequested X excursion that nothing surfaced (design/14 §8).
    achieved_x = float(ctrl.core.get_x_position())
    achieved_y = float(ctrl.core.get_y_position())
    return {
        "x_um": round(target_x, 3),
        "y_um": round(target_y, 3),
        "achieved_um": [round(achieved_x, 3), round(achieved_y, 3)],
        "error_um": [
            round(achieved_x - target_x, 3),
            round(achieved_y - target_y, 3),
        ],
        "status": "Moved.",
    }


# --- Z Stage ---

@emits_nothing
def get_z_position(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    z = ctrl.core.get_position()
    result = {"z_um": round(z, 3)}
    violation = _bounds_violation(guard.check_z, z)
    if violation:
        result["out_of_bounds"] = [violation]
    return result


def _emit_stage_settle(device_expr: str, target: float) -> str:
    return f"settle_stage_move(core, {device_expr}, {target!r})"


def _emit_stage_dispatch(set_line: str, device_expr: str, target: float) -> str:
    return "\n".join([
        "try:",
        f"    {set_line}",
        "except Exception as _move_exc:",
        f"    raise stage_move_dispatch_failure(core, {device_expr}, {target!r}, _move_exc) from _move_exc",
    ])


def _stage_move_contract_source() -> str:
    constants = "\n".join([
        f"STAGE_MOVE_TOLERANCE_UM = {move_controller.STAGE_MOVE_TOLERANCE_UM!r}",
        f"STAGE_MOVE_TIMEOUT_S = {move_controller.STAGE_MOVE_TIMEOUT_S!r}",
        f"STAGE_MOVE_POLL_S = {move_controller.STAGE_MOVE_POLL_S!r}",
        f"STAGE_MOVE_REQUIRED_SAMPLES = {move_controller.STAGE_MOVE_REQUIRED_SAMPLES!r}",
        f"STAGE_MOVE_STABILITY_WINDOW_S = {move_controller.STAGE_MOVE_STABILITY_WINDOW_S!r}",
    ])
    return "\n".join([
        constants,
        inspect.getsource(move_controller.StageMoveError),
        inspect.getsource(move_controller.stage_move_dispatch_failure),
        inspect.getsource(move_controller.settle_stage_move),
    ])


def _emit_move_stage_z(params: RecordedParams) -> str:
    target = params.result.get("requested_um")
    if target is None and params.get("absolute", True):
        target = params.get("z_um")
    if target is None:
        raise CannotEmit("the focus-stage move recorded no resolved target")
    return "\n".join([
        _emit_stage_dispatch(f"core.set_position({target!r})", "core.get_focus_device()", target),
        _emit_stage_settle("core.get_focus_device()", target),
    ])


@emits(_emit_move_stage_z)
def move_stage_z(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    z_um: float,
    absolute: bool = True,
) -> dict:
    if absolute:
        target_z = z_um
    else:
        current_z = ctrl.core.get_position()
        target_z = current_z + z_um

    guard.check_z(target_z)

    device = ctrl.core.get_focus_device()
    try:
        if absolute:
            ctrl.core.set_position(target_z)
        else:
            ctrl.core.set_relative_position(z_um)
    except Exception as exc:
        raise stage_move_dispatch_failure(ctrl.core, device, target_z, exc) from exc
    return settle_stage_move(ctrl.core, device, target_z)


# --- Named stages (design/14 §6) ---

def _device_type_name(core, label: str) -> str:
    """Classify a device via core.get_device_type(label).

    The ordinal table and the implementation live in
    microclaw.authorization (design/33 needs the same classification to
    auto-classify StateDevices), so there is exactly one copy. Imported lazily
    like the other authorization helpers in this module.

    Deliberately avoids get_loaded_devices_of_type: that needs a DeviceType
    enum value, whose static shadow must go through the JavaClass cache
    workaround and hung once in the design/14 spike (V3). Per-device
    classification needs no JavaClass at all.
    """
    from microclaw.authorization import device_type_name

    return device_type_name(core, label)


@emits_nothing
def list_stages(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """Every stage device, and which ones the core's Z/XY tools actually drive.

    move_stage_z/get_z_position address ONLY core.get_focus_device(). In the
    amr_test session the TIRF beam-steering axis (a second single-axis stage)
    was unreachable and the task was handed back to the human (design/14 §6).
    """
    focus = str(ctrl.core.get_focus_device())
    xy = str(ctrl.core.get_xy_stage_device())
    single, xy_stages = [], []
    for label in _str_vector(ctrl.core.get_loaded_devices()):
        kind = _device_type_name(ctrl.core, label)
        if kind in ("StageDevice", "5"):
            single.append(label)
        elif kind in ("XYStageDevice", "6"):
            xy_stages.append(label)
    return {
        "focus_device": focus,
        "xy_device": xy,
        "single_axis_stages": single,
        "xy_stages": xy_stages,
        "other_single_axis": [d for d in single if d != focus],
        "note": (
            "move_stage_z targets focus_device only; use move_named_stage "
            "(with a named_stages safety entry) for the rest."
        ),
    }


@emits_nothing
def get_stage_position(
    ctrl: MicroscopeController, guard: SafetyGuard, device: str
) -> dict:
    return {
        "device": device,
        "position_um": round(float(ctrl.core.get_position(device)), 4),
    }


def _emit_move_named_stage(params: RecordedParams) -> str:
    """Emit the absolute move that actually ran, never the argument given.

    `requested_um` is the *resolved* target: a relative call resolves against
    the live position before writing, so emitting the raw `um` would resolve it
    a second time against wherever the standalone script's stage happens to sit
    and land somewhere else entirely.
    """
    result = params.result
    if "device" not in result or "requested_um" not in result:
        raise CannotEmit("the named-stage move recorded no resolved target")
    return "\n".join([
        _emit_stage_dispatch(
            f"core.set_position({result['device']!r}, {result['requested_um']!r})",
            repr(result["device"]), result["requested_um"],
        ),
        _emit_stage_settle(repr(result["device"]), result["requested_um"]),
    ])


@emits(_emit_move_named_stage)
def move_named_stage(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    device: str,
    um: float,
    absolute: bool = True,
) -> dict:
    """Move a single-axis stage addressed by label, guarded by the PER-DEVICE
    limits table (named_stages in the safety config — fail-closed)."""
    current = float(ctrl.core.get_position(device))
    target = um if absolute else current + um
    guard.check_named_stage(device, target)
    try:
        ctrl.core.set_position(device, target)
    except Exception as exc:
        raise stage_move_dispatch_failure(ctrl.core, device, target, exc) from exc
    result = settle_stage_move(ctrl.core, device, target)
    return {"device": device, **result}


# --- Channel / Config ---

def _has_channel_authorization_map(ctrl: MicroscopeController) -> bool:
    """Whether this session can use the capture/authorize/replay executor.

    A legacy session with no property_authorization has no authorization map. It keeps MM
    delegation for compatibility, including MM's preset-definition re-read.
    """
    return getattr(ctrl, "authorization_map", None) is not None

def _check_acquisition_channel(
    ctrl: MicroscopeController, guard: SafetyGuard, channel: str
) -> None:
    """Gate a channel used as an acquisition *axis*, not as a one-off switch.

    The acquisition tools hand `channel` straight to pycro-manager as
    `channel_group="Channel"`, so Micro-Manager's group has to be the thing that
    defines it. A rig whose channels come from anywhere else (design/41 F6)
    cannot switch on that axis at all, and must not be allowed to run an
    acquisition that silently images every plane on whichever line was last on.
    """
    from microclaw.authorization import CHANNEL_SOURCE_CONFIG_GROUP, _channel_source

    guard.check_channel(channel)
    source = _channel_source(ctrl, guard.is_illumination_enable)
    if source.kind == CHANNEL_SOURCE_CONFIG_GROUP:
        return
    raise SafetyViolation(
        f"This acquisition cannot drive a channel axis for {channel!r}: an "
        "acquisition switches channels through Micro-Manager's \"Channel\" config "
        f"group, and this rig has no preset there. {source.describe()} Call "
        "set_channel first and run the acquisition without a channel argument — "
        "once per channel if the run needs more than one."
    )


def _emit_set_channel(params: RecordedParams) -> str:
    """Render the writes that actually ran, never a plan rebuilt from the rig.

    A channel is not always a Micro-Manager preset (design/41 F6), so emitting
    `core.set_config('Channel', ...)` would fail outright on the rig the plan
    came from. The recorded effect list is the one thing true of both sources.
    """
    return _emit_recorded_channel_effects(
        params.result, params.get("preset"), label="channel"
    )


def _emit_set_config_preset(params: RecordedParams) -> str:
    return _emit_recorded_channel_effects(
        params.result, params.get("preset"),
        label=f"config preset {params.get('group')!r}",
    )


def _emit_recorded_channel_effects(result: dict, preset: str, *, label: str) -> str:
    """Render one executed channel effect record for a standalone script."""
    effects = result.get("effects")
    if effects:
        lines = [f"# {label} {preset!r} ({result.get('channel_source')})"]
        verified = []
        for effect in effects:
            try:
                device, prop, value = (str(item) for item in effect)
            except (TypeError, ValueError):
                raise CannotEmit(
                    f"the recorded channel effect {effect!r} is not a "
                    "device/property/value triple"
                ) from None
            lines.append(f"core.set_property({device!r}, {prop!r}, {value!r})")
            if device != "Core":     # MM's pseudo-device never becomes busy
                lines.append(f"core.wait_for_device({device!r})")
            verified.append((device, prop, value))
        for device, prop, value in verified:
            # The executor's own check, inlined by _channel_verification_source.
            # Never hand-write the comparison here; see that function for why.
            lines.append(f"_verify_property(core, {device!r}, {prop!r}, {value!r})")
        return "\n".join(lines)
    group = result.get("config_group")
    if group:
        return (
            f"core.set_config({group!r}, {preset!r})\n"
            f"core.wait_for_config({group!r}, {preset!r})"
        )
    raise CannotEmit(
        "the recorded result has no executed channel effects, so the writes that "
        "ran cannot be reproduced"
    )


def _set_channel_for_composite(
    ctrl: MicroscopeController, guard: SafetyGuard, preset: str, *, cancel=None
) -> dict:
    from microclaw.authorization import CHANNEL_CONFIG_GROUP, execute_channel_plan

    guard.check_channel(preset)
    if _has_channel_authorization_map(ctrl):
        return execute_channel_plan(
            ctrl, guard, preset, confirm_fn=CONFIRM_FN, cancel=cancel
        )
    ctrl.core.set_config(CHANNEL_CONFIG_GROUP, preset)
    ctrl.core.wait_for_config(CHANNEL_CONFIG_GROUP, preset)
    ctrl.refresh_gui()
    return {
        "status": f"Channel set to '{preset}'.",
        "config_group": CHANNEL_CONFIG_GROUP,
    }


def _channel_effects_for_later_phase(
    ctrl: MicroscopeController, guard: SafetyGuard, preset: str
) -> dict:
    """Capture the intended export route without executing the later phase."""
    from microclaw.authorization import (
        CHANNEL_CONFIG_GROUP, _authorize_channel_effect, _channel_source,
        authorize_channel,
    )

    guard.check_channel(preset)
    if not _has_channel_authorization_map(ctrl):
        return {"config_group": CHANNEL_CONFIG_GROUP, "planned": True}
    authorize_channel(ctrl, preset)
    source = _channel_source(ctrl, guard.is_illumination_enable)
    effects = [
        (str(device), str(prop), "" if value is None else str(value))
        for device, prop, value in source.expand(ctrl.core, preset)
    ]
    for effect in effects:
        _authorize_channel_effect(ctrl, guard, *effect, CONFIRM_FN)
    return {"effects": [list(effect) for effect in effects],
            "channel_source": source.describe(), "planned": True}


@emits(_emit_set_channel)
def set_channel(
    ctrl: MicroscopeController, guard: SafetyGuard, preset: str, *, cancel=None
) -> dict:
    return _set_channel_for_composite(ctrl, guard, preset, cancel=cancel)


@emits(_emit_set_config_preset)
def set_config_preset(
    ctrl: MicroscopeController, guard: SafetyGuard, group: str, preset: str,
    *, cancel=None,
) -> dict:
    """Apply one Micro-Manager config preset through the channel-plan executor.

    Only the ``Channel`` group has the startup ``authorized_presets`` allowlist.
    Other groups are authorized from their freshly expanded effects: every
    device/property/value must pass its exact typed, illumination, or categorical
    gate before any write begins.

    **There is deliberately no map-less delegation branch here**, unlike
    ``set_channel``. The first implementation had one, and review measured it
    applying a laser-enabling preset with zero confirmations while reporting
    success. It cannot be repaired in place: ``set_channel`` gates its own
    map-less path with ``guard.check_channel``, which consults
    ``channels.allowed`` — the wrong question to ask about a camera preset, which
    it would refuse for not being a channel. No name-level gate exists for an
    arbitrary group, because per-effect authorization *is* the authorization
    here. The executor is therefore the only route, and it needs no map:
    ``authorize_channel`` returns early for a non-``Channel`` group, and
    ``_authorize_channel_effect`` falls through to the guard's own checks when
    no map is attached.
    """
    from microclaw.authorization import execute_channel_plan

    if not group:
        raise ValueError("group must be a non-empty config-group name")
    if not preset:
        raise ValueError("preset must be a non-empty preset name")
    return execute_channel_plan(
        ctrl, guard, preset, group=group,
        confirm_fn=CONFIRM_FN, cancel=cancel,
    )


@emits_nothing
def list_config_groups(
    ctrl: MicroscopeController, guard: SafetyGuard,
    group: str | None = None, preset: str | None = None,
) -> dict:
    """List config groups compactly, or expand one requested preset.

    Preset settings are omitted from the all-groups listing because real rigs can
    carry many large groups. Supplying both ``group`` and ``preset`` returns that
    preset's exact membership instead, so a caller never has to infer membership
    from live property values.

    The two shapes are exclusive on purpose. The first implementation returned
    the whole listing *and* the expansion, which the demo gate showed re-walking
    every group over a serialized bridge to answer a question about one preset —
    thirteen round trips on that rig to deliver one three-field answer the caller
    already had the listing for.
    """
    from microclaw.authorization import _expand_preset, _strings

    if (group is None) != (preset is None):
        raise ValueError("group and preset must be supplied together")
    if group is not None and preset is not None:
        return {
            "group": group,
            "preset": preset,
            "settings": [
                {"device": device, "property": prop, "value": value}
                for device, prop, value in _expand_preset(ctrl.core, preset, group)
            ],
        }
    # Two bridge round trips per group, and pyjavaz serializes them, which is
    # why preset *settings* are opt-in rather than walked for every group here.
    groups = []
    for name in _strings(ctrl.core.get_available_config_groups()):
        item: dict[str, Any] = {"name": name}
        try:
            item["presets"] = _strings(ctrl.core.get_available_configs(name))
        except Exception as exc:
            item["presets"] = []
            item["error"] = str(exc)
        try:
            active = str(ctrl.core.get_current_config(name) or "")
            item["active_preset"] = active or None
        except Exception as exc:
            item["active_preset"] = None
            detail = f"active preset read failed: {exc}"
            item["error"] = f"{item['error']}; {detail}" if "error" in item else detail
        groups.append(item)
    return {"groups": groups}


@emits_nothing
def get_available_channels(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    from microclaw.authorization import _channel_source

    source = _channel_source(ctrl, guard.is_illumination_enable)
    result: dict[str, Any] = {
        "channels": list(source.names), "source": source.describe(),
    }
    # M5 gate, 2026-08-06: a session ran with `channels.allowed: []`, was told it
    # had four channels, picked one, and was refused. The allowlist is still the
    # only authority -- asking it here rather than re-reading the config keeps it
    # that way -- and `channels` still reports what the rig has, so this hides no
    # rig reality. It only stops the model being offered what it cannot use.
    authorized = []
    for name in source.names:
        try:
            guard.check_channel(name)
        except SafetyViolation:
            continue
        authorized.append(name)
    if authorized != list(source.names):
        result["authorized"] = authorized
    # Never let a refused slot look like "this rig simply has no channels".
    if source.unavailable:
        result["unavailable"] = {
            label: list(reasons) for label, reasons in source.unavailable.items()
        }
    if source.problems:
        result["problems"] = list(source.problems)
    return result


# --- Device Properties ---

@emits(lambda p: f"core.set_property({p['device']!r}, {p['property']!r}, {p['value']!r})")
def set_device_property(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    device: str,
    property: str,
    value: str,
) -> dict:
    from microclaw.authorization import authorize_property_write

    authorize_property_write(ctrl, device, property)
    guard.check_device_property(ctrl.core, device, property, value)
    # Illumination gate (design/14 §3): shutter enables block on a human 'y',
    # power writes are capped and ratcheted. In code, not just the prompt.
    guard.check_illumination(ctrl.core, device, property, value, confirm_fn=CONFIRM_FN)
    ctrl.core.set_property(device, property, value)
    ctrl.refresh_gui()
    return {"status": f"Set {device}.{property} = {value!r}."}


@emits_nothing
def get_device_property(
    ctrl: MicroscopeController, guard: SafetyGuard, device: str, property: str
) -> dict:
    value = ctrl.core.get_property(device, property)
    return {"device": device, "property": property, "value": value}


@emits_nothing
def list_devices(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    devices = _str_vector(ctrl.core.get_loaded_devices())
    return {"devices": devices}


@emits_nothing
def list_device_properties(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    device: str,
) -> dict:
    props = _str_vector(ctrl.core.get_device_property_names(device))
    return {"device": device, "properties": props, "count": len(props)}


# mmcorej.PropertyType enum ordinals, verified over the ZMQ bridge (design/14
# V2: swig_value() → int, to_string() → name).
_PROP_TYPES = {0: "Undef", 1: "String", 2: "Float", 3: "Integer"}


def _property_type_name(core, device: str, prop: str) -> str:
    """Resolve the MM PropertyType enum to its name.

    Over the ZMQ bridge get_property_type() returns a pyjavaz proxy whose repr
    is `<pyjavaz...mmcorej_PropertyType object at 0x...>`. The previous
    `str(...).split(".")[-1]` sliced that repr mid-string and leaked a
    nondeterministic heap address into the model's context on every property
    inspection (design/14 §11), poisoning the prompt cache along the way.
    """
    raw = core.get_property_type(device, prop)
    if hasattr(raw, "to_string"):
        name = str(raw.to_string())
        if name in _PROP_TYPES.values():
            return name
    if hasattr(raw, "swig_value"):
        try:
            return _PROP_TYPES.get(int(raw.swig_value()), "Unknown")
        except (TypeError, ValueError):
            pass
    if isinstance(raw, str) and raw in _PROP_TYPES.values():
        return raw
    try:
        return _PROP_TYPES.get(int(raw), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"


@emits_nothing
def get_device_property_info(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    device: str,
    property: str,
) -> dict:
    read_only = bool(ctrl.core.is_property_read_only(device, property))
    pre_init = bool(ctrl.core.is_property_pre_init(device, property))
    prop_type = _property_type_name(ctrl.core, device, property)

    allowed_sv = ctrl.core.get_allowed_property_values(device, property)
    allowed = _str_vector(allowed_sv) if allowed_sv.size() > 0 else None

    has_limits = bool(ctrl.core.has_property_limits(device, property))
    lower = ctrl.core.get_property_lower_limit(device, property) if has_limits else None
    upper = ctrl.core.get_property_upper_limit(device, property) if has_limits else None

    current = ctrl.core.get_property(device, property)

    info = {
        "device": device,
        "property": property,
        "current_value": current,
        "type": prop_type,
        "read_only": read_only,
        "pre_init": pre_init,
        "allowed_values": allowed,
        "lower_limit": lower,
        "upper_limit": upper,
    }
    # The driver's range is what the hardware permits; the declared policy is
    # what this rig's reviewer permits, and it may be tighter. Reporting only
    # the former advertises authority the guard will refuse.
    policy = guard.typed_actuator_policy(device, property)
    if policy is not None:
        info["declared_policy"] = {
            "kind": policy.kind,
            "units": policy.units,
            "minimum": policy.minimum,
            "maximum": policy.maximum,
        }
    return info


@emits_nothing
def get_full_device_state(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    device: str,
) -> dict:
    props = _str_vector(ctrl.core.get_device_property_names(device))
    state = {}
    for p in props:
        try:
            state[p] = ctrl.core.get_property(device, p)
        except Exception as exc:
            state[p] = f"<error: {exc}>"
    return {"device": device, "state": state}


# --- System State ---

_NO_LASER_MAP = (
    "unknown — this rig has no EMU laser map, which is the only per-SLOT laser "
    "source microclaw has. This is not evidence that the rig has no lasers: a "
    "non-EMU rig drives its lasers as ordinary device properties. Read "
    "declared_illumination_properties and list_devices before saying anything "
    "about what illumination exists here."
)

# Absence of an EMU config is a fact about microclaw's map, not about the
# hardware. The demo rig ships Emu.jar with no config.uicfg, so "not an EMU
# rig" was wrong there in both directions.
_NO_EMU_CONFIG = (
    "No EMU configuration file on this rig, so the EMU semantic map is "
    "unavailable. That does not mean the rig has no lasers or filters — on a "
    "non-EMU rig they are ordinary device properties."
)


def _shutter_state(ctrl: MicroscopeController) -> Any:
    """Shutter device, and whether it is open. Never silently absent.

    An omitted key is what let the agent sign off "no lasers were involved"
    with nothing behind it (design/20 S4). "unknown" is a fact; a missing field
    is an invitation.
    """
    try:
        device = str(ctrl.core.get_shutter_device())
    except Exception:
        return "unknown"
    if not device:
        # Knowing there is no shutter is not knowing the light is off.
        return "no shutter device configured"
    entry: dict[str, Any] = {"device": device}
    for key, read in (("open", ctrl.core.get_shutter_open),
                      ("auto", ctrl.core.get_auto_shutter)):
        try:
            entry[key] = bool(read())
        except Exception:
            entry[key] = "unknown"
    # "Closed right now" is not "closed during your last exposure" (design/21
    # S2). A manual shutter's resting state is its exposure state; under
    # autoshutter the resting read says nothing — refuse to let `open` answer a
    # question about the past.
    if entry.get("auto") is True:
        entry["open_during_exposure"] = (
            "unknown (autoshutter opens the shutter for each exposure)"
        )
    elif entry.get("auto") is False and isinstance(entry.get("open"), bool):
        entry["open_during_exposure"] = entry["open"]
    else:
        entry["open_during_exposure"] = "unknown"
    return entry


def _laser_state(ctrl: MicroscopeController) -> Any:
    """Per-slot laser enable/power, read through the EMU map.

    Slot index pairs each laser with its own lines; nothing here infers a slot
    from device order (design/14 §1).
    """
    from microclaw.emu_manager import build_emu_map

    props, params = _cached_emu_properties(ctrl)
    if not props:
        return _NO_LASER_MAP
    try:
        lasers = build_emu_map(props, params)["lasers"]
    except Exception:
        return "unknown"
    if not lasers:
        return _NO_LASER_MAP

    out: dict[int, Any] = {}
    for slot, laser in sorted(lasers.items()):
        readings: dict[str, Any] = {}
        if "name" in laser:
            readings["name"] = laser["name"]
        for key, field in (("enabled", "enable"), ("power_pct", "power_pct")):
            line = laser.get(field)
            if not line or "device" not in line:
                continue
            try:
                readings[key] = str(
                    ctrl.core.get_property(line["device"], line["property"])
                )
            except Exception:
                readings[key] = "unknown"
        out[slot] = readings or "unknown"
    return out


@emits_nothing
def get_system_state(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    state: dict[str, Any] = {}
    # Reported on every call, deliberately. There is no "first call" to hang a
    # one-shot warning on: Microclaw opens mid-session and more than once, so
    # any "already warned" state is something a second launch would trip over.
    # Absent entirely when every axis is inside its envelope -- an always-present
    # field reads as a warning.
    out_of_bounds = []
    try:
        x = ctrl.core.get_x_position()
        y = ctrl.core.get_y_position()
        state["x_um"] = round(x, 3)
        state["y_um"] = round(y, 3)
        violation = _bounds_violation(guard.check_xy, x, y)
        if violation:
            out_of_bounds.append(violation)
    except Exception:
        state["xy_stage"] = "unavailable"
    try:
        z = ctrl.core.get_position()
        state["z_um"] = round(z, 3)
        violation = _bounds_violation(guard.check_z, z)
        if violation:
            out_of_bounds.append(violation)
    except Exception:
        state["z_stage"] = "unavailable"
    # One bridge round trip per declared named stage, on the tool a session calls
    # to orient itself -- and pyjavaz serializes every call, so these are paid in
    # sequence. That cost is accepted because a named stage nobody reads is a
    # named stage whose limit nobody can check. Read only what `named_stages`
    # declares; do NOT enumerate the rig's devices, and do not add further reads
    # here without weighing the same trade.
    if guard._c.named_stages:
        state["named_stages"] = {}
        for limits in guard._c.named_stages:
            try:
                position = float(ctrl.core.get_position(limits.device))
                state["named_stages"][limits.device] = round(position, 4)
                violation = _bounds_violation(
                    guard.check_named_stage, limits.device, position
                )
                if violation:
                    out_of_bounds.append(violation)
            except Exception:
                state["named_stages"][limits.device] = "unavailable"
    if out_of_bounds:
        state["out_of_bounds"] = out_of_bounds
    try:
        state["exposure_ms"] = ctrl.core.get_exposure()
    except Exception:
        pass
    try:
        state["live_view"] = ctrl.studio.live().is_live_mode_on()
    except Exception:
        pass
    # Always present, even as "unknown": a sign-off like "no lasers were
    # enabled" has to be sourced from here or not made at all (design/20 F2).
    state["shutter"] = _shutter_state(ctrl)
    state["lasers"] = _laser_state(ctrl)
    illumination = guard.declared_illumination_state(ctrl.core)
    if illumination:
        state["declared_illumination_properties"] = illumination
    try:
        label = str(ctrl.core.get_camera_device())
        state["camera"] = {
            "label": label or "unknown",
            # The label is whatever the config author typed; the adapter is the
            # hardware. F4 keys knowledge entries on the adapter for that reason.
            "adapter": str(ctrl.core.get_device_name(label)) if label else "unknown",
        }
    except Exception:
        state["camera"] = "unknown"
    return state


# --- Acquisitions ---

def _build_acquisition_events(
    *,
    channel: str | None = None,
    exposure_ms: float | None = None,
    **acq_kwargs: Any,
) -> list:
    """Build an event list via multi_d_acquisition_events with channel handling.

    acq_kwargs carry the event-shape parameters: z_start/z_end/z_step for a
    Z-stack, or num_time_points/time_interval_s for a timelapse. The hook
    machinery is agnostic to which shape is used.
    """
    if channel:
        acq_kwargs.update(channel_group="Channel", channels=[channel])
        if exposure_ms is not None:
            acq_kwargs["channel_exposures_ms"] = [exposure_ms]
    return multi_d_acquisition_events(**acq_kwargs)


def _acquire_with_hooks(
    guard: SafetyGuard,
    save_dir: str,
    name: str,
    events: list | Callable[[Any], Any],
    hook: Any | None = None,
    reservation: Reservation | None = None,
    close_reservation: bool = True,
) -> str:
    """Run one Acquisition, attaching hook callables if a hook is supplied.

    Returns the on-disk dataset path. A hook is any object exposing
    post_hardware_hook_fn and/or image_process_fn; both are optional and are
    wired in only if present, so the same runner serves plain and adaptive
    acquisitions of any event shape.

    `events` is a list, or a factory called with the live Acquisition that
    returns what acquire() should be handed (design/24 Fix 2 — in practice a
    generator that holds the event source open so a detector hook can extend
    the scan). A factory, not a pre-built generator, because the generator
    must hold the acquisition's REAL event queue to own its terminator
    (abort() clears the queue, mark_finished()'s None included — Fix 2a), and
    that queue does not exist until the Acquisition is constructed here.

    Known-up-front acquisitions pass their list directly to acquire(). They
    cannot be cancelled mid-run, and never could; generator feeding was
    measured at 3.14x the list cost and removed (design/32 §2). Adaptive
    factories remain generators because their later events do not yet exist.

    The one place an acquisition touches the filesystem, and so the one place
    `save_dir` is confined to a configured workspace. Without this the guard is
    one-sided: `export_dataset_as_tiff` resolves the path it reads, while the
    acquisition that wrote it could put a dataset anywhere on disk — so a z-stack
    saved outside the workspace can never be exported, and the refusal arrives
    only after the objective has already swept the range. Callers resolve
    `save_dir` up front too, to fail before any hardware moves; resolving twice
    is idempotent.
    """
    save_dir = guard.resolve_in_workspace(save_dir)
    hook_fn_kwargs: dict[str, Any] = {}
    if hook is not None:
        if reservation is not None and hasattr(hook, "bind_reservation"):
            hook.bind_reservation(reservation)
        if hasattr(hook, "post_hardware_hook_fn"):
            hook_fn_kwargs["post_hardware_hook_fn"] = hook.post_hardware_hook_fn
        if hasattr(hook, "pre_hardware_hook_fn"):
            hook_fn_kwargs["pre_hardware_hook_fn"] = hook.pre_hardware_hook_fn
        if hasattr(hook, "image_process_fn"):
            hook_fn_kwargs["image_process_fn"] = hook.image_process_fn

    if reservation is not None:
        def account_saved_frame(axes, dataset):
            # pycro-manager 1.0.2 calls this after the image is on disk. Unlike
            # image_process_fn, its arguments contain no pixel array, so plain
            # acquisitions retain the Java-side streaming fast path.
            if not reservation.commit_frame():
                _note_budget_exhausted(
                    hook,
                    reservation.plan.frames,
                    overrun_frames=reservation.overrun_frames,
                )

        hook_fn_kwargs["image_saved_fn"] = account_saved_frame

    dataset_path = None
    restoration_attempted = {"named-stage": False, "property": False}

    def restore_hardware() -> list[str]:
        failures = []
        if hook is None:
            return failures
        for label, method_name, result_name in (
            ("named-stage", "restore_named_stage", "_named_stage_restoration"),
            ("property", "restore_property", "_property_restoration"),
        ):
            method = getattr(hook, method_name, None)
            if restoration_attempted[label] or not callable(method):
                continue
            restoration_attempted[label] = True
            try:
                setattr(hook, result_name, method())
            except Exception as restore_exc:
                failures.append(f"{label} restoration failed: {restore_exc}")
        return failures

    try:
        with Acquisition(directory=save_dir, name=name, show_display=True, **hook_fn_kwargs) as acq:
            # Resolve collision suffixes before dispatching the first event: a
            # first-frame hook failure must still report the data already owned
            # by this acquisition. This is safe to read here because
            # pycro-manager 1.0.2 sets `_dataset_disk_location` inside
            # Acquisition.__init__ from the Java storage's real disk location
            # (java_backend_acquisitions.py:301), before acquire() dispatches
            # anything. If that moves, the fallback in _acq_dataset_path returns
            # the UNSUFFIXED path — which is precisely the wrong guess design/38
            # F7 is about, so re-verify this on any pycro-manager upgrade.
            dataset_path = _acq_dataset_path(acq, save_dir, name)
            if hook is not None and hasattr(hook, "bind_artifact_directory"):
                hook.bind_artifact_directory(
                    Path(dataset_path) / "artifacts"
                )
            if callable(events):
                events = events(acq)
            acq.acquire(events)
        # Acquisition.acquire() only submits work. __exit__ marks the stream
        # finished and awaits completion, so named-stage restoration is safe
        # only after the context has exited and all callbacks have run.
        restoration_failures = restore_hardware()
        if restoration_failures:
            raise RuntimeError("; ".join(restoration_failures))
    except Exception as exc:
        restoration_failures = restore_hardware()
        if restoration_failures:
            exc = RuntimeError(f"{exc}; {'; '.join(restoration_failures)}")
        if hook is not None and dataset_path is not None:
            stage_ctx = getattr(hook, "_named_stage_context", None)
            property_ctx = getattr(hook, "_property_context", None)
            if stage_ctx is not None and property_ctx is not None:
                last_state = {
                    "named_stage": {
                        "device": stage_ctx["device"],
                        "position_um": stage_ctx["last_known"],
                    },
                    "property": {
                        "device": property_ctx["device"],
                        "property": property_ctx["property"],
                        "value": property_ctx["last_known"],
                    },
                }
            elif stage_ctx is not None:
                last_state = {
                    "device": stage_ctx["device"],
                    "position_um": stage_ctx["last_known"],
                }
            elif property_ctx is not None:
                last_state = {
                    "device": property_ctx["device"],
                    "property": property_ctx["property"],
                    "value": property_ctx["last_known"],
                }
            else:
                last_state = None
            raise _HookedAcquisitionFailure(
                exc, dataset_path,
                # A hooked run with no reservation is real, not hypothetical:
                # the survey runner passes a hook with reservation=None
                # whenever `adaptive` is false. So this is optional, not
                # defensive. Reporting 0 there would state a count nothing
                # measured; None says the frame count is unknown, which is the
                # true claim, and _hooked_failure_result omits it.
                frames_exposed=(None if reservation is None
                                else reservation.completed_frames),
                last_hardware_state=last_state,
            ) from exc
        raise
    finally:
        if reservation is not None and close_reservation:
            reservation.close()

    return _acq_dataset_path(acq, save_dir, name)


def _acq_dataset_path(acq, save_dir: str, name: str) -> str:
    """On-disk path of a completed Acquisition's dataset.

    pycro-manager exposes no public accessor for the dataset directory, so we
    read the private `_dataset_disk_location`. Verified against pycro-manager
    1.0.2; if that attribute drifts, the fallback keeps the path deterministic
    (Acquisition writes to <save_dir>/<name> by default).
    """
    return getattr(acq, "_dataset_disk_location", None) or str(Path(save_dir) / name)


@_acquisition_entry_point
@emits(_emit_zstack)
def run_zstack(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    z_start_um: float,
    z_end_um: float,
    z_step_um: float,
    save_dir: str,
    channel: str | None = None,
    exposure_ms: float | None = None,
    name: str = "zstack",
    hook_strategy: str | None = None,
    hook_params: dict | None = None,
    log_path: str | None = None,
    illumination_envelope: dict | None = None,
    named_stage_envelope: dict | None = None,
    property_envelope: dict | None = None,
    hook_action_plan: list[dict] | None = None,
    artifact_limits: dict | None = None,
    _reservation: Reservation | None = None,
) -> dict:
    # Before set_exposure and before the sweep: an out-of-workspace save_dir
    # must not cost an acquisition to discover.
    save_dir = guard.resolve_in_workspace(save_dir)
    carries_hardware_capability = any(
        value is not None for value in (
            illumination_envelope, artifact_limits, named_stage_envelope,
            property_envelope, hook_action_plan,
        )
    )
    if _reservation is not None and (hook_strategy or carries_hardware_capability):
        raise ValueError(
            "A hook or hook hardware plan cannot be nested in a reserved "
            "per-position protocol. Pass "
            "run_multiposition_acquisition(hook_strategy=...) instead; it uses one "
            "Acquisition and one hook log across every position. A "
            "hook_action_plan has no such route: its indices address one run's events."
        )
    guard.check_z(z_start_um)
    guard.check_z(z_end_um)
    if channel:
        _check_acquisition_channel(ctrl, guard, channel)
    if exposure_ms is not None:
        guard.check_exposure(exposure_ms)

    # Event construction is pure, and everything above only reads or validates
    # the rig, so the capability guard below can precede the preamble's one
    # mutation without changing the order of any observable hardware action.
    events = _build_acquisition_events(
        channel=channel, exposure_ms=exposure_ms,
        z_start=z_start_um, z_end=z_end_um, z_step=z_step_um,
    )
    carries_plan = hook_action_plan is not None
    hook = None
    log_path = (
        _prepare_log_path(
            guard, log_path, default=f"{save_dir}/{name}_plan_log.jsonl"
        ) if carries_plan else
        _prepare_log_path(guard, log_path) if hook_strategy else None
    )
    if hook_strategy:
        try:
            hook = _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path)
        except ValueError as exc:
            return {"error": str(exc)}
    elif carries_plan:
        from microclaw.hook_decisions import PLAN_ONLY, UntrustedHookAdapter
        hook = UntrustedHookAdapter(PLAN_ONLY, log_path=log_path)
    try:
        # Unconditional and ahead of set_exposure: a capability with no hook to
        # carry it refuses before the camera is changed.
        _configure_hook_capabilities(
            hook, ctrl, guard, save_dir, name, illumination_envelope,
            artifact_limits, named_stage_envelope, hook_action_plan, events,
            property_envelope=property_envelope,
        )
    except _HookArtifactBudgetError as exc:
        return {"error": str(exc)}
    if not channel and exposure_ms is not None:
        ctrl.core.set_exposure(exposure_ms)
    plan = plan_events(ctrl, events, exposure_ms)
    if hook is not None:
        plan = _plan_with_hook_dose(plan, hook)
    reservation = _reservation or _authorize_acquisition(ctrl, guard, plan)
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    try:
        dataset_path = _acquire_with_hooks(
            guard, save_dir, name, events, hook, reservation=reservation,
            close_reservation=_reservation is None,
        )
    except _HookedAcquisitionFailure as exc:
        return _hooked_failure_result(exc, log_path)
    result = {
        "status": "Z-stack complete.", "dataset_path": dataset_path,
        **_reservation_report(reservation),
    }
    if hook is not None:
        result.update(_adaptive_result(
            dataset_path, log_path, status="Z-stack complete.",
            frames_planned=len(events), frames_acquired=len(events),
            frames_exposed=len(events),
            started_at=started_at.isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_s=round(time.monotonic() - started, 6),
            **_reservation_report(reservation),
        ))
        restoration = getattr(hook, "_named_stage_restoration", None)
        if restoration is not None:
            result["named_stage_restoration"] = restoration
        property_restoration = getattr(hook, "_property_restoration", None)
        if property_restoration is not None:
            result["property_restoration"] = property_restoration
    return result


def _verify_trigger_line_armed(ctrl: MicroscopeController, laser_slot: int) -> dict:
    """Verify only that an EMU slot's trigger line is armed.

    This checks trigger mode and trigger sequence when the map declares them.
    It does not verify any other part of the emission path.
    """
    from microclaw.emu_manager import build_emu_map

    props, params = _cached_emu_properties(ctrl)
    if not props:
        return {
            "guarantee": "no trigger-line verification available",
            "checked": [],
            "not_verified": ["device-level enables", "illumination properties", "emission path"],
        }
    lasers = build_emu_map(props, params)["lasers"]
    laser = lasers.get(laser_slot)
    if laser is None:
        raise SafetyViolation(
            f"No EMU laser at slot {laser_slot}. Configured slots: "
            f"{sorted(lasers)}. Call get_emu_laser_map() — never infer a slot "
            f"index from device naming order."
        )
    checked = []
    trig = laser.get("trigger_mode")
    if trig and "device" in trig:
        mode = str(ctrl.core.get_property(trig["device"], trig["property"]))
        checked.append({"kind": "trigger mode", "device": trig["device"],
                        "property": trig["property"], "value": mode})
        if mode.strip().startswith("0"):
            raise SafetyViolation(
                f"Laser slot {laser_slot} trigger mode is {mode!r}: the trigger "
                f"line is not armed. Set {trig['device']}.{trig['property']} to an armed mode (e.g. "
                f"'4 - Follow') first."
            )
    seq = laser.get("trigger_sequence")
    if seq and "device" in seq:
        value = str(ctrl.core.get_property(seq["device"], seq["property"]))
        checked.append({"kind": "trigger sequence", "device": seq["device"],
                        "property": seq["property"], "value": value})
        if value.strip() == "0":
            raise SafetyViolation(
                f"Laser slot {laser_slot} trigger sequence is 0: the laser is "
                f"not armed at the trigger for any frame. Set {seq['device']}."
                f"{seq['property']} (65535 = always on) first."
            )
    return {
        "guarantee": "trigger line is armed",
        "checked": checked,
        "not_verified": ["device-level enables", "illumination properties", "emission path"],
    }


def shutter_declared_illumination(
    ctrl: MicroscopeController, guard: SafetyGuard
) -> dict:
    """Explicitly drive all declared illumination properties to off_value."""
    attempted = [
        f"{item['device']}.{item['property']}"
        for item in guard.declared_illumination_state(ctrl.core)
    ]
    shuttered = guard.shutter_all(ctrl.core)
    return {"status": "Declared illumination shutter requested.",
            "attempted": attempted, "shuttered": shuttered}


@_acquisition_entry_point
@emits(_emit_timelapse)
def run_timelapse(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    n_frames: int,
    interval_s: float,
    save_dir: str,
    channel: str | None = None,
    exposure_ms: float | None = None,
    name: str = "timelapse",
    laser_slot: int | None = None,
    hook_strategy: str | None = None,
    hook_params: dict | None = None,
    log_path: str | None = None,
    illumination_envelope: dict | None = None,
    named_stage_envelope: dict | None = None,
    property_envelope: dict | None = None,
    hook_action_plan: list[dict] | None = None,
    artifact_limits: dict | None = None,
    _reservation: Reservation | None = None,
) -> dict:
    save_dir = guard.resolve_in_workspace(save_dir)   # before any hardware moves
    carries_hardware_capability = any(
        value is not None for value in (
            illumination_envelope, artifact_limits, named_stage_envelope,
            property_envelope, hook_action_plan,
        )
    )
    if _reservation is not None and (hook_strategy or carries_hardware_capability):
        raise ValueError(
            "A hook or hook hardware plan cannot be nested in a reserved "
            "per-position protocol. Pass "
            "run_multiposition_acquisition(hook_strategy=...) instead; it uses one "
            "Acquisition and one hook log across every position. A "
            "hook_action_plan has no such route: its indices address one run's events."
        )
    if hook_action_plan is not None and n_frames > 1 and interval_s == 0:
        raise ValueError(
            "interval_s=0 lets the engine hardware-sequence the time axis, and a "
            "sequenced burst runs with no software between exposures, so a "
            "per-frame hook_action_plan cannot be honoured. Pass a nonzero "
            "interval_s to disable time-axis sequencing."
        )
    trigger_preflight = None
    if laser_slot is not None:
        trigger_preflight = _verify_trigger_line_armed(ctrl, laser_slot)
    if channel:
        _check_acquisition_channel(ctrl, guard, channel)
    if exposure_ms is not None:
        guard.check_exposure(exposure_ms)

    # Event construction is pure, and everything above only reads or validates
    # the rig, so the capability guard below can precede the preamble's one
    # mutation without changing the order of any observable hardware action.
    events = _build_acquisition_events(
        channel=channel, exposure_ms=exposure_ms,
        num_time_points=n_frames, time_interval_s=interval_s,
    )
    carries_plan = hook_action_plan is not None
    hook = None
    log_path = (
        _prepare_log_path(
            guard, log_path, default=f"{save_dir}/{name}_plan_log.jsonl"
        ) if carries_plan else
        _prepare_log_path(guard, log_path) if hook_strategy else None
    )
    if hook_strategy:
        try:
            hook = _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path)
        except ValueError as exc:
            return {"error": str(exc)}
    elif carries_plan:
        from microclaw.hook_decisions import PLAN_ONLY, UntrustedHookAdapter
        hook = UntrustedHookAdapter(PLAN_ONLY, log_path=log_path)
    try:
        # Unconditional and ahead of set_exposure: a capability with no hook to
        # carry it refuses before the camera is changed.
        _configure_hook_capabilities(
            hook, ctrl, guard, save_dir, name, illumination_envelope,
            artifact_limits, named_stage_envelope, hook_action_plan, events,
            property_envelope=property_envelope,
        )
    except _HookArtifactBudgetError as exc:
        return {"error": str(exc)}
    # Without a channel, events carry no exposure, so set it directly. This is
    # the preamble's only mutation and therefore stays below the guard.
    if not channel and exposure_ms is not None:
        ctrl.core.set_exposure(exposure_ms)
    plan = plan_events(ctrl, events, exposure_ms)
    if hook is not None:
        plan = _plan_with_hook_dose(plan, hook)
    reservation = _reservation or _authorize_acquisition(ctrl, guard, plan)
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    try:
        dataset_path = _acquire_with_hooks(
            guard, save_dir, name, events, hook, reservation=reservation,
            close_reservation=_reservation is None,
        )
    except _HookedAcquisitionFailure as exc:
        return _hooked_failure_result(exc, log_path)
    result = {
        "status": "Timelapse complete.", "dataset_path": dataset_path,
        **_reservation_report(reservation),
    }
    if trigger_preflight is not None:
        result["trigger_preflight"] = trigger_preflight
    illumination = guard.declared_illumination_state(ctrl.core)
    if illumination:
        result["declared_illumination_properties"] = illumination
    if hook is not None:
        result.update(_adaptive_result(
            dataset_path, log_path, status="Timelapse complete.",
            frames_planned=len(events), frames_acquired=len(events),
            frames_exposed=len(events),
            started_at=started_at.isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_s=round(time.monotonic() - started, 6),
            **_reservation_report(reservation),
        ))
        restoration = getattr(hook, "_named_stage_restoration", None)
        if restoration is not None:
            result["named_stage_restoration"] = restoration
        property_restoration = getattr(hook, "_property_restoration", None)
        if property_restoration is not None:
            result["property_restoration"] = property_restoration
    return result


def export_dataset_as_tiff(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    dataset_path: str,
    output_path: str,
) -> dict:
    dataset_path = guard.resolve_readable_path(dataset_path)
    output_path = guard.resolve_in_workspace(output_path)
    # Every other writing tool makes its parent, and the generic path hint tells
    # the reader microclaw does -- so when this one did not, the failure named
    # three causes that were all wrong and cost nine calls on M5 before an agent
    # worked around it by writing a README into the folder first. Exporting
    # frames is how images reach a classifier for training, so this sits on the
    # retraining path.
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    dataset = Dataset(dataset_path)
    axes = dataset.axes

    # Iterate the full product of ALL non-spatial axes rather than only z OR
    # time — the old branch silently dropped every axis but one. Order axes
    # ImageJ-first (T, Z, C, position) so the hyperstack metadata lines up, with
    # any unexpected axis names appended.
    preferred = [a for a in ("time", "z", "channel", "position") if a in axes]
    axis_names = preferred + [a for a in axes if a not in preferred]

    if axis_names:
        # Iterate the ACTUAL coordinate values NDTiff reports, not range(len)
        # (design/28 F3). Two wrong assumptions broke multi-position export:
        #   (a) each axis's coords are 0..len-1 — false for `position`, whose
        #       coordinates are not guaranteed contiguous/zero-based, so
        #       read_image(position=k) raised KeyError: 'position';
        #   (b) every combo in the Cartesian product exists — false for a
        #       one-frame-per-position grid, which is a sparse hypercube.
        # has_image guards each read; missing cells become zero frames so the
        # ImageJ hyperstack stays rectangular.
        coord_values = {a: sorted(axes[a]) for a in axis_names}
        combos = list(itertools.product(*(coord_values[a] for a in axis_names)))
        present = {
            tuple(coords[a] for a in axis_names): dataset.read_image(**coords)
            for coords in _iter_present_coords(dataset, {})
        }
        if not present:
            return {"error": f"No images found in dataset: {dataset_path}"}
        sample = next(iter(present.values()))
        frame_shape, frame_dtype = sample.shape, sample.dtype
        frames = [
            present.get(combo, np.zeros(frame_shape, dtype=frame_dtype))
            for combo in combos
        ]
        shape = tuple(len(coord_values[a]) for a in axis_names)
        stack = np.stack(frames).reshape(*shape, *frame_shape)
    else:
        stack = dataset.read_image()

    tifffile.imwrite(output_path, stack, imagej=True)
    return {"status": "Export complete.", "output_path": output_path,
            "axes": axis_names,
            "artifact": {"kind": "tiff", "path": output_path}}


def _iter_present_coords(dataset, fixed_axes: dict) -> Any:
    """Yield real coordinates for present cells in a sparse NDTiff dataset."""
    unknown = set(fixed_axes) - set(dataset.axes)
    if unknown:
        raise ValueError(f"Unknown dataset axes: {sorted(unknown)}")
    invalid = {
        axis: value for axis, value in fixed_axes.items()
        if value not in dataset.axes[axis]
    }
    if invalid:
        raise ValueError(f"Dataset axis selections are not present: {invalid}")

    axis_names = list(dataset.axes)
    values = [
        [fixed_axes[axis]] if axis in fixed_axes else sorted(dataset.axes[axis])
        for axis in axis_names
    ]
    for combo in itertools.product(*values):
        coords = dict(zip(axis_names, combo))
        if dataset.has_image(**coords):
            yield coords


# Camera model lives under a different per-image key for every vendor, because
# MM stamps whatever the adapter happens to call its property. Measured on real
# datasets: the Andor iXon exposes 'Andor-Camera', while the Hamamatsu
# C15440-20UP has no '-Camera' key at all and carries the model under
# '-CameraName' with the serial under '-CameraID'. Resolve in order and record
# which key answered, rather than assuming one vendor's shape is the contract.
_CAMERA_MODEL_KEY_SUFFIXES = ("-Camera", "-CameraName", "-CameraID")


def _camera_model(metadata: dict, camera: str) -> tuple[str, str] | tuple[None, None]:
    for suffix in _CAMERA_MODEL_KEY_SUFFIXES:
        key = f"{camera}{suffix}"
        value = metadata.get(key)
        if value not in (None, ""):
            return str(value), key
    return None, None


def _mosaic_dataset_identity(metadata_items: list[tuple[dict, dict]]) -> dict:
    """Read the real MM per-image camera/ROI/binning keys and require consistency."""
    identities = []
    model_key = None
    for coords, metadata in metadata_items:
        camera = metadata.get("Core-Camera")
        if camera in (None, ""):
            model = None
        else:
            model, model_key = _camera_model(metadata, str(camera))
        raw_roi = metadata.get("ROI")
        try:
            roi = [int(value) for value in str(raw_roi).split("-")]
        except (TypeError, ValueError):
            roi = None
        if roi is not None and len(roi) != 4:
            roi = None
        raw_binning = metadata.get("Binning")
        try:
            binning = int(str(raw_binning).lower().split("x", 1)[0])
        except (TypeError, ValueError):
            binning = None
        identities.append((camera, model, roi, binning, coords))
    first = identities[0]
    if any(item[:4] != first[:4] for item in identities[1:]):
        raise ValueError("Dataset camera, ROI, or binning changes within the selected plane")
    model_names = " or ".join(f"{first[0]}{suffix}" for suffix in _CAMERA_MODEL_KEY_SUFFIXES)
    missing = [name for name, value in zip(
        ("Core-Camera", model_names, "ROI", "Binning"), first[:4]
    ) if value is None]
    if missing:
        raise ValueError("Dataset calibration identity metadata is incomplete; missing " + ", ".join(missing))
    return {"camera_device": str(first[0]), "camera_model": str(first[1]),
            "camera_model_key": model_key, "roi": first[2], "binning": first[3]}


@refuses(
    "offline mosaic dependencies transitively require the package calibration module, so inlining would not be standalone"
)
def build_stage_coordinate_mosaic(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    dataset_path: str,
    output_path: str,
    axis_selection: dict | None = None,
    calibration_ref: dict | None = None,
    output_pixel_size_um: float | None = None,
) -> dict:
    """Build one stage-coordinate mosaic from a selected saved-NDTiff plane.

    This is a zero-exposure read. Later tiles overwrite earlier tiles as a
    deterministic display convention; it is not image alignment or object
    matching. The TIFF is uint16 and the adjacent JSON manifest contains no
    timestamp, so identical inputs and selection produce identical pixels and
    hashed payloads.
    """
    if axis_selection is None:
        axis_selection = {}
    if not isinstance(axis_selection, dict):
        raise ValueError("axis_selection must be an object")
    dataset_path = guard.resolve_readable_path(dataset_path)
    output_path = guard.resolve_in_workspace(output_path)
    dataset = Dataset(dataset_path)
    axes = set(dataset.axes)
    non_position_axes = axes - {"position"}
    unknown = set(axis_selection) - non_position_axes
    missing = non_position_axes - set(axis_selection)
    if unknown:
        raise ValueError(f"Unknown or position axis selections: {sorted(unknown)}")
    for axis in sorted(missing):
        values = list(dataset.axes[axis])
        if len(values) == 1:
            axis_selection[axis] = values[0]
    missing = non_position_axes - set(axis_selection)
    if missing:
        available = {axis: list(dataset.axes[axis]) for axis in sorted(non_position_axes)}
        raise ValueError(
            "axis_selection must fix every ambiguous non-position axis; remaining "
            f"{sorted(missing)}. Full non-position axis values: {available}"
        )
    for axis, value in axis_selection.items():
        if value not in dataset.axes[axis]:
            raise ValueError(f"Dataset axis selection is not present: {axis}={value!r}")

    coords = list(_iter_present_coords(dataset, axis_selection))
    if not coords:
        raise ValueError("No images exist in the selected dataset plane")
    metadata_items = [(item, dataset.read_metadata(**item)) for item in coords]
    dataset_identity = _mosaic_dataset_identity(metadata_items)
    affine, calibration_identity = resolve_calibration(
        dataset, calibration_ref, fixed_axes=axis_selection, ctrl=ctrl, guard=guard
    )
    # Refuse only where the difference changes the transform we apply or means
    # this is a different microscope. Camera device/model is design/29 §5's
    # requirement — it is what stops one instrument's affine reaching another's
    # dataset. Binning scales the effective pixel size, so an affine measured at
    # one binning is numerically wrong at another.
    mismatches = {
        key: {"dataset": dataset_identity[key], "calibration": calibration_identity[key]}
        for key in ("camera_device", "camera_model")
        if dataset_identity[key] != calibration_identity[key]
    }
    if dataset_identity["binning"] != affine.binning:
        mismatches["binning"] = {
            "dataset": dataset_identity["binning"], "calibration": affine.binning
        }
    if mismatches:
        raise ValueError(f"Calibration identity contradicts dataset metadata: {mismatches}")
    # ROI is recorded, never gating. Placement consumes only the affine's four
    # coefficients, and ROI does not enter that arithmetic: a crop changes
    # neither pixel size nor rotation. An off-centre crop displaces the true
    # optical centre from the frame centre we place at, but identically for
    # every tile in the dataset, so it costs a constant translation of the whole
    # mosaic and nothing in the relative geometry. Measured live on M2: an
    # affine calibrated at full frame (0,0,512,512) is valid for Run A's
    # 453x227 crop on the same camera.
    roi_difference = None
    if dataset_identity["roi"] != calibration_identity["roi"]:
        roi_difference = {
            "dataset": dataset_identity["roi"],
            "calibration": calibration_identity["roi"],
            "effect": (
                "calibration measured at a different ROI on the same camera; "
                "placement is unaffected apart from a constant translation of "
                "the whole mosaic"
            ),
        }

    class SelectedFrames:
        """Re-read each tile per pass so source images are never retained together."""
        iteration = 0

        def __iter__(self):
            self.iteration += 1
            for item, metadata in metadata_items:
                absent = [key for key in ("XPosition_um_Intended", "YPosition_um_Intended")
                          if metadata.get(key) in (None, "")]
                if absent:
                    raise ValueError(
                        "Selected image lacks intended stage coordinates "
                        f"{absent} at {item}; XPosition_um_Intended and "
                        "YPosition_um_Intended are required"
                    )
                pixels = None
                if self.iteration == 1:
                    pixel_dtypes = {"GRAY8": np.dtype(np.uint8), "GRAY16": np.dtype(np.uint16)}
                    try:
                        shape = (int(metadata["Height"]), int(metadata["Width"]))
                        image_dtype = pixel_dtypes[str(metadata["PixelType"]).upper()]
                        if min(shape) <= 0:
                            raise ValueError
                        pixels = MosaicFrameShape(shape, image_dtype)
                    except (KeyError, TypeError, ValueError):
                        pass
                if pixels is None:
                    pixels = dataset.read_image(**item)
                yield (pixels, float(metadata["XPosition_um_Intended"]),
                       float(metadata["YPosition_um_Intended"]))

    sampling = affine.pixel_size_um if output_pixel_size_um is None else output_pixel_size_um
    assembled = assemble_stage_coordinate_mosaic(
        SelectedFrames(), MosaicGeometry(affine, float(sampling))
    )
    pixels = assembled.pop("mosaic")
    coverage = assembled.pop("coverage_mask")
    if not np.issubdtype(pixels.dtype, np.integer):
        raise ValueError("16-bit TIFF output requires integer source pixels")
    pixels16 = pixels.astype(np.uint16, copy=False)
    pixel_sha256 = hashlib.sha256(pixels16.tobytes(order="C")).hexdigest()
    manifest_path = str(Path(output_path).with_suffix(Path(output_path).suffix + ".json"))
    result = {
        "kind": "stage_coordinate_mosaic",
        "selection": {key: axis_selection[key] for key in sorted(axis_selection)},
        "calibration_identity": calibration_identity,
        **({"calibration_warning": calibration_identity["objective_unrecorded_reason"]}
           if calibration_identity.get("objective_unrecorded") else {}),
        # What the dataset says about itself, recorded alongside what the
        # calibration claims. camera_model_key names which vendor key answered,
        # because that differs per adapter and a future reader cannot infer it.
        "dataset_identity": dataset_identity,
        "calibration_roi_difference": roi_difference,
        "shape": list(pixels16.shape),
        **assembled,
        "coverage_fraction": float(np.count_nonzero(coverage) / coverage.size),
        "overwrite_convention": "later source tiles overwrite earlier source tiles for display",
        "pixel_sha256": pixel_sha256,
        "artifact": {"kind": "tiff", "path": output_path},
        "manifest_path": manifest_path,
    }
    manifest_payload = dict(result)
    payload_bytes = json.dumps(
        manifest_payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    result["manifest_payload_sha256"] = hashlib.sha256(payload_bytes).hexdigest()
    # The digest covers the canonical inner payload without recursively covering
    # itself. External readers can reproduce it directly from manifest_payload.
    manifest_bytes = json.dumps(
        {"manifest_payload": manifest_payload,
         "manifest_payload_sha256": result["manifest_payload_sha256"]},
        sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")

    output_parent = Path(output_path).parent
    output_parent.mkdir(parents=True, exist_ok=True)
    temporary_paths = []
    try:
        with tempfile.NamedTemporaryFile(dir=output_parent, suffix=".tif", delete=False) as handle:
            temporary_tiff = handle.name
        temporary_paths.append(temporary_tiff)
        tifffile.imwrite(temporary_tiff, pixels16)
        with tempfile.NamedTemporaryFile(dir=output_parent, suffix=".json", delete=False) as handle:
            temporary_manifest = handle.name
            handle.write(manifest_bytes)
        temporary_paths.append(temporary_manifest)
        os.replace(temporary_tiff, output_path)
        temporary_paths.remove(temporary_tiff)
        os.replace(temporary_manifest, manifest_path)
        temporary_paths.remove(temporary_manifest)
    finally:
        for path in temporary_paths:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
    return result


@emits_nothing
def run_analysis_on_saved_dataset(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    dataset_path: str,
    adapter: str,
    axis_selection: dict,
    input_kind: str,
    parameters: dict,
    output_dir: str,
    calibration_ref: dict | None = None,
    output_pixel_size_um: float | None = None,
    model_project_config: dict | None = None,
    artifact_limits: dict | None = None,
    max_array_bytes: int = 512 * 1024 * 1024,
) -> dict:
    """Run a reviewed adapter over saved pixels without touching ``ctrl``.

    ``ctrl`` is accepted only because public tools share one dispatcher shape;
    it is deliberately not forwarded to the offline runner or adapter.
    """
    from microclaw.completed_dataset import run_analysis_on_saved_dataset as run
    return run(
        guard, dataset_path, adapter, axis_selection, input_kind, parameters,
        output_dir, calibration_ref=calibration_ref,
        output_pixel_size_um=output_pixel_size_um,
        model_project_config=model_project_config,
        artifact_limits=artifact_limits, max_array_bytes=max_array_bytes,
    )


# --- Image capture with analysis ---

def _parse_quoted_json(value, expected_type):
    """Recover a faithfully quoted structured tool argument."""
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except ValueError:
        return value
    return parsed if isinstance(parsed, expected_type) else value


def _validate_metric_region(
    region: list[int] | None, frame_width: int, frame_height: int
) -> tuple[list[int] | None, str | None]:
    """Validate a software metric crop without changing or clamping it."""
    if region is None:
        return None, None
    if isinstance(region, str):
        # A literal region the model quoted. The Nikon 54c gate measured four
        # consecutive `"[726, 591, 174, 171]"` calls, three of them after the
        # operator asked for an array, all refused as malformed — the capability
        # 54b shipped was unreachable through the agent. A faithful JSON array
        # of four integers says exactly one thing however it is quoted; parse
        # it, and let everything else fall through to the refusals below.
        region = _parse_quoted_json(region, list)
    if not isinstance(region, list) or len(region) != 4:
        return None, (
            f"Malformed region {region!r}: expected four integer values "
            "[x, y, w, h]."
        )
    if any(type(value) is not int for value in region):
        return None, (
            f"Malformed region {region!r}: expected four integer values "
            "[x, y, w, h]."
        )
    x, y, width, height = region
    if x < 0 or y < 0 or width < 0 or height < 0:
        return None, f"Malformed region {region!r}: values must not be negative."
    if width <= 1 or height <= 1:
        return None, (
            f"Degenerate region {region!r}: width and height must both be greater "
            "than 1 pixel."
        )
    if x + width > frame_width or y + height > frame_height:
        return None, (
            f"Region {region!r} does not fit frame "
            f"[{frame_width}, {frame_height}]."
        )
    return list(region), None


def _metric_stamp(
    ctrl: MicroscopeController, region: list[int] | None = None
) -> dict:
    """The settings a focus metric is only comparable within (design/14 §10).

    Split from _focus_metric_payload so a multi-tile result can carry one stamp
    over many metrics: every tile of a grid shares the ROI, exposure, binning and
    software region, so repeating the block per tile would be N copies of one fact.
    """
    try:
        roi = ctrl.core.get_roi()
        roi_list = [int(roi.x), int(roi.y), int(roi.width), int(roi.height)]
    except Exception:
        roi_list = None
    try:
        exposure_ms = round(float(ctrl.core.get_exposure()), 1)
    except Exception:
        exposure_ms = None
    try:
        binning = str(
            ctrl.core.get_property(ctrl.core.get_camera_device(), "Binning")
        )
    except Exception:
        binning = None
    return {
        # "_gated" records that focus_metric now travels with focus_metric_valid
        # and snr (design/25): the presence of a gate is self-describing in a saved
        # history, not silently inferred from whether the extra keys happen to be there.
        "focus_metric_kind": "tenengrad_gated",
        "metric_valid_for": {
            "roi": roi_list,
            "exposure_ms": exposure_ms,
            "binning": binning,
            "region": region,
        },
    }


def _analysis_gate(guard: SafetyGuard) -> tuple[float, str]:
    """Resolve the current rig gate once at a tool boundary."""
    return resolve_min_snr(configured=guard.analysis_min_snr)


def _focus_metric_payload(
    ctrl: MicroscopeController,
    stats: ImageStats,
    min_snr: float,
    min_snr_source: str,
    region: list[int] | None = None,
) -> dict:
    """Focus metric stamped with the settings it is only comparable within, and
    with the SNR gate that says whether it is a measurement at all (design/25).

    A bare float invites exactly the cross-setting comparison the amr_test model
    made — reading a laser-power increase as a focus improvement (design/14 §10);
    a bare float on an EMPTY field invites ranking it as the sharpest tile on the
    grid (design/25). metric_valid_for guards the ROI/exposure/binning
    dependence, and focus_metric_valid guards "is there any signal to be sharp
    about at all?".

    The metric is NOT illumination-normalised (design/36 — the normaliser that
    was supposed to make it so is what inverted it, so a frame's own pixels can
    no longer buy comparability across a laser change). The agent prompt carries
    the illumination half of the domain; metric_valid_for carries the half the
    camera can report.
    """
    payload = {
        "focus_metric": _round_sig(stats.focus_metric),
        "focus_metric_valid": stats.focus_metric_valid,
        "background_level": stats.background_level,
        "snr": stats.snr,
        "snr_valid": stats.snr_valid,
        "snr_invalid_reason": stats.snr_invalid_reason,
        "min_snr": min_snr,
        "min_snr_source": min_snr_source,
        **_metric_stamp(ctrl, region),
    }
    if not stats.snr_valid:
        payload["warning"] = stats.snr_invalid_reason
    elif not stats.focus_metric_valid:
        payload["warning"] = focus_invalid_warning(stats.snr, min_snr)
    return payload


@emits(_emit_snap_and_analyze)
def snap_and_analyze(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    return_thumbnail: bool = False,
    thumbnail_size: int = 512,
    display: bool = True,
    region: list[int] | str | None = None,
) -> list | dict:
    """Snap an image, display it in the MM viewer, and return numerical stats.

    display=True (default) snaps through studio.live().snap(True) so the
    biologist sees the same exposure the stats describe — the old core-only
    path silently never reached the viewer, and the agent told the user
    otherwise (design/14 §7). display=False keeps the snap headless.
    Live view is paused around the snap either way (V1: never probe by calling).
    """
    if region == "drawn":
        try:
            region = ctrl.drawn_region()
        except ValueError as exc:
            return {"error": str(exc)}
    validated, error = _validate_metric_region(
        region,
        int(ctrl.core.get_image_width()),
        int(ctrl.core.get_image_height()),
    )
    if error:
        return {"error": error}
    with _pause_live(ctrl) as live_state:
        image = snap_to_numpy_displayed(ctrl) if display else snap_to_numpy(ctrl)
    # Checked twice, against two different frames. The check above reads the
    # camera, so a bad box costs no exposure; this one reads the array that
    # actually came back, because a second client can change binning or the ROI
    # in between and numpy slicing TRUNCATES rather than raising — the same
    # reason _run_autofocus_passes re-checks inside its metric_fn per frame.
    validated, error = _validate_metric_region(
        validated, image.shape[1], image.shape[0]
    )
    if error:
        return {"error": error}
    if validated is not None:
        x, y, w, h = validated
        image = image[y:y + h, x:x + w]
    min_snr, min_snr_source = _analysis_gate(guard)
    stats = compute_stats(image, min_snr=min_snr)
    text_payload: dict[str, Any] = {
        "z_um": round(ctrl.core.get_position(), 3),
        # Observed, not assumed. This used to echo the `display` parameter, so
        # it said "true" through the whole design/18 first-snap bug while the
        # Preview window sat on its placeholder and the agent told the user
        # their image was on screen. Now it reports whether MM actually has a
        # Preview window open (which snap_to_numpy_displayed has just repainted).
        "displayed_in_mm_viewer": bool(display) and preview_window_open(ctrl),
        **_focus_metric_payload(
            ctrl, stats, min_snr, min_snr_source, validated
        ),
        "mean_intensity": round(stats.mean_intensity, 1),
        "min_intensity": round(stats.min_intensity, 1),
        "max_intensity": round(stats.max_intensity, 1),
        "saturated_fraction": round(stats.saturated_fraction, 6),
        "signal_coverage": round(stats.signal_coverage, 6),
        "structure_coverage": round(stats.structure_coverage, 6),
        "signal_concentration": round(stats.signal_concentration, 6),
    }
    restore = _live_restore_report(live_state)
    if restore:
        text_payload["live_view"] = (
            "paused for the snap; camera sequence restart verified"
            if restore["sequence_running"] is True
            else "paused for the snap; camera sequence restart not verified"
        )
        text_payload["live_view_restore"] = restore
    try:
        pixel_size = float(ctrl.core.get_pixel_size_um())
    except Exception:
        pixel_size = None
    if pixel_size == 0.0:
        # Compose, don't clobber: _focus_metric_payload may already have set a
        # focus-invalid warning (design/25), and dropping it silently to report
        # the pixel-size one would re-open the very "bare number out of context"
        # gap the gate closes.
        pixel_warning = (
            "No pixel-size calibration: image-pixel offsets cannot be "
            "converted to stage µm."
        )
        existing = text_payload.get("warning")
        text_payload["warning"] = f"{existing} {pixel_warning}" if existing else pixel_warning
    if not return_thumbnail:
        return text_payload
    return image_content(text_payload, image, max_size=thumbnail_size)


# --- Stage↔camera calibration (design/14 §8) ---

def _current_objective(ctrl: MicroscopeController) -> str:
    """Best available label for the current optical path (pixel-size config)."""
    try:
        name = str(ctrl.core.get_current_pixel_size_config())
        if name:
            return name
    except Exception:
        pass
    return "default"


def _current_binning(ctrl: MicroscopeController) -> int:
    try:
        raw = str(ctrl.core.get_property(ctrl.core.get_camera_device(), "Binning"))
        return int(raw.split("x")[0])          # "1" or "1x1"
    except Exception:
        return 1


def _load_current_affine(ctrl: MicroscopeController):
    from microclaw.calibration import load_affine

    return load_affine(_current_objective(ctrl), _current_binning(ctrl))


def _calibration_pixel_size_hint(
    ctrl: MicroscopeController, pixel_size_hint_um: float | None
) -> float | None:
    """The pixel size to scale the calibration step against: the caller's hint,
    else MM's configured value, else None (no scaling possible)."""
    if pixel_size_hint_um:
        return float(pixel_size_hint_um)
    try:
        mm_px = float(ctrl.core.get_pixel_size_um())
    except Exception:
        return None
    return mm_px if mm_px > 0 else None


def _diagnose_calibration_shift(
    shift, frame_hw: tuple[int, int], step_um: float, px_hint: float | None,
) -> str | None:
    """Name WHY one axis failed to register, or None if it registered cleanly.

    Splits the single "degenerate" verdict (design/28 F4) into actionable cases
    the model can key on: the move overran the FOV (reduce step), or it moved the
    image too little to measure (increase step). The diagnosis is GEOMETRIC — the
    RMS `error` phase_cross_correlation returns was empirically ~1.0 even for a
    clean registration on these fields, so it is not a usable reliability signal;
    the commanded step vs the known FOV, and the measured shift magnitude, are.
    The residual "featureless / periodic field" case (shifts present but
    linearly dependent) is caught by solve_affine's determinant backstop.
    """
    h, w = frame_hw
    frame = min(h, w)
    mag = float(np.hypot(shift[1], shift[0]))
    expected_px = (step_um / px_hint) if px_hint else None

    # Overlap too small: geometry says the commanded move exceeds ~a third of
    # the frame, or the measured shift is already half a frame. On truly
    # non-overlapping frames phase_cross_correlation aliases to a small shift,
    # so the geometric expectation is the more reliable tell when px is known.
    if (expected_px is not None and expected_px >= frame / 3) or mag >= frame / 2:
        detail = (
            "expected ~%.0f px shift" % expected_px
            if expected_px is not None else "no pixel size to scale against"
        )
        return (
            f"step_um={step_um:g} is too large for this FOV (~{frame} px, "
            f"{detail}): the move left too little overlap to register. Reduce "
            f"step_um (≈¼ of the smaller FOV dimension) or clear the ROI to image "
            f"the full sensor."
        )
    if np.isfinite(mag) and mag < 0.5:
        return (
            f"the commanded move produced no image shift at all (|shift| {mag:.2f} px). "
            "A merely small step produces a small shift, not none; this is the "
            "signature of a stationary feature dominating correlation (for example "
            "a vignette rim, sensor dirt, or fixed reflection). Crop to the illuminated "
            "centre and retry before changing step_um."
        )
    if not np.isfinite(mag) or mag < 1.0:
        return (
            f"the stage move produced no measurable image shift (|shift| "
            f"{mag:.2f} px) — step_um={step_um:g} is too small to move the image, "
            f"or the field is featureless. Increase step_um or snap a contrasty "
            f"field."
        )
    return None


def calibrate_stage_to_camera(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    step_um: float | None = None,
    pixel_size_hint_um: float | None = None,
) -> dict:
    """Snap, move a known ΔX, snap, cross-correlate; repeat for ΔY. ~4 snaps.

    Solves the 2×2 stage↔camera affine — pixel size, camera rotation, and both
    axis flips — instead of asking the model to infer sign conventions from
    thumbnails (design/14 §8). Cached in the knowledge base per
    (objective, binning); it is a property of the optical path, not the session.

    step_um defaults to ¼ of the smaller FOV dimension when a pixel size is known
    (the caller's pixel_size_hint_um, else MM's configured value), keeping ~75%
    overlap between the two snaps. A fixed 20 µm step on a ~19 µm cropped ROI
    translated the scene entirely out of frame — zero overlap — and the aliased
    near-zero shift was misread as a "degenerate/featureless" field (design/28
    F4). With no pixel size to scale against, step_um falls back to 20 µm.
    """
    from skimage.registration import phase_cross_correlation
    from microclaw.calibration import affine_version_key, save_affine, solve_affine

    px_hint = _calibration_pixel_size_hint(ctrl, pixel_size_hint_um)
    x0, y0 = ctrl.core.get_x_position(), ctrl.core.get_y_position()

    with _pause_live(ctrl, restore=False) as live_state:
        # Snap the reference first so the step can be scaled to the ACTUAL frame
        # (ROI-cropped or full sensor); no stage move has happened yet.
        ref = snap_to_numpy(ctrl)
        frame_hw = (int(ref.shape[0]), int(ref.shape[1]))
        if step_um is None:
            step_um = round(0.25 * px_hint * min(frame_hw), 3) if px_hint else 20.0

        # Guard both excursions before the first move.
        guard.check_xy(x0 + step_um, y0)
        guard.check_xy(x0, y0 + step_um)

        move_stage_xy(ctrl, guard, step_um, 0, absolute=False)
        img_x = snap_to_numpy(ctrl)
        move_stage_xy(ctrl, guard, -step_um, 0, absolute=False)

        move_stage_xy(ctrl, guard, 0, step_um, absolute=False)
        img_y = snap_to_numpy(ctrl)
        move_stage_xy(ctrl, guard, 0, -step_um, absolute=False)

    live_report = _live_restore_report(live_state)
    live_payload = {"live_view_restore": live_report} if live_report else {}

    # phase_cross_correlation returns (row, col) = (dy_px, dx_px).
    shift_x, _, _ = phase_cross_correlation(ref, img_x, upsample_factor=10)
    shift_y, _, _ = phase_cross_correlation(ref, img_y, upsample_factor=10)

    for axis, shift in (("X", shift_x), ("Y", shift_y)):
        why = _diagnose_calibration_shift(shift, frame_hw, step_um, px_hint)
        if why is not None:
            return {"error": f"Calibration failed on the {axis} move: {why}",
                    "step_um": step_um, "frame_px": list(frame_hw), **live_payload}

    try:
        affine = solve_affine(
            (float(shift_x[0]), float(shift_x[1])),
            (float(shift_y[0]), float(shift_y[1])),
            step_um,
            objective=_current_objective(ctrl),
            binning=_current_binning(ctrl),
        )
    except ValueError as e:
        return {"error": f"Calibration failed: {e}", **live_payload}

    try:
        camera_device = str(ctrl.core.get_camera_device())
        if not camera_device:
            raise ValueError("camera device is empty")
        camera_model = str(ctrl.core.get_device_name(camera_device))
        if not camera_model:
            raise ValueError("camera model is empty")
        roi_value = ctrl.core.get_roi()
        roi = [int(roi_value.x), int(roi_value.y),
               int(roi_value.width), int(roi_value.height)]
        if roi[2] <= 0 or roi[3] <= 0:
            raise ValueError(f"camera ROI has invalid geometry {roi}")
    except Exception as error:
        return {
            "error": (
                "Calibration measured but not saved: complete camera device, "
                f"model, and ROI identity could not be read ({error})."
            ),
            **live_payload,
        }
    key = save_affine(
        affine, camera_device=camera_device, camera_model=camera_model, roi=roi,
    )
    from dataclasses import asdict
    return {
        **asdict(affine),
        "n_snaps": 4,
        "step_um": step_um,
        "frame_px": list(frame_hw),
        "knowledge_key": key,
        "calibration_ref": {"kind": "knowledge_version",
                            "key": affine_version_key(affine)},
        "status": (
            "Calibrated and cached. Image-pixel offsets can now be converted "
            "to stage µm (find_features reports offset_from_center_um)."
        ),
        **live_payload,
    }


# --- Feature detection and centring (design/14 §9) ---

def find_features(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    min_sigma: float = 1.0,
    max_sigma: float = 4.0,
    threshold_rel: float = 0.15,
) -> dict:
    """Snap and return spot count, intensity-weighted centroid, and its offset
    from the field centre — in pixels always, in µm when calibrated."""
    with _pause_live(ctrl) as live_state:
        image = snap_to_numpy(ctrl)
    out = detect_features(image, min_sigma, max_sigma, threshold_rel)
    out["detector_scope"] = (
        "Puncta detector: an extended or filamentous field can contain strong "
        "signal and still score low here."
    )
    live_report = _live_restore_report(live_state)
    if live_report:
        out["live_view_restore"] = live_report

    if out["offset_from_center_px"] is not None:
        affine = _load_current_affine(ctrl)
        if affine is not None:
            off_x, off_y = out["offset_from_center_px"]
            dx_um, dy_um = affine.px_to_um(off_x, off_y)
            out["offset_from_center_um"] = [round(dx_um, 2), round(dy_um, 2)]
        else:
            out["note"] = (
                "No stage-camera calibration for the current objective/binning; "
                "offsets are pixels only. Run calibrate_stage_to_camera()."
            )

    try:
        px = float(ctrl.core.get_pixel_size_um())
    except Exception:
        px = 0.0
    if px > 0:
        h, w = image.shape[:2]
        # Doubles as the SMLM blinking-density check (spots per µm²).
        out["spot_density_per_um2"] = round(out["n_spots"] / (h * w * px * px), 4)
    return out


def center_feature(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    max_iter: int = 3,
    tol_px: float = 5.0,
) -> dict:
    """Closed loop: find_features → pixel offset → affine → stage move → repeat.

    Turns "centre the cell in the ROI" from a guess-shift-resnap conversation
    into arithmetic. Requires calibrate_stage_to_camera to have run for the
    current objective/binning; every stage move passes the XY guard.
    """
    affine = _load_current_affine(ctrl)
    if affine is None:
        return {
            "error": (
                "No stage-camera calibration for the current objective/binning. "
                "Run calibrate_stage_to_camera() first."
            )
        }

    residual = None
    for i in range(max_iter + 1):
        feats = find_features(ctrl, guard)
        residual = feats["offset_from_center_px"]
        if residual is None:
            return {
                "error": "No signal above background — nothing to centre.",
                "iterations": i,
            }
        if math.hypot(*residual) <= tol_px:
            return {"centered": True, "iterations": i, "residual_px": residual}
        if i == max_iter:
            break
        dx_um, dy_um = affine.px_to_um(residual[0], residual[1])
        move_stage_xy(ctrl, guard, -dx_um, -dy_um, absolute=False)

    return {
        "centered": False,
        "iterations": max_iter,
        "residual_px": residual,
        "hint": (
            "Residual did not fall below tol_px. If it GREW between iterations, "
            "the calibration may be stale — rerun calibrate_stage_to_camera."
        ),
    }


# --- Autofocus (Form A — standalone) ---

def _metric_pixel_count(ctrl, region: list[int] | None) -> int:
    """Pixels the focus metric is averaged over — the region, or the live frame.

    The flat-curve guard scales with this, and it must not care HOW the frame
    got small. A camera ROI cropped to 32x32 in Micro-Manager averages the
    metric over exactly as few pixels as a 32x32 software region, and reaches
    the same noise floor; reading the live frame rather than assuming a
    reference size is what makes both routes refuse.
    """
    if region is not None:
        return int(region[2]) * int(region[3])
    return int(ctrl.core.get_image_width()) * int(ctrl.core.get_image_height())


def _run_autofocus_passes(
    ctrl: MicroscopeController,
    z_range_um: float,
    z_step_um: float,
    method: str,
    settle_ms: int,
    region: list[int] | None = None,
    probe_device: str | None = None,
    probe_property: str | None = None,
    in_focus_values: list[str] | None = None,
    stop_when_found: bool = False,
    z_min_um: float | None = None,
    z_max_um: float | None = None,
    dwell_ms: float | None = None,
) -> AutofocusResult:
    metric_fn = tenengrad
    min_contrast = (MIN_CONTRAST if probe_device is not None else
                    contrast_threshold(_metric_pixel_count(ctrl, region)))
    if region is not None:
        x, y, width, height = region

        def metric_fn(image):
            # Re-checked per frame rather than once before the sweep, because
            # numpy slicing TRUNCATES instead of raising: a frame smaller than
            # the region would score the metric over whatever pixels exist and
            # report it as if nothing were wrong. This function is inlined into
            # the exported script, which carries no other check —
            # _validate_metric_region lives in run_autofocus, and run_autofocus
            # is not emitted.
            if image.shape[0] < y + height or image.shape[1] < x + width:
                raise RuntimeError(
                    f"Region {[x, y, width, height]} does not fit frame "
                    f"[{image.shape[1]}, {image.shape[0]}]."
                )
            return tenengrad(image[y:y + height, x:x + width])
    probe = None
    if probe_device is not None:
        entry_z = float(ctrl.core.get_position())
        lo_um = entry_z - z_range_um / 2 if z_min_um is None else z_min_um
        hi_um = entry_z + z_range_um / 2 if z_max_um is None else z_max_um
        probe = property_probe(
            ctrl.core, probe_device, probe_property, in_focus_values,
            step_um=z_step_um,
            lo_um=lo_um, hi_um=hi_um,
            dwell_s=None if dwell_ms is None else dwell_ms / 1000.0,
            stop_when_found=stop_when_found,
        )
    else:
        probe = image_probe(ctrl, metric_fn, region, min_contrast)
    if method == "coarse_then_fine":
        return coarse_then_fine_autofocus(
            ctrl, z_range_um, max(z_step_um * 5, 1.0), z_step_um, settle_ms,
            metric_fn=metric_fn, min_contrast=min_contrast, probe=probe,
            z_min_um=z_min_um, z_max_um=z_max_um,
        )
    return single_sweep_autofocus(
        ctrl, z_range_um, z_step_um, settle_ms, metric_fn=metric_fn,
        min_contrast=min_contrast, probe=probe,
        z_min_um=z_min_um, z_max_um=z_max_um,
    )


def _round_sig(value: float, sig: int = 4) -> float:
    """Round to significant figures, not decimal places.

    The normalized focus metric lives at 1e-2..1e-4, where a fixed round(v, 2)
    collapses an entire focus curve to zeros while `contrast` still reports a
    real peak — instrumentation lying to the model, which is the whole point of
    design/14. Fixed-decimal rounding was safe only for the old raw metric's
    ~1e4 scale.
    """
    if not math.isfinite(value) or value == 0.0:
        return float(value)
    return float(f"%.{sig}g" % value)


def _sweep_payload(sweep, min_contrast: float | None = None,
                   probe: FocusProbe | None = None) -> dict | None:
    if sweep is None:
        return None
    payload = {
        "z_positions": [round(z, 3) for z in sweep.z_positions],
        "measured_z_positions": [
            round(z, 3) for z in sweep.measured_z_positions
        ],
        "best_z_um": round(sweep.best_z_um, 3),
    }
    # peak_interior answers "is the chosen plane away from a sweep boundary",
    # which only means anything about a curve that was swept to its end. An
    # early-stopped sweep stops BECAUSE it found the target, so the chosen plane
    # is always the last row of the table it returns: reporting True contradicts
    # the table, and reporting False would read as design/28 F1's edge-peak
    # failure. Omit it and say the stopping rule instead.
    if getattr(sweep, "stopped_early", False):
        payload["stopping_rule"] = (
            "stopped at the first in-focus plane; peak_interior does not apply"
        )
    else:
        payload["peak_interior"] = sweep.peak_interior
    if probe is not None and probe.exposures_per_plane == 0 and probe.in_focus_values:
        payload["readings"] = list(sweep.metric_values)
        payload["in_range"] = [value in probe.in_focus_values
                               for value in sweep.metric_values]
        payload["unsettled_planes"] = list(sweep.unsettled_indices)
        payload["stopped_early"] = sweep.stopped_early
        payload["planes_read"] = len(sweep.metric_values)
        payload["planes_planned"] = sweep.planes_planned
    else:
        payload["metric_curve"] = [_round_sig(v) for v in sweep.metric_values]
        payload["contrast"] = round(curve_contrast(sweep.metric_values), 3)
        if min_contrast is not None:
            payload["min_contrast"] = round(min_contrast, 3)
    return payload


@emits(_emit_autofocus)
def run_autofocus(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    z_range_um: float | None = None,
    z_step_um: float | None = None,
    method: str = "coarse_then_fine",
    settle_ms: int = 50,
    return_thumbnail: bool = True,
    region: list[int] | str | None = None,
    probe: dict | str | None = None,
    z_min_um: float | None = None,
    z_max_um: float | None = None,
) -> list | dict:
    """Sweep Z to find the sharpest focal plane.

    Reports BOTH passes (the coarse pass chooses the plane; the old payload
    showed only the fine curve — design/14 §4), refuses to move the stage on a
    structureless metric curve OR a peak pinned at the sweep edge (design/28 F1),
    and always reports entry_z_um so a bad result is trivially undone.

    The metric is Tenengrad (design/36): maximised at focus, and
    polarity-insensitive, so bright puncta on a dark field do not require an
    inverted or separately selected metric. It replaced a normalized Laplacian
    variance that was MINIMISED at focus on real fields, which is what made two
    live sessions chase the sweep boundary away from the operator's own focus.

    The sweep is headless: live view is paused for its duration and left off
    afterwards, and the viewer does not show the sweep as it happens.
    """
    explicit_window = z_min_um is not None or z_max_um is not None
    if z_range_um is not None and explicit_window:
        return {"error": "Supply either z_range_um or z_min_um/z_max_um, not both forms."}
    if explicit_window and (z_min_um is None or z_max_um is None):
        return {"error": "An explicit window requires both z_min_um and z_max_um."}
    if z_range_um is None and not explicit_window:
        return {"error": "Supply z_range_um or both z_min_um and z_max_um."}
    if z_step_um is None:
        return {"error": "z_step_um is required."}
    if explicit_window and z_min_um >= z_max_um:
        return {"error": "z_min_um must be less than z_max_um."}
    probe = _parse_quoted_json(probe, dict)
    if probe is not None:
        if not isinstance(probe, dict):
            return {"error": f"Malformed probe {probe!r}: expected an object."}
        if set(probe) - {"device", "property", "in_focus_values", "stop_when_found", "dwell_ms"}:
            return {"error": "Malformed probe: expected only device, property, in_focus_values, stop_when_found, and dwell_ms."}
        if not isinstance(probe.get("device"), str) or not isinstance(probe.get("property"), str):
            return {"error": "Malformed probe: device and property must be strings."}
        values = probe.get("in_focus_values")
        if values is not None and (not isinstance(values, list) or
                                   not values or
                                   any(not isinstance(v, str) for v in values)):
            return {"error": "Malformed probe: in_focus_values must be a non-empty array of strings."}
        if "stop_when_found" in probe and not isinstance(probe["stop_when_found"], bool):
            return {"error": "Malformed probe: stop_when_found must be a boolean."}
        dwell_ms = probe.get("dwell_ms")
        if dwell_ms is not None and (isinstance(dwell_ms, bool)
                                     or not isinstance(dwell_ms, (int, float))
                                     or dwell_ms < 0):
            return {"error": "Malformed probe: dwell_ms must be a non-negative number."}
        if "stop_when_found" in probe and values is None:
            return {
                "error": (
                    "stop_when_found does not apply to a numeric probe: it has "
                    "no in_focus_values target state to stop on."
                )
            }
        probe["stop_when_found"] = probe.get("stop_when_found", values is not None)
        if method == "coarse_then_fine":
            return {
                "error": (
                    "A property probe requires method='sweep'. The coarse pass "
                    "exists to save exposures, and a property read spends no "
                    "exposures; its coarser step can skip the capture band."
                )
            }
        if region is not None:
            return {
                "error": (
                    "A probe reads a device property, so a focus-metric region "
                    "does not apply. Omit region or omit probe."
                )
            }
    if region == "drawn":
        try:
            region = ctrl.drawn_region()
        except ValueError as exc:
            return {"error": str(exc)}
    validated, error = ((None, None) if probe is not None else
                        _validate_metric_region(
                            region, int(ctrl.core.get_image_width()),
                            int(ctrl.core.get_image_height())))
    if error:
        return {"error": error}

    entry_z = ctrl.core.get_position()
    lo_um = entry_z - z_range_um / 2 if z_min_um is None else z_min_um
    hi_um = entry_z + z_range_um / 2 if z_max_um is None else z_max_um
    effective_range_um = hi_um - lo_um
    guard.check_z(lo_um)
    guard.check_z(hi_um)

    # A sweep against an engaged focus lock fights the piezo servo loop — a
    # candidate cause of the flat, structureless curve in amr_test (§5).
    lock = get_focus_lock_state(ctrl, guard)
    if lock.get("engaged"):
        return {
            "error": (
                f"Focus lock is engaged ({lock['property']}); a Z sweep would "
                f"fight the servo loop and produce a meaningless metric curve. "
                f"Call set_focus_lock(enabled=false) first, then re-engage it "
                f"after focusing."
            ),
            "focus_lock": lock,
        }

    with _pause_live(ctrl, restore=False) as live_state:
        try:
            result = _run_autofocus_passes(
                ctrl, effective_range_um, z_step_um, method, settle_ms, validated,
                probe.get("device") if probe else None,
                probe.get("property") if probe else None,
                probe.get("in_focus_values") if probe else None,
                probe.get("stop_when_found", False) if probe else False,
                z_min_um=z_min_um, z_max_um=z_max_um,
                dwell_ms=probe.get("dwell_ms") if probe else None,
            )
        except ValueError as exc:
            return {"error": str(exc)}

    # The same number the sweep compared against, from the same helper — the
    # payload and the refusal must not be able to disagree. Reported only when
    # it is not the default, so an ordinary full-frame payload keeps its shape;
    # absent means MIN_CONTRAST, the same convention `region` uses.
    active_probe = None
    if probe:
        try:
            active_probe = property_probe(
                ctrl.core, probe["device"], probe["property"],
                probe.get("in_focus_values"), step_um=z_step_um,
                lo_um=lo_um, hi_um=hi_um,
                dwell_s=(None if probe.get("dwell_ms") is None
                         else probe["dwell_ms"] / 1000.0),
                stop_when_found=probe.get("stop_when_found", False),
            )
        except ValueError as exc:
            return {"error": str(exc)}
    applied_min_contrast = (None if active_probe else contrast_threshold(
        _metric_pixel_count(ctrl, validated)
    ))
    if applied_min_contrast == MIN_CONTRAST:
        applied_min_contrast = None

    payload: dict[str, Any] = {
        "converged": result.converged,
        "moved": result.moved,
        "reason": result.reason,
        "entry_z_um": round(result.entry_z_um, 3),
        "final_z_um": round(result.final_z_um, 3),
        "z_range_um": z_range_um,
        # BOTH passes — the caller can see which one chose the plane.
        "coarse": _sweep_payload(result.coarse, applied_min_contrast, active_probe),
        "fine": _sweep_payload(result.fine, applied_min_contrast, active_probe),
        "warning": (
            "Peak focus was at the edge of the sweep range; consider widening z_range_um."
            if result.converged and not result.coarse.peak_interior
            else None
        ),
    }
    if explicit_window:
        payload["z_min_um"] = z_min_um
        payload["z_max_um"] = z_max_um
    if active_probe is not None and active_probe.in_focus_values:
        payload["stopped_early"] = result.coarse.stopped_early
        payload["planes_read"] = len(result.coarse.metric_values)
        payload["planes_planned"] = result.coarse.planes_planned
    if active_probe is None:
        active_probe = image_probe(
            ctrl, tenengrad, validated, applied_min_contrast or MIN_CONTRAST
        )
    criterion = active_probe.describe
    exposures_per_plane = active_probe.exposures_per_plane if active_probe else 1
    payload["criterion"] = criterion
    payload["exposures_spent"] = exposures_per_plane * sum(
        len(s.z_positions) for s in (result.coarse, result.fine) if s is not None
    )
    if exposures_per_plane == 0:
        # From the probe that ran, not re-derived: the default follows the
        # stopping rule now, and a payload that recomputed it could disagree.
        payload["property_dwell_ms"] = round(active_probe.dwell_s * 1000)
        payload["convergence_means"] = "criterion satisfied; not proof the sample is in focus"
        payload["thumbnail_suppressed"] = (
            "Property-probe autofocus spends zero exposures; focus_metric_at_final "
            "and return_thumbnail were suppressed."
        )
    # Present only when a region was used, so a regionless payload keeps its
    # shape. Every number above — both metric curves, contrast, and
    # focus_metric_at_final below — is measured over these pixels, and
    # run_autofocus carries no metric_valid_for block to say so otherwise.
    if validated is not None:
        payload["region"] = validated
    live_report = _live_restore_report(live_state)
    if live_report:
        payload["live_view_restore"] = live_report

    # Invariant that would have surfaced the amr_test bug immediately: the
    # first pass must span the requested window around the entry Z.
    swept_lo = result.coarse.z_positions[0]
    swept_hi = swept_lo + effective_range_um
    if explicit_window:
        assert abs(swept_lo - lo_um) <= 1e-6 and abs(swept_hi - hi_um) <= 1e-6, (
            "autofocus sweep did not span the requested window"
        )
    else:
        assert swept_lo - 1e-6 <= result.entry_z_um <= swept_hi + 1e-6, (
            "autofocus sweep window does not contain the entry Z"
        )

    if not return_thumbnail or exposures_per_plane == 0:
        return payload

    payload["exposures_spent"] += 1

    with _pause_live(ctrl, restore=False):
        image = snap_to_numpy(ctrl)
    metric_image = image
    if validated is not None:
        x, y, w, h = validated
        metric_image = image[y:y + h, x:x + w]
    payload["focus_metric_at_final"] = _round_sig(tenengrad(metric_image))
    return image_content(payload, image)


# --- Position management ---

# Position-list operations (including mark_position) emit nothing because the
# list is session state, not a standalone hardware-routine action. They are also
# inputs to named-position resolution: the exporter replays snapshots and deltas
# so a later acquisition carries the coordinates current at that point.
# _emit_multiposition refuses the export if it cannot recover the complete set.
# Keep these classifications coupled to that fail-closed guard: weakening it
# would silently turn a marked session into a positionless/partial script.

def _preflight_native_positions(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    *,
    preserve_unsupported: bool = False,
) -> tuple[PositionProjection | None, dict | None]:
    """Refresh from MM and stop on conflicts before a position operation."""
    projection = _validate_position_projection(
        ctrl.inspect_current_position_list(), guard
    )
    if preserve_unsupported:
        projection = PositionProjection(
            projection.positions,
            projection.native_entries,
            [i for i in projection.issues if i.get("code") != "unsupported_only"],
        )
    if projection.issues:
        conflict = _position_conflict(projection)
        conflict["position_list_conflict"].update({
            "source": "pre-existing entries, not positions this call would add",
            "hint": (
                "Resolve or clear the listed pre-existing entries; proposed positions "
                "from this call were not evaluated or written."
            ),
        })
        return None, conflict
    ctrl.set_position_projection(projection)
    return projection, None

@emits_nothing
def mark_position(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    name: str,
    include_z: bool = True,
    x_um: float | None = None,
    y_um: float | None = None,
    z_um: float | None = None,
    preserve_unsupported: bool = False,
) -> dict:
    """Record a position in microclaw's list and MM's GUI list.

    With x_um/y_um: records that KNOWN coordinate WITHOUT moving the stage and
    WITHOUT imaging. Without them: the current stage position, as before. The
    second form is why Episode B cost 16 exposures (design/23) — the only zero-move
    way to write a known coordinate into the list was an acquisition tool's
    mark_positions=True flag, and that flag images.
    """
    _, conflict = _preflight_native_positions(
        ctrl, guard, preserve_unsupported=preserve_unsupported
    )
    if conflict:
        return conflict
    if (x_um is None) != (y_um is None):
        return {"error": "Provide both x_um and y_um, or neither."}
    supplied = x_um is not None
    if supplied:
        x, y = float(x_um), float(y_um)
        z = float(z_um) if z_um is not None else None
    else:
        x = float(ctrl.core.get_x_position())
        y = float(ctrl.core.get_y_position())
        z = float(ctrl.core.get_position()) if include_z else None
    # Supplied coordinates are unvalidated caller input, unlike the current stage
    # position, which is reachable by definition. Guard BEFORE anything is written.
    guard.check_xy(x, y)
    if z is not None:
        guard.check_z(z)
    ctrl.add_position(name, x, y, z)
    return {
        "status": f"Position '{name}' marked (visible in MM's Position List Manager).",
        "x_um": x,
        "y_um": y,
        **({"z_um": z} if z is not None else {}),
        # In the payload the agent actually reads: marking is free.
        "imaged": False,
        "stage_moved": not supplied,
    }


@emits_nothing
def get_position_list(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """Return all positions from MM's native position list."""
    projection = _validate_position_projection(
        ctrl.inspect_current_position_list(), guard
    )
    result = {"positions": projection.positions, "count": len(projection.positions)}
    if projection.issues:
        result["position_list_conflict"] = {"issues": projection.issues}
    else:
        ctrl.set_position_projection(projection)
    return result


@emits(_emit_go_to_position)
def go_to_position(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    name: str,
    preserve_unsupported: bool = False,
) -> dict:
    """Move the stage to a named position from MM's native position list."""
    projection, conflict = _preflight_native_positions(
        ctrl, guard, preserve_unsupported=preserve_unsupported
    )
    if conflict:
        return conflict
    positions = {p["name"]: p for p in projection.positions}
    if name not in positions:
        return {"error": f"Position '{name}' not found."}
    pos = positions[name]
    guard.check_xy(pos["x_um"], pos["y_um"])
    if "z_um" in pos:
        guard.check_z(pos["z_um"])
    ctrl.go_to_position(name)
    return {"status": f"Moved to '{name}'.", **pos}


@emits_nothing
def delete_position(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    name: str,
    confirm_conflict_resolution: bool = False,
) -> dict:
    """Delete a named position from MM's native position list."""
    _, conflict = _preflight_native_positions(ctrl, guard)
    if conflict:
        if not confirm_conflict_resolution:
            return conflict
        issues = conflict["position_list_conflict"]["issues"]
        affected = [i for i in issues if i.get("label") == name]
        if not affected:
            return conflict
        if any(i.get("code") == "duplicate_label" for i in affected):
            return {
                **conflict,
                "error": (
                    f"Position label {name!r} is duplicated; delete is ambiguous. "
                    "Repair the list in Micro-Manager or clear it after confirmation."
                ),
            }
        if not CONFIRM_FN(
            f"Remove conflicted native position {name!r} from Micro-Manager's list?"
        ):
            return {"status": "Position deletion cancelled."}
        ctrl.remove_native_position(name)
        return {"status": f"Conflicted position '{name}' deleted from MM position list."}
    ctrl.remove_position(name)
    return {"status": f"Position '{name}' deleted from MM position list."}


@emits_nothing
def clear_position_list(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    confirm_conflict_resolution: bool = False,
) -> dict:
    """Clear all positions from MM's native position list."""
    _, conflict = _preflight_native_positions(ctrl, guard)
    if conflict:
        if not confirm_conflict_resolution:
            return conflict
        if not CONFIRM_FN(
            "Clear the entire inconsistent native Micro-Manager position list?"
        ):
            return {"status": "Position-list clear cancelled."}
    ctrl.clear_positions()
    return {"status": "Position list cleared."}


@emits_nothing
def save_position_list(ctrl: MicroscopeController, guard: SafetyGuard, path: str) -> dict:
    """Save MM's current native position list to a `.pos` file."""
    if not path.lower().endswith(".pos"):
        path += ".pos"
    path = guard.resolve_in_workspace(path)
    projection = ctrl.save_position_list(path)
    # The `artifact` key is for the transcript renderer, which draws a download
    # chip from it. Saying so structurally beats regexing paths out of `status`:
    # that works for six months and then matches a filename in an error message.
    result = {"status": f"Position list saved to {path}.",
              "artifact": {"kind": "position_list", "path": path}}
    if isinstance(projection, PositionProjection):
        checked = _validate_position_projection(projection, guard)
        if checked.issues:
            result["position_list_conflict"] = {"issues": checked.issues}
        else:
            ctrl.set_position_projection(checked)
    return result


def _validate_position_projection(
    projection: PositionProjection, guard: SafetyGuard
) -> PositionProjection:
    """Return projection plus safety issues, without mutating either list."""
    issues = list(projection.issues)
    for i, p in enumerate(projection.positions):
        try:
            if "x_um" in p and "y_um" in p:
                guard.check_xy(p["x_um"], p["y_um"])
            if "z_um" in p:
                guard.check_z(p["z_um"])
        except SafetyViolation as e:
            issues.append({
                "code": "unsafe_coordinate", "index": i, "label": p["name"],
                "details": str(e), "allowed_resolutions": ["cancel", "remove_entry"],
            })
    return PositionProjection(projection.positions, projection.native_entries, issues)


def _position_conflict(
    projection: PositionProjection,
    *,
    path: str | None = None,
    content_hash: str | None = None,
) -> dict:
    payload: dict = {"issues": projection.issues}
    if path is not None:
        payload["path"] = path
    if content_hash is not None:
        payload["content_hash"] = content_hash
    return {
        "error": "Position list requires user resolution; no changes were published.",
        "position_list_conflict": payload,
    }


@emits_nothing
def load_position_list(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    path: str,
    preserve_unsupported: bool = False,
    remove_conflicting_indexes: list[int] | None = None,
    expected_content_hash: str | None = None,
) -> dict:
    """Transactionally load a native Micro-Manager position-list file."""
    path = guard.resolve_readable_path(path)  # local read; workspace confines writes/serves
    try:
        prepared = ctrl.prepare_position_list(path)
    except PositionListConflict as e:
        projection = PositionProjection([], [], e.issues)
        return _position_conflict(projection, path=e.path, content_hash=e.content_hash)
    if expected_content_hash is not None and prepared.content_hash != expected_content_hash:
        changed = PositionProjection([], [], [{
            "code": "file_changed_since_review",
            "details": "The position-list file changed after the reported conflict.",
            "allowed_resolutions": ["retry", "cancel"],
        }])
        return _position_conflict(
            changed, path=prepared.path, content_hash=prepared.content_hash
        )
    if remove_conflicting_indexes:
        if expected_content_hash is None:
            return {"error": "expected_content_hash is required to remove file entries."}
        indexes = sorted(set(remove_conflicting_indexes), reverse=True)
        n = int(prepared.candidate.get_number_of_positions())
        if any(not isinstance(i, int) or i < 0 or i >= n for i in indexes):
            return {"error": f"Removal indexes must be integers from 0 to {n - 1}."}
        if not CONFIRM_FN(
            f"Load {path} after removing native position indexes {sorted(indexes)}?"
        ):
            return {"status": "Position-list conflict resolution cancelled."}
        for index in indexes:
            prepared.candidate.remove_position(index)
        projection = ctrl.project_position_list(prepared.candidate)
        prepared = type(prepared)(
            prepared.candidate, projection, prepared.path, prepared.content_hash
        )
    projection = _validate_position_projection(prepared.projection, guard)
    if preserve_unsupported:
        projection = PositionProjection(
            projection.positions, projection.native_entries,
            [i for i in projection.issues if i.get("code") != "unsupported_only"],
        )
    if projection.issues:
        return _position_conflict(
            projection, path=prepared.path, content_hash=prepared.content_hash
        )
    ctrl.commit_position_list(prepared)
    return {
        "status": f"Loaded {len(projection.positions)} positions from {path}.",
        "count": len(projection.positions),
    }


@emits_nothing
def import_mm_positions(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    preserve_unsupported: bool = False,
) -> dict:
    """Import positions from MM's GUI position list into the agent's internal store.

    Use this after the user has set up positions in Micro-Manager's Position List
    Manager. The imported positions will be available for all acquisition tools.
    """
    projection = _validate_position_projection(
        ctrl.inspect_current_position_list(), guard
    )
    if preserve_unsupported:
        projection = PositionProjection(
            projection.positions, projection.native_entries,
            [i for i in projection.issues if i.get("code") != "unsupported_only"],
        )
    if projection.issues:
        return _position_conflict(projection)
    ctrl.set_position_projection(projection)
    names = [p["name"] for p in projection.positions]
    return {
        "status": f"Imported {len(names)} position(s) from MM.",
        "imported": names,
        "total": len(projection.positions),
    }


# --- Multiposition acquisition ---

def _protocol_shape_kwargs(protocol: str, params: dict) -> dict:
    """protocol_params (tool-facing) -> multi_d_acquisition_events kwargs."""
    if protocol == "zstack":
        return {"z_start": params["z_start_um"], "z_end": params["z_end_um"],
                "z_step": params["z_step_um"]}
    if protocol == "timelapse":
        return {"num_time_points": params["n_frames"],
                "time_interval_s": params.get("interval_s", 0)}
    raise ValueError(f"Unknown protocol '{protocol}'.")


def _plan_protocol_repetitions(
    ctrl: MicroscopeController, protocol: str, params: dict, repetitions: int
) -> AcquisitionPlan:
    """Bound a repeated per-position protocol before the first stage move."""
    if repetitions <= 0:
        raise SafetyViolation("Acquisition has no valid positions to acquire.")
    shape = _protocol_shape_kwargs(protocol, params)
    exposure_ms = params.get("exposure_ms")
    one = plan_events(
        ctrl,
        _build_acquisition_events(
            channel=params.get("channel"), exposure_ms=exposure_ms, **shape
        ),
        exposure_ms,
    )
    return AcquisitionPlan(
        one.frames * repetitions,
        one.exposure_ms_per_frame,
        one.estimated_duration_s * repetitions,
        one.estimated_bytes * repetitions,
    )


def _run_protocol_at(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    pos_label: str,
    x_um: float,
    y_um: float,
    z_um: float | None,
    protocol: str,
    pos_save_dir: str | None,
    params: dict,
    mark_position_in_list: bool = False,
    reservation: Reservation | None = None,
) -> dict:
    guard.check_xy(x_um, y_um)
    ctrl.core.set_xy_position(x_um, y_um)
    _wait(ctrl, ctrl.core.get_xy_stage_device())
    if z_um is not None:
        guard.check_z(z_um)
        ctrl.core.set_position(z_um)
        _wait(ctrl, ctrl.core.get_focus_device())
    marked = {}
    if mark_position_in_list:
        # Same path as the mark_position tool: mirrors into microclaw's list
        # and MM's PositionList, so the grid appears in the GUI list.
        ctrl.add_position(
            pos_label,
            float(x_um),
            float(y_um),
            float(z_um) if z_um is not None else None,
        )
        marked = {"marked": True}
    if protocol == "snap":
        # snap(True) hands the pixels back for the one exposure it fires; this
        # branch used to drop them, so "scan a grid and tell me the max and min
        # at each point" had no tool that answered it and the agent hand-rolled
        # an 18-call move+snap loop instead (design/20 F1). Costs no exposure.
        with _pause_live(ctrl, restore=False):      # snap(True) wedges under live (V1)
            image = snap_to_numpy_displayed(ctrl)
        min_snr, min_snr_source = _analysis_gate(guard)
        stats = compute_stats(image, min_snr=min_snr)
        tile = {
            "position": pos_label,
            "status": "snapped",
            "saved": False,
            **marked,
            # No metric_valid_for stamp per tile: the grid shares one
            # ROI/exposure/binning, so the caller stamps it once. A bare float
            # would otherwise invite the cross-setting comparison design/14 §10
            # warns about — here the comparison across tiles is the point.
            "focus_metric": _round_sig(stats.focus_metric),
            # THE tile-ranking fix (design/25): ranking these floats against each
            # other is what walked the stage to the emptiest field on the grid.
            # focus_metric_valid is False where there is no signal to be sharp about.
            "focus_metric_valid": stats.focus_metric_valid,
            "snr": stats.snr,
            "snr_valid": stats.snr_valid,
            "snr_invalid_reason": stats.snr_invalid_reason,
            "min_snr": min_snr,
            "min_snr_source": min_snr_source,
            "background_level": stats.background_level,
            "mean_intensity": round(stats.mean_intensity, 1),
            "min_intensity": round(stats.min_intensity, 1),
            "max_intensity": round(stats.max_intensity, 1),
            "saturated_fraction": round(stats.saturated_fraction, 6),
        }
        if not stats.snr_valid:
            tile["warning"] = stats.snr_invalid_reason
        elif not stats.focus_metric_valid:
            tile["warning"] = focus_invalid_warning(stats.snr, min_snr)
        return tile
    if pos_save_dir is None:
        return {
            "position": pos_label,
            "error": f"save_dir is required for protocol '{protocol}'.",
            **marked,
        }
    Path(pos_save_dir).mkdir(parents=True, exist_ok=True)
    if protocol == "zstack":
        r = run_zstack(
            ctrl, guard, save_dir=pos_save_dir, name=pos_label,
            _reservation=reservation, **params
        )
        return {"position": pos_label, **marked, **r}
    elif protocol == "timelapse":
        r = run_timelapse(
            ctrl, guard, save_dir=pos_save_dir, name=pos_label,
            _reservation=reservation, **params
        )
        return {"position": pos_label, **marked, **r}
    else:
        return {"position": pos_label, "error": f"Unknown protocol '{protocol}'."}


@_acquisition_entry_point
@emits(_emit_multiposition)
def run_multiposition_acquisition(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    protocol: str,
    save_dir: str | None = None,
    position_names: list[str] | None = None,
    positions: list[dict] | None = None,
    name: str = "multipos",
    protocol_params: dict | None = None,
    mark_positions: bool = False,
    hook_strategy: str | list[str] | None = None,
    hook_params: dict | list[dict | None] | None = None,
    log_path: str | None = None,
    preserve_unsupported: bool = False,
    illumination_envelope: dict | None = None,
    artifact_limits: dict | None = None,
) -> dict:
    """Visit each position and run a per-position protocol.

    Supply either position_names (labels in the MM position list) or positions
    (list of {x_um, y_um, name, z_um?} dicts). Providing both is an error.

    protocol options:
      "snap"       — display-only; does NOT save to disk (returns saved=False).
                     save_dir is not needed and may be omitted. Returns
                     focus_metric and mean/min/max intensity per position, so a
                     grid survey needs neither a hook nor a manual loop; the
                     metric's comparability stamp is on the top-level result.
      "zstack"     — saves a Z-stack at each position to save_dir/<position>.
      "timelapse"  — saves a timelapse at each position to save_dir/<position>.
                     Neither returns image statistics: a "per-position numbers"
                     request answered with timelapse n_frames=1 produces
                     datasets and none of the numbers (rig runs 20260716_140329
                     and _144714 both took that detour; the schema now leads
                     with the protocol-choice rule).

    To save a single plane per position (equivalent to snapping but with data
    written to disk), use protocol="timelapse" with
    protocol_params={"n_frames": 1, "interval_s": 0}.

    mark_positions=True additionally records each visited position into the
    stage position list (microclaw's list + MM's Position List Manager), as
    the mark_position tool would.

    hook_strategy switches to a single Acquisition spanning every position: one
    dataset with a `position` axis and one hook log covering every point, rather
    than the per-position loop's N datasets and N logs (design/19 F2). Not
    compatible with protocol="snap", which takes no acquisition images.
    """
    if position_names is not None and positions is not None:
        return {"error": "Provide position_names or positions, not both."}
    if position_names is None and positions is None:
        return {"error": "Provide either position_names or positions."}
    if protocol != "snap" and not save_dir:
        return {"error": f"save_dir is required for protocol '{protocol}'."}
    if save_dir:
        # Resolve the root before the per-position directories are derived from
        # it, so mkdir never creates a tree outside a configured workspace.
        save_dir = guard.resolve_in_workspace(save_dir)

    params = protocol_params or {}
    supplied = [
        key for key in ("hook_strategy", *HOOK_CAPABILITY_ARGS)
        if params.get(key) is not None
    ]
    if supplied:
        return {
            "error": "protocol_params cannot carry per-run hook capabilities in a "
            "reserved multiposition protocol: " + ", ".join(supplied)
        }
    results = []

    projection = None
    if position_names is not None or mark_positions:
        projection, conflict = _preflight_native_positions(
            ctrl, guard, preserve_unsupported=preserve_unsupported
        )
        if conflict:
            return conflict

    if position_names is not None:
        all_positions = {p["name"]: p for p in projection.positions}
        resolved = []
        for pos_name in position_names:
            if pos_name not in all_positions:
                results.append({"position": pos_name, "error": "Not found in position list."})
            else:
                pos = all_positions[pos_name]
                resolved.append((pos_name, pos["x_um"], pos["y_um"], pos.get("z_um")))
    else:
        resolved = [
            (p["name"], p["x_um"], p["y_um"], p.get("z_um")) for p in positions
        ]

    if hook_strategy:
        if protocol == "snap":
            return {"error":
                    "hook_strategy needs acquisition images; 'snap' is display-only. "
                    "Use protocol='timelapse' with protocol_params={'n_frames': 1, "
                    "'interval_s': 0} to capture one hooked frame per position."}
        if results:
            return {"error": "Positions not found in position list: "
                             f"{[r['position'] for r in results]}"}
        try:
            shape = _protocol_shape_kwargs(protocol, params)
        except ValueError as e:
            return {"error": str(e)}
        except KeyError as e:
            return {"error": f"protocol_params for '{protocol}' is missing {e}."}
        if mark_positions:
            # Match the non-hooked path: validate coordinates before publishing
            # anything to the operator's native position list.
            sweeps_z = "z_start" in shape
            for _label, x_um, y_um, z_um in resolved:
                guard.check_xy(x_um, y_um)
                if not sweeps_z and z_um is not None:
                    guard.check_z(z_um)
            if sweeps_z:
                guard.check_z(shape["z_start"])
                guard.check_z(shape["z_end"])
            # The grid coordinates are known up front, so marking needs no stage
            # reads and no visit loop — mark before the Acquisition takes over.
            for pos_label, x_um, y_um, z_um in resolved:
                ctrl.add_position(pos_label, float(x_um), float(y_um),
                                  float(z_um) if z_um is not None else None)
        added_labels = [item[0] for item in resolved] if mark_positions else []
        with _pause_live(ctrl, restore=False) as live_state:
            try:
                hooked = _acquire_positions_with_hook(
                    ctrl, guard,
                    positions=[{"name": n, "x_um": x, "y_um": y, "z_um": z}
                               for n, x, y, z in resolved],
                    save_dir=save_dir, name=name, hook_strategy=hook_strategy,
                    hook_params=hook_params, log_path=log_path,
                    channel=params.get("channel"), exposure_ms=params.get("exposure_ms"),
                    illumination_envelope=illumination_envelope,
                    artifact_limits=artifact_limits,
                    **shape,
                )
            except SafetyViolation as exc:
                if not added_labels:
                    raise
                hooked = {"error": str(exc)}
            except Exception as exc:
                hooked = {"error": str(exc)}
        restore = _live_restore_report(live_state)
        if "error" in hooked:
            # Transaction boundary: undo only entries written by this call.
            # Pre-existing list entries are never part of this rollback.
            rollback_errors = []
            for label in reversed(added_labels):
                try:
                    ctrl.remove_position(label)
                except Exception as exc:
                    rollback_errors.append({"position": label, "error": str(exc)})
            hooked["position_list_rollback"] = {
                "attempted": added_labels,
                "complete": not rollback_errors,
                "errors": rollback_errors,
            }
            if restore:
                hooked["live_view_restore"] = restore
            return hooked
        # The coordinates are known exactly, right here — the hooked branch used
        # to drop them, so "where was tile r2_c1?" had no answer short of
        # re-imaging the grid (design/23 Episode A). The non-hooked branch has
        # attached them since design/19 F3; this is the same fix on the path every
        # survey actually takes. read_hook_log joins to this on `position`.
        return {**hooked, "tiles": [
            {"position": n, "x_um": round(x, 3), "y_um": round(y, 3),
             **({"z_um": round(z, 3)} if z is not None else {})}
            for n, x, y, z in resolved
        ], **({"live_view_restore": restore} if restore else {})}

    reservation = (
        _authorize_acquisition(
            ctrl, guard, _plan_protocol_repetitions(ctrl, protocol, params, len(resolved))
        )
        if protocol != "snap" else None
    )
    with _pause_live(ctrl, restore=False) as live_state:
        try:
            for pos_label, x_um, y_um, z_um in resolved:
                pos_save_dir = str(Path(save_dir) / pos_label) if save_dir else None
                # Coordinates on every row, including the error rows. The agent used to
                # publish X/Y columns filled from its own call ordering rather than from
                # anything a tool returned (design/19 F3, design/20 S1).
                where = {"x_um": round(x_um, 3), "y_um": round(y_um, 3)}
                if z_um is not None:
                    where["z_um"] = round(z_um, 3)
                try:
                    result = _run_protocol_at(
                        ctrl, guard, pos_label, x_um, y_um, z_um, protocol, pos_save_dir,
                        params, mark_position_in_list=mark_positions,
                        reservation=reservation,
                    )
                    results.append({**where, **result})
                except Exception as e:
                    results.append({"position": pos_label, **where, "error": str(e)})
        finally:
            if reservation is not None:
                reservation.close()

    total = len(position_names or positions)
    n_ok = sum(1 for r in results if "error" not in r)
    payload = {
        "status": f"{n_ok}/{total} positions completed.",
        "results": results,
    }
    restore = _live_restore_report(live_state)
    if restore:
        payload["live_view_restore"] = restore
    if protocol == "snap" and n_ok:
        # One stamp for the whole grid: the per-tile focus_metric values are
        # comparable to each other under these settings and to nothing else.
        payload.update(_metric_stamp(ctrl))
    return payload


@_acquisition_entry_point
@emits(_emit_tile)
def run_tile_acquisition(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    rows: int,
    cols: int,
    step_um: float,
    protocol: str,
    save_dir: str | None = None,
    name: str = "tile",
    protocol_params: dict | None = None,
    mark_positions: bool = False,
    hook_strategy: str | None = None,
    hook_params: dict | None = None,
    log_path: str | None = None,
    center_x_um: float | None = None,
    center_y_um: float | None = None,
    return_to_center: bool = True,
) -> dict:
    """Acquire a rows×cols tile grid centered on center_x_um/center_y_um.

    The center defaults to the current stage position, and the stage is driven
    back there afterwards. Both halves matter: the grid ends on its last tile,
    so without the return move a default-centered scan run twice would walk
    diagonally forward by half a grid each time — three "do it again" runs
    surveying three different regions, which a uniform sample (or the demo
    camera, which returns one frame regardless of position) hides completely.
    Pass center_* to pin a grid to absolute coordinates and reproduce an earlier
    scan exactly.

    hook_strategy runs one hooked Acquisition across the whole grid; see
    run_multiposition_acquisition.
    """
    center_x = ctrl.core.get_x_position() if center_x_um is None else center_x_um
    center_y = ctrl.core.get_y_position() if center_y_um is None else center_y_um
    if center_x_um is not None and center_y_um is not None:
        center_source = "explicit"
    elif center_x_um is None and center_y_um is None:
        center_source = "current_stage_position"
    else:
        center_source = "partially_explicit"
    if center_x_um is not None or center_y_um is not None:
        # A supplied center is unvalidated caller input, and an even-sided grid
        # puts it between tiles — so the per-tile bounds check never covers it.
        # Refuse here, before the first move, not on the way home.
        guard.check_xy(center_x, center_y)
    x_start = center_x - (cols - 1) / 2 * step_um
    y_start = center_y - (rows - 1) / 2 * step_um
    positions = [
        {
            "name": f"{name}_r{r}_c{c}",
            "x_um": x_start + c * step_um,
            "y_um": y_start + r * step_um,
        }
        for r in range(rows)
        for c in range(cols)
    ]
    result = run_multiposition_acquisition(
        ctrl, guard,
        protocol=protocol,
        save_dir=save_dir,
        positions=positions,
        name=name,
        protocol_params=protocol_params,
        mark_positions=mark_positions,
        hook_strategy=hook_strategy,
        hook_params=hook_params,
        log_path=log_path,
    )
    return_result = None
    if return_to_center:
        # Deliberately unguarded: the center was cleared up front (supplied) or
        # is where the stage already sat (default). Re-checking could only refuse
        # the move *home*, stranding the objective out over the sample on the
        # last tile — the opposite of what the guard is for.
        return_started = time.monotonic()
        ctrl.set_xy(center_x, center_y)
        try:
            achieved_x = float(ctrl.core.get_x_position())
            achieved_y = float(ctrl.core.get_y_position())
            return_result = {
                "requested_um": [center_x, center_y],
                "achieved_um": [achieved_x, achieved_y],
                "error_um": [achieved_x - center_x, achieved_y - center_y],
                "duration_s": round(time.monotonic() - return_started, 6),
            }
        except Exception:
            return_result = {"requested_um": [center_x, center_y],
                             "achieved_um": None,
                             "duration_s": round(time.monotonic() - return_started, 6)}
    # Report where the grid actually sat, so a caller comparing two runs can see
    # they measured the same ground rather than assuming it.
    return {**result, "grid_center_x_um": center_x, "grid_center_y_um": center_y,
            "grid_center_source": center_source,
            **({"return_to_center": return_result} if return_result else {})}


@_acquisition_entry_point
def run_multiposition_with_autofocus(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    position_names: list[str] | None = None,
    z_range_um: float | None = None,
    z_step_um: float | None = None,
    protocol: str | None = None,
    save_dir: str | None = None,
    name: str = "multipos_af",
    autofocus_method: str = "coarse_then_fine",
    settle_ms: int = 50,
    protocol_params: dict | None = None,
    preserve_unsupported: bool = False,
    positions: list[dict] | None = None,
) -> dict:
    """Deprecated forwarding wrapper for composed multiposition acquisition."""
    # Planning and authorization are deliberately delegated: the forwarded
    # path reaches _plan_protocol_repetitions/_authorize_acquisition for plain
    # runs and hook-aware authorization for this autofocus run.
    missing = [name for name, value in (
        ("z_range_um", z_range_um), ("z_step_um", z_step_um),
        ("protocol", protocol), ("save_dir", save_dir),
    ) if value is None]
    if missing:
        return {"error": f"Missing required arguments: {missing}."}
    save_dir = guard.resolve_in_workspace(save_dir)  # before any forwarded move
    if autofocus_method != "coarse_then_fine":
        return {"error": "The deprecated wrapper only forwards coarse_then_fine autofocus."}
    if protocol == "snap":
        return {
            "error": "The deprecated wrapper cannot compose autofocus with display-only "
                     "snap. Use protocol='timelapse' with n_frames=1 and interval_s=0."
        }
    compatibility_log = str(Path(save_dir) / f"{name}_autofocus_log.json")
    result = run_multiposition_acquisition(
        ctrl, guard, protocol=protocol, save_dir=save_dir,
        position_names=position_names, positions=positions, name=name,
        protocol_params=protocol_params,
        hook_strategy="autofocus_per_position",
        hook_params={"z_range_um": z_range_um, "z_step_um": z_step_um,
                     "settle_ms": settle_ms},
        log_path=compatibility_log,
        preserve_unsupported=preserve_unsupported,
    )
    if "error" not in result:
        autofocus_by_position: dict[str, dict[str, Any]] = {}
        try:
            records = json.loads(Path(compatibility_log).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            records = []
        for record in records:
            position = record.get("position")
            if position is not None and (
                "best_z_um" in record or "autofocus" in record
            ):
                autofocus_by_position[str(position)] = {
                    **({"best_z_um": record["best_z_um"]}
                       if "best_z_um" in record else {}),
                    "autofocus_converged": bool(record.get("converged", False)),
                    **({"autofocus_warning": record["warning"]}
                       if "warning" in record else {}),
                }
        compatibility_results = [
            {**tile, **autofocus_by_position.get(str(tile["position"]), {}),
             "status": "complete"}
            for tile in result.get("tiles", [])
        ]
        result["results"] = compatibility_results
        result["status"] = (
            f"{len(compatibility_results)}/{len(compatibility_results)} positions "
            "completed with autofocus."
        )
    return {
        **result,
        "deprecation": (
            "run_multiposition_with_autofocus is deprecated; use "
            "run_multiposition_acquisition(..., "
            "hook_strategy='autofocus_per_position'). This forwarding path writes "
            "one dataset with a position axis; the former implementation wrote one "
            "dataset per position."
        ),
    }


# --- Hook-based adaptive acquisition ---

def _prepare_log_path(guard: SafetyGuard, log_path: str | None, *,
                      default: str | None = None) -> str | None:
    """Resolve a hook's log path in the workspace and create its parent directory.

    The hook writes this file itself, so the path never passed the guard on its
    way to being written. Local reads are unconfined but writes are not, so
    resolve it here or a hook can write outside a configured workspace.

    The mkdir matters as much as the resolve: the hook only opens the file on
    its first frame, so a missing parent surfaces as FileNotFoundError *inside
    the image processor*, after the acquisition has already moved the stage and
    written a dataset. save_dir is created up front; log_path must be too.
    """
    defaulted = not log_path and bool(default)
    log_path = log_path or default
    if not log_path:
        return None
    log_path = guard.resolve_in_workspace(log_path)
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    if defaulted:
        return _next_available_log_path(Path(log_path))
    return log_path


def _resolve_hook(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    hook_strategy: str,
    hook_params: dict | None,
    log_path: str | None,
) -> Any:
    """Instantiate a hook with an explicit provenance-based trust category.

    Registry classes are reviewed, shipped control code and retain their
    existing capabilities. Every class loaded from the saved-hook directory is
    untrusted regardless of its manifest ``source`` label, receives no
    controller/guard/log capability, and is wrapped by trusted parent code.
    Saved source still runs in-process; review plus hash pinning remain the
    containment story until Block 13.
    """
    from microclaw.hooks import PRECODED_HOOK_REGISTRY
    from microclaw.hook_decisions import UntrustedHookAdapter
    from microclaw.hook_manager import load_hook_class, list_saved_hooks

    params = dict(hook_params or {})
    if log_path:
        params["log_path"] = log_path

    if hook_strategy in PRECODED_HOOK_REGISTRY:
        hook_cls = PRECODED_HOOK_REGISTRY[hook_strategy]
        trusted_builtin = True
    elif hook_strategy in list_saved_hooks():
        hook_cls = load_hook_class(hook_strategy)
        trusted_builtin = False
    else:
        raise ValueError(
            f"Unknown hook strategy '{hook_strategy}'. "
            "Run list_hooks() to see available strategies."
        )

    if trusted_builtin:
        sig = inspect.signature(hook_cls.__init__)
        if "ctrl" in sig.parameters:
            params.setdefault("ctrl", ctrl)
        if "guard" in sig.parameters:
            params.setdefault("guard", guard)
        return hook_cls(**params)

    # Refuse a saved hook that means to keep its own log. The audit path now
    # belongs to the trusted parent, so such a hook would construct fine, take
    # every exposure, and record nothing: the parent notes only that a frame was
    # retained or discarded, and whatever the hook measured is dropped. That is
    # the silent-loss failure design/19 F3 and design/24 exist to prevent, and it
    # is exactly what the 2026-07-27 demo gate caught in
    # test_generate_save_and_use_custom_hook. Refuse before any hardware moves.
    from microclaw.hooks import HookBase
    if issubclass(hook_cls, HookBase) or "log_path" in inspect.signature(
        hook_cls.__init__
    ).parameters:
        raise ValueError(
            f"Saved hook '{hook_strategy}' writes its own log, which is no "
            "longer possible: the trusted parent owns the audit record so hook "
            "source cannot forge or omit it. As written this hook would acquire "
            "images and record none of its measurements. Give it "
            "analyze_frame(image, metadata) returning a HookResult instead — its "
            "measurements are then written to the log by the parent, with "
            "provenance — and drop log_path and any HookBase inheritance."
        )

    # Do not let caller-supplied hook_params smuggle capabilities across the
    # provenance boundary either. The trusted adapter, not generated code,
    # owns the audit path.
    # One definition, shared with describe_hook. A second copy would drift the
    # moment this list grows, and describe_hook would then report a parameter as
    # accepted while this line silently drops it — worse than not reporting at all.
    from microclaw.hook_manager import FORBIDDEN_SAVED_HOOK_PARAMS
    for forbidden in FORBIDDEN_SAVED_HOOK_PARAMS:
        params.pop(forbidden, None)
    adapter = UntrustedHookAdapter(hook_cls(**params), log_path=log_path)
    adapter.strategy_name = hook_strategy
    return adapter


def _resolve_hooks(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    hook_strategy: str | list[str],
    hook_params: dict | list[dict | None] | None,
    log_path: str | None,
) -> Any:
    """Resolve one hook unchanged, or independently resolve and compose many."""
    if isinstance(hook_strategy, str):
        if isinstance(hook_params, list):
            raise ValueError("hook_params must be an object for one hook_strategy.")
        return _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path)
    if not isinstance(hook_strategy, list) or not hook_strategy or not all(
        isinstance(name, str) and name for name in hook_strategy
    ):
        raise ValueError("hook_strategy must be a hook name or a non-empty list of names.")
    if hook_params is None:
        params = [None] * len(hook_strategy)
    elif isinstance(hook_params, list) and len(hook_params) == len(hook_strategy):
        params = hook_params
    else:
        raise ValueError(
            "For composed hooks, hook_params must be a same-length list of objects or nulls."
        )
    from microclaw.hook_decisions import CompositeHook
    # Child hooks receive no shared log or newly combined capabilities. Saved
    # hooks are still hash-checked and adapter-wrapped by each _resolve_hook call.
    hooks = [
        (strategy, _resolve_hook(ctrl, guard, strategy, param, None))
        for strategy, param in zip(hook_strategy, params)
    ]
    return CompositeHook(hooks, log_path)


def _plan_with_hook_dose(plan: AcquisitionPlan, hook: Any) -> AcquisitionPlan:
    """Add worst-case hook-fired exposures to an event-plan reservation."""
    extra_absolute = getattr(hook, "planned_extra_exposures", lambda: 0)()
    extra_per_event = getattr(
        hook, "planned_extra_exposures_per_event", lambda: 0
    )()
    # Non-dose hooks leave the plan completely transparent. Besides avoiding
    # needless reconstruction, this preserves capability-confirmation ordering
    # without adding a new plan-inspection contract to that path.
    if extra_per_event == 0 and extra_absolute == 0:
        return plan
    extra = plan.frames * extra_per_event + extra_absolute
    return AcquisitionPlan(
        frames=plan.frames + extra,
        exposure_ms_per_frame=plan.exposure_ms_per_frame,
        estimated_duration_s=(
            plan.estimated_duration_s + extra * plan.exposure_ms_per_frame / 1000.0
        ),
        # Autofocus snaps are analyzed in memory and are not stored.
        estimated_bytes=plan.estimated_bytes,
    )


def _configure_hook_capabilities(hook: Any, ctrl: MicroscopeController,
                                 guard: SafetyGuard, save_dir: str, name: str,
                                 illumination_envelope: dict | None,
                                 artifact_limits: dict | None,
                                 named_stage_envelope: dict | None = None,
                                 hook_action_plan: list[dict] | None = None,
                                 events: list[dict] | None = None,
                                 property_envelope: dict | None = None,
                                 adaptive: bool = False) -> None:
    """Validate and authorize independent parent-side hook capabilities."""
    from microclaw.hook_decisions import CompositeHook, UntrustedHookAdapter
    if adaptive and hook_action_plan is not None:
        raise ValueError("run_adaptive_survey rejects hook_action_plan; its events are selected at runtime.")
    if isinstance(hook, CompositeHook):
        emitters = hook.artifact_emitting_hook_names
    elif bool(getattr(hook, "can_emit_artifacts", False)):
        emitters = [getattr(hook, "strategy_name", hook.__class__.__name__)]
    else:
        emitters = []
    if emitters and artifact_limits is None:
        raise _HookArtifactBudgetError(
            "Hook(s) " + ", ".join(repr(name) for name in emitters) +
            " can emit artifacts, but no artifact_limits budget was configured. "
            "The acquisition was refused during planning before any exposure; "
            "pass artifact_limits with max_artifact_bytes, max_count, and "
            "max_total_bytes."
        )
    if isinstance(hook, CompositeHook):
        untrusted = [child for _name, child in hook.named_hooks
                     if isinstance(child, UntrustedHookAdapter)]
        if illumination_envelope:
            raise ValueError(
                "illumination_envelope is not supported for composed hooks."
            )
        if (named_stage_envelope is not None or property_envelope is not None or
                hook_action_plan is not None):
            raise ValueError(
                "hardware envelopes and hook_action_plan are not supported for composed hooks."
            )
        if artifact_limits is not None:
            if not untrusted:
                raise ValueError("Hook envelopes apply only to saved generated hooks.")
            allowed = {"max_artifact_bytes", "max_count", "max_total_bytes"}
            if set(artifact_limits) != allowed:
                raise ValueError(f"artifact_limits must contain exactly {sorted(allowed)}.")
            limits = {key: artifact_limits[key] for key in allowed}
            if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0
                   for v in limits.values()):
                raise ValueError("artifact limits must be positive integers.")
            hook.configure_artifacts(
                target_dir=Path(save_dir) / name / "artifacts", **limits
            )
        return
    if not isinstance(hook, UntrustedHookAdapter):
        supplied = {
            "illumination_envelope": illumination_envelope,
            "artifact_limits": artifact_limits,
            "named_stage_envelope": named_stage_envelope,
            "property_envelope": property_envelope,
            "hook_action_plan": hook_action_plan,
        }
        if any(value is not None for value in supplied.values()):
            raise ValueError(
                "Hook envelopes with neither a hook nor a hook_action_plan have "
                "no effect; pass hook_strategy naming a saved generated hook. "
                "A precoded hook cannot carry them."
                if hook is None else
                "Hook envelopes apply only to saved generated hooks."
            )
        return
    if artifact_limits is not None:
        allowed = {"max_artifact_bytes", "max_count", "max_total_bytes"}
        if set(artifact_limits) != allowed:
            raise ValueError(f"artifact_limits must contain exactly {sorted(allowed)}.")
        limits = {key: artifact_limits[key] for key in allowed}
        if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0
               for v in limits.values()):
            raise ValueError("artifact limits must be positive integers.")
        hook.configure_artifacts(
            target_dir=Path(save_dir) / name / "artifacts", **limits,
        )
    summaries = []
    illumination_config = None
    if illumination_envelope is not None:
        allowed = {"device", "property", "max_power_percent", "max_writes"}
        if set(illumination_envelope) != allowed:
            raise ValueError(f"illumination_envelope must contain exactly {sorted(allowed)}.")
        device = illumination_envelope["device"]
        prop = illumination_envelope["property"]
        ceiling = illumination_envelope["max_power_percent"]
        writes = illumination_envelope["max_writes"]
        if not guard.is_illumination_power(device, prop):
            raise ValueError(
                "illumination envelope device/property is not declared in "
                "illumination.power_properties."
            )
        if isinstance(ceiling, bool) or not isinstance(ceiling, (int, float)) or not math.isfinite(ceiling) or ceiling < 0:
            raise ValueError("illumination envelope max_power_percent must be finite and non-negative.")
        configured = guard.max_illumination_power_percent
        if configured is not None and ceiling > configured:
            raise ValueError(
                f"illumination envelope ceiling {ceiling}% exceeds configured "
                f"illumination.max_power_percent ({configured}%)."
            )
        if isinstance(writes, bool) or not isinstance(writes, int) or writes <= 0:
            raise ValueError("illumination envelope max_writes must be a positive integer.")
        initial = float(ctrl.core.get_property(device, prop))
        if not math.isfinite(initial):
            raise ValueError("initial illumination power must be finite.")
        illumination_config = (device, prop, float(ceiling), writes, initial)
        summaries.append(
            f"Illumination: {device}.{prop}; ceiling {float(ceiling):g}%; "
            f"{writes} accepted increasing writes maximum. Generated hook code "
            "cannot enable a shutter or turn light on."
        )

    # Preserve the block-7b illumination-only contract byte-for-byte. The
    # combined dialog below is used only when named-stage authority is present.
    if named_stage_envelope is None and property_envelope is None:
        if hook_action_plan is not None:
            raise ValueError("hook_action_plan requires named_stage_envelope or property_envelope.")
        if illumination_config is not None:
            device, prop, ceiling, writes, initial = illumination_config
            summary = (
                f"AUTHORIZE UNATTENDED HOOK ILLUMINATION: {device}.{prop}\n"
                f"Ceiling: {float(ceiling):g}% ({writes} accepted writes maximum).\n"
                "Generated hook code will drive this power unattended, per frame, "
                "for the duration of the run. It cannot enable a shutter or turn light on."
            )
            if not CONFIRM_FN(summary, kind="illumination"):
                raise SafetyViolation(
                    f"User declined hook illumination envelope for {device}.{prop}; "
                    "acquisition was not started."
                )
            hook.configure_illumination(
                core=ctrl.core, guard=guard, device=device, property=prop,
                max_power_percent=ceiling, max_writes=writes, initial_value=initial,
            )
        return

    named_config = None
    property_config = None
    if property_envelope is not None:
        categorical = {"device", "property", "allowed_values", "max_writes", "restore"}
        numeric = {"device", "property", "min", "max", "max_writes", "restore"}
        keys = set(property_envelope)
        if keys not in (categorical, numeric):
            raise ValueError(
                "property_envelope must contain exactly the categorical keys "
                f"{sorted(categorical)} or numeric keys {sorted(numeric)}."
            )
        device, prop = property_envelope["device"], property_envelope["property"]
        writes, restore = property_envelope["max_writes"], property_envelope["restore"]
        if not isinstance(device, str) or not device or not isinstance(prop, str) or not prop:
            raise ValueError("property_envelope device and property must be non-empty strings.")
        if isinstance(writes, bool) or not isinstance(writes, int) or writes <= 0:
            raise ValueError("property_envelope max_writes must be a positive integer.")
        if not (restore in {"leave", "entry"} if isinstance(restore, str) else
                isinstance(restore, dict) and set(restore) == {"value"} and
                isinstance(restore["value"], str)):
            raise ValueError("property_envelope restore must be 'leave', 'entry', or {'value': string}.")
        driver_allowed = _str_vector(ctrl.core.get_allowed_property_values(device, prop))
        if keys == categorical:
            requested = property_envelope["allowed_values"]
            if (not isinstance(requested, list) or not requested or
                    any(not isinstance(value, str) for value in requested) or
                    len(set(requested)) != len(requested)):
                raise ValueError("property_envelope allowed_values must be unique strings and non-empty.")
            effective = tuple(value for value in requested
                              if not driver_allowed or value in driver_allowed)
            if not effective:
                raise ValueError("property_envelope has no values allowed by Micro-Manager.")
            allowed_values, low, high = effective, None, None
            bound_summary = f"approved values {list(effective)!r}"
        else:
            low, high = property_envelope["min"], property_envelope["max"]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
                   not math.isfinite(v) for v in (low, high)) or low > high:
                raise ValueError("property_envelope min/max must be finite with min <= max.")
            if bool(ctrl.core.has_property_limits(device, prop)):
                low = max(float(low), float(ctrl.core.get_property_lower_limit(device, prop)))
                high = min(float(high), float(ctrl.core.get_property_upper_limit(device, prop)))
                if low > high:
                    raise ValueError("property_envelope does not intersect Micro-Manager limits.")
                bound_summary = f"approved interval {low:g}-{high:g} (reviewed and Micro-Manager intersection)"
            else:
                if low != high:
                    raise ValueError("an unbounded numeric property may be approved only at one exact finite value.")
                bound_summary = f"exact value {low:g}; Microclaw has no independent range to verify"
            allowed_values = None
        initial = str(ctrl.core.get_property(device, prop))
        property_config = (device, prop, allowed_values, low, high, writes, initial, restore)
        summaries.append(
            f"Property: {device}.{prop}; {bound_summary}; {writes} attempted writes maximum; "
            f"restore {restore!r}."
        )
    if named_stage_envelope is not None:
        allowed = {"device", "min_um", "max_um", "max_writes", "restore"}
        if set(named_stage_envelope) != allowed:
            raise ValueError(f"named_stage_envelope must contain exactly {sorted(allowed)}.")
        device = named_stage_envelope["device"]
        low, high = named_stage_envelope["min_um"], named_stage_envelope["max_um"]
        writes, restore = named_stage_envelope["max_writes"], named_stage_envelope["restore"]
        if not isinstance(device, str) or not device:
            raise ValueError("named_stage_envelope device must be a non-empty string.")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in (low, high)) or low > high:
            raise ValueError("named_stage_envelope bounds must be finite numbers with min_um <= max_um.")
        if isinstance(writes, bool) or not isinstance(writes, int) or writes <= 0:
            raise ValueError("named_stage_envelope max_writes must be a positive integer.")
        if not (restore in {"leave", "entry"} if isinstance(restore, str) else
                isinstance(restore, dict) and set(restore) == {"value"}):
            raise ValueError("named_stage_envelope restore must be 'leave', 'entry', or {'value': number}.")
        # Approval cannot widen a configured bound, so the envelope's own
        # endpoints face the guard before the operator is shown them. M5,
        # 2026-08-17: an approved `18000-21100 um` interval over an axis capped
        # at 20000 was displayed as reachable and the run died mid-sweep on the
        # first target that crossed it. A fixed plan checks only its targets,
        # which says nothing about the reach being offered -- and an adaptive
        # run has no targets to check at approval at all.
        guard.check_named_stage(device, low)
        guard.check_named_stage(device, high)
        initial = float(ctrl.core.get_position(device))
        if not math.isfinite(initial):
            raise ValueError("initial named-stage position must be finite.")
        named_config = (device, float(low), float(high), writes, initial, restore)
        summaries.append(
            # ASCII on purpose: every CONFIRM_FN implementation print()s this
            # summary to the rig's console, and an en dash is absent from some
            # Windows console code pages -- an encode error there would take
            # down the confirmation itself, refusing a run for a typographic
            # reason. The emitted standalone script is ASCII for the same reason.
            f"Named stage: {device}; approved interval {float(low):g}-{float(high):g} um; "
            f"{writes} attempted writes maximum; restore {restore!r}."
        )
    from microclaw.hook_decisions import MoveNamedStage, SetDeviceProperty, parse_action
    if not adaptive and (hook_action_plan is None or events is None or
                         not isinstance(hook_action_plan, list)):
        raise ValueError("a hardware envelope requires a hook_action_plan for the generated events.")
    parsed_plan = {}
    for entry in hook_action_plan or []:
        if not isinstance(entry, dict) or set(entry) != {"hook_event_index", "actions"}:
            raise ValueError("each hook_action_plan entry must contain exactly actions and hook_event_index.")
        index = entry["hook_event_index"]
        if isinstance(index, bool) or not isinstance(index, int) or index in parsed_plan:
            raise ValueError("hook_action_plan indices must be unique integers.")
        if not isinstance(entry["actions"], list):
            raise ValueError("hook_action_plan actions must be a list (empty is explicit).")
        actions = tuple(parse_action(action) for action in entry["actions"])
        permitted = tuple(cls for cls, envelope in (
            (MoveNamedStage, named_stage_envelope), (SetDeviceProperty, property_envelope)
        ) if envelope is not None)
        if any(not isinstance(action, permitted) for action in actions):
            raise ValueError("fixed hook_action_plan actions require their matching named-stage or property envelope.")
        parsed_plan[index] = actions
    if not adaptive and set(parsed_plan) != set(range(len(events))):
        raise ValueError(f"hook_action_plan indices must be exactly 0..{len(events) - 1}.")
    axes_plan = {}
    for index, event in enumerate(events or []):
        try:
            signature = hook.axes_signature(event)
        except (TypeError, RuntimeError) as exc:
            raise ValueError(f"generated event {index} has invalid axes: {exc}") from exc
        if signature in axes_plan and not adaptive:
            raise ValueError(f"generated events have duplicate axes signature {dict(signature)!r}.")
        if not adaptive:
            axes_plan[signature] = (index, parsed_plan[index])
    if named_config is not None:
        device, low, high, writes, initial, restore = named_config
        reserved = 0 if restore == "leave" else 1
        planned_writes = sum(isinstance(action, MoveNamedStage)
                             for actions in parsed_plan.values() for action in actions)
        if planned_writes + reserved > writes:
            raise ValueError("hook_action_plan would consume the write reserved for restoration.")
        restore_target = initial if restore == "entry" else (
            restore.get("value") if isinstance(restore, dict) else None
        )
        targets = [a.position_um for actions in parsed_plan.values() for a in actions
                   if isinstance(a, MoveNamedStage)]
        if restore_target is not None:
            targets.append(restore_target)
        for target in targets:
            if (isinstance(target, bool) or not isinstance(target, (int, float)) or
                    not math.isfinite(target)):
                raise ValueError("named-stage planned and restoration positions must be finite numbers.")
            if target < low or target > high:
                raise ValueError("named-stage planned or restoration position is outside the envelope.")
            guard.check_named_stage(device, target)
    property_reserved = 0 if property_config is None or property_config[-1] == "leave" else 1
    property_planned = sum(isinstance(a, SetDeviceProperty) for actions in parsed_plan.values() for a in actions)
    if property_config is not None and property_planned + property_reserved > property_config[5]:
        raise ValueError("hook_action_plan would consume the property write reserved for restoration.")
    if property_config is not None:
        from microclaw.authorization import authorize_property_write
        from microclaw.safety import _finite_number_text
        device, prop, values, low, high, _writes, initial, restore = property_config
        authorize_property_write(ctrl, device, prop)
        restore_value = initial if restore == "entry" else (
            restore["value"] if isinstance(restore, dict) else None
        )
        planned_values = [
            action.value for actions in parsed_plan.values() for action in actions
            if isinstance(action, SetDeviceProperty)
        ] + ([restore_value] if restore_value is not None else [])
        for value in planned_values:
            if values is not None:
                if value not in values:
                    raise ValueError("planned or restoration property value is outside the envelope.")
            else:
                number = _finite_number_text(value, "SetDeviceProperty.value")
                if number < low or number > high:
                    raise ValueError("planned or restoration property value is outside the envelope.")
            guard.check_device_property(
                ctrl.core, device, prop, value, approved_envelope=True,
            )
            guard.check_illumination(
                ctrl.core, device, prop, value,
                confirm_fn=None,
            )
    if summaries and not CONFIRM_FN(
        "ALLOW HOOK HARDWARE CONTROL FOR THIS RUN\n" + "\n".join(summaries) +
        f"\nAcquisition: {len(events or [])} frames, {Path(save_dir) / name}",
        kind="hook_hardware",
    ):
        raise SafetyViolation("User declined hook hardware envelope; acquisition was not started.")
    if illumination_config is not None:
        device, prop, ceiling, writes, initial = illumination_config
        hook.configure_illumination(
            core=ctrl.core, guard=guard, device=device, property=prop,
            max_power_percent=ceiling, max_writes=writes, initial_value=initial,
        )
    if named_config is not None:
        device, low, high, writes, initial, restore = named_config
        hook.configure_named_stage(
            core=ctrl.core, guard=guard, device=device, min_um=low, max_um=high,
            max_writes=writes, initial_value=initial, restore=restore,
            action_plan=None if adaptive else axes_plan,
        )
    if property_config is not None:
        device, prop, values, low, high, writes, initial, restore = property_config
        hook.configure_property(
            ctrl=ctrl, guard=guard, device=device, property=prop,
            allowed_values=values, min_value=low, max_value=high,
            max_writes=writes, initial_value=initial, restore=restore,
            action_plan=None if adaptive else axes_plan,
        )


def _adaptive_result(
    dataset_path: str,
    log_path: str | None,
    status: str = "Adaptive acquisition complete.",
    **extra: Any,
) -> dict:
    result: dict[str, Any] = {
        "status": status,
        "dataset_path": dataset_path,
        "artifact": {"kind": "dataset", "path": dataset_path},
        **extra,
    }
    if log_path:
        result["log_path"] = log_path
        result["hint"] = "Call read_hook_log to retrieve per-image results."
    return result


def _acquire_positions_with_hook(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    positions: list[dict],
    save_dir: str,
    name: str,
    hook_strategy: str | list[str],
    hook_params: dict | list[dict | None] | None = None,
    log_path: str | None = None,
    channel: str | None = None,
    exposure_ms: float | None = None,
    illumination_envelope: dict | None = None,
    artifact_limits: dict | None = None,
    **shape_kwargs: Any,
) -> dict:
    """One Acquisition across every position, with one or composed hooks.

    positions are {name, x_um, y_um, z_um?} dicts; shape_kwargs carry the
    per-position event shape (z_start/z_end/z_step or num_time_points/
    time_interval_s), exactly as the adaptive pair passes them.

    pycro-manager moves the stage here, so each image's metadata carries
    axes["position"] — the hook keys its log to the grid point instead of
    guessing metadata names (design/19 F3). Contrast a per-position loop, where
    a fresh hook per position truncates a shared log (see HookBase._write_log).
    """
    save_dir = guard.resolve_in_workspace(save_dir)
    log_path = _prepare_log_path(guard, log_path)

    # A Z range makes the event list sweep Z itself, and multi_d_acquisition_events
    # reads z_start/z_end as ABSOLUTE alongside xy_positions but as offsets
    # RELATIVE to each point's Z alongside xyz_positions. The per-position z_um is
    # therefore dropped when a range is present — which loses nothing, because the
    # unhooked loop's run_zstack sweeps the same absolute range after its move to
    # z_um. Swapping in xyz_positions here would silently reinterpret z_start_um.
    sweeps_z = "z_start" in shape_kwargs

    # The stage is driven by the Acquisition, not by us, so there is no
    # per-move guard call. Check every point up front: the refusal must not
    # arrive on tile 7 of 9, with the objective already out over the sample.
    for p in positions:
        guard.check_xy(p["x_um"], p["y_um"])
        if not sweeps_z and p.get("z_um") is not None:
            guard.check_z(p["z_um"])
    if sweeps_z:
        guard.check_z(shape_kwargs["z_start"])     # the planes actually visited
        guard.check_z(shape_kwargs["z_end"])
    if channel:
        _check_acquisition_channel(ctrl, guard, channel)
    if exposure_ms is not None:
        guard.check_exposure(exposure_ms)
        if not channel:
            ctrl.core.set_exposure(exposure_ms)   # eventless exposure; see run_zstack

    try:
        hook = _resolve_hooks(ctrl, guard, hook_strategy, hook_params, log_path)
    except ValueError as e:
        return {"error": str(e)}

    if not sweeps_z and any(p.get("z_um") is not None for p in positions):
        # xyz_positions is all-or-nothing: a point without a Z holds the current
        # focus plane rather than dropping out of the event list.
        current_z = ctrl.core.get_position()
        shape_kwargs["xyz_positions"] = [
            (p["x_um"], p["y_um"], p["z_um"] if p.get("z_um") is not None else current_z)
            for p in positions
        ]
    else:
        shape_kwargs["xy_positions"] = [(p["x_um"], p["y_um"]) for p in positions]

    events = _build_acquisition_events(
        channel=channel, exposure_ms=exposure_ms,
        position_labels=[p["name"] for p in positions], **shape_kwargs,
    )
    try:
        _configure_hook_capabilities(hook, ctrl, guard, save_dir, name,
                                     illumination_envelope, artifact_limits)
    except _HookArtifactBudgetError as exc:
        return {"error": str(exc)}
    plan = _plan_with_hook_dose(plan_events(ctrl, events, exposure_ms), hook)
    reservation = _authorize_acquisition(ctrl, guard, plan)
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    try:
        dataset_path = _acquire_with_hooks(
            guard, save_dir, name, events, hook, reservation=reservation
        )
    except _HookedAcquisitionFailure as exc:
        return _hooked_failure_result(exc, log_path)
    completed_at = datetime.now(timezone.utc)
    # Say how many positions ran. "Adaptive acquisition complete." over a grid
    # left no way to confirm every tile fired without opening the log.
    return _adaptive_result(
        dataset_path, log_path,
        status=f"Hooked acquisition complete across {len(positions)} position(s).",
        positions=len(positions),
        positions_planned=len(positions), positions_completed=len(positions),
        frames_planned=len(events), frames_acquired=len(events),
        reservation_frames_planned=plan.frames,
        hook_extra_exposures_planned=plan.frames - len(events),
        started_at=started_at.isoformat(), completed_at=completed_at.isoformat(),
        duration_s=round(time.monotonic() - started, 6),
        **_reservation_report(reservation),
    )


class SurveyProgress:
    """How many survey images have come back.

    Written by the processor thread (the hook), read by the event thread (the
    generator in _acquire_survey_with_detector) — so it is a real cross-thread
    object, not a counter. Nothing else in the runner tracks acquisition
    progress; this is the one genuinely new primitive design/24 introduces.
    """

    def __init__(self, n_survey: int) -> None:
        self._n_survey, self._lock, self._done = n_survey, threading.Lock(), 0
        self._done_early = False
        self._budget_exhausted = False

    def image_done(self) -> None:
        with self._lock:
            self._done += 1

    def set_total(self, n_survey: int) -> None:
        """Set the event total once the acquisition plan has been built."""
        with self._lock:
            if self._done:
                raise RuntimeError("survey total cannot change after images arrive")
            self._n_survey = n_survey

    def expect_one_more(self) -> None:
        """An extra frame has just been committed to the candidates queue.

        Completion is measured against frames the survey will actually receive,
        so an authorized refocus raises the total only when its re-exposure is
        really queued. Sizing by the authorized budget instead means a survey
        that does not spend it never reaches its total and dies on the idle
        watchdog: on M5 2026-08-11 three of four budgeted surveys visited every
        planned tile and still logged `stalled` sixty seconds later, while the
        one run with no budget completed cleanly. Called before image_done() for
        the frame that produced the extra event, so the total can never trail
        the count.
        """
        with self._lock:
            self._n_survey += 1

    def done_early(self) -> None:
        """The hook decided the survey is over before n_survey images came
        back — the adaptive runner's stop signal (design/27 Fix 4). Counting
        alone can never get there: an early stop means the remaining tiles
        were never submitted, so n_done never reaches n_survey. Ordering
        contract: decide; then put the next tile OR call this; then
        image_done().
        """
        with self._lock:
            self._done_early = True

    def budget_exhausted(self) -> None:
        with self._lock:
            self._budget_exhausted = True
            self._done_early = True

    @property
    def exhausted_budget(self) -> bool:
        with self._lock:
            return self._budget_exhausted

    @property
    def n_done(self) -> int:
        with self._lock:
            return self._done

    @property
    def stopped_early(self) -> bool:
        """Whether done_early() ever fired — the result payload reads this so
        "complete across 9 position(s)" can never again describe a run that
        acquired 4 (the 20260716_140329 misreport)."""
        with self._lock:
            return self._done_early

    def survey_complete(self) -> bool:
        with self._lock:
            return self._done_early or self._done >= self._n_survey


# How often the survey generator polls its candidates queue while idle. Each
# empty pass also costs one is_finished() bridge round trip (measured ~<0.1 ms
# on localhost, design/24), so at 0.05 s the poll's bridge duty cycle is ~0.1%.
_CANDIDATE_POLL_S = 0.05


def _survey_event_stream(
    survey_events: list,
    candidates: "queue.Queue",
    progress: SurveyProgress,
    max_idle_s: float,
    hook: Any,
    adaptive: bool = False,
    max_events: int | None = None,
) -> Callable[[Any], Any]:
    """The events-factory for _acquire_with_hooks: a survey whose event stream
    stays OPEN, so a hook can extend it (design/24 Fix 2/2a).

    adaptive=True is design/24's runner tilted the other way (design/27 Fix
    4): only survey_events[0] is pre-dispatched, and every later tile exists
    only if the hook submits it through `candidates` after scoring the frame
    that just arrived. A pre-dispatched grid is unstoppable — it sits in the
    engine's queue within microseconds and no hook return value can cancel it
    (a returned None becomes a ghost exposure, design/27) — whereas an event
    that was never submitted needs no skip mechanism. Stopping is NOT PUTTING
    the next tile and calling progress.done_early(); the stream drains, the
    finally puts the terminator, and the acquisition ends cleanly.

    EventQueue.get() expands a generator in place and only reads the next queue
    item — mark_finished()'s None — once the generator raises StopIteration. So
    a generator that yields the survey and then blocks, feeding follow-up
    events as the hook finds them, holds pycro-manager's event source open for
    the whole scan. The hook must NOT put() to pycro-manager's own event queue
    — that is a silent no-op under this runner too (the terminator is queued
    ahead of it); it puts to `candidates`, which this generator drains.

    The watchdog measures IDLENESS, not elapsed time: the survey is DISPATCHED
    at socket speed and EXECUTED at camera speed, so a deadline started at
    submission is a cap on the whole scan and silently drops every late
    detection once it blows — the very bug this runner exists to remove
    (design/24 Fix 2a; spike A_3 is its regression test). max_idle_s is a
    stall detector, sized against the slowest thing ONE tile can legitimately
    do (hardware autofocus included), never against the length of the scan.
    """

    def factory(acq):
        # The acquisition's REAL event queue: the generator owns the
        # terminator (see the finally). And the same abort observable the real
        # event source polls before every send (java_backend_acquisitions.py),
        # so an abort is seen promptly with the right log word instead of
        # max_idle_s late as a "stall".
        event_queue = acq._event_queue

        def acq_finished() -> bool:
            try:
                return bool(acq._acq.is_finished())
            except Exception:
                return False

        def event_stream():
            emitted = 0
            try:
                if adaptive:
                    if survey_events and (max_events is None or emitted < max_events):
                        emitted += 1
                        if max_events is not None and emitted >= max_events and hasattr(hook, "close_adaptive_handoff"):
                            hook.close_adaptive_handoff()
                        yield survey_events[0]  # the ONLY pre-dispatched event
                else:
                    yield from survey_events      # dispatched in microseconds...
                last_activity = time.monotonic()  # ...executed over the next minutes
                last_count = progress.n_done

                while True:
                    try:
                        event = candidates.get(timeout=_CANDIDATE_POLL_S)
                    except queue.Empty:
                        pass
                    else:
                        if max_events is not None and emitted >= max_events:
                            _note_budget_exhausted(hook, max_events)
                            progress.budget_exhausted()
                            return
                        last_activity = time.monotonic()
                        emitted += 1
                        if max_events is not None and emitted >= max_events and hasattr(hook, "close_adaptive_handoff"):
                            hook.close_adaptive_handoff()
                        yield event
                        continue

                    # BOTH conditions: the hook put()s a candidate before it
                    # marks the image done, so an empty queue over a complete
                    # survey is the only state in which no detection can still
                    # be in flight.
                    if progress.survey_complete() and candidates.empty():
                        return

                    if acq_finished():
                        hook.note_aborted()
                        return

                    # Images still arriving means the rig is alive; reset the
                    # watchdog. It fires only on a genuine stall — an image
                    # that never comes back — never because the survey is long.
                    n = progress.n_done
                    if n != last_count:
                        last_count, last_activity = n, time.monotonic()

                    if time.monotonic() - last_activity > max_idle_s:
                        hook.note_stalled(max_idle_s)   # loud in the log, not silent
                        return
            finally:
                # The generator OWNS the terminator (design/24 Fix 2a, spike
                # A_10): Acquisition.abort() clears the queue, mark_finished()'s
                # None included, and a generator that trusts that None leaves
                # the event source blocked forever in get() and __exit__ joined
                # to it forever, uninterruptibly. Runs on every exit path; an
                # extra None on the normal path is read by nobody.
                event_queue.put(None)

        return event_stream()

    return factory


def _acquire_survey_with_detector(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    positions: list[dict],
    save_dir: str,
    name: str,
    hook: Any,
    progress: SurveyProgress,
    candidates: "queue.Queue",
    max_idle_s: float = 60.0,
    channel: str | None = None,
    exposure_ms: float | None = None,
    adaptive: bool = False,
    illumination_envelope: dict | None = None,
    artifact_limits: dict | None = None,
    autofocus_budget: dict | None = None,
    named_stage_envelope: dict | None = None,
    property_envelope: dict | None = None,
    hook_action_plan: list[dict] | None = None,
    acquire_plan: AcquisitionPlan | None = None,
    acquire_hits: list[dict] | None = None,
    acquire_max_hits: int | None = None,
    search_phase_channel: str | None = None,
    acquire_phase_channel: str | None = None,
    **shape_kwargs: Any,
) -> dict:
    """One survey acquisition whose event stream a detector hook can EXTEND.

    The generator-backed runner design/24 specifies and design/26's
    mode="acquire" needs: the survey events are yielded up front, then the
    stream stays open draining `candidates` — derived events the hook enqueues
    from image_process_fn — until the survey is complete with nothing in
    flight, the acquisition is aborted, or nothing has happened for
    max_idle_s. The caller constructs `progress` and `candidates` and hands
    the same objects to the hook, whose contract is load-bearing (design/24
    Fix 2a, spikes A_7/A_8): analyze survey-labelled frames only, guard every
    derived event (check_xy/check_z) before enqueueing, and put() the
    candidate BEFORE calling progress.image_done().

    adaptive=True (design/27 Fix 4) submits ONE EVENT AT A TIME instead: the
    built tile list is handed to the hook as hook.survey_events (seed at index
    0), only survey_events[0] is pre-dispatched, and the hook decides per
    frame whether the next tile exists — candidates.put(the next tile) to
    continue, progress.done_early() to stop. Use it only where what we see
    must change what we do (stop-on-condition, refine-where-interesting):
    one event in flight serializes each tile behind the previous frame's
    scoring, a cost inherent to the decision, not the mechanism. A fixed
    survey that just reports keeps the batched pre-dispatch above.

    positions are {name, x_um, y_um} dicts; shape_kwargs carry the
    per-position event shape, as in _acquire_positions_with_hook.
    """
    from microclaw.hook_decisions import UntrustedHookAdapter

    if isinstance(hook, UntrustedHookAdapter):
        if not adaptive:
            raise ValueError(
                "Saved untrusted hooks are not supported by the non-adaptive "
                "survey-with-detector runner; use run_adaptive_survey."
            )
        from microclaw.hook_manager import validate_hook_contract
        contract_errors = validate_hook_contract(hook, required_callback="analyze_frame")
        if contract_errors:
            # A legacy saved hook has no way to reach candidates/progress, so it
            # can never advance the survey past the seed tile. Left to run, the
            # generator would idle out max_idle_s and log "stalled" — a
            # structural impossibility reported as a hardware symptom. Refuse
            # before any position is exposed.
            raise ValueError(
                f"Adaptive analyze_frame contract failed: {contract_errors[0]} "
                "This saved hook defines only a legacy image_process_fn, so it "
                "cannot propose ContinueSurvey or StopSurvey and can never "
                "advance an adaptive survey past the seed tile. Give it an "
                "analyze_frame(image, metadata) method, or run it under a "
                "batched runner (run_tile_acquisition, run_timelapse, "
                "run_zstack)."
            )
    save_dir = guard.resolve_in_workspace(save_dir)

    # The stage is driven by the Acquisition, so check every survey point up
    # front — the refusal must not arrive mid-scan. Derived events are guarded
    # by the hook at enqueue time; nothing else ever sees them.
    for p in positions:
        guard.check_xy(p["x_um"], p["y_um"])
    if "z_start" in shape_kwargs:
        guard.check_z(shape_kwargs["z_start"])
        guard.check_z(shape_kwargs["z_end"])
    if channel:
        _check_acquisition_channel(ctrl, guard, channel)
    if exposure_ms is not None:
        guard.check_exposure(exposure_ms)
        if not channel:
            ctrl.core.set_exposure(exposure_ms)   # eventless exposure; see run_zstack

    survey_events = _build_acquisition_events(
        channel=channel, exposure_ms=exposure_ms,
        position_labels=[p["name"] for p in positions],
        xy_positions=[(p["x_um"], p["y_um"]) for p in positions],
        **shape_kwargs,
    )
    _configure_hook_capabilities(
        hook, ctrl, guard, save_dir, name, illumination_envelope, artifact_limits,
        named_stage_envelope=named_stage_envelope,
        property_envelope=property_envelope, hook_action_plan=hook_action_plan,
        events=survey_events, adaptive=adaptive,
    )

    autofocus_reexposures = 0
    if autofocus_budget is not None:
        if not isinstance(hook, UntrustedHookAdapter):
            raise ValueError("autofocus_budget applies only to saved generated hooks.")
        allowed = {"max_exposures", "z_range_um", "z_step_um", "method", "settle_ms"}
        if set(autofocus_budget) != allowed:
            raise ValueError(f"autofocus_budget must contain exactly {sorted(allowed)}.")
        budget = dict(autofocus_budget)
        if isinstance(budget["max_exposures"], bool) or not isinstance(budget["max_exposures"], int) or budget["max_exposures"] <= 0:
            raise ValueError("autofocus_budget max_exposures must be a positive integer.")
        for key in ("z_range_um", "z_step_um"):
            value = budget[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"autofocus_budget {key} must be finite and positive.")
        if budget["method"] not in {"coarse_then_fine", "single_sweep"}:
            raise ValueError("autofocus_budget method must be 'coarse_then_fine' or 'single_sweep'.")
        if isinstance(budget["settle_ms"], bool) or not isinstance(budget["settle_ms"], int) or budget["settle_ms"] < 0:
            raise ValueError("autofocus_budget settle_ms must be a non-negative integer.")
        sweep_exposures = (
            coarse_then_fine_plane_count(budget["z_range_um"],
                                          max(budget["z_step_um"] * 5, 1.0),
                                          budget["z_step_um"])
            if budget["method"] == "coarse_then_fine" else
            sweep_plane_count(-budget["z_range_um"] / 2,
                              budget["z_range_um"] / 2, budget["z_step_um"])
        )
        hook.configure_autofocus(
            ctrl=ctrl, guard=guard, sweep_exposures=sweep_exposures,
            focus_lock_check=lambda: get_focus_lock_state(ctrl, guard), **budget,
        )
        autofocus_reexposures = hook.planned_refocus_reexposures()

    # Completion is measured in returned images, so its total is the event
    # plan, not the number of XY positions. A multi-frame tile contributes one
    # completion unit per frame; a refocus adds its re-exposure through
    # SurveyProgress.expect_one_more() at the moment it is queued, never from
    # the authorized budget -- see that method for what sizing it up front cost.
    # max_events below is the dose *cap* and does carry the budget, which is a
    # different quantity from what the survey expects to receive.
    progress.set_total(len(survey_events))

    if isinstance(hook, UntrustedHookAdapter):
        if adaptive:
            hook.configure_adaptive(
                events=survey_events, candidates=candidates, progress=progress,
                guard=guard, max_events=len(survey_events) + autofocus_reexposures,
                acquire_hits=acquire_hits, max_hits=acquire_max_hits,
                read_z=ctrl.core.get_position if acquire_hits is not None else None,
            )
    else:
        # Reviewed built-ins retain the legacy direct control contract.
        hook.candidates = candidates
        hook.progress = progress
    if adaptive and not isinstance(hook, UntrustedHookAdapter):
        # The tile list becomes state the hook walks, one candidates.put()
        # per decision; the stream pre-dispatches only survey_events[0].
        # Deliberate limit: the reservation covers exactly the planned grid.
        # A hook may revisit a planned tile but may not add a derived extra
        # frame; widening that requires a separately planned event allowance.
        hook.survey_events = survey_events
    events = _survey_event_stream(survey_events, candidates, progress, max_idle_s, hook,
                                  adaptive=adaptive,
                                  max_events=(len(survey_events) + autofocus_reexposures)
                                  if adaptive else None)
    reservation = (
        _authorize_acquisition(
            ctrl, guard, _plan_with_hook_dose(
                plan_events(ctrl, survey_events, exposure_ms), hook
            )
        )
        if adaptive else None
    )
    acquire_reservation = None
    search_channel_effects = None
    planned_acquire_effects = None
    try:
        if acquire_plan is not None:
            acquire_reservation = _authorize_acquisition(ctrl, guard, acquire_plan)
        if acquire_phase_channel is not None:
            planned_acquire_effects = _channel_effects_for_later_phase(
                ctrl, guard, acquire_phase_channel
            )
        if search_phase_channel is not None:
            search_channel_effects = _set_channel_for_composite(
                ctrl, guard, search_phase_channel
            )
        dataset_path = _acquire_with_hooks(
            guard, save_dir, name, events, hook, reservation=reservation
        )
    except _HookedAcquisitionFailure as exc:
        # The two fixed runners already translate this; the survey runner did
        # not, so every adaptive abort reported an error string and dropped the
        # partial dataset path, the frames exposed and the last known hardware
        # state. Measured on M5, 2026-08-17, across three aborted runs -- the
        # operator read the axis back by hand each time. design/52
        # §"Failure semantics and audit" owes all three on this path too.
        if acquire_reservation is not None:
            acquire_reservation.close()
        if reservation is not None:
            reservation.close()
        return _hooked_failure_result(exc, getattr(hook, "log_path", None))
    except Exception:
        if acquire_reservation is not None:
            acquire_reservation.close()
        if reservation is not None:
            reservation.close()
        raise
    stage_restoration = getattr(hook, "_named_stage_restoration", None)
    property_restoration = getattr(hook, "_property_restoration", None)
    return _adaptive_result(
        dataset_path, hook.log_path,
        status=f"Survey acquisition complete across {len(positions)} position(s).",
        positions=len(positions),
        **(_reservation_report(reservation) if reservation is not None else {}),
        **({"_acquire_reservation": acquire_reservation}
           if acquire_reservation is not None else {}),
        **({"_search_channel_effects": search_channel_effects}
           if search_channel_effects is not None else {}),
        **({"_planned_acquire_effects": planned_acquire_effects}
           if planned_acquire_effects is not None else {}),
        **({"named_stage_restoration": stage_restoration}
           if stage_restoration is not None else {}),
        **({"property_restoration": property_restoration}
           if property_restoration is not None else {}),
    )


@emits(_emit_adaptive_survey)
@_acquisition_entry_point
def run_adaptive_survey(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    protocol: str,
    save_dir: str,
    hook_strategy: str,
    position_names: list[str] | None = None,
    positions: list[dict] | None = None,
    name: str = "survey",
    protocol_params: dict | None = None,
    hook_params: dict | None = None,
    log_path: str | None = None,
    max_idle_s: float = 60.0,
    preserve_unsupported: bool = False,
    illumination_envelope: dict | None = None,
    artifact_limits: dict | None = None,
    autofocus_budget: dict | None = None,
    acquire_on_hit: dict | None = None,
    named_stage_envelope: dict | None = None,
    property_envelope: dict | None = None,
    hook_action_plan: list[dict] | None = None,
) -> dict:
    """Acquire positions one at a time; the hook decides whether the next
    position is acquired at all.

    The tool surface for _acquire_survey_with_detector(adaptive=True) — the
    design/27 Fix 4 runner. Before this existed the adaptive stack was
    reachable only from tests: rig run 20260716_140329 wrote a correct
    adaptive hook and had no tool to drive it (a raise at frame 1 was the
    best available outcome). This is that missing caller: it builds the
    position list, constructs the SurveyProgress/candidates pair, resolves
    the hook, and hands all three to the runner.

    Positions are visited in the order given — pass the list reversed for a
    reverse scan. Supply either position_names (labels in the MM position
    list) or positions ({name, x_um, y_um} dicts), not both. Per-position Z
    is not supported (the survey runner drives XY; a zstack protocol sweeps
    the same absolute Z range at every tile).

    The hook must implement the adaptive contract, which differs by provenance
    (see hook_docs "Skipping and stopping"). A saved hook returns a HookResult
    from analyze_frame carrying ContinueSurvey or StopSurvey, and trusted parent
    code dispatches it; a reviewed built-in keeps the direct contract, where the
    runner sets hook.survey_events / hook.candidates / hook.progress and the hook
    submits the next tile with candidates.put() OR calls progress.done_early(),
    then image_done(). A built-in that only logs runs fine here too, but
    serialized — prefer run_tile_acquisition / run_multiposition_acquisition for
    fixed surveys that just report. A saved hook that only logs is REFUSED here:
    with the runner state parent-side it has no way to ask for the next tile, so
    it would idle out max_idle_s at the seed and report a stall.
    """
    if position_names is not None and positions is not None:
        return {"error": "Provide position_names or positions, not both."}
    if position_names is None and positions is None:
        return {"error": "Provide either position_names or positions."}
    if protocol == "snap":
        return {"error":
                "The adaptive survey needs acquisition images; 'snap' is "
                "display-only. Use protocol='timelapse' with protocol_params="
                "{'n_frames': 1, 'interval_s': 0} for one frame per tile."}

    acquire_params = None
    acquire_protocol = None
    acquire_channel = None
    max_hits = None
    if acquire_on_hit is not None:
        if not isinstance(acquire_on_hit, dict):
            return {"error": "acquire_on_hit must be an object."}
        missing = [key for key in ("channel", "protocol", "protocol_params", "max_hits")
                   if key not in acquire_on_hit]
        if missing:
            return {"error": f"acquire_on_hit is missing {missing}."}
        acquire_channel = acquire_on_hit["channel"]
        acquire_protocol = acquire_on_hit["protocol"]
        acquire_params = dict(acquire_on_hit["protocol_params"])
        if "z_start_um" in acquire_params or "z_end_um" in acquire_params:
            return {"error": "acquire_on_hit.protocol_params refuses absolute "
                    "z_start_um/z_end_um; use z_offset_start_um/z_offset_end_um."}
        max_hits = acquire_on_hit["max_hits"]
        if isinstance(max_hits, bool) or not isinstance(max_hits, int) or max_hits <= 0:
            return {"error": "acquire_on_hit.max_hits must be a positive integer."}
        if acquire_protocol == "zstack":
            try:
                acquire_shape_for_plan = {
                    "z_start_um": acquire_params["z_offset_start_um"],
                    "z_end_um": acquire_params["z_offset_end_um"],
                    "z_step_um": acquire_params["z_step_um"],
                }
            except KeyError as exc:
                return {"error": f"acquire_on_hit.protocol_params for 'zstack' "
                        f"is missing {exc}."}
            acquire_shape_for_plan.update(
                {k: v for k, v in acquire_params.items()
                 if k not in {"z_offset_start_um", "z_offset_end_um", "z_step_um"}}
            )
        elif acquire_protocol == "timelapse":
            acquire_shape_for_plan = acquire_params
        else:
            return {"error": f"Unknown acquire_on_hit protocol {acquire_protocol!r}."}

    params = protocol_params or {}
    try:
        shape = _protocol_shape_kwargs(protocol, params)
    except ValueError as e:
        return {"error": str(e)}
    except KeyError as e:
        return {"error": f"protocol_params for '{protocol}' is missing {e}."}

    if position_names is not None:
        projection, conflict = _preflight_native_positions(
            ctrl, guard, preserve_unsupported=preserve_unsupported
        )
        if conflict:
            return conflict
        all_positions = {p["name"]: p for p in projection.positions}
        missing = [n for n in position_names if n not in all_positions]
        if missing:
            return {"error": f"Positions not found in position list: {missing}"}
        resolved = [{"name": n,
                     "x_um": all_positions[n]["x_um"],
                     "y_um": all_positions[n]["y_um"]} for n in position_names]
    else:
        if any(p.get("name") is None for p in positions):
            return {"error": "Adaptive survey positions must have a non-null name."}
        resolved = [{"name": p["name"], "x_um": p["x_um"], "y_um": p["y_um"]}
                    for p in positions]

    log_path = _prepare_log_path(guard, log_path)
    try:
        hook = _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path)
    except ValueError as e:
        return {"error": str(e)}

    if acquire_on_hit is not None:
        from microclaw.hook_decisions import UntrustedHookAdapter
        if not isinstance(hook, UntrustedHookAdapter):
            return {
                "error": f"acquire_on_hit requires a saved/generated hook that returns "
                         f"typed AcquireAt actions; registry built-in {hook_strategy!r} "
                         "uses the direct candidates queue contract. Use a saved hook "
                         "with analyze_frame(...)->HookResult, or omit acquire_on_hit."
            }

    progress = SurveyProgress(len(resolved))
    candidates: queue.Queue = queue.Queue()
    hits: list[dict] = []
    channel_effects = None
    acquire_plan = None
    if acquire_on_hit is not None:
        search_channel = params.get("channel")
        if not search_channel:
            return {"error": "protocol_params.channel is required with acquire_on_hit."}
        try:
            acquire_plan = _plan_protocol_repetitions(
                ctrl, acquire_protocol, acquire_shape_for_plan, max_hits
            )
        except (SafetyViolation, ValueError, KeyError) as exc:
            return {"error": str(exc)}
        channel_effects = {}
    result = _acquire_survey_with_detector(
        ctrl, guard, resolved, save_dir, name,
        hook=hook, progress=progress, candidates=candidates,
        max_idle_s=max_idle_s,
        channel=None if acquire_on_hit is not None else params.get("channel"),
        exposure_ms=params.get("exposure_ms"),
        adaptive=True, illumination_envelope=illumination_envelope,
        artifact_limits=artifact_limits, autofocus_budget=autofocus_budget,
        named_stage_envelope=named_stage_envelope,
        property_envelope=property_envelope, hook_action_plan=hook_action_plan,
        acquire_plan=acquire_plan,
        acquire_hits=hits if acquire_on_hit is not None else None,
        acquire_max_hits=max_hits,
        search_phase_channel=params.get("channel") if acquire_on_hit is not None else None,
        acquire_phase_channel=acquire_channel,
        **shape,
    )
    acquire_reservation = result.pop("_acquire_reservation", None)
    search_effects = result.pop("_search_channel_effects", None)
    planned_acquire_effects = result.pop("_planned_acquire_effects", None)
    if acquire_on_hit is not None:
        channel_effects["search"] = search_effects
        channel_effects["acquire"] = planned_acquire_effects
    hits_acquired = 0
    acquire_phase_ran = False
    if acquire_on_hit is not None:
        try:
            if hits:
                channel_effects["acquire"] = _set_channel_for_composite(
                    ctrl, guard, acquire_channel
                )
                if acquire_params.get("exposure_ms") is not None:
                    guard.check_exposure(acquire_params["exposure_ms"])
                    ctrl.core.set_exposure(acquire_params["exposure_ms"])
                acquire_phase_ran = True
                acquire_events = []
                for hit in hits:
                    guard.check_xy(hit["x_um"], hit["y_um"])
                    if acquire_protocol == "timelapse":
                        guard.check_z(hit["z_um"])
                        hit_shape = _protocol_shape_kwargs(acquire_protocol, acquire_params)
                    else:
                        z_start = hit["z_um"] + acquire_params["z_offset_start_um"]
                        z_end = hit["z_um"] + acquire_params["z_offset_end_um"]
                        guard.check_z(z_start)
                        guard.check_z(z_end)
                        hit_shape = {"z_start": z_start, "z_end": z_end,
                                     "z_step": acquire_params["z_step_um"]}
                    events_for_hit = _build_acquisition_events(
                        channel=None, exposure_ms=acquire_params.get("exposure_ms"),
                        position_labels=[hit["name"]],
                        xy_positions=[(hit["x_um"], hit["y_um"])], **hit_shape,
                    )
                    if acquire_protocol == "timelapse":
                        for event in events_for_hit:
                            event["z"] = hit["z_um"]
                    acquire_events.extend(events_for_hit)
                acquire_path = _acquire_with_hooks(
                    guard, save_dir, f"{name}_acquire", acquire_events,
                    reservation=acquire_reservation,
                )
                hits_acquired = len(hits)
                result["acquire_dataset_path"] = acquire_path
            elif acquire_reservation is not None:
                acquire_reservation.close()
        except Exception:
            if acquire_reservation is not None:
                acquire_reservation.close()
            raise
    # The batched status ("complete across 9 position(s)") is exactly the
    # sentence that made 5 ghost exposures read as a clean early stop
    # (20260716_140329). Say what actually ran, from the counter the hook
    # itself drove — and attach the planned coordinates so the hook log
    # joins on `position` without re-imaging (design/23 Episode A).
    if "error" in result:
        # An aborted run keeps its failure report verbatim — dataset path,
        # frames exposed, last known hardware state, and design/38 F7's "do not
        # treat the run as untouched" hint. The rewrites below would dress it as
        # a completed survey and replace exactly that warning.
        return result
    stopped = progress.stopped_early
    result.pop("positions", None)   # "positions: 9" is the ambiguity this tool retires
    # "acquired of N planned tile(s)" read as coverage, and a hook may revisit a
    # planned tile instead of advancing: D4 case 1 acquired two frames at p0 and
    # never visited p1, and the reading agent reported "both planned tiles were
    # acquired (2 of 2)". Frames on one side of "of" and tiles on the other is
    # the same conflation `positions: 9` was removed for.
    result["status"] = (
        f"Adaptive survey: {progress.n_done} frame(s) acquired from a "
        f"{len(resolved)}-tile plan"
        + (", stopped early by the hook." if stopped else ".")
    )
    result["frames_acquired"] = progress.n_done
    result["stopped_early"] = stopped
    if log_path:
        # Deliberately replace _adaptive_result's generic read-back hint with the
        # survey-specific warning: control state is not a content measurement.
        result["hint"] = (
            "stopped_early describes the hook's control decisions, not what was found. "
            "Per-tile measurements are in log_path; call read_hook_log before saying "
            "anything about content."
        )
    else:
        result["hint"] = (
            "stopped_early describes the hook's control decisions, not what was found. "
            "No per-tile log was written for this run, so there is nothing to read back."
        )
    # Only when typed actions were actually observed at the parent dispatch. An
    # empty dict is not "zero decisions" — it is a hook that never routed one
    # through the parent, which is the normal shape for a precoded control hook
    # AND for a saved hook that only records measurements (HookResult.actions
    # defaults to ()). Emitting {"ContinueSurvey": 0} there says, in the one
    # content-shaped field this result has, that the hook decided nothing on a
    # run where it continued at every tile. That is F8's defect in a new key.
    action_counts = getattr(hook, "action_counts", None)
    if action_counts:
        result["hook_actions"] = {
            "ContinueSurvey": action_counts.get("ContinueSurvey", 0),
            "StopSurvey": action_counts.get("StopSurvey", 0),
        }
    result["budget_exhausted"] = progress.exhausted_budget
    result["tiles_planned"] = [
        {"position": p["name"], "x_um": round(p["x_um"], 3),
         "y_um": round(p["y_um"], 3)} for p in resolved
    ]
    if acquire_on_hit is not None:
        acquire_frames_reserved = acquire_reservation.plan.frames
        acquire_frames_accounted = acquire_reservation.completed_frames
        result["hits_recorded"] = len(hits)
        result["hits_acquired"] = hits_acquired
        result["max_hits_reached"] = len(hits) >= max_hits
        result["acquire_phase_ran"] = acquire_phase_ran
        result["channel_effects"] = channel_effects
        result["acquire_frames_reserved"] = acquire_frames_reserved
        result["acquire_frames_accounted"] = acquire_frames_accounted
        result["acquire_frames_unused"] = (
            acquire_frames_reserved - acquire_frames_accounted
        )
        result["hits"] = [{k: v for k, v in hit.items() if k != "_key"}
                          for hit in hits]
    return result


# --- The read side: open what we wrote (design/42) ---

def _sidecar_manifest(resolved: Path) -> dict | None:
    """The manifest microclaw wrote beside this artifact, if it wrote one.

    build_stage_coordinate_mosaic writes `<artifact><suffix>.json` — the same
    spelling reproduced here rather than guessed. Anything else living at that
    name is not ours, and reading it as provenance would be worse than having
    none, so the shape is checked before it counts.
    """
    sidecar = resolved.with_suffix(resolved.suffix + ".json")
    if not sidecar.is_file():
        return None
    try:
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or "manifest_payload" not in manifest:
        return None
    return manifest


def _verify_against_manifest(manifest: dict, resolved: Path) -> dict:
    """Recompute both digests the writer recorded, and report match/mismatch.

    A mismatch does NOT stop the file opening. The operator is entitled to look
    at a file whose provenance failed — that is often exactly the file they need
    to look at — so this reports and never refuses.
    """
    inner = manifest.get("manifest_payload")
    if not isinstance(inner, dict):
        return {"provenance": ("A manifest sits beside this file but has no "
                               "manifest_payload; nothing could be verified.")}
    payload_bytes = json.dumps(
        inner, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    verified = {
        "kind": inner.get("kind"),
        "manifest_path": str(resolved.with_suffix(resolved.suffix + ".json")),
        "manifest_payload_sha256_matches":
            hashlib.sha256(payload_bytes).hexdigest()
            == manifest.get("manifest_payload_sha256"),
    }
    recorded_pixels = inner.get("pixel_sha256")
    if recorded_pixels is not None:
        try:
            pixels = tifffile.imread(resolved)
            digest = hashlib.sha256(
                pixels.astype(np.uint16, copy=False).tobytes(order="C")
            ).hexdigest()
            verified["pixel_sha256_matches"] = digest == recorded_pixels
        except Exception as exc:
            verified["pixel_sha256_matches"] = None
            verified["pixel_sha256_unverified"] = (
                f"Could not re-read the pixels to check them: "
                f"{type(exc).__name__}: {exc}"
            )
    # The numbers that make the picture readable, straight from the manifest.
    verified |= {key: inner[key] for key in
                 ("coverage_fraction", "origin_um", "extent_um",
                  "output_basis_um", "overwrite_convention") if key in inner}
    if "calibration_warning" in inner:
        verified["calibration_warning"] = inner["calibration_warning"]
    return verified


def _measured_shape(resolved: Path) -> dict:
    """What Python reads from the artifact, to check the window against.

    A bridge call returning is not proof a window painted, and a title match
    alone is nearly self-confirming — the dimensions are the load-bearing part
    (design/42). Header reads only for a TIFF; for a dataset, the index plus one
    plane. Zero exposure either way.

    A directory is read as an NDTiff dataset first, which is the case that
    carries plane counts across split stack files. When it is not one, this falls
    back to the TIFFs that `open_in_imagej` would actually open — otherwise a
    directory holding a stray TIFF beside its datasets reports an error here
    while a window is on screen, and `dimensions_match` disappears from a payload
    that still says `opened: true` (round-3 gate, `D:\\stitch_test`).
    """
    try:
        if resolved.is_dir():
            try:
                dataset = Dataset(str(resolved))
                coordinates = dataset.get_image_coordinates_list()
                if not coordinates:
                    return {"error": "The dataset index lists no images."}
                plane = dataset.read_image(**coordinates[0])
                return {"width": int(plane.shape[-1]),
                        "height": int(plane.shape[-2]),
                        "n_planes": len(coordinates)}
            except Exception as dataset_exc:
                return _measured_stack_files(resolved, dataset_exc)
        return _measured_tiff(resolved)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _measured_tiff(path: Path) -> dict:
    with tifffile.TiffFile(path) as handle:
        series = handle.series[0]
        shape = tuple(int(x) for x in series.shape)
        return {"width": shape[-1], "height": shape[-2],
                "n_planes": int(np.prod(shape[:-2])) if len(shape) > 2 else 1}


def _measured_stack_files(directory: Path, dataset_exc: Exception) -> dict:
    """Measure the TIFFs a non-dataset directory would open, one entry each.

    `files` is what makes this checkable when a directory holds more than one:
    a single width/height could only be compared against one window, and the
    others would ride along unchecked.
    """
    measured = []
    for stack in dataset_stack_files(directory):
        try:
            measured.append({"file": stack.name, **_measured_tiff(stack)})
        except Exception as exc:
            measured.append({"file": stack.name,
                             "error": f"{type(exc).__name__}: {exc}"})
    if not measured:
        return {"error": f"{type(dataset_exc).__name__}: {dataset_exc}"}
    readable = [m for m in measured if "width" in m]
    if not readable:
        return {"not_a_dataset": str(dataset_exc), "files": measured}
    return {
        "width": readable[0]["width"],
        "height": readable[0]["height"],
        "n_planes": sum(m["n_planes"] for m in readable),
        "not_a_dataset": str(dataset_exc),
        "files": measured,
    }


def _select_plane(resolved: Path, axis_selection: dict | None) -> tuple[np.ndarray, dict]:
    """One 2-D plane to render, or a ValueError naming what is missing.

    Refuses on an ambiguous stack rather than silently rendering plane 0 and
    letting the model describe it as "the image". Axis names are the ones
    tifffile reads out of the file, lowercased, so the refusal can name the
    exact keys a retry needs.
    """
    with tifffile.TiffFile(resolved) as handle:
        series = handle.series[0]
        axes = str(series.axes)
        array = series.asarray()
    array = np.squeeze(array)
    if array.ndim == 2:
        return array, {}
    stack_axes = [name.lower() for name, length in zip(axes, series.shape)
                  if length > 1][:-2]
    if not stack_axes or len(stack_axes) != array.ndim - 2:
        # Axis labels and array rank disagree; index positionally rather than
        # invent names for something we cannot describe.
        stack_axes = [f"axis{i}" for i in range(array.ndim - 2)]
    selection = axis_selection or {}
    unknown = set(selection) - set(stack_axes)
    if unknown:
        raise ValueError(
            f"axis_selection names axes this file does not have: {sorted(unknown)}. "
            f"Its stack axes are {stack_axes} with lengths "
            f"{list(array.shape[:-2])}."
        )
    missing = [name for name in stack_axes if name not in selection]
    if missing:
        raise ValueError(
            f"This file is a {list(array.shape)} stack, so 'the image' is "
            f"ambiguous. Pass axis_selection with an index for each of "
            f"{missing} (lengths {list(array.shape[:-2])}) and call again."
        )
    index = []
    for name, length in zip(stack_axes, array.shape[:-2]):
        value = selection[name]
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < length:
            raise ValueError(
                f"axis_selection['{name}'] must be an integer in 0..{length - 1}; "
                f"got {value!r}."
            )
        index.append(value)
    return array[tuple(index)], {name: selection[name] for name in stack_axes}


@emits_nothing
def open_artifact(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    path: str,
    analyze: bool = False,
    axis_selection: dict | None = None,
    max_size: int = 512,
) -> dict | list:
    """Open a file microclaw wrote in Micro-Manager, and say what it is.

    Zero exposure; moves nothing. Opens a NEW window and leaves it — microclaw
    never closes or re-uses the user's windows — and never renders the pixels
    into the conversation unless `analyze` asks it to.

    `opened` is a **boolean at the top level** of the payload, beside `via` and
    `windows`, exactly as open_in_imagej reports them. Reading it must not
    require reaching through a container, because the one thing a caller has to
    get right here is not claiming a window that did not appear.
    """
    resolved = Path(guard.resolve_readable_path(path))
    if not resolved.exists():
        return {"error": f"Artifact not found: {resolved}"}

    if resolved.suffix.lower() in TEXT_SUFFIXES:
        # ImageJ reads the first bytes as an image header, fails, and can leave
        # the bridge wedged: on M5 2026-08-11 an exported .py produced
        # "not a TIFF file: header=b'from'" and cost a Micro-Manager restart
        # mid-gate. Text opens in the platform's normal text editor.
        via = open_in_editor(resolved)
        return {
            "path": str(resolved), "opened": True, "via": via, "windows": [],
            "provenance": (
                "Opened as text in this machine's editor, not in ImageJ — "
                "ImageJ reads files as images and cannot display a script."
            ),
        }

    # open_in_imagej's keys are lifted, not nested. Nesting them under a key of
    # their own gave the payload a top-level `opened` that was a *dict* — truthy
    # even when the open failed — and "do not claim a window unless opened is
    # true" is the one instruction in this tool that must not be able to mislead.
    payload: dict = {"path": str(resolved), **ctrl.open_in_imagej(str(resolved))}
    measured = _measured_shape(resolved)
    payload["measured"] = measured
    windows = payload.get("windows") or []
    if payload.get("opened") and "width" in measured:
        # With per-file measurements every window is checked against some file
        # that was read; without them there is one shape to compare against.
        # `any` over windows would let extra windows ride along unchecked.
        shapes = [(m["width"], m["height"]) for m in measured.get("files", ())
                  if "width" in m] or [(measured["width"], measured["height"])]
        payload["dimensions_match"] = bool(windows) and all(
            (window.get("width"), window.get("height")) in shapes
            for window in windows
        )

    manifest = _sidecar_manifest(resolved)
    if manifest is not None:
        payload |= _verify_against_manifest(manifest, resolved)
    else:
        payload["provenance"] = (
            "No microclaw manifest beside this file; opened, but its origin is "
            "unverified."
        )

    # The default path ends here: the file is on the user's screen and its
    # provenance is stated. Reading the pixels in is a separate, costlier act —
    # an image block stays in the conversation for every subsequent turn.
    if not analyze or resolved.is_dir():
        if analyze and resolved.is_dir():
            payload["analysis_refused"] = (
                "The dataset's stack files are open in ImageJ, but "
                "reading its pixels here needs one plane named: export or "
                "mosaic the frames you want, then analyze that file."
            )
        return payload

    try:
        plane, selection = _select_plane(resolved, axis_selection)
    except Exception as exc:
        payload["analysis_refused"] = str(exc)
        return payload
    payload["selection"] = selection
    mask = plane != 0
    zero_fraction = float(np.count_nonzero(~mask) / mask.size)
    payload["thumbnail_stretch"] = (
        f"2nd-99.8th percentile measured over nonzero pixels only; "
        f"{zero_fraction:.0%} of this image is zero and was excluded from the "
        "stretch, so uncovered area does not flatten the real signal."
    )
    return image_content(payload, plane, max_size=max_size, mask=mask)


@emits_nothing
def read_hook_log(ctrl: MicroscopeController, guard: SafetyGuard, log_path: str) -> dict:
    """Read a hook's output log file after an acquisition completes."""
    log_path = guard.resolve_readable_path(log_path)
    path = Path(log_path)
    if not path.exists():
        return {"error": f"Log file not found: {log_path}"}
    entries = json.loads(path.read_text(encoding="utf-8"))
    return {"log_path": log_path, "entry_count": len(entries), "entries": entries,
            "artifact": {"kind": "hook_log", "path": log_path}}


_COVERAGE_METRICS = frozenset({
    "signal_coverage", "structure_coverage", "signal_concentration",
})


@emits_nothing
def rank_hook_log(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    log_path: str,
    metric: str = "snr",
    budgets: list[int] | None = None,
    position_list_path: str | None = None,
) -> dict:
    """Replay a deterministic whole-record ranking from a completed hook log.

    This is deliberately offline: it neither analyzes images nor moves hardware.
    Equal metric values are ordered by position label, so replay is independent
    of JSON record order and model arithmetic.
    """
    started = time.monotonic()
    log_path = guard.resolve_readable_path(log_path)
    path = Path(log_path)
    if not path.exists():
        return {"error": f"Log file not found: {log_path}"}
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        return {"error": "Hook log must contain a JSON array."}
    seen: set[str] = set()
    rows = []
    invalid_rows = []
    for i, entry in enumerate(entries):
        if entry.get("schema") != "microclaw.analysis-observation/v1":
            continue
        label = entry.get("position")
        result = entry.get("result") or {}
        missing = [k for k in ("position", "x_um", "y_um") if entry.get(k) is None]
        if metric not in result:
            if metric in _COVERAGE_METRICS:
                return {"error": (
                    f"Entry {i} predates the {metric} statistic; this hook log "
                    "must be reacquired before it can be ranked by coverage."
                )}
            missing.append(f"result.{metric}")
        if missing:
            return {"error": f"Entry {i} is missing required field(s): {missing}"}
        if label in seen:
            return {"error": f"Duplicate position in hook log: {label}"}
        seen.add(label)
        valid_key = f"{metric}_valid"
        saturated = result.get("saturated_fraction")
        # A coverage statistic has no validity flag of its own (it is defined for
        # every finite image), so clipping has to be caught here or not at all.
        # Without this, ranking by coverage puts the overexposed tiles on top --
        # measured on the 2026-08-06 M5 raster, where the two highest
        # signal_coverage tiles of 324 were 4.0% and 19.8% saturated and both
        # were frames snr had refused to score. The limit is coverage's own and
        # is far looser than snr's: real bead fields run 0.017%-0.220% saturated
        # and must stay rankable (design/43 F6, block 43g).
        clipped = (metric in _COVERAGE_METRICS and saturated is not None
                   and saturated > MAX_SATURATED_FRACTION_FOR_COVERAGE)
        if result.get(valid_key) is False or clipped:
            invalid_rows.append({
                "position": label, "x_um": entry["x_um"], "y_um": entry["y_um"],
                **({"z_um": entry["z_um"]} if entry.get("z_um") is not None else {}),
                metric: result[metric],
                valid_key: False,
                "invalid_reason": (
                    f"{saturated:.4%} of pixels are saturated (limit "
                    f"{MAX_SATURATED_FRACTION_FOR_COVERAGE:.4%}); a frame this "
                    "clipped cannot say how much of the field is sample."
                    if clipped else result.get(f"{metric}_invalid_reason")
                ),
                "focus_metric_valid": result.get("focus_metric_valid"),
                "saturated_fraction": saturated,
            })
            continue
        try:
            value = float(result[metric])
        except (TypeError, ValueError):
            return {"error": f"Entry {i} has a non-numeric result.{metric}."}
        if not math.isfinite(value):
            return {"error": f"Entry {i} has a non-finite result.{metric}."}
        rows.append({
            "position": label, "x_um": entry["x_um"], "y_um": entry["y_um"],
            **({"z_um": entry["z_um"]} if entry.get("z_um") is not None else {}),
            metric: value,
            valid_key: result.get(valid_key),
            "focus_metric_valid": result.get("focus_metric_valid"),
            "saturated_fraction": result.get("saturated_fraction"),
            "min_snr": entry.get("parameters", {}).get("min_snr"),
            "min_snr_source": entry.get("parameters", {}).get("min_snr_source"),
        })
    rows.sort(key=lambda row: (-row[metric], row["position"]))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    requested = sorted(set(budgets or []))
    if any(not isinstance(k, int) or k < 1 or k > len(rows) for k in requested):
        return {"error": f"Every budget must be an integer from 1 to {len(rows)}."}
    result = {
        "log_path": log_path,
        "metric": metric,
        "ranking_key": f"descending result.{metric}, then ascending position label",
        "metric_validity": (
            "Coverage statistics deliberately have no validity flag; they are "
            "defined for every finite image. Their min_snr threshold and source "
            "travel with each ranked row."
            if metric in _COVERAGE_METRICS
            else f"Rows with result.{metric}_valid false are not ranked."
        ),
        "entry_count": len(rows) + len(invalid_rows),
        "ranked_entry_count": len(rows),
        "invalid_entry_count": len(invalid_rows),
        "ranking": rows,
        "invalid_rows": invalid_rows,
        "budget_views": {str(k): rows[:k] for k in requested},
    }
    if invalid_rows:
        result["warning"] = (
            f"Ranking is incomplete: {len(invalid_rows)} observation(s) had invalid "
            f"{metric} and are listed in invalid_rows rather than ranked."
        )
    if position_list_path:
        position_list_path = guard.resolve_readable_path(position_list_path)
        try:
            projection = ctrl.project_position_list_file(position_list_path)
        except PositionListConflict as e:
            return _position_conflict(
                PositionProjection([], [], e.issues),
                path=e.path, content_hash=e.content_hash,
            )
        selected = projection.native_entries
        actual = [p["name"] for p in selected]
        expected = [r["position"] for r in rows[:len(actual)]]
        coordinate_matches = []
        added_axes = []
        for saved_position, ranked in zip(selected, rows):
            axes = ("x_um", "y_um", "z_um")
            # An axis the RANKING carries but the saved position lost is a real
            # mismatch. An axis the saved position adds is not: a top-k revisit is
            # marked with a focus Z the operator supplies, while a fixed-Z survey
            # stamps no ZPosition_um_Intended and so its records have no z_um at
            # all. Requiring both sides to carry the same axes reported M5's
            # correct k=2 save as a coordinate mismatch (R1, 2026-07-28) with X
            # and Y agreeing exactly — a false alarm on the one check standing
            # between a ranking and what gets re-exposed.
            lost = [a for a in axes if a in ranked and a not in saved_position]
            added_axes.append([a for a in axes if a in saved_position and a not in ranked])
            coordinate_matches.append(
                not lost and all(
                    math.isclose(
                        float(saved_position[axis]), float(ranked[axis]),
                        rel_tol=0.0, abs_tol=1e-3,
                    )
                    for axis in axes
                    if axis in saved_position and axis in ranked
                )
            )
        result["position_list_verification"] = {
            "path": position_list_path,
            "matches_ranking_prefix": actual == expected and all(coordinate_matches),
            "label_match": actual == expected,
            "coordinate_matches": coordinate_matches,
            # Axes the saved position carries that the ranking does not — normally
            # ["z_um"], the focus Z added at marking time. Reported so the operator
            # can see what was added rather than inferring it from a silent pass.
            "axes_added_when_saved": added_axes,
            "projection_issues": projection.issues,
            "expected": expected, "actual": actual,
        }
    result["duration_s"] = round(time.monotonic() - started, 6)
    return result


@emits_nothing
def validate_positions(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    positions: list[dict],
) -> dict:
    """Validate XY/Z coordinates against current guards without moving hardware."""
    accepted, rejected = [], []
    for i, position in enumerate(positions):
        name = position.get("name", position.get("position", f"position_{i}"))
        try:
            if position.get("x_um") is None or position.get("y_um") is None:
                raise ValueError("x_um and y_um are required")
            try:
                guard.check_xy(float(position["x_um"]), float(position["y_um"]))
            except SafetyViolation as exc:
                rejected.append({"name": name,
                                 "reason": str(exc)})
                continue
            if position.get("z_um") is not None:
                try:
                    guard.check_z(float(position["z_um"]))
                except SafetyViolation as exc:
                    rejected.append({"name": name,
                                     "reason": str(exc)})
                    continue
            accepted.append({"name": name, "x_um": position["x_um"],
                             "y_um": position["y_um"],
                             **({"z_um": position["z_um"]}
                                if position.get("z_um") is not None else {})})
        except (TypeError, ValueError) as e:
            rejected.append({"name": name, "reason": str(e)})
    return {"accepted": accepted, "rejected": rejected, "clipped": 0}


@emits_nothing
def inspect_artifacts(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    paths: list[str],
    manifest_path: str | None = None,
    max_files: int = 1000,
    max_total_bytes: int = 1024 * 1024 * 1024,
    max_depth: int = 16,
    hash: bool = True,
) -> dict:
    """Survey local artifacts deterministically, then optionally hash them."""
    limits = (max_files, max_total_bytes, max_depth)
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
           for value in limits):
        return {"error": "Artifact inspection limits must be positive integers."}
    if not isinstance(hash, bool):
        return {"error": "hash must be true or false."}
    files: set[Path] = set()
    survey_by_path: dict[Path, dict] = {}

    def survey() -> list[dict]:
        return [survey_by_path[path] for path in sorted(survey_by_path, key=str)]

    def refusal(message: str) -> dict:
        return {
            "error": message,
            "survey": survey(),
            "survey_totals": {
                "file_count": len(files),
                "total_bytes": sum(path.stat().st_size for path in files),
            },
        }

    for raw in paths:
        resolved = Path(guard.resolve_readable_path(raw))
        if not resolved.exists():
            return refusal(f"Artifact path not found: {resolved}")
        if resolved.is_dir():
            pending = [(resolved, 0)]
            while pending:
                directory, depth = pending.pop()
                direct_count = 0
                direct_bytes = 0
                try:
                    entries = sorted(directory.iterdir(), key=lambda path: str(path))
                except OSError as exc:
                    return refusal(f"Cannot list artifact directory {directory}: {exc}")
                children = []
                for entry in entries:
                    if entry.is_dir() and not entry.is_symlink():
                        children.append(entry)
                        continue
                    if not entry.is_file():
                        continue
                    size = entry.stat().st_size
                    direct_count += 1
                    direct_bytes += size
                    if entry not in files and len(files) >= max_files:
                        survey_by_path[directory] = {
                            "path": str(directory), "depth": depth,
                            "direct_file_count": direct_count,
                            "direct_bytes": direct_bytes,
                        }
                        return refusal(
                            f"Artifact inspection reached max_files={max_files}; "
                            "use the directory survey or narrow paths."
                        )
                    files.add(entry)
                    if hash and sum(path.stat().st_size for path in files) > max_total_bytes:
                        survey_by_path[directory] = {
                            "path": str(directory), "depth": depth,
                            "direct_file_count": direct_count,
                            "direct_bytes": direct_bytes,
                        }
                        return refusal(
                            f"Artifact inspection exceeded max_total_bytes={max_total_bytes}; "
                            "use hash=false or narrow paths."
                        )
                survey_by_path[directory] = {
                    "path": str(directory), "depth": depth,
                    "direct_file_count": direct_count, "direct_bytes": direct_bytes,
                }
                if children and depth >= max_depth:
                    return refusal(
                        f"Artifact inspection reached max_depth={max_depth}; "
                        "use the directory survey or narrow paths."
                    )
                pending.extend((child, depth + 1) for child in reversed(children))
        else:
            if resolved not in files and len(files) >= max_files:
                return refusal(f"Artifact inspection reached max_files={max_files}.")
            files.add(resolved)
    total_bytes = sum(path.stat().st_size for path in files)
    if hash and total_bytes > max_total_bytes:
        return refusal(
            f"Artifact inspection would hash {total_bytes} bytes, exceeding "
            f"max_total_bytes={max_total_bytes}."
        )
    artifacts = []
    for path in sorted(files, key=lambda p: str(p)):
        artifact = {"path": str(path), "size_bytes": path.stat().st_size}
        if hash:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            artifact["sha256"] = digest.hexdigest()
        artifacts.append(artifact)
    result = {"artifact_count": len(artifacts), "total_bytes": total_bytes,
              "hashes_computed": hash, "artifacts": artifacts, "survey": survey()}
    if manifest_path:
        manifest_path = guard.resolve_in_workspace(manifest_path)
        Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(manifest_path).write_text(
            json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
        )
        result["manifest"] = {"kind": "sha256_manifest", "path": manifest_path}
    return result


@emits_nothing
def compare_revisit_frames(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    source_tiff: str,
    revisit_tiff: str,
    comparisons: list[dict],
    min_correlation: float = 0.5,
) -> dict:
    """Register corresponding source/revisit TIFF pages without acquisition."""
    from scipy.ndimage import gaussian_filter, shift as image_shift
    from skimage.registration import phase_cross_correlation

    source_tiff = guard.resolve_readable_path(source_tiff)
    revisit_tiff = guard.resolve_readable_path(revisit_tiff)
    source = tifffile.imread(source_tiff)
    revisit = tifffile.imread(revisit_tiff)
    if source.ndim == 2:
        source = source[None, ...]
    if revisit.ndim == 2:
        revisit = revisit[None, ...]
    affine = _load_current_affine(ctrl)
    rows = []
    for item in comparisons:
        si, ri = int(item["source_index"]), int(item["revisit_index"])
        if not (0 <= si < len(source) and 0 <= ri < len(revisit)):
            return {"error": f"Frame index out of range for {item.get('position', item)}"}
        a, b = source[si].astype(float), revisit[ri].astype(float)
        if a.shape != b.shape:
            return {"error": f"Frame shapes differ for {item.get('position', item)}: "
                             f"{a.shape} versus {b.shape}"}
        # High-pass removes camera offset/illumination drift; a Hann window
        # prevents wrap-around edges from dominating phase correlation.
        window = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
        ah = (a - gaussian_filter(a, 8)) * window
        bh = (b - gaussian_filter(b, 8)) * window
        shift, _, _ = phase_cross_correlation(ah, bh, upsample_factor=100)
        aligned = image_shift(bh, shift, order=1, mode="constant", cval=0)
        margin = int(np.ceil(np.max(np.abs(shift)))) + 3
        mask = np.ones(a.shape, dtype=bool)
        if margin * 2 >= min(a.shape):
            return {"error": f"Registration shift leaves no reliable overlap for "
                             f"{item.get('position', item)}"}
        mask[:margin] = mask[-margin:] = False
        mask[:, :margin] = mask[:, -margin:] = False
        correlation = float(np.corrcoef(ah[mask], aligned[mask])[0, 1])
        row = {
            "position": item.get("position"), "source_index": si, "revisit_index": ri,
            "shift_to_apply_to_revisit_dy_dx_px": [float(shift[0]), float(shift[1])],
            "translation_magnitude_px": float(np.hypot(*shift)),
            "post_registration_correlation": correlation,
            "registration_valid": bool(np.isfinite(correlation) and
                                       correlation >= min_correlation),
        }
        if affine is not None:
            dx_um, dy_um = affine.px_to_um(float(shift[1]), float(shift[0]))
            row.update(translation_stage_dx_um=dx_um, translation_stage_dy_um=dy_um,
                       translation_magnitude_um=float(np.hypot(dx_um, dy_um)),
                       calibration_identity={"objective": affine.objective,
                                             "binning": affine.binning})
        else:
            row["translation_um"] = None
            row["calibration_warning"] = (
                "No stage-camera affine for the current objective/binning."
            )
        rows.append(row)
    return {"source_tiff": source_tiff, "revisit_tiff": revisit_tiff,
            "comparisons": rows}


def calibrate_snr_threshold(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    dark_log_paths: list[str],
    illuminated_log_paths: list[str],
    output_path: str,
    context: dict,
) -> dict:
    """Derive a provisional rig SNR gate from confirmed control logs.

    No frames are acquired. A clean gap is required; overlapping controls are
    reported as non-separable rather than forced into a misleading threshold.
    """
    required_context = {"objective", "camera", "roi", "binning", "exposure_ms", "channel"}
    missing_context = sorted(required_context - set(context))
    if missing_context:
        return {"error": f"Calibration context is missing: {missing_context}"}

    def values(paths: list[str]) -> list[float]:
        out = []
        for raw in paths:
            path = Path(guard.resolve_readable_path(raw))
            records = json.loads(path.read_text(encoding="utf-8"))
            out.extend(float(r["result"]["snr"]) for r in records)
        return out
    if len(dark_log_paths) < 2 or len(illuminated_log_paths) < 2:
        return {"error": "Use at least two confirmed dark and two illuminated logs."}
    dark, illuminated = values(dark_log_paths), values(illuminated_log_paths)
    dark_max, illuminated_min = max(dark), min(illuminated)
    if dark_max >= illuminated_min:
        return {"status": "not_separable", "dark_max_snr": dark_max,
                "illuminated_min_snr": illuminated_min,
                "error": "Confirmed dark and illuminated SNR distributions overlap."}
    recommended = (dark_max + illuminated_min) / 2
    artifact = {
        "schema": "microclaw.snr-calibration/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "context": context,
        "dark_log_paths": [guard.resolve_readable_path(p) for p in dark_log_paths],
        "illuminated_log_paths": [guard.resolve_readable_path(p)
                                  for p in illuminated_log_paths],
        "dark_frame_count": len(dark), "illuminated_frame_count": len(illuminated),
        "dark_max_snr": dark_max, "illuminated_min_snr": illuminated_min,
        "recommended_min_snr": recommended,
        "method": "midpoint between maximum confirmed-dark and minimum confirmed-illuminated SNR",
    }
    output_path = guard.resolve_in_workspace(output_path)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return {**artifact, "artifact": {"kind": "snr_calibration", "path": output_path}}


# --- Hook management ---

# Saving a hook writes a .py file and a manifest entry under ~/.microclaw/hooks
# and touches no hardware, so there is nothing for it to reproduce -- and the
# emitted script does not need it to: an adaptive export inlines the hook's
# source verbatim, with the manifest sha256 as a provenance comment, so the
# class exists in the script without the manifest existing anywhere.
#
# Undecorated it collected the default refusal, which plants a loud
# `raise RuntimeError` at the recorded position. The demo gate of 2026-08-10
# found what that costs: the agent wrote a hook, ran an adaptive survey with it
# and exported -- the exact workflow F14 exists for -- and the script died three
# lines before the adaptive program it had correctly emitted.
@emits_nothing
def generate_and_save_hook(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    name: str,
    code: str,
    description: str,
    source: str = "claude_generated",
    runner_contract: str = "fixed",
) -> dict:
    """Lint and save a hook script. Call ONLY after the user has confirmed the code.

    The lint is advisory: warnings are surfaced and, if any fire, an explicit
    confirmation is required before saving (benign hooks legitimately use
    open/os, so warnings must not hard-block). The human review of the full code
    is the actual gate.
    """
    from microclaw.hook_manager import (
        lint_hook_code, save_hook, saved_hook_source_refusal,
        validate_hook_contract,
    )
    warnings = lint_hook_code(code)
    if runner_contract not in {"fixed", "adaptive"}:
        return {"error": "runner_contract must be 'fixed' or 'adaptive'."}
    required_callback = "analyze_frame" if runner_contract == "adaptive" else None
    contract_errors = validate_hook_contract(code, required_callback=required_callback)
    if contract_errors:
        return {
            "error": "Hook failed static preflight; it was not saved.",
            "contract_errors": contract_errors,
            "warnings": warnings,
            "preflight": "static-only (source was not imported or executed)",
        }
    source_refusal = saved_hook_source_refusal(code)
    if source_refusal["reasons"]:
        return {
            "error": "Hook would be refused at run time; it was not saved.",
            "resolve_refusal": {
                "would_refuse": True,
                "reasons": source_refusal["reasons"],
                "remedy": {
                    "insufficient_for": source_refusal["insufficient_for"],
                    "note": source_refusal["note"],
                },
            },
            "warnings": warnings,
            "preflight": "static-only (source was not imported or executed)",
        }
    if warnings and not CONFIRM_FN(
        f"Hook '{name}' — advisory lint flagged:\n" + "\n".join(warnings)
        + "\n\nSave anyway?",
        kind="hook",
    ):
        return {"error": "User declined after lint warnings.", "warnings": warnings}
    save_hook(name, code, description, source=source)
    return {
        "status": f"Hook '{name}' saved successfully.",
        "path": str(Path.home() / ".microclaw" / "hooks" / f"{name}.py"),
        "source": source,
        "warnings": warnings,
        "preflight": (
            f"Static syntax and {runner_contract} runner contract passed using "
            f"{('analyze_frame' if required_callback else 'the saved-hook callback validator')}. Source was not "
            "imported or executed because no hook sandbox is configured."
        ),
    }


@emits_nothing
def read_hook_from_file(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    path: str,
) -> dict:
    """Read a user-specified hook file and run the AST safety scan.

    Returns the code and any safety warnings so Claude can display both to the
    user before asking for confirmation. Does NOT save the hook.
    """
    from microclaw.hook_manager import read_hook_from_file as _read
    try:
        path = guard.resolve_readable_path(path)
        code, warnings = _read(path)
    except SafetyViolation as e:
        return {"error": str(e)}
    except FileNotFoundError:
        return {"error": f"File not found: {path}"}
    return {"code": code, "warnings": warnings, "path": path}


@emits_nothing
def list_hooks(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """List all available hook strategies (pre-coded and saved)."""
    from microclaw.hooks import PRECODED_HOOK_REGISTRY
    from microclaw.hook_manager import describe_saved_hook, list_saved_hooks
    saved = list_saved_hooks()
    for name, entry in saved.items():
        described = describe_saved_hook(name)
        refusal = described.get("resolve_refusal")
        if refusal is None:
            refusal = {"would_refuse": True, "reasons": [described["error"]]}
        entry["resolvable"] = not refusal["would_refuse"]
        entry["resolve_refusal"] = refusal
    return {
        "precoded": list(PRECODED_HOOK_REGISTRY.keys()),
        "saved": saved,
        "hint": "Call describe_hook(name) to see constructor parameters and resolve-time compatibility.",
    }


@emits_nothing
def describe_hook(
    ctrl: MicroscopeController, guard: SafetyGuard, name: str
) -> dict:
    """Describe a hook without running saved source.

    Saved hooks are read and parsed as AST only. Unlike execution, description
    remains available for hash-mismatched and legacy unpinned files so an
    operator can inspect what changed; the returned provenance flags that state
    rather than importing or executing the source.
    """
    from microclaw.hooks import PRECODED_HOOK_REGISTRY
    from microclaw.hook_manager import describe_saved_hook, list_saved_hooks

    if name in PRECODED_HOOK_REGISTRY:
        hook_cls = PRECODED_HOOK_REGISTRY[name]
        signature = inspect.signature(hook_cls)
        parameters = []
        for parameter in signature.parameters.values():
            required = (
                parameter.default is inspect.Parameter.empty
                and parameter.kind not in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                )
            )
            item = {"name": parameter.name, "required": required}
            if parameter.default is not inspect.Parameter.empty:
                item["default"] = repr(parameter.default)
            parameters.append(item)
        parameter_names = {item["name"] for item in parameters}
        callback = (
            "analyze_frame" if hasattr(hook_cls, "analyze_frame")
            else "image_process_fn" if hasattr(hook_cls, "image_process_fn")
            else None
        )
        own_doc = hook_cls.__dict__.get("__doc__")
        return {
            "name": name,
            "kind": "precoded",
            "class_name": hook_cls.__name__,
            "class_docstring": inspect.cleandoc(own_doc) if own_doc else None,
            "constructor_parameters": parameters,
            "callback": callback,
            "resolve_refusal": {"would_refuse": False, "reasons": []},
            "parameter_handling": {
                "stripped": [],
                "injected": [
                    parameter for parameter in ("ctrl", "guard")
                    if parameter in parameter_names
                ],
            },
        }
    if name in list_saved_hooks():
        description = describe_saved_hook(name)
        description["adaptive_hardware_actions"] = (
            "run_adaptive_survey may apply MoveNamedStage or SetDeviceProperty "
            "to the event selected by the same HookResult, within acquisition-call "
            "envelopes; predetermined runs require hook_action_plan instead."
        )
        return description
    return {
        "error": f"Unknown hook strategy '{name}'. Run list_hooks() to see available strategies."
    }


@emits_nothing
def list_mm_plugins(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """List installed MM plugins by role so a human can review/gate them."""
    try:
        plugins = ctrl.plugins.list_plugins()
    except Exception as e:
        return {"error": f"Could not list MM plugins: {e}"}
    return {
        "plugins": plugins,
        "hint": (
            "Analyzer plugins run with hook_strategy='mm_plugin_analyzer' "
            "(allowed unless in plugins.blocked). Autofocus plugins run with "
            "hook_strategy='autofocus_mm_plugin'. Hardware-moving plugin hooks are "
            "permitted by default, but microclaw guards only the resulting position, "
            "not the plugin's motion itself. Always confirm the classpath with the "
            "user before enabling a plugin hook."
        ),
    }


@emits_nothing
def get_hook_documentation(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    from microclaw.hook_docs import HOOK_REFERENCE
    return {"documentation": HOOK_REFERENCE}


@emits_nothing
def get_smlm_documentation(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    from microclaw.smlm_docs import SMLM_REFERENCE
    return {"documentation": SMLM_REFERENCE}


@emits_nothing
def get_dna_paint_documentation(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """The DNA-PAINT protocol in full, behind get_smlm_documentation's summary.

    Two documents cover DNA-PAINT and they are not peers: SMLM_REFERENCE plans
    any SMLM session including this one, and this is the depth behind it —
    binding kinetics, buffer recipes, strand design, and the bench procedure
    from folding to reconstruction. Where a number appears in both, this one is
    the accurate one and SMLM_REFERENCE says so where it quotes it.
    """
    from microclaw.dna_paint_docs import load_reference
    return {"documentation": load_reference()}


@emits_nothing
def check_emu_installed(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    from microclaw.emu_manager import (
        find_mm_app_dir, find_plugin_jars, read_emu_config, _emu_config_path,
    )

    mm_dir = find_mm_app_dir(ctrl)
    if mm_dir is None:
        return {
            "emu_installed": False,
            "htsmlm_installed": False,
            "htsmlm_configured": False,
            "mm_app_dir": None,
            "note": (
                "Micro-Manager installation directory not found automatically. "
                "If MM is installed in a non-standard location, call "
                "get_emu_configuration(mm_app_dir='...') with the correct path."
            ),
        }

    jars = find_plugin_jars(mm_dir)
    config_exists = _emu_config_path(mm_dir).exists()
    plugin_name = ""
    if config_exists:
        try:
            plugin_name = read_emu_config(mm_dir).get("plugin_name", "")
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    htsmlm_configured = "".join(
        char for char in plugin_name.casefold() if char.isalnum()
    ) == "htsmlm"
    return {
        "emu_installed": bool(jars["EMU"]) or config_exists,
        "htsmlm_installed": bool(jars["htSMLM"]),
        "htsmlm_configured": htsmlm_configured,
        "mm_app_dir": str(mm_dir),
        "emu_jars": jars["EMU"],
        "htsmlm_jars": jars["htSMLM"],
        "emu_config_present": config_exists,
    }


@emits_nothing
def get_htsmlm_documentation(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    from microclaw.htsmlm_docs import HTSMLM_REFERENCE
    return {"documentation": HTSMLM_REFERENCE}


@emits_nothing
def save_knowledge(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    category: str,
    key: str,
    value: dict,
) -> dict:
    """Persist a knowledge base entry. Gated by an in-code confirmation."""
    import yaml
    from microclaw.knowledge_manager import (
        RIG_TOPICS,
        load_knowledge,
        rig_profile_gaps,
        save_entry,
    )
    # A devices/ entry can suppress an alarm (design/21 S4); it must name the
    # hardware it was observed on, or it detaches from its trigger and applies
    # to whatever camera is loaded next.
    if category == "devices" and "observed_on" not in value:
        return {"error":
                "A devices/ entry must carry observed_on: the camera adapter it "
                "was observed with (the 'adapter' field of get_system_state's "
                "camera block, e.g. 'DCam'). An entry that suppresses an alarm "
                "must name the condition it holds under."}
    # rig/ *is* the profile, so its keys are the profile's topics. A rig fact
    # filed under any other key closes no topic — the interview re-asks it every
    # session — and never reaches its point of use, because get_roi looks up
    # illuminated_field by name. Measured on the demo machine (design/43 F1,
    # block 43f): asked to store a deliberate crop, the agent invented
    # `saved_roi`, then recited illuminated_field as still open without
    # connecting the two. delete_knowledge is deliberately not restricted, so a
    # key saved before this refusal existed can still be removed.
    if category == "rig" and key not in RIG_TOPICS:
        return {"error":
                f"'{key}' is not a rig profile topic, and rig/ holds only those. "
                f"Save this under whichever topic it answers: "
                f"{', '.join(RIG_TOPICS)} — extra detail belongs inside that "
                "topic's value. A fact about one piece of hardware belongs in "
                "devices/, and a named imaging recipe in strategies/.",
                "rig_topics": list(RIG_TOPICS)}
    if not CONFIRM_FN(
        f"Save knowledge {category}/{key}:\n{yaml.safe_dump({key: value})}",
        kind="knowledge",
    ):
        return {"error": "User declined to save this knowledge entry."}
    try:
        save_entry(category, key, value)
    except ValueError as e:
        return {"error": str(e)}
    result = {
        "status": f"Saved '{key}' under '{category}'.",
        "category": category,
        "key": key,
        "value": value,
    }
    if category == "rig":
        result["remaining_rig_profile_topics"] = rig_profile_gaps(load_knowledge())
    return result


@emits_nothing
def get_knowledge(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    category: str | None = None,
) -> dict:
    """Return knowledge base entries for a category, or all categories if omitted."""
    from microclaw.knowledge_manager import load_knowledge
    data = load_knowledge()
    if category is not None:
        return {"category": category, "entries": data.get(category, {})}
    return {"knowledge": data}


@emits_nothing
def delete_knowledge(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    category: str,
    key: str,
) -> dict:
    """Remove a single entry from the knowledge base."""
    from microclaw.knowledge_manager import delete_entry
    found = delete_entry(category, key)
    if found:
        return {"status": f"Deleted '{key}' from '{category}'."}
    return {"error": f"No entry '{key}' in category '{category}'."}


# Parsed EMU properties for this session, so derived tools (laser map, focus
# lock, acquisition pre-flight) don't re-read the config file per call.
_EMU_SESSION_CACHE: dict[str, Any] = {}


def _read_emu_properties(ctrl: MicroscopeController, mm_app_dir: str) -> dict:
    """Read + parse the EMU config and cache the result for this session."""
    from microclaw.emu_manager import read_emu_config

    # Device labels are needed to split "DeviceLabel-PropertyLabel" strings —
    # labels contain hyphens, so the parse is ambiguous without them (§2a).
    try:
        device_labels = _str_vector(ctrl.core.get_loaded_devices())
    except Exception:
        device_labels = []
    config = read_emu_config(mm_app_dir, device_labels)
    _EMU_SESSION_CACHE["properties"] = config["properties"]
    _EMU_SESSION_CACHE["parameters"] = config["parameters"]
    _EMU_SESSION_CACHE["plugin_name"] = config.get("plugin_name", "")
    return config


def _cached_emu_properties(
    ctrl: MicroscopeController,
) -> tuple[dict | None, dict]:
    """Parsed EMU properties and parameters for this session."""
    if "properties" in _EMU_SESSION_CACHE:
        return (
            _EMU_SESSION_CACHE["properties"],
            _EMU_SESSION_CACHE.get("parameters", {}),
        )
    from microclaw.emu_manager import find_mm_app_dir

    try:
        mm_dir = find_mm_app_dir(ctrl)
        if mm_dir is None:
            _EMU_SESSION_CACHE["properties"] = None
            _EMU_SESSION_CACHE["parameters"] = {}
            return None, {}
        config = _read_emu_properties(ctrl, str(mm_dir))
        return config["properties"], config["parameters"]
    except Exception:
        _EMU_SESSION_CACHE["properties"] = None
        _EMU_SESSION_CACHE["parameters"] = {}
        return None, {}


@emits_nothing
def get_emu_configuration(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    mm_app_dir: str | None = None,
) -> dict:
    from microclaw.emu_manager import (
        build_emu_map,
        find_mm_app_dir,
        save_mm_app_dir,
        _candidate_mm_dirs,
    )

    if mm_app_dir is not None:
        save_mm_app_dir(mm_app_dir)
        resolved = mm_app_dir
    else:
        found = find_mm_app_dir(ctrl)
        if found is None:
            searched = [str(p) for p in _candidate_mm_dirs()]
            return {
                "error": (
                    "Cannot locate the EMU configuration file. "
                    "The Micro-Manager app directory was not found automatically."
                ),
                "action_required": (
                    "Call get_emu_configuration(mm_app_dir='/path/to/micro-manager') "
                    "with the path to your Micro-Manager installation directory. "
                    "The path will be saved for future calls."
                ),
                "searched_paths": searched,
            }
        resolved = str(found)

    config = _read_emu_properties(ctrl, resolved)
    # Structured, placeholder-free view (design/14 §2): same information as
    # the raw property dict at ~1/3 the tokens, shaped so a laser cannot be
    # mismatched to another slot's trigger line.
    emu_map = build_emu_map(config["properties"], config["parameters"])
    return {
        "config_name": config["config_name"],
        "plugin_name": config["plugin_name"],
        "mm_app_dir": resolved,
        **emu_map,
        "note": (
            "Lasers are keyed by EMU slot index; each slot pairs its own "
            "enable, power and trigger lines. Never infer a slot index from "
            "device naming order. Use resolve_emu_device(semantic_name) for "
            "properties under 'other'."
        ),
    }


def _read_qpd(ctrl: MicroscopeController, focus_lock: dict) -> dict | None:
    qpd = focus_lock.get("qpd")
    if not qpd:
        return None
    out = {}
    for axis, entry in qpd.items():
        if "device" in entry:
            try:
                out[axis] = ctrl.core.get_property(entry["device"], entry["property"])
            except Exception:
                pass
    return out or None


@emits_nothing
def _lock_status_properties(ctrl, device: str) -> dict[str, str]:
    """The lock device's read-only properties and what they read right now.

    get_focus_lock_state named the DEVICE and stopped there, which left the one
    thing run_autofocus's probe needs -- the property -- to be discovered by
    hand. Measured twice: on a Nikon Ti the model hand-stepped Z rather than
    look, and on a Nikon Ti2-E/Dragonfly it proposed an image sweep until the
    operator asked "why not do a PFS search?".

    Nothing here is rig-specific, and it cannot be: the two rigs disagree about
    every name. The Ti reports Status -> "Out of focus search range"; the
    Dragonfly reports "PFS in Range" -> "In Range" alongside a "PFS Status" that
    is a 16-bit string no one should probe. Showing the values is what makes the
    difference between them obvious without a single extra tool call.
    """
    try:
        names = _str_vector(ctrl.core.get_device_property_names(device))
    except Exception:
        return {}
    readable = {}
    for name in names:
        try:
            if not bool(ctrl.core.is_property_read_only(device, name)):
                continue
            readable[str(name)] = str(ctrl.core.get_property(device, name))
        except Exception:
            continue
    return readable


def get_focus_lock_state(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """Read the hardware focus lock via the EMU map ('Z stage focus locking').

    In amr_test the model answered its own 'Focus lock engaged?' checklist item
    with 'You confirmed focus looks fine' — a sharp image is not an engaged
    lock (design/14 §5). This is the one-call check it lacked.
    """
    from microclaw.emu_manager import build_emu_map

    props, params = _cached_emu_properties(ctrl)
    if not props:
        try:
            raw_device = ctrl.core.get_auto_focus_device()
            device = raw_device if isinstance(raw_device, str) else ""
        except Exception:
            device = ""
        try:
            raw_engaged = ctrl.core.is_continuous_focus_enabled()
            engaged = raw_engaged if isinstance(raw_engaged, bool) else None
        except Exception:
            engaged = None
        if device:
            status = _lock_status_properties(ctrl, device)
            return {
                "engaged": engaged,
                "property": f"continuous focus device {device}",
                "device": device,
                "status_properties": status,
                **({"probe_hint": (
                    f"To find this lock's capture range at zero exposures, call "
                    f"run_autofocus with probe device '{device}', one of the "
                    f"properties above, and the values that mean in-range. Read "
                    f"the values shown to pick the property; a bitfield or a "
                    f"number is not the one you want."
                )} if status else {}),
                **({} if engaged is not None else {
                    "reason": "The autofocus adapter does not report whether continuous focus is enabled."
                }),
            }
        return {"engaged": None, "reason": "No hardware autofocus device is configured."}
    lock = build_emu_map(props, params)["focus_lock"]
    if lock is None or "device" not in lock:
        return {"engaged": None, "reason": "No focus-lock property in the EMU map."}
    value = str(ctrl.core.get_property(lock["device"], lock["property"]))
    on_value = str(lock.get("on", "1"))
    return {
        "engaged": value == on_value,
        "raw_value": value,
        "property": f"{lock['device']}.{lock['property']}",
        "qpd": _read_qpd(ctrl, lock),
    }


@emits(_emit_focus_lock)
def set_focus_lock(
    ctrl: MicroscopeController, guard: SafetyGuard, enabled: bool
) -> dict:
    """Engage or disengage the hardware focus lock via the EMU map."""
    from microclaw.emu_manager import build_emu_map

    props, params = _cached_emu_properties(ctrl)
    if not props:
        try:
            raw_device = ctrl.core.get_auto_focus_device()
            device = raw_device if isinstance(raw_device, str) else ""
        except Exception:
            device = ""
        if not device:
            return {"error": "No hardware autofocus device is configured — cannot control a focus lock."}
        try:
            ctrl.core.enable_continuous_focus(bool(enabled))
        except Exception as exc:
            return {"error": f"Cannot {'engage' if enabled else 'disengage'} continuous focus on {device}: {exc}"}
        ctrl.refresh_gui()
        return {
            "engaged": bool(enabled),
            "property": f"continuous focus device {device}",
            # The emitter's discriminator: this route is MMCore's own switch,
            # not a device property write, and `property` above is prose.
            "continuous_focus_device": device,
            "value": bool(enabled),
        }
    lock = build_emu_map(props, params)["focus_lock"]
    if lock is None or "device" not in lock:
        return {"error": "No focus-lock property in the EMU map."}
    target = str(lock.get("on", "1")) if enabled else str(lock.get("off", "0"))
    from microclaw.authorization import authorize_property_write
    authorize_property_write(ctrl, lock["device"], lock["property"])
    guard.check_device_property(ctrl.core, lock["device"], lock["property"], target)
    ctrl.core.set_property(lock["device"], lock["property"], target)
    ctrl.refresh_gui()
    return {
        "engaged": enabled,
        "property": f"{lock['device']}.{lock['property']}",
        "value": target,
    }


@emits_nothing
def get_emu_laser_map(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """The slot → laser table (enable / power / trigger lines) from the EMU map."""
    from microclaw.emu_manager import build_emu_map

    props, params = _cached_emu_properties(ctrl)
    if not props:
        return {"error": _NO_EMU_CONFIG}
    lasers = build_emu_map(props, params)["lasers"]
    return {
        "lasers": lasers,
        "note": (
            "Slot index pairs each laser with ITS OWN trigger lines. "
            "Verify trigger_mode/trigger_sequence on the SAME slot you enable."
        ),
    }


@emits_nothing
def resolve_emu_device(
    ctrl: MicroscopeController, guard: SafetyGuard, semantic_name: str
) -> dict:
    """Resolve an EMU semantic name ('Laser 3 enable') to its MM device/property."""
    from microclaw.emu_manager import resolve_emu_device as _resolve

    props, params = _cached_emu_properties(ctrl)
    if not props:
        return {"error": _NO_EMU_CONFIG}
    try:
        return {
            "semantic_name": semantic_name,
            **_resolve(props, semantic_name, params),
        }
    except KeyError as e:
        return {"error": str(e).strip("'\"")}


def _emu_power_entry(ctrl: MicroscopeController, slot: int) -> dict:
    from microclaw.emu_manager import build_emu_map
    props, params = _cached_emu_properties(ctrl)
    if not props:
        raise ValueError(_NO_EMU_CONFIG)
    entry = build_emu_map(props, params)["lasers"].get(int(slot), {}).get("power_pct")
    if not entry or "device" not in entry:
        raise ValueError(f"EMU laser slot {slot} has no allocated percentage property.")
    try:
        slope = float(entry["slope"])
        offset = float(entry.get("offset", 0.0))
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"EMU laser slot {slot} has no valid rescaling calibration.") from e
    if slope <= 0:
        raise ValueError(f"EMU laser slot {slot} has invalid slope {slope!r}.")
    return {**entry, "slope": slope, "offset": offset}


def _round_raw(value: float) -> int:
    """Round a non-negative MM integer property half-up, not Python half-even."""
    return int(math.floor(value + 0.5))


def verify_emu_laser_power_calibration(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    slot: int,
    observations: list[dict],
) -> dict:
    """Verify two GUI-percent/raw observations against EMU's affine mapping."""
    entry = _emu_power_entry(ctrl, slot)
    if len(observations) < 2:
        return {"error": "Two known GUI percent/raw observations are required."}
    checked = []
    for point in observations:
        percent = float(point["percent"])
        raw = int(point["raw_value"])
        expected = _round_raw(entry["slope"] * percent + entry["offset"])
        checked.append({"percent": percent, "raw_value": raw, "expected_raw": expected})
    if len({p["percent"] for p in checked}) < 2:
        return {"error": "The observations must use two distinct GUI percentages."}
    if any(p["raw_value"] != p["expected_raw"] for p in checked):
        return {
            "verified": False,
            "error": "Observed GUI/raw values disagree with the EMU calibration; keep illumination disabled.",
            "observations": checked,
        }
    key = (entry["device"], entry["property"], entry["slope"], entry["offset"])
    _EMU_SESSION_CACHE.setdefault("verified_power_calibrations", {})[int(slot)] = key
    return {"verified": True, "slot": int(slot), "observations": checked,
            "formula": "raw = slope * percent + offset"}


@emits_nothing
def get_emu_laser_power_percentage(
    ctrl: MicroscopeController, guard: SafetyGuard, slot: int
) -> dict:
    entry = _emu_power_entry(ctrl, slot)
    raw_text = str(ctrl.core.get_property(entry["device"], entry["property"]))
    raw = float(raw_text)
    effective = (raw - entry["offset"]) / entry["slope"]
    verified = _EMU_SESSION_CACHE.get("verified_power_calibrations", {}).get(int(slot)) == (
        entry["device"], entry["property"], entry["slope"], entry["offset"]
    )
    return {
        "slot": int(slot),
        "commanded_state": {"raw_value": raw_text, "property": f"{entry['device']}.{entry['property']}"},
        "interpreted_state": {"effective_percent": effective, "calibration_verified": verified},
        "measured_state": None,
        "gui_state": None,
        "warning": None if verified else "Calibration is not verified against two known GUI settings.",
    }


def set_emu_laser_power_percentage(
    ctrl: MicroscopeController, guard: SafetyGuard, slot: int, percent: float
) -> dict:
    entry = _emu_power_entry(ctrl, slot)
    key = (entry["device"], entry["property"], entry["slope"], entry["offset"])
    if _EMU_SESSION_CACHE.get("verified_power_calibrations", {}).get(int(slot)) != key:
        return {"error": (
            "Calibration is unverified. Keep illumination disabled and call "
            "verify_emu_laser_power_calibration with two known GUI percent/raw observations first."
        )}
    requested = float(percent)
    if requested < 0:
        return {"error": "Laser power percentage cannot be negative."}
    raw = _round_raw(entry["slope"] * requested + entry["offset"])
    info = get_device_property_info(ctrl, guard, entry["device"], entry["property"])
    if info["read_only"]:
        return {"error": f"{entry['device']}.{entry['property']} is read-only."}
    if info["type"] == "Integer":
        raw_value = str(raw)
    else:
        raw_value = str(entry["slope"] * requested + entry["offset"])
    numeric = float(raw_value)
    if info["lower_limit"] is not None and numeric < float(info["lower_limit"]):
        return {"error": f"Converted raw value {raw_value} is below the property limit."}
    if info["upper_limit"] is not None and numeric > float(info["upper_limit"]):
        return {"error": f"Converted raw value {raw_value} exceeds the property limit."}
    effective = (numeric - entry["offset"]) / entry["slope"]
    min_nonzero = max(0.0, (1.0 - entry["offset"]) / entry["slope"])
    representable = math.isclose(effective, requested, rel_tol=0, abs_tol=1e-9)
    from microclaw.authorization import authorize_property_write
    authorize_property_write(ctrl, entry["device"], entry["property"])
    guard.check_device_property(ctrl.core, entry["device"], entry["property"], raw_value)
    guard.check_illumination(ctrl.core, entry["device"], entry["property"], raw_value,
                             confirm_fn=CONFIRM_FN)
    ctrl.core.set_property(entry["device"], entry["property"], raw_value)
    written = str(ctrl.core.get_property(entry["device"], entry["property"]))
    written_effective = (float(written) - entry["offset"]) / entry["slope"]
    ctrl.refresh_gui()
    return {
        "requested_percent": requested,
        "raw_value_written": written,
        "effective_percent": written_effective,
        "representable": representable and math.isclose(written_effective, requested,
                                                          rel_tol=0, abs_tol=1e-9),
        "min_nonzero_percent": min_nonzero,
        "commanded_state": {"raw_value": written},
        "interpreted_state": {"effective_percent": written_effective, "calibration_verified": True},
        "measured_state": None,
        "gui_state": None,
        "warning": None if representable else (
            "Requested percentage is not representable by this property; review the effective value before enabling illumination."
        ),
    }


def _datastore_state(store: Any) -> dict | None:
    if store is None:
        return None
    out = {}
    for name, getter in (("image_count", "get_num_images"), ("frozen", "is_frozen"),
                         ("name", "get_name"), ("save_path", "get_save_path")):
        try:
            out[name] = getattr(store, getter)()
        except Exception:
            out[name] = None
    return out


@emits_nothing
def get_album_state(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    store = ctrl.studio.album().get_datastore()
    return {"album_exists": store is not None, "datastore": _datastore_state(store)}


def snap_to_album(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    guard.check_exposure(float(ctrl.core.get_exposure()))
    with _pause_live(ctrl) as live_state:
        images = ctrl.studio.acquisitions().snap()
        created = bool(ctrl.studio.album().add_images(images))
    state = get_album_state(ctrl, guard)
    return {"status": "Snap added to the Micro-Manager Album.",
            "created_new_album": created,
            "live_view_restarted": live_state["was_on"],
            **({"live_view_restore": _live_restore_report(live_state)}
               if live_state["was_on"] else {}),
            **state}


_MDA_SCALARS = (
    "prefix", "root", "save", "should_display_images", "use_frames", "num_frames",
    "interval_ms", "use_position_list", "use_slices", "slice_z_bottom_um",
    "slice_z_top_um", "slice_z_step_um", "relative_z_slice", "use_channels",
    "channel_group", "use_autofocus", "skip_autofocus_count",
    "keep_shutter_open_channels", "keep_shutter_open_slices", "acq_order_mode",
    "camera_timeout", "comment",
)


def _read_mda_settings(settings: Any) -> dict:
    out = {}
    for name in _MDA_SCALARS:
        try:
            value = getattr(settings, name)()
            out[name] = value if value is None or isinstance(value, (bool, int, float, str)) else str(value)
        except Exception as e:
            out[name] = f"<unreadable: {type(e).__name__}>"
    # These are non-scalar but safety-critical: channel exposure changes and
    # exact slice-list changes must invalidate the preview token.
    #
    # slices()/channels() return a java.util.ArrayList, which is NOT directly
    # Python-iterable over the bridge (`for v in list` raises TypeError) — read
    # it by size()/get(i), the same pattern as _str_vector. And on ChannelSpec
    # the two accessors differ: `useChannel` is a public field (camelCase,
    # CLAUDE.md) but `exposure` is a METHOD — `spec.exposure` is a bound method
    # and `float(spec.exposure)` throws; `spec.exposure()` returns the value.
    # Both facts measured on M5 (design/32 Block 4 G5 introspection probe).
    if out.get("use_slices") is True:
        try:
            sl = settings.slices()
            out["slices"] = [float(sl.get(i)) for i in range(int(sl.size()))]
        except Exception as e:
            out["slices"] = f"<unreadable: {type(e).__name__}>"
    if out.get("use_channels") is True:
        try:
            raw = settings.channels()
            out["channels"] = [
                {
                    "use_channel": bool(raw.get(i).useChannel),
                    "exposure_ms": float(raw.get(i).exposure()),
                }
                for i in range(int(raw.size()))
            ]
        except Exception as e:
            out["channels"] = f"<unreadable: {type(e).__name__}>"
    return out


@emits_nothing
def get_mda_settings(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    manager = ctrl.studio.acquisitions()
    settings = manager.get_acquisition_settings()
    values = _read_mda_settings(settings)
    fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    _EMU_SESSION_CACHE["mda_preview"] = (fingerprint, settings)
    enabled = [axis for flag, axis in (("use_frames", "time"), ("use_position_list", "positions"),
                                        ("use_slices", "z"), ("use_channels", "channels"),
                                        ("use_autofocus", "autofocus")) if values.get(flag) is True]
    return {"source": "MMStudio GUI current MDA", "settings": values,
            "enabled_axes": enabled, "preview_token": fingerprint,
            "warning": "Inspect illumination, motion, saving, and all enabled axes before running."}


@_acquisition_entry_point
def run_mda(ctrl: MicroscopeController, guard: SafetyGuard, preview_token: str) -> dict:
    from microclaw.authorization import authorize_path
    authorize_path(ctrl, "mmstudio-mda")
    preview = _EMU_SESSION_CACHE.get("mda_preview")
    if not preview or preview[0] != preview_token:
        return {"error": "Missing or stale MDA preview. Call get_mda_settings immediately before run_mda."}
    manager = ctrl.studio.acquisitions()
    current = _read_mda_settings(manager.get_acquisition_settings())
    current_token = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
    if current_token != preview_token:
        return {"error": "MMStudio MDA settings changed after preview; inspect them again."}
    if current.get("save") is True and current.get("root"):
        guard.resolve_in_workspace(str(current["root"]))
    exposure_ms = float(ctrl.core.get_exposure())
    guard.check_exposure(exposure_ms)
    try:
        time_points = int(current["num_frames"]) if current.get("use_frames") else 1
        if time_points <= 0:
            raise ValueError("num_frames must be positive")
        if current.get("use_slices"):
            slice_values = current.get("slices")
            if not isinstance(slice_values, list) or not slice_values:
                raise ValueError("enabled slice list is unreadable or empty")
            slices = len(slice_values)
        else:
            slices = 1
        if current.get("use_position_list"):
            position_list = ctrl.studio.positions().get_position_list()
            positions = int(position_list.get_number_of_positions())
            if positions <= 0:
                raise ValueError("enabled position list is empty")
        else:
            positions = 1
        if current.get("use_channels"):
            channel_values = current.get("channels")
            if not isinstance(channel_values, list):
                raise ValueError("enabled channel list is unreadable")
            enabled_channels = [
                c for c in channel_values
                if isinstance(c, dict) and c.get("use_channel") is True
            ]
            if not enabled_channels:
                raise ValueError("enabled channel list is empty")
            channels = len(enabled_channels)
            exposure_ms = max(float(c["exposure_ms"]) for c in enabled_channels)
            guard.check_exposure(exposure_ms)
        else:
            channels = 1
        frames = time_points * slices * positions * channels
        width = int(ctrl.core.get_image_width())
        height = int(ctrl.core.get_image_height())
        bpp = int(ctrl.core.get_bytes_per_pixel())
        if min(width, height, bpp) <= 0:
            raise ValueError("camera geometry is unavailable")
        interval_s = float(current.get("interval_ms") or 0) / 1000.0
        plan = AcquisitionPlan(
            frames=frames,
            exposure_ms_per_frame=exposure_ms,
            estimated_duration_s=max(
                frames * exposure_ms / 1000.0,
                max(0, time_points - 1) * interval_s + exposure_ms / 1000.0,
            ),
            estimated_bytes=frames * width * height * bpp,
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise SafetyViolation(f"MMStudio MDA is unplannable: {exc}") from exc
    reservation = _authorize_acquisition(ctrl, guard, plan, confirm=False)
    summary = (
        json.dumps(current, indent=2, sort_keys=True)
        + "\n\nPlan: "
        + f"{plan.frames} frames, {plan.exposure_ms_per_frame:g} ms/frame, "
        + f"{plan.estimated_duration_s:g} s estimated, "
        + f"{plan.estimated_bytes} bytes estimated."
    )
    if not CONFIRM_FN("RUN MMSTUDIO CURRENT MDA:\n" + summary, kind="acquisition"):
        reservation.close()
        return {"error": "User declined to run the current MMStudio MDA."}
    try:
        # MMStudio owns this opaque call and it runs to completion. Like the
        # list-backed runners, it cannot be cancelled mid-run; this is not new.
        store = manager.run_acquisition()
        for _ in range(plan.frames):
            reservation.commit_frame()
    finally:
        reservation.close()
    _EMU_SESSION_CACHE.pop("mda_preview", None)
    return {
        "status": "MMStudio MDA complete.",
        "source": "MMStudio GUI current MDA",
        "resolved_settings": current,
        "datastore": _datastore_state(store),
        **_reservation_report(reservation),
    }


# --- Tool Registry ---

# snap_image was removed (design/14 §7): it differed from snap_and_analyze only
# by an invisible display side-effect, a trap the model fell into. The display
# now lives in snap_and_analyze itself.
TOOL_REGISTRY = {
    "snap_and_analyze": snap_and_analyze,
    "start_live_view": start_live_view,
    "stop_live_view": stop_live_view,
    "set_exposure": set_exposure,
    "get_exposure": get_exposure,
    "get_pixel_size": get_pixel_size,
    "get_roi": get_roi,
    "set_roi": set_roi,
    "clear_roi": clear_roi,
    "get_xy_position": get_xy_position,
    "move_stage_xy": move_stage_xy,
    "get_z_position": get_z_position,
    "move_stage_z": move_stage_z,
    "list_stages": list_stages,
    "get_stage_position": get_stage_position,
    "move_named_stage": move_named_stage,
    "set_channel": set_channel,
    "get_available_channels": get_available_channels,
    "list_config_groups": list_config_groups,
    "set_config_preset": set_config_preset,
    "set_device_property": set_device_property,
    "get_device_property": get_device_property,
    "list_devices": list_devices,
    "list_device_properties": list_device_properties,
    "get_device_property_info": get_device_property_info,
    "get_full_device_state": get_full_device_state,
    "get_system_state": get_system_state,
    "shutter_declared_illumination": shutter_declared_illumination,
    "calibrate_stage_to_camera": calibrate_stage_to_camera,
    "find_features": find_features,
    "center_feature": center_feature,
    "export_dataset_as_tiff": export_dataset_as_tiff,
    "build_stage_coordinate_mosaic": build_stage_coordinate_mosaic,
    "run_analysis_on_saved_dataset": run_analysis_on_saved_dataset,
    "run_autofocus": run_autofocus,
    "mark_position": mark_position,
    "get_position_list": get_position_list,
    "go_to_position": go_to_position,
    "delete_position": delete_position,
    "clear_position_list": clear_position_list,
    "save_position_list": save_position_list,
    "load_position_list": load_position_list,
    "import_mm_positions": import_mm_positions,
    "run_multiposition_acquisition": run_multiposition_acquisition,
    "run_tile_acquisition": run_tile_acquisition,
    "run_multiposition_with_autofocus": run_multiposition_with_autofocus,
    "run_zstack": run_zstack,
    "run_timelapse": run_timelapse,
    "run_adaptive_survey": run_adaptive_survey,
    "open_artifact": open_artifact,
    "read_hook_log": read_hook_log,
    "rank_hook_log": rank_hook_log,
    "validate_positions": validate_positions,
    "inspect_artifacts": inspect_artifacts,
    "compare_revisit_frames": compare_revisit_frames,
    "calibrate_snr_threshold": calibrate_snr_threshold,
    "generate_and_save_hook": generate_and_save_hook,
    "read_hook_from_file": read_hook_from_file,
    "list_hooks": list_hooks,
    "describe_hook": describe_hook,
    "list_mm_plugins": list_mm_plugins,
    "get_hook_documentation": get_hook_documentation,
    "get_smlm_documentation": get_smlm_documentation,
    "get_dna_paint_documentation": get_dna_paint_documentation,
    "check_emu_installed": check_emu_installed,
    "get_htsmlm_documentation": get_htsmlm_documentation,
    "get_emu_configuration": get_emu_configuration,
    "get_emu_laser_map": get_emu_laser_map,
    "verify_emu_laser_power_calibration": verify_emu_laser_power_calibration,
    "get_emu_laser_power_percentage": get_emu_laser_power_percentage,
    "set_emu_laser_power_percentage": set_emu_laser_power_percentage,
    "resolve_emu_device": resolve_emu_device,
    "get_focus_lock_state": get_focus_lock_state,
    "set_focus_lock": set_focus_lock,
    "get_album_state": get_album_state,
    "snap_to_album": snap_to_album,
    "get_mda_settings": get_mda_settings,
    "run_mda": run_mda,
    "export_session_script": export_session_script,
    "get_current_datetime": get_current_datetime,
    "write_text_file": write_text_file,
    "save_knowledge": save_knowledge,
    "get_knowledge": get_knowledge,
    "delete_knowledge": delete_knowledge,
}


def execute_tool(
    name: str,
    tool_input: dict,
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    registry=TOOL_REGISTRY,
    *,
    setup_mode: bool = False,
    cancel=None,
    records=None,
) -> str | list:
    """Execute a tool and return content for the tool_result block.

    Returns a list of content blocks for image-returning tools, or a JSON string
    for all other tools. Never raises — errors are captured and returned.
    """
    if name not in registry:
        if setup_mode:
            return json.dumps({
                "error": f"Tool '{name}' is unavailable in setup mode; hardware control is locked."
            })
        return json.dumps({"error": f"Unknown tool '{name}'."})
    fn = registry.get(name)
    if fn is None:
        # A registry whose advertised key has no callable is malformed. Keep
        # this model-visible and non-throwing like every other dispatch error.
        return json.dumps({"error": f"Tool '{name}' has no implementation."})
    try:
        if (
            name != "run_mda"
            and getattr(fn, "_microclaw_acquisition_entry_point", False)
        ):
            from microclaw.authorization import authorize_path
            authorize_path(ctrl, f"acquisition-tool:{name}")
        if name == "export_session_script":
            result = fn(ctrl, guard, records=records, **tool_input)
        elif name == "set_channel":
            result = fn(ctrl, guard, cancel=cancel, **tool_input)
        else:
            result = fn(ctrl, guard, **tool_input)
        return result if isinstance(result, list) else json.dumps(result)
    except SafetyViolation as e:
        return json.dumps({"error": f"Safety constraint prevented this action: {e}"})
    except Exception as e:
        # Translate rather than forward: a Java stack trace teaches the model
        # nothing (design/14 §7). Known errors get an actionable one-liner, and
        # the hint names the subsystem that actually failed — a blanket "may be
        # a hardware error" on a FileNotFoundError sends the model to the stage.
        return json.dumps({
            "error": f"{type(e).__name__}: {humanize_java_error(e)}",
            "hint": hint_for_error(e),
        })
