# Block 81b gate — per-field component counts and their detection evidence

**No microscope, no rig booking.** Every computing limb runs over real saved
acquisitions already in the evidence archive. Your part is one command and then
looking at two pictures.

## Pin

The gate scores this implementation:

```
git merge-base --is-ancestor 536fe07 HEAD && echo PINNED
```

If that does not print `PINNED`, you are on the wrong tree; stop.

## Step 1 — run the program (about 3 minutes)

One command. It reports each of eight limbs independently, owns its own log,
and exits nonzero if any limb fails **or** reports NOT EXERCISED.

```
cd <your microclaw checkout>
.venv/bin/python design/81-block81b-gate.py \
    --archive "$HOME/Documents/Documents - Beyonce/Projects/Micro-Claw" \
    --out /tmp/81b-gate
```

It prints a `BLOCK 81b GATE PASSED` / `FAILED` / `INCOMPLETE` line and writes
`/tmp/81b-gate/gate-log.txt` and `gate-results.json`. Send those two files back
whatever the verdict.

The eight limbs, and what each is actually asking:

| | asks |
|---|---|
| A | does the built-in count each original saved field separately, keyed by its saved position? |
| B | is signal in two overlapping fields counted in **both**, with no deduplication? |
| C | do 81 positions against a 64-artifact budget still get 81 counts, with the shortfall disclosed? |
| D | does the mosaic path write a detection overlay (register row `R43`)? |
| E | does every count link to the artifact showing the pixels it came from? |
| F | do tile placements carry identity read from the dataset, not the live position list? |
| G | does the evidence image show the **sample**, not only the tool's own marks? |
| H | does an unfiltered count disclose that it is mostly single pixels? |

Expected numbers on the current archive, so a silent change is visible: A gives
9 fields; B gives 5 fields and 164 shared centroids; C gives 81 counted and 64
annotated; F gives 9 placements all carrying a `PositionName`; G's worst
filtered field renders ~93 % interior; H's worst field is 170/180 (94 %) single
pixels.

## Step 2 — look at two pictures (this is the part only you can do)

The gate wrote annotated fields under `/tmp/81b-gate`. Open these two sets in
whatever you normally use:

- `/tmp/81b-gate/g-readable/artifacts/components-*.tiff` — nine fields measured
  with `min_area_um2 = 0.2`.
- `/tmp/81b-gate/a-beads/artifacts/components-*.tiff` — the same nine fields at
  the **default** filter.

Three questions, and the answers matter more than the gate's verdict:

1. In the `g-readable` set, **can you see the beads themselves**, not just the
   outlines and numbers drawn on them? The whole point of the block is that you
   can check a number against the pixels. If the field is black, the block has
   failed even though limb G passed, and I want to know.
2. Do the outlines land on things you would call beads, and does the field
   labelled `T4` look empty to you? `T4` is `r1_c0` — the tile the 2026-09-09
   session's hand-written hook reported **52 beads** on. It should show noise
   and no detections at all.
3. In the `a-beads` set, is it obvious at a glance that the numbers there are
   nonsense? They are: those fields report 150–224 components where your eye
   counted 3–5. It should be visibly a wall of ink, and the result carries a
   note saying so.

Question 1 is the one to answer carefully. The block already shipped one
version where every evidence image rendered as glyphs on a black field, and
four tests passed while it did, because they asserted the ink's value instead
of looking at the picture.

## What a FAIL or NOT EXERCISED means

A limb that could not run its mechanism prints **NOT EXERCISED**, and that is
never a pass — it means the gate learned nothing about that limb. Send the log
either way; do not re-run to try to get a green line.

## What this gate does not establish

- **Not a validated bead counter.** A component is contiguous thresholded
  signal. Touching beads merge, noise fragments, and the threshold matters. The
  block calls the number a *component count* everywhere for that reason.
- **n=2 camera geometries**, both M5-family — a 90° rotated Andor and a sheared
  Hamamatsu. It says nothing about a rig whose optical path differs from both.
- **No grid was acquired for this block**, so no deliberately-chosen overlap
  fraction was tested; limb B uses whatever overlap that 2026-08 acquisition
  happened to have.
