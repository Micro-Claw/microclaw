# Block 58c demo gate — human mechanisms

Run these commands, unedited and in order, in Windows PowerShell 5.1 from the
`design58/two-slots` checkout. The program performs all backup, hashing,
installation, fixture-building, process inspection, and assertions. The human
only operates the desktop icon and Micro-Manager when prompted.

**Precondition, checked by the program before it touches anything:** this
machine already has a reviewed `%APPDATA%\microclaw\safety_config.yaml`. The
`Prepare` phase drives `install.bat` three times with piped input, and an
installer that finds no reviewed config opens a blocking browser-setup server
instead of finishing — the gate would hang rather than fail. `Prepare` refuses
with that message if the file is missing or does not classify `ready`.

Step zero is the literal backup command. It copies `%APPDATA%\microclaw` and the
legacy `%LOCALAPPDATA%\microclaw\env`, prints the backup path, hashes roaming
data, runs `install.bat` twice, and builds the real second slot and non-uv
control. It fails nonzero if any mechanism cannot run.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\design\58-block58c-demo-gate.ps1 -Mode Prepare
```

With Micro-Manager open and its pycro-manager bridge enabled, run the observer
below and follow its prompt to **double-click the desktop icon**. This captures
the real slot child's command line. Stop the server with Ctrl+C afterwards.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\design\58-block58c-demo-gate.ps1 -Mode Healthy
```

Close Micro-Manager completely. Run the observer and follow its prompt to
**double-click the desktop icon**. After the bridge refusal, press Enter in the
child console so it exits. The observer records nonce count, active slot, and
rollback state; do not launch the icon a second time.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\design\58-block58c-demo-gate.ps1 -Mode Closed
```

Run the rollback observer. It temporarily hides the inactive slot's installed
package while retaining its executable and valid metadata, so the child really
starts but cannot import and never writes health. Follow both prompts to
**double-click the desktop icon**: once for the failed candidate and once for
the next successful launch that reports rollback. Stop the final server with
Ctrl+C.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\design\58-block58c-demo-gate.ps1 -Mode Rollback
```

Finally run every independent computing limb. PASS, FAIL, and NOT EXERCISED are
distinct; NOT EXERCISED produces INCOMPLETE and a nonzero exit. The program owns
`gate.txt` and `results.json`. Send the entire printed evidence folder back.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\design\58-block58c-demo-gate.ps1 -Mode Verify
```

## If a phase fails: put the machine back

`Prepare` migrates `%LOCALAPPDATA%\microclaw\env` to `env-a` before it installs
anything, so a phase that fails after that point leaves the desktop icon
pointing at a path that no longer exists. **That is expected during a failed
gate and it is fully reversible** — the environment was moved, not rebuilt, and
`Prepare` copied it as well.

Run this to return the machine to exactly its pre-gate state. It runs unedited
and prints what it did.

```powershell
$root="$env:LOCALAPPDATA\microclaw"; if((Test-Path "$root\env-a") -and -not (Test-Path "$root\env")){Move-Item "$root\env-a" "$root\env"; Write-Host "restored: $root\env"} else {Write-Host "nothing to restore"}; foreach($f in 'active-slot.txt','pending-slot.txt','launch-health.txt','rollback-report.txt','launcher-protocol.txt','launcher.log','update-state.json','Microclaw.cmd','updater-launcher.ps1','58c-gate-evidence.txt'){if(Test-Path "$root\$f"){Remove-Item "$root\$f" -Force; Write-Host "removed: $f"}}; if(Test-Path "$root\env-b"){Remove-Item "$root\env-b" -Recurse -Force; Write-Host "removed: env-b"}; & "$root\env\Scripts\python.exe" -c "pass" 2>$null; if($LASTEXITCODE -eq 0){& "$root\env\Scripts\microclaw.exe" install-shortcut; Write-Host 'ICON RESTORED'} else {Write-Host 'ENVIRONMENT CANNOT START ITS PYTHON - the icon stays broken until the next install.bat run, which detects this and rebuilds the environment. Your settings in %APPDATA%\microclaw are untouched.'}
```

A gate must not leave production state pointing into its own evidence folder.
The demo machine's installed environment recorded
`home = ...\block5b-20260804-124610\fresh-appdata\uv\python\...` in its
`pyvenv.cfg` — an old gate redirected uv's Python install directory into a
throwaway fixture, the venv wrote that path down permanently, and the install
broke three weeks later when the fixture was deleted. This gate's `Verify` phase
checks the property directly: every slot's interpreter must start, and its
`sys.base_prefix` must exist and must not sit under the evidence directory.

The last step rewrites the desktop shortcut and its wrapper against the restored
environment — but only after proving that environment's Python can actually
start. A uv venv's `python.exe` is a trampoline onto a uv-managed CPython
elsewhere on disk, and when that base is replaced or pruned the file is still
there while every spawn fails with *"uv trampoline failed to spawn Python child
process"*. That is not damage this gate caused and it is not lost work: the next
`install.bat` run detects it and rebuilds the environment in place.
`%APPDATA%\microclaw` is never touched by any of this; the gate's own copy of it
is in the evidence folder under `backup\`.
