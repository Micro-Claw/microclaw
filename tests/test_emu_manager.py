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


# ── build_emu_map / resolve_emu_device (design/14 §1/§2) ─────────────────────
# Slot layout from the amr_test rig: slot 2 = Cobolt561, slot 3 = Luxx638.
# The agent once read Mode2 (the 561's line) and declared the 638 verified.

FULL = {
    "Laser 2 enable": "Cobolt561-Laser",
    "Laser 3 enable": "Luxx638-Laser Operation Select",
    "Laser 3 power percentage": "Luxx638-Laser Power Set-point Select [%]",
    "Laser trigger 2 mode": "Laser Trigger-Mode2",
    "Laser trigger 3 mode": "Laser Trigger-Mode3",
    "Laser trigger 3 sequence": "Laser Trigger-Sequence3",
    "Filter wheel position": "Servos-Position3",
    "Filter wheel position state 0": "5000",
    "Filter wheel position state 3": "32000",
    "Z stage focus locking": "PIZStage-External sensor",
    "Z stage focus locking - On value": "1",
    "Z stage focus locking - Off value": "0",
    "QPD X": "Analog Input-AnalogInput0",
    "Two-state device 1": "Thorlabs ELL9-1-Label",
    "UV pulse duration": "Unallocated",
    "Booster enable fine": "Enter value",
}
FULL_DEVICES = [
    "Cobolt561", "Luxx638", "Laser Trigger", "Servos", "PIZStage",
    "Analog Input", "Thorlabs ELL9", "Thorlabs ELL9-1",
]


@pytest.fixture
def emu_map():
    props = emu_manager._parse_properties(FULL, FULL_DEVICES)
    return emu_manager.build_emu_map(props)


class TestBuildEmuMap:
    def test_laser_slot_pairs_enable_with_its_own_trigger_line(self, emu_map):
        """The §1 regression: slot 3 is Luxx638 with Mode3; slot 2 is Cobolt561."""
        lasers = emu_map["lasers"]
        assert lasers[3]["enable"]["device"] == "Luxx638"
        assert lasers[3]["trigger_mode"]["property"] == "Mode3"
        assert lasers[3]["trigger_sequence"]["property"] == "Sequence3"
        assert lasers[2]["enable"]["device"] == "Cobolt561"
        assert lasers[2]["trigger_mode"]["property"] == "Mode2"

    def test_power_property_is_the_writable_select_variant(self, emu_map):
        assert emu_map["lasers"][3]["power_pct"]["property"] == (
            "Laser Power Set-point Select [%]"
        )

    def test_filter_wheel_carries_the_state_table(self, emu_map):
        fw = emu_map["filter_wheels"][1]
        assert fw["device"] == "Servos"
        assert fw["property"] == "Position3"
        assert fw["states"] == {0: "5000", 3: "32000"}

    def test_focus_lock_with_on_off_and_qpd(self, emu_map):
        lock = emu_map["focus_lock"]
        assert lock["device"] == "PIZStage"
        assert lock["property"] == "External sensor"
        assert lock["on"] == "1" and lock["off"] == "0"
        assert lock["qpd"]["x"]["property"] == "AnalogInput0"

    def test_unallocated_is_names_only(self, emu_map):
        assert emu_map["unallocated"] == ["Booster enable fine", "UV pulse duration"]

    def test_unmatched_allocated_entries_land_in_other(self, emu_map):
        assert "Two-state device 1" in emu_map["other"]
        assert emu_map["other"]["Two-state device 1"]["device"] == "Thorlabs ELL9-1"

    def test_simple_ui_alternate_laser_shape(self):
        # Plugin-specific names (§2a): the demo "Simple UI" says "Laser0 on/off".
        props = emu_manager._parse_properties(
            {"Laser0 on/off": "Laser-OnOff", "Laser0 power": "Laser-Power"},
            ["Laser"],
        )
        lasers = emu_manager.build_emu_map(props)["lasers"]
        assert lasers[0]["enable"]["property"] == "OnOff"
        assert lasers[0]["power_pct"]["property"] == "Power"


