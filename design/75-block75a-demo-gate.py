"""Block 75a demo-machine gate: D4's record, written where a restart cannot lose it.

Every limb here only computes or drives an acquisition this program owns, so it
ships as a program rather than pasted PowerShell blocks (58a): each limb scores
itself independently, one FAIL cannot hide the rest behind a cascade, the gate
owns its own log, and the exit status is nonzero on any FAIL *or* any
NOT EXERCISED.

What this gate can and cannot establish.

D4 is a diagnostic record, and almost all of it is settled off-rig -- the
bounded queue, the coalescing, the ack grace, the correlation-id plumbing and
both entry points' sibling filenames all have tests, and the round-2 review
verified each of them by mutation. Do not add a limb that repeats one.

What needs real hardware is narrower and is all here:

* **Every unit test drives a fake `Acquisition`.** Nothing has ever observed the
  order in which *real* pycro-manager fires `image_saved_fn` relative to
  `__exit__` returning, and the whole incident turns on that thread. Limb A is
  the first observation of it.
* **How long a lifecycle record takes to reach the disk**, which is what decides
  whether an abruptly closed MicroClaw leaves a record behind. Limb D measures
  it and then ends the process to prove it.
* **What a healthy one-frame acquisition costs end to end** on real hardware,
  from D4's own timestamps -- the reference arm for the number block 75b's two
  constants come from. The demo camera cannot set those constants; M2 does, in
  `design/75-block75a-m2-latency.py`. This arm exists so the M2 numbers have
  something to be compared against.

What it deliberately does **not** do: drive a browser session. 75b's gate needs
a browser for D2's structured result and can carry the `agent.py` handoff then;
spending the operator's session time twice for a one-line pass-through that
already has a test is what `CLAUDE.md` warns about. Limb F picks the join up for
free if this machine happens to have a real session's files lying around.

It requires **no configuration the product does not require** (60b): no
`--safety-config`, and no workspace. Datasets go under the machine's configured
`workspace_dir` when it has one, and under `--out` when it does not.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(os.environ.get("MICROCLAW_TREE_UNDER_TEST") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))

RESULTS = []

# The record kinds one complete acquisition writes. Read off the product, not
# off the design: a gate that hard-coded the design's names would pass a build
# that renamed one.
#
# **This is a set, not a sequence, and the demo machine is why.** The first run
# of this gate asserted the order our fakes produce -- accounting before
# mark_finished -- and real pycro-manager does the opposite in 23 of 23
# acquisitions:
#
#   construction -> event_submission -> mark_finished
#     -> first_frame_accounted -> planned_final_frame_accounted
#     -> teardown_completion
#
# The frame is accounted *inside* `acq.__exit__`, which is the observable
# consequence of design/75's "the callback shares the failed path". Limb A owns
# the ordering and asserts only the invariants that must hold on any rig; every
# other limb compares sets, so one rig's ordering cannot fail a limb about
# survival.
COMPLETE_LIFECYCLE = (
    "acquisition_construction",
    "acquisition_event_submission",
    "acquisition_first_frame_accounted",
    "acquisition_planned_final_frame_accounted",
    "acquisition_mark_finished",
    "acquisition_teardown_completion",
)


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
    """Return (records, torn_lines). A torn line is the finding, not a crash.

    Deliberately per-line: block 52b's export died because one embedded newline
    made `ast.parse` reject a whole session's file, and an append-only
    diagnostic has exactly that exposure. Report which line failed rather than
    losing the file.
    """
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
                torn.append({"line": number, "error": str(exc),
                             "prefix": line[:120]})
    return records, torn


def by_call(records):
    calls = {}
    for record in records:
        key = record.get("tool_call_id")
        if key is None:
            continue
        calls.setdefault(key, []).append(record)
    return calls


def lifecycle_kinds(records):
    return [r["type"] for r in records if r.get("type") != "acquisition_progress"]


def stamps(records, kind):
    return [r["timestamp"] for r in records if r.get("type") == kind]


def iso_to_s(text):
    from datetime import datetime
    return datetime.fromisoformat(text).timestamp()


CHILD_DRIVER = '''
"""Child driver for limbs C and D. Runs acquisitions through the product's own
wiring, in a process the parent can end.

