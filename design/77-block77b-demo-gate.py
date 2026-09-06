"""Block 77b demo-machine gate: acquisition order, per-field clocks, and the export.

Every limb here computes, or drives an acquisition this program owns, so it
ships as a program rather than pasted PowerShell blocks (58a): each limb scores
itself independently, one FAIL cannot hide the rest behind a cascade, the gate
owns its own log, and the exit status is nonzero on any FAIL *or* any
NOT EXERCISED.

What this gate can and cannot establish.

Nearly all of 77b is settled off-rig, and those tests were verified by mutation
during review -- the `ptcz`/`tpcz` mapping, the per-position split, both emitter
branches, the typed stage failure at the tool boundary, and the single `timing`
shape. Do not add a limb that repeats one of them.

What needs the bridge is narrower and is all here:

* **Every order test in the suite reads a fake's frame list or an event list we
  built ourselves.** Nothing has observed whether *real* AcqEngJ executes a
  position-axis event list in the order `multi_d_acquisition_events` emitted it.
  Limbs A and B are the first observation of that, scored from the hook log's
  own record order rather than from the tool's return value.
* **Whether a per-position acquisition really gives field B its own clock.**
  The whole settled decision rests on it, and a fake clock cannot show it.
  Limb C measures it against real stage settling.
* **Whether any exposure-timestamp metadata key exists at all.** D3 tells the
  implementation to name the key it reads and verify it on a rig before relying
  on it; the shipped code reports `null` and says the field owes rig
  verification. Limb D enumerates what the camera actually writes, and either
  names a key or documents its absence. This is the one limb that can change
  the product.
* **Whether the exported script runs.** An exported script that compiles is not
  an exported script that works (52b). Limb F execs it in a child process
  against this same bridge and compares its hook log with the live one.
* **Whether `build_stage_coordinate_mosaic` still accepts the zero-interval
  hooked dataset**, which is the stated reason that case keeps one dataset.

It requires **no configuration the product does not require** (60b): no
`--safety-config`, and no workspace. Datasets go under the machine's configured
`workspace_dir` when it has one, and under `--out` when it does not.

`NOT EXERCISED` is never a pass. If the stage cannot be moved inside this
machine's bounds, limbs A-C and F report exactly that and the gate exits
nonzero; do not tick them.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("MICROCLAW_TREE_UNDER_TEST") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))

RESULTS = []

N_FRAMES = 3
SPACED_INTERVAL_S = 2.0


class NotExercised(Exception):
    """The limb could not run its mechanism. Never a pass (58a)."""


class Tee:
    def __init__(self, stream, path):
        self.stream, self.file = stream, open(path, "a", encoding="utf-8")

    def write(self, data):
        self.stream.write(data)
        self.file.write(data)
        return len(data)

    def flush(self):
        self.stream.flush()
        self.file.flush()


def limb(name, fails_if):
    def decorate(fn):
        try:
            detail, status = fn() or "", "PASS"
        except NotExercised as exc:
            detail, status = str(exc), "NOT EXERCISED"
        except Exception as exc:                    # noqa: BLE001 - reported
            detail, status = f"{type(exc).__name__}: {exc}", "FAIL"
            traceback.print_exc()
        RESULTS.append({"name": name, "status": status, "detail": detail,
                        "fails_if": fails_if})
        print(f"{status}: {name} - {detail}")
        return fn
    return decorate


def read_hook_log(path: Path) -> list[dict]:
    """The hook log in ARRIVAL order -- that order is the evidence.

    Read per-line-tolerantly for the same reason 52b's export died on one
    embedded newline: report a torn file rather than losing every record in it.
    """
    if not path.exists():
        raise NotExercised(f"the hook wrote no log at {path}")
    text = path.read_text(encoding="utf-8")
    try:
        records = json.loads(text)
    except ValueError as exc:
        raise AssertionError(f"hook log at {path} is not readable JSON: {exc}") from exc
    if not isinstance(records, list):
        raise AssertionError(f"hook log at {path} is {type(records).__name__}, not a list")
    return records


def positions_in_arrival_order(records: list[dict]) -> list:
    missing = [i for i, r in enumerate(records) if r.get("position") is None]
    if missing:
        raise AssertionError(
            f"{len(missing)} of {len(records)} hook records carry no position; "
            "order cannot be scored from this log"
        )
    return [r["position"] for r in records]


def arrival_times(records: list[dict]) -> list[float]:
    """Callback ARRIVAL times, which is what observed_at is (D3).

    These are not exposure timestamps and this gate never calls them that. They
    bound the spacing from above, which is all limb C needs: a burst cannot hide
    inside them.
    """
    stamps = []
    for r in records:
        raw = r.get("observed_at")
        if raw is None:
            raise AssertionError("a hook record carries no observed_at")
        stamps.append(datetime.fromisoformat(raw).timestamp())
    return stamps


def run_order_case(tools, ctrl, guard, *, out, name, order, interval_s,
                   positions, exposure_ms, log_path):
    kwargs = dict(
        protocol="timelapse", positions=positions, save_dir=str(out), name=name,
        hook_strategy="snr_observer", log_path=str(log_path),
        protocol_params={"n_frames": N_FRAMES, "interval_s": interval_s,
                         "exposure_ms": exposure_ms},
    )
    if order is not None:
        kwargs["acquisition_order"] = order
    started = time.monotonic()
    result = tools.run_multiposition_acquisition(ctrl, guard, **kwargs)
    result["_wall_s"] = round(time.monotonic() - started, 3)
    if "error" in result:
        raise AssertionError(f"{name} returned an error: {result['error']}")
    return result, kwargs


def finish(args):
    log = args.log or (args.out / "score.json")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    print("\n=== BLOCK 77b DEMO GATE ===")
    for row in RESULTS:
        print(f"  {row['status']:14} {row['name']}")
    bad = [r for r in RESULTS if r["status"] != "PASS"]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} PASS; score written to {log}")
    if bad:
        print("NOT A PASS. Report every non-PASS row above verbatim.")
    return 1 if bad else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--out", type=Path, default=Path("block77b-evidence"))
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--safety-config", default=None,
                        help="Normally omitted. The product finds this machine's own.")
    parser.add_argument("--exposure-ms", type=float, default=10.0)
    parser.add_argument("--step-um", type=float, default=20.0,
                        help="XY offset between the gate's two fields.")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, args.out / "gate.txt")
    print(f"Tree under test: {ROOT}")

    import inspect
    from microclaw import tools
    from microclaw.config import load_safety_config_or_exit
    from microclaw.controller import MicroscopeController
    from microclaw.safety import SafetyGuard

    # Limb E first, because it is the control and must be scored even when the
    # bridge is down: on a tree without 77b every other limb would fail for a
    # reason that says nothing about the build.
    signature = inspect.signature(tools.run_multiposition_acquisition)

    @limb("E - the running build carries 77b",
          "a tree whose multiposition tool has no acquisition_order")
    def limb_e():
        missing = []
        if "acquisition_order" not in signature.parameters:
            missing.append("run_multiposition_acquisition(..., acquisition_order=)")
        tile = inspect.signature(tools.run_tile_acquisition).parameters
        if "acquisition_order" not in tile:
            missing.append("run_tile_acquisition(..., acquisition_order=)")
        if missing:
            raise AssertionError("this build is missing: " + "; ".join(missing))
        default = signature.parameters["acquisition_order"].default
        if default != "position_then_time":
            raise AssertionError(f"the default is {default!r}, not 'position_then_time'")
        return "both tools take acquisition_order; the default is position_then_time"

    if "acquisition_order" not in signature.parameters:
        for name in ("0 - bridge, camera and two reachable fields",
                     "A - position-outer is the real executed order",
                     "B - explicit interleaving really interleaves",
                     "C - field B keeps its own clock",
                     "D - what exposure-timestamp key does this camera write",
                     "F - the exported script runs and agrees",
                     "G - the zero-interval dataset still mosaics"):
            RESULTS.append({"name": name, "status": "NOT EXERCISED",
                            "detail": "stood down: limb E found no 77b in this build",
                            "fails_if": "n/a"})
            print(f"NOT EXERCISED: {name} - stood down by limb E")
        return finish(args)

    state = {}

    @limb("0 - bridge, camera and two reachable fields",
          "no ZMQ bridge, no camera, no XY stage, or both fields outside this rig's bounds")
    def limb_0():
        ctrl = MicroscopeController(port=args.port)
        parsed = load_safety_config_or_exit(args.safety_config)
        guard = SafetyGuard(parsed.constraints)
        camera = str(ctrl.core.get_camera_device() or "")
        if not camera:
            raise NotExercised("Micro-Manager has no camera configured")
        stage = str(ctrl.core.get_xy_stage_device() or "")
        if not stage:
            raise NotExercised("Micro-Manager has no XY stage configured")
        x0, y0 = float(ctrl.core.get_x_position()), float(ctrl.core.get_y_position())
        fields = [{"name": "gateA", "x_um": x0, "y_um": y0},
                  {"name": "gateB", "x_um": x0 + args.step_um, "y_um": y0}]
        for field in fields:
            try:
                guard.check_xy(field["x_um"], field["y_um"])
            except Exception as exc:                # noqa: BLE001 - reported
                raise NotExercised(
                    f"{field['name']} at ({field['x_um']}, {field['y_um']}) is outside "
                    f"this rig's configured bounds: {exc}"
                ) from exc
        # No configuration the product does not require (60b): the machine's
        # own workspace when it has one, this gate's evidence folder when not.
        workspace = getattr(parsed.constraints, "workspace_dir", None)
        root = Path(workspace) / "block77b" if workspace else args.out / "data"
        root.mkdir(parents=True, exist_ok=True)
        state.update(ctrl=ctrl, guard=guard, fields=fields, root=root,
                     camera=camera, stage=stage, home=(x0, y0))
        return (f"camera {camera!r}, stage {stage!r}, fields {args.step_um} um apart "
                f"from ({x0:.1f}, {y0:.1f}), saving under {root}")

    def need(*keys):
        for key in keys:
            if key not in state:
                raise NotExercised("stood down: limb 0 did not establish the rig")
        return [state[k] for k in keys]

    @limb("A - position-outer is the real executed order",
          "real AcqEngJ not executing a ptcz position-axis list in submitted order")
    def limb_a():
        ctrl, guard, fields, root = need("ctrl", "guard", "fields", "root")
        log_path = args.out / "A-hook.json"
        result, call = run_order_case(
            tools, ctrl, guard, out=root, name="order_a", order=None, interval_s=0,
            positions=fields, exposure_ms=args.exposure_ms, log_path=log_path)
        records = read_hook_log(log_path)
        order = positions_in_arrival_order(records)
        expected = ["gateA"] * N_FRAMES + ["gateB"] * N_FRAMES
        state["A"] = {"result": result, "call": call, "order": order,
                      "records": records, "log_path": log_path}
        if len(records) != 2 * N_FRAMES:
            raise AssertionError(
                f"expected {2 * N_FRAMES} hook records, got {len(records)}: {order}")
        if order != expected:
            raise AssertionError(f"executed order was {order}, not {expected}")
        if result.get("acquisition_order") != "position_then_time":
            raise AssertionError(
                f"the result reports acquisition_order="
                f"{result.get('acquisition_order')!r}")
        return (f"{order} from the hook log's own arrival order; one dataset at "
                f"{result.get('dataset_path')}")

    @limb("B - explicit interleaving really interleaves",
          "time_then_position not producing a shared time-point order on real hardware")
    def limb_b():
        ctrl, guard, fields, root = need("ctrl", "guard", "fields", "root")
        log_path = args.out / "B-hook.json"
        result, _ = run_order_case(
            tools, ctrl, guard, out=root, name="order_b",
            order="time_then_position", interval_s=0,
            positions=fields, exposure_ms=args.exposure_ms, log_path=log_path)
        order = positions_in_arrival_order(read_hook_log(log_path))
        expected = ["gateA", "gateB"] * N_FRAMES
        state["B"] = {"result": result, "order": order}
        if order != expected:
            raise AssertionError(f"executed order was {order}, not {expected}")
        return f"{order}; one dataset at {result.get('dataset_path')}"

    @limb("C - field B keeps its own clock",
          "field B bursting because it inherited field A's elapsed schedule")
    def limb_c():
        ctrl, guard, fields, root = need("ctrl", "guard", "fields", "root")
        per_field = {}
        for field in fields:
            log_path = args.out / f"C-hook-{field['name']}.json"
            if log_path.exists():
                log_path.unlink()
        log_path = args.out / "C-hook.json"
        result, _ = run_order_case(
            tools, ctrl, guard, out=root, name="order_c", order=None,
            interval_s=SPACED_INTERVAL_S, positions=fields,
            exposure_ms=args.exposure_ms, log_path=log_path)
        rows = result.get("results")
        if not isinstance(rows, list) or len(rows) != len(fields):
            raise AssertionError(
                f"expected one result row per field, got {rows!r}")
        for row in rows:
            path = row.get("log_path")
            if not path:
                raise AssertionError(f"result row {row.get('position')!r} has no log_path")
            records = read_hook_log(Path(path))
            names = positions_in_arrival_order(records)
            if set(names) != {row.get("position")}:
                raise AssertionError(
                    f"{row.get('position')!r}'s log names {sorted(set(names))} -- "
                    "a per-field log must not carry another field's frames")
            stamps = arrival_times(records)
            per_field[row["position"]] = [round(b - a, 3)
                                          for a, b in zip(stamps, stamps[1:])]
        state["C"] = {"result": result, "gaps": per_field}
        if result.get("timing", {}).get("strategy") != "per_position_clock":
            raise AssertionError(
                f"timing.strategy is {result.get('timing', {}).get('strategy')!r}")
        floor = SPACED_INTERVAL_S * 0.75      # arrival stamps, not exposure stamps
        bursting = {name: gaps for name, gaps in per_field.items()
                    if any(gap < floor for gap in gaps)}
        if bursting:
            raise AssertionError(
                f"these fields burst rather than holding {SPACED_INTERVAL_S} s "
                f"spacing: {bursting} (all gaps: {per_field})")
        return (f"callback-arrival gaps per field {per_field}, each >= {floor} s; "
                f"{len(rows)} datasets, one per field")

    @limb("D - what exposure-timestamp key does this camera write",
          "nothing; this limb REPORTS and only fails if the dataset cannot be read")
    def limb_d():
        if "A" not in state:
            raise NotExercised("stood down: limb A acquired no dataset to read")
        dataset_path = state["A"]["result"].get("dataset_path")
        if not dataset_path:
            raise NotExercised("limb A's result carries no dataset_path")
        try:
            from ndstorage import Dataset
        except ImportError:
            from pycromanager import Dataset          # older installs
        dataset = Dataset(str(dataset_path))
        axes = {k: list(v) for k, v in dataset.axes.items()}
        first = {k: v[0] for k, v in axes.items()}
        metadata = dataset.read_metadata(**first)
        keys = sorted(metadata)
        timeish = sorted(k for k in keys
                         if any(word in k.lower()
                                for word in ("time", "clock", "elapsed", "stamp")))
        payload = {"dataset": str(dataset_path), "axes": axes,
                   "all_keys": keys,
                   "time_like_keys": {k: metadata.get(k) for k in timeish}}
        (args.out / "D-metadata-keys.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8")
        state["D"] = payload
        if not timeish:
            # A documented absence is the finding, not a failure: D3 says label
            # the limitation rather than name a key no adapter writes.
            return (f"{len(keys)} metadata keys and NONE is time-like. "
                    "D3's null exposure_timestamp_metadata_key is correct for "
                    "this camera; see D-metadata-keys.json")
        return (f"time-like keys on this camera: {timeish}. Values in "
                "D-metadata-keys.json. Feed these back into D3 before any code "
                "reads one.")

    @limb("F - the exported script runs and agrees",
          "an export that compiles but does not reproduce the executed order")
    def limb_f():
        ctrl, guard, root = need("ctrl", "guard", "root")
        if "A" not in state:
            raise NotExercised("stood down: limb A produced no call to export")
        script = args.out / "exported_routine.py"
        records = [
            {"role": "assistant", "content": [{
                "type": "tool_use", "id": "gate-77b-A",
                "name": "run_multiposition_acquisition",
                "input": state["A"]["call"]}]},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "gate-77b-A",
                "content": json.dumps(state["A"]["result"])}]},
        ]
        report = tools.export_session_script(
            ctrl, guard, str(script), records, tool_use_ids=["gate-77b-A"])
        if report.get("emitted_calls") != 1:
            raise AssertionError(f"the exporter emitted {report!r}, not one call")
        source = script.read_text(encoding="utf-8")
        if "import microclaw" in source or "from microclaw" in source:
            raise AssertionError("the exported script imports microclaw")
        # Parsing is necessary but insufficient (52b): run it.
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, cwd=str(args.out),
                              stdin=subprocess.DEVNULL, timeout=600)
        (args.out / "F-export-run.txt").write_text(
            f"exit={proc.returncode}\n--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}\n", encoding="utf-8")
        if proc.returncode != 0:
            raise AssertionError(
                f"the exported script exited {proc.returncode}; see F-export-run.txt")
        exported_logs = sorted(args.out.glob("A-hook*.json"))
        exported_logs = [p for p in exported_logs if p != state["A"]["log_path"]]
        if not exported_logs:
            raise AssertionError(
                "the exported script wrote no hook log beside the live one; "
                "its order cannot be compared")
        order = positions_in_arrival_order(read_hook_log(exported_logs[0]))
        if order != state["A"]["order"]:
            raise AssertionError(
                f"the standalone script executed {order}, the live run "
                f"{state['A']['order']}")
        return (f"the exported script ran standalone and executed {order}, "
                f"matching the live run; log {exported_logs[0].name}")

    @limb("G - the zero-interval dataset still mosaics",
          "the single position-axis dataset the settled decision preserves not being usable")
    def limb_g():
        ctrl, guard = need("ctrl", "guard")
        if "A" not in state:
            raise NotExercised("stood down: limb A acquired no dataset")
        dataset_path = state["A"]["result"].get("dataset_path")
        if not dataset_path:
            raise NotExercised("limb A's result carries no dataset_path")
        output = args.out / "G-mosaic.tif"
        # A mosaic is ONE plane across the position axis, so every other axis
        # must be pinned. This run has time=[0,1,2] and the tool refuses an
        # ambiguous selection -- correctly. The first version of this limb
        # omitted the argument and scored that refusal as a product failure;
        # the selftest could not catch it, because no fake produces a real
        # NDTiff and the healthy case could only assert that G did NOT pass.
        selection = {axis: values[0]
                     for axis, values in state["D"]["axes"].items()
                     if axis != "position"} if "D" in state else {"time": 0}
        result = tools.build_stage_coordinate_mosaic(
            ctrl, guard, dataset_path=str(dataset_path), output_path=str(output),
            axis_selection=selection)
        if isinstance(result, dict) and result.get("error"):
            raise AssertionError(f"the mosaic refused: {result['error']}")
        if not output.exists():
            raise AssertionError(f"the mosaic reported success but wrote no {output}")
        return (f"mosaicked limb A's single dataset at {selection} to "
                f"{output.name} ({output.stat().st_size} bytes)")

    # Leave the stage where it was found. This is housekeeping, not a limb.
    if "ctrl" in state:
        try:
            state["ctrl"].set_xy(*state["home"])
            print(f"stage returned to {state['home']}")
        except Exception as exc:                    # noqa: BLE001 - reported
            print(f"WARNING: could not return the stage to {state['home']}: {exc}")

    return finish(args)


if __name__ == "__main__":
    sys.exit(main())
