# Runtime pycro-manager hook documentation skill

## Goal

Add a `get_hook_documentation` tool so the runtime microclaw agent has a precise
pycro-manager hook API reference (https://pycro-manager.readthedocs.io/en/latest/)
available on demand when it writes a new hook. The agent calls this tool as step 1 
of the hook-writing flow, before generating any code, so that hook signatures, 
return-value contracts, and event-queue usage are grounded in documentation rather 
than training knowledge.

---

## 1. New file — `microclaw/hook_docs.py`

Contains a single module-level string constant `HOOK_REFERENCE` with the complete
hook API reference. No imports, no classes, no logic. Content to cover:

### 1a. `Acquisition` constructor hook kwargs

All five hook kwargs that `Acquisition(...)` accepts, with their Python types:

| kwarg | type |
|---|---|
| `image_process_fn` | `Callable[[np.ndarray, dict, Queue], tuple \| None]` |
| `post_hardware_hook_fn` | `Callable[[dict], dict \| None]` |
| `pre_hardware_hook_fn` | `Callable[[dict], dict \| None]` |
| `event_generation_hook_fn` | `Callable[[dict], list[dict] \| None]` |
| `image_saved_hook_fn` | `Callable[[str, dict, np.ndarray, dict], None]` |

### 1b. Signatures and return-value contracts for each hook type

```
image_process_fn(image: np.ndarray, metadata: dict, event_queue) -> tuple | None
  - Return (image, metadata) to keep the image.
  - Return None to discard the image and suppress remaining events for that position.
  - Push new events: event_queue.put({"axes": {...}, ...})
  - Signal acquisition end:  event_queue.put(None)

post_hardware_hook_fn(event: dict) -> dict | None
  - Called after hardware moves to the event position, before the camera fires.
  - Return the (optionally modified) event dict to proceed.
  - Return None to skip image capture for this event.

pre_hardware_hook_fn(event: dict) -> dict | None
  - Called before hardware moves for this event.
  - Return the event to proceed (possibly with modified axes/positions).
  - Return None to skip this event entirely.

event_generation_hook_fn(event: dict) -> list[dict] | None
  - Called to dynamically generate or replace events.
  - Return a list of replacement events, or None to use the original event unchanged.

image_saved_hook_fn(dataset_path: str, axes: dict, image: np.ndarray, metadata: dict) -> None
  - Called after each image is written to disk.
  - Return value is ignored.
```

### 1c. Event dict structure

```python
{
    "axes": {"z": 0, "time": 0, "position": 0, "channel": 0},  # integer indices
    "z": 10.5,           # absolute Z position in µm (if Z axis)
    "x": 100.0,          # absolute X in µm (if XY set in event)
    "y": 200.0,          # absolute Y in µm (if XY set in event)
    "channel": {"group": "Channel", "config": "DAPI"},
    "exposure": 100,     # ms
    "min_start_time": 0, # seconds from acquisition start
}
```

Keys present only when the acquisition uses that axis. `axes` indices are
zero-based sequence numbers, not physical values.

### 1d. `event_queue` usage

```python
# Add a new event dynamically from within image_process_fn:
event_queue.put({"axes": {"z": 0, "time": t}, "exposure": 50})

# Signal that the acquisition should end:
event_queue.put(None)
```

### 1e. `HookBase` pattern (required for all generated hooks)

All generated hooks must inherit from `microclaw.hooks.HookBase` and follow
this pattern:

```python
from microclaw.hooks import HookBase

class MyHook(HookBase):
    def __init__(self, ..., log_path=None):
        super().__init__(log_path)
        ...

    def image_process_fn(self, image, metadata, event_queue):
        ...
        self._log.append({...})
        self._write_log()
        return image, metadata   # or None to discard
```

`HookBase` provides `self._log` (list) and `self._write_log()` (writes to
`self.log_path`). Accept `ctrl` and `guard` if the hook needs hardware access.

### 1f. Which hook type to use for common tasks

| Task | Hook type |
|---|---|
| Skip/filter positions by intensity | `image_process_fn` → return None |
| Adjust exposure per frame | `image_process_fn` → call ctrl.core.set_exposure |
| Autofocus before each image | `post_hardware_hook_fn` (hardware already at position) |
| Modify stage position before move | `pre_hardware_hook_fn` |
| Generate extra events dynamically | `event_generation_hook_fn` |
| Log saved-image metadata | `image_saved_hook_fn` |

---

## 2. Changes to `microclaw/tools.py`

Add a `get_hook_documentation` function immediately before the `TOOL_REGISTRY`
dict (after `list_hooks`):

```python
def get_hook_documentation(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    from microclaw.hook_docs import HOOK_REFERENCE
    return {"documentation": HOOK_REFERENCE}
```

Add to `TOOL_REGISTRY`:

```python
"get_hook_documentation": get_hook_documentation,
```

---

## 3. Changes to `microclaw/tools_schema.py`

Add a new entry to the `TOOLS` list, immediately before `generate_and_save_hook`:

```python
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
```

Note: `TOOLS_CACHED` is built as `[*TOOLS[:-1], {**TOOLS[-1], "cache_control": ...}]`
so it will pick up the new entry automatically — no change needed there.

---

## 4. Changes to `microclaw/agent.py` — system prompt

In the `Hook-based adaptive acquisition` section, replace step 3b:

**Before:**
```
  3b. If the user asks you to write one: follow the hook template (class with image_process_fn), show the full code and any warnings, wait for explicit confirmation, then call generate_and_save_hook(source='claude_generated').
```

**After:**
```
  3b. If the user asks you to write one: call get_hook_documentation first, then write a hook that conforms to the API reference it returns. Show the full code and any warnings, wait for explicit confirmation, then call generate_and_save_hook(source='claude_generated').
```

---

## 5. Tests

Add to `tests/test_tools.py`:

- `test_get_hook_documentation_returns_string` — call `execute_tool("get_hook_documentation", {}, ctrl, guard)`, parse the JSON result, assert `result["documentation"]` is a non-empty string.
- `test_get_hook_documentation_covers_key_concepts` — assert that the returned string contains `"image_process_fn"`, `"post_hardware_hook_fn"`, `"HookBase"`, and `"event_queue"`.

No mock or hardware needed — the function has no `ctrl`/`guard` dependency.

---

## Out of scope

- Fetching live docs from readthedocs at runtime (adds network dependency with no
  meaningful benefit given how stable the hook API is).
- A parallel Claude Code project skill for development-time use (separate concern,
  can be added later independently).
