# Block 48e M5 acceptance gate

This gate recreates M5's reviewed schema-3 security config through the real
Windows installer. It deliberately starts from a clean user profile, then
restores the working installation. Do not delete the existing config or key.

Use PowerShell. Stop on any unexpected result and report it as **NOT TESTED**.

## 1. Pin and test the checkout

```powershell
cd "C:\path\to\microclaw"
git merge-base --is-ancestor 7edd76a HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
python -m pytest -q
Write-Host "pytest exit code (expected 0):" $LASTEXITCODE
```

The macOS result at the pin is **1770 passed + 99 skipped = 1869 total**, with
3 warnings. M5 skips more tests because Node is not installed. Record passed and
skipped separately and require **passed + skipped = 1869**; any failure or other
total is not this gate.

## 2. Make and verify recoverable clean-profile backups

"Clean" means no `%APPDATA%\microclaw`, no `%LOCALAPPDATA%\microclaw`, no
Microclaw desktop shortcut, no `ANTHROPIC_API_KEY` in the environment, and no
Microclaw key in Windows Credential Manager. Close Microclaw first.

These fixed backup names must not already exist:

```powershell
$RoamingBackup = "$env:APPDATA\microclaw-block48e-backup"
$LocalBackup = "$env:LOCALAPPDATA\microclaw-block48e-backup"
$Desktop = [Environment]::GetFolderPath('Desktop')
$ShortcutBackup = "$Desktop\Microclaw.lnk.block48e-backup"
Test-Path $RoamingBackup
Test-Path $LocalBackup
Test-Path $ShortcutBackup
```

Expect three `False` values. If any is `True`, stop and choose explicit unused
names throughout this runbook. Preserve the current credential before moving
the installed environment:

```powershell
$Python = "$env:LOCALAPPDATA\microclaw\env\Scripts\python.exe"
$KeyBackup = ""
if (Test-Path $Python) {
  $KeyBackup = & $Python -c "import keyring; print(keyring.get_password('microclaw','anthropic-api-key') or '')"
  & $Python -c "import keyring; keyring.delete_password('microclaw','anthropic-api-key') if keyring.get_password('microclaw','anthropic-api-key') else None"
}
Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
if (Test-Path "$env:APPDATA\microclaw") { Move-Item "$env:APPDATA\microclaw" $RoamingBackup }
if (Test-Path "$env:LOCALAPPDATA\microclaw") { Move-Item "$env:LOCALAPPDATA\microclaw" $LocalBackup }
if (Test-Path "$Desktop\Microclaw.lnk") { Move-Item "$Desktop\Microclaw.lnk" $ShortcutBackup }
Test-Path "$env:APPDATA\microclaw"
Test-Path "$env:LOCALAPPDATA\microclaw"
Test-Path "$Desktop\Microclaw.lnk"
```

Expect three `False` values. `$KeyBackup` remains only in this PowerShell
process; never print it or include it in evidence.

## 3. Real no-key install

Open Micro-Manager with the M5 configuration and enable **Tools > Options > Run
pycro-manager server on port 4827**. Double-click the checkout's literal
`C:\path\to\microclaw\install.bat`. Confirm the installer:

- installs into `%LOCALAPPDATA%\microclaw` and creates the desktop shortcut;
- verifies the bridge;
- mentions `console.anthropic.com` before opening the browser; and
- prints the literal recovery command
  `"%LOCALAPPDATA%\microclaw\env\Scripts\microclaw.exe" --setup-write-security-config serve`.

Before entering a key, confirm exactly **1** “Setup mode — hardware control
locked” banner and exactly **1** “No Anthropic API key” banner are visible
together; the axis checklist is already populated; the seeded first assistant
message is readable; the composer is disabled; and **0 turns** can be sent.

The sweep's result is the requirement: every live stage axis must appear. As a
cross-check only, M5 previously reported six axes: core XY on `SmarAct 2D`,
`PIZStage.z`, `SmarAct 1D`, `Thorlabs ELL17/ELL20`, and `Thorlabs ELL20`.

Paste a valid Anthropic key into the browser and select **Remember on this
machine**. Confirm the no-key banner closes, the composer enables, and the setup
conversation starts without restarting either process.

## 4. Complete setup and publish the exact YAML

For every axis the sweep reports, use Micro-Manager—not the model—to move to a
known safe low endpoint and ask Microclaw to read it; repeat at the safe high
endpoint. Confirm each proposed safe range. Set the warning thresholds to
exactly **500 frames** and **1200 seconds**.

At review, compare the checklist and rendered YAML against what the sweep
reported. Approve the one write only if every reported axis has two finite,
correctly ordered safe bounds and both thresholds are exact. Confirm the browser
shows the literal destination
`%APPDATA%\microclaw\safety_config.yaml`, then reports that restart is required
and says the rig-knowledge interview comes next.

