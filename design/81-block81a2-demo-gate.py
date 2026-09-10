r"""Does a required autofocus failure stop the run before it exposes the field?

Demo machine. About fifteen minutes. DemoCamera, so no dose that matters, and
its trigger drives nothing. **Firefox is this machine's browser; nothing here
needs one.**

Block 81a-2 makes a per-position autofocus hook FAIL CLOSED. Before it, a hook
whose sweep window fell outside the Z guard logged `autofocus: skipped`,
returned the event unmodified, and the field was exposed at whatever plane the
stage happened to hold -- while the tool reported success. That is the
2026-09-09 bead incident: nine fields exposed for nothing, and a status string
saying `Acquisition complete across 9 position(s).`

Everything about the refusal itself is settled off-rig, by 52 local tests and
by mutation. **Three things are not, and they are why this trip exists:**

  * that a real focus curve on real hardware CONVERGES through the new code
    path, records its provenance, and that the recorded numbers agree with each
    other (`R101` is an open register row saying the emitted autofocus hook has
    never converged on a real curve);
  * that the refusal reaches the engine's hook thread and stops the run
    BEFORE the frame is written -- a fake cannot prove that, because design/27's
    ghost exposure is exactly a fake-shaped assumption about hook returns;
  * that the EXPORTED script still runs. Block 52b spent three M5 trips on the
    export alone and two of those defects survived compilation and every grep.
    This limb is also 81a-1's owed export evidence (operator decision,
    2026-09-09).

Run:

    uv run python -c "print('uv warm')"
    uv run python design\81-block81a2-demo-gate.py --out block81a2-evidence

Every limb reports INDEPENDENTLY, the script owns its own log (PowerShell does
not capture a native child process's stdout), and the exit status is nonzero on
any FAIL *or* any NOT EXERCISED. **NOT EXERCISED is never a pass.**

What this gate deliberately does NOT test, because a rig cannot say it better
than the 16 local combinations already do: forcing a selected measured
coordinate OUTSIDE its sweep window (D3(d)). Real hardware will not reliably
produce that reading on demand, and inventing it here would be a fake wearing a
rig's clothes. What this gate DOES collect is the positive control -- that
every converged sweep records commanded, measured and window, and that measured
sits inside window -- reported as an observation, not a verdict.

Prerequisites: Micro-Manager open, this machine's usual demo config loaded, and
the pycro-manager bridge running ("Run server on port 4827", Tools -> Options).
The config needs a camera, an XY stage and a focus device, and the safety config
needs finite Z bounds. Any of those missing is NOT EXERCISED naming which.
This gate requires no configuration the product does not require, and changes
no durable state: it restores the focus axis and writes only under --out.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS = []
OBSERVATIONS = []


class NotExercised(Exception):
    """The limb could not run its mechanism. Never a pass (58a)."""


class Tee:
    def __init__(self, stream, path):
        self.stream, self.file = stream, open(path, "a", encoding="utf-8")

    def write(self, data):
        self.stream.write(data); self.file.write(data); return len(data)

    def flush(self):
        self.stream.flush(); self.file.flush()


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


def observe(name, detail):
    """A reading this trip collects but does not score (58a: a limb that
    cannot fail is not a criterion)."""
    OBSERVATIONS.append({"name": name, "detail": detail})
    print(f"OBSERVATION: {name} - {detail}")


def read_log(path: Path) -> list[dict]:
    """The hook log in ARRIVAL order -- that order is the evidence."""
    if not path.exists():
        raise NotExercised(f"no hook log at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def outcomes(records: list[dict]) -> dict:
    counts = {}
    for row in records:
        key = row.get("autofocus")
        if key is not None:
            counts[key] = counts.get(key, 0) + 1
    return counts


def dataset_frames(tools, path: Path) -> int:
    """Frames actually on disk, counted the way the product counts them.

    `tools._iter_present_coords` is the in-tree enumerator for a sparse NDTiff
    dataset. Established, not guessed: an earlier draft of this gate called a
    `Dataset.get_index_keys()` that does not exist, and would have died here on
    the demo machine.
    """
    from ndstorage import Dataset
    try:
        return sum(1 for _ in tools._iter_present_coords(Dataset(str(path)), {}))
    except Exception as exc:                        # noqa: BLE001 - reported
        raise NotExercised(f"could not read dataset {path}: {exc}")


REQUIRED_AUTOFOCUS_REFUSAL = "Required autofocus stopped"


PLAN_TIME_REACH_REFUSAL = "planned post_hardware_hook_fn sweep would reach"


def refusal_kind(message: str):
    """Which autofocus refusal stopped this run, or None.

    Two protect the sample and BOTH are correct answers for limb B. Round 3
    expected only the runtime one and scored the plan-time one NOT EXERCISED,
    which understated the product: refusing before the run even starts is the
    better outcome, and it is D3(a) rather than D3(b).
    """
    text = str(message)
    if PLAN_TIME_REACH_REFUSAL in text:
        return "plan-time reach"
    if is_the_safety_refusal(text):
        return "runtime required-autofocus"
    return None


def is_the_safety_refusal(message: str) -> bool:
    """Did the run fail through the mechanism this gate exists to observe?

    Module level so the selftest can CALL it rather than grep for it. Round 1
    (2026-09-10) passed limb B on `cannot import name planned_hook_z_reach`,
    because "an error came back and no frames were written" is satisfied by
    any failure at all -- and a source-text check for the right string is
    fooled by the string merely being present.
    """
    return REQUIRED_AUTOFOCUS_REFUSAL in str(message)


def grid(z_um, step_um, x_um, y_um, n=2):
    return [{"name": f"f{i}", "x_um": x_um + i * step_um, "y_um": y_um,
             "z_um": z_um} for i in range(n)]


def export_and_run(tools, guard, params, out: Path, label: str):
    """Emit the standalone script for this recorded call, then RUN it.

    Compiling is not running: 52b's rule. The child gets a closed stdin, because
    a subprocess inherits stdin even when its output is captured and a CLI that
    registers an exit pause would hang forever (58e, measured).
    """
    records = [{"role": "assistant", "content": [{
        "type": "tool_use", "id": f"toolu_{label}",
        "name": "run_multiposition_acquisition", "input": params,
    }]}]
    script = out / f"{label}.py"
    result = tools.export_session_script(None, guard, str(script), records)
    if not result.get("complete"):
        raise AssertionError(
            f"the export refused: {result.get('not_emitted_calls')}")
    source = script.read_text(encoding="utf-8")
    if "import microclaw" in source or "from microclaw" in source:
        raise AssertionError("the emitted script imports microclaw; not standalone")
    proc = subprocess.run(
        [sys.executable, str(script)], cwd=str(out), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=900)
    (out / f"{label}.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (out / f"{label}.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    return proc, source


def finish(args):
    log = args.log or (args.out / "score.json")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"limbs": RESULTS, "observations": OBSERVATIONS},
                              indent=2), encoding="utf-8")
    print("\n=== BLOCK 81a-2 DEMO GATE ===")
    for row in RESULTS:
        print(f"  {row['status']:14} {row['name']}")
    for row in OBSERVATIONS:
        print(f"  {'(observation)':14} {row['name']}")
    bad = [r for r in RESULTS if r["status"] != "PASS"]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} PASS; score written to {log}")
    if bad:
        print("NOT A PASS. Report every non-PASS row above verbatim, and send "
              "the whole --out directory: this gate is scored from its "
              "artifacts, not from this summary.")
    return 1 if bad else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--out", type=Path, default=Path("block81a2-evidence"))
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--exposure-ms", type=float, default=10.0)
    parser.add_argument("--step-um", type=float, default=20.0,
                        help="XY offset between the gate's two fields.")
    parser.add_argument("--z-range-um", type=float, default=4.0)
    parser.add_argument("--z-step-um", type=float, default=0.25)
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, args.out / "gate.txt")
    print(f"Tree under test: {ROOT}")

    from microclaw import tools
    from microclaw.hooks import AutofocusHook
    from microclaw.safety import SafetyGuard, SafetyViolation
    from microclaw.config import load_safety_config_or_exit

    coherent = {}

    # ---- TREE, first of all, because a mixed tree makes every later limb a
    # lie. Round 1 (2026-09-10) ran against a checkout whose tools.py carried
    # 81a-2 and whose hooks.py did not -- local edits survive `git checkout
    # <branch>` -- and limbs B and C reported PASS on an ImportError. The
    # runbook did carry a merge-base check; it sat AFTER the run command, and
    # a step written beside the command instead of inside it is a step that
    # does not run.
    @limb("TREE - this checkout actually contains block 81a-2, coherently",
          "a tree without 81a-2, or a MIXED tree with some files updated")
    def limb_tree():
        try:
            from microclaw.hooks import planned_hook_z_reach  # noqa: F401
        except ImportError as exc:
            raise NotExercised(
                f"this checkout does not contain block 81a-2: {exc}. "
                "Nothing below this line tested the product. Run "
                "`git status` (local edits to microclaw/ survive a branch "
                "checkout and produce exactly this mixed tree) and "
                "`git log --oneline -1`, then check out "
                "design81/81a2-runtime-refusal and re-run.")
        import inspect
        from microclaw import tools
        from microclaw.hooks import AutofocusHook
        if "planned_z_reach" not in dir(AutofocusHook):
            raise NotExercised(
                "microclaw.hooks imports but AutofocusHook has no reach "
                "contract: this tree is mixed. See `git status`.")
        if "planned_hook_z_reach" not in inspect.getsource(tools._plan_with_hook_dose):
            raise NotExercised(
                "hooks.py carries 81a-2 but tools.py does not call its reach "
                "check: this tree is mixed. See `git status`.")
        coherent["ok"] = True
        return "hooks.py and tools.py both carry 81a-2"

    def needs_tree():
        if not coherent.get("ok"):
            raise NotExercised(
                "the TREE limb did not pass, so this limb would score an "
                "unrelated failure as evidence. Not a pass.")

    # ---- CONTROL, off-bridge, and it FIRES on a pre-81a-2 tree -------------
    # 58a: a limb that cannot fail is not a criterion. Before this block the
    # hook logged `skipped` and RETURNED THE EVENT, so this raises only with
    # 81a-2 in the tree. Deliberately first and needing no microscope, so a
    # bridge or config problem cannot take the block's central claim with it.
    @limb("CONTROL - a required autofocus refusal raises instead of returning an exposable event",
          "a pre-81a-2 tree, where the hook logs 'skipped' and returns the event")
    def control():
        narrow = SafetyGuard(load_safety_config_or_exit(None).constraints)
        # A window that cannot fit: ask for a sweep far outside any real bound.
        core = SimpleNamespace(
            get_position=lambda *a: 1e9, get_focus_device=lambda: "Z",
            snap_image=lambda: None, wait_for_device=lambda *a: None)
        hook = AutofocusHook(SimpleNamespace(core=core), narrow,
                             z_range_um=args.z_range_um, z_step_um=args.z_step_um,
                             settle_ms=0)
        event = {"axes": {"position": 0}}
        try:
            returned = hook.post_hardware_hook_fn(dict(event))
        except SafetyViolation as exc:
            if "Required autofocus stopped" not in str(exc):
                raise AssertionError(
                    f"it raised, but not the required-autofocus refusal: {exc}")
            return f"raised SafetyViolation: {str(exc)[:110]}"
        raise AssertionError(
            "the hook RETURNED an event instead of raising "
            f"({returned!r}). On this tree that event would have been exposed "
            "at the unfocused plane -- the 2026-09-09 incident.")

    loaded = {}

    def rig():
        """Bridge, controller and guard, loaded once, never exiting the process.

        `load_safety_config_or_exit` raises SystemExit by design, so a gate that
        lets it through prints no score at all.
        """
        if not loaded:
            try:
                from microclaw.controller import MicroscopeController
                config = load_safety_config_or_exit(None)
                ctrl = MicroscopeController(port=args.port)
                loaded.update(ctrl=ctrl, guard=SafetyGuard(config.constraints),
                              config=config)
            except (SystemExit, Exception) as exc:  # noqa: BLE001 - reported
                loaded.update(ctrl=None, guard=None, config=None,
                              error=str(exc) or type(exc).__name__)
        if loaded.get("ctrl") is None:
            raise NotExercised(
                "no bridge/safety config on this machine: "
                + loaded.get("error", "unknown"))
        return loaded["ctrl"], loaded["guard"], loaded["config"]

    @limb("0 - bridge, camera, XY stage, focus device, and finite Z bounds",
          "any of them missing on this machine")
    def limb_0():
        needs_tree()
        ctrl, guard, config = rig()
        missing = []
        if not ctrl.core.get_camera_device():
            missing.append("camera")
        if not ctrl.core.get_xy_stage_device():
            missing.append("XY stage")
        if not ctrl.core.get_focus_device():
            missing.append("focus device")
        if missing:
            raise NotExercised("this config has no " + ", ".join(missing))
        stage = config.constraints.stage
        if stage.z_min is None or stage.z_max is None:
            raise NotExercised(
                "this machine's safety config has no finite Z bounds, and the "
                "reach check is a comparison against them. Set stage.z_min and "
                "stage.z_max. (The PRODUCT requires this too for a reach "
                "check; the gate is not asking for extra configuration.)")
        z = float(ctrl.core.get_position())
        loaded["entry_z"] = z
        span = stage.z_max - stage.z_min
        if span <= args.z_range_um:
            raise NotExercised(
                f"a {args.z_range_um} um sweep does not fit inside this rig's "
                f"{span:.2f} um Z envelope; the gate will not narrow the bounds "
                "to make room.")
        return (f"camera={ctrl.core.get_camera_device()}, "
                f"focus={ctrl.core.get_focus_device()}, Z now {z:.3f} um, "
                f"bounds {stage.z_min}..{stage.z_max}")

    live = {}

    def locate_focus(ctrl, guard, config):
        """Find this camera's focus plane before demanding convergence near it.

        Round 3 and 4 both stood limb A down because the sweep's argmax sat on
        the boundary: the peak is not within 2 um of wherever the stage happens
        to rest. That is a fact about where the stage was parked, not
        necessarily about the camera -- so look first, with ONE wide coarse
        sweep, and centre the real test on what it finds. If the wide sweep's
        argmax is also on ITS boundary, this camera really has no interior
        maximum in the allowed range and limb A stands down honestly.
        """
        from microclaw.autofocus import sweep_autofocus
        stage = config.constraints.stage
        lo, hi = float(stage.z_min), float(stage.z_max)
        span = min(hi - lo, 40.0)
        centre = min(max(loaded["entry_z"], lo + span / 2), hi - span / 2)
        start, end = centre - span / 2, centre + span / 2
        swept = sweep_autofocus(
            ctrl, start, end, span / 20.0, settle_ms=0, move_to_best=False)
        planes, metrics = swept.z_positions, swept.metric_values
        if not planes or len(set(metrics)) < 2:
            raise NotExercised(
                "the focus metric is constant across a "
                f"{span:.1f} um sweep centred at {centre:.2f} um, so this "
                "camera has no focus response at all. R101 needs a rig.")
        best = planes[metrics.index(max(metrics))]
        observe("A - wide focus probe",
                f"{len(planes)} planes over {span:.1f} um, argmax at "
                f"{best:.2f} um, metric range "
                f"{min(metrics):.4g}..{max(metrics):.4g}")
        if best <= planes[0] or best >= planes[-1]:
            raise NotExercised(
                f"even a {span:.1f} um sweep puts the argmax at {best:.2f} um, "
                f"on its own boundary ({planes[0]:.2f}..{planes[-1]:.2f}). "
                "This camera has no interior focus maximum in the allowed Z "
                "range, so convergence cannot be observed here and R101 "
                "stays open for a rig with a real sample.")
        return best

    @limb("A - a real focus curve converges through the new path and records its provenance",
          "autofocus not converging on a machine that HAS a focus response, "
          "or a log whose numbers disagree")
    def limb_a():
        needs_tree()
        ctrl, guard, config = rig()
        z = locate_focus(ctrl, guard, config)
        x, y = ctrl.core.get_x_position(), ctrl.core.get_y_position()
        params = {
            "protocol": "timelapse",
            "protocol_params": {"n_frames": 1, "interval_s": 0,
                                "exposure_ms": args.exposure_ms},
            "positions": grid(z, args.step_um, x, y),
            "save_dir": str(args.out / "A-live"), "name": "converge",
            "hook_strategy": "autofocus_per_position",
            "hook_params": {"z_range_um": args.z_range_um,
                            "z_step_um": args.z_step_um, "settle_ms": 0},
            "log_path": str(args.out / "A-live" / "hook.json"),
        }
        result = json.loads(tools.execute_tool(
            "run_multiposition_acquisition", dict(params), ctrl, guard,
            acquisition_session_id="gate81a2", tool_call_id="toolu_A"))
        (args.out / "A-result.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8")
        live["params"], live["result"] = params, result
        # The sweep runs before the outcome is known, so restoration (limb C)
        # is evidenced by a refusing run just as well as a converging one.
        # Round 3: 20 hook exposures across two fields, then the refusal.
        try:
            swept = read_log(Path(params["log_path"]))
        except NotExercised:
            swept = []
        if any(row.get("hook_exposures_observed") for row in swept):
            loaded["a_swept"] = True
        if "error" in result:
            # Round 3 (2026-09-10): this machine's DemoCamera has no usable
            # focus response at an arbitrary Z -- the sweep's argmax sat on
            # the boundary at BOTH fields, so the peak is outside any 4 um
            # window here. That is autofocus correctly refusing, not the
            # block failing, and demanding convergence from a camera that
            # cannot provide one would be a criterion no tree could pass.
            # R101 (the emitted hook has never converged on a real curve)
            # therefore needs a rig with a real sample, not this machine.
            if "edge of the searched Z range" in str(result["error"]):
                raise NotExercised(
                    "this camera has no interior focus maximum within the "
                    "sweep: the argmax sat on the boundary. Convergence "
                    "cannot be observed here, and R101 stays open for a real "
                    f"rig. The refusal itself was correct: {result['error'][:150]}")
            raise AssertionError(f"the run failed: {result['error']}")
        records = read_log(Path(params["log_path"]))
        counts = outcomes(records)
        if counts.get("converged", 0) != 2:
            raise AssertionError(
                f"expected 2 converged fields, got {counts} -- {records!r:.400}")
        # Numbers that must agree with each other. 52a's third gate passed
        # every stated limb while carrying a defect whose only tell was a
        # reported position disagreeing with the log's own final value.
        for row in (r for r in records if r.get("autofocus") == "converged"):
            lo, hi = row["sweep_window_um"]
            for field in ("final_commanded_z_um", "final_readback_z_um",
                          "sweep_window_um", "hook_exposures_observed"):
                if field not in row:
                    raise AssertionError(f"a converged record has no {field!r}")
            measured = row["final_readback_z_um"]
            if not (lo - 2.0 <= measured <= hi + 2.0):
                raise AssertionError(
                    f"read-back {measured} is outside its own sweep window "
                    f"{lo}..{hi} by more than the arrival band")
        first = (result.get("results") or [{}])[0]
        path = first.get("dataset_path") or result.get("dataset_path")
        if not path:
            raise NotExercised(
                f"the result names no dataset_path: {sorted(result)}")
        frames = dataset_frames(tools, Path(path))
        observe("A - saved frames on disk vs reported",
                f"dataset holds {frames}; result reports "
                f"frames_acquired={result.get('frames_acquired')}")
        observe("A - hook exposures observed per field",
                str([r.get("hook_exposures_observed") for r in records
                     if r.get("autofocus") == "converged"]))
        return f"2 fields converged; outcomes {counts}"

    @limb("B - an unsafe runtime window stops the run BEFORE the field is exposed",
          "a frame written for the refused field, or the run reporting success")
    def limb_b():
        needs_tree()
        ctrl, guard, config = rig()
        z = loaded["entry_z"]
        x, y = ctrl.core.get_x_position(), ctrl.core.get_y_position()
        stage = config.constraints.stage
        # Narrow the guard's Z bounds for THIS CALL ONLY, through a separate
        # guard object. The machine's own config is never edited: 5b's rule is
        # that a gate must not leave production state pointing into its own
        # evidence folder, and the softer version of that is not to write the
        # operator's safety config at all.
        import copy
        narrowed = copy.deepcopy(config.constraints)
        narrowed.stage.z_min = z - args.z_range_um / 4
        narrowed.stage.z_max = z + args.z_range_um / 4
        tight = SafetyGuard(narrowed)
        save = args.out / "B-refuse"
        params = {
            "protocol": "timelapse",
            "protocol_params": {"n_frames": 1, "interval_s": 0,
                                "exposure_ms": args.exposure_ms},
            "positions": grid(z, args.step_um, x, y),
            "save_dir": str(save), "name": "refuse",
            "hook_strategy": "autofocus_per_position",
            "hook_params": {"z_range_um": args.z_range_um,
                            "z_step_um": args.z_step_um, "settle_ms": 0},
            "log_path": str(save / "hook.json"),
        }
        # Through execute_tool deliberately: D3(b)'s refusal has to survive
        # every `except Exception` between the hook thread and this boundary,
        # which is the half of block 60a's lesson a unit test cannot reach.
        result = json.loads(tools.execute_tool(
            "run_multiposition_acquisition", dict(params), ctrl, tight,
            acquisition_session_id="gate81a2", tool_call_id="toolu_B"))
        (args.out / "B-result.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8")
        if "error" not in result:
            raise AssertionError(
                "the run reported SUCCESS with a sweep window outside the "
                f"guard. That is the 2026-09-09 incident: {result!r:.300}")
        # WHICH error. Round 1 passed this limb on an ImportError, because
        # "an error came back and no frames were written" is satisfied by any
        # failure at all. A limb that passes when its mechanism never ran
        # manufactures evidence, which is worse than one that cannot fail.
        message = str(result["error"])
        which = refusal_kind(message)
        if which is None:
            raise NotExercised(
                "the run failed, but NOT through either autofocus refusal "
                f"this limb exists to observe: {message[:200]}")
        loaded["b_kind"] = which
        # The dose is the criterion, not the message. Score it from the disk.
        written = sorted(p.name for p in save.rglob("*.tif")) if save.exists() else []
        ndtiff = sorted(p.name for p in save.rglob("*NDTiff*")) if save.exists() else []
        if written or ndtiff:
            raise AssertionError(
                f"the refusal fired but frames were written anyway: "
                f"{written[:4]} {ndtiff[:4]}")
        return (f"{which} refusal, before any exposure: "
                f"{str(result['error'])[:120]} (nothing under {save.name})")

    @limb("C - the focus axis is back where it started after the refusal",
          "the axis left parked where the refused sweep put it")
    def limb_c():
        needs_tree()
        ctrl, _guard, config = rig()
        entry = loaded.get("entry_z")
        if entry is None:
            raise NotExercised("limb 0 did not record an entry Z")
        if not loaded.get("a_swept"):
            raise NotExercised(
                "no sweep ran, so the axis was never moved and an unchanged "
                "Z proves nothing. Limb B's plan-time refusal fires BEFORE "
                "any motion, so it cannot evidence restoration; limb A's "
                "sweep is what moves the axis.")
        now = float(ctrl.core.get_position())
        band = max(2.0, 0.1 * args.z_range_um)
        if abs(now - entry) > band:
            raise AssertionError(
                f"the axis is at {now:.3f} um, {abs(now - entry):.3f} um from "
                f"its entry {entry:.3f} um (band {band:.2f})")
        return f"Z {now:.3f} um vs entry {entry:.3f} um, within {band:.2f}"

    @limb("D - the EXPORTED script runs standalone and agrees with the live run",
          "an emitted script that compiles and then dies, or disagrees")
    def limb_d():
        needs_tree()
        ctrl, guard, _config = rig()
        if not live.get("params"):
            raise NotExercised("limb A never ran, so there is nothing to export")
        params = dict(live["params"])
        params["save_dir"] = str(args.out / "D-standalone")
        params["log_path"] = str(args.out / "D-standalone" / "hook.json")
        proc, source = export_and_run(tools, guard, params, args.out, "D-emitted")
        # Equivalence is the criterion, not success. A live run that REFUSED
        # must emit a script that refuses the same way; one that converged
        # must emit one that converges. Requiring exit 0 would have made this
        # limb unreachable on a machine whose camera cannot focus.
        live_failed = "error" in live["result"]
        if live_failed and proc.returncode == 0:
            raise AssertionError(
                "the live run refused and the emitted script did NOT -- the "
                "standalone script is laxer than the tool it reproduces")
        if not live_failed and proc.returncode != 0:
            raise AssertionError(
                f"the live run succeeded and the emitted script exited "
                f"{proc.returncode}. stderr tail: {proc.stderr[-400:]!r}")
        if live_failed:
            live_kind = refusal_kind(str(live["result"]["error"]))
            if live_kind and live_kind != refusal_kind(proc.stderr):
                raise AssertionError(
                    f"live refused with the {live_kind} refusal; the emitted "
                    f"script did not. stderr tail: {proc.stderr[-300:]!r}")
        # The emitted script anchors beside ITSELF -- `_HERE =
        # Path(__file__).resolve().parent`, pinned by
        # test_every_acquisition_emitter_anchors_beside_script -- so it
        # ignores the absolute save_dir in the record and writes next to the
        # script. Round 4 scored this limb NOT EXERCISED for looking at
        # save_dir while the log sat in --out all along.
        beside = sorted(args.out.glob("hook*.json"))
        if not beside:
            raise NotExercised(
                f"the emitted script wrote no hook log beside itself in "
                f"{args.out}; it anchors at _HERE, not at save_dir")
        emitted = json.loads(beside[0].read_text(encoding="utf-8"))
        want = outcomes(read_log(Path(live["params"]["log_path"])))
        got = outcomes(emitted)
        if got != want:
            raise AssertionError(
                f"the standalone run's outcomes {got} differ from live {want}")
        observe("D - emitted script size",
                f"{len(source.splitlines())} lines, sha256 "
                f"{hashlib.sha256(source.encode()).hexdigest()[:16]}...")
        return f"ran standalone, exit 0, outcomes {got} match the live run"

    return finish(args)


if __name__ == "__main__":
    sys.exit(main())
