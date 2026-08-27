$ErrorActionPreference = 'Stop'
$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo
git merge-base --is-ancestor 01b634f HEAD
if ($LASTEXITCODE -ne 0) { throw 'This checkout does not contain block 58c.' }
$pointer = "$env:LOCALAPPDATA\microclaw\58c-gate-evidence.txt"
if (-not (Test-Path $pointer)) { throw 'The human gate did not create its evidence pointer.' }
$evidence = (Get-Content $pointer -Raw).Trim()
uv run python (Join-Path $repo 'design\58-block58c-demo-gate.py') --repo $repo --out $evidence
$code = $LASTEXITCODE
Write-Host "Evidence written to: $evidence"
if ($code -ne 0) { Write-Host 'GATE DID NOT PASS - FAIL and NOT EXERCISED are never passes.' -ForegroundColor Red }
exit $code
