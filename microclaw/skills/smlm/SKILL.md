---
name: smlm
description: Plan and run SMLM workflows including dSTORM, PALM, PAINT, and DNA-PAINT.
---
# Single-Molecule Localization Microscopy (SMLM) reference (microclaw)

## What SMLM is

SMLM is a family of super-resolution techniques (PALM, STORM/dSTORM, PAINT/DNA-PAINT)
that achieve ~20–50 nm lateral resolution by imaging sparse, spatially isolated
fluorophores frame-by-frame and fitting each PSF to extract sub-pixel (x, y)
coordinates. Thousands to tens of thousands of raw diffraction-limited frames are
collected and then processed by external localization software to build a
super-resolution image from the accumulated localizations.

Microclaw's role is to:
  1. Help the user set up the correct acquisition parameters.
  2. Run the raw-frame stack with run_timelapse (interval_s=0 for max frame rate).
  3. Export the dataset with export_dataset_as_tiff so external software can analyse it.
  4. Optionally attach an observation-only hook for real-time density logging.

Microclaw does NOT perform localization fitting — that requires external software
such as ThunderSTORM (FIJI plugin), SMAP, DECODE, or Picasso (see Software section).

---

## Technique variants

### dSTORM (direct STORM) — most common for fixed cells
- Fluorophore: photoswitchable synthetic dye (Alexa Fluor 647 or Cy5 recommended
  for beginners). Requires a thiol-based photoswitching buffer (TRIS-buffered saline + 35 mM MEA
  + enzymatic oxygen scavenger such as GLOX).
- Two acquisition regimes — ask the user which they prefer:

  A) Slow STORM (preferred — highest data quality):
     - Pre-bleach: slowly reduce excitation power to ~0.2 kW/cm² and wait until
       the overall signal intensity in the ROI is no longer decreasing (~1–2 min,
       judged by eye on the live image).
     - Acquisition: 100 ms exposure, ~6 kW/cm² excitation power.
     - Frame count: ~80,000 frames.
     - Expected AF647 yield: ~8,000 photons/localization, localization
       precision ~5 ± 2 nm lateral.

  B) Regular STORM (faster, slightly reduced quality):
     - No separate pre-bleach; begin acquisition immediately at high power.
     - Acquisition: 30 ms exposure, ~20 kW/cm² excitation power.
     - Frame count: ~40,000 frames.
     - Expected AF647 yield: ~6,000 photons/localization.

- Optional 405 nm activation: the operator may pulse at low power when blinking
  density drops too low during acquisition. The operator gradually increases the
  pulse length and stops increasing at the maximum; if density is still dropping,
  the acquisition is near complete. Microclaw does not automate this feedback loop.
- Ask the user: channel name for main excitation, which STORM regime they want,
  whether a 405 nm activation channel is available, and whether the
  photoswitching buffer is in place.

### PALM / fPALM — for live cells or sparse labelling
- Fluorophore: photoactivatable or photoconvertible fluorescent protein
  (PA-GFP, PAmCherry, mEos family). No special buffer needed.
- Pre-acquisition: brief low-power 405 nm pulse to activate a sparse subset;
  image until bleached, then repeat.
- Frame count is lower than dSTORM (typically 5,000–20,000) because each
  molecule bleaches irreversibly after activation.
- Ask the user: whether a 405 nm activation channel is configured and what
  power the laser operates at.

### PAINT / DNA-PAINT — no photobleaching, unlimited reservoir
- Fluorophore: transiently binding dye (e.g., Cy3B for PAINT; short imager
  strands for DNA-PAINT). No photoswitching buffer needed.
- ON-state lifetime (τb) and OFF-state lifetime (τd) are set by dye/DNA
  concentration and strand length, not laser power. Both are calculable:
    - τb = 1/k_off, set by duplex length — a 9-bp duplex gives τb ≈ 500 ms, and
      each added base pair raises τb by roughly 10× (each removed one lowers it
      by roughly 10×). Match the camera exposure to τb.
    - τd = 1/(c_imager · k_on), with k_on ≈ 10⁶ M⁻¹s⁻¹ typical. At 10 nM,
      τd ≈ 100 s; a site is visited at least once with ~98% probability after
      t ≈ 4·τd, but useful images need multiple events per site (~33 min).
