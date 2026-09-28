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
    assert result == {'status': 'Acquisition cancelled: analysis declined', 'cancelled': True}
    assert not live.engine.core.trace and not live.engine.backends
    assert prompts[0][1] == dict(kind='analysis', subject='fixture-lab/conformance-fixture@' + live.analysis['release_digest'])
    assert 'each dataset' in prompts[0][0] and 'overflow' in prompts[0][0]


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


def test_no_analysis_reads_no_store_or_supervisor(live, monkeypatch, tmp_path):
    def forbidden(*a, **k):
        pytest.fail('analysis work without an analysis argument')
    monkeypatch.setattr(store, 'load_trust_policy', forbidden)
    monkeypatch.setattr(store, 'resolve', forbidden)
    monkeypatch.setattr(cd, 'analysis_supervisor', forbidden)
    result = json.loads(tools.execute_tool('run_timelapse', dict(
        n_frames=1, interval_s=0, save_dir=str(tmp_path)), live.ctrl, live.guard))
    assert 'error' not in result and 'analysis' not in result
    assert len(live.engine.core.captures) == 1


def test_dispatch_cwd_precedes_submit_records_off_thread_and_real_teardown(live, monkeypatch):
    foreground = threading.get_ident()
    written = threading.Event()
    original_write = store._write
    def write(path, value):
        if Path(path).parent.name == 'jobs':
            assert threading.get_ident() != foreground
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
            assert job['failure']['reason'] == 'record_write_failed'
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
    with capsys.disabled():
        print(f'83e-3 D8 n=10 per route machine={platform.platform()} '
              f'foreground_dispatch_ms median={statistics.median(dispatch):.3f} max={max(dispatch):.3f}; '
              f'call_to_acquire_without_ms median={statistics.median(before_acquire[False]):.3f} max={max(before_acquire[False]):.3f}; '
              f'call_to_acquire_with_ms median={statistics.median(before_acquire[True]):.3f} max={max(before_acquire[True]):.3f}; '
              'automatic consent; dispatch includes mkdir, package lock, submit, recorder startup; '
              'individual phases and concurrent CPU/storage contention not attributed; no real microscope or human wait')


@pytest.mark.parametrize('hooked,where', [(False, 'exit'), (True, 'exit'), (False, 'acquire')])
def test_engine_and_hooked_failures_keep_jobs_and_writer_truth(live, monkeypatch, hooked, where):
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
    messages = jobs[0].handle.record()['notifications']
    message = next(item['message'] for item in messages if item['type'] == 'acquisition')
    assert message['outcome'] == 'failed'
    assert message['writer'] == ('finished' if where == 'exit' else 'unknown')
    if where == 'acquire':
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


@pytest.mark.parametrize('value', [None, [], {}, {'adapter': 'frame_statistics', 'release_digest': 'a' * 64, 'parameters': {}},
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
