param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('prepare','measure','session','verify','cleanup','selftest')]
    [string]$Phase,
    [string]$Out,
    [int[]]$SelftestSizes = @(2,120,40),
    [int]$SelftestFrameSize = 32
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'microclaw'
$pointer = Join-Path $root '87a-gate-evidence.txt'
if (-not $Out) {
    if ($Phase -in @('prepare','selftest')) {
        $Out = Join-Path $env:LOCALAPPDATA ('block87a-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    } elseif (Test-Path -LiteralPath $pointer) {
        $Out = (Get-Content -LiteralPath $pointer -Raw -Encoding UTF8).Trim()
    } elseif ($Phase -eq 'cleanup') {
        $Out = Join-Path $env:LOCALAPPDATA ('block87a-cleanup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    } else { throw 'No evidence folder recorded. Run -Phase prepare first, or pass -Out.' }
}
$active = (Get-Content -LiteralPath (Join-Path $root 'active-slot.txt') -Raw).Trim()
if ($active -notin @('a','b')) { throw 'active-slot.txt must name a or b.' }
$python = Join-Path $root "env-$active\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Gate interpreter missing: $python" }
# The installed slot, not this checkout, must carry protocol 1.2 and the metrics accessor.
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $python -I -c "from microclaw import skill_packages, skill_supervisor; assert '1.2' in skill_packages.SUPPORTED_PROTOCOLS; assert hasattr(skill_supervisor.JobHandle, 'metrics_snapshot')" 2>&1 | Out-Null
$probe = $LASTEXITCODE
$ErrorActionPreference = $previous
if ($probe -ne 0) {
    Write-Host "NOT EXERCISED: $python lacks block 87a. Run step 0 (install.bat on this branch)." -ForegroundColor Red
    exit 2
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null
if ($Phase -eq 'prepare') { Set-Content -LiteralPath $pointer -Value $Out -Encoding UTF8 }
Write-Host "Gate interpreter: $python"
$ErrorActionPreference = 'Continue'
if ($Phase -eq 'selftest' -and $SelftestSizes.Count -ne 3) { throw 'SelftestSizes needs repetitions, burst frames, session frames.' }
$gateArgs = @((Join-Path $PSScriptRoot '87-block87a-demo-gate.py'), $Phase, '--out', $Out)
if ($Phase -eq 'selftest') {
    $gateArgs += @('--selftest-frame-size', [string]$SelftestFrameSize)
    $gateArgs += '--selftest-sizes'
    foreach ($size in $SelftestSizes) { $gateArgs += [string]$size }
}
& $python @gateArgs
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
Write-Host "Evidence: $Out"
exit $code
