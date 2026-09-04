---
name: fluorescence-microscopy
description: Plan general fluorescence imaging - sample prep, objectives, sampling, channels, photobleaching, and trustworthy intensity comparisons.
---

# General fluorescence microscopy reference (microclaw)

## Scope

The general reference behind an imaging session: what makes a fluorescence image
*measurable* rather than merely pretty. It covers sample preparation, objective
choice, resolution and sampling, channel configuration, photobleaching, the
instrument faults that silently corrupt intensities, and the analysis and
statistics rules that keep a comparison honest.

**Confocal-specific configuration is deliberately omitted** — pinhole/Airy-unit
sizing, scan speed and pixel dwell time, point-scanning optical zoom, spectral
detectors. Microclaw drives camera-based acquisition through Micro-Manager and
cannot set those. If the user is on a point-scanning or spinning-disk system,
those settings stay in the vendor software; everything below still applies.

Numbers here are the sources', measured on their instruments. They are starting
points to confirm against the user's rig and sample, **never settings to apply
unasked**. Where a specialist skill exists — `smlm`, `dna-paint`, `htsmlm`,
`nikon-pfs`, `optical-paths` — its numbers supersede these.

---

## The premise

`Garbage in = garbage out`. The higher the performance of the microscope, the
more likely it is to reveal inadequate sample preparation. A microscope cannot
remove non-specific staining and will not make a weak signal brighter.

And a fluorescence intensity is comparable to another fluorescence intensity only
if everything between the fluorophore and the number stayed constant: the same
illumination power, the same exposure, the same focal plane, the same position in
a non-uniform field, the same detector settings, the same processing. Most of
this document is a list of the ways that silently fails to hold.

---

## 1. Sample preparation

### Coverslips and refractive index

- Modern objectives are designed for **170 µm** coverslips — #1.5, which really
  range 160–190 µm. #1.5H high-precision (±5 µm) exists and is worth it for
  high-resolution work. Any other thickness needs an objective with a
  correction collar.
- Check that only **one** coverslip is present; a stuck pair is a common cause
  of an inexplicably blurry image.
- Refractive-index mismatch between immersion medium and sample causes
  **spherical aberration**: axial blur and lost intensity, worsening with depth.
  It is usually not noticeable until >10 µm into the sample, so cells grown
  directly on the coverslip are largely spared. Grow cells on the coverslip
  where you can.

### Fixation, permeabilization, labeling

There is no standard protocol for any of the three; each must be optimized per
cell or tissue type, target and antibody. What to raise with the user:

- **Fixation** trades morphology against antigenicity — enough cross-linking to
  preserve structure, not so much that antibodies cannot bind or penetrate.
  Formaldehyde made from paraformaldehyde powder beats methanol-containing
  formalin for immunocytochemistry. Microtubules are better preserved with a
  little glutaraldehyde; cytoskeletal fixation often benefits from warm (37 °C)
  fixative. Polyclonal staining survives chemical fixation better than
  monoclonal.
- **Permeabilization** must be tuned too. Too little and antibodies reach only
  the top and bottom of a tissue section; too much and soluble targets are
  washed out or redistributed. Triton X-100 is harsher than saponin, whose
  effect is reversible and so must be present in *every* solution of the
  protocol.
- **Labeling**: lower antibody concentrations often stain *better* — specific
  binding is retained while non-specific binding drops. Many short washes beat
  a few long ones. Overnight at 4 °C can cut background where room temperature
  cannot.
- **Controls are not optional.** Minus-primary and isotype controls; a positive
  control (overexpression, or a system known to express the target) matters as
  much as a negative one when the antibody is unproven. The ideal negative
  control is knockdown of the target.
- Tissue thickness: 4–10 µm for widefield, 10–40 µm for optically sectioned
  imaging. Antibody penetration beyond 40 µm is hard and may need modified
  protocols, perfusion, or clearing.

### Fluorophore choice

- Use the brightest, most photostable dyes available: Alexa Fluor, cyanine,
  DyLight, silicon-rhodamine, Janelia Fluor. **Avoid flow-cytometry dyes**
  (fluorescein, phycoerythrin) — they bleach far too fast for imaging.
