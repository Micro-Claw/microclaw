"""Run block 75b's demo gate against a bridge-shaped fake, before it ships.

`CLAUDE.md`: a gate is code, and handing an operator code nobody executed is the
defect this workflow keeps paying for. design/59 block 59a reached the demo
machine and every limb came back
`TypeError: 'mmcorej_StrVector' object is not iterable` -- a `list()` over a
Core collection that worked against every fake in the suite. **A MagicMock would
not have caught it**, because it hands back Python-friendly objects, so the Core
collection here returns `size()`/`get(i)` and raises on `__iter__`.

    .venv/bin/python design/75-block75b-gate-selftest.py

One command covers both trees: **case 6 builds `main` itself** with
`git archive` and asserts the whole gate stands down at limb E there. Earlier
blocks asked an operator to run the selftest twice and compare; there is no
reason to, and a comparison nobody performs is not evidence.

Two things this selftest does on purpose.

* **The fake acquisition lives in a real module file, not an exec'd string.**
  75a's selftest exec'd its fakes and installed `tools.Acquisition` as a
  *factory function*. 75b's gate subclasses `tools.Acquisition` to inject the
  hang, and it reads `inspect.getsource(Acquisition.__exit__)` to check that
  pycro-manager still spells teardown the way the injection assumes. Neither
  works against a function or against a class with no source file, so the fake
  is written to disk and imported.
* **Its `__exit__` fires `image_saved_fn` inside `await_completion`**, which is
  what block 75a *measured* on two rigs across 43 acquisitions -- not what the
  suite's fakes do. A fake that reproduced the suite's order would be testing
  the assumption 75a disproved.

Deliberate failure arms, because a gate whose fake only ever feeds it the happy
path has not been tested, it has been rehearsed:

* case 2 gates expiry on the callback -- the exact mutation D1 forbids -- and
  the mandatory limb C must FAIL;
* case 3 keys the phase off `plan.frames`, and limb C must FAIL on the phase;
* case 4 drops `bound_s` from the result, and limbs B and C must FAIL;
* case 5 makes the timeout record unwritable, and limb G must FAIL;
* case 6 points the gate at a tree with no policy at all.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE = HERE / "75-block75b-demo-gate.py"
TREE = Path(os.environ.get("MICROCLAW_TREE_UNDER_TEST") or HERE.parent)
# The gate itself takes no --safety-config: 60b's rule is that a gate must not
# require configuration the product does not require, and the demo machine has
# its own. A *developer* machine usually does not, and load_safety_config_or_exit
# calls sys.exit() -- which killed every case of this selftest's first run
# before finish() could write a score. So the selftest supplies one; the
# operator never does.
SAFETY_FIXTURE = TREE / "tests" / "fixtures" / "50a-demo-safety_config.yaml"

FAKE_MODULE = '''
"""Bridge-shaped fake microscope for block 75b's gate selftest.

