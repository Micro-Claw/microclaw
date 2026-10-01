# 83e-5 — demo gate: supervisor priority versus a normal-priority control

Use **Windows PowerShell 5.1**. Keep **Micro-Manager open** with this machine's usual demo
config and the bridge running (Tools → Options, "Run server on port 4827").
**Keep MicroClaw closed** the whole time: the program refuses to start while
anything listens on port 8000. No chat, no browser, no clicks. Running the
program is your consent to the analysis and acquisition-size confirmations it
answers. Prepare needs the network once, to install the fixture's Python.

The run is **13 burst timelapses, 13,000 frames**: 1000 frames per run,
`interval_s=0`, exposure 10 ms. About **7 GB** at 512 × 512, 16-bit;
the disk check requires 20% more (about 8.2 GB). Prepare and run refuse, printing both numbers,
if there is less.
It creates `%LOCALAPPDATA%\microclaw\skill-packages` (refusing if one exists)
with **TEST-ONLY trust roots** and the fixture package; step 3 removes both.
The evidence pointer is `%LOCALAPPDATA%\microclaw\83e5-gate-evidence.txt`.
Datasets go to `block83e5-<stamp>` under this machine's workspace, or under
`%LOCALAPPDATA%\microclaw` if there is none, and stay afterwards.

## 0. Install the branch

```powershell
cd D:\Code\microclaw
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
git checkout block-83e-5
if ($LASTEXITCODE -ne 0) { throw 'checkout failed' }
git pull
if ($LASTEXITCODE -ne 0) { throw 'pull failed' }
git merge-base --is-ancestor c6f3a56 HEAD
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
.\design\83-block83e5-demo-gate.ps1 -Phase prepare
```

Expect a `Disk:` line, one `installed executable-fixture 1.0.0: …` line, five
`PLAN repetition` lines (one none warm-up, then four measured repetitions) and `RECORDED: prepare`.

## 2. Run

```powershell
.\design\83-block83e5-demo-gate.ps1 -Phase run
```

Estimated **5–8 minutes**, not measured; `RUN k/13` counts up. Leave the machine alone: no other
programs, no Micro-Manager clicks. Expect `RECORDED: run`. If it stops early,
go to step 3 anyway.

## 3. Verify

```powershell
.\design\83-block83e5-demo-gate.ps1 -Phase verify
```

Expect eight `limb` lines, a measurement table, a `CLEANUP:` line and a last
line `RESULT: <k> failed or not exercised limbs / 8; measurement printed above`.
**NOT EXERCISED is never a pass.** The table is not pass/fail.

There is **one warm-up total**, a `none` burst, excluded from statistics. Each
measured repetition visits `none`, `loaded` (default `priority: inherit`, the
supervisor's class), and `normal` (loaded with fixture `priority: normal`). The
cyclic Latin orders are none/loaded/normal, loaded/normal/none,
normal/none/loaded, none/loaded/normal. They balance position over a complete
three-repetition cycle; the fourth repeats the first. They are **not
carry-over balanced** (a three-cell Williams design needs six sequences).

Limb 5 reads the **job record and the fixture**. On Windows, each supervisor
sample covers the processes in the worker's Job Object (including venv launchers
and their interpreter children) and records the highest class by scheduling
rank. Loaded runs require requested = at_start = at_end = fixture read_back =
0x4000, unless the job inherited MicroClaw's class; then both samples must match
that inherited class, which is reported without a below-normal failure. Normal
controls require supervisor requested = 0x4000, fixture applied = true and
at_end = fixture read_back = 0x20. None runs must have no job. Every run's
`processes_read`, at_end and reason are reported. A missing at_end **fails limb
5 for either loaded cell**, even with a reason: the control must demonstrate
the raise, and the default run must demonstrate that its class stayed lowered.
The product still treats the read as best-effort and does not fail the analysis
job. An exited job with no readable processes records null, zero processes read,
and a reason; a truncated list or vanished pid is reported in the reason.
Limb 7 requires the below-normal CPU priority disclosure; the slot probe refuses
an older installed product with NOT EXERCISED.

All measured per-run durations, mean gaps and p95 gaps are printed. Metadata
gaps use the same window for all cells, starting at the latest loaded-start
frame; p95 is nearest rank. The control-versus-loaded separation rule is
strict: all four values in one cell lie beyond all four in the other (ties do
not separate; chance level 2/70 ≈ 2.9%). Unqualified loaded runs remain listed,
marked and counted. Loaded-versus-none is reported as ranges, **with no equality
claim**. This n=4 demo-machine observation is not a property of rigs: the demo
camera makes frames in software.

## 4. Send

Send the whole folder printed after **Evidence:**, even after a failure. It
holds everything needed to re-score off-rig; the datasets stay where they are.
