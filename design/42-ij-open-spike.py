#!/usr/bin/env python
"""Spike: can we open a file we wrote in the ImageJ that Micro-Manager runs under?

NOTHING HERE MOVES A STAGE, OPENS A SHUTTER, OR FIRES A CAMERA. Zero exposure.
It reads two files off disk, asks the running ImageJ to open them, and asks
ImageJ what windows exist. It never closes a window — see "Windows" below.

Run this MANUALLY on the rig (M5) with Micro-Manager OPEN and the pycro-manager
ZMQ server enabled (Tools > Options, port 4827 by default). PowerShell:

    python design\\42-ij-open-spike.py > out.txt 2>&1
    python design\\42-ij-open-spike.py --foreign "D:\\SSD\\some_file.nd2" > out.txt 2>&1
    python design\\42-ij-open-spike.py --only 6 > out6.txt 2>&1
    Get-Content out.txt

See design/42-block42a-rig-gate.md on this branch for the full runbook,
including what to do if it hangs.

WHY THIS IMPORTS microclaw (do not "fix" it)
--------------------------------------------
Every other probe in design/ is pycromanager + stdlib only, because design/29's
kit had to run for a remote operator whose microclaw install might not start.
This one is different ON PURPOSE: it imports `_new_static_java_class` from
`microclaw.controller` rather than copying it. The whole question here is
whether the SHIPPED helper keeps `ij.IJ` and `ij.WindowManager` apart over the
bridge (design/12's pyjavaz static-class cache collision). A copied helper is a
different code path, and could pass here while the shipped one fails on the rig
— the one outcome this spike must not produce.

WHAT IT CHECKS (design/42 "Spike first"; each check is isolated, and a failing
check never stops the ones after it)

  1. ij.IJ and ij.WindowManager both resolve through _new_static_java_class,
     wrapped in BOTH orders, each order in its own clean subprocess, each
     preceded by a decoy static wrap so a collision has something to collide
     with. Reports the METHOD SURFACE each shadow exposes, because the
     collision's signature is a shadow that exists and carries the wrong
     class's methods. Includes a CONTROL run with the eviction helper bypassed:
     if the control does not reproduce the collision, check 1 passing proves
     much less, and the summary says so.
  2. Does a shadow obtained BEFORE another static wrap still work AFTER it?
     Decides whether design/42's "re-wrap statics per call" is a rule or
     hygiene.
  3. IJ.open() on the mosaic TIFF: does a new window appear, and do
     WindowManager's dimensions match what tifffile reads from the same file?
     Both numbers are reported, not a verdict; a title match alone would be
     nearly self-confirming.
  5. IJ.open() on the NDTiff DIRECTORY. Expected to fail; the point is to
     record exactly how, so block 42b can refuse it with a reason.
  4. IJ.open() on a format IJ1 cannot read natively (--foreign). SKIPs with a
     reason if no path is given; a SKIP is a real result, a fabricated pass is
     not.
  6. Does the call block, and can it stall the bridge? pyjavaz holds ONE lock
     across every round trip (pyjavaz.bridge.Bridge.send_and_receive), so a
     modal Bio-Formats dialog would stall EVERY subsequent core call while a
     sample sits under illumination. Reports the wall time of IJ.open, whether
     WindowManager shows the window immediately with no sleep, and the wall
     time of a trivial core call straight after.

Checks run in the order 1, 2, 3, 5, 4, 6 — the two that can provoke a modal
dialog run last, so the answers to the others are already on disk. Every line
is flushed as it is produced.

STALLS. Each bridge call runs on a daemon thread with a watchdog, so a modal
dialog cannot hang this script silently: it prints a nudge, waits --timeout
seconds, then reports the stall as check 6's answer and exits. Once the pyjavaz
lock is held by a blocked call, nothing else can use the bridge, so the run
ends there — dismiss the dialog and re-run with `--only 6`.

WINDOWS. Every window this opens is LEFT OPEN, with its ID and title printed at
the end so you can close them by hand. Microclaw does not close the user's
windows, and a spike that did would be teaching the wrong thing.
"""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import textwrap
import threading
import time
import traceback

DEFAULT_MOSAIC = r"D:\SSD\stitch_test\stitch_test_mosaic.tiff"
DEFAULT_NDTIFF = r"D:\SSD\stitch_test\stitch_test_1"

# Imports are deferred into a guard so that --help works on a machine with no
# microclaw and no pycromanager. The operator will typo an argument, and a
# traceback at that point costs a round trip to the rig.
_MICROCLAW_IMPORT_ERROR = None
_PYCROMANAGER_IMPORT_ERROR = None
try:
    import microclaw
    from microclaw.controller import _new_static_java_class
except Exception as exc:  # pragma: no cover - rig environment guard
    microclaw = None
    _new_static_java_class = None
    _MICROCLAW_IMPORT_ERROR = exc
try:
    from pycromanager import Core, JavaClass
except Exception as exc:  # pragma: no cover - rig environment guard
    Core = JavaClass = None
    _PYCROMANAGER_IMPORT_ERROR = exc


