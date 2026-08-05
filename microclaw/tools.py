from __future__ import annotations
import inspect
import hashlib
import itertools
import json
import logging
import math
import queue
import os
import tempfile
import threading
import time
import weakref
from datetime import datetime, timezone
from contextlib import contextmanager, ExitStack
from pathlib import Path
from typing import Any, Callable

import numpy as np
import tifffile
from pycromanager import Acquisition, multi_d_acquisition_events
from ndstorage import Dataset

from microclaw.autofocus import (
    AutofocusResult,
    coarse_then_fine_autofocus,
    curve_contrast,
    single_sweep_autofocus,
)
from microclaw.controller import (
    MicroscopeController,
    PositionListConflict,
    PositionProjection,
)
from microclaw.errors import hint_for_error, humanize_java_error
from microclaw.image_analysis import (
    ImageStats,
    compute_stats,
    detect_features,
    focus_invalid_warning,
    make_thumbnail,
    snap_to_numpy,
    preview_window_open,
    resolve_min_snr,
    snap_to_numpy_displayed,
    tenengrad,
)
from microclaw.safety import SafetyGuard, SafetyViolation
from microclaw.acquisition import AcquisitionLedger, AcquisitionPlan, Reservation, plan_events
from microclaw.calibration import resolve_calibration
from microclaw.dataset_mosaic import MosaicFrameShape, MosaicGeometry, assemble_stage_coordinate_mosaic

logger = logging.getLogger(__name__)


def _require_confirmation(summary: str, kind: str = "action") -> bool:
    """Blocking stdin confirmation for actions that persist model-writable content.

    Prints the exact thing about to be persisted and requires an explicit yes.
    `kind` ("knowledge", "hook", "illumination") is for frontends that render
    kinds differently; a terminal already reads the summary, so it is unused
    here.
    """
    print(f"\n[microclaw] Confirmation required:\n{summary}")
    return input("Proceed? [y/N] ").strip().lower() in {"y", "yes"}


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


class _HookedAcquisitionFailure(RuntimeError):
    """A hook failed after pycro-manager resolved the dataset directory."""

    def __init__(self, error: Exception, dataset_path: str) -> None:
        super().__init__(str(error))
        self.dataset_path = dataset_path


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


