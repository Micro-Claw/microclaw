# Block 58e demo gate — restart and end-to-end update

Run these commands unedited and in order in Windows PowerShell 5.1. Send the
whole printed evidence directory back. The safety backup is deliberately its
sibling, not inside the evidence sent for review.

The branch is unmerged. `Prepare` backs up both production roots, records the
branch slot, and temporarily changes only `installed_commit` to the real
`origin/main~1`; otherwise discovery correctly reports `diverged`. Staging then
builds real `origin/main`, which does not contain 58e. That is expected: the
branch slot requests the restart and the external launcher performs it.

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

The program records the process command line and cached `automatic_restart`.
Stop the direct server with Ctrl+C. There must be no Restart-now control; that
absence is computed from `automatic_restart: false`, not hand-entered.

## Desktop staging, one-job refusal, progress and comparison

Double-click the desktop icon. With the browser open, run:

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
.\design\58-block58e-demo-gate.ps1 -Mode Stage
```

The program posts Stage twice while the first job owns the build, polls cached
status through building and ready/refused states, and invokes both real slot
CLIs against the shared config from a working directory outside the checkout.
It records the 202/409 responses and both config classifications. Active files
remain locked throughout.

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

For the ordinary-exit control, stop the relaunched desktop server with Ctrl+C.
The console must show `Press Enter to close this window...`; press Enter.

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Ordinary
```

## Restart later

Launch from the desktop, stage once, click **Restart later**, then stop the
server normally. Record the selector, double-click the desktop icon once, and
run Restart again. The selector must change only at that next launch. The gate
scores the launch lines and selector transition rather than a checkbox.

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Later
```

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
$root="$env:LOCALAPPDATA\microclaw"; $active=(Get-Content "$root\active-slot.txt" -Raw).Trim(); $failed=if($active -eq 'a'){'b'}else{'a'}; $py="$root\env-$failed\Scripts\python.exe"; $pkg=(& $py -I -c "import pathlib,microclaw; print(pathlib.Path(microclaw.__file__).parent)").Trim(); if($LASTEXITCODE -ne 0 -or -not (Test-Path $pkg)){throw 'inactive package is not runnable'}; $hidden="$pkg.58e-gate-hidden"; Move-Item -LiteralPath $pkg -Destination $hidden; Set-Content -LiteralPath "$root\pending-slot.txt" -Value $failed -Encoding ASCII; Write-Host "hidden: $pkg"; Write-Host "restore with: Move-Item -LiteralPath '$hidden' -Destination '$pkg'"
```

Double-click the icon once. After the failing launch exits, restore the package
with the printed literal command, then launch the icon once more. The returned
launcher log and rollback report must show the report only on this next healthy
launch.

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Rollback
```

## Offline and Micro-Manager closed

Disconnect the network and close Micro-Manager. Double-click the desktop icon
once and wait for the bridge refusal, then press Enter. Do not launch twice.
Capture the state:

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Closed
```

The scorer requires the launcher's nonce-matched health marker, the new slot
kept, no rollback report, and no second launch line. Reconnect the network.

## Restore — even after a failed phase

Always run Restore. It restores the original `installed_commit` and active slot,
removes pending state, and records the after hash of `%APPDATA%\microclaw` for
comparison with Prepare.

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Restore
```

## Public ZIP last

Public ZIP replaces the managed install, so it goes last. Extract a fresh
GitHub **Download ZIP**, run its `install.bat`, and allow its first background
check to cache the private-repository 404. Then run:

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
