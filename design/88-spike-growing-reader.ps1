# Runs design/88-spike-growing-reader.py with the installed slot's interpreter.
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'microclaw'
$active = (Get-Content -LiteralPath (Join-Path $root 'active-slot.txt') -Raw).Trim()
$python = Join-Path $root "env-$active\Scripts\python.exe"
$out = Join-Path $env:LOCALAPPDATA ('spike88b-reader-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$ErrorActionPreference = 'Continue'
& $python -I (Join-Path $PSScriptRoot '88-spike-growing-reader.py') --out $out
Write-Host "exit $LASTEXITCODE; report: $out\spike-88b.json"
