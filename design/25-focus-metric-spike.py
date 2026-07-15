"""design/25 spike — is the focus metric really inverted on empty fields, and do
the two fixes (low-pass + SNR gate) actually repair it?

design/25 asserts, from a SYNTHETIC reproduction it admits is not backed by a
committed spike (its lines 32-36):

    on a field with no signal, focus_metric *inflates*. An empty field scores
    ~60x "sharper" than a real cell.

and proposes three fixes whose key numbers are all TODO(rig):

    fix 1  low-pass (block_reduce to the PSF footprint) before the Laplacian
    fix 2  gate the metric on SNR (< min_snr -> "no measurement")
    fix 3  DCTS as the autofocus metric (+ dct_snr for the gate)

This spike makes every one of those reproducible. It follows design/23's shape:

  OFFLINE (default, no hardware) — builds synthetic empty / in-focus / out-of-focus
  fields and a synthetic focus stack, then:
    1. reproduces the inversion with today's normalized_laplacian_variance
    2. shows fix 1 (low-pass) deflates the empty field below the cell, and does
       NOT wreck the in>out focus ordering it is bolted onto (regression)
    3. checks psf_downscale_factor against the paper's own worked example
       (16x/0.8 NA, 6.5um px -> r_p = 1.5 / 3 px support) — resolves the TODO(rig)
       at design/25:170 with pure arithmetic
    4. shows the F7 snr() gate separates empty from cell, and sweeps min_snr to
       turn the "3.0, PLACEHOLDER" into a defended range
    5. exercises DCTS + dct_snr on the focus stack: DCTS peaks at focus, and
       dct_snr — NOT DCTS — is what flags the empty field, confirming design/25's
       central caveat that the metric fixes focusing, the gate fixes ranking

  LIVE (--live, needs a running Micro-Manager demo config on Windows) — the three
  things only the rig can answer:
    6. what get_pixel_size_um() actually returns on this config (the whole
       downscale path and _pixel_size_or_zero hinge on it)
    7. whether NA is genuinely absent from the core (design/25:393 asserts there
       is no get_numerical_aperture(); this probes for one)
    8. that the full focus_score -> _focus_metric_payload path runs on a real
       demo-camera frame without throwing, and how long block_reduce+Laplacian
       and DCTS cost on the real frame size

NOTE the demo camera always renders a synthetic pattern, so it CANNOT produce an
empty field: the live run proves the plumbing and the two bridge facts (6, 7). It
does NOT reproduce the inversion — that is what the synthetic offline run is for,
exactly as design/23 kept its bridge checks separate from its offline proof.

Run:

    python design/25-focus-metric-spike.py                 # offline 1-5
    python design/25-focus-metric-spike.py --live          # + real MM demo config
    python design/25-focus-metric-spike.py --live --port 4827
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
from scipy.fft import dctn
from scipy.ndimage import gaussian_filter, laplace
from skimage.measure import block_reduce

# The metric as it ships today. Everything the spike proposes is measured against
# this, imported not re-implemented, so the spike cannot drift from the code.
from microclaw.image_analysis import normalized_laplacian_variance


# ===========================================================================
# Candidate implementations of the design/25 stubs. These are what the spike
# is here to test; if a number below is convincing, THIS is the body that ships.
# ===========================================================================

def snr(image: np.ndarray, background: float | None = None) -> float:
    """design/23 F7's ONE definition: (p99.5 - bg) / (1.4826 * MAD).

    Copied verbatim from design/23 (its lines 463-482) because F7 is a
    prerequisite of design/25, not built yet. The gate is only as good as this;
    the spike leans on it, so it must be exactly F7's, not a lookalike.
    """
    img = image.astype(np.float64)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    bg = float(np.median(img)) if background is None else background
    mad = float(np.median(np.abs(img - np.median(img))))
    noise = 1.4826 * mad
    if noise <= 0:
        return 0.0
    return float((np.percentile(img, 99.5) - bg) / noise)


def psf_downscale_factor(
    pixel_size_um: float,
    wavelength_um: float = 0.52,
    na: float = 1.4,
) -> int:
    """design/25 fix 1: downscale factor matching the PSF footprint on the detector.

    2*(0.61*lambda/NA) is the Airy diameter in um; dividing by the sample-space
    pixel size gives it in pixels. Returns >= 1; pixel_size_um <= 0 returns 1.
    """
    if pixel_size_um <= 0:
        return 1
    airy_diameter_um = 2.0 * 0.61 * wavelength_um / na
    return max(1, int(round(airy_diameter_um / pixel_size_um)))


def normalized_laplacian_variance_lowpass(
    image: np.ndarray, background: float | None = None, downscale: int = 1
) -> float:
    """fix 1 applied: block-average to the PSF footprint, THEN the ship metric.

    Wraps the imported normalized_laplacian_variance so the only thing under test
    is the block_reduce pre-step — the maths after it is production code.
    """
    img = image.astype(np.float64)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    if downscale > 1:
        img = block_reduce(img, block_size=downscale, func=np.mean)
    return normalized_laplacian_variance(img, background=background)


def dct_shannon_entropy(image: np.ndarray, otf_support_frac: float = 0.25) -> float:
    """fix 3: DCTS — Shannon entropy of the normalized DCT coefficients inside the
    OTF support (Royer et al. 2016, Supp. Eq. 32). Higher = sharper.

    A blurry image is low-pass: its energy piles into a few low-freq coefficients,
    so the normalized coefficient distribution is peaked -> LOW entropy. A sharp
    specimen fills the support -> flatter distribution -> HIGH entropy. Restricting
    to the OTF support IS the low-pass that keeps out-of-band noise from dominating.

    otf_support_frac is a stand-in for the real per-objective cutoff (which comes
    from NA/pixel size, same input as psf_downscale_factor); the spike sweeps it
    where it matters rather than pretending a fraction is calibrated.
    """
    img = image.astype(np.float64)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    img = img - img.mean()
    c = np.abs(dctn(img, norm="ortho"))
    h, w = c.shape
    sh, sw = max(1, int(h * otf_support_frac)), max(1, int(w * otf_support_frac))
    c = c[:sh, :sw].ravel()
    c[0] = 0.0  # drop DC; it carries no focus information
    total = c.sum()
    if total <= 0:
        return 0.0
    p = c / total
    nz = p > 0
    return float(-(p[nz] * np.log2(p[nz])).sum())


def dct_snr(image: np.ndarray, otf_support_frac: float = 0.25) -> float:
    """fix 3: SNR from the same transform. Noise has a flat DCT spectrum, so the
    per-coefficient power OUT of the OTF support estimates the noise floor; power
    IN the support above that floor is signal (Royer, "Adjusting laser power").

    Candidate, not F7: design/25 is explicit (its lines 372-383) that if DCTS
    ships this must REPLACE F7's snr() rather than coexist, so the spike reports
    both scales side by side instead of quietly choosing one.
    """
    img = image.astype(np.float64)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    img = img - img.mean()
    c2 = dctn(img, norm="ortho") ** 2
    h, w = c2.shape
    sh, sw = max(1, int(h * otf_support_frac)), max(1, int(w * otf_support_frac))
    mask = np.zeros_like(c2, dtype=bool)
    mask[:sh, :sw] = True
    mask[0, 0] = False
    in_support = c2[mask]
    out_support = c2[~mask]
    if out_support.size == 0 or in_support.size == 0:
        return 0.0
    noise_per_coeff = float(out_support.mean())
    if noise_per_coeff <= 0:
        return 0.0
    signal = max(0.0, float(in_support.mean()) - noise_per_coeff)
    return float(np.sqrt(signal / noise_per_coeff))


# ===========================================================================
# Synthetic frames. Deterministic (seeded) so the numbers are reproducible.
# ===========================================================================

BG = 100.0          # camera offset in ADU (Evolve512-ish)
READ_NOISE = 3.0    # read-noise sigma in ADU
ROI = 256           # square ROI; the session's tiles were small crops
RNG = np.random.default_rng(20260715)


def _read_noise(shape) -> np.ndarray:
    return RNG.normal(0.0, READ_NOISE, size=shape)


def empty_field() -> np.ndarray:
    """Background + read noise. No specimen. This is the emptiest tile on the grid."""
    return (BG + _read_noise((ROI, ROI))).astype(np.float64)


def cell_field(blur_sigma: float, brightness: float = 220.0) -> np.ndarray:
    """A realistic 'cell' field: a bright, spatially-extended body carrying fine
    internal texture, defocused by blur_sigma, with read noise added AFTER the blur.

    Two ingredients, because a real cell has both and they play different roles:
      - the bright extended body gives a large mean(|I - bg|), i.e. a large
        DENOMINATOR — this is what an empty field lacks and why the empty field's
        ratio explodes. A single dim blob does NOT reproduce the bug; the cell
        must actually be bright.
      - the fine internal texture is the high-frequency SIGNAL that defocus
        removes. Without it, blur_sigma changes nothing and there is no focus to
        find. Read noise is added after the blur, so — unlike the texture — it is
        never defocused, which is exactly why noise reads as 'sharp'.
    """
    ys, xs = np.mgrid[0:ROI, 0:ROI]
    body = np.zeros((ROI, ROI))
    for _ in range(3):
        cy, cx = RNG.uniform(ROI * 0.3, ROI * 0.7, size=2)
        body += brightness * np.exp(-((ys - cy) ** 2 + (xs - cx) ** 2) / (2 * 22.0 ** 2))
    tex = gaussian_filter(RNG.normal(0.0, 1.0, (ROI, ROI)), 1.2)
    obj = body + 0.45 * brightness * (tex / tex.std()) * (body > 0.1 * brightness)
    obj = np.clip(obj, 0, None)
    if blur_sigma > 0:
        obj = gaussian_filter(obj, blur_sigma)
    return (BG + obj + _read_noise((ROI, ROI))).astype(np.float64)


# ===========================================================================
# OFFLINE checks 1-5
# ===========================================================================

def check_1_inversion() -> bool:
    """1. Reproduce the inversion with the SHIPPING metric."""
    print(f"\n{'=' * 74}\n1. THE INVERSION — today's normalized_laplacian_variance\n{'=' * 74}")
    empty = empty_field()
    out_focus = cell_field(blur_sigma=4.0)
    in_focus = cell_field(blur_sigma=0.0)

    rows = [
        ("empty (bg + read noise)", empty),
        ("cell, out of focus", out_focus),
        ("cell, in focus", in_focus),
    ]
    print(f"\n  ROI {ROI}x{ROI}, bg {BG:.0f} ADU, read noise {READ_NOISE:.0f} ADU\n")
    print(f"  {'field':28s} {'focus_metric':>14s} {'SNR':>7s}")
    vals = {}
    for name, img in rows:
        fm = normalized_laplacian_variance(img)
        s = snr(img)
        vals[name] = fm
        print(f"  {name:28s} {fm:>14.2f} {s:>7.2f}")

    empty_fm = vals["empty (bg + read noise)"]
    cell_fm = max(vals["cell, out of focus"], vals["cell, in focus"])
    inflated = empty_fm > cell_fm
    ratio = empty_fm / cell_fm if cell_fm > 0 else float("inf")
    print(f"\n  --> empty field outscores the best cell: {'YES' if inflated else 'NO'}"
          f"   ({ratio:.0f}x)")
    print("      design/25's premise. If NO here, the whole document is questionable;")
    print("      if YES, the ratio is this rig-independent synthetic's figure, not the ~60x.")
    return inflated


def check_2_lowpass() -> bool:
    """2. Does fix 1 (block_reduce low-pass) actually deflate the empty field?

    This is where the spike earns its keep. design/25 calls the block-mean low-pass
    "the direct antidote to the noise inflation" (its lines 90-93). But microclaw's
    metric was already made SCALE-FREE by design/14 (divide by mean|I-bg|²), and a
    scale-free ratio is, by construction, blind to the very rescaling block_reduce
    performs on white noise. The spike checks that head-on."""
    print(f"\n{'=' * 74}\n2. FIX 1 (low-pass) — does block_reduce repair the inversion?\n{'=' * 74}")

    # 2a. The mechanism, analytically. For pure white noise the ratio is a CONSTANT
    # independent of amplitude: var(laplace(noise)) = 20*sigma^2 (the 5-point kernel
    # has sum-of-squares 20), mean|noise| = sigma*sqrt(2/pi), so the ratio is
    # 20 / (2/pi) = 10*pi ~= 31.4 for ANY sigma. block_reduce by k averages 2x2
    # independent pixels into one independent pixel: still white noise, just at
    # sigma/k. Same constant. So the low-pass cannot move the empty field.
    print("\n  2a. normalized_laplacian_variance on PURE white noise vs downscale:")
    print(f"      (analytic prediction for white noise: 10*pi = {10 * np.pi:.2f})\n")
    print(f"      {'downscale':>10} {'metric':>10}")
    noise = empty_field()
    flat = True
    base = None
    for k in (1, 2, 4, 8):
        v = normalized_laplacian_variance_lowpass(noise, downscale=k)
        print(f"      {k:>10} {v:>10.2f}")
        if base is None:
            base = v
        elif abs(v - base) / base > 0.15:
            flat = False
    print(f"\n      --> metric is ~constant across downscale: {'YES' if flat else 'NO'}")
    print("          block_reduce is a no-op on the empty field BECAUSE the metric is")
    print("          already scale-free (design/14). This is the finding: fix 1 as")
    print("          WRITTEN (block mean) does not deflate an empty field.")

    # 2b. What that does to the ranking. Empty stays pinned at ~31.4; the cell's
    # ratio actually DROPS under block_reduce (its noise numerator is cut ~4x while
    # its bright signal denominator is untouched), so fix 1 makes the gap WORSE.
    px_um = 0.325
    factor = psf_downscale_factor(px_um, wavelength_um=0.52, na=0.8)
    empty = empty_field()
    out_focus = cell_field(blur_sigma=4.0)
    in_focus = cell_field(blur_sigma=0.0)
    print(f"\n  2b. ranking under block_reduce (factor {factor}):")
    print(f"      {'field':16s} {'raw':>10s} {'block-reduce':>14s}")
    res = {}
    for name, img in [("empty", empty), ("cell out", out_focus), ("cell in", in_focus)]:
        raw = normalized_laplacian_variance(img)
        lp = normalized_laplacian_variance_lowpass(img, downscale=factor)
        res[name] = lp
        print(f"      {name:16s} {raw:>10.2f} {lp:>14.2f}")
    repaired = res["empty"] < min(res["cell out"], res["cell in"])
    print(f"\n      --> after block_reduce, empty is BELOW both cells: "
          f"{'YES' if repaired else 'NO (fix 1 does not repair ranking)'}")

    # 2c. The constructive alternative. A GAUSSIAN pre-filter (which design/25:205
    # explicitly rejected — "Block mean, not a Gaussian") DOES reduce the empty
    # field, because it correlates the noise: laplace of a smoothed field has far
    # less variance relative to its amplitude. This is the fix that would actually
    # work, and it contradicts the doc's stated choice.
    print(f"\n  2c. a Gaussian pre-filter instead (the alternative design/25 rejected):")
    print(f"      {'field':16s} {'raw':>10s} {'gaussian':>10s}")
    gauss_repaired_gap = None
    gres = {}
    for name, img in [("empty", empty), ("cell out", out_focus), ("cell in", in_focus)]:
        g = normalized_laplacian_variance(gaussian_filter(img.astype(np.float64), 1.5))
        gres[name] = g
        print(f"      {name:16s} {normalized_laplacian_variance(img):>10.2f} {g:>10.2f}")
    gaussian_lowers_empty = gres["empty"] < 0.5 * 10 * np.pi
    gaussian_repairs = gres["empty"] < min(gres["cell out"], gres["cell in"])
    print(f"\n      --> Gaussian moves the empty field OFF its ~31.4 pin: "
          f"{'YES' if gaussian_lowers_empty else 'NO'}"
          f"   (block mean could not; a correlating filter can)")
    print(f"      --> ...but does Gaussian then rank empty BELOW the cells? "
          f"{'YES' if gaussian_repairs else 'NO'}")
    print("          The honest read: a Gaussian (overlapping) low-pass is the only")
    print("          low-pass that even touches the empty field — the block mean the")
    print("          doc specifies (design/25:205) is defeated by design/14's scale")
    print("          normalization. But NO low-pass restores the ranking on its own,")
    print("          because it also crushes the cell's signal. Only the gate ranks.")

    # The verdict for fix 1: it does NOT repair ranking, which is the honest result.
    print(f"\n  VERDICT: block-mean low-pass repairs the empty-field ranking: "
          f"{'YES' if repaired else 'NO'}")
    print("           The gate (fix 2) is doing the real work; fix 1 as written is not")
    print("           the antidote design/25 claims. Returns FAIL to flag this loudly.")
    return repaired


def check_3_psf_formula() -> bool:
    """3. psf_downscale_factor vs the paper's own worked example. Pure arithmetic;
    resolves the TODO(rig) at design/25:170 with no rig."""
    print(f"\n{'=' * 74}\n3. FIX 1 formula — reproduce Royer's r_p = 1.5 (design/25:170 TODO)\n{'=' * 74}")
    print("\n  Paper: 16x/0.8 NA, 6.5 um camera pixels -> r_p = 1.5 (3 px support).")
    print("  Sample-space pixel = 6.5 / 16 = 0.40625 um. Emission ~0.52 um.\n")
    px_um = 6.5 / 16
    airy_diameter = 2.0 * 0.61 * 0.52 / 0.8
    factor = psf_downscale_factor(px_um, wavelength_um=0.52, na=0.8)
    print(f"  Airy diameter 2*0.61*0.52/0.8 = {airy_diameter:.3f} um")
    print(f"  factor = round({airy_diameter:.3f} / {px_um:.4f}) = {factor}")
    print(f"\n  Paper's support DIAMETER (2 * r_p) = 3 px. This function returns {factor}.")
    close = factor in (2, 3, 4)
    print(f"\n  --> formula lands in the paper's neighbourhood (2-4 px): "
          f"{'YES' if close else 'NO'}")
    print("      design/25 warns the formula (a downscale factor) and the paper (a support")
    print("      RADIUS) are 'only loosely coupled'. This is the check it asked for: they")
    print(f"      agree to within a factor of ~2, which is the ±1 the doc calls acceptable.")
    print("      If NO, design/25:172 wins: this becomes a per-objective lookup, not a formula.")
    return close


def check_4_gate() -> bool:
    """4. The SNR gate separates empty from cell; sweep min_snr for a defended range."""
    print(f"\n{'=' * 74}\n4. FIX 2 (gate) — does F7 snr() separate empty from cell?\n{'=' * 74}")
    # A spread of fields so the separation is a gap, not two points.
    empties = [empty_field() for _ in range(8)]
    cells = [cell_field(blur_sigma=b, brightness=p)
             for b in (0.0, 2.0, 4.0) for p in (60.0, 120.0, 220.0)]
    empty_snr = np.array([snr(i) for i in empties])
    cell_snr = np.array([snr(i) for i in cells])
    print(f"\n  empty fields  SNR: min {empty_snr.min():.2f}  max {empty_snr.max():.2f}"
          f"  (n={len(empties)})")
    print(f"  cell  fields  SNR: min {cell_snr.min():.2f}  max {cell_snr.max():.2f}"
          f"  (n={len(cells)})")
    gap_lo, gap_hi = empty_snr.max(), cell_snr.min()
    separated = gap_lo < gap_hi
    print(f"\n  --> a threshold separates them cleanly: {'YES' if separated else 'NO'}")
    if separated:
        print(f"      any min_snr in ({gap_lo:.2f}, {gap_hi:.2f}) splits every synthetic field")
        print(f"      correctly; midpoint {(gap_lo + gap_hi) / 2:.2f}. design/25's placeholder")
        print("      3.0 is inside a plausible band — but this is SYNTHETIC. Calibrate on the")
        print("      rig against real frames before shipping (design/25:227, design/23 F7).")
    else:
        print("      Overlap: no single min_snr works even synthetically. The gate needs a")
        print("      better signal statistic than F7's snr(), or per-objective thresholds.")
    return separated


def check_5_dcts() -> bool:
    """5. DCTS peaks at focus across a stack; dct_snr — not DCTS — flags the empty
    field. This is design/25's central caveat made concrete (its lines 124-134)."""
    print(f"\n{'=' * 74}\n5. FIX 3 (DCTS) — focuses correctly, but does NOT rank fields\n{'=' * 74}")
    # A focus stack: blur large -> 0 -> large, specimen present in EVERY plane.
    blurs = [6, 4, 2, 1, 0, 1, 2, 4, 6]
    stack = [cell_field(blur_sigma=b, brightness=220.0) for b in blurs]
    dcts = [dct_shannon_entropy(i) for i in stack]
    nlv = [normalized_laplacian_variance(i) for i in stack]
    best_dcts = int(np.argmax(dcts))
    best_nlv = int(np.argmax(nlv))
    focus_idx = blurs.index(0)
    print(f"\n  blur sweep (0 = focus at index {focus_idx}):")
    print(f"  {'idx':>4} {'blur':>5} {'DCTS':>9} {'norm_lap':>10}")
    for i, b in enumerate(blurs):
        mark = "  <- focus" if i == focus_idx else ""
        print(f"  {i:>4} {b:>5} {dcts[i]:>9.3f} {nlv[i]:>10.2f}{mark}")
    dcts_ok = best_dcts == focus_idx
    print(f"\n  --> DCTS peaks at true focus       : {'YES' if dcts_ok else 'NO'}"
          f"   (argmax at idx {best_dcts})")
    print(f"  --> norm_lap peaks at true focus   : "
          f"{'YES' if best_nlv == focus_idx else 'NO'}   (argmax at idx {best_nlv})")

    # Now the caveat: DCTS on an empty field is NOT low, so DCTS alone still can't
    # tell an empty tile from a cell. dct_snr is what does that.
    empty = empty_field()
    cell = cell_field(blur_sigma=0.0, brightness=220.0)
    print(f"\n  the field-ranking case DCTS does NOT solve:")
    print(f"  {'field':16s} {'DCTS':>9} {'dct_snr':>9} {'F7 snr':>9}")
    for name, img in [("empty", empty), ("cell in focus", cell)]:
        print(f"  {name:16s} {dct_shannon_entropy(img):>9.3f} "
              f"{dct_snr(img):>9.2f} {snr(img):>9.2f}")
    empty_dcts = dct_shannon_entropy(empty)
    cell_dcts = dct_shannon_entropy(cell)
    dcts_ranks_wrong = empty_dcts >= cell_dcts
    gate_separates = dct_snr(empty) < dct_snr(cell)
    print(f"\n  --> DCTS alone would ALSO misrank the empty field: "
          f"{'YES' if dcts_ranks_wrong else 'NO'}")
    print(f"  --> dct_snr separates empty from cell            : "
          f"{'YES' if gate_separates else 'NO'}")
    print("      This is design/25's thesis: the metric choice fixes FOCUSING, the SNR")
    print("      gate fixes field RANKING. They are different problems; fix 3 is not fix 2.")
    return dcts_ok and gate_separates


