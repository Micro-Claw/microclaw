"""Two-tree route replay; run each checkout with --tree in a fresh process.

No observed route responses exist yet. New scoring uses argument predicates,
not invented prose vocabulary; the forbidden vocabulary is imported unchanged.
Declarations below are hypotheses, not measured control outcomes. Live sample
sizes/comparisons belong to the coordinator's gate. --budget stops before the
next sample after reported spend reaches it, not a hard in-flight dollar cap.
Movement arms: native, sweep, adaptive, incompatible (control expected to fail).
Regression arms: observer, plugin, per-field (both expected to pass); their
passes are not evidence of improvement.
R107's three attribution arms ride along, separately labelled and scored.

Cost measured 2026-09-09, claude-opus-4-8, arm-only development pilot:
$2.85 for 25 samples (~$0.114/sample); route samples averaged 3.4 turns
(range 2-6). Fixture/cache defects prevented a usable product measurement.
These are measured pilot costs, not a prediction or a gate result.
"""
from __future__ import annotations
import argparse
import copy
import importlib.util
import json
import math
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
    # interval_s in passing is a scripted example, not the criterion: any
    # nonnegative finite interval with distinct engine deadlines passes.
    'sweep': dict(kind='movement',
        user='Take five frames at 20 ms. Before frames 0 through 4, set Trigger / Duration to 0, 100, 200, 300, 400 respectively and verify each value before its exposure. Restore the entry value. I authorize that range and five writes. Do this as quickly as the verified writes allow; no image feedback is needed.',
        passing=call('run_timelapse', n_frames=5, interval_s=.05, exposure_ms=20, save_dir='/replay', property_envelope=ENVELOPE, hook_action_plan=PLAN),
        wrong=call('run_timelapse', n_frames=5, interval_s=0, exposure_ms=20, save_dir='/replay', property_envelope=ENVELOPE, hook_action_plan=PLAN)),
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
    compared = expected['input'].copy()
    if scenario == 'sweep':
        del compared['interval_s']
    matched = acquisition.get('name') == expected['name'] and all(k in args and args[k] == v for k,v in compared.items())
    if scenario == 'sweep':
        from microclaw.tools import _refuse_sequenced_time_axis
        delay = args.get('interval_s')
        valid_delay = (isinstance(delay, (int, float)) and not isinstance(delay, bool)
                       and math.isfinite(delay) and delay >= 0)
        if valid_delay and matched:
            try:
                _refuse_sequenced_time_axis(args['n_frames'], delay, hardware_actions=True)
            except ValueError:
                valid_delay = False
        matched = matched and valid_delay
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
    """MMCore method shapes, including non-iterable StrVectors and enum names."""
    devices = {'Camera':'CameraDevice', 'Trigger':'GenericDevice',
               'Z':'StageDevice', 'XY':'XYStageDevice', 'Focus':'AutoFocusDevice'}
    def __init__(self): self.z = 0.0; self.xy = (0.0, 0.0)
    def get_loaded_devices(self): return Vector(list(self.devices))
    def get_device_type(self, device): return self.devices[device]
    def get_device_name(self, device): return device
    def get_device_library(self, device): return 'Replay'
    def get_device_property_names(self, device):
        if device not in self.devices: raise KeyError(device)
        return Vector(['Duration'] if device == 'Trigger' else ['Enabled'] if device == 'Focus' else [])
    def get_property(self, device, prop):
        if (device, prop) not in (('Trigger', 'Duration'), ('Focus', 'Enabled')): raise KeyError((device, prop))
        return '0'
    def is_property_read_only(self, device, prop): self.get_property(device, prop); return False
    def is_property_pre_init(self, device, prop): self.get_property(device, prop); return False
    def get_property_type(self, device, prop): self.get_property(device, prop); return 'Float'
    def get_allowed_property_values(self, device, prop): self.get_property(device, prop); return Vector([])
    def has_property_limits(self, device, prop): self.get_property(device, prop); return True
    def get_property_lower_limit(self, device, prop): return 0
    def get_property_upper_limit(self, device, prop): return 400
    def get_exposure(self): return 20
    def get_x_position(self): return self.xy[0]
    def get_y_position(self): return self.xy[1]
    def get_position(self, device='Z'):
        if device != 'Z': raise KeyError(device)
        return self.z
    def set_position(self, *args):
        device, value = ('Z', args[0]) if len(args) == 1 else args
        if device != 'Z': raise KeyError(device)
        self.z = float(value)
    def set_xy_position(self, x, y): self.xy = (float(x), float(y))
    def get_focus_device(self): return 'Z'
    def get_xy_stage_device(self): return 'XY'
    def get_camera_device(self): return 'Camera'
    def get_auto_focus_device(self): return 'Focus'
    def is_continuous_focus_enabled(self): return False
    def get_available_pixel_size_configs(self): return Vector([])
    def get_available_config_groups(self): return Vector([])
    def get_available_configs(self, group): return Vector([])
    def get_pixel_size_um(self): return .1
    def get_roi(self): return SimpleNamespace(x=0, y=0, width=64, height=64)
    def get_image_width(self): return 64
    def get_image_height(self): return 64
    def get_shutter_device(self): return ''