- Longer-wavelength (red/far-red) dyes suffer less autofluorescence and
  penetrate deeper, but **resolution is inversely proportional to wavelength**:
  the same tubulin looks sharper in AF488 than in AF647. Choose deliberately.
- **Chromatic aberration** — different wavelengths from one point focusing to
  different points — is worst when a blue-emitting dye is combined with others,
  and it destroys colocalization. Measure it with multicolour beads and correct
  for it. Few objectives are well corrected across the whole spectrum.
- Use a spectra viewer (Chroma, Semrock, Omega, Thermo Fisher) against the rig's
  actual lines and filters, which `get_system_state` and `list_config_groups`
  will report. Four fluorophores can usually be separated cleanly by sequential
  acquisition; five or more is a sample-prep, imaging *and* analysis problem
  that is often better solved by labeling two samples.

### Mounting media — a common source of "failed" experiments

Unexpected staining results are blamed on antibodies far more often than they
deserve. In [Jonkman2020], one triple-labeled sample mounted four ways gave four
different answers from the *same* antibody tubes:

- **Vectashield preserved DyLight 405 but strongly reduced AF647 and completely
  quenched AF488** — the AF488 result reproduced with a new bottle and two
  different secondaries.
- One bottle of 100% glycerol produced strong spurious nuclear staining; a
  second bottle of the same product did not.
- No single antifade suits all fluorophores, and published compatibility data is
  scarce. Test mountants against your dyes empirically.
- Hardening mountants flatten 3D structure and their RI changes as they cure.
  Glycerol-based or uncured mountants preserve morphology and colocalization
  better; seal non-hardening mounts with quick-drying coloured nail polish.
- **Do not use a mountant containing DAPI** — stain DNA as a separate step.
  DAPI also photoconverts to green fluorescence under extended UV.

If a result is unexpected, confirm the reagents before rejecting the biology.

### Live cells

- Environment first: temperature, CO₂, humidity. With a stage-top incubator and
  an immersion lens, the **objective acts as a heat sink** — it needs its own
  heater. Chamber humidification is usually inadequate; add wet tissue or fill
  spare wells with water so osmolarity does not drift as media evaporates.
- Use #1.5 coverslip-bottom dishes/chambers so a high-NA lens can be used at
  all. Plastic-bottom plates need long-working-distance, low-NA lenses and
  destroy polarization (so no DIC). Check plastic dishes are compatible with the
  immersion medium — some oils dissolve them.
- Vital dyes perturb physiology; find the **lowest** concentration that still
  shows the structure — often hundreds of times below the manufacturer's
  suggestion. Avoid DNA-intercalating dyes (Hoechst, DRAQ5) for long
  experiments; FP-histones or SiR-Hoechst are gentler.
- Fluorescent proteins: the newest and brightest is not automatically the right
  one — aggregation and mislocalization make older, tested variants such as
  EGFP the better choice for many studies. Monitor expression level;
  overexpression artifacts are real. SNAP/HALO tags carrying optimized dyes are
  better for light-intensive work.
- Shorter-wavelength light is considerably more phototoxic. Avoid blue-emitting
  dyes for live work where possible.
- Cells must be healthy *before* imaging. Transport stress is real — let cells
  recover near the microscope for a few hours. Check morphology, proliferation
  and death rate first, and carry a transmitted-light image of untreated cells
  as the reference for "normal".

### Quality control before the rig

Check every fixed sample on a simple widefield stand first: negative controls
dark, positive controls bright and specific, no antibody precipitates, no
rounded or blebbing cells. If the sample is suboptimal, start again — imaging
time spent on a bad sample is wasted twice.

---

## 2. Objectives

The **NA, not the magnification**, determines what the lens can do.

    NA = n · sin θ          n = refractive index of the immersion medium

- Double the NA → **twice** the resolution and **four times** the collected
  light. NA is printed next to the magnification (`20×/0.8`).
- High NA costs working distance. A 63×/1.4 oil lens has ~0.19 mm free working
  distance and can only image near the coverslip; a 32×/0.4 air lens reaches
  3 mm through a plastic-bottom dish at a quarter of the sensitivity.
