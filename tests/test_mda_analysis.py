import json
import threading
from types import SimpleNamespace

import pytest
from microclaw import tools, completed_dataset
from tests.test_mda_supervision import rig, install_clock

ATTACH = dict(adapter='lab/pkg:observe', release_digest='a' * 64, parameters={})


@pytest.fixture
def observer(monkeypatch):
    made = []
    snapshots = []
    # Record every raw call, including the finally safety net. The real job
    # suppresses repeated outcomes; this fake must expose extra deliveries.
    class Job:
        def __init__(self, prepared, dataset):
            self.dataset = dataset
            self.thread = threading.get_ident()
            self.events = []
            self.metadata = {'package': 'lab/pkg'}
            self.handle = SimpleNamespace(job_id='job', metrics_snapshot=self.snapshot)
            made.append(self)
        def snapshot(self, **kwargs):
            snapshots.append(kwargs)
            return dict(declared={'density': dict(unit='counts', description='Density')},
                        latest={'density': dict(value=3, frames=[0, 8])},
                        feed_alive=True, lost_frames=False)
        def acquisition_finished(self, outcome):
            self.events.append(outcome)
        def writer_finished(self): self.events.append('writer')
        def record(self): return dict(dataset=self.dataset, events=self.events)
    monkeypatch.setattr(completed_dataset, 'prepare_package_analysis', lambda **kw: {})
    monkeypatch.setattr(completed_dataset, 'AcquisitionAnalysisJob', Job)
    return made, snapshots


def execute(rig, **kw):
    ctrl, _, guard, settings = rig
    return json.loads(tools.execute_tool('run_mda', dict(settings=settings, analysis=ATTACH),
                                        ctrl, guard, **kw))


def test_completed_binding_lifecycle_metrics(rig, observer):
    made, snapshots = observer
    events = []
    result = execute(rig, acquisition_event_sink=events.append)
    assert result['outcome'] == 'completed', result
    assert len(made) == 1
    assert made[0].dataset == '/data/actual_suffix'
    assert made[0].thread != threading.get_ident()
    assert made[0].events == ['completed', 'writer', 'cancelled']
    assert result['analysis']['jobs'][0]['dataset'] == '/data/actual_suffix'
    assert 'analysis_dispatch' in result['duration_breakdown']['phases']
    progress = [e for e in events if 'analysis_metrics' in e]
    assert len(progress) == 1
    assert snapshots == [{'for_progress': True}]
    assert progress[0]['analysis_metrics'] == dict(job_id='job', package_id='pkg',
        values={'density': dict(value=3, frames=[0, 8], unit='counts', description='Density')},
        feed_alive=True, lost_frames=False)


def test_stop_midrun(rig, observer, monkeypatch):
    cancel = threading.Event()
    monkeypatch.setattr(tools, '_mda_poll_wait', lambda _: cancel.set())
    result = execute(rig, cancel=cancel)
    assert result['outcome'] == 'cancelled'
    assert observer[0][0].events == ['cancelled', 'writer', 'cancelled']
    assert rig[1].calls.count('abort') == 1


@pytest.mark.parametrize('mode', ['MULTIPAGE_TIFF', 'SINGLEPLANE_TIFF_SERIES'])
def test_tiff_refused_before_consent(rig, monkeypatch, mode):
    rig[3]['save_mode'] = mode
    confirms = []
    monkeypatch.setattr(tools, 'CONFIRM_FN', lambda *a, **k: confirms.append(a))
    result = execute(rig)
    assert 'ND_TIFF' in result['error']
    assert confirms == [] and rig[1].calls == []


def test_feedback_refused_before_analysis_consent(rig, monkeypatch):
    confirms = []
    monkeypatch.setattr(tools, 'CONFIRM_FN', lambda *a, **k: confirms.append(a))
    ctrl, manager, guard, settings = rig
    result = json.loads(tools.execute_tool('run_mda', dict(settings=settings,
        analysis=ATTACH, analysis_feedback={}), ctrl, guard))
    assert 'analysis_feedback' in result['error']
    assert confirms == [] and manager.calls == []


@pytest.mark.parametrize('failure', ['null', 'raise'])
def test_failed_start_no_dataset(rig, observer, monkeypatch, failure):
    def start(*args):
        if failure == 'raise': raise RuntimeError('failed start')
    monkeypatch.setattr(rig[1], 'run_acquisition_with_settings', start)
    result = execute(rig)
    assert result['outcome'] == 'failed'
    assert observer[0] == []
    assert result['analysis']['jobs'] == []
    assert 'No datastore save path' in result['analysis']['reason']


