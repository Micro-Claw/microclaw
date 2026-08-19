# Block 56 rig gate — measured move reporting

## Frozen contract

All single-axis moves return `requested_um`, `measured_um`, `tolerance_um`,
`within_tolerance: true`, `elapsed_s`, and `last_device_status`. Named moves
also return `device`. A move that does not satisfy the same contract raises
`StageMoveError`; its `result` carries those fields with
`within_tolerance: false`.

The default tolerance is **0.5 µm**. Rig profiles contain no per-axis
repeatability, so this is a deliberately conservative generic software policy,
not a hardware claim. The timeout is **10 s**, long compared with ordinary
stage settling while still bounding a failed move. Positions are sampled every
**50 ms**. Success requires **three consecutive** in-tolerance samples spanning
at least a **100 ms stability window**; this rejects a transient crossing while
adding only about 100 ms to an ordinary move. Device busy/idle status is
reported but is not the gate: an idle device outside target tolerance cannot
succeed.

## Commands

```powershell
git merge-base --is-ancestor b549884 HEAD
$LASTEXITCODE
```

Expected output: `0`.

```powershell
python -m pytest -q tests/test_tools.py tests/test_controller.py tests/test_hook_decisions.py tests/test_session_script_export.py > block56-tests.txt 2>&1
$LASTEXITCODE
```

Expected output: `0`; the summary reports no failures.

## Verbatim agent prompt: ordinary focus-stage move

> Call `get_z_position`. Then call `move_stage_z` with `absolute=true` and
> `z_um` equal to the reported Z plus 1.0. Report the complete tool result.
> Call `get_z_position` again and report it. Do not use a named-stage tool.

Expected numbers: `requested_um` is entry Z plus 1.0, `tolerance_um` is `0.5`,
`within_tolerance` is `true`, and the independent Z differs from `measured_um`
by no more than 0.5 µm.

## Verbatim agent prompt: named-stage hard-limit miss

This limb requires M2 or M5. The demo stages deterministically achieve every
valid target, so the demo rig cannot supply a real target miss and is not an
acceptable fallback for this limb.

> Call `list_stages`. Choose one single-axis stage whose safety profile permits
> motion and whose current position can be read. Call `get_stage_position` for
> it. Call `move_named_stage` on that exact device with `absolute=true` and a
> target 1.0 µm beyond a known mechanical travel limit but still inside the
> configured safety bound. Report the complete failure. Then call
> `get_stage_position` independently and report it. Do not substitute
> `move_stage_z` or a property write. If no such safe target exists on this rig,
> stop the gate and route it to the other physical rig; do not substitute the
> demo device or another mechanism.

Expected numbers when runnable: `tolerance_um` is `0.5`, `within_tolerance` is
`false`, and independent position differs from `measured_um` by no more than
0.5 µm.

## Verbatim agent prompt: approved hook named-stage path

> Save and register a fixed-run hook named `block56_stage_observer`. Its
> `analyze_frame(image, metadata)` must return a `HookResult` with an empty
> measurement dictionary and no hardware actions; the acquisition plan, not
> the hook result, will supply the moves. Show the source before saving it.

> Call `get_stage_position` for `TIRF Stage`. Then call `run_timelapse` for two
> frames with `interval_s=1`, no channel, `exposure_ms=10`, saving to
> `block56_hook`,
> and hook strategy `block56_stage_observer`. Supply a `hook_action_plan` whose
> frame-zero `MoveNamedStage` target is the measured entry position and whose
> frame-one target is entry position plus 1.0 µm. Approve a
> `named_stage_envelope` on `TIRF Stage` from entry position through entry plus
> 1.0 µm, with `max_writes=2` and `restore="leave"`. Report the run result and
> read its hook log. Do not issue parent-side `move_named_stage` calls for these
> two moves.

Expected numbers: exactly two accepted `MoveNamedStage` records; each has
`tolerance_um: 0.5`, `within_tolerance: true`, and `measured_um` within 0.5 µm
of `requested_um`. The second accepted record has `hook_event_index: 1`.

## Verbatim agent prompt: export and execute this session

> Export the current session with `export_session_script` to
> `block56-export.py`. Do not start a fresh session. Report the export result.

```powershell
python block56-export.py > block56-export.txt 2>&1
$LASTEXITCODE
```

Expected output: `0` for an in-tolerance recorded move. For a recorded missed
move, the expected output is nonzero and `block56-export.txt` contains
`within_tolerance` and `False` with the requested and measured values.