# --------------------------------------------------------------------------- #
# Result harness (house style, design/ij-plugins-spike.py)
# --------------------------------------------------------------------------- #
# PASS  the property held.          FAIL  it did not.
# INFO  no pass/fail — the answer IS the detail (checks 4, 5, 6's raw numbers).
# SKIP  not run, with a reason.     ERROR the check itself blew up.
_RESULTS: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    _RESULTS.append((status, name, detail))
    line = f"[{status:5}] {name}"
    if detail:
        line += "\n" + textwrap.indent(str(detail).rstrip(), "         ")
    print(line, flush=True)


class SkipSpike(Exception):
    """Raise inside a check to record SKIP with a reason."""


class BridgeStalled(BaseException):
    """A bridge call did not return inside the watchdog window.

    Deliberately NOT an Exception: checks 4 and 5 catch Exception on purpose
    (they expect Java-side failures), and a stall must not be swallowed by
    one of them. It ends the run — see the module docstring.
    """


def check(name: str):
    """Run a check function; record PASS/FAIL/INFO/SKIP/ERROR.

    The function may return a string (PASS, detail) or a (status, detail)
    tuple, or raise SkipSpike. Any other exception is ERROR, never a silent
    pass. BridgeStalled propagates: nothing after it can use the bridge.
    """
    def wrap(fn):
        print(f"\n--- {name} ---", flush=True)
        try:
            out = fn()
            if isinstance(out, tuple) and len(out) == 2:
                record(str(out[0]), name, str(out[1]))
            else:
                record("PASS", name, "" if out is None else str(out))
        except SkipSpike as s:
            record("SKIP", name, str(s))
        except BridgeStalled:
            raise
        except Exception as exc:
            record("ERROR", name, f"{type(exc).__name__}: {exc}")
            sys.stdout.flush()
            traceback.print_exc()
        return fn
    return wrap


# --------------------------------------------------------------------------- #
# Bridge access, watchdogged
# --------------------------------------------------------------------------- #
def bridge_call(label: str, fn, timeout: float):
    """Run one bridge interaction on a daemon thread with a watchdog.

    pyjavaz serialises every round trip under a single lock and there is no way
    to interrupt one in flight, so a modal Java dialog blocks the calling thread
    forever. Running on a daemon thread lets us report that instead of hanging,
    and lets the process exit afterwards.

    Returns fn()'s value; re-raises whatever fn raised; raises BridgeStalled if
    it never returned.
    """
    box: dict = {}

    def runner():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            box["error"] = exc
            box["traceback"] = traceback.format_exc()

    t = threading.Thread(target=runner, name=f"bridge:{label}", daemon=True)
    t0 = time.perf_counter()
    t.start()
    nudged = False
    while t.is_alive():
        t.join(2.0)
        waited = time.perf_counter() - t0
        if not t.is_alive():
            break
        if waited >= timeout:
            raise BridgeStalled(
                f"{label!r} has not returned after {waited:.1f} s "
                f"(watchdog limit {timeout:.0f} s)")
        if waited >= 5.0:
            if not nudged:
                print(f"\n  *** {label!r} has not returned after {waited:.1f} s.",
                      flush=True)
                print("  *** LOOK AT THE MICRO-MANAGER SCREEN NOW: a modal ImageJ "
                      "or Bio-Formats dialog", flush=True)
                print("  *** blocks the single pyjavaz lock. Dismiss it and this "
                      "will continue.", flush=True)
                print(f"  *** Giving up at {timeout:.0f} s and reporting a stall.",
                      flush=True)
                nudged = True
            else:
                print(f"  ... still waiting on {label!r} ({waited:.1f} s)", flush=True)
    if "error" in box:
        print(box.get("traceback", ""), flush=True)
        raise box["error"]
    return box.get("value")


def timed(label: str, fn, timeout: float) -> tuple[object, float]:
    """bridge_call plus the wall time of the call itself."""
    t0 = time.perf_counter()
    value = bridge_call(label, fn, timeout)
    return value, time.perf_counter() - t0


# --------------------------------------------------------------------------- #
# Java shadow introspection
# --------------------------------------------------------------------------- #
def _norm(name: str) -> str:
    return name.lower().replace("_", "")


def method_names(obj) -> list[str]:
    return sorted(m for m in dir(obj) if not m.startswith("_"))


def resolve(obj, *java_names) -> tuple[str | None, object]:
    """Find a Java method on a bridge shadow whatever pyjavaz named it.

    pyjavaz snake_cases method names (getIDList -> get_id_list) but the exact
    split is version-dependent, so match on a normalised form and report the
    name that was actually found.
    """
    table = {_norm(m): m for m in method_names(obj)}
    for jn in java_names:
        attr = table.get(_norm(jn))
        if attr is not None:
            return attr, getattr(obj, attr)
    return None, None


def jmethod(obj, *java_names):
    attr, fn = resolve(obj, *java_names)
    if fn is None:
        raise AttributeError(
            f"none of {java_names} present on this shadow; "
            f"surface ({len(method_names(obj))} methods) = {method_names(obj)}")
    return fn


# (required, foreign) per class. "foreign" = methods this class does NOT have
# but the other one does; their presence is the cache collision's signature.
_SURFACE = {
    "ij.IJ": (
        ["open", "getDirectory", "getVersion", "log"],
        ["getIDList", "getNthImageID"],
    ),
    "ij.WindowManager": (
        ["getIDList", "getImageCount", "getImage"],
        ["open", "getDirectory", "runMacro"],
    ),
    "java.lang.System": (
        ["getProperty", "currentTimeMillis"],
        ["open", "getIDList", "getDirectory"],
    ),
}


