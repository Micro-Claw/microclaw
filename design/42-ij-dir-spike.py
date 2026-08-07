#!/usr/bin/env python
"""Spike: what entry point actually opens a DIRECTORY on this install?

Block 42b's open question, and the reason it is asked. Block 42a measured that
`IJ.open` on an NDTiff **directory** is a silent no-op that held the pyjavaz
bridge for 6.94 s — no exception, no window — while the operator dragged that
same folder onto the toolbar and it opened. So `IJ.open` and drag-and-drop are
not one entry point for a directory, and `open_artifact` must either find the
one the drag uses or refuse directories by name. NDTiff datasets are the
commonest thing microclaw writes, so this is half the tool's value.

NOTHING HERE MOVES A STAGE, OPENS A SHUTTER, OR FIRES A CAMERA. Zero exposure.
It reads a dataset off disk, asks the running JVM to open it, and asks what
windows and viewers exist. It never closes a window — see "Windows" below.

Run this MANUALLY on the rig with Micro-Manager OPEN and the pycro-manager ZMQ
server enabled (Tools > Options, port 4827 by default). PowerShell:

    python design\\42-ij-dir-spike.py 2>&1 | Out-File -Encoding utf8 out42bdir.txt
    python design\\42-ij-dir-spike.py --dir "D:\\SSD\\stitch_test\\stitch_test_1" ...
    python design\\42-ij-dir-spike.py --only D4 --try-drag ...
    Get-Content out42bdir.txt

PowerShell's bare `>` writes UTF-16LE, which made 42a's evidence awkward to
read; `Out-File -Encoding utf8` avoids it. `uv run` writes progress to stderr,
which PowerShell surfaces as a red NativeCommandError before the script starts
— that is not a failure.

See design/42-block42b-rig-gate.md on this branch (gate G0) for the full
runbook, including what to do if it hangs.

WHAT THE JAR ALREADY SAYS, AND WHY THIS RUN STILL MATTERS
---------------------------------------------------------
This spike was written against a disassembly of the *same* two jars the rig
runs — `ij-1.53c.jar` and `MMJ_.jar` from Micro-Manager 2.0.3-20260625 — so it
is not fishing. It is confirming, over the bridge, four things the bytecode
already asserts. What javap cannot tell us is whether pyjavaz can reach them,
whether they block, and what the operator sees.

  1. `IJ.open` never touches `ij.plugin.DragAndDrop`. `IJ.open` builds an
     `ij.io.Opener` and calls `Opener.open`. `DragAndDrop` is a
     `DropTargetListener`; its `drop` -> `run()` -> `openFile(File)`, and for a
     directory -> the PRIVATE `openDirectory(File,String)`.
  2. `DragAndDrop.openDirectory` puts up a **modal GenericDialog** — "Open all
     N images in "<folder>" as a stack?", Yes/No/Cancel — BEFORE it does
     anything. Yes runs `IJ.run("Image Sequence...", "open=[dir] sort")`; No
     opens every file in the folder one at a time. So the toolbar drag on a
     folder is not a headless mechanism: it contains a human decision.
     `openFile(File)` is public, and therefore reachable over the bridge, but
     calling it would raise that dialog while holding the single pyjavaz lock —
     design/42 check 6's blocker condition, on the commonest artifact.
  3. There is a SECOND drop target on this install, and it is Micro-Manager's.
     `org.micromanager.internal.MainFrame` installs
     `new DropTarget(this, new DragDropUtil(mmStudio_))`, and
     `DragDropUtil.drop` -> `studio.data().loadData(path, false)`,
     `studio.displays().manage(store)`, `studio.displays().loadDisplays(store)`,
     on a background thread.
  4. `DefaultDataManager.loadData` dispatches on
     `NDTiffAdapter.isNDTiffDataSet(path)` to MM's **native NDTiff reader**. Its
     only modal is a JOptionPane "Insufficient Memory Warning", and that entire
     block is guarded by `if (!isVirtual)` (bytecode `140: iload_3; 141: ifne`).
     So `loadData(path, TRUE)` is dialog-free, and that is the call this spike
     recommends to 42b.

WHAT IT CHECKS (each isolated; a failing check never stops the ones after it)

  D1. Which candidate classes resolve over the bridge at all: DragAndDrop,
      FolderOpener, Opener, DragDropUtil, NDTiffAdapter. Names only, nothing
      called. Cheapest possible first question.
  D2. THE CANDIDATE 42b SHIPS. studio.data().loadData(dir, virtual=True) then
      displays().manage() then displays().loadDisplays(). Reports the viewer
      count before/after, the wall time of each leg, whether an ImageJ window
      also appeared, the datastore's dimensions against what ndstorage reads
      Python-side, and a trivial core call afterwards to prove the bridge is
      free. This is MM's own reader for MM's own format.
  D3. `ij.plugin.FolderOpener.open(dir)` — the headless equivalent of the YES
      branch of the drag's dialog. Returns an ImagePlus or null; if non-null we
      show() it. INFO, not PASS/FAIL: whether an image sequence of the NDTiff
      stack files is a USEFUL thing to show a user is a judgement, and the
      point is to record what it produces.
  D4. `new DragAndDrop().openFile(new File(dir))` — the LITERAL entry point the
      drag dispatches to. SKIPPED unless --try-drag, because per (2) above it
      WILL put up a modal dialog and hold the bridge until you answer it. Run it
      once, deliberately, with a finger on the dialog: it is the only way to see
      what the operator saw. Answer it and report which button you pressed.

  Not checked: `IJ.open` on the directory. 42a already measured that (silent
  no-op, 6.94 s), and re-running it would cost seven seconds to learn nothing.

STALLS. Each bridge call runs on a daemon thread with a watchdog, so a modal
dialog cannot hang this script silently: it prints a nudge, waits --timeout
seconds, then reports the stall and exits. Once the pyjavaz lock is held by a
blocked call nothing else can use the bridge, so the run ends there — dismiss
the dialog and re-run with --only.

WINDOWS. Every window and viewer this opens is LEFT OPEN, listed at the end so
you can close them by hand. Microclaw does not close the user's windows, and a
spike that did would be teaching the wrong thing.
"""
from __future__ import annotations

