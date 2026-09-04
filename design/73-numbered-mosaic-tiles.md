# Number mosaic tiles so an operator can name one

## Request

The stage-coordinate mosaic is already correct. Add a visible label to every
tile so an operator can point at a field as `P1`, `P2`, ... and ask the agent to
revisit it. It must not change tile placement, rasterization, overlap
precedence, canvas bounds, the order in which the existing stitcher draws source
tiles, or the pixels any measurement reads.

## What exists

`build_stage_coordinate_mosaic` (`tools.py:5009`) selects one saved NDTiff plane
and passes its frames to `assemble_stage_coordinate_mosaic`
(`dataset_mosaic.py:41`). The pure stitcher derives canvas bounds from intended
stage XY, inverse-samples each source image, and lets later frames overwrite
earlier frames. That path is the authority for mosaic pixels and geometry and
does not change.

Facts this design depends on, each checked in the tree:

- The stitcher's dtype follows the **source** (`dataset_mosaic.py:71-77`), and
  `tools.py:5137` casts to uint16 **without rescaling**. A GRAY8 acquisition
  therefore produces a mosaic whose real data spans 0-255 inside a uint16 file.
- `open_artifact(analyze=True)` (`tools.py:9096-9104`) reads a mosaic's pixels
  into the conversation after a **2nd-99.8th percentile stretch over nonzero
  pixels**, and reports the zero fraction as uncovered area. Bright or zero
  annotation pixels corrupt both.
- `/api/artifact`'s download allowlist (`webserve.py:250-284`) and the
  checkpoint's artifact collection (`conversation.py:298-303`) both read a
  **singular** `artifact` dict. A file not named there is not downloadable from
  the browser, though `open_artifact` still reaches it.
- `open_artifact` verifies a file against its sidecar `<path>.json`
  (`tools.py:8853`) and lifts a **fixed** key set out of the manifest
  (`tools.py:8889-8891`).
- The tool is `@refuses` for export (`tools.py:5006`). Adding an argument
  changes nothing there.

A hooked multiposition acquisition supplies positions to
`multi_d_acquisition_events` with `position_labels` (`tools.py:7971`,
`tools.py:8344`). Each saved image is expected to carry a `position` axis
coordinate, may carry `PositionName`, and carries the `XPosition_um_Intended` /
`YPosition_um_Intended` the stitcher already uses. **`PositionName`'s presence
in a *saved* dataset is not evidenced in this repo** — every recorded
observation is of live hook metadata (design/23), and `HookBase.where()` falls
back to `Axes.position`, which implies it is not always there. The saved
position coordinate may itself be a supplied label rather than an ordinal, so
sorting it is not evidence of visit order: lexical order can put `P10` before
`P2`, and arbitrary names can reorder in any way. See "Before implementation".

The live Micro-Manager position list is not a valid source during offline
rendering. It may have been reordered, renamed, cleared, or replaced since the
acquisition. The saved dataset is the acquisition-time record.

Two register rows are in this family. **R43** (design/70) records the shape an
operator already asked for — *"the built-in writes a label map or outline TIFF
next to the mosaic and `open_artifact` opens it"*. **R40** records that zero
padding in a mosaic already corrupts every `ImageStats` statistic; annotation
pixels must not become a second cause.

## Decision

### 1. One public option, defaulting on, that writes a second file

Add to `build_stage_coordinate_mosaic` and its tool schema:

```python
show_position_labels: bool = True
```

The file at `output_path` is **always** the plain mosaic, byte for byte what the
current implementation writes. When labels are on, the tool additionally writes
a labeled copy beside it, with its own manifest. The copy keeps the caller's own
suffix — `mosaic.tiff` yields `mosaic.labeled.tiff`, not `.tif` — because the
analysis path already writes `stage_coordinate_mosaic.tiff`
(`completed_dataset.py:429`) and an operator should not be handed a file whose
extension they did not choose.

