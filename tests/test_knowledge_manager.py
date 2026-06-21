import pytest
import yaml
from microclaw.knowledge_manager import (
    load_knowledge,
    save_entry,
    delete_entry,
    format_for_prompt,
    KNOWLEDGE_PATH,
)


@pytest.fixture(autouse=True)
def isolated_kb(tmp_path, monkeypatch):
    monkeypatch.setattr("microclaw.knowledge_manager.KNOWLEDGE_PATH", tmp_path / "knowledge.yaml")


def test_load_knowledge_missing_file():
    assert load_knowledge() == {}


def test_save_and_load_entry():
    save_entry("devices", "Thorlabs-ELL-9", {"role": "cylindrical_lens", "description": "3D SMLM astigmatism lens"})
    data = load_knowledge()
    assert data["devices"]["Thorlabs-ELL-9"]["role"] == "cylindrical_lens"


def test_save_multiple_categories():
    save_entry("samples", "HeLa_actin", {"description": "HeLa GFP-actin", "typical_exposure_ms": 100})
    save_entry("devices", "FilterWheel-ND", {"description": "ND filter wheel"})
    save_entry("strategies", "survey_20x", {"z_range_um": 20, "hook_strategy": "autofocus_per_position"})
    data = load_knowledge()
    assert "samples" in data
    assert "devices" in data
    assert "strategies" in data


def test_save_entry_upserts():
    save_entry("devices", "MyDevice", {"description": "first"})
    save_entry("devices", "MyDevice", {"description": "updated"})
    data = load_knowledge()
    assert data["devices"]["MyDevice"]["description"] == "updated"
    assert len(data["devices"]) == 1


def test_save_entry_invalid_category():
    with pytest.raises(ValueError, match="Unknown category"):
        save_entry("nonsense", "key", {"description": "x"})


def test_delete_entry_existing():
    save_entry("samples", "MyCell", {"description": "test"})
    found = delete_entry("samples", "MyCell")
    assert found is True
    assert load_knowledge() == {}


def test_delete_entry_missing_key():
    save_entry("samples", "MyCell", {"description": "test"})
    found = delete_entry("samples", "OtherCell")
    assert found is False
    assert "MyCell" in load_knowledge()["samples"]


def test_delete_entry_missing_category():
    found = delete_entry("devices", "SomeDevice")
    assert found is False


def test_delete_removes_empty_category():
    save_entry("strategies", "my_strategy", {"description": "test"})
    delete_entry("strategies", "my_strategy")
    data = load_knowledge()
    assert "strategies" not in data


def test_format_for_prompt_empty():
    assert format_for_prompt({}) is None


def test_format_for_prompt_with_data():
    save_entry("devices", "Thorlabs-ELL-9", {"description": "cylindrical lens"})
    text = format_for_prompt(load_knowledge())
    assert text is not None
    assert "User knowledge base" in text
    assert "Thorlabs-ELL-9" in text


def test_format_for_prompt_excludes_empty_categories():
    save_entry("devices", "MyDevice", {"description": "test"})
    text = format_for_prompt(load_knowledge())
    assert "samples" not in text
    assert "strategies" not in text
    assert "devices" in text


def test_knowledge_file_is_valid_yaml(tmp_path, monkeypatch):
    kb_path = tmp_path / "knowledge.yaml"
    monkeypatch.setattr("microclaw.knowledge_manager.KNOWLEDGE_PATH", kb_path)
    save_entry("samples", "U2OS", {"description": "U2OS cells", "exposure_ms": 50})
    parsed = yaml.safe_load(kb_path.read_text())
    assert parsed["samples"]["U2OS"]["exposure_ms"] == 50
