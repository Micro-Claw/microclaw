"""Pure geometry for deterministic stage-coordinate mosaics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import numpy as np

from microclaw.calibration import StageCameraAffine


@dataclass(frozen=True)
class MosaicGeometry:
    """A pixel-to-stage calibration and the requested output sampling."""

    affine: StageCameraAffine
    output_pixel_size_um: float

    def __post_init__(self) -> None:
        coefficients = (self.affine.a, self.affine.b, self.affine.c, self.affine.d)
        if not all(math.isfinite(value) for value in coefficients):
            raise ValueError("Mosaic affine coefficients must all be finite")
        determinant = self.affine.a * self.affine.d - self.affine.b * self.affine.c
        scale = max(abs(value) for value in coefficients)
        if not math.isfinite(determinant) or abs(determinant) <= 1e-12 * max(1.0, scale * scale):
            raise ValueError("Mosaic affine must be nonsingular")
        if not math.isfinite(self.output_pixel_size_um) or self.output_pixel_size_um <= 0:
            raise ValueError("output_pixel_size_um must be finite and positive")


@dataclass(frozen=True)
class MosaicFrameShape:
    """Cheap first-pass shape/dtype metadata for a dataset-backed frame."""

    shape: tuple[int, int]
    dtype: np.dtype


def assemble_stage_coordinate_mosaic(
    frames: Iterable[tuple[np.ndarray | MosaicFrameShape, float, float]],
    geometry: MosaicGeometry,
) -> dict:
    """Place image pixels by their optical-centre stage coordinates.

    Arrays use ``(row, col) == (y_px, x_px)`` and the affine is called as
    ``px_to_um(dx_px, dy_px)``. Output is +X right and +Y down. Source pixels are
    sampled by inverse mapping: each output-cell centre in a tile's
    transformed bounding box is mapped through the inverse affine to a source
    coordinate, then nearest-neighbour sampled. Exact half source coordinates
    round toward increasing source indices (``floor(value + 0.5)``). Canvas bounds
    include transformed pixel centres, and cells outside every transformed tile
    are zero. Later frames overwrite earlier frames. This is a deterministic
    display convention, not image alignment or object matching.

    The input must be deterministic and re-iterable: it is read once for bounds
    and again for rasterization, allowing dataset-backed callers to avoid holding
    source tiles in memory. The output image and coverage/count arrays are the
    large allocations. All frames must have the same two-dimensional dtype.
    """
    frame_descriptors: list[tuple[tuple[int, int], float, float]] = []
    bounds: list[tuple[float, float, float, float]] = []
    dtype = None
    for pixels, intended_x, intended_y in frames:
        if isinstance(pixels, MosaicFrameShape):
            image_shape = pixels.shape
            image_dtype = np.dtype(pixels.dtype)
        else:
            image = np.asarray(pixels)
            image_shape = image.shape
            image_dtype = image.dtype
        if len(image_shape) != 2 or math.prod(image_shape) == 0:
            raise ValueError("Mosaic frames must be nonempty two-dimensional arrays")
        if dtype is None:
            dtype = image_dtype
        elif image_dtype != dtype:
            raise ValueError("Mosaic frames must have one common dtype")
        x = float(intended_x)
        y = float(intended_y)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("Intended stage coordinates must be finite")
        height, width = image_shape
        # Bounds are over pixel centres. Even dimensions therefore straddle the
        # optical centre symmetrically at half-integer pixel displacements.
        corners = []
        for dy in (-(height - 1) / 2, (height - 1) / 2):
            for dx in (-(width - 1) / 2, (width - 1) / 2):
                sx, sy = geometry.affine.px_to_um(dx, dy)
                corners.append((x + sx, y + sy))
        bounds.append((min(p[0] for p in corners), max(p[0] for p in corners),
                       min(p[1] for p in corners), max(p[1] for p in corners)))
        frame_descriptors.append((image_shape, x, y))
    if not frame_descriptors:
        raise ValueError("At least one frame is required")

    origin_x = min(item[0] for item in bounds)
    max_x = max(item[1] for item in bounds)
    origin_y = min(item[2] for item in bounds)
    max_y = max(item[3] for item in bounds)
    sample = geometry.output_pixel_size_um
    # Endpoints are inclusive because bounds describe pixel centres.
    width = int(math.ceil((max_x - origin_x) / sample - 1e-12)) + 1
    height = int(math.ceil((max_y - origin_y) / sample - 1e-12)) + 1
    mosaic = np.zeros((height, width), dtype=dtype)
    coverage_count = np.zeros((height, width), dtype=np.uint16)

    determinant = geometry.affine.a * geometry.affine.d - geometry.affine.b * geometry.affine.c
    inverse = np.array(
        [[geometry.affine.d, -geometry.affine.b],
         [-geometry.affine.c, geometry.affine.a]],
        dtype=np.float64,
    ) / determinant

    rasterized = 0
    rasterized_output_samples = 0
    for frame_index, (pixels, centre_x, centre_y) in enumerate(frames):
        if frame_index >= len(frame_descriptors):
            raise ValueError("Frame iterable changed between bounds and raster passes")
        image = np.asarray(pixels)
        expected_shape, expected_x, expected_y = frame_descriptors[frame_index]
        if (image.shape != expected_shape or image.dtype != dtype
                or float(centre_x) != expected_x or float(centre_y) != expected_y):
            raise ValueError("Frame iterable changed between bounds and raster passes")
        image_height, image_width = image.shape
        tile_min_x, tile_max_x, tile_min_y, tile_max_y = bounds[frame_index]
        # Include the output cells whose centres are nearest to the transformed
        # endpoint centres. This preserves the complete edge sample when a tile's
        # lattice has a fractional phase relative to the mosaic lattice.
        first_col = max(0, math.floor((tile_min_x - origin_x) / sample + 0.5))
        last_col = min(width - 1, math.floor((tile_max_x - origin_x) / sample + 0.5))
        first_row = max(0, math.floor((tile_min_y - origin_y) / sample + 0.5))
        last_row = min(height - 1, math.floor((tile_max_y - origin_y) / sample + 0.5))

        output_rows, output_cols = np.meshgrid(
            np.arange(first_row, last_row + 1),
            np.arange(first_col, last_col + 1),
            indexing="ij",
        )
        stage_dx = origin_x + output_cols * sample - expected_x
        stage_dy = origin_y + output_rows * sample - expected_y
        source_dx = inverse[0, 0] * stage_dx + inverse[0, 1] * stage_dy
        source_dy = inverse[1, 0] * stage_dx + inverse[1, 1] * stage_dy
        source_cols = np.floor(source_dx + (image_width - 1) / 2 + 0.5).astype(np.int64)
        source_rows = np.floor(source_dy + (image_height - 1) / 2 + 0.5).astype(np.int64)
        valid = (
            (source_rows >= 0) & (source_rows < image_height)
            & (source_cols >= 0) & (source_cols < image_width)
        )
        target_rows = output_rows[valid]
        target_cols = output_cols[valid]
        mosaic[target_rows, target_cols] = image[source_rows[valid], source_cols[valid]]
        tile_coverage = coverage_count[first_row:last_row + 1, first_col:last_col + 1]
        np.add(tile_coverage, valid, out=tile_coverage, casting="unsafe")
        rasterized_output_samples += int(np.count_nonzero(valid))
        rasterized += 1
    if rasterized != len(frame_descriptors):
        raise ValueError("frames must be a deterministic re-iterable, not a one-shot iterator")

    coverage = coverage_count != 0
    overlap_pixels = int(np.count_nonzero(coverage_count > 1))
    return {
        "mosaic": mosaic,
        "origin_um": [origin_x, origin_y],
        "extent_um": [max_x - origin_x, max_y - origin_y],
        "output_basis_um": [[sample, 0.0], [0.0, sample]],
        "coverage_mask": coverage,
        "overlap_statistics": {
            "covered_pixels": int(np.count_nonzero(coverage)),
            "uncovered_pixels": int(coverage.size - np.count_nonzero(coverage)),
            "overlap_pixels": overlap_pixels,
            "maximum_coverage": int(coverage_count.max()),
            "rasterized_output_sample_count": rasterized_output_samples,
        },
    }
