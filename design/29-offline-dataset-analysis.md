# design/29 — offline analysis of saved datasets (stitch / count / orient)

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
  needed the user to open the file and report. A read-side stitch would have made
  each iteration a zero-dose recompute.
- **`export_dataset_as_tiff` — the one tool that touches saved data — is broken**
  for multi-position datasets (design/28 Finding 3, `KeyError: 'position'`), so
  even dumping the tiles for external analysis failed.

The dose asymmetry is the core problem: analysis is cheap (CPU on bytes already
on disk) but is currently priced at the cost of a full re-acquisition.

## The enabling fact

The pixels **and** the per-tile stage XY are already saved. `ndstorage.Dataset`
exposes both without any re-imaging:

- `dataset.axes` — ordered coordinate values per axis (`position`, `channel`, …).
- `dataset.read_image(**coords)` / `dataset.has_image(**coords)` — pixels.
- `dataset.read_metadata(**coords)` — per-image metadata, which carries
  `XPosition_um_Intended` / `YPosition_um_Intended` for every multi-position
  acquisition (see [[hook-metadata-has-xy]] / design/23 F2).

So the exact placement the live stitcher hook did — accumulate each tile's pixels
keyed by stage XY at a known pixel size — is fully reconstructable from disk. The
live hook and an offline reader are the *same computation* over the *same
inputs*; only the source of the frames differs. That means the placement,
counting, and orientation logic should live in **one shared module** that both
the hook and the offline tools call, not be re-authored per session as
claude-generated hook bodies.

## Proposed shape

### 1. A shared placement/segmentation module — `microclaw/mosaic.py`

Extract the logic the session's hooks reinvented into importable functions, so
"live" and "offline" differ only in where frames come from.

```python
# microclaw/mosaic.py
from dataclasses import dataclass
import numpy as np

@dataclass
class TilePlacement:
    pixel_size_um: float
    rot90_k: int = 0          # per-tile CCW quarter-turns (camera->stage axis align)
    flip_after: bool = False  # horizontal flip after rotation
    # The rot90_k=1, flip_after=True that session 20260717 verified is the
    # DEFAULT for this rig's SmarAct-2D + Hamamatsu path — but it is an optical
    # property, so the real fix is to MEASURE it (calibrate_stage_to_camera,
    # design/28 F4) and cache it, not hard-code it here.

    def orient(self, tile: np.ndarray) -> np.ndarray:
        t = np.rot90(tile, k=self.rot90_k)
        return np.fliplr(t) if self.flip_after else t

def assemble_mosaic(tiles_xy, placement: TilePlacement):
    """tiles_xy: iterable of (pixels, x_um, y_um). Returns (mosaic, origin_xy_um).

    Places each oriented tile onto a common canvas by stage XY at
    placement.pixel_size_um. Later tiles overwrite earlier in overlap (display
    convention — matches the session stitcher). Canvas size is derived from the
    XY extent, NOT the tile count, so it works for 36 or 2500 tiles alike.
    """
    ...

def count_cells(mosaic, pixel_size_um, min_area_um2=20.0, max_area_um2=1000.0,
                snr_min=3.0):
    """Threshold above (bg + k·noise), label connected components, keep those
    whose area in µm² falls in [min_area_um2, max_area_um2]. Counts across the
    STITCHED mosaic so a cell straddling several tiles is counted once — the
    property the user asked for. Reuses image_analysis.snr for the gate so there
    is ONE snr definition (design/23 F7)."""
    from scipy import ndimage
    ...
```

### 2. Read-side tools that source frames from a saved dataset

