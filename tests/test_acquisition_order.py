"""Engine-based order evidence: real event builder; submission is not execution.

The constructor fake dispatches to a backend, like pycro-manager's __new__.
Queued frames are consumed only inside backend.__exit__, independently of event
construction. Hardware callbacks receive batches, image callbacks single frames.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from pycromanager import multi_d_acquisition_events

from microclaw import tools
from microclaw.acquisition import AcquisitionLedger, AcquisitionPlan
from microclaw.hooks import HookBase


POSITIONS = [dict(name='A', x_um=0., y_um=0.), dict(name='B', x_um=10., y_um=0.)]


class RecordingBackend:
    def __init__(self, rig, **kwargs):
        self.rig, self.kwargs = rig, kwargs
        self._exception = None
        self._dataset_disk_location = str(Path(kwargs['directory']) / kwargs['name'])
        self.origin = rig.now
        rig.acquisitions.append(self)

    def acquire(self, events):
        self.events = list(events)
        self.rig.submitted.append([dict(e['axes']) for e in self.events])

    def __enter__(self):
        return self

    def __exit__(self, *_):
        r = self.rig
        r.now += r.queue_delay
        pre = self.kwargs.get('pre_hardware_hook_fn')
        if pre:
            pre(self.events)
        post = self.kwargs.get('post_hardware_hook_fn')
        if post:
            post(self.events)
        for event in self.events:
            if 'x' in event:
                r.move(event['x'], event['y'])
            r.now = max(r.now, self.origin + event.get('min_start_time', 0))
            axes = event['axes']
            label = axes.get('position', 'A' if r.xy[0] == 0 else 'B')
            r.frames.append((label, axes.get('time', axes.get('z')), r.now))
            metadata = {'Axes': dict(axes), 'PositionName': label,
                        'FrameIndex': axes.get('time', axes.get('z')),
                        'XPosition_um_Intended': r.xy[0], 'YPosition_um_Intended': r.xy[1]}
            processor = self.kwargs.get('image_process_fn')
            if processor:
                processor(np.arange(64, dtype=np.uint16).reshape(8, 8), metadata, None)
            saved = self.kwargs.get('image_saved_fn')
            if saved:
                saved(axes, self)
            r.now += .01


@pytest.fixture
def rig(monkeypatch, tmp_path):
    r = SimpleNamespace(now=0., xy=(0., 0.), frames=[], submitted=[],
                        acquisitions=[], moves=[], queue_delay=.17, settle=7.)
    def move(x, y):
        if (x, y) != r.xy:
            r.now += r.settle
            r.xy = (x, y)
        r.moves.append((x, y, r.now))
        return {}
    r.move = move
    class Dispatch:
        def __new__(cls, **kwargs):
            return RecordingBackend(r, **kwargs)
    monkeypatch.setattr(tools, 'Acquisition', Dispatch)
    r.ctrl = MagicMock()
    r.ctrl.set_xy.side_effect = move
    r.ctrl.core.set_xy_position.side_effect = move
    r.ctrl.core.get_x_position.side_effect = lambda: r.xy[0]
    r.ctrl.core.get_y_position.side_effect = lambda: r.xy[1]
    r.ctrl.core.get_exposure.return_value = 10.
    r.ctrl.core.get_image_width.return_value = 8
    r.ctrl.core.get_image_height.return_value = 8
    r.ctrl.core.get_bytes_per_pixel.return_value = 2
    r.ctrl.studio.live().is_live_mode_on.return_value = False
    r.guard = MagicMock()
    r.guard.resolve_in_workspace.side_effect = str
    r.guard.analysis_min_snr = None
    r.guard.declared_illumination_state.return_value = {}
    r.guard.stage_move_tolerance.return_value = None
    monkeypatch.setattr(tools, 'read_xy_start_position', lambda *_: r.xy)
    monkeypatch.setattr(tools, 'settle_xy_move', lambda *_: {})
    ledger = AcquisitionLedger()
    r.reservations = []
    def authorize(ctrl, guard, plan):
        res = ledger.reserve(guard, plan)
        r.reservations.append(res)
        return res
    monkeypatch.setattr(tools, '_authorize_acquisition', authorize)
    r.params = dict(protocol='timelapse', positions=POSITIONS, save_dir=str(tmp_path),
                    protocol_params=dict(n_frames=3, interval_s=0))
    r.run = lambda **kw: tools.run_multiposition_acquisition(r.ctrl, r.guard, **{**r.params, **kw})
    return r


@pytest.mark.parametrize('hook,order', [(False,None),(True,None),
                                      (False,'time_then_position'),(True,'time_then_position')])
def test_order_event_axes_or_stage_and_frame_execution_across_boundaries(rig, hook, order):
    """Combined paths: event axes; position loops: stage and executed frame records."""
    kw = {'hook_strategy':'snr_observer'} if hook else {}
    if order:
        kw['acquisition_order'] = order
    result = rig.run(**kw)
    assert 'error' not in result, result
    expected = [(p,t) for t in range(3) for p in 'AB'] if order else [(p,t) for p in 'AB' for t in range(3)]
    assert [(p,t) for p,t,_ in rig.frames] == expected
    assert result['acquisition_order'] == (order or 'position_then_time')
    if hook or order:
        assert [(e['position'],e['time']) for e in rig.submitted[0]] == expected
        assert len(rig.acquisitions) == 1
    else:
        assert len(rig.acquisitions) == 2
        assert [xy[:2] for xy in rig.moves] == [(0.,0.),(10.,0.)]
    assert all('min_start_time' not in e for a in rig.acquisitions for e in a.events)


@pytest.mark.parametrize('settle,delay', [(7.,.17),(19.,.43)])
def test_spaced_hook_execution_clock_survives_delayed_consumption_and_slow_stage(rig, settle, delay):
    rig.settle, rig.queue_delay = settle, delay
    result = rig.run(hook_strategy='snr_observer', protocol_params=dict(n_frames=3,interval_s=2))
    assert 'error' not in result, result
    assert len(rig.acquisitions) == 2
    assert [(p,t) for p,t,_ in rig.frames] == [(p,t) for p in 'AB' for t in range(3)]
    a,b = [v for p,t,v in rig.frames if p=='A'], [v for p,t,v in rig.frames if p=='B']
    assert np.diff(b) == pytest.approx(np.diff(a))
    assert min(np.diff(b)) >= 2-delay
    assert result['timing']['strategy'] == 'per_position_clock'
    assert len(rig.reservations) == 2 and all(r._closed for r in rig.reservations)


@pytest.mark.parametrize('protocol', ['snap','zstack'])
def test_inapplicable_order_refuses_before_any_hardware(rig, protocol):
    params = {} if protocol=='snap' else dict(z_start_um=0,z_end_um=2,z_step_um=1)
    result = rig.run(protocol=protocol, protocol_params=params, acquisition_order='time_then_position')
    assert 'inapplicable' in result['error']
    assert not rig.ctrl.core.mock_calls
    assert not rig.acquisitions


def test_sequenced_hardware_batch_delivers_individual_frame_metadata_once(rig, monkeypatch):
    class Observer(HookBase):
        def __init__(self):
            super().__init__()
            self.batches, self.seen = [], []
        def post_hardware_hook_fn(self, events):
            self.batches.append(events)
            return events
        def image_process_fn(self, image, metadata, queue):
            self.seen.append((metadata['PositionName'],metadata['FrameIndex']))
            return image,metadata
    observer=Observer()
    monkeypatch.setattr(tools,'_resolve_hooks',lambda *_:observer)
    result=rig.run(hook_strategy='observer')
    assert 'error' not in result, result
    assert len(observer.batches)==1 and len(observer.batches[0])==6
    assert observer.seen == [(p,t) for p in 'AB' for t in range(3)]


@pytest.mark.parametrize('hook,order,interval', [(False,None,0),(True,None,0),
    (False,'time_then_position',0),(True,'time_then_position',2),(True,None,2)])
def test_export_exec_matches_live_frames_clocks_labels_and_hook_output(rig, tmp_path, monkeypatch, hook, order, interval):
    kw = {**rig.params, 'protocol_params':dict(n_frames=3,interval_s=interval)}
    if hook:
        kw.update(hook_strategy='snr_observer',log_path=str(tmp_path/'live.json'))
    if order:
        kw['acquisition_order']=order
    result=rig.run(**kw)
    assert 'error' not in result, result
    live=list(rig.frames)
    import json
    def observations(paths):
        rows=[]
        for path in paths:
            for row in json.loads(Path(path).read_text(encoding="utf-8")):
                row.pop("observed_at",None)
                row.get("result",{}).pop("analysis_ms",None)
                rows.append(row)
        return rows
    paths=([r["log_path"] for r in result.get("results",[])] if interval and not order
           else [result["log_path"]]) if hook else []
    live_observations=observations(paths)
    rig.now=0.;rig.xy=(0.,0.);rig.frames=[];rig.moves=[];rig.acquisitions=[]
    from tests.test_session_script_export import export, completed_call
    export_dir=tmp_path/'export'
    export_dir.mkdir()
    _, report, source=export(export_dir, completed_call('run_multiposition_acquisition',kw,result))
    assert report['emitted_calls']==1, report
    ast.parse(source)
    import pycromanager
    monkeypatch.setattr(pycromanager,'Acquisition',tools.Acquisition)
    monkeypatch.setattr(pycromanager,'Core',lambda:rig.ctrl.core)
    env={'__file__':str(export_dir/'routine.py')}
    exec(source,env)
    assert rig.frames == live
    if hook:
        assert observations(sorted(export_dir.glob('live*.json'))) == live_observations



@pytest.mark.parametrize('gap', [False, True])
@pytest.mark.parametrize('interval', [0,2])
def test_stateful_movie_observer_separates_fields_and_detects_interruption(rig, monkeypatch, tmp_path, gap, interval):
    """Observers key state by field in combined data; spaced movies initialize separately."""
    observers=[]
    class MovieObserver(HookBase):
        def __init__(self, log):
            super().__init__(log)
            self.frames={}
            observers.append(self)
        def image_process_fn(self,image,metadata,queue):
            field=metadata['PositionName']
            frame=metadata['FrameIndex']
            if gap and field=='B' and frame==1:
                raise RuntimeError('injected movie gap')
            self.frames.setdefault(field,[]).append(frame)
            self.log(metadata,frame=frame)
            return image,metadata
        def verdict(self,field):
            return 'no motion' if self.frames.get(field)==[0,1,2] else 'incomplete'
    monkeypatch.setattr(tools,'_resolve_hooks',lambda c,g,s,p,log:MovieObserver(log))
    result=rig.run(hook_strategy='movie',log_path=str(tmp_path/'movie.json'),
                   protocol_params=dict(n_frames=3,interval_s=interval))
    assert len(observers)==(2 if interval else 1)
    assert observers[0].frames['A'] == [0,1,2]
    assert observers[0].verdict('A')=='no motion'
    assert observers[-1].verdict('B')==('incomplete' if gap else 'no motion')
    assert ('error' in result)==gap
    if interval:
        assert set(observers[-1].frames)=={'B'}
        assert Path(observers[0].log_path).read_text(encoding="utf-8")!=Path(observers[1].log_path).read_text(encoding="utf-8")
        assert len(result['results'])==(1 if gap else 2)


def test_declared_per_frame_plan_refused_before_exposure_and_runtime_batch_guard(rig):
    """Multiposition has no fixed action-plan route; nested plans refuse before hardware.

    Independently exercise the existing adapter batch guard with its bound-plan shape.
    This says nothing about decisions requiring a yet-unacquired input image.
    """
    result=rig.run(hook_strategy='snr_observer',protocol_params={
        'n_frames':3,'interval_s':0,'hook_action_plan':[{'event_index':1,'actions':[]}]})
    assert 'cannot carry per-run hook capabilities' in result['error']
    assert not rig.acquisitions and not rig.ctrl.core.mock_calls
    from microclaw.hook_decisions import UntrustedHookAdapter
    adapter=UntrustedHookAdapter(object())
    adapter._fixed_plan_context={}
    with pytest.raises(RuntimeError,match='hardware-sequenced burst'):
        adapter.pre_hardware_hook_fn(multi_d_acquisition_events(
            num_time_points=3,xy_positions=[(0,0)],order='ptcz'))


@pytest.mark.parametrize('tool_name', ['run_multiposition_acquisition','run_tile_acquisition',
                                      'run_multiposition_with_autofocus'])
def test_unterminated_spaced_movie_survives_real_entry_boundary_without_next_acquisition(rig, monkeypatch, tool_name):
    """Typed failure raised at the supervised boundary must bypass every composite catch."""
    import json
    monkeypatch.setattr('microclaw.authorization.authorize_path',lambda *_:None)
    import threading
    release=threading.Event()
    original=RecordingBackend.acquire
    def submit_then_fail(self,events):
        original(self,events)
        self._exception=RuntimeError('injected engine fault')
    def blocked_exit(self,*_):
        release.wait(5)
    monkeypatch.setattr(RecordingBackend,'acquire',submit_then_fail)
    monkeypatch.setattr(RecordingBackend,'__exit__',blocked_exit)
    monkeypatch.setattr(tools,'ERROR_TEARDOWN_GRACE_S',.01)
    monkeypatch.setattr(tools,'_ACQUISITION_POLL_S',.002)
    monkeypatch.setattr(tools,'_resolve_hooks',lambda *_:HookBase())
    params={**rig.params,'hook_strategy':'observer','protocol_params':dict(n_frames=3,interval_s=2)}
    if tool_name=='run_tile_acquisition':
        params.pop('positions')
        params.update(rows=1,cols=2,step_um=10,center_x_um=0,center_y_um=0)
    if tool_name=='run_multiposition_with_autofocus':
        params.pop('hook_strategy')
        params.update(z_range_um=2,z_step_um=1)
    try:
        result=json.loads(tools.execute_tool(tool_name,params,rig.ctrl,rig.guard))
        assert result.get('acquisition')=='unterminated',result
        assert len(rig.acquisitions)==1
        assert len(rig.reservations)==1 and not rig.reservations[0]._closed
    finally:
        release.set()
        pending=getattr(rig.ctrl,'_microclaw_unterminated_acquisition',None)
        if isinstance(pending,dict):
            pending['waiter'].join(1)
    assert rig.reservations[0]._closed


@pytest.mark.parametrize('tool_name', ['run_multiposition_acquisition', 'run_tile_acquisition',
                                      'run_multiposition_with_autofocus'])
def test_stage_failure_at_second_movie_preserves_type_and_finished_dataset(rig, monkeypatch, tool_name):
    """Execute the first movie, fail B's settled move, and count real constructions."""
    import json
    monkeypatch.setattr('microclaw.authorization.authorize_path', lambda *_: None)
    monkeypatch.setattr(tools, '_resolve_hooks', lambda *_: HookBase())
    failure = tools.StageMoveError({
        'start_um': [0., 0.], 'requested_um': [10., 0.], 'measured_um': [0., 0.],
        'elapsed_s': 1., 'tolerance_um': 2., 'band_source': 'relative',
        'last_device_status': 'busy',
    })
    moves = []
    def move(x, y):
        moves.append((x, y))
        if len(moves) == 2:
            raise failure
        return rig.move(x, y)
    rig.ctrl.set_xy.side_effect = move
    seen = []
    original_hint = tools._hint_for_tool_error
    def hint(fn, exc):
        seen.append(exc)
        return original_hint(fn, exc)
    monkeypatch.setattr(tools, '_hint_for_tool_error', hint)
    params = {**rig.params, 'hook_strategy': 'observer',
              'protocol_params': dict(n_frames=3, interval_s=2)}
    if tool_name == 'run_tile_acquisition':
        params.pop('positions')
        params.update(rows=1, cols=2, step_um=10, center_x_um=5, center_y_um=0)
    elif tool_name == 'run_multiposition_with_autofocus':
        params.pop('hook_strategy')
        params.update(z_range_um=2, z_step_um=1)
    result = json.loads(tools.execute_tool(tool_name, params, rig.ctrl, rig.guard))
    assert seen == [failure], result  # The original typed object reaches execute_tool.
    assert result['error'].startswith('StageMoveError:'), result
    assert len(rig.acquisitions) == 1 and len(moves) == 2
    assert len(rig.frames) == 3
    assert result['positions_completed'][0]['dataset_path'] == rig.acquisitions[0]._dataset_disk_location
    assert len(result['positions_completed']) == 1
    assert all(r._closed for r in rig.reservations)


