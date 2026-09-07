"""Selftest for design/80-block80c-demo-gate.py. Run it before the gate ships.

Gate code gets no review pass and runs unattended on someone else's machine, so
it is executed here first. design/59a is why the fake must be **bridge shaped**:
that block's gate reached the demo machine and every limb died on
`TypeError: 'mmcorej_StrVector' object is not iterable`, because a `list()` over
a Core collection works against every fake in the suite and fails on every rig.
A `MagicMock` would not have caught it, and would not catch this block's shape
either -- `getPropertyNames()` returns a Java `String[]`, which pyjavaz delivers
as a shadow with no `iterator()`, no `__len__` and no `__getitem__`.

Four rules this file obeys:

* **Reuse the shapes that already exist, do not write a fourth fake.** The
  array shape comes from `design/80-block80c-probe-selftest.py`, which was
  written from `pyjavaz/bridge.py` rather than from our caller -- the mistake
  that cost this notebook a rig trip.
* **The fake is written from the dependency, not from the caller** (60b).
* **Carry controls that fire** (58a). Three of them: a mutated checksum, a
  refusing emitter, and a config with hardware motion disabled.
* **Check the gate's greps against a REAL export**, never against a guess. Every
  string limb B looks for is asserted present in an actually emitted script, so
  a rename in `tools.py` fails here and not on the operator's machine.

What this selftest CANNOT cover, and the runbook says so too: limb C runs the
exported script in a **child process against the real bridge and a real
OughtaFocus**. No fake can be that. Whether the emitted script actually runs is
the one thing this gate exists to find out, and only the demo machine can
answer it.

    .venv/bin/python -m pytest -q design/80-block80c-gate-selftest.py
    MICROCLAW_TREE_UNDER_TEST=<pre-80c checkout> \
      .venv/bin/python -m pytest -q design/80-block80c-gate-selftest.py -k standdown
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_session_script_export import Guard  # noqa: E402

GATE_PATH = ROOT / "design" / "80-block80c-demo-gate.py"


def load_gate():
    spec = importlib.util.spec_from_file_location("block80c_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gate():
    module = load_gate()
    module.RESULTS.clear()
    module.OBSERVATIONS.clear()
    return module


def _string_array():
    """A Java String[] as pyjavaz actually delivers one.

    `_deserialize` routes a non-primitive array through `unserialized-object`
    and `_JavaClassFactory.create` gives the shadow only what the server
    reported for the class -- for an array, java.lang.Object's methods and no
    fields. So: no iterator(), no length, no __len__, no __getitem__.
    """
    return type("[Ljava_lang_String;", (), {})()


def test_the_array_fake_is_actually_array_shaped():
    """If this fake is wrong, every case below is testing the wrong thing."""
    names = _string_array()
    with pytest.raises(TypeError):
        list(names)
    for attr in ("iterator", "__len__", "__getitem__", "length"):
        assert not hasattr(names, attr), f"the fake grew {attr}; it is not a Java array"


def test_the_product_drain_still_cannot_read_it():
    """The measurement two rigs made, pinned so a 'fix' to the drain is noticed.

    If `_drain_java_iterable` ever learns arrays this fails, and that is a
    decision for a block to take deliberately -- the drain is inlined into every
    exported script, so a change there travels.
    """
    from microclaw import controller
    with pytest.raises(AttributeError, match="iterator"):
        controller._drain_java_iterable(_string_array())


def test_gate_parses_and_exposes_its_harness(gate):
    for name in ("limb", "observe", "emitter_probe", "hookless_checksum",
                 "read_hook_log", "focused_records", "skipped_records",
                 "unsafe_records", "comparable", "choose_centre",
                 "printed_snapshot", "finish", "main", "NotExercised",
                 "HOOKLESS_SHA", "DISCLOSURE"):
        assert hasattr(gate, name), f"the gate lost {name}"


def test_emitter_probe_renders_the_plugin_hook_on_this_tree(gate):
    from microclaw import tools
    rendered = gate.emitter_probe(tools, "autofocus_mm_plugin")
    assert not rendered.startswith("REFUSED: "), rendered
    # The probe must supply everything the emitter needs, or limb E would report
    # a missing-bounds refusal as "no 80c in this build".
    assert "_LIMITS = " in rendered


def test_emitter_probe_still_refuses_the_analyzer_and_names_get_object(gate):
    from microclaw import tools
    rendered = gate.emitter_probe(tools, "mm_plugin_analyzer")
    assert rendered.startswith("REFUSED: "), rendered
    assert "get_object" in rendered, rendered


def test_limb_e_strings_appear_in_a_real_emitter_render(gate):
    """Limb E's greps, checked against the emitter rather than against a guess."""
    from microclaw import tools
    rendered = gate.emitter_probe(tools, "autofocus_mm_plugin")
    for needed in ("class MMAutofocusPluginHook", "mm.plugins = ",
                   "_PLUGIN_MOTION_TOKEN", gate.DISCLOSURE):
        assert needed in rendered, f"limb E looks for {needed!r} and it is absent"


