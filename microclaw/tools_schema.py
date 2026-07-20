from typing import Any

TOOLS: list[dict[str, Any]] = [
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
            "active channel, exposure time, whether live view is running, and the "
            "shutter and per-slot laser state. "
            "Call this first when you need context before executing a protocol. "
            "The shutter and lasers fields are always present, and read 'unknown' "
            "when this rig cannot report them — never tell the user illumination "
            "was off unless this tool said so."
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
        "description": (
            "Run a timelapse acquisition. On an EMU/htSMLM rig, pass laser_slot "
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
            "so Z is restored for inspection. The normalized Laplacian metric is "
            "polarity-insensitive and applies to bright-on-dark puncta as well as "
            "dark-on-bright structure. "
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
                "name": {"type": "string", "description": "Position label."}
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
                "name": {"type": "string", "description": "Position label to delete."}
            },
            "required": ["name"],
        },
    },
    {
        "name": "clear_position_list",
        "description": "Clear all positions from MM's native position list.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "save_position_list",
        "description": (
            "Save the position list to a microclaw JSON file for use in future "
            "sessions. This is microclaw's own format, not MM's native .pos file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Output JSON file path."}
            },
            "required": ["path"],
        },
    },
    {
        "name": "load_position_list",
        "description": "Load a previously saved position list file into the agent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the position list file."}
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
        "input_schema": {"type": "object", "properties": {}, "required": []},
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
            "point. Not compatible with protocol='snap' (display-only, no acquisition "
            "images) — use protocol='timelapse' with n_frames=1 instead."
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
                "hook_strategy": {
                    "type": "string",
                    "description": (
                        "Hook strategy name (from list_hooks). Runs ONE acquisition "
                        "across all positions with a single hook instance. Cannot be "
                        "combined with protocol='snap'. BATCHED: every position's "
                        "event is submitted before the first frame arrives, so the "
                        "hook can measure and log but can never stop the scan early "
                        "— for stop-on-condition use run_adaptive_survey."
                    ),
                },
                "hook_params": {
                    "type": "object",
                    "description": "Parameters passed to the hook constructor.",
                },
                "log_path": {
                    "type": "string",
                    "description": (
                        "Path for the hook's output log, covering every position "
                        "(optional). Read it back with read_hook_log."
                    ),
                },
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
            "Visit each named position, run software autofocus, then run a per-position "
            "protocol (snap, zstack, or timelapse). Use this for automated surveys where "
            "each stored image must be in focus."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "position_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of position labels to visit.",
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
            },
            "required": ["position_names", "z_range_um", "z_step_um", "protocol", "save_dir"],
        },
    },
    {
        "name": "run_adaptive_zstack",
        "description": (
            "Run a Z-stack acquisition with a hook strategy for adaptive behaviour "
            "— the hook adapts settings (exposure, focus) between frames of a FIXED "
            "plane sequence at the current position; it cannot skip planes or stop "
            "early (for stop-on-condition use run_adaptive_survey). "
            "Pre-coded strategies: autofocus_per_position, focus_feedback, "
            "intensity_adaptive, position_filter, mm_plugin_analyzer, autofocus_mm_plugin. "
            "The mm_plugin_analyzer and autofocus_mm_plugin strategies delegate to an "
            "installed Micro-Manager plugin (see list_mm_plugins); autofocus_mm_plugin "
            "requires plugins.allow_hardware_motion: true in safety_config.yaml. "
            "Call list_hooks() to see all available strategies including saved hooks. "
            "After the acquisition, call read_hook_log(log_path) to retrieve results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "z_start_um": {"type": "number", "description": "Start Z in µm."},
                "z_end_um": {"type": "number", "description": "End Z in µm."},
                "z_step_um": {"type": "number", "description": "Step size in µm."},
                "save_dir": {"type": "string", "description": "Directory to save the dataset."},
                "hook_strategy": {
                    "type": "string",
                    "description": "Hook strategy name (from list_hooks).",
                },
                "hook_params": {
                    "type": "object",
                    "description": "Parameters passed to the hook constructor.",
                },
                "channel": {
                    "type": "string",
                    "description": "Channel preset (optional).",
                },
                "name": {
                    "type": "string",
                    "description": "Dataset name (default 'adaptive').",
                    "default": "adaptive",
                },
                "log_path": {
                    "type": "string",
                    "description": "Path for the hook's output log (optional).",
                },
            },
            "required": ["z_start_um", "z_end_um", "z_step_um", "save_dir", "hook_strategy"],
        },
    },
    {
        "name": "run_adaptive_timelapse",
        "description": (
            "Run a timelapse acquisition with a hook strategy for adaptive behaviour "
            "— the hook adapts settings (exposure, focus) between frames of a FIXED "
            "frame sequence at the current position; it cannot skip frames or stop "
            "early (for stop-on-condition use run_adaptive_survey). "
            "Pre-coded strategies: autofocus_per_position, focus_feedback, "
            "intensity_adaptive, position_filter, mm_plugin_analyzer, autofocus_mm_plugin. "
            "focus_feedback corrects Z drift per frame and is well suited to timelapses. "
            "The mm_plugin_analyzer and autofocus_mm_plugin strategies delegate to an "
            "installed Micro-Manager plugin (see list_mm_plugins); autofocus_mm_plugin "
            "requires plugins.allow_hardware_motion: true in safety_config.yaml. "
            "Call list_hooks() to see all available strategies including saved hooks. "
            "After the acquisition, call read_hook_log(log_path) to retrieve results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "n_frames": {"type": "integer", "description": "Number of frames."},
                "interval_s": {
                    "type": "number",
                    "description": "Interval between frames in seconds.",
                },
                "save_dir": {"type": "string", "description": "Directory to save the dataset."},
                "hook_strategy": {
                    "type": "string",
                    "description": "Hook strategy name (from list_hooks).",
                },
                "hook_params": {
                    "type": "object",
                    "description": "Parameters passed to the hook constructor.",
                },
                "channel": {
                    "type": "string",
                    "description": "Channel preset (optional).",
                },
                "name": {
                    "type": "string",
                    "description": "Dataset name (default 'adaptive').",
                    "default": "adaptive",
                },
                "log_path": {
                    "type": "string",
                    "description": "Path for the hook's output log (optional).",
                },
            },
            "required": ["n_frames", "interval_s", "save_dir", "hook_strategy"],
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
            "ADAPTIVE hook: the runner sets hook.survey_events (the full planned "
            "tile list, seed included), hook.candidates and hook.progress; after "
            "each frame the hook must candidates.put() the next tile OR call "
            "progress.done_early(), then progress.image_done() — see "
            "get_hook_documentation ('Skipping and stopping'). Serializes the "
            "scan (each tile waits on the previous frame's scoring), so for a "
            "fixed survey that only reports per-tile numbers use "
            "run_tile_acquisition or run_multiposition_acquisition instead. "
            "The result reports frames_acquired and stopped_early; read the "
            "hook's own numbers back with read_hook_log(log_path)."
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
            },
            "required": ["protocol", "save_dir", "hook_strategy"],
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
        "name": "list_hooks",
        "description": (
            "List all available hook strategies: pre-coded hooks and previously saved hooks "
            "(with their descriptions and source). Call this before run_adaptive_zstack "
            "or run_adaptive_timelapse to confirm the strategy name."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_mm_plugins",
        "description": (
            "List installed Micro-Manager plugins grouped by role (autofocus, processor, "
            "menu) so a human can review classpaths before enabling a plugin-backed hook. "
            "Analyzer plugins run via hook_strategy='mm_plugin_analyzer' (allowed unless "
            "listed in plugins.blocked); autofocus plugins run via "
            "hook_strategy='autofocus_mm_plugin' and require plugins.allow_hardware_motion: "
            "true in safety_config.yaml. Requires a Micro-Manager build with the unified "
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
            "event_queue usage, and the HookBase pattern required by microclaw. "
            "Call this before writing a new hook."
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
            "Returns emu_installed, htsmlm_installed, the MM app directory found, and the "
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
            "(slot index → its own enable, power and trigger lines), 'filter_wheel' "
            "(with the state → value table), 'focus_lock', 'other', and 'unallocated' "
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
            "Use this instead of guessing which device backs an htSMLM control."
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
            },
            "required": ["name", "code", "description"],
        },
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
            "Save a non-standard fact about a sample, device, or imaging strategy to "
            "the user's persistent knowledge base (~/.microclaw/knowledge.yaml). "
            "This information is loaded automatically in future sessions. "
            "Only call after the user has confirmed they want it saved."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["samples", "devices", "strategies"],
                    "description": (
                        "'samples' for sample/specimen profiles, "
                        "'devices' for non-standard hardware mappings and roles, "
                        "'strategies' for named imaging recipes."
                    ),
                },
                "key": {
                    "type": "string",
                    "description": (
                        "Short identifier for the entry, e.g. 'U2OS_actin_Alexa647' "
                        "or 'Thorlabs-ELL-9'. Use underscores, no spaces."
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
                    "enum": ["samples", "devices", "strategies"],
                    "description": "Category to retrieve. Omit to return all categories.",
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
                    "enum": ["samples", "devices", "strategies"],
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