@pytest.mark.parametrize('hook', [False, True])
def test_interleaved_trigger_preflight_only_preserves_hookless_contract(rig, monkeypatch, hook):
    calls = []
    monkeypatch.setattr(tools, '_verify_trigger_line_armed', lambda *args: calls.append(args))
    result = rig.run(acquisition_order='time_then_position',
                     hook_strategy='snr_observer' if hook else None,
                     protocol_params=dict(n_frames=3, interval_s=0, laser_slot=1))
    assert 'error' not in result, result
    assert len(calls) == (0 if hook else 1)


def test_multiposition_timing_has_one_shape_for_every_execution_path(rig):
    timings = []
    for hook in (None, 'snr_observer'):
        for order in ('position_then_time', 'time_then_position'):
            for interval in (0, 2):
                result = rig.run(hook_strategy=hook, acquisition_order=order,
                                 protocol_params=dict(n_frames=3, interval_s=interval))
                assert 'error' not in result, result
                timings.append(result['timing'])
    assert all(set(timing) == set(timings[0]) for timing in timings)
    assert all(timing['hook_observed_at'] == 'callback arrival time, not exposure time'
               for timing in timings)
    assert all(timing['frame_timestamp_metadata_key'] is None for timing in timings)


def test_every_stage_move_error_subclass_carries_the_completed_movie_attribute():
    """execute_tool reads .positions_completed on any StageMoveError it catches.

    XYStageMoveError overrides __init__ without calling super(), so declaring
    the attribute inside StageMoveError.__init__ leaves every XY failure without
    it and turns execute_tool's own error handler into an AttributeError.
    """
    from microclaw.controller import StageMoveError, XYStageMoveError

    result = {'start_um': [0., 0.], 'requested_um': [20., 0.], 'measured_um': [0., 0.],
              'elapsed_s': 0., 'tolerance_um': 2., 'band_source': 'relative',
              'x_tolerance_um': 2., 'x_band_source': 'relative',
              'y_tolerance_um': 2., 'y_band_source': 'relative',
              'last_device_status': 'busy'}
    subclasses = [StageMoveError, *StageMoveError.__subclasses__()]
    assert XYStageMoveError in subclasses
    for cls in subclasses:
        error = (cls(result, ['x']) if cls is XYStageMoveError
                 else cls({**result, 'start_um': 0., 'requested_um': 20., 'measured_um': 0.}))
        assert error.positions_completed is None, cls

