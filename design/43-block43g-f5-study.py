"""Block 43g offline study, F5 half — structure_coverage on the 36-tile raster.

design/43 F5: this raster returned would_keep:false at every tile while the
operator could see cells in it, out of focus. The log's own ridge_coverage
separates the tiles that had material (0.17-0.30) from bare glass (~0.001);
that separation is the ground truth here, and it is the session's own number,
not one computed for this study.

The question: does structure_coverage see the material that the per-pixel gate
and snr both missed?
"""
import contextlib
import io
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/Users/zachcm/Code/microclaw-43g")
from microclaw.image_analysis import compute_stats  # noqa: E402
from ndstorage import Dataset  # noqa: E402

D = Path("/Users/zachcm/Documents/Documents - Beyonce/Projects/Micro-Claw"
         "/nestor-06082026/microclaw_data/mt_search_561")


def main():
    obs = {r["position"]: r["result"]
           for r in json.loads((D / "filament_raster.json").read_text())
           if r.get("schema") == "microclaw.analysis-observation/v1"}
    with contextlib.redirect_stdout(io.StringIO()):
        ds = Dataset(str(D / "mt_raster_1"))
    positions = sorted(ds.axes["position"])

    rows = []
    for pos in positions:
        with contextlib.redirect_stdout(io.StringIO()):
            img = ds.read_image(position=pos, time=0)
        s = compute_stats(img, min_snr=3.1)
        o = obs.get(pos, {})
        rows.append({"pos": pos.replace("mt_raster_", ""),
                     "ridge": o.get("ridge_coverage"), "logged_snr": o.get("snr"),
                     "snr": s.snr, "sig": s.signal_coverage,
                     "struct": s.structure_coverage, "conc": s.signal_concentration,
                     "sat": s.saturated_fraction})

    # The session's own label: ridge_coverage separates material from glass.
    material = [r for r in rows if (r["ridge"] or 0) >= 0.10]
    glass = [r for r in rows if (r["ridge"] or 0) < 0.01]
    print(f"36-tile raster: {len(material)} tiles with ridge_coverage >= 0.10 "
          f"(material, out of focus), {len(glass)} with < 0.01 (glass)\n")

    print(f"  {'tile':<8} {'ridge':>6} {'snr':>7} {'sig_cov':>8} {'struct':>8} "
          f"{'conc':>6}   label")
    for r in sorted(rows, key=lambda r: -(r["ridge"] or 0)):
        if r not in material and r not in glass[:4]:
            continue
        label = "MATERIAL" if r in material else "glass"
        snr = f"{r['snr']:.2f}" if r["snr"] is not None else "refused"
        print(f"  {r['pos']:<8} {r['ridge']:>6.3f} {snr:>7} {r['sig']:>8.4f} "
              f"{r['struct']:>8.4f} {r['conc']:>6.3f}   {label}")

    def stat(group, key):
        vals = [g[key] for g in group]
        return f"{np.median(vals):.4f} [{min(vals):.4f}-{max(vals):.4f}]"

    print(f"\n  {'':<16}{'signal_coverage':>28}{'structure_coverage':>30}")
    for name, g in (("material", material), ("glass", glass)):
        print(f"  {name:<16}{stat(g,'sig'):>28}{stat(g,'struct'):>30}")

    print("\n  separation (does either statistic order material above glass?)")
    for key in ("sig", "struct", "conc"):
        m, gl = [x[key] for x in material], [x[key] for x in glass]
        overlap = sum(1 for v in gl if v >= min(m))
        print(f"    {key:<8} material min {min(m):.4f} | glass max {max(gl):.4f} | "
              f"glass tiles at or above the weakest material tile: {overlap}/{len(gl)}")

    print("\n  min_snr sweep on the material tiles (median structure_coverage)")
    for ms in (3.1, 2.8, 2.5, 2.0):
        vals, gvals = [], []
        for r in rows:
            with contextlib.redirect_stdout(io.StringIO()):
                img = ds.read_image(position="mt_raster_" + r["pos"], time=0)
            sc = compute_stats(img, min_snr=ms).structure_coverage
            (vals if r in material else gvals if r in glass else []).append(sc)
        print(f"    min_snr {ms}: material {np.median(vals):.4f}, "
              f"glass {np.median(gvals) if gvals else float('nan'):.4f}")


if __name__ == "__main__":
    main()
