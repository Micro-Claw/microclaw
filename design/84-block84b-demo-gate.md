# 84b — demo-machine gate: a package window, its Close button, and burst cadence

Use **Windows PowerShell 5.1** and **Firefox**. Keep **Micro-Manager open** with
this machine's usual demo config and the bridge running (Tools → Options, "Run
server on port 4827"). Expected time: **about 25 minutes**, of which about 10 are
hands-off. Prepare needs the network once, to install the fixture's Python.

The gate sets **your own package store** aside
(`%LOCALAPPDATA%\microclaw\skill-packages` → `skill-packages.84b-saved`), installs
a TEST-ONLY fixture package, and puts your store back in step 5. If a run stops
partway, run `-Phase cleanup`; it finishes the restore. Datasets (12 × 1000
frames, about 6 GB at 512 × 512) go to `block84b-<stamp>` under this machine's
workspace, or under `%LOCALAPPDATA%\microclaw`, and stay afterwards.

## 0. Install the branch

```powershell
cd D:\Code\microclaw
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
git checkout design-84-package-windows
if ($LASTEXITCODE -ne 0) { throw 'checkout failed' }
git pull
if ($LASTEXITCODE -ne 0) { throw 'pull failed' }
git merge-base --is-ancestor 7781f49 HEAD
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
.\design\84-block84b-demo-gate.ps1 -Phase prepare
```

Expect `Operator store set aside`, a `Tk probe:` line whose `exit_code` is `0`,
six `PLAN repetition` lines and `RECORDED: prepare`. **If the Tk probe's
exit_code is not 0, continue anyway**: the gate reports the window limbs NOT
EXERCISED, which is the finding.

## 2. Measure (MicroClaw closed, hands off)

```powershell
.\design\84-block84b-demo-gate.ps1 -Phase measure
```

Twelve 1000-frame bursts, half of them with the fixture window open. A small
window titled **MicroClaw fixture window** appears and disappears six times;
**do not click it or close it**, and do not use the machine. Expect
`RECORDED: measure`.

## 3. Session (you drive MicroClaw)

```powershell
.\design\84-block84b-demo-gate.ps1 -Phase session
```

The program prints six numbered steps and waits for you to type `DONE` after
each one (`STOP` abandons). It takes its own screenshots, so you don't need to.
The steps are: launch MicroClaw from the desktop icon; paste the chat line it
prints and approve; answer YES or NO to "did the window appear?"; open
**Community skill packages**; click **Close** on the window row; paste the
second chat line and approve; then quit MicroClaw by closing its console and
launcher windows. **Keep the fixture window uncovered** when you type DONE after
step 2. No confirmation appears when you click Close. That is intended.

## 4. Verify and cleanup (MicroClaw closed)

```powershell
.\design\84-block84b-demo-gate.ps1 -Phase verify
```

Expect `RESULT: 0 computed FAIL/NOT EXERCISED limbs / 9`, a `9 operator-judged`
line carrying your answer, a MEASUREMENT block and `CLEANUP:` saying your store
was restored. **NOT EXERCISED is never a pass.** Limb 10 scores the agreed
bounds: median burst with a window ≤ 1.10 × without, and the window's lag during
the burst p95 ≤ 100 ms, max ≤ 1 s.

## 5. Send

Send the whole folder printed after **Evidence:**, even after a failure. It
holds the screenshots and everything needed to re-score off-rig. The datasets
stay where they are.
