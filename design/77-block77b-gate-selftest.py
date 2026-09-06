"""Selftest for design/77-block77b-demo-gate.py, against a BRIDGE-SHAPED fake.

Gate code gets no review pass and runs unattended on someone else's machine, so
it is run here first. Two rules this file exists to obey:

* **Bridge shaped, not MagicMock shaped** (59a). A `MagicMock` hands back
  Python-friendly objects; real Core collections answer `size()`/`get(i)` and
  raise on `__iter__`. `_StrVector` below raises from `__iter__` deliberately,
  so a `list(...)` anywhere in the gate or the product path it drives fails here
  instead of on the demo machine.
* **The fake is written from the dependency, not from the caller** (60b). The
  hook log is produced by the REAL `SNRObservationHook` writing through the real
  `HookBase._write_log`, and the dataset name comes from the arguments the real
  emitter passes -- never from a filename this file invents to match the gate's
  glob. That is precisely the defect that cost block 60b its rig trip.

Run it on BOTH trees so its result discriminates:

    .venv/bin/python design/77-block77b-gate-selftest.py
    MICROCLAW_TREE_UNDER_TEST=<pre-77b checkout> .venv/bin/python design/77-block77b-gate-selftest.py --expect-standdown

On this branch every case must hold. On a pre-77b tree the gate must stand down
at limb E and score nothing else.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GATE = ROOT / "design" / "77-block77b-demo-gate.py"

CASES = []


def case(name):
    def decorate(fn):
        CASES.append((name, fn))
        return fn
    return decorate


class _StrVector:
    """A Core collection: size()/get(i), and NEVER iterable (59a)."""

    def __init__(self, values):
        self._values = list(values)

    def size(self):
        return len(self._values)

    def get(self, index):
        return self._values[index]

    def __iter__(self):
        raise TypeError("'mmcorej_StrVector' object is not iterable")


class FakeCore:
    def __init__(self, rig):
        self._rig = rig

    # --- scalars the gate reads directly ---
    def get_camera_device(self):
        return self._rig.camera

    def get_xy_stage_device(self):
        return "XY" if self._rig.has_stage else ""

    def get_focus_device(self):
        return "Z"

    def get_x_position(self):
        return self._rig.xy[0]

    def get_y_position(self):
        return self._rig.xy[1]

    def get_position(self):
        return 0.0

    def set_xy_position(self, x, y):
        self._rig.move(x, y)

    def set_position(self, z):
        pass

    def set_exposure(self, ms):
        self._rig.exposure_ms = ms

    def get_exposure(self):
        return self._rig.exposure_ms

    def wait_for_device(self, device):
        pass

    def device_busy(self, device):
        return False

    # --- collections: bridge shaped ---
    def get_available_config_groups(self):
        return _StrVector(["Channel"])

    def get_available_configs(self, group):
        return _StrVector(["DAPI"])

    def get_loaded_devices(self):
        return _StrVector(["Camera", "XY", "Z"])

    def get_image_width(self):
        return 8

    def get_image_height(self):
        return 8

    def get_bytes_per_pixel(self):
        return 2

    def __getattr__(self, name):
        raise AttributeError(
            f"the gate reached Core.{name}, which this fake does not model -- "
            "model it here rather than discovering it on the demo machine"
        )


def build_rig(tmp, *, order_executed=None, burst_second_field=False,
              camera="DCam", has_stage=True, drop_position_key=False):
    """A rig whose Acquisition dispatches and consumes frames in __exit__.

    `order_executed` overrides the executed order regardless of the submitted
    event list, which is how the deliberate-failure cases pretend the engine
    ignored `order=`.
    """
    import numpy as np

    rig = SimpleNamespace(now=0.0, xy=(0.0, 0.0), camera=camera,
                          has_stage=has_stage, exposure_ms=10.0,
                          acquisitions=[], settle=0.4, frames=[])

    def move(x, y):
        if (x, y) != rig.xy:
            rig.now += rig.settle
            rig.xy = (x, y)
        return {"requested_um": [x, y], "measured_um": [x, y]}

    rig.move = move
    rig.core = FakeCore(rig)

    class Backend:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.origin = rig.now
            self._dataset_disk_location = str(
                Path(kwargs["directory"]) / kwargs["name"])
            rig.acquisitions.append(self)

        def acquire(self, events):
            self.events = list(events)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            events = self.events
            if order_executed is not None:
                key = {"ptcz": lambda e: (e["axes"].get("position"), e["axes"].get("time")),
                       "tpcz": lambda e: (e["axes"].get("time"), e["axes"].get("position"))}
                events = sorted(events, key=key[order_executed])
            processor = self.kwargs.get("image_process_fn")
            for event in events:
                if "x" in event:
                    rig.move(event["x"], event["y"])
                due = self.origin + event.get("min_start_time", 0)
                if burst_second_field and rig.xy[0] != 0.0:
                    due = rig.now          # B inherits an already-elapsed clock
                rig.now = max(rig.now, due)
                axes = dict(event["axes"])
                label = axes.get("position")
                rig.frames.append((label, axes.get("time"), rig.now))
                metadata = {"Axes": axes, "FrameIndex": axes.get("time"),
                            "XPosition_um_Intended": rig.xy[0],
                            "YPosition_um_Intended": rig.xy[1]}
                if drop_position_key:
                    # BOTH keys, written from the product rather than from what
                    # the gate expects: HookBase.where() reads
                    # metadata["PositionName"] and falls back to
                    # Axes["position"], so dropping only the first leaves the
                    # position intact and tests nothing. The selftest caught
                    # exactly that.
                    metadata["Axes"] = {k: v for k, v in axes.items()
                                        if k != "position"}
                else:
                    metadata["PositionName"] = label
                if processor:
                    processor(np.zeros((8, 8), dtype=np.uint16), metadata, None)
                rig.now += 0.01

    rig.Backend = Backend

    class Dispatch:
        def __new__(cls, **kwargs):
            return Backend(**kwargs)

    rig.Dispatch = Dispatch
    return rig


class FrozenClock:
    """observed_at must advance with the rig's simulated clock, not wall time.

    The gate reads `observed_at`, which the real hook stamps from
    datetime.now(). Under a fake engine that "waits" 2 s in zero real time,
    wall-clock stamps would collapse to microseconds and limb C would fail for
    a reason that says nothing about the product.
    """

    def __init__(self, rig):
        self.rig = rig

    def install(self, monkey, hooks_module):
        import datetime as _dt
        rig = self.rig
        base = _dt.datetime(2026, 9, 6, 12, 0, 0, tzinfo=_dt.timezone.utc)

        class _DT(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return base + _dt.timedelta(seconds=rig.now)

        monkey(hooks_module, "datetime", _DT)


def load_gate():
    spec = importlib.util.spec_from_file_location("block77b_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_gate(rig, out: Path, extra_argv=()):
    """Run the gate's main() against `rig`, returning its score rows."""
    from microclaw import hooks as hooks_module
    from microclaw import tools

    gate = load_gate()
    saved = {}

    def monkey(obj, name, value):
        saved.setdefault((obj, name), getattr(obj, name))
        setattr(obj, name, value)

    guard = SimpleNamespace(
        resolve_in_workspace=lambda p: str(p),
        resolve_readable_path=lambda p: str(p),
        check_xy=lambda x, y: None,
        check_z=lambda z: None,
        check_exposure=lambda ms: None,
        stage_move_tolerance=lambda *a, **k: None,
        analysis_min_snr=None,
        declared_illumination_state=lambda *a, **k: {},
        check_acquisition=lambda **kwargs: None,
        check_plugin=lambda classpath: None,
    )
    ctrl = SimpleNamespace(core=rig.core, set_xy=rig.move,
                           studio=SimpleNamespace(
                               live=lambda: SimpleNamespace(
                                   is_live_mode_on=lambda: False)))

    try:
        monkey(tools, "Acquisition", rig.Dispatch)
        monkey(tools, "read_xy_start_position", lambda *a: rig.xy)
        monkey(tools, "settle_xy_move", lambda *a: {})
        # Use the REAL reservation object. A hand-written stand-in missed
        # has_overrun and every acquisition failed for a reason that said
        # nothing about the gate -- a fake encoding an assumption, exactly what
        # this file is here to avoid.
        from microclaw.acquisition import AcquisitionLedger
        ledger = AcquisitionLedger()
        rig.reservations = []

        def authorize(c, g, plan):
            reservation = ledger.reserve(g, plan)
            rig.reservations.append(reservation)
            return reservation

        monkey(tools, "_authorize_acquisition", authorize)
        FrozenClock(rig).install(monkey, hooks_module)
        # The gate imports these INSIDE main(), so they must be patched at
        # their source modules, not on the gate module. Discovering that here
        # is the point of running the gate against a fake at all.
        from microclaw import config as config_module
        from microclaw import controller as controller_module
        from microclaw import safety as safety_module
        monkey(controller_module, "MicroscopeController", lambda port=None: ctrl)
        monkey(config_module, "load_safety_config_or_exit",
               lambda path=None: SimpleNamespace(
                   constraints=SimpleNamespace(workspace_dir=None)))
        monkey(safety_module, "SafetyGuard", lambda constraints: guard)
        argv = ["gate", "--out", str(out), *extra_argv]
        saved_argv, sys.argv = sys.argv, argv
        saved_out, saved_err = sys.stdout, sys.stderr
        try:
            gate.main()
        finally:
            sys.argv = saved_argv
            sys.stdout, sys.stderr = saved_out, saved_err
    finally:
        for (obj, name), value in saved.items():
            setattr(obj, name, value)
    return json.loads((out / "score.json").read_text(encoding="utf-8"))


