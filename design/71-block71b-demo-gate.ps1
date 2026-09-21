param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('prepare','installed','staged','restarted','reinstalled','recovery','restore','verify')]
    [string]$Phase,
    [string]$Out,
    [switch]$Fresh
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'microclaw'
$pointer = Join-Path $root '71b-gate-evidence.txt'
if (-not $Out) {
    if ($Phase -eq 'prepare') {
        $Out = Join-Path $env:LOCALAPPDATA ('block71b-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    } elseif (Test-Path -LiteralPath $pointer) {
        $Out = (Get-Content -LiteralPath $pointer -Raw).Trim()
    } else { throw 'No evidence folder recorded. Run prepare first, or supply -Out.' }
}
if ($Fresh -and $Phase -ne 'prepare') { throw '-Fresh is only valid for prepare.' }
$journalPath = Join-Path $Out 'recovery-journal.json'
if ($Phase -eq 'restore' -and (Test-Path -LiteralPath $journalPath)) {
    # Recovery must still be callable after an interrupted selector restore.
    $journal = Get-Content -LiteralPath $journalPath -Raw | ConvertFrom-Json
    $active = $journal.slot
} else {
    $active = (Get-Content -LiteralPath (Join-Path $root 'active-slot.txt') -Raw).Trim()
}
if ($active -notin @('a','b')) { throw 'active-slot.txt must name a or b.' }
$slot = $active
# A process executing inside the preserved environment would prevent its rename
# on Windows. Use the other slot, including during manual restore retries.
if ($Phase -in @('reinstalled','recovery','restore')) {
    $slot = if ($active -eq 'a') { 'b' } else { 'a' }
}
$python = Join-Path $root "env-$slot\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Control interpreter missing: $python. Retain the preserved environment; send the evidence folder." }
function Invoke-SlotProbe([string]$code) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $python -I -c $code 2>&1 | Out-Null; return $LASTEXITCODE }
    finally { $ErrorActionPreference = $previous }
}
if ((Invoke-SlotProbe 'pass') -ne 0) { throw "Control interpreter cannot run: $python" }
# Only prepare requires the branch build: staging deliberately targets the
# discovery candidate on origin/main, which can predate this branch before merge.
if ($Phase -eq 'prepare' -and (Invoke-SlotProbe 'from microclaw import skills,extensions; assert len(skills.SkillMetadata.__dataclass_fields__)==4; assert callable(extensions.recorded_errors)') -ne 0) {
    Write-Host 'STOP: active slot lacks 71b/71c; checkout block-71b AND run .\install.bat before prepare.' -ForegroundColor Red
    exit 2
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null
if ($Phase -eq 'prepare') {
    Set-Content -LiteralPath $pointer -Value $Out -Encoding UTF8
}
$gateArgs = @((Join-Path $PSScriptRoot '71-block71b-demo-gate.py'), $Phase, '--out', $Out)
if ($Fresh) { $gateArgs += '--fresh' }
Write-Host "Gate interpreter: $python"
$ErrorActionPreference = 'Continue'
& $python @gateArgs
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
Write-Host "Evidence: $Out"
exit $code
