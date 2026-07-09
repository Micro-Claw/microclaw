#!/usr/bin/env python
"""Spike: can we halt the stage from a second thread while a move is in flight?

Informs design/16 §6 (v4b hardware halt). `microclaw serve` wants an always-live
"Halt" button that calls core.stop() on the stage devices and then shutters
illumination, from the web server's threadpool, WHILE a tool call is blocked in
core.wait_for_device() on another thread. Two premises have to hold and neither
is established:

  1. core.stop(label) actually stops the device. CMMCore::stop() is documented as
     stopping the XY or focus stage motors, with the caveat that not all stages
     support it. An adapter that returns cleanly having done nothing gives us a
     button the operator reaches for INSTEAD of the hardware E-stop. That is
     worse than no button.

  2. Two threads may have OVERLAPPING request/reply round-trips on the pyjavaz
     ZMQ bridge. design/11b Spike C showed the bridge is not thread-affine (a
     hook on an acquisition worker thread can call core.get_position()), but that
     is a weaker claim: it never had two calls in flight at once. A ZMQ REQ
     socket has strict send/recv alternation, so if pyjavaz shares one socket and
     does not lock, concurrent callers can read each other's replies. The failure
     mode is a silently mismatched reply on a hardware call.

The first run of this spike (design-16-stop-test.txt) answered NEITHER question
and reported two FAILs that were both artifacts of the spike itself:

  * It guessed a fixed 0.2 s delay before calling stop(). The stage completed a
    200 um move in less than that, so stop() hit an idle stage -- and since
    nothing overlapped, Test 3 never tested concurrency either. Both results were
    void, and only one of them said so.
  * Its shape check was `isinstance(v, float)`, which rejects the integer 456
    that the bridge returns for an exact 456.0 position. That reported a reply
    desync where the replies were in fact perfectly correct.

So this version never guesses. It MEASURES the move first, refuses to proceed if
the move is too short to halt, and fires stop() on device_busy() rather than on a
timer -- then reports INCONC, not FAIL, when it failed to test the thing.

Run this MANUALLY on the Windows lab machine with Micro-Manager OPEN and the
pycro-manager ZMQ server enabled:

    python 16-halt-concurrency-spike.py --i-understand-this-moves-the-stage
    python 16-halt-concurrency-spike.py --i-understand-this-moves-the-stage \\
        --port 4827 --distance-um 5000 --safety-config safety_config.yaml

>>> THIS MOVES THE XY STAGE by --distance-um (default 2000 um), twice: once to
    time the move, once to halt it mid-travel. Check the travel is clear first.
    It restores the starting position on every exit path. It fires no camera and
    enables no illumination.

>>> IT DOES NOT MOVE Z. Z is the axis that drives the objective into the
    coverslip — the exact accident the Halt button exists to prevent — so we
    probe core.stop() on the focus device only while it is IDLE, and never test
    halting a Z move. If that leaves the Z half of the feature unproven, good:
    that is the honest state of our knowledge, and the alternative is risking an
    objective to find out.

    With --safety-config, also fires guard.shutter_all() from the second thread
    mid-move. That WRITES the configured shutter properties (to their off
    values). It only ever turns illumination OFF.

Report the printed PASS/FAIL/INFO summary back for the design doc.
"""
from __future__ import annotations

import argparse
import math
import numbers
import sys
import threading
import time
import traceback

# A halt has to cross browser -> uvicorn -> threadpool -> ZMQ -> Java -> serial.
# Below this, no software button can catch the move and the spike cannot either.
MIN_HALTABLE_S = 0.15

try:
    from pycromanager import Core
except Exception as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "pycromanager is required (pip install pycromanager). Import failed: %r" % exc
    )

