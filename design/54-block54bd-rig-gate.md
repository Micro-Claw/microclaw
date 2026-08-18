# Blocks 54b + 54d rig gate — a software region, and a guard that scales with it

Implementation ancestor: `5ed5fef`

**This runbook replaces `design/54-block54b-rig-gate.md`, which is deleted in the
same commit.** That one was written before 54d and its Step 3 criterion was
measured invalid by its own results — do not run it if you find a copy.

Run every step on the **Nikon** from this branch, on a brightfield field whose
sharp structure is a small fraction of the frame. Use PowerShell from the
checkout. Save `block54bd-pytest.txt`, `out54-box.txt`, the emitted script, both
standalone outputs, and the complete Microclaw transcript.

Two rig facts that shape the run:

- **TIZDrive has intermittent serial timeouts.** `Wait for device "TIZDrive"
  timed out after 5000ms` is a rig fault, not a gate failure — re-run the step
  and say how many attempts it took. Do not change the step to avoid it.
- **PFS must read Off for every sweep.** `run_autofocus` refuses against an
  engaged lock. Check before Steps 2, 3 and 5.

Steps 1–4 leave the camera ROI alone. **Steps 5 and 6 crop it on purpose** and
say so.

## Step 0 — pin the implementation and run the suite

```powershell
git merge-base --is-ancestor 5ed5fef HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
python -m pytest -q > block54bd-pytest.txt 2>&1
Write-Host "pytest exit code (expected 0):" $LASTEXITCODE
Get-Content block54bd-pytest.txt | Select-Object -Last 3
```

Expected: both printed exit codes **0**. macOS reference at `5ed5fef` is **1905
passed, 99 skipped, 2004 collected**. Windows skips more; the **collected** total
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
Record the camera ROI line too; it should read `w=1024 h=1024`.

## Step 2 — the control: full frame on this field

**Without this step the gate cannot fail.** Start a normal Microclaw session
against the Nikon's reviewed safety config. Confirm PFS reads Off. Type
verbatim:

> Call `run_autofocus(z_range_um=10, z_step_um=0.5)` exactly once, with no
> region. Do not set or clear the camera ROI. Do not substitute a different
> tool, a hook, or a script. Report the complete result including
> `coarse.contrast`, `coarse.metric_curve`, `converged`, `reason`, and whether
> `region` or `coarse.min_contrast` keys are present.

Expected: `converged: false`, `coarse.contrast` well below 0.15 (the 2026-08-18
runs read 0.015), the reason naming `< 0.15`, and **neither `region` nor
`coarse.min_contrast` present** — a full-frame regionless payload keeps exactly
its old shape, and their absence is the evidence for that.

If this converges, the premise is absent: this field does not reproduce the
dilution the block is about. Say so and stop.

**Record `coarse.contrast` to three decimals.** Call it `C_full`.

## Step 3 — the region, scored against its own noise floor

Same session, same Z, PFS still Off. Type verbatim, replacing **only** the four
bracketed words with Step 1's numbers:

> Call `run_autofocus(z_range_um=10, z_step_um=0.5, region=[PROBE_X, PROBE_Y,
> PROBE_W, PROBE_H])` exactly once. Do not set or clear the camera ROI, do not
> call `set_roi`, and do not crop the sensor by any route. Do not substitute a
> different tool, a hook, or a script. Report the complete result including
> `coarse.contrast`, `coarse.min_contrast`, `coarse.metric_curve`, `converged`,
> `reason`, the `region` key, and `focus_metric_at_final`. Then report the
> current camera ROI with the normal read-only ROI tool.

Pasted unedited this fails with a malformed-region refusal naming `PROBE_X`.
That is the intended loud failure; fix the numbers and re-run, it costs no
exposure.

Required, and each is **NOT TESTED** if it does not hold:

- `region` equals **exactly** Step 1's four numbers.
- The camera ROI read-back is **unchanged** — same x, y, width, height as Step 1.
- `coarse.min_contrast` is **present**, and equals the scaled threshold. Check it
  with this block, editing only the first line (left as zeros so an unedited run
  throws a divide-by-zero rather than printing a wrong number):

```powershell
$w = 0; $h = 0     # <-- put your box's width and height here
"expected coarse.min_contrast: {0:N3}" -f (0.15 * [Math]::Sqrt(1048576.0 / ($w * $h)))
```

### The criterion — and why it is not the one the last gate used

54b's gate asked for the region's `contrast` to beat the full frame's. It did,
0.015 → 0.037, **and that proved nothing**: `curve_contrast` is span-over-median
of a per-pixel mean, so its noise floor rises as 1/√N and shrinking to that box
inflates contrast by ~5.2× on noise alone. Measured on structureless synthetic
frames the inflation was 5.66× — *larger* than the rig's 2.47×.

The scale-aware score is **`contrast / min_contrast`**: how far the curve rises
above the noise floor *for its own pixel count*. Compute both:

```
C_full / 0.15                    =  ____     (Step 2)
C_region / coarse.min_contrast   =  ____     (Step 3)
```

