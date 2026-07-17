"""design/26 — spike: can microclaw detect a user-described ROI and act on it?

Three questions, run in order. None of them need Micro-Manager or a microscope.

  B. Can a classifier trained on FIVE example images pick the right tiles?
     Synthetic 'mitotic-like' vs 'interphase-like' vs 'empty' fields. Pure
     numpy/skimage — the point is to find the floor, i.e. what we get with the
     dependencies already in pyproject.toml, before arguing for torch. B also
     measures what a REFIT does to the score scale, which is what makes a
     shipped default threshold impossible (F3).

  C. What does it cost — to fit, and per tile? A tile scan has a dwell budget of
     roughly (exposure + settle) ≈ 100–500 ms. Anything under that is free;
     anything over it halves scan throughput.

  D. Does UMAP belong in the scoring chain? (Optional; skips if absent.)

  E. Where does the classical floor actually BREAK, and how far does a still-
     free numpy/skimage descriptor climb before a learned (torch) backend is
     the only lever left? The whole backend ladder in the survey table is
     justified by argument, because on B's by-construction data the classical
     floor is already perfect. E builds concepts it is structurally blind to.

The sections are lettered B/C/D/E because A — "can a hook enqueue an acquisition
from image_process_fn?" — outgrew this file and now lives in
design/24-event-queue-spike.py, alongside design/24, which is the bug it found.

Run:  python design/26-roi-detection-spike.py
Nothing here touches hardware, the network, or ~/.microclaw.
"""
from __future__ import annotations

import sys
import time

import numpy as np

RNG = np.random.default_rng(0)
SEP = "=" * 78


# ---------------------------------------------------------------------------
# B. Five examples, one classifier
# ---------------------------------------------------------------------------
#
# The user hands us a description and a handful of example images. Three
# synthetic classes stand in for "what the user wants" vs "what else is on the
# slide": bright compact chromatin (call it mitotic), diffuse nuclei
# (interphase), and empty/debris field. Only the first is wanted.
#
# Everything here uses numpy + scipy + skimage, all of which microclaw already
# depends on. No torch, no sklearn.

TILE = 256


