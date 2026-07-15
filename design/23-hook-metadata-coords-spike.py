"""design/23 spike — does hook metadata actually carry stage coordinates?

design/19 F3 concluded there is no `XPosition_um_Intended` in hook metadata, and
`hook_docs.py` now tells every generated hook so, in as many words:

    Do not guess stage-coordinate metadata names (there is no
    "XPosition_um_Intended"); a multi-position acquisition names its positions.

That claim was drawn from a SINGLE-POSITION Z-STACK, which carries no xy_positions
at all. This spike checks whether it generalises to the multi-position tile grids
that every survey in the Nestor run actually used.

Two layers:

  OFFLINE (default, no hardware) — Cases A/B/C drive pycro-manager's own event
  builder and its own metadata assembler (`acq_eng_py`, a Python port of the Java
  AcqEngJ that microclaw talks to) with a stub core. Proves the CLAIM, but one
  layer below where microclaw runs: microclaw uses the real ZMQ->Java Acquisition
  (tools.py:644), not this port.

  LIVE (--live, needs a running Micro-Manager with the demo config) — closes that
  gap. It (1) runs a REAL demo-config acquisition through the same image_process_fn
  path microclaw's hooks use and dumps the exact metadata dict a hook receives over
  the bridge, and (2) times ctrl.add_position in a loop to measure the F3 O(N^2)
  PositionList trap on the real bridge. Neither can be answered offline.

Run:

    python design/23-hook-metadata-coords-spike.py                 # offline A/B/C
    python design/23-hook-metadata-coords-spike.py --live          # + real MM
    python design/23-hook-metadata-coords-spike.py --live --port 4827
"""

import argparse
import json
import sys

from pycromanager import multi_d_acquisition_events
from pycromanager.acquisition.acq_eng_py.main.acq_eng_metadata import AcqEngMetadata
from pycromanager.acquisition.acq_eng_py.main.acquisition_event import AcquisitionEvent


class StubCore:
    """The handful of core reads add_image_metadata makes. No microscope."""

    def get_pixel_size_um(self): return 0.065
    def get_camera_device(self): return "StubCam"
    def get_focus_device(self): return "StubZ"
    def get_image_width(self): return 2304
    def get_image_height(self): return 2304
    def get_bytes_per_pixel(self): return 2
    def get_number_of_components(self): return 1
    def get_image_bit_depth(self): return 16
    def get_magnification_factor(self): return 1.0


def metadata_for(event_dict: str | dict) -> dict:
    """Assemble the image metadata pycro-manager would hand image_process_fn."""
    event = AcquisitionEvent.from_json(event_dict, acq=None)
    tags: dict = {}
    AcqEngMetadata.add_image_metadata(StubCore(), tags, event, elapsed_ms=0, exposure=50.0)
    return tags


# One place that names the three coordinate keys where() reads, so the offline and
# live checks assert against the same spelling.
COORD_KEYS = {
    "x": "XPosition_um_Intended",
    "y": "YPosition_um_Intended",
    "z": "ZPosition_um_Intended",
}


def _present_axes(tags: dict) -> set:
    return {axis for axis, key in COORD_KEYS.items() if tags.get(key) is not None}


def report(title: str, events: list, note: str, expect: set) -> bool:
    """Print the assembled metadata for events[0] and check it carries `expect`.

    `expect` is a set drawn from {"x","y","z"} — the coordinate axes this
    acquisition SHOULD stamp. Returns True iff exactly those axes are present.
    """
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")
    print(f"  {note}\n")
    print(f"  event as built : {json.dumps(events[0])}")

    tags = metadata_for(events[0])
    coord_keys = [k for k in tags if k.endswith("_um_Intended")]

    print(f"  Axes           : {tags.get('Axes')}")
    print(f"  PositionName   : {tags.get('PositionName')!r}")
    print(f"  coordinate keys: {coord_keys or '(none)'}")
    for k in coord_keys:
        print(f"      {k:24s} = {tags[k]}")

    present = _present_axes(tags)
    ok = present == expect
    want = "".join(sorted(a.upper() for a in expect)) or "(none)"
    got = "".join(sorted(a.upper() for a in present)) or "(none)"
    print(f"\n  --> coordinate axes present: {got}   (expected {want})   "
          f"{'OK' if ok else 'MISMATCH'}")
    return ok


