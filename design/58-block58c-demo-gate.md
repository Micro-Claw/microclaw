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
