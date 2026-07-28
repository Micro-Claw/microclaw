#!/usr/bin/env python3
"""Read-only convention spike for overlapping design/29 NDTiff tiles.

Uses only NumPy/SciPy.  It never opens Micro-Manager and never writes to the
dataset.  Pixel shifts are the (dy, dx) displacement applied to the destination
tile to align it to the source tile, matching ``solve_affine``'s convention.

This measures the convention from tiles ALREADY on disk, at zero dose.  When it
cannot -- because a dataset is dark, has no adjacent pairs, or (as on run_a_2)
resolves only one column -- **do not write a move/snap spike to finish the job.**
Micro-Manager already ships that measurement in
``org.micromanager.internal.pixelcalibrator``: ``AutomaticCalibrationThread``
moves the stage, snaps, and cross-correlates, and ``CalibrationThread.result_``
is a full ``java.awt.geom.AffineTransform``.  Run MM's pixel calibrator on a
contrast-rich field instead, then read the result back with
``29-mm-pixel-affine-probe.py``.  See design/29's "Do not build a move/snap
affine spike" section.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
from ndstorage import Dataset
from scipy.signal import fftconvolve


STEP_UM = 20.0
MIN_OVERLAP_FRACTION = 0.25
MIN_CORRELATION = 0.60
MIN_PEAK_RATIO = 1.10
MIN_STRUCTURED_FRACTION = 0.005


@dataclass
class Pair:
    source: str
    destination: str
    axis: str
    shift_dy_dx: np.ndarray
    peak_ratio: float
    correlation: float
    overlap_fraction: float

    @property
    def strong(self) -> bool:
        return bool(np.isfinite(self.correlation) and self.correlation >= MIN_CORRELATION
                    and self.peak_ratio >= MIN_PEAK_RATIO
                    and self.overlap_fraction >= MIN_OVERLAP_FRACTION)


def _register(reference: np.ndarray, moved: np.ndarray):
    """Linear overlap-normalized cross-correlation and quality metrics.

    At every lag this subtracts the two means and divides by both standard
    deviations over exactly the pixels that overlap. Candidates retaining less
    than ``MIN_OVERLAP_FRACTION`` of a frame are excluded before peak selection.
    """
    a = reference.astype(float)
    b = moved.astype(float)
    # Scaling first keeps the FFT sum-of-squares arithmetic well conditioned.
    offset = min(float(np.median(a)), float(np.median(b)))
    scale = max(float(np.std(a)), float(np.std(b)), 1.0)
    a, b = (a - offset) / scale, (b - offset) / scale
    oa, ob = np.ones_like(a), np.ones_like(b)
    reverse = lambda value: value[::-1, ::-1]
    count = fftconvolve(oa, reverse(ob), mode="full")
    sum_ab = fftconvolve(a, reverse(b), mode="full")
    sum_a = fftconvolve(a, reverse(ob), mode="full")
    sum_b = fftconvolve(oa, reverse(b), mode="full")
    sum_a2 = fftconvolve(a * a, reverse(ob), mode="full")
    sum_b2 = fftconvolve(oa, reverse(b * b), mode="full")
    with np.errstate(invalid="ignore", divide="ignore"):
        numerator = sum_ab - sum_a * sum_b / count
        variance_a = np.maximum(0, sum_a2 - sum_a * sum_a / count)
        variance_b = np.maximum(0, sum_b2 - sum_b * sum_b / count)
        surface = numerator / np.sqrt(variance_a * variance_b)
    overlap = count / float(a.size)
    surface[(overlap < MIN_OVERLAP_FRACTION) | ~np.isfinite(surface)] = -np.inf
    peak = np.unravel_index(int(np.argmax(surface)), surface.shape)
    shift = np.asarray(peak, dtype=float) - np.asarray(b.shape) + 1
    guard = surface.copy()
    y, x = peak
    guard[max(0, y - 4):y + 5, max(0, x - 4):x + 5] = -np.inf
    second = float(np.max(guard))
    correlation = float(surface[peak])
    peak_ratio = correlation / second if second > 0 else float("inf")
    return shift, peak_ratio, correlation, float(overlap[peak])


def _load(path: str):
    dataset = Dataset(path)
    records = []
    for coords in dataset.get_image_coordinates_list():
        metadata = dataset.read_metadata(**coords)
        records.append((
            str(coords.get("position", coords)),
            float(metadata["XPosition_um_Intended"]),
            float(metadata["YPosition_um_Intended"]),
            dataset.read_image(**coords), metadata,
        ))
    return records


def _signal_summary(records):
    pixels = np.concatenate([record[3].ravel() for record in records])
    background = float(np.median(pixels))
    mad = float(np.median(np.abs(pixels - background)))
    threshold = background + 5 * max(mad, 1.0)
    structured = float(np.mean(pixels > threshold))
    lasers = sorted({str(record[4].get("Cobolt561-Laser", "<?>")) for record in records})
    return background, mad, structured, int(pixels.min()), int(pixels.max()), lasers


def _axis_cluster(pairs: list[Pair], axis: str):
    axis_pairs = [pair for pair in pairs if pair.axis == axis]
    strong = [pair for pair in axis_pairs if pair.strong]
    if not strong:
        return axis_pairs, [], None
    shifts = np.asarray([pair.shift_dy_dx for pair in strong])
    seed = np.median(shifts, axis=0)
    # A single physical displacement should agree in direction. Tolerances allow
    # the observed X bimodality while excluding unrelated peaks across the frame.
    members = [pair for pair in strong
               if abs(pair.shift_dy_dx[0] - seed[0]) <= 30
               and abs(pair.shift_dy_dx[1] - seed[1]) <= 15]
    required = max(2, int(np.ceil(0.6 * len(axis_pairs))))
    return axis_pairs, members, required


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", help="completed NDTiff dataset (read-only)")
    args = parser.parse_args()
    records = _load(args.dataset)
    pairs: list[Pair] = []
    background, mad, structured, minimum, maximum, lasers = _signal_summary(records)
    print(f"dataset: {args.dataset}")
    print(f"frames: {len(records)}; intensity min/max={minimum}/{maximum} "
          f"background={background:.1f} MAD={mad:.1f} "
          f"fraction>background+5*MAD={structured:.4%}; Cobolt561={lasers}")
    actual_xy_keys = sorted({key for record in records for key in record[4]
                             if "Position" in key and "Intended" not in key
                             and (key.startswith("X") or key.startswith("Y"))})
    print("actual stage XY metadata:", actual_xy_keys or "ABSENT (intended XY only)")
    if structured < MIN_STRUCTURED_FRACTION:
        print("DATASET VERDICT: DARK/SIGNAL-FREE — insufficient structure for registration; "
              "no convention verdict issued")
        return

    for source, sx, sy, simage, _ in records:
        for destination, dx, dy, dimage, _ in records:
            delta = (dx - sx, dy - sy)
            axis = "X" if np.allclose(delta, (STEP_UM, 0)) else (
                "Y" if np.allclose(delta, (0, STEP_UM)) else None)
            if axis:
                shift, ratio, corr, overlap = _register(simage, dimage)
                pairs.append(Pair(source, destination, axis, shift, ratio, corr, overlap))

    print(f"frames: {len(records)}; adjacent pairs: {len(pairs)}")
    for pair in pairs:
        quality = "STRONG" if pair.strong else "WEAK"
        print(f"{pair.axis} {pair.source} -> {pair.destination}: "
              f"shift(dy,dx)={pair.shift_dy_dx.tolist()} "
              f"overlap={pair.overlap_fraction:.3f} peak_ratio={pair.peak_ratio:.3f} "
              f"NCC={pair.correlation:.3f} {quality}")

    determined = {}
    for axis in "XY":
        axis_pairs, cluster, required = _axis_cluster(pairs, axis)
        if required is None or len(cluster) < required:
            print(f"{axis} AXIS VERDICT: UNRESOLVED — cluster={len(cluster)}/{len(axis_pairs)} "
                  f"pairs (need {required or 2}); no 2x2 column or scale reported")
            determined[axis] = None
            continue
        shifts = np.asarray([pair.shift_dy_dx for pair in cluster])
        median = np.median(shifts, axis=0)
        q25, q75 = np.percentile(shifts, [25, 75], axis=0)
        iqr = q75 - q25
        magnitudes = np.hypot(shifts[:, 0], shifts[:, 1])
        scales = STEP_UM / magnitudes
        determined[axis] = median
        print(f"{axis} AXIS VERDICT: DETERMINED — cluster={len(cluster)}/{len(axis_pairs)}; "
              f"median(dy,dx)={median.tolist()} IQR={iqr.tolist()}; "
              f"implied scale range={scales.min():.4f}..{scales.max():.4f} um/px")

    if all(determined.get(axis) is not None for axis in "XY"):
        sx, sy = determined["X"], determined["Y"]
        px_per_um = np.array([[sx[1], sy[1]], [sx[0], sy[0]]]) / STEP_UM
        print("measured [[a,b],[c,d]]:", np.linalg.inv(px_per_um).tolist())
        print("DATASET VERDICT: PASS — both 2x2 columns are determined")
    elif any(determined.get(axis) is not None for axis in "XY"):
        print("measured [[a,b],[c,d]]: unavailable until both columns are determined")
        print("DATASET VERDICT: PARTIAL — one stage-axis convention is determined")
    else:
        print("measured [[a,b],[c,d]]: unavailable")
        print("DATASET VERDICT: UNRESOLVED — neither stage-axis convention is determined")


if __name__ == "__main__":
    main()
