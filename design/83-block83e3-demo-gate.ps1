param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('prepare','session','verify')]
    [string]$Phase,
    [string]$Out
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'microclaw'
$pointer = Join-Path $root '83e3-gate-evidence.txt'
if (-not $Out) {
    if ($Phase -eq 'prepare') {
        $Out = Join-Path $env:LOCALAPPDATA ('block83e3-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    } elseif (Test-Path -LiteralPath $pointer) {
        $Out = (Get-Content -LiteralPath $pointer -Raw).Trim()
    } else { throw 'No evidence folder recorded. Run -Phase prepare first, or pass -Out.' }
}
$active = (Get-Content -LiteralPath (Join-Path $root 'active-slot.txt') -Raw).Trim()
if ($active -notin @('a','b')) { throw 'active-slot.txt must name a or b.' }
$slot = $active
$python = Join-Path $root "env-$slot\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Gate interpreter missing: $python" }
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $python -I -c "from microclaw.tools_schema import ANALYSIS_TOOLS; assert ANALYSIS_TOOLS" 2>&1 | Out-Null
$probe = $LASTEXITCODE
$ErrorActionPreference = $previous
if ($probe -ne 0) {
    Write-Host "NOT EXERCISED: $python lacks block 83e-3. Run step 0 (install.bat on this branch)." -ForegroundColor Red
    exit 2
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null
if ($Phase -eq 'prepare') { Set-Content -LiteralPath $pointer -Value $Out -Encoding ASCII }
Write-Host "Gate interpreter: $python"
$ErrorActionPreference = 'Continue'
& $python (Join-Path $PSScriptRoot '83-block83e3-demo-gate.py') $Phase --out $Out
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
Write-Host "Evidence: $Out"
exit $code
