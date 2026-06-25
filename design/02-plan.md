# Microclaw: AI Agent for Micro-Manager

**Date:** 2026-05-15  
**Stack:** Python · Anthropic API (tool use) · pycro-manager · Micro-Manager 2.0

---

## 1. Goals and Constraints

- Biologist controls Micro-Manager through natural language; the full MM GUI responds in real time.
- Agent connects to an already-running MM application via pycro-manager (ZMQ); the biologist opens MM as normal, then opens the agent chat.
- Hardware safety constraints are user-defined, loaded at startup, and **cannot be overridden by the AI** — they are enforced as a hard gate before every relevant tool call.
- Headless mode (no GUI, for unattended runs) uses identical code; only the startup differs.
- Agent is tested against the Micro-Manager Demo configuration so no real hardware is required.

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Biologist terminal / chat UI                           │
│                                                         │
│  User: "run a 20-slice z-stack, save to /data/today"    │
└───────────────────────┬─────────────────────────────────┘
                        │  natural language
                        ▼
┌─────────────────────────────────────────────────────────┐
│  agent.py  —  AgentLoop                                 │
│  • builds messages[]                                    │
│  • calls Anthropic API (tool use, prompt caching)       │
│  • dispatches tool calls → ToolRegistry                 │
│  • collects tool_result blocks → back to Anthropic      │
│  • loops until stop_reason == "end_turn"                │
└───────────────┬─────────────────────────────────────────┘
                │  tool call
                ▼
┌─────────────────────────────────────────────────────────┐
│  safety.py  —  SafetyGuard                              │
│  • reads SafetyConstraints (from YAML at startup)       │
│  • rejects any tool call that violates a constraint     │
│  • returns SafetyViolation with the breached rule       │
└───────────────┬─────────────────────────────────────────┘
                │  approved call
                ▼
┌─────────────────────────────────────────────────────────┐
│  tools.py  —  ToolRegistry                              │
│  • one Python function per tool                         │
│  • calls MicroscopeController (thin wrapper)            │
│  • catches MM exceptions, formats as error dicts        │
└───────────────┬─────────────────────────────────────────┘
                │  pycro-manager calls
                ▼
┌─────────────────────────────────────────────────────────┐
│  controller.py  —  MicroscopeController                 │
│  • holds Core() and Studio() objects                    │
│  • manages connection / reconnection                    │
│  • exposes typed Python methods                         │
└───────────────┬─────────────────────────────────────────┘
                │  ZMQ socket (port 4827)
                ▼
┌─────────────────────────────────────────────────────────┐
│  Java Micro-Manager (MMStudio)  —  running with GUI     │
│  ZMQServer  →  MMCoreJ  →  C++ MMCore  →  device adapters │
│  Biologist watches GUI update in real time              │
└─────────────────────────────────────────────────────────┘
```

---

## 3. File Structure

```
microclaw/
├── microclaw/
│   ├── __init__.py
│   ├── agent.py           # AgentLoop: Anthropic API calls, tool dispatch
│   ├── controller.py      # MicroscopeController: thin pycro-manager wrapper
│   ├── tools.py           # Tool function implementations
│   ├── tools_schema.py    # Anthropic tool definitions (JSON schema)
│   ├── safety.py          # SafetyConstraints, SafetyGuard
│   ├── errors.py          # Error types
│   └── config.py          # Load safety_config.yaml, agent settings
├── tests/
│   ├── conftest.py        # pytest fixtures: headless Demo config, mock controller
│   ├── test_tools.py      # Unit tests for each tool (mock controller)
│   ├── test_safety.py     # Safety constraint tests
│   ├── test_agent.py      # Integration tests: prompt → tool call sequences
│   └── fixtures/
│       └── safety_config.yaml
├── safety_config.yaml     # Default safety config (checked into repo)
├── pyproject.toml
└── README.md
```

---

## 4. Safety Constraints

Safety constraints are the only part of the system that the AI cannot influence. They are loaded once at startup from a YAML file and enforced synchronously before any tool that moves hardware or changes properties executes.

### 4.1 safety_config.yaml

```yaml
# All values are in micrometers unless noted.
# Omit a field to leave it unconstrained.

stage:
  x_min: -5000.0
  x_max:  5000.0
  y_min: -5000.0
  y_max:  5000.0
  z_min:  0.0
  z_max:  200.0

camera:
  max_exposure_ms: 5000.0

channels:
  # If present, only these presets may be set via set_channel.
  # Remove the list entirely to allow all channels.
  allowed: [DAPI, FITC, TRITC, Brightfield]

# Device properties the AI may never touch, regardless of prompt.
forbidden_properties:
  - device: Core
    property: Initialize
