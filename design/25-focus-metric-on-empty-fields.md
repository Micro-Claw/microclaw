# design/25 — The focus metric is inverted on empty fields

Split out of design/23 F6, which found it but is a document about redundant
imaging, not about focus. Source: the same Nestor stage-scan run
(`20260714_131941_microclaw_history_nestor_stage_scan.json`, msgs 33–55).

**The bug:** on a field with no signal, `focus_metric` *inflates*. The agent
ranked nine tiles by it, moved to the emptiest field on the grid, and called it
the sharpest. It was reading the tool correctly; the tool was wrong.

## Why it inverts

Every tool that reports `focus_metric` reports
`normalized_laplacian_variance` (`image_analysis.py:28`, surfaced via
`_focus_metric_payload`, `tools.py:847` and `:1382`) —

    var(laplace(I - bg)) / mean(|I - bg|)²

The denominator was added by design/14 to make the metric scale-free across
laser power and ROI, which it does. But on an empty field the denominator
collapses toward the noise floor while sharp read-noise keeps the numerator up,
so the ratio explodes. Synthetic fields matching the session's ROI and noise:

| field | `focus_metric` | SNR |
|---|---|---|
| **empty background + read noise** | **31.4** | 2.6 |
| real cell, out of focus | 0.53 | 5.8 |
| real cell, in focus | 0.47 | 8.6 |

An empty field scores ~60× "sharper" than a real cell.

> **Caveat on this table:** it is a synthetic reproduction — but it is now backed
> by a committed spike (`design/25-focus-metric-spike.py`, check 1), which both
> reproduces the inversion and pins down *why* the empty number is what it is. For
> pure read noise the metric is an amplitude-independent constant:
> `var(laplace(n)) / mean|n|² = 20 / (2/π) = 10π ≈ 31.4` (the 5-point Laplacian
> has sum-of-squares 20; `mean|n| = σ√(2/π)`). That is exactly the empty-field
> value above, and the value in the transcript (the empty x=150 column read
> 22.8–29.1; msg 39). The one quantity that stays merely indicative is the ~60×
> *ratio*: it depends on how dim the competing cell is, and the spike's brighter
> synthetic cell gives ~18×. The direction and the 10π empty constant are solid; a
> real-frame reproduction on the rig is still owed before a threshold ships.

Note also that `ImageStats.focus_metric` (`image_analysis.py:11`) holds the
**raw** `laplacian_variance`, and every tool overrides it. That field is dead.
A gate belongs where the normalized metric is actually computed — in
`normalized_laplacian_variance` and the payload builders — not in `ImageStats`.

## Intensity-only is the worst possible replacement

The obvious "just use brightness" fix is the one change guaranteed to make
things worse, and Royer et al. settle it with numbers. The supplement of
*Adaptive light-sheet microscopy* (Nat Biotechnol 2016, doi:10.1038/nbt.3708 —
Supp. Methods 2, Supp. Figs 3–7) benchmarks **30 image-quality metrics** on 24
synthetic focus stacks and 66 real light-sheet stacks with hand-annotated
ground-truth focal planes.

> "Only 5 metrics fail to produce a median error of zero: *Mean, Kurtosis,
> Kurtosis of differences, **Maximum**, and Normalized Haar wavelet transform
> Shannon entropy*... we find that **statistical image quality metrics perform
> worst**, correlative and differential metrics are tied in the middle, and
> spectral metrics achieve the lowest mean error."

Maximum intensity is one of the five worst of thirty — it cannot reach a median
focus error of zero even on *synthetic* stacks. Laplacian-type metrics are
*differential*, the middle tier:

| metric class | example | real-data median focus error | cost |
|---|---|---|---|
| spectral (DCT entropy) | **DCTS** — *best overall* | **0 (median), 320 nm mean** | 27 ns/px |
| differential | Tenengrad (Sobel) — best non-spectral | 250 nm | ~4.5 ns/px |
| differential | Laplacian variance — *what we use* | 250–810 nm | cheap |
| statistical | **Maximum**, Mean, Kurtosis | fails; worst class | cheap |

## Noise is the whole game — and the paper's low-pass fixes it, but not for us

