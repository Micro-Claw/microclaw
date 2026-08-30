"""design/68 rig probe: does an XY move on THIS rig demonstrate that it arrived?

Every limb only computes, so this is a program rather than a runbook of pasted
steps (CLAUDE.md: a pasted `throw` ends a pipeline, not a session, and block 58a
reported PASSED over five failed limbs that way). Each limb is scored
independently — one refusal must not hide the rest behind its cascade — and the
script owns its own log, because PowerShell 5.1's Start-Transcript does not
capture a native child's stdout.

    uv run python design/68-gate-probe.py --out gate68

Limbs that could not run their mechanism report NOT EXERCISED, which is never a
pass. Exit code is 0 only when every limb PASSed.

What this does NOT settle, deliberately: whether the per-axis band arithmetic is
right. That is settled off-rig, by a suite that watches the test fail with the
band check deleted. What needs a rig is whether a real asynchronous stage, read
over a real bridge, is out-waited rather than sampled once — and whether a
genuine non-response is refused rather than reported as an arrival.

Limb C needs a physical action and is therefore NOT run by default. See
`design/68-block68-m2-runbook.md`.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

RESULTS: list[dict] = []
STAGE_DEAD: str | None = None   # set by limb 0 when the stage does not respond


def limb(name, mechanism):
    def decorate(fn):
        fn._limb = (name, mechanism)
        return fn
    return decorate


def record(name, mechanism, status, detail):
    RESULTS.append({"limb": name, "mechanism": mechanism,
                    "status": status, "detail": detail})
    print(f"[{status:<13}] {name}\n                {detail}\n", flush=True)


def read_xy(core):
    return float(core.get_x_position()), float(core.get_y_position())


@limb("0_xy_stage_responds", "the XY stage moves and reports it, measured raw")
def limb_stage_responds(ctrl, guard, out):
    """Precondition, and deliberately NOT routed through move_stage_xy.

    move_stage_xy is the subject of this gate. If limb 0 used it, a tool that
    refuses everything would report "this rig has no working stage" — a
    confident, wrong statement about the machine, which is the failure design/59
    records for an enumeration that cannot distinguish "empty" from "unreadable".
    So this limb drives `core` directly and waits on the clock.

    A device that is not busy is not a device that arrived, so this does not
    consult `device_busy` either: it commands a known displacement, waits a
    generous fixed interval, and measures. That is a weaker check than the
    contract under test, which is the point — it must be able to pass on a rig
    where the contract is broken, or it cannot serve as its precondition.
    """
    global STAGE_DEAD
    step_um = float(getattr(limb_stage_responds, "step_um", 20.0))
    core = ctrl.core
    entry = read_xy(core)
    guard.check_xy(entry[0] + step_um, entry[1])

    core.set_relative_xy_position(step_um, 0.0)
    time.sleep(3.0)
    moved = read_xy(core)
    core.set_relative_xy_position(-step_um, 0.0)
    time.sleep(3.0)
    restored = read_xy(core)

    dx = moved[0] - entry[0]
    (out / "stage_response.json").write_text(json.dumps({
        "commanded_step_um": step_um,
        "entry_um": list(entry), "after_move_um": list(moved),
        "after_restore_um": list(restored),
        "observed_dx_um": dx,
        "restore_residual_um": math.hypot(restored[0] - entry[0],
                                          restored[1] - entry[1]),
    }, indent=2), encoding="utf-8")

    if abs(dx) < 0.5 * step_um:
        STAGE_DEAD = (f"a commanded {step_um} um X move produced {dx:.2f} um "
                      f"of measured motion after 3 s")
        return "NOT EXERCISED", (
            STAGE_DEAD + ". Every later limb would be measuring a stage that is "
            "not moving, so none of them can say anything about the code. Check "
            "the controller, the axis, and the joystick lock."
        )
    return "PASS", (
        f"commanded {step_um} um, measured {dx:.2f} um, returned to within "
        f"{math.hypot(restored[0] - entry[0], restored[1] - entry[1]):.2f} um "
        f"of entry — the stage responds and reports position"
    )


@limb("A_move_settles", "move_stage_xy reports a MEASURED arrival, per axis")
def limb_move_settles(ctrl, guard, out):
    """The ordinary success path, scored on the fields that did not exist before.

    `measured_um` must not merely equal `requested_um`: block 56's defect was a
    premature read-back that returned the PRE-move position and called it an
    achievement, and design/56 measured a successful Nikon move still reporting
    `last_device_status: "busy"` after 0.89 s and ~18 polls. So `elapsed_s` and
    the residuals are the evidence here, not the verdict field.
    """
    from microclaw.tools import move_stage_xy

    if STAGE_DEAD is not None:
        return "NOT EXERCISED", f"limb 0: {STAGE_DEAD}"
    step_um = float(getattr(limb_stage_responds, "step_um", 20.0))
    entry = read_xy(ctrl.core)
    try:
        result = move_stage_xy(ctrl, guard, step_um, step_um, absolute=False)
    finally:
        pass
    exit_xy = read_xy(ctrl.core)
    (out / "move_settles.json").write_text(
        json.dumps({"entry_um": list(entry), "result": result,
                    "reread_after_return_um": list(exit_xy)},
                   indent=2, default=str), encoding="utf-8")
    # Put the stage back where the operator left it, through the same contract.
    move_stage_xy(ctrl, guard, -step_um, -step_um, absolute=False)

    required = ["x_arrival_residual_um", "y_arrival_residual_um",
                "x_tolerance_um", "y_tolerance_um", "x_band_source",
                "y_band_source", "measured_um", "elapsed_s", "within_tolerance"]
    missing = [key for key in required if key not in result]
    if missing:
        return "FAIL", f"the move record is missing {missing} — pre-block-68 code?"
    if not result["within_tolerance"]:
        return "FAIL", f"an ordinary {step_um} um move did not settle: {result}"
    for axis in ("x", "y"):
        if result[f"{axis}_arrival_residual_um"] > result[f"{axis}_tolerance_um"]:
            return "FAIL", (f"{axis.upper()} reported within_tolerance with a "
                            f"residual outside its own band: {result}")
    return "PASS", (
        f"settled in {result['elapsed_s']} s; residuals "
        f"x={result['x_arrival_residual_um']} y={result['y_arrival_residual_um']} um "
        f"against bands x={result['x_tolerance_um']} ({result['x_band_source']}) "
        f"y={result['y_tolerance_um']} ({result['y_band_source']}); "
        f"measured {result['measured_um']} for requested {result['requested_um']}"
    )


@limb("B_per_axis_bands", "a held axis is NOT given the moving axis's band")
def limb_per_axis_bands(ctrl, guard, out):
    """The mechanism, not the outcome (block 52b's lesson about outcome-shaped
    steps). A `hypot` gate over the pair passes every ordinary move; it is only
    visible on an asymmetric one, where it would hand a held Y the band earned
    by a long X. So this limb reads the two bands out of the record and compares
    them to each other — the outcome, "the move succeeded", proves nothing.
    """
    from microclaw.tools import move_stage_xy

    if STAGE_DEAD is not None:
        return "NOT EXERCISED", f"limb 0: {STAGE_DEAD}"
    long_um = float(getattr(limb_per_axis_bands, "long_um", 200.0))
    entry = read_xy(ctrl.core)
    guard.check_xy(entry[0] + long_um, entry[1])
    result = move_stage_xy(ctrl, guard, long_um, 0.0, absolute=False)
    move_stage_xy(ctrl, guard, -long_um, 0.0, absolute=False)
    (out / "per_axis_bands.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8")

    x_band, y_band = result["x_tolerance_um"], result["y_tolerance_um"]
    if result["x_band_source"] == "configured" or result["y_band_source"] == "configured":
        return "PASS", (
            f"this rig declares its own XY bands (x={x_band} from "
            f"{result['x_band_source']}, y={y_band} from {result['y_band_source']}); "
            "the response rule is overridden by the operator's own numbers, "
            "which is the documented precedence"
        )
    if x_band <= y_band:
        return "FAIL", (
            f"a {long_um} um X move alongside a held Y produced x_band={x_band} "
            f"and y_band={y_band}. The moving axis must earn a wider band than "
            "the held one; equal bands are the signature of a shared-pair gate."
        )
    return "PASS", (
        f"X moved {long_um} um and earned a {x_band} um band ({result['x_band_source']}); "
        f"Y was held and kept the {y_band} um floor ({result['y_band_source']}). "
        f"Held-axis excursion: {result['error_um'][1]} um, reported and not gated."
    )


@limb("C_non_response_control", "a stage that cannot move REFUSES, naming the axis")
def limb_non_response(ctrl, guard, out):
    """The control that must fire, and the limb this trip exists for.

    Not run unless --control is passed, because it needs a physical action no
    program can perform: the operator disconnects or blocks one axis. Block 66's
    equivalent limb is the one that found a real product defect — a link that is
    down fails EVERY bridge call, so the pre-dispatch read raised before the
    write and an untyped exception escaped carrying none of the contract.

    A limb that cannot fail is not a criterion. This one is scored on the TYPE
    and on the record: an `XYStageMoveError` naming the dead axis, carrying
    `start_um`. A bare bridge exception is a FAIL, not an error in the gate.
    """
    from microclaw.controller import XYStageMoveError

    if not getattr(limb_non_response, "enabled", False):
        return "NOT EXERCISED", (
            "not run: this limb needs an axis physically blocked or "
            "disconnected. See the runbook, then rerun with "
            "--limbs C --control. NOT EXERCISED is never a pass, and this is "
            "the limb the trip exists for."
        )
    from microclaw.tools import move_stage_xy

    step_um = float(getattr(limb_non_response, "step_um", 50.0))
    entry = read_xy(ctrl.core)
    captured: dict = {"entry_um": list(entry), "commanded_step_um": step_um}
    # BOTH axes, and this is load-bearing. An earlier version commanded X only;
    # against a fake with Y blocked it reported "a blocked axis arrived", which
    # is a confidently wrong verdict, because a Y commanded to move zero
    # trivially satisfies its band. The operator blocks whichever axis their rig
    # lets them reach, so the limb must command motion on both and let the
    # refusal name the one that did not respond. Found by the selftest, not on
    # the rig.
    try:
        result = move_stage_xy(ctrl, guard, step_um, step_um, absolute=False)
    except XYStageMoveError as error:
        captured["exception_class"] = type(error).__name__
        captured["result"] = error.result
        captured["axes"] = getattr(error, "axes", None)
        captured["message"] = str(error)
        status = None
    except Exception as error:                    # noqa: BLE001 — that IS the finding
        captured["exception_class"] = type(error).__name__
        captured["message"] = str(error)
        status = ("FAIL", (
            f"a non-responding axis raised {type(error).__name__}, not a typed "
            "XYStageMoveError. An untyped bridge exception carries no start_um, "
            "no band and no axis, so nothing downstream can key on it — this is "
            "exactly the defect block 66's control limb found for single axes."
        ))
    else:
        captured["result"] = result
        status = ("FAIL", (
            "a blocked axis reported a successful arrival: "
            f"{result}. Either the axis was not actually blocked — in which case "
            "this limb did not run its mechanism and must not be ticked — or the "
            "contract is reporting a move that did not happen."
        ))
    (out / "non_response_control.json").write_text(
        json.dumps(captured, indent=2, default=str), encoding="utf-8")
    if status is not None:
        return status

    result = captured["result"]
    if result.get("start_um") is None:
        return "FAIL", (
            "XYStageMoveError was raised but carries start_um: null, so the "
            "refusal cannot say what the axis was doing before the command. On a "
            "blocked (not disconnected) axis the start IS readable; a null here "
            "means the read was skipped or swallowed."
        )
    if not captured.get("axes"):
        return "FAIL", "the refusal does not name which axis failed"
    return "PASS", (
        f"blocked axis refused: {captured['exception_class']} naming "
        f"{captured['axes']}, start_um={result['start_um']}, "
        f"measured_um={result['measured_um']}, bands "
        f"x={result['x_tolerance_um']}/y={result['y_tolerance_um']}, "
        f"after {result['elapsed_s']} s"
    )


@limb("D_centring_sub_band", "center_feature completes THROUGH sub-band corrections")
def limb_centring(ctrl, guard, out):
    """design/68's own trap: a final centring correction is routinely smaller
    than the 2.0 um response floor, so it reports arrival_unverifiable. That is
    correct and must not refuse — the loop's NEXT residual is the response
    evidence. This limb exists to prove the arrival contract did not break
    centring, which is the tool that made the register row urgent.

    Free corroboration for design/67: `residuals_px` is written out whatever the
    verdict, so an operator reading this evidence later gets the convergence
    sequence for nothing.
    """
    from microclaw.tools import center_feature, find_features

    if STAGE_DEAD is not None:
        return "NOT EXERCISED", f"limb 0: {STAGE_DEAD}"
    before = find_features(ctrl, guard)
    if before.get("brightest_feature_offset_px") is None:
        return "NOT EXERCISED", (
            "no punctum in this field, so there is nothing to centre. Move to a "
            "field with something in it and rerun --limbs D."
        )
    start_px = math.hypot(*before["brightest_feature_offset_px"])
    result = center_feature(ctrl, guard, max_iter=4, tol_px=5.0)
    after = find_features(ctrl, guard)
    (out / "centring.json").write_text(json.dumps({
        "before": before, "center_feature": result, "after": after,
        "residual_px_entry": start_px,
    }, indent=2, default=str), encoding="utf-8")

    if "error" in result:
        return "FAIL", f"center_feature failed under the arrival contract: {result['error']}"
    unverifiable = result.get("arrival_unverifiable_corrections")
    if unverifiable is None:
        return "FAIL", ("center_feature does not report "
                        "arrival_unverifiable_corrections — pre-block-68 code?")
    # Say plainly which half of the mechanism ran. The loop completing under the
    # arrival contract is always exercised; the sub-band half only runs if some
    # correction was actually smaller than its band, which depends on where the
    # feature happened to sit. Reporting "PASS" without that distinction is how
    # a limb comes to be ticked for something it never did.
    sub_band = ("no correction was sub-band on this field, so the "
                "arrival_unverifiable half of this limb did NOT run"
                if not unverifiable else
                f"corrections too small to verify: {unverifiable}, recorded and "
                "not refused")
    return "PASS", (
        f"entered at {start_px:.1f} px, residuals {result.get('residuals_px')}, "
        f"centered={result.get('centered')} in {result.get('iterations')} "
        f"iteration(s); {sub_band}; smallest commanded "
        f"{result.get('smallest_correction_um')} um"
    )


@limb("E_export_carries_contract", "the exported script settles, with THIS run's bands")
def limb_export(ctrl, guard, out):
    """52b: an export gate step needs a run in front of it — limb A is that run,
    and `export_session_script` compiles THIS session's recorded calls, so a
    fresh session emits a 13-line stub.

    Scored structurally, not by substring: block 66's round 1 replaced a
    substring match after an integer target slipped through one, and this
    block's own review round found the emitted `settle_stage_move` DEFINITION
    matching an assertion meant for a CALL.
    """
    from microclaw.tools import export_session_script

    settled = out / "move_settles.json"
    if not settled.exists():
        return "NOT EXERCISED", "limb A did not run, so there is nothing to export"
    payload = json.loads(settled.read_text(encoding="utf-8"))["result"]
    records = [
        {"role": "assistant", "content": [{
            "type": "tool_use", "id": "gate68-move", "name": "move_stage_xy",
            "input": {"x_um": payload["requested_um"][0],
                      "y_um": payload["requested_um"][1], "absolute": True},
        }]},
        {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "gate68-move",
            "content": json.dumps(payload, default=str),
        }]},
    ]
    script = out / "exported_move.py"
    export_session_script(ctrl, guard, str(script), records)
    source = script.read_text(encoding="utf-8")
    if "# NOT EMITTED" in source:
        return "FAIL", "move_stage_xy did not emit"

    tree = ast.parse(source)
    settle_calls = [node for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "settle_xy_move"]
    if not settle_calls:
        return "FAIL", ("the emitted script contains no settle_xy_move CALL — a "
                        "definition alone means the move is still unverified")
    if "core.wait_for_device(core.get_xy_stage_device())" in source:
        return "FAIL", "the emitted script still waits on busy instead of settling"

    literals = [ast.literal_eval(arg) if isinstance(arg, ast.Constant) else None
                for arg in settle_calls[0].args]
    recorded_bands = {payload["x_band_source"]: payload["x_tolerance_um"],
                      payload["y_band_source"]: payload["y_tolerance_um"]}
    if payload["x_band_source"] == "configured" and payload["x_tolerance_um"] not in literals:
        return "FAIL", ("this rig declares an X band and the emitted call does "
                        f"not carry it: {literals}")
    return "PASS", (
        f"emitted {len(settle_calls)} settle_xy_move call(s); first carries "
        f"{literals}; the run's own bands were {recorded_bands}; "
        f"{len(source.splitlines())} lines, parsed"
    )


LIMBS = [limb_stage_responds, limb_move_settles, limb_per_axis_bands,
         limb_non_response, limb_centring, limb_export]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="gate68", help="evidence directory")
    parser.add_argument("--safety-config", default=None)
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--step-um", type=float, default=20.0,
                        help="ordinary move size for limbs 0 and A")
    parser.add_argument("--long-um", type=float, default=200.0,
                        help="the long-axis move limb B needs to separate the bands")
    parser.add_argument("--control", action="store_true",
                        help="run limb C: an axis is physically blocked RIGHT NOW")
    parser.add_argument("--limbs", default=None,
                        help="comma-separated limb prefixes, e.g. '0,C'. Limb 0 "
                             "always runs: every other limb is meaningless on a "
                             "stage that is not moving.")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from microclaw.authorization import RigAuthorizationError, validate_live_rig
    from microclaw.config import load_safety_config_or_exit
    from microclaw.controller import MicroscopeController
    from microclaw.safety import SafetyGuard

    parsed = load_safety_config_or_exit(args.safety_config)
    guard = SafetyGuard(parsed.constraints)
    ctrl = MicroscopeController(port=args.port, guard=guard)
    if not ctrl.is_connected():
        print("FAIL  could not connect to Micro-Manager; is the ZMQ server on?")
        return 2
    try:
        validate_live_rig(ctrl, parsed, guard=guard)
    except RigAuthorizationError as error:
        print(f"FAIL  this rig is not authorized for a session: {error}")
        return 2

    limb_stage_responds.step_um = args.step_um
    limb_per_axis_bands.long_um = args.long_um
    limb_non_response.enabled = args.control
    limb_non_response.step_um = args.step_um
    selected = None
    if args.limbs:
        selected = {piece.strip().upper() for piece in args.limbs.split(",")}
    print(f"design/68 XY arrival gate — {datetime.now().isoformat(timespec='seconds')}")
    if selected:
        print(f"partial run: {sorted(selected)} (plus limb 0, always)")
    print()
    for fn in LIMBS:
        name, mechanism = fn._limb
        if selected is not None and fn is not limb_stage_responds:
            if name.split("_")[0].upper() not in selected:
                continue
        try:
            status, detail = fn(ctrl, guard, out)
        except Exception as error:                   # one limb, one failure
            status, detail = "FAIL", f"{type(error).__name__}: {error}"
            (out / f"{name}_traceback.txt").write_text(
                traceback.format_exc(), encoding="utf-8")
        record(name, mechanism, status, detail)

    (out / "gate68_results.json").write_text(
        json.dumps(RESULTS, indent=2), encoding="utf-8")
    if selected:
        print("PARTIAL RUN — limbs not selected were not run at all, which is "
              "not the same as passing.")
    passed = sum(r["status"] == "PASS" for r in RESULTS)
    unexercised = [r["limb"] for r in RESULTS if r["status"] == "NOT EXERCISED"]
    failed = [r["limb"] for r in RESULTS if r["status"] == "FAIL"]
    print(f"{passed}/{len(RESULTS)} PASS")
    if unexercised:
        print(f"NOT EXERCISED (never a pass): {', '.join(unexercised)}")
    if failed:
        print(f"FAIL: {', '.join(failed)}")
    print(f"evidence: {out.resolve()}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
