"""The `reviewed:` gate, and the per-user paths behind it (design/17 v2).

After v3 a desktop shortcut launches `microclaw serve` with no arguments, so this
gate is the last thing between a double-click and a stage moving under the
example config's fictional limits. Everything here is about failing closed.
"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from microclaw import config, paths
from microclaw.config import (
    UnreviewedSafetyConfig,
    load_safety_config,
    load_safety_config_or_exit,
    validate_safety_config,
)
from microclaw.safety import ParsedSafetyConfig, SafetyConfigError, SafetyConstraints

REAL = """
schema_version: 2
reviewed: true
property_authorization: {mode: guaranteed, allowed_categorical: [], denied: []}
stage: {x_min: -100.0, x_max: 100.0, y_min: -100.0, y_max: 100.0}
camera: {max_exposure_ms: 500.0}
acquisition:
  max_frames: 10000
  max_duration_s: 3600
  max_bytes: 50000000000
  max_illuminated_ms: 600000
  max_session_illuminated_ms: 1800000
  confirm_above_frames: 500
  confirm_above_duration_s: 300
  confirm_above_bytes: 5000000000
  confirm_above_illuminated_ms: 60000
"""


def _write(tmp_path, text) -> Path:
    p = tmp_path / "safety_config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


# ---- the gate ----

def test_reviewed_true_loads(tmp_path):
    c = load_safety_config(_write(tmp_path, REAL))
    assert isinstance(c, ParsedSafetyConfig)
    assert c.constraints.stage.x_max == 100.0


def test_old_authorization_key_is_refused_by_strict_schema(tmp_path):
    legacy = REAL.replace(
        "property_authorization: {mode: guaranteed, allowed_categorical: [], denied: []}",
        "rig_profile: {mode: guaranteed, categorical_properties: [], typed_actuators: [], excluded_properties: []}",
    )
    result = validate_safety_config(_write(tmp_path, legacy))
    assert result.parsed is None
    assert not result.can_start_live_validation
    assert [item.kind for item in result.diagnostics] == ["schema"]
    message = result.diagnostics[0].message
    assert "rig_profile: unknown top-level key" in message
    assert "property_authorization: missing required property authorization map" in message


def test_missing_acquisition_section_names_file_and_required_fields(tmp_path):
    p = _write(tmp_path, REAL[:REAL.index("acquisition:")])
    with pytest.raises(SafetyConfigError) as exc:
        ParsedSafetyConfig.from_yaml(str(p))
    message = str(exc.value)
    assert str(p) in message
    for key in (
        "max_frames", "max_duration_s", "max_bytes", "max_illuminated_ms",
        "confirm_above_frames", "confirm_above_duration_s",
        "confirm_above_illuminated_ms",
    ):
        assert key in message


def test_partial_acquisition_section_aggregates_every_missing_key(tmp_path):
    text = REAL[:REAL.index("acquisition:")] + "acquisition: {max_frames: 1}\n"
    with pytest.raises(SafetyConfigError) as exc:
        ParsedSafetyConfig.from_yaml(str(_write(tmp_path, text)))
    message = str(exc.value)
    assert message.count("missing required key") == 6


def test_deprecated_optional_acquisition_keys_still_load(tmp_path):
    text = REAL.replace("  max_session_illuminated_ms: 1800000\n", "")
    text = text.replace("  confirm_above_bytes: 5000000000\n", "")
    parsed = ParsedSafetyConfig.from_yaml(str(_write(tmp_path, text)))
    assert parsed.constraints.acquisition.max_session_illuminated_ms is None
    assert parsed.constraints.acquisition.confirm_above_bytes is None

    parsed = ParsedSafetyConfig.from_yaml(str(_write(tmp_path, REAL)))
    assert parsed.constraints.acquisition.max_session_illuminated_ms == 1_800_000
    assert parsed.constraints.acquisition.confirm_above_bytes == 5_000_000_000


@pytest.mark.parametrize("bad", ["nope", "true", ".nan", ".inf", "0", "-1"])
def test_acquisition_values_must_be_finite_positive_numbers(tmp_path, bad):
    text = REAL.replace("max_frames: 10000", f"max_frames: {bad}")
    with pytest.raises(SafetyConfigError, match="acquisition.max_frames"):
        ParsedSafetyConfig.from_yaml(str(_write(tmp_path, text)))


def test_acquisition_unknown_key_and_value_errors_aggregate(tmp_path):
    text = REAL.replace(
        "  max_frames: 10000",
        "  max_frames: false\n  surprise_budget: 1",
    )
    with pytest.raises(SafetyConfigError) as exc:
        ParsedSafetyConfig.from_yaml(str(_write(tmp_path, text)))
    message = str(exc.value)
    assert "acquisition.surprise_budget: unknown key" in message
    assert "acquisition.max_frames" in message


def test_direct_constraints_cannot_enter_validated_loader_path():
    with pytest.raises(TypeError):
        load_safety_config(SafetyConstraints())


def test_cli_and_web_build_guard_from_retained_parsed_contract(monkeypatch):
    from microclaw import __main__ as cli
    pytest.importorskip("fastapi")
    from microclaw import webserve

    parsed = ParsedSafetyConfig(SafetyConstraints(), {})
    cli_seen = []
    web_seen = []

    class Disconnected:
        def __init__(self, port, guard):
            (cli_seen if not cli_seen else web_seen).append(guard._c)

        def is_connected(self):
            return False

    monkeypatch.setattr(cli, "load_safety_config_or_exit", lambda path: parsed)
    monkeypatch.setattr(cli, "MicroscopeController", Disconnected)
    with pytest.raises(SystemExit, match="Could not connect"):
        cli.run_session(SimpleNamespace(safety_config=None, port=1))

    monkeypatch.setattr(webserve, "load_safety_config_or_exit", lambda path: parsed)
    monkeypatch.setattr(webserve, "MicroscopeController", Disconnected)
    with pytest.raises(SystemExit, match="Could not connect"):
        webserve.Session(SimpleNamespace(safety_config=None, port=1))
    assert cli_seen == [parsed.constraints]
    assert web_seen == [parsed.constraints]


def test_reviewed_false_refuses(tmp_path):
    with pytest.raises(UnreviewedSafetyConfig):
        load_safety_config(_write(tmp_path, REAL.replace("true", "false")))


def test_missing_reviewed_key_refuses(tmp_path):
    """Every config predating this gate lands here. Refuse, don't assume."""
    with pytest.raises(SafetyConfigError, match="schema_version"):
        load_safety_config(_write(tmp_path, "stage: {x_min: -1.0, x_max: 1.0}\n"))


