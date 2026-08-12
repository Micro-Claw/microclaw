# Block 47 rig gate — camera ROI typed capability

Implementation ancestor: `9018188`

Run every step on the **demo machine** from this branch. The demo carries this
whole gate: ROI is camera geometry and its camera has a sensor. Block 46 A2
uses the same `set_roi`/`clear_roi` code path, so block 46 cannot close ROI;
this gate is where that path is proved.

Use PowerShell from the checkout. Preserve `block47-pytest.txt`,
`block47-authorization-map.txt`, and the saved Microclaw transcript.

## Step 0 — pin and full suite

Paste:

```powershell
git merge-base --is-ancestor 9018188 HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
python -m pytest -q > block47-pytest.txt 2>&1
Write-Host "pytest exit code (expected 0):" $LASTEXITCODE
Get-Content block47-pytest.txt
```

Expected on macOS for this implementation: **1832 passed, 99 skipped, 3
warnings = 1931 collected**. Windows skip counts may differ. Record the exact
passed and skipped counts and compare the skipped count with the previous full
suite on this same host. Both printed exit codes must be **0**. A higher skip
count than the previous host run is **NOT TESTED**, even if pytest is green.
The usual Starlette deprecation and two featureless-image warnings are normal.

## Step 1 — authorization map names ROI as typed geometry

With Micro-Manager running the demo configuration and its ZMQ bridge enabled,
paste the same launch prefix used for normal demo sessions. If the demo uses an
explicit safety file, include it before the subcommand exactly as usual:

```powershell
python -m microclaw authorization-map > block47-authorization-map.txt 2>&1
Write-Host "authorization-map exit code (expected 0):" $LASTEXITCODE
Get-Content block47-authorization-map.txt
```

Expected: exit code **0**, one `camera-roi` entry with classification
`built_in_typed_capability`, capability `camera-roi`, and the connected camera
device. There must be no excluded `camera-roi` entry. `mmstudio-mda` remains
`excluded`; that is deliberate and is not exercised by this block.

If an explicit safety file was required but omitted, the command tested no
map. Correct the command and rerun; otherwise record **NOT TESTED**.

## Step 2 — establish full frame, then apply a corner crop

Start a normal Microclaw agent session against the demo safety config. This
step must route through the agent tools named **`clear_roi`, `get_roi`,
`set_roi`, then `get_roi`**. Say this verbatim:

> Use `clear_roi` once, then use `get_roi` and report the exact full-frame x, y,
> width, and height. Call those values FULL_X, FULL_Y, FULL_WIDTH, and
> FULL_HEIGHT. Use `set_roi` to set a corner crop at FULL_X, FULL_Y whose width
> is floor(FULL_WIDTH / 4) and height is floor(FULL_HEIGHT / 4). Then use
> `get_roi` again and report the exact resulting rectangle. Do not use a raw
> property write, a generated script, or any acquisition tool.

Expected:

- `clear_roi` succeeds and the first `get_roi` reports a positive full frame;
- `set_roi` succeeds with `ROI set.` and does **not** mention exclusion or a
  safety-config remedy;
- the second `get_roi` reports the requested quarter-size rectangle (or the
  camera's documented hardware-aligned rectangle, which must still be a real
  smaller crop visible in Micro-Manager);
- the transcript visibly names all four tool calls.

If the agent uses `set_device_property`, Java/Python directly, or any tool other
than the named sequence, record **NOT TESTED**. Seeing a crop in the GUI after a
different call site is not evidence for this block.

## Step 3 — measure whether the adapter accepts repositioning outside the crop

This step must route through the agent tools **`set_roi` then `get_roi`** and
must use the full-frame dimensions recorded in Step 2. Say this verbatim:

> Using the exact FULL_X, FULL_Y, FULL_WIDTH, and FULL_HEIGHT recorded in Step
> 2, use `set_roi` to request an equal-sized quarter-frame crop in the opposite
> corner: x = FULL_X + FULL_WIDTH - floor(FULL_WIDTH / 4), y = FULL_Y +
> FULL_HEIGHT - floor(FULL_HEIGHT / 4), width = floor(FULL_WIDTH / 4), height =
> floor(FULL_HEIGHT / 4). This rectangle is fully inside the sensor but entirely
> outside the current crop. Do not clear first and do not clamp or retry. Report
> the raw `set_roi` outcome verbatim, then use `get_roi` once and report the
> actual rectangle.

This is a measurement, not a predetermined PASS outcome. Record one of:

- **ACCEPTED:** `set_roi` succeeds and `get_roi` reports the opposite-corner
  crop (or a documented hardware-aligned version of it); or
- **ADAPTER REFUSED:** preserve the complete camera-adapter error and confirm
  `get_roi` still reports the Step-2 crop.

Either raw outcome is evidence if it came from the named tool without an
intervening clear. A Microclaw `SafetyViolation`, an authorization-map refusal,
or safety-config advice is a gate failure: Microclaw must pass valid positive
integer geometry to the adapter.

If the agent clears, clamps/corrects the rectangle, calls a different tool, or
retries, record **NOT TESTED**.

## Step 4 — adapter-independent nonsense is refused by the guard

This step must route through the agent tools **`get_roi`, `set_roi`, then
`get_roi`**. Say this verbatim:

> Use `get_roi` and record the current rectangle. Then call `set_roi` with x=0,
> y=0, width=0, and height=10. This invalid request is deliberate. Report the
> complete refusal verbatim and do not retry or correct it. Then use `get_roi`
> again and report the rectangle.

Expected: `set_roi` is refused by `SafetyGuard` because width and height must
be positive; the two `get_roi` results are identical, proving no camera write
occurred. An adapter exception, authorization refusal, correction, retry, or
different routing is **NOT TESTED**.

## Step 5 — clear restores full frame without reading or guarding geometry

This step must route through the agent tools **`clear_roi` then `get_roi`**. Say
this verbatim:

> Use `clear_roi` exactly once, then use `get_roi` and report the exact restored
> rectangle. Do not use `set_roi`, a raw property write, Java/Python directly,
> or an acquisition tool.

Expected: `clear_roi` succeeds with `ROI cleared (full frame).`; `get_roi`
reports a positive rectangle larger than the Step-2 crop, and Micro-Manager
shows the full frame. No authorization-map or safety-config refusal appears.
The transcript must visibly name `clear_roi` and `get_roi`.

Wrong routing is **NOT TESTED**. In particular, calling `set_roi` with the old
dimensions does not test `clear_roi`.

## Step 6 — export remains standalone and refuses to invent ROI support

This step routes through the agent tool **`export_session_script`**. Say this
verbatim:

> Export this session to `block47-roi-export.py`. Then read that file and quote
> the complete `RECORDED TOOL`, `NOT EMITTED`, and following `RuntimeError`
> lines for every `set_roi` and `clear_roi` record. Confirm whether any
> `microclaw` import or `authorize_path` call occurs. Do not edit or run the
> exported file.

Expected: export succeeds; each included ROI write is represented by a loud
`# NOT EMITTED` line followed by a `RuntimeError`; the file contains neither
`import microclaw`, `from microclaw`, nor `authorize_path`. `get_roi` may appear
as `# No hardware-routine effect.` because reads have no emitted hardware
effect. If the agent exports a hand-written substitute or another transcript,
record **NOT TESTED**.

## Return evidence

Return:

- the two Step-0 exit codes and complete pytest totals, including collected and
  skipped counts plus the previous skip count from this host;
- `block47-authorization-map.txt`;
- the complete agent transcript for Steps 2–6;
- the Step-3 raw adapter outcome and resulting ROI;
- the Step-4 guard refusal verbatim and matching before/after ROI;
- `block47-roi-export.py`;
- the machine name/configuration used. A pass on any machine must say which
  machine actually ran it; do not report generic demo sufficiency as measured.
