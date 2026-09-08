r"""Selftest for block 78b's demo gate, with two engines that disagree.

CLAUDE.md requires a gate be run against a fake before an operator sees it. The
fake that matters here is not "an engine" but **two** engines:

- `TruncationEngine` batches by AcqEngJ's actual rule, implemented from the
  bytecode reading in design/78: consecutive events sequence when
  `int(min_start_time * 1000)` is equal.
- `ThresholdEngine` batches when `interval_s < 0.001`, which is the plausible
  wrong model this whole block exists to reject.

The gate must PASS against the first and FAIL its 4006/4007 limb against the
second. A gate that cannot tell those two apart would have reported success on
the demo machine while proving nothing, because every short run agrees.

    .venv/bin/python -m pytest -q design/78-block78b-demo-gate-selftest.py
"""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "gate78b", HERE / "78-block78b-demo-gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class _Acq:
    """Stands in for pycromanager.Acquisition.

    Deliberately NOT a subclass of anything: CLAUDE.md's ninth engine contract
    records that `pycromanager.Acquisition` is a dispatching constructor which
    cannot be subclassed, and that an ordinary subclassable stand-in is exactly
    the fake that hides it. This one only has to deliver events the way the
    engine does -- in batches its own rule decides.
    """

    batcher = None

    def __init__(self, **kwargs):
        self._hooks = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def acquire(self, events):
        hook = self._hooks.get("pre_hardware_hook_fn")
        for group in type(self).batcher(events):
            hook(group if len(group) > 1 else group[0])


def _group_by_truncated_ms(events):
    """AcqEngJ's real rule, from the bytecode: equal Long ms deadlines merge."""
    groups, current = [], []
    for event in events:
        ms = event.get("min_start_time")
        key = None if ms is None else int(ms * 1000.0)
        if current:
            prev = current[-1].get("min_start_time")
            prev_key = None if prev is None else int(prev * 1000.0)
            # A missing deadline never blocks sequencing (design/77b).
            if key is None or prev_key is None or key == prev_key:
                current.append(event)
                continue
            groups.append(current)
            current = []
        current.append(event)
    if current:
        groups.append(current)
    return groups


def _group_by_threshold(events):
    """The wrong model: 'anything at or above 1 ms is safe'."""
    if len(events) < 2:
        return [events]
    first, second = events[0].get("min_start_time"), events[1].get("min_start_time")
    if first is None or second is None or (second - first) < 0.001 - 1e-12:
        return [events]
    return [[event] for event in events]


def _events(num_time_points, time_interval_s):
    out = []
    for index in range(num_time_points):
        event = {"axes": {"time": index}}
        if time_interval_s != 0:
            event["min_start_time"] = index * time_interval_s
        out.append(event)
    return out


@pytest.fixture
def engine(monkeypatch):
    import pycromanager
    monkeypatch.setattr(pycromanager, "Acquisition", _Acq)
    monkeypatch.setattr(pycromanager, "multi_d_acquisition_events", _events)
    return _Acq


def _run(tmp_path, long_run=True):
    report = gate.Report()
    gate.run_engine_limbs(report, tmp_path, 1.0, long_run)
    return {l["limb"]: l for l in report.limbs}


def test_the_real_rule_passes_every_engine_limb(engine, tmp_path):
    engine.batcher = _group_by_truncated_ms
    limbs = _run(tmp_path)
    for name, limb in limbs.items():
        assert limb["status"] == "PASS", f"{name}: {limb['detail']}"


def test_a_threshold_engine_fails_the_4006_limb(engine, tmp_path):
    """The discriminator. Every SHORT run agrees; only the long one separates."""
    engine.batcher = _group_by_threshold
    limbs = _run(tmp_path)
    crown = [l for name, l in limbs.items() if "4006" in name]
    assert len(crown) == 1
    assert crown[0]["status"] == "FAIL", (
        "a gate that passes against a threshold engine cannot tell the block's "
        "central claim from its opposite")
    # And it must be the ONLY difference, or the limb is not isolating anything.
    others = [n for n, l in limbs.items() if "4006" not in n and l["status"] != "PASS"]
    assert others == [], f"threshold engine also failed {others}"


def test_the_long_limb_is_not_exercised_when_skipped(engine, tmp_path):
    """Skipping the long run must never read as a pass."""
    engine.batcher = _group_by_truncated_ms
    limbs = _run(tmp_path, long_run=False)
    crown = [l for name, l in limbs.items() if "4006" in name]
    assert crown[0]["status"] == "NOT EXERCISED"


def test_a_broken_engine_reports_not_exercised_not_pass(engine, tmp_path):
    def explode(events):
        raise RuntimeError("engine is down")
    engine.batcher = explode
    limbs = _run(tmp_path)
    control = [l for n, l in limbs.items() if n.startswith("control")]
    assert control[0]["status"] == "NOT EXERCISED"


def test_refusal_limbs_pass_against_the_real_product(tmp_path):
    """These need no engine: they assert the plan-time refusal and its control."""
    report = gate.Report()
    gate.run_refusal_limbs(report, tmp_path, tmp_path / "tmp")
    limbs = {l["limb"]: l for l in report.limbs}
    assert len(limbs) == 2
    for name, limb in limbs.items():
        assert limb["status"] == "PASS", f"{name}: {limb['detail']}"


def test_exit_status_is_nonzero_unless_every_limb_passes(engine, tmp_path):
    engine.batcher = _group_by_threshold
    report = gate.Report()
    gate.run_engine_limbs(report, tmp_path, 1.0, True)
    assert report.finish(tmp_path) != 0
    score = json.loads((tmp_path / "score.json").read_text())
    assert score["passed"] < score["total"]
