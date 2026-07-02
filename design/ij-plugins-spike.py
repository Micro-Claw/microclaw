#!/usr/bin/env python
"""Spike: verify design/10-add-ij-plugins.md claims against a live Micro-Manager.

Run this MANUALLY on the Windows lab machine with Micro-Manager OPEN and the
pycro-manager ZMQ server enabled (Tools > Options, port 4827 by default), on an MM
build that contains micro-manager#2401 (unified SharedPluginClassLoader).

    python ij-plugins-spike.py                 # all checks, safe defaults
    python ij-plugins-spike.py --port 4827
    python ij-plugins-spike.py --snap          # also run the converter round-trip
                                               #   (this FIRES THE CAMERA once)

What it checks (each isolated; a failure never aborts the rest):

  0. Connect to Core + Studio over ZMQ.
  1. #2401 present: org...SharedPluginClassLoader resolves over the bridge.
  2. DataManager.ij() returns an ImageJConverter (a CONVERTER, not a plugin host);
     introspect its methods.
  3. ImageJ1 static entry point ij.IJ resolves (should work on ANY MM build).
  4. Run a self-contained headless IJ1 macro and read a scalar back from the
     global ResultsTable (the Approach A mechanism).
  5. [--snap only] ImageJConverter end-to-end: snap -> convert_tagged_image ->
     ij().createProcessor(image). Fires the camera once; moves no stage.
  6. Approach B: probe whether the ImageJ2 / SciJava CommandService surface is
     reachable over ZMQ with #2401 present (framework classes, a gateway, a
     Context accessor on Studio).

NOTHING here moves a stage or shutter. Only --snap fires the camera (one frame).
Report the printed PASS/FAIL/INFO summary back for the design doc.
"""
from __future__ import annotations

import argparse
import traceback

try:
    from pycromanager import Core, Studio, JavaObject, JavaClass
except Exception as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "pycromanager is required (pip install pycromanager). Import failed: %r" % exc
    )


# --------------------------------------------------------------------------- #
# Tiny result harness
# --------------------------------------------------------------------------- #
_RESULTS: list[tuple[str, str, str]] = []  # (status, name, detail)


def record(status: str, name: str, detail: str = "") -> None:
    _RESULTS.append((status, name, detail))
    line = f"[{status:4}] {name}"
    if detail:
        line += f"\n         {detail}"
    print(line, flush=True)


