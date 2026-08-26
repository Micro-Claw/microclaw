import json
import subprocess

import pytest

from microclaw import config


@pytest.mark.parametrize(
    ("active", "candidate", "proceed"),
    [
        (active, candidate, active == candidate)
        for active in ("ready", "blocked", "missing")
        for candidate in ("ready", "blocked", "missing")
    ],
)
def test_config_promotion_guard_has_all_nine_cells(active, candidate, proceed):
    comparison = config.compare_config_classifications(active, candidate)
    assert comparison.proceed is proceed
    if candidate == "ready" and active != "ready":
        assert "Repair or re-review the file in setup" in comparison.reason
        assert "this version also classifies it `ready`" in comparison.reason


def test_each_slot_classification_comes_from_its_own_cli(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        classification = "missing" if command[0] == "active.exe" else "blocked"
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"classification": classification}), "",
        )

    monkeypatch.setattr(config.subprocess, "run", run)
    comparison = config.compare_slot_configurations(
        "active.exe", "candidate.exe", tmp_path / "safety.yaml",
    )
    assert not comparison.proceed
    assert [call[0][0] for call in calls] == ["active.exe", "candidate.exe"]
    for command, kwargs in calls:
        assert command[-2:] == ["check-config", "--json"]
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False


def test_slot_classification_refuses_invalid_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        config.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "not json", ""),
    )
    with pytest.raises(RuntimeError, match="invalid config-classification JSON"):
        config.classify_config_with_slot("slot.exe", tmp_path / "safety.yaml")
