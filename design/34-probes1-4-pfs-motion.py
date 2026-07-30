"""Mutates PFS and may command bounded TIZDrive motion near the sample.

Cases 1-4 share one file deliberately so ceiling, step, travel-budget, status,
and abort enforcement cannot diverge. The probe forces PFS Off on cleanup and
verifies that safer state by read-back; it does not restore Z. The panel case
observes one operator-commanded Studio move and never claims which API made it.
"""
from __future__ import annotations

import argparse
import sys
import time
from functools import partial
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nikon_kit_common as kit

PROBE = "probes1-4-pfs-motion"
ALLOWED = {"Locked in focus", "Focus lock failed", "Out of focus search range"}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    kit.add_common_arguments(p)
    p.add_argument("--case", required=True,
                   choices=("absolute", "relative", "panel", "api-vs-property"))
    p.add_argument("--z-ceiling", required=True, type=kit.finite,
                   help="hard ceiling for this run (um)")
    p.add_argument("--confirmed-z-ceiling", required=True, type=kit.finite,
                   help="operator-confirmed maximum ceiling (um)")
    p.add_argument("--z-minimum", required=True, type=kit.finite)
    p.add_argument("--step", required=True, type=kit.finite)
    p.add_argument("--max-step", required=True, type=kit.positive)
    p.add_argument("--travel-budget", required=True, type=kit.positive)
    p.add_argument("--outcome-tolerance", required=True, type=kit.positive,
                   help="maximum Z error used to classify completed/rejected moves (um)")
    p.add_argument("--observe-seconds", type=kit.positive, default=10)
    p.add_argument("--dry-run", action="store_true")
    return p


def validate_plan(a, current=None):
    if a.z_ceiling > a.confirmed_z_ceiling:
        raise kit.Refusal(f"REFUSED: --z-ceiling {a.z_ceiling} is above operator-confirmed "
                          f"--confirmed-z-ceiling {a.confirmed_z_ceiling}.")
    if a.z_minimum > a.z_ceiling:
        raise kit.Refusal("REFUSED: --z-minimum is above --z-ceiling.")
    if abs(a.step) > a.max_step:
        raise kit.Refusal(f"REFUSED: absolute --step {abs(a.step)} exceeds "
                          f"--max-step {a.max_step}.")
    if abs(a.step) > a.travel_budget:
        raise kit.Refusal("REFUSED: --step exceeds --travel-budget.")
    if current is not None and not a.z_minimum <= current + a.step <= a.z_ceiling:
        raise kit.Refusal("REFUSED: planned target is outside the confirmed Z envelope.")


def dry(a):
    validate_plan(a)
    print("DRY RUN ONLY — no Micro-Manager connection and no hardware commands.")
    print(f"Case: {a.case}")
    print("Would validate TIZDrive/TIPFSStatus/TIPFSOffset and current Z.")
    print("Would abort if any measured Z is outside "
          f"[{a.z_minimum}, {a.z_ceiling}] um or Status is unexpected.")
    if a.case == "absolute":
        print(f"Would command set_position('TIZDrive', current_z + {a.step}).")
    elif a.case == "relative":
        print(f"Would command set_relative_position('TIZDrive', {a.step}).")
    elif a.case == "panel":
        print(f"Would observe one Stage Control move of exactly {a.step} um; the script "
              f"cannot stop the GUI move, so stop before approaching {a.z_ceiling} um.")
    else:
        print("Would compare enable_continuous_focus(True) with "
              "TIPFSStatus.State='On'; no commanded Z move.")
    print(f"Would classify movement with --outcome-tolerance {a.outcome_tolerance} um.")
    print("Would force TIPFSStatus.State Off and verify read-back on cleanup.")


def check_status(point):
    if point["status"] not in ALLOWED:
        raise kit.Refusal(
            f"ABORTED: unexpected TIPFSStatus.Status {point['status']!r}.")


def checked_observation(core, started, guard):
    point = kit.observation(core, started)
    check_status(point)
    guard.check_position(point["z_um"])
    return point


def watch(core, started, seconds, guard):
    out = []
    while time.monotonic() - started < seconds:
        out.append(checked_observation(core, started, guard))
        time.sleep(.1)
    out.append(checked_observation(core, started, guard))
    return out


def body(core, payload, log, options):
    current = float(core.get_position("TIZDrive"))
    validate_plan(options, current)
    guard = kit.MotionGuard(
        core, options.z_ceiling, options.confirmed_z_ceiling,
        options.z_minimum, options.max_step, options.travel_budget)
    guard.check_position(current)
    target = current + options.step
    initial_status = str(core.get_property("TIPFSStatus", "Status"))
    if initial_status not in ALLOWED:
        raise kit.Refusal(
            f"REFUSED: unexpected initial TIPFSStatus.Status {initial_status!r}.")
    transitions = []
    if options.case == "api-vs-property":
        for mechanism in ("continuous-focus-api", "State-property"):
            core.set_property("TIPFSStatus", "State", "Off")
            armed = time.monotonic()
            if mechanism == "continuous-focus-api":
                core.enable_continuous_focus(True)
            else:
                core.set_property("TIPFSStatus", "State", "On")
            transitions.append({
                "mechanism": mechanism,
                "observations": watch(core, armed, options.observe_seconds, guard),
            })
    else:
        core.set_property("TIPFSStatus", "State", "On")
        armed = time.monotonic()
        before = checked_observation(core, armed, guard)
        guard.approve(current, target)
        if options.case == "absolute":
            core.set_position("TIZDrive", target)
        elif options.case == "relative":
            core.set_relative_position("TIZDrive", options.step)
        else:
            print(f"During the next {options.observe_seconds} seconds, use Stage Control "
                  f"once to move TIZDrive by exactly {options.step} um. The absolute "
                  f"Z ceiling is {options.z_ceiling} um. The script cannot stop a GUI "
                  "move: stop manually before approaching that ceiling.")
        observations = watch(core, armed, options.observe_seconds, guard)
        after_z = float(core.get_position("TIZDrive"))
        guard.check_position(after_z)
        delta = after_z - current
        tolerance = options.outcome_tolerance
        if abs(delta - options.step) <= tolerance:
            outcome = "completed"
        elif abs(delta) <= tolerance:
            outcome = "rejected"
        else:
            outcome = "modified by servo"
        transitions.append({
            "before": before,
            "observations": observations,
            "after_z_um": after_z,
            "measured_delta_um": delta,
            "outcome": outcome,
            "outcome_tolerance_um": tolerance,
        })
    payload.update({
        "case": options.case,
        "initial_z_um": current,
        "planned_target_um": target,
        "transitions": transitions,
        "safety": {
            "ceiling": options.z_ceiling,
            "minimum": options.z_minimum,
            "max_step": options.max_step,
            "travel_budget": options.travel_budget,
            "outcome_tolerance_um": options.outcome_tolerance,
            "measured_z_checked_on_every_observation": True,
        },
    })
    log.append(
        f"Case {options.case} completed; see JSON for transition timing and outcome.")

if __name__ == "__main__":
    args = parser().parse_args()
    try:
        if args.dry_run:
            dry(args)
            raise SystemExit(0)
        probe_body = partial(body, options=args)
        raise SystemExit(kit.run_probe(
            PROBE, Path(__file__), args, probe_body, kit.force_pfs_off))
    except kit.Refusal as error:
        print(error)
        raise SystemExit(2)
