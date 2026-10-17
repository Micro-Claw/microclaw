import ast
import json
import threading
from types import SimpleNamespace

import pytest
from microclaw import tools
from microclaw.safety import SafetyGuard
from tests.mda_fakes import Manager, Studio, Core, static_class


@pytest.fixture
def rig(monkeypatch, tmp_path, unconstrained_guard):
    manager = Manager()
    ctrl = SimpleNamespace(studio=Studio(manager), core=Core(), _port=4827)
    from microclaw import controller
    monkeypatch.setattr(controller, '_new_static_java_class', static_class)
    monkeypatch.setattr(tools, 'CONFIRM_FN', lambda *a, **k: True)
    monkeypatch.setattr(tools, '_mda_index_complete', manager.index_complete)
    monkeypatch.setattr(tools, '_mda_poll_wait', lambda _: None)
    return ctrl, manager, unconstrained_guard, dict(n_frames=2, interval_s=0,
        exposure_ms=10, name='run', save_dir=str(tmp_path))


def test_fresh_builder_and_no_unnecessary_work(rig):
    ctrl, manager, guard, settings = rig
    manager.settings.values.update(use_slices=True, use_position_list=True, use_channels=True)
    result = tools.run_mda(ctrl, guard, settings=settings)
    assert result['outcome'] == 'completed'
    assert result['writer_completion']['state'] == 'proved'
    assert result['dataset_path'] == '/data/actual_suffix'
    assert result['settings_source'] == 'datastore summary metadata'
    assert manager.calls.count('start') == 1
    assert manager.calls.count('builder') == 1
    assert 'settings' not in manager.calls
    assert ctrl.core.exposures == [10]
    for key in ('use_slices', 'use_channels', 'use_position_list', 'use_autofocus',
                'use_custom_intervals', 'is_test_acquisition'):
        assert manager.settings.values[key] is False
    assert result['duration_breakdown']['accounted_s'] >= 0


def test_frozen_is_not_flush(rig):
    ctrl, manager, guard, settings = rig
    manager.observations = [(True, 1, False, False), (False, 2, True, False), (False, 2, True, True)]
    result = tools.run_mda(ctrl, guard, settings=settings)
    assert result['writer_completion']['state'] == 'proved'
    assert manager.index_reads == 2
    assert manager.calls.count('poll') == 3


def install_clock(monkeypatch, manager, step=1000):
    now = [0.]
    monkeypatch.setattr(tools, '_acquisition_monotonic', lambda: now[0])
    def join(worker, _):
        # Synchronize with the fake bridge entry rather than measuring throughput.
        assert manager.entered.wait(5)
        now[0] += step
    monkeypatch.setattr(tools, '_join_acquisition_waiter', join)


@pytest.mark.parametrize('block', ['start', 'poll', 'abort'])
def test_hung_call_keeps_one_worker_and_refuses_without_probe(rig, monkeypatch, block):
    ctrl, manager, guard, settings = rig
    manager.block = block
    cancel = threading.Event()
    if block == 'abort': cancel.set()
    install_clock(monkeypatch, manager)
    result = json.loads(tools.execute_tool('run_mda', {'settings': settings}, ctrl, guard, cancel=cancel))
    assert result.get('acquisition') == 'unterminated', result
    assert result['phase'] == {'start': 'starting', 'poll': 'acquiring', 'abort': 'stopping'}[block]
    waiter = ctrl._microclaw_unterminated_acquisition['waiter']
    assert waiter.is_alive()
    before = list(manager.calls)
    refused = json.loads(tools.execute_tool('run_timelapse', {}, ctrl, guard))
    assert refused['acquisition'] == 'refused'
    assert manager.calls == before
    assert ctrl.core.probes == 0
    assert manager.calls.count('start') == 1
    if block == 'start': assert 'poll' not in manager.calls
    if block == 'poll': assert manager.calls.count('poll') == 1
    manager.release.set(); waiter.join(5)
    assert not waiter.is_alive()
    if block == 'abort': assert manager.calls.count('abort') == 1


