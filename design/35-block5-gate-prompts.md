# design/35 Block 5 — deployed-config hygiene and `init` gate

This runbook verifies branch `design33/deployed-config-hygiene`. The implementation
is pinned at `6262acb`; later runbook or correction commits are valid descendants.
Run the demo-machine gate first. Run M5 only after the demo evidence passes.

All commands are PowerShell-safe. If this installation uses `uv run microclaw`,
substitute that invocation throughout. Preserve the complete evidence directories.
Bind a path once and reuse the variable; do not repeatedly substitute a placeholder.

This gate normally prefers artifacts produced by the workflow itself: setup's
inventory/interview files and `check-config` output. G3 deliberately captures a
running server's stdout because redirected stdout is the product behavior under
test—the Block 4d defect was precisely that this artifact remained empty. This is
not a substitute for normal session evidence. A session-history JSONL is created
only on its first appended message (`conversation.py:153`), so any separate live
session evidence must include at least one message before shutdown.

## G0 — branch identity and full suite

Run on each machine from the Microclaw checkout:

```powershell
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Evidence = "block5-$Stamp"
New-Item -ItemType Directory -Path $Evidence
git fetch origin > "$Evidence\git-fetch.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\git-fetch-exit.txt"
git switch design33/deployed-config-hygiene > "$Evidence\git-switch.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\git-switch-exit.txt"
git pull --ff-only > "$Evidence\git-pull.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\git-pull-exit.txt"
git status --short > "$Evidence\status.txt" 2>&1
git rev-parse HEAD > "$Evidence\head.txt" 2>&1
git merge-base --is-ancestor 6262acb HEAD > "$Evidence\implementation-ancestor.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\implementation-ancestor-exit.txt"
git merge-base --is-ancestor 577acc4 HEAD > "$Evidence\installer-fix-ancestor.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\installer-fix-ancestor-exit.txt"
python -m pytest -q > "$Evidence\pytest.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\pytest-exit.txt"
Copy-Item "design\35-block5-gate-prompts.md" "$Evidence\runbook.md"
```

Two ancestry pins, not one: `6262acb` is the `init`/detector/flush implementation
and `577acc4` is the installer-boundary fix that followed review. Both are pinned
by ancestry rather than by an exact tip hash, so amending this runbook cannot
invalidate its own pin.

It worked when every `*-exit.txt` above contains `0`, pytest reports no failures,
and `status.txt` is empty. The expected suite result is **1377 passed, 99 skipped,
3 warnings**; the 3 warnings are one `StarletteDeprecationWarning` and two
`phase_cross_correlation` empty-image warnings, all pre-existing. Send back the
complete evidence directory.

## G1 — demo machine, installer boundary, `init` redirect, and escape hatch

Micro-Manager need not be running for this step. Use paths that do not already
exist. First prove the installer does not invoke either command that can contact
the rig, and prints setup only as a post-install instruction:

```powershell
$RigCmds = @(Select-String -Path "install.bat" -Pattern '^"%MC_EXE%" init$','^"%MC_EXE%" first-launch-setup')
$RigCmds > "$Evidence\installer-rig-command-lines.txt"
$RigCmds.Count > "$Evidence\installer-rig-command-count.txt"
$NextSteps = @(Select-String -Path "install.bat" -Pattern 'Run pycro-manager server on port 4827','echo     "%MC_EXE%" init')
$NextSteps > "$Evidence\installer-next-steps.txt"
$NextSteps.Count > "$Evidence\installer-next-steps-count.txt"
```

It worked when `installer-rig-command-count.txt` is `0` — installation invokes
neither live setup command — and `installer-next-steps-count.txt` is `2`, with
`installer-next-steps.txt` showing the ZMQ prerequisite at a lower line number
than the printed `init` command. This is structural evidence, not a request to
reinstall Microclaw on the rig.

**Why counts and not exit codes.** `Select-String` is a cmdlet, and PowerShell
sets `$LASTEXITCODE` only from *native* executables. Reading it after a cmdlet
returns whatever the last `git`, `python`, or `microclaw` call left behind, so a
match check written that way records a stale number and proves nothing. Every
match step in this runbook therefore reports a count. Steps that invoke
`microclaw`, `python`, or `git` are native and keep using `$LASTEXITCODE`
correctly.

