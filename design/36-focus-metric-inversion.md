# design/36 — the focus metric was minimised at focus

Source: M5 session `20260804_140012_031193_microclaw_history.jsonl` (rig
evidence folder `m5-autofocus-fail`, with the `safety_config.yaml` in play).
Second occurrence of the same defect; the first was design/28 F2 (Nestor,
2026-07-17), which examined it and concluded it was not a metric-sign bug.

This document is the mechanism and the fix. The session it came out of is
recorded in [design/37](37-m5-autofocus-session-findings.md), whose Finding 1
this is, and which also covers the hardware-motion plugin wall the same session
hit and the one behavioural miss it exposed.

**It was a metric-sign bug.** This document establishes the mechanism, records
why the earlier analysis was wrong in a way that was hard to see, and fixes it.

---

## What the operator saw

They asked for an autofocus on a field they had already focused by eye, with the
640 nm laser on and camera-triggered. Signal was good — SNR 31.9,
`focus_metric_valid: true`, no saturation. Two sweeps ran:

| sweep | Z window | result |
| --- | --- | --- |
| ±10 µm, 2.5 µm coarse | 39.9 – 59.9 | not converged; best Z at the **upper boundary**, metric 18.8 |
| ±20 µm, 2.5 µm coarse | 29.9 – 69.9 | not converged; best Z at the **upper boundary**, metric 25.9 |

The operator then supplied the decisive datum: **manual best focus is the
current position, Z ≈ 49.9 µm.** At that Z the metric read **1.95 — the lowest
value anywhere in either sweep.** The metric was not merely noisy; it was
anti-correlated with focus, and widening the range made it worse, because a
wider window reaches more defocus and defocus scored higher.

Nothing moved. design/28 F1's boundary check held both times and restored the
entry Z, which is the only reason this cost an afternoon rather than a sample.
That check remains correct and is untouched here.

## Mechanism

`normalized_laplacian_variance` = `var(laplace(I - bg)) / mean(|I - bg|)²`,
with `bg` the per-frame median. Both halves fail, in opposite directions:

**The numerator is pinned to the noise floor.** A bare Laplacian is the most
noise-amplifying high-pass there is: white noise of standard deviation σ
contributes a constant `20σ²` to its variance, regardless of focus. Unless the
sample's detail is at the pixel scale AND bright, that constant is most of the
numerator. Measured across a full defocus series on cell-like structure, the
numerator moves from 1244 to 1223 — flat to within noise.

**The denominator is itself a focus measure, pointing the same way.**
`mean(|I - bg|)` is a contrast statistic, and contrast falls with defocus.
Worse, `bg` is the per-frame **median**, so as the sample blurs, the spread
light lifts the median to meet it and the deviation collapses faster still.
Same series: 55 → 21.

A flat numerator over a halving denominator is a metric that **rises with
defocus**. That is the entire defect, and `design/36-focus-metric-spike.py`
prints all three curves side by side so the arithmetic is visible rather than
argued.

