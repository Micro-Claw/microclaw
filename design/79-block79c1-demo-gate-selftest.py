r"""Selftest for block 79c-1's demo gate. Run it before the gate reaches a machine.

A gate is code, and handing an operator code nobody executed is the defect
CLAUDE.md's workflow keeps paying for. Every limb here is exercised twice: once
against a payload shaped like the product's, and once against a mutant that
breaks exactly that limb. A limb that passes both is not a criterion.

The stub returns Core device names as plain strings, which is what
`get_camera_device()` returns over the bridge -- this gate never iterates a Core
collection, so there is no `mmcorej_StrVector` to shape (design/59a's trap does
not apply here, and saying so is cheaper than a fake nobody needs).

    uv run python -m pytest -q design\79-block79c1-demo-gate-selftest.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).with_name("79-block79c1-demo-gate.py")


def load_gate():
    spec = importlib.util.spec_from_file_location("block79c1_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["block79c1_gate"] = module
    spec.loader.exec_module(module)
    return module


gate = load_gate()


def breakdown(*, acquisitions, duration_s=1.0, accounted_s=0.6,
              child_breakdowns=False, dominant=False, drift=0.0,
              extra_bytes=0, disclose=True):
    phases = {
        "acquisition": {"count": acquisitions, "min_s": 0.01, "max_s": 0.2,
                        "total_s": accounted_s, "mean_s": accounted_s / acquisitions},
        "restoration": {"count": acquisitions, "min_s": 1e-6, "max_s": 2e-6,
                        "total_s": 1e-5, "mean_s": 1e-6},
    }
    meaning = ("completed spans including failed actions; acquisition and "
               "restoration exclude nested write spans; unaccounted_s includes "
               "between-field work (XY moves, settling, preflight, mkdir)")
    if disclose:
        meaning += ("; per-field breakdowns are folded into this composite and "
                    "omitted from child results to keep timing detail bounded")
    b = {
        "clock": "time.monotonic", "duration_s": duration_s,
        "record_count": 0, "phases": phases,
        "accounted_s": accounted_s,
        "unaccounted_s": duration_s - accounted_s + drift,
        "phase_meaning": meaning,
        "slowest_records": [], "slowest_meaning": "up to three write records",
    }
    if dominant:
        b["dominant_phase"] = "acquisition"
    if extra_bytes:
        b["padding"] = "x" * extra_bytes
    return b


def payload(*, n, acquisitions, **kw):
    b = breakdown(acquisitions=acquisitions, **kw)
    child = {"position": "P0", "dataset_path": "/tmp/x"}
    if kw.get("child_breakdowns"):
        child = {**child, "duration_breakdown": breakdown(acquisitions=1)}
    return {"status": f"{n}/{n} positions completed.",
            "results": [child] * n,
            "duration_s": b["duration_s"], "duration_breakdown": b}


def install(monkeypatch, tmp_path, *, respond, bridge=True):
    """Wire the gate's rig() and the tool it drives, without a real bridge."""
    from microclaw import tools

    class Core:
        def get_camera_device(self):
            return "DCam"

        def get_xy_stage_device(self):
            return "XY"

    class Ctrl:
        core = Core()

    class Guard:
        def resolve_in_workspace(self, path):
            return str(path)

    class Config:
        constraints = object()

    import microclaw.config as config_module
    import microclaw.controller as controller_module
    import microclaw.safety as safety_module

    if bridge:
        monkeypatch.setattr(controller_module, "MicroscopeController",
                            lambda port=None: Ctrl(), raising=False)
        monkeypatch.setattr(config_module, "load_safety_config_or_exit",
                            lambda _: Config(), raising=False)
        monkeypatch.setattr(safety_module, "SafetyGuard",
                            lambda _c: Guard(), raising=False)
    else:
        def refuse(_):
            raise SystemExit("no safety config on this machine")
        monkeypatch.setattr(config_module, "load_safety_config_or_exit",
                            refuse, raising=False)

    monkeypatch.setattr(tools, "run_multiposition_acquisition", respond,
                        raising=False)


def score(monkeypatch, tmp_path, respond, bridge=True, fields=8, bound=24):
    install(monkeypatch, tmp_path, respond=respond, bridge=bridge)
    out = tmp_path / "evidence"
    monkeypatch.setattr(sys, "argv",
                        ["gate", "--out", str(out), "--fields", str(fields),
                         "--bound-fields", str(bound)])
    status = gate.main()
    return status, json.loads((out / "score.json").read_text())


