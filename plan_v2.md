# Microclaw v2: Adaptive Acquisitions and Image Analysis

**Date:** 2026-05-18  
**Stack:** Python · Anthropic API (multimodal tool use) · pycro-manager · Micro-Manager 2.0  
**Builds on:** plan.md (v1 architecture)

---

## 1. Goals

v1 gave the agent language-level control over the microscope: it can move stages,
set channels, snap images, and run structured acquisitions. What it cannot do is
*see* the images it captures or make decisions based on their content.

v2 adds:

1. **Vision in the conversation loop** — snapped images are returned to Claude as
   visual content, not just file paths. Claude can assess focus quality, detect
   cells, and make semantic judgments about image content. <While this is a good idea,
   using Claude to assess focus quality, etc. should be a last resort. There are many
   established tools, such as Cellpose (https://www.cellpose.org/) to detect cells.
   Claude should pass off as many image analysis tasks as possible to established
   tools, via pycro-manager hooks, and synthesize the information they provide to 
   make a suggestion about what to do next in the acquisiton.>
2. **Software autofocus** — a Python sweep algorithm computes focus metrics across
   a Z range and moves to the sharpest plane, with Claude reporting the result.
   <Please see if there is a way to implement autofocus with pycro-manager hooks
   and run the Python-native version via pycro-manager instead of using a custom
   implementation here. If so, is this easier or harder to use than the Python-native
   implementation here?>
3. **Position list management** — the agent can build, inspect, and navigate a
   named position list (XY or XYZ), matching the biologist's normal MM workflow.
4. **Adaptive multiposition acquisition** — visit each position in a list, run a
   configurable per-position protocol (snap, Z-stack, timelapse), and optionally
   autofocus at each stop before acquiring.
5. **Hook-based adaptive acquisition** (advanced) — a library of pre-coded
   pycro-manager image-processing hooks that the agent can select and configure,
   enabling real-time feedback loops that run at acquisition speed without Claude
   in the inner loop. <Can we use Claude to build the code adaptive image-processing 
   hooks, save them, and then call these hooks for acquisition, rather than have 
   Claude take care of the adaptive acquisiton? That is, when a user requests a new
   automated acquistion feature, the agent will build a script that works with
   pycro-manager, then save this script for current and future use, then call this
   script as a pycro-manager hook. Claude then only handles using the output of the 
   hook to suggest what to do next.>

---

## 2. Approach and Key Design Decisions

### 2.1 Three tiers of adaptivity

Adaptive acquisition in microscopy spans a spectrum of latency requirements:

| Tier | Speed requirement | Who is in the loop | Use cases |
|------|-------------------|-------------------|-----------|
| **Slow — Claude reasoning** | Seconds to minutes | Claude (API calls) | Semantic analysis, interactive autofocus, quality checks <How much of this can be offloaded to Python/pycro-manager hooks? Claude can focus on interpreting the results,
rather than handling the work itself. Autofocus, for example, could run independently of Claude as a hook. Claude could
simply call the autofocus hook when the user asks for it or when Claude decides an autofocus is needed.> |
| **Medium — Python metrics** | 10–500 ms per frame | Python (NumPy/SciPy) | Sweep autofocus, exposure adaptation, intensity screening |
| **Fast — C extension** | Sub-millisecond | Native code, pycro hooks | Real-time focus feedback, hardware-triggered events |

v2 targets tiers 1 and 2. Tier 3 is partially addressed through pre-coded hooks
(section 8) but is not designed for AI-in-the-loop operation. <Could Tier 3 be a
part of the AI-in-the-loop operation, where the AI simply looks at the output of 
the hook after calling it? Or looks at the image after the hook finishes running?>

### 2.2 Vision: images as multimodal tool results

The Anthropic Messages API allows tool result content to be a list of blocks
rather than a plain string. An image block passes a base64-encoded PNG directly
into Claude's context:

```python
# execute_tool returns this list instead of a JSON string for image tools
[
    {
        "type": "text",
        "text": '{"z_um": 45.0, "focus_metric": 3821.4, "mean_intensity": 112.3}'
    },
    {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "<base64-encoded PNG thumbnail>"
        }
    }
]
```

Claude then receives the image as if it were part of the conversation and can
make visual judgments ("the sample looks in focus", "I can see mitotic cells",
"the background is too bright"). This is the correct mechanism for vision tasks.

**Why not a separate analyze_image tool that calls a vision model?** Claude
*is* the vision model. Sending the thumbnail directly in the tool result is
simpler, cheaper, and avoids a second API call.

**Why a thumbnail rather than the full image?** Raw fluorescence images are
typically 2048×2048 16-bit (8 MB). Sending that to the API is expensive and
slow. A 512×512 8-bit PNG is sufficient for Claude's visual assessment (≈ 200 KB
before base64 overhead). The full-resolution image is always saved to disk for
subsequent analysis.

### 2.3 Autofocus: Python in the inner loop, Claude at the boundary

Calling the Anthropic API once per Z position during a focus sweep would add
~1–3 seconds of latency per slice — unacceptably slow. The focus sweep algorithm
runs entirely in Python (NumPy), with no API calls in the inner loop.

Claude is involved only at the boundary:
- **Before**: Claude decides to call `run_autofocus` with the appropriate parameters.
- **After**: the tool returns the best Z position, the metric curve, and optionally
  a thumbnail of the in-focus image; Claude reports this to the user.

If the user asks Claude to visually confirm focus ("does this look sharp to you?"),
Claude can call `snap_and_analyze` after autofocus completes.

### 2.4 Multiposition: a Python loop orchestrated by Claude

For multiposition acquisition with per-position autofocus, the options are:

**Option A: Claude iterates manually.** Claude calls `go_to_position`, then
`run_autofocus`, then `run_zstack`, in a loop. This works but generates many API
calls and is slow for long position lists.

**Option B: A single high-level Python tool handles the loop.** Claude calls
`run_multiposition_with_autofocus(names=[...], ...)` once. Python handles the
loop entirely. Claude receives a summary at the end.

**Option B is the right choice for most workflows.** It is fast, robust, and
generates a predictable number of API calls regardless of position list length.
Claude retains control over setup: it chooses which positions to include, the
autofocus parameters, and the per-position protocol.

Option A (manual iteration) remains available for exploratory or interactive
sessions where the biologist wants to inspect each position before proceeding.

### 2.5 Hooks: pre-coded strategies, not Claude-generated code

pycro-manager's `image_process_fn` and event-generation hooks are callback
functions that execute inside the acquisition loop. Having Claude generate hook
code at runtime would be unsafe and fragile. <Could Claude generate hook code
with a warning that it might not work? Could safety.py be augmented to keep
operation safe even if the hook code is bad?>

Instead, a library of pre-coded hook strategies is implemented in `hooks.py`.
Claude selects and configures a strategy by name; the hook function itself is
pre-audited Python. This gives biologists access to real-time adaptive acquisition
without any risk from dynamically generated code. <This is a good idea, and it
would be great to ship with some simple hooks already written. Do not compromise
this idea for the other suggestions in this document. Ensure whatever you decide,
you keep this pre-coded hook strategy.>

---

## 3. New Module Architecture

