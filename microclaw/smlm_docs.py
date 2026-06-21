SMLM_REFERENCE = """
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
  4. Optionally attach a hook for real-time density feedback or adaptive 405 nm control.

Microclaw does NOT perform localization fitting — that requires external software
such as ThunderSTORM (FIJI plugin), SMAP, DECODE, or Picasso (see Software section).

---

## Technique variants

### dSTORM (direct STORM) — most common for fixed cells
- Fluorophore: photoswitchable synthetic dye (Alexa Fluor 647 or Cy5 recommended
  for beginners). Requires a thiol-based photoswitching buffer (PBS + 100 mM MEA
  at pH 7.4 + enzymatic oxygen scavenger such as GLOX or PCA/PCD).
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

- Optional 405 nm activation: pulse at low power to increase the localization rate
  when blinking density drops too low during acquisition. Increase pulse length
  gradually; stop increasing when maximum pulse length is reached and density
  is still dropping (acquisition is near complete).
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
  concentration and strand length, not laser power.
- Typical frame counts: 10,000–100,000+, acquisition can continue until
  sufficient sampling is achieved.
- Ask the user: imager strand concentration and identity, docking-strand target.

---

## Acquisition parameters

### Exposure time
- Match the fluorophore ON-state lifetime: typically 10–100 ms.
- For dSTORM (Alexa Fluor 647):
    - Slow STORM regime: 100 ms.
    - Regular STORM regime: 30 ms.
- For PALM fluorescent proteins: 30–100 ms.
- For DNA-PAINT: 100–500 ms (longer τb allows more photons per event).
- Use: run_timelapse(exposure_ms=<value>, ...)

### Frame interval
- Set interval_s=0 to acquire as fast as the camera allows (back-to-back frames).
- Do NOT use a non-zero interval for SMLM — idle time wastes acquisition time
  without reducing background.

### Number of frames
- Fixed-cell dSTORM:
    - Slow STORM regime: ~80,000 frames (acquire until 405 nm pulse length maxes out).
    - Regular STORM regime: ~40,000 frames.
    - Minimum for a small structure (e.g., centriole): ≥10,000 frames.
- PALM: 5,000–20,000 frames.
- DNA-PAINT: 10,000–100,000 frames; more is better.
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

## Recommended acquisition protocol (step-by-step)

1. Confirm sample is in photoswitching buffer (dSTORM) or correct imaging medium.
2. Call get_system_state() to orient yourself.
3. Call get_available_channels() to confirm the excitation channel.
4. Set exposure: set_exposure(exposure_ms) or pass exposure_ms to run_timelapse.
5. Snap a wide-field image for reference: snap_and_analyze().
   - Check focus (Laplacian variance metric). If low, suggest run_autofocus.
   - Check intensity. For dSTORM it should be high (all fluorophores ON).
6. Remind the user to check the back focal plane (BFP) image for air bubbles in
   the immersion oil before starting. Air bubbles appear as dark occlusions in the
   BFP and will cause PSF distortions and poor localization. If bubbles are present,
   clean the objective and replace the oil.
7. If the microscope has a hardware focus lock (e.g., NIR laser + QPD), confirm it
   is engaged before starting. This is critical for long acquisitions and 3D SMLM.
   In Micro-Manager, check the focus stabilization device in the device list.
8. (dSTORM only) Ask the user to perform the pre-bleach step manually in
   Micro-Manager or confirm they have already done so:
   - Slow STORM: reduce laser power to ~0.2 kW/cm², wait until signal is no longer
     decreasing (~1–2 min), then switch to acquisition power (~6 kW/cm²).
   - Regular STORM: skip pre-bleach; proceed directly at ~20 kW/cm².
   Do NOT automate pre-bleaching — high-power laser control is outside the
   safety-guarded parameter range.
9. (3D only) Confirm the astigmatic lens is inserted (3D mode active) and that
   a PSF calibration file is available for the localization software.
10. Confirm the number of frames with the user (default suggestions: slow STORM
    80,000; regular STORM 40,000; unsure → 20,000).
11. Ask for a save directory and dataset name.
12. Start acquisition:
      run_timelapse(n_frames=<n>, interval_s=0, save_dir=<dir>,
                    channel=<ch>, exposure_ms=<ms>, name=<name>)
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

This can be implemented as an image_process_fn hook that:
  1. Thresholds each frame to count bright local maxima.
  2. Logs the count per frame.
  3. Optionally signals end-of-acquisition when density drops below a threshold.

If the user asks for adaptive density control, offer to write a hook following
the get_hook_documentation pattern. Call get_smlm_documentation first to confirm
the SMLM context, then call get_hook_documentation for the hook API.

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
"""
