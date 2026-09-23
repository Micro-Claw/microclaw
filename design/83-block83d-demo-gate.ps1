param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('prepare','desktop','updated','rolledback','reinstalled','verify')]
    [string]$Phase,
    [string]$Out
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'microclaw'
$pointer = Join-Path $root '83d-gate-evidence.txt'
if (-not $Out) {
    if ($Phase -eq 'prepare') {
        $Out = Join-Path $env:LOCALAPPDATA ('block83d-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    } elseif (Test-Path -LiteralPath $pointer) {
        $Out = (Get-Content -LiteralPath $pointer -Raw).Trim()
    } else { throw 'No evidence folder recorded. Run -Phase prepare first, or pass -Out.' }
}
$active = (Get-Content -LiteralPath (Join-Path $root 'active-slot.txt') -Raw).Trim()
if ($active -notin @('a','b')) { throw 'active-slot.txt must name a or b.' }
# install.bat rebuilds the ACTIVE slot during 'reinstalled'; Windows cannot replace a
# running interpreter, so that phase (and verify after it) runs from the other slot.
$slot = $active
if ($Phase -in @('reinstalled','verify')) { $slot = if ($active -eq 'a') { 'b' } else { 'a' } }
$python = Join-Path $root "env-$slot\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Gate interpreter missing: $python" }
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $python -I -c "from microclaw import skill_store; assert callable(skill_store.start_job)" 2>&1 | Out-Null
$probe = $LASTEXITCODE
$ErrorActionPreference = $previous
if ($probe -ne 0) {
    Write-Host "NOT EXERCISED: $python lacks block 83d. Run step 0 (install.bat on this branch)." -ForegroundColor Red
    exit 2
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null
if ($Phase -eq 'prepare') { Set-Content -LiteralPath $pointer -Value $Out -Encoding ASCII }
Write-Host "Gate interpreter: $python"
$ErrorActionPreference = 'Continue'
& $python (Join-Path $PSScriptRoot '83-block83d-demo-gate.py') $Phase --out $Out
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
Write-Host "Evidence: $Out"
exit $code
