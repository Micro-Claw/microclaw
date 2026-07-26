"""Block 4 G5: discover the correct MMStudio SequenceSettings accessors.

`_read_mda_settings` reads slices/channels via `settings.slices()` and
`settings.channels()` with `spec.useChannel` / `spec.exposure`. Both throw
TypeError on M5's MMStudio build, so any z-stack/multichannel MDA is refused
as unplannable. This probe introspects the live settings object over the
bridge to find the accessors that actually work, so the fix is measured, not
guessed a third time.

Read-only: reads the current MDA settings, takes no image, moves nothing.
Set up any MDA in the MM GUI first (Z slices + at least one channel enabled).

  uv run python design/32-block4-mda-settings-probe.py --config <config>
"""
import argparse
import sys

from microclaw.config import load_safety_config_or_exit
from microclaw.safety import SafetyGuard
from microclaw.controller import MicroscopeController

sys.stdout.reconfigure(line_buffering=True)

parser = argparse.ArgumentParser()
parser.add_argument("--config", required=True)
args = parser.parse_args()

parsed = load_safety_config_or_exit(args.config)
guard = SafetyGuard(parsed.constraints)
ctrl = MicroscopeController(guard=guard)

settings = ctrl.studio.acquisitions().get_acquisition_settings()
print("settings repr:", repr(settings))
print("settings type:", type(settings).__name__)


def show(label, thunk):
    """Run thunk, print its result type/preview or the exception."""
    try:
        v = thunk()
    except Exception as e:
        print(f"  {label:34s} -> {type(e).__name__}: {e}")
        return None
    kind = type(v).__name__
    prev = repr(v)
    if len(prev) > 70:
        prev = prev[:70] + "..."
    print(f"  {label:34s} -> {kind}: {prev}")
    return v


print("\n--- dir(settings) (filtered to likely names) ---")
try:
    names = [n for n in dir(settings)
             if any(k in n.lower() for k in ("slice", "channel", "frame", "z_"))]
    print(" ", names or "(none matched; full dir below)")
    if not names:
        print(" ", [n for n in dir(settings) if not n.startswith("__")][:60])
except Exception as e:
    print("  dir() failed:", type(e).__name__, e)

print("\n--- candidate SLICE accessors ---")
slice_val = None
for label, thunk in [
    ("settings.slices (field)",     lambda: settings.slices),
    ("settings.slices() (method)",  lambda: settings.slices()),
    ("settings.get_slices()",       lambda: settings.get_slices()),
    ("settings.z_positions()",      lambda: settings.z_positions()),
]:
    v = show(label, thunk)
    if v is not None and slice_val is None:
        slice_val = v
if slice_val is not None:
    show("  len(slice result)",     lambda: len(slice_val))
    show("  list(slice result)",    lambda: [x for x in slice_val])

print("\n--- candidate CHANNEL accessors ---")
chan_list = None
for label, thunk in [
    ("settings.channels (field)",    lambda: settings.channels),
    ("settings.channels() (method)", lambda: settings.channels()),
    ("settings.get_channels()",      lambda: settings.get_channels()),
]:
    v = show(label, thunk)
    if v is not None and chan_list is None:
        chan_list = v
if chan_list is not None:
    show("  len(channel result)",   lambda: len(chan_list))
    try:
        first = None
        for c in chan_list:
            first = c
            break
        if first is not None:
            print("  first ChannelSpec repr:", repr(first)[:70])
            print("  first ChannelSpec type:", type(first).__name__)
            print("  --- ChannelSpec field/method candidates ---")
            for label, thunk in [
                ("spec.useChannel (field)",   lambda: first.useChannel),
                ("spec.use_channel()",        lambda: first.use_channel()),
                ("spec.exposure (field)",     lambda: first.exposure),
                ("spec.exposure() (method)",  lambda: first.exposure()),
                ("spec.config (field)",       lambda: first.config),
                ("spec.config() (method)",    lambda: first.config()),
            ]:
                show("    " + label, thunk)
            print("  --- dir(ChannelSpec) (filtered) ---")
            cn = [n for n in dir(first)
                  if any(k in n.lower() for k in ("channel", "exposure", "config", "use"))]
            print("   ", cn or [n for n in dir(first) if not n.startswith("__")][:40])
    except Exception as e:
        print("  channel iteration failed:", type(e).__name__, e)

print("\nDONE. Paste the whole output.")
