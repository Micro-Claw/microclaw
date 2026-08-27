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
CMD = ROOT / "scripts" / "Microclaw.cmd"
PS1 = ROOT / "scripts" / "updater-launcher.ps1"


@pytest.fixture(scope="module")
def bat() -> str:
    return BAT.read_text(encoding="utf-8")


def test_installer_exists_at_the_repo_root():
    """Step 2 of the README tells the user to double-click it after extracting."""
    assert BAT.is_file()


def test_external_launchers_are_installer_owned_only(bat):
    assert CMD.is_file() and PS1.is_file()
    assert 'scripts\\Microclaw.cmd" "%MC_HOME%\\Microclaw.cmd' in bat
    assert 'scripts\\updater-launcher.ps1" "%MC_HOME%\\updater-launcher.ps1' in bat
    for source in (ROOT / "microclaw").rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert "updater-launcher.ps1" not in text
        assert not re.search(r"Microclaw\.cmd.*(?:write_text|open\()", text)


def test_managed_cmd_is_slot_independent_and_bypasses_execution_policy():
    text = CMD.read_text(encoding="utf-8")
    assert "MICROCLAW_FROM_SHORTCUT=1" in text
    assert "-NoProfile -ExecutionPolicy Bypass" in text
    assert "updater-launcher.ps1" in text
    assert "env-a" not in text and "env-b" not in text


def test_powershell_launcher_has_activation_health_and_rollback_branches():
    text = PS1.read_text(encoding="utf-8")
    for mechanism in ("active-slot.txt", "activate_pending", "fresh_launch",
                      "wait_for_launcher_health", "rollback_slot", "consume_rollback_report",
                      "if (-not $healthy)", "$child.WaitForExit()"):
        assert mechanism in text
    assert "ConvertFrom-Json" not in text
    assert "[Guid]::NewGuid()" not in text
    assert "Set-Content -LiteralPath $activePath" not in text
    assert "Remove-Item -LiteralPath $healthPath" not in text
    assert "Start-Sleep" not in text


def test_installer_migrates_only_localappdata_env_and_never_uses_editable_install(bat):
    assert 'move "%MC_HOME%\\env" "%MC_HOME%\\env-a"' in bat
    assert 'set "MC_ENV=%MC_HOME%\\env-%MC_ACTIVE_SLOT%"' in bat
    assert 'set "MC_ENV=%CONDA_PREFIX%' not in bat
    assert 'pip install --python "%CONDA_PREFIX%' not in bat
    assert " pip install -e " not in bat
    assert "Existing non-uv Microclaw environment left untouched" in bat
    assert "The desktop icon now moves" in bat


def test_installer_uses_package_provenance_and_slot_marker_contracts(bat):
    assert "installer_provenance, write_slot_marker, write_state" in bat
    assert "updates will follow public head" in (
        ROOT / "microclaw" / "updates.py"
    ).read_text(encoding="utf-8")
    assert "ConvertTo-Json" not in bat
    assert 'set "MC_SOURCE_DIR=%~dp0."' in bat
    assert 'git -C "%MC_SOURCE_DIR%" rev-parse HEAD' in bat


def test_installer_declares_and_copies_launcher_protocol(bat):
    declaration = ROOT / "scripts" / "launcher-protocol.txt"
    assert declaration.read_text(encoding="ascii").strip() == "1"
    assert 'scripts\\launcher-protocol.txt" "%MC_HOME%\\launcher-protocol.txt' in bat


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
    retired = "%MC_EXE%\" " + "first-launch" + "-setup"
    assert retired not in finish
    check = '"%MC_EXE%" check-bridge'
    setup = '"%MC_EXE%" %MC_SETUP_ARGS% serve'
    assert bat.count(check) == 1
    launches = re.findall(r'^"%MC_EXE%" %MC_SETUP_ARGS% serve$', bat, re.M)
    assert len(launches) == 1
    assert bat.index(check) < bat.index(setup, bat.index("Starting restricted browser setup"))
    assert "if not errorlevel 1 goto :bridge_ready" in bat


def test_write_authority_is_withheld_when_a_config_must_be_protected(bat):
    """The single launch carries write authority only when there is nothing to
    overwrite. Pinning the literal flagged command instead would pass just as
    well for an installer that promises a read-only session and then hands the
    writer to it anyway — which is what this branch used to do."""
    assert 'set "MC_SETUP_ARGS=--setup-write-security-config"' in bat
    # The clearing assignment lives in the invalid-config branch, between the
    # message naming the preserved file and the jump to the bridge steps.
    invalid = bat[bat.index("Existing security bounds are invalid"):]
    invalid = invalid[:invalid.index("goto :bridge_instructions")]
    assert 'set "MC_SETUP_ARGS="' in invalid
    assert "read-only" in invalid


def test_installer_prints_setup_fallback_after_micro_manager_instruction(bat):
    instruction = 'Run pycro-manager server on port 4827'
    setup_command = 'echo     "%MC_EXE%" --setup-write-security-config serve'
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


def test_upgrade_preserves_existing_profile_and_skips_setup(bat):
    assert 'if exist "%APPDATA%\\microclaw\\safety_config.yaml" (' in bat
    assert '"%MC_EXE%" check-config >nul 2>&1' in bat
    assert "Existing security bounds are invalid or unreviewed and were preserved" in bat
    assert "%APPDATA%\\microclaw\\safety_config.yaml" in bat
    assert "cannot overwrite that file" in bat
    assert "Existing reviewed security bounds preserved" in bat
    assert "goto :installed_done" in bat


def test_installer_relies_on_bare_check_config_failure_to_protect_bounds(bat):
    protected = re.search(
        r'if exist "%APPDATA%\\microclaw\\safety_config\.yaml" \((.*?)\n\)',
        bat, re.S,
    ).group(1)
    assert '"%MC_EXE%" check-config >nul 2>&1' in protected
    assert "check-config --json" not in protected
    check = protected.index('"%MC_EXE%" check-config >nul 2>&1')
    refusal = protected.index("if errorlevel 1", check)
    preserve = protected.index("Existing security bounds are invalid or unreviewed", refusal)
    assert check < refusal < preserve


def test_setup_authority_is_one_time_and_never_in_the_shortcut(bat):
    assert '"%MC_EXE%" --setup-write-security-config serve' in bat
    shortcut_call = re.search(r"^:finish$(.*?)(?=^:\w+)", bat, re.M | re.S).group(1)
    assert "--setup-write-security-config" not in shortcut_call
    shortcut = (ROOT / "microclaw" / "shortcut.py").read_text(encoding="utf-8")
    assert '"serve"' in shortcut
    assert "--setup-write-security-config" not in shortcut


def test_installer_prepares_the_user_for_the_browser_key_gate(bat):
    key_notice = bat.index("console.anthropic.com")
    launch = bat.index('"%MC_EXE%" --setup-write-security-config serve', key_notice)
    assert key_notice < launch
    assert "browser page" in bat
    assert "will ask for the key" in bat


def test_retired_terminal_setup_is_absent(bat):
    assert "init --yes" not in bat
    assert "first-launch" + "-setup" not in bat
    assert "review_steps" not in bat


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


def test_installer_says_how_to_stop_the_setup_server(bat):
    """It launches serve in the foreground, so the window keeps running after
    the browser is done with it (48e acceptance run, 2026-08-14)."""
    launch = bat.index("Starting restricted browser setup")
    guidance = bat[launch - 400:launch + 400].lower()
    assert "ctrl+c" in guidance
    assert "desktop icon" in guidance