@pytest.mark.parametrize('mode', ['real', 'fallback', 'absent', 'unreadable', 'split'])
def test_frame_spacing_from_recorded_metadata(rig, monkeypatch, mode):
    import json
    data = json.loads(Path('design/77-block77b-limbA-metadata.json').read_text(encoding='utf-8'))
    pairs = list(zip(data['coordinates'], data['metadata']))
    assert [m['ElapsedTime-ms'] for _, m in pairs] == [20, 39, 60, 100, 110, 130]
    class Replay:
        axes = data['axes']
        def __init__(self, path):
            if mode == 'unreadable':
                raise OSError('injected unreadable dataset')
            self.pairs = pairs
            if mode == 'split':
                label = 'gateA' if 'gateA' in path else 'gateB'
                self.pairs = [(c, m) for c, m in pairs if c['position'] == label]
        def get_image_coordinates_list(self):
            return [dict(reversed(list(c.items()))) for c, _ in reversed(self.pairs)]
        def read_metadata(self, **coords):
            metadata = dict(next(m for c, m in self.pairs if c == coords))
            if mode in ('fallback', 'absent'):
                metadata.pop('ElapsedTime-ms', None)
            if mode == 'fallback':
                metadata['TimeReceivedByCore'] = f"2026-09-06 09:49:57.{coords['time'] * 100000:06d}"
            if mode == 'absent':
                metadata.pop('TimeReceivedByCore', None)
            return metadata
    monkeypatch.setattr(tools, 'Dataset', Replay)
    result = rig.run(hook_strategy='snr_observer',
                     positions=[dict(p, name=n) for p, n in zip(POSITIONS, ['gateA', 'gateB'])],
                     protocol_params=dict(n_frames=3, interval_s=2 if mode == 'split' else 0))
    assert 'error' not in result
    timing = result['timing']
    assert 'exposure_timestamp_metadata_key' not in timing
    key = timing['frame_timestamp_metadata_key']
    if mode in ('absent', 'unreadable'):
        assert key is None
        assert timing['observed_per_field_spacing'] is None
        assert ('injected unreadable dataset' if mode == 'unreadable' else 'no frame timestamp key established') in timing['verification']
    else:
        assert key == ('TimeReceivedByCore' if mode == 'fallback' else 'ElapsedTime-ms')
        assert timing['observed_per_field_spacing'] == (
            {'gateA': [.1, .1], 'gateB': [.1, .1]} if mode == 'fallback' else
            {'gateA': [.019, .021], 'gateB': [.01, .02]})
        assert timing['frame_timestamp_meaning'] == (
            'absolute arrival time at the core' if mode == 'fallback' else
            'milliseconds since acquisition start')
        if mode == 'split':
            assert len(result['results']) == 2