def surface_report(classpath: str, obj) -> tuple[bool, str]:
    """Report what a shadow ACTUALLY exposes. True if it is the right class."""
    required, foreign = _SURFACE[classpath]
    names = method_names(obj)
    found, missing, contaminated = [], [], []
    for jn in required:
        attr, _ = resolve(obj, jn)
        (found.append(f"{jn} -> {attr}") if attr else missing.append(jn))
    for jn in foreign:
        attr, _ = resolve(obj, jn)
        if attr:
            contaminated.append(f"{jn} -> {attr}")
    ok = not missing and not contaminated
    # Both halves are the collision's signature, and design/12's actual lab
    # symptom was the first one: a shadow that exists and has NONE of its own
    # methods ("'java_lang_Class' object has no attribute 'get_directory'").
    # So say WRONG SURFACE for either, not only for the carried-methods case.
    if ok:
        verdict = f"OK — this shadow carries {classpath}'s own surface"
    else:
        why = []
        if missing:
            why.append(f"missing its own {', '.join(missing)}")
        if contaminated:
            why.append(f"carrying another class's {', '.join(contaminated)}")
        verdict = ("WRONG SURFACE — " + "; ".join(why)
                   + ". This is the design/12 static-class cache collision.")
    text = (
        f"{classpath}: shadow type={type(obj).__name__}, {len(names)} methods\n"
        f"  required present : {', '.join(found) or '(none)'}\n"
        f"  required MISSING : {', '.join(missing) or '(none)'}\n"
        f"  foreign present  : {', '.join(contaminated) or '(none)'}\n"
        f"  VERDICT          : {verdict}\n"
        f"  full surface     : {', '.join(names)}"
    )
    return ok, text


# --------------------------------------------------------------------------- #
# Check 1 — runs in its own subprocess so the wrap order is genuinely clean
# --------------------------------------------------------------------------- #
def run_wrap_order(port: int, order: str) -> int:
    """Child mode. Wrap a decoy then two target statics; report every surface.

    order "ij"      : decoy, ij.IJ, ij.WindowManager   (through the helper)
    order "wm"      : decoy, ij.WindowManager, ij.IJ   (through the helper)
    order "control" : decoy, ij.WindowManager          (plain JavaClass, NO
                      eviction) — reproduces design/12's collision, or shows
                      that this pyjavaz no longer has it.

    The decoy matters: with nothing wrapped first, the shared "java.lang.Class"
    cache key is empty and a collision is impossible by construction, so a pass
    would mean nothing.
    """
    if _new_static_java_class is None or JavaClass is None:
        print(f"CHILD-RESULT: ERROR imports unavailable "
              f"({_MICROCLAW_IMPORT_ERROR or _PYCROMANAGER_IMPORT_ERROR})", flush=True)
        return 3
    decoy = "java.lang.System"
    try:
        if order == "control":
            print("CONTROL: plain JavaClass, eviction helper BYPASSED.", flush=True)
            d = JavaClass(decoy, port=port)
            ok_d, txt = surface_report(decoy, d)
            print(f"1. wrapped decoy {decoy}\n{txt}", flush=True)
            wm = JavaClass("ij.WindowManager", port=port)
            ok_w, txt = surface_report("ij.WindowManager", wm)
            print(f"2. wrapped ij.WindowManager\n{txt}", flush=True)
            if ok_d and not ok_w:
                print("CHILD-RESULT: COLLISION the un-evicted wrap returned the "
                      "wrong class's surface, exactly as design/12 describes. "
                      "Check 1's PASS is therefore a real result.", flush=True)
            else:
                print("CHILD-RESULT: NOCOLLISION the un-evicted wrap came back "
                      "clean, so this pyjavaz does not reproduce design/12 here. "
                      "Check 1 passing is then WEAK evidence: it cannot "
                      "distinguish a working helper from an absent bug.", flush=True)
            return 0

        sequence = ([decoy, "ij.IJ", "ij.WindowManager"] if order == "ij"
                    else [decoy, "ij.WindowManager", "ij.IJ"])
        print(f"Wrap order (all through microclaw's _new_static_java_class): "
              f"{' -> '.join(sequence)}", flush=True)
        bad = []
        for i, cp in enumerate(sequence, start=1):
            obj = _new_static_java_class(port, cp)
            ok, txt = surface_report(cp, obj)
            print(f"{i}. wrapped {cp}\n{txt}", flush=True)
            if not ok:
                bad.append(cp)
        if bad:
            print(f"CHILD-RESULT: FAIL wrong surface on {', '.join(bad)}", flush=True)
            return 1
        print("CHILD-RESULT: PASS every shadow exposed its own class's methods "
              "and none of the other's", flush=True)
        return 0
    except Exception as exc:
        traceback.print_exc()
        print(f"CHILD-RESULT: ERROR {type(exc).__name__}: {exc}", flush=True)
        return 3