class JavaCollection:
    """AutofocusManager returns a Java List, not MMCore's StrVector."""
    def __init__(self, values): self.values = values
    def __iter__(self): raise TypeError('bridge collections require iterator()')
    def iterator(self):
        pending = iter(self.values)
        index = [0]
        def next_item(): index[0] += 1; return next(pending)
        return SimpleNamespace(has_next=lambda:index[0] < len(self.values), next=next_item)


class AutofocusManager:
    """Configured LabFocus method, consumed by the real PluginAccess seam."""
    def __init__(self, core): self.core = core; self.calls = 0
    def get_all_autofocus_methods(self): return JavaCollection(['LabFocus'])
    def set_autofocus_method_by_name(self, name):
        if name != 'LabFocus': raise ValueError(name)
    def get_autofocus_method(self): return self
    def get_name(self): return 'LabFocus'
    def full_focus(self): self.calls += 1; return self.core.z


# These public tools write hardware or initiate an independent exposure/motion
# workflow. They are choices, never discovery responses; none executes here.
HARDWARE_WRITES = {
    'move_stage_xy', 'move_stage_z', 'move_named_stage', 'go_to_position',
    'set_device_property', 'set_channel', 'set_config_preset', 'set_exposure',
    'set_roi', 'clear_roi', 'start_live_view', 'stop_live_view',
    'shutter_declared_illumination', 'set_focus_lock',
    'set_emu_laser_power_percentage', 'verify_emu_laser_power_calibration',
    'run_autofocus', 'calibrate_stage_to_camera', 'center_feature',
    'find_features', 'snap_to_album', 'calibrate_snr_threshold',
}


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
        from microclaw import tools, hook_manager, emu_manager, knowledge_manager
        self.tools = tools
        from microclaw.safety import SafetyGuard, SafetyConstraints, StageConstraints, PluginConstraints
        self.guard = SafetyGuard(SafetyConstraints(stage=StageConstraints(x_min=-100, x_max=100, y_min=-100, y_max=100, z_min=-10, z_max=10), plugins=PluginConstraints(allow_hardware_motion=True)))
        # Real EMU discovery reads an isolated installation-shaped filesystem.
        mm = directory/'mm'
        (mm/'EMU').mkdir(parents=True)
        (mm/'mmplugins').mkdir()
        for jar in ('EMU.jar', 'htSMLM-30b6bfd.jar'):
            (mm/'mmplugins'/jar).touch()
        (mm/'EMU'/'config.uicfg').write_text(json.dumps({
            'defaultConfigurationName':'Replay', 'pluginConfigurations':[
                {'configurationName':'Replay', 'pluginName':'htSMLM',
                 'properties':{'Z stage focus locking':'Focus-Enabled',
                               'Z stage focus locking - On value':'1',
                               'Z stage focus locking - Off value':'0'},
                 'parameters':{}, 'settings':{}}]}), encoding='utf-8')
        stack.enter_context(patch.object(emu_manager, '_MICROCLAW_DIR', directory))
        stack.enter_context(patch.object(emu_manager, '_EMU_CACHE', directory/'emu.json'))
        stack.enter_context(patch.object(tools, '_EMU_SESSION_CACHE', {}))
        stack.enter_context(patch.object(knowledge_manager, 'KNOWLEDGE_PATH', directory/'knowledge.yaml'))
        stack.enter_context(patch.object(hook_manager, 'HOOKS_DIR', directory))
        stack.enter_context(patch.object(hook_manager, 'MANIFEST', directory/'manifest.json'))
        for name, source in [('mean_stop', MEAN_SOURCE), ('blink_stop', BLINK_SOURCE)]:
            hook_manager.save_hook(name, source, source.splitlines()[1], 'user_provided')
        from microclaw.controller import PluginAccess
        from microclaw.authorization import AuthorizationMap
        core = Core()
        af = AutofocusManager(core)
        pm = SimpleNamespace(get_processor_plugins=lambda:{}, get_menu_plugins=lambda:{})
        studio = SimpleNamespace(get_autofocus_manager=lambda:af, plugins=lambda:pm,
                                 live=lambda:SimpleNamespace(is_live_mode_on=lambda:False))
        self.ctrl = SimpleNamespace(core=core, studio=studio, plugins=PluginAccess(studio),
                                    get_mm_app_dir=lambda:str(mm),
                                    authorization_map=AuthorizationMap('strict', 'pass', True))
        self.allowed = {'load_skill','list_hooks','describe_hook','list_mm_plugins',
                        'list_devices','list_device_properties','get_device_property',
                        'get_system_state','get_device_property_info','check_emu_installed',
                        'get_xy_position','get_z_position','list_stages','get_current_datetime',
                        'get_stage_position','get_focus_lock_state','get_full_device_state',
                        'get_exposure','get_pixel_size','get_roi','list_config_groups',
                        'get_available_channels','get_emu_laser_map','get_emu_configuration','get_knowledge'}

    def get(self, key, default=None):
        name, raw = key
        if name not in self.allowed: return default
        args = json.loads(raw)
        if name == 'get_emu_configuration' and args.get('mm_app_dir') not in (None, self.ctrl.get_mm_app_dir()):
            return default  # never read a model-selected host path
        try:
            result = getattr(self.tools, name)(self.ctrl, self.guard, **args)
        except (AttributeError, KeyError, TypeError, ValueError):
            # Unsupported fixture arguments/dependencies are still counted by
            # the imported driver, never converted into a fabricated answer.
            return default
        return json.dumps(result, default=str)


