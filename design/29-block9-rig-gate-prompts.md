# design/29 Block 9 — acquisition and landmark gate

Gate for `design29/stage-coordinate-mosaic` (pushed, `9b70d53`). **Do not merge
until this passes.** Two of Block 9's three prerequisites are discharged; this
covers the third, plus the one gate item that needs a human eye.

**The rig is Windows.** Commands are PowerShell; redirection with `>` and `2>&1`
behaves the same in PowerShell and `cmd`.

Keep one dated evidence directory: commands, stdout/stderr, the saved dataset
path, hashes, and a verdict per section.

## What this gate settles, and what it cannot

This is a **discovery** gate, not a regression check. Block 9 is unit-tested at
1092 passed / 99 skipped / 3 warnings and every synthetic geometry case passes,
which is exactly why none of it can answer the two questions below.

1. **Does Block 8's acquisition-recorded calibration branch work on real data?**
   Every dataset we have records the all-zeros sentinel, so that branch has only
   ever been unit-tested. `Res1` now activates, so a new acquisition will stamp
   a real per-image `PixelSizeAffine` for the first time.
2. **Is the mosaic actually correct**, not merely complete, deterministic, and
   self-consistent?

It cannot settle whether the mosaic is globally flipped relative to the
specimen. Every tile would be wrong identically and every overlap would still
agree. R5 was written to address that by eye.

**Superseded 2026-07-29** — see "R5, resolved offline" below. The mirror half of
that question is answered mechanically, by comparing a tile's rendering against
the dihedral group of the raw frame; a bead field is a fine fingerprint for a
*comparison* even though it gives no *absolute* orientation. The remaining half
— whether MM's stage frame is physically right-handed — is a rig property, not a
Block 9 property.

## R1 — confirm `Res1` is active BEFORE acquiring

If `Res1` is not the active pixel-size config at acquisition time, MM stamps the
all-zeros sentinel again and the entire point of this gate is lost. Check first,
not afterwards.

Run `design/29-mm-pixel-affine-probe.py` and confirm:

- `current config` is `'Res1'`
- `Res1` shows `would config activate : YES`
- the current affine is `[0.0, 0.127, 0.0, -0.127, 0.0, 0.0]`, hash
  `d60e84f434efebf41832af1cbb3eb425208921563ec73d9d3c059fe503b046f6`

**Stop if any of these differ.** Do not acquire against a config you have not
confirmed is live.

## R2 — acquire an overlapping grid on structured sample

Requirements, each for a reason:

- **Multi-position.** Intended-XY metadata is absent from single-position
  acquisitions, and without it the mosaic cannot be built at all. This is what
  makes the spiral fixture unusable.
- **Overlapping tiles.** The field is 453×227 px at 0.127 µm/px, which is
  **28.8 µm along stage X and 57.5 µm along stage Y**. A **14 µm step** gives
  roughly 50% overlap in X and 75% in Y. Run A's 20 µm step also overlaps, so
  this is not a new constraint — it just makes the overlap generous.
- **Contrast-rich and in focus.** `run_a_1` was acquired with every laser off
  and is unusable for anything correlational; it is the cautionary case. A
  featureless overlap measures nothing and is silently skipped.
- **A 3×3 or 4×4 grid** is plenty. Nine to sixteen exposures.

Keep the dose as low as the structure allows. This is a geometry test; nothing
about it needs a good image, only a *structured* one.

**Optionally but valuably: also acquire a short unidirectional line along stage
Y** — 5 or 6 positions at 14 µm, all approached from the same direction, no
reversal. Rationale in R4: a plain raster cannot test the Y column, and this
line is the only thing that can. It is a handful of extra exposures.

### The one parameter that can void the whole run

`run_tile_acquisition` **without `hook_strategy` writes one separate dataset per
position**, each single-position, each with no `position` axis and therefore
**no intended XY at all**. That is not a hypothetical: it is exactly how the
design/30 spiral was acquired, and it is precisely why that fixture is unusable
for placement. Sixteen tiles acquired that way would be sixteen unusable
datasets and the gate would have to be re-run.

