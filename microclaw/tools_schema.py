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
    {
        "name": "snap_and_analyze",
        "description": (
            "Snap a single image and return numerical stats (focus metric, mean intensity, "
            "saturation). Use this whenever you need quantitative image data. "
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
            },
            "required": [],
        },
    },
    {
        "name": "run_autofocus",
        "description": (
            "Run a software autofocus sweep to find the sharpest Z plane. "
            "Sweeps Z from (current_z - z_range_um/2) to (current_z + z_range_um/2) "
            "in z_step_um steps. Returns best Z, focus metric curve, and a thumbnail. "
            "Default parameters for a 20× objective: z_range_um=20, z_step_um=0.5. "
            "Widen z_range_um if the result says the peak was at the boundary."
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
            "Save the current stage position to MM's native position list. "
            "The position is immediately visible in the MM GUI's XY Stage Control window. "
            "Call this after the biologist has navigated to a site of interest."
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
                    "description": "Also save the current Z position (default true).",
                    "default": True,
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
        "description": "Save MM's position list to a .pos file for use in future sessions.",
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
            "Supply either position_names (labels already in the MM position list) OR positions "
            "(a list of {name, x_um, y_um, z_um?} dicts — no prior mark_position needed). "
            "Saves each position's data to a subdirectory of save_dir."
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
                    "description": "'snap', 'zstack', or 'timelapse'.",
                },
                "save_dir": {"type": "string", "description": "Root directory for saved data."},
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
            },
            "required": ["protocol", "save_dir"],
        },
    },
    {
        "name": "run_tile_acquisition",
        "description": (
            "Acquire a rows×cols tile grid centered on the current stage position. "
            "Computes grid coordinates automatically — no prior mark_position needed. "
            "Runs a per-position protocol (snap, zstack, or timelapse) at each tile."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rows": {"type": "integer", "description": "Number of rows in the grid."},
                "cols": {"type": "integer", "description": "Number of columns in the grid."},
                "step_um": {"type": "number", "description": "Step size between tiles in µm."},
                "protocol": {
                    "type": "string",
                    "description": "'snap', 'zstack', or 'timelapse'.",
                },
                "save_dir": {"type": "string", "description": "Root directory for saved data."},
                "name": {
                    "type": "string",
                    "description": "Dataset name prefix (default 'tile').",
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
            },
            "required": ["rows", "cols", "step_um", "protocol", "save_dir"],
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
            "Run a Z-stack acquisition with a hook strategy for adaptive behaviour. "
            "Pre-coded strategies: autofocus_per_position, focus_feedback, "
            "intensity_adaptive, position_filter. "
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
            "Run a timelapse acquisition with a hook strategy for adaptive behaviour. "
            "Pre-coded strategies: autofocus_per_position, focus_feedback, "
            "intensity_adaptive, position_filter. "
            "focus_feedback corrects Z drift per frame and is well suited to timelapses. "
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
            "Read the EMU configuration file and return the mapping from htSMLM UIProperty "
            "names to Micro-Manager device labels and property names. "
            "Only call this if check_emu_installed has confirmed EMU is installed, "
            "OR if the user has explicitly mentioned htSMLM or EMU. "
            "Auto-detects the Micro-Manager installation directory from common platform paths "
            "and a local cache (~/.microclaw/emu.json). If auto-detection fails, returns an "
            "error with instructions; call again with mm_app_dir set to the correct path and "
            "it will be saved for future calls. "
            "The returned 'properties' dict maps UIProperty names (e.g. 'Laser 0 enable') to "
            "dicts containing 'device', 'property', and any TwoState (on/off) or Rescaled "
            "(slope/offset) metadata needed to compute the correct MM property value."
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
