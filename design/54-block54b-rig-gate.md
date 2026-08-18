# Block 54b rig gate — a software region on the focus metric

Implementation ancestor: `e144759` (review fixes on top of `f2ffd26`)

Run every step on the **Nikon** from this branch, on **the same brightfield
field as the session of 2026-08-18** if it can be found — a field whose sharp
structure is a small fraction of the frame. Use PowerShell from the checkout.
Save `block54b-pytest.txt`, `out54-box.txt`, the emitted script, the standalone
output, and the complete Microclaw transcript.

**Nothing in this gate changes the camera ROI except Step 5, which says so.**
That is the point of the block: the metric narrows and the sensor does not.

Two rig facts that shape the run:

- **TIZDrive has intermittent serial timeouts.** A sweep can die with
  `Wait for device "TIZDrive" timed out after 5000ms`. That is a rig fault, not
  a gate failure — re-run the step and say how many attempts it took. Do not
  change the step to avoid it.
- **PFS must read Off for every sweep.** `run_autofocus` refuses against an
  engaged lock. Check it before Step 2 and again before Step 3.

## Step 0 — pin the implementation and run the suite

Paste this literal PowerShell block:

```powershell
git merge-base --is-ancestor e144759 HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
python -m pytest -q > block54b-pytest.txt 2>&1
Write-Host "pytest exit code (expected 0):" $LASTEXITCODE
Get-Content block54b-pytest.txt | Select-Object -Last 3
```

Expected: both printed exit codes are **0**. Record the exact passed, skipped
and collected totals. Reference totals from macOS at `e144759` are **1898
passed, 99 skipped**; Windows skip counts may differ, and a higher skip count
than a previous Nikon full-suite run is **NOT TESTED** until explained.

## Step 1 — draw the box and read its coordinates

In Micro-Manager: bring up the field, snap or run live so there is an image in
the Preview, and **draw a rectangle with the ImageJ rectangle tool around the
structure you want focused** — a cell with hard edges, not empty background.
Do **not** push it to the camera.

```powershell
python design\54-display-roi-probe.py 2>&1 | Out-File -Encoding utf8 out54-box.txt
Get-Content out54-box.txt | Select-String -Pattern "DRAWN BOX|camera ROI      :"
```

Expected: a `DRAWN BOX = (x, y, w, h)` line. **Write those four numbers down —
every later step uses them.** Also record the camera ROI line; it should be
`x=0 y=0 w=1024 h=1024` unless the session already cropped.

If `DRAWN BOX` does not print, the rectangle is not on the Preview window.
Redraw it and re-run before going on.

## Step 2 — the control: full frame on this field must be flat

**Without this step the gate cannot fail.** A converging regional sweep proves
nothing unless the same field, in the same session, refuses to converge on the
full frame. If Step 2 converges, the premise is absent — say so and stop; this
field does not reproduce the dilution the block is about, and Step 3's result
would be uninterpretable.

Start a normal Microclaw agent session against the Nikon's reviewed safety
config. Confirm PFS reads Off. Type this verbatim:

> Call `run_autofocus(z_range_um=10, z_step_um=0.5)` exactly once, with no
> region. Do not set or clear the camera ROI. Do not substitute a different
> tool, a hook, or a script. Report the complete result including
> `coarse.contrast`, `coarse.metric_curve`, `converged`, `reason`, and whether a
> `region` key is present.

Expected: `converged: false` with a flat-metric or edge-peak `reason`,
`coarse.contrast` **well below 0.15** (the 2026-08-18 session read 0.007 and
0.018 on this field), and **no `region` key in the payload at all**. Record the
contrast to three decimals — Step 3 is compared against this exact number.

A missing `region` key is required, not incidental: it is the evidence that a
regionless payload kept its old shape.

## Step 3 — the mechanism: the same sweep, restricted to the drawn box

Same session, same Z, PFS still Off. Type this verbatim, replacing **only** the
four bracketed words with the numbers Step 1 printed:

> Call `run_autofocus(z_range_um=10, z_step_um=0.5, region=[PROBE_X, PROBE_Y,
> PROBE_W, PROBE_H])` exactly once. Do not set or clear the camera ROI, do not
> call `set_roi`, and do not crop the sensor by any route — the region is a
> software crop and the camera must stay as it is. Do not substitute a different
> tool, a hook, or a script. Report the complete result including
> `coarse.contrast`, `coarse.metric_curve`, `converged`, `reason`, the `region`
> key, and `focus_metric_at_final`. Then report the current camera ROI with the
> normal read-only ROI tool.

If you paste this unedited, the call fails with a malformed-region refusal
naming `PROBE_X`. That is the intended loud failure — fix the numbers and
re-run; it costs no exposure.

Expected:

- `region` in the payload equals **exactly** the four numbers Step 1 printed. If
  it does not, you typed a different box and the step is **NOT TESTED**.
- The camera ROI read-back is **unchanged from Step 1** — same x, y, width,
  height. Any change means something cropped the sensor and the step is **NOT
  TESTED**, whatever the metric did.
- `coarse.contrast` is **higher than Step 2's**. Report both numbers together.