def check1(port: int, timeout: float) -> None:
    """Parent side of check 1: run each order in a clean process."""
    control_verdict = []
    for order, label in (("ij", "1a. wrap order ij.IJ then ij.WindowManager"),
                         ("wm", "1b. wrap order ij.WindowManager then ij.IJ"),
                         ("control", "1c. CONTROL: same wrap with the eviction "
                                     "helper bypassed")):
        print(f"\n--- {label} ---", flush=True)
        cmd = [sys.executable, os.path.abspath(__file__),
               "--port", str(port), "--wrap-order", order]
        print(f"  $ {' '.join(cmd)}", flush=True)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=max(timeout, 60.0))
        except subprocess.TimeoutExpired:
            record("ERROR", label,
                   "the subprocess never returned; it was killed. A static wrap "
                   "should never block — treat this as a bridge problem, not an "
                   "answer to check 1.")
            continue
        except Exception as exc:
            record("ERROR", label, f"could not start subprocess: {exc!r}")
            continue
        out = (proc.stdout or "") + (proc.stderr or "")
        print(textwrap.indent(out.rstrip(), "  | "), flush=True)
        verdict = next((ln for ln in reversed(out.splitlines())
                        if ln.startswith("CHILD-RESULT:")), "")
        body = verdict.split(":", 1)[1].strip() if verdict else ""
        word = body.split(" ", 1)[0] if body else ""
        detail = body[len(word):].strip()
        if order == "control":
            control_verdict.append(word)
            record("INFO", label, f"{word}: {detail}" if word else
                   f"no CHILD-RESULT line; exit code {proc.returncode}")
        elif word in ("PASS", "FAIL", "ERROR"):
            record(word, label, detail)
        else:
            record("ERROR", label,
                   f"no CHILD-RESULT line; exit code {proc.returncode}")
    if control_verdict == ["NOCOLLISION"]:
        record("INFO", "1c. what the control means",
               "The collision did not reproduce without the helper on this "
               "install, so 1a/1b passing does not prove the helper is what "
               "keeps the classes apart. Report this: it changes how much "
               "design/42's correction of design/10 rests on check 1.")


# --------------------------------------------------------------------------- #
# Windows and opening
# --------------------------------------------------------------------------- #
_OPENED: list[tuple[str, int]] = []   # (which check, ImageJ window id)


def window_ids(port: int, timeout: float) -> tuple[set[int], str]:
    """Current ImageJ image-window IDs, and how we got them.

    design/42's stub assumes WindowManager.getIDList() comes back iterable.
    pyjavaz turns a Java int[] into a numpy array, so it should — but if it
    does not, the getImageCount()/getNthImageID() route (1-based, no array
    serialisation at all) answers the same question, and which route worked is
    itself something 42b needs to know.
    """
    wm = bridge_call("wrap ij.WindowManager",
                     lambda: _new_static_java_class(port, "ij.WindowManager"), timeout)
    try:
        raw = bridge_call("WindowManager.getIDList",
                          jmethod(wm, "getIDList"), timeout)
        if raw is None:
            return set(), "getIDList -> null (no image windows open)"
        return {int(i) for i in raw}, f"getIDList -> {type(raw).__name__}"
    except BridgeStalled:
        raise
    except Exception as exc:
        first = f"getIDList unusable ({type(exc).__name__}: {exc}); "
    count = int(bridge_call("WindowManager.getImageCount",
                            jmethod(wm, "getImageCount"), timeout))
    nth = jmethod(wm, "getNthImageID")
    ids = {int(bridge_call(f"getNthImageID({n})", lambda n=n: nth(n), timeout))
           for n in range(1, count + 1)}
    return ids, first + "fell back to getImageCount/getNthImageID"


def describe_windows(port: int, ids, timeout: float) -> list[dict]:
    if not ids:
        return []
    wm = bridge_call("wrap ij.WindowManager",
                     lambda: _new_static_java_class(port, "ij.WindowManager"), timeout)
    get_image = jmethod(wm, "getImage")
    out = []
    for i in sorted(ids):
        try:
            imp = bridge_call(f"WindowManager.getImage({i})",
                              lambda i=i: get_image(i), timeout)
            if imp is None:
                out.append({"id": i, "error": "getImage returned null"})
                continue
            out.append({
                "id": i,
                "title": str(bridge_call("getTitle", jmethod(imp, "getTitle"), timeout)),
                "width": int(bridge_call("getWidth", jmethod(imp, "getWidth"), timeout)),
                "height": int(bridge_call("getHeight", jmethod(imp, "getHeight"), timeout)),
                "n_slices": int(bridge_call("getStackSize",
                                            jmethod(imp, "getStackSize"), timeout)),
            })
        except BridgeStalled:
            raise
        except Exception as exc:
            out.append({"id": i, "error": f"{type(exc).__name__}: {exc}"})
    return out


