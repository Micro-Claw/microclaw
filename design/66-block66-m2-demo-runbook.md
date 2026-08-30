# Block 66 M2/demo rig gate

Every command below is literal PowerShell. You set six variables in Step 0 and
Step 2; nothing else needs editing, and a command that runs unedited either
works or fails loudly.

The operator owns the decision whether disconnecting or powering down the TIRF
controller is safe; if it is not, record the control as NOT EXERCISED and do not
report the M2 gate as passed. Do not add any tolerance setting for this gate —
the setup-generated safety config must stay unchanged.

## Step 0 — the checkout, the interpreter, and the evidence directory

Check the branch out on the rig and `cd` into it, then:

```powershell
$REPO = (& git rev-parse --show-toplevel)
git merge-base --is-ancestor 47331a4 HEAD
if ($LASTEXITCODE -ne 0) { throw "Block 66 implementation is not present" }
```

The exported script imports `pycromanager` and opens its own `Core()`, so the
interpreter is the one the Microclaw install runs on: pycromanager is a core
dependency, so the venv that runs Microclaw is by definition the one that can
import it. Resolve it from the **active slot** — never hardcode `env-a`, which
is the other build on a machine that has updated:

```powershell
$root = "$env:LOCALAPPDATA\microclaw"
$PYTHON = $null
if (Test-Path "$root\active-slot.txt") {
    $slot = (Get-Content "$root\active-slot.txt" -Raw).Trim()
    $PYTHON = "$root\env-$slot\Scripts\python.exe"
} elseif (Test-Path "$root\env\Scripts\python.exe") {
    $PYTHON = "$root\env\Scripts\python.exe"
}
if (-not $PYTHON -or -not (Test-Path $PYTHON)) {
    throw "no installed microclaw interpreter found; if you run Microclaw from a git checkout, use that checkout's .venv\Scripts\python.exe"
}
& $PYTHON -c "import pycromanager, microclaw, sys; print(sys.executable); print(pycromanager.__file__)"
if ($LASTEXITCODE -ne 0) { throw "this interpreter cannot import pycromanager" }
Write-Host "PYTHON = $PYTHON"
```

The import line is the test, not `Test-Path`: a slot's `python.exe` is a
trampoline onto a uv-managed CPython elsewhere on the disk, and a trampoline
whose target is gone is still a file.

**Set the one path you choose.** Everything else derives from it:

```powershell
$EVIDENCE = "$env:USERPROFILE\Documents\block66-m2"   # <-- edit this line only
New-Item -ItemType Directory -Force -Path $EVIDENCE | Out-Null
$SHEET   = Join-Path $EVIDENCE "capture-sheet.ps1"
$CAPTURE = Join-Path $EVIDENCE "standalone-capture.txt"
$M2LOG   = Join-Path $EVIDENCE "m2-success.log"
$CTLLOG  = Join-Path $EVIDENCE "m2-control.log"
$DEMOLOG = Join-Path $EVIDENCE "demo.log"
Write-Host "evidence: $EVIDENCE"
```

**Prove the instrument still discriminates before spending any dose.** One
second, no hardware:

```powershell
& $PYTHON (Join-Path $REPO "design\66-stage-move-gate-selftest.py")
if ($LASTEXITCODE -ne 0) { throw "gate self-test failed; do not run the gate" }
```

## Step 1 — the M2 success limb, in Microclaw

1. Call `get_stage_position` for `TIRF Stage`. Confirm the safe starting
   coordinate is more than 20 µm from 199.9 µm and inside the existing reviewed
   envelope. If it is not, move to another already-reviewed safe coordinate
   first and re-read it. Twenty is the tie: start any closer and the floor wins,
   the relative rule is never exercised on hardware, and this limb passes while
   testing a constant.
2. Call the registered `move_named_stage` tool with device `TIRF Stage`,
   `um=199.9`, `absolute=true`. **Do not retarget to the achieved coordinate.**
3. Export **only this call**: `export_session_script` with this call's exact
   `tool_use_id` in `tool_use_ids`. An unselected export re-runs every
   acquisition the session made when executed standalone.
4. Close Microclaw.

## Step 2 — name the two files that session produced

