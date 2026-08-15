# Block 53a rig gate — verify presets as sets

Implementation ancestor: `23e62b6` (the round-2 message shape; `f82469a` is its parent)

Run every step on **M5** from this branch. Use PowerShell from the checkout and
save `block53a-pytest.txt` plus the complete Microclaw transcript. Do not create
or edit any Micro-Manager preset during this gate. *Applying* an existing preset
from Micro-Manager's own Configuration Settings panel is required in Steps 0b and
2 and is not editing.

## Step 0 — pin the implementation and run the suite

Paste this literal PowerShell block:

```powershell
git merge-base --is-ancestor 23e62b6 HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
python -m pytest -q > block53a-pytest.txt 2>&1
Write-Host "pytest exit code (expected 0):" $LASTEXITCODE
Get-Content block53a-pytest.txt
```

Expected: both printed exit codes are **0**. Record the exact passed, skipped,
warning, and collected totals. Windows skip counts may differ from macOS; a
higher skip count than M5's previous full-suite run is **NOT TESTED** until
explained.

## Step 0b — put the camera in the wrong mode first, or the gate cannot fail

**Coordinator addition.** Every later step is vacuous if M5 already sits in
`Normal Mode`: the plan's writes become no-ops, `Exposure` is never written under
the wrong scan mode, and the ordering trap this block exists for is never sprung.
A green Step 1 from that start proves nothing (block 46's lesson: a gate that
cannot fail is worth zero).

In **Micro-Manager's own Configuration Settings panel** — not Microclaw, and
without creating or editing any preset — double-click `System` → `Camera`. Then
read these in the Property Browser and write both numbers down:

- `HamamatsuHam_DCAM.ScanMode` — expected **2**
- `HamamatsuHam_DCAM.Exposure` — record whatever `Camera` sets

If `ScanMode` does not read **2** here, stop and report that instead: the
reproducer's precondition is absent and Steps 1–3 cannot mean what they claim.

## Step 1 — Normal Mode lands as one verified preset

Start the normal Microclaw agent session against M5's reviewed safety config.
Keep Micro-Manager's Property Browser visible. Confirm Step 0b's `ScanMode` **2**
is still showing before you type anything. Type this verbatim:

> Call `set_config_preset(group="System", preset="Normal Mode")` exactly once.
> Report the complete result, including the write count. Do not substitute
> `core.set_config`, raw property writes, Python, or a generated script. After it
> succeeds, use the normal read-only property tool to report the exact current
> values of `HamamatsuHam_DCAM.ScanMode` and
> `HamamatsuHam_DCAM.Exposure`.

Expected: `Config preset System.Normal Mode applied.`, **11 writes**, ScanMode
**3**, and Exposure exactly **100.0030** in the device property read-back and
Property Browser. There must be no read-back failure, rollback, partial
application, or `SAFE STATE NOT VERIFIED`. Wrong routing is **NOT TESTED**.

If the call instead fails naming `HamamatsuHam_DCAM.Exposure` with
`got '100.0140'`, record **PREMISE FAILURE, not a code failure**. Capture the
complete refusal and the Property Browser values of both ScanMode and Exposure
after rollback, then stop the gate: do not retry and do not edit the preset. It
means the driver does not re-derive exposure on a mode change, so plan-level
read-back is unsatisfiable for presets of this shape and the design needs a
different answer.

## Step 2 — export and execute the Normal Mode switch standalone

In the Step-1 session, type this verbatim:

> Use `export_session_script` to write this session to
> `block53a-normal-mode.py`. Report the complete result verbatim. Do not edit the
> emitted file.

Close Microclaw entirely, leaving Micro-Manager and its bridge running.

**Coordinator addition — two things before the PowerShell.**