@pytest.mark.parametrize('hook', [None, 'snr_observer'])
def test_export_omits_recorded_measurements(rig, tmp_path, hook):
    from tests.test_session_script_export import export, completed_call
    kw = {**rig.params, 'hook_strategy': hook}
    result = rig.run(**kw)
    result['timing'].update(observed_per_field_spacing={'measurement_sentinel': [123.456]},
                            frame_timestamp_metadata_key='ElapsedTime-ms')
    _, report, source = export(tmp_path, completed_call('run_multiposition_acquisition', kw, result))
    assert report['emitted_calls'] == 1
    assert 'measurement_sentinel' not in source
    assert 'ElapsedTime-ms' not in source
    assert 'requested_interval_s=0' in source


@pytest.mark.parametrize('hook', [None, 'snr_observer'])
def test_no_time_axis_spacing_is_not_applicable(rig, monkeypatch, hook):
    import json
    data = json.loads(Path('design/77-block77b-limbA-metadata.json').read_text(encoding='utf-8'))
    coordinates = [{'position': c['position'], 'z': c['time']} for c in data['coordinates']]
    opened = []
    class ZReplay:
        axes = {'position': data['axes']['position'], 'z': data['axes']['time']}
        def __init__(self, path):
            opened.append(path)
        def get_image_coordinates_list(self):
            return coordinates
        def read_metadata(self, **coords):
            return data['metadata'][coordinates.index(coords)]
    monkeypatch.setattr(tools, 'Dataset', ZReplay)
    result = rig.run(protocol='zstack', hook_strategy=hook,
                     protocol_params=dict(z_start_um=0, z_end_um=2, z_step_um=1))
    assert 'error' not in result, result
    assert len(rig.frames) == 6
    timing = result['timing']
    assert timing['verification'] == 'not applicable: acquisition has no time axis'
    assert timing['strategy'] == 'no_time_axis'
    for field in ('observed_per_field_spacing', 'observed_per_field_spacing_meaning',
                  'frame_timestamp_metadata_key', 'frame_timestamp_meaning'):
        assert timing[field] is None
    assert not opened


