"""Mutates PFS and may command bounded TIZDrive motion near the sample.

Cases 1-4 share one file deliberately so ceiling, step, travel-budget, status,
and abort enforcement cannot diverge. The probe forces PFS Off on cleanup and
verifies that safer state by read-back; it does not restore Z. The panel case
observes one operator-commanded Studio move and never claims which API made it.
"""
from __future__ import annotations
import argparse, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import nikon_kit_common as kit
PROBE = "probes1-4-pfs-motion"
ALLOWED = {"Locked in focus", "Focus lock failed", "Out of focus search range"}

def parser():
    p=argparse.ArgumentParser(description=__doc__); kit.add_common_arguments(p)
    p.add_argument("--case", required=True, choices=("absolute","relative","panel","api-vs-property"))
    p.add_argument("--z-ceiling", required=True, type=kit.finite, help="hard ceiling for this run (um)")
    p.add_argument("--confirmed-z-ceiling", required=True, type=kit.finite, help="operator-confirmed maximum ceiling (um)")
    p.add_argument("--z-minimum", required=True, type=kit.finite)
    p.add_argument("--step", required=True, type=kit.finite)
    p.add_argument("--max-step", required=True, type=kit.positive)
    p.add_argument("--travel-budget", required=True, type=kit.positive)
    p.add_argument("--observe-seconds", type=kit.positive, default=10)
    p.add_argument("--dry-run", action="store_true")
    return p

def validate_plan(a, current=None):
    if a.z_ceiling > a.confirmed_z_ceiling: raise kit.Refusal(f"REFUSED: --z-ceiling {a.z_ceiling} is above operator-confirmed --confirmed-z-ceiling {a.confirmed_z_ceiling}.")
    if a.z_minimum > a.z_ceiling: raise kit.Refusal("REFUSED: --z-minimum is above --z-ceiling.")
    if abs(a.step) > a.max_step: raise kit.Refusal(f"REFUSED: absolute --step {abs(a.step)} exceeds --max-step {a.max_step}.")
    if abs(a.step) > a.travel_budget: raise kit.Refusal("REFUSED: --step exceeds --travel-budget.")
    if current is not None and not a.z_minimum <= current+a.step <= a.z_ceiling: raise kit.Refusal("REFUSED: planned target is outside the confirmed Z envelope.")

def dry(a):
    validate_plan(a)
    print("DRY RUN ONLY — no Micro-Manager connection and no hardware commands.")
    print(f"Case: {a.case}")
    print("Would validate TIZDrive/TIPFSStatus/TIPFSOffset and current Z.")
    print("Would arm PFS, record State/Status/enabled/locked transitions, and abort on an unexpected Status.")
    if a.case == "absolute": print(f"Would command set_position('TIZDrive', current_z + {a.step}).")
    elif a.case == "relative": print(f"Would command set_relative_position('TIZDrive', {a.step}).")
    elif a.case == "panel": print(f"Would observe one Stage Control move of exactly {a.step} um during the timed window.")
    else: print("Would compare enable_continuous_focus(True) with TIPFSStatus.State='On'; no commanded Z move.")
    print("Would force TIPFSStatus.State Off and verify read-back on cleanup.")

def check_status(point):
    if point["status"] not in ALLOWED: raise kit.Refusal(f"ABORTED: unexpected TIPFSStatus.Status {point['status']!r}.")

def watch(core, started, seconds):
    out=[]
    while time.monotonic()-started < seconds:
        point=kit.observation(core,started); check_status(point); out.append(point); time.sleep(.1)
    point=kit.observation(core,started); check_status(point); out.append(point); return out

def body(core,payload,log):
    current=float(core.get_position("TIZDrive")); validate_plan(args,current)
    guard=kit.MotionGuard(core,args.z_ceiling,args.confirmed_z_ceiling,args.z_minimum,args.max_step,args.travel_budget)
    guard.check_position(current); target=current+args.step
    initial_status=str(core.get_property("TIPFSStatus","Status"))
    if initial_status not in ALLOWED: raise kit.Refusal(f"REFUSED: unexpected initial TIPFSStatus.Status {initial_status!r}.")
    transitions=[]
    if args.case == "api-vs-property":
        for mechanism in ("continuous-focus-api","State-property"):
            core.set_property("TIPFSStatus","State","Off")
            armed=time.monotonic()
            if mechanism == "continuous-focus-api": core.enable_continuous_focus(True)
            else: core.set_property("TIPFSStatus","State","On")
            transitions.append({"mechanism":mechanism,"observations":watch(core,armed,args.observe_seconds)})
    else:
        core.set_property("TIPFSStatus","State","On"); armed=time.monotonic(); before=kit.observation(core,armed); check_status(before)
        guard.approve(current,target)
        if args.case == "absolute": core.set_position("TIZDrive",target)
        elif args.case == "relative": core.set_relative_position("TIZDrive",args.step)
        else:
            print(f"During the next {args.observe_seconds} seconds, use Stage Control once to move TIZDrive by exactly {args.step} um.")
        observations=watch(core,armed,args.observe_seconds)
        after_z=float(core.get_position("TIZDrive")); delta=after_z-current
        tolerance=max(.1,abs(args.step)*.05)
        outcome="completed" if abs(delta-args.step)<=tolerance else ("rejected" if abs(delta)<=tolerance else "modified by servo")
        transitions.append({"before":before,"observations":observations,"after_z_um":after_z,"measured_delta_um":delta,"outcome":outcome})
    payload.update({"case":args.case,"initial_z_um":current,"planned_target_um":target,
                    "transitions":transitions,"safety":{"ceiling":args.z_ceiling,"minimum":args.z_minimum,
                    "max_step":args.max_step,"travel_budget":args.travel_budget}})
    log.append(f"Case {args.case} completed; see JSON for transition timing and outcome.")

if __name__ == "__main__":
    args=parser().parse_args()
    try:
        if args.dry_run: dry(args); raise SystemExit(0)
        raise SystemExit(kit.run_probe(PROBE,Path(__file__),args,body,kit.force_pfs_off))
    except kit.Refusal as e:
        print(e); raise SystemExit(2)