def offline() -> bool:
    print("#" * 74)
    print("# OFFLINE — synthetic frames, no hardware")
    print("#" * 74)
    r1 = check_1_inversion()
    r2 = check_2_lowpass()
    r3 = check_3_psf_formula()
    r4 = check_4_gate()
    r5 = check_5_dcts()

    print(f"\n{'=' * 74}\nOFFLINE VERDICT\n{'=' * 74}")
    print(f"""
  1. inversion reproduced (empty > cell)         : {'PASS' if r1 else 'FAIL'}
  2. fix 1 (block-mean low-pass) repairs ranking  : {'PASS' if r2 else 'FAIL — see below'}
  3. fix 1 formula matches paper's r_p example    : {'PASS' if r3 else 'FAIL'}
  4. fix 2 gate separates empty from cell         : {'PASS' if r4 else 'FAIL'}
  5. fix 3 DCTS focuses; dct_snr ranks            : {'PASS' if r5 else 'FAIL'}

  The check-2 FAIL is the spike's main FINDING, not a spike bug: design/25's fix 1
  as written does NOT work. microclaw's metric was made scale-free by design/14
  (divide by mean|I-bg|^2), and the empty field's value is the analytic constant
  10*pi ~= 31.4 for ANY noise amplitude -- so block_reduce, which just rescales
  white noise, cannot move it (check 2a). The block mean the doc specifically
  chose over a Gaussian (design/25:205) is the one filter design/14 renders inert.
  Recommendation for design/25: drop fix 1's block-mean, or replace it with a
  Gaussian pre-filter, and treat fix 2 (the gate) as THE fix for field-ranking --
  the spike shows the gate carries that load alone (checks 4, 5).

  What DID hold: the inversion is real and lands on 10*pi; the SNR gate cleanly
  separates empty (pinned ~2.58) from cell with room around min_snr=3; DCTS
  focuses correctly while dct_snr -- not DCTS -- is what ranks fields.

  CAVEAT this offline run cannot lift: synthetic frames. MIN_SNR and the NA feeding
  psf_downscale_factor are still rig measurements. Run --live for pixel size, NA
  availability, and real-frame timing.
""")
    # Success == "the spike ran and its checks are decisive." Fix 1 failing is a
    # finding we WANT surfaced, so it does not fail the run; 1/3/4/5 must hold.
    return all((r1, r3, r4, r5))


