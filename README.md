# Microclaw

An AI agent for [Micro-Manager](https://micro-manager.org) fluorescence microscopy control. Describe your acquisition protocol in plain language; Microclaw translates it into Micro-Manager tool calls while the GUI responds in real time.

> [!CAUTION]
> The hardware safety features are not comprehensive. Always be mindful of what your microscope is doing. Use at your own risk.

## Architecture

```
User (natural language) → AgentLoop (Anthropic API) → ToolRegistry → SafetyGuard → MicroscopeController → pycro-manager ZMQ → MM GUI
```

- **Agent**: `claude-opus-4-8` via Anthropic API with tool use and prompt caching.
- **Safety**: User-defined `safety_config.yaml` enforced as a hard gate before every hardware call. The AI cannot override these limits.
- **Backend**: pycro-manager (ZMQ on port 4827). Open Micro-Manager normally; Microclaw connects to the running instance.

## Quick start

### Prerequisites

1. Install [Micro-Manager 2.0](https://micro-manager.org/Download_Micro-Manager_Latest_Release).
2. Enable the ZMQ server: **Tools → Options → Run pycro-manager server on port 4827**.
3. Set your `ANTHROPIC_API_KEY` environment variable.

### Install

```bash
pip install -e ".[test]"
```

### Run

```bash
microclaw --safety-config safety_config.yaml
```

## Set up a runnable .bat with the environment variables

Create a `.bat` file containing the following lines, updated to your installation path and API key.

```
set ANTHROPIC_API_KEY=your-key-here
cd C:\path\to\microclaw
C:\path\to\miniconda3\Scripts\activate.bat microclaw && microclaw --safety-config safety_config.yaml
```

## Safety configuration

Edit `safety_config.yaml` to set hardware limits. These are enforced before every tool call and cannot be overridden by the AI.

```yaml
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
  allowed: [DAPI, FITC, TRITC, Brightfield]

# Raw device-property writes default to a denylist: named (device, property)
# pairs are refused and every other property stays writable.
forbidden_properties:
  - device: Core
    property: Initialize

# For hardware-attached rigs, swap the denylist for an allowlist — ONLY the
# listed pairs may be written, everything else is refused. This is the only mode
# in which raw property writes have a hard gate. (forbidden_properties is ignored
# while allowed_properties is set.)
# allowed_properties:
#   - device: DWheel
#     property: Label

# Optional filesystem sandbox for file-touching tools (hook reads/writes,
# position-list saves, TIFF export). Unset = unrestricted. When set, those tools
# are confined to this directory; `..` and symlink escapes are rejected.
# workspace_dir: /data/microclaw

# Micro-Manager plugin hooks run arbitrary Java that bypasses the checks above,
# so they have their own two gates.
plugins:
  # Fully-qualified classpaths to forbid. Analyzer (read-only) plugins are
  # allowed by default; list only the ones to block. Normally empty.
  blocked: []
  # Hardware-motion plugins (e.g. autofocus) are gated behind this single flag,
  # not a per-plugin list. Default off; a human flips it to opt in.
  allow_hardware_motion: false
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

## Available tools

| Tool | Description |
|---|---|
| `snap_image` | Snap a single image (display only; use `run_timelapse` with `n_frames=1` to save) |
| `snap_and_analyze` | Snap and return focus metric, intensity stats, and thumbnail |
| `start_live_view` / `stop_live_view` | Live camera preview |
| `set_exposure` / `get_exposure` | Camera exposure |
| `get_roi` / `set_roi` / `clear_roi` | Camera region of interest |
| `get_pixel_size` | Effective pixel size at the sample plane (µm) |
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
| `mark_position` | Mark current stage position (mirrored into MM's Position List Manager) |
| `get_position_list` | Return all marked positions |
| `go_to_position` | Move stage to a named position |
| `delete_position` | Delete a named position |
| `clear_position_list` | Clear all positions |
| `save_position_list` | Save position list to a microclaw JSON file |
| `load_position_list` | Load a microclaw JSON position file |
| `import_mm_positions` | Import positions from the MM GUI position list |
| `run_multiposition_acquisition` | Visit each position and run snap/zstack/timelapse |
| `run_tile_acquisition` | Acquire a rows×cols tile grid centered on current stage position |
| `run_multiposition_with_autofocus` | Same, with software autofocus at each position |
| `run_adaptive_zstack` | Z-stack with a hook strategy for adaptive behaviour |
| `run_adaptive_timelapse` | Timelapse with a hook strategy for adaptive behaviour |
| `read_hook_log` | Read hook output log after an acquisition |
| `list_hooks` | List pre-coded and saved hook strategies |
| `get_hook_documentation` | Return the pycro-manager hook API reference (called automatically before hook generation) |
| `generate_and_save_hook` | Validate and save a hook script |
| `read_hook_from_file` | Read and AST-scan a user-provided hook file |
| `list_mm_plugins` | List installed MM plugins grouped by role (autofocus, processor, …) for use as hooks |
| `get_smlm_documentation` | Return the SMLM protocol reference (dSTORM/PALM/PAINT parameters, acquisition protocol, drift correction, post-processing, pitfalls) |
| `check_emu_installed` | Detect whether EMU and htSMLM are installed by scanning the Micro-Manager plugins directory for their JARs |
| `get_htsmlm_documentation` | Return the htSMLM/EMU reference (UIProperty inventory, control workflow, panel descriptions) — only called if EMU/htSMLM is detected or user mentions it |
| `get_emu_configuration` | Read the EMU config file and return the UIProperty→MM device/property mapping for the active htSMLM configuration — only called if EMU is detected or user mentions it |
| `save_knowledge` | Save a non-standard fact about a sample, device, or strategy to the persistent knowledge base |
| `get_knowledge` | Retrieve entries from the persistent knowledge base |
| `delete_knowledge` | Remove a single entry from the persistent knowledge base |
