"""Selftest for design/78 block 78a's M5 gate scorer.

The fixture is 533 lines of M5's REAL CoreLog -- one verbatim write-to-exposure
cycle from `CoreLog20260904T110001_pid18460.txt`, the log design/78 was scored
from -- because the defect this file exists to prevent is a scorer written from
our own assumptions about the log rather than from the log.

That defect was real and this selftest was written after it: the first draft
matched exposures on `[Snap Image] called`, which design/78 quotes from **M2**
and which does not occur once in M5's log. Every cadence limb would have
reported zero exposures on the rig, after the dose was spent.

    .venv/bin/python -m pytest -q design/78-block78a-m5-gate-selftest.py
"""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
EXCERPT = HERE / "78-block78a-m5-corelog-excerpt.txt"

spec = importlib.util.spec_from_file_location(
    "gate78a", HERE / "78-block78a-m5-gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

DEVICE, PROP = "Laser Trigger", "Duration0 (us)"


@pytest.fixture
def real_events():
    return gate.read_corelog(EXCERPT)


def test_every_real_line_parses(real_events):
    """The line grammar must cover the real log, not a tidied version of it."""
    raw = [l for l in EXCERPT.read_text(encoding="utf-8").splitlines() if l.strip()]
    # Continuation lines of multi-line messages are legitimately unparsed;
    # anything more than a handful means the grammar is wrong.
    assert len(real_events) >= len(raw) - 5, (
        f"parsed {len(real_events)} of {len(raw)} real lines")


def test_exposure_marker_matches_this_rig_not_m2(real_events):
    """The M2 string design/78 quotes must NOT be what we match on."""
    assert "[Snap Image] called" not in EXCERPT.read_text(encoding="utf-8")
    assert len(gate.snaps(real_events)) >= 1


def test_write_pairs_and_latency_match_design78(real_events):
    pairs = gate.writes(real_events, DEVICE, PROP)
    assert len(pairs) == 1
    will, did = pairs[0]
    latency = (did.ts - will.ts).total_seconds()
    # design/78 attributes the setter at 39 us on this rig; the point is that
    # it is microseconds, not the seconds the session agent blamed it for.
    assert latency < 0.001, f"setter took {latency}s"


def test_the_fanout_is_present_and_is_the_cost(real_events):
    """The control this gate depends on: with the refresh, the fan-out is huge."""
    fan = gate.emu_reads(real_events)
    assert len(fan) > 50, f"only {len(fan)} EMU retrievals in a real cycle"
    assert len(gate.gui_updates(real_events)) == 1


def test_fanout_is_counted_per_thread(real_events):
    """design/78 asks whether the work MOVES threads; tids must be retained."""
    tids = {e.tid for e, _ in gate.emu_reads(real_events)}
    assert len(tids) >= 2, (
        "M5's real cycle fans out across more than one tid; a scorer that "
        f"collapsed threads would hide that. saw {tids}")


def _score(tmp_path, corelog_text, arms):
    log = tmp_path / "CoreLog.txt"
    log.write_text(corelog_text, encoding="utf-8")
    arms_file = tmp_path / "arms.json"
    arms_file.write_text(json.dumps(arms), encoding="utf-8")
    out = tmp_path / "out"
    rc = gate.main(["--corelog", str(log), "--arms", str(arms_file),
                    "--out", str(out)])
    return rc, json.loads((out / "score.json").read_text())


def _arm(name, refresh_in_source):
    return {"name": name, "start": "2026-09-04T14:11:00",
            "end": "2026-09-04T14:11:30", "device": DEVICE, "property": PROP,
            "commit": name, "refresh_in_source": refresh_in_source}


def test_a_log_with_no_debug_lines_is_not_exercised(tmp_path):
    """An IFO-level log cannot show device reads; saying PASS would be a lie."""
    ifo_only = "\n".join(
        l for l in EXCERPT.read_text(encoding="utf-8").splitlines()
        if "[dbg," not in l) + "\n"
    rc, score = _score(tmp_path, ifo_only, [_arm("write-with-refresh", True)])
    assert rc == 1
    first = score["limbs"][0]
    assert first["limb"] == "CoreLog is at debug level"
    assert first["status"] == "NOT EXERCISED"


def test_without_refresh_arm_can_actually_pass(tmp_path):
    """A limb that cannot pass is not a criterion.

    Build the counterfactual from the real cycle by deleting exactly the lines
    the block removes -- the GUI repaint window and the EMU retrievals -- and
    assert the two key limbs turn PASS. Without this, a scorer that always
    reported FAIL would look identical on the rig.
    """
    kept = []
    for line in EXCERPT.read_text(encoding="utf-8").splitlines():
        if "[EMU] -- Retrieved MMProperty" in line:
            continue
        if "Updating GUI;" in line or line.endswith("Finished updating GUI"):
            continue
        kept.append(line)
    rc, score = _score(tmp_path, "\n".join(kept) + "\n",
                       [_arm("write-without-refresh", False)])
    limbs = {l["limb"]: l for l in score["limbs"]}
    assert limbs["no GUI update between a write and its exposure"]["status"] == "PASS"
    assert limbs["the read fan-out no longer blocks the write path"]["status"] == "PASS"
    # Still nonzero overall: the other two arms are genuinely absent here.
    assert rc == 1


def test_the_two_write_arms_must_be_different_trees(tmp_path):
    """Scoring the same build twice must be caught, not reported as success."""
    text = EXCERPT.read_text(encoding="utf-8")
    rc, score = _score(tmp_path, text, [_arm("write-with-refresh", True),
                                        _arm("write-without-refresh", True)])
    limbs = {l["limb"]: l for l in score["limbs"]}
    assert limbs["the two write arms ran different trees"]["status"] == "FAIL"


def test_fanout_on_the_writing_thread_still_fails(tmp_path):
    """The causal question: does the fan-out still block OUR write path?

    Feed the real with-refresh cycle in as if it were the without-refresh arm.
    Its EMU reads are on tid548, the same thread that performed the write, so
    the limb must FAIL. This is the discriminator that background polling on
    another thread must NOT trip -- see the next test.
    """
    rc, score = _score(tmp_path, EXCERPT.read_text(encoding="utf-8"),
                       [_arm("write-without-refresh", False)])
    limbs = {l["limb"]: l for l in score["limbs"]}
    limb = limbs["the read fan-out no longer blocks the write path"]
    assert limb["status"] == "FAIL"
    assert "on the writing thread" in limb["detail"]


def test_background_polling_on_another_thread_does_not_fail_the_limb(tmp_path):
    """M2's round 1 failed on two reads that were EMU's own background polling.

    EMU polled at ~4.2/s on M2 regardless of what we did -- 502 retrievals in a
    two-minute idle gap with nothing running. Reassign the excerpt's EMU lines
    to a thread that is not the writer's and the limb must PASS, reporting the
    other-thread count rather than failing on it.
    """
    moved = []
    for line in EXCERPT.read_text(encoding="utf-8").splitlines():
        if "[EMU] -- Retrieved MMProperty" in line:
            line = line.replace("tid548", "tid9999").replace("tid15000", "tid9999")
        moved.append(line)
    rc, score = _score(tmp_path, "\n".join(moved) + "\n",
                       [_arm("write-without-refresh", False)])
    limbs = {l["limb"]: l for l in score["limbs"]}
    limb = limbs["the read fan-out no longer blocks the write path"]
    assert limb["status"] == "PASS", limb["detail"]
    assert "on other threads" in limb["detail"]


# --- the rig-side probe -------------------------------------------------------
# It cannot be run end to end without a bridge, but the half that decides
# `refresh_in_source` is pure source inspection and must be right: the scorer
# voids the whole comparison when the two write arms disagree with the trees
# they actually ran.

probe_spec = importlib.util.spec_from_file_location(
    "probe78a", HERE / "78-block78a-m5-probe.py")
probe = importlib.util.module_from_spec(probe_spec)
probe_spec.loader.exec_module(probe)


def test_probe_reports_this_branch_has_no_per_write_refresh():
    """On this branch the answer must be False -- that is what the block did."""
    assert probe.tree_has_per_write_refresh() is False


def test_probe_would_report_true_for_a_tree_that_still_refreshes(monkeypatch):
    """The control: it must be able to say True, or it is not a discriminator."""
    from microclaw import hook_decisions

    class StillRefreshes:
        def _apply_property(self):
            self.ctrl.refresh_gui()

    monkeypatch.setattr(hook_decisions, "UntrustedHookAdapter", StillRefreshes)
    assert probe.tree_has_per_write_refresh() is True


def test_readback_limb_is_not_exercised_when_the_counter_sees_nothing(tmp_path):
    """A counter that sees no read-backs must not report 'exactly one'.

    The read-back count comes from EMU's own log line, not MMCore's, so it is
    not guaranteed across rigs the way the Core strings are. Strip those lines
    and the limb must say NOT EXERCISED rather than pass on an empty count --
    block 78b's gate shipped exactly this shape and passed a vacuous control.
    """
    text = EXCERPT.read_text(encoding="utf-8")
    without_emu = "\n".join(l for l in text.splitlines()
                            if "[EMU] -- Retrieved MMProperty" not in l) + "\n"
    rc, score = _score(tmp_path, without_emu,
                       [_arm("write-with-refresh", True),
                        _arm("write-without-refresh", False)])
    limbs = {l["limb"]: l for l in score["limbs"]}
    assert limbs["EMU no longer re-reads the target after each write"]["status"] == "NOT EXERCISED"


def test_readback_limb_passes_when_the_counter_demonstrably_works(tmp_path):
    """The control: with real EMU lines present the limb must be able to pass."""
    text = EXCERPT.read_text(encoding="utf-8")
    # The without-refresh arm keeps one read-back of the target and drops the
    # GUI fan-out, which is the post-fix shape.
    rc, score = _score(tmp_path, text, [_arm("write-with-refresh", True),
                                        _arm("write-without-refresh", False)])
    limbs = {l["limb"]: l for l in score["limbs"]}
    assert limbs["EMU no longer re-reads the target after each write"]["status"] != "NOT EXERCISED"
