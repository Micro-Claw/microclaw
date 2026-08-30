# Make `center_feature` move toward one feature

## Problem

`center_feature` systematically moves in the wrong direction when it uses an
affine produced by `calibrate_stage_to_camera`. A feature can therefore move
farther from the centre on every iteration and eventually leave the field of
view.

There is a second contract mismatch. The tool schema says it centres the
brightest feature, but `detect_features` supplies the intensity-weighted centre
of mass of **all** positive signal in the image. In a field containing several
objects, debris, a gradient, or extended structure, that point need not belong
to any detected feature. Even after the motion sign is fixed, this can put the
feature the operator intended to centre toward the side of the image.

**Status: implemented 2026-08-30.** The convention question this doc left open
is settled — see "Which convention" below — and Micro-Manager is now the
calibration authority.

## Root cause 1: the calibration correction is negated twice

`calibrate_stage_to_camera` calls
`phase_cross_correlation(reference, image_after_move)`. The returned vector is
the registration shift that would move the second image back onto the
reference; it is the negative of the scene's observed displacement caused by
the stage move.

`solve_affine` inverts those registration vectors. Consequently, for a current
feature offset `r = [dx_px, dy_px]`, `affine.px_to_um(*r)` already gives the
stage correction that moves the feature toward the image centre. The live tool
currently applies its negative:

```python
dx_um, dy_um = affine.px_to_um(*residual)
move_stage_xy(ctrl, guard, -dx_um, -dy_um, absolute=False)
```

The standalone-session emitter repeats the same extra negation with
`core.set_relative_xy_position(-_center_dx_um, -_center_dy_um)`.

The current tests conceal the error. The calibration simulation makes a
positive stage move shift its synthetic image in the positive image direction,
whereas the centring simulation makes the same move shift its spot in the
negative image direction. Each test is internally consistent, but the two
models contradict one another.

## Root cause 2: the detector does not identify the centring target

`detect_features` runs `blob_log`, but uses its output only for `n_spots`. Its
reported centroid and offset come from `scipy.ndimage.center_of_mass(sig)` over
the entire above-median signal image. `center_feature` then treats that global
centroid as the location of the "brightest feature."

This also means a frame may have `n_spots == 0` yet still produce a non-null
offset and trigger stage motion.

## Decision

### Which convention, and why it is not a house style

Verified two ways rather than assumed, because everything else rests on it.

**Numerically.** `phase_cross_correlation(reference, moved)` returns the vector
that registers `moved` back onto `reference` — the *negative* of the scene's
observed displacement. `solve_affine` inverts the stacked pair, so `px_to_um(r)`
is already the stage move that centres a feature at offset `r`.

**Against Micro-Manager.** MM's `PixelSizeAffine` is the SAME map, established
from MM's own consumer path, not from its docs (which do not state it) and not
from design/29's motion spike (which deliberately absorbs a global sign:
`sign = -1 if |M + reported| < |M - reported|`, printed as "the flip is the
motion convention, not an error" — so its "+0.30 deg" agreement says nothing
about sign). `CenterAndDragListener` double-click-to-centre passes the NEGATED
offset (`0.5 * width - center.x`) to `XYNavigator.moveSampleOnDisplayPixels`,
and `XYNavigator.toStageSpace` applies the affine and then negates both axes:

```java
affineTransform_.transform(source, dest);
// not sure why, but for the stage movement to be correct, we need
// to invert both axes"
dest.setLocation(-dest.getX(), -dest.getY());
```

The two negations cancel: MM's move to centre a feature at offset `r` is `+A·r`.
That is exactly `px_to_um(r)`, so **an MM affine is adopted with no sign
change.** MM's own "not sure why" comment is this same double-negation
confusion in the reference implementation.

`dataset_mosaic` already reads `px_to_um` this way (it places the pixel at
offset `r` at stage `(x, y) + px_to_um(r)` — the stage position at which that
pixel would be centred), so the fix leaves the mosaic correct for both sources.

### Use one sign convention end to end

Keep the persisted affine convention produced by the existing calibration:

> pixel registration/correction vector to stage displacement

Change both the live tool and its emitter to apply the affine result directly,
without an additional minus sign:

```python
dx_um, dy_um = affine.px_to_um(*residual)
move_stage_xy(ctrl, guard, dx_um, dy_um, absolute=False)
```

Update `StageCameraAffine`, `solve_affine`, and tool comments to say
"registration/correction vector" rather than the ambiguous "image-pixel
displacement." Do not silently reinterpret or negate saved coefficients: the
coefficients already produced by `calibrate_stage_to_camera` have the convention
above.