@pytest.mark.parametrize('interrupt', [False, True])
def test_cancel_continues_until_completion_once(rig, monkeypatch, interrupt):
    ctrl, manager, guard, settings = rig
    cancel = threading.Event()
    manager.block = 'poll'
    if not interrupt: cancel.set()
    def join(worker, seconds):
        assert manager.entered.wait(5)
        manager.release.set()
        if interrupt and not cancel.is_set():
            cancel.set()
            raise KeyboardInterrupt
        worker.join(5)
    monkeypatch.setattr(tools, '_join_acquisition_waiter', join)
    # Ensure a poll after the first blocked call, so interrupt reaches worker.
    manager.observations = [(True, 0, False, False)] * 10 + [(False, 2, True, True)]
    result = json.loads(tools.execute_tool('run_mda', {'settings': settings}, ctrl, guard,
                                         cancel=None if interrupt else cancel))
    assert result['outcome'] == 'cancelled', result
    assert manager.calls.count('abort') == 1
    assert manager.calls[-1] == 'poll'
    assert result['frames_accounted'] == 2


def test_preview_save_mode_invalidates(rig):
    ctrl, manager, guard, _ = rig
    preview = tools.get_mda_settings(ctrl, guard)
    manager.settings.values['save_mode'] = 'MULTIPAGE_TIFF'
    result = tools.run_mda(ctrl, guard, preview_token=preview['preview_token'])
    assert 'changed' in result['error']
    assert 'start' not in manager.calls


@pytest.mark.parametrize('route', ['settings', 'preview'])
def test_install_admission(rig, route):
    ctrl, manager, guard, settings = rig
    ctrl._microclaw_extension_install = {'running': True}
    with pytest.raises(RuntimeError, match='install admission'):
        tools.run_mda(ctrl, guard, **({'settings': settings} if route == 'settings' else {'preview_token': 'x'}))
    assert not manager.calls


def test_frozen_never_flushed_expires_and_reservation_stays_owned(rig, monkeypatch):
    ctrl, manager, guard, settings = rig
    manager.observations = [(False, 2, True, False)]
    tick = threading.Event(); advance = threading.Event(); release = threading.Event()
    now = [0.]
    monkeypatch.setattr(tools, '_acquisition_monotonic', lambda: now[0])
    def pause(_):
        tick.set()
        advance.wait(); advance.clear()
        if release.is_set(): manager.observations = [(False, 2, True, True)]
    monkeypatch.setattr(tools, '_mda_poll_wait', pause)
    def join(worker, _):
        assert tick.wait(5); tick.clear()
        now[0] += 1000
        # leave worker parked at expiry so pending ownership is observable
    monkeypatch.setattr(tools, '_join_acquisition_waiter', join)
    result = json.loads(tools.execute_tool('run_mda', {'settings': settings}, ctrl, guard))
    assert result.get('acquisition') == 'unterminated', result
    assert result['phase'] == 'writer_finalizing'
    assert result['writer_completion']['state'] == 'not_reached'
    assert result['outcome'] == 'unterminated'
    waiter = ctrl._microclaw_unterminated_acquisition['waiter']
    assert tools._acquisition_ledger(ctrl).in_flight
    release.set(); advance.set(); waiter.join(5)
    assert not waiter.is_alive()
    assert not tools._acquisition_ledger(ctrl).in_flight


@pytest.mark.parametrize('first', [True, False])
def test_planned_long_startup_and_interval_do_not_false_expire(rig, monkeypatch, first):
    ctrl, manager, guard, settings = rig
    import queue
    settings.update(n_frames=2, interval_s=2000)
    manager.observations = ([(True, 0, False, False), (True, 0, False, False),
                             (False, 2, True, True)] if first else
                            [(True, 1, False, False), (True, 1, False, False),
                             (False, 2, True, True)])
    now = [0.]; joins = [0]
    ticks = queue.Queue(); advance = queue.Queue()
    def pause(_):
        ticks.put(True)
        advance.get(timeout=5)
    def join(worker, _):
        if joins[0]: advance.put(True)
        if joins[0] < 2:
            ticks.get(timeout=5)
            now[0] += 1500  # exceeds the quiet floor and then the runtime ceiling
        else:
            worker.join(5)
        joins[0] += 1
    monkeypatch.setattr(tools, '_mda_poll_wait', pause)
    monkeypatch.setattr(tools, '_join_acquisition_waiter', join)
    monkeypatch.setattr(tools, '_acquisition_monotonic', lambda: now[0])
    try:
        result = tools.run_mda(ctrl, guard, settings=settings)
        assert result['outcome'] == 'completed'
        assert result['frames_accounted'] == 2
        assert joins[0] >= 2
    finally:
        advance.put(True); advance.put(True)


