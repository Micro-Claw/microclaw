"""Block 4 G5 (v2): read the MMStudio SequenceSettings ArrayLists correctly.

v1 established that settings.slices() and settings.channels() WORK and each
return a java_util_ArrayList; the original _read_mda_settings TypeError was
from `for v in <ArrayList>` -- bridge ArrayLists are not directly iterable
(the codebase reads such collections via .size()/.get(i), see _str_vector).

v1 had a capture bug (it introspected `settings.slices`, the bound method,
not the called result), so it never reached a real ChannelSpec. This version
calls the methods, reads the ArrayLists by index, and introspects a real
ChannelSpec so the channel exposure/useChannel accessors are measured.

Read-only. Set up an MDA in the GUI with Z slices + >=1 channel first.
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


def show(label, thunk):
    try:
        v = thunk()
    except Exception as e:
        print(f"  {label:36s} -> {type(e).__name__}: {e}")
        return None
    prev = repr(v)
    if len(prev) > 68:
        prev = prev[:68] + "..."
    print(f"  {label:36s} -> {type(v).__name__}: {prev}")
    return v


print("--- SLICES: settings.slices() is an ArrayList; read it by index ---")
sl = show("settings.slices()", lambda: settings.slices())
if sl is not None:
    n = show("  slices.size()", lambda: sl.size())
    show("  slices.get(0)", lambda: sl.get(0))
    show("  direct iter [x for x in slices]", lambda: [x for x in sl])
    if isinstance(n, int) and n > 0:
        vals = []
        for i in range(n):
            try:
                vals.append(float(sl.get(i)))
            except Exception as e:
                vals.append(f"<{type(e).__name__}>")
        print("  slices by index+float ->", vals)

print("\n--- CHANNELS: settings.channels() is an ArrayList of ChannelSpec ---")
ch = show("settings.channels()", lambda: settings.channels())
if ch is not None:
    cn = show("  channels.size()", lambda: ch.size())
    spec = show("  channels.get(0)", lambda: ch.get(0))
    if spec is not None:
        print("  channels.get(0) type:", type(spec).__name__)
        print("  --- ChannelSpec accessor candidates ---")
        for label, thunk in [
            ("spec.useChannel (field)",   lambda: spec.useChannel),
            ("spec.useChannel() (call)",  lambda: spec.useChannel()),
            ("spec.use_channel()",        lambda: spec.use_channel()),
            ("spec.exposure (field)",     lambda: spec.exposure),
            ("spec.exposure() (call)",    lambda: spec.exposure()),
            ("spec.config (field)",       lambda: spec.config),
            ("spec.config() (call)",      lambda: spec.config()),
        ]:
            show("    " + label, thunk)
        print("  --- dir(ChannelSpec) filtered ---")
        try:
            names = [x for x in dir(spec)
                     if any(k in x.lower() for k in
                            ("channel", "exposure", "config", "use", "color", "z_offset"))]
            print("   ", names)
        except Exception as e:
            print("    dir failed:", type(e).__name__, e)

print("\nDONE. Paste the whole output.")
