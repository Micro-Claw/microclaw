"""83e-3: real tool boundary, shared consent, and dataset observer isolation."""
import json
import os
import platform
import queue
import statistics
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from microclaw import completed_dataset as cd, skill_store as store, tools
from microclaw.acquisition import AcquisitionLedger
from microclaw.tools_schema import ANALYSIS_SCHEMA, ANALYSIS_TOOLS, TOOLS
from tests.test_completed_dataset import package_analysis
from tests.test_session_script_export import hooked_engine


@pytest.fixture
def live(package_analysis, hooked_engine, monkeypatch, tmp_path):
    args, directory, record = package_analysis
    monkeypatch.setattr('microclaw.authorization.authorize_path', lambda *_: None)
    monkeypatch.setattr(tools, '_authorize_acquisition',
                        lambda ctrl, guard, plan: AcquisitionLedger().reserve(MagicMock(), plan))
    monkeypatch.setattr(tools, '_wait', lambda *_: None)
    ctrl = SimpleNamespace(core=hooked_engine.core)
    analysis = {key: args[key] for key in ('adapter', 'release_digest', 'parameters')}
    def run(**overrides):
        inputs = dict(n_frames=1, interval_s=0, save_dir=str(tmp_path), analysis=analysis)
        inputs.update(overrides)
        return json.loads(tools.execute_tool('run_timelapse', inputs, ctrl, args['guard']))
    return SimpleNamespace(run=run, analysis=analysis, ctrl=ctrl, guard=args['guard'],
                           engine=hooked_engine, directory=directory)


def test_schema_is_shared_and_mda_discloses_boundary():
    selected = {tool['name']: tool for tool in TOOLS if tool['name'] in ANALYSIS_TOOLS}
    assert len(selected) == 6
    assert all(tool['input_schema']['properties']['analysis'] is ANALYSIS_SCHEMA
               for tool in selected.values())
    mda = next(tool for tool in TOOLS if tool['name'] == 'run_mda')
    assert 'analysis' not in mda['input_schema']['properties']
    assert "Micro-Manager's own engine" in mda['description']


@pytest.mark.parametrize('name', sorted(ANALYSIS_TOOLS))
def test_decline_precedes_every_real_tool_body(live, monkeypatch, name):
    prompts = []
    def decline(summary, **kwargs):
        prompts.append((summary, kwargs))
        with store.package_lock('conformance-fixture'):
            pass  # No package lock survives into a confirmation.
        return False
    monkeypatch.setattr(tools, 'CONFIRM_FN', decline)
    result = json.loads(tools.execute_tool(name, {'analysis': live.analysis}, live.ctrl, live.guard))
    assert result == {'error': 'Acquisition cancelled: analysis declined; nothing was acquired.',
                      'cancelled': True}
    assert not live.engine.core.trace and not live.engine.backends
    assert prompts[0][1] == dict(kind='analysis', subject='fixture-lab/conformance-fixture@' + live.analysis['release_digest'])
    assert "The analysis runs at the same time as the acquisition, at below-normal CPU priority. It is not throttled, and it still competes with the acquisition for CPU and disk." in prompts[0][0]
    assert 'each dataset' in prompts[0][0] and 'overflow' in prompts[0][0]
    assert 'Output goes to <dataset>/analysis/<job_id>/ inside each dataset.' in prompts[0][0]


@pytest.mark.parametrize('change', ['remove', 'policy'])
def test_human_wait_invalidates_release_and_policy(live, monkeypatch, change):
    def confirm(*a, **k):
        if change == 'remove':
            (live.directory / 'install.json').unlink()
        else:
            monkeypatch.setattr(store, 'load_trust_policy', lambda: (_ for _ in ()).throw(ValueError('policy changed')))
        return True
    monkeypatch.setattr(tools, 'CONFIRM_FN', confirm)
    result = live.run()
    assert 'error' in result and not live.engine.backends
    assert 'policy changed' in result['error'] if change == 'policy' else 'no ready install' in result['error']