```powershell
$HISTORY = "<paste the *_microclaw_history.jsonl path for this session>"
$SCRIPT  = "<paste the path export_session_script wrote>"
foreach ($p in @($HISTORY, $SCRIPT)) {
    if (-not (Test-Path $p)) { throw "not found: $p" }
}
Copy-Item $HISTORY $EVIDENCE; Copy-Item $SCRIPT $EVIDENCE
```

`$HISTORY` may be the live `*_microclaw_history.jsonl` or a saved `.json`
history — the scorer reads both.

## Step 3 — run the exported script standalone and capture it

```powershell
& $PYTHON (Join-Path $REPO "design\66-stage-move-command-sheet.py") --interpreter $PYTHON --script $SCRIPT --capture $CAPTURE --output $SHEET
if ($LASTEXITCODE -ne 0) { throw "command-sheet generation failed" }
& $SHEET
if (-not (Test-Path $CAPTURE)) { throw "the sheet wrote no capture" }
```

If `& $SHEET` is refused by the execution policy, run
`powershell -ExecutionPolicy Bypass -File $SHEET` instead — it writes the same
capture. Do not redirect the sheet's own output and do not set
`$ErrorActionPreference = 'Stop'` around it: the sheet owns its capture, with
one writer and a pinned encoding, because `>` is `Out-File` (UTF-16LE) while
`Add-Content` defaults to the ANSI code page and a scorer reading UTF-8 gets
mojibake or raises before it reaches the limb.

## Step 4 — score the success limb

```powershell
& $PYTHON (Join-Path $REPO "design\66-stage-move-gate-scorer.py") --history $HISTORY --emitted $SCRIPT --capture $CAPTURE --log $M2LOG --device "TIRF Stage" --target 199.9 --mode m2-success
if ($LASTEXITCODE -ne 0) { throw "M2 success limb did not pass" }
```

The scorer independently requires one matching successful record, a residual
greater than 0.5 µm, `band_source` and `band_policy` both `relative`, the exact
band recomputed from the recorded start and target, settlement below 1 second,
an unchanged target, a selected-call script whose emitted `settle_stage_move`
carries the recorded start and the same `band_policy`, and one standalone
`EXIT_CODE=0` trailer. It matches the call structurally — by tool name, device
and numeric target — so an integer target in the record is not a miss.

A residual at or below 0.5 µm means the limb tested nothing and is
**NOT EXERCISED**, exactly as `band_source: "floor"` is: the same stage landed
inside 0.5 µm on several of Amr's commands and those would have passed under the
old constant too. The move having succeeded is not the evidence.

## Step 5 — the genuine non-response control

**The control limb must be able to fail.** With your explicit judgement that it
is safe on this hardware, power down or disconnect the `TIRF Stage` controller.
From a coordinate more than 20 µm from 199.9 µm, repeat the same registered
command without changing its target. Preserve that session's history.

```powershell
$CONTROL = "<paste the control session's history path>"
if (-not (Test-Path $CONTROL)) { throw "not found: $CONTROL" }
Copy-Item $CONTROL $EVIDENCE
& $PYTHON (Join-Path $REPO "design\66-stage-move-gate-scorer.py") --history $CONTROL --log $CTLLOG --device "TIRF Stage" --target 199.9 --mode m2-control
if ($LASTEXITCODE -ne 0) { throw "M2 non-response control did not pass" }
```

Control mode takes **no** `--emitted` or `--capture`: a failed live call is
deliberately not emitted as a runnable move, so it scores the refusal alone, and
passing another run's artifacts is refused rather than counted as two limbs that
cannot fail. The required evidence is a dispatch refusal, a stable
`start_um == measured_um`, or an unreadable start position — a link that is down
hard enough that the axis cannot be interrogated at all, which is what M2
produced on 2026-08-30 — read out of the refusal message — a refused tool
records an error string, not a result dict, so the message is the only channel.
A refusal whose message carries no `started … from <source> policy` clause was
written by pre-block-66 code and is NOT EXERCISED.

**Restore controller power and connectivity before leaving the rig.**

If you judge the control unsafe, it reports NOT EXERCISED, which is never a
pass, and the block's only discrimination evidence is the off-rig suite — say so
in the checklist row rather than ticking it.

## Step 6 — the demo regression limb (demo machine, not M2)

The demo machine proves the regression half: an axis that arrives exactly and
instantly is still verified, and reports how. It also carries the limb M2 could
not: a **selected** export.