```

### 4.2 `safety.py`

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import yaml


class SafetyViolation(Exception):
    """Raised when a tool call would violate a user-defined safety constraint."""


@dataclass
class StageConstraints:
    x_min: Optional[float] = None
    x_max: Optional[float] = None
    y_min: Optional[float] = None
    y_max: Optional[float] = None
    z_min: Optional[float] = None
    z_max: Optional[float] = None


@dataclass
class CameraConstraints:
    max_exposure_ms: Optional[float] = None


@dataclass
class ForbiddenProperty:
    device: str
    property: str


@dataclass
class SafetyConstraints:
    stage: StageConstraints = field(default_factory=StageConstraints)
    camera: CameraConstraints = field(default_factory=CameraConstraints)
    allowed_channels: Optional[list[str]] = None  # None means all allowed
    forbidden_properties: list[ForbiddenProperty] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str) -> SafetyConstraints:
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}

        stage_cfg = cfg.get("stage", {})
        camera_cfg = cfg.get("camera", {})
        channels_cfg = cfg.get("channels", {})
        forbidden = [
            ForbiddenProperty(**p)
            for p in cfg.get("forbidden_properties", [])
        ]
        return cls(
            stage=StageConstraints(**stage_cfg),
            camera=CameraConstraints(**camera_cfg),
            allowed_channels=channels_cfg.get("allowed"),
            forbidden_properties=forbidden,
        )


class SafetyGuard:
    def __init__(self, constraints: SafetyConstraints):
        self._c = constraints

    def check_xy(self, x: float, y: float) -> None:
        s = self._c.stage
        if s.x_min is not None and x < s.x_min:
            raise SafetyViolation(
                f"X={x:.1f} µm is below the minimum allowed ({s.x_min:.1f} µm)."
            )
        if s.x_max is not None and x > s.x_max:
            raise SafetyViolation(
                f"X={x:.1f} µm exceeds the maximum allowed ({s.x_max:.1f} µm)."
            )
        if s.y_min is not None and y < s.y_min:
            raise SafetyViolation(
                f"Y={y:.1f} µm is below the minimum allowed ({s.y_min:.1f} µm)."
            )
        if s.y_max is not None and y > s.y_max:
            raise SafetyViolation(
                f"Y={y:.1f} µm exceeds the maximum allowed ({s.y_max:.1f} µm)."
            )

    def check_z(self, z: float) -> None:
        s = self._c.stage
        if s.z_min is not None and z < s.z_min:
            raise SafetyViolation(
                f"Z={z:.1f} µm is below the minimum allowed ({s.z_min:.1f} µm)."
            )
        if s.z_max is not None and z > s.z_max:
            raise SafetyViolation(
                f"Z={z:.1f} µm exceeds the maximum allowed ({s.z_max:.1f} µm)."
            )

    def check_exposure(self, ms: float) -> None:
        limit = self._c.camera.max_exposure_ms
        if limit is not None and ms > limit:
            raise SafetyViolation(
                f"Exposure {ms:.0f} ms exceeds the maximum allowed ({limit:.0f} ms)."
            )

    def check_channel(self, preset: str) -> None:
        allowed = self._c.allowed_channels
        if allowed is not None and preset not in allowed:
            raise SafetyViolation(
                f"Channel '{preset}' is not in the allowed list: {allowed}."
            )

    def check_property(self, device: str, prop: str) -> None:
        for fp in self._c.forbidden_properties:
            if fp.device == device and fp.property == prop:
                raise SafetyViolation(
                    f"Property '{device}.{prop}' is forbidden by safety config."
                )
```

**Enforcement rule:** `SafetyGuard` is instantiated once at startup with the loaded `SafetyConstraints`. Every tool that moves hardware calls the appropriate `check_*` method before touching the microscope. A `SafetyViolation` surfaces as an error tool result, which Claude relays to the user in plain language.

---

## 5. Tool Definitions

### 5.1 Complete Tool List

| Tool | MM call(s) | Safety check |
|---|---|---|
| `snap_image` | `studio.live().snap(True)` | — |
| `start_live_view` | `studio.live().set_live_mode_on(True)` | — |
| `stop_live_view` | `studio.live().set_live_mode_on(False)` | — |
| `set_exposure` | `core.set_exposure(ms)` | `check_exposure` |
| `get_exposure` | `core.get_exposure()` | — |
| `get_xy_position` | `core.get_x_position(), core.get_y_position()` | — |
| `move_stage_xy` | `core.set_xy_position()` / `set_relative_xy_position()` | `check_xy` (resolves absolute before check) |
| `get_z_position` | `core.get_position()` | — |
| `move_stage_z` | `core.set_position()` / `set_relative_position()` | `check_z` (resolves absolute before check) |
| `set_channel` | `core.set_config("Channel", preset)` | `check_channel` |
| `get_available_channels` | `core.get_available_configs("Channel")` | — |
| `set_device_property` | `core.set_property(device, prop, value)` | `check_property` |
| `get_device_property` | `core.get_property(device, prop)` | — |
| `list_devices` | `core.get_loaded_devices()` | — |
| `get_system_state` | composite: position + channel + exposure | — |
| `run_zstack` | `Acquisition` + `multi_d_acquisition_events` | `check_z` on start and end |
| `run_timelapse` | `Acquisition` + time events | — |
| `run_mda_acquisition` | `Acquisition` + full MDA events | `check_xy`, `check_z`, `check_channel` per event |
| `export_dataset_as_tiff` | `Dataset` + `tifffile.imwrite` | — |

### 5.2 Tool Schema (`tools_schema.py`, excerpt)

The full list follows this pattern. Tools are passed to Claude as the `tools` parameter with `cache_control` on the final entry to cache the definitions across turns.

