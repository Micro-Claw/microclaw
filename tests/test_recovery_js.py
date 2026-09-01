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


def test_reload_boot_starts_polling_while_running_and_stops_when_turn_settles():
    result = run_node(f"""
      let visible = true, pairing = 0, calls = 0, clock = 0;
      const timers = [], failures = [];
      const states = [
        {{grants: [], running: true, turn_id: 'turn-reloaded'}},
        {{grants: [], running: true, turn_id: 'turn-reloaded'}},
        {{grants: [], running: false, turn_id: 'turn-reloaded'}},
      ];
      const fetch = async () => {{ calls += 1; return {{status: 200, ok: true,
        json: async () => states.shift()}}; }};
      const recovery = window.Recovery.create({{{BASE_OPTIONS} fetch,
        now: () => clock,
        setTimeout: (fn, delay) => {{ timers.push({{fn, delay}}); return timers.length; }},
      }});
      const bootState = await recovery.startFromBoot();
      await new Promise(setImmediate);
      const afterBoot = calls;
      clock += 1000;
      const timer = timers.pop();
      await timer.fn();
      await new Promise(setImmediate);
      process.stdout.write(JSON.stringify({{
        afterBoot, calls, timers: timers.length, adopted: bootState.turn_id
      }}));
    """)
    assert result == {
        "afterBoot": 2,
        "calls": 3,
        "timers": 0,
        "adopted": "turn-reloaded",
    }


def test_reload_boot_with_no_running_turn_starts_no_poll_loop():
    result = run_node(f"""
      let visible = true, pairing = 0, calls = 0;
      const timers = [], failures = [];
      const fetch = async () => {{ calls += 1; return {{status: 200, ok: true,
        json: async () => ({{grants: [], running: false}})}}; }};
      const recovery = window.Recovery.create({{{BASE_OPTIONS} fetch}});
      await recovery.startFromBoot();
      await new Promise(setImmediate);
      process.stdout.write(JSON.stringify({{calls, timers: timers.length}}));
    """)
    assert result == {"calls": 1, "timers": 0}


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


def test_complete_comment_frame_counts_as_activity_but_partial_bytes_do_not():
    result = run_node("""
      let controller, frames = 0;
      const body = new ReadableStream({start(value) { controller = value; }});
      const events = window.Recovery.sseEvents(body, () => { frames += 1; });
      const pending = events.next();
      controller.enqueue(new TextEncoder().encode(': pi'));
      await new Promise(setImmediate);
      const afterPartial = frames;
      controller.enqueue(new TextEncoder().encode('ng\\n\\n'));
      await new Promise(setImmediate);
      const afterComment = frames;
      controller.enqueue(new TextEncoder().encode(
        'data:{"type":"round_start"}\\n\\n'));
      const event = await pending;
      controller.close();
      process.stdout.write(JSON.stringify({afterPartial, afterComment, frames, event}));
    """)
    assert result == {
        "afterPartial": 0,
        "afterComment": 1,
        "frames": 2,
        "event": {"value": {"type": "round_start"}, "done": False},
    }


def test_stream_silent_from_its_first_byte_is_armed_and_settles_once():
    result = run_node(f"""
      let visible = true, pairing = 0, silent = 0, settled = 0, nextId = 0;
      const timers = [], failures = [], cleared = [];
      const fetch = async () => ({{status: 200, ok: true,
        json: async () => ({{grants: [], running: false}})}});
      const recovery = window.Recovery.create({{{BASE_OPTIONS} fetch,
        setTimeout: (fn, delay) => {{
          const timer = {{id: ++nextId, fn, delay}}; timers.push(timer); return timer.id;
        }},
        clearTimeout: (id) => cleared.push(id),
        noteStreamSilence: () => {{ silent += 1; }},
        settleRecovered: () => {{ settled += 1; }},
      }});
      recovery.armSilenceTimer();
      const armedAtStart = timers[0].delay;
      timers[0].fn();
      recovery.markStreamSilent();
      const didSettle = recovery.settleIfRecovered({{running: false}});
      const didSettleTwice = recovery.settleIfRecovered({{running: false}});
      process.stdout.write(JSON.stringify({{
        armedAtStart, silent, settled, didSettle, didSettleTwice
      }}));
    """)
    assert result == {
        "armedAtStart": 30000,
        "silent": 1,
        "settled": 1,
        "didSettle": True,
        "didSettleTwice": False,
    }


def test_pending_text_priority_includes_authoritative_resolution_disclosure():
    html = resources.files("microclaw").joinpath("serve.html").read_text(
        encoding="utf-8"
    )
    start = html.index("  let pendingConfirmation = null;")
    end = html.index("\n\n  const confirmationRecovery", start)
    resolver = html[start:end]
    result = run_node(f"""
      const pendingText = {{textContent: ''}};
      function $(id) {{
        if (id !== 'pending-text') throw new Error(`unexpected element ${{id}}`);
        return pendingText;
      }}
      {resolver}
      const cases = {{}};
      pendingProgress = 'frames 700 / 700';
      pendingResolution = 'Confirmation timed out and was declined';
      renderPendingText(); cases.staleProgressTimeout = pendingText.textContent;
      streamSilenceDetected = true;
      renderPendingText(); cases.silenceTimeout = pendingText.textContent;
      pendingConfirmation = 'Waiting for your confirmation.';
      renderPendingText(); cases.confirmationWins = pendingText.textContent;
      pendingConfirmation = null; streamSilenceDetected = false;
      renderPendingText(); cases.timeoutAlone = pendingText.textContent;
      pendingResolution = null; streamSilenceDetected = true;
      renderPendingText(); cases.silenceProgress = pendingText.textContent;
      streamSilenceDetected = false;
      renderPendingText(); cases.progressDefault = pendingText.textContent;
      process.stdout.write(JSON.stringify(cases));
    """)
    assert result == {
        "staleProgressTimeout": "Confirmation timed out and was declined",
        "silenceTimeout": "Confirmation timed out and was declined",
        "confirmationWins": "Waiting for your confirmation.",
        "timeoutAlone": "Confirmation timed out and was declined",
        "silenceProgress": "Live updates interrupted; checking Microclaw…",
        "progressDefault": "frames 700 / 700",
    }
    assert html.count('$("pending-text").textContent =') == 1


