"""Off-rig selftest for block 61b's demo gate: run it on BOTH trees.

CLAUDE.md's rule is that a gate is code, and handing an operator code nobody
executed is the defect this workflow keeps paying for. So this runs the gate
program against two installed-shaped trees and checks that its verdict
*discriminates*: PASS on the 61b tree, FAIL on `main`, where the anchors do not
exist. A selftest that only proves the gate passes on the good tree proves
nothing about whether it can fail.

Four things it deliberately does, each paid for by an earlier block.

**The artifacts are built from the demo machine's own recorded payload**, not
from what this gate's author expected. The focus-lock result below is the
`focus` block of `design/61-block61a-system-state.json`, captured on that
machine during 61a's gate: device `Autofocus`, adapter `DAutoFocus`. 60b's gate
failed its only rig limb because its fake wrote the filename its glob expected;
a fake that encodes your assumption is not a test of it, and gate code gets no
review pass.

**The exported script is produced by the installed exporter**, not hand-written
here, for the same reason.

**H5's negative limb is proved able to fail.** The checklist's coordinator note
requires it: a limb that cannot fail is not a criterion, and 58a's opt-out limb
passed three rounds for exactly that reason. So a second synthetic history --
identical except that the agent loaded `nikon-pfs` -- is run through the gate,
and H5 must go red on it.

**NOT EXERCISED is proved never to be a pass.** The good tree is run once with
no artifacts at all: the history limbs must report NOT EXERCISED and the gate
must still exit nonzero.

The gate touches no Micro-Manager bridge -- it reads schemas, the prompt, and
JSON the session wrote -- so there is no Core collection to shape here, and a
decorative Core fake would be worse than none. What would have been the bridge
fake is replaced by the check that matters for THIS gate: that its
`_import_installed` guard actually refuses a checkout. 59a lost a rig trip to
`list()` over a Core collection because every fake was Python-friendly; the
equivalent mistake here is a gate that silently scores the repository instead of
the install, so that is what is exercised.

    python3 design/61-block61b-gate-selftest.py --main-tree /path/to/main-worktree

Run from the 61b tree. Do not commit its output.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

HERE = Path(__file__).resolve()
GATE = HERE.parent / "61-block61b-demo-gate.py"

# The demo machine's own focus-lock reading, from 61a's gate evidence
# (design/61-block61a-system-state.json, "focus"). A real hardware focus lock
# that is not a Nikon PFS -- which is the CRISP shape of the suite's negative
# limb, live on the machine that runs this gate.
DEMO_FOCUS_STATE = {
    "device": "Autofocus",
    "engaged": False,
    "property": "continuous focus device Autofocus",
    "status_properties": {
        "Description": "Demo auto-focus adapter",
        "HubID": "",
        "Name": "DAutoFocus",
    },
}


def _message_pair(call_id, name, params, result):
    return [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": call_id, "name": name, "input": params}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call_id,
             "content": json.dumps(result)}]},
    ]


def _write_history(path: Path, *, loaded_nikon: bool) -> Path:
    """A session shaped like the runbook's, as JSONL microclaw itself can read.

    `load_history` accepts a JSON array or JSONL; the saved-history files are
    JSONL named `*_microclaw_history.jsonl` (conversation.py). Written in that
    shape so the gate is exercised on the format it will actually meet.
    """
    messages = [{"role": "user", "content": "Load the smlm skill and name its first heading."}]
    messages += _message_pair("c1", "load_skill", {"name": "smlm"},
                              {"documentation": "# Single-Molecule Localization Microscopy\n..."})
    messages.append({"role": "user", "content": "Engage the focus lock on this microscope."})
    messages += _message_pair("c2", "get_focus_lock_state", {}, DEMO_FOCUS_STATE)
    if loaded_nikon:
        messages += _message_pair("c3", "load_skill", {"name": "nikon-pfs"},
                                  {"documentation": "Nikon rigs:\n..."})
    messages += _message_pair("c4", "set_focus_lock", {"enabled": True},
                              {"engaged": True,
                               "property": "continuous focus device Autofocus",
                               "continuous_focus_device": "Autofocus",
                               "value": True})
    path.write_text(
        "".join(json.dumps(m, ensure_ascii=False, separators=(",", ":")) + "\n"
                for m in messages), encoding="utf-8")
    return path


def _export_with(interpreter: Path, history: Path, out: Path) -> Path:
    """Produce the exported script with the INSTALLED exporter, not by hand."""
    snippet = (
        "import sys\n"
        "from pathlib import Path\n"
        "from microclaw.conversation import load_history\n"
        "from microclaw.tools import export_session_script\n"
        "class G:\n"
        "    def __init__(self, r): self.root = Path(r)\n"
        "    def resolve_in_workspace(self, p): return str(self.root / p)\n"
        "msgs = load_history(sys.argv[1]).messages\n"
        "export_session_script(None, G(sys.argv[2]), 'routine.py', msgs)\n"
    )
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(interpreter), "-c", snippet, str(history), str(out)],
                   check=True, capture_output=True, text=True)
    return out / "routine.py"


def _install(tree: Path, root: Path) -> tuple[Path, Path]:
    """Install `tree` into a venv and return (interpreter, that venv's site-packages).

    `system_site_packages=True` with `--no-deps` is deliberate, and 61a's first
    version got it wrong. Installing with `--no-deps` into an ISOLATED venv left
    no numpy and no anthropic, so every limb died on an unrelated
    ModuleNotFoundError and BOTH trees "failed" -- which would have reported
    discrimination the gate had not actually demonstrated. The demo machine runs
    `install.bat`, which installs `.[serve]` with its whole dependency set, so an
    environment without them is not shaped like the thing under test.

    The borrowed environment can also contain an editable microclaw, so the
    caller MUST check that the gate reported an origin inside `site` below;
    otherwise this would score the checkout, which is the failure the whole file
    exists to prevent.
    """
    environment = root / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(environment)
    interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([str(interpreter), "-m", "pip", "install", "--no-deps", "-q", str(tree)],
                   check=True, capture_output=True, text=True)
    site = subprocess.run(
        [str(interpreter), "-c",
         "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        check=True, capture_output=True, text=True).stdout.strip()
    return interpreter, Path(site)


def _imported_from(output: str) -> Path | None:
    """The package directory the gate said it scored, resolved.

    Compared as resolved paths, never as strings: the venv reports its
    site-packages under /var on macOS while an import resolves the same file
    under /private/var, and a string compare would report a shadowed install
    that had not happened.
    """
    for line in output.splitlines():
        if line.startswith("installed microclaw: "):
            return Path(line.split(": ", 1)[1]).resolve().parent
    return None


def _run_gate(interpreter: Path, workdir: Path, *extra: str) -> tuple[int, str]:
    """Run the gate with the installed interpreter, from outside any checkout."""
    completed = subprocess.run(
        [str(interpreter), str(GATE), "--output", str(workdir / "evidence"), *extra],
        cwd=workdir, capture_output=True, text=True,
    )
    return completed.returncode, completed.stdout + completed.stderr


def _status_of(output: str, limb: str) -> str | None:
    for line in output.splitlines():
        if f": {limb} " in line:
            return line.split(":", 1)[0].strip()
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-tree", type=Path, required=True,
                        help="A checkout of main, where the anchors do not exist.")
    parser.add_argument("--this-tree", type=Path, default=HERE.parents[1])
    args = parser.parse_args()

    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clean = _write_history(root / "clean_microclaw_history.jsonl", loaded_nikon=False)
        misrouted = _write_history(root / "bad_microclaw_history.jsonl", loaded_nikon=True)

        # 1. The 61b tree with no artifacts: the history limbs must report NOT
        #    EXERCISED, and the gate must STILL exit nonzero. NOT EXERCISED is
        #    never a pass (58a), and a gate that exits 0 on it would let an
        #    operator bank a run that measured nothing.
        good = root / "good"
        good.mkdir()
        interpreter, site = _install(args.this_tree, good)
        code, output = _run_gate(interpreter, good)
        print(f"--- 61b tree, no artifacts: exit {code}\n{output}")
        if code == 0:
            failures.append("the gate exited 0 with no session artifacts; "
                            "NOT EXERCISED was treated as a pass")
        for name in ("H4", "H5", "H6"):
            if _status_of(output, name) != "NOT EXERCISED":
                failures.append(f"{name} was {_status_of(output, name)} with no "
                                f"artifacts, expected NOT EXERCISED")
        imported = _imported_from(output)
        if imported != (site / "microclaw").resolve():
            failures.append(
                f"the gate imported microclaw from {imported}, not from "
                f"{site / 'microclaw'}; the ambient environment shadowed the "
                f"install and this scored the wrong tree")

        # 2. The 61b tree with the runbook's artifacts must PASS.
        exported = _export_with(interpreter, clean, root / "exported")
        code, output = _run_gate(interpreter, good, "--history", str(clean),
                                 "--exported", str(exported))
        print(f"--- 61b tree, with artifacts: exit {code}\n{output}")
        if code != 0:
            failures.append("the 61b tree did not pass its own gate")
        if "BLOCK 61b DEMO GATE PASSED" not in output:
            failures.append("no PASSED banner on the 61b tree")

        # 3. H5's negative must be ABLE to fail. Same session, except that the
        #    agent loaded nikon-pfs on a rig whose lock is `Autofocus`. If this
        #    still passes, H5 is decoration and the block's central claim is
        #    unmeasured.
        code, output = _run_gate(interpreter, good, "--history", str(misrouted),
                                 "--exported", str(exported))
        print(f"--- 61b tree, misrouted session: exit {code}\n{output}")
        if _status_of(output, "H5") != "FAIL":
            failures.append(
                f"H5 was {_status_of(output, 'H5')} on a session that loaded "
                f"nikon-pfs on a non-PFS rig; the negative limb cannot fail")

        # 4. main must FAIL. If it does not, the gate cannot detect the absence
        #    of the thing the block adds, and every limb above is decoration.
        bad = root / "bad"
        bad.mkdir()
        interpreter, site = _install(args.main_tree, bad)
        code, output = _run_gate(interpreter, bad, "--history", str(clean))
        print(f"--- main tree: exit {code}\n{output}")
        if code == 0:
            failures.append("main passed the gate: the gate does not discriminate")
        if _imported_from(output) != (site / "microclaw").resolve():
            failures.append("the main-tree run did not import the tree it installed")
        # And it must fail for the RIGHT reason. A dependency error would fail
        # too, and would report discrimination this gate had not demonstrated.
        if "carries no nikon-pfs anchor" not in output:
            failures.append(
                "main failed for a reason other than the missing anchors; read "
                "the output above before believing the discrimination")
        if "the focus-lock ordering invariant is not in the prompt" not in output:
            failures.append("main's H2 did not fail on the missing ordering invariant")

        # 5. The checkout guard must refuse when run from inside the repository
        #    with the repository importable, which is how an operator would run
        #    it by accident. 61a lost an operator round to a path defect.
        env = dict(os.environ, PYTHONPATH=str(args.this_tree))
        completed = subprocess.run(
            [sys.executable, str(GATE), "--output", str(root / "checkout-evidence")],
            cwd=str(args.this_tree), capture_output=True, text=True, env=env,
        )
        print(f"--- checkout guard: exit {completed.returncode}\n"
              f"{completed.stdout + completed.stderr}")
        if "Refusing to score the checkout" not in (completed.stdout + completed.stderr):
            failures.append("the gate scored the checkout instead of the install")

    print()
    print(json.dumps({"failures": failures}, indent=2))
    print("SELFTEST " + ("FAILED" if failures else "PASSED"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
