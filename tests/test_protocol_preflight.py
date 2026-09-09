import json
from unittest.mock import MagicMock

import pytest

from microclaw import tools
from microclaw import tools_schema


TIMELAPSE = {"n_frames": 2, "interval_s": 0, "channel": "DAPI",
             "exposure_ms": 5, "laser_slot": 1}
ZSTACK = {"z_start_um": 0, "z_end_um": 2, "z_step_um": 1,
          "channel": "DAPI", "exposure_ms": 5}


@pytest.mark.parametrize(
    ("protocol", "params", "shape"),
    [
        ("timelapse", TIMELAPSE,
         {"num_time_points": 2, "time_interval_s": 0}),
        ("zstack", ZSTACK, {"z_start": 0, "z_end": 2, "z_step": 1}),
        ("snap", {}, {}),
    ],
)
def test_protocol_preflight_accepts_exact_documented_shapes(protocol, params, shape):
    assert tools._protocol_shape_kwargs(protocol, params) == shape


@pytest.mark.parametrize(
    ("protocol", "params", "message"),
    [
        ("timelapse", {"n_frames": 2}, "missing 'interval_s'"),
        ("timelapse", {"interval_s": 0}, "missing 'n_frames'"),
        ("timelapse", {}, "missing 'n_frames', 'interval_s'"),
        ("zstack", {"z_start_um": 0, "z_end_um": 2}, "missing 'z_step_um'"),
        ("zstack", {"z_start_um": 0}, "missing 'z_end_um', 'z_step_um'"),
        ("zstack", {**ZSTACK, "n_frames": 2},
         "timelapse parameters do not apply to a zstack"),
        ("timelapse", {**TIMELAPSE, "z_start_um": 0},
         "zstack parameters do not apply to a timelapse"),
        ("zstack", {**ZSTACK, "laser_slot": 1},
         "incompatible key 'laser_slot'; timelapse parameters"),
        ("zstack", {**ZSTACK, "max_frames": 2},
         "incompatible key 'max_frames'; timelapse parameters"),
        ("zstack", {**ZSTACK, "n_frames": 2, "interval_s": 0, "laser_slot": 1},
         "incompatible keys 'interval_s', 'laser_slot', 'n_frames'; timelapse"),
        ("timelapse", {**TIMELAPSE, "z_start_um": 0, "z_end_um": 2,
                       "z_step_um": 1},
         "incompatible keys 'z_end_um', 'z_start_um', 'z_step_um'; zstack"),
        ("timelapse", {**TIMELAPSE, "max_frames": 2}, "incompatible key 'max_frames'"),
        ("snap", {"exposure_ms": 5}, "'snap' contains incompatible key 'exposure_ms'"),
    ],
)
def test_protocol_preflight_reports_missing_and_incompatible_keys(
    protocol, params, message
):
    with pytest.raises(ValueError, match=message):
        tools._protocol_shape_kwargs(protocol, params)


@pytest.mark.parametrize("key", ("hook_strategy", *tools.HOOK_CAPABILITY_ARGS))
def test_protocol_preflight_keeps_hook_capability_refusal(key):
    with pytest.raises(ValueError, match="cannot carry per-run hook capabilities"):
        tools._protocol_shape_kwargs("timelapse", {**TIMELAPSE, key: "supplied"})


def _call_outer(name, ctrl, guard, params, hooked, tmp_path):
    common = {"protocol": "timelapse", "protocol_params": params}
    if name == "run_tile_acquisition":
        return tools.run_tile_acquisition(
            ctrl, guard, rows=1, cols=1, step_um=1, save_dir=str(tmp_path),
            hook_strategy="probe" if hooked else None, **common,
        )
    if name == "run_multiposition_acquisition":
        return tools.run_multiposition_acquisition(
            ctrl, guard, positions=[{"name": "p", "x_um": 0, "y_um": 0}],
            save_dir=str(tmp_path), hook_strategy="probe" if hooked else None,
            **common,
        )
    if name == "run_multiposition_with_autofocus":
        return tools.run_multiposition_with_autofocus(
            ctrl, guard, positions=[{"name": "p", "x_um": 0, "y_um": 0}],
            z_range_um=2, z_step_um=1, save_dir=str(tmp_path), **common,
        )
    return tools.run_adaptive_survey(
        ctrl, guard, positions=[{"name": "p", "x_um": 0, "y_um": 0}],
        save_dir=str(tmp_path), hook_strategy="probe", **common,
    )