- Typical frame counts: start 7,500; full datasets 10,000–100,000+, acquisition
  can continue until sufficient sampling is achieved.
- Imager concentration is the most consequential knob and it cuts both ways: too
  low under-samples the structure, too high raises unbound-imager background AND
  causes cross-talk localizations — two nearby sites bound at once, fitted as one
  false spot between them.
- Ask the user: imager strand concentration and identity, docking-strand target.
- **Call `load_skill(name="dna-paint")` for the full protocol** — buffer recipes,
  the oxygen-scavenging system, strand design, sample prep, and the acquisition
  procedure. Its numbers supersede the DNA-PAINT figures in this document.

---

## Acquisition parameters

### Exposure time
- Match the fluorophore ON-state lifetime: typically 10–100 ms.
- For dSTORM (Alexa Fluor 647):
    - Slow STORM regime: 100 ms.
    - Regular STORM regime: 30 ms.
- For PALM fluorescent proteins: 30–100 ms.
- For DNA-PAINT: match τb — ~300 ms for the common 9-bp imager/docking duplex
  (τb ≈ 500 ms); 100–500 ms across usual duplex lengths. Longer τb allows more
  photons per event. See `load_skill(name="dna-paint")`.
- Use: run_timelapse(exposure_ms=<value>, ...)

### Frame interval
- Set interval_s=0 to acquire as fast as the camera allows (back-to-back frames).
- Do not use a nonzero interval for SMLM unless a bounded, predeclared per-frame
  `hook_action_plan` spans more than one frame and needs Python between exposures;
  that idle time slows acquisition without reducing background.

### Number of frames
- Fixed-cell dSTORM:
    - Slow STORM regime: ~80,000 frames. In a manually supervised acquisition,
      the operator may stop when the 405 nm pulse length maxes out.
    - Regular STORM regime: ~40,000 frames.
    - Minimum for a small structure (e.g., centriole): ≥10,000 frames.
- PALM: 5,000–20,000 frames.
- DNA-PAINT: start at 7,500 frames; full datasets 10,000–100,000+, more is better.
- If the user is unsure, start with 20,000 frames and check density.
- Use: run_timelapse(n_frames=<value>, ...)

### Channel selection
- Always confirm the channel name before starting.
  Use get_available_channels() then set_channel() to verify the active channel.
- For dSTORM: the main excitation channel (e.g., "TIRF-647" or "Cy5").
- Do NOT switch channels during SMLM acquisition unless doing sequential
  multicolour imaging.

### Illumination mode (TIRF vs. epi)
- TIRF (total internal reflection fluorescence) reduces background and improves
  SNR — strongly preferred for membrane-proximal targets (within ~200 nm of
  the coverslip).
- HILO (highly inclined) is a good alternative for targets slightly deeper
  in the cell.
- Wide-field (epi) is acceptable but will have higher background.
- Ask the user which illumination mode their microscope is configured for.
  In Micro-Manager this is usually a device property (e.g., "IlluminationMode"
  or a TIRF motor angle). Use list_device_properties / set_device_property to
  adjust if needed.

### 3D SMLM — astigmatic imaging
- A cylindrical lens pair (astigmatic lens) placed in the emission path encodes the
  z-position of each emitter via an asymmetric PSF: the PSF extends along x when the
  molecule is above focus and along y when below.
- The astigmatic lens is inserted/removed without realigning the emission path.
  In Micro-Manager this is typically a device property (e.g., a two-state device
  that moves the lens in/out). Confirm with the user before starting.
- z-range accessible: typically ±400 nm around the focal plane with good localization
  precision. The calibration (see PSF calibration below) defines the useable z-range.
