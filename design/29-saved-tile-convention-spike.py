#!/usr/bin/env python3
"""Read-only convention spike for overlapping design/29 NDTiff tiles.

Uses only NumPy/SciPy.  It never opens Micro-Manager and never writes to the
dataset.  Pixel shifts are the (dy, dx) displacement applied to the destination
tile to align it to the source tile, matching ``solve_affine``'s convention.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
from ndstorage import Dataset
from scipy.ndimage import gaussian_filter, shift as image_shift
from scipy.signal import fftconvolve


STEP_UM = 20.0
MIN_CORRELATION = 0.35


@dataclass
class Pair:
    source: str
    destination: str
    axis: str
    shift_dy_dx: np.ndarray
    peak_ratio: float
    correlation: float

    @property
    def strong(self) -> bool:
        return bool(np.isfinite(self.correlation) and self.correlation >= MIN_CORRELATION
                    and self.peak_ratio >= 1.05)


def _overlap_slices(shape: tuple[int, int], shift: np.ndarray):
    dy, dx = (int(round(float(v))) for v in shift)
    ay = slice(max(0, dy), min(shape[0], shape[0] + dy))
    ax = slice(max(0, dx), min(shape[1], shape[1] + dx))
    by = slice(max(0, -dy), min(shape[0], shape[0] - dy))
    bx = slice(max(0, -dx), min(shape[1], shape[1] - dx))
    return (ay, ax), (by, bx)


def _register(reference: np.ndarray, moved: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Linear (non-wrapping) high-pass cross-correlation and quality metrics."""
    a = reference.astype(float)
    b = moved.astype(float)
    a -= gaussian_filter(a, 8)
    b -= gaussian_filter(b, 8)
    surface = fftconvolve(a, b[::-1, ::-1], mode="full")
    peak = np.unravel_index(int(np.argmax(surface)), surface.shape)
    shift = np.asarray(peak, dtype=float) - np.asarray(b.shape) + 1
    guard = surface.copy()
    y, x = peak
    guard[max(0, y - 4):y + 5, max(0, x - 4):x + 5] = -np.inf
    second = float(np.max(guard))
    peak_ratio = float(surface[peak] / second) if second > 0 else float("inf")

    aligned = image_shift(b, shift, order=1, mode="constant", cval=0)
    (ay, ax), _ = _overlap_slices(a.shape, shift)
    av, bv = a[ay, ax].ravel(), aligned[ay, ax].ravel()
    correlation = (float(np.corrcoef(av, bv)[0, 1])
                   if av.size >= 100 and np.std(av) and np.std(bv) else float("nan"))
    return shift, peak_ratio, correlation


def _load(path: str):
    dataset = Dataset(path)
    records = []
    for coords in dataset.get_image_coordinates_list():
        metadata = dataset.read_metadata(**coords)
        records.append((
            str(coords.get("position", coords)),
            float(metadata["XPosition_um_Intended"]),
            float(metadata["YPosition_um_Intended"]),
            dataset.read_image(**coords),
        ))
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", help="completed NDTiff dataset (read-only)")
    args = parser.parse_args()
    records = _load(args.dataset)
    pairs: list[Pair] = []
    for source, sx, sy, simage in records:
        for destination, dx, dy, dimage in records:
            delta = (dx - sx, dy - sy)
            axis = "X" if np.allclose(delta, (STEP_UM, 0)) else (
                "Y" if np.allclose(delta, (0, STEP_UM)) else None)
            if axis:
                shift, ratio, corr = _register(simage, dimage)
                pairs.append(Pair(source, destination, axis, shift, ratio, corr))

    print(f"dataset: {args.dataset}")
    print(f"frames: {len(records)}; adjacent pairs: {len(pairs)}")
    for pair in pairs:
        quality = "STRONG" if pair.strong else "WEAK"
        print(f"{pair.axis} {pair.source} -> {pair.destination}: "
              f"shift(dy,dx)={pair.shift_dy_dx.tolist()} "
              f"peak_ratio={pair.peak_ratio:.3f} corr={pair.correlation:.3f} {quality}")

    means = {}
    for axis in "XY":
        shifts = np.asarray([p.shift_dy_dx for p in pairs if p.axis == axis])
        means[axis] = shifts.mean(axis=0)
        print(f"{axis} all-pair mean(dy,dx)={means[axis].tolist()} "
              f"scatter_std={shifts.std(axis=0, ddof=1).tolist()}")
    strong = [p for p in pairs if p.strong]
    if len([p for p in strong if p.axis == "X"]) >= 2 and len(
            [p for p in strong if p.axis == "Y"]) >= 2:
        sx = np.mean([p.shift_dy_dx for p in strong if p.axis == "X"], axis=0)
        sy = np.mean([p.shift_dy_dx for p in strong if p.axis == "Y"], axis=0)
        px_per_um = np.array([[sx[1], sy[1]], [sx[0], sy[0]]]) / STEP_UM
        affine = np.linalg.inv(px_per_um)
        pixel_size = float(np.sqrt(abs(np.linalg.det(affine))))
        print("measured [[a,b],[c,d]]:", affine.tolist())
        print(f"area-equivalent pixel size: {pixel_size:.6f} um/px")
        plausible = 0.08 <= pixel_size <= 0.30
        print("optics sanity: " + ("PLAUSIBLE" if plausible else "IMPLAUSIBLE")
              + " for a 16 um-pixel DU897 and versus M2's 0.127 um/px")
        print("VERDICT: PASS — saved data determines the convention" if plausible else
              "VERDICT: FAIL — correlations imply an implausible pixel size")
    else:
        print("measured [[a,b],[c,d]]: unavailable (insufficient strong pairs on both axes)")
        print("VERDICT: FAIL — saved data cannot disambiguate signs/order: adjacent-tile "
              "correlations are weak or inconsistent, especially the marginal Y overlap.")
        print("PROPOSAL ONLY: snap a contrasty field, move +X by about one quarter of the "
              "smaller FOV and snap, restore; repeat for +Y. No move/exposure was made.")


if __name__ == "__main__":
    main()