```
microclaw/
├── agent.py              # Modified: execute_tool handles multimodal returns
├── controller.py         # Unchanged
├── tools.py              # Extended: new tools added
├── tools_schema.py       # Extended: schemas for new tools
├── safety.py             # Extended: autofocus range constraints
├── config.py             # Unchanged
├── errors.py             # Unchanged
├── image_analysis.py     # NEW: focus metrics, thumbnail generation
├── autofocus.py          # NEW: sweep autofocus algorithm
├── positions.py          # NEW: in-memory position list
└── hooks.py              # NEW: pre-coded pycro-manager hook strategies
```

---

## 4. `image_analysis.py` — Focus Metrics and Thumbnail Generation

This module contains all image analysis that runs in Python (no API calls).

```python
# microclaw/image_analysis.py
from __future__ import annotations
import base64
import io
from typing import NamedTuple

import numpy as np
from PIL import Image  # pillow


class ImageStats(NamedTuple):
    focus_metric: float     # Laplacian variance (higher = sharper)
    mean_intensity: float
    max_intensity: float
    min_intensity: float
    saturated_fraction: float  # fraction of pixels at bit-depth max


def laplacian_variance(image: np.ndarray) -> float:
    """Compute Laplacian variance as a focus metric.

    Higher values indicate sharper focus. Works on both 8-bit and 16-bit images.
    Uses a 3x3 Laplacian kernel.
    """
    from scipy.ndimage import laplace
    img_float = image.astype(np.float64)
    return float(np.var(laplace(img_float)))


def compute_stats(image: np.ndarray) -> ImageStats:
    """Compute image statistics including focus metric."""
    bit_depth_max = float(np.iinfo(image.dtype).max) if np.issubdtype(image.dtype, np.integer) else 1.0
    saturated = np.sum(image >= bit_depth_max) / image.size
    return ImageStats(
        focus_metric=laplacian_variance(image),
        mean_intensity=float(np.mean(image)),
        max_intensity=float(np.max(image)),
        min_intensity=float(np.min(image)),
        saturated_fraction=float(saturated),
    )


def make_thumbnail(
    image: np.ndarray,
    max_size: int = 512,
    percentile_low: float = 2.0,
    percentile_high: float = 99.8,
) -> str:
    """Convert a microscopy image to a base64-encoded PNG thumbnail for Claude.

    Handles 8-bit and 16-bit grayscale images. Applies percentile contrast
    stretching so dim fluorescence images are visible to the model.
    Returns a base64-encoded PNG string.
    """
    img = image.astype(np.float32)
    p_low = np.percentile(img, percentile_low)
    p_high = np.percentile(img, percentile_high)
    if p_high > p_low:
        img = (img - p_low) / (p_high - p_low)
    img = np.clip(img, 0, 1)
    img_8bit = (img * 255).astype(np.uint8)

    pil_img = Image.fromarray(img_8bit, mode='L')

    # Resize preserving aspect ratio
    pil_img.thumbnail((max_size, max_size), Image.LANCZOS)

    buf = io.BytesIO()
    pil_img.save(buf, format='PNG')
    buf.seek(0)
    return base64.standard_b64encode(buf.read()).decode('ascii')


def snap_to_numpy(ctrl) -> np.ndarray:
    """Snap an image and return it as a NumPy array via pycro-manager."""
    ctrl.core.snap_image()
    ctrl.core.wait_for_image_synced()
    tagged = ctrl.core.get_tagged_image()
    width = tagged.tags['Width']
    height = tagged.tags['Height']
    pixels = np.frombuffer(tagged.pix, dtype=np.uint16)
    return pixels.reshape(height, width)
```

**Dependencies added:** `scipy`, `Pillow`.

---

## 5. `autofocus.py` — Sweep Autofocus

```python
# microclaw/autofocus.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

import numpy as np

from microclaw.controller import MicroscopeController
from microclaw.image_analysis import snap_to_numpy, laplacian_variance


@dataclass
class AutofocusResult:
    best_z_um: float
    metric_values: list[float]   # one per step, in z_positions order
    z_positions: list[float]
    settled: bool                # True if the peak was unambiguous


def sweep_autofocus(
    ctrl: MicroscopeController,
    z_start_um: float,
    z_end_um: float,
    z_step_um: float,
    settle_ms: int = 50,
    metric_fn: Callable[[np.ndarray], float] = laplacian_variance,
) -> AutofocusResult:
    """Move through Z positions, measure focus at each step, return best Z.

    Does NOT move back to the starting position; moves to best_z_um.
    The caller (run_autofocus tool) is responsible for safety checks.
    """
    import time

    focus_device = ctrl.core.get_focus_device()

    z_positions = list(np.arange(z_start_um, z_end_um + z_step_um / 2, z_step_um))
    metric_values = []

    for z in z_positions:
        ctrl.core.set_position(z)
        ctrl.core.wait_for_device(focus_device)
        if settle_ms > 0:
            time.sleep(settle_ms / 1000.0)
        image = snap_to_numpy(ctrl)
        metric_values.append(metric_fn(image))

    best_idx = int(np.argmax(metric_values))
    best_z = z_positions[best_idx]

    # Move to best position
    ctrl.core.set_position(best_z)
    ctrl.core.wait_for_device(focus_device)

    # "Settled" means the peak is not at the boundary of the sweep
    settled = 0 < best_idx < len(z_positions) - 1

    return AutofocusResult(
        best_z_um=best_z,
        metric_values=metric_values,
        z_positions=z_positions,
        settled=settled,
    )


def coarse_then_fine_autofocus(
    ctrl: MicroscopeController,
    z_range_um: float,
    coarse_step_um: float,
    fine_step_um: float,
    settle_ms: int = 50,
) -> AutofocusResult:
    """Two-pass autofocus: coarse sweep to find approximate peak, then fine.

    Starts from the current Z position. Searches ± z_range_um/2 coarsely,
    then ± coarse_step_um around the coarse best.
    """
    current_z = ctrl.core.get_position()
    z_start = current_z - z_range_um / 2
    z_end = current_z + z_range_um / 2

    coarse = sweep_autofocus(ctrl, z_start, z_end, coarse_step_um, settle_ms)
    fine_start = coarse.best_z_um - coarse_step_um
    fine_end = coarse.best_z_um + coarse_step_um

    return sweep_autofocus(ctrl, fine_start, fine_end, fine_step_um, settle_ms)
```

---

## 6. `positions.py` — Position List Manager

Micro-Manager's GUI has a built-in position list (XY Stage Control → Mark/Go
Position List). v2 implements an in-memory equivalent in Python that the agent
can build and navigate. A future iteration could read/write MM's native `.pos`
XML format for interoperability. <Let's not make this a future iteration, but the
current iteration. Let's have Claude read/write from MM's native position list
to perform multiposition acquisitions.>

```python
# microclaw/positions.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Position:
    name: str
    x_um: float
    y_um: float
    z_um: Optional[float] = None   # None if only XY was recorded


class PositionList:
    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}

    def add(self, name: str, x_um: float, y_um: float, z_um: Optional[float] = None) -> Position:
        pos = Position(name=name, x_um=x_um, y_um=y_um, z_um=z_um)
        self._positions[name] = pos
        return pos

    def get(self, name: str) -> Position:
        if name not in self._positions:
            raise KeyError(f"Position '{name}' not found.")
        return self._positions[name]

    def all(self) -> list[Position]:
        return list(self._positions.values())

    def names(self) -> list[str]:
        return list(self._positions.keys())

    def delete(self, name: str) -> None:
        if name not in self._positions:
            raise KeyError(f"Position '{name}' not found.")
        del self._positions[name]

    def clear(self) -> None:
        self._positions.clear()
```