```python
# tools_schema.py
from typing import Any

TOOLS: list[dict[str, Any]] = [
    {
        "name": "snap_image",
        "description": (
            "Snap a single image and display it in the Micro-Manager snap/live window. "
            "Use this for a quick one-shot image capture."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "start_live_view",
        "description": "Start the Micro-Manager camera live preview stream.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "stop_live_view",
        "description": "Stop the live camera preview stream.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_exposure",
        "description": "Set the camera exposure time in milliseconds.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ms": {
                    "type": "number",
                    "description": "Exposure time in milliseconds (must be positive).",
                }
            },
            "required": ["ms"],
        },
    },
    {
        "name": "get_exposure",
        "description": "Get the current camera exposure time in milliseconds.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_xy_position",
        "description": "Get the current XY stage position in micrometers.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "move_stage_xy",
        "description": (
            "Move the XY stage. With absolute=true (default), move to the given "
            "coordinates. With absolute=false, move relative to current position."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x_um": {"type": "number", "description": "X in micrometers."},
                "y_um": {"type": "number", "description": "Y in micrometers."},
                "absolute": {
                    "type": "boolean",
                    "description": "True for absolute coordinates, False for relative.",
                    "default": True,
                },
            },
            "required": ["x_um", "y_um"],
        },
    },
    {
        "name": "get_z_position",
        "description": "Get the current Z (focus) stage position in micrometers.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "move_stage_z",
        "description": (
            "Move the Z (focus) stage. With absolute=true (default), move to the "
            "given position. With absolute=false, move relative to current position."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "z_um": {"type": "number", "description": "Z in micrometers."},
                "absolute": {
                    "type": "boolean",
                    "description": "True for absolute, False for relative.",
                    "default": True,
                },
            },
            "required": ["z_um"],
        },
    },
    {
        "name": "set_channel",
        "description": (
            "Set the imaging channel by applying a Micro-Manager hardware preset "
            "from the 'Channel' config group. Use get_available_channels to list "
            "valid preset names."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "preset": {
                    "type": "string",
                    "description": "Name of the channel preset (e.g. 'DAPI', 'FITC').",
                }
            },
            "required": ["preset"],
        },
    },
    {
        "name": "get_available_channels",
        "description": "List the available channel preset names in the 'Channel' config group.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_device_property",
        "description": (
            "Set a Micro-Manager device property. Use list_devices to find device names "
            "and get_device_property to inspect current values. Avoid calling this for "
            "high-level operations that have dedicated tools (exposure, stage, channel)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Device name."},
                "property": {"type": "string", "description": "Property name."},
                "value": {"type": "string", "description": "New value (always a string)."},
            },
            "required": ["device", "property", "value"],
        },
    },
    {
        "name": "get_device_property",
        "description": "Get the current value of a Micro-Manager device property.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {"type": "string"},
                "property": {"type": "string"},
            },
            "required": ["device", "property"],
        },
    },
    {
        "name": "list_devices",
        "description": "List all devices currently loaded in Micro-Manager.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_system_state",
        "description": (
            "Return a summary of the current microscope state: stage positions, "
            "active channel, exposure time, and whether live view is running. "
            "Call this first when you need context before executing a protocol."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "run_zstack",
        "description": (
            "Run a Z-stack acquisition. Moves Z from z_start to z_end in z_step "
            "increments, capturing one image per step. Saves the dataset to save_dir."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "z_start_um": {"type": "number", "description": "Start Z in µm."},
                "z_end_um": {"type": "number", "description": "End Z in µm."},
                "z_step_um": {
                    "type": "number",
                    "description": "Step size in µm (positive; direction is inferred).",
                },
                "channel": {
                    "type": "string",
                    "description": "Channel preset name. Uses current channel if omitted.",
                },
                "exposure_ms": {
                    "type": "number",
                    "description": "Exposure in ms. Uses current exposure if omitted.",
                },
                "save_dir": {
                    "type": "string",
                    "description": "Directory to save the dataset.",
                },
                "name": {
                    "type": "string",
                    "description": "Dataset name. Defaults to 'zstack'.",
                    "default": "zstack",
                },
            },
            "required": ["z_start_um", "z_end_um", "z_step_um", "save_dir"],
        },
    },
    {
        "name": "run_timelapse",
        "description": "Run a timelapse acquisition.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n_frames": {"type": "integer", "description": "Number of frames."},
                "interval_s": {"type": "number", "description": "Interval between frames in seconds."},
                "channel": {"type": "string", "description": "Channel preset (optional)."},
                "exposure_ms": {"type": "number", "description": "Exposure in ms (optional)."},
                "save_dir": {"type": "string"},
                "name": {"type": "string", "default": "timelapse"},
            },
            "required": ["n_frames", "interval_s", "save_dir"],
        },
    },
    {
        "name": "export_dataset_as_tiff",
        "description": (
            "Export a previously acquired pycro-manager dataset as a standard "
            "multi-page TIFF file suitable for ImageJ or FIJI."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_path": {
                    "type": "string",
                    "description": "Path to the pycro-manager dataset directory.",
                },
                "output_path": {
                    "type": "string",
                    "description": "Output .tiff file path.",
                },
            },
            "required": ["dataset_path", "output_path"],
        },
    },
]

# Add cache_control on the last tool so the entire tool list is cached.
TOOLS_CACHED = [*TOOLS[:-1], {**TOOLS[-1], "cache_control": {"type": "ephemeral"}}]
```