Passing `hook_strategy` switches to a single Acquisition spanning every position
— one dataset, one `position` axis, intended XY on every frame. Use
`"snr_observer"`: it observes and logs and changes nothing about the
acquisition. This is how `run_a_2` was acquired, which is why `run_a_2` works.

### The Y line is a 1-column grid, and that is the whole trick

`run_tile_acquisition` generates a **plain raster**, not a serpentine: each row
restarts at column 0, so between rows the stage makes a full-width X return —
`(cols-1) × step`. In `run_a_2` that was 4 columns × 20 µm = the 60 µm return
that R4's stage-Y residual is actually measuring.

Set `cols=1` and there is no X motion anywhere in the run. Y steps monotonically
and unidirectionally, one direction, no reversal. `rows=6, cols=1, step_um=14`
is the Y line, and it is the only thing in this gate that can retire "Y is
single-sourced".

### Before you start microclaw (by hand)

1. R1 is passed — `Res1` is the active pixel-size config.
2. Sample in focus, focus lock settled. **Do not run autofocus during the gate**:
   the lock owns Z, and design/28 F1/F2 are unresolved for this metric.
3. Pick the ROI and leave it alone for both runs. Full frame `512×512` is the
   better choice — it matches the calibration's own ROI exactly, is square so X
   and Y overlap equally, and gives more area to correlate. Your `453×227` crop
   also works; a difference is recorded, not refused.
4. Set the Andor readout preset from the `Camera` config group in the GUI.
   Microclaw is not authorized to change it, by design.
5. Both runs use the same ROI, binning and center. Do not change them in between.

### The prompt

Paste this to microclaw:

> I am running the design/29 Block 9 geometry gate. Two acquisitions, both
> saved under `F:/DataSSD/b9_gate`.
>
> First, turn on Luxx488 and set its power to the lowest level that gives
> visible structure — start at 2% and raise it only if the field is flat. Keep
> the exposure near 10 ms. This is a geometry test, so I want structure, not
> signal; do not optimize image quality.
>
> Then run a 4×4 tile grid with a 14 µm step, `protocol="timelapse"` with
> `protocol_params={"n_frames": 1, "interval_s": 0}`, and
> `hook_strategy="snr_observer"`, named `b9grid`. The hook_strategy is
> mandatory — without it each position is written as its own single-position
> dataset with no intended-XY metadata and the run is worthless to me. I need
> one dataset with a `position` axis.
>
> Then, from the same center, run a second acquisition with `rows=6, cols=1`
> and the same 14 µm step, same protocol, same hook_strategy, named `b9yline`.
> One column is deliberate: it steps Y unidirectionally with no X return.
>
> Do not run autofocus, do not move Z, and do not change the ROI, the binning
> or the camera preset between the two runs. Report the save path of each
> dataset and turn the laser off when you are done.

Expect ~22 exposures total. Both runs sit under every confirmation threshold in
the M2 profile, so the only prompt you should see is the illumination
confirmation when the 488 is enabled. **If microclaw asks you to confirm
anything about frames, duration or bytes, something is larger than intended —
stop and read it.**

## R3 — verify the affine landed, then hand the dataset over

On the rig, confirm the saved dataset carries a real affine rather than the
sentinel:

```powershell
python -c "from ndstorage import Dataset; d=Dataset(r'<path>'); c={k:sorted(v)[0] for k,v in d.axes.items()}; m=d.read_metadata(**c); print(m['PixelSizeAffine']); print(m.get('XPosition_um_Intended'), m.get('YPosition_um_Intended'))" > r3.txt 2>&1
```

Expect `PixelSizeAffine` to be `0.0;0.127;0.0;-0.127;0.0;0.0` and **not** all
zeros, and both intended-XY keys to be present.

### R3b — settle the objective key: it decides what R2 was worth

Added 2026-07-29, after reading M2's config and device-property probe. This runs
on R2's saved dataset, alongside the affine check above — it needs no extra
exposure and no second visit to the rig.

