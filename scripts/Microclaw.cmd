@echo off
set MICROCLAW_FROM_SHORTCUT=1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0updater-launcher.ps1"
exit /b %ERRORLEVEL%
