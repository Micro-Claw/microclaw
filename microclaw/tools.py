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
    ctrl.core.set_roi(x, y, width, height)
    _wait(ctrl, ctrl.core.get_camera_device())
    live_restarted = _bounce_live_if_on(ctrl)
    result: dict = {"status": "ROI set.", "x": x, "y": y, "width": width, "height": height}
    if live_restarted:
        result["live_view"] = "restarted"
    return result


def clear_roi(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
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


def list_device_properties(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    device: str,
) -> dict:
    props = _str_vector(ctrl.core.get_device_property_names(device))
    return {"device": device, "properties": props, "count": len(props)}


def get_device_property_info(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    device: str,
    property: str,
) -> dict:
    read_only = bool(ctrl.core.is_property_read_only(device, property))
    pre_init = bool(ctrl.core.is_property_pre_init(device, property))
    prop_type = str(ctrl.core.get_property_type(device, property)).split(".")[-1]

    allowed_sv = ctrl.core.get_allowed_property_values(device, property)
    allowed = _str_vector(allowed_sv) if allowed_sv.size() > 0 else None

    has_limits = bool(ctrl.core.has_property_limits(device, property))
    lower = ctrl.core.get_property_lower_limit(device, property) if has_limits else None
    upper = ctrl.core.get_property_upper_limit(device, property) if has_limits else None

    current = ctrl.core.get_property(device, property)

    return {
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
    save_dir: str,
    name: str,
    events: list,
    hook: Any | None = None,
) -> str:
    """Run one Acquisition, attaching hook callables if a hook is supplied.

    Returns the on-disk dataset path. A hook is any object exposing
    post_hardware_hook_fn and/or image_process_fn; both are optional and are
    wired in only if present, so the same runner serves plain and adaptive
    acquisitions of any event shape.
    """
    hook_fn_kwargs: dict[str, Any] = {}
    if hook is not None:
        if hasattr(hook, "post_hardware_hook_fn"):
            hook_fn_kwargs["post_hardware_hook_fn"] = hook.post_hardware_hook_fn
        if hasattr(hook, "image_process_fn"):
            hook_fn_kwargs["image_process_fn"] = hook.image_process_fn

    with Acquisition(directory=save_dir, name=name, show_display=True, **hook_fn_kwargs) as acq:
        acq.acquire(events)

    return acq._dataset_disk_location or str(Path(save_dir) / name)


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
    if not channel and exposure_ms is not None:
        ctrl.core.set_exposure(exposure_ms)

    events = _build_acquisition_events(
        channel=channel, exposure_ms=exposure_ms,
        z_start=z_start_um, z_end=z_end_um, z_step=z_step_um,
    )
    dataset_path = _acquire_with_hooks(save_dir, name, events)
    return {"status": "Z-stack complete.", "dataset_path": dataset_path}


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

    events = _build_acquisition_events(
        channel=channel, exposure_ms=exposure_ms,
        num_time_points=n_frames, time_interval_s=interval_s,
    )
    dataset_path = _acquire_with_hooks(save_dir, name, events)
    return {"status": "Timelapse complete.", "dataset_path": dataset_path}


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
    return_thumbnail: bool = False,
    thumbnail_size: int = 512,
) -> list | dict:
    """Snap an image and return numerical stats, plus an optional thumbnail."""
    image = snap_to_numpy(ctrl)
    stats = compute_stats(image)
    text_payload = {
        "z_um": round(ctrl.core.get_position(), 3),
        "focus_metric": round(stats.focus_metric, 2),
        "mean_intensity": round(stats.mean_intensity, 1),
        "max_intensity": round(stats.max_intensity, 1),
        "saturated_fraction": round(stats.saturated_fraction, 4),
    }
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

    live = ctrl.studio.live()
    was_live = live.is_live_mode_on()
    if was_live:
        live.set_live_mode_on(False)
    try:
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
    finally:
        if was_live:
            live.set_live_mode_on(True)

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

