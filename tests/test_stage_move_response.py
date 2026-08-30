"""Block 66's response-vs-accuracy stage-move contract (design/66 Tests 1-17)."""
import inspect
import math
from unittest.mock import MagicMock

import pytest

from microclaw import controller
from microclaw.controller import (MicroscopeController, StageMoveError, settle_stage_move,
                                  stage_move_dispatch_failure)
from microclaw.safety import (NamedStageLimits, SafetyConfigError, SafetyConstraints,
                              SafetyGuard, StageConstraints, ParsedSafetyConfig)
from microclaw.tools import move_named_stage, move_stage_z
from microclaw.autofocus import FocusProbe, sweep_autofocus
from microclaw.hook_decisions import MoveNamedStage, UntrustedHookAdapter


class ParkedStage:
    """A bridge-shaped stage whose device, not the test caller, chooses its landing."""
    def __init__(self, start, landing):
        self.position = float(start)
        self.landing = float(landing)
        self.writes = []

    def get_position(self, device=None):
        return self.position

    def set_position(self, *args):
        target = args[-1]
        self.writes.append(float(target))
        self.position = self.landing

    def set_relative_position(self, delta):
        self.writes.append(float(delta))
        self.position = self.landing

    def device_busy(self, device):
        return False

    def get_focus_device(self):
        return "Z"


@pytest.fixture(autouse=True)
def fast_settle(monkeypatch):
    monkeypatch.setattr(controller, "STAGE_MOVE_POLL_S", 0.0)
    monkeypatch.setattr(controller, "STAGE_MOVE_STABILITY_WINDOW_S", 0.0)
    monkeypatch.setattr(controller, "STAGE_MOVE_REQUIRED_SAMPLES", 3)
    monkeypatch.setattr(controller, "STAGE_MOVE_TIMEOUT_S", 0.002)


def settle(core, target, start, policy="relative", configured=None):
    core.position = core.landing
    return settle_stage_move(core, "Z", target, start, policy, configured)


def test_01_large_move_stably_short_uses_relative_band_not_deadline():
    core = ParkedStage(0, 38.9)
    result = settle(core, 40, 0)
    assert result["band_source"] == "relative"
    assert result["tolerance_um"] == 4.0
    assert result["arrival_residual_um"] == 1.1
    assert result["elapsed_s"] < controller.STAGE_MOVE_TIMEOUT_S


@pytest.mark.parametrize("landing,passes", [(198.9, True), (100.0, False)])
def test_02_relative_band_accepts_small_residual_and_refuses_halfway(landing, passes):
    core = ParkedStage(0, landing)
    if passes:
        assert settle(core, 200, 0)["tolerance_um"] == 20.0
    else:
        with pytest.raises(StageMoveError):
            settle(core, 200, 0)


def test_03_stationary_axis_refuses_and_error_names_start_and_measured():
    core = ParkedStage(10, 10)
    with pytest.raises(StageMoveError) as caught:
        settle(core, 50, 10)
    assert caught.value.result["start_um"] == caught.value.result["measured_um"] == 10
    assert "did not move" in str(caught.value)


def test_04_premature_reads_do_not_pass_before_true_arrival():
    core = ParkedStage(0, 40)
    core.get_position = MagicMock(side_effect=[0, 0, 40, 40, 40])
    result = settle(core, 40, 0)
    assert core.get_position.call_count == 5
    assert result["measured_um"] == 40


@pytest.mark.parametrize(
    "distance,unverifiable,source,minimum_progress",
    [(1.9, True, "floor", 0), (2.0, True, "floor", 0),
     (2.1, False, "floor", .1), (2.22, False, "floor", .22),
     (20.0, False, "relative", 18.0)],
)
def test_05_floor_boundaries_and_minimum_progress(distance, unverifiable, source,
                                                   minimum_progress):
    landing = minimum_progress
    result = settle(ParkedStage(0, landing), distance, 0)
    assert result["arrival_unverifiable"] is unverifiable
    assert result["band_source"] == source


def test_05_configured_and_missing_start_are_unverifiable():
    assert settle(ParkedStage(0, 0), 1.5, 0, configured=1.5)["arrival_unverifiable"]
    assert settle(ParkedStage(0, 200), 200, None)["arrival_unverifiable"]


def test_06_registered_named_stage_receives_its_own_declared_accuracy():
    core = ParkedStage(0, 195)
    ctrl = MagicMock(core=core)
    guard = SafetyGuard(SafetyConstraints(named_stages=[
        NamedStageLimits("Z", 0, 300, 1.5), NamedStageLimits("Other", 0, 300, 7.0)
    ]))
    with pytest.raises(StageMoveError) as caught:
        move_named_stage(ctrl, guard, "Z", 200)
    assert caught.value.result["tolerance_um"] == 1.5
    assert caught.value.result["verification_kind"] == "configured_accuracy"


def _focus_guard(tolerance):
    return SafetyGuard(SafetyConstraints(stage=StageConstraints(
        z_min=0, z_max=100, z_move_tolerance_um=tolerance)))


