#!/usr/bin/env python
"""design/30 spike: are MMStudio Album and MDA usable over pyjavaz/ZMQ?

Run this MANUALLY on the Windows microscope computer with Micro-Manager open and
its pycro-manager server enabled.  The default run is read-only: it discovers
the bridge surfaces, reads Album state, reads the MDA dialog's SequenceSettings,
and checks that returned Java proxies remain usable on later calls.

    python design/30-album-mda-spike.py
    python design/30-album-mda-spike.py --port 4827
    python design/30-album-mda-spike.py --album-snap
    python design/30-album-mda-spike.py --mda-roundtrip
    python design/30-album-mda-spike.py --run-mda

Every run writes ``30-album-mda-spike-output-<timestamp>.txt`` beside this file.
Send that file back; it records proxy types, exact resolved method spellings,
accepted argument representations, return values, and proxy reuse.

SAFETY
------
The default run fires no camera and changes no hardware. ``--album-snap`` fires
the current camera(s) ONCE using MMStudio's acquisition manager and adds the
returned Image object(s) to Album. It does not change channel, exposure, stage,
or illumination, but the current hardware state still determines the exposure.

``--mda-roundtrip`` passes the same immutable SequenceSettings object back to
the MDA dialog. ``--run-mda`` is intentionally interactive and much more
consequential: it runs the GUI's CURRENT MDA exactly as configured, including
all channels, illumination, positions, Z moves, time points, saving, and
autofocus. The spike prints the resolved settings and requires a typed phrase
immediately before submission. Never use it until those settings and the sample
are safe. The run is blocking so the returned Datastore can be inspected.

The Java API names below are candidates, not assumed bridge contracts. Each is
resolved from alternatives and logged. In particular, methods are normally
snake_cased while Java fields remain camelCase, and Java collections/objects may
or may not survive a trip back into Java through pyjavaz.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from pycromanager import Core, Studio


RESULTS: list[tuple[str, str, str]] = []
WATCH = {"name": None, "step": None, "deadline": 0.0}
OUT: Path | None = None


class Tee:
    def __init__(self, path: Path):
        self.stdout = sys.stdout
        self.file = path.open("w", encoding="utf-8")

    def write(self, value: str) -> None:
        self.stdout.write(value)
        self.file.write(value)
        self.file.flush()

    def flush(self) -> None:
        self.stdout.flush()
        self.file.flush()


class SkipSpike(Exception):
    pass


def record(status: str, name: str, detail: str = "") -> None:
    RESULTS.append((status, name, detail))
    print(f"[{status:4}] {name}" + (f"\n       {detail}" if detail else ""), flush=True)


def step(detail: str) -> None:
    WATCH["step"] = detail
    print(f"    step: {detail}", flush=True)


def watchdog() -> None:
    while True:
        time.sleep(1)
        if WATCH["name"] and time.time() > float(WATCH["deadline"]):
            record("HANG", str(WATCH["name"]),
                   f"bridge did not reply; last step={WATCH['step']!r}")
            summarize()
            sys.stdout.flush()
            os._exit(2)


def check(name: str, timeout: float = 45.0):
    def decorator(fn):
        print(f"\n--- {name} ---", flush=True)
        WATCH.update(name=name, step="check start", deadline=time.time() + timeout)
        try:
            detail = fn()
            record("PASS", name, "" if detail is None else str(detail))
        except SkipSpike as exc:
            record("SKIP", name, str(exc))
        except Exception as exc:
            record("FAIL", name, f"{type(exc).__name__}: {exc}")
            traceback.print_exc(file=sys.stdout)
        finally:
            WATCH.update(name=None, step=None, deadline=0.0)
        return fn
    return decorator


def public_names(obj: Any) -> list[str]:
    try:
        return sorted(name for name in dir(obj) if not name.startswith("_"))
    except Exception as exc:
        return [f"<dir failed: {type(exc).__name__}: {exc}>"]


def resolve(obj: Any, candidates: list[str]) -> tuple[str, Callable]:
    errors = []
    for name in candidates:
        try:
            value = getattr(obj, name)
            if callable(value):
                return name, value
            errors.append(f"{name}=non-callable {type(value).__name__}")
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}")
    raise AttributeError(f"none of {candidates!r} resolved ({'; '.join(errors)}); "
                         f"public names={public_names(obj)}")


def call(obj: Any, candidates: list[str], *args) -> tuple[str, Any]:
    name, fn = resolve(obj, candidates)
    step(f"{type(obj).__name__}.{name}({', '.join(type(a).__name__ for a in args)})")
    return name, fn(*args)


def describe_proxy(value: Any) -> str:
    if value is None:
        return "None"
    names = public_names(value)
    return f"python_type={type(value).__module__}.{type(value).__name__}; public_names={names}"


SETTING_GETTERS = [
    "prefix", "root", "save", "save_mode", "should_display_images",
    "use_frames", "num_frames", "interval_ms", "use_custom_intervals",
    "custom_intervals_ms", "use_position_list", "use_slices", "slices",
    "slice_z_bottom_um", "slice_z_top_um", "slice_z_step_um", "relative_z_slice",
    "use_channels", "channel_group", "channels", "use_autofocus",
    "skip_autofocus_count", "keep_shutter_open_channels",
    "keep_shutter_open_slices", "acq_order_mode", "camera_timeout", "comment",
]


def safe_value(value: Any) -> str:
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    try:
        return repr(list(value))
    except Exception:
        return f"<{type(value).__name__} proxy>"


def read_settings(settings: Any) -> dict[str, str]:
    values: dict[str, str] = {}
    for candidate in SETTING_GETTERS:
        spellings = [candidate]
        # Some bridge builds expose raw camelCase despite conversion being on.
        parts = candidate.split("_")
        camel = parts[0] + "".join(part.title() for part in parts[1:])
        if camel != candidate:
            spellings.append(camel)
        try:
            name, fn = resolve(settings, spellings)
            values[name] = safe_value(fn())
        except Exception as exc:
            values[candidate] = f"<UNREADABLE: {type(exc).__name__}: {exc}>"
    return values


def datastore_state(store: Any) -> str:
    if store is None:
        return "datastore=None"
    fields = [describe_proxy(store)]
    for candidates, label in [
        (["get_num_images", "getNumImages"], "num_images"),
        (["is_frozen", "isFrozen"], "frozen"),
        (["get_name", "getName"], "name"),
        (["get_save_path", "getSavePath"], "save_path"),
    ]:
        try:
            used, value = call(store, candidates)
            fields.append(f"{label}={value!r} via {used}")
        except Exception as exc:
            fields.append(f"{label}=UNREADABLE({type(exc).__name__}: {exc})")
    return "\n       ".join(fields)


def summarize() -> None:
    print("\n" + "=" * 72 + "\nSUMMARY")
    for status, name, detail in RESULTS:
        print(f"  {status:4}  {name}" + (f" — {detail.splitlines()[0]}" if detail else ""))
    print(f"\nOutput: {OUT}", flush=True)


def main() -> int:
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--album-snap", action="store_true",
                        help="FIRE current camera(s) once and add returned Image(s) to Album")
    parser.add_argument("--mda-roundtrip", action="store_true",
                        help="pass the unchanged settings proxy back into the MDA dialog")
    parser.add_argument("--run-mda", action="store_true",
                        help="RUN the GUI's current MDA after typed confirmation")
    parser.add_argument("--no-prompt", action="store_true",
                        help="do not ask GUI observation questions (cannot bypass MDA confirmation)")
    args = parser.parse_args()

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    OUT = Path(__file__).with_name(f"30-album-mda-spike-output-{stamp}.txt")
    sys.stdout = Tee(OUT)
    threading.Thread(target=watchdog, daemon=True, name="spike-watchdog").start()

    print(__doc__)
    print(f"port={args.port}; album_snap={args.album_snap}; "
          f"mda_roundtrip={args.mda_roundtrip}; run_mda={args.run_mda}")

    try:
        core = Core(port=args.port)
        studio = Studio(port=args.port)
        version = core.get_version_info()
        record("PASS", "0. connect Core + Studio", str(version))
    except Exception as exc:
        record("FAIL", "0. connect Core + Studio", f"{type(exc).__name__}: {exc}")
        summarize()
        return 1

    state: dict[str, Any] = {}

    @check("1. resolve studio.album() and inspect Album proxy")
    def album_surface():
        used, album = call(studio, ["album", "get_album", "getAlbum"])
        state["album"] = album
        return f"resolved Studio.{used}; {describe_proxy(album)}"

    @check("2. read Album datastore and reuse its proxy")
    def album_state():
        album = state.get("album")
        if album is None:
            raise SkipSpike("Album proxy unavailable")
        used1, store1 = call(album, ["get_datastore", "getDatastore"])
        # A later bridge call through the same returned proxy tests its lifetime.
        time.sleep(0.1)
        used2, store2 = call(album, ["get_datastore", "getDatastore"])
        state["album_store_before"] = store2
        return (f"first via {used1}, second via {used2}; album proxy reusable=yes\n       "
                f"first={describe_proxy(store1)}\n       second state: {datastore_state(store2)}")

    @check("3. snap through AcquisitionManager and add Java Image(s) to Album", 90)
    def album_add():
        if not args.album_snap:
            raise SkipSpike("pass --album-snap (FIRES current camera(s) once)")
        album = state.get("album")
        if album is None:
            raise SkipSpike("Album proxy unavailable")
        _, acq = call(studio, ["acquisitions", "get_acquisition_manager",
                              "getAcquisitionManager"])
        snap_name, images = call(acq, ["snap"])
        state["snap_images"] = images
        try:
            image_count = len(images)
        except Exception:
            image_count = "unknown"
        detail = [f"{snap_name} returned {describe_proxy(images)}; len={image_count}"]

        # This directly answers whether pyjavaz accepts the returned Java
        # collection as addImages(Collection). It adds each image only once.
        try:
            add_name, created = call(album, ["add_images", "addImages"], images)
            detail.append(f"collection encoding accepted by {add_name}; returned {created!r}")
        except Exception as collection_exc:
            detail.append("collection encoding REJECTED: "
                          f"{type(collection_exc).__name__}: {collection_exc}")
            # Fall back to the documented single-image API, iterating whatever
            # representation pyjavaz decoded. This also proves Image proxy input.
            try:
                decoded = list(images)
            except Exception as exc:
                raise RuntimeError("cannot iterate returned snap collection for add_image; "
                                   f"{type(exc).__name__}: {exc}") from collection_exc
            outcomes = []
            for image in decoded:
                add_name, created = call(album, ["add_image", "addImage"], image)
                outcomes.append(f"{add_name}({type(image).__name__})->{created!r}")
            detail.append("single Image proxy encoding accepted: " + ", ".join(outcomes))

        _, after = call(album, ["get_datastore", "getDatastore"])
        detail.append("Album after add: " + datastore_state(after))
        if not args.no_prompt:
            WATCH.update(name=None, step=None, deadline=0.0)
            answer = input("\nGUI CHECK: did an Album window repaint and show the new image(s)? ").strip()
            detail.append(f"operator GUI observation={answer or 'UNANSWERED'}")
        return "\n       ".join(detail)

    @check("4. resolve studio.acquisitions() and inspect manager proxy")
    def mda_surface():
        used, acq = call(studio, ["acquisitions", "get_acquisition_manager",
                                  "getAcquisitionManager"])
        state["acq"] = acq
        interesting = [name for name in public_names(acq) if any(tag in name.lower()
                       for tag in ("acqui", "setting", "sequence", "snap", "run"))]
        return f"resolved Studio.{used}; type={type(acq).__name__}; relevant_names={interesting}"

    @check("5. read current MDA SequenceSettings and reuse proxy")
    def mda_settings():
        acq = state.get("acq")
        if acq is None:
            raise SkipSpike("AcquisitionManager proxy unavailable")
        used, settings = call(acq, ["get_acquisition_settings", "getAcquisitionSettings"])
        state["settings"] = settings
        first = read_settings(settings)
        time.sleep(0.1)
        # Repeat a scalar call after many bridge round trips: concrete lifetime test.
        prefix_name, prefix_fn = resolve(settings, ["prefix"])
        prefix_again = prefix_fn()
        lines = [f"resolved {used}; {describe_proxy(settings)}",
                 f"proxy reuse: {prefix_name}() again -> {prefix_again!r}",
                 "resolved settings:"]
        lines.extend(f"  {key} = {value}" for key, value in first.items())
        return "\n       ".join(lines)

    @check("6. pass unchanged SequenceSettings back to AcquisitionManager")
    def mda_roundtrip():
        if not args.mda_roundtrip:
            raise SkipSpike("pass --mda-roundtrip to apply the unchanged object to the GUI")
        acq, settings = state.get("acq"), state.get("settings")
        if acq is None or settings is None:
            raise SkipSpike("manager/settings proxy unavailable")
        used, returned = call(acq, ["set_acquisition_settings", "setAcquisitionSettings"], settings)
        _, reread = call(acq, ["get_acquisition_settings", "getAcquisitionSettings"])
        before, after = read_settings(settings), read_settings(reread)
        differences = {k: (before.get(k), after.get(k)) for k in before if before.get(k) != after.get(k)}
        detail = (f"{used}(SequenceSettings proxy) accepted; return={returned!r}; "
                  f"reread differences={differences or 'none'}")
        if not args.no_prompt:
            WATCH.update(name=None, step=None, deadline=0.0)
            answer = input("\nGUI CHECK: did the MDA dialog visibly remain/update correctly? ").strip()
            detail += f"; operator GUI observation={answer or 'UNANSWERED'}"
        return detail

    @check("7. run GUI's current MDA and inspect returned Datastore", 3600)
    def run_mda():
        if not args.run_mda:
            raise SkipSpike("pass --run-mda only when the printed current settings are safe")
        acq = state.get("acq")
        if acq is None:
            raise SkipSpike("AcquisitionManager proxy unavailable")
        WATCH.update(name=None, step=None, deadline=0.0)
        print("\nDANGER: this will now execute the GUI's CURRENT MDA, including hardware "
              "motion, illumination, autofocus, timing, and saving shown above.")
        phrase = input("Type RUN CURRENT MDA exactly to continue: ").strip()
        if phrase != "RUN CURRENT MDA":
            raise SkipSpike("operator did not provide the required confirmation phrase")
        WATCH.update(name="7. run GUI's current MDA and inspect returned Datastore",
                     step="submitting acquisition", deadline=time.time() + 3600)
        used, store = call(acq, ["run_acquisition", "runAcquisition"])
        detail = f"{used} returned after completion; {datastore_state(store)}"
        # Returned proxy remains usable after another manager call.
        running_name, running = call(acq, ["is_acquisition_running", "isAcquisitionRunning"])
        detail += f"\n       subsequent manager call {running_name}={running!r}"
        detail += f"\n       datastore reused: {datastore_state(store)}"
        if not args.no_prompt:
            WATCH.update(name=None, step=None, deadline=0.0)
            answer = input("\nGUI CHECK: did MDA/display repaint and complete as expected? ").strip()
            detail += f"\n       operator GUI observation={answer or 'UNANSWERED'}"
        return detail

    summarize()
    failures = [status for status, _, _ in RESULTS if status in {"FAIL", "HANG"}]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