def test_unterminated_no_writer(rig, observer, monkeypatch):
    manager = rig[1]
    manager.observations = [(True, 1, False, False)]
    manager.block = 'poll'
    # Block only the second poll, after binding the job.
    original = manager.is_acquisition_running
    def poll():
        if observer[0]: manager.blocked('poll')
        return original() if not observer[0] else True
    manager.block = None
    def pause(_):
        manager.block = 'poll'
    monkeypatch.setattr(manager, 'is_acquisition_running', poll)
    monkeypatch.setattr(tools, '_mda_poll_wait', pause)
    install_clock(monkeypatch, manager)
    try:
        result = execute(rig)
        assert result['acquisition'] == 'unterminated'
        assert observer[0][0].events == ['unterminated', 'cancelled']
    finally:
        manager.observations = [(False, 2, True, True)]
        monkeypatch.setattr(manager, 'is_acquisition_running', original)
        manager.release.set()
        pending = getattr(rig[0], '_microclaw_unterminated_acquisition', {})
        if pending: pending['done'].wait(5)


def test_no_analysis_no_observer_work(rig, observer, monkeypatch):
    events = []
    notifications = []
    original = tools._supervise_mda
    def supervise(*args, **kwargs):
        notify = kwargs['notify']
        def capture(event):
            notifications.append(event['type'])
            return notify(event)
        kwargs['notify'] = capture
        return original(*args, **kwargs)
    monkeypatch.setattr(tools, '_supervise_mda', supervise)
    ctrl, _, guard, settings = rig
    result = json.loads(tools.execute_tool('run_mda', dict(settings=settings), ctrl, guard,
                                        acquisition_event_sink=events.append))
    assert result['outcome'] == 'completed'
    assert observer == ([], [])
    assert not any(name.startswith('analysis_') for name in notifications)
    assert not any(e['type'].startswith('analysis_') or 'analysis_metrics' in e for e in events)
    assert 'analysis_dispatch' not in result['duration_breakdown']['phases']


def test_schema_attachment_rule():
    from microclaw.tools_schema import TOOLS, ANALYSIS_TOOLS
    schema = next(t for t in TOOLS if t['name'] == 'run_mda')
    assert 'run_mda' in ANALYSIS_TOOLS
    assert 'not yet supported' not in schema['description']
    assert 'unavailable' not in schema['description']
    assert 'Only settings.save_mode ND_TIFF' in schema['input_schema']['properties']['analysis']['description']


def test_export_attachment_matches_timelapse(rig):
    import ast
    settings = rig[3]
    params = dict(settings=settings, _export_safety_limits=dict(exposure_ms_max=1000))
    result = dict(outcome='completed', resolved_settings={'root': settings['save_dir']})
    plain = tools._emit_mda(tools.RecordedParams(params, result))
    attached = tools._emit_mda(tools.RecordedParams(dict(params, analysis=ATTACH), result))
    assert plain == attached
    ast.parse(attached)
    assert 'from microclaw' not in attached


def test_dispatch_timing_and_metrics_cadence(rig, observer, monkeypatch):
    now = [0.]
    monkeypatch.setattr(tools, '_acquisition_monotonic', lambda: now[0])
    job_type = completed_dataset.AcquisitionAnalysisJob
    def dispatch(*args):
        now[0] += 0.25
        return job_type(*args)
    monkeypatch.setattr(completed_dataset, 'AcquisitionAnalysisJob', dispatch)
    monkeypatch.setattr(tools, '_mda_poll_wait', lambda _: now.__setitem__(0, now[0] + 0.4))
    rig[1].observations = [(True, i, False, False) for i in range(1, 5)] + [(False, 5, True, True)]
    result = execute(rig)
    assert result['outcome'] == 'completed', result
    phases = result['duration_breakdown']['phases']
    assert phases['analysis_dispatch']['total_s'] == 0.25
    assert phases['acquiring']['total_s'] == pytest.approx(1.6)
    assert len(observer[1]) == 2


def test_delayed_real_path(rig, observer, monkeypatch):
    reads = iter([None, '/data/run_1'])
    monkeypatch.setattr(rig[1].store, 'get_save_path', lambda: next(reads))
    result = execute(rig)
    assert result['outcome'] == 'completed', result
    assert [job.dataset for job in observer[0]] == ['/data/run_1']
    assert observer[0][0].events == ['completed', 'writer', 'cancelled']


def test_worker_baseexception_cancels_job_without_relabelling_mda(rig, observer, monkeypatch):
    class WorkerExit(BaseException):
        pass
    failures = []
    failure_reported = threading.Event()
    def report_failure(args):
        failures.append(args.exc_value)
        failure_reported.set()
    monkeypatch.setattr(threading, 'excepthook', report_failure)
    def interrupt(_):
        assert len(observer[0]) == 1, 'The observer must already be bound'
        raise WorkerExit('worker exited')
    monkeypatch.setattr(tools, '_mda_poll_wait', interrupt)
    events = []
    result = execute(rig, acquisition_event_sink=events.append)
    assert observer[0][0].events == ['cancelled']
    assert 'without an acquisition outcome' in result.get('error', ''), result
    assert 'outcome' not in result
    assert not any(e['type'] == 'acquisition_finished' for e in events)
    assert failure_reported.wait(5)
    assert len(failures) == 1 and isinstance(failures[0], WorkerExit)
    assert not tools._acquisition_ledger(rig[0]).in_flight