---

## 6. Tool Implementations

### 6.1 `controller.py`

```python
# controller.py
from __future__ import annotations
from pycromanager import Core, Studio


class MicroscopeController:
    """Thin wrapper around pycro-manager Core and Studio.
    Holds the ZMQ connection; all tool functions go through here."""

    def __init__(self, port: int = 4827):
        self._core = Core(port=port)
        self._studio = Studio(port=port)

    @property
    def core(self) -> Core:
        return self._core

    @property
    def studio(self) -> Studio:
        return self._studio

    def is_connected(self) -> bool:
        try:
            self._core.get_version_info()
            return True
        except Exception:
            return False
```

### 6.2 `tools.py`

```python
# tools.py
from __future__ import annotations
import time
from pathlib import Path
from typing import Any

import numpy as np
import tifffile
from pycromanager import Acquisition, multi_d_acquisition_events, Dataset

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

    # Collect all images. For a z-stack: axis 'z'.
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
```

### 6.3 Tool Registry

```python
# tools.py (continued)

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
```

---

## 7. Error Handling

### 7.1 Error taxonomy

| Source | Exception | Meaning |
|---|---|---|
| Safety layer | `SafetyViolation` | Call would violate a user constraint |
| pycro-manager / MM | `Exception` from Java | Device not found, stage at limit, hardware busy, ZMQ timeout |
| Bad parameters | `ValueError`, `TypeError` | Agent called a tool with invalid arguments |
| Connection lost | `ConnectionError` | MM not running, ZMQ server disabled |

### 7.2 Tool execution wrapper

```python
# agent.py

import json
import traceback
from microclaw.safety import SafetyViolation


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
        # Surface the MM error class name so Claude can diagnose it.
        return json.dumps({
            "error": f"{type(e).__name__}: {e}",
            "hint": (
                "This may be a hardware error (device busy, stage at limit, "
                "device not found) or a connection problem."
            ),
        })
```

### 7.3 How errors reach the user

When a tool returns an error JSON, Claude receives it as a `tool_result` content block. The system prompt instructs Claude to:
1. Describe the error in plain language.
2. Suggest a corrective action (e.g., "The stage limit was reached — try a smaller Z range.").
3. Not retry the failed call with the same parameters without user approval.

---

## 8. Agent Loop

### 8.1 System Prompt

```python
SYSTEM_PROMPT = """You are Microclaw, an AI assistant that controls a Micro-Manager fluorescence microscope.

The biologist is watching the Micro-Manager GUI. Every tool call you make is immediately reflected there: images appear in the viewer, the stage position display updates, acquisitions play out in the acquisition window.

Guidelines:
- Before executing a multi-step protocol, call get_system_state to orient yourself.
- If the user's request is ambiguous (e.g. "run a z-stack" without specifying range), ask one focused clarifying question rather than guessing.
- After each tool call, briefly describe what happened in plain language (e.g., "I moved the stage to Z=50 µm").
- If a tool returns an error, explain it plainly and suggest what to try next. Never retry with the same out-of-range parameters.
- If a safety constraint blocks an action, clearly tell the user which limit was hit and what the allowed range is.
- Available acquisition outputs are pycro-manager datasets (NDTiff). Use export_dataset_as_tiff to convert to standard TIFF when the user requests it.
- Never call set_device_property for core operations that have dedicated tools (stage, channel, exposure).
"""
```

### 8.2 Agent loop (`agent.py`)

```python
# agent.py
from __future__ import annotations
import json
from typing import Any

import anthropic

from microclaw.controller import MicroscopeController
from microclaw.safety import SafetyConstraints, SafetyGuard
from microclaw.tools import TOOL_REGISTRY, execute_tool
from microclaw.tools_schema import TOOLS_CACHED

client = anthropic.Anthropic()
MODEL = "claude-opus-4-7"


def run_agent(
    user_message: str,
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    history: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    """
    Run one user turn through the agent loop.
    Returns (assistant_text_reply, updated_history).
    history is the full conversation; pass it on repeated calls for multi-turn.
    """
    messages: list[dict[str, Any]] = list(history or [])
    messages.append({"role": "user", "content": user_message})

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},  # cache system prompt
                }
            ],
            tools=TOOLS_CACHED,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            text = next(
                (b.text for b in response.content if hasattr(b, "text")), ""
            )
            return text, messages

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result_json = execute_tool(
                        block.name, block.input, ctrl, guard
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_json,
                        }
                    )
            messages.append({"role": "user", "content": tool_results})
            # Loop: send tool results back to Claude.
            continue

        # Unexpected stop reason — surface it.
        return f"[Unexpected stop reason: {response.stop_reason}]", messages
```

### 8.3 Multi-step acquisition flow (example walkthrough)

**User:** `"run a z-stack from −10 to +10 µm in 2 µm steps, then export as TIFF to /data/today.tiff"`

