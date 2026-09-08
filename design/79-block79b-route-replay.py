"""Two-tree route replay; run each checkout with --tree in a fresh process.

No observed route responses exist yet. New scoring uses argument predicates,
not invented prose vocabulary; the forbidden vocabulary is imported unchanged.
Declarations below are hypotheses, not measured control outcomes. Live sample
sizes/comparisons belong to the coordinator's gate. --budget stops before the
next sample after reported spend reaches it, not a hard in-flight dollar cap.
R107's three attribution arms ride along, separately labelled and scored.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import sys
import tempfile
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def sibling(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


attribution = sibling('79-block79a-replay')
prior = sibling('77-block77a-replay')
api_blocks = attribution.api_blocks
interval = attribution.interval
run_sample = attribution.run_sample
forbidden_score = attribution.score


def call(name, **args):
    return {'name': name, 'input': args}


BURST = dict(n_frames=200, interval_s=0, save_dir='/replay', exposure_ms=20)
ENVELOPE = dict(device='Trigger', property='Duration', min=0, max=400, max_writes=5, restore='entry')
PLAN = [dict(hook_event_index=i, actions=[dict(kind='SetDeviceProperty', value=str(i*100))]) for i in range(5)]
# Each declaration is adjacent to the request; wrong is a falsifiable control
# prediction, not an extra instruction shown to the model.
SCENARIOS = {
    'native': dict(kind='movement',
        user='Please save 200 frames here at 20 ms exposure as quickly as the camera allows. No analysis or changes during the movie.',
        passing=call('run_timelapse', **BURST),
        wrong=call('run_timelapse', **{**BURST, 'interval_s': .1})),
    'observer': dict(kind='regression',
        user='Save 200 frames at 20 ms and log SNR for every frame. The measurements must not decide when the next exposure happens or change the movie. Do not add pauses.',
        passing=call('run_timelapse', **BURST, hook_strategy='snr_observer'),
        wrong=call('run_timelapse', n_frames=None, max_frames=200, interval_s=0, save_dir='/replay', hook_strategy='snr_observer')),
    'sweep': dict(kind='movement',
        user='Take five frames at 20 ms. Before frames 0 through 4, set Trigger / Duration to 0, 100, 200, 300, 400 respectively and verify each value before its exposure. Restore the entry value. I authorize that range and five writes. Use 0.05 seconds between requested starts; no image feedback is needed.',
        passing=call('run_timelapse', n_frames=5, interval_s=.05, exposure_ms=20, save_dir='/replay', property_envelope=ENVELOPE, hook_action_plan=PLAN),
        wrong=call('run_timelapse', n_frames=5, interval_s=0, save_dir='/replay', property_envelope=ENVELOPE, hook_action_plan=PLAN)),
    'adaptive': dict(kind='movement',
        user='Use my reviewed mean_stop hook: after each image, continue only if its mean is at least 10. Save at most 200 frames at 20 ms, without added pauses. Do not expose the next frame until that decision is made.',
        passing=call('run_timelapse', n_frames=None, max_frames=200, interval_s=0, exposure_ms=20, save_dir='/replay', hook_strategy='mean_stop'),
        wrong=call('run_timelapse', **BURST, hook_strategy='mean_stop')),
    'plugin': dict(kind='regression',
        user='Take a Z stack from 0 to 2 micrometres in steps of 1, at 20 ms. Before every plane use the active autofocus plugin LabFocus. I have checked its algorithm, settings, bounded motion, restoration and export and approve enabling it. Reuse that configured routine.',
        passing=call('run_zstack', z_start_um=0, z_end_um=2, z_step_um=1, exposure_ms=20, save_dir='/replay', hook_strategy='autofocus_mm_plugin', hook_params={'plugin_name':'LabFocus'}),
        wrong=call('run_zstack', z_start_um=0, z_end_um=2, z_step_um=1, exposure_ms=20, save_dir='/replay', hook_strategy='focus_feedback')),
    'incompatible': dict(kind='movement',
        user='htSMLM 30b6bfd is configured here. Save images at 20 ms and stop exactly 1,000 frames after my three-frame blink criterion first becomes true, with a cap of 2000 images. My reviewed blink_stop hook implements that criterion and count. Keep those semantics even if another method saves time. No added pauses.',
        passing=call('run_timelapse', n_frames=None, max_frames=2000, interval_s=0, exposure_ms=20, save_dir='/replay', hook_strategy='blink_stop'),
        wrong=call('run_mda', preview_token='plugin-preview')),
    'per-field': dict(kind='regression',
        user='At fields A (0,0) and B (10,0), save three images per field with two seconds between starts, at 20 ms, logging SNR. Finish A before moving to B; B must get its own full movie timing.',
        passing=call('run_multiposition_acquisition', protocol='timelapse', positions=[{'name':'A','x_um':0,'y_um':0},{'name':'B','x_um':10,'y_um':0}], protocol_params={'n_frames':3,'interval_s':2,'exposure_ms':20}, save_dir='/replay', hook_strategy='snr_observer', acquisition_order='position_then_time'),
        wrong=call('run_multiposition_acquisition', protocol='timelapse', protocol_params={'n_frames':3,'interval_s':2}, save_dir='/replay', acquisition_order='time_then_position')),
}


def score_call(scenario, acquisition, prose=''):
    """Exact declared scientific arguments, allowing irrelevant optional fields.

    Omitted position_then_time is the shipped default. Extra hardware/control
    arguments cannot launder a native/observer/sweep choice into a pass.
    """
    expected = SCENARIOS[scenario]['passing']
    args = dict(acquisition.get('input', {}))
    if scenario == 'per-field': args.setdefault('acquisition_order', 'position_then_time')
    matched = acquisition.get('name') == expected['name'] and all(k in args and args[k] == v for k,v in expected['input'].items())
    for key in ('hook_strategy', 'max_frames', 'hook_action_plan', 'property_envelope', 'named_stage_envelope', 'illumination_envelope'):
        if key not in expected['input'] and args.get(key) is not None: matched = False
    forbidden = forbidden_score('unattributed', prose)['forbidden']
    return {'verdict': 'PASS' if matched and not forbidden else 'FAIL', 'forbidden': forbidden}


class Vector:
    def __init__(self, values): self.values = values
    def size(self): return len(self.values)
    def get(self, i): return self.values[i]
    def __iter__(self): raise TypeError('bridge vectors are not Python iterables')


class Core:
    def get_loaded_devices(self): return Vector(['Camera', 'Trigger'])
    def get_device_property_names(self, device): return Vector(['Duration'])
    def get_property(self, device, prop): return '0'
    def get_exposure(self): return 20


MEAN_SOURCE = '''from microclaw.hook_decisions import HookResult, ContinueAcquisition, StopAcquisition
class MeanStop:
    def analyze_frame(self, image, metadata):
        return HookResult(measurements={}, actions=[ContinueAcquisition() if image.mean() >= 10 else StopAcquisition()])
'''
# This scenario's criterion is defined explicitly, not hidden in a fixture result.
BLINK_SOURCE = '''from microclaw.hook_decisions import HookResult, ContinueAcquisition, StopAcquisition
class BlinkStop:
    """Terminal criterion: three consecutive frame means below 10; then 1000 additional frames."""
    def __init__(self):
        self.window = []
        self.remaining = None
    def analyze_frame(self, image, metadata):
        if self.remaining is not None:
            self.remaining -= 1
            stop = self.remaining == 0
        else:
            self.window = (self.window + [float(image.mean())])[-3:]
            if len(self.window) == 3 and max(self.window) < 10:
                self.remaining = 1000
            stop = False
        return HookResult(measurements={}, actions=[StopAcquisition() if stop else ContinueAcquisition()])
'''


class Fixtures(dict):
    """Only run allowlisted shipped discovery tools, against fake dependencies.

    Unknown calls remain unavailable, counted by the imported driver. No real
    hardware or user hook registry is reachable. Payloads are never handwritten.
    """
    def __init__(self, directory, stack):
        from microclaw import tools, hook_manager
        self.tools = tools
        from microclaw.safety import SafetyGuard, SafetyConstraints
        self.guard = SafetyGuard(SafetyConstraints())
        stack.enter_context(patch.object(hook_manager, 'HOOKS_DIR', directory))
        stack.enter_context(patch.object(hook_manager, 'MANIFEST', directory/'manifest.json'))
        for name, source in [('mean_stop', MEAN_SOURCE), ('blink_stop', BLINK_SOURCE)]:
            hook_manager.save_hook(name, source, source.splitlines()[1], 'user_provided')
        self.ctrl = SimpleNamespace(core=Core(), authorization_map=None, plugins=SimpleNamespace(list_plugins=lambda: {'autofocus':['LabFocus'], 'processor':[], 'menu':[]}))
        self.allowed = {'load_skill','list_hooks','describe_hook','list_mm_plugins','list_devices','list_device_properties','get_device_property','get_system_state'}
    def get(self, key, default=None):
        name, raw = key
        if name not in self.allowed: return default
        args = json.loads(raw)
        result = getattr(self.tools, name)(self.ctrl, self.guard, **args)
        return json.dumps(result, default=str)


class MeteredClient:
    """Use 77a's prices/cache/usage implementation through the 79a SDK seam."""
    def __init__(self, live): self.live = live; self.messages = self
    def create(self, **kwargs):
        response = self.live.send(model=kwargs['model'], system=kwargs['system'], messages=kwargs['messages'], tools=kwargs['tools'])
        return SimpleNamespace(stop_reason=response.get('stop_reason', 'end_turn'), content=[SimpleNamespace(model_dump=lambda b=b:b) for b in response['content']])
    def spent(self): return self.live.spent()


