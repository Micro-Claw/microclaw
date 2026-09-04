"""Run block 74a's demo gate against a bridge-shaped fake, before it ships.

`CLAUDE.md`: a gate is code, and handing an operator code nobody executed is the
defect this workflow keeps paying for. design/59 block 59a reached the demo
machine and every orientation limb came back
`TypeError: 'mmcorej_StrVector' object is not iterable` — a `list()` over a Core
collection that worked against every fake in the suite. **A MagicMock would not
have caught it**, because it hands back Python-friendly objects, so the fake
here returns `size()`/`get(i)` vectors whose `__iter__` raises.

    .venv/bin/python design/74-block74a-gate-selftest.py

Run it on **both trees**. On the block's branch every case must hold; on `main`
the run must exit nonzero at limb E, which is what proves the gate discriminates
rather than passing whatever it is pointed at.

Five cases, and three of them are deliberately *failures* — a gate whose fake
only ever feeds it the happy path has not been tested, it has been rehearsed.
design/60 block 60b's gate failed the single limb its rig trip existed for
because its fake wrote the filename the glob expected.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "gate74a", Path(__file__).with_name("74-block74a-demo-gate.py"))
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


class StrVector:
    """Bridge-shaped: deliberately NOT Python-iterable, like mmcorej_StrVector."""

    def __init__(self, values):
        self._values = list(values)

    def size(self):
        return len(self._values)

    def get(self, index):
        return self._values[index]

    def __iter__(self):
        raise TypeError("mmcorej_StrVector is not iterable")


class JavaString:
    """A bridge shadow that is not a Python str. `str()` of it is not the value.

    This is the shape limb A exists to detect: the discriminator compares
    against a frozenset of plain strings, so a shadow can never match and the
    refusal would silently never fire on any rig.
    """

    def __init__(self, value):
        self._value = value

    def __repr__(self):
        return f"<java.lang.String {self._value!r}>"


class FakeCore:
    def __init__(self, library="DemoCamera", adapter="DAutoFocus",
                 device="Autofocus", readonly=("Description", "HubID", "Name"),
                 values=None, wrap=str):
        self._library, self._adapter, self._device = library, adapter, device
        self._readonly = set(readonly)
        self._values = values or {"Description": "Demo auto-focus adapter",
                                  "HubID": "", "Name": "DAutoFocus"}
        self._wrap = wrap
        self.z = 100.0

    def get_auto_focus_device(self):
        return self._device

    def is_continuous_focus_enabled(self):
        return False

    def get_device_library(self, _device):
        return self._wrap(self._library)

    def get_device_name(self, _device):
        return self._wrap(self._adapter)

    def get_device_property_names(self, _device):
        return StrVector(sorted(self._values))

    def is_property_read_only(self, _device, name):
        return name in self._readonly

    def get_property(self, _device, name):
        return self._values[name]

    def get_position(self):
        return self.z


class FakeCtrl:
    def __init__(self, core):
        self.core = core


def check(label: str, got: str, want: str, detail: str = "") -> bool:
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}: {got}"
          + (f" — {detail[:150]}" if not ok and detail else ""))
    return ok


def main() -> int:
    out = Path(__file__).with_name("_selftest74a_scratch")
    out.mkdir(exist_ok=True)
    ok = True

    print("case 1 — demo machine, well-formed bridge strings")
    ctrl = FakeCtrl(FakeCore())
    status, detail = gate.limb_adapter_identity(ctrl, None, out)
    ok &= check("limb A", status, gate.PASS, detail)

    print("case 2 — the bridge hands back a shadow, not a Python str")
    ctrl = FakeCtrl(FakeCore(wrap=JavaString))
    status, detail = gate.limb_adapter_identity(ctrl, None, out)
    ok &= check("limb A", status, gate.FAIL, detail)
    ok &= check("  and it says why", "not a Python str" in detail, True, detail)

    print("case 3 — some other machine's adapter, not this demo config")
    ctrl = FakeCtrl(FakeCore(library="NikonTI", adapter="TIPFSStatus"))
    status, detail = gate.limb_adapter_identity(ctrl, None, out)
    ok &= check("limb A", status, gate.FAIL, detail)

    print("case 4 — no autofocus device configured at all")
    ctrl = FakeCtrl(FakeCore(device=""))
    status, detail = gate.limb_adapter_identity(ctrl, None, out)
    ok &= check("limb A", status, gate.NOT_EXERCISED, detail)

    print("case 5 — limb C must FAIL if this machine were ever classified")
    # The point of limb C is the negative control. Prove it can fail: patch the
    # tool's own refusal path so run_autofocus returns the hardware-lock
    # refusal, exactly as it would if the discriminator misread this adapter.
    import microclaw.tools as tools
    real = tools.run_autofocus
    tools.run_autofocus = lambda *a, **k: {
        "error": "This rig has a hardware focus lock, 'Autofocus', and it is "
                 "not engaged. Probe the lock first.",
    }
    try:
        ctrl = FakeCtrl(FakeCore())
        status, detail = gate.limb_image_sweep_runs(ctrl, None, out)
        ok &= check("limb C", status, gate.FAIL, detail)
        ok &= check("  and it names the cause",
                    "classified" in detail, True, detail)
    finally:
        tools.run_autofocus = real

    print("case 6 — limb E is the control: does this tree carry the block?")
    status, detail = gate.limb_running_build(None, None, out)
    print(f"  limb E on THIS tree: {status} — {detail[:200]}")
    print("  (must be PASS on design74/lock-refuses-image-sweep, FAIL on main;"
          " that difference is the whole point)")

    for stray in out.glob("*"):
        stray.unlink()
    out.rmdir()
    print("\nselftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