```
Turn 1 — User message arrives.
  Claude → calls get_system_state (orients itself)
  Tool result → {"z_um": 45.0, "x_um": 0, "y_um": 0, "exposure_ms": 50.0, ...}

Turn 2 — Claude has context.
  Claude → calls run_zstack(z_start_um=35.0, z_end_um=55.0, z_step_um=2.0,
                            save_dir="/data/today", name="zstack")
  Note: absolute positions resolved as current_z ± 10 µm.
  SafetyGuard.check_z(35.0) → OK
  SafetyGuard.check_z(55.0) → OK
  Acquisition runs. MM viewer opens and fills with 11 slices.
  Tool result → {"status": "Z-stack complete.", "dataset_path": "/data/today/zstack"}

Turn 3 — Claude has dataset path.
  Claude → calls export_dataset_as_tiff(
               dataset_path="/data/today/zstack",
               output_path="/data/today.tiff")
  Tool result → {"status": "Export complete.", "output_path": "/data/today.tiff"}

Turn 4 — No more tool calls.
  Claude → "The z-stack ran successfully (11 slices from Z=35 to Z=55 µm) and
            has been exported to /data/today.tiff."
  stop_reason = "end_turn". Return reply.
```

### 8.4 Entry point (`__main__.py` / CLI)

```python
# microclaw/__main__.py
import sys
from microclaw.agent import run_agent
from microclaw.controller import MicroscopeController
from microclaw.config import load_safety_config
from microclaw.safety import SafetyGuard


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Microclaw: AI agent for Micro-Manager")
    parser.add_argument("--safety-config", default="safety_config.yaml")
    parser.add_argument("--port", type=int, default=4827)
    args = parser.parse_args()

    constraints = load_safety_config(args.safety_config)
    guard = SafetyGuard(constraints)

    print("Connecting to Micro-Manager...")
    ctrl = MicroscopeController(port=args.port)
    if not ctrl.is_connected():
        sys.exit("Could not connect to Micro-Manager. Is the ZMQ server enabled in Tools → Options?")
    print("Connected. Type your instructions (Ctrl-C to exit).\n")

    history = []
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break
        if not user_input:
            continue
        reply, history = run_agent(user_input, ctrl, guard, history)
        print(f"\nMicroclaw: {reply}\n")


if __name__ == "__main__":
    main()
```

---

## 9. Headless Mode

For unattended runs on verified protocols, pass `--headless` and a config file:

```python
# config.py (headless launcher)
from pycromanager import start_headless, Core, Studio

def launch_headless(mm_app_path: str, config_file: str, port: int = 4827):
    start_headless(
        mm_app_path=mm_app_path,
        config_file=config_file,
        port=port,
    )
    # After start_headless, Core() / Studio() connect exactly as in GUI mode.
    # No changes to tool functions or agent loop required.
```

The tool functions and agent loop are identical in both modes.

---

## 10. Testing Plan

### 10.1 Demo Configuration

All tests run against Micro-Manager's built-in Demo configuration (`MMConfig_demo.cfg`). It includes:

| Device | Type |
|---|---|
| `DCam` | Simulated camera |
| `DXYStage` | Simulated XY stage |
| `DStage` | Simulated Z stage |
| `Dichroic` | Simulated dichroic wheel |
| `Emission` | Simulated emission filter |
| `Excitation` | Simulated excitation filter |

Channel presets (`DAPI`, `FITC`, `Cy5`) are configured in the Demo config.

### 10.2 Pytest fixtures (`tests/conftest.py`)

```python
# tests/conftest.py
import pytest
from unittest.mock import MagicMock, patch
from microclaw.controller import MicroscopeController
from microclaw.safety import SafetyConstraints, SafetyGuard, StageConstraints, CameraConstraints


# ── Mock controller (no MM required) ───────────────────────────────────────

@pytest.fixture
def mock_core():
    core = MagicMock()
    core.get_x_position.return_value = 0.0
    core.get_y_position.return_value = 0.0
    core.get_position.return_value = 50.0
    core.get_exposure.return_value = 100.0
    core.get_xy_stage_device.return_value = "DXYStage"
    core.get_focus_device.return_value = "DStage"
    core.get_available_configs.return_value = ["DAPI", "FITC", "Cy5"]
    core.get_loaded_devices.return_value = ["DCam", "DXYStage", "DStage"]
    return core


@pytest.fixture
def mock_studio(mock_core):
    studio = MagicMock()
    studio.live().is_live_mode_on.return_value = False
    return studio


@pytest.fixture
def mock_ctrl(mock_core, mock_studio):
    ctrl = MagicMock(spec=MicroscopeController)
    ctrl.core = mock_core
    ctrl.studio = mock_studio
    return ctrl


# ── Safety fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def default_guard():
    constraints = SafetyConstraints(
        stage=StageConstraints(x_min=-1000, x_max=1000, y_min=-1000, y_max=1000,
                               z_min=0, z_max=200),
        camera=CameraConstraints(max_exposure_ms=2000),
        allowed_channels=["DAPI", "FITC", "Cy5"],
    )
    return SafetyGuard(constraints)


@pytest.fixture
def unconstrained_guard():
    return SafetyGuard(SafetyConstraints())


# ── Headless MM (requires real MM installation) ──────────────────────────────

@pytest.fixture(scope="session")
def headless_mm(mm_app_path, demo_config_path):
    """Launch MM in headless mode with Demo config. Requires --mm-path and --demo-config."""
    from pycromanager import start_headless
    start_headless(mm_app_path=mm_app_path, config_file=demo_config_path)
    ctrl = MicroscopeController()
    yield ctrl
```

### 10.3 Unit tests — each tool (`tests/test_tools.py`)

