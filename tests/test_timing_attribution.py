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
    monkeypatch.setattr(controller.time, 'perf_counter', lambda: now[0])
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
    assert timing['clock'] == hd.timing_clock_name()
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


@pytest.mark.parametrize('delayed', ['restoration', 'refresh_gui', 'write', 'construction'])
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
        def __init__(self): operation('construction')
        def acquire(self, events):
            applied = hook._apply_property(hd.SetDeviceProperty('B'), {})
            hook._verify_property_actions([applied])
        def __exit__(self, *args): operation('exit')
    # Acquisition's constructor dispatches to a backend; completion is in exit.
    monkeypatch.setattr(tools, 'Acquisition', lambda **kwargs: Backend())
    monkeypatch.setattr(tools.time, 'perf_counter', lambda: now[0])
    teardown = {}
    tools._acquire_with_hooks(guard, '/data', 'run', [], hook, ctrl=ctrl,
                             policy=tools.DEFAULT, teardown_timing=teardown)
    breakdown = tools._run_duration_breakdown(hook, teardown, now[0] - 100)
    durations = {p: v['total_s'] for p, v in breakdown['phases'].items()}
    phase = 'acquisition' if delayed == 'construction' else delayed
    assert durations[phase] == 3
    assert max(durations, key=durations.get) == phase
    assert breakdown['accounted_s'] == 3
    assert breakdown['unaccounted_s'] == 0
    assert calls == ['construction', 'validation', 'write', 'wait', 'read_back', 'type',
                     'exit', 'restoration', 'refresh_gui']
    assert teardown['clock'] == hd.timing_clock_name()
    assert teardown['acquisition'] == {
        'start_s': 100., 'end_s': 103. if delayed in ('write', 'construction') else 100.}
    # The acquisition includes construction, but the nested write owns its delay.
    assert durations['acquisition'] == (3 if delayed == 'construction' else 0)


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
    now = [100.0]
    monkeypatch.setattr(tools.time, 'perf_counter', lambda: now[0])
    class Backend:
        _exception = None
        _dataset_disk_location = str(tmp_path / 'run')
        def acquire(self, events): pass
        def __exit__(self, *args): now[0] += 8
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
    import json
    assert 'dominant' not in json.dumps(result).lower()
    assert 'teardown_timing' not in result
    assert 'hardware_write_timing_summary' not in result
    breakdown = result['duration_breakdown']
    assert breakdown['phases']['restoration'] == {
        'count': 1, 'min_s': 0, 'mean_s': 0, 'max_s': 0, 'total_s': 0,
    }
    assert breakdown['duration_s'] == result['duration_s'] == 8
    assert breakdown['phases']['acquisition']['total_s'] == 8
    assert breakdown['accounted_s'] == 8
    assert breakdown['unaccounted_s'] == 0
    assert set(breakdown) == {'clock', 'duration_s', 'record_count', 'phases',
        'phase_meaning', 'slowest_records', 'slowest_meaning', 'accounted_s', 'unaccounted_s'}
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
    monkeypatch.setattr(tools.time, 'perf_counter', lambda: now[0])
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
            yield {'hook_event_index': i, 'timing': {'clock': hd.timing_clock_name(),
                'validation': {'start_s': 0, 'end_s': 1},
                'wait': {'start_s': 1, 'end_s': 1 + i % 10}}}
    tracemalloc.start()
    try:
        result = tools._run_duration_breakdown(SimpleNamespace(_log=records(100000)), {}, 600000)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert 'hardware_write_records' not in result
    summary = result
    assert summary['record_count'] == 100000
    assert summary['clock'] == hd.timing_clock_name()
    assert summary['phases'] == {
        'validation': {'count': 100000, 'min_s': 1, 'mean_s': 1, 'max_s': 1, 'total_s': 100000},
        'wait': {'count': 100000, 'min_s': 0, 'mean_s': 4.5, 'max_s': 9, 'total_s': 450000},
    }
    assert len(summary['slowest_records']) == 3
    assert [r['hook_event_index'] for r in summary['slowest_records']] == [9, 19, 29]
    sizes = [len(json.dumps(result))]
    for count in (5, 10000):
        sizes.append(len(json.dumps(tools._run_duration_breakdown(
            SimpleNamespace(_log=records(count)), {}, 600000))))
    assert max(sizes) < 3000
    assert max(sizes) - min(sizes) < 300
    assert peak < 1000000, 'aggregation must not copy the full log'
    assert result['accounted_s'] == 550000
    assert result['unaccounted_s'] == 50000
    assert all('start_s' not in r['timing']['wait'] and 'end_s' not in r['timing']['wait']
               for r in result['slowest_records'])


