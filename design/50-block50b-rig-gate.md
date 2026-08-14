# Block 50b rig gate — a session that starts out of bounds says so

Implementation ancestor: `8e72c6f`

Run every step on **M5**, from this branch, in PowerShell. **M5 is the
reproducer**: on 2026-08-12 its stage sat at Y = 11968.9 µm against a configured
`y_max` of 5000 µm, and Microclaw said nothing for 92 history lines.

**Do not edit `%APPDATA%\microclaw\safety_config.yaml` before step 3.** The wrong
limit is the fixture. Steps 1 and 2 need it exactly as the rig runs it today.

Preserve `block50b-pytest.txt` and both saved transcripts into the evidence
archive as `50b-m5`.

What this gate proves, in one line: a stage outside its configured envelope is
reported at the first read and stated to the operator, the refusals name the
axis, and the report disappears once the envelope is corrected.

## Step 0 — pin, test, and record the starting position

Close Microclaw, then paste:

```powershell
git merge-base --is-ancestor 8e72c6f HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
python -m pytest -q > block50b-pytest.txt 2>&1
Write-Host "pytest exit code (expected 0):" $LASTEXITCODE
Get-Content block50b-pytest.txt -Tail 3
Select-String -Path "$env:APPDATA\microclaw\safety_config.yaml" -Pattern "x_min","x_max","y_min","y_max","z_min","z_max"
```

Expected: ancestor exit **0**, pytest exit **0**, and the stage limits printed.
macOS at this implementation was **1788 passed / 99 skipped / 3 warnings**
(baseline at `32e74d0` was 1781 / 99 / 3; this block adds 7 tests). Record M5's
exact passed and skipped numbers. Windows skips more than macOS — 49a measured
25 more — so a higher skip count is expected; a higher count than M5's own
previous full run is **NOT TESTED**, not a pass.

**Write down the configured `y_max` and the stage's current X/Y/Z from the
Micro-Manager stage display before starting Microclaw.** Every later step
compares against these, and they must come from the GUI, not from a tool.

If the stage is *inside* the configured envelope on the day you run this, steps 1
and 2 cannot be run as written. Do **not** drive the stage out of bounds to
create the condition — the envelope exists because the stage can reach the
objective. Instead, lower `y_max` in the safety config to a value below the
current Y, restart, and say so in the report — the code path is identical, the
fixture is honest, and nothing moves.

## Step 1 — the first read says it, and the agent says it out loud

Start Micro-Manager and Microclaw as normal. Then, as the **very first thing** in
a fresh session, say this verbatim:

> Use `get_system_state` exactly once and report the complete result verbatim.
> Do not call any other tool and do not change any hardware state.

Expected, all four:

- Exactly **1** call.
- The result carries an `out_of_bounds` field — a list of strings — naming **Y**,
  the value **11968.9** (or the day's actual Y) and the limit **5000.0** (or the
  day's configured `y_max`). Both numbers must be present; a field that says only
  "out of bounds" is a partial pass — record it as such.
- **The agent tells you, in its reply, before doing anything else.** A result
  that carries the field while the reply says "system state looks normal" is a
  **gate failure**, and the more important half of this step. The field alone was
  never the fix.
- No stage motion. Confirm from the Micro-Manager stage display, not the tool
  result — the numbers must equal what you wrote down in step 0.

If M5's safety config declares any `named_stages`, the result also carries a
`named_stages` block reading each one's position, and a declared stage outside
its own limits adds its message to the same `out_of_bounds` list. Both are
expected. A declared stage that cannot be read reads `"unavailable"` rather than
failing the call — record it if you see one; it is a config/rig mismatch worth
knowing about, and it does not fail this gate.

## Step 2 — the refusals name the axis

Still in the same session, say this verbatim:

> Use `validate_positions` exactly once on the current stage position. Report
> the complete result verbatim. Do not move the stage.

Expected: the rejection reason names **Y**, the value and the limit. The exact
string `Rejected by the current XY safety guard.` is the defect under repair — if
it appears, record it verbatim and the step has failed.

Then say this verbatim:

> Use `get_xy_position` exactly once and report the complete result verbatim.

Expected: the same out-of-bounds report as step 1. An agent that orients with
this tool instead of `get_system_state` must not be left in the dark.

## Step 3 — correct the envelope, and the report goes away

This is the half that catches a field which is always on, and it is also the
point of the whole block: **the session that produced this finding should end
with the rig usable.**

**`y_max` is not the stage's travel limit.** The stage can drive much further
than is safe — far enough to hit the objective. This number is the envelope
*you* are willing to let software move within, and it must be narrower than the
hardware's range. Do not read it off the stage controller, a manufacturer
specification, or a Micro-Manager device property; all three will be too wide.

Before changing anything, answer this, because it is worth more than the rest of
the step: **the stage was at Y = 11968.9 on 2026-08-12 and imaging beads
successfully, so `y_max: 5000` was not protecting anything at that position.**
Which is it?

- The envelope was authored too narrow, and 5000 excludes usable sample area; or
- the envelope was authored against a **different origin** — the stage was
  re-homed since, or the value came from another rig's config — in which case
  every stage bound in the file is suspect, not just this one.

Write down which, and what you based it on. The step-10 design gate records this
and the next session on M5 depends on it.

Then close Microclaw and set `y_max` to the furthest Y you are willing to let
Microclaw drive to without risking a collision, **judged by you at the rig**,
with margin. If the answer above was "different origin", check `x_min`/`x_max`
and `z_min`/`z_max` against where the stage actually operates while you are in
the file.

Edit `%APPDATA%\microclaw\safety_config.yaml` to that `y_max`, then paste:

```powershell
Select-String -Path "$env:APPDATA\microclaw\safety_config.yaml" -Pattern "y_max"
```

Restart Microclaw and say this verbatim:

> Use `get_system_state` exactly once and report the complete result verbatim.

Expected:

- **No out-of-bounds field at all** — not an empty one, not one saying "within
  limits". Absent. An always-present field reads as a warning and will be
  reported as one.
- The reply does not mention stage limits.

## Step 4 — and now the move that could not happen

Say this verbatim:

> Move the stage 50 µm in X from its current position, then move it back. Report
> every tool call with its complete result.

Expected: both moves succeed. This is the action refused on 2026-08-12 at line
94, and it closes the loop the original session could not.

If it still refuses, record the refusal verbatim — the corrected `y_max` is
wrong, or a second limit is involved, and that is a finding worth more than the
rest of this gate.

## Not in this gate, deliberately

- Any check of the **Z** or named-stage limbs against a real violation. M5's Z
  and named stages are inside their envelopes and driving them out to prove a
  message is not a trade this gate makes. Those limbs are covered off-rig.
- Microclaw moving the stage back into bounds on its own. It must not, and step 1
  confirms it did not.

## Reporting

Save both browser conversations with the normal transcript-save control as
`block50b-m5-before` and `block50b-m5-after`. Return, per step: the tool calls
made, the verbatim results, **and the agent's own reply text for step 1** — that
reply is the evidence for half the block. Include the corrected `y_max` and where
the number came from. Report what happened, including anything that looked wrong;
do not diagnose the system or judge whether the evidence is valid.