# --------------------------------------------------------------------------- #
# Tiny result harness (mirrors design/hook-thread-affinity-spike.py)
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
    print("HALT SPIKE SUMMARY (design/16 §6)")
    print("=" * 68)
    for status, name, _ in _RESULTS:
        print(f"  {status:6}  {name}")
    counts: dict[str, int] = {}
    for status, _, _ in _RESULTS:
        counts[status] = counts.get(status, 0) + 1
    print("-" * 68)
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("=" * 68)
    print(
        "\nInterpretation:\n"
        "  Test 2 (stop halts the move) and Test 3 (no reply desync) must BOTH\n"
        "  pass for /api/halt to be worth building. Both are only MEANINGFUL if\n"
        "  Test 2a confirms the stop() call actually overlapped a live move --\n"
        "  a move that finishes first tests nothing, in either test.\n"
        "\n"
        "  INCONC -> the run answered nothing. Read the detail line; it says what\n"
        "            to change. Do not read an INCONC as evidence either way.\n"
        "  2 FAIL -> this stage's adapter does not implement Stop, or ignores it.\n"
        "            A Halt button here would return 'stopped' and do nothing.\n"
        "            Do NOT ship it for this rig; grey it out, say why.\n"
        "  3 FAIL -> the bridge lets concurrent calls interleave replies. Halting\n"
        "            mid-move can corrupt the in-flight tool call's result. Do NOT\n"
        "            ship /api/halt at all; the fallback is the cooperative Stop\n"
        "            (design/16 §5), which is not a kill switch and must not be\n"
        "            labelled as one.\n"
        "  BOTH   -> build /api/halt: stop() the stages, then shutter_all().\n"
        "            Label it 'Halt', never 'Emergency Stop'. Software cannot beat\n"
        "            physics; the E-stop and the guard's bounds checks remain the\n"
        "            real protection.\n"
        "\n"
        "  Note Test 2a's measured move duration. If a full-travel move completes\n"
        "  in less time than a browser->uvicorn->ZMQ->Java round trip (~tens of\n"
        "  ms), then a software Halt cannot catch this stage no matter what the\n"
        "  other tests say. That is a finding, not a test failure.\n")


def _is_number(v) -> bool:
    """A bridge reply of the right SHAPE for a position.

    NOT `isinstance(v, float)`: the bridge JSON-encodes an exact 456.0 as the
    integer 456, so a float check reports a desync on a perfectly good reply.
    That bug voided the first run of this spike -- see design/16 §6.
    """
    return isinstance(v, numbers.Real) and not isinstance(v, bool)