A file the gate spawns rather than a thread, because the property under test is
what survives a process ending -- which a thread cannot show.
"""
import json, os, sys, time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
mode, acq_path, save_root, port, safety, frames, exposure = sys.argv[2:9]

from microclaw import tools
from microclaw.conversation import AcquisitionDiagnosticWriter, AuditLog
from microclaw.config import load_safety_config_or_exit
from microclaw.controller import MicroscopeController
from microclaw.safety import SafetyGuard

# The one seam in this gate, and it exists for the gate's own selftest.
# Limbs C and D are about what survives a process ending, which no in-process
# fake can show -- so the selftest has to run this driver for real, with a fake
# microscope underneath it. Injecting the fakes by rewriting this string would
# break the moment an import moved; a named file it execs will not. Unset on
# the demo machine, where the microscope is the point.
_prelude = os.environ.get("BLOCK75A_CHILD_PRELUDE")
if _prelude:
    print(f"CHILD_PRELUDE {_prelude}", flush=True)
    exec(compile(Path(_prelude).read_text(encoding="utf-8"), _prelude, "exec"))

# **extra, because the acquisition threshold path passes grant_metadata= and a
# narrower stub makes every confirmed call a TypeError.
tools.CONFIRM_FN = (
    lambda summary, kind="action", subject=None, **extra: True)
ctrl = MicroscopeController(port=int(port))
guard = SafetyGuard(load_safety_config_or_exit(safety or None).constraints)
writer = AcquisitionDiagnosticWriter(AuditLog(Path(acq_path)))
session_id = Path(acq_path).name.replace("_acquisitions.jsonl", "_history")


def run(call_id, n_frames):
    return tools.execute_tool(
        "run_timelapse",
        {"n_frames": n_frames, "interval_s": 0, "exposure_ms": float(exposure),
         "save_dir": save_root, "name": f"block75a_{mode}_{call_id}"},
        ctrl, guard, tools.TOOL_REGISTRY,
        acquisition_diagnostic_writer=writer,
        acquisition_session_id=session_id,
        tool_call_id=call_id,
    )


if mode == "shutdown":
    # The normal path: one acquisition, then the same close() every entry
    # point calls from its finally.
    print(run("child-complete", 1))
    writer.close()
    print("CHILD_CLEAN_EXIT")
    sys.exit(0)

# mode == "kill": one acquisition that completes, then a long one the parent
# ends mid-flight. The parent waits for the long call's submission record to
# reach the disk before ending it, so what it scores is a call with a beginning
# and no end -- the incident's own evidence shape.
print(run("child-before-kill", 1))
print("CHILD_FIRST_DONE", flush=True)
print("CHILD_LONG_RESULT " + str(run("child-killed", int(frames))), flush=True)
print("CHILD_SURVIVED_KILL")
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--out", type=Path, default=Path("block75a-evidence"))
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--safety-config", default=None,
                        help="Normally omitted. The product finds this machine's own.")
    parser.add_argument("--exposure-ms", type=float, default=50.0,
                        help="The incident's exposure.")
    parser.add_argument("--latency-runs", type=int, default=20,
                        help="One-frame acquisitions for the reference arm.")
    parser.add_argument("--kill-frames", type=int, default=400,
                        help="Frames for the call limb D interrupts. Keep it "
                             "below this rig's confirm_above_frames, or the "
                             "call becomes a gated hardware-sequenced burst.")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, args.out / "gate.txt")
    print(f"Tree under test: {ROOT}")

    from microclaw import tools
    from microclaw.config import load_safety_config_or_exit
    from microclaw.controller import MicroscopeController
    from microclaw.safety import SafetyGuard

    # Limb E first, because it is the control and it must be scored even when
    # the bridge is down: on a tree without D4 every other limb would fail for
    # a reason that says nothing about the build.
    import inspect
    execute_params = set(inspect.signature(tools.execute_tool).parameters)
    try:
        from microclaw.conversation import AcquisitionDiagnosticWriter, AuditLog
        writer_import_error = None
    except ImportError as exc:
        AcquisitionDiagnosticWriter = AuditLog = None
        writer_import_error = f"{type(exc).__name__}: {exc}"

    @limb("E - the running build carries D4",
          "a tree without the writer, or without the correlation plumbing")
    def limb_e():
        missing = []
        if writer_import_error is not None:
            missing.append(f"AcquisitionDiagnosticWriter ({writer_import_error})")
        for name in ("acquisition_diagnostic_writer", "acquisition_session_id",
                     "tool_call_id"):
            if name not in execute_params:
                missing.append(f"execute_tool(..., {name}=)")
        if missing:
            raise AssertionError("this build is missing: " + "; ".join(missing))
        return "writer importable; execute_tool accepts all three arguments"

    if writer_import_error is not None or "tool_call_id" not in execute_params:
        # Every remaining limb needs the mechanism limb E just found absent.
        # Reporting each one FAIL would be six restatements of one fact.
        for name in ("0 - bridge, camera and save root", "A - real lifecycle order",
                     "B - one-frame reference latency", "C - normal shutdown flushes",
                     "D - an abrupt end still leaves a record",
                     "F - a real session's history join"):
            RESULTS.append({"name": name, "status": "NOT EXERCISED",
                            "detail": "stood down: limb E found no D4 in this build",
                            "fails_if": "n/a"})
            print(f"NOT EXERCISED: {name} - stood down by limb E")
        return finish(args)

    state = {}

    @limb("0 - bridge, camera and save root",
          "no ZMQ bridge, no camera, or an unwritable save root")
    def limb_0():
        ctrl = MicroscopeController(port=args.port)
        parsed = load_safety_config_or_exit(args.safety_config)
        guard = SafetyGuard(parsed.constraints)
        camera = str(ctrl.core.get_camera_device() or "")
        if not camera:
            raise NotExercised("Micro-Manager has no camera configured")
        # No configuration the product does not require: use the machine's
        # workspace when it has one, and the evidence directory when it does
        # not. Never ask the operator to edit a production safety config (60b).
        workspace = getattr(parsed.constraints, "workspace_dir", None)
        root = Path(workspace) / "block75a-gate" if workspace else args.out / "data"
        root.mkdir(parents=True, exist_ok=True)
        resolved = guard.resolve_in_workspace(str(root))
        confirmations = []

        def gate_confirm(summary, kind="action", subject=None, **extra):
            # A program-shaped gate has no console to answer a confirmation on
            # (_require_confirmation calls input()), so auto-approve -- but
            # record every question, because an auto-approval nobody can read
            # afterwards is design/60 F5's session grant again.
            #
            # **extra is load-bearing, not defensive. The acquisition threshold
            # path passes `grant_metadata=` (tools.py:2707), and a stub without
            # it turns any confirmed call into
            # "TypeError: got an unexpected keyword argument 'grant_metadata'"
            # -- which this gate's own selftest produced the moment a call
            # crossed the fixture's confirm_above_frames. The keywords in the
            # product today are kind, subject and grant_metadata; **extra means
            # a fourth cannot break the gate.
            confirmations.append({"kind": kind, "subject": subject,
                                  "summary": summary, "extra": sorted(extra)})
            print(f"[gate] auto-approved {kind}/{subject}: {summary}")
            return True

        tools.CONFIRM_FN = gate_confirm
        state.update(ctrl=ctrl, guard=guard, camera=camera, save_root=resolved,
                     confirmations=confirmations,
                     workspace=str(workspace) if workspace else None)
        return (f"camera={camera} save_root={resolved} "
                f"workspace={'configured' if workspace else 'none'}")

    def acquisitions_path(tag):
        # The product derives this name by replacing _history.jsonl; the gate
        # does the same rather than inventing a name of its own, so a rename in
        # the product breaks this gate instead of hiding from it.
        stamp = time.strftime("%Y%m%d_%H%M%S")
        history = args.out / f"{stamp}_{tag}_microclaw_history.jsonl"
        return history, Path(str(history).replace("_history.jsonl",
                                                  "_acquisitions.jsonl"))

    def drive(writer, session_id, call_id, n_frames, name):
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
        result = json.loads(raw) if isinstance(raw, str) else raw
        return result, wall_s

    @limb("A - real lifecycle order",
          "real pycro-manager firing image_saved_fn in an order our fakes do not")
    def limb_a():
        if "ctrl" not in state:
            raise NotExercised("limb 0 did not establish a bridge")
        history, acq_path = acquisitions_path("limbA")
        session_id = history.name.replace("_history.jsonl", "_history")
        writer = AcquisitionDiagnosticWriter(AuditLog(acq_path))
        result, wall_s = drive(writer, session_id, "gate-limb-a", 1,
                               "block75a_limbA")
        writer.close()
        state["limb_a"] = {"result": result, "wall_s": wall_s,
                           "acquisitions_file": str(acq_path)}
        if "error" in result:
            raise AssertionError(f"the acquisition itself failed: {result['error']}")
        records, torn = read_jsonl(acq_path)
        if torn:
            raise AssertionError(f"unparseable line(s): {torn}")
        calls = by_call(records)
        if list(calls) != ["gate-limb-a"]:
            raise AssertionError(
                f"expected one correlated call, got {sorted(calls)}")
        kinds = lifecycle_kinds(calls["gate-limb-a"])
        # Assert what must be true on any rig; *report* what the engine chose.
        # The first version of this limb asserted the order our fakes produce
        # and the demo machine refuted it, which is the finding -- but a fixed
        # sequence would now simply encode one rig's answer in place of another
        # rig's. These four invariants are what a reader of the record needs,
        # and each of them can actually fail.
        missing = set(COMPLETE_LIFECYCLE) - set(kinds)
        if missing:
            raise AssertionError(f"records missing: {sorted(missing)}; got {kinds}")
        if kinds[0] != "acquisition_construction":
            raise AssertionError(f"the record does not open with construction: {kinds}")
        if kinds[1] != "acquisition_event_submission":
            raise AssertionError(f"submission does not follow construction: {kinds}")
        if kinds[-1] != "acquisition_teardown_completion":
            raise AssertionError(f"the record does not end with teardown: {kinds}")
        accounting = [i for i, k in enumerate(kinds) if "accounted" in k]
        if max(accounting) > kinds.index("acquisition_teardown_completion"):
            raise AssertionError(
                f"a frame was accounted after teardown completed: {kinds}")
        times = [iso_to_s(r["timestamp"]) for r in calls["gate-limb-a"]]
        if times != sorted(times):
            raise AssertionError(f"timestamps not monotonic: {times}")
        marked = kinds.index("acquisition_mark_finished")
        accounted_inside_teardown = min(accounting) > marked
        construction = next(r for r in records
                            if r["type"] == "acquisition_construction")
        for field in ("dataset_path", "frames_planned", "estimated_bytes",
                      "active_bound_s", "active_bound_term"):
            if construction.get(field) is None:
                raise AssertionError(f"construction record has no {field}")
        # The number the report does not state: the file's own span against the
        # tool call's measured wall time. A large disagreement means the record
        # is not describing this call.
        span = iso_to_s(stamps(records, "acquisition_teardown_completion")[0]) - \
            iso_to_s(stamps(records, "acquisition_construction")[0])
        state["limb_a"]["file_span_s"] = span
        drift = abs(span - wall_s)
        if drift > max(1.0, wall_s):
            raise AssertionError(
                f"file span {span:.3f}s disagrees with the call's {wall_s:.3f}s")
        state["limb_a"]["observed_order"] = kinds
        state["limb_a"]["accounted_inside_teardown"] = accounted_inside_teardown
        return (f"6/6 records, invariants hold; frames accounted "
                f"{'INSIDE teardown' if accounted_inside_teardown else 'before mark_finished'}; "
                f"order={kinds}; bound={construction['active_bound_s']:g}s "
                f"term={construction['active_bound_term']}; "
                f"file span {span:.3f}s vs call {wall_s:.3f}s")

    @limb("B - one-frame reference latency",
          "an incomplete record; the latency numbers themselves are a measurement")
    def limb_b():
        if "ctrl" not in state:
            raise NotExercised("limb 0 did not establish a bridge")
        history, acq_path = acquisitions_path("limbB")
        session_id = history.name.replace("_history.jsonl", "_history")
        writer = AcquisitionDiagnosticWriter(AuditLog(acq_path))
        walls = []
        for index in range(args.latency_runs):
            _, wall_s = drive(writer, session_id, f"gate-limb-b-{index}", 1,
                              f"block75a_limbB_{index}")
            walls.append(wall_s)
        writer.close()
        records, torn = read_jsonl(acq_path)
        if torn:
            raise AssertionError(f"unparseable line(s): {torn}")
        calls = by_call(records)
        # The *set*, not the sequence: limb A owns ordering, and one
        # out-of-order callback must not also fail this limb. A limb that fails
        # for another limb's cause cannot be scored (58a's cascade, reversed).
        incomplete = {call: lifecycle_kinds(rs) for call, rs in calls.items()
                      if set(lifecycle_kinds(rs)) != set(COMPLETE_LIFECYCLE)}
        if len(calls) != args.latency_runs or incomplete:
            raise AssertionError(
                f"{len(calls)}/{args.latency_runs} calls recorded; "
                f"incomplete: {incomplete}")
        end_to_end, finalization = [], []
        for records_for_call in calls.values():
            begin = iso_to_s(stamps(records_for_call, "acquisition_construction")[0])
            marked = iso_to_s(stamps(records_for_call, "acquisition_mark_finished")[0])
            done = iso_to_s(stamps(records_for_call,
                                   "acquisition_teardown_completion")[0])
            end_to_end.append(done - begin)
            finalization.append(done - marked)

        def summary(values):
            ordered = sorted(values)
            return {
                "n": len(ordered),
                "p50": statistics.median(ordered),
                "p95": ordered[min(len(ordered) - 1,
                                   int(round(0.95 * (len(ordered) - 1))))],
                "max": ordered[-1],
            }

        state["limb_b"] = {
            "acquisitions_file": str(acq_path),
            "file_end_to_end_s": summary(end_to_end),
            "file_finalization_s": summary(finalization),
            "measured_call_wall_s": summary(walls),
        }
        e2e, fin = summary(end_to_end), summary(finalization)
        return (f"n={e2e['n']} end-to-end p50={e2e['p50']:.3f} p95={e2e['p95']:.3f} "
                f"max={e2e['max']:.3f}s; finalization p50={fin['p50']:.3f} "
                f"max={fin['max']:.3f}s. REFERENCE ARM - M2 sets 75b's constants")

    def spawn_child(mode, acq_path, frames):
        driver = args.out / "block75a-child.py"
        driver.write_text(CHILD_DRIVER, encoding="utf-8")
        return subprocess.Popen(
            [sys.executable, str(driver), str(ROOT), mode, str(acq_path),
             state["save_root"], str(args.port), args.safety_config or "",
             str(frames), str(args.exposure_ms)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            # Explicit, never inherited: a child that inherits a console can
            # register the shortcut exit pause and block after printing (58e).
            stdin=subprocess.DEVNULL, text=True,
        )

    @limb("C - normal shutdown flushes the tail",
          "a queued record lost when the entry point closes the writer")
    def limb_c():
        if "ctrl" not in state:
            raise NotExercised("limb 0 did not establish a bridge")
        _, acq_path = acquisitions_path("limbC")
        child = spawn_child("shutdown", acq_path, 1)
        output = child.communicate(timeout=600)[0]
        state["limb_c"] = {"exit": child.returncode, "output": output[-2000:],
                           "acquisitions_file": str(acq_path)}
        if child.returncode != 0 or "CHILD_CLEAN_EXIT" not in output:
            raise AssertionError(
                f"child exited {child.returncode} without a clean exit: "
                f"{output[-400:]!r}")
        records, torn = read_jsonl(acq_path)
        if torn:
            raise AssertionError(f"unparseable line(s): {torn}")
        calls = by_call(records)
        # A set: this limb's claim is that nothing was *lost*, and the engine's
        # ordering is limb A's business. The demo machine failed this limb for
        # limb A's cause while its own claim held perfectly.
        kinds = lifecycle_kinds(calls.get("child-complete", []))
        lost = set(COMPLETE_LIFECYCLE) - set(kinds)
        if lost:
            raise AssertionError(
                f"the tail did not survive a normal exit; missing {sorted(lost)} "
                f"from {kinds}")
        return "the child's full lifecycle, teardown completion included, is on disk"

    @limb("D - an abrupt end still leaves a record",
          "a lifecycle record still queued when the process ends")
    def limb_d():
        if "ctrl" not in state:
            raise NotExercised("limb 0 did not establish a bridge")
        _, acq_path = acquisitions_path("limbD")
        child = spawn_child("kill", acq_path, args.kill_frames)
        deadline = time.monotonic() + 300.0
        submitted_at = None
        # Keep the child's own words. The first selftest run reported only
        # "child ended on its own (exit 0)" because this limb discarded the
        # pipe -- and the cause was in it. Score from artifacts (step 6), and
        # the child's stdout is one.
        state.setdefault("limb_d", {})
        started_waiting = time.monotonic()
        try:
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    output = child.communicate()[0] or ""
                    state["limb_d"]["child_output"] = output[-3000:]
                    returned = "CHILD_LONG_RESULT" in output
                    raise NotExercised(
                        f"child ended on its own (exit {child.returncode}) "
                        f"before the long call could be interrupted"
                        + (" - the long call RETURNED rather than running long "
                           "enough to interrupt; raise --kill-frames, and read "
                           "its result below in case this machine refused it"
                           if returned else "")
                        + f". Its output: {output[-800:]!r}")
                records, _ = read_jsonl(acq_path)
                pending = by_call(records).get("child-killed", [])
                if any(r["type"] == "acquisition_event_submission" for r in pending):
                    submitted_at = time.monotonic() - started_waiting
                    break
                time.sleep(0.05)
            if submitted_at is None:
                raise NotExercised(
                    "the long call's submission record never reached the disk; "
                    "nothing to interrupt")
            # End it exactly here: the record is on disk, the acquisition is
            # still running, and no teardown has happened. This is the state the
            # operator's own close left behind.
            child.kill()
            child.wait(timeout=120)
        finally:
            if child.poll() is None:
                child.kill()
            if not state["limb_d"].get("child_output"):
                # Only if the poll loop did not already take it: a second
                # communicate() on the same Popen returns empty and would
                # clobber the one useful artifact this limb has.
                try:
                    state["limb_d"]["child_output"] = (
                        child.communicate(timeout=30)[0] or "")[-3000:]
                except Exception as exc:            # noqa: BLE001 - reported
                    state["limb_d"]["child_output"] = f"unreadable: {exc}"
        records, torn = read_jsonl(acq_path)
        state["limb_d"].update({"exit": child.returncode, "torn": torn,
                                "acquisitions_file": str(acq_path),
                                "records": len(records),
                                "submission_to_disk_wait_s": submitted_at})
        if torn:
            raise AssertionError(
                f"the abrupt end tore a line, which would cost the whole file "
                f"a reader: {torn}")
        calls = by_call(records)
        before = lifecycle_kinds(calls.get("child-before-kill", []))
        lost = set(COMPLETE_LIFECYCLE) - set(before)
        if lost:
            raise AssertionError(
                f"the completed call did not survive the end; missing "
                f"{sorted(lost)} from {before}")
        killed = lifecycle_kinds(calls.get("child-killed", []))
        if "acquisition_construction" not in killed or \
                "acquisition_event_submission" not in killed:
            raise AssertionError(
                f"the interrupted call left no beginning: {killed}")
        if "acquisition_teardown_completion" in killed:
            raise NotExercised(
                "the long call finished before the end landed; raise "
                "--kill-frames and run limb D again")
        return (f"{len(records)} records survived; the interrupted call left "
                f"{killed} and no completion, which is the incident's own "
                f"shape. Its submission record was readable "
                f"{submitted_at:.2f}s after the child started running it")

    @limb("F - a real session's history join",
          "a session whose acquisition records name a tool call the history does not")
    def limb_f():
        # Seeded from what a session produces anyway, never from a marker the
        # operator is asked to plant (69a): a setup step skipped three times
        # should be deleted, not repeated.
        histories = sorted(Path.cwd().glob("*_microclaw_history.jsonl"))
        pairs = [(h, Path(str(h).replace("_history.jsonl", "_acquisitions.jsonl")))
                 for h in histories]
        pairs = [(h, a) for h, a in pairs if a.exists()]
        if not pairs:
            raise NotExercised(
                f"no *_microclaw_history.jsonl with an _acquisitions.jsonl sibling "
                f"in {Path.cwd()}; this machine has run no D4 session yet")
        history, acq_path = pairs[-1]
        transcript, _ = read_jsonl(history)
        records, torn = read_jsonl(acq_path)
        if torn:
            raise AssertionError(f"unparseable line(s) in {acq_path.name}: {torn}")
        transcript_ids = set()
        for message in transcript:
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        transcript_ids.add(block.get("id"))
        recorded = {r.get("tool_call_id") for r in records} - {None}
        orphans = sorted(recorded - transcript_ids)
        state["limb_f"] = {"history": history.name, "acquisitions": acq_path.name,
                           "recorded_calls": len(recorded),
                           "orphans": orphans}
        if not recorded:
            raise NotExercised(
                f"{acq_path.name} carries no tool_call_id, so there is nothing to "
                "join; it predates the correlation id")
        if orphans:
            raise AssertionError(
                f"{len(orphans)} recorded call(s) name no tool_use in "
                f"{history.name}: {orphans[:5]}")
        return (f"{len(recorded)} correlated call(s) in {acq_path.name} all join "
                f"to a tool_use in {history.name}")

    return finish(args, state)


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