On 54b's numbers these were **0.100** and **0.048** — by this measure the drawn
box was *half as good* as the whole frame. **That is the result to expect, and
it is a real finding, not a failed run.**

- **Region score > full-frame score** — restricting the metric genuinely helped
  on this field. Report both numbers.
- **Region score ≤ full-frame score** — it did not, and design/54's dilution
  hypothesis is **not supported on this rig**. Report both numbers and stop.
  Do **not** retry with different boxes until one wins; a hunted-for green is
  worth nothing, and this outcome is worth more than a manufactured pass.

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
32×32 — and click MM's ROI (crop) button. Then, same session, type verbatim:

> Report the current camera ROI with the normal read-only ROI tool. Then call
> `run_autofocus(z_range_um=10, z_step_um=0.5)` exactly once, with **no**
> region. Report `coarse.contrast`, `coarse.min_contrast`, `converged`,
> `moved`, `entry_z_um`, `final_z_um` and `reason`.

Expected: the ROI read-back shows the small crop; `coarse.min_contrast` is now
**present and large** (≈4.8 at 32×32, ≈2.4 at 64×64 — check it with the Step 3
PowerShell block using the *cropped ROI's* width and height); `converged: false`,
`moved: false`, and `entry_z_um == final_z_um`. **A converged result here, or
any Z motion, is a gate failure.**

The pre-fix behaviour cannot be demonstrated on the rig from this branch — it is
covered off-rig by `test_small_camera_roi_pure_noise_does_not_converge_or_move`
and its emitted twin, both watched failing with the stage moving 1 µm. This limb
confirms the refusal fires against real hardware and real camera noise.

**Leave the ROI cropped — Step 6 needs it.**

## Step 6 — export, then execute standalone twice

Type verbatim:

> Use `export_session_script` to write this session to `block54bd.py`. Report the
> complete result verbatim. Do not edit the emitted file.

Close Microclaw entirely, leaving Micro-Manager and its bridge running. Take the
**absolute path** from the tool's result — the file lands in the configured
workspace, not necessarily this checkout.

First, restore the full frame in Micro-Manager (MM's clear-ROI button) and
confirm it reads 1024×1024. Then:

```powershell
$Script = "<absolute path reported by export_session_script>"
if (-not (Test-Path $Script)) {
    Write-Host "STOP: `$Script was not edited, or the path is wrong."
    exit 1
}
Select-String -Path $Script -Pattern "_run_autofocus_passes\(mm|does not fit frame|# SKIPPED|def _metric_pixel_count"
Write-Host "--- counts ---"
Write-Host "autofocus calls (expected 3):" @(Select-String -Path $Script -Pattern "_run_autofocus_passes\(mm").Count
Write-Host "bounds guards (expected 1 or more):" @(Select-String -Path $Script -Pattern "does not fit frame").Count
Write-Host "pixel-count helper (expected 1):" @(Select-String -Path $Script -Pattern "def _metric_pixel_count").Count
Write-Host "set_roi calls (expected 0):" @(Select-String -Path $Script -Pattern "set_roi\(").Count
python $Script > block54bd-standalone.txt 2>&1
Write-Host "standalone exit code (expected 0):" $LASTEXITCODE
Get-Content block54bd-standalone.txt
```

Expected: **3** autofocus calls — Step 2 ending `50, None)`, Step 3 carrying your
four numbers, Step 5 ending `50, None)` again; **1 or more** bounds guards; the
`_metric_pixel_count` helper **present** (zero means the frame-derived threshold
did not travel, and the whole 54d fix is absent from the script); **0** `set_roi`.
Step 4's refusal appears as a `# SKIPPED` comment. Exit code **0**, no
`NameError`, no traceback.

**Then crop the camera again** — a small rectangle plus MM's ROI button, as in
Step 5 — and re-run the same script:

```powershell
python $Script > block54bd-standalone-cropped.txt 2>&1
Write-Host "cropped-frame exit code (expected NONZERO):" $LASTEXITCODE
Get-Content block54bd-standalone-cropped.txt | Select-Object -Last 20
```

Expected: **nonzero** exit and a `RuntimeError` naming your region and the frame
it did not fit — the emitted crop refuses rather than truncating silently.
**Exit code 0 here is a gate failure.**

Restore the full frame in Micro-Manager afterwards (clear-ROI button). Microclaw
does not do it for you.

## Return evidence

Return `block54bd-pytest.txt` with Step 0's exit codes and the **collected**
total; `out54-box.txt` and the `DRAWN BOX` line; the complete agent transcript
for Steps 2–6; **`C_full`, `C_region`, `coarse.min_contrast`, and both
`contrast / min_contrast` scores written out side by side**; Step 3's `region`
echo and before/after camera ROI; Step 4's verbatim refusal; Step 5's ROI
read-back, `min_contrast`, and `entry_z_um`/`final_z_um`; the emitted script,
both standalone outputs, both exit codes, and the four printed counts. Say how
many attempts each sweep took and whether any died on a TIZDrive timeout. State
that the machine was the Nikon and name the loaded configuration and the field.
