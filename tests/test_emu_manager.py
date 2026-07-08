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


# ── _parse_properties on the REAL config format (design/14 §2a) ─────────────
# Verbatim shapes from the amr_test rig: "Device-Property" strings (never
# "Device::Property"), " - On/Off value" and " state N" metadata keys,
# hyphenated device labels, and one label a prefix of another.

REAL = {
    "Laser 3 enable": "Luxx638-Laser Operation Select",
    "Laser 3 enable - On value": "On",
    "Laser 3 enable - Off value": "Off",
    "Filter wheel position": "Servos-Position3",
    "Filter wheel position state 3": "32000",
    "Filter wheel position state 0": "5000",
    "Focus-lock enable fine": "Focus-lock-Enable Fine",
    "Two-state device 1": "Thorlabs ELL9-1-Label",
    "Laser 3 power percentage": "Luxx638-Laser Power Set-point Select [%]",
    "Laser 3 power percentage slope": "1.0",
    "Laser 3 power percentage offset": "0.0",
    "UV pulse duration": "Unallocated",
}
DEVICES = ["Luxx638", "Servos", "Focus-lock", "Thorlabs ELL9", "Thorlabs ELL9-1"]


class TestParseProperties:
    def test_device_and_property_populated(self):
        p = emu_manager._parse_properties(REAL, DEVICES)
        assert p["Laser 3 enable"]["device"] == "Luxx638"
        assert p["Laser 3 enable"]["property"] == "Laser Operation Select"
        assert p["Filter wheel position"]["device"] == "Servos"
        assert p["Filter wheel position"]["property"] == "Position3"

    def test_hyphenated_device_labels_split_on_the_longest_match(self):
        p = emu_manager._parse_properties(REAL, DEVICES)
        assert p["Focus-lock enable fine"]["device"] == "Focus-lock"  # not "Focus"
        assert p["Focus-lock enable fine"]["property"] == "Enable Fine"
        assert p["Two-state device 1"]["device"] == "Thorlabs ELL9-1"  # not "Thorlabs ELL9"
        assert p["Two-state device 1"]["property"] == "Label"

    def test_metadata_is_folded_into_the_parent_property(self):
        p = emu_manager._parse_properties(REAL, DEVICES)
        assert p["Laser 3 enable"]["on"] == "On"
        assert p["Laser 3 enable"]["off"] == "Off"
        assert p["Filter wheel position"]["states"] == {0: "5000", 3: "32000"}
        assert p["Laser 3 power percentage"]["slope"] == "1.0"
        assert p["Laser 3 power percentage"]["offset"] == "0.0"
        # 68/120 keys used to leak as top-level pseudo-properties.
        assert "Laser 3 enable - On value" not in p
        assert "Filter wheel position state 3" not in p
        assert "Laser 3 power percentage slope" not in p

    def test_legacy_double_colon_format_still_parses(self):
        p = emu_manager._parse_properties(
            {"Laser 0 enable": "Laser::OnOff"}, DEVICES
        )
        assert p["Laser 0 enable"]["device"] == "Laser"
        assert p["Laser 0 enable"]["property"] == "OnOff"

    def test_no_device_list_leaves_split_unpopulated(self):
        p = emu_manager._parse_properties(REAL)
        assert "device" not in p["Laser 3 enable"]
        assert p["Laser 3 enable"]["mm_property_string"] == (
            "Luxx638-Laser Operation Select"
        )

    def test_unallocated_placeholder_kept_verbatim(self):
        p = emu_manager._parse_properties(REAL, DEVICES)
        assert p["UV pulse duration"] == {"mm_property_string": "Unallocated"}


def test_read_emu_config_passes_device_labels(tmp_path):
    mm = tmp_path / "MM"
    emu_dir = mm / "EMU"
    emu_dir.mkdir(parents=True)
    (emu_dir / "config.uicfg").write_text(json.dumps({
        "defaultConfigurationName": "conf",
        "pluginConfigurations": [{
            "configurationName": "conf",
            "pluginName": "htSMLM",
            "properties": {
                "Laser 3 enable": "Luxx638-Laser Operation Select",
                "Laser 3 enable - On value": "On",
            },
            "settings": {},
        }],
    }))
    cfg = emu_manager.read_emu_config(mm, ["Luxx638"])
    entry = cfg["properties"]["Laser 3 enable"]
    assert entry["device"] == "Luxx638"
    assert entry["on"] == "On"
    assert cfg["plugin_name"] == "htSMLM"


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