- Higher NA also focuses the excitation into a smaller spot, so **irradiance at
  the sample goes up** — more bleaching and more phototoxicity.
- Oil is not always right. When imaging >10 µm into an aqueous or cleared
  sample, a water, glycerol or silicone-oil lens with slightly lower NA usually
  beats an oil lens with spherical aberration. Fixed cells in glycerol mountant
  under a high-NA *water* lens is the same mistake in reverse.
- A **correction collar** must be set for the actual immersion medium, coverslip
  thickness and temperature — better still, adjusted live for maximum sharpness
  and brightness.
- More magnification is not more resolution. A 20×/0.8 dry lens still resolves
  ~400 nm over a ~0.5 × 0.5 mm field — ample for nuclear-intensity work, and
  roughly ten times the field of a 63×.
- Check the lens is **clean** before blaming anything else. Dried oil from the
  previous user is a leading cause of a weak image. Front lenses are concave;
  cleaning takes repetition.

| Mag / NA | Immersion | FWD | Typical use |
|---|---|---|---|
| 20×/0.8 | air (n=1.0) | 0.55 mm | moderate-resolution multi-position work |
| 20×/1.0 | water (n=1.33) | 2.1 mm | intravital, thick live samples |
| 32×/0.4 | air | 3.1 mm | cell culture through plastic |
| 40×/0.95 (collar) | air | 0.25 mm | widefield cell culture |
| 63×/1.2 (collar) | water | 0.28 mm | live cells >10 µm deep |
| 63×/1.4 | oil (n=1.518) | 0.19 mm | fixed cells; live <10 µm from coverslip |

Microclaw generally cannot tell which lens is seated. `optical_path` in
`get_system_state` reports the motorized turret state only, and an unlabeled
position such as `4-Unknown` means the config author did not name it. **Ask.**
See `load_skill(name="optical-paths")`.

---

## 3. Resolution and sampling

Rayleigh's criterion, as a rule of thumb:

    r_xy ≈ 1.22 · λ / 2NA

- Example: λ ≈ 500 nm at NA 1.4 → ~200 nm lateral.
- **Axial resolution is 2–3× worse** than lateral — ~400–600 nm in that example.
- **Nyquist**: pixels should be **2–3× smaller** than the resolution limit, or
  than the smallest feature that must actually be resolved. For the example
  above, ~100 nm/pixel. If the feature of interest is larger than the
  diffraction limit, sample against the feature instead and save the photons.
- Apply the same rule in Z: 2–3 sampled steps per optical section thickness.
- Undersampling loses detail and forbids deconvolution; oversampling costs
  photons, time and disk with no gain.

Do not trust nominal magnification for the pixel size. Call `get_pixel_size`,
and where it is absent or suspect, `calibrate_stage_to_camera` — moving a bead
by known stage distances is the measurement; the label on the objective is not.

Resolution is a trade, not a goal. A higher-resolution image puts less signal in
each pixel, so it needs more light or more time — more bleaching, more
phototoxicity, slower acquisition, bigger files. Ask what the biological
question actually needs before maximizing anything.

---

## 4. Configuring channels and exposure

### Cross-talk

- **Sequential acquisition is the safe default** — one excitation line and its
  detection at a time. Reserve simultaneous acquisition for fast live dynamics
  where speed genuinely rules, and correct for cross-talk afterwards.
- Vendor wizards assume all fluorophores are equally bright, which is why
  "semi-sequential" schemes mislead: DAPI's emission extends to ~600 nm, and if
  it is much brighter than the red dye it bleeds through regardless of how
  well-separated the spectra look.
- Spectral unmixing needs single-labeled controls for accurate reference spectra
  and balanced labeling intensities. Treat an unmixed result as a claim to be
  validated, not a measurement.
- Save the configuration (or apply settings from a known-good image) so
  replicates on different days are actually comparable. `list_config_groups`
  and `set_config_preset` are how microclaw reaches a saved channel.

### Setting the levels

1. **Image the brightest sample first** — usually the positive control. The
   settings must span the full intensity range of the whole set, or the bright
   sample will clip later.
2. Start low on illumination and raise only as needed; turn on one channel at a
   time so the others do not bleach while you optimize.
