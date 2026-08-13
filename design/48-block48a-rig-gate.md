# Block 48a rig gate — schema 3 minimal security bounds

Implementation ancestor: `444784d`

Run every step on **M5** from this branch in PowerShell. Preserve
`block48a-pytest.txt`, the authored
`%APPDATA%\microclaw\safety_config.yaml`, and the saved Microclaw transcript.

## Step 0 — preserve the schema-2 record, pin, and test

The existing reviewed file is the only written record of M5's reviewed bounds.
Rename it; never delete it. Close Microclaw, then paste:

```powershell
Rename-Item "$env:APPDATA\microclaw\safety_config.yaml" "safety_config.schema2.bak.yaml"
Test-Path "$env:APPDATA\microclaw\safety_config.schema2.bak.yaml"
git merge-base --is-ancestor 444784d HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
python -m pytest -q > block48a-pytest.txt 2>&1
Write-Host "pytest exit code (expected 0):" $LASTEXITCODE
Get-Content block48a-pytest.txt
```

Expected: `Test-Path` prints `True`, the ancestor exit code is **0**, and pytest
exits **0**. This implementation's macOS result is **1840
passed, 99 skipped, 3 warnings**. Record M5's exact passed and skipped numbers;
a higher skipped count than M5's previous full run is **NOT TESTED**.

## Step 1 — hand-author M5's minimal schema-3 file

First display the preserved record and open a new file:

```powershell
Get-Content "$env:APPDATA\microclaw\safety_config.schema2.bak.yaml"
notepad "$env:APPDATA\microclaw\safety_config.yaml"
```

In Notepad, type this document. Replace each `M5_*` token with the exact
reviewed numeric bound for that same M5 axis from the backup. Include every M5
stage axis present in the backup and no nonexistent axis. If M5 has a named
stage, replace `M5_NAMED_DEVICE` and its two bounds; otherwise omit the entire
`named_stages` section.

```yaml
schema_version: 3
reviewed: true
stage:
  x_min: M5_X_MIN
  x_max: M5_X_MAX
  y_min: M5_Y_MIN
  y_max: M5_Y_MAX
  z_min: M5_Z_MIN
  z_max: M5_Z_MAX
named_stages:
  - device: M5_NAMED_DEVICE
    min_um: M5_NAMED_MIN
    max_um: M5_NAMED_MAX
acquisition:
  confirm_above_frames: 500
  confirm_above_duration_s: 1200
```

Save, close Notepad, then paste:

```powershell
python -m microclaw check-config "$env:APPDATA\microclaw\safety_config.yaml"
Write-Host "check-config exit code (expected 0):" $LASTEXITCODE
Get-Content "$env:APPDATA\microclaw\safety_config.yaml"
```

Expected: exit code **0**, `Schema: valid`, and exactly one frame threshold
(`500`) plus one duration threshold (`1200`). The file contains no `camera`,
`channels`, `illumination`, `plugins`, `analysis`, `property_authorization`,
workspace, or `max_*` key. Any literal `M5_*` token is a gate failure.

## Step 2 — start M5 and refuse an out-of-bounds XY move

Start Micro-Manager with M5 and its ZMQ bridge, then paste:

```powershell
python -m microclaw serve
```

Save the complete browser conversation as `block48a-microclaw-transcript` using
the normal transcript-save control. Say this verbatim, replacing `M5_X_MAX`
with the exact numeric `stage.x_max` authored in Step 1 and doing no arithmetic
in the prompt:

> Use `move_stage` exactly once with x_um=M5_X_MAX_PLUS_ONE and y_um=0, where
> M5_X_MAX_PLUS_ONE is the numeric x_max in the active safety config plus 1
> micrometer. This refusal is deliberate. Report the complete result verbatim;
> do not clamp, retry, or use a raw property write.

Expected: exactly **1** `move_stage` call and **0** stage moves. The refusal
names X, the requested number, and the configured maximum. Correction, retry,
or another call path is **NOT TESTED**.

## Step 3 — exactly 500 frames asks once and runs after approval

Say this verbatim:

> Use `run_timelapse` for exactly 500 frames at an interval suitable for M5,
> saving under the normal M5 data directory as `block48a-500-frames`. When the
> large-acquisition confirmation appears, stop and let me answer it. Do not
> split the run, reduce the frame count, or call the tool a second time.

Expected before approval: exactly **1** confirmation naming **500 frames** and
exactly **0** acquired frames. Approve once. Expected after approval: exactly
**1** `run_timelapse` call total, **500** frames acquired, and no second
large-acquisition confirmation. Decline, split, retry, or fewer/more than 500
frames is **NOT TESTED**.

## Step 4 — omitted illumination policy does not block the old typed laser

Use the laser device/property/value that the renamed schema-2 file declared.
Say this verbatim after replacing the three `M5_OLD_*` tokens with those exact
values:

> The preserved schema-2 file typed M5_OLD_LASER_DEVICE property
> M5_OLD_LASER_PROPERTY. Use `set_device_property` exactly once to set it to
> M5_OLD_ON_VALUE, then read the same property once and report its exact value.
> The active schema-3 file deliberately has no illumination section. Do not add
> one, use another laser path, retry, or turn on any other source.

Expected: exactly **1** property write, the laser fires visibly on M5, the read
returns `M5_OLD_ON_VALUE`, and there is **0** Microclaw illumination-policy
refusal or enable confirmation. Immediately turn the laser off through
Micro-Manager's normal manual control after recording the result. Wrong routing
or a different source is **NOT TESTED**.

## Return evidence

Return these three artifacts unchanged:

- `block48a-pytest.txt`, with the exact passed/skipped counts and exit code;
- `%APPDATA%\microclaw\safety_config.yaml`, the authored schema-3 YAML (retain
  `safety_config.schema2.bak.yaml` on M5; do not delete it);
- `block48a-microclaw-transcript`, containing Steps 2–4, the one confirmation,
  the approval, all tool calls, and complete results.

Also report the Step-0 ancestor exit code, the out-of-bounds requested number
and refusal, the acquired frame count (**500** expected), and the exact old
laser device/property/value used.
