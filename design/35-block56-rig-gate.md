# Block 56 rig gate — measured move reporting

## Which rig, and the one precondition

**Run this on the Nikon.** It is not a second-choice venue: the defect this
block fixes was *measured there*. At 11:40 on 2026-08-05 `move_named_stage` on
`TIPFSOffset` returned `{"requested_um": 5, "achieved_um": 27.85,
"error_um": 22.85}` **as a success** (design/40 `:115`), and `TIPFSOffset` is
already declared under `named_stages` in that rig's config (design/40 `:271`).
The named-stage limbs below reproduce that exact call.

**The demo machine cannot run the miss limb** — its simulated stages
deterministically achieve every valid target, so a "miss" there would be
manufactured, not observed. On M2 or M5 the same limbs run against that rig's
own named stage and its own known floor; substitute the device and keep
everything else.

> ### Precondition: PFS must be disengaged for every limb below
>
> **This is not optional and it is Nikon-specific.** When PFS is locked the
> servo drives `TIPFSOffset` **on its own** — at 11:40 a lock at 2500 pulled it
> from 27.85 to 183.55 with no command (design/40 `:52`). A settle check
> measures whether the axis reached and held a commanded target; an armed servo
> moving it independently makes every measurement below meaningless.
>
> Before starting, call `get_focus_lock_state` and report it. If a lock is
> engaged, call `set_focus_lock(enabled=false)` and report the result, then
> re-read the state and confirm it is disengaged. Record both readings. Leave
> PFS disengaged until the gate is finished.
>
> ### Precondition: confirm `Core.Focus` is assigned
>
> Also Nikon-specific, and cheap insurance. Two sessions on this rig were lost
> to an unassigned `Core.Focus`, which surfaces as `z_stage: "unavailable"` from
> `get_system_state` and as a raw Java exception from every Z-facing tool — it
> is still undiagnosed on `main` (register, "The five that outlived Track B").
> Call `get_system_state` and confirm it reports a real focus device. If it
> reports `unavailable`, assign the focus role in Micro-Manager before starting;
> otherwise the first limb below fails for a reason that has nothing to do with
> this block.

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
git merge-base --is-ancestor 108b19f HEAD
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

This is the 11:40 reproduction. It needs a target that is **below the axis's
mechanical floor and still inside the configured `named_stages` bound** — the
combination that produced the original defect.

> Call `get_stage_position` for `TIPFSOffset` and report it. Report the
> configured `named_stages` bound for `TIPFSOffset` from the active safety
> config. Then call `move_named_stage` on `TIPFSOffset` with `absolute=true`
> and `um` set to the **lowest value that bound permits**. Report the complete
> result or the complete failure, whichever occurs. Then call
> `get_stage_position` for `TIPFSOffset` again and report it. Do not substitute
> `move_stage_z`, a property write, or another device.

Expected when the bound permits a sub-floor target: a `StageMoveError` whose
result carries `tolerance_um: 0.5`, `within_tolerance: false`, and a
`measured_um` at the mechanical floor — the 11:40 session floored at **27.85
µm** — with the independently read position within 0.5 µm of `measured_um`.

**Two other outcomes are valid results, not runbook failures.** Record whichever
happens and stop the limb:

- **The axis reaches the target within tolerance.** The configured minimum is at
  or above the floor on this config, so no miss is available. Report the
  configured bound and the achieved position; the limb is not runnable here.
- **`check_named_stage` refuses before dispatch.** The bound excludes the target
  entirely. Report the refusal text verbatim — that is a guard working, and it
  is not the settlement contract under test.

## Verbatim agent prompt: approved hook named-stage path

> Save and register a fixed-run hook named `block56_stage_observer`. Its
> `analyze_frame(image, metadata)` must return a `HookResult` with an empty
> measurement dictionary and no hardware actions; the acquisition plan, not
> the hook result, will supply the moves. Show the source before saving it.

> Call `get_stage_position` for `TIPFSOffset`. Then call `run_timelapse` for two
> frames with `interval_s=1`, no channel, `exposure_ms=10`, saving to
> `block56_hook`,
> and hook strategy `block56_stage_observer`. Supply a `hook_action_plan` whose
> frame-zero `MoveNamedStage` target is the measured entry position and whose
> frame-one target is entry position plus 1.0 µm. Approve a
> `named_stage_envelope` on `TIPFSOffset` from entry position through entry plus
> 1.0 µm, with `max_writes=2` and `restore="leave"`. Report the run result and
> read its hook log. Do not issue parent-side `move_named_stage` calls for these
> two moves.

Expected numbers: exactly two accepted `MoveNamedStage` records; each has
`tolerance_um: 0.5`, `within_tolerance: true`, and `measured_um` within 0.5 µm
of `requested_um`. The second accepted record has `hook_event_index: 1`.

## Verbatim agent prompt: the restoration path settles too

This block changed what a restoration failure *is*: a restore that lands outside
tolerance now raises where a 1.1 µm miss previously returned `restored: true`.
The limb above runs `restore="leave"` and never exercises that. Restoration is
also the one area block 52c had to reopen, so it gets its own limb rather than
being inferred from the unit tests.

> Call `get_stage_position` for `TIPFSOffset` and record it. Run the same
> `run_timelapse` as the previous limb — two frames, `interval_s=1`, no channel,
> `exposure_ms=10`, saving to `block56_restore`, hook strategy
> `block56_stage_observer` — but approve the `named_stage_envelope` with
> `restore="entry"` and `max_writes=3`, and give the `hook_action_plan` a single
> frame-zero `MoveNamedStage` to entry position plus 1.0 µm. Report the run
> result, the restoration block of the result, and the hook log. Then call
> `get_stage_position` for `TIPFSOffset` independently and report it.

Expected numbers: the restoration block reports `policy: "entry"`,
`restored: true`, and an `entry_um` equal to the position recorded before the
run. The independently read position is within 0.5 µm of `entry_um`. The hook
log carries a third accepted `MoveNamedStage` record, the restoration move, with
`within_tolerance: true`.

**If restoration instead reports a failure**, that is a real result and not a
runbook defect: record the reported `measured_um`, `requested_um` and
`last_device_status`, read the axis independently, and report it. A stage whose
restore settles outside 0.5 µm is exactly the finding this block is looking for,
and it decides whether the tolerance is right.

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
