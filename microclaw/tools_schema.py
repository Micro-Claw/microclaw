from typing import Any

_PROTOCOL_PARAMS_SCHEMA = {
    "type": "object",
    "description": (
        "Parameters for the selected per-position protocol. Put channel and "
        "exposure_ms here, never at the top level. Valid shapes: timelapse: "
        "{n_frames, interval_s, optional channel, optional exposure_ms, optional "
        "laser_slot}; zstack: {z_start_um, z_end_um, z_step_um, optional channel, "
        "optional exposure_ms}. The nested Z keys describe the acquisition "
        "protocol's planes and spacing, not an autofocus search. snap takes no "
        "protocol_params."
    ),
    "properties": {
        "channel": {
            "type": "string",
            "description": "Optional channel for each frame.",
        },
        "exposure_ms": {
            "type": "number",
            "description": "Optional exposure in ms for each frame.",
        },
        "n_frames": {"type": "integer"},
        "interval_s": {"type": "number"},
        "laser_slot": {
            "type": "integer",
            "description": "Timelapse only: EMU trigger slot to pre-flight.",
        },
        "z_start_um": {"type": "number", "description": 'First plane as an absolute stage Z coordinate in µm, not an offset from current focus or a half-thickness. Read current Z with get_system_state and place the stack around it. Must be finite and less than z_end_um; equal endpoints are one plane, not a stack, and are refused. Supply all three Z keys.'},
        "z_end_um": {"type": "number", "description": 'Last requested plane as an absolute stage Z coordinate in µm. Must be finite and greater than z_start_um. The inclusive-overshooting final plane may lie up to one z_step_um beyond this value; actual generated extrema are guarded. Supply all three Z keys.'},
        "z_step_um": {"type": "number", "description": 'Finite positive step in µm for ascending sweeps only; direction is not inferred. Supply all three Z keys with z_end_um greater than z_start_um. The inclusive-overshooting final plane may lie up to one step beyond z_end_um; actual generated extrema are guarded.'},
    },
}

_HOOK_ILLUMINATION_ENVELOPE_SCHEMA = {
    "type": "object",
    "description": (
        "Optional pre-run envelope for generated-hook illumination power modulation. "
        "Names one configured power device/property, a percent ceiling, and the "
        "maximum accepted increasing writes. Power-only: a shutter enable or turning "
        "light on is not expressible. The whole envelope is confirmed once before "
        "the run; no callback-thread prompt occurs."
    ),
    "properties": {
        "device": {"type": "string"},
        "property": {"type": "string"},
        "max_power_percent": {"type": "number"},
        "max_writes": {"type": "integer"},
    },
    "required": ["device", "property", "max_power_percent", "max_writes"],
    "additionalProperties": False,
}

_HOOK_NAMED_STAGE_ENVELOPE_SCHEMA = {
    "type": "object",
    "description": (
        "One labelled stage, inclusive interval, attempted-write budget, and "
        "explicit restoration policy. max_writes caps hook-proposed moves only; "
        "the restoration promised by this envelope is not charged to that budget. "
        "Every planned position and an explicit restore value must be inside "
        "min_um/max_um. The exact recorded entry position used by restore='entry' "
        "may lie outside that proposal interval."
    ),
    "properties": {
        "device": {"type": "string", "minLength": 1},
        "min_um": {"type": "number"}, "max_um": {"type": "number"},
        "max_writes": {"type": "integer", "minimum": 1},
        "restore": {"oneOf": [
            {"type": "string", "enum": ["leave", "entry"]},
            {"type": "object", "properties": {"value": {"type": "number"}},
             "required": ["value"], "additionalProperties": False},
        ]},
    },
    "required": ["device", "min_um", "max_um", "max_writes", "restore"],
    "additionalProperties": False,
}

_HOOK_PROPERTY_ENVELOPE_SCHEMA = {
    "type": "object",
    "description": (
        "One exact device/property, categorical values or numeric interval, "
        "attempted-write budget, and explicit restoration policy. max_writes "
        "caps hook-proposed writes only; the restoration promised by this "
        "envelope is not charged to that budget. Every planned value and an "
        "explicit restore value must be inside the approved set or interval. "
        "The exact recorded entry value used by restore='entry' may lie outside "
        "the proposal bounds."
    ),
    "properties": {
        "device": {"type": "string", "minLength": 1},
        "property": {"type": "string", "minLength": 1},
        "allowed_values": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
        "min": {"type": "number"}, "max": {"type": "number"},
        "max_writes": {"type": "integer", "minimum": 1},
        "restore": {"oneOf": [
            {"type": "string", "enum": ["leave", "entry"]},
            {"type": "object", "properties": {"value": {"type": "string"}},
             "required": ["value"], "additionalProperties": False},
        ]},
    },
    "oneOf": [
        {"required": ["device", "property", "allowed_values", "max_writes", "restore"]},
        {"required": ["device", "property", "min", "max", "max_writes", "restore"]},
    ],
    "additionalProperties": False,
}

_HOOK_ACTION_PLAN_SCHEMA = {
    "type": "array",
    "description": (
        "Exactly one indexed action list for every generated fixed-run event; "
        "empty action lists are explicit. Each action is a discriminated object, "
        "using exactly {'kind': 'MoveNamedStage', 'position_um': 12.5} or "
        "{'kind': 'SetDeviceProperty', 'value': 'On'}. hooked_fixed_plan needs no hook_strategy: "
        "write, settle and read-back precede each event, without image-decision handoff. Needs named_stage_envelope or "
        "property_envelope to authorize it. For a timelapse, every consecutive "
        "int(k * interval_s * 1000.0) deadline must differ across n_frames; "
        "equal truncated millisecond deadlines allow hardware-sequencing and "
        "are refused at plan time for per-frame hardware control."
    ),
    "items": {"type": "object", "properties": {
        "hook_event_index": {"type": "integer", "minimum": 0},
        "actions": {"type": "array", "items": {"oneOf": [
            {"type": "object", "properties": {
                "kind": {"type": "string", "enum": ["MoveNamedStage"]},
                "position_um": {"type": "number"}},
             "required": ["kind", "position_um"], "additionalProperties": False},
            {"type": "object", "properties": {
                "kind": {"type": "string", "enum": ["SetDeviceProperty"]},
                "value": {"type": "string"}},
             "required": ["kind", "value"], "additionalProperties": False},
        ]}},
    }, "required": ["hook_event_index", "actions"], "additionalProperties": False},
}

_HOOK_ARTIFACT_LIMITS_SCHEMA = {
    "type": "object",
    "description": (
        "Optional parent-owned limits for hook artifacts: maximum bytes per artifact, "
        "artifact count, and total bytes for the run. Hooks receive no path."
    ),
    "properties": {
        "max_artifact_bytes": {"type": "integer"},
        "max_count": {"type": "integer"},
        "max_total_bytes": {"type": "integer"},
    },
    "required": ["max_artifact_bytes", "max_count", "max_total_bytes"],
    "additionalProperties": False,
}

_ADAPTIVE_AUTOFOCUS_BUDGET_SCHEMA = {
    "type": "object",
    "description": (
        "Optional exposure budget authorizing RequestAutofocus from a saved or "
        "generated adaptive survey hook. max_exposures bounds autofocus sweep "
        "snaps plus refocused-tile re-exposures; each tile may be refocused once."
    ),
    "properties": {
        "max_exposures": {"type": "integer"},
        "z_range_um": {"type": "number"},
        "z_step_um": {"type": "number"},
        "method": {"type": "string", "enum": ["coarse_then_fine", "single_sweep"]},
        "settle_ms": {"type": "integer"},
    },
    "required": ["max_exposures", "z_range_um", "z_step_um", "method", "settle_ms"],
    "additionalProperties": False,
}

