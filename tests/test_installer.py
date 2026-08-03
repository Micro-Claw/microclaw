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


def test_runs_the_subcommands_that_exist(bat):
    """install-shortcut and init are both real subcommands, spelled this way."""
    main = (ROOT / "microclaw" / "__main__.py").read_text(encoding="utf-8")
    for cmd in ("install-shortcut", "init"):
        assert f'"{cmd}"' in main, f"{cmd} is not a subcommand any more"
        assert f'%MC_EXE%" {cmd}' in bat, f"install.bat no longer runs {cmd}"


def test_shortcut_is_created_before_init_offers_first_launch_setup(bat):
    """init's setup offer should be the last interactive installer step."""
    assert bat.index("install-shortcut") < bat.index('%MC_EXE%" init')


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
