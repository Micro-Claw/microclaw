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
  self._log        list[dict]  — append your per-image/per-event records here
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
"""