class TestResolveEmuDevice:
    def test_resolves_allocated_name(self):
        props = emu_manager._parse_properties(FULL, FULL_DEVICES)
        assert emu_manager.resolve_emu_device(props, "Laser 3 enable") == {
            "device": "Luxx638", "property": "Laser Operation Select",
        }

    def test_unallocated_name_raises(self):
        props = emu_manager._parse_properties(FULL, FULL_DEVICES)
        with pytest.raises(KeyError, match="not an allocated"):
            emu_manager.resolve_emu_device(props, "UV pulse duration")


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


# ── parameters and human-facing names (design/39) ───────────────────────────

M5_CONFIG = Path(__file__).parent / "fixtures" / "m5-config.uicfg"
M5_DEVICES = [
    "iChrome-MLE-TCP", "Laser Trigger", "Thorlabs Filter Wheel",
    "Thorlabs Filter Wheel-1", "Thorlabs ELL6", "Thorlabs ELL20",
    "Thorlabs ELL17/ELL20", "PIZStage", "Analog Input", "iBeamSmartCW",
]


@pytest.fixture
def m5_config(tmp_path):
    mm = tmp_path / "MM"
    (mm / "EMU").mkdir(parents=True)
    (mm / "EMU" / "config.uicfg").write_text(M5_CONFIG.read_text())
    return emu_manager.read_emu_config(mm, M5_DEVICES)


@pytest.fixture
def m5_map(m5_config):
    return emu_manager.build_emu_map(
        m5_config["properties"], m5_config["parameters"]
    )


@pytest.fixture
def m5_props_no_devices(tmp_path):
    """The real config parsed with no device list, as tools.py falls back to."""
    mm = tmp_path / "MM"
    (mm / "EMU").mkdir(parents=True)
    (mm / "EMU" / "config.uicfg").write_text(M5_CONFIG.read_text())
    config = emu_manager.read_emu_config(mm, [])
    return config["properties"], config["parameters"]


def test_real_m5_laser_names_come_from_parameters(m5_map):
    assert m5_map["lasers"][3]["name"] == "640"
    assert m5_map["lasers"][1]["name"] == "488"


def test_two_state_names_ignore_none_placeholder(m5_map):
    assert m5_map["other"]["Two-state device 3"]["name"] == "BFP"
    assert "Two-state device 4" not in m5_map["other"]


def test_filter_names_strip_whitespace_and_mark_empty_slots(m5_map):
    slots = m5_map["filter_wheels"][1]["slots"]
    assert slots[2]["name"] == "676/37"
    assert slots[5] == {"name": None, "value": "5", "empty": True}


def test_second_filter_wheel_has_its_own_names(m5_map):
    assert m5_map["filter_wheels"][2]["slots"][0]["name"] == "525/50"


def test_filter_name_count_mismatch_fails_loudly():
    props = emu_manager._parse_properties({
        "Filter wheel position": "Wheel-State",
        "Filter wheel position state 0": "0",
        "Filter wheel position state 1": "1",
    }, ["Wheel"])
    params = emu_manager._parse_parameters({"Filters - Filter names": "DAPI"})
    wheel = emu_manager.build_emu_map(props, params)["filter_wheels"][1]
    assert "slots" not in wheel
    assert wheel["name_mismatch"] == {"names": 1, "states": 2}


def test_resolve_configured_names_preserves_laser_slot_pairing(m5_config):
    props, params = m5_config["properties"], m5_config["parameters"]
    bfp = emu_manager.resolve_emu_device(props, " BfP ", params)
    assert bfp["device"] == "Thorlabs ELL6"
    assert bfp["property"] == "State"
    laser = emu_manager.resolve_emu_device(props, "640", params)
    assert laser["name"] == "640"
    assert laser["trigger_mode"]["property"] == "Mode3"
    focus = emu_manager.resolve_emu_device(props, "Focus stabilization", params)
    assert focus["device"] == "PIZStage"