- Achievable localization precision under ideal conditions: ~2 nm lateral, ~8 nm axial.
- PSF calibration is required before 3D fitting: acquire a z-stack (e.g., −1 µm to
  +1 µm in 20 nm steps) of sparse, well-separated fluorescent beads (~100 nm
  TetraSpeck or similar). Calibration is performed in SMAP (or equivalent) and
  produces a spline PSF model used for MLE fitting.
- For 3D SMLM: ensure a hardware focus lock is active — axial drift over a long
  acquisition will broaden the apparent z-distribution and cannot be fully corrected
  in post-processing.
- Ask the user: whether the astigmatic lens is in place and calibrated, and the
  device property name used to toggle it.

### Multicolor imaging
- Sequential multicolor: acquire one channel at a time, switching excitation laser
  and emission filter between rounds. Simpler but slower; prone to drift between
  channels.
- Simultaneous multicolor (preferred for co-localization): an image splitter
  (dichroic + relay optics) splits the emission into two spectral bands and images
  them side-by-side on a single camera chip. Both channels are captured in every
  frame with no time delay.
- Ratiometric dSTORM with far-red dyes (e.g., AF647 + CF680): two spectrally
  overlapping far-red fluorophores are excited by the same laser and discriminated
  by their intensity ratio in the two camera channels. This requires a calibrated
  channel transformation (affine mapping from channel 1 pixel coordinates to
  channel 2 coordinates).
- For dual-channel 3D fitting, a global PSF model that covers both channels is
  required (calibrated from the same bead stacks, fitting both channels jointly).
- Ask the user: number of colors, labelling strategy, and whether the image splitter
  is installed and aligned.

### Objective and pixel size
- Requires a high-NA objective (NA ≥ 1.4), typically 60× or 100× oil immersion.
- Effective pixel size at the sample should be ~100–110 nm
  (camera pixel / total magnification). Calibrate by imaging a single bead and
  moving the xy stage by known distances; do not rely on the nominal magnification.
  Example: 6.5 µm camera pixel / 61× total magnification ≈ 105 nm/pixel.
- Pixel size too large → undersampling; too small → fewer molecules per FOV.

---

## Machine-checked pre-acquisition checklist

Every row here is answered by CALLING THE TOOL, not by inspecting an image and
asserting the answer. A checklist the model can satisfy with vibes is not a
checklist: in a past session the "focus lock engaged?" item was answered with
"you confirmed focus looks fine at Z=45.2 µm", which is not the same claim.

| Check                  | Tool                                    | Pass condition                     |
|------------------------|-----------------------------------------|------------------------------------|
| Focus lock engaged     | get_focus_lock_state()                  | engaged == true                    |
| Trigger line armed     | run_timelapse(laser_slot=N)             | trigger mode/sequence accepted     |
| Blinking density       | find_features()                         | spot_density_per_um2 in [0.1, 1.0] |
| No saturation          | snap_and_analyze()                      | saturated_fraction == 0            |
| In focus               | run_autofocus() or snap_and_analyze()   | converged == true                  |
| Pixel size known       | get_pixel_size() / calibrate_stage_to_camera() | pixel_size_um > 0           |

Items a tool CANNOT check (buffer, BFP bubbles, astigmatic lens, pre-bleach)
must be asked of the user explicitly and confirmed in their reply.

---

## Recommended acquisition protocol (step-by-step)

0. Run the machine-checked checklist above; report each result to the user.
1. Confirm sample is in photoswitching buffer (dSTORM) or correct imaging medium.
2. Call get_system_state() to orient yourself.
3. Call get_available_channels() to confirm the excitation channel.
4. Set exposure: set_exposure(exposure_ms) or pass exposure_ms to run_timelapse.
5. Snap a wide-field image for reference: snap_and_analyze().
   - Check focus (Tenengrad metric; higher = sharper). If low, suggest run_autofocus.
   - Check intensity. For dSTORM it should be high (all fluorophores ON).
