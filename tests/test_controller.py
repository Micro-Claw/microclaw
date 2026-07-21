"""Controller-level unit tests (no live Micro-Manager).

MicroscopeController.__init__ opens ZMQ connections, so these construct the
object without __init__ and inject mock Core/Studio, then exercise the pure
position-store / guarded-seam logic.
"""
from unittest.mock import MagicMock

import pytest

from microclaw.controller import (
    MicroscopeController,
    PositionListConflict,
    PositionProjection,
)
from microclaw.safety import (
    SafetyConstraints,
    SafetyGuard,
    SafetyViolation,
    StageConstraints,
)


def make_controller(guard=None):
    ctrl = MicroscopeController.__new__(MicroscopeController)
    ctrl._port = 4827
    ctrl._core = MagicMock()
    ctrl._core.get_xy_stage_device.return_value = "DXYStage"
    ctrl._core.get_focus_device.return_value = "DStage"
    ctrl._studio = MagicMock()
    ctrl._plugins = None
    ctrl._guard = guard
    ctrl._positions = []
    return ctrl


def bounded_guard():
    return SafetyGuard(
        SafetyConstraints(stage=StageConstraints(z_min=0, z_max=200,
                                                 x_min=-500, x_max=500,
                                                 y_min=-500, y_max=500))
    )


class TestGuardedSeam:
    def test_set_z_guarded(self):
        ctrl = make_controller(guard=bounded_guard())
        with pytest.raises(SafetyViolation):
            ctrl.set_z(999.0)
        ctrl._core.set_position.assert_not_called()

    def test_set_z_in_range_moves(self):
        ctrl = make_controller(guard=bounded_guard())
        ctrl.set_z(100.0)
        ctrl._core.set_position.assert_called_once_with(100.0)

    def test_set_xy_guarded(self):
        ctrl = make_controller(guard=bounded_guard())
        with pytest.raises(SafetyViolation):
            ctrl.set_xy(9999.0, 0.0)
        ctrl._core.set_xy_position.assert_not_called()

    def test_no_guard_does_not_block(self):
        ctrl = make_controller(guard=None)
        ctrl.set_z(999.0)  # no guard → no check
        ctrl._core.set_position.assert_called_once_with(999.0)


class TestGoToPositionZOnly:
    def test_z_only_skips_xy(self):
        ctrl = make_controller()
        ctrl._positions = [{"name": "Zonly", "z_um": 42.0}]
        ctrl.go_to_position("Zonly")
        ctrl._core.set_xy_position.assert_not_called()
        ctrl._core.set_position.assert_called_once_with(42.0)

    def test_xy_and_z_both_set(self):
        ctrl = make_controller()
        ctrl._positions = [{"name": "P", "x_um": 1.0, "y_um": 2.0, "z_um": 3.0}]
        ctrl.go_to_position("P")
        ctrl._core.set_xy_position.assert_called_once_with(1.0, 2.0)
        ctrl._core.set_position.assert_called_once_with(3.0)


class TestNativePositionListPrepareCommit:
    def test_prepare_loads_temporary_native_list(self, tmp_path, monkeypatch):
        ctrl = make_controller()
        f = tmp_path / "positions.pos"
        f.write_text("native")
        candidate = MagicMock()
        monkeypatch.setattr("pycromanager.JavaObject", lambda *a, **k: candidate)
        projection = PositionProjection(
            [{"name": "P", "x_um": 1.0, "y_um": 2.0}], [], []
        )
        monkeypatch.setattr(ctrl, "_project_mm_position_list", lambda p: projection)

        prepared = ctrl.prepare_position_list(str(f))

        candidate.load.assert_called_once_with(str(f))
        assert prepared.candidate is candidate
        assert prepared.projection is projection
        ctrl._studio.positions().set_position_list.assert_not_called()

    def test_file_change_during_java_load_is_rejected(self, tmp_path, monkeypatch):
        ctrl = make_controller()
        f = tmp_path / "positions.pos"
        f.write_text("before")
        candidate = MagicMock()
        candidate.load.side_effect = lambda path: f.write_text("after")
        monkeypatch.setattr("pycromanager.JavaObject", lambda *a, **k: candidate)

        with pytest.raises(PositionListConflict) as exc:
            ctrl.prepare_position_list(str(f))

        assert exc.value.issues[0]["code"] == "file_changed_during_load"
        ctrl._studio.positions().set_position_list.assert_not_called()

    def test_commit_publishes_java_first_then_projection(self):
        from microclaw.controller import PreparedPositionList

        ctrl = make_controller()
        projection = PositionProjection(
            [{"name": "P", "z_um": 12.0}], [{"name": "P", "z_um": 12.0}], []
        )
        candidate = object()
        prepared = PreparedPositionList(candidate, projection, "p.pos", "hash")

        ctrl.commit_position_list(prepared)

        ctrl._studio.positions().set_position_list.assert_called_once_with(candidate)
        assert ctrl._positions == projection.positions

    def test_save_uses_current_native_list(self, tmp_path, monkeypatch):
        ctrl = make_controller()
        plist = ctrl._studio.positions().get_position_list.return_value
        monkeypatch.setattr(
            ctrl, "_project_mm_position_list",
            lambda p: PositionProjection([], [], []),
        )
        f = tmp_path / "positions.pos"

        ctrl.save_position_list(str(f))

        plist.save.assert_called_once_with(str(f))


