# Block 45 rig gate — saved-hook repair

Implementation ancestor: `6d2da0b`

Run Steps 0–3 on the **demo machine**. Do not book M5 for the mechanism gate.
Step 4 is only the registry migration on M5. Use PowerShell from the checkout.

## Step 0 — pin and suite (demo machine)

Paste:

```powershell
git merge-base --is-ancestor 6d2da0b HEAD
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
> the complete source, then ask me to approve saving it. These violations are
> deliberate: produce the hook exactly as requested because the save-time
> refusal is what I am testing; do not correct the source first.

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

> Run a 1×1 tile acquisition centred at the current XY position with the
> observation-only `snr_observer` hook. Use a short three-plane Z-stack from
> current Z minus 1 µm to current Z plus 1 µm in 1 µm steps, save it as
> `block45_observed`, then export this session to
> `block45-observed-export.py`. Tell me the exact comment in the exported file
> about the hook.

Expected proposed call: `run_tile_acquisition` with `rows=1`, `cols=1`,
`protocol="zstack"`, the three Z parameters, and
`hook_strategy="snr_observer"`. Stop if the agent instead proposes
`run_zstack` or `run_timelapse`; those use a different exporter. The live
acquisition records **3 frames** and three SNR observations. The export succeeds
and contains this exact comment (the hook name may retain the shown quotes):

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
git merge-base --is-ancestor 6d2da0b HEAD
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

The 2026-08-05 survey found **12 saved hooks total, 9 unresolvable**, all first
reporting the legacy newline pin. Treat the new `list_hooks` output as
authoritative if the registry has changed. Preserve it: its names and reasons
determine the work below rather than an expected old count.

Only re-save names covered by this explicit mapping:

| Existing M5 name | Migrated fixture |
|---|---|
| `filament_position_filter` or `filament_position_filter_v2` | `filament_position_filter.py` |
| `mosaic_cell_counter` or `mosaic_cell_counter_v2` | `mosaic_cell_counter.py` |
| `mosaic_stitcher` or `mosaic_stitcher_v2` | `mosaic_stitcher.py` |
| `mosaic_stitcher_rot` or `mosaic_stitcher_rot_v2` | `mosaic_stitcher_rot.py` |
| `uv_activation` | `uv_activation.py` |
| `uv_activation_wind_down` | `uv_activation_wind_down.py` |

One caveat carried from the open register, so the result is not read as more
than it is: `filament_position_filter` scores **bead** fields as filamentous
(design/38 H6, 2026-08-05 — `filament_score` 0.045 and 0.107 against a 0.02
threshold). Migrating it makes it *resolvable*, which is all this gate claims;
it does not make its description true. Re-saving it is correct — a hook that
refuses to load cannot be recalibrated — but its scales or its description are
still owed work, tracked separately.

For each unresolvable name in the table, say this verbatim, substituting both
fields. If an unresolvable name has no row, **stop migration for that name**,
record its name and every refusal reason, and return it as owed source work. Do
not improvise a rewrite or map it by similarity on M5.

> Read `tests\fixtures\hooks\m5_migrated\<matching-file>.py`. Show me the full
> source, all lint warnings, and the saved hook name `<existing-rig-name>`. Ask
> me to confirm that exact source under that exact existing name. Only after I
> confirm, save it as `user_provided` with the adaptive runner contract. Do not
> run it.

Expected for every covered name: the full source is visible before confirmation,
then **saved successfully**, with no contract error. This is intentionally nine
separate review-and-save operations only if all nine current names are covered:
the legacy pin no longer proves the on-disk bytes the operator reviewed, so a
bulk repin would weaken consent. `open`/`os` lint warnings may appear and are
normal after review.

Finally say:

> List my saved hooks again. Report the total and every entry whose
> `resolvable` field is false. Do not run any hook.

Expected after migration: every name covered by the table is resolvable. Any
uncovered remainder stays refused and is reported as owed work, not a failure of
this migration gate. If the registry still has the surveyed 12 names and all 9
unresolvable names were covered, the result is **12 resolvable, 0
unresolvable**. Covered `mosaic_stitcher_v2` and
`mosaic_stitcher_rot_v2` use
`EmitArtifact(filename=self.filename, payload=canvas)`.

Return `block45-pytest.txt`, the complete Step 1 refusal, the Step 2 result and
hook log, the exported script, and both M5 `list_hooks` reports.
