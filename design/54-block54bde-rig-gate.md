# Blocks 54b + 54d + 54e rig gate — a software region, a guard that scales, a sweep that settles

Implementation ancestor: `2cdb336`

**This runbook replaces `design/54-block54bd-rig-gate.md`, deleted in the same
commit.** That one's Steps 5 and 6 described their camera crop in prose beside
the step instead of inside it; the crop was never made, and both of 54d's
hardware limbs were reported as passing while producing no evidence. If you find
a copy, it is stale.

Run every step on the **Nikon**, from this branch, on a brightfield field whose
sharp structure is a small fraction of the frame. PowerShell, from the checkout.

Save every `.txt` this produces, the emitted script, and the complete Microclaw
transcript. **Write text with `Out-File -Encoding utf8`** — PowerShell's bare `>`
writes UTF-16LE, which is what made the last trip's `pytest` log unreadable.

Three rig facts that shape the run:

- **PFS must read Off for every sweep.** `run_autofocus` refuses against an
  engaged lock. Check before Steps 2, 3, 5 and 6.
- **TIZDrive has intermittent serial timeouts.** This is no longer purely a rig
  fault to re-run: since 54e a plane that never settles raises `StageMoveError`
  after 10 s, on purpose. Record which it was — a bridge timeout, or a typed
  settlement failure — and say how many attempts the step took.
- **Sweeps are slower now.** Each plane costs at least three extra position
  reads and ≥0.1 s. On this microscope block 56 measured a successful move still
  reporting busy at 0.89 s, so a 20-plane coarse+fine sweep may take ~18 s
  longer than you remember. That is the fix working, not a hang.

Steps 1–4 leave the camera ROI alone. **Steps 5 and 6 crop it on purpose**, and
each one is gated by a command that halts if the crop is not already in place.

## Step 0 — pin the implementation and run the suite

```powershell
git merge-base --is-ancestor 2cdb336 HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
python -m pytest -q 2>&1 | Out-File -Encoding utf8 block54bde-pytest.txt
Write-Host "pytest exit code (expected 0):" $LASTEXITCODE
Get-Content block54bde-pytest.txt | Select-Object -Last 3
```

Expected: both printed exit codes **0**. macOS reference at `2cdb336` is **1921
passed, 99 skipped, 2020 collected**. Windows skips more; the **collected** total
is the number that must agree. A different collected total is **NOT TESTED**
until explained.

## Step 1 — draw the box and read its coordinates

In Micro-Manager: bring up the field, snap or run live, and **draw a rectangle
with the ImageJ rectangle tool around the structure you want focused** — a cell
with hard edges, not empty background. Do **not** push it to the camera.

```powershell
python design\54-display-roi-probe.py 2>&1 | Out-File -Encoding utf8 out54-box.txt
Get-Content out54-box.txt | Select-String -Pattern "DRAWN BOX|camera ROI      :"
```

Expected: a `DRAWN BOX = (x, y, w, h)` line. **Write those four numbers down.**
The camera ROI line should read `w=1024 h=1024`.

## Step 2 — the control, measured TWICE

**Without this step the gate cannot fail, and one reading of it is not enough.**
The 2026-08-19 trip ran this call twice and got contrast **0.144** and **0.065** —
the same measurement on the same field, varying 2.22×. A criterion resting on a
single control reading is not measuring the field.

```powershell
python design\54-roi-precondition.py --expect full
Write-Host "precondition exit code (expected 0):" $LASTEXITCODE
```

Do not continue until it prints `PRECONDITION MET`.

Start a normal Microclaw session against the Nikon's reviewed safety config.
Confirm PFS reads Off. Type verbatim:

> Call `run_autofocus(z_range_um=10, z_step_um=0.5)` exactly **twice**, one after
> the other, with no region. Do not set or clear the camera ROI. Do not
> substitute a different tool, a hook, or a script. For **each** call report
> `coarse.contrast`, `coarse.z_positions`, `coarse.measured_z_positions`,
> `converged`, `reason`, `entry_z_um`, `final_z_um`, and whether the `region` and
> `coarse.min_contrast` keys are present.