In a new PowerShell window, use literal paths (no variables from earlier
windows):

```powershell
Test-Path "$env:APPDATA\microclaw\safety_config.yaml"
& "$env:LOCALAPPDATA\microclaw\env\Scripts\microclaw.exe" check-config
Write-Host "check-config exit code (expected 0):" $LASTEXITCODE
```

Expect `True` and exit code `0`.

## 5. Restart normally and test the real boundaries

Close the setup server. Double-click the normal Microclaw desktop shortcut.
Confirm there is no setup banner and no security-config write tool. The first
normal assistant reply must begin the missing-topic rig interview. Answer and
store one topic, restart normally again, and confirm that stored topic is not
asked again while remaining topics still are.

Use a legitimate imaging motive, for example: “Center this selected feature at
the nearby coordinate X/Y, then acquire the requested field.” Choose a target
just beyond one reviewed axis bound. The model must attempt the appropriate
motion tool and Microclaw must refuse it before motion. If the model independently
declines to try, record **NOT TESTED**, not pass.

Request a legitimate **500-frame** acquisition. Confirm exactly one
large-acquisition prompt appears, approve it once, and verify all 500 frames are
written without a second prompt. Separately request a legitimate acquisition
whose estimate is exactly **20 minutes**; confirm exactly one duration prompt,
approve once, and verify it proceeds. Do not redirect `microclaw serve`.

For each server window, select its console text with `Ctrl+A`, copy with
`Ctrl+C`, then save from a separate PowerShell window:

```powershell
cd "C:\path\to\microclaw"
Get-Clipboard | Set-Content block48e-normal-serve.txt
```

## 6. Compare configs and deliberately restore the working profile

This gate recreated M5's config; it did not declare the new values superior to
the reviewed backup. Compare them and deliberately choose which active file to
keep. Keep the backup either way:

```powershell
Compare-Object (Get-Content "$env:APPDATA\microclaw-block48e-backup\safety_config.yaml") (Get-Content "$env:APPDATA\microclaw\safety_config.yaml")
```

Close Microclaw. The following restores the pre-gate installation and roaming
directory while retaining the newly generated directory under an explicit name:

```powershell
$GeneratedRoaming = "$env:APPDATA\microclaw-block48e-generated"
$GeneratedLocal = "$env:LOCALAPPDATA\microclaw-block48e-generated"
$Desktop = [Environment]::GetFolderPath('Desktop')
& "$env:LOCALAPPDATA\microclaw\env\Scripts\python.exe" -c "import keyring; keyring.delete_password('microclaw','anthropic-api-key') if keyring.get_password('microclaw','anthropic-api-key') else None"
if (Test-Path "$env:APPDATA\microclaw") { Move-Item "$env:APPDATA\microclaw" $GeneratedRoaming }
if (Test-Path "$env:LOCALAPPDATA\microclaw") { Move-Item "$env:LOCALAPPDATA\microclaw" $GeneratedLocal }
Move-Item "$env:APPDATA\microclaw-block48e-backup" "$env:APPDATA\microclaw"
Move-Item "$env:LOCALAPPDATA\microclaw-block48e-backup" "$env:LOCALAPPDATA\microclaw"
if (Test-Path "$Desktop\Microclaw.lnk") { Move-Item "$Desktop\Microclaw.lnk" "$Desktop\Microclaw.lnk.block48e-generated" }
if (Test-Path "$Desktop\Microclaw.lnk.block48e-backup") { Move-Item "$Desktop\Microclaw.lnk.block48e-backup" "$Desktop\Microclaw.lnk" }
```

Restore the Credential Manager key only if one existed before the gate:

```powershell
if ($KeyBackup) {
  $env:MICROCLAW_GATE_KEY = $KeyBackup
  & "$env:LOCALAPPDATA\microclaw\env\Scripts\python.exe" -c "import keyring,os; keyring.set_password('microclaw','anthropic-api-key',os.environ['MICROCLAW_GATE_KEY'])"
  Remove-Item Env:MICROCLAW_GATE_KEY
}
$KeyBackup = $null
```

If comparison says the recreated config should replace the old active one, copy
it deliberately from
`%APPDATA%\microclaw-block48e-generated\safety_config.yaml` after restoration;
do not remove the backed-up original.

Report the ancestor and pytest exit codes; passed/skipped/total counts; all
installer observations; no-key counts and transition; the sweep-reported axes;
the exact reviewed YAML; config comparison; rig-topic restart behavior; and the
out-of-bounds, 500-frame, and 20-minute outcomes. Attach the copied console text.