The `PositionList` instance is held by `MicroscopeController` (extended in v2):

```python
# controller.py addition
from microclaw.positions import PositionList

class MicroscopeController:
    def __init__(self, port: int = 4827):
        self._core = Core(port=port)
        self._studio = Studio(port=port)
        self.positions = PositionList()   # new in v2
```

---

## 7. New Tool Implementations (`tools.py` extensions)

### 7.1 Image capture with visual return (`snap_and_analyze`)

This tool replaces the simple `snap_image` when the agent or user wants Claude
to see the image. It returns a multimodal content list.

```python
# tools.py (new)
from microclaw.image_analysis import snap_to_numpy, compute_stats, make_thumbnail

def snap_and_analyze(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    thumbnail_size: int = 512,
) -> list:
    """Snap image, compute metrics, return thumbnail for Claude to see.

    Returns a multimodal content list (text + image) rather than a JSON string.
    The execute_tool dispatcher detects the list return and passes it directly
    as the tool_result content.
    """
    image = snap_to_numpy(ctrl)
    stats = compute_stats(image)
    z_um = round(ctrl.core.get_position(), 3)
    thumbnail_b64 = make_thumbnail(image, max_size=thumbnail_size)

    text_payload = {
        "z_um": z_um,
        "focus_metric": round(stats.focus_metric, 2),
        "mean_intensity": round(stats.mean_intensity, 1),
        "max_intensity": round(stats.max_intensity, 1),
        "saturated_fraction": round(stats.saturated_fraction, 4),
    }
    return [
        {"type": "text", "text": json.dumps(text_payload)},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": thumbnail_b64,
            },
        },
    ]
```

### 7.2 Autofocus

```python
# tools.py (new)
from microclaw.autofocus import coarse_then_fine_autofocus, sweep_autofocus

def run_autofocus(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    z_range_um: float,
    z_step_um: float,
    method: str = "coarse_then_fine",
    settle_ms: int = 50,
    return_thumbnail: bool = True,
) -> list | dict:
    """Sweep the Z axis and move to the sharpest focal plane.

    method='coarse_then_fine': coarse sweep over z_range_um, then fine
      sweep of ±z_step_um around the coarse best. z_step_um is the fine step;
      coarse step is 5× the fine step.
    method='sweep': single sweep from (current_z - z_range_um/2) to
      (current_z + z_range_um/2) in z_step_um increments.

    Safety: checks all Z positions in the sweep range before starting.
    """
    current_z = ctrl.core.get_position()
    z_start = current_z - z_range_um / 2
    z_end = current_z + z_range_um / 2

    guard.check_z(z_start)
    guard.check_z(z_end)
    guard.check_autofocus_range(z_range_um)

    if method == "coarse_then_fine":
        coarse_step = max(z_step_um * 5, 1.0)
        result = coarse_then_fine_autofocus(
            ctrl, z_range_um, coarse_step, z_step_um, settle_ms
        )
    else:
        result = sweep_autofocus(ctrl, z_start, z_end, z_step_um, settle_ms)

    payload = {
        "best_z_um": round(result.best_z_um, 3),
        "settled": result.settled,
        "metric_curve": [round(v, 2) for v in result.metric_values],
        "z_positions": [round(z, 3) for z in result.z_positions],
        "warning": None if result.settled else (
            "Peak focus was at the edge of the sweep range; consider widening z_range_um."
        ),
    }

    if not return_thumbnail:
        return payload

    # Snap at best Z and return thumbnail so Claude can visually confirm
    image = snap_to_numpy(ctrl)
    stats = compute_stats(image)
    payload["focus_metric_at_best"] = round(stats.focus_metric, 2)
    thumbnail_b64 = make_thumbnail(image)

    return [
        {"type": "text", "text": json.dumps(payload)},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": thumbnail_b64,
            },
        },
    ]
```

### 7.3 Position management

```python
# tools.py (new)
def mark_position(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    name: str,
    include_z: bool = True,
) -> dict:
    """Record the current stage position under a given name."""
    x = round(ctrl.core.get_x_position(), 3)
    y = round(ctrl.core.get_y_position(), 3)
    z = round(ctrl.core.get_position(), 3) if include_z else None
    ctrl.positions.add(name, x, y, z)
    entry = {"name": name, "x_um": x, "y_um": y}
    if z is not None:
        entry["z_um"] = z
    return {"status": f"Position '{name}' saved.", **entry}


def get_position_list(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """Return all named positions."""
    positions = [
        {"name": p.name, "x_um": p.x_um, "y_um": p.y_um,
         **({"z_um": p.z_um} if p.z_um is not None else {})}
        for p in ctrl.positions.all()
    ]
    return {"positions": positions, "count": len(positions)}


def go_to_position(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    name: str,
) -> dict:
    """Move to a previously saved named position."""
    pos = ctrl.positions.get(name)
    guard.check_xy(pos.x_um, pos.y_um)
    if pos.z_um is not None:
        guard.check_z(pos.z_um)

    ctrl.core.set_xy_position(pos.x_um, pos.y_um)
    _wait(ctrl, ctrl.core.get_xy_stage_device())
    if pos.z_um is not None:
        ctrl.core.set_position(pos.z_um)
        _wait(ctrl, ctrl.core.get_focus_device())

    return {"status": f"Moved to '{name}'.", "x_um": pos.x_um, "y_um": pos.y_um,
            **({"z_um": pos.z_um} if pos.z_um is not None else {})}


def delete_position(ctrl: MicroscopeController, guard: SafetyGuard, name: str) -> dict:
    ctrl.positions.delete(name)
    return {"status": f"Position '{name}' deleted."}


def clear_position_list(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    ctrl.positions.clear()
    return {"status": "Position list cleared."}
```

### 7.4 Multiposition acquisition