def _run_protocol_at(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    pos_label: str,
    x_um: float,
    y_um: float,
    z_um: float | None,
    protocol: str,
    pos_save_dir: str,
    params: dict,
) -> dict:
    guard.check_xy(x_um, y_um)
    ctrl.core.set_xy_position(x_um, y_um)
    _wait(ctrl, ctrl.core.get_xy_stage_device())
    if z_um is not None:
        guard.check_z(z_um)
        ctrl.core.set_position(z_um)
        _wait(ctrl, ctrl.core.get_focus_device())
    Path(pos_save_dir).mkdir(parents=True, exist_ok=True)
    if protocol == "snap":
        ctrl.studio.live().snap(True)
        return {"position": pos_label, "status": "snapped", "saved": False}
    elif protocol == "zstack":
        r = run_zstack(ctrl, guard, save_dir=pos_save_dir, name=pos_label, **params)
        return {"position": pos_label, **r}
    elif protocol == "timelapse":
        r = run_timelapse(ctrl, guard, save_dir=pos_save_dir, name=pos_label, **params)
        return {"position": pos_label, **r}
    else:
        return {"position": pos_label, "error": f"Unknown protocol '{protocol}'."}


def run_multiposition_acquisition(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    protocol: str,
    save_dir: str,
    position_names: list[str] | None = None,
    positions: list[dict] | None = None,
    name: str = "multipos",
    protocol_params: dict | None = None,
) -> dict:
    """Visit each position and run a per-position protocol.

    Supply either position_names (labels in the MM position list) or positions
    (list of {x_um, y_um, name, z_um?} dicts). Providing both is an error.

    protocol options:
      "snap"       — display-only; does NOT save to disk (returns saved=False).
      "zstack"     — saves a Z-stack at each position to save_dir/<position>.
      "timelapse"  — saves a timelapse at each position to save_dir/<position>.

    To save a single plane per position (equivalent to snapping but with data
    written to disk), use protocol="timelapse" with
    protocol_params={"n_frames": 1, "interval_s": 0}.
    """
    if position_names is not None and positions is not None:
        return {"error": "Provide position_names or positions, not both."}
    if position_names is None and positions is None:
        return {"error": "Provide either position_names or positions."}

    params = protocol_params or {}
    results = []

    if position_names is not None:
        all_positions = {p["name"]: p for p in ctrl.get_positions()}
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

    for pos_label, x_um, y_um, z_um in resolved:
        pos_save_dir = str(Path(save_dir) / pos_label)
        try:
            result = _run_protocol_at(
                ctrl, guard, pos_label, x_um, y_um, z_um, protocol, pos_save_dir, params
            )
            results.append(result)
        except Exception as e:
            results.append({"position": pos_label, "error": str(e)})

    total = len(position_names or positions)
    n_ok = sum(1 for r in results if "error" not in r)
    return {
        "status": f"{n_ok}/{total} positions completed.",
        "results": results,
    }


def run_tile_acquisition(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    rows: int,
    cols: int,
    step_um: float,
    protocol: str,
    save_dir: str,
    name: str = "tile",
    protocol_params: dict | None = None,
) -> dict:
    """Acquire a rows×cols tile grid centered on the current stage position."""
    center_x = ctrl.core.get_x_position()
    center_y = ctrl.core.get_y_position()
    x_start = center_x - (cols - 1) / 2 * step_um
    y_start = center_y - (rows - 1) / 2 * step_um
    positions = [
        {"name": f"r{r}_c{c}", "x_um": x_start + c * step_um, "y_um": y_start + r * step_um}
        for r in range(rows)
        for c in range(cols)
    ]
    return run_multiposition_acquisition(
        ctrl, guard,
        protocol=protocol,
        save_dir=save_dir,
        positions=positions,
        name=name,
        protocol_params=protocol_params,
    )


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

    live = ctrl.studio.live()
    was_live = live.is_live_mode_on()
    if was_live:
        live.set_live_mode_on(False)
    try:
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
    finally:
        if was_live:
            live.set_live_mode_on(True)

    n_ok = sum(1 for r in results if "error" not in r)
    return {
        "status": f"{n_ok}/{len(position_names)} positions completed with autofocus.",
        "results": results,
    }


# --- Hook-based adaptive acquisition ---

