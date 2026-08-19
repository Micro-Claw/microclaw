# Block 54c rig gate — `region="drawn"` reads the box the operator drew

Implementation ancestor: `42e08b8`

Run every step on the **Nikon**, from this branch, on a brightfield field whose
sharp structure is a small fraction of the frame. PowerShell, from the checkout.

Save every `.txt` this produces, the emitted script, and the complete Microclaw
transcript. **Write text with `Out-File -Encoding utf8`** — PowerShell's bare `>`
writes UTF-16LE, which made an earlier trip's pytest log unreadable.

**What this block is, and is not.** 54b shipped `region=[x, y, w, h]` and two
Nikon trips showed it did **not** improve the focus score on this field. 54c is
**ergonomics**: the same capability reached by drawing a box instead of typing
four numbers. Nothing here is evidence about whether restricting the metric
helps focus, and no step below asks that question.

Three rig facts that shape the run:

- **PFS must read Off for every sweep.** `run_autofocus` refuses against an
  engaged lock. Check before Steps 2, 3 and 6.
- **TIZDrive has intermittent serial timeouts.** Since 54e a plane that never
  settles raises `StageMoveError` after 10 s, on purpose. Record which it was —
  a bridge timeout or a typed settlement failure — and how many attempts.
- **Sweeps are slow.** Each plane costs three extra position reads and ≥0.1 s.
  A 20-plane coarse+fine sweep takes ~18 s longer than it used to. That is 54e
  working, not a hang.

## Step 0 — pin the implementation and run the suite

```powershell
git merge-base --is-ancestor 42e08b8 HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
python -m pytest -q 2>&1 | Out-File -Encoding utf8 block54c-pytest.txt
Write-Host "pytest exit code (expected 0):" $LASTEXITCODE
Get-Content block54c-pytest.txt | Select-String -Pattern "passed|failed|error"
```

Expected: both printed exit codes **0**, and a summary line reading **`1945
passed, 99 skipped`** (macOS reference at `f02c316`; Windows skips more, so the
skip count may differ). **The number to look at is `failed`. Any nonzero failure
count stops the gate** — the last trip ran with a red suite because its Step 0
emphasised the collected total.

## Step 1 — draw a box, and confirm the bridge can read it

In Micro-Manager: bring up the field, snap or go live so **Preview** is open,
and **draw a rectangle with the ImageJ rectangle tool** around structure with
hard edges. Do **not** push it to the camera.

```powershell
python design\54-display-roi-probe.py 2>&1 | Out-File -Encoding utf8 out54c-box.txt
Get-Content out54c-box.txt | Select-String -Pattern "DRAWN BOX|camera ROI      :|R2 |R3 "
```

Expected: `R2 PASS`, `R3 PASS`, a `DRAWN BOX = (x, y, w, h)` line, and a camera
ROI of `w=1024 h=1024`. **Write those four numbers down as the PROBE BOX.**

This step is zero-exposure and it is not redundant with Step 2: the unit tests
for the reader use `MagicMock` image objects, which answer to any method name at
all. **Only the rig can say whether `get_image_plus`, `get_roi` and `get_bounds`
are what the bridge actually calls them.** If Step 2 fails with an
`AttributeError` naming one of those, this step's output is what identifies it.

## Step 2 — the mechanism: `region="drawn"`

Start a normal Microclaw session against the Nikon's reviewed safety config.
Confirm PFS reads Off. Type verbatim:

> Call `run_autofocus(z_range_um=10, z_step_um=0.5, region="drawn")` exactly
> once. Pass the string `"drawn"` — do **not** pass a literal `[x, y, w, h]`
> region, do not read the box yourself, do not substitute a different tool, a
> hook, or a script, and do not set or clear the camera ROI. Report
> `coarse.contrast`, `coarse.min_contrast`, `converged`, `reason`, the `region`
> key, `entry_z_um` and `final_z_um`. Then report the current camera ROI with
> the normal read-only ROI tool.

