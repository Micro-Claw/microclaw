param([string]$Out = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) ('block71a-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))))
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'microclaw'
$active = (Get-Content -LiteralPath (Join-Path $root 'active-slot.txt') -Raw).Trim()
if ($active -notin @('a', 'b')) { throw 'active-slot.txt must name a or b.' }
$python = Join-Path $root "env-$active\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Active interpreter missing: $python" }
# The native child owns gate.log; Start-Transcript cannot capture its stdout.
& $python (Join-Path $PSScriptRoot '71-block71a-demo-gate.py') --out $Out
$code = $LASTEXITCODE
Write-Host "Evidence: $Out"
exit $code
