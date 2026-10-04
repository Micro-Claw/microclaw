param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('publish','prepare','session','verify','cleanup','selftest')]
    [string]$Phase,
    [string]$Out
)
$ErrorActionPreference = 'Stop'
# Use the installed slot, exactly as the 83f-3 demo launcher does.
$root = Join-Path $env:LOCALAPPDATA 'microclaw'
$active = (Get-Content -LiteralPath (Join-Path $root 'active-slot.txt') -Raw).Trim()
if ($active -notin @('a','b')) { throw 'active-slot.txt must name a or b.' }
$python = Join-Path $root "env-$active\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Gate interpreter missing: $python. Run step 0 (install.bat)." }
$gateArgs = @($Phase)
if ($Out) { $gateArgs += @('--out', $Out) }
Write-Host "Gate interpreter: $python"
$ErrorActionPreference = 'Continue'
& $python -I (Join-Path $PSScriptRoot '83-block83f5-gate.py') @gateArgs
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
$pointer = Join-Path $root '83f5-gate-evidence.txt'
if ($Phase -ne 'selftest' -and (Test-Path -LiteralPath $pointer)) { Write-Host ('Evidence: ' + (Get-Content -LiteralPath $pointer -Raw -Encoding UTF8).Trim()) }
exit $code