def test_limb_b_strings_appear_in_a_real_exported_script(gate, tmp_path):
    """Every string limb B requires, read out of an actually emitted file.

    60b's gate lost its one rig limb to a glob matching what its fake wrote
    rather than what the dependency writes. Same shape, one level up: a rename
    in tools.py must fail here, not on the operator's machine.
    """
    from microclaw import tools
    snapshot = {"available": True,
                "settings": {"SearchRange_um": "10", "FFTLowerCutoff(%)": "2,5"}}
    records = [
        {"role": "assistant", "content": [{
            "type": "tool_use", "id": "toolu_x",
            "name": "run_multiposition_acquisition",
            "input": {"protocol": "timelapse",
                      "positions": [{"name": "a", "x_um": 1, "y_um": 2, "z_um": 3},
                                    {"name": "b", "x_um": 11, "y_um": 2, "z_um": 3}],
                      "protocol_params": {"n_frames": 1, "interval_s": 0,
                                          "exposure_ms": 10},
                      "save_dir": "/data", "name": "run",
                      "hook_strategy": "autofocus_mm_plugin",
                      "hook_params": {"plugin_name": "OughtaFocus"}},
        }]},
        {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "toolu_x",
            "content": __import__("json").dumps(
                {"dataset_path": "/data/run", "autofocus_settings_snapshot": snapshot}),
        }]},
    ]
    path = tmp_path / "exported.py"
    result = tools.export_session_script(None, Guard(tmp_path), str(path), records)
    assert result["complete"], result.get("not_emitted_calls")
    source = path.read_text(encoding="utf-8")
    for needed in ("class MMAutofocusPluginHook",
                   "def _autofocus_settings_snapshot",
                   "def _new_static_java_class", "java.lang.reflect.Array",
                   "mm.plugins = ", "_PLUGIN_MOTION_TOKEN = ",
                   "def check_plugin_motion", "_LIMITS = ",
                   "guard.check_xy(", "post_hardware_hook_fn", gate.DISCLOSURE):
        assert needed in source, f"limb B looks for {needed!r} and the export lacks it"
    assert "input(" not in source
    assert "asList" not in source
    # And limb B's per-setting check: the recorded values must be renderable.
    for value in snapshot["settings"].values():
        assert repr(value) in source, value
    # Limb D reads these two out of the child process's stdout.
    counts = [l for l in source.splitlines() if "HOOK ACQUISITION COUNTS" in l]
    assert counts and "hook_exposures=" in counts[0]
    assert [l for l in source.splitlines() if "Recorded settings snapshot:" in l]
    assert [l for l in source.splitlines() if "Live settings snapshot:" in l]


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


