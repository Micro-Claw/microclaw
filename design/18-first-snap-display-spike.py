#!/usr/bin/env python
"""Spike for design/18: why the FIRST snap of a session shows "Waiting for Image...".

Symptom (lab, 2026-07-09, history 20260709_183409): the first snap_and_analyze of
a freshly-launched session fired the camera and returned pixels, the MM Preview
window popped up — and never displayed the image. It read "Waiting for Image...".
The second snap displayed normally. snap_and_analyze reported
displayed_in_mm_viewer=true both times, because that field echoes its own
`display` parameter (tools.py:762) rather than observing anything.

WHAT IS ALREADY ESTABLISHED (by javap over MM 2.0.3's MMJ_.jar — not guesswork,
and not in need of re-checking here):

  F1  "Waiting for Image..." is a string literal in
      DisplayUIController.buildInitialUI(). It is the placeholder the snap/live
      window is CONSTRUCTED with, and it is swapped out only when
      DisplayUIController.displayImages(...) runs.

  F2  SnapLiveManager.displayImage(Image) starts with
      `if (!SwingUtilities.isEventDispatchThread()) { invokeLater(...); return; }`
      Called from our ZMQ thread it is FULLY ASYNCHRONOUS.

  F3  SnapLiveManager.snap(boolean) = acquisitions().snap(), then
      displayImage(img) per image, then `if (display_ != null) display_.toFront()`.
      On a cold snap display_ is still null at that last check, so snap(True)
      returns to Python BEFORE the window exists (and skips toFront()).

  F4  SnapLiveManager.getDisplay() is a pure accessor: returns display_, or null
      if it is absent or closed. It never creates a display. Safe to poll.

  F5  createOrResetDatastoreAndDisplay() rebuilds when `display_ == null ||
      display_.isClosed()`. Therefore CLOSING THE PREVIEW WINDOW restores the
      cold condition — an MM restart is not needed between runs.

The repaint that clears the placeholder is not a direct paint: the new-image
event drives DisplayController's asynchronous, COALESCING image-stats pipeline
("Submitting compute request" -> "Image stats ready" -> displayImages).

WHAT IS STILL UNKNOWN, and is the whole point of this spike:

  U1  Is display_ really still null when snap(True) returns? (F3 predicts YES.)
      And how long after does the window appear?

  U2  Does re-pushing the SAME image via live().display_image(img) clear the
      placeholder — i.e. is one extra displayImage enough, or does the
      coalescing pipeline collapse the two inserts into one dropped repaint?

  U3  Does it matter WHEN we re-push: immediately (queued behind the
      window-construction EDT task), or after get_display() goes non-null?

U2/U3 decide the fix in image_analysis.snap_to_numpy_displayed. The re-push is
attractive because it costs NO extra exposure — the sample does not bleach.
The alternative (warming the window by toggling live mode at connect) burns
frames on every session start.

Also confirmed here: whether `get_display` / `display_image` are the right
snake_cased names over the bridge (CLAUDE.md: methods are snake_cased, fields
are not).

------------------------------------------------------------------------------
HOW TO RUN

The cold condition is CONSUMED by the first snap, so this spike takes ONE cold
measurement per run. Run it three times, once per --repush mode, CLOSING THE MM
PREVIEW WINDOW BEFORE EACH RUN (F5 — no restart needed):

    # 1. control: reproduce the bug, no fix attempted
    python 18-first-snap-display-spike.py --repush none

    # 2. candidate fix A: re-push immediately after snap(True) returns
    python 18-first-snap-display-spike.py --repush immediate

    # 3. candidate fix B: wait for get_display(), then re-push   <- expected fix
    python 18-first-snap-display-spike.py --repush after-display

Each writes 18-first-snap-display-spike-output-<mode>.txt. Send back all three.

>>> Fires the camera TWICE (the cold snap, then a control snap). Moves no      <<<
>>> stage, changes no channel, touches no laser. Safe on the DEMO config;      <<<
>>> on real hardware it is two exposures at the current settings.              <<<

This spike ASKS YOU WHAT IS ON SCREEN at two moments. The whole result is that
answer — the bridge cannot see the placeholder, only you can. Pass --no-prompt
to skip the questions (then the eyeball lines read UNANSWERED and the run is
worth much less).

Live mode must be OFF: live().snap(True) under live mode never returns and
wedges the single-lock bridge for the whole process (design/14 V1). This spike
refuses to start if live mode is on rather than probing it.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import traceback
from pathlib import Path

try:
    from pycromanager import Studio
except Exception as exc:  # pragma: no cover - environment guard
    raise SystemExit("pycromanager is required. Import failed: %r" % exc)


# --------------------------------------------------------------------------- #
# Harness: Tee + watchdog + result records. Inlined from design/14-demo-spike.py
# so this file stands alone. The watchdog exists because a Java-side non-reply
# blocks EVERY later bridge call from ANY thread (one bridge per port, holding
# _communication_lock across the round trip) — a hang can be detected, never
# recovered in-process.
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


_RESULTS: list[tuple[str, str, str]] = []   # (status, name, detail)
_WATCH = {"name": None, "step": None, "deadline": 0.0}
_OUT_PATH: Path | None = None
_EYEBALL: list[tuple[str, str]] = []        # (question, answer)


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
                   "this process. Exiting cleanly instead of jamming the terminal.")
            print("\nWATCHDOG: the process will now exit. Recovery: stop live view "
                  "in MM if it is running, then re-run — a fresh process "
                  "reconnects fine.", flush=True)
            summarize(_OUT_PATH or Path("18-spike-output.txt"))
            sys.stdout.flush()
            os._exit(2)


class SkipSpike(Exception):
    pass


class AbortSpike(Exception):
    """A precondition failed in a way that makes every later check meaningless."""


def check(name: str, timeout_s: float = 45.0):
    def wrap(fn):
        print(f"\n--- {name} ---", flush=True)
        _WATCH.update(name=name, step="(check start)", deadline=time.time() + timeout_s)
        try:
            detail = fn()
            record("PASS", name, "" if detail is None else str(detail))
        except SkipSpike as s:
            record("SKIP", name, str(s))
        except AbortSpike:
            raise
        except Exception as exc:
            record("FAIL", name, f"{type(exc).__name__}: {exc}")
            traceback.print_exc(file=sys.stdout)
        finally:
            _WATCH.update(name=None, step=None)
        return fn
    return wrap


def probe_attrs(obj, names: list[str]):
    """Return (resolved_name, bound_attr) for the first name that resolves."""
    for n in names:
        attr = getattr(obj, n, None)
        if attr is not None:
            return n, attr
    return None, None


# --------------------------------------------------------------------------- #
# The eyeball. The bridge can tell us whether display_ is non-null; it cannot
# tell us whether the canvas shows an image or the "Waiting for Image..." label
# (F1: that swap happens inside DisplayUIController, with no API that reports
# it). So the operator is the instrument for exactly this one reading.
# --------------------------------------------------------------------------- #
_PROMPTS_ON = True


def eyeball(question: str) -> str:
    print("\n" + "!" * 68, flush=True)
    print(f">>> EYEBALL CHECK: {question}", flush=True)
    print("!" * 68, flush=True)
    if not _PROMPTS_ON:
        answer = "UNANSWERED (--no-prompt)"
        print(f"    {answer}", flush=True)
        _EYEBALL.append((question, answer))
        return answer
    # Disarm the watchdog: a human thinking is not a wedged bridge.
    saved = dict(_WATCH)
    _WATCH.update(name=None, step=None)
    try:
        answer = input(">>> answer (free text; 'image' / 'waiting' / anything): ").strip()
    except EOFError:
        answer = "UNANSWERED (no tty)"
    finally:
        _WATCH.update(saved)
        _WATCH["deadline"] = time.time() + 45.0   # fresh budget after the pause
    answer = answer or "UNANSWERED (empty)"
    _EYEBALL.append((question, answer))
    return answer


# --------------------------------------------------------------------------- #
# Display-state probes. get_display() is a pure accessor (F4) — polling it is
# a cheap field read and can never create or mutate a window.
# --------------------------------------------------------------------------- #
def get_display(live):
    name, fn = probe_attrs(live, ["get_display", "getDisplay"])
    if fn is None:
        raise RuntimeError("SnapLiveManager exposes neither get_display nor getDisplay")
    return fn()


def display_present(live) -> bool:
    """Java null -> Python None over pyjavaz. Anything else means a live window.

    If pyjavaz ever hands back a proxy for null instead of None, this reads
    "present" forever and check 1 dead-ends. Check 1 therefore logs the raw
    repr, so that failure is diagnosable rather than a wall.
    """
    return get_display(live) is not None


def wait_for_display(live, timeout_s: float, poll_s: float = 0.025):
    """Poll get_display() until non-null. Returns (appeared: bool, seconds)."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        if display_present(live):
            return True, time.perf_counter() - t0
        time.sleep(poll_s)
    return False, time.perf_counter() - t0