@pytest.mark.parametrize('optional', [{}, {'analysis': None}])
def test_no_analysis_reads_no_store_or_supervisor(live, monkeypatch, tmp_path, optional):
    def forbidden(*a, **k):
        pytest.fail('analysis work without an analysis argument')
    monkeypatch.setattr(store, 'load_trust_policy', forbidden)
    monkeypatch.setattr(store, 'resolve', forbidden)
    monkeypatch.setattr(cd, 'analysis_supervisor', forbidden)
    monkeypatch.setattr(tools, 'CONFIRM_FN', forbidden)
    original = tools.TOOL_REGISTRY['run_timelapse']
    def body(ctrl, guard, **kwargs):
        assert 'analysis' not in kwargs
        return original(ctrl, guard, **kwargs)
    monkeypatch.setitem(tools.TOOL_REGISTRY, 'run_timelapse', body)
    result = json.loads(tools.execute_tool('run_timelapse', dict(
        n_frames=1, interval_s=0, save_dir=str(tmp_path), **optional), live.ctrl, live.guard))
    assert 'error' not in result and 'analysis' not in result
    assert len(live.engine.core.captures) == 1


def test_dispatch_cwd_precedes_submit_later_records_off_thread_and_real_teardown(live, monkeypatch):
    foreground = threading.get_ident()
    written = threading.Event()
    write_threads = []
    original_write = store._write
    def write(path, value):
        if Path(path).parent.name == 'jobs':
            write_threads.append(threading.get_ident())
            if len(write_threads) > 1:
                written.set()
        return original_write(path, value)
    monkeypatch.setattr(store, '_write', write)
    pool = cd.analysis_supervisor()
    original_submit = pool.submit
    handles = []
    def submit(*a, **k):
        assert Path(k['output_dir']).is_dir()
        assert Path(k['output_dir']).parts[-2:] == ('analysis', k['job_id'])
        assert not live.engine.core.captures
        handle = original_submit(*a, **k)
        handles.append(handle)
        return handle
    monkeypatch.setattr(pool, 'submit', submit)
    constructor = tools.Acquisition
    def collision(**kwargs):
        backend = constructor(**kwargs)
        backend._dataset_disk_location += '_7'
        return backend
    monkeypatch.setattr(tools, 'Acquisition', collision)
    result = live.run(name='collision')
    assert 'error' not in result
    job = result['analysis']['jobs'][0]
    assert job['dataset'].endswith('collision_7')
    assert written.wait(5)
    assert write_threads[0] == foreground
    assert all(t != foreground for t in write_threads[1:])
    assert handles[0].wait(5)
    final = handles[0].record()
    assert final['state'] == 'succeeded', final
    assert final['lifecycle'] == {'acquisition': 'completed', 'writer': 'finished'}
    assert len(live.engine.backends[0].saved) == 1
    assert tools._recorded_outcome(result) is None


@pytest.mark.parametrize('failure', ['lock', 'mkdir', 'submit', 'refused', 'queue', 'record'])
def test_dispatch_failures_never_fail_acquisition(live, monkeypatch, failure):
    pool = cd.analysis_supervisor()
    locked = None
    write_failed = threading.Event()
    if failure == 'queue':
        monkeypatch.setattr(pool._queue, 'put_nowait', lambda *_: (_ for _ in ()).throw(queue.Full()))
    if failure == 'refused':
        original_submit = pool.submit
        def refused(*a, **k):
            return original_submit(*a, **dict(k, operation='undeclared'))
        monkeypatch.setattr(pool, 'submit', refused)
    if failure == 'submit':
        monkeypatch.setattr(pool, 'submit', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('refused submit')))
    if failure == 'mkdir':
        original = Path.mkdir
        def mkdir(path, *a, **k):
            if path.parent.name == 'analysis':
                raise OSError('mkdir denied')
            return original(path, *a, **k)
        monkeypatch.setattr(Path, 'mkdir', mkdir)
    if failure == 'record':
        original = store._write
        def write(path, value):
            if Path(path).parent.name == 'jobs':
                write_failed.set()
                raise OSError('record denied')
            return original(path, value)
        monkeypatch.setattr(store, '_write', write)
    original_constructor = tools.Acquisition
    def construct(**kwargs):
        nonlocal locked
        backend = original_constructor(**kwargs)
        if failure == 'lock':
            locked = store.package_lock('conformance-fixture')
            locked.__enter__()  # After consent, at dataset construction.
        if failure == 'record':
            acquire = backend.acquire
            def dispatch(events):
                assert write_failed.wait(5)
                acquire(events)
            backend.acquire = dispatch
        return backend
    monkeypatch.setattr(tools, 'Acquisition', construct)
    try:
        result = live.run()
        assert 'error' not in result, result
        assert tools._recorded_outcome(result) is None
        job = result['analysis']['jobs'][0]
        if failure == 'record':
            assert job['record_failure'] == 'record denied'
        else:
            assert job['state'] in {'dispatch_failed', 'refused'}, job
            assert job['failure']
        assert len(live.engine.core.captures) == 1
    finally:
        if locked:
            locked.__exit__(None, None, None)