# ---------------------------------------------------------------------------
# Case A: the multi-position tile grid. This is what run_tile_acquisition builds
# (tools.py:1920 sets xy_positions; :1924 passes position_labels).
# ---------------------------------------------------------------------------
def offline_cases() -> bool:
    a_ok = report(
        "CASE A — hooked TILE GRID (xy_positions set)  <- every Nestor survey",
        multi_d_acquisition_events(
            xy_positions=[(-1034.2, -6948.2), (-994.2, -6948.2)],
            position_labels=["c561_r0_c0", "c561_r0_c1"],
            num_time_points=1,
            time_interval_s=0,
        ),
        "This is the path run_tile_acquisition takes for a hooked survey.",
        expect={"x", "y"},
    )

    # Case B: the single-position z-stack design/19 actually observed. No
    # xy_positions, so the event carries no X/Y for the engine to stamp — but it
    # SHOULD stamp Z, which where() reads as z_um. B now asserts that too.
    b_ok = report(
        "CASE B — single-position Z-STACK (no xy_positions)  <- what design/19 saw",
        multi_d_acquisition_events(z_start=40.0, z_end=42.0, z_step=1.0),
        "No XY in the event list; the engine stamps Z only.",
        expect={"z"},
    )

    # Case C: position + Z together. where() extracts all three; A tests XY-only
    # and B tests Z-only, so nothing before proved the three coexist. A Nestor
    # survey run with autofocus/z is exactly this shape.
    c_ok = report(
        "CASE C — TILE GRID + Z-STACK (xy_positions AND a z range)  <- survey w/ focus",
        multi_d_acquisition_events(
            xy_positions=[(-1034.2, -6948.2), (-994.2, -6948.2)],
            position_labels=["c561_r0_c0", "c561_r0_c1"],
            z_start=40.0, z_end=42.0, z_step=1.0,
        ),
        "All three of X/Y/Z should be stamped on one image's metadata.",
        expect={"x", "y", "z"},
    )

    print(f"\n{'=' * 74}\nOFFLINE VERDICT\n{'=' * 74}")
    print(f"""
  Case A  tile grid  carries X/Y     : {'PASS' if a_ok else 'FAIL'}
  Case B  z-stack    carries Z only  : {'PASS' if b_ok else 'FAIL'}
  Case C  grid + z   carries X/Y/Z   : {'PASS' if c_ok else 'FAIL'}

  The gate is acq_eng_metadata.py:73 --

      if event.get_x_position() is not None and event.get_y_position() is not None:
          AcqEngMetadata.set_stage_x_intended(tags, event.get_x_position())
          AcqEngMetadata.set_stage_y_intended(tags, event.get_y_position())

  ...so each key is absent exactly when the event has no coordinate for that axis:
  the z-stack design/19 examined has no XY, but the tile grid every survey uses
  does, and a grid+z acquisition carries all three at once.

  pycro-manager asserts this itself, against real hardware, in
  pycromanager/test/test_acquisition.py:610:

      assert metadata['XPosition_um_Intended'] == xy[0]

  CONCLUSION: hook_docs.py's "there is no XPosition_um_Intended" is FALSE for
  multi-position acquisitions. The coordinates were in the metadata all along;
  the docs told the generated hook not to look for them.

  CAVEAT this offline run cannot lift: it drives acq_eng_py, the Python PORT, not
  the ZMQ->Java AcqEngJ path microclaw actually runs (tools.py:644). Live hooks
  today key off different names -- position_index, time (hooks.py:216) -- so run
  --live to confirm the real bridge delivers these exact keys to a real hook.
""")
    return a_ok and b_ok and c_ok


