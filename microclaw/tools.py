from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import tifffile
from pycromanager import Acquisition, multi_d_acquisition_events
from ndstorage import Dataset

from microclaw.controller import MicroscopeController
from microclaw.safety import SafetyGuard, SafetyViolation


def _wait(ctrl: MicroscopeController, device: str | None = None) -> None:
    if device:
        ctrl.core.wait_for_device(device)
    else:
        ctrl.core.wait_for_system()


# --- Camera ---

def snap_image(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    ctrl.studio.live().snap(True)
    return {"status": "Image snapped and displayed in MM viewer."}


def start_live_view(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    ctrl.studio.live().set_live_mode_on(True)
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
    return {"x_um": round(target_x, 3), "y_um": round(target_y, 3), "status": "Moved."}


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


# --- Channel / Config ---

def set_channel(ctrl: MicroscopeController, guard: SafetyGuard, preset: str) -> dict:
    guard.check_channel(preset)
    ctrl.core.set_config("Channel", preset)
    ctrl.core.wait_for_config("Channel", preset)
    return {"status": f"Channel set to '{preset}'."}


def get_available_channels(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    channels = list(ctrl.core.get_available_configs("Channel"))
    return {"channels": channels}


# --- Device Properties ---

def set_device_property(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    device: str,
    property: str,
    value: str,
) -> dict:
    guard.check_property(device, property)
    ctrl.core.set_property(device, property, value)
    return {"status": f"Set {device}.{property} = {value!r}."}


def get_device_property(
    ctrl: MicroscopeController, guard: SafetyGuard, device: str, property: str
) -> dict:
    value = ctrl.core.get_property(device, property)
    return {"device": device, "property": property, "value": value}


def list_devices(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    devices = list(ctrl.core.get_loaded_devices())
    return {"devices": devices}


# --- System State ---

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
    return state


# --- Acquisitions ---

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
) -> dict:
    guard.check_z(z_start_um)
    guard.check_z(z_end_um)
    if channel:
        guard.check_channel(channel)
    if exposure_ms is not None:
        guard.check_exposure(exposure_ms)

    kwargs: dict[str, Any] = {
        "z_start": z_start_um,
        "z_end": z_end_um,
        "z_step": z_step_um,
    }
    if channel:
        kwargs.update(channel_group="Channel", channels=[channel])
        if exposure_ms is not None:
            kwargs["channel_exposures_ms"] = [exposure_ms]
    elif exposure_ms is not None:
        ctrl.core.set_exposure(exposure_ms)

    events = multi_d_acquisition_events(**kwargs)
    save_path = str(Path(save_dir) / name)

    with Acquisition(directory=save_dir, name=name, show_display=True) as acq:
        acq.acquire(events)

    return {"status": "Z-stack complete.", "dataset_path": save_path}


def run_timelapse(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    n_frames: int,
    interval_s: float,
    save_dir: str,
    channel: str | None = None,
    exposure_ms: float | None = None,
    name: str = "timelapse",
) -> dict:
    if channel:
        guard.check_channel(channel)
    if exposure_ms is not None:
        guard.check_exposure(exposure_ms)

    kwargs: dict[str, Any] = {
        "num_time_points": n_frames,
        "time_interval_s": interval_s,
    }
    if channel:
        kwargs.update(channel_group="Channel", channels=[channel])
        if exposure_ms is not None:
            kwargs["channel_exposures_ms"] = [exposure_ms]

    events = multi_d_acquisition_events(**kwargs)
    save_path = str(Path(save_dir) / name)

    with Acquisition(directory=save_dir, name=name, show_display=True) as acq:
        acq.acquire(events)

    return {"status": "Timelapse complete.", "dataset_path": save_path}


def export_dataset_as_tiff(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    dataset_path: str,
    output_path: str,
) -> dict:
    dataset = Dataset(dataset_path)
    axes = dataset.axes

    if "z" in axes:
        frames = [
            dataset.read_image(z=z) for z in range(len(axes["z"]))
        ]
    elif "time" in axes:
        frames = [
            dataset.read_image(time=t) for t in range(len(axes["time"]))
        ]
    else:
        frames = [dataset.read_image()]

    stack = np.stack(frames)
    tifffile.imwrite(output_path, stack, imagej=True)
    return {"status": "Export complete.", "output_path": output_path}


# --- Tool Registry ---

TOOL_REGISTRY = {
    "snap_image": snap_image,
    "start_live_view": start_live_view,
    "stop_live_view": stop_live_view,
    "set_exposure": set_exposure,
    "get_exposure": get_exposure,
    "get_xy_position": get_xy_position,
    "move_stage_xy": move_stage_xy,
    "get_z_position": get_z_position,
    "move_stage_z": move_stage_z,
    "set_channel": set_channel,
    "get_available_channels": get_available_channels,
    "set_device_property": set_device_property,
    "get_device_property": get_device_property,
    "list_devices": list_devices,
    "get_system_state": get_system_state,
    "run_zstack": run_zstack,
    "run_timelapse": run_timelapse,
    "export_dataset_as_tiff": export_dataset_as_tiff,
}


def execute_tool(
    name: str,
    tool_input: dict,
    ctrl: MicroscopeController,
    guard: SafetyGuard,
) -> str:
    """Execute a tool and return a JSON string for the tool_result block.
    Never raises — all errors are captured and returned as error content."""
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool '{name}'."})
    try:
        result = fn(ctrl, guard, **tool_input)
        return json.dumps(result)
    except SafetyViolation as e:
        return json.dumps({
            "error": f"Safety constraint prevented this action: {e}"
        })
    except Exception as e:
        return json.dumps({
            "error": f"{type(e).__name__}: {e}",
            "hint": (
                "This may be a hardware error (device busy, stage at limit, "
                "device not found) or a connection problem."
            ),
        })