def test_malformed_neighbors_and_second_process_sweep_leave_dispatch_alone(live):
    store._write(store.analysis_job_path('e' * 32), {'garbage': True})
    broken = live.directory.parent / 'bad' / 'install.json'
    broken.parent.mkdir()
    broken.write_text('{bad json', encoding='utf-8')
    result = live.run()
    assert 'error' not in result, result
    job = result['analysis']['jobs'][0]
    # Use an explicitly live foreign owner: startup may not claim ownership.
    record = dict(job, state='running', owner={'pid': os.getpid(), 'nonce': 'another-serve'})
    path = store.analysis_job_path('f' * 32)
    store._write(path, dict(record, job_id='f' * 32))
    store.abandon_analysis_jobs()
    assert store.analysis_job_status('f' * 32)['state'] == 'running'
    assert job['digest'] in store.retained_digests()


def test_d8_foreground_dispatch_measurement(live, monkeypatch, capsys, tmp_path):
    foreground = threading.get_ident()
    dispatch = []
    initial_writes = []
    original_write = store._write
    def write(path, value):
        started = time.perf_counter()
        try:
            return original_write(path, value)
        finally:
            if Path(path).parent.name == 'jobs' and threading.get_ident() == foreground:
                initial_writes.append((time.perf_counter() - started) * 1000)
    monkeypatch.setattr(store, '_write', write)
    before_acquire = {False: [], True: []}
    original = cd.AcquisitionAnalysisJob
    def measured(*a, **k):
        assert threading.get_ident() == foreground
        started = time.perf_counter()
        try:
            return original(*a, **k)
        finally:
            dispatch.append((time.perf_counter() - started) * 1000)
    monkeypatch.setattr(cd, 'AcquisitionAnalysisJob', measured)
    constructor = tools.Acquisition
    def construct(**kwargs):
        backend = constructor(**kwargs)
        acquire = backend.acquire
        def measure_acquire(events):
            before_acquire[enabled].append((time.perf_counter() - start) * 1000)
            acquire(events)
        backend.acquire = measure_acquire
        return backend
    monkeypatch.setattr(tools, 'Acquisition', construct)
    for enabled in (False, True):
        for i in range(10):
            inputs = dict(n_frames=1, interval_s=0, save_dir=str(tmp_path), name=f'measure-{enabled}-{i}')
            if enabled:
                inputs['analysis'] = live.analysis
            start = time.perf_counter()
            result = json.loads(tools.execute_tool('run_timelapse', inputs, live.ctrl, live.guard))
            assert 'error' not in result
    assert len(initial_writes) == 10
    with capsys.disabled():
        print(f'83e-3 D8 n=10 per route machine={platform.platform()} '
              f'foreground_dispatch_ms median={statistics.median(dispatch):.3f} max={max(dispatch):.3f}; '
              f'initial_job_write_ms median={statistics.median(initial_writes):.3f} max={max(initial_writes):.3f}; '
              f'call_to_acquire_without_ms median={statistics.median(before_acquire[False]):.3f} max={max(before_acquire[False]):.3f}; '
              f'call_to_acquire_with_ms median={statistics.median(before_acquire[True]):.3f} max={max(before_acquire[True]):.3f}; '
              'automatic consent; dispatch includes mkdir, package lock, submit, initial atomic write, recorder startup; '
              'individual phases and concurrent CPU/storage contention not attributed; no real microscope or human wait')


