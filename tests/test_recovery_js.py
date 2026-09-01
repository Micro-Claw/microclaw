"""Execute the browser recovery reconciler's pure functions under node."""
import json
import shutil
import subprocess
from importlib import resources

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def reconcile(payload, *, confirm_id=None, grant_ids=(), turn_id=None):
    path = resources.files("microclaw").joinpath("recovery.js")
    script = (
        "global.window = {};\n"
        f"require({json.dumps(str(path))});\n"
        "const calls = [];\n"
        "const recovery = window.Recovery.create({\n"
        f"  fetch: async (url) => ({{ status: 200, ok: true, json: async () => ({json.dumps(payload)}) }}),\n"
        "  now: () => 1234,\n"
        "  setTimeout: () => 1,\n"
        "  clearTimeout: () => {},\n"
        "  isVisible: () => true,\n"
        f"  getConfirmId: () => {json.dumps(confirm_id)},\n"
        f"  getGrantIds: () => {json.dumps(list(grant_ids))},\n"
        "  showGrants: (grants) => calls.push(['grants', grants]),\n"
        "  showConfirm: (pending) => calls.push(['confirm', pending]),\n"
        "  hideConfirm: (id, decision) => calls.push(['hide', id, decision]),\n"
        "  showRemaining: (seconds) => calls.push(['remaining', seconds]),\n"
        "  notePollFailure: () => {},\n"
        "  needsPairing: () => {},\n"
        "});\n"
        f"recovery.setTurnId({json.dumps(turn_id)});\n"
        "recovery.reconcileConfirmation().then((state) => {\n"
        "  process.stdout.write(JSON.stringify({ calls, state, now: recovery.now() }));\n"
        "});\n"
    )
    return json.loads(subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    ).stdout)


def test_boot_reconciliation_renders_grants_and_pending_confirmation():
    pending = {"id": "c1", "summary": "Proceed?", "grants": [{"id": "g1"}]}
    result = reconcile(pending)
    assert result == {
        "calls": [["grants", [{"id": "g1"}]], ["confirm", pending]],
        "state": pending,
        "now": 1234,
    }


def test_boot_reconciliation_with_no_pending_confirmation_is_a_no_op_when_empty():
    state = {"grants": []}
    result = reconcile(state)
    assert result["calls"] == []
    assert result["state"] == state


def test_same_pending_id_and_grant_ids_are_reconciliation_no_ops():
    state = {"id": "c1", "grants": [{"id": "g1"}, {"id": "g2"}]}
    assert reconcile(state, confirm_id="c1", grant_ids=("g1", "g2"))["calls"] == []


def test_missing_pending_confirmation_hides_the_rendered_banner():
    result = reconcile({"grants": []}, confirm_id="c1")
    assert result["calls"] == [["hide", "c1", None]]


def test_reload_adopts_the_server_turn_and_matches_resolution_by_both_ids():
    state = {
        "grants": [], "turn_id": "turn-1",
        "last_resolution": {"id": "c1", "turn_id": "turn-1", "decision": "declined"},
    }
    assert reconcile(state)["calls"] == [["hide", "c1", "declined"]]
    assert reconcile(state, confirm_id="other")["calls"] == [["hide", "other", None]]
    assert reconcile(state, confirm_id="c1", turn_id="other-turn")["calls"] == [
        ["hide", "c1", None],
    ]


def test_no_pending_id_hides_a_stale_banner_despite_a_nonmatching_resolution():
    state = {
        "grants": [], "turn_id": "turn-2",
        "last_resolution": {"id": "old", "turn_id": "turn-1", "decision": "approved"},
    }
    assert reconcile(state, confirm_id="stale", turn_id="turn-2")["calls"] == [
        ["hide", "stale", None],
    ]


def test_server_resolution_not_the_local_countdown_labels_the_outcome():
    state = {
        "grants": [], "turn_id": "turn-1",
        "last_resolution": {
            "id": "c1", "turn_id": "turn-1", "decision": "approved",
        },
    }
    assert reconcile(state, confirm_id="c1")["calls"] == [["hide", "c1", "approved"]]


def test_pending_deadline_is_rendered_as_informational_seconds():
    state = {"id": "c1", "grants": [], "turn_id": "turn-1", "remaining_s": 4.2}
    assert reconcile(state, turn_id="turn-1")["calls"] == [
        ["confirm", state], ["remaining", 5],
    ]


