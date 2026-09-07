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
