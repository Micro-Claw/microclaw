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
| 64a | Sign, centring target, MM as authority, and their export | `design/64-center-feature` | `1f07761` | *(this branch)* | **M2 2026-08-30, 9/9 PASS — product confirmed; two gate defects found, F2 re-run owed** | — | — |

**Deviation from the workflow, recorded rather than hidden.** 64a was
implemented by the coordinator inline instead of being delegated to a runner in
its own worktree (step 2), and it began in the `main` working tree before being
moved to this branch. Nothing reached `main`. A runner review of the diff was
commissioned after the fact; that is not the same as step 2 and this row says so.

### The gate

`design/64-gate-probe.py`, run on a rig with a sample in the field. Eight limbs,
each scored independently, exit nonzero unless every one PASSes. It is a
program, not a runbook, because every limb only computes — block 58a reported
`PASSED` over five failed limbs when pasted `throw`s ended a pipeline rather
than the session.

```
uv run python design/64-gate-probe.py --out gate64
```

**This gate needs M2 or M5. The demo machine cannot run it** — its camera
returns the same image every snap regardless of stage position (operator,
2026-08-30), and in other modes the frame follows the snap count rather than the
stage (design/20 §S3). Limb 0 measures that and stands the centring limbs down
rather than letting them diagnose hardware from a camera that is not watching
it. Limbs 0, A–E and G still run there and are worth having, but F/F2 — the
reason the trip exists — report NOT EXERCISED, which is never a pass.

| Limb | Mechanism under test |
|---|---|
| 0 | **precondition** — frames actually respond to the stage, measured, not assumed |
| A | `get_pixel_size_affine()` read through `_strings`, never `list()` (design/59a) |
| B | `_resolve_current_affine` adopts MM's affine, or reports our override |
| C | what it resolved actually reached the knowledge base |
| D | `find_features` names a target: `brightest_feature_offset_px` |
| E | **the control that must fire** — a gradient refuses AND the stage does not move |
| F | **one** correction cuts the residual to under 0.6× — the sign test |
| F2 | the loop reaches tolerance, re-measured independently after it returns |
| G | the exported script carries the same unnegated affine and target |

**What the gate is NOT for.** The sign convention is settled off-rig — twice,
numerically and against MM's own source — so no rig limb is asked to establish
it. What needs a rig is whether this installation publishes an affine we can
read, and whether the loop converges through real optics on a real sample.
Limb F is scored from `convergence.json`, not from its verdict.

**Dry-run before shipping, and it paid for itself three times.**
`design/64-gate-probe-selftest.py` drives the whole probe against a
bridge-shaped fake — Core collections that refuse `__iter__` and answer
`size()`/`get(i)`, and the same `tests/synthetic_optics.py` model, whose only
statement is how the scene moves when the stage does.

It found five defects in the gate, none of which any review had caught:

1. `import microclaw.safety.load_constraints` — **a function that does not
   exist**. The probe would have died on its first line on the rig.
2. `microclaw.session_record` — **a module I invented**; the real
   `export_session_script(ctrl, guard, path, records)` takes a conversation
   transcript. Limb G could never have run.
3. **Limb F could not see the bug the gate exists for.** It asked only that the
   residual be smaller after the whole loop; run with the defect restored, the
   loop flailed for four iterations and finished 4% closer *by luck* — 47.2 →
   45.3 px — and the limb reported PASS. A criterion a broken mechanism can
   satisfy is not a criterion. It now scores a **single** correction, where the
   arithmetic is unambiguous, and reports the ratio whatever the verdict.

Discrimination is now demonstrated rather than assumed:

```
python design/64-gate-probe-selftest.py               -> 8/8 PASS, exit 0
python design/64-gate-probe-selftest.py --flip-sign   -> F_one_correction FAIL
        residual 47.2 -> 94.4 px, ratio 2.00
```

4. **Limb F would have reported FAIL on the demo machine**, computing a ratio
   near 1.0 and concluding "the stage did not arrive" — a confident, wrong
   diagnosis about hardware that is working fine. A gate that cries wolf on the
   demo machine gets its real FAILs dismissed. Limb 0 now measures coupling
   first: two frames with no motion, then two spanning a known move.