Now exercise the human-facing command normally, without redirecting it. **Answer
`N` at the prompt**; this step proves that declining writes nothing:

```powershell
$Redirect = Join-Path $Evidence "redirect-must-not-exist.yaml"
Start-Transcript -Path "$Evidence\init-interactive-transcript.txt"
microclaw init --path $Redirect --no-edit
Stop-Transcript
Test-Path $Redirect > "$Evidence\init-interactive-wrote.txt"
```

It worked when the transcript names `first-launch-setup`, human review, and
restart, and `init-interactive-wrote.txt` is `False`.

Now prove redirected input never waits for an answer. This process must return
immediately without reading the piped line or writing a config:

```powershell
$NonInteractive = Join-Path $Evidence "noninteractive-must-not-exist.yaml"
cmd /c "echo unused| microclaw init --path `"$NonInteractive`" --no-edit > `"$Evidence\init-noninteractive.txt`" 2>&1"
echo $LASTEXITCODE > "$Evidence\init-noninteractive-exit.txt"
Test-Path $NonInteractive > "$Evidence\init-noninteractive-wrote.txt"
```

It worked when the exit is `0`, output says non-interactive setup was not started,
and the wrote file says `False`.

Finally exercise the deliberate hand-authoring escape hatch:

```powershell
$Example = Join-Path $Evidence "hand-authored-example.yaml"
microclaw init --from-example --path $Example --no-edit > "$Evidence\init-from-example.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\init-from-example-exit.txt"
Get-FileHash -Algorithm SHA256 $Example > "$Evidence\init-from-example.sha256.txt"
microclaw check-config $Example > "$Evidence\example-check.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\example-check-exit.txt"
```

It worked when copying exits `0`, the file exists, and `check-config` names both
the intentionally unreviewed profile and the packaged fictional example-limit
values. Its nonzero validator exit is expected because `reviewed: false` remains
the hard gate; the example-value diagnostic itself must say it is a review warning,
not a refusal.

## G2 — demo machine, example warning and clean negative control

The positive control is `$Example` from G1. Create a schema-valid reviewed negative
control whose numeric limits intentionally differ from every packaged value:

```powershell
$Distinct = Join-Path $Evidence "distinct-limits.yaml"
@'
schema_version: 2
reviewed: true
property_authorization: {mode: guaranteed, allowed_categorical: [], denied: []}
stage: {x_min: -101.0, x_max: 101.0, y_min: -102.0, y_max: 102.0}
camera: {max_exposure_ms: 501.0}
acquisition:
  max_frames: 10001
  max_duration_s: 3601
  max_bytes: 50000000001
  max_illuminated_ms: 600001
  max_session_illuminated_ms: 1800001
  confirm_above_frames: 501
  confirm_above_duration_s: 301
  confirm_above_bytes: 5000000001
  confirm_above_illuminated_ms: 60001
