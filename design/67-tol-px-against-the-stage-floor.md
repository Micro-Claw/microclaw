# `tol_px` is a pixel tolerance with no relation to what the stage can reach

## Problem

`center_feature(max_iter=3, tol_px=5.0)` declares success when the brightest
feature's residual falls within `tol_px` pixels of centre. Nothing checks that
number against the smallest correction the stage can actually make.

On M2, measured:

| quantity | value | source |
| --- | ---: | --- |
| pixel size, `Res1` binning 1 | 0.127 µm/px | MM `PixelSizeAffine`, `gate64-m2/affine_resolution.json` |
| stage quantization | ~0.8 µm | design/29 — identical 2 µm commands gave 14.8 or 22.4 px |
| **that quantization in pixels** | **6.30 px** | 0.8 / 0.127 |
| `center_feature` default `tol_px` | 5.0 px | `tools.py:5316` |

One step of this stage is larger than the tolerance the loop is trying to reach.
Convergence is then partly luck about where a step lands:

- `gate64b-m2` limb F2 — the only real convergence measurement we have — went
  47.0 → **3.9 px in 3 iterations**. It passed. 3.9 px is 0.50 µm, *less than
  one quantization step*: the loop landed inside a tolerance the stage cannot
  reliably deliver.
- `gate64-m2` limb F2 entered at 3.9 px, already inside `tol_px=5.0`, and did
  **0 iterations**. That limb measured nothing about convergence and its own
  probe comment says so.
- The measurement has a floor of its own: design/64 recorded the loop reporting
  2.1 px while an independent re-snap of the same state measured 3.9 px, so on a
  defocused bead field detector repeatability is ~2 px.

`tol_px = 5.0` therefore sits *between* the two floors — above the detector's
~2 px, below the stage's 6.3 px.

**And when the loop fails, it misdiagnoses.** The only hint is:

> Residual did not fall below tol_px. If it GREW between iterations, the
> calibration may be stale — rerun calibrate_stage_to_camera.

A stage floor is not a stale calibration and the fix is different. The defect is
already written into the suite: `test_failure_to_converge_is_finite_and_honest`
(`tests/test_tools.py:3247`) builds a stage that **quantizes to 2 µm** — the
stage-floor case exactly — and asserts the returned hint says
`rerun calibrate_stage_to_camera`. The test pins the misdiagnosis.

## Decision

### Do not learn, configure, or enforce a step size

The obvious fix — read the stage's resolution, derive a floor, refuse a `tol_px`
below it — requires a number Micro-Manager does not publish and an operator
cannot supply at install time. design/66 built precisely that machinery for the
single-axis settle band and then **deleted it**: "All of that machinery existed
to discover a number we do not need." The same applies here. No config key, no
runtime calibration of repeatability, no knowledge-base entry, no refusal.

### Report what the loop already knows

Three of the four changes cost nothing but plumbing, because the values already
exist inside the loop and are thrown away.

1. **Keep the residual sequence.** `residuals_px`, one magnitude per iteration.
   It is the evidence that separates the failure causes, and today only the last
   one survives.
2. **Report the residual in µm as well as px** — `residual_um`, through the same
   affine already in hand. "3.9 px" is not comparable to a stage specification;
   "0.50 µm" is, and the operator can see at a glance that it is under their
   step size.
3. **Report the smallest correction actually commanded**, in µm. That is the
   scale at which the loop was asking the stage to do something, and it is the
   number to compare against a step size.

### Classify the non-convergence from the sequence

Replace the single hint with one derived from `residuals_px`, so each cause gets
the sentence that fits it:

- **rising** — residual grew across iterations → the calibration may be stale or
  wrong. This is today's hint, now *earned* from the sequence instead of
  asserted for every failure.
- **plateaued** — fell, then stopped improving while still above `tol_px` → a
  floor. Name both candidates: the stage's step size, with the smallest
  commanded correction in µm beside it, and the detector's own repeatability.
  Say that `tol_px` may be below what this rig can reach and that raising it is
  a legitimate answer.
- **still falling** — improving at `max_iter` → it simply needs more iterations.
  Blame nothing; say to raise `max_iter`.