@pytest.mark.parametrize('hooked,where', [(False, 'exit'), (True, 'exit'), (False, 'acquire')])
def test_engine_and_hooked_failures_keep_jobs_and_writer_truth(live, monkeypatch, hooked, where):
    # Keep the observer alive until lifecycle notifications are delivered.
    live.analysis['parameters'] = {'behaviour': 'lifecycle'}
    jobs = []
    original_job = cd.AcquisitionAnalysisJob
    def track(*a, **k):
        job = original_job(*a, **k)
        jobs.append(job)
        return job
    monkeypatch.setattr(cd, 'AcquisitionAnalysisJob', track)
    original_constructor = tools.Acquisition
    def construct(**kwargs):
        backend = original_constructor(**kwargs)
        def fail(*_):
            raise RuntimeError('engine failed')
        if where == 'acquire':
            backend.acquire = fail
        else:
            # Python special methods are resolved on the class, not instance.
            monkeypatch.setattr(type(backend), '__exit__', fail)
        return backend
    monkeypatch.setattr(tools, 'Acquisition', construct)
    options = {}
    if hooked:
        monkeypatch.setattr(tools, '_resolve_hook', lambda *a, **k: SimpleNamespace())
        monkeypatch.setattr(tools, '_configure_hook_capabilities', lambda *a, **k: None)
        monkeypatch.setattr(tools, '_plan_with_hook_dose', lambda plan, *a, **k: plan)
        monkeypatch.setattr(tools, '_prepare_log_path', lambda *a, **k: None)
        options['hook_strategy'] = 'fixture'
    result = live.run(**options)
    assert 'error' in result, result
    assert len(result['analysis']['jobs']) == 1
    from tests.test_skill_supervisor import wait_until
    wait_until(lambda: any(item['type'] == 'acquisition' and item['state'] != 'pending'
                          for item in jobs[0].handle.record()['notifications']), jobs[0].handle.record)
    messages = jobs[0].handle.record()['notifications']
    entry = next(item for item in messages if item['type'] == 'acquisition')
    record = jobs[0].handle.record()
    assert entry['state'] == 'delivered', dict(notifications=messages,
        state=record['state'], failure=record['failure'], stderr_tail=record['stderr_tail'])
    message = entry['message']
    assert message['outcome'] == 'failed'
    assert message['writer'] == ('finished' if where == 'exit' else 'unknown')
    if where == 'acquire':
        jobs[0].handle.notify_writer_finished()
    else:
        # No separate writer notification is valid when acquisition already says finished.
        jobs[0].handle.cancel()


def test_per_position_jobs_and_per_call_collector(live, tmp_path):
    live.guard._c.stage = live.engine.guard._c.stage
    live.ctrl.studio = MagicMock()
    live.ctrl.studio.live().is_live_mode_on.return_value = False
    inputs = dict(protocol='timelapse', save_dir=str(tmp_path),
                  positions=[dict(name=f'p{i}', x_um=i, y_um=0) for i in range(3)],
                  protocol_params=dict(n_frames=1, interval_s=0), analysis=live.analysis)
    result = json.loads(tools.execute_tool('run_multiposition_acquisition', inputs, live.ctrl, live.guard))
    assert 'error' not in result, result
    jobs = result['analysis']['jobs']
    assert len(jobs) == 3
    assert len({job['dataset'] for job in jobs}) == len({job['job_id'] for job in jobs}) == 3
    assert all(job['digest'] == live.analysis['release_digest'] for job in jobs)
    next_result = live.run(name='next-call')
    assert len(next_result['analysis']['jobs']) == 1


@pytest.mark.parametrize('value', [[], {}, {'adapter': 'frame_statistics', 'release_digest': 'a' * 64, 'parameters': {}},
    {'adapter': 'fixture-lab/conformance-fixture:observe_dataset', 'release_digest': 'bad', 'parameters': {}},
    {'adapter': 'fixture-lab/conformance-fixture:observe_dataset', 'release_digest': 'a' * 64, 'parameters': {}, 'unexpected': 1}])
def test_invalid_analysis_refuses_without_consent_or_hardware(live, monkeypatch, value):
    monkeypatch.setattr(tools, 'CONFIRM_FN', lambda *a, **k: pytest.fail('invalid analysis prompted'))
    result = live.run(analysis=value)
    assert 'error' in result
    assert not live.engine.backends and not live.engine.core.trace


def test_malformed_matching_install_does_not_hide_ready_copy(live):
    good = live.directory.parent / 'ffffffffffffffff-ffffff'
    live.directory.rename(good)
    live.directory.mkdir()
    record = store._read(good / 'install.json')
    store._write(live.directory / 'install.json', dict(record, intake=None))
    store._write(live.directory / 'verdict.json', store._read(good / 'verdict.json'))
    result = live.run()
    assert 'error' not in result, result
    assert len(result['analysis']['jobs']) == 1