def test_06_controller_set_z_delivers_the_declared_core_focus_accuracy():
    """The Core-focus half of test 06's delivery path.

    A 0.6 um residual is the discrimination: it is above the old 0.5 um
    constant and below the 2.0 um floor, and the 40 um displacement puts the
    relative band at 4.0 um. Every band this site could reach WITHOUT the
    declared value threaded -- relative 4.0, floor 2.0 -- accepts this
    landing, so the refusal can only come from `stage.z_move_tolerance_um`
    arriving as `configured_band_um`.
    """
    core = ParkedStage(0, 39.4)
    ctrl = MicroscopeController.__new__(MicroscopeController)
    ctrl._core = core
    ctrl._guard = _focus_guard(0.35)
    with pytest.raises(StageMoveError) as caught:
        ctrl.set_z(40)
    assert caught.value.result["tolerance_um"] == 0.35
    assert caught.value.result["band_source"] == "configured"
    assert caught.value.result["verification_kind"] == "configured_accuracy"
    assert settle(ParkedStage(0, 39.4), 40, 0)["within_tolerance"] is True


def test_06_sweep_probe_move_delivers_the_declared_core_focus_accuracy():
    """The same declared value must reach every probe move of a sweep.

    A one-micron plane step gives the relative rule the 2.0 um floor, which
    accepts the 0.6 um miss; only the declared 0.35 um band refuses it. The
    sweep reports the band it used, so the exporter never has to ask the live
    configuration for it.
    """
    core = ParkedStage(10, 10)
    core.set_position = lambda z: setattr(core, "position", float(z) - 0.6)
    ctrl = type("Ctrl", (), {})()
    ctrl.core = core
    ctrl._guard = _focus_guard(0.35)
    probe = FocusProbe(read=lambda: 1.0, choose=lambda values: 0,
                       admit=lambda values: None, exposures_per_plane=0,
                       describe="fixture")
    with pytest.raises(StageMoveError) as caught:
        sweep_autofocus(ctrl, 9, 11, 1, settle_ms=0, move_to_best=False,
                        probe=probe)
    assert caught.value.result["tolerance_um"] == 0.35
    assert caught.value.result["band_source"] == "configured"

    ctrl._guard = _focus_guard(None)
    swept = sweep_autofocus(ctrl, 9, 11, 1, settle_ms=0, move_to_best=False,
                            probe=probe)
    assert swept.configured_move_tolerance_um is None
    ctrl._guard = _focus_guard(0.9)
    assert sweep_autofocus(ctrl, 9, 11, 1, settle_ms=0, move_to_best=False,
                           probe=probe).configured_move_tolerance_um == 0.9


def test_06_declared_band_survives_a_controller_that_computes_its_guard():
    """The lookup must not depend on `_guard` sitting in the instance dict.

    `getattr(ctrl, "__dict__", {}).get("_guard")` returned None for a
    controller exposing `_guard` as a property or through __slots__, and a
    dropped declared band does not raise -- it silently widens the check to
    the package rule, which accepts this 0.6 um miss.
    """
    core = ParkedStage(10, 10)
    core.set_position = lambda z: setattr(core, "position", float(z) - 0.6)
    declared = _focus_guard(0.35)

    class ComputedGuard:
        core = None

        @property
        def _guard(self):
            return declared

    ctrl = ComputedGuard()
    ctrl.core = core
    probe = FocusProbe(read=lambda: 1.0, choose=lambda values: 0,
                       admit=lambda values: None, exposures_per_plane=0,
                       describe="fixture")
    with pytest.raises(StageMoveError) as caught:
        sweep_autofocus(ctrl, 9, 11, 1, settle_ms=0, move_to_best=False,
                        probe=probe)
    assert caught.value.result["tolerance_um"] == 0.35


def test_07_floor_restoration_is_stricter_than_relative_and_configured_wins():
    core = ParkedStage(200, 3)
    with pytest.raises(StageMoveError) as caught:
        settle(core, 0, 200, "floor")
    assert caught.value.result["band_policy"] == "floor"
    assert caught.value.result["band_source"] == "floor"
    assert settle(ParkedStage(200, 3), 0, 200, "floor", 4)["band_source"] == "configured"


def _write_config(tmp_path, body):
    path = tmp_path / "safety.yaml"
    path.write_text("schema_version: 3\nreviewed: true\nacquisition:\n"
                    "  confirm_above_frames: 5\n  confirm_above_duration_s: 5\n" + body,
                    encoding="utf-8")
    return path


