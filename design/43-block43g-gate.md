# Block 43g gate — coverage statistics

`design43/coverage-statistics`. **Most of this block's evidence is already in
hand and was produced offline**, against the saved 2026-08-06 M5 Nestor data.
What is left for a session is one criterion, and it does not need a microscope
with a sample on it — the demo config is enough.

Read §"What the offline study already settled" before running anything. It
tells you what this block does and, more importantly, what it does **not** do,
so the session is not spent re-testing a claim that has already been withdrawn.

## Pin

Run in the repo, on this branch:

```
git merge-base --is-ancestor 3cd78de HEAD
```

Exit code 0 means the implementation this gate describes is present. Print the
word, not the number:

```powershell
if ($LASTEXITCODE -eq 0) { "PINNED" } else { "STALE - do not run this gate" }
```

## What ships

Three statistics on `ImageStats`, computed once in `coverage_stats` and carried
by every observation record and offline adapter through `_asdict()`:

- `signal_coverage` — fraction of pixels above `background + min_snr · noise`.
- `structure_coverage` — the same after a σ=2 px blur, with the noise floor
  re-estimated on the blurred frame.
- `signal_concentration` — share of above-background signal held by the
  brightest 1% of pixels.

Plus: `rank_hook_log` prefers `signal_coverage` for surveys and refuses to rank
a clipped frame by coverage; `find_features` states its own scope under
`detector_scope`; `SYSTEM_PROMPT` says snr is a tail statistic and must not rank
tiles alone.

## What the offline study already settled

Run against `nestor-06082026/microclaw_data`, joined to the observation records
the shipped code wrote on the day. **Control: recomputed snr reproduced the
logged snr to 0.0000 over all 313 scored tiles of the 324-tile 488 raster and
all 36 tiles of the 561 raster**, so the join is not in question.

**1. Coverage is a better gate than snr, and that is what this block delivers.**
Sweeping the threshold over the 324-tile raster:

| min_snr | tiles `sig>0.01` | tiles `sig>0.05` |
|---|---|---|
| 4.0 | 6 | 4 |
| 3.5 | 7 | 5 |
| 3.1 | 13 | 6 |
| 2.8 | 60 | 6 |
| 2.5 | 100 | 6 |
| 2.0 | 256 | 57 |

`min_snr` 3.1 sits on a cliff — one step to 2.8 takes the count from 13 to 60 —
but a coverage gate of 0.05 is flat at 5–6 tiles across the whole 2.5–3.5 band.
Gate on coverage magnitude, not on `min_snr` alone.

**2. Coverage ranked clipped frames first, and now refuses them — under its own
limit, not snr's.** Two of the top two tiles by `signal_coverage` were 4.0% and
19.8% saturated, frames snr had already refused (four of 324 are affected).
Coverage now applies `MAX_SATURATED_FRACTION_FOR_COVERAGE = 1%`, which is 100×
looser than the SNR gate: coverage is a fraction, not a tail statistic, so a
clipped pixel is still legitimately above threshold whereas a plateau lands
p99.5 inside itself. The looser limit is required by the bead evidence below —
the first version of this fix used snr's 0.01% gate and would have refused every
real bead field measured.

**3. Beads — the limb this gate first recorded as owed, now met.** Six bead
fields in `stitch_test_cant_open/stitch_test_1`, join verified against the
session's own `snr_observer.log` (`background_level` and `focus_metric` both
reproduce exactly):

| field | sat % | `signal_coverage` | `structure_coverage` | `signal_concentration` |
|---|---|---|---|---|
| survey_1 | 0.017 | 0.0718 | 0.1250 | 0.807 |
| near_1 | 0.020 | 0.0958 | 0.1450 | 0.771 |
| near_2 | 0.074 | 0.1306 | 0.1671 | 0.732 |
| survey_3 | 0.077 | 0.1760 | 0.1959 | 0.477 |
| survey_2 | 0.150 | 0.1646 | 0.1823 | 0.564 |
| near_3 | 0.220 | 0.1777 | 0.2034 | 0.615 |