```python
# tests/test_tools.py
import pytest
from microclaw.tools import (
    set_exposure, move_stage_z, move_stage_xy, set_channel,
    set_device_property, run_zstack,
)
from microclaw.safety import SafetyViolation


class TestSetExposure:
    def test_valid_exposure(self, mock_ctrl, default_guard):
        result = set_exposure(mock_ctrl, default_guard, ms=100.0)
        mock_ctrl.core.set_exposure.assert_called_once_with(100.0)
        assert result["status"]

    def test_exceeds_max(self, mock_ctrl, default_guard):
        with pytest.raises(SafetyViolation, match="2000"):
            set_exposure(mock_ctrl, default_guard, ms=3000.0)


class TestMoveStageZ:
    def test_absolute_in_range(self, mock_ctrl, default_guard):
        result = move_stage_z(mock_ctrl, default_guard, z_um=100.0, absolute=True)
        mock_ctrl.core.set_position.assert_called_once_with(100.0)

    def test_absolute_below_min(self, mock_ctrl, default_guard):
        with pytest.raises(SafetyViolation, match="0"):
            move_stage_z(mock_ctrl, default_guard, z_um=-5.0, absolute=True)

    def test_absolute_above_max(self, mock_ctrl, default_guard):
        with pytest.raises(SafetyViolation, match="200"):
            move_stage_z(mock_ctrl, default_guard, z_um=250.0, absolute=True)

    def test_relative_resolves_to_absolute_before_check(self, mock_ctrl, default_guard):
        # Current Z is 50.0 (from fixture). Relative +200 → target 250 → exceeds max.
        with pytest.raises(SafetyViolation):
            move_stage_z(mock_ctrl, default_guard, z_um=200.0, absolute=False)

    def test_relative_in_range(self, mock_ctrl, default_guard):
        result = move_stage_z(mock_ctrl, default_guard, z_um=10.0, absolute=False)
        mock_ctrl.core.set_relative_position.assert_called_once_with(10.0)


class TestMoveStageXY:
    def test_in_range(self, mock_ctrl, default_guard):
        move_stage_xy(mock_ctrl, default_guard, x_um=100.0, y_um=-50.0)
        mock_ctrl.core.set_xy_position.assert_called_once_with(100.0, -50.0)

    def test_x_exceeds_max(self, mock_ctrl, default_guard):
        with pytest.raises(SafetyViolation, match="1000"):
            move_stage_xy(mock_ctrl, default_guard, x_um=1500.0, y_um=0.0)

    def test_relative_resolves_absolute_first(self, mock_ctrl, default_guard):
        # Current X=0, Y=0. Relative (2000, 0) → absolute (2000, 0) → x exceeds max.
        with pytest.raises(SafetyViolation):
            move_stage_xy(mock_ctrl, default_guard, x_um=2000.0, y_um=0.0, absolute=False)


class TestSetChannel:
    def test_allowed_channel(self, mock_ctrl, default_guard):
        set_channel(mock_ctrl, default_guard, preset="DAPI")
        mock_ctrl.core.set_config.assert_called_once_with("Channel", "DAPI")

    def test_forbidden_channel(self, mock_ctrl, default_guard):
        with pytest.raises(SafetyViolation, match="allowed list"):
            set_channel(mock_ctrl, default_guard, preset="GFP")

    def test_any_channel_when_unconstrained(self, mock_ctrl, unconstrained_guard):
        set_channel(mock_ctrl, unconstrained_guard, preset="GFP")  # no error


class TestSetDeviceProperty:
    def test_forbidden_property(self, mock_ctrl):
        from microclaw.safety import SafetyConstraints, SafetyGuard, ForbiddenProperty
        constraints = SafetyConstraints(
            forbidden_properties=[ForbiddenProperty(device="Core", property="Initialize")]
        )
        guard = SafetyGuard(constraints)
        with pytest.raises(SafetyViolation, match="forbidden"):
            set_device_property(mock_ctrl, guard, device="Core", property="Initialize", value="1")

    def test_allowed_property(self, mock_ctrl, unconstrained_guard):
        set_device_property(mock_ctrl, unconstrained_guard,
                            device="DCam", property="Gain", value="0")
        mock_ctrl.core.set_property.assert_called_once_with("DCam", "Gain", "0")
```

### 10.4 Safety boundary tests (`tests/test_safety.py`)

