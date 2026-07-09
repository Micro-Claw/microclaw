#!/usr/bin/env python
"""Spike for design/14: verify V1/V2/V3 + the §2a EMU-parser claims on the MM demo.

design/14's appendix flags three assumptions that must be verified before its
code stubs are trusted. All are testable on the DEMO config (no lasers, no real
stages) plus an EMU plugin with a default-ish config.uicfg:

  V1 (§7)  studio.live().snap(True) returns List<Image> over ZMQ and the raw
           pixels round-trip through pyjavaz (-> snap_and_analyze can display
           and analyze ONE exposure). Also captures the exact Java error text
           of core.snap_image() under live view (for errors.py hint matching),
           and whether live().snap(True) itself tolerates live mode.
  V2 (§11) which accessor turns a mmcorej_PropertyType proxy into an int
           (swig_value / value / int(...)), and the ordinal->name mapping,
           checked against demo properties of KNOWN type
           (Exposure=Float, Binning=Integer/String?, CameraName=String).
  V3 (§6)  core.get_position(label) / set_position(label, pos) /
           set_relative_position(label, delta) overload dispatch, and how to
           enumerate stage devices (mmcorej.DeviceType static access — three
           candidate routes, all subject to the pyjavaz static-class cache
           collision documented in CLAUDE.md / design/12).
  §2a      dump the RAW EMU config.uicfg properties and run BOTH the current
           _parse_properties and the proposed fixed parser against it, with
           counts for every claim in design/14 §2a (":: separator matches 0",
           " - On value"/" state N" leak, hyphenated device labels).
  §8 INFO  whether the demo camera's image content responds to XY stage motion
           at all (decides if calibrate_stage_to_camera can be demo-tested).

Run MANUALLY on the machine with the MM DEMO config OPEN (EMU plugin installed)
and the pycro-manager ZMQ server enabled:

    python 14-demo-spike.py
    python 14-demo-spike.py --port 4827 --mm-dir "C:/Program Files/Micro-Manager-2.0"

>>> Fires the (demo) camera several times, toggles live mode, and moves the   <<<
>>> demo Z by +1 um and XY by +20 um, restoring both. Touches nothing else.   <<<

ALL output (including raw payload dumps) is tee'd to a results file printed at
the end — pass that single file back for the design doc.

REV 2 (after demo run 1, 2026-07-08 11:36): run 1 answered V1, V2 and the §7
error-text question, then JAMMED — studio.live().snap(True) while live mode is
ON does not throw, it never returns. Consequence for §7: snap_and_analyze must
check is_live_mode_on() and pause live BEFORE calling live().snap(True); it can
never probe by calling. That probe is now the gated FINAL check.

REV 3 (after demo run 2, 2026-07-08 11:44): run 2 jammed again, somewhere
inside the mmcorej.DeviceType static-class check — and taught us two lessons
now baked in here:

  1. pyjavaz has ONE bridge per port for the whole process
     (Bridge._cached_bridges_by_port), and every call holds
     _communication_lock for the full request/response with an unbounded
     retry loop (bridge.py send_and_receive). A Java-side non-reply therefore
     blocks EVERY subsequent bridge call from ANY thread — a hang cannot be
     isolated or recovered in-process, only detected. (Rev 2's "fresh Studio
     in a worker thread gets its own socket" was wrong.)
  2. Checks that buffer their findings and print at the end tell you NOTHING
     when they hang. Every remaining-unknown check now step()-logs each bridge
     call BEFORE making it, and a WATCHDOG thread converts any hang into a
     recorded HANG result + summary + clean process exit (no jammed terminal).
     After a hang, kill nothing, just re-run: a fresh process reconnects fine
     (observed after run 1's jam); the stuck Java handler thread lingers
     harmlessly. Stop live view manually in MM if it was left running.

  Checks are now ordered so the known/likely hangers run LAST: everything
  proven in runs 1-2 first, then file-based §2a, then low-risk method-call
  probes, then the DeviceType static class (run 2's jam site, now split into
  step-logged sub-probes), then the gated live-snap deadlock re-probe.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path

try:
    import numpy as np
    from pycromanager import Core, Studio
except Exception as exc:  # pragma: no cover - environment guard
    raise SystemExit("pycromanager and numpy are required. Import failed: %r" % exc)

DEFAULT_OUT = Path(__file__).with_name("14-demo-spike-output.txt")

# --------------------------------------------------------------------------- #
# Tee: everything printed also lands in the results file
# --------------------------------------------------------------------------- #
class _Tee:
    def __init__(self, path: Path):
        self._file = open(path, "w", encoding="utf-8")
        self._stdout = sys.stdout

    def write(self, s: str) -> None:
        self._stdout.write(s)
        self._file.write(s)
        self._file.flush()          # a hang must not strand buffered output

    def flush(self) -> None:
        self._stdout.flush()
        self._file.flush()


# --------------------------------------------------------------------------- #
# Result harness + watchdog. The watchdog is the rev-3 anti-jam mechanism: a
# bridge hang is unrecoverable in-process (see docstring), so on timeout we
# record HANG with the last step()-logged call, summarize, and os._exit.
# --------------------------------------------------------------------------- #
_RESULTS: list[tuple[str, str, str]] = []  # (status, name, detail)
_WATCH = {"name": None, "step": None, "deadline": 0.0}
_OUT_PATH: Path | None = None


def record(status: str, name: str, detail: str = "") -> None:
    _RESULTS.append((status, name, detail))
    line = f"[{status:4}] {name}"
    if detail:
        line += f"\n         {detail}"
    print(line, flush=True)


def step(msg: str) -> None:
    """Log a sub-step BEFORE the bridge call it names, so a hang is attributable."""
    _WATCH["step"] = msg
    print(f"    step: {msg}", flush=True)


def _watchdog_loop() -> None:
    while True:
        time.sleep(1.0)
        if _WATCH["name"] is not None and time.time() > _WATCH["deadline"]:
            record("HANG", _WATCH["name"],
                   f"no response from the ZMQ bridge; last step: {_WATCH['step']!r}. "
                   "The Java-side handler did not reply — the bridge is wedged for "
                   "this process (single bridge per port, communication lock held). "
                   "Exiting cleanly instead of jamming the terminal.")
            print("\nWATCHDOG: the process will now exit. To recover: stop live "
                  "view in MM if it is running, then simply re-run the spike — a "
                  "fresh process reconnects fine (observed after run 1's jam).",
                  flush=True)
            summarize(_OUT_PATH or DEFAULT_OUT)
            sys.stdout.flush()
            os._exit(2)


class SkipSpike(Exception):
    pass


def check(name: str, timeout_s: float = 45.0):
    def wrap(fn):
        print(f"\n--- {name} ---", flush=True)
        _WATCH.update(name=name, step="(check start)", deadline=time.time() + timeout_s)
        try:
            detail = fn()
            record("PASS", name, "" if detail is None else str(detail))
        except SkipSpike as s:
            record("SKIP", name, str(s))
        except Exception as exc:
            record("FAIL", name, f"{type(exc).__name__}: {exc}")
            traceback.print_exc(file=sys.stdout)
        finally:
            _WATCH.update(name=None, step=None)
        return fn
    return wrap


# --------------------------------------------------------------------------- #
# Bridge helpers (inlined from microclaw/controller.py so the spike is
# self-contained; see that file for the full pyjavaz cache-collision story)
# --------------------------------------------------------------------------- #
def new_static_java_class(port: int, classpath: str):
    """JavaClass for STATIC access, evicting the colliding pyjavaz cache key
    ("java.lang.Class") first — otherwise the first static class wrapped in the
    process wins and every later one silently gets its methods (design/12)."""
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


def drain(java_iterable) -> list[str]:
    """String elements of a Java collection / StrVector / python list."""
    if isinstance(java_iterable, (list, tuple, set)):
        return [str(x) for x in java_iterable]
    if hasattr(java_iterable, "iterator"):
        it = java_iterable.iterator()
        has_next = getattr(it, "has_next", None) or getattr(it, "hasNext")
        out = []
        while has_next():
            out.append(str(it.next()))
        return out
    if hasattr(java_iterable, "size") and hasattr(java_iterable, "get"):
        return [str(java_iterable.get(i)) for i in range(int(java_iterable.size()))]
    raise TypeError(f"Cannot drain {type(java_iterable).__name__}")


def probe_attrs(obj, names: list[str]):
    """Return (resolved_name, bound_attr) for the first name that resolves."""
    for n in names:
        attr = getattr(obj, n, None)
        if attr is not None:
            return n, attr
    return None, None


# --------------------------------------------------------------------------- #
# §11 / V2 — PropertyType proxy -> int accessor probe
# --------------------------------------------------------------------------- #
def probe_enum_accessors(raw) -> dict:
    """Try every plausible way to get an int (and a name) out of an enum proxy."""
    out: dict = {"repr": repr(raw)[:120], "str": str(raw)[:120]}
    for acc in ("swig_value", "swigValue", "value", "ordinal", "to_string", "toString", "name"):
        fn = getattr(raw, acc, None)
        if fn is None:
            out[acc] = "<absent>"
        else:
            try:
                out[acc] = repr(fn())
            except Exception as e:
                out[acc] = f"<raises {type(e).__name__}: {e}>"
    try:
        out["int()"] = repr(int(raw))
    except Exception as e:
        out["int()"] = f"<raises {type(e).__name__}>"
    # design/14 §11: this is the CURRENT tools.py:260 expression — expected to
    # produce a sliced repr with a heap address on the ZMQ backend.
    out["CURRENT tools.py expr"] = str(raw).split(".")[-1][:120]
    return out


# --------------------------------------------------------------------------- #
# §2a — proposed fixed parser (from the design/14 appendix), inlined
# --------------------------------------------------------------------------- #
_ON_OFF_RE = re.compile(r"^(?P<base>.+?) - (?P<which>On|Off) value$")
_STATE_RE = re.compile(r"^(?P<base>.+?) state (?P<idx>\d+)$")
_RESCALE_SUFFIXES = (" slope", " offset")
_PLACEHOLDER = {"Unallocated", "Enter value"}


def split_device_property(mm_str: str, device_labels: list[str]):
    """Longest-device-prefix split (device labels themselves contain hyphens)."""
    matches = [d for d in device_labels if mm_str.startswith(d + "-")]
    if not matches:
        return None
    device = max(matches, key=len)
    return device, mm_str[len(device) + 1:]


def parse_properties_proposed(raw: dict[str, str], device_labels: list[str]) -> dict:
    base: dict[str, str] = {}
    meta: dict[str, dict] = {}
    for key, value in raw.items():
        if (m := _ON_OFF_RE.match(key)):
            meta.setdefault(m["base"], {})[m["which"].lower()] = value
        elif (m := _STATE_RE.match(key)):
            meta.setdefault(m["base"], {}).setdefault("states", {})[int(m["idx"])] = value
        elif key.endswith(_RESCALE_SUFFIXES):
            suffix = next(s for s in _RESCALE_SUFFIXES if key.endswith(s))
            meta.setdefault(key[: -len(suffix)], {})[suffix.strip()] = value
        else:
            base[key] = value
    result = {}
    for name, mm_str in base.items():
        entry: dict = {"mm_property_string": mm_str}
        if mm_str not in _PLACEHOLDER and (split := split_device_property(mm_str, device_labels)):
            entry["device"], entry["property"] = split
        entry.update(meta.get(name, {}))
        result[name] = entry
    return result


def find_emu_config(core, port: int, mm_dir_arg: str | None) -> Path | None:
    """Locate EMU/config.uicfg: --mm-dir, then microclaw's finder, then ij.IJ."""
    candidates: list[Path] = []
    if mm_dir_arg:
        candidates.append(Path(mm_dir_arg))
    try:  # use the installed package's finder if available
        from microclaw.emu_manager import find_mm_app_dir
        p = find_mm_app_dir(None)
        if p:
            candidates.append(Path(p))
    except Exception:
        pass
    # Only ask the JVM if the offline routes found nothing: JavaClass("ij.IJ")
    # is a get-class request, the same operation family as run 2's jam site.
    if not any((c / "EMU" / "config.uicfg").exists() for c in candidates):
        try:
            step("JavaClass('ij.IJ') get-class probe for the MM app dir "
                 "(skippable: pass --mm-dir to avoid this bridge call)")
            ij = new_static_java_class(port, "ij.IJ")
            getdir = getattr(ij, "get_directory", None) or getattr(ij, "getDirectory", None)
            if getdir:
                step("ij.IJ.get_directory('imagej')")
                raw = getdir("imagej")
                if raw:
                    candidates.append(Path(str(raw)))
        except Exception:
            pass
    for c in candidates:
        cfg = c / "EMU" / "config.uicfg"
        if cfg.exists():
            return cfg
    return None


