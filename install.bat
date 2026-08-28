@echo off
rem =====================================================================
rem  Microclaw installer (Windows) -- design/17 v4
rem
rem  Double-click this file. It needs no administrator rights and touches
rem  nothing outside %LOCALAPPDATA%\microclaw.
rem
rem  It installs `uv` (which downloads its own Python), builds an isolated
rem  environment, installs Microclaw into it, puts a shortcut on the
rem  desktop, verifies the Micro-Manager bridge, and starts restricted setup.
rem
rem  Source of the code, in order:
rem    1. %MICROCLAW_SRC%, if set -- a URL or a pip requirement.
rem    2. the folder this script sits in.
rem  The repo is private today, so a URL install would need a token; the
rem  user downloads the repo ZIP and runs this from inside it. When the
rem  repo goes public, set MICROCLAW_SRC to the archive URL and the same
rem  script becomes a one-file download. Nothing else changes.
rem =====================================================================

setlocal EnableExtensions
title Microclaw installer

set "MC_HOME=%LOCALAPPDATA%\microclaw"
set "MC_SOURCE_DIR=%~dp0."

echo.
echo   Microclaw installer
echo   ===================
echo   Installing into %MC_HOME%
echo   No administrator rights needed.
echo.

call :resolve_source || goto :fail
call :find_uv        || goto :fail
call :migrate_layout || goto :fail
call :make_env       || goto :fail
call :install_pkg    || goto :fail
call :write_managed  || goto :fail
call :finish         || goto :fail

echo.
echo   Installation complete. There is now a Microclaw icon on your desktop.
echo.
rem Setup is launched with write authority only when there is nothing to protect.
rem An existing file that setup must not overwrite gets a genuinely read-only
rem session instead: the writer is never offered, so the refusal arrives before
rem the operator spends a conversation capturing endpoints that cannot be saved.
set "MC_SETUP_ARGS=--setup-write-security-config"
if exist "%APPDATA%\microclaw\safety_config.yaml" (
    "%MC_EXE%" check-config >nul 2>&1
    if errorlevel 1 (
        echo   Existing security bounds are invalid or unreviewed and were preserved:
        echo     %APPDATA%\microclaw\safety_config.yaml
        echo   Setup will open read-only, and it cannot overwrite that file. Move it
        echo   aside or repair it deliberately, then rerun the one-time command below.
        set "MC_SETUP_ARGS="
        goto :bridge_instructions
    )
    echo   Existing reviewed security bounds preserved. Setup is not repeated during an upgrade.
    goto :installed_done
)
:bridge_instructions
echo   Now open Micro-Manager and tick:
echo   Tools ^> Options ^> Run pycro-manager server on port 4827
echo.
echo   Leave Micro-Manager open. This installer will verify the bridge and then
echo   continue into restricted browser setup in this same terminal.
echo.
set "MC_BRIDGE_ATTEMPTS=0"

:check_bridge
set /a MC_BRIDGE_ATTEMPTS+=1
echo   Press any key when Micro-Manager is open and the ZMQ option is ticked.
pause >nul
"%MC_EXE%" check-bridge
if not errorlevel 1 goto :bridge_ready
echo.
echo   The readiness check did not find an answering Micro-Manager ZMQ bridge
echo   on port 4827. Check that Micro-Manager is open and the option is ticked.
if %MC_BRIDGE_ATTEMPTS% LSS 3 goto :check_bridge

echo.
echo   Microclaw is installed, but browser setup was not started after
echo   three readiness checks. When Micro-Manager and its ZMQ bridge are ready,
echo   run:
echo     "%MC_EXE%" --setup-write-security-config serve
goto :installed_done

:bridge_ready
echo.
echo   The Micro-Manager bridge answered.
echo   You need an Anthropic API key from console.anthropic.com. The browser page
echo   will ask for the key and can store it securely on this machine.
echo.
echo   Starting restricted browser setup...
echo.
echo   This window becomes the setup server and keeps running while you use the
echo   browser. When the browser says your security bounds are saved, press
echo   Ctrl+C here to stop it, then start Microclaw from the desktop icon.
echo   If you close it early, rerun this one-time setup command:
echo     "%MC_EXE%" --setup-write-security-config serve
rem MC_SETUP_ARGS is empty when an existing config must be protected, which is
rem what makes the read-only promise printed above true.
"%MC_EXE%" %MC_SETUP_ARGS% serve
if not errorlevel 1 goto :installed_done
echo.
echo   Browser setup did not finish, but Microclaw is installed. Resolve
echo   the message above and retry with:
echo     "%MC_EXE%" --setup-write-security-config serve
echo.
:installed_done
pause
exit /b 0


