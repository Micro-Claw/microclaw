param([ValidateSet('Prepare','Direct','Stage','NotReady','Restart','Later','Ordinary','Rollback','Failure','Offline','Closed','Restore','PublicZip','Verify')][string]$Mode)
$ErrorActionPreference = 'Stop'
$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo
git merge-base --is-ancestor fc9dd4e HEAD
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
# Failure launches the actual active server from this process, so the staging
# worker inherits the unreachable index. It is session-scoped and never setx.
$failureChild = $null
if ($Mode -eq 'Failure') {
    $env:UV_INDEX_URL = 'https://127.0.0.1:1/unreachable'
    $active = (Get-Content -LiteralPath "$env:LOCALAPPDATA\microclaw\active-slot.txt" -Raw).Trim()
    $exe = "$env:LOCALAPPDATA\microclaw\env-$active\Scripts\microclaw.exe"
    $failureChild = Start-Process -FilePath $exe -ArgumentList 'serve --no-browser' -PassThru -NoNewWindow
}
# The Python process imports the checkout's gate helpers through its real project
# environment. It owns gate.txt because PowerShell 5.1 transcripts omit native stdout.
uv run python (Join-Path $repo 'design\58-block58e-demo-gate.py') `
    --mode $Mode.ToLowerInvariant() --repo $repo --out $evidence
$code = $LASTEXITCODE
if ($null -ne $failureChild -and -not $failureChild.HasExited) { Stop-Process -Id $failureChild.Id }
Write-Host "Evidence written to: $evidence"
if ($code -ne 0) { Write-Host "$Mode did not pass; send the evidence folder anyway." -ForegroundColor Red }
exit $code