Required, and each is **NOT TESTED** if it does not hold:

- The `region` key is **present** and equals Step 1's PROBE BOX exactly, all
  four numbers. This is the whole block: the string resolved to the operator's
  rectangle.
- `coarse.min_contrast` is **present** — the resolved pixel count, not the full
  frame, set the threshold. A missing key means the box never reached 54d's
  scaling.
- The camera ROI read-back is **unchanged** at 1024×1024. A software crop must
  disturb no camera setting.

**If the agent passes a literal region instead of `"drawn"`, the step has not
run.** An outcome-shaped step gets satisfied by the better route, and the
mechanism under test never fires — that is how a whole trip was lost on 52b.
Re-issue the prompt.

**Record the `region` value the payload echoed. Call it the ECHOED BOX.**

## Step 3 — the same box, typed literally, must reach the same threshold

Same session, same Z, PFS still Off. Type verbatim, replacing **only** the four
bracketed words with the ECHOED BOX from Step 2:

> Call `run_autofocus(z_range_um=10, z_step_um=0.5, region=[ECHO_X, ECHO_Y,
> ECHO_W, ECHO_H])` exactly once. Do not set or clear the camera ROI. Report
> `coarse.contrast`, `coarse.min_contrast`, `converged`, `reason`, the `region`
> key, `entry_z_um` and `final_z_um`.

Pasted unedited this fails with a malformed-region refusal naming `ECHO_X`. That
is the intended loud failure; fix the numbers and re-run, it costs no exposure.

**This step failed on 2026-08-19 and is the reason for this re-gate.** The agent
sent `"region": "[726, 591, 174, 171]"` — the array as a quoted string — four
times, three of them after the operator asked for an array in plain words, and
every call was refused as malformed. The schema then carried a `oneOf` with no
top-level `type`. It now declares `type: ["array", "string"]`, and a stringified
array is parsed rather than refused, so **both** the array and the quoted form
must now work. Report the raw `region` value the agent sent, exactly as it
appears in the transcript, alongside the payload's `region` echo — this step is
measuring what the model emits, and only a live session can measure that.

Required:

- `region` equals the ECHOED BOX.
- **`coarse.min_contrast` is identical to Step 2's, to every digit.** Same pixel
  count, same threshold. This is the criterion.

**`coarse.contrast` is NOT required to match, and a difference is not a
failure.** The 2026-08-19 trip measured the same call on the same field twice at
`0.144` and `0.065` — a 2.22× spread from camera noise alone. Report both
contrasts; draw no conclusion from their difference.

## Step 4 — the three refusals, each named

Same session. Each limb below must produce **its own** refusal. A different
refusal is **NOT TESTED**, not a pass — three failure modes that all "refuse" are
three different mechanisms.

### 4a — nothing drawn

In Micro-Manager, with Preview still open, clear the selection: **Edit ▸
Selection ▸ Select None** (`Ctrl+Shift+A`). Confirm it is really gone:

```powershell
python design\54-display-roi-probe.py 2>&1 | Out-File -Encoding utf8 out54c-noselection.txt
Get-Content out54c-noselection.txt | Select-String -Pattern "R3 |getRoi"
```

Expected: R3 **SKIP**, reporting `getRoi() -> null`. **Do not continue until it
does** — if a selection is still live this limb tests nothing. Then:

> Call `run_autofocus(z_range_um=10, z_step_um=0.5, region="drawn")` exactly
> once. Report the complete refusal verbatim. Do not draw a box, do not
> substitute a literal region, and do not fall back to a full-frame sweep.

Expected, verbatim: `{"error": "Region 'drawn' has no selection. Draw a
selection on the Preview window and try again."}` — with **no exposure and no Z
motion**.

### 4b — a stale box, after the frame changed under it