6. Remind the user to check the back focal plane (BFP) image for air bubbles in
   the immersion oil before starting. Air bubbles appear as dark occlusions in the
   BFP and will cause PSF distortions and poor localization. If bubbles are present,
   clean the objective and replace the oil.
7. If the microscope has a hardware focus lock (e.g., NIR laser + QPD), confirm it
   is engaged before starting by calling get_focus_lock_state() — a sharp image is
   NOT evidence the lock is on. This is critical for long acquisitions and 3D SMLM.
   Note run_autofocus refuses to sweep while the lock is engaged; disengage with
   set_focus_lock(false), focus, then re-engage.
8. (dSTORM only) Ask the user to perform the pre-bleach step manually in
   Micro-Manager or confirm they have already done so:
   - Slow STORM: Start the laser at low power (~0.2 kW/cm²) and watch until the average image 
    intensity drops to half the initial value. Once this happens, bump the power up again and
    wait again for intensity to again drop to half of the initial value. Ramp the power from
    ~0.2 kW/cm² up to the user-defined power value (~6 kW/cm²) for the acquisiton in
    ~5 of these steps. All the steps together should not take longer than 30 seconds - 2 minutes.
   - Regular STORM: skip pre-bleach; proceed directly at ~20 kW/cm².
9. (3D only) Confirm the astigmatic lens is inserted (3D mode active) and that
   a PSF calibration file is available for the localization software.
10. Confirm the number of frames with the user (default suggestions: slow STORM
    80,000; regular STORM 40,000; unsure → 20,000).
11. Ask for a save directory and dataset name.
12. Start acquisition (pass laser_slot on an EMU rig so the trigger pre-flight
    verifies the excitation will actually fire — a gated-off laser yields a
    silently blank dataset):
      run_timelapse(n_frames=<n>, interval_s=0, save_dir=<dir>,
                    channel=<ch>, exposure_ms=<ms>, name=<name>,
                    laser_slot=<slot from get_emu_laser_map>)
13. After completion, offer to export to TIFF for analysis:
      export_dataset_as_tiff(dataset_path=<path>, output_path=<tiff_path>)

---

## Drift correction (critical)

Sample drift during long acquisitions (~30 min) is the most common artefact.
Without drift correction, the reconstructed image will appear blurry even if
individual localizations are precise.

Recommended approaches (implemented in external software, not Microclaw):
- Fiducial markers: add 40–100 nm gold or fluorescent beads to the sample
  before mounting. They appear in every frame and allow sub-nm drift tracking.
  Tell the user to add fiducials if they haven't already.
- Cross-correlation drift correction: available in most localization software;
  does not require fiducials but is less accurate.

If the user has a hardware focus lock (e.g., NIR laser + QPD system, Nikon Perfect
Focus, Zeiss Definite Focus), check that it is active before starting acquisition.
In Micro-Manager this appears as a focus-stabilization device in the device list.
Expected focus lock performance for a well-aligned system:
  - Long-term axial drift: ≤200 nm/h (corrected by focus lock and post-processing).
  - Medium-term instability (10 min): ≤5 nm peak-to-peak.
  - Short-term vibrations: ≤5 nm peak-to-peak.
Larger instabilities usually indicate air currents around the microscope; enclose
the setup if possible.

---

## Density monitoring (optional hook)

For long acquisitions it is useful to track per-frame blinking density to detect:
  - Too many ON molecules (PSF overlap → poor localizations): reduce excitation
    power or 405 nm activation.
  - Too few ON molecules (acquisition proceeding too slowly): increase 405 nm
    activation power.

Observation-only density logging is supported on `run_timelapse`: a user-authored
`analyze_frame(image, metadata) -> HookResult | None` hook can measure and log each
frame. The shipped `snr_observer` is a reviewed built-in example of observation-only
behavior, not the callback shape to copy for a user-authored hook.

Fixed, predeclared property schedules are also supported with a bounded
`hook_action_plan` under a `property_envelope`. A per-frame plan spanning more than
one frame requires `interval_s > 0`: with zero, pycro-manager may hardware-sequence
the time axis, so no Python callback runs between exposures to apply an action.