def test_focus_skip_and_abort_records_are_scored_apart(gate):
    records = [{"position": "a", "best_z_um": 3.2, "plugin": "OughtaFocus"},
               {"position": "b", "autofocus": "skipped", "reason": "plugin raised"},
               {"position": "c", "autofocus": "unsafe_abort", "unsafe_z": 999.0}]
    assert [r["position"] for r in gate.focused_records(records)] == ["a"]
    assert [r["position"] for r in gate.skipped_records(records)] == ["b"]
    assert [r["position"] for r in gate.unsafe_records(records)] == ["c"]


def test_comparable_drops_only_the_arrival_stamp(gate):
    records = [{"position": "a", "best_z_um": 3.2, "plugin": "OughtaFocus",
                "observed_at": "2026-09-07T21:00:00"}]
    assert gate.comparable(records) == [
        {"position": "a", "best_z_um": 3.2, "plugin": "OughtaFocus"}]


def test_read_hook_log_reports_a_torn_file_rather_than_losing_it(gate, tmp_path):
    path = tmp_path / "torn.json"
    path.write_text('[{"position": "a"', encoding="utf-8")
    with pytest.raises(AssertionError, match="not readable JSON"):
        gate.read_hook_log(path)
    with pytest.raises(gate.NotExercised):
        gate.read_hook_log(tmp_path / "absent.json")


def test_printed_snapshot_survives_a_locale_comma_and_an_embedded_newline(gate):
    """The two shapes design/80 measured, parsed rather than regexed.

    `'2,5'` is the demo machine's FFTLowerCutoff against M2's `'2.5'`; the
    newline is what every Micro-Manager bridge exception carries, and 52b's
    export died on exactly one of those. `print(dict)` reprs the dict, so the
    newline is escaped and the line stays single -- assert that rather than
    assuming it.
    """
    import io
    from contextlib import redirect_stdout
    for value in ({"available": True, "settings": {"FFTLowerCutoff(%)": "2,5"}},
                  {"available": False, "reason": "RuntimeError: names\nbridge detail"}):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print("Recorded settings snapshot:", value)
        text = buffer.getvalue()
        assert len(text.strip().splitlines()) == 1, (
            "the emitted print spans two lines, so a line-oriented reader "
            "cannot recover it: " + text)
        assert gate.printed_snapshot(text, "Recorded settings snapshot:") == value
    assert gate.printed_snapshot("nothing here", "Recorded settings snapshot:") is None


def test_choose_centre_uses_the_plugins_own_range_on_this_machines_envelope(gate):
    """The demo machine's real numbers: Z envelope 0..100, OughtaFocus 10 um."""
    centre, note = gate.choose_centre(z_now=1.0, z_min=0.0, z_max=100.0, search_um=10.0)
    assert centre - 5.0 > 0.0, (
        "the search sits on or below the inclusive bound, which passes the "
        "check while leaving the plugin no room")
    assert centre + 5.0 < 100.0
    assert "centre moved" in note


def test_choose_centre_leaves_a_usable_stage_alone(gate):
    centre, note = gate.choose_centre(z_now=50.0, z_min=0.0, z_max=100.0, search_um=10.0)
    assert centre == 50.0
    assert "already clears" in note


def test_choose_centre_stands_down_rather_than_narrowing_the_plugin(gate):
    """It must never shrink SearchRange_um: that is the substitution 80c forbids."""
    with pytest.raises(gate.NotExercised, match="will not narrow"):
        gate.choose_centre(z_now=1.0, z_min=0.0, z_max=4.0, search_um=10.0)


def test_choose_centre_never_returns_an_out_of_bounds_edge(gate):
    for z_now in (-5.0, 0.0, 0.5, 1.0, 7.0, 99.0, 500.0):
        for z_min, z_max in ((0.0, 100.0), (0.0, 3.0), (-50.0, -10.0), (10.0, 11.0)):
            for search in (10.0, 1.0, 0.5):
                try:
                    centre, _ = gate.choose_centre(z_now, z_min, z_max, search)
                except gate.NotExercised:
                    continue
                assert z_min <= centre - search / 2, (z_now, z_min, z_max, search)
                assert centre + search / 2 <= z_max, (z_now, z_min, z_max, search)


