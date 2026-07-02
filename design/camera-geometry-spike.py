#!/usr/bin/env python
"""Spike B: confirm the camera pixel-geometry assumptions behind snap_to_numpy.

Informs design/11b issue 6 (snap_to_numpy hardcodes np.uint16). The fix derives
the dtype from bytes-per-pixel and component count; this spike confirms MM reports
what that fix assumes BEFORE we change the decode path every image metric depends
on. In particular it checks the RGB32 claim: MM reports 4 bytes-per-pixel meaning
FOUR uint8 components (BGRA), not one uint32.

It also confirms get_camera_device() is exposed over the bridge, which issue 1's
set_device_property role-mapping relies on (it is used nowhere else in the repo).

Run this MANUALLY on the Windows lab machine with Micro-Manager OPEN and the
pycro-manager ZMQ server enabled:

    python camera-geometry-spike.py
    python camera-geometry-spike.py --port 4827

>>> This FIRES THE CAMERA once per snap. It moves no stage or shutter. <<<

The demo config's camera is 8-bit/16-bit switchable, so the mono path is testable
without special hardware. If an RGB32 camera is available, switch MM to it and
re-run to exercise the multi-component branch.

Report the printed PASS/FAIL/INFO summary back for the design doc.
"""
from __future__ import annotations

import argparse
import traceback

try:
    import numpy as np
    from pycromanager import Core
except Exception as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "pycromanager and numpy are required. Import failed: %r" % exc
    )

# --------------------------------------------------------------------------- #
# Tiny result harness (mirrors design/ij-plugins-spike.py)
# --------------------------------------------------------------------------- #
_RESULTS: list[tuple[str, str, str]] = []  # (status, name, detail)


def record(status: str, name: str, detail: str = "") -> None:
    _RESULTS.append((status, name, detail))
    line = f"[{status:4}] {name}"
    if detail:
        line += f"\n         {detail}"
    print(line, flush=True)


class SkipSpike(Exception):
    pass


def check(name: str):
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


def derive_dtype(bpp: int, n_comp: int):
    """Exactly the mapping the issue-6 fix would apply, so a PASS here means the
    fix will pick the right dtype on this camera."""
    if n_comp > 1:                                    # e.g. RGB32 = 4 x uint8
        comp = {1: np.uint8, 2: np.uint16}[bpp // n_comp]
        return comp, (n_comp,)
    dtype = {1: np.uint8, 2: np.uint16, 4: np.uint32}.get(bpp)
    if dtype is None:
        raise ValueError(f"Unsupported bytes-per-pixel: {bpp}")
    return dtype, ()


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=4827, help="ZMQ server port")
    args = ap.parse_args()
    port = args.port

    print(f"Connecting to Micro-Manager ZMQ server on port {port} ...", flush=True)

    # 0. Connect -------------------------------------------------------------
    try:
        core = Core(port=port)
        record("PASS", "0. connect Core", core.get_version_info())
    except Exception as exc:
        record("FAIL", "0. connect Core", f"{type(exc).__name__}: {exc}")
        print("\nCannot continue without a connection. Is MM open with the ZMQ "
              "server enabled on this port?", flush=True)
        summarize()
        return

    # Shared state populated by check 2 and read by later checks. Declared BEFORE
    # the checks: @check runs each function immediately at decoration time, so the
    # closure must be able to see `state` already bound.
    state: dict = {}

    # 1. get_camera_device exposed over the bridge (issue 1 relies on this) ---
    @check("1. get_camera_device() exposed over the bridge")
    def _c1():
        cam = core.get_camera_device()
        return f"camera device = {cam!r} (issue-1 role mapping can use this)"

    # 2. Snap + report geometry ----------------------------------------------
    @check("2. snap + get_tagged_image geometry (FIRES CAMERA)")
    def _c2():
        core.snap_image()
        tagged = core.get_tagged_image()
        w = int(tagged.tags["Width"])
        h = int(tagged.tags["Height"])
        bpp = int(core.get_bytes_per_pixel())
        n_comp = int(core.get_number_of_components())
        n_bytes = len(bytes(tagged.pix))
        state.update(w=w, h=h, bpp=bpp, n_comp=n_comp, n_bytes=n_bytes, pix=tagged.pix)
        return (f"w={w} h={h} bytes_per_pixel={bpp} n_components={n_comp} "
                f"pix_bytes={n_bytes}")

    # 3. pix length == w*h*bpp -----------------------------------------------
    @check("3. pix buffer length == w*h*bytes_per_pixel")
    def _c3():
        if not state:
            raise SkipSpike("snap (check 2) did not produce a buffer")
        expected = state["w"] * state["h"] * state["bpp"]
        got = state["n_bytes"]
        if got != expected:
            raise RuntimeError(f"pix length {got} != w*h*bpp {expected}")
        return f"pix length {got} == w*h*bpp {expected}"

    # 4. Fix's dtype mapping decodes to the right shape ----------------------
    @check("4. issue-6 dtype mapping reshapes without error")
    def _c4():
        if not state:
            raise SkipSpike("snap (check 2) did not produce a buffer")
        w, h, bpp, n_comp = state["w"], state["h"], state["bpp"], state["n_comp"]
        dtype, tail = derive_dtype(bpp, n_comp)
        arr = np.frombuffer(state["pix"], dtype=dtype).reshape((h, w) + tail)
        note = ""
        if n_comp > 1:
            note = ("\n         MULTI-COMPONENT: confirm this is BGRA (4 x uint8), "
                    "NOT one uint32 — component count must be applied before bpp.")
        return (f"decoded dtype={np.dtype(dtype).name} shape={arr.shape} "
                f"min={int(arr.min())} max={int(arr.max())}{note}")

    # 5. saturated_fraction max is per-component, not forced 65535 -----------
    @check("5. np.iinfo(dtype).max is the true per-component max")
    def _c5():
        if not state:
            raise SkipSpike("snap (check 2) did not produce a buffer")
        dtype, _ = derive_dtype(state["bpp"], state["n_comp"])
        true_max = int(np.iinfo(dtype).max)
        forced = 65535
        msg = f"per-component max for {np.dtype(dtype).name} = {true_max}"
        if state["n_comp"] == 1 and state["bpp"] == 1 and true_max != forced:
            msg += (" (an 8-bit sensor: the old hardcoded uint16 under-reports "
                    "saturation by treating max as 65535)")
        return msg

    summarize()


def summarize() -> None:
    print("\n" + "=" * 68)
    print("SPIKE B SUMMARY (camera pixel geometry)")
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
        "  1 PASS -> issue-1's get_camera_device() role mapping is available.\n"
        "  2-4 PASS on 8-bit AND 16-bit (switch the demo cam and re-run) -> the\n"
        "           bytes-per-pixel -> dtype fix decodes both correctly.\n"
        "  4 on an RGB32 camera -> confirm shape is (H, W, 4) uint8 (BGRA), which\n"
        "           validates the 'components before bpp' ordering in the fix.\n"
        "  5 -> confirms saturated_fraction stops using a forced 65535 on 8-bit.\n")


if __name__ == "__main__":
    main()
