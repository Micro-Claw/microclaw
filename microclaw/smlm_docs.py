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
- Pre-bleach step: illuminate with high-power excitation (~10–30 kW cm⁻²) for
  5–30 s to drive most fluorophores into the dark state. Only then begin frame
  acquisition at lower power (~1–3 kW cm⁻²).
- Optional 405 nm activation: pulse at low power to increase the localization rate
  when molecule density falls too low late in the acquisition.
- Ask the user: channel name for main excitation, whether a 405 nm activation
  channel is available, and whether the photoswitching buffer is in place.

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
- For dSTORM (Alexa Fluor 647): 10–30 ms is a good starting point.
- For PALM fluorescent proteins: 30–100 ms.
- For DNA-PAINT: 100–500 ms (longer τb allows more photons per event).
- Use: run_timelapse(exposure_ms=<value>, ...)

### Frame interval
- Set interval_s=0 to acquire as fast as the camera allows (back-to-back frames).
- Do NOT use a non-zero interval for SMLM — idle time wastes acquisition time
  without reducing background.

### Number of frames
- Fixed-cell dSTORM: 10,000–60,000 frames is typical.
  Rule of thumb: for a 300 × 300 nm² structure (e.g., centriole), aim for
  ≥10,000 frames; for larger fields of view (e.g., entire cell), 30,000–60,000.
- PALM: 5,000–20,000 frames.
- DNA-PAINT: 10,000–100,000 frames; more is better.
- If the user is unsure, start with 20,000 frames and check density in FIJI.
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

### Objective and pixel size
- Requires a high-NA objective (NA ≥ 1.4), typically 60× or 100× oil immersion.
- Effective pixel size at the sample should be ~100–150 nm
  (camera pixel / total magnification). Confirm with the user.
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
6. (dSTORM only) Ask the user to perform the pre-bleach step manually in
   Micro-Manager by increasing laser power for 10–30 s, or confirm they have
   already done so. Do NOT automate pre-bleaching — high-power laser control
   is outside the safety-guarded parameter range.
7. Confirm the number of frames with the user (suggest 20,000 if unsure).
8. Ask for a save directory and dataset name.
9. Start acquisition:
     run_timelapse(n_frames=<n>, interval_s=0, save_dir=<dir>,
                   channel=<ch>, exposure_ms=<ms>, name=<name>)
10. After completion, offer to export to TIFF for analysis:
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

If the user has a hardware focus lock (e.g., Nikon Perfect Focus, Zeiss Definite
Focus), check that it is active before starting acquisition. In Micro-Manager
this appears as a focus-stabilization device in the device list.

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
- dSTORM 2D → ThunderSTORM
- dSTORM/PALM 3D → SMAP or ZOLA-3D
- DNA-PAINT → Picasso
- High-throughput / GPU → DECODE

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

---

## Key questions to ask the user before starting

1. Which SMLM technique? (dSTORM / PALM / PAINT / DNA-PAINT)
2. Which fluorophore and labelling strategy?
3. Is the photoswitching buffer / imaging medium in place? (for dSTORM)
4. Which excitation channel and laser power will be used?
5. Is TIRF mode available and configured?
6. Is a hardware focus lock active?
7. Are fiducial markers present for drift correction?
8. How many frames and what exposure time?
9. Save directory for the raw data?
"""