def test_restoration_write_spans_are_not_counted_twice():
    record = {'restoration': True, 'timing': {'clock': hd.timing_clock_name(),
        'write': {'start_s': 102, 'end_s': 104},
        'wait': {'start_s': 104, 'end_s': 108}}}
    teardown = {'clock': hd.timing_clock_name(),
                'restoration': {'start_s': 101, 'end_s': 110},
                'refresh_gui': {'start_s': 110, 'end_s': 118}}
    result = tools._run_duration_breakdown(SimpleNamespace(_log=[record]), teardown, 20)
    assert {p: v['total_s'] for p, v in result['phases'].items()} == {
        'write': 2, 'wait': 4, 'restoration': 3, 'refresh_gui': 8}
    assert result['accounted_s'] == 17
    assert result['unaccounted_s'] == 3
    assert result['accounted_s'] + result['unaccounted_s'] == result['duration_s']
    assert result['slowest_records'][0]['timing']['write'] == {'duration_s': 2}
    assert record['timing']['write'] == {'start_s': 102, 'end_s': 104}


def test_duration_breakdown_accumulate_only_then_report():
    accumulator = {}
    teardown = {'clock': hd.timing_clock_name(),
                'acquisition': {'start_s': 10, 'end_s': 12}}
    assert tools._run_duration_breakdown(
        None, teardown, None, accumulator=accumulator) is None
    result = tools._run_duration_breakdown(None, {}, 3, accumulator=accumulator)
    assert result['phases']['acquisition']['count'] == 1
    assert result['accounted_s'] == 2
    assert result['unaccounted_s'] == 1


def test_duration_breakdown_preserves_float_subtraction():
    duration = 23.61496476639424
    acquired = 2.554806101834151
    teardown = {'clock': hd.timing_clock_name(),
                'acquisition': {'start_s': 0., 'end_s': acquired}}
    result = tools._run_duration_breakdown(None, teardown, duration)
    assert result['unaccounted_s'] == duration - acquired
    assert result['accounted_s'] + result['unaccounted_s'] == pytest.approx(duration)


# --- 79c-2: one clock for the acquisition timing domain ---------------------

TIMING_DOMAIN_MODULES = ('controller', 'hook_decisions', 'tools')

#: Clock reads in the domain's three modules that are deliberately NOT part of
#: it, keyed by the function that owns them, with the reason each is a deadline
#: rather than a measurement. A read added anywhere else in these modules fails
#: `test_one_clock_reads_the_whole_timing_domain`, which is the point: this
#: block's assigned inventory was built from a grep and missed three sites in
#: `tools.py` alone, one of them a filter that would have dropped every span in
#: silence. Membership is decided per site, never swept.
NON_DOMAIN_CLOCK_READS = {
    ('tools', '_pause_live'): 'live-mode restore deadline; no value is recorded',
    ('tools', 'start_live_view'): 'live-mode start deadline; no value is recorded',
    ('tools', '_survey_event_stream'):
        'stall watchdog; `note_stalled` records the threshold, never an elapsed',
}

_CLOCK_ATTRS = ('monotonic', 'perf_counter', 'time', 'monotonic_ns',
                'perf_counter_ns', 'time_ns', 'process_time', 'thread_time')
