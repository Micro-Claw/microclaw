#!/usr/bin/env python
"""Block 55a gate probe -- drive the exact refused call shapes at a live rig.

WHY A PROBE AND NOT A TYPED PROMPT. The call this block exists to refuse is one
no agent will ever compose on purpose: a `hook_action_plan` with no
`hook_strategy`. Ask an agent for a per-frame sweep and it correctly reaches for
a hook, the refusal never fires, and the step "passes" having tested nothing --
that is 52b's mandatory limb, which asked for an outcome and got the better
route. So the shapes are issued here, literally, against the same
MicroscopeController and SafetyGuard a session builds.

WHAT IT DOES TO THE RIG. Every case below is expected to REFUSE, so on a fixed
tree nothing moves and nothing is exposed. Two reads bracket every case -- the
named stage's position and the camera's exposure -- and a change in either is a
FAIL, not a note. The last case is the control: one ordinary two-frame timelapse
that must still run and save, because a guard that refuses everything is also a
regression. On the demo machine that is a simulated camera and a simulated
stage; on M2 or M5 the control case is a real two-frame dose, so run it there
only with the exposure you would use for a throwaway field.

It writes nothing to Micro-Manager on any exit path.

PowerShell:

    uv run python design\\55-block55a-probe.py --device "Aux Z" --min 0 --max 200 `
        --save-root "$Evidence\\probe" > "$Evidence\\55a-probe.txt" 2>&1
    Write-Output "probe exit code (0 = every case as expected):" $LASTEXITCODE

Exit status is 0 only when every case behaved as stated. Read the printed table
either way; a nonzero exit with a readable table is the useful outcome, a
traceback is not.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import traceback
from pathlib import Path

from microclaw import tools
from microclaw.authorization import RigAuthorizationError, validate_live_rig
from microclaw.config import load_safety_config_or_exit
from microclaw.controller import MicroscopeController
from microclaw.safety import SafetyGuard


def plan(*targets: float) -> list[dict]:
    return [{"hook_event_index": i,
             "actions": [{"kind": "MoveNamedStage", "position_um": float(t)}]}
            for i, t in enumerate(targets)]


def check_precondition(ctrl, guard, args) -> int:
    """Prove the named stage exists and carries exactly the stated bound.

    Behavioural on purpose: it asks the same `check_named_stage` the tools ask,
    rather than reading `guard._c.named_stages`. A stage with no entry fails
    closed here for the same reason it would fail closed mid-run.
    """
    failures = 0
    try:
        position = float(ctrl.core.get_position(args.device))
    except Exception as exc:
        print(f"FAIL  no stage labelled {args.device!r} on this rig: {exc}")
        return 1
    print(f"device            : {args.device}")
    print(f"current position  : {position} um")
    for value, must_pass in ((args.min, True), (args.max, True),
                             (args.min - 10.0, False), (args.max + 10.0, False)):
        try:
            guard.check_named_stage(args.device, value)
        except Exception as exc:
            ok = not must_pass
            print(f"  {value:>12} um : REFUSED  {exc}")
        else:
            ok = must_pass
            print(f"  {value:>12} um : allowed")
        if not ok:
            failures += 1
            print("    ^^ FAIL -- the configured bound is not "
                  f"{args.min} .. {args.max}")
    print("")
    print("PRECONDITION PASS" if failures == 0 else "PRECONDITION FAIL")
    return 0 if failures == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", required=True,
                    help="named stage label, e.g. 'Aux Z' on the demo machine")
    ap.add_argument("--min", type=float, required=True, help="envelope min_um")
    ap.add_argument("--max", type=float, required=True, help="envelope max_um")
    ap.add_argument("--save-root", default=None,
                    help="directory the probe may create dataset dirs under "
                         "(required unless --check-only)")
    ap.add_argument("--check-only", action="store_true",
                    help="verify the precondition and exit; touches no tool")
    ap.add_argument("--exposure-ms", type=float, default=10.0,
                    help="exposure for the control acquisition only (default 10)")
    ap.add_argument("--port", type=int, default=4827)
    ap.add_argument("--safety-config", default=None)
    args = ap.parse_args()
    if not args.check_only and not args.save_root:
        ap.error("--save-root is required unless --check-only")

    parsed = load_safety_config_or_exit(args.safety_config)
    guard = SafetyGuard(parsed.constraints)
    ctrl = MicroscopeController(port=args.port, guard=guard)
    if not ctrl.is_connected():
        print("FAIL  could not connect to Micro-Manager; is the ZMQ server on?")
        return 2
    try:
        validate_live_rig(ctrl, parsed, guard=guard)
    except RigAuthorizationError as exc:
        print(f"FAIL  this rig is not authorized for a session: {exc}")
        return 2

    if args.check_only:
        return check_precondition(ctrl, guard, args)

    # Confirmation must never be reached by a refused call. If one gets this far
    # the operator would be asked to approve an envelope for a run that cannot
    # legally exist, so answer no and record it.
    approvals: list[str] = []

    def refuse_confirm(summary, kind=None):
        approvals.append(f"{kind}: {summary.splitlines()[0]}")
        return False

    tools.CONFIRM_FN = refuse_confirm

    lo, hi = args.min, args.max
    mid = (lo + hi) / 2.0
    envelope = {"device": args.device, "min_um": lo, "max_um": hi,
                "max_writes": 4, "restore": "entry"}

    # The message each case must refuse WITH. A refusal for the wrong reason is
    # not evidence -- block 9a's gate shipped a test whose fake died on a missing
    # file before it ever reached the refusal under test. Case A must hit the
    # plan-time sequencing check; every other case must hit the no-hook check and
    # name the argument that would fix it.
    SEQUENCING = "hardware-sequence the time axis"
    NO_HOOK = "pass hook_strategy"

    # Each refused case asks for an exposure DIFFERENT from the one before it.
    # If any refusal lands after `core.set_exposure`, the bracketing read at the
    # end disagrees with the entry value and the case fails on that alone.
    cases = [
        ("A  line-89 shape, interval_s=0", "timelapse", SEQUENCING, dict(
            n_frames=3, interval_s=0, exposure_ms=101.0,
            named_stage_envelope=envelope, hook_action_plan=plan(lo, mid, hi))),
        ("B  line-89 shape, interval_s=2", "timelapse", NO_HOOK, dict(
            n_frames=3, interval_s=2, exposure_ms=102.0,
            named_stage_envelope=envelope, hook_action_plan=plan(lo, mid, hi))),
        ("C  run_zstack, envelope + plan", "zstack", NO_HOOK, dict(
            exposure_ms=103.0,
            named_stage_envelope=envelope, hook_action_plan=plan(lo, mid))),
        ("D  hook_action_plan alone", "timelapse", NO_HOOK, dict(
            n_frames=2, interval_s=2, exposure_ms=104.0,
            hook_action_plan=plan(lo, hi))),
        ("E  named_stage_envelope alone", "timelapse", NO_HOOK, dict(
            n_frames=2, interval_s=2, exposure_ms=105.0,
            named_stage_envelope=envelope)),
        ("F  property_envelope alone", "timelapse", NO_HOOK, dict(
            n_frames=2, interval_s=2, exposure_ms=106.0,
            property_envelope={"device": args.device, "property": "Position",
                               "min": lo, "max": hi, "max_writes": 2,
                               "restore": "entry"})),
        ("G  artifact_limits alone", "timelapse", NO_HOOK, dict(
            n_frames=2, interval_s=2, exposure_ms=107.0,
            artifact_limits={"max_artifact_bytes": 1024, "max_count": 1,
                             "max_total_bytes": 1024})),
        ("H  illumination_envelope alone", "timelapse", NO_HOOK, dict(
            n_frames=2, interval_s=2, exposure_ms=108.0,
            illumination_envelope={"device": "nonexistent-for-this-probe",
                                   "property": "Power", "max_power_percent": 1.0,
                                   "max_writes": 1})),
        ("I  empty {} named_stage_envelope", "timelapse", NO_HOOK, dict(
            n_frames=2, interval_s=2, exposure_ms=109.0,
            named_stage_envelope={})),
    ]

    save_root = Path(args.save_root)
    save_root.mkdir(parents=True, exist_ok=True)

    entry_position = float(ctrl.core.get_position(args.device))
    entry_exposure = float(ctrl.core.get_exposure())
    entry_z = float(ctrl.core.get_position())
    print(f"device            : {args.device}")
    print(f"entry position um : {entry_position}")
    print(f"entry exposure ms : {entry_exposure}")
    print(f"core focus z um   : {entry_z}  (run_zstack case C sweeps nothing "
          f"from here, so check_z cannot refuse for the wrong reason)")
    print(f"envelope          : {lo} .. {hi} um, targets {lo} / {mid} / {hi}")
    print("")

    failures = 0
    for label, kind, expect, kwargs in cases:
        name = label.split()[0]
        save_dir = save_root / f"case_{name}"
        try:
            if kind == "timelapse":
                result = tools.run_timelapse(ctrl, guard, save_dir=str(save_dir),
                                             name=f"case_{name}", **kwargs)
            else:
                result = tools.run_zstack(ctrl, guard, z_start_um=entry_z,
                                          z_end_um=entry_z,
                                          z_step_um=1.0, save_dir=str(save_dir),
                                          name=f"case_{name}", **kwargs)
        except Exception as exc:                       # the refusal we want
            message = str(exc)
            outcome = f"REFUSED  {type(exc).__name__}: {message}"
            refused = True
        else:
            message = ""
            outcome = f"RAN      {result}"
            refused = False
        created = save_dir.exists()
        right_reason = expect in message
        print(f"{label}\n    {outcome}\n    dataset dir created: {created}")
        print(f"    refused for the right reason (expects {expect!r}): {right_reason}")
        if not refused or created or not right_reason:
            failures += 1
            print("    ^^ FAIL")
        print("")

    after_position = float(ctrl.core.get_position(args.device))
    after_exposure = float(ctrl.core.get_exposure())
    print(f"position after the refused cases : {after_position} "
          f"({'unchanged' if after_position == entry_position else 'CHANGED -- FAIL'})")
    print(f"exposure after the refused cases : {after_exposure} "
          f"({'unchanged' if after_exposure == entry_exposure else 'CHANGED -- FAIL'})")
    if after_position != entry_position or after_exposure != entry_exposure:
        failures += 1
    if approvals:
        failures += 1
        print("FAIL  a refused call reached CONFIRM_FN:")
        for line in approvals:
            print(f"    {line}")
    print("")

    # The control. A guard that refuses everything is also a regression, and the
    # reorder this block makes is in the preamble every ordinary run walks.
    control_dir = save_root / "control"
    try:
        control = tools.run_timelapse(ctrl, guard, n_frames=2, interval_s=1,
                                      exposure_ms=args.exposure_ms,
                                      save_dir=str(control_dir), name="control")
    except Exception:
        failures += 1
        print("J  control plain timelapse\n    FAIL -- it raised:")
        traceback.print_exc()
    else:
        ok = "dataset_path" in control and Path(control["dataset_path"]).exists()
        print(f"J  control plain timelapse\n    {control}\n    dataset on disk: {ok}")
        if not ok:
            failures += 1
            print("    ^^ FAIL")
        control_exposure = float(ctrl.core.get_exposure())
        print(f"    exposure after the control run: {control_exposure} "
              f"(expected {args.exposure_ms}; the control is the one call that "
              f"SHOULD set it)")
        if control_exposure != args.exposure_ms:
            failures += 1
            print("    ^^ FAIL")

    print("")
    print(f"CASES FAILED: {failures}")
    print("PROBE PASS" if failures == 0 else "PROBE FAIL")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(3)
