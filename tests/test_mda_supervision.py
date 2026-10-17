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
    def wait(done, _):
        # Synchronize with the fake bridge entry rather than measuring throughput.
        assert manager.entered.wait(5)
        now[0] += step
    monkeypatch.setattr(tools, '_wait_mda_completion', wait)


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
    def wait(done, seconds):
        assert manager.entered.wait(5)
        manager.release.set()
        if interrupt and not cancel.is_set():
            cancel.set()
            raise KeyboardInterrupt
        done.wait(5)
    monkeypatch.setattr(tools, '_wait_mda_completion', wait)
    # Ensure a poll after the first blocked call, so interrupt reaches worker.
    manager.observations = [(True, 0, False, False)] * 10 + [(False, 2, True, True)]
    result = json.loads(tools.execute_tool('run_mda', {'settings': settings}, ctrl, guard,
                                         cancel=None if interrupt else cancel))
    assert result['outcome'] == 'cancelled', result
    assert manager.calls.count('abort') == 1
    assert manager.calls[-1] == 'poll'
    assert result['frames_accounted'] == 2


def test_install_admission(rig):
    ctrl, manager, guard, settings = rig
    ctrl._microclaw_extension_install = {'running': True}
    with pytest.raises(RuntimeError, match='install admission'):
        tools.run_mda(ctrl, guard, settings=settings)
    assert not manager.calls


@pytest.mark.parametrize('save_mode', ['ND_TIFF', 'MULTIPAGE_TIFF'])
def test_frozen_never_flushed_expires_and_reservation_stays_owned(rig, monkeypatch, save_mode):
    ctrl, manager, guard, settings = rig
    settings['save_mode'] = save_mode
    stack = None
    if save_mode == 'MULTIPAGE_TIFF':
        from tests.test_mda_tiff import write_stack
        from pathlib import Path
        stack = write_stack(Path(settings['save_dir']) / 'run_MMStack_Pos0.ome.tif')
        closed_size = stack.stat().st_size
        # Small preallocation substitute; never allocate the real 4 GiB.
        preallocated = 4 * 1024 * 1024
        monkeypatch.setattr(tools, '_MDA_MULTIPAGE_PREALLOCATED_BYTES', preallocated)
        with stack.open('r+b') as stream: stream.truncate(preallocated)
        monkeypatch.setattr(manager.store, 'get_save_path', lambda: settings['save_dir'])
    manager.observations = [(False, 2, True, False)]
    tick = threading.Event(); advance = threading.Event(); release = threading.Event()
    now = [0.]
    monkeypatch.setattr(tools, '_acquisition_monotonic', lambda: now[0])
    def pause(_):
        tick.set()
        advance.wait(); advance.clear()
        if release.is_set():
            if stack is not None:
                with stack.open('r+b') as stream: stream.truncate(closed_size)
            manager.observations = [(False, 2, True, True)]
    monkeypatch.setattr(tools, '_mda_poll_wait', pause)
    def wait(done, _):
        assert tick.wait(5); tick.clear()
        now[0] += 1000
        # leave worker parked at expiry so pending ownership is observable
    monkeypatch.setattr(tools, '_wait_mda_completion', wait)
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
    def wait(done, _):
        if joins[0]: advance.put(True)
        if joins[0] < 2:
            ticks.get(timeout=5)
            now[0] += 1500  # exceeds the quiet floor and then the runtime ceiling
        else:
            done.wait(5)
        joins[0] += 1
    monkeypatch.setattr(tools, '_mda_poll_wait', pause)
    monkeypatch.setattr(tools, '_wait_mda_completion', wait)
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
    with pytest.raises(CannotEmit, match='recorded result'):
        tools._emit_mda(tools.RecordedParams({'settings': settings}))


@pytest.mark.parametrize('mode,save_mode', [
    *[(mode, 'ND_TIFF') for mode in ('complete', 'interrupt', 'bound', 'frozen', 'over_limit')],
    ('complete', 'MULTIPAGE_TIFF'), ('complete', 'SINGLEPLANE_TIFF_SERIES')])
