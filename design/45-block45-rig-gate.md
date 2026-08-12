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

**Measured on M5 2026-08-12: 21 saved hooks, 9 unresolvable.** The 2026-08-05
survey's "12 total" was already stale — later blocks added hooks. Treat the live
`list_hooks` output as authoritative and preserve it: its names and reasons
determine the work below, never an expected count.

**Every unresolvable name is its own registry entry.** A `_v2` name is not an
alias for the un-suffixed one; M5 carries both, and re-saving one leaves the
other refused. The 2026-08-12 round lost four names to exactly that misreading.
Route each name by **its own refusal reasons**:

- **Pin-only** (`saved hook uses a legacy newline-normalized hash` and nothing
  else): the source is already contract-clean. Re-save it from **its own path**,
  which `resolve_refusal.remedy.path` gives you. Do **not** overwrite it from a
  fixture — that would discard the operator's own migration for no reason.
- **Any source or contract reason as well** (`subclasses HookBase`, takes
  `log_path`, an `EmitArtifact` contract violation): the source must change, so
  re-save from the migrated fixture below.

| Existing M5 name | Migrated fixture |
|---|---|
| `filament_position_filter`, `filament_position_filter_v2` | `filament_position_filter.py` |
| `mosaic_cell_counter`, `mosaic_cell_counter_v2` | `mosaic_cell_counter.py` |
| `mosaic_stitcher`, `mosaic_stitcher_v2` | `mosaic_stitcher.py` |
| `mosaic_stitcher_rot`, `mosaic_stitcher_rot_v2` | `mosaic_stitcher_rot.py` |
| `uv_activation` | `uv_activation.py` |
| `uv_activation_wind_down` | `uv_activation_wind_down.py` |

**Outstanding after the 2026-08-12 round — four names, all `_v2`, to be finished
in place:** `filament_position_filter_v2` and `mosaic_cell_counter_v2` are
pin-only, so re-save each from its own path; `mosaic_stitcher_v2` and
`mosaic_stitcher_rot_v2` also carry the `EmitArtifact` contract violation, so
re-save those two from the corrected fixtures. Nothing is deleted. This is a
re-save, not a gate round, and it does not block the block.

One caveat carried from the open register, so the result is not read as more
than it is: `filament_position_filter` scores **bead** fields as filamentous
(design/38 H6, 2026-08-05 — `filament_score` 0.045 and 0.107 against a 0.02
threshold). Migrating it makes it *resolvable*, which is all this gate claims;
it does not make its description true. Re-saving it is correct — a hook that
refuses to load cannot be recalibrated — but its scales or its description are
still owed work, tracked separately.

Work one name at a time. For a **source/contract** name, say this verbatim,
substituting both fields:

> Read `tests\fixtures\hooks\m5_migrated\<matching-file>.py`. Show me the full
> source, all lint warnings, and the saved hook name `<existing-rig-name>`. Ask
> me to confirm that exact source under that exact existing name. Only after I
> confirm, save it as `user_provided` with the adaptive runner contract. Do not
> run it.

For a **pin-only** name, say this verbatim instead:

> Read `<the remedy path list_hooks gave for this hook>`. Show me the full
> source and all lint warnings, and confirm it has no contract violations. Ask
> me to confirm that exact source under the existing name `<existing-rig-name>`.
> Only after I confirm, save it as `user_provided` with the adaptive runner
> contract. Do not run it.

If an unresolvable name has no row and is not pin-only, **stop for that name**,
record it and every refusal reason, and return it as owed source work. Do not
improvise a rewrite or map it by similarity on M5.

Expected for every name: the full source is visible before confirmation, then
**saved successfully**, with no contract error. This is deliberately one
review-and-save per entry — the legacy pin no longer proves the on-disk bytes
the operator reviewed, so a bulk repin would weaken consent. `open`/`os` lint
warnings may appear and are normal after review.

Finally say:

> List my saved hooks again. Report the total and every entry whose
> `resolvable` field is false. Do not run any hook.

Expected after migration: every name you re-saved is resolvable, and the
unresolvable count has dropped by exactly that many. Any remainder is reported as
owed work, not a failure of this migration gate. **The 2026-08-12 round measured
21 total, 9 unresolvable before and 4 after**, all four being the `_v2` entries
listed above. If a later round finishes those four the result is **21
resolvable, 0
unresolvable**. Covered `mosaic_stitcher_v2` and
`mosaic_stitcher_rot_v2` use
`EmitArtifact(filename=self.filename, payload=canvas)`.

Return `block45-pytest.txt`, the complete Step 1 refusal, the Step 2 result and
hook log, the exported script, and both M5 `list_hooks` reports.