@pytest.mark.parametrize("name", [
    "run_tile_acquisition", "run_multiposition_acquisition",
    "run_multiposition_with_autofocus", "run_adaptive_survey",
])
@pytest.mark.parametrize("hooked", [False, True])
def test_each_outer_boundary_refuses_before_any_effect(
    name, hooked, tmp_path, monkeypatch
):
    ctrl = MagicMock()
    guard = MagicMock()
    effects = MagicMock()
    monkeypatch.setattr(tools, "_preflight_native_positions",
                        lambda *_a, **_k: effects("position_preflight"))
    monkeypatch.setattr(tools, "_plan_protocol_repetitions",
                        lambda *_a, **_k: effects("planning"))
    if name == "run_multiposition_with_autofocus":
        monkeypatch.setattr(
            tools, "run_multiposition_acquisition",
            MagicMock(side_effect=lambda *_a, **_k: effects("forward")),
        )

    result = _call_outer(name, ctrl, guard, {"n_frames": 2}, hooked, tmp_path)

    assert "missing 'interval_s'" in result["error"]
    assert ctrl.mock_calls == []
    assert guard.mock_calls == []
    effects.assert_not_called()


def test_snap_route_reaches_preflight_before_default_tile_center(tmp_path):
    ctrl, guard = MagicMock(), MagicMock()
    result = tools.run_tile_acquisition(
        ctrl, guard, rows=1, cols=1, step_um=1, protocol="snap",
        protocol_params={"exposure_ms": 5}, save_dir=str(tmp_path),
    )
    assert "'snap' contains incompatible key 'exposure_ms'" in result["error"]
    ctrl.core.get_x_position.assert_not_called()
    ctrl.core.get_y_position.assert_not_called()


@pytest.mark.parametrize(("params", "expected"), [
    ({"exposure_ms": 5}, "contains incompatible key 'exposure_ms'"),
    ({"n_frames": 1, "interval_s": 0},
     "contains incompatible keys 'interval_s', 'n_frames'"),
])
def test_snap_multiposition_route_reaches_preflight(params, expected, monkeypatch):
    """Acceptance test 6 names the plain multiposition planning path, which is
    guarded by `if protocol != "snap"` and so reached the helper on no path at
    all before this block. The tile route above covers the other one."""
    ctrl, guard = MagicMock(), MagicMock()
    seen = []
    original = tools._protocol_shape_kwargs

    def record(protocol, supplied, acquisition_order="position_then_time"):
        seen.append(protocol)
        return original(protocol, supplied, acquisition_order)

    monkeypatch.setattr(tools, "_protocol_shape_kwargs", record)
    result = tools.run_multiposition_acquisition(
        ctrl, guard, protocol="snap",
        positions=[{"name": "p", "x_um": 0, "y_um": 0}],
        protocol_params=params,
    )

    assert expected in result["error"]
    assert seen == ["snap"]
    assert ctrl.mock_calls == []
    assert guard.mock_calls == []


def test_execute_tool_refuses_forwarding_case_before_stage_or_camera(monkeypatch):
    ctrl, guard = MagicMock(), MagicMock()
    run_zstack = MagicMock()
    monkeypatch.setattr("microclaw.authorization.authorize_path", lambda *_: None)
    monkeypatch.setattr(tools, "run_zstack", run_zstack)
    result = json.loads(tools.execute_tool(
        "run_multiposition_acquisition",
        {"protocol": "zstack", "save_dir": "/tmp/preflight",
         "positions": [{"name": "p", "x_um": 0, "y_um": 0}],
         "protocol_params": {"z_start_um": 0, "z_end_um": 2,
                             "z_step_um": 1, "n_frames": 1}},
        ctrl, guard,
    ))
    assert result["error"] == (
        "protocol_params for 'zstack' contains incompatible key 'n_frames'; "
        "timelapse parameters do not apply to a zstack."
    )
    run_zstack.assert_not_called()
    ctrl.core.get_x_position.assert_not_called()
    ctrl.core.set_xy_position.assert_not_called()
    ctrl.core.set_exposure.assert_not_called()


