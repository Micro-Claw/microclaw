"""Block 75a, M2 arm: what a healthy one-frame acquisition costs end to end.

This is the measurement block 75b's two constants come from, and it is the only
part of block 75a that needs M2 rather than the demo machine. design/75 §"Why
both terms" asks for the maximum healthy **end-to-end** latency -- submission to
returned result -- with the finalization segment broken out, because
`runtime_slack_s` is the term that decides when the operator sees a result and
the quiet floor only decides whether a late frame can extend it.

Why M2 and not the demo machine: this is the rig and the arm the incident
happened on -- an Andor iXon writing to `F:\\DataSSD`, 50 ms, `laser_slot=3`,
one frame, `interval_s=0`, with a `snap_and_analyze` immediately before each
call. DemoCamera against a local disk is not a bound on any of that. The demo
gate carries the same measurement as a reference arm so these numbers have
something to be compared against.

**Dose.** With the defaults this fires the arm's laser for `2 x --runs x 50 ms`
-- 20 runs is 40 exposures, about 2 seconds of total illumination. The camera
trigger fires the lasers on M2, so the snaps are a dose too and are counted
above. Nothing here sweeps a stage or moves an objective.

It sets no constants. It writes the numbers; the coordinator records them in
design/75 and 75b chooses from them, so that the choice and the measurement are
not made by the same pass.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Fold rather than fork: the demo gate already owns the JSONL reader, the
# per-call grouping and the lifecycle vocabulary, and two readers that drifted
# apart would let this arm and that gate disagree about the same file.
_spec = importlib.util.spec_from_file_location(
    "gate75a", Path(__file__).with_name("75-block75a-demo-gate.py"))
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def summary(values):
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "min": ordered[0],
        "p50": statistics.median(ordered),
        "p95": ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))],
        "max": ordered[-1],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--save-dir", required=True,
                        help=r"Where the datasets go, e.g. F:\DataSSD\block75a")
    parser.add_argument("--out", type=Path, default=Path("block75a-m2-latency"))
    parser.add_argument("--safety-config", default=None,
                        help="Normally omitted; the product finds this rig's own.")
    parser.add_argument("--runs", type=int, default=20,
                        help="One-frame acquisitions. 20 shows a distribution; "
                             "design/75 explicitly does not want 100.")
    parser.add_argument("--exposure-ms", type=float, default=50.0,
                        help="The incident's exposure. Do not change it lightly.")
    parser.add_argument("--laser-slot", type=int, default=3,
                        help="The incident's slot. --laser-slot -1 omits it.")
    parser.add_argument("--no-snap", action="store_true",
                        help="Skip the snap before each run. The session that "
                             "failed did snap, so the default reproduces it.")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = gate.Tee(sys.__stdout__, args.out / "m2-latency.txt")

    from microclaw import tools
    from microclaw.conversation import AcquisitionDiagnosticWriter, AuditLog
    from microclaw.config import load_safety_config_or_exit
    from microclaw.controller import MicroscopeController
    from microclaw.safety import SafetyGuard

    ctrl = MicroscopeController(port=args.port)
    guard = SafetyGuard(load_safety_config_or_exit(args.safety_config).constraints)
    confirmations = []

    def confirm(summary_text, kind="action", subject=None):
        # No console here, so record and approve -- and print it, because an
        # auto-approval nobody can read afterwards is design/60 F5 again.
        confirmations.append({"kind": kind, "subject": subject,
                              "summary": summary_text})
        print(f"[m2] auto-approved {kind}/{subject}: {summary_text}")
        return True

    tools.CONFIRM_FN = confirm

    stamp = time.strftime("%Y%m%d_%H%M%S")
    history = args.out / f"{stamp}_m2_microclaw_history.jsonl"
    acq_path = Path(str(history).replace("_history.jsonl", "_acquisitions.jsonl"))
    writer = AcquisitionDiagnosticWriter(AuditLog(acq_path))
    session_id = history.name.replace("_history.jsonl", "_history")

    camera = str(ctrl.core.get_camera_device() or "")
    print(f"camera={camera} save_dir={args.save_dir} runs={args.runs} "
          f"exposure={args.exposure_ms}ms laser_slot="
          f"{'omitted' if args.laser_slot < 0 else args.laser_slot} "
          f"snap={'no' if args.no_snap else 'yes'}")
    print(f"acquisitions file: {acq_path}")

    # snap_and_analyze takes no exposure argument -- its parameters are
    # return_thumbnail, thumbnail_size, display and region -- so the exposure is
    # set once here, through the tool that owns it. Checking that signature is
    # why this line exists: an invented `exposure_ms` would have come back as an
    # error result from every snap on the rig, and read as a product failure.
    exposure_raw = tools.execute_tool(
        "set_exposure", {"ms": args.exposure_ms}, ctrl, guard, tools.TOOL_REGISTRY,
        acquisition_diagnostic_writer=writer, acquisition_session_id=session_id,
        tool_call_id="m2-latency-set-exposure",
    )
    print(f"set_exposure -> {exposure_raw}")

    calls, failures = [], []
    for index in range(args.runs):
        call_id = f"m2-latency-{index}"
        if not args.no_snap:
            # The session's own shape: a snap immediately before each
            # acquisition, so camera ownership changes hands the same way. D5 /
            # R91 is about whether that churn matters; this arm does not test
            # it, it only avoids measuring a shape the incident did not have.
            snap_raw = tools.execute_tool(
                "snap_and_analyze", {"return_thumbnail": False},
                ctrl, guard, tools.TOOL_REGISTRY,
                acquisition_diagnostic_writer=writer,
                acquisition_session_id=session_id,
                tool_call_id=f"{call_id}-snap",
            )
            if isinstance(snap_raw, str) and '"error"' in snap_raw:
                failures.append({"call": f"{call_id}-snap", "raw": snap_raw[:400]})

        payload = {"n_frames": 1, "interval_s": 0,
                   "exposure_ms": args.exposure_ms,
                   "save_dir": args.save_dir,
                   "name": f"block75a_m2_latency_{stamp}_{index}"}
        if args.laser_slot >= 0:
            payload["laser_slot"] = args.laser_slot
        started = time.monotonic()
        raw = tools.execute_tool(
            "run_timelapse", payload, ctrl, guard, tools.TOOL_REGISTRY,
            acquisition_diagnostic_writer=writer,
            acquisition_session_id=session_id,
            tool_call_id=call_id,
        )
        wall_s = time.monotonic() - started
        result = json.loads(raw) if isinstance(raw, str) else raw
        calls.append({"call": call_id, "wall_s": wall_s,
                      "dataset_path": result.get("dataset_path"),
                      "error": result.get("error")})
        if result.get("error"):
            failures.append({"call": call_id, "error": result["error"]})
            print(f"  {index}: ERROR {result['error']}")
        else:
            print(f"  {index}: {wall_s:.3f}s -> {result.get('dataset_path')}")

    writer.close()

    records, torn = gate.read_jsonl(acq_path)
    grouped = gate.by_call(records)
    end_to_end, finalization, incomplete = [], [], {}
    for call in calls:
        if call["error"]:
            continue
        for_call = grouped.get(call["call"], [])
        kinds = set(gate.lifecycle_kinds(for_call))
        if kinds != set(gate.COMPLETE_LIFECYCLE):
            incomplete[call["call"]] = sorted(kinds)
            continue
        begin = gate.iso_to_s(gate.stamps(for_call, "acquisition_construction")[0])
        marked = gate.iso_to_s(gate.stamps(for_call, "acquisition_mark_finished")[0])
        done = gate.iso_to_s(
            gate.stamps(for_call, "acquisition_teardown_completion")[0])
        end_to_end.append(done - begin)
        finalization.append(done - marked)

    payload = {
        "camera": camera, "runs": args.runs, "exposure_ms": args.exposure_ms,
        "laser_slot": None if args.laser_slot < 0 else args.laser_slot,
        "snap_before_each": not args.no_snap,
        "acquisitions_file": str(acq_path),
        "torn_lines": torn,
        "failures": failures,
        "incomplete_records": incomplete,
        "calls": calls,
        "confirmations": confirmations,
        # Two independent measurements of the same quantity. They should agree
        # closely; a disagreement means the record is not describing the call,
        # which is the finding.
        "file_end_to_end_s": summary(end_to_end) if end_to_end else None,
        "file_finalization_s": summary(finalization) if finalization else None,
        "measured_call_wall_s": summary([c["wall_s"] for c in calls
                                         if not c["error"]]) if calls else None,
    }
    target = args.out / "m2-latency.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str),
                      encoding="utf-8")

    print("\n--- what to send back ---")
    print(f"{target}")
    print(f"{acq_path}")
    if torn:
        print(f"TORN LINES: {torn}")
    if failures:
        print(f"FAILURES: {len(failures)} - see the JSON")
    if incomplete:
        print(f"INCOMPLETE RECORDS: {incomplete}")
    if payload["file_end_to_end_s"] is None:
        print("NOT EXERCISED - no run produced a complete record, so there is "
              "no latency to report. This is never a pass.")
        return 1
    e2e = payload["file_end_to_end_s"]
    fin = payload["file_finalization_s"]
    wall = payload["measured_call_wall_s"]
    print(f"end-to-end   n={e2e['n']} min={e2e['min']:.3f} p50={e2e['p50']:.3f} "
          f"p95={e2e['p95']:.3f} max={e2e['max']:.3f} s")
    print(f"finalization n={fin['n']} min={fin['min']:.3f} p50={fin['p50']:.3f} "
          f"p95={fin['p95']:.3f} max={fin['max']:.3f} s")
    print(f"call wall    n={wall['n']} p50={wall['p50']:.3f} "
          f"max={wall['max']:.3f} s (independent of the file)")
    print("\nThis arm sets no constant. design/75 asks for at least 10x headroom "
          "over the maximum healthy end-to-end latency; the coordinator records "
          f"max={e2e['max']:.3f}s in design/75 and 75b chooses from it.")
    return 1 if (torn or failures or incomplete) else 0


if __name__ == "__main__":
    sys.exit(main())
