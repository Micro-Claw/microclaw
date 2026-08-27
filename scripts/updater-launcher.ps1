# Stable protocol-1 launcher. install.bat is its only writer.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$activePath = Join-Path $root 'active-slot.txt'
$pendingPath = Join-Path $root 'pending-slot.txt'
$healthPath = Join-Path $root 'launch-health.txt'
$rollbackPath = Join-Path $root 'rollback-report.txt'

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
$previous = $null
$pending = Read-Slot $pendingPath $false
if ($null -ne $pending) {
    if ($pending -ne $active) {
        $previous = $active
        Set-Content -LiteralPath $activePath -Value $pending -Encoding ASCII
        $active = $pending
    }
    Remove-Item -LiteralPath $pendingPath -Force
}

$exe = Join-Path $root "env-$active\Scripts\microclaw.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    if ($null -ne $previous) {
        Set-Content -LiteralPath $activePath -Value $previous -Encoding ASCII
        Set-Content -LiteralPath $rollbackPath -Value "Microclaw rolled back from slot $active to slot $previous." -Encoding UTF8
    }
    throw "Active slot executable is missing: $exe"
}

$nonce = [Guid]::NewGuid().ToString('N')
Remove-Item -LiteralPath $healthPath -Force -ErrorAction SilentlyContinue
$env:MICROCLAW_LAUNCHER_OWNED = '1'
$env:MICROCLAW_LAUNCH_ROOT = $root
$env:MICROCLAW_LAUNCH_SLOT = $active
$env:MICROCLAW_LAUNCH_NONCE = $nonce
$child = Start-Process -FilePath $exe -ArgumentList 'serve' -PassThru -NoNewWindow
$deadline = (Get-Date).AddSeconds(30)
$healthy = $false
while (-not $child.HasExited -and (Get-Date) -lt $deadline) {
    if ((Test-Path -LiteralPath $healthPath) -and
        ((Get-Content -LiteralPath $healthPath -Raw).Trim() -ceq $nonce)) {
        $healthy = $true
        break
    }
    Start-Sleep -Milliseconds 100
    $child.Refresh()
}
if (-not $healthy -and (Test-Path -LiteralPath $healthPath)) {
    $healthy = ((Get-Content -LiteralPath $healthPath -Raw).Trim() -ceq $nonce)
}

if (-not $healthy) {
    if (-not $child.HasExited) { Stop-Process -Id $child.Id -Force }
    if ($null -ne $previous) {
        Set-Content -LiteralPath $activePath -Value $previous -Encoding ASCII
        Set-Content -LiteralPath $rollbackPath -Value "Microclaw rolled back from slot $active to slot $previous." -Encoding UTF8
    }
    exit 1
}

if (Test-Path -LiteralPath $rollbackPath) {
    Write-Host (Get-Content -LiteralPath $rollbackPath -Raw)
    Remove-Item -LiteralPath $rollbackPath -Force
}
# Health is final: even a later bridge failure keeps this slot and never relaunches.
if (-not $child.HasExited) { $child.WaitForExit() }
exit $child.ExitCode
