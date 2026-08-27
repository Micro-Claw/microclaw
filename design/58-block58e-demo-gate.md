# Block 58e demo gate — restart and end-to-end update

Run these commands unedited and in order in Windows PowerShell 5.1. Send the
whole printed evidence directory back. The safety backup is deliberately its
sibling, not inside the evidence sent for review.

The branch is unmerged. `Prepare` records the
branch slot, and temporarily changes only `installed_commit` to the real
`origin/main~1`; otherwise discovery correctly reports `diverged`. Staging then
builds real `origin/main`, which does not contain 58e. That is expected: the
branch slot requests the restart and the external launcher performs it.

## Safety copy — the first literal command

This block can brick the managed install. Before checkout or installation,
copy both production roots beside (not inside) the later evidence directory:

```powershell
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'; $backup=Join-Path ([Environment]::GetFolderPath('MyDocuments')) "block58e-SAFETY-BACKUP-$stamp"; New-Item -ItemType Directory -Force -Path $backup | Out-Null; if(Test-Path "$env:APPDATA\microclaw"){Copy-Item "$env:APPDATA\microclaw" "$backup\appdata" -Recurse}; if(Test-Path "$env:LOCALAPPDATA\microclaw"){Copy-Item "$env:LOCALAPPDATA\microclaw" "$backup\localappdata" -Recurse}; Write-Host "SAFETY BACKUP COPIED TO: $backup"
```

## Install the branch under test

Close Microclaw, then run:

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
git fetch origin
git checkout design58/restart
git pull
.\install.bat
```

## Prepare and back up

The first gate command copies `%APPDATA%\microclaw` and the complete managed
root, prints the backup path, and records hashes and selectors before mutation.

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Prepare
```

## Direct executable: Restart later only

Start the branch slot directly with this literal command:

```powershell
$root="$env:LOCALAPPDATA\microclaw"; $slot=(Get-Content "$root\active-slot.txt" -Raw).Trim(); & "$root\env-$slot\Scripts\microclaw.exe" serve --no-browser
```

Leave it running. In a second PowerShell window run:

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
.\design\58-block58e-demo-gate.ps1 -Mode Direct
```

The program itself posts `/api/update/stage`, waits until `pending_staged` is
true, and then records `automatic_restart` and the process command line. It can
pass only when a real pending slot exists and that direct process still reports
`automatic_restart: false`.

Stop the direct server with Ctrl+C. Press Enter at its ordinary exit prompt.
Restore the inactive slot from the safety copy and remove the pending selector
before the launcher-owned phases. This literal command reads both paths from the
gate's own pointer and `prepare.json`:

```powershell
$root="$env:LOCALAPPDATA\microclaw"; $evidence=(Get-Content "$root\58e-gate-evidence.txt" -Raw).Trim(); $prep=Get-Content (Join-Path $evidence 'prepare.json') -Raw | ConvertFrom-Json; $backup=$prep.backup; $active=$prep.branch_active; $inactive=if($active -eq 'a'){'b'}else{'a'}; if(Test-Path "$root\env-$inactive"){Remove-Item "$root\env-$inactive" -Recurse -Force}; Copy-Item "$backup\localappdata\env-$inactive" "$root\env-$inactive" -Recurse; Remove-Item "$root\pending-slot.txt" -Force -ErrorAction SilentlyContinue; Write-Host "restored inactive slot $inactive and removed pending selector"
```

## Deliberately incompatible config — separate refusal phase

Start the branch from the desktop icon. With the server running, execute:

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
.\design\58-block58e-demo-gate.ps1 -Mode NotReady
```

This phase invokes the production `updates.stage_inactive_slot` function. A
gate-owned fake `uv` leaves the already-built inactive environment in place and
temporarily substitutes a CLI that classifies the one shared reviewed config as
`blocked`; the active real CLI classifies that same file normally. Production
comparison must refuse, cache `comparison_refused` plus its reason, and publish
no pending selector. The phase restores the original inactive executable and
slot marker in `finally` and records their hashes.

## Desktop staging, one-job refusal, progress and comparison

Double-click the desktop icon. With the browser open, run:

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
.\design\58-block58e-demo-gate.ps1 -Mode Stage
```

The program posts Stage twice while the first job owns the build and polls
cached status through `staging: true` and `pending_staged: true`. It imports
`microclaw.config` from this checkout and calls
`classify_config_with_slot` plus `compare_slot_configurations` on the two exact
`env-{slot}\Scripts\microclaw.exe` paths and one shared config. Each slot also
runs an isolated `python.exe -I` import from the evidence directory. Active
executable and marker hashes are recorded before and after; the inactive slot
must be the only one whose bytes change.

## Restart now and the exit pause

Start the observer, then click **Restart now** when it tells you:

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Restart
```

The observer times the operation and computes the two distinct launcher nonces,
selector flip, matching second health marker, consumed request, reconciled
commit, process command line, and marker beside the running executable. The one
human judgment is whether the console stopped at `Press Enter to close this
window...`; if it does, report the measured elapsed time and stop the gate.

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Ordinary
```

Follow the observer's prompts: Ctrl+C the relaunched child, wait until its
`Press Enter to close this window...` prompt is visible, then press Enter in the
observer. It records the process list while the prompt is displayed. Only then
press Enter in the child console and return to the observer. PASS requires the
same server PID to be present before Enter and absent afterwards; elapsed time
is reported but is not the criterion.

## Restart later

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Later
```