def test_acquire_on_hit_timelapse_requires_interval_before_search_frame(
    tmp_path, monkeypatch
):
    from microclaw.hook_decisions import HookResult, UntrustedHookAdapter

    class Hook:
        def analyze_frame(self, _image, _metadata):
            return HookResult({})

    ctrl, guard = MagicMock(), MagicMock()
    guard.resolve_in_workspace.side_effect = lambda path: path
    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    monkeypatch.setattr(tools, "_resolve_hook", lambda *_a, **_k: adapter)
    acquire = MagicMock()
    monkeypatch.setattr(tools, "_acquire_survey_with_detector", acquire)

    result = tools.run_adaptive_survey(
        ctrl, guard, protocol="timelapse", save_dir=str(tmp_path),
        hook_strategy="saved", positions=[{"name": "p", "x_um": 0, "y_um": 0}],
        protocol_params={"n_frames": 1, "interval_s": 0, "channel": "DAPI"},
        acquire_on_hit={
            "channel": "FITC", "protocol": "timelapse", "max_hits": 1,
            "protocol_params": {"n_frames": 2, "exposure_ms": 5},
        },
    )

    assert "missing 'interval_s'" in result["error"]
    acquire.assert_not_called()
    ctrl.core.set_exposure.assert_not_called()


def _run_acquire_on_hit_zstack(tmp_path, monkeypatch, acquire_params):
    from microclaw.acquisition import AcquisitionPlan
    from microclaw.hook_decisions import HookResult, UntrustedHookAdapter

    class Hook:
        def analyze_frame(self, _image, _metadata):
            return HookResult({})

    ctrl, guard = MagicMock(), MagicMock()
    guard.resolve_in_workspace.side_effect = lambda path: path
    adapter = UntrustedHookAdapter(Hook(), str(tmp_path / "hook.json"))
    monkeypatch.setattr(tools, "_resolve_hook", lambda *_a, **_k: adapter)
    monkeypatch.setattr(
        tools, "plan_events", lambda *_a, **_k: AcquisitionPlan(3, 5, 1, 1)
    )
    acquire = MagicMock(return_value={"error": "survey stopped by test"})
    monkeypatch.setattr(tools, "_acquire_survey_with_detector", acquire)
    monkeypatch.setattr(
        tools, "_channel_effects_for_later_phase", lambda *_a, **_k: {}
    )
    seen = []
    original = tools._protocol_shape_kwargs

    def record_shape(protocol, params):
        seen.append((protocol, dict(params)))
        return original(protocol, params)

    monkeypatch.setattr(tools, "_protocol_shape_kwargs", record_shape)
    result = tools.run_adaptive_survey(
        ctrl, guard, protocol="timelapse", save_dir=str(tmp_path),
        hook_strategy="saved", positions=[{"name": "p", "x_um": 0, "y_um": 0}],
        protocol_params={"n_frames": 1, "interval_s": 0, "channel": "DAPI"},
        acquire_on_hit={
            "channel": "FITC", "protocol": "zstack", "max_hits": 2,
            "protocol_params": acquire_params,
        },
    )
    return result, acquire, seen


def test_acquire_on_hit_zstack_translates_offsets_and_still_plans(
    tmp_path, monkeypatch
):
    result, acquire, seen = _run_acquire_on_hit_zstack(
        tmp_path, monkeypatch,
        {"z_offset_start_um": -1, "z_offset_end_um": 1,
         "z_step_um": 0.5, "exposure_ms": 5},
    )

    assert result["error"] == "survey stopped by test"
    assert ("zstack", {"z_start_um": -1, "z_end_um": 1,
                       "z_step_um": 0.5, "exposure_ms": 5}) in seen
    acquire.assert_called_once()
    assert acquire.call_args.kwargs["acquire_plan"].frames == 6


