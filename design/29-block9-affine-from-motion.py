"""Measure the pixel->stage affine from commanded motion, on any rig, from puncta.

WHY THIS EXISTS. The landmark check scores a *mosaic*: it asks whether placement
is self-consistent. This asks the prior question -- whether the affine the rig
reports is the affine the hardware actually has -- without building a mosaic,
without touching placement code, and without assuming anything about the
affine's shape.

DELIBERATELY RIG-AGNOSTIC. An earlier pass at M2 decomposed residuals into
"along stage X" and "along stage Y" by knowing that M2's affine happens to be a
90 degree rotation. That reasoning does not survive a rig whose affine is
identity-like, 45 degrees, anisotropic, or reflected. Nothing here knows or
cares: it solves for the 2x2 from data and compares to whatever was reported.

METHOD. For a commanded stage displacement d (um) between two frames, a feature
fixed in the specimen moves in the image by dp (px), related through the
pixel->stage affine. Stacking many (dp, d) pairs and solving least squares gives
a MEASURED 2x2, M, with d = M . dp. Comparing M to the reported affine tests
scale (per column), orientation (angle per column) and handedness (sign of det)
independently.

WHY PUNCTA AND NOT PHASE CORRELATION. On the M2 gate data phase correlation
returned err=1.0 on every pair and "measured travel" that saturated at ~2 um for
commanded moves from 2 to 140 um -- i.e. it silently reported a spurious
near-zero peak rather than failing. The sample is sparse fluorescent puncta with
a *changing* population between frames (blinking, bleaching, focus), which is
exactly the regime where whitening the spectrum destroys the signal. Detecting
puncta and voting on pairwise offsets instead is robust to a partial
correspondence: beads that appear or vanish simply cast no vote. The vote count
is reported so a weak estimate is visible rather than averaged in.

TWO THINGS THIS CANNOT DO, stated up front rather than discovered later:

  1. It cannot separate a stage that under-travels from a pixel size wrong by
     the reciprocal factor -- both give the same um-commanded-per-px. Separating
     them needs an external length standard. For PLACEMENT the distinction is
     irrelevant: a mosaic consumes exactly um-commanded-per-px and nothing else.
  2. It cannot say whether the stage frame is physically right-handed. Every
     coordinate in a dataset lives in that frame.

The correlation sign convention is VERIFIED by a synthetic self-test at startup
rather than assumed, because getting it backwards silently mirrors every result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from ndstorage import Dataset
from scipy import ndimage


def find_puncta(image: np.ndarray, sigma_k: float = 6.0, min_px: int = 2):
    """Centroids of connected pixels a robust sigma above the background."""
    data = image.astype(float)
    background = np.median(data)
    mad = np.median(np.abs(data - background)) * 1.4826
    mask = data > background + sigma_k * max(mad, 1.0)
    labels, count = ndimage.label(mask)
    if count == 0:
        return np.zeros((0, 2))
    centroids = np.array(ndimage.center_of_mass(data, labels, range(1, count + 1)))
    sizes = np.array(ndimage.sum(mask, labels, range(1, count + 1)))
    return centroids[sizes >= min_px]


def vote_translation(first_pts, second_pts, tol=1.5):
    """Dominant translation between two point sets, with its vote count.

    Every (a, b) pair proposes an offset; the offset that the most pairs agree
    on wins. Robust to puncta appearing and disappearing, which is the whole
    reason this exists instead of a correlation.
    """
    if len(first_pts) == 0 or len(second_pts) == 0:
        return None, 0, 0
    offsets = (second_pts[:, None, :] - first_pts[None, :, :]).reshape(-1, 2)
    best_votes, best = -1, None
    for candidate in offsets:
        votes = int((np.abs(offsets - candidate) < tol).all(axis=1).sum())
        if votes > best_votes:
            best_votes, best = votes, candidate
    # Refine: mean of the inliers, not the seed offset.
    inliers = offsets[(np.abs(offsets - best) < tol).all(axis=1)]
    return inliers.mean(axis=0), best_votes, min(len(first_pts), len(second_pts))


def _self_test() -> None:
    rng = np.random.default_rng(0)
    field = np.zeros((160, 160))
    for y, x in rng.integers(20, 140, size=(12, 2)):
        field[y - 1:y + 2, x - 1:x + 2] = 5000.0
    field += rng.normal(200, 5, field.shape)
    for truth in ((7, 0), (0, -5), (11, 6)):
        moved = np.roll(np.roll(field, truth[0], axis=0), truth[1], axis=1)
        got, votes, _ = vote_translation(find_puncta(field), find_puncta(moved))
        if got is None or not np.allclose(got, truth, atol=0.3):
            raise SystemExit(
                f"translation sign self-test FAILED: rolled by {truth}, "
                f"recovered {got}. Refusing to report affines built on it.")
    print("translation sign self-test : PASS "
          "(vote_translation returns the displacement of content)")


def to_xy(row_col: np.ndarray) -> np.ndarray:
    """(row, col) -> (x, y), because MMCore's affine is in image x/y.

    This bit an earlier version of this script: solving with (row, col) fed the
    fit a silently transposed basis, which turned M2's 90 degree rotation into a
    diagonal matrix and reported the rig as REFLECTED. Nothing was wrong with
    the rig. Numpy indexes (row, col); MMCore's PixelSizeAffine acts on (x, y)
    where x is the COLUMN. Convert once, here, and nowhere else.
    """
    return np.array([row_col[1], row_col[0]], dtype=float)


def load(path: Path, axis_fix: dict):
    dataset = Dataset(str(path))
    frames = []
    for position in sorted(dataset.axes["position"], key=str):
        coords = dict(axis_fix, position=position)
        if not dataset.has_image(**coords):
            continue
        metadata = dataset.read_metadata(**coords)
        if "XPosition_um_Intended" not in metadata:
            return None
        frames.append((dataset.read_image(**coords),
                       float(metadata["XPosition_um_Intended"]),
                       float(metadata["YPosition_um_Intended"])))
    return frames


def chain(frames, min_votes: int, min_vote_frac: float):
    """Cumulative content displacement, accumulated frame to frame.

    Consecutive frames are the only ones guaranteed to overlap, so the chain is
    built stepwise and summed. A step that cannot be estimated breaks the chain
    rather than being bridged by a guess.
    """
    steps, cumulative_px, cumulative_um = [], np.zeros(2), np.zeros(2)
    out = [(cumulative_px.copy(), cumulative_um.copy())]
    for i in range(len(frames) - 1):
        a_img, ax, ay = frames[i]
        b_img, bx, by = frames[i + 1]
        offset, votes, possible = vote_translation(find_puncta(a_img),
                                                   find_puncta(b_img))
        weak = (offset is None or votes < min_votes
                or (possible and votes < min_vote_frac * possible))
        if weak:
            steps.append((i, None, votes, possible))
            break
        steps.append((i, offset, votes, possible))
        cumulative_px = cumulative_px + to_xy(offset)
        cumulative_um = cumulative_um + np.array([bx - ax, by - ay])
        out.append((cumulative_px.copy(), cumulative_um.copy()))
    return steps, out


def describe(M, label):
    det = float(np.linalg.det(M))
    scales = [float(np.hypot(M[0, k], M[1, k])) for k in (0, 1)]
    angles = [float(np.degrees(np.arctan2(M[1, k], M[0, k]))) for k in (0, 1)]
    print(f"  {label}")
    print(f"    2x2                 : [[{M[0,0]:+.5f}, {M[0,1]:+.5f}], "
          f"[{M[1,0]:+.5f}, {M[1,1]:+.5f}]]")
    print(f"    column scales um/px : {scales[0]:.5f}  {scales[1]:.5f}")
    print(f"    column angles deg   : {angles[0]:+.2f}  {angles[1]:+.2f}")
    print(f"    determinant         : {det:+.6f}  "
          f"({'REFLECTED' if det < 0 else 'not reflected'})")
    return scales, angles, det


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("datasets", nargs="+")
    parser.add_argument("--axis", action="append", default=[])
    parser.add_argument("--reported-affine", default=None)
    parser.add_argument("--min-votes", type=int, default=3)
    parser.add_argument("--min-vote-frac", type=float, default=0.5,
                        help="reject a step whose winning offset convinces fewer "
                             "than this fraction of the puncta it could have")
    args = parser.parse_args()

    _self_test()

    axis_fix = {}
    for item in args.axis:
        key, _, raw = item.partition("=")
        try:
            axis_fix[key] = int(raw)
        except ValueError:
            axis_fix[key] = raw

    reported = None
    if args.reported_affine:
        payload = json.loads(Path(args.reported_affine).read_text())
        payload = payload.get("payload", payload)
        reported = np.array([[payload["a"], payload["b"]],
                             [payload["c"], payload["d"]]])

    print()
    observations, per_step = [], []
    for raw_path in args.datasets:
        path = Path(raw_path)
        frames = load(path, axis_fix)
        if frames is None or len(frames) < 2:
            print(f"{path.name:22} SKIPPED -- unusable")
            continue
        steps, cum = chain(frames, args.min_votes, args.min_vote_frac)
        ok = [s for s in steps if s[1] is not None]
        broke = len(ok) < len(frames) - 1
        print(f"{path.name:22} {len(ok):2d}/{len(frames)-1} steps tracked"
              + ("   CHAIN BROKE -- fields stop overlapping" if broke else ""))
        for _, offset, votes, possible in ok:
            per_step.append((path.name, to_xy(offset), votes, possible))
        for px, um in cum[1:]:
            observations.append((px, um))

    if len(observations) < 2:
        print("\nnothing measurable")
        return 2

    design = np.array([px for px, _ in observations])
    target = np.array([um for _, um in observations])
    M, *_ = np.linalg.lstsq(design, target, rcond=None)
    M = M.T
    residual = np.abs(design @ M.T - target)

    print(f"\n{'='*72}\nMEASURED AFFINE, pooled ({len(observations)} cumulative "
          f"observations)\n{'='*72}")
    describe(M, "measured  d = M . dp")
    print(f"    residual um         : median {np.median(residual):.3f}  "
          f"p90 {np.percentile(residual,90):.3f}  max {residual.max():.3f}")

    if reported is not None:
        print()
        describe(reported, "reported (config)")
        sign = -1 if np.abs(M + reported).max() < np.abs(M - reported).max() else +1
        aligned = sign * reported
        print(f"\n  sign-aligned comparison ({'-' if sign < 0 else '+'}A) -- the "
              "flip is the motion convention, not an error:")
        for k in (0, 1):
            got = np.hypot(M[0, k], M[1, k])
            want = np.hypot(aligned[0, k], aligned[1, k])
            angle = np.degrees(np.arctan2(M[1, k], M[0, k])
                               - np.arctan2(aligned[1, k], aligned[0, k]))
            angle = (angle + 180) % 360 - 180
            print(f"    column {k}: measured {got:.5f} vs reported {want:.5f} "
                  f"um/px   ratio {got/want:.4f} ({100*(got/want-1):+.1f}%)   "
                  f"angle {angle:+.2f} deg")
        same = np.sign(np.linalg.det(M)) == np.sign(np.linalg.det(reported))
        print(f"    handedness: {'AGREE' if same else 'DISAGREE -- one is mirrored'}")

    print(f"\n{'='*72}\nPER-STEP displacement (quantization shows up here, not "
          f"in the fit)\n{'='*72}")
    print(f"{'dataset':22} {'votes':>6} {'d_row px':>9} {'d_col px':>9} "
          f"{'|dp| px':>9}")
    for name, offset, votes, possible in per_step:
        print(f"{name:22} {votes:3d}/{possible:<3d} {offset[0]:+9.2f} "
              f"{offset[1]:+9.2f} {np.hypot(*offset):9.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