> "the focus value response to a **noiseless** focus stack is remarkably accurate
> for almost all image quality metrics... **It is only after adding noise that the
> image quality metrics start to exhibit performance differences.**" (Supp. Fig. 3)

> "we add an image **downscaling preprocessing step** to non-spectral image
> quality metrics that emulates the low-pass filtering built into spectral
> metrics... this simple and convolution-less preprocessing step **restored the
> performance of most non-spectral image quality metrics in noisy data sets**."
> (Supp. Methods 2, Supp. Fig. 7)

That is exactly our failure mode. The Laplacian isn't broken in principle — it is
broken *on noise*, as the paper predicts, and the paper's own remedy is a cheap
low-pass pre-filter rather than a new metric. They match the support diameter to
the PSF footprint on the detector (`r_p = 1.5`, i.e. 3 px, for 16×/0.8 NA with
6.5 µm pixels) — which must be re-derived per objective, so we derive it from
`get_pixel_size` and the NA rather than hard-coding it.

> **But the paper's remedy does not transfer to *our* metric, and the spike
> proves it (check 2).** Royer's downscaling restores *raw* focus metrics by
> cutting the noise floor's absolute contribution. Ours is not raw: design/14
> already divided by `mean|I-bg|²` to make it scale-free — and a scale-free ratio
> is, by construction, blind to the rescaling `block_reduce` performs. Averaging
> 2×2 *independent* noise pixels yields one *independent* pixel at σ/2: still white
> noise, so the ratio stays pinned at 10π. The spike confirms the empty field
> reads ~31 at downscale 1, 2, 4, and 8 alike. The block mean is the single filter
> design/14's own normalization renders inert. This reshapes the fixes below: the
> low-pass is demoted, and the SNR gate — not the low-pass — is the fix for the
> bug.

## Fixes, in increasing order of ambition

**1. Low-pass before the Laplacian — but NOT the block mean, and NOT as the fix
for this bug.** The earlier draft made this fix 1 with "do this first." The spike
(check 2) refuted that. Because the metric is already scale-free (design/14),
`block_reduce` cannot move an empty field off its 10π pin — the spike shows ~31 at
every downscale factor. A *Gaussian* pre-filter (overlapping, so it correlates the
noise) does pull the empty field down, but it also crushes the cell's signal, and
the spike confirms no low-pass restores the empty-below-cell *ranking* on its own.
So the honest scope of a low-pass is narrow: a Gaussian may reduce the noise
sensitivity of the *focus sweep* (the within-stack question the paper actually
measured), but it is not the antidote to field-ranking, and the block mean is not
the filter to use for anything here. Downscale to the PSF footprint
(`factor = round(2 · 0.61λ/NA / pixel_size_um)`, floor 1) only in the Gaussian
form, and only if the sweep needs it — otherwise skip fix 1 entirely.

**2. Gate the metric on SNR — this is THE fix.** See design/23 F7, which puts a
single robust `snr()` in `image_analysis.py`. Below `min_snr`, sharpness is not a
meaningful quantity, so return "no measurement" rather than a big number, and
surface `focus_metric_valid: false` plus a warning in the tool payload so the
agent can never read a bare number out of context. The spike (check 4) shows the
gate cleanly separates the two populations: F7's `snr()` pins an empty field at
~2.58 (another amplitude-independent constant, `2.576 / (1.4826 · 0.6745)`) while
cells read 11–84, so `min_snr = 3.0` sits just above the empty floor with room.
With fix 1 demoted, this is not one of two co-equal fixes — it is the only thing
that stops an empty field outranking a cell. It answers *"is anything here at
all?"*, which is the question the Nestor run actually got wrong.

`run_autofocus` already does the right thing here: its flat-curve refusal
("contrast 0.02 < 0.15 — the sweep saw noise, not a focus peak") is what finally
told the agent the truth at msg 55. **The gate exists in the sweep and is missing
from the single-image statistic.** This just brings the two into line.

