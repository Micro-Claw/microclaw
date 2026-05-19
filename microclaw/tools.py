from __future__ import annotations
import inspect
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import tifffile
from pycromanager import Acquisition, multi_d_acquisition_events
from ndstorage import Dataset

from microclaw.autofocus import coarse_then_fine_autofocus, sweep_autofocus
from microclaw.controller import MicroscopeController
from microclaw.image_analysis import compute_stats, make_thumbnail, snap_to_numpy
from microclaw.safety import SafetyGuard, SafetyViolation


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
    channels = _str_vector(ctrl.core.get_available_configs("Channel"))
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
    devices = _str_vector(ctrl.core.get_loaded_devices())
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

    with Acquisition(directory=save_dir, name=name, show_display=False) as acq:
        acq.acquire(events)

    actual_path = acq._dataset_disk_location or str(Path(save_dir) / name)
    return {"status": "Z-stack complete.", "dataset_path": actual_path}


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

    with Acquisition(directory=save_dir, name=name, show_display=False) as acq:
        acq.acquire(events)

    actual_path = acq._dataset_disk_location or str(Path(save_dir) / name)
    return {"status": "Timelapse complete.", "dataset_path": actual_path}


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


# --- Image capture with analysis ---

def snap_and_analyze(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    thumbnail_size: int = 512,
) -> list:
    """Snap an image and return numerical stats plus a thumbnail for Claude's vision."""
    image = snap_to_numpy(ctrl)
    stats = compute_stats(image)
    text_payload = {
        "z_um": round(ctrl.core.get_position(), 3),
        "focus_metric": round(stats.focus_metric, 2),
        "mean_intensity": round(stats.mean_intensity, 1),
        "max_intensity": round(stats.max_intensity, 1),
        "saturated_fraction": round(stats.saturated_fraction, 4),
    }
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


# --- Autofocus (Form A — standalone) ---

def run_autofocus(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    z_range_um: float,
    z_step_um: float,
    method: str = "coarse_then_fine",
    settle_ms: int = 50,
    return_thumbnail: bool = True,
) -> list | dict:
    """Sweep Z to find the sharpest focal plane."""
    current_z = ctrl.core.get_position()
    guard.check_z(current_z - z_range_um / 2)
    guard.check_z(current_z + z_range_um / 2)

    if method == "coarse_then_fine":
        result = coarse_then_fine_autofocus(
            ctrl, z_range_um, max(z_step_um * 5, 1.0), z_step_um, settle_ms
        )
    else:
        result = sweep_autofocus(
            ctrl,
            current_z - z_range_um / 2,
            current_z + z_range_um / 2,
            z_step_um,
            settle_ms,
        )

    payload: dict[str, Any] = {
        "best_z_um": round(result.best_z_um, 3),
        "settled": result.settled,
        "metric_curve": [round(v, 2) for v in result.metric_values],
        "z_positions": [round(z, 3) for z in result.z_positions],
        "warning": (
            None
            if result.settled
            else "Peak focus was at the edge of the sweep range; consider widening z_range_um."
        ),
    }

    if not return_thumbnail:
        return payload

    image = snap_to_numpy(ctrl)
    payload["focus_metric_at_best"] = round(compute_stats(image).focus_metric, 2)
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

def mark_position(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    name: str,
    include_z: bool = True,
) -> dict:
    """Record the current stage position in MM's native position list."""
    x = round(ctrl.core.get_x_position(), 3)
    y = round(ctrl.core.get_y_position(), 3)
    z = round(ctrl.core.get_position(), 3) if include_z else None
    guard.check_xy(x, y)
    if z is not None:
        guard.check_z(z)
    ctrl.add_position(name, x, y, z)
    return {
        "status": f"Position '{name}' saved to MM position list.",
        "x_um": x,
        "y_um": y,
        **({"z_um": z} if z is not None else {}),
    }


