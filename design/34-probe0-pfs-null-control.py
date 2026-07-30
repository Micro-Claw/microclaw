"""Mutates PFS state without commanded Z moves; arming PFS may servo-drive TIZDrive.

Run one operator-positioned case per invocation. This probe restores the initial
TIPFSStatus.State and verifies it by read-back. It never moves to either supplied Z.
"""
from __future__ import annotations
import argparse, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nikon_kit_common as kit

PROBE = "probe0-pfs-null-control"

def parser():
    p = argparse.ArgumentParser(description=__doc__)
    kit.add_common_arguments(p)
    p.add_argument("--case", choices=("out-of-range", "in-range"), required=True)
    p.add_argument("--out-of-range-z", required=True, type=kit.finite,
                   help="operator-confirmed out-of-range observation Z (um); probe does not move there")
    p.add_argument("--in-range-z", required=True, type=kit.finite,
                   help="operator-confirmed lockable observation Z (um); probe does not move there")
    p.add_argument("--position-tolerance", required=True, type=kit.positive)
    return p

def body(core, payload, log):
    expected = args.out_of_range_z if args.case == "out-of-range" else args.in_range_z
    actual = float(core.get_position("TIZDrive"))
    if abs(actual - expected) > args.position_tolerance:
        raise kit.Refusal(f"REFUSED: current TIZDrive is {actual} um, but --case {args.case} requires "
                          f"{expected} +/- {args.position_tolerance} um. Position the rig there manually, "
                          "confirm it is safe, and run the same command again.")
    timeout = str(core.get_property("TIPFSStatus", "FullFocusTimeoutMs"))
    payload.update({"case": args.case, "declared_z_um": expected,
                    "position_tolerance_um": args.position_tolerance,
                    "full_focus_timeout_ms": timeout, "commanded_z_motion": False})
    core.set_property("TIPFSStatus", "State", "On")
    armed = time.monotonic()
    samples = [kit.observation(core, armed)]
    enabled = samples[0]["continuous_focus_enabled"]
    never_armed = samples[0]["state"] != "On" or enabled is False
    if never_armed:
        verdict = "never armed — inconclusive"
        payload["observations"] = samples
        payload["verdict"] = {"case": args.case, "name": verdict,
                              "requires_rerun": True,
                              "reason": "PFS was not armed on the first read after the On command."}
        message = (f"Verdict [{args.case}]: {verdict}. This case must be re-run and "
                   "must not be interpreted.")
        log.extend([f"Case: {args.case}", f"FullFocusTimeoutMs: {timeout}"])
        raise kit.Refusal(message)
    for deadline in (1, 2, 5, 10, 30):
        time.sleep(max(0, armed + deadline - time.monotonic()))
        sample = kit.observation(core, armed); samples.append(sample)
        print(f"{deadline}s: State={sample['state']} Status={sample['status']}")
    payload["observations"] = samples
    fell = any(a["state"] == "On" and b["state"] == "Off" for a, b in zip(samples, samples[1:]))
    verdict = "autonomous timeout observed" if fell else "remained armed through window"
    payload["verdict"] = {"case": args.case, "name": verdict,
                          "requires_rerun": False}
    log.extend([f"Case: {args.case}", f"FullFocusTimeoutMs: {timeout}", f"Verdict [{args.case}]: {verdict}"])

if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(kit.run_probe(PROBE, Path(__file__), args, body, kit.restore_pfs))