def status_of(rows, prefix):
    for row in rows:
        if row["name"].startswith(prefix):
            return row["status"], row["detail"]
    raise AssertionError(f"no limb named {prefix!r} in {[r['name'] for r in rows]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-standdown", action="store_true",
                        help="Assert the gate stands down at limb E (a pre-77b tree).")
    args = parser.parse_args()

    if args.expect_standdown:
        with tempfile.TemporaryDirectory() as tmp:
            rig = build_rig(Path(tmp))
            rows = run_gate(rig, Path(tmp) / "out")
        status, detail = status_of(rows, "E - ")
        assert status == "FAIL", f"limb E on a pre-77b tree scored {status}: {detail}"
        others = [r for r in rows if not r["name"].startswith("E - ")]
        bad = [r for r in others if r["status"] != "NOT EXERCISED"]
        assert not bad, f"these limbs ran on a pre-77b tree: {bad}"
        print(f"PASS: pre-77b tree stands down at E; {len(others)} limbs NOT EXERCISED")
        return 0

    failures = []
    for name, fn in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                fn(Path(tmp))
            except Exception as exc:                 # noqa: BLE001 - reported
                import traceback
                traceback.print_exc()
                failures.append(name)
                print(f"SELFTEST FAIL: {name}: {type(exc).__name__}: {exc}")
            else:
                print(f"SELFTEST PASS: {name}")
    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} selftest cases pass")
    if failures:
        print("FAILURES:", failures)
    return 1 if failures else 0