```python
# tools.py (new)
from pathlib import Path
from pycromanager import Acquisition, multi_d_acquisition_events

def run_multiposition_acquisition(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    position_names: list[str],
    protocol: str,
    save_dir: str,
    name: str = "multipos",
    protocol_params: dict | None = None,
) -> dict:
    """Visit each named position and run a protocol (snap, zstack, timelapse).

    protocol_params are forwarded to the per-position tool function.
    Returns a summary with per-position status.
    """
    params = protocol_params or {}
    results = []

    for pos_name in position_names:
        pos = ctrl.positions.get(pos_name)
        guard.check_xy(pos.x_um, pos.y_um)

        ctrl.core.set_xy_position(pos.x_um, pos.y_um)
        _wait(ctrl, ctrl.core.get_xy_stage_device())
        if pos.z_um is not None:
            guard.check_z(pos.z_um)
            ctrl.core.set_position(pos.z_um)
            _wait(ctrl, ctrl.core.get_focus_device())

        pos_save_dir = str(Path(save_dir) / pos_name)
        Path(pos_save_dir).mkdir(parents=True, exist_ok=True)

        try:
            if protocol == "snap":
                ctrl.studio.live().snap(True)
                results.append({"position": pos_name, "status": "snapped"})
            elif protocol == "zstack":
                r = run_zstack(ctrl, guard, save_dir=pos_save_dir, name=pos_name, **params)
                results.append({"position": pos_name, **r})
            elif protocol == "timelapse":
                r = run_timelapse(ctrl, guard, save_dir=pos_save_dir, name=pos_name, **params)
                results.append({"position": pos_name, **r})
            else:
                results.append({"position": pos_name, "error": f"Unknown protocol '{protocol}'."})
        except Exception as e:
            results.append({"position": pos_name, "error": str(e)})

    n_ok = sum(1 for r in results if "error" not in r)
    return {
        "status": f"{n_ok}/{len(position_names)} positions completed.",
        "results": results,
    }


def run_multiposition_with_autofocus(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    position_names: list[str],
    z_range_um: float,
    z_step_um: float,
    protocol: str,
    save_dir: str,
    name: str = "multipos_af",
    autofocus_method: str = "coarse_then_fine",
    settle_ms: int = 50,
    protocol_params: dict | None = None,
) -> dict:
    """Visit each position, autofocus, then run a protocol.

    At each position:
      1. Move to saved XY (and Z if recorded).
      2. Run autofocus sweep centered on current Z.
      3. Run the specified protocol.

    Returns per-position status including best-Z found.
    """
    params = protocol_params or {}
    results = []

    # Safety-check the autofocus range once before starting
    guard.check_autofocus_range(z_range_um)

    for pos_name in position_names:
        pos = ctrl.positions.get(pos_name)
        guard.check_xy(pos.x_um, pos.y_um)

        ctrl.core.set_xy_position(pos.x_um, pos.y_um)
        _wait(ctrl, ctrl.core.get_xy_stage_device())
        if pos.z_um is not None:
            guard.check_z(pos.z_um)
            ctrl.core.set_position(pos.z_um)
            _wait(ctrl, ctrl.core.get_focus_device())

        # Autofocus
        current_z = ctrl.core.get_position()
        z_start = current_z - z_range_um / 2
        z_end = current_z + z_range_um / 2
        try:
            guard.check_z(z_start)
            guard.check_z(z_end)
        except Exception as e:
            results.append({"position": pos_name, "error": f"Autofocus range out of bounds: {e}"})
            continue

        if autofocus_method == "coarse_then_fine":
            coarse_step = max(z_step_um * 5, 1.0)
            af_result = coarse_then_fine_autofocus(ctrl, z_range_um, coarse_step, z_step_um, settle_ms)
        else:
            af_result = sweep_autofocus(ctrl, z_start, z_end, z_step_um, settle_ms)

        best_z = af_result.best_z_um

        # Acquisition
        pos_save_dir = str(Path(save_dir) / pos_name)
        Path(pos_save_dir).mkdir(parents=True, exist_ok=True)
        try:
            if protocol == "snap":
                ctrl.studio.live().snap(True)
                results.append({"position": pos_name, "best_z_um": round(best_z, 3), "status": "snapped"})
            elif protocol == "zstack":
                r = run_zstack(ctrl, guard, save_dir=pos_save_dir, name=pos_name, **params)
                results.append({"position": pos_name, "best_z_um": round(best_z, 3), **r})
            elif protocol == "timelapse":
                r = run_timelapse(ctrl, guard, save_dir=pos_save_dir, name=pos_name, **params)
                results.append({"position": pos_name, "best_z_um": round(best_z, 3), **r})
            else:
                results.append({"position": pos_name, "error": f"Unknown protocol '{protocol}'."})
        except Exception as e:
            results.append({"position": pos_name, "best_z_um": round(best_z, 3), "error": str(e)})

    n_ok = sum(1 for r in results if "error" not in r)
    return {
        "status": f"{n_ok}/{len(position_names)} positions completed with autofocus.",
        "results": results,
    }
```

---

## 8. `hooks.py` — Pre-coded Adaptive Acquisition Strategies

These hooks run inside pycro-manager's `Acquisition` context manager. They are
called per-image at acquisition speed (no API calls). Claude selects and
configures a strategy at setup time via `run_adaptive_acquisition`.

```python
# microclaw/hooks.py
from __future__ import annotations
from typing import Any
import numpy as np
from microclaw.image_analysis import laplacian_variance


class FocusFeedbackHook:
    """Adjusts Z position after each image to maintain focus.

    Uses a proportional controller: if the focus metric drops below
    `threshold_fraction` of the reference metric, jogs Z and re-acquires.
    Suitable for drift-correcting timelapses.
    """

    def __init__(self, ctrl, threshold_fraction: float = 0.85, z_step_um: float = 0.5, max_jogs: int = 5):
        self.ctrl = ctrl
        self.threshold = threshold_fraction
        self.z_step = z_step_um
        self.max_jogs = max_jogs
        self.reference_metric: float | None = None

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue) -> tuple[np.ndarray, dict]:
        metric = laplacian_variance(image)
        if self.reference_metric is None:
            self.reference_metric = metric
            return image, metadata

        if metric < self.reference_metric * self.threshold:
            # Simple jog-and-check to recover focus
            focus_device = self.ctrl.core.get_focus_device()
            for _ in range(self.max_jogs):
                current_z = self.ctrl.core.get_position()
                self.ctrl.core.set_position(current_z + self.z_step)
                self.ctrl.core.wait_for_device(focus_device)
                self.ctrl.core.snap_image()
                self.ctrl.core.wait_for_image_synced()
                tagged = self.ctrl.core.get_tagged_image()
                pixels = np.frombuffer(tagged.pix, dtype=np.uint16).reshape(image.shape)
                new_metric = laplacian_variance(pixels)
                if new_metric >= self.reference_metric * self.threshold:
                    self.reference_metric = new_metric
                    break
        return image, metadata


class IntensityAdaptiveHook:
    """Adjusts exposure if mean intensity is outside a target range.

    Useful for timelapses where sample brightness changes over time
    (e.g., photoactivation, growth).
    """

    def __init__(self, ctrl, guard, target_mean: float, tolerance: float = 0.1,
                 min_exposure_ms: float = 1.0, max_exposure_ms: float = 1000.0):
        self.ctrl = ctrl
        self.guard = guard
        self.target_mean = target_mean
        self.tolerance = tolerance
        self.min_exp = min_exposure_ms
        self.max_exp = max_exposure_ms

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue) -> tuple[np.ndarray, dict]:
        mean = float(np.mean(image))
        if abs(mean - self.target_mean) / self.target_mean > self.tolerance:
            ratio = self.target_mean / max(mean, 1.0)
            current_exp = self.ctrl.core.get_exposure()
            new_exp = float(np.clip(current_exp * ratio, self.min_exp, self.max_exp))
            try:
                self.guard.check_exposure(new_exp)
                self.ctrl.core.set_exposure(new_exp)
            except Exception:
                pass
        return image, metadata


class PositionFilterHook:
    """Rejects positions in a multiposition acquisition where no signal is found.

    Compares mean intensity against a threshold. If the image is too dark
    (no cell at this position), the remaining events for this position are
    dropped. Requires pycro-manager's event_queue access.

    NOTE: Event cancellation via the event_queue is pycro-manager ≥1.0 API.
    Verify the API against the installed pycro-manager version.
    """

    def __init__(self, min_mean_intensity: float = 100.0):
        self.min_mean = min_mean_intensity
        self.rejected_positions: list[int] = []

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue) -> tuple[np.ndarray, dict] | None:
        mean = float(np.mean(image))
        if mean < self.min_mean:
            pos_idx = metadata.get("position_index", -1)
            if pos_idx not in self.rejected_positions:
                self.rejected_positions.append(pos_idx)
            return None  # returning None signals pycro-manager to discard this image
        return image, metadata
```