3. Adjust exposure (`set_exposure`) and camera gain in preference to power where
   you can — light is the expensive resource.
4. **Never clip.** No saturated pixels, and no pixels driven to zero by an
   inappropriate offset. `snap_and_analyze` reports `saturated_fraction`; for
   any quantification, require it to be **0**. Contrast is adjusted afterwards
   in display; clipped data cannot be recovered.
5. Prefer 16-bit over 8-bit: more grey levels between the dim and bright
   features of the same field.
6. If a bright and a weak sample genuinely cannot share settings, change one
   linear parameter only (illumination power or exposure), **record both
   values**, and account for the ratio in analysis.
7. Keep the settings identical for every image that will be compared.

---

## 5. Photobleaching and phototoxicity

Bleaching happens mostly *before* the acquisition, while the field is being found
by eye. Measured in [Jonkman2020] with a metal-halide lamp at full power through
a 63×/1.4 oil lens: **Alexa Fluor 488 lost half its intensity in 3 s** and 75% in
10 s; after 60 s it was gone. That is an unknown, uncontrolled and unrecorded
scaling factor on every intensity that follows.

- Turn the lamp to its **lowest** setting, ~10% or lower with neutral-density
  filters for live cells. Dim the room lights and let your eyes adapt. This
  single act dominates every other mitigation.
- Close the shutter the moment you look away. `shutter_declared_illumination`
  is microclaw's route.
- Find cells in **transmitted light** (DIC, phase contrast) or in the red
  channel where sensitivity permits.
- Establish the settings on a **test region**, then move to a fresh, unexposed
  area to acquire the data.
- Photobleaching produces reactive oxygen species — bleaching and phototoxicity
  are the same event seen from two sides. Evidence of viability (normal
  morphology, continued division) is part of a live-cell result, not an optional
  extra.
- A transmitted-light channel is often a cheaper way to get cell boundaries or
  cell-cycle stage than another fluorophore. DIC does not attenuate fluorescence
  the way a phase ring does, but its analyzer and prism must be retracted for
  fluorescence acquisition, and DIC cannot work through plastic.

---

## 6. Instrument faults that corrupt intensities silently

These are why a careful experiment can still produce uncomparable numbers. None
of them announce themselves in the image.

| Fault | What it does | What to do |
|---|---|---|
| **Non-uniform illumination** | The same cell reads differently depending on where it sat in the field. Measured: 40% corner-to-centre on a wide-field high-NA lens, 60% at minimum zoom; 20–60% across older spinning-disk illuminators; ~10% even on a good system. | Image a uniform fluorescent slide or a saturated dye solution, apply flat-field/shading correction, and re-measure periodically — the profile drifts. Or restrict imaging to the centre of the field and tile. |
| **Focus drift** | On a sectioning system it changes intensity, not just sharpness. Most stands need **2–3 h** to stabilize after power-on; >1 µm per 10 min in the first hour is common. | Warm up 1–2 h, or leave the stand powered 24/7. Stabilize room temperature. Use a hardware focus lock for timelapse — but note locks rely on an RI mismatch and are often incompatible with fixed samples in hardened mountant under oil. See `load_skill(name="nikon-pfs")`. |
| **Illumination power instability** | Fluctuations >10% are ordinary even after an hour's warm-up; one 488 nm line varied ~12% continuously. Power at the same nominal wavelength varies **50-fold between instruments**, and can change **twofold** after a laser is replaced or the system is serviced — usually unmeasured and unrecorded. | Measure through the objective with a power meter and record the value with the data. Re-measure after any service. Never compare raw intensities across instruments, or across a service event, without it. |
| **Jitter and stripes** | Periodic distortion; invisible on punctate labeling, ruinous for measurement. | Check the table is floating and nothing with a fan sits on it; check for a loose stage or objective and for cables under tension; dim room lights (mains at 50–60 Hz shows up in sensitive detection); suspect electronics and drivers last. Reveal jitter with linear structures (actin), stripes with a uniform slide. |

The general lesson is the one microclaw applies to hardware everywhere: **a
reading that looks plausible is not a reading that is right.** A number that has
not been measured against a standard is not evidence.

---