# --------------------------------------------------------------------------- #
def main() -> None:
    global _OUT_PATH
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=4827, help="ZMQ server port")
    ap.add_argument("--mm-dir", default=None,
                    help="MM install root (containing EMU/config.uicfg); "
                         "autodetected if omitted")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="results file (default: next to this script)")
    ap.add_argument("--probe-live-snap", action="store_true",
                    help="re-probe live().snap(True) under live mode "
                         "(KNOWN to deadlock the bridge; watchdog will exit)")
    args = ap.parse_args()
    port = args.port
    out_path = Path(args.out)
    _OUT_PATH = out_path

    sys.stdout = _Tee(out_path)
    sys.stderr = sys.stdout
    threading.Thread(target=_watchdog_loop, daemon=True,
                     name="spike-watchdog").start()

    print(f"design/14 demo spike (rev 3) — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Connecting to Micro-Manager ZMQ server on port {port} ...", flush=True)

    # 0. Connect --------------------------------------------------------------
    try:
        core = Core(port=port)
        studio = Studio(port=port)
        record("PASS", "0. connect Core + Studio", str(core.get_version_info()))
    except Exception as exc:
        record("FAIL", "0. connect Core + Studio", f"{type(exc).__name__}: {exc}")
        print("\nCannot continue. Is MM open with the ZMQ server enabled?", flush=True)
        summarize(out_path)
        return

    state: dict = {}  # shared between checks (bound before @check runs anything)

    # ========== proven in runs 1-2; kept so one file holds everything ========
    @check("1. device roles + loaded devices")
    def _c1():
        cam = str(core.get_camera_device())
        focus = str(core.get_focus_device())
        xy = str(core.get_xy_stage_device())
        devices = drain(core.get_loaded_devices())
        state.update(cam=cam, focus=focus, xy=xy, devices=devices)
        return f"camera={cam!r} focus={focus!r} xy={xy!r}\n         loaded={devices}"

    @check("2. V2/§11: PropertyType proxy accessor probe (known-typed props)")
    def _c2():
        if "cam" not in state:
            raise SkipSpike("no camera device")
        cam = state["cam"]
        # Runs 1-2: swig_value() -> int (String=1 Float=2 Integer=3) and
        # to_string() -> the name directly ('Float'). Re-confirmed for the record.
        lines = []
        ordinals = {}
        for prop in ("Exposure", "Binning", "CameraName"):
            try:
                raw = core.get_property_type(cam, prop)
            except Exception as e:
                lines.append(f"{prop}: get_property_type raised {type(e).__name__}: {e}")
                continue
            probe = probe_enum_accessors(raw)
            lines.append(f"{prop}:")
            for k, v in probe.items():
                lines.append(f"    {k:24} = {v}")
            for acc in ("swig_value", "swigValue", "value", "int()"):
                v = probe.get(acc, "")
                if isinstance(v, str) and v.isdigit():
                    ordinals[prop] = (acc, int(v))
                    break
        state["prop_type_ordinals"] = ordinals
        lines.append(f"WORKING int accessors: {ordinals}")
        return "\n         ".join(lines)

    @check("3. baseline: core snap path (snap_to_numpy equivalent) — FIRES CAMERA")
    def _c3():
        try:
            studio.live().set_live_mode_on(False)
        except Exception:
            pass
        core.snap_image()
        tagged = core.get_tagged_image()
        w, h = int(tagged.tags["Width"]), int(tagged.tags["Height"])
        bpp = int(core.get_bytes_per_pixel())
        n_comp = int(core.get_number_of_components())
        dtype = {1: np.uint8, 2: np.uint16, 4: np.uint32}[bpp] if n_comp == 1 else np.uint8
        arr = np.frombuffer(tagged.pix, dtype=dtype)
        state.update(w=w, h=h, bpp=bpp, core_mean=float(arr.mean()))
        return f"w={w} h={h} bpp={bpp} n_comp={n_comp} mean={arr.mean():.1f}"

    @check("4. V1/§7: studio.live().snap(True) — List<Image> + raw pixel round-trip")
    def _c4():
        live = studio.live()
        images = live.snap(True)   # should ALSO display in the MM viewer
        lines = [f"snap(True) returned: {type(images).__name__}"]
        if isinstance(images, (list, tuple)):
            img = images[0]
            lines.append(f"python list, len={len(images)}")
        else:
            size_name, size_fn = probe_attrs(images, ["size", "get_size"])
            get_name, get_fn = probe_attrs(images, ["get"])
            if get_fn is None:
                raise RuntimeError(f"no element access on {type(images).__name__}")
            n = int(size_fn()) if size_fn else -1
            img = get_fn(0)
            lines.append(f"java List proxy: {size_name}()={n}, element via {get_name}(0)")
        resolved = {}
        for logical, names in {
            "width": ["get_width", "getWidth"],
            "height": ["get_height", "getHeight"],
            "bytes_per_pixel": ["get_bytes_per_pixel", "getBytesPerPixel"],
            # run 2: the DefaultImage accessor is get_num_components
            "n_components": ["get_num_components", "get_number_of_components"],
            "raw_pixels": ["get_raw_pixels", "getRawPixels", "get_raw_pixels_copy"],
        }.items():
            name, _fn = probe_attrs(img, names)
            resolved[logical] = name
        lines.append(f"Image accessors resolved: {resolved}")
        if resolved["raw_pixels"] is None or resolved["width"] is None:
            raise RuntimeError("Image accessors missing")
        w = int(getattr(img, resolved["width"])())
        h = int(getattr(img, resolved["height"])())
        bpp = int(getattr(img, resolved["bytes_per_pixel"])())
        pix = getattr(img, resolved["raw_pixels"])()
        lines.append(f"w={w} h={h} bpp={bpp} pixels type={type(pix).__name__}")
        arr = None
        for conv, label in ((lambda p: np.asarray(p), "np.asarray"),
                            (lambda p: np.frombuffer(bytes(p),
                             dtype={1: np.uint8, 2: np.uint16}[bpp]), "np.frombuffer(bytes)")):
            try:
                a = conv(pix)
                if a.size == w * h:
                    arr = a.reshape(h, w)
                    lines.append(f"pixel round-trip via {label}: dtype={arr.dtype} "
                                 f"shape={arr.shape} mean={float(arr.mean()):.1f} "
                                 f"(core-path mean was {state.get('core_mean', float('nan')):.1f})")
                    break
            except Exception as e:
                lines.append(f"{label} failed: {type(e).__name__}: {e}")
        if arr is None:
            raise RuntimeError("raw pixels did not round-trip")
        lines.append(">>> EYEBALL CHECK: did the MM Snap/Live window just update? "
                     "Note YES/NO when you send the output back. <<<")
        return "\n         ".join(lines)

    @check("5. §7: core.snap_image() while live view is ON (expected to FAIL in Java)")
    def _c5():
        live = studio.live()
        live.set_live_mode_on(True)
        time.sleep(1.0)
        try:
            core.snap_image()
            return "NO exception — core.snap_image() tolerated live mode?!"
        except Exception as e:
            text = str(e)
            needle = "sequence acquisition is running"
            return (f"threw {type(e).__name__}; needle {needle!r} present={needle in text}\n"
                    f"         first 300 chars: {text[:300]}")
        finally:
            # Live must go OFF here — leaving it on is what set up run 1's
            # live().snap deadlock (now the gated final check).
            live.set_live_mode_on(False)
            time.sleep(0.3)

    @check("6. §7: is_live_mode_on / set_live_mode_on round-trip")
    def _c6():
        live = studio.live()
        live.set_live_mode_on(True)
        time.sleep(0.5)
        on = bool(live.is_live_mode_on())
        live.set_live_mode_on(False)
        time.sleep(0.3)
        off = bool(live.is_live_mode_on())
        if not on or off:
            raise RuntimeError(f"round-trip broken: after ON read {on}, after OFF read {not off}")
        return "ON->True, OFF->False (the _pause_live context manager primitive works)"

    # ========== NOT yet answered — ordered safest-first from here ============

    @check("7. §2a: EMU config.uicfg — raw dump + current vs proposed parser")
    def _c7():
        step("locating config.uicfg (offline routes first)")
        cfg_path = find_emu_config(core, port, args.mm_dir)
        if cfg_path is None:
            raise SkipSpike("EMU/config.uicfg not found — pass --mm-dir explicitly")
        raw = json.loads(cfg_path.read_text())
        lines = [f"config file: {cfg_path}"]
        configs = raw.get("pluginConfigurations", [])
        default_name = raw.get("defaultConfigurationName", "")
        lines.append(f"defaultConfigurationName={default_name!r}; "
                     f"configurations={[c.get('configurationName') for c in configs]}")
        active = next((c for c in configs if c.get("configurationName") == default_name),
                      configs[0] if configs else {})
        props: dict = active.get("properties", {})
        lines.append(f"plugin={active.get('pluginName')!r}, {len(props)} property entries")

        n = len(props)
        with_sep = sum(1 for v in props.values() if "::" in str(v))
        onoff = sum(1 for k in props if _ON_OFF_RE.match(k))
        staten = sum(1 for k in props if _STATE_RE.match(k))
        old_sfx = sum(1 for k in props
                      if k.endswith((" on", " off", " slope", " offset"))
                      and not _ON_OFF_RE.match(k))
        placeholders = sum(1 for v in props.values() if str(v) in _PLACEHOLDER)
        lines.append(f"CLAIM CHECKS: total={n}, values containing '::'={with_sep}, "
                     f"' - On/Off value' keys={onoff}, ' state N' keys={staten}, "
                     f"old-code suffixes (' on'/' off'/' slope'/' offset')={old_sfx}, "
                     f"placeholder values={placeholders}")

        try:
            from microclaw.emu_manager import _parse_properties as current_parse
            cur = current_parse(props)
            cur_dev = sum(1 for v in cur.values() if "device" in v)
            leaked = sum(1 for k in cur if _ON_OFF_RE.match(k) or _STATE_RE.match(k))
            lines.append(f"CURRENT _parse_properties: {len(cur)} entries, "
                         f"device populated on {cur_dev}, leaked metadata keys={leaked}")
        except Exception as e:
            lines.append(f"CURRENT parser not importable/failed: {type(e).__name__}: {e}")

        devices = state.get("devices") or []
        prop_parsed = parse_properties_proposed(props, devices)
        allocated = {k: v for k, v in prop_parsed.items()
                     if v["mm_property_string"] not in _PLACEHOLDER}
        dev_ok = sum(1 for v in allocated.values() if "device" in v)
        unmatched = sorted(v["mm_property_string"] for v in allocated.values()
                           if "device" not in v)
        lines.append(f"PROPOSED parser: {len(prop_parsed)} top-level entries "
                     f"({len(allocated)} allocated), device resolved on "
                     f"{dev_ok}/{len(allocated)}")
        if unmatched:
            lines.append(f"UNRESOLVED against live device list {devices}:")
            for u in unmatched[:20]:
                lines.append(f"    {u!r}")

        lines.append("RAW-EMU-PROPERTIES-BEGIN")
        lines.append(json.dumps(props, indent=1, sort_keys=True))
        lines.append("RAW-EMU-PROPERTIES-END")
        lines.append("PROPOSED-PARSE-BEGIN")
        lines.append(json.dumps(prop_parsed, indent=1, sort_keys=True, default=str))
        lines.append("PROPOSED-PARSE-END")
        return "\n         ".join(lines)

    @check("8. misc: pixel size + get_roi() field access")
    def _c8():
        step("core.get_pixel_size_um()")
        lines = [f"get_pixel_size_um() = {float(core.get_pixel_size_um())}"]
        step("core.get_roi()")
        roi = core.get_roi()
        lines.append(f"get_roi() -> {type(roi).__name__}")
        for field in ("x", "y", "width", "height"):
            step(f"roi.{field} (Java field access)")
            try:
                lines.append(f"  roi.{field} = {getattr(roi, field)}")
            except Exception as e:
                _, fn = probe_attrs(roi, [f"get_{field}", f"get{field.capitalize()}"])
                lines.append(f"  roi.{field} raises {type(e).__name__}; "
                             f"getter -> {fn() if fn else 'NONE RESOLVED'}")
        return "\n         ".join(lines)

    @check("9. V3/§6: get/set_position(label) overloads — MOVES DEMO Z +1um, restores")
    def _c9():
        focus = state.get("focus")
        if not focus:
            raise SkipSpike("no focus device")
        lines = []
        step(f"core.get_position({focus!r}) labelled GET overload")
        z0 = float(core.get_position(focus))
        lines.append(f"get_position({focus!r}) = {z0:.3f} "
                     f"(unlabelled get_position() = {float(core.get_position()):.3f})")
        step(f"core.set_position({focus!r}, z0+1.0) labelled SET overload")
        core.set_position(focus, z0 + 1.0)
        core.wait_for_device(focus)
        z1 = float(core.get_position(focus))
        lines.append(f"set_position({focus!r}, z0+1.0) -> readback {z1:.3f} "
                     f"(moved={abs(z1 - z0 - 1.0) < 0.2})")
        step(f"core.set_relative_position({focus!r}, -1.0) labelled RELATIVE overload")
        try:
            core.set_relative_position(focus, -1.0)
            core.wait_for_device(focus)
            z2 = float(core.get_position(focus))
            lines.append(f"set_relative_position({focus!r}, -1.0) -> {z2:.3f} "
                         f"(restored={abs(z2 - z0) < 0.2})")
        except Exception as e:
            lines.append(f"set_relative_position(label, d) FAILED: {type(e).__name__}: "
                         f"{str(e)[:200]} — restoring via set_position")
            core.set_position(focus, z0)
            core.wait_for_device(focus)
        if abs(float(core.get_position(focus)) - z0) > 0.2:
            core.set_position(focus, z0)
            core.wait_for_device(focus)
        return "\n         ".join(lines)

    @check("10. V3/§6: core.get_device_type(label) per device (static-class-free route)")
    def _c10():
        # If this works, list_stages can classify every device WITHOUT touching
        # mmcorej.DeviceType (run 2's jam site): get_device_type returns the
        # same enum-proxy shape whose swig_value()/to_string() check 2 proved.
        devices = state.get("devices") or []
        if not devices:
            raise SkipSpike("no device list")
        lines = []
        for dev in devices:
            step(f"core.get_device_type({dev!r})")
            try:
                raw = core.get_device_type(dev)
                probe = probe_enum_accessors(raw)
                short = {k: probe[k] for k in ("swig_value", "to_string")
                         if not str(probe.get(k, "")).startswith("<")}
                lines.append(f"{dev}: {short or probe['repr']}")
            except Exception as e:
                lines.append(f"{dev}: {type(e).__name__}: {str(e)[:120]}")
        lines.append("(swig_value: StageDevice=5, XYStageDevice=6 per MMDeviceConstants; "
                     "if to_string() names them, list_stages needs no DeviceType class)")
        return "\n         ".join(lines)

    @check("11. §8 INFO: image response to XY motion (phase correlation) — MOVES XY +20um, restores")
    def _c11():
        try:
            from skimage.registration import phase_cross_correlation
        except Exception:
            raise SkipSpike("scikit-image not installed; pip install scikit-image to run this")
        xy = state.get("xy")
        if not xy:
            raise SkipSpike("no XY stage device")

        def snap():
            core.snap_image()
            t = core.get_tagged_image()
            w, h = int(t.tags["Width"]), int(t.tags["Height"])
            bpp = int(core.get_bytes_per_pixel())
            return np.frombuffer(t.pix, dtype={1: np.uint8, 2: np.uint16}[bpp]).reshape(h, w)

        step("core.get_x_position()/get_y_position()")
        x0, y0 = float(core.get_x_position()), float(core.get_y_position())
        step("snap (reference)")
        ref = snap()
        step("core.set_relative_xy_position(20, 0)")
        core.set_relative_xy_position(20.0, 0.0)
        core.wait_for_device(xy)
        step("snap (moved)")
        moved = snap()
        step("restore XY")
        core.set_xy_position(x0, y0)
        core.wait_for_device(xy)
        shift, err, _ = phase_cross_correlation(ref, moved, upsample_factor=10)
        return (f"20um X move -> pixel shift (row, col) = ({shift[0]:.2f}, {shift[1]:.2f}), "
                f"error={float(err):.3f}\n"
                "         (near-zero shift means the demo camera does NOT simulate "
                "stage translation and §8 calibrate_stage_to_camera must be "
                "validated on the lab rig, not the demo)")

    # ========== run 2's jam site, LAST among the real checks =================
    @check("12. V3/§6: mmcorej.DeviceType static class — RUN 2 JAMMED IN HERE", timeout_s=60.0)
    def _c12():
        focus = state.get("focus")
        if not focus:
            raise SkipSpike("no focus device from check 1")
        lines = []
        winners = []
        # Every sub-step is step()-logged BEFORE its bridge call: if this hangs
        # again, the HANG record names the exact call, the watchdog writes the
        # summary, and the process exits instead of jamming.
        dt = None
        step("new_static_java_class('mmcorej.DeviceType') — the get-class request")
        try:
            dt = new_static_java_class(port, "mmcorej.DeviceType")
            lines.append(f"JavaClass('mmcorej.DeviceType') OK: "
                         f"{[d for d in dir(dt) if not d.startswith('_')][:25]}")
        except Exception as e:
            lines.append(f"JavaClass('mmcorej.DeviceType') failed: {type(e).__name__}: {e}")

        def try_route(label, val_fn):
            try:
                step(f"{label}: resolve the value")
                val = val_fn()
                step(f"{label}: core.get_loaded_devices_of_type(...)")
                res = drain(core.get_loaded_devices_of_type(val))
                ok = focus in res
                lines.append(f"{label}: -> {res} (focus present={ok})")
                if ok:
                    winners.append(label)
            except Exception as e:
                lines.append(f"{label}: {type(e).__name__}: {str(e)[:200]}")

        if dt is not None:
            for meth in ("swig_to_enum", "swigToEnum"):
                if hasattr(dt, meth):
                    try_route(f"route A: DeviceType.{meth}(5)",
                              lambda m=meth: getattr(dt, m)(5))
                    break
            else:
                lines.append("route A: no swig_to_enum/swigToEnum on the static shadow")
            if "StageDevice" in dir(dt):
                # static FIELD read -> a get-field bridge request
                try_route("route B: DeviceType.StageDevice static field",
                          lambda: dt.StageDevice)
            else:
                lines.append("route B: no StageDevice attribute on the static shadow")
        try_route("route C: plain int 5", lambda: 5)
        lines.append(f"WORKING routes: {winners or 'NONE'}")
        if not winners:
            lines.append("(then list_stages must use check 10's per-device "
                         "get_device_type route, which avoids this class entirely)")
        return "\n         ".join(lines)

    # ========== gated: run 1's deadlock, re-probe only on request ============
    @check("13. §7: live().snap(True) while live ON — KNOWN DEADLOCK, gated", timeout_s=30.0)
    def _c13():
        if not args.probe_live_snap:
            raise SkipSpike(
                "run 1 (2026-07-08): this call never returns — and pyjavaz has ONE "
                "bridge per port, so the whole process wedges (rev 3 docstring). "
                "Finding stands: snap_and_analyze must pause live BEFORE "
                "live().snap(True). Pass --probe-live-snap to re-probe; the "
                "watchdog will record the hang and exit cleanly.")
        live = studio.live()
        step("set_live_mode_on(True)")
        live.set_live_mode_on(True)
        time.sleep(0.5)
        step("live().snap(True) with live ON — the deadlocking call; watchdog "
             "fires in ~30s if it hangs again")
        images = live.snap(True)
        got = "python list" if isinstance(images, (list, tuple)) else type(images).__name__
        step("set_live_mode_on(False)")
        live.set_live_mode_on(False)
        return f"snap(True) SUCCEEDED under live mode this time (returned {got})"

    summarize(out_path)