### 8.1 The `run_adaptive_acquisition` tool

```python
# tools.py (new)
HOOK_REGISTRY = {
    "focus_feedback": FocusFeedbackHook,
    "intensity_adaptive": IntensityAdaptiveHook,
    "position_filter": PositionFilterHook,
}

def run_adaptive_acquisition(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    z_start_um: float,
    z_end_um: float,
    z_step_um: float,
    save_dir: str,
    hook_strategy: str,
    hook_params: dict | None = None,
    channel: str | None = None,
    name: str = "adaptive",
) -> dict:
    """Run a Z-stack acquisition with a real-time adaptive hook strategy.

    hook_strategy must be one of: 'focus_feedback', 'intensity_adaptive',
    'position_filter'.
    hook_params are forwarded to the hook class constructor.
    """
    if hook_strategy not in HOOK_REGISTRY:
        return {"error": f"Unknown strategy '{hook_strategy}'. Valid: {list(HOOK_REGISTRY)}."}

    guard.check_z(z_start_um)
    guard.check_z(z_end_um)
    if channel:
        guard.check_channel(channel)

    params = hook_params or {}
    hook_cls = HOOK_REGISTRY[hook_strategy]

    # Inject ctrl/guard for hooks that need hardware access
    if hook_strategy in ("focus_feedback", "intensity_adaptive"):
        params.setdefault("ctrl", ctrl)
    if hook_strategy == "intensity_adaptive":
        params.setdefault("guard", guard)

    hook = hook_cls(**params)

    kwargs: dict[str, Any] = {
        "z_start": z_start_um,
        "z_end": z_end_um,
        "z_step": z_step_um,
    }
    if channel:
        kwargs.update(channel_group="Channel", channels=[channel])

    events = multi_d_acquisition_events(**kwargs)

    with Acquisition(
        directory=save_dir,
        name=name,
        image_process_fn=hook.image_process_fn,
        show_display=True,
    ) as acq:
        acq.acquire(events)

    return {"status": "Adaptive acquisition complete.", "dataset_path": str(Path(save_dir) / name)}
```

---

## 9. Modified `execute_tool` — Multimodal Return Handling

`snap_and_analyze` and `run_autofocus` (when `return_thumbnail=True`) return a
`list` of content blocks rather than a `dict`. The dispatcher must handle both:

```python
# tools.py (modified execute_tool)
def execute_tool(
    name: str,
    tool_input: dict,
    ctrl: MicroscopeController,
    guard: SafetyGuard,
) -> str | list:
    """Execute a tool and return either a JSON string or a multimodal content list.

    The agent loop checks the return type and passes it directly as
    tool_result content (the Messages API accepts both str and list[block]).
    """
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool '{name}'."})
    try:
        result = fn(ctrl, guard, **tool_input)
        # Multimodal tools return a list; text tools return a dict
        if isinstance(result, list):
            return result
        return json.dumps(result)
    except SafetyViolation as e:
        return json.dumps({"error": f"Safety constraint prevented this action: {e}"})
    except Exception as e:
        return json.dumps({
            "error": f"{type(e).__name__}: {e}",
            "hint": (
                "This may be a hardware error (device busy, stage at limit, "
                "device not found) or a connection problem."
            ),
        })
```

The agent loop in `agent.py` is already compatible: `content` in a tool_result
block can be either a string or a list of blocks, and the Anthropic SDK and API
both accept either form. No changes to `agent.py` are required.

---

## 10. Tool Schema Additions (`tools_schema.py`)

### New entries (abbreviated — full schemas follow the same pattern as v1):

```python
{
    "name": "snap_and_analyze",
    "description": (
        "Snap a single image and return it to you as a thumbnail for visual analysis, "
        "along with focus metric and intensity statistics. Use this when you need to "
        "see the image to make a decision (focus assessment, cell detection, quality check). "
        "For a quick snap without analysis, use snap_image."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "thumbnail_size": {
                "type": "integer",
                "description": "Maximum thumbnail dimension in pixels. Default 512.",
                "default": 512,
            }
        },
        "required": [],
    },
},
{
    "name": "run_autofocus",
    "description": (
        "Sweep the Z axis and move to the focal plane with the highest sharpness. "
        "Uses Laplacian variance as the focus metric. Call get_system_state first "
        "if you are unsure of the current Z position. "
        "'coarse_then_fine' (default) is recommended; it performs a coarse sweep "
        "over z_range_um then a fine sweep of ±z_step_um around the best coarse "
        "position. Returns the best Z, the metric curve, and an image thumbnail."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "z_range_um": {"type": "number", "description": "Total range to sweep in µm (centered on current Z)."},
            "z_step_um": {"type": "number", "description": "Fine step size in µm (coarse step is 5×)."},
            "method": {
                "type": "string",
                "enum": ["coarse_then_fine", "sweep"],
                "default": "coarse_then_fine",
            },
            "settle_ms": {"type": "integer", "description": "Wait time in ms after each Z move. Default 50.", "default": 50},
            "return_thumbnail": {"type": "boolean", "default": True},
        },
        "required": ["z_range_um", "z_step_um"],
    },
},
{
    "name": "mark_position",
    "description": (
        "Save the current stage position under a name for later retrieval. "
        "Use this to build a position list for multiposition acquisitions. "
        "If include_z is true (default), the Z position is also recorded."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "A unique name for this position (e.g. 'cell_1', 'FOV_A')."},
            "include_z": {"type": "boolean", "default": True},
        },
        "required": ["name"],
    },
},
{
    "name": "get_position_list",
    "description": "Return all named positions saved in the current session.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
},
{
    "name": "go_to_position",
    "description": "Move the stage to a previously saved named position.",
    "input_schema": {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
},
{
    "name": "delete_position",
    "description": "Remove a named position from the position list.",
    "input_schema": {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
},
{
    "name": "clear_position_list",
    "description": "Delete all saved positions.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
},
{
    "name": "run_multiposition_acquisition",
    "description": (
        "Visit each named position in order and run a protocol at each one. "
        "protocol must be one of: 'snap', 'zstack', 'timelapse'. "
        "protocol_params are forwarded to the per-position protocol "
        "(e.g. for 'zstack': z_start_um, z_end_um, z_step_um)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "position_names": {"type": "array", "items": {"type": "string"}},
            "protocol": {"type": "string", "enum": ["snap", "zstack", "timelapse"]},
            "save_dir": {"type": "string"},
            "name": {"type": "string", "default": "multipos"},
            "protocol_params": {"type": "object"},
        },
        "required": ["position_names", "protocol", "save_dir"],
    },
},
{
    "name": "run_multiposition_with_autofocus",
    "description": (
        "Visit each named position, autofocus at each one, then run a protocol. "
        "This is the primary tool for automated plate or slide surveys where focus "
        "varies across positions. Use when the user asks to 'image multiple wells', "
        "'scan the slide', or 'autofocus at each position'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "position_names": {"type": "array", "items": {"type": "string"}},
            "z_range_um": {"type": "number", "description": "Autofocus sweep range in µm."},
            "z_step_um": {"type": "number", "description": "Autofocus fine step in µm."},
            "protocol": {"type": "string", "enum": ["snap", "zstack", "timelapse"]},
            "save_dir": {"type": "string"},
            "name": {"type": "string", "default": "multipos_af"},
            "autofocus_method": {"type": "string", "enum": ["coarse_then_fine", "sweep"], "default": "coarse_then_fine"},
            "settle_ms": {"type": "integer", "default": 50},
            "protocol_params": {"type": "object"},
        },
        "required": ["position_names", "z_range_um", "z_step_um", "protocol", "save_dir"],
    },
},
{
    "name": "run_adaptive_acquisition",
    "description": (
        "Run a Z-stack acquisition with a real-time adaptive image-processing hook. "
        "hook_strategy options: "
        "'focus_feedback' — corrects Z drift per frame (timelapse use case); "
        "'intensity_adaptive' — adjusts exposure if brightness drifts; "
        "'position_filter' — skips positions where no signal is detected."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "z_start_um": {"type": "number"},
            "z_end_um": {"type": "number"},
            "z_step_um": {"type": "number"},
            "save_dir": {"type": "string"},
            "hook_strategy": {"type": "string", "enum": ["focus_feedback", "intensity_adaptive", "position_filter"]},
            "hook_params": {"type": "object", "description": "Parameters forwarded to the hook (strategy-specific)."},
            "channel": {"type": "string"},
            "name": {"type": "string", "default": "adaptive"},
        },
        "required": ["z_start_um", "z_end_um", "z_step_um", "save_dir", "hook_strategy"],
    },
},
```