def open_and_observe(port: int, core, path: str, *, which: str,
                     timeout: float, wait_s: float) -> dict:
    """IJ.open(path), fully instrumented. The one place a file gets opened.

    Measures, in this order and with no sleep anywhere before the first two:
      open_s        wall time of IJ.open itself;
      wm_query_s    wall time of the FIRST bridge traffic after it (wrap
                    WindowManager + read the ID list) — a still-held pyjavaz
                    lock shows up here before anywhere else;
      immediate     whether the new window is visible to WindowManager with no
                    wait at all (design/18's lesson: a call returning is not a
                    window painting);
      core_s        wall time of a trivial core.get_version_info() straight
                    after, the plain confirmation that the bridge is free.
    """
    obs: dict = {"path": path,
                 "exists_python_side": os.path.exists(path),
                 "is_dir_python_side": os.path.isdir(path)}
    before, how = window_ids(port, timeout)
    obs["ids_source"] = how
    obs["windows_before"] = len(before)

    ij = bridge_call("wrap ij.IJ",
                     lambda: _new_static_java_class(port, "ij.IJ"), timeout)
    ij_open = jmethod(ij, "open")
    print(f"  calling IJ.open({path!r}) ...", flush=True)
    t0 = time.perf_counter()
    try:
        bridge_call(f"IJ.open({path})", lambda: ij_open(path), timeout)
        obs["open_error"] = None
    except BridgeStalled:
        raise
    except Exception as exc:
        obs["open_error"] = f"{type(exc).__name__}: {exc}"
    obs["open_s"] = round(time.perf_counter() - t0, 3)

    t1 = time.perf_counter()
    after, _ = window_ids(port, timeout)
    obs["wm_query_s"] = round(time.perf_counter() - t1, 3)
    obs["immediate"] = bool(after - before)

    t2 = time.perf_counter()
    bridge_call("core.get_version_info", core.get_version_info, timeout)
    obs["core_s"] = round(time.perf_counter() - t2, 3)

    new = after - before
    obs["appeared_after_s"] = 0.0 if new else None
    deadline = time.perf_counter() + wait_s
    while not new and time.perf_counter() < deadline:
        time.sleep(0.25)
        after, _ = window_ids(port, timeout)
        new = after - before
        if new:
            obs["appeared_after_s"] = round(wait_s - (deadline - time.perf_counter()), 2)
    obs["new_window_ids"] = sorted(new)
    obs["windows"] = describe_windows(port, new, timeout)
    for i in sorted(new):
        _OPENED.append((which, i))
    return obs


def fmt(obs: dict) -> str:
    lines = [f"{k:>20} : {obs[k]}" for k in
             ("path", "exists_python_side", "is_dir_python_side", "ids_source",
              "windows_before", "open_error", "open_s", "wm_query_s", "immediate",
              "appeared_after_s", "core_s", "new_window_ids") if k in obs]
    for w in obs.get("windows", []):
        lines.append(f"{'window':>20} : {w}")
    return "\n".join(lines)


def tiff_dims(path: str) -> dict:
    """Python-side truth for check 3. Reads headers, not pixels."""
    import tifffile
    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        return {"n_pages": len(tf.pages),
                "page_shape": tuple(int(x) for x in page.shape),
                "dtype": str(page.dtype),
                "series0_shape": tuple(int(x) for x in tf.series[0].shape)}


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=4827, help="MM ZMQ server port")
    ap.add_argument("--mosaic", default=DEFAULT_MOSAIC,
                    help=f"TIFF for check 3 (default: {DEFAULT_MOSAIC})")
    ap.add_argument("--ndtiff", default=DEFAULT_NDTIFF,
                    help=f"NDTiff DIRECTORY for check 5 (default: {DEFAULT_NDTIFF})")
    ap.add_argument("--foreign", default=None,
                    help="a file IJ1 cannot read natively (.nd2/.czi/.lif) for "
                         "check 4. Omitted -> check 4 records SKIP with a reason.")
    ap.add_argument("--only", type=int, nargs="+", metavar="N",
                    choices=[1, 2, 3, 4, 5, 6],
                    help="run only these checks, e.g. --only 6 after a stall")
    ap.add_argument("--timeout", type=float, default=90.0,
                    help="seconds before a bridge call is reported as a stall "
                         "(default 90)")
    ap.add_argument("--wait", type=float, default=10.0,
                    help="seconds to keep polling for a window after IJ.open "
                         "returns (default 10)")
    ap.add_argument("--wrap-order", choices=["ij", "wm", "control"],
                    help=argparse.SUPPRESS)   # internal: check 1's child mode
    args = ap.parse_args()

    if _new_static_java_class is None:
        raise SystemExit(
            "This spike imports microclaw ON PURPOSE (see the module docstring) "
            f"and the import failed: {_MICROCLAW_IMPORT_ERROR!r}\n"
            "Run `pip install -e .` in the repo and try again.")
    if Core is None:
        raise SystemExit(f"pycromanager import failed: {_PYCROMANAGER_IMPORT_ERROR!r}")

    if args.wrap_order:
        return run_wrap_order(args.port, args.wrap_order)

    want = set(args.only or [1, 2, 3, 4, 5, 6])
    port, timeout = args.port, args.timeout

    # ----- header: attribute every answer below to an install ---------------
    print("=" * 72, flush=True)
    print("design/42 block 42a — IJ.open spike", flush=True)
    print("=" * 72, flush=True)
    print(f"  when            : {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"  python          : {sys.version.split()[0]} on {platform.platform()}",
          flush=True)
    print(f"  microclaw from  : {getattr(microclaw, '__file__', '?')}", flush=True)
    print(f"  port            : {port}", flush=True)
    print(f"  checks          : {sorted(want)}", flush=True)
    print(f"  mosaic          : {args.mosaic}", flush=True)
    print(f"  ndtiff dir      : {args.ndtiff}", flush=True)
    print(f"  foreign file    : {args.foreign or '(none given -> check 4 SKIPs)'}",
          flush=True)

    try:
        core = Core(port=port)
        t0 = time.perf_counter()
        mm_version = bridge_call("core.get_version_info", core.get_version_info, timeout)
        baseline = round(time.perf_counter() - t0, 3)
        for _ in range(2):
            t0 = time.perf_counter()
            bridge_call("core.get_version_info", core.get_version_info, timeout)
            baseline = min(baseline, round(time.perf_counter() - t0, 3))
        record("PASS", "0. connect to Micro-Manager",
               f"MMCore: {mm_version}\n"
               f"trivial core call baseline (best of 3): {baseline:.3f} s")
    except BridgeStalled as stall:
        record("FAIL", "0. connect to Micro-Manager", str(stall))
        summarize(port, timeout, stalled=True)
        os._exit(2)
    except Exception as exc:
        record("ERROR", "0. connect to Micro-Manager", f"{type(exc).__name__}: {exc}")
        print("\nCannot continue without a connection. Is MM open with the ZMQ "
              "server enabled on this port?", flush=True)
        summarize(port, timeout, stalled=True)
        return 3

    try:
        ij = bridge_call("wrap ij.IJ",
                         lambda: _new_static_java_class(port, "ij.IJ"), timeout)

        def soft(java_name, *call_args):
            """Read an accessor that may not exist on every IJ1 build."""
            attr, fn = resolve(ij, java_name)
            if fn is None:
                return f"(no {java_name} on this build)"
            return bridge_call(f"IJ.{java_name}", lambda: fn(*call_args), timeout)

        record("PASS", "0b. ImageJ version",
               f"IJ.getVersion={soft('getVersion')}  "
               f"IJ.getFullVersion={soft('getFullVersion')}\n"
               f"IJ.getDirectory('imagej')={soft('getDirectory', 'imagej')}")
    except BridgeStalled as stall:
        record("FAIL", "0b. ImageJ version", str(stall))
        summarize(port, timeout, stalled=True)
        os._exit(2)
    except Exception as exc:
        record("ERROR", "0b. ImageJ version", f"{type(exc).__name__}: {exc}")

    try:
        run_checks(want, port, core, args, timeout, baseline)
    except BridgeStalled as stall:
        record("FAIL", "6. IJ.open can stall the bridge",
               f"THE BRIDGE WAS STALLED. {stall}\n"
               "pyjavaz holds one lock across every round trip, so nothing else "
               "can use the bridge while that call is blocked — including a core "
               "call that would end an exposure. This is design/42's blocker "
               "condition: block 42b does not start in this shape.\n"
               "Look at the Micro-Manager screen and report what dialog (if any) "
               "was up, then dismiss it and re-run with --only 6.")
        summarize(port, timeout, stalled=True)
        os._exit(2)

    summarize(port, timeout, stalled=False)
    return 0