def test_empty_file_refuses(tmp_path):
    with pytest.raises(SafetyConfigError, match="schema_version"):
        load_safety_config(_write(tmp_path, ""))


@pytest.mark.parametrize("truthy", ["yes", "1", "'true'", "True"])
def test_only_a_real_yaml_true_passes(tmp_path, truthy):
    """`reviewed: 1` is truthy in Python but is not a human saying yes.

    YAML resolves `yes`/`True` to booleans and `'true'`/`1` to a str/int; the gate
    tests `is True`, so anything the user fumbled reads as unreviewed. `True` does
    pass — it is the same YAML boolean — which is fine.
    """
    p = _write(tmp_path, REAL.replace("reviewed: true", f"reviewed: {truthy}"))
    parsed = yaml.safe_load(p.read_text())["reviewed"]
    if parsed is True:
        load_safety_config(p)          # `yes` and `True` are YAML true
    else:
        with pytest.raises((UnreviewedSafetyConfig, SafetyConfigError)):
            load_safety_config(p)


def test_missing_file_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_safety_config(tmp_path / "nope.yaml")


def test_gate_applies_to_an_explicit_path_too(tmp_path):
    """"The file I typed" and "the file the icon loaded" obey the same rule."""
    with pytest.raises(UnreviewedSafetyConfig):
        load_safety_config(str(_write(tmp_path, REAL.replace("true", "false"))))