---

## 11. Safety Extensions

<I think these will be covered by the stage constrains already in place.
What is the reason for the additional constraints, e.g. in z?>

### 11.1 New constraint: `autofocus.max_z_range_um`

Add to `safety_config.yaml`:

```yaml
autofocus:
  max_z_range_um: 100.0   # cannot sweep more than 100 µm in one autofocus call
```

Add to `SafetyConstraints` and `SafetyGuard`:

```python
# safety.py additions

@dataclass
class AutofocusConstraints:
    max_z_range_um: Optional[float] = None


@dataclass
class SafetyConstraints:
    stage: StageConstraints = field(default_factory=StageConstraints)
    camera: CameraConstraints = field(default_factory=CameraConstraints)
    autofocus: AutofocusConstraints = field(default_factory=AutofocusConstraints)
    allowed_channels: Optional[list[str]] = None
    forbidden_properties: list[ForbiddenProperty] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str) -> SafetyConstraints:
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}
        # ... existing parsing ...
        af_cfg = cfg.get("autofocus", {})
        return cls(
            ...existing...,
            autofocus=AutofocusConstraints(**af_cfg),
        )


class SafetyGuard:
    # ... existing methods ...

    def check_autofocus_range(self, z_range_um: float) -> None:
        limit = self._c.autofocus.max_z_range_um
        if limit is not None and z_range_um > limit:
            raise SafetyViolation(
                f"Autofocus range {z_range_um:.1f} µm exceeds the maximum allowed ({limit:.1f} µm)."
            )
```

### 11.2 Autofocus boundary pre-check

<This is a good idea, but perhaps max_z_range_um could instead just be
the maximum range of the z stage.>

The `run_autofocus` tool computes `z_start = current_z - z_range/2` and
`z_end = current_z + z_range/2` and calls `guard.check_z` on both before
starting the sweep. This prevents the sweep from violating stage limits even if
the autofocus range itself is within the allowed `max_z_range_um`.

---

## 12. System Prompt Updates

Add to `SYSTEM_PROMPT` in `agent.py`:

```
Image analysis:
- Use snap_and_analyze (not snap_image) when you need to see or assess an image.
- The focus_metric field (Laplacian variance) indicates sharpness: higher is better.
  Typical in-focus values depend on sample and magnification; relative comparisons
  are more reliable than absolute thresholds.
- If an image looks blurry, call run_autofocus before re-imaging. <suggest this to the user, but don't automatically call run_autofocus unless the user explicitly tells you to.>
- Never estimate focus from metric values alone without visual confirmation for
  critical acquisitions.

Position lists:
- Use mark_position to record interesting locations before starting a survey.
- Use run_multiposition_with_autofocus for automated multi-site imaging — do not
  manually loop over go_to_position unless the user specifically asks for it.

Autofocus:
- Start with z_range_um=20 and z_step_um=0.5 as a reasonable default for a 20× objective.
  Widen the range if the warning says the peak was at the boundary.
- Always report the best_z_um found and whether focus was settled.

Adaptive acquisition:
- run_adaptive_acquisition hooks run at acquisition speed without Claude in the loop.
  Prefer run_multiposition_with_autofocus for position-level adaptation, and
  run_adaptive_acquisition for frame-level adaptation during a single acquisition.
```

---

## 13. Example Workflows

<These are good examples. Please keep these headings.>

### 13.1 Interactive autofocus

```
User: "Can you focus on my sample?"

Claude → get_system_state
  Result: {z_um: 45.0, ...}

Claude → run_autofocus(z_range_um=20, z_step_um=0.5)
  Result: {best_z_um: 48.2, settled: true, ...} + thumbnail image

Claude (sees image): "I ran a focus sweep from 35 to 55 µm and found the sharpest
  focus at Z = 48.2 µm (focus metric 4231). The image looks sharp — I can see the
  cell boundaries clearly."
```

### 13.2 Building a position list and imaging

```
User: "I've moved the stage to three interesting cells. Mark each one as I tell you,
  then run a DAPI z-stack at each with autofocus."

Claude → (user moves stage) mark_position(name="cell_1")
Claude → (user moves stage) mark_position(name="cell_2")
Claude → (user moves stage) mark_position(name="cell_3")

User: "Now image all three."

Claude → run_multiposition_with_autofocus(
    position_names=["cell_1", "cell_2", "cell_3"],
    z_range_um=20, z_step_um=0.5,
    protocol="zstack",
    save_dir="/data/today",
    protocol_params={"z_start_um": -5, "z_end_um": 5, "z_step_um": 1, "channel": "DAPI"}
  )
  Result: {status: "3/3 positions completed with autofocus.", results: [...]}

Claude: "All three cells were imaged successfully. Best focus was found at Z=48.2 µm
  (cell_1), Z=51.7 µm (cell_2), and Z=49.1 µm (cell_3). Z-stacks saved to
  /data/today/{cell_1,cell_2,cell_3}/."
```

### 13.3 Semantic image-guided decision

```
User: "Snap an image and tell me if there's a cell in frame."

Claude → snap_and_analyze()
  Result: {focus_metric: 1850.3, mean_intensity: 85.2, ...} + thumbnail

Claude (looks at image): "I can see what appears to be a single elongated cell in the
  center-left of the frame. The nucleus is visible as a brighter oval region. Would you
  like me to move the stage to center it and acquire a z-stack?"
```

### 13.4 Drift-corrected timelapse

```
User: "Run a 50-frame timelapse every 30 seconds with Z drift correction."

Claude → run_adaptive_acquisition(
    z_start_um=45, z_end_um=55, z_step_um=1,
    save_dir="/data/timelapse",
    hook_strategy="focus_feedback",
    hook_params={"threshold_fraction": 0.85, "z_step_um": 0.5}
  )
```