@pytest.mark.parametrize(
    ("params", "message", "reaches_shared_helper"),
    [
        ({"z_start_um": -1, "z_end_um": 1, "z_step_um": 0.5},
         "refuses absolute z_start_um/z_end_um", False),
        ({"z_offset_start_um": -1, "z_step_um": 0.5},
         "acquire_on_hit.protocol_params for 'zstack' is missing 'z_offset_end_um'",
         False),
        ({"z_offset_start_um": -1, "z_offset_end_um": 1, "z_step_um": 0.5,
          "n_frames": 2},
         "timelapse parameters do not apply to a zstack", True),
    ],
)
def test_acquire_on_hit_zstack_refusals_stay_on_their_own_boundaries(
    params, message, reaches_shared_helper, tmp_path, monkeypatch
):
    result, acquire, seen = _run_acquire_on_hit_zstack(
        tmp_path, monkeypatch, params
    )

    assert message in result["error"]
    assert any(protocol == "zstack" for protocol, _params in seen) is reaches_shared_helper
    acquire.assert_not_called()


def test_shared_schema_publishes_snap_as_parameterless():
    assert "snap takes no protocol_params" in tools_schema._PROTOCOL_PARAMS_SCHEMA[
        "description"
    ]


# Reuse the engine-dispatching backend: frames are delivered at teardown.
from tests.test_acquisition_order import rig
from pycromanager import multi_d_acquisition_events
import math


@pytest.mark.parametrize('shape, reason', [
    ({'z_start': 0, 'z_end': 0, 'z_step': 1}, 'Equal Z endpoints'),
    ({'z_start': 65.183, 'z_end': 65.183, 'z_step': 1}, 'Equal Z endpoints'),
    ({'z_start': 0, 'z_end': 0, 'z_step': 0}, 'z_step must be positive'),
    ({'z_start': 65, 'z_end': 65, 'z_step': 0}, 'z_step must be positive'),
    ({'z_start': 60, 'z_end': 70, 'z_step': 0}, 'z_step must be positive'),
    ({'z_start': 70, 'z_end': 60, 'z_step': 1}, 'z_end must be greater'),
    ({'z_start': 60, 'z_end': 70, 'z_step': -1}, 'z_step must be positive'),
    ({'z_start': 70, 'z_end': 60, 'z_step': -2.5}, 'z_step must be positive'),
    ({'z_start': 0, 'z_end': 1}, 'complete z_start, z_end, z_step triple'),
    ({'z_step': 1}, 'complete z_start, z_end, z_step triple'),
    *[({'z_start': 0, 'z_end': 1, 'z_step': 1, key: value}, f'{key} must be finite')
      for key in ('z_start', 'z_end', 'z_step')
      for value in (float('nan'), float('inf'), -float('inf'), None)],
])
def test_z_sweep_input_refusal_precedes_dependency(shape, reason, monkeypatch):
    engine = MagicMock(wraps=multi_d_acquisition_events)
    monkeypatch.setattr(tools, 'multi_d_acquisition_events', engine)
    with pytest.raises(ValueError, match=reason) as error:
        tools._build_acquisition_events(**shape)
    engine.assert_not_called()
    if reason == 'Equal Z endpoints':
        assert 'absolute stage coordinates' in str(error.value)
        assert 'protocol="timelapse"' in str(error.value)
        assert '{"n_frames": 1, "interval_s": 0}' in str(error.value)
        assert 'distinct endpoints around the current Z' in str(error.value)


@pytest.mark.parametrize('damage,reason', [
    ('empty', 'generated no events'),
    ('missing', 'missing or non-finite Z'),
    ('nan', 'missing or non-finite Z'),
    ('one', 'at least two distinct Z'),
])
def test_z_sweep_generated_output_refusal(damage, reason, monkeypatch):
    def damaged_engine(**kwargs):
        events = multi_d_acquisition_events(**kwargs)
        if damage == 'empty':
            return events[:0]
        if damage == 'one':
            for event in events:
                event['z'] = events[0]['z']
        elif damage == 'missing':
            events[-1].pop('z')
        else:
            events[-1]['z'] = float('nan')
        return events
    monkeypatch.setattr(tools, 'multi_d_acquisition_events', damaged_engine)
    with pytest.raises(ValueError, match=reason):
        tools._build_acquisition_events(z_start=60, z_end=61, z_step=1)


def _z_call(rig, route, start=60, end=60.5, step=1):
    shape = dict(z_start_um=start, z_end_um=end, z_step_um=step, exposure_ms=7)
    if route == 'zstack':
        return 'run_zstack', dict(save_dir=rig.params['save_dir'], **shape)
    params = {**rig.params, 'protocol': 'zstack', 'protocol_params': shape}
    if route == 'hooked':
        params['hook_strategy'] = 'snr_observer'
    return 'run_multiposition_acquisition', params