Run this with the desktop server open. The phase stages a fresh pending slot
itself, waits for readiness, then prompts you to click **Restart later**, stop
the server, and launch the icon once. It records the pending selector before
that launch and requires the next launch to activate and consume it.

## Unreachable PyPI, session-scoped

Close Microclaw and run:

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Failure
```

This phase starts the actual active server as a child of the PowerShell process
that holds `UV_INDEX_URL=https://127.0.0.1:1/unreachable`, posts Stage itself,
waits for the real worker, and stops the child. It never calls `setx`. The
scorer requires a cached build error, no pending selector, unchanged active
slot, and a retained retry deadline.

For the separate failed-start rollback, run this block unedited. It resolves the
inactive slot's installed package with that slot's isolated interpreter, hides
it while retaining the executable and marker, and publishes the slot pending:

```powershell
$root="$env:LOCALAPPDATA\microclaw"; $active=(Get-Content "$root\active-slot.txt" -Raw).Trim(); $failed=if($active -eq 'a'){'b'}else{'a'}; $py="$root\env-$failed\Scripts\python.exe"; $pkg=(& $py -I -c "import pathlib,microclaw; print(pathlib.Path(microclaw.__file__).parent)").Trim(); if($LASTEXITCODE -ne 0 -or -not (Test-Path $pkg)){throw 'inactive package is not runnable'}; $hidden="$pkg.58e-gate-hidden"; Move-Item -LiteralPath $pkg -Destination $hidden; Set-Content -LiteralPath "$root\pending-slot.txt" -Value $failed -Encoding ASCII; Write-Host "hidden for failed-start phase: $pkg"
```

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Rollback
```

Now follow the observer's two prompts. First double-click the icon once and wait
for the failing launch to exit; the observer snapshots the complete launcher
log and restores the hidden package itself. Launch the icon once more and return
to the observer after rollback is reported. PASS requires
no `rollback-reported=` log line after failure, exactly one after the next
healthy launch, and the original selector restored.

## Offline launch — its own phase

Leave Micro-Manager open. Close Microclaw, disconnect the network, and run:

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Offline
```

The phase temporarily selects the real `public-head` provider and removes
`last_attempt`/`next_check`, so a disconnected launch must attempt HTTPS rather
than accept a clone's cached remote ref with a warning. It records the prior
`last_success`, prompts for one desktop launch, captures the failure, and then
restores the original state file. PASS requires nonce-matched health, a moved
`last_attempt`, a cached network `last_error`, and byte-identical
`last_success`. Reconnect the network after the phase.

## Micro-Manager closed — separate from offline

With the network connected, close Micro-Manager and run:

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Closed
```

The phase first stages a real update through the running server. When prompted,
stop that server and double-click the desktop icon exactly once. The observer captures
the selector after that child starts, then asks you to wait for the bridge
refusal and exit. PASS requires exactly one new launcher line, matching health,
and the selector unchanged between child start and child exit. A hidden relaunch
or rollback therefore cannot look like success.

## Restore — even after a failed phase

Always run Restore. It restores the original `installed_commit` and active slot,
removes pending state, and records the after hash of `%APPDATA%\microclaw` for
comparison with Prepare.

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Restore
```

## Public ZIP last

Public ZIP replaces the managed install, so it goes last. Download GitHub's
**Download ZIP** into the normal Downloads folder, close Microclaw, then run
this block. It selects exactly the newest `microclaw*.zip`, fails if none exists,
extracts it, resolves the one `install.bat`, and runs it:

```powershell
$zip=Get-ChildItem "$env:USERPROFILE\Downloads\microclaw*.zip" | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if($null -eq $zip){throw 'No microclaw ZIP in Downloads'}; $dest=Join-Path $env:TEMP 'block58e-publiczip'; if(Test-Path $dest){Remove-Item $dest -Recurse -Force}; Expand-Archive -LiteralPath $zip.FullName -DestinationPath $dest; $installer=Get-ChildItem $dest -Filter install.bat -Recurse; if($installer.Count -ne 1){throw "Expected one install.bat, found $($installer.Count)"}; & $installer.FullName; if($LASTEXITCODE -ne 0){throw "public ZIP install failed: $LASTEXITCODE"}
```

Launch it once and allow its first background check to cache the private 404.
Then run:

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
.\design\58-block58e-demo-gate.ps1 -Mode PublicZip
```

The scorer requires `public-head`, installed commit `unknown`, and the cached
`repository is not public (404)` state. Finally reinstall from the clone:

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
.\install.bat
```

## Verify

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Verify
```

Verify preflights `phases.json`, names every exact command still owed, scores
independent falsifiable limbs with PASS/FAIL/NOT EXERCISED, writes its own
`gate.txt`, prints `BLOCK 58e DEMO GATE PASSED`, `FAILED`, or `INCOMPLETE`, and
exits nonzero for every non-pass.