**3. Consider DCTS** (`-Σ |c| log₂|c|` over the normalized DCT coefficients within
the OTF support, Supp. Eq. 32) as the metric for `run_autofocus`. It is the paper's
winner — median error 0, mean 320 nm — at 27 ns/px, ~6× slower than Tenengrad but
still nothing next to a 50 ms exposure. The spike (check 8) timed the candidate at
**3.0 ms on a real 512×512 demo frame (~11 ns/px), i.e. ~11 ms at 1000×1000** —
faster than the paper's own 27 ns/px figure, and the cost objection is retired.
The spike's focus stack (check 5) confirms DCTS peaks at true focus.
`scipy.fft.dctn` gives it to us in a few lines. Royer's own fallback if speed ever
bites is **Tenengrad**, not Laplacian.

The same transform hands us the SNR estimate too:

> "an image composed exclusively of noise has a random DCT spectrum with uniformly
> distributed energy over all frequencies. In the presence of signal, computing the
> power ratio of signal versus noise provides a way of estimating the signal to
> noise ratio." (Supp. Methods 2, *Adjusting laser power*)

So one DCT *could* yield both the focus metric and the gate — but the spike
(check 8) found the catch, on a real frame: F7's `snr()` read **0.95** on the demo
pattern while `dct_snr` read **4010**. They do not merely differ in scale, they
disagree in *direction* — F7 says "essentially no signal," `dct_snr` says
"enormous." So `dct_snr` cannot be dropped into a gate whose `min_snr` was
calibrated against F7's `snr()`; adopting it means re-deriving `min_snr` on the
`dct_snr` scale, not reusing 3.0. This is exactly the "two functions named `snr`,
different scales" hazard below, now demonstrated rather than feared. DCTS is still
the version worth building for *focusing*; the shared-transform SNR is a second
decision, not a freebie.

## The caveat I must not paper over

This benchmark measures *focus error within a focus stack* — "which z-plane of this
specimen is sharpest." That is **not** the question that broke the Nestor run. At
msg 39 the agent was **ranking different fields against each other** — "which of
these nine tiles contains a cell" — and *no* metric in the benchmark, DCTS
included, is designed for that. Every stack in the paper contains a specimen in
every plane. Swapping in DCTS would make `run_autofocus` genuinely better and would
blunt the noise sensitivity, but it would **not** by itself stop an empty field
from outranking a cell. **The SNR gate is the fix for field-ranking; the metric
choice is the fix for focusing.** They are different problems and both are real.

## Code stubs

Signatures against the code as it stands today (`microclaw/image_analysis.py`,
`microclaw/tools.py`, `microclaw/autofocus.py`). Bodies are filled in where the
design settles the maths and left as `TODO` exactly where it does not — the
`min_snr` value and the `r_p` derivation are both rig measurements, and writing a
plausible number here would launder a guess into a constant.

### `image_analysis.py` — fix 1, the low-pass

> **Superseded by spike check 2.** The `block_reduce` body below is kept only to
> show what the earlier draft proposed and why it does not work: a block mean is
> scale-invariant on white noise, so it leaves the empty field's 10π value
> untouched (the spike confirms ~31 at every downscale). `psf_downscale_factor`
> stays useful, but if any low-pass ships it must be a *Gaussian* and it belongs
> in the focus sweep, not in this ranking-facing statistic. Do not ship the
> `block_reduce` line as written.