`resolve_calibration`'s acquisition-recorded branch needs five identity fields,
not just the affine: objective, binning, camera device, camera model, ROI. Any
one missing and it returns `acquisition calibration identity is incomplete;
missing …` and refuses, with a correct affine sitting right there in the
metadata.

Four of the five resolve on M2. `run_a_2`'s per-image metadata was audited
directly: `Binning='1'`, `Core-Camera='Andor'`, `Andor-Camera='| iXon Ultra |
DU897_BV | 8172 |'` (this is the vendor-key fix in `f941896` earning its keep),
`ROI='36-50-453-227'`. **The objective does not.** Across all 420 metadata keys,
none of `Objective`, `ObjectiveLabel`, `PixelSizeConfig`, `PixelSizeConfigName`
is present — M2 has no objective turret in the config, so the system-state dump
has no objective property to stamp.

The open question is whether MM stamps `PixelSizeConfig` **only when a pixel-size
config is active**. `run_a_2` was acquired with none active (`PixelSizeUm=0`,
sentinel affine), so it cannot distinguish "never stamped" from "not stamped
then". `Res1` is active now, so R2's own first frame settles it — no extra
exposure needed. Against the saved dataset:

```powershell
python -c "from ndstorage import Dataset; d=Dataset(r'<path>'); c={k:sorted(v)[0] for k,v in d.axes.items()}; m=d.read_metadata(**c); print({k:m[k] for k in m if 'bjective' in k or 'PixelSizeConfig' in k})" > r3b.txt 2>&1
```

- **Non-empty** → question 1 is live and R2's dataset answers it. Proceed
  normally.
- **Empty** → the acquisition-recorded branch cannot succeed on M2 for a reason
  that has nothing to do with the affine, and no amount of rig time changes
  that. **This does not stop the gate.** Question 2 — is the mosaic correct —
  is answered through an explicit calibration artifact, which is the documented
  higher-precedence path (design/29 §5) and exactly what R4 already uses. Run R2
  and R3 as written, note the result here, and record it as a finding: on rigs
  with no objective device, acquisition-recorded calibration is unreachable and
  an artifact is mandatory.

Do not "fix" this by inventing an objective key on the rig.


