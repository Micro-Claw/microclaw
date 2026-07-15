"""Fail-open logging behaviour for FocusFeedbackHook / IntensityAdaptiveHook.

These hooks run in pycro-manager acquisition threads, so a swallowed
SafetyViolation never reaches execute_tool's handler — the log line is the
operator's only signal. Each hook must distinguish a guard rejection (expected)
from a hardware error (loud) and never claim a correction it didn't make.
"""
import numpy as np
import pytest
from unittest.mock import MagicMock

from microclaw.hooks import (
    FocusFeedbackHook,
    HookBase,
    IntensityAdaptiveHook,
    PositionFilterHook,
)
from microclaw.safety import (
    CameraConstraints,
    SafetyConstraints,
    SafetyGuard,
    StageConstraints,
)


def _guard(z_max=200.0, max_exposure_ms=1000.0):
    return SafetyGuard(
        SafetyConstraints(
            stage=StageConstraints(z_min=0, z_max=z_max),
            camera=CameraConstraints(max_exposure_ms=max_exposure_ms),
        )
    )


# Two frames: first sets the reference metric, second triggers a correction
# because it is well below threshold. A blurry-ish array keeps laplacian low.
_SHARP = np.tile(np.arange(64, dtype=np.uint16), (64, 1))
_BLURRY = np.zeros((64, 64), dtype=np.uint16)


class TestFocusFeedbackHook:
    def _ctrl(self):
        ctrl = MagicMock()
        ctrl.core.get_focus_device.return_value = "DStage"
        ctrl.core.get_position.return_value = 199.9
        return ctrl

    def test_guard_rejection_logged_not_claimed(self, monkeypatch):
        # z_step pushes past z_max=200 → guard blocks every jog. The hook must
        # log blocked_by_guard with focus_correction False, not True.
        ctrl = self._ctrl()
        hook = FocusFeedbackHook(ctrl, _guard(z_max=200.0), z_step_um=1.0, max_jogs=3)
        hook.image_process_fn(_SHARP, {"time": 0}, None)      # sets reference
        hook.image_process_fn(_BLURRY, {"time": 1}, None)     # triggers

        entry = hook.get_summary()[-1]
        assert entry["focus_correction"] is False
        assert entry["outcome"] == "blocked_by_guard"
        assert "reason" in entry
        ctrl.core.set_position.assert_not_called()

    def test_hardware_error_distinct_from_guard(self, monkeypatch):
        monkeypatch.setattr("microclaw.hooks.snap_to_numpy", lambda c: _BLURRY)
        ctrl = self._ctrl()
        ctrl.core.get_position.return_value = 50.0            # well within bounds
        ctrl.core.set_position.side_effect = RuntimeError("stage stalled")
        hook = FocusFeedbackHook(ctrl, _guard(), z_step_um=1.0, max_jogs=3)
        hook.image_process_fn(_SHARP, {"time": 0}, None)
        hook.image_process_fn(_BLURRY, {"time": 1}, None)

        entry = hook.get_summary()[-1]
        assert entry["outcome"] == "hardware_error"
        assert entry["focus_correction"] is False

    def test_one_log_entry_per_triggering_frame(self, monkeypatch):
        monkeypatch.setattr("microclaw.hooks.snap_to_numpy", lambda c: _BLURRY)
        ctrl = self._ctrl()
        ctrl.core.get_position.return_value = 50.0
        hook = FocusFeedbackHook(ctrl, _guard(), z_step_um=1.0, max_jogs=3)
        hook.image_process_fn(_SHARP, {"time": 0}, None)
        hook.image_process_fn(_BLURRY, {"time": 1}, None)
        # reference frame logs nothing; the one triggering frame logs exactly once
        assert len(hook.get_summary()) == 1