---

## 14. Testing Plan

### 14.1 Unit tests: image analysis (`tests/test_image_analysis.py`)

```python
import numpy as np
from microclaw.image_analysis import laplacian_variance, compute_stats, make_thumbnail

def test_laplacian_variance_sharp_vs_blurry():
    """Sharp images (high-frequency content) should have higher metric."""
    from scipy.ndimage import gaussian_filter
    sharp = np.random.randint(0, 65535, (256, 256), dtype=np.uint16)
    blurry = gaussian_filter(sharp.astype(np.float32), sigma=5).astype(np.uint16)
    assert laplacian_variance(sharp) > laplacian_variance(blurry)

def test_make_thumbnail_returns_base64_png():
    img = np.random.randint(0, 65535, (512, 512), dtype=np.uint16)
    b64 = make_thumbnail(img, max_size=128)
    import base64, io
    from PIL import Image
    raw = base64.standard_b64decode(b64)
    pil = Image.open(io.BytesIO(raw))
    assert pil.size == (128, 128)

def test_thumbnail_clips_max_size():
    img = np.zeros((1024, 2048), dtype=np.uint16)
    b64 = make_thumbnail(img, max_size=256)
    import base64, io
    from PIL import Image
    raw = base64.standard_b64decode(b64)
    pil = Image.open(io.BytesIO(raw))
    assert max(pil.size) == 256

def test_compute_stats_saturated():
    img = np.full((64, 64), 65535, dtype=np.uint16)
    stats = compute_stats(img)
    assert stats.saturated_fraction == 1.0
```

### 14.2 Unit tests: autofocus (`tests/test_autofocus.py`)

```python
import numpy as np
import pytest
from unittest.mock import MagicMock, call
from microclaw.autofocus import sweep_autofocus, AutofocusResult


def make_ctrl_with_focus_at(best_z: float, width: int = 64):
    """Mock controller that returns images with focus metric peaking at best_z."""
    core = MagicMock()
    core.get_focus_device.return_value = "DStage"

    current_z = [50.0]

    def set_position(z):
        current_z[0] = z

    def get_position():
        return current_z[0]

    def get_tagged_image():
        z = current_z[0]
        # Focus metric peaks sharply at best_z (Gaussian envelope)
        sigma = 3.0
        sharpness = np.exp(-((z - best_z) ** 2) / (2 * sigma ** 2))
        noise = np.random.normal(0, 0.02)
        # Encode as a 64×64 image whose Laplacian variance reflects sharpness
        base = (np.random.rand(width, width) * sharpness + noise).astype(np.float32)
        pixels = (base * 65535).clip(0, 65535).astype(np.uint16)
        tagged = MagicMock()
        tagged.pix = pixels.tobytes()
        tagged.tags = {"Width": width, "Height": width}
        return tagged

    core.set_position.side_effect = set_position
    core.get_position.side_effect = get_position
    core.get_tagged_image.side_effect = get_tagged_image
    core.snap_image = MagicMock()
    core.wait_for_image_synced = MagicMock()
    core.wait_for_device = MagicMock()

    ctrl = MagicMock()
    ctrl.core = core
    return ctrl


def test_sweep_finds_correct_z():
    """Sweep should move to the Z with the highest focus metric."""
    best_z = 52.0
    ctrl = make_ctrl_with_focus_at(best_z)
    result = sweep_autofocus(ctrl, z_start_um=45.0, z_end_um=55.0, z_step_um=1.0, settle_ms=0)
    assert abs(result.best_z_um - best_z) <= 1.0  # within one step


def test_sweep_settled_when_peak_not_at_boundary():
    ctrl = make_ctrl_with_focus_at(50.0)
    result = sweep_autofocus(ctrl, z_start_um=45.0, z_end_um=55.0, z_step_um=1.0, settle_ms=0)
    assert result.settled


def test_sweep_not_settled_when_peak_at_boundary():
    ctrl = make_ctrl_with_focus_at(45.0)  # best is at the sweep start boundary
    result = sweep_autofocus(ctrl, z_start_um=45.0, z_end_um=55.0, z_step_um=1.0, settle_ms=0)
    assert not result.settled
```

### 14.3 Unit tests: position list (`tests/test_positions.py`)

```python
import pytest
from microclaw.positions import PositionList

def test_add_and_retrieve():
    pl = PositionList()
    pl.add("cell_1", x_um=100.0, y_um=-50.0, z_um=45.0)
    pos = pl.get("cell_1")
    assert pos.x_um == 100.0
    assert pos.z_um == 45.0

def test_get_missing_raises():
    pl = PositionList()
    with pytest.raises(KeyError):
        pl.get("nonexistent")

def test_delete():
    pl = PositionList()
    pl.add("a", 0, 0)
    pl.delete("a")
    assert pl.names() == []

def test_clear():
    pl = PositionList()
    pl.add("a", 0, 0)
    pl.add("b", 1, 1)
    pl.clear()
    assert pl.all() == []
```

### 14.4 Unit tests: new tool functions (`tests/test_tools.py` additions)

```python
# snap_and_analyze
def test_snap_and_analyze_returns_multimodal(mock_ctrl, unconstrained_guard, monkeypatch):
    import numpy as np
    fake_image = np.zeros((64, 64), dtype=np.uint16)
    monkeypatch.setattr("microclaw.tools.snap_to_numpy", lambda ctrl: fake_image)
    mock_ctrl.core.get_position.return_value = 50.0
    result = snap_and_analyze(mock_ctrl, unconstrained_guard)
    assert isinstance(result, list)
    assert result[0]["type"] == "text"
    assert result[1]["type"] == "image"
    assert result[1]["source"]["media_type"] == "image/png"


# run_autofocus - safety
def test_autofocus_range_blocked_by_guard(mock_ctrl):
    from microclaw.safety import SafetyConstraints, SafetyGuard, AutofocusConstraints
    tight_guard = SafetyGuard(SafetyConstraints(
        autofocus=AutofocusConstraints(max_z_range_um=10.0)
    ))
    mock_ctrl.core.get_position.return_value = 50.0
    with pytest.raises(SafetyViolation, match="Autofocus range"):
        run_autofocus(mock_ctrl, tight_guard, z_range_um=50.0, z_step_um=1.0)


# mark_position
def test_mark_position_uses_current_stage(mock_ctrl, unconstrained_guard):
    from microclaw.controller import MicroscopeController
    from microclaw.positions import PositionList
    mock_ctrl.positions = PositionList()
    mock_ctrl.core.get_x_position.return_value = 100.0
    mock_ctrl.core.get_y_position.return_value = -50.0
    mock_ctrl.core.get_position.return_value = 45.0
    result = mark_position(mock_ctrl, unconstrained_guard, name="my_cell")
    assert result["x_um"] == 100.0
    assert mock_ctrl.positions.get("my_cell").z_um == 45.0


# go_to_position - safety
def test_go_to_position_blocked_by_xy_limit(mock_ctrl):
    from microclaw.positions import PositionList
    from microclaw.safety import SafetyConstraints, SafetyGuard, StageConstraints
    mock_ctrl.positions = PositionList()
    mock_ctrl.positions.add("far", x_um=9999.0, y_um=0.0)
    tight_guard = SafetyGuard(SafetyConstraints(
        stage=StageConstraints(x_max=5000.0)
    ))
    with pytest.raises(SafetyViolation):
        go_to_position(mock_ctrl, tight_guard, name="far")
```

