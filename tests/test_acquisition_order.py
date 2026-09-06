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
            r.frames.append((label, axes['time'], r.now))
            metadata = {'Axes': dict(axes), 'PositionName': label,
                        'FrameIndex': axes['time'],
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