Then copy the dataset off the rig to
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/` as with the other
fixtures, and give the coordinator the path. Hash at both ends.

## R4 — the landmark check (coordinator runs this)

`design/29-block9-landmark-check.py` renders each tile separately through the
same geometry, finds pairs whose placed footprints overlap, and cross-correlates
the two renderings inside the overlap. If the transform and convention are
right, the same feature lands in the same output cell from either tile and the
residual is ~0.

**Why residuals are grouped by stage axis.** A wrong affine is a property of the
transform and cannot be axis-selective — it rotates every tile's content
identically while tile centres come from stage XY, so it inflates every group
together. A residual that is small along one axis and large along the other is
therefore stage error, not a transform error.

Measured on `run_a_2` with the `Res1` affine (2026-07-29):

```
stage-X    n=9    median    5.15 px  (0.65 um)
stage-Y    n=12   median   99.85 px  (12.7 um)
diagonal   n=18   median   60.93 px
```

That is a **third** independent confirmation of the X column, after MM's
calibrator and the raw-tile correlation. It also shows why R2's optional Y line
matters: `run_a_2` is a plain raster, so every Y-adjacent pair is separated by a
60 µm X return, and the Y residual is measuring uncorrected backlash rather than
anything about the transform. design/29 §4 already states the mosaic consumes
intended XY and cannot correct an unrecorded achieved-XY residual.

**Pass criteria.** The stage-X group stays small (single-digit px median) on the
new dataset, confirming nothing regressed. If the optional Y line was acquired,
its stage-Y median should also be small — that would complete the 2×2 and let
design/29 stop describing Y as single-sourced. If the Y line still shows a large
residual, that is a **stage** finding to record, not a Block 9 defect, and the
merge is not blocked by it.

## R5 — the one thing only a human can do (SUPERSEDED, see below)

The script writes `landmark_check.png`. Look at it and answer one question:

> Is a recognisable asymmetric feature oriented the way it is on the microscope,
> or is the whole mosaic mirrored or rotated?

**This ran and could not be answered**: both gate datasets are fields of
isolated beads, which have no handedness. Rather than book more rig time for a
sample that does, the mirror question was reformulated and answered offline —
next section. Keep this section for the reasoning; do not re-run it.

Every overlap residual can be zero and this can still be wrong, because a
uniformly wrong transform is uniformly wrong in every tile. There is no
automated substitute; if you cannot tell from the image, say so rather than
guessing, and we will find a feature you can.

## R5, resolved offline — no graticule, no rig time

**R6b is withdrawn.** R5 asked whether the mosaic is mirrored, and a bead field
was said to be unable to answer it. That was half right. A bead constellation
cannot give *absolute* orientation, but it is a perfectly good fingerprint for a
*comparison* — and the comparison that matters is the tile's rendering against
the raw camera frame.

`design/29-block9-mirror-check.py` renders one tile and scores it against all
eight dihedral transforms of the raw frame. On both gate datasets:

```
rot90 CCW              NCC  1.0000   <== MATCH
rot90 CW               NCC  0.0349
MIRROR lr + rot90 CW   NCC  0.4174
MIRROR lr + rot90 CCW  NCC -0.0030
```

An exact 90° CCW rotation, no reflection — consistent with the affine's own
positive determinant (`+0.016129`). The renderer does not mirror.

**What genuinely remains untestable, and why it is not ours.** Whether MM's stage
frame is physically right-handed — whether MM's +X is the direction the specimen
actually travels — cannot be settled from any self-consistent set of images,
because everything in a dataset lives in MM's frame. But that is a rig and
Micro-Manager property, identical for MM's own Preview and position list, and it
is flip-invariant for distances, counts, and drive-back-to-a-coordinate. It is
not a Block 9 question and Block 9 should not be held for it.

## R6 — the one thing still open: stage X

The 10% stage-X residual is the only unresolved item, and the operator's
suggestion is the right experiment: **steps small enough that the same beads
stay in the field**, so individual beads can be tracked in raw pixel coordinates
with no placement, no mosaic, and no correlation ambiguity anywhere in the
measurement.

The field is 206 px ≈ 26.2 µm along stage X. Two runs, **the same 16 µm of total
travel** in each:

> Run a tile acquisition `rows=1, cols=9`, **2 µm** step, same protocol and
> `hook_strategy="snr_observer"`, named `b9xfine2`. Then `rows=1, cols=5` at
> **4 µm**, named `b9xfine4`. Same ROI, exposure, laser and centre for both.
> Then the same two runs along Y (`cols=1`, `rows=9` @ 2 µm and `rows=5` @ 4 µm),
> named `b9yfine2` and `b9yfine4`.

Holding total travel constant is what makes it a discriminator:

- **Scale error** → total error is 10% of 16 µm ≈ 1.6 µm in **both** runs.
- **Fixed per-move offset** → eight 2 µm moves accumulate twice the error of four
  4 µm moves.

Each 2 µm step displaces content by 15.7 px, comfortably measurable, and a bead
entering at one edge survives the whole 16 µm sweep. The bead track also shows
the direction of travel directly, confirming the sign of the affine's X column
against the live camera independently of every correlation method used so far.

**State the degeneracy plainly, because it does not go away.** This measures
px-of-content per µm-commanded, which is `actual_moved / (commanded × true µm/px)`
— one number, two unknowns. It cannot separate "the stage under-moves by 10%"
from "the pixel size is 10% larger than 0.127". A graticule would, and there
isn't one. **It does not matter for the mosaic**: placement needs exactly the
px-per-commanded-µm ratio, which is exactly what this measures. The unresolved
physics is real and operationally irrelevant here — record it, do not chase it.

## Verdicts

Record PASS/FAIL per section. **Stop and report rather than continuing** if R1
shows `Res1` inactive, if R3 shows the all-zeros sentinel, or if R4's stage-X
group is large — the last would mean the transform is wrong and everything built
on it needs re-examining before any more rig time is spent.