This is R43's shape, and it is why the default can be on. Nothing downstream
needs an exception, because nothing downstream reads the labeled file: the
annotation cannot reach `run_analysis_on_saved_dataset`, an `ImageStats` call, a
future analyzer, or `open_artifact(analyze=True)` on the base mosaic. An opt-out
that one measurement caller must remember to pass is the alternative, and it is
one forgotten caller away from a wrong number.

Reject non-boolean values. Do not use a string enum or an inverse option such as
`plain`; the positive name makes the default visible at every call site.

**The result's singular `artifact` names the labeled copy when one was written**,
because that is the file the operator is meant to look at, and it is the only
key the browser download and the checkpoint collector read. The base mosaic path
stays in the result and the manifest as `base_artifact_path` — a plain string
key, exactly as `manifest_path` already is — and `open_artifact` reaches it by
path. With labels off, `artifact` names the base mosaic as it does today.

### 2. Number by position-event submission order, or name by identity

Build a position table from the already-selected `metadata_items`. Group frames
by their dataset `position` coordinate, resolve each position against the
acquisition-order record described in §8, and assign the one-based labels `P1`,
`P2`, ... in increasing visit ordinal. The contract defines "tile order" as the
order in which position events were submitted to the acquisition engine. That
is the durable software boundary corresponding to the supplied stage-position
list; it is established before asynchronous exposure callbacks or file saving
can reorder later observations. `P1` means the first submitted position event,
not the top-left tile, the first file saved, or merely the lowest position
coordinate.

New mosaic-capable acquisitions always write the §8 record; this does not depend
on the probe finding metadata inadequate. The mosaic reader uses that record as
the authority. For legacy datasets without it, the pre-implementation probe may
establish a saved per-image sequence or timestamp as a fallback only by proving
that it reproduces position-event submission order. Record the selected source,
raw value for each position, and interpretation in the manifest.

Do not accept `FrameIndex` or `Frame` merely because the key exists. The current
code treats those as a frame/time index (`hooks.py:63-77`); they may repeat at
every position and therefore may not be a global acquisition sequence. The
probe must demonstrate uniqueness or a valid composite ordering across
positions and compare the resulting order with an independent event-submission
trace. Likewise, a file-write, exposure-callback, or callback-completion
timestamp is not automatically position order: an asynchronous pipeline may
observe or save images in a different order.

For a position containing several frames, every entry in the explicit record
for that visit carries the same visit ordinal. A legacy metadata fallback uses
the earliest proven ordering value recorded at that position. Deterministic
legacy ties are broken by ascending saved `position` coordinate, but a tie is
also disclosed in the manifest; the tie-break must never be described as proven
submission order.

If the saved dataset exposes no trustworthy acquisition-order field, the tool
may use ascending position coordinate **only after** the pre-implementation
probe proves that the fixed multiposition writer assigns those coordinates in
the supplied stage-list visit order. That fallback is valid only for that proven
fixed acquisition shape.

**Two label vocabularies, never mixed in one mosaic.** `P<n>` is an ordering
claim and is written only where position-event submission order is proven. Where it is not —
adaptive, jumping, revisited, or otherwise reordered acquisitions, and any
dataset whose order evidence fails — the tiles are still labeled, with `T<n>`,
which claims identity and nothing else. The operator's request is to point at a
field and revisit it; that needs a name, not a rank, and withholding the name
because the rank is unknowable would leave the runs that most need naming with
no labels at all. A mosaic carries one vocabulary throughout, the manifest
records which, and no output ever mixes them.

`T<n>` numbering is not an ordering claim and must not be described as one: when
the saved position coordinate is an integer, `n` is that coordinate; when it is a
supplied string label, `n` is the one-based index into the coordinates in the
dataset's own `sorted` order, and the manifest maps `T<n>` to the saved label. In
both cases the map is the authority and the drawn number is a handle.