def _bound_z(rig, maximum):
    def check(z):
        if not math.isfinite(z) or z > maximum:
            raise ValueError(f'actual Z {z} exceeds bound {maximum}')
    rig.guard.check_z.side_effect = check


@pytest.mark.parametrize('route', ['zstack', 'hooked', 'unhooked'])
@pytest.mark.parametrize('maximum', [60.5, 61])
def test_execute_z_sweep_guards_actual_extrema_before_effects(rig, monkeypatch, route, maximum):
    monkeypatch.setattr('microclaw.authorization.authorize_path', lambda *_: None)
    _bound_z(rig, maximum)
    rig.ctrl.studio.live().is_live_mode_on.return_value = True
    name, params = _z_call(rig, route)
    result = json.loads(tools.execute_tool(name, params, rig.ctrl, rig.guard))
    seen = [call.args[0] for call in rig.guard.check_z.call_args_list]
    assert seen[:2] == [60., 61.], result
    if maximum == 60.5:
        assert 'actual Z 61.0 exceeds bound 60.5' in result['error']
        assert not rig.acquisitions
        assert not rig.reservations
        rig.ctrl.core.set_exposure.assert_not_called()
        rig.ctrl.core.set_xy_position.assert_not_called()
        rig.ctrl.studio.live().set_live_mode_on.assert_not_called()
    else:
        assert 'error' not in result, result
        assert rig.acquisitions


@pytest.mark.parametrize('route', ['zstack', 'hooked', 'unhooked'])
def test_execute_equal_endpoints_refuse_before_effects(rig, monkeypatch, route):
    monkeypatch.setattr('microclaw.authorization.authorize_path', lambda *_: None)
    name, params = _z_call(rig, route, 0, 0)
    result = json.loads(tools.execute_tool(name, params, rig.ctrl, rig.guard))
    assert 'Equal Z endpoints' in result['error']
    assert not rig.acquisitions
    assert not rig.reservations
    rig.ctrl.core.set_exposure.assert_not_called()
    rig.ctrl.core.set_xy_position.assert_not_called()
    rig.ctrl.studio.live().set_live_mode_on.assert_not_called()


@pytest.mark.parametrize('route', ['zstack', 'hooked', 'unhooked'])
def test_validated_engine_list_is_submitted_without_rebuilding(rig, monkeypatch, route):
    from tests.test_acquisition_order import RecordingBackend
    monkeypatch.setattr('microclaw.authorization.authorize_path', lambda *_: None)
    built, submitted = [], []
    def engine(**kwargs):
        events = multi_d_acquisition_events(**kwargs)
        built.append(events)
        return events
    acquire = RecordingBackend.acquire
    def submit(self, events):
        submitted.append(events)
        return acquire(self, events)
    monkeypatch.setattr(tools, 'multi_d_acquisition_events', engine)
    monkeypatch.setattr(RecordingBackend, 'acquire', submit)
    name, params = _z_call(rig, route, 60, 61, .3)
    result = json.loads(tools.execute_tool(name, params, rig.ctrl, rig.guard))
    assert 'error' not in result, result
    assert len(built) == 1
    assert submitted and all(events is built[0] for events in submitted)
    expected_shape = dict(z_start=60, z_end=61, z_step=.3)
    if route == 'hooked':
        expected_shape.update(xy_positions=[(0., 0.), (10., 0.)], position_labels=['A', 'B'])
    assert built[0] == multi_d_acquisition_events(**expected_shape)


def test_builder_preserves_engine_position_dependent_fractional_z():
    shape = dict(z_start=1, z_end=2, z_step=.3,
                 xyz_positions=[(0, 0, 60), (10, 0, 65)], position_labels=['A', 'B'])
    assert tools._build_acquisition_events(**shape) == multi_d_acquisition_events(**shape)