def test_none_means_the_per_user_default(tmp_path, monkeypatch):
    p = _write(tmp_path, REAL)
    monkeypatch.setattr(config, "default_safety_config", lambda: p)
    assert load_safety_config(None).constraints.stage.x_max == 100.0


# ---- the messages a novice reads in a console about to close ----

def test_exit_on_missing_file_names_init(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "default_safety_config", lambda: tmp_path / "nope.yaml")
    with pytest.raises(SystemExit) as e:
        load_safety_config_or_exit(None)
    assert "microclaw init" in str(e.value)


def test_exit_on_unreviewed_says_what_to_change(tmp_path):
    p = _write(tmp_path, REAL.replace("true", "false"))
    with pytest.raises(SystemExit) as e:
        load_safety_config_or_exit(p)
    msg = str(e.value)
    assert "reviewed: true" in msg and str(p) in msg


def test_exit_on_malformed_yaml(tmp_path):
    with pytest.raises(SystemExit, match="Could not parse"):
        load_safety_config_or_exit(_write(tmp_path, "reviewed: true\nstage: [unclosed\n"))


def test_exit_on_invalid_schema_names_file_and_all_errors(tmp_path):
    p = _write(tmp_path, "reviewed: true\nstagee: {}\nstage: {x_mim: 0}\n")
    with pytest.raises(SystemExit) as exc:
        load_safety_config_or_exit(p)
    message = str(exc.value)
    assert str(p) in message
    assert "stagee" in message and "stage.x_mim" in message


# ---- offline diagnostics ----

def test_offline_validator_returns_parsed_config_for_phase5_reuse(tmp_path):
    result = validate_safety_config(_write(tmp_path, REAL))
    assert result.parsed is not None
    assert result.reviewed is True
    assert result.can_start_live_validation
    assert [item.kind for item in result.diagnostics] == ["live_check"]
    assert "cannot enumerate the rig" in result.diagnostics[0].message


def test_offline_validator_reports_unreviewed_as_expected_next_action(tmp_path):
    result = validate_safety_config(_write(tmp_path, REAL.replace("true", "false")))
    assert result.parsed is not None
    assert result.reviewed is False
    assert not result.can_start_live_validation
    review = next(item for item in result.diagnostics if item.kind == "review")
    assert "review every limit" in review.message.lower()
    assert "`reviewed: true`" in review.message


def test_offline_validator_does_not_call_missing_reviewed_intentional(tmp_path):
    result = validate_safety_config(_write(tmp_path, REAL.replace("reviewed: true\n", "")))
    assert result.reviewed is None
    assert [item.kind for item in result.diagnostics] == ["schema"]
    assert "missing required key" in result.diagnostics[0].message
    assert "intentionally unreviewed" not in result.diagnostics[0].message


def test_offline_validator_reports_review_and_all_schema_problems_together(tmp_path):
    text = REAL.replace("reviewed: true", "reviewed: false").replace(
        "stage: {x_min: -100.0, x_max: 100.0, y_min: -100.0, y_max: 100.0}",
        "stage: {x_mim: bad}\nstagee: {}",
    )
    result = validate_safety_config(_write(tmp_path, text))
    assert result.reviewed is False
    assert result.parsed is None
    assert [item.kind for item in result.diagnostics] == ["review", "schema"]
    message = result.diagnostics[1].message
    assert "stage.x_mim" in message and "stagee" in message