Expected, for both calls: `converged: false`, the reason naming `< 0.15`, and
**neither `region` nor `coarse.min_contrast` present** — a full-frame regionless
payload keeps its old shape, and their absence is the evidence for that.

**Record both `coarse.contrast` values to three decimals.** Call them `C_full_1`
and `C_full_2`.

If either converges, the premise is absent: this field does not reproduce the
dilution the block is about. Say so and stop.

## Step 3 — the region, scored against its own noise floor

Same session, same Z, PFS still Off. First get the expected threshold — this
command takes your four numbers and both checks they fit and prints what the
step must report, so there is no arithmetic to edit:

```powershell
python design\54-roi-precondition.py --expect full --region 428 387 82 68
Write-Host "precondition exit code (expected 0):" $LASTEXITCODE
```

**Replace `428 387 82 68` with Step 1's four numbers.** Left unedited it prints a
threshold for the wrong box, which is why the step below asks you to compare
against what this printed rather than against a number written here.

Then type verbatim, replacing **only** the four bracketed words with the same
numbers:

> Call `run_autofocus(z_range_um=10, z_step_um=0.5, region=[PROBE_X, PROBE_Y,
> PROBE_W, PROBE_H])` exactly once. Do not set or clear the camera ROI, do not
> call `set_roi`, and do not crop the sensor by any route. Do not substitute a
> different tool, a hook, or a script. Report `coarse.contrast`,
> `coarse.min_contrast`, `coarse.metric_curve`, `coarse.z_positions`,
> `coarse.measured_z_positions`, `converged`, `reason`, the `region` key,
> `entry_z_um`, `final_z_um`, and `focus_metric_at_final`. Then report the
> current camera ROI with the normal read-only ROI tool.

Pasted unedited this fails with a malformed-region refusal naming `PROBE_X`.
That is the intended loud failure; fix the numbers and re-run, it costs no
exposure.

Required, and each is **NOT TESTED** if it does not hold:

- `region` equals **exactly** Step 1's four numbers.
- The camera ROI read-back is **unchanged** — same x, y, width, height as Step 1.
- `coarse.min_contrast` is **present** and equals the number the precondition
  command printed above.

### The criterion

**Raw contrasts are not comparable across region sizes** — `curve_contrast` is
span-over-median of a per-pixel mean, so its noise floor rises as 1/√N and
shrinking the box inflates contrast on noise alone. That mistake invalidated the
first gate and is the one most likely to be repeated. The scale-aware score is
`contrast / min_contrast`. Compute three numbers:

```
C_full_1 / 0.15                  =  ____     (Step 2, first call)
C_full_2 / 0.15                  =  ____     (Step 2, second call)
C_region / coarse.min_contrast   =  ____     (Step 3)
```

- **Region score above BOTH full-frame scores** — restricting the metric helped
  on this field. Report all three.
- **Region score below both** — it did not, and design/54's dilution hypothesis
  is **not supported on this rig**. Report all three and stop. Do **not** retry
  with different boxes until one wins; a hunted-for green is worth nothing, and
  this outcome is worth more than a manufactured pass.
- **Region score between the two full-frame scores** — the control's own spread
  is larger than the effect, and the question is **unanswerable on this field**.
  Report all three and say so. This is a real result, not a failed run.

Either way `converged` will almost certainly be `false`. That is not the
question this step asks.

## Step 4 — the out-of-frame refusal

Same session. These numbers are literal and correct for a 1024×1024 frame:

> Call `run_autofocus(z_range_um=10, z_step_um=0.5, region=[900, 900, 400,
> 400])` exactly once. Report the complete refusal verbatim. Do not retry, do
> not adjust the region, and do not fall back to a full-frame sweep.

