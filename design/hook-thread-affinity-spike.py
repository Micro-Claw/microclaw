#!/usr/bin/env python
"""Spike C: can a hook call ctrl.core from a pycro-manager acquisition thread?

Informs design/11b issue 7 (fail-open hook error handling). Hooks such as
FocusFeedbackHook and IntensityAdaptiveHook call ctrl.core (get_position,
get_exposure, set_exposure, set_position) from inside pycro-manager's
image_process_fn, which runs on an acquisition worker thread — NOT the thread
that built the bridge. pycro-manager bridge objects have historically been
thread-affine, and our unit tests mock ctrl, so they can't catch a cross-thread
bridge failure. If the bridge is thread-affine here, hardening hook error
handling on the assumption that these calls merely "sometimes raise a
SafetyViolation" would be built on a false premise — every core call from the
hook thread would raise regardless.

Run this MANUALLY on the Windows lab machine with Micro-Manager OPEN and the
pycro-manager ZMQ server enabled:

    python hook-thread-affinity-spike.py
    python hook-thread-affinity-spike.py --port 4827

>>> This runs a 1-event acquisition, which FIRES THE CAMERA once. It moves no
    stage or shutter — the hook only READS core state (get_position /
    get_exposure) plus the current thread id. <<<

Report the printed PASS/FAIL/INFO summary back for the design doc.
"""
from __future__ import annotations

import argparse
import tempfile
import threading
import traceback

try:
    from pycromanager import Acquisition, Core, multi_d_acquisition_events
except Exception as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "pycromanager is required (pip install pycromanager). Import failed: %r" % exc
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


def summarize() -> None:
    print("\n" + "=" * 68)
    print("SPIKE C SUMMARY (hook thread-affinity)")
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
        "  PASS -> core calls from the acquisition thread work; issue-7 hardening\n"
        "          (distinguish SafetyViolation from hardware error, always log) is\n"
        "          built on a valid premise.\n"
        "  FAIL -> the bridge is thread-affine here: EVERY ctrl.core call from a\n"
        "          hook raises. Then the real issue is that hooks can't touch core\n"
        "          on the acq thread at all — the fail-open handlers would be\n"
        "          swallowing that, and the fix is architectural (marshal the call\n"
        "          to the owning thread / use a per-thread bridge), not just better\n"
        "          logging. Resolve this BEFORE landing issue 7.\n")


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=4827, help="ZMQ server port")
    args = ap.parse_args()
    port = args.port

    print(f"Connecting to Micro-Manager ZMQ server on port {port} ...", flush=True)

    # 0. Connect (on the MAIN thread) ----------------------------------------
    try:
        core = Core(port=port)
        main_tid = threading.get_ident()
        record("PASS", "0. connect Core (main thread)",
               f"MMCore: {core.get_version_info()}; main thread id={main_tid}")
    except Exception as exc:
        record("FAIL", "0. connect Core (main thread)", f"{type(exc).__name__}: {exc}")
        print("\nCannot continue without a connection. Is MM open with the ZMQ "
              "server enabled on this port?", flush=True)
        summarize()
        return

    # Baseline: confirm the same call works on the main thread first, so a FAIL
    # in the hook is attributable to thread-affinity and not a bad call.
    try:
        z0 = core.get_position()
        record("PASS", "1. core.get_position() on main thread", f"z={z0}")
    except Exception as exc:
        record("FAIL", "1. core.get_position() on main thread",
               f"{type(exc).__name__}: {exc} (core call broken independent of threads)")

    # The hook records into this shared dict from the acquisition worker thread.
    result: dict = {}

    def image_process_fn(image, metadata, event_queue):
        # This runs on a pycro-manager acquisition thread — the thing under test.
        hook_tid = threading.get_ident()
        result["hook_tid"] = hook_tid
        result["cross_thread"] = hook_tid != main_tid
        try:
            result["z"] = core.get_position()          # cross-thread bridge READ
            result["exposure"] = core.get_exposure()   # a second read for good measure
            result["ok"] = True
        except Exception as exc:
            result["ok"] = False
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["trace"] = traceback.format_exc()
        return image, metadata

    # 2. Run a 1-event acquisition with the probing hook ---------------------
    print("\n--- 2. run 1-event acquisition; hook calls core from its thread ---",
          flush=True)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            with Acquisition(directory=tmp, name="microclaw_threadspike",
                             show_display=False,
                             image_process_fn=image_process_fn) as acq:
                acq.acquire(multi_d_acquisition_events(num_time_points=1))
        # Acquisition context exit waits for the event to be processed.
    except Exception as exc:
        record("FAIL", "2. acquisition ran", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        summarize()
        return

    if not result:
        record("FAIL", "2. hook fired",
               "image_process_fn never ran — no image was processed")
        summarize()
        return

    record("INFO", "2. hook fired",
           f"hook thread id={result.get('hook_tid')}, "
           f"cross_thread={result.get('cross_thread')}")

    # 3. Verdict on the cross-thread core call -------------------------------
    if result.get("ok"):
        record("PASS", "3. core call from acquisition thread",
               f"get_position()={result.get('z')}, "
               f"get_exposure()={result.get('exposure')} — bridge is NOT thread-affine")
    else:
        record("FAIL", "3. core call from acquisition thread",
               f"{result.get('error')} — bridge appears thread-affine")
        if result.get("trace"):
            print(result["trace"], flush=True)

    if result.get("cross_thread") is False:
        record("INFO", "3. note",
               "hook ran on the MAIN thread — this backend may process images "
               "inline, so thread-affinity was not actually exercised. Verify the "
               "hook thread id differs from the main thread id above.")

    summarize()


if __name__ == "__main__":
    main()