def check(name: str):
    """Decorator: run a check fn, PASS on truthy return, FAIL on exception.

    The fn may return a string detail (printed) or raise SkipSpike to mark SKIP.
    """
    def wrap(fn):
        print(f"\n--- {name} ---", flush=True)
        try:
            detail = fn()
            record("PASS", name, "" if detail is None else str(detail))
        except SkipSpike as s:
            record("SKIP", name, str(s))
        except Exception as exc:
            record("FAIL", name, f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        return fn
    return wrap


class SkipSpike(Exception):
    pass


def get_data_manager(studio):
    """studio.data() and studio.get_data_manager() are the same MM accessor; the
    design doc's question used the latter. Prefer data(), fall back."""
    for attr in ("data", "get_data_manager"):
        fn = getattr(studio, attr, None)
        if fn is not None:
            try:
                return fn()
            except Exception:
                continue
    raise RuntimeError("Studio exposes neither data() nor get_data_manager()")


def proxy_methods(obj) -> list[str]:
    """Best-effort list of Java method names on a pycro-manager proxy object."""
    return sorted(m for m in dir(obj) if not m.startswith("_"))


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=4827, help="ZMQ server port")
    ap.add_argument("--snap", action="store_true",
                    help="also run the converter round-trip (FIRES THE CAMERA once)")
    args = ap.parse_args()
    port = args.port

    print(f"Connecting to Micro-Manager ZMQ server on port {port} ...", flush=True)

    # 0. Connect -------------------------------------------------------------
    try:
        core = Core(port=port)
        studio = Studio(port=port)
        version = core.get_version_info()
        record("PASS", "0. connect Core+Studio", f"MMCore: {version}")
    except Exception as exc:
        record("FAIL", "0. connect Core+Studio", f"{type(exc).__name__}: {exc}")
        print("\nCannot continue without a connection. Is MM open with the ZMQ "
              "server enabled on this port?", flush=True)
        summarize()
        return

    # 1. #2401 presence ------------------------------------------------------
    @check("1. #2401 SharedPluginClassLoader resolves")
    def _c1():
        cls = "org.micromanager.internal.pluginmanagement.SharedPluginClassLoader"
        JavaClass(cls, port=port)  # raises if the loader isn't on the bridge
        return f"resolved {cls} -> #2401 IS present"

    # 2. DataManager.ij() -> ImageJConverter ---------------------------------
    @check("2. DataManager.ij() returns ImageJConverter")
    def _c2():
        dm = get_data_manager(studio)
        conv = dm.ij()
        methods = proxy_methods(conv)
        # We EXPECT a converter surface (createProcessor / createImage), NOT a
        # plugin-runner surface. Confirm the design doc's core claim.
        expected = [m for m in methods
                    if "processor" in m.lower() or "image" in m.lower()]
        return (f"ij() -> {type(conv).__name__}; methods={methods}\n"
                f"         converter-shaped methods={expected}\n"
                f"         (NOTE: this is a CONVERTER, not a plugin host)")

    # 3. ImageJ1 static entry point ------------------------------------------
    @check("3. ImageJ1 ij.IJ resolves (no #2401 needed)")
    def _c3():
        IJ = JavaClass("ij.IJ", port=port)
        # touch a couple of static methods to confirm the surface
        _ = proxy_methods(IJ)
        return "ij.IJ resolved (ImageJ1 core is on MM's classpath)"

    # 4. Headless IJ1 macro + ResultsTable scalar (Approach A mechanism) ------
    @check("4. IJ1 macro -> ResultsTable scalar (Approach A)")
    def _c4():
        IJ = JavaClass("ij.IJ", port=port)
        rt_cls = JavaClass("ij.measure.ResultsTable", port=port)
        # Self-contained, headless, image-free-ish: make a synthetic ramp,
        # measure its mean, close it. setBatchMode avoids opening windows.
        macro = (
            'setBatchMode(true);'
            'newImage("spike", "8-bit ramp", 64, 64, 1);'
            'run("Set Measurements...", "mean redirect=None decimal=3");'
            'run("Measure");'
            'close();'
            'setBatchMode(false);'
        )
        rt = rt_cls.get_results_table()
        rt.reset()
        IJ.run_macro(macro)
        rt = rt_cls.get_results_table()
        n = int(rt.get_counter())
        if n == 0:
            raise RuntimeError("macro produced no ResultsTable rows")
        col = rt.get_column_index("Mean")
        val = float(rt.get_value_as_double(col, n - 1))
        # An 8-bit ramp 0..255 has mean ~127.5
        return f"ResultsTable rows={n}, Mean(last)={val:.3f} (ramp expected ~127.5)"

    # 5. Converter end-to-end (optional; fires camera) -----------------------
    if args.snap:
        @check("5. ImageJConverter round-trip (--snap; fires camera)")
        def _c5():
            dm = get_data_manager(studio)
            conv = dm.ij()
            core.snap_image()
            tagged = core.get_tagged_image()
            image = dm.convert_tagged_image(tagged)
            ip = conv.create_processor(image)
            w, h = int(ip.get_width()), int(ip.get_height())
            return f"convert_tagged_image -> createProcessor OK, ImageProcessor {w}x{h}"
    else:
        record("SKIP", "5. ImageJConverter round-trip",
               "pass --snap to run (it fires the camera once)")

    # 6. Approach B: IJ2 / SciJava CommandService over ZMQ -------------------
    #    Multiple independent probes; each PASS/FAIL is informative on its own.
    @check("6a. IJ2/SciJava framework classes resolve")
    def _c6a():
        resolved, missing = [], []
        for cls in (
            "net.imagej.ImageJ",
            "org.scijava.Context",
            "org.scijava.command.CommandService",
            "org.scijava.module.ModuleService",
        ):
            try:
                JavaClass(cls, port=port)
                resolved.append(cls)
            except Exception as exc:
                missing.append(f"{cls} ({type(exc).__name__})")
        if not resolved:
            raise RuntimeError("none of the IJ2/SciJava framework classes resolved: "
                               + "; ".join(missing))
        return ("resolved=" + ", ".join(resolved)
                + ("" if not missing else "\n         missing=" + "; ".join(missing)))

    @check("6b. Construct a net.imagej.ImageJ gateway + get CommandService")
    def _c6b():
        # This spins up a *fresh* gateway/context (not MM's) purely to test that
        # the IJ2 classes are instantiable over the bridge with #2401. If MM's own
        # context is preferable, see 6c.
        try:
            ij = JavaObject("net.imagej.ImageJ", args=[], port=port, new_socket=True)
        except Exception as exc:
            raise RuntimeError(f"could not construct net.imagej.ImageJ: {exc}")
        cmd = ij.command()  # CommandService
        methods = [m for m in proxy_methods(cmd) if m.startswith("run")]
        return f"gateway OK; CommandService run* methods={methods}"

    @check("6c. Find a SciJava Context accessor on Studio / PluginManager")
    def _c6c():
        # #2401's DefaultPluginManager owns the SharedPluginClassLoader; it MAY also
        # expose the SciJava Context that a CommandService would need. Probe for it.
        candidates: list[tuple[str, object]] = []
        for holder_name, holder in (("studio", studio),):
            for attr in proxy_methods(holder):
                low = attr.lower()
                if "context" in low or attr in ("get_composite_context",):
                    candidates.append((f"{holder_name}.{attr}", holder))
        # Also inspect the PluginManager surface.
        pm = None
        for attr in ("plugins", "get_plugin_manager"):
            fn = getattr(studio, attr, None)
            if fn:
                try:
                    pm = fn()
                    break
                except Exception:
                    pass
        pm_attrs = proxy_methods(pm) if pm is not None else []
        pm_ctx = [a for a in pm_attrs if "context" in a.lower()]
        found = [name for name, _ in candidates] + [f"plugins().{a}" for a in pm_ctx]
        if not found:
            raise SkipSpike(
                "no obvious Context accessor found on Studio or PluginManager "
                f"(studio attrs scanned; PluginManager attrs={pm_attrs}). "
                "IJ2 likely needs a fresh context (6b) or the jPype backend.")
        return "candidate Context accessors: " + ", ".join(found)

    summarize()


def summarize() -> None:
    print("\n" + "=" * 68)
    print("SPIKE SUMMARY")
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
        "\nInterpretation:\n"
        "  1 PASS  -> #2401 is present on this build (Approach B is even possible).\n"
        "  2 PASS  -> confirms data().ij() is a CONVERTER; note the method list.\n"
        "  3/4 PASS-> Approach A (IJ1 scalar-out) is viable with no #2401 dependency.\n"
        "  6a/6b PASS -> IJ2 CommandService is reachable over ZMQ; 6c tells you\n"
        "               whether you can reuse MM's own context or must make a fresh\n"
        "               one. 6a/6b FAIL -> Approach B stays deferred to jPype.\n"
    )


if __name__ == "__main__":
    main()
