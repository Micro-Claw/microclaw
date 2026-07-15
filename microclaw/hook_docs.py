HOOK_REFERENCE = """
# pycro-manager hook API reference (microclaw)

## Acquisition constructor hook kwargs

Pass these as keyword arguments to Acquisition(...):

  image_process_fn       callable(image, metadata, event_queue) -> tuple | None
  post_hardware_hook_fn  callable(event) -> dict | None
  pre_hardware_hook_fn   callable(event) -> dict | None
  event_generation_hook_fn  callable(event) -> list[dict] | None
  image_saved_hook_fn    callable(dataset_path, axes, image, metadata) -> None

Only pass the hook kwargs you actually implement — unused ones are omitted.

## Hook function signatures and return-value contracts

### image_process_fn(image: np.ndarray, metadata: dict, event_queue) -> tuple | None

Called after every image arrives from the camera, before it is saved.

  - Return (image, metadata) to keep the image (you may modify either).
  - Return None to discard this image and drop all remaining events for that
    position (pycro-manager interprets None as "skip this position").
  - Push new events from within the function:
      event_queue.put({"axes": {"time": t}, "exposure": 50})
  - Signal that the acquisition should end early:
      event_queue.put(None)

To key a log entry to the image's place in the acquisition, call
self.log(metadata, ...) on HookBase: it stamps position/x_um/y_um/z_um for you
from the image metadata, so every entry is self-describing.

If you read the metadata yourself: a multi-position acquisition carries
"PositionName", "XPosition_um_Intended" and "YPosition_um_Intended"; a Z-stack
carries "ZPosition_um_Intended". An acquisition without a given axis does not
carry its keys, so read every one with .get() and fall back to
`metadata["Axes"]["position"]` for identity. (metadata["Axes"] holds e.g.
{"position": "tile_r0_c1", "time": 0, "z": 3}.)

### post_hardware_hook_fn(event: dict) -> dict | None

Called after the hardware has moved to the event's position (XY, Z, channel)
but before the camera fires.

  - Return the (optionally modified) event dict to proceed with image capture.
  - Return None to skip image capture for this event entirely.
  - Use this for autofocus: the stage is already at the nominal XY, so you can
    do a Z sweep here and update the focus device before the shutter opens.

### pre_hardware_hook_fn(event: dict) -> dict | None

Called before the hardware moves for this event.

  - Return the (optionally modified) event dict to proceed.
  - You may change event["z"], event["x"], event["y"] to redirect hardware.
  - Return None to skip this event entirely (hardware never moves).

### event_generation_hook_fn(event: dict) -> list[dict] | None

Called to dynamically generate or replace the events for an acquisition.

  - Return a list of replacement event dicts to use instead of the original.
  - Return None to leave the original event unchanged.
  - Use this when you need to compute event parameters at acquisition time
    rather than ahead of time.

### image_saved_hook_fn(dataset_path: str, axes: dict, image: np.ndarray, metadata: dict) -> None

Called after each image has been written to the NDTiff dataset on disk.
Return value is ignored. Use for side-channel logging, copying, or notification.

## Event dict structure

```python
{
    "axes": {
        "z":        0,    # zero-based index within the Z axis
        "time":     0,    # zero-based time-point index
        "position": 0,    # zero-based position index
        "channel":  0,    # zero-based channel index
    },
    "z":        10.5,             # absolute Z position in µm  (if Z axis used)
    "x":       100.0,             # absolute X in µm           (if set explicitly)
    "y":       200.0,             # absolute Y in µm           (if set explicitly)
    "channel": {"group": "Channel", "config": "DAPI"},  # (if channel axis used)
    "exposure": 100,              # exposure in ms             (if set explicitly)
    "min_start_time": 0.0,        # earliest start time in seconds from acq start
}
```

Only keys relevant to the current axis configuration are present. The "axes"
indices are sequence numbers (0, 1, 2, …), not physical values.

## event_queue usage (inside image_process_fn)

```python
# Add a new event dynamically:
event_queue.put({
    "axes": {"time": next_t, "z": 0},
    "exposure": 50,
})

# End the acquisition early:
event_queue.put(None)
```

## HookBase pattern (required for all microclaw-generated hooks)

Every hook saved through microclaw MUST inherit from HookBase. This ensures
the hook writes a structured log that the agent can read with read_hook_log().

```python
from microclaw.hooks import HookBase
import numpy as np

class MyHook(HookBase):
    def __init__(self, ..., log_path=None):
        super().__init__(log_path)   # sets self.log_path, self._log = []
        # store any extra init params here

    # Implement ONE OR MORE of the hook methods below.

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue):
        # ... your logic ...
        self._log.append({"frame": metadata.get("time"), "key": "value"})
        self._write_log()            # persists self._log to self.log_path
        return image, metadata       # or: return None  (to discard)

    def post_hardware_hook_fn(self, event: dict) -> dict:
        # ... your logic ...
        self._log.append({...})
        self._write_log()
        return event                 # or: return None  (to skip)
```

If the hook needs hardware access, accept `ctrl` and `guard` in __init__:

```python
    def __init__(self, ctrl, guard, ..., log_path=None):
        super().__init__(log_path)
        self.ctrl = ctrl
        self.guard = guard
```

HookBase provides:
  self.log(metadata, **fields) — append ONE entry stamped with position + stage
                                 XY/Z from the image metadata, then persist.
                                 Prefer this over appending to self._log by hand.
  self.log_event(event, **fields) — same, for pre/post-hardware hooks that get an
                                 event dict instead of image metadata.
  HookBase.where(metadata)     — just the {position, x_um, y_um, z_um} dict.
  self._log        list[dict]  — the raw record list (log()/log_event() append here)
  self._write_log()            — writes self._log as JSON to self.log_path
  self.log_path    str | None  — path supplied at construction time

## Choosing the right hook type

  Task                                   Hook to implement
  -------------------------------------  --------------------------------
  Skip/filter positions by intensity     image_process_fn → return None
  Adjust exposure or settings per frame  image_process_fn → ctrl.core.set_*
  Autofocus before each image capture    post_hardware_hook_fn (stage already at XY)
  Redirect stage before hardware moves   pre_hardware_hook_fn (modify event["z"] etc.)
  Generate events dynamically at runtime event_generation_hook_fn
  Log metadata after image is saved      image_saved_hook_fn

## Micro-Manager plugin hooks

Two pre-coded strategies delegate hook logic to an installed Micro-Manager
plugin instead of re-implementing it in Python. Call list_mm_plugins() to see
installed plugins and their classpaths. These require a Micro-Manager build with
the unified plugin classloader (PR #2401); on older builds the plugin classes
are not resolvable over the ZMQ bridge and the hook fails loudly at startup.

MM plugins are arbitrary Java: they bypass SafetyGuard (which only gates
microclaw's own hardware calls) and the Python AST scanner (which only sees
Python source). ALWAYS confirm the classpath + method with the user before
enabling a plugin hook.

### mm_plugin_analyzer  (analyzer — read-only, allowed by default)

Treats the plugin as an ANALYZER: microclaw computes a scalar feature from the
image (np.mean) in Python, passes only that scalar to the plugin, and uses the
plugin's returned score for a guarded, Python-side keep/skip decision. The full
image never crosses the bridge, and the plugin must NOT move hardware here.

  hook_params:
    classpath      fully-qualified plugin class (e.g. "org.lab.QualityScorePlugin")
    method         plugin method to call with the scalar feature (default "analyze")
    reject_below   optional float; discard the image if score < this value

  Safety gate: SafetyGuard.check_plugin — allowed unless the classpath is listed
  in safety_config.yaml plugins.blocked. Fails open: if the plugin call raises,
  the image is kept and the error is logged (data is never lost to a plugin bug).

### autofocus_mm_plugin  (hardware motion — off by default)

Drop-in alternative to autofocus_per_position: runs the lab's validated MM
autofocus plugin (via the autofocus manager) in the post_hardware slot before
each capture. The PLUGIN owns the Z motion.

  hook_params:
    plugin_name    optional MM autofocus plugin name; omit to use the active one

  Safety gate: SafetyGuard.check_plugin_motion — requires
  plugins.allow_hardware_motion: true in safety_config.yaml (blocklist also
  applies). PASSIVE guard on the result: after the plugin focuses, microclaw
  reads the new Z and calls check_z; if it is out of bounds the capture is
  skipped and the acquisition stops (return None). microclaw does NOT re-drive Z
  — an active correction would fight the plugin's own safety controller.

### The composition rule (one image-processing side per hook)

Per hook, keep the heavy full-image work on ONE side (Python or Java) and let
only scalars/metadata cross the bridge. If microclaw itself issues the hardware
move from a plugin's non-image output, SafetyGuard applies normally (guard the
move as usual) — that differs from autofocus_mm_plugin, where the plugin drives
the stage and microclaw can only guard passively. Full-image Java processing
should be an MM processor pipeline, not a hook.
"""