class MeteredClient:
    """Use 77a's prices/cache/usage implementation through the 79a SDK seam."""
    def __init__(self, live): self.live = live; self.messages = self
    def create(self, **kwargs):
        # Reserve one breakpoint each for 77a's system and tools. Keep the
        # earliest stable recorded prefix and the newest conversation prefix;
        # a middle fixture-result marker is redundant once discovery follows it.
        # Pre-marking the newest block makes 77a's subsequent marking idempotent.
        messages = prior._cache_conversation_prefix(copy.deepcopy(kwargs['messages']))
        marked = []
        def walk(value):
            if isinstance(value, dict):
                if 'cache_control' in value: marked.append(value)
                for child in value.values(): walk(child)
            elif isinstance(value, list):
                for child in value: walk(child)
        walk(messages)
        for block in marked[1:-1]: del block['cache_control']
        response = self.live.send(model=kwargs['model'], system=kwargs['system'], messages=messages, tools=kwargs['tools'])
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
                        model=model, system=system, tools_schema=schema, max_turns=max_turns, acquisition_tools=acquisitions | HARDWARE_WRITES)
    if 'acquisition' in result:
        result.update(score_call(scenario, result['acquisition'], '\n'.join(result['said'])))
        if result['acquisition']['name'] in HARDWARE_WRITES:
            result['verdict'] = 'FAIL'
        elif result['not_available']: result['verdict'] = 'NOT_AVAILABLE'
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
                                  ('get_device_property_info', {'device':'Trigger','property':'Duration'}),
                                  ('check_emu_installed', {}), ('get_xy_position', {}),
                                  ('get_z_position', {}), ('list_stages', {}),
                                  ('get_current_datetime', {}),
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