_SEAM_NAMES = ('timing_clock', '_acquisition_monotonic')


def _clock_reads(module_name):
    """Every clock read in a module, as (enclosing functions, spelling, line)."""
    import ast
    import importlib
    import inspect
    module = importlib.import_module(f'microclaw.{module_name}')
    reads = []

    def spelling_of(func):
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == 'time' and func.attr in _CLOCK_ATTRS):
            return f'time.{func.attr}'
        if isinstance(func, ast.Name) and func.id in _SEAM_NAMES:
            return func.id
        if isinstance(func, ast.Attribute) and func.attr in _SEAM_NAMES:
            return func.attr
        return None

    def visit(node, scopes):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes = scopes + (node.name,)
        if isinstance(node, ast.Call):
            spelling = spelling_of(node.func)
            if spelling is not None:
                reads.append((scopes, spelling, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, scopes)

    visit(ast.parse(inspect.getsource(module)), ())
    return reads


def test_one_clock_reads_the_whole_timing_domain():
    """No span and no `duration_s` source reads a clock of its own.

    Source-inspected rather than reviewed, in the shape of
    `test_emitted_inline_defines_every_name_it_uses`: review is not a guard for
    "no site was missed". Both halves of a measured pair must come from one
    clock -- an offset cancels inside a difference, so the defect a partial
    migration leaves behind is a `started` read on one clock subtracted from an
    end read on another, and that is invisible in any single number.
    """
    offenders, exercised = [], set()
    for module_name in TIMING_DOMAIN_MODULES:
        for scopes, spelling, lineno in _clock_reads(module_name):
            if spelling in _SEAM_NAMES:
                continue
            allowed = {(module_name, scope) for scope in scopes
                       if (module_name, scope) in NON_DOMAIN_CLOCK_READS}
            if allowed:
                exercised |= allowed
            else:
                offenders.append(
                    f'{module_name}.py:{lineno} '
                    f'{".".join(scopes) or "<module>"} reads {spelling}')
    assert offenders == []
    # A deadline that stops existing leaves the list too, so the list cannot
    # rot into a blanket exemption for a module.
    assert exercised == set(NON_DOMAIN_CLOCK_READS)


def test_the_clock_is_chosen_at_exactly_one_site():
    import inspect
    from microclaw import controller
    assert 'getattr(time, _TIMING_CLOCK)()' in inspect.getsource(controller.timing_clock)
    assert controller._TIMING_CLOCK == 'perf_counter'
    # `monotonic` is `GetTickCount64()` on Windows: its unit is the millisecond
    # but its update period was measured at ~15.6 ms (design/70 `R128`), so it
    # reports the 41-97 us property write design/78 measured as `0.0`.
    assert controller._TIMING_CLOCK != 'monotonic'


def test_no_timing_record_spells_its_clock_by_hand():
    """The recorded `clock` is derived; a second spelling is what rots.

    This is the assertion that covers `_run_duration_breakdown`'s FILTER, which
    selects records by clock name and so is invisible to a grep for the call.
    Left on the old literal it would have skipped every span and handed
    `unaccounted_s` the whole run, with no error anywhere.
    """
    import ast
    import importlib
    import inspect
    from microclaw import controller
    offenders = []
    for module_name in TIMING_DOMAIN_MODULES:
        module = importlib.import_module(f'microclaw.{module_name}')
        tree = ast.parse(inspect.getsource(module))
        # `timing_clock_name` is the one place the prefix may be written, and
        # it writes only the prefix: the clock half comes from `_TIMING_CLOCK`.
        derivation = {id(node) for scope in ast.walk(tree)
                      if isinstance(scope, ast.FunctionDef)
                      and scope.name == 'timing_clock_name'
                      for node in ast.walk(scope)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value.startswith('time.')
                    and id(node) not in derivation):
                offenders.append(f'{module_name}.py:{node.lineno} {node.value!r}')
    assert offenders == []
    # And the one exempt site names no clock of its own.
    source = inspect.getsource(controller.timing_clock_name)
    assert 'f"time.{_TIMING_CLOCK}"' in source
    assert 'monotonic' not in source and 'perf_counter' not in source


def test_one_clock_object_reaches_every_module_and_the_seam_delegates(monkeypatch):
    """Not two seams: one function object, reached by three module bindings."""
    import time as time_module
    from microclaw import controller
    assert controller.timing_clock is tools.timing_clock is hd.timing_clock
    assert (controller.timing_clock_name is tools.timing_clock_name
            is hd.timing_clock_name)
    # Substituting the clock on `time` reaches all three at once, so no two
    # bindings can report different times for the same interval.
    monkeypatch.setattr(time_module, 'perf_counter', lambda: 7.5)
    assert controller.timing_clock() == 7.5
    # The supervisor's test seam delegates rather than reading a clock itself.
    assert tools._acquisition_monotonic() == 7.5
    assert controller.timing_clock_name() == 'time.perf_counter'


@pytest.mark.parametrize('clock_name', ['perf_counter', 'monotonic'])
def test_recorded_clock_names_the_function_actually_called(monkeypatch, clock_name):
    """The `clock` field follows the function, for a breakdown and a hook record.

    Parameterized over a substitution so the string cannot pass by being right
    once: moving the clock the package reads must move both the numbers and the
    name they are labelled with.
    """
    import time as time_module
    from microclaw import controller
    monkeypatch.setattr(controller, '_TIMING_CLOCK', clock_name)
    monkeypatch.setattr(time_module, clock_name, lambda: 40.5)
    core = SimpleNamespace(
        set_property=lambda *a: None, wait_for_device=lambda *a: None,
        get_property=lambda *a: '1', get_property_type=lambda *a: 'String')
    ctrl = SimpleNamespace(core=core, authorization_map=None)
    guard = SimpleNamespace(check_device_property=lambda *a, **k: None,
                            check_illumination=lambda *a, **k: None)
    hook = hd.UntrustedHookAdapter(hd.PLAN_ONLY)
    hook.configure_property(
        ctrl=ctrl, guard=guard, device='Wheel', property='State',
        allowed_values=('1',), min_value=None, max_value=None, max_writes=1,
        initial_value='0', restore='leave', action_plan=None)
    hook._verify_property_actions([hook._apply_property(hd.SetDeviceProperty('1'), {})])
    timing = hook._log[-1]['timing']
    assert timing['clock'] == f'time.{clock_name}'
    assert timing['write']['start_s'] == 40.5   # the function actually called
    teardown = {'clock': controller.timing_clock_name(),
                'acquisition': {'start_s': 0.0, 'end_s': 1.0}}
    breakdown = tools._run_duration_breakdown(hook, teardown, 1.0)
    assert breakdown['clock'] == f'time.{clock_name}'
    assert breakdown['slowest_records'][0]['timing']['clock'] == f'time.{clock_name}'
    # Read rather than filtered out: the record-selecting filter names the
    # clock too, and this is the number that goes to zero if it does not.
    assert breakdown['record_count'] == 1


def _timed_timelapse(monkeypatch, ctrl, guard, tmp_path, exit_sleep=0.05):
    import time as time_module
    class Backend:
        _exception = None
        _dataset_disk_location = str(tmp_path / 'run')
        def acquire(self, events): pass
        def __exit__(self, *args): time_module.sleep(exit_sleep)
    monkeypatch.setattr(tools, 'Acquisition', lambda **kwargs: Backend())
    started = time_module.perf_counter()
    result = tools.run_timelapse(ctrl, guard, n_frames=2, interval_s=0,
                                 save_dir=str(tmp_path))
    return result, time_module.perf_counter() - started


def test_residual_reconciles_against_the_runs_real_elapsed_time(
    monkeypatch, mock_ctrl, unconstrained_guard, tmp_path,
):
    """`accounted_s + unaccounted_s == duration_s`, on the real clock.

    Driven rather than fabricated, so `duration_s` and the spans come from the
    run instead of from the test: the residual must be a remainder bounded by
    the wall time this test measured for itself, not an offset between clocks.
    """
    result, wall_s = _timed_timelapse(monkeypatch, mock_ctrl, unconstrained_guard,
                                      tmp_path)
    breakdown = result['duration_breakdown']
    assert breakdown['clock'] == tools.timing_clock_name() == 'time.perf_counter'
    assert (breakdown['accounted_s'] + breakdown['unaccounted_s']
            == breakdown['duration_s'] == result['duration_s'])
    assert 0.05 <= breakdown['phases']['acquisition']['total_s'] <= wall_s
    assert 0 <= breakdown['unaccounted_s'] <= wall_s
    assert 0.05 <= breakdown['duration_s'] <= wall_s


def test_no_domain_site_still_reads_the_old_clock(
    monkeypatch, mock_ctrl, unconstrained_guard, tmp_path,
):
    """A run still reconciles while `time.monotonic` is 1000 s off the real clock.

    Regression guard, not watched-it-fail: on macOS `monotonic` and
    `perf_counter` are byte-identical (`R128`), so a site left behind is
    numerically invisible until the old clock is moved away. Any surviving read
    then pairs a 1000 s-shifted value with a `perf_counter` one and the
    residual becomes an offset. The deadline reads deliberately left on
    `monotonic` are compared only against each other, so the shift cannot reach
    a record through them.
    """
    import time as time_module
    real = time_module.perf_counter
    monkeypatch.setattr(time_module, 'monotonic', lambda: real() + 1000.0)
    result, wall_s = _timed_timelapse(monkeypatch, mock_ctrl, unconstrained_guard,
                                      tmp_path)
    breakdown = result['duration_breakdown']
    assert (breakdown['accounted_s'] + breakdown['unaccounted_s']
            == breakdown['duration_s'])
    assert 0 <= breakdown['unaccounted_s'] <= wall_s
    assert 0.05 <= breakdown['duration_s'] <= wall_s


# --- R124: the one hardware write a grid can authorize -----------------------

def _illumination_probe(*, delayed=None, stale=False, refuse=None,
                        write_error=False):
    """Drive the illumination branch, recording every call by name.

    The no-added-bridge-call assertion reads this list, the way
    `test_property_write_spans_attribute_delay_without_extra_bridge_calls`
    does, rather than a mock's call count.
    """
    import time as time_module
    calls = []

    def operation(name):
        calls.append(name)
        if delayed == name:
            time_module.sleep(0.02)

    class Core:
        value = '4.0'
        def get_property(self, device, prop):
            operation('read_back')
            return self.value
        def set_property(self, device, prop, value):
            operation('write')
            if write_error:
                raise RuntimeError('write failed')
            self.value = value

    class Guard:
        def illumination_from_percent(self, device, prop, percent):
            operation('validation')
            return percent
        def illumination_to_percent(self, device, prop, raw):
            return float(raw)
        def check_illumination(self, *args, **kwargs):
            if refuse == 'guard':
                raise RuntimeError('guard refused')

    adapter = hd.UntrustedHookAdapter(hd.PLAN_ONLY)
    adapter.configure_illumination(
        core=Core(), guard=Guard(), device='Laser', property='Power',
        max_power_percent=10, max_writes=2, initial_value=4)
    if stale:
        adapter._illumination_context['baseline_stale'] = True
    return adapter, calls


BRIDGE_CALLS = ('read_back', 'write')


@pytest.mark.parametrize('delayed', ['validation', 'write'])
def test_illumination_write_spans_attribute_delay_without_extra_bridge_calls(delayed):
    """`R124`: the only hardware write a grid can authorize now reports its cost.

    Before this, a `SetIlluminationPower` action wrote the device and recorded
    nothing, so on the one multi-field shape that can write hardware
    (`R123`) `duration_breakdown` attributed the write to nobody.
    """
    adapter, calls = _illumination_probe(delayed=delayed)
    assert adapter._dispatch(hd.SetIlluminationPower(6), {}) is None
    record = adapter._log[-1]
    assert record['decision'] == 'accepted'
    timing = record['timing']
    assert timing['clock'] == hd.timing_clock_name()
    assert set(timing) == {'clock', 'validation', 'write'}
    durations = {phase: timing[phase]['end_s'] - timing[phase]['start_s']
                 for phase in ('validation', 'write')}
    assert durations[delayed] >= 0.015
    assert max(durations, key=durations.get) == delayed
    # Ordered and non-nested, so `_run_duration_breakdown` counts each once.
    assert timing['validation']['end_s'] <= timing['write']['start_s']
    # One bridge call, the same one the un-instrumented branch made.
    assert calls == ['validation', 'write']
    assert [c for c in calls if c in BRIDGE_CALLS] == ['write']


def test_illumination_stale_baseline_reread_is_the_read_back_span():
    """The route's one read is a read-back: it asks what a failed write left.

    A healthy write has nothing to read back and records no `read_back`, which
    is why the span is present exactly when a read happened. No bridge call was
    added for it -- it wraps the re-read that was already here.
    """
    adapter, calls = _illumination_probe(stale=True, delayed='read_back')
    assert adapter._dispatch(hd.SetIlluminationPower(6), {}) is None
    timing = adapter._log[-1]['timing']
    assert set(timing) == {'clock', 'read_back', 'validation', 'write'}
    assert (timing['read_back']['end_s'] <= timing['validation']['start_s']
            <= timing['validation']['end_s'] <= timing['write']['start_s'])
    assert timing['read_back']['end_s'] - timing['read_back']['start_s'] >= 0.015
    assert calls == ['read_back', 'validation', 'write']
    # The intermediate baseline-re-read record carries no timing:
    # `_run_duration_breakdown` sums every record it walks, so a span repeated
    # on two records of one action would be counted twice.
    assert [r.get('event') for r in adapter._log] == [
        'illumination_baseline_reread', 'hook_action']
    assert len([r for r in adapter._log if 'timing' in r]) == 1


@pytest.mark.parametrize('case,decision,attempted', [
    ('ceiling', 'refused', {'validation'}),
    ('guard', 'refused', {'validation'}),
    ('write_error', 'failed', {'validation', 'write'}),
])
def test_illumination_failure_retains_attempted_spans(case, decision, attempted):
    adapter, calls = _illumination_probe(
        refuse='guard' if case == 'guard' else None,
        write_error=case == 'write_error')
    percent = 99 if case == 'ceiling' else 6
    assert adapter._dispatch(hd.SetIlluminationPower(percent), {}) is None
    record = adapter._log[-1]
    assert record['decision'] == decision
    timing = record['timing']
    assert set(timing) == {'clock', *attempted}
    for phase in attempted:
        assert timing[phase]['end_s'] >= timing[phase]['start_s']


def test_illumination_spans_reach_a_duration_breakdown():
    """The point of `R124`: the write's cost lands in a phase, not the residual."""
    adapter, calls = _illumination_probe(delayed='write')
    adapter._dispatch(hd.SetIlluminationPower(6), {})
    breakdown = tools._run_duration_breakdown(
        adapter, {'clock': hd.timing_clock_name()}, 10.0)
    assert breakdown['record_count'] == 1
    assert breakdown['phases']['write']['count'] == 1
    assert breakdown['phases']['write']['total_s'] >= 0.015
    assert set(breakdown['phases']) == {'validation', 'write'}
    assert breakdown['accounted_s'] + breakdown['unaccounted_s'] == 10.0
