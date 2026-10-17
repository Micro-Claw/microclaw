# 88a — demo-machine gate: a supervised `run_mda`

Use **Windows PowerShell 5.1**, **Firefox**, and Micro-Manager open with the
usual demo config and the bridge on port **4827**. Allow **25 minutes**; step 1
is hands-off for about 5. No extra configuration.

Datasets go under the workspace (or the launcher root if there is none), in a
`block88a-<time>` folder. That is about **7 GB** of 512 × 512 frames. The disk
check reserves the planned maximum, about **16 GB**. The datasets can be deleted
after you send the evidence.

**Third run (2026-10-17).** Run 2 passed 15/15, but its artifacts and three probes
found defects: Micro-Manager's modal "Directory not found. Create it?" when a
save folder does not exist yet (MicroClaw now creates it first), a 30 s check on
freshly written "Separate image files" (now count and size only), and wording
that told you Stop would not stop an MDA. Start again at step 0: `install.bat`
must run again. Earlier runs' datasets can be deleted.

## 0. Install the branch

```powershell
cd D:\Code\microclaw
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
git checkout block-88a
if ($LASTEXITCODE -ne 0) { throw 'checkout failed' }
git pull
if ($LASTEXITCODE -ne 0) { throw 'pull failed' }
git merge-base --is-ancestor 16b1831 HEAD
if ($LASTEXITCODE -ne 0) { throw 'WRONG TREE - stop' }
.\install.bat
if ($LASTEXITCODE -ne 0) { throw 'install failed' }
```

Checking out the branch only gives you the gate script: **`install.bat` is what
puts this branch in the slot**. If the installer pauses for bridge/setup, cancel
with Ctrl+C (Y); the install has already completed. Then **close MicroClaw's
console and launcher windows**. The update banner will say "diverged" until you
reinstall from `main`; that is expected.

## 1. Measure (MicroClaw closed, hands off)

```powershell
.\design\88-block88a-demo-gate.ps1 -Phase measure
```

It runs, through MicroClaw's own tools: three 1000-frame MDA bursts and three
1000-frame `run_timelapse` bursts (alternating), one 1000-frame MDA saved as each
TIFF type ("Image stack file", then "Separate image files"), three 2000-frame MDAs
it stops partway with the Stop signal, and one it stops with a Ctrl-C it sends itself.
Do not press Ctrl-C yourself. Each run prints one line such as
`r0-mda: outcome=completed frames=1000 writer=proved`. The stopped runs say
`outcome=cancelled` with fewer frames. Expect `RECORDED: measure`.

**Do not click anything in Micro-Manager during measure.** If a dialog appears,
for example "Directory not found. Create it?", leave it open, note its text and
send the evidence. A dialog during measure is a defect this gate exists to catch.

Exit 2 means **NOT EXERCISED** (wrong install, MicroClaw running, no disk). That
is not a pass.

## 2. Session (Firefox)

```powershell
.\design\88-block88a-demo-gate.ps1 -Phase session
```

The program prints each step and its **literal line** to paste into MicroClaw.
Paste exactly that and approve when MicroClaw asks. In summary:

1. In Micro-Manager choose **Tools > Multi-Dimensional Acquisition...** and
   leave that window open. Launch MicroClaw from its desktop icon. Arrange Firefox, the
   Multi-Dimensional Acquisition window and PowerShell side by side.
2. A 1500-frame `run_mda`. Type DONE **while frames are still being acquired**;
   the program takes `dialog.png`. It then asks whether the MDA window's
   **Count** and **Name prefix** show the values it names. Answer YES or NO as
   you see it.
3. `export_session_script`.
4. A 6000-frame `run_mda` (about a minute). While frames are moving, click
   MicroClaw's **Stop after the current step** button, then type DONE at once.
   The program takes `stop.png` and asks whether Micro-Manager opened any window
   asking you to confirm the abort. Answer as you see it.
5. Quit MicroClaw (close its console and launcher windows).

Typing STOP at any prompt abandons the session.

## 3. Script (MicroClaw closed)

```powershell
.\design\88-block88a-demo-gate.ps1 -Phase script
```

Runs the script exported in step 2.3 with the installed interpreter. It takes
1500 frames on its own (about 20 s) and prints its envelope. Expect
`RECORDED: script (exit 0, new datasets ['gate88a-dialog_…'])`.

## 4. Verify

```powershell
.\design\88-block88a-demo-gate.ps1 -Phase verify
```

Expect 15 limbs PASS, then the MEASUREMENT lines. Those lines (frame-interval
spread of MDA vs `run_timelapse`, abort-to-return time, save-path timing,
reads of the growing dataset) are reported with **no pass bound** (agreed
2026-10-16). Any FAIL or NOT EXERCISED exits nonzero.

## 5. Send

Send the **whole Evidence folder** printed by the launcher, even on failure:
`gate.log`, `runs\*` (inputs, results, events, state, clocks, dataset facts,
growing reads), the diagnostics JSONL, `session.json`, `session-history\`,
`dialog.png`, `stop.png`, `gate88a-export.py`, `script-stdout.txt`,
`script-stderr.txt`, `script.json` and `verify.json`.
