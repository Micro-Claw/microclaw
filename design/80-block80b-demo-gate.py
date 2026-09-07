"""Block 80b demo-machine gate: does the emitted hooked script actually run?

Every limb computes or drives an acquisition this program owns, so it ships as a
program rather than pasted PowerShell blocks (58a): each limb scores itself
independently, one FAIL cannot hide the rest behind a cascade, the gate owns its
log, and the exit status is nonzero on any FAIL *or* any NOT EXERCISED.

What this gate does NOT test, because it is settled off-rig and was verified by
mutation and by independent watch-it-fail during review. Do not add a limb for
any of it:

* All four built-ins emit, and the hookless export stays byte-identical.
* The wrapper with no recorded `hook_strategy` still gets the hook runtime.
* An incomplete used-axis envelope refuses at export, per axis actually used.
* The seed preflight precedes every hardware write.
* Live-vs-export state writes and logs, against a dispatching fake engine.
* **That `post_hardware_hook_fn` is AcqEngJ's `AFTER_HARDWARE_HOOK`**, and that
  `AutofocusHook.post_hardware_hook_fn` already handles a batched event list --
  both read off the installed dependency and the hook source on 2026-09-07 and
  recorded in design/80. A rig cannot say it better than the source does.

What needs the bridge is exactly one thing, and it is the thing this block is
for: **an exported script that compiles is not an exported script that works**
(52b, which cost three M5 trips). Limbs C and D exec the emitted script in a
child process against this same bridge and compare it with the live run.

It requires **no configuration the product does not require** (60b): no
`--safety-config`, no workspace. Datasets go under the machine's configured
`workspace_dir` when it has one, else under `--out`.

`NOT EXERCISED` is never a pass. If the stage cannot move inside this machine's
bounds, the limbs that need motion say exactly that and the gate exits nonzero.
Do not tick them.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(os.environ.get("MICROCLAW_TREE_UNDER_TEST") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))

RESULTS = []
HOOKLESS_SHA = "9a56d7a9ca1e93e173cb4358712d94f4b837dfcfe7a933010af9f4df033b0826"


class NotExercised(Exception):
    """The limb could not run its mechanism. Never a pass (58a)."""


class StandDown(Exception):
    """The control says this tree is not the one under test."""


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

    Read tolerantly for the same reason 52b's export died on one embedded
    newline: report a torn file rather than losing every record in it.
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


def autofocus_records(records: list[dict]) -> list[dict]:
    """Records that report a completed sweep, in arrival order.

    Scored on `best_z_um`, which only the sweep writes. A record carrying
    `autofocus: "skipped"` is a real outcome and is counted separately -- it is
    the hook's own bounds refusal, not a missing callback.
    """
    return [r for r in records if r.get("best_z_um") is not None]


def skipped_records(records: list[dict]) -> list[dict]:
    return [r for r in records if r.get("autofocus") == "skipped"]


def export_hooked(tools, ctrl, guard, out: Path, name: str) -> tuple[dict, str, Path]:
    """Export THIS session and return (result, source, path).

    export_session_script compiles *this session's* recorded calls, so this must
    run after the live acquisition -- a fresh session emits a 13-line stub (52b).
    """
    path = out / f"{name}.py"
    result = tools.execute_tool(
        ctrl, guard, "export_session_script", {"output_path": str(path)},
    ) if hasattr(tools, "execute_tool") else None
    return result, path.read_text(encoding="utf-8") if path.exists() else "", path


def emitter_probe(tools):
    """Does this build emit a hooked fixed plan at all? No bridge needed.

    The control, and it fires: on a pre-80b tree this raises CannotEmit, which
    is the whole behaviour the block changed. Driven through the emitter the way
    the suite drives it, so limb E can be scored before the bridge is touched.
    """
    params = tools.RecordedParams({
        "protocol": "timelapse",
        "positions": [{"name": "a", "x_um": 1.0, "y_um": 2.0, "z_um": 3.0}],
        "protocol_params": {"n_frames": 1, "interval_s": 0, "exposure_ms": 10},
        "save_dir": "/data", "name": "probe",
        "hook_strategy": "autofocus_per_position",
        "hook_params": {"z_range_um": 4, "z_step_um": 1},
        "_export_safety_limits": {
            "x_um": (-1000.0, 1000.0), "y_um": (-1000.0, 1000.0),
            "z_um": (-1000.0, 1000.0), "exposure_ms": (0.0, None),
            "analysis_min_snr": None, "z_move_tolerance_um": None,
            "x_move_tolerance_um": None, "y_move_tolerance_um": None,
            "named_stage_move_tolerances_um": {},
        },
    }, {"results": [{"dataset_path": "/data/probe"}]})
    params["_tool_use_id"] = "toolu_probe"
    emitter = getattr(tools.run_multiposition_acquisition, "_microclaw_emitter", None)
    if emitter is None:
        raise AssertionError("run_multiposition_acquisition carries no @emits emitter")
    try:
        return emitter(params)
    except tools.CannotEmit as exc:
        return f"REFUSED: {exc}"


def hookless_checksum(tools, guard, path: Path) -> tuple[str, int]:
    """The regression that matters most: a plain grid must not change at all.

    Checksummed over the WHOLE exported file, because that is what the recorded
    constant was measured over -- the emitter's body alone would never match it,
    and a checksum compared against the wrong thing is worse than none.
    """
    records = [{"role": "assistant", "content": [{
        "type": "tool_use", "id": "toolu_hookless",
        "name": "run_multiposition_acquisition",
        "input": {"protocol": "timelapse",
                  "positions": [{"name": "a", "x_um": 1, "y_um": 2, "z_um": 3},
                                {"name": "b", "x_um": 11, "y_um": 2, "z_um": 3}],
                  "protocol_params": {"n_frames": 1, "interval_s": 0},
                  "save_dir": "/data", "name": "run"},
    }]}]
    tools.export_session_script(None, guard, str(path), records)
    source = path.read_text(encoding="utf-8")
    return hashlib.sha256(source.encode()).hexdigest(), len(source.splitlines())


def finish(args):
    log = args.log or (args.out / "score.json")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    print("\n=== BLOCK 80b DEMO GATE ===")
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
    parser.add_argument("--out", type=Path, default=Path("block80b-evidence"))
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--exposure-ms", type=float, default=10.0)
    parser.add_argument("--step-um", type=float, default=20.0,
                        help="XY offset between the gate's two fields.")
    parser.add_argument("--z-range-um", type=float, default=4.0)
    parser.add_argument("--z-step-um", type=float, default=1.0)
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, args.out / "gate.txt")
    print(f"Tree under test: {ROOT}")

    from microclaw import tools
    from microclaw.config import load_safety_config_or_exit
    from microclaw.controller import MicroscopeController
    from microclaw.safety import SafetyGuard

    LATER = ("0 - bridge, camera, and a reachable field pair",
             "A - a live hooked autofocus grid focuses at every position",
             "B - the exported script is standalone and inlines the real hook",
             "C - the exported script RUNS and agrees with the live run",
             "D - post_hardware_hook_fn really fired under real AcqEngJ",
             "F - an image hook round-trips too",
             "G - the hookless grid is byte-identical")

    # E first, and it is the control: it needs no bridge and it FIRES on a
    # pre-80b tree, where this emitter refuses. 58a's lesson is that a limb
    # which cannot fail is not a criterion.
    @limb("E - this build emits a hooked fixed plan (CONTROL)",
          "a pre-80b tree, where the fixed-plan emitter refuses every hook")
    def limb_e():
        rendered = emitter_probe(tools)
        if rendered.startswith("REFUSED: "):
            raise AssertionError(
                "the fixed-plan emitter still refuses a hooked plan, so this is "
                "not a tree with 80b in it: " + rendered[len("REFUSED: "):])
        if "class AutofocusHook" not in rendered and "AutofocusHook(" not in rendered:
            raise AssertionError("it emitted, but without the autofocus hook in it")
        return "the emitter renders an autofocus_per_position fixed plan"

    if RESULTS[0]["status"] != "PASS":
        for name in LATER:
            RESULTS.append({"name": name, "status": "NOT EXERCISED",
                            "detail": "stood down: limb E found no 80b in this build",
                            "fails_if": "n/a"})
            print(f"NOT EXERCISED: {name} - stood down by limb E")
        return finish(args)

    state = {}

    @limb("0 - bridge, camera, and a reachable field pair",
          "no ZMQ bridge, no camera, no XY or Z stage, or fields outside this rig's bounds")
    def limb_0():
        ctrl = MicroscopeController(port=args.port)
        parsed = load_safety_config_or_exit(None)
        guard = SafetyGuard(parsed.constraints)
        camera = str(ctrl.core.get_camera_device() or "")
        if not camera:
            raise NotExercised("Micro-Manager has no camera configured")
        stage = str(ctrl.core.get_xy_stage_device() or "")
        if not stage:
            raise NotExercised("Micro-Manager has no XY stage configured")
        focus = str(ctrl.core.get_focus_device() or "")
        if not focus:
            raise NotExercised("Micro-Manager has no focus device; autofocus cannot run")
        x0, y0 = float(ctrl.core.get_x_position()), float(ctrl.core.get_y_position())
        z0 = float(ctrl.core.get_position())
        fields = [{"name": "gateA", "x_um": x0, "y_um": y0, "z_um": z0},
                  {"name": "gateB", "x_um": x0 + args.step_um, "y_um": y0, "z_um": z0}]
        for field in fields:
            try:
                guard.check_xy(field["x_um"], field["y_um"])
                guard.check_z(field["z_um"])
            except Exception as exc:                # noqa: BLE001 - reported
                raise NotExercised(
                    f"{field['name']} at ({field['x_um']}, {field['y_um']}, "
                    f"{field['z_um']}) is outside this rig's configured bounds: {exc}"
                ) from exc
        # The sweep itself must fit the envelope, or limb A measures the hook's
        # own skip path rather than a focus run.
        try:
            guard.check_z(z0 - args.z_range_um / 2)
            guard.check_z(z0 + args.z_range_um / 2)
        except Exception as exc:                    # noqa: BLE001 - reported
            raise NotExercised(
                f"a {args.z_range_um} um sweep centred on {z0} leaves this rig's Z "
                f"bounds: {exc}. Lower --z-range-um and re-run."
            ) from exc
        workspace = getattr(parsed.constraints, "workspace_dir", None)
        root = Path(workspace) / "block80b" if workspace else args.out / "data"
        root.mkdir(parents=True, exist_ok=True)
        state.update(ctrl=ctrl, guard=guard, fields=fields, root=root,
                     camera=camera, stage=stage, focus=focus, home=(x0, y0, z0),
                     parsed=parsed)
        return (f"camera {camera!r}, XY {stage!r}, focus {focus!r}; fields "
                f"{args.step_um} um apart from ({x0:.1f}, {y0:.1f}, {z0:.1f}); "
                f"saving under {root}")

    def need(*keys):
        for key in keys:
            if key not in state:
                raise NotExercised("stood down: limb 0 did not establish the rig")
        return [state[k] for k in keys]

    @limb("A - a live hooked autofocus grid focuses at every position",
          "the hook not running once per position, or reporting no best Z")
    def limb_a():
        ctrl, guard, fields, root = need("ctrl", "guard", "fields", "root")
        log_path = args.out / "A-live-hook.json"
        # One dict drives the live call AND limb B's export, so the exported
        # script cannot be reproducing arguments the run did not make.
        call_input = dict(
            protocol="timelapse", positions=fields, save_dir=str(root),
            name="live_af", hook_strategy="autofocus_per_position",
            hook_params={"z_range_um": args.z_range_um,
                         "z_step_um": args.z_step_um},
            log_path=str(log_path),
            protocol_params={"n_frames": 1, "interval_s": 0,
                             "exposure_ms": args.exposure_ms},
        )
        result = tools.run_multiposition_acquisition(ctrl, guard, **call_input)
        if "error" in result:
            raise AssertionError(f"the live run returned an error: {result['error']}")
        records = read_hook_log(log_path)
        swept, skipped = autofocus_records(records), skipped_records(records)
        state["A"] = {"result": result, "records": records, "log": log_path,
                      "swept": swept, "call_input": call_input}
        if skipped:
            raise AssertionError(
                f"{len(skipped)} of {len(records)} positions SKIPPED autofocus: "
                f"{[r.get('reason') for r in skipped]}. The hook refused its own "
                "bounds, so this limb measured the skip path, not a focus run.")
        if len(swept) != len(fields):
            raise AssertionError(
                f"expected one completed sweep per position ({len(fields)}), got "
                f"{len(swept)} from {len(records)} records")
        zs = [r["best_z_um"] for r in swept]
        return (f"{len(swept)} sweeps, best Z {zs}, converged "
                f"{[r.get('converged') for r in swept]}; dataset "
                f"{result.get('dataset_path')}")

    @limb("B - the exported script is standalone and inlines the real hook",
          "an export that imports microclaw, hand-writes a sweep, or drops the guard")
    def limb_b():
        ctrl, guard = need("ctrl", "guard")
        if "A" not in state:
            raise NotExercised("stood down: limb A did not run, so there is no session to export")
        path = args.out / "C-exported.py"
        # export_session_script compiles a SUPPLIED record, and this process is
        # not an agent session, so the gate hands it the one call limb A made --
        # built from limb A's own call_input, never retyped (52b).
        records = [
            {"role": "assistant", "content": [{
                "type": "tool_use", "id": "toolu_gate_af",
                "name": "run_multiposition_acquisition",
                "input": state["A"]["call_input"],
            }]},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "toolu_gate_af",
                "content": json.dumps(state["A"]["result"]),
            }]},
        ]
        result = tools.export_session_script(ctrl, guard, str(path), records)
        state["B"] = {"result": result, "path": path}
        if not result.get("complete", False):
            raise AssertionError(
                f"the export reports incomplete: {result.get('not_emitted_calls')}")
        source = path.read_text(encoding="utf-8")
        ast.parse(source)
        leaked = [l for l in source.splitlines()
                  if re.match(r"\s*(from|import)\s+microclaw", l)]
        if leaked:
            raise AssertionError(f"the export imports microclaw: {leaked}")
        for needed in ("class AutofocusHook", "def coarse_then_fine_autofocus",
                       "_LIMITS = ", "guard.check_xy(", "post_hardware_hook_fn"):
            if needed not in source:
                raise AssertionError(f"the exported script is missing {needed!r}")
        state["B"]["source"] = source
        return (f"{len(source.splitlines())} lines, parses, imports no microclaw, "
                f"carries the inlined hook, its recorded bounds and the preflight")

    @limb("C - the exported script RUNS and agrees with the live run",
          "an exported script that compiles but does not work (52b)")
    def limb_c():
        need("ctrl")
        if "B" not in state or "source" not in state.get("B", {}):
            raise NotExercised("stood down: limb B produced no runnable script")
        path = state["B"]["path"]
        # Run it in a CHILD process, from its own directory: the emitted script
        # resolves _HERE against its own file and must not need this process.
        proc = subprocess.run(
            [sys.executable, str(path)], cwd=str(path.parent),
            capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL,
        )
        state["C"] = {"proc": proc}
        (args.out / "C-stdout.txt").write_text(proc.stdout, encoding="utf-8")
        (args.out / "C-stderr.txt").write_text(proc.stderr, encoding="utf-8")
        if proc.returncode != 0:
            raise AssertionError(
                f"the exported script exited {proc.returncode}. stderr tail: "
                + " | ".join(proc.stderr.strip().splitlines()[-4:]))
        logs = sorted(path.parent.glob("A-live-hook*.json"))
        standalone = [p for p in logs if p != state["A"]["log"]]
        if not standalone:
            raise NotExercised(
                "the standalone run wrote no hook log beside the script; "
                f"looked for A-live-hook*.json in {path.parent}")
        records = read_hook_log(standalone[-1])
        swept = autofocus_records(records)
        live_swept = state["A"]["swept"]
        state["C"]["swept"] = swept
        if len(swept) != len(live_swept):
            raise AssertionError(
                f"the standalone run completed {len(swept)} sweeps against the live "
                f"run's {len(live_swept)}")
        return (f"exit 0; {len(swept)} sweeps standalone against {len(live_swept)} "
                f"live; best Z standalone {[r['best_z_um'] for r in swept]} vs live "
                f"{[r['best_z_um'] for r in live_swept]}")

    @limb("D - post_hardware_hook_fn really fired under real AcqEngJ",
          "a standalone run that saves frames while the hook never runs")
    def limb_d():
        if "C" not in state:
            raise NotExercised("stood down: limb C did not run the exported script")
        out = state["C"]["proc"].stdout
        counts = [l for l in out.splitlines() if "HOOK ACQUISITION COUNTS" in l]
        if not counts:
            raise AssertionError(
                "the exported script printed no HOOK ACQUISITION COUNTS line, so "
                "its own accounting cannot be read")
        # Scored on hook_exposures, which the HOOK increments through its
        # reservation -- never on saved_frames, which arrives on the callback
        # CLAUDE.md's eighth contract says a predicate must not be gated on.
        exposures = [int(m) for m in re.findall(r"hook_exposures=\s*(\d+)", out)]
        if not exposures:
            raise AssertionError(f"could not read hook_exposures from: {counts}")
        if not any(n > 0 for n in exposures):
            raise AssertionError(
                "hook_exposures is 0 in the standalone run: real AcqEngJ never "
                "invoked post_hardware_hook_fn, so the frames are unfocused while "
                "the script reports success. This is the defect the block exists "
                "to prevent.")
        envelope = [l for l in out.splitlines() if "no dose budget" in l]
        if not envelope:
            raise AssertionError(
                "the script did not disclose that it enforces no dose budget")
        return (f"hook_exposures {exposures}; the envelope discloses no dose "
                f"budget in {len(envelope)} line(s)")

    @limb("F - an image hook round-trips too",
          "an image_process_fn hook that emits but does not run standalone")
    def limb_f():
        ctrl, guard, fields, root = need("ctrl", "guard", "fields", "root")
        log_path = args.out / "F-live-hook.json"
        call_input = dict(
            protocol="timelapse", positions=fields, save_dir=str(root),
            name="live_intensity", hook_strategy="intensity_adaptive",
            hook_params={"target_mean": 120}, log_path=str(log_path),
            protocol_params={"n_frames": 1, "interval_s": 0,
                             "exposure_ms": args.exposure_ms},
        )
        result = tools.run_multiposition_acquisition(ctrl, guard, **call_input)
        if "error" in result:
            raise AssertionError(f"the live run returned an error: {result['error']}")
        live = read_hook_log(log_path)
        path = args.out / "F-exported.py"
        records = [
            {"role": "assistant", "content": [{
                "type": "tool_use", "id": "toolu_gate_int",
                "name": "run_multiposition_acquisition", "input": call_input}]},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "toolu_gate_int",
                "content": json.dumps(result)}]},
        ]
        export = tools.export_session_script(ctrl, guard, str(path), records)
        if not export.get("complete", False):
            raise AssertionError(f"incomplete export: {export.get('not_emitted_calls')}")
        proc = subprocess.run([sys.executable, str(path)], cwd=str(path.parent),
                              capture_output=True, text=True, timeout=900,
                              stdin=subprocess.DEVNULL)
        (args.out / "F-stdout.txt").write_text(proc.stdout, encoding="utf-8")
        (args.out / "F-stderr.txt").write_text(proc.stderr, encoding="utf-8")
        if proc.returncode != 0:
            raise AssertionError(
                f"the exported script exited {proc.returncode}. stderr tail: "
                + " | ".join(proc.stderr.strip().splitlines()[-4:]))
        standalone = [p for p in sorted(path.parent.glob("F-live-hook*.json"))
                      if p != log_path]
        if not standalone:
            raise NotExercised("the standalone intensity run wrote no hook log")
        emitted = read_hook_log(standalone[-1])
        return (f"live {len(live)} record(s), standalone {len(emitted)}; live "
                f"exposures {[r.get('new_exposure_ms') for r in live]} vs standalone "
                f"{[r.get('new_exposure_ms') for r in emitted]}")

    @limb("G - the hookless grid is byte-identical",
          "80b having changed a plain multiposition export")
    def limb_g():
        (guard,) = need("guard")
        digest, lines = hookless_checksum(tools, guard, args.out / "G-hookless.py")
        if digest != HOOKLESS_SHA:
            raise AssertionError(
                f"the hookless export is {digest} over {lines} lines, not the "
                f"recorded {HOOKLESS_SHA}. A plain SMLM grid changed.")
        return f"sha256 {digest[:16]}... over {lines} lines, unchanged"

    # No teardown here on purpose: MicroscopeController has no close(), and
    # microclaw writes nothing to Micro-Manager on any exit path.
    return finish(args)


if __name__ == "__main__":
    raise SystemExit(main())
