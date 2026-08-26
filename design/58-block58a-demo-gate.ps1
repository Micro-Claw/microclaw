# Block 58a demo gate. Run this file; do not paste its contents.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File design\58-block58a-demo-gate.ps1
#
# Why a script and not a runbook of blocks: pasted interactively, a `throw` ends
# only the current pipeline, so round 1 failed four limbs and still printed
# PASSED. A script cannot do that. It also captures its own evidence, which the
# runbook did not - the operator had to send console scrollback.
#
# Micro-Manager is not needed. Nothing here touches hardware.

$ErrorActionPreference = 'Stop'

$repo = (git rev-parse --show-toplevel)
if ($LASTEXITCODE -ne 0) { throw 'Not inside a git checkout.' }
$repo = $repo.Trim()
Set-Location $repo

git merge-base --is-ancestor 84d49cb HEAD
if ($LASTEXITCODE -ne 0) {
    throw 'This checkout does not contain Block 58a. Run: git checkout design58/discovery'
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$evidence = Join-Path ([Environment]::GetFolderPath('MyDocuments')) "block58a-$stamp"
New-Item -ItemType Directory -Path $evidence -Force | Out-Null
# The Python writes its own gate.txt. Start-Transcript is deliberately NOT used:
# in PowerShell 5.1 it does not capture a native child process's stdout, so round
# 2's transcript held a header, a footer, and nothing else.
uv run python (Join-Path $repo 'design\58-block58a-demo-gate.py') --repo $repo --out $evidence
$code = $LASTEXITCODE

Write-Host ''
Write-Host "Evidence written to: $evidence"
Write-Host '  gate.txt      full output, including the environment block'
Write-Host '  results.json  one record per limb'
Write-Host '  *-state.json  the update state each limb produced'
Write-Host ''
Write-Host 'Send that whole folder back.'

if ($code -ne 0) {
    Write-Host ''
    Write-Host 'GATE DID NOT PASS - see the FAIL / NOT EXERCISED lines above.' -ForegroundColor Red
}
exit $code