@pytest.mark.parametrize('order,meaning,gap', [
    ('position_then_time', 'consecutive frames within one field\'s movie', .01),
    ('time_then_position', 'revisit interval spanning the other fields\' exposures', .02),
])
def test_spacing_meaning_follows_resolved_order(rig, monkeypatch, order, meaning, gap):
    class Replay:
        axes = {'position': ['A', 'B'], 'time': [0, 1, 2]}
        def __init__(self, path):
            pass
        def get_image_coordinates_list(self):
            return [{'position': p, 'time': t} for p in self.axes['position'] for t in self.axes['time']]
        def read_metadata(self, position, time):
            # Every camera frame is 10 ms apart; only field ordering differs.
            p = self.axes['position'].index(position)
            index = p * 3 + time if order == 'position_then_time' else time * 2 + p
            return {'ElapsedTime-ms': index * 10}
    monkeypatch.setattr(tools, 'Dataset', Replay)
    result = rig.run(hook_strategy='snr_observer', acquisition_order=order)
    assert 'error' not in result, result
    assert result['acquisition_order'] == order
    timing = result['timing']
    assert timing['observed_per_field_spacing'] == {'A': [gap, gap], 'B': [gap, gap]}
    assert timing['observed_per_field_spacing_meaning'] == meaning