### 14.5 Safety boundary tests (`tests/test_safety.py` additions)

```python
def test_autofocus_range_constraint():
    from microclaw.safety import SafetyConstraints, SafetyGuard, AutofocusConstraints
    guard = SafetyGuard(SafetyConstraints(autofocus=AutofocusConstraints(max_z_range_um=50.0)))
    guard.check_autofocus_range(50.0)   # at limit — should not raise
    with pytest.raises(SafetyViolation):
        guard.check_autofocus_range(50.001)

def test_autofocus_range_unconstrained():
    from microclaw.safety import SafetyConstraints, SafetyGuard
    guard = SafetyGuard(SafetyConstraints())
    guard.check_autofocus_range(10_000.0)  # no constraint, no exception
```

### 14.6 Agent-level tests: prompt → tool sequence (`tests/test_agent.py` additions)

```python
class TestAutofocusPrompt:
    def test_autofocus_called(self, ctrl, guard):
        scripted = [
            tool_use_response("get_system_state", {}, "c1"),
            tool_use_response("run_autofocus", {"z_range_um": 20, "z_step_um": 0.5}, "c2"),
            text_response("I found focus at Z=48.2 µm."),
        ]
        with patch("microclaw.agent.client", make_mock_client(scripted)):
            reply, _ = run_agent("Please autofocus on my sample", ctrl, guard)
        assert "focus" in reply.lower() or "z" in reply.lower()


class TestMultipositionWithAutofocusPrompt:
    def test_tool_called_with_positions(self, ctrl, guard):
        scripted = [
            tool_use_response("get_position_list", {}, "c1"),
            tool_use_response(
                "run_multiposition_with_autofocus",
                {
                    "position_names": ["cell_1", "cell_2"],
                    "z_range_um": 20,
                    "z_step_um": 0.5,
                    "protocol": "snap",
                    "save_dir": "/tmp",
                },
                "c2",
            ),
            text_response("Imaged 2 positions with autofocus."),
        ]
        with patch("microclaw.agent.client", make_mock_client(scripted)):
            reply, _ = run_agent(
                "Image all positions in my list with autofocus and snap at each", ctrl, guard
            )
        assert "position" in reply.lower() or "imaged" in reply.lower()
```

### 14.7 Integration tests against Demo config (`tests/test_integration.py` additions)

```python
@pytest.mark.integration
def test_autofocus_demo(headless_mm, unconstrained_guard):
    from microclaw.tools import run_autofocus
    result_or_list = run_autofocus(headless_mm, unconstrained_guard, z_range_um=10, z_step_um=1)
    # Result may be a list (multimodal) or dict
    if isinstance(result_or_list, list):
        import json
        payload = json.loads(result_or_list[0]["text"])
    else:
        payload = result_or_list
    assert "best_z_um" in payload
    assert isinstance(payload["metric_curve"], list)


@pytest.mark.integration
def test_snap_and_analyze_demo(headless_mm, unconstrained_guard):
    from microclaw.tools import snap_and_analyze
    result = snap_and_analyze(headless_mm, unconstrained_guard)
    assert isinstance(result, list)
    import json, base64
    payload = json.loads(result[0]["text"])
    assert "focus_metric" in payload
    assert result[1]["type"] == "image"
    # Verify the base64 is valid PNG
    raw = base64.standard_b64decode(result[1]["source"]["data"])
    assert raw[:4] == b'\x89PNG'


@pytest.mark.integration
def test_multiposition_with_autofocus_demo(headless_mm, unconstrained_guard, tmp_path):
    from microclaw.tools import mark_position, run_multiposition_with_autofocus
    from microclaw.positions import PositionList
    headless_mm.positions = PositionList()
    mark_position(headless_mm, unconstrained_guard, name="pos_a")
    mark_position(headless_mm, unconstrained_guard, name="pos_b")
    result = run_multiposition_with_autofocus(
        headless_mm, unconstrained_guard,
        position_names=["pos_a", "pos_b"],
        z_range_um=5, z_step_um=1,
        protocol="snap",
        save_dir=str(tmp_path),
    )
    assert "2/2" in result["status"]
    assert all("error" not in r for r in result["results"])
```

---

## 15. New Dependencies

```toml
# pyproject.toml additions
dependencies = [
    ...existing...,
    "scipy>=1.12",
    "Pillow>=10.0",
]
```

---

## 16. Implementation Roadmap

| Phase | Deliverable | Files changed | Prerequisite |
|-------|-------------|---------------|--------------|
| **1** | `image_analysis.py` + unit tests | `image_analysis.py`, `tests/test_image_analysis.py` | None |
| **2** | `snap_and_analyze` tool (multimodal return), modified `execute_tool` | `tools.py`, `tools_schema.py`, `agent.py` (verify) | Phase 1 |
| **3** | `autofocus.py` + `run_autofocus` tool + safety extension + unit tests | `autofocus.py`, `safety.py`, `tools.py`, `tools_schema.py`, `safety_config.yaml`, tests | Phase 1 |
| **4** | `positions.py` + position management tools + unit tests | `positions.py`, `controller.py`, `tools.py`, `tools_schema.py`, tests | None |
| **5** | `run_multiposition_acquisition` + `run_multiposition_with_autofocus` + unit tests | `tools.py`, `tools_schema.py`, tests | Phases 3, 4 |
| **6** | System prompt update | `agent.py` | Phase 2 |
| **7** | Integration tests against Demo config | `tests/test_integration.py` | Phases 1–6 + MM installed |
| **8** | `hooks.py` + `run_adaptive_acquisition` tool | `hooks.py`, `tools.py`, `tools_schema.py`, tests | Phase 5 |

Phases 1–7 deliver the core adaptive capability. Phase 8 (hooks) is separable and
can be deferred without affecting the primary workflows.

---

## 17. Known Limitations and Mitigations

| Limitation | Mitigation |
|---|---|
| `snap_to_numpy` uses `get_tagged_image()` which may behave differently across camera adapters and pycro-manager versions | Test against Demo config first; wrap with `try/except` and fall back to `core.get_image()` if tagged image fails |
| Autofocus sweep blocks the agent loop (no progress updates) | Acceptable for v2; a v3 improvement could run the sweep in a thread and stream intermediate metrics |
| Focus metric (Laplacian variance) is sensitive to noise in low-SNR fluorescence images | Apply Gaussian pre-smoothing (σ = 0.5–1.0 px) before computing metric; expose as a parameter |
| Position list is in-memory only — lost if agent restarts | v3: serialize to JSON or MM `.pos` format on disk; for now, instruct user to rebuild the list after restart |
| `run_multiposition_with_autofocus` does not support multi-channel acquisitions per position directly | Workaround: pass protocol='zstack' with channel params; full multi-channel multiposition is a v3 feature |
| Hook strategies (section 8) use internal pycro-manager `image_process_fn` API that may change | Pin pycro-manager version; add a compatibility test that verifies hook signature |
| Vision analysis via Claude introduces per-call API latency (~1–3 s) | Acceptable for interactive workflows; not suitable for real-time feedback; document this clearly |
| Large position lists (>50 positions) will produce long multiposition result payloads | Truncate per-position details in the tool result; always return aggregate counts |