# ===========================================================================
# LIVE checks 6-8 — needs a running MM demo config
# ===========================================================================

def live(port: int) -> bool:
    print("\n" + "#" * 74)
    print(f"# LIVE — real MM demo config on port {port}")
    print("#" * 74)
    from microclaw.controller import MicroscopeController

    ctrl = MicroscopeController(port=port)
    if not ctrl.is_connected():
        print("  !! Not connected to MM on this port; skipping live checks.")
        return False

    # --- 6. pixel size ---
    print(f"\n{'=' * 74}\n6. get_pixel_size_um() on this config (design/25 _pixel_size_or_zero)\n{'=' * 74}")
    try:
        px = float(ctrl.core.get_pixel_size_um())
    except Exception as e:
        px = 0.0
        print(f"  get_pixel_size_um() raised: {e!r}")
    factor = psf_downscale_factor(px) if px > 0 else 1
    print(f"\n  pixel_size_um = {px}")
    print(f"  psf_downscale_factor({px}) = {factor}")
    if px <= 0:
        print("  --> uncalibrated: _pixel_size_or_zero returns 0.0, factor 1, NO downscale.")
        print("      Fix 1 is a no-op until someone sets a pixel-size config. Expected on a")
        print("      bare demo config; note it so the rig person adds one before trusting fix 1.")
    else:
        print(f"  --> calibrated: fix 1 would block-reduce by {factor} on this objective.")

    # --- 7. is NA available from the core? ---
    print(f"\n{'=' * 74}\n7. Is NA reachable from the core? (design/25:393 says no)\n{'=' * 74}")
    na_found = _probe_for_na(ctrl)
    print(f"\n  --> a numerical-aperture accessor exists on the core: "
          f"{'YES' if na_found else 'NO'}")
    if not na_found:
        print("      Confirms design/25:393. NA must be stored per objective in calibration.py")
        print("      (its option 1); it cannot be read from MM.")

    # --- 8. end-to-end on a real frame ---
    print(f"\n{'=' * 74}\n8. focus_score path + timing on a real demo-camera frame\n{'=' * 74}")
    ok8 = _live_end_to_end(ctrl, px, factor)

    print(f"\n{'=' * 74}\nLIVE VERDICT\n{'=' * 74}")
    print(f"  6. pixel size read              : {px} um (factor {factor})")
    print(f"  7. NA absent from core          : {'CONFIRMED' if not na_found else 'NA FOUND'}")
    print(f"  8. real-frame plumbing runs     : {'PASS' if ok8 else 'FAIL'}")
    print("  Fold pixel size, the NA finding, and the timings into design/25.\n")
    return ok8


