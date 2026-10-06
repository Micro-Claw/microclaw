param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('fixtures','prepare','session','verify','cleanup','selftest')]
    [string]$Phase,
    [string]$Out,
    [ValidateSet('83f3','83f6')]
    [string]$Block = '83f3'
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'microclaw'
$pointer = Join-Path $root "$Block-gate-evidence.txt"
if (-not $Out) {
    if ($Phase -in @('prepare','fixtures','selftest')) {
        $Out = Join-Path $env:LOCALAPPDATA ('block' + $Block + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    } elseif (Test-Path -LiteralPath $pointer) {
        $Out = (Get-Content -LiteralPath $pointer -Raw -Encoding UTF8).Trim()
    } elseif ($Phase -eq 'cleanup') {
        $Out = Join-Path $env:LOCALAPPDATA ('block' + $Block + '-cleanup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    } else { throw 'No evidence folder recorded. Run -Phase prepare first, or pass -Out.' }
}
$active = (Get-Content -LiteralPath (Join-Path $root 'active-slot.txt') -Raw).Trim()
if ($active -notin @('a','b')) { throw 'active-slot.txt must name a or b.' }
$python = Join-Path $root "env-$active\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Gate interpreter missing: $python" }
# The active slot must carry the gate's product features. Both gates share
# this interpreter/pointer logic; 83f-6 additionally requires pack --locks.
$probeCode = "from microclaw import skill_store; assert callable(getattr(skill_store, 'panel_catalog', None))"
if ($Block -eq '83f6') {
    $probeCode += "; import inspect; from microclaw import catalog_intake; assert 'locks' in inspect.signature(catalog_intake.pack).parameters"
}
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $python -I -c $probeCode 2>&1 | Out-Null
$probe = $LASTEXITCODE
$ErrorActionPreference = $previous
if ($probe -ne 0) {
    Write-Host "NOT EXERCISED: $python lacks the required $Block product features. Run step 0 (install.bat on this branch)." -ForegroundColor Red
    exit 2
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null
Write-Host "Gate interpreter: $python"
$ErrorActionPreference = 'Continue'
& $python (Join-Path $PSScriptRoot "83-block$Block-demo-gate.py") $Phase --out $Out
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
Write-Host "Evidence: $Out"
exit $code