**On what counts as a pass, honestly.** The block's claim is that restricting the
region removes the dilution. The strong outcome is contrast crossing **0.15**
with `converged: true` and `peak_interior: true` at a Z you agree is focus.
**A weaker outcome is a real result, not a failed run**: if contrast rises
substantially but stays under 0.15, or the curve is bimodal, record it and stop.
Brightfield has a genuine second problem — a thin transparent object has minimum
contrast at focus — and design/54 says explicitly that this block does not solve
it. **Do not retry with different regions until one converges.** One box, one
control, one comparison. A hunted-for green is worth nothing.

## Step 4 — the refusal fires, and does not clamp

Same session. Type this verbatim — the numbers are literal and correct as
written for a 1024×1024 frame:

> Call `run_autofocus(z_range_um=10, z_step_um=0.5, region=[900, 900, 400,
> 400])` exactly once. Report the complete refusal verbatim. Do not retry, do
> not adjust the region, and do not fall back to a full-frame sweep.

Expected: an `error` payload naming both `[900, 900, 400, 400]` and the frame it
did not fit, **and no exposure taken and no Z motion** — the refusal is before
the sweep. A result that silently measured a smaller region is the exact defect
this limb exists to catch. If the camera ROI is not 1024×1024 on your rig, these
numbers may fit; in that case report the ROI and skip this limb rather than
inventing new numbers.

## Step 5 — export, then execute standalone against a smaller frame

This step runs the emitted script twice and **is the only step that changes the
camera ROI**. It also moves Z and takes exposures, because the emitted script
re-runs the sweeps. Read it through before starting.

In the Step-2/3/4 session, type this verbatim:

> Use `export_session_script` to write this session to `block54b.py`. Report the
> complete result verbatim. Do not edit the emitted file.

Close Microclaw entirely, leaving Micro-Manager and its bridge running. Take the
**absolute path** out of the tool's reported result — `export_session_script`
resolves through the configured workspace and the file may not land in this
checkout.

```powershell
$Script = "<absolute path reported by export_session_script>"
if (-not (Test-Path $Script)) {
    Write-Host "STOP: `$Script was not edited, or the path is wrong. Paste the"
    Write-Host "absolute path export_session_script reported and re-run."
    exit 1
}
Select-String -Path $Script -Pattern "_run_autofocus_passes\(mm|does not fit frame|# SKIPPED"
Write-Host "--- counts ---"
Write-Host "autofocus calls (expected 2):" @(Select-String -Path $Script -Pattern "_run_autofocus_passes\(mm").Count
Write-Host "bounds guards (expected 1 or more):" @(Select-String -Path $Script -Pattern "does not fit frame").Count
Write-Host "set_roi calls (expected 0):" @(Select-String -Path $Script -Pattern "set_roi\(").Count
python $Script > block54b-standalone.txt 2>&1
Write-Host "standalone exit code (expected 0):" $LASTEXITCODE
Get-Content block54b-standalone.txt
```

Expected, and these are the numbers this session produces — Step 2 emits one
call, Step 3 emits the other, Step 4 was refused and emits a `# SKIPPED`
comment carrying its error text:

- **autofocus calls: 2.** One line ends `50, None)` (Step 2, regionless) and one
  ends with your four numbers (Step 3). Both must appear.
- **bounds guards: 1 or more** — the check inlined with `_run_autofocus_passes`.
  **Zero means the guard did not travel**, and the cropped-frame run below is
  then guaranteed to pass silently for the wrong reason.
- **set_roi calls: 0.** Any `set_roi` in this script means the session cropped
  the sensor somewhere and Step 3 is **NOT TESTED**.

Standalone exit code **0**, no `NameError`, no traceback. Preserve the emitted
script and the output file.

**Then make the frame too small on purpose.** In Micro-Manager, draw a small
rectangle in the Preview and click MM's ROI (crop) button so the camera ROI
really shrinks — this is the one place the gate does that. Confirm the new ROI
in MM, then:

```powershell
python $Script > block54b-standalone-cropped.txt 2>&1
Write-Host "cropped-frame exit code (expected NONZERO):" $LASTEXITCODE
Get-Content block54b-standalone-cropped.txt | Select-Object -Last 20
```

Expected: a **nonzero** exit code and a `RuntimeError` naming the region and the
frame it did not fit. **Exit code 0 here is a failure of the gate**: it means the
emitted crop truncated silently and scored the metric over the wrong pixels,
which is the defect found in review of this block. Restore the full frame in MM
afterwards (MM's clear-ROI button); Microclaw does not do it for you.

## Return evidence

Return `block54b-pytest.txt` with Step 0's two exit codes and totals;
`out54-box.txt` and the `DRAWN BOX` line; the complete agent transcript for
Steps 2–5; **Step 2's and Step 3's `coarse.contrast` side by side**, with both
`metric_curve`s; Step 3's `region` echo and the before/after camera ROI;
Step 4's verbatim refusal; the emitted script, `block54b-standalone.txt`,
`block54b-standalone-cropped.txt`, both exit codes, and the four printed counts.
Say how many attempts each sweep took and whether any died on a TIZDrive
timeout. State that the machine was the Nikon and name the loaded configuration
and the field.
