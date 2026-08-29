"""Survey per-frame NDTiff metadata size across every dataset we have kept.

The band fix (design/60, after closure) rests on one empirical claim: the
per-frame metadata size `m` is **unstable between configurations but stable
within one**, so a disclosure can measure it from a dataset the rig already
wrote instead of modelling it with a constant. Two datasets are not evidence for
that. This reads every `NDTiff.index` in the evidence archive.

Why it matters: the shipped disclosure tests `n > MAX_FILE_SIZE // (w*h*bpp)` --
the RAW pixel bound. A file also holds metadata and an IFD, so it really fills at
`MAX_FILE_SIZE // (w*h*bpp + m)`. Runs between the two cross a 4 GiB boundary
undisclosed, and the width of that band is exactly `m / (w*h*bpp)` -- negligible
on a full chip, large on the small ROIs SMLM uses to go fast.

    python design/60-metadata-size-survey.py [archive_root]
"""
from __future__ import annotations

import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from ndstorage.ndtiff_index import read_ndtiff_index

MAX_FILE_SIZE = 2**32
DEFAULT_ROOT = Path.home() / "Documents/Documents - Beyonce/Projects/Micro-Claw"
# Directory names carry the rig: 43a-m2, 43b-m5, 41-block41c-demo, 56-nikon...
RIG = re.compile(r"(?<![a-z0-9])(m2|m5|demo|nikon)(?![a-z0-9])", re.I)
BYTES_PER_PIXEL = {0: 1, 2: 1}          # everything else in NDTiff is 16-bit


def rig_of(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.as_posix()
    found = RIG.findall(rel)
    return found[0].lower() if found else "unknown"


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROOT
    indexes = sorted(root.rglob("NDTiff.index"))
    print(f"{len(indexes)} datasets under {root}\n")

    groups = defaultdict(list)          # (rig, w, h, bpp) -> [md_length, ...]
    per_dataset = []
    unreadable = []
    for path in indexes:
        try:
            index = read_ndtiff_index(path.read_bytes(), verbose=False)
        except Exception as exc:        # noqa: BLE001 - counted, not hidden
            unreadable.append((path, f"{type(exc).__name__}: {exc}"))
            continue
        if not index:
            unreadable.append((path, "no entries"))
            continue
        rig = rig_of(path, root)
        mds, shapes = [], set()
        for e in index.values():
            mds.append(e.metadata_length)
            shapes.add((e.image_width, e.image_height,
                        BYTES_PER_PIXEL.get(e.pixel_type, 2)))
        for shape in shapes:
            groups[(rig, *shape)].extend(mds if len(shapes) == 1 else [])
        w, h, bpp = sorted(shapes)[0]
        per_dataset.append({
            "rig": rig, "n": len(mds), "w": w, "h": h, "bpp": bpp,
            "mean": statistics.mean(mds), "min": min(mds), "max": max(mds),
            "spread": (max(mds) - min(mds)) / statistics.mean(mds) if mds else 0,
            "shapes": len(shapes), "path": path,
        })

    # ---- within a dataset: how stable is m? --------------------------------
    print("=" * 100)
    print("WITHIN a dataset: spread of m, the claim the band fix depends on")
    print("=" * 100)
    spreads = sorted(d["spread"] for d in per_dataset)
    multi = [d for d in per_dataset if d["shapes"] > 1]
    print(f"datasets parsed        {len(per_dataset)}")
    print(f"median spread          {100*statistics.median(spreads):.2f}% of mean")
    print(f"90th percentile        {100*spreads[int(0.9*len(spreads))]:.2f}%")
    print(f"worst                  {100*max(spreads):.2f}%")
    print(f"datasets over 5%       {sum(1 for s in spreads if s > 0.05)}")
    print(f"mixed-ROI datasets     {len(multi)} (excluded from the grouping below)")

    # ---- between configurations: how much does m move? ---------------------
    print()
    print("=" * 100)
    print(f"{'rig':8} {'ROI':>16} {'raw B/frame':>12} {'m (mean)':>10} "
          f"{'m/raw':>7} {'N_raw':>9} {'N_true':>9} {'band':>8} {'frames':>10}")
    print("=" * 100)
    rows = []
    for (rig, w, h, bpp), mds in sorted(groups.items()):
        if not mds:
            continue
        m = statistics.mean(mds)
        raw = w * h * bpp
        n_raw = MAX_FILE_SIZE // raw
        n_true = MAX_FILE_SIZE // int(raw + m)
        band = 100 * (n_raw / n_true - 1)
        rows.append((rig, w, h, bpp, raw, m, n_raw, n_true, band, len(mds)))
        print(f"{rig:8} {f'{w}x{h}x{bpp}B':>16} {raw:>12,} {m:>10,.0f} "
              f"{100*m/raw:>6.1f}% {n_raw:>9,} {n_true:>9,} {band:>7.1f}% {len(mds):>10,}")

    print()
    ms = [r[5] for r in rows]
    if ms:
        print(f"m across configurations: min {min(ms):,.0f} B  max {max(ms):,.0f} B  "
              f"ratio {max(ms)/min(ms):.1f}x")
        worst = max(rows, key=lambda r: r[8])
        print(f"widest band: {worst[0]} at {worst[1]}x{worst[2]} -> {worst[8]:.1f}% "
              f"({worst[7]:,} real vs {worst[6]:,} disclosed)")
    if unreadable:
        print(f"\n{len(unreadable)} unreadable index files:")
        for path, why in unreadable[:8]:
            print(f"  {why}: {path.parent.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