Draw a **large** rectangle in Preview — at least half the frame — then change
the frame under it. Prefer **binning 2** (Device Property Browser ▸ camera ▸
Binning), which halves the frame and leaves the window in place.

Do **not** use `54-roi-precondition.py` here: its `--expect cropped` wants a
sensor under 256×256, and binning 2 gives 512×512, which that script reads as a
*full* frame. Use this instead — it reads exactly the two numbers
`_validate_metric_region` compares the box against:

```powershell
python -c "from pycromanager import Core; c = Core(port=4827); w = int(c.get_image_width()); h = int(c.get_image_height()); print('frame is', w, 'x', h); raise SystemExit(0 if w * h < 1048576 else 1)"
Write-Host "frame-changed exit code (expected 0):" $LASTEXITCODE
```

**This command is the step.** Exit 1 means the frame is still 1024×1024, the
frame never changed, and the limb has not started. Then:

> Call `run_autofocus(z_range_um=10, z_step_um=0.5, region="drawn")` exactly
> once. Report the complete refusal verbatim.

Expected: a refusal of the form `Region [x, y, w, h] does not fit frame [w, h].`
naming **both** the drawn box and the **new, smaller** frame. It must refuse,
never clamp.

If instead you get 4a's "has no selection" refusal, the selection did not
survive the frame change: that is a **different mechanism** and this limb is
**NOT TESTED**. Restore the frame, redraw, and try the other route (camera ROI
crop rather than binning, or vice versa). Report which route was used.

**Restore full frame and binning 1 before continuing.**

### 4c — the Preview window is closed

Close the MM Preview window. Close **every other ImageJ image window too** — the
fallback route is `WindowManager.getCurrentImage()`, which follows window focus,
so an open dataset window is a box this could read by mistake.

> Call `run_autofocus(z_range_um=10, z_step_um=0.5, region="drawn")` exactly
> once. Report the complete refusal verbatim.

Expected, verbatim: `{"error": "Region 'drawn' cannot be read because no Preview
display is reachable. Open Preview, draw a selection, and try again."}`

### 4d — the fallback must not read the wrong window

Re-open Preview and draw a **small** box in it. Then open any saved TIFF in
ImageJ (`File ▸ Open`), draw a **clearly different, larger** box on *that*
window, and leave that window focused.

The probe reads through `WindowManager.getCurrentImage()` — the *fallback*
route, the one that follows focus — so it is the instrument for this limb:

```powershell
python design\54-display-roi-probe.py 2>&1 | Out-File -Encoding utf8 out54c-twowindows.txt
Get-Content out54c-twowindows.txt | Select-String -Pattern "DRAWN BOX|windows matching"
```

**The probe's `DRAWN BOX` must be the OTHER window's box**, not Preview's. If it
reports Preview's box, focus did not move and the limb has not started — click
the other window and re-run. **Write the probe's box down.** Then:

> Call `snap_and_analyze(region="drawn")` exactly once. Report
> `metric_valid_for.region` and `mean_intensity`.

Expected: `metric_valid_for.region` is **Preview's small box**, and it
**differs from the box the probe just reported**. MM's own
`studio.live().get_display()` route names the Preview specifically and must win
over window focus; the probe following focus to the wrong window is what makes
this a real test rather than a coincidence. Two boxes that happen to be equal
prove nothing — redraw one of them so they cannot be confused.

This limb was **NOT TESTED** on 2026-08-19: both `snap_and_analyze` calls
returned `[186, 216, 96, 84]` and nothing in the evidence shows a second window
was ever open, so there was no wrong box available to read.

**Close the extra window before continuing.**

## Step 5 — the snap path, and its second check

Full frame, Preview open, a box drawn.

> Call `snap_and_analyze(region="drawn")` exactly once. Report
> `metric_valid_for.region`, `focus_metric`, `snr` and `mean_intensity`.

Required: `metric_valid_for.region` equals the drawn box.