def healthy(**overrides):
    """Respond the way the shipped product does for each shape."""
    def respond(ctrl, guard, **kwargs):
        n = len(kwargs["positions"])
        params = kwargs["protocol_params"]
        hooked = kwargs.get("hook_strategy") is not None
        shared = hooked and params["interval_s"] == 0
        return payload(n=n, acquisitions=1 if shared else n, **overrides)
    return respond


def statuses(report):
    return {l["limb"].split(" - ")[0]: l["status"] for l in report["limbs"]}


def test_a_healthy_product_passes_every_criterion(monkeypatch, tmp_path):
    status, report = score(monkeypatch, tmp_path, healthy())
    assert status == 0, report
    assert set(statuses(report).values()) == {"PASS", "MEASURED"}
    # The measurement is reported and excluded from the score.
    assert report["total"] == len(report["limbs"]) - 1
    measured = [l for l in report["limbs"] if l["status"] == "MEASURED"]
    assert len(measured) == 1 and measured[0]["data"]


def test_no_bridge_reports_not_exercised_and_still_fails(monkeypatch, tmp_path):
    status, report = score(monkeypatch, tmp_path, healthy(), bridge=False)
    assert status != 0
    seen = statuses(report)
    assert seen["0"] == "NOT EXERCISED"
    assert "PASS" not in seen.values()


@pytest.mark.parametrize("limb,mutate", [
    ("1", lambda: healthy(dominant=True)),
    ("2", lambda: healthy(drift=1e-3)),
    ("4", lambda: healthy(child_breakdowns=True)),
    ("4", lambda: healthy(disclose=False)),
])
def test_each_payload_mutant_fails_its_own_limb(monkeypatch, tmp_path, limb, mutate):
    status, report = score(monkeypatch, tmp_path, mutate())
    assert status != 0
    assert statuses(report)[limb] == "FAIL", report


def test_a_missing_breakdown_fails_limb_one(monkeypatch, tmp_path):
    def respond(ctrl, guard, **kwargs):
        result = healthy()(ctrl, guard, **kwargs)
        result.pop("duration_breakdown")
        return result
    status, report = score(monkeypatch, tmp_path, respond)
    assert status != 0
    assert statuses(report)["1"] == "FAIL"


def test_a_route_blind_breakdown_fails_limb_three(monkeypatch, tmp_path):
    """The discriminating control: every shape reporting N acquisitions.

    This is the mutant that a gate written without a control would pass. The
    shared-dataset route constructs one Acquisition for the whole grid; a
    breakdown that reports N there is not measuring the route the run took.
    """
    def respond(ctrl, guard, **kwargs):
        n = len(kwargs["positions"])
        return payload(n=n, acquisitions=n)
    status, report = score(monkeypatch, tmp_path, respond)
    assert status != 0
    seen = statuses(report)
    assert seen["3"] == "FAIL"
    assert seen["1"] == "PASS", "the mutant must be well formed, or limb 3 is untested"


def test_an_unbounded_payload_fails_limb_five(monkeypatch, tmp_path):
    def respond(ctrl, guard, **kwargs):
        n = len(kwargs["positions"])
        params = kwargs["protocol_params"]
        hooked = kwargs.get("hook_strategy") is not None
        shared = hooked and params["interval_s"] == 0
        # Grows with the field count, which is what the bound forbids.
        return payload(n=n, acquisitions=1 if shared else n, extra_bytes=20 * n)
    status, report = score(monkeypatch, tmp_path, respond)
    assert status != 0
    assert statuses(report)["5"] == "FAIL"


def test_a_tool_error_fails_rather_than_scoring_a_pass(monkeypatch, tmp_path):
    def respond(ctrl, guard, **kwargs):
        return {"error": "refused: workspace not configured"}
    status, report = score(monkeypatch, tmp_path, respond)
    assert status != 0
    assert statuses(report)["1"] == "FAIL"
    # Downstream limbs must not invent a pass from an empty payload set.
    assert "PASS" not in {k: v for k, v in statuses(report).items()
                          if k in {"2", "3"}}.values()


def test_a_raising_tool_is_reported_with_a_traceback(monkeypatch, tmp_path):
    def respond(ctrl, guard, **kwargs):
        raise RuntimeError("bridge went away")
    install(monkeypatch, tmp_path, respond=respond)
    out = tmp_path / "evidence"
    monkeypatch.setattr(sys, "argv", ["gate", "--out", str(out)])
    assert gate.main() != 0
    assert list(out.glob("traceback-*.txt")), "a raising limb must leave its trace"
