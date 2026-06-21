# htSMLM / EMU support in microclaw

## Overview

Some SMLM microscopes run **htSMLM**, an EMU plugin that provides a
Micro-Manager 2 GUI for laser control, filter wheels, focus locking, and
MicroFPGA triggering. This document describes how microclaw detects, reads,
and controls htSMLM/EMU installations, and the design choices made along the
way.

---

## Background: what EMU and htSMLM are

**EMU** (Easier Micro-Manager User interfaces) is a Micro-Manager 2 plugin
framework. Its configuration wizard maps named *UIProperties* — abstract
controls in the GUI — to real MM device properties on the connected hardware.
The mapping is stored as a JSON file at:

```
<MM_app_dir>/EMU/config.uicfg
```

**htSMLM** is an EMU plugin built for SMLM microscopes. It exposes:

| Panel | UIProperty names |
|---|---|
| Laser 0–3 | `"Laser N enable"`, `"Laser N power percentage"` |
| Filters | `"Filters Filter wheel position"` (single or dual FW) |
| Focus | `"Focus Z stage position"`, `"Focus Z stage focus locking"` |
| Controls | `"Two-state device 1"` through `"Two-state device 6"` |
| Laser triggers | `"Laser trigger N mode"`, `"Laser trigger N pulse duration"`, `"Laser trigger N sequence"` |
| iBeamSmart | `"<name> laser power"`, `"<name> operation"`, etc. |
| QPD / Powermeter | read-only monitors |

UIProperty names follow the pattern `"{panel_label} {property_suffix}"`.
For example, `LaserControlPanel("Laser 0")` with suffix `"enable"` gives the
UIProperty name `"Laser 0 enable"`.

---

## Design: no EMU scripting API

EMU has no Python or scripting API. The only way to control htSMLM-managed
hardware programmatically is to:

1. Read the UIProperty→MM device/property mapping from `config.uicfg`.
2. Call `core.set_property(device, property, value)` directly — which is what
   microclaw's existing `set_device_property` tool does.

---

## New files and functions

### `microclaw/emu_manager.py`

Core logic for detection, caching, and config parsing. No MM connection
required — all operations are filesystem reads.

**Detection**

```python
find_mm_app_dir() -> Path | None
```

Checks in order:

1. Cache at `~/.microclaw/emu.json` (field `"mm_app_dir"`)
2. Platform-specific default paths:
   - macOS: `/Applications/Micro-Manager-2.0{,.1,.2}`
   - Windows: `C:/Program Files/Micro-Manager-2.0{,.1,.2}`
   - Linux: `/opt/micro-manager`, `/usr/local/lib/micro-manager`, etc.

A candidate path is accepted if it contains either an EMU/htSMLM JAR file or
the `EMU/config.uicfg` file (see `_has_emu()`). This means detection works
even before the user has opened the EMU configuration wizard for the first
time.

```python
find_plugin_jars(mm_app_dir: Path) -> dict[str, list[str]]
```

Globs `mmplugins/EMU*.jar`, `plugins/EMU*.jar`, and the htSMLM equivalents.
Returns `{"EMU": [...], "htSMLM": [...]}`. Both subdirectory names are checked
because different MM builds use different conventions.

**Caching**

```python
save_mm_app_dir(mm_app_dir: str) -> None
```

Writes `{"mm_app_dir": "..."}` to `~/.microclaw/emu.json`. Merges with any
existing keys so that other cached state (if added later) is not overwritten.
Follows the same `~/.microclaw/` convention as the hooks manifest
(`~/.microclaw/hooks/manifest.json`).

**Config parsing**

```python
read_emu_config(mm_app_dir: str | Path) -> dict
```

Reads `EMU/config.uicfg` (JSON, written by EMU's `ConfigurationIO` using
Gson). The active configuration is the one whose `configurationName` matches
`defaultConfigurationName` at the top level, falling back to the first entry.

The raw `properties` map is a flat `TreeMap<String, String>`. EMU serialises
UIProperty metadata as extra keys with suffixes:

| suffix | meaning |
|---|---|
| `" on"` | MM value for ON state (TwoState) |
| `" off"` | MM value for OFF state (TwoState) |
| `" slope"` | scale factor (Rescaled) |
| `" offset"` | additive offset (Rescaled) |

`_parse_properties()` separates these into a nested dict keyed by UIProperty
name. Example output for two properties:

```python
{
    "Laser 0 enable": {
        "mm_property_string": "iChrome-MLE::Laser0",
        "device": "iChrome-MLE",
        "property": "Laser0",
        "on": "1",
        "off": "0",
    },
    "Laser 0 power percentage": {
        "mm_property_string": "iChrome-MLE::Laser0Power",
        "device": "iChrome-MLE",
        "property": "Laser0Power",
        "slope": "0.5",
        "offset": "0.0",
    },
    "Focus Z stage focus locking": {
        "mm_property_string": "CRISP::CRISP State",
        "device": "CRISP",
        "property": "CRISP State",
        "on": "Locked",
        "off": "Idle",
    },
}
```