def test_emitted_source_executes_shared_supervisor(rig, monkeypatch, tmp_path, capsys, mode, save_mode):
    import builtins
    ctrl, manager, guard, settings = rig
    if save_mode == 'ND_TIFF':
        settings['save_dir'] = str(tmp_path / 'mda-root')
    settings['save_mode'] = save_mode
    if save_mode == 'MULTIPAGE_TIFF':
        from tests.test_mda_tiff import write_stack
        write_stack(tmp_path / 'run_MMStack_Pos0.ome.tif')
    elif save_mode == 'SINGLEPLANE_TIFF_SERIES':
        import numpy as np
        import tifffile
        position = tmp_path / 'Default'
        position.mkdir()
        for frame in range(2):
            tifffile.imwrite(position / f'img_channel000_position000_time{frame:09}_z000.tif',
                             np.zeros((512, 512), np.uint16), byteorder='>')
    if save_mode != 'ND_TIFF':
        monkeypatch.setattr(manager.store, 'get_save_path', lambda: str(tmp_path))
    result = tools.run_mda(ctrl, guard, settings=settings)
    if save_mode == 'ND_TIFF':
        from pathlib import Path
        Path(settings['save_dir']).rmdir()  # exported script must recreate its missing root
    monkeypatch.undo()  # emit product source, not the live test's patched dependencies
    from tests.test_session_script_export import Guard, completed_call
    export_guard = Guard(tmp_path)
    export_guard._c.camera.max_exposure_ms = 5 if mode == 'over_limit' else 15
    exported = tools.export_session_script(None, export_guard, 'mda.py',
        completed_call('run_mda', {'settings': settings}, result))
    assert 'error' not in exported
    source = (tmp_path / 'mda.py').read_text(encoding="utf-8")
    ast.parse(source)
    assert 'from microclaw' not in source and 'import microclaw' not in source
    ctrl.core._creation_port = 5839
    ports = []
    manager = Manager()
    if save_mode != 'ND_TIFF':
        monkeypatch.setattr(manager.store, 'get_save_path', lambda: str(tmp_path))
    manager.block = 'start' if mode == 'bound' else 'poll'
    manager.observations = ([(False, 2, True, False)] if mode == 'frozen' else
                            [(True, 1, False, False), (False, 2, True, True)])
    now = [0.]; interrupted = [False]; workers = []
    paused = threading.Event(); release_pause = threading.Event()
    events = []
    class Event:
        def __init__(self):
            self.event = threading.Event()
            events.append(self)
        def set(self): self.event.set()
        def is_set(self): return self.event.is_set()
        def wait(self, seconds):
            if self is events[1]:  # completion event: drive the foreground wait
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
                    manager.release.set()
                    self.event.wait(5)
            elif mode == 'frozen':
                paused.set(); release_pause.wait()
            return self.event.is_set()
    class Thread:
        def __init__(self, **kwargs):
            self.worker = threading.Thread(**kwargs); workers.append(self.worker)
        def start(self): self.worker.start()
        def is_alive(self): return self.worker.is_alive()
        def join(self, seconds):
            raise AssertionError('MDA foreground must not join its worker')
    class Reader:
        def __init__(self, path):
            assert manager.observation[2]
            self.index = {i: None for i in range(2 if manager.observation[3] else 1)}
        def close(self): pass
    original_import = builtins.__import__
    def importing(name, *args, **kwargs):
        if name == 'pycromanager': return SimpleNamespace(Core=lambda: ctrl.core, Acquisition=object, multi_d_acquisition_events=lambda **kw: [], Studio=lambda port: (ports.append(('studio', port)) or Studio(manager)), JavaClass=lambda cls, port: (ports.append(('enum', port)) or static_class(port, cls)))
        if name == 'ndstorage': return SimpleNamespace(Dataset=Reader)
        if name == 'threading': return SimpleNamespace(Thread=Thread, Event=Event, Lock=threading.Lock)
        if name == 'time': return SimpleNamespace(perf_counter=lambda: now[0])
        return original_import(name, *args, **kwargs)
    if save_mode == 'SINGLEPLANE_TIFF_SERIES':
        import tifffile
        def no_tiff_open(*args, **kwargs):
            raise AssertionError('Emitted single-plane verifier must use stat only')
        monkeypatch.setattr(tifffile, 'TiffFile', no_tiff_open)
    scope = {'__file__': str(tmp_path / 'mda.py'), '__builtins__': {**vars(builtins), '__import__': importing}}
    try:
        if mode == 'over_limit':
            with pytest.raises(Exception, match='exceeds recorded maximum'):
                exec(compile(source, '<mda-export>', 'exec'), scope)
            assert not manager.calls
        elif mode in ('bound', 'frozen'):
            with pytest.raises(RuntimeError, match='unterminated'):
                exec(compile(source, '<mda-export>', 'exec'), scope)
            assert workers[0].is_alive()
        else:
            exec(compile(source, '<mda-export>', 'exec'), scope)
            assert scope['_mda_state']['outcome'] == ('cancelled' if mode == 'interrupt' else 'completed')
            assert manager.calls.count('abort') == (1 if mode == 'interrupt' else 0)
            assert scope['_mda_state']['writer_completion']['state'] == 'proved'
        assert ports == ([] if mode == 'over_limit' else [('studio', 5839), ('enum', 5839)])
        if mode != 'over_limit':
            assert scope['_mda_image_bytes'] == 16 * 16 * 2
        assert scope['_LIMITS']['exposure_ms'] == (0.0, export_guard._c.camera.max_exposure_ms)
        assert 'MDA envelope:' in capsys.readouterr().out
    finally:
        manager.observations = [(False, 2, True, True)]
        manager.release.set(); release_pause.set()
        for worker in workers: worker.join(5)


