# Stable protocol-1 launcher. install.bat is its only writer.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$activePath = Join-Path $root 'active-slot.txt'
$pendingPath = Join-Path $root 'pending-slot.txt'
$healthPath = Join-Path $root 'launch-health.txt'
$protocolPath = Join-Path $root 'launcher-protocol.txt'

function Read-Slot([string]$path, [bool]$required) {
    if (-not (Test-Path -LiteralPath $path)) {
        if ($required) { throw "Missing launcher state: $path" }
        return $null
    }
    $value = (Get-Content -LiteralPath $path -Raw).Trim()
    if ($value -ne 'a' -and $value -ne 'b') { throw "Invalid slot selector: $path" }
    return $value
}

$active = Read-Slot $activePath $true
$launcherProtocol = (Get-Content -LiteralPath $protocolPath -Raw).Trim()
if ($launcherProtocol -notmatch '^[1-9][0-9]*$') { throw 'Invalid installed launcher protocol.' }
$selectorPython = Join-Path $root "env-$active\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $selectorPython)) { throw "Active slot Python is missing: $selectorPython" }
# The tested Python state machine owns pending validation, protocol checks,
# atomic activation, and consumption. PowerShell only carries its two text lines.
$selection = @(& $selectorPython -c "import sys; from microclaw.updates import activate_pending; a,p=activate_pending(sys.argv[1],int(sys.argv[2])); print(a); print(p or '')" $root $launcherProtocol)
if ($LASTEXITCODE -ne 0 -or $selection.Count -lt 2) { throw 'Pending-slot activation failed.' }
$active = $selection[0].Trim()
$previous = $selection[1].Trim()
if ($previous -eq '') { $previous = $null }

$exe = Join-Path $root "env-$active\Scripts\microclaw.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    if ($null -ne $previous) {
        & $selectorPython -c "import sys; from microclaw.updates import rollback_slot; rollback_slot(sys.argv[1],sys.argv[2],sys.argv[3])" $root $active $previous
    }
    throw "Active slot executable is missing: $exe"
}

$launch = @(& $selectorPython -c "import sys; from microclaw.updates import fresh_launch; n,p=fresh_launch(sys.argv[1],sys.argv[2]); print(n); print(p)" $root $active)
if ($LASTEXITCODE -ne 0 -or $launch.Count -lt 2) { throw 'Could not prepare fresh launcher health.' }
$nonce = $launch[0].Trim()
$healthPath = $launch[1].Trim()
$logPath = Join-Path $root 'launcher.log'
Add-Content -LiteralPath $logPath -Value ("{0:o} slot={1} nonce={2}" -f (Get-Date), $active, $nonce)
if ((Get-Item -LiteralPath $logPath).Length -gt 65536) {
    $tail = Get-Content -LiteralPath $logPath -Tail 200
    Set-Content -LiteralPath $logPath -Value $tail -Encoding UTF8
}
$env:MICROCLAW_LAUNCHER_OWNED = '1'
$env:MICROCLAW_LAUNCH_ROOT = $root
$env:MICROCLAW_LAUNCH_SLOT = $active
$env:MICROCLAW_LAUNCH_NONCE = $nonce
$env:MICROCLAW_LAUNCHER_PROTOCOL = $launcherProtocol
$child = Start-Process -FilePath $exe -ArgumentList 'serve' -PassThru -NoNewWindow
$verdict = & $selectorPython -c "import sys; from microclaw.updates import wait_for_launcher_health; print(wait_for_launcher_health(sys.argv[1],sys.argv[2],int(sys.argv[3])))" $healthPath $nonce $child.Id
$healthy = ($verdict.Trim() -ceq 'healthy')

if (-not $healthy) {
    if ($verdict.Trim() -ceq 'timeout') { Stop-Process -Id $child.Id -Force }
    if ($null -ne $previous) {
        & $selectorPython -c "import sys; from microclaw.updates import rollback_slot; rollback_slot(sys.argv[1],sys.argv[2],sys.argv[3])" $root $active $previous
    }
    exit 1
}

$report = & $selectorPython -c "import sys; from microclaw.updates import consume_rollback_report; print(consume_rollback_report(sys.argv[1]) or '')" $root
if ($report.Trim()) {
    Write-Host $report.Trim()
    Add-Content -LiteralPath $logPath -Value ("{0:o} rollback-reported={1}" -f (Get-Date), $report.Trim())
}
# Health is final: even a later bridge failure keeps this slot and never relaunches.
if (-not $child.HasExited) { $child.WaitForExit() }
exit $child.ExitCode
