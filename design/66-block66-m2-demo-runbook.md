# Block 66 M2/demo rig gate

Implementation pin: before any rig action, run:

```powershell
git merge-base --is-ancestor ff6723c HEAD
if ($LASTEXITCODE -ne 0) { throw "Block 66 implementation is not present" }
```

Use a fresh evidence directory and record its literal absolute path. Do not add
any tolerance setting for this gate. The setup-generated safety config must stay
unchanged. The operator owns the decision whether disconnecting or powering down
the TIRF controller is safe; if it is not, record the control as NOT EXERCISED
and do not report the M2 gate as passed.

## M2 success limb

1. In Microclaw, call `get_stage_position` for `TIRF Stage`. Confirm the safe
   starting coordinate is more than 20 µm from 199.9 µm and inside the existing
   reviewed envelope. If it is not, move to another already-reviewed safe
   coordinate first and re-read it.
2. Call the registered `move_named_stage` tool with device `TIRF Stage`,
   `um=199.9`, `absolute=true`. Do not retarget to the achieved coordinate.
3. Export only this call with `export_session_script`, passing its exact
   `tool_use_id` in `tool_use_ids`. Save the history JSONL and selected script in
   the evidence directory.
4. Close Microclaw. Generate the PowerShell 5.1 capture sheet with resolved
   literal paths (the interpreter must be the Python installation containing
   pycromanager):

```powershell
& <PYTHON> design/66-stage-move-command-sheet.py --interpreter <PYTHON> --script <SELECTED_SCRIPT> --capture <CAPTURE> --output <SHEET>
if ($LASTEXITCODE -ne 0) { throw "command-sheet generation failed" }
& <SHEET>
```

5. Score the artifacts. Every argument below is required; replace each
   angle-bracket item with a quoted literal path. `<HISTORY>` is either the
   live `*_microclaw_history.jsonl` or the saved `.json` history — the scorer
   reads both:

```powershell
& <PYTHON> design/66-stage-move-gate-scorer.py --history <HISTORY> --emitted <SELECTED_SCRIPT> --capture <CAPTURE> --log <M2_SUCCESS_LOG> --device "TIRF Stage" --target 199.9 --mode m2-success
if ($LASTEXITCODE -ne 0) { throw "M2 success limb did not pass" }
```

The scorer independently requires one matching successful record, a residual
greater than 0.5 µm, `band_source` and `band_policy` both `relative`, the exact
band recomputed from the recorded start and target, settlement below 1 second,
an unchanged target, a selected-call script whose emitted `settle_stage_move`
carries the recorded start and the same `band_policy`, and one standalone
`EXIT_CODE=0` trailer. It matches the call structurally — by tool name, device
and numeric target — so an integer target in the record is not a miss.

## M2 genuine non-response control

With the operator's explicit judgement that it is safe, power down or disconnect
the `TIRF Stage` controller. From a coordinate more than 20 µm from 199.9 µm,
repeat the same registered command without changing its target. Preserve the
control history. A failed live call is deliberately not emitted as a runnable
move, so the control mode takes **no** `--emitted` or `--capture`: it scores the
refusal alone, and passing another run's artifacts is refused rather than
counted as two limbs that cannot fail.

```powershell
& <PYTHON> design/66-stage-move-gate-scorer.py --history <CONTROL_HISTORY> --log <CONTROL_LOG> --device "TIRF Stage" --target 199.9 --mode m2-control
if ($LASTEXITCODE -ne 0) { throw "M2 non-response control did not pass" }
```

Restore controller power/connectivity before leaving the rig. The required
evidence is a dispatch refusal or a stable `start_um == measured_um`, read out
of the refusal message — a refused tool records an error string, not a result
dict, so the message is the only channel. A refusal whose message carries no
`started ... from <source> policy` clause was written by pre-block-66 code and
is NOT EXERCISED.

## Demo regression limb

On the demo machine, choose the declared named stage and an in-bounds target at
least 40 µm from its current coordinate. Run one registered
`move_named_stage`, export only that call, generate the capture, and score it
with the demo device and target substituted literally:

```powershell
& <PYTHON> design/66-stage-move-gate-scorer.py --history <DEMO_HISTORY> --emitted <DEMO_SCRIPT> --capture <DEMO_CAPTURE> --log <DEMO_LOG> --device <DEMO_DEVICE> --target <DEMO_TARGET> --mode demo
if ($LASTEXITCODE -ne 0) { throw "demo regression limb did not pass" }
```

**If this demo machine declares no named stage**, do not add one — the product
does not require one and neither may this gate. Run the same limb on the core
focus axis with `move_stage_z` instead, which needs no configuration:

```powershell
& <PYTHON> design/66-stage-move-gate-scorer.py --history <DEMO_HISTORY> --emitted <DEMO_SCRIPT> --capture <DEMO_CAPTURE> --log <DEMO_LOG> --tool move_stage_z --target <DEMO_TARGET> --mode demo
if ($LASTEXITCODE -ne 0) { throw "demo regression limb did not pass" }
```

Before the rig, confirm the instrument itself still discriminates:

```powershell
& <PYTHON> design/66-stage-move-gate-selftest.py
if ($LASTEXITCODE -ne 0) { throw "gate self-test failed" }
```

Return the three scorer logs, histories, selected scripts, and UTF-8 captures.
PASS requires every required limb to say PASS. An absent, empty, duplicate, or
zero-match artifact is NOT EXERCISED and the scorer exits nonzero.
