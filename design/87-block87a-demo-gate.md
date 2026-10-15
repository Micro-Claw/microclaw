# 87a — demo-machine gate: package metrics during acquisition

Use **Windows PowerShell 5.1**, **Firefox**, and Micro-Manager open with the
usual demo config and bridge on port **4827**. Allow **20–30 minutes**; measure
is hands-off for about 5–10 minutes. Prepare installs both stdlib-only fixtures
**offline**, using the installed slot's base Python. No extra configuration.

Prepare sets your package store aside as `skill-packages.87a-saved`; verify
restores it. On interruption run `-Phase cleanup`. Keep **MicroClaw closed for
prepare, measure and verify**. Datasets stay under the workspace (or the launcher
root if no workspace): **20 × 1000 frames plus 1200 session frames**, about
**11.2 GB at 512 × 512 uint16**, with a 20% disk margin checked before starting.

## 0. Install the branch

```powershell
cd D:\Code\microclaw
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
git checkout block-87a
if ($LASTEXITCODE -ne 0) { throw 'checkout failed' }
git pull
if ($LASTEXITCODE -ne 0) { throw 'pull failed' }
git merge-base --is-ancestor d792322 HEAD
if ($LASTEXITCODE -ne 0) { throw 'WRONG TREE - stop' }
.\install.bat
if ($LASTEXITCODE -ne 0) { throw 'install failed' }
```

Checking out the branch only gives you the gate script: **install.bat is what
puts this branch in the slot**. If the installer pauses for bridge/setup, cancel
with Ctrl+C (Y); the install has already completed. Then **close MicroClaw's
console and launcher windows**. The update banner will say "diverged" until you
reinstall from `main`; that is expected.

## 1. Prepare (MicroClaw closed)

```powershell
.\design\87-block87a-demo-gate.ps1 -Phase prepare
```

Expect `Operator store set aside`, two offline installs (photon publisher-a and
thermal publisher-b), six rotating `PLAN repetition` lines and `RECORDED:
prepare`. The launcher checks the installed slot for 87a; exit 2 means **NOT
EXERCISED**, not a gate pass.

## 2. Measure (MicroClaw closed, hands off)

```powershell
.\design\87-block87a-demo-gate.ps1 -Phase measure
```

Six rotating repetitions of `none / metrics / flood`, then thermal and broken
runs. Each is 1000 frames, interval 0, exposure 10 ms, through the product tool.
Repetition 0 is warm-up and excluded from scored comparisons. The broken
analysis is expected to fail; its acquisition must still save all 1000 frames.
Expect `RECORDED: measure`. Do not use the machine during measurement.

## 3. Session (Firefox, launch from the desktop icon)

```powershell
.\design\87-block87a-demo-gate.ps1 -Phase session
```

**Put Firefox and the PowerShell window side by side**, both visible: the
program screenshots the screen while the acquisition runs. Follow the printed
steps and literal chat lines. Step 1 names `run_timelapse`, 1200 frames
(about one minute), interval 0.05, exposure 10, and the photon adapter/digest/parameters.
Approve; type DONE **while the working line is visible and acquisition is still
running**. The program waits for live job evidence and captures `working.png`.
Answer its YES/NO question about changing flux/slow/total values (`load` is bytes per frame and stays constant). It then
prints `analysis_job_status` with the actual job ID; paste it, wait for the reply,
and type DONE. Finally quit MicroClaw and type DONE. STOP abandons a step.

## 4. Verify and cleanup (MicroClaw closed)

```powershell
.\design\87-block87a-demo-gate.ps1 -Phase verify
```

Expect all computed limbs PASS, the operator-judged verbatim YES/NO, the CPU/gap
MEASUREMENT and `CLEANUP` confirming your store is restored. Any FAIL or NOT
EXERCISED exits nonzero. Only metrics/none median product `duration_s` is bounded
at **1.10**; flood/none is reported without a performance threshold. Gap p50/p95/
max come from saved frame metadata through the earlier gate's dataset reader.

The diagnostic writer timestamps non-lifecycle progress. Browser sink payloads
have no timestamp: the gate records them unchanged and matches them to product
UTC diagnostic timestamps for the ≥1-second progress check. Missing or ambiguous
matches are NOT EXERCISED. Tool-result metrics are the last-known snapshot at
return; final status is checked separately after analysis finishes.

## 5. Send

Send the **whole Evidence folder printed by the launcher**, even on failure:
`gate.log`, phase JSON, per-run inputs/results/clocks/dataset summaries/browser
payloads/job records/status results, diagnostic JSONL, session history JSONL,
`working.png`, and `verify.json`. Datasets remain on the machine.

For an interrupted restore:

```powershell
.\design\87-block87a-demo-gate.ps1 -Phase cleanup
```