@pytest.mark.parametrize('route', ['saved', 'acquisition'])
def test_initial_job_record_is_published_under_package_lock(live, package_analysis, monkeypatch, route):
    observed = []
    foreground = threading.get_ident()
    original = store._write
    seen = set()
    def write(path, value):
        if Path(path).parent.name == 'jobs' and str(path) not in seen:
            seen.add(str(path))
            try:
                with store.package_lock('conformance-fixture'):
                    held = False
            except store.PackageRefusal as exc:
                assert exc.field == 'lock'
                held = True
            observed.append((held, threading.get_ident()))
        return original(path, value)
    monkeypatch.setattr(store, '_write', write)
    if route == 'saved':
        args, _, _ = package_analysis
        result = tools.run_analysis_on_saved_dataset(**args)
    else:
        result = live.run()
    assert 'error' not in result, result
    assert observed == [(True, foreground)]


@pytest.mark.parametrize('route', ['saved', 'acquisition'])
def test_dispatch_and_record_write_failures_both_survive(live, package_analysis, monkeypatch, route):
    pool = cd.analysis_supervisor()
    monkeypatch.setattr(pool._queue, 'put_nowait', lambda *_: (_ for _ in ()).throw(queue.Full()))
    original = store._write
    def write(path, value):
        if Path(path).parent.name == 'jobs':
            raise OSError('record denied')
        return original(path, value)
    monkeypatch.setattr(store, '_write', write)
    if route == 'saved':
        args, _, _ = package_analysis
        result = tools.run_analysis_on_saved_dataset(**args)
        job = result['analysis']
    else:
        result = live.run()
        job = result['analysis']['jobs'][0]
    assert 'error' not in result
    assert job['failure']['reason'] == 'queue_full'
    assert job['record_failure'] == 'record denied'
    assert tools._recorded_outcome(result) is None


def test_keyboard_interrupt_notifies_cancelled_unknown_once_and_reraises(live, monkeypatch):
    jobs = []
    original_job = cd.AcquisitionAnalysisJob
    def track(*a, **k):
        job = original_job(*a, **k)
        jobs.append(job)
        return job
    monkeypatch.setattr(cd, 'AcquisitionAnalysisJob', track)
    constructor = tools.Acquisition
    interrupt = KeyboardInterrupt('operator interrupt')
    def construct(**kwargs):
        backend = constructor(**kwargs)
        def acquire(events):
            raise interrupt
        backend.acquire = acquire
        return backend
    monkeypatch.setattr(tools, 'Acquisition', construct)
    with pytest.raises(KeyboardInterrupt) as caught:
        live.run()
    assert caught.value is interrupt
    job = jobs[0]
    job.acquisition_finished('failed')  # The fallback cannot overwrite or duplicate an outcome.
    notifications = job.handle.record()['notifications']
    messages = [entry['message'] for entry in notifications if entry['type'] == 'acquisition']
    assert len(messages) == 1
    assert (messages[0]['outcome'], messages[0]['writer']) == ('cancelled', 'unknown')
    assert not live.engine.core.captures
    job.handle.cancel()


def test_declined_acquisition_exports_as_skipped_not_as_an_acquisition(live, monkeypatch, tmp_path):
    # The result is taken from the real execute_tool decline, never hand-built:
    # a record whose shape the test invents cannot catch a shape the exporter misreads.
    monkeypatch.setattr(tools, 'CONFIRM_FN', lambda *_, **__: False)
    inputs = dict(n_frames=5, interval_s=0, save_dir=str(tmp_path), name='declined',
                  analysis=live.analysis)
    raw = tools.execute_tool('run_timelapse', inputs, live.ctrl, live.guard)
    records = [
        {'role': 'assistant', 'content': [
            {'type': 'tool_use', 'id': 't1', 'name': 'run_timelapse', 'input': inputs},
            {'type': 'tool_use', 'id': 't2', 'name': 'set_exposure', 'input': {'ms': 12}},
        ]},
        {'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': 't1', 'content': raw},
            {'type': 'tool_result', 'tool_use_id': 't2', 'content': json.dumps({'ms': 12})},
        ]},
    ]
    from tests.test_session_script_export import export
    _, _, source = export(tmp_path, records)
    section = source.split('# RECORDED TOOL: run_timelapse', 1)[1].split('# RECORDED TOOL:', 1)[0]
    assert '# SKIPPED: run_timelapse' in section
    assert 'Acquisition(' not in section and 'acquire(' not in section
    assert 'core.set_exposure(12)' in source