def _probe_stop_idle(core, label: str, kind: str) -> None:
    """Does core.stop(label) raise on an idle device? Cheap and safe: an idle
    stage that accepts Stop is a no-op. A raise here means the adapter has no
    Stop at all, and no amount of mid-move testing will change that."""
    try:
        core.stop(label)
        record("PASS", f"1. core.stop({label!r}) accepted while idle [{kind}]",
               "does not raise; whether it HALTS a live move is Test 2")
    except Exception as exc:
        record("FAIL", f"1. core.stop({label!r}) rejected while idle [{kind}]",
               f"{type(exc).__name__}: {exc}\n"
               f"         This adapter has no Stop. Halt cannot work for this device.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=4827, help="ZMQ server port")
    ap.add_argument("--distance-um", type=float, default=2000.0,
                    help="XY travel for the halt test (default 2000). Must be far "
                         "enough that the move lasts >150 ms, or the halt cannot "
                         "overlap it and the run is inconclusive.")
    ap.add_argument("--safety-config", default=None,
                    help="If given, also fire guard.shutter_all() mid-move. "
                         "This WRITES shutter properties (off values only).")
    ap.add_argument("--i-understand-this-moves-the-stage", action="store_true",
                    help="Required. This spike physically moves the XY stage.")
    args = ap.parse_args()

    if not args.i_understand_this_moves_the_stage:
        sys.exit("Refusing to run: this spike moves the XY stage. Re-run with "
                 "--i-understand-this-moves-the-stage once the objective is "
                 "clear and nobody's hands are near the stage.")

    print(f"Connecting to Micro-Manager ZMQ server on port {args.port} ...", flush=True)

    # 0. Connect on the MAIN thread ------------------------------------------
    try:
        core = Core(port=args.port)
        version = core.get_version_info()
        record("PASS", "0. connect Core (main thread)", f"MMCore: {version}")
    except Exception as exc:
        record("FAIL", "0. connect Core (main thread)", f"{type(exc).__name__}: {exc}")
        print("\nCannot continue without a connection. Is MM open with the ZMQ "
              "server enabled on this port?", flush=True)
        summarize()
        return

    xy_label = core.get_xy_stage_device()
    z_label = core.get_focus_device()
    # Read the axes explicitly rather than via get_xy_position(), whose return
    # shape varies across builds/bridges — and shape is what Test 3 checks.
    x0, y0 = core.get_x_position(), core.get_y_position()
    record("INFO", "0b. devices and start position",
           f"xy={xy_label!r} z={z_label!r} start=({x0:.2f}, {y0:.2f}) um")

    # 1. Probe stop() on idle devices ----------------------------------------
    # Z is probed idle ONLY. We never move Z here — see the module docstring.
    _probe_stop_idle(core, xy_label, "XY, will also be tested mid-move")
    _probe_stop_idle(core, z_label, "focus/Z, idle probe only, never moved")

    if args.safety_config:
        try:
            sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
            from microclaw.config import load_safety_config
            from microclaw.safety import SafetyGuard
            guard = SafetyGuard(load_safety_config(args.safety_config))
            # guard._c is private; design/16 §6 proposes real accessors for
            # named_stages and workspace_dir. Reaching in is fine for a spike.
            for s in guard._c.named_stages:
                _probe_stop_idle(core, s.device, "named_stage, idle probe only")
        except Exception as exc:
            guard = None
            record("INFO", "1b. named_stages probe skipped",
                   f"{type(exc).__name__}: {exc}")
    else:
        guard = None
        record("INFO", "1b. named_stages / shutter test skipped",
               "pass --safety-config to exercise them")

    # 2a. TIMING PASS — how long does this move even take? -------------------
    # The first run of this spike guessed a 0.2 s delay, the stage finished in
    # less than that, and both tests silently measured nothing. Never guess:
    # measure the move, then decide whether it is long enough to halt at all.
    target_x = x0 + args.distance_um
    try:
        t = time.perf_counter()
        core.set_xy_position(target_x, y0)
        core.wait_for_device(xy_label)
        move_s = time.perf_counter() - t
        core.set_xy_position(x0, y0)
        core.wait_for_device(xy_label)
    except Exception as exc:
        record("FAIL", "2a. timing pass raised",
               f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        summarize()
        return

    record("INFO", "2a. measured move duration",
           f"{args.distance_um:.0f} um took {move_s * 1e3:.1f} ms "
           f"({args.distance_um / move_s / 1e3:.2f} mm/s)")

    if move_s < MIN_HALTABLE_S:
        record("INCONC", "2a. move is too fast to halt from software",
               f"{move_s * 1e3:.1f} ms < {MIN_HALTABLE_S * 1e3:.0f} ms. A halt has "
               f"to cross browser -> uvicorn -> threadpool -> ZMQ -> Java -> "
               f"serial;\n         it cannot catch this move, and neither can this "
               f"spike. Re-run with a larger --distance-um to get a move long "
               f"enough to test.\n         If no safe travel is long enough, that "
               f"IS the answer for this rig: a software Halt is theatre here.")
        summarize()
        return

    # 2b & 3. Halt a move that is verifiably still running --------------------
    # The halt thread does not sleep a guessed interval. It waits for the move to
    # start, polls device_busy() until the stage is actually moving, and only
    # then calls stop(). That poll is itself concurrent bridge traffic against
    # the main thread's blocking wait_for_device() -- which is exactly the
    # overlap Test 3 exists to detect.
    box: dict = {}
    move_started = threading.Event()

    def halter() -> None:
        box["halt_tid"] = threading.get_ident()
        move_started.wait(timeout=5.0)
        try:
            # A read first, with a reply recognizable by shape. A desynced bridge
            # hands back the main thread's reply (a number) instead of a string.
            box["halt_version"] = core.get_version_info()

            deadline = time.perf_counter() + move_s * 2
            while time.perf_counter() < deadline:
                if core.device_busy(xy_label):
                    box["busy_at_stop"] = True
                    break
                time.sleep(0.002)
            else:
                box["busy_at_stop"] = False     # never caught it moving

            t = time.perf_counter()
            core.stop(xy_label)
            box["stop_wall_s"] = time.perf_counter() - t
            if guard is not None:
                box["shuttered"] = guard.shutter_all(core)
        except Exception as exc:
            box["halt_exc"] = f"{type(exc).__name__}: {exc}"
            box["halt_tb"] = traceback.format_exc()

    thread = threading.Thread(target=halter, name="halt-button", daemon=True)
    try:
        record("INFO", "2b. starting move to halt",
               f"({x0:.2f}, {y0:.2f}) -> ({target_x:.2f}, {y0:.2f}) um; "
               f"stop() fires as soon as device_busy() is True")
        thread.start()
        t_start = time.perf_counter()
        move_started.set()
        core.set_xy_position(target_x, y0)
        core.wait_for_device(xy_label)          # the blocking call being interrupted
        halted_move_s = time.perf_counter() - t_start
        thread.join(timeout=10.0)

        x1, y1 = core.get_x_position(), core.get_y_position()
        travelled = abs(x1 - x0) if _is_number(x1) else float("nan")
        overlapped = box.get("busy_at_stop") is True

        # ---- Test 2: did stop() halt it? Only askable if we overlapped. -----
        if box.get("halt_exc"):
            record("FAIL", "2c. stop() from the second thread raised",
                   box["halt_exc"])
        elif not overlapped:
            record("INCONC", "2c. stop() never overlapped a live move",
                   f"device_busy({xy_label!r}) was never True from the halt thread, "
                   f"so stop() hit an idle stage.\n         travelled "
                   f"{travelled:.2f}/{args.distance_um:.2f} um means nothing here. "
                   f"Increase --distance-um.")
        elif travelled < 0.9 * args.distance_um:
            record("PASS", "2c. stop() HALTED a verifiably live move",
                   f"travelled {travelled:.2f} um of {args.distance_um:.2f} um; "
                   f"wait_for_device returned after {halted_move_s * 1e3:.1f} ms "
                   f"(unhalted: {move_s * 1e3:.1f} ms); "
                   f"stop() call took {box.get('stop_wall_s', float('nan')) * 1e3:.1f} ms")
        else:
            record("FAIL", "2c. stop() did NOT halt a live move",
                   f"the stage was confirmed busy when stop() was called, and it "
                   f"still travelled the full {travelled:.2f} um.\n         This "
                   f"adapter accepts Stop and ignores it. A Halt button on this rig "
                   f"would report success and do nothing.")

        # ---- Test 3: did the concurrent calls cross replies? ----------------
        # Every assertion is about SHAPE. A crossed reply is a wrong-typed value,
        # not an exception. See _is_number() for the bug this used to have.
        problems = []
        hv = box.get("halt_version")
        if not isinstance(hv, str) or "MMCore" not in hv:
            problems.append(f"halt-thread get_version_info() -> {hv!r} (want a str)")
        if not (_is_number(x1) and _is_number(y1)):
            problems.append(f"get_x/y_position() -> {x1!r}, {y1!r} (want numbers)")
        elif math.isnan(x1) or math.isnan(y1):
            problems.append("position readback is NaN")
        if box.get("busy_at_stop") not in (True, False):
            problems.append(f"device_busy() -> {box.get('busy_at_stop')!r} (want bool)")
        try:
            after = core.get_version_info()      # bridge still sane afterwards?
            if not isinstance(after, str) or "MMCore" not in after:
                problems.append(f"post-halt get_version_info() -> {after!r}")
        except Exception as exc:
            problems.append(f"post-halt call raised {type(exc).__name__}: {exc}")

        if problems:
            record("FAIL", "3. concurrent calls desynced the ZMQ bridge",
                   "; ".join(problems) + "\n"
                   "         Replies crossed between threads. Do NOT ship /api/halt.")
        elif not overlapped:
            record("INCONC", "3. no concurrency actually occurred",
                   "the halt thread never caught the stage moving, so its calls did "
                   "not overlap\n         the main thread's. This says nothing about "
                   "the bridge. Increase --distance-um.")
        else:
            record("PASS", "3. no reply desync under verified concurrency",
                   f"halt thread polled device_busy() and called stop() while the "
                   f"main thread was blocked in wait_for_device();\n         every "
                   f"reply had the right shape and the bridge was healthy after.")

        if guard is not None:
            sh = box.get("shuttered")
            if sh is None:
                record("INFO", "4. shutter_all mid-move not reached", "")
            elif sh:
                record("PASS", "4. shutter_all() from the halt thread, mid-move",
                       f"shuttered: {', '.join(sh)}")
            else:
                record("INFO", "4. shutter_all() ran but shuttered nothing",
                       "no illumination.shutters configured, or all writes failed "
                       "(shutter_all swallows exceptions by design)")

    except Exception as exc:
        record("FAIL", "2/3. move + halt raised on the main thread",
               f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    finally:
        # Restore. Runs even if the halt left the stage somewhere unexpected.
        try:
            core.stop(xy_label)
        except Exception:
            pass
        try:
            core.set_xy_position(x0, y0)
            core.wait_for_device(xy_label)
            xr, yr = core.get_x_position(), core.get_y_position()
            record("INFO", "5. restored start position",
                   f"({xr:.2f}, {yr:.2f}) um (was ({x0:.2f}, {y0:.2f}))")
        except Exception as exc:
            record("FAIL", "5. COULD NOT RESTORE START POSITION",
                   f"{type(exc).__name__}: {exc}\n"
                   f"         Stage may be at ({target_x:.2f}, {y0:.2f}). "
                   f"Check it before running anything else.")

    summarize()


if __name__ == "__main__":
    main()