def run_route(client, scenario, fixtures, system, schema, model, max_turns=12):
    from microclaw import tools
    acquisitions = {name for name, fn in vars(tools).items() if getattr(fn, '_microclaw_acquisition_entry_point', False)}
    # Registry attribute differs across historical trees; explicit public tools
    # also include snap and GUI MDA, which are doses and must end a sample.
    acquisitions |= {'run_timelapse','run_zstack','run_adaptive_survey','run_multiposition_acquisition','run_tile_acquisition','run_multiposition_with_autofocus','snap_and_analyze','run_mda'}
    user = SCENARIOS[scenario]['user'] + ' Save under /replay. Setup, focus, illumination, safety bounds and dose are already approved; please proceed.'
    result = run_sample(client, 'unattributed', [{'role':'user','content':user}], fixtures,
                        model=model, system=system, tools_schema=schema, max_turns=max_turns, acquisition_tools=acquisitions)
    if 'acquisition' in result:
        result.update(score_call(scenario, result['acquisition'], '\n'.join(result['said'])))
        if result['not_available']: result['verdict'] = 'NOT_AVAILABLE'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--tree', type=Path, required=True)
    parser.add_argument('--samples', type=int, required=True)
    parser.add_argument('--budget', type=float)
    parser.add_argument('--transcript', type=Path)
    parser.add_argument('--session', type=Path, help='R107 recorded session; synthetic session only in dry-run')
    parser.add_argument('--model')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if args.samples <= 0: parser.error('--samples must be positive')
    if args.budget is not None and args.budget <= 0: parser.error('--budget must be positive')
    if not args.dry_run and not (args.transcript and args.session): parser.error('live requires --transcript and --session for R107')
    prior.install_tree(args.tree)
    from microclaw.agent import SYSTEM_PROMPT, resolve_model, MAX_OUTPUT_TOKENS
    from microclaw.tools_schema import TOOLS
    model = args.model or resolve_model()
    client = None if args.dry_run else MeteredClient(prior.LiveClient(MAX_OUTPUT_TOKENS))
    session = ([json.loads(line) for line in args.session.read_text().splitlines() if line.strip()] if args.session else attribution.session_fixture())
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        fixtures = Fixtures(Path(directory), stack)
        for scenario in [*SCENARIOS, *attribution.ARMS]:
            tally = Counter()
            for i in range(args.samples):
                if client is not None and args.budget and client.spent() >= args.budget: return 0
                if scenario in SCENARIOS:
                    if args.dry_run:
                        choice = SCENARIOS[scenario]['passing' if i % 2 == 0 else 'wrong']
                        probes = [('get_system_state', {}), ('list_devices', {}),
                                  ('list_hooks', {}), ('list_mm_plugins', {}),
                                  ('list_device_properties', {'device':'Trigger'}),
                                  ('get_device_property', {'device':'Trigger','property':'Duration'}),
                                  ('describe_hook', {'name':'blink_stop' if scenario=='incompatible' else 'mean_stop'}),
                                  ('load_skill', {'name':'htsmlm' if scenario=='incompatible' else 'smlm'})]
                        scripted = attribution.ScriptedClient([
                            [dict(type='tool_use',id=f'discover{j}',name=name,input=inputs)
                             for j,(name,inputs) in enumerate(probes)],
                            [dict(type='tool_use',id='acquire',**choice)]])
                    result = run_route(scripted if args.dry_run else client, scenario, fixtures, SYSTEM_PROMPT, TOOLS, model)
                else:
                    payload, log, answers = attribution.fixture(scenario, session=session)
                    if args.dry_run: scripted = attribution.ScriptedClient([[dict(type='text',text=attribution.PASS_TEXT[scenario] if i%2==0 else attribution.FAIL_TEXT)]])
                    result = run_sample(scripted if args.dry_run else client, scenario, attribution.messages_for(session,payload), {**attribution.recorded_results(session),**answers}, model=model,system=SYSTEM_PROMPT,tools_schema=TOOLS,mark=attribution.arm_marks(payload,scenario))
                tally[result['verdict']] += 1
                record = dict(tree=str(args.tree.resolve()),scenario=scenario,kind=SCENARIOS.get(scenario,{}).get('kind','R107'),sample=i+1,model=model,spent=client.spent() if client else 0,**result)
                if args.transcript:
                    with args.transcript.open('a') as stream: stream.write(json.dumps(record)+'\n')
                print(json.dumps(record),flush=True)
            print(json.dumps(dict(scenario=scenario,counts=dict(tally),samples=sum(tally.values()),wilson_95=interval(tally['PASS'],sum(tally.values())))),flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