def run_checks(want, port, core, args, timeout, baseline) -> None:
    # ----- 1 --------------------------------------------------------------- #
    if 1 in want:
        check1(port, timeout)
    else:
        record("SKIP", "1. static wraps in both orders", "not selected by --only")

    # ----- 2 --------------------------------------------------------------- #
    if 2 in want:
        @check("2. a shadow held across another static wrap still works")
        def _c2():
            ij1 = _new_static_java_class(port, "ij.IJ")
            v1 = bridge_call("IJ.getDirectory (before)",
                             lambda: jmethod(ij1, "getDirectory")("imagej"), timeout)
            wm1 = _new_static_java_class(port, "ij.WindowManager")  # evicts
            v2_err = None
            try:
                v2 = bridge_call("IJ.getDirectory (stale shadow, after eviction)",
                                 lambda: jmethod(ij1, "getDirectory")("imagej"), timeout)
            except BridgeStalled:
                raise
            except Exception as exc:
                v2, v2_err = None, f"{type(exc).__name__}: {exc}"
            c1 = bridge_call("WindowManager.getImageCount (before)",
                             jmethod(wm1, "getImageCount"), timeout)
            ij2 = _new_static_java_class(port, "ij.IJ")             # evicts again
            c2_err = None
            try:
                c2 = bridge_call("WindowManager.getImageCount (stale shadow)",
                                 jmethod(wm1, "getImageCount"), timeout)
            except BridgeStalled:
                raise
            except Exception as exc:
                c2, c2_err = None, f"{type(exc).__name__}: {exc}"
            detail = (
                f"held ij.IJ shadow, then wrapped ij.WindowManager (evicts the "
                f"shared cache key), then called the HELD shadow:\n"
                f"  IJ.getDirectory before = {v1!r}\n"
                f"  IJ.getDirectory after  = {v2!r}  {('ERR: ' + v2_err) if v2_err else ''}\n"
                f"held ij.WindowManager shadow, then wrapped ij.IJ, then called it:\n"
                f"  getImageCount before   = {c1!r}\n"
                f"  getImageCount after    = {c2!r}  {('ERR: ' + c2_err) if c2_err else ''}\n"
                f"shadow class regenerated by the eviction: "
                f"{type(ij1) is not type(ij2)} "
                f"({type(ij1).__name__} vs {type(ij2).__name__})\n"
                f"(getImageCount can legitimately differ if a window opened or "
                f"closed between the two reads — check the numbers, not just the "
                f"verdict.)")
            ok = (v2_err is None and v2 == v1 and v1
                  and c2_err is None and c2 is not None)
            if ok:
                return ("PASS", detail + "\nA held shadow survived a later static "
                        "wrap: design/42's 're-wrap statics per call' is HYGIENE, "
                        "not a correctness rule.")
            return ("FAIL", detail + "\nA held shadow did NOT survive a later "
                    "static wrap: 're-wrap statics per call' is a RULE, and 42b "
                    "must get each static immediately before it uses it.")
    else:
        record("SKIP", "2. held shadow across a wrap", "not selected by --only")

    # ----- 3 --------------------------------------------------------------- #
    if 3 in want:
        @check("3. IJ.open on the mosaic TIFF")
        def _c3():
            if not os.path.exists(args.mosaic):
                raise SkipSpike(f"{args.mosaic} is not on this machine. Pass "
                                "--mosaic with a TIFF that is; a fabricated pass "
                                "here would be worthless.")
            try:
                py = tiff_dims(args.mosaic)
            except Exception as exc:
                py = {"error": f"{type(exc).__name__}: {exc}"}
            obs = open_and_observe(port, core, args.mosaic, which="3",
                                   timeout=timeout, wait_s=args.wait)
            detail = fmt(obs) + f"\n{'tifffile (python)':>20} : {py}"
            if not obs["new_window_ids"]:
                return ("FAIL", detail + "\nNo new ImageJ window appeared. "
                        "IJ.open returning is not proof a window exists "
                        "(design/18), and this is that case.")
            if "page_shape" not in py:
                return ("INFO", detail + "\nA window appeared, but tifffile could "
                        "not read the file Python-side, so the dimensions are "
                        "UNCONFIRMED. A title match alone is near "
                        "self-confirming — do not read this as a pass.")
            h, w = py["page_shape"][-2], py["page_shape"][-1]
            got = [(x.get("width"), x.get("height")) for x in obs["windows"]]
            if (w, h) in got:
                return ("PASS", detail + f"\nImageJ {got} == tifffile {(w, h)} "
                        "(width, height). The dimensions match.")
            return ("FAIL", detail + f"\nImageJ {got} != tifffile {(w, h)} "
                    "(width, height). Report both numbers; a mismatch may mean "
                    "IJ opened a DIFFERENT file (the path resolves Java-side).")
    else:
        record("SKIP", "3. IJ.open on the mosaic TIFF", "not selected by --only")

    # ----- 5 (before 4: 5 is quick, 4 can hold a dialog) -------------------- #
    if 5 in want:
        @check("5. IJ.open on the NDTiff DIRECTORY (expected to fail)")
        def _c5():
            obs = open_and_observe(port, core, args.ndtiff, which="5",
                                   timeout=timeout, wait_s=min(args.wait, 5.0))
            ij = _new_static_java_class(port, "ij.IJ")
            attr, _ = resolve(ij, "redirectErrorMessages")
            note = (f"\nIJ.redirectErrorMessages present as {attr!r} — 42b can use "
                    "it to send an open failure to the Log window instead of a "
                    "modal dialog." if attr else
                    "\nIJ.redirectErrorMessages is NOT on this shadow.")
            if obs["open_error"]:
                how = ("It RAISED over the bridge — 42b can catch this and refuse "
                       "with a reason.")
            elif obs["new_window_ids"]:
                how = ("It OPENED something. Say what the window contains — this "
                       "contradicts the expectation and 42b's tool description "
                       "must follow what happened, not the design doc.")
            else:
                how = ("SILENT NO-OP: no exception and no window. 42b must treat "
                       "'no new window' as the refusal signal, because there is "
                       "no error to catch.")
            return ("INFO", fmt(obs) + "\n" + how + note)
    else:
        record("SKIP", "5. IJ.open on the NDTiff directory", "not selected by --only")

    # ----- 4 (can raise a modal Bio-Formats importer dialog) ---------------- #
    if 4 in want:
        @check("4. IJ.open on a format IJ1 cannot read natively")
        def _c4():
            found, absent = [], []
            for cp in ("loci.plugins.BF", "loci.formats.ImageReader",
                       "loci.plugins.LociImporter", "HandleExtraFileTypes"):
                try:
                    bridge_call(f"wrap {cp}",
                                lambda cp=cp: _new_static_java_class(port, cp), timeout)
                    found.append(cp)
                except BridgeStalled:
                    raise
                except Exception as exc:
                    absent.append(f"{cp} ({type(exc).__name__})")
            bf = (f"Bio-Formats / HandleExtraFileTypes on this install:\n"
                  f"  resolved : {', '.join(found) or '(none)'}\n"
                  f"  absent   : {', '.join(absent) or '(none)'}")
            if not args.foreign:
                raise SkipSpike(
                    "no --foreign path was given, so nothing was opened. "
                    "Re-run with a .nd2/.czi/.lif this rig has. A SKIP is a real "
                    "result here; a pass without a file would not be.\n" + bf)
            if not os.path.exists(args.foreign):
                raise SkipSpike(f"{args.foreign} is not on this machine.\n" + bf)
            obs = open_and_observe(port, core, args.foreign, which="4",
                                   timeout=timeout, wait_s=max(args.wait, 20.0))
            if obs["new_window_ids"] and not obs["open_error"]:
                verdict = ("Bio-Formats delegation via HandleExtraFileTypes IS "
                           "real on this install: IJ.open handled a format IJ1 "
                           "does not read natively.")
            elif obs["open_error"]:
                verdict = ("It failed, and THIS is the record of how a "
                           "non-native format fails on an install without "
                           "working Bio-Formats delegation. 42b's refusal should "
                           "quote it.")
            else:
                verdict = ("No window and no error — a silent no-op, the worst "
                           "case for 42b, which cannot distinguish it from a "
                           "successful open without the window check.")
            return ("INFO", bf + "\n" + fmt(obs) + "\n" + verdict)
    else:
        record("SKIP", "4. IJ.open on a non-native format", "not selected by --only")

    # ----- 6 --------------------------------------------------------------- #
    if 6 in want:
        @check("6. does IJ.open block, and can it stall the bridge?")
        def _c6():
            if not os.path.exists(args.mosaic):
                raise SkipSpike(f"{args.mosaic} is not on this machine; pass "
                                "--mosaic. Check 6 needs a file that really opens.")
            obs = open_and_observe(port, core, args.mosaic, which="6",
                                   timeout=timeout, wait_s=args.wait)
            detail = (
                fmt(obs) +
                f"\n{'core baseline':>20} : {baseline:.3f} s (best of 3, before "
                "anything was opened)\n"
                f"{'core after open':>20} : {obs['core_s']:.3f} s\n"
                f"{'window immediate':>20} : {obs['immediate']}"
                f"  (appeared_after_s={obs['appeared_after_s']})")
            stalled = obs["core_s"] > max(2.0, 10 * baseline)
            if stalled:
                return ("FAIL", detail + "\nThe trivial core call after IJ.open "
                        "took far longer than the baseline: the bridge WAS held. "
                        "This is design/42's blocker — 42b does not start in this "
                        "shape.")
            note = ("\nIJ.open returned and the bridge was free straight after: "
                    "on this install and this file it does not stall the single "
                    "pyjavaz lock. Note the scope of that — it says nothing "
                    "about a format that raises a modal importer dialog, which "
                    "is what check 4 with --foreign is for.")
            if not obs["immediate"]:
                note += ("\nThe window was NOT visible to WindowManager the "
                         "instant IJ.open returned "
                         f"(it appeared after {obs['appeared_after_s']} s). The "
                         "call is ASYNC with respect to the window, so 42b's "
                         "structural check must WAIT rather than read once — "
                         "design/18's lesson in a new place.")
            else:
                note += ("\nThe window was visible to WindowManager with no "
                         "sleep, so 42b's structural check can read once.")
            return ("INFO", detail + note)
    else:
        record("SKIP", "6. does IJ.open block?", "not selected by --only")