@pytest.mark.parametrize('changes', [{'n_frames': 0}, {'interval_s': -1},
    {'exposure_ms': 0}, {'unknown': 1}, {'save_mode': 'BAD'}, {'interval_s': float('nan')}])
def test_invalid_settings_precede_builder_and_exposure(rig, changes):
    ctrl, manager, guard, settings = rig
    with pytest.raises(ValueError): tools.run_mda(ctrl, guard, settings={**settings, **changes})
    assert not manager.calls
    assert not ctrl.core.exposures


def test_emitter_routes_and_missing_record(rig):
    from microclaw.tools import CannotEmit
    _, _, _, settings = rig
    with pytest.raises(CannotEmit, match='dialog'):
        tools._emit_mda(tools.RecordedParams({'preview_token': 'x'}, {'status': 'done'}))
    with pytest.raises(CannotEmit, match='recorded result'):
        tools._emit_mda(tools.RecordedParams({'settings': settings}))


@pytest.mark.parametrize('mode', ['complete', 'interrupt', 'bound', 'frozen'])
def test_emitted_source_executes_shared_supervisor(rig, monkeypatch, mode):
    import builtins
    ctrl, manager, guard, settings = rig
    result = tools.run_mda(ctrl, guard, settings=settings)
    monkeypatch.undo()  # emit product source, not the live test's patched dependencies
    source = tools._emit_mda(tools.RecordedParams(
        {'settings': settings, '_export_safety_limits': {'exposure_ms': (0, 100)}}, result))
    ast.parse(source)
    assert 'from microclaw' not in source and 'import microclaw' not in source
    manager = Manager()
    manager.block = 'start' if mode == 'bound' else 'poll'
    manager.observations = ([(False, 2, True, False)] if mode == 'frozen' else
                            [(True, 1, False, False), (False, 2, True, True)])
    now = [0.]; interrupted = [False]; workers = []
    paused = threading.Event(); release_pause = threading.Event()
    class Event:
        def __init__(self): self.event = threading.Event()
        def set(self): self.event.set()
        def is_set(self): return self.event.is_set()
        def wait(self, seconds):
            if mode == 'frozen':
                paused.set(); release_pause.wait()
            # no real sleeping: the scripted clock drives finalization expiry
    class Thread:
        def __init__(self, **kwargs):
            self.worker = threading.Thread(**kwargs); workers.append(self.worker)
        def start(self): self.worker.start()
        def is_alive(self): return self.worker.is_alive()
        def join(self, seconds):
            assert manager.entered.wait(5)
            if mode == 'interrupt' and not interrupted[0]:
                interrupted[0] = True
                raise KeyboardInterrupt
            if mode == 'bound':
                now[0] += 2000
            elif mode == 'frozen':
                manager.release.set()
                assert paused.wait(5)
                now[0] += 2000
            else:
                manager.release.set(); self.worker.join(5)
    class Reader:
        def __init__(self, path):
            assert manager.observation[2]
            self.index = {i: None for i in range(2 if manager.observation[3] else 1)}
        def close(self): pass
    original_import = builtins.__import__
    def importing(name, *args, **kwargs):
        if name == 'pycromanager': return SimpleNamespace(Studio=lambda: Studio(manager), JavaClass=lambda cls, port: static_class(port, cls))
        if name == 'ndstorage': return SimpleNamespace(Dataset=Reader)
        if name == 'threading': return SimpleNamespace(Thread=Thread, Event=Event, Lock=threading.Lock)
        if name == 'time': return SimpleNamespace(perf_counter=lambda: now[0])
        return original_import(name, *args, **kwargs)
    scope = {'core': ctrl.core, '__builtins__': {**vars(builtins), '__import__': importing}}
    try:
        if mode in ('bound', 'frozen'):
            with pytest.raises(RuntimeError, match='unterminated'):
                exec(compile(source, '<mda-export>', 'exec'), scope)
            assert workers[0].is_alive()
        else:
            exec(compile(source, '<mda-export>', 'exec'), scope)
            assert scope['_mda_state']['outcome'] == ('cancelled' if mode == 'interrupt' else 'completed')
            assert manager.calls.count('abort') == (1 if mode == 'interrupt' else 0)
            assert scope['_mda_state']['writer_completion']['state'] == 'proved'
    finally:
        manager.observations = [(False, 2, True, True)]
        manager.release.set(); release_pause.set()
        for worker in workers: worker.join(5)


