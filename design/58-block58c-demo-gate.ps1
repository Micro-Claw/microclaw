param([ValidateSet('Prepare','Healthy','Closed','Rollback','Verify')][string]$Mode)
$ErrorActionPreference = 'Stop'
$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo
git merge-base --is-ancestor 89b5451 HEAD
if ($LASTEXITCODE -ne 0) { throw 'This checkout does not contain the complete block 58c implementation.' }
$pointer = "$env:LOCALAPPDATA\microclaw\58c-gate-evidence.txt"
if ($Mode -eq 'Prepare') {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $evidence = Join-Path ([Environment]::GetFolderPath('MyDocuments')) "block58c-$stamp"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $pointer) | Out-Null
    Set-Content -LiteralPath $pointer -Value $evidence -Encoding ASCII
} else {
    if (-not (Test-Path -LiteralPath $pointer)) { throw 'Run the Prepare phase first.' }
    $evidence = (Get-Content -LiteralPath $pointer -Raw).Trim()
}
uv run python (Join-Path $repo 'design\58-block58c-demo-gate.py') `
    --mode $Mode.ToLowerInvariant() --repo $repo --out $evidence
$code = $LASTEXITCODE
Write-Host "Evidence written to: $evidence"
if ($code -ne 0) { Write-Host "$Mode did not pass; send the evidence folder anyway." -ForegroundColor Red }
exit $code
