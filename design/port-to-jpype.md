# Plan: Port Microclaw from Pycro-Manager to a JPype bridge (jPypeMM)

## Goal

Replace the [pycro-manager](https://github.com/micro-manager/pycro-manager) ZMQ
bridge with the in-process JPype bridge
[jPypeMM](https://github.com/nicost/jPypeMM/tree/main), so that Micro-Manager's
`Studio` and `CMMCore` are driven directly from the JVM embedded in the Python
process instead of over a socket.

## TL;DR feasibility

**Yes, it is possible — but it is not a drop-in swap.** Two changes dominate the
work:

1. **Method names change from snake_case to camelCase.** Pycro-manager
   auto-translates Java `getXPosition()` → Python `get_x_position()`. JPype does
   *not*: it exposes the raw Java names. Every `ctrl.core.<...>()` call (~60
   sites) and every `ctrl.studio.live().<...>()` call is affected.
2. **jPypeMM has no acquisition engine, no `multi_d_acquisition_events`, and no
   hooks.** Pycro-manager's `Acquisition`, event generator, and the
   `post_hardware_hook_fn` / `image_process_fn` callback contract underpin
   `run_zstack`, `run_timelapse`, `run_adaptive_acquisition`, and the entire
   `hooks.py` subsystem. These must be re-created. There are **two options** for
   this, evaluated below:
   - **(A) Pure-Python engine** — a small frame-by-frame loop we own (§3).
   - **(B) Drive AcqEngJ via JPype** — the *same* Java engine pycro-manager uses,
     called directly through jPypeMM's in-process JVM (§3b). **Recommended**
     for fidelity. See "Acquisition engine decision" below.

The recommended strategy is to **insulate the rest of the codebase behind two
adapters** so that the ~60 call sites and the `hooks.py` contract do *not* have
to change individually:

- A `CoreAdapter` / `StudioAdapter` that translate snake_case → camelCase and
  normalise return types.
- A pure-Python `acquisition.py` that re-creates `multi_d_acquisition_events`
  and `Acquisition` honouring the existing hook method signatures.

This concentrates the churn into 3 new/rewritten files and leaves `tools.py`,
`autofocus.py`, and `hooks.py` almost untouched.

---

## How the two bridges differ

| Concern | pycro-manager (current) | jPypeMM (target) |
|---|---|---|
| Transport | ZMQ socket to a separate Java process | JVM embedded **in** the Python process via JPype/JNI |
| Entry point | `Core(port=4827)`, `Studio(port=4827)` | `studio, core = start_mm.main(skip_intro=True)` |
| Method names | snake_case (`get_x_position`) | raw Java camelCase (`getXPosition`) |
| Snap → numpy | `core.snap_image()` + `core.get_tagged_image()` → `.pix`/`.tags` | `start_mm.snap_core(core)` (or `start_mm.snap(studio.live())`) returns numpy directly |
| MDA / events | `multi_d_acquisition_events(...)` | **none** — reimplement |
| Acquisition | `Acquisition(...)` context manager | **none** — reimplement |
| Hooks | `post_hardware_hook_fn`, `image_process_fn` | **none** — reimplement |
| Dataset write/read | NDTiff via the Java engine + `ndstorage.Dataset` | **none** — write TIFF ourselves |
| Headless | `start_headless(...)` | **no true headless**; needs a Windows desktop session |
| Lifecycle | MM independent; Python attaches/detaches | Python **owns** MM; one JVM per process, **not restartable** |
| Platform | Win/Mac/Linux | **Windows only** (per the repo) |

> jPypeMM's README explicitly warns it is experimental: *"Each and every aspect
> of it can and will change so do not base other projects on it unless you are
> ready to maintain a fork."* See the **Risks** section.

---

## Current pycro-manager touchpoints (inventory)

Files that import or call pycro-manager APIs:

- `microclaw/controller.py` — `from pycromanager import Core, Studio`; constructs
  `Core(port=)`, `Studio(port=)`; `is_connected()` via `get_version_info()`;
  reads MM GUI position list through `studio.positions()`.
- `microclaw/config.py` — `from pycromanager import start_headless`.
- `microclaw/tools.py` — `from pycromanager import Acquisition,
  multi_d_acquisition_events`; ~60 `ctrl.core.*` / `ctrl.studio.live().*` calls;
  the `_str_vector` helper.
- `microclaw/image_analysis.py` — `snap_to_numpy()` using `snap_image()` +
  `get_tagged_image()`.
- `microclaw/hooks.py` — hook classes built around pycro-manager's hook
  signatures; also call `ctrl.core.*`.
- `microclaw/autofocus.py` — `ctrl.core.*` and `snap_to_numpy()`.
- `microclaw/__main__.py` — connection failure message ("ZMQ server").
- `microclaw/errors.py` — docstring references ZMQ.
- `pyproject.toml` — `pycromanager`, `ndstorage` dependencies.
- `tests/conftest.py` — `MagicMock` core/studio; integration fixture using
  `MicroscopeController(port=)`.

---

## Target architecture

```
            tools.py / autofocus.py / hooks.py        (≈ unchanged)
                         │  call ctrl.core.get_x_position(), etc.
                         ▼
        ┌───────────────────────────────────────────┐
        │ MicroscopeController                        │
        │   .core  -> CoreAdapter   (snake→camel)     │  NEW adapter layer
        │   .studio-> StudioAdapter (snake→camel)     │
        └───────────────────────────────────────────┘
                         │ delegates to raw JPype objects
                         ▼
            jPypeMM start_mm.main()  ->  (studio_java, core_java)
                         │
                         ▼
                   embedded JVM (Micro-Manager)

        acquisition.py  (NEW)  re-creates multi_d_acquisition_events()
                               and Acquisition(...) in pure Python,
                               calling ctrl.core via the adapter and
                               invoking hook callbacks itself.
```

### Why the adapter (vs. rewriting every call site)

`tools.py`, `autofocus.py`, and `hooks.py` contain ~60 `ctrl.core.<snake>()`
calls. A `__getattr__`-based adapter converts the name once, at the boundary,
so those files keep compiling unchanged and the existing `MagicMock`-based unit
tests keep working with almost no edits. The alternative — hand-editing 60 call
sites to camelCase — is more error-prone and bloats the diff. Recommend the
adapter.

---

## File-by-file changes

### 1. `microclaw/controller.py` — rewrite connection + adapters

Replace the ZMQ `Core`/`Studio` construction with an in-process JVM launch, and
wrap the raw Java objects in name-translating adapters.

```python
from __future__ import annotations
import json
import re
from pathlib import Path

# jPypeMM ships start_mm as a top-level module (uv project). Importing it
# launches/locates the bundled JRE; the JVM is created lazily on main().
import start_mm


def _snake_to_camel(name: str) -> str:
    """get_x_position -> getXPosition ; snap_image -> snapImage."""
    head, *rest = name.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


class _JavaAdapter:
    """Delegates snake_case Python calls to a raw JPype Java object using
    camelCase. Methods return Java objects; callers already wrap scalars with
    float()/int()/str() and use the _str_vector / ROI helpers, so most return
    types are compatible. Add explicit coercions here only where needed."""

    # Names the codebase calls that do NOT follow simple snake->camel,
    # or that jPypeMM exposes differently.
    _OVERRIDES: dict[str, str] = {
        # e.g. "get_pixel_size_um": "getPixelSizeUm",  # verify exact MMCore spelling
    }

    def __init__(self, java_obj):
        object.__setattr__(self, "_java", java_obj)

    def __getattr__(self, name):
        java_name = self._OVERRIDES.get(name, _snake_to_camel(name))
        return getattr(object.__getattribute__(self, "_java"), java_name)


class CoreAdapter(_JavaAdapter):
    """Wraps CMMCore. snap_image/get_tagged_image are handled in
    image_analysis.snap_to_numpy via start_mm.snap_core(); other calls pass
    straight through the snake->camel translation."""


class StudioAdapter(_JavaAdapter):
    """Wraps MMStudio. .live() returns the Java LiveManager wrapped again so
    that .set_live_mode_on()/.is_live_mode_on()/.snap() translate too."""

    def live(self):
        return _JavaAdapter(object.__getattribute__(self, "_java").live())

    def positions(self):
        return _JavaAdapter(object.__getattribute__(self, "_java").positions())


class MicroscopeController:
    """Thin wrapper around the jPypeMM in-process Studio and Core."""

    def __init__(self, port: int = 4827, config_file: str | None = None):
        # NOTE: `port` is now ignored (kept for CLI compatibility). The JVM is
        # in-process; there is no socket. One JVM per Python process.
        studio_java, core_java = start_mm.main(skip_intro=True)
        if config_file:
            core_java.loadSystemConfiguration(config_file)
        self._core = CoreAdapter(core_java)
        self._studio = StudioAdapter(studio_java)
        self._core_raw = core_java   # keep raw handle for start_mm.snap_core()
        self._positions: list[dict] = []

    @property
    def core(self):
        return self._core

    @property
    def studio(self):
        return self._studio

    @property
    def core_raw(self):
        """Raw JPype CMMCore — needed by start_mm.snap_core()."""
        return self._core_raw

    def is_connected(self) -> bool:
        try:
            self._core.get_version_info()   # -> getVersionInfo()
            return True
        except Exception:
            return False

    # --- Position list management: only the Java getters change name ---
    def _read_mm_position_list(self) -> list[dict]:
        pl = self._studio.positions().get_position_list()      # getPositionList()
        out = []
        for i in range(int(pl.getNumberOfPositions())):
            msp = pl.getPosition(i)
            entry: dict = {"name": str(msp.getLabel())}
            for j in range(int(msp.size())):
                sp = msp.get(j)
                if int(sp.numAxes) == 2:          # field: numAxes (was num_axes)
                    entry["x_um"] = round(float(sp.x), 3)
                    entry["y_um"] = round(float(sp.y), 3)
                elif int(sp.numAxes) == 1:
                    entry["z_um"] = round(float(sp.x), 3)
            out.append(entry)
        return out

    # add_position / go_to_position / etc. are unchanged: go_to_position calls
    # self._core.set_xy_position(...) which the adapter maps to setXYPosition.
    # ⚠ VERIFY: MMCore spells it setXYPosition / getXYStageDevice (XY upper).
    #   Add these to CoreAdapter._OVERRIDES because snake->camel yields
    #   "setXyPosition"/"getXyStageDevice".
```

> **Important spelling note:** MMCore uses `XY` (both caps): `setXYPosition`,
> `getXYStageDevice`, `setRelativeXYPosition`, `getXPosition`, `getYPosition`.
> Naïve snake→camel turns `set_xy_position` into `setXyPosition`, which is
> wrong. Put every `*_xy_*` method in `_OVERRIDES`. Likewise verify
> `getPixelSizeUm` vs `getPixelSizeUm()` casing and ROI getters.

### 2. `microclaw/image_analysis.py` — rewrite `snap_to_numpy`

```python
def snap_to_numpy(ctrl) -> np.ndarray:
    """Snap and return a NumPy array via jPypeMM's snap helper."""
    import start_mm
    # start_mm.snap_core copies the JVM-heap buffer into a correctly shaped,
    # correctly typed numpy array (handles uint8/uint16/RGB).
    return np.asarray(start_mm.snap_core(ctrl.core_raw))
```

This removes the dependency on `get_tagged_image()`, `.pix`, and `.tags`, and
also removes the hard-coded `uint16`/`reshape(h, w)` assumption (jPypeMM derives
shape and dtype from the camera). `compute_stats` / `make_thumbnail` are
unchanged.

### 3. `microclaw/acquisition.py` — **NEW** pure-Python acquisition engine

This is the largest piece of new code. It reproduces the two pycro-manager
symbols `tools.py` imports, plus the hook callback contract that `hooks.py`
already targets. The hook classes in `hooks.py` then need **no change**.

```python
from __future__ import annotations
from pathlib import Path
import time
import numpy as np
import tifffile

from microclaw.image_analysis import snap_to_numpy


def multi_d_acquisition_events(
    *,
    num_time_points: int | None = None,
    time_interval_s: float = 0.0,
    z_start: float | None = None,
    z_end: float | None = None,
    z_step: float | None = None,
    channel_group: str | None = None,
    channels: list[str] | None = None,
    channel_exposures_ms: list[float] | None = None,
    order: str = "tpcz",
) -> list[dict]:
    """Generate a flat list of event dicts. Mirrors the subset of
    pycro-manager's generator that tools.py uses (time, z, channel)."""
    z_positions = None
    if z_step is not None and z_start is not None and z_end is not None:
        n = int(round((z_end - z_start) / z_step)) + 1
        z_positions = [z_start + i * z_step for i in range(n)]
    times = range(num_time_points) if num_time_points else [None]
    chans = channels or [None]

    events: list[dict] = []
    for t in times:
        for ci, ch in enumerate(chans):
            for zi, z in enumerate(z_positions or [None]):
                axes = {}
                if t is not None: axes["time"] = t
                if ch is not None: axes["channel"] = ch
                if z is not None: axes["z"] = zi
                ev: dict = {"axes": axes}
                if z is not None: ev["z"] = z
                if ch is not None:
                    ev["channel"] = {"group": channel_group, "config": ch}
                    if channel_exposures_ms:
                        ev["exposure"] = channel_exposures_ms[ci]
                if t is not None and time_interval_s:
                    ev["min_start_time"] = t * time_interval_s
                events.append(ev)
    return events


class Acquisition:
    """Drop-in replacement for pycro-manager's Acquisition for the cases
    tools.py needs: directory/name, show_display, post_hardware_hook_fn,
    image_process_fn. Honors the same hook signatures as hooks.py expects."""

    def __init__(self, *, directory, name, show_display=True,
                 ctrl=None,                       # NEW: explicit core handle
                 post_hardware_hook_fn=None,
                 image_process_fn=None):
        self.directory = Path(directory)
        self.name = name
        self.ctrl = ctrl
        self._post_hw = post_hardware_hook_fn
        self._img_proc = image_process_fn
        self._frames: list[np.ndarray] = []
        self._t0 = None
        self._dataset_disk_location = None

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self._t0 = time.time()
        return self

    def __exit__(self, *exc):
        # Write a single OME-TIFF stack (replaces the NDTiff dataset).
        if self._frames:
            out = self.directory / f"{self.name}.ome.tif"
            tifffile.imwrite(out, np.stack(self._frames), imagej=True)
            self._dataset_disk_location = str(out)
        return False

    def acquire(self, events: list[dict]):
        core = self.ctrl.core
        for event in events:
            # honor min_start_time pacing (timelapse interval)
            if "min_start_time" in event:
                wait = event["min_start_time"] - (time.time() - self._t0)
                if wait > 0:
                    time.sleep(wait)
            # hardware moves
            if "channel" in event:
                core.set_config(event["channel"]["group"], event["channel"]["config"])
                core.wait_for_config(event["channel"]["group"], event["channel"]["config"])
            if "exposure" in event:
                core.set_exposure(event["exposure"])
            if "z" in event:
                core.set_position(event["z"])
                core.wait_for_device(core.get_focus_device())
            # post-hardware hook (may mutate or drop the event)
            if self._post_hw is not None:
                event = self._post_hw(event)
                if event is None:
                    continue
            # capture
            image = snap_to_numpy(self.ctrl)
            metadata = dict(event.get("axes", {}))
            # image-process hook (may return None to drop, or (image, meta))
            if self._img_proc is not None:
                result = self._img_proc(image, metadata, None)  # event_queue=None
                if result is None:
                    continue
                image, metadata = result
            self._frames.append(image)
```

> **Hook-contract caveats** (call out in code comments):
> - pycro-manager passes a real `event_queue` to `image_process_fn`. None of the
>   current hooks in `hooks.py` use it (they accept it as a positional arg and
>   ignore it), so passing `None` is safe **today**. Generated/user hooks could
>   rely on it; document the limitation.
> - `PositionFilterHook` returns `None` to "drop the rest of that position's
>   events." pycro-manager prunes the queue; our flat-list loop only drops the
>   current frame. For true position-level pruning we'd need to track
>   `position_index` and skip subsequent same-position events. Implement if the
>   filter behaviour matters (see Risks).

### 3b. `microclaw/acquisition.py` — **ALTERNATIVE & RECOMMENDED**: drive AcqEngJ via JPype

**Yes — AcqEngJ can be used as the acquisition engine, and it is the better
option.** This is the key realization: *pycro-manager's `Acquisition` and hooks
are already a thin Python wrapper over the Java engine
[AcqEngJ](https://github.com/micro-manager/AcqEngJ).* It communicates with the
JVM over ZMQ. With jPypeMM the JVM is **in-process**, so we can talk to the exact
same Java classes directly through JPype — no socket, no reimplementation of the
hard parts (hardware sequencing, threading, async capture, NDTiff writing). We
are essentially re-creating the small slice of pycro-manager that bridges Python
callbacks to AcqEngJ's Java interfaces, but over JNI instead of ZMQ.

#### Relevant AcqEngJ Java API (verified from source)

```java
// org.micromanager.acqj.internal.Engine   (singleton, holds the CMMCore)
public Engine(CMMCore core)                 // construct once with jPypeMM's core
public static Engine getInstance()
public static CMMCore getCore()

// org.micromanager.acqj.main.Acquisition  (implements AcquisitionAPI)
public Acquisition(AcqEngJDataSink sink)
public void addHook(AcquisitionHook hook, int type)
public void addImageProcessor(TaggedImageProcessor p)
public Future submitEventIterator(Iterator<AcquisitionEvent> evt)
public void finish()
public void waitForCompletion()
public void abort()

// hook insertion points (org.micromanager.acqj.api.AcquisitionAPI constants)
BEFORE_HARDWARE_HOOK = 1
BEFORE_Z_DRIVE_HOOK  = 2
AFTER_HARDWARE_HOOK  = 3   // <- maps to pycro-manager post_hardware_hook_fn
AFTER_CAMERA_HOOK    = 4
AFTER_EXPOSURE_HOOK  = 5

// org.micromanager.acqj.api.AcquisitionHook
public AcquisitionEvent run(AcquisitionEvent event);   // return event, or null to drop
public void close();

// org.micromanager.acqj.api.TaggedImageProcessor  (runs on its own thread)
public void setAcqAndQueues(AcquisitionAPI acq,
        BlockingQueue<TaggedImage> source,
        BlockingQueue<TaggedImage> sink);  // pull from source, push to sink

// org.micromanager.acqj.main.AcquisitionEvent
public AcquisitionEvent(AcquisitionAPI acq)
setZ(Integer index, Double positionUm)
setX(double) / setY(double)
setConfigGroup(String) / setConfigPreset(String)   // channel
setExposure(double)
setMinimumStartTime(Long ms)                        // timelapse interval
setAxisPosition(String label, Object position)
```

#### How it maps to Microclaw's existing hook contract

The beautiful part: `hooks.py` already speaks pycro-manager's hook dialect
(`post_hardware_hook_fn(event)` → event; `image_process_fn(image, metadata,
event_queue)` → `(image, metadata)` or `None`). We keep `hooks.py` **unchanged**
and write two small JPype shim classes that implement the Java interfaces and
translate at the boundary:

```python
from __future__ import annotations
import queue
import numpy as np
import jpype
from jpype import JClass, JImplements, JOverride

# Resolved after the JVM is up (jPypeMM has already started it).
_Engine          = lambda: JClass("org.micromanager.acqj.internal.Engine")
_Acquisition     = lambda: JClass("org.micromanager.acqj.main.Acquisition")
_AcquisitionEvent= lambda: JClass("org.micromanager.acqj.main.AcquisitionEvent")
_API             = lambda: JClass("org.micromanager.acqj.api.AcquisitionAPI")


def ensure_engine(core_raw):
    """Engine is a singleton; create it once from jPypeMM's CMMCore."""
    E = _Engine()
    if E.getInstance() is None:
        E(core_raw)              # constructor registers the singleton
    return E.getInstance()


def _event_to_dict(ev) -> dict:
    """AcquisitionEvent (Java) -> the dict shape hooks.py expects."""
    axes = {}
    # AcquisitionEvent exposes getZIndex/getTIndex/getConfigPreset etc.;
    # VERIFY exact getters against the installed AcqEngJ version.
    return {"axes": axes, "_java": ev}   # keep the Java handle for write-back


@JImplements("org.micromanager.acqj.api.AcquisitionHook")
class _PostHardwareHookShim:
    """Wraps a Microclaw hook's post_hardware_hook_fn for AFTER_HARDWARE_HOOK."""
    def __init__(self, py_fn):
        self._py_fn = py_fn

    @JOverride
    def run(self, event):
        d = _event_to_dict(event)
        out = self._py_fn(d)         # existing AutofocusHook.post_hardware_hook_fn
        return None if out is None else event   # hooks here mutate hardware, not event

    @JOverride
    def close(self):
        pass


@JImplements("org.micromanager.acqj.api.TaggedImageProcessor")
class _ImageProcessorShim:
    """Wraps a Microclaw hook's image_process_fn. Pulls TaggedImage off the
    source queue, runs the Python fn, forwards (or drops) onto the sink."""
    def __init__(self, py_fn):
        self._py_fn = py_fn

    @JOverride
    def setAcqAndQueues(self, acq, source, sink):
        import threading
        self._acq, self._source, self._sink = acq, source, sink
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        jpype.java.lang.Thread.currentThread()   # ensure JVM thread attach
        POISON = None
        while True:
            tagged = self._source.take()          # blocking
            if tagged is None or tagged.tags is None:   # end-of-stream sentinel
                self._sink.put(tagged)
                break
            image = _tagged_to_numpy(tagged)
            meta = _tags_to_dict(tagged.tags)
            result = self._py_fn(image, meta, None)   # event_queue: see caveat
            if result is not None:
                self._sink.put(tagged)            # forward original to storage


def _tagged_to_numpy(tagged) -> np.ndarray:
    w = int(tagged.tags.getInt("Width")); h = int(tagged.tags.getInt("Height"))
    return np.asarray(tagged.pix[:]).reshape(h, w)   # dtype per camera; verify
```

The thin `Acquisition` facade keeps the **same constructor/`acquire()` surface**
that `tools.py` already uses, so `tools.py` barely changes:

```python
class Acquisition:
    def __init__(self, *, directory, name, show_display=True, ctrl=None,
                 post_hardware_hook_fn=None, image_process_fn=None):
        ensure_engine(ctrl.core_raw)
        sink = _make_ndtiff_sink(directory, name)   # see DataSink note below
        self._acq = _Acquisition()(sink)
        if post_hardware_hook_fn:
            self._acq.addHook(_PostHardwareHookShim(post_hardware_hook_fn),
                              _API().AFTER_HARDWARE_HOOK)
        if image_process_fn:
            self._acq.addImageProcessor(_ImageProcessorShim(image_process_fn))
        self._dir, self._name = directory, name

    def __enter__(self): return self
    def __exit__(self, *exc):
        self._acq.finish(); self._acq.waitForCompletion(); return False

    def acquire(self, events: list[dict]):
        self._acq.submitEventIterator(_PyEventIterator(self._acq, events))

    @property
    def _dataset_disk_location(self):
        return str(Path(self._dir) / self._name)
```

`multi_d_acquisition_events` stays a pure-Python dict generator (as in §3); a
`_PyEventIterator` (a `@JImplements(java.util.Iterator)` shim) converts each dict
into a Java `AcquisitionEvent` on the fly via `setZ`/`setConfigPreset`/
`setExposure`/`setMinimumStartTime`. So `tools.py`'s event-building code is
unchanged; only the iterator boundary differs.

#### DataSink / NDTiff

AcqEngJ writes through an `AcqEngJDataSink`. Two choices:

- **Reuse the Java NDTiff sink** (`NDTiffAndRAMStorage`, shipped in
  `NDTiffStorage.jar` / `PycroManagerJava.jar`). This preserves the **exact**
  on-disk NDTiff format, so `ndstorage.Dataset` and the current
  `export_dataset_as_tiff` keep working **unchanged**, and we keep `ndstorage`
  as a dependency. *Recommended.*
- Implement `AcqEngJDataSink` in Python (`@JImplements`) and write TIFF
  ourselves (as in §3). More code; only worth it if we want to drop the NDTiff
  jars.

#### Classpath prerequisite (must verify)

AcqEngJ ships as `AcqEngJ.jar` alongside `NDTiffStorage.jar`, `NDViewer.jar`,
and `PycroManagerJava.jar`. These are included in Micro-Manager nightly builds
that support pycro-manager, and jPypeMM assembles its classpath from the MM
install's jars — so they are **likely already on the classpath**. **Action:**
during the spike, confirm `JClass("org.micromanager.acqj.main.Acquisition")`
resolves; if not, add the jars to the classpath jPypeMM builds (or drop them
into the MM `plugins`/`jars` folder).

#### Option A vs Option B — recommendation

| | A: pure-Python engine (§3) | B: AcqEngJ via JPype (§3b) |
|---|---|---|
| Fidelity to current behaviour | approximate | **identical** (same engine pycro-manager uses) |
| Hardware sequencing / async capture / performance | none (Python `sleep`) | **full** |
| Hook semantics (event injection, pre/post hardware, position pruning) | partial, hand-rolled | **native** |
| NDTiff output + `ndstorage` reader + `export_dataset_as_tiff` | dropped → OME-TIFF | **preserved unchanged** |
| `hooks.py` changes | none | none (shims translate) |
| JPype interop complexity | low | **higher** (`@JImplements` interfaces, Java iterator, threading) |
| Dependency on MM-shipped jars | no | yes (must be on classpath) |
| Risk surface | larger behavioural drift | JVM-thread/JPype-callback subtleties |

**Recommendation: pursue Option B (AcqEngJ).** It reuses the engine Microclaw
already depends on (just over a different transport), preserves the data format
and the entire `hooks.py`/`export` surface, and avoids re-litigating frame
timing and hook semantics. Keep Option A documented as a fallback if AcqEngJ's
jars turn out not to be on jPypeMM's classpath or the JPype callback threading
proves troublesome.

### 4. `microclaw/tools.py` — swap import, thread `ctrl` into `Acquisition`

```python
# was: from pycromanager import Acquisition, multi_d_acquisition_events
from microclaw.acquisition import Acquisition, multi_d_acquisition_events
```

Every `with Acquisition(directory=save_dir, name=name, show_display=True, ...)`
gains `ctrl=ctrl` (3 sites: `run_zstack`, `run_timelapse`,
`run_adaptive_acquisition`). Example:

```python
with Acquisition(directory=save_dir, name=name, show_display=True, ctrl=ctrl) as acq:
    acq.acquire(events)
actual_path = acq._dataset_disk_location or str(Path(save_dir) / name)
```

All ~55 `ctrl.core.*` / `ctrl.studio.live().*` calls in this file stay as-is —
the adapter translates them. `_str_vector` is unchanged (Java `StrVector` still
exposes `.size()`/`.get(i)`; `getAvailableConfigs`/`getLoadedDevices` reached
via the adapter). `get_roi()` still reads `.x/.y/.width/.height` off the Java
`Rectangle`. **Verify** the `_OVERRIDES` cover `getXPosition`,
`setXYPosition`, `getXYStageDevice`, etc.

### 5. `microclaw/hooks.py` — **no change to the hook classes**

The hook method signatures (`post_hardware_hook_fn(self, event)`,
`image_process_fn(self, image, metadata, event_queue)`) match what
`acquisition.Acquisition` now calls. Their internal `self.ctrl.core.*` calls go
through the adapter. **Action:** none, beyond verifying behaviour against the
new engine (esp. `PositionFilterHook` semantics).

### 6. `microclaw/autofocus.py` — **no change**

All access is `ctrl.core.*` and `snap_to_numpy(ctrl)`, both adapter-routed.
Verify `get_focus_device`, `set_position`, `wait_for_device` map cleanly
(`getFocusDevice`, `setPosition`, `waitForDevice` — all clean snake→camel).

### 7. `microclaw/config.py` — replace headless launch

jPypeMM has no true headless. `launch_headless` becomes a config-loading helper
(the JVM is started in the controller). Keep the function name to avoid breaking
imports, but change semantics + docstring:

```python
def launch_headless(mm_app_path: str, config_file: str, port: int = 4827) -> None:
    """DEPRECATED under jPypeMM: there is no true headless mode. The JVM is
    launched in-process by MicroscopeController. Pass config_file to the
    controller instead (it calls core.loadSystemConfiguration). Requires a
    Windows desktop session."""
    raise NotImplementedError(
        "Headless mode is not available with jPypeMM. Construct "
        "MicroscopeController(config_file=...) inside a desktop session."
    )
```

### 8. `microclaw/__main__.py` — connection messaging + config plumbing

- Update the failure message (no ZMQ server). The real failure modes now are:
  JVM failed to start, MM install not found, not in a desktop session, wrong
  arch / missing VC++ redist.
- Optionally add `--config-file` and pass it to `MicroscopeController`.

```python
print("Starting embedded Micro-Manager (JVM)...")
ctrl = MicroscopeController(config_file=args.config_file)
if not ctrl.is_connected():
    sys.exit(
        "Could not start the embedded Micro-Manager JVM. Check that you are in "
        "a desktop session, MM 2.0 (64-bit) is installed, and the VC++ "
        "redistributable is present."
    )
```

### 9. `microclaw/errors.py` — update docstring

Replace "ZMQ timeout"/"ZMQ server disabled" wording with JPype/JVM failure
modes (`jpype.JException` for Java-side errors; `RuntimeError` if the JVM is
down).

### 10. `pyproject.toml` — dependency swap

```toml
dependencies = [
    "anthropic>=0.40.0",
    "jpype1>=1.5.0",          # JPype runtime
    # jPypeMM itself is not on PyPI; install from the repo (see note below)
    "tifffile>=2024.1.1",
    "numpy>=1.26",
    "pyyaml>=6.0",
    "scipy>=1.12",
    "Pillow>=10.0",
]
```

- **Remove** `pycromanager`. **`ndstorage` depends on the engine choice:**
  under **Option B** with the NDTiff Java sink, datasets stay in NDTiff format,
  so **keep `ndstorage`** and `export_dataset_as_tiff` is unchanged. Under
  **Option A**, drop `ndstorage` and write OME-TIFF (see `export_dataset_as_tiff`
  below).
- jPypeMM is a `uv`-managed repo, not a PyPI package. Either vendor `start_mm.py`
  + its helpers into the project, add it as a git/submodule dependency, or
  document a manual install. **Recommend vendoring** given the upstream "expect
  breakage / maintain a fork" warning.

### 11. `export_dataset_as_tiff` (in `tools.py`) — drop ndstorage

Currently reads NDTiff via `ndstorage.Dataset`. Since the new `Acquisition`
writes a plain OME-TIFF stack, this tool can either:
- become a thin "already a TIFF" passthrough/copy, or
- be retargeted to read the OME-TIFF we wrote.

```python
def export_dataset_as_tiff(ctrl, guard, dataset_path, output_path) -> dict:
    # New datasets are already OME-TIFF stacks; just (re)write/copy.
    import shutil
    src = Path(dataset_path)
    if src.is_dir():   # legacy NDTiff dir — no longer produced; warn
        return {"error": "NDTiff datasets are no longer produced under jPypeMM."}
    shutil.copyfile(src, output_path)
    return {"status": "Export complete.", "output_path": output_path}
```

### 12. `tests/conftest.py` and the test suite

- Unit tests mock `ctrl.core` / `ctrl.studio` with `MagicMock`, so they are
  **largely insulated** — a `MagicMock` answers `get_x_position()` and
  `getXPosition()` equally. The mocks keep their snake_case names because the
  *tests* call through `ctrl.core` which the production code reaches via the
  adapter; but the adapter is bypassed when `ctrl` itself is a MagicMock. ✅
  Minimal change.
- **Acquisition tests** that import `pycromanager.Acquisition` /
  `multi_d_acquisition_events` (if any patch those symbols) must repoint to
  `microclaw.acquisition`. Grep: `grep -rn "pycromanager\|multi_d_acquisition\|Acquisition" tests/`.
- The integration fixture `headless_mm` must change: there is no port; it must
  build `MicroscopeController(config_file=...)` and can only run on a Windows
  desktop session. Re-mark/skip accordingly (e.g. skip unless `MM_DESKTOP=1`).
- Add a unit test for the new `multi_d_acquisition_events` event shape and for
  `Acquisition.acquire()` driving a `MagicMock` core + invoking hook fns.

### 13. Docs: `README.md`, `design/runtime-pycromanager-skill.md`, CLAUDE.md memory

- README: replace the "enable ZMQ server / Pycro-Manager" setup with the
  jPypeMM in-process launch, Windows-only caveat, and `uv`/VC++ prerequisites.
- The project memory note ([[project_microclaw]]) describing the
  pycro-manager + ZMQ architecture should be updated after the port lands.

---

## Suggested implementation order

1. **Spike the bridge** (no Microclaw code): confirm `start_mm.main()`,
   `snap_core()`, the exact MMCore method spellings, **and that
   `JClass("org.micromanager.acqj.main.Acquisition")` resolves** (Option B
   classpath check, risk #12) on a real Windows box. This de-risks everything
   downstream. (Owner action — see Open questions.)
2. `controller.py` adapters + `_OVERRIDES` table, verified against a live core.
3. `image_analysis.snap_to_numpy`.
4. `acquisition.py` — implement **Option B** (AcqEngJ shims: hook + image-processor
   `@JImplements`, `_PyEventIterator`, NDTiff sink) if the spike confirmed the
   jars; otherwise fall back to **Option A** (pure Python). Unit-test the event
   generator and iterator against a mock core.
5. `tools.py` import swap + `ctrl=ctrl` threading; `export_dataset_as_tiff`.
6. `config.py`, `__main__.py`, `errors.py`, `pyproject.toml`.
7. Test suite repoint + integration fixture.
8. Docs + memory update.

---

## Potential issues & risks

1. **camelCase mapping correctness (high).** MMCore's `XY` casing
   (`setXYPosition`, `getXYStageDevice`) breaks naïve snake→camel. Every XY
   method, plus any acronym methods, must be in `_OVERRIDES`. Wrong mappings
   fail only at runtime against hardware. Mitigation: build `_OVERRIDES` from the
   live `dir(core)` listing during the spike; add an adapter unit test asserting
   each expected Java attribute exists.

2. **No acquisition engine / hooks (high) — largely mitigated by Option B.**
   jPypeMM ships no MDA/hook layer. **Option B (§3b) drives AcqEngJ directly**,
   which removes this risk: it is the same engine (hardware sequencing, async
   capture, NDTiff) pycro-manager uses, reached over JNI instead of ZMQ. The
   residual risks then become #11 (JVM-thread callbacks) and the classpath
   prerequisite (#12). If we instead fall back to Option A (pure Python), this
   reverts to a real concern: frame timing governed by Python `time.sleep`, no
   sequencing — acceptable only for low-throughput, agent-driven use.

3. **`event_queue` contract gap (medium).** Our `image_process_fn` is called
   with `event_queue=None`. Current hooks ignore it, but Claude-generated hooks
   (`hook_manager.py` lets users/Claude author new hooks) might use it. Document
   the limitation in the hook-authoring docs/`hook_docs.py`.

4. **`PositionFilterHook` semantics (medium).** It relies on returning `None` to
   prune *remaining events at that position*. The flat-loop engine drops only
   the current frame unless we add position-aware skipping. Reproduce the
   behaviour explicitly or document the change.

5. **No true headless / Windows-only (high, environmental).** jPypeMM needs a
   Windows desktop session; no Mac/Linux, no headless CI. The dev machine here
   is macOS (`darwin`) — **you cannot run the integration path locally.** CI
   integration tests must move to a Windows desktop runner or stay skipped.

6. **One JVM per process, not restartable (medium).** Reconnecting after an MM
   crash means restarting the whole Python process. The `--port` arg and any
   reconnect logic become meaningless. A JVM crash takes the agent down with it
   — consider supervising the process.

7. **Upstream instability (medium).** jPypeMM's own README says the API "can and
   will change." Recommend **vendoring** `start_mm.py` (+ helpers) at a pinned
   commit rather than depending on the moving repo.

8. **Dataset format change (medium).** Switching from NDTiff to OME-TIFF changes
   on-disk output. Anything (downstream tools, EMU/SMLM workflows in
   `emu_manager.py`, docs) that assumes an NDTiff directory must be checked.
   Verify `emu_manager.py` and the `*_docs.py` references.

9. **Return-type coercion (low–medium).** JPype returns Java `double`/`String`/
   `StrVector` objects. Most call sites already wrap with `float()`/`int()`/
   `str()` or use `_str_vector`, but audit spots that don't (e.g. raw
   `get_exposure()` return passed straight into JSON in `get_exposure` tool;
   Java `Double` usually JSON-serialises, but coerce to be safe).

10. **`StrVector` iteration (low).** `_str_vector` checks `hasattr(sv, "size")`.
    JPype `StrVector` exposes `.size()`/`.get(i)`, so it still works — but
    confirm JPype doesn't instead present it as a Python list (in which case the
    `else` branch handles it anyway). Low risk; covered.

11. **Threading / JVM callbacks / Swing EDT (medium; higher under Option B).**
    MM GUI calls (live view, `studio.live().snap(True)`) may expect the Swing
    event-dispatch thread; pycro-manager marshalled this over the socket, but
    in-process JPype calls run on the Python thread. **Under Option B**, AcqEngJ
    invokes our `AcquisitionHook.run()` on its hardware thread and runs
    `TaggedImageProcessor` on its own thread — so Python callbacks execute on
    *Java* threads. JPype must attach those threads to the interpreter, and the
    GIL is taken on each crossing; long-running Python hook code can stall the
    engine. Keep hook bodies short, ensure thread attachment, and test
    `post_hardware_hook_fn` (autofocus sweep) and `image_process_fn` paths under
    load. This is the main reason Option A is retained as a fallback.

12. **AcqEngJ jars on the classpath (medium; Option B only).** Option B requires
    `AcqEngJ.jar`, `NDTiffStorage.jar`, `NDViewer.jar`, and `PycroManagerJava.jar`
    to be resolvable in jPypeMM's assembled classpath. They ship with
    pycro-manager-capable MM builds and are likely present, but this is a hard
    prerequisite — verify `JClass("org.micromanager.acqj.main.Acquisition")`
    resolves during the spike before committing to Option B.

---

## Open questions for the user

- **Platform:** Microclaw will become **Windows-desktop-only**. Is that
  acceptable, or do we need to keep a pycro-manager fallback path for
  Mac/Linux/headless CI? (Could keep both backends behind a controller flag.)
- **Acquisition engine choice (now answered — confirm):** §3b shows AcqEngJ
  *can* be driven directly through JPype, giving full fidelity with no change to
  `hooks.py`. Recommendation is **Option B (AcqEngJ)** with Option A (pure
  Python) as fallback. Confirm this is the intended direction, contingent on the
  spike verifying the AcqEngJ jars are on jPypeMM's classpath (risk #12).
- **Dependency strategy for jPypeMM:** vendor at a pinned commit (recommended) vs.
  git submodule vs. manual install instructions?