```python
# tests/test_safety.py
import pytest
from microclaw.safety import SafetyConstraints, SafetyGuard, SafetyViolation, StageConstraints


@pytest.fixture
def guard():
    c = SafetyConstraints(
        stage=StageConstraints(x_min=-500, x_max=500, y_min=-500, y_max=500,
                               z_min=10, z_max=150),
    )
    return SafetyGuard(c)


class TestBoundaryExact:
    """Values at exactly the boundary must pass; one unit outside must fail."""

    def test_x_at_max(self, guard):
        guard.check_xy(500.0, 0.0)  # exactly at limit — should not raise

    def test_x_beyond_max(self, guard):
        with pytest.raises(SafetyViolation):
            guard.check_xy(500.001, 0.0)

    def test_z_at_min(self, guard):
        guard.check_z(10.0)  # exactly at limit

    def test_z_below_min(self, guard):
        with pytest.raises(SafetyViolation):
            guard.check_z(9.999)

    def test_z_at_max(self, guard):
        guard.check_z(150.0)

    def test_z_beyond_max(self, guard):
        with pytest.raises(SafetyViolation):
            guard.check_z(150.001)


class TestNoConstraints:
    """Unconstrained guard should allow anything."""

    def test_extreme_z(self):
        guard = SafetyGuard(SafetyConstraints())
        guard.check_z(1_000_000.0)  # no exception

    def test_all_channels(self):
        guard = SafetyGuard(SafetyConstraints())
        guard.check_channel("AnythingAtAll")


class TestFromYaml:
    def test_loads_yaml(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "stage:\n  z_min: 5.0\n  z_max: 100.0\n"
            "camera:\n  max_exposure_ms: 500\n"
        )
        constraints = SafetyConstraints.from_yaml(str(cfg))
        guard = SafetyGuard(constraints)
        with pytest.raises(SafetyViolation):
            guard.check_z(0.0)
        with pytest.raises(SafetyViolation):
            guard.check_exposure(600.0)
```

### 10.5 Integration tests — multi-step prompts (`tests/test_agent.py`)

These tests check that given a specific user prompt, the agent calls tools in the expected sequence. They use a mock Anthropic client to avoid real API calls while still exercising the dispatch logic.

```python
# tests/test_agent.py
"""
Prompt-level tests: verify that a given user input produces the expected
sequence of tool calls. Uses a mock Anthropic client so no real API key needed.
The mock pre-scripts the sequence of Claude responses (tool_use blocks).
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from microclaw.agent import run_agent
from microclaw.safety import SafetyConstraints, SafetyGuard


def make_mock_client(scripted_responses: list):
    """Return a mock client whose messages.create() yields scripted_responses in order."""
    client = MagicMock()
    client.messages.create.side_effect = scripted_responses
    return client


def tool_use_response(tool_name: str, tool_input: dict, call_id: str = "call_1"):
    """Build a fake Claude tool_use response block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    block.id = call_id
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    return response


def text_response(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


@pytest.fixture
def guard():
    return SafetyGuard(SafetyConstraints())  # unconstrained for these tests


@pytest.fixture
def ctrl(mock_ctrl):
    return mock_ctrl


class TestSnapAndShowPrompt:
    """'Take a picture' → snap_image → text reply."""

    def test_snap_image_called(self, ctrl, guard):
        scripted = [
            tool_use_response("snap_image", {}),
            text_response("I snapped an image — it's now showing in the MM viewer."),
        ]
        with patch("microclaw.agent.client", make_mock_client(scripted)):
            reply, _ = run_agent("Take a picture", ctrl, guard)
        assert "snap" in reply.lower() or "image" in reply.lower()


class TestZStackThenExportPrompt:
    """
    'Run a 5-slice z-stack from 40 to 60 µm, then export to /tmp/out.tiff'
    Expected tool call sequence: run_zstack → export_dataset_as_tiff
    """

    def test_tool_sequence(self, ctrl, guard):
        scripted = [
            # First: Claude calls get_system_state to orient
            tool_use_response("get_system_state", {}, call_id="c1"),
            # Second: Claude calls run_zstack
            tool_use_response(
                "run_zstack",
                {"z_start_um": 40.0, "z_end_um": 60.0, "z_step_um": 5.0,
                 "save_dir": "/tmp", "name": "zstack"},
                call_id="c2",
            ),
            # Third: Claude calls export
            tool_use_response(
                "export_dataset_as_tiff",
                {"dataset_path": "/tmp/zstack", "output_path": "/tmp/out.tiff"},
                call_id="c3",
            ),
            text_response("Z-stack done and exported to /tmp/out.tiff."),
        ]
        with patch("microclaw.agent.client", make_mock_client(scripted)):
            reply, _ = run_agent(
                "Run a 5-slice z-stack from 40 to 60 µm, then export to /tmp/out.tiff",
                ctrl,
                guard,
            )
        assert "export" in reply.lower() or "tiff" in reply.lower()


class TestSafetyBlockedInLoop:
    """
    When a safety violation is returned as a tool_result error, Claude
    should inform the user rather than retrying.
    """

    def test_safety_error_surfaced(self, mock_ctrl, tmp_path):
        from microclaw.safety import SafetyConstraints, SafetyGuard, StageConstraints
        tight_guard = SafetyGuard(SafetyConstraints(
            stage=StageConstraints(z_min=0, z_max=100)
        ))
        scripted = [
            # Claude attempts to move Z to 500 (violates constraint)
            tool_use_response("move_stage_z", {"z_um": 500.0}, call_id="c1"),
            # After receiving the error, Claude replies with explanation
            text_response(
                "The stage cannot move to 500 µm because the maximum allowed Z is 100 µm."
            ),
        ]
        with patch("microclaw.agent.client", make_mock_client(scripted)):
            reply, _ = run_agent("Move Z to 500 µm", mock_ctrl, tight_guard)
        assert "100" in reply or "maximum" in reply.lower() or "limit" in reply.lower()
```

### 10.6 Prompt-level expected tool call table

The following table documents expected tool call sequences for representative inputs. These serve as specification for the integration tests and for evaluating the agent with a real API key.