### 6.1 — Set up, and let a command choose the route

Run all of Step 0 on this machine — `$REPO`, the pin, `$PYTHON`, a fresh
`$EVIDENCE`, and the self-test. Then let the config decide which tool this limb
uses, rather than deciding by eye:

```powershell
& $PYTHON -c "import os,yaml;p=os.path.join(os.environ['APPDATA'],'microclaw','safety_config.yaml');d=yaml.safe_load(open(p,encoding='utf-8'));n=d.get('named_stages') or [];print('ROUTE: NAMED' if n else 'ROUTE: FOCUS');[print('  device:',s.get('device')) for s in n];z=d.get('stage') or {};print('  z bounds:',z.get('z_min'),z.get('z_max'))"
```

`ROUTE: NAMED` — use the printed device with `move_named_stage`.
`ROUTE: FOCUS` — use `move_stage_z` on the core focus axis. **Do not add a named
stage to the config to get the first route**: the product does not require one,
and neither may this gate.

### 6.2 — Drive one session, using these prompts verbatim

Open Microclaw on the demo machine and paste each prompt as written. The first
names the tool because routing is not what this limb tests; the third
deliberately does **not** say how to select a call, because whether an agent can
find that mechanism is exactly what M2 left unexercised.

1. `What is the current position of <the device from 6.1, or the focus axis>?`
2. **ROUTE: NAMED** — `Use move_named_stage to move <device> to <a target at
   least 40 um from the position it just reported, inside the z bounds printed
   in 6.1>, absolute.`
   **ROUTE: FOCUS** — `Use move_stage_z to move the focus axis to <same rule>,
   absolute.`
   Record the target you used; it is `<demo target>` below. Do not retarget to
   the achieved coordinate.
3. `Export a standalone script containing only that move — none of the other
   calls in this session.`
4. If the export reports every recorded call rather than one, **stop and record
   what the agent did**, then ask it again naming the id explicitly. Both
   outcomes are evidence: the first is the M2 failure recurring, the second
   still exercises the selection.

Close Microclaw.

### 6.3 — Name the files, capture, and score

```powershell
$HISTORY = "<paste this session's *_microclaw_history.jsonl>"
$SCRIPT  = "<paste the path export_session_script wrote>"
foreach ($p in @($HISTORY, $SCRIPT)) { if (-not (Test-Path $p)) { throw "not found: $p" } }
Copy-Item $HISTORY $EVIDENCE; Copy-Item $SCRIPT $EVIDENCE
& $PYTHON (Join-Path $REPO "design\66-stage-move-command-sheet.py") --interpreter $PYTHON --script $SCRIPT --capture $CAPTURE --output $SHEET
if ($LASTEXITCODE -ne 0) { throw "command-sheet generation failed" }
& $SHEET
if (-not (Test-Path $CAPTURE)) { throw "the sheet wrote no capture" }
```

**ROUTE: NAMED**

```powershell
& $PYTHON (Join-Path $REPO "design\66-stage-move-gate-scorer.py") --history $HISTORY --emitted $SCRIPT --capture $CAPTURE --log $DEMOLOG --device "<demo device>" --target <demo target> --mode demo
if ($LASTEXITCODE -ne 0) { throw "demo regression limb did not pass" }
```

**ROUTE: FOCUS**

```powershell
& $PYTHON (Join-Path $REPO "design\66-stage-move-gate-scorer.py") --history $HISTORY --emitted $SCRIPT --capture $CAPTURE --log $DEMOLOG --tool move_stage_z --target <demo target> --mode demo
if ($LASTEXITCODE -ne 0) { throw "demo regression limb did not pass" }
```

The demo limb requires `within_tolerance: true` with `measured_um` exactly equal
to the target — the regression check — plus the selected-export limb, which
reports **NOT EXERCISED** on a whole-session export however well its move scores.
A simulated stage lands exactly, so a demo `measured_um` that is *not* the target
is a finding, not a tolerance question.

## What to return

The whole `$EVIDENCE` directory: three scorer logs, the histories, the selected
scripts, and the UTF-8 captures. PASS requires every required limb to say PASS.
An absent, empty, duplicate, or zero-match artifact is NOT EXERCISED and the
scorer exits nonzero.
