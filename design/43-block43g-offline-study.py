"""Block 43g offline calibration study — design/43 F6, on real M5 data.

Re-analyses the 324 saved tiles of the 2026-08-06 Nestor 488 raster with the
coverage statistics on design43/coverage-statistics, joined to the observation
records the shipped code wrote at the time (snr_log.json, min_snr 3.1,
package_default_uncalibrated).

Control: recomputed snr must reproduce the logged snr exactly, or the join is
wrong and nothing below means anything.

snr is ranked the way rank_hook_log ranks it — rows whose snr_valid is False are
routed out, not sorted as NaN.
"""
import contextlib
import io
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/Users/zachcm/Code/microclaw-43g")
from microclaw.image_analysis import (  # noqa: E402
    MAX_SATURATED_FRACTION_FOR_SNR, compute_stats,
)

ROOT = Path("/Users/zachcm/Documents/Documents - Beyonce/Projects/Micro-Claw"
            "/nestor-06082026/microclaw_data/scan300_488")
CORNER = "scan300_488_r12_c15"          # F6's winner: snr 26.21, would not focus
SHORT = lambda p: p.replace("scan300_488_", "")   # noqa: E731


def load():
    records = {r["position"]: r for r in json.loads((ROOT / "snr_log.json").read_text())}
    from ndstorage import Dataset
    with contextlib.redirect_stdout(io.StringIO()):
        ds = Dataset(str(ROOT / "scan300_488_1"))
    frames = {}
    for pos in sorted(ds.axes["position"]):
        with contextlib.redirect_stdout(io.StringIO()):
            frames[pos] = ds.read_image(position=pos, time=0)
    return records, frames


def measure(frames, min_snr):
    rows = []
    for pos, img in frames.items():
        s = compute_stats(img, min_snr=min_snr)
        rows.append({
            "pos": pos, "snr": s.snr, "snr_valid": s.snr_valid,
            "sig": s.signal_coverage, "struct": s.structure_coverage,
            "conc": s.signal_concentration, "sat": s.saturated_fraction,
            "clipped": s.saturated_fraction > MAX_SATURATED_FRACTION_FOR_SNR,
        })
    return rows


def show(rows, key, label, n=8):
    print(f"\n  {label}")
    print(f"  {'#':>2} {'tile':<10} {'snr':>7} {'sig_cov':>8} {'struct':>7} "
          f"{'conc':>6} {'sat%':>7}")
    for i, r in enumerate(rows[:n], 1):
        tag = "  <-- F6's tile" if r["pos"] == CORNER else ("  CLIPPED" if r["clipped"] else "")
        snr = f"{r['snr']:.2f}" if r["snr"] is not None else "refused"
        print(f"  {i:>2} {SHORT(r['pos']):<10} {snr:>7} {r['sig']:>8.4f} "
              f"{r['struct']:>7.4f} {r['conc']:>6.3f} {100*r['sat']:>6.2f}%{tag}")


def rank_of(rows, pos):
    names = [r["pos"] for r in rows]
    return names.index(pos) + 1 if pos in names else None


def main():
    records, frames = load()
    worst = max(abs(compute_stats(img, min_snr=3.1).snr - records[p]["result"]["snr"])
                for p, img in frames.items() if records[p]["result"].get("snr") is not None)
    n_ctrl = sum(1 for p in frames if records[p]["result"].get("snr") is not None)
    print(f"CONTROL: recomputed snr == logged snr over {n_ctrl}/{len(frames)} tiles; "
          f"max abs difference {worst:.4f}")
    if worst > 0.01:
        print("JOIN IS WRONG — stopping."); return

    rows = measure(frames, 3.1)
    clipped = [r for r in rows if r["clipped"]]
    print(f"\nSATURATION: {len(clipped)}/{len(rows)} tiles exceed the "
          f"{MAX_SATURATED_FRACTION_FOR_SNR:.4%} clipping gate "
          f"(so snr and focus_metric are refused on them)")
    print("  " + ", ".join(f"{SHORT(r['pos'])} ({100*r['sat']:.1f}%)"
                           for r in sorted(clipped, key=lambda r: -r["sat"])[:8]))

    by_snr = sorted([r for r in rows if r["snr_valid"]], key=lambda r: -r["snr"])
    by_cov = sorted(rows, key=lambda r: -r["sig"])
    by_cov_clean = sorted([r for r in rows if not r["clipped"]], key=lambda r: -r["sig"])

    print(f"\n{'='*78}\nAT THE SHIPPED THRESHOLD (min_snr = 3.1, package_default_uncalibrated)")
    show(by_snr, "snr", f"ranked by snr — what the session ranked on "
                        f"({len(by_snr)} rankable, {len(rows)-len(by_snr)} refused)")
    show(by_cov, "sig", "ranked by signal_coverage — what 43g proposes, as shipped")
    show(by_cov_clean, "sig", "ranked by signal_coverage, clipped tiles removed")

    print(f"\n  F6's tile {SHORT(CORNER)}: #{rank_of(by_snr, CORNER)} by snr, "
          f"#{rank_of(by_cov, CORNER)} by coverage as shipped, "
          f"#{rank_of(by_cov_clean, CORNER)} by coverage without clipped tiles")

    print(f"\n{'='*78}\nmin_snr SWEEP — how much of the field registers as sample")
    print(f"  {'min_snr':>8} {'tiles sig>0.01':>15} {'tiles sig>0.05':>15} "
          f"{'median sig_cov':>16} {'F6 tile rank':>13}")
    for m in (4.0, 3.5, 3.1, 2.8, 2.5, 2.0, 1.5):
        rs = measure(frames, m)
        clean = sorted([r for r in rs if not r["clipped"]], key=lambda r: -r["sig"])
        print(f"  {m:>8} {sum(1 for r in rs if r['sig']>0.01):>15} "
              f"{sum(1 for r in rs if r['sig']>0.05):>15} "
              f"{np.median([r['sig'] for r in rs]):>16.5f} "
              f"{rank_of(clean, CORNER):>13}")


if __name__ == "__main__":
    main()
