r"""design/79's M2 close-out: the two things only an EMU rig can measure.

design/79's 79c brief named two residuals. One is now measured -- block 75a's
per-acquisition span multiplies across a grid, 6.5-7.9x on the demo machine,
n=2. The other, `R105`, has never been measured on any machine: M2's frame gaps
were ~0.20-0.35 s at 50 ms exposure in the 2026-09-04 session, and block 78a's
gate was booked to measure what dispatch still costs after its fix and could
not, because all three of its arms requested `interval_s = 0.5`, which hides any
residual below half a second.

**This is a measurement, not a criteria gate.** Every limb reports MEASURED and
is excluded from the score. design/79 forbids shipping a wall-clock threshold, so
there is nothing here to pass or fail except whether the runs happened at all --
which is the one PASS/FAIL guard, because a limb that could not run its
mechanism reports NOT EXERCISED and that is never a pass.

**Why not 78a's CoreLog gate, which `R105`'s row nominates.** That scorer reads
exposure markers out of the CoreLog, and its own docstring says a *sequenced*
run yields one marker per burst -- so its cadence limb would report n=1 for
exactly the short-interval run `R105` needs. microclaw's own
`inter_frame_gap_summary` is measured per saved frame, is now on a sub-microsecond
clock after block 79c-2, and needs no log at all. That is the right instrument
and it is already in the result.

M2's camera triggers the lasers, so these runs fire light. Operator authorised
the dose, 2026-09-10. About 100 frames at 50 ms.

    uv run python design\79-m2-closeout-gate.py --out block79-m2-evidence
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


class NotExercised(Exception):
    """The limb's mechanism could not run here. Never a pass."""


class Report:
    def __init__(self):
        self.limbs = []

    def add(self, name, status, detail, data=None, *, measurement=False):
        assert status in ("PASS", "FAIL", "NOT EXERCISED", "MEASURED")
        self.limbs.append({"limb": name, "status": status, "detail": detail,
                           "kind": "measurement" if measurement else "criterion",
                           "data": data})
        print(f"{status:<14} {name}\n               {detail}", flush=True)

    def finish(self, out: Path):
        # Classified by declared KIND, not by the status it happened to report.
        # A measurement that could not run is reported and not scored; a
        # criterion that could not run fails, because NOT EXERCISED is never a
        # pass. The selftest found this: `--skip-multiplier` failed the gate.
        criteria = [l for l in self.limbs if l["kind"] == "criterion"]
        ok = sum(1 for l in criteria if l["status"] == "PASS")
        print(f"\n{ok}/{len(criteria)} PASS "
              f"({len(self.limbs) - len(criteria)} measured)")
        for l in criteria:
            if l["status"] != "PASS":
                print(f"  {l['status']}: {l['limb']}")
        (out / "score.json").write_text(
            json.dumps({"limbs": self.limbs, "passed": ok,
                        "total": len(criteria)}, indent=2), encoding="utf-8")
        return 0 if ok == len(criteria) else 1


