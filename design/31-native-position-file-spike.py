#!/usr/bin/env python
"""Probe native PositionList file behavior over a live pycro-manager bridge.

Run with Micro-Manager open and its ZMQ server enabled:

    python design/31-native-position-file-spike.py [--port 4827]

The spike loads tests/fixtures/PD_PositionList2.pos into throwaway Java objects,
never publishes them to Micro-Manager's PositionListManager, and never touches
hardware. It briefly writes a sibling ``.spike-roundtrip.pos`` file and removes
it in ``finally``.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import traceback

from pycromanager import Core, JavaObject, Studio


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "PD_PositionList2.pos"
ROUNDTRIP = FIXTURE.with_name("PD_PositionList2.spike-roundtrip.pos")


def first_attr(obj, names: tuple[str, ...]):
    errors = []
    for name in names:
        try:
            value = getattr(obj, name)
            return name, value() if callable(value) else value
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}")
    raise RuntimeError(f"none of {names!r} resolved ({', '.join(errors)})")


def report(name: str, fn):
    try:
        detail = fn()
        print(f"[PASS] {name}\n       {detail}", flush=True)
        return detail
    except Exception as exc:
        print(f"[FAIL] {name}: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4827)
    args = parser.parse_args()

    if not FIXTURE.is_file():
        raise SystemExit(f"fixture not found: {FIXTURE}")

    core = Core(port=args.port)
    studio = Studio(port=args.port)
    report("connect", lambda: (
        f"core={core.get_version_info()}; "
        f"xy={core.get_xy_stage_device()!r}; z={core.get_focus_device()!r}; "
        f"GUI positions={studio.positions().get_position_list().get_number_of_positions()}"
    ))

    candidate = JavaObject("org.micromanager.PositionList", port=args.port)
    report("PositionList.load(String)", lambda: (
        candidate.load(str(FIXTURE)) or
        f"loaded {candidate.get_number_of_positions()} positions from {FIXTURE}"
    ))

    def inspect_first():
        if int(candidate.get_number_of_positions()) != 25:
            raise RuntimeError(
                f"expected 25 positions, got {candidate.get_number_of_positions()}"
            )
        msp = candidate.get_position(0)
        label_name, label = first_attr(msp, ("get_label", "getLabel"))
        xy_name, default_xy = first_attr(
            msp, ("get_default_xy_stage", "getDefaultXYStage")
        )
        z_name, default_z = first_attr(
            msp, ("get_default_z_stage", "getDefaultZStage")
        )
        sp = msp.get(0)
        axes_name, axes = first_attr(sp, ("numAxes", "num_axes"))
        device_name, device = first_attr(
            sp,
            ("stageName", "stage_name", "get_stage_name", "getStageName", "device"),
        )
        x_name, x = first_attr(sp, ("x", "get_x", "getX"))
        y_name, y = first_attr(sp, ("y", "get_y", "getY"))
        if str(label) != "spiral_01" or int(axes) != 2:
            raise RuntimeError(f"unexpected first entry: label={label!r}, axes={axes!r}")
        return (
            f"label via {label_name}={label!r}; defaults via {xy_name}/{z_name}="
            f"{default_xy!r}/{default_z!r}; device via {device_name}={device!r}; "
            f"axes via {axes_name}={int(axes)}; coords via {x_name}/{y_name}="
            f"{float(x)}, {float(y)}"
        )

    report("field and method spellings", inspect_first)

    try:
        report("PositionList.save(String)", lambda: (
            candidate.save(str(ROUNDTRIP)) or f"saved {ROUNDTRIP}"
        ))
        reloaded = JavaObject("org.micromanager.PositionList", port=args.port)
        report("saved file reload", lambda: (
            reloaded.load(str(ROUNDTRIP)) or
            f"reloaded {reloaded.get_number_of_positions()} positions"
        ))

        def compare_directly():
            _, left = first_attr(candidate, ("to_property_map", "toPropertyMap"))
            _, right = first_attr(reloaded, ("to_property_map", "toPropertyMap"))
            equal = left.equals(right)
            if not bool(equal):
                raise RuntimeError("Property Maps differ after native save/reload")
            return "Property Maps are equal after native save/reload"

        report("Property Map equality", compare_directly)
    finally:
        try:
            ROUNDTRIP.unlink(missing_ok=True)
        except Exception as exc:
            print(f"[WARN] could not remove {ROUNDTRIP}: {exc}", flush=True)


if __name__ == "__main__":
    main()