This is deliberately separate from the frame iterator handed to
`assemble_stage_coordinate_mosaic`. Do not sort, group, or rebuild
`metadata_items` to add labels. The stitcher receives precisely the same frame
sequence as before, so overlap winners and the base output are unchanged.

The revisit identity is the non-empty saved `PositionName`, falling back to the
saved `Axes.position` / dataset `position` coordinate when that value is a
non-empty supplied label. The manifest records which source supplied the
identity. Each entry maps:

```json
{
  "display_label": "P1",
  "position_coordinate": 0,
  "position_name": "tile_r0_c0",
  "position_name_source": "PositionName",
  "acquisition_order_source": "acquisition_order_sidecar",
  "acquisition_order_raw_value": 17,
  "visit_ordinal": 1,
  "x_um": 123.4,
  "y_um": 567.8
}
```

Under the `T` vocabulary the same entry carries the `T<n>` display label and
`null` for `acquisition_order_source`, `acquisition_order_raw_value`, and
`visit_ordinal` — an absent order is recorded as absent, never as a zero or an
identity index standing in for one.

An agent asked to revisit a label resolves it through its entry to the recorded
`position_name` and coordinates; it must not guess from raster row/column or
consult the current list order. Existing safety checks still govern any move.

A position coordinate holding several frames still gets one label. Label count
is the number of distinct saved positions, not the number of images.

### 3. Draw on the copy, after stitching

Keep `assemble_stage_coordinate_mosaic` pure and unchanged. Take its uint16
canvas as the base, write it, then draw on a copy.

Anchor each label at the output coordinate of that position's intended stage
centre:

```text
col = round((x_um - origin_x_um) / output_pixel_size_um)
row = round((y_um - origin_y_um) / output_pixel_size_um)
```

The centre is invariant under camera rotation and mirroring, always lies within
the transformed tile, and avoids inventing a source-image "top left" whose
screen direction changes with the affine. Text is upright in output-image
coordinates. Centre the text box on the anchor, clipping to the canvas only when
a very small tile or an edge makes that necessary.

**Ink comes from the data, not from the container.** The stitcher's pre-cast
dtype is the authority: foreground is that dtype's maximum (255 for a GRAY8
acquisition, 65535 for GRAY16). Writing 65535 over an 8-bit mosaic renders the
whole sample black in any auto-scaling viewer.

**The outline value is 1, not 0.** Zero means "uncovered" to
`open_artifact(analyze=True)` and to R40's statistics, so the renderer must not
deliberately add more zero pixels. Drawing over a genuinely zero-valued source
pixel can still reduce the labeled copy's zero count; no equality with the base
is claimed. Both outline and foreground alter image statistics, which is why
the base file exists and why every measurement reads it instead.

Use one bundled bitmap font of twelve glyphs — `P`, `T` and the ten digits — in
a 5x7 cell, rendered without antialiasing, magnified by integer nearest neighbour. The
magnification is fixed by a stated formula, not by taste at call time:

```text
short_side_px = min side of the median tile's transformed bounding box, in output pixels
scale         = min(8, max(1, floor(short_side_px / 70)))
```

so a label is about a tenth of a tile's short side and never disappears or
swallows a tile. The constants are display taste; they live in code, are
recorded in the manifest, and make a replay checkable. No platform font, no
FreeType, no host rendering difference.

