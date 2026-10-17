param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('measure','session','script','verify','selftest')]
    [string]$Phase,
    [string]$Out
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'microclaw'
$pointer = Join-Path $root '88a-gate-evidence.txt'
if (-not $Out) {
    if ($Phase -in @('measure','selftest')) {
        $Out = Join-Path $env:LOCALAPPDATA ('block88a-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    } elseif (Test-Path -LiteralPath $pointer) {
        $Out = (Get-Content -LiteralPath $pointer -Raw -Encoding UTF8).Trim()
    } else { throw 'No evidence folder recorded. Run -Phase measure first, or pass -Out.' }
}
$active = (Get-Content -LiteralPath (Join-Path $root 'active-slot.txt') -Raw).Trim()
if ($active -notin @('a','b')) { throw 'active-slot.txt must name a or b.' }
$python = Join-Path $root "env-$active\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Gate interpreter missing: $python" }
# The installed slot, not this checkout, must carry block 88a's run_mda.
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $python -I -c "from microclaw import tools; from microclaw.tools_schema import TOOLS; assert hasattr(tools, '_supervise_mda'); assert 'settings' in next(t for t in TOOLS if t['name'] == 'run_mda')['input_schema']['properties']" 2>&1 | Out-Null
$probe = $LASTEXITCODE
$ErrorActionPreference = $previous
if ($probe -ne 0) {
    Write-Host "NOT EXERCISED: $python lacks block 88a. Run step 0 (install.bat on this branch)." -ForegroundColor Red
    exit 2
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null
if ($Phase -eq 'measure') { Set-Content -LiteralPath $pointer -Value $Out -Encoding UTF8 }
Write-Host "Gate interpreter: $python"
$ErrorActionPreference = 'Continue'
$gateArgs = @((Join-Path $PSScriptRoot '88-block88a-demo-gate.py'), $Phase)
if ($Phase -ne 'selftest') { $gateArgs += @('--out', $Out) }
& $python @gateArgs
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
Write-Host "Evidence: $Out"
exit $code
