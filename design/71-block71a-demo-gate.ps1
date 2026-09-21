param(
    [string]$Out = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) ('block71a-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))),
    # -Fresh undoes what a previous run of this gate left behind: the installed
    # h5py, the extensions.json record, and uv's warm cache. Without it a second
    # run cannot exercise the absent-extension limbs or see uv download anything.
    [switch]$Fresh
)
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

New-Item -ItemType Directory -Force -Path $Out | Out-Null

# The gate program writes under-test.json itself. It used to be a `python -c`
# string here and PowerShell stripped its inner double quotes when building the
# native command line, so `user_data_dir()/"update-state.json"` arrived as a bare
# name and raised NameError -- and `>` wrote the traceback out as UTF-16.
# Measured on the demo machine, 2026-09-21. Anything that computes belongs in
# the program, not in the shell that launches it.

if ($Fresh) {
    # uv is not necessarily on PATH -- the first gate run found it through the
    # installer's bootstrap location, which is exactly why locate_uv() has that
    # route. Resolve it the same way rather than assuming.
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv) { $uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe' }
    if (-not (Test-Path -LiteralPath $uv)) { throw "uv not found on PATH or at $uv" }
    Write-Host "Resetting for a fresh run (uv: $uv)"

    $ErrorActionPreference = 'Continue'
    & $uv pip uninstall --python $python h5py 2>&1 | Out-Null
    Write-Host ('  h5py uninstalled from the slot: exit ' + $LASTEXITCODE)
    & $uv cache clean h5py numpy 2>&1 | Out-Null
    Write-Host ('  uv cache cleared for h5py and numpy: exit ' + $LASTEXITCODE)
    $ErrorActionPreference = 'Stop'

    $record = Join-Path $root 'extensions.json'
    if (Test-Path -LiteralPath $record) {
        Copy-Item -LiteralPath $record -Destination (Join-Path $Out 'extensions.json.before') -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $record -Force
        Write-Host "  removed $record (the gate rewrites it)"
    } else {
        Write-Host "  no extensions.json to remove"
    }
    Write-Host ''
}

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