**Current capability boundary:** on a fixed `run_timelapse`, `analyze_frame` may
propose image-driven `SetIlluminationPower` after the user explicitly authorizes an
`illumination_envelope` once before the run; no prompt occurs from the callback thread.
It is power-only (not shutter control), bounded by a percent ceiling and a budget of
increasing writes, and has no automatic final restoration: the device stays at the
last accepted value. With `interval_s=0` and more than one frame, images are still
analyzed, but writes land asynchronously with respect to exposures rather than between
chosen frames.

Image-driven `SetDeviceProperty` and `MoveNamedStage` are refused under a fixed plan;
those actions must be in a predeclared `hook_action_plan`. An image-driven conditional
stop is also refused. `run_adaptive_survey` is not a substitute for single-field
STORM: it walks a planned position list. Do not offer to write an adaptive STORM hook
until a single-field adaptive timelapse route exists.

---

## Post-processing (external software)

Microclaw produces an NDTiff dataset or a TIFF stack. The user must run
localization fitting in one of these tools:

| Software        | Platform    | Notes                                  |
|-----------------|-------------|----------------------------------------|
| ThunderSTORM    | FIJI plugin | Widely used, good for 2D dSTORM/PALM  |
| SMAP            | MATLAB/GUI  | State-of-art MLE, 3D, PSF calibration |
| DECODE          | Python/GPU  | Deep-learning, very fast               |
| Picasso         | Python/GUI  | DNA-PAINT specialist tool              |
| ZOLA-3D         | FIJI plugin | 3D PSF engineering                     |

Suggest the appropriate tool based on the technique:
- dSTORM 2D → ThunderSTORM or SMAP
- dSTORM/PALM 3D → SMAP or ZOLA-3D
- DNA-PAINT → Picasso
- High-throughput / GPU → DECODE

### SMAP filtering thresholds (recommended starting points for AF647 dSTORM)
After localization fitting, filter out poor localizations before rendering:
- Lateral localization precision (locprec): < 20 nm
- Relative log-likelihood (LLrel): > −1  (removes poorly-fitted PSFs)
- PSF size (PSFxnm): < 175 nm  (removes out-of-focus emitters, 2D data only)
- Frames filter: remove early frames where density may be too high, and late
  frames where signal is exhausted.
After filtering, run drift correction (redundant cross-correlation in SMAP) and
merge localizations from consecutive frames that originate from the same
blinking event (grouping).

### Expected data quality metrics for AF647 dSTORM
Use these to judge whether an acquisition is healthy:
- Photon count per localization: ~6,000–8,000 (regular / slow STORM respectively)
- Background per pixel: ~100 photons/pixel
- Mean on-time (average frames per blink event): 2–2.5 frames
- Peak localization precision: 5 ± 2 nm lateral; ~8–10 nm axial (3D)

### Biological validation benchmark: Nuclear Pore Complex (NPC)
The nuclear pore complex is the standard benchmark for SMLM performance because
its geometry is well-characterised by cryo-EM:
- Nup96 is present in 32 copies per NPC (16 cytoplasmic + 16 nucleoplasmic ring).
- Each ring has 8 corners; each corner contains 2 Nup96 proteins.
- Adjacent corners within a ring: ~12 nm apart; non-adjacent corners: ~42 nm apart.
- Ring diameter: ~107 nm.
- Axial separation between cytoplasmic and nucleoplasmic rings: ~50 nm
  (expected value in SMAP z-scaling calibration: 49.3 nm).
- Image NPCs closest to the coverslip (nuclear membrane areas that are flat and
  parallel to the coverslip give the sharpest rings with most NPCs in the same focal
  plane).
- At 60% labelling efficiency (typical), most NPCs will display 6–8 visible corners.
  Clearly circular rings with visible eightfold symmetry indicate good resolution.

