import numpy as np
import pytest
from unittest.mock import MagicMock

from microclaw.controller import PluginAccess
from microclaw.hooks import (
    MMAutofocusPluginHook,
    MMPluginHook,
    PRECODED_HOOK_REGISTRY,
)
from microclaw.tools import list_mm_plugins
from microclaw.safety import (
    PluginConstraints,
    SafetyConstraints,
    SafetyGuard,
    SafetyViolation,
    StageConstraints,
)


def _guard(**plugin_kwargs):
    return SafetyGuard(
        SafetyConstraints(
            stage=StageConstraints(z_min=0, z_max=200),
            plugins=PluginConstraints(**plugin_kwargs),
        )
    )


def _ctrl_with_plugin(plugin_obj):
    """A controller whose plugins.get_object returns the given fake plugin."""
    ctrl = MagicMock()
    ctrl.plugins.get_object.return_value = plugin_obj
    return ctrl


def _ctrl_with_autofocus(af_obj):
    ctrl = MagicMock()
    ctrl.plugins.get_autofocus_method.return_value = af_obj
    return ctrl


class TestRegistry:
    def test_strategies_registered(self):
        assert PRECODED_HOOK_REGISTRY["mm_plugin_analyzer"] is MMPluginHook
        assert PRECODED_HOOK_REGISTRY["autofocus_mm_plugin"] is MMAutofocusPluginHook


class TestMMPluginHook:
    def test_blocklist_denies_at_construction(self):
        guard = _guard(blocked=["org.bad.Plugin"])
        with pytest.raises(SafetyViolation, match="blocked"):
            MMPluginHook(_ctrl_with_plugin(MagicMock()), guard, "org.bad.Plugin")

    def test_keeps_when_score_above_threshold(self):
        plugin = MagicMock()
        plugin.analyze.return_value = 9.0
        hook = MMPluginHook(
            _ctrl_with_plugin(plugin), _guard(), "org.lab.Score", reject_below=5.0
        )
        img = np.ones((4, 4), dtype=np.uint16)
        result = hook.image_process_fn(img, {"time": 0}, None)
        assert result is not None
        out_img, out_meta = result
        assert out_img is img
        assert hook.get_summary()[-1]["kept"] is True

    def test_rejects_when_score_below_threshold(self):
        plugin = MagicMock()
        plugin.analyze.return_value = 1.0
        hook = MMPluginHook(
            _ctrl_with_plugin(plugin), _guard(), "org.lab.Score", reject_below=5.0
        )
        result = hook.image_process_fn(np.ones((4, 4)), {"time": 0}, None)
        assert result is None
        assert hook.get_summary()[-1]["kept"] is False

    def test_only_scalar_crosses_bridge(self):
        plugin = MagicMock()
        plugin.analyze.return_value = 3.0
        hook = MMPluginHook(_ctrl_with_plugin(plugin), _guard(), "org.lab.Score")
        img = np.full((2, 2), 7.0)
        hook.image_process_fn(img, {"time": 0}, None)
        (arg,), _ = plugin.analyze.call_args
        assert isinstance(arg, float)
        assert arg == pytest.approx(7.0)

    def test_fail_open_on_plugin_error(self):
        plugin = MagicMock()
        plugin.analyze.side_effect = RuntimeError("java boom")
        hook = MMPluginHook(
            _ctrl_with_plugin(plugin), _guard(), "org.lab.Score", reject_below=5.0
        )
        img = np.ones((4, 4))
        result = hook.image_process_fn(img, {"time": 2}, None)
        assert result == (img, {"time": 2})  # image kept despite error
        assert "plugin_error" in hook.get_summary()[-1]


class TestPluginAccess:
    def test_list_plugins_groups_by_role(self):
        pm = MagicMock()
        pm.get_autofocus_plugins.return_value.key_set.return_value = [
            "org.mm.OughtaFocus",
            "org.mm.Autofocus",
        ]
        pm.get_processor_plugins.return_value.key_set.return_value = ["org.mm.Proc"]
        pm.get_menu_plugins.return_value.key_set.return_value = []
        studio = MagicMock()
        studio.plugins.return_value = pm

        access = PluginAccess(studio)
        out = access.list_plugins()
        assert out["autofocus"] == ["org.mm.Autofocus", "org.mm.OughtaFocus"]  # sorted
        assert out["processor"] == ["org.mm.Proc"]
        assert out["menu"] == []

    def test_list_plugins_tolerates_role_failure(self):
        pm = MagicMock()
        pm.get_autofocus_plugins.return_value.key_set.return_value = ["a"]
        pm.get_processor_plugins.side_effect = RuntimeError("no processors")
        pm.get_menu_plugins.return_value.key_set.return_value = ["m"]
        studio = MagicMock()
        studio.plugins.return_value = pm

        out = PluginAccess(studio).list_plugins()
        assert out["autofocus"] == ["a"]
        assert out["processor"] == []  # failure degrades to empty, not raise
        assert out["menu"] == ["m"]


class TestListMMPluginsTool:
    def test_returns_plugins_and_hint(self):
        ctrl = MagicMock()
        ctrl.plugins.list_plugins.return_value = {"autofocus": ["org.mm.AF"]}
        out = list_mm_plugins(ctrl, MagicMock())
        assert out["plugins"] == {"autofocus": ["org.mm.AF"]}
        assert "hint" in out

    def test_reports_error_on_failure(self):
        ctrl = MagicMock()
        ctrl.plugins.list_plugins.side_effect = RuntimeError("pre-#2401 MM")
        out = list_mm_plugins(ctrl, MagicMock())
        assert "error" in out


class TestMMAutofocusPluginHook:
    def test_motion_gate_denies_by_default(self):
        with pytest.raises(SafetyViolation, match="allow_hardware_motion"):
            MMAutofocusPluginHook(_ctrl_with_autofocus(MagicMock()), _guard())

    def test_runs_and_keeps_event_when_z_in_bounds(self):
        af = MagicMock()
        af.full_focus.return_value = 100.0
        hook = MMAutofocusPluginHook(
            _ctrl_with_autofocus(af), _guard(allow_hardware_motion=True)
        )
        event = {"axes": {"position": 0}}
        assert hook.post_hardware_hook_fn(event) is event
        assert hook.get_summary()[-1]["best_z_um"] == 100.0

    def test_passive_guard_aborts_without_redriving(self):
        af = MagicMock()
        af.full_focus.return_value = 9999.0  # out of z_max=200 bounds
        ctrl = _ctrl_with_autofocus(af)
        hook = MMAutofocusPluginHook(ctrl, _guard(allow_hardware_motion=True))
        result = hook.post_hardware_hook_fn({"axes": {}})
        assert result is None  # capture skipped, acquisition signalled to stop
        # PASSIVE: microclaw must NOT re-drive Z after an unsafe result.
        ctrl.core.set_position.assert_not_called()
        assert hook.get_summary()[-1]["autofocus"] == "unsafe_abort"

    def test_skips_on_plugin_error_without_aborting(self):
        af = MagicMock()
        af.full_focus.side_effect = RuntimeError("no focus")
        hook = MMAutofocusPluginHook(
            _ctrl_with_autofocus(af), _guard(allow_hardware_motion=True)
        )
        event = {"axes": {}}
        assert hook.post_hardware_hook_fn(event) is event  # capture proceeds
        assert hook.get_summary()[-1]["autofocus"] == "skipped"
