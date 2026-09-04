"""Run block 75a's demo gate against a bridge-shaped fake, before it ships.

`CLAUDE.md`: a gate is code, and handing an operator code nobody executed is the
defect this workflow keeps paying for. design/59 block 59a reached the demo
machine and every limb came back
`TypeError: 'mmcorej_StrVector' object is not iterable` — a `list()` over a Core
collection that worked against every fake in the suite. **A MagicMock would not
have caught it**, because it hands back Python-friendly objects, so the Core
collections here return `size()`/`get(i)` and raise on `__iter__`.

    .venv/bin/python design/75-block75a-gate-selftest.py

Run it on **both trees**. On this branch every case must hold; on `main` cases
1-5 and 7 must fail at limb E and case 6 is the only one that passes — that is
what proves the gate discriminates rather than passing whatever it is pointed
at.

Seven cases, and **four of them are deliberately failures** — a gate whose fake
only ever feeds it the happy path has not been tested, it has been rehearsed.
design/60 block 60b's gate failed the single limb its rig trip existed for
because its fake wrote the filename the glob expected.

Three things this selftest does on purpose:

* **The records are written by the real `AuditLog` through the real
  `AcquisitionDiagnosticWriter`**, never hand-assembled. The file's format and
  name come from the product, so a change there breaks this rather than hiding
  from it. Only the microscope is fake.
* **Limbs C and D run real child processes**, spawned and ended by the gate's
  own code, because what they assert is what survives a process ending. The
  gate's poll-then-end sequencing — the part nobody reviews — executes for real.
* **Case 7 executes the M2 arm**, which is a program an operator runs on a
  booked rig. Its first draft called `snap_and_analyze` with an invented
  `exposure_ms` that the tool does not take; that would have come back as an
  error from every snap on M2 and read as a product failure.

What it found before shipping, all in the gate rather than the product: limb B
failing for limb A's cause, limb D discarding the child's stdout so a failure
had no cause, a `finally` clobbering that stdout with a second `communicate()`,
and a 4,000-frame long call that crossed the rig's confirmation threshold and
returned instead of running.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAFETY_FIXTURE = ROOT / "tests" / "fixtures" / "50a-demo-safety_config.yaml"

_spec = importlib.util.spec_from_file_location(
    "gate75a", Path(__file__).with_name("75-block75a-demo-gate.py"))
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


FAKE_CORE_SOURCE = '''
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
'''

FAKE_ACQ_SOURCE = '''
class FakeAcquisition:
    """Fires image_saved_fn per event, then returns from __exit__.

    `saved_after_exit` is the mutation arm for the one question this gate exists
    to answer on hardware: whether real pycro-manager can deliver a saved-frame
    callback after teardown has already been marked finished. Our whole suite
    assumes it cannot.
    """

    saved_after_exit = False
    per_frame_s = 0.0

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._dataset_disk_location = None
        self._exception = None
        self._pending = 0

    def acquire(self, events):
        listed = list(events)
        if type(self).saved_after_exit:
            self._pending = len(listed)
            return
        for _ in listed:
            if type(self).per_frame_s:
                time.sleep(type(self).per_frame_s)
            self.kwargs["image_saved_fn"]({}, object())

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        for _ in range(self._pending):
            self.kwargs["image_saved_fn"]({}, object())
        self._pending = 0
        return None
'''


def install_fakes(*, saved_after_exit=False, per_frame_s=0.0, dataset_root=None):
    """Patch the microscope out from under the real product, in this process."""
    import time as _time
    namespace = {"time": _time}
    exec(FAKE_CORE_SOURCE, namespace)
    exec(FAKE_ACQ_SOURCE, namespace)
    from microclaw import tools

    acq_class = namespace["FakeAcquisition"]
    acq_class.saved_after_exit = saved_after_exit
    acq_class.per_frame_s = per_frame_s
    counter = {"n": 0}
    root = Path(dataset_root or tempfile.mkdtemp())

    def factory(**kwargs):
        instance = acq_class(**kwargs)
        counter["n"] += 1
        location = root / f"{kwargs.get('name', 'run')}_{counter['n']}"
        location.mkdir(parents=True, exist_ok=True)
        instance._dataset_disk_location = str(location)
        return instance

    tools.Acquisition = factory

    class FakeCtrl:
        def __init__(self, **_kwargs):
            self.core = namespace["FakeCore"]()

        def is_connected(self):
            return True

    import microclaw.controller as controller_module
    controller_module.MicroscopeController = FakeCtrl
    return FakeCtrl


CHILD_PRELUDE = '''
# Executed by the gate's child driver through BLOCK75A_CHILD_PRELUDE. Installs
# the same fakes in the child, so limbs C and D exercise the gate's real spawn,
# poll and end against a fake microscope. Everything below the microscope --
# execute_tool, _acquire_with_hooks, AcquisitionDiagnosticWriter, AuditLog --
# is the product.
import time, tempfile
from pathlib import Path
import microclaw.controller as _controller
from microclaw import tools as _tools

{core}
{acq}

_acq = FakeAcquisition
_acq.saved_after_exit = False
_acq.per_frame_s = {per_frame_s!r}
_root = Path(tempfile.mkdtemp())
_count = {{"n": 0}}


def _factory(**kwargs):
    inst = _acq(**kwargs)
    _count["n"] += 1
    loc = _root / f"{{kwargs.get('name', 'run')}}_{{_count['n']}}"
    loc.mkdir(parents=True, exist_ok=True)
    inst._dataset_disk_location = str(loc)
    return inst


_tools.Acquisition = _factory


class _FakeCtrl:
    def __init__(self, **_kwargs):
        self.core = FakeCore()

    def is_connected(self):
        return True


_controller.MicroscopeController = _FakeCtrl
# The driver did `from microclaw.controller import MicroscopeController` before
# it reached this prelude, so it holds the real class under its own global name
# and patching the module attribute alone changes nothing -- the first run of
# this selftest failed exactly there, with "Couldn't create Core". The prelude
# execs into the driver's globals, so rebind the name the driver will call.
MicroscopeController = _FakeCtrl
{extra}
'''

TORN_LINE_EXTRA = '''
# Mutation arm: a torn last line, which is what an interrupted write would
# leave and what cost block 52b a whole session's export.
_orig_append = AuditLog.append


def _tearing_append(self, message):
    record = _orig_append(self, message)
    if message.get("type") == "acquisition_event_submission":
        with self.path.open("a", encoding="utf-8") as _s:
            _s.write('{"type": "acquisition_progress", "frames_acc')
    return record


AuditLog.append = _tearing_append
'''


def child_prelude(path: Path, *, per_frame_s, extra=""):
    text = CHILD_PRELUDE.format(core=FAKE_CORE_SOURCE, acq=FAKE_ACQ_SOURCE,
                                per_frame_s=per_frame_s, extra=extra)
    path.write_text(text, encoding="utf-8")
    return path


def seed_session_pair(directory: Path, *, orphan=False):
    """Write a history/acquisitions pair the way a real session would.

    The acquisition records go through the real `AuditLog`; the transcript is a
    plain JSONL of the shape `ConversationStore` appends. Limb F's claim is the
    *join*, not the filename -- both entry points' naming has its own test.
    """
    from microclaw.conversation import AuditLog
    stamp = time.strftime("%Y%m%d_%H%M%S")
    history = directory / f"{stamp}_123456_microclaw_history.jsonl"
    with history.open("w", encoding="utf-8") as stream:
        for call_id in ("toolu_seed_1", "toolu_seed_2"):
            stream.write(json.dumps({"role": "assistant", "content": [
                {"type": "tool_use", "id": call_id, "name": "run_timelapse",
                 "input": {"n_frames": 1}}]}) + "\n")
    acquisitions = AuditLog(
        Path(str(history).replace("_history.jsonl", "_acquisitions.jsonl")))
    recorded = ["toolu_seed_1", "toolu_orphan"] if orphan else \
        ["toolu_seed_1", "toolu_seed_2"]
    for call_id in recorded:
        acquisitions.append({"type": "acquisition_construction",
                             "tool_call_id": call_id, "session_id": history.stem,
                             "timestamp": "2026-09-04T18:00:00+00:00"})
    return history


def _invoke(out, *, latency_runs, kill_frames):
    argv = sys.argv
    # The selftest names a fixture because this machine has no installed safety
    # document; the demo machine omits the argument entirely, which is the whole
    # point of the gate not requiring one (60b).
    sys.argv = ["gate", "--out", str(out / "evidence"),
                "--safety-config", str(SAFETY_FIXTURE),
                "--latency-runs", str(latency_runs),
                "--kill-frames", str(kill_frames),
                "--exposure-ms", "50"]
    try:
        return gate.main()
    finally:
        sys.argv = argv


def run_case(name, *, expect, out, saved_after_exit=False, per_frame_s=0.0,
             kill_frames=400, latency_runs=3, extra="", seed=None, no_d4=False):
    """Run the gate once and check each limb's status against `expect`."""
    out.mkdir(parents=True, exist_ok=True)
    gate.RESULTS.clear()
    install_fakes(saved_after_exit=saved_after_exit, per_frame_s=per_frame_s,
                  dataset_root=out / "datasets")

    prelude = child_prelude(out / "prelude.py", per_frame_s=per_frame_s,
                            extra=extra)
    os.environ["BLOCK75A_CHILD_PRELUDE"] = str(prelude)

    from microclaw import tools
    saved_execute = tools.execute_tool
    if no_d4:
        # What `main` looks like: no correlation arguments at all. Wrapping
        # rather than editing a tree keeps this honest about *why* the gate
        # stands down -- it reads the signature, so the signature is what has
        # to change.
        def legacy_execute_tool(name_, tool_input, ctrl, guard, registry=None,
                                *, setup_mode=False, cancel=None, records=None,
                                acquisition_event_sink=None):
            raise AssertionError("the gate must not reach execute_tool on main")
        tools.execute_tool = legacy_execute_tool

    cwd = Path.cwd()
    workdir = out / "cwd"
    workdir.mkdir(parents=True, exist_ok=True)
    if seed is not None:
        seed_session_pair(workdir, orphan=(seed == "orphan"))
    os.chdir(workdir)
    real_stdout = sys.stdout
    try:
        code = _invoke(out, latency_runs=latency_runs, kill_frames=kill_frames)
    finally:
        os.chdir(cwd)
        sys.stdout = sys.stderr = real_stdout
        tools.execute_tool = saved_execute
        os.environ.pop("BLOCK75A_CHILD_PRELUDE", None)

    actual = {row["name"].split(" - ")[0]: row["status"] for row in gate.RESULTS}
    detail = {row["name"].split(" - ")[0]: row["detail"] for row in gate.RESULTS}
    problems = []
    for limb, wanted in expect.items():
        if actual.get(limb) != wanted:
            problems.append(f"{limb}: expected {wanted}, got {actual.get(limb)}")
    if problems:
        print(f"  SELFTEST FAIL [{name}]")
        for problem in problems:
            print(f"    {problem}")
        for limb in sorted(detail):
            print(f"    {limb}: {detail[limb][:200]}")
        return False
    print(f"  ok [{name}] exit={code} " +
          " ".join(f"{k}={v}" for k, v in sorted(actual.items())))
    return True