def test_mda_authorization_refuses_before_start(rig):
    from microclaw.authorization import AuthorizationEntry
    ctrl, manager, guard, settings = rig
    # The same excluded acquisition-tool row that refuses a timelapse must
    # refuse MDA before any hardware effect.
    ctrl.authorization_map = SimpleNamespace(entries=[AuthorizationEntry(
        path='acquisition-tool:run_mda',
        classification='excluded')])
    inputs = {'settings': settings}
    result = json.loads(tools.execute_tool('run_mda', inputs, ctrl, guard))
    assert 'excluded' in result['error']
    assert 'start' not in manager.calls


@pytest.mark.parametrize('mode,label', [
    ('MULTIPAGE_TIFF', 'verified_multipage_tiff_closed'),
    ('SINGLEPLANE_TIFF_SERIES', 'verified_singleplane_files')])
def test_tiff_supervisor_proves_writer(rig, monkeypatch, mode, label):
    from pathlib import Path
    import numpy as np
    import tifffile
    from tests.test_mda_tiff import write_stack
    ctrl, manager, guard, settings = rig
    root = Path(settings['save_dir'])
    if mode == 'MULTIPAGE_TIFF':
        write_stack(root / 'run_MMStack_Pos0.ome.tif')
    else:
        position = root / 'Default'
        position.mkdir()
        for frame in range(2):
            tifffile.imwrite(position / f'img_channel000_position000_time{frame:09}_z000.tif',
                             np.zeros((512, 512), np.uint16), byteorder='>')
    monkeypatch.setattr(manager.store, 'get_save_path', lambda: str(root))
    predicate = tools._mda_writer_complete
    def checked(path, count, save_mode, image_bytes):
        assert manager.frozen_reads and not manager.observation[0]
        return predicate(path, count, save_mode, image_bytes)
    monkeypatch.setattr(tools, '_mda_writer_complete', checked)
    result = tools.run_mda(ctrl, guard, settings={**settings, 'save_mode': mode})
    assert result['outcome'] == 'completed'
    assert result['writer_completion'] == {'state': 'proved', 'flush': label}


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


def test_mda_settings_required_by_schema_and_signature(rig):
    from microclaw.tools_schema import TOOLS
    schema = next(t['input_schema'] for t in TOOLS if t['name'] == 'run_mda')
    assert schema['required'] == ['settings']
    assert set(schema['properties']) == {'settings'}
    ctrl, manager, guard, _ = rig
    with pytest.raises(TypeError, match='settings'):
        tools.run_mda(ctrl, guard)
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
    assert result['outcome'] == 'cancelled'
    assert 'error' not in result
    assert result['diagnostics']['count'] == 1
    assert manager.calls.count('abort') == 1
    assert manager.calls.count('poll') == 2
    assert result['writer_completion']['state'] == 'proved'
    assert result['frames_accounted'] == 2
    assert not tools._acquisition_ledger(ctrl).in_flight