class TestGetMmAppDir:
    """get_mm_app_dir() resilience (design/12): evict the pyjavaz static-class
    cache collision (all static JavaClass shadows share the 'java.lang.Class'
    key, so the first-wrapped class wins) and fall back to System user.dir."""

    def _connected_ctrl(self):
        ctrl = make_controller()
        ctrl._core.get_version_info.return_value = "MMCore v"  # is_connected() -> True
        return ctrl

    @staticmethod
    def _java_class(mapping):
        """Return a fake JavaClass factory dispatching on classpath.

        mapping maps classpath -> callable(port) producing the proxy (or a
        callable that raises to simulate a construction failure).
        """
        def factory(classpath, port=None):
            producer = mapping[classpath]
            return producer()
        return factory

    def test_imagej_snake_case(self, monkeypatch):
        import pycromanager
        ij = type("IJ", (), {"get_directory": staticmethod(lambda a: "/Applications/MM/")})()
        monkeypatch.setattr(
            pycromanager, "JavaClass", self._java_class({"ij.IJ": lambda: ij})
        )
        ctrl = self._connected_ctrl()
        from pathlib import Path
        assert ctrl.get_mm_app_dir() == str(Path("/Applications/MM"))

    def test_imagej_camelcase_fallback(self, monkeypatch):
        import pycromanager
        # Only the camelCase alias is exposed on the proxy this run.
        ij = type("IJ", (), {"getDirectory": staticmethod(lambda a: "/opt/mm/")})()
        monkeypatch.setattr(
            pycromanager, "JavaClass", self._java_class({"ij.IJ": lambda: ij})
        )
        ctrl = self._connected_ctrl()
        from pathlib import Path
        assert ctrl.get_mm_app_dir() == str(Path("/opt/mm"))

    def test_new_static_java_class_evicts_collision_key(self, monkeypatch):
        """The helper pops the colliding 'java.lang.Class' cache key off the
        live bridge's class factory so pyjavaz regenerates the right shadow."""
        import weakref
        import pycromanager
        from pyjavaz.bridge import Bridge
        from microclaw import controller as ctrl_mod

        class FakeFactory:
            def __init__(self):
                self.classes = {"java.lang.Class": object()}  # poisoned entry

        class FakeBridge:
            def __init__(self):
                self._class_factory = FakeFactory()

        fake_bridge = FakeBridge()
        monkeypatch.setitem(Bridge._cached_bridges_by_port, 4827, weakref.ref(fake_bridge))
        seen = {}
        monkeypatch.setattr(
            pycromanager, "JavaClass",
            lambda cp, port=None: seen.update(cp=cp, port=port) or "SHADOW",
        )

        out = ctrl_mod._new_static_java_class(4827, "ij.IJ")

        assert out == "SHADOW"
        assert seen == {"cp": "ij.IJ", "port": 4827}
        # The colliding key was evicted, forcing a fresh shadow next generation.
        assert "java.lang.Class" not in fake_bridge._class_factory.classes

    def test_falls_back_to_user_dir(self, monkeypatch):
        import pycromanager
        # ij.IJ resolves to the wrong (collision) shadow with no get_directory;
        # get_mm_app_dir must fall through to the System user.dir probe.
        bare_ij = object()
        system = type("Sys", (), {"get_property": staticmethod(lambda a: "/opt/mm")})()
        monkeypatch.setattr(
            pycromanager,
            "JavaClass",
            self._java_class({"ij.IJ": lambda: bare_ij, "java.lang.System": lambda: system}),
        )
        ctrl = self._connected_ctrl()
        from pathlib import Path
        assert ctrl.get_mm_app_dir() == str(Path("/opt/mm"))

    def test_both_probes_fail_returns_none(self, monkeypatch):
        import pycromanager
        bare_ij = object()
        bare_system = object()
        monkeypatch.setattr(
            pycromanager,
            "JavaClass",
            self._java_class({"ij.IJ": lambda: bare_ij, "java.lang.System": lambda: bare_system}),
        )
        ctrl = self._connected_ctrl()
        assert ctrl.get_mm_app_dir() is None

    def test_not_connected_returns_none(self, monkeypatch):
        import pycromanager
        # Should never touch JavaClass when disconnected.
        monkeypatch.setattr(
            pycromanager, "JavaClass",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")),
        )
        ctrl = make_controller()
        ctrl._core.get_version_info.side_effect = RuntimeError("no bridge")
        assert ctrl.get_mm_app_dir() is None
