# Block 47 rig gate — camera ROI typed capability

Implementation ancestor: `73a378d`

Run every step on the **demo machine** from this branch. The demo carries this
whole gate: ROI is camera geometry and its camera has a sensor. Block 46 A2
uses the same `set_roi`/`clear_roi` code path, so block 46 cannot close ROI;
this gate is where that path is proved.

Use PowerShell from the checkout. Preserve `block47-pytest.txt`,
`block47-authorization-map.txt`, and the saved Microclaw transcript.

## Step 0 — pin and full suite

Paste:

```powershell
git merge-base --is-ancestor 73a378d HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
python -m pytest -q > block47-pytest.txt 2>&1
Write-Host "pytest exit code (expected 0):" $LASTEXITCODE
Get-Content block47-pytest.txt
```

Expected on macOS for this implementation: **1833 passed, 99 skipped, 3
warnings = 1932 collected**. Windows skip counts may differ. Record the exact
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

## Step 2 — real ROI takes effect through the named tools

Start a normal Microclaw agent session against the demo safety config. This
step must route through the agent tools named **`get_roi`, `set_roi`, then
`get_roi`**. Say this verbatim:

> Use `get_roi` and report its exact x, y, width, and height. Then use `set_roi`
> to set a positive rectangle wholly inside those returned bounds: keep the
> same x and y, and use half the returned width and half the returned height,
> rounded down to integers. Then use `get_roi` again and report the camera's
> exact resulting rectangle. Do not use a raw property write, a generated
> script, or any acquisition tool.

Expected:

- the first `get_roi` reports a positive rectangle;
- `set_roi` succeeds with `ROI set.` and does **not** mention exclusion or a
  safety-config remedy;
- the second `get_roi` reports the requested half-size rectangle (or the
  camera's documented hardware-aligned rectangle, which must still be a real
  smaller crop visible in Micro-Manager);
- the transcript visibly names all three tool calls.

If the agent uses `set_device_property`, Java/Python directly, or any tool other
than the named sequence, record **NOT TESTED**. Seeing a crop in the GUI after a
different call site is not evidence for this block.

## Step 3 — an out-of-bounds ROI is refused by the guard

This step must route through the agent tools **`get_roi` then `set_roi`**. Say
this verbatim:

> Use `get_roi` once. Then call `set_roi` with x equal to the returned x, y
> equal to the returned y, width equal to the returned width plus 1, and height
> equal to the returned height. This invalid request is deliberate. Report the
> complete refusal verbatim and do not retry, clear, clamp, or correct it.

Expected: `set_roi` is refused before hardware mutation with a `SafetyViolation`
whose reason says the requested rectangle is **outside the camera-reported
bounds** and says to clear the ROI first before expanding or repositioning.
It must not say `camera-roi` is excluded, must not recommend editing the safety
config, and the ROI visible in Micro-Manager must remain unchanged from Step 2.

If the agent clamps/corrects the rectangle, calls a different tool, or retries,
record **NOT TESTED**. A camera-adapter exception is a failure: this negative
case must be refused by Microclaw's guard before it reaches the adapter.

## Step 4 — clear is guarded and restores full frame

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
dimensions does not test `clear_roi`'s separate guard call.

## Step 5 — export remains standalone and refuses to invent ROI support

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
- the complete agent transcript for Steps 2–5;
- the Step-3 guard refusal verbatim and confirmation the Step-2 crop remained;
- `block47-roi-export.py`;
- the machine name/configuration used. A pass on any machine must say which
  machine actually ran it; do not report generic demo sufficiency as measured.
