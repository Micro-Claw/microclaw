# design/28 — findings from the tiling-with-Nestor session (2026-07-17)

Source transcript: `20260717_133127_microclaw_history_tiling_with_nestor.json`
(the run history from a live rig session; not committed — histories live in
OneDrive/Microclaw per [[history-json-location]]).

## What the session did

User asked for a gapless raster of a 100 µm square, cells counted, on 561 @ 5%
/ filter 600/60. It grew into: a cross-tile cell count (11 cells, 561), stitched
mosaics in both 488 and 561 at 100 µm and again at 900 µm, plus a long
orientation-debugging detour. The deliverables were produced. Getting there
exposed four tool problems, ordered below by how much they cost.

The agent's *judgment* was mostly sound — it repeatedly refused to trust a false
`converged: true`, verified every channel switch, and checked stage reach before
committing 5000 exposures. The friction came from tools that don't fit this
sample type (sparse fluorescent puncta on a dark field), not from bad decisions.
The one behavioural miss: it moved the stage on its own to "hunt for signal" and
was rightly rebuked. That is a prompt/behaviour issue, not covered here.

---

## Finding 1 — autofocus reports `converged: true` with the peak at the sweep edge

**Severity: high.** This is the single biggest time sink in the transcript.

`run_autofocus` returned `converged: true, moved: true` several times while the
best Z sat at the *boundary* of the sweep (`peak_interior: false`), moving the
stage 10 µm onto a non-peak. The edge case only produces a soft `warning`
string; `converged`/`moved` ignore it entirely.

Root cause — `microclaw/autofocus.py`, `coarse_then_fine_autofocus` (and the
`single_sweep`/`sweep` variants): the convergence decision consults
`curve_contrast` (is the curve flat?) but never `peak_interior` (is the argmax
at an edge?). A monotonic curve that climbs to a boundary has high contrast and
passes the gate, so the tool "converges" onto the edge.

```python
# autofocus.py — fine pass tail of coarse_then_fine_autofocus (approx line 178)
# BEFORE: converges regardless of where the peak sits
_restore(ctrl, fine.best_z_um)
return AutofocusResult(
    coarse=coarse, fine=fine, entry_z_um=entry_z,
    final_z_um=fine.best_z_um, converged=True, moved=True, reason=None,
)

# AFTER: an edge peak is not enough evidence of convergence.
if not fine.peak_interior:
    _restore(ctrl, entry_z)                     # don't move onto a boundary
    return AutofocusResult(
        coarse=coarse, fine=fine, entry_z_um=entry_z, final_z_um=entry_z,
        converged=False, moved=False,
        reason=_edge_reason(fine, entry_z),     # report ambiguity; inspect curve
    )
_restore(ctrl, fine.best_z_um)
return AutofocusResult(..., converged=True, moved=True, reason=None)
```

`_edge_reason` mirrors `_flat_reason`: state that Z was NOT moved and that the
curve has no interior maximum. The reason must not claim a unique cause: focus
may be outside the searched range, but a U-shaped or noise-dominated curve can
also put its maximum at an edge. The *contract* the model keys on (`converged`,
`moved`) must reflect the failure either way.

---

## Finding 2 — anomalous U-shaped focus curve; cause not established

> **SUPERSEDED 2026-08-04 by [design/36](36-focus-metric-inversion.md).** It was
> a metric-sign bug. The section below is kept as written, because how it
> reached the wrong answer matters: the reasoning is sound except for one
> clause — "the normalization denominator ... does not collapse merely because
> the signal becomes spatially concentrated" — which is false, because `bg` is
> the per-frame *median* and the median rises to meet the spreading light. The
> synthetic that exonerated the metric was noiseless, and a noise floor is
> exactly what pins the numerator flat while that denominator collapses. The
> metric was minimised at focus. design/36 reproduces it offline, on four field
> types, and replaces the metric with Tenengrad. Read the fix instruction below
> ("Keep normalized Laplacian variance as the single default") as reversed;
> the *process* instruction it ends with — compare candidates against real
> Z-stacks with visual ground truth — was right, and is still owed.

**Severity: high for this session, but not a demonstrated metric-sign bug.**

The recorded `normalized_laplacian_variance` sweep was genuinely U-shaped:
highest at both ends and lowest near the user's by-eye focus of 60.62 µm. The
autofocus therefore selected a boundary rather than the visual focal plane. That
observation is valid; the original conclusion that Laplacian variance has the
wrong sign for sparse fluorescent puncta is not.

Laplacian variance is polarity-insensitive. Reversing a bright-on-dark edge to a
dark-on-bright edge reverses the Laplacian's sign, but variance removes that sign.
For a flux-conserving point-spread function, concentrating the same photons into
a tighter spot increases its high-frequency/Laplacian energy. The normalization
denominator, `mean(abs(I - bg))²`, is approximately total signal per image area
and does not collapse merely because the signal becomes spatially concentrated.
A synthetic, flux-conserving bright punctum on a dark background confirms that
the existing normalized Laplacian score decreases monotonically with defocus.