def _probe_for_na(ctrl) -> bool:
    """Look for any numerical-aperture accessor on the core, and for a per-property
    NA. design/23 confirmed a bridge claim exactly this way (dump + probe)."""
    hits = [n for n in dir(ctrl.core)
            if "aperture" in n.lower() or n.lower().endswith("_na")
            or "numericalaperture" in n.lower().replace("_", "")]
    print(f"  core methods matching NA / aperture: {hits or '(none)'}")
    for guess in ("get_numerical_aperture", "getNumericalAperture"):
        fn = getattr(ctrl.core, guess, None)
        if callable(fn):
            try:
                print(f"  {guess}() = {fn()}")
                return True
            except Exception as e:
                print(f"  {guess}() exists but raised: {e!r}")
    return bool(hits)


def _live_end_to_end(ctrl, px: float, factor: int) -> bool:
    """Snap one real frame; run the fix-1 metric, the gate, and DCTS on it, timed.
    The demo pattern is a specimen, not an empty field — this proves the code runs
    on real dtype/shape, NOT the inversion (see the module docstring)."""
    from microclaw.image_analysis import snap_to_numpy

    try:
        img = snap_to_numpy(ctrl)
    except Exception as e:
        print(f"  !! snap failed: {e!r}")
        return False
    print(f"\n  frame: shape {img.shape}, dtype {img.dtype}")

    def timed(label, fn):
        t0 = time.perf_counter()
        val = fn()
        ms = (time.perf_counter() - t0) * 1000
        print(f"  {label:34s} = {val:<12.4g}  ({ms:6.1f} ms)")
        return val

    try:
        timed("normalized_laplacian_variance", lambda: normalized_laplacian_variance(img))
        timed(f"  + low-pass (factor {factor})",
              lambda: normalized_laplacian_variance_lowpass(img, downscale=factor))
        s = timed("F7 snr", lambda: snr(img))
        timed("dct_shannon_entropy", lambda: dct_shannon_entropy(img))
        timed("dct_snr", lambda: dct_snr(img))
    except Exception as e:
        print(f"  !! a metric raised on the real frame: {e!r}")
        return False

    print(f"\n  --> full focus_score path runs on a real {img.shape} {img.dtype} frame.")
    print(f"      Demo pattern SNR {s:.1f} (a specimen, so no gate refusal expected here).")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="also run the real-MM checks (needs a running demo config)")
    ap.add_argument("--port", type=int, default=4827, help="MM ZMQ port (default 4827)")
    args = ap.parse_args()

    offline_ok = offline()
    if not args.live:
        print("  (offline only -- pass --live against a running MM demo config for checks 6-8)\n")
        return 0 if offline_ok else 1

    live_ok = live(args.port)
    return 0 if (offline_ok and live_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
