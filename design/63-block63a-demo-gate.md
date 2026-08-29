# Block 63a demo-machine gate — an ordinary session's script must still run

**What this gate is for.** `export_session_script` plants a `raise RuntimeError`
into any script that recorded a tool carrying no export decoration. Eleven tools
carried none — including `find_features`, `export_dataset_as_tiff` and
`snap_to_album`, which are ordinary in a session. Three previous blocks
discovered this the same way: on a rig, when the gate's own exported script died
on a line that had nothing to do with the block being gated.

So the criterion is not "the exporter works". It is that **a session which uses
these tools exports a script that runs**, and that the tools which genuinely
cannot be reproduced standalone say *why* instead of carrying a generic sentence.

**You drive one session. Everything else is a program.** The prompts below name
tools literally on purpose — this gate scores the exporter, not the agent's
judgement about which tool to reach for.

Estimated time: ~25 minutes, most of it the install and the session.

---

## Step 1 — check out the branch and install it

Open **PowerShell** and paste this whole block. It derives every path; nothing
is hardcoded to a drive letter. `Set-StrictMode` makes an unset variable a named
error instead of an empty expansion.

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Run this from anywhere inside your microclaw checkout.
$repo = (git rev-parse --show-toplevel)
if (-not $repo) { throw "Not inside a git checkout. cd into your microclaw clone first." }
$repo = $repo -replace '/', '\'
Write-Host "repo:      $repo"

$evidence = Join-Path $env:USERPROFILE "block63a-evidence"
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
Write-Host "evidence:  $evidence"

$work = Join-Path $env:USERPROFILE "block63a-work"
New-Item -ItemType Directory -Force -Path $work | Out-Null
Write-Host "work:      $work"

$gate = Join-Path $repo "design\63-block63a-demo-gate.py"
if (-not (Test-Path $gate)) { throw "Gate program not found at $gate" }
Write-Host "gate:      $gate"

cd $repo
git fetch origin
git checkout design63/decorate-the-eleven
git pull --ff-only
git log --oneline -1
```

Then install it the way this machine normally installs, and confirm the
implementation this runbook was written against is in what you checked out:

```powershell
.\install.bat
git merge-base --is-ancestor IMPLEMENTATION_COMMIT HEAD
if ($LASTEXITCODE -eq 0) { Write-Host "IMPLEMENTATION PRESENT" } else { Write-Host "WRONG COMMIT - stop and tell the coordinator" }
```

If it prints `WRONG COMMIT`, stop.

**`$work` must be inside your configured `workspace_dir`.** If it is not, change
`$work` above to a folder that is — the acquisition turns write there and
microclaw will refuse a path outside it. Nothing else in this gate needs any
configuration you do not already have.

---

## Step 2 — drive one session

Start microclaw the way you normally do (`microclaw serve`, or the desktop
icon). Connect to the demo configuration.

Paste each prompt **exactly as written**, one turn at a time, and let each turn
finish. Where a prompt contains `PASTE_WORK_PATH`, replace it with the `work:`
path Step 1 printed — do that substitution **before** you send the turn, and
check the path is really in the text you sent. A placeholder left in a literal
command is a step that does not run: 52c's strictest criterion produced no
evidence at all that way.

### Turn 1 — an acquisition and an offline export

> Run a one-frame timelapse at the current position, saving into
> `PASTE_WORK_PATH`, and tell me the dataset path it wrote. Then call
> `export_dataset_as_tiff` on that dataset, writing the TIFF to
> `PASTE_WORK_PATH\gate.tif`.

### Turn 2 — the two analysis-and-illumination tools

> Call `find_features` on the current field, then call
> `shutter_declared_illumination`. Report what each returned.

### Turn 3 — the two MMStudio tools

> Call `snap_to_album`. Then call `get_mda_settings`, and immediately after it
> call `run_mda`.

`run_mda` asks you to authorize MMStudio's MDA. **Approve it.** If you decline,
say so — the call is still recorded and the gate still scores it, but say which
you did.

### Turn 4 — the calibration tool

> Call `calibrate_stage_to_camera`.

**This one is expected to fail on the demo camera**, whose frames do not change
when the stage moves. That is fine and is not a gate failure: the tool being
*recorded* is what this gate needs, and a call that errored is recorded exactly
like one that did not. Note what it reported.

### Turn 5 — export the whole session

> Export this session as a standalone script to `PASTE_WORK_PATH\full.py`, and
> tell me the exact path you wrote and how many calls it emitted.

### Turn 6 — export a runnable subset

> Export this session again to `PASTE_WORK_PATH\runnable.py`, this time
> excluding the `calibrate_stage_to_camera`, `snap_to_album` and `run_mda`
> steps by their tool_use ids. Tell me the exact path you wrote.

Those three tools cannot be reproduced outside microclaw, so their steps are
*meant* to be loud refusals in the full script — which means the full script
cannot run to the end, by design. The subset is the one you run in Step 3.

---

## Step 3 — run the emitted script with microclaw closed

**Close microclaw.** Leave Micro-Manager and its bridge running — the script
needs the Core. Then:

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$slot = (Get-Content "$env:LOCALAPPDATA\microclaw\active-slot.txt" -Raw).Trim()
$py   = "$env:LOCALAPPDATA\microclaw\env-$slot\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "NO INSTALLED INTERPRETER AT $py - rerun install.bat" }

$runnable = Join-Path $work "runnable.py"
if (-not (Test-Path $runnable)) { throw "Turn 6 did not write $runnable" }
$log = Join-Path $evidence "standalone.txt"

cd $work
& $py $runnable *> $log
"EXIT=$LASTEXITCODE" | Out-File -Append -Encoding utf8 $log
Get-Content $log -Tail 20
```

