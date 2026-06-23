# Implementation plan: Port Microclaw to jPypeMM + AcqEngJ (Option B)

Detailed plan for replacing pycro-manager with the in-process JPype bridge
[jPypeMM](https://github.com/nicost/jPypeMM) while driving acquisitions through
the Java engine [AcqEngJ](https://github.com/micro-manager/AcqEngJ) directly.

This supersedes the high-level Option B sketch in `port-to-jpype.md` (§3b).

## Scope & decisions (locked)

- **Engine:** AcqEngJ, called over JNI through jPypeMM's in-process JVM. This is
  the same engine pycro-manager wraps, so behaviour and on-disk data match.
- **Headless:** **dropped entirely.** No `start_headless`, no headless config
  path. MM launches in-process and requires a desktop session.
- **Platform:** **Windows only** for now. No Mac/Linux support is attempted; the
  dev machine (macOS) cannot run the live path — see "Spike / verification".
- **jPypeMM dependency:** **always install the latest from GitHub** (track the
  default branch; no version pin), via **Strategy B (clone-on-path)** — see §1.3.
- **Default save format:** **OME-TIFF** (Bio-Formats-compatible, written
  Java-side); NDTiff remains available via `storage_format="ndtiff"`.
- **No name adapter — raw camelCase Java calls.** `ctrl.core`/`ctrl.studio` are
  the raw JPype objects; call sites use Java names directly (`getXPosition`,
  `setLiveModeOn`). Deliberate readability choice (a Java call is visibly a Java
  call) that also removes the snake→camel converter's casing-bug class. Cost: a
  larger, mechanical diff (every call site) and a careful conftest mock rename.
- **`hooks.py` and `autofocus.py`:** *logic* unchanged — the JPype shims feed the
  hooks the dict/numpy shapes they already expect; only their internal
  `ctrl.core.*` calls switch to camelCase (§7).

---

## 1. Dependencies (`pyproject.toml`)

### 1.1 Key facts about jPypeMM packaging (confirmed from its repo)

- Project name `jpypemm` 0.1.0; importable entry module is `start_mm` (a
  root-level `start_mm.py`, **flat layout, no `[build-system]`, not a installable
  package**).
- Requires **Python `>=3.11,<3.14`** → Microclaw must bump from `>=3.10`.
- Core deps are heavy (`jpype1`, `numpy>=2`, `ndv[qt]`, `scikit-image`,
  `cellpose`, optional `torch`). We only need `start_mm` + `jpype1` + `numpy`;
  the rest are for jPypeMM's own examples.

Because jPypeMM has **no build backend**, `pip install git+…` is unreliable
(setuptools auto-discovery on a flat script repo). Two install strategies; pick
per environment.

### 1.2 Strategy A — git direct reference (alternative, not chosen)

```toml
[project]
name = "microclaw"
version = "0.1.0"
requires-python = ">=3.11,<3.14"          # bumped to satisfy jPypeMM
dependencies = [
    "anthropic>=0.40.0",
    "jpype1>=1.7.1",                       # JPype runtime (also a jPypeMM dep)
    "jpypemm @ git+https://github.com/nicost/jPypeMM.git@main",
    # `ndstorage` and `tifffile` are dropped — saving is Java-side and the
    # `export_dataset_as_tiff` tool is removed (see §1.4, §5).
    "numpy>=1.26",
    "pyyaml>=6.0",
    "scipy>=1.12",
    "Pillow>=10.0",
]
```

> ⚠ **Build caveat.** If `pip`/`uv` cannot build `jpypemm` (no `[build-system]`),
> use Strategy B. To force "latest" with a git ref, reinstall with
> `pip install --upgrade --force-reinstall "jpypemm @ git+…"` (pip caches git URLs
> otherwise); `uv` re-resolves git deps on `uv lock --upgrade-package jpypemm`.

### 1.3 Strategy B — clone-on-path bootstrap (**CHOSEN**)

Vendor the repo next to Microclaw and put it on `sys.path`; a single
`git pull` refreshes to latest. Keeps jPypeMM's heavy transitive deps out of our
environment (we install only `jpype1`).

```bash
# one-time, e.g. in a Makefile target `make jpypemm`:
git clone https://github.com/nicost/jPypeMM third_party/jPypeMM   # or `git -C … pull`
```

```python
# microclaw/_jpypemm_bootstrap.py  (imported first, before `import start_mm`)
import os, sys
_p = os.environ.get("JPYPEMM_PATH",
                    os.path.join(os.path.dirname(__file__), "..", "third_party", "jPypeMM"))
if _p not in sys.path:
    sys.path.insert(0, os.path.abspath(_p))
```

`pyproject.toml` under Strategy B drops the `jpypemm @ git+…` line and keeps just
`jpype1`. Document `JPYPEMM_PATH` override in the README.

**Decision: use Strategy B.** It is deterministic, needs no build backend, and
keeps jPypeMM's heavy transitive deps (torch/cellpose) out of our environment.
Strategy A is retained above only as a documented alternative for anyone who
prefers a pip-managed install.

### 1.3a Can the install be automated?

Partly — there are three layers with different automation stories:

- **Fetching/updating jPypeMM itself — yes, automatable.** A `microclaw-setup`
  console-script (or Makefile target) can `git clone`/`git pull` the repo for
  Strategy B, or `uv`/`pip` resolves the git dependency for Strategy A. No manual
  download needed.
- **JVM / classpath / native DLL wiring — automatic at runtime.** `start_mm`
  auto-detects the MM install under `C:\Program Files\`, points JPype at the
  bundled `jvm.dll`, and assembles the classpath + device-DLL paths itself. We
  just call `start_mm.main()`; nothing for the user to configure.
- **Micro-Manager 2.0 (64-bit) + VC++ 2015–2022 x64 redistributable — manual.**
  These are external native installers that cannot be pulled in via pip/uv. The
  user installs MM once; we document it as a prerequisite (the AcqEngJ jars, §1.5,
  ride along with a pycro-manager-capable MM build).

So Microclaw can ship a one-command setup that fetches jPypeMM and verifies the
environment, but the MM + VC++ install remains a documented user step.

### 1.4 Removed deps

- **Remove `pycromanager`** everywhere.
- **Drop `ndstorage` too.** Its only consumer was `export_dataset_as_tiff`'s
  NDTiff→TIFF conversion. With **OME-TIFF as the default save format** (already
  standard/Bio-Formats-readable, written Java-side), no Python-side reader or
  conversion step is needed — the agent just reports the saved `.ome.tif` path.
  This lets us remove `export_dataset_as_tiff` entirely (§5). Users who
  deliberately choose `storage_format="ndtiff"` read those datasets with standard
  NDTiff tooling, or simply re-run with OME-TIFF when they want a portable TIFF.
- **`tifffile` can also go.** Its only use is `export_dataset_as_tiff`
  (`tools.py:402`); removing that tool removes the last `tifffile` consumer. Drop
  it unless a future feature needs plain TIFF I/O.

### 1.5 Classpath jars (hard prerequisite)

AcqEngJ must be resolvable in jPypeMM's assembled classpath.
**✓ confirmed on the dev install** — `JClass("org.micromanager.acqj.main.Acquisition")`
and all `org.micromanager.acqj.*` classes resolve (spike §10.4), so `AcqEngJ.jar`
is present.

> ⚠ **But `org.micromanager.ndtiffstorage.NDTiffAndRAMStorage` did NOT resolve**
> (spike §10) — the NDTiff storage jar is apparently not bundled in this MM build,
> or the class lives under a different name. Consequences: the **NDTiff sink option
> is unconfirmed/unavailable as named**, which reinforces defaulting to **OME-TIFF
> via the MM `Datastore`** (§4.4) — that path uses `MMStudio` (`studio.data()`),
> not a separate storage jar. Before relying on NDTiff, the camera-equipped re-run
> must locate the real storage class (or we drop the jar into MM `plugins/`/`jars/`).

---

## 2. Architecture

```
 tools.py / autofocus.py / hooks.py        (logic unchanged; calls now camelCase)
        │  ctrl.core.getXPosition()  ·  with Acquisition(...) as acq: acq.acquire(events)
        ▼
 MicroscopeController
   .core   -> raw JPype CMMCore   (camelCase Java calls directly)   [controller.py]
   .studio -> raw JPype MMStudio
        │
        ▼
 jPypeMM:  studio, core = start_mm.main(skip_intro=True)   → embedded JVM (MM 2.0)
        │
        ▼
 acquisition.py  (NEW)  ── drives AcqEngJ via JPype ──────────────────────────────┐
   Engine(core)                             singleton, once                        │
   Acquisition(sink)                        main engine object                     │
   addHook(_PostHardwareHookShim, AFTER_HARDWARE_HOOK)   ← wraps post_hardware_fn   │
   addImageProcessor(_ImageProcessorShim)               ← wraps image_process_fn   │
   submitEventIterator(_PyEventIterator(events))         ← dict → AcquisitionEvent  │
   sink = make_sink(...)  → MM Datastore (OME-TIFF *or* NDTiff), Java-side ──────────┘
```

`ctrl.core` / `ctrl.studio` are the **raw JPype objects** — no name adapter. Call
sites use the actual Java camelCase names (`getXPosition`, `setLiveModeOn`), which
makes a Java/JVM call visibly distinct from surrounding Python. The shims in
`acquisition.py` translate AcqEngJ's Java objects (`AcquisitionEvent`,
`TaggedImage`) to the dict/numpy shapes `hooks.py` expects, so the hook *classes*
keep their signatures (only their internal `ctrl.core.*` calls go camelCase).

---

## 3. `controller.py` — in-process launch, raw JPype objects (no adapter)

Replaces ZMQ `Core`/`Studio`. `ctrl.core` and `ctrl.studio` expose the **raw
JPype objects directly** — no name-translation layer. All call sites use Java
camelCase (`getXPosition`, `setLiveModeOn`, …). The position-list logic keeps its
structure; only the Java method names change (snake → camel).

```python
from __future__ import annotations
import json
from pathlib import Path

from microclaw import _jpypemm_bootstrap  # noqa: F401  (Strategy B path bootstrap)
import start_mm


class MicroscopeController:
    """Wrapper around jPypeMM's in-process Studio and Core. Windows desktop
    session only; one JVM per process (not restartable).

    `core` and `studio` are the raw JPype CMMCore / MMStudio objects — callers
    use Java camelCase method names directly. Returned Java objects
    (double/String/StrVector/Rectangle) are coerced at the call site with
    float()/int()/str() or the `_str_vector` helper."""

    def __init__(self, config_file: str | None = None):
        studio_java, core_java = start_mm.main(skip_intro=True)
        if config_file:
            core_java.loadSystemConfiguration(config_file)
        self._core = core_java        # raw CMMCore (also used by start_mm.snap_core
        self._studio = studio_java    # and the AcqEngJ Engine / OME-TIFF sink)
        self._positions: list[dict] = []

    @property
    def core(self): return self._core
    @property
    def studio(self): return self._studio

    def is_connected(self) -> bool:
        try:
            self._core.getVersionInfo()
            return True
        except Exception:
            return False

    # ---- Position list (logic identical to today; getters are camelCase) ----
    def _read_mm_position_list(self) -> list[dict]:
        pl = self._studio.positions().getPositionList()
        out = []
        for i in range(int(pl.getNumberOfPositions())):
            msp = pl.getPosition(i)
            entry: dict = {"name": str(msp.getLabel())}
            for j in range(int(msp.size())):
                sp = msp.get(j)
                if int(sp.numAxes) == 2:
                    entry["x_um"] = round(float(sp.x), 3)
                    entry["y_um"] = round(float(sp.y), 3)
                elif int(sp.numAxes) == 1:
                    entry["z_um"] = round(float(sp.x), 3)
            out.append(entry)
        return out

    # import_from_mm_position_list / add_position / get_positions /
    # go_to_position / remove_position / clear_positions /
    # save_position_list / load_position_list:  logic UNCHANGED, but the Java
    # calls inside them go camelCase — e.g. go_to_position uses
    # self._core.setXYPosition(...), setPosition(...), waitForDevice(...).
```

> **Exact Java spellings — record once, here.** MMCore uses irregular casing that
> a mechanical snake→camel would get wrong, so keep the spellings (✓ confirmed
> against `dir(core)`, spike §10.3) in a comment near the class: `setXYPosition`,
> `setRelativeXYPosition`, `getXYStageDevice`, `getXPosition`, `getYPosition`,
> `getROI`, `setROI`, `clearROI`, `getPixelSizeUm`. Keeping this short list in one
> place replaces the old `_OVERRIDES` table.

`port` is removed from the signature (no socket); `core`/`studio` are now the raw
handles (the former `core_raw`/`studio_raw` properties are gone — use `core` /
`studio`). Update the one caller in `__main__.py` (§6) and the integration
fixture (§9).

---

## 4. `acquisition.py` — **NEW** AcqEngJ bridge

This is the bulk of the work. It exposes the same two symbols `tools.py`
imports — `multi_d_acquisition_events` and `Acquisition` — so `tools.py` changes
are minimal.

### 4.1 Lazy JClass handles + Engine

```python
from __future__ import annotations
import threading
from pathlib import Path
from typing import Any

import numpy as np
import jpype
from jpype import JClass, JImplements, JOverride

# JVM is already running (jPypeMM started it). Resolve classes lazily so this
# module imports fine even before the JVM exists (e.g. during unit tests).
def _J(name): return JClass(name)

_ACQENG = "org.micromanager.acqj"


def ensure_engine(core):
    """AcqEngJ Engine is a singleton holding the CMMCore. Create once.
    `core` is the raw JPype CMMCore (ctrl.core)."""
    Engine = _J(f"{_ACQENG}.internal.Engine")
    if Engine.getInstance() is None:
        Engine(core)                # constructor registers the singleton
    return Engine.getInstance()
```

### 4.2 Event generation (pure Python; same shape as today) + Java iterator

`multi_d_acquisition_events` stays a Python dict generator — identical to the
current pycro-manager helper for the subset `tools.py` uses (time / z / channel),
so `run_zstack`, `run_timelapse`, `run_adaptive_acquisition` need no change to
how they build `kwargs`.

```python
def multi_d_acquisition_events(
    *, num_time_points=None, time_interval_s=0.0,
    z_start=None, z_end=None, z_step=None,
    channel_group=None, channels=None, channel_exposures_ms=None,
) -> list[dict]:
    z_positions = None
    if None not in (z_start, z_end, z_step):
        n = int(round((z_end - z_start) / z_step)) + 1
        z_positions = [(i, z_start + i * z_step) for i in range(n)]
    times = list(range(num_time_points)) if num_time_points else [None]
    chans = list(enumerate(channels)) if channels else [(None, None)]

    events: list[dict] = []
    for t in times:
        for ci, ch in chans:
            for zi, z in (z_positions or [(None, None)]):
                ev: dict[str, Any] = {"axes": {}}
                if t is not None:
                    ev["axes"]["time"] = t
                    if time_interval_s:
                        ev["min_start_time_ms"] = int(t * time_interval_s * 1000)
                if ch is not None:
                    ev["axes"]["channel"] = ch
                    ev["channel"] = {"group": channel_group, "config": ch}
                    if channel_exposures_ms:
                        ev["exposure"] = channel_exposures_ms[ci]
                if z is not None:
                    ev["axes"]["z"] = zi
                    ev["z_index"], ev["z_um"] = zi, z
                events.append(ev)
    return events


@JImplements("java.util.Iterator")
class _PyEventIterator:
    """Converts our event dicts into Java AcquisitionEvent objects on demand."""
    def __init__(self, acq_java, events: list[dict]):
        self._acq = acq_java
        self._it = iter(events)
        self._next = self._advance()

    def _advance(self):
        try: return next(self._it)
        except StopIteration: return None

    @JOverride
    def hasNext(self):
        return self._next is not None

    @JOverride
    def next(self):
        d, self._next = self._next, self._advance()
        return _dict_to_event(self._acq, d)


def _dict_to_event(acq_java, d: dict):
    # All setters below confirmed against AcqEngJ via the spike's reflection dump
    # (§10): setZ(Integer, Double), setConfigGroup/Preset(String), setExposure(double),
    # setMinimumStartTime(Long), setTimeIndex(int), setAxisPosition(String, Object).
    Event = _J(f"{_ACQENG}.main.AcquisitionEvent")
    ev = Event(acq_java)
    Integer = jpype.java.lang.Integer
    Double = jpype.java.lang.Double
    Long = jpype.java.lang.Long
    if "z_um" in d:
        ev.setZ(Integer(int(d["z_index"])), Double(float(d["z_um"])))
    if "channel" in d:
        ev.setConfigGroup(d["channel"]["group"])
        ev.setConfigPreset(d["channel"]["config"])
    if "exposure" in d:
        ev.setExposure(float(d["exposure"]))
    if "min_start_time_ms" in d:
        ev.setMinimumStartTime(Long(int(d["min_start_time_ms"])))
    for axis, idx in d.get("axes", {}).items():
        if axis == "time":
            ev.setTimeIndex(int(idx))                 # dedicated setter (confirmed)
        elif axis not in ("z", "channel"):            # z/channel already set above
            ev.setAxisPosition(axis, Integer(int(idx)) if isinstance(idx, int) else idx)
    return ev
```

> AcqEngJ also offers `AcquisitionEvent.fromJSON(JSONObject, AcquisitionAPI)` as an
> alternative to the per-setter construction above (and `toJSON()` for the reverse),
> if we ever prefer to build events from a JSON blob. There is **no**
> `setAcquireImage`; a plain event acquires one image and the engine exposes a
> read-only `shouldAcquireImage()`. End an acquisition with `finish()` or the
> static `createAcquisitionSequenceEndEvent(acq)`.

### 4.3 Hook + image-processor shims (translate to `hooks.py`'s contract)

```python
@JImplements(f"{_ACQENG}.api.AcquisitionHook")
class _PostHardwareHookShim:
    """AFTER_HARDWARE_HOOK. Wraps a Microclaw hook's post_hardware_hook_fn,
    which receives an event *dict* and returns it (or None to drop)."""
    def __init__(self, py_fn):
        self._py_fn = py_fn

    @JOverride
    def run(self, event):
        # AutofocusHook only reads event["axes"] and moves the focus device;
        # it never mutates the event object, so we pass a translated dict and
        # return the original Java event (or None to drop the frame).
        result = self._py_fn(_event_to_dict(event))
        return None if result is None else event

    @JOverride
    def close(self):
        pass


@JImplements(f"{_ACQENG}.api.TaggedImageProcessor")
class _ImageProcessorShim:
    """Runs on its own JVM thread. Pulls TaggedImage off `source`, runs the
    Microclaw image_process_fn(image, metadata, event_queue), and forwards the
    image to `sink` (or drops it when the fn returns None)."""
    def __init__(self, py_fn):
        self._py_fn = py_fn

    def _start(self, acq, source, sink):
        self._acq, self._source, self._sink = acq, source, sink
        threading.Thread(target=self._loop, name="microclaw-imgproc",
                         daemon=True).start()

    @JOverride
    def setAcqAndQueues(self, acq, source, sink):
        self._start(acq, source, sink)

    @JOverride
    def setAcqAndDequeues(self, acq, source, sink):
        # Deprecated variant — but JPype's @JImplements requires EVERY abstract
        # interface method to be overridden, and TaggedImageProcessor declares
        # both (confirmed in the spike). LinkedBlockingDeque is a BlockingQueue,
        # so the same _loop works.
        self._start(acq, source, sink)

    def _loop(self):
        # Attach this Python-spawned thread to the JVM before touching Java objs.
        if not jpype.isThreadAttachedToJVM():
            jpype.attachThreadToJVM()
        while True:
            tagged = self._source.take()           # blocking
            if tagged is None or tagged.tags is None:
                self._sink.put(tagged)             # forward end-of-stream sentinel
                break
            image = _tagged_to_numpy(tagged)
            meta = _tags_to_dict(tagged.tags)
            result = self._py_fn(image, meta, None)   # event_queue=None — see §7
            if result is not None:
                self._sink.put(tagged)             # storage sees the original image


def _event_to_dict(event) -> dict:
    # getAxisPositions() confirmed (spike §10): returns a java.util.HashMap
    # {axis_name -> index}. JPype lets us iterate its entrySet().
    axes = {}
    jmap = event.getAxisPositions()
    if jmap is not None:
        for entry in jmap.entrySet():
            axes[str(entry.getKey())] = entry.getValue()
    return {"axes": axes}


def _tagged_to_numpy(tagged) -> np.ndarray:
    tags = tagged.tags
    w, h = int(tags.getInt("Width")), int(tags.getInt("Height"))
    arr = np.asarray(tagged.pix[:])    # JPype copies the Java primitive array
    # Java has no unsigned types: a 16-bit camera buffer is short[] -> int16, an
    # 8-bit one is byte[] -> int8. Reinterpret the bits as unsigned (confirmed in
    # the spike: pix was short[], arr came back int16). VIEW, not astype.
    if arr.dtype == np.int16:
        arr = arr.view(np.uint16)
    elif arr.dtype == np.int8:
        arr = arr.view(np.uint8)
    return arr.reshape(h, w)


def _tags_to_dict(tags) -> dict:
    # Hooks only read metadata.get("time")/("position_index"). Key names CONFIRMED
    # from the spike's tags.toString() dump: the per-image tags carry FrameIndex,
    # PositionIndex, SliceIndex, ChannelIndex (ints), plus a nested
    # Axes:{"time":0}. mmcorej JSONObject exposes has()/getInt() (NOT keySet()).
    def _opt_int(key):
        return int(tags.getInt(key)) if tags.has(key) else None
    return {
        "time": _opt_int("FrameIndex"),
        "position_index": _opt_int("PositionIndex"),
        "slice_index": _opt_int("SliceIndex"),
        "channel_index": _opt_int("ChannelIndex"),
    }
```

### 4.4 DataSink — save on the Java side (NDTiff *or* OME-TIFF, user-selectable)

Saving is handled entirely by MM's Java data layer; Python only wires the objects
together. A `storage_format` argument ("ometiff" | "ndtiff", **default "ometiff"**)
picks the backend. Both formats go through a MM `Datastore` (`createMultipageTIFFDatastore`
/ `createNDTIFFDatastore` — the standalone `NDTiffAndRAMStorage` is **not on this
MM build's classpath**, spike §10, so we use `DataManager` and avoid that jar).

#### Why not the built-in `AcqEngJMDADataSink`

MM does ship `org.micromanager.acquisition.internal.acqengjcompat.AcqEngJMDADataSink`,
but the spike's reflection shows its only constructor is
`AcqEngJMDADataSink(EventManager, AcqEngJAdapter)` — it needs MM's **internal
`AcqEngJAdapter`**, the object MM builds to run *its own* MDA. That's tightly
coupled to MM's MDA flow and contradictory to construct when we're driving AcqEngJ
ourselves, so we **do not** use it. (It exposes `setDatastore(Datastore)` /
`setPipeline(Pipeline)` if a future approach ever reuses MM's adapter wholesale.)

#### Sink: hand-rolled `_DatastoreSink` (validated — both formats)

The spike confirmed this writes both NDTiff **and** OME-TIFF (2-event acquisitions,
2 images each). Two requirements the spike pinned down:

1. **`setSummaryMetadata(...)` is mandatory before `putImage`** — the
   multipage-TIFF storage throws a `NullPointerException` on the first `putImage`
   if no summary metadata was set. Build a minimal one via
   `studio.data().summaryMetadataBuilder().build()`. (NDTiff tolerated its absence;
   OME-TIFF does not — this was the only real OME-TIFF blocker.) It must be a
   `SummaryMetadata` object, **not** the AcqEngJ JSONObject `initialize` receives.
2. **Each image needs distinct `Coords`** — `convertTaggedImage(tagged)` (1-arg)
   builds an `Image` *with* tag-derived metadata; `copyAtCoords(coords)` relocates
   it so the 2nd `putImage` doesn't collide (`DatastoreRewriteException`).

`make_sink` creates the `Datastore` **once** and passes it in — do not create it
inside the sink (JPype can instantiate the proxy more than once, and
`createMultipageTIFFDatastore` refuses an existing path):

```python
def make_sink(ctrl, directory, name, storage_format="ometiff"):
    studio = ctrl.studio
    path = str(Path(directory) / name)
    datastore = (studio.data().createMultipageTIFFDatastore(path, True, False)
                 if storage_format == "ometiff"
                 else studio.data().createNDTIFFDatastore(path))
    return _DatastoreSink(studio, datastore)


@JImplements(f"{_ACQENG}.api.AcqEngJDataSink")
class _DatastoreSink:
    """Forwards each image into a MM Datastore (Java Storage does the writing).
    The datastore is created once in make_sink and passed in."""
    def __init__(self, studio, datastore):
        self._studio, self._ds = studio, datastore
        self._finished = False
        self._n = 0

    @JOverride
    def initialize(self, acq, summary_metadata):
        # REQUIRED: multipage-TIFF storage NPEs on the first putImage without a
        # SummaryMetadata. Build a minimal one (NOT the AcqEngJ JSONObject arg).
        dm = self._studio.data()
        self._ds.setSummaryMetadata(dm.summaryMetadataBuilder().build())

    @JOverride
    def putImage(self, tagged):
        # 1-arg convertTaggedImage -> Image with tag-derived metadata; copyAtCoords
        # relocates it to a distinct time index so the 2nd putImage doesn't collide
        # (DatastoreRewriteException). Both confirmed in the spike.
        dm = self._studio.data()
        coords = dm.coordsBuilder().t(self._n).build()
        self._ds.putImage(dm.convertTaggedImage(tagged).copyAtCoords(coords))
        self._n += 1
        return None

    @JOverride
    def anythingAcquired(self):
        return self._n > 0

    @JOverride
    def isFinished(self):
        return self._finished

    @JOverride
    def finish(self):
        self._ds.freeze()                          # flush + close (Java-side)
        self._finished = True
```

### 4.5 The `Acquisition` facade

```python
class Acquisition:
    """Same surface tools.py already uses: context manager + acquire(events) +
    _dataset_disk_location. Internally drives AcqEngJ."""

    _HOOK_AFTER_HARDWARE = 3   # AcquisitionAPI.AFTER_HARDWARE_HOOK

    def __init__(self, *, directory, name, show_display=True, ctrl=None,
                 storage_format="ometiff",
                 post_hardware_hook_fn=None, image_process_fn=None):
        if ctrl is None:
            raise ValueError("Acquisition requires ctrl= for the AcqEngJ engine.")
        ensure_engine(ctrl.core)
        self._dir, self._name = directory, name
        self._sink = make_sink(ctrl, directory, name, storage_format)  # Java-side
        self._acq = _J(f"{_ACQENG}.main.Acquisition")(self._sink)
        if post_hardware_hook_fn is not None:
            self._acq.addHook(_PostHardwareHookShim(post_hardware_hook_fn),
                              self._HOOK_AFTER_HARDWARE)
        if image_process_fn is not None:
            self._acq.addImageProcessor(_ImageProcessorShim(image_process_fn))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._acq.finish()
        self._acq.waitForCompletion()
        return False

    def acquire(self, events: list[dict]):
        future = self._acq.submitEventIterator(_PyEventIterator(self._acq, events))
        return future

    @property
    def _dataset_disk_location(self):
        # Mirrors the attribute tools.py reads after each acquisition. Both the
        # NDTiff and OME-TIFF datastores write under this directory.
        return str(Path(self._dir) / self._name)
```

---

## 5. `tools.py` — minimal edits

1. Swap the import:

```python
# was: from pycromanager import Acquisition, multi_d_acquisition_events
from microclaw.acquisition import Acquisition, multi_d_acquisition_events
```

2. Add `ctrl=ctrl` to the three `Acquisition(...)` constructions (`run_zstack`,
   `run_timelapse`, `run_adaptive_acquisition`):

```python
with Acquisition(directory=save_dir, name=name, show_display=True, ctrl=ctrl) as acq:
    acq.acquire(events)
actual_path = acq._dataset_disk_location or str(Path(save_dir) / name)
```

3. **Convert all ~55 `ctrl.core.*` / `ctrl.studio.live().*` calls to camelCase**
   (`get_x_position` → `getXPosition`, `set_exposure` → `setExposure`,
   `studio.live().set_live_mode_on` → `setLiveModeOn`, etc.) — there is no
   adapter. This is the bulk of the diff but mechanical. `_str_vector` still works
   (Java `StrVector` exposes `size()`/`get(i)`); the spelling gotchas are listed
   in §3.

4. **Storage-format passthrough.** Add an optional `storage_format`
   ("ometiff" | "ndtiff", default `"ometiff"`) arg to `run_zstack` /
   `run_timelapse` / `run_adaptive_acquisition` and forward it to
   `Acquisition(..., storage_format=...)`.

5. **Remove `export_dataset_as_tiff` entirely.** It existed only to convert
   pycro-manager NDTiff datasets into a portable TIFF for external analysis. With
   OME-TIFF as the default save format — already Bio-Formats-compatible and
   written Java-side — there is nothing to convert: the saved `.ome.tif` *is* the
   portable output. Remove:
   - the function (`tools.py:381`) and its `from ndstorage import Dataset` /
     `import tifffile` imports;
   - its `TOOL_REGISTRY` entry (`tools.py:1095`);
   - its schema (`tools_schema.py:273`).

   And update the prompt/doc references that point users at it:
   - `agent.py:27` and `agent.py:57` — replace "export with export_dataset_as_tiff"
     with "datasets are saved as OME-TIFF (Bio-Formats-readable) by default."
   - `smlm_docs.py:16` and `smlm_docs.py:202` — same: drop the export step, point
     at the saved `.ome.tif`.

---

## 6. `__main__.py` — startup messaging + config flag

- Drop the port arg; add `--config-file`.
- Replace the ZMQ error text.

```python
parser.add_argument("--config-file", default=None,
                    help="MM hardware config to load after launch.")
# (remove --port)

print("Launching embedded Micro-Manager (JVM)...")
ctrl = MicroscopeController(config_file=args.config_file)
if not ctrl.is_connected():
    sys.exit(
        "Could not launch the embedded Micro-Manager JVM. Check that you are on "
        "Windows in a desktop session, MM 2.0 (64-bit) is installed, and the "
        "VC++ 2015-2022 x64 redistributable is present."
    )
```

The history-writing loop and agent calls are unchanged.

---

## 7. Files whose **logic** is unchanged (only Java calls go camelCase)

These keep their structure and signatures; their internal `ctrl.core.*` calls are
rewritten to camelCase like every other call site (§5.3).

- **`hooks.py`** — hook classes keep their pycro-manager-style signatures
  (`post_hardware_hook_fn(event)`, `image_process_fn(image, metadata,
  event_queue)`); the §4.3 shims feed them the right shapes. Their bodies' device
  calls change spelling only (e.g. `self.ctrl.core.get_position()` →
  `getPosition()`, `set_exposure` → `setExposure`).
  - ⚠ `event_queue` is passed as `None`. Current hooks ignore it. If a
    Claude-/user-authored hook (via `hook_manager.py`) needs to inject events,
    document this gap or expose AcqEngJ's event queue later.
  - ⚠ `PositionFilterHook` returns `None` to drop a frame; the processor shim
    drops that one image. Engine-level "skip the rest of this position" requires
    pruning via a `BEFORE_HARDWARE_HOOK` that inspects `position_index` — add
    only if that behaviour is needed.
- **`autofocus.py`** — pure `ctrl.core.*` + `snap_to_numpy`; logic identical, the
  `ctrl.core.*` calls go camelCase (`getFocusDevice`, `setPosition`,
  `waitForDevice`).

## 7b. `config.py` and `errors.py`

- **`config.py`:** **delete `launch_headless` entirely** (headless is dropped).
  If anything imports it, remove those imports. If `load_safety_config` is the
  only other content, keep just that.
- **`errors.py`:** update the docstring — replace "ZMQ timeout / ZMQ server
  disabled" with JPype/JVM failure modes (`jpype.JException` for Java-side
  errors; `RuntimeError`/startup failure if the JVM/MM can't launch).

## 7c. `hook_docs.py` — **needs updating**

`hook_docs.py` (`HOOK_REFERENCE`, the prompt the agent reads when writing hooks)
will partly *not* work as written, because it describes pycro-manager's full hook
surface while the AcqEngJ bridge (§4.5) only wires two hook points. The precoded
hooks in `hooks.py` are unaffected (they use only `post_hardware_hook_fn` and
`image_process_fn`, both supported), but the *reference doc* must be corrected so
Claude doesn't generate hooks against unsupported features:

- **Advertised kwargs → keep only the wired two.** The facade wires
  `post_hardware_hook_fn` (AFTER_HARDWARE_HOOK) and `image_process_fn` (image
  processor). `pre_hardware_hook_fn`, `event_generation_hook_fn`, and
  `image_saved_hook_fn` are **not** wired. Either remove them from the doc, or
  wire them first (AcqEngJ has `BEFORE_HARDWARE_HOOK` for pre-hardware; an
  image-saved notification would need a `DataSink` callback). Recommend trimming
  the doc to the supported two until the rest are implemented.
- **`event_queue` is unsupported.** The image-processor shim passes
  `event_queue=None` (§4.3). Remove the "push new events" / "end early
  (`event_queue.put(None)`)" guidance, or mark it as a future capability.
- **`image_process_fn` returning `None` drops only the current frame**, not the
  whole position (§7 caveat). Fix the doc's "drop all remaining events for that
  position" claim.
- **"written to the NDTiff dataset on disk"** → now OME-TIFF by default; reword.
- **Event-dict fields.** `_event_to_dict` (§4.3) currently populates only
  `event["axes"]`; the doc promises `z`/`x`/`y`/`channel`/`exposure`/
  `min_start_time`. Either **enrich `_event_to_dict`** to read those off the Java
  `AcquisitionEvent` (preferred — keeps hooks capable and the doc accurate) or
  trim the documented dict to just `axes`.
- **Unaffected:** the `HookBase` pattern, `ctrl`/`guard` injection, `_write_log`,
  and the "choosing the right hook type" rows for `post_hardware_hook_fn` /
  `image_process_fn` all stay valid.

## 8. `image_analysis.py` — `snap_to_numpy`

```python
def snap_to_numpy(ctrl) -> np.ndarray:
    """Snap and return a NumPy array via jPypeMM's helper."""
    import start_mm
    return np.asarray(start_mm.snap_core(ctrl.core))
```

Removes the `get_tagged_image()` / `.pix` / hard-coded `uint16` path;
`compute_stats` / `make_thumbnail` unchanged.

---

## 9. Tests

- **Rename the conftest mocks to camelCase (correctness-sensitive).** The
  `MagicMock` core/studio fixtures configure return values on snake_case names
  (`core.get_x_position.return_value = 0.0`, `get_available_configs`,
  `get_loaded_devices`, …). With no adapter, production code now calls
  `core.getXPosition()` etc., so those configured values are silently ignored
  (the mock returns a fresh `Mock`) unless every fixture attribute is renamed to
  camelCase. Treat this as a deliberate pass, not a blind find-replace: audit each
  `*.return_value`/`assert_called` against the camelCase name the code now uses.
- **conftest `headless_mm` fixture:** remove the port; build
  `MicroscopeController(config_file=...)`. Gate on a Windows desktop session
  (e.g. skip unless `MM_DESKTOP=1`), since it can't run on the macOS dev box or
  in CI.
- **Acquisition tests:** repoint any patch of
  `pycromanager.Acquisition`/`multi_d_acquisition_events` to
  `microclaw.acquisition`. Grep: `grep -rn "pycromanager\|Acquisition\|multi_d_acquisition" tests/`.
- **New unit tests (no JVM needed):**
  - `multi_d_acquisition_events` output shape for z-only, time-only, channel.
  - `Acquisition(...).acquire(...)` against a fake `ctrl`/engine: assert hooks
    are added with the right type and the event iterator yields the right count.
    (Mock `JClass` so the engine objects are stand-ins.)
- **Live tests (Windows desktop only):** snap, exposure get/set, a small
  z-stack, and an adaptive acquisition exercising one hook of each kind.

---

## 10. Spike / verification checklist (do first, on Windows)

Run before writing Microclaw code; each line de-risks a specific assumption.
**Status: SPIKE COMPLETE — 12/12 PASS on a live Windows MM 2.0 install (demo
config). Every assumption the port depends on is confirmed, including both save
formats.**
- ✓ Startup, config load, snap (→ 512×512 **uint16**), AcqEngJ classpath,
  `Engine(core)`, proxy build, event introspection.
- ✓ **Full AcqEngJ bridge validated end-to-end** (`6+7`): `Engine → Acquisition(sink)
  → addHook → addImageProcessor → submitEventIterator → finish/waitForCompletion`;
  hook fired twice, processor received 2 images on its JVM thread, sink got 2
  `putImage` calls. A plain event captures — no `setAcquireImage` needed.
- ✓ All AcqEngJ signatures confirmed (folded into §4: `getAxisPositions()`→HashMap,
  `setTimeIndex(int)`, both `TaggedImageProcessor` methods, no `setAcquireImage`).
- ✓ **Pixel dtype** confirmed — `short[]`→`uint16` reinterpret (§4.3).
- ✓ **Both save formats write** — NDTiff (5a) **and** OME-TIFF (5b-i/ii/iii) each
  wrote both images. The OME-TIFF blocker was a missing **`setSummaryMetadata`**
  (multipage-TIFF NPEs without it); `_DatastoreSink` now sets it in `initialize()`
  and uses `convertTaggedImage(tagged).copyAtCoords(coords)` (§4.4). `Image` API
  confirmed: `copyAtCoords(Coords)`, `copyWith(Coords, Metadata)`.
- ✓ **`TaggedImage` tag keys captured** from `tags.toString()`: time→`FrameIndex`,
  position→`PositionIndex`, also `SliceIndex`/`ChannelIndex` and nested
  `Axes:{...}`. `_tags_to_dict` finalized (§4.3).
- **`AcqEngJMDADataSink` is NOT used** — its ctor needs MM's internal
  `AcqEngJAdapter` (§4.4); the hand-rolled `_DatastoreSink` is the sink.
- **`NDTiffAndRAMStorage` is not on the classpath** (§1.5); NDTiff routes through
  `studio.data().createNDTIFFDatastore(...)` instead (§4.4).

No open spike items remain — next is implementing the port (§12).

1. `studio, core = start_mm.main(skip_intro=True)` succeeds in a desktop session.
   — **✓ confirmed.**
2. `np.asarray(start_mm.snap_core(core))` returns a correctly shaped/typed array.
   — **✓ confirmed.**
3. `dir(core)` — **✓ confirmed** against a live Windows MM 2.0 install. Every
   method name the port relies on is present and correctly cased, so the §3
   gotcha list stands exactly as written: `getXPosition`, `getYPosition`,
   `getXYStageDevice`, `setXYPosition`, `setRelativeXYPosition`, `getROI`,
   `setROI`, `clearROI`, `getPixelSizeUm`, plus `getExposure`/`setExposure`,
   `getPosition`/`setPosition`, `getCameraDevice`, `getFocusDevice`,
   `getImageWidth`/`getImageHeight`, `getAvailableConfigs`, `getLoadedDevices`,
   `getDevicePropertyNames`, `getProperty`/`setProperty`, `setConfig`, `snapImage`,
   `getVersionInfo`, and `waitForDevice`/`waitForSystem`/`waitForConfig`.
   - Aside: `getXYPosition` / `getXYStagePosition` also exist and return both axes
     at once, but they use C++ output-reference args that are awkward through
     JPype — keep the separate `getXPosition`/`getYPosition` getters the current
     code already uses.
   - Full `dir(core)` output is archived outside the doc (it is long); the names
     above are the only ones the port touches.
4. `JClass("org.micromanager.acqj.main.Acquisition")` resolves (classpath has
   the jars — risk if not, §1.5). — **✓ confirmed**, returns the Java class
   without error.
5. `Engine(core)` + a trivial 2-event acquisition writes a dataset via the MM
   `Datastore` sink (`_DatastoreSink`) for **both** formats:
   `studio.data().createMultipageTIFFDatastore(...)` (OME-TIFF) and
   `createNDTIFFDatastore(...)` (NDTiff), fed via `convertTaggedImage` +
   `putImage`. (The standalone `NDTiffAndRAMStorage` is absent — §1.5.) Also probe
   for a built-in AcqEngJ→Datastore sink (if MM ships one, `_DatastoreSink` is
   unnecessary).
6. A `@JImplements("…AcquisitionHook")` Python class is accepted by `addHook`,
   and its `run()` fires (confirms JPype Python→Java interface + thread attach).
7. A `TaggedImageProcessor` shim receives images on its thread and forwards them.
8. Confirm `AcquisitionEvent` getters used in `_event_to_dict`/`_tags_to_dict`
   (`getAxisPositions`, tag keys) match the installed AcqEngJ version.

### Running the spike (`spike.py`)

Steps 5–8 are automated by `spike.py` at the repo root. Run it in a **Windows
desktop session** with Micro-Manager 2.0 installed:

```bat
python spike.py --config "C:\Program Files\Micro-Manager-2.0\MMConfig_demo.cfg"
```

- `--config` is **required in practice** — without it MM starts with no camera,
  and the hardened script will **skip** steps 2/5/6/7 (the first run did exactly
  this). The demo config guarantees a camera + Z-stage so the 2-event
  acquisitions actually capture.
- Other flags: `--out <file.txt>` (default `spike_output_<timestamp>.txt`),
  `--save-dir <dir>` (default `./spike_data_<timestamp>` — timestamped so MM's
  Datastore factories don't collide with a prior run's directory),
  `--jpypemm-path <dir>` (else `$JPYPEMM_PATH`, then `./third_party/jPypeMM`).

The script is deliberately defensive: every check is isolated (one failure does
not abort the run), it **reflects and dumps the real Java signatures**
(constructors, methods, hook constants, `TaggedImage` tag keys) rather than
assuming them, and it tees all output to the `.txt` (flushed per line, so the log
is complete even though the MM GUI usually keeps the process alive at the end —
close MM or Ctrl-C to exit). After the first run it was hardened to: **skip** the
image steps with a loud warning when no camera is loaded; wrap the proxy-class
build in the same try/log (so a `@JImplements` failure lands in the `.txt`, not
just the console); and run `waitForCompletion()` under a **watchdog thread that
aborts on a 60 s timeout** so a stalled engine can't freeze the run. It
re-verifies steps 1–4, then exercises:

- **5a** NDTiff via a MM `Datastore` (`createNDTIFFDatastore`) through
  `_DatastoreSink` with per-image `Coords` — ✓ wrote both images;
- **5b** OME-TIFF via a MM `Datastore` (`createMultipageTIFFDatastore` +
  `convertTaggedImage` + `putImage`) — same path, validated once the datastore is
  created in `initialize()` (not the proxy `__init__`);
- **6 + 7 + 8** in one run — a Python `@JImplements` `AcquisitionHook`,
  `TaggedImageProcessor`, and `AcqEngJDataSink` (hook fires, processor receives
  images on its JVM thread, `putImage` is called); the live
  `AcquisitionEvent`/`TaggedImage` APIs are dumped, including `tags.toString()`.

The spike has now confirmed every dependency the port relies on — bridge,
classpath, signatures, dtype, both storage formats, and the tag keys. Remaining
work is implementing the port itself (§12), not further spiking.

---

## 11. Risks (Option-B specific)

1. **AcqEngJ jars not on classpath (resolved on dev install; low).** Was the main
   blocker; spike §10.4 confirmed `Acquisition` resolves on the dev MM build. Still
   verify on each target install; mitigation if missing: drop jars into MM
   `plugins/`.
2. **JPype Python→Java interfaces & thread attachment (medium).** `@JImplements`
   classes called from AcqEngJ's hardware/processor threads must attach to the
   JVM (handled in `_loop`); long Python hook bodies hold the GIL and can stall
   the engine. Keep hooks short; test under load. **Also: `@JImplements` requires
   *every* abstract method of the interface to be overridden** — `TaggedImageProcessor`
   has both `setAcqAndQueues` and the deprecated `setAcqAndDequeues`, so the shim
   must implement both (confirmed via the spike; see §4.3).
3. **Exact AcqEngJ API drift (low — fully confirmed on this build).** All
   class/method signatures **and** the `TaggedImage.tags` key names
   (`FrameIndex`/`PositionIndex`/`SliceIndex`/`ChannelIndex`) are ✓ confirmed
   against the build's reflection + `tags.toString()` dumps (§10). Still
   version-sensitive across MM builds — keep every Java name isolated in
   `_event_to_dict`/`_tags_to_dict`/`_dict_to_event` so any future fix stays
   one-line.
4. **jPypeMM packaging / latest-from-git (medium).** No build backend → `pip
   install git+…` may fail; Strategy B (clone-on-path) is the reliable "latest"
   path. jPypeMM also drags heavy deps (torch/cellpose) under Strategy A.
5. **Python version bump (low/medium).** jPypeMM needs `>=3.11,<3.14`; verify
   Microclaw's other deps and the user's interpreter satisfy this.
6. **Windows-only is a jPypeMM *implementation* limit, not a fundamental one
   (accepted for now).** You're right that the stack is cross-platform in
   principle — the JVM, Micro-Manager, AcqEngJ, and pycro-manager all run on
   macOS/Linux. The constraint is purely that jPypeMM's *current code* hardcodes
   Windows specifics: it points JPype at `jre\bin\server\jvm.dll`, auto-detects MM
   under `C:\Program Files\`, and registers Windows device libraries
   (`MMCoreJ_wrap.dll` + ~341 `.dll`s) plus the VC++ redistributable. On
   macOS/Linux those would become `libjvm.dylib`/`libjvm.so`, a different MM
   install path, and `.dylib`/`.so` device libraries. Lifting the limit means
   generalizing those paths upstream in jPypeMM (or in a fork) — feasible but out
   of scope here. (Separately, one JVM per process / not restartable after a crash
   *is* inherent to the in-process JNI model.)
7. **`event_queue`=None and `PositionFilterHook` semantics (low/medium).** See
   §7 caveats; address only if those hook behaviours are required.

---

## 12. Implementation order

1. **Spike (§10)** on Windows — gate the whole effort on steps 4–7.
2. `pyproject.toml` deps + `_jpypemm_bootstrap.py` (Strategy B) + Python bump.
3. `controller.py` — raw `core`/`studio` (no adapter) + the gotcha-spelling
   comment (from spike step 3).
4. `image_analysis.snap_to_numpy`.
5. `acquisition.py` — events + iterator + shims + sink + facade; unit tests with
   mocked `JClass`.
6. `tools.py` — convert all `ctrl.core.*`/`ctrl.studio.*` calls to camelCase (§5.3),
   import swap + `ctrl=ctrl` + `storage_format` passthrough; remove
   `export_dataset_as_tiff` and its schema/registry/prompt references (§5).
   Convert the camelCase calls in `autofocus.py` and `hooks.py` too (§7).
7. `__main__.py`, `config.py` (delete headless), `errors.py`.
8. Test suite: rename conftest mocks to camelCase (§9) + repoint acquisition
   patches + `headless_mm` fixture rework.
9. Live validation on Windows; update README + project memory.

---

## 13. Open questions

- **DataSink — resolved & validated (§4.4):** saving is Java-side via a MM
  `Datastore`; default **OME-TIFF** (`createMultipageTIFFDatastore`), with NDTiff
  (`createNDTIFFDatastore`) per acquisition via `storage_format`.
  `ndstorage`/`tifffile` and `export_dataset_as_tiff` are dropped (§1.4, §5). The
  hand-rolled **`_DatastoreSink`** is the sink (spike wrote both NDTiff images);
  the built-in `AcqEngJMDADataSink` is **not** used — its ctor needs MM's internal
  `AcqEngJAdapter`.
- **Install strategy — see §1.2/§1.3/§1.3a:** Strategy B (clone-on-path) is the
  default; jPypeMM fetch/update can be automated, but the MM 2.0 + VC++ install
  stays a documented manual prerequisite.
- **Python interpreter — resolved:** bump Microclaw to `>=3.11,<3.14` (already
  reflected in the §1.2 `pyproject.toml`).
- **`hook_docs.py` — resolved (§7c):** the reference must be trimmed to the two
  wired hook points (`post_hardware_hook_fn`, `image_process_fn`), with
  `event_queue` and the unsupported hook kinds removed until implemented.