def _resolve_hook(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    hook_strategy: str,
    hook_params: dict | None,
    log_path: str | None,
) -> Any:
    """Instantiate a hook by strategy name (pre-coded registry or saved hook).

    Injects ctrl/guard/log_path where the hook constructor accepts them.
    Raises ValueError if the strategy is unknown.
    """
    from microclaw.hooks import PRECODED_HOOK_REGISTRY
    from microclaw.hook_manager import load_hook_class, list_saved_hooks

    params = dict(hook_params or {})
    if log_path:
        params["log_path"] = log_path

    if hook_strategy in PRECODED_HOOK_REGISTRY:
        hook_cls = PRECODED_HOOK_REGISTRY[hook_strategy]
    elif hook_strategy in list_saved_hooks():
        hook_cls = load_hook_class(hook_strategy)
    else:
        raise ValueError(
            f"Unknown hook strategy '{hook_strategy}'. "
            "Run list_hooks() to see available strategies."
        )

    sig = inspect.signature(hook_cls.__init__)
    if "ctrl" in sig.parameters:
        params.setdefault("ctrl", ctrl)
    if "guard" in sig.parameters:
        params.setdefault("guard", guard)

    return hook_cls(**params)


def _adaptive_result(dataset_path: str, log_path: str | None) -> dict:
    result: dict[str, Any] = {
        "status": "Adaptive acquisition complete.",
        "dataset_path": dataset_path,
    }
    if log_path:
        result["log_path"] = log_path
        result["hint"] = "Call read_hook_log to retrieve per-image results."
    return result


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
) -> dict:
    """Run a Z-stack acquisition with a hook strategy for adaptive behaviour.

    hook_strategy: a key from PRECODED_HOOK_REGISTRY or a saved hook name.
    After the acquisition, call read_hook_log(log_path) to retrieve results.
    """
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
    dataset_path = _acquire_with_hooks(save_dir, name, events, hook)
    return _adaptive_result(dataset_path, log_path)


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
) -> dict:
    """Run a timelapse acquisition with a hook strategy for adaptive behaviour.

    hook_strategy: a key from PRECODED_HOOK_REGISTRY or a saved hook name.
    After the acquisition, call read_hook_log(log_path) to retrieve results.
    """
    if channel:
        guard.check_channel(channel)

    try:
        hook = _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path)
    except ValueError as e:
        return {"error": str(e)}

    events = _build_acquisition_events(
        channel=channel, num_time_points=n_frames, time_interval_s=interval_s,
    )
    dataset_path = _acquire_with_hooks(save_dir, name, events, hook)
    return _adaptive_result(dataset_path, log_path)


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


def get_hook_documentation(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    from microclaw.hook_docs import HOOK_REFERENCE
    return {"documentation": HOOK_REFERENCE}


def get_smlm_documentation(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    from microclaw.smlm_docs import SMLM_REFERENCE
    return {"documentation": SMLM_REFERENCE}


def check_emu_installed(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    from microclaw.emu_manager import find_mm_app_dir, find_plugin_jars, _emu_config_path

    mm_dir = find_mm_app_dir()
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
    """Persist a knowledge base entry. Call only after user confirmation."""
    from microclaw.knowledge_manager import save_entry
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


def get_emu_configuration(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    mm_app_dir: str | None = None,
) -> dict:
    from microclaw.emu_manager import find_mm_app_dir, save_mm_app_dir, read_emu_config, _candidate_mm_dirs

    if mm_app_dir is not None:
        save_mm_app_dir(mm_app_dir)
        resolved = mm_app_dir
    else:
        found = find_mm_app_dir()
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

    config = read_emu_config(resolved)
    config["mm_app_dir"] = resolved
    return config


# --- Tool Registry ---

TOOL_REGISTRY = {
    "snap_image": snap_image,
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
    "set_channel": set_channel,
    "get_available_channels": get_available_channels,
    "set_device_property": set_device_property,
    "get_device_property": get_device_property,
    "list_devices": list_devices,
    "list_device_properties": list_device_properties,
    "get_device_property_info": get_device_property_info,
    "get_full_device_state": get_full_device_state,
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
    "run_tile_acquisition": run_tile_acquisition,
    "run_multiposition_with_autofocus": run_multiposition_with_autofocus,
    "run_adaptive_zstack": run_adaptive_zstack,
    "run_adaptive_timelapse": run_adaptive_timelapse,
    "read_hook_log": read_hook_log,
    "generate_and_save_hook": generate_and_save_hook,
    "read_hook_from_file": read_hook_from_file,
    "list_hooks": list_hooks,
    "get_hook_documentation": get_hook_documentation,
    "get_smlm_documentation": get_smlm_documentation,
    "check_emu_installed": check_emu_installed,
    "get_htsmlm_documentation": get_htsmlm_documentation,
    "get_emu_configuration": get_emu_configuration,
    "save_knowledge": save_knowledge,
    "get_knowledge": get_knowledge,
    "delete_knowledge": delete_knowledge,
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