def first_image(images):
    """snap() returns a java.util.List proxy (design/14 check 4) or a python list."""
    if isinstance(images, (list, tuple)):
        return images[0], len(images)
    size_name, size_fn = probe_attrs(images, ["size", "get_size"])
    get_name, get_fn = probe_attrs(images, ["get"])
    if get_fn is None:
        raise RuntimeError(f"no element access on {type(images).__name__}")
    n = int(size_fn()) if size_fn else -1
    return get_fn(0), n


# --------------------------------------------------------------------------- #
def main() -> None:
    global _OUT_PATH, _PROMPTS_ON
    ap = argparse.ArgumentParser(description="design/18 first-snap display spike")
    ap.add_argument("--port", type=int, default=4827, help="ZMQ server port")
    ap.add_argument("--repush", choices=("none", "immediate", "after-display"),
                    default="after-display",
                    help="U2/U3: when to re-push the already-acquired image. "
                         "'none' reproduces the bug as a control.")
    ap.add_argument("--repush-count", type=int, default=1,
                    help="how many times to re-push (U2: does the coalescing "
                         "pipeline swallow a single extra insert?)")
    ap.add_argument("--display-timeout", type=float, default=5.0,
                    help="seconds to wait for get_display() to go non-null")
    ap.add_argument("--no-control-snap", action="store_true",
                    help="skip the second snap (saves one exposure; you lose the "
                         "control that proves a warm snap still paints)")
    ap.add_argument("--force-warm", action="store_true",
                    help="run even if a Preview window already exists. The cold "
                         "measurement is then MEANINGLESS — warm-path control only.")
    ap.add_argument("--no-prompt", action="store_true",
                    help="skip the eyeball questions (run is worth much less)")
    ap.add_argument("--out", default=None, help="results file")
    args = ap.parse_args()

    _PROMPTS_ON = not args.no_prompt
    out_path = Path(args.out) if args.out else Path(__file__).with_name(
        f"18-first-snap-display-spike-output-{args.repush}.txt")
    _OUT_PATH = out_path

    sys.stdout = _Tee(out_path)
    sys.stderr = sys.stdout
    threading.Thread(target=_watchdog_loop, daemon=True, name="spike-watchdog").start()

    print("=" * 68)
    print(f"design/18 first-snap display spike — repush={args.repush} "
          f"x{args.repush_count}")
    print(f"port={args.port}  control_snap={not args.no_control_snap}")
    print("=" * 68)

    studio = Studio(port=args.port)
    live = studio.live()
    state: dict = {}

    try:
        # ------------------------------------------------------------------ #
        @check("1. preconditions: live mode OFF, and the display is COLD")
        def _c1():
            on_name, on_fn = probe_attrs(live, ["is_live_mode_on", "isLiveModeOn"])
            step(f"{on_name}()")
            if bool(on_fn()):
                raise AbortSpike(
                    "LIVE MODE IS ON. live().snap(True) under live mode never "
                    "returns and wedges the bridge (design/14 V1). Turn live view "
                    "off in MM and re-run. This spike will not probe it.")
            step("get_display() — cold check (F4: pure accessor, creates nothing)")
            raw = get_display(live)
            print(f"    get_display() raw -> {type(raw).__name__}: {raw!r}", flush=True)
            present = raw is not None
            state["cold"] = not present
            if present:
                if not args.force_warm:
                    raise AbortSpike(
                        "A Preview window already exists, so the FIRST-snap "
                        "condition is gone and this run would measure nothing. "
                        "CLOSE the MM Preview window and re-run — per F5, closing "
                        "it restores the cold state; no MM restart needed. "
                        "(--force-warm runs anyway, as a warm-path control.)\n"
                        f"         If NO Preview window is open and you still see "
                        f"this, pyjavaz is not mapping Java null to None: "
                        f"get_display() returned {raw!r}. Send that repr back — "
                        f"display_present() needs a different emptiness test.")
                return "WARM (--force-warm): display_ already non-null. Cold " \
                       "measurement below is INVALID; treat as warm control."
            return "COLD: get_display() is None — display_ == null, as required."

        # ------------------------------------------------------------------ #
        @check("2. API surface: get_display / display_image resolve over the bridge")
        def _c2():
            lines = []
            for logical, names in {
                "getDisplay":   ["get_display", "getDisplay"],
                "displayImage": ["display_image", "displayImage"],
                "snap":         ["snap"],
            }.items():
                n, fn = probe_attrs(live, names)
                lines.append(f"{logical:14} -> {n!r}" if n else
                             f"{logical:14} -> UNRESOLVED, tried {names}")
                if n is None:
                    raise RuntimeError(f"{logical} unresolved; the fix cannot be written")
            state["display_image_name"] = probe_attrs(
                live, ["display_image", "displayImage"])[0]
            lines.append("(CLAUDE.md: methods snake_cased, public fields are not)")
            return "\n         ".join(lines)

        # ------------------------------------------------------------------ #
        @check("3. U1: cold snap(True) — does it return BEFORE the window exists?",
               timeout_s=60.0)
        def _c3():
            step("live().snap(True) — FIRES THE CAMERA (cold: no Preview window yet)")
            t0 = time.perf_counter()
            images = live.snap(True)
            t_snap = time.perf_counter() - t0

            # The single most diagnostic read in this spike. F3 predicts None:
            # snap() hands back pixels while the window is still a queued EDT task.
            step("get_display() IMMEDIATELY after snap(True) returned")
            present_at_return = display_present(live)

            img, n = first_image(images)
            state["img"] = img
            state["n_images"] = n

            appeared, secs = wait_for_display(live, args.display_timeout)
            state["display_appeared"] = appeared

            return "\n         ".join([
                f"snap(True) returned in {t_snap*1000:.0f} ms, {n} image(s)",
                f"display_ non-null AT RETURN: {present_at_return}   "
                f"<- F3 predicts False on a cold snap",
                f"display_ appeared within {secs*1000:.0f} ms: {appeared}",
                "U1 ANSWERED: snap(True) " + (
                    "returned before the window existed (F3 CONFIRMED)"
                    if not present_at_return else
                    "returned with the window already up (F3 CONTRADICTED — "
                    "the race is elsewhere; say so in the results)"),
            ])

        # ------------------------------------------------------------------ #
        @check("4. eyeball: what does the Preview window show RIGHT NOW?",
               timeout_s=600.0)
        def _c4():
            ans = eyeball(
                "The camera has fired once and no fix has been applied yet. "
                "Look at the MM Preview window. Does it show the IMAGE, or the "
                "text 'Waiting for Image...'? (If --repush=none, this is the "
                "whole experiment: 'waiting' here reproduces the bug.)")
            state["eyeball_before"] = ans
            return f"before any re-push: {ans!r}"

        # ------------------------------------------------------------------ #
        @check("5. U2/U3: re-push the SAME image — no new exposure", timeout_s=60.0)
        def _c5():
            if args.repush == "none":
                raise SkipSpike(
                    "--repush=none: control run. Check 4's answer is the "
                    "unmitigated first-snap behaviour.")
            img = state.get("img")
            if img is None:
                raise SkipSpike("check 3 did not yield an image to re-push")

            if args.repush == "after-display":
                if not state.get("display_appeared"):
                    raise SkipSpike(
                        f"display_ never went non-null within "
                        f"{args.display_timeout}s, so 'after-display' has no "
                        f"moment to fire. Re-run with --repush=immediate.")
                note = "display_ was non-null before re-pushing"
            else:
                note = ("re-pushed immediately; per F2 this queues an EDT task "
                        "BEHIND the window-construction task (invokeLater is FIFO)")

            name = state["display_image_name"]
            fn = getattr(live, name)
            for i in range(args.repush_count):
                step(f"live().{name}(img) — re-push {i+1}/{args.repush_count}, "
                     f"no camera exposure")
                fn(img)
                time.sleep(0.2)     # let the stats pipeline run between pushes
            return f"{args.repush_count} re-push(es) via {name}(); {note}"

        # ------------------------------------------------------------------ #
        @check("6. eyeball: did the re-push clear the placeholder?", timeout_s=600.0)
        def _c6():
            if args.repush == "none":
                raise SkipSpike("no re-push was attempted")
            time.sleep(1.0)         # coalescing pipeline is asynchronous
            ans = eyeball(
                "After the re-push (no new exposure). Does the Preview window "
                "NOW show the image? This is the answer to U2/U3 — whether the "
                "fix in snap_to_numpy_displayed works.")
            state["eyeball_after"] = ans
            return f"after re-push: {ans!r}"

        # ------------------------------------------------------------------ #
        @check("7. control: a warm snap(True) — FIRES THE CAMERA AGAIN",
               timeout_s=60.0)
        def _c7():
            if args.no_control_snap:
                raise SkipSpike("--no-control-snap")
            step("get_display() before the warm snap")
            warm = display_present(live)
            step("live().snap(True) — second exposure, window should exist now")
            images = live.snap(True)
            img, n = first_image(images)
            ans = eyeball(
                "After a SECOND snap (a real second exposure). Does the Preview "
                "window show the image? In the lab report this always worked — "
                "if it does here too, the bug is confined to the cold snap.")
            state["eyeball_control"] = ans
            return "\n         ".join([
                f"display_ non-null before warm snap: {warm}",
                f"warm snap returned {n} image(s)",
                f"eyeball: {ans!r}",
            ])

    except AbortSpike as a:
        record("ABRT", "preconditions", str(a))

    summarize(out_path)


