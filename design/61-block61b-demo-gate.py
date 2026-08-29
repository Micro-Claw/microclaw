"""Block 61b demo-machine gate: the Nikon anchors, on a rig that is not a Nikon.

Every limb here is a computation, so it ships as a program rather than pasted
PowerShell blocks: each limb reports independently, a refusal in one cannot hide
the rest, and the exit status is nonzero if any limb did not pass (58a). The
half that needs a human -- an agent *choosing* not to load a skill -- is in the
runbook beside this file, because that is the half a program cannot judge.

What needs the demo machine is narrow, and it is the negative direction. The Ti
is unreachable pre-merge (design/61, "Block 61b -- replay first, demo machine
second"), so the positive route is carried-forward row R1 and is not scored
here. What this machine can prove is that an ordinary rig is unharmed.

**And this machine is a better negative than the design expected.** The demo
config configures an autofocus device -- label `Autofocus`, adapter
`DAutoFocus`, measured in `design/61-block61a-system-state.json`. So the rig
that runs this gate has a real hardware focus lock that is *not* a Nikon PFS,
which is the CRISP shape of the suite's negative limb, live. The design told
this gate to mark the no-device limb NOT EXERCISED if the demo config had a
device; it does, so H4 says so rather than passing.

This program reads no hardware, opens no bridge, and needs no safety config or
workspace_dir. A gate must not require configuration the product does not
require (60b), so there is nothing to prepare and nothing to put back. The one
limb that scores live-rig evidence (H4) reads a JSON file the runbook's session
produced; where that file is absent it is NOT EXERCISED, never a pass.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

RESULTS = []


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("block61b-demo-evidence"))
    parser.add_argument(
        "--history", type=Path, default=None,
        help="The runbook session's saved *_microclaw_history.jsonl.")
    parser.add_argument(
        "--exported", type=Path, default=None,
        help="Script written by export_session_script in the runbook's session.")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, args.output / "gate.txt")

    # The limbs are nested here, and that is load-bearing: @limb runs the
    # function it decorates immediately, so a limb defined at module level would
    # execute at import -- before the log exists and before the checkout guard
    # has had a chance to refuse. Same shape as 61a's and 60b's gates.
    origin = _import_installed()
    print(f"installed microclaw: {origin}")
    print(f"interpreter: {sys.executable}")

    @limb("H1 the three anchors shipped, and each names which lock and how to identify it",
          fails_if="an anchor is missing, or says 'this kind of hardware lock' without "
                   "naming the Nikon PFS and the device value that identifies it")
    def _h1():
        from microclaw.tools_schema import TOOLS
        by_name = {tool["name"]: tool for tool in TOOLS}
        found = {}
        for name in ("run_autofocus", "get_focus_lock_state", "set_focus_lock"):
            description = by_name[name]["description"]
            if "nikon-pfs" not in description:
                raise AssertionError(f"{name} carries no nikon-pfs anchor")
            # The anchor must discriminate. Its first shipped form read "on a
            # rig with this kind of hardware lock", whose antecedent was a
            # generic hardware focus lock -- which tells an agent on this very
            # machine, whose lock is `Autofocus`, to load the Nikon skill. No
            # suite test can catch that: the discriminator fixture asserts what
            # the tool returns, not how a model reads a description.
            if "Perfect Focus" not in description:
                raise AssertionError(f"{name}'s anchor does not name which lock it means")
            anchor = next(s for s in description.split(". ") if "nikon-pfs" in s)
            if "device" not in anchor:
                raise AssertionError(
                    f"{name}'s anchor does not identify the lock by its device value: {anchor!r}")
            found[name] = anchor.strip()[:80]
        return f"3 anchors, each naming the PFS and the device: {json.dumps(found, sort_keys=True)}"

    @limb("H2 the core prompt carries the ordering invariant, vendor-neutrally",
          fails_if="the invariant is missing, names a vendor, or the Nikon paragraph "
                   "61b must retain has gone early")
    def _h2():
        from microclaw.agent import SYSTEM_PROMPT
        sentence = next(
            (line for line in SYSTEM_PROMPT.splitlines()
             if "before any operation that engages or adjusts a hardware focus lock"
             in line.lower()), None)
        if sentence is None:
            raise AssertionError("the focus-lock ordering invariant is not in the prompt")
        if "get_focus_lock_state" not in sentence:
            raise AssertionError(f"the invariant names no discovery tool: {sentence!r}")
        named = [word for word in ("Nikon", "PFS", "TIPFS") if word in sentence]
        if named:
            raise AssertionError(f"the invariant is meant to be vendor-neutral, names {named}")
        if "(stage, channel, exposure, focus lock)" not in SYSTEM_PROMPT:
            raise AssertionError("focus lock is not named as a dedicated-tool operation")
        # 61b ships with the Nikon procedure still in the core prompt. Its
        # removal is 61c and is authorized only by a positive Ti confirmation
        # (row R1). A gate that let it vanish early would hide exactly that.
        if "Do BOTH of these every time you engage the lock" not in SYSTEM_PROMPT:
            raise AssertionError("the Nikon paragraph 61b must retain is gone")
        return f"invariant present and vendor-neutral, Nikon paragraph retained, prompt {len(SYSTEM_PROMPT)} chars"

    @limb("H3 a recorded focus-lock read exports a script with no planted refusal",
          fails_if="get_focus_lock_state is undecorated, so every exported script of "
                   "a session that read the lock carries NOT EMITTED and a RuntimeError")
    def _h3():
        # Read the property off the export, not off a decorator attribute: the
        # observable failure is the planted refusal, and that is what shipped
        # three times before (43h generate_and_save_hook, 47 set_roi/clear_roi,
        # 52a move_named_stage). Needs no rig and no workspace_dir -- the guard
        # shim below does only what the exporter asks of it, placing the file.
        import json as _json
        import tempfile
        from microclaw.tools import export_session_script

        class _Guard:
            def __init__(self, root):
                self.root = Path(root)

            def resolve_in_workspace(self, path):
                return str(self.root / path)

        records = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "gate-h3", "name": "get_focus_lock_state",
                 "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "gate-h3",
                 "content": _json.dumps({"engaged": False, "device": "Autofocus"})}]},
        ]
        with tempfile.TemporaryDirectory() as root:
            export_session_script(None, _Guard(root), "routine.py", records)
            source = (Path(root) / "routine.py").read_text(encoding="utf-8")
        compile(source, "routine.py", "exec")
        if "NOT EMITTED" in source:
            raise AssertionError(
                "exported script carries " +
                next(line for line in source.splitlines() if "NOT EMITTED" in line))
        return f"exported {len(source.splitlines())} lines, compiles, no NOT EMITTED"

    def _session_calls():
        """The runbook session's tool calls, in order, read the product's way.

        `load_history` and `_recorded_tool_calls` are microclaw's own readers --
        the same ones the exporter uses -- so this cannot misread a format it
        does not own. 60b's gate globbed a filename it had guessed and reported
        a perfect run as FAIL; the saved-history glob is
        `*_microclaw_history.jsonl` (conversation.py), not `*.jsonl`.
        """
        if args.history is None or not args.history.exists():
            raise NotExercised(
                "no --history was supplied; drive the runbook's session first")
        from microclaw.conversation import load_history
        from microclaw.tools import _recorded_tool_calls
        loaded = load_history(args.history)
        for warning in loaded.warnings:
            print(f"  history warning: {warning}")
        return _recorded_tool_calls(loaded.messages)

    @limb("H4 the live rig's focus-lock identity",
          fails_if="get_focus_lock_state returned no device on a rig that has a lock")
    def _h4():
        calls = _session_calls()
        reads = [params.result for name, params in calls
                 if name == "get_focus_lock_state"]
        if not reads:
            raise NotExercised(
                "the session never called get_focus_lock_state, so there is no "
                "identity to score; see H5, which scores the same session")
        state = reads[0]
        if "device" not in state:
            # The design told this gate to mark the no-device limb NOT
            # EXERCISED if the demo config had a device. It does --
            # `Autofocus`/`DAutoFocus`, measured in
            # design/61-block61a-system-state.json -- so reaching here means a
            # different configuration was loaded. Report it; do not pass it.
            raise NotExercised(
                f"this configuration reports no autofocus device "
                f"({state.get('reason', 'no reason given')}), so the no-device "
                f"path was exercised and the non-PFS-lock path was not")
        device = str(state["device"])
        if "PFS" in device.upper():
            raise NotExercised(
                f"this rig's lock IS a PFS ({device!r}); H5's negative limbs do "
                f"not apply to it, and the positive route is row R1's Ti session")
        return f"lock device {device!r} -- a real focus lock that is not a PFS"

    @limb("H5 an ordinary rig is unharmed: no nikon-pfs, and state read before set",
          fails_if="the session loaded nikon-pfs on a rig whose lock is not a PFS, "
                   "or engaged the lock without reading its state first")
    def _h5():
        calls = _session_calls()
        order = [name for name, _ in calls]
        print(f"  tool calls in order: {order}")
        # RecordedParams IS the input dict (it subclasses dict) with the
        # recorded result attached as .result -- checked against tools.py:193,
        # not assumed.
        loaded = [params.get("name") for name, params in calls
                  if name == "load_skill"]
        if "nikon-pfs" in loaded:
            raise AssertionError(
                f"the session loaded nikon-pfs on a rig whose lock is not a PFS; "
                f"tool order was {order}")
        if "set_focus_lock" not in order:
            raise NotExercised(
                f"the session never called set_focus_lock, so the ordering "
                f"invariant was never put to the agent; tool order was {order}")
        if "get_focus_lock_state" not in order:
            raise AssertionError(
                f"set_focus_lock ran with no get_focus_lock_state before it; "
                f"tool order was {order}")
        if order.index("get_focus_lock_state") > order.index("set_focus_lock"):
            raise AssertionError(
                f"the lock was engaged before its state was read; order {order}")
        return (f"no nikon-pfs load; state read before set; "
                f"tool order {order}; skills loaded this session: "
                f"{loaded or 'none'}")

    @limb("H5c control: some skill IS reachable in this session",
          fails_if="no skill was loaded at all, which would make H5's negative "
                   "limb pass on a build where routing is entirely dead")
    def _h5c():
        # Without this, "the session did not load nikon-pfs" is equally
        # satisfied by a build where no skill is reachable at all. 58a's opt-out
        # limb passed three rounds for exactly that reason: a limb that cannot
        # fail is not a criterion. The runbook's R1 turn asks for a skill by
        # name, so this scores reachability, NOT routing -- an agent told to
        # load a skill and loading it proves the catalog and the loader work on
        # this build, and nothing about what it would choose unprompted.
        calls = _session_calls()
        loaded = [params.get("name") for name, params in calls if name == "load_skill"]
        if not loaded:
            raise AssertionError(
                "no skill was loaded anywhere in this session, so H5's negative "
                "measured nothing; run the runbook's R1 turn")
        return f"skills loaded: {loaded} (reachability, not routing)"

    @limb("H6 that session's exported script",
          fails_if="the exported script carries NOT EMITTED, does not compile, or "
                   "imports microclaw")
    def _h6():
        if args.exported is None or not args.exported.exists():
            raise NotExercised("no --exported script was supplied")
        source = args.exported.read_text(encoding="utf-8")
        compile(source, str(args.exported), "exec")
        if "NOT EMITTED" in source:
            raise AssertionError(
                "the exported script carries " +
                next(line for line in source.splitlines() if "NOT EMITTED" in line))
        if "import microclaw" in source or "from microclaw" in source:
            raise AssertionError("the exported script imports microclaw; it is not standalone")
        return f"{len(source.splitlines())} lines, compiles, standalone, no NOT EMITTED"

    (args.output / "results.json").write_text(
        json.dumps({"results": RESULTS, "installed": str(origin),
                    "interpreter": sys.executable}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    failed = [item for item in RESULTS if item["status"] != "PASS"]
    print("BLOCK 61b DEMO GATE " + ("FAILED" if failed else "PASSED"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
