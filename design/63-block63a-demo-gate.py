"""Block 63a demo-machine gate: an ordinary session's script must not die.

Every limb here is a computation, so it ships as a program rather than pasted
PowerShell blocks: each limb reports independently, a refusal in one cannot hide
the rest, and the exit status is nonzero if any limb did not pass (58a). The
half a program cannot judge -- an agent driving a real session -- is in the
runbook beside this file.

What this gate is for. `export_session_script` plants a `raise RuntimeError`
into any script that recorded a tool carrying none of the three marker
attributes. That has killed three blocks' own gate scripts, each time on a rig,
and eleven tools still carried no marker on 2026-08-29. So the criterion is not
"the exporter works" -- it is that a session which uses these ordinary tools
still exports a script that runs.

This program reads no hardware, opens no bridge, and needs no safety config or
workspace_dir (60b: a gate must not require configuration the product does not
require). G1 and G2 read the installed build; the rest score files the
runbook's session produced, and are NOT EXERCISED -- never a pass -- when those
files are absent.

**G3 is the control.** G4 cannot fail if the session recorded none of the
eleven, and 58a's opt-out limb passed three rounds for exactly that reason. So
G3 counts which of the eleven the session actually reached, and fails when the
answer is none.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

RESULTS = []

# design/63's decision table, as the gate must find it. Not a count: a runner
# that decorated all eleven `@emits_nothing` would pass a count and would have
# silently deleted four hardware writes from every exported script.
DECIDED = {
    "calibrate_snr_threshold": "emits_nothing",
    "verify_emu_laser_power_calibration": "emits_nothing",
    "export_dataset_as_tiff": "emits_nothing",
    "shutter_declared_illumination": "emits",
    "set_emu_laser_power_percentage": "emits",
    "find_features": "emits",
    "center_feature": "emits",
    "run_multiposition_with_autofocus": "emits",
    "calibrate_stage_to_camera": "refuses",
    "snap_to_album": "refuses",
    "run_mda": "refuses",
}
MARKERS = {
    "emits": "_microclaw_emitter",
    "emits_nothing": "_microclaw_emits_nothing",
    "refuses": "_microclaw_refusal_reason",
}
# The sentence the exporter writes for a tool with no marker at all. It must
# never reach an operator's script again; a refusal must say why THIS tool
# cannot be emitted.
DEFAULT_REFUSAL = "no standalone emitter has been implemented for this tool"


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


def _import_installed():
    """Import microclaw as the demo machine runs it, never as a checkout.

    A checkout ahead of the install would make every limb below report on
    source the operator is not running. Refuse rather than guess.
    """
    here = Path(__file__).resolve()
    import microclaw
    origin = Path(microclaw.__file__).resolve()
    if origin.parent.parent == here.parent.parent:
        raise SystemExit(
            f"Refusing to score the checkout at {origin.parent}. Run this file "
            f"with the installed interpreter from outside the repository; see "
            f"the runbook's Step 1."
        )
    return origin


def _classes_of(fn):
    return sorted(k for k, attr in MARKERS.items() if hasattr(fn, attr))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("block63a-demo-evidence"))
    parser.add_argument(
        "--history", type=Path, default=None,
        help="The runbook session's saved *_microclaw_history.jsonl.")
    parser.add_argument(
        "--exported", type=Path, default=None,
        help="Script written by export_session_script in the runbook's session.")
    parser.add_argument(
        "--standalone-log", type=Path, default=None,
        help="Console capture of running that script with microclaw closed.")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, args.output / "gate.txt")

    # The limbs are nested here, and that is load-bearing: @limb runs the
    # function it decorates immediately, so a limb defined at module level would
    # execute at import -- before the log exists and before the checkout guard
    # has had a chance to refuse. Same shape as 61a's and 61b's gates.
    origin = _import_installed()
    print(f"installed microclaw: {origin}")
    print(f"interpreter: {sys.executable}")

    @limb("G1 every registry tool carries exactly one export marker",
          fails_if="any tool carries none (its recorded call plants a raise in "
                   "every exported script) or more than one")
    def _g1():
        from microclaw.tools import TOOL_REGISTRY
        undecorated = sorted(n for n, f in TOOL_REGISTRY.items() if not _classes_of(f))
        doubled = sorted(n for n, f in TOOL_REGISTRY.items() if len(_classes_of(f)) > 1)
        if undecorated:
            raise AssertionError(f"undecorated: {undecorated}")
        if doubled:
            raise AssertionError(f"more than one marker: {doubled}")
        counts = {}
        for fn in TOOL_REGISTRY.values():
            counts[_classes_of(fn)[0]] = counts.get(_classes_of(fn)[0], 0) + 1
        return (f"{len(TOOL_REGISTRY)} tools, 0 undecorated, "
                f"{json.dumps(counts, sort_keys=True)}")

    @limb("G2 the eleven carry the marker design/63 decided, each one",
          fails_if="a hardware-writing tool was swept into @emits_nothing, which "
                   "would delete its write from every exported script silently")
    def _g2():
        from microclaw.tools import TOOL_REGISTRY
        wrong = {}
        for name, expected in DECIDED.items():
            fn = TOOL_REGISTRY.get(name)
            if fn is None:
                wrong[name] = "not in TOOL_REGISTRY"
                continue
            got = _classes_of(fn)
            if got != [expected]:
                wrong[name] = f"expected {expected}, found {got or 'nothing'}"
        if wrong:
            raise AssertionError(json.dumps(wrong, sort_keys=True))
        return f"all 11 as decided: {json.dumps(DECIDED, sort_keys=True)}"

    @limb("G3 the session actually reached some of the eleven",
          fails_if="it reached none, in which case G4 measured nothing about "
                   "this block and is not evidence")
    def _g3():
        if args.history is None or not args.history.exists():
            raise NotExercised("no --history was supplied")
        from microclaw.conversation import load_history
        from microclaw.tools import _recorded_tool_calls
        calls = _recorded_tool_calls(load_history(str(args.history)).messages)
        used = [name for name, _ in calls]
        reached = sorted({n for n in used if n in DECIDED})
        emitting = [n for n in reached if DECIDED[n] == "emits"]
        refusing = [n for n in reached if DECIDED[n] == "refuses"]
        if not reached:
            raise AssertionError(
                f"none of the eleven were called; session ran {sorted(set(used))}")
        detail = (f"{len(reached)}/11 reached: {reached} "
                  f"({len(emitting)} emitting, {len(refusing)} refusing)")
        if not emitting:
            raise AssertionError(
                "no tool with a new emitter was called, so no new emitter ran: " + detail)
        return detail

    @limb("G4 that session's exported script",
          fails_if="it carries a NOT EMITTED for a tool that should emit, does "
                   "not compile, or imports microclaw")
    def _g4():
        if args.exported is None or not args.exported.exists():
            raise NotExercised("no --exported script was supplied")
        source = args.exported.read_text(encoding="utf-8")
        compile(source, str(args.exported), "exec")
        if "import microclaw" in source or "from microclaw" in source:
            raise AssertionError("the exported script imports microclaw; it is not standalone")
        refused = [line.split("NOT EMITTED:", 1)[1].strip()
                   for line in source.splitlines()
                   if line.lstrip().startswith("# NOT EMITTED:")]
        unexpected = [r for r in refused
                      if DECIDED.get(r.split("—")[0].strip().split(" ")[0]) != "refuses"]
        if unexpected:
            raise AssertionError(f"refusals for tools that should emit: {unexpected}")
        # Which emitting tools actually put executable code in the script, and
        # which emitted only a comment. A faithful emission of a call that did
        # nothing IS a comment -- the demo config declares no illumination, so
        # `shutter_declared_illumination` correctly emits one -- but then that
        # tool's write path was not exercised, and a limb that reports it as
        # "reached" without saying so hides a NOT EXERCISED inside a PASS.
        sections = {}
        current = None
        for line in source.splitlines():
            if line.startswith("# RECORDED TOOL: "):
                current = line.split(": ", 1)[1].strip()
                sections[current] = []
            elif current is not None:
                sections[current].append(line)
        comment_only = sorted(
            name for name, body in sections.items()
            if DECIDED.get(name) == "emits"
            and not any(l.strip() and not l.lstrip().startswith("#") for l in body)
        )
        return (f"{len(source.splitlines())} lines, compiles, standalone; "
                f"{len(refused)} refusal(s), all from the decided-refuses set: {refused}"
                + (f"; EMITTED ONLY A COMMENT (write path not exercised): "
                   f"{comment_only}" if comment_only else ""))

    @limb("G5 every refusal in that script says why THAT tool cannot be emitted",
          fails_if="a refusal carries the default sentence, which is the "
                   "undecorated-tool failure this block exists to remove")
    def _g5():
        if args.exported is None or not args.exported.exists():
            raise NotExercised("no --exported script was supplied")
        source = args.exported.read_text(encoding="utf-8")
        if DEFAULT_REFUSAL in source:
            raise AssertionError(
                "the exported script carries the default refusal: " +
                next(line for line in source.splitlines() if DEFAULT_REFUSAL in line))
        reasons = [line for line in source.splitlines()
                   if line.lstrip().startswith("# NOT EMITTED:")]
        if not reasons:
            raise NotExercised(
                "this session recorded no refusing tool, so the specific-reason "
                "check had nothing to read")
        return f"{len(reasons)} refusal(s), none default: {reasons}"

    @limb("G6 the emitted script ran standalone, with microclaw closed",
          fails_if="it raised, or its exit status was nonzero; a script that "
                   "compiles is not a script that works (52b)")
    def _g6():
        if args.standalone_log is None or not args.standalone_log.exists():
            raise NotExercised("no --standalone-log was supplied")
        text = args.standalone_log.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            raise AssertionError(
                "the capture is empty, which proves nothing either way (43h)")
        if "Traceback (most recent call last)" in text:
            raise AssertionError(
                "the standalone run raised: " + text.strip().splitlines()[-1])
        if "EXIT=0" not in text:
            raise AssertionError(
                "the capture carries no EXIT=0 line; run the runbook's command "
                "verbatim so the exit status is recorded: " +
                text.strip().splitlines()[-1])
        return f"{len(text.splitlines())} lines captured, exit 0, no traceback"

    (args.output / "results.json").write_text(
        json.dumps({"results": RESULTS, "installed": str(origin),
                    "interpreter": sys.executable}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    failed = [item for item in RESULTS if item["status"] != "PASS"]
    print("BLOCK 63a DEMO GATE " + ("FAILED" if failed else "PASSED"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