class TestIntensityAdaptiveHook:
    def _ctrl(self, exposure=100.0):
        ctrl = MagicMock()
        ctrl.core.get_exposure.return_value = exposure
        return ctrl

    def test_guard_rejection_logged_as_blocked(self):
        # target_mean far above current mean → wants a big exposure increase that
        # the guard (max 200 ms) rejects. Must log "blocked", not swallow.
        ctrl = self._ctrl(exposure=100.0)
        hook = IntensityAdaptiveHook(
            ctrl, _guard(max_exposure_ms=200.0),
            target_mean=1000.0, tolerance=0.1, min_exposure_ms=1.0, max_exposure_ms=100000.0,
        )
        image = np.full((16, 16), 10.0)      # mean 10 << target 1000
        hook.image_process_fn(image, {"time": 5}, None)

        entry = hook.get_summary()[-1]
        assert entry["exposure_change"] == "blocked"
        assert "reason" in entry
        ctrl.core.set_exposure.assert_not_called()

    def test_hardware_error_logged_as_error(self):
        ctrl = self._ctrl(exposure=100.0)
        ctrl.core.set_exposure.side_effect = RuntimeError("camera busy")
        hook = IntensityAdaptiveHook(
            ctrl, _guard(max_exposure_ms=100000.0),
            target_mean=1000.0, tolerance=0.1,
        )
        image = np.full((16, 16), 10.0)
        hook.image_process_fn(image, {"time": 5}, None)

        entry = hook.get_summary()[-1]
        assert entry["exposure_change"] == "error"

    def test_success_logged(self):
        ctrl = self._ctrl(exposure=100.0)
        hook = IntensityAdaptiveHook(
            ctrl, _guard(max_exposure_ms=100000.0),
            target_mean=200.0, tolerance=0.1,
        )
        image = np.full((16, 16), 100.0)     # mean 100, target 200 → doubles exposure
        hook.image_process_fn(image, {"time": 5}, None)

        entry = hook.get_summary()[-1]
        assert "new_exposure_ms" in entry
        ctrl.core.set_exposure.assert_called_once()


class TestHookBaseWhere:
    """design/23 F2: the coordinates were always in the metadata. where() reads
    them (falling back through .get()), so every hook log is self-describing and
    no hook has to guess key names — the mistake that cost Episode A a re-scan."""

    def test_multiposition_metadata_carries_xy(self):
        meta = {
            "PositionName": "grid_r2_c1",
            "XPosition_um_Intended": -1034.2,
            "YPosition_um_Intended": 512.0,
            "Axes": {"position": "grid_r2_c1"},
        }
        assert HookBase.where(meta) == {
            "position": "grid_r2_c1", "x_um": -1034.2, "y_um": 512.0,
        }

    def test_zstack_metadata_carries_only_z(self):
        # A single-position Z-stack (what design/19 examined) has no XY keys, and
        # a missing key means "no such axis", read with .get() — not a wrong name.
        meta = {"ZPosition_um_Intended": 30.5, "Axes": {"position": "P0", "z": 3}}
        where = HookBase.where(meta)
        assert where == {"position": "P0", "z_um": 30.5}
        assert "x_um" not in where

    def test_position_falls_back_to_axes(self):
        # No PositionName, but Axes carries the label — the documented fallback.
        assert HookBase.where({"Axes": {"position": "P7"}})["position"] == "P7"

    def test_where_never_raises_on_empty_metadata(self):
        assert HookBase.where({}) == {"position": None}

    def test_log_stamps_position_onto_every_entry(self, tmp_path):
        hook = HookBase(log_path=str(tmp_path / "log.json"))
        meta = {"PositionName": "cell_03", "XPosition_um_Intended": 1.0,
                "YPosition_um_Intended": 2.0}
        hook.log(meta, snr=73.1)
        entry = hook.get_summary()[-1]
        # The Episode A fix: "cell_03, SNR 73" can never again omit where cell_03 was.
        assert entry == {"position": "cell_03", "x_um": 1.0, "y_um": 2.0, "snr": 73.1}

    def test_where_event_reads_absolute_coords(self):
        event = {"axes": {"position": "P1"}, "x": 10.0, "y": 20.0, "z": 5.0}
        assert HookBase.where_event(event) == {
            "position": "P1", "x_um": 10.0, "y_um": 20.0, "z_um": 5.0,
        }


class TestPositionFilterHookIdentity:
    """design/23 F2 latent bug: the live rig has no metadata["position_index"], so
    the old read collapsed every rejection under -1 and logged only the first. The
    hook now dedups on the stamped position identity from where()."""

    def test_rejections_keyed_to_distinct_positions(self, tmp_path):
        hook = PositionFilterHook(min_mean_intensity=100.0,
                                  log_path=str(tmp_path / "log.json"))
        dim = np.full((8, 8), 10.0)          # mean 10 < 100 → rejected
        hook.image_process_fn(dim, {"PositionName": "P0"}, None)
        hook.image_process_fn(dim, {"PositionName": "P1"}, None)
        hook.image_process_fn(dim, {"PositionName": "P1"}, None)   # dup, not re-logged
        logged = [e["position"] for e in hook.get_summary()]
        assert logged == ["P0", "P1"]        # two distinct, not collapsed under -1

    def test_dim_image_still_discarded(self, tmp_path):
        hook = PositionFilterHook(min_mean_intensity=100.0)
        assert hook.image_process_fn(np.full((8, 8), 10.0), {"PositionName": "P0"}, None) is None
        img = np.full((8, 8), 500.0)
        assert hook.image_process_fn(img, {"PositionName": "P1"}, None) is not None