@pytest.mark.parametrize('accessor', ['running', 'count', 'path'])
def test_transient_observation_never_aborts_and_completed_run_exports(rig, monkeypatch, tmp_path, accessor):
    from tests.test_session_script_export import completed_call, export
    ctrl, manager, guard, settings = rig
    owner, method = (manager, 'is_acquisition_running') if accessor == 'running' else (
        manager.store, 'get_num_images' if accessor == 'count' else 'get_save_path')
    original = getattr(owner, method)
    attempts = [0]
    def observe():
        attempts[0] += 1
        if attempts[0] == 1:
            raise RuntimeError('Transient observation failure')
        return original()
    monkeypatch.setattr(owner, method, observe)
    result = tools.run_mda(ctrl, guard, settings=settings)
    assert manager.calls.count('abort') == 0
    assert result['outcome'] == 'completed'
    assert 'error' not in result
    assert result['diagnostics'] == {'count': 1, 'first': ['RuntimeError: Transient observation failure']}
    # Export the actual session record, including _recorded_outcome filtering.
    monkeypatch.undo()
    _, exported, source = export(tmp_path, completed_call('run_mda', {'settings': settings}, result))
    assert 'SKIPPED' not in source and 'NOT EMITTED: run_mda' not in source
    assert 'run_acquisition_with_settings' in source
    ast.parse(source)


@pytest.mark.parametrize('outcome', ['cancelled', 'unterminated'])
def test_partial_mda_record_refuses_full_plan_export(rig, outcome):
    _, _, _, settings = rig
    with pytest.raises(tools.CannotEmit, match='partial'):
        tools._emit_mda(tools.RecordedParams({'settings': settings}, {
            'outcome': outcome, 'resolved_settings': {'root': settings['save_dir']}}))


def test_repeated_observation_errors_are_bounded_diagnostics_not_abort(rig, monkeypatch):
    ctrl, manager, guard, settings = rig
    count = [0]
    def observe():
        count[0] += 1
        raise RuntimeError(f'Observation {count[0]} unavailable')
    monkeypatch.setattr(manager, 'is_acquisition_running', observe)
    tick = threading.Event(); advance = threading.Event()
    now = [0.]
    def pause(_):
        tick.set(); advance.wait(); advance.clear()
    def wait(done, _):
        assert tick.wait(5); tick.clear()
        now[0] += 101
        if now[0] < 1200: advance.set()
    monkeypatch.setattr(tools, '_mda_poll_wait', pause)
    monkeypatch.setattr(tools, '_acquisition_monotonic', lambda: now[0])
    monkeypatch.setattr(tools, '_wait_mda_completion', wait)
    # Use a short policy seam so all 12 diagnostics are exercised before expiry.
    monkeypatch.setattr(tools, '_stall_quiet_s', lambda *args: (1200, 'test'))
    try:
        result = json.loads(tools.execute_tool('run_mda', {'settings': settings}, ctrl, guard))
        assert result['acquisition'] == 'unterminated'
        assert result['phase'] == 'acquiring'
        assert manager.calls.count('abort') == 0
        assert result['diagnostics']['count'] >= 12
        assert len(result['diagnostics']['first']) == 8
        assert result['diagnostics']['first'][0] == 'RuntimeError: Observation 1 unavailable'
    finally:
        monkeypatch.setattr(manager, 'is_acquisition_running', lambda: False)
        manager.observation = (False, 2, True, True)
        advance.set()
        pending = getattr(ctrl, '_microclaw_unterminated_acquisition', {})
        if pending: pending['waiter'].join(5)