def test_later_group_refuses_before_first_acquisition_or_exposure(rig, monkeypatch):
    monkeypatch.setattr('microclaw.authorization.authorize_path', lambda *_: None)
    _bound_z(rig, 70)
    rig.ctrl.studio.live().is_live_mode_on.return_value = True
    def engine(**kwargs):
        # Fault injection at the dependency boundary; geometry still comes from
        # the installed engine, with a later group's actual Z outside the bound.
        if kwargs.get('position_labels') == ['B']:
            kwargs['xyz_positions'] = [(10, 0, 71)]
            kwargs.pop('xy_positions', None)
        return multi_d_acquisition_events(**kwargs)
    monkeypatch.setattr(tools, 'multi_d_acquisition_events', engine)
    result = json.loads(tools.execute_tool('run_multiposition_acquisition', {
        **rig.params, 'hook_strategy': 'snr_observer',
        'protocol_params': dict(n_frames=2, interval_s=1, exposure_ms=7),
    }, rig.ctrl, rig.guard))
    assert 'actual Z 71 exceeds bound 70' in result['error']
    assert not rig.acquisitions
    assert not rig.reservations
    rig.ctrl.core.set_exposure.assert_not_called()
    rig.ctrl.studio.live().set_live_mode_on.assert_not_called()


@pytest.mark.parametrize('hooked', [False, True])
def test_suggested_single_plane_timelapse_works(rig, monkeypatch, hooked):
    monkeypatch.setattr('microclaw.authorization.authorize_path', lambda *_: None)
    result = json.loads(tools.execute_tool('run_multiposition_acquisition', {
        **rig.params, 'protocol_params': dict(n_frames=1, interval_s=0),
        **({'hook_strategy': 'snr_observer'} if hooked else {}),
    }, rig.ctrl, rig.guard))
    assert 'error' not in result, result
    assert len(rig.frames) == 2


@pytest.mark.parametrize('events,reason', [
    ([], 'generated no events'),
    ([{'axes': {}}], 'missing or non-finite Z'),
    ([{'axes': {'z': 0}, 'z': 60.}], 'at least two distinct Z'),
    ([{'axes': {'z': 0}, 'z': float('nan')}], 'missing or non-finite Z'),
])
def test_injected_z_events_cannot_bypass_shared_refusal(rig, events, reason):
    with pytest.raises(ValueError, match=reason):
        tools.run_zstack(rig.ctrl, rig.guard, 60, 61, 1,
                         rig.params['save_dir'], exposure_ms=7, _events=events)
    assert not rig.acquisitions
    rig.ctrl.core.set_exposure.assert_not_called()


@pytest.mark.parametrize('maximum', [60.5, 61.])
def test_acquire_on_hit_guards_generated_extrema_before_submission(rig, monkeypatch, maximum):
    from microclaw.hook_decisions import UntrustedHookAdapter

    monkeypatch.setattr('microclaw.authorization.authorize_path', lambda *_: None)
    monkeypatch.setattr(tools, '_resolve_hook', lambda *_a, **_k: UntrustedHookAdapter(object()))
    monkeypatch.setattr(tools, '_set_channel_for_composite', lambda *_: {})
    _bound_z(rig, maximum)

    def search(*_args, **kwargs):
        # Supply the detection phase's runtime output; the acquire phase below
        # uses the real event builder and the dispatching acquisition backend.
        kwargs['acquire_hits'].append(dict(name='hit', x_um=0., y_um=0., z_um=60.))
        reservation = tools._authorize_acquisition(rig.ctrl, rig.guard, kwargs['acquire_plan'])
        return {'dataset_path': '/survey', '_acquire_reservation': reservation}

    monkeypatch.setattr(tools, '_acquire_survey_with_detector', search)
    result = json.loads(tools.execute_tool('run_adaptive_survey', {
        'protocol': 'timelapse', 'protocol_params': {'n_frames': 1, 'interval_s': 0, 'channel': 'DAPI'},
        'positions': [dict(name='seed', x_um=0., y_um=0.)],
        'save_dir': rig.params['save_dir'], 'hook_strategy': 'saved',
        'acquire_on_hit': {'channel': 'FITC', 'protocol': 'zstack', 'max_hits': 1,
                           'protocol_params': {'z_offset_start_um': 0., 'z_offset_end_um': .5,
                                               'z_step_um': 1.}},
    }, rig.ctrl, rig.guard))
    if maximum == 60.5:
        assert 'error' in result, result
        assert 'actual Z 61.0 exceeds bound 60.5' in result['error']
        assert not rig.acquisitions
        assert not rig.submitted
    else:
        assert 'error' not in result, result
        assert result['hits_acquired'] == 1
        assert len(rig.acquisitions) == 1
        assert rig.acquisitions[0].events == multi_d_acquisition_events(
            z_start=60., z_end=60.5, z_step=1.,
            xy_positions=[(0., 0.)], position_labels=['hit'],
        )
    assert [call.args[0] for call in rig.guard.check_z.call_args_list] == [60., 61.]