```python
# tools.py
def stitch_saved_dataset(ctrl, guard, dataset_path, output_path,
                         channel=None, rot90_k=None, flip_after=None,
                         pixel_size_um=None) -> dict:
    """Stitch a mosaic from a dataset ALREADY on disk — no new exposures.

    Reads each stored image's pixels + intended stage XY (read_metadata) and
    calls mosaic.assemble_mosaic. rot90_k/flip_after default to the cached
    stage-camera affine's orientation if present, else must be supplied. Writes
    a 16-bit TIFF and returns size/extent + the artifact path.
    """
    dataset = Dataset(guard.resolve_in_workspace(dataset_path))
    coords_iter = _iter_present_coords(dataset, fix_channel=channel)  # design/28 F3 pattern
    def frames():
        for coords in coords_iter:
            md = dataset.read_metadata(**coords)
            yield (dataset.read_image(**coords),
                   float(md["XPosition_um_Intended"]),
                   float(md["YPosition_um_Intended"]))
    ...

def count_cells_in_saved_dataset(ctrl, guard, dataset_path, channel=None,
                                 pixel_size_um=None, min_area_um2=20.0,
                                 max_area_um2=1000.0, snr_min=3.0) -> dict:
    """Assemble the mosaic offline and run mosaic.count_cells — answers
    'how many cells' for ANY saved scan, including the 900 µm ones that were
    never counted, at zero additional dose."""
    ...
```

Both reuse the `_iter_present_coords` helper that design/28 Finding 3 introduces
for the exporter fix (iterate real coordinate values + `has_image` guard), so the
exporter fix and this feature share the traversal and land together.

### 3. Metadata-XY dependency and its fallback

Placement needs `XPosition_um_Intended` / `YPosition_um_Intended`. Per
[[hook-metadata-has-xy]] these are present for every *multi-position*
acquisition and absent only for single-position ones — which is exactly the case
that doesn't need stitching, so the dependency is safe for the mosaic path. Guard
it explicitly and fail loud with the reason rather than placing tiles at the
origin:

```python
if "XPosition_um_Intended" not in md:
    return {"error": "Dataset has no intended-XY metadata (single-position "
                     "acquisition?); cannot place tiles. Nothing was re-imaged."}
```

A secondary fallback — reconstructing XY from the `row`/`column` tile axes plus a
known `step_um` — is possible but should be a separate, explicitly-requested mode,
not a silent default, because it reintroduces exactly the row/column↔stage-axis
ambiguity the session spent four re-scans resolving.

## Memory / scale note (the 2500-tile case)

The 900 µm mosaic is 8582×8578 px ≈ 74 M px → ~140 MB as uint16. `assemble_mosaic`
holds one canvas of that size, which is fine; the pitfall is holding all 2500
tiles in memory *at once*. Source frames lazily (the `frames()` generator above)
so only one tile plus the canvas is resident. This is a real advantage over the
live hook, which accumulated every tile's pixels before writing.

## Testing

- Unit: synthesize a tiny NDTiff (or mock `Dataset`) with 4 tiles at known XY and
  a planted object straddling a seam; assert `assemble_mosaic` places them and
  `count_cells` returns 1, not 2 — the cross-seam dedup is the whole point.
- Orientation: assert `TilePlacement.orient` with `rot90_k=1, flip_after=True`
  matches the transform the session verified, and that dims swap on odd `k`.
- Exporter regression: the multi-position dataset that raised `KeyError:
  'position'` (design/28 F3) now traverses via `_iter_present_coords` and both
  the exporter and `stitch_saved_dataset` read it cleanly. These are MM-adjacent
  but the traversal + placement + counting are pure-numpy and testable off-rig.

## Relationship to the other findings

- **Builds on design/28 Finding 3** (`_iter_present_coords` traversal) — do that
  first; this reuses it.
- **Retires the live-only constraint** that made design/28 Findings 1–2 (focus)
  so costly to diagnose: an offline stitch lets an orientation/quality hypothesis
  be checked without re-dosing the sample.
- **Does not replace live hooks** — adaptive/gated acquisition ([[design-27-ghost-exposures]])
  still needs to run during the scan. This is strictly for analysis of what was
  already captured.
