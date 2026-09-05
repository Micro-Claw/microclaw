"""Block 75b demo-machine gate: the supervised-runtime bound, on real hardware.

Ships as a program, not pasted PowerShell blocks (58a): each limb scores itself
independently so one FAIL cannot hide the rest behind a cascade, the gate owns
its own log, NOT EXERCISED is never a pass, and the exit status is nonzero on
either.

**No rig time is owed by this block and none is asked for.** Block 75a's M2 arm
already measured the healthy one-frame supervised window (n=20, max 446 ms) and
D1's two constants were chosen from it at 11.2x headroom. What is left needs an
injected blocking teardown, which needs no camera trigger, no dose and no booked
session -- design/60 gated the same class of bound on this machine for the same
reason.

What needs real hardware here, and it is narrower than the design list:

* **Every test of this bound drives a fake `Acquisition`.** Limb A is the first
  time a *real* one-frame acquisition on real pycro-manager selects SHORT_FIXED
  and runs under a 5-second slack. If a healthy run on this machine were slower
  than the bound, the constants would be wrong and only a machine can say so.
* **D2's typed result has never been produced by a real hang.** Limbs B and C
  inject one -- with the saved-frame callback delivered and with it suppressed.
  C is the mandatory limb: it is the incident's own shape, zero frames
  accounted, and it is the case the draft design would have failed.
* **The refusal, the dataset and the record** are what the operator actually
  does next (limbs D, F, G).

How the hang is injected. `Acquisition.__exit__` in pycro-manager 1.0.2 is
exactly `self.mark_finished()` then `self.await_completion()` -- read off
`acquisition_superclass.py` with `inspect.getsource`, not off this design, which
is 60b's lesson about writing a fake from the dependency rather than from your
caller. The subclass below reproduces those two statements with a wait between
them, so the acquisition is real, the frame is really written, and the only
thing that changes is that teardown does not return. Suppressing the callback is
done by dropping `image_saved_fn` before construction, which is what a lost
image-saved notification looks like from MicroClaw's side.

It requires **no configuration the product does not require** (60b): no
`--safety-config`, no workspace. Datasets go under the machine's configured
`workspace_dir` when it has one and under `--out` when it does not.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(os.environ.get("MICROCLAW_TREE_UNDER_TEST") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))

RESULTS = []

# The one seam in this gate, and it exists for the gate's own selftest: every
# limb runs in this process, so the selftest substitutes a bridge-shaped fake
# microscope and a fake Acquisition here rather than rewriting the file. Unset
# on the demo machine, where the microscope is the point.
_PRELUDE = os.environ.get("BLOCK75B_PRELUDE")


class NotExercised(Exception):
    """This machine could not exercise the limb; never a pass."""


class Tee:
    def __init__(self, stream, path):
        self.stream = stream
        self.file = path.open("w", encoding="utf-8")

    def write(self, data):
        # The gate owns its log: PowerShell 5.1's Start-Transcript does not
        # capture a native child process's stdout and came back empty twice
        # (58a). Keep the file UTF-8 and the console ASCII-safe.
        self.stream.write(data.encode("ascii", "backslashreplace").decode("ascii"))
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


def read_jsonl(path: Path):
    """Return (records, torn_lines). A torn line is the finding, not a crash."""
    records, torn = [], []
    if not path.exists():
        return records, torn
    with path.open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                torn.append({"line": number, "error": str(exc), "prefix": line[:120]})
    return records, torn


def for_call(records, call_id):
    return [r for r in records if r.get("tool_call_id") == call_id]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--out", type=Path, default=Path("block75b-evidence"))
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--safety-config", default=None,
                        help="Normally omitted. The product finds this machine's own.")
    parser.add_argument("--exposure-ms", type=float, default=50.0,
                        help="The incident's exposure.")
    parser.add_argument("--healthy-runs", type=int, default=5,
                        help="Un-injected one-frame acquisitions for limb A.")
    parser.add_argument("--hold-s", type=float, default=25.0,
                        help="How long an injected teardown withholds "
                             "await_completion. Must exceed the bound by enough "
                             "that the tool returns while teardown is still live.")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, args.out / "gate.txt")
    print(f"Tree under test: {ROOT}")
    if _PRELUDE:
        print(f"PRELUDE {_PRELUDE}", flush=True)

    from microclaw import tools

    # ---- limb E: the control. Scored first and even when the bridge is down,
    # because on a tree without 75b every other limb would fail for a reason
    # that says nothing about this build.
    policy_missing = []
    for name in ("AcquisitionSupervisionPolicy", "SHORT_FIXED", "DEFAULT",
                 "CAMERA_STATE_PROBE_GRACE_S"):
        if not hasattr(tools, name):
            policy_missing.append(name)
    supervisor_params = set(inspect.signature(tools._acquire_with_hooks).parameters)

    @limb("E - the running build carries 75b's bound",
          "a tree whose supervisor still takes the 900 s floor for every caller")
    def limb_e():
        missing = list(policy_missing)
        if "policy" not in supervisor_params:
            missing.append("_acquire_with_hooks(..., policy=)")
        if missing:
            raise AssertionError("this build is missing: " + "; ".join(missing))
        short, default = tools.SHORT_FIXED, tools.DEFAULT
        # Read the values off the product; a gate that hard-coded 5.0 would
        # pass a build that had quietly moved it.
        assert short.terminal_frames == 1, short
        assert default.terminal_frames is None, default
        assert short.quiet_floor_s < default.quiet_floor_s, (short, default)
        assert short.runtime_slack_s < default.runtime_slack_s, (short, default)
        return (f"SHORT_FIXED quiet {short.quiet_floor_s:g}s slack "
                f"{short.runtime_slack_s:g}s bound_name {short.bound_name!r}; "
                f"DEFAULT quiet {default.quiet_floor_s:g}s slack "
                f"{default.runtime_slack_s:g}s; camera probe grace "
                f"{tools.CAMERA_STATE_PROBE_GRACE_S:g}s")

    if policy_missing or "policy" not in supervisor_params:
        for name in ("0 - bridge, camera and save root",
                     "A - a real healthy one-frame run selects the short policy",
                     "B - a blocked teardown with the frame accounted",
                     "C - a blocked teardown with no frame accounted",
                     "D - the session refuses a second acquisition, then lifts",
                     "F - the timed-out dataset opens once teardown finishes",
                     "G - the timeout is in D4's file"):
            RESULTS.append({"name": name, "status": "NOT EXERCISED",
                            "detail": "stood down: limb E found no 75b bound in this build",
                            "fails_if": "n/a"})
            print(f"NOT EXERCISED: {name} - stood down by limb E")
        return finish(args)

    from microclaw.conversation import AcquisitionDiagnosticWriter, AuditLog, DIAGNOSTIC_FLUSH_GRACE_S
    from microclaw.config import load_safety_config_or_exit
    from microclaw.safety import SafetyGuard
    import microclaw.controller as controller_module

    if _PRELUDE:
        exec(compile(Path(_PRELUDE).read_text(encoding="utf-8"), _PRELUDE, "exec"))
    # Resolved through the module, never bound to a local: the selftest's
    # prelude installs its fake microscope here, and a name captured by
    # `from ... import` before the prelude ran would silently stay the real one.
    # The selftest caught exactly that, which is the argument for running it.

    # The delivery ceiling is composed from the product's own constants, never
    # from the 10.1 s the design happens to quote: a later measurement must be
    # able to move a constant without making this gate wrong.
    def delivery_ceiling_s(policy, estimated_duration_s):
        expiry = max(estimated_duration_s * 1.5,
                     estimated_duration_s + policy.runtime_slack_s,
                     policy.quiet_floor_s)
        return (expiry + tools._ACQUISITION_POLL_S
                + tools.CAMERA_STATE_PROBE_GRACE_S + DIAGNOSTIC_FLUSH_GRACE_S)

    REAL_ACQUISITION = tools.Acquisition
    hang = {"release": threading.Event(), "suppress": False,
            "after_frames": False, "active": False, "entered": None}
    patched_exits: dict = {}

    # **The hang is installed by wrapping, never by subclassing.** Block 75b's
    # first demo run lost both blocked limbs to a subclass that was never used:
    # `pycromanager.Acquisition` is a *dispatching constructor* whose `__new__`
    # ignores `cls` and returns a `JavaBackendAcquisition` (or, under pymmcore,
    # a `PythonBackendAcquisition`). `class X(Acquisition)` compiles, answers
    # every reasonable question about itself -- `inspect.getsource(X.__exit__)`
    # included -- and can never be instantiated as itself. So the gate builds
    # the real object and patches the *returned type's* `__exit__`, which is
    # where the lookup actually lands.
    #
    # `Acquisition.__exit__` in pycro-manager 1.0.2 is exactly `mark_finished()`
    # then `await_completion()`, read off `acquisition_superclass.py`. The
    # replacement reproduces those two statements with a hold between them, so
    # the acquisition is real and the frame is really written; only teardown
    # stops returning.
    #
    # The two arms hold in different places on purpose, and the supervisor
    # cannot tell them apart any other way:
    #   after_frames=False -- stuck inside the call design/60 measured at 95
    #                         minutes, nothing accounted (the incident's shape);
    #   after_frames=True  -- the frames land, the supervisor reports
    #                         `finalizing`, and teardown still never returns.
    def _install_hang(acq) -> None:
        cls = type(acq)
        if cls in patched_exits:
            return
        original = cls.__exit__

        def blocking_exit(self, exc_type, exc_val, exc_tb):
            if not hang["active"]:
                return original(self, exc_type, exc_val, exc_tb)
            hang["entered"] = time.monotonic()
            self.mark_finished()
            if hang["after_frames"]:
                self.await_completion()
                hang["release"].wait(args.hold_s)
            else:
                hang["release"].wait(args.hold_s)
                self.await_completion()
            return None

        cls.__exit__ = blocking_exit
        patched_exits[cls] = original

    class InjectionUnavailable(Exception):
        """The hang could not be installed on this machine's Acquisition."""

    def blocking_acquisition(**kwargs):
        if hang["suppress"]:
            # What a lost image-saved notification looks like from MicroClaw's
            # side: the frame is written by Java, and nothing ever tells the
            # supervisor.
            kwargs.pop("image_saved_fn", None)
        acq = REAL_ACQUISITION(**kwargs)
        try:
            _install_hang(acq)
        except Exception as exc:                # noqa: BLE001 - reported below
            raise InjectionUnavailable(
                f"could not replace __exit__ on {type(acq).__name__}: "
                f"{type(exc).__name__}: {exc}") from exc
        return acq

    def _restore_acquisition():
        tools.Acquisition = REAL_ACQUISITION
        hang["active"] = False
        for cls, original in list(patched_exits.items()):
            try:
                cls.__exit__ = original
            except Exception:                   # noqa: BLE001 - best effort
                pass
            patched_exits.pop(cls, None)

    state = {}

    @limb("0 - bridge, camera and save root",
          "no ZMQ bridge, no camera, an unwritable save root, or a pycro-manager "
          "whose __exit__ is no longer mark_finished + await_completion")
    def limb_0():
        source = inspect.getsource(REAL_ACQUISITION.__exit__)
        for statement in ("mark_finished", "await_completion"):
            if statement not in source:
                raise AssertionError(
                    f"pycro-manager's Acquisition.__exit__ no longer calls "
                    f"{statement}; the injection below would not reproduce a "
                    f"teardown hang. Source:\n{source}")
        ctrl = controller_module.MicroscopeController(port=args.port)
        parsed = load_safety_config_or_exit(args.safety_config)
        guard = SafetyGuard(parsed.constraints)
        camera = str(ctrl.core.get_camera_device() or "")
        if not camera:
            raise NotExercised("Micro-Manager has no camera configured")
        workspace = getattr(parsed.constraints, "workspace_dir", None)
        root = Path(workspace) / "block75b-gate" if workspace else args.out / "data"
        root.mkdir(parents=True, exist_ok=True)
        resolved = guard.resolve_in_workspace(str(root))
        confirmations = []

        def gate_confirm(summary, kind="action", subject=None, **extra):
            # A program-shaped gate has no console to answer on. **extra is
            # load-bearing: the acquisition threshold path passes
            # grant_metadata=, and a narrower stub turns any confirmed call
            # into a TypeError (75a's selftest found exactly that).
            confirmations.append({"kind": kind, "subject": subject,
                                  "summary": summary, "extra": sorted(extra)})
            print(f"[gate] auto-approved {kind}/{subject}: {summary}")
            return True

        tools.CONFIRM_FN = gate_confirm
        state.update(ctrl=ctrl, guard=guard, camera=camera, save_root=resolved,
                     confirmations=confirmations,
                     workspace=str(workspace) if workspace else None)
        # Report how this pycro-manager builds an Acquisition. The gate's first
        # demo run was lost to a dispatching __new__ that no limb mentioned, so
        # the fact now reaches the artifact whether or not anything fails.
        dispatches = isinstance(REAL_ACQUISITION, type) and "__new__" in vars(REAL_ACQUISITION)
        state["acquisition_construction"] = {
            "callable": f"{getattr(REAL_ACQUISITION, '__module__', '?')}."
                        f"{getattr(REAL_ACQUISITION, '__name__', REAL_ACQUISITION)}",
            "is_class": isinstance(REAL_ACQUISITION, type),
            "defines_own_new": bool(dispatches),
        }
        return (f"camera={camera} save_root={resolved} "
                f"workspace={'configured' if workspace else 'none'}; "
                f"__exit__ is mark_finished + await_completion; "
                f"Acquisition={state['acquisition_construction']['callable']} "
                f"(dispatching __new__: {bool(dispatches)}) - the gate wraps the "
                f"constructed object rather than subclassing it")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    history = args.out / f"{stamp}_block75b_microclaw_history.jsonl"
    acq_path = Path(str(history).replace("_history.jsonl", "_acquisitions.jsonl"))
    writer = AcquisitionDiagnosticWriter(AuditLog(acq_path))
    session_id = history.name.replace(".jsonl", "")

    def drive(call_id, name, n_frames=1):
        started = time.monotonic()
        raw = tools.execute_tool(
            "run_timelapse",
            {"n_frames": n_frames, "interval_s": 0,
             "exposure_ms": args.exposure_ms,
             "save_dir": state["save_root"], "name": name},
            state["ctrl"], state["guard"], tools.TOOL_REGISTRY,
            acquisition_diagnostic_writer=writer,
            acquisition_session_id=session_id,
            tool_call_id=call_id,
        )
        wall_s = time.monotonic() - started
        return (json.loads(raw) if isinstance(raw, str) else raw), wall_s

    @limb("A - a real healthy one-frame run selects the short policy",
          "a healthy acquisition on this machine that is slower than the bound "
          "chosen for it, or a run that never reports a phase")
    def limb_a():
        if "ctrl" not in state:
            raise NotExercised("limb 0 did not establish a bridge")
        walls, phases = [], []
        events = []
        with tools._acquisition_diagnostic_context({"sink": events.append}):
            for index in range(args.healthy_runs):
                result, wall_s = drive(f"healthy-{index}", f"block75b_healthy_{index}")
                assert result.get("acquisition") != "unterminated", result
                assert "error" not in result, result
                walls.append(wall_s)
                phases.append([e.get("phase") for e in events
                               if e.get("type") == "acquisition_progress"])
                events.clear()
        records, torn = read_jsonl(acq_path)
        assert not torn, torn
        constructions = [r for r in records
                         if r.get("type") == "acquisition_construction"]
        bounds = {r.get("active_bound_term") for r in constructions}
        bound_s = {round(float(r["active_bound_s"]), 3) for r in constructions}
        short = tools.SHORT_FIXED
        expected_term = f"plan_plus_{short.runtime_slack_s:g}_s"
        assert bounds == {expected_term}, (bounds, expected_term)
        # The whole point of the block: a one-frame run is bounded in seconds,
        # not in the fifteen minutes that produced no record on 2026-09-04.
        assert max(bound_s) < tools.DEFAULT.quiet_floor_s, (bound_s,)
        state["limb_a"] = {
            "runs": len(walls), "wall_s_max": round(max(walls), 3),
            "wall_s_p50": round(sorted(walls)[len(walls) // 2], 3),
            "active_bound_s": sorted(bound_s), "active_bound_term": sorted(bounds),
            "progress_phases": phases,
        }
        # A healthy run must finish far inside its own bound. This is the limb
        # that can say the measured constants are wrong for this machine.
        assert max(walls) < min(bound_s), (
            f"a healthy one-frame call took {max(walls):.3f}s against a "
            f"{min(bound_s):g}s bound; the constants do not fit this machine")
        return (f"{len(walls)} healthy run(s), tool call p50 "
                f"{sorted(walls)[len(walls) // 2]:.3f}s max {max(walls):.3f}s, "
                f"bound {sorted(bound_s)} s ({sorted(bounds)}); phases seen "
                f"{phases}")

    def blocked_run(call_id, name, suppress, after_frames):
        hang["release"].clear()
        hang["suppress"] = suppress
        hang["after_frames"] = after_frames
        hang["entered"] = None
        hang["active"] = True
        tools.Acquisition = blocking_acquisition
        events = []
        try:
            with tools._acquisition_diagnostic_context({"sink": events.append}):
                result, wall_s = drive(call_id, name)
        finally:
            tools.Acquisition = REAL_ACQUISITION
            hang["active"] = False
        if hang["entered"] is None:
            # The mechanism never ran, so nothing here is evidence about the
            # product. NOT EXERCISED, never FAIL (58a) -- and never a pass.
            raise NotExercised(
                "the injected teardown hang never executed, so this limb "
                "measured nothing about the bound. Its own __exit__ was not "
                "the one that ran; check limb 0's report of this "
                "pycro-manager's Acquisition construction.")
        return result, wall_s, events

    def score_blocked(result, wall_s, suppress, call_id):
        short = tools.SHORT_FIXED
        # Take the bound the product actually recorded rather than recomputing
        # it here, and take it from *this* call: limb B leaves a timeout record
        # behind, so an unscoped read would let limb C score limb B's evidence.
        records, _ = read_jsonl(acq_path)
        timeouts = [r for r in for_call(records, call_id)
                    if r.get("type") == "acquisition_timeout"]
        assert timeouts, f"no acquisition_timeout record for {call_id} in D4's file"
        bound_s = float(timeouts[-1]["active_bound_s"])
        ceiling = (bound_s + tools._ACQUISITION_POLL_S
                   + tools.CAMERA_STATE_PROBE_GRACE_S + DIAGNOSTIC_FLUSH_GRACE_S)
        assert result.get("acquisition") == "unterminated", result
        assert result.get("expired_bound") == short.bound_name, result
        assert result.get("frames_accounted") == (0 if suppress else 1), result
        assert result.get("phase") == (
            "acquiring_or_notifying" if suppress else "finalizing"), result
        assert result.get("bound_s") is not None, (
            "the result named no bound_s, so it says a bound expired without "
            f"saying what it was: {result}")
        assert result.get("teardown_running") is True, result
        assert result.get("dataset_path"), result
        # A zero-frame timeout must never describe the data as saved (D2).
        assert "complete" not in str(result.get("error", "")).lower(), result
        if suppress:
            assert "saved" not in str(result.get("data", "")).lower(), result
        return bound_s, ceiling

    @limb("B - a blocked teardown with the frame accounted",
          "a hang whose typed result never arrives, or arrives calling the "
          "acquisition complete")
    def limb_b():
        if "ctrl" not in state:
            raise NotExercised("limb 0 did not establish a bridge")
        result, wall_s, events = blocked_run("blocked-saved", "block75b_blocked_saved",
                                             suppress=False, after_frames=True)
        bound_s, ceiling = score_blocked(result, wall_s, suppress=False,
                                         call_id="blocked-saved")
        # 75a measured the tool call at 2.8x the supervised window on M2, so
        # the operator's wait is wider than the bound by the work outside it.
        # Report that overhead rather than asserting a number for it.
        #
        # **Named for what it is.** Round 2 reported this as
        # `outside_supervised_window_s`, which it is not: it also contains the
        # poll granularity, the camera probe and the diagnostic flush, all of
        # which are deliberately inside the delivery ceiling. A reader comparing
        # it to 75a's M2 figure would have compared two different quantities.
        overhead = wall_s - bound_s
        state["limb_b"] = {"wall_s": round(wall_s, 3), "bound_s": bound_s,
                           "ceiling_s": round(ceiling, 3),
                           "after_bound_expired_s": round(overhead, 3),
                           "phases": [e.get("phase") for e in events
                                      if e.get("type") == "acquisition_progress"],
                           "result": result}
        assert wall_s <= ceiling + 2.0, (
            f"the tool call took {wall_s:.2f}s against a {ceiling:.2f}s delivery "
            f"ceiling (+2 s allowance for work outside the supervised window)")
        assert "finalizing" in state["limb_b"]["phases"], (
            "the progress phase never reached finalizing after the frame was "
            "accounted, so the browser could not say so: "
            f"{state['limb_b']['phases']}")
        return (f"unterminated in {wall_s:.2f}s against a {ceiling:.2f}s ceiling; "
                f"1 frame accounted, phase finalizing, {overhead:.2f}s between "
                f"the bound expiring and the tool returning (poll + camera probe "
                f"+ diagnostic flush + the work outside the supervised window)")

    @limb("C - a blocked teardown with no frame accounted "
          "(MANDATORY - the incident's own shape)",
          "the bound failing to fire when the callback never arrives, which is "
          "the case design/75's first draft would have hung on")
    def limb_c():
        if "ctrl" not in state:
            raise NotExercised("limb 0 did not establish a bridge")
        # The waiter from limb B must be gone, or execute_tool refuses this one
        # for the previous acquisition rather than running it.
        hang["release"].set()
        _wait_for_clear(tools, state["ctrl"], args.hold_s + 30)
        result, wall_s, events = blocked_run("blocked-lost", "block75b_blocked_lost",
                                             suppress=True, after_frames=False)
        bound_s, ceiling = score_blocked(result, wall_s, suppress=True,
                                         call_id="blocked-lost")
        state["limb_c"] = {"wall_s": round(wall_s, 3), "bound_s": bound_s,
                           "ceiling_s": round(ceiling, 3),
                           "phases": [e.get("phase") for e in events
                                      if e.get("type") == "acquisition_progress"],
                           "result": result}
        assert wall_s <= ceiling + 2.0, (
            f"the tool call took {wall_s:.2f}s against a {ceiling:.2f}s delivery "
            f"ceiling (+2 s allowance for work outside the supervised window)")
        return (f"unterminated in {wall_s:.2f}s against a {ceiling:.2f}s ceiling; "
                f"0 frames accounted, phase acquiring_or_notifying - the bound "
                f"fired with no callback at all")

    @limb("D - the session refuses a second acquisition, then lifts",
          "a refusal that never fires, or one that never clears without a restart")
    def limb_d():
        if "limb_c" not in state:
            raise NotExercised("limb C produced no unterminated acquisition")
        refused = json.loads(tools.execute_tool(
            "run_timelapse",
            {"n_frames": 1, "interval_s": 0, "exposure_ms": args.exposure_ms,
             "save_dir": state["save_root"], "name": "block75b_refused"},
            state["ctrl"], state["guard"], tools.TOOL_REGISTRY,
            acquisition_diagnostic_writer=writer, acquisition_session_id=session_id,
            tool_call_id="refused",
        ))
        assert refused.get("acquisition") == "refused", refused
        hang["release"].set()
        waited = _wait_for_clear(tools, state["ctrl"], args.hold_s + 30)
        # No restart: the same process, the same controller, the next call runs.
        after, _ = drive("after-refusal", "block75b_after_refusal")
        assert after.get("acquisition") != "unterminated", after
        assert "error" not in after, after
        state["limb_d"] = {"refused": refused, "cleared_after_s": round(waited, 2),
                           "after": after.get("status")}
        return (f"refused while teardown lived, cleared {waited:.1f}s later "
                f"without restarting MicroClaw, and the next acquisition ran")

    @limb("F - the timed-out dataset opens once teardown finishes",
          "a dataset the operator cannot read after the bound fired")
    def limb_f():
        if "limb_c" not in state:
            raise NotExercised("limb C produced no timed-out dataset")
        path = Path(state["limb_c"]["result"]["dataset_path"])
        if not path.exists():
            raise AssertionError(f"the reported dataset path does not exist: {path}")
        entries = sorted(p.name for p in path.iterdir())
        index = path / "NDTiff.index"
        # design/60's precedent: the index is what says what Java wrote, and it
        # is readable when a multi-GB .tif is not.
        assert entries, f"{path} is empty"
        state["limb_f"] = {"dataset_path": str(path), "entries": entries,
                           "index_bytes": index.stat().st_size if index.exists() else None}
        # The result must not have promised clean finalization before this.
        assert "complete" not in str(state["limb_c"]["result"].get("status", "")).lower()
        return (f"{path.name} holds {entries}; index "
                f"{state['limb_f']['index_bytes']} bytes. The result had not "
                f"claimed finalization.")

    @limb("G - the timeout is in D4's file",
          "a bound that fires and leaves no record, which is the 2026-09-04 "
          "failure repeated one layer down")
    def limb_g():
        if "limb_c" not in state:
            raise NotExercised("limb C produced no timeout")
        writer.close()
        records, torn = read_jsonl(acq_path)
        assert not torn, torn
        lost = for_call(records, "blocked-lost")
        timeouts = [r for r in lost if r.get("type") == "acquisition_timeout"]
        assert timeouts, f"no acquisition_timeout for blocked-lost in {acq_path.name}"
        record = timeouts[-1]
        for field in ("phase", "active_bound_s", "active_bound_term",
                      "quiet_bound_s", "camera_sequence_running", "frames_accounted"):
            assert field in record, (
                f"the timeout record is missing {field!r}, so D4's file cannot "
                f"say what expired: {sorted(record)}")
        assert record["phase"] == "acquiring_or_notifying", record
        assert record["frames_accounted"] == 0, record
        persisted = state["limb_c"]["result"].get("diagnostic_persisted")
        state["limb_g"] = {"record": record, "diagnostic_persisted": persisted,
                           "file": acq_path.name, "records": len(records)}
        assert persisted is True, (
            f"the result reported diagnostic_persisted={persisted!r} while the "
            f"record is on disk; the acknowledgement did not agree with the file")
        return (f"{acq_path.name} carries the timeout for blocked-lost with "
                f"phase={record['phase']} bound={record['active_bound_s']}s "
                f"({record['active_bound_term']}); result reported "
                f"diagnostic_persisted={persisted}")

    hang["release"].set()
    _restore_acquisition()
    try:
        writer.close()
    except Exception:
        pass
    state["history_stub"] = str(history)
    return finish(args, state)


def _wait_for_clear(tools, ctrl, timeout_s):
    """Wait for the session's unterminated flag to clear. Returns seconds waited."""
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        pending = getattr(ctrl, "_microclaw_unterminated_acquisition", None)
        if not isinstance(pending, dict):
            return time.monotonic() - started
        waiter = pending.get("waiter")
        if waiter is not None and not waiter.is_alive():
            # execute_tool clears the flag on the next entry-point call once the
            # camera is idle and the waiter is dead; do not clear it here, or the
            # gate would be testing itself instead of the product.
            return time.monotonic() - started
        time.sleep(0.5)
    return time.monotonic() - started


def finish(args, state=None):
    payload = {"results": RESULTS, "state": _jsonable(state or {})}
    target = args.log or (args.out / "score.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str),
                      encoding="utf-8")
    print("\n--- summary ---")
    for row in RESULTS:
        print(f"{row['status']:>14}: {row['name']}")
    bad = [r for r in RESULTS if r["status"] != "PASS"]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} PASS. Evidence: {target}")
    if bad:
        # NOT EXERCISED is never a pass (58a).
        print("NOT ALL LIMBS PASSED - report exactly what is printed above.")
    return 1 if bad else 0


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()
                if k not in ("ctrl", "guard")}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


if __name__ == "__main__":
    sys.exit(main())
