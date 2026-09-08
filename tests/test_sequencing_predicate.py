"""AcqEngJ bytecode arithmetic model; running-engine confirmation is a rig gate."""
import itertools
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from microclaw import tools
from microclaw.hooks import FocusFeedbackHook, IntensityAdaptiveHook, AutofocusHook, MMAutofocusPluginHook
from microclaw.hook_decisions import CompositeHook, UntrustedHookAdapter
from microclaw.safety import SafetyConstraints, SafetyGuard, StageConstraints


@pytest.mark.parametrize('interval', [0, 1e-9, 1e-6, .0001, .0005, .0009, .0009999999])
def test_submillisecond_deadlines_collide(interval):
    assert tools._sequenced_ms(0, interval) == tools._sequenced_ms(1, interval) == 0


def test_long_run_double_rounding_collision_4006_4007():
    deadlines = [tools._sequenced_ms(k, .001) for k in range(200001)]
    pairs = [k for k in range(len(deadlines)-1) if deadlines[k] == deadlines[k+1]]
    assert not any(k < 1999 for k in pairs)
    assert pairs[0] == 4006
    assert deadlines[4006:4008] == [4006, 4006]


def test_tenth_millisecond_deadline_runs_of_ten():
    deadlines = [tools._sequenced_ms(k, .0001) for k in range(2000)]
    assert [len(list(group)) for _, group in itertools.groupby(deadlines)] == [10] * 200


ROUTES = ['plan_only', 'plan_hook', 'illumination', 'focus', 'intensity', 'autofocus',
          'plugin_autofocus', 'multipos_position', 'multipos_time', 'multipos_composed']


@pytest.fixture
def acquisition_environment(monkeypatch, tmp_path):
    ctrl = MagicMock()
    guard = SafetyGuard(SafetyConstraints(stage=StageConstraints(x_min=-1, x_max=1, y_min=-1, y_max=1)))
    monkeypatch.setattr(guard, 'resolve_in_workspace', lambda path: path)
    acquisition = MagicMock(side_effect=RuntimeError('counted Acquisition construction'))
    monkeypatch.setattr(tools, 'Acquisition', acquisition)
    monkeypatch.setattr(tools, '_configure_hook_capabilities', lambda *a, **k: None)
    monkeypatch.setattr(tools, '_authorize_acquisition', lambda *a, **k: MagicMock(has_overrun=False))
    monkeypatch.setattr(tools, '_plan_with_hook_dose', lambda plan, hook: plan)
    # Keep actual event deadlines and the real acquisition runner; only rig reads are fake.
    ctrl.core.get_image_width.return_value = 2
    ctrl.core.get_image_height.return_value = 2
    ctrl.core.get_bytes_per_pixel.return_value = 2
    ctrl.core.get_exposure.return_value = 1
    return ctrl, guard, acquisition


def run_route(route, n, interval, env, monkeypatch, tmp_path):
    ctrl, guard, _ = env
    classes = {'focus': FocusFeedbackHook, 'intensity': IntensityAdaptiveHook,
               'autofocus': AutofocusHook, 'plugin_autofocus': MMAutofocusPluginHook}
    hook = object.__new__(classes.get(route, FocusFeedbackHook)) if route in classes or route.startswith('multipos') else UntrustedHookAdapter(object())
    if route == 'multipos_composed':
        hook = CompositeHook([('focus', hook)], None)
    monkeypatch.setattr(tools, '_resolve_hook', lambda *a, **k: hook)
    monkeypatch.setattr(tools, '_resolve_hooks', lambda *a, **k: hook)
    if route.startswith('multipos'):
        return tools.run_multiposition_acquisition(
            ctrl, guard, protocol='timelapse', save_dir=str(tmp_path),
            positions=[{'name': 'a', 'x_um': 0, 'y_um': 0}], hook_strategy='focus_feedback',
            acquisition_order='time_then_position' if route == 'multipos_time' else 'position_then_time',
            protocol_params={'n_frames': n, 'interval_s': interval})
    kwargs = {}
    if route in ('plan_only', 'plan_hook'):
        kwargs['hook_action_plan'] = [{'hook_event_index': k, 'actions': [
            {'kind': 'SetDeviceProperty', 'value': 'A'}]} for k in range(n)]
    if route == 'illumination':
        kwargs['illumination_envelope'] = {'device': 'Laser'}
    return tools.run_timelapse(ctrl, guard, n_frames=n, interval_s=interval,
        save_dir=str(tmp_path), hook_strategy=None if route in ('plain', 'plan_only') else 'saved', **kwargs)


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('n,interval', [(5, .0001), (200001, .001)])
def test_refusal_precedes_acquisition_construction(route, n, interval, acquisition_environment, monkeypatch, tmp_path):
    error = None
    try:
        result = run_route(route, n, interval, acquisition_environment, monkeypatch, tmp_path)
        if isinstance(result, dict) and 'error' in result:
            error = ValueError(result['error'])
    except (ValueError, RuntimeError) as exc:
        error = exc
    assert acquisition_environment[2].call_count == 0
    assert isinstance(error, ValueError)
    assert 'truncated millisecond' in str(error)


@pytest.mark.parametrize('route,interval', [('plain', 0), ('observer', 0), ('focus', .05), ('plan_only', .05)])
def test_distinct_deadlines_and_observation_bursts_reach_acquisition(route, interval, acquisition_environment, monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match='counted Acquisition construction'):
        run_route(route, 5, interval, acquisition_environment, monkeypatch, tmp_path)
    assert acquisition_environment[2].call_count == 1


def test_backstop_explains_planning_gap():
    hook = UntrustedHookAdapter(object())
    hook._fixed_plan_context = object()
    with pytest.raises(RuntimeError) as error:
        hook.pre_hardware_hook_fn([{'axes': {'time': 0}}, {'axes': {'time': 1}}])
    assert 'should have been refused at plan time' in str(error.value)
    assert 'int(k * interval_s * 1000.0)' in str(error.value)
    assert 'nonzero' not in str(error.value)


@pytest.mark.parametrize('file', ['tools_schema.py', 'skills/hook-authoring/SKILL.md', 'skills/smlm/SKILL.md', 'skills/dna-paint/SKILL.md'])
def test_guidance_states_predicate_without_threshold(file):
    source = (Path(tools.__file__).parent / file).read_text(encoding='utf-8')
    assert 'int(k * interval_s * ' in source
    assert not re.search(r'\b1\s*ms\b|\b0\.001\b|requires? (?:a )?nonzero|NONZERO', source)


@pytest.mark.parametrize('interval', [0, .0001, .001, .05])
def test_event_builder_emits_the_deadlines_being_modelled(interval):
    events = tools._build_acquisition_events(num_time_points=5, time_interval_s=interval)
    assert [event['axes']['time'] for event in events] == list(range(5))
    if interval == 0:
        assert all('min_start_time' not in event for event in events)
    else:
        assert [event['min_start_time'] for event in events] == [k * interval for k in range(5)]


def test_refusal_source_contains_no_threshold_claim():
    import inspect
    source = inspect.getsource(tools._refuse_sequenced_time_axis)
    assert not re.search(r'\b1\s*ms\b|\b0\.001\b|nonzero', source)
