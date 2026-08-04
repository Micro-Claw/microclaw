"""design/36 — reproduce the inverted focus metric, offline, from first principles.

Run:  python design/36-focus-metric-spike.py

Two live sessions (Nestor 2026-07-17, M5 2026-08-04) swept Z and got a metric
curve that was LOWEST at the operator's own manual best focus and climbed toward
both sweep edges. design/28 F2 examined the first one and declined to call it a
sign bug, because a noiseless isolated PSF scores correctly under the same
metric. This spike shows why both observations are true at once: the noiseless
isolated PSF is the ONE case the old metric gets right.

The decisive detail is the ORDER of blur and noise. A camera blurs in the optics
and adds noise afterwards, at a floor that does not blur. Blurring an
already-noisy frame — which is what a naive synthetic does — smooths the noise
away with the signal and flatters any high-pass metric.

No rig, no saved frames, no output files (see [[spike-outputs-not-committed]]).
"""
import numpy as np
from scipy.ndimage import gaussian_filter, laplace, sobel

H, W = 284, 320          # the ROI in play on M5
OFFSET = 180.0           # camera pedestal; M5 reported background_level 182
DEFOCUS = (0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0)


def normalized_laplacian_variance(image):
    """The metric microclaw used until design/36: var(laplace(I-bg)) / mean|I-bg|²."""
    img = image.astype(np.float64)
    bg = float(np.median(img))
    sig = img - bg
    mean = float(np.mean(np.abs(sig)))
    return float(np.var(laplace(sig)) / mean ** 2) if mean > 0 else 0.0


def tenengrad(image):
    """The metric it was replaced with: mean squared Sobel gradient."""
    img = image.astype(np.float64)
    return float(np.mean(sobel(img, 0) ** 2 + sobel(img, 1) ** 2))


def mean_abs_deviation(image):
    """The old metric's DENOMINATOR, plotted on its own — the whole story."""
    img = image.astype(np.float64)
    return float(np.mean(np.abs(img - np.median(img))))


def raw_laplacian_variance(image):
    """The old metric's NUMERATOR, plotted on its own."""
    return float(np.var(laplace(image.astype(np.float64))))


def scene(kind, rng):
    if kind == "puncta":                       # sparse emitters, dark field
        img = np.zeros((H, W))
        for y, x in zip(rng.integers(8, H - 8, 140), rng.integers(8, W - 8, 140)):
            img[y, x] += 9000.0
        return gaussian_filter(img, 1.2)
    if kind == "extended":                     # cell-like structure, all lit
        base = np.clip(gaussian_filter(rng.normal(0, 1, (H, W)), 6), 0, None) * 3000
        return gaussian_filter(base, 1.2)
    raise ValueError(kind)


def detect(truth, defocus, gain, rng):
    """Blur in the optics (flux-conserving), THEN let the detector add noise."""
    blurred = gaussian_filter(truth, defocus) if defocus > 0 else truth
    return (rng.poisson((blurred + 2.0) * gain).astype(np.float64)
            + OFFSET + rng.normal(0, 2.0, truth.shape))


METRICS = {
    "OLD nLV": normalized_laplacian_variance,
    "  its numerator": raw_laplacian_variance,
    "  its denominator": mean_abs_deviation,
    "NEW tenengrad": tenengrad,
}


def run(kind, gain):
    rng = np.random.default_rng(11)
    truth = scene(kind, rng)
    print(f"\n=== {kind}, gain {gain} " + "=" * 40)
    print(f"{'defocus_px':>10}  " + "  ".join(f"{n:>17s}" for n in METRICS))
    curves = {n: [] for n in METRICS}
    for d in DEFOCUS:
        frame = detect(truth, d, gain, rng)
        for name, fn in METRICS.items():
            curves[name].append(fn(frame))
        print(f"{d:10.1f}  " + "  ".join(f"{curves[n][-1]:17.4g}" for n in METRICS))
    for name, values in curves.items():
        peak = DEFOCUS[int(np.argmax(values))]
        verdict = "peaks AT FOCUS" if peak == 0 else f"peaks at defocus {peak}"
        print(f"{name:>18}: {verdict}")


def blur_then_noise_vs_noise_then_blur():
    """Why design/28 F2's synthetic exonerated the metric that was failing."""
    rng = np.random.default_rng(3)
    truth = scene("extended", rng)
    print("\n=== same field, two noise models " + "=" * 27)
    print(f"{'defocus_px':>10}  {'detector order':>17}  {'naive order':>17}")
    for d in (0.0, 4.0, 12.0):
        detector = normalized_laplacian_variance(detect(truth, d, 1.0, rng))
        noisy = detect(truth, 0.0, 1.0, rng)          # noise added first...
        naive = normalized_laplacian_variance(
            gaussian_filter(noisy, d) if d > 0 else noisy)   # ...then blurred
        print(f"{d:10.1f}  {detector:17.4g}  {naive:17.4g}")
    print("  Blurring the noise too removes the floor that pins the numerator,")
    print("  so the old metric looks correctly signed. A camera cannot do that.")


if __name__ == "__main__":
    for kind in ("puncta", "extended"):
        for gain in (1.0, 0.1):
            run(kind, gain)
    blur_then_noise_vs_noise_then_blur()