def run_m2_arm(out: Path):
    """Execute design/75-block75a-m2-latency.py against the same fakes.

    It is a program an operator runs on a booked rig, so it must not arrive
    there unexecuted -- and its first draft called `snap_and_analyze` with an
    invented `exposure_ms`, which the tool does not take. That would have come
    back as an error result from every snap and read as a product failure.
    """
    import numpy as np

    out.mkdir(parents=True, exist_ok=True)
    install_fakes(per_frame_s=0.0, dataset_root=out / "datasets")
    from microclaw import tools
    import microclaw.controller as controller_module

    # The snap path reaches MMStudio through `_pause_live` and
    # `snap_to_numpy_displayed`, so the fake needs a studio as well as a frame
    # source -- `live()` with `is_live_mode_on`, `set_live_mode_on`, `snap`,
    # `display_image` and `get_display`. Read off those two functions rather
    # than guessed; the first version of this case fabricated only the frame and
    # came back with "'FakeCtrl' object has no attribute 'studio'".
    saved_displayed = getattr(tools, "snap_to_numpy_displayed", None)
    saved_plain = getattr(tools, "snap_to_numpy", None)
    frame = (np.random.default_rng(0).random((64, 64)) * 500 + 100).astype("uint16")
    tools.snap_to_numpy_displayed = lambda _ctrl: frame
    tools.snap_to_numpy = lambda _ctrl: frame

    class FakeLive:
        def __init__(self):
            self.on = False

        def is_live_mode_on(self):
            return self.on

        def set_live_mode_on(self, value):
            self.on = bool(value)

        def snap(self, _display):
            return None

        def display_image(self, _image):
            return None

        def get_display(self):
            return None

    class FakeStudio:
        def __init__(self):
            self._live = FakeLive()

        def live(self):
            return self._live

    base_ctrl = controller_module.MicroscopeController

    class SnapCapableCtrl(base_ctrl):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.studio = FakeStudio()

    controller_module.MicroscopeController = SnapCapableCtrl

    spec = importlib.util.spec_from_file_location(
        "m2arm", Path(__file__).with_name("75-block75a-m2-latency.py"))
    arm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(arm)

    def invoke(evidence, extra):
        argv, real_stdout = sys.argv, sys.stdout
        sys.argv = ["m2", "--save-dir", str(out / "data"),
                    "--out", str(out / evidence),
                    "--safety-config", str(SAFETY_FIXTURE), "--runs", "2",
                    "--laser-slot", "-1", *extra]
        try:
            code = arm.main()
        finally:
            sys.argv, sys.stdout = argv, real_stdout
            sys.stderr = real_stdout
        return code, json.loads(
            (out / evidence / "m2-latency.json").read_text())

    try:
        # Two runs, because they answer different questions and only one of
        # them can be answered off-rig.
        #
        # `--no-snap` must be clean: that is this script's own plumbing -- the
        # acquisitions, the records, the two independent latency measurements --
        # and a fake camera is enough for all of it.
        code, payload = invoke("evidence", ["--no-snap"])
        # With the snap, a fake will always run out of camera surface eventually
        # (`get_position`, here). What must NOT happen is the failure this case
        # was written after: calling a tool with an argument it does not take.
        # So the criterion is the *shape* of the error, not its absence.
        _, with_snap = invoke("evidence-snap", [])
    finally:
        if saved_displayed is not None:
            tools.snap_to_numpy_displayed = saved_displayed
        if saved_plain is not None:
            tools.snap_to_numpy = saved_plain
        controller_module.MicroscopeController = base_ctrl

    problems = []
    if code != 0:
        problems.append(f"--no-snap exit {code}; failures={payload.get('failures')} "
                        f"torn={payload.get('torn_lines')} "
                        f"incomplete={payload.get('incomplete_records')}")
    if payload.get("file_end_to_end_s") is None:
        problems.append("no end-to-end summary was produced")
    elif payload["file_end_to_end_s"]["n"] != 2:
        problems.append(f"summary covers {payload['file_end_to_end_s']['n']}/2 runs")
    if payload.get("file_finalization_s") is None:
        problems.append("no finalization summary was produced")
    bad_calls = [f for f in with_snap.get("failures", [])
                 if "unexpected keyword" in str(f) or "TypeError" in str(f)]
    if bad_calls:
        problems.append(f"a tool was called with arguments it does not accept: "
                        f"{bad_calls}")
    if with_snap.get("file_end_to_end_s") is None:
        problems.append("the snap arm produced no latency summary at all, so the "
                        "snap failure stopped the acquisitions too")
    if problems:
        print("  SELFTEST FAIL [m2-arm]")
        for problem in problems:
            print(f"    {problem}")
        return False
    e2e = payload["file_end_to_end_s"]
    print(f"  ok [m2-arm] exit=0 n={e2e['n']} p50={e2e['p50']:.4f}s "
          f"max={e2e['max']:.4f}s (fakes, so the numbers mean nothing)")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true",
                        help="Keep the temporary trees for inspection.")
    args = parser.parse_args()
    base = Path(tempfile.mkdtemp(prefix="block75a-selftest-"))
    ok = True

    print("1. happy path - every limb PASS")
    ok &= run_case(
        "happy", out=base / "happy", per_frame_s=0.05, kill_frames=400,
        seed="joined",
        expect={"0": "PASS", "A": "PASS", "B": "PASS", "C": "PASS",
                "D": "PASS", "E": "PASS", "F": "PASS"})

    print("2. a saved-frame callback arriving AFTER teardown - limb A must FAIL")
    print("   (the one question this gate exists to answer on hardware)")
    ok &= run_case(
        "saved-after-exit", out=base / "after-exit", saved_after_exit=True,
        per_frame_s=0.05, kill_frames=400, seed="joined",
        expect={"A": "FAIL", "B": "PASS", "E": "PASS"})

    print("3. a torn line from an interrupted write - limb D must FAIL")
    ok &= run_case(
        "torn-line", out=base / "torn", per_frame_s=0.05, kill_frames=400,
        extra=TORN_LINE_EXTRA, seed="joined",
        expect={"D": "FAIL", "C": "FAIL", "E": "PASS"})

    print("4. the long call finishing before the end - limb D must NOT be a pass")
    ok &= run_case(
        "no-kill", out=base / "nokill", per_frame_s=0.0, kill_frames=1,
        seed="joined",
        expect={"D": "NOT EXERCISED", "E": "PASS"})

    print("5. an acquisition record naming no tool_use - limb F must FAIL")
    ok &= run_case(
        "orphan-join", out=base / "orphan", per_frame_s=0.05, kill_frames=400,
        seed="orphan",
        expect={"F": "FAIL", "E": "PASS"})

    print("6. a tree without D4 - limb E FAILs and every limb stands down")
    print("   (this is what the control arm on `main` must look like)")
    ok &= run_case(
        "no-d4", out=base / "nod4", no_d4=True,
        expect={"E": "FAIL", "0": "NOT EXERCISED", "A": "NOT EXERCISED",
                "B": "NOT EXERCISED", "C": "NOT EXERCISED",
                "D": "NOT EXERCISED", "F": "NOT EXERCISED"})

    print("7. the M2 latency arm runs at all - against the same fakes")
    ok &= run_m2_arm(base / "m2")

    if not args.keep:
        shutil.rmtree(base, ignore_errors=True)
    else:
        print(f"\nkept: {base}")
    print("\nSELFTEST PASSED" if ok else "\nSELFTEST FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
