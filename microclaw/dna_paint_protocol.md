# DNA-PAINT Experiment Protocol — DNA Origami (in vitro), Singleplex

Returned by `get_dna_paint_documentation`. `get_smlm_documentation` is the
general SMLM reference and covers DNA-PAINT at planning depth; this document is
the depth behind it — kinetics, buffers, strand design, and the full bench
procedure. Where the two differ, **this document is the accurate one**.

Parameter values below are the published protocol's, measured on the reference
instrument in [Schnitzbauer2017]. They are starting points to confirm against
the user's own rig, sample and imager stock — never settings to apply unasked.

## Sources

Compiled from two reference papers, cited throughout as:

- **[Schnitzbauer2017]** — Schnitzbauer J, Strauss MT, Schlichthaerle T, Schueder F, Jungmann R.
  "Super-resolution microscopy with DNA-PAINT." *Nat Protoc.* 2017;12(6):1198–1228.
  [doi:10.1038/nprot.2017.024](https://doi.org/10.1038/nprot.2017.024) — the detailed, step-by-step
  DNA-PAINT/Picasso wet-lab protocol; `Step N` below refers to this paper. This is also the
  primary reference setup this document is modeled on — see [§0](#0-this-experiments-scope).
- **[Lelek2021]** — Lelek M, Gyparaki MT, Beliu G, Schueder F, Griffié J, Manley S, Jungmann R,
  Sauer M, Lakadamyali M, Zimmer C. "Single-molecule localization microscopy."
  *Nat Rev Methods Primers.* 2021;1:39.
  [doi:10.1038/s43586-021-00038-x](https://doi.org/10.1038/s43586-021-00038-x) — broader SMLM primer covering kinetics and general
  experimental considerations.

---

## 0. This experiment's scope

Decided for this first run:

- **Sample**: DNA origami nanostructures only (in vitro), no cellular/in situ labeling.
  This skips immunofixation, antibody-DNA conjugation and cell culture entirely
  [Schnitzbauer2017, Steps 1–18 apply; Steps 19–33 (in situ) do not].
- **Multiplexing**: singleplex — one imager species, one acquisition, no Exchange-PAINT fluid
  exchange and no spectral multiplexing. Simpler chamber, simpler acquisition.
- **Oxygen scavenging**: the PPT system (PCA/PCD/Trolox) is included from the start — mixed
  into the imager solution at least 1 h before imaging to improve dye photostability and photon
  yield [Schnitzbauer2017]. Full ultra-resolution settings (Box 2: 350 ms exposure, 80,000
  frames, ~4.5 kW/cm², drift-marker origami) are not adopted here — just the buffer.
- **Microscope**: any inverted stand with TIRF (or HILO) illumination, a hardware focus
  lock, a low-noise camera (EMCCD or sCMOS) and an excitation line matching the imager dye.
  The reference setup in [Schnitzbauer2017, EQUIPMENT] is a Nikon Ti-Eclipse + Perfect Focus
  System with an Andor iXon Ultra DU-897 EMCCD; its camera/focus recommendations transfer to
  a comparably equipped rig — see [§5](#5-imaging-parameters). **Ask the user what they have
  rather than assuming this one**, and call `get_system_state` to see what is configured.
- **Reagents**: all reagents for the experiment are already on hand, so the antibody-labeling
  and cell-culture reagent lists from the source paper are omitted below.

**Assumption to confirm**: for a 561 nm excitation line, this protocol defaults to the
paper's 561 nm-compatible reference handle — imager `CTAGATGTAT` labeled with **Cy3B**,
docking strand `TTATACATCTA` (the "P1" handle) [Schnitzbauer2017, MATERIALS]. If the user's origami
structures/imager stock use a different sequence or dye, swap it in — nothing else in this
protocol depends on the specific sequence, only on duplex length and dye/laser match
(see [§4](#4-imagerdocking-strand-design)).

**Assumption to confirm**: this protocol includes the full design→fold→purify workflow
(§6.A–B) for a start from unfolded staples. With folded, purified origami already on hand,
skip straight to [§6.C](#c-immobilization-on-glass).

---

## 1. Background & purpose

In DNA-PAINT, transient hybridization of short, dye-labeled **"imager" strands** to their
complementary **"docking" strands** (here, staple extensions on a DNA origami structure)
creates the stochastic fluorescence "blinking" needed for single-molecule localization
microscopy (SMLM). This decouples blinking from dye photophysics, so imaging works with
virtually any single-molecule-compatible dye and requires no photoswitching buffer
[Schnitzbauer2017, Introduction; Lelek2021, "Temporarily binding" fluorophore class].

Why DNA origami as the first sample: it's a known-geometry, ground-truth test structure —
docking sites sit at defined, designed positions, so imager concentration, exposure time and
laser power can be validated against a known answer before ever touching a biological sample
[Schnitzbauer2017, "Design and preparation of DNA origami structures"]. It's also the field's
standard calibration/reference structure for exactly this reason
[Schnitzbauer2017, Fig. 1b].

Key properties motivating DNA-PAINT generally:

- **No photobleaching as a hard limit** — the imager-strand reservoir in solution is
  effectively infinite, so bleaching of an individual dye has only minimal effect on the
  overall experiment [Schnitzbauer2017].
- **Programmable blinking kinetics** — bright time τ_b = 1/k_off is set by duplex stability
  (length, GC content, salt/temperature); dark time τ_d = 1/(c_i·k_on) is set by imager
  concentration, with k_on ≈ 10⁶ M⁻¹s⁻¹ typical for DNA-PAINT duplexes [Lelek2021]. A 1-bp
  increase in duplex length increases τ_b by roughly an order of magnitude [Lelek2021].
- **~1 nm localization precision / sub-5 nm resolution** has been demonstrated with this
  approach under optimized ("ultra-resolution") conditions [Schnitzbauer2017].

Limitations to keep in mind [Schnitzbauer2017, "Advantages and limitations"]: imager strands
are non-fluorogenic (always present, always slightly fluorescent in solution), so DNA-PAINT
requires optical sectioning (TIRF/HILO) to suppress background from unbound imager.

---

## 2. Materials & reagents

Condensed from [Schnitzbauer2017, MATERIALS/REAGENTS/EQUIPMENT], limited to what's needed for
a singleplex in vitro DNA origami experiment (antibody-conjugation and cell-culture reagents
from the source paper are not needed here and are omitted).

### DNA origami folding & purification
- Staple strands, modified (docking-strand extensions) and unmodified core staples.
- M13mp18 ssDNA scaffold (p7249).
- Biotinylated staples (for surface attachment).
- Tris pH 8.0 (1 M), EDTA pH 8.0 (0.5 M), magnesium (1 M), water — for 10× folding buffer
  (see [§3](#3-buffer-conditions)).
- Agarose, 50× TAE buffer, SYBR Safe DNA gel stain, DNA gel loading dye, DNA ladder — for gel
  purification.

### Immobilization & imaging
- BSA-biotin (10 mg/ml stock), streptavidin (10 mg/ml stock).
- Imager strand, dye-labeled (see [§4](#4-imagerdocking-strand-design)).
- 8 mm-channel custom flow chamber: microscopy slide, high-precision cover glass
  (No. 1.5H), double-sided adhesive tape, epoxy glue.
- Isopropanol (cleaning slides/coverslips).
- Tween 20 (buffer component).

### Oxygen-scavenging system (PPT)
- Protocatechuic acid (PCA), protocatechuate 3,4-dioxygenase (PCD), Trolox, NaOH, methanol —
  for the 40×/100×/100× stocks mixed 1:1:1 into the imaging buffer (see
  [§3](#3-buffer-conditions)) [Schnitzbauer2017]. ⚠ PCA and Trolox cause skin/respiratory/eye
  irritation — avoid breathing dust/fumes [Schnitzbauer2017, REAGENTS cautions].

### Equipment
- Inverted microscope with TIRF (or HILO) illumination and a hardware focus lock.
- Low-noise camera — EMCCD or back-illuminated sCMOS.
- Excitation laser matching the imager dye (561 nm for Cy3B). A laser power meter +
  microscopy-slide thermal power sensor to calibrate actual power **density at the sample
  plane**, since rated laser output is not the relevant number (see
  [§5](#5-imaging-parameters)) [Schnitzbauer2017, EQUIPMENT SETUP].
- Micro-Manager acquisition software [Schnitzbauer2017] — driven through Microclaw/pycro-manager
  here.
- Picasso (jungmannlab.org / github.com/jungmannlab/picasso) for origami design, localization,
  rendering and drift correction [Schnitzbauer2017].
- Thermocycler (for annealing), gel chamber + power supply + blue-light transilluminator +
  gel imager (for purification/QC).

---

## 3. Buffer conditions

Recipes from [Schnitzbauer2017, REAGENT SETUP], limited to the in vitro subset:

| Buffer | Composition | Storage |
|---|---|---|
| Buffer A | 10 mM Tris-HCl, 100 mM NaCl, pH 8.0 | RT, 6 months |
| Buffer A+ | Buffer A + 0.05% (vol/vol) Tween 20 | RT, 6 months |
| Buffer B | 5 mM Tris-HCl, 10 mM MgCl₂, 1 mM EDTA, pH 8.0 | RT, 6 months |
| Buffer B+ | Buffer B + 0.05% (vol/vol) Tween 20 | RT, 6 months |
| 10× folding buffer | 125 mM MgCl₂, 100 mM Tris, 10 mM EDTA, pH 8.0 | RT, 6 months |
| Gel buffer | 1× TAE | RT, 1 year |
| Gel running buffer | 1× TAE + 12.5 mM MgCl₂ | RT, 1 year |
| BSA-biotin stock | 10 mg/ml BSA-biotin in Buffer A; 20 µl aliquots | −20 °C, 6 months |
| BSA-biotin solution | 1 mg/ml BSA-biotin in Buffer A+ | 4 °C, 3 days (fresh) |
| Streptavidin stock | 10 mg/ml streptavidin in Buffer A; 10 µl aliquots | −20 °C, 6 months |
| Streptavidin solution | 0.5 mg/ml streptavidin in Buffer A+ | 4 °C, 3 days (fresh) |
| 100× Trolox | 100 mg Trolox + 430 µl methanol + 345 µl 1 M NaOH in 3.2 ml H₂O; 20 µl aliquots | −20 °C, 6 months |
| 40× PCA | 154 mg PCA in 10 ml water, pH 9.0 (NaOH); 20 µl aliquots | −20 °C, 6 months |
| 100× PCD | 9.3 mg PCD in 13.3 ml (50% glycerol, 50 mM KCl, 1 mM EDTA, 100 mM Tris-HCl pH 8.0); 20 µl aliquots | −20 °C, 6 months |
| **PPT oxygen-scavenging mix** | 1:1:1 ratio of 1× PCA : 1× PCD : 1× Trolox, mixed into imager solution **≥1 h before imaging** | fresh |
| **Imager solution (in vitro, with PPT)** | 1× Buffer B+ + 1× PCA + 1× PCD + 1× Trolox + fluorophore-labeled DNA strand, 100 pM–10 nM (start ~5 nM for a 12-site structure) | fresh |

PPT improves fluorophore photostability and therefore photon yield per binding event
[Schnitzbauer2017, Box 2] — worth having in from the start rather than only for a dedicated
ultra-resolution attempt, since it costs little beyond a 1 h pre-mix wait.

Imager concentration is the most consequential tuning knob: too low under-samples the
structure; too high raises unbound-imager background and causes "cross-talk" localizations
where two nearby sites bind simultaneously and get fitted as a single false spot in between
[Schnitzbauer2017, Fig. 7d]. Picasso's `Simulate` module can screen concentration/exposure
combinations in silico before committing microscope time [Schnitzbauer2017, Box 3].

---

## 4. Imager/docking strand design

- Docking and imager strands are short complementary single-stranded DNA oligos, typically
  **8–10 nucleotides** [Schnitzbauer2017]. Here, the docking strand is a staple extension on
  the origami; the imager strand carries the dye and diffuses freely until it transiently
  binds.
- **Default handle for this protocol** (561 nm / Cy3B match — confirm against the user's
  actual stock, see [§0](#0-this-experiments-scope)):
  - Docking (staple extension): `TTATACATCTA`
  - Imager: `CTAGATGTAT` labeled with **Cy3B**, excited at 561 nm
  [Schnitzbauer2017, MATERIALS; this is the same P1 sequence used for the calibration
  experiments on the paper's own TIRF setup].
- **Duplex length sets the bright/ON time** (τ_b = 1/k_off): a typical 9-bp duplex (like P1
  above) gives τ_b ≈ 500 ms. Each added base pair increases τ_b roughly 10×; each removed base
  pair decreases it roughly 10× [Lelek2021]. Camera exposure time should be matched to τ_b
  [Schnitzbauer2017, "Data acquisition"].
- **Imager concentration sets the dark/OFF time** (τ_d = 1/(c_i·k_on)), k_on ≈ 10⁶ M⁻¹s⁻¹
  typical [Lelek2021].
- **Estimating required imaging time**: at 10 nM imager and k_on ≈ 10⁶ M⁻¹s⁻¹,
  τ_d = (k_on·c)⁻¹ ≈ 100 s. For a ~98% probability that any given site is visited at least once
  (P = 1 − e^(−t/τ_d)), total imaging time t ≈ 4·τ_d ≈ 400 s; [Schnitzbauer2017] recommends
  **~33 min total** to get multiple binding events per site (needed for decent image quality,
  not just "visited once").
- Docking-site layout (hexagonal lattice, 5 nm spacing) is designed in `Picasso: Design`, which
  also auto-generates staple order lists and pipetting schemes [Schnitzbauer2017, Steps 1–5].

---

## 5. Imaging parameters

The published values, measured on the paper's reference instrument
[Schnitzbauer2017, EQUIPMENT]. They transfer to a comparably equipped rig — TIRF, hardware
focus lock, low-noise camera — and each is a starting point to confirm, not a setting to
apply unasked.

| Parameter | Recommendation | Source |
|---|---|---|
| Exposure time | ~300 ms (paper's in vitro Buffer B+ example, matched to the P1 9-bp duplex τ_b ≈ 500 ms) | [Schnitzbauer2017, Step 37] |
| Frame interval | 0 ms (back-to-back frames) | [Schnitzbauer2017, Step 47] |
| Frame count | Start at 7,500; typical full datasets 10,000–100,000+ depending on desired sampling | [Schnitzbauer2017, Step 46] |
| Pre-focus laser power density | ~0.25 kW/cm² at the sample plane | [Schnitzbauer2017, Step 41] |
| Imaging laser power density | ~2.5–6 kW/cm² at the sample plane (561 nm/Cy3B reference range: 1–6 kW/cm²); raise until bright time (τ_b) starts to *decrease* — that's imager bleaching while bound, and is the practical ceiling | [Schnitzbauer2017, Step 43; Table 1 troubleshooting] |
| **Power density calibration** | **Power density at the sample plane is what matters, not rated laser output** — measure it directly with a microscopy-slide thermal power sensor before picking a % laser power setting. A watt-class source runs at a small fraction of full power for this, so a % setting carried over from another experiment is not evidence of anything. | [Schnitzbauer2017, EQUIPMENT SETUP, "Power density calibration"] |
| Illumination mode | TIRF where available — best background suppression for surface-immobilized origami; HILO otherwise | [Schnitzbauer2017, Step 44] |
| Focus | Engage the hardware focus lock for prefocus and for the run (`set_focus_lock`) — the paper explicitly recommends a hardware lock over software refocusing | [Schnitzbauer2017, Step 42, CRITICAL STEP] |
| Camera settings | EMCCD reference values: Output amplifier: Conventional; ROI: Full Image; Frame Transfer: On; PixelType: 16-bit; ReadMode: Image; shutters: Open; readout mode = lowest-noise frequency whose readout time doesn't exceed the exposure time. On an sCMOS, the equivalent choice is the lowest-noise readout mode that sustains the frame rate | [Schnitzbauer2017, Steps 39, 45] |
| Photon conversion (for `Picasso: Localize`) | EM Gain, Baseline, Sensitivity, Quantum Efficiency set per the camera's actual specs — set EM Gain = 1 when using conventional (non-EM) amplification | [Schnitzbauer2017, Step 54] |
| Equilibration before acquiring | 5–15 min on the microscope before starting, to let thermal/mechanical drift settle | [Schnitzbauer2017, Table 1 troubleshooting] |

---

## 6. Step-by-step procedure

### A. Design & fold DNA origami *(skip if structures are already folded)*

[Schnitzbauer2017, Steps 1–9]:

1. In `Picasso: Design`, lay out docking sites on the hexagonal lattice canvas (5 nm
   spacing) and assign the extension sequence (§4) (Steps 1–4).
2. Export the staple order list (`Get plates`) and order staples, plus the biotinylated
   surface-attachment staples (Step 5). *(Pause point: 2–10 working days for synthesis.)*
3. Pool staples per the auto-generated pipetting scheme into stock mixes (Steps 6–7).
4. Fold via thermal annealing: 80 °C hold, then ramp 60 °C → 4 °C at ~3 min 12 s/°C over
   ~57 cycles, hold at 4 °C (Step 9). *(Pause point: store folded structures at 4 °C up to
   1 week, or −20 °C long-term.)*

### B. Purify

[Schnitzbauer2017, Steps 10–17]:

1. Run a 1.5% agarose gel (gel buffer + MgCl₂ + SYBR Safe), 90 V, 90 min, at 4 °C/on ice
   (Steps 11–15).
2. Image the gel; excise the origami band (a single sharp band, distinct from and slightly
   shifted relative to bare scaffold) (Step 16–17).
3. Recover via Freeze 'N Squeeze spin column, 6 min at 1,000g, 4 °C (Step 17).
   *(Pause point: store purified origami at 4 °C for 1 week, or −20 °C for long-term.)*

### C. Immobilization on glass

[Schnitzbauer2017, Step 18, option A — sealed custom chamber]:

1. Clean a microscopy slide and coverslip with isopropanol.
2. Build an ~8 mm-wide flow chamber with two strips of double-sided tape between slide and
   coverslip (~20–30 µl channel volume).
3. BSA-biotin solution (1 mg/ml), 2 min → wash with Buffer A+.
4. Streptavidin solution (0.5 mg/ml), 2 min → wash with Buffer A+, then Buffer B+.
5. Origami solution (~5 nM for a 12-site structure, diluted in Buffer B+), 2 min → wash with
   Buffer B+.
6. Add imager solution with PPT (§3/§4). ⚠ Mix the PCA/PCD/Trolox into the imager solution
   **at least 1 h before this step** — prepare it while folding/purifying, not last-minute
   [Schnitzbauer2017].
7. Seal the chamber with epoxy glue; wait ~15 min for it to dry before mounting.
   ⚠ Wait until fully dry — uncured epoxy can contaminate the objective
   [Schnitzbauer2017, Step 18A, CRITICAL STEP].

### D. Data acquisition

[Schnitzbauer2017, Steps 34–48], with the microclaw call for each step where there is one:

1. Mount the sample; raise the objective until immersion oil contacts the coverslip
   (Step 34).
2. With Micro-Manager running, confirm the configured camera and channel with
   `get_system_state` (Step 36).
3. Set exposure ≈ 300 ms and the camera parameters from [§5](#5-imaging-parameters)
   (Steps 37, 39). Exposure travels with the acquisition call — `run_timelapse(exposure_ms=300)`
   — rather than being set separately.
4. Go live, autostretch contrast — confirm you see background noise only (Step 40).
5. Open the excitation shutter at low power (~0.25 kW/cm² at the sample — calibrate against
   the power meter, not the laser dial) and focus, then engage the hardware focus lock
   (`run_autofocus`, then `set_focus_lock(enabled=true)`) (Steps 41–42).
6. Raise power to imaging density (~2.5–6 kW/cm²); you should now see individual blinking,
   diffraction-limited spots (Step 43).
7. Adjust the TIRF incident angle for best SNR (Step 44).
8. Set EMCCD readout mode for lowest noise consistent with the exposure time (Step 45).
9. Acquire with `run_timelapse(n_frames=7500, interval_s=0, exposure_ms=300)` — `interval_s=0`
   is back-to-back frames, and the dataset is saved to disk by the acquisition itself
   (Steps 46–47). **`interval_s=0` lets the engine hardware-sequence the time axis, which
   means a per-frame hook action cannot run between exposures**; a hook that needs one
   requires a nonzero interval, and microclaw refuses the combination rather than silently
   dropping the action.
10. Let the sample equilibrate 5–15 min, then acquire (Step 48).

### E. Reconstruction (pointer)

Full detail in [Schnitzbauer2017, Steps 50–60]; summary:

0. Export the raw stack for the localization software with `export_dataset_as_tiff`.
   Microclaw does not fit localizations; everything below runs outside it.
1. `Picasso: Localize` — identify and fit single-molecule spots (box side ≈ 6σ_PSF + 1; set a
   minimum net-gradient threshold; set the camera's photon-conversion parameters from
   [§5](#5-imaging-parameters)) (Steps 50–55).
2. `Picasso: Render` — render the super-resolution image (Steps 56–58).
3. Drift correction — redundant cross-correlation (RCC) first; for higher resolution, follow
   with fiducial/origami-based correction (Steps 59–60).
4. Since the docking-site layout is known by design, compare the reconstructed image against
   the designed pattern as a sanity check [Schnitzbauer2017, "Anticipated results"].