rem ---------------------------------------------------------------------
:resolve_source
if defined MICROCLAW_SRC (
    echo   [1/7] Source: %MICROCLAW_SRC%
    set "MC_SPEC=microclaw[serve] @ %MICROCLAW_SRC%"
    exit /b 0
)
rem %~dp0 is this script's folder, with a trailing backslash.
if not exist "%~dp0pyproject.toml" (
    echo.
    echo   ERROR: no pyproject.toml next to this script.
    echo   Run install.bat from inside the extracted Microclaw folder,
    echo   or set MICROCLAW_SRC to a URL first.
    exit /b 1
)
echo   [1/7] Source: %~dp0
set "MC_SPEC=.[serve]"
exit /b 0


rem ---------------------------------------------------------------------
:find_uv
rem uv is a single static binary that provisions its own CPython, so this
rem works whether or not Python is already installed on the machine.
set "UV="
for /f "delims=" %%I in ('where uv 2^>nul') do set "UV=%%I"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if defined UV (
    echo   [2/7] Found uv: %UV%
    exit /b 0
)
echo   [2/7] Installing uv...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if errorlevel 1 exit /b 1
set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not exist "%UV%" (
    echo   ERROR: uv did not install where expected: %UV%
    exit /b 1
)
exit /b 0


rem ---------------------------------------------------------------------
:make_env
rem An upgrade and a migration both arrive here with the slot already present,
rem and `uv venv` refuses to reuse an existing environment -- which is how the
rem first migration this installer ever performed failed on the demo machine.
rem The slot IS the known-good environment at that point, so reuse it and let
rem `uv pip install --python` upgrade in place.
rem
rem But an interpreter that EXISTS is not an interpreter that RUNS. A uv venv's
rem python.exe is a trampoline onto a uv-managed CPython elsewhere on the disk;
rem when that base is replaced or pruned, the file is still there and every
rem attempt to spawn it fails with "uv trampoline failed to spawn Python child
rem process". Reusing that environment would carry the fault forward into
rem `uv pip install`. So the test is execution, not existence.
rem
rem --clear is reached only after the interpreter has failed to run, which is
rem the one condition under which nothing is lost by replacing it: an
rem environment that cannot start is not one the user is still running from.
if exist "%MC_PY%" (
    "%MC_PY%" -c "pass" >nul 2>&1
    if not errorlevel 1 (
        echo   [4/7] Reusing the working environment at %MC_ENV%.
        exit /b 0
    )
    echo   [4/7] The environment at %MC_ENV% has a Python that cannot start.
    echo         Rebuilding it. Your settings in %APPDATA%\microclaw are untouched.
    "%UV%" venv --clear --python 3.12 "%MC_ENV%"
    if errorlevel 1 exit /b 1
    exit /b 0
)
if exist "%MC_ENV%" (
    echo   [4/7] Replacing a directory with no Python at %MC_ENV%...
    "%UV%" venv --clear --python 3.12 "%MC_ENV%"
    if errorlevel 1 exit /b 1
    exit /b 0
)
echo   [4/7] Creating an isolated Python environment...
"%UV%" venv --python 3.12 "%MC_ENV%"
if errorlevel 1 exit /b 1
exit /b 0


rem ---------------------------------------------------------------------
:migrate_layout
echo   [3/7] Preparing the managed two-slot layout...
if not exist "%MC_HOME%" mkdir "%MC_HOME%"
if exist "%MC_HOME%\env" if not exist "%MC_HOME%\env-a" (
    echo   Migrating only %MC_HOME%\env to %MC_HOME%\env-a.
    move "%MC_HOME%\env" "%MC_HOME%\env-a" >nul
    if errorlevel 1 exit /b 1
)
if not exist "%MC_HOME%\active-slot.txt" (
    >"%MC_HOME%\.active-slot.txt.tmp" echo a
    move /Y "%MC_HOME%\.active-slot.txt.tmp" "%MC_HOME%\active-slot.txt" >nul
    if errorlevel 1 exit /b 1
)
for /f "usebackq delims=" %%I in ("%MC_HOME%\active-slot.txt") do set "MC_ACTIVE_SLOT=%%I"
if /i not "%MC_ACTIVE_SLOT%"=="a" if /i not "%MC_ACTIVE_SLOT%"=="b" (
    echo   ERROR: active-slot.txt must contain exactly a or b.
    exit /b 1
)
set "MC_ENV=%MC_HOME%\env-%MC_ACTIVE_SLOT%"
set "MC_PY=%MC_ENV%\Scripts\python.exe"
set "MC_EXE=%MC_ENV%\Scripts\microclaw.exe"
echo   Installing into active slot %MC_ACTIVE_SLOT% at %MC_ENV%.
for /f "delims=" %%I in ('where microclaw 2^>nul') do call :report_unmanaged "%%~fI" "PATH"
if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\Scripts\microclaw.exe" call :report_unmanaged "%CONDA_PREFIX%\Scripts\microclaw.exe" "CONDA_PREFIX"
for %%R in (miniforge3 miniconda3 anaconda3) do for /d %%D in ("%USERPROFILE%\%%R\envs\*") do if exist "%%~fD\Scripts\microclaw.exe" call :report_unmanaged "%%~fD\Scripts\microclaw.exe" "common conda roots"
echo   Detection covers PATH, CONDA_PREFIX, and common conda roots; an arbitrary
echo   embedded Python cannot be discovered automatically and remains untouched.
exit /b 0

