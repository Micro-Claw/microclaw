@echo off
rem =====================================================================
rem  Microclaw installer (Windows) -- design/17 v4
rem
rem  Double-click this file. It needs no administrator rights and touches
rem  nothing outside %LOCALAPPDATA%\microclaw.
rem
rem  It installs `uv` (which downloads its own Python), builds an isolated
rem  environment, installs Microclaw into it, puts a shortcut on the
rem  desktop, and opens this machine's safety-limits file for editing.
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
set "MC_ENV=%MC_HOME%\env"
set "MC_PY=%MC_ENV%\Scripts\python.exe"
set "MC_EXE=%MC_ENV%\Scripts\microclaw.exe"

echo.
echo   Microclaw installer
echo   ===================
echo   Installing into %MC_HOME%
echo   No administrator rights needed.
echo.

call :resolve_source || goto :fail
call :find_uv        || goto :fail
call :make_env       || goto :fail
call :install_pkg    || goto :fail
call :finish         || goto :fail

echo.
echo   Done. There is now a Microclaw icon on your desktop.
echo.
echo   IMPORTANT: the safety-limits file that just opened contains EXAMPLE
echo   limits. They match no real microscope. Edit every limit for your
echo   instrument, then change `reviewed: false` to `reviewed: true`.
echo   Microclaw refuses to start until you do.
echo.
echo   Before launching: open Micro-Manager and enable the ZMQ server under
echo   Tools ^> Options ^> "Run pycro-manager server on port 4827".
echo.
pause
exit /b 0


rem ---------------------------------------------------------------------
:resolve_source
if defined MICROCLAW_SRC (
    echo   [1/5] Source: %MICROCLAW_SRC%
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
echo   [1/5] Source: %~dp0
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
    echo   [2/5] Found uv: %UV%
    exit /b 0
)
echo   [2/5] Installing uv...
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
echo   [3/5] Creating an isolated Python environment...
"%UV%" venv --python 3.12 "%MC_ENV%"
if errorlevel 1 exit /b 1
exit /b 0


rem ---------------------------------------------------------------------
:install_pkg
echo   [4/5] Installing Microclaw and its dependencies. This takes a few minutes.
rem pushd so a relative ".[serve]" resolves against the source folder, and so a
rem path containing spaces never reaches the command line unquoted.
if not defined MICROCLAW_SRC pushd "%~dp0"
"%UV%" pip install --python "%MC_PY%" "%MC_SPEC%"
set "MC_RC=%ERRORLEVEL%"
if not defined MICROCLAW_SRC popd
if not "%MC_RC%"=="0" exit /b 1
if not exist "%MC_EXE%" (
    echo   ERROR: microclaw.exe missing after install: %MC_EXE%
    exit /b 1
)
exit /b 0


rem ---------------------------------------------------------------------
:finish
rem Shortcut first, `init` last: init opens an editor, and the file it opens
rem should be the last thing on screen -- editing it is the user's next act.
echo   [5/5] Creating the desktop shortcut...
"%MC_EXE%" install-shortcut
if errorlevel 1 exit /b 1
"%MC_EXE%" init
if errorlevel 1 exit /b 1
exit /b 0


rem ---------------------------------------------------------------------
:fail
echo.
echo   Install failed. Copy the messages above and send them to the maintainer.
echo.
pause
exit /b 1