```python
#: Fallback optics when the objective's NA is unknown. 0.52 µm ≈ mid-visible
#: emission; NA 1.4 is the oil objective. Both are guesses and BOTH are wrong on
#: some rig — see "Where NA comes from" below, and prefer the calibrated value.
_DEFAULT_EMISSION_UM = 0.52
_DEFAULT_NA = 1.4


def psf_downscale_factor(
    pixel_size_um: float,
    wavelength_um: float = _DEFAULT_EMISSION_UM,
    na: float = _DEFAULT_NA,
) -> int:
    """Downscale factor that matches the PSF footprint on the detector.

    Royer et al. match the low-pass support to the diffraction limit rather than
    picking a blur radius: 2·(0.61λ/NA) is the Airy diameter in µm, and dividing
    by the sample-space pixel size gives it in pixels. They report r_p = 1.5
    (3 px support) for 16×/0.8 NA on 6.5 µm pixels.

    Returns >= 1; 1 means "already at or below the diffraction limit, do not
    downscale". pixel_size_um <= 0 (MM with no calibration) also returns 1 —
    an uncalibrated rig gets today's behaviour rather than a fabricated factor.

    Spike check 3: on the paper's own example (16×/0.8 NA, 6.5 µm px → 0.406 µm
    sample px) this returns 2, against the paper's 3-px support — same
    neighbourhood, within the ±1 the doc treats as acceptable. The formula is
    sane, but this only matters if fix 1 ships in its Gaussian form; the block
    mean is dead (see "Fixes"). The paper states a support *radius* and this
    returns a *downscale* factor; if a rig ever needs them to agree exactly, the
    paper wins and this becomes a lookup, not a formula.
    """
    if pixel_size_um <= 0:
        return 1
    airy_diameter_um = 2.0 * 0.61 * wavelength_um / na
    return max(1, int(round(airy_diameter_um / pixel_size_um)))


def normalized_laplacian_variance(
    image: np.ndarray,
    background: float | None = None,
    downscale: int = 1,
) -> float:
    """var(laplace(I - bg)) / mean(|I - bg|)² — scale-free w.r.t. illumination.

    ... (existing docstring) ...

    NOTE (spike check 2): this block-mean version does NOT work. Because the
    metric is scale-free, block-averaging white noise (which stays white noise at
    σ/2) leaves the empty-field ratio pinned at 10π ≈ 31.4 — the spike measured
    ~31 at downscale 1, 2, 4, 8. Kept here only to document the dead end. A
    Gaussian pre-filter is the only low-pass that moves the empty field, and it
    belongs in the focus sweep (autofocus.py), not in this statistic.
    """
    from scipy.ndimage import laplace
    from skimage.measure import block_reduce

    img = image.astype(np.float64)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    if downscale > 1:
        # WRONG (spike check 2): block_reduce is scale-invariant on white noise,
        # so it cannot deflate an empty field under a scale-free metric. The
        # paper's convolution-less argument holds for RAW metrics; design/14's
        # normalization defeats it here. If a low-pass ships at all, use a
        # Gaussian in the sweep, not this.
        img = block_reduce(img, block_size=downscale, func=np.mean)
    bg = float(np.median(img)) if background is None else background
    ...  # unchanged from here
```

### `image_analysis.py` — fix 2, the gate

`snr()` is **design/23 F7's** shared definition — `(p99.5 - bg) / (1.4826 · MAD)`,
one function used by both `compute_stats` and `detect_features`. It is a
prerequisite for this document, not a deliverable of it; do not write a second
one here. What design/25 adds is the gate on top of it:

```python
#: Below this SNR, "how sharp is this field?" has no answer, because there is
#: nothing in the field to be sharp. The spike (check 4) supports 3.0 as a
#: starting value: an empty field is pinned at SNR ~2.58 and synthetic cells read
#: 11–84, so any threshold in (2.6, 11.6) separates them and 3.0 sits just above
#: the empty floor. STILL A PLACEHOLDER for the rig, though — design/23 F7 is
#: explicit that it must be measured on real frames against the F7 snr()
#: definition, and it varies by objective and by sample. Note the value is on
#: F7's snr() scale; if fix 3's dct_snr is ever adopted for the gate this number
#: does NOT carry over (spike check 8: F7 0.95 vs dct_snr 4010 on one frame).
MIN_SNR = 3.0  # TODO(rig): calibrate. See design/23 F7.


class FocusScore(NamedTuple):
    """A focus metric that cannot be read out of context.

    `value` is meaningless when `valid` is False, so the two travel together and
    every payload builder must surface both. Returning a bare float is what let
    the Nestor agent rank an empty field as the sharpest tile on the grid.
    """
    value: float
    valid: bool
    snr: float
    reason: str | None      # why it is invalid, in words, for the agent to relay


def focus_score(
    image: np.ndarray,
    min_snr: float = MIN_SNR,
    background: float | None = None,
) -> FocusScore:
    """The gated focus metric. This is what tools should call.

    Just the gate now — spike check 2 removed fix 1 from this path. The low-pass
    does not deflate an empty field under a scale-free metric, so there is nothing
    for a `downscale`/`pixel_size_um` argument to do here; it was dropped. The gate
    is the whole fix: below min_snr there is no signal to be sharp about, so refuse
    to report a number the agent could rank.

    Mirrors what run_autofocus already does correctly — its flat-curve refusal
    ("contrast 0.02 < 0.15 — the sweep saw noise, not a focus peak",
    autofocus.py:122) is the same refusal, one level up. The gate exists in the
    sweep and is missing from the single-image statistic; this closes that.
    """
    bg = float(np.median(image)) if background is None else background
    s = snr(image, background=bg)                      # design/23 F7
    if s < min_snr:
        return FocusScore(
            value=0.0,
            valid=False,
            snr=round(s, 2),
            reason=(
                f"SNR {s:.1f} < {min_snr} — no signal in this field, so it has "
                f"no sharpness to measure. Do NOT compare this against other "
                f"fields; an empty field inflates the metric rather than "
                f"deflating it."
            ),
        )
    value = normalized_laplacian_variance(image, background=bg)
    return FocusScore(value=value, valid=True, snr=round(s, 2), reason=None)
```