def get_position_list(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """Return all positions from MM's native position list."""
    positions = ctrl.get_positions()
    return {"positions": positions, "count": len(positions)}


def go_to_position(ctrl: MicroscopeController, guard: SafetyGuard, name: str) -> dict:
    """Move the stage to a named position from MM's native position list."""
    positions = {p["name"]: p for p in ctrl.get_positions()}
    if name not in positions:
        return {"error": f"Position '{name}' not found."}
    pos = positions[name]
    guard.check_xy(pos["x_um"], pos["y_um"])
    if "z_um" in pos:
        guard.check_z(pos["z_um"])
    ctrl.go_to_position(name)
    return {"status": f"Moved to '{name}'.", **pos}


def delete_position(ctrl: MicroscopeController, guard: SafetyGuard, name: str) -> dict:
    """Delete a named position from MM's native position list."""
    ctrl.remove_position(name)
    return {"status": f"Position '{name}' deleted from MM position list."}


def clear_position_list(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """Clear all positions from MM's native position list."""
    ctrl.clear_positions()
    return {"status": "Position list cleared."}


def save_position_list(ctrl: MicroscopeController, guard: SafetyGuard, path: str) -> dict:
    """Save MM's position list to a .pos file."""
    ctrl.save_position_list(path)
    return {"status": f"Position list saved to {path}."}


def load_position_list(ctrl: MicroscopeController, guard: SafetyGuard, path: str) -> dict:
    """Load a .pos file into MM's native position list."""
    ctrl.load_position_list(path)
    positions = ctrl.get_positions()
    return {"status": f"Loaded {len(positions)} positions from {path}.", "count": len(positions)}


def import_mm_positions(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """Import positions from MM's GUI position list into the agent's internal store.

    Use this after the user has set up positions in Micro-Manager's Position List
    Manager. The imported positions will be available for all acquisition tools.
    """
    names = ctrl.import_from_mm_position_list()
    positions = ctrl.get_positions()
    return {
        "status": f"Imported {len(names)} position(s) from MM.",
        "imported": names,
        "total": len(positions),
    }


# --- Multiposition acquisition ---

def run_multiposition_acquisition(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    position_names: list[str],
    protocol: str,
    save_dir: str,
    name: str = "multipos",
    protocol_params: dict | None = None,
) -> dict:
    """Visit each position in the MM position list and run a per-position protocol."""
    params = protocol_params or {}
    all_positions = {p["name"]: p for p in ctrl.get_positions()}
    results = []

    for pos_name in position_names:
        if pos_name not in all_positions:
            results.append({"position": pos_name, "error": "Not found in position list."})
            continue
        pos = all_positions[pos_name]
        guard.check_xy(pos["x_um"], pos["y_um"])
        ctrl.go_to_position(pos_name)
        pos_save_dir = str(Path(save_dir) / pos_name)
        Path(pos_save_dir).mkdir(parents=True, exist_ok=True)
        try:
            if protocol == "snap":
                ctrl.studio.live().snap(True)
                results.append({"position": pos_name, "status": "snapped"})
            elif protocol == "zstack":
                r = run_zstack(ctrl, guard, save_dir=pos_save_dir, name=pos_name, **params)
                results.append({"position": pos_name, **r})
            elif protocol == "timelapse":
                r = run_timelapse(ctrl, guard, save_dir=pos_save_dir, name=pos_name, **params)
                results.append({"position": pos_name, **r})
            else:
                results.append({"position": pos_name, "error": f"Unknown protocol '{protocol}'."})
        except Exception as e:
            results.append({"position": pos_name, "error": str(e)})

    n_ok = sum(1 for r in results if "error" not in r)
    return {
        "status": f"{n_ok}/{len(position_names)} positions completed.",
        "results": results,
    }


def run_multiposition_with_autofocus(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    position_names: list[str],
    z_range_um: float,
    z_step_um: float,
    protocol: str,
    save_dir: str,
    name: str = "multipos_af",
    autofocus_method: str = "coarse_then_fine",
    settle_ms: int = 50,
    protocol_params: dict | None = None,
) -> dict:
    """Visit each position, autofocus, then run a per-position protocol."""
    params = protocol_params or {}
    all_positions = {p["name"]: p for p in ctrl.get_positions()}
    results = []

    for pos_name in position_names:
        if pos_name not in all_positions:
            results.append({"position": pos_name, "error": "Not found in position list."})
            continue
        pos = all_positions[pos_name]
        guard.check_xy(pos["x_um"], pos["y_um"])
        ctrl.go_to_position(pos_name)

        current_z = ctrl.core.get_position()
        try:
            guard.check_z(current_z - z_range_um / 2)
            guard.check_z(current_z + z_range_um / 2)
        except Exception as e:
            results.append(
                {"position": pos_name, "error": f"Autofocus range out of bounds: {e}"}
            )
            continue

        coarse_step = max(z_step_um * 5, 1.0)
        af = (
            coarse_then_fine_autofocus(ctrl, z_range_um, coarse_step, z_step_um, settle_ms)
            if autofocus_method == "coarse_then_fine"
            else sweep_autofocus(
                ctrl,
                current_z - z_range_um / 2,
                current_z + z_range_um / 2,
                z_step_um,
                settle_ms,
            )
        )

        pos_save_dir = str(Path(save_dir) / pos_name)
        Path(pos_save_dir).mkdir(parents=True, exist_ok=True)
        try:
            if protocol == "snap":
                ctrl.studio.live().snap(True)
                results.append(
                    {"position": pos_name, "best_z_um": round(af.best_z_um, 3), "status": "snapped"}
                )
            elif protocol == "zstack":
                r = run_zstack(ctrl, guard, save_dir=pos_save_dir, name=pos_name, **params)
                results.append({"position": pos_name, "best_z_um": round(af.best_z_um, 3), **r})
            elif protocol == "timelapse":
                r = run_timelapse(ctrl, guard, save_dir=pos_save_dir, name=pos_name, **params)
                results.append({"position": pos_name, "best_z_um": round(af.best_z_um, 3), **r})
            else:
                results.append(
                    {
                        "position": pos_name,
                        "best_z_um": round(af.best_z_um, 3),
                        "error": f"Unknown protocol '{protocol}'.",
                    }
                )
        except Exception as e:
            results.append(
                {"position": pos_name, "best_z_um": round(af.best_z_um, 3), "error": str(e)}
            )

    n_ok = sum(1 for r in results if "error" not in r)
    return {
        "status": f"{n_ok}/{len(position_names)} positions completed with autofocus.",
        "results": results,
    }


# --- Hook-based adaptive acquisition ---

def run_adaptive_acquisition(
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
) -> dict:
    """Run a Z-stack acquisition with a hook strategy for adaptive behaviour.

    hook_strategy: a key from PRECODED_HOOK_REGISTRY or a saved hook name.
    After the acquisition, call read_hook_log(log_path) to retrieve results.
    """
    from microclaw.hooks import PRECODED_HOOK_REGISTRY
    from microclaw.hook_manager import load_hook_class, list_saved_hooks

    guard.check_z(z_start_um)
    guard.check_z(z_end_um)
    if channel:
        guard.check_channel(channel)

    params = dict(hook_params or {})
    if log_path:
        params["log_path"] = log_path

    if hook_strategy in PRECODED_HOOK_REGISTRY:
        hook_cls = PRECODED_HOOK_REGISTRY[hook_strategy]
    elif hook_strategy in list_saved_hooks():
        hook_cls = load_hook_class(hook_strategy)
    else:
        return {
            "error": (
                f"Unknown hook strategy '{hook_strategy}'. "
                "Run list_hooks() to see available strategies."
            )
        }

    sig = inspect.signature(hook_cls.__init__)
    if "ctrl" in sig.parameters:
        params.setdefault("ctrl", ctrl)
    if "guard" in sig.parameters:
        params.setdefault("guard", guard)

    hook = hook_cls(**params)

    acq_kwargs: dict[str, Any] = {"z_start": z_start_um, "z_end": z_end_um, "z_step": z_step_um}
    if channel:
        acq_kwargs.update(channel_group="Channel", channels=[channel])
    events = multi_d_acquisition_events(**acq_kwargs)

    hook_fn_kwargs: dict[str, Any] = {}
    if hasattr(hook, "post_hardware_hook_fn"):
        hook_fn_kwargs["post_hardware_hook_fn"] = hook.post_hardware_hook_fn
    if hasattr(hook, "image_process_fn"):
        hook_fn_kwargs["image_process_fn"] = hook.image_process_fn

    with Acquisition(directory=save_dir, name=name, show_display=False, **hook_fn_kwargs) as acq:
        acq.acquire(events)

    result: dict[str, Any] = {
        "status": "Adaptive acquisition complete.",
        "dataset_path": str(Path(save_dir) / name),
    }
    if log_path:
        result["log_path"] = log_path
        result["hint"] = "Call read_hook_log to retrieve per-image results."
    return result


def read_hook_log(ctrl: MicroscopeController, guard: SafetyGuard, log_path: str) -> dict:
    """Read a hook's output log file after an acquisition completes."""
    path = Path(log_path)
    if not path.exists():
        return {"error": f"Log file not found: {log_path}"}
    entries = json.loads(path.read_text())
    return {"log_path": log_path, "entry_count": len(entries), "entries": entries}


# --- Hook management ---

def generate_and_save_hook(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    name: str,
    code: str,
    description: str,
    source: str = "claude_generated",
) -> dict:
    """Validate and save a hook script. Call ONLY after the user has confirmed the code."""
    from microclaw.hook_manager import validate_hook_code, save_hook
    warnings = validate_hook_code(code)
    if warnings:
        return {
            "error": "Safety validation found issues — hook not saved.",
            "warnings": warnings,
        }
    save_hook(name, code, description, source=source)
    return {
        "status": f"Hook '{name}' saved successfully.",
        "path": str(Path.home() / ".microclaw" / "hooks" / f"{name}.py"),
        "source": source,
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
        code, warnings = _read(path)
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
    }


# --- Tool Registry ---

TOOL_REGISTRY = {
    "snap_image": snap_image,
    "snap_and_analyze": snap_and_analyze,
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
    "run_multiposition_with_autofocus": run_multiposition_with_autofocus,
    "run_adaptive_acquisition": run_adaptive_acquisition,
    "read_hook_log": read_hook_log,
    "generate_and_save_hook": generate_and_save_hook,
    "read_hook_from_file": read_hook_from_file,
    "list_hooks": list_hooks,
}


def execute_tool(
    name: str,
    tool_input: dict,
    ctrl: MicroscopeController,
    guard: SafetyGuard,
) -> str | list:
    """Execute a tool and return content for the tool_result block.

    Returns a list of content blocks for image-returning tools, or a JSON string
    for all other tools. Never raises — errors are captured and returned.
    """
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool '{name}'."})
    try:
        result = fn(ctrl, guard, **tool_input)
        return result if isinstance(result, list) else json.dumps(result)
    except SafetyViolation as e:
        return json.dumps({"error": f"Safety constraint prevented this action: {e}"})
    except Exception as e:
        return json.dumps({
            "error": f"{type(e).__name__}: {e}",
            "hint": (
                "This may be a hardware error (device busy, stage at limit, "
                "device not found) or a connection problem."
            ),
        })