def browser_turn_snippets():
    html = resources.files("microclaw").joinpath("serve.html").read_text(
        encoding="utf-8"
    )
    set_busy_start = html.index("  function setBusy(on)")
    set_busy_end = html.index("\n\n  // ---- stop ----", set_busy_start)
    recovery_start = html.index("  let confirmId = null;")
    recovery_end = html.index("\n\n  function showGrants", recovery_start)
    turn_start = html.index("  async function runTurn(text)")
    turn_end = html.index("\n\n  document.addEventListener", turn_start)
    submit_start = html.index('  $("composer").addEventListener("submit"')
    submit_end = html.index('\n\n  $("expand")', submit_start)
    return "\n".join((
        html[set_busy_start:set_busy_end],
        html[recovery_start:recovery_end],
        html[turn_start:turn_end],
        html[submit_start:submit_end],
    ))


def run_browser_turn(*, unrelated_abort=False):
    path = resources.files("microclaw").joinpath("recovery.js")
    snippets = browser_turn_snippets()
    body_failure = "true" if unrelated_abort else "false"
    script = f"""
      global.window = {{}};
      require({json.dumps(str(path))});
      global.Recovery = window.Recovery;
      const elements = new Map(), listeners = {{}}, timers = new Map();
      let nextTimer = 0, toasts = [], refreshes = 0, paints = 0;
      function element(id) {{
        if (!elements.has(id)) {{
          const classes = new Set(['hidden']);
          elements.set(id, {{
          value: '', disabled: false, textContent: '', dataset: {{}},
          classList: {{
            toggle(name, force) {{ force ? classes.add(name) : classes.delete(name); }},
            add(name) {{ classes.add(name); }}, remove(name) {{ classes.delete(name); }},
            contains(name) {{ return classes.has(name); }}
          }},
          focus() {{}}, querySelectorAll() {{ return []; }},
          addEventListener(type, fn) {{ listeners[id + ':' + type] = fn; }},
          }});
        }}
        return elements.get(id);
      }}
      function $(id) {{ return element(id); }}
      const msg = element('msg'), send = element('send');
      let busy = false, hasKey = true, history = [];
      function toast(value) {{ toasts.push(value); }}
      function autogrow() {{}}
      function paint() {{ paints += 1; }}
      function schedulePaint() {{}}
      function cancelScheduledPaint() {{}}
      function applyEvent() {{}}
      function showGrants() {{}}
      function showConfirm() {{}}
      function hideConfirm() {{}}
      function scrollToBottom() {{}}
      async function detail() {{ return 'detail'; }}
      async function refresh() {{ refreshes += 1; }}
      window.setTimeout = (fn, delay) => {{ const id = ++nextTimer; timers.set(id, {{fn, delay}}); return id; }};
      window.clearTimeout = (id) => timers.delete(id);
      global.document = {{
        visibilityState: 'visible',
        addEventListener() {{}},
      }};
      let readReject;
      const body = {{
        pipeThrough() {{
          return {{getReader() {{
            return {{read() {{
              if ({body_failure}) return Promise.reject(
                new DOMException('lost', 'AbortError'));
              return new Promise((_resolve, reject) => {{ readReject = reject; }});
            }}}};
          }}}};
        }}
      }};
      async function apiFetch(url, options = {{}}) {{
        if (url === '/api/confirm') return {{status: 200, ok: true,
          json: async () => ({{grants: [], running: false}})}};
        options.signal.addEventListener('abort', () =>
          readReject?.(new DOMException('recovered', 'AbortError')));
        return {{ok: true, headers: {{get: () => 'turn-1'}}, body}};
      }}
      {snippets}
      msg.value = 'do not duplicate';
      const submission = listeners['composer:submit']({{preventDefault() {{}}}});
      await new Promise(setImmediate);
      if (!{body_failure}) {{
        const silence = [...timers.values()].find(timer => timer.delay === 30000);
        silence.fn();
        await confirmationRecovery.reconcileConfirmation();
      }}
      await submission;
      process.stdout.write(JSON.stringify({{
        sendDisabled: send.disabled, msgDisabled: msg.disabled, msg: msg.value,
        pendingHidden: element('pending').classList.contains('hidden'),
        toasts, refreshes, paints
      }}));
    """
    wrapper = "(async () => {\n" + script + "\n})().catch(e => { console.error(e); process.exit(1); });"
    result = subprocess.run(
        ["node", "-e", wrapper], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_recovered_abort_reenables_composer_without_restoring_prompt_or_toast():
    result = run_browser_turn()
    assert result["sendDisabled"] is False
    assert result["msgDisabled"] is False
    assert result["msg"] == ""
    assert result["pendingHidden"] is True
    assert result["toasts"] == []
    assert result["refreshes"] == 1


def test_unrelated_abort_restores_prompt_and_is_not_false_success():
    result = run_browser_turn(unrelated_abort=True)
    assert result["sendDisabled"] is False
    assert result["msg"] == "do not duplicate"
    assert result["toasts"] == ["Request failed: lost"]
    assert result["refreshes"] == 1