The U-curve must therefore be treated as an acquisition- or normalization-specific
failure until raw frames establish its cause. Plausible causes include signal
falling toward the noise floor at the sweep ends, Z-dependent background or
illumination, clipping/saturation, different axial sample structure, drift or
bleaching, and other acquisition artifacts. The transcript retained metric
values and only the final thumbnail, not every raw sweep frame, so it cannot
distinguish these explanations.

### Fix

- Keep normalized Laplacian variance as the single default autofocus objective.
  Do not infer metric choice from one entry image.
- Treat a boundary argmax as non-convergence and restore the entry Z (Finding 1),
  without claiming that the true peak necessarily lies beyond the window: a
  U-shaped or noise-dominated curve can produce the same result.
- Preserve the full metric curve and inspect signal, saturation, and intensity
  stability across the sweep. A future diagnostic acquisition should retain raw
  frames at every Z so the anomalous curve can be reproduced and explained.
- Evaluate any alternative metric (for example Tenengrad, Brenner, or a puncta
  peakedness score) only against the same saved real Z-stacks with visual ground
  truth. Do not ship a content classifier or make an alternative the default
  until that comparison demonstrates an advantage.

The implementation briefly added `puncta_sharpness`, a one-frame
`choose_focus_metric` heuristic, and public `metric="auto"|"puncta"|"laplacian"`
arguments. Those changes were removed: their tests showed only that the new
metric itself peaked on a synthetic PSF, while failing to test—and in fact being
contradicted by—the premise that normalized Laplacian variance inverted on that
same PSF.

---

## Finding 3 — `export_dataset_as_tiff` raises `KeyError: 'position'` on multi-position data

**Severity: medium (blocks a real workflow; has a live-hook workaround).**

Exporting the saved 36-tile dataset failed with `KeyError: 'position'`, which
forced the whole stitch to be redone *live* via a hook instead of read off the
already-saved NDTiff.

Root cause — `microclaw/tools.py::export_dataset_as_tiff` (~line 800):

```python
ranges = [range(len(axes[a])) for a in axis_names]          # positional indices
frames = [dataset.read_image(**dict(zip(axis_names, combo)))
          for combo in itertools.product(*ranges)]           # dense hypercube
```

Two wrong assumptions: (a) each axis's coordinate values are exactly
`0..len-1` — for the `position` axis NDTiff coordinates are not guaranteed
contiguous/zero-based, so `read_image(position=k)` looks up a coordinate that
isn't stored and raises `KeyError: 'position'`; (b) every combo in the full
Cartesian product exists — a multi-position grid with one frame per position is
not a dense (T,Z,C,P) hypercube.

Fix — iterate the *actual* coordinate values NDTiff reports, and guard each read
with `has_image` so a sparse product skips cleanly:

```python
coord_values = {a: sorted(axes[a]) for a in axis_names}      # real coords, not range()
frames = []
for combo in itertools.product(*(coord_values[a] for a in axis_names)):
    coords = dict(zip(axis_names, combo))
    if dataset.has_image(**coords):
        frames.append(dataset.read_image(**coords))
    else:
        frames.append(np.zeros(frame_shape, dtype=frame_dtype))  # keep hyperstack rectangular
```

(Determine `frame_shape`/`frame_dtype` from the first present image.) This also
lets the exporter double as the missing "stitch a saved dataset" primitive —
see Finding 5.

---

## Finding 4 — calibration's "degenerate" is a misdiagnosis; the real cause was step > FOV

**Severity: high (the false failure cascaded into everything downstream).**

This is the finding the user flagged directly: the field was **not** a single
symmetric blob, and it's unclear why it was read that way. Here is the actual
mechanism.

`calibrate_stage_to_camera` snaps a reference, moves `step_um` (default **20**)
along X, snaps, cross-correlates; repeats for Y. `solve_affine`
(`calibration.py:66`) declares "degenerate" when the 2×2 matrix of the two
measured pixel-shifts has a near-zero determinant, and the message blames a
"featureless field, or a step too small to move the image."

But do the FOV arithmetic for the ROI in play. The cropped ROI was 180×176 px at
0.105 µm/px → an **18.9 × 18.5 µm** field of view. A **20 µm** stage move shifts
the image by 20 / 0.105 ≈ **190 px** — *more than the entire frame width*. After
the move, the reference and the shifted snap share essentially **zero overlap**:
they are pictures of two different, non-overlapping regions. `phase_cross_
correlation` is FFT-based and circular, so on non-overlapping frames it returns
an aliased / near-zero shift. Both axis moves do this, the two shift vectors come
out collinear/tiny, the determinant is ~0 — "degenerate." The 10 µm retry is no
better: 10 / 0.105 ≈ 95 px in a 180 px frame leaves < 50 % overlap, marginal at
best. The features were there the whole time; the move simply translated them
out of frame. **The message points at the opposite of the true cause** (step too
*large* for the FOV, not too small / featureless).