def test_role_alias_does_not_allocate_its_target(m5_map):
    assert "Two-state device 5" in m5_map["unallocated"]
    assert "Two-state device 5" not in m5_map["other"]


def test_config_without_parameters_has_no_invented_names(tmp_path):
    mm = tmp_path / "MM"
    (mm / "EMU").mkdir(parents=True)
    (mm / "EMU" / "config.uicfg").write_text(json.dumps({
        "defaultConfigurationName": "plain",
        "pluginConfigurations": [{
            "configurationName": "plain", "pluginName": "Simple UI",
            "properties": {"Laser0 on/off": "Laser-OnOff"},
            "settings": {},
        }],
    }))
    config = emu_manager.read_emu_config(mm, ["Laser"])
    emu_map = emu_manager.build_emu_map(
        config["properties"], config["parameters"]
    )
    assert config["parameters"] == {}
    assert all("name" not in laser for laser in emu_map["lasers"].values())


def test_find_jars_searches_emu_case_insensitively(tmp_path):
    emu = tmp_path / "EMU"
    emu.mkdir()
    (emu / "htsmlm-2.1.0.jar").write_text("")
    assert emu_manager._find_jars(tmp_path, "htSMLM") == ["htsmlm-2.1.0.jar"]


def _slot3_props():
    return emu_manager._parse_properties({
        "Laser 3 enable": "L-On", "Laser 3 power percentage": "L-Pct",
        "Laser trigger 3 mode": "T-Mode3", "Laser trigger 3 sequence": "T-Seq3",
    }, ["L", "T"])


@pytest.mark.parametrize("first", ["Laser 3", "Laser trigger 3"])
def test_conflicting_panel_names_leave_the_slot_unnamed(first):
    """Two panels, two labels, one slot: the answer must not be key order.

    Silently keeping whichever panel the JSON happened to list first is the
    same class of defect as the incident this block fixes — an agent asking
    for '640' routed to a slot by dict ordering.
    """
    labels = {"Laser 3": "640", "Laser trigger 3": "405"}
    second = "Laser trigger 3" if first == "Laser 3" else "Laser 3"
    params = {first: {"Name": labels[first]}, second: {"Name": labels[second]}}

    slot = emu_manager.build_emu_map(_slot3_props(), params)["lasers"][3]

    assert "name" not in slot
    assert sorted(slot["name_conflict"]) == ["405", "640"]


def test_a_third_agreeing_panel_does_not_resurrect_a_conflicted_name():
    """Three panels on one slot: 640, 405, 640. It stays unnamed."""
    props = emu_manager._parse_properties({
        "Laser 3 enable": "L-On",
        "Laser trigger 3 mode": "T-Mode3",
        "Laser3 on/off": "A-OnOff",
    }, ["L", "T", "A"])
    params = {
        "Laser 3": {"Name": "640"},
        "Laser trigger 3": {"Name": "405"},
        "Laser3": {"Name": "640"},
    }
    slot = emu_manager.build_emu_map(props, params)["lasers"][3]
    assert "name" not in slot
    assert sorted(slot["name_conflict"]) == ["405", "640"]


def test_name_lookup_refuses_when_the_device_split_failed(m5_props_no_devices):
    """The name path must refuse exactly where the exact-key path refuses.

    tools.py falls back to an empty device list when get_loaded_devices()
    fails, leaving mm_property_string unsplit. Returning a record with no
    'device' would fail in the caller instead of here.
    """
    props, params = m5_props_no_devices
    with pytest.raises(KeyError, match="not an allocated EMU property"):
        emu_manager.resolve_emu_device(props, "Two-state device 3", params)
    with pytest.raises(KeyError, match="could not be split"):
        emu_manager.resolve_emu_device(props, "BFP", params)
