# design/35 Block 4c — named-stage setup gate

This runbook verifies branch `design33/setup-named-stages`. The implementation
is pinned at `aaebc09`; later documentation or test commits are valid
descendants. Run the demo gate first without hazardous hardware motion, then run
the M5 motion gate with a qualified operator at the microscope. Do not make a
generated profile permanent.

The implementation deliberately excludes a non-core `XYStageDevice` and prints
why: top-level `named_stages` can represent only one axis, so setup cannot state
honest independent X/Y bounds. It asks about every loaded single-axis
`StageDevice` except the core focus device.

## G0 — identity and suite on both machines

Run from PowerShell. Preserve every file named below; every mechanical command
records its exit code separately.

```powershell
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Evidence = "block4c-$Stamp"
New-Item -ItemType Directory -Path $Evidence
git fetch origin > "$Evidence\git-fetch.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\git-fetch-exit.txt"
git switch design33/setup-named-stages > "$Evidence\git-switch.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\git-switch-exit.txt"
git pull --ff-only > "$Evidence\git-pull.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\git-pull-exit.txt"
git status --short > "$Evidence\status.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\status-exit.txt"
git rev-parse HEAD > "$Evidence\head.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\head-exit.txt"
git merge-base --is-ancestor aaebc09 HEAD > "$Evidence\implementation-ancestor.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\implementation-ancestor-exit.txt"
python -m pytest -q > "$Evidence\pytest.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\pytest-exit.txt"
Copy-Item "design\35-block4c-gate-prompts.md" "$Evidence\runbook.md"
Copy-Item "design\35-block4c-gate-check.py" "$Evidence\gate-check.py"
```

The ancestor exit must be `0`, the tree must be clean, and pytest must have no
failures. Return the raw `status.txt`, `head.txt`, and `pytest.txt`, not only a
summary.

## G1 — demo-machine interview, validation, and startup; no motion

Make a temporary copy of the normal Micro-Manager demo configuration. In the
Hardware Configuration Wizard add a second DemoCamera single-axis stage with a
distinct label such as `Aux Z`, but leave the normal `Z` device assigned as
Core focus. Save only the temporary configuration. This creates the non-core
stage needed to exercise authoring without hazardous motion; do not move it in
this gate. If the DemoCamera adapter cannot provide a second `StageDevice`, stop
and return that fact rather than fabricating inventory.

```powershell
$Port = 4827
$Draft = Join-Path $Evidence "demo-profile.yaml"
$Inventory = Join-Path $Evidence "demo-inventory"
microclaw --port $Port first-launch-setup --out $Draft --mm-config <temporary-demo-config> --evidence-out $Inventory
echo $LASTEXITCODE > "$Evidence\demo-interview-exit.txt"
python design\35-block4c-gate-check.py profile $Draft "Aux Z" > "$Evidence\demo-named-stage-check.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\demo-named-stage-check-exit.txt"
microclaw check-config $Draft > "$Evidence\demo-check-unreviewed.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\demo-check-unreviewed-exit.txt"
Copy-Item $Draft "$Evidence\demo-profile.reviewed.yaml"
```

The profile check must print two `True` lines. `check-config` must reject only
because setup intentionally writes `reviewed: false`. Inspect the raw draft and
the complete transcript under `demo-inventory`: they must show the `Aux Z`
questions and typed-value or accepted-proposal audit lines, and must not emit a
named entry for core focus `Z`.

Read the copied profile completely and change only `reviewed: false` to
`reviewed: true` by hand. Then validate and start a normal session:

```powershell
microclaw check-config "$Evidence\demo-profile.reviewed.yaml" > "$Evidence\demo-check-reviewed.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\demo-check-reviewed-exit.txt"
microclaw --port $Port --safety-config "$Evidence\demo-profile.reviewed.yaml" serve
echo $LASTEXITCODE > "$Evidence\demo-session-exit.txt"
```

The reviewed validator exit must be `0`, and the session must reach its prompt.
Ask one read-only question, then exit. Preserve the raw inventory JSON,
transcript, draft, reviewed profile, both validator outputs, and session output
or history. This gate requires no stage motion.

## G2 — M5 in-range move and out-of-range refusal

Run a fresh full interview against M5 and preserve its new inventory and
transcript. Keep illumination off. The generated profile must contain the real
non-core single-axis stages (expected from captured evidence: `SmarAct 1D`,
`Thorlabs ELL17/ELL20`, and `Thorlabs ELL20`) and must exclude core focus
`PIZStage`. Review each travel range as a safety boundary, not as proof that all
positions are physically safe.

```powershell
$Draft = Join-Path $Evidence "m5-profile.yaml"
$Inventory = Join-Path $Evidence "m5-inventory"
microclaw first-launch-setup --out $Draft --evidence-out $Inventory
echo $LASTEXITCODE > "$Evidence\m5-interview-exit.txt"
python design\35-block4c-gate-check.py profile $Draft "<chosen-non-core-stage>" > "$Evidence\m5-named-stage-check.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\m5-named-stage-check-exit.txt"
Copy-Item $Draft "$Evidence\m5-profile.reviewed.yaml"
```

After reading the entire profile, change only `reviewed: false` to
`reviewed: true`, validate it, and start a session:

```powershell
microclaw check-config "$Evidence\m5-profile.reviewed.yaml" > "$Evidence\m5-check-reviewed.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\m5-check-reviewed-exit.txt"
microclaw --safety-config "$Evidence\m5-profile.reviewed.yaml" serve
```

Choose one non-core stage and safe absolute value strictly inside its declared
range. In the browser ask:

> Call `move_named_stage` for device `<chosen-non-core-stage>` at absolute
> position `<inside-value>` µm, then report the achieved position.

Then choose a value strictly outside the declared range and ask exactly:

> Call `move_named_stage` for device `<chosen-non-core-stage>` at absolute
> position `<outside-value>` µm. Do not pre-check or decline the call: this is a
> test that Microclaw's safety guard itself refuses the attempted tool call.

The first call must succeed with a read-back; the second tool call must occur
and return a safety refusal without motion. Mechanically check the raw session
history, substituting its path and the exact numeric values:

```powershell
python design\35-block4c-gate-check.py history <history.jsonl> "<chosen-non-core-stage>" <inside-value> <outside-value> > "$Evidence\m5-motion-check.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\m5-motion-check-exit.txt"
```

Both lines must print `True` and the exit must be `0`. Preserve the raw history
JSONL read by this check, plus the profile and transcript read by the profile
check. Restore the stage to its original safe position if local practice
requires it, using an authorized in-range move, and record that result.

## Return to the coordinator

Return both complete evidence directories. Include every `*-exit.txt`, raw
pytest/status/head outputs, inventories, full interview transcripts, draft and
reviewed profiles, validator/session outputs, M5 history JSONL, mechanical-check
outputs, chosen stage, declared bounds, original/inside/outside/final positions,
and exact refusal text. Report any unclear prompt, missing expected stage,
unexpected non-core XY device, startup refusal, motion discrepancy, or suite
failure. Do not merge or push `main`.
