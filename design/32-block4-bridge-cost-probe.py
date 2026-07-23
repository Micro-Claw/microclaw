"""Block 4: attribute the per-frame cost to bridge callbacks, not to microclaw.

G1 showed the branch ~3.4x slower than main, and the G1a diagnosis cleared
gating (wide == asis), the pixel transfer, and the is_finished poll. What is
left is that main hands the engine a LIST and NO callbacks -- nothing crosses
the bridge during the run -- while the branch makes the engine pull each event
from a Python generator AND fires image_saved_fn per image.

This runs the 2x2 directly against pycro-manager, with microclaw's acquisition
code entirely out of the path, so the attribution cannot be confounded by
anything in tools.py:

    list  + no callback   <- main's shape (baseline)
    gen   + no callback   <- isolates generator event-fetch
    list  + image_saved   <- isolates the saved-image callback
    gen   + image_saved   <- the branch's shape

Dark, minimum exposure. Writes 4 x <frames> throwaway frames.
"""
import argparse
import sys
import time

from pycromanager import Acquisition, multi_d_acquisition_events

from microclaw.config import load_safety_config_or_exit
from microclaw.safety import SafetyGuard
from microclaw.controller import MicroscopeController

sys.stdout.reconfigure(line_buffering=True)

parser = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--config", required=True)
parser.add_argument("--save-dir", required=True, help="Parent dir for four datasets.")
parser.add_argument("--frames", type=int, default=200)
parser.add_argument("--exposure-ms", type=float, default=5.0)
args = parser.parse_args()

parsed = load_safety_config_or_exit(args.config)
guard = SafetyGuard(parsed.constraints)
ctrl = MicroscopeController(guard=guard)
ctrl.core.set_exposure(args.exposure_ms)
print(f"exposure set to {ctrl.core.get_exposure()} ms; frames={args.frames}")

VARIANTS = (
    ("list_nocb", False, False),
    ("gen_nocb",  True,  False),
    ("list_cb",   False, True),
    ("gen_cb",    True,  True),
)

results = {}
for label, use_generator, use_callback in VARIANTS:
    events = multi_d_acquisition_events(num_time_points=args.frames, time_interval_s=0)
    saved = {"n": 0}

    def on_saved(axes, dataset):
        saved["n"] += 1

    kwargs = {"directory": args.save_dir, "name": f"probe_{label}", "show_display": False}
    if use_callback:
        kwargs["image_saved_fn"] = on_saved

    payload = (e for e in events) if use_generator else events

    t0 = time.monotonic()
    with Acquisition(**kwargs) as acq:
        acq.acquire(payload)
    elapsed = time.monotonic() - t0
    results[label] = elapsed
    print(f"{label:10s} elapsed_s={elapsed:8.3f} "
          f"per_frame_ms={elapsed*1000/args.frames:7.2f} "
          f"saved_callbacks={saved['n']}")

base = results["list_nocb"]
print("\n--- relative to list_nocb (main's shape) ---")
for label, _, _ in VARIANTS:
    print(f"{label:10s} {results[label]/base:5.2f}x")
print("\ngenerator cost  =", f"{results['gen_nocb']/base:.2f}x")
print("callback cost   =", f"{results['list_cb']/base:.2f}x")
print("combined        =", f"{results['gen_cb']/base:.2f}x")
