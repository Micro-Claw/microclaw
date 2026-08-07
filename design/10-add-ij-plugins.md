# Using ImageJ (`ij()`) plugins inside microclaw hooks

> Companion to `design/09-add-micromanager-plugins.md`. Read 09 first — this doc
> reuses its `PluginAccess` seam, its safety gates (`check_plugin` /
> `check_plugin_motion`), and its "only scalars cross the bridge" rule, and only
> describes where ImageJ *differs* from the MM-plugin case.

> **Spike-validated (2026-07-02, MMCore 12.5.0, #2401 present).** Run
> `design/ij-plugins-spike.py` on the lab machine confirmed the central thesis but
> corrected two mechanisms below (static IJ1 dispatch and the IJ2 gateway). See
> [Spike results](#spike-results) before implementing — the code stubs are updated
> to match what actually worked.

## TL;DR

Yes, ImageJ is reachable from microclaw hooks — but **not** the way the question
frames it, and the mechanics differ from MM plugins in three load-bearing ways:

1. **`studio.get_data_manager().ij()` is not a plugin runner.** ✅ *Confirmed by
   spike:* it returns `org.micromanager.data.internal.DefaultImageJConverter`, whose
   methods are `create_processor`, `create_image`, `create_processor_from_component`,
   `create_blank_processor` — a *pixel-format bridge* between MM `Image`/`Coords` and
   ImageJ `ImageProcessor`. It converts images; it does not run plugins. It is
   genuinely useful here (the sanctioned way to hand an MM image to ImageJ, and the
   round-trip works — spike check 5), but it answers a different question than "how
   do I run an ImageJ plugin."
2. **There are two ImageJ plugin worlds**, reached differently:
   - **ImageJ1 (IJ1)** — the classic ImageJ that MMStudio embeds. The class `ij.IJ`
     *resolves* over the bridge on any build (spike check 3), and IJ1 plugin
     resolution happens Java-side through ImageJ's own PluginClassLoader, so — unlike
     MM plugins — it does **not** depend on #2401. ~~⚠️ **But** *the spike disproved the
     obvious call path:* `JavaClass("ij.IJ").run_macro(...)` /
     `JavaClass("ij.measure.ResultsTable").get_results_table()` fail — pyjavaz hands
     back a bare `java.lang.Class`, so **static IJ1 methods are not directly callable
     over ZMQ**.~~ **RETRACTED 2026-08-07 — see the amendment note below.** The
     instance-based path (converter → `ImageProcessor` → `get_statistics()`, or an
     instantiated `PlugInFilter`) is still fine and still recommended for
     measurement; it is simply no longer the *only* path.

     > **Amendment, 2026-08-07 (design/42 block 42a).** The struck sentence is
     > wrong, and the check-4 failure it rests on was misattributed. The error —
     > `AttributeError: 'java_lang_Class' object has no attribute
     > 'get_results_table'` — is the pyjavaz **static-class cache collision** that
     > design/12 diagnosed ten days after this spike ran: every static `JavaClass`
     > shadow is cached under the single key `"java.lang.Class"`, so the first
     > classpath wrapped in a process wins and every later one silently inherits
     > its surface. Check 4 wrapped `ij.IJ` (check 3) *before*
     > `ij.measure.ResultsTable`, so `ResultsTable` came back carrying `ij.IJ`'s
     > methods. This spike diagnosed a dead end that was really a cache bug.
     >
     > `controller._new_static_java_class(port, classpath)` evicts the colliding
     > key first, and **static IJ1 dispatch works through it.** Measured on M5,
     > 2026-08-07, MMCore 12.5.0 / ImageJ 1.53c (`design/42-ij-open-spike.py`,
     > evidence `out42a.txt`): `ij.IJ` and `ij.WindowManager` were each wrapped in
     > both orders behind a decoy static wrap, and each exposed its own 138 / 38
     > methods with none of the other's. `IJ.open()` then opened a real window
     > whose dimensions matched the file read Python-side.
     >
     > The **control** is why this is a measurement and not an assumption: the
     > same wrap with the eviction bypassed reproduced the collision exactly —
     > `ij.WindowManager` came back with `java.lang.System`'s 40 methods and none
     > of its own. The bug is real, still present in this pyjavaz, and suppressed
     > by the helper.
     >
     > What has **not** changed: the macro engine is still unnecessary and still
     > not recommended (point 3's global-state argument stands on its own), and
     > `net.imagej` (Approach B) is still not bundled. What changes is only that
     > "static IJ1 is unreachable" may no longer be cited as a reason for
     > anything. `_probe_imagej_dir` had in fact been calling `ij.IJ.getDirectory`
     > in production on every rig for months while this document said it could
     > not work.
   - **ImageJ2 / SciJava (IJ2)** — `net.imagej.ImageJ` gateway + `CommandService`.
     ⚠️ *Spike:* the SciJava framework classes (`org.scijava.Context`,
     `CommandService`, `ModuleService`) **do** resolve, but the `net.imagej.ImageJ`
     gateway is **not bundled** (class-not-found, checks 6a/6b), and MM exposes **no
     Context accessor** over the bridge (check 6c). Approach B stays deferred, now for
     a concrete reason, not a guess.
3. **IJ1 is a GUI singleton with thread + global-state constraints** that MM
   plugins mostly don't have. `IJ.run(...)` operates on the *current* image
   (`WindowManager.getCurrentImage()`) and pushes results to global singletons
   (`ResultsTable`, ROI Manager) on the EDT. Combined with the static-dispatch gap
   (point 2), this is why the recommended path avoids the IJ1 macro engine entirely
   and works through instance objects.

**Recommendation (revised post-spike):** the proven, lowest-risk capability is the
**converter** (`data().ij().create_processor`) plus instance-method analysis. Ship
that as a one-shot **analysis tool** (`run_ij_analysis`, Approach C-shaped) first,
operating on a snapped/converted image; it needs no static dispatch, no #2401, and
no macro engine. Treat the per-frame `IJPluginHook` as a second step gated on a
follow-up spike for a working static/macro path, and IJ2/`CommandService` as
deferred to the jPype backend. Defer full-image Java processing to an MM **processor
pipeline** (per 09's composition rule), not a hook.

---

## Spike results

Ran `design/ij-plugins-spike.py --snap` against the Windows lab machine, MMCore 12.5.0,
an MM build containing #2401. Summary (`PASS=6 FAIL=2 SKIP=1`):

| # | Check | Result | What it tells us |
|---|-------|--------|------------------|
| 0 | connect Core+Studio | **PASS** | MMCore 12.5.0 |
| 1 | `SharedPluginClassLoader` resolves | **PASS** | #2401 is present on this build |
| 2 | `data().ij()` → converter | **PASS** | class is `DefaultImageJConverter`; methods `create_processor`, `create_image`, `create_processor_from_component`, `create_blank_processor`. **Confirms the doc's premise: `ij()` is a converter, not a plugin host.** |
| 3 | `ij.IJ` resolves | **PASS** | IJ1 core is on MM's classpath (no #2401 needed to *reference* it) |
| 4 | IJ1 macro → `ResultsTable` scalar | **FAIL** | `AttributeError: 'java_lang_Class' has no attribute 'get_results_table'`. **Static IJ1 methods aren't callable via `JavaClass(...).method()` over pyjavaz** — it returns a bare `java.lang.Class`. The macro-engine path in the original stub does not work as written. |
| 5 | converter round-trip (`--snap`) | **PASS** | `snap → convert_tagged_image → ij().create_processor` returned a real `ImageProcessor` 512×512. **The converter works end-to-end**, so instance-based analysis on a converted image is viable. |
| 6a | IJ2/SciJava classes resolve | **PASS (partial)** | `org.scijava.Context`, `CommandService`, `ModuleService` resolve; **`net.imagej.ImageJ` does not** (not bundled). |
| 6b | construct `net.imagej.ImageJ` gateway | **FAIL** | "Class not found on any classloaders." The ImageJ2 gateway isn't in this MM's classpath, #2401 or not. |
| 6c | find a SciJava `Context` accessor on Studio/PluginManager | **SKIP** | none found. `PluginManager` *does* expose `get_plugin_class_loader` (the #2401 shared loader) but no `Context` getter, so there's no supported way to reach MM's own `CommandService` over the bridge. |

**Net conclusions folded into the design below:**

1. **Premise confirmed.** `data().ij()` is `DefaultImageJConverter` — a converter,
   not a plugin runner — and it round-trips MM `Image` → `ImageProcessor` cleanly.
2. ~~**Static IJ1 dispatch over pyjavaz doesn't work.**~~ **RETRACTED 2026-08-07
   (design/42 block 42a) — static IJ1 dispatch works, through
   `controller._new_static_java_class`.** What this spike measured was the
   pyjavaz static-class cache collision of design/12, not a property of IJ1 or of
   ZMQ; check 4 wrapped `ij.IJ` before `ij.measure.ResultsTable` and got `ij.IJ`'s
   surface back. Measured on M5 2026-08-07 in both wrap orders, with a control
   that reproduced the collision when the eviction was bypassed. The follow-up
   spike this conclusion called for **has now been run and is that spike.** Full
   note in §2 above. Instance-based use (`ImageProcessor` from the converter, an
   instantiated `PlugInFilter`) remains the recommendation for *measurement* — on
   its merits, not because statics are unreachable.
3. **The converter is the load-bearing, proven asset.** It, plus `ImageProcessor`
   instance methods, is enough for real analysis without the macro engine.
4. **Approach B is genuinely blocked here**, and now precisely: SciJava is present
   (MM is built on it) but the `net.imagej` ImageJ2 gateway isn't bundled, and MM
   doesn't surface its SciJava `Context`. Reaching a `CommandService` would need
   either an MM-side change to expose the `Context`, bundling `net.imagej`, or the
   in-process jPype backend.

---

## Correcting the premise: what `data().ij()` actually is

The MM Java API method is (verify signatures against the installed MM build, as 09
does for #2401):

```java
// org.micromanager.data.DataManager
ImageJConverter ij();

// org.micromanager.data.ImageJConverter  (a CONVERTER, not a plugin host)
ImageProcessor createProcessor(Image image);
ImageProcessor createProcessorFromComponent(Image image, int component);
Image          createImage(ImageProcessor ip, Coords coords, Metadata metadata);
```

So `studio.get_data_manager().ij()` → `ImageJConverter`. Over the pycro-manager
bridge that's `studio.data().ij()` (snake_case: `create_processor`, `create_image`).
Its job is marshalling MM `Image` ↔ ImageJ `ImageProcessor`. It is the *right tool
for the crossing problem* (09, caveat 2) but it presupposes you already have an MM
`Image` Java-side and want an `ImageProcessor` to feed a plugin. It is not itself a
way to *invoke* a plugin.

The upshot: `ij()` is an asset we should *use* inside an IJ plugin hook (to build
the `ImageProcessor` a plugin needs), but the actual invocation goes through
`ij.IJ` (IJ1) or `net.imagej.ImageJ`/`CommandService` (IJ2).

---

## How an ImageJ plugin becomes reachable from Python

### IJ1 (the reachable path — via the converter, not the macro engine)

ImageJ1 core classes (`ij.IJ`, `ij.ImagePlus`, `ij.WindowManager`,
`ij.process.ImageProcessor`, `ij.measure.ResultsTable`) live in **MM's own
classpath** — Micro-Manager is built on top of ImageJ1 — so the ZMQ server's base
loader **resolves the class names** (spike check 3: `JavaClass("ij.IJ")` resolves on
this build). But resolving a class is not the same as calling its static methods:

- ⚠️ **Static methods are not callable via `JavaClass(...).method()`.** The spike
  (check 4) showed `JavaClass("ij.measure.ResultsTable").get_results_table()` raises
  `AttributeError: 'java_lang_Class' has no attribute 'get_results_table'` — pyjavaz
  returns a plain `java.lang.Class`, not a static-dispatch proxy. So `ij.IJ`'s
  `runMacro`/`runPlugIn`/`run` (all static) can't be invoked this way, and the
  `ResultsTable` static getter can't either. **The macro-engine path is not usable
  as originally stubbed.**
- ✅ **Instances work.** `data().ij().create_processor(image)` returns a real
  `ImageProcessor` (spike check 5), and its *instance* methods are callable over the
  bridge. `ImageProcessor.get_statistics()` yields an `ImageStatistics` with `.mean`,
  `.std_dev`, etc. — scalar analysis without the macro engine or any static call.
- For third-party IJ1 *plugins* that implement `PlugInFilter`, the instance path is
  `JavaObject("com.foo.MyFilter")` then `.run(ip)` — but that requires the plugin
  class to be on a classloader the bridge sees (the #2401 shared loader or MM's
  classpath); IJ's own `plugins/` `PluginClassLoader` is *not* automatically that
  loader, so this is unverified and may class-not-found. Confirm per plugin.

Net: the reachable IJ1 surface here is **the converter + `ImageProcessor` instance
methods**, not the static `ij.IJ` macro engine. That is enough for measurement-style
analysis; it is not enough to run an arbitrary installed IJ1 menu command.

```python
from pycromanager import Studio
studio = Studio(port=4827)
image = studio.data().convert_tagged_image(tagged)  # an MM Image, Java-side
ip = studio.data().ij().create_processor(image)      # proven: ImageProcessor
stats = ip.get_statistics()                          # instance call, no statics
mean = float(stats.mean)                             # only a scalar crosses back
```

### IJ2 / SciJava (deferred — now for a concrete reason)

IJ2 commands are `@Plugin(type=Command.class)` SciJava plugins, run via a
`net.imagej.ImageJ` gateway or a `CommandService`:

```java
ij.command().run(MyCommand.class, true, "input", img, "sigma", 2.0);
```

The spike pinned down *why* this is blocked on the ZMQ backend, replacing the
earlier guesswork:

- **The SciJava framework is present but the ImageJ2 gateway is not.**
  `org.scijava.Context`, `CommandService`, and `ModuleService` all resolve (check
  6a) — unsurprising, since MM's own plugins are SciJava — but `net.imagej.ImageJ`
  is **not on any classloader** (checks 6a/6b: "Class not found"). So there is no
  gateway to construct, #2401 or not.
- **MM exposes no `Context` accessor over the bridge.** Studio and `PluginManager`
  have no `*context*` getter (check 6c); `PluginManager` does expose
  `get_plugin_class_loader` (the #2401 shared loader) but not the SciJava `Context`
  a `CommandService` needs. So even though `CommandService` *resolves*, there's no
  supported handle to MM's live one.

Unlocking Approach B would require an MM-side change (expose the `Context`, or bundle
`net.imagej`), or the in-process jPype backend. Treat IJ2 as deferred, not "research
the API" — the API is simply absent here.

---

## The load-bearing difference: IJ1 is a GUI singleton on the wrong thread

MM plugins (09) are mostly plain objects with methods you call. IJ1 is a
**stateful, single-instance, GUI-oriented, EDT-bound** framework, and a
pycro-manager hook runs on the **acquisition thread**, in-process to Java only over
ZMQ. Concretely:

- **"Current image" coupling.** `IJ.run("Measure")` and most menu commands act on
  `WindowManager.getCurrentImage()`, not on an image you pass. To analyze the hook's
  image you must first make it the current image (`imp.show()` / `WindowManager`
  bookkeeping) — which opens windows and mutates global state per frame.
- **Results via global singletons.** IJ1 plugins report through
  `ResultsTable.getResultsTable()`, the ROI Manager, and the Log window — not via a
  return value. A hook reads results back by *pulling scalars out of the
  `ResultsTable`* after the run, and must reset it between frames or rows accumulate.
- **Threading.** IJ1 assumes the EDT; some commands spawn dialogs. Headless
  operation needs ImageJ in headless/batch mode (`java.awt.headless`,
  `IJ.setBatchMode(true)`), and even then not every plugin is headless-safe.
- **Concurrency.** One embedded ImageJ, one `ResultsTable`, one WindowManager,
  shared with MM's own display/live path (project memory notes MM's Preview canvas
  is already touchy in-process — see `project_jpypemm_display_limitation`). A hook
  firing IJ1 commands per frame contends with that.

On top of these framework constraints, the spike added a harder one: **the static
`ij.IJ` entry point that would run such a macro isn't callable over pyjavaz at all**
(check 4). So the originally-imagined "run a macro, read `ResultsTable`" hook is
doubly blocked — by IJ1's global-state fragility *and* by static dispatch. The path
that survives both is the instance route: convert the frame to an `ImageProcessor`
and call its instance methods, touching neither the macro engine nor any singleton.

---

## Where the image lives, and the crossing rule (09) applied to ImageJ

In a pycro-manager `image_process_fn` hook, **Python already holds the numpy
image**; ImageJ's `ImagePlus` lives in Java. So there are three shapes, and 09's
rule ("keep full-image work on one side; only scalars cross") picks the winner:

1. **Scalar-out on an already-Java image (best).** Analyze an image MM already holds
   Java-side (a snapped `TaggedImage` → `create_processor`) and cross back only a
   scalar. No full image leaves Python. This is the `run_ij_analysis` tool (C′) and
   is what the spike proved. It's *not* the per-frame `image_process_fn` case, because
   there the image originates in Python (see shape 2).
2. **Per-frame from Python (the crossing).** In an `image_process_fn` hook the numpy
   frame originates in Python, so feeding it to the converter means synthesizing an MM
   `Image` from the array — a full-image round-trip Java-ward (09, caveat 2),
   expensive and type-mismatched. `IJPluginHook` (A′) does exactly this and pays that
   cost knowingly; only the scalar crosses *back*. Use it only when a per-frame IJ
   statistic is genuinely required.
3. **Full-image Java (make it a processor pipeline, not a hook).** If ImageJ should
   do heavy full-image work, it should ingest the image Java-side. Per 09, that's an
   MM **`ProcessorPlugin`** pipeline (runs before Python sees the frame), or an
   ImageJ plugin reading a Datastore — with the Python hook only orchestrating and
   reading scalar/tag results. `data().ij().create_processor(image)` is the correct
   converter for that Java-side crossing.

If you must cross a full image (shape 2), use the sanctioned converter (`data().ij()`
+ `DataManager.create_image`), not a hand-rolled `ShortProcessor(w, h, short[])`, to
keep the pixel-format handling correct.

---

## Proposals

Reordered after the spike. All reuse 09's `PluginAccess` seam and safety gates. The
proposals are now built on the **converter + `ImageProcessor` instance methods**
(proven), not the static macro engine (disproven).

### Approach C′ — `run_ij_analysis` converter tool (recommended first ship)

A one-shot tool: snap a frame, `convert_tagged_image` → `ij().create_processor` →
call `ImageProcessor` instance methods (`get_statistics()`), return scalars. This is
the *only* path the spike proved end-to-end (checks 2 + 5), needs no static dispatch,
no macro engine, and no #2401. It is the ImageJ analog of 09's Approach C, but built
on the converter rather than `IJ.runMacro`.

**Benefits** — proven working today; touches no hardware beyond the snap; only
scalars cross back; simplest thing to gate.

**Drawbacks** — not "a plugin as part of a hook" (the original ask); limited to what
`ImageProcessor`/`ImageStatistics` expose plus any *instantiable* `PlugInFilter`
(class-resolution unverified — see IJ1 section).

### Approach A′ — `IJPluginHook`: per-frame converter analyzer (second step)

An `image_process_fn` hook that, per frame, builds an MM `Image` from the numpy
frame, converts it to an `ImageProcessor`, computes a scalar via instance methods,
and makes a guarded Python-side keep/skip decision — mirroring `MMPluginHook`
(`hooks.py:194`). Registered in `PRECODED_HOOK_REGISTRY` (`hooks.py:275`);
`_resolve_hook`/`_acquire_with_hooks` need no changes.

**Benefits** — reuses the whole adaptive path; only a scalar crosses *back*.

**Drawbacks** — building the MM `Image` from the numpy frame pushes the **full image
Java-ward per frame** (09, caveat 2: the crossing we otherwise avoid). Prefer C′ /
an MM processor pipeline unless a per-frame IJ statistic is genuinely required. Also
inherits IJ1's global-state fragility if it ever reaches beyond `ImageProcessor`
instance methods.

### Approach B — IJ2 `CommandService` hook (blocked here, not just deferred)

A typed hook that runs a SciJava `Command` via a `net.imagej.ImageJ`/`CommandService`
gateway. **The spike showed this is unreachable on the current ZMQ backend:** the
`net.imagej.ImageJ` gateway class is not bundled (checks 6a/6b) and MM exposes no
SciJava `Context` over the bridge (6c). SciJava *core* resolves, so the blocker is
specifically the missing gateway + missing Context handle, not the framework. Unlock
requires an MM-side change (expose the `Context`, or bundle `net.imagej`) or the
in-process jPype backend (project memory: jPype backend exists only on a branch).

### Recommendation

Ship **C′** (converter analysis tool) first — it's the proven capability. Add **A′**
(per-frame converter hook) only if a real per-frame IJ statistic is needed, accepting
the crossing cost. Keep **B** blocked pending an MM-side change or jPype. Before
building any per-frame or macro variant, run the **follow-up static-dispatch spike**
(Caveats 1) — if no static/macro entry point is reachable, the IJ story stays
"converter + instance methods," full stop.

---

## Safety

The 09 gates apply, with ImageJ-specific notes:

1. **Reuse `check_plugin` (`safety.py:137`) as the analyzer gate.** Pass the IJ
   identity as the "classpath" — a stable `ij:analysis:<name>` string for the
   converter tool, or a `PlugInFilter` class name if one is ever instantiated — so
   the existing `plugins.blocked` blocklist (`safety_config.yaml`) covers the IJ path
   with no new config. Enforce at **runtime** in the tool/hook `__init__`, exactly as
   `MMPluginHook` does (`hooks.py:211`).
2. **Analyzer-only by default.** The proven converter path only reads pixels and
   computes statistics — no hardware — so it's the read-only class: allow by default,
   blocklist the rare bad actor. *If* the static/macro engine later becomes reachable
   (follow-up spike), note that a macro string is arbitrary Java the AST scanner can't
   see (same blind spot as 09's runtime gate); route any hardware-touching IJ path
   through `check_plugin_motion` (`safety.py:148`) + `plugins.allow_hardware_motion`
   and guard the *result* passively (never re-drive), per 09's autofocus rule.
3. **User confirmation before enabling an IJ path**, surfacing the analysis
   identity + any scalar read — same policy as MM plugin hooks (`hook_docs.py`).
4. **Global-state hygiene stays relevant if IJ1 statics are ever used.** The
   converter/`ImageProcessor` path touches no singletons, so it's clean. But the
   moment anything reaches the macro engine, `ResultsTable`/WindowManager are shared
   with MM's own paths — reset the table around each run and leave no orphan
   `ImagePlus` windows, or you corrupt *another* part of MM. Treat that as mandatory
   for any future macro-based variant.

---

## Code stubs

Stubs updated to the spike-proven mechanism. The static `ij_runner()` / `ij_results()`
accessors from the first draft are **removed** — they relied on
`JavaClass("ij.IJ").run_macro(...)` / the `ResultsTable` static getter, which the
spike showed pyjavaz cannot dispatch (check 4).

### 1. `controller.py` — a converter accessor on `PluginAccess`

```python
# on PluginAccess (controller.py:49). Spike-verified: studio.data().ij() returns
# org.micromanager.data.internal.DefaultImageJConverter, and create_processor()
# round-trips (spike check 5). No #2401 probe needed for this.

def ij(self):
    """Return the MM<->ImageJ pixel converter (DefaultImageJConverter).

    NOTE: this CONVERTS images; it does NOT run plugins. There is no working way to
    call the static ij.IJ macro engine over pyjavaz (spike check 4), so IJ analysis
    goes through this converter + ImageProcessor instance methods. Kept here so
    hooks/tools never import pycromanager.
    """
    return self._studio.data().ij()

def image_to_processor(self, tagged):
    """MM TaggedImage -> ImageProcessor via the converter (instance-method surface)."""
    image = self._studio.data().convert_tagged_image(tagged)
    return self.ij().create_processor(image)
```

### 2. `tools.py` / `tools_schema.py` — `run_ij_analysis` tool (Approach C′, proven)

```python
# tools.py -- register in TOOL_REGISTRY (tools.py:1199) with a schema entry.
def run_ij_analysis(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """Snap one frame and return ImageJ ImageProcessor statistics. Analysis only.

    Proven end-to-end by the spike (converter round-trip + instance methods). Reads
    pixels only; fires the camera once; moves no stage. Gated by plugins.blocked.
    """
    guard.check_plugin("ij:analysis:statistics")
    ctrl.core.snap_image()
    tagged = ctrl.core.get_tagged_image()
    ip = ctrl.plugins.image_to_processor(tagged)
    stats = ip.get_statistics()                    # ij.process.ImageStatistics
    return {"mean": float(stats.mean), "std_dev": float(stats.std_dev),
            "min": float(stats.min), "max": float(stats.max),
            "width": int(ip.get_width()), "height": int(ip.get_height())}
```

### 3. `hooks.py` — `IJPluginHook` (Approach A′), per-frame converter analyzer

```python
class IJPluginHook(HookBase):
    """Per-frame ImageJ analysis via the converter, scalar-out.

    Builds an MM Image from the numpy frame, converts to an ImageProcessor, and
    reads a scalar statistic for a guarded Python-side keep/skip decision -- the
    ImageJ analog of MMPluginHook.

    COST: building the MM Image pushes the FULL image Java-ward per frame (09,
    caveat 2 -- the crossing we normally avoid). Prefer run_ij_analysis / an MM
    processor pipeline unless a per-frame IJ statistic is genuinely needed. Only the
    scalar crosses back.
    """

    def __init__(self, ctrl, guard, stat: str = "mean",
                 reject_below: float | None = None, log_path: str | None = None):
        super().__init__(log_path)
        self.ctrl = ctrl
        self.guard = guard
        guard.check_plugin(f"ij:analysis:{stat}")    # runtime blocklist, like MM hook
        self.stat = stat
        self.reject_below = reject_below

    def image_process_fn(self, image: np.ndarray, metadata: dict, event_queue):
        try:
            dm = self.ctrl.studio.data()
            # createImage(pixels, w, h, bytesPerPixel, numComponents, coords, meta):
            # this is the full-image crossing -- see the class docstring.
            mm_image = dm.create_image(
                image.tobytes(), int(image.shape[1]), int(image.shape[0]),
                int(image.dtype.itemsize), 1, dm.create_coords().build(), None)
            ip = self.ctrl.plugins.ij().create_processor(mm_image)
            score = float(getattr(ip.get_statistics(), self.stat))
        except Exception as e:
            self._log.append({"frame": metadata.get("time"), "ij_error": str(e)})
            self._write_log()
            return image, metadata                   # fail open: never lose data
        keep = self.reject_below is None or score >= self.reject_below
        self._log.append({"frame": metadata.get("time"),
                          "ij": self.stat, "score": score, "kept": keep})
        self._write_log()
        return (image, metadata) if keep else None


PRECODED_HOOK_REGISTRY.update({
    "ij_analysis": IJPluginHook,
})
```

> The `create_image(...)` signature above is illustrative — pin it against the MM
> build's `DataManager` before coding (it's the piece the spike did **not** exercise;
> the spike converted an existing `TaggedImage`, not a synthesized one).

No changes needed to `run_adaptive_zstack` / `run_adaptive_timelapse`,
`_resolve_hook`, or `_acquire_with_hooks` — `ij_analysis` flows through the existing
`hook_strategy` parameter. Add it to the adaptive-tool schema descriptions and
append an "ImageJ analysis" subsection to `HOOK_REFERENCE` (`hook_docs.py:151`, next
to the MM plugin section), documenting the converter-only scope (no macro engine),
the per-frame crossing cost, and the reuse of `plugins.blocked`.

---

## Caveats & open questions

1. **Follow-up spike: is *any* static/macro IJ1 entry point reachable over pyjavaz?**
   The spike proved `JavaClass("ij.IJ").<static>()` and the `ResultsTable` static
   getter do **not** work. Before building anything beyond the converter path, spike
   alternatives: does pyjavaz expose a static-invocation form; can a thin Java helper
   on MM's classpath wrap `IJ.runMacro`; does `studio` offer any macro-run entry? If
   all fail, the IJ story is permanently "converter + `ImageProcessor` instance
   methods," and the macro-based ideas should be struck, not just deferred.
2. **Pin the synthesized-image path.** `run_ij_analysis` (C′) converts an *existing*
   `TaggedImage` and is spike-proven. `IJPluginHook` (A′) *synthesizes* an MM `Image`
   from a numpy frame via `DataManager.create_image(...)` — a signature the spike did
   **not** exercise. Verify it (and `create_coords`) against the installed MM build
   before relying on A′; it's the untested seam.
3. **Instantiable `PlugInFilter` plugins are unverified.** Running a third-party IJ1
   plugin as `JavaObject("com.foo.MyFilter").run(ip)` needs the plugin class on a
   loader the bridge sees. IJ's own `plugins/` `PluginClassLoader` is *not*
   automatically that loader, and #2401's shared loader holds *MM* plugin JARs, not
   IJ `plugins/` JARs. Spike a specific plugin before promising this.
4. **`convert_camel_case`.** As in 09, `create_processor`/`get_statistics`/`std_dev`
   are the snake_case bridge names for `createProcessor`/`getStatistics`/`stdDev`;
   set the object `convert_camel_case=False` if a specific call needs exact Java names.
5. **Latency.** Each converter/instance call is a ZMQ round-trip on the acquisition
   thread, and A′ additionally ships a full frame Java-ward per image. Benchmark
   before fast timelapses (09, caveat 3); this is a strong reason to prefer C′ or a
   processor pipeline.
6. **IJ2 is blocked at the MM build level, not just deferred.** `net.imagej.ImageJ`
   isn't bundled and there's no `Context` accessor (spike 6a–6c). It becomes tractable
   only via an MM-side change or the in-process jPype backend; revisit then.

## Suggested implementation steps

0. **Done (spike):** confirmed `data().ij()` → `DefaultImageJConverter`, the
   `create_processor` round-trip, the static-dispatch limitation, and IJ2 being
   absent. Record `DefaultImageJConverter` + method names in a `controller.py`
   comment like `_MM_PLUGIN_LOADER_CLASS` (`controller.py:17`).
1. Add `PluginAccess.ij()` + `image_to_processor()` (`controller.py:49`). No #2401
   probe needed for the converter.
2. Add `run_ij_analysis` (C′) + schema (`tools.py:1199`, `tools_schema.py`); verify
   scalars against a real snap. This is the proven first ship.
3. Run the follow-up static-dispatch spike (Caveat 1). Only if it finds a working
   entry point, reconsider a macro-based variant.
4. (Optional, if a per-frame IJ statistic is needed) Verify `DataManager.create_image`
   (Caveat 2), then add `IJPluginHook` (A′); register in `PRECODED_HOOK_REGISTRY`
   (`hooks.py:275`); test blocklist-deny and fail-open with a mock converter, and
   benchmark the per-frame crossing.
5. Extend `HOOK_REFERENCE` (`hook_docs.py:151`) and adaptive-tool schema descriptions
   (converter-only scope, no macro engine); require user confirmation before an IJ
   path runs.
6. Keep IJ2/`CommandService` (Approach B) blocked pending an MM-side change or jPype.