_CALIBRATION_REF_SCHEMA = {
    "description": (
        "Optional tagged calibration reference. Omit it to use calibration recorded "
        "in the NDTiff dataset. Every explicit reference must include the shown "
        "'kind' discriminator."
    ),
    "oneOf": [
        {"type": "object", "properties": {
            "kind": {"const": "artifact"}, "path": {"type": "string"}},
         "required": ["kind", "path"], "additionalProperties": False},
        {"type": "object", "properties": {
            "kind": {"const": "knowledge_version"}, "key": {"type": "string"}},
         "required": ["kind", "key"], "additionalProperties": False},
        {"type": "object", "properties": {
            "kind": {"const": "confirmed_current"},
            "objective": {"type": "string"}, "binning": {"type": "integer"}},
         "required": ["kind", "objective", "binning"], "additionalProperties": False},
        {"type": "object", "properties": {
            "kind": {"const": "legacy_derived"},
            "pixel_size_um": {"type": "number", "exclusiveMinimum": 0},
            "objective": {"type": "string"}, "binning": {"type": "integer"},
            "rot90_k": {"type": "integer"}, "flip_x": {"type": "boolean"},
            "flip_y": {"type": "boolean"}, "camera_device": {"type": "string"},
            "camera_model": {"type": "string"},
            "roi": {"type": "array", "items": {"type": "integer"},
                    "minItems": 4, "maxItems": 4}},
         "required": ["kind", "pixel_size_um", "objective", "binning"],
         "additionalProperties": False},
    ],
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "export_session_script",
        "description": (
            "Compile the supplied append-only session record to a standalone "
            "pycro-manager Python script. Unsupported calls make the script fail "
            "loudly rather than being reconstructed. Refusals appear in "
            "not_emitted_calls with complete: false; each reason names the exact "
            "missing export capability. This is a Microclaw capability gap, "
            "never a camera, rig or hardware defect: quote the reason in your "
            "report rather than attributing it to hardware. For a diagnosis "
            "needing source detail, read the emitted artifact."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "output_path": {"type": "string"},
                "tool_use_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional recorded tool_use ids to emit. An id is the "
                        "`toolu_...` identifier of the call itself, never a tool "
                        "name: passing \"move_named_stage\" is refused as an "
                        "unknown id. Take the ids from a previous export's "
                        "`recorded_calls`, which lists every id beside its tool. "
                        "Excluded calls remain visible as SKIPPED comments; "
                        "dependencies are not inferred."
                    ),
                },
            },
            "required": ["output_path"],
        },
    },
    {
        "name": "get_current_datetime",
        "description": (
            "Return the machine's current local date and time. You have no clock "
            "of your own, so call this before date-stamping anything — a dataset "
            "or folder name, a saved note — instead of guessing the date. Returns "
            "'date' (2026-08-20), 'time' (14:32:05), 'compact' (20260820_143205, "
            "the stamp Microclaw's own history files use and the one to put in a "
            "directory name), the full local and UTC ISO timestamps, and the "
            "timezone."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "write_text_file",
        "description": (
            "Write supplied text to a confirmed workspace path without overwriting "
            "an existing file. Use export_session_script, not this tool, to compile "
            "recorded hardware calls. If that export refuses or emits nothing and "
            "you write acquisition code by hand instead, that is allowed and often "
            "more useful than nothing — but in the same message say plainly that it "
            "is NOT the exported artifact, that nothing has run or checked it, and "
            "that it may not work. Report the export's refusal as well: it is a "
            "defect worth fixing, and a hand-written script that quietly stands in "
            "for the export hides it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["path", "text"],
            "additionalProperties": False,
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
        "name": "get_pixel_size",
        "description": (
            "Return the effective pixel size at the sample plane in micrometers, "
            "as configured in Micro-Manager's pixel size calibration. "
            "Returns 0.0 with a warning if no calibration is set. "
            "Call this before any SMLM acquisition or when computing physical distances "
            "from pixel coordinates."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_roi",
        "description": (
            "Get the current camera region of interest (ROI) as pixel coordinates "
            "(x, y, width, height) measured from the top-left of the sensor."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_roi",
        "description": (
            "Crop the camera to a rectangular region of interest (ROI). Coordinates "
            "are in pixels from the top-left corner of the full sensor frame. "
            "Reducing the ROI increases frame rate and reduces data volume. "
            "If live view is running it will be restarted so the display updates "
            "immediately. Use clear_roi to return to the full frame."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {
                    "type": "integer",
                    "description": "Left edge of ROI in pixels (0 = left of sensor).",
                },
                "y": {
                    "type": "integer",
                    "description": "Top edge of ROI in pixels (0 = top of sensor).",
                },
                "width": {
                    "type": "integer",
                    "description": "Width of ROI in pixels (must be positive).",
                },
                "height": {
                    "type": "integer",
                    "description": "Height of ROI in pixels (must be positive).",
                },
            },
            "required": ["x", "y", "width", "height"],
        },
    },
    {
        "name": "clear_roi",
        "description": (
            "Reset the camera ROI to the full sensor frame. "
            "If live view is running it will be restarted so the display updates "
            "immediately. Returns the full-frame image dimensions."
        ),
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
        "name": "calibrate_stage_to_camera",
        "description": (
            "Measure the stage↔camera affine (pixel size, camera rotation, and both "
            "axis flips) in ~4 snaps: snap, move a known ΔX, snap, cross-correlate; "
            "repeat for ΔY; the stage returns to its start. Cached per "
            "(objective, binning) in the knowledge base. Run this before any "
            "image-guided navigation — with it, 'move the feature to the centre' is "
            "arithmetic instead of guessing axis signs from thumbnails. Needs a "
            "structured field of view (features to track). The step is scaled to "
            "the field of view by default (≈¼ of the smaller FOV dimension) so the "
            "two snaps overlap; a fixed step larger than the FOV — common on a "
            "cropped ROI — pushes the scene out of frame and fails to register. "
            "On failure the error names the cause: step too large for the FOV, "
            "step too small, or no trackable structure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "step_um": {
                    "type": "number",
                    "description": (
                        "Stage step for the measurement. Omit to auto-scale to ¼ of "
                        "the smaller FOV dimension when a pixel size is known "
                        "(falls back to 20 µm otherwise). Set explicitly only to "
                        "override."
                    ),
                },
                "pixel_size_hint_um": {
                    "type": "number",
                    "description": (
                        "Known camera pixel size (µm/px) used to scale the step when "
                        "MM has no pixel-size calibration configured. Omit if MM "
                        "already knows the pixel size."
                    ),
                },
            },
            "required": [],
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
            "given position. With absolute=false, move relative to current position. "
            "Success reports the measured settled position; a target miss raises a "
            "typed StageMoveError with the same measured fields."
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
        "name": "list_stages",
        "description": (
            "List every stage device by type and show which ones the core Z/XY "
            "tools actually drive. move_stage_z and get_z_position address ONLY "
            "the core focus device; any other single-axis stage (e.g. a TIRF "
            "beam-steering axis) must be driven with move_named_stage. Call this "
            "before assuming an axis is unreachable."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_stage_position",
        "description": "Get the position (µm) of a single-axis stage addressed by device label.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Stage device label (from list_stages)."},
            },
            "required": ["device"],
        },
    },
    {
        "name": "move_named_stage",
        "description": (
            "Move a single-axis stage addressed by device label (e.g. a TIRF "
            "steering axis that is not the core focus device). Guarded by the "
            "per-device named_stages limits in the safety config — a stage with "
            "no entry there cannot be moved (fail-closed); tell the user to add "
            "one if refused. Success reports the measured settled position; a "
            "target miss raises a typed StageMoveError with the same measured fields."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Stage device label (from list_stages)."},
                "um": {"type": "number", "description": "Target position (absolute) or delta (relative) in µm."},
                "absolute": {
                    "type": "boolean",
                    "description": "True for absolute coordinates, False for relative.",
                    "default": True,
                },
            },
            "required": ["device", "um"],
        },
    },
    {
        "name": "set_channel",
        "description": (
            "Set the imaging channel by applying its authorized hardware writes in "
            "order, with read-back verification and rollback on failure. On a rig "
            "with a Micro-Manager 'Channel' config group a channel is a preset "
            "there; on a rig without one the channels are the named laser slots of "
            "the EMU configuration, and a switch moves laser enables only. Always "
            "call get_available_channels first: it gives this rig's exact channel "
            "names, which source they come from, and what a switch moves."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "preset": {
                    "type": "string",
                    "description": (
                        "Exact channel name from get_available_channels (e.g. 'DAPI', "
                        "'FITC', or a configured laser name like '640'). Never infer "
                        "it from a device property string or a slot index."
                    ),
                }
            },
            "required": ["preset"],
        },
    },
    {
        "name": "get_available_channels",
        "description": (
            "List this rig's channel names and say where they come from: the "
            "Micro-Manager 'Channel' config group, or — on a rig without one — the "
            "named laser slots of the EMU configuration. Also reports any "
            "channel-shaped thing the rig refused to name, with the reason. "
            "If an 'authorized' list is present, this session's safety config "
            "permits only those; 'channels' still lists everything the rig has, "
            "so set_channel on a name outside 'authorized' will be refused."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_config_groups",
        "description": (
            "With no arguments, lists every Micro-Manager config group, its preset "
            "names, and its currently active preset (null when live state matches "
            "none). With both group and preset, returns that one preset's exact "
            "device/property/value settings instead of the listing. Group and "
            "preset names are case-sensitive."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "group": {"type": "string", "description": "Exact config-group name."},
                "preset": {"type": "string", "description": "Exact preset name in that group."},
            },
            "required": [],
        },
    },
    {
        "name": "set_config_preset",
        "description": (
            "Apply a Micro-Manager config preset with per-effect authorization, "
            "read-back verification, and reverse rollback on failure. Use "
            "list_config_groups first; never infer preset membership or names."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "group": {"type": "string", "description": "Exact config-group name."},
                "preset": {"type": "string", "description": "Exact preset name."},
            },
            "required": ["group", "preset"],
        },
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
            "active channel, exposure time, whether live view is running, and the "
            "shutter, per-slot laser, optical-path, objective-calibration, and "
            "focus-lock state. "
            "When illumination shutters are declared, also returns each declared "
            "illumination property with its current and configured off values, "
            "without judging which values are required. "
            "Call this first when you need context before executing a protocol. "
            "The shutter and lasers fields are always present, and read 'unknown' "
            "when this rig cannot report them — never tell the user illumination "
            "was off unless this tool said so."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "shutter_declared_illumination",
        "description": (
            "Explicitly drive every property declared under illumination.shutters "
            "to its configured off_value. Use only when the operator asks to shutter "
            "declared illumination; session exit never calls this action."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "run_zstack",
        "description": (
            'Acquire the requested Z planes in one fixed stack (no_time_axis). Without hooks or actions, fixed_plan submits the planes together; hooked_fixed_plan adds callbacks. Hardware actions wait for validation, settling and read-back before their plane.'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "z_start_um": _PROTOCOL_PARAMS_SCHEMA["properties"]["z_start_um"],
                "z_end_um": _PROTOCOL_PARAMS_SCHEMA["properties"]["z_end_um"],
                "z_step_um": _PROTOCOL_PARAMS_SCHEMA["properties"]["z_step_um"],
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
                "hook_strategy": {"type": "string", "description": ("Hook from list_hooks. snr_observer measures every frame without threshold actions or acquisition changes; results do not gate exposures. Use read_hook_log afterwards. Fixed stacks cannot skip planes or stop early; use run_adaptive_survey for stop-on-condition work. Hardware-moving plugins require confirmation; only the resulting position is guarded.")},
                "hook_params": {"type": "object", "description": "Hook constructor parameters."},
                "log_path": {"type": "string", "description": "Hook output log path."},
                "illumination_envelope": _HOOK_ILLUMINATION_ENVELOPE_SCHEMA,
                "named_stage_envelope": _HOOK_NAMED_STAGE_ENVELOPE_SCHEMA,
                "property_envelope": _HOOK_PROPERTY_ENVELOPE_SCHEMA,
                "hook_action_plan": _HOOK_ACTION_PLAN_SCHEMA,
                "artifact_limits": _HOOK_ARTIFACT_LIMITS_SCHEMA,
            },
            "required": ["z_start_um", "z_end_um", "z_step_um", "save_dir"],
        },
    },
    {
        "name": "run_timelapse",
        "description": (
            'Acquire a fixed-length movie or an image-driven time series in one acquisition. fixed_plan submits the fixed events together; hooked_fixed_plan adds callbacks without restarting acquisition. adaptive_handoff publishes one successor only after analysis and guarded actions finish. Microclaw ships no rig-specific achieved-cadence measurement.'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "n_frames": {"type": ["integer", "null"], "description": (
                    "fixed_plan frame count; omit hooks and action plans when no analysis or control is needed. Provide exactly one of n_frames or "
                    "max_frames; pass null here when max_frames is used. With "
                    "interval_s=0 and more than one frame, "
                    "the time axis permits hardware sequencing if the devices support it; Microclaw's "
                    "Stop button and engine abort may not stop it promptly."
                )},
                "max_frames": {"type": "integer", "description": (
                    "adaptive_handoff frame and dose cap: analysis and verified actions gate each next exposure. A saved analyze_frame hook must return exactly one ContinueAcquisition or StopAcquisition per image. No hook_action_plan. Provide exactly one of "
                    "n_frames or max_frames; max_frames requires hook_strategy."
                )},
                "interval_s": {"type": "number", "description": (
                    "Requested seconds between starts: interval_s=0 is no_requested_delay, not inverse-exposure frame rate; positive intervals use shared_timepoint_clock. Readout, processing and storage can add delay. Fixed runs with per-frame "
                    "hardware control (including hook_action_plan) require distinct "
                    "consecutive int(k * interval_s * 1000.0) deadlines throughout "
                    "n_frames; equal truncated millisecond "
                    "deadlines allow hardware-sequencing and are refused at plan time. "
                    "Observation-only bursts remain allowed. At zero interval, the Stop button "
                    "and engine abort may not stop a burst promptly."
                )},
                "channel": {"type": "string", "description": "Channel preset (optional)."},
                "exposure_ms": {"type": "number", "description": "Exposure in ms (optional)."},
                "save_dir": {"type": "string"},
                "name": {"type": "string", "default": "timelapse"},
                "laser_slot": {
                    "type": "integer",
                    "description": (
                        "EMU slot index of the excitation laser (from "
                        "get_emu_laser_map); pass on EMU/htSMLM rigs to verify the excitation trigger: a gated-off laser silently produces blank frames. The acquisition is refused if that "
                        "slot's trigger mode is '0 - Off' or its sequence is 0."
                    ),
                },
                "hook_strategy": {"type": "string", "description": ("Hook from list_hooks. snr_observer measures every frame without threshold actions or acquisition changes; results do not gate exposures. Use read_hook_log afterwards. Fixed hooks cannot skip or stop; max_frames enables single-field conditional stopping, and run_adaptive_survey handles stop-on-condition spatial surveys. Hardware-moving plugins require confirmation; only the resulting position is guarded.")},
                "hook_params": {"type": "object", "description": "Hook constructor parameters."},
                "log_path": {"type": "string", "description": "Hook output log path."},
                "illumination_envelope": _HOOK_ILLUMINATION_ENVELOPE_SCHEMA,
                "named_stage_envelope": _HOOK_NAMED_STAGE_ENVELOPE_SCHEMA,
                "property_envelope": _HOOK_PROPERTY_ENVELOPE_SCHEMA,
                "hook_action_plan": _HOOK_ACTION_PLAN_SCHEMA,
                "artifact_limits": _HOOK_ARTIFACT_LIMITS_SCHEMA,
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
    {
        "name": "build_stage_coordinate_mosaic",
        "description": (
            "Build a zero-exposure stage-coordinate mosaic from one explicitly "
            "selected plane of a saved NDTiff dataset. Requires ONE dataset "
            "containing every tile on a `position` axis. Acquire it with "
            "run_multiposition_acquisition(hook_strategy=...). A plain "
            "run_multiposition_acquisition call or "
            "run_multiposition_with_autofocus writes one dataset per position "
            "and CANNOT be mosaicked; plan the acquisition accordingly before "
            "exposing the sample."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_path": {"type": "string"},
                "output_path": {"type": "string"},
                "axis_selection": {
                    "type": "object",
                    "description": "Coordinate values for ambiguous non-position axes; singleton axes default automatically.",
                },
                "calibration_ref": _CALIBRATION_REF_SCHEMA,
                "output_pixel_size_um": {"type": "number", "exclusiveMinimum": 0},
            },
            "required": ["dataset_path", "output_path"],
        },
    },
    {
        "name": "run_analysis_on_saved_dataset",
        "description": (
            "Measure a completed NDTiff dataset. Zero hardware action: it reads "
            "saved pixels and exposes nothing. Two general adapters are BUILT IN and need "
            "no review, hash pin or confirmation — reach for them before writing "
            "anything and before reasoning from a picture. 'connected_components' "
            "reports component counts, calibrated area and geometry, with separate "
            "detection evidence for original saved frames. A component is contiguous "
            "thresholded signal, not an object identification. "
            "'frame_statistics' (input_kind='frames') scores every "
            "saved frame with the same statistics as a live snap: this is how you "
            "say whether anything is in an acquisition you already ran. "
            "'ilastik_pixel_classification' is the completed-survey batch boundary "
            "for an installed ilastik and a user-trained, SHA-256-pinned .ilp; its "
            "pooled whole-field scores remain scientifically unverified. An adapter "
            "from the user's saved manifest also runs here, and those stay reviewed "
            "and hash-pinned."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_path": {"type": "string"},
                "adapter": {"type": "string"},
                "axis_selection": {
                    "type": "object",
                    "description": (
                        "Saved axes to select, e.g. time, channel and z. Frames measures "
                        "each matching coordinate separately, including position; omitted "
                        "axes are enumerated, never pooled into one count. Select explicit "
                        "time/channel/Z planes for a per-position report. Mosaic requires "
                        "every ambiguous non-position axis fixed and selects all positions."
                    ),
                },
                "input_kind": {
                    "type": "string", "enum": ["frames", "stage_coordinate_mosaic"],
                    "description": (
                        "For connected_components, 'frames' measures original saved frames "
                        "per coordinate with real zero-valued pixels included. Overlapping "
                        "fields can count the same signal twice; their sum is not a unique "
                        "object total. 'stage_coordinate_mosaic' measures resampled, "
                        "later-tile-overwritten mosaic signal, not per-field counts. Both "
                        "require resolvable recorded calibration or calibration_ref; "
                        "confirmed_current is unavailable offline. Source stage geometry "
                        "requires saved XPosition_um_Intended/YPosition_um_Intended; "
                        "missing XY is disclosed with pixel geometry only."
                    ),
                },
                "parameters": {
                    "type": "object",
                    "description": (
                        "Constructor arguments for the adapter itself — this is where "
                        "every adapter-specific value goes, NOT model_project_config. "
                        "connected_components accepts min_snr (otherwise configured/default), "
                        "min_area_um2 (default 0), max_area_um2 (default no upper bound), "
                        "and write_annotations (boolean, default true). Frame and mosaic "
                        "detection annotations are separate artifacts linked to observations. "
                        "T labels number sorted saved position identities, not capture order; "
                        "the recorded mapping resolves each label. Artifact "
                        "limits can omit evidence with a disclosed reason without failing counts. "
                        "'ilastik_pixel_classification' needs only four: project_path "
                        "and the three label roles background_label, numerator_label "
                        "and denominator_label, whose names must be the project's own "
                        "— its refusal lists them for you. If the project's label names "
                        "are unknown, call with `project_path` and placeholder label names: "
                        "the refusal lists the project's own labels and reads no dataset. "
                        "Everything else is worked "
                        "out and recorded: the installed ilastik is found, the project "
                        "is hashed, and the decimation matches the pixel size it was "
                        "drawn at. **Do not ask the user for a digest, an executable "
                        "path or a target size.** Pass executable_path only if the "
                        "adapter reports it could not find ilastik or found several. "
                        "Decimation is automatic and you should not set it: the "
                        "adapter matches the pixel size the project was drawn at, "
                        "and records decimation_mode, decimation_stride and the "
                        "effective pixel size in every observation. target_size "
                        "overrides that and is only for a caller who has a reason."
                    ),
                },
                "output_dir": {"type": "string"},
                "calibration_ref": _CALIBRATION_REF_SCHEMA,
                "output_pixel_size_um": {"type": "number", "exclusiveMinimum": 0},
                "model_project_config": {
                    "type": "object",
                    "description": (
                        "Provenance only: paths recorded and hashed into the manifest so "
                        "a run can be reproduced. Passing an adapter's arguments here "
                        "does NOT configure it — they belong in parameters."
                    ),
                },
                "artifact_limits": _HOOK_ARTIFACT_LIMITS_SCHEMA,
                "max_array_bytes": {"type": "integer", "minimum": 1},
            },
            "required": ["dataset_path", "adapter", "axis_selection", "input_kind",
                         "parameters", "output_dir"],
        },
    },
    {
        "name": "snap_and_analyze",
        "description": (
            "Snap a single image, display it in the Micro-Manager viewer, and return "
            "numerical stats (focus metric, mean/min/max intensity, saturation). This is THE "
            "snap tool — use it both for quantitative image data and for one-shot "
            "captures the user wants to see. The payload's displayed_in_mm_viewer "
            "field reports whether Micro-Manager has a Preview window open for it. "
            "Live view is paused "
            "around the snap and restored automatically. "
            "Only set return_thumbnail=true when you genuinely need to see the image visually — "
            "it incurs significant vision token cost."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "return_thumbnail": {
                    "type": "boolean",
                    "description": (
                        "Return a thumbnail image alongside the stats. "
                        "Only set true when visual inspection is necessary; "
                        "omit or set false to save vision token cost (default false)."
                    ),
                    "default": False,
                },
                "thumbnail_size": {
                    "type": "integer",
                    "description": "Max pixel dimension of the thumbnail (default 512).",
                    "default": 512,
                },
                "display": {
                    "type": "boolean",
                    "description": (
                        "Show the snapped image in the MM viewer (default true). "
                        "Set false for a headless snap when display churn is unwanted."
                    ),
                    "default": True,
                },
                "region": {
                    # Both types at the top level, with the array shape kept.
                    # A oneOf with no top-level "type" made the model quote the
                    # array -- four refused calls on the Nikon, 2026-08-19.
                    "type": ["array", "string"],
                    "items": {"type": "integer"},
                    "minItems": 4,
                    "maxItems": 4,
                    "description": (
                        "Optional software analysis region. Either an ARRAY of "
                        "four integers [x, y, w, h] in current frame pixels — "
                        "unquoted, e.g. [726, 591, 174, 171] — or the string "
                        "\"drawn\", which reads the rectangle currently drawn on "
                        "the Micro-Manager Preview window. All statistics use "
                        "this region."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "find_features",
        "description": (
            "Snap and return NUMBERS about the field: spot count (blob detection), "
            "the intensity-weighted centroid of all signal and its offset from "
            "the field centre, the brightest detected punctum "
            "(brightest_feature_xy_px / brightest_feature_offset_px, both null "
            "when nothing was detected), background level, SNR, and "
            "spot_density_per_um2 (the SMLM blinking-density check). Use this — "
            "not a thumbnail — whenever you need to answer 'is the feature "
            "centred?', 'is there anything here?', or 'is the blinking density "
            "right?'. Deterministic and identical on every call. "
            "centering_move_um, when calibrated, is the RELATIVE STAGE MOVE that "
            "would centre the brightest punctum — pass it to move_stage_xy as-is "
            "with absolute=false, do not negate it — not a distance, and not "
            "about the aggregate centroid. Prefer center_feature, which applies "
            "it in a closed loop."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "min_sigma": {
                    "type": "number",
                    "description": "Smallest blob scale in px (default 1.0).",
                    "default": 1.0,
                },
                "max_sigma": {
                    "type": "number",
                    "description": "Largest blob scale in px (default 4.0).",
                    "default": 4.0,
                },
                "threshold_rel": {
                    "type": "number",
                    "description": "Relative blob detection threshold, 0-1 (default 0.15).",
                    "default": 0.15,
                },
            },
            "required": [],
        },
    },
    {
        "name": "center_feature",
        "description": (
            "Closed loop that centres the brightest DETECTED punctum in the field "
            "of view: find_features → that punctum's pixel offset → stage-camera "
            "affine → guarded stage move → repeat, until the residual is below "
            "tol_px or max_iter is reached. Uses Micro-Manager's own "
            "PixelSizeAffine when the rig publishes one (adopting it into the "
            "knowledge base), else a cached calibrate_stage_to_camera "
            "measurement; if neither exists it refuses and asks you to run "
            "calibrate_stage_to_camera. Refuses without moving when no punctum is "
            "detected — a gradient or extended structure is not a centring "
            "target, so use find_features to check n_spots first on a "
            "filamentous or confluent field. Use this instead of manually "
            "nudging the stage and re-snapping."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_iter": {
                    "type": "integer",
                    "description": "Maximum correction moves (default 3).",
                    "default": 3,
                },
                "tol_px": {
                    "type": "number",
                    "description": "Acceptable residual offset in pixels (default 5).",
                    "default": 5.0,
                },
            },
            "required": [],
        },
    },
    {
        "name": "run_autofocus",
        "description": (
            "Run a software autofocus sweep to find the sharpest Z plane. "
            "A probe reads a device property at each plane instead of the camera; "
            "use it when the rig has a hardware focus lock. When get_focus_lock_state "
            "reports a Nikon Perfect Focus System (PFS) as the lock device, call "
            "load_skill(name=\"nikon-pfs\") before engaging or adjusting it. "
            "Without a probe the sweep "
            "maximises image sharpness, which finds the sharpest plane, not "
            "necessarily the sample plane. method='sweep' is right with a probe: "
            "the coarse pass exists to save exposures and property reads spend none. "
            "Sweeps either the explicit z_min_um/z_max_um window or the window "
            "from (current_z - z_range_um/2) to (current_z + z_range_um/2) "
            "in z_step_um steps. Returns BOTH passes (coarse chooses the plane, fine "
            "refines it) with their metric curves and contrast, plus converged/moved/"
            "entry_z_um/final_z_um. If the metric curve is structureless (low "
            "contrast — e.g. faint signal or structure diluted by a mostly "
            "background field), restrict the metric region around structure. "
            "The stage is NOT "
            "moved: Z is restored to entry_z_um and converged=false explains why. "
            "A peak pinned at the sweep boundary is also NOT convergence — it "
            "may mean focus is outside the window or that the curve is invalid, "
            "so Z is restored for inspection. The metric (Tenengrad, mean squared "
            "image gradient) peaks at focus and is polarity-insensitive, so it "
            "applies to bright-on-dark puncta as well as dark-on-bright "
            "structure. It is not illumination-normalised, so do not change "
            "laser power, exposure or ROI part-way through a sweep. "
            "The sweep is headless: live view is paused for its duration and "
            "restored afterwards, and the viewer does not show the sweep as it happens. "
            "Default parameters for a 20× objective: z_range_um=20, z_step_um=0.5. "
            "Widen z_range_um if the result says the peak was at the boundary. "
            "If the focus is not converging, check if there are any sharp boundaries in the image. If so, alert the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "z_range_um": {
                    "type": "number",
                    "description": "Total Z sweep range in µm, centred on current Z.",
                },
                "z_min_um": {
                    "type": "number",
                    "description": "Explicit lower Z sweep bound in µm. Supply z_min_um AND z_max_um TOGETHER, and then do NOT supply z_range_um -- the two forms are alternatives and passing both is refused. Use this form to search a span you have not searched yet, rather than moving the stage and computing a half-width around it.",
                },
                "z_max_um": {
                    "type": "number",
                    "description": "Explicit upper Z sweep bound in µm; supply with z_min_um instead of z_range_um.",
                },
                "z_step_um": {
                    "type": "number",
                    "description": (
                        "Z step in µm. Size it to the capture range you expect: "
                        "a step that is too fine costs time, while one that is "
                        "too coarse can step over the band entirely."
                    ),
                },
                "method": {
                    "type": "string",
                    "description": "'coarse_then_fine' (default) or 'sweep'.",
                    "default": "coarse_then_fine",
                },
                "settle_ms": {
                    "type": "integer",
                    "description": "Camera settle after each Z move for image-based autofocus, in ms (default 50). Does not control a property probe.",
                    "default": 50,
                },
                "return_thumbnail": {
                    "type": "boolean",
                    "description": "Include a thumbnail of the focused image (default true). It is automatically suppressed for a zero-exposure property probe.",
                    "default": True,
                },
                "region": {
                    # Both types at the top level, with the array shape kept.
                    # A oneOf with no top-level "type" made the model quote the
                    # array -- four refused calls on the Nikon, 2026-08-19.
                    "type": ["array", "string"],
                    "items": {"type": "integer"},
                    "minItems": 4,
                    "maxItems": 4,
                    "description": (
                        "Optional software focus-metric region. Either an ARRAY "
                        "of four integers [x, y, w, h] in current frame pixels — "
                        "unquoted, e.g. [726, 591, 174, 171] — or the string "
                        "\"drawn\", which reads the rectangle currently drawn on "
                        "the Micro-Manager Preview window."
                    ),
                },
                "probe": {
                    "type": "object",
                    "description": (
                        "Optional. Read a device property at each plane instead of "
                        "measuring image sharpness. Use this for a hardware focus "
                        "lock that reports its capture range. Costs no exposures; "
                        "omit for ordinary image-based autofocus."
                    ),
                    "properties": {
                        "device": {"type": "string", "description": "Device label."},
                        "property": {"type": "string", "description": "Property read at each plane."},
                        "in_focus_values": {
                            "type": "array", "items": {"type": "string"},
                            "minItems": 1,
                            "description": (
                                "Values that mean in range. Copy them exactly; "
                                "where the device enumerates its values, one it "
                                "never reports is refused before any Z move. "
                                "Where it enumerates NOTHING you cannot look them "
                                "up, so pass your best guess and run the sweep: if "
                                "no plane matches, the refusal lists every value "
                                "the sweep actually observed, which names the "
                                "right spelling at zero exposures. Never step Z by "
                                "hand to discover them. Name "
                                "only STEADY states, never a transient one that "
                                "appears while the device is settling or being "
                                "engaged: the sweep stops at the first plane that "
                                "matches, so a transient stops it at whatever "
                                "plane happened to be under way, and holding for "
                                "the settle interval does not make a transient a "
                                "steady state. Omit only for a numeric property, "
                                "which is maximised."
                            ),
                        },
                        "stop_when_found": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "For a categorical probe, stop at the first stable "
                                "in_focus_values reading and leave Z there (default "
                                "true). Set false to sweep the complete window, "
                                "validate the band shape, and move to its centre. "
                                "Not valid for a numeric probe."
                            ),
                        },
                        "dwell_ms": {
                            "type": "number",
                            "minimum": 0,
                            "default": 0,
                            "description": (
                                "Extra wait after the stage settles before each "
                                "property read, for a property that updates slower "
                                "than the stage settles (default 0). This is not a "
                                "camera settle."
                            ),
                        },
                    },
                    "required": ["device", "property"],
                },
                "image_metric_reason": {
                    "type": "string",
                    "description": (
                        "Auditable caller assertion saying why the image metric "
                        "is the right instrument for this autofocus call. Supply "
                        "a non-empty reason to proceed after the hardware lock is "
                        "offered first — for example, the operator asked for an "
                        "image-based focus, or a property probe reported no band "
                        "at this XY. Recorded as a caller assertion, not an "
                        "instrument measurement."
                    ),
                },
            },
            # No top-level oneOf/allOf/anyOf. The Messages API rejects the whole
            # request -- "input_schema does not support oneOf, allOf, or anyOf at
            # the top level" -- which means NO tools load and the session cannot
            # start. Block 56b expressed the either/or that way and took the rig
            # down on the first prompt of a gate. The constraint is stated in the
            # descriptions and enforced by run_autofocus's own refusals, which
            # answer with a reason the model can act on. See
            # test_no_tool_schema_uses_a_top_level_combinator.
            "required": ["z_step_um"],
        },
    },
    {
        "name": "mark_position",
        "description": (
            "Mark a stage position. It is stored in microclaw's list and mirrored "
            "into MM's PositionList, so it appears immediately in the MM GUI's "
            "Position List Manager. Pass x_um/y_um to record a KNOWN coordinate "
            "WITHOUT moving there and WITHOUT imaging it — never re-run an "
            "acquisition just to get positions into the list. Omit them to mark "
            "wherever the stage currently sits, e.g. after the biologist has "
            "navigated to a site of interest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Label for this position (e.g. 'cell_1', 'Pos001').",
                },
                "include_z": {
                    "type": "boolean",
                    "description": (
                        "Also save the current Z position (default true). Ignored "
                        "when x_um/y_um are supplied; pass z_um to set Z explicitly."
                    ),
                    "default": True,
                },
                "x_um": {
                    "type": "number",
                    "description": (
                        "Known X (µm) to record without moving or imaging. Requires "
                        "y_um. Omit both to use the current stage position."
                    ),
                },
                "y_um": {
                    "type": "number",
                    "description": "Known Y (µm) to record without moving or imaging. Requires x_um.",
                },
                "z_um": {
                    "type": "number",
                    "description": (
                        "Optional known Z (µm) to record alongside x_um/y_um, again "
                        "without moving there."
                    ),
                },
                "preserve_unsupported": {
                    "type": "boolean",
                    "description": (
                        "After the user explicitly approves, preserve native entries "
                        "for unconfigured devices while omitting them from this operation."
                    ),
                    "default": False,
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_position_list",
        "description": (
            "Return all positions currently in MM's native position list, "
            "including any positions added via the MM GUI."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "go_to_position",
        "description": "Move the stage to a named position from MM's native position list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Position label."},
                "preserve_unsupported": {
                    "type": "boolean",
                    "description": "Retry after explicit approval to preserve and omit unsupported-device entries.",
                    "default": False,
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "delete_position",
        "description": "Delete a named position from MM's native position list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Position label to delete."},
                "confirm_conflict_resolution": {
                    "type": "boolean",
                    "description": "Set only after the user asks to delete this conflicted native entry.",
                    "default": False,
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "clear_position_list",
        "description": "Clear all positions from MM's native position list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "confirm_conflict_resolution": {
                    "type": "boolean",
                    "description": "Set only after the user asks to clear an inconsistent native list.",
                    "default": False,
                }
            },
            "required": [],
        },
    },
    {
        "name": "save_position_list",
        "description": (
            "Save MM's current native position list to an interoperable .pos file. "
            "If the path does not end in .pos, the suffix is appended."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Output .pos file path."}
            },
            "required": ["path"],
        },
    },
    {
        "name": "load_position_list",
        "description": (
            "Transactionally load a native Micro-Manager position-list file into "
            "both microclaw and MM's Position List Manager."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the position list file."},
                "preserve_unsupported": {
                    "type": "boolean",
                    "description": "Retry after explicit approval to preserve unsupported-device entries natively and omit them from microclaw navigation.",
                    "default": False,
                },
                "remove_conflicting_indexes": {
                    "type": "array", "items": {"type": "integer"},
                    "description": "Native indexes explicitly approved for removal from the loaded candidate. Requires expected_content_hash.",
                },
                "expected_content_hash": {
                    "type": "string",
                    "description": "Hash returned with the reviewed conflict; prevents acting on a changed file.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "import_mm_positions",
        "description": (
            "Import positions from Micro-Manager's GUI Position List Manager into the "
            "agent's internal position store. Call this after the user has set up "
            "positions in the MM GUI. The imported positions are then available for "
            "mark_position, get_position_list, go_to_position, and all acquisition tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "preserve_unsupported": {
                    "type": "boolean",
                    "description": "Retry after explicit approval to preserve and omit unsupported-device entries.",
                    "default": False,
                }
            },
            "required": [],
        },
    },
    {
        "name": "run_multiposition_acquisition",
        "description": (
            "Run a multiposition protocol (snap, zstack, or timelapse). Without hook_strategy, "
            "the default completes each position's protocol before moving to the next. "
            "PROTOCOL CHOICE: when the deliverable is per-position NUMBERS (max/min/"
            "mean intensity, focus metric), protocol='snap' already returns them for "
            "every position — writing nothing to disk is correct when nothing was "
            "asked to be saved. zstack/timelapse write datasets and return NO image "
            "statistics; reach for them only when data must land on disk. "
            "Supply either position_names (labels already in the MM position list) OR positions "
            "(a list of {name, x_um, y_um, z_um?} dicts — no prior mark_position needed). "
            "The hookless default saves each position's data to a subdirectory of save_dir. "
            "Pass mark_positions=true to also record every visited position into the "
            "stage position list. "
            "Prefer one call for a tiled acquisition; do not also run a second per-position form "
            "unless the user explicitly asks for both, because doing both repeats every exposure. "
            "Any route that writes one dataset per position — the hookless default, and a hooked "
            "run with interval_s>0 — produces separate datasets that CANNOT be passed to "
            "build_stage_coordinate_mosaic, which mosaics one dataset's own `position` axis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "acquisition_order": {
                    "type": "string",
                    "enum": ["position_then_time", "time_then_position"],
                    "default": "position_then_time",
                    "description": "Finish each field's movie by default, with or without hooks. At interval_s>0 each field gets its own acquisition, dataset and clock, paying startup and settling. Hooked interval_s=0 shares a position-axis dataset and log. time_then_position interleaves on shared_timepoint_clock; inapplicable to snap and zstack.",
                },
                "position_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Labels of positions already in the MM position list. "
                        "Use this OR positions, not both."
                    ),
                },
                "positions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "x_um": {"type": "number"},
                            "y_um": {"type": "number"},
                            "z_um": {"type": "number"},
                        },
                        "required": ["name", "x_um", "y_um"],
                    },
                    "description": (
                        "Raw XY(Z) coordinates to visit in order. "
                        "Use this OR position_names, not both."
                    ),
                },
                "protocol": {
                    "type": "string",
                    "description": (
                        "'snap', 'zstack', or 'timelapse'. 'snap' returns focus_metric "
                        "and mean/min/max intensity for every position — the right "
                        "choice whenever the request is per-position statistics; it "
                        "writes nothing to disk, which is the point, not a limitation, "
                        "when no saved data was requested. 'zstack'/'timelapse' write "
                        "datasets and return NO image statistics."
                    ),
                },
                "save_dir": {
                    "type": "string",
                    "description": (
                        "Root directory for saved data. Required for zstack/timelapse; "
                        "omit for display-only snap."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "Dataset name prefix (default 'multipos').",
                    "default": "multipos",
                },
                "protocol_params": _PROTOCOL_PARAMS_SCHEMA,
                "mark_positions": {
                    "type": "boolean",
                    "description": (
                        "Also record every visited position into the stage position list "
                        "(microclaw's list + MM's Position List Manager), as mark_position "
                        "would. Default false."
                    ),
                    "default": False,
                },
                "preserve_unsupported": {
                    "type": "boolean",
                    "description": "Retry after explicit approval to preserve and omit unrelated unsupported-device entries.",
                    "default": False,
                },
                "hook_strategy": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    ],
                    "description": (
                        "snr_observer measures every acquired frame without threshold actions, hardware changes or event submission; read_hook_log afterwards. "
                        "One hook name or an ordered list from list_hooks. Order is independent of hooks. "
                        "Zero-interval position-outer movies share one dataset and log; spaced ones use "
                        "a fresh hook and separate log per position. Results index those datasets and logs; read_hook_log takes one of them at a time. Explicit interleaving shares one "
                        "acquisition clock. Cannot be combined with snap. Fixed movie events are "
                        "submitted before images arrive; use run_adaptive_survey for stop-on-condition."
                    ),
                },
                "hook_params": {
                    "oneOf": [
                        {"type": "object"},
                        {"type": "array", "items": {"oneOf": [
                            {"type": "object"}, {"type": "null"}]}},
                    ],
                    "description": "For a hook list, an aligned list of objects or nulls.",
                },
                "log_path": {
                    "type": "string",
                    "description": (
                        "Path for the hook's output log, covering every position "
                        "(optional). Read it back with read_hook_log."
                    ),
                },
                "illumination_envelope": _HOOK_ILLUMINATION_ENVELOPE_SCHEMA,
                "artifact_limits": _HOOK_ARTIFACT_LIMITS_SCHEMA,
            },
            "required": ["protocol"],
        },
    },
    {
        "name": "run_tile_acquisition",
        "description": (
            "Acquire a rows×cols tile grid centered on center_x_um/center_y_um, "
            "defaulting to the current stage position. "
            "Computes grid coordinates automatically — no prior mark_position needed. "
            "To re-scan a grid you already ran, pass the center_x_um/center_y_um "
            "returned by that run (grid_center_x_um/grid_center_y_um); relying on "
            "the default center twice does NOT reproduce the same tiles unless the "
            "stage is back where it started. "
            "Runs a per-position protocol (snap, zstack, or timelapse) at each tile. "
            "PROTOCOL CHOICE: when the deliverable is per-tile NUMBERS (max/min/mean "
            "intensity, focus metric), protocol='snap' already returns them for every "
            "tile — writing nothing to disk is correct when nothing was asked to be "
            "saved. zstack/timelapse write datasets and return NO image statistics; "
            "reach for them only when data must land on disk. "
            "Pass mark_positions=true to also record every tile into the stage "
            "position list. "
            "Order is independent of hooks: position_then_time finishes each tile's movie; "
            "time_then_position interleaves tiles. Hooked runs with interval_s=0, and "
            "interleaved runs, use a single dataset with a `position` axis and one log. "
            "A hooked run with interval_s>0 uses a fresh acquisition clock, dataset and hook "
            "log per tile, and those separate datasets CANNOT be passed to "
            "build_stage_coordinate_mosaic. "
            "This is how you compute a custom per-tile quantity that snap does "
            "not already return — never spell a "
            "grid as N single-plane z-stacks. Not compatible with protocol='snap' "
            "(display-only, no acquisition images) — use protocol='timelapse' with "
            "protocol_params={'n_frames': 1, 'interval_s': 0} for one hooked frame "
            "per tile."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "acquisition_order": {
                    "type": "string",
                    "enum": ["position_then_time", "time_then_position"],
                    "default": "position_then_time",
                    "description": "Finish each field's movie by default, with or without hooks. At interval_s>0 each field gets its own acquisition, dataset and clock, paying startup and settling. Hooked interval_s=0 shares a position-axis dataset and log. time_then_position interleaves on shared_timepoint_clock; inapplicable to snap and zstack.",
                },
                "rows": {"type": "integer", "description": "Number of rows in the grid."},
                "cols": {"type": "integer", "description": "Number of columns in the grid."},
                "step_um": {"type": "number", "description": "Step size between tiles in µm."},
                "protocol": {
                    "type": "string",
                    "description": (
                        "'snap', 'zstack', or 'timelapse'. 'snap' returns focus_metric "
                        "and mean/min/max intensity for every tile — the right choice "
                        "whenever the request is per-tile statistics; it writes "
                        "nothing to disk, which is the point, not a limitation, when "
                        "no saved data was requested. 'zstack'/'timelapse' write "
                        "datasets and return NO image statistics."
                    ),
                },
                "save_dir": {
                    "type": "string",
                    "description": (
                        "Root directory for saved data. Required for zstack/timelapse; "
                        "omit for display-only snap."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Dataset and position-label prefix (default 'tile'); tiles are "
                        "labelled <name>_r<row>_c<col>."
                    ),
                    "default": "tile",
                },
                "protocol_params": _PROTOCOL_PARAMS_SCHEMA,
                "mark_positions": {
                    "type": "boolean",
                    "description": (
                        "Also record every tile position into the stage position list "
                        "(microclaw's list + MM's Position List Manager), as mark_position "
                        "would. Default false."
                    ),
                    "default": False,
                },
                "hook_strategy": {
                    "type": "string",
                    "description": (
                        "snr_observer measures every acquired frame without threshold actions, hardware changes or event submission; read_hook_log afterwards. "
                        "Hook strategy name (from list_hooks). Runs ONE acquisition "
                        "for each movie, preserving the selected acquisition_order. "
                        "Spaced position-outer movies have separate hooks and logs; otherwise "
                        "the grid shares one hook and log. Cannot be combined with snap. "
                        "Fixed movie events are submitted before frames arrive; "
                        "for stop-on-condition use run_adaptive_survey."
                    ),
                },
                "hook_params": {
                    "type": "object",
                    "description": "Parameters passed to the hook constructor.",
                },
                "log_path": {
                    "type": "string",
                    "description": (
                        "Optional hook log path. Spaced position-outer movies derive separate "
                        "collision-free log paths from it, indexed in results. Read each returned "
                        "log_path with read_hook_log."
                    ),
                },
                "center_x_um": {
                    "type": "number",
                    "description": (
                        "Absolute X of the grid center. Defaults to the current stage X. "
                        "Pass it to pin the grid to fixed coordinates — e.g. to re-measure "
                        "the exact tiles of an earlier scan."
                    ),
                },
                "center_y_um": {
                    "type": "number",
                    "description": (
                        "Absolute Y of the grid center. Defaults to the current stage Y."
                    ),
                },
                "return_to_center": {
                    "type": "boolean",
                    "description": (
                        "Drive the stage back to the grid center when the scan finishes, "
                        "so the grid does not walk forward across repeated runs. "
                        "Default true; set false to leave the stage on the last tile."
                    ),
                    "default": True,
                },
            },
            "required": ["rows", "cols", "step_um", "protocol"],
        },
    },
    {
        "name": "run_multiposition_with_autofocus",
        "description": (
            "DEPRECATED forwarding name. Use run_multiposition_acquisition with "
            "hook_strategy='autofocus_per_position' (or an ordered hook list). "
            "Zero-interval or interleaved runs write one dataset with a `position` axis. "
            "Spaced position-outer movies write one dataset per position with separate hook "
            "logs indexed in results; those datasets CANNOT be used as one position-axis acquisition. "
            "Display-only snap is not supported by this deprecated wrapper."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "acquisition_order": {
                    "type": "string",
                    "enum": ["position_then_time", "time_then_position"],
                    "default": "position_then_time",
                    "description": "Finish each field's movie by default, with or without hooks. At interval_s>0 each field gets its own acquisition, dataset and clock, paying startup and settling. Hooked interval_s=0 shares a position-axis dataset and log. time_then_position interleaves on shared_timepoint_clock; inapplicable to snap and zstack.",
                },
                "position_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Stored position labels. Use this OR positions.",
                },
                "positions": {
                    "type": "array",
                    "items": {"type": "object", "properties": {
                        "name": {"type": "string"}, "x_um": {"type": "number"},
                        "y_um": {"type": "number"}, "z_um": {"type": "number"}},
                        "required": ["name", "x_um", "y_um"]},
                    "description": "Raw XY(Z) coordinates. Use this OR position_names.",
                },
                "z_range_um": {
                    "type": "number",
                    "description": "Z sweep range for autofocus in µm.",
                },
                "z_step_um": {
                    "type": "number",
                    "description": "Fine step size for autofocus in µm.",
                },
                "protocol": {
                    "type": "string",
                    "description": "'snap', 'zstack', or 'timelapse'.",
                },
                "save_dir": {"type": "string", "description": "Root directory for saved data."},
                "name": {
                    "type": "string",
                    "description": "Dataset name prefix (default 'multipos_af').",
                    "default": "multipos_af",
                },
                "autofocus_method": {
                    "type": "string",
                    "description": "'coarse_then_fine' (default) or 'sweep'.",
                    "default": "coarse_then_fine",
                },
                "settle_ms": {
                    "type": "integer",
                    "description": "Wait time after each Z move in ms (default 50).",
                    "default": 50,
                },
                "protocol_params": _PROTOCOL_PARAMS_SCHEMA,
                "preserve_unsupported": {
                    "type": "boolean",
                    "description": "Retry after explicit approval to preserve and omit unrelated unsupported-device entries.",
                    "default": False,
                },
            },
            "required": ["z_range_um", "z_step_um", "protocol", "save_dir"],
        },
    },
    {
        "name": "run_adaptive_survey",
        "description": (
            "Acquire positions ONE AT A TIME, letting the hook decide after each "
            "frame whether the next position is acquired at all — the only tool "
            "that can stop an acquisition early based on the images (stop-on-"
            "condition, refine-where-interesting). Positions are visited in the "
            "order given; pass the list reversed for a reverse scan. Requires an "
            "ADAPTIVE hook, and the contract depends on where the hook came "
            "from. A SAVED or GENERATED hook implements analyze_frame(image, "
            "metadata) and returns a HookResult whose actions include "
            "ContinueAcquisition (acquire the next planned tile) or StopAcquisition (end "
            "the scan); the trusted parent, not the hook, dispatches them. It "
            "may also request one refocus of a promising tile when the caller "
            "provides an exposure-bounded autofocus_budget; the refocused frame "
            "is judged again with metadata['microclaw_refocused'] true. It "
            "never receives ctrl, guard, or a queue. A reviewed built-in from "
            "the pre-coded registry keeps the older direct contract: the runner "
            "sets hook.survey_events, hook.candidates and hook.progress, and the "
            "hook must candidates.put() the next tile OR call "
            "progress.done_early(), then progress.image_done() — see "
            "load_skill(name=\"hook-authoring\") ('Skipping and stopping'). Serializes the "
            "scan (each tile waits on the previous frame's scoring), so for a "
            "fixed survey that only reports per-tile numbers use "
            "run_tile_acquisition or run_multiposition_acquisition instead. "
            "The result reports frames_acquired and stopped_early, plus "
            "hook_actions only when typed actions were observed at the trusted "
            "parent dispatch (precoded hooks omit it rather than inventing "
            "counts). stopped_early describes control decisions, not what was "
            "found. When log_path is present, call read_hook_log(log_path) "
            "before making any claim about the per-tile measurements; otherwise "
            "no per-tile log was written. For 'search in one channel, acquire "
            "only detected tiles in another', pass acquire_on_hit: AcquireAt "
            "then records a tile and its current Z for one deferred second phase. "
            "Both the complete search dose and worst-case max_hits × frames-per-hit "
            "acquire dose are reserved before the first exposure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "protocol": {
                    "type": "string",
                    "description": (
                        "'zstack' or 'timelapse' — the shape acquired at each "
                        "position. 'snap' is not valid (no acquisition images); "
                        "use 'timelapse' with n_frames=1 for one frame per tile."
                    ),
                },
                "save_dir": {
                    "type": "string",
                    "description": "Directory to save the dataset.",
                },
                "hook_strategy": {
                    "type": "string",
                    "description": (
                        "snr_observer measures every acquired frame without threshold actions, hardware changes or event submission; read_hook_log afterwards. "
                        "Hook strategy name (from list_hooks). Must implement "
                        "the adaptive contract; snr_observer alone observes only the seed tile; a hook that never calls "
                        "candidates.put() acquires only the first tile."
                    ),
                },
                "position_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Labels of positions already in the MM position list. "
                        "Use this OR positions, not both."
                    ),
                },
                "positions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "x_um": {"type": "number"},
                            "y_um": {"type": "number"},
                        },
                        "required": ["name", "x_um", "y_um"],
                    },
                    "description": (
                        "Raw XY coordinates to visit in order. "
                        "Use this OR position_names, not both."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "Dataset name prefix (default 'survey').",
                    "default": "survey",
                },
                "protocol_params": _PROTOCOL_PARAMS_SCHEMA,
                "hook_params": {
                    "type": "object",
                    "description": "Parameters passed to the hook constructor.",
                },
                "log_path": {
                    "type": "string",
                    "description": (
                        "Path for the hook's output log (optional). "
                        "Read it back with read_hook_log."
                    ),
                },
                "max_idle_s": {
                    "type": "number",
                    "description": (
                        "Watchdog: end the survey if no frame arrives and no "
                        "event is submitted for this many seconds (default 60)."
                    ),
                    "default": 60.0,
                },
                "preserve_unsupported": {
                    "type": "boolean",
                    "description": "Retry after explicit approval to preserve and omit unrelated unsupported-device entries.",
                    "default": False,
                },
                "illumination_envelope": _HOOK_ILLUMINATION_ENVELOPE_SCHEMA,
                "named_stage_envelope": _HOOK_NAMED_STAGE_ENVELOPE_SCHEMA,
                "property_envelope": _HOOK_PROPERTY_ENVELOPE_SCHEMA,
                "artifact_limits": _HOOK_ARTIFACT_LIMITS_SCHEMA,
                "autofocus_budget": _ADAPTIVE_AUTOFOCUS_BUDGET_SCHEMA,
                "acquire_on_hit": {
                    "type": "object",
                    "description": (
                        "Deferred second channel acquisition. channel is applied once "
                        "after search; protocol is timelapse or zstack; protocol_params "
                        "contains n_frames/interval_s/exposure_ms or relative "
                        "z_offset_start_um/z_offset_end_um/z_step_um/exposure_ms; "
                        "max_hits deduplicates and bounds accepted AcquireAt actions. "
                        "Requires a saved or generated hook returning typed AcquireAt "
                        "actions; registry built-ins use the direct queue contract and "
                        "are refused. The operator is asked to authorize BOTH channels "
                        "before the first search frame, so they may approve enabling an "
                        "acquire channel that a zero-hit run never switches to."
                    ),
                    "properties": {
                        "channel": {"type": "string"},
                        "protocol": {"type": "string", "enum": ["timelapse", "zstack"]},
                        "protocol_params": {"type": "object"},
                        "max_hits": {"type": "integer", "minimum": 1},
                    },
                    "required": ["channel", "protocol", "protocol_params", "max_hits"],
                },
            },
            "required": ["protocol", "save_dir", "hook_strategy"],
        },
    },
    {
        "name": "open_artifact",
        "description": (
            "Open a file microclaw wrote — a mosaic or exported TIFF, or a saved "
            "acquisition dataset directory — on the user's screen, and verify it "
            "against the digests recorded when it was written. This is how you "
            "show the user a file: an image file opens in the ImageJ window "
            "Micro-Manager runs under, a dataset directory opens in "
            "Micro-Manager's own dataset viewer, and the result reports which "
            "windows appeared with their dimensions. Never tell the user to open "
            "a file in FIJI or the MM GUI; call this instead. The result's "
            "top-level `opened` is true only when a window really appeared; if "
            "it is false, `reason` says why and there is nothing on the user's "
            "screen to describe. Zero exposure; touches no hardware."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "The direct path to the image or dataset artifact to open."},
                "analyze": {"type": "boolean", "description":
                    "Also read the pixels into this conversation so you can "
                    "measure or describe them. Default false. Set it ONLY when "
                    "the user asked you to analyze, interpret, count, or check "
                    "the contents of the image. If they just want to look at it "
                    "— 'show me', 'open it' — leave it false: the file is "
                    "already on their screen, and rendering it also costs "
                    "context for every remaining turn of the session. Not "
                    "available for a dataset directory; export or mosaic the "
                    "frames you want first."},
                "axis_selection": {"type": "object", "description":
                    "With analyze, fixes the plane to render in a multi-plane "
                    "TIFF, e.g. {\"z\": 3}. Keys are the file's own stack axis "
                    "names; if you omit it for a stack, the refusal names the "
                    "exact keys and lengths to pass."},
                "max_size": {"type": "integer", "description":
                    "With analyze, longest rendered edge in px. Default 512."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_hook_log",
        "description": (
            "Read a hook's output log file after an acquisition completes. "
            "Use this to retrieve per-position autofocus results, focus corrections, "
            "intensity adjustments, or rejection events, and report them to the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "log_path": {"type": "string", "description": "Path to the hook log file."}
            },
            "required": ["log_path"],
        },
    },
    {
        "name": "rank_hook_log",
        "description": (
            "Deterministically rank completed observation records offline. No image "
            "analysis, acquisition, motion, thresholding, network, or adjudicator is "
            "used. The key is descending result metric with ascending position label "
            "as the tie-break. For surveys, prefer metric='signal_coverage' over "
            "snr: coverage measures field extent, while snr is a tail statistic. "
            "Coverage deliberately has no validity flag and retains every finite "
            "row; the returned rows carry the min_snr threshold and source used to "
            "compute it. Returns the full ordering and requested budget views."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "log_path": {"type": "string"},
                "metric": {"type": "string", "default": "snr", "description":
                    "Ranking statistic. Prefer signal_coverage for survey fields; "
                    "state another choice and why. Logs recorded before coverage "
                    "was added are refused rather than ranked silently."},
                "budgets": {"type": "array", "items": {"type": "integer"}},
                "position_list_path": {
                    "type": "string",
                    "description": "Optional saved list to verify against the ranking prefix."
                },
            },
            "required": ["log_path"],
        },
    },
    {
        "name": "validate_positions",
        "description": (
            "Check proposed XY/Z coordinates against the current safety guards without "
            "moving hardware. Returns accepted and rejected positions and never clips."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "positions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "x_um": {"type": "number"},
                            "y_um": {"type": "number"},
                            "z_um": {"type": "number"},
                        },
                        "required": ["name", "x_um", "y_um"],
                    },
                }
            },
            "required": ["positions"],
        },
    },
    {
        "name": "inspect_artifacts",
        "description": (
            "List or search a local folder the user named, to resolve an incomplete "
            "filename. Use this when the user says a file is in Downloads, Desktop, "
            "or another named folder but does not give its exact path. Returns "
            "absolute candidate paths; pass the chosen direct path to the consuming "
            "tool. With hash=true it also computes SHA-256 for provenance. Reads "
            "directory metadata only — never file contents unless hashing was asked "
            "for — and touches no hardware. Optionally saves a JSON manifest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"},
                          "description": "Explicit file or directory paths to inspect; no home or whole-machine search is inferred."},
                "name_glob": {"type": "string", "default": "*",
                              "description": "Case-insensitive glob matched against basenames only. Must not contain /, \\, or ..."},
                "recursive": {"type": "boolean", "default": True,
                              "description": "Search child directories. False returns the complete top-level scope and is not truncation."},
                "manifest_path": {"type": "string", "description":
                    "Write the provenance result as JSON. A hash=true call or "
                    "a call supplying manifest_path refuses if traversal is "
                    "incomplete, and writes no partial manifest."},
                "max_files": {"type": "integer", "minimum": 1, "default": 1000,
                              "description":
                    "Maximum regular files examined, not matches returned. In "
                    "discovery, empty matches with truncated=true and "
                    "examined_count means not found within the bound, not that "
                    "the requested scope contains no matching file."},
                "max_total_bytes": {"type": "integer", "minimum": 1,
                                    "default": 1073741824},
                "max_depth": {"type": "integer", "minimum": 1, "default": 16},
                "hash": {"type": "boolean", "default": True,
                         "description": "False returns a bounded listing without reading file contents."},
            },
            "required": ["paths"],
        },
    },
    {
        "name": "compare_revisit_frames",
        "description": (
            "Offline subpixel registration of corresponding pages in source and "
            "revisit TIFF stacks. Reports pixel translation, registration quality, "
            "and micrometres only when a current stage-camera affine exists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_tiff": {"type": "string"},
                "revisit_tiff": {"type": "string"},
                "comparisons": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "position": {"type": "string"},
                            "source_index": {"type": "integer"},
                            "revisit_index": {"type": "integer"},
                        },
                        "required": ["position", "source_index", "revisit_index"],
                    },
                },
                "min_correlation": {"type": "number", "default": 0.5},
            },
            "required": ["source_tiff", "revisit_tiff", "comparisons"],
        },
    },
    {
        "name": "calibrate_snr_threshold",
        "description": (
            "Create a provisional SNR validity-gate artifact from multiple confirmed "
            "dark and illuminated hook logs. Requires separated distributions and "
            "records the optical/acquisition context supplied by the operator."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dark_log_paths": {"type": "array", "items": {"type": "string"}},
                "illuminated_log_paths": {"type": "array", "items": {"type": "string"}},
                "output_path": {"type": "string"},
                "context": {
                    "type": "object",
                    "description": "Objective, camera, ROI, binning, exposure, and channel identity."
                },
            },
            "required": ["dark_log_paths", "illuminated_log_paths", "output_path", "context"],
        },
    },
    {
        "name": "list_hooks",
        "description": (
            "List all available hook strategies: pre-coded hooks and previously saved hooks "
            "with resolvable status, every resolve refusal reason, and its remedy. Never "
            "attach an entry whose resolvable field is false. Call describe_hook with a strategy name "
            "to discover constructor parameters and resolve-time compatibility before an "
            "acquisition."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "describe_hook",
        "description": (
            "Describe a pre-coded or saved hook: class and docstring, constructor "
            "parameters and defaults, callback, resolve-time refusal, stripped or "
            "injected parameters, and saved-source integrity provenance. Saved source "
            "is parsed without importing or executing it, including when changed or unpinned."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Hook strategy name from list_hooks."}
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_mm_plugins",
        "description": (
            "List installed Micro-Manager plugins grouped by role (autofocus, processor, "
            "menu) so a human can review classpaths before enabling a plugin-backed hook. "
            "Analyzer plugins run via hook_strategy='mm_plugin_analyzer' (allowed unless "
            "listed in plugins.blocked); autofocus plugins run via "
            "hook_strategy='autofocus_mm_plugin', which is permitted by default but "
            "requires explicit user confirmation before enabling; microclaw guards "
            "only the resulting position, not the plugin's motion itself. "
            "Requires a Micro-Manager build with the unified "
            "plugin classloader (PR #2401). Always confirm the classpath with the user "
            "before enabling a plugin hook."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "load_skill",
        "description": (
            "Load one repository-owned workflow skill by its exact catalog name. "
            "The result is procedural guidance only: it reads no hardware, changes no "
            "state, and grants no authority."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact skill name from the generated system-prompt catalog.",
                }
            },
            "required": ["name"],
        },
    },
    {
        "name": "check_emu_installed",
        "description": (
            "Detect whether EMU and/or htSMLM are installed by scanning the Micro-Manager "
            "plugins directory for their JAR files. "
            "Returns emu_installed, htsmlm_installed, htsmlm_configured, the MM app "
            "directory found, and the "
            "names of any plugin JARs discovered. "
            "After positive plugin/configuration detection, load_skill(name=\"htsmlm\") and "
            "use get_emu_configuration. Also load that skill when the operator identifies the "
            "system or workflow as htSMLM or EMU, even if the application directory cannot be "
            "located. Never load it merely because the catalog lists it."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_emu_configuration",
        "description": (
            "Read the EMU configuration and return the AUTHORITATIVE structured map "
            "from htSMLM semantic names to Micro-Manager devices/properties: 'lasers' "
            "(slot index → its name and own enable, power and trigger lines), "
            "'filter_wheels' (ordinal → named slot/value table), 'focus_lock', "
            "'other', and 'unallocated' "
            "(names only). On an EMU/htSMLM rig, call this BEFORE list_device_properties "
            "or any device probing — never infer a laser/filter/trigger index from "
            "device naming order. "
            "Only call this if check_emu_installed has confirmed EMU is installed, "
            "OR if the user has explicitly mentioned htSMLM or EMU. In either case, "
            "load_skill(name=\"htsmlm\") before operating the specialized workflow. "
            "Auto-detects the Micro-Manager installation directory from common platform paths "
            "and a local cache (~/.microclaw/emu.json). If auto-detection fails, returns an "
            "error with instructions; call again with mm_app_dir set to the correct path and "
            "it will be saved for future calls."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mm_app_dir": {
                    "type": "string",
                    "description": (
                        "Absolute path to the Micro-Manager installation directory "
                        "(the one that contains the EMU/ subfolder). "
                        "Omit to use auto-detection or cached path."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_emu_laser_map",
        "description": (
            "Return the EMU slot → laser table: for each slot index, that laser's "
            "enable, power and trigger (mode/sequence) device-properties. htSMLM "
            "indexes 'Laser i …' and 'Laser trigger i …' by the same physical slot, "
            "so always verify the trigger line on the SAME slot you enable — never "
            "infer a slot index from device naming order. Errors on non-EMU rigs."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "resolve_emu_device",
        "description": (
            "Resolve an EMU semantic UIProperty name (e.g. 'Laser 3 enable', "
            "'Filter wheel position') to its Micro-Manager {device, property} pair. "
            "Accepts exact UIProperty keys or configured names such as '640' and 'BFP'. "
            "A laser name returns its whole paired slot record. Use this instead of "
            "guessing which device backs an htSMLM control."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "semantic_name": {
                    "type": "string",
                    "description": "EMU UIProperty name as shown by get_emu_configuration.",
                },
            },
            "required": ["semantic_name"],
        },
    },
    {
        "name": "get_focus_lock_state",
        "description": (
            "Read whether the hardware focus lock (external sensor / QPD) is engaged, "
            "using the generic Micro-Manager autofocus device, or the EMU map when "
            "one exists, plus current QPD readings where available. If the returned "
            "device is a Nikon Perfect Focus System (PFS), call "
            "load_skill(name=\"nikon-pfs\") before engaging or adjusting it — identify "
            "the lock by this device value, never by a property name that happens to "
            "contain 'PFS'. A sharp "
            "image is NOT evidence that the lock is engaged — always answer the SMLM "
            "checklist's focus-lock item with this tool. Returns engaged=null on rigs "
            "with no configured or readable focus-lock device."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_focus_lock",
        "description": (
            "Engage or disengage the hardware focus lock through Micro-Manager's "
            "configured autofocus device (or the EMU map when present). Disengage before running a "
            "software autofocus sweep (which would otherwise fight the servo loop), "
            "and re-engage afterwards — run_autofocus refuses to run while it is on. "
            "When get_focus_lock_state reports a Nikon Perfect Focus System (PFS) as "
            "the lock device, call load_skill(name=\"nikon-pfs\") before engaging or "
            "adjusting it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "True to engage the lock, False to disengage it.",
                },
            },
            "required": ["enabled"],
        },
    },
    {
        "name": "generate_and_save_hook",
        "description": (
            "Validate and save a hook script to disk. Refuses before writing if the source "
            "would be refused at run time, returning every reason and the required fix. "
            "IMPORTANT: Call this ONLY after showing the full code to the user and receiving "
            "explicit confirmation. Runs an AST safety scan before saving. "
            "source must be 'claude_generated' or 'user_provided'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Hook name (used as filename)."},
                "code": {"type": "string", "description": "Full Python source code of the hook."},
                "description": {
                    "type": "string",
                    "description": "One-sentence description of what the hook does.",
                },
                "source": {
                    "type": "string",
                    "description": "'claude_generated' or 'user_provided'.",
                    "default": "claude_generated",
                },
                "runner_contract": {
                    "type": "string",
                    "enum": ["fixed", "adaptive"],
                    "description": (
                        "Static callback contract: fixed accepts live callbacks or saved offline "
                        "adapters for run_analysis_on_saved_dataset; adaptive additionally "
                        "requires analyze_frame. Saving an offline adapter does not make it "
                        "a live acquisition hook."
                    ),
                    "default": "fixed",
                },
            },
            "required": ["name", "code", "description"],
        },
    },
    {
        "name": "verify_emu_laser_power_calibration",
        "description": (
            "Verify an EMU laser's raw = slope * GUI-percent + offset calibration "
            "against two distinct known GUI settings. Required once per session "
            "before semantic percentage writes are allowed. Keep illumination disabled."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slot": {"type": "integer", "description": "EMU laser slot index."},
                "observations": {
                    "type": "array", "minItems": 2,
                    "items": {"type": "object", "properties": {
                        "percent": {"type": "number"}, "raw_value": {"type": "integer"}
                    }, "required": ["percent", "raw_value"]}
                },
            },
            "required": ["slot", "observations"],
        },
    },
    {
        "name": "get_emu_laser_power_percentage",
        "description": (
            "Read an EMU laser power property and report commanded raw, interpreted "
            "percentage, calibration status, and unavailable measured/GUI states separately."
        ),
        "input_schema": {"type": "object", "properties": {
            "slot": {"type": "integer"}}, "required": ["slot"]},
    },
    {
        "name": "set_emu_laser_power_percentage",
        "description": (
            "Set an EMU laser by semantic GUI percentage using the code-owned affine "
            "conversion. Refuses until two-point calibration verification and reports "
            "requested, raw, effective, representability, and minimum nonzero percentage. "
            "Review the effective value before enabling illumination."
        ),
        "input_schema": {"type": "object", "properties": {
            "slot": {"type": "integer"}, "percent": {"type": "number", "minimum": 0}
        }, "required": ["slot", "percent"]},
    },
    {
        "name": "get_album_state",
        "description": "Read the current Micro-Manager Album datastore state; this is not a disk dataset or montage.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "snap_to_album",
        "description": (
            "Snap with MMStudio and add every returned camera image directly to its "
            "Album, preserving MM metadata and GUI visibility. This creates independent "
            "Album snaps, not a contact sheet, spatial mosaic, stitch, or TIFF."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_mda_settings",
        "description": (
            "Inspect and preview MMStudio's current GUI MDA settings. Returns a token "
            "required by run_mda; it does not construct pycro-manager events."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "run_mda",
        "description": (
            "Run exactly the MMStudio GUI MDA settings most recently previewed by "
            "get_mda_settings. Refuses stale previews and requires blocking human "
            "confirmation because current settings may move hardware, illuminate, or save."
        ),
        "input_schema": {"type": "object", "properties": {
            "preview_token": {"type": "string"}}, "required": ["preview_token"]},
    },
    {
        "name": "read_hook_from_file",
        "description": (
            "Read a user-specified hook file and run the AST safety scan. "
            "Returns the code and any warnings for review. Does NOT save — "
            "call generate_and_save_hook(source='user_provided') after user confirms."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the hook Python file."}
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_device_properties",
        "description": (
            "List all property names exposed by a loaded Micro-Manager device. "
            "Call this when you encounter a device whose properties are not known. "
            "Follow up with get_device_property_info to learn each property's type "
            "and valid values before calling set_device_property."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "description": "Device name (from list_devices).",
                },
            },
            "required": ["device"],
        },
    },
    {
        "name": "get_device_property_info",
        "description": (
            "Return metadata for a single Micro-Manager device property: "
            "its current value, data type (String/Float/Integer), whether it is "
            "read-only or pre-init only, the list of allowed values for enum "
            "properties, numeric limits, and the exact pair's read-only "
            "authorization classification. The authorization result says whether "
            "a raw write is admitted, explicitly excluded, admitted only through "
            "an approved envelope, or blocked because the device carries declared "
            "stage bounds. Always call this before "
            "set_device_property on a property you have not used before."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Device name."},
                "property": {"type": "string", "description": "Property name."},
            },
            "required": ["device", "property"],
        },
    },
    {
        "name": "get_full_device_state",
        "description": (
            "Return the current value of every property of a Micro-Manager device "
            "in a single call. Use this to orient yourself about an unknown device "
            "or to report its complete state to the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "description": "Device name (from list_devices).",
                },
            },
            "required": ["device"],
        },
    },
    {
        "name": "save_knowledge",
        "description": (
            "Save a fact about this rig, a sample, device, or imaging strategy to "
            "the user's persistent knowledge base (~/.microclaw/knowledge.yaml). "
            "This information is loaded automatically in future sessions. "
            "Only call after the user has confirmed they want it saved."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["rig", "samples", "devices", "strategies"],
                    "description": (
                        "'rig' for installation-wide facts such as the illuminated "
                        "field, illumination path, calibration status, device roles, "
                        "and emission filters; 'samples' for sample/specimen profiles, "
                        "'devices' for non-standard hardware mappings and roles, "
                        "'strategies' for named imaging recipes."
                    ),
                },
                "key": {
                    "type": "string",
                    "description": (
                        "Short identifier for the entry, e.g. 'U2OS_actin_Alexa647' "
                        "or 'Thorlabs-ELL-9'. Use underscores, no spaces. "
                        "For category 'rig' the key must be one of the profile "
                        "topics — illuminated_field, illumination_path, "
                        "calibration, device_roles, emission_filters — and any "
                        "other key is refused; put extra detail inside that "
                        "topic's value."
                    ),
                },
                "value": {
                    "type": "object",
                    "description": (
                        "Structured data for the entry. Use descriptive field names. "
                        "For entries other than an optical-path position map, include a "
                        "'description' field summarizing the entry. For an optical-path "
                        "position map, supply exactly the structured "
                        "shape {'kind': 'optical_path_position_map', 'device': <StateDevice "
                        "config label>, 'positions': {<exact state label>: <operator "
                        "meaning>, ...}}. The caller supplies kind, device, and positions; "
                        "save_knowledge resolves observed_on from live identity, so do not "
                        "send observed_on."
                    ),
                    "additionalProperties": True,
                },
            },
            "required": ["category", "key", "value"],
        },
    },
    {
        "name": "get_knowledge",
        "description": (
            "Retrieve entries from the user's persistent knowledge base "
            "(~/.microclaw/knowledge.yaml). Use at the start of a session involving "
            "a named sample or unfamiliar device to recall stored profiles and notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["rig", "samples", "devices", "strategies"],
                    "description": (
                        "Category to retrieve. 'rig' contains installation-wide facts "
                        "such as the illuminated field, illumination path, calibration "
                        "status, device roles, and emission filters. Omit to return all "
                        "categories."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "delete_knowledge",
        "description": "Remove a single entry from the user's persistent knowledge base.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["rig", "samples", "devices", "strategies"],
                    "description": (
                        "Use 'rig' for installation-wide facts such as the illuminated "
                        "field, illumination path, calibration status, device roles, "
                        "and emission filters."
                    ),
                },
                "key": {
                    "type": "string",
                    "description": "The key of the entry to remove.",
                },
            },
            "required": ["category", "key"],
        },
    },
]

# Add cache_control on the last tool so the entire tool list is cached.
TOOLS_CACHED = [*TOOLS[:-1], {**TOOLS[-1], "cache_control": {"type": "ephemeral"}}]
