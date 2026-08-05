"""plus_mosaic_stitcher — stage-coordinate mosaic hook.

Accumulates each incoming tile's raw pixels onto a common-coordinate canvas,
placed by the tile's INTENDED stage XY (from metadata) and a known pixel size.
Once all n_tiles have arrived, emits the assembled 16-bit mosaic as a single
EmitArtifact (the trusted parent writes + hashes it in the run artifact dir).

Stage->canvas mapping: stage +X increases canvas column; stage +Y (stage is
Y-up) DECREASES canvas row (images are Y-down). No rotation is applied; this
matches an unrotated camera whose pixel axes are parallel to the stage axes.
Overlapping pixels take the most recently placed tile (display mosaic, not
blended), matching the saved mosaic_stitcher semantics.
"""

import numpy as np
from microclaw.hook_decisions import HookResult, ContinueSurvey, EmitArtifact


class PlusMosaicStitcher:
    def __init__(self, pixel_size_um=0.1056, n_tiles=5, out_name="mosaic.tiff"):
        self.pixel_size_um = float(pixel_size_um)
        self.n_tiles = int(n_tiles)
        self.out_name = str(out_name)
        # per-tile records: (x_um, y_um, image) collected until all arrive
        self._tiles = []

    def analyze_frame(self, image: np.ndarray, metadata: dict):
        # Identify the tile's intended stage position; fall back gracefully.
        x = metadata.get("XPosition_um_Intended")
        y = metadata.get("YPosition_um_Intended")
        name = metadata.get("PositionName")
        if name is None:
            name = metadata.get("Axes", {}).get("position")

        measurements = {
            "position": name,
            "x_um_intended": x,
            "y_um_intended": y,
            "tile_index": len(self._tiles),
            "tile_shape": list(image.shape),
        }

        # If a tile lacks intended coordinates we cannot place it; record and
        # keep going rather than guessing a location.
        if x is None or y is None:
            measurements["status"] = "unplaceable_no_intended_xy"
            return HookResult(measurements, (ContinueSurvey(),))

        self._tiles.append((float(x), float(y), np.asarray(image)))

        # Not all tiles in yet: just continue.
        if len(self._tiles) < self.n_tiles:
            return HookResult(measurements, (ContinueSurvey(),))

        # All tiles present -> assemble the mosaic.
        mosaic, extent = self._assemble()
        measurements["status"] = "mosaic_emitted"
        measurements["mosaic_shape"] = list(mosaic.shape)
        measurements["extent_um"] = extent
        return HookResult(
            measurements,
            (EmitArtifact(mosaic, self.out_name), ContinueSurvey()),
        )

    def _assemble(self):
        px = self.pixel_size_um
        # Tile pixel dimensions (assume uniform ROI across tiles).
        h, w = self._tiles[0][2].shape[:2]

        # Convert each tile's stage XY to a top-left pixel coordinate on a
        # shared canvas. Column = +X; row = -Y (stage Y-up vs image Y-down).
        # Work in "tile-center pixel" space first, then offset to top-left.
        cols_center = []
        rows_center = []
        for (x, y, _img) in self._tiles:
            col_c = x / px
            row_c = -y / px
            cols_center.append(col_c)
            rows_center.append(row_c)

        # Top-left of each tile.
        lefts = [c - w / 2.0 for c in cols_center]
        tops = [r - h / 2.0 for r in rows_center]

        min_left = min(lefts)
        min_top = min(tops)
        # Integer placement relative to canvas origin.
        placements = []
        for (left, top) in zip(lefts, tops):
            col0 = int(round(left - min_left))
            row0 = int(round(top - min_top))
            placements.append((row0, col0))

        max_row = max(r0 + h for (r0, _c0) in placements)
        max_col = max(c0 + w for (_r0, c0) in placements)

        dtype = self._tiles[0][2].dtype
        canvas = np.zeros((max_row, max_col), dtype=dtype)

        # Place in arrival order; later tiles overwrite overlaps.
        for (row0, col0), (_x, _y, img) in zip(placements, self._tiles):
            canvas[row0:row0 + h, col0:col0 + w] = img[:h, :w]

        # Ensure 16-bit output.
        if canvas.dtype != np.uint16:
            canvas = np.clip(canvas, 0, 65535).astype(np.uint16)

        extent = {
            "rows": int(max_row),
            "cols": int(max_col),
            "pixel_size_um": px,
        }
        return canvas, extent