def test_job_disclosure_names_the_rerun_fields_not_the_whole_record(live, monkeypatch, tmp_path):
    # From a real dispatch, so the record carries everything the supervisor adds
    # (notifications, status, result). A demo-machine export printed all of it,
    # 2,167 characters on one comment line, including a snapshot `state` that
    # read "running" for a job that succeeded moments later.
    monkeypatch.setattr(tools, 'CONFIRM_FN', lambda *_, **__: True)
    inputs = dict(n_frames=1, interval_s=0, save_dir=str(tmp_path), name='shown', analysis=live.analysis)
    raw = tools.execute_tool('run_timelapse', inputs, live.ctrl, live.guard)
    job = json.loads(raw)['analysis']['jobs'][0]
    records = [
        {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 't1', 'name': 'run_timelapse', 'input': inputs}]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': raw}]},
    ]
    from tests.test_session_script_export import export
    _, _, source = export(tmp_path, records)
    [line] = [l for l in source.splitlines() if l.startswith('# Package analysis not reproduced:')]
    for key in ('package', 'digest', 'operation', 'parameters', 'dataset', 'output_dir',
                'job_id', 'job_record_path'):
        assert f'{key}=' in line, key
    assert job['job_id'] in line and job['digest'] in line
    for absent in ('notifications', 'status', 'state', 'owner', 'protocol'):
        assert f'{absent}=' not in line and f"'{absent}'" not in line, absent


@pytest.mark.parametrize('desktop', [True, False])
def test_84a_live_window_consent_and_desktop_refusal(live, monkeypatch, desktop):
    import socket
    from microclaw.skill_supervisor import Supervisor
    live.analysis['adapter'] = 'fixture-lab/conformance-fixture:window_worker'
    monkeypatch.setattr(cd, '_analysis_supervisor', Supervisor(desktop_probe=lambda: desktop))
    monkeypatch.setattr('microclaw.skill_supervisor.interactive_desktop', lambda: desktop)
    calls = []
    def decline(summary, **kwargs):
        calls.append((summary, kwargs))
        assert socket.gethostname() in summary
        assert 'stays open after the analysis ends' in summary and 'quit MicroClaw' in summary
        assert kwargs['subject'] == 'fixture-lab/conformance-fixture@' + live.analysis['release_digest'] + '+window'
        return False
    monkeypatch.setattr(tools, 'CONFIRM_FN', decline)
    result = live.run()
    if desktop:
        assert result['cancelled'] is True and len(calls) == 1
    else:
        assert not calls
        assert 'cannot show one' in result['error']
    assert not live.engine.backends


def test_84a_revision_live_window_persists_handoff(live, monkeypatch):
    from microclaw.skill_supervisor import Supervisor
    from tests.test_completed_dataset import terminal_analysis
    sup = Supervisor(desktop_probe=lambda: True)
    monkeypatch.setattr(cd, '_analysis_supervisor', sup)
    monkeypatch.setattr('microclaw.skill_supervisor.interactive_desktop', lambda: True)
    monkeypatch.setattr(tools, 'CONFIRM_FN', lambda *a, **k: True)
    live.analysis.update(adapter='fixture-lab/conformance-fixture:window_worker',
                         parameters={'behaviour': 'window_hang'})
    result = live.run()
    assert 'error' not in result
    job_id = result['analysis']['jobs'][0]['job_id']
    terminal_analysis(job_id)
    persisted = json.loads(store.analysis_job_path(job_id).read_text(encoding='utf-8'))
    assert persisted['window_retained'] is True and persisted['exit_code'] is None
    assert live.analysis['release_digest'] in store.retained_digests()
    assert sup._windows[job_id][1].poll() is None