Written to a real file so `inspect.getsource` works on `__exit__`, which the
gate reads before it injects a hang.
"""
import tempfile
from pathlib import Path


class StrVector:
    """Bridge-shaped: deliberately NOT Python-iterable, like mmcorej_StrVector."""

    def __init__(self, values):
        self._values = list(values)

    def size(self):
        return len(self._values)

    def get(self, index):
        return self._values[index]

    def __iter__(self):
        raise TypeError("mmcorej_StrVector is not iterable")


class FakeCore:
    def __init__(self):
        self.exposure = 50.0

    def get_camera_device(self):
        return "FakeCam"

    def get_loaded_devices(self):
        return StrVector(["FakeCam", "FakeStage"])

    def get_image_width(self):
        return 512

    def get_image_height(self):
        return 512

    def get_bytes_per_pixel(self):
        return 2

    def get_exposure(self):
        return self.exposure

    def set_exposure(self, value):
        self.exposure = float(value)

    def is_sequence_running(self):
        return False

    def refresh_gui(self):
        return None


class FakeCtrl:
    def __init__(self, **_kwargs):
        self.core = FakeCore()

    def is_connected(self):
        return True


_ROOT = Path(tempfile.mkdtemp(prefix="block75b-selftest-"))
_COUNT = {"n": 0}


class FakeAcquisition:
    """A fake that fires image_saved_fn where the real engine fires it.

    Block 75a measured, in 43 of 43 acquisitions on two rigs and two cameras,
    that pycro-manager accounts the frame *inside* `acq.__exit__` -- so this
    fake fires it from `await_completion`, and `__exit__` is spelled exactly as
    pycro-manager 1.0.2 spells it: mark_finished, then await_completion. The
    gate's injection replaces this method, so the two must agree.
    """

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._exception = None
        self._events = []
        _COUNT["n"] += 1
        location = _ROOT / f"{kwargs.get('name', 'run')}_{_COUNT['n']}"
        location.mkdir(parents=True, exist_ok=True)
        # design/60's precedent: the index is what says what Java wrote.
        (location / "NDTiff.index").write_bytes(b"\\x00" * 64)
        self._dataset_disk_location = str(location)

    def acquire(self, events):
        self._events = list(events)

    def __enter__(self):
        return self

    def mark_finished(self):
        self._finished = True

    def await_completion(self):
        saved = self.kwargs.get("image_saved_fn")
        if saved is None:
            return
        for _ in self._events:
            saved({}, object())

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.mark_finished()
        self.await_completion()
'''

# Each mutation is a one-property change to the product, applied to a copy of
# the tree. Never to the tree under test.
MUTATIONS = {
    "callback_gated": (
        "            runtime_expired = now >= runtime_deadline and quiet\n",
        "            runtime_expired = (now >= runtime_deadline and quiet\n"
        "                               and frames_accounted > 0)\n",
    ),
    "phase_never_finalizes": (
        '            phase = ("finalizing" if policy.terminal_frames is not None\n'
        '                     and count >= policy.terminal_frames else "acquiring")\n',
        '            phase = "acquiring"\n',
    ),
    "no_bound_s": (
        '        "expired_bound": exc.expired_bound, "bound_s": exc.bound_s,\n',
        '        "expired_bound": exc.expired_bound,\n',
    ),
    "record_loses_phase": (
        '                    "type": "acquisition_timeout",\n'
        '                    "phase": failure.phase,\n',
        '                    "type": "acquisition_timeout",\n',
    ),
}


def make_tree(source: Path, mutation: str | None, workspace: Path) -> Path:
    target = workspace / ("tree" if mutation is None else f"tree-{mutation}")
    shutil.copytree(source / "microclaw", target / "microclaw")
    if mutation is not None:
        old, new = MUTATIONS[mutation]
        path = target / "microclaw" / "tools.py"
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise SystemExit(
                f"selftest mutation {mutation!r} no longer matches the product. "
                f"That is a finding: fix the mutation, never the assertion.")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return target


def run_gate(tree: Path, out: Path, fake_dir: Path, prelude: Path):
    env = dict(os.environ)
    env["MICROCLAW_TREE_UNDER_TEST"] = str(tree)
    env["BLOCK75B_PRELUDE"] = str(prelude)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(fake_dir), env.get("PYTHONPATH", "")]).strip(os.pathsep)
    proc = subprocess.run(
        [sys.executable, str(GATE), "--out", str(out),
         "--safety-config", str(SAFETY_FIXTURE),
         "--healthy-runs", "2", "--hold-s", "14"],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=600,
    )
    score = out / "score.json"
    payload = json.loads(score.read_text(encoding="utf-8")) if score.exists() else {}
    statuses = {row["name"].split(" -")[0]: row["status"]
                for row in payload.get("results", [])}
    details = {row["name"].split(" -")[0]: row.get("detail", "")
               for row in payload.get("results", [])}
    return proc, statuses, details, payload


def check(name, statuses, details, proc, expect_pass, expect_fail=(),
          expect_stood_down=False):
    """`expect_fail` maps a limb to a substring its detail must contain.

    Status alone is not enough. 75a's gate had limb B failing for limb A's
    cause, and a selftest that only counted FAILs would have called that
    correct. A mutation must fail the limb it names, *for the reason it names*.
    """
    problems = []
    if expect_stood_down:
        if statuses.get("E") != "FAIL":
            problems.append(f"limb E is {statuses.get('E')!r}, expected FAIL")
        others = {k: v for k, v in statuses.items() if k != "E"}
        if set(others.values()) - {"NOT EXERCISED"}:
            problems.append(f"limbs did not stand down: {others}")
    else:
        for limb in expect_pass:
            if statuses.get(limb) != "PASS":
                problems.append(f"limb {limb} is {statuses.get(limb)!r}, expected PASS")
        for limb, because in expect_fail.items():
            if statuses.get(limb) != "FAIL":
                problems.append(f"limb {limb} is {statuses.get(limb)!r}, expected FAIL")
            elif because not in details.get(limb, ""):
                problems.append(
                    f"limb {limb} failed for the wrong reason: expected "
                    f"{because!r} in {details.get(limb, '')!r}")
    verdict = "OK" if not problems else "BROKEN"
    print(f"[{verdict}] {name}: {statuses}")
    for problem in problems:
        print(f"        {problem}")
    if problems and proc.returncode == 0 and expect_fail:
        print("        (and the gate exited 0 while a limb failed)")
    if problems:
        print(textwrap.indent(proc.stdout[-3000:], "        | "))
    return not problems


def main():
    workspace = Path(tempfile.mkdtemp(prefix="block75b-selftest-"))
    fake_dir = workspace / "fakes"
    fake_dir.mkdir()
    (fake_dir / "block75b_fakes.py").write_text(FAKE_MODULE, encoding="utf-8")
    prelude = workspace / "prelude.py"
    prelude.write_text(textwrap.dedent('''
        # Executed by the gate through BLOCK75B_PRELUDE, after it has imported
        # the product and captured tools.Acquisition. Everything below the
        # microscope -- execute_tool, _acquire_with_hooks, the writer, AuditLog
        # -- stays the product.
        import block75b_fakes
        import microclaw.controller as _controller
        from microclaw import tools as _tools

        _tools.Acquisition = block75b_fakes.FakeAcquisition
        _controller.MicroscopeController = block75b_fakes.FakeCtrl
        MicroscopeController = block75b_fakes.FakeCtrl
    '''), encoding="utf-8")

    healthy = TREE
    print(f"Tree under test: {healthy}")
    ok = True

    # Each expected failure names the substring its detail must contain, so a
    # limb that fails for a neighbouring limb's cause is BROKEN, not OK.
    cases = [
        ("1 - the branch as shipped", None,
         ("E", "0", "A", "B", "C", "D", "F", "G"), {}, False),
        ("2 - expiry gated on the callback (D1's forbidden mutation)",
         "callback_gated", ("E", "0", "A", "B"),
         {"C": "no acquisition_timeout record for blocked-lost"}, False),
        ("3 - the phase never reaches finalizing", "phase_never_finalizes",
         ("E", "0", "A", "C"),
         {"B": "never reached finalizing"}, False),
        ("4 - the result stops reporting its bound", "no_bound_s",
         ("E", "0", "A"),
         {"B": "named no bound_s", "C": "named no bound_s"}, False),
        ("5 - the timeout record loses its phase", "record_loses_phase",
         ("E", "0", "A", "B", "C", "D", "F"),
         {"G": "missing 'phase'"}, False),
    ]

    for name, mutation, expect_pass, expect_fail, stood_down in cases:
        tree = make_tree(healthy, mutation, workspace)
        out = workspace / f"out-{mutation or 'shipped'}"
        proc, statuses, details, _payload = run_gate(tree, out, fake_dir, prelude)
        ok &= check(name, statuses, details, proc, expect_pass, expect_fail,
                    stood_down)

    # Case 6 is the control arm and needs a tree genuinely without 75b. Taken
    # from `main` with `git archive` rather than by editing this tree's source:
    # the first draft renamed the policy class and left DEFAULT referencing it,
    # so the gate died on an ImportError instead of standing down -- which is a
    # broken control arm, not a control arm.
    control = workspace / "tree-control"
    control.mkdir()
    archive = subprocess.run(
        ["git", "-C", str(healthy), "archive", "main", "microclaw"],
        capture_output=True, timeout=120,
    )
    if archive.returncode != 0:
        print("[BROKEN] 6 - control arm: could not `git archive main microclaw`: "
              + archive.stderr.decode("utf-8", "replace")[-400:])
        ok = False
    else:
        (workspace / "main.tar").write_bytes(archive.stdout)
        subprocess.run(["tar", "-xf", str(workspace / "main.tar"), "-C", str(control)],
                       check=True, timeout=120)
        out = workspace / "out-control"
        proc, statuses, details, _payload = run_gate(control, out, fake_dir, prelude)
        ok &= check("6 - `main`, which has no supervision policy (the control arm)",
                    statuses, details, proc, (), {}, True)

    print()
    if ok:
        print("All selftest cases behaved as expected.")
    else:
        print("SELFTEST BROKEN - fix the gate before it ships.")
    print(f"Artifacts: {workspace}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
