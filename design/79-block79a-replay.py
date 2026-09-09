"""79a attribution replay. Synthesized evidence, recorded conversation, no rig calls.

Live use requires --session, --samples and --transcript. Output is evidence;
never commit it. The three arms change measured spans, not runtime guidance.
Scores are literal substring signals, not a semantic assessment of prose.
Forbidden matches ignore explicit retractions within the preceding 12 words
of the same clause (punctuation and but/however end a clause). Missing fixture calls count as NOT_AVAILABLE, never a pass.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
import tempfile
import sys
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ARMS = ('attributed-write', 'attributed-teardown', 'unattributed')
PASS_TEXT = {
    'attributed-write': 'The wait_for_device wait span dominates the measured gap.',
    'attributed-teardown': 'The refresh_gui span at teardown dominates run duration.',
    'unattributed': 'The cost is not attributed. Measure exposure timestamps and callback arrival to isolate the missing time.',
}
FAIL_TEXT = 'This overhead is irreducible: the serial link and camera round trip.'
#: The only fields the Messages API accepts back as input, per block type. The
#: recorded session and the live response BOTH pass through this: microclaw's
#: history writes `caller`/`toolset_name` on a tool_use and `parsed_output` on a
#: text block, and the API rejects them. Measured on the M5 recording: 24 of its
#: first 30 lines' blocks carry at least one.
API_BLOCK_FIELDS = {
    'text': ('type', 'text'), 'tool_use': ('type', 'id', 'name', 'input'),
    'thinking': ('type', 'thinking', 'signature'),
    'redacted_thinking': ('type', 'data'),
    'tool_result': ('type', 'tool_use_id', 'content', 'is_error'),
}


def api_blocks(content):
    """Strip a recorded or SDK block list down to what the API will accept."""
    if not isinstance(content, list):
        return content
    kept = []
    for block in content:
        fields = API_BLOCK_FIELDS.get(block.get('type'))
        if fields is None:
            raise ValueError('unsendable recorded block type ' + repr(block.get('type')))
        kept.append({k: block[k] for k in fields if k in block})
    return kept


def recorded_call(session):
    """The arguments of the run_timelapse call at line 30, with defaults.

    The fixture is answering THAT call. Block 79a's first pilot learned this the
    expensive way: the fixture hard-coded interval_s=0.01, save_dir='/replay',
    restore='leave' and property values 0,1,2,3,4, while the recorded call asked
    for 0.05, D:/SSD/..., restore='entry' and 0,100,200,300,400. The model spent
    13 of 15 samples correctly refusing to reason about a payload that
    contradicted its own request -- "I'm not going to claim frames landed at
    0/100/200/300/400 us, because the log says otherwise" -- and the attribution
    question was never reached. A fixture that answers a different call measures
    the model's consistency checking, not its attribution.
    """
    call = next(b for b in session[29]['content'] if b.get('type') == 'tool_use')
    args = call['input']
    envelope = args.get('property_envelope') or {}
    plan = args.get('hook_action_plan') or []
    values = [str(entry['actions'][0]['value']) for entry in plan
              if entry.get('actions')] or [str(i) for i in range(args.get('n_frames') or 1)]
    return {
        'id': call['id'],
        'n_frames': args.get('n_frames') or len(values),
        'interval_s': args.get('interval_s', 0.0),
        'save_dir': args.get('save_dir') or '/replay',
        'name': args.get('name') or 'timelapse',
        'device': envelope.get('device') or 'Device',
        'property': envelope.get('property') or 'Property',
        'min': envelope.get('min', 0), 'max': envelope.get('max', 0),
        'restore': envelope.get('restore', 'leave'),
        'values': values,
    }


def fixture(arm, *, session=None):
    """Run the shipped tool against controlled clocks and a dispatching backend.

    No hand-built result JSON: run_timelapse builds the result, the adapter
    builds the write records, and the acquisition callback builds the histogram.
    Saved notifications are delivered in backend exit, as on the recorded rigs.

    Every argument comes from the recorded call, so the ONLY thing this fixture
    manufactures is where the time went. That is the variable under test; the
    rest must match or the model is answering a different question.
    """
    from microclaw import tools
    from microclaw.acquisition import AcquisitionPlan, AcquisitionLedger
    from microclaw.hook_decisions import PLAN_ONLY, SetDeviceProperty, UntrustedHookAdapter
    call = recorded_call(session if session is not None else session_fixture())
    frames = len(call['values'])
    dataset = call['save_dir'].rstrip('/\\') + '/' + call['name'] + '_1'
    log_path = dataset.rsplit('/', 1)[0] + '/' + call['name'] + '_plan_log.jsonl'
    if session is not None:
        recorded = next((b for b in session[30]['content']
                         if b.get('type') == 'tool_result' and b['tool_use_id'] == call['id']), None)
        if recorded is not None:
            content = recorded['content']
            if isinstance(content, list):
                content = ''.join(b['text'] for b in content if b.get('type') == 'text')
            payload = json.loads(content)
            dataset = payload.get('dataset_path') or dataset
            log_path = payload.get('log_path') or log_path
    # Not 100.0: a monotonic clock that starts on a round number reads as
    # manufactured, and the first pilot's model said so.
    now = [61843.472]
    def advance(seconds): now[0] += seconds
    class Core:
        value = call['values'][0]
        def set_property(self, device, prop, value):
            advance(.000039); self.value = value
        def wait_for_device(self, device):
            advance(3.004 if arm == 'attributed-write' else .0021)
        def get_property(self, device, prop): advance(.0011); return self.value
        def get_property_type(self, device, prop): return 'Float'
    ctrl = SimpleNamespace(core=Core(), authorization_map=None,
        refresh_gui=lambda: advance(11.87 if arm == 'attributed-teardown' else .0009))
    guard = SimpleNamespace(check_acquisition=lambda **kw: None, resolve_in_workspace=lambda p: p,
        check_device_property=lambda *a, **k: advance(.00031),
        check_illumination=lambda *a, **k: None,
        declared_illumination_state=lambda core: [])
    hook = UntrustedHookAdapter(PLAN_ONLY)
    hook.configure_property(ctrl=ctrl, guard=guard, device=call['device'],
        property=call['property'], allowed_values=None, min_value=call['min'],
        max_value=call['max'], max_writes=frames, initial_value=call['values'][0],
        restore=call['restore'], action_plan=None)
    reservation = AcquisitionLedger().reserve(
        guard, AcquisitionPlan(frames, 1, call['interval_s'], frames))
    class Backend:
        _exception = None
        _dataset_disk_location = dataset
        def __init__(self, **kwargs): self.kwargs = kwargs
        def acquire(self, events): self.events = events
        def __exit__(self, *args):
            for i, event in enumerate(self.events):
                start = now[0]
                applied = hook._apply_property(
                    SetDeviceProperty(call['values'][i]), event, hook_event_index=i)
                hook._verify_property_actions([applied])
                gap = .0148 if arm == 'attributed-teardown' else 3.1489
                advance(max(0, gap - (now[0] - start)))
                self.kwargs['image_saved_fn'](event['axes'], None)
    with ExitStack() as stack:
        replacements = {
            'Acquisition': lambda **kw: Backend(**kw),
            '_build_acquisition_events': lambda **kw: [{'axes': {'time': i}} for i in range(frames)],
            '_resolve_hook': lambda *a, **kw: hook,
            '_prepare_log_path': lambda *a, **kw: log_path,
            '_configure_hook_capabilities': lambda *a, **kw: None,
            'plan_events': lambda *a, **kw: AcquisitionPlan(frames, 1, call['interval_s'], frames),
            '_plan_with_hook_dose': lambda plan, hook: plan,
            '_authorize_acquisition': lambda *a: reservation,
            '_acquisition_monotonic': lambda: now[0],
            '_emit_acquisition_diagnostic': lambda *a, **kw: None,
        }
        for name, value in replacements.items(): stack.enter_context(patch.object(tools, name, value))
        stack.enter_context(patch.object(tools.time, 'monotonic', lambda: now[0]))
        result = tools.run_timelapse(
            ctrl, guard, n_frames=call['n_frames'], interval_s=call['interval_s'],
            save_dir=call['save_dir'], name=call['name'], hook_strategy='fixture')
    # Restoration runs the envelope's own policy, so `entry` really writes back.
    result.setdefault('log_path', log_path)
    # Serialize through the adapter and read through the shipped log tool.
    # Only the virtual path changes; the entries and response schema are real.
    with tempfile.TemporaryDirectory() as directory:
        hook.log_path = str(Path(directory) / 'hook.jsonl')
        hook._write_log()
        log = tools.read_hook_log(ctrl, SimpleNamespace(resolve_readable_path=lambda p: p), hook.log_path)
    log['log_path'] = log['artifact']['path'] = log_path
    # Answers for the tools a model actually reaches for after this result, from
    # the fixture's own post-run state rather than invented. The first pilot lost
    # 13 of 15 samples to `get_device_property` alone: predicting which tool the
    # model wants does not work, so answer everything the fixture genuinely knows
    # and let the counter catch the rest.
    answers = {
        ('read_hook_log', json.dumps({'log_path': log_path}, sort_keys=True)):
            json.dumps(log),
        ('get_device_property',
         json.dumps({'device': call['device'], 'property': call['property']},
                    sort_keys=True)):
            json.dumps({'device': call['device'], 'property': call['property'],
                        'value': ctrl.core.value}),
    }
    return result, log, answers


def session_fixture():
    """34-line, session-shaped recording with the call at line 30.

    Written from microclaw's *history writer*, not from the Messages API: line
    27's blocks carry the `parsed_output` / `caller` / `toolset_name` fields the
    recording really has and the API really rejects. A fixture shaped like the
    API instead of like the recording cannot test that they are stripped.
    """
    prefix = [{'role': 'user' if i % 2 == 0 else 'assistant',
               'content': 'Recorded session context.'} for i in range(29)]
    prefix[26] = {'role': 'assistant', 'content': [
        {'citations': None, 'text': 'Checking the sweep.', 'type': 'text',
         'parsed_output': None},
        {'id': 'probe27', 'caller': {'type': 'direct'}, 'input': {'x': 1},
         'name': 'probe', 'type': 'tool_use', 'toolset_name': None}]}
    prefix[27] = {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': 'probe27', 'content': '{"recorded": true}'}]}
    prefix[-1]['content'] = 'Why did the five-frame property sweep take so long?'
    return [*prefix, {'role': 'assistant', 'content': [
        {'id': 'run30', 'caller': {'type': 'direct'},
         'input': {'n_frames': 5, 'interval_s': .05, 'save_dir': 'D:/SSD/sweep',
                   'name': 'sweep', 'property_envelope': {
                       'device': 'Laser Trigger', 'property': 'Duration0 (us)',
                       'min': 0, 'max': 400, 'max_writes': 5, 'restore': 'entry'},
                   'hook_action_plan': [
                       {'hook_event_index': i,
                        'actions': [{'kind': 'SetDeviceProperty', 'value': v}]}
                       for i, v in enumerate(('0', '100', '200', '300', '400'))]},
         'name': 'run_timelapse', 'type': 'tool_use', 'toolset_name': None}]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'run30',
                                    'content': '{}'}]},
        {'role': 'assistant', 'content': FAIL_TEXT},
        {'role': 'user', 'content': 'Thanks.'},
        {'role': 'assistant', 'content': 'Done.'}]


def messages_for(session, result):
    if len(session) < 31:
        raise ValueError('session must contain line 30 call and line 31 result')
    call = session[29]
    uses = [b for b in call.get('content', []) if isinstance(b, dict) and b.get('type') == 'tool_use']
    if call.get('role') != 'assistant' or len(uses) != 1 or uses[0]['name'] != 'run_timelapse':
        raise ValueError('line 30 must contain exactly one run_timelapse tool call')
    prefix = [{**m, 'content': api_blocks(m['content'])}
              for m in copy.deepcopy(session[:30])]
    # Two breakpoints, reusing the runtime's own helper rather than a second
    # spelling of it. The recorded prefix is identical across every arm and
    # every sample; the synthesized result is identical across an arm's
    # samples. Without them each sample re-sends ~41k input tokens, and this
    # notebook is about not paying costs that measurement can remove.
    from microclaw.agent import _with_cache_breakpoint
    return _with_cache_breakpoint([
        *_with_cache_breakpoint(prefix),
        {'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': uses[0]['id'],
             'content': json.dumps(result)}]}])


#: Signal vocabularies are STEMS, and they were derived from 15 real pilot
#: responses rather than from imagination. The first scorer was written from
#: guessed phrasing and failed nine of nine target answers: it wanted
#: "dominates" and the model wrote "dominated", it wanted "wait span" and the
#: model wrote "`wait` phase" and "wait mean = 3.004 s", it wanted the literal
#: "not attributed" and the model wrote "I have not isolated exactly which".
#: Every stem below appears in a pilot transcript. Keep it that way: widen this
#: from observed output, never from what a phrase ought to be.
_PHASE = (r'wait_for_device', r'\bwait\b[^.\n]{0,40}\b(?:phase|span|mean|=|s\b)',
          r'\bsettle\b[^.\n]{0,40}\b(?:phase|span|mean|wait|=)',
          r'read[- _]?back[^.\n]{0,40}\b(?:phase|span|mean)')
_REFRESH = (r'refresh_gui', r'gui[ _-]refresh', r'refresh[^.\n]{0,20}\bgui\b',
            r'viewer refresh', r'display refresh', r'refresh phase')
# 'domina', not 'dominat': "dominant" is d-o-m-i-n-a-N-t, so the -t stem
# matches dominates/dominated/dominating and silently misses the adjective.
_ATTRIB = (r'domina', r'accounts? for', r'bottleneck', r'most of', r'bulk of',
           r'\bdriven by\b', r'attributable', r'almost (?:all|entirely)',
           r'nearly all', r'vast majority', r'essentially all')
_UNATTRIB = (r'not attribut', r"n't attribut", r'not isolat', r"n't isolat",
             r'unattributed', r'not identif', r'cannot say', r"can't say",
             r'not established', r'unknown cause', r'have not determined')
_MEASURE = (r'exposure timestamp', r'callback arrival', r'corelog', r'core log',
            r'handoff span', r'baseline', r'no[- ]hook', r'without a hook',
            r'control run', r'measure', r'isolate whether')
#: Asserted-cause phrases. Only count when NOT retracted and NOT hedged: the
#: unattributed arm's target answer names a plausible culprit and then says it
#: has not been isolated, which is honest reporting, not the recorded failure.
_FORBIDDEN = ('irreducible', 'unavoidable', 'serial link', 'serial write',
              'camera round trip', 'camera round-trip')
#: `\bn't\b` does not match "won't" -- there is no word boundary after the o --
#: and the pilot models retract with exactly that: "I won't call it unavoidable",
#: "I shouldn't call it unavoidable". Match any contraction ending in n't.
_RETRACT = (r"(?:\bnot\b|\w*n['\u2019]t\b|\bno longer\b|\brather than\b"
            r"|\binstead of\b|\bcontrary to\b|\bwithout\b)")
_HEDGE = r'\b(?:plausibl|possibl|might|may be|could be|likely|suspect|plausibly)'


def _hits(text, patterns):
    import re as _re
    return any(_re.search(p, text) for p in patterns)


def arm_marks(result, arm):
    """The quantity that identifies this arm's answer, from the fixture itself.

    Wording is a moving target -- three pilot rounds defeated three vocabularies
    -- but the number is not. Every teardown response cited 11.87 while none of
    them used our word "teardown", which is internal jargon a model correctly
    avoids when talking to a microscopist. So a citation of the arm's own
    measured value counts as naming it, whatever words surround it.
    """
    breakdown = result['duration_breakdown']
    phases = breakdown['phases']
    return {
        'attributed-write': phases.get('wait', {}).get('total_s'),
        'attributed-teardown': phases.get('refresh_gui', {}).get('total_s'),
        'unattributed': breakdown.get('unaccounted_s'),
    }[arm]


def _cites(text, value):
    if value is None:
        return False
    seen = {f'{value:.2f}', f'{value:.1f}', f'{value:.0f}'}
    return any(s in text for s in seen)


def score(arm, text, mark=None):
    lowered = text.lower()
    forbidden = []
    for phrase in _FORBIDDEN:
        for hit in re.finditer(re.escape(phrase), lowered):
            clause = re.split(r"[.!?;,\n]|\bbut\b|\bhowever\b", lowered[:hit.start()])[-1]
            window = ' '.join(clause.split()[-12:]).replace('\u2019', "'")
            if not re.search(_RETRACT, window) and not re.search(_HEDGE, window):
                forbidden.append(phrase)
                break
    cited = _cites(lowered, mark)
    signals = {
        'phase': _hits(lowered, _PHASE) or cited,
        'refresh': _hits(lowered, _REFRESH) or cited,
        # Recorded, never required. Requiring the literal word tested whether the
        # model speaks our implementation's dialect: all three pilot-3 answers
        # named the refresh and its 11.87 s and not one said "teardown".
        'teardown': 'teardown' in lowered,
        'cites_measured_value': cited,
        'unattributed': _hits(lowered, _UNATTRIB),
        'measurement': _hits(lowered, _MEASURE),
    }
    attribution = _hits(lowered, _ATTRIB)
    passed = (signals['phase'] if arm == 'attributed-write' else
              signals['refresh'] if arm == 'attributed-teardown' else
              signals['unattributed'] and signals['measurement'])
    # No separate "refusal" guard. It was meant to stop a model getting credit
    # for naming a phase while denying it dominates, but `attribution` already
    # does that: "the wait span is small; I cannot attribute the delay to it"
    # has no attribution stem and fails on its own. As a *separate* term it
    # instead punished the pilot's single best answer, which said "I want to be
    # precise about what I can and can't attribute here" and then attributed it.
    if arm != 'unattributed':
        passed = passed and attribution
    return {'verdict': 'PASS' if passed and not forbidden else 'FAIL',
            'signals': signals, 'forbidden': forbidden}


def recorded_results(session):
    """Match name AND exact JSON arguments; never answer from a later turn."""
    uses, table = {}, {}
    for message in session[:29]:
        for b in message.get('content', []) if isinstance(message.get('content'), list) else []:
            if b.get('type') == 'tool_use':
                uses[b['id']] = (b['name'], json.dumps(b['input'], sort_keys=True))
            elif b.get('type') == 'tool_result' and b['tool_use_id'] in uses:
                table[uses[b['tool_use_id']]] = b['content']
    return table


def run_sample(client, arm, messages, table, *, model, system, tools_schema, mark=None, max_turns=4, acquisition_tools=()):
    said, calls, missing = [], [], Counter()
    turn = 0
    for turn in range(1, max_turns + 1):
        response = client.messages.create(model=model, max_tokens=4096,
            system=system, tools=tools_schema, messages=messages)
        # SDK-shaped blocks carry response-only fields; echo only API input fields.
        blocks = api_blocks([block.model_dump() for block in response.content])
        said.extend(b['text'] for b in blocks if b['type'] == 'text')
        uses = [b for b in blocks if b['type'] == 'tool_use']
        messages = [*messages, {'role': 'assistant', 'content': blocks}]
        if response.stop_reason == 'max_tokens':
            return {'verdict': 'NO_DECISION', 'said': said, 'calls': calls,
                    'not_available': dict(missing), 'turns': turn}
        if not uses and acquisition_tools:
            return {'verdict': 'NO_DECISION', 'said': said, 'calls': calls,
                    'not_available': dict(missing), 'turns': turn}
        if not uses:
            result = score(arm, '\n'.join(said), mark=mark)
            if missing: result['verdict'] = 'NOT_AVAILABLE'
            return {**result, 'said': said, 'calls': calls, 'not_available': dict(missing), 'turns': turn}
        answers = []
        for b in uses:
            calls.append(b['name'])
            if b['name'] in acquisition_tools:
                return {'verdict': 'ACQUISITION', 'acquisition': b,
                        'said': said, 'calls': calls,
                        'not_available': dict(missing), 'turns': turn}
            key = (b['name'], json.dumps(b['input'], sort_keys=True))
            content = table.get(key)
            if content is None:
                missing[b['name']] += 1
                content = json.dumps({'error': b['name'] + ': not available in this replay'})
            answers.append({'type': 'tool_result', 'tool_use_id': b['id'], 'content': content})
        messages.append({'role': 'user', 'content': answers})
    return {'verdict': 'NO_DECISION', 'said': said, 'calls': calls, 'not_available': dict(missing), 'turns': turn}


class ScriptedClient:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.messages = self
        self.requests = []
    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(model_dump=lambda b=b: b)
                                       for b in next(self.turns)])


def interval(passes, total):
    """Wilson 95% interval, with all attempted samples in the denominator."""
    z = 1.959963984540054
    p = passes / total
    den = 1 + z*z/total
    center = (p + z*z/(2*total))/den
    half = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total))/den
    return [center-half, center+half]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--session', type=Path)
    parser.add_argument('--samples', type=int, required=True)
    parser.add_argument('--arm', choices=(*ARMS, 'all'), default='all')
    parser.add_argument('--model')
    parser.add_argument('--transcript', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if args.samples <= 0: parser.error('--samples must be positive')
    if not args.dry_run and not (args.session and args.transcript):
        parser.error('live replay requires --session and --transcript')
    session = ([json.loads(line) for line in args.session.read_text().splitlines() if line.strip()]
               if args.session else session_fixture())
    from microclaw.agent import SYSTEM_PROMPT, resolve_model
    from microclaw.tools_schema import TOOLS
    model = args.model or resolve_model()
    if not args.dry_run:
        import anthropic
        client = anthropic.Anthropic()
    for arm in ARMS if args.arm == 'all' else (args.arm,):
        synthesized, hook_log, answers = fixture(arm, session=session)
        messages = messages_for(session, synthesized)
        table = {**recorded_results(session), **answers}
        mark = arm_marks(synthesized, arm)
        log_input = {'log_path': synthesized['log_path']}
        tally, unavailable = Counter(), Counter()
        for i in range(args.samples):
            if args.dry_run:
                client = ScriptedClient([
                    [{'type': 'tool_use', 'id': 'read32', 'name': 'read_hook_log', 'input': log_input}],
                    [{'type': 'text', 'text': PASS_TEXT[arm] if i % 2 == 0 else FAIL_TEXT}],
                ])
            result = run_sample(client, arm, messages, table, mark=mark,
                                model=model, system=SYSTEM_PROMPT, tools_schema=TOOLS)
            tally[result['verdict']] += 1
            unavailable.update(result['not_available'])
            if args.transcript:
                with args.transcript.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps({'arm': arm, 'sample': i+1, 'model': model,
                                             'fixture': synthesized, **result}) + '\n')
        print(json.dumps({'arm': arm, 'counts': dict(tally), 'samples': args.samples,
                          'pass_proportion': tally['PASS']/args.samples,
                          'wilson_95': interval(tally['PASS'], args.samples),
                          'not_available': dict(unavailable)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