def test_09_parses_distinct_focus_and_named_stage_tolerances(tmp_path):
    parsed = ParsedSafetyConfig.from_yaml(_write_config(tmp_path,
        "stage: {z_min: 0, z_max: 300, z_move_tolerance_um: 0.37}\n"
        "named_stages:\n- {device: A, min_um: 0, max_um: 300, move_tolerance_um: 1.3}\n"
        "- {device: B, min_um: -10, max_um: 10, move_tolerance_um: 2.7}\n"))
    assert parsed.constraints.stage.z_move_tolerance_um == .37
    assert [x.move_tolerance_um for x in parsed.constraints.named_stages] == [1.3, 2.7]


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf")])
def test_09_invalid_tolerance_fails_at_exact_path(tmp_path, value):
    path = _write_config(tmp_path, "stage:\n  z_min: 0\n  z_max: 10\n  z_move_tolerance_um: 1\n")
    text = path.read_text(encoding="utf-8").replace(
        "z_move_tolerance_um: 1", f"z_move_tolerance_um: {value}"
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SafetyConfigError, match=r"stage\.z_move_tolerance_um"):
        ParsedSafetyConfig.from_yaml(path)


@pytest.mark.parametrize("axis", ["x", "y"])
def test_10_inert_xy_keys_are_recognized_and_refused_exactly(tmp_path, axis):
    with pytest.raises(SafetyConfigError) as caught:
        ParsedSafetyConfig.from_yaml(_write_config(
            tmp_path, f"stage:\n  {axis}_move_tolerance_um: 1.2\n"))
    assert f"stage.{axis}_move_tolerance_um" in str(caught.value)
    assert "move_stage_xy" in str(caught.value)


def test_11_contract_arguments_have_no_defaults():
    for fn in (settle_stage_move, stage_move_dispatch_failure):
        for name in ("start_um", "band_policy", "configured_band_um"):
            assert inspect.signature(fn).parameters[name].default is inspect.Parameter.empty


def test_12_registered_z_tool_reads_fresh_start_immediately_before_write():
    core = ParkedStage(0, 38.9)
    core.get_position = MagicMock(side_effect=[7.0, 38.9, 38.9, 38.9])
    ctrl = MagicMock(core=core)
    guard = SafetyGuard(SafetyConstraints(stage=StageConstraints(z_min=0, z_max=100)))
    result = move_stage_z(ctrl, guard, 40)
    assert result["start_um"] == 7.0
    assert result["tolerance_um"] == pytest.approx(3.3)


def test_13_dispatch_failure_uses_the_same_effective_policy():
    core = ParkedStage(0, 0)
    err = stage_move_dispatch_failure(core, "Z", 200, 0, "relative", None,
                                      RuntimeError("driver"))
    assert err.result["tolerance_um"] == 20
    assert err.result["band_source"] == "relative"
    assert err.result["verification_kind"] == "response"


def test_14_wide_band_does_not_expand_exact_travel_bounds():
    core = ParkedStage(0, 500)
    ctrl = MagicMock(core=core)
    guard = SafetyGuard(SafetyConstraints(stage=StageConstraints(
        z_min=0, z_max=100, z_move_tolerance_um=1000)))
    with pytest.raises(Exception):
        move_stage_z(ctrl, guard, 500)
    assert core.writes == []


def test_08_autofocus_aggregates_unverifiable_planes_without_refusal():
    core = ParkedStage(10, 10)
    core.set_position = lambda z: setattr(core, "position", float(z))
    ctrl = type("Ctrl", (), {})()
    ctrl.core = core
    ctrl._guard = SafetyGuard(SafetyConstraints(stage=StageConstraints(z_min=0, z_max=20)))
    probe = FocusProbe(read=lambda: 1.0, choose=lambda values: 0,
                       admit=lambda values: None, exposures_per_plane=0,
                       describe="fixture")
    result = sweep_autofocus(ctrl, 9, 11, 1, settle_ms=0,
                             move_to_best=False, probe=probe)
    assert result.arrival_unverifiable_indices == [0, 1, 2]
    assert len(result.arrival_unverifiable_indices) == 3


def test_08_hook_records_unverifiable_response_fields_and_floor_restoration():
    core = ParkedStage(10, 10)
    core.set_position = lambda device, target: setattr(core, "position", float(target))
    guard = SafetyGuard(SafetyConstraints(named_stages=[NamedStageLimits("Axis", 0, 100)]))
    hook = UntrustedHookAdapter(object())
    hook.configure_named_stage(core=core, guard=guard, device="Axis", min_um=0,
                               max_um=100, max_writes=2, initial_value=10,
                               restore="entry", action_plan=None)
    hook._apply_named_stage(MoveNamedStage(11), {"axes": {"time": 0}})
    hook.restore_named_stage()
    writes = [row for row in hook._log
              if row.get("event") == "hook_action"
              and row.get("action", {}).get("kind") == "MoveNamedStage"]
    assert writes[0]["arrival_unverifiable"] is True
    assert writes[0]["band_policy"] == "relative"
    assert writes[1]["band_policy"] == "floor"


def test_09_tolerance_without_bounds_is_exact_path_error(tmp_path):
    with pytest.raises(SafetyConfigError) as caught:
        ParsedSafetyConfig.from_yaml(_write_config(
            tmp_path, "stage: {z_move_tolerance_um: 0.4}\n"))
    assert "stage.z_move_tolerance_um" in str(caught.value)
    assert "requires declared travel bounds" in str(caught.value)