# ---------------------------------------------------------------------------
# LIVE — needs a running Micro-Manager (demo config). Closes the port-vs-bridge
# gap the offline run cannot: does the metadata dict a hook ACTUALLY receives over
# ZMQ carry these keys, spelled this way? And what does the F3 add_position loop
# actually cost on the real PositionList?
# ---------------------------------------------------------------------------

# Demo-config-safe stage coordinates (the demo XY/Z stages sit near the origin).
LIVE_XY = [(0.0, 0.0), (100.0, 0.0)]
LIVE_LABELS = ["c561_r0_c0", "c561_r0_c1"]


def live_metadata_for(xy_positions, position_labels, z=None) -> list[dict]:
    """Run a REAL demo-config acquisition and return the metadata dicts a hook saw.

    Uses the same Acquisition + image_process_fn path microclaw's hooks run on
    (tools.py:644), so `captured` is exactly what HookBase.where() would be handed
    over the bridge -- not the acq_eng_py port's reconstruction of it.
    """
    import shutil
    import tempfile
    from pycromanager import Acquisition

    captured: list[dict] = []

    def capture(image, metadata, event_queue):
        captured.append(dict(metadata))
        return image, metadata

    kwargs = dict(xy_positions=xy_positions, position_labels=position_labels)
    if z is not None:
        kwargs.update(z_start=z[0], z_end=z[1], z_step=z[2])
    events = multi_d_acquisition_events(**kwargs)

    # NOT TemporaryDirectory(): on Windows the NDTiff writer can still hold the
    # dataset .tif open when the block exits, so auto-cleanup raises WinError 32
    # AFTER capture -- losing the result we came for. Own the dir, clean up
    # best-effort once the Acquisition has fully closed.
    save_dir = tempfile.mkdtemp(prefix="d23_spike_")
    try:
        with Acquisition(directory=save_dir, name="d23_spike",
                         show_display=False, image_process_fn=capture) as acq:
            acq.acquire(events)
    finally:
        shutil.rmtree(save_dir, ignore_errors=True)  # leftover .tif is harmless
    return captured


def live_metadata_dump(port: int) -> bool:
    """(1) Dump the real hook-received metadata dict; check the keys where() needs."""
    print(f"\n{'=' * 74}\nLIVE 1 — real hook metadata over the ZMQ bridge (port {port})\n{'=' * 74}")
    print("  Running a real demo-config acquisition; capturing what image_process_fn sees.\n")

    grid = live_metadata_for(LIVE_XY, LIVE_LABELS)
    if not grid:
        print("  !! No images captured -- is a camera configured in the demo config?")
        return False
    md = grid[0]

    print(f"  metadata keys a hook receives (first image of a plain grid):\n")
    for k in sorted(md):
        marker = "  <--" if k.endswith("_um_Intended") or k in ("PositionName", "Axes",
                                                                 "position_index") else ""
        val = md[k]
        val = json.dumps(val) if isinstance(val, (dict, list)) else val
        print(f"      {k:28s} = {val}{marker}")

    print(f"\n  where() reads these:")
    print(f"      PositionName            = {md.get('PositionName')!r}")
    print(f"      Axes.position           = {(md.get('Axes') or {}).get('position')!r}")
    for axis in ("x", "y"):
        print(f"      {COORD_KEYS[axis]:24s} = {md.get(COORD_KEYS[axis])!r}")

    xy_ok = md.get(COORD_KEYS["x"]) is not None and md.get(COORD_KEYS["y"]) is not None
    id_ok = md.get("PositionName") is not None or (md.get("Axes") or {}).get("position") is not None

    # Combined grid + z, to confirm all three land together on the real path (Case C, live).
    print(f"\n  --- grid + z-stack, real bridge (Case C over ZMQ) ---")
    gz = live_metadata_for(LIVE_XY, LIVE_LABELS, z=(0.0, 2.0, 1.0))
    z_ok = False
    if gz:
        present = _present_axes(gz[0])
        z_ok = present == {"x", "y", "z"}
        print(f"      coordinate axes present: "
              f"{''.join(sorted(a.upper() for a in present)) or '(none)'}   (expected XYZ)")
        for axis in ("x", "y", "z"):
            print(f"      {COORD_KEYS[axis]:24s} = {gz[0].get(COORD_KEYS[axis])!r}")

    ok = xy_ok and id_ok and z_ok
    print(f"\n  --> real bridge carries X/Y for a grid : {'YES' if xy_ok else 'NO'}")
    print(f"  --> real bridge carries a position id  : {'YES' if id_ok else 'NO'}"
          f" (PositionName or Axes.position -- where()'s fallback)")
    print(f"  --> real bridge carries X/Y/Z for grid+z: {'YES' if z_ok else 'NO'}")
    if not ok:
        print("\n  !! where() as designed in F2 would NOT populate correctly here.")
        print("     Adjust HookBase.where()'s key names/fallback to match the dump above.")
    else:
        print("\n  OK: the keys F2's where() reads are present on the real bridge, as named.")
    return ok


