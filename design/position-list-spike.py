#!/usr/bin/env python
"""Spike A: can microclaw write to MM's PositionList over ZMQ, round-trip it, and
have the MM GUI actually repaint?

Blocks design/11b issue-5 fix (a) "write through to MM's PositionList". Our own
history (project_jpypemm_display_limitation) shows programmatic GUI updates over
the bridge don't always repaint, so the "mark a position and it shows up in the
Position List Manager" claim is exactly the kind of thing to verify before
building on it. If check 4 (repaint) fails we take fix 5 *option (b)* — correct
the docs — instead of shipping a write-back that silently doesn't show.

Run this MANUALLY on the Windows lab machine with Micro-Manager OPEN and the
pycro-manager ZMQ server enabled (Tools > Options, port 4827 by default):

    python position-list-spike.py
    python position-list-spike.py --port 4827
    python position-list-spike.py --keep     # don't remove the spike entry at the end

What it checks (each isolated; a failure never aborts the rest):

  0. Connect to Core + Studio over ZMQ.
  1. Construct org.micromanager.MultiStagePosition over the bridge.
  2. Resolve org.micromanager.StagePosition (TOP-LEVEL class, not an inner class)
     and discover the snake_case-mangled name of its create2D/create1D factories.
  3. Build a 2-axis (XY) StagePosition via create2D and add it to the MSP.
  4. Round-trip: add the MSP, set_position_list, re-read the list, and confirm the
     count incremented AND the entry reads back with the same fields the real read
     path uses (controller._read_mm_position_list: num_axes / x / y / label).
  5. GUI repaint — a MANUAL visual observation (does MICROCLAW_SPIKE appear in the
     Position List Manager?). This is the check most likely to flip the plan.
  6. PositionList.save(path) -> a real .pos file (issue-5 file-format fix).
  7. Cleanup: remove the spike entry and restore the list (unless --keep).

NOTHING here moves a stage, shutter, or camera. It DOES mutate the GUI position
list (that is the thing under test); cleanup restores it. Report the printed
PASS/FAIL/INFO summary back for the design doc.
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

SPIKE_LABEL = "MICROCLAW_SPIKE"

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
    """Decorator: run a check fn immediately, PASS on return, FAIL on exception.

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


def proxy_methods(obj) -> list[str]:
    """Best-effort list of Java method names on a pycro-manager proxy object."""
    return sorted(m for m in dir(obj) if not m.startswith("_"))