The trio behaves sensibly on a punctate field: moderate coverage with high
concentration (0.48–0.81) is exactly the signature of sparse bright puncta, and
it is well separated from the 0.09–0.14 concentration band the extended cell
fields occupy. **`snr` is refused on all six** — every bead field clips a few
bead centres — so on this sample class the coverage statistics are the only
ranking signal available, which is a stronger argument for the block than the
one F6 made.

**4. This block does NOT close design/43 F6 or supply F5's measurement.**
Measured, not assumed:

- F6's tile `scan300_488_r12_c15` ranks #1 by snr and #2 by coverage. Its
  `signal_concentration` is **0.137**, inside the 0.09–0.14 band every good tile
  occupies. The statistic added to catch it does not flag it. F6's own account
  of the tile as a small bright corner is wrong — it has `signal_coverage`
  0.148, a broad bright region.
- On F5's 36-tile raster (`mt_search_561/filament_raster.json`), the six tiles
  with real material score `signal_coverage` **0.0000** and `structure_coverage`
  **0.0000**, while all 27 bare-glass tiles score 0.0031–0.0049. **A complete
  inversion.** The material tiles are 20× brighter (median 3963 vs 220); their
  snr is low because a frame uniformly full of signal has an enormous MAD. No
  background-relative threshold can see signal that has become the background.

The operator confirmed by eye that frames 20 and 17 of that raster are
out-of-focus cells, and that frames 12 and 21 — the one pair where
`ridge_coverage` and intensity disagree — are not distinguishable by eye. So no
single-frame intensity **or** texture statistic has been shown to separate cells
from a diffuse bright gradient on this data. **The discriminator that worked in
both findings was the focus response**: F6's tile was exposed by autofocus
refusing it (contrast 0.124), and F5's tiles were confirmed by hand-refocus
converging at 9.6–42.8. That is block 43i, which this block now feeds as a
refocus *trigger* rather than a verdict.

## Step 0 — the suite, on the gate machine

Standing constraint: a block gated in a session runs the full suite there too.

```powershell
uv run pytest -q > step0.txt 2>&1
if ($LASTEXITCODE -eq 0) { "SUITE GREEN" } else { "SUITE RED" }
```

Expected on macOS at `3cd78de`: **1709 passed, 99 skipped, 3 warnings**, so
1808 collected. Derive the total on the gate machine from its own
`passed + skipped`; do not compare to the number above if the skip count
differs, and report the skip count either way.

## G1 — the reach criterion (the only criterion that needs a session)

Standing constraint, from 43e: gate the reach, not the plumbing. **Do not name
any statistic, tool, or field in the request.** A criterion that names
`signal_coverage` tests whether the code works, which the offline study already
established; it cannot test whether anything finds the code.

Run a survey over any multi-tile area — the demo config is fine, and a real
sample is not required — with a hook that writes an observation log. Then ask,
in these words or your own:

> which of those tiles has the most stuff in it?

**PASS** requires all three:

1. Microclaw ranks the tiles using `signal_coverage` (or explicitly says which
   statistic it ranked on and why it chose it).
2. It does **not** rank on `snr` alone silently.
3. If any tile in the log is clipped, it reports that tile as unranked with the
   saturation reason, rather than putting it on top.

**FAIL** if it ranks on snr without saying so, or invents a composite score in
prose instead of using the ranking tool.

Record the exact wording you used and the exact wording that came back — the
phrasing is the evidence here, not the numbers.

## What is NOT gated here, deliberately

- **The three statistics' numerical behaviour.** Settled offline, on real M5
  data, reproducibly. A session would measure it worse.
- **`min_snr` calibration.** The sweep above is the calibration evidence. A
  value chosen from it belongs in `safety_config.yaml` as `analysis_min_snr`;
  until one is set, `min_snr_source` reads `package_default_uncalibrated` and
  says so in every record.
- **Beads.** Met offline, §3 above. An earlier draft of this doc said no saved
  bead pixels existed anywhere — that was wrong, generalised from one folder
  (`multicolor_bead_run_m5`, which holds only a history and confirmations).
  Most saved data in this project outside the Nestor and amr sessions is beads;
  `stitch_test_cant_open` is one of many. **Search the evidence archive by its
  real folder names before declaring a data class absent.**