5. And the fix for (4) **landed twice in limb F and not at all in F2**, so F2
   still reported FAIL on a frozen camera. Only running the thing found that.

The selftest itself fails loudly if `--flip-sign` does not turn limb F red, and
`--demo-camera` fails if any centring limb reports FAIL rather than standing
down — so the gate cannot quietly stop discriminating, nor quietly start
blaming hardware, later.

```
python design/64-gate-probe-selftest.py --demo-camera
        -> limb 0 NOT EXERCISED, F/F2/G NOT EXERCISED, no FAIL
```

## Required tests — and where they landed

All eight are implemented. Named here so a reader can check the claim rather
than take it.

| # | Requirement | Where |
|---|---|---|
| 1 | One shared optical model; no hand-written affine in the centring half | `tests/synthetic_optics.py`; `TestCentringAgainstItsOwnCalibration.calibrate` runs the real tool and lets `center_feature` load what it saved |
| 2 | Parameterized over both axis flips and a 90° rotation; residual must shrink | `test_calibration_then_centring_converges`, 6 cases; `test_one_correction_reduces_the_residual` asserts the shrink over a SINGLE iteration so an overshoot-and-recover cannot pass |
| 3 | First commanded move equals `px_to_um(offset)`, not its negative | `test_first_commanded_move_is_the_unnegated_affine`, 6 cases, and it asserts the negative explicitly |
| 4 | Exported script runs against the same model, same move sign | `test_emitted_center_feature_moves_the_same_way_as_the_live_tool` — execs the emitted source and compares commanded moves element-wise |
| 5 | Two puncta of unequal signal; the brighter one centres | `test_centres_the_brighter_punctum_not_the_aggregate_centroid`, which also asserts the dim one did NOT move to centre |
| 6 | Gradient with no puncta; no stage move | `test_structure_without_puncta_moves_nothing`, plus `test_emitted_center_feature_refuses_a_field_with_no_punctum` for the script |
| 7 | Equal-score two-blob field selects deterministically | `test_equal_puncta_select_deterministically` — noiseless on purpose, since with texture underneath two equal puncta do not actually tie |
| 8 | Guard and max-iteration assertions kept | **There were none to keep.** `test_every_correction_is_bounds_checked` and `test_failure_to_converge_is_finite_and_honest` are new; the second needs a stage that quantizes (design/29 measured ~0.8 µm on M2), because a noiseless linear model converges to exactly zero and cannot exercise the give-up path |

Plus `TestMicroManagerIsTheCalibrationAuthority`: adoption, re-adoption on an MM
recalibration, idempotence, the local override and its disagreement report, the
sentinel, the Manual-Simple note, and centring driven by an adopted MM affine
with no local calibration at all.

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

## Review findings, and what they changed

Reviewed by a Codex runner against `6f62a90`. It confirmed the live sign, the MM
adoption sign, the 6- and 4-value decodings, the mosaic's consistency, emitter
parity and the blob arithmetic, and found five defects. Four were real:

- **A cached affine survived a camera swap.** `affine_key` is only
  (objective, binning), so a different camera on the same objective got the
  first camera's pixel size and rotation. `_cached_affine_is_stale` now rejects
  it. **ROI deliberately does not invalidate** — cropping moves the centre, and
  `offset_from_center_px` is measured against the real frame, so the map still
  holds; the reviewer suggested including ROI and that would refuse on every
  ordinary crop.
- **`center_feature` pinned one affine while re-analysing each frame.** Another
  MM client can turn the turret mid-loop. It now re-resolves per iteration and
  **stops** rather than correcting a rotated field along the old axes.
- **An unwritable knowledge base cost a valid MM affine.** MM is the live
  authority; caching it is a convenience. A failed `save_affine` now reports
  `calibration_not_cached` and proceeds.
- **Making a read able to write made concurrent launches more likely to
  collide.** `knowledge_manager` now replaces the file atomically, closing the
  torn-read window. **The lost-update race is NOT closed** — two processes that
  load, edit and save the whole document can still drop each other's unrelated
  edits. That predates design/64, which only made it more frequent, and it is
  recorded here rather than claimed fixed.

