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
)
from microclaw.safety import ParsedSafetyConfig, SafetyConfigError, SafetyConstraints

REAL = """
schema_version: 2
reviewed: true
rig_profile: {mode: guaranteed, categorical_properties: [], excluded_properties: []}
stage: {x_min: -100.0, x_max: 100.0, y_min: -100.0, y_max: 100.0}
camera: {max_exposure_ms: 500.0}
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