1. `export_session_script` resolves its `output_path` through
   `guard.resolve_in_workspace`, so the file lands in the configured workspace
   root (or Microclaw's working directory), **not necessarily in this checkout**.
   Take the absolute path out of the tool's reported result and put it in
   `$Script` below; do not assume the relative name resolves here.
2. **Put the camera back in the wrong mode first.** After Step 1 the rig already
   holds `ScanMode` 3 and `Exposure` 100.0030, so a standalone script that
   rewrites those values verifies trivially and proves nothing. In
   Micro-Manager's own Configuration Settings panel, double-click `System` →
   `Camera` again, and confirm the Property Browser reads `ScanMode` **2** before
   you run the script.

```powershell
$Script = "<absolute path reported by export_session_script>"
$SetLines = Select-String -Path $Script -Pattern "^core.set_property\("
$VerifyLines = Select-String -Path $Script -Pattern "^_verify_property\("
$SetLines
$VerifyLines
$LastSet = ($SetLines | Measure-Object -Property LineNumber -Maximum).Maximum
$FirstVerify = ($VerifyLines | Measure-Object -Property LineNumber -Minimum).Minimum
Write-Host "set-property count (expected 11):" @($SetLines).Count
Write-Host "verify count (expected 11):" @($VerifyLines).Count
Write-Host "last set line must be less than first verify line:" $LastSet $FirstVerify
python $Script > block53a-standalone.txt 2>&1
Write-Host "standalone exit code (expected 0):" $LASTEXITCODE
Get-Content block53a-standalone.txt
```

Expected: both counts are **11**; every printed `core.set_property` line number
is less than every printed `_verify_property` line number (`$LastSet` is less
than `$FirstVerify`); standalone exit code is **0**; and
`block53a-standalone.txt` contains no verification failure, `NameError`, or
traceback. An empty standalone output file is expected because a successful
script need not print. Confirm in the Property Browser that ScanMode is **3**
and Exposure is **100.0030** after the standalone run. Preserve the emitted
script and standalone output.

## Step 3 — switch repeatedly in both directions

Start a new normal Microclaw agent session against the same reviewed M5 safety
config. Type this verbatim:

> Using only `set_config_preset`, switch `System/Camera`, then
> `System/Normal Mode`, then `System/Camera`, then `System/Normal Mode` — four
> calls in that exact order. Do not combine, reorder, retry, or replace a call.
> Report each complete result and write count separately. After the fourth call,
> use the normal read-only property tool to report the exact current values of
> `HamamatsuHam_DCAM.ScanMode` and `HamamatsuHam_DCAM.Exposure`.

Expected: all four calls report their named preset applied with **11 writes**
each. The final read-back is ScanMode **3** and Exposure **100.0030**. Neither
direction may report a verification failure, rollback, partial application, or
`SAFE STATE NOT VERIFIED`. A run with only one direction, fewer than four
calls, an automatic retry, or different routing is **NOT TESTED**.

## Step 4 — naturally unsatisfiable preset, if one already exists

Do **not** author or modify a preset for this step. If M5 already has a preset
known from ordinary use to contain a value the device genuinely ignores, type
this verbatim, replacing only the two bracketed names with that existing preset:

> Call `set_config_preset(group="[EXISTING GROUP]", preset="[EXISTING
> PRESET]")` exactly once. This preset is expected to be unsatisfiable. Report
> the complete refusal verbatim, including every mismatched device/property
> pair, `applied`, `attempted`, and `rolled_back`. Do not retry, repair, create,
> or edit a preset. Then report the affected properties' current values with the
> normal read-only property tool.

Expected when such a natural preset exists: the refusal reads
`applied all N writes, then read-back verification failed for K of them` — not
`stopped after`, which now means only that a write itself raised — and names
**every** mismatched pair in its `mismatched=[...]` list; rollback writes are in
reverse plan order; the read-only values match the values held before the call;
and no `SAFE STATE NOT VERIFIED` appears when every restore succeeded.

If no such preset already exists, record exactly:

> **Limb 3 NOT RUN — M5 has no naturally unsatisfiable preset, and the runbook
> forbids authoring one. Off-rig coverage:
> `test_verify_pass_reports_every_ignored_pair_and_rolls_back_in_reverse`.**

That is the required outcome for an unavailable natural reproducer, not a gate
failure. Never make a harmless preset unsatisfiable to turn this limb into a
live test.

## Return evidence

Return `block53a-pytest.txt`, both Step-0 exit codes and totals, **Step 0b's two
recorded Property Browser values and confirmation that `ScanMode` read 2 before
Step 1 and again before the standalone run**, the complete
agent transcript for Steps 1–4, the emitted script,
`block53a-standalone.txt`, the ordering counts and line numbers, the Property
Browser values after Steps 1–3, and either the complete natural-refusal evidence
or the exact Limb-3-not-run statement above. State that the machine was M5 and
name the loaded configuration.