@case("healthy rig: E, 0, A, B, C PASS; the bridge-only limbs never fake a pass")
def _healthy(tmp):
    rig = build_rig(tmp)
    rows = run_gate(rig, tmp / "out")
    for prefix in ("E - ", "0 - ", "A - ", "B - ", "C - "):
        status, detail = status_of(rows, prefix)
        assert status == "PASS", f"{prefix} scored {status}: {detail}"
    # D, F and G need a real NDTiff, a real bridge for the standalone child, and
    # a real dataset to mosaic. No fake can exercise them, so the property worth
    # checking is that they do NOT report a pass here -- a limb that cannot fail
    # is not a criterion (58a). They are exercised only on the demo machine.
    for prefix in ("D - ", "F - ", "G - "):
        status, _ = status_of(rows, prefix)
        assert status != "PASS", (
            f"{prefix} PASSed against a fake that cannot exercise it; it would "
            "pass on the demo machine without measuring anything")


@case("engine ignores order=: limb A FAILs and says what it executed")
def _wrong_order(tmp):
    rig = build_rig(tmp, order_executed="tpcz")
    rows = run_gate(rig, tmp / "out")
    status, detail = status_of(rows, "A - ")
    assert status == "FAIL", f"limb A scored {status} against a tpcz-executing engine"
    assert "executed order was" in detail, detail


@case("field B bursts: limb C FAILs and names the field")
def _burst(tmp):
    rig = build_rig(tmp, burst_second_field=True)
    rows = run_gate(rig, tmp / "out")
    status, detail = status_of(rows, "C - ")
    assert status == "FAIL", f"limb C scored {status} against a bursting field B"
    assert "burst" in detail, detail


@case("metadata carries no PositionName: limb A FAILs rather than scoring None order")
def _no_position(tmp):
    rig = build_rig(tmp, drop_position_key=True)
    rows = run_gate(rig, tmp / "out")
    status, detail = status_of(rows, "A - ")
    assert status == "FAIL", f"limb A scored {status} on a log with no positions"
    assert "carry no position" in detail, detail


@case("no camera: limb 0 NOT EXERCISED and the rest stand down, never PASS")
def _no_camera(tmp):
    rig = build_rig(tmp, camera="")
    rows = run_gate(rig, tmp / "out")
    status, detail = status_of(rows, "0 - ")
    assert status == "NOT EXERCISED", f"limb 0 scored {status} with no camera"
    for prefix in ("A - ", "B - ", "C - "):
        status, _ = status_of(rows, prefix)
        assert status != "PASS", f"{prefix} PASSed with no camera"


@case("no XY stage: limb 0 NOT EXERCISED, never PASS")
def _no_stage(tmp):
    rig = build_rig(tmp, has_stage=False)
    rows = run_gate(rig, tmp / "out")
    status, detail = status_of(rows, "0 - ")
    assert status == "NOT EXERCISED", f"limb 0 scored {status} with no XY stage"
    assert "XY stage" in detail, detail


if __name__ == "__main__":
    sys.exit(main())
