# 83f-6 — Windows panel install of pack-filled scientific locks

Use **Windows PowerShell 5.1** and **Firefox**. Internet is needed for **PyPI**
and **raw.githubusercontent.com**. Expected time: **about 15 minutes**.
Micro-Manager does not need to be open.

## 0. Install the branch

```powershell
cd D:\Code\microclaw
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
git checkout block-83f-6
if ($LASTEXITCODE -ne 0) { throw 'checkout failed' }
git pull
if ($LASTEXITCODE -ne 0) { throw 'pull failed' }
git merge-base --is-ancestor 8f9cb3f HEAD
if ($LASTEXITCODE -ne 0) { throw 'WRONG TREE - stop' }
.\install.bat
if ($LASTEXITCODE -ne 0) { throw 'install failed' }
```

`install.bat` puts this branch into the managed slot. If the installer pauses
for bridge or setup, cancel with Ctrl+C (Y); installation has already completed.
Close MicroClaw's console and launcher windows.

## 1. Cleanup and prepare

```powershell
.\design\83-block83f6-demo-gate.ps1 -Phase cleanup
.\design\83-block83f6-demo-gate.ps1 -Phase prepare
```

This machine already has **your own package store** (83f-5 kept the
`session-start` example installed). The gate never deletes it. Cleanup leaves it
untouched. Prepare asks you to close MicroClaw, then **renames it** to
`%LOCALAPPDATA%\microclaw\skill-packages.83f6-saved` and writes TEST-ONLY roots
in a fresh store. Step 3's cleanup removes the test store and renames yours
back. If a run stops partway, run `-Phase cleanup` again; it finishes the
restore.

Expect `Your package store was set aside`, `Prepared TEST-ONLY roots only` and
`RECORDED: prepare`. **If prepare does not print `RECORDED: prepare`, stop
there** and send the folder printed after `Evidence:`. The pointer is
`%LOCALAPPDATA%\microclaw\83f6-gate-evidence.txt`.

## 2. Session

```powershell
.\design\83-block83f6-demo-gate.ps1 -Phase session
```

Launch the **desktop icon**, wait for Firefox, and type `DONE` in PowerShell.
There is one panel step: open **Community skill packages**, click **Install**
on **fixture-lab/lock-fixture**, then **Install** in the box. Wait until the row
shows installed, then type `DONE` in PowerShell. Several minutes is normal;
scipy is about 37 MB. The gate snapshots the store. Nothing goes in chat.
`DONE` is the only accepted completion; `STOP` abandons the step.

## 3. Verify and cleanup

Leave MicroClaw open and run this even if the session failed or stopped:

```powershell
.\design\83-block83f6-demo-gate.ps1 -Phase verify
```

Expect `RESULT: 0 failed or not exercised limbs / 5; 0 operator-judged`.
The five independent limbs read artifacts: successful raw catalog fetch,
active ready release and committed digest, index route (`find_links` null),
fresh distributions exactly equal to the four pack-filled Windows pins, and
imports of numpy/scipy/h5py/tifffile at those versions. **NOT EXERCISED is never
a pass.** The install timing is reported with n=1, not scored; it measures
`install.json` creation to its preserved ready-record write, excluding download.

After scoring, follow the prompt to close MicroClaw and type `DONE` in
PowerShell. Cleanup removes the TEST-ONLY roots first, then the test store and
pointer, and puts your own store back; expect `Your package store is back`. Send the **whole folder printed after Evidence:**, including after a
failure. It contains the snapshot, fresh probe, import stdout/stderr, timings
and per-limb verdicts. Evidence stays in that folder; do not commit gate results.
