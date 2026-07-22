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
    identity recorded with the acquisition when available. Never silently apply
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

Placement needs `XPosition_um_Intended` / `YPosition_um_Intended`. These are
present in Microclaw's multi-position acquisitions and absent from a
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

1. a calibration identity recorded with the acquisition, if one is ever added;
2. an explicitly supplied calibration artifact or immutable knowledge-version key;
3. an explicitly confirmed current `objective`/`binning` entry from the
   knowledge base.

It must never *silently* apply the microscope's current cached calibration to a
historical dataset — confirmation is required. Resolving the current alias pins
its immutable version before rasterization; the result never records only the
mutable alias. ROI/camera/timestamp are not required fields today.

If calibration drift turns out to matter — a dataset placed with a stale affine,
or an ROI change that the objective/binning key cannot distinguish — add
ROI/camera/timestamp to the stored entry and promote branch 1 to a required
per-run record. Until then, the immutable knowledge-base version and exact affine
payload carry the identity. `calibration_provenance` is intentionally **not** a
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
- Spiral regression: synthesize non-grid intended-XY positions and assert placement
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