def test_interrupt_from_watchdog_clock_requests_abort_and_keeps_supervising(rig, monkeypatch):
    ctrl, manager, guard, settings = rig
    manager.block = 'poll'
    interrupted = [False]
    foreground = threading.get_ident()
    joining = [False]
    def clock():
        if threading.get_ident() == foreground and joining[0] and not interrupted[0]:
            interrupted[0] = True
            raise KeyboardInterrupt
        return 0.
    def wait(done, _):
        assert manager.entered.wait(5)
        if not interrupted[0]:
            joining[0] = True  # next foreground clock read is outside wait
        else:
            manager.release.set(); done.wait(5)
    monkeypatch.setattr(tools, '_acquisition_monotonic', clock)
    monkeypatch.setattr(tools, '_wait_mda_completion', wait)
    try:
        try:
            result = tools.run_mda(ctrl, guard, settings=settings)
        except KeyboardInterrupt:
            pytest.fail('Watchdog clock interrupt escaped while the MDA worker was alive')
        assert interrupted[0]
        assert result['outcome'] == 'cancelled'
        assert manager.calls.count('abort') == 1
        assert result['writer_completion']['state'] == 'proved'
        assert not tools._acquisition_ledger(ctrl).in_flight
    finally:
        manager.release.set()


def test_real_interrupt_inside_wait_preserves_live_worker_and_cancels(rig, monkeypatch):
    import _thread
    import sys
    ctrl, manager, guard, settings = rig
    release = threading.Event()
    wait_entered = threading.Event()
    worker = []
    observed = []
    foreground = threading.get_ident()
    original_wait = getattr(tools, '_wait_mda_completion', lambda event, seconds: event.wait(seconds))
    original_join = tools._join_acquisition_waiter
    first = [True]
    def pause(_):
        if not first[0]: return
        first[0] = False
        worker.append(threading.current_thread())
        assert wait_entered.wait(5)
        # Deliver the real interrupt only once the foreground is inside the
        # threading blocking primitive, rather than guessing with a timer.
        for _ in range(10000):
            frame = sys._current_frames()[foreground]
            while frame:
                if frame.f_code.co_filename == threading.__file__ and frame.f_code.co_name in ('wait', '_wait_for_tstate_lock'):
                    _thread.interrupt_main()
                    assert release.wait(5)
                    return
                frame = frame.f_back
            threading.Event().wait(0.001)
        raise AssertionError('Foreground never entered its blocking wait')
    def interrupted_wait(call):
        if observed:
            release.set()  # the supervisor has now latched the abort request
        wait_entered.set()
        try:
            return call()
        except KeyboardInterrupt:
            alive = worker[0].is_alive()
            observed.append(alive)
            assert alive, 'Interrupted Thread.join falsely marked a still-running MDA worker stopped'
            raise
    # The old join seam is used only when this regression is run against f79ed81.
    monkeypatch.setattr(tools, '_join_acquisition_waiter',
        lambda waiter, seconds: interrupted_wait(lambda: original_join(waiter, seconds)))
    monkeypatch.setattr(tools, '_wait_mda_completion',
        lambda done, seconds: interrupted_wait(lambda: original_wait(done, seconds)), raising=False)
    monkeypatch.setattr(tools, '_mda_poll_wait', pause)
    monkeypatch.setattr(tools, '_ACQUISITION_POLL_S', 0.05)
    events = []
    try:
        result = json.loads(tools.execute_tool('run_mda', {'settings': settings}, ctrl, guard,
                                            acquisition_event_sink=events.append))
        assert 'error' not in result, result
        assert observed == [True]
        assert result['outcome'] == 'cancelled'
        assert manager.calls.count('abort') == 1
        assert result['writer_completion']['state'] == 'proved'
        assert any(event['type'] == 'writer_finished' for event in events)
        assert not tools._acquisition_ledger(ctrl).in_flight
        # Cleanup and writer completion were observed before returning. No join
        # was interrupted, so native thread liveness remains trustworthy too.
        worker[0].join(5)
        assert not worker[0].is_alive()
    finally:
        release.set()
        if worker: worker[0].join(5)


def test_mda_rejects_missing_supervision_outcome(rig, monkeypatch):
    ctrl, _, guard, settings = rig
    def incomplete(*args, **kwargs):
        kwargs['close']()
        return {'outcome': None}
    monkeypatch.setattr(tools, '_supervise_mda', incomplete)
    with pytest.raises(RuntimeError, match='without an acquisition outcome'):
        tools.run_mda(ctrl, guard, settings=settings)


