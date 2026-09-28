# 83e-3 — demo-machine gate: package analysis on live acquisitions

Use **PowerShell**, the real **Microclaw desktop icon**, and **Firefox**. Keep
**Micro-Manager open** with this machine's usual demo config and the bridge
running (Tools → Options, "Run server on port 4827"). About 20 minutes, one
Microclaw launch, seven short turns. The demo camera takes about 130 frames,
half of them when the exported script is run. The XY stage does not move: both
positions are the stage's current point. Prepare needs the network once, to
install the fixture's Python.

What it does to this machine, all undone by step 3: it creates
`%LOCALAPPDATA%\microclaw\skill-packages` (and refuses if one already exists),
puts **TEST-ONLY trust roots** there, and installs `fixture-lab/executable-fixture`
1.0.0. Datasets go to `block83e3-<stamp>` under this machine's workspace, or
under `%LOCALAPPDATA%\microclaw` if there is none. They stay afterwards. The
program scores serve's history, confirmation audit and acquisition log, the job
records, the datasets and the exported script. You only judge the panel line.

## 0. Install the branch

```powershell
cd D:\Code\microclaw
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
git checkout block-83e-3
if ($LASTEXITCODE -ne 0) { throw 'checkout failed' }
git pull
if ($LASTEXITCODE -ne 0) { throw 'pull failed' }
git merge-base --is-ancestor c4c7a2d HEAD
if ($LASTEXITCODE -ne 0) { throw 'WRONG TREE - stop' }
.\install.bat
if ($LASTEXITCODE -ne 0) { throw 'install failed' }
```

Checking out the branch only gives you the gate script: **install.bat is what
puts this branch in the slot**. If the installer pauses for bridge/setup, cancel
with Ctrl+C (Y); the install has already completed. Then close Microclaw's
console and launcher windows.

## 1. Prepare

```powershell
.\design\83-block83e3-demo-gate.ps1 -Phase prepare
```

Expect one `installed executable-fixture 1.0.0: …` line and `RECORDED: prepare`.

## 2. Session

```powershell
.\design\83-block83e3-demo-gate.ps1 -Phase session
```

It reads the stage position, then asks you to launch the **desktop icon**. Wait
for Firefox and type `DONE`. It then prints each chat message **with the digest,
paths and position already filled in**. Copy each one into Microclaw's message
box unchanged, and type `DONE` when the reply has finished.

| Turn | Call | Click |
|---|---|---|
| 1 | timelapse + analysis | **Decline** |
| 2 | same, new name | **Approve for this session** |
| 3 | turn 2 again | nothing (no banner) |
| 4 | turn 2 without analysis | nothing |
| 5 | two positions + analysis | nothing (no banner) |
| 6 | open **Community skill packages**, read only | **no button** inside it |
| 7 | export the script | nothing |

On turn 6, type the panel's sentence that mentions `run_mda`, exactly as
shown, or `NONE`. Then close the panel. If the agent asks whether to go ahead,
answer `yes, go ahead`. If a separate banner about the acquisition's size
appears, click **Approve**. Type `STOP` to abandon.

## 3. Verify, clean up, send back

Leave Microclaw **open** and run:

```powershell
.\design\83-block83e3-demo-gate.ps1 -Phase verify
```

It scores ten limbs and runs the exported script with Microclaw's own Python
against Micro-Manager. That repeats turns 2–5's acquisitions under
`<evidence>\export-run`. It **then** asks you to close Microclaw and removes the
store and the TEST-ONLY roots.

Expect `RESULT: 0 failed or not exercised limbs / 10; 1 operator-judged`:
- **Limb 6** (cadence) is a measurement, not a criterion. It prints the frame
  gaps with and without analysis side by side.
- **Limb 7** is `OPERATOR-JUDGED`: the coordinator reads what you typed.

**NOT EXERCISED is not a pass.** Send the whole printed evidence folder, plus
the newest `%LOCALAPPDATA%\microclaw\*_microclaw_history.jsonl`,
`*_microclaw_confirmations.jsonl` and `*_microclaw_acquisitions.jsonl`, even on
failure. If a phase fails, stop, run `verify` anyway (it still cleans up), and
send everything.
