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


def assemble_stage_coordinate_mosaic(
    frames: Iterable[tuple[np.ndarray, float, float]], geometry: MosaicGeometry
) -> dict:
    """Place image pixels by their optical-centre stage coordinates.

    Arrays use ``(row, col) == (y_px, x_px)`` and the affine is called as
    ``px_to_um(dx_px, dy_px)``. Output is +X right and +Y down. Each source pixel
    centre is forward-sampled into its nearest output cell; exact half cells round
    toward increasing output indices. Canvas bounds include transformed pixel
    centres, and uncovered pixels are zero. Within a frame, later row-major source
    pixels overwrite earlier ones; later frames overwrite earlier frames. This is
    a deterministic display convention, not image alignment or object matching.

    The input must be deterministic and re-iterable: it is read once for bounds
    and again for rasterization, allowing dataset-backed callers to avoid holding
    source tiles in memory. The output image and coverage/count arrays are the
    large allocations. All frames must have the same two-dimensional dtype.
    """
    frame_descriptors: list[tuple[tuple[int, int], float, float]] = []
    bounds: list[tuple[float, float, float, float]] = []
    dtype = None
    for pixels, intended_x, intended_y in frames:
        image = np.asarray(pixels)
        if image.ndim != 2 or image.size == 0:
            raise ValueError("Mosaic frames must be nonempty two-dimensional arrays")
        if dtype is None:
            dtype = image.dtype
        elif image.dtype != dtype:
            raise ValueError("Mosaic frames must have one common dtype")
        x = float(intended_x)
        y = float(intended_y)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("Intended stage coordinates must be finite")
        height, width = image.shape
        # Bounds are over pixel centres. Even dimensions therefore straddle the
        # optical centre symmetrically at half-integer pixel displacements.
        corners = []
        for dy in (-(height - 1) / 2, (height - 1) / 2):
            for dx in (-(width - 1) / 2, (width - 1) / 2):
                sx, sy = geometry.affine.px_to_um(dx, dy)
                corners.append((x + sx, y + sy))
        bounds.append((min(p[0] for p in corners), max(p[0] for p in corners),
                       min(p[1] for p in corners), max(p[1] for p in corners)))
        frame_descriptors.append((image.shape, x, y))
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

    rasterized = 0
    for frame_index, (pixels, centre_x, centre_y) in enumerate(frames):
        if frame_index >= len(frame_descriptors):
            raise ValueError("Frame iterable changed between bounds and raster passes")
        image = np.asarray(pixels)
        expected_shape, expected_x, expected_y = frame_descriptors[frame_index]
        if image.shape != expected_shape or float(centre_x) != expected_x or float(centre_y) != expected_y:
            raise ValueError("Frame iterable changed between bounds and raster passes")
        image_height, image_width = image.shape
        for row in range(image_height):
            dy = row - (image_height - 1) / 2
            for col in range(image_width):
                dx = col - (image_width - 1) / 2
                stage_dx, stage_dy = geometry.affine.px_to_um(dx, dy)
                out_col = math.floor((centre_x + stage_dx - origin_x) / sample + 0.5)
                out_row = math.floor((centre_y + stage_dy - origin_y) / sample + 0.5)
                mosaic[out_row, out_col] = image[row, col]
                if coverage_count[out_row, out_col] < np.iinfo(np.uint16).max:
                    coverage_count[out_row, out_col] += 1
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
            "source_sample_count": int(coverage_count.sum(dtype=np.uint64)),
        },
    }