def test_mda_emitter_refuses_missing_limits_with_recorded_reason(rig):
    _, _, _, settings = rig
    with pytest.raises(tools.CannotEmit, match='reviewed constraints unavailable'):
        tools._emit_mda(tools.RecordedParams({'settings': settings,
            '_export_safety_limits': None,
            '_export_safety_limits_error': 'reviewed constraints unavailable'},
            {'outcome': 'completed', 'resolved_settings': {'root': settings['save_dir']}}))


def test_mda_inspection_stable_with_fresh_enum_shadows(rig):
    ctrl, manager, guard, settings = rig
    first = tools.get_mda_settings(ctrl, guard)
    second = tools.get_mda_settings(ctrl, guard)
    left, right = manager.settings._save_mode_reads[-2:]
    assert left is not right
    assert str(left) != str(right)
    assert ' object at 0x' in str(left)
    assert left.name() == right.name() == 'ND_TIFF'
    assert first == second
    assert set(first) == {'source', 'settings', 'enabled_axes', 'warning'}
    assert first['settings']['save_mode'] == second['settings']['save_mode'] == 'ND_TIFF'
    result = tools.run_mda(ctrl, guard, settings=settings)
    assert 'error' not in result, result
    assert result['outcome'] == 'completed'
    assert result['settings_that_ran']['save_mode'] == 'ND_TIFF'
    assert manager.calls.count('start') == 1


def test_mda_static_enum_fake_matches_measured_pyjavaz_surface():
    modes = static_class(4827, 'org.micromanager.data.Datastore$SaveMode')
    assert type(modes).__name__ == 'java_lang_Class'
    assert [name for name in dir(modes) if not name.startswith('_')] == ['value_of', 'values']
    with pytest.raises(AttributeError, match="'java_lang_Class' object has no attribute 'ND_TIFF'"):
        getattr(modes, 'ND_TIFF')
    mode = modes.value_of('ND_TIFF')
    assert mode.name() == mode.to_string() == 'ND_TIFF'
    assert type(mode).__str__ is object.__str__
    assert type(mode).__repr__ is object.__repr__
    array = modes.values()
    assert type(array).__name__ == '[Lorg_micromanager_data_Datastore$SaveMode;'
    assert [name for name in dir(array) if not name.startswith('_')] == [
        'clone', 'equals', 'finalize', 'get_class', 'hash_code', 'notify',
        'notify_all', 'register_natives', 'to_string', 'wait']
    assert not any(hasattr(array, name) for name in ('__iter__', '__len__', '__getitem__'))
    with pytest.raises(TypeError): iter(array)
    with pytest.raises(TypeError): len(array)
    with pytest.raises(TypeError): array[0]


def test_mda_default_object_repr_is_unreadable_and_stable(rig):
    ctrl, manager, guard, _ = rig
    manager.settings.values['comment'] = object()
    first = tools.get_mda_settings(ctrl, guard)
    manager.settings.values['comment'] = object()
    second = tools.get_mda_settings(ctrl, guard)
    assert first['settings']['comment'] == second['settings']['comment'] == (
        '<unreadable: default Python object representation>')
    assert first == second
    assert ' object at 0x' not in json.dumps(first['settings'])


def test_mda_enum_name_failure_is_labelled_unreadable(rig, monkeypatch):
    _, manager, _, _ = rig
    class EnumShadow:
        def name(self): raise RuntimeError('Java enum name unavailable')
    monkeypatch.setattr(manager.settings, 'save_mode', lambda: EnumShadow())
    result = tools._read_mda_settings(manager.settings)
    assert result['save_mode'] == '<unreadable: RuntimeError>'
    assert ' object at 0x' not in result['save_mode']


def test_mda_missing_save_root_created_only_after_consent(rig, monkeypatch):
    from pathlib import Path
    ctrl, manager, guard, settings = rig
    root = Path(settings['save_dir']) / 'missing' / 'nested'
    settings['save_dir'] = str(root)
    confirmations = []
    def confirm(*args, **kwargs):
        assert not root.exists(), 'No directory write before acquisition consent'
        confirmations.append(True)
        return True
    monkeypatch.setattr(tools, 'CONFIRM_FN', confirm)
    result = tools.run_mda(ctrl, guard, settings=settings)
    assert result['outcome'] == 'completed', result
    assert root.is_dir()
    assert confirmations == [True]
    assert manager.calls.count('start') == 1
    assert 'modal_dialog' not in manager.calls


