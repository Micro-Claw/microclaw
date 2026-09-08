"""79a: bounded measurements, exercised through the operations they describe."""
from types import SimpleNamespace

import pytest

from microclaw import tools, hook_decisions as hd


AXIS = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2,
        0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75,
        2.0, 2.25, 2.5, 2.75, 3.0, 3.25, 3.5, 3.75, 4.0, 4.25, 4.5,
        4.75, 5.0, 10.0, 30.0, 60.0, 300.0, 900.0, float('inf'))


def test_gap_axis_clamps_and_bounds_storage_and_payload():
    assert tools._GAP_HISTOGRAM_UPPER_S == AXIS
    summary = tools._new_gap_summary()
    bins = summary['bins']
    for _ in range(100000):
        tools._record_gap(summary, 3.15)
    payload = tools._gap_summary_payload(summary)
    assert summary['bins'] is bins and len(bins) == len(AXIS)
    assert set(summary) == {'count', 'min_s', 'max_s', 'sum_s', 'bins'}
    assert set(payload) == {'count', 'min_s', 'max_s', 'mean_s', 'median_le_s',
                            'p95_le_s', 'nonzero_histogram'}
    assert payload['nonzero_histogram'] == [{'upper_s': 3.25, 'count': 100000}]
    assert payload['median_le_s'] == payload['p95_le_s'] == 3.15


PHASES = ['validation', 'read_stage_start_position', 'write', 'settle_stage_move']


def stage_probe(monkeypatch, delayed=None, failure=None):
    from microclaw import controller
    now = [100.0]
    calls = []
    def operation(name):
        calls.append(name)
        if name == delayed:
            now[0] += 3
        if name == failure and name != 'read_stage_start_position':
            raise RuntimeError(name + ' failed')
    class Core:
        value = 0
        def get_position(self, device):
            operation('position')
            if failure == 'read_stage_start_position':
                raise RuntimeError('read_stage_start_position failed')
            return self.value
        def set_position(self, device, value):
            operation('write'); self.value = value
        def device_busy(self, device):
            operation('busy'); return False
    def validation(*args, **kwargs): operation('validation')
    core = Core()
    adapter = hd.UntrustedHookAdapter(hd.PLAN_ONLY)
    adapter.configure_named_stage(core=core, guard=SimpleNamespace(check_named_stage=validation),
        device='Axis', min_um=0, max_um=10, max_writes=1, initial_value=0,
        restore='leave', action_plan=None)
    # Real settling implementation; simulated clock advances its actual sleeps.
    monkeypatch.setattr(controller.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(controller.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds + 1e-9))
    for name in ('read_stage_start_position', 'settle_stage_move'):
        original = getattr(controller, name)
        def wrapped(*args, _name=name, _original=original, **kwargs):
            operation(_name)
            return _original(*args, **kwargs)
        monkeypatch.setattr(hd, name, wrapped)
    if failure:
        with pytest.raises(RuntimeError, match=failure + ' failed'):
            adapter._apply_named_stage(hd.MoveNamedStage(1), {})
    else:
        adapter._apply_named_stage(hd.MoveNamedStage(1), {})
    return adapter._log[-1], calls


@pytest.mark.parametrize('delayed', PHASES)
def test_named_stage_spans_attribute_each_phase_without_bridge_calls(monkeypatch, delayed):
    record, calls = stage_probe(monkeypatch, delayed=delayed)
    timing = record['timing']
    assert set(timing) == {'clock', *PHASES}
    assert timing['clock'] == 'time.monotonic'
    durations = {p: timing[p]['end_s'] - timing[p]['start_s'] for p in PHASES}
    assert 3 <= durations[delayed] < 3.2
    assert max(durations, key=durations.get) == delayed
    assert calls == ['validation', 'read_stage_start_position', 'position', 'write',
                     'settle_stage_move', 'busy', 'position', 'busy', 'position',
                     'busy', 'position']


@pytest.mark.parametrize('failure', PHASES)
def test_named_stage_failure_retains_attempted_spans(monkeypatch, failure):
    record, calls = stage_probe(monkeypatch, failure=failure)
    if failure == 'read_stage_start_position':
        assert record['event'] == 'named_stage_start_position_failure'
        assert record['reason'].startswith('start-position read failed before dispatch:')
    timing = record['timing']
    assert set(timing) == {'clock', *PHASES[:PHASES.index(failure) + 1]}
    for p in PHASES[:PHASES.index(failure) + 1]:
        assert timing[p]['end_s'] >= timing[p]['start_s']


