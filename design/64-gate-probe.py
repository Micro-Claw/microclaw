"""design/64 rig probe: is the centring loop pointed the right way on THIS rig?

Every limb below only computes, so it is a program rather than a runbook step
(CLAUDE.md: pasted `throw`s end a pipeline, not a session, and block 58a
reported PASSED over five failed limbs that way). Each limb is scored
independently -- one refusal must not hide the rest behind its cascade -- and
the script owns its own log because PowerShell 5.1's Start-Transcript does not
capture a native child's stdout.

    uv run python design/64-gate-probe.py --out gate64

Limbs that could not run their mechanism report NOT EXERCISED, which is never a
pass. Exit code is 0 only when every limb PASSed.

What this does NOT settle, deliberately: the sign convention itself. That was
settled off-rig -- numerically against phase_cross_correlation, and against
Micro-Manager's own CenterAndDragListener/XYNavigator composition (design/64).
What needs a rig is whether THIS installation publishes an affine we can read
and adopt, and whether the loop converges on a real sample through real optics.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import sys
import traceback
from datetime import datetime
from pathlib import Path

RESULTS: list[dict] = []
DECOUPLED: str | None = None   # set by limb 0 when frames ignore the stage


def limb(name, mechanism):
    """Register one independently scored limb."""
    def decorate(fn):
        fn._limb = (name, mechanism)
        return fn
    return decorate


def record(name, mechanism, status, detail):
    RESULTS.append({"limb": name, "mechanism": mechanism,
                    "status": status, "detail": detail})
    print(f"[{status:<13}] {name}\n                {detail}\n", flush=True)


@limb("0_frames_follow_the_stage", "the camera images the sample the stage moves")
def limb_coupling(ctrl, guard, out):
    """Precondition. Without it, F and F2 measure nothing and would lie.

    **The demo camera cannot run the centring limbs**, in either of the two
    shapes it takes. The demo machine returns the *same image every snap*
    regardless of where the stage is (operator, 2026-08-30); in other modes the
    frame is a function of how many times you have snapped rather than of stage
    position — design/20 §S3's rotating test pattern. Either way the camera is
    not imaging the thing the stage moves, so the residual barely changes,
    limb F computes a ratio near 1.0 and reports "the stage did not arrive" — a
    confident, wrong diagnosis about hardware that is working fine. A gate that
    cries wolf on the demo machine gets its real FAILs dismissed.

    Measured, not assumed: snap two frames with NO stage motion, then one
    spanning a known move, and compare how much the pixels changed. A camera
    imaging a real sample changes far more across the move than across the still
    pair; a frozen frame changes by nothing either way; a rotating pattern
    changes as much standing still as it does moving.

    **The criterion is a whole-frame difference, deliberately not a registered
    shift.** The M2 run of 2026-08-30 scored a phase correlation, and on that
    bead field it locked onto the wrong bead: it measured (-23.3, -0.7) px for a
    +10 um X move where the affine — validated minutes later by limb F to within
    2.3% — predicts (0, -78.7). Wrong axis, 3.4x wrong magnitude. It passed only
    because 23.3 happened to clear the threshold; another field could as easily
    have returned ~0 and declared a healthy rig decoupled, which reads as a fact
    about the machine. design/29 had already measured phase correlation failing
    on this exact sample: "sparse beads give a correlator few features to lock
    onto, and it can lock onto the wrong one". The shift is still reported, as
    information, and never scored.
    """
    global DECOUPLED
    import numpy as np
    from skimage.registration import phase_cross_correlation

    from microclaw.tools import snap_to_numpy

    step_um = float(getattr(limb_coupling, "step_um", 10.0))
    entry = (ctrl.core.get_x_position(), ctrl.core.get_y_position())
    guard.check_xy(entry[0] + step_um, entry[1])

    def change(a, b):
        """Normalised RMS difference — no peak finding, nothing to mislock."""
        a = a.astype(np.float64)
        b = b.astype(np.float64)
        scale = float(np.std(a)) or 1.0
        return float(np.sqrt(np.mean((a - b) ** 2))) / scale

    first = snap_to_numpy(ctrl)
    second = snap_to_numpy(ctrl)
    still_change = change(first, second)

    from microclaw.tools import move_stage_xy
    move_stage_xy(ctrl, guard, step_um, 0.0, absolute=False)
    try:
        moved_frame = snap_to_numpy(ctrl)
    finally:
        move_stage_xy(ctrl, guard, -step_um, 0.0, absolute=False)
    moved_change = change(first, moved_frame)

    # Reported, never scored: see the docstring.
    shift, _, _ = phase_cross_correlation(first, moved_frame, upsample_factor=10)
    identical = bool(np.array_equal(first, moved_frame))
    (out / "coupling.json").write_text(json.dumps({
        "step_um": step_um,
        "change_without_moving": still_change,
        "change_after_moving": moved_change,
        "frames_byte_identical": identical,
        "registered_shift_dy_dx_px_UNSCORED": [float(shift[0]), float(shift[1])],
    }, indent=2), encoding="utf-8")

    if identical or moved_change < 1e-9:
        DECOUPLED = (
            f"a {step_um} um stage move changed the frame by "
            f"{moved_change:.4f} (byte-identical: {identical})"
        )
        return "NOT EXERCISED", (
            DECOUPLED + ". A camera that returns the same image wherever the "
            "stage is, is the demo camera — the centring limbs cannot run here, "
            "and that says nothing about the code."
        )
    if moved_change < 2.0 * still_change:
        DECOUPLED = (
            f"moving {step_um} um changed the frame by {moved_change:.3f} but "
            f"two frames with NO motion already differ by {still_change:.3f}"
        )
        return "NOT EXERCISED", (
            DECOUPLED + ". The pixels are not tracking the stage — a frame "
            "driven by snap count rather than position (design/20 §S3), a stage "
            "that is not moving, or a step too small for this magnification."
        )
    return "PASS", (
        f"frame change {still_change:.3f} still vs {moved_change:.3f} after a "
        f"{step_um} um move — the pixels follow the stage (registered shift, "
        f"unscored: {math.hypot(float(shift[0]), float(shift[1])):.1f} px)"
    )


@limb("A_affine_readable", "core.get_pixel_size_affine() via _strings, NOT list()")
def limb_affine_readable(ctrl, guard, out):
    """design/59a: a list() over a Core collection passes every fake, fails every rig."""
    from microclaw.authorization import _strings

    raw_vector = ctrl.core.get_pixel_size_affine()
    try:
        iter(raw_vector)
        iterable = True
    except TypeError:
        iterable = False
    values = _strings(raw_vector)
    if not values:
        return "NOT EXERCISED", (
            "core.get_pixel_size_affine() returned nothing this probe could read; "
            "MM may have no pixel-size config selected. Limbs B and D cannot run."
        )
    return "PASS", (f"read {len(values)} values via _strings "
                    f"(python-iterable: {iterable}); raw={';'.join(values)}")


@limb("B_affine_resolution", "_resolve_current_affine: MM adopted, or ours used")
def limb_affine_resolution(ctrl, guard, out):
    from microclaw.calibration import MEASURED_AFFINE_SOURCE, MM_AFFINE_SOURCE
    from microclaw.tools import _resolve_current_affine

    affine, report = _resolve_current_affine(ctrl)
    (out / "affine_resolution.json").write_text(json.dumps(
        {"report": report,
         "affine": None if affine is None else
                   {"a": affine.a, "b": affine.b, "c": affine.c, "d": affine.d,
                    "pixel_size_um": affine.pixel_size_um}},
        indent=2, default=str), encoding="utf-8")
    source = report.get("calibration_source")
    if affine is None:
        return "NOT EXERCISED", (
            "Neither MM nor the knowledge base has a calibration for this "
            "objective/binning. Run calibrate_stage_to_camera, then rerun."
        )
    note = f" NOTE: {report['calibration_note'][:90]}..." if "calibration_note" in report else ""
    if source == MM_AFFINE_SOURCE:
        return "PASS", (
            f"MM's PixelSizeAffine is the authority here "
            f"(adopted this run: {report.get('adopted_from_micro_manager', False)}); "
            f"det={affine.a * affine.d - affine.b * affine.c:+.6f}{note}"
        )
    if source == MEASURED_AFFINE_SOURCE:
        differs = report.get("micro_manager_affine_differs")
        return "PASS", (
            "a local calibrate_stage_to_camera measurement is in use"
            + (f"; MM disagrees: {differs['micro_manager']} vs {differs['in_use']}"
               if differs else "; MM publishes none or the same")
        )
    return "FAIL", f"unrecognised calibration_source {source!r}"


@limb("C_adoption_reached_the_kb", "the affine center_feature reads is persisted")
def limb_adoption_persisted(ctrl, guard, out):
    """The user's requirement: MM's affine must BE in the KB before centring."""
    from microclaw.calibration import load_affine_entry
    from microclaw.tools import _current_binning, _current_objective

    objective = _current_objective(ctrl) or ""
    binning = _current_binning(ctrl)
    stored, identity = load_affine_entry(objective, binning)
    if stored is None:
        return "FAIL", (
            f"nothing cached for objective={objective!r} binning={binning}; "
            "_resolve_current_affine did not persist what it resolved"
        )
    return "PASS", (
        f"knowledge base holds [{stored.a:+.5f} {stored.b:+.5f}; "
        f"{stored.c:+.5f} {stored.d:+.5f}] for objective={objective!r} "
        f"binning={binning}, source={identity.get('source')!r}, "
        f"camera={identity.get('camera_model')!r}"
    )


@limb("D_detector_names_a_target", "find_features reports brightest_feature_offset_px")
def limb_detector(ctrl, guard, out):
    from microclaw.tools import find_features

    result = find_features(ctrl, guard)
    (out / "find_features.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8")
    for key in ("brightest_feature_xy_px", "brightest_feature_offset_px"):
        if key not in result:
            return "FAIL", f"find_features did not report {key}"
    if result["n_spots"] == 0:
        return "NOT EXERCISED", (
            "no puncta in this field, so the centring target is null by design. "
            "Move to a field with something in it and rerun; the CONVERGENCE "
            "limb cannot run either."
        )
    if result["brightest_feature_offset_px"] is None:
        return "FAIL", f"n_spots={result['n_spots']} but no brightest target"
    return "PASS", (
        f"n_spots={result['n_spots']}, brightest at "
        f"{result['brightest_feature_xy_px']}, offset "
        f"{result['brightest_feature_offset_px']} px, "
        f"centering_move_um={result.get('centering_move_um')}"
    )


@limb("E_no_puncta_no_motion", "center_feature refuses before moving")
def limb_refusal_control(ctrl, guard, out):
    """The control that must FIRE. A limb that cannot fail is not a criterion
    (block 58a's opt-out limb passed three rounds without ever being checked),
    so this one deliberately makes detection impossible and requires a refusal
    AND an unmoved stage."""
    from microclaw import tools

    before = (ctrl.core.get_x_position(), ctrl.core.get_y_position())
    real_snap = tools.snap_to_numpy
    try:
        import numpy as np
        frame = real_snap(ctrl)
        ramp = np.tile(
            np.linspace(400, 3000, frame.shape[1], dtype=np.float32),
            (frame.shape[0], 1),
        ).astype(frame.dtype)
        tools.snap_to_numpy = lambda _ctrl: ramp
        result = tools.center_feature(ctrl, guard)
    finally:
        tools.snap_to_numpy = real_snap
    after = (ctrl.core.get_x_position(), ctrl.core.get_y_position())
    moved = math.hypot(after[0] - before[0], after[1] - before[1])
    if "No detected feature to centre" not in result.get("error", ""):
        return "FAIL", f"a gradient did not refuse; got {result}"
    if moved > 0.5:
        return "FAIL", f"refused but the stage moved {moved:.2f} um"
    return "PASS", (f"a gradient (signal, no puncta) refused and the stage "
                    f"stayed put (moved {moved:.3f} um)")


@limb("F_one_correction", "a SINGLE correction moves the feature toward centre")
def limb_one_correction(ctrl, guard, out):
    """The limb the rig trip exists for, and it scores ONE correction.

    An earlier version asked only that the residual be smaller after the whole
    loop. Its own dry run passed with design/64's defect restored — the loop
    flailed for four iterations and landed 4% closer by luck (47.2 -> 45.3). A
    criterion a broken mechanism can satisfy is not a criterion.

    One correction is the measurement with teeth, because the arithmetic is
    unambiguous: on a linear system the right move lands near zero, and the
    wrong sign lands at exactly twice the offset. The RATIO is the diagnosis, so
    it is reported whatever the verdict.
    """
    from microclaw.tools import center_feature, find_features

    if DECOUPLED is not None:
        return "NOT EXERCISED", (
            "skipped because limb 0 measured that frames do not follow the "
            f"stage: {DECOUPLED}. A residual measured here would diagnose "
            "hardware from a camera that is not watching it."
        )
    before = find_features(ctrl, guard)
    if before.get("brightest_feature_offset_px") is None:
        return "NOT EXERCISED", "no punctum to centre in this field"
    start = math.hypot(*before["brightest_feature_offset_px"])
    if start <= 8.0:
        return "NOT EXERCISED", (
            f"the feature is already centred ({start:.1f} px). Move the stage "
            "10-20 um off it and rerun — a correction this small measures noise."
        )
    entry = (ctrl.core.get_x_position(), ctrl.core.get_y_position())

    # tol_px=0 so the loop cannot decide it is already done and skip the move.
    result = center_feature(ctrl, guard, max_iter=1, tol_px=0.0)
    after = find_features(ctrl, guard)
    exit_xy = (ctrl.core.get_x_position(), ctrl.core.get_y_position())
    end = (math.hypot(*after["brightest_feature_offset_px"])
           if after.get("brightest_feature_offset_px") is not None else None)
    ratio = None if end is None else end / start
    (out / "one_correction.json").write_text(json.dumps({
        "before": before, "center_feature": result, "after": after,
        "residual_px_before": start, "residual_px_after": end,
        "residual_ratio": ratio,
        "stage_entry_um": list(entry), "stage_exit_um": list(exit_xy),
        "stage_move_um": [exit_xy[0] - entry[0], exit_xy[1] - entry[1]],
    }, indent=2, default=str), encoding="utf-8")

    if end is None:
        return "FAIL", (
            f"the punctum left the field on one correction from {start:.1f} px. "
            "That is what a sign error looks like on a rig with no wrap-around."
        )
    diagnosis = ("~2.0 means the correction was applied with the wrong sign; "
                 "~1.0 means the stage did not arrive; >1 either way is wrong")
    if ratio > 0.6:
        return "FAIL", (
            f"residual {start:.1f} -> {end:.1f} px, ratio {ratio:.2f}. Expected "
            f"well under 0.6 for one correction. {diagnosis}"
        )
    return "PASS", (
        f"residual {start:.1f} -> {end:.1f} px in ONE correction, ratio "
        f"{ratio:.2f}; stage moved "
        f"{math.hypot(exit_xy[0] - entry[0], exit_xy[1] - entry[1]):.2f} um"
    )


@limb("F2_convergence", "the loop reaches tolerance on a real sample")
def limb_convergence(ctrl, guard, out):
    """Separate from F on purpose: converging and moving the right way are two
    claims, and a gate that merges them can pass on either."""
    from microclaw.tools import (_resolve_current_affine, center_feature,
                                 find_features, move_stage_xy)

    if DECOUPLED is not None:
        return "NOT EXERCISED", (
            "skipped because limb 0 measured that frames do not follow the "
            f"stage: {DECOUPLED}. A residual measured here would diagnose "
            "hardware from a camera that is not watching it."
        )
    affine, _ = _resolve_current_affine(ctrl)
    if affine is None:
        return "NOT EXERCISED", "no calibration to displace by; see limb B"
    seed = find_features(ctrl, guard)
    if seed.get("brightest_feature_offset_px") is None:
        return "NOT EXERCISED", "no punctum to centre in this field"

    # Displace the feature FIRST. On M2 (2026-08-30) this limb ran after F had
    # already centred the field, entered at 3.9 px against tol_px=5.0, returned
    # in ZERO iterations and reported PASS — a limb that cannot fail is not a
    # criterion (CLAUDE.md; block 58a's opt-out limb passed three rounds the
    # same way). Moving by -A·r is the inverse of the centring move, so it puts
    # the punctum at a known offset; the offset is then re-measured rather than
    # assumed, so a wrong affine cannot hide here either.
    displacement_px = (0.0, 45.0)
    dx_um, dy_um = affine.px_to_um(*displacement_px)
    entry = (ctrl.core.get_x_position(), ctrl.core.get_y_position())
    guard.check_xy(entry[0] - dx_um, entry[1] - dy_um)
    move_stage_xy(ctrl, guard, -dx_um, -dy_um, absolute=False)

    before = find_features(ctrl, guard)
    if before.get("brightest_feature_offset_px") is None:
        return "NOT EXERCISED", (
            f"displacing by {displacement_px} px pushed the punctum out of the "
            "field; use a smaller displacement or a more central feature"
        )
    start = math.hypot(*before["brightest_feature_offset_px"])
    if start <= 5.0:
        return "NOT EXERCISED", (
            f"the displacement left the punctum {start:.1f} px from centre, "
            "inside tol_px, so the loop would not have to do anything"
        )
    result = center_feature(ctrl, guard, max_iter=4, tol_px=5.0)
    after = find_features(ctrl, guard)
    end = (math.hypot(*after["brightest_feature_offset_px"])
           if after.get("brightest_feature_offset_px") is not None else None)
    (out / "convergence.json").write_text(json.dumps({
        "seed": seed, "displacement_px": list(displacement_px),
        "displacement_move_um": [-dx_um, -dy_um],
        "before": before, "center_feature": result, "after": after,
        "residual_px_before": start, "residual_px_after": end,
        "iterations": result.get("iterations"),
    }, indent=2, default=str), encoding="utf-8")

    if result.get("error"):
        return "FAIL", f"center_feature reported: {result['error']}"
    if not result.get("iterations"):
        return "FAIL", (
            f"center_feature returned in {result.get('iterations')} iterations "
            f"from {start:.1f} px — the loop never ran, so this limb measured "
            "nothing"
        )
    if not result.get("centered"):
        return "FAIL", (
            f"did not reach tolerance in {result.get('iterations')} iterations; "
            f"residual {start:.1f} -> {end} px"
        )
    if end is None or end > 5.0:
        return "FAIL", (
            f"center_feature reported centered=True but a fresh find_features "
            f"measures {end} px, which is outside tol_px=5.0"
        )
    return "PASS", (f"residual {start:.1f} -> {end:.1f} px in "
                    f"{result['iterations']} iteration(s), independently "
                    f"re-measured after the loop")


@limb("G_export_matches", "the exported script carries the same unnegated affine")
def limb_export(ctrl, guard, out):
    """52b: an export gate step needs a run in front of it — F is that run."""
    from microclaw.tools import export_session_script

    convergence = out / "convergence.json"
    if not convergence.exists():
        return "NOT EXERCISED", "limb F did not run, so there is nothing to export"
    payload = json.loads(convergence.read_text(encoding="utf-8"))
    # export_session_script compiles THIS session's recorded calls from the
    # conversation transcript, so the gate hands it a transcript of the run limb
    # F just made. A fresh session emits a 13-line stub (block 52b).
    records = [
        {"role": "assistant", "content": [{
            "type": "tool_use", "id": "gate64-center", "name": "center_feature",
            "input": {"max_iter": 4, "tol_px": 5.0},
        }]},
        {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "gate64-center",
            "content": json.dumps(payload["center_feature"], default=str),
        }]},
    ]
    script = out / "exported_center.py"
    export_session_script(ctrl, guard, str(script), records)
    source = script.read_text(encoding="utf-8")
    if "# NOT EMITTED" in source or "RuntimeError('# NOT EMITTED" in source:
        return "FAIL", "center_feature did not emit"

    # Structural, not substring. A substring cannot tell a negation rendered
    # upstream from one rendered here, and block 66's round 1 replaced exactly
    # this kind of match after an integer target slipped through one. Find the
    # call and read its actual arguments.
    tree = ast.parse(source)
    relative = [node for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "set_relative_xy_position"]
    if not relative:
        return "FAIL", "the exported script issues no relative XY correction"
    args = relative[0].args
    if not all(isinstance(arg, ast.Name) for arg in args):
        return "FAIL", (
            "the emitted correction is not the two affine terms as computed; "
            f"got {[ast.dump(a) for a in args]} — a negation or rescale here is "
            "the design/64 defect returning by another spelling"
        )
    names = [arg.id for arg in args]
    if names != ["_center_dx_um", "_center_dy_um"]:
        return "FAIL", f"the emitted correction passes {names}, not the affine result"
    if "brightest_feature_offset_px" not in source:
        return "FAIL", "the exported loop still steers by the aggregate centroid"

    # Added 2026-08-31 by block 64d: the live centring correction is now settled
    # per axis, so an exported one that merely dispatches is LOOSER than the tool
    # it reproduces. Before 64d this limb could not have asked for it; after it,
    # a regression that dropped the contract from the centring emitter would
    # otherwise leave this gate green.
    settles = [node for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Name)
               and node.func.id == "settle_xy_move"]
    if not settles:
        return "FAIL", (
            "the emitted correction is dispatched but never settled. The live "
            "center_feature waits for each correction to arrive (design/68); an "
            "exported step must not be looser than the tool it reproduces."
        )
    coefficients = payload["center_feature"].get("affine_coefficients", {})
    missing = [f"{v!r}" for v in coefficients.values() if repr(v) not in source]
    if missing:
        return "FAIL", f"emitted affine does not carry the coefficients used: {missing}"
    return "PASS", (f"{script.name} emits the session's own coefficients "
                    f"{list(coefficients.values())}, unnegated, brightest-punctum "
                    f"target, and settles each correction "
                    f"({len(settles)} settle_xy_move call(s))")


LIMBS = [limb_coupling, limb_affine_readable, limb_affine_resolution,
         limb_adoption_persisted,
         limb_detector, limb_refusal_control, limb_one_correction,
         limb_convergence, limb_export]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="gate64", help="evidence directory")
    parser.add_argument("--safety-config", default=None)
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--step-um", type=float, default=10.0,
                        help="stage step for the coupling precondition (limb 0)")
    parser.add_argument("--limbs", default=None,
                        help="comma-separated limb prefixes to run, e.g. '0,F2'. "
                             "A re-run after a fix should not cost a full gate; "
                             "limb 0 always runs, because F and F2 are unsafe to "
                             "score without it.")
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

    limb_coupling.step_um = args.step_um
    selected = None
    if args.limbs:
        selected = {piece.strip().upper() for piece in args.limbs.split(",")}
    print(f"design/64 centring gate — {datetime.now().isoformat(timespec='seconds')}")
    if selected:
        print(f"partial run: {sorted(selected)} (plus limb 0, always)")
    print()
    for fn in LIMBS:
        name, mechanism = fn._limb
        if selected is not None and fn is not limb_coupling:
            if name.split("_")[0].upper() not in selected:
                continue
        try:
            status, detail = fn(ctrl, guard, out)
        except Exception as error:                       # one limb, one failure
            status, detail = "FAIL", f"{type(error).__name__}: {error}"
            (out / f"{name}_traceback.txt").write_text(
                traceback.format_exc(), encoding="utf-8")
        record(name, mechanism, status, detail)

    (out / "gate64_results.json").write_text(
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