def run_node(body):
    path = resources.files("microclaw").joinpath("recovery.js")
    script = (
        "global.window = {};\n"
        f"require({json.dumps(str(path))});\n"
        "(async () => {\n" + body + "\n})().catch(e => { console.error(e); process.exit(1); });\n"
    )
    return json.loads(subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    ).stdout)


BASE_OPTIONS = """
    now: () => 0,
    setTimeout: (fn, delay) => { timers.push({fn, delay}); return timers.length; },
    clearTimeout: () => {},
    isVisible: () => visible,
    getConfirmId: () => null,
    getGrantIds: () => [],
    showGrants: () => {}, showConfirm: () => {}, hideConfirm: () => {},
    showRemaining: () => {},
    notePollFailure: (count) => failures.push(count),
    needsPairing: () => { pairing += 1; },
"""


def test_slow_polls_never_overlap():
    result = run_node(f"""
      let calls = 0, resolveFetch, visible = true, pairing = 0;
      const timers = [], failures = [];
      const fetch = () => {{ calls += 1; return new Promise(resolve => {{ resolveFetch = resolve; }}); }};
      const recovery = window.Recovery.create({{{BASE_OPTIONS} fetch}});
      recovery.startConfirmationRecovery();
      await recovery.pollConfirmation();
      const whilePending = calls;
      resolveFetch({{status: 200, ok: true, json: async () => ({{grants: []}})}});
      await new Promise(setImmediate);
      recovery.stopConfirmationRecovery();
      process.stdout.write(JSON.stringify({{whilePending, calls}}));
    """)
    assert result == {"whilePending": 1, "calls": 1}


def test_401_stops_without_a_retry_or_final_fetch():
    result = run_node(f"""
      let calls = 0, visible = true, pairing = 0;
      const timers = [], failures = [];
      const fetch = async () => {{ calls += 1; return {{status: 401, ok: false}}; }};
      const recovery = window.Recovery.create({{{BASE_OPTIONS} fetch}});
      recovery.startConfirmationRecovery();
      await new Promise(setImmediate);
      recovery.resumeConfirmationRecovery();
      await recovery.pollConfirmation();
      process.stdout.write(JSON.stringify({{calls, pairing, timers: timers.length}}));
    """)
    assert result == {"calls": 1, "pairing": 1, "timers": 0}


def test_hidden_poll_suspends_and_visible_event_resumes_immediately():
    result = run_node(f"""
      let calls = 0, visible = false, pairing = 0;
      const timers = [], failures = [];
      const fetch = async () => {{ calls += 1; return {{status: 200, ok: true,
        json: async () => ({{grants: []}})}}; }};
      const recovery = window.Recovery.create({{{BASE_OPTIONS} fetch}});
      recovery.startConfirmationRecovery();
      await new Promise(setImmediate);
      const hiddenCalls = calls;
      visible = true;
      recovery.resumeConfirmationRecovery();
      await new Promise(setImmediate);
      recovery.stopConfirmationRecovery();
      process.stdout.write(JSON.stringify({{hiddenCalls, calls}}));
    """)
    assert result == {"hiddenCalls": 0, "calls": 1}


def test_failures_warn_and_back_off_without_touching_banner_callbacks():
    result = run_node(f"""
      let visible = true, pairing = 0;
      const timers = [], failures = [], scheduled = [];
      const fetch = async () => ({{status: 503, ok: false}});
      const recovery = window.Recovery.create({{{BASE_OPTIONS} fetch,
        setTimeout: (fn, delay) => {{ scheduled.push(delay); timers.push({{fn, delay}}); return timers.length; }}
      }});
      recovery.startConfirmationRecovery();
      for (let i = 0; i < 3; i += 1) {{
        await new Promise(setImmediate);
        const timer = timers.pop();
        if (timer) await timer.fn();
      }}
      recovery.stopConfirmationRecovery();
      process.stdout.write(JSON.stringify({{failures, scheduled}}));
    """)
    assert result["failures"][:3] == [1, 2, 3]
    assert result["scheduled"][:3] == [1000, 1000, 2000]


def test_polling_only_reads_confirmation_state_and_never_posts_a_decision():
    result = run_node(f"""
      let visible = true, pairing = 0;
      const timers = [], failures = [], requests = [];
      const fetch = async (...args) => {{ requests.push(args); return {{status: 200, ok: true,
        json: async () => ({{grants: []}})}}; }};
      const recovery = window.Recovery.create({{{BASE_OPTIONS} fetch}});
      recovery.startConfirmationRecovery();
      await new Promise(setImmediate);
      recovery.stopConfirmationRecovery();
      process.stdout.write(JSON.stringify(requests));
    """)
    assert result == [["/api/confirm"]]
