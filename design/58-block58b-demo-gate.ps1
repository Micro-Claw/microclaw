# Block 58b demo gate. Micro-Manager is not needed; no hardware is touched.
$ErrorActionPreference = 'Stop'

$repo = (git rev-parse --show-toplevel)
if ($LASTEXITCODE -ne 0) { throw 'Not inside a git checkout.' }
$repo = $repo.Trim()
Set-Location $repo

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$evidence = Join-Path ([Environment]::GetFolderPath('MyDocuments')) "block58b-$stamp"
New-Item -ItemType Directory -Path $evidence -Force | Out-Null

# The Python process owns gate.txt because PowerShell 5.1 transcripts omit
# native-child stdout. uv supplies the checkout's environment and CLI.
uv run python (Join-Path $repo 'design\58-block58b-demo-gate.py') `
    --repo $repo --out $evidence
$code = $LASTEXITCODE

Write-Host ''
Write-Host "Evidence written to: $evidence"
Write-Host '  gate.txt      full output and environment'
Write-Host '  results.json  one PASS / FAIL / NOT EXERCISED record per limb'
Write-Host '  *.yaml        gate-owned config fixtures (missing.yaml stays absent)'
Write-Host 'Send that whole folder back.'
if ($code -ne 0) {
    Write-Host 'GATE DID NOT PASS - see FAIL / NOT EXERCISED above.' -ForegroundColor Red
}
exit $code
