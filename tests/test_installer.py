"""install.bat (design/17 v4).

cmd.exe is not available here, so these are structural: they check the installer
cannot silently drift away from the package it installs. The script itself is
verified by running it on Windows.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BAT = ROOT / "install.bat"


@pytest.fixture(scope="module")
def bat() -> str:
    return BAT.read_text(encoding="utf-8")


def test_installer_exists_at_the_repo_root():
    """Step 2 of the README tells the user to double-click it after extracting."""
    assert BAT.is_file()


def test_labels_and_calls_agree(bat):
    """A `call :typo` in batch prints an error and keeps going. Catch it here."""
    labels = set(re.findall(r"^:(\w+)", bat, re.M))
    called = set(re.findall(r"^call :(\w+)", bat, re.M))
    jumped = set(re.findall(r"goto :(\w+)", bat))
    assert called <= labels, f"call to missing label: {called - labels}"
    assert jumped <= labels, f"goto to missing label: {jumped - labels}"
    assert "fail" in labels


def test_every_step_is_guarded(bat):
    """Each `call :step` must branch to :fail, or a failed step is invisible."""
    for line in bat.splitlines():
        if line.startswith("call :"):
            assert "goto :fail" in line, f"unguarded step: {line!r}"


def test_installs_the_serve_extra_that_pyproject_defines(bat):
    """`serve` is what pulls in fastapi/uvicorn; the shortcut launches `serve`."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "\nserve = [" in pyproject
    assert "[serve]" in bat


def test_runs_the_shortcut_subcommand_that_exists(bat):
    """The installer creates the shortcut with the package's real subcommand."""
    main = (ROOT / "microclaw" / "__main__.py").read_text(encoding="utf-8")
    assert '"install-shortcut"' in main
    assert '%MC_EXE%" install-shortcut' in bat


def test_installer_checks_bridge_before_running_setup(bat):
    """Core setup is reachable only after the bounded readiness command succeeds."""
    finish = re.search(r"^:finish$(.*?)(?=^:\w+)", bat, re.M | re.S).group(1)
    assert '%MC_EXE%" init' not in finish
    assert '%MC_EXE%" first-launch-setup' not in finish
    check = '"%MC_EXE%" check-bridge'
    setup = '"%MC_EXE%" init --yes'
    assert bat.count(check) == 1
    assert bat.count(setup) == 1
    assert bat.index(check) < bat.index(setup)
    assert "if not errorlevel 1 goto :bridge_ready" in bat


def test_installer_prints_setup_fallback_after_micro_manager_instruction(bat):
    instruction = 'Run pycro-manager server on port 4827'
    setup_command = 'echo     "%MC_EXE%" init'
    assert instruction in bat
    assert setup_command in bat
    assert bat.index(instruction) < bat.index(setup_command)


def test_installer_retries_bridge_three_times_and_keeps_install_successful(bat):
    assert 'set "MC_BRIDGE_ATTEMPTS=0"' in bat
    assert "set /a MC_BRIDGE_ATTEMPTS+=1" in bat
    assert "if %MC_BRIDGE_ATTEMPTS% LSS 3 goto :check_bridge" in bat
    exhausted = bat.index("three readiness checks")
    assert "goto :fail" not in bat[exhausted:bat.index(":bridge_ready")]
    assert "Installation complete" in bat


def test_upgrade_preserves_existing_profile_and_skips_first_launch(bat):
    assert 'if exist "%APPDATA%\\microclaw\\safety_config.yaml" (' in bat
    assert "Existing safety profile preserved" in bat
    assert "goto :installed_done" in bat


def test_installer_never_asks_for_admin(bat):
    """Teaching a novice to elevate a downloaded .bat is a habit worth not teaching."""
    lowered = bat.lower()
    for red_flag in ("runas", "shellexecute", "-verb runas"):
        assert red_flag not in lowered


def test_installer_stays_inside_localappdata(bat):
    assert 'set "MC_HOME=%LOCALAPPDATA%\\microclaw"' in bat


def test_source_falls_back_to_the_scripts_own_folder(bat):
    """The repo is private, so a URL install would need a token. %~dp0 does not."""
    assert "%~dp0pyproject.toml" in bat
    assert "MICROCLAW_SRC" in bat, "the public-repo switch must stay available"


def test_batch_files_are_forced_to_crlf():
    """cmd.exe can mis-parse a label read with a bare LF."""
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.bat text eol=crlf" in attrs
    assert "tests/fixtures/hooks/m5_legacy/*.py binary" in attrs


@pytest.mark.skipif(sys.platform != "win32", reason="only meaningful in a Windows checkout")
def test_working_tree_copy_is_crlf():
    assert b"\r\n" in BAT.read_bytes()