@pytest.mark.parametrize('free', [1279, 1280])
def test_mda_low_disk_refuses_before_consent(rig, monkeypatch, free):
    from pathlib import Path
    ctrl, manager, guard, settings = rig
    root = Path(settings['save_dir']) / 'missing'
    settings['save_dir'] = str(root)
    # getTotalMemory is 2 frames * 16 * 16 * 2; Java requires > 1.25 times it.
    monkeypatch.setattr(tools.shutil, 'disk_usage', lambda path: SimpleNamespace(free=free))
    confirmations = []
    monkeypatch.setattr(tools, 'CONFIRM_FN', lambda *a, **k: confirmations.append(True))
    with pytest.raises(tools.SafetyViolation, match='Not enough free disk space'):
        tools.run_mda(ctrl, guard, settings=settings)
    assert not confirmations
    assert not root.exists()
    assert 'start' not in manager.calls
    assert not tools._acquisition_ledger(ctrl).in_flight


def test_mda_unwritable_parent_refuses_before_consent(rig, monkeypatch):
    ctrl, manager, guard, settings = rig
    monkeypatch.setattr(tools.os, 'access', lambda *a: False)
    confirmations = []
    monkeypatch.setattr(tools, 'CONFIRM_FN', lambda *a, **k: confirmations.append(True))
    with pytest.raises(tools.SafetyViolation, match='writable existing parent'):
        tools.run_mda(ctrl, guard, settings=settings)
    assert not confirmations and 'start' not in manager.calls


def test_mda_directory_writability_rechecked_after_consent(rig, monkeypatch):
    ctrl, manager, guard, settings = rig
    checks = iter([True, False])
    monkeypatch.setattr(tools.os, 'access', lambda *a: next(checks))
    result = tools.run_mda(ctrl, guard, settings=settings)
    assert result['outcome'] == 'failed'
    assert 'not writable' in result['error']
    assert 'start' not in manager.calls
    assert not tools._acquisition_ledger(ctrl).in_flight


def test_mda_null_start_is_failed_without_polling(rig, monkeypatch):
    ctrl, manager, guard, settings = rig
    def declined(*args):
        manager.calls.append('start')
        return None
    monkeypatch.setattr(manager, 'run_acquisition_with_settings', declined)
    def no_retry(*args):
        raise RuntimeError('A null datastore must never enter observation retry')
    monkeypatch.setattr(tools, '_mda_poll_wait', no_retry)
    result = tools.run_mda(ctrl, guard, settings=settings)
    assert result['outcome'] == 'failed', result
    assert 'poll' not in manager.calls
    assert 'Micro-Manager declined to start' in result['error']
    assert 'message that needs dismissing' in result['error']
    assert 'poll' not in manager.calls
    assert result['writer_completion']['state'] == 'not_reached'
    assert not tools._acquisition_ledger(ctrl).in_flight


def test_mda_progress_has_planned_frame_count(rig):
    ctrl, manager, guard, settings = rig
    events = []
    result = json.loads(tools.execute_tool('run_mda', {'settings': settings}, ctrl, guard,
                                         acquisition_event_sink=events.append))
    assert result['outcome'] == 'completed'
    progress = [event for event in events if event['type'] == 'acquisition_progress']
    assert progress
    assert [event['frames_accounted'] for event in progress] == [1, 2]
    assert all(event['frames_planned'] == settings['n_frames'] for event in progress)


def test_mda_stop_guidance_and_cancel_banner_are_explicit():
    from microclaw.agent import CANCEL_REASON
    from microclaw.tools_schema import TOOLS
    description = next(tool['description'] for tool in TOOLS if tool['name'] == 'run_mda')
    assert 'Stop in the web UI, or Ctrl-C in the terminal, aborts the MDA mid-run' in description
    assert 'keeps supervising until the dataset is finished' in description
    assert 'MDA was aborted' in CANCEL_REASON
    assert 'any other running step finished first' in CANCEL_REASON
    assert 'last step completed' not in CANCEL_REASON