import argparse
import os
import platform
import sys
import textwrap
import threading
import time
import traceback

DEFAULT_DIR = r"D:\SSD\stitch_test\stitch_test_1"

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
    from pycromanager import Core, Studio, JavaObject
except Exception as exc:  # pragma: no cover - rig environment guard
    Core = Studio = JavaObject = None
    _PYCROMANAGER_IMPORT_ERROR = exc


# --------------------------------------------------------------------------- #
# Result harness (house style, design/42-ij-open-spike.py)
# --------------------------------------------------------------------------- #
# PASS  the property held.          FAIL  it did not.
# INFO  no pass/fail — the answer IS the detail.
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

    Deliberately NOT an Exception: the checks below catch Exception on purpose
    (they expect Java-side failures), and a stall must not be swallowed by one
    of them. It ends the run.
    """


def check(name: str):
    """Run a check function; record PASS/FAIL/INFO/SKIP/ERROR."""
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
    forever. Running on a daemon thread lets us report that instead of hanging.
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
                print("  *** LOOK AT THE MICRO-MANAGER SCREEN NOW: a modal ImageJ, "
                      "Micro-Manager or", flush=True)
                print("  *** Bio-Formats dialog blocks the single pyjavaz lock. "
                      "Dismiss it and this continues.", flush=True)
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
    return value, round(time.perf_counter() - t0, 3)


# --------------------------------------------------------------------------- #
# Java shadow introspection (design/42-ij-open-spike.py conventions)
# --------------------------------------------------------------------------- #
def _norm(name: str) -> str:
    return name.lower().replace("_", "")


def method_names(obj) -> list[str]:
    return sorted(m for m in dir(obj) if not m.startswith("_"))


def resolve(obj, *java_names) -> tuple[str | None, object]:
    """Find a Java method on a bridge shadow whatever pyjavaz named it."""
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


def java_list_size(java_list) -> int:
    """Length of a Java List over the bridge.

    pyjavaz collections are not Python-iterable and len() does not work on them
    (design/32 Block 4). size() is the only reliable answer.
    """
    if java_list is None:
        return 0
    if isinstance(java_list, (list, tuple, set)):
        return len(java_list)
    return int(jmethod(java_list, "size")())


_OPENED: list[str] = []   # human-readable descriptions, printed at the end


def imagej_window_ids(port: int, timeout: float) -> set[int]:
    """Current ImageJ image-window IDs, or an empty set."""
    wm = bridge_call("wrap ij.WindowManager",
                     lambda: _new_static_java_class(port, "ij.WindowManager"), timeout)
    raw = bridge_call("WindowManager.getIDList", jmethod(wm, "getIDList"), timeout)
    return set() if raw is None else {int(i) for i in raw}


def describe_imagej_windows(port: int, ids, timeout: float) -> list[dict]:
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


def python_side_dataset(path: str) -> dict:
    """Python-side truth for D2. Reads the index and ONE plane, no exposure."""
    try:
        from ndstorage import Dataset
    except Exception as exc:
        return {"error": f"ndstorage unavailable: {type(exc).__name__}: {exc}"}
    try:
        dataset = Dataset(path)
        coords = dataset.get_image_coordinates_list()
        out = {"n_images": len(coords), "axes": sorted(dataset.axes)}
        if coords:
            plane = dataset.read_image(**coords[0])
            out["plane_shape"] = tuple(int(x) for x in plane.shape)
            out["dtype"] = str(plane.dtype)
        return out
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- #
def run_checks(want, port, core, studio, args, timeout) -> None:
    directory = args.dir

    # ----- D1 --------------------------------------------------------------- #
    if "D1" in want:
        @check("D1. which candidate entry points resolve over the bridge")
        def _d1():
            statics = ("ij.plugin.FolderOpener", "ij.plugin.DragAndDrop",
                       "ij.io.Opener",
                       "org.micromanager.data.internal.ndtiff.NDTiffAdapter",
                       "org.micromanager.internal.utils.DragDropUtil")
            lines = []
            for cp in statics:
                try:
                    obj = bridge_call(f"wrap {cp}",
                                      lambda cp=cp: _new_static_java_class(port, cp),
                                      timeout)
                    lines.append(f"  {cp}: RESOLVED, {len(method_names(obj))} "
                                 f"static-shadow methods")
                except BridgeStalled:
                    raise
                except Exception as exc:
                    lines.append(f"  {cp}: absent ({type(exc).__name__}: {exc})")
            # Instances matter separately: DragAndDrop.openFile and Opener.open
            # are instance methods, and JavaObject is a different code path from
            # the static shadow above (design/12 affects only statics).
            for cp in ("ij.plugin.DragAndDrop", "ij.io.Opener"):
                try:
                    obj = bridge_call(f"new {cp}",
                                      lambda cp=cp: JavaObject(cp, port=port), timeout)
                    attr, _ = resolve(obj, "openFile" if "DragAndDrop" in cp else "open")
                    lines.append(f"  new {cp}(): INSTANTIABLE, target method as "
                                 f"{attr!r}")
                except BridgeStalled:
                    raise
                except Exception as exc:
                    lines.append(f"  new {cp}(): not instantiable "
                                 f"({type(exc).__name__}: {exc})")
            return ("INFO", "\n".join(lines) + textwrap.dedent("""

                Names only; nothing was called. What matters for 42b is that the
                Micro-Manager side (NDTiffAdapter / DragDropUtil / Studio) is
                reachable, since D2 is the path it ships. A DragAndDrop that
                resolves is NOT a green light — see D4."""))
    else:
        record("SKIP", "D1. candidate entry points", "not selected by --only")

    # ----- D2 --------------------------------------------------------------- #
    if "D2" in want:
        @check("D2. Micro-Manager's own dataset reader (the candidate 42b ships)")
        def _d2():
            if not os.path.isdir(directory):
                raise SkipSpike(f"{directory} is not a directory on this machine. "
                                "Pass --dir with an NDTiff dataset that is; a "
                                "fabricated pass here would be worthless.")
            py = python_side_dataset(directory)
            displays = bridge_call("studio.displays()", studio.displays, timeout)
            data = bridge_call("studio.data()", studio.data, timeout)

            def viewer_counts():
                windows = bridge_call("displays.getAllImageWindows",
                                      jmethod(displays, "getAllImageWindows"), timeout)
                viewers = bridge_call("displays.getAllDataViewers",
                                      jmethod(displays, "getAllDataViewers"), timeout)
                return java_list_size(windows), java_list_size(viewers)

            ij_before = imagej_window_ids(port, timeout)
            windows_before, viewers_before = viewer_counts()

            load = jmethod(data, "loadData")
            # virtual=True on purpose: loadData's ONLY modal (the "Insufficient
            # Memory Warning" JOptionPane) sits inside an `if (!isVirtual)`
            # block, and a modal here holds the single pyjavaz lock.
            store, load_s = timed(f"data.loadData({directory}, virtual=True)",
                                  lambda: load(directory, True), timeout)
            if store is None:
                return ("FAIL", f"python side: {py}\nloadData returned null after "
                        f"{load_s:.3f} s — nothing to display.")
            _, manage_s = timed("displays.manage(store)",
                                lambda: jmethod(displays, "manage")(store), timeout)
            created, show_s = timed("displays.loadDisplays(store)",
                                    lambda: jmethod(displays, "loadDisplays")(store),
                                    timeout)
            n_created = java_list_size(created)

            windows_after, viewers_after = viewer_counts()
            ij_after = imagej_window_ids(port, timeout)
            _, core_s = timed("core.get_version_info", core.get_version_info, timeout)

            java = {}
            for label, names in (("save_path", ("getSavePath",)),
                                 ("n_images", ("getNumImages",))):
                try:
                    java[label] = bridge_call(f"store.{names[0]}",
                                              jmethod(store, *names), timeout)
                except BridgeStalled:
                    raise
                except Exception as exc:
                    java[label] = f"({type(exc).__name__}: {exc})"
            try:
                any_image = bridge_call("store.getAnyImage",
                                        jmethod(store, "getAnyImage"), timeout)
                java["width"] = int(bridge_call(
                    "image.getWidth", jmethod(any_image, "getWidth"), timeout))
                java["height"] = int(bridge_call(
                    "image.getHeight", jmethod(any_image, "getHeight"), timeout))
            except BridgeStalled:
                raise
            except Exception as exc:
                java["dimensions"] = f"({type(exc).__name__}: {exc})"

            new_ij = ij_after - ij_before
            _OPENED.append(f"D2: MM dataset viewer for {directory} "
                           f"({n_created} display(s) created)")
            for i in sorted(new_ij):
                _OPENED.append(f"D2: ImageJ window id {i}")

            detail = "\n".join([
                f"{'directory':>22} : {directory}",
                f"{'ndstorage (python)':>22} : {py}",
                f"{'datastore (java)':>22} : {java}",
                f"{'loadData_s':>22} : {load_s}",
                f"{'manage_s':>22} : {manage_s}",
                f"{'loadDisplays_s':>22} : {show_s}",
                f"{'displays created':>22} : {n_created}",
                f"{'MM image windows':>22} : {windows_before} -> {windows_after}",
                f"{'MM data viewers':>22} : {viewers_before} -> {viewers_after}",
                f"{'ImageJ window ids':>22} : {len(ij_before)} -> {len(ij_after)} "
                f"(new: {sorted(new_ij)})",
                f"{'core call after':>22} : {core_s} s",
            ])
            if new_ij:
                detail += ("\n" + textwrap.indent("\n".join(
                    str(w) for w in describe_imagej_windows(port, new_ij, timeout)),
                    " " * 25))

            if n_created == 0 and windows_after <= windows_before:
                return ("FAIL", detail + "\nNo display was created. MM's own reader "
                        "did not open this directory either, and 42b must refuse "
                        "directories by name. Say what was on screen.")
            expected = py.get("plane_shape")
            if expected and "width" in java:
                agree = (int(expected[-1]), int(expected[-2])) == (java["width"],
                                                                   java["height"])
                detail += (f"\nDIMENSIONS: java (w,h)=({java['width']},"
                           f"{java['height']}) vs ndstorage (w,h)="
                           f"({expected[-1]},{expected[-2]}) -> "
                           f"{'MATCH' if agree else 'MISMATCH'}")
                if not agree:
                    return ("FAIL", detail + "\nA display appeared but it is not "
                            "this dataset's shape. Report both numbers.")
            return ("PASS", detail + textwrap.dedent("""
                A viewer appeared for the dataset, and the bridge was free
                straight after. LOOK AT THE SCREEN and say what you actually
                see: a Micro-Manager display window with the dataset name, or
                nothing. WindowManager agreeing is a structural check; your eyes
                are the only check on whether anything painted."""))
    else:
        record("SKIP", "D2. Micro-Manager's dataset reader", "not selected by --only")

    # ----- D3 --------------------------------------------------------------- #
    if "D3" in want:
        @check("D3. FolderOpener.open(dir) — the headless YES branch of the drag")
        def _d3():
            if not os.path.isdir(directory):
                raise SkipSpike(f"{directory} is not a directory on this machine.")
            fo = bridge_call("wrap ij.plugin.FolderOpener",
                             lambda: _new_static_java_class(port, "ij.plugin.FolderOpener"),
                             timeout)
            before = imagej_window_ids(port, timeout)
            open_fn = jmethod(fo, "open")
            imp, open_s = timed(f"FolderOpener.open({directory})",
                                lambda: open_fn(directory), timeout)
            shown = None
            if imp is not None:
                try:
                    bridge_call("imp.show", jmethod(imp, "show"), timeout)
                    shown = True
                except BridgeStalled:
                    raise
                except Exception as exc:
                    shown = f"show() failed: {type(exc).__name__}: {exc}"
            after = imagej_window_ids(port, timeout)
            new = after - before
            for i in sorted(new):
                _OPENED.append(f"D3: ImageJ window id {i}")
            windows = describe_imagej_windows(port, new, timeout)
            detail = "\n".join([
                f"{'open_s':>22} : {open_s}",
                f"{'returned':>22} : {'null' if imp is None else 'an ImagePlus'}",
                f"{'show() called':>22} : {shown}",
                f"{'new window ids':>22} : {sorted(new)}",
                f"{'windows':>22} : {windows}",
            ])
            if imp is None and not new:
                return ("INFO", detail + textwrap.dedent("""
                    FolderOpener produced nothing on this directory. The YES
                    branch of the drag's dialog does not open an NDTiff folder
                    either, which strengthens D2 as the only real answer."""))
            return ("INFO", detail + textwrap.dedent("""
                FolderOpener produced something. Say WHAT — an image sequence of
                the NDTiff stack files is not the dataset, and a window that
                shows the wrong thing is worse for 42b than no window. Compare
                n_slices against the dataset's image count in D2."""))
    else:
        record("SKIP", "D3. FolderOpener.open", "not selected by --only")

    # ----- D4 --------------------------------------------------------------- #
    if "D4" in want:
        @check("D4. DragAndDrop.openFile(dir) — the literal drag entry point")
        def _d4():
            if not args.try_drag:
                raise SkipSpike(textwrap.dedent(f"""
                    Not run without --try-drag, and that is a deliberate refusal,
                    not an oversight.

                    ij-1.53c's DragAndDrop.openFile(File) sends a DIRECTORY to the
                    private openDirectory(File,String), which opens with a modal
                    GenericDialog — "Open all N images in "{os.path.basename(directory)}"
                    as a stack?", Yes/No/Cancel — before doing anything at all.
                    Over this bridge that dialog holds the single pyjavaz lock
                    until a human answers it, which is design/42 check 6's blocker
                    condition.

                    Run it once, on purpose, with a finger on the dialog:
                        python design\\42-ij-dir-spike.py --only D4 --try-drag
                    Then report which button you pressed and what appeared. That
                    is the only way to see what the operator saw when the folder
                    "opened", and it is the evidence that says whether the drag
                    was ever headless."""))
            print("  *** --try-drag: a modal 'Open Folder' dialog is EXPECTED.",
                  flush=True)
            print("  *** Watch the screen and answer it. The bridge is held until "
                  "you do.", flush=True)
            dd = bridge_call("new ij.plugin.DragAndDrop",
                             lambda: JavaObject("ij.plugin.DragAndDrop", port=port),
                             timeout)
            f = bridge_call("new java.io.File",
                            lambda: JavaObject("java.io.File", port=port,
                                               args=[directory]), timeout)
            before = imagej_window_ids(port, timeout)
            _, open_s = timed(f"DragAndDrop.openFile({directory})",
                              lambda: jmethod(dd, "openFile")(f), timeout)
            after = imagej_window_ids(port, timeout)
            new = after - before
            for i in sorted(new):
                _OPENED.append(f"D4: ImageJ window id {i}")
            _, core_s = timed("core.get_version_info", core.get_version_info, timeout)
            return ("INFO", "\n".join([
                f"{'openFile_s':>22} : {open_s}  (includes the time you spent on "
                "the dialog)",
                f"{'new window ids':>22} : {sorted(new)}",
                f"{'windows':>22} : {describe_imagej_windows(port, new, timeout)}",
                f"{'core call after':>22} : {core_s} s",
            ]) + textwrap.dedent("""
                REPORT: did a dialog appear, what did it say, which button did you
                press, and what opened. A long openFile_s here is YOU, not the
                machine — that is the point. If a dialog appeared, 42b must not
                call this, however well it works for a human."""))
    else:
        record("SKIP", "D4. DragAndDrop.openFile", "not selected by --only")


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=4827, help="MM ZMQ server port")
    ap.add_argument("--dir", default=DEFAULT_DIR,
                    help=f"NDTiff dataset DIRECTORY (default: {DEFAULT_DIR})")
    ap.add_argument("--only", nargs="+", metavar="ID",
                    choices=["D1", "D2", "D3", "D4"],
                    help="run only these checks, e.g. --only D4")
    ap.add_argument("--try-drag", action="store_true",
                    help="run D4, which WILL raise a modal dialog and hold the "
                         "bridge until you answer it")
    ap.add_argument("--timeout", type=float, default=120.0,
                    help="seconds before a bridge call is reported as a stall "
                         "(default 120; D4 needs room for a human)")
    args = ap.parse_args()

    if _new_static_java_class is None:
        raise SystemExit(
            "This spike imports microclaw ON PURPOSE (the whole question is "
            "whether the SHIPPED helper reaches these classes) and the import "
            f"failed: {_MICROCLAW_IMPORT_ERROR!r}\n"
            "Run `pip install -e .` in the repo and try again.")
    if Core is None:
        raise SystemExit(f"pycromanager import failed: {_PYCROMANAGER_IMPORT_ERROR!r}")

    want = set(args.only or ["D1", "D2", "D3", "D4"])
    port, timeout = args.port, args.timeout

    print("=" * 72, flush=True)
    print("design/42 block 42b — the DIRECTORY entry-point spike", flush=True)
    print("=" * 72, flush=True)
    print(f"  when            : {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"  python          : {sys.version.split()[0]} on {platform.platform()}",
          flush=True)
    print(f"  microclaw from  : {getattr(microclaw, '__file__', '?')}", flush=True)
    print(f"  port            : {port}", flush=True)
    print(f"  checks          : {sorted(want)}", flush=True)
    print(f"  directory       : {args.dir}", flush=True)
    print(f"  --try-drag      : {args.try_drag}", flush=True)

    try:
        core = Core(port=port)
        studio = Studio(port=port)
        mm_version = bridge_call("core.get_version_info", core.get_version_info, timeout)
        record("PASS", "0. connect to Micro-Manager", f"MMCore: {mm_version}")
    except BridgeStalled as stall:
        record("FAIL", "0. connect to Micro-Manager", str(stall))
        summarize()
        os._exit(2)
    except Exception as exc:
        record("ERROR", "0. connect to Micro-Manager", f"{type(exc).__name__}: {exc}")
        print("\nCannot continue without a connection. Is MM open with the ZMQ "
              "server enabled on this port?", flush=True)
        summarize()
        return 3

    try:
        run_checks(want, port, core, studio, args, timeout)
    except BridgeStalled as stall:
        record("FAIL", "THE BRIDGE WAS STALLED",
               f"{stall}\npyjavaz holds one lock across every round trip, so "
               "nothing else can use the bridge while that call is blocked — "
               "including a core call that would end an exposure. Look at the "
               "Micro-Manager screen, report what dialog (if any) was up, dismiss "
               "it, and re-run the remaining checks with --only.")
        summarize()
        os._exit(2)

    summarize()
    return 0


def summarize() -> None:
    print("\n" + "=" * 72, flush=True)
    print("WINDOWS THIS SPIKE OPENED — they are left open ON PURPOSE", flush=True)
    print("=" * 72, flush=True)
    for line in _OPENED or ["  (none)"]:
        print(f"  {line}" if not line.startswith("  ") else line, flush=True)
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
        How to read this (design/42, block 42b, gate G0):

          D2 PASS  -> the shipped directory branch is right: MM's own reader,
                      virtual=True, opened MM's own format, and the dimensions
                      agree with what ndstorage reads. This is the answer.
          D2 FAIL  -> nothing reproduces the drag headlessly. 42b must refuse
                      directories by name, and the tool's description must say so.
          D3       -> INFO either way. It records what the drag's YES branch
                      produces, which is what a user would get by hand.
          D4       -> only meaningful if you ran it and answered the dialog. Say
                      which button you pressed. If a dialog appeared at all, 42b
                      is right not to call this path.

        Send back the whole file AND what you saw on screen.
        """), flush=True)


if __name__ == "__main__":
    sys.exit(main())