(The very first full-frame attempt — FOV ~242 µm, 20 µm move, ~8 % shift, plenty
of overlap — failing is less certain and probably a different, secondary issue;
the ROI attempts are unambiguously the overlap problem.)

### What a field needs for this calibration to succeed

The registration is a translation cross-correlation between two snaps, so the
requirements are about **overlap and unambiguous structure**, not "lots of
cells":

1. **Substantial overlap between the two snaps.** The move must be small
   relative to the FOV — rule of thumb `step ≲ ¼–⅓ × min(FOV_x, FOV_y)` — so a
   large fraction of the *same* scene appears in both frames. This is the
   requirement the fixed 20 µm default violated on a ~18 µm ROI. It is the
   dominant constraint.
2. **Aperiodic, contrasty structure in the overlap region.** Texture or several
   features spread across the frame give one sharp, unambiguous correlation
   peak. A *single* compact feature actually registers fine for pure translation
   (bead tracking works) — so "single blob" was not the real problem — but it is
   fragile if it clips the edge. *Periodic/repeating* patterns are the genuine
   killer (aliased peaks), as is a near-uniform field (no peak).
3. **Signal above the noise floor** (SNR above the same few-count threshold used
   elsewhere) so the peak isn't buried in shot noise.
4. Both axis moves must keep structure in frame so the two shift vectors are
   measurable and linearly independent.

### Proposed fixes

- **Scale the step to the FOV instead of a fixed 20 µm.** If a `pixel_size_um`
  is known (the user had 105 nm here), compute `step = frac × min(FOV)` with
  `frac ≈ 0.25`. With no pixel size, start small and auto-grow until the shift is
  both measurable and < ⅓ frame:

```python
def calibrate_stage_to_camera(ctrl, guard, step_um=None, pixel_size_hint_um=None):
    if step_um is None and pixel_size_hint_um:
        h, w = _roi_shape(ctrl)                       # px
        fov_min_um = pixel_size_hint_um * min(h, w)
        step_um = 0.25 * fov_min_um                   # keep ~75% overlap
    ...
```

- **Use the correlation quality that is already computed but discarded.**
  `phase_cross_correlation(..., upsample_factor=10)` returns `(shift, error,
  phasediff)`; the code throws `error` away with `_, _`. A high `error` (or a
  shift magnitude ≥ ~½ frame) means "no reliable registration" and should be
  reported *as such*, distinctly from geometric degeneracy.

- **Split the failure message into the three real cases** so the model can act:
  shift ≈ full frame → "step too large for this ROI/FOV, reduce step_um or clear
  the ROI"; shift ≈ 0 with good peak → "step too small, increase step_um"; no
  usable peak → "field lacks trackable structure." The current single string
  conflates all three and, in this session, named the wrong one.

---

## Finding 5 — no way to analyse or stitch a *saved* dataset (recurring)

**Severity: medium (design gap, not a bug).** Promoted to its own doc:
**design/29-offline-dataset-analysis.md**.

Every "count the cells in the mosaic" and "is the orientation right?" question
required either a live re-scan (36–2500 fresh exposures of bleaching) or the
user's own eyes, because no tool loads a saved TIFF/NDTiff back for analysis.
Consequences in this session: the 900 µm scans produced *zero* cell counts
(the counter only ran live on the first 100 µm 561 scan → 11 cells); the
orientation fix took four full re-scans because the agent couldn't read its own
output back to check.

A read-side analysis tool (`analyze_saved_dataset` / `stitch_saved_dataset`)
would let counting, stitching, and orientation checks run on pixels already on
disk — no extra dose, no live hook. Finding 3's fix is the natural foundation:
once the exporter iterates real coordinates, the same reader can feed the
existing `mosaic_cell_counter` / stitch logic offline. Worth its own design doc.

---

## Priority

1. **Finding 1** (autofocus edge = false convergence) and **Finding 4**
   (calibration misdiagnosis) — both cause confident-wrong hardware moves and
   both cascaded through the whole session. Small, self-contained fixes.
2. **Finding 2** (anomalous autofocus curve) — retain the safe boundary rejection
   and collect raw Z-stack evidence before changing the focus objective.
3. **Finding 3** (exporter KeyError) — small, unblocks the saved-data workflow.
4. **Finding 5** (offline dataset analysis) — larger; **design/29**; builds on #3.
