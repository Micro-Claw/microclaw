# Block 81b gate — per-field component counts

**No microscope, no rig booking.** Every scoring limb runs over real saved
acquisitions already in the evidence archive. The coordinator has already run
it; what is left for you is one question about one picture.

## Pin

```
git merge-base --is-ancestor 06074bd HEAD && echo PINNED
```

## What the block delivers, after the annotations were cut

`connected_components` measures **each original saved frame** rather than only
a resampled mosaic: real recorded calibration including rotation and shear,
real zero-valued camera pixels counted as observations, one observation per
saved coordinate, tile placement identity in the mosaic manifest, and a
disclosure of what an unfiltered count is made of.

It **does not** write detection-evidence images. It did; they were unreadable
and were removed in full. That work returns to register row `R43` and
design/73 §3, together with the larger problem underneath it — see "What is
not settled".

## The program

```
cd <your microclaw checkout>
.venv/bin/python design/81-block81b-gate.py \
    --archive "$HOME/Documents/Documents - Beyonce/Projects/Micro-Claw" \
    --out /tmp/81b-gate
```

Seven limbs, each reported independently; it owns its log
(`/tmp/81b-gate/gate-log.txt`) and exits nonzero on any FAIL or on any
NOT EXERCISED other than limb E, which is a deliverable rather than a verdict.

| | asks | last measured |
|---|---|---|
| A | is each original saved field counted separately, keyed by its saved position? | 9 fields |
| B | is signal in two overlapping fields counted in **both**, with no deduplication? | 5 fields, 164 shared centroids |
| C | does it scale, and does a frames run write no artifacts? | 81 fields, 0 artifacts |
| D | does one object seen from two fields get **one** stage coordinate? | 15 pairs, median 750 nm |
| E | writes the figure a person has to judge | 9 figures, always NOT EXERCISED |
| F | do tile placements carry identity read from the dataset, not the live list? | 9 placements, 9 named |
| G | does an unfiltered count disclose it is mostly single pixels? | worst 170/180 (94 %) |

Limb D is the one that would catch a sign error, a transpose, or a wrong
frame-centre convention, and it runs on a **sheared** calibration where those
cannot hide.

## The one thing that needs your eye

The program writes `/tmp/81b-gate/figure-<position>.png` — nine fields, the
real data on a background-weighted stretch, with the reported bounding boxes
in red drawn *outside* each object so they cover nothing.

**Question: do the red boxes sit on real objects, and is anything obviously
missed?**

You have already answered this once, on the same rendering, for `r0_c2`:
*"the red boxes are around real beads (or clusters of beads, which will show
up as a single, brighter spot than individual beads since they are
sub-diffraction limit in size). The image looks like a real image."* Re-running
it is only worth your time if you want to check a field other than that one.
`figure-r1_c0.png` is the useful second look: it is the field the 2026-09-09
session's hook reported **52 beads** on, and it should show noise and no boxes
at all.

A count that agrees with itself is not a count that found the objects. This
figure is the only check for that, and no program can make it.

## What this gate does not establish

- **It is not a bead counter, and cannot become one by improving segmentation.**
  Your own point, and it is now the first clause of the product's
  `count_semantics`: sub-diffraction objects that cluster image as one
  *brighter* spot, not a larger one, so a count of spots is not a count of
  objects at any threshold. The quantity that separates them is integrated
  intensity, which this measurement does not report — register row `R120`.
- **Limb D's residual is not attributed.** 750 nm median, ~7 px, and X-neighbour
  pairs do far better than Y-neighbour pairs. Stage positioning against the
  recorded intended coordinate, a calibration error and drift all look like
  this from one dataset on one rig. It is inherited from the same affine and
  intended coordinates the mosaic path already uses, not introduced here.
- **n = 2 camera geometries**, both M5-family: a 90°-rotated Andor and a
  sheared Hamamatsu.
- **No dataset here was acquired by the current tree.** All three predate
  81a-1 and 81a-2. Worth ticking opportunistically on the next multiposition
  run anywhere; not worth a booking, because the failure mode is a loud typed
  refusal rather than a wrong number.