def summarize(out_path: Path) -> None:
    print("\n" + "=" * 68)
    print("DESIGN/18 FIRST-SNAP DISPLAY SPIKE SUMMARY")
    print("=" * 68)
    for status, name, _ in _RESULTS:
        print(f"  {status:4}  {name}")
    counts: dict[str, int] = {}
    for status, _, _ in _RESULTS:
        counts[status] = counts.get(status, 0) + 1
    print("-" * 68)
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if _EYEBALL:
        print("\nEYEBALL ANSWERS (the actual result of this spike):")
        for q, a in _EYEBALL:
            print(f"  Q: {q[:64]}...")
            print(f"  A: {a}")
    print("=" * 68)
    print(
        "\nHow to read this (design/18):\n"
        "  check 3  -> U1: was display_ null when snap(True) returned? F3 says yes.\n"
        "  check 4  -> the bug itself: 'waiting' = reproduced, 'image' = did NOT\n"
        "              reproduce (then the cold state was not really cold, or the\n"
        "              race lost differently this run — note the timing in 3).\n"
        "  check 5/6-> U2/U3: does re-pushing the held image clear the placeholder,\n"
        "              and does the timing of the re-push matter?\n"
        "  check 7  -> control: a warm snap paints, as it always did in the lab.\n"
        "\nThe three runs together decide the fix:\n"
        "  none          check 4 = 'waiting'                 -> bug reproduced\n"
        "  immediate     check 6 = 'image'                   -> EDT FIFO is enough\n"
        "  after-display check 6 = 'image'                   -> gate on get_display()\n"
        "If 'immediate' fails but 'after-display' works, snap_to_numpy_displayed\n"
        "must poll get_display() before re-pushing. If BOTH fail, one extra\n"
        "displayImage is swallowed by the coalescing stats pipeline: re-run with\n"
        "--repush-count 2 before considering the (bleaching) live-mode warm-up.\n"
        "\nBEFORE EACH RUN: close the MM Preview window (F5 — closing restores the\n"
        "cold state; getDisplay() returns null once isClosed()). No MM restart.\n"
        f"\nFull output written to: {out_path}\n"
        "Send that file back as-is, one per --repush mode.\n")


if __name__ == "__main__":
    main()