@pytest.mark.parametrize('delayed', ['restoration', 'refresh_gui', 'write'])
def test_teardown_and_property_delay_are_distinguishable(monkeypatch, delayed):
    now = [100.0]
    calls = []
    def operation(name):
        calls.append(name)
        if name == delayed:
            now[0] += 3
    class Core:
        value = 'A'
        def set_property(self, *args): operation('write'); self.value = args[-1]
        def wait_for_device(self, *args): operation('wait')
        def get_property(self, *args): operation('read_back'); return self.value
        def get_property_type(self, *args): operation('type'); return 'String'
    ctrl = SimpleNamespace(core=Core(), authorization_map=None,
                           refresh_gui=lambda: operation('refresh_gui'))
    guard = SimpleNamespace(check_illumination=lambda *a, **k: None, resolve_in_workspace=lambda p: p, check_device_property=lambda *a, **k: operation('validation'))
    hook = hd.UntrustedHookAdapter(hd.PLAN_ONLY)
    hook.configure_property(ctrl=ctrl, guard=guard, device='Wheel', property='State',
        allowed_values=('A', 'B'), min_value=None, max_value=None, max_writes=1,
        initial_value='A', restore='leave', action_plan=None)
    original = hook.restore_property
    def restore(): operation('restoration'); return original()
    hook.restore_property = restore
    class Backend:
        _exception = None
        _dataset_disk_location = '/data/run'
        def acquire(self, events):
            applied = hook._apply_property(hd.SetDeviceProperty('B'), {})
            hook._verify_property_actions([applied])
        def __exit__(self, *args): operation('exit')
    # Acquisition's constructor dispatches to a backend; completion is in exit.
    monkeypatch.setattr(tools, 'Acquisition', lambda **kwargs: Backend())
    monkeypatch.setattr(tools.time, 'monotonic', lambda: now[0])
    teardown = {}
    tools._acquire_with_hooks(guard, '/data', 'run', [], hook, ctrl=ctrl,
                             policy=tools.DEFAULT, teardown_timing=teardown)
    spans = {**{p: v for p, v in hook._log[0]['timing'].items() if p != 'clock'},
             **{p: v for p, v in teardown.items() if p != 'clock'}}
    durations = {p: v['end_s'] - v['start_s'] for p, v in spans.items()}
    assert durations[delayed] == 3
    assert max(durations, key=durations.get) == delayed
    assert calls == ['validation', 'write', 'wait', 'read_back', 'type',
                     'exit', 'restoration', 'refresh_gui']
    assert teardown['clock'] == 'time.monotonic'


@pytest.mark.parametrize('n,interval,adaptive,expected', [
    (5, 0, False, True), (5, .001, False, False),
    (200000, .001, False, True), (5, 0, True, False),
])
def test_route_uses_engine_deadlines(n, interval, adaptive, expected):
    timing = tools._single_run_timing(n_frames=n, interval_s=interval,
                                      hook=None, adaptive=adaptive)
    assert timing['time_axis_sequencing_eligible'] is expected
    assert timing['requested_interval_s'] == interval
    assert timing['dispatch'] == ('adaptive_handoff' if adaptive else 'fixed_plan')


@pytest.mark.parametrize('kind', ['timelapse', 'zstack'])
@pytest.mark.parametrize('hooked', [False, True])
def test_run_result_surfaces_route_and_cleanup_without_gui_calls(
    monkeypatch, mock_ctrl, unconstrained_guard, tmp_path, kind, hooked,
):
    class Backend:
        _exception = None
        _dataset_disk_location = str(tmp_path / 'run')
        def acquire(self, events): pass
        def __exit__(self, *args): pass
    monkeypatch.setattr(tools, 'Acquisition', lambda **kwargs: Backend())
    if hooked:
        monkeypatch.setattr(tools, '_resolve_hook', lambda *a, **k: SimpleNamespace())
    kwargs = dict(save_dir=str(tmp_path), hook_strategy='observer' if hooked else None)
    if kind == 'timelapse':
        result = tools.run_timelapse(mock_ctrl, unconstrained_guard, n_frames=2,
                                    interval_s=.5, **kwargs)
    else:
        result = tools.run_zstack(mock_ctrl, unconstrained_guard,
                                 z_start_um=0, z_end_um=1, z_step_um=1, **kwargs)
    assert 'error' not in result
    assert result['timing']['dispatch'] == ('hooked_fixed_plan' if hooked else 'fixed_plan')
    assert result['timing']['strategy'] == ('shared_timepoint_clock' if kind == 'timelapse' else 'no_time_axis')
    assert set(result['teardown_timing']) == {'clock', 'restoration'}
    assert result['duration_s'] >= 0
    assert result['inter_frame_gap_summary']['count'] == 0
    mock_ctrl.refresh_gui.assert_not_called()