def test_limb_d_reads_hook_exposures_and_rejects_a_silent_callback(gate):
    """Scored on the HOOK's counter, never on the saved-frame callback.

    CLAUDE.md's eighth contract: if the notification path is what failed,
    frames_accounted is 0 and a predicate over it never fires.
    """
    import re
    for text, expected in (
            ("HOOK ACQUISITION COUNTS run saved_frames= 2 hook_exposures= 2 no dose budget", True),
            ("HOOK ACQUISITION COUNTS run saved_frames= 2 hook_exposures= 0 no dose budget", False)):
        found = [int(m) for m in re.findall(r"hook_exposures=\s*(\d+)", text)]
        assert found, "the gate's own regex must match the script it emits"
        assert any(n > 0 for n in found) is expected


def test_limb_b_leak_regex_catches_a_microclaw_import(gate):
    import re
    for line in ("from microclaw import tools", "  import microclaw.safety",
                 "from microclaw.hooks import HookBase"):
        assert re.match(r"\s*(from|import)\s+microclaw", line), line
    for line in ("# from microclaw import tools", "text = 'import microclaw'"):
        assert not re.match(r"\s*(from|import)\s+microclaw", line), line


def test_limb_g_is_not_coupled_to_the_bridge(gate):
    """G is a checksum over an emitted file and must survive a stand-down.

    On 80b's first demo run G reported NOT EXERCISED because limb 0 could not
    establish the rig -- bridge-free evidence lost to a coupling that gate
    introduced. Read structurally, not by grepping for a marker comment.
    """
    tree = ast.parse(GATE_PATH.read_text(encoding="utf-8"))
    later = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(getattr(x, "id", None) == "LATER" for x in node.targets)):
            later = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
    assert later is not None, "the gate no longer has a LATER stand-down list"
    assert not any("hookless" in name for name in later), (
        f"limb G is in the stand-down list {later}, so limb E taking the gate "
        "down would take G's bridge-free evidence with it")
    assert any(name.startswith("A ") for name in later)
    assert any(name.startswith("C ") for name in later)


def _synthetic_config(monkeypatch, *, allow_motion=True):
    """Give the gate a config without one being installed on this machine."""
    import microclaw.config as cfg
    from microclaw.safety import (PluginConstraints, SafetyConstraints,
                                  StageConstraints)
    parsed = SimpleNamespace(constraints=SafetyConstraints(
        stage=StageConstraints(x_min=-1000, x_max=1000, y_min=-1000, y_max=1000,
                               z_min=0.0, z_max=100.0),
        plugins=PluginConstraints(blocked=[], allow_hardware_motion=allow_motion)))
    monkeypatch.setattr(cfg, "load_safety_config_or_exit", lambda _p=None: parsed)
    return parsed


def test_limb_g_scores_even_when_the_control_stands_the_gate_down(gate, tmp_path,
                                                                  monkeypatch):
    """Drive main() with a refusing control and require a PASSING G row."""
    _synthetic_config(monkeypatch)
    monkeypatch.setattr(gate, "emitter_probe",
                        lambda _t, _s: "REFUSED: pretend pre-80c")
    monkeypatch.setattr(sys, "argv", ["gate", "--out", str(tmp_path / "ev")])
    rc = gate.main()
    assert rc == 1, "a stood-down gate must exit nonzero"
    rows = {r["name"]: r for r in gate.RESULTS}
    g = [name for name in rows if name.startswith("G ")]
    assert g, f"no G row in a stood-down run: {list(rows)}"
    assert rows[g[0]]["status"] == "PASS", rows[g[0]]
    assert rows[[n for n in rows if n.startswith("A ")][0]]["status"] == "NOT EXERCISED"


