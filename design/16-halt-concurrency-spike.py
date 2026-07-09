#!/usr/bin/env python
"""Spike: can we halt the stage from a second thread while a move is in flight?

>>> ANSWERED, FROM SOURCE: NO. pyjavaz's Bridge.send_and_receive() holds a single
    self._communication_lock across the whole request/reply exchange, and there is
    one Bridge per port. core.wait_for_device() is one Java call that blocks in
    Java for the move's duration -- holding that lock the entire time. A second
    thread calling stop() blocks at the `with` until the move finishes, then
    stops an idle stage. /api/halt is CANCELLED (design/16 §6).

    This spike is now CONFIRMATION, not discovery. Nothing is gated on it. Run it
    to (a) see the serialization in timing numbers rather than only in source, and
    (b) settle whether core.stop() halts a live move on this rig's adapter at all
    -- the one fact the cancellation leaves open, and which a future in-loop halt
    would need. Expect 3a to FAIL (serialized); that is the confirmation.

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

So run 2 never guessed: it measured the move (214 ms, comfortably haltable) and
fired stop() on device_busy(). It came back INCONC on both tests -- device_busy()
was never True from the halt thread across a 427 ms poll window. That is a real
signal, and run 2 could not read it, because two very different things produce it:

  (A) The bridge SERIALIZED the halt thread behind the main thread's in-flight
      call, so its first request didn't return until the move was already over.
      If so, /api/halt can never preempt anything -- it queues behind the very
      tool call it is trying to interrupt.
  (B) device_busy() simply never reports True on this adapter, and the halt
      thread was running concurrently the whole time, polling a dead signal.

(A) kills the feature. (B) is a broken trigger in the spike. So this version
baselines an idle round trip, checks device_busy() single-threaded before relying
on it, and -- the decisive datum -- TIMES when the halt thread's first bridge call
returns relative to the move. Latency ~= baseline means concurrent; latency ~=
move duration means serialized.

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
        "\nInterpretation: read 3a FIRST. Everything else is downstream of it.\n"
        "\n"
        "  3a FAIL (serialized) -> the halt thread's request queued behind the\n"
        "            in-flight move instead of preempting it. /api/halt cannot\n"
        "            interrupt a running tool call on this bridge AT ALL; it is the\n"
        "            cooperative Stop (design/16 §5) with a scarier label. Do not\n"
        "            build it. 3b and 3c are moot -- a serialized bridge trivially\n"
        "            cannot cross replies, and stop() never met a moving stage.\n"
        "  3a PASS (concurrent) -> the bridge overlaps calls from two threads. Now\n"
        "            3b and 3c both have to pass:\n"
        "  3b FAIL -> this stage's adapter accepts Stop and ignores it. A Halt\n"
        "            button here would report success and do nothing. Worse than\n"
        "            no button: the operator reaches for it instead of the E-stop.\n"
        "  3c FAIL -> concurrent calls interleave replies. Halting mid-move can\n"
        "            corrupt the in-flight tool call's result -- the worst class of\n"
        "            bug this project can have, introduced in the name of safety.\n"
        "  ALL PASS -> build /api/halt: stop() the stages, then shutter_all().\n"
        "            Label it 'Halt', never 'Emergency Stop'. Software cannot beat\n"
        "            physics; the E-stop and the guard's bounds checks remain the\n"
        "            real protection.\n"
        "\n"
        "  INCONC -> the run answered nothing. Read the detail line; it says what\n"
        "            to change. Do not read an INCONC as evidence either way.\n"
        "  2c FAIL -> device_busy() is useless on this adapter, so 3b fired stop()\n"
        "            on a timer instead. That is sound (the timer is derived from\n"
        "            the MEASURED move), but 3b's overlap is inferred, not proven.\n"
        "\n"
        "  Note 2a's measured move duration and 2b's baseline round trip. If a\n"
        "  full-travel move completes in less time than a browser->uvicorn->ZMQ->\n"
        "  Java round trip, a software Halt cannot catch this stage no matter what\n"
        "  any other test says. That is a finding, not a test failure.\n")


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

    # 2b. BASELINE — what does one idle bridge round trip cost? ---------------
    # Run 2's halt thread never saw device_busy() go True across a 427 ms window
    # on a 214 ms move. Two explanations, and it recorded nothing to tell them
    # apart: either (A) the bridge SERIALIZED the halt thread behind the main
    # thread's in-flight call, so its first request didn't return until the move
    # was already over, or (B) device_busy() simply never reports True on this
    # adapter. Both are decisive, and they point opposite ways. Baseline the
    # idle round-trip time so we can recognize a blocked call when we see one.
    try:
        samples = []
        for _ in range(20):
            t = time.perf_counter()
            core.get_version_info()
            samples.append(time.perf_counter() - t)
        samples.sort()
        rtt_s = samples[len(samples) // 2]
    except Exception as exc:
        record("FAIL", "2b. baseline RTT probe raised", f"{type(exc).__name__}: {exc}")
        summarize()
        return
    record("INFO", "2b. baseline bridge round trip (idle)",
           f"median get_version_info() = {rtt_s * 1e3:.2f} ms over 20 calls")

    # 2c. Is device_busy() usable AT ALL? Single-threaded, no concurrency. -----
    # Also reveals whether set_xy_position() blocks: if it returns only after the
    # move is done, then wait_for_device() is a no-op and the call a halt has to
    # interleave with is set_xy_position() itself.
    busy_usable = False
    setpos_blocks = False
    try:
        t = time.perf_counter()
        core.set_xy_position(target_x, y0)
        setpos_s = time.perf_counter() - t
        busy_seen, t_busy_end = False, None
        deadline = time.perf_counter() + move_s * 2
        while time.perf_counter() < deadline:
            if core.device_busy(xy_label):
                busy_seen = True
            elif busy_seen:
                t_busy_end = time.perf_counter()
                break
            time.sleep(0.002)
        core.wait_for_device(xy_label)
        core.set_xy_position(x0, y0)
        core.wait_for_device(xy_label)
    except Exception as exc:
        record("FAIL", "2c. device_busy probe raised", f"{type(exc).__name__}: {exc}")
        summarize()
        return

    setpos_blocks = setpos_s > 0.5 * move_s
    busy_usable = busy_seen
    if busy_seen:
        record("PASS", "2c. device_busy() reports a live move (single-threaded)",
               f"set_xy_position() returned in {setpos_s * 1e3:.1f} ms; "
               f"device_busy() was True from the SAME thread"
               + (f" for ~{(t_busy_end - t) * 1e3:.0f} ms" if t_busy_end else ""))
    else:
        record("FAIL", "2c. device_busy() NEVER reports a live move",
               f"set_xy_position() returned in {setpos_s * 1e3:.1f} ms and "
               f"device_busy({xy_label!r}) stayed False throughout, on ONE thread "
               f"with no concurrency.\n         This adapter cannot tell us when "
               f"the stage is moving. The halt trigger falls back to a timer at "
               f"30% of the measured move.")
    if setpos_blocks:
        record("INFO", "2c. set_xy_position() BLOCKS for the move duration",
               f"{setpos_s * 1e3:.1f} ms of a {move_s * 1e3:.1f} ms move. The call a "
               f"halt must interleave with is set_xy_position(), not wait_for_device().")

    # 3. Halt a live move, and TIME the halt thread's first call ---------------
    # The halt thread stamps when its first bridge call RETURNS, relative to the
    # move's start. That single number separates (A) from (B):
    #   first-call latency ~= baseline RTT  -> the bridge ran us concurrently
    #   first-call latency ~= move duration -> the bridge SERIALIZED us behind it
    # If the bridge serializes, /api/halt cannot preempt anything: the halt
    # request simply queues behind the tool call it is trying to interrupt, which
    # makes it precisely the cooperative Stop of design/16 §5, wearing a hat.
    box: dict = {}
    move_started = threading.Event()
    trigger = "device_busy()" if busy_usable else f"timer at {0.3 * move_s * 1e3:.0f} ms"

    def halter() -> None:
        box["halt_tid"] = threading.get_ident()
        move_started.wait(timeout=5.0)
        t0 = box["t0"]
        try:
            # First bridge call from this thread. Its RETURN time is the datum.
            # A desynced bridge would also hand back a number instead of a string.
            box["halt_version"] = core.get_version_info()
            box["first_call_return_s"] = time.perf_counter() - t0

            if busy_usable:
                deadline = t0 + move_s * 2
                while time.perf_counter() < deadline:
                    if core.device_busy(xy_label):
                        box["busy_at_stop"] = True
                        break
                    time.sleep(0.002)
                else:
                    box["busy_at_stop"] = False
            else:
                # device_busy is useless here; fire on the MEASURED move, not a
                # guess. Anything left of the travel means we overlapped.
                while time.perf_counter() - t0 < 0.3 * move_s:
                    time.sleep(0.002)
                box["busy_at_stop"] = None      # unknown by construction

            box["t_stop_call"] = time.perf_counter() - t0
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
        record("INFO", "3a. starting move to halt",
               f"({x0:.2f}, {y0:.2f}) -> ({target_x:.2f}, {y0:.2f}) um; "
               f"stop() fires on {trigger}")
        thread.start()
        t_start = time.perf_counter()
        box["t0"] = t_start
        move_started.set()
        core.set_xy_position(target_x, y0)
        core.wait_for_device(xy_label)          # the blocking call being interrupted
        halted_move_s = time.perf_counter() - t_start
        thread.join(timeout=10.0)

        x1, y1 = core.get_x_position(), core.get_y_position()
        travelled = abs(x1 - x0) if _is_number(x1) else float("nan")

        # ---- Test 3a: did the bridge run us concurrently, or serialize us? --
        # THE decisive test. Everything else is downstream of it.
        first_s = box.get("first_call_return_s")
        serialized = None
        if box.get("halt_exc"):
            record("FAIL", "3a. halt thread raised", box["halt_exc"])
        elif first_s is None:
            record("INCONC", "3a. halt thread never completed its first call", "")
        elif first_s > 0.5 * move_s:
            serialized = True
            record("FAIL", "3a. the bridge SERIALIZED the halt thread",
                   f"its first call returned {first_s * 1e3:.1f} ms after the move "
                   f"started -- a {move_s * 1e3:.1f} ms move, against a "
                   f"{rtt_s * 1e3:.2f} ms idle round trip.\n         The halt request "
                   f"queued behind the in-flight move instead of preempting it. "
                   f"/api/halt CANNOT interrupt a\n         running tool call on this "
                   f"bridge; it degrades to the cooperative Stop (design/16 §5). "
                   f"Do not build it.")
        else:
            serialized = False
            record("PASS", "3a. the bridge ran both threads concurrently",
                   f"halt thread's first call returned {first_s * 1e3:.1f} ms in "
                   f"(baseline {rtt_s * 1e3:.2f} ms) while the main thread was still "
                   f"{move_s * 1e3:.0f} ms deep in its move.")

        # ---- Test 3b: did stop() halt it? Meaningless if we never overlapped.
        overlapped = (serialized is False) and box.get("busy_at_stop") is not False
        if box.get("halt_exc"):
            pass                                 # already reported
        elif serialized:
            record("INCONC", "3b. stop() could not overlap a live move",
                   f"the halt thread was queued behind the move (3a), so stop() "
                   f"necessarily hit an idle stage.\n         travelled "
                   f"{travelled:.2f}/{args.distance_um:.2f} um says nothing about "
                   f"whether this adapter honours Stop. Answer 3a first.")
        elif not overlapped:
            record("INCONC", "3b. stop() never overlapped a live move",
                   f"device_busy({xy_label!r}) was never True from the halt thread. "
                   f"travelled {travelled:.2f}/{args.distance_um:.2f} um "
                   f"means nothing here. Increase --distance-um.")
        elif travelled < 0.9 * args.distance_um:
            record("PASS", "3b. stop() HALTED a verifiably live move",
                   f"travelled {travelled:.2f} um of {args.distance_um:.2f} um; "
                   f"wait_for_device returned after {halted_move_s * 1e3:.1f} ms "
                   f"(unhalted: {move_s * 1e3:.1f} ms); stop() issued at t+"
                   f"{box.get('t_stop_call', float('nan')) * 1e3:.0f} ms and took "
                   f"{box.get('stop_wall_s', float('nan')) * 1e3:.1f} ms")
        else:
            record("FAIL", "3b. stop() did NOT halt a live move",
                   f"stop() was issued at t+{box.get('t_stop_call', 0) * 1e3:.0f} ms "
                   f"of a {move_s * 1e3:.0f} ms move and the stage still travelled "
                   f"the full {travelled:.2f} um.\n         This adapter accepts Stop "
                   f"and ignores it. A Halt button on this rig would report success "
                   f"and do nothing.")

        # ---- Test 3c: did the concurrent calls cross replies? ---------------
        # Every assertion is about SHAPE. A crossed reply is a wrong-typed value,
        # not an exception. See _is_number() for the bug this used to have.
        # Only meaningful if 3a says the calls actually overlapped: a serialized
        # bridge trivially cannot cross replies, so a PASS here would be vacuous.
        problems = []
        hv = box.get("halt_version")
        if not isinstance(hv, str) or "MMCore" not in hv:
            problems.append(f"halt-thread get_version_info() -> {hv!r} (want a str)")
        if not (_is_number(x1) and _is_number(y1)):
            problems.append(f"get_x/y_position() -> {x1!r}, {y1!r} (want numbers)")
        elif math.isnan(x1) or math.isnan(y1):
            problems.append("position readback is NaN")
        try:
            after = core.get_version_info()      # bridge still sane afterwards?
            if not isinstance(after, str) or "MMCore" not in after:
                problems.append(f"post-halt get_version_info() -> {after!r}")
        except Exception as exc:
            problems.append(f"post-halt call raised {type(exc).__name__}: {exc}")

        if problems:
            record("FAIL", "3c. concurrent calls desynced the ZMQ bridge",
                   "; ".join(problems) + "\n"
                   "         Replies crossed between threads. Do NOT ship /api/halt.")
        elif serialized:
            record("INCONC", "3c. desync untestable: the bridge serialized us",
                   "a serialized bridge cannot cross replies, so this proves nothing "
                   "about\n         concurrent safety. It also makes the question "
                   "moot -- see 3a.")
        elif serialized is False:
            record("PASS", "3c. no reply desync under verified concurrency",
                   f"the halt thread's calls overlapped the main thread's blocking "
                   f"move;\n         every reply had the right shape and the bridge "
                   f"was healthy after.")
        else:
            record("INCONC", "3c. desync untestable: no concurrency established", "")

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
