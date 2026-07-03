# Locate the Micro-Manager install directory via a Java call

## Overview

`emu_manager.find_mm_app_dir()` currently locates the Micro-Manager (MM)
installation directory by **guessing** — it consults a per-OS list of hardcoded
default paths (`_candidate_mm_dirs()`) and accepts the first one that contains an
EMU/htSMLM JAR or `EMU/config.uicfg`. This breaks whenever MM is installed
somewhere non-standard (custom folder, renamed version dir, network mount, a
platform we didn't enumerate).

Since microclaw now talks to MM over the pycro-manager ZMQ bridge, we have a
live handle into the MM/ImageJ JVM. That JVM already **knows** where it was
installed. So we can *ask* it instead of guessing.

This document proposes making an authoritative Java call the preferred detection
step, while keeping the existing path-guessing as an offline fallback.

---

## Background: why a Java call is authoritative

Micro-Manager 2 is ImageJ1 + plugins living under a single root directory. That
root is exactly the `mm_app_dir` we want — it contains `mmplugins/`, `plugins/`,
and `EMU/`. ImageJ1 knows its own install directory and exposes it:

```java
ij.IJ.getDirectory("imagej")   // -> "/Applications/Micro-Manager-2.0/"
```

Two reasons `ij.IJ.getDirectory("imagej")` is the right handle:

- **No #2401 dependency.** `design/ij-plugins-spike.py` (check 3, line 145)
  already verified that `ij.IJ` "resolves on ANY MM build" — ImageJ1 core is on
  MM's classpath regardless of the plugin-classloader work. Unlike
  `PluginAccess`, this detection does not need a modern nightly.
- **Correct by construction.** It returns MM's actual root, so it works for
  non-standard install locations, unenumerated platforms, and renamed version
  directories for free — no heuristic, no per-OS path list.

Secondary / rejected alternatives:

- `System.getProperty("user.dir")` — the JVM working directory. Usually the MM
  root (MM launches from its install dir), but a weaker guarantee than the
  purpose-built ImageJ call. Originally "keep only if the first returns empty";
  the lab run (see [Lab findings](#lab-findings-2026-07-03)) promoted it to an
  active fallback as a second independent probe once the `ij.IJ` route proved
  fragile over the bridge. Safe to fall back to because `_looks_like_mm_dir()`
  still validates it.
- `mmcorej.CMMCore` — does **not** expose an install path.

---

## Design: Java call preferred, path-guessing as fallback

The Java call requires a **live connection**; `find_mm_app_dir()` today is
pure-filesystem and works offline (`check_emu_installed` can be called before a
scope is connected). So we do not remove `_candidate_mm_dirs()` — we make the
Java call the first step in a fallback chain:

1. **Java call** (`ij.getDirectory("imagej")`) when `ctrl` is connected — authoritative.
2. **Cache** at `~/.microclaw/emu.json` (`"mm_app_dir"`).
3. **Path guessing** via `_candidate_mm_dirs()` — offline fallback.

A successful Java lookup also **writes through to the cache**, so subsequent
offline calls benefit from it.

To keep pycro-manager imports out of `emu_manager.py` (mirroring the
`PluginAccess` seam in `controller.py`), the actual Java call lives on
`MicroscopeController`; `find_mm_app_dir()` only receives the resolved path.

---

## Multiple MM installs

Locating a *strangely-placed* install is only half the motivation. The larger
correctness win is disambiguating **which of several installs is the one we are
actually driving**.

### The current bug

`_candidate_mm_dirs()` enumerates versioned defaults
(`Micro-Manager-2.0.1`, `2.0.2`, `2.0.3`, …) and `find_mm_app_dir()` returns the
**first** candidate for which `_has_emu()` passes. With two installs present,
this is not a "can't find it" failure — it is a **silent wrong answer**: if you
are running `2.0.3` but `2.0.1` also has EMU, path-guessing returns `2.0.1`, and
`read_emu_config` then parses the `EMU/config.uicfg` of an install you are *not*
running. Reported plugins and property mappings come from the wrong install.

### Why the Java call fixes it

`ij.getDirectory("imagej")` returns the root of the JVM microclaw is **connected
to and driving**. That is, by construction, the install whose `plugins/` loaded
the running htSMLM and whose `EMU/config.uicfg` is in effect. There is no
first-match choice to get wrong — the running instance names itself.

### Guarantee by connection state

| State | Multiple-install behaviour |
|---|---|
| **Connected** | Fully resolved. Step 1 always precedes the cache and path-guessing, so the running install's own answer wins — a stale cache or an ambiguous default can never override it. |
| **Offline** | Inherently ambiguous. Falls to cache (may be stale if the user switched installs) then first-match path-guessing (same ambiguity as today). |

Two things bound the offline risk: **cache write-through** means every connected
lookup overwrites the cache with the running install's path, so the cache
converges on "the install most recently driven"; and in practice
`check_emu_installed` / `get_emu_configuration` are used against a connected
scope. The residual truth is that **no purely-offline method can know which of
two installs is intended** — that fact lives only in the running JVM, so the
resolution for an ambiguous offline case is simply "connect, which refreshes the
cache."

---

## Code stubs

### `microclaw/controller.py` — new method on `MicroscopeController`

> **Updated after the lab run.** The original stub was a single
> `ij.IJ.getDirectory("imagej")` call. On a long-lived bridge it raised
> `AttributeError('java_lang_Class' object has no attribute 'get_directory')`
> — traced to a pyjavaz cache collision, not a flaky/aged bridge — see
> [Lab findings](#lab-findings-2026-07-03). The shipped version routes every
> static `JavaClass` through `_new_static_java_class()`, which evicts the
> colliding cache key, and falls back to `user.dir`.

```python
def _new_static_java_class(port: int, classpath: str):
    """Create a JavaClass for static access, around a pyjavaz cache collision.

    pyjavaz caches each shadow class by the serialized Java class name, which
    for EVERY static JavaClass is "java.lang.Class" (pyjavaz marks a call
    static via `_java_class == "java.lang.Class"`). So all static-class
    shadows collide under one cache key: the first classpath wrapped in the
    process wins, and later JavaClass(...) calls return its static methods.
    Evict the key so pyjavaz regenerates the shadow from this class's own
    serialized methods. Every static JavaClass in microclaw must go through
    here, or evicting for one call breaks the next site's call.
    """
    try:
        from pyjavaz.bridge import Bridge
        ref = Bridge._cached_bridges_by_port.get(port)
        bridge = ref() if ref is not None else None
        if bridge is not None:
            bridge._class_factory.classes.pop("java.lang.Class", None)
    except Exception:
        pass
    from pycromanager import JavaClass
    return JavaClass(classpath, port=port)

def get_mm_app_dir(self) -> str | None:
    """Return MM's install root by asking the running JVM, or None.

    Primary probe: ij.IJ.getDirectory("imagej") (resolves on ANY MM build,
    no #2401 needed — see design/ij-plugins-spike.py check 3). Secondary:
    the JVM working dir (System user.dir), since MM launches from its
    install root. Returns None if not connected or both probes fail; the
    raw answer is validated by find_mm_app_dir()'s _looks_like_mm_dir().
    """
    if not self.is_connected():
        return None
    raw = self._probe_imagej_dir() or self._probe_user_dir()
    if not raw:
        return None
    # ImageJ returns a trailing-slash path string; normalise for Path use.
    return str(Path(raw))

def _probe_imagej_dir(self) -> str | None:
    """ij.IJ.getDirectory("imagej") — MM's ImageJ install root."""
    try:
        ij = _new_static_java_class(self._port, "ij.IJ")
        getdir = getattr(ij, "get_directory", None) or getattr(
            ij, "getDirectory", None)
        if getdir is None:
            return None
        return getdir("imagej") or None
    except Exception:
        return None

def _probe_user_dir(self) -> str | None:
    """Secondary probe: the JVM working directory (System user.dir)."""
    try:
        system = _new_static_java_class(self._port, "java.lang.System")
        getprop = getattr(system, "get_property", None) or getattr(
            system, "getProperty", None)
        if getprop is None:
            return None
        return getprop("user.dir") or None
    except Exception:
        return None
```

### `microclaw/emu_manager.py` — chain the Java call first

**Why live before cache (not cache first).** The cache is only a remembered
*past* answer, and it goes stale in exactly the cases that matter — the user
switched which MM they launch, upgraded to a new version dir, or moved/deleted
the install. The live call reports the install we are *actually driving now*, so
whenever a live answer exists it must win. Checking the cache first would
reintroduce the silent-wrong-answer bug: a stale entry pointing at the *old*
install would beat the running instance and we'd parse the wrong
`config.uicfg` — while connected, the one case we most want bulletproof. So the
cache's only real job is **offline operation**: it is a "last-known-good while
connected" snapshot, kept fresh by the write-through in step 1, that lets the
EMU tools answer before a scope is connected. Live is the source of truth; the
cache is its offline echo.

**Validate the live answer.** We trust ImageJ's answer over the cache, so a
bogus live answer would be actively harmful (see the open question about
`getDirectory("imagej")` possibly returning a user-home ImageJ dir). Guard step 1
with a light sanity check that the returned dir *looks like* an MM root — it
contains `mmplugins/` or `plugins/`. If it fails the check we fall through to
cache/guessing rather than caching and returning a wrong path. This is
deliberately weaker than `_has_emu()`: a valid MM install without EMU yet is
still the correct `mm_app_dir`, so we must not require EMU on the live answer.

```python
def _looks_like_mm_dir(p: Path) -> bool:
    """True if p has the shape of an MM install root (has a plugins dir).

    Deliberately weaker than _has_emu(): a valid MM without EMU is still a
    correct mm_app_dir. Used only to sanity-check the live Java answer before
    trusting it over the cache.
    """
    return (p / "mmplugins").is_dir() or (p / "plugins").is_dir()


def find_mm_app_dir(ctrl: "MicroscopeController | None" = None) -> Path | None:
    """Return the µManager app directory.

    Order: (1) authoritative Java call via ctrl, (2) cache, (3) path guessing.
    ctrl is optional so offline callers keep working.
    """
    # 1. Ask the running MM JVM (authoritative). Validate before trusting it
    #    over the cache, then write through so offline calls stay fresh.
    if ctrl is not None:
        app_dir = ctrl.get_mm_app_dir()
        if app_dir:
            p = Path(app_dir)
            if p.exists() and _looks_like_mm_dir(p):
                save_mm_app_dir(str(p))
                return p
            # Bogus live answer (e.g. user-home ImageJ dir): fall through.

    # 2. Cache.
    if _EMU_CACHE.exists():
        try:
            cached = json.loads(_EMU_CACHE.read_text())
            cached_dir = cached.get("mm_app_dir")
            if cached_dir and Path(cached_dir).exists():
                return Path(cached_dir)
        except (json.JSONDecodeError, OSError):
            pass

    # 3. Path guessing (offline fallback).
    for candidate in _candidate_mm_dirs():
        if _has_emu(candidate):
            return candidate

    return None
```

> Note: the live answer is sanity-checked with `_looks_like_mm_dir()` (has a
> `plugins/` or `mmplugins/` dir), **not** `_has_emu()` — a valid MM install
> without EMU yet is still the correct `mm_app_dir`. The stricter `_has_emu()`
> gate applies only to the *guessed* paths in step 3, where we have no live JVM
> vouching for the directory.

### `microclaw/tools.py` — thread `ctrl` into the two call sites

```python
# check_emu_installed(...)
mm_dir = find_mm_app_dir(ctrl)          # was: find_mm_app_dir()

# get_emu_configuration(...)
found = find_mm_app_dir(ctrl)           # was: find_mm_app_dir()
```

Both tools already receive `ctrl`, so this is a one-argument change at each site.
The `_candidate_mm_dirs()` reference in `get_emu_configuration`'s error branch
(the `searched_paths` list) stays as-is — it's still an accurate description of
what the offline fallback tried.

---

## Behavioural notes

- **Offline still works.** With `ctrl=None` or a disconnected scope, the chain
  degrades to today's cache-then-guess behaviour. No regression.
- **Non-standard installs now resolve automatically** whenever a scope is
  connected — the main win. The user no longer has to pass
  `get_emu_configuration(mm_app_dir=...)` on unusual layouts.
- **Cache write-through** means the first connected call primes the cache, so a
  later offline `check_emu_installed` finds the right dir without re-guessing.

## Lab findings (2026-07-03)

Verified against the live Windows lab bridge. Both original open questions are
answered — **and** a real pyjavaz bug surfaced that took three lab runs plus a
read of pyjavaz's source to pin down. The dead-ends are recorded here because
each looked convincing and the next reader should not re-walk them.

**Question 1+2 (answered):** run in isolation (`pytest -m integration -k
mm_app_dir`) both tests passed — `get_directory` (snake_case) maps correctly and
returns the MM root (passes `_looks_like_mm_dir()`, not a user-home ImageJ dir).
So the fast route is correct *when it runs first*.

**The failure and the two wrong theories.** In the full suite the same two tests
failed:

```
AttributeError: 'java_lang_Class' object has no attribute 'get_directory'
```

- *Wrong theory 1 — "aged/incomplete method table."* Because `headless_mm` is
  session-scoped and these tests run last (after ~60 tests), it looked like a
  bridge that degrades under load / large classes. Fix attempted: retry with a
  fresh proxy + accept the camelCase alias. **Next lab run still failed** — and
  the diagnostic dump killed the theory: *all four* probes were missing,
  including `java.lang.System.getProperty`, a trivial core class. Not a
  size/load problem.

- *Root cause (confirmed by reading `pyjavaz/bridge.py`).* pyjavaz's
  `_JavaClassFactory` caches each generated shadow class **keyed by the
  serialized Java class name**, and for *every* static `JavaClass` that name is
  `"java.lang.Class"` — pyjavaz literally branches on
  `static = _java_class == "java.lang.Class"`. So all static-class shadows
  collide under one cache key: **the first classpath wrapped in the process wins,
  and every later `JavaClass(...)` returns that first class's static methods.**
  Isolation passed because `ij.IJ` was wrapped first; the full suite failed
  because `StagePosition` (position tests) was wrapped first, so `ij.IJ` and
  `System` inherited *its* methods and had no `get_directory`/`getProperty`.
  Reproduced deterministically off the bridge by driving two fake `get-class`
  payloads through `_JavaClassFactory.create`.

**Resolution (shipped):** a `_new_static_java_class(port, classpath)` helper
evicts the colliding `"java.lang.Class"` cache key before each static
`JavaClass`, forcing pyjavaz to regenerate the shadow from *that* class's own
serialized methods (the `get-class` round-trip happens every call anyway, so the
cost is just regenerating the Python class). **All** static `JavaClass` sites in
`controller.py` route through it — the plugin-loader probe and `StagePosition`
factory too, not just the ImageJ probes — because evicting for one call would
otherwise leave that class cached and break the next site. This also fixes a
latent, pre-existing ordering bug those two sites had. `get_mm_app_dir()` still
falls back to `System user.dir`, and `_looks_like_mm_dir()` validates whichever
probe answers, so the weaker fallback cannot cache a non-MM dir.

## Testing

- Unit: monkeypatch a fake `ctrl.get_mm_app_dir()` returning a tmp dir → assert
  `find_mm_app_dir(ctrl)` prefers it and writes the cache.
- Unit: `ctrl=None` and disconnected `ctrl` → assert the chain falls through to
  cache then `_candidate_mm_dirs()` exactly as before.
- Unit: `ctrl.get_mm_app_dir()` returns a real dir that lacks `plugins/` and
  `mmplugins/` → assert `_looks_like_mm_dir()` rejects it, nothing is cached, and
  the chain falls through to cache/guessing (the bogus-live-answer guard).
- Unit (controller): fake `JavaClass` covering `get_mm_app_dir()` —
  snake_case and camelCase resolution, `user.dir` fallback when `ij.IJ` resolves
  to the wrong (collision) shadow, both probes failing → `None`, and
  disconnected → `None` (never touches `JavaClass`). Plus a focused test that
  `_new_static_java_class()` evicts the `"java.lang.Class"` key off the live
  bridge's class factory.
- Integration (lab machine): connected scope resolves the real install root with
  no cache primed. On failure the test dumps each probe through the eviction
  helper (`ij.IJ` `get_directory`/`getDirectory`, `System` `user.dir`) plus the
  raw un-evicted proxy type, so one run names the broken probe.