The same mechanism is the root of **design/25** ("an empty field inflates the
metric"): an empty field has no contrast, so the denominator collapses to the
noise level and the ratio inflates. design/25 gated the symptom with an SNR
check. The gate was a good idea for its own reasons and stays, but it was
treating the same disease.

## Why design/28 F2 got the opposite answer

F2's reasoning was sound and its test passed. It argued that Laplacian variance
is polarity-insensitive (true), that a flux-conserving PSF concentrates
high-frequency energy as it tightens (true), and that `mean(|I - bg|)²` is
roughly total signal per unit area and does not collapse merely because signal
concentrates (**false, and this is the crux — the per-frame median makes it
collapse**). It then verified against a synthetic bright punctum on a dark
background, which scored correctly.

Two properties of that synthetic hid the defect, and both are worth naming
because they will recur:

1. **It was noiseless.** With no noise floor, the numerator is pure signal and
   carries the focus information the real numerator cannot.
2. **Where synthetics do add noise, they usually add it first and blur
   afterwards** — which smooths the noise away along with the signal. A camera
   cannot do this: the optics blur, then the sensor adds a floor that does not
   blur. The spike prints both orders on one field. Detector order: the metric
   climbs 0.39 → 1.83 with defocus. Naive order: it falls 0.40 → 0.0001, and
   looks perfect.

The lesson generalises past this metric: **a synthetic that models defocus
without modelling the detector will exonerate any high-pass metric.** The
regression tests added with this change use detector order, and say so.

F2's process instruction — do not ship an alternative metric until it is
compared against real Z-stacks with visual ground truth — was not met then and
is not fully met now (see "What is still owed" below). What has changed is that
the premise is no longer in doubt: the failure is reproduced offline, from
first principles, on four field types, and the live curve's shape is the one
the mechanism predicts.

## The fix

**`tenengrad` — `mean(|∇I|²)` over Sobel gradients — is the focus metric.**

Measured over defocus series on four field types (sparse puncta and extended
structure, each at full brightness and at 10–20× dimmer), scoring the argmax:

| field | old metric | tenengrad |
| --- | --- | --- |
| puncta, bright | focus (but U-shaped: turns around and climbs again) | **focus** |
| puncta, dim | **maximum defocus** | **focus** |
| extended, bright | **maximum defocus** | **focus** |
| extended, dim | **maximum defocus** | **focus** |

Two properties earn it the job. The Sobel kernel smooths across the differencing
axis, so the noise floor does not swamp broad structure the way a bare Laplacian
does. And there is no normaliser, so there is no focus-dependent denominator to
fight the numerator — a constant added to every pixel (camera pedestal, uniform
haze) differentiates away exactly.

Rejected alternatives, all of which reproduce the same inversion because their
denominators also collapse with defocus: `var(laplace)/var(I)`, and a
scale-free two-band ratio `LoG(σ=1)/LoG(σ=4)`. A Laplacian-of-Gaussian
(σ=1) is correctly signed everywhere tenengrad is, but has less dynamic range on
the hardest case (extended, dim: 1.06× versus 1.19×) and introduces a length
scale that would need per-objective calibration.

### The comparability trade, made explicitly

design/14 §10 introduced the normaliser to stop the model reading a laser-power
increase as a focus improvement (raw Laplacian variance rose 2.5× with laser
power and 3.4× with an ROI crop, at constant focus). That failure was real, and
tenengrad does not fix it: it scales with photon count.

**No per-frame normaliser can fix it.** Separating the camera pedestal from
uniform out-of-focus haze is not possible from a single frame, so every
available normaliser is a contrast statistic, and every contrast statistic falls
with defocus, and any denominator that falls with defocus inverts the metric.
The choice is not "normalised or raw" — it is "comparable across illumination,
or correct about focus". Correct about focus wins; a number that is comparable
between fields but points away from focus is worse than no number.

Comparability is therefore declared rather than computed:

- Tool payloads carry `metric_valid_for` (ROI, exposure, binning) and
  `focus_metric_kind: "tenengrad_gated"` — a self-describing stamp in the saved
  history, not an inference.
- The agent prompt carries the half the camera cannot report: illumination.
  It now states that the metric is *not* normalised, rather than implying it is.
- A Z sweep holds all of it fixed, which is exactly the domain the metric needs.

### Changed with it

- `run_autofocus` and all three sweep functions score with tenengrad.
- `FocusFeedbackHook` (timelapse Z-drift correction) had the worst version of
  the bug: it was jogging Z away from focus and reading the resulting rise as a
  recovery. It now scores **tenengrad over flux²**, where flux is
  `mean(I) - offset` and the offset is taken once from a low percentile of the
  first frame and then **held fixed**. Fixed is what makes it safe: a per-frame
  background estimate rises with defocus and would re-introduce the inversion,
  while a constant leaves the denominator responding only to real dimming. This
  preserves the bleaching insensitivity that the old comment there was right to
  want — a timelapse dims, and an uncorrected metric would read that as
  defocus and jog the stage for no reason.
- `compute_stats().focus_metric`, and so every snap and every tile of a survey
  grid. An empty field now scores **below** a field with signal instead of
  above it, so ranking tiles by focus_metric no longer picks the emptiest one
  even before the design/25 validity flag is consulted.
- `focus_invalid_warning` no longer says the metric inflates on an empty field,
  because it no longer does. The gate stays: a field with no signal has no
  sharpness, and the number that comes back is a reading of camera noise.

`normalized_laplacian_variance` and `laplacian_variance` remain exported and
score nothing. Saved hooks may import them, and the two live sweep curves are
only readable against the function that produced them. Their docstrings carry
the warning.

## What is still owed

**This fix has not been validated on a rig.** Everything above is offline: a
reproduction of the failure from first principles, agreement with the shape of
two live curves, and a replacement that is right-signed across four synthetic
field types. That is enough to justify the change and not enough to close it.

The rig gate is `design/36-gate-prompts.md`, on this branch. It is a single
sweep on the M5 field that failed, against the operator's own manual focus — the
one comparison neither previous session was able to make against a working
metric. Until it comes back, this document describes a defect that is understood
and a fix that is untested where it matters.

**Still not obtained: raw Z-stack frames.** design/28 F2 asked for a diagnostic
acquisition that retains every frame of a sweep so the curve can be re-derived
offline against visual ground truth. It still does not exist, and both sessions
would have been an afternoon shorter with it. `run_autofocus` retains the metric
curve but discards the frames. That is worth building and is not in this change.
