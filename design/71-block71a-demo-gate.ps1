param([string]$Out = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) ('block71a-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))))
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'microclaw'
$active = (Get-Content -LiteralPath (Join-Path $root 'active-slot.txt') -Raw).Trim()
if ($active -notin @('a', 'b')) { throw 'active-slot.txt must name a or b.' }
$python = Join-Path $root "env-$active\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Active interpreter missing: $python" }

# Probe a native command without letting PowerShell turn its stderr into a
# terminating NativeCommandError: under $ErrorActionPreference = 'Stop' a
# redirected native stderr can throw, which would kill this gate on the very
# line that exists to print a diagnosis.
function Invoke-SlotProbe([string]$code) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $python -c $code 2>&1 | Out-Null; return $LASTEXITCODE }
    finally { $ErrorActionPreference = $previous }
}

# The slot runs the code the installer put there, NOT this checkout. Checking
# the branch out supplies the gate script and nothing else, so without
# install.bat the gate imports the slot's older microclaw and all eleven limbs
# report NOT EXERCISED -- which is what happened on 2026-09-21 and cost a trip.
# Say it in one line instead of eleven.
if ((Invoke-SlotProbe 'import microclaw.extensions') -ne 0) {
    $ErrorActionPreference = 'Continue'
    $where = (& $python -c 'import microclaw,sys; sys.stdout.write(microclaw.__file__)' 2>&1 | Select-Object -First 1)
    $ErrorActionPreference = 'Stop'
    Write-Host ''
    Write-Host 'STOP: the active slot is not running this branch.' -ForegroundColor Red
    Write-Host "  slot interpreter : $python"
    Write-Host "  its microclaw    : $where"
    Write-Host '  microclaw.extensions is absent, so the slot predates block 71a.'
    Write-Host ''
    Write-Host '  Install this branch into the slot, then run this gate again:'
    Write-Host '      cd $env:USERPROFILE\Documents\GitHub\microclaw'
    Write-Host '      git fetch origin'
    Write-Host '      git checkout block-71a'
    Write-Host '      git pull'
    Write-Host '      .\install.bat'
    Write-Host ''
    exit 2
}

# Record WHICH commit is under test, into the evidence, so a passing gate can be
# tied to a tree afterwards rather than taken on trust.
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$probe = 'import json,sys,microclaw' +
    ';from microclaw.updates import user_data_dir' +
    ';p=user_data_dir()/"update-state.json"' +
    ';s=json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}' +
    ';sys.stdout.write(json.dumps({"installed_commit":s.get("installed_commit"),' +
    '"microclaw":microclaw.__file__,"python":sys.executable},indent=2))'
$ErrorActionPreference = 'Continue'
& $python -c $probe > (Join-Path $Out 'under-test.json') 2>&1
$ErrorActionPreference = 'Stop'
Get-Content -LiteralPath (Join-Path $Out 'under-test.json')

# h5py already in the slot makes the absent-extension limbs NOT EXERCISED.
# install.bat never installs it, and this gate's own install limb puts it back,
# so removing it beforehand is both safe and self-healing.
if ((Invoke-SlotProbe 'import h5py') -eq 0) {
    Write-Host ''
    Write-Host 'NOTE: h5py is already in this slot, so the absent-extension limbs' -ForegroundColor Yellow
    Write-Host '      will report NOT EXERCISED. To exercise them, remove it first:'
    Write-Host "      uv pip uninstall --python `"$python`" h5py"
    Write-Host '      This gate reinstalls it; nothing in microclaw[serve] needs it.'
    Write-Host ''
}

# The native child owns gate.log; Start-Transcript cannot capture its stdout.
& $python (Join-Path $PSScriptRoot '71-block71a-demo-gate.py') --out $Out
$code = $LASTEXITCODE
Write-Host "Evidence: $Out"
exit $code
