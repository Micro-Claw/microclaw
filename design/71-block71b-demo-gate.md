# 71b/71c — demo-machine gate

Use **PowerShell**, the real **Microclaw desktop icon**, and **Firefox**.
Keep Micro-Manager open with its pycro-manager server enabled on port 4827.
The `.ps1` resolves the managed slot's `Scripts\python.exe`; reinstall/recovery
use the other slot so Windows can release the environment being moved.
Nothing needs node. The program records API JSON; you judge the panel/buttons.
Real installs and discovery need network on the demo machine.

## 0. Install the branch; close Microclaw afterward

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
git checkout block-71b
if ($LASTEXITCODE -ne 0) { throw 'checkout failed' }
git pull
if ($LASTEXITCODE -ne 0) { throw 'pull failed' }
git merge-base --is-ancestor 513e483 HEAD
if ($LASTEXITCODE -ne 0) { throw 'WRONG TREE - stop' }
.\install.bat
if ($LASTEXITCODE -ne 0) { throw 'install failed' }
```

`513e483` is 71c, the last product commit; the gate files came after it, so
amending them cannot invalidate this check. Checkout alone supplies
only the gate script: **install.bat puts this branch in the slot**.
Close Microclaw's console and launcher before prepare. Leave Micro-Manager open.

```powershell
.\design\71-block71b-demo-gate.ps1 -Phase prepare -Fresh
```

Expect `RECORDED: prepare`, `PASS: 71c absent disclosure`, and `RESULT: 0 ... / 1`.
The program prints the evidence and local safety-backup paths. `-Fresh` first
backs up `%APPDATA%\microclaw`, launcher-root files, slot markers and extension
records, then uninstalls **h5py only** from both slots and clears its record. It
does not delete environments. It captures both slot probes and the absent-skill
text, arranges `origin/main~1` as the recorded installed commit, and makes the
launch check due. This is intentional test state, not an actual downgrade.

The real candidate is **origin/main**, which may predate this branch. That is
why both 71c observations happen before restart. The gate records the candidate
actually offered, not a guessed commit. Each later command reuses the evidence
folder automatically; do not run prepare again into that folder.

## 1. Install from the panel

Launch **Microclaw** from its desktop icon. In Firefox, wait for the update
banner, open **Extensions**, install **ilastik**, and wait for **Ready**.
Then run and type `DONE` when prompted:

```powershell
.\design\71-block71b-demo-gate.ps1 -Phase installed
```

Expect three PASS lines: active-slot import, record, and exact before/after skill
diff (the prepare phase already checked the absent half). The original file is
always retained. Do not stage until these pass.

## 2. Stage, then restart

Start this command **before pressing Update**, so it clears old build verdicts:

```powershell
.\design\71-block71b-demo-gate.ps1 -Phase staged
```

When prompted, press **Update** in Firefox. Wait for the restart controls; type
`DONE` in PowerShell. Expect two PASS lines: the initially h5py-absent inactive
slot now imports it, and pending/state/extension errors agree. If discovery did
not produce a candidate, use the app's **Check now**, then retry this phase.

```powershell
.\design\71-block71b-demo-gate.ps1 -Phase restarted
```

Press **Restart now** in Firefox when the program tells you. It waits first for
the new launcher line, then that launch's own health nonce. Expect three PASS
lines: flipped slot and candidate marker, ready extension, surviving record.

## 3. Ordinary reinstall

```powershell
.\design\71-block71b-demo-gate.ps1 -Phase reinstalled
```

Follow its prompt: close Microclaw and its launcher; in a **second PowerShell**:

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
.\install.bat
```

If the installer pauses for bridge/setup, cancel at that prompt with Ctrl+C
(and Y if asked); the managed install has already completed. A nonzero exit
from that deliberate cancellation is expected. If it instead prints **Install
failed**, type `STOP` in the gate and send evidence. No configuration field is
required by the gate. Launch the desktop icon again, confirm ilastik
is Ready in Firefox, then type `DONE` in the gate. Expect one PASS line: readiness
survived. The environment's before/after `pyvenv.cfg` timestamps are reported
as observations; changing that timestamp does not fail the limb.

## 4. Recovery — keep the gate running while following its prompts

```powershell
.\design\71-block71b-demo-gate.ps1 -Phase recovery
```

The program asks you to close Microclaw/launcher, snapshots recovery state, and
**renames the active environment inside `%LOCALAPPDATA%\microclaw`** to a
`71b-preserved-...` directory. No environment is copied to Documents.

When prompted, run the same `install.bat` command from step 3 in the second
window. **Do not launch Microclaw yet. If the installer asks to press a key for
bridge/setup, leave it paused and type DONE in the gate first.** This lets the
program prove h5py is absent before any server is launched. Then cancel that
paused installer with Ctrl+C (Y if asked), as the next gate prompt instructs.

Launch the desktop icon. In Firefox Extensions, confirm **recorded but missing**
and a recovery button. Type that button's exact label at the gate prompt,
without pressing it yet. The gate records your answer verbatim alongside the
missing-state JSON and checks that you typed `Reinstall`. At its next prompt, press **Reinstall**, wait for
**Ready**, and type `DONE`. Expect three PASS lines: genuine absence, the missing
panel, and recovered readiness plus deletion of the preserved environment.

On failure the program stops replacement servers/launcher, moves the failed
environment aside, restores the preserved environment and pre-recovery files,
and verifies a fresh launcher health nonce. **Do not manually delete preserved
or failed directories.** If restoration failed or the program was interrupted,
keep the evidence folder and retry with this one command:

```powershell
.\design\71-block71b-demo-gate.ps1 -Phase restore
```

It prints the retained environment's path on another failure. Deletion occurs
only after recovery is verified. If deletion itself failed part-way, `restore`
rechecks the working replacement and retries cleanup; it never restores the
partly deleted directory. The local safety backup is retained in all cases.

## 5. Verify and send back

```powershell
$gateEvidence = (Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA 'microclaw\71b-gate-evidence.txt') -Raw).Trim()
.\design\71-block71b-demo-gate.ps1 -Phase verify -Out $gateEvidence
```

Expect **13 PASS lines**, `GATE CLEANUP COMPLETE`, and
`RESULT: 0 failed or not exercised limbs / 13`. Verify reports and removes the
launcher root's `71b-recovery-files-...` directory and `71b-gate-evidence.txt`
(pointer last). Recovery snapshots remain at the reported local path **outside**
the launcher root, with the journal updated so restoration remains possible.
Retained `71b-preserved-*` environments are not removed by verify.

After verify, the pointer is gone. In this same PowerShell window, a restoration
retry uses the evidence path retained above:

```powershell
.\design\71-block71b-demo-gate.ps1 -Phase restore -Out $gateEvidence
```

In a new window, supply the printed evidence-folder path with `-Out`.

Every limb is independent. **NOT EXERCISED is not a pass**; any failure or missing
mechanism exits nonzero. Stop on a failed phase, then run verify and send the
**whole printed evidence folder**, even on failure. It contains `gate.log`,
`under-test.json`, per-phase raw observations, slot-probe payloads, the fixture,
results and (after recovery) its restoration journal. Backups stay local because
roaming data can contain credentials; send them only if specifically requested.