def _authorize_acquisition(
    ctrl, guard: SafetyGuard, plan: AcquisitionPlan, *, confirm: bool = True
) -> Reservation:
    reservation = _acquisition_ledger(ctrl).reserve(guard, plan)
    c = guard.acquisition_confirmation_thresholds
    exceeded = [
        label for label, value, threshold in (
            ("frames", plan.frames, c.confirm_above_frames),
            ("duration", plan.estimated_duration_s, c.confirm_above_duration_s),
            ("illuminated time", plan.illuminated_ms, c.confirm_above_illuminated_ms),
        ) if threshold is not None and value > threshold
    ]
    if confirm and exceeded and not CONFIRM_FN(
        "ACQUISITION PLAN\n"
        f"frames={plan.frames}, exposure_ms/frame={plan.exposure_ms_per_frame:g}, "
        f"duration_s≈{plan.estimated_duration_s:g}, bytes≈{plan.estimated_bytes}, "
        f"illuminated_ms={plan.illuminated_ms:g}\n"
        f"Confirmation thresholds exceeded: {', '.join(exceeded)}.",
        kind="acquisition",
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
def _pause_live(ctrl: MicroscopeController):
    """Stop live mode for the duration of a camera op, then restore it.

    core.snap_image() throws "sequence acquisition is running" if live mode is
    on, and studio.live().snap(True) is worse — it never returns and wedges the
    single-lock ZMQ bridge (design/14 V1). Every snap path must run inside
    this. Yields a mutable observation record. Restore success is checked
    against CMMCore's actual camera sequence, not MM Studio's live-mode flag.
    """
    live = ctrl.studio.live()
    was_on = bool(live.is_live_mode_on())
    if was_on:
        live.set_live_mode_on(False)
    state: dict[str, Any] = {"was_on": was_on, "restore_observed": None}
    try:
        yield state
    finally:
        if was_on:
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


def stop_live_view(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    ctrl.studio.live().set_live_mode_on(False)
    return {"status": "Live view stopped."}


def set_exposure(ctrl: MicroscopeController, guard: SafetyGuard, ms: float) -> dict:
    guard.check_exposure(ms)
    ctrl.core.set_exposure(ms)
    return {"status": f"Exposure set to {ms} ms."}


def get_exposure(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    ms = ctrl.core.get_exposure()
    return {"exposure_ms": ms}


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


def get_roi(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    roi = ctrl.core.get_roi()
    return {
        "x": int(roi.x),
        "y": int(roi.y),
        "width": int(roi.width),
        "height": int(roi.height),
    }


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
    ctrl.core.set_roi(x, y, width, height)
    _wait(ctrl, ctrl.core.get_camera_device())
    live_restarted = _bounce_live_if_on(ctrl)
    result: dict = {"status": "ROI set.", "x": x, "y": y, "width": width, "height": height}
    if live_restarted:
        result["live_view"] = "restarted"
    return result


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
    return result


# --- XY Stage ---

def get_xy_position(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    x = ctrl.core.get_x_position()
    y = ctrl.core.get_y_position()
    return {"x_um": round(x, 3), "y_um": round(y, 3)}


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

def get_z_position(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    z = ctrl.core.get_position()
    return {"z_um": round(z, 3)}


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

    if absolute:
        ctrl.core.set_position(target_z)
    else:
        ctrl.core.set_relative_position(z_um)

    _wait(ctrl, ctrl.core.get_focus_device())
    return {"z_um": round(target_z, 3), "status": "Moved."}


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


def get_stage_position(
    ctrl: MicroscopeController, guard: SafetyGuard, device: str
) -> dict:
    return {
        "device": device,
        "position_um": round(float(ctrl.core.get_position(device)), 4),
    }


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
    ctrl.core.set_position(device, target)
    ctrl.core.wait_for_device(device)
    achieved = float(ctrl.core.get_position(device))
    return {
        "device": device,
        "requested_um": round(target, 4),
        "achieved_um": round(achieved, 4),
        "error_um": round(achieved - target, 4),
    }


# --- Channel / Config ---

def _has_channel_authorization_map(ctrl: MicroscopeController) -> bool:
    """Whether this session can use the capture/authorize/replay executor.

    A legacy session with no property_authorization has no authorization map. It keeps MM
    delegation for compatibility, including MM's preset-definition re-read.
    """
    return getattr(ctrl, "authorization_map", None) is not None

def set_channel(
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
    return {"status": f"Channel set to '{preset}'."}


def get_available_channels(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    from microclaw.authorization import CHANNEL_CONFIG_GROUP
    channels = _str_vector(ctrl.core.get_available_configs(CHANNEL_CONFIG_GROUP))
    return {"channels": channels}


# --- Device Properties ---

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
    ctrl.studio.app().refresh_gui()
    return {"status": f"Set {device}.{property} = {value!r}."}


def get_device_property(
    ctrl: MicroscopeController, guard: SafetyGuard, device: str, property: str
) -> dict:
    value = ctrl.core.get_property(device, property)
    return {"device": device, "property": property, "value": value}


def list_devices(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    devices = _str_vector(ctrl.core.get_loaded_devices())
    return {"devices": devices}


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
    "unknown — no EMU laser map on this rig, so microclaw cannot read laser state"
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

    props = _cached_emu_properties(ctrl)
    if not props:
        return _NO_LASER_MAP
    try:
        lasers = build_emu_map(props)["lasers"]
    except Exception:
        return "unknown"
    if not lasers:
        return _NO_LASER_MAP

    out: dict[int, Any] = {}
    for slot, laser in sorted(lasers.items()):
        readings: dict[str, Any] = {}
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


def get_system_state(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    state: dict[str, Any] = {}
    try:
        state["x_um"] = round(ctrl.core.get_x_position(), 3)
        state["y_um"] = round(ctrl.core.get_y_position(), 3)
    except Exception:
        state["xy_stage"] = "unavailable"
    try:
        state["z_um"] = round(ctrl.core.get_position(), 3)
    except Exception:
        state["z_stage"] = "unavailable"
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
        if hasattr(hook, "post_hardware_hook_fn"):
            hook_fn_kwargs["post_hardware_hook_fn"] = hook.post_hardware_hook_fn
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
    except Exception as exc:
        if hook is not None and dataset_path is not None:
            raise _HookedAcquisitionFailure(exc, dataset_path) from exc
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
    _reservation: Reservation | None = None,
) -> dict:
    # Before set_exposure and before the sweep: an out-of-workspace save_dir
    # must not cost an acquisition to discover.
    save_dir = guard.resolve_in_workspace(save_dir)
    guard.check_z(z_start_um)
    guard.check_z(z_end_um)
    if channel:
        guard.check_channel(channel)
    if exposure_ms is not None:
        guard.check_exposure(exposure_ms)
    if not channel and exposure_ms is not None:
        ctrl.core.set_exposure(exposure_ms)

    events = _build_acquisition_events(
        channel=channel, exposure_ms=exposure_ms,
        z_start=z_start_um, z_end=z_end_um, z_step=z_step_um,
    )
    reservation = _reservation or _authorize_acquisition(
        ctrl, guard, plan_events(ctrl, events, exposure_ms)
    )
    dataset_path = _acquire_with_hooks(
        guard, save_dir, name, events, reservation=reservation,
        close_reservation=_reservation is None,
    )
    return {
        "status": "Z-stack complete.", "dataset_path": dataset_path,
        **_reservation_report(reservation),
    }


def _assert_excitation_will_fire(ctrl: MicroscopeController, laser_slot: int) -> dict:
    """Verify only that an EMU slot's trigger line is armed.

    This checks trigger mode and trigger sequence when the map declares them.
    It does not verify any other part of the emission path.
    """
    from microclaw.emu_manager import build_emu_map

    props = _cached_emu_properties(ctrl)
    if not props:
        return {
            "guarantee": "no trigger-line verification available",
            "checked": [],
            "not_verified": ["device-level enables", "illumination properties", "emission path"],
        }
    lasers = build_emu_map(props)["lasers"]
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
    _reservation: Reservation | None = None,
) -> dict:
    save_dir = guard.resolve_in_workspace(save_dir)   # before any hardware moves
    excitation_preflight = None
    if laser_slot is not None:
        excitation_preflight = _assert_excitation_will_fire(ctrl, laser_slot)
    if channel:
        guard.check_channel(channel)
    if exposure_ms is not None:
        guard.check_exposure(exposure_ms)
    # Without a channel, the acquisition events carry no exposure, so set it on
    # the core directly (mirrors run_zstack). This is the SMLM path —
    # run_timelapse(interval_s=0) with no channel — where exposure must still apply.
    if not channel and exposure_ms is not None:
        ctrl.core.set_exposure(exposure_ms)

    events = _build_acquisition_events(
        channel=channel, exposure_ms=exposure_ms,
        num_time_points=n_frames, time_interval_s=interval_s,
    )
    reservation = _reservation or _authorize_acquisition(
        ctrl, guard, plan_events(ctrl, events, exposure_ms)
    )
    dataset_path = _acquire_with_hooks(
        guard, save_dir, name, events, reservation=reservation,
        close_reservation=_reservation is None,
    )
    result = {
        "status": "Timelapse complete.", "dataset_path": dataset_path,
        **_reservation_report(reservation),
    }
    if excitation_preflight is not None:
        result["excitation_preflight"] = excitation_preflight
    illumination = guard.declared_illumination_state(ctrl.core)
    if illumination:
        result["declared_illumination_properties"] = illumination
    return result


def export_dataset_as_tiff(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    dataset_path: str,
    output_path: str,
) -> dict:
    dataset_path = guard.resolve_readable_path(dataset_path)
    output_path = guard.resolve_in_workspace(output_path)
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


def build_stage_coordinate_mosaic(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    dataset_path: str,
    output_path: str,
    axis_selection: dict,
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
    if missing:
        raise ValueError(f"axis_selection must fix every non-position axis: {sorted(missing)}")
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

def _metric_stamp(ctrl: MicroscopeController) -> dict:
    """The settings a focus metric is only comparable within (design/14 §10).

    Split from _focus_metric_payload so a multi-tile result can carry one stamp
    over many metrics: every tile of a grid shares the ROI, exposure and binning,
    so repeating the block per tile would be N copies of one fact.
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
        "min_snr": min_snr,
        "min_snr_source": min_snr_source,
        **_metric_stamp(ctrl),
    }
    if not stats.focus_metric_valid:
        payload["warning"] = focus_invalid_warning(stats.snr, min_snr)
    return payload


def snap_and_analyze(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    return_thumbnail: bool = False,
    thumbnail_size: int = 512,
    display: bool = True,
) -> list | dict:
    """Snap an image, display it in the MM viewer, and return numerical stats.

    display=True (default) snaps through studio.live().snap(True) so the
    biologist sees the same exposure the stats describe — the old core-only
    path silently never reached the viewer, and the agent told the user
    otherwise (design/14 §7). display=False keeps the snap headless.
    Live view is paused around the snap either way (V1: never probe by calling).
    """
    with _pause_live(ctrl) as live_state:
        image = snap_to_numpy_displayed(ctrl) if display else snap_to_numpy(ctrl)
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
        **_focus_metric_payload(ctrl, stats, min_snr, min_snr_source),
        "mean_intensity": round(stats.mean_intensity, 1),
        "min_intensity": round(stats.min_intensity, 1),
        "max_intensity": round(stats.max_intensity, 1),
        "saturated_fraction": round(stats.saturated_fraction, 4),
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
    return [
        {"type": "text", "text": json.dumps(text_payload)},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": make_thumbnail(image, max_size=thumbnail_size),
            },
        },
    ]


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

    with _pause_live(ctrl):
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

    # phase_cross_correlation returns (row, col) = (dy_px, dx_px).
    shift_x, _, _ = phase_cross_correlation(ref, img_x, upsample_factor=10)
    shift_y, _, _ = phase_cross_correlation(ref, img_y, upsample_factor=10)

    for axis, shift in (("X", shift_x), ("Y", shift_y)):
        why = _diagnose_calibration_shift(shift, frame_hw, step_um, px_hint)
        if why is not None:
            return {"error": f"Calibration failed on the {axis} move: {why}",
                    "step_um": step_um, "frame_px": list(frame_hw)}

    try:
        affine = solve_affine(
            (float(shift_x[0]), float(shift_x[1])),
            (float(shift_y[0]), float(shift_y[1])),
            step_um,
            objective=_current_objective(ctrl),
            binning=_current_binning(ctrl),
        )
    except ValueError as e:
        return {"error": f"Calibration failed: {e}"}

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
            )
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
    with _pause_live(ctrl):
        image = snap_to_numpy(ctrl)
    out = detect_features(image, min_sigma, max_sigma, threshold_rel)

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

def _run_autofocus_passes(
    ctrl: MicroscopeController,
    z_range_um: float,
    z_step_um: float,
    method: str,
    settle_ms: int,
) -> AutofocusResult:
    if method == "coarse_then_fine":
        return coarse_then_fine_autofocus(
            ctrl, z_range_um, max(z_step_um * 5, 1.0), z_step_um, settle_ms,
        )
    return single_sweep_autofocus(ctrl, z_range_um, z_step_um, settle_ms)


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


def _sweep_payload(sweep) -> dict | None:
    if sweep is None:
        return None
    return {
        "z_positions": [round(z, 3) for z in sweep.z_positions],
        "metric_curve": [_round_sig(v) for v in sweep.metric_values],
        "best_z_um": round(sweep.best_z_um, 3),
        "peak_interior": sweep.peak_interior,
        "contrast": round(curve_contrast(sweep.metric_values), 3),
    }


def run_autofocus(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    z_range_um: float,
    z_step_um: float,
    method: str = "coarse_then_fine",
    settle_ms: int = 50,
    return_thumbnail: bool = True,
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

    The sweep is headless: live view is paused for its duration and restored
    afterwards, and the viewer does not show the sweep as it happens.
    """
    entry_z = ctrl.core.get_position()
    guard.check_z(entry_z - z_range_um / 2)
    guard.check_z(entry_z + z_range_um / 2)

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

    with _pause_live(ctrl):
        result = _run_autofocus_passes(ctrl, z_range_um, z_step_um, method, settle_ms)

    payload: dict[str, Any] = {
        "converged": result.converged,
        "moved": result.moved,
        "reason": result.reason,
        "entry_z_um": round(result.entry_z_um, 3),
        "final_z_um": round(result.final_z_um, 3),
        "z_range_um": z_range_um,
        # BOTH passes — the caller can see which one chose the plane.
        "coarse": _sweep_payload(result.coarse),
        "fine": _sweep_payload(result.fine),
        "warning": (
            "Peak focus was at the edge of the sweep range; consider widening z_range_um."
            if result.converged and not result.coarse.peak_interior
            else None
        ),
    }

    # Invariant that would have surfaced the amr_test bug immediately: the
    # first pass must span the requested window around the entry Z.
    zs = result.coarse.z_positions
    assert min(zs) - 1e-6 <= result.entry_z_um <= max(zs) + 1e-6, (
        "autofocus sweep window does not contain the entry Z"
    )

    if not return_thumbnail:
        return payload

    with _pause_live(ctrl):
        image = snap_to_numpy(ctrl)
    payload["focus_metric_at_final"] = _round_sig(tenengrad(image))
    return [
        {"type": "text", "text": json.dumps(payload)},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": make_thumbnail(image),
            },
        },
    ]


# --- Position management ---

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
        return None, _position_conflict(projection)
    ctrl.set_position_projection(projection)
    return projection, None

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
        with _pause_live(ctrl):                     # snap(True) wedges under live (V1)
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
            "min_snr": min_snr,
            "min_snr_source": min_snr_source,
            "background_level": stats.background_level,
            "mean_intensity": round(stats.mean_intensity, 1),
            "min_intensity": round(stats.min_intensity, 1),
            "max_intensity": round(stats.max_intensity, 1),
            "saturated_fraction": round(stats.saturated_fraction, 4),
        }
        if not stats.focus_metric_valid:
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
    hook_strategy: str | None = None,
    hook_params: dict | None = None,
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
            # The grid coordinates are known up front, so marking needs no stage
            # reads and no visit loop — mark before the Acquisition takes over.
            for pos_label, x_um, y_um, z_um in resolved:
                ctrl.add_position(pos_label, float(x_um), float(y_um),
                                  float(z_um) if z_um is not None else None)
        with _pause_live(ctrl) as live_state:
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
        if "error" in hooked:
            return hooked
        # The coordinates are known exactly, right here — the hooked branch used
        # to drop them, so "where was tile r2_c1?" had no answer short of
        # re-imaging the grid (design/23 Episode A). The non-hooked branch has
        # attached them since design/19 F3; this is the same fix on the path every
        # survey actually takes. read_hook_log joins to this on `position`.
        restore = _live_restore_report(live_state)
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
    with _pause_live(ctrl) as live_state:
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
    """Visit each position, autofocus, then run a per-position protocol.

    """
    if position_names is not None and positions is not None:
        return {"error": "Provide position_names or positions, not both."}
    if position_names is None and positions is None:
        return {"error": "Provide either position_names or positions."}
    missing = [name for name, value in (
        ("z_range_um", z_range_um), ("z_step_um", z_step_um),
        ("protocol", protocol), ("save_dir", save_dir),
    ) if value is None]
    if missing:
        return {"error": f"Missing required arguments: {missing}."}
    save_dir = guard.resolve_in_workspace(save_dir)   # before the stage moves
    params = protocol_params or {}
    if position_names is not None:
        projection, conflict = _preflight_native_positions(
            ctrl, guard, preserve_unsupported=preserve_unsupported
        )
        if conflict:
            return conflict
        all_positions = {p["name"]: p for p in projection.positions}
        requested = [(name, all_positions.get(name), True) for name in position_names]
    else:
        requested = [(str(p["name"]), p, False) for p in positions]
    results = []
    valid_count = sum(1 for _name, pos, _stored in requested if pos is not None)
    reservation = (
        _authorize_acquisition(
            ctrl, guard,
            _plan_protocol_repetitions(ctrl, protocol, params, valid_count),
        )
        if protocol != "snap" and valid_count else None
    )

    with ExitStack() as cleanup:
        live_state = cleanup.enter_context(_pause_live(ctrl))
        if reservation is not None:
            cleanup.callback(reservation.close)
        for pos_name, pos, stored in requested:
            if pos is None:
                results.append({"position": pos_name, "error": "Not found in position list."})
                continue
            try:
                guard.check_xy(pos["x_um"], pos["y_um"])
                if "z_um" in pos:
                    guard.check_z(pos["z_um"])
            except SafetyViolation as e:
                results.append(
                    {"position": pos_name, "error": f"Stored position out of bounds: {e}"}
                )
                continue
            if stored:
                ctrl.go_to_position(pos_name)
            else:
                ctrl.set_xy(float(pos["x_um"]), float(pos["y_um"]))
                if "z_um" in pos:
                    ctrl.set_z(float(pos["z_um"]))

            current_z = ctrl.core.get_position()
            try:
                guard.check_z(current_z - z_range_um / 2)
                guard.check_z(current_z + z_range_um / 2)
            except Exception as e:
                results.append(
                    {"position": pos_name, "error": f"Autofocus range out of bounds: {e}"}
                )
                continue

            af = _run_autofocus_passes(
                ctrl, z_range_um, z_step_um, autofocus_method, settle_ms
            )
            # Non-convergence restores the entry Z; the protocol still runs
            # there (same plane as no autofocus), but the result must say so —
            # a silent {"status": "complete"} on an unfocused position is the
            # design/14 §4 failure mode.
            af_info: dict[str, Any] = {
                "best_z_um": round(af.final_z_um, 3),
                "autofocus_converged": af.converged,
            }
            if not af.converged:
                af_info["autofocus_warning"] = af.reason

            pos_save_dir = str(Path(save_dir) / pos_name)
            Path(pos_save_dir).mkdir(parents=True, exist_ok=True)
            try:
                if protocol == "snap":
                    ctrl.studio.live().snap(True)
                    results.append({"position": pos_name, **af_info, "status": "snapped"})
                elif protocol == "zstack":
                    r = run_zstack(
                        ctrl, guard, save_dir=pos_save_dir, name=pos_name,
                        _reservation=reservation, **params
                    )
                    results.append({"position": pos_name, **af_info, **r})
                elif protocol == "timelapse":
                    r = run_timelapse(
                        ctrl, guard, save_dir=pos_save_dir, name=pos_name,
                        _reservation=reservation, **params
                    )
                    results.append({"position": pos_name, **af_info, **r})
                else:
                    results.append(
                        {
                            "position": pos_name,
                            **af_info,
                            "error": f"Unknown protocol '{protocol}'.",
                        }
                    )
            except Exception as e:
                results.append(
                    {"position": pos_name, **af_info, "error": str(e)}
                )
    n_ok = sum(1 for r in results if "error" not in r)
    restore = _live_restore_report(live_state)
    return {
        "status": f"{n_ok}/{len(requested)} positions completed with autofocus.",
        "results": results,
        **({"live_view_restore": restore} if restore else {}),
    }


# --- Hook-based adaptive acquisition ---

def _prepare_log_path(guard: SafetyGuard, log_path: str | None) -> str | None:
    """Resolve a hook's log path in the workspace and create its parent directory.

    The hook writes this file itself, so the path never passed the guard on its
    way to being written. Local reads are unconfined but writes are not, so
    resolve it here or a hook can write outside a configured workspace.

    The mkdir matters as much as the resolve: the hook only opens the file on
    its first frame, so a missing parent surfaces as FileNotFoundError *inside
    the image processor*, after the acquisition has already moved the stage and
    written a dataset. save_dir is created up front; log_path must be too.
    """
    if not log_path:
        return None
    log_path = guard.resolve_in_workspace(log_path)
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
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
    return UntrustedHookAdapter(hook_cls(**params), log_path=log_path)


def _configure_hook_capabilities(hook: Any, ctrl: MicroscopeController,
                                 guard: SafetyGuard, save_dir: str, name: str,
                                 illumination_envelope: dict | None,
                                 artifact_limits: dict | None) -> None:
    """Validate and authorize independent parent-side hook capabilities."""
    from microclaw.hook_decisions import UntrustedHookAdapter
    if not isinstance(hook, UntrustedHookAdapter):
        if illumination_envelope or artifact_limits:
            raise ValueError("Hook envelopes apply only to saved generated hooks.")
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
    if illumination_envelope is None:
        return
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
        max_power_percent=float(ceiling), max_writes=writes, initial_value=initial,
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


@_acquisition_entry_point
def run_adaptive_zstack(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    z_start_um: float,
    z_end_um: float,
    z_step_um: float,
    save_dir: str,
    hook_strategy: str,
    hook_params: dict | None = None,
    channel: str | None = None,
    name: str = "adaptive",
    log_path: str | None = None,
    illumination_envelope: dict | None = None,
    artifact_limits: dict | None = None,
) -> dict:
    """Run a Z-stack acquisition with a hook strategy for adaptive behaviour.

    hook_strategy: a key from PRECODED_HOOK_REGISTRY or a saved hook name.
    After the acquisition, call read_hook_log(log_path) to retrieve results.
    """
    save_dir = guard.resolve_in_workspace(save_dir)
    log_path = _prepare_log_path(guard, log_path)
    guard.check_z(z_start_um)
    guard.check_z(z_end_um)
    if channel:
        guard.check_channel(channel)

    try:
        hook = _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path)
    except ValueError as e:
        return {"error": str(e)}

    events = _build_acquisition_events(
        channel=channel, z_start=z_start_um, z_end=z_end_um, z_step=z_step_um,
    )
    _configure_hook_capabilities(hook, ctrl, guard, save_dir, name,
                                 illumination_envelope, artifact_limits)
    reservation = _authorize_acquisition(
        ctrl, guard, plan_events(ctrl, events, None)
    )
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    try:
        dataset_path = _acquire_with_hooks(
            guard, save_dir, name, events, hook, reservation=reservation
        )
    except _HookedAcquisitionFailure as exc:
        return _hooked_failure_result(exc, log_path)
    completed_at = datetime.now(timezone.utc)
    return _adaptive_result(
        dataset_path, log_path, frames_planned=len(events), frames_acquired=len(events),
        started_at=started_at.isoformat(), completed_at=completed_at.isoformat(),
        duration_s=round(time.monotonic() - started, 6),
        **_reservation_report(reservation),
    )


@_acquisition_entry_point
def run_adaptive_timelapse(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    n_frames: int,
    interval_s: float,
    save_dir: str,
    hook_strategy: str,
    hook_params: dict | None = None,
    channel: str | None = None,
    name: str = "adaptive",
    log_path: str | None = None,
    illumination_envelope: dict | None = None,
    artifact_limits: dict | None = None,
) -> dict:
    """Run a timelapse acquisition with a hook strategy for adaptive behaviour.

    hook_strategy: a key from PRECODED_HOOK_REGISTRY or a saved hook name.
    After the acquisition, call read_hook_log(log_path) to retrieve results.
    """
    save_dir = guard.resolve_in_workspace(save_dir)
    log_path = _prepare_log_path(guard, log_path)
    if channel:
        guard.check_channel(channel)

    try:
        hook = _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path)
    except ValueError as e:
        return {"error": str(e)}

    events = _build_acquisition_events(
        channel=channel, num_time_points=n_frames, time_interval_s=interval_s,
    )
    _configure_hook_capabilities(hook, ctrl, guard, save_dir, name,
                                 illumination_envelope, artifact_limits)
    reservation = _authorize_acquisition(ctrl, guard, plan_events(ctrl, events, None))
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    dataset_path = _acquire_with_hooks(
        guard, save_dir, name, events, hook, reservation=reservation
    )
    completed_at = datetime.now(timezone.utc)
    return _adaptive_result(
        dataset_path, log_path, frames_planned=len(events), frames_acquired=len(events),
        started_at=started_at.isoformat(), completed_at=completed_at.isoformat(),
        duration_s=round(time.monotonic() - started, 6),
        **_reservation_report(reservation),
    )


def _acquire_positions_with_hook(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    positions: list[dict],
    save_dir: str,
    name: str,
    hook_strategy: str,
    hook_params: dict | None = None,
    log_path: str | None = None,
    channel: str | None = None,
    exposure_ms: float | None = None,
    illumination_envelope: dict | None = None,
    artifact_limits: dict | None = None,
    **shape_kwargs: Any,
) -> dict:
    """One Acquisition across every position, with a single hook instance.

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
        guard.check_channel(channel)
    if exposure_ms is not None:
        guard.check_exposure(exposure_ms)
        if not channel:
            ctrl.core.set_exposure(exposure_ms)   # eventless exposure; see run_zstack

    try:
        hook = _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path)
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
    _configure_hook_capabilities(hook, ctrl, guard, save_dir, name,
                                 illumination_envelope, artifact_limits)
    reservation = _authorize_acquisition(
        ctrl, guard, plan_events(ctrl, events, exposure_ms)
    )
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
        started_at=started_at.isoformat(), completed_at=completed_at.isoformat(),
        duration_s=round(time.monotonic() - started, 6),
        **_reservation_report(reservation),
    )


class SurveyProgress:
    """How many survey images have come back.

    Written by the processor thread (the hook), read by the event thread (the
    generator in _acquire_survey_with_detector) — so it is a real cross-thread
    object, not a counter. Nothing else in microclaw tracks acquisition
    progress; this is the one genuinely new primitive design/24 introduces.
    """

    def __init__(self, n_survey: int) -> None:
        self._n_survey, self._lock, self._done = n_survey, threading.Lock(), 0
        self._done_early = False
        self._budget_exhausted = False

    def image_done(self) -> None:
        with self._lock:
            self._done += 1

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
        if not hook.proposes_actions:
            # A legacy saved hook has no way to reach candidates/progress, so it
            # can never advance the survey past the seed tile. Left to run, the
            # generator would idle out max_idle_s and log "stalled" — a
            # structural impossibility reported as a hardware symptom. Refuse
            # before any position is exposed.
            raise ValueError(
                "This saved hook defines only a legacy image_process_fn, so it "
                "cannot propose ContinueSurvey or StopSurvey and can never "
                "advance an adaptive survey past the seed tile. Give it an "
                "analyze_frame(image, metadata) method, or run it under a "
                "batched runner (run_tile_acquisition, run_adaptive_timelapse, "
                "run_adaptive_zstack)."
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
        guard.check_channel(channel)
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

    _configure_hook_capabilities(hook, ctrl, guard, save_dir, name,
                                 illumination_envelope, artifact_limits)

    if isinstance(hook, UntrustedHookAdapter):
        if adaptive:
            hook.configure_adaptive(
                events=survey_events, candidates=candidates, progress=progress,
                guard=guard, max_events=len(survey_events),
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
                                  max_events=len(survey_events) if adaptive else None)
    reservation = (
        _authorize_acquisition(
            ctrl, guard, plan_events(ctrl, survey_events, exposure_ms)
        )
        if adaptive else None
    )
    dataset_path = _acquire_with_hooks(
        guard, save_dir, name, events, hook, reservation=reservation
    )
    return _adaptive_result(
        dataset_path, hook.log_path,
        status=f"Survey acquisition complete across {len(positions)} position(s).",
        positions=len(positions),
        **(_reservation_report(reservation) if reservation is not None else {}),
    )


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
        resolved = [{"name": p["name"], "x_um": p["x_um"], "y_um": p["y_um"]}
                    for p in positions]

    log_path = _prepare_log_path(guard, log_path)
    try:
        hook = _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path)
    except ValueError as e:
        return {"error": str(e)}

    progress = SurveyProgress(len(resolved))
    candidates: queue.Queue = queue.Queue()
    result = _acquire_survey_with_detector(
        ctrl, guard, resolved, save_dir, name,
        hook=hook, progress=progress, candidates=candidates,
        max_idle_s=max_idle_s,
        channel=params.get("channel"), exposure_ms=params.get("exposure_ms"),
        adaptive=True, illumination_envelope=illumination_envelope,
        artifact_limits=artifact_limits, **shape,
    )
    # The batched status ("complete across 9 position(s)") is exactly the
    # sentence that made 5 ghost exposures read as a clean early stop
    # (20260716_140329). Say what actually ran, from the counter the hook
    # itself drove — and attach the planned coordinates so the hook log
    # joins on `position` without re-imaging (design/23 Episode A).
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
    result["budget_exhausted"] = progress.exhausted_budget
    result["tiles_planned"] = [
        {"position": p["name"], "x_um": round(p["x_um"], 3),
         "y_um": round(p["y_um"], 3)} for p in resolved
    ]
    return result


def read_hook_log(ctrl: MicroscopeController, guard: SafetyGuard, log_path: str) -> dict:
    """Read a hook's output log file after an acquisition completes."""
    log_path = guard.resolve_readable_path(log_path)
    path = Path(log_path)
    if not path.exists():
        return {"error": f"Log file not found: {log_path}"}
    entries = json.loads(path.read_text(encoding="utf-8"))
    return {"log_path": log_path, "entry_count": len(entries), "entries": entries,
            "artifact": {"kind": "hook_log", "path": log_path}}


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
    for i, entry in enumerate(entries):
        label = entry.get("position")
        result = entry.get("result") or {}
        missing = [k for k in ("position", "x_um", "y_um") if entry.get(k) is None]
        if metric not in result:
            missing.append(f"result.{metric}")
        if missing:
            return {"error": f"Entry {i} is missing required field(s): {missing}"}
        if label in seen:
            return {"error": f"Duplicate position in hook log: {label}"}
        seen.add(label)
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
            "focus_metric_valid": result.get("focus_metric_valid"),
            "saturated_fraction": result.get("saturated_fraction"),
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
        "entry_count": len(rows),
        "ranking": rows,
        "budget_views": {str(k): rows[:k] for k in requested},
    }
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
            except SafetyViolation:
                rejected.append({"name": name,
                                 "reason": "Rejected by the current XY safety guard."})
                continue
            if position.get("z_um") is not None:
                try:
                    guard.check_z(float(position["z_um"]))
                except SafetyViolation:
                    rejected.append({"name": name,
                                     "reason": "Rejected by the current Z safety guard."})
                    continue
            accepted.append({"name": name, "x_um": position["x_um"],
                             "y_um": position["y_um"],
                             **({"z_um": position["z_um"]}
                                if position.get("z_um") is not None else {})})
        except (TypeError, ValueError) as e:
            rejected.append({"name": name, "reason": str(e)})
    return {"accepted": accepted, "rejected": rejected, "clipped": 0}


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

def generate_and_save_hook(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    name: str,
    code: str,
    description: str,
    source: str = "claude_generated",
) -> dict:
    """Lint and save a hook script. Call ONLY after the user has confirmed the code.

    The lint is advisory: warnings are surfaced and, if any fire, an explicit
    confirmation is required before saving (benign hooks legitimately use
    open/os, so warnings must not hard-block). The human review of the full code
    is the actual gate.
    """
    from microclaw.hook_manager import lint_hook_code, save_hook, validate_hook_contract
    warnings = lint_hook_code(code)
    contract_errors = validate_hook_contract(code)
    if contract_errors:
        return {
            "error": "Hook failed static preflight; it was not saved.",
            "contract_errors": contract_errors,
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
            "Static syntax and image_process_fn contract passed. Source was not "
            "imported or executed because no hook sandbox is configured."
        ),
    }


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


def list_hooks(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """List all available hook strategies (pre-coded and saved)."""
    from microclaw.hooks import PRECODED_HOOK_REGISTRY
    from microclaw.hook_manager import list_saved_hooks
    return {
        "precoded": list(PRECODED_HOOK_REGISTRY.keys()),
        "saved": list_saved_hooks(),
        "hint": "Call describe_hook(name) to see constructor parameters and resolve-time compatibility.",
    }


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
        return describe_saved_hook(name)
    return {
        "error": f"Unknown hook strategy '{name}'. Run list_hooks() to see available strategies."
    }


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
            "hook_strategy='autofocus_mm_plugin' and require BOTH "
            "plugins.allow_hardware_motion: true AND property_authorization.mode: "
            "degraded_trusted_plugins in safety_config.yaml, then a restart -- the "
            "motion flag alone is refused at startup in guaranteed mode. "
            "Always confirm the classpath with the user before enabling a plugin hook."
        ),
    }


def get_hook_documentation(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    from microclaw.hook_docs import HOOK_REFERENCE
    return {"documentation": HOOK_REFERENCE}


def get_smlm_documentation(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    from microclaw.smlm_docs import SMLM_REFERENCE
    return {"documentation": SMLM_REFERENCE}


def check_emu_installed(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    from microclaw.emu_manager import find_mm_app_dir, find_plugin_jars, _emu_config_path

    mm_dir = find_mm_app_dir(ctrl)
    if mm_dir is None:
        return {
            "emu_installed": False,
            "htsmlm_installed": False,
            "mm_app_dir": None,
            "note": (
                "Micro-Manager installation directory not found automatically. "
                "If MM is installed in a non-standard location, call "
                "get_emu_configuration(mm_app_dir='...') with the correct path."
            ),
        }

    jars = find_plugin_jars(mm_dir)
    config_exists = _emu_config_path(mm_dir).exists()
    return {
        "emu_installed": bool(jars["EMU"]) or config_exists,
        "htsmlm_installed": bool(jars["htSMLM"]),
        "mm_app_dir": str(mm_dir),
        "emu_jars": jars["EMU"],
        "htsmlm_jars": jars["htSMLM"],
        "emu_config_present": config_exists,
    }


def get_htsmlm_documentation(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    from microclaw.htsmlm_docs import HTSMLM_REFERENCE
    return {"documentation": HTSMLM_REFERENCE}


def save_knowledge(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    category: str,
    key: str,
    value: dict,
) -> dict:
    """Persist a knowledge base entry. Gated by an in-code confirmation."""
    import yaml
    from microclaw.knowledge_manager import save_entry
    # A devices/ entry can suppress an alarm (design/21 S4); it must name the
    # hardware it was observed on, or it detaches from its trigger and applies
    # to whatever camera is loaded next.
    if category == "devices" and "observed_on" not in value:
        return {"error":
                "A devices/ entry must carry observed_on: the camera adapter it "
                "was observed with (the 'adapter' field of get_system_state's "
                "camera block, e.g. 'DCam'). An entry that suppresses an alarm "
                "must name the condition it holds under."}
    if not CONFIRM_FN(
        f"Save knowledge {category}/{key}:\n{yaml.safe_dump({key: value})}",
        kind="knowledge",
    ):
        return {"error": "User declined to save this knowledge entry."}
    try:
        save_entry(category, key, value)
    except ValueError as e:
        return {"error": str(e)}
    return {"status": f"Saved '{key}' under '{category}'.", "category": category, "key": key, "value": value}


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
    _EMU_SESSION_CACHE["plugin_name"] = config.get("plugin_name", "")
    return config


def _cached_emu_properties(ctrl: MicroscopeController) -> dict | None:
    """Parsed EMU properties, or None when this is not an EMU rig."""
    if "properties" in _EMU_SESSION_CACHE:
        return _EMU_SESSION_CACHE["properties"]
    from microclaw.emu_manager import find_mm_app_dir

    try:
        mm_dir = find_mm_app_dir(ctrl)
        if mm_dir is None:
            _EMU_SESSION_CACHE["properties"] = None
            return None
        return _read_emu_properties(ctrl, str(mm_dir))["properties"]
    except Exception:
        _EMU_SESSION_CACHE["properties"] = None
        return None


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
    emu_map = build_emu_map(config["properties"])
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


def get_focus_lock_state(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """Read the hardware focus lock via the EMU map ('Z stage focus locking').

    In amr_test the model answered its own 'Focus lock engaged?' checklist item
    with 'You confirmed focus looks fine' — a sharp image is not an engaged
    lock (design/14 §5). This is the one-call check it lacked.
    """
    from microclaw.emu_manager import build_emu_map

    props = _cached_emu_properties(ctrl)
    if not props:
        return {"engaged": None, "reason": "No EMU configuration — cannot read a focus lock."}
    lock = build_emu_map(props)["focus_lock"]
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


def set_focus_lock(
    ctrl: MicroscopeController, guard: SafetyGuard, enabled: bool
) -> dict:
    """Engage or disengage the hardware focus lock via the EMU map."""
    from microclaw.emu_manager import build_emu_map

    props = _cached_emu_properties(ctrl)
    if not props:
        return {"error": "No EMU configuration — cannot control a focus lock."}
    lock = build_emu_map(props)["focus_lock"]
    if lock is None or "device" not in lock:
        return {"error": "No focus-lock property in the EMU map."}
    target = str(lock.get("on", "1")) if enabled else str(lock.get("off", "0"))
    from microclaw.authorization import authorize_property_write
    authorize_property_write(ctrl, lock["device"], lock["property"])
    guard.check_device_property(ctrl.core, lock["device"], lock["property"], target)
    ctrl.core.set_property(lock["device"], lock["property"], target)
    return {
        "engaged": enabled,
        "property": f"{lock['device']}.{lock['property']}",
        "value": target,
    }


def get_emu_laser_map(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """The slot → laser table (enable / power / trigger lines) from the EMU map."""
    from microclaw.emu_manager import build_emu_map

    props = _cached_emu_properties(ctrl)
    if not props:
        return {"error": "No EMU configuration found — this is not an EMU/htSMLM rig."}
    lasers = build_emu_map(props)["lasers"]
    return {
        "lasers": lasers,
        "note": (
            "Slot index pairs each laser with ITS OWN trigger lines. "
            "Verify trigger_mode/trigger_sequence on the SAME slot you enable."
        ),
    }


def resolve_emu_device(
    ctrl: MicroscopeController, guard: SafetyGuard, semantic_name: str
) -> dict:
    """Resolve an EMU semantic name ('Laser 3 enable') to its MM device/property."""
    from microclaw.emu_manager import resolve_emu_device as _resolve

    props = _cached_emu_properties(ctrl)
    if not props:
        return {"error": "No EMU configuration found — this is not an EMU/htSMLM rig."}
    try:
        return {"semantic_name": semantic_name, **_resolve(props, semantic_name)}
    except KeyError as e:
        return {"error": str(e).strip("'\"")}


def _emu_power_entry(ctrl: MicroscopeController, slot: int) -> dict:
    from microclaw.emu_manager import build_emu_map
    props = _cached_emu_properties(ctrl)
    if not props:
        raise ValueError("No EMU configuration found — this is not an EMU/htSMLM rig.")
    entry = build_emu_map(props)["lasers"].get(int(slot), {}).get("power_pct")
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
    ctrl.studio.app().refresh_gui()
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
    "run_zstack": run_zstack,
    "run_timelapse": run_timelapse,
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
    "run_adaptive_zstack": run_adaptive_zstack,
    "run_adaptive_timelapse": run_adaptive_timelapse,
    "run_adaptive_survey": run_adaptive_survey,
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
    "save_knowledge": save_knowledge,
    "get_knowledge": get_knowledge,
    "delete_knowledge": delete_knowledge,
}


def execute_tool(
    name: str,
    tool_input: dict,
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    cancel=None,
) -> str | list:
    """Execute a tool and return content for the tool_result block.

    Returns a list of content blocks for image-returning tools, or a JSON string
    for all other tools. Never raises — errors are captured and returned.
    """
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool '{name}'."})
    try:
        if (
            name != "run_mda"
            and getattr(fn, "_microclaw_acquisition_entry_point", False)
        ):
            from microclaw.authorization import authorize_path
            authorize_path(ctrl, f"acquisition-tool:{name}")
        if name == "set_channel":
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