@pytest.mark.parametrize('hook', [None, 'snr_observer'])
@pytest.mark.parametrize('tile', [False, True])
def test_composite_breakdown_exports_compile_and_execute(rig, tmp_path, monkeypatch, hook, tile):
    from tests.test_session_script_export import export, completed_call
    kw = {**rig.params, 'hook_strategy': hook,
          'protocol_params': dict(n_frames=1, interval_s=.5)}
    tool = 'run_multiposition_acquisition'
    if tile:
        tool = 'run_tile_acquisition'
        kw.pop('positions')
        kw.update(rows=1, cols=2, step_um=10, center_x_um=5, center_y_um=0)
    result = getattr(tools, tool)(rig.ctrl, rig.guard, **kw)
    assert 'error' not in result, result
    assert 'duration_breakdown' in result, 'composite has no duration_breakdown'
    assert result['duration_s'] == round(result['duration_s'], 6)
    assert result['duration_breakdown']['phases']['acquisition']['count'] == 2
    assert result['duration_breakdown']['accounted_s'] + result['duration_breakdown']['unaccounted_s'] == pytest.approx(result['duration_s'])
    result['duration_breakdown']['measurement_sentinel'] = 'duration-must-not-be-emitted'
    _, report, source = export(tmp_path, completed_call(tool, kw, result))
    assert report['emitted_calls'] == 1
    assert 'duration-must-not-be-emitted' not in source
    ast.parse(source)
    live = list(rig.frames)
    rig.now = 0.; rig.xy = (0., 0.); rig.frames = []; rig.acquisitions = []
    import pycromanager
    monkeypatch.setattr(pycromanager, 'Acquisition', tools.Acquisition)
    monkeypatch.setattr(pycromanager, 'Core', lambda: rig.ctrl.core)
    exec(source, {'__file__': str(tmp_path / 'routine.py')})
    assert rig.frames == live
    assert len(rig.acquisitions) == 2