## 7. Weak or blurry image — where to look

Work outward from cheapest to most expensive. Ask first: microscope, or sample?

**Microscope**

- Everything powered on and initialized? Restart the software if a component may
  not have initialized.
- Trace the light path from source to sample to detector. Motorized elements are
  in `optical_path`; **manual** ones — a prism slider, a filter cube at a
  detent, a closed field diaphragm, a condenser out of position — are invisible
  to software and are the usual culprit for a black frame with correct readings.
  See `load_skill(name="optical-paths")`.
- Objective rotated fully into position? Clean? Damaged (oil inside,
  scratched)? A spring-loaded front element stuck compressed — often from
  focusing near the edge of a stage insert — brings the sample into focus with
  severe aberration.
- Correct immersion medium, no air bubbles, correction collar set?
- A DIC polarizer left in the emission path will dim everything.
- Illumination power and detector gain at sane levels? Has the source warmed up,
  or is it dying? Compare a power-meter reading against previous ones.
- Does the same sample look right on another objective, or another stand?

**Sample**

- #1.5 coverslip, exactly one of them?
- Does a known-good test slide image normally with the same settings? This is
  the fastest single discriminator.
- How deep are you imaging — within the working distance, and is the immersion
  RI matched at that depth?
- Did the label work? Compare primaries and secondaries; check penetration;
  check mountant/antifade compatibility (§1).
- For live cells: did transfection work? FP signal can quench on fixation.
- Is the sample old, already photobleached, stored warm or lit, or has the
  coverslip slipped? Fluorophores diffuse away from their targets over time.

---

## 8. Planning the experiment

- **Run a pilot.** Take a small sample all the way through to the final graph
  *before* committing to the full experiment. It exposes the parameter that
  needs changing, gives an estimate of the effect size, tells you how many
  fields you will need, and identifies the brightest condition so acquisition
  settings can be fixed for the whole set. Acquisition, analysis and design are
  a feedback loop, not a pipeline.
- **Remove selection bias.** Your eye will find the fields that support the
  hypothesis. Have a colleague code the slides so acquisition and analysis stay
  blinded until they are finished.
- **Remove field-of-view bias.** Image whole structures or whole sections rather
  than choosing regions — a motorized stage with tiling and stitching
  (`run_tile_acquisition`), or thin sectioning plus whole-slide scanning.
  Dropping to a lower magnification is usually a better trade than picking
  fields by hand.
- Decide the sampling scheme before acquiring, not after.

---

## 9. Analysis, presentation and statistics

**Integrity rules — these decide whether a comparison is admissible.**

1. Always keep the original, unmanipulated image **and its metadata** in the
   vendor format. Converting to plain TIFF usually loses the metadata; use
   OME-TIFF if you must convert. JPEG is for slides, never for analysis.
2. Every image being compared — visually or numerically — must be processed by
   **exactly the same steps**, including display contrast and thresholds. Or use
   the same auto-threshold algorithm everywhere, which at least adapts
   consistently.
3. Non-linear adjustments (gamma) treat dim and bright objects differently. Use
   sparingly and **state it in the legend**.
4. Never edit part of an image selectively. Blemishes are data.
5. Linear smoothing (Gaussian low-pass) before analysis is usually fine and
   often helps.
6. Deconvolution helps 3D data, does little for a single plane, and needs a
   correct PSF, SNR and background estimate. Use identical parameters across all
   images, never on undersampled data, and watch for mottled artifacts.
7. Archive raw and processed data for 5–10 years per your institution's policy.
   OMERO is the standard institutional option; the BioImage Archive is the
   public one.

Tools: Fiji/ImageJ (BioFormats reads vendor formats and metadata; macros make
repetitive analysis reproducible), CellProfiler for pipelines over large image
sets, QuPath/Halo/VisioPharm for whole-slide tissue work, napari and
scikit-image for Python, Imaris/Amira/Arivis for heavy 3D. A saved pipeline is
itself evidence — it can be inspected by reviewers.

**Statistics**

- Distinguish **experimental replicates** (repeated on different days, fully
  independent) from **technical replicates** (the same preparation repeated in
  one run). Technical replicates check preparation consistency; they never
  substitute for experimental ones. Three experimental replicates is a minimum.
