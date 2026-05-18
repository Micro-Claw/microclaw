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

1. **Image analysis via established tools** — image analysis tasks (cell detection,
   focus measurement, intensity quantification) are handled by purpose-built
   Python libraries running in pycro-manager hooks. Claude synthesizes their
   numerical outputs to decide what to do next. Claude's own vision capability
   is reserved for tasks no algorithm handles well (e.g. "is this tissue section
   oriented correctly?") and for interactive confirmation by the biologist.

2. **Software autofocus** — a Python sweep algorithm computes focus metrics across
   a Z range and moves to the sharpest plane. Available in two forms: (a) a
   standalone `run_autofocus` tool for interactive use, and (b) a
   `post_hardware_hook_fn` strategy that runs a focus sweep at each position
   inside a pycro-manager `Acquisition`, ensuring each stored image is already
   focused. The comparison between these two forms is detailed in section 5.

3. **Position list management** — the agent reads from and writes to Micro-Manager's
   native position list via the pycro-manager Studio API, so positions are
   immediately visible in the MM GUI's XY Stage Control window.

4. **Adaptive multiposition acquisition** — visit each position in the MM position
   list, run a configurable per-position protocol (snap, Z-stack, timelapse), and
   optionally autofocus at each stop before acquiring.

5. **Hook-based adaptive acquisition** — two tiers of hooks:
   - **Pre-coded hooks** shipped with microclaw (focus feedback, intensity
     adaptation, position filtering). Claude selects and configures these by
     name. The hook architecture is designed so additional analysis plugins
     (e.g. cell segmentation models) can be added as new hook classes without
     changing any other code.
   - **Claude-generated hooks** — when no pre-coded hook covers a biologist's
     request, Claude writes a new pycro-manager hook script, presents it to the
     user for review, saves it to disk, and thereafter calls it by name. Claude
     never executes generated code without user confirmation.

---

## 2. Approach and Key Design Decisions

### 2.1 Revised tiering model: hooks do the work, Claude synthesizes results

The original tier table treated "Claude reasoning" as the primary analysis tier.
This is revised: Claude's role is to *decide* and *explain*, not to *measure*.
Established tools run as hooks at acquisition speed; Claude reads their output
after the fact.

| Tier | Speed requirement | Who runs | What Claude does |
|------|-------------------|----------|-----------------|
| **Hook tier** | 10 ms–5 s per image | Pre-coded or generated Python hook | Selects hook + params at setup; reads summary after acquisition |
| **Metric tier** | 10–500 ms | NumPy/SciPy in hook or tool | Sees numerical result, interprets, suggests next step |
| **Vision tier** | 1–3 s (API call) | Claude, from a thumbnail in the tool result | Used only when no algorithm gives a suitable answer, or for interactive confirmation |

This means even Tier-3-style real-time operations (focus feedback, cell
detection) are handled by hooks running at acquisition speed. After the
acquisition, hooks write a summary to a log file; Claude reads the log and
reports findings to the biologist.

### 2.2 Vision: Claude as fallback synthesizer

The Anthropic Messages API allows tool result content to be a list of blocks.
An image block passes a base64-encoded PNG thumbnail directly into Claude's
context:

```python
[
    {"type": "text",  "text": '{"cell_count": 12, "mean_area_px2": 340, "z_best_um": 48.2}'},
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}
]
```

Claude first reads the quantitative fields (cell count, focus metric, intensity)
from the text block. The image is included so Claude can do a visual sanity
check and describe what it sees to the biologist — but the *decisions* are driven
by the numbers, not by Claude staring at pixels.

For tasks that are genuinely semantic and cannot be reduced to numbers (e.g.
"is this the right region of the slide?", "do these cells look healthy?"),
Claude's visual assessment is appropriate and no hook is needed.

**Image preprocessing** for thumbnails: raw 16-bit images are percentile-
normalised to 8-bit and resized to ≤512 px before encoding. The full-resolution
image is always saved to disk.

### 2.3 Autofocus: two forms, different use cases

#### Form A — standalone Python tool (`run_autofocus`)

Calls `sweep_autofocus()` / `coarse_then_fine_autofocus()` directly, outside any
`Acquisition` context. Claude calls this when the user says "please focus on my
sample" interactively.

*Pros:* simple, no `Acquisition` overhead, works any time.  
*Cons:* not integrated with pycro-manager's acquisition engine; if used inside a
multiposition loop, images at intermediate positions are not guaranteed to land in
the acquisition dataset at the focused Z.

#### Form B — autofocus as a pycro-manager hook (`AutofocusHook`)

pycro-manager's `post_hardware_hook_fn` fires *after* hardware moves to the
event's target position, *before* the camera captures the image. An autofocus
hook placed here can:
1. Receive the current event (position, channel, Z).
2. Run a mini focus sweep centred on the event Z.
3. Update the event's Z coordinate to the best found.
4. Return, allowing the acquisition engine to capture the image at the focused Z.

The image stored in the dataset is then at the correct focus plane without any
extra Python loop outside the acquisition.

*Pros:* focus is embedded in the acquisition; all images in the NDTiff dataset
are at the correct Z; works naturally with pycro-manager's multiposition engine.  
*Cons:* more complex setup (hook must be created before `Acquisition` starts);
hook parameters are fixed at setup time; pycro-manager must be ≥1.0.

**Recommendation:** Form A for interactive autofocus. Form B (via
`run_adaptive_acquisition` with `hook_strategy="autofocus_per_position"`) for
automated multiposition surveys. Both are implemented; Claude chooses based on
context.

### 2.4 Multiposition: use MM's native position list

Rather than maintaining a separate Python data structure, v2 reads from and
writes to MM's native position list via the pycro-manager Studio API:

```python
pos_list_mgr = ctrl.studio.positions()       # PositionListManager
pos_list     = pos_list_mgr.get_position_list()  # PositionList (Java object)
```

Changes made through this API appear immediately in MM's XY Stage Control window.
The biologist can also manually edit the position list in the MM GUI, and the
agent will see the changes on the next `get_position_list` call.

For loading/saving position lists across sessions, MM's native `.pos` XML format
is used (`core.get_position_list_data()` / `core.set_position_list_data()`).

### 2.5 Hooks: pre-coded strategies and Claude-generated strategies

Two tiers of hooks, both implemented:

**Pre-coded hooks** (shipped with microclaw, in `hooks.py`):

| Hook class | What it does |
|------------|-------------|
| `AutofocusHook` | Runs a Z sweep at each position before image capture |
| `FocusFeedbackHook` | Corrects Z drift per frame during timelapse |
| `IntensityAdaptiveHook` | Adjusts exposure if mean intensity drifts |
| `PositionFilterHook` | Skips positions below a minimum mean intensity |

These hooks are always available, require no code generation, and are the
recommended starting point.

**Claude-generated hooks** (new in v2, optional):

When a biologist requests an adaptive behavior not covered by a pre-coded hook,
Claude can generate a new hook script. The workflow is:

1. Claude writes a hook module following a standard template.
2. The generated code is shown to the user in full — Claude does not save or
   execute it until the user explicitly confirms.
3. On confirmation, the script is saved to `~/.microclaw/hooks/<name>.py` with
   metadata in `~/.microclaw/hooks/manifest.json`.
4. Claude (and future sessions) can call this hook by name via
   `run_adaptive_acquisition(hook_strategy="<name>", ...)`.

A static-analysis safety pass (AST scan) runs before presenting the code to
the user, blocking obvious dangerous patterns (subprocess, os.system, exec,
eval, unrestricted file writes). This reduces but does not eliminate risk —
user review is mandatory.

The pre-coded hooks are never replaced or compromised by this mechanism.

---

## 3. New Module Architecture

```
microclaw/
├── agent.py              # Modified: execute_tool handles multimodal returns
├── controller.py         # Unchanged
├── tools.py              # Extended: new tools added
├── tools_schema.py       # Extended: schemas for new tools
├── safety.py             # Minor extension: no new constraints (section 11)
├── config.py             # Unchanged
├── errors.py             # Unchanged
├── image_analysis.py     # NEW: focus metrics, thumbnail generation
├── autofocus.py          # NEW: sweep autofocus algorithm
├── hooks.py              # NEW: pre-coded pycro-manager hook strategies
└── hook_manager.py       # NEW: save/load/validate Claude-generated hooks
```

Position management no longer requires a separate `positions.py` module — it
goes through MM's native API via `controller.py`.

---

## 4. `image_analysis.py` — Focus Metrics and Thumbnail Generation

This module contains all image analysis that runs in Python (no API calls).
It is used by both hooks (for acquisition-time analysis) and tools (for
interactive analysis).

```python
# microclaw/image_analysis.py
from __future__ import annotations
import base64
import io
from typing import NamedTuple

import numpy as np
from PIL import Image  # pillow


class ImageStats(NamedTuple):
    focus_metric: float       # Laplacian variance (higher = sharper)
    mean_intensity: float
    max_intensity: float
    min_intensity: float
    saturated_fraction: float # fraction of pixels at bit-depth max


def laplacian_variance(image: np.ndarray) -> float:
    """Laplacian variance focus metric. Higher = sharper."""
    from scipy.ndimage import laplace
    return float(np.var(laplace(image.astype(np.float64))))


def compute_stats(image: np.ndarray) -> ImageStats:
    bit_max = float(np.iinfo(image.dtype).max) if np.issubdtype(image.dtype, np.integer) else 1.0
    return ImageStats(
        focus_metric=laplacian_variance(image),
        mean_intensity=float(np.mean(image)),
        max_intensity=float(np.max(image)),
        min_intensity=float(np.min(image)),
        saturated_fraction=float(np.sum(image >= bit_max) / image.size),
    )


def make_thumbnail(
    image: np.ndarray,
    max_size: int = 512,
    percentile_low: float = 2.0,
    percentile_high: float = 99.8,
) -> str:
    """Percentile-normalised, resized PNG thumbnail, returned as base64."""
    img = image.astype(np.float32)
    p_lo, p_hi = np.percentile(img, percentile_low), np.percentile(img, percentile_high)
    if p_hi > p_lo:
        img = (img - p_lo) / (p_hi - p_lo)
    img_8bit = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    pil_img = Image.fromarray(img_8bit, mode='L')
    pil_img.thumbnail((max_size, max_size), Image.LANCZOS)
    buf = io.BytesIO()
    pil_img.save(buf, format='PNG')
    return base64.standard_b64encode(buf.getvalue()).decode('ascii')


def snap_to_numpy(ctrl) -> np.ndarray:
    """Snap and return a NumPy array via pycro-manager's tagged image API."""
    ctrl.core.snap_image()
    ctrl.core.wait_for_image_synced()
    tagged = ctrl.core.get_tagged_image()
    w, h = tagged.tags['Width'], tagged.tags['Height']
    return np.frombuffer(tagged.pix, dtype=np.uint16).reshape(h, w)
```

**Dependencies added:** `scipy`, `Pillow`.

---

## 5. `autofocus.py` — Sweep Autofocus (Form A)

This module implements the standalone focus sweep used by the `run_autofocus`
tool. It is also imported by `AutofocusHook` (Form B) to share the algorithm.

```python
# microclaw/autofocus.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
import time

import numpy as np

from microclaw.controller import MicroscopeController
from microclaw.image_analysis import snap_to_numpy, laplacian_variance


@dataclass
class AutofocusResult:
    best_z_um: float
    metric_values: list[float]
    z_positions: list[float]
    settled: bool   # True if peak is not at the boundary of the sweep


def sweep_autofocus(
    ctrl: MicroscopeController,
    z_start_um: float,
    z_end_um: float,
    z_step_um: float,
    settle_ms: int = 50,
    metric_fn: Callable[[np.ndarray], float] = laplacian_variance,
) -> AutofocusResult:
    """Sweep Z, measure focus metric at each step, move to best Z."""
    focus_device = ctrl.core.get_focus_device()
    z_positions = list(np.arange(z_start_um, z_end_um + z_step_um / 2, z_step_um))
    metric_values = []

    for z in z_positions:
        ctrl.core.set_position(z)
        ctrl.core.wait_for_device(focus_device)
        if settle_ms > 0:
            time.sleep(settle_ms / 1000.0)
        metric_values.append(metric_fn(snap_to_numpy(ctrl)))

    best_idx = int(np.argmax(metric_values))
    best_z = z_positions[best_idx]
    ctrl.core.set_position(best_z)
    ctrl.core.wait_for_device(focus_device)

    return AutofocusResult(
        best_z_um=best_z,
        metric_values=metric_values,
        z_positions=z_positions,
        settled=0 < best_idx < len(z_positions) - 1,
    )


def coarse_then_fine_autofocus(
    ctrl: MicroscopeController,
    z_range_um: float,
    coarse_step_um: float,
    fine_step_um: float,
    settle_ms: int = 50,
) -> AutofocusResult:
    """Two-pass autofocus: coarse sweep then fine sweep around the coarse peak."""
    current_z = ctrl.core.get_position()
    coarse = sweep_autofocus(
        ctrl,
        current_z - z_range_um / 2,
        current_z + z_range_um / 2,
        coarse_step_um,
        settle_ms,
    )
    return sweep_autofocus(
        ctrl,
        coarse.best_z_um - coarse_step_um,
        coarse.best_z_um + coarse_step_um,
        fine_step_um,
        settle_ms,
    )
```

---

## 6. Position Management via MM's Native API

Positions are stored in MM's native position list and are immediately visible in
the XY Stage Control window of the MM GUI. The agent interacts with them through
pycro-manager's Studio bridge.

```python
# controller.py — new helpers (no separate positions.py needed)

class MicroscopeController:
    # ... existing __init__ ...

    def _pos_list(self):
        """Return MM's live PositionList Java object via pycro-manager."""
        return self._studio.positions().get_position_list()

    def add_position(self, label: str, x: float, y: float, z: float | None = None) -> None:
        """Add a position to MM's native position list."""
        from pycromanager import MultiStagePosition, StagePosition
        msp = MultiStagePosition()
        msp.set_label(label)
        xy_stage = self._core.get_xy_stage_device()
        sp_xy = StagePosition()
        sp_xy.stage_device_label = xy_stage
        sp_xy.num_axes = 2
        sp_xy.x = x
        sp_xy.y = y
        msp.add(sp_xy)
        if z is not None:
            z_stage = self._core.get_focus_device()
            sp_z = StagePosition()
            sp_z.stage_device_label = z_stage
            sp_z.num_axes = 1
            sp_z.x = z
            msp.add(sp_z)
        self._pos_list().add_position(msp)

    def get_positions(self) -> list[dict]:
        """Return all positions from MM's native list."""
        pl = self._pos_list()
        out = []
        for i in range(pl.get_number_of_positions()):
            msp = pl.get_position(i)
            entry = {"name": str(msp.get_label())}
            for j in range(msp.size()):
                sp = msp.get(j)
                if sp.num_axes == 2:
                    entry["x_um"] = round(sp.x, 3)
                    entry["y_um"] = round(sp.y, 3)
                elif sp.num_axes == 1:
                    entry["z_um"] = round(sp.x, 3)
            out.append(entry)
        return out

    def go_to_position(self, label: str) -> None:
        """Move stage to a named position from MM's native list."""
        pl = self._pos_list()
        for i in range(pl.get_number_of_positions()):
            msp = pl.get_position(i)
            if str(msp.get_label()) == label:
                pl.go_to_position(i, self._core)
                return
        raise KeyError(f"Position '{label}' not found in MM position list.")

    def remove_position(self, label: str) -> None:
        pl = self._pos_list()
        for i in range(pl.get_number_of_positions()):
            if str(pl.get_position(i).get_label()) == label:
                pl.remove_position(i)
                return
        raise KeyError(f"Position '{label}' not found.")

    def clear_positions(self) -> None:
        self._pos_list().clear_all_positions()

    def save_position_list(self, path: str) -> None:
        """Save MM position list to a .pos file."""
        self._pos_list().save(path)

    def load_position_list(self, path: str) -> None:
        """Load a .pos file into MM's native position list."""
        self._pos_list().load(path)
        self._studio.positions().set_position_list(self._pos_list())
```

> **Note on API verification:** The exact Java method names exposed by pycro-manager
> for `PositionList` and `MultiStagePosition` must be confirmed against the installed
> pycro-manager version. `studio.positions()` returns a `PositionListManager`; the
> methods `get_position_list()`, `go_to_position()`, `get_number_of_positions()`,
> etc., mirror the MM 2.0 Java API. A small integration test against the Demo config
> (Phase 4) validates this before it is relied upon.

---

## 7. `hooks.py` — Pre-coded Adaptive Acquisition Strategies

These hooks implement the analysis strategies that run at acquisition speed.
Claude selects and configures them at setup time; they never call the Claude API.
After the acquisition, hooks write a summary to a JSON log file that Claude reads.

```python
# microclaw/hooks.py
from __future__ import annotations
import json
import time
from pathlib import Path
import numpy as np
from microclaw.image_analysis import laplacian_variance, snap_to_numpy


class HookBase:
    """All hooks write a summary log so Claude can read results afterward."""

    def __init__(self, log_path: str | None = None):
        self.log_path = log_path
        self._log: list[dict] = []

    def _write_log(self) -> None:
        if self.log_path:
            Path(self.log_path).write_text(json.dumps(self._log, indent=2))

    def get_summary(self) -> list[dict]:
        return self._log


class AutofocusHook(HookBase):
    """Form B autofocus: runs a Z sweep before each image in the acquisition.

    Used as a post_hardware_hook_fn inside an Acquisition context.
    After hardware moves to the event's XY (and nominal Z), this hook
    sweeps Z and updates the focus device to the sharpest plane before
    the camera fires.
    """

    def __init__(self, ctrl, guard, z_range_um: float, z_step_um: float,
                 settle_ms: int = 50, log_path: str | None = None):
        super().__init__(log_path)
        from microclaw.autofocus import coarse_then_fine_autofocus
        self.ctrl = ctrl
        self.guard = guard
        self.z_range_um = z_range_um
        self.z_step_um = z_step_um
        self.settle_ms = settle_ms
        self._autofocus_fn = coarse_then_fine_autofocus

    def post_hardware_hook_fn(self, event: dict) -> dict:
        """Called after hardware moves to event position, before image capture."""
        current_z = self.ctrl.core.get_position()
        z_start = current_z - self.z_range_um / 2
        z_end = current_z + self.z_range_um / 2
        try:
            self.guard.check_z(z_start)
            self.guard.check_z(z_end)
        except Exception as e:
            self._log.append({"event": event, "autofocus": "skipped", "reason": str(e)})
            self._write_log()
            return event

        coarse_step = max(self.z_step_um * 5, 1.0)
        result = self._autofocus_fn(
            self.ctrl, self.z_range_um, coarse_step, self.z_step_um, self.settle_ms
        )
        self._log.append({
            "position": event.get("axes", {}),
            "best_z_um": round(result.best_z_um, 3),
            "settled": result.settled,
        })
        self._write_log()
        return event


class FocusFeedbackHook(HookBase):
    """Corrects Z drift per frame during timelapse using Laplacian variance."""

    def __init__(self, ctrl, guard, threshold_fraction: float = 0.85,
                 z_step_um: float = 0.5, max_jogs: int = 5,
                 log_path: str | None = None):
        super().__init__(log_path)
        self.ctrl = ctrl
        self.guard = guard
        self.threshold = threshold_fraction
        self.z_step = z_step_um
        self.max_jogs = max_jogs
        self.reference_metric: float | None = None

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue):
        metric = laplacian_variance(image)
        if self.reference_metric is None:
            self.reference_metric = metric
            return image, metadata

        if metric < self.reference_metric * self.threshold:
            focus_device = self.ctrl.core.get_focus_device()
            for _ in range(self.max_jogs):
                current_z = self.ctrl.core.get_position()
                try:
                    self.guard.check_z(current_z + self.z_step)
                    self.ctrl.core.set_position(current_z + self.z_step)
                    self.ctrl.core.wait_for_device(focus_device)
                    new_image = snap_to_numpy(self.ctrl)
                    new_metric = laplacian_variance(new_image)
                    if new_metric >= self.reference_metric * self.threshold:
                        self.reference_metric = new_metric
                        break
                except Exception:
                    break
            self._log.append({"frame": metadata.get("time"), "focus_correction": True})
            self._write_log()
        return image, metadata


class IntensityAdaptiveHook(HookBase):
    """Adjusts exposure per frame to keep mean intensity near a target."""

    def __init__(self, ctrl, guard, target_mean: float, tolerance: float = 0.1,
                 min_exposure_ms: float = 1.0, max_exposure_ms: float = 1000.0,
                 log_path: str | None = None):
        super().__init__(log_path)
        self.ctrl = ctrl
        self.guard = guard
        self.target_mean = target_mean
        self.tolerance = tolerance
        self.min_exp = min_exposure_ms
        self.max_exp = max_exposure_ms

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue):
        mean = float(np.mean(image))
        if abs(mean - self.target_mean) / self.target_mean > self.tolerance:
            ratio = self.target_mean / max(mean, 1.0)
            new_exp = float(np.clip(self.ctrl.core.get_exposure() * ratio,
                                    self.min_exp, self.max_exp))
            try:
                self.guard.check_exposure(new_exp)
                self.ctrl.core.set_exposure(new_exp)
                self._log.append({"frame": metadata.get("time"),
                                  "new_exposure_ms": round(new_exp, 1)})
                self._write_log()
            except Exception:
                pass
        return image, metadata


class PositionFilterHook(HookBase):
    """Rejects positions where mean intensity is below a threshold.

    Returns None to discard the image; pycro-manager drops the remaining
    events for that position.
    """

    def __init__(self, min_mean_intensity: float = 100.0, log_path: str | None = None):
        super().__init__(log_path)
        self.min_mean = min_mean_intensity
        self.rejected: list = []

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue):
        mean = float(np.mean(image))
        if mean < self.min_mean:
            pos = metadata.get("position_index", -1)
            if pos not in self.rejected:
                self.rejected.append(pos)
                self._log.append({"position_index": pos, "mean_intensity": round(mean, 1),
                                  "action": "rejected"})
                self._write_log()
            return None
        return image, metadata


PRECODED_HOOK_REGISTRY: dict[str, type] = {
    "autofocus_per_position": AutofocusHook,
    "focus_feedback":         FocusFeedbackHook,
    "intensity_adaptive":     IntensityAdaptiveHook,
    "position_filter":        PositionFilterHook,
}

# To add a new analysis plugin (e.g. a cell segmentation hook), define a class
# that inherits from HookBase and implements image_process_fn, then register it
# here. No other code needs to change.
```

---

## 8. `hook_manager.py` — Claude-Generated Hooks

This module implements the save/load/validate pipeline for hooks written by
Claude. The pre-coded hooks in `hooks.py` are completely separate and unaffected.

### 8.1 Hook template

Every Claude-generated hook must follow this structure:

```python
# MICROCLAW_HOOK
# name: <hook_name>
# description: <one sentence>
# params:
#   <param>: <type> — <description>

from __future__ import annotations
import numpy as np

class <HookClassName>:
    def __init__(self, **params):
        ...

    def image_process_fn(
        self, image: np.ndarray, metadata: dict, event_queue
    ) -> tuple[np.ndarray, dict] | None:
        ...
        # Return (image, metadata) to keep, None to discard
```

The template header comment is machine-readable and used by `hook_manager.py`
to extract metadata for the manifest.

### 8.2 Safety validation

Before saving, the code goes through `validate_hook_code()`:

```python
# microclaw/hook_manager.py
import ast
import json
import importlib.util
from pathlib import Path

HOOKS_DIR = Path.home() / ".microclaw" / "hooks"
MANIFEST = HOOKS_DIR / "manifest.json"

BANNED_NAMES = {"subprocess", "os.system", "eval", "exec", "__import__"}
BANNED_OPENS = {"w", "a", "wb", "ab"}  # write-mode file opens outside save_dir


def validate_hook_code(code: str) -> list[str]:
    """Return a list of safety warnings. Empty list means no issues found."""
    warnings = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"Syntax error: {e}"]

    for node in ast.walk(tree):
        # Block dangerous builtins
        if isinstance(node, ast.Name) and node.id in {"eval", "exec"}:
            warnings.append(f"Dangerous call: {node.id}()")
        # Block subprocess / os.system via attribute access
        if isinstance(node, ast.Attribute):
            chain = f"{getattr(node.value, 'id', '?')}.{node.attr}"
            if chain in BANNED_NAMES:
                warnings.append(f"Dangerous call: {chain}")
        # Block __import__
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                warnings.append("Dangerous call: __import__()")

    return warnings


def save_hook(name: str, code: str, description: str) -> None:
    """Save validated hook code to disk and update manifest."""
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    hook_path = HOOKS_DIR / f"{name}.py"
    hook_path.write_text(code)

    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    manifest[name] = {"description": description, "path": str(hook_path)}
    MANIFEST.write_text(json.dumps(manifest, indent=2))


def load_hook_class(name: str):
    """Dynamically import a saved hook and return its class."""
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    if name not in manifest:
        raise KeyError(f"No generated hook named '{name}'.")
    spec = importlib.util.spec_from_file_location(name, manifest[name]["path"])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Find the class that has image_process_fn
    for attr in dir(mod):
        cls = getattr(mod, attr)
        if isinstance(cls, type) and hasattr(cls, "image_process_fn"):
            return cls
    raise AttributeError(f"No class with image_process_fn found in hook '{name}'.")


def list_generated_hooks() -> dict[str, str]:
    """Return {name: description} for all saved generated hooks."""
    if not MANIFEST.exists():
        return {}
    return {k: v["description"] for k, v in json.loads(MANIFEST.read_text()).items()}
```

### 8.3 New tools for hook management

```python
# tools.py (new)

def generate_and_save_hook(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    name: str,
    code: str,
    description: str,
) -> dict:
    """Validate and save a Claude-generated hook script.

    This tool is called ONLY after the user has seen and approved the code.
    The system prompt instructs Claude to show the code to the user and wait
    for explicit confirmation before calling this tool.
    """
    from microclaw.hook_manager import validate_hook_code, save_hook
    warnings = validate_hook_code(code)
    if warnings:
        return {
            "error": "Safety validation found issues — hook not saved.",
            "warnings": warnings,
        }
    save_hook(name, code, description)
    return {"status": f"Hook '{name}' saved successfully.", "path": str(
        Path.home() / ".microclaw" / "hooks" / f"{name}.py"
    )}


def list_hooks(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """List all available hook strategies (pre-coded and generated)."""
    from microclaw.hooks import PRECODED_HOOK_REGISTRY
    from microclaw.hook_manager import list_generated_hooks
    return {
        "precoded": list(PRECODED_HOOK_REGISTRY.keys()),
        "generated": list_generated_hooks(),
    }


def read_hook_log(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    log_path: str,
) -> dict:
    """Read a hook's output log file after an acquisition completes.

    Claude calls this to retrieve per-position cell counts, autofocus results,
    focus corrections, etc., and synthesize them for the user.
    """
    import json
    path = Path(log_path)
    if not path.exists():
        return {"error": f"Log file not found: {log_path}"}
    entries = json.loads(path.read_text())
    return {"log_path": log_path, "entry_count": len(entries), "entries": entries}
```

---

## 9. New Tool Implementations (`tools.py` extensions)

### 9.1 Image capture with visual return (`snap_and_analyze`)

Returns a multimodal content list so Claude can see the image and numerical
stats simultaneously.

```python
def snap_and_analyze(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    thumbnail_size: int = 512,
) -> list:
    image = snap_to_numpy(ctrl)
    stats = compute_stats(image)
    text_payload = {
        "z_um": round(ctrl.core.get_position(), 3),
        "focus_metric": round(stats.focus_metric, 2),
        "mean_intensity": round(stats.mean_intensity, 1),
        "max_intensity": round(stats.max_intensity, 1),
        "saturated_fraction": round(stats.saturated_fraction, 4),
    }
    return [
        {"type": "text", "text": json.dumps(text_payload)},
        {"type": "image", "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": make_thumbnail(image, max_size=thumbnail_size),
        }},
    ]
```

### 9.2 Autofocus (Form A — standalone)

```python
def run_autofocus(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    z_range_um: float,
    z_step_um: float,
    method: str = "coarse_then_fine",
    settle_ms: int = 50,
    return_thumbnail: bool = True,
) -> list | dict:
    current_z = ctrl.core.get_position()
    guard.check_z(current_z - z_range_um / 2)
    guard.check_z(current_z + z_range_um / 2)

    if method == "coarse_then_fine":
        result = coarse_then_fine_autofocus(
            ctrl, z_range_um, max(z_step_um * 5, 1.0), z_step_um, settle_ms
        )
    else:
        result = sweep_autofocus(
            ctrl, current_z - z_range_um / 2, current_z + z_range_um / 2, z_step_um, settle_ms
        )

    payload = {
        "best_z_um": round(result.best_z_um, 3),
        "settled": result.settled,
        "metric_curve": [round(v, 2) for v in result.metric_values],
        "z_positions": [round(z, 3) for z in result.z_positions],
        "warning": None if result.settled else
            "Peak focus was at the edge of the sweep range; consider widening z_range_um.",
    }

    if not return_thumbnail:
        return payload

    image = snap_to_numpy(ctrl)
    payload["focus_metric_at_best"] = round(compute_stats(image).focus_metric, 2)
    return [
        {"type": "text", "text": json.dumps(payload)},
        {"type": "image", "source": {
            "type": "base64", "media_type": "image/png",
            "data": make_thumbnail(image),
        }},
    ]
```

### 9.3 Position management

```python
def mark_position(ctrl, guard, name: str, include_z: bool = True) -> dict:
    x = round(ctrl.core.get_x_position(), 3)
    y = round(ctrl.core.get_y_position(), 3)
    z = round(ctrl.core.get_position(), 3) if include_z else None
    guard.check_xy(x, y)
    if z is not None:
        guard.check_z(z)
    ctrl.add_position(name, x, y, z)
    return {"status": f"Position '{name}' saved to MM position list.",
            "x_um": x, "y_um": y, **({"z_um": z} if z else {})}


def get_position_list(ctrl, guard) -> dict:
    positions = ctrl.get_positions()
    return {"positions": positions, "count": len(positions)}


def go_to_position(ctrl, guard, name: str) -> dict:
    positions = {p["name"]: p for p in ctrl.get_positions()}
    if name not in positions:
        return {"error": f"Position '{name}' not found."}
    pos = positions[name]
    guard.check_xy(pos["x_um"], pos["y_um"])
    if "z_um" in pos:
        guard.check_z(pos["z_um"])
    ctrl.go_to_position(name)
    return {"status": f"Moved to '{name}'.", **pos}


def delete_position(ctrl, guard, name: str) -> dict:
    ctrl.remove_position(name)
    return {"status": f"Position '{name}' deleted from MM position list."}


def clear_position_list(ctrl, guard) -> dict:
    ctrl.clear_positions()
    return {"status": "Position list cleared."}


def save_position_list(ctrl, guard, path: str) -> dict:
    ctrl.save_position_list(path)
    return {"status": f"Position list saved to {path}."}


def load_position_list(ctrl, guard, path: str) -> dict:
    ctrl.load_position_list(path)
    positions = ctrl.get_positions()
    return {"status": f"Loaded {len(positions)} positions from {path}.", "count": len(positions)}
```

### 9.4 Multiposition acquisition

```python
def run_multiposition_acquisition(
    ctrl, guard,
    position_names: list[str],
    protocol: str,
    save_dir: str,
    name: str = "multipos",
    protocol_params: dict | None = None,
) -> dict:
    params = protocol_params or {}
    all_positions = {p["name"]: p for p in ctrl.get_positions()}
    results = []

    for pos_name in position_names:
        if pos_name not in all_positions:
            results.append({"position": pos_name, "error": "Not found in position list."})
            continue
        pos = all_positions[pos_name]
        guard.check_xy(pos["x_um"], pos["y_um"])
        ctrl.go_to_position(pos_name)
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
        except Exception as e:
            results.append({"position": pos_name, "error": str(e)})

    n_ok = sum(1 for r in results if "error" not in r)
    return {"status": f"{n_ok}/{len(position_names)} positions completed.", "results": results}


def run_multiposition_with_autofocus(
    ctrl, guard,
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
    params = protocol_params or {}
    all_positions = {p["name"]: p for p in ctrl.get_positions()}
    results = []

    for pos_name in position_names:
        if pos_name not in all_positions:
            results.append({"position": pos_name, "error": "Not found in position list."})
            continue
        pos = all_positions[pos_name]
        guard.check_xy(pos["x_um"], pos["y_um"])
        ctrl.go_to_position(pos_name)

        current_z = ctrl.core.get_position()
        try:
            guard.check_z(current_z - z_range_um / 2)
            guard.check_z(current_z + z_range_um / 2)
        except Exception as e:
            results.append({"position": pos_name, "error": f"Autofocus range out of bounds: {e}"})
            continue

        coarse_step = max(z_step_um * 5, 1.0)
        af = (coarse_then_fine_autofocus(ctrl, z_range_um, coarse_step, z_step_um, settle_ms)
              if autofocus_method == "coarse_then_fine"
              else sweep_autofocus(ctrl, current_z - z_range_um / 2,
                                   current_z + z_range_um / 2, z_step_um, settle_ms))

        pos_save_dir = str(Path(save_dir) / pos_name)
        Path(pos_save_dir).mkdir(parents=True, exist_ok=True)
        try:
            if protocol == "snap":
                ctrl.studio.live().snap(True)
                results.append({"position": pos_name, "best_z_um": round(af.best_z_um, 3), "status": "snapped"})
            elif protocol == "zstack":
                r = run_zstack(ctrl, guard, save_dir=pos_save_dir, name=pos_name, **params)
                results.append({"position": pos_name, "best_z_um": round(af.best_z_um, 3), **r})
            elif protocol == "timelapse":
                r = run_timelapse(ctrl, guard, save_dir=pos_save_dir, name=pos_name, **params)
                results.append({"position": pos_name, "best_z_um": round(af.best_z_um, 3), **r})
        except Exception as e:
            results.append({"position": pos_name, "best_z_um": round(af.best_z_um, 3), "error": str(e)})

    n_ok = sum(1 for r in results if "error" not in r)
    return {"status": f"{n_ok}/{len(position_names)} positions completed with autofocus.", "results": results}
```

### 9.5 Adaptive acquisition (hook-based)

```python
def run_adaptive_acquisition(
    ctrl, guard,
    z_start_um: float,
    z_end_um: float,
    z_step_um: float,
    save_dir: str,
    hook_strategy: str,
    hook_params: dict | None = None,
    channel: str | None = None,
    name: str = "adaptive",
    log_path: str | None = None,
) -> dict:
    """Run a Z-stack/timelapse acquisition with a hook strategy.

    hook_strategy: a key from PRECODED_HOOK_REGISTRY or a generated hook name.
    log_path: where the hook writes its per-image/per-position log.
    After the acquisition, call read_hook_log(log_path) to retrieve results.
    """
    from microclaw.hooks import PRECODED_HOOK_REGISTRY
    from microclaw.hook_manager import load_hook_class, list_generated_hooks

    guard.check_z(z_start_um)
    guard.check_z(z_end_um)
    if channel:
        guard.check_channel(channel)

    params = dict(hook_params or {})
    if log_path:
        params["log_path"] = log_path

    if hook_strategy in PRECODED_HOOK_REGISTRY:
        hook_cls = PRECODED_HOOK_REGISTRY[hook_strategy]
    elif hook_strategy in list_generated_hooks():
        hook_cls = load_hook_class(hook_strategy)
    else:
        return {"error": f"Unknown hook strategy '{hook_strategy}'. "
                f"Run list_hooks() to see available strategies."}

    # Inject ctrl/guard for hooks that need hardware access
    import inspect
    sig = inspect.signature(hook_cls.__init__)
    if "ctrl" in sig.parameters:
        params.setdefault("ctrl", ctrl)
    if "guard" in sig.parameters:
        params.setdefault("guard", guard)

    hook = hook_cls(**params)

    kwargs = {"z_start": z_start_um, "z_end": z_end_um, "z_step": z_step_um}
    if channel:
        kwargs.update(channel_group="Channel", channels=[channel])
    events = multi_d_acquisition_events(**kwargs)

    hook_fn_kwargs = {}
    if hasattr(hook, "post_hardware_hook_fn"):
        hook_fn_kwargs["post_hardware_hook_fn"] = hook.post_hardware_hook_fn
    if hasattr(hook, "image_process_fn"):
        hook_fn_kwargs["image_process_fn"] = hook.image_process_fn

    with Acquisition(directory=save_dir, name=name, show_display=True,
                     **hook_fn_kwargs) as acq:
        acq.acquire(events)

    result = {
        "status": "Adaptive acquisition complete.",
        "dataset_path": str(Path(save_dir) / name),
    }
    if log_path:
        result["log_path"] = log_path
        result["hint"] = "Call read_hook_log to retrieve per-image results."
    return result
```

---

## 10. Modified `execute_tool` — Multimodal Return Handling

Image-returning tools return a `list` of content blocks; text tools return a
`dict` (serialised to JSON). The dispatcher handles both:

```python
def execute_tool(name, tool_input, ctrl, guard) -> str | list:
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool '{name}'."})
    try:
        result = fn(ctrl, guard, **tool_input)
        return result if isinstance(result, list) else json.dumps(result)
    except SafetyViolation as e:
        return json.dumps({"error": f"Safety constraint prevented this action: {e}"})
    except Exception as e:
        return json.dumps({
            "error": f"{type(e).__name__}: {e}",
            "hint": "Hardware error, connection problem, or invalid parameters.",
        })
```

No changes to `agent.py` are required — the Messages API accepts both `str` and
`list[block]` as `tool_result` content.

---

## 11. Safety

No new `SafetyConstraints` fields are needed for v2.

The existing `z_min` / `z_max` stage constraints cover autofocus safety: the
`run_autofocus` tool and `AutofocusHook` both call `guard.check_z(z_start)` and
`guard.check_z(z_end)` before the sweep begins. If the requested range would
exit `z_min`/`z_max`, the action is blocked with a clear error.

An `autofocus.max_z_range_um` constraint was considered (to prevent
excessively wide sweeps) but is not needed: a wide sweep that stays within
`z_min`/`z_max` is always valid, and the biologist can already constrain the
stage travel to any desired range via the existing stage limits.

**Hook safety** (generated hooks): the AST validation in `hook_manager.py`
(section 8.2) provides a first-pass safety check. User confirmation is always
required before a generated hook is saved or run. Generated hooks that raise
exceptions are caught by `execute_tool`'s outer `try/except` and returned as
error tool results; the acquisition is aborted gracefully rather than crashing.

---

## 12. System Prompt Updates

Add to `SYSTEM_PROMPT` in `agent.py`:

```
Image analysis:
- Use snap_and_analyze when you need to see or assess an image interactively.
  The focus_metric (Laplacian variance) and any hook-computed metrics
  are in the text block; the thumbnail is for visual context and confirmation.
- Prefer numerical metrics from hooks over your own visual assessment for
  quantitative decisions (focus quality, cell presence, intensity).
- If the image appears blurry, suggest run_autofocus to the user — do not call
  it automatically unless the user has explicitly asked you to.
- Never over-interpret a single image; recommend re-imaging or a wider survey
  if you are uncertain.

Position lists:
- Position lists are stored in MM's native format and visible in the MM GUI.
  Use mark_position after the biologist has navigated to a site of interest.
- Use save_position_list / load_position_list to persist positions across sessions.
- Use run_multiposition_with_autofocus for automated surveys — do not manually
  loop over go_to_position unless the user explicitly asks for it.

Autofocus:
- run_autofocus (standalone) is for interactive focus requests.
- run_adaptive_acquisition with hook_strategy='autofocus_per_position' is for
  automated surveys where each stored image must be in focus.
- Default parameters for a 20× objective: z_range_um=20, z_step_um=0.5.
  Widen z_range_um if the warning says the peak was at the boundary.

Hook-based adaptive acquisition:
- Pre-coded hooks: autofocus_per_position, focus_feedback, intensity_adaptive,
  position_filter.
- Generated hooks: call list_hooks() to see what has been saved previously.
- After an adaptive acquisition, call read_hook_log(log_path) to get per-position
  or per-frame results, then synthesize and report them to the user.
- To create a new hook: write the code following the template in section 8.1 of
  plan_v2.md, show it to the user in full, wait for explicit confirmation, then
  call generate_and_save_hook().
```

---

## 13. Example Workflows

### 13.1 Interactive autofocus

```
User: "Can you focus on my sample?"

Claude → get_system_state
  Result: {z_um: 45.0, ...}

Claude → run_autofocus(z_range_um=20, z_step_um=0.5)
  Result: {best_z_um: 48.2, settled: true, metric_curve: [...]} + thumbnail

Claude: "I swept Z from 35 to 55 µm and found the sharpest focus at Z = 48.2 µm
  (focus metric 4231). The sweep was settled (peak not at boundary). I can see the
  cell boundaries clearly in the thumbnail."
```

### 13.2 Building a position list and imaging with autofocus

```
User: "I've navigated to three cells. Mark each one, then image all three with
  autofocus and a DAPI z-stack."

Claude → mark_position(name="cell_1")
User moves stage.
Claude → mark_position(name="cell_2")
User moves stage.
Claude → mark_position(name="cell_3")

User: "Good. Image them now."

Claude → run_multiposition_with_autofocus(
    position_names=["cell_1", "cell_2", "cell_3"],
    z_range_um=20, z_step_um=0.5,
    protocol="zstack",
    save_dir="/data/today",
    protocol_params={"z_start_um": -5, "z_end_um": 5, "z_step_um": 1, "channel": "DAPI"}
  )
  Result: {status: "3/3 positions completed with autofocus.",
           results: [{position: "cell_1", best_z_um: 48.2, ...}, ...]}

Claude: "All three positions were imaged. Best focus was at Z=48.2 µm (cell_1),
  Z=51.7 µm (cell_2), Z=49.1 µm (cell_3). Z-stacks saved to /data/today/{cell_1,...}/."
```

### 13.3 Semantic image-guided decision (Claude vision as last resort)

```
User: "Snap an image and tell me if there's a cell in frame."

Claude → snap_and_analyze()
  Result: {focus_metric: 1850.3, mean_intensity: 85.2, ...} + thumbnail

Claude: "I can see an elongated cell in the centre-left of the frame. The nucleus
  is visible as a brighter oval region. Focus metric is 1850 — moderate sharpness.
  Would you like me to run autofocus and then acquire a z-stack?"
```

### 13.4 Drift-corrected timelapse

```
User: "Run a 50-frame timelapse every 30 seconds with Z drift correction."

Claude → run_adaptive_acquisition(
    z_start_um=45, z_end_um=46, z_step_um=1,
    save_dir="/data/timelapse",
    hook_strategy="focus_feedback",
    hook_params={"threshold_fraction": 0.85, "z_step_um": 0.5},
    log_path="/data/timelapse/focus_log.json"
  )

Claude → read_hook_log("/data/timelapse/focus_log.json")
Claude: "The timelapse completed. Focus correction was triggered at frames 12 and
  31, suggesting ~2 µm of Z drift over 50 frames."
```

### 13.5 Claude-generated hook for a custom adaptive behaviour

```
User: "I want to automatically increase exposure at each position if it's too dark,
  then capture 3 frames at the adjusted exposure."

Claude (thinks: no pre-coded hook does exactly this): "I'll write a new hook for
  this. Here is the code — please review it before I save it:

  ---
  # MICROCLAW_HOOK
  # name: dark_exposure_boost
  # description: Boost exposure if mean intensity < threshold, then capture 3 frames
  ...
  [full code shown]
  ---

  Does this look correct to you?"

User: "Yes, save it."

Claude → generate_and_save_hook(
    name="dark_exposure_boost",
    code="...",
    description="Boost exposure if mean intensity < threshold"
  )
  Result: {status: "Hook 'dark_exposure_boost' saved successfully.", path: "..."}

Claude → run_adaptive_acquisition(
    ...,
    hook_strategy="dark_exposure_boost",
    hook_params={"intensity_threshold": 500, "max_exposure_ms": 2000}
  )
```

---

## 14. Testing Plan

### 14.1 Unit tests: image analysis (`tests/test_image_analysis.py`)

```python
import numpy as np
from microclaw.image_analysis import laplacian_variance, compute_stats, make_thumbnail

def test_laplacian_variance_sharp_vs_blurry():
    from scipy.ndimage import gaussian_filter
    sharp = np.random.randint(0, 65535, (256, 256), dtype=np.uint16)
    blurry = gaussian_filter(sharp.astype(np.float32), sigma=5).astype(np.uint16)
    assert laplacian_variance(sharp) > laplacian_variance(blurry)

def test_make_thumbnail_returns_valid_png():
    img = np.random.randint(0, 65535, (512, 512), dtype=np.uint16)
    b64 = make_thumbnail(img, max_size=128)
    import base64, io
    from PIL import Image
    pil = Image.open(io.BytesIO(base64.standard_b64decode(b64)))
    assert max(pil.size) == 128

def test_thumbnail_preserves_aspect_ratio():
    img = np.zeros((1024, 2048), dtype=np.uint16)
    b64 = make_thumbnail(img, max_size=256)
    import base64, io
    from PIL import Image
    pil = Image.open(io.BytesIO(base64.standard_b64decode(b64)))
    assert max(pil.size) == 256

def test_compute_stats_saturated():
    img = np.full((64, 64), 65535, dtype=np.uint16)
    assert compute_stats(img).saturated_fraction == 1.0
```

### 14.2 Unit tests: autofocus (`tests/test_autofocus.py`)

```python
import numpy as np
from unittest.mock import MagicMock
from microclaw.autofocus import sweep_autofocus


def make_ctrl_with_focus_at(best_z: float, width: int = 64):
    core = MagicMock()
    core.get_focus_device.return_value = "DStage"
    current_z = [50.0]
    core.set_position.side_effect = lambda z: current_z.__setitem__(0, z)
    core.get_position.side_effect = lambda: current_z[0]

    def get_tagged_image():
        z = current_z[0]
        sharpness = np.exp(-((z - best_z) ** 2) / (2 * 3.0 ** 2))
        pixels = (np.random.rand(width, width) * sharpness * 65535).clip(0, 65535).astype(np.uint16)
        tagged = MagicMock()
        tagged.pix = pixels.tobytes()
        tagged.tags = {"Width": width, "Height": width}
        return tagged

    core.get_tagged_image.side_effect = get_tagged_image
    core.snap_image = MagicMock()
    core.wait_for_image_synced = MagicMock()
    core.wait_for_device = MagicMock()
    ctrl = MagicMock()
    ctrl.core = core
    return ctrl


def test_sweep_finds_correct_z():
    ctrl = make_ctrl_with_focus_at(52.0)
    result = sweep_autofocus(ctrl, 45.0, 55.0, 1.0, settle_ms=0)
    assert abs(result.best_z_um - 52.0) <= 1.0

def test_sweep_settled_when_peak_interior():
    ctrl = make_ctrl_with_focus_at(50.0)
    result = sweep_autofocus(ctrl, 45.0, 55.0, 1.0, settle_ms=0)
    assert result.settled

def test_sweep_not_settled_when_peak_at_boundary():
    ctrl = make_ctrl_with_focus_at(45.0)
    result = sweep_autofocus(ctrl, 45.0, 55.0, 1.0, settle_ms=0)
    assert not result.settled
```

### 14.3 Unit tests: hook safety validation (`tests/test_hook_manager.py`)

```python
from microclaw.hook_manager import validate_hook_code

def test_clean_code_passes():
    code = "import numpy as np\nclass MyHook:\n    def image_process_fn(self, img, meta, q):\n        return img, meta\n"
    assert validate_hook_code(code) == []

def test_eval_is_blocked():
    code = "eval('os.system(\"rm -rf /\")')"
    warnings = validate_hook_code(code)
    assert any("eval" in w for w in warnings)

def test_syntax_error_reported():
    code = "def broken(:"
    warnings = validate_hook_code(code)
    assert any("Syntax" in w for w in warnings)

def test_subprocess_blocked():
    code = "import subprocess\nsubprocess.run(['rm', '-rf', '/'])"
    warnings = validate_hook_code(code)
    assert any("subprocess" in w for w in warnings)
```

### 14.4 Unit tests: new tool functions (`tests/test_tools.py` additions)

```python
def test_snap_and_analyze_returns_multimodal(mock_ctrl, unconstrained_guard, monkeypatch):
    import numpy as np
    monkeypatch.setattr("microclaw.tools.snap_to_numpy", lambda ctrl: np.zeros((64, 64), dtype=np.uint16))
    mock_ctrl.core.get_position.return_value = 50.0
    result = snap_and_analyze(mock_ctrl, unconstrained_guard)
    assert isinstance(result, list)
    assert result[0]["type"] == "text"
    assert result[1]["type"] == "image"

def test_run_autofocus_z_boundary_check(mock_ctrl, default_guard):
    mock_ctrl.core.get_position.return_value = 5.0
    # Sweep would go to -5 µm, below z_min=0
    with pytest.raises(SafetyViolation):
        run_autofocus(mock_ctrl, default_guard, z_range_um=20.0, z_step_um=1.0)

def test_mark_position_check_xy_safety(mock_ctrl):
    from microclaw.safety import SafetyConstraints, SafetyGuard, StageConstraints
    guard = SafetyGuard(SafetyConstraints(stage=StageConstraints(x_max=100.0)))
    mock_ctrl.core.get_x_position.return_value = 200.0
    mock_ctrl.core.get_y_position.return_value = 0.0
    mock_ctrl.core.get_position.return_value = 50.0
    with pytest.raises(SafetyViolation):
        mark_position(mock_ctrl, guard, name="out_of_bounds")
```

### 14.5 Agent-level tests (`tests/test_agent.py` additions)

```python
class TestAutofocusPrompt:
    def test_autofocus_called(self, ctrl, guard):
        scripted = [
            tool_use_response("get_system_state", {}, "c1"),
            tool_use_response("run_autofocus", {"z_range_um": 20, "z_step_um": 0.5}, "c2"),
            text_response("Found focus at Z=48.2 µm."),
        ]
        with patch("microclaw.agent.client", make_mock_client(scripted)):
            reply, _ = run_agent("Please autofocus on my sample", ctrl, guard)
        assert "focus" in reply.lower() or "z" in reply.lower()

```

### 14.6 Integration tests against Demo config (`tests/test_integration.py` additions)

```python
@pytest.mark.integration
def test_snap_and_analyze_demo(headless_mm, unconstrained_guard):
    result = snap_and_analyze(headless_mm, unconstrained_guard)
    assert isinstance(result, list)
    import json, base64
    payload = json.loads(result[0]["text"])
    assert "focus_metric" in payload
    assert base64.standard_b64decode(result[1]["source"]["data"])[:4] == b'\x89PNG'

@pytest.mark.integration
def test_autofocus_demo(headless_mm, unconstrained_guard):
    result = run_autofocus(headless_mm, unconstrained_guard, z_range_um=10, z_step_um=1)
    payload = json.loads(result[0]["text"]) if isinstance(result, list) else result
    assert "best_z_um" in payload
    assert isinstance(payload["metric_curve"], list)

@pytest.mark.integration
def test_position_list_roundtrip_demo(headless_mm, unconstrained_guard):
    headless_mm.clear_positions()
    mark_position(headless_mm, unconstrained_guard, name="test_pos")
    positions = headless_mm.get_positions()
    assert any(p["name"] == "test_pos" for p in positions)
    headless_mm.remove_position("test_pos")

@pytest.mark.integration
def test_autofocus_hook_demo(headless_mm, unconstrained_guard, tmp_path):
    log_path = str(tmp_path / "af_log.json")
    result = run_adaptive_acquisition(
        headless_mm, unconstrained_guard,
        z_start_um=45, z_end_um=55, z_step_um=2,
        save_dir=str(tmp_path), name="af_hook_test",
        hook_strategy="autofocus_per_position",
        hook_params={"z_range_um": 5, "z_step_um": 0.5},
        log_path=log_path,
    )
    assert "complete" in result["status"]
    import json
    log = json.loads(Path(log_path).read_text())
    assert len(log) > 0
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
| **2** | `snap_and_analyze` tool, modified `execute_tool` | `tools.py`, `tools_schema.py` | Phase 1 |
| **3** | `autofocus.py` + `run_autofocus` tool + safety boundary pre-check + unit tests | `autofocus.py`, `tools.py`, `tools_schema.py`, tests | Phase 1 |
| **4** | Position management via MM native API + unit tests | `controller.py`, `tools.py`, `tools_schema.py`, tests | None |
| **5** | `run_multiposition_acquisition` + `run_multiposition_with_autofocus` | `tools.py`, `tools_schema.py`, tests | Phases 3, 4 |
| **6** | `hooks.py` + `run_adaptive_acquisition` + `read_hook_log` + unit tests | `hooks.py`, `tools.py`, `tools_schema.py`, tests | Phases 3, 4 |
| **7** | System prompt update | `agent.py` | Phase 2 |
| **8** | `hook_manager.py` + `generate_and_save_hook` + `list_hooks` + unit tests | `hook_manager.py`, `tools.py`, `tools_schema.py`, tests | Phase 6 |
| **9** | Integration tests against Demo config | `tests/test_integration.py` | Phases 1–7 + MM installed |

Phases 1–7 deliver the core capability. Phase 8 (Claude-generated hooks) is
separable and can be deferred without affecting the primary workflows.

---

## 17. Known Limitations and Mitigations

| Limitation | Mitigation |
|---|---|
| pycro-manager's `PositionList` Java API must be confirmed against the installed version | Phase 4 integration test validates the API before it is relied upon; fall back to a Python-side list if unavailable |
| `snap_to_numpy` via `get_tagged_image()` may behave differently across camera adapters | Wrap with `try/except`; fall back to `core.get_image()` |
| Autofocus sweep blocks the agent loop with no progress updates | Acceptable for v2; v3 could run in a thread and stream intermediate metrics |
| Laplacian variance is sensitive to noise in low-SNR fluorescence images | Pre-smooth with Gaussian (σ=0.5–1.0 px); expose as `smooth_sigma` parameter |
| Claude-generated hook code bypasses AST checks via indirect calls | User review is mandatory; document that the AST check is a first pass, not a guarantee |
| Hook log files can grow large for long multiposition acquisitions | Cap log entries per position; write summary stats at top of log |
| Position list changes made in the MM GUI between agent calls are not automatically reflected | `get_position_list` reads from MM on every call, so the agent always sees the current state |
