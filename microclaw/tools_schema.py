from typing import Any

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
            "loudly rather than being reconstructed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "output_path": {"type": "string"},
                "tool_use_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional recorded tool_use ids to emit. Excluded calls "
                        "remain visible as SKIPPED comments; dependencies are not inferred."
                    ),
                },
            },
            "required": ["output_path"],
        },
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
            "one if refused. Returns requested vs achieved position and the "
            "settling error."
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
            "shutter and per-slot laser state. "
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
            "Run a fixed Z-stack, plain or with an optional hook. A hook may adapt "
            "settings between planes, or measure every plane without changing the "
            "acquisition; for observation use snr_observer and call read_hook_log "
            "afterwards. Hooks cannot skip planes or stop early; use "
            "run_adaptive_survey for stop-on-condition work. Call list_hooks() "
            "to include saved hooks. Hardware-moving plugin hooks require both "
            "plugins.allow_hardware_motion: true and property_authorization.mode: "
            "degraded_trusted_plugins, plus a restart."
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
                "hook_strategy": {"type": "string", "description": "Optional hook from list_hooks."},
                "hook_params": {"type": "object", "description": "Hook constructor parameters."},
                "log_path": {"type": "string", "description": "Hook output log path."},
                "illumination_envelope": _HOOK_ILLUMINATION_ENVELOPE_SCHEMA,
                "artifact_limits": _HOOK_ARTIFACT_LIMITS_SCHEMA,
            },
            "required": ["z_start_um", "z_end_um", "z_step_um", "save_dir"],
        },
    },
    {
        "name": "run_timelapse",
        "description": (
            "Run a fixed timelapse, plain or with an optional hook. A hook may adapt "
            "settings between frames, or measure every frame without changing the "
            "acquisition; for observation use snr_observer and call read_hook_log "
            "afterwards. Hooks cannot skip frames or stop early; use "
            "run_adaptive_survey for stop-on-condition work. Call list_hooks() "
            "to include saved hooks. Hardware-moving plugin hooks require both "
            "plugins.allow_hardware_motion: true and property_authorization.mode: "
            "degraded_trusted_plugins, plus a restart. "
            "On an EMU/htSMLM rig, pass laser_slot "
            "(the EMU slot of the excitation laser, from get_emu_laser_map) so the "
            "pre-flight can verify that laser's trigger line will actually fire — "
            "otherwise a gated-off laser silently produces blank frames."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "n_frames": {"type": "integer", "description": "Number of frames."},
                "interval_s": {"type": "number", "description": "Interval between frames in seconds."},
                "channel": {"type": "string", "description": "Channel preset (optional)."},
                "exposure_ms": {"type": "number", "description": "Exposure in ms (optional)."},
                "save_dir": {"type": "string"},
                "name": {"type": "string", "default": "timelapse"},
                "laser_slot": {
                    "type": "integer",
                    "description": (
                        "EMU slot index of the excitation laser (from "
                        "get_emu_laser_map). The acquisition is refused if that "
                        "slot's trigger mode is '0 - Off' or its sequence is 0."
                    ),
                },
                "hook_strategy": {"type": "string", "description": "Optional hook from list_hooks."},
                "hook_params": {"type": "object", "description": "Hook constructor parameters."},
                "log_path": {"type": "string", "description": "Hook output log path."},
                "illumination_envelope": _HOOK_ILLUMINATION_ENVELOPE_SCHEMA,
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
            "saved pixels and exposes nothing. Two adapters are BUILT IN and need "
            "no review, hash pin or confirmation — reach for them before writing "
            "anything and before reasoning from a picture. 'connected_components' "
            "(input_kind='stage_coordinate_mosaic') labels contiguous signal and "
            "reports each object's area in um^2, centroid in STAGE coordinates and "
            "bounding box: this is how you answer whether two positions sit on the "
            "same object. 'frame_statistics' (input_kind='frames') scores every "
            "saved frame with the same statistics as a live snap: this is how you "
            "say whether anything is in an acquisition you already ran. An adapter "
            "from the user's saved manifest also runs here, and those stay reviewed "
            "and hash-pinned."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_path": {"type": "string"},
                "adapter": {"type": "string"},
                "axis_selection": {"type": "object"},
                "input_kind": {"type": "string", "enum": ["frames", "stage_coordinate_mosaic"]},
                "parameters": {"type": "object"},
                "output_dir": {"type": "string"},
                "calibration_ref": _CALIBRATION_REF_SCHEMA,
                "output_pixel_size_um": {"type": "number", "exclusiveMinimum": 0},
                "model_project_config": {"type": "object"},
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
            },
            "required": [],
        },
    },
    {
        "name": "find_features",
        "description": (
            "Snap and return NUMBERS about the field: spot count (blob detection), "
            "intensity-weighted centroid, offset of the signal from the field "
            "centre (pixels, and µm when calibrated), background level, SNR, and "
            "spot_density_per_um2 (the SMLM blinking-density check). Use this — "
            "not a thumbnail — whenever you need to answer 'is the feature "
            "centred?', 'is there anything here?', or 'is the blinking density "
            "right?'. Deterministic and identical on every call."
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
            "Closed loop that centres the brightest feature in the field of view: "
            "find_features → pixel offset → stage-camera affine → guarded stage "
            "move → repeat, until the residual is below tol_px or max_iter is "
            "reached. Requires calibrate_stage_to_camera to have run for the "
            "current objective/binning. Use this instead of manually nudging the "
            "stage and re-snapping."
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
            "Sweeps Z from (current_z - z_range_um/2) to (current_z + z_range_um/2) "
            "in z_step_um steps. Returns BOTH passes (coarse chooses the plane, fine "
            "refines it) with their metric curves and contrast, plus converged/moved/"
            "entry_z_um/final_z_um. If the metric curve is structureless (low "
            "contrast — e.g. faint signal or a too-small ROI), the stage is NOT "
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
                "z_step_um": {
                    "type": "number",
                    "description": "Step size in µm for the fine sweep.",
                },
                "method": {
                    "type": "string",
                    "description": "'coarse_then_fine' (default) or 'sweep'.",
                    "default": "coarse_then_fine",
                },
                "settle_ms": {
                    "type": "integer",
                    "description": "Wait time after each Z move in ms (default 50).",
                    "default": 50,
                },
                "return_thumbnail": {
                    "type": "boolean",
                    "description": "Include a thumbnail of the focused image (default false). Only set to True if absolutely necessary.",
                    "default": False,
                },
            },
            "required": ["z_range_um", "z_step_um"],
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
            "Visit each position and run a per-position protocol (snap, zstack, or timelapse). "
            "PROTOCOL CHOICE: when the deliverable is per-position NUMBERS (max/min/"
            "mean intensity, focus metric), protocol='snap' already returns them for "
            "every position — writing nothing to disk is correct when nothing was "
            "asked to be saved. zstack/timelapse write datasets and return NO image "
            "statistics; reach for them only when data must land on disk. "
            "Supply either position_names (labels already in the MM position list) OR positions "
            "(a list of {name, x_um, y_um, z_um?} dicts — no prior mark_position needed). "
            "Saves each position's data to a subdirectory of save_dir. "
            "Pass mark_positions=true to also record every visited position into the "
            "stage position list. "
            "Pass hook_strategy to run one hooked acquisition across every position: a "
            "single dataset with a `position` axis and one hook log covering every "
            "point. Prefer this single-dataset option for a tiled acquisition; do not "
            "also run the per-position form unless the user explicitly requests both, "
            "because doing both repeats every exposure. Not compatible with "
            "protocol='snap' (display-only, no acquisition "
            "images) — use protocol='timelapse' with n_frames=1 instead. Without "
            "hook_strategy, zstack/timelapse writes one dataset per position; those "
            "separate datasets CANNOT be passed to build_stage_coordinate_mosaic."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
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
                "protocol_params": {
                    "type": "object",
                    "description": (
                        "Extra parameters forwarded to the per-position protocol. "
                        "For zstack: z_start_um, z_end_um, z_step_um. "
                        "For timelapse: n_frames, interval_s."
                    ),
                },
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
                        "One hook name or an ordered list (from list_hooks). Runs ONE "
                        "acquisition across all positions. Cannot be "
                        "combined with protocol='snap'. BATCHED: every position's "
                        "event is submitted before the first frame arrives, so the "
                        "hook can measure and log but can never stop the scan early "
                        "— for stop-on-condition use run_adaptive_survey."
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
            "Pass hook_strategy to run one hooked acquisition across the whole grid: a "
            "single dataset with a `position` axis and one hook log covering every "
            "tile. This is how you compute a custom per-tile quantity that snap does "
            "not already return — never spell a "
            "grid as N single-plane z-stacks. Not compatible with protocol='snap' "
            "(display-only, no acquisition images) — use protocol='timelapse' with "
            "protocol_params={'n_frames': 1, 'interval_s': 0} for one hooked frame "
            "per tile."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
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
                "protocol_params": {
                    "type": "object",
                    "description": (
                        "Extra parameters forwarded to the per-position protocol. "
                        "For zstack: z_start_um, z_end_um, z_step_um. "
                        "For timelapse: n_frames, interval_s."
                    ),
                },
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
                        "Hook strategy name (from list_hooks). Runs ONE acquisition "
                        "across the whole grid with a single hook instance. Cannot be "
                        "combined with protocol='snap'. BATCHED: every tile's event "
                        "is submitted before the first frame arrives, so the hook "
                        "can measure and log but can never stop the grid early — "
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
                        "Path for the hook's output log, covering every tile "
                        "(optional). Read it back with read_hook_log."
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
            "The forwarded run writes one dataset with a `position` axis. The old "
            "implementation wrote one dataset per position, whose output CANNOT be "
            "used as one position-axis acquisition; that duplicated path is gone. "
            "Display-only snap is not supported by this deprecated wrapper."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
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
                "protocol_params": {
                    "type": "object",
                    "description": "Extra parameters forwarded to the per-position protocol.",
                },
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
            "ContinueSurvey (acquire the next planned tile) or StopSurvey (end "
            "the scan); the trusted parent, not the hook, dispatches them. It "
            "may also request one refocus of a promising tile when the caller "
            "provides an exposure-bounded autofocus_budget; the refocused frame "
            "is judged again with metadata['microclaw_refocused'] true. It "
            "never receives ctrl, guard, or a queue. A reviewed built-in from "
            "the pre-coded registry keeps the older direct contract: the runner "
            "sets hook.survey_events, hook.candidates and hook.progress, and the "
            "hook must candidates.put() the next tile OR call "
            "progress.done_early(), then progress.image_done() — see "
            "get_hook_documentation ('Skipping and stopping'). Serializes the "
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
                        "Hook strategy name (from list_hooks). Must implement "
                        "the adaptive contract; a hook that never calls "
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
                "protocol_params": {
                    "type": "object",
                    "description": (
                        "Per-position protocol parameters. "
                        "For zstack: z_start_um, z_end_um, z_step_um. "
                        "For timelapse: n_frames, interval_s. "
                        "Optionally channel and exposure_ms for either."
                    ),
                },
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
                         "description": "The artifact path a tool returned."},
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
            "Recursively enumerate files under workspace artifact paths and compute "
            "their size and optional SHA-256 within deterministic file-count, byte, "
            "and depth bounds. Refusals include a per-directory count/byte survey so "
            "a caller can narrow the next request. Optionally save a JSON manifest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}},
                "manifest_path": {"type": "string"},
                "max_files": {"type": "integer", "minimum": 1, "default": 1000},
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
            "(with their descriptions and source). Call describe_hook with a strategy name "
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
            "hook_strategy='autofocus_mm_plugin' and requires BOTH plugins.allow_hardware_motion: true AND property_authorization.mode: degraded_trusted_plugins in safety_config.yaml (the motion flag alone is refused at startup in guaranteed mode), plus a restart. "
            "Requires a Micro-Manager build with the unified "
            "plugin classloader (PR #2401). Always confirm the classpath with the user "
            "before enabling a plugin hook."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_hook_documentation",
        "description": (
            "Return the pycro-manager hook API reference: Acquisition hook kwargs, "
            "hook function signatures, return-value contracts, event dict structure, "
            "event_queue usage, the HookBase pattern required by microclaw, and the "
            "integration interview for adapting a user's Python package, executable, "
            "plugin, or other custom analysis. Call this before writing a new hook."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_smlm_documentation",
        "description": (
            "Return the SMLM (single-molecule localization microscopy) protocol reference: "
            "technique variants (dSTORM, PALM, PAINT/DNA-PAINT), acquisition parameters "
            "(exposure, frame count, laser power, channel, TIRF mode), step-by-step "
            "acquisition protocol, drift-correction guidance, post-processing software "
            "recommendations, common pitfalls, and key questions to ask the user. "
            "Call this before planning or starting any SMLM acquisition."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_emu_installed",
        "description": (
            "Detect whether EMU and/or htSMLM are installed by scanning the Micro-Manager "
            "plugins directory for their JAR files. "
            "Returns emu_installed, htsmlm_installed, htsmlm_configured, the MM app "
            "directory found, and the "
            "names of any plugin JARs discovered. "
            "Call this to determine whether get_htsmlm_documentation and get_emu_configuration "
            "are relevant for the current setup. If neither plugin is detected AND the user has "
            "not mentioned htSMLM or EMU, do not call those tools."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_htsmlm_documentation",
        "description": (
            "Return the htSMLM / EMU reference: what EMU and htSMLM are, how to control "
            "htSMLM hardware via set_device_property, the complete UIProperty name inventory "
            "(lasers, filters, focus lock, two-state devices, laser triggers, iBeamSmart, QPD), "
            "the get_emu_configuration workflow, plugin settings, acquisition guidance, and "
            "key questions to ask the user. "
            "Only call this if check_emu_installed has confirmed htSMLM is installed, "
            "OR if the user has explicitly mentioned htSMLM or EMU."
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
            "OR if the user has explicitly mentioned htSMLM or EMU. "
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
            "resolved through the EMU map, plus the current QPD readings. A sharp "
            "image is NOT evidence that the lock is engaged — always answer the SMLM "
            "checklist's focus-lock item with this tool. Returns engaged=null on rigs "
            "with no focus-lock property."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_focus_lock",
        "description": (
            "Engage or disengage the hardware focus lock. Disengage before running a "
            "software autofocus sweep (which would otherwise fight the servo loop), "
            "and re-engage afterwards — run_autofocus refuses to run while it is on."
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
            "Validate and save a hook script to disk. "
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
                    "description": "Runner that will execute the hook; adaptive requires analyze_frame.",
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
            "properties, and numeric limits. Always call this before "
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
                        "Always include a 'description' field summarizing the entry."
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
