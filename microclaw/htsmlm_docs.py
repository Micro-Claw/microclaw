HTSMLM_REFERENCE = """
# htSMLM / EMU reference (microclaw)

## What EMU and htSMLM are

EMU (Easier Micro-Manager User interfaces) is a Micro-Manager 2 plugin that
provides configurable graphical user interfaces. The configuration wizard maps
named "UI properties" (abstract controls in the GUI) to real Micro-Manager
device properties on the running hardware. This mapping is saved as a JSON file
at <MM_app_dir>/EMU/config.uicfg.

htSMLM is an EMU plugin designed specifically for SMLM microscopes. It provides:
- Per-laser on/off and power controls (up to 4 lasers)
- Filter wheel position controls
- Focus panel with Z-stage positioning and focus-lock toggle
- Quadrant photodiode (QPD) signal monitor
- Laser trigger mode / pulse duration / sequence control (MicroFPGA)
- Additional two-state device toggles (e.g., TIRF/HILO/3D/BFP modes)
- Optional iBeamSmart laser panels
- An Acquisition Wizard for unsupervised multi-position experiments

htSMLM/EMU is NOT always present. Only ask about or use it if the user
mentions htSMLM, EMU, or a SMLM control GUI running inside Micro-Manager.

---

## How to control htSMLM hardware via microclaw

Because EMU has no scripting API, control goes through the underlying
Micro-Manager device properties that EMU is configured to use.

Workflow:
  1. Call get_emu_configuration() to read the UIProperty→MM device/property
     mapping from the EMU config file.
  2. Look up the UIProperty of interest in the returned "properties" dict.
     Each entry has:
       "device"   — the MM device label (use with set_device_property)
       "property" — the MM property name (use with set_device_property)
       "on"       — value for ON state (TwoState properties only)
       "off"      — value for OFF state (TwoState properties only)
       "slope"    — linear scale factor (Rescaled properties only)
       "offset"   — linear offset (Rescaled properties only)
  3. Call set_device_property(device, property, value) with the device and
     property from step 2.

Example — enable Laser 0:
  config = get_emu_configuration()
  entry  = config["properties"]["Laser 0 enable"]
  set_device_property(entry["device"], entry["property"], entry["on"])

Example — set Laser 0 to 50% power:
  entry = config["properties"]["Laser 0 power percentage"]
  # For a Rescaled property the MM value = slope * ui_value + offset
  mm_value = float(entry.get("slope", 1)) * 50 + float(entry.get("offset", 0))
  set_device_property(entry["device"], entry["property"], str(mm_value))

---

## UIProperty name inventory

These are the exact UIProperty names used in htSMLM. They appear as keys in
the "properties" dict returned by get_emu_configuration().

### Lasers  (one panel per laser, labeled "Laser 0" through "Laser 3")
  "Laser 0 enable"              TwoState  — laser on/off
  "Laser 0 power percentage"    Rescaled  — 0–100 power % (MM value scaled by slope/offset)
  (same pattern for Laser 1, 2, 3)

### Filters  (panel label "Filters")
  "Filters Filter wheel position"    MultiState — filter wheel slot (0-based index)
  "Filters Filter wheel 2 position"  MultiState — second filter wheel (DualFW mode only)

### Focus  (panel label "Focus")
  "Focus Z stage position"       SingleState — numeric Z position in µm
  "Focus Z stage focus locking"  TwoState    — engage/disengage hardware focus lock

### Additional two-state controls  (panel label "Controls")
  "Two-state device 1" through "Two-state device 6"
  These are mapped by the user to any two-state MM device property. Common uses:
    - TIRF vs. HILO vs. epi illumination mode
    - 3D astigmatic lens in/out
    - BFP (back focal plane / Bertrand lens) in/out
    - Focus lock laser on/off
  Ask the user what each numbered device corresponds to on their system, or
  inspect the UIParameter names in the config ("Two-state device N name").

### Laser triggers  (panel label "Laser trigger 0" through "Laser trigger 3")
  "Laser trigger 0 mode"           MultiState — Off | On | Rising | Falling | Camera
  "Laser trigger 0 pulse duration" Rescaled   — pulse length in µs (max 65535)
  "Laser trigger 0 sequence"       MultiState — 16-bit binary pattern string
  (same pattern for triggers 1, 2, 3)

### iBeamSmart panels  (panel label is user-configurable, e.g. "405" or "iBeamSmart #1")
  "<name> laser power"   Rescaled  — laser power
  "<name> operation"     TwoState  — on/off
  "<name> enable fine"   TwoState  — fine power mode on/off (optional)
  "<name> fine a (%)"    Rescaled  — fine A power (optional)
  "<name> fine b (%)"    Rescaled  — fine B power (optional)

### QPD  (panel label "QPD")
  "QPD X", "QPD Y", "QPD Z"  — read-only signal monitors; do not set these.

### Powermeter  (panel label "Powermeter")
  "Powermeter Laser powermeter"  — read-only power readout; do not set this.

---

## Plugin-level settings (from plugin_settings in get_emu_configuration)

These control which panels are visible. Useful for knowing what's available:
  "Powermeter tab"        bool — powermeter tab enabled
  "Trigger tab"           bool — laser trigger tab enabled (MicroFPGA)
  "Additional FW tab"     bool — additional filter wheel tab enabled
  "Single FW panel"       bool — true = single FW, false = dual FW
  "QPD tab"               bool — QPD tab enabled
  "iBeamSmart #1"         bool — first iBeamSmart panel enabled
  "iBeamSmart #1 name"    str  — display name of first iBeamSmart panel
  "iBeamSmart #2"         bool — second iBeamSmart panel enabled
  "iBeamSmart #2 name"    str  — display name of second iBeamSmart panel
  "Additional FW tab title" str — title of the additional filters tab

---

## Acquisition: do NOT use htSMLM's Acquisition Wizard

htSMLM has its own multi-position acquisition wizard. Do not attempt to drive
it programmatically. For all acquisitions use microclaw's native tools:
  run_timelapse(...)             — single-position SMLM stacks
  run_multiposition_acquisition(...) — multi-position
  run_tile_acquisition(...)      — tile scans

---

## Key questions to ask if the user has htSMLM

1. What is the µManager installation directory?
   (Needed to read the EMU config; try get_emu_configuration() first —
   it will prompt for the directory if auto-detection fails.)

2. What does each "Two-state device N" button control?
   (TIRF/HILO/epi, 3D, BFP, etc. — varies per setup.)

3. Are the iBeamSmart panels enabled and what are their names?
   (Determines the UIProperty name prefix for those lasers.)

4. Is the Trigger tab (MicroFPGA) present?
   (Required to set laser trigger modes and pulse durations.)
"""