- Biological variability is large — 40% experiment-to-experiment and 45%
  cell-to-cell in the worked example. Plan for it.
- **~30 measurements per condition** characterizes a distribution well; 10
  begins to show it and can already resolve a real effect; 3 cannot represent it
  at all.
- Plot **individual points over a box plot**, not a bar graph — the reader can
  then judge spread, outliers and clustering. Do not drop outliers unless there
  is an identified flaw in collection or analysis, and then use a formal test.
- Be explicit about what *n* is: experiments, cells, or objects. The same data
  gives different P values under each.
- Convention: `*` P<0.05, `**` P<0.01, `***` P<0.001. P<0.05 is the usual
  threshold for biology, P<0.01 is safer — and an increasing number of
  statisticians prefer reporting the P value without a significance claim.

---

## 10. Colocalization — read before promising it

Colocalization shows that two labels are **near** each other, bounded by the
resolution of the system — for a diffraction-limited system, a few hundred
nanometres laterally and worse axially. It does **not** show interaction. An
indirect antibody puts its fluorophore up to 20 nm from the epitope on its own.
Confirmation requires FRET (5–10 nm), fluorescence cross-correlation
spectroscopy, a proximity ligation assay (~40 nm), or biochemistry.

*False positives* come from: antibody cross-reactivity; excitation or emission
cross-talk (worst when the two channels differ wildly in brightness); high
background or autofluorescence; hardening mountant compressing the sample in Z;
insufficient axial resolution; low NA; spherical aberration; undersampling; and
analysis errors — unexcluded background pixels, wrong thresholds, or
pixel-by-pixel correlation where object-based colocalization was appropriate.

*False negatives* come from: one protein far more abundant than the other;
incomplete antibody penetration; badly mismatched channel brightness; poor SNR;
chromatic aberration; misaligned optics; and the same analysis errors.

Widefield's lack of optical sectioning makes it generally unsuitable for
colocalization: signals from different depths land in the same pixel. Say so
rather than producing a number.

---

## 11. Machine-checked pre-acquisition checklist

Answer each row by **calling the tool**, not by looking at an image and
asserting the answer.

| Check | Tool | Pass condition |
|---|---|---|
| Pixel size known and measured | `get_pixel_size` / `calibrate_stage_to_camera` | `pixel_size_um > 0`, and sampled per §3 |
| No clipping at these settings | `snap_and_analyze` on the **brightest** sample | `saturated_fraction == 0` |
| In focus | `run_autofocus` or `snap_and_analyze` | `converged == true` / sharpness metric peaked |
| Focus lock engaged (timelapse, 3D) | `get_focus_lock_state` | `engaged == true` |
| Channel is the intended one | `get_available_channels`, `set_channel` | active channel confirmed by name |
| Exposure recorded | `get_exposure` | matches what was decided, and is identical for every compared image |
| Light path as expected | `get_system_state` → `optical_path` | motorized devices in the intended positions |

Items no tool can check — coverslip number and thickness, mountant, immersion
medium and bubbles, correction collar, manual light-path elements, warm-up time,
illumination power in mW, sample health — must be **asked** and confirmed in the
user's reply. Do not tick them silently.

---

## 12. Key questions to ask before starting

1. Fixed or live? If live: incubation, objective heater, humidity — and what is
   the viability control?
2. What is the biological question, and what must actually be measured to answer
   it? (Intensity, count, distance, dynamics, colocalization?)
3. Which fluorophores, and do their spectra fit this rig's lines and filters?
4. Coverslip thickness and mounting medium — and has this mountant been checked
   against these dyes?
5. Which objective is seated, and is it the right immersion for this sample
   depth?
6. What is the smallest feature that must be resolved? (Sets the pixel size and
   the objective, per §3.)
7. Single or multi-channel? If multi: sequential, and in what order?
8. Which is the brightest sample in the set, and can it be imaged first?
9. How long has the stand been powered on? (Focus drift, §6.)
10. Has illumination power been measured recently, and is it recorded with the
    data?
11. How many fields, cells and replicates — and is field selection blinded?
12. Where does the data go, and what will analyse it?
