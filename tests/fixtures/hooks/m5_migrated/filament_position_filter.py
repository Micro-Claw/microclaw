from microclaw.hook_decisions import DiscardFrame, HookResult
import numpy as np

try:
    from skimage.filters import sato
    from scipy import ndimage as ndi
    _HAVE_SKIMAGE = True
except Exception:
    _HAVE_SKIMAGE = False


class FilamentPositionFilter:
    """Position-filter hook: keeps fields with filamentous (e.g. microtubule)
    signal, skips fields that are empty, blobby, or noise-dominated.

    NOTE: this detects *filamentous structure*, not microtubules specifically.
    It cannot distinguish tubulin from other fibrous signal. Meaningful only on
    focused, adequate-SNR images.
    """

    def __init__(self, min_filament_score=0.02, min_snr=3.0,
                 ridge_sigmas=(1.0, 2.0, 3.0)):
        self.min_filament_score = float(min_filament_score)
        self.min_snr = float(min_snr)
        self.ridge_sigmas = tuple(float(s) for s in ridge_sigmas)

    def _score(self, image):
        img = image.astype(np.float32)
        # Robust background / noise estimate
        bg = np.median(img)
        mad = np.median(np.abs(img - bg)) + 1e-6
        noise = 1.4826 * mad
        signal_max = np.percentile(img, 99.5)
        snr = float((signal_max - bg) / (noise + 1e-6))

        if not _HAVE_SKIMAGE:
            # Fallback: gradient-structure proxy (weaker, still SNR-gated)
            gx, gy = np.gradient(img)
            resp = np.hypot(gx, gy)
        else:
            sub = np.clip(img - bg, 0, None)
            # Sato tubeness: high response along bright curvilinear ridges
            resp = sato(sub, sigmas=self.ridge_sigmas, black_ridges=False)

        # Threshold ridge response relative to its own spread
        r_bg = np.median(resp)
        r_mad = np.median(np.abs(resp - r_bg)) + 1e-6
        ridge_mask = resp > (r_bg + 6.0 * 1.4826 * r_mad)
        coverage = float(ridge_mask.mean())

        # SNR gate: a noisy empty field cannot score high
        filament_score = coverage if snr >= self.min_snr else 0.0
        return filament_score, snr, coverage

    def analyze_frame(self, image, metadata):
        axes = metadata.get("Axes", {}) or {}
        pos = axes.get("position")
        try:
            score, snr, coverage = self._score(np.asarray(image))
        except Exception as e:
            # Fail-open: keep the image if scoring errors, log the reason
            return HookResult({"position": pos, "error": repr(e), "kept": True})

        keep = score >= self.min_filament_score
        measurements = {
            "position": pos,
            "filament_score": round(score, 5),
            "snr": round(snr, 3),
            "ridge_coverage": round(coverage, 5),
            "kept": bool(keep),
        }
        return HookResult(measurements, () if keep else (DiscardFrame(),))
