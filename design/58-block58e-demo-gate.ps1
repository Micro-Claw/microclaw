param([ValidateSet('Prepare','Direct','Stage','Restart','Failure','Closed','Restore','PublicZip','Verify')][string]$Mode)
$ErrorActionPreference = 'Stop'
$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo
git merge-base --is-ancestor 7e584af HEAD
if ($LASTEXITCODE -ne 0) { throw 'This checkout does not contain the complete block 58e implementation.' }
$pointer = "$env:LOCALAPPDATA\microclaw\58e-gate-evidence.txt"
if ($Mode -eq 'Prepare') {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $evidence = Join-Path ([Environment]::GetFolderPath('MyDocuments')) "block58e-$stamp"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $pointer) | Out-Null
    Set-Content -LiteralPath $pointer -Value $evidence -Encoding ASCII
} else {
    if (-not (Test-Path -LiteralPath $pointer)) { throw 'Run the Prepare phase first.' }
    $evidence = (Get-Content -LiteralPath $pointer -Raw).Trim()
}
# Session-only: never setx. A staging failure phase receives an unreachable index.
if ($Mode -eq 'Failure') { $env:UV_INDEX_URL = 'https://127.0.0.1:1/unreachable' }
python -S (Join-Path $repo 'design\58-block58e-demo-gate.py') --mode $Mode.ToLowerInvariant() --repo $repo --out $evidence
$code = $LASTEXITCODE
Write-Host "Evidence written to: $evidence"
if ($code -ne 0) { Write-Host "$Mode did not pass; send the evidence folder anyway." -ForegroundColor Red }
exit $code
