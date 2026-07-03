"""Unit tests for emu_manager.find_mm_app_dir's fallback chain (design/12).

Order under test: (1) authoritative Java call via ctrl, (2) cache, (3) path
guessing. The cache and candidate paths are monkeypatched onto tmp dirs so the
tests never touch the real ~/.microclaw or the machine's MM install.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from microclaw import emu_manager


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    """Point the emu cache at a tmp file and clear the candidate path list."""
    cache_dir = tmp_path / ".microclaw"
    monkeypatch.setattr(emu_manager, "_MICROCLAW_DIR", cache_dir)
    monkeypatch.setattr(emu_manager, "_EMU_CACHE", cache_dir / "emu.json")
    # Default: no guessed candidates unless a test provides them.
    monkeypatch.setattr(emu_manager, "_candidate_mm_dirs", lambda: [])
    return cache_dir


def _make_mm_dir(base: Path) -> Path:
    """Create a dir that looks like an MM install root (has plugins/)."""
    mm = base / "Micro-Manager-2.0"
    (mm / "plugins").mkdir(parents=True)
    return mm


def _ctrl_returning(app_dir) -> MagicMock:
    ctrl = MagicMock()
    ctrl.get_mm_app_dir.return_value = str(app_dir) if app_dir is not None else None
    return ctrl


def test_live_answer_preferred_and_cached(tmp_cache, tmp_path):
    """A valid live answer wins and is written through to the cache."""
    mm = _make_mm_dir(tmp_path)
    ctrl = _ctrl_returning(mm)

    result = emu_manager.find_mm_app_dir(ctrl)

    assert result == mm
    cached = json.loads(emu_manager._EMU_CACHE.read_text())
    assert cached["mm_app_dir"] == str(mm)


def test_none_ctrl_falls_through_to_cache(tmp_cache, tmp_path):
    """ctrl=None skips the live call and uses the cache."""
    mm = _make_mm_dir(tmp_path)
    emu_manager.save_mm_app_dir(str(mm))

    result = emu_manager.find_mm_app_dir(None)

    assert result == mm


def test_disconnected_ctrl_falls_through_to_cache(tmp_cache, tmp_path):
    """A ctrl whose get_mm_app_dir() returns None falls to the cache."""
    mm = _make_mm_dir(tmp_path)
    emu_manager.save_mm_app_dir(str(mm))
    ctrl = _ctrl_returning(None)

    result = emu_manager.find_mm_app_dir(ctrl)

    assert result == mm


def test_falls_through_to_path_guessing(tmp_cache, tmp_path, monkeypatch):
    """With no live answer and no cache, path guessing (with EMU) wins."""
    mm = _make_mm_dir(tmp_path)
    (mm / "plugins" / "EMU_1.0.jar").write_text("")  # make _has_emu() pass
    monkeypatch.setattr(emu_manager, "_candidate_mm_dirs", lambda: [mm])

    result = emu_manager.find_mm_app_dir(None)

    assert result == mm


def test_bogus_live_answer_rejected(tmp_cache, tmp_path):
    """A live dir lacking plugins/ and mmplugins/ is not trusted or cached."""
    bogus = tmp_path / "home-imagej"
    bogus.mkdir()  # exists but no plugins dir → _looks_like_mm_dir() False
    ctrl = _ctrl_returning(bogus)

    # Cache holds a good, different install → the chain must fall to it.
    good = _make_mm_dir(tmp_path)
    emu_manager.save_mm_app_dir(str(good))

    result = emu_manager.find_mm_app_dir(ctrl)

    assert result == good
    # The bogus answer must not have overwritten the cache.
    cached = json.loads(emu_manager._EMU_CACHE.read_text())
    assert cached["mm_app_dir"] == str(good)


def test_live_answer_wins_over_stale_cache(tmp_cache, tmp_path):
    """A valid live answer overrides an existing (stale) cache entry."""
    stale = _make_mm_dir(tmp_path / "old")
    emu_manager.save_mm_app_dir(str(stale))
    current = _make_mm_dir(tmp_path / "new")
    ctrl = _ctrl_returning(current)

    result = emu_manager.find_mm_app_dir(ctrl)

    assert result == current
    cached = json.loads(emu_manager._EMU_CACHE.read_text())
    assert cached["mm_app_dir"] == str(current)


def test_looks_like_mm_dir(tmp_path):
    mm_plugins = tmp_path / "a"
    (mm_plugins / "mmplugins").mkdir(parents=True)
    assert emu_manager._looks_like_mm_dir(mm_plugins)

    plugins = tmp_path / "b"
    (plugins / "plugins").mkdir(parents=True)
    assert emu_manager._looks_like_mm_dir(plugins)

    empty = tmp_path / "c"
    empty.mkdir()
    assert not emu_manager._looks_like_mm_dir(empty)
