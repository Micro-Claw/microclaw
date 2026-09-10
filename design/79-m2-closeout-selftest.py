r"""Selftest for design/79's M2 close-out gate. Run before it reaches the rig.

A gate is code, and handing an operator code nobody executed is the defect
CLAUDE.md's workflow keeps paying for. This drives `main()` end to end against a
stub tool layer: a healthy rig, a rig with no bridge, an arm that saves no
frames, a tool that refuses, and a tool that raises.

The stub returns device names as plain strings, which is what
`get_camera_device()` returns over the bridge. This gate iterates no Core
collection, so design/59a's `mmcorej_StrVector` trap does not apply -- saying so
is cheaper than a fake nobody needs.

    uv run python -m pytest -q design\79-m2-closeout-selftest.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).with_name("79-m2-closeout-gate.py")
spec = importlib.util.spec_from_file_location("m2_closeout_gate", GATE)
gate = importlib.util.module_from_spec(spec)
sys.modules["m2_closeout_gate"] = gate
spec.loader.exec_module(gate)


def timelapse_payload(*, n_gaps=19, median=0.28, interval_s=0.0):
    return {
        "status": "Timelapse complete.",
        "dataset_path": "/tmp/x",
        "timing": {"strategy": "no_requested_delay" if not interval_s
                   else "shared_timepoint_clock"},
        "inter_frame_gap_summary": {
            "count": n_gaps, "min_s": 0.21, "mean_s": median,
            "median_le_s": median, "p95_le_s": 0.35, "max_s": 0.36,
            "nonzero_histogram": [{"upper_s": 0.3, "count": n_gaps}],
        },
        "duration_breakdown": {
            "clock": "time.perf_counter", "duration_s": 6.0,
            "phases": {"acquisition": {"count": 1, "min_s": 5.4, "max_s": 5.4,
                                       "total_s": 5.4, "mean_s": 5.4}},
            "accounted_s": 5.4, "unaccounted_s": 0.6,
        },
    }


def grid_payload(*, acquisitions, duration_s):
    return {
        "status": "ok",
        "duration_s": duration_s,
        "duration_breakdown": {
            "clock": "time.perf_counter", "duration_s": duration_s,
            "phases": {"acquisition": {
                "count": acquisitions, "min_s": 0.1, "max_s": 0.4,
                "total_s": 0.3 * acquisitions,
                "mean_s": 0.3}},
            "accounted_s": 0.3 * acquisitions,
            "unaccounted_s": duration_s - 0.3 * acquisitions,
        },
    }


def install(monkeypatch, *, bridge=True, timelapse=None, grid=None):
    from microclaw import tools
    import microclaw.config as config_module
    import microclaw.controller as controller_module
    import microclaw.safety as safety_module

    class Core:
        def get_camera_device(self):
            return "Andor"

        def get_x_position(self):
            return 4392.6      # M2's real position when the first run went out

        def get_y_position(self):
            return -5082.4

    class Ctrl:
        core = Core()

    class Guard:
        def resolve_in_workspace(self, path):
            return str(path)

    if bridge:
        monkeypatch.setattr(controller_module, "MicroscopeController",
                            lambda port=None: Ctrl(), raising=False)
        monkeypatch.setattr(config_module, "load_safety_config_or_exit",
                            lambda _: type("C", (), {"constraints": object()})(),
                            raising=False)
        monkeypatch.setattr(safety_module, "SafetyGuard", lambda _c: Guard(),
                            raising=False)
    else:
        def refuse(_):
            raise SystemExit("no safety config on this machine")
        monkeypatch.setattr(config_module, "load_safety_config_or_exit", refuse,
                            raising=False)

    monkeypatch.setattr(
        tools, "run_timelapse",
        timelapse or (lambda ctrl, guard, n, interval_s, save_dir, **kw:
                      timelapse_payload(interval_s=interval_s)),
        raising=False)
    monkeypatch.setattr(
        tools, "run_multiposition_acquisition",
        grid or (lambda ctrl, guard, **kw: grid_payload(
            acquisitions=len(kw["positions"]) if not kw.get("hook_strategy") else 1,
            duration_s=3.0 if not kw.get("hook_strategy") else 0.6)),
        raising=False)


def score(monkeypatch, tmp_path, **kw):
    extra = kw.pop("argv", [])
    install(monkeypatch, **kw)
    out = tmp_path / "evidence"
    monkeypatch.setattr(sys, "argv",
                        ["gate", "--out", str(out), "--frames", "20"] + extra)
    status = gate.main()
    return status, json.loads((out / "score.json").read_text()), out


def statuses(report):
    return {l["limb"].split(" - ")[0]: l["status"] for l in report["limbs"]}


def test_a_healthy_rig_measures_both_and_scores_only_the_guard(monkeypatch, tmp_path):
    status, report, out = score(monkeypatch, tmp_path)
    assert status == 0, report
    assert statuses(report) == {"0": "PASS", "1": "MEASURED", "2": "MEASURED"}
    # One criterion, two measurements: the measurements must not be scored.
    assert report["total"] == 1 and report["passed"] == 1
    cadence = json.loads((out / "cadence.json").read_text())
    assert set(cadence) == {"zero-interval", "short-60ms", "control-500ms",
                            "short-60ms-no-hook"}
    # The no-hook control is what makes the software-paced cost attributable.
    assert cadence["short-60ms-no-hook"]["hook"] is None
    assert cadence["short-60ms"]["hook"] == "snr_observer"
    assert all(row["n_gaps"] == 19 for row in cadence.values())
    mult = json.loads((out / "multiplier.json").read_text())
    assert mult["per-field"]["acquisitions"] == 6
    assert mult["shared-dataset"]["acquisitions"] == 1
    assert mult["ratio"] == pytest.approx(5.0)
    assert (out / "payload-zero-interval.json").exists()


def test_no_bridge_is_not_exercised_and_fails(monkeypatch, tmp_path):
    status, report, _ = score(monkeypatch, tmp_path, bridge=False)
    assert status != 0
    seen = statuses(report)
    assert seen["0"] == "NOT EXERCISED"
    # A measurement with nothing to measure must not invent one.
    assert seen["1"] == "NOT EXERCISED"


def test_an_arm_that_saved_no_frames_fails_the_guard(monkeypatch, tmp_path):
    def timelapse(ctrl, guard, n, interval_s, save_dir, **kw):
        if interval_s == 0.06:
            return timelapse_payload(n_gaps=0, interval_s=interval_s)
        return timelapse_payload(interval_s=interval_s)
    status, report, _ = score(monkeypatch, tmp_path, timelapse=timelapse)
    assert status != 0
    assert statuses(report)["0"] == "FAIL"
    # The measurement still reports what the other two arms did produce.
    assert statuses(report)["1"] == "MEASURED"


def test_a_refused_tool_fails_rather_than_scoring_a_pass(monkeypatch, tmp_path):
    status, report, _ = score(
        monkeypatch, tmp_path,
        timelapse=lambda *a, **k: {"error": "refused: exposure above envelope"})
    assert status != 0
    assert statuses(report)["0"] == "FAIL"


def test_a_raising_tool_leaves_a_traceback(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("bridge went away")
    status, report, out = score(monkeypatch, tmp_path, timelapse=boom)
    assert status != 0
    assert statuses(report)["0"] == "FAIL"
    assert list(out.glob("traceback-*.txt"))


def test_skip_multiplier_reports_not_exercised_not_a_pass(monkeypatch, tmp_path):
    status, report, _ = score(monkeypatch, tmp_path, argv=["--skip-multiplier"])
    seen = statuses(report)
    assert seen["2"] == "NOT EXERCISED"
    # It is a measurement, so skipping it must not fail the gate.
    assert status == 0 and seen["0"] == "PASS"


def test_the_grid_is_offset_from_the_stage_not_absolute(monkeypatch, tmp_path):
    """The first M2 run asked for absolute (0,0) from 4.4 mm away and failed.

    The demo machine's stage sits at the origin, so absolute coordinates are
    invisible there — this is CLAUDE.md's "never anchor on one microscope" as a
    gate defect, and only a real rig could show it.
    """
    seen = []

    def grid(ctrl, guard, **kw):
        seen.append([(p["x_um"], p["y_um"]) for p in kw["positions"]])
        return grid_payload(
            acquisitions=len(kw["positions"]) if not kw.get("hook_strategy") else 1,
            duration_s=3.0 if not kw.get("hook_strategy") else 0.6)

    status, report, _ = score(monkeypatch, tmp_path, grid=grid,
                             argv=["--fields", "3", "--step-um", "20"])
    assert status == 0
    assert seen, "the grid limb did not run"
    for positions in seen:
        assert positions == [(4392.6, -5082.4), (4412.6, -5082.4),
                             (4432.6, -5082.4)], positions


def test_the_arms_are_r105_shaped(monkeypatch, tmp_path):
    """R105 needs a short interval and 50 ms; 0.5 s is 78a's blind control."""
    assert gate.EXPOSURE_MS == 50.0
    intervals = {label: interval for label, interval, _ in gate.CADENCE_ARMS}
    assert intervals["control-500ms"] == 0.5, "78a's blind arm must be carried"
    assert min(intervals.values()) == 0.0
    assert any(0 < v < 0.1 for v in intervals.values()), \
        "a short nonzero interval is the arm 78a lacked"
    assert any(h is None for _, _, h in gate.CADENCE_ARMS), \
        "design/79 item 4 asks for a no-hook control"