Expected: `{"error": "Region [900, 900, 400, 400] does not fit frame [1024, 1024]."}`,
with **no exposure and no Z motion** — the refusal is before the sweep.

## Step 5 — the 54d limb: a cropped sensor must refuse too

**This step crops the camera.** It is the reason 54d exists: the metric does not
care *how* the frame got small, and before this branch a 32×32 camera ROI
converged on pure noise 43% of the time and moved the focus drive.

In Micro-Manager, draw a **small** rectangle in the Preview — aim for roughly
32×32 — and click MM's ROI (crop) button. Then run this, and **do not proceed
until it prints `PRECONDITION MET`**:

```powershell
python design\54-roi-precondition.py --expect cropped
Write-Host "precondition exit code (expected 0):" $LASTEXITCODE
```

**This command is the step.** On the 2026-08-19 trip the crop was skipped, the
sensor stayed at 1024×1024, and the limb reported a pass while testing nothing.
An exit code of 1 here means the step has not started yet.

**Write down the `expected coarse.min_contrast` it printed.** Then, same session:

> Report the current camera ROI with the normal read-only ROI tool. Then call
> `run_autofocus(z_range_um=10, z_step_um=0.5)` exactly once, with **no**
> region. Report `coarse.contrast`, `coarse.min_contrast`, `converged`,
> `moved`, `entry_z_um`, `final_z_um`, `coarse.z_positions`,
> `coarse.measured_z_positions` and `reason`.

Required:

- The ROI read-back shows the small crop, matching what the precondition printed.
- `coarse.min_contrast` is **present** and equals that printed value (≈4.800 at
  32×32, ≈2.400 at 64×64).
- `converged: false` and `moved: false`. **A converged result here, or any
  deliberate Z motion, is a gate failure.**
- `abs(entry_z_um - final_z_um) <= 0.5`. **This is a tolerance check, not an
  equality check** — since 54e `final_z_um` is a measured settled position while
  `entry_z_um` is a single read, so a correct run shows two slightly different
  numbers beside `moved: false`. Equality would fail a correct run.

The pre-fix behaviour cannot be demonstrated on the rig from this branch — it is
covered off-rig by `test_small_camera_roi_pure_noise_does_not_converge_or_move`
and its emitted twin. This limb confirms the refusal fires against real hardware
and real camera noise.

**Leave the ROI cropped — Step 6 needs it.**

## Step 6 — export, then execute standalone twice

Type verbatim:

> Use `export_session_script` to write this session to `block54bde.py`. Report
> the complete result verbatim. Do not edit the emitted file.

Close Microclaw entirely, leaving Micro-Manager and its bridge running. Take the
**absolute path** from the tool's result — the file lands in the configured
workspace, not necessarily this checkout.

Restore the full frame in Micro-Manager (clear-ROI button), then:

```powershell
$Script = "<absolute path reported by export_session_script>"
if (-not (Test-Path $Script)) {
    Write-Host "STOP: `$Script was not edited, or the path is wrong."
    exit 1
}
python design\54-roi-precondition.py --expect full
Write-Host "precondition exit code (expected 0):" $LASTEXITCODE
Select-String -Path $Script -Pattern "_run_autofocus_passes\(mm|does not fit frame|# SKIPPED|def _metric_pixel_count|def settle_stage_move|AUTOFOCUS ENVELOPE"
Write-Host "--- counts ---"
Write-Host "autofocus calls (expected 4):" @(Select-String -Path $Script -Pattern "_run_autofocus_passes\(mm").Count
Write-Host "bounds guards (expected 1 or more):" @(Select-String -Path $Script -Pattern "does not fit frame").Count
Write-Host "pixel-count helper (expected 1):" @(Select-String -Path $Script -Pattern "def _metric_pixel_count").Count
Write-Host "settle contract (expected 1):" @(Select-String -Path $Script -Pattern "def settle_stage_move").Count
Write-Host "envelope prints (expected 4):" @(Select-String -Path $Script -Pattern "AUTOFOCUS ENVELOPE").Count
Write-Host "set_roi calls (expected 0):" @(Select-String -Path $Script -Pattern "set_roi\(").Count
python $Script 2>&1 | Out-File -Encoding utf8 block54bde-standalone-full.txt
Write-Host "standalone exit code (expected 0):" $LASTEXITCODE
Get-Content block54bde-standalone-full.txt
```

Expected: **4** autofocus calls — Step 2's two, Step 3's carrying your four
numbers, Step 5's ending `50, None)`; **1 or more** bounds guards; the
`_metric_pixel_count` helper and the `settle_stage_move` contract **present
exactly once each** (zero for either means 54d's threshold or 54e's settlement
did not travel, and that half of the branch is absent from the script); **4**
envelope prints; **0** `set_roi`. Step 4's refusal appears as a `# SKIPPED`
comment. Exit code **0**, no `NameError`, no traceback.

