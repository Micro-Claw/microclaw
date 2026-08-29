"""Off-rig selftest for block 63a's demo gate: run it on BOTH trees.

CLAUDE.md's rule is that a gate is code, and handing an operator code nobody
executed is the defect this workflow keeps paying for. So this installs two
trees, runs the gate against each, and checks that its verdict *discriminates*:
PASS on the 63a tree, FAIL on `main`, where eleven tools carry no export marker.
A selftest that only proves the gate passes on the good tree proves nothing
about whether it can fail.

Four things it deliberately does, each paid for by an earlier block.

**The exported script is produced by the installed exporter**, never hand
written here. 60b's gate failed its only rig limb because its fake wrote the
filename its glob expected; a fake that encodes your assumption is not a test of
it, and gate code gets no review pass.

**Every limb is proved able to fail**, not merely to pass. G3 gets a history
that reached none of the eleven, G5 a script carrying the default refusal, G6 a
capture with a traceback in it. 58a's opt-out limb passed three rounds because
it could not fail.

**NOT EXERCISED is proved never to be a pass.** The good tree is run once with
no artifacts at all: G3 through G6 must report NOT EXERCISED and the gate must
still exit nonzero.

**The checkout guard is exercised.** This gate opens no Micro-Manager bridge --
it reads the registry and files a session wrote -- so there is no Core
collection to shape here, and a decorative Core fake would be worse than none
(design/59's `list()`-over-a-StrVector defect has no analogue on this path).
What replaces it is the equivalent mistake for *this* gate: silently scoring the
repository instead of the install.

    python3 design/63-block63a-gate-selftest.py --main-tree /path/to/main-worktree

Run from the 63a tree. Do not commit its output.
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
GATE = HERE.parent / "63-block63a-demo-gate.py"


def _message_pair(call_id, name, params, result):
    return [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": call_id, "name": name, "input": params}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call_id,
             "content": json.dumps(result)}]},
    ]


# Result payloads copied from each tool's own return statement in
# microclaw/tools.py, not from what this file's author expected a session to
# look like. `shuttered` carries lists rather than tuples because that is what
# survives the JSON round trip into the history the gate actually reads.
def _write_history(path: Path, *, reaches_the_eleven: bool) -> Path:
    """A session shaped like the runbook's, as JSONL microclaw itself can read."""
    messages = [{"role": "user", "content": "Take a frame and look at the field."}]
    messages += _message_pair("c1", "get_exposure", {}, {"exposure_ms": 10.0})
    if reaches_the_eleven:
        messages += _message_pair("c2", "find_features", {}, {
            "n_spots": 3, "centroid_xy_px": [11.0, 9.0],
            "offset_from_center_px": [-5.0, 1.0], "background_level": 100.0,
            "snr": 4.2, "detector_scope": "Puncta detector: ...",
        })
        messages += _message_pair("c3", "shutter_declared_illumination", {}, {
            "status": "Declared illumination shutter requested.",
            "attempted": ["LaserA.Enable"],
            "shuttered": [["LaserA", "Enable", "0"]],
        })
        messages += _message_pair("c4", "export_dataset_as_tiff",
                                  {"dataset_path": "d", "output_path": "d.tif"},
                                  {"status": "Exported.", "frames": 1})
        # Recorded even though it failed: on the demo camera this tool is
        # expected to fail, and a refusal is emitted before the outcome check,
        # so the refusal limbs must be exercised on exactly this shape.
        messages += _message_pair("c5", "calibrate_stage_to_camera", {},
                                  {"error": "Degenerate: no measurable shift."})
        messages += _message_pair("c6", "snap_to_album", {},
                                  {"status": "Snap added to the Micro-Manager Album."})
        messages += _message_pair("c7", "run_mda", {"preview_token": "abc"},
                                  {"status": "MDA complete.", "frames": 1})
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
    # cwd=out is load-bearing, and this file shipped without it. `python -c`
    # puts the CURRENT DIRECTORY at the head of sys.path, so run from the 63a
    # checkout -- which is where the docstring tells you to run this file --
    # `import microclaw` resolved to the checkout's source rather than to
    # `interpreter`'s install. The main-tree export was therefore produced by
    # 63a's exporter, and the two limbs that exist to prove the gate can fail
    # (G4, G5) came back green on main. Caught by running this selftest, which
    # is the whole argument for having one.
    subprocess.run([str(interpreter), "-c", snippet, str(history), str(out)],
                   check=True, capture_output=True, text=True, cwd=out)
    return out / "routine.py"


def _install(tree: Path, root: Path) -> tuple[Path, Path]:
    """Install `tree` into a venv and return (interpreter, its site-packages).

    `system_site_packages=True` with `--no-deps` is deliberate: installing into
    an isolated venv would leave no numpy and no anthropic, every limb would die
    on an unrelated ModuleNotFoundError, and BOTH trees would "fail" -- which
    reports a discrimination the gate has not demonstrated. The demo machine
    runs install.bat, which installs the whole dependency set.

    The borrowed environment can also contain an editable microclaw, so the
    caller MUST check the gate reported an origin inside `site`.
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

    Compared as resolved paths, never as strings: a venv reports its
    site-packages under /var on macOS while an import resolves the same file
    under /private/var, and a string compare would report a shadowed install
    that had not happened.
    """
    for line in output.splitlines():
        if line.startswith("installed microclaw: "):
            return Path(line.split(": ", 1)[1]).resolve().parent
    return None


def _run_gate(interpreter: Path, workdir: Path, *extra: str) -> tuple[int, str]:
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


CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    CHECKS.append((name, passed, detail))
    print(("ok   " if passed else "FAIL ") + name + (f" - {detail}" if detail else ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-tree", type=Path, required=True,
                        help="A worktree checked out at main, for the discrimination run.")
    args = parser.parse_args()
    tree = HERE.parent.parent

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        good_py, good_site = _install(tree, root / "good")
        main_py, main_site = _install(args.main_tree, root / "main")

        work = root / "work"
        work.mkdir()
        history = _write_history(work / "20260830_microclaw_history.jsonl",
                                 reaches_the_eleven=True)
        thin = _write_history(work / "thin_microclaw_history.jsonl",
                              reaches_the_eleven=False)
        exported = _export_with(good_py, history, work / "export")
        standalone = work / "standalone.txt"
        standalone.write_text("done\nEXIT=0\n", encoding="utf-8")

        artifacts = ("--history", str(history), "--exported", str(exported),
                     "--standalone-log", str(standalone))

        # 1. The good tree passes, and scored the INSTALL, not this checkout.
        code, out = _run_gate(good_py, work, *artifacts)
        check("63a tree: gate passes", code == 0 and "GATE PASSED" in out,
              out.strip().splitlines()[-1] if out.strip() else "no output")
        check("63a tree: scored the install, not the checkout",
              _imported_from(out) == good_site.resolve() / "microclaw",
              str(_imported_from(out)))

        # 2. main fails, and fails on the limbs that are about this block.
        #    Its script is exported by MAIN's exporter, not the good tree's --
        #    scoring the good tree's artifact would measure this file's fixture
        #    instead of the tree under test, and G4/G5 would go green on main.
        exported_main = _export_with(main_py, history, work / "export-main")
        code, out = _run_gate(main_py, work, "--history", str(history),
                              "--exported", str(exported_main),
                              "--standalone-log", str(standalone))
        check("main tree: gate fails", code != 0 and "GATE FAILED" in out)
        check("main tree: G1 red (the sweep)", _status_of(out, "G1") == "FAIL")
        check("main tree: G2 red (the decision table)", _status_of(out, "G2") == "FAIL")
        check("main tree: G4 red (tools that should emit were refused)",
              _status_of(out, "G4") == "FAIL")
        check("main tree: G5 red (default refusal reaches the script)",
              _status_of(out, "G5") == "FAIL")

        # 3. Each artifact limb is proved able to fail on the GOOD tree, so a
        #    pass above is a measurement rather than a limb that cannot fail.
        code, out = _run_gate(good_py, work, "--history", str(thin),
                              "--exported", str(exported),
                              "--standalone-log", str(standalone))
        check("G3 can fail: a session that reached none of the eleven",
              _status_of(out, "G3") == "FAIL" and code != 0)

        doctored = work / "doctored.py"
        doctored.write_text(
            exported.read_text(encoding="utf-8").replace(
                "# NOT EMITTED: run_mda —",
                "# NOT EMITTED: run_mda — no standalone emitter has been "
                "implemented for this tool #"),
            encoding="utf-8")
        code, out = _run_gate(good_py, work, "--history", str(history),
                              "--exported", str(doctored),
                              "--standalone-log", str(standalone))
        check("G5 can fail: the default refusal in a script",
              _status_of(out, "G5") == "FAIL" and code != 0)

        raised = work / "raised.txt"
        raised.write_text(
            "Traceback (most recent call last):\n"
            "  File \"routine.py\", line 3\nRuntimeError: NOT EMITTED\nEXIT=1\n",
            encoding="utf-8")
        code, out = _run_gate(good_py, work, "--history", str(history),
                              "--exported", str(exported),
                              "--standalone-log", str(raised))
        check("G6 can fail: a standalone run that raised",
              _status_of(out, "G6") == "FAIL" and code != 0)

        empty = work / "empty.txt"
        empty.write_text("", encoding="utf-8")
        code, out = _run_gate(good_py, work, "--history", str(history),
                              "--exported", str(exported),
                              "--standalone-log", str(empty))
        check("G6 can fail: an empty capture is never a pass",
              _status_of(out, "G6") == "FAIL" and code != 0)

        # 4. NOT EXERCISED is never a pass.
        code, out = _run_gate(good_py, work)
        exercised = [_status_of(out, limb) for limb in ("G3", "G4", "G5", "G6")]
        check("no artifacts: G3-G6 NOT EXERCISED and the gate still fails",
              all(status == "NOT EXERCISED" for status in exercised) and code != 0,
              str(exercised))

        # 5. The checkout guard refuses, which is what stops this gate from
        #    scoring source the operator is not running.
        # Reproduce the operator mistake the guard exists for: an interpreter
        # whose `import microclaw` lands inside the very checkout the gate file
        # came from -- which is what `uv run design/63-...py` does from inside
        # the repository, tried and recorded on the demo machine in 61b. A bare
        # `sys.executable` is NOT that case: this machine's editable install
        # points at the primary checkout, a different tree, so the guard
        # correctly stays quiet and the check would have measured nothing.
        environment = dict(os.environ, PYTHONPATH=str(tree))
        completed = subprocess.run([sys.executable, str(GATE), "--output",
                                    str(work / "evidence")],
                                   cwd=tree, capture_output=True, text=True,
                                   env=environment)
        check("checkout guard refuses to score the repository",
              completed.returncode != 0 and "Refusing to score the checkout"
              in (completed.stdout + completed.stderr))

    failed = [name for name, passed, _ in CHECKS if not passed]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} selftest checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
