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
agree. Only R3's visual check addresses that.

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

## R1b — settle the objective key, or R2 cannot answer question 1

Added 2026-07-29, after reading M2's config and device-property probe. **This is
cheap and it decides what R2 is worth.**

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
python -c "from ndstorage import Dataset; d=Dataset(r'<path>'); c={k:sorted(v)[0] for k,v in d.axes.items()}; m=d.read_metadata(**c); print({k:m[k] for k in m if 'bjective' in k or 'PixelSizeConfig' in k})" > r1b.txt 2>&1
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

## R3 — verify the affine landed, then hand the dataset over

On the rig, confirm the saved dataset carries a real affine rather than the
sentinel:

```powershell
python -c "from ndstorage import Dataset; d=Dataset(r'<path>'); c={k:sorted(v)[0] for k,v in d.axes.items()}; m=d.read_metadata(**c); print(m['PixelSizeAffine']); print(m.get('XPosition_um_Intended'), m.get('YPosition_um_Intended'))" > r3.txt 2>&1
```

Expect `PixelSizeAffine` to be `0.0;0.127;0.0;-0.127;0.0;0.0` and **not** all
zeros, and both intended-XY keys to be present.

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

## R5 — the one thing only a human can do

The script writes `landmark_check.png`. Look at it and answer one question:

> Is a recognisable asymmetric feature oriented the way it is on the microscope,
> or is the whole mosaic mirrored or rotated?

Every overlap residual can be zero and this can still be wrong, because a
uniformly wrong transform is uniformly wrong in every tile. There is no
automated substitute; if you cannot tell from the image, say so rather than
guessing, and we will find a feature you can.

## Verdicts

Record PASS/FAIL per section. **Stop and report rather than continuing** if R1
shows `Res1` inactive, if R3 shows the all-zeros sentinel, or if R4's stage-X
group is large — the last would mean the transform is wrong and everything built
on it needs re-examining before any more rig time is spent.
