# Block 43n rig gate — two-channel search and acquire

Run from branch `design43/acquire-on-hit`. Save every transcript, hook log,
dataset path, audit extract, and exported script with the step number.

## What demo hardware can prove

A demo camera can prove event counts, search-before-acquire phase order, hit and
dose bounds, decision records, per-hit Z in submitted events, and standalone
script replay. Identical demo frames cannot prove that 561 evidence selected a
biological hit, that 488 differs optically from 561, that an enable line emitted
light, or that the acquired burst contains the intended structure. Do not book
demo time as evidence for those claims. M5 plus operator-visible images is
required for them.

## Step 0 — pin and offline suite

In PowerShell, from the checkout:

```powershell
git merge-base --is-ancestor 0f40238 HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
python -m pytest -q -p no:cacheprovider > block43n-pytest.txt 2>&1
Write-Host "pytest exit code (expected 0):" $LASTEXITCODE
Get-Content block43n-pytest.txt
```

Expected macOS baseline for this branch: **1909 collected, 1810 passed, 99
skipped, 3 warnings**. The warnings are one `StarletteDeprecationWarning` and two
empty-image `phase_cross_correlation` warnings. On Windows, record the exact
count; a separate known socket-race item may intermittently add one warning but
must not change pass/fail counts.

Stop if the ancestor check, suite exit code, collection total, or test totals do
not match the applicable platform baseline.

## M5 — authorization-map route, no `Channel` group

### Step 1 — reach the feature from operator language

Start Microclaw on M5 and give the agent exactly this request (do not name a
tool):

> Search these marked fields in 561 one at a time. Whenever the saved detection
> hook reports a hit, acquire a three-frame burst at that field in 488, at most
> two fields. Save the search, bursts, hook log, and a standalone replay script.

Before confirmation, capture the proposed call. It must be one adaptive survey
with `protocol_params.channel="561"` and an `acquire_on_hit` object containing
`channel="488"`, `protocol="timelapse"`, `n_frames=3`, and `max_hits=2`. It must
not propose a manual loop over a prior hook log. Stop if the feature is not
reached or either phase/exposure bound is absent.

### Step 2 — positive hit run

Use a reviewed saved hook and fields where the operator can identify at least
one real positive. Approve both acquisition reservations and both
`illumination/enable` streams. Retain the complete audit and hook log.

Pass requires all positive limbs:

- operator-visible search frames use 561, and the later burst uses 488;
- all search frames precede all acquire frames, with exactly one 488 phase
  switch regardless of hit count;
- the audit contains separate search and acquire enable confirmations and the
  acquire illuminated dose equals returned acquire frames times the acquire
  exposure, never exceeding `max_hits × 3 × exposure_ms`;
- each accepted action says `planned tile recorded for acquire phase`; a repeated
  tile says `planned tile is already recorded for acquire phase`; a third unique
  tile says `acquire phase max_hits exhausted`;
- the result reports `hits_recorded`, `hits_acquired`, `max_hits_reached`, and
  `acquire_phase_ran`; acquired positions are unique and their submitted Z values
  equal their hit-time focus Z values;
- the saved 488 images visibly contain the intended structure. An inert run,
  matching counts without a real positive, does not pass this M5 step.

### Step 3 — zero-hit run

Run the same request with a reviewed hook/threshold that records no hits. Pass
requires a successful search result with `hits_recorded=0`, `hits_acquired=0`,
`max_hits_reached=false`, and `acquire_phase_ran=false`; there must be no 488
switch, no acquire dataset, and zero acquire frames/dose. Search frames must
still exist. A run with no frames at all cannot pass.

### Step 4 — standalone replay with Microclaw closed

Export the positive session script. Confirm the script contains the hook source,
the adaptive decision loop, `hits = []`, hit-time `core.get_position`, recorded
561 and 488 channel effects, and an acquire loop using each fresh hit's `z_um`.
It must not contain the positive run's hit list as acquire coordinates.

Close Microclaw completely, leave Micro-Manager/pycro-manager available, then:

```powershell
python .\<exported-script-name>.py > block43n-standalone.txt 2>&1
Write-Host "standalone exit code (expected 0):" $LASTEXITCODE
Get-Content block43n-standalone.txt
```

Pass requires new search frames, a newly selected hit set, one 488 switch only
when that new set is non-empty, acquire frames bounded by `max_hits × 3`, and
per-hit restored Z. A script that merely replays the recorded session's
coordinates fails even if it exits zero.

## `Channel`-group machine — preset route

### Step 5 — preset smoke test

On a machine whose channel source is a Micro-Manager `Channel` config group, run
the Step 1 request with safe presets and `max_hits=1`. Retain the exported script
and system/audit output.

Pass requires actual search frames before actual acquire frames, one preset
transition into the acquire channel, result hit counts matching datasets, and an
export containing `core.set_config` plus `core.wait_for_config` for both recorded
phase presets. Run that script with Microclaw closed as in Step 4. A script that
contains preset lines but produces no search and acquire frames does not pass.

## Report

Return the Step 0 transcript, exact platform suite counts, proposed tool call,
result JSON, hook logs, audit extracts for both enable streams and dose, event/Z
evidence, dataset paths and operator image observations, exported scripts, and
standalone transcripts. Mark demo-only observations separately from M5 optical
evidence.