The last line of `$log` must read `EXIT=0`. Record it either way — the gate
reads that line, and an empty capture proves nothing in either direction (43h
scored a 0-byte one as VOID rather than a pass).

---

## Step 4 — save the session and run the program

Close or save the session so its history is written, then:

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# microclaw writes its history into ITS OWN working directory as
# <timestamp>_microclaw_history.jsonl - wherever the shortcut or shell launched
# it from, NOT a fixed folder. Find it rather than assume it.
$roots = @($env:USERPROFILE, $repo, $work, $PWD.Path) | Select-Object -Unique
$candidates = $roots | ForEach-Object {
    Get-ChildItem -Path $_ -Filter "*_microclaw_history.jsonl" -Recurse -ErrorAction SilentlyContinue
} | Sort-Object LastWriteTime -Descending | Select-Object -First 5
if (-not $candidates) { throw "No *_microclaw_history.jsonl found under: $($roots -join ', ')" }
$candidates | Format-Table LastWriteTime, FullName -AutoSize
$history = $candidates | Select-Object -First 1
Write-Host "history:   $($history.FullName)"
Write-Host "written:   $($history.LastWriteTime)"
```

**Confirm that timestamp is your session just now.** If the newest one is older,
the session has not saved yet — save or close it and re-run this block. Picking
an older file would score somebody else's run, and every history limb below
would report on it without complaint.

Then run the gate with the **installed** interpreter, from outside the
repository — the program refuses to score a checkout, deliberately:

```powershell
$slot = (Get-Content "$env:LOCALAPPDATA\microclaw\active-slot.txt" -Raw).Trim()
$py   = "$env:LOCALAPPDATA\microclaw\env-$slot\Scripts\python.exe"
Write-Host "interpreter: $py"

cd $HOME
& $py $gate --output $evidence --history $history.FullName `
    --exported (Join-Path $work "full.py") `
    --standalone-log (Join-Path $evidence "standalone.txt")
Write-Host "gate exit code: $LASTEXITCODE"
```

Do **not** use bare `python` (this machine has no python on PATH — the Microsoft
Store alias answers and exits 9009) and do **not** use `uv run` (from outside
the repo microclaw is not importable; from inside it the gate correctly refuses
to score a checkout). Both were tried on 2026-08-29 and neither ran a gate.

The program writes `gate.txt` and `results.json` into `$evidence` and prints one
line per limb. **It owns its own log** — do not use `Start-Transcript`, which
does not capture a native child process's stdout and came back empty twice in
58a.

---

## Step 5 — what to send back

Send the whole `$evidence` folder (`gate.txt`, `results.json`,
`standalone.txt`), both exported scripts (`full.py`, `runnable.py`), and the
session transcript.

### What the program reports, so you can read its output

| limb | what it means |
| --- | --- |
| G1 | every registry tool carries exactly one export marker — the sweep, on the build you are running |
| G2 | each of the eleven carries the marker design/63 decided, not a blanket sweep |
| G3 | **the control**: your session actually reached some of the eleven, and at least one with a new emitter. Without this, G4 cannot fail |
| G4 | your full export compiles, is standalone, and refuses only the three tools that are supposed to refuse |
| G5 | every refusal in it says why *that* tool cannot be emitted — no generic sentence |
| G6 | the subset script ran standalone to `EXIT=0` with microclaw closed |

**NOT EXERCISED is never a pass**, and the gate exits nonzero on it. If G3–G6
come back NOT EXERCISED, the history, the script or the log did not reach the
program — check Step 4's paths rather than re-driving the session.

---

## What this gate does not measure, deliberately

- **The EMU pair.** `set_emu_laser_power_percentage` and
  `verify_emu_laser_power_calibration` need an EMU rig; the demo machine has no
  EMU, and inventing one proves nothing. They ship gated by the suite alone
  unless an M5 trip is available, and this is recorded rather than glossed.
- **`center_feature` and `run_multiposition_with_autofocus`.** `center_feature`
  needs a stage-to-camera calibration and real features, and the demo camera has
  neither — the same reason Turn 4 is expected to fail. Both are covered by
  tests that execute the emitted source against fakes, which is weaker evidence
  than a rig and is stated as such.
- **Whether the refused three *could* be emitted.** They refuse by decision, not
  by defect. G5 scores the reason, not the refusal.