def test_offline_clean_nulls_are_reported_as_live_startup_blockers(tmp_path):
    text = REAL.replace("max_exposure_ms: 500.0", "max_exposure_ms: null")
    for line in (
        "max_frames: 10000", "max_duration_s: 3600", "max_bytes: 50000000000",
        "max_illuminated_ms: 600000", "max_session_illuminated_ms: 1800000",
        "confirm_above_frames: 500", "confirm_above_duration_s: 300",
        "confirm_above_bytes: 5000000000", "confirm_above_illuminated_ms: 60000",
    ):
        text = text.replace(line, line.split(":", 1)[0] + ": null")
    p = _write(tmp_path, text)
    assert ParsedSafetyConfig.from_yaml(str(p))  # strict schema accepts this split
    result = validate_safety_config(p)
    blockers = [item for item in result.diagnostics if item.blocking]
    assert len(blockers) == 8
    assert any("camera.max_exposure_ms" in item.message for item in blockers)
    for field in (
        "max_frames", "max_duration_s", "max_bytes", "max_illuminated_ms",
        "confirm_above_frames", "confirm_above_duration_s",
        "confirm_above_illuminated_ms",
    ):
        assert any(f"acquisition.{field}" in item.message for item in blockers)


def test_degraded_null_caps_are_nonblocking_but_never_silent(tmp_path):
    text = REAL.replace("mode: guaranteed", "mode: degraded_trusted_plugins")
    text = text.replace("max_exposure_ms: 500.0", "max_exposure_ms: null")
    fields = (
        "max_frames", "max_duration_s", "max_bytes", "max_illuminated_ms",
        "max_session_illuminated_ms", "confirm_above_frames",
        "confirm_above_duration_s", "confirm_above_bytes",
        "confirm_above_illuminated_ms",
    )
    for field in fields:
        text = text.replace(f"{field}: " + {
            "max_frames": "10000", "max_duration_s": "3600",
            "max_bytes": "50000000000", "max_illuminated_ms": "600000",
            "max_session_illuminated_ms": "1800000",
            "confirm_above_frames": "500", "confirm_above_duration_s": "300",
            "confirm_above_bytes": "5000000000",
            "confirm_above_illuminated_ms": "60000",
        }[field], f"{field}: null")
    result = validate_safety_config(_write(tmp_path, text))
    assert result.can_start_live_validation
    assert [item.kind for item in result.diagnostics] == [
        "degraded_mode", "live_check",
    ]
    warning, live_check = result.diagnostics
    assert not warning.blocking
    assert "runtime checks do not enforce" in warning.message
    assert "camera.max_exposure_ms" in warning.message
    required_fields = set(fields) - {
        "max_session_illuminated_ms", "confirm_above_bytes",
    }
    assert all(f"acquisition.{field}" in warning.message for field in required_fields)
    assert "acquisition.max_session_illuminated_ms" not in warning.message
    assert "acquisition.confirm_above_bytes" not in warning.message
    assert "completeness claim" in live_check.message
    assert "explicitly suspended" in live_check.message


def test_check_config_cli_is_thin_offline_presenter(tmp_path, monkeypatch, capsys):
    from microclaw import __main__ as cli

    monkeypatch.setattr(
        cli, "MicroscopeController",
        lambda *a, **k: pytest.fail("offline validator connected to Micro-Manager"),
    )
    cli.check_config(SimpleNamespace(path=_write(tmp_path, REAL), safety_config=None))
    output = capsys.readouterr().out
    assert "Schema: valid" in output
    assert "LIVE CHECK REQUIRED" in output
    assert "Offline checks passed" in output


# ---- paths ----

@pytest.mark.skipif(os.name == "nt", reason="POSIX conventions")
def test_posix_dirs_follow_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert paths.user_config_dir() == tmp_path / "cfg" / "microclaw"
    assert paths.user_data_dir() == tmp_path / "data" / "microclaw"
    assert paths.default_safety_config().name == "safety_config.yaml"


def test_credentials_and_safety_config_share_a_directory():
    """One module owns the convention, so the two cannot drift apart."""
    from microclaw import credentials

    assert credentials.config_path().parent == paths.user_config_dir()
    assert config.default_safety_config().parent == paths.user_config_dir()


def test_data_dir_is_not_the_config_dir():
    """The shortcut's target must be machine-local; roaming config may be a share."""
    assert paths.user_data_dir() != paths.user_config_dir()