def find_factory(sp_cls, base: str, candidates: list[str]):
    """Return (name, callable) for the first resolvable factory on sp_cls.

    The snake_case mangling of Java's create2D/create1D over the bridge is the
    first unknown to pin down; probe a few likely manglings and fall back to
    scanning dir() for anything that looks like the factory.
    """
    available = proxy_methods(sp_cls)
    for name in candidates:
        fn = getattr(sp_cls, name, None)
        if fn is not None:
            return name, fn
    # Fall back: anything containing the base tag (e.g. "create2") we can find.
    guesses = [m for m in available if base.lower().replace("d", "") in m.lower()
               and "create" in m.lower()]
    if guesses:
        name = guesses[0]
        return name, getattr(sp_cls, name)
    raise RuntimeError(
        f"no {base} factory found; StagePosition statics were: {available}")


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=4827, help="ZMQ server port")
    ap.add_argument("--keep", action="store_true",
                    help="do not remove the spike entry from the list at the end")
    args = ap.parse_args()
    port = args.port

    print(f"Connecting to Micro-Manager ZMQ server on port {port} ...", flush=True)

    # 0. Connect -------------------------------------------------------------
    try:
        core = Core(port=port)
        studio = Studio(port=port)
        record("PASS", "0. connect Core+Studio", core.get_version_info())
    except Exception as exc:
        record("FAIL", "0. connect Core+Studio", f"{type(exc).__name__}: {exc}")
        print("\nCannot continue without a connection. Is MM open with the ZMQ "
              "server enabled on this port?", flush=True)
        summarize()
        return

    pm = studio.positions()
    plist = pm.get_position_list()
    before = int(plist.get_number_of_positions())
    xy_stage = core.get_xy_stage_device()
    record("INFO", "positions before", f"count={before}, xy_stage={xy_stage!r}")

    # Shared state passed between checks.
    state: dict = {"msp": None, "sp_factory": None}

    # 1. Construct MultiStagePosition ---------------------------------------
    @check("1. construct MultiStagePosition over the bridge")
    def _c1():
        msp = JavaObject("org.micromanager.MultiStagePosition", port=port)
        msp.set_label(SPIKE_LABEL)
        state["msp"] = msp
        return f"MSP built; label set to {SPIKE_LABEL!r}"

    # 2. Resolve StagePosition + find create2D/create1D ----------------------
    @check("2. resolve StagePosition + discover create2D/create1D factory names")
    def _c2():
        sp_cls = JavaClass("org.micromanager.StagePosition", port=port)
        statics = proxy_methods(sp_cls)
        # Likely manglings of create2D/create1D over the bridge, best guess first.
        name2d, _ = find_factory(
            sp_cls, "create2D",
            ["create2_d", "create2D", "create2d"])
        name1d, _ = find_factory(
            sp_cls, "create1D",
            ["create1_d", "create1D", "create1d"])
        state["sp_cls"] = sp_cls
        state["name2d"] = name2d
        state["name1d"] = name1d
        return (f"StagePosition statics={statics}\n"
                f"         create2D -> {name2d!r}   create1D -> {name1d!r}")

    # 3. Build a 2-axis StagePosition and add it -----------------------------
    @check("3. build 2-axis StagePosition via create2D and add to MSP")
    def _c3():
        if state.get("msp") is None or state.get("sp_cls") is None:
            raise SkipSpike("prerequisite check 1/2 did not pass")
        sp_cls = state["sp_cls"]
        create2d = getattr(sp_cls, state["name2d"])
        sp = create2d(xy_stage, 0.0, 0.0)
        methods = proxy_methods(sp)
        # Confirm the READ-path fields the controller relies on are present.
        for field in ("num_axes", "x", "y"):
            if field not in methods:
                raise RuntimeError(
                    f"StagePosition lacks {field!r} that _read_mm_position_list "
                    f"uses; surface was {methods}")
        state["msp"].add(sp)
        return (f"create2D({xy_stage!r}, 0, 0) OK; "
                f"num_axes={int(sp.num_axes)}, x={float(sp.x)}, y={float(sp.y)}")

    # 4. Round-trip: add + set_position_list + re-read -----------------------
    @check("4. round-trip: set_position_list then re-read the marked entry")
    def _c4():
        if state.get("msp") is None:
            raise SkipSpike("prerequisite check 1-3 did not pass")
        plist.add_position(state["msp"])
        pm.set_position_list(plist)

        # Re-read through a fresh handle exactly as the controller does.
        fresh = pm.get_position_list()
        after = int(fresh.get_number_of_positions())
        if after != before + 1:
            raise RuntimeError(f"count did not increment: before={before} after={after}")

        # Locate our entry and read it via the num_axes/x/y path.
        found = None
        for i in range(after):
            msp = fresh.get_position(i)
            if str(msp.get_label()) == SPIKE_LABEL:
                entry: dict = {"name": SPIKE_LABEL}
                for j in range(int(msp.size())):
                    sp = msp.get(j)
                    if int(sp.num_axes) == 2:
                        entry["x_um"] = round(float(sp.x), 3)
                        entry["y_um"] = round(float(sp.y), 3)
                found = entry
                break
        if found is None:
            raise RuntimeError("entry not found on re-read despite count increment")
        return f"count {before} -> {after}; read back {found}"

    # 5. GUI repaint — MANUAL observation ------------------------------------
    record(
        "CHECK", "5. GUI repaint (MANUAL)",
        "Look at MM's Position List Manager window NOW. Does an entry labelled "
        f"{SPIKE_LABEL!r} appear WITHOUT you clicking refresh?\n"
        "         PASS => fix 5(a) write-through is viable.\n"
        "         FAIL (row absent until refocus/refresh) => same repaint gap as the\n"
        "         Preview canvas; take fix 5(b) (correct the docs) instead.")

    # 6. PositionList.save -> .pos file --------------------------------------
    @check("6. PositionList.save writes a real .pos file")
    def _c6():
        out = "microclaw_spike.pos"
        pm.get_position_list().save(out)
        return f"PositionList.save({out!r}) OK -> issue-5 .pos round-trip is possible"

    # 7. Cleanup -------------------------------------------------------------
    if args.keep:
        record("SKIP", "7. cleanup", "--keep passed; leaving spike entry in the list")
    else:
        @check("7. cleanup: remove spike entry and restore list")
        def _c7():
            cur = pm.get_position_list()
            removed = 0
            # Remove from the end so indices stay valid as we delete.
            for i in reversed(range(int(cur.get_number_of_positions()))):
                if str(cur.get_position(i).get_label()) == SPIKE_LABEL:
                    cur.remove_position(i)
                    removed += 1
            pm.set_position_list(cur)
            final = int(pm.get_position_list().get_number_of_positions())
            if final != before:
                raise RuntimeError(
                    f"list not restored: removed {removed}, count now {final}, "
                    f"expected {before}")
            return f"removed {removed} spike entry(ies); count back to {final}"

    summarize()


def summarize() -> None:
    print("\n" + "=" * 68)
    print("SPIKE A SUMMARY (position-list write-back)")
    print("=" * 68)
    for status, name, _ in _RESULTS:
        print(f"  {status:5} {name}")
    counts: dict[str, int] = {}
    for status, _, _ in _RESULTS:
        counts[status] = counts.get(status, 0) + 1
    print("-" * 68)
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("=" * 68)
    print(
        "\nInterpretation:\n"
        "  1-4 PASS -> write-through round-trips over the bridge; the plumbing works.\n"
        "  5 PASS (manual) -> the GUI repaints -> ship fix 5(a) write-through.\n"
        "  5 FAIL (manual) -> repaint gap -> ship fix 5(b) (correct the docs), even\n"
        "                     if 1-4 passed. This is the decisive check.\n"
        "  6 PASS -> real .pos serialization is available; prefer it over the JSON\n"
        "            rename for the file-format fix.\n")


if __name__ == "__main__":
    main()
