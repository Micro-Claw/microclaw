# Block 56 rig gate — measured move reporting

## Which rig, and the one precondition

**Run this on the Nikon.** It is not a second-choice venue: the defect this
block fixes was *measured there*. At 11:40 on 2026-08-05 `move_named_stage` on
`TIPFSOffset` returned `{"requested_um": 5, "achieved_um": 27.85,
"error_um": 22.85}` **as a success** (design/40 `:115`), and `TIPFSOffset` is
already declared under `named_stages` in that rig's config (design/40 `:271`).
The named-stage limbs below reproduce that exact call.

> **GATE COMPLETE — block 56 merged `c8f1801` on 2026-08-19. This document is a
> record of what was run, not a plan. Do not re-run it.**

**Final gate status: five limbs run, all passed, and the missed-move criterion is
recorded as unreproducible on this hardware.** The focus move, the hook path, the
restoration path and the export all passed with agreeing independent reads, and
the rig suite reported 551 passed.

**Neither of the two limbs written to produce a missed move could produce one**,
and that is the honest outcome rather than a gap to fill later:

- **Hard-limit limb** — `TIPFSOffset` commanded to 0.0, its configured minimum,
  **reached 0.0 exactly**. There is no mechanical floor inside the bound.
- **Servo-override limb** — with PFS armed and `Locked in focus`, `TIPFSOffset`
  commanded from 1.0 to 21.0 **reached 21.0 exactly** (independently re-read).
  So the offset is writable under an active lock and the servo does not override
  a commanded write.

**What the gate proved instead, positively.** The successful move returned
`last_device_status: "busy"` with `elapsed_s: 0.89` — about eighteen polls. The
settle loop out-waited a device that was **still reporting busy**, which is the
entire mechanism under test. It also explains the original defect: the old path
read the device once, immediately after `wait_for_device`, so on an axis that
takes ~0.9 s the read landed before the move finished and returned the pre-move
position. **A premature read-back — not a floor, and not a servo override.**

**Two findings that are not this block's**, both now carried as register rows in
the checklist: `get_focus_lock_state` answered *"No EMU configuration — cannot
read a focus lock"* on a rig whose hardware lock was working and **blocked this
gate**, forcing a raw `TIPFSStatus.State` write; and the step asking for the
configured `named_stages` bound **did not run at all**, because no tool reports
the bounds microclaw is enforcing.

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
> **One limb is the exception and it is deliberate**: "the servo-override miss"
> arms PFS on purpose, because that is the only condition on this rig that
> produces a real missed move. Run it **last**, and disengage PFS again when it
> is done.
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

## Verbatim agent prompt: ordinary focus-stage move — **PASS 2026-08-19**

> Call `get_z_position`. Then call `move_stage_z` with `absolute=true` and
> `z_um` equal to the reported Z plus 1.0. Report the complete tool result.
> Call `get_z_position` again and report it. Do not use a named-stage tool.

Expected numbers: `requested_um` is entry Z plus 1.0, `tolerance_um` is `0.5`,
`within_tolerance` is `true`, and the independent Z differs from `measured_um`
by no more than 0.5 µm.

## Verbatim agent prompt: named-stage hard-limit miss — **RUN 2026-08-19, negative result, do not re-run**

> **Already run and settled.** `TIPFSOffset` was commanded to 0.0, the lowest its
> configured bound allows (`min_um: 0.0`), and **reached 0.0 exactly**
> (`within_tolerance: true`, independent read 0.0). There is no mechanical floor
> inside the configured bound on this axis, so this limb cannot produce a miss
> and is retired. The servo-override limb below was written to replace it and could not produce a miss either; see the gate status at the top.
>
> **One step in it could not run at all**: it asked the agent to report the
> configured `named_stages` bound from the active safety config, and **no tool
> exposes that**. The bound had to be read from `safety_config.yaml` by hand.
> Do not write another step that asks for it until a tool exists.

This was the 11:40 reproduction. It needs a target that is **below the axis's
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

## Verbatim agent prompt: approved hook named-stage path — **PASS 2026-08-19**

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

## Verbatim agent prompt: the restoration path settles too — **PASS 2026-08-19**

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

## Verbatim agent prompt: the servo-override miss — **RUN 2026-08-19, negative result: the move succeeded under lock**

**Why this limb exists.** The 2026-08-19 gate could not produce a missed move:
`TIPFSOffset` was commanded to 0.0, the lowest its configured bound allows, and
**reached 0.0 exactly**. The 27.85 µm figure from the 11:40 session of
2026-08-05 was never a mechanical floor — that reading was taken while **PFS was
locked and holding the axis**, which is why the commanded move to 5 µm left it
where it was. design/40 records the same session's servo driving the offset from
27.85 to 183.55 with no command at all.

So on this rig the reproducible miss is a **servo override**, not a hard limit,
and it needs PFS armed. This limb is the 11:40 defect reproduced on its real
mechanism.

**Safety note.** This is a software write that the servo may ignore — exactly
what happened accidentally at 11:40, with no harm. If the write *is* honoured it
shifts the focal plane by the offset delta, which the last step puts back.

> Call `get_focus_lock_state` and report it. Call `set_focus_lock(enabled=true)`,
> then re-read `get_focus_lock_state` and report it; do not continue until it
> reports an engaged, locked state. If it will not lock, stop and report that —
> do not force it.
>
> Once locked, call `get_stage_position` for `TIPFSOffset` and report it; this is
> the settled offset the servo has chosen, and it is the number the next step
> works from. Call `move_named_stage` on `TIPFSOffset` with `absolute=true` and
> `um` set to that settled position **plus 20.0**, unless that exceeds 980, in
> which case use the settled position **minus 20.0**. Report the complete result
> or the complete failure, whichever occurs, in full.
>
> Then call `get_stage_position` for `TIPFSOffset` again and report it. Then call
> `set_focus_lock(enabled=false)`, re-read `get_focus_lock_state`, and report it.
> Finally call `get_stage_position` for `TIPFSOffset` once more and report it.
>
> Do not substitute `move_stage_z`, a property write, or another device, and do
> not retry the move more than once.

Expected numbers if the servo overrides the write: a `StageMoveError` whose
result carries `tolerance_um: 0.5`, `within_tolerance: false`, a `measured_um`
that is **not** within 0.5 µm of `requested_um`, a nonzero `elapsed_s` near the
10 s timeout, and a `last_device_status`. The independently read position must
be within 0.5 µm of the reported `measured_um` — that agreement is the point of
the limb, more than the failure itself.

**Two other outcomes are valid results, not runbook failures.** Record whichever
happens, finish the remaining steps, and stop:

- **The move succeeds and reports `within_tolerance: true`.** Then PFS offset
  writes *are* honoured under lock on this rig, which is a useful measurement in
  its own right and contradicts nothing in the block — but the missed-move
  criterion still has no rig evidence and stays owed. Say so plainly.
- **PFS will not lock.** Report the state readings and stop; this limb is not
  runnable today.

## Verbatim agent prompt: export and execute this session — **PASS 2026-08-19, run twice**

> Export the current session with `export_session_script` to
> `block56-export.py`. Do not start a fresh session. Report the export result.

```powershell
python block56-export.py > block56-export.txt 2>&1
$LASTEXITCODE
```

Expected output: `0` for an in-tolerance recorded move. For a recorded missed
move, the expected output is nonzero and `block56-export.txt` contains
`within_tolerance` and `False` with the requested and measured values.