# --- F-J: the offsets path must not quote the absolute spelling back ----------

_OFFSET_SWEEP_CASES = [
    ("equal_endpoints", {"z_offset_start_um": 0.0, "z_offset_end_um": 0.0,
                         "z_step_um": 0.5}),
    ("reversed_range", {"z_offset_start_um": 2.0, "z_offset_end_um": -2.0,
                        "z_step_um": 0.5}),
    ("nonpositive_step", {"z_offset_start_um": -2.0, "z_offset_end_um": 2.0,
                          "z_step_um": 0.0}),
    ("nonfinite", {"z_offset_start_um": -2.0, "z_offset_end_um": float("nan"),
                   "z_step_um": 0.5}),
]


@pytest.mark.parametrize("check,offsets", _OFFSET_SWEEP_CASES,
                         ids=[case[0] for case in _OFFSET_SWEEP_CASES])
def test_offset_sweep_refusal_never_quotes_the_absolute_spelling(
    check, offsets, tmp_path, monkeypatch
):
    """run_adaptive_survey refuses absolute Z keys, so its refusals must not name them.

    The shared validator sees hit-relative offsets under the absolute spelling,
    so an untranslated message tells the caller their offsets are "absolute
    stage coordinates" and advises z_start_um -- which this very tool rejects a
    few lines earlier, so the refusal contradicts the tool that raised it.
    Drives the real refusal rather than reading the table, because the table is
    the fix and the message is the behaviour.
    """
    result, acquire, _seen = _run_acquire_on_hit_zstack(
        tmp_path, monkeypatch, {**offsets, "exposure_ms": 5}
    )

    error = result["error"]
    # The bare tokens, not the _um spellings: the untranslated reversed-range
    # message reads "z_end must be greater than z_start" and would slip past a
    # check for "z_start_um". And the claim, not the phrase -- a correct
    # message is allowed to say these are NOT absolute stage coordinates.
    assert "z_start" not in error and "z_end" not in error, error
    assert "are absolute stage coordinates" not in error, error
    assert "z_offset" in error or "z_step_um" in error, error
    if check == "equal_endpoints":
        # Folded in from the single-case version of this test: the equal-endpoint
        # refusal is the one that has to offer a route, and the route it offers
        # must be the one THIS tool accepts.
        assert "z_offset_start_um" in error and "z_offset_end_um" in error, error
        assert "each hit" in error, error
        assert "acquire_on_hit" in error and "timelapse" in error, error
    acquire.assert_not_called()


@pytest.mark.parametrize("check,offsets", _OFFSET_SWEEP_CASES,
                         ids=[case[0] for case in _OFFSET_SWEEP_CASES])
def test_offset_sweep_shape_raises_the_matching_typed_check(check, offsets):
    """Structural pin, not behavioural evidence: the translation keys off .check.

    Matching on message text would stop translating silently the day a message
    is reworded, so each shape the offsets path can produce must carry the
    check its entry is filed under.
    """
    with pytest.raises(tools.ZSweepShapeError) as caught:
        tools._build_acquisition_events(
            z_start=offsets["z_offset_start_um"],
            z_end=offsets["z_offset_end_um"],
            z_step=offsets["z_step_um"],
        )
    assert caught.value.check == check
    assert check in tools._OFFSET_SWEEP_REFUSALS


def test_absolute_sweep_refusal_still_teaches_the_absolute_rule():
    """D1 requires the absolute wording where the keys really are absolute."""
    with pytest.raises(tools.ZSweepShapeError) as caught:
        tools._build_acquisition_events(z_start=0.0, z_end=0.0, z_step=1.0)
    assert caught.value.check == "equal_endpoints"
    assert "absolute stage coordinates" in str(caught.value)
    assert 'protocol="timelapse"' in str(caught.value)