# R105's own conditions: 50 ms exposure, and intervals short enough that the
# engine is not simply honouring min_start_time. 0.5 s is 78a's blind arm and is
# carried as the control that reproduces its result.
CADENCE_ARMS = [
    ("zero-interval", 0.0, "snr_observer"),
    ("short-60ms", 0.06, "snr_observer"),
    ("control-500ms", 0.5, "snr_observer"),
    # design/79 item 4 asks for a no-hook control and the first M2 run had
    # none, so its ~0.19 s software-paced per-frame cost could not be
    # attributed between dispatch and the hook's own analysis.
    ("short-60ms-no-hook", 0.06, None),
]
FRAMES = 20
EXPOSURE_MS = 50.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=4827)
    ap.add_argument("--frames", type=int, default=FRAMES)
    ap.add_argument("--exposure-ms", type=float, default=EXPOSURE_MS)
    ap.add_argument("--fields", type=int, default=6,
                    help="fields for the per-acquisition multiplier limb")
    ap.add_argument("--step-um", type=float, default=20.0,
                    help="field spacing, as an offset from the current position")
    ap.add_argument("--skip-multiplier", action="store_true",
                    help="run the R105 cadence arms only")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = Report()

    from microclaw import tools
    from microclaw.safety import SafetyGuard
    from microclaw.config import load_safety_config_or_exit

    loaded = {}

    def rig():
        if not loaded:
            try:
                from microclaw.controller import MicroscopeController
                config = load_safety_config_or_exit(None)
                ctrl = MicroscopeController(port=args.port)
                loaded.update(ctrl=ctrl, guard=SafetyGuard(config.constraints))
            except (SystemExit, Exception) as exc:  # noqa: BLE001 - reported
                loaded.update(ctrl=None, guard=None,
                              error=str(exc) or type(exc).__name__)
        if loaded.get("ctrl") is None:
            raise NotExercised("no bridge/safety config on this machine: "
                               + loaded.get("error", "unknown"))
        return loaded["ctrl"], loaded["guard"]

    def limb(name, *, measurement=False):
        def decorate(fn):
            try:
                status, detail, data = fn()
            except NotExercised as exc:
                report.add(name, "NOT EXERCISED", str(exc),
                           measurement=measurement)
            except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                (out / f"traceback-{len(report.limbs)}.txt").write_text(
                    traceback.format_exc(), encoding="utf-8")
                report.add(name, "FAIL", f"{type(exc).__name__}: {exc}",
                           measurement=measurement)
            else:
                report.add(name, status, detail, data, measurement=measurement)
            return fn
        return decorate

    cadence = {}

    @limb("0 - the runs happened: bridge, camera, and frames actually saved")
    def _limb0():
        ctrl, guard = rig()
        camera = ctrl.core.get_camera_device()
        if not camera:
            return "FAIL", "no camera device", None
        save_dir = out / "data"
        save_dir.mkdir(parents=True, exist_ok=True)
        guard.resolve_in_workspace(str(save_dir))
        for label, interval_s, hook in CADENCE_ARMS:
            result = tools.run_timelapse(
                ctrl, guard, args.frames, interval_s,
                str(save_dir / label), exposure_ms=args.exposure_ms,
                name=label, **({"hook_strategy": hook} if hook else {}),
            )
            (out / f"payload-{label}.json").write_text(
                json.dumps(result, indent=2, default=str), encoding="utf-8")
            cadence[label] = result
        empty = [label for label, r in cadence.items()
                 if not (r.get("inter_frame_gap_summary") or {}).get("count")]
        if empty:
            return "FAIL", f"arms with no measured gaps: {empty}", None
        return "PASS", (f"{len(cadence)} arms, {args.frames} frames each at "
                        f"{args.exposure_ms:g} ms on {camera!r}"), {
            "camera": camera, "arms": list(cadence)}

    @limb("1 - MEASURED: R105, the achieved gap at a short requested interval",
          measurement=True)
    def _limb1():
        """The number design/79 has been asking for since block 78a."""
        if not cadence:
            raise NotExercised("limb 0 produced no runs")
        rows = {}
        for label, interval_s, hook in CADENCE_ARMS:
            r = cadence.get(label)
            if not r:
                continue
            gaps = r.get("inter_frame_gap_summary") or {}
            b = r.get("duration_breakdown") or {}
            rows[label] = {
                "requested_interval_s": interval_s,
                "hook": hook,
                "n_gaps": gaps.get("count"),
                "min_s": gaps.get("min_s"), "median_le_s": gaps.get("median_le_s"),
                "mean_s": gaps.get("mean_s"), "p95_le_s": gaps.get("p95_le_s"),
                "max_s": gaps.get("max_s"),
                "route": (r.get("timing") or {}).get("strategy"),
                "accounted_s": b.get("accounted_s"),
                "unaccounted_s": b.get("unaccounted_s"),
            }
        if not rows:
            raise NotExercised("no gap summary in any arm")
        (out / "cadence.json").write_text(json.dumps(rows, indent=2),
                                          encoding="utf-8")
        detail = "; ".join(
            f"{k}: requested {v['requested_interval_s']:g}s -> "
            f"median {v['median_le_s']}s over {v['n_gaps']} gaps"
            for k, v in rows.items())
        return "MEASURED", detail, rows

    @limb("2 - MEASURED: the per-acquisition multiplier on a real stage",
          measurement=True)
    def _limb2():
        """6.5-7.9x on the demo machine, whose stage moves are nearly free."""
        if args.skip_multiplier:
            raise NotExercised("--skip-multiplier was passed")
        ctrl, guard = rig()
        save_dir = out / "data"
        rows = {}
        # Fields are offsets from WHERE THE STAGE IS, never absolute microns.
        # The first version hardcoded 0, 20, 40... which is harmless on the demo
        # machine, whose stage sits at the origin, and on M2 asked for a 4.4 mm
        # move to absolute (0, 0): the stage travelled 971 um, went idle, and
        # `settle_xy_move` correctly refused to claim arrival -- so the limb
        # measured nothing and left the stage displaced. `run_tile_acquisition`
        # has always defaulted its centre to the current position; do the same.
        home_x = ctrl.core.get_x_position()
        home_y = ctrl.core.get_y_position()
        for label, hook, interval_s, expected in (
                ("per-field", None, 0.0, args.fields),
                ("shared-dataset", "snr_observer", 0.0, 1)):
            result = tools.run_multiposition_acquisition(
                ctrl, guard, protocol="timelapse",
                positions=[{"name": f"P{i}",
                            "x_um": home_x + float(i * args.step_um),
                            "y_um": home_y}
                           for i in range(args.fields)],
                save_dir=str(save_dir / label), name=label,
                protocol_params={"n_frames": 1, "interval_s": interval_s,
                                 "exposure_ms": args.exposure_ms},
                **({"hook_strategy": hook} if hook else {}))
            (out / f"payload-{label}.json").write_text(
                json.dumps(result, indent=2, default=str), encoding="utf-8")
            b = result.get("duration_breakdown") or {}
            acq = (b.get("phases") or {}).get("acquisition") or {}
            rows[label] = {
                "acquisitions": acq.get("count"),
                "expected_acquisitions": expected,
                "acquisition_total_s": acq.get("total_s"),
                "residual_s": b.get("unaccounted_s"),
                "duration_s": b.get("duration_s"),
            }
        per, one = rows.get("per-field", {}), rows.get("shared-dataset", {})
        ratio = (per.get("duration_s") / one["duration_s"]
                 if one.get("duration_s") else None)
        rows["ratio"] = ratio
        rows["ratio_meaning"] = (
            "per-field wall clock over shared-dataset wall clock, same frame "
            "count and exposure; 6.5x and 7.9x on the demo machine, n=2")
        (out / "multiplier.json").write_text(json.dumps(rows, indent=2),
                                             encoding="utf-8")
        detail = (f"{per.get('acquisitions')} acquisitions {per.get('duration_s')}s "
                  f"vs {one.get('acquisitions')} acquisition {one.get('duration_s')}s"
                  + (f" -> {ratio:.1f}x" if ratio else ""))
        return "MEASURED", detail, {**rows, "ratio": ratio}

    return report.finish(out)


if __name__ == "__main__":
    sys.exit(main())
