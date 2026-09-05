import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from microclaw import autofocus
from microclaw.tools import _sweep_payload


@pytest.fixture
def nikon():
    return json.loads((Path(__file__).resolve().parents[1] /
                       'design/76-r91-nikon-sweep.json').read_text(encoding="utf-8"))


@pytest.fixture
def recorded_sweep(nikon):
    return autofocus.SweepResult(
        z_positions=list(range(2110, 2601)), metric_values=nikon['readings'],
        best_z_um=2110.0, peak_interior=False,
        measured_z_positions=nikon['measured_z_positions'], planes_planned=491,
    )


def make_probe(core, values):
    core.get_allowed_property_values.return_value = []
    return autofocus.property_probe(core, 'lock', 'status', values,
                                    step_um=1, lo_um=2110, hi_um=2600)


def test_nikon_exact_value_spans(recorded_sweep):
    probe = make_probe(MagicMock(), ['Locked in focus'])
    payload = _sweep_payload(recorded_sweep, probe=probe)
    assert payload['value_spans'] == [
        {'value': 'Out of focus search range', 'z_um': [2110.0, 2385.0],
         'planes': [0, 276], 'in_range': False},
        {'value': 'Within range of focus search', 'z_um': [2385.975, 2399.0],
         'planes': [277, 290], 'in_range': False},
        {'value': 'Out of focus search range', 'z_um': [2400.0, 2599.0],
         'planes': [291, 490], 'in_range': False},
    ]
    assert payload['readings'] == recorded_sweep.metric_values
    assert payload['measured_z_positions'] == recorded_sweep.measured_z_positions


def test_nikon_refusal_coordinates_through_caller(monkeypatch, recorded_sweep):
    core = MagicMock()
    core.get_position.return_value = 2104.225
    probe = make_probe(core, ['Locked in focus'])
    monkeypatch.setattr(autofocus, 'sweep_autofocus', lambda *a, **k: recorded_sweep)
    monkeypatch.setattr(autofocus, '_restore', lambda *a: {'measured_um': 2104.225})
    result = autofocus.single_sweep_autofocus(
        SimpleNamespace(core=core), 490, 1, probe=probe,
        z_min_um=2110, z_max_um=2600,
    )
    assert "'Within range of focus search' at [2385.975, 2399.0] um (1 run)" in result.reason
    assert "'Out of focus search range' at [2110.0, 2599.0] um (2 runs)" in result.reason


def test_constant_refusal_sentence_unchanged():
    core = MagicMock()
    probe = make_probe(core, ['Locked in focus'])
    assert probe.admit(['blind'] * 5, [2110., 2111., 2112., 2113., 2114.]) == (
        "Every plane between 2110 and 2600 um returned the constant reading 'blind' "
        "(5 planes, 1 um step), and none of ['Locked in focus'] was seen. Either "
        "this window does not reach the band at all, or the sensor cannot "
        "evaluate focus here — an optical element out of the path reads "
        "constant too. Widen the window before suspecting the hardware."
    )


@pytest.mark.parametrize('matched', [False, True])
def test_summary_adds_zero_calls_after_sweep(monkeypatch, nikon, matched):
    core = MagicMock()
    core.get_focus_device.return_value = 'Z'
    core.get_position.return_value = 2110.0
    # Replay the recorded per-plane readings through the real stable-read path.
    core.get_property.side_effect = [v for v in nikon['readings'] for _ in range(3)]
    measured = iter(nikon['measured_z_positions'])
    monkeypatch.setattr(autofocus, 'settle_stage_move',
                        lambda *a: {'measured_um': next(measured),
                                    'arrival_unverifiable': False})
    values = ['Within range of focus search'] if matched else ['Locked in focus']
    probe = make_probe(core, values)
    sweep = autofocus.sweep_autofocus(SimpleNamespace(core=core), 2110, 2600, 1,
                                     probe=probe, move_to_best=False)
    planes = 278 if matched else 491
    assert core.set_position.call_count == planes
    assert core.get_property.call_count == 3 * planes
    assert core.snap_image.call_count == 0
    before = list(core.mock_calls)
    payload = _sweep_payload(sweep, probe=probe)
    probe.admit(sweep.metric_values, sweep.measured_z_positions)
    assert core.mock_calls == before
    assert sweep.stopped_early is matched
    if matched:
        assert payload['value_spans'] == [
            {'value': 'Out of focus search range', 'z_um': [2110.0, 2385.0],
             'planes': [0, 276], 'in_range': False},
            {'value': 'Within range of focus search', 'z_um': [2385.975, 2385.975],
             'planes': [277, 277], 'in_range': True},
        ]


def test_intermittent_refusal_is_bounded_by_distinct_values():
    readings = ['A' if i % 2 == 0 else 'B' for i in range(491)]
    z = [2110.0 + i for i in range(491)]
    reason = autofocus._band_admit(readings, z, {'Locked in focus'},
                                   1.0, 2110.0, 2600.0)
    assert "'A' at [2110.0, 2600.0] um (246 runs)" in reason
    assert "'B' at [2111.0, 2599.0] um (245 runs)" in reason
    assert reason.count(' at [') == 2
    assert len(reason) < 500


def test_value_span_precision_matches_measured_positions():
    sweep = autofocus.SweepResult(
        z_positions=[0.1, 1.0, 2.346], metric_values=['A'] * 3,
        best_z_um=0.1, peak_interior=False,
        measured_z_positions=[0.10000000000000009, 1.0, 2.3456789],
    )
    payload = _sweep_payload(sweep, probe=make_probe(MagicMock(), ['A']))
    assert payload['measured_z_positions'] == [0.1, 1.0, 2.346]
    assert payload['value_spans'][0]['z_um'] == [0.1, 2.346]
