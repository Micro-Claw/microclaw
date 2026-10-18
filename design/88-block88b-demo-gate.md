# 88b — demo-machine gate: package analysis on ND-TIFF MDA

Use **Windows PowerShell 5.1**, **Firefox**, and Micro-Manager open with the
usual demo config and bridge on port **4827**. Allow **20–30 minutes**;
measure takes about 5 minutes hands-off. No extra configuration or downloads:
prepare builds and installs both stdlib-only fixture packages offline, using
the installed slot's base Python.

Keep **MicroClaw closed except during session**. Prepare sets your package store
aside as `skill-packages.88b-saved`; verify restores it. On interruption use
cleanup below. Datasets stay under the workspace (or the launcher root if none),
in a `block88b-…` folder. Maximum imaging is 29,000 frames at 512 × 512 uint16,
about **15.2 GB**, with a 20% disk margin (about **18.2 GB**) checked first.
The refusal plans reserve an additional conservative 2,000 frames of disk,
so prepare checks about **19.5 GB** in total.
Datasets can be deleted after sending the evidence.

## 0. Install the branch

```powershell
cd D:\Code\microclaw
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
git checkout block-88b
if ($LASTEXITCODE -ne 0) { throw 'checkout failed' }
git pull
if ($LASTEXITCODE -ne 0) { throw 'pull failed' }
git merge-base --is-ancestor 03ab1de HEAD
if ($LASTEXITCODE -ne 0) { throw 'WRONG TREE - stop' }
.\install.bat
if ($LASTEXITCODE -ne 0) { throw 'install failed' }
```

Checking out the branch gives you the gate: **`install.bat` puts this branch in
the installed slot**. If installation pauses for bridge/setup, cancel with
Ctrl+C (Y); installation has completed. Close MicroClaw's console and launcher
windows. The update banner may say "diverged" until reinstalling from `main`.

## 1. Prepare (MicroClaw closed)

```powershell
.\design\88-block88b-demo-gate.ps1 -Phase prepare
```

Expect the operator-store backup, two offline installs
(`fixture-lab/photon-observer` and `weather-lab/thermal-observer`), six rotating
PLAN lines, the disk reservation and `RECORDED: prepare`. The launcher resolves
the installed slot's interpreter and checks MDA analysis support. Exit 2 means
**NOT EXERCISED**, never a pass. Keep the Evidence folder it prints.

## 2. Measure (MicroClaw closed, hands off)

```powershell
.\design\88-block88b-demo-gate.ps1 -Phase measure
```

**Do not click anything in Micro-Manager during measure. If a dialog appears,
leave it open, note its text and send the evidence.** Do not dismiss it or press
Ctrl+C to help the gate. A dialog during measure is a defect to preserve.

Six rotating repetitions of 1000-frame `none / photon` MDA bursts, interval 0,
10 ms, ND-TIFF; repetition 0 is warm-up. Then one thermal burst, two 2000-frame
photon MDAs stopped by the session cancel event at ≥200 frames, and TIFF and
feedback refusals that must start nothing. Each run prints its outcome, saved
frames and writer state, followed by its job ID and actual bound dataset.
The program waits up to the recorded terminal-job bound between runs, so a
late worker cannot contaminate the next measurement.

## 3. Session (Firefox, desktop icon)

```powershell
.\design\88-block88b-demo-gate.ps1 -Phase session
```

Follow the printed steps and **literal chat lines**; do not paraphrase them.
Typing STOP at a prompt abandons the phase.

1. Launch MicroClaw from its desktop icon. Put Firefox and PowerShell side by side.
2. Paste the printed R01 question about stopping a 12000-frame interval-0 MDA.
   The agent must run nothing. Wait for its reply, then type DONE. The program
   saves the reply verbatim from history in `r01.json`; it is reported, unscored.
3. Paste the printed 12000-frame `run_mda` line, with the actual photon digest.
   Approve. You have about two minutes: answer the question and click Stop
   promptly. While frames move and metrics are visible, type DONE. The program
   takes `working.png` and asks whether photon's block shows changing values
   and "up to frame" numbers. Answer YES or NO as you see it.
4. When instructed, click **Stop after the current step**, then type DONE.
   The program takes `stop.png`. Wait for MicroClaw's reply, then type DONE.
5. Quit MicroClaw by closing its console and launcher windows; type DONE.

## 4. Verify and restore (MicroClaw closed)

```powershell
.\design\88-block88b-demo-gate.ps1 -Phase verify
```

Expect **B1–B8 and S1–S2 PASS**, R01 REPORTED verbatim, MEASUREMENT lines and
CLEANUP confirming your operator store is restored. Any FAIL or NOT EXERCISED
exits nonzero. A package that never reported during acquisition or fell more
than 200 frames behind is NOT EXERCISED, decided from package-side history.
If the package reported during acquisition but MicroClaw forwarded no live
metrics before completion, B4 FAILs. Only the median product photon/none
`duration_breakdown.duration_s` ratio is bounded, at **1.10** over five scored
repetitions per arm. Gap p50/p95, dispatch, save-path receipt, metrics load/lag
and writer-to-terminal times are reported without bounds.

Verification reads captured files only; it does not reopen datasets or query
hardware. The optional final restore uses the captured store-ownership record.
The 87a metrics scorer receives a temporary file-derived view of MDA's nested
arguments/duration and uses MDA acquisition completion as its live boundary.
The synthetic selftest labels UI and timing controls explicitly; it is no
substitute for this demo-machine run.

For an interrupted restore:

```powershell
.\design\88-block88b-demo-gate.ps1 -Phase cleanup
```

## 5. Send the whole Evidence folder

Send the folder printed by the launcher, even on failure: `gate.log`, prepare
and measurement JSON, releases and test-policy snapshots, `runs\*` (raw inputs,
results, events and receipt times, confirmations, engine state, clocks, dataset
facts, terminal job records and waits, analysis-directory listings), diagnostic
JSONL, `session.json`, `session-history\*`, `session-final\*`, `r01.json`,
`working.png`, `stop.png`, and `verify.json`. Datasets remain on the machine.
