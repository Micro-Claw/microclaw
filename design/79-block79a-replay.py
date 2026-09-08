"""79a attribution replay. Synthesized evidence, recorded conversation, no rig calls.

Live use requires --session, --samples and --transcript. Output is evidence;
never commit it. The three arms change measured spans, not runtime guidance.
Scores are literal substring signals, not a semantic assessment of prose.
Forbidden phrases also match negations: inspect transcripts before interpreting
counts. Missing fixture calls count as NOT_AVAILABLE, never a pass.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
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


def fixture(arm):
    """Run the shipped tool against controlled clocks and a dispatching backend.

    No hand-built result JSON: run_timelapse builds the result, the adapter
    builds the write records, and the acquisition callback builds the histogram.
    Saved notifications are delivered in backend exit, as on the recorded rigs.
    """
    from microclaw import tools
    from microclaw.acquisition import AcquisitionPlan, AcquisitionLedger
    from microclaw.hook_decisions import PLAN_ONLY, SetDeviceProperty, UntrustedHookAdapter
    now = [100.0]
    def advance(seconds): now[0] += seconds
    class Core:
        value = '0'
        def set_property(self, device, prop, value):
            advance(.00004); self.value = value
        def wait_for_device(self, device):
            advance(3.0 if arm == 'attributed-write' else .002)
        def get_property(self, device, prop): advance(.001); return self.value
        def get_property_type(self, device, prop): return 'Float'
    ctrl = SimpleNamespace(core=Core(), authorization_map=None,
        refresh_gui=lambda: advance(12 if arm == 'attributed-teardown' else .001))
    guard = SimpleNamespace(check_acquisition=lambda **kw: None, resolve_in_workspace=lambda p: p,
        check_device_property=lambda *a, **k: advance(.0003),
        check_illumination=lambda *a, **k: None,
        declared_illumination_state=lambda core: [])
    hook = UntrustedHookAdapter(PLAN_ONLY)
    hook.configure_property(ctrl=ctrl, guard=guard, device='Laser Trigger',
        property='Duration0 (us)', allowed_values=None, min_value=0,
        max_value=10, max_writes=5, initial_value='0', restore='leave', action_plan=None)
    reservation = AcquisitionLedger().reserve(guard, AcquisitionPlan(5, 1, .01, 5))
    class Backend:
        _exception = None
        _dataset_disk_location = '/replay/synthesized'
        def __init__(self, **kwargs): self.kwargs = kwargs
        def acquire(self, events): self.events = events
        def __exit__(self, *args):
            for i, event in enumerate(self.events):
                start = now[0]
                applied = hook._apply_property(SetDeviceProperty(str(i)), event, hook_event_index=i)
                hook._verify_property_actions([applied])
                gap = .015 if arm == 'attributed-teardown' else 3.15
                advance(max(0, gap - (now[0] - start)))
                self.kwargs['image_saved_fn'](event['axes'], None)
    with ExitStack() as stack:
        replacements = {
            'Acquisition': lambda **kw: Backend(**kw),
            '_build_acquisition_events': lambda **kw: [{'axes': {'time': i}} for i in range(5)],
            '_resolve_hook': lambda *a, **kw: hook,
            '_prepare_log_path': lambda *a, **kw: None,
            '_configure_hook_capabilities': lambda *a, **kw: None,
            'plan_events': lambda *a, **kw: AcquisitionPlan(5, 1, .01, 5),
            '_plan_with_hook_dose': lambda plan, hook: plan,
            '_authorize_acquisition': lambda *a: reservation,
            '_acquisition_monotonic': lambda: now[0],
            '_emit_acquisition_diagnostic': lambda *a, **kw: None,
        }
        for name, value in replacements.items(): stack.enter_context(patch.object(tools, name, value))
        stack.enter_context(patch.object(tools.time, 'monotonic', lambda: now[0]))
        return tools.run_timelapse(ctrl, guard, n_frames=5, interval_s=.01,
                                  save_dir='/replay', hook_strategy='fixture')


def session_fixture():
    """34-line, session-shaped recording with the call at line 30."""
    prefix = [{'role': 'user' if i % 2 == 0 else 'assistant',
               'content': 'Recorded session context.'} for i in range(29)]
    prefix[-1]['content'] = 'Why did the five-frame property sweep take so long?'
    return [*prefix, {'role': 'assistant', 'content': [
        {'type': 'tool_use', 'id': 'run30', 'name': 'run_timelapse',
         'input': {'n_frames': 5, 'interval_s': .01, 'save_dir': '/replay'}}]},
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
    return [*copy.deepcopy(session[:30]), {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': uses[0]['id'], 'content': json.dumps(result)}]}]


def score(arm, text):
    lowered = text.lower()
    forbidden = [s for s in ('irreducible', 'serial link', 'serial write', 'camera round trip',
                              'camera round-trip') if s in lowered]
    signals = {
        'phase': any(s in lowered for s in ('wait_for_device', 'wait span', 'settle span')),
        'refresh': any(s in lowered for s in ('refresh_gui', 'gui refresh')),
        'teardown': 'teardown' in lowered,
        'unattributed': 'not attributed' in lowered,
        'measurement': any(s in lowered for s in ('exposure timestamp', 'callback arrival',
                                                   'corelog', 'core log', 'handoff span')),
    }
    attribution = any(s in lowered for s in ('dominates', 'dominant', 'accounts for',
                                             'bottleneck', 'most of', 'spent'))
    refusal = any(s in lowered for s in ('cannot attribute', 'can’t attribute',
                                         "can't attribute", 'not attributed',
                                         'not dominant', 'does not dominate'))
    passed = (signals['phase'] if arm == 'attributed-write' else
              signals['refresh'] and signals['teardown'] if arm == 'attributed-teardown' else
              signals['unattributed'] and signals['measurement'])
    if arm != 'unattributed':
        passed = passed and attribution and not refusal
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


def run_sample(client, arm, messages, table, *, model, system, tools_schema, max_turns=4):
    said, calls, missing = [], [], Counter()
    for _ in range(max_turns):
        response = client.messages.create(model=model, max_tokens=4096,
            system=system, tools=tools_schema, messages=messages)
        # SDK-shaped blocks carry response-only fields; echo only API input fields.
        blocks = []
        for block in response.content:
            b = block.model_dump()
            keys = {'text': ('type', 'text'), 'tool_use': ('type', 'id', 'name', 'input'),
                    'thinking': ('type', 'thinking', 'signature'),
                    'redacted_thinking': ('type', 'data')}[b['type']]
            blocks.append({k: b[k] for k in keys})
        said.extend(b['text'] for b in blocks if b['type'] == 'text')
        uses = [b for b in blocks if b['type'] == 'tool_use']
        messages = [*messages, {'role': 'assistant', 'content': blocks}]
        if response.stop_reason == 'max_tokens':
            return {'verdict': 'NO_DECISION', 'said': said, 'calls': calls,
                    'not_available': dict(missing)}
        if not uses:
            result = score(arm, '\n'.join(said))
            if missing: result['verdict'] = 'NOT_AVAILABLE'
            return {**result, 'said': said, 'calls': calls, 'not_available': dict(missing)}
        answers = []
        for b in uses:
            calls.append(b['name'])
            key = (b['name'], json.dumps(b['input'], sort_keys=True))
            content = table.get(key)
            if content is None:
                missing[b['name']] += 1
                content = json.dumps({'error': b['name'] + ': not available in this replay'})
            answers.append({'type': 'tool_result', 'tool_use_id': b['id'], 'content': content})
        messages.append({'role': 'user', 'content': answers})
    return {'verdict': 'NO_DECISION', 'said': said, 'calls': calls, 'not_available': dict(missing)}


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
        synthesized = fixture(arm)
        messages = messages_for(session, synthesized)
        tally, unavailable = Counter(), Counter()
        for i in range(args.samples):
            if args.dry_run:
                client = ScriptedClient([[{'type': 'text', 'text': PASS_TEXT[arm] if i % 2 == 0 else FAIL_TEXT}]])
            result = run_sample(client, arm, messages, recorded_results(session),
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
