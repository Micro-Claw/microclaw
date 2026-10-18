# 89a — demo-machine probes P3, P2, P1, P4

Use **Windows PowerShell 5.1** on the demo machine, with Micro-Manager open on
the usual demo config and the bridge on port **4827**. **Keep MicroClaw
closed.** The probes import nothing from MicroClaw; they use only the installed
slot's interpreter, so **no `install.bat`** is needed. Each probe runs unattended
and leaves Micro-Manager as it found it: the probed property, exposure, and the
debug-log setting are restored.

Each probe writes to its own `%LOCALAPPDATA%\probe89-<probe>-<time>` folder and
prints `exit <code>; report: <path>`. Exit 0 = every limb PASS, 1 = a FAIL,
2 = NOT EXERCISED. **Send the whole folder** for every run, whatever the exit
code. P1, P2 and P4 delete each run's image stacks once scored and keep the
index and timestamps; P3 keeps its small datasets.

## 0. Check out the branch

```powershell
cd D:\Code\microclaw
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
git checkout block-89a
if ($LASTEXITCODE -ne 0) { throw 'checkout failed' }
git pull
if ($LASTEXITCODE -ne 0) { throw 'pull failed' }
git merge-base --is-ancestor 90fdf18 HEAD
if ($LASTEXITCODE -ne 0) { throw 'WRONG TREE - stop' }
```

## 1. P3 — are per-event properties applied inside a burst? (~2 min)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File design\89-probe-p3.ps1
```

It writes `Camera` `TestProperty1` only (recorded on this machine by block
77b), after checking that it is writable and has limits. If that check fails,
P3 reports NOT EXERCISED and writes nothing. Three short runs: a plain burst, a
burst whose frames each carry a different value, and a spaced control. The
result line to look for is `verdict PASS CONFIRMED: applied …` or
`verdict FAIL fact 5 refuted: …`. Either is a result; send it.

## 2. P2 — stopping mid-burst (~4 min)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File design\89-probe-p2.ps1
```

Five 3,000-frame bursts, each aborted 10 s in, with `TestProperty1` written at
5 Hz until the abort.

## 3. P1 — does a concurrent property write change the frame cadence? (~12 min)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File design\89-probe-p1.ps1
```

Nine 5,000-frame, 10 ms bursts: no writes, 1 Hz, 5 Hz, three of each,
interleaved. **If the cadence limbs say `demo machine in R157 slow state`**,
the machine was slow during the run (`R157`). That is not a failure. Rerun P1
later, and send both folders.

## 4. P4 — does reading the index during a burst change the cadence? (~30 min)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File design\89-probe-p4.ps1
```

Bursts of 3,000 frames at 10 ms and at the fastest of 1/2/5 ms that the camera
allows. The arms are: no reads, index reads at 2/5/10 Hz, a worker following
the dataset, and a worker kept behind. Three of each, interleaved, and a final
10 ms control. The same R157 note applies.

## What to send

The four `%LOCALAPPDATA%\probe89-*` folders (plus any reruns), and the answer to
this question for the rig profile: **on M2, may the activation be changed while
it is emitting?** (M2 limbs of P1 and P4 are owed and not part of this run.)