def test_86a_acquisition_failure_after_worker_exit_is_undelivered(live, monkeypatch):
    jobs = []
    original_job = cd.AcquisitionAnalysisJob
    def track(*args, **kwargs):
        job = original_job(*args, **kwargs)
        jobs.append(job)
        return job
    monkeypatch.setattr(cd, 'AcquisitionAnalysisJob', track)
    constructor = tools.Acquisition
    def construct(**kwargs):
        backend = constructor(**kwargs)
        def fail(*_):
            # Default conformance success exits without reading lifecycle messages.
            assert jobs[0].handle.wait(8), jobs[0].handle.record()
            raise RuntimeError('engine failed after observer exit')
        backend.acquire = fail
        return backend
    monkeypatch.setattr(tools, 'Acquisition', construct)
    sent = []
    from microclaw.skill_supervisor import JobHandle
    notify = JobHandle.notify_acquisition
    def capture(handle, outcome, *, writer):
        sent.append((outcome, writer))
        return notify(handle, outcome, writer=writer)
    monkeypatch.setattr(JobHandle, 'notify_acquisition', capture)
    result = live.run()
    assert 'engine failed after observer exit' in result['error']
    assert len(result['analysis']['jobs']) == 1
    record = jobs[0].handle.record()
    assert record['state'] == 'succeeded', record
    assert record['failure'] is None
    assert sent == [('failed', 'unknown')]
    assert record['notifications'] == [dict(type='acquisition', state='undelivered', reason='worker_exited')]


def install_metrics_fixture(live, fixture):
    """An installed signed release, following the same offline store convention."""
    import shutil
    import sys
    from tests.test_skill_supervisor import release
    source = release(fixture)
    package_id = source['manifest']['package_id']
    record = dict(source, state='ready', artifact_digest=source['intake']['artifact_digest'],
                  python=sys.executable, interpreter={'test': 'current'}, install_id='0'*16+'-000000')
    record.pop('release_dir')
    directory = store._package(package_id) / 'installs' / record['install_id']
    shutil.copytree(source['release_dir'], directory / 'release')
    store._write(directory / 'install.json', record)
    store._write(directory / 'verdict.json', dict(build=store.current_build(),
                 interpreter=record['interpreter'], eligible=True, reasons=[]))
    store._write(directory.parent.parent / 'pointer.json', dict(active=record['install_id'], previous=None))
    from tests.test_skill_packages import NOW, trust_file
    store.store_trust_policy(trust_file('telemetry-policy'))
    store._write(directory.parent.parent / 'discovery.json',
                 store._discovery_decision(True, record['artifact_digest'], now=NOW, source='install'))
    return dict(adapter=f"{source['manifest']['publisher']}/{package_id}:observe_dataset",
                release_digest=record['artifact_digest'], parameters={}), source


@pytest.mark.parametrize('fixture,name,unit,lines,expected', [
    ('metrics', 'flux', 'photons', [dict(frames=[0, 0], values={'flux': [10]}),
                                  dict(frames=[1, 99], values={'flux': [0]*99})], .1),
    ('weather', 'temperature', 'bytes', [dict(frames=[0, 9], values={'temperature': 23})], 23),
])
def test_87_both_packages_discovery_status_result_live_without_saved_frames(
        live, monkeypatch, tmp_path, fixture, name, unit, lines, expected):
    from tests.test_skill_supervisor import wait_status, wait_until
    from tests.test_completed_dataset import terminal_analysis
    analysis, source = install_metrics_fixture(live, fixture)
    # Both weighted messages deliberately arrive in the same reception second.
    from tests.test_skill_supervisor import metrics_clock
    metrics_clock(monkeypatch, [100.1]*len(lines), snapshot_seconds=[102.5, 103.5, 104.5])
    analysis['parameters'] = dict(lines=lines, lifecycle=True)
    discovery = store.discovery_text()
    assert name in discovery and unit in discovery and source['manifest']['package_id'] in discovery
    loaded = tools.load_skill(live.ctrl, live.guard,
                              f"{source['manifest']['publisher']}/{source['manifest']['package_id']}/workflow")
    assert loaded['operations'][1]['metrics'] == source['manifest']['operations'][1]['metrics']
    for entry in loaded['operations'][1]['metrics'].values():
        assert entry['description'] not in discovery
    jobs = []
    original_job = cd.AcquisitionAnalysisJob
    def bind(*a, **k):
        job = original_job(*a, **k)
        jobs.append(job)
        wait_status(job.handle)
        return job
    monkeypatch.setattr(cd, 'AcquisitionAnalysisJob', bind)
    release_exit = threading.Event()
    original_constructor = tools.Acquisition
    def construct(**kwargs):
        backend = original_constructor(**kwargs)
        original_exit = type(backend).__exit__
        def exit(self, *a):
            assert release_exit.wait(8), 'clock-driven progress did not arrive'
            return original_exit(self, *a)
        monkeypatch.setattr(type(backend), '__exit__', exit)
        return backend
    monkeypatch.setattr(tools, 'Acquisition', construct)
    events = []
    def sink(event):
        if 'analysis_metrics' in event:
            events.append((time.monotonic(), event))
            if len(events) == 3:
                release_exit.set()
    inputs = dict(n_frames=1, interval_s=0, save_dir=str(tmp_path), analysis=analysis)
    try:
        result = json.loads(tools.execute_tool('run_timelapse', inputs, live.ctrl, live.guard,
                                              acquisition_event_sink=sink))
    finally:
        release_exit.set()
    assert 'error' not in result, result
    assert len(events) == 3
    assert all(b[0]-a[0] >= 1 for a, b in zip(events, events[1:]))
    assert all(event['frames_accounted'] == 0 for _, event in events)
    for expected_age, (_, event) in zip((2.4, 3.4, 4.4), events):
        metrics = event['analysis_metrics']
        assert metrics['feed_alive'] and not metrics['lost_frames']
        assert metrics['values'][name]['value'] == expected
        assert metrics['values'][name]['unit'] == unit
        assert 'received_monotonic' not in metrics['values'][name]
        assert metrics['values'][name]['age_s'] == pytest.approx(expected_age)
        assert 'received_at' in metrics['values'][name]
        assert metrics['values'][name]['frames'] == [lines[0]['frames'][0], lines[-1]['frames'][1]]
    record = result['analysis']['jobs'][0]
    assert name in record['metrics']['latest']
    durable = terminal_analysis(record['job_id'])
    status = tools.analysis_job_status(live.ctrl, live.guard, record['job_id'])['analysis']['metrics']
    assert status == durable['metrics']
    assert status['history'][-1]['values'][name] == expected
    assert not status['feed_alive']