The renderer is a standalone pure helper beside the stitcher —
`draw_text_labels(canvas, labels, *, foreground, outline, scale)` over
`(row, col, text)` anchors — not a private step inside the mosaic tool. It takes
no dataset, no manifest and no metadata, so a later block that wants to draw on a
mosaic copy for another reason (R43's segmentation overlay is the open one) calls
it instead of writing a second text renderer. That is the only thing this block
owes R43, and it is why R43 stays a register row rather than becoming scope here.

Labels are drawn once after every source tile has been rasterized, so they take
no part in tile overwrite precedence and no tile can erase one. Where two label
boxes overlap, the higher label number draws last — later event submission
under `P`, higher saved identity under `T` — and the manifest records which.

### 4. Degrade and disclose; do not refuse

A label is display. Nothing unsafe follows from a missing one — the manifest is
the revisit authority and the move tools still guard the move — so identity
problems must not fail a mosaic that renders correctly today:

- no `position` axis, or no identity at all: write the base mosaic, skip the
  labeled copy, record `position_label_vocabulary: null` with a reason. This is
  the only case that produces no labels;
- no trustworthy acquisition order for this acquisition shape: write the labeled
  copy with the `T` vocabulary, and record the unavailable or invalid ordering
  evidence as the reason `P` was not used;
- one position missing both a usable `PositionName` and a usable saved position
  label, or holding contradictory intended XY: label every position that is
  identifiable, omit that one, set `position_labels_partial: true`, and name the
  coordinate and the reason.

The manifest is the channel for that disclosure. A refusal here would turn a
display preference into a regression for every dataset acquired before this
block, on the default path.

### 5. One manifest per TIFF

Each written TIFF keeps its own sidecar `<path>.json`, so
`_verify_against_manifest` works for either file and neither opens unverified.
The payloads are identical apart from `artifact`, `pixel_sha256`, and
`position_labels_drawn_here`. Both carry:

```json
{
  "position_label_vocabulary": "P",
  "position_labels_drawn_here": false,
  "position_labels_partial": false,
  "position_label_reason": null,
  "position_labels": [{"display_label": "P1", "...": "the entries from section 2, in label order"}],
  "base_artifact_path": ".../mosaic.tiff",
  "labeled_artifact_path": ".../mosaic.labeled.tiff",
  "base_pixel_sha256": "...",
  "annotation_convention": {
    "order": "ascending position-event visit ordinal",
    "order_source": "acquisition_order_sidecar",
    "anchor": "intended stage centre",
    "text": "P<one-based position-event submission order>",
    "glyph_cell_px": [5, 7],
    "glyph_scale": 4,
    "foreground": 65535,
    "outline": 1,
    "overlap": "later submitted position label overwrites earlier label pixels"
  }
}
```

The `foreground` shown is a GRAY16 dataset's; a GRAY8 one records 255. A `T`
mosaic records `"order": "none; T numbers saved position identity, not capture
order"` and the matching `text`.

**Three fields, three questions, because one token cannot answer them.**
`position_label_vocabulary` says what a drawn label would mean — `"P"`, `"T"`, or
`null` for a dataset that could not be labeled at all. `position_labels_drawn_here`
says whether *this* file carries them, and is true only in the labeled copy's
manifest, so a reader of the base sidecar is never left inferring from a path
whether labels exist elsewhere. `position_labels_partial` says whether some
position was skipped, with `position_label_reason` carrying the evidence for a
null vocabulary or a partial run. A single `position_labels_shown` would have made
"could not label" and "labeled in the sibling file" the same value.

`pixel_sha256` continues to hash the pixels of the file it sits beside, so
`open_artifact`'s existing check keeps working unchanged. `base_pixel_sha256`
always hashes the unannotated mosaic. The position map is written **even when
labels are off**, whenever the dataset supplies identity — with
`display_label: null` on every entry when the vocabulary is null. Annotation is
a display choice and `P<n>` is an ordering claim; the `position_name`-and-XY
revisit map is neither.

Add `position_labels`, `position_label_vocabulary` and
`position_labels_drawn_here` to the key set `_verify_against_manifest` lifts
(`tools.py:8889-8891`). Without that, an
operator who opens a labeled mosaic in a later session and says "go back to P3"
gets a payload with no mapping in it, and the agent has to know to read a JSON
file nobody told it about.

The canonical manifest hash changes for **both** paths, because the payload gains
keys whether or not labels are drawn. Manifest hashes recorded before this block
will not reproduce; `pixel_sha256` for a labels-off build will.

### 6. Commit the two TIFF/manifest pairs as one generation

When labels are on, construct and validate the base TIFF, labeled TIFF, and both
manifest byte strings before replacing any destination. Write all four to
temporary files in the destination directory, fsync and close them, then replace
the destinations. A failure before replacement leaves the previous generation
untouched and removes every temporary file.

Four filesystem replacements cannot be truly atomic as a group. Therefore each
manifest carries one shared, deterministic `artifact_generation_sha256`, computed
over both canonical payloads **with the `artifact_generation_sha256` key itself
absent** and then inserted into each; it cannot cover itself, and
`manifest_payload_sha256` still covers the payload including it. Both payloads
also name both files — `base_artifact_path` and `labeled_artifact_path` — so a
reader holding either sidecar can find the other and check that both name the
same generation. If a process or filesystem failure interrupts the replacement
sequence, a mixed generation is detectable and must be reported as incomplete
rather than silently pairing an old base with a new labeled copy. Replace the
base TIFF and its manifest last so `output_path` continues to identify the last
completely prepared generation for as long as possible.

When labels are off, retain the current one-TIFF/one-manifest staging behavior.

### 7. Downstream needs one line, and it is not an exception

`run_analysis_on_saved_dataset` already reads the file at `mosaic_path`
(`completed_dataset.py:430-441`), which is the plain mosaic under this design, so
its measurement is safe with no change at all. Pass `show_position_labels=False`
there anyway, for the single reason that a labeled copy nothing in that path
opens is a file and an artifact record written for nobody. If R43 is picked up
later, that is the line to revisit.

## Before implementation

This probe exists only to recover order for datasets acquired before §8 ships.
It does not decide whether new acquisitions write the order record; they always
do.

Confirm against fixed and, if available, adaptive legacy saved datasets in the
evidence archive (`~/Documents/Documents - Beyonce/Projects/Micro-Claw`) that
`Dataset.read_metadata()` returns `PositionName` for a hooked multiposition
acquisition, and whether `Axes.position` or the dataset coordinate retains the
supplied label when it does not. Also enumerate the saved sequence/timestamp
candidates and compare their order with the acquisition log or another
independent event-submission trace. Specifically test whether `FrameIndex` /
`Frame` repeats across positions and whether any timestamp reproduces submission
order rather than save or callback completion. Record the keys exactly as they
come back, their types, uniqueness, monotonicity, tie behavior, and whether
ascending `position` coordinate is proven to equal supplied-list order for the
fixed writer. Every observation in the repo today is of live hook tags. This
probe decides both whether §4's partial path is common and which legacy evidence
can truthfully support `P1` meaning "first submitted position."

The probe also settles what no unit test can: whether a candidate legacy order
agrees with an independent event-submission trace. A fixture cannot answer that
— it would only replay the assumption being tested.

If legacy datasets contain no field and no proven fixed fallback, do not broaden
or reinterpret a weak key. Those datasets get the `T` vocabulary and the stated
reason. New datasets do not depend on that outcome because §8 records their
submission order unconditionally.

### 8. Always record position-event submission order for new datasets

The reader can only report order that something wrote down. Every
mosaic-capable acquisition therefore records, for each submitted position, the
dataset position coordinate, supplied position label, intended XY, and a
monotonically increasing visit ordinal at event submission. This is unconditional
for new datasets, even when the probe finds that a current metadata key happens
to reproduce the same order. It prevents the contract from depending on adapter
metadata or asynchronous saving behavior.

**Write it as a sidecar beside the dataset, not as a new event key.** The engine
contract in `CLAUDE.md` is explicit: `event_to_json` / `event_from_json`
serialise a **closed** key set and silently drop everything else, so an invented
event field is the failure mode this project has already paid for. `tags` is
inside that set, but whether an event's `tags` reach saved NDTiff image metadata
is unverified here, and "unverified" is not a mechanism. The sidecar is written by
the acquisition tool, needs no engine cooperation, and is testable. A metadata
route may replace it later only once a real acquisition has demonstrated the key
surviving to `read_metadata`.

The mosaic reader's preference order is: this record, then a legacy metadata or
fixed-writer fallback proven by the probe, then `T`. A dataset acquired before
this ships is unaffected and keeps `T` when no legacy fallback is proven — the
whole point of §2's fallback is that it degrades honestly rather than inventing
order.

This section is the one part of the block a fake cannot settle. Its gate is G2.

## Rejected alternatives

- **Burning labels into the mosaic at `output_path`, with an opt-out.** The
  contamination surface is not one caller: `open_artifact(analyze=True)` stretches
  on percentiles over nonzero pixels and calls zeros uncovered, R40's statistics
  read the whole canvas, and every future analyzer would inherit the obligation
  to know which pixels are text. A second file costs disk; this costs a wrong
  number nobody can see.
- **Annotating only the conversation thumbnail** (`image_analysis.py:508`). The
  operator's real view is the ImageJ window `open_artifact` opens, and a
  thumbnail-only label is invisible there.
- **Reading the current stage list.** Mutable, and not evidence of what this
  dataset acquired.
- **Using raster row/column.** Placement may be rotated, mirrored, irregular,
  sparse, or non-grid; visual order is not saved order.
- **Using `PositionName` as the displayed text.** Names can be long or generated
  as `tile_r7_c12`. `P<n>` is compact; the manifest retains the exact name.
- **Labels inside the geometry assembler.** Couples presentation to the
  already-correct placement primitive and risks bounds or overlap semantics.
- **Antialiased system fonts.** Output would depend on the OS and installed font
  stack, defeating deterministic replay.
- **Declaring both files through a list-valued `artifact` key.** Both readers
  (`webserve.py:283`, `conversation.py:300`) require a dict, and the plural
  `artifacts` key already means "files I found", not "files I wrote" — widening
  it would put anything `inspect_artifacts` listed into the download allowlist.

## Costs accepted

A labeled mosaic doubles the TIFF bytes on disk, on the default path, for what
can be a large canvas. `show_position_labels=false` halves it. Taking the
cheaper default would mean an operator has to know the option exists before they
can name a tile, which is the whole feature.

Only the labeled copy is downloadable from the browser while labels are on,
because the allowlist reads one key. The base mosaic is reachable through
`open_artifact`, and a labels-off build declares it directly.

## Acceptance tests

1. **Ordering rule is discriminating.** A fixture whose recorded event-submission
   sequence disagrees with its position coordinates labels by visit ordinal,
   and the test asserts the two orders differ — so it fails if the
   implementation numbers by coordinate, save order, or spatial order.
2. Names such as `left`, `sample-B`, `tile_r0_c0` appear unchanged in the map for
   their label. Fixtures cover both `PositionName` and the saved `Axes.position`
   fallback and assert the recorded `position_name_source`.
3. A rotated affine and a reflected affine both anchor every label at the
   intended stage centre, with text upright.
4. The base TIFF is pixel-hash-identical to a committed pre-feature golden, for a
   fixture with overlaps and gaps, **with labels on and with labels off**.
   Overlap statistics and coverage are identical in both.
5. Omitting the argument behaves exactly as `show_position_labels=true`.
6. Two annotated builds of one input are byte-identical. No host qualifier.
7. A GRAY8 source produces a labeled copy whose maximum is 255, not 65535.
8. Outline pixels are nonzero, and the test makes no claim that annotation
   preserves the labeled copy's zero count or any other image statistic.
9. One position missing both `PositionName` and a usable saved position label
   yields labels for the rest, `position_labels_partial: true`, and names the
   offending coordinate. No refusal, and the base mosaic is written.
10. A dataset with no `position` axis writes the base mosaic and no labeled copy,
    `position_label_vocabulary: null`, and a stated reason.
11. **The `T` fallback labels rather than withholds.** A fixture whose order
    evidence fails — the probed key absent, non-monotonic, or an acquisition
    shape outside the proven fixed fallback — still writes a labeled copy, with
    `position_label_vocabulary: "T"`, the reason `P` was refused, and a map whose
    entries carry their `T<n>`. **No `P` glyph appears in the rendered copy and no
    `P<n>` string anywhere in either manifest**, so a vocabulary leak fails the
    test rather than passing as a plausible label.
12. Integer and string position coordinates both produce `T<n>`: the integer case
    numbers by coordinate, the string case by sorted index, and the map resolves
    each to its saved label.
13. Repeating `FrameIndex` / `Frame` values across positions are rejected as a
    global order, and the mosaic falls back to `T` rather than numbering by a
    repeated key.
14. The three manifest fields are independent: a labeled build's base sidecar
    carries `position_labels_drawn_here: false` with a non-null vocabulary, and
    an unlabelable dataset carries a null vocabulary — so the two cases are
    distinguishable without reading a path.
15. Both manifests verify: `manifest_payload_sha256_matches` and
    `pixel_sha256_matches` are true for each file, and `open_artifact`'s payload
    carries the position map. A deliberately mixed pair from two builds is
    rejected by `artifact_generation_sha256`.
16. The analysis path's analyzer input contains no annotation values and no
    labeled copy is written beside it.
17. Schema parity covers the boolean, its default, and its explanation.
18. A forced failure while staging any of the four outputs leaves an existing
    generation untouched and no temporary files. A simulated interruption
    between replacements produces a detectably incomplete generation.
19. Existing geometry tests pass unchanged; the annotation renderer is tested
    directly, separately from `assemble_stage_coordinate_mosaic`.

§8's record is **not** in this list. Locally it can only be asserted against a
fake that replays our own assumption about what the engine and the writer do,
which is the failure this repo keeps paying for; the reader's *preference* order
(record, then probed key, then `T`) is unit-testable and belongs to test 11's
family, but "the record is present, monotonic, and matches what the operator
watched happen" is G2's, on the demo machine.

## Block, gate and ledger

One block, two gates. §§1-7 settle **locally** — synthetic mosaics and saved
datasets from the evidence archive. §8 does not: proving a written record
survives a real acquisition needs the **demo machine**, and no fake can stand in
for it.

| Block | Scope | Status | Start | Merge |
| --- | --- | --- | --- | --- |
| 73a | §§1-8 and all 19 tests | not started | | |

- **G1, local.** Full suite, plus a script that builds a labeled and a plain
  mosaic from a real saved dataset and reports each check independently.
- **G2, demo machine.** One short multiposition acquisition, plus one that
  reorders its positions, showing the §8 record present, monotonic, and agreeing
  with the independently logged event-submission order; then a mosaic of each,
  showing `P` on both and no `T`.

Both gates are programs, not runbooks: every step computes, so each reports its
limbs independently, exits nonzero, and owns its log. Run G1 against a
bridge-shaped fake before it is used, and check the pre-fix tree fails test 1 and
test 4 for the stated reasons.

The tool stays `@refuses` for export; a new argument changes nothing in the
exporter and `test_every_registered_tool_has_exactly_one_export_decision`
is unaffected. Say so in the block's notes so it does not read as unaddressed.

## Scope

This block adds an identification overlay on a separate file and a durable
`P<n>`-to-position map. It does not change acquisition order, stage-list
contents, position names, mosaic registration, interpolation, canvas geometry,
TIFF dimensionality, or stage movement. §8 adds one written record beside a
dataset and changes nothing about how an acquisition runs.

It does not close R43, which asks for a segmentation overlay from
`connected_components` — a different writer, a different artifact channel, and
boundaries rather than glyphs — but it leaves `draw_text_labels` in place for
whoever takes that row. A later feature may accept a mosaic manifest plus a label
as a navigation input; until then the agent resolves the recorded mapping and
uses the existing guarded position tools.