`snap_and_analyze` validates the box **twice** — once against the camera's
reported frame before spending an exposure, once against the array that actually
came back. The two disagree only if the frame changes between those reads, which
cannot be staged deterministically on a rig; that path is covered off-rig by
`test_region_is_rechecked_against_the_frame_that_came_back`, which measured the
unfixed code reporting a 40×40 box while metering 24×24 pixels. This limb
confirms the first check against real hardware.

## Step 6 — export, then execute standalone

Same session, after Steps 2–5. Type verbatim:

> Use `export_session_script` to write this session to `block54c.py`. Report the
> complete result verbatim. Do not edit the emitted file.

Close Microclaw entirely, leaving Micro-Manager and its bridge running. Take the
**absolute path** from the tool's result — the file lands in the configured
workspace, not necessarily this checkout. Restore the full frame first.

```powershell
$Script = "<absolute path reported by export_session_script>"
if (-not (Test-Path $Script)) {
    Write-Host "STOP: `$Script was not edited, or the path is wrong."
    exit 1
}
python design\54-roi-precondition.py --expect full
Write-Host "precondition exit code (expected 0):" $LASTEXITCODE
Write-Host "--- counts ---"
Write-Host "resolved region literals (expected 2 or more):" @(Select-String -Path $Script -Pattern "_autofocus_region = \[").Count
Write-Host "the string drawn OUTSIDE comments (expected 0):" @(Select-String -Path $Script -Pattern "drawn" | Where-Object { $_.Line -notmatch "^\s*#" }).Count
Write-Host "bounds guards (expected 1 or more):" @(Select-String -Path $Script -Pattern "does not fit frame").Count
Write-Host "pixel-count helper (expected 1):" @(Select-String -Path $Script -Pattern "def _metric_pixel_count").Count
Write-Host "settle contract (expected 1):" @(Select-String -Path $Script -Pattern "def settle_stage_move").Count
Write-Host "envelope prints (expected 2 or more):" @(Select-String -Path $Script -Pattern "AUTOFOCUS ENVELOPE").Count
python $Script 2>&1 | Out-File -Encoding utf8 block54c-standalone.txt
Write-Host "standalone exit code (expected 0):" $LASTEXITCODE
Add-Content block54c-standalone.txt "standalone exit code: $LASTEXITCODE"
Get-Content block54c-standalone.txt
```

Expected: **`the string drawn OUTSIDE comments` is 0.** A standalone script has
no display and no box; if `"drawn"` survives into emitted *code* the script is
not standalone, whatever its exit code. It legitimately appears inside
`# SKIPPED` comments, which quote the refusal text verbatim — the 2026-08-19 run
had two, and the previous version of this command counted them and would have
failed the step for it. Each `_autofocus_region = [...]` must carry a real
rectangle — Step 2's resolved box and Step 3's literal one, which are the same
four numbers reached by two different routes.

Step 4's refusals appear as `# SKIPPED` comments. Exit code **0**, no
`NameError`, no traceback, and **the output file must not be empty** — each
sweep prints an envelope and an outcome, so 0 bytes means the script did not run.

## Return evidence

`block54c-pytest.txt` with Step 0's two exit codes and its **pass/fail line**;
`out54c-box.txt` with the PROBE BOX and the R2/R3 verdicts;
`out54c-noselection.txt`; every precondition command's output and exit code; the
complete agent transcript for Steps 2–6.

Side by side: **the PROBE BOX, the ECHOED BOX, Step 3's `region`, and both
`coarse.min_contrast` values.**

Step 4's four refusals verbatim, with the route used for 4b and both boxes for
4d; Step 5's `metric_valid_for.region`; the emitted script, its standalone
output, its exit code, and the six printed counts.

Say how many attempts each sweep took, and for any failure whether it was a
bridge timeout or a typed `StageMoveError`. State that the machine was the Nikon
and name the loaded configuration and the field.