'@ | Set-Content -Encoding utf8 $Distinct
microclaw check-config $Distinct > "$Evidence\distinct-check.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\distinct-check-exit.txt"
$ExampleWarn = @(Select-String -Path "$Evidence\example-check.txt" -Pattern "EXAMPLE-LIMIT REVIEW")
$ExampleWarn > "$Evidence\example-warning-match.txt"
$ExampleWarn.Count > "$Evidence\example-warning-count.txt"
$DistinctWarn = @(Select-String -Path "$Evidence\distinct-check.txt" -Pattern "EXAMPLE-LIMIT REVIEW")
$DistinctWarn > "$Evidence\distinct-warning-match.txt"
$DistinctWarn.Count > "$Evidence\distinct-warning-count.txt"
```

It worked when `example-warning-count.txt` is `1`, the distinct config validates
with exit `0`, and `distinct-warning-count.txt` is `0` because no example-limit
diagnostic is present. That zero is the discriminating result of this step: it is
what shows the detector is reading the config rather than always firing. The
negative control is only an offline validator fixture; its values do not describe
the demo rig and it must not be used to start a session.

## G3 — demo machine, redirected server banner

Bind the reviewed safety profile used by the demo machine's normal workflow once:

```powershell
$DemoConfig = "C:\replace-once\with\reviewed-demo-safety-config.yaml"
microclaw check-config $DemoConfig > "$Evidence\demo-config-check.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\demo-config-check-exit.txt"
microclaw --safety-config $DemoConfig serve > "$Evidence\serve-redirected.txt" 2>&1
```

Wait until the browser GUI is reachable, then stop the console command with
Ctrl+C. Do not substitute the synthetic G2 negative control for `$DemoConfig`.

```powershell
(Get-Item "$Evidence\serve-redirected.txt").Length > "$Evidence\serve-redirected-bytes.txt"
$UrlMatch = @(Select-String -Path "$Evidence\serve-redirected.txt" -Pattern "Microclaw GUI:")
$UrlMatch > "$Evidence\serve-url-match.txt"
$UrlMatch.Count > "$Evidence\serve-url-count.txt"
```

It worked when the byte count is greater than zero and `serve-url-count.txt` is
`1`, both recorded from a run that was a long-running process until Ctrl+C.
Captured stdout is the required artifact in this one step because flushing that
banner is the defect being tested — that is the deliberate exception to this
project's rule of preferring an artifact the normal workflow already produces.

## G4 — M5, report the deployed budgets without editing them

This step is read-only. The coordinator owns the four Block 4d key renames and the
operator-owned budget edit. Run this check after the deployed file has the current
schema keys but before its budgets are revised; do not edit it in this runbook.

**Read this before interpreting the result — the premise changed.** The checklist
and design/33 `:790` say M5's budgets were copied from the fictional example. The
coordinator replayed the captured `m5-deployed-before.yaml` (Block 4d evidence)
through this block's detector on 2026-08-03, and **that is no longer true**: every
acquisition budget and the exposure cap have since been edited away from the
example, not toward measured values but to numbers that effectively do not bind —
`max_duration_s: 1e21`, `max_illuminated_ms: 1000001720`, `max_bytes: 1.06e12`,
`camera.max_exposure_ms: 10000.0172`. Only `acquisition.confirm_above_illuminated_ms`
and `stage.z_min` still match the example, and both are plausibly coincidental.

So this step is **not** expected to show a config full of example values. Its job
is to record what the deployed budgets actually are, so the coordinator-sequenced
editing session starts from measured fact rather than from a stale claim. A small
match count here is the expected result, not a failure.

Bind the deployed file's exact normal-launcher path once:

```powershell
$DeployedConfig = "C:\replace-once\with\M5-deployed-safety-config.yaml"
Get-FileHash -Algorithm SHA256 $DeployedConfig > "$Evidence\m5-deployed.sha256.txt" 2>&1
Copy-Item $DeployedConfig "$Evidence\m5-deployed-before-budget-review.yaml"
microclaw check-config $DeployedConfig > "$Evidence\m5-deployed-check.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\m5-deployed-check-exit.txt"
$M5Warn = @(Select-String -Path "$Evidence\m5-deployed-check.txt" -Pattern "EXAMPLE-LIMIT REVIEW")
$M5Warn > "$Evidence\m5-example-budget-match.txt"
$M5Warn.Count > "$Evidence\m5-example-budget-count.txt"
```

Also record the deployed budgets themselves, which are the numbers the editing
session actually needs:

```powershell
Select-String -Path $DeployedConfig -Pattern "max_","confirm_above_" > "$Evidence\m5-deployed-budgets.txt"
```

It worked when `m5-deployed-check.txt` names only keys actually present in the
deployed file, says a matching value requires rig review but is not by itself a
refusal, and `m5-deployed-budgets.txt` records the current numbers. Any count is
an acceptable result here — see the premise note above; report the number rather
than judging it. Preserve the original hash and copy. **Do not change acquisition
or exposure budgets here**; the operator chooses real values in the
coordinator-sequenced editing session.

Send back the complete evidence directory, the exact `$DemoConfig` and
`$DeployedConfig` paths used, and any unexpected diagnostic verbatim.
