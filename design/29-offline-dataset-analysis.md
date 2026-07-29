# design/29 — offline analysis of saved datasets (mosaic / analyze / orient)

Promotes **design/28 Finding 5** into its own design. Finding 5 was the recurring
one: every "count the cells in the mosaic," "stitch the tiles," or "is the
orientation right?" question in rig session `20260717_133127` forced either a
live re-scan (36–2500 fresh exposures of bleaching) or the user's own eyes,
because **no tool reads a saved dataset back for analysis**. Everything microclaw
can measure, it can only measure *live*, at the moment of acquisition.

## Why this hurts (evidence from the session)

- **The 900 µm scans produced zero cell counts.** The `mosaic_cell_counter` hook
  only ever ran live on the first 100 µm 561 scan → 11 cells. The 488 channel and
  both 2500-tile 900 µm mosaics went through the *stitcher* hook (places pixels,
  doesn't count), so the headline question — "how many cells" — is unanswered for
  every scan except the first. Re-answering it means re-imaging 2500 tiles.
- **The orientation fix cost four full re-scans.** The transpose→rot90→flip
  debugging (`rot90_k=1, flip_after=true`) took four 36-tile acquisitions because
  the agent could not read its own output TIFF back to check; each hypothesis
  needed the user to open the file and report. A read-side mosaic would have made
  each iteration a zero-dose recompute.
- **`export_dataset_as_tiff` — the one tool that touches saved data — *was* broken**
  for multi-position datasets (design/28 Finding 3, `KeyError: 'position'`), so at
  the time of the session even dumping the tiles for external analysis failed. That
  fix is now merged to main (`tools.py::export_dataset_as_tiff`), but it landed as
  an *inline* real-coords + `has_image` traversal, not a reusable helper — see the
  shared-traversal note under Proposed shape §2.

A second session made the same gap concrete. The **Pallavi spiral session
(2026-07-20, design/30 Finding 2)** acquired 25 positions on a *spiral* and got a
5×5 row-major **contact sheet** — panels in acquisition order, not placed by stage
XY — because the only montage path was a live hook. The tiles and their stage
coordinates were saved, so a correct placement is a zero-dose offline recompute;
design/30 explicitly delegates that coordinate-aware mosaic to this design. A
spiral is the sharpest case for design/29's core claim: there is no row/column
grid to fall back on, so placement *must* come from the recorded intended-XY.

The dose asymmetry is the core problem: analysis is cheap (CPU on bytes already
on disk) but is currently priced at the cost of a full re-acquisition. This is the
same dose accounting design/32 Finding 2 proposes to budget with a session dose
ledger — offline analysis is the mechanism that keeps re-analysis *off* that
ledger entirely.

## The enabling fact — and its limit

The pixels **and** the per-tile stage XY are already saved. `ndstorage.Dataset`
exposes both without any re-imaging:

- `dataset.axes` — ordered coordinate values per axis (`position`, `channel`, …).
- `dataset.read_image(**coords)` / `dataset.has_image(**coords)` — pixels.
- `dataset.read_metadata(**coords)` — per-image metadata, which carries
  `XPosition_um_Intended` / `YPosition_um_Intended` for Microclaw's
  multi-position acquisitions (see [[hook-metadata-has-xy]] / design/23 F2).

Stage-coordinate placement can therefore be reconstructed from disk. Pure geometry
should be shared between live and offline paths where their inputs actually match,
rather than re-authored in per-session generated hook bodies.

That does **not** make the paths generally equivalent. A live analyzer may see
pre-save pixels, mutable metadata, and acquisition state; an offline analyzer sees
only the processed pixels and metadata that survived into the dataset. Replay tests
must establish equivalence for any analysis that claims it.

## Probe findings (2026-07-28)

These findings cover three systems and one historical acquisition family; they
are not a survey of Micro-Manager installations.

| System | Available pixel-size configs | Current config | Current scalar | Affine verdict |
|---|---|---|---|---|
| M5 | none | empty | 0.0 µm/px | current all-zero sentinel; genuinely unconfigured |
| M2 | `Res0` | empty (`Res0` does not activate) | 0.0 µm/px; `Res0` is operator-reported as 0.127 µm/px | current all-zero sentinel; `Res0` identity default |
| MM demo | `Res10x`, `Res20x`, `Res40x` | `Res10x` (matching and active) | 1.0 µm/px | current and all three by-ID affines are the same identity default |

M5's clean ROI is `(0,0,2304,2304)` and its camera is
`HamamatsuHam_DCAM`. On M2 the exact blocking predicate is
`'SmarActXY'.'Frequency' expected='5' live='5000'` (the other two rules
match). More importantly, `Res0`'s own affine is identity. Correcting the rule
would therefore expose its scalar but no orientation. The enhanced probe now
calls `get_pixel_size_um_by_id` alongside each by-ID affine; because that call
was added after these runs, M2's configured 0.127 µm/px remains
operator-reported rather than machine-confirmed.

The demo is positive evidence that identity means **default**, not calibration:
its 10x, 20x, and 40x configs all contain the same identity affine and the same
canonical SHA-256, `3c9a2409d8902b67c481f68efd9606bac9a430202c3334d3379ced14433fe893`,
even though 20x cannot have the same µm/px as 10x. The identical hash also
appears on unrelated M2's `Res0`. Identity is thus an empirically confirmed
hazard, not merely a theoretical one: the demo's healthy, matching, active path
passes a naive finite+nonsingular check and would silently yield 1 µm/px,
unrotated, for every objective.

The original probe had decoded MMCore's
row-major `[m00,m01,m02,m10,m11,m12]` as Java `getMatrix` order, canonicalized
before printing, manufactured NaN for zero-length axes, omitted live config-rule
values, iterated a bridged Java Rectangle instead of reading camelCase fields,
and inspected only summary metadata. The repair prints raw values first,
distinguishes non-finite/singular/all-zero/identity verdicts, enumerates every
config and by-ID affine, reads Rectangle fields, and walks per-image metadata.
`javap` confirms local `AffineUtils.doubleToAffine` reads indices
`0,3,1,4,2,5`. Fixed live runs on M5, demo, and M2 answer the three gate
questions: none exposes a measured affine; config activation can hide a scalar
without hiding a useful affine; and the saved datasets identify neither a
historical transform nor calibration identity.

On these three available systems, MM is not a calibration source. Microclaw's
own `calibration.solve_affine` plus its knowledge base is the primary source.
The MM-sourced resolver branch remains correct and cheap, but will normally fall
through; it is not treated as a supported acquisition-recorded identity.

MM stores the pixel-size scalar and the affine separately, which is why a config
can carry a correct scalar and a default affine at the same time.

### Do not build a move/snap affine spike — Micro-Manager already ships one

The earlier "unverified lead" is now **verified by inspection** of the local
`MMJ_.jar` (`Micro-Manager-2.0.3-20260625`, matching the rigs' MMCore 12.5.0).
The pixel calibrator is not a plugin; it is internal to MM:

```
org.micromanager.internal.pixelcalibrator.CalibrationThread
    protected java.awt.geom.AffineTransform result_    <- a FULL affine, not a scalar
    java.awt.geom.AffineTransform getResult()

org.micromanager.internal.pixelcalibrator.AutomaticCalibrationThread
    public static java.awt.geom.Point2D$Double measureDisplacement(
        ij.process.ImageProcessor, ij.process.ImageProcessor, boolean)

org.micromanager.internal.pixelcalibrator.ManualPreciseCalibrationThread
org.micromanager.internal.pixelcalibrator.ManualSimpleCalibrationThread
org.micromanager.internal.pixelcalibrator.PixelCalibratorDialog
    public java.lang.Double getCalibratedPixelSize()
    public double safeTravelRadius()          <- bounded stage travel
```

`measureDisplacement` moves the stage, snaps, and cross-correlates: it **is** the
move/snap measurement this design proposed, already implemented and far better
tested than anything we would write. `result_` being an `AffineTransform` proves
it yields the full 2×2 rather than a scalar, and `safeTravelRadius` proves travel
is bounded. Three modes exist — Automatic, ManualPrecise, ManualSimple — so a
field where automatic correlation fails still has a path.

The result is written back through
`org.micromanager.internal.dialogs.PixelSizeProvider`, which declares
`getAffineTransform` / `setAffineTransform` and is implemented by both
`PixelConfigEditor` and `PixelPresetEditor` (reached from `CalibrationListDlg`).
So the calibrator is launched from the pixel-size config editor and stores its
affine into that config, exactly where our probe's `get_pixel_size_affine_by_id`
already reads.

**The launch control is resolved** (2026-07-29, `javap` + constant-pool strings
on the local `MMJ_.jar`). `AffineEditorPanel` — the "Affine Transform (Rotation
and Scaling)" box inside the Pixel Preset Editor — carries three buttons:

| Button | What it does | Use it? |
|---|---|---|
| `Measure` | constructs `PixelCalibratorDialog(Studio, PixelSizeProvider)`; that dialog offers `Select method:` = Automatic / Manual-Precise / Manual-Simple, a `Safe travel radius, um:` combo, and `Start` | **yes** — this is the real move/snap calibrator |
| `Calculate` | calls `PixelSizeProvider.getPixelSize()` + `AffineUtils.noTransform()` to synthesize a **pure-scale** affine from the scalar | **never** |
| `Reset` | restores `originalAffineTransform` | — |

`Measure` only opens the dialog; the run starts at `Start` inside it.

**`Calculate` is a trap and must be named as one.** It fabricates a
non-sentinel-looking affine with zero rotation and no measurement behind it —
which would pass any finite-and-nonsingular check, satisfy the
"obtain a dataset with a non-sentinel affine" prerequisite, and be a lie. The
same fabrication is offered as a prompt: `PixelPresetEditor` contains the string
`"Affine transform appears wrong.  Calculate from pixelSize?"`, which MM raises
precisely because the stored affine is identity. **Answer No.** This is the
identity-default hazard of the demo's 10×/20×/40× configs, except self-inflicted.

**Consequence for this design: the outstanding Y column is an operator action,
not an implementation task.** Do not write a move/snap spike. Run MM's calibrator
on a contrast-rich, in-focus field — it is a correlation method and will fail on
a blank or dim field exactly as `run_a_1` did — then rerun our probe. Success is
that config's affine no longer reporting `IDENTITY`. On M2 the config's blocking
predicate must be corrected first, or the measured affine has no active config to
land in.

That rerun is also the **independent cross-check**: MM's calibrator and our
`run_a_2` correlation are wholly separate methods, so agreement on ~90°
stage/camera rotation at ~0.13 µm/px would settle the convention outright.
Disagreement is equally informative and must be resolved before either is
trusted.

The fixed dataset arm confirms Run A intended XY on every image, string position
values, and varying axes (`position+time`, plus Z on the sparse revisit). Summary
affine is `Undefined`; per-image `PixelSizeAffine` is all-zero.

**Run A was acquired on M2, not M5** (operator-confirmed 2026-07-28). Its device
set — Andor iXon DU897 at a 453×227 ROI, `SmarActXY`/`SmarActZ`, Luxx 405/488/638,
Cobolt561 — matches M2's probe, whose `Res0` rules key on exactly the
`SmarActZ.Frequency`, `SmarActXY.Frequency`, and `SmarActXY.Hold time (ms)`
properties Run A recorded, at the same `5000`/`10` values. M5 is a different
microscope: Hamamatsu at 2304×2304 with an iChrome-MLE-TCP engine. Do not read
the Andor→Hamamatsu difference as one rig's detector being swapped; these are two
instruments, and that distinction is what §5's camera-identity requirement is
actually about.
`run_a_1` is unusable for a convention check: every acquisition laser is off and
only 0.238% of pixels exceed background + 5×MAD (min/max 146/328). Its old weak
correlations measured read noise and carry no verdict. Signal-bearing `run_a_2`
gives a **PARTIAL** result after overlap-normalized correlation. Seven of nine +X
pairs form the cluster `(dy,dx)={(139,1),(140,2),(140,1),(157,2),(162,2),
(163,2),(163,2)}` px (combined IQR `(22.5,0.5)` px). Thus stage +X displaces
content almost entirely along image rows: stage/camera axes are about 90° apart.
The implied 0.1227–0.1439 µm/px brackets M2's configured 0.127 µm/px. Because
Run A is M2, that is agreement on **the same optical path**, from two independent
methods — a stored operator calibration and a correlation over saved tiles — not
a coincidence between similar instruments. It is the strongest corroboration
available today for both the scale and the row-major decode. The two shift modes,
139–140 and 157–163 px
(roughly 20 versus 23 µm at 0.13 µm/px for the same intended 20 µm step), remain
unexplained and cannot be resolved from saved data.

The Y column is **UNRESOLVED**: normalized peaks do not form a cluster (IQR
spread in the coordinator cross-check was `(227,182)` px), so no complete 2×2 is
reported. The acquisition is a plain raster: each Y-adjacent comparison crosses
a 60 µm X return from c3 to c0, whereas X pairs are small unidirectional moves.
Uncorrected backlash after that reversal is the leading hypothesis, not a
finding. Bleaching and drift are not supported: tiles are ~0.7 s apart over
7.8 s and mean intensity has no monotonic trend (time correlation 0.03). The
dataset records only intended XY, never achieved stage XY, so backlash or other
settling error cannot be tested retrospectively. `run_a_revisit_top2_1` has real
signal but no adjacent 20 µm pairs.

The measured X column is a consistency check every future measured or MM-sourced
affine for this optical path must satisfy: about 90° stage/camera rotation at
roughly 0.13 µm/px. The smaller remaining proposal is to resolve Y and confirm X
on a contrast-rich field, approaching every reference and destination from one
direction to avoid raster reversal. It remains a proposal only; no stage move or
exposure was made.

## Calibration session, and what it corrected (2026-07-29)

M2 now carries a measured affine. Four things in the section above were wrong,
and one of them was ours.

**The Y column is resolved, and X is corroborated.** MM's calibrator produced,
in a new config `Res1`:

```
pixel->stage 2x2  : [[0.0, 0.127], [-0.127, 0.0]]
x-axis angle deg  : -90.0     x/y scale: 0.127  0.127
reflection        : False     determinant: 0.016129
canonical SHA-256 : d60e84f434efebf41832af1cbb3eb425208921563ec73d9d3c059fe503b046f6
```

Inverting it, a +20 µm stage X step should displace image content **157.5 px
along rows and 0 along columns**. `run_a_2`'s measured cluster was
`(dy,dx)` = (139,1) (140,2) (140,1) (157,2) (162,2) (163,2) (163,2). The upper
mode, 157–163 px, is 19.94–20.70 µm for an intended 20 µm. Two wholly
independent methods — MM's move/snap cross-correlation and our overlap-normalized
correlation over saved tiles — agree on ~90° at 0.127 µm/px. The convention is
settled for X.

**Y is supplied, not corroborated.** The calibrator gives the full 2×2, but
`run_a_2` could never resolve Y, so nothing independent checks that column. Do
not describe the 2×2 as cross-validated; describe X as cross-validated and Y as
single-sourced. The 139–140 mode (17.65–17.78 µm, ~11% short) remains
unexplained and is still only consistent with, not evidence for, the raster-
reversal backlash hypothesis.

**Record the suspicious exactness.** Exact zeros, exactly ±0.127, exactly
−90.00°, zero shear, and both scales identical to the digit is cleaner than a
correlation fit normally lands, and 0.127 was already `Res0`'s scalar. The
operator confirms every value came from the calibrator and nothing was typed. It
is recorded as measured — but if MM turns out to regularize on store, this
paragraph is how a future reader spots it.

**M2's "does not activate" was our artifact, not a misconfigured rig.** The
blocking predicate is real but its cause is config-string format drift. The
post-calibration probe shows both configs against one unchanged machine state:

```
Res0  rule[1]: 'SmarActXY'.'Frequency' expected='5'    live='5000'  MISMATCH -> activate NO
Res1  rule[1]: 'SmarActXY'.'Frequency' expected='5000' live='5000'  MATCH    -> activate YES
```

`Res0` was authored years ago and stored `5`; `Res1` captured the *same* state
today and stored `5000`. The Device Property Browser shows `5`, with an allowed
range of 1–18500. Micro-Manager's own CoreLog writes these values with locale
separators embedded — `SmarActXY/Frequency:18,500`, `SmarActZ/Frequency:18,315`
— i.e. property values pass through a locale-aware formatter. A value formatted
under one convention and parsed under another turns 5 into 5000, which is
exactly the observed drift on a de_AT system. **Correction: the instruction to
"correct M2's blocking predicate" pointed at the wrong thing.** Creating a new
pixel-size config is the right move; repairing the old one is not, and editing
the live device to match a stale string would have been actively wrong.

**MM's calibrator can crash the rig, and the workaround is a new config.**
Repeated attempts against `Res0` killed Micro-Manager outright. The CoreLog ends
mid-sequence with no exception:

```
08:49:06.635  [dev:Andor] Stopped sequence acquisition
08:49:06.657  [dev:Andor] PrepareSnap();          <-- last line in the file
```

A native process death 22 ms after aborting an iXon sequence acquisition. The
same transition survived twice earlier at 13 ms and 20 ms, so it is a race in
the Andor SDK's wind-down, not a deterministic fault, and not the calibration
config. Calibrating into a **new** config succeeded with no crash. Separately,
every `SmarAct*/Frequency` write blocks MM's event thread for 5.0–7.5 s
(`EDTHangLogger`), which reads as a freeze and is not one. None of this
overturns "do not build a move/snap spike" — the calibrator did the job once it
had somewhere to write.

**The spiral fixture cannot be placed, and it is M2.** Corrections: the spiral
was acquired on **M2**, not a third instrument — same Andor iXon DU897_BV serial
8172 at the same `36-50-453-227` ROI as Run A. And it is **25 separate
single-position datasets** (`spiral_NN/spiral_NN_1`), each with axes `{'time':
[0]}` and no `position` axis. Per-image intended XY is **absent**, exactly as §4
predicts for a single-position acquisition, so the tool refuses it — verified.
The coordinates exist only in the sidecar `montage_hook_log.txt` as `x_um` /
`y_um` per tile. Consuming a sidecar is a different input contract from reading
dataset metadata and is not in scope here. Note also the geometry: a 200 µm step
against a 453×227 tile at 0.127 µm/px (57.5 × 28.8 µm) leaves the tiles entirely
non-overlapping, so even with coordinates this fixture exercises **gaps and
coverage**, never seams.

**A third confirmation of X, from placed-mosaic overlaps.**
`design/29-block9-landmark-check.py` renders each tile separately through one
shared geometry and cross-correlates pairs inside their placed overlap. On
`run_a_2` with the `Res1` affine, grouped by intended stage displacement:

```
stage-X    n=9    median   5.15 px  (0.65 um)
stage-Y    n=12   median  99.85 px  (12.7 um)
diagonal   n=18   median  60.93 px
```

A wrong affine cannot be axis-selective — it rotates every tile's content
identically while tile centres come from stage XY, so it would inflate all three
groups together. So this corroborates X a third time (after the calibrator and
the raw-tile correlation) and independently reproduces the Y non-clustering from
a different method entirely. The stage-Y figure is measuring the raster's 60 µm
X return, i.e. uncorrected backlash, exactly as §4 warns: placement consumes
intended XY and cannot correct an unrecorded achieved-XY residual. **Testing the
Y column requires a unidirectional line**, not a raster; that is R2 in
`design/29-block9-rig-gate-prompts.md`.

**ROI must not gate.** This section already said a constant off-centre ROI "adds
only a global translation and is harmless for relative placement", and Block 9's
first implementation refused on it anyway — which blocked the first genuinely
measured affine we have from reaching the first fixture we have, because the
calibrator ran at full frame `(0,0,512,512)` and Run A is a `453×227` crop.
Placement consumes only the affine's four coefficients and ROI never enters that
arithmetic. Refuse on camera device/model and binning, which change the transform
or the instrument; **record** ROI differences.

**M2 has no objective, and four-fifths of an identity is a refusal.** Reading
M2's config and device-property probe to build the Block 9 rig-gate safety
profile turned up something the gate had not accounted for. The
acquisition-recorded branch needs five identity fields, and the affine is only
one of them. Audited directly against `run_a_2`'s 420 metadata keys:

| field | key that resolves it | present on M2 |
|---|---|---|
| binning | `Binning` | `'1'` |
| camera_device | `Core-Camera` | `'Andor'` |
| camera_model | `Andor-Camera` | `'\| iXon Ultra \| DU897_BV \| 8172 \|'` |
| roi | `ROI` | `'36-50-453-227'` |
| **objective** | `Objective` / `ObjectiveLabel` / `PixelSizeConfig` / `PixelSizeConfigName` | **none present** |

M2's config declares no objective turret, so the system-state dump has no
objective property to stamp. `_resolve_from_acquisition` therefore returns
`acquisition calibration identity is incomplete; missing objective` with a
perfectly good affine in hand, and `resolve_calibration` refuses when no explicit
`calibration_ref` is supplied.

Whether MM stamps `PixelSizeConfig` only while a pixel-size config is *active* is
untested and unresolvable from what we have: `run_a_2` ran with none active, so
"never stamped" and "not stamped then" are indistinguishable in it. `Res1` is
active now, so R2's own first frame settles it at zero extra cost — that is
`R1b` in `design/29-block9-rig-gate-prompts.md`.

This does not block Block 9. The artifact path is the documented
higher-precedence route (§5) and is what the landmark check already uses; the
mosaic still builds. What it does mean is that **gate question 1 may be
unanswerable on M2 for a reason unrelated to the affine**, and that on any rig
without an objective device an explicit artifact is mandatory rather than
merely preferred. Note the asymmetry with the camera model: that was a real key
under a vendor-specific name and the fix was to look wider (`f941896`). This one
is a key that does not exist, and widening the search would only invent it.

**Footnote on `-0.0`.** The `.cfg` stores `PixelSizeAffine,Res1,-0.0,0.127,...`,
and `serialize_affine_payload` emits `"a":-0.0` where `0.0` gives `"a":0.0` — a
different SHA-256. Nothing in the current paths breaks on it: the cross-frame
comparison at `calibration.py:367` uses `!=`, and `-0.0 != 0.0` is False in
Python; the artifact hash check compares an artifact against itself. The
exposure is narrow and latent — a stored `version_key` built from `-0.0` will not
match one built from `0.0` for the same physical calibration. Recorded here so
it is recognised rather than rediscovered.

## The Block 9 rig gate, run (2026-07-29)

Two acquisitions on M2 with `Res1` live: `b9grid_2` (4×4 raster, 14 µm) and
`b9yline_1` (6×1 unidirectional Y line, 14 µm). Both at a `263-95-222-206` ROI —
not the full frame the R1 probe reported, which matters only in that a 26×28 µm
field makes two-apart tiles meet in a sliver. Sparse fluorescent beads, Luxx488
at 2.0%, 10 ms.

**The affine landed. This is the first non-sentinel per-image affine we have.**
`PixelSizeAffine = -0.0;0.127;0.0;-0.127;0.0;0.0`, intended XY present on every
frame. Block 8's acquisition-recorded stamping works.

Note the value is literally `-0.0`, exactly as the `.cfg` stores it. The footnote
above stops being hypothetical: MM hands back the signed zero, so any
`version_key` derived from this dataset is built from `"a":-0.0`.

**The objective key is absent even with a config active.** R3b returned `{}`.
That settles it: MM does not stamp `PixelSizeConfig`, and on M2 the
acquisition-recorded branch can never complete an identity no matter how good
the affine is. An explicit artifact is mandatory here. Gate question 1 is
answered, negatively, for a reason that has nothing to do with the transform.

**The Y column is corroborated. Stop calling it single-sourced.** The Y line is
the clean experiment: no X motion anywhere in the run, so no reversal and no
return. Its **cross-axis** residual — content displacement along stage X, which
should be zero — is `+0.40 px, sd 0.05` (0.05 µm) across all five adjacent
pairs. A wrong Y column would misplace a 14 µm Y move by up to 110 px. It
misplaces it by two fifths of one pixel. Along Y itself the residual is
`+1.20 px, sd 6.09` — zero plus about ±0.8 µm of random stage jitter.

**Stage X carries a systematic 10% error, and it fails R4's stated criterion.**
Across all twelve stage-X pairs in the grid:

```
stage-X   residual along X : median +11.00 px  mean +11.07  sd 1.27   (+1.40 um)
          residual along Y : median  -0.30 px  mean  +0.20  sd 1.98   (-0.04 um)
stage-Y (Y line, clean)
          residual along X : median  +0.40 px  mean  +0.36  sd 0.05   (+0.05 um)
          residual along Y : median  +3.00 px  mean  +1.20  sd 6.09   (+0.38 um)
```

`sd 1.27` over twelve pairs spanning every row and every column step is not
scatter; it is a systematic offset of 1.40 µm on a 14 µm step, **10.0%**. R4's
criterion was a single-digit px median. This is 11.

What it is **not**:

- Not a rotation error. Cross-axis terms are ~0 in both directions.
- Not an isotropic scale error. That would inflate the Y line identically; the Y
  line shows 1.1% ± 5.5%.
- **Not backlash.** The raster returns −42 µm in X between rows, so `c0→c1` is
  the first step after a reversal while `c1→c2` and `c2→c3` continue in the same
  direction. If backlash were the cause, `c0→c1` would stand out. It does not:
  `c0→c1` reads 10.70 / 10.90 / 12.60 / 12.30 across the four rows, and the
  continuing steps read 7.90–12.50. Indistinguishable. This is a scale-type
  error, not a reversal offset.

What remains degenerate: a 10% stage-X scale error and a 10%-low affine X scale
(0.127 where the truth is ~0.1397) predict identical placement residuals. Note
0.1397 falls inside design/28 F4's measured 0.1227–0.1439 range. Placement alone
cannot separate them — an X line at two different step sizes distinguishes
scale from offset, and separating stage scale from affine scale needs an
independent length reference (a graticule), not more mosaics.

**R5, reformulated and answered offline (no graticule, no rig time).** The
first reading below was half wrong, and the correction is worth stating plainly.

A bead field gives no *absolute* orientation, so it cannot say "this points the
way it does down the eyepiece". But the mirror question does not need absolute
orientation — it needs a *comparison*, and a random bead constellation is an
excellent fingerprint for one. `design/29-block9-mirror-check.py` renders a tile
and scores it against all eight dihedral transforms of the raw camera frame:

```
rot90 CCW              NCC  1.0000   <== MATCH
rot90 CW               NCC  0.0349
MIRROR lr + rot90 CW   NCC  0.4174
MIRROR lr + rot90 CCW  NCC -0.0030
```

Identical on both gate datasets. An exact 90° CCW rotation with no reflection,
consistent with the affine's own positive determinant (`+0.016129`). **The
renderer does not mirror.**

What is genuinely left is whether MM's stage frame is physically right-handed —
whether MM's +X is the direction the specimen actually travels. No self-consistent
set of images can settle it, because every coordinate in a dataset lives in MM's
frame. But it is a rig and Micro-Manager property, identical for MM's own Preview
and position list, and it is flip-invariant for distances, counts, and
drive-back-to-a-coordinate. It is not Block 9's to answer.

**The original reading, kept for the reasoning:**

**R5 cannot be answered on this sample.** Both PNGs are fields of isolated
beads. A bead field has no asymmetric feature, so it cannot show a global mirror
or 90° flip — every residual would be identical either way. The gate anticipated
exactly this and said to say so rather than guess. **The global-orientation
question is still open**, and it is the one open question that is genuinely about
Block 9's correctness rather than about the stage.

**One script defect the data exposed.** `--min-overlap-px` defaulted to a flat
400. Two tiles a full field apart met in a 412 px sliver — about one output row —
and `phase_cross_correlation` returned 98.90 and 73.40 px of noise, which then
set the script's own reported p90 and max. A sliver is not a small measurement;
it is not a measurement. The floor is now 5% of a tile's area, and the Y line's
reported max drops from 67.90 px to 9.80. Separately, two grid pairs with real
overlap still returned ~99 px: sparse beads give a correlator few features to
lock onto, and it can lock onto the wrong one. That is a sample property, not a
bug.

## R6, and the correction it forced (2026-07-29)

R6 ran: `b9xfine2/4`, `b9yfine2/4`, `b9xline14/28`, plus repeats `b9grid_3` and
`b9yline_2`. The headline is that **the reported affine's orientation is right
and its scales are not**, and the second headline is that two of our own
measurement methods were unfit for this sample and had to be replaced.

### The measurement, from motion, on raw frames

`design/29-block9-affine-from-motion.py` solves the 2×2 from commanded motion
alone — no mosaic, no placement code, and no assumption about the affine's
shape. Four independent series, two step sizes per axis, **the same 16 µm of
total travel in each**:

```
series        step   n   total px   total um   um/px
b9xfine2_1    2.0    8     148.88      16.00   0.10747   stage-X
b9xfine4_1    4.0    4     150.15      16.00   0.10656   stage-X
b9yfine2_1    2.0    8     130.46      16.00   0.12265   stage-Y
b9yfine4_1    4.0    4     130.09      16.00   0.12300   stage-Y
```

Pooled (24 cumulative observations, residual median **0.023 µm**, max 0.438):

```
                    column 0    column 1
measured um/px       0.12254     0.10706
reported um/px       0.12700     0.12700
ratio                 -3.5%      -15.7%
angle vs reported    +0.30 deg   -0.02 deg
handedness           AGREE (det +0.0131 vs +0.0161, both positive)
```

**Orientation and handedness are confirmed to a third of a degree.** The 90°
structure, the signs, and the absence of shear all hold. **The scales do not**:
one column is 3.5% low, the other 15.7% low, and the truth is **anisotropic by
14% between axes where the config reports perfect isotropy**.

design/29 already recorded the suspicion — "exact zeros, exactly ±0.127, exactly
−90.00°, zero shear, and both scales identical to the digit is cleaner than a
correlation fit normally lands". **That suspicion is now vindicated by
measurement.** The exactness was a symptom. This is direct evidence for the
standing position that MM is not a calibration source and
`calibration.solve_affine` plus the knowledge base is primary.

### It is a scale error, not a per-move offset — the discriminator fired

This was the whole point of holding total travel constant. Eight 2 µm moves and
four 4 µm moves cover the same 16 µm: a fixed per-move offset would leave the
8-step series differing from the 4-step series by four times the offset. They
differ by **under 1%** on both axes. Scale, definitively.

The per-step numbers are nonetheless bimodal — identical 2 µm commands produce
14.8 or 22.4 px — which is stage quantization of roughly 0.8 µm, averaging out
over a run. That is a stage property to record, not a transform property.

### Two of our own methods were wrong for this sample

**A (row, col) vs (x, y) convention bug reported the rig as REFLECTED.** The
first version of the motion script solved with numpy's `(row, col)` while
MMCore's affine acts on `(x, y)` where x is the column. That silently transposed
the basis, turned M2's 90° rotation into a diagonal matrix, and produced
`determinant −0.0105 → REFLECTED`. Nothing was wrong with the rig. It was caught
only because the *structure* came out diagonal where a rotation must be
anti-diagonal — the determinant sign alone would have been believed. Convention
conversion now happens in exactly one function, `to_xy`, with the failure
recorded in its docstring.

**Phase correlation is unfit for sparse blinking puncta.** On these datasets it
returned `err=1.0` on every pair and "measured travel" that saturated at ~2 µm
for commanded moves from 2 µm to 140 µm — a spurious near-zero peak reported as
a number rather than a failure. The sample is a handful of puncta whose
population changes frame to frame (4–12 detected per frame, bright-pixel counts
varying 2× across a 2 µm step). Whitening the spectrum destroys what little
signal there is. The replacement detects puncta and votes on pairwise offsets,
which is robust to partial correspondence — appearing and vanishing puncta
simply cast no vote — and reports the vote count so a weak estimate is visible.

**This impugns the landmark check on this sample class**, because it correlates
too. Its medians do move the right way when handed the measured affine
(stage-X 11.01 → 8.98, diagonal 16.03 → 10.92, stage-Y 8.73 → 5.51 on
`b9grid_2`; same direction on `b9grid_3`), which corroborates the measurement,
but it cannot give a sharp number here and single pairs still return ~99 px of
noise. **Treat the landmark check as a falsification test, not a metrology
tool**, and treat the motion script as the metrology.

### Reproducibility, old runs vs new

- `b9grid_2` stage-X median 11.01 → `b9grid_3` 10.85. Reproduces.
- Mirror check: `rot90 CCW` at NCC 1.0000 on `b9grid_3`, `b9yline_2` and
  `b9xfine2_1`, identical to the first pass. Reproduces exactly.
- `b9yline_1` stage-Y median 4.42 → `b9yline_2` 8.51, with one pair returning
  −18.70/−62.50. That spread is the correlator, not the stage — see above.

### A limit added to the mirror check

The dihedral group only spans the possible renderings when the affine is a
multiple of 90°, as M2's happens to be. On a 45°, anisotropic or sheared rig
every NCC would be low and "best match" would be meaningless. The script now
**refuses** outside that regime and points at the motion script, whose
determinant comparison is general.

### The 28 µm runs cannot work, and that is arithmetic

The field is 206 px ≈ 26.2 µm along stage X. A 28 µm step cannot overlap at all,
so `b9xline28_1` and `b9xline28_focused_1` carry no recoverable correspondence —
both chains break at the first step. `b9xline14` and the 14 µm lines break too,
on vote fraction. Nothing is wrong with them; they simply cannot answer the
question, and the tooling now says so rather than fitting noise.

## Proposed shape

### 1. A shared geometry module — `microclaw/dataset_mosaic.py`

Extract only the dataset-independent geometry the session hooks reinvented.
Biological segmentation/counting has a different trust and validation boundary
and does not belong in this module.

```python
# microclaw/dataset_mosaic.py
from dataclasses import dataclass

from microclaw.calibration import StageCameraAffine

@dataclass
class MosaicGeometry:
    # The pixel/image -> stage transform is not re-represented here: it IS the
    # calibration.StageCameraAffine measured for this dataset. Placement calls
    # affine.px_to_um(dx_px, dy_px) directly, so there is exactly one copy of
    # (a, b, c, d) and one implementation of the matrix product. Intended stage
    # XY is the coordinate of the optical centre of the frame. This object is
    # pure geometry: no calibration provenance, no dataset identity, no
    # precedence policy. Those live in §2's tool, keyed off the knowledge base.
    affine: StageCameraAffine       # its [[a, b], [c, d]] is the placement transform
    output_pixel_size_um: float     # controls output sampling only, not placement

    def __post_init__(self):
        """Reject numerically invalid geometry before allocating or rasterizing.

        This checks self-contained numeric invariants only — nothing that
        requires knowing where the affine came from:
          - all four affine coefficients finite (no NaN/inf),
          - finite, non-negligible determinant (nonsingular transform),
          - finite, strictly positive output_pixel_size_um.
        Provenance completeness and calibration precedence are NOT checked here;
        the geometry object cannot see which precedence branch produced it. That
        validation belongs to the resolver in §2.
        """
        ...

def assemble_stage_coordinate_mosaic(frames, geometry: MosaicGeometry):
    """Rasterize (pixels, intended_x_um, intended_y_um) frames in stage space.

    Derive every source pixel's stage coordinate as intended_xy plus
    geometry.affine.px_to_um(dx_px, dy_px) for its displacement from the frame
    centre. Compute the transformed bounds and resample onto a documented
    +X-right/+Y-down output raster at
    output_pixel_size_um. Later tiles overwrite earlier pixels in overlap: this
    is a display convention, not registration, blending, or object deduplication.

    Return the mosaic, stage-space origin/extent, output basis, coverage mask,
    and overlap statistics. Canvas size follows transformed bounds, not tile count.
    """
    ...
```

The full 2×2 transform is required. `rot90_k` plus a flip cannot represent a
non-90° rotation, unequal axis scales, or shear. The existing
`StageCameraAffine` is **pixel/image → stage**, despite older prose calling it
stage→camera, and its `px_to_um(dx_px, dy_px)` method already implements exactly
this multiply. `MosaicGeometry` therefore **holds the affine rather than a copy
of its matrix**: no `pixel_to_stage` numpy field, no re-authored matrix product,
one source of truth for `(a, b, c, d)`. Retaining the object also keeps its
`objective` and `binning` attached instead of stripping them into a bare array.
A legacy rot90/flip description may be converted into a `StageCameraAffine` (an
explicit 2×2) only as an opt-in fallback; it is not a second placement algorithm
and must be marked as a derived legacy fallback rather than a measured calibration
artifact.

**Two responsibilities, two homes.** `MosaicGeometry` owns the geometry;
calibration provenance and precedence policy do not live on it (see §5 for where
they do). This keeps the object at the altitude §1 declares — dataset-independent
geometry — and avoids the geometry dataclass having to reason about which
precedence branch produced its affine.

**Validation splits along that seam.** `StageCameraAffine` currently permits
direct construction without checking its values, so someone has to. Split it:

- *Numeric invariants* — finite coefficients, nonsingular determinant, finite
  positive `output_pixel_size_um` — are self-contained and belong in
  `MosaicGeometry.__post_init__`, which runs before any canvas is allocated.
  Callers must not be able to bypass this by constructing the dataclass directly.
- *Provenance completeness and calibration precedence* — which identity fields a
  given source (acquisition-recorded / supplied artifact / confirmed-current)
  must carry — is policy the geometry object cannot see. It belongs to the
  precedence resolver in §2, which validates before it ever builds a
  `MosaicGeometry`.

Conversion and artifact-loading helpers should also validate the numeric part at
their own boundary, but the geometry-level check is the backstop that cannot be
skipped.

The coordinate contract is load-bearing: numpy arrays are indexed `(row, col)` =
`(y_px, x_px)`, the affine consumes `(dx_px, dy_px)`, and intended XY denotes the
frame's optical centre. Tests must cover even and odd image sizes, non-square
images, reflections, arbitrary rotations, and anisotropic scale. Interpolation,
rounding, canvas bounds, uncovered pixels, and overwrite order must be specified
rather than inherited accidentally from an image library.

### 2. One generic read-side mosaic tool

Use design/30's vocabulary precisely. This produces a **stage-coordinate mosaic**:
tiles placed by recorded XY, with a documented overwrite convention. It is **not**
a *stitched mosaic*, which would register and blend overlaps.

```python
# tools.py
def build_stage_coordinate_mosaic(ctrl, guard, dataset_path, output_path,
                                  axis_selection, calibration_ref=None,
                                  output_pixel_size_um=None) -> dict:
    """Place one selected plane from an NDTiff already on disk; take no exposure.

    axis_selection fixes exactly one value for every dataset axis other than
    position. Read the selected pixels and intended XY and call
    dataset_mosaic.assemble_stage_coordinate_mosaic.

    calibration_ref is a tagged reference selecting exactly one explicit source:
    an artifact path, an immutable knowledge-base version key, a confirmed-current
    objective/binning entry, or a derived legacy transform. If omitted, use an
    calibration identity recorded with the acquisition when available and valid.
    Today this may be MM's per-image PixelSizeAffine, parsed as semicolon-delimited
    MMCore row-major [m00,m01,m02,m10,m11,m12]. Never silently apply
    the microscope's current cached calibration to a historical dataset.

    Write a 16-bit TIFF plus a JSON result manifest containing the exact resolved
    affine payload and return kind='stage_coordinate_mosaic', selection,
    calibration identity/payload, size/extent, overlap/coverage statistics,
    artifact and manifest paths, and hashes.
    """
    dataset = Dataset(guard.resolve_in_workspace(dataset_path))
    coords_iter = _iter_present_coords(dataset, fixed_axes=axis_selection)
    def frames():
        for coords in coords_iter:
            md = dataset.read_metadata(**coords)
            yield (dataset.read_image(**coords),
                   float(md["XPosition_um_Intended"]),
                   float(md["YPosition_um_Intended"]))
    ...
```

`axis_selection` is required whenever non-position axes exist. A dataset with
channel, time, Z, or another axis cannot silently composite all values into one
canvas. A later batch convenience may produce one explicitly named mosaic per
Cartesian selection, but the single-output tool fixes one *real dataset coordinate
value* per axis and rejects missing, unknown, or ambiguous selections.
Run A makes this concrete: position coordinates are strings, not integers, and
the sparse revisit has a Z axis absent from the grid datasets.

**Shared-traversal prerequisite.** design/28 Finding 3 is merged, but its fix is
an inline real-coordinate + `has_image` loop in `export_dataset_as_tiff`. Extract
that loop into `_iter_present_coords(dataset, fixed_axes)` and refactor the
exporter to call it, so both features share one traversal. The helper iterates
actual coordinate values rather than assuming zero-based contiguous integers and
guards every candidate with `has_image`.

**Data-source scope.** This reads `ndstorage.Dataset` (saved NDTiff). design/30's
Album and MMStudio-MDA paths can return live `DefaultDatastore` proxies with no
disk path; those need a separate reader. Say "saved NDTiff dataset," not "any
saved scan."

**Calibration resolver contract.** `calibration_ref` is one tagged object rather
than several partially overlapping parameters:

- `{"kind": "artifact", "path": ...}` selects a calibration artifact;
- `{"kind": "knowledge_version", "key": ...}` selects an immutable version;
- `{"kind": "confirmed_current", "objective": ..., "binning": ...}` selects
  the current alias only after explicit confirmation; and
- `{"kind": "legacy_derived", ...}` explicitly converts a rot90/flip
  description and records that it was derived rather than measured.

The resolver returns `(affine, calibration_identity)`. The identity includes the
source kind and source reference plus the canonical resolved affine payload. The
tool passes only `affine` into `MosaicGeometry`, but retains the identity for the
result manifest, response, and hashes. Thus geometry stays dataset-independent
without discarding how its transform was obtained.

### 3. Completed-dataset analysis follows design/26

There is deliberately no `count_cells_in_saved_dataset` public tool. Biological
counting is an analysis integration, even when implemented as a small classical
threshold-and-components baseline. Calling its result provisional does not exempt
it from design/26's adapter boundary or acceptance gates.

Instead, counting the saved 900 µm surveys is the first fixture for design/26's
generic completed-dataset runner:

1. Select saved dataset coordinates and, if required by the analyzer, build a
   stage-coordinate mosaic through the generic geometry primitive.
2. Invoke the reviewed counting adapter through
   `run_analysis_on_saved_dataset(..., input_kind="stage_coordinate_mosaic")`.
3. Verify that it emits a provisional observation and no hardware action.

The runner is a prerequisite, not an existing surface. Its invocation shapes,
capability restrictions, saved-adapter loading, observation statuses, provenance,
and deterministic replay rules are normative in design/26's "Completed-dataset
replay runner" section. This design supplies only the saved-NDTiff traversal,
axis-selection rules, calibration-bearing mosaic primitive, and counting fixture.

### 4. Metadata-XY dependency and fallback

Placement needs `XPosition_um_Intended` / `YPosition_um_Intended`. Their presence
is confirmed on every image in the real Run A datasets. They are absent from a
single-position acquisition, which does not need a mosaic. Guard the dependency
explicitly and fail loud rather than placing every tile at the origin:

```python
if "XPosition_um_Intended" not in md or "YPosition_um_Intended" not in md:
    return {"error": "Selected dataset images have no intended-XY metadata; "
                     "cannot build a stage-coordinate mosaic. Nothing was re-imaged."}
```

Reconstructing XY from `row`/`column` axes plus `step_um` is possible only as a
separate, explicitly requested mode. It reintroduces the row/column↔stage-axis
ambiguity that cost four re-scans and cannot represent Pallavi's spiral at all.

Run A records no achieved/actual stage XY, only the intended values. Placement
therefore consumes intended XY, but intended need not equal achieved after a
direction reversal on a stage with backlash or settling error. A coordinate
mosaic must state that limitation; it cannot correct an unrecorded residual.

### 5. Calibration identity is versioned and resolved into every result

The measured pixel→stage affine is a property of the optical path — objective,
binning, and (in principle) ROI/camera geometry — not of the session. The
provenance store builds on the one that already exists: `calibration.save_affine`
/ `load_affine` persist `StageCameraAffine` entries in the knowledge base under
`devices`. The current `affine_key(objective, binning)` is mutable and therefore
is only a **current alias**, not a durable identity.

Saving a calibration must also create an immutable version whose key includes a
canonical-payload hash (or an equivalently unique version) and update the
objective/binning alias to point to it. Existing unversioned entries are treated
as mutable legacy aliases and resolved into a new immutable version before use.
Loading an immutable version must verify that its stored canonical payload still
matches its key.

The canonical affine payload contains `a`, `b`, `c`, `d`, `objective`, `binning`,
and `pixel_size_um` with a specified serialization and hash algorithm. The
immutable version key plus that payload is the calibration identity. Versioning
prevents a later recalibration from changing what an old identity resolves to.

The precedence resolver in §2 owns the policy, in this order:

1. an explicitly supplied calibration artifact or immutable knowledge-version key;
2. an explicitly confirmed current `objective`/`binning` entry from the
   knowledge base;
3. a calibration identity recorded with the acquisition.

**Corrected 2026-07-28 (Block 8 landed).** This list previously put the
acquisition record first, contradicting §2's "*If omitted*, use an identity
recorded with the acquisition when available" — which makes the explicit
reference the primary and the acquisition record the fallback. §2 is right and
this list was wrong. An operator supplies an explicit reference *precisely when
they know the recorded value is wrong*, so letting the recorded value silently
win would reproduce the exact failure class this design exists to prevent. The
shipped resolver follows the corrected order and records `source_kind` either
way; when an explicit reference is supplied and a usable acquisition record also
exists, the record is audited into the identity rather than discarded silently.

MM's current acquisition record is per-image `PixelSizeAffine`, a semicolon-
delimited MMCore row-major `[m00,m01,m02,m10,m11,m12]`. Parse exactly six finite
floats into `[[m00,m01],[m10,m11]]`, ignore translation for relative placement,
and require a nonsingular determinant. Fall through on `Undefined`, all zeros,
or identity.

**The per-image metadata contract, measured (Block 8).** Every field below was
read from real Run A metadata, not inferred. Block 8's first implementation was
green against invented key names and shapes and wrong against all of these, so
the exact forms are normative:

| Need | Real key | Real form |
|---|---|---|
| affine | `PixelSizeAffine` | `'0.0;0.0;0.0;0.0;0.0;0.0'` — semicolons |
| ROI | `ROI` | `'36-50-453-227'` — **dash**-delimited `x-y-w-h` string |
| binning | `Binning` | `'1'`, and `'1x1'` on M5 — both must parse |
| camera | `Core-Camera` | `'Andor'` |
| camera model | *(vendor-specific)* | **no single key.** Andor: `Andor-Camera` = `'\| iXon Ultra \| DU897_BV \| 8172 \|'`. Hamamatsu: **no `-Camera` key at all** — `HamamatsuHam_DCAM-CameraName` = `'C15440-20UP'`, serial under `-CameraID` = `'S/N: 500975'` |
| objective | **absent** | no `Objective`/`ObjectiveLabel`/`PixelSizeConfig` key exists |

The camera-model row is corrected from Block 8's version, which recorded "only
as `<Device>-Camera`". That was true of the only datasets we had then — all
Andor — and generalising it hardcoded one vendor's shape. Every M5 dataset was
then refused as "identity metadata is incomplete", including the 2500-tile
fixture the scale gate needs. Resolve the model by trying `-Camera`,
`-CameraName`, `-CameraID` in order and **record which key answered**; a reader
cannot infer it from the value. This is the second time an assumed key shape
passed a green suite and failed on real data, which is why the three measured
forms above are pinned by name in the tests.

Two consequences are load-bearing. The ROI is a **dash**-delimited string, so a
parser splitting on `[,;]` yields `None` for every real dataset — which also
silently disables the mid-dataset ROI-change check, since an unparsed ROI reads
as "no ROI seen" rather than "unknown". Unknown must never collapse into
unchanged. And there is **no per-image objective**, so an identity cannot be
completed from MM metadata alone today: the resolver must mark it unknown and
fall through, never stringify a missing value into the payload it then hashes. This MM-sourced branch is retained because it is correct and cheap,
but on the available systems it normally falls through to microclaw's measured
`solve_affine` knowledge-base calibration. It is not a supported
acquisition-recorded identity. Identity is especially dangerous: finite and
nonsingular, the active demo config demonstrates that it can silently make a
plausible unrotated 1 µm/px mosaic from a default.

Never infer "uncalibrated" solely from `get_pixel_size_um() == 0` or an empty
current config. Enumerate all configs and surface calibrated-but-nonmatching
configs and rule mismatches. Selection rules are arbitrary device/property
predicates and need not describe the optical path, so a config name cannot be a
calibration identity both because it is mutable and because activation may be
unrelated to optics. Never silently apply current calibration to a historical
dataset. Resolving the current alias pins its immutable version before
rasterization; the result never records only the mutable alias.

Camera identity is required, and the reason is **cross-instrument**, not a
detector swap on one rig. Run A is M2 (Andor iXon, 453×227); M5 is a different
microscope (Hamamatsu, 2304×2304). An objective/binning key carries nothing that
distinguishes the two, so M2 data could resolve against an M5 entry — or against
a demo entry — and produce a mosaic with no error anywhere. Note the aggravating
detail: both instruments have a `Res`-prefixed pixel-size config and a MicroFPGA,
so neither the config name nor a casual device-list glance separates them. This
also means the same failure exists *within* one instrument whenever its detector
or ROI changes; the cross-instrument case is simply the one we can demonstrate.
Identity must carry camera device/model,
objective, binning, ROI geometry, exact affine payload, and hash. A constant
off-centre ROI adds only a global translation and is harmless for relative
placement; a mid-dataset ROI change changes frame geometry and must be rejected
or handled per frame. Timestamp remains provenance rather than a lookup key.
`calibration_provenance` is intentionally **not** a
field on `MosaicGeometry`; if a richer typed calibration artifact later replaces
the raw `StageCameraAffine`, it enters through the §2 resolver, not the geometry
object.

Every successful mosaic writes a JSON result manifest next to the TIFF. It embeds
the complete canonical affine payload actually used, its immutable version key
when one exists, `source_kind` (`acquisition_recorded`, `artifact`,
`knowledge_version`, `confirmed_current`, or `legacy_derived`), the original
source reference, and hashes of both the payload and output. The returned result
contains the same record and the manifest path. Reproducing a mosaic therefore
does not depend on the knowledge base still existing or on any mutable alias: the
manifest's resolved payload is sufficient, while the version key preserves the
calibration history and audit trail.

`pixel_size_um` alone is insufficient whenever the camera axes are rotated,
reflected, anisotropic, or sheared relative to stage axes. It may control output
sampling, but not source placement.

## Seam correctness is an analysis question

Stage-coordinate compositing does not guarantee that a cell crossing two tiles is
counted once. Stage error, lack of image registration, overlap overwrite,
illumination mismatch, interpolation, or a gap can split, merge, erase, or duplicate
objects. "Later tile overwrites earlier" is acceptable as a declared display
convention; it is not a biological correctness property.

A counting adapter must validate overlap/gap cases independently. If mosaic-level
segmentation is inadequate, detect attributed objects per tile and reconcile them
in stage coordinates across overlaps. The acceptance tests and slide-level gates
remain those of design/26.

## Memory / scale note (the 2500-tile case)

The 900 µm mosaic is 8582×8578 px ≈ 74 M px → ~140 MB as uint16. Lazy frame
loading avoids retaining all 2500 source tiles, but "one tile plus one canvas" is
not a valid peak-memory promise: affine resampling, coverage/overlap masks, TIFF
encoding, connected-component labels, and analyzer temporaries can each allocate
canvas-sized arrays.

Measure peak RSS on the 2500-tile fixture and set a budget. If it is not
comfortably within that budget, rasterize to a chunked or memory-mapped artifact
and require completed-dataset analyzers to support bounded-memory access.

**Measured (2026-07-29), and chunking is not needed.** `scan488_900_1`, 2500
positions, one time point:

| | |
|---|---|
| output | 8576 × 8580 px (predicted 8582 × 8578) |
| wall time | 3.4 s |
| peak RSS | 723 MB |
| coverage | 1.0000, zero uncovered pixels |
| overlap depth | max 4 tiles (corners); 5.43 M cells covered more than once |

Two estimates above were wrong in the same direction. The source tiles are
**180 × 176 px on a `1336-1084-180-176` ROI**, not the full 2304², so the
rasterizing work is ~79 M output-cell operations rather than the 13 G the
full-frame assumption implied. And the 18 µm step against a 180 px tile at
0.105 µm/px (18.9 µm) gives a real but thin overlap, which is why coverage is
complete and the depth reaches 4 at tile corners.

723 MB for a 140 MB canvas is roughly 5×, accounted for by the mosaic, the
`uint16` coverage counter, the boolean mask, and the TIFF encode buffer. That is
comfortably within budget, so **chunked/memory-mapped output is not implemented**
and the design's conditional does not fire. Re-measure before assuming this
holds for a full-frame 2304² tiling, which would be ~164× the per-tile work.

## Testing

- Geometry: synthesize tiles at known XY and verify output extent and pixel
  locations for identity, 90° and arbitrary rotations, both reflections,
  anisotropic scale, even/odd dimensions, and non-square frames.
- Seam behavior: plant objects across overlaps and gaps and record overwrite,
  split, merge, and interpolation behavior. Do not assert deduplication as a
  property of placement.
- Exporter regression: after extracting `_iter_present_coords`, assert the
  exporter and mosaic tool both traverse the multi-position dataset that once
  raised `KeyError: 'position'`.
- Spiral regression: **must be synthetic.** The real spiral fixture is 25
  single-position datasets carrying no intended XY (see the 2026-07-29 section),
  so it can verify only the §4 refusal path. Synthesize non-grid intended-XY positions and assert placement
  by coordinate rather than acquisition order or row/column axes.
- Axes: reject omitted or invalid channel/time/Z selections and prove that frames
  outside the selected plane cannot overwrite it.
- Geometry validation: `MosaicGeometry.__post_init__` rejects NaN/inf
  coefficients, a singular affine, and non-positive `output_pixel_size_um`,
  and cannot be bypassed by direct construction; provenance/precedence are not
  checked here.
- Calibration precedence (resolver, §2): accept a matching supplied artifact or
  immutable knowledge-version key, or a confirmed current knowledge-base entry
  keyed by objective/binning; reject when no identity is available, and never
  silently consume current microscope state for an old dataset without
  confirmation. Assert that legacy-derived sources remain marked as such.
- Calibration versioning/result provenance: recalibrating the same
  objective/binning creates a new immutable version without changing the old
  payload; mutable aliases resolve to a pinned version. Assert that each mosaic's
  JSON manifest embeds the exact canonical affine payload, source kind/reference,
  version key when available, and matching hashes, and that replay succeeds from
  the manifest payload after the knowledge-base entry is unavailable.
- Replay/provenance: run a provisional counting adapter through the generic
  completed-dataset runner, assert full source/selection/calibration hashes, and
  assert deterministic normalized output with no acquisition-driving action.
- Scale: measure peak RSS and output correctness on the 2500-tile fixture.

## Relationship to the other findings

- **Builds on design/28 Finding 3** (merged) — extract its inline traversal into
  `_iter_present_coords` and reuse it in the exporter and mosaic tool.
- **Uses design/28 Finding 4** (merged) — placement consumes the FOV-scaled
  calibration's measured pixel/image→stage affine tied to the acquisition or an
  explicitly supplied artifact, not hard-coded orientation or silent current state.
- **Discharges design/30 Finding 2** — this design owns the coordinate-aware
  offline mosaic and preserves design/30's distinction between Album, contact
  sheet, stage-coordinate mosaic, stitched mosaic, and multi-page TIFF.
- **Bounded by design/26** — geometry is a generic dataset primitive; counting is
  a reviewed observation-only adapter invoked through generic completed-dataset
  orchestration, not an analysis-specific public tool. Validation and later
  acquisition-driving use remain entirely in design/26.
- **Reinforces design/32** — Finding 2 keeps replay off the dose ledger. Finding 4
  requires generated analysis behind a capability-limited, eventually isolated
  boundary; moving an unvalidated counter into an in-repo module would not by
  itself make it trusted.
- **Retires the live-only constraint** that made design/28 Findings 1–2 costly to
  diagnose: placement, orientation, and analysis hypotheses can be replayed
  without re-dosing the sample.
- **Does not replace live hooks** — adaptive/gated acquisition
  ([[design-27-ghost-exposures]]) still runs during the scan. This design covers
  only pixels already captured and saved.
