# 83e-4 — demo-machine gate: does a loaded analysis worker disturb acquisition?

Use **PowerShell**. Keep **Micro-Manager open** with this machine's usual demo
config and the bridge running (Tools → Options, "Run server on port 4827").
**Keep MicroClaw closed** the whole time: the program refuses to start while
anything listens on port 8000. No chat, no browser, no clicks. Running the
program is your consent to the analysis and acquisition-size confirmations it
answers. Prepare needs the network once, to install the fixture's Python.

The run is 42 timelapses, 23,100 frames: about 12 GB at 512 × 512, 16-bit, and
it wants 20% more than that free. Prepare and run refuse, printing both numbers,
if there is less.
It creates `%LOCALAPPDATA%\microclaw\skill-packages` (refusing if one exists)
with **TEST-ONLY trust roots** and the fixture package; step 3 removes both.
Datasets go to `block83e4-<stamp>` under this machine's workspace, or under
`%LOCALAPPDATA%\microclaw` if there is none, and stay afterwards.

## 0. Install the branch

```powershell
cd D:\Code\microclaw
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
git checkout block-83e-4
if ($LASTEXITCODE -ne 0) { throw 'checkout failed' }
git pull
if ($LASTEXITCODE -ne 0) { throw 'pull failed' }
git merge-base --is-ancestor 7c43abc HEAD
if ($LASTEXITCODE -ne 0) { throw 'WRONG TREE - stop' }
.\install.bat
if ($LASTEXITCODE -ne 0) { throw 'install failed' }
```

Checking out the branch only gives you the gate script: **install.bat is what
puts this branch in the slot**. If the installer pauses for bridge/setup, cancel
with Ctrl+C (Y); the install has already completed. Then close MicroClaw's
console and launcher windows.

## 1. Prepare

```powershell
.\design\83-block83e4-demo-gate.ps1 -Phase prepare
```

Expect a `Disk:` line, one `installed executable-fixture 1.0.0: …` line, seven
`PLAN repetition` lines and `RECORDED: prepare`.

## 2. Run

```powershell
.\design\83-block83e4-demo-gate.ps1 -Phase run
```

Estimated 15 minutes, not measured; `RUN k/42` counts up. Leave the machine alone: no other
programs, no Micro-Manager clicks. Expect `RECORDED: run`. If it stops early,
go to step 3 anyway.

## 3. Verify

```powershell
.\design\83-block83e4-demo-gate.ps1 -Phase verify
```

Expect eight `limb` lines, a measurement table, a `CLEANUP:` line and a last
line `RESULT: <k> failed or not exercised limbs / 8; measurement printed above`.
**NOT EXERCISED is never a pass.** The table is not pass/fail.

## 4. Send

Send the whole folder printed after **Evidence:**, even after a failure. It
holds everything needed to re-score off-rig; the datasets stay where they are.
