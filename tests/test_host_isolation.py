"""The suite must not read the machine it runs on.

Two rig failures came from tests that asserted the host rather than the
behaviour: one passed on macOS and failed on Windows (a hardcoded path
separator, design/19), one passed on a laptop and failed on the microscope (a
mock controller reaching the lab's real EMU config, design/20).

conftest redirects ~/.microclaw and primes the EMU session cache. These tests
pin that, so the guard cannot be removed without something going red.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from microclaw import emu_manager, hook_manager, knowledge_manager, tools


_REAL_HOME = Path.home() / ".microclaw"


def _redirected_into(p: Path, tmp_path: Path) -> bool:
    """Is p inside this test's tmp_path?

    NOT "is p outside Path.home()" — on Windows pytest's tmp_path lives under
    C:/Users/<you>/AppData/Local/Temp, i.e. inside the home directory. That
    proxy failed on the rig and passed on macOS, which is the exact defect
    these tests exist to catch, committed inside the test that catches it.
    """
    return Path(p).resolve().is_relative_to(Path(tmp_path).resolve())


def test_saved_hooks_do_not_come_from_the_developers_home(tmp_path):
    # On the rig this manifest holds a hook the agent saved mid-session.
    assert _redirected_into(hook_manager.HOOKS_DIR, tmp_path)
    assert _redirected_into(hook_manager.MANIFEST, tmp_path)
    assert hook_manager.HOOKS_DIR != _REAL_HOME / "hooks"
    assert hook_manager.list_saved_hooks() == {}


def test_the_knowledge_base_does_not_come_from_the_developers_home(tmp_path):
    assert _redirected_into(knowledge_manager.KNOWLEDGE_PATH, tmp_path)
    assert knowledge_manager.KNOWLEDGE_PATH != _REAL_HOME / "knowledge.yaml"
    assert knowledge_manager.load_knowledge() == {}


def test_emu_discovery_never_touches_the_host(monkeypatch):
    """_cached_emu_properties must short-circuit before any host lookup.

    MagicMock implements __fspath__, so Path(ctrl.get_mm_app_dir()) yields a
    plausible non-existent path instead of raising, and find_mm_app_dir falls
    through to ~/.microclaw/emu.json and then to guessing at
    C:/Program Files/Micro-Manager-2.0. Every one of those can hit on a rig.
    """
    monkeypatch.setattr(
        emu_manager, "find_mm_app_dir",
        lambda *a, **k: pytest.fail("a unit test reached for the host's MM install"),
    )
    assert tools._cached_emu_properties(MagicMock()) is None


def test_get_system_state_reports_no_laser_map_by_default(unconstrained_guard):
    # The default rig for a unit test has no EMU map — and says so, rather than
    # omitting the field or inheriting whatever the lab machine has configured.
    assert tools.get_system_state(MagicMock(), unconstrained_guard)["lasers"] == (
        tools._NO_LASER_MAP
    )


def test_a_test_can_still_opt_in_to_an_emu_rig(monkeypatch, unconstrained_guard):
    # The guard is a default, not a wall: patches in the test body win.
    monkeypatch.setattr(tools, "_cached_emu_properties", lambda ctrl: {"stub": {}})
    monkeypatch.setattr(
        "microclaw.emu_manager.build_emu_map",
        lambda props: {"lasers": {1: {"enable": {"device": "L1", "property": "On"}}}},
    )
    ctrl = MagicMock()
    ctrl.core.get_property.return_value = "0"
    assert tools.get_system_state(ctrl, unconstrained_guard)["lasers"] == {
        1: {"enabled": "0"}
    }