def test_hardware_motion_disabled_is_not_exercised_not_failed(gate, tmp_path,
                                                              monkeypatch):
    """The PRODUCT's own gate. A config that forbids motion is not a defect.

    It must report NOT EXERCISED naming the literal line to add, and the gate
    must never edit a production safety config to get past it (60b).
    """
    _synthetic_config(monkeypatch, allow_motion=False)
    monkeypatch.setattr(sys, "argv", ["gate", "--out", str(tmp_path / "ev")])
    rc = gate.main()
    assert rc == 1
    row = next(r for r in gate.RESULTS if r["name"].startswith("0 "))
    assert row["status"] == "NOT EXERCISED", row
    assert "allow_hardware_motion: true" in row["detail"], row["detail"]
    assert "will not edit" in row["detail"], row["detail"]


def test_a_missing_safety_config_does_not_kill_the_gate(gate, tmp_path, monkeypatch):
    """load_safety_config_or_exit raises SystemExit BY DESIGN.

    A gate that lets it through prints no score at all, so the control's answer
    would be lost to an unrelated missing file.
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


def test_observations_are_not_scored_as_limbs(gate, tmp_path, monkeypatch):
    """58a: a limb that cannot fail is not a criterion, so passengers are not limbs."""
    _synthetic_config(monkeypatch)
    monkeypatch.setattr(gate, "emitter_probe",
                        lambda _t, _s: "REFUSED: pretend pre-80c")
    monkeypatch.setattr(sys, "argv", ["gate", "--out", str(tmp_path / "ev")])
    gate.main()
    assert all("passenger" not in r["name"] for r in gate.RESULTS), (
        "a passenger reading is being scored as a limb")


def test_standdown_on_a_pre_80c_tree(gate):
    """Point MICROCLAW_TREE_UNDER_TEST at a pre-80c checkout and run this.

    On such a tree the fixed-plan emitter refuses the plugin hook, so limb E
    must FAIL and every later limb must report NOT EXERCISED -- never PASS, and
    never a cascade of unrelated failures that says nothing about the build.
    """
    from microclaw import tools
    rendered = gate.emitter_probe(tools, "autofocus_mm_plugin")
    if not rendered.startswith("REFUSED: "):
        pytest.skip("this tree has 80c in it; run with MICROCLAW_TREE_UNDER_TEST "
                    "pointed at a pre-80c checkout to exercise the stand-down")
    assert "plugin capabilities" in rendered


# --- Added after the demo machine's first run, whose two non-PASS rows were
# --- both the instrument's. Every criterion was satisfied by the artifacts the
# --- operator sent; only the gate misread them. These carry that run's numbers.

def _limb_source(name):
    tree = ast.parse(GATE_PATH.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def test_limb_d_does_not_refuse_on_a_counter_this_hook_cannot_move(gate):
    """Round 1 FAILed on `hook_exposures= 0`, which is correct for this hook.

    That counter is incremented through the reservation by a hook that snaps its
    own sweep (`AutofocusHook`, hooks.py:299). `MMAutofocusPluginHook` snaps
    nothing -- the plugin searches inside Java and microclaw never sees those
    frames -- so it is 0 in both arms and always will be. The limb was scoring
    the block's own central workflow as a failure. `saved_frames` is no better:
    CLAUDE.md's eighth contract forbids gating a predicate on the saved-frame
    callback.
    """
    # The refusal lives in the `if`, not in the `raise` -- an earlier cut of
    # this case walked Raise nodes only and passed against the very gate it was
    # written to reject. Check the CONDITION that guards each raise.
    for node in ast.walk(_limb_source("limb_d")):
        if not isinstance(node, ast.If):
            continue
        if not any(isinstance(inner, ast.Raise) for inner in ast.walk(node)):
            continue
        names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        assert "exposures" not in names, (
            "limb D refuses on hook_exposures again; it is 0 for this hook in "
            "every healthy run, so the limb would score the block's own central "
            "workflow as a failure")
        assert "saved" not in names, (
            "limb D refuses on saved_frames, which the eighth engine contract "
            "says a predicate must never be gated on")


def test_limb_d_scores_the_hook_log_which_only_the_callback_writes(gate):
    """The honest signal: a record per position, written from inside the callback."""
    # Matched on the string CONSTANT, not on the dump text: the pre-fix limb
    # said "the frames are unfocused", which contains "focused" and made a
    # substring check pass against the gate it was meant to reject.
    literals = {n.value for n in ast.walk(_limb_source("limb_d"))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "focused" in literals, (
        "limb D no longer reads state['C']['focused'], the standalone hook log, "
        "which is the only evidence that post_hardware_hook_fn fired under real "
        "AcqEngJ")


def test_the_demo_machines_round_1_stdout_now_reads_as_a_pass(gate):
    """The exact bytes the demo machine produced on 2026-09-07.

    A REGRESSION case, not watch-it-fail evidence: it drives helpers this fix
    did not change, so it passes on both trees by design. It is here because
    the decisive verification was running the corrected predicates against the
    operator's real artifacts, and this pins that run's shape in the tree.
    """
    out = (
        "HOOK ACQUISITION ENVELOPE: this script enforces no dose budget.\n"
        "AUTOFOCUS PLUGIN ENVELOPE: autofocus:OughtaFocus\n"
        "The plugin's live settings, not this script, decide the focus.\n"
        "Recorded settings snapshot: {'SearchRange_um': '10', 'FFTLowerCutoff(%)': '2,5'}\n"
        "Live settings snapshot: {'SearchRange_um': '10', 'FFTLowerCutoff(%)': '2,5'}\n"
        "HOOK ACQUISITION COUNTS live_plugin saved_frames= 2 hook_exposures= 0 no dose budget\n"
    )
    assert gate.DISCLOSURE in out
    recorded = gate.printed_snapshot(out, "Recorded settings snapshot:")
    live = gate.printed_snapshot(out, "Live settings snapshot:")
    assert recorded == live and recorded is not None
    # The locale value must survive the round trip as text, never as a number.
    assert recorded["FFTLowerCutoff(%)"] == "2,5"
    assert ("settings differ" in out) == (recorded != live)
    # And the log the limb now scores on, in that run's own shape.
    records = [{"position": "gateA", "best_z_um": 6.0, "plugin": "OughtaFocus"},
               {"position": "gateB", "best_z_um": 6.0, "plugin": "OughtaFocus"}]
    assert len(gate.focused_records(records)) == 2


def test_limb_h_takes_its_dataset_prefix_from_the_record(gate):
    """Round 1 globbed 'run'*, which was 80b's dataset name, not this run's.

    The standalone dataset was sitting beside the script as `live_plugin_1` and
    the limb reported NOT EXERCISED over it. That is 60b's defect exactly: a
    glob written from the previous gate rather than from what this one produces.
    """
    node = _limb_source("limb_h")
    literals = {n.value for n in ast.walk(node)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "run" not in literals, (
        "limb H is matching a hardcoded dataset name again")
    assert "call_input" in ast.dump(node), (
        "limb H no longer derives the dataset prefix from the recorded call")


def test_limb_h_prefix_matches_what_the_demo_machine_actually_wrote(gate, tmp_path):
    """`name='live_plugin'` produced `live_plugin_1`. Check the match, not the guess."""
    (tmp_path / "live_plugin_1").mkdir()
    (tmp_path / "unrelated").mkdir()
    prefix = "live_plugin"
    matches = [p for p in sorted(tmp_path.iterdir())
               if p.is_dir() and p.name.startswith(prefix)]
    assert [p.name for p in matches] == ["live_plugin_1"]
    assert not [p for p in sorted(tmp_path.iterdir())
                if p.is_dir() and p.name.startswith("run")], (
        "the round-1 glob would still find nothing here")
