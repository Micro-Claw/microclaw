# Block 45 rig gate — saved-hook repair

Implementation ancestor: `620a094`

Run Steps 0–3 on the **demo machine**. Do not book M5 for the mechanism gate.
Step 4 is only the registry migration on M5. Use PowerShell from the checkout.

## Step 0 — pin and suite (demo machine)

Paste:

```powershell
git merge-base --is-ancestor 620a094 HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
python -m pytest -q > block45-pytest.txt 2>&1
Write-Host "pytest exit code (expected 0):" $LASTEXITCODE
Get-Content block45-pytest.txt
```

Expected on macOS for this branch: **1815 passed, 99 skipped, 3 warnings**.
Windows skip counts may differ; record its exact totals. Both printed exit codes
must be **0**. The three macOS warnings (one Starlette deprecation and two
featureless-image warnings) look noisy but are normal. Stop on a failed test or
ancestor check.

Start Microclaw against the demo configuration after this step.

## Step 1 — an unusable generated hook is refused before save

Say this verbatim:

> Write an observation hook named `block45_dead` that subclasses `HookBase`,
> takes `log_path` in its constructor, and records the mean intensity. Show me
> the complete source, then ask me to approve saving it.

Approve the displayed source. Expected after approval:

- the save result says **it was not saved**;
- `would_refuse` is `true`;
- the two reasons are **saved hook subclasses HookBase** and **saved hook
  constructor takes log_path**;
- the remedy says re-review alone is insufficient and explicitly says the
  source must not inherit `HookBase`, must not take `log_path`, and must provide
  `analyze_frame(image, metadata)` returning `HookResult`;
- the agent does not recommend attaching the hook.

Then say:

> List my saved hooks and tell me whether `block45_dead` exists.

Expected: `block45_dead` is absent. A lint warning about imports is normal; lint
is advisory and is not the refusal under test.

## Step 2 — a valid saved hook resolves and runs for one tile

Say this verbatim:

> Write a plain saved hook named `block45_one_tile` with no base class and no
> `log_path`. Its `analyze_frame(image, metadata)` should return a `HookResult`
> whose measurements contain `{"gate": "block45"}` and whose only action is
> `StopSurvey()`. Show me the complete source and ask me to approve it. After I
> approve it, describe it and run an adaptive survey of exactly one tile at the
> current XY position, one frame only, saving as `block45_one_tile`.

Approve only if the displayed source matches that request. Expected:

- save status says **saved successfully**;
- describe reports `would_refuse: false` and zero refusal reasons;
- the survey plans and acquires exactly **1 tile / 1 frame**;
- its hook log contains `"gate": "block45"` and an accepted `StopSurvey`;
- no unknown-hook or resolve refusal appears.

If the demo reports that stopping after the only planned tile has no further
tile to suppress, that is normal; the one acquired frame and accepted hook
result are the gate.

## Step 3 — exported observation acquisition discloses the omission

Say this verbatim:

> At the current position, acquire one frame with the observation-only
> `snr_observer` hook, save it as `block45_observed`, then export this session to
> `block45-observed-export.py`. Tell me the exact comment in the exported file
> about the hook.

Expected: the live acquisition records **1 frame** and an SNR observation. The
export succeeds and contains this exact comment (the hook name may retain the
shown quotes):

```python
# OBSERVATION HOOK NOT ATTACHED: 'snr_observer'; this standalone script reproduces imaging only and does not reproduce its measurements or hook log.
```

The exported acquisition deliberately has no hook attached. That is normal:
it reproduces the hardware imaging, not the Microclaw-owned measurement/logging
path, and now says so instead of silently dropping it. The file must parse and
must contain no `from microclaw` or `import microclaw`.

## Step 4 — re-save M5's registry (M5 only, not a mechanism gate)

Check out this branch on M5 and paste:

```powershell
git merge-base --is-ancestor 620a094 HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
Get-ChildItem tests\fixtures\hooks\m5_migrated\*.py | Select-Object Name,Length
```

Expected: exit code **0** and exactly **6 files**:
`filament_position_filter.py`, `mosaic_cell_counter.py`,
`mosaic_stitcher.py`, `mosaic_stitcher_rot.py`, `uv_activation.py`, and
`uv_activation_wind_down.py`. Do not edit anything under `m5_legacy`.

Start Microclaw on M5 and say:

> List my saved hooks. Report the total, then list every entry whose
> `resolvable` field is false with all refusal reasons and its source path. Do
> not run any hook.

Expected before migration: **12 saved hooks total, 9 unresolvable**. All nine
first report the legacy newline pin. Preserve this output: it supplies the exact
rig names rather than guessing from an old survey.

For **each of those 9 names**, say this verbatim, substituting the name and the
matching migrated fixture filename. The two stitcher names on M5 are
`mosaic_stitcher_v2` and `mosaic_stitcher_rot_v2`; map both to their fixture
without `_v2` in the filename.

> Read `tests\fixtures\hooks\m5_migrated\<matching-file>.py`. Show me the full
> source, all lint warnings, and the saved hook name `<existing-rig-name>`. Ask
> me to confirm that exact source under that exact existing name. Only after I
> confirm, save it as `user_provided` with the adaptive runner contract. Do not
> run it.

Expected for every repetition: the full source is visible before confirmation,
then **saved successfully**, with no contract error. This is intentionally nine
review-and-save operations: the legacy pin no longer proves the on-disk bytes
the operator reviewed, so a bulk repin would weaken consent. `open`/`os` lint
warnings may appear and are normal after review.

Finally say:

> List my saved hooks again. Report the total and every entry whose
> `resolvable` field is false. Do not run any hook.

Expected after migration: **12 saved hooks total, 12 resolvable, 0
unresolvable**. In particular, `mosaic_stitcher_v2` and
`mosaic_stitcher_rot_v2` must be resolvable; their copied source uses
`EmitArtifact(filename=self.filename, payload=canvas)`.

Return `block45-pytest.txt`, the complete Step 1 refusal, the Step 2 result and
hook log, the exported script, and both M5 `list_hooks` reports.