`centered: true` is unchanged. This block adds no refusal and no confirmation:
a "no" would only abort a run the operator asked for, so it is information, and
information goes to the result (CLAUDE.md).

## Tests

`tests/synthetic_optics.py` already models a quantizing stage (`quantum_um`), so
every case below is off-rig.

1. A stage quantizing well above `tol_px` produces the **plateaued** hint, and
   that hint names the stage and carries the smallest commanded correction in
   µm. This is `test_failure_to_converge_is_finite_and_honest` rewritten: its
   current assertion on `rerun calibrate_stage_to_camera` is the defect.
2. A **wrong** affine (residual growing) still produces the stale-calibration
   hint — the one case where today's text is right. Watch it fail against a
   version that always says "plateaued".
3. A loop still improving at `max_iter` says so and blames neither.
4. `residual_um` equals `residual_px` through the session's affine, on both axis
   conventions, and is absent when no affine resolved.
5. `residuals_px` has one entry per iteration performed, and its last entry is
   `residual_px`'s magnitude — including on the `centered: true` path.
6. Export: the emitted script's loop is unchanged in behaviour. The emitter
   renders `tol_px` as a literal (`tools.py:329`) and must keep doing so; a
   `residuals_px` that exists only in the live result is not a `CannotEmit`.

**What the tests could all happen to pass.** A fixture whose stage quantizes
*below* `tol_px` converges cleanly and cannot distinguish any of the three
classifications. Every case above needs a quantum large enough that the loop
genuinely cannot reach the tolerance — and case 3 needs a quantum small enough
that it would converge given more iterations, or it is case 1 wearing a
different name.

## Implemented 2026-08-30 — two decisions the implementation forced

**`residual_offset_um`, not `residual_um`.** design/66 (in flight) defines
`residual_um` on a *single-axis stage move* result as the arrival miss,
`|measured_um - target_um|`. This block's field is a different quantity — the
feature's remaining offset, expressed as the stage move that would centre it —
and `center_feature` calls `move_stage_xy`, so once design/66's XY loop exists
both would appear under one name in adjacent records of the same session. An
agent reading `residual_um: 0.5` could not tell whether the stage missed by half
a micron or the feature is half a micron off centre, and those imply opposite
actions. Renamed here rather than there because design/66's rig gate already
scores on `residual_um` and, as that document says of `tolerance_um`, a result
field becomes load-bearing about one block after it ships.

**The rising test is last-versus-first, and deliberately conservative.** A loop
that converges and *then* diverges — 100 → 5 → 50 px — is classified
`plateaued`, not `rising`, because its final residual is still below its first.
The alternative rule, "worse than the best seen", would catch that case and also
flag ordinary plateau noise (5.0 → 4.9 → 5.0) as a stale calibration — which is
precisely the over-blaming this block exists to stop. Being conservative about
claiming "stale calibration" is the correct direction to err here. Recorded as a
known limitation rather than fixed: the sequence is in `residuals_px`, so an
operator who wants that reading has the numbers.

## Rig gate

**None.** Every mechanism here is a computation over values the loop already
has, and the M2 data that sized it is already in `gate64-m2` / `gate64b-m2`.
Scoring the new hint on a rig would need a session that *fails* to converge,
which is dose spent to observe a string. If a later block books M2 time for
`move_stage_xy`, read `residuals_px` and `residual_um` out of that session's
history as free corroboration — but this block does not book a trip.

## Out of scope

- **`move_stage_xy`'s arrival contract** (design/35 register). It is the reason
  the loop can snap before the stage has arrived, and it would let
  `center_feature` measure the stage's real step directly — from requested
  versus measured — instead of inferring a floor from residuals. That is the
  better mechanism and it belongs in that block, after design/66 lands.
- Changing the `tol_px` or `max_iter` defaults. Nothing measured here says what
  they should be on a rig that is not M2, and one machine is not a property.
- Refusing, clamping, or warning on a `tol_px` the loop believes unreachable.
- Detector repeatability as a measured quantity. Recorded as ~2 px from one
  bead field, n=1; not a number to build on.