@pytest.mark.parametrize('route', ['settings', 'dialog'])
def test_route_authorization_refuses_before_start(rig, route):
    from microclaw.authorization import AuthorizationEntry
    ctrl, manager, guard, settings = rig
    # The same excluded acquisition-tool row that refuses a timelapse must
    # refuse typed MDA; dialog still goes through its separate excluded path.
    ctrl.authorization_map = SimpleNamespace(entries=[AuthorizationEntry(
        path='acquisition-tool:run_mda' if route == 'settings' else 'mmstudio-mda',
        classification='excluded')])
    inputs = {'settings': settings} if route == 'settings' else {'preview_token': 'x'}
    result = json.loads(tools.execute_tool('run_mda', inputs, ctrl, guard))
    assert 'excluded' in result['error']
    assert 'start' not in manager.calls


@pytest.mark.parametrize('mode', ['MULTIPAGE_TIFF', 'SINGLEPLANE_TIFF_SERIES'])
def test_other_save_modes_disclose_unverified_flush(rig, mode):
    ctrl, manager, guard, settings = rig
    result = tools.run_mda(ctrl, guard, settings={**settings, 'save_mode': mode})
    assert result['outcome'] == 'completed'
    assert result['writer_completion'] == {'state': 'frozen-unverified', 'flush': 'unverified',
                                         'reason': 'save is off or save mode is not ND_TIFF'}
    assert manager.index_reads == 0


def test_reservation_closes_on_decline_and_start_failure(rig, monkeypatch):
    ctrl, manager, guard, settings = rig
    monkeypatch.setattr(tools, 'CONFIRM_FN', lambda *a, **k: False)
    result = tools.run_mda(ctrl, guard, settings=settings)
    assert 'declined' in result['error']
    assert not tools._acquisition_ledger(ctrl).in_flight
    assert 'start' not in manager.calls
    monkeypatch.setattr(tools, 'CONFIRM_FN', lambda *a, **k: True)
    def fail(*args): raise RuntimeError('Start rejected')
    monkeypatch.setattr(manager, 'run_acquisition_with_settings', fail)
    result = tools.run_mda(ctrl, guard, settings=settings)
    assert result['outcome'] == 'failed'
    assert result['writer_completion']['state'] == 'not_reached'
    assert not tools._acquisition_ledger(ctrl).in_flight


def test_summary_settings_fallback_is_labelled(rig, monkeypatch):
    ctrl, manager, guard, settings = rig
    def unavailable(): raise RuntimeError('summary unavailable')
    monkeypatch.setattr(manager.store, 'get_summary_metadata', unavailable)
    result = tools.run_mda(ctrl, guard, settings=settings)
    assert result['settings_source'] == 'manager current settings fallback'
    assert result['settings_that_ran']['num_frames'] == 2
    assert manager.calls.count('settings') == 1


@pytest.mark.parametrize('inputs', [{}, {'settings': {}, 'preview_token': 'x'}])
def test_routes_are_mutually_exclusive_before_bridge(rig, inputs):
    ctrl, manager, guard, _ = rig
    with pytest.raises(ValueError, match='Exactly one'):
        tools.run_mda(ctrl, guard, **inputs)
    assert not manager.calls


def test_required_save_dir_precedes_builder(rig):
    ctrl, manager, guard, settings = rig
    del settings['save_dir']
    with pytest.raises(ValueError, match='save_dir'):
        tools.run_mda(ctrl, guard, settings=settings)
    assert not manager.calls


def test_failed_abort_still_supervises_engine_and_writer(rig, monkeypatch):
    ctrl, manager, guard, settings = rig
    cancel = threading.Event(); cancel.set()
    def abort():
        manager.calls.append('abort')
        raise RuntimeError('Stop request failed')
    monkeypatch.setattr(manager, 'abort_acquisition', abort)
    result = json.loads(tools.execute_tool('run_mda', {'settings': settings}, ctrl, guard, cancel=cancel))
    assert result['outcome'] == 'failed'
    assert manager.calls.count('abort') == 1
    assert manager.calls.count('poll') == 2
    assert result['writer_completion']['state'] == 'proved'
    assert result['frames_accounted'] == 2
    assert not tools._acquisition_ledger(ctrl).in_flight