The `device` / `property` split uses `"::"` as the separator, which is the
standard Micro-Manager convention for identifying device properties uniquely.

---

### `microclaw/htsmlm_docs.py`

`HTSMLM_REFERENCE` — a documentation string loaded lazily (like
`smlm_docs.SMLM_REFERENCE`) by the `get_htsmlm_documentation` tool. Covers:

- What EMU/htSMLM is and when to use these tools
- The two-step control workflow (`get_emu_configuration` → `set_device_property`)
- Complete UIProperty name inventory for all htSMLM panels
- How to compute MM values for Rescaled properties
- Plugin settings keys (tab visibility flags)
- Acquisition guidance: do not use htSMLM's built-in Acquisition Wizard;
  use microclaw's `run_timelapse` / `run_multiposition_acquisition` instead
- Key clarifying questions to ask the user

---

## New tools (tools.py / tools_schema.py)

### `check_emu_installed`

Lightweight detection tool. Does not require a running MM connection. Returns:

```json
{
    "emu_installed": true,
    "htsmlm_installed": true,
    "mm_app_dir": "/Applications/Micro-Manager-2.0",
    "emu_jars": ["EMU-2.0.jar"],
    "htsmlm_jars": ["htSMLM-1.3.jar"],
    "emu_config_present": true
}
```

If MM is not found at any standard path:

```json
{
    "emu_installed": false,
    "htsmlm_installed": false,
    "mm_app_dir": null,
    "note": "Micro-Manager installation directory not found automatically. ..."
}
```

**When to call it:** The agent should call this when it needs to decide whether
htSMLM-specific tools are relevant. If both `emu_installed` and
`htsmlm_installed` are false and the user has not mentioned htSMLM or EMU, the
agent must not call `get_htsmlm_documentation` or `get_emu_configuration`.

### `get_htsmlm_documentation`

Returns `HTSMLM_REFERENCE`. Schema description gates invocation:

> "Only call this if `check_emu_installed` has confirmed htSMLM is installed,
> OR if the user has explicitly mentioned htSMLM or EMU."

### `get_emu_configuration`

Reads and returns the parsed EMU config. Optional parameter `mm_app_dir`:

- Omitted → auto-detect via `find_mm_app_dir()`; return structured error if
  not found, prompting the agent to ask the user for the path
- Provided → save to cache and use immediately

Example return (abbreviated):

```json
{
    "config_name": "My SMLM setup",
    "plugin_name": "htSMLM",
    "mm_app_dir": "/Applications/Micro-Manager-2.0",
    "properties": {
        "Laser 0 enable": {
            "device": "iChrome-MLE",
            "property": "Laser0",
            "on": "1",
            "off": "0"
        },
        "Laser 0 power percentage": {
            "device": "iChrome-MLE",
            "property": "Laser0Power",
            "slope": "0.5",
            "offset": "0.0"
        }
    },
    "plugin_settings": {
        "Trigger tab": "true",
        "QPD tab": "false"
    }
}
```

Schema description also gates invocation:

> "Only call this if `check_emu_installed` has confirmed EMU is installed,
> OR if the user has explicitly mentioned htSMLM or EMU."

---

## Agent workflow: controlling htSMLM hardware

```
1. check_emu_installed()
       → emu_installed=true, htsmlm_installed=true

2. get_htsmlm_documentation()
       → learn UIProperty names and workflow

3. get_emu_configuration()
       → {"properties": {"Laser 0 enable": {"device": "iChrome-MLE",
                                              "property": "Laser0",
                                              "on": "1", "off": "0"}, ...}}

4. set_device_property("iChrome-MLE", "Laser0", "1")
       → laser 0 enabled

5. # Rescaled property: MM value = slope * ui_value + offset
   # "Laser 0 power percentage" slope=0.5, offset=0.0, desired=50%
   set_device_property("iChrome-MLE", "Laser0Power", "25.0")
```

---

## Fallback: MM directory not at a standard path

If `check_emu_installed()` returns `mm_app_dir: null`, the agent should ask
the user for the path. On the next call to `get_emu_configuration`:

```
get_emu_configuration(mm_app_dir="/custom/path/Micro-Manager")
```

The path is written to `~/.microclaw/emu.json` and reused in all future
sessions without prompting again.

---

## What is deliberately excluded

- **htSMLM Acquisition Wizard**: htSMLM has a built-in multi-position
  acquisition mode. It is not driven programmatically. All acquisitions go
  through microclaw's native tools (`run_timelapse`,
  `run_multiposition_acquisition`, `run_tile_acquisition`).

- **EMU scripting API**: there is none. The only path is via
  `core.set_property()` using the device/property mapping from the config file.

- **QPD and Powermeter writes**: these UIProperties are read-only hardware
  monitors. The agent must not attempt to set them.