**The output file must not be empty.** On the 2026-08-19 trip it was 0 bytes and
still read as a pass, because nothing in the emitted script printed. Since 54e
each sweep prints an envelope and an outcome. An empty file now means the script
did not run.

**Then crop the camera again** and re-run — this is the limb that never ran last
time:

```powershell
python design\54-roi-precondition.py --expect cropped
Write-Host "precondition exit code (expected 0):" $LASTEXITCODE
python $Script 2>&1 | Out-File -Encoding utf8 block54bde-standalone-cropped.txt
Write-Host "cropped-frame exit code (expected NONZERO):" $LASTEXITCODE
Get-Content block54bde-standalone-cropped.txt | Select-Object -Last 20
```

Expected: **nonzero** exit and a `RuntimeError` naming your region and the frame
it did not fit — the emitted crop refuses rather than truncating silently.
**Exit code 0 here is a gate failure.**

Restore the full frame in Micro-Manager afterwards (clear-ROI button). Microclaw
does not do it for you.

## Step 7 — the 54e evidence: where was the axis when the frame was taken?

No new hardware run. Read it out of what Steps 2, 3 and 5 already reported.

For each sweep, compare `coarse.z_positions` (requested) against
`coarse.measured_z_positions` (measured, added by 54e) plane by plane, and
report the **largest** absolute difference across all sweeps.

This is the question the last gate could not answer. `sweep_autofocus` used to
snap 50 ms after `wait_for_device` returned, and block 56 measured on this
microscope that a successful move still reported busy at 0.89 s — so every plane
may have been exposed mid-move, which would contaminate every curve and explain
Step 2's 2.22× spread. **It is a hypothesis, and this is the first run that can
measure it.**

- **Differences at or below ~0.5 µm** — the old path was probably fine on this
  axis, and Step 2's spread has another cause. Say so; it does not fail the gate.
- **Differences well above that** — the axis was materially short of target when
  frames were taken, every historical brightfield curve on this rig is suspect,
  and that belongs in design/54 as a finding in its own right.

Report the numbers either way. Do not tune anything to make them small.

## Return evidence

`block54bde-pytest.txt` with Step 0's two exit codes and the **collected** total;
`out54-box.txt` and the `DRAWN BOX` line; every precondition command's printed
output and exit code; the complete agent transcript for Steps 2–6.

Written out side by side: **`C_full_1`, `C_full_2`, `C_region`,
`coarse.min_contrast`, and all three `contrast / min_contrast` scores.**

Step 3's `region` echo and before/after camera ROI; Step 4's verbatim refusal;
Step 5's ROI read-back, `min_contrast`, and `entry_z_um`/`final_z_um` with their
difference; Step 7's largest requested-vs-measured Z difference; the emitted
script, both standalone outputs, both exit codes, and the six printed counts.

Say how many attempts each sweep took, and for any failure whether it was a
bridge timeout or a typed `StageMoveError`. State that the machine was the Nikon
and name the loaded configuration and the field.