# Every grid shape a real caller of run_multiposition_acquisition can produce,
# with whether each field gets its own acquisition. None of them can authorize
# property_envelope or named_stage_envelope — run_multiposition_acquisition
# accepts neither, _protocol_shape_kwargs refuses both inside protocol_params,
# and the 77b split loop passes neither to _configure_hook_capabilities. So a
# reachable grid never restores hook-held hardware, never repaints and never
# times a write: its restoration span is the no-op sweep finish_owned_cleanup
# always measures, and acquisition and restoration are its only phases.
REACHABLE_GRIDS = [(None, 0., True), (None, .5, True),
                   ('snr_observer', .5, True), ('snr_observer', 0., False)]


@pytest.mark.parametrize('hook,interval,per_field', REACHABLE_GRIDS)
@pytest.mark.parametrize('n', [4, 8])
def test_composite_breakdown_measures_every_reachable_field(
        rig, monkeypatch, hook, interval, per_field, n):
    """One composite breakdown per grid, and its residual is the stage motion."""
    monkeypatch.setattr(tools.time, 'monotonic', lambda: rig.now)
    kw = {'positions': [dict(name=f'P{i}', x_um=i * 10., y_um=0.) for i in range(n)],
          'protocol_params': dict(n_frames=2, interval_s=interval)}
    if hook:
        kw['hook_strategy'] = hook
    result = rig.run(**kw)
    assert 'error' not in result, result
    assert 'duration_breakdown' in result, 'composite has no duration_breakdown'
    b = result['duration_breakdown']
    assert len(rig.acquisitions) == (n if per_field else 1)
    assert set(b['phases']) == {'acquisition', 'restoration'}
    for phase in b['phases'].values():
        assert phase['count'] == len(rig.acquisitions)
    if per_field:
        # N short windows with the settles between them, which is the per-field
        # multiplier the composite exists to make visible.
        assert b['phases']['acquisition']['max_s'] < rig.settle
    else:
        # One window over the whole grid, with the settles inside it.
        assert b['phases']['acquisition']['max_s'] == pytest.approx(b['duration_s'])
    assert 'dominant_phase' not in b
    assert b['clock'] == 'time.monotonic'
    assert b['duration_s'] == result['duration_s'] == round(rig.now, 6)
    assert b['accounted_s'] + b['unaccounted_s'] == pytest.approx(result['duration_s'])
    assert b['unaccounted_s'] == b['duration_s'] - b['accounted_s']
    # The residual is the between-field work the composite owns: one settled
    # move per field boundary when each field is its own acquisition, and none
    # when the engine moves the stage inside a single acquisition window.
    assert b['unaccounted_s'] == pytest.approx((n - 1) * rig.settle if per_field else 0.)
    assert 'unaccounted_s includes between-field work (XY moves, settling, preflight, mkdir)' \
        in b['phase_meaning']
    assert 'per-field breakdowns are folded into this composite and omitted from child results' \
        in b['phase_meaning']
    assert all('duration_breakdown' not in child for child in result.get('results', ()))


