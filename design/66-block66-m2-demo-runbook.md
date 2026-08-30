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
   angle-bracket item with a quoted literal path:

```powershell
& <PYTHON> design/66-stage-move-gate-scorer.py --history <HISTORY_JSONL> --emitted <SELECTED_SCRIPT> --capture <CAPTURE> --log <M2_SUCCESS_LOG> --device "TIRF Stage" --target 199.9 --mode m2-success
if ($LASTEXITCODE -ne 0) { throw "M2 success limb did not pass" }
```

The scorer independently requires one matching successful record, a residual
greater than 0.5 µm, `band_source` and `band_policy` both `relative`, the exact
band recomputed from the recorded start and target, settlement below 1 second,
a parseable selected-call script, and one standalone `EXIT_CODE=0` trailer.

## M2 genuine non-response control

With the operator's explicit judgement that it is safe, power down or disconnect
the `TIRF Stage` controller. From a coordinate more than 20 µm from 199.9 µm,
repeat the same registered command without changing its target. Preserve the
control history. A failed live call is deliberately not emitted as a runnable
move, so reuse the selected success script and its successful capture as the
two required standalone arguments while scoring the independent control record:

```powershell
& <PYTHON> design/66-stage-move-gate-scorer.py --history <CONTROL_HISTORY_JSONL> --emitted <SELECTED_SUCCESS_SCRIPT> --capture <SUCCESS_CAPTURE> --log <CONTROL_LOG> --device "TIRF Stage" --target 199.9 --mode m2-control
if ($LASTEXITCODE -ne 0) { throw "M2 non-response control did not pass" }
```

Restore controller power/connectivity before leaving the rig. The required
evidence is a dispatch refusal or stable `start_um == measured_um`. The reused
success script must still have its unique `EXIT_CODE=0`; an
import/interpreter/ZMQ failure is NOT EXERCISED, not a control pass.

## Demo regression limb

On the demo machine, choose the declared named stage and an in-bounds target at
least 40 µm from its current coordinate. Run one registered
`move_named_stage`, export only that call, generate the capture, and score it
with the demo device and target substituted literally:

```powershell
& <PYTHON> design/66-stage-move-gate-scorer.py --history <DEMO_HISTORY_JSONL> --emitted <DEMO_SCRIPT> --capture <DEMO_CAPTURE> --log <DEMO_LOG> --device <DEMO_DEVICE> --target <DEMO_TARGET> --mode demo
if ($LASTEXITCODE -ne 0) { throw "demo regression limb did not pass" }
```

Return the three scorer logs, histories, selected scripts, and UTF-8 captures.
PASS requires every required limb to say PASS. An absent, empty, duplicate, or
zero-match artifact is NOT EXERCISED and the scorer exits nonzero.