### `tools.py` — surfacing it

Both payload builders, because both currently override the dead
`ImageStats.focus_metric` with a bare normalized float.

No `_pixel_size_or_zero` helper: the earlier draft fed pixel size to `focus_score`
for the low-pass, and fix 1 is dead (spike check 2), so `focus_score` no longer
takes it and nothing in this path reads it. (`get_pixel_size` remains its own tool;
this document just stops threading it through the metric.)

```python
def _focus_metric_payload(ctrl: MicroscopeController, image: np.ndarray) -> dict:
    """Focus metric stamped with the settings it is only comparable within, and
    with the SNR gate that says whether it is a measurement at all (design/25).

    ... (existing docstring) ...
    """
    score = focus_score(image)
    payload = {
        "focus_metric": _round_sig(score.value),
        "focus_metric_valid": score.valid,
        "snr": score.snr,
        **_metric_stamp(ctrl),
    }
    if not score.valid:
        payload["warning"] = score.reason
    return payload
```

`_metric_stamp` (`tools.py:828`) declares `"focus_metric_kind":
"normalized_laplacian_variance"`. With fix 1 dropped the metric is still
`normalized_laplacian_variance` — the string is no longer a lie about a low-pass —
but it is now *gated*, and a saved history should record that. Bump it to
`"normalized_laplacian_variance_gated"` (or `"dcts"` under fix 3) so the presence
of `focus_metric_valid`/`snr` is self-describing. The per-tile branch
(`tools.py:1382`, `protocol="snap"`) takes the same treatment:

```python
        score = focus_score(image)
        return {
            "position": pos_label,
            "status": "snapped",
            "saved": False,
            **marked,
            # No metric_valid_for stamp per tile: the grid shares one
            # ROI/exposure/binning ... (existing comment)
            "focus_metric": _round_sig(score.value),
            "focus_metric_valid": score.valid,   # ← the tile-ranking fix (design/25)
            "snr": score.snr,
            ...
        }
```

**This branch is the one that broke the Nestor run** — it is what
`run_tile_acquisition(protocol="snap")` returns per tile, and ranking those
`focus_metric` floats against each other is what walked the stage to the emptiest
field on the grid. `focus_metric_valid: false` on the empty tiles is the whole
fix for the ranking case, and it lands here.

### `autofocus.py` — fix 3, DCTS (optional, separate)

`sweep_autofocus` / `coarse_then_fine_autofocus` / `single_sweep_autofocus`
already take `metric_fn: Callable[[np.ndarray], float]` (`autofocus.py:82`, `:135`,
`:190`), defaulted to `normalized_laplacian_variance`. So DCTS needs no plumbing
— write the function and change the default:

```python
def dct_shannon_entropy(image: np.ndarray, otf_support_px: int | None = None) -> float:
    """DCTS — Royer et al.'s best-of-30 focus metric: -Σ |c| log₂|c| over the
    normalized DCT coefficients inside the OTF support (Supp. Eq. 32).

    Median focus error 0 on real light-sheet stacks (Laplacian variance:
    250–810 nm). Spike check 8 timed the candidate at ~11 ns/px on a real 512×512
    demo frame (3.0 ms), so ~11 ms at 1000×1000 — faster than the paper's 27 ns/px
    and trivial against a 50 ms exposure. scipy.fft.dctn does the transform, and
    spike check 5 confirms this body peaks at true focus across a stack.

    A blurry image concentrates energy in a few low-freq coefficients (peaked
    distribution → LOW entropy); a sharp specimen fills the support (flatter
    distribution → HIGH entropy). Restricting to the OTF support IS the low-pass:
    everything beyond the diffraction limit is noise by construction, which is why
    the spectral metrics did not need the downscaling preprocessing step. See
    design/25-focus-metric-spike.py:dct_shannon_entropy for the validated body
    (drop DC, normalize |c| within the support to a distribution, -Σ p·log₂p).
    """
    from scipy.fft import dctn
    ...


def dct_snr(image: np.ndarray) -> float:
    """SNR from the same transform: noise has a flat DCT spectrum, so the
    in-support / out-of-support power ratio estimates signal over noise
    (Supp. Methods 2, "Adjusting laser power").

    One transform COULD yield both the metric and its gate — but spike check 8
    found this is not a freebie. On a real demo frame F7's snr() read 0.95 while
    this read 4010: they disagree in scale AND direction. So if DCTS ships, this
    does not slot into the existing gate — MIN_SNR (calibrated on F7's scale) would
    have to be re-derived on the dct_snr scale. F7's snr() must stay the single
    definition used by compute_stats and detect_features, so this either replaces
    F7's body wholesale (and MIN_SNR is recalibrated) or does not exist. TWO
    functions named snr, returning different scales, is the design/20 failure class
    F7 was written to avoid — and the spike shows it is a live hazard, not a
    hypothetical.
    """
```

**Fix 3 does not close this document, and shipping it alone would be the wrong
call.** DCTS makes `run_autofocus` genuinely better at *"which z-plane is
sharpest"* — the question the benchmark measures. It does nothing for *"which of
these nine tiles contains a cell"*, which is the question that broke the run. Fix
2 is the one that must ship.

### Where NA comes from

**Only relevant if a low-pass ships.** With fix 1 demoted, the shipping fix (the
gate) needs no NA at all — so this section is now conditional on someone reviving a
*Gaussian* low-pass for the focus sweep. Do not build the calibration plumbing
below for the gate; it does not need it. Kept because if the sweep low-pass is
built, NA is still the blocker.

`psf_downscale_factor` needs λ and NA, and **neither is available from the MM
core** — there is no `get_numerical_aperture()`. The spike confirmed this on the
live bridge (check 7): probing the core for any aperture/NA accessor returned
nothing, so this is no longer an assumption. Three options, in preference order:

1. **Store it per objective in `calibration.py`.** It already keys a store by
   `affine_key(objective, binning)` (`calibration.py:85`), and NA is a property of
   exactly that key. An `optics` entry alongside the affine costs nothing and is
   asked for at the same moment (calibration time), by the same person, who is the
   only person who knows the answer.
2. Read the objective label and pattern-match `"60x/1.4"`. Cheap, and wrong the
   first time a lab names a turret position `"oil"`.
3. Fall back to `_DEFAULT_NA` and emit a warning in the payload.

(3) is the floor, not the plan: a wrong NA silently changes the factor by ±1,
which changes the metric, which is the class of quiet miscalibration this whole
document is about. Do (1).

## Field calibration correction (2026-07-20)

The first Windows bead-rig control showed that the synthetic empty-field estimate
does not transfer unchanged: confirmed dark frames scored SNR 3.00–3.07, so the
package value 3.0 marked them focus-valid. Treat `MIN_SNR` as an uncalibrated fallback,
not a scientific default. A usable gate requires multiple confirmed dark and
illuminated logs under matching objective, camera, ROI, binning, exposure, and channel
conditions. If those distributions overlap, calibration fails rather than choosing a
threshold. The observation record must state whether its gate came from an explicit
parameter, rig configuration, a calibration artifact, or the package fallback.

## On MM's autofocus plugin

Already reachable (`autofocus_mm_plugin`, a pre-coded hook), and where a lab has a
validated one it should be preferred for *driving Z* — that is what it is for. It
does not solve this problem. The harm here came from `focus_metric` as a **per-tile
comparison statistic used to rank fields in a survey**, which MM's autofocus does
not provide; and the plugin path runs arbitrary Java that bypasses the safety guard
and needs `plugins.allow_hardware_motion`. Keep the MM plugin for autofocus, and
fix microclaw's own metric. Different questions.
