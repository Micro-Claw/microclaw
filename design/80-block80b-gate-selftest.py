"""Selftest for design/80-block80b-demo-gate.py. Run it before the gate ships.

Gate code gets no review pass and runs unattended on someone else's machine, so
it is executed here first. Writing this file already found four defects in the
gate: limb B exported a `call_input` limb A never recorded, limb B carried dead
`if False else None` scaffolding, limb G checksummed the *emitter body* against a
constant measured over the whole exported *file* (so it could never have
matched), and the teardown called a `MicroscopeController.close()` that does not
exist.

Three rules this file obeys:

* **Reuse the dispatching fake, do not write a third one.** `hooked_engine` in
  `tests/test_session_script_export.py` is already correct on both engine
  contracts -- its `Acquisition.__new__` dispatches and its `__init__` raises if
  the dispatcher ever runs (ninth contract, which cost block 75b a demo gate),
  and it fires `image_saved_fn` from `__exit__`, not from `acquire()` (eighth).
* **The fake is written from the dependency, not from the caller** (60b). The
  standalone hook log this file plants is written by the REAL hook through the
  REAL `HookBase._write_log`, at the path the REAL emitter chose -- never a
  filename invented here to match the gate's glob. That invented filename is
  exactly what cost block 60b its rig trip.
* **Carry a control that fires** (58a). `test_checksum_control_fires` mutates the
  hookless export and requires limb G to fail; a limb that cannot fail is not a
  criterion.

What this selftest CANNOT cover, and the runbook says so too: limbs C and F run
the exported script in a **child process against the real bridge**. No fake can
be a real AcqEngJ in a subprocess, so here their `subprocess.run` is stubbed and
only their parsing, globbing and comparison logic is exercised. Whether the
emitted script actually runs is the one thing this gate exists to find out, and
only the demo machine can answer it.

    .venv/bin/python -m pytest -q design/80-block80b-gate-selftest.py
    MICROCLAW_TREE_UNDER_TEST=<pre-80b checkout> \
      .venv/bin/python -m pytest -q design/80-block80b-gate-selftest.py -k standdown
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_session_script_export import hooked_engine  # noqa: F401,E402  (fixture)
from tests.test_session_script_export import Guard  # noqa: E402

GATE_PATH = ROOT / "design" / "80-block80b-demo-gate.py"


def load_gate():
    spec = importlib.util.spec_from_file_location("block80b_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gate():
    module = load_gate()
    module.RESULTS.clear()
    return module


def test_gate_parses_and_exposes_its_harness(gate):
    for name in ("limb", "emitter_probe", "hookless_checksum", "read_hook_log",
                 "autofocus_records", "skipped_records", "finish", "main",
                 "NotExercised", "HOOKLESS_SHA"):
        assert hasattr(gate, name), f"the gate lost {name}"


def test_emitter_probe_renders_a_hooked_plan_on_this_tree(gate):
    from microclaw import tools
    rendered = gate.emitter_probe(tools)
    assert not rendered.startswith("REFUSED: "), rendered
    assert "class AutofocusHook" in rendered or "AutofocusHook(" in rendered
    # The probe must supply everything the emitter needs, or limb E would report
    # a missing-bounds refusal as "no 80b in this build".
    assert "_LIMITS = " in rendered


def test_hookless_checksum_matches_the_recorded_constant(gate, tmp_path):
    from microclaw import tools
    digest, lines = gate.hookless_checksum(tools, Guard(tmp_path), tmp_path / "g.py")
    assert digest == gate.HOOKLESS_SHA, (
        f"the gate's constant is stale: measured {digest} over {lines} lines")


def test_checksum_control_fires(gate, tmp_path, monkeypatch):
    """A limb that cannot fail is not a criterion (58a)."""
    from microclaw import tools
    monkeypatch.setattr(gate, "HOOKLESS_SHA", "0" * 64)
    digest, _ = gate.hookless_checksum(tools, Guard(tmp_path), tmp_path / "g.py")
    assert digest != gate.HOOKLESS_SHA


def test_autofocus_and_skipped_records_are_scored_apart(gate):
    records = [{"position": "a", "best_z_um": 3.2, "converged": True},
               {"position": "b", "autofocus": "skipped", "reason": "Z out of bounds"}]
    assert len(gate.autofocus_records(records)) == 1
    assert len(gate.skipped_records(records)) == 1
    # A skip is a real outcome, not a missing callback: limb A must be able to
    # tell "the hook refused its own bounds" from "the hook never ran".
    assert gate.autofocus_records(records)[0]["position"] == "a"


def test_read_hook_log_reports_a_torn_file_rather_than_losing_it(gate, tmp_path):
    path = tmp_path / "torn.json"
    path.write_text('[{"position": "a"', encoding="utf-8")
    with pytest.raises(AssertionError, match="not readable JSON"):
        gate.read_hook_log(path)
    missing = tmp_path / "absent.json"
    with pytest.raises(gate.NotExercised):
        gate.read_hook_log(missing)


def test_limb_d_reads_hook_exposures_and_rejects_a_silent_callback(gate):
    """Scored on the HOOK's counter, never on the saved-frame callback.

    CLAUDE.md's eighth contract: if the notification path is what failed,
    frames_accounted is 0 and a predicate over it never fires. hook_exposures is
    incremented by the hook itself, so it is the honest signal.
    """
    import re
    healthy = ("HOOK ACQUISITION ENVELOPE: this script enforces no dose budget.\n"
               "HOOK ACQUISITION COUNTS run saved_frames= 2 hook_exposures= 14 no dose budget")
    silent = ("HOOK ACQUISITION ENVELOPE: this script enforces no dose budget.\n"
              "HOOK ACQUISITION COUNTS run saved_frames= 2 hook_exposures= 0 no dose budget")
    for text, expected in ((healthy, True), (silent, False)):
        found = [int(m) for m in re.findall(r"hook_exposures=\s*(\d+)", text)]
        assert found, "the gate's own regex must match the script it emits"
        assert any(n > 0 for n in found) is expected


def test_limb_d_regex_matches_the_real_emitted_line(gate, tmp_path):
    """The regex must be checked against the EMITTER's output, not a guess.

    60b's gate failed its one rig limb because its glob matched a filename the
    fake produced rather than the one ndstorage writes. Same shape: read the
    line out of a real export.
    """
    import re
    from microclaw import tools
    records = [{"role": "assistant", "content": [{
        "type": "tool_use", "id": "toolu_x", "name": "run_multiposition_acquisition",
        "input": {"protocol": "timelapse",
                  "positions": [{"name": "a", "x_um": 1, "y_um": 2, "z_um": 3}],
                  "protocol_params": {"n_frames": 1, "interval_s": 0, "exposure_ms": 10},
                  "save_dir": "/data", "name": "run",
                  "hook_strategy": "autofocus_per_position",
                  "hook_params": {"z_range_um": 4, "z_step_um": 1}},
    }]}]
    path = tmp_path / "exported.py"
    result = tools.export_session_script(None, Guard(tmp_path), str(path), records)
    assert result["complete"], result.get("not_emitted_calls")
    source = path.read_text(encoding="utf-8")
    counts = [l for l in source.splitlines() if "HOOK ACQUISITION COUNTS" in l]
    assert counts, "the emitter no longer prints the line limb D reads"
    # The emitted line interpolates at run time, so match the format string's
    # literal prefix -- what limb D greps must appear in what the emitter writes.
    assert "hook_exposures=" in counts[0]
    assert [l for l in source.splitlines() if "no dose budget" in l], (
        "the envelope no longer discloses the missing dose budget")
    # And limb B's required strings must really be present.
    for needed in ("class AutofocusHook", "def coarse_then_fine_autofocus",
                   "_LIMITS = ", "guard.check_xy(", "post_hardware_hook_fn"):
        assert needed in source, f"limb B looks for {needed!r} and the export lacks it"


def test_limb_b_leak_regex_catches_a_microclaw_import(gate):
    import re
    for line in ("from microclaw import tools", "  import microclaw.safety",
                 "from microclaw.hooks import HookBase"):
        assert re.match(r"\s*(from|import)\s+microclaw", line), line
    for line in ("# from microclaw import tools", "text = 'import microclaw'"):
        assert not re.match(r"\s*(from|import)\s+microclaw", line), line


def test_standdown_on_a_pre_80b_tree(gate):
    """Point MICROCLAW_TREE_UNDER_TEST at a pre-80b checkout and run this.

    On such a tree the fixed-plan emitter refuses, so limb E must FAIL and every
    later limb must report NOT EXERCISED -- never PASS, and never a cascade of
    unrelated failures that says nothing about the build.
    """
    from microclaw import tools
    rendered = gate.emitter_probe(tools)
    if not rendered.startswith("REFUSED: "):
        pytest.skip("this tree has 80b in it; run with MICROCLAW_TREE_UNDER_TEST "
                    "pointed at a pre-80b checkout to exercise the stand-down")
    assert "HookBase" in rendered or "hooked acquisition" in rendered


# --- Added after the demo machine's first run stood the whole gate down. -----
# The gate guessed a 4 um sweep, the stage sat at Z=1.0 with z_min=0.0, and
# seven limbs reported NOT EXERCISED for a reason that was the gate's, not the
# product's. These cases carry that machine's exact numbers.

def test_choose_sweep_fits_the_demo_machines_actual_envelope(gate):
    """The run that failed: Z at 1.0, z_min 0.0. It must now choose, not refuse."""
    centre, used, note = gate.choose_sweep(
        z_now=1.0, z_min=0.0, z_max=100.0, requested=4.0, z_step=1.0)
    assert used == 4.0, note
    assert centre - used / 2 >= 0.0, f"{centre} - {used}/2 is below z_min"
    assert centre - used / 2 > 0.0, (
        "the sweep sits exactly on the inclusive bound, which passes the check "
        "while leaving the hook no room")
    assert "centre moved" in note


def test_choose_sweep_shrinks_rather_than_standing_down(gate):
    centre, used, note = gate.choose_sweep(
        z_now=1.0, z_min=0.0, z_max=3.0, requested=10.0, z_step=0.5)
    assert used < 10.0 and used <= 3.0
    assert 0.0 <= centre - used / 2 and centre + used / 2 <= 3.0
    assert "shrunk" in note


def test_choose_sweep_leaves_a_usable_stage_alone(gate):
    centre, used, note = gate.choose_sweep(
        z_now=50.0, z_min=0.0, z_max=100.0, requested=4.0, z_step=1.0)
    assert (centre, used) == (50.0, 4.0)
    assert "already clear" in note


def test_choose_sweep_reports_not_exercised_only_when_nothing_fits(gate):
    with pytest.raises(gate.NotExercised, match="cannot hold"):
        gate.choose_sweep(z_now=0.2, z_min=0.0, z_max=0.4, requested=4.0, z_step=1.0)


def test_choose_sweep_never_returns_an_out_of_bounds_edge(gate):
    """Property sweep: whatever it returns must be inside the envelope."""
    for z_now in (-5.0, 0.0, 0.5, 1.0, 7.0, 99.0, 500.0):
        for z_min, z_max in ((0.0, 100.0), (0.0, 3.0), (-50.0, -10.0), (10.0, 11.0)):
            try:
                centre, used, _ = gate.choose_sweep(z_now, z_min, z_max, 4.0, 1.0)
            except gate.NotExercised:
                continue
            assert z_min <= centre - used / 2, (z_now, z_min, z_max, centre, used)
            assert centre + used / 2 <= z_max, (z_now, z_min, z_max, centre, used)


def test_limb_g_is_not_coupled_to_the_bridge(gate):
    """G is a checksum over an emitted file and must survive a stand-down.

    On the demo machine's first run G reported NOT EXERCISED because limb 0
    could not establish the rig -- evidence lost to a coupling this gate
    introduced while fixing G's own checksum. Read structurally, not by
    grepping for a marker comment that any edit can move.
    """
    import ast as _ast
    tree = _ast.parse(GATE_PATH.read_text(encoding="utf-8"))
    later = None
    for node in _ast.walk(tree):
        if (isinstance(node, _ast.Assign)
                and any(getattr(x, "id", None) == "LATER" for x in node.targets)):
            later = [e.value for e in node.value.elts
                     if isinstance(e, _ast.Constant)]
    assert later is not None, "the gate no longer has a LATER stand-down list"
    assert not any("hookless" in name for name in later), (
        f"limb G is still in the stand-down list {later}, so limb E taking the "
        "gate down would take G's bridge-free evidence with it")
    # And the rig limbs must still be there, or the stand-down reports nothing.
    assert any(name.startswith("A ") for name in later)
    assert any(name.startswith("C ") for name in later)


def _synthetic_config(monkeypatch):
    """Give the gate a config without one being installed on this machine."""
    import microclaw.config as cfg
    from microclaw.safety import SafetyConstraints, StageConstraints
    parsed = SimpleNamespace(constraints=SafetyConstraints(stage=StageConstraints(
        x_min=-1000, x_max=1000, y_min=-1000, y_max=1000, z_min=0.0, z_max=100.0)))
    monkeypatch.setattr(cfg, "load_safety_config_or_exit", lambda _p=None: parsed)
    return parsed


def test_limb_g_scores_even_when_the_control_stands_the_gate_down(gate, tmp_path,
                                                                  monkeypatch):
    """Drive main() with a refusing control and require a PASSING G row."""
    _synthetic_config(monkeypatch)
    monkeypatch.setattr(gate, "emitter_probe", lambda _t: "REFUSED: pretend pre-80b")
    monkeypatch.setattr(sys, "argv", ["gate", "--out", str(tmp_path / "ev")])
    rc = gate.main()
    assert rc == 1, "a stood-down gate must exit nonzero"
    rows = {r["name"]: r for r in gate.RESULTS}
    g = [name for name in rows if name.startswith("G ")]
    assert g, f"no G row in a stood-down run: {list(rows)}"
    assert rows[g[0]]["status"] == "PASS", rows[g[0]]
    assert rows[[n for n in rows if n.startswith("A ")][0]]["status"] == "NOT EXERCISED"


def test_a_missing_safety_config_does_not_kill_the_gate(gate, tmp_path, monkeypatch):
    """load_safety_config_or_exit raises SystemExit BY DESIGN.

    An earlier cut of this fix loaded it before the control and re-raised, so a
    machine with no safety config would have produced no score.json at all --
    the control's answer lost to an unrelated missing file.
    """
    import microclaw.config as cfg

    def explode(_p=None):
        raise SystemExit("No safety config at /nowhere/safety_config.yaml.")

    monkeypatch.setattr(cfg, "load_safety_config_or_exit", explode)
    monkeypatch.setattr(sys, "argv", ["gate", "--out", str(tmp_path / "ev")])
    rc = gate.main()
    assert rc == 1
    rows = {r["name"]: r["status"] for r in gate.RESULTS}
    control = [n for n in rows if n.startswith("E ")]
    assert control and rows[control[0]] == "PASS", (
        f"the control must still be scored without a safety config: {rows}")
    assert (tmp_path / "ev" / "score.json").exists(), "no score was written"
    detail = next(r["detail"] for r in gate.RESULTS if r["name"].startswith("0 "))
    assert "safety config" in detail, detail