def test_disconnected_stage_read_is_recorded_as_pre_dispatch_failure(monkeypatch):
    from microclaw.controller import StageMoveError
    now = [100.0]
    calls = []
    class Core:
        def get_position(self, device):
            calls.append(('get_position', device))
            now[0] += 3
            raise RuntimeError('serial timeout on ' + device)
        def device_busy(self, device):
            calls.append(('device_busy', device))
            raise RuntimeError('serial timeout on ' + device)
        def set_position(self, *args):
            pytest.fail('a disconnected axis must refuse before dispatch')
    adapter = hd.UntrustedHookAdapter(hd.PLAN_ONLY)
    adapter.configure_named_stage(core=Core(),
        guard=SimpleNamespace(check_named_stage=lambda *args: None), device='Axis',
        min_um=0, max_um=10, max_writes=1, initial_value=0, restore='leave', action_plan=None)
    monkeypatch.setattr(tools.time, 'monotonic', lambda: now[0])
    with pytest.raises(StageMoveError) as caught:
        adapter._apply_named_stage(hd.MoveNamedStage(1), {})
    record = adapter._log[-1]
    assert record['event'] == 'named_stage_start_position_failure'
    assert record['decision'] == 'refused'
    assert record['reason'].startswith('start-position read failed before dispatch:')
    assert 'serial timeout on Axis' in record['reason']
    assert record['start_um'] is caught.value.result['start_um'] is None
    assert record['arrival_unverifiable'] is True
    assert set(record['timing']) == {'clock', 'validation', 'read_stage_start_position'}
    span = record['timing']['read_stage_start_position']
    # The dependency also attempts its diagnostic position read after failure.
    assert span['end_s'] - span['start_s'] == 6
    assert calls == [('get_position', 'Axis'), ('get_position', 'Axis'), ('device_busy', 'Axis')]


def test_m5_quantile_bounds_use_maximum_not_mean_or_minimum():
    summary = tools._new_gap_summary()
    for gap in (3.11, 3.11, 3.15, 3.203):
        tools._record_gap(summary, gap)
    payload = tools._gap_summary_payload(summary)
    assert payload['nonzero_histogram'] == [{'upper_s': 3.25, 'count': 4}]
    assert payload['median_le_s'] == payload['p95_le_s'] == 3.203
    assert payload['mean_s'] == pytest.approx(3.14325)
    assert payload['min_s'] == 3.11


def test_write_timing_summary_bounds_aggregation_and_slowest_records():
    import json
    import tracemalloc
    def records(count):
        for i in range(count):
            yield {'hook_event_index': i, 'timing': {'clock': 'time.monotonic',
                'validation': {'start_s': 0, 'end_s': 1},
                'wait': {'start_s': 1, 'end_s': 1 + i % 10}}}
    tracemalloc.start()
    try:
        result = tools._adaptive_result('/data', '/log', hook=SimpleNamespace(_log=records(100000)))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert 'hardware_write_records' not in result
    summary = result['hardware_write_timing_summary']
    assert summary['record_count'] == 100000
    assert summary['clock'] == 'time.monotonic'
    assert summary['phases'] == {
        'validation': {'count': 100000, 'min_s': 1, 'mean_s': 1, 'max_s': 1, 'total_s': 100000},
        'wait': {'count': 100000, 'min_s': 0, 'mean_s': 4.5, 'max_s': 9, 'total_s': 450000},
    }
    assert len(summary['slowest_records']) == 3
    assert [r['hook_event_index'] for r in summary['slowest_records']] == [9, 19, 29]
    assert len(json.dumps(result)) < 3000
    assert peak < 1000000, 'aggregation must not copy the full log'
    assert result['log_path'] == '/log'