| User prompt | Expected tool call sequence | Notes |
|---|---|---|
| `"Take a picture"` | `snap_image` | Single-step |
| `"Start live view"` | `start_live_view` | Single-step |
| `"Set exposure to 200ms"` | `set_exposure(ms=200)` | Single-step |
| `"What channel am I using?"` | `get_system_state` or `get_device_property(Camera, Channel)` | State query |
| `"Move stage right 50 microns"` | `move_stage_xy(x_um=50, y_um=0, absolute=False)` | Relative move |
| `"Run a z-stack from −5 to +5 µm in 1 µm steps"` | `get_system_state` → `run_zstack` | Needs current Z to compute absolutes |
| `"Run a z-stack then export as TIFF to /tmp/out.tiff"` | `get_system_state` → `run_zstack` → `export_dataset_as_tiff` | Multi-step |
| `"Take a 10-frame timelapse every 2 seconds"` | `run_timelapse(n_frames=10, interval_s=2, save_dir=…)` | Will prompt for save_dir |
| `"What devices are loaded?"` | `list_devices` | Single-step |
| `"Move Z to 1000 µm"` (with z_max=200) | `move_stage_z(z_um=1000)` → safety error → text reply | Safety block |
| `"Switch to GFP channel"` (not in allowed list) | `set_channel(preset="GFP")` → safety error → text reply | Safety block |
| `"Set the camera gain to 0"` | `get_available_channels` (optional) → `set_device_property(DCam, Gain, "0")` | Property set |

### 10.7 Integration tests with real Demo config

These tests require a real MM installation. Mark them with `@pytest.mark.integration` and skip by default:

```python
# tests/test_integration.py
import pytest

pytestmark = pytest.mark.integration  # skip unless -m integration


def test_snap_image_demo(headless_mm, unconstrained_guard):
    from microclaw.tools import snap_image
    result = snap_image(headless_mm, unconstrained_guard)
    assert "status" in result


def test_move_and_return_z(headless_mm, unconstrained_guard):
    from microclaw.tools import get_z_position, move_stage_z
    original = get_z_position(headless_mm, unconstrained_guard)["z_um"]
    move_stage_z(headless_mm, unconstrained_guard, z_um=original + 5.0)
    new_z = get_z_position(headless_mm, unconstrained_guard)["z_um"]
    assert abs(new_z - (original + 5.0)) < 0.1
    move_stage_z(headless_mm, unconstrained_guard, z_um=original)  # restore


def test_zstack_creates_dataset(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import run_zstack
    result = run_zstack(
        headless_mm, unconstrained_guard,
        z_start_um=45.0, z_end_um=55.0, z_step_um=2.0,
        save_dir=str(tmp_path), name="test_stack",
    )
    assert result["status"] == "Z-stack complete."
    assert (tmp_path / "test_stack").exists()


def test_zstack_export_tiff(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import run_zstack, export_dataset_as_tiff
    run_zstack(headless_mm, unconstrained_guard,
               z_start_um=45.0, z_end_um=50.0, z_step_um=1.0,
               save_dir=str(tmp_path), name="stack")
    out = str(tmp_path / "out.tiff")
    result = export_dataset_as_tiff(
        headless_mm, unconstrained_guard,
        dataset_path=str(tmp_path / "stack"),
        output_path=out,
    )
    assert result["status"] == "Export complete."
    import tifffile
    img = tifffile.imread(out)
    assert img.ndim >= 2
```

---

## 11. Dependencies (`pyproject.toml`)

```toml
[project]
name = "microclaw"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "anthropic>=0.40.0",
    "pycromanager>=0.32.0",
    "tifffile>=2024.1.1",
    "numpy>=1.26",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
test = [
    "pytest>=8.0",
    "pytest-mock",
]

[project.scripts]
microclaw = "microclaw.__main__:main"
```

---

## 12. Implementation Roadmap

| Phase | Deliverable | Prerequisite |
|---|---|---|
| 1 | `safety.py` complete with tests | None |
| 2 | `controller.py` + `tools.py` with mock-based unit tests passing | Phase 1 |
| 3 | `tools_schema.py` — all Anthropic tool definitions written and reviewed | Phase 2 |
| 4 | `agent.py` — agent loop with prompt caching; prompt-level tests passing | Phase 3 |
| 5 | Integration tests against Demo config (`-m integration`) | Phase 4 + MM installed |
| 6 | CLI (`__main__.py`), README, example sessions | Phase 5 |
| 7 | Headless mode validation for overnight protocol runs | Phase 6 |

---

## 13. Known Limitations and Mitigations

| Limitation | Mitigation |
|---|---|
| pycromanager version must match MM ZMQ server | Pin both in docs; `is_connected()` check on startup surfaces mismatch early |
| Long-running acquisitions block the agent loop | Acceptable for v1; v2 could run `Acquisition` in a thread and poll for completion |
| Single ZMQ port prevents concurrent operations | One operation at a time is the intended mode; document this |
| MM crash takes down the ZMQ connection | `execute_tool` catches `ConnectionError`; instruct user to restart MM and reconnect |
| Claude may hallucinate device names | `set_device_property` tool calls `get_property` first to validate; `list_devices` is available for Claude to check |
| No image analysis in v1 | Hooks and image processors (pycro-manager features) are the v2 path for adaptive acquisitions |