### Centre the brightest detected punctum

Preserve the existing aggregate centroid fields in `find_features` for field
statistics, but add an explicit centring target derived from `blob_log`:

- Score every detected blob by background-subtracted signal in its local blob
  footprint (with a deterministic tie break by `(y, x)`).
- Report the winning blob's centre as `brightest_feature_xy_px` and its signed
  offset as `brightest_feature_offset_px`.
- Report both fields as `None` when `n_spots == 0`.
- Make `center_feature` use `brightest_feature_offset_px`, not the aggregate
  `offset_from_center_px`.
- If there is no detected blob, refuse before moving with "No detected feature
  to centre." Positive background structure alone is not a motion target.

This makes the implementation match the public "brightest feature" contract.
Selection of a particular non-brightest object or tracking an object between
frames is separate future scope.

### Micro-Manager is the calibration authority

`center_feature` reads one place — the knowledge base — and MM's affine is put
there before it runs. Resolution order in `_resolve_current_affine`:

1. **MM publishes a usable `PixelSizeAffine`** → adopt it into the KB (tagged
   `source: micro_manager_pixel_size_affine`) and use it. It is the calibration
   the operator already maintains, and adopting it *into* the KB keeps one
   storage path, one lookup, and an exported script whose coefficients match the
   session that ran. Re-adopted whenever MM's value changes.
2. **MM has none, or a sentinel** → use a cached `calibrate_stage_to_camera`
   measurement.
3. **Neither** → refuse and say to run `calibrate_stage_to_camera`.

`calibrate_stage_to_camera` is the deliberate override for a rig whose MM affine
is absent or measured wrong: once it has run, its measurement stands even if MM
later publishes a different one. That disagreement is *reported*
(`micro_manager_affine_differs`), never silently resolved — discarding an
operator's own measurement is not ours to do quietly.

**One caveat is reported, not enforced.** design/29 measured M2's MM affine at
3.5% and 15.7% low, anisotropic by 14% where the config claimed isotropy,
because MM's *Manual-Simple* calibrator never measures a scale: it applies the
pixel size you already had to both axes and snaps orientation to one of eight
cases. Its output is therefore always `scalar × signed permutation`, which is
detectable, so an adopted affine with that fingerprint carries a
`calibration_note`. It is not a refusal: a closed loop converges through a scale
error (it just takes an extra iteration), and orientation — the part that
actually decides convergence versus divergence — was rig-confirmed to a third of
a degree. A mosaic is the case that cannot absorb it.

Consequently design/29's "MM is not a calibration source" stands **for placement
and stage-coordinate measurement** and does not stand for centring.

## Blocks, gates and run ledger

design/64 is its own block, not a `design/35` row — the same shape design/58
ran. `CLAUDE.md` §"The block workflow" owns the process; this table is only the
record.

| Block | Scope | Branch | Start commit | Implementation | Rig evidence | Merge | Design reconciliation |
|---|---|---|---|---|---|---|---|
| 64a | Sign, centring target, MM as authority, and their export | `design/64-center-feature` | `1f07761` | *(this branch)* | **owed — gate below not yet run** | — | — |

**Deviation from the workflow, recorded rather than hidden.** 64a was
implemented by the coordinator inline instead of being delegated to a runner in
its own worktree (step 2), and it began in the `main` working tree before being
moved to this branch. Nothing reached `main`. A runner review of the diff was
commissioned after the fact; that is not the same as step 2 and this row says so.

### The gate

`design/64-gate-probe.py`, run on a rig with a sample in the field. Seven limbs,
each scored independently, exit nonzero unless every one PASSes. It is a
program, not a runbook, because every limb only computes — block 58a reported
`PASSED` over five failed limbs when pasted `throw`s ended a pipeline rather
than the session.

```
uv run python design/64-gate-probe.py --out gate64
```

| Limb | Mechanism under test |
|---|---|
| A | `get_pixel_size_affine()` read through `_strings`, never `list()` (design/59a) |
| B | `_resolve_current_affine` adopts MM's affine, or reports our override |
| C | what it resolved actually reached the knowledge base |
| D | `find_features` names a target: `brightest_feature_offset_px` |
| E | **the control that must fire** — a gradient refuses AND the stage does not move |
| F | the residual shrinks on a real sample; a ratio near 2.0 means the sign, near 1.0 means arrival |
| G | the exported script carries the same unnegated affine and target |