def _tile(kind: str) -> np.ndarray:
    """A 256x256 uint16 'field of view'. Camera offset ~400, read noise ~15."""
    img = RNG.normal(400, 15, (TILE, TILE))
    yy, xx = np.mgrid[0:TILE, 0:TILE]

    if kind == "empty":
        for _ in range(RNG.integers(0, 3)):            # a speck of debris
            cy, cx = RNG.integers(20, TILE - 20, 2)
            img += RNG.uniform(200, 600) * np.exp(
                -((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 2.0 ** 2))
        return np.clip(img, 0, 65535).astype(np.uint16)

    n_cells = RNG.integers(3, 7)
    for _ in range(n_cells):
        cy, cx = RNG.integers(30, TILE - 30, 2)
        if kind == "interphase":
            # one broad, smooth, dim nucleus
            img += RNG.uniform(300, 500) * np.exp(
                -((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 14.0 ** 2))
        elif kind == "mitotic":
            # condensed chromatin: several bright, tight blobs in a tight cluster
            for _ in range(RNG.integers(4, 8)):
                by, bx = cy + RNG.normal(0, 6), cx + RNG.normal(0, 6)
                img += RNG.uniform(1500, 2600) * np.exp(
                    -((yy - by) ** 2 + (xx - bx) ** 2) / (2 * 2.6 ** 2))
    return np.clip(img, 0, 65535).astype(np.uint16)


def features(image: np.ndarray) -> np.ndarray:
    """A tile -> a fixed-length descriptor, using only what's in pyproject.toml.

    Deliberately generic: nothing here knows what 'mitotic' means. It is the
    same descriptor for every experiment; the FIVE EXAMPLES are what pick out
    the direction in this space that the user cares about. That is the whole
    bet — that a generic descriptor plus a handful of labels beats a bespoke
    network nobody has time to train mid-experiment.
    """
    from scipy import ndimage
    from skimage.feature import blob_log

    img = image.astype(np.float32)
    bg = float(np.median(img))
    sig = np.clip(img - bg, 0, None)
    peak = float(sig.max()) or 1.0
    norm = sig / peak

    # intensity distribution
    pcts = np.percentile(sig, [50, 75, 90, 99, 99.9])
    # texture: how much energy is at fine scales vs coarse (condensed vs diffuse)
    fine = ndimage.gaussian_filter(norm, 1.5)
    coarse = ndimage.gaussian_filter(norm, 8.0)
    band = float(np.mean(np.abs(fine - coarse)))
    lap = float(np.var(ndimage.laplace(norm)))
    # objects: how many, how tight, how clustered
    blobs = blob_log(norm, min_sigma=1.5, max_sigma=6, num_sigma=4, threshold=0.12)
    n = len(blobs)
    sigma_mean = float(blobs[:, 2].mean()) if n else 0.0
    if n >= 2:                                   # mean nearest-neighbour distance
        d = np.linalg.norm(blobs[:, None, :2] - blobs[None, :, :2], axis=-1)
        np.fill_diagonal(d, np.inf)
        nn = float(d.min(axis=1).mean())
    else:
        nn = float(TILE)
    frac_bright = float(np.mean(sig > 0.25 * peak))

    return np.array([
        *(pcts / peak), band, lap, np.log1p(n), sigma_mean,
        np.log1p(nn), frac_bright, peak / (float(sig.std()) or 1.0),
    ], dtype=np.float64)


class NearestMeanProbe:
    """The FIRST draft of the probe, kept only so B_1 can fail it.

    Standardize, take the class means, score along the difference of prototypes
    (a diagonal-LDA / nearest-mean rule). It fits in microseconds and it reads
    like the obviously-right answer for five examples: no capacity, nothing to
    overfit.

    It is also the worst head in the table, and it took FIVE SEEDS to see that.
    The first draft of design/26 reported this probe at AUC 0.994 / precision@k
    0.95 — which is seed 0, and seed 0 is the luckiest of five. On seed 3 it gets
    AUC 0.727 and 2 of its top 19 candidates are real. A mean-difference direction
    has no margin: it is set by the CENTROID of the five examples, so one
    unrepresentative example tilts it and nothing pushes back.

    Kept in the tree for the same reason make_event_stream_total_cap is kept —
    a claim this doc made and this spike now refutes should stay executable.
    """
    name = "nearest-mean (first draft)"

    def fit(self, X_pos: np.ndarray, X_neg: np.ndarray) -> "NearestMeanProbe":
        X = np.vstack([X_pos, X_neg])
        self.mu = X.mean(0)
        self.sd = X.std(0) + 1e-6
        Z_pos = (X_pos - self.mu) / self.sd
        Z_neg = (X_neg - self.mu) / self.sd
        self.w = Z_pos.mean(0) - Z_neg.mean(0)
        self.w /= np.linalg.norm(self.w) or 1.0
        self.b = -0.5 * (Z_pos.mean(0) + Z_neg.mean(0)) @ self.w
        # The score scale is the largest projection seen ON THE TRAINING SET, so
        # it is a property of the fit, not of the sample: refit and every score
        # moves. That is not a law of nature — it is a property of THIS head, and
        # B_2 shows the logistic head does not have it.
        proj = np.concatenate([Z_pos @ self.w, Z_neg @ self.w]) + self.b
        self.scale = float(np.abs(proj).max()) or 1.0
        return self

    def score(self, x: np.ndarray) -> float:
        z = (x - self.mu) / self.sd
        return float(np.clip((z @ self.w + self.b) / self.scale, -1, 1))


class LinearSVMProbe:
    """Soft-margin linear SVM: hinge + L2, plain subgradient descent.

    Same SHAPE as nearest-mean — one direction in a ~12-dim standardized space,
    no capacity to overfit — but the direction is set by the BOUNDARY points
    rather than by the centroid, which is what makes it stable across seeds.
    n~60, d~12, so "gradient descent" here is ~15 ms and needs no sklearn.
    """
    name = "linear SVM"

    def __init__(self, C: float = 1.0, iters: int = 2000, lr: float = 0.05):
        self.C, self.iters, self.lr = C, iters, lr

    def fit(self, X_pos: np.ndarray, X_neg: np.ndarray) -> "LinearSVMProbe":
        X = np.vstack([X_pos, X_neg])
        y = np.concatenate([np.ones(len(X_pos)), -np.ones(len(X_neg))])
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-6
        Z = (X - self.mu) / self.sd
        cw = np.where(y > 0, len(y) / (2 * (y > 0).sum()), len(y) / (2 * (y < 0).sum()))
        w, b = np.zeros(Z.shape[1]), 0.0
        for t in range(self.iters):
            act = (y * (Z @ w + b)) < 1
            lr = self.lr / (1 + t / 200)
            w -= lr * (w - self.C * ((cw * y * act) @ Z))
            b -= lr * (-self.C * float((cw * y * act).sum()))
        self.w, self.b = w, b
        self.scale = float(np.abs(Z @ w + b).max()) or 1.0      # fit-dependent, as above
        return self

    def score(self, x: np.ndarray) -> float:
        z = (x - self.mu) / self.sd
        return float(np.clip((z @ self.w + self.b) / self.scale, -1, 1))


class LogisticProbe:
    """L2-regularized logistic regression. THE ONE TO SHIP — but not for its AUC.

    On this data it ties the SVM (both are perfect on all five seeds), so accuracy
    is not the tiebreak. The tiebreak is the SCALE. This head's output is a
    PROBABILITY, so its range is a property of the model class, not of the
    training set: `scale` is 1.0 by construction and stays 1.0 across a refit.
    The nearest-mean and SVM heads normalise by max|projection| over the fit, so
    refitting them rescales every score in the survey (B_2).

    Read that carefully, because it is exactly the kind of claim design/20 exists
    to stop: the scale is FIT-INDEPENDENT, which is not the same as CALIBRATED.
    The fit is class-weighted to 50/50 and the slide is ~12% positive, so p=0.5
    does NOT mean "half the tiles like this one are hits." The number stops MOVING
    under a refit; it does not thereby start MEANING anything. That is why B_2
    ends on a rank-relative budget rather than on a probability.

    The L2 term is load-bearing, not hygiene: the classes here are linearly
    separable, and unregularized logistic weights diverge on separable data.
    """
    name = "logistic"

    def __init__(self, lam: float = 1.0, iters: int = 3000, lr: float = 0.1):
        self.lam, self.iters, self.lr = lam, iters, lr

    def fit(self, X_pos: np.ndarray, X_neg: np.ndarray) -> "LogisticProbe":
        X = np.vstack([X_pos, X_neg])
        y = np.concatenate([np.ones(len(X_pos)), np.zeros(len(X_neg))])
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-6
        Z = (X - self.mu) / self.sd
        cw = np.where(y > 0, len(y) / (2 * y.sum()), len(y) / (2 * (1 - y).sum()))
        w, b = np.zeros(Z.shape[1]), 0.0
        for _ in range(self.iters):
            g = cw * (1 / (1 + np.exp(-(Z @ w + b))) - y)
            w -= self.lr * ((g @ Z) / len(y) + self.lam * w / len(y))
            b -= self.lr * g.mean()
        self.w, self.b, self.scale = w, b, 1.0      # a probability needs no fit-derived scale
        return self

    def score(self, x: np.ndarray) -> float:
        z = (x - self.mu) / self.sd
        return float(1 / (1 + np.exp(-(z @ self.w + self.b))))


FewShotProbe = LogisticProbe          # what design/26 proposes to ship
HEADS = (NearestMeanProbe, LinearSVMProbe, LogisticProbe)


def _augment(img: np.ndarray) -> list[np.ndarray]:
    """5 examples -> 40 via the dihedral group. Microscopy has no canonical up.

    B_3 shows this is a NO-OP for the classical descriptor, and says why it is
    kept anyway. Do not delete it without reading B_3.
    """
    out = []
    for k in range(4):
        r = np.rot90(img, k)
        out.extend([r, np.fliplr(r)])
    return out


def _auc(scores: np.ndarray, truth: np.ndarray) -> float:
    """AUC by rank-sum. No sklearn."""
    n_pos = int(truth.sum())
    ranks = np.empty(len(scores))
    ranks[np.argsort(scores)] = np.arange(1, len(scores) + 1)
    return float((ranks[truth].sum() - n_pos * (n_pos + 1) / 2)
                 / (n_pos * (len(scores) - n_pos)))


def _one_slide(seed: int):
    """A fresh user, a fresh slide: 5 examples, 20 mined negatives, 150 survey tiles.

    Re-seeding the module RNG is what makes B_1 possible at all. The first draft
    of this spike ran ONE seed and reported its number as the finding.
    """
    global RNG
    RNG = np.random.default_rng(seed)

    examples = [_tile("mitotic") for _ in range(5)]
    # Negatives: microclaw does NOT need the user to supply these. The survey scan
    # itself is a bucket of unlabelled tiles that are overwhelmingly negative —
    # sample from it. (Here: 20 random non-example fields.)
    neg_pool = [_tile(RNG.choice(["empty", "interphase"])) for _ in range(20)]
    X_pos = np.array([features(a) for e in examples for a in _augment(e)])
    X_neg = np.array([features(n) for n in neg_pool])

    truth, tiles = [], []
    for _ in range(150):
        k = RNG.choice(["mitotic", "interphase", "empty"], p=[0.12, 0.44, 0.44])
        tiles.append(_tile(k))
        truth.append(k == "mitotic")
    return X_pos, X_neg, np.array([features(t) for t in tiles]), np.array(truth)


def section_b() -> FewShotProbe:
    print(SEP)
    print("B. Train on 5 example images. Rank 150 unseen tiles.")
    print(SEP)
    slides = [_one_slide(s) for s in range(5)]
    probe = _b1_the_head_is_the_variance(slides)
    _b2_what_survives_a_refit(slides)
    _b3_the_augmentation_is_a_no_op()
    return probe


def _b1_the_head_is_the_variance(slides) -> FewShotProbe:
    """B_1 — five seeds, three heads. The head is the whole variance.

    The first draft of design/26 fitted a nearest-mean probe on ONE seed, got
    AUC 0.994 / precision@k 0.95, and reported it as F2's finding. Run the same
    fit on five seeds and it is 0.926 +/- 0.102, with one seed at 0.727 where two
    of the top nineteen candidates are real. Seed 0 was the best of the five.

    That is the design/20 failure — a number that sounded right, stated as a fact,
    never measured twice — committed by the doc whose thesis is that you must not
    do that. It is kept here as the first thing section B prints.

    The fix costs nothing: the same 5 examples, the same descriptor, the same
    one-direction shape, a head with a margin. No new dependency, no GPU, ~15-30 ms.
    """
    print("\n  B_1  the same probe, five slides. Which head?")
    print(f"\n  {'head':<26} {'AUC':>15} {'precision@k':>15} {'fit':>8}")
    rows: dict[str, list] = {}
    for X_pos, X_neg, X_survey, truth in slides:
        n_pos = int(truth.sum())
        for Head in HEADS:
            t0 = time.perf_counter()
            p = Head().fit(X_pos, X_neg)
            t_fit = (time.perf_counter() - t0) * 1000
            s = np.array([p.score(x) for x in X_survey])
            order = np.argsort(-s)
            rows.setdefault(Head.name, []).append(
                (_auc(s, truth), float(truth[order[:n_pos]].mean()), t_fit))

    for name, r in rows.items():
        a, p_at_k, t = (np.array([x[i] for x in r]) for i in range(3))
        print(f"  {name:<26} {a.mean():.3f} +/- {a.std():.3f}"
              f"   {p_at_k.mean():.2f} +/- {p_at_k.std():.2f}   {t.mean():>6.1f} ms")

    nm = rows["nearest-mean (first draft)"]
    seed0, worst = nm[0], min(nm, key=lambda x: x[0])
    rank = sorted(nm, key=lambda x: -x[0]).index(seed0) + 1
    print(f"\n       nearest-mean, seed 0     : AUC {seed0[0]:.3f}  precision@k {seed0[1]:.2f}"
          f"   <- the number the first draft reported")
    print(f"       nearest-mean, worst seed : AUC {worst[0]:.3f}  precision@k {worst[1]:.2f}"
          f"   <- the number it did not look for")
    print(f"       seed 0 ranks {rank} of {len(nm)} for this head. The doc froze the seed,"
          "\n       reported it to 3 decimal places, and called it a finding.")
    print("       VERDICT: the HEAD is the variance, not the descriptor and not the"
          "\n       five examples. A margin fixes it for free. And one seed is not a"
          "\n       measurement — which is this doc's own thesis (design/20, design/21).")
    print("\n       CAVEAT, loudly: these classes differ BY CONSTRUCTION, so a perfect"
          "\n       AUC is a property of the synthetic data, not a promise about cells."
          "\n       The transferable claim is narrow: the head was the bottleneck here,"
          "\n       and fixing it is free. Measure the floor again on a real sample.")

    # Fit the shipped head once more on slide 0, for section C to time.
    X_pos, X_neg, _, _ = slides[0]
    return FewShotProbe().fit(X_pos, X_neg)


def _b2_what_survives_a_refit(slides) -> None:
    """B_2 — a refit rescales an unnormalised score. A RANK survives it.

    refine_roi_detector re-fits on the adjudicator's verdicts. What happens to an
    operating point chosen against the previous fit?

      * nearest-mean and SVM normalise by max|projection| OVER THE TRAINING SET,
        so a refit rescales every score in the survey. A threshold chosen by
        looking at fit #1's candidates is a different operating point on fit #2.
      * the logistic head emits a probability, whose scale is fixed by the model
        class. It does not move.

    But the honest ending is not "so ship logistic and keep your threshold." A
    fit-independent scale is not a CALIBRATED one: the fit is class-weighted 50/50
    and the slide is 12% positive, so p=0.5 means nothing about this slide. What
    actually survives a refit — for every head — is the RANKING. So the operating
    point that compiles is a BUDGET ("image the top k"), not a threshold.
    """
    print("\n  B_2  what survives a refit: the scale, or the ranking?")
    print(f"\n  {'head':<26} {'scale drift':>18} {'thr: n before/after':>21}"
          f" {'top-k set kept':>15}")
    for Head in HEADS:
        d_scale, d_thr, d_jac = [], [], []
        for X_pos, X_neg, X_survey, truth in slides:
            p1 = Head().fit(X_pos, X_neg)
            s1 = np.array([p1.score(x) for x in X_survey])
            # Adjudication: the top 12 crops come back labelled — exactly what
            # refine_roi_detector feeds into the refit.
            top = np.argsort(-s1)[:12]
            p2 = Head().fit(np.vstack([X_pos, X_survey[top][truth[top]]]),
                            np.vstack([X_neg, X_survey[top][~truth[top]]]))
            s2 = np.array([p2.score(x) for x in X_survey])

            # (a) an ABSOLUTE threshold, carried across the refit
            thr = 0.5 if p1.scale == 1.0 else 0.2      # a sane point for each scale
            d_thr.append((int((s1 >= thr).sum()), int((s2 >= thr).sum())))
            # (b) a RANK-RELATIVE budget: the top 20, before and after
            k = 20
            a, b = set(np.argsort(-s1)[:k]), set(np.argsort(-s2)[:k])
            d_jac.append(len(a & b) / k)
            d_scale.append((p1.scale, p2.scale))

        s_lo = np.mean([abs(b - a) / max(a, 1e-9) for a, b in d_scale])
        n1, n2 = np.mean([x[0] for x in d_thr]), np.mean([x[1] for x in d_thr])
        print(f"  {Head.name:<26} {s_lo*100:>15.1f} %   {n1:>8.1f} ->{n2:>6.1f}"
              f"   {np.mean(d_jac)*100:>12.0f} %")

    print("\n       The logistic scale does not drift, because a probability's range"
          "\n       is a property of the MODEL CLASS, not of the training set. But a"
          "\n       fit-independent scale is NOT a calibrated one: the fit is weighted"
          "\n       50/50 and the slide is 12% positive, so p=0.5 does not mean 'half"
          "\n       the tiles like this are hits'. The number stops MOVING; it does not"
          "\n       start MEANING anything.")
    print("       VERDICT: the ranking is what survives a refit, for every head. So"
          "\n       the operating point that can be COMPILED into a standalone hook is"
          "\n       a BUDGET ('image the top k'), not a threshold — and a budget can"
          "\n       only be evaluated once the survey's scores exist, which is an"
          "\n       argument for the two-pass flow over the online one (design/26 F7).")


def _b3_the_augmentation_is_a_no_op() -> None:
    """B_3 — the dihedral augmentation adds exactly zero information. Here it is.

    F2's first draft called this 'correct augmentation rather than the usual
    hopeful kind'. It is neither: it is a no-op. Every feature in the classical
    descriptor — percentiles, band energy, laplacian variance, blob count, mean
    blob sigma, nearest-neighbour spacing, bright fraction, SNR — is invariant
    under rotation and reflection, and rot90/fliplr are exact pixel permutations.
    So the eight 'augmentations' of one example are eight copies of one vector.

    It is not merely useless. It inflates the apparent positive count from 5 to 40
    while the effective sample stays 5, so a class-weighted head is weighted on a
    lie: it believes it has 40 positives against 20 negatives.

    Kept, not deleted — because it is a property of the DESCRIPTOR, not of the
    probe. It is a no-op for this rotation-invariant descriptor and genuinely
    load-bearing for a CNN/ViT embedding, which is not invariant to anything.
    """
    print("\n  B_3  does the dihedral augmentation add information?")
    img = _tile("mitotic")
    F = np.array([features(a) for a in _augment(img)])
    distinct = len(np.unique(F.round(9), axis=0))
    print(f"       augment() returns          : {len(F)} images")
    print(f"       distinct feature vectors   : {distinct}")
    print(f"       max spread across the 8    : {np.abs(F - F[0]).max():.1e}"
          "   (float noise)")
    print("       VERDICT: a NO-OP for the classical descriptor — every feature in"
          "\n       it is rotation- and flip-invariant. '5 examples -> 40' is 5"
          "\n       examples wearing a hat, and it fakes the class balance (40 vs 20)"
          "\n       that a weighted head then trusts. Keep it for the EMBEDDING"
          "\n       backend, which is invariant to nothing; it earns its keep there."
          if distinct == 1 else
          "       VERDICT: distinct vectors — the descriptor is NOT invariant. Recheck.")


# ---------------------------------------------------------------------------
# C. What it costs: once to fit, and then every tile
# ---------------------------------------------------------------------------
#
# Two different budgets, and the doc must not confuse them. The FIT is paid once,
# before the scan, with the stage parked — seconds there are free. The SCORE is
# paid on every tile, inside the dwell — anything over ~100-500 ms halves scan
# throughput. The first draft of design/26 quoted a fit cost this spike never
# measured; hence (i).


def section_c(probe: FewShotProbe) -> None:
    print(SEP)
    print("C. What it costs: to FIT once, and to SCORE every tile")
    print(SEP)

    # (i) The fit — what train_roi_detector pays, ONCE. Measured as we would SHIP
    # it: no dihedral augmentation, because B_3 shows it is a no-op for this
    # descriptor (augment_helps = False). So: featurise 5 examples + 20 mined
    # negatives = 25 tiles, then fit.
    t0 = time.perf_counter()
    X_pos = np.array([features(_tile("mitotic")) for _ in range(5)])
    X_neg = np.array([features(_tile("empty")) for _ in range(20)])
    t_feat = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    FewShotProbe().fit(X_pos, X_neg)
    t_fit = (time.perf_counter() - t0) * 1000
    print("\n  ONCE, at train_roi_detector (stage parked — seconds are free here):")
    print(f"    featurise 5 examples + 20 mined negatives : {t_feat:>7.0f} ms")
    print(f"    fit the probe                             : {t_fit:>7.1f} ms")
    print(f"    -> compile a detector                     : {(t_feat + t_fit) / 1000:>7.2f} s")

    # (ii) The score — what the hook pays on EVERY tile, inside the dwell.
    print("\n  PER TILE, in the scan (budget: ~100-500 ms of exposure + settle):")
    for h, w in ((256, 256), (1024, 1024), (2048, 2048)):
        img = RNG.integers(400, 3000, (h, w), dtype=np.uint16)
        # A big frame is downscaled before featurisation: we are choosing a
        # STAGE POSITION, not a pixel, so full resolution buys nothing here.
        t0 = time.perf_counter()
        for _ in range(3):
            small = img[:: max(1, h // 256), :: max(1, w // 256)]
            probe.score(features(small))
        dt = (time.perf_counter() - t0) / 3 * 1000
        print(f"    {h:>4d}x{w:<4d} uint16 -> decimate -> features -> score : "
              f"{dt:>6.1f} ms/tile")
    print("\n    (Run to run this moves by tens of percent on the same laptop —"
          "\n     quote it as a RANGE, well inside the dwell, not as a point.)")

    print("\n  For reference, and NOT measured here (torch is not installed):")
    print("    DINOv3 ViT-S/16 embedding, 224x224, CPU   ~50-150 ms/tile   [estimate]")
    print("    Cellpose-SAM segmentation, 512x512, CPU    ~2-10 s/tile     [estimate]")
    print("    Cellpose-SAM segmentation, 512x512, GPU    ~0.1-0.5 s/tile  [estimate]")
    print("    one Claude vision call on a thumbnail      ~1-3 s + $       [estimate]")
    print("  Measure these on the rig before quoting them to a user (design/20).")


# ---------------------------------------------------------------------------
# D. Does UMAP belong in the scoring chain?
# ---------------------------------------------------------------------------
#
# Edge Impulse's feature explorer is features -> UMAP -> SVM, and it is a
# reasonable thing to propose here: it is fast, it is few-shot, and it produces
# the 2-D picture F3 keeps asking for. Test it literally.
#
# umap-learn and sklearn are NOT microclaw dependencies and this section does not
# propose making them ones. It skips if they are absent, like section A does.


def section_d() -> None:
    print(SEP)
    print("D. UMAP in the scoring chain? (optional; needs umap-learn + sklearn)")
    print(SEP)
    try:
        import umap
        from sklearn.svm import SVC
    except ImportError:
        print("  umap-learn / scikit-learn not installed — skipping D.")
        print("  (Neither is a microclaw dependency, and D is the argument that")
        print("   neither should become one. Findings are in design/26 F2.)")
        return

    import warnings
    warnings.filterwarnings("ignore")

    for seed in range(3):
        X_pos, X_neg, X_survey, truth = _one_slide(seed)
        X = np.vstack([X_pos, X_neg])
        y = np.r_[np.ones(len(X_pos)), np.zeros(len(X_neg))]
        mu, sd = X.mean(0), X.std(0) + 1e-6
        Z, Zs = (X - mu) / sd, (X_survey - mu) / sd

        direct = SVC(kernel="linear", C=1.0, class_weight="balanced").fit(Z, y)
        auc_direct = _auc(direct.decision_function(Zs), truth)

        nn = min(15, len(Z) - 1)
        red = umap.UMAP(n_components=2, n_neighbors=nn, random_state=42).fit(Z)
        head = SVC(kernel="linear", C=1.0, class_weight="balanced").fit(red.embedding_, y)
        auc_umap = _auc(head.decision_function(red.transform(Zs)), truth)

        # What a SCANNER pays: transform() one tile, not a batch of 150.
        t0 = time.perf_counter()
        for i in range(10):
            red.transform(Zs[i:i + 1])
        t_one = (time.perf_counter() - t0) / 10 * 1000

        # The one that decides it: same data, same points, a different random_state.
        red2 = umap.UMAP(n_components=2, n_neighbors=nn, random_state=7).fit(Z)
        head2 = SVC(kernel="linear", C=1.0, class_weight="balanced").fit(red2.embedding_, y)
        s1 = head.decision_function(red.transform(Zs))
        s2 = head2.decision_function(red2.transform(Zs))
        rho = float(np.corrcoef(np.argsort(np.argsort(s1)),
                                np.argsort(np.argsort(s2)))[0, 1])

        print(f"\n  seed {seed}")
        print(f"    AUC  features -> SVM             : {auc_direct:.3f}")
        print(f"    AUC  features -> UMAP(2d) -> SVM : {auc_umap:.3f}")
        print(f"    UMAP transform, ONE tile         : {t_one:>6.1f} ms   <- a scanner pays this")
        print(f"    rank corr between two UMAP seeds : {rho:>6.3f}   (1.0 = same ranking)")

    print("\n  VERDICT: no, not in the scoring chain, and the seed column is why.")
    print("  UMAP is unsupervised, so a 12->2 bottleneck discards the very direction")
    print("  the labels identify — it COSTS AUC. It costs ms/tile a scanner does not")
    print("  have. And two fits of the SAME points with a different random_state")
    print("  produce rankings that barely correlate: the score is a function of the")
    print("  seed, not of the tile. F3 argues a refit that RESCALES is survivable by")
    print("  rescoring; a refit that REORDERS at random is not a classifier.")
    print("\n  But it earns the place Edge Impulse actually gives it: the REVIEW UI.")
    print("  F3's whole demand is that the operating point be chosen BY LOOKING, and")
    print("  a 2-D map of the survey — examples marked, candidates coloured by score —")
    print("  is exactly the artifact to hand the adjudicator. Inside")
    print("  review_roi_candidates, never inside ROIDetectorHook.")


# ---------------------------------------------------------------------------
# E. Where does the classical floor break, and what climbs back WITHOUT torch?
# ---------------------------------------------------------------------------
#
# The survey table lists a whole ladder of heavier backends above the classical
# floor — a frozen ViT embedding ("the upgrade path"), Cellpose, bioimage.io.
# Every one of them is justified in the doc BY ARGUMENT, because on section B's
# synthetic data the classical floor already scores a perfect AUC: there is no
# gap for a 2 GB torch dependency to climb. "It does not measure YOLOE, or
# DINOv2, against the classical floor" is in the doc's own list of things it
# does not do.
#
# We cannot run DINOv2/Cellpose/YOLOE here (torch is not installed, and that is
# the honest state of the rig too — section C). But we do not have to, to prune,
# because the question those rows sit on top of is answerable with numpy alone:
#
#   1. Does the classical floor have a REAL failure mode, or only an argued one?
#      Section B's classes differ by construction, so a perfect AUC there is a
#      property of the fake data. Build a concept the classical descriptor is
#      STRUCTURALLY blind to and watch it collapse.
#   2. When it collapses, does a still-free numpy/skimage descriptor climb back,
#      or is a learned embedding genuinely the only lever left?
#
# Three concepts, increasing in the kind of understanding they need. The probe
# (LogisticProbe, the shipped head) and the fit are held FIXED across all three;
# the ONLY thing that changes is the descriptor — which is exactly the seam the
# doc says every backend plugs into ("the only thing a backend supplies is
# image -> np.ndarray"). Everything below is numpy/scipy/skimage, already in
# pyproject.toml. No torch.


def _add_gaussian(img: np.ndarray, cy: float, cx: float, sigma: float, amp: float) -> None:
    yy, xx = np.mgrid[0:TILE, 0:TILE]
    img += amp * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * sigma ** 2))


def _add_segment(img: np.ndarray, cy: float, cx: float, angle: float,
                 length: float, width: float, amp: float) -> None:
    """A thin bright ridge: a fiber. Distance-to-segment, vectorised."""
    yy, xx = np.mgrid[0:TILE, 0:TILE]
    ux, uy = np.cos(angle), np.sin(angle)
    t = np.clip((xx - cx) * ux + (yy - cy) * uy, -length / 2, length / 2)
    d2 = (xx - (cx + t * ux)) ** 2 + (yy - (cy + t * uy)) ** 2
    img += amp * np.exp(-d2 / (2 * width ** 2))


def _fiber_tile(aligned: bool) -> np.ndarray:
    """A field of short fibers. ORIENTED-TEXTURE concept.

    Positive (aligned): every fiber points the same way (+/- 8 deg). Negative:
    orientations uniform-random. EVERYTHING ELSE IS MATCHED — same count, same
    length distribution, same width, same brightness, same positions in
    expectation. The two classes differ ONLY in orientation coherence, which is
    exactly the axis the classical descriptor throws away by being rotation- and
    flip-invariant (B_3). This is the classical floor's blind spot, by design.
    """
    img = RNG.normal(400, 15, (TILE, TILE))
    theta0 = RNG.uniform(0, np.pi)
    for _ in range(8):
        cy, cx = RNG.integers(30, TILE - 30, 2)
        ang = theta0 + RNG.normal(0, np.deg2rad(8)) if aligned else RNG.uniform(0, np.pi)
        _add_segment(img, cy, cx, ang, length=RNG.uniform(28, 40),
                     width=1.6, amp=RNG.uniform(1200, 2000))
    return np.clip(img, 0, 65535).astype(np.uint16)


def _triple_tile(collinear: bool) -> np.ndarray:
    """Three bright blobs. CONFIGURAL concept — relational, not textural.

    Positive: the three are collinear, equally spaced. Negative: an equilateral
    triangle. Both are matched on every term a GENERIC descriptor computes —
    3 blobs, identical sigma, identical brightness, and (this is the careful
    part) identical mean nearest-neighbour spacing: collinear ends/middle each
    have a neighbour at d, and an equilateral side is also d. The classes differ
    only in whether three points fall on a line — a fact no percentile, band
    energy, blob count, spacing or orientation statistic encodes.
    """
    img = RNG.normal(400, 15, (TILE, TILE))
    d = RNG.uniform(34, 44)
    theta = RNG.uniform(0, 2 * np.pi)
    cy, cx = RNG.integers(70, TILE - 70, 2)
    amp = RNG.uniform(1600, 2400)
    if collinear:
        ux, uy = np.cos(theta), np.sin(theta)
        for k in (-1, 0, 1):
            _add_gaussian(img, cy + k * d * uy, cx + k * d * ux, 2.6, amp)
    else:
        r = d / np.sqrt(3.0)                       # circumradius so side == d
        for a in (0.0, 2 * np.pi / 3, 4 * np.pi / 3):
            _add_gaussian(img, cy + r * np.sin(theta + a),
                          cx + r * np.cos(theta + a), 2.6, amp)
    return np.clip(img, 0, 65535).astype(np.uint16)


def _oriented_features(image: np.ndarray) -> np.ndarray:
    """Three ROTATION-INVARIANT texture-anisotropy scalars, from the gradient
    structure tensor. numpy/scipy only — the cheapest possible rung ABOVE the
    classical floor, and still `augment_helps = False` (a global rotation leaves
    all three unchanged), so it still compiles to a self-contained hook.

      * energy-weighted local coherence  (aligned fibers -> high)
      * global structure-tensor coherence (a field-wide dominant axis -> high)
      * 1 - normalised orientation entropy (one orientation dominating -> high)
    """
    from scipy import ndimage

    img = image.astype(np.float32)
    sig = np.clip(img - float(np.median(img)), 0, None)
    norm = sig / (float(sig.max()) or 1.0)
    gy, gx = np.gradient(norm)

    s = 4.0
    Axx = ndimage.gaussian_filter(gx * gx, s)
    Axy = ndimage.gaussian_filter(gx * gy, s)
    Ayy = ndimage.gaussian_filter(gy * gy, s)
    tr = Axx + Ayy
    coh = np.sqrt((Axx - Ayy) ** 2 + 4 * Axy ** 2) / (tr + 1e-9)
    mean_coh = float((coh * tr).sum() / (tr.sum() + 1e-9))

    Gxx, Gxy, Gyy = float((gx * gx).sum()), float((gx * gy).sum()), float((gy * gy).sum())
    global_coh = float(np.sqrt((Gxx - Gyy) ** 2 + 4 * Gxy ** 2) / (Gxx + Gyy + 1e-9))

    ang = np.arctan2(gy, gx) % np.pi
    hist, _ = np.histogram(ang, bins=18, range=(0, np.pi),
                           weights=np.sqrt(gx ** 2 + gy ** 2))
    p = hist / (hist.sum() + 1e-9)
    ent = float(-(p * np.log(p + 1e-12)).sum()) / np.log(18)

    return np.array([mean_coh, global_coh, 1.0 - ent], dtype=np.float64)


def _concept_slide(pos_fn, neg_fn, seed: int):
    """5 positive examples, 15 mined negatives, 100 survey tiles (half positive).

    Featurises each tile ONCE into both descriptors, so classical and
    classical+oriented are scored on the identical pixels — the comparison is the
    descriptor and nothing else.
    """
    global RNG
    RNG = np.random.default_rng(1000 + seed)

    def both(img):
        c = features(img)
        return c, np.concatenate([c, _oriented_features(img)])

    pos_ex = [both(pos_fn()) for _ in range(5)]
    neg_ex = [both(neg_fn()) for _ in range(15)]
    truth, tiles = [], []
    for _ in range(100):
        hit = RNG.random() < 0.5
        tiles.append(both(pos_fn() if hit else neg_fn()))
        truth.append(hit)

    def stack(exs, idx):
        return np.array([e[idx] for e in exs])
    return (stack(pos_ex, 0), stack(neg_ex, 0), stack([t for t in tiles], 0),
            stack(pos_ex, 1), stack(neg_ex, 1), stack([t for t in tiles], 1),
            np.array(truth))


def section_e() -> None:
    print(SEP)
    print("E. Where the classical floor breaks, and what climbs back without torch")
    print(SEP)

    concepts = [
        ("photometric  (section B's concept)", lambda: _tile("mitotic"),
         lambda: _tile(RNG.choice(["empty", "interphase"]))),
        ("oriented-texture (aligned fibers)  ", lambda: _fiber_tile(True),
         lambda: _fiber_tile(False)),
        ("configural   (collinear triple)    ", lambda: _triple_tile(True),
         lambda: _triple_tile(False)),
    ]

    print("\n  probe = LogisticProbe (shipped head), fixed. Only the DESCRIPTOR moves.")
    print(f"\n  {'concept':<37} {'classical':>18} {'classical+oriented':>20}")
    results = {}
    for label, pos_fn, neg_fn in concepts:
        cl, en = [], []
        for seed in range(3):
            (Xp_c, Xn_c, Xs_c, Xp_e, Xn_e, Xs_e, truth) = _concept_slide(pos_fn, neg_fn, seed)
            p_c = LogisticProbe().fit(Xp_c, Xn_c)
            p_e = LogisticProbe().fit(Xp_e, Xn_e)
            cl.append(_auc(np.array([p_c.score(x) for x in Xs_c]), truth))
            en.append(_auc(np.array([p_e.score(x) for x in Xs_e]), truth))
        cl, en = np.array(cl), np.array(en)
        results[label] = (cl.mean(), en.mean())
        print(f"  {label:<37} {cl.mean():>8.3f} +/- {cl.std():<5.3f}"
              f"  {en.mean():>10.3f} +/- {en.std():<5.3f}")

    print("\n  Read the rows as a LADDER, because that is what the survey table is.")
    print("\n  photometric      : classical is already perfect and the three extra")
    print("                     scalars neither help nor hurt. A learned embedding")
    print("                     here would be 2 GB of dependency buying nothing —")
    print("                     which is the doc's 'classical is the default' claim,")
    print("                     now with a floor UNDER it instead of only over it.")
    print("\n  oriented-texture : the classical floor COLLAPSES to chance — it is")
    print("                     rotation/flip-invariant (B_3), so 'all fibers point")
    print("                     the same way' is invisible to it BY CONSTRUCTION. And")
    print("                     a still-free numpy descriptor — three structure-tensor")
    print("                     scalars, no torch, still augment_helps=False, still")
    print("                     compiles to a self-contained hook — climbs most of the")
    print("                     way back. This is a WHOLE class of 'complex' concepts")
    print("                     (fiber alignment, tissue anisotropy, polarity) that")
    print("                     does NOT need the embedding rung. Prune the reflex.")
    print("\n  configural       : classical gets PARTIAL, CAPPED purchase (~0.78) and the")
    print("                     texture rung does not move it (~0.76). Note what that is")
    print("                     NOT: it is not chance, and the first draft of this section")
    print("                     PREDICTED chance and was wrong — 'are these blobs")
    print("                     collinear' leaks into generic stats, because collinear")
    print("                     blobs span a longer extent than a triangle and that")
    print("                     bleeds into band energy. But it plateaus far short of the")
    print("                     ~1.0 the free descriptors reach when the concept actually")
    print("                     lives in their space. THIS is the rung the embedding/")
    print("                     ilastik ladder is for — relational structure — and the")
    print("                     spike (torch absent) cannot climb it. What it CAN do is")
    print("                     set the bar a learned backend must clear: not 0.5, but")
    print("                     ~0.78. 'Measure DINOv2 someday' now has a number to beat.")
    print("\n  VERDICT: the ladder has THREE rungs, not two, and the middle one is free.")
    print("  photometric -> classical (numpy).  oriented/textural -> ENRICHED classical")
    print("  (three structure-tensor scalars, numpy, still augment_helps=False, still")
    print("  compiles).  relational/configural -> the learned rung, and only there — and")
    print("  it inherits a floor of ~0.78 from the generic descriptor, not a free win over")
    print("  chance. The survey's 'embed/cellpose' rows should be gated on a concept being")
    print("  ABOVE the texture tier AND beating that floor, measured, not reached for the")
    print("  moment classical dips.")
    print("  CAVEAT: these classes still differ by construction, and the configural")
    print("  leak is the reminder — a 'clean' synthetic concept still bleeds")
    print("  into generic stats. The narrow, transferable claim is about STRUCTURE, not")
    print("  cells: a rotation-invariant descriptor cannot see orientation, a cheap tensor")
    print("  can, and configuration caps both well short of textural or photometric ones.")


if __name__ == "__main__":
    probe = section_b()
    section_c(probe)
    section_d()
    section_e()
    print("\n" + SEP)
    print("Findings are written up in design/26-ml-roi-detection.md.")
    print("The event-queue question that used to be section A is now")
    print("design/24-event-queue-spike.py + design/24-event-queue-put-is-a-no-op.md.")
    sys.exit(0)