def test_composite_breakdown_payload_is_bounded_in_field_count(rig):
    """Constant additional storage: fixed phase names, at most three records."""
    import json
    sizes = []
    for n in (2, 500):
        result = rig.run(positions=[dict(name=f'P{i}', x_um=i * 10., y_um=0.)
                                    for i in range(n)],
                         protocol_params=dict(n_frames=1, interval_s=0))
        assert 'error' not in result, result
        assert 'duration_breakdown' in result, 'composite has no duration_breakdown'
        b = result['duration_breakdown']
        assert set(b['phases']) == {'acquisition', 'restoration'}
        assert b['phases']['acquisition']['count'] == n
        assert b['slowest_records'] == []
        assert 'dominant_phase' not in b
        assert all('duration_breakdown' not in child for child in result['results'])
        assert b['accounted_s'] + b['unaccounted_s'] == pytest.approx(result['duration_s'])
        sizes.append(len(json.dumps(b)))
    print(f'79c composite breakdown bytes: 2 fields={sizes[0]}, 500 fields={sizes[1]}')
    assert max(sizes) < 1500
    assert max(sizes) - min(sizes) < 100


def test_composite_breakdown_survives_a_hook_failure_return(rig, monkeypatch):
    """The split loop's early error return still carries the composite's timing."""
    from microclaw import hooks as hooks_module
    real = hooks_module.compute_stats
    def failing_analysis(image, **kwargs):
        if len(rig.frames) >= 3:
            raise RuntimeError('injected analysis failure')
        return real(image, **kwargs)
    monkeypatch.setattr(hooks_module, 'compute_stats', failing_analysis)
    result = rig.run(hook_strategy='snr_observer',
                     positions=[dict(name=f'P{i}', x_um=i * 10., y_um=0.) for i in range(4)],
                     protocol_params=dict(n_frames=2, interval_s=.5))
    assert result['error'] == 'injected analysis failure'
    assert len(rig.acquisitions) == 2
    assert 'duration_breakdown' in result, 'composite has no duration_breakdown'
    b = result['duration_breakdown']
    # The field that failed is folded too: its acquisition window is real time.
    assert b['phases']['acquisition']['count'] == 2
    assert b['accounted_s'] + b['unaccounted_s'] == pytest.approx(result['duration_s'])
    assert 'dominant_phase' not in b