**What the gate is NOT for.** The sign convention is settled off-rig — twice,
numerically and against MM's own source — so no rig limb is asked to establish
it. What needs a rig is whether this installation publishes an affine we can
read, and whether the loop converges through real optics on a real sample.
Limb F is scored from `convergence.json`, not from its verdict.

**Not yet dry-run against a bridge-shaped fake.** `CLAUDE.md` requires that
before an operator sees it (`design/55-gate-probe-selftest.py` is the
instrument, and it must return `size()`/`get(i)` vectors whose `__iter__`
raises). Do that before step 4.

## Required tests

1. Use one shared synthetic optical model for calibration and centring. Run
   `calibrate_stage_to_camera`, load the affine it saved, then run
   `center_feature` against the same model. Do not inject a hand-written affine
   into the centring half.
2. Parameterize that end-to-end test over both axis flips and a 90-degree camera
   rotation. In every case, the residual magnitude after a correction must be
   smaller than before it and the feature must converge within `tol_px`.
3. Add a regression assertion on the first commanded move. For a known feature
   offset, it must equal `affine.px_to_um(offset)` and not its negative.
4. Execute an exported `center_feature` script against the same fake optical
   model and assert that it converges with the same move sign as the live tool.
5. Build a field with two puncta of unequal signal. Assert that the brighter
   punctum, rather than their aggregate centre of mass, reaches the centre.
6. Build a field with a gradient or extended non-blob signal and no detected
   puncta. Assert that no stage move occurs.
7. Build an equal-score two-blob field and assert deterministic target
   selection.
8. Keep the existing guard and maximum-iteration assertions: every correction
   remains bounds-checked, and failure to reach tolerance remains finite and
   honestly reported.

## Also fixed, because they were the same defect

- `find_features` reported `px_to_um(offset)` as `offset_from_center_um`. That
  value is a stage MOVE, not a distance, and the name is the ambiguity that
  caused this bug. Renamed **`centering_move_um`**, and it now describes the
  brightest punctum — the one the agent would act on — not the aggregate
  centroid. `calibrate_stage_to_camera`'s status string advertised the old name
  and now states the convention.
- `compare_revisit_frames` labelled `px_to_um(registration_shift)` as
  `translation_stage_dx_um`, which reads as the drift that occurred; it is the
  correction, of opposite sign. Renamed `realign_move_*`.
- `_load_current_affine` was collapsed into `_resolve_current_affine`. Keeping
  both would have left every test that patched the old name silently inert for
  the tools that had moved on.

## Deliberately NOT in scope

- **`move_stage_xy` still has no arrival contract** (design/35 register,
  design/63 block 63a). The loop can snap before the stage stops, so a residual
  can be measured mid-move. That is a real defect and it is not this one; fixing
  the sign does not depend on it and does not hide it.
- Choosing a non-brightest target, or tracking one object across frames.

## Acceptance criteria

- For a valid current calibration, every successful correction reduces the
  selected feature's pixel residual in the shared optical model.
- The live tool and exported program issue the same signed stage displacement.
- A multi-feature image centres the reported brightest detected feature.
- A frame without a detected feature causes no stage motion.
- Existing saved calibration coefficients continue to work without migration.
  (Confirmed: the coefficients are unchanged; only their documented meaning and
  their application changed. Entries written before this doc carry no `source`
  and are read as ours, so MM cannot silently replace one.)

## Evidence

- `tests/synthetic_optics.py` — ONE optical model, whose only statement is how
  the scene moves when the stage does. Both halves of the loop run against it.
- `TestCentringAgainstItsOwnCalibration` (`tests/test_tools.py`) — calibrates
  with the real tool, then centres with what it saved, over both axis flips and
  a 90° rotation. Nothing hand-writes an affine.
- `TestMicroManagerIsTheCalibrationAuthority` — adoption, re-adoption,
  idempotence, the local override, the Manual-Simple note, and centring driven
  by an adopted MM affine with no local calibration at all. Its fake returns a
  `size()`/`get(i)` vector whose `__iter__` raises, because a `list()` over a
  Core collection works against every naive fake and fails on every rig
  (design/59a).
- `test_emitted_center_feature_moves_the_same_way_as_the_live_tool` — execs the
  emitted script against the same model and compares commanded moves.

**Watched to fail.** Restoring the extra negation turns the residual from 47.20
px to 94.40 px in one iteration — exactly doubled, the signature of applying
`−correction` — and fails 13 of 16. Steering by the aggregate centroid again
fails 14 of 16, including the two-punctum and gradient limbs specifically.

