#!/usr/bin/env python
"""Spike: can we read the rectangle the operator drew on the MM window?

design/54's one unknown. The rectangle is an `ij.gui.Roi` on the display's
`ImagePlus`, and MM's own ROI toolbar button reads it as
`WindowManager.getCurrentImage().getRoi()`. What is NOT known is whether MM
2.0's snap/live display is visible to `ij.WindowManager` at all: that display is
not a plain ImageWindow, it is MM's DisplayController with an ImageJ bridge
putting a proxy ImagePlus behind it. The bridge is why the rectangle tool works;
whether the proxy is registered is a different question, and this answers it.

BY DEFAULT NOTHING HERE MOVES A STAGE, OPENS A SHUTTER, OR FIRES A CAMERA.
Checks R0-R4 are pure reads. R5 costs ONE exposure and only runs with --snap.

It never opens, closes, focuses or re-titles a window. Microclaw writes nothing
to MM on any exit path (feedback_no_state_change_on_exit) and neither does this.

BEFORE YOU RUN IT
-----------------
1. Micro-Manager open, pycro-manager ZMQ server enabled (Tools > Options).
2. A snap or live image on screen -- the Preview/snap window, not a saved dataset.
3. **Draw a rectangle on it with the ImageJ rectangle tool.** Put it around real
   structure (a cell edge), not empty background. R3 has nothing to read
   otherwise, and that is a SKIP, not a FAIL.
4. Do not push it to the camera. The whole point is that the camera ROI stays
   full-frame; R0 records what it is.

PowerShell:

    python design\\54-display-roi-probe.py 2>&1 | Out-File -Encoding utf8 out54.txt
    python design\\54-display-roi-probe.py --port 4827 --snap 2>&1 | Out-File -Encoding utf8 out54.txt
    Get-Content out54.txt

PowerShell's bare `>` writes UTF-16LE, which made design/42's evidence awkward
to read; `Out-File -Encoding utf8` avoids it.

WHAT IT CHECKS (each isolated; a failing check never stops the ones after it)

  R0. INFO. Camera ROI, binning, image dimensions, whether a Preview window
      exists at all. The baseline every later check is compared against.
  R1. Which ImageJ statics resolve, and what `WindowManager` reports: the ID
      list, `getCurrentImage()`, and every window's title and dimensions.
  R2. THE QUESTION. Is the MM display among them? Decided structurally -- an
      ImagePlus whose dimensions equal the camera ROI's -- not by title text,
      which is localised and version-dependent. Also probes MM's own
      `DisplayWindow` shadow for a `getImagePlus`, the second candidate route.
  R3. Read the drawn Roi and its bounds. Reads the bounds BOTH ways on purpose:
      `rect.x` (public field, correct) and `rect.get_x()` (method, if pyjavaz
      exposes one) and reports whether they agree. Fields and methods use
      different naming conventions over this bridge and the wrong one returns
      wrong data without erroring (design/32 Block 4).
  R4. Coordinate frame. Do the ImagePlus dimensions equal the camera ROI's? If
      they do, the Roi's coordinates index the array `snap_to_numpy` returns
      directly and no offset arithmetic is needed. If they do not, design/54 §2
      is wrong and the conversion has to be measured here.
  R5. --snap only, ONE exposure. Crops that region out of a real frame and
      reports tenengrad and structure_coverage for the full frame and for the
      region. This is the number design/54 exists to move: on the Nikon session
      of 2026-08-18 the full frame read structure_coverage 0.0006 and a flat
      curve while the operator's eye said focus.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
import threading
import time
import traceback

_MICROCLAW_IMPORT_ERROR = None
_PYCROMANAGER_IMPORT_ERROR = None
try:
    from microclaw.controller import _new_static_java_class
    from microclaw.image_analysis import compute_stats, tenengrad
except Exception as exc:  # pragma: no cover - rig environment guard
    _new_static_java_class = compute_stats = tenengrad = None
    _MICROCLAW_IMPORT_ERROR = exc
try:
    from pycromanager import Core, Studio
except Exception as exc:  # pragma: no cover - rig environment guard
    Core = Studio = None
    _PYCROMANAGER_IMPORT_ERROR = exc


# --------------------------------------------------------------------------- #
# Result harness (house style, design/42-ij-dir-spike.py)
# --------------------------------------------------------------------------- #
# PASS  the property held.          FAIL  it did not.
# INFO  no pass/fail -- the answer IS the detail.
# SKIP  not run, with a reason.     ERROR the check itself blew up.
_RESULTS: list[tuple[str, str, str]] = []
_FACTS: dict = {}


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

    Deliberately NOT an Exception: the checks below catch Exception on purpose,
    and a stall must not be swallowed by one of them. It ends the run.
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


def bridge_call(label: str, fn, timeout: float = 30.0):
    """Run one bridge interaction on a daemon thread with a watchdog.

    pyjavaz serialises every round trip under a single lock and there is no way
    to interrupt one in flight, so a modal Java dialog blocks the calling thread
    forever. A daemon thread lets us report that instead of hanging.
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
        if waited >= 5.0 and not nudged:
            print(f"\n  *** {label!r} has not returned after {waited:.1f} s.",
                  flush=True)
            print("  *** LOOK AT THE MICRO-MANAGER SCREEN NOW: a modal dialog "
                  "blocks the single pyjavaz lock.", flush=True)
            nudged = True
    if "error" in box:
        print(box.get("traceback", ""), flush=True)
        raise box["error"]
    return box.get("value")