:report_unmanaged
set "MC_OLD=%~1"
echo %MC_OLD% | findstr /i /b /l /c:"%MC_HOME%\" >nul
if not errorlevel 1 exit /b 0
echo   Existing non-uv Microclaw environment left untouched at:
echo     %MC_OLD%
echo   Detected through %~2.
echo   The desktop icon now moves to the managed installation at %MC_HOME%.
exit /b 0


rem ---------------------------------------------------------------------
:install_pkg
echo   [5/7] Installing Microclaw and its dependencies. This takes a few minutes.
rem pushd so a relative ".[serve]" resolves against the source folder, and so a
rem path containing spaces never reaches the command line unquoted.
if not defined MICROCLAW_SRC pushd "%~dp0"
"%UV%" pip install --python "%MC_PY%" "%MC_SPEC%"
set "MC_RC=%ERRORLEVEL%"
if not defined MICROCLAW_SRC popd
if not "%MC_RC%"=="0" (
    echo   ERROR: the update could not be built.
    rem An index override in the environment is the one cause the messages above
    rem describe by URL but never by name.  A demo-gate phase left
    rem UV_INDEX_URL=https://127.0.0.1:1/unreachable in an operator's shell and
    rem cost three failed installs before anyone connected the two.
    if defined UV_INDEX_URL echo   NOTE: UV_INDEX_URL is set to %UV_INDEX_URL%
    if defined UV_DEFAULT_INDEX echo   NOTE: UV_DEFAULT_INDEX is set to %UV_DEFAULT_INDEX%
    if defined UV_EXTRA_INDEX_URL echo   NOTE: UV_EXTRA_INDEX_URL is set to %UV_EXTRA_INDEX_URL%
    if defined PIP_INDEX_URL echo   NOTE: PIP_INDEX_URL is set to %PIP_INDEX_URL%
    if defined UV_INDEX_URL echo   If that is not deliberate, clear it and run this installer again.
    exit /b 1
)
if not exist "%MC_EXE%" (
    echo   ERROR: microclaw.exe missing after install: %MC_EXE%
    exit /b 1
)
exit /b 0


rem ---------------------------------------------------------------------
:write_managed
echo   [6/7] Writing the external launcher and managed state...
copy /Y "%~dp0scripts\Microclaw.cmd" "%MC_HOME%\Microclaw.cmd" >nul
if errorlevel 1 exit /b 1
copy /Y "%~dp0scripts\updater-launcher.ps1" "%MC_HOME%\updater-launcher.ps1" >nul
if errorlevel 1 exit /b 1
copy /Y "%~dp0scripts\launcher-protocol.txt" "%MC_HOME%\launcher-protocol.txt" >nul
if errorlevel 1 exit /b 1
set "MC_COMMIT=unknown"
if exist "%MC_SOURCE_DIR%\.git" for /f "delims=" %%I in ('git -C "%MC_SOURCE_DIR%" rev-parse HEAD 2^>nul') do set "MC_COMMIT=%%I"
rem retract_pending_slot runs first and deliberately: a slot staged but never
rem restarted would otherwise be activated on the very next launch, switching
rem away from the slot just installed and overwriting the installed_commit
rem written two statements later.  Measured on M5 2026-08-28: active=b with
rem pending=a standing from an update that was staged and never restarted.
"%MC_PY%" -c "import sys; from pathlib import Path; from microclaw.updates import installer_provenance, retract_pending_slot, write_slot_marker, write_state; home=Path(sys.argv[3]); discarded=retract_pending_slot(home); print('  Discarded a staged update that was never restarted: slot '+discarded) if discarded else None; state,note=installer_provenance(sys.argv[1],sys.argv[2]); print('  NOTE: '+note) if note else None; write_state(state,home/'update-state.json'); write_slot_marker(sys.argv[2],int(sys.argv[4]))" "%MC_SOURCE_DIR%" "%MC_COMMIT%" "%MC_HOME%" 1
if errorlevel 1 exit /b 1
exit /b 0


rem ---------------------------------------------------------------------
:finish
echo   [7/7] Creating the desktop shortcut...
"%MC_EXE%" install-shortcut
if errorlevel 1 exit /b 1
exit /b 0


rem ---------------------------------------------------------------------
:fail
echo.
echo   Install failed. Copy the messages above and send them to the maintainer.
echo.
pause
exit /b 1
