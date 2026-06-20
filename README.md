# Microclaw

An AI agent for [Micro-Manager](https://micro-manager.org) fluorescence microscopy control. Describe your acquisition protocol in plain language; Microclaw translates it into Micro-Manager tool calls while the GUI responds in real time.

## Architecture

```
User (natural language) → AgentLoop (Anthropic API) → ToolRegistry → SafetyGuard → MicroscopeController → pycro-manager ZMQ → MM GUI
```

- **Agent**: `claude-opus-4-7` via Anthropic API with tool use and prompt caching.
- **Safety**: User-defined `safety_config.yaml` enforced as a hard gate before every hardware call. The AI cannot override these limits.
- **Backend**: pycro-manager (ZMQ on port 4827). Open Micro-Manager normally; Microclaw connects to the running instance.

## Quick start

### Prerequisites

1. Install [Micro-Manager 2.0](https://micro-manager.org/Download_Micro-Manager_Latest_Release).
2. Enable the ZMQ server: **Tools → Options → Run ZMQ server on port 4827**.
3. Set your `ANTHROPIC_API_KEY` environment variable.

### Install

```bash
pip install -e ".[test]"
```

### Run

```bash
microclaw --safety-config safety_config.yaml
```

## Safety configuration

Edit `safety_config.yaml` to set hardware limits. These are enforced before every tool call and cannot be overridden by the AI.

```yaml
stage:
  z_min: 0.0
  z_max: 200.0

camera:
  max_exposure_ms: 5000.0

channels:
  allowed: [DAPI, FITC, TRITC, Brightfield]

forbidden_properties:
  - device: Core
    property: Initialize
```

## Testing

Unit and agent tests run without Micro-Manager:

```bash
pytest
```

Integration tests require a running MM instance with the Demo configuration:

1. Open Micro-Manager and load `MMConfig_demo.cfg`.
2. Set `MM_RUNNING=1` (and optionally `MM_PORT` if not using the default 4827).
3. Run:

```bash
MM_RUNNING=1 pytest -m integration
```

## Headless mode

For unattended runs of established protocols, use `launch_headless` from `microclaw.config`:

```python
from microclaw.config import launch_headless
launch_headless(mm_app_path="/path/to/MM", config_file="MMConfig_demo.cfg")
```

The tool functions and agent loop are identical in GUI and headless modes.

## Available tools

| Tool | Description |
|---|---|
| `snap_image` | Snap a single image (display only; use `run_timelapse` with `n_frames=1` to save) |
| `snap_and_analyze` | Snap and return focus metric, intensity stats, and thumbnail |
| `start_live_view` / `stop_live_view` | Live camera preview |
| `set_exposure` / `get_exposure` | Camera exposure |
| `get_roi` / `set_roi` / `clear_roi` | Camera region of interest |
| `move_stage_xy` / `get_xy_position` | XY stage |
| `move_stage_z` / `get_z_position` | Z (focus) stage |
| `set_channel` / `get_available_channels` | Channel presets |
| `set_device_property` / `get_device_property` | Raw device properties |
| `list_devices` | List loaded devices |
| `list_device_properties` | List all property names for a device |
| `get_device_property_info` | Type, limits, and allowed values for a property |
| `get_full_device_state` | All property values for a device |
| `get_system_state` | Composite state snapshot |
| `run_autofocus` | Software autofocus Z-sweep |
| `run_zstack` | Z-stack acquisition |
| `run_timelapse` | Timelapse acquisition |
| `export_dataset_as_tiff` | Export NDTiff dataset to ImageJ TIFF |
| `mark_position` | Save current stage position to MM position list |
| `get_position_list` | Return all positions from MM position list |
| `go_to_position` | Move stage to a named position |
| `delete_position` | Delete a named position |
| `clear_position_list` | Clear all positions |
| `save_position_list` | Save position list to a `.pos` file |
| `load_position_list` | Load a `.pos` file into MM position list |
| `import_mm_positions` | Import positions from the MM GUI position list |
| `run_multiposition_acquisition` | Visit each position and run snap/zstack/timelapse |
| `run_tile_acquisition` | Acquire a rows×cols tile grid centered on current stage position |
| `run_multiposition_with_autofocus` | Same, with software autofocus at each position |
| `run_adaptive_acquisition` | Z-stack with a hook strategy for adaptive behaviour |
| `read_hook_log` | Read hook output log after an acquisition |
| `list_hooks` | List pre-coded and saved hook strategies |
| `generate_and_save_hook` | Validate and save a hook script |
| `read_hook_from_file` | Read and AST-scan a user-provided hook file |