def summarize(port: int, timeout: float, *, stalled: bool) -> None:
    print("\n" + "=" * 72, flush=True)
    print("WINDOWS THIS SPIKE OPENED — they are left open ON PURPOSE", flush=True)
    print("=" * 72, flush=True)
    if not _OPENED:
        print("  (none)", flush=True)
    elif stalled:
        for which, i in _OPENED:
            print(f"  check {which}: window id {i} (titles unavailable — the "
                  "bridge is stalled)", flush=True)
    else:
        try:
            for w in describe_windows(port, {i for _, i in _OPENED}, timeout):
                print(f"  {w}", flush=True)
        except BaseException as exc:  # noqa: BLE001 - reporting path
            print(f"  could not list them ({type(exc).__name__}: {exc}); ids: "
                  f"{[i for _, i in _OPENED]}", flush=True)
    print("  Close them by hand. Microclaw never closes a user's windows, and "
          "neither does this spike.", flush=True)

    print("\n" + "=" * 72, flush=True)
    print("SPIKE SUMMARY", flush=True)
    print("=" * 72, flush=True)
    for status, name, _ in _RESULTS:
        print(f"  {status:5}  {name}", flush=True)
    counts: dict[str, int] = {}
    for status, _, _ in _RESULTS:
        counts[status] = counts.get(status, 0) + 1
    print("-" * 72, flush=True)
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())), flush=True)
    print("=" * 72, flush=True)
    print(textwrap.dedent("""
        How to read this (design/42, block 42a):

          1 FAIL   -> STOP. design/42's correction of design/10 rests on the
                      static IJ1 path being open through the eviction helper.
                      If it is not, the document's premise goes with it. Report
                      it; do not work around it.
          1c       -> the control. COLLISION means check 1 measured something.
                      NOCOLLISION means the bug did not reproduce here, so 1a/1b
                      passing is weaker evidence than it looks. Report which.
          2 PASS   -> "re-wrap statics per call" is hygiene.
            2 FAIL -> it is a rule, and 42b must obey it.
          3 PASS   -> IJ.open really opens a window, and the DIMENSIONS agree
                      with what Python reads. That is the load-bearing part.
          5        -> whatever it says is the answer 42b refuses with. Its tool
                      description must match this, not the design doc.
          4 SKIP   -> a real result, meaning nobody supplied a non-native file.
          6 FAIL, or this run ending in a STALL
                   -> STOP. The bridge can be held, and block 42b does not start
                      in this shape.

        Send back the whole of out.txt AND which windows you saw appear on
        screen. WindowManager agreeing is a structural check; your eyes are the
        only check on whether anything painted.
        """), flush=True)


if __name__ == "__main__":
    sys.exit(main())
