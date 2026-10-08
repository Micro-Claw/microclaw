param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('prepare','measure','session','verify','cleanup','selftest')]
    [string]$Phase,
    [string]$Out
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'microclaw'
$pointer = Join-Path $root '84b-gate-evidence.txt'
if (-not $Out) {
    if ($Phase -in @('prepare','selftest')) {
        $Out = Join-Path $env:LOCALAPPDATA ('block84b-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    } elseif (Test-Path -LiteralPath $pointer) {
        $Out = (Get-Content -LiteralPath $pointer -Raw -Encoding UTF8).Trim()
    } elseif ($Phase -eq 'cleanup') {
        $Out = Join-Path $env:LOCALAPPDATA ('block84b-cleanup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    } else { throw 'No evidence folder recorded. Run -Phase prepare first, or pass -Out.' }
}
$active = (Get-Content -LiteralPath (Join-Path $root 'active-slot.txt') -Raw).Trim()
if ($active -notin @('a','b')) { throw 'active-slot.txt must name a or b.' }
$python = Join-Path $root "env-$active\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Gate interpreter missing: $python" }
# The installed slot, not this checkout, must carry the window UI and F1 outcomes.
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $python -I -c "import inspect; from microclaw import webserve, skill_supervisor; assert '/api/skill-packages/windows' in inspect.getsource(webserve); assert 'already_closed' in inspect.getsource(skill_supervisor.Supervisor.close_window)" 2>&1 | Out-Null
$probe = $LASTEXITCODE
$ErrorActionPreference = $previous
if ($probe -ne 0) {
    Write-Host "NOT EXERCISED: $python lacks block 84b. Run step 0 (install.bat on this branch)." -ForegroundColor Red
    exit 2
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null
if ($Phase -eq 'prepare') { Set-Content -LiteralPath $pointer -Value $Out -Encoding UTF8 }
Write-Host "Gate interpreter: $python"
$ErrorActionPreference = 'Continue'
& $python (Join-Path $PSScriptRoot '84-block84b-demo-gate.py') $Phase --out $Out
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
Write-Host "Evidence: $Out"
exit $code