def summarize(out_path: Path) -> None:
    print("\n" + "=" * 68)
    print("DESIGN/14 DEMO SPIKE SUMMARY (rev 3)")
    print("=" * 68)
    for status, name, _ in _RESULTS:
        print(f"  {status:4}  {name}")
    counts: dict[str, int] = {}
    for status, _, _ in _RESULTS:
        counts[status] = counts.get(status, 0) + 1
    print("-" * 68)
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("=" * 68)
    print(
        "\nInterpretation map (design/14):\n"
        "  2       -> V2 (§11): swig_value()/to_string() confirmed in runs 1-2\n"
        "  4-6     -> V1 (§7):  confirmed in runs 1-2; kept for one complete file\n"
        "  7       -> §2a: parser claims measured on a real config + fixture data\n"
        "  8       -> §7/§9 stubs: get_roi() field access + pixel size\n"
        "  9       -> V3 (§6): labelled stage overloads (move_named_stage)\n"
        "  10      -> V3 (§6): static-class-free stage classification (list_stages)\n"
        "  11      -> §8:  whether calibration can be demo-tested at all\n"
        "  12      -> V3 (§6): DeviceType static class — run 2's jam site\n"
        "  13      -> §7:  live().snap under live = bridge deadlock (gated)\n"
        "\nIf a HANG is recorded above, the process exited itself — the terminal\n"
        "should NOT be jammed. Recovery: stop live view in MM if running, re-run.\n"
        "\nEYEBALL ITEM: when sending results back, note whether check 4 updated\n"
        "the MM Snap/Live window on screen (YES/NO).\n"
        f"\nFull output written to: {out_path}\n"
        "Send that file back as-is.\n")


if __name__ == "__main__":
    main()