# --------------------------------------------------------------------------- #
# Java shadow introspection (design/42-ij-open-spike.py conventions)
# --------------------------------------------------------------------------- #
def _norm(name: str) -> str:
    return name.lower().replace("_", "")


def method_names(obj) -> list[str]:
    return sorted(m for m in dir(obj) if not m.startswith("_"))


def resolve(obj, *java_names) -> tuple[str | None, object]:
    """Find a Java member on a bridge shadow whatever pyjavaz named it."""
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


def describe_imageplus(imp) -> dict:
    """Title and dimensions of an ImagePlus shadow, defensively."""
    out: dict = {}
    for key, names in (("title", ("getTitle",)),
                       ("width", ("getWidth",)),
                       ("height", ("getHeight",)),
                       ("n_planes", ("getStackSize",))):
        try:
            value = bridge_call(f"ImagePlus.{names[0]}", jmethod(imp, *names))
            out[key] = str(value) if key == "title" else int(value)
        except Exception as exc:
            out[key] = f"<{type(exc).__name__}: {exc}>"
    return out


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=4827,
                        help="pycro-manager ZMQ port (MM default 4827).")
    parser.add_argument("--snap", action="store_true",
                        help="Run R5, which costs ONE camera exposure. "
                             "On a rig where the camera trigger drives the "
                             "light, that is one dose.")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="Per-call bridge watchdog, seconds.")
    args = parser.parse_args()

    if _MICROCLAW_IMPORT_ERROR is not None:
        print(f"FAIL: cannot import microclaw: {_MICROCLAW_IMPORT_ERROR}")
        print("Has the package been installed here?  pip install -e .")
        return 2
    if _PYCROMANAGER_IMPORT_ERROR is not None:
        print(f"FAIL: cannot import pycromanager: {_PYCROMANAGER_IMPORT_ERROR}")
        return 2

    print(f"design/54 display-ROI probe  port={args.port}  snap={args.snap}",
          flush=True)

    try:
        core = bridge_call("Core()", lambda: Core(port=args.port), args.timeout)
        studio = bridge_call("Studio()", lambda: Studio(port=args.port), args.timeout)
    except BaseException as exc:
        print(f"FAIL: no bridge on port {args.port}: {type(exc).__name__}: {exc}")
        print("Is Micro-Manager open with the ZMQ server enabled?")
        return 2

    # ----------------------------------------------------------------- R0 --
    @check("R0 baseline: camera ROI, binning, Preview window")
    def _r0():
        roi = bridge_call("core.get_roi", core.get_roi, args.timeout)
        _FACTS["camera_roi"] = (int(roi.x), int(roi.y),
                                int(roi.width), int(roi.height))
        try:
            camera = bridge_call("core.get_camera_device",
                                 core.get_camera_device, args.timeout)
            binning = str(bridge_call(
                "core.get_property Binning",
                lambda: core.get_property(camera, "Binning"), args.timeout))
        except Exception as exc:
            camera, binning = "<unknown>", f"<{type(exc).__name__}>"
        _FACTS["binning"] = binning
        try:
            display = bridge_call("live().get_display",
                                  studio.live().get_display, args.timeout)
        except Exception as exc:
            display = None
            print(f"  (get_display raised {type(exc).__name__}: {exc})", flush=True)
        _FACTS["preview_open"] = display is not None
        _FACTS["display_shadow"] = display
        return ("INFO", f"camera={camera}  roi={_FACTS['camera_roi']}  "
                        f"binning={binning}\n"
                        f"preview window exists: {_FACTS['preview_open']}")

    # ----------------------------------------------------------------- R1 --
    @check("R1 ij.WindowManager: what windows does ImageJ admit to having?")
    def _r1():
        wm = _new_static_java_class(args.port, "ij.WindowManager")
        lines = [f"WindowManager shadow: {len(method_names(wm))} members"]

        ids = None
        try:
            get_ids = jmethod(wm, "getIDList")
            ids = bridge_call("WindowManager.getIDList", get_ids, args.timeout)
        except Exception as exc:
            lines.append(f"getIDList: <{type(exc).__name__}: {exc}>")
        id_list = [] if ids is None else [int(i) for i in ids]
        _FACTS["window_ids"] = id_list
        lines.append(f"getIDList -> {id_list or 'null/empty'}")

        described = []
        for wid in id_list:
            try:
                imp = bridge_call(
                    f"WindowManager.getImage({wid})",
                    lambda w=wid: jmethod(wm, "getImage")(w), args.timeout)
                info = describe_imageplus(imp) if imp is not None else {"error": "null"}
            except Exception as exc:
                info = {"error": f"{type(exc).__name__}: {exc}"}
            info["id"] = wid
            described.append(info)
            lines.append(f"  id={wid}  {info}")
        _FACTS["windows"] = described

        current = None
        try:
            current = bridge_call("WindowManager.getCurrentImage",
                                  jmethod(wm, "getCurrentImage"), args.timeout)
        except Exception as exc:
            lines.append(f"getCurrentImage: <{type(exc).__name__}: {exc}>")
        _FACTS["current_image"] = current
        if current is None:
            lines.append("getCurrentImage -> null  "
                         "(no ImageJ window is current; this is the answer R2 reads)")
        else:
            info = describe_imageplus(current)
            _FACTS["current_image_info"] = info
            lines.append(f"getCurrentImage -> {info}")

        status = "PASS" if current is not None or id_list else "FAIL"
        return (status, "\n".join(lines))

    # ----------------------------------------------------------------- R2 --
    @check("R2 THE QUESTION: is the MM snap/live display reachable as an ImagePlus?")
    def _r2():
        lines = []
        cam_w, cam_h = _FACTS.get("camera_roi", (0, 0, 0, 0))[2:]

        # Structural match, not a title match: MM's window title is localised and
        # changes between versions, its dimensions cannot lie about which pixels
        # it holds.
        candidates = []
        current_info = _FACTS.get("current_image_info")
        if current_info and current_info.get("width") == cam_w \
                and current_info.get("height") == cam_h:
            candidates.append(("getCurrentImage", current_info))
        for info in _FACTS.get("windows", []):
            if info.get("width") == cam_w and info.get("height") == cam_h:
                candidates.append((f"getImage({info.get('id')})", info))
        lines.append(f"camera ROI is {cam_w}x{cam_h}; ImagePlus windows matching "
                     f"that shape: {len(candidates)}")
        for via, info in candidates:
            lines.append(f"  via {via}: {info}")

        # Second candidate route: MM's own DisplayWindow shadow.
        display = _FACTS.get("display_shadow")
        if display is None:
            lines.append("MM DisplayWindow: none open, so its route is untested "
                         "here (open a Preview and re-run to test it).")
        else:
            attr, _ = resolve(display, "getImagePlus")
            lines.append(f"MM DisplayWindow shadow: getImagePlus present = "
                         f"{attr is not None}"
                         + (f" (as .{attr})" if attr else ""))
            if attr is None:
                lines.append("  members: " + ", ".join(method_names(display)))
            _FACTS["display_has_imageplus"] = attr is not None

        _FACTS["mm_imageplus_candidates"] = candidates
        if candidates:
            return ("PASS", "\n".join(lines) +
                    "\nThe ImageJ route reaches the MM display. design/54 §2 stands.")
        return ("FAIL", "\n".join(lines) +
                "\nNo ImagePlus matches the camera frame. Either no image is on "
                "screen (check that first -- it is the boring cause), or MM's "
                "display is not registered with WindowManager and design/54 must "
                "ship the literal `region` alone, with `\"drawn\"` refusing by name.")

    # ----------------------------------------------------------------- R3 --
    @check("R3 read the drawn Roi, and read its bounds BOTH ways")
    def _r3():
        candidates = _FACTS.get("mm_imageplus_candidates") or []
        if not candidates:
            raise SkipSpike("R2 found no MM ImagePlus, so there is nothing to "
                            "read a Roi from.")
        wm = _new_static_java_class(args.port, "ij.WindowManager")
        # Prefer getCurrentImage: it is the call MM's own ROI button makes.
        imp = _FACTS.get("current_image")
        via = "getCurrentImage"
        if imp is None:
            wid = candidates[0][1].get("id")
            imp = bridge_call(f"WindowManager.getImage({wid})",
                              lambda: jmethod(wm, "getImage")(wid), args.timeout)
            via = f"getImage({wid})"

        roi = bridge_call("ImagePlus.getRoi", jmethod(imp, "getRoi"), args.timeout)
        if roi is None:
            raise SkipSpike(
                f"via {via}: getRoi() -> null. Nothing is drawn on that window. "
                "Draw a rectangle with the ImageJ rectangle tool and re-run. "
                "A null here is the documented no-selection case, not a failure.")

        lines = [f"via {via}: getRoi() -> a Roi ({len(method_names(roi))} members)"]
        try:
            roi_type = bridge_call("Roi.getTypeAsString",
                                   jmethod(roi, "getTypeAsString"), args.timeout)
            lines.append(f"  type: {roi_type}")
        except Exception as exc:
            lines.append(f"  type: <{type(exc).__name__}: {exc}>")

        rect = bridge_call("Roi.getBounds", jmethod(roi, "getBounds"), args.timeout)

        # java.awt.Rectangle's x/y/width/height are PUBLIC FIELDS. Fields keep
        # their raw camelCase name over this bridge; methods are snake_cased.
        # Reading the wrong one returns wrong data WITHOUT erroring, so read
        # both and say whether they agree.
        field_box, method_box = {}, {}
        for name in ("x", "y", "width", "height"):
            try:
                field_box[name] = int(getattr(rect, name))
            except Exception as exc:
                field_box[name] = f"<{type(exc).__name__}: {exc}>"
            attr, fn = resolve(rect, f"get{name.capitalize()}")
            if fn is None:
                method_box[name] = "<absent>"
            else:
                try:
                    method_box[name] = int(bridge_call(f"Rectangle.{attr}", fn))
                except Exception as exc:
                    method_box[name] = f"<{type(exc).__name__}: {exc}>"
        lines.append(f"  bounds via public FIELDS : {field_box}")
        lines.append(f"  bounds via METHODS       : {method_box}")
        agree = all(field_box.get(k) == method_box.get(k)
                    for k in ("x", "y", "width", "height"))
        lines.append(f"  the two agree: {agree}"
                     + ("" if agree else
                        "  <-- READ THE FIELDS. A disagreement here is exactly the "
                        "silent-wrong-data trap design/32 Block 4 hit."))

        try:
            box = tuple(int(field_box[k]) for k in ("x", "y", "width", "height"))
        except Exception:
            return ("FAIL", "\n".join(lines) + "\nCould not read the bounds as ints.")
        _FACTS["drawn_box"] = box
        lines.append(f"  DRAWN BOX = {box}")
        if box[2] <= 1 or box[3] <= 1:
            return ("FAIL", "\n".join(lines) +
                    "\nDegenerate box (a line or a point) -- it has no gradient to "
                    "measure. design/54 must refuse this before the sweep.")
        return ("PASS", "\n".join(lines))

    # ----------------------------------------------------------------- R4 --
    @check("R4 coordinate frame: does the drawn box index the snapped array?")
    def _r4():
        box = _FACTS.get("drawn_box")
        if box is None:
            raise SkipSpike("R3 read no box.")
        cam_x, cam_y, cam_w, cam_h = _FACTS["camera_roi"]
        info = (_FACTS.get("current_image_info")
                or (_FACTS["mm_imageplus_candidates"][0][1]
                    if _FACTS.get("mm_imageplus_candidates") else {}))
        imp_w, imp_h = info.get("width"), info.get("height")
        x, y, w, h = box
        lines = [
            f"camera ROI      : x={cam_x} y={cam_y} w={cam_w} h={cam_h} "
            f"binning={_FACTS.get('binning')}",
            f"ImagePlus frame : w={imp_w} h={imp_h}",
            f"drawn box       : x={x} y={y} w={w} h={h}",
        ]
        same_frame = (imp_w == cam_w and imp_h == cam_h)
        lines.append(f"ImagePlus frame == camera ROI frame: {same_frame}")
        inside = (0 <= x and 0 <= y and x + w <= (imp_w or 0)
                  and y + h <= (imp_h or 0))
        lines.append(f"box lies inside that frame: {inside}")
        if same_frame and inside:
            lines.append(
                "So image[y:y+h, x:x+w] on the array snap_to_numpy returns is the "
                "drawn region, with no offset arithmetic. design/54 §2 stands.")
            return ("PASS", "\n".join(lines))
        if not same_frame:
            lines.append(
                "The display is NOT showing the current camera frame -- a stale "
                "window, a different binning, or a saved dataset. design/54's "
                "stale-box refusal is the live case, and this run is evidence for "
                "it. Say which of the three it was.")
        return ("FAIL", "\n".join(lines))

    # ----------------------------------------------------------------- R5 --
    @check("R5 does the region actually move the metric? (ONE exposure)")
    def _r5():
        if not args.snap:
            raise SkipSpike("--snap not given, so no exposure was taken.")
        box = _FACTS.get("drawn_box")
        if box is None:
            raise SkipSpike("R3 read no box, so there is nothing to crop.")
        live = studio.live()
        if bridge_call("live.is_live_mode_on", live.is_live_mode_on, args.timeout):
            raise SkipSpike(
                "Live mode is ON. snap through the core while a sequence runs "
                "throws, and live().snap(True) does not throw -- it wedges the "
                "single pyjavaz lock forever (design/14 V1). Turn live off and "
                "re-run.")

        from microclaw.image_analysis import snap_to_numpy

        class _Ctrl:
            pass
        shim = _Ctrl()
        shim.core = core
        image = bridge_call("snap_to_numpy", lambda: snap_to_numpy(shim),
                            max(args.timeout, 60.0))

        x, y, w, h = box
        if y + h > image.shape[0] or x + w > image.shape[1]:
            return ("FAIL",
                    f"box {box} does not fit the snapped frame {image.shape}. "
                    "This is the stale-box case design/54 refuses; it is real, "
                    "and it happened here.")
        region = image[y:y + h, x:x + w]

        full_stats = compute_stats(image)
        region_stats = compute_stats(region)
        lines = [
            f"frame {image.shape[1]}x{image.shape[0]}  region {w}x{h} at ({x},{y})"
            f"  = {100.0 * region.size / image.size:.2f}% of the frame",
            "",
            f"{'':22}{'FULL FRAME':>14}{'REGION':>14}",
            f"{'tenengrad':22}{full_stats.focus_metric:>14.1f}"
            f"{region_stats.focus_metric:>14.1f}",
            f"{'structure_coverage':22}{full_stats.structure_coverage:>14.4f}"
            f"{region_stats.structure_coverage:>14.4f}",
            f"{'signal_coverage':22}{full_stats.signal_coverage:>14.4f}"
            f"{region_stats.signal_coverage:>14.4f}",
            f"{'background_level':22}{full_stats.background_level:>14.1f}"
            f"{region_stats.background_level:>14.1f}",
            f"{'max_intensity':22}{full_stats.max_intensity:>14.1f}"
            f"{region_stats.max_intensity:>14.1f}",
        ]
        ratio = (region_stats.structure_coverage
                 / full_stats.structure_coverage) if full_stats.structure_coverage else None
        lines.append("")
        lines.append(f"structure_coverage ratio region/full: "
                     f"{'n/a' if ratio is None else f'{ratio:.1f}x'}")
        lines.append(
            "This is ONE plane, so it is not a focus curve and proves nothing "
            "about convergence. It measures the dilution design/54 is about: how "
            "much more of the region is structure than of the whole frame.")
        return ("INFO", "\n".join(lines))

    summarize(args)
    return 0


def summarize(args) -> None:
    print("\n" + "=" * 72, flush=True)
    print("PROBE SUMMARY", flush=True)
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
        How to read this (design/54):

          R2 PASS + R3 PASS + R4 PASS
              -> `region="drawn"` is buildable exactly as §2 describes. Ship it.
          R2 PASS, R3 SKIP (getRoi -> null)
              -> the window is reachable but nothing was drawn on it. Draw a
                 rectangle and re-run; this is not evidence either way.
          R2 FAIL
              -> MM's display is not registered with WindowManager. §1 (a literal
                 [x,y,w,h] region) still ships and is most of the value; "drawn"
                 refuses by name. Before concluding that, confirm an image was
                 actually on screen -- an empty desktop fails R2 identically.
          R4 FAIL with same_frame False
              -> the coordinate frames differ. §2's "no offset arithmetic" claim
                 is wrong and the conversion must be measured before anything is
                 written. Say what was on screen.
          R3 fields and methods DISAGREE
              -> report this loudly whatever else passed. It means the bridge's
                 field/method naming split bites here too.

        This probe opened no window and closed none. Send back the whole file.
        """), flush=True)


if __name__ == "__main__":
    sys.exit(main())
