import json
import subprocess
from pathlib import Path

import pytest

from microclaw import config


def test_missing_classification_does_not_restat_after_validation(monkeypatch, tmp_path):
    calls = 0

    def exists(path):
        nonlocal calls
        calls += 1
        return False if calls == 1 else True

    monkeypatch.setattr(Path, "exists", exists)
    result = config.validate_safety_config(tmp_path / "appears-after-check.yaml")
    assert result.classification == "missing"
    assert calls == 1


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


def test_slot_classification_never_inherits_the_callers_stdin(tmp_path, monkeypatch):
    """The hang that stopped block 58e's demo gate twice.

    `capture_output` redirects stdout and stderr only, so without an explicit
    `stdin` the slot CLI keeps the caller's console -- and the desktop launcher
    exports MICROCLAW_FROM_SHORTCUT=1 into the server, which makes that child
    register the "Press Enter to close this window..." handler and block after
    printing its JSON.  Measured on the demo machine: 0.66s with stdin closed,
    45s+ (timeout) with an inherited console, on both slots.

    The subject here is *how* the subprocess is invoked, which no in-process
    fixture can observe -- reproducing it needs a real console -- so this
    asserts the argument rather than the behaviour, deliberately.
    """
    seen = {}

    def record(command, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(command, 0, '{"classification": "ready"}', "")

    monkeypatch.setattr(config.subprocess, "run", record)
    assert config.classify_config_with_slot("slot.exe", tmp_path / "safety.yaml") == "ready"
    assert seen["stdin"] is subprocess.DEVNULL
