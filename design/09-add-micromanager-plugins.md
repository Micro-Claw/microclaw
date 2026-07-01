# Using Micro-Manager plugins inside microclaw hooks

## TL;DR

[micro-manager/micro-manager#2401](https://github.com/micro-manager/micro-manager/pull/2401)
introduces a single **`SharedPluginClassLoader`** that holds *all* plugin JARs and
is the loader handed to Micro-Manager's ZMQ server. Today microclaw talks to MM
over that ZMQ bridge (pycro-manager, `Core`/`Studio` on port 4827 — see
`controller.py:5`). Because the ZMQ server could previously only resolve classes
its own loader knew about, Python could construct *core/Studio* objects but **not
third-party plugin classes**. After #2401 every installed plugin lives in the
loader the bridge sees, so `JavaObject("org.foo.MyPlugin")` becomes resolvable
from Python.

That unlocks a new hook flavor: a hook whose `image_process_fn` /
`post_hardware_hook_fn` delegates to a real MM plugin (autofocus plugins,
analysis/decision plugins, processors) instead of re-implementing the logic in
Python. This doc proposes how to wire that into microclaw's existing hook
machinery (`hooks.py`, `_resolve_hook`/`_acquire_with_hooks` in `tools.py`) with
minimal new surface, plus the safety problem it creates and how to contain it.

---

## What PR #2401 actually changes

From the diff (paraphrased — verify against the merged commit before coding):

- **New `SharedPluginClassLoader`** — a `URLClassLoader` parented to MM's loader
  (deliberately *not* the system loader, to stay Java-9+ and ZMQ-bridge safe)
  that accumulates every discovered plugin JAR's URL.
- **`PluginFinder.findPlugins(loader, root)`** adds discovered JARs to that shared
  loader before SciJava discovery; `findPluginsInUrls()` discovers on isolated
  URLs but **loads the classes through the shared loader**.
- **`DefaultPluginManager`** owns one `SharedPluginClassLoader`, created at
  construction.
- **`MMStudio`** ZMQ-server init is simplified to pass that **one** classloader
  instead of collecting separate loaders per plugin type (menu / autofocus /
  display-gear / processor / overlay).

Net effect that matters to us: **the classloader registered with the ZMQ server
now contains all plugins in one namespace.** That is precisely the loader
pycro-manager's `JavaObject`/`JavaClass` use to resolve a `classpath` string.

> ⚠️ This is a refactor of *plugin loading*, not a new public Python API. It
> makes plugin classes *reachable*; it does not give them a stable, documented
> contract. We are leaning on MM/SciJava plugin interfaces (`AutofocusPlugin`,
> `ProcessorPlugin`, `SciJavaPlugin`) plus reflective access over the bridge.

---

## Why this matters for microclaw (current limitation)

`pycromanager` already exposes generic Java access:

```python
from pycromanager import JavaObject, JavaClass
# JavaObject(classpath, args=None, port=4827, ..., convert_camel_case=True)
# JavaClass(classpath, port=4827, ...)
```

Verified locally (pycromanager 1.0.2): both are importable and resolve any class
the ZMQ server's loader can see. Before #2401 that loader did **not** reliably
see plugin JARs, so `JavaObject("org.micromanager.someplugin.Foo")` would fail
with a class-not-found even though the plugin was installed and visible in MM's
menus. After #2401 it resolves.

Today microclaw hooks (`hooks.py`) are pure-Python: `AutofocusHook` re-implements
a Z sweep, `FocusFeedbackHook`/`IntensityAdaptiveHook` compute on the numpy
image, `PositionFilterHook` thresholds intensity. None can call the lab's
existing, validated MM plugins. For a biologist who already trusts, say, a
specific hardware-autofocus plugin or a custom htSMLM/EMU analysis plugin, the
value is "use *that*, from inside my adaptive acquisition," not "let Claude
re-derive it in Python."

> Note on the jPype port: project memory records a planned in-process
> jPypeMM/AcqEngJ backend, but **`main` currently runs the pycro-manager ZMQ
> backend** (`controller.py`, `tools.py:10` import `pycromanager`; the JPype work
> was reverted — commit `b4e8d03`). #2401 is therefore directly relevant to the
> backend in production *today*. If the jPype port lands later, plugin access
> gets *easier* (in-process JVM, no classloader-over-ZMQ gap), and the
> `PluginAccess` abstraction below is the seam that lets both backends share one
> hook implementation.

---

## How a plugin becomes reachable from Python

Two access paths, both enabled/strengthened by #2401:

1. **By role, via Studio managers** — for plugins MM already categorizes:
   - Autofocus: `studio.get_autofocus_manager().get_autofocus_method()` →
     an `AutofocusPlugin` with `full_focus()`, `incremental_focus()`,
     `set_property_value(name, value)`.
   - Processors: `studio.data().get_application_pipeline...` /
     `ProcessorPlugin.createConfigurator()/createFactory()`.
   These leaned on per-type classloaders before; #2401 keeps them working through
   the unified loader.

2. **By class name, generically** — `JavaObject("fully.qualified.PluginClass")`.
   This is the path that *only* works reliably post-#2401, and is what we use for
   arbitrary analysis/decision plugins that don't fit a Studio manager role.

---

## Proposals

Three approaches, smallest-surface first. They share a `PluginAccess` seam on the
controller so hooks never import `pycromanager` directly.

### Approach A — A `PluginAccess` accessor + a generic `MMPluginHook` (recommended)

Add one thin accessor to `MicroscopeController` and one new hook class that
delegates to a plugin object. Register it like any other strategy in
`PRECODED_HOOK_REGISTRY` (`hooks.py:194`); `_resolve_hook` (`tools.py:836`) and
`_acquire_with_hooks` (`tools.py:329`) need **no changes** — they already inject
`ctrl`/`guard` and wire `image_process_fn`/`post_hardware_hook_fn` by duck typing.

**Benefits**

- Reuses the entire existing adaptive-acquisition path (zstack + timelapse).
- One backend seam (`PluginAccess`) keeps `pycromanager` out of `hooks.py` and
  makes the eventual jPype backend a drop-in.
- Generic: any plugin class with a callable analysis method works.

**Drawbacks**

- Plugin methods are arbitrary Java — the Python AST safety scan
  (`hook_manager.validate_hook_code`) can't see them. Needs the runtime plugin
  gate (blocklist / motion flag) below.
- Camel/snake conversion and Java↔numpy marshalling are fiddly; the hook must
  hand the plugin something it understands (a scalar/`TaggedImage`, not a numpy
  array). Best kept to plugins that take/return simple values.

### Approach B — Role-specific adapter hooks (autofocus first)

Skip the generic escape hatch; ship one well-typed hook per MM plugin *role*. The
highest-value, lowest-risk one is **autofocus**: a `post_hardware_hook_fn` that
calls the user's selected MM autofocus plugin via the AF manager.

**Benefits**

- Strongly typed, predictable contract (`full_focus()` returns the new Z); no
  numpy marshalling.
- Maps cleanly onto the existing `AutofocusHook` slot — drop-in alternative
  strategy `autofocus_mm_plugin`.
- Device motion still funnels through readback we can guard (see Safety).

**Drawbacks**

- One adapter per role; processors/overlays need separate work and don't bridge
  to `image_process_fn` cleanly (different Image types).
- Doesn't cover bespoke analysis plugins that aren't an MM "role."

### Approach C — A `call_mm_plugin` escape-hatch *tool* (not a hook)

Expose plugin invocation as a standalone tool (`call_mm_plugin(classpath,
method, args)`) rather than inside an acquisition. Useful for one-shot "run the
plugin once and tell me the result," outside the hook lifecycle.

**Benefits**

- Simplest to reason about; no acquisition-lifetime concerns.
- Good for discovery/experimentation before committing to a hook.

**Drawbacks**

- Doesn't satisfy the actual request (plugins *as part of hooks*).
- Maximally unconstrained — a raw reflective call surface for the LLM. Hardest to
  guard.

### Recommendation

Ship **A + B together**: `PluginAccess` + generic `MMPluginHook` (A) for
analysis/decision plugins, and a typed `MMAutofocusPluginHook` (B) as the
flagship, safe, role-specific case. Defer C unless a non-acquisition use case
appears. Both reuse `_resolve_hook`/`_acquire_with_hooks` unchanged. 

---

## Safety (the load-bearing section)

MM plugins are arbitrary Java. They can move stages, fire shutters, and ignore
`SafetyGuard` entirely — the guard (`safety.py`) only gates microclaw's own
hardware tool calls. The AST scanner (`hook_manager.py:16`) inspects *Python*
source and is blind to anything happening across the bridge. So plugin hooks
**bypass two of microclaw's three safety layers**. Mitigations:

1. **Two risk classes, two gates — blocklist for analyzers, a motion flag for
   hardware.** The list is not malware defense (most plugins are vetted
   independently); it constrains what the *autonomous LLM* can reach that bypasses
   `SafetyGuard`. Split by risk:
   - **Read-only analyzer plugins** (Approach A / `MMPluginHook`) return a value
     and touch no hardware. Allow by default; support an *optional blocklist* in
     `safety_config.yaml` (`plugins.blocked`) for the rare bad actor.
     `SafetyGuard.check_plugin(classpath)` raises only if the class is blocked.
   - **Hardware-motion plugins** (Approach B / autofocus) move the stage, and the
     LLM chose to invoke them. A blocklist can't protect against a *legitimate*
     plugin parking Z somewhere unsafe, so gate this class behind a single global
     `plugins.allow_hardware_motion` flag (default `true`), not a per-plugin
     allowlist. The LLM cannot edit this file.
2. **Guard passively — never put a second *active* controller on an axis the
   plugin is driving.** For autofocus, read Z *after* the plugin runs and call
   `guard.check_z(new_z)`; if it is out of bounds, **abort/skip the capture and
   log** — do **not** re-position the stage. An active correction (microclaw
   moving Z back while the plugin also drives Z) creates *conflicting* safety
   systems: two controllers with different setpoints can fight or oscillate, and
   microclaw's configured limit can disagree with the plugin's/device's own limit.
   A passive assert-and-abort is at worst *redundant* with the plugin's internal
   limits (redundancy toward "stop" is harmless); an active re-drive is where the
   conflict lives. microclaw trusts the plugin + the device's hard limits to *own
   the motion*, and only decides whether to keep acquiring. (Caveat: cleanly
   aborting mid-acquisition from `post_hardware_hook_fn` is awkward in the
   pycro-manager API — return `None` to skip the capture and signal the outer loop
   to stop; it is not a one-liner.)
3. **Always require user confirmation to enable a plugin hook**, mirroring the
   existing rule that Claude-generated hooks need confirmation before save
   (`generate_and_save_hook` flow). Surface the classpath + method to the user.
4. **Read-only by default.** The generic `MMPluginHook` should default to
   treating the plugin as an analyzer (return a value used for a *Python-side*,
   guarded decision) and forbid the plugin from being the thing that moves
   hardware unless the role-specific, guarded path (B) is used.
5. **Enforce the plugin gate at *runtime*, not save time.** A saved plugin hook
   calls Java via `JavaObject("some.Class")` — the classpath is just a Python
   string the AST scanner can't interpret. So `check_plugin` /
   `allow_hardware_motion` must be enforced inside `PluginAccess` when the object
   is constructed / the plugin runs, not when the hook is saved.

---

## Code stubs

### 1. `controller.py` — a backend seam for plugin access

```python
class PluginAccess:
    """Resolve and call Micro-Manager plugins over the active backend.

    On the pycro-manager backend this is JavaObject/JavaClass over ZMQ; the
    classes are only resolvable once micro-manager#2401 lands (unified
    SharedPluginClassLoader handed to the ZMQ server). On a future jPype backend
    this becomes a direct in-process JVM lookup with the same surface.
    """

    def __init__(self, studio, port: int = 4827):
        self._studio = studio
        self._port = port

    def list_plugins(self) -> dict[str, list[str]]:
        """Return installed plugins grouped by role (menu/autofocus/processor...).

        Reads studio.plugins() (PluginManager). Used by the list_mm_plugins tool
        so the LLM/user can see classpaths to review/gate.
        """
        pm = self._studio.plugins()
        out: dict[str, list[str]] = {}
        for role, getter in (
            ("autofocus", pm.get_autofocus_plugins),
            ("processor", pm.get_processor_plugins),
            ("menu", pm.get_menu_plugins),
        ):
            try:
                out[role] = sorted(str(k) for k in getter().key_set())
            except Exception:
                out[role] = []
        return out

    def get_object(self, classpath: str, args: list | None = None):
        """Construct an arbitrary plugin object by fully-qualified class name.

        Only reachable post-#2401. Kept here so hooks never import pycromanager.
        """
        from pycromanager import JavaObject
        return JavaObject(classpath, args=args or [], port=self._port)

    def get_autofocus_method(self, plugin_name: str | None = None):
        """Return the active (or named) MM autofocus plugin."""
        afm = self._studio.get_autofocus_manager()
        if plugin_name:
            afm.set_autofocus_method_by_name(plugin_name)
        return afm.get_autofocus_method()


# on MicroscopeController:
#     @property
#     def plugins(self) -> "PluginAccess":
#         return PluginAccess(self._studio, self._port)   # cache if desired
```

### 2. `hooks.py` — generic plugin hook (Approach A)

```python
class MMPluginHook(HookBase):
    """Delegate per-image analysis to an installed Micro-Manager plugin.

    The plugin is treated as an ANALYZER: it receives a scalar/feature derived
    from the image and returns a value microclaw uses for a guarded, Python-side
    decision (e.g. keep/skip). The plugin must NOT be relied on to move hardware
    here — use MMAutofocusPluginHook for that.
    """

    def __init__(self, ctrl, guard, classpath: str, method: str = "analyze",
                 reject_below: float | None = None, log_path: str | None = None):
        super().__init__(log_path)
        self.ctrl = ctrl
        self.guard = guard
        guard.check_plugin(classpath)          # runtime blocklist check
        self.classpath = classpath
        self.method = method
        self.reject_below = reject_below
        self._plugin = ctrl.plugins.get_object(classpath)

    def image_process_fn(self, image, metadata, event_queue):
        feature = float(np.mean(image))        # keep marshalling trivial/scalar
        try:
            score = float(getattr(self._plugin, self.method)(feature))
        except Exception as e:
            self._log.append({"frame": metadata.get("time"), "plugin_error": str(e)})
            self._write_log()
            return image, metadata             # fail open: never lose data on bug
        keep = self.reject_below is None or score >= self.reject_below
        self._log.append({"frame": metadata.get("time"),
                          "plugin": self.classpath, "score": score, "kept": keep})
        self._write_log()
        return (image, metadata) if keep else None
```

### 3. `hooks.py` — typed autofocus-plugin hook (Approach B, flagship)

```python
class MMAutofocusPluginHook(HookBase):
    """Run an installed MM autofocus plugin before each capture, guarded.

    Drop-in alternative to the pure-Python AutofocusHook: same post_hardware slot,
    but focusing is delegated to the lab's validated MM autofocus plugin.
    """

    def __init__(self, ctrl, guard, plugin_name: str | None = None,
                 log_path: str | None = None):
        super().__init__(log_path)
        self.ctrl = ctrl
        self.guard = guard
        self.plugin_name = plugin_name
        # Hardware-motion plugin: gate on the global motion flag, not a blocklist.
        guard.check_plugin_motion(f"autofocus:{plugin_name or '<active>'}")
        self._af = ctrl.plugins.get_autofocus_method(plugin_name)

    def post_hardware_hook_fn(self, event: dict):
        try:
            new_z = float(self._af.full_focus())     # plugin owns the motion
        except Exception as e:
            self._log.append({"axes": event.get("axes", {}),
                              "autofocus": "skipped", "reason": str(e)})
            self._write_log()
            return event
        # PASSIVE guard: assert on the result; if unsafe, skip capture and stop —
        # do NOT re-drive Z (that would fight the plugin's own safety controller).
        try:
            self.guard.check_z(new_z)
        except Exception as e:
            self._log.append({"axes": event.get("axes", {}),
                              "autofocus": "unsafe_abort", "unsafe_z": new_z,
                              "reason": str(e)})
            self._write_log()
            return None                              # skip this capture; signal stop
        self._log.append({"axes": event.get("axes", {}), "best_z_um": round(new_z, 3),
                          "plugin": self.plugin_name})
        self._write_log()
        return event


PRECODED_HOOK_REGISTRY.update({
    "mm_plugin_analyzer": MMPluginHook,
    "autofocus_mm_plugin": MMAutofocusPluginHook,
})
```

### 4. `safety.py` / `safety_config.yaml` — plugin blocklist + motion flag

```yaml
# safety_config.yaml
plugins:
  # Analyzer (read-only) plugins are allowed by default; list only the ones to
  # forbid. Most plugins are vetted independently, so this is normally empty.
  blocked:
    - "org.example.KnownBadPlugin"
  # Hardware-motion plugins (e.g. autofocus) are gated behind this single flag,
  # NOT a per-plugin list. Default off; a human flips it to opt in.
  allow_hardware_motion: false
```

```python
# safety.py (SafetyGuard)
def check_plugin(self, classpath: str) -> None:
    """Gate a read-only analyzer plugin: allow by default, deny if blocklisted."""
    blocked = self._cfg.get("plugins", {}).get("blocked", [])
    if classpath in blocked:
        raise SafetyError(
            f"Plugin '{classpath}' is in safety_config.yaml plugins.blocked."
        )

def check_plugin_motion(self, classpath: str) -> None:
    """Gate a hardware-motion plugin behind the global opt-in flag."""
    self.check_plugin(classpath)   # blocklist still applies
    if not self._cfg.get("plugins", {}).get("allow_hardware_motion", False):
        raise SafetyError(
            f"Plugin '{classpath}' moves hardware; set plugins.allow_hardware_motion: "
            "true in safety_config.yaml to permit hardware-motion plugin hooks. "
            "microclaw guards the *result* (see check_z) but does not re-drive the "
            "axis the plugin controls."
        )
```

### 5. New tool + schema (discovery) — `tools.py` / `tools_schema.py`

```python
# tools.py
def list_mm_plugins(ctrl: MicroscopeController, guard: SafetyGuard) -> dict:
    """List installed MM plugins by role so a human can review/gate them."""
    plugins = ctrl.plugins.list_plugins()
    return {"plugins": plugins,
            "hint": "Analyzer plugins run with hook_strategy='mm_plugin_analyzer' "
                    "(allowed unless in plugins.blocked). Autofocus plugins run with "
                    "hook_strategy='autofocus_mm_plugin' and require "
                    "plugins.allow_hardware_motion: true in safety_config.yaml."}

# add to TOOL_REGISTRY (tools.py:~1093) and a schema entry (tools_schema.py).
```

No change needed to `run_adaptive_zstack` / `run_adaptive_timelapse`,
`_resolve_hook`, or `_acquire_with_hooks` — the new strategies flow through the
existing `hook_strategy` parameter. Update each adaptive tool's schema
`description` (e.g. `tools_schema.py:597`) to mention the two new strategies, and
append a "Micro-Manager plugin hooks" section to `HOOK_REFERENCE`
(`hook_docs.py`) documenting the analyzer-vs-autofocus split, the passive-guard
rule, and the blocklist / `allow_hardware_motion` gates.

---

## Composing plugins in a reusable hook

The goal of hooks is **reusable Python scripts** encoding a lab's standard
pipeline, saved via `hook_manager` and re-callable by microclaw. Plugin hooks fit
that model, and the composition question ("can one hook drive several plugins, or
make hardware moves from a plugin's output?") has a clean answer — governed by one
rule.

### The rule: per hook, keep image processing on one side; only scalars cross

Decide, per hook, whether the heavy *full-image* work happens in **Python** or in
**Java**, and never shuttle the image across the bridge between steps. Crossing a
*statistic* (a scalar, a fit result, a metadata tag) is fine and cheap; crossing a
*full image* is the expensive, type-mismatched case to avoid unless truly
unavoidable. This is a sharper statement of the marshalling caveat below, and
`MMPluginHook` (Approach A) already follows it — it computes `np.mean(image)` in
Python and passes only the scalar to Java, so the image never crosses.

The one case the rule *excludes* is "all-Java on the *full* image." In a
pycro-manager `image_process_fn` hook, Python already holds the numpy image by the
time the hook runs, so handing it back to Java is exactly the round-trip to avoid.
Full-image Java processing should therefore **not be a hook** — configure it as an
MM **processor pipeline** (`ProcessorPlugin`), which runs Java-side *before* Python
ever sees the image, and let the Python hook orchestrate / read its results.

### (a) One hook, multiple plugins

Yes. A saved hook is ordinary Python orchestration: it can construct several
plugin objects and call them in sequence.

```python
class StandardPipelineHook(HookBase):
    def __init__(self, ctrl, guard, log_path=None):
        super().__init__(log_path)
        self.ctrl, self.guard = ctrl, guard
        # Multiple analyzer plugins, each gated at construction (runtime blocklist).
        self.qc   = ctrl.plugins.get_object("org.lab.QualityScorePlugin")
        self.clas = ctrl.plugins.get_object("org.lab.CellClassifierPlugin")
        guard.check_plugin("org.lab.QualityScorePlugin")
        guard.check_plugin("org.lab.CellClassifierPlugin")

    def image_process_fn(self, image, metadata, event_queue):
        feat = float(np.mean(image))            # only a scalar crosses
        score = float(self.qc.score(feat))
        label = str(self.clas.classify(feat))
        # ... combine, decide, log ...
```

If the plugins must pass *images* to each other, don't route each image through
Python — chain them Java-side as a native processor pipeline and let the hook
orchestrate handles / configuration.

### (b) Python hardware calls from a plugin's non-image output

Yes — this is the recommended pattern and the highest-value one: the plugin does
the specialized analysis Java is good at, returns a **non-image** result, and
microclaw makes a **guarded** Python hardware call on it.

```python
    def image_process_fn(self, image, metadata, event_queue):
        drift = float(self.focus_plugin.estimate_drift_um(float(np.mean(image))))
        if abs(drift) > self.deadband:
            new_z = self.ctrl.core.get_position() - drift
            self.guard.check_z(new_z)           # microclaw owns THIS move → guarded
            self.ctrl.core.set_position(new_z)
        return image, metadata
```

Note the safety asymmetry with Approach B: here **microclaw** issues the move
(from a plugin's advice), so `SafetyGuard` applies normally — there is no
competing controller, so no conflict. That differs from `MMAutofocusPluginHook`,
where the *plugin* drives the stage and microclaw can only guard passively.

MM-specific nuance: a `Processor`'s native output is an `Image`+metadata, not a
scalar return value. To consume a "non-image output," read it off the
**metadata/tags** the processor attaches (a tiny crossing), not the pixels.

Latency: each plugin call is a bridge round-trip, so N plugins per image = N
round-trips per image. Fine for slow acquisitions; benchmark before fast
timelapses (see caveat 3).

---

## Caveats & open questions

1. **Pin the PR contents before coding.** The summary above is paraphrased from
   the PR *page*, which is a moving target (force-pushes, renames, merged-different,
   or not-yet-merged). Before writing code against those names, lock onto a
   concrete merged artifact. There are **two dependency surfaces**, pinned
   differently:

   - **Python — `pycromanager`** (`JavaObject`/`JavaClass`): a real pip dependency.
     Pin it in `pyproject.toml` (verified `1.0.2`).
   - **Java — the MM build that contains #2401**: *not* pip-pinnable; you can't
     version-lock the user's Micro-Manager install from Python. Pin it by
     **feature detection + a recorded expected version**, not a constraint:

     a. **Record the verified artifact as a constant** (a human fills this in
        *after* opening the merged commit and confirming the class/method names —
        code can't verify names it doesn't yet know are right):

        ```python
        # controller.py / plugin_access.py
        # Verified against micro-manager merge commit
        # ad936ced45f6bcc0b55dad7b2f3c1c2436884f6f (PR #2401), merged 2026-06-25.
        # If these names drift, update here + design/09.
        _MM_PLUGIN_LOADER_SINCE = "20260626"   # first MM nightly after the #2401 merge
        ```

     b. **Probe the capability at runtime** in `PluginAccess`, so a pre-#2401 MM
        fails loudly instead of raising a cryptic Java `ClassNotFoundException`
        mid-acquisition:

        ```python
        def _assert_plugin_loader(self) -> None:
            """Fail fast with a clear message if the MM build predates #2401."""
            try:
                JavaClass(
                    "org.micromanager.internal.pluginmanagement.SharedPluginClassLoader",
                    port=self._port)          # only resolves post-#2401
            except Exception as e:
                raise RuntimeError(
                    "MM plugin access requires a Micro-Manager build with the "
                    "unified plugin classloader (PR #2401, nightly >= "
                    f"{_MM_PLUGIN_LOADER_SINCE}). Update Micro-Manager, or don't "
                    "use plugin hooks."
                ) from e
        ```

     c. **Optionally gate at connect time** by comparing `studio.get_version()` /
        the nightly date against `_MM_PLUGIN_LOADER_SINCE`, so the failure surfaces
        at startup rather than partway through an acquisition.

   The manual verification step stays manual: confirm the names against the merged
   commit and fill in the SHA + nightly date before implementing.
2. **numpy ↔ Java marshalling.** Passing a full image to a plugin across ZMQ is
   expensive and type-mismatched (`TaggedImage`/`Image` vs numpy). This only bites
   when a single hook splits *full-image* work across both sides. The
   "Composing plugins in a reusable hook" section above resolves it with a rule:
   per hook, keep the full-image processing on **one** side and let only scalars
   cross; a full-image *Java* path should be an MM processor pipeline, not a hook.
   The stubs already follow this (only scalars cross). A true processor-pipeline
   bridge (running the plugin's `Processor` on MM `Image` objects Java-side) is a
   larger, separate effort and probably belongs to the jPype backend.
3. **Thread/context.** pycro-manager hooks run on the acquisition thread; plugin
   calls over ZMQ add latency per image and may need `new_socket=True` to avoid
   contending with the main bridge socket. Benchmark before using on fast
   timelapses.
4. **`convert_camel_case`.** `JavaObject(..., convert_camel_case=True)` is the
   default and is why the stubs call `full_focus()`/`get_position()`. If a plugin
   relies on exact Java names, set it `False` for that object.
5. **Safety is the real gate, not the plumbing.** The plumbing is small; the
   blocklist + motion flag + passive result-guarding + user-confirmation policy is
   what makes this acceptable on real hardware. Don't ship A/B without the Safety
   section's runtime gates (points 1, 2, 5).

## Suggested implementation steps

0. Verify #2401's class/method names against the merged commit; record the SHA +
   first nightly date in `_MM_PLUGIN_LOADER_SINCE`. Pin `pycromanager` in
   `pyproject.toml`.
1. Add `PluginAccess` + `MicroscopeController.plugins` (`controller.py`), including
   the `_assert_plugin_loader` capability probe (caveat 1) so a pre-#2401 MM fails
   loudly.
2. Add `SafetyGuard.check_plugin` (blocklist) + `check_plugin_motion` (motion
   flag) + `plugins:` config schema (`safety.py`, `config.py`). Analyzers allowed
   by default; hardware motion off by default.
3. Add `list_mm_plugins` tool + schema; verify it returns classpaths against a
   real MM with a plugin installed.
4. Add `MMAutofocusPluginHook` (B) first; register and test against the Demo
   config / a stub autofocus plugin. Test the passive guard: an out-of-bounds
   result must skip capture WITHOUT re-driving Z, and the motion flag must gate it.
5. Add `MMPluginHook` (A); register; test the blocklist-deny and fail-open paths
   with a mock plugin object.
6. Extend `HOOK_REFERENCE` (`hook_docs.py`) and the adaptive-tool schema
   descriptions; require user confirmation before a plugin hook runs.
7. Live-validate end-to-end on the lab machine once an MM build with #2401 is
   installed (gated like other live tests).
```