### Reference standard for PAINT / DNA-PAINT: DNA origami rulers
DNA origami nanostructures are the gold-standard reference sample for DNA-PAINT
because they are chemically defined, commercially available (e.g., Massive
Photonics "GATTA-PAINT" series), and provide absolute distance calibration:
- Docking strands are placed at prescribed positions with sub-nm stoichiometric
  precision. Common ruler spacings: 20 nm, 40 nm, 80 nm, 160 nm.
- Each docking-strand site produces independent binding events; the number of
  distinct peaks confirms site occupancy and localisation precision.
- **Localisation precision check**: fit each docking-site cluster with a 2-D
  Gaussian; the σ of that fit is the experimental localisation precision (target
  ≤ 5 nm for DNA-PAINT with Cy3B or ATTO 655 imager strands).
- **Calibration**: measure the centre-to-centre distance between two sites and
  compare to the design value to detect x/y pixel-size errors.
- **Imager strand concentration**: 100 pM–10 nM, starting around 5 nM for a
  12-site structure, in a Mg-based imaging buffer (5 mM Tris-HCl, 10 mM MgCl₂,
  1 mM EDTA, 0.05% Tween 20, pH 8.0) with the PCA/PCD/Trolox oxygen-scavenging
  system mixed in ≥1 h before imaging. Lower concentration reduces background but
  increases τdark (time between binding events). Optimise for a τb / τdark ratio
  that keeps <10% of sites occupied simultaneously. Recipes and the rationale are
  in `load_skill(name="dna-paint")`.
- Origami passivation: BSA (1 mg/mL) + Pluronic F-127 (0.05%) in the imaging
  buffer reduces non-specific imager binding to the coverslip.
- TIRF illumination is strongly preferred to minimise background from free imager
  strands in solution.

---

## Common pitfalls and how to address them

| Problem                         | Cause                          | Fix                                          |
|---------------------------------|--------------------------------|----------------------------------------------|
| Blurry super-resolution image   | Drift not corrected            | Use fiducial beads or software drift corr.   |
| No blinking visible             | Pre-bleach not done / bad buf  | Confirm pre-bleach step; check MEA buffer    |
| Too many simultaneous emitters  | Excitation power too high      | Reduce laser power; reduce 405 nm activation |
| Too few localizations           | Acquisition too short          | Increase n_frames; check buffer freshness    |
| Artefactual clusters            | Multiple localisations/molecule| Use merge-blinks filter in analysis software |
| Low SNR                         | High background / epi mode     | Switch to TIRF; check EM gain setting        |
| PSF distorted / asymmetric      | Air bubble in immersion oil    | Check BFP image; clean objective; replace oil|
| Poor z-localization in 3D       | No PSF calibration / lens out  | Confirm astigmatic lens in path; recalibrate |
| Photon count lower than expected| Bad/acidified imaging buffer   | Prepare fresh buffer; check buffer age       |
| Low labelling efficiency        | Cell culture / reagent issue   | Fresh cell cultures; repeat sample prep      |

---

## Key questions to ask the user before starting

1. Which SMLM technique? (dSTORM / PALM / PAINT / DNA-PAINT)
   For DNA-PAINT, call `load_skill(name="dna-paint")` before planning parameters.
2. Which fluorophore and labelling strategy?
3. Is the photoswitching buffer / imaging medium in place? (for dSTORM)
4. Which excitation channel and laser power will be used?
5. Is TIRF or HILO mode available and configured? (preferred over epi for SNR)
6. Is a hardware focus lock active?
7. Are fiducial markers present for drift correction?
8. 2D or 3D acquisition? If 3D: is the astigmatic lens inserted and calibrated?
9. Single-color or multicolor? If multicolor: sequential or simultaneous (image splitter)?
10. Which dSTORM regime: slow STORM (~100 ms / 6 kW/cm² / 80k frames) or
    regular STORM (~30 ms / 20 kW/cm² / 40k frames)?
11. Has the back focal plane been checked for air bubbles in the immersion oil?
12. How many frames and what exposure time?
13. Save directory for the raw data?
