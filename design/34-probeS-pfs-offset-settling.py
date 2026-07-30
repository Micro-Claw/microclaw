"""Commands TIPFSOffset; the locked PFS servo can move TIZDrive in response.

Requires PFS already locked. Three bounded targets are commanded. Cleanup does
not restore the offset or disable a locked PFS: either could cause unsafe servo
motion. It preserves the final locked state and verifies PFS remains On/locked.
"""
from __future__ import annotations
import argparse, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import nikon_kit_common as kit
PROBE = "probeS-pfs-offset-settling"

def parser():
    p = argparse.ArgumentParser(description=__doc__); kit.add_common_arguments(p)
    p.add_argument("--targets", nargs=3, required=True, type=kit.finite, metavar=("FIRST", "SECOND", "THIRD"))
    p.add_argument("--max-offset-delta", required=True, type=kit.positive)
    p.add_argument("--total-excursion-budget", required=True, type=kit.positive)
    p.add_argument("--z-minimum", required=True, type=kit.finite)
    p.add_argument("--z-maximum", required=True, type=kit.finite)
    p.add_argument("--settle-tolerance", required=True, type=kit.positive)
    p.add_argument("--settle-timeout", required=True, type=kit.positive)
    p.add_argument("--poll-interval", type=kit.positive, default=.05)
    return p

def locked(core):
    return str(core.get_property("TIPFSStatus", "State")) == "On" and bool(core.is_continuous_focus_locked())

def cleanup(core, initial):
    result = {"contract": "preserve final locked PFS; do not restore offset or disable lock", "ok": False}
    try:
        result["state"] = str(core.get_property("TIPFSStatus", "State")); result["locked"] = bool(core.is_continuous_focus_locked())
        result["final_offset"] = float(core.get_position("TIPFSOffset")); result["final_z_um"] = float(core.get_position("TIZDrive"))
        result["ok"] = result["state"] == "On" and result["locked"] and args.z_minimum <= result["final_z_um"] <= args.z_maximum
        if not result["ok"]: result["error"] = "PFS is not locked On or Z left the confirmed envelope"
    except Exception as e: result["error"] = str(e)
    return result

def body(core, payload, log):
    if args.z_minimum >= args.z_maximum: raise kit.Refusal("REFUSED: --z-minimum must be below --z-maximum.")
    if not locked(core): raise kit.Refusal("REFUSED: PFS must already be On and locked before Probe S starts.")
    current = float(core.get_position("TIPFSOffset")); total = 0.0; moves = []
    for target in args.targets:
        delta = abs(target-current)
        if delta > args.max_offset_delta: raise kit.Refusal(f"REFUSED: offset delta {delta} exceeds --max-offset-delta.")
        total += delta
        if total > args.total_excursion_budget: raise kit.Refusal("REFUSED: targets exceed --total-excursion-budget.")
        z = float(core.get_position("TIZDrive"))
        if not args.z_minimum <= z <= args.z_maximum: raise kit.Refusal(f"ABORTED: TIZDrive {z} is outside the confirmed envelope.")
        started = time.monotonic(); core.set_position("TIPFSOffset", target); core.wait_for_device("TIPFSOffset")
        first = {"elapsed_s": time.monotonic()-started, "offset": float(core.get_position("TIPFSOffset")), "z_um": float(core.get_position("TIZDrive"))}
        polls = [first]; stable = 0
        while time.monotonic()-started < args.settle_timeout:
            time.sleep(args.poll_interval)
            point = {"elapsed_s": time.monotonic()-started, "offset": float(core.get_position("TIPFSOffset")), "z_um": float(core.get_position("TIZDrive"))}
            polls.append(point)
            if not args.z_minimum <= point["z_um"] <= args.z_maximum: raise kit.Refusal(f"ABORTED: measured TIZDrive {point['z_um']} left the confirmed envelope.")
            stable = stable+1 if abs(point["offset"]-target) <= args.settle_tolerance else 0
            if stable >= 3: break
        if stable < 3: raise kit.Refusal(f"ABORTED: TIPFSOffset did not settle at {target} before timeout.")
        move = {"requested_target": target, "first_read_after_wait_for_device": first,
                "poll_series": polls, "settled_value": polls[-1]["offset"], "settled_z_um": polls[-1]["z_um"],
                "observed_offset_change": polls[-1]["offset"]-current,
                "observed_z_change_um": polls[-1]["z_um"]-z}
        moves.append(move); current = polls[-1]["offset"]
        print(f"Move {len(moves)}/3 settled: offset={current}, Z={polls[-1]['z_um']}")
    payload["moves"] = moves; payload["expected_excursion"] = None
    payload["expected_excursion_reason"] = "No prior measured evidence was supplied; no excursion was inferred from offset delta."
    log.append("Three consecutive moves completed and settled; see JSON for full polling series.")

if __name__ == "__main__":
    args = parser().parse_args(); raise SystemExit(kit.run_probe(PROBE, Path(__file__), args, body, cleanup, True))
