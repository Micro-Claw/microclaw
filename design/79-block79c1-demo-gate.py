r"""Block 79c-1's demo-machine gate: does the composite breakdown measure a real grid?

design/79's 79c brief asked for the *measured* residuals to be optimized, and
found that both of them are per-acquisition costs. Block 75a measured one of
them -- `mark_finished` -> first frame accounted, 158.8 ms p50 on this machine,
96.7% of a one-frame acquisition's window -- and design/79 says plainly that
those numbers describe those one-frame experiments and not a universal floor.
Whether the cost is *multiplied* by a multi-field grid has never been measured,
because until this block neither composite reported any timing at all.

So this gate has two jobs and they are scored differently.

The criteria are structural: the breakdown is well formed, it reconciles, it
distinguishes the route the run actually took, and its payload is bounded. Those
are PASS/FAIL.

The measurement is not a criterion. It reports `acquisition.total_s` for matched
shapes that differ in how many Acquisitions they construct, so the per-field
multiplier is a number rather than an argument. **No wall-clock threshold is
asserted anywhere in this file** -- design/79 forbids shipping one, and a gate
that failed on a timing constant would be measuring this machine's mood.

No sample, no dose, no hardware write: the demo camera and the demo stage only.

    uv run python design\79-block79c1-demo-gate.py --out block79c1-evidence
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

    def add(self, name, status, detail, would_fail=None, data=None):
        assert status in ("PASS", "FAIL", "NOT EXERCISED", "MEASURED")
        self.limbs.append({"limb": name, "status": status, "detail": detail,
                           "would_have_failed_if": would_fail, "data": data})
        print(f"{status:<14} {name}\n               {detail}", flush=True)

    def finish(self, out: Path):
        # MEASURED limbs carry numbers, not verdicts, and are excluded from the
        # score. A criterion that cannot fail is not a criterion (design/58a).
        criteria = [l for l in self.limbs if l["status"] != "MEASURED"]
        ok = sum(1 for l in criteria if l["status"] == "PASS")
        print(f"\n{ok}/{len(criteria)} PASS ({len(self.limbs) - len(criteria)} measured)")
        for l in criteria:
            if l["status"] != "PASS":
                print(f"  {l['status']}: {l['limb']}")
        (out / "score.json").write_text(
            json.dumps({"limbs": self.limbs, "passed": ok,
                        "total": len(criteria)}, indent=2), encoding="utf-8")
        return 0 if ok == len(criteria) else 1


# Every shape a real caller can reach, with the number of Acquisitions each
# constructs. The last one is the shared-dataset route: one acquisition with a
# `position` axis, which is safe only at interval_s == 0 (CLAUDE.md's tenth
# engine contract). `acqs` is a function of the field count.
SHAPES = [
    ("hookless-zero",   None,           0.0, lambda n: n),
    ("hookless-spaced", None,           0.5, lambda n: n),
    ("hooked-spaced",   "snr_observer", 0.5, lambda n: n),
    ("hooked-zero",     "snr_observer", 0.0, lambda n: 1),
]

REQUIRED_KEYS = {"clock", "duration_s", "record_count", "phases",
                 "accounted_s", "unaccounted_s", "phase_meaning",
                 "slowest_records", "slowest_meaning"}


def run_grid(tools, ctrl, guard, out: Path, label, hook, interval_s, n,
             n_frames=1, exposure_ms=10.0):
    """One grid through the real tool. Returns its whole payload."""
    save_dir = out / "data" / f"{label}-n{n}"
    save_dir.mkdir(parents=True, exist_ok=True)
    params = {"n_frames": n_frames, "interval_s": interval_s,
              "exposure_ms": exposure_ms}
    kwargs = {"protocol": "timelapse",
              "positions": [{"name": f"P{i}", "x_um": float(i * 20),
                             "y_um": 0.0} for i in range(n)],
              "save_dir": str(save_dir), "name": label,
              "protocol_params": params}
    if hook:
        kwargs["hook_strategy"] = hook
    result = tools.run_multiposition_acquisition(ctrl, guard, **kwargs)
    (out / f"payload-{label}-n{n}.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=4827)
    ap.add_argument("--fields", type=int, default=8,
                    help="fields for the structural limbs and the measurement")
    ap.add_argument("--bound-fields", type=int, default=24,
                    help="fields for the payload-bound limb")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = Report()

    from microclaw import tools
    from microclaw.safety import SafetyGuard
    from microclaw.config import load_safety_config_or_exit

    loaded = {}

    def rig():
        """Bridge, controller and guard, loaded once, never exiting the process.

        `load_safety_config_or_exit` raises SystemExit by design, so a gate that
        lets it through prints no score at all.
        """
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

    payloads = {}

    def limb(name, would_fail):
        def decorate(fn):
            try:
                status, detail, data = fn()
            except NotExercised as exc:
                report.add(name, "NOT EXERCISED", str(exc), would_fail)
            except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                (out / f"traceback-{len(report.limbs)}.txt").write_text(
                    traceback.format_exc(), encoding="utf-8")
                report.add(name, "FAIL", f"{type(exc).__name__}: {exc}", would_fail)
            else:
                report.add(name, status, detail, would_fail, data)
            return fn
        return decorate

    @limb("0 - bridge, camera and XY stage reachable, workspace configured",
          "any of them missing on this machine")
    def _limb0():
        ctrl, guard = rig()
        camera = ctrl.core.get_camera_device()
        xy = ctrl.core.get_xy_stage_device()
        if not camera or not xy:
            return "FAIL", f"camera={camera!r} xy_stage={xy!r}", None
        # Fail here rather than inside every grid limb below.
        guard.resolve_in_workspace(str(out / "data"))
        return "PASS", f"camera={camera!r}, xy_stage={xy!r}", {
            "camera": camera, "xy_stage": xy}

    @limb("1 - every reachable grid returns one well-formed composite breakdown",
          "a missing breakdown, a missing key, or a dominant_phase field")
    def _limb1():
        ctrl, guard = rig()
        problems, seen = [], {}
        for label, hook, interval_s, acqs in SHAPES:
            result = run_grid(tools, ctrl, guard, out, label, hook,
                              interval_s, args.fields)
            payloads[label] = result
            if "error" in result:
                problems.append(f"{label}: error {result['error']!r}")
                continue
            b = result.get("duration_breakdown")
            if b is None:
                problems.append(f"{label}: no duration_breakdown")
                continue
            missing = REQUIRED_KEYS - set(b)
            if missing:
                problems.append(f"{label}: missing {sorted(missing)}")
            if "dominant_phase" in b:
                problems.append(f"{label}: carries dominant_phase")
            if set(b["phases"]) - {"acquisition", "restoration"}:
                problems.append(f"{label}: unexpected phases {sorted(b['phases'])}")
            if "acquisition" not in b["phases"]:
                problems.append(f"{label}: no acquisition phase")
            seen[label] = {"duration_s": b["duration_s"],
                           "phases": {k: v["count"] for k, v in b["phases"].items()}}
        if problems:
            return "FAIL", "; ".join(problems), seen
        return "PASS", f"{len(SHAPES)} shapes, all well formed", seen

    @limb("2 - the breakdown reconciles against the run's own duration",
          "accounted_s + unaccounted_s drifting from duration_s")
    def _limb2():
        if not payloads:
            raise NotExercised("limb 1 produced no payloads")
        rows, problems = {}, []
        for label, result in payloads.items():
            b = result.get("duration_breakdown")
            if b is None:
                continue
            drift = abs(b["accounted_s"] + b["unaccounted_s"] - b["duration_s"])
            rows[label] = {"duration_s": b["duration_s"],
                           "accounted_s": b["accounted_s"],
                           "unaccounted_s": b["unaccounted_s"], "drift": drift}
            # Float subtraction, not an exact identity: block 79c-1's round 1
            # shipped a math.nextafter fudge to force bit-exactness and it left
            # the identity broken in 3.2% of value pairs anyway.
            if drift > 1e-9 * max(1.0, abs(b["duration_s"])):
                problems.append(f"{label}: drift {drift:.3e}")
            if b["duration_s"] != result.get("duration_s"):
                problems.append(f"{label}: duration_s disagrees with the payload's")
        if not rows:
            raise NotExercised("no breakdown to reconcile")
        if problems:
            return "FAIL", "; ".join(problems), rows
        return "PASS", "every shape reconciles to float precision", rows

    @limb("3 - the breakdown names the route the run actually took",
          "the per-field and shared-dataset shapes reporting the same count")
    def _limb3():
        if not payloads:
            raise NotExercised("limb 1 produced no payloads")
        rows, problems = {}, []
        for label, hook, interval_s, acqs in SHAPES:
            result = payloads.get(label, {})
            b = result.get("duration_breakdown")
            if b is None or "acquisition" not in b.get("phases", {}):
                continue
            expected = acqs(args.fields)
            got = b["phases"]["acquisition"]["count"]
            rows[label] = {"expected_acquisitions": expected, "reported": got}
            if got != expected:
                problems.append(f"{label}: expected {expected} acquisitions, "
                                f"breakdown reports {got}")
        if len(rows) < len(SHAPES):
            raise NotExercised(f"only {len(rows)}/{len(SHAPES)} shapes produced "
                               "an acquisition phase")
        # The discriminating control: if these agreed, the breakdown would not
        # be measuring the route at all.
        if rows["hookless-zero"]["reported"] == rows["hooked-zero"]["reported"]:
            problems.append("per-field and shared-dataset shapes report the same "
                            "acquisition count; the breakdown is not route-aware")
        if problems:
            return "FAIL", "; ".join(problems), rows
        return "PASS", ", ".join(f"{k}={v['reported']}" for k, v in rows.items()), rows

    @limb("4 - no child row carries its own breakdown",
          "a per-field breakdown left in results, which is unbounded in field count")
    def _limb4():
        if not payloads:
            raise NotExercised("limb 1 produced no payloads")
        offenders = [f"{label}[{i}]"
                     for label, result in payloads.items()
                     for i, child in enumerate(result.get("results", ()) or ())
                     if isinstance(child, dict) and "duration_breakdown" in child]
        if offenders:
            return "FAIL", f"child breakdowns present: {offenders[:5]}", offenders
        disclosed = all(
            "omitted from child results" in (r.get("duration_breakdown") or {})
                .get("phase_meaning", "")
            for r in payloads.values() if r.get("duration_breakdown"))
        if not disclosed:
            return "FAIL", "omission is not disclosed in phase_meaning", None
        return "PASS", "folded and disclosed in phase_meaning", None

    @limb("5 - the payload is bounded in the field count",
          "a breakdown that grows with the number of fields")
    def _limb5():
        ctrl, guard = rig()
        sizes = {}
        for n in (2, args.bound_fields):
            result = run_grid(tools, ctrl, guard, out, "bound", None, 0.0, n)
            b = result.get("duration_breakdown")
            if b is None:
                raise NotExercised(f"n={n} returned no breakdown")
            sizes[n] = len(json.dumps(b))
        growth = max(sizes.values()) - min(sizes.values())
        detail = ", ".join(f"{n} fields={s} bytes" for n, s in sizes.items())
        if growth > 200:
            return "FAIL", f"{detail}; grew {growth} bytes", sizes
        return "PASS", f"{detail}; grew {growth} bytes", sizes

    @limb("6 - MEASURED: what a grid's time is actually spent on",
          None)
    def _limb6():
        """Not a criterion. The number 79c's brief asked for and could not get.

        Matched on frame count and exposure, differing in how many Acquisitions
        are constructed. Confound named rather than hidden: the per-field shape
        also writes N datasets and performs N-1 settled stage moves, and the
        stage moves are reported separately as the residual.
        """
        if not payloads:
            raise NotExercised("limb 1 produced no payloads")
        rows = {}
        for label, hook, interval_s, acqs in SHAPES:
            b = (payloads.get(label, {}) or {}).get("duration_breakdown")
            if b is None or "acquisition" not in b.get("phases", {}):
                continue
            phase = b["phases"]["acquisition"]
            rows[label] = {
                "acquisitions": phase["count"],
                "acquisition_total_s": round(phase["total_s"], 6),
                "acquisition_mean_s": round(phase["mean_s"], 6),
                "acquisition_max_s": round(phase["max_s"], 6),
                "residual_s": round(b["unaccounted_s"], 6),
                "duration_s": round(b["duration_s"], 6),
                "accounted_fraction": (round(b["accounted_s"] / b["duration_s"], 4)
                                       if b["duration_s"] else None),
            }
        if not rows:
            raise NotExercised("no acquisition phase to measure")
        (out / "measurement.json").write_text(json.dumps(rows, indent=2),
                                              encoding="utf-8")
        detail = "; ".join(
            f"{k}: {v['acquisitions']} acq, {v['acquisition_total_s']}s in acq, "
            f"{v['residual_s']}s residual" for k, v in rows.items())
        return "MEASURED", detail, rows

    return report.finish(out)


if __name__ == "__main__":
    sys.exit(main())