@pytest.mark.parametrize('route', ['saved', 'acquisition'])
def test_87_periodic_persistence_is_on_recorder_thread(live, monkeypatch, tmp_path, route):
    from tests.test_skill_supervisor import wait_status
    from tests.test_completed_dataset import terminal_analysis
    analysis, _ = install_metrics_fixture(live, 'weather')
    gate = tmp_path / 'gate'
    analysis['parameters'] = dict(lines=[dict(frames=[0, 0], values={'temperature': 20})], gate=str(gate))
    writes = []
    recorded = threading.Event()
    original_write = store._write
    def write(path, value):
        if Path(path).parent.name == 'jobs':
            writes.append((threading.current_thread().name, time.monotonic(), value))
            assert not threading.current_thread().name.startswith('skill-pipe-')
        original_write(path, value)
        if (Path(path).parent.name == 'jobs' and value.get('metrics', {}).get('received')
                and value['state'] == 'running'):
            recorded.set()
    monkeypatch.setattr(store, '_write', write)
    prepared = cd.prepare_package_analysis(analysis['adapter'], analysis['release_digest'], analysis['parameters'], disclosure=[])
    if route == 'acquisition':
        job = cd.AcquisitionAnalysisJob(prepared, tmp_path / 'acquisition')
        handle = job.handle
        wait_status(handle)
        job_id = handle.job_id
    else:
        result = cd.run_package_analysis(live.guard, str(tmp_path), analysis['adapter'],
                                        analysis['release_digest'], analysis['parameters'], str(tmp_path / 'out87'))
        assert 'error' not in result, result
        job_id = result['analysis']['job_id']
    try:
        assert recorded.wait(8), writes
        periodic = [(name, t, r) for name, t, r in writes if r.get('metrics', {}).get('received')]
        assert periodic and all(name.startswith('analysis-record-') for name, _, _ in periodic)
        assert periodic[0][1] - writes[0][1] >= 4.9
        assert tools.analysis_job_status(live.ctrl, live.guard, job_id)['analysis']['metrics']['received'] == 1
    finally:
        gate.touch()
    durable = terminal_analysis(job_id)
    assert durable['metrics']['received'] == 1 and not durable['metrics']['feed_alive']


def test_87_review_legacy_status_has_no_metrics(live):
    from tests.test_completed_dataset import terminal_analysis
    result = live.run()
    assert 'error' not in result, result
    record = result['analysis']['jobs'][0]
    assert 'metrics' not in record
    terminal_analysis(record['job_id'])
    assert 'metrics' not in tools.analysis_job_status(live.ctrl, live.guard, record['job_id'])['analysis']
