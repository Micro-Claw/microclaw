# 83e-2 — demo-machine gate: the analysis prompt, its session grant, and export

Use **PowerShell**, the real **Microclaw desktop icon**, and **Firefox**. Keep
Micro-Manager open. About 20 minutes, one Microclaw launch, six short chat turns
(a few cents of API use). It needs network once, to install the fixture's Python.

What it does to this machine, all undone by step 3: it creates
`%LOCALAPPDATA%\microclaw\skill-packages` (refusing if one already exists), puts
**TEST-ONLY trust roots** there, and installs two releases of
`fixture-lab/executable-fixture`. The program scores serve's confirmation audit,
the job records, the worker's output files and the exported script; you judge
what the banner shows.

## 0. Install the branch

```powershell
cd D:\Code\microclaw
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
git checkout block-83e-2
if ($LASTEXITCODE -ne 0) { throw 'checkout failed' }
git pull
if ($LASTEXITCODE -ne 0) { throw 'pull failed' }
git merge-base --is-ancestor 632536e HEAD
if ($LASTEXITCODE -ne 0) { throw 'WRONG TREE - stop' }
.\install.bat
if ($LASTEXITCODE -ne 0) { throw 'install failed' }
```

Checkout alone supplies only the gate script: **install.bat puts this branch in
the slot**. If the installer pauses for bridge/setup, cancel with Ctrl+C (Y); the
install has already completed. Close Microclaw's console and launcher windows.

## 1. Prepare

```powershell
.\design\83-block83e2-demo-gate.ps1 -Phase prepare
```

Expect two `installed executable-fixture …` lines and `RECORDED: prepare`.

## 2. Session

```powershell
.\design\83-block83e2-demo-gate.ps1 -Phase session
```

When told, launch the **desktop icon** and type `DONE`. The program then prints
each chat message to send, **with the digest and paths already filled in**:
copy each one into Microclaw's message box unchanged. If the agent asks whether
to go ahead, answer `yes, go ahead`. It tells you which button to click:

| Turn | Release | Expect | Click |
|---|---|---|---|
| 1 | 1.0.0 | banner | **Approve for this session** |
| 2 | 1.0.0 | **no** banner | nothing |
| 3 | 1.1.0 | banner (another release) | **Decline** |
| — | | | **Revoke** on the analysis chip, top right |
| 4 | 1.0.0 | banner again | **Approve** (the plain one) |
| 5 | job status | — | — |
| 6 | export the script | — | — |

After each turn it asks what you saw. Answer `YES`/`NO`, or type the words it
asks for. Type `STOP` to abandon.

## 3. Verify, clean up, send back

```powershell
.\design\83-block83e2-demo-gate.ps1 -Phase verify
```

This scores seven limbs, **then** asks you to close Microclaw, and then removes
the store and the TEST-ONLY roots. The export limb runs the exported script with
Microclaw's own Python: that script connects to Micro-Manager and does nothing
else. Expect **7 PASS lines** and `RESULT: 0 failed or not exercised limbs / 7`.
Send the whole printed evidence folder, plus the newest
`%LOCALAPPDATA%\microclaw\*_microclaw_confirmations.jsonl` and
`*_microclaw_history.jsonl`, even on failure. **NOT EXERCISED is not a pass.**
If a phase fails, stop, run `verify` anyway (it still cleans up), and send
everything.
