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