def live_f3_timing(port: int) -> bool:
    """(2) Measure the F3 O(N^2) PositionList trap: add_position() N times on the bridge."""
    import time

    print(f"\n{'=' * 74}\nLIVE 2 — F3 add_position timing on the real PositionList (port {port})\n{'=' * 74}")
    print("  ctrl.add_position rebuilds+round-trips MM's WHOLE PositionList each call")
    print("  (controller.py:324). This measures whether that is O(N^2) enough to need")
    print("  the batch add_positions() F3 flags. Demo config -- no real stage moves.\n")

    from microclaw.controller import MicroscopeController

    ctrl = MicroscopeController(port=port)
    if not ctrl.is_connected():
        print("  !! Not connected to MM on this port; skipping.")
        return False

    print(f"  {'N':>5}  {'total_s':>9}  {'per_call_ms':>12}")
    prev_per_call = None
    scales_linearly = True
    for n in (10, 50, 100):
        ctrl.clear_positions()
        t0 = time.perf_counter()
        for i in range(n):
            ctrl.add_position(f"d23_{i:03d}", 100.0 + i, 0.0, z=0.0)
        dt = time.perf_counter() - t0
        per_call_ms = dt / n * 1000
        print(f"  {n:>5}  {dt:>9.3f}  {per_call_ms:>12.1f}")
        # Per-call cost that climbs with N is the O(N^2) signature (each call
        # rebuilds a list that is getting longer).
        if prev_per_call is not None and per_call_ms > prev_per_call * 1.5:
            scales_linearly = False
        prev_per_call = per_call_ms
    ctrl.clear_positions()

    print()
    if scales_linearly:
        print("  --> per-call cost roughly flat: add_position() is fine as-is; the batch")
        print("      add_positions() in F3 is optional. Record these numbers in design/23.")
    else:
        print("  --> per-call cost climbs with N: the O(N^2) trap is real on this rig.")
        print("      Ship F3's ctrl.add_positions() batch (one set_position_list) too.")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="also run the real-MM checks (needs a running demo config)")
    ap.add_argument("--port", type=int, default=4827, help="MM ZMQ port (default 4827)")
    args = ap.parse_args()

    offline_ok = offline_cases()

    if not args.live:
        print("  (offline only -- pass --live against a running MM demo config for the "
              "bridge checks)\n")
        return 0 if offline_ok else 1

    md_ok = live_metadata_dump(args.port)
    live_f3_timing(args.port)  # informational; timing is data, not pass/fail

    print(f"\n{'=' * 74}\nLIVE VERDICT\n{'=' * 74}")
    print(f"  real bridge delivers where()'s keys to a hook: {'PASS' if md_ok else 'FAIL'}")
    print("  F3 timing: see LIVE 2 above; fold the numbers into design/23 F3.\n")
    return 0 if (offline_ok and md_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
