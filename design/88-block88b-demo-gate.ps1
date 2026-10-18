param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('prepare','measure','session','verify','cleanup','selftest')]
    [string]$Phase,
    [string]$Out
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'microclaw'
$pointer = Join-Path $root '88b-gate-evidence.txt'
if (-not $Out) {
    if ($Phase -in @('prepare','selftest')) {
        $Out = Join-Path $env:LOCALAPPDATA ('block88b-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    } elseif (Test-Path -LiteralPath $pointer) {
        $Out = (Get-Content -LiteralPath $pointer -Raw -Encoding UTF8).Trim()
    } elseif ($Phase -eq 'cleanup') {
        $Out = Join-Path $env:LOCALAPPDATA ('block88b-cleanup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    } else { throw 'No evidence folder recorded. Run -Phase prepare first, or pass -Out.' }
}
$active = (Get-Content -LiteralPath (Join-Path $root 'active-slot.txt') -Raw).Trim()
if ($active -notin @('a','b')) { throw 'active-slot.txt must name a or b.' }
$python = Join-Path $root "env-$active\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Gate interpreter missing: $python" }
# The installed slot, not this checkout, must carry block 88b.
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $python -I -c "from microclaw.tools_schema import ANALYSIS_TOOLS, TOOLS; assert 'run_mda' in ANALYSIS_TOOLS; assert 'analysis' in next(t for t in TOOLS if t['name'] == 'run_mda')['input_schema']['properties']" 2>&1 | Out-Null
$probe = $LASTEXITCODE
$ErrorActionPreference = $previous
if ($probe -ne 0) {
    Write-Host "NOT EXERCISED: $python lacks block 88b. Run step 0 (install.bat on this branch)." -ForegroundColor Red
    exit 2
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null
if ($Phase -eq 'prepare') { Set-Content -LiteralPath $pointer -Value $Out -Encoding UTF8 }
Write-Host "Gate interpreter: $python"
$ErrorActionPreference = 'Continue'
$gateArgs = @((Join-Path $PSScriptRoot '88-block88b-demo-gate.py'), $Phase, '--out', $Out)
& $python @gateArgs
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
Write-Host "Evidence: $Out"
exit $code
