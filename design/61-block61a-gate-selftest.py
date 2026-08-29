"""Off-rig selftest for block 61a's demo gate: run it on BOTH trees.

CLAUDE.md's rule is that a gate is code, and handing an operator code nobody
executed is the defect this workflow keeps paying for. So this runs the gate
program against two installed-shaped trees and checks that its verdict
*discriminates*: PASS on the 61a tree, FAIL on `main`, where `load_skill` and
the skills package do not exist. A selftest that only proves the gate passes on
the good tree proves nothing about whether it can fail.

Two things it deliberately does NOT fake.

The gate touches no Micro-Manager bridge -- `load_skill` reads no hardware -- so
there is no Core collection to shape here. What would have been the bridge fake
is replaced by the check that matters for THIS gate: that its `_import_installed`
guard actually refuses a checkout. 59a lost a rig trip to `list()` over a Core
collection because every fake was Python-friendly; the equivalent mistake here
is a gate that silently scores the repository instead of the install, so that is
what is exercised.

And the third tree is built by INSTALLING, not by copying: `pip install .` into
a throwaway venv is the operation whose package-data pattern the gate exists to
check, so faking it with a file copy would encode the assumption under test.
That is the 60b lesson -- the gate's own fake is the one nobody reviews.

    python3 design/61-block61a-gate-selftest.py --main-tree /path/to/main-worktree

Run from the 61a tree. Do not commit its output.
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
GATE = HERE.parent / "61-block61a-demo-gate.py"


def _install(tree: Path, root: Path) -> tuple[Path, Path]:
    """Install `tree` into a venv and return (interpreter, that venv's site-packages).

    `system_site_packages=True` with `--no-deps` is deliberate, and the first
    version of this file got it wrong.  Installing with `--no-deps` into an
    ISOLATED venv left no numpy and no anthropic, so every limb died on an
    unrelated ModuleNotFoundError and BOTH trees "failed" -- which would have
    reported discrimination the gate had not actually demonstrated.  The demo
    machine runs `install.bat`, which installs `.[serve]` with its whole
    dependency set, so an environment without them is not shaped like the thing
    under test.  Borrowing the ambient dependencies and installing only
    microclaw itself reproduces that shape without a network round trip per
    tree.

    The borrowed environment can also contain an editable microclaw, so the
    caller MUST check that the gate reported an origin inside `site` below;
    otherwise this would score the checkout, which is the failure the whole
    file exists to prevent.
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


def _run_gate(interpreter: Path, workdir: Path) -> tuple[int, str]:
    """Run the gate with the installed interpreter, from outside any checkout."""
    completed = subprocess.run(
        [str(interpreter), str(GATE), "--output", str(workdir / "evidence")],
        cwd=workdir, capture_output=True, text=True,
    )
    return completed.returncode, completed.stdout + completed.stderr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-tree", type=Path, required=True,
                        help="A checkout of main, where load_skill does not exist.")
    parser.add_argument("--this-tree", type=Path, default=HERE.parents[1])
    args = parser.parse_args()

    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # 1. The 61a tree must PASS.
        good = root / "good"
        good.mkdir()
        interpreter, site = _install(args.this_tree, good)
        code, output = _run_gate(interpreter, good)
        print(f"--- 61a tree: exit {code}\n{output}")
        if code != 0:
            failures.append("the 61a tree did not pass its own gate")
        if "BLOCK 61a DEMO GATE PASSED" not in output:
            failures.append("no PASSED banner on the 61a tree")
        imported = _imported_from(output)
        if imported != (site / "microclaw").resolve():
            failures.append(
                f"the gate imported microclaw from {imported}, not from "
                f"{site / 'microclaw'}; the ambient environment shadowed the "
                f"install and this scored the wrong tree")

        # 2. main must FAIL. If it does not, the gate cannot detect the absence
        #    of the thing the block adds, and every limb above is decoration.
        bad = root / "bad"
        bad.mkdir()
        interpreter, site = _install(args.main_tree, bad)
        code, output = _run_gate(interpreter, bad)
        print(f"--- main tree: exit {code}\n{output}")
        if code == 0:
            failures.append("main passed the gate: the gate does not discriminate")
        if _imported_from(output) != (site / "microclaw").resolve():
            failures.append("the main-tree run did not import the tree it installed")
        # And it must fail for the RIGHT reason. A dependency error would fail
        # too, and would report discrimination this gate had not demonstrated.
        if "No module named 'microclaw.skills'" not in output:
            failures.append(
                "main failed for a reason other than the missing skills package; "
                "read the output above before believing the discrimination")

        # 3. The checkout guard must refuse when run from inside the repository
        #    with the repository importable, which is how an operator would run
        #    it by accident.
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