The fifth was half right, and the half that was wrong matters. The reviewer
predicted that flipping the convention in *both* `solve_affine` and
`center_feature` would leave every centring test green. It does not:
`test_first_commanded_move_is_the_unnegated_affine` fails in all six cases,
because it compares the commanded move against the *unnegated* stored affine.
But the underlying point stands — the convergence tests alone cannot see it, and
nothing tied our stored convention to MM's, which matters precisely because both
now land in the same knowledge-base slot.
`test_our_calibration_agrees_with_mm_convention` is that oracle: it asserts what
`calibrate_stage_to_camera` measures equals `-m_phys⁻¹`, derived from MM's
semantics alone. It fails under the global flip; the convergence tests do not.

The reviewer's pytest exited 139 before collection in its sandbox. The same
tests pass in that same worktree outside it, so its mutation experiment produced
no runtime result and its finding 5 was reasoned algebraically — which is how
the prediction came to be wrong.

**Scope objection, partly accepted.** The reviewer called the adoption-on-read
state machine more than a sign fix needs. It is, but it is what "MM's affine
must be in the knowledge base before `center_feature` is called" requires. The
294-line design doc and 299-line gate are fair criticism against `CLAUDE.md`'s
"short and to the point"; the gate is a program the operator runs rather than
prose they read, and this document has been left long because the convention
derivation is the artifact that stops this being re-litigated.

## M2, 2026-08-30 — scored from the artifacts

Dense bead field, slightly defocused so the beads read as blobs. All nine limbs
reported PASS. Scored from `gate64-m2/*.json` rather than from that verdict,
which is where both of the following came from.

**The claim this block exists for is confirmed on hardware.** M2 publishes
`0;0.127;0;-0.127;0;0`, it was adopted unchanged, and **one correction took the
punctum from 72.3 px to 2.5 px — ratio 0.03**. The independent check is that
the adopted affine predicts the observed motion: the punctum moved
`(-23.0, +71.0)` px for a stage move of `(-9.0, -2.7)` µm, against a prediction
of `(-21.3, +70.9)` px — **2.3% on a 74 px displacement**. The sign is right on
a real microscope, and MM's affine needs no negation.

Root cause 2 is confirmed too, on the same frame: the aggregate centroid sat at
`(12.8, -10.4)` px while the brightest punctum was at `(21.5, -69.0)` — **70 px
apart**. The old code would have driven to a point on neither bead.

The Manual-Simple fingerprint fired, on the very rig whose scale design/29
measured. Per-axis scales derived from this single correction are 0.117 and
0.127 µm/px against MM's declared 0.127 — the same direction as design/29's
−3.5%/−15.7%, but **n=1 through a stage that quantizes**, so this corroborates
the sign of the error and measures nothing about its size.

### Two gate defects, neither visible in the verdict

- **F2 passed without running.** F centres the field, so F2 entered at 3.9 px
  against `tol_px=5.0`, returned in **0 iterations** and reported PASS. A limb
  that cannot fail is not a criterion — block 58a's opt-out limb passed three
  rounds the same way. F2 now displaces the punctum by a known 45 px first,
  re-measures rather than assuming, and **FAILs on `iterations == 0`**.
- **Limb 0 passed for the wrong reason.** It scored a phase correlation, which
  on this bead field locked onto the wrong bead: `(-23.3, -0.7)` px for a +10 µm
  X move where the affine — validated minutes later by limb F — predicts
  `(0, -78.7)`. Wrong axis, 3.4× wrong magnitude. It cleared the threshold by
  luck; another field could as easily have returned ~0 and reported a healthy M2
  as decoupled, which reads as a fact about the machine. design/29 had already
  measured this exact failure on this exact sample. The criterion is now a
  whole-frame difference with no peak finding; the shift is still reported and
  explicitly **unscored**.

**Owed: a partial re-run of F2 on M2 or M5** — `--limbs F2` runs it plus limb 0
and nothing else, so the re-run costs two minutes rather than a full gate.
Nothing else needs re-running: the other eight limbs' evidence is unaffected by
either fix.

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

