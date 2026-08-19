# The drawn box is not the camera ROI

## Problem

On the Nikon, 2026-08-18 (`20260818_134324_111650_microclaw_history.jsonl`),
brightfield autofocus failed three times in a row and the metric was right to
refuse each time:

| sweep | range | contrast | verdict |
|---|---|---|---|
| line 24 | 8 µm | 0.007 | flat, Z not moved |
| line 30 | 40 µm | 0.018 | flat, Z not moved |

`MIN_CONTRAST` is 0.15. Widening the window 5× moved the number by nothing,
because the window was never the problem. The operator then focused by hand and
asked whether the metric had changed (line 32): `focus_metric` went 25990 →
26590, **~2%**, still inside the flat band, while the thumbnail was visibly,
unambiguously in focus.

The frame explains it. `structure_coverage = 0.0006` — the sharp cell edges are
0.06% of 1024×1024. Tenengrad is a mean squared gradient over the whole frame,
so ~600 sharp pixels are averaged into ~10⁶ flat ones. **The metric was correct
and the field was in focus at the same time.**

The operator's own fix was the right one. They drew a rectangle around the cell
with the ImageJ rectangle tool and asked to autofocus over just that region
(line 38). Microclaw could not do it, and told them so twice:

- `get_roi()` returned the full sensor, because the drawn rectangle is a display
  overlay and does not re-crop the camera.
- "There's no microclaw tool that reads your drawn overlay's coordinates" — true.
- The two offered routes both required the operator to type four numbers or push
  the crop to the camera by hand.

Both statements were accurate. The implied conclusion — that cropping the sensor
is the only way to narrow the metric — is not. Two separable gaps:

1. **We cannot read the box.** The coordinates exist, in the JVM we are already
   connected to, and MM's own ROI button reads them the same way.
2. **We cannot use a region without cropping the camera.** This one is a
   surfacing gap only: the seam already exists and is unexposed.

The blast radius is wider than one session. `_flat_reason`
(`autofocus.py:138`) and the `run_autofocus` schema description
(`tools_schema.py:839`) both advise the operator to autofocus on the **full
frame rather than a small ROI** — the exact opposite of the fix for this failure
mode, printed three times into a session where whole-frame dilution was the
cause.

## Decision

**Read the drawn box; compute the metric over it in software; leave the camera
alone.** Two changes, no new module, no new tool.

### 1. `region` on the tools that measure focus

`sweep_autofocus` already takes `metric_fn` (`autofocus.py:101`) and
`coarse_then_fine_autofocus` / `single_sweep_autofocus` already thread it
through (`:171`, `:247`). The whole computation is a numpy slice of the array
`snap_to_numpy` already returns:

```python
metric_fn = lambda img: tenengrad(img[y:y + h, x:x + w])
```

So `run_autofocus` and `snap_and_analyze` grow one optional argument,
`region: [x, y, w, h] | "drawn"`, and nothing else is built. `"drawn"` resolves
via §2 at call time; the literal box is for a caller that already knows it.

Three things move with it, and none are optional:

- **`_run_autofocus_passes` (`tools.py:4062`) is the single entry** used by the
  live run *and* by `_emit_autofocus` (`tools.py:133`). `region` reaches both or
  the exported script silently sweeps the full frame while the live run swept
  the cell — 43j's lesson about an argument the tool accepts never arriving in
  the emitted program.
- **`_metric_stamp` (`tools.py:3619`) must carry the region.** It stamps
  `metric_valid_for` with the camera `roi`, which is now insufficient: two
  analyses at one camera ROI over different regions produce incomparable numbers
  under an identical stamp. A region metric is comparable only among frames
  sharing the region.

  **As first written this bullet also claimed the problem for `run_autofocus`,
  and that was wrong** — 54b's implementer caught it. `run_autofocus` returns no
  `metric_valid_for` block at all, so there was no false claim of comparability
  to fix, and adding a stamp would have changed the regionless payload shape.
  The stamp was extended where it already exists, on analysis results;
  `run_autofocus` instead echoes `region` in its payload, present only when one
  was used, because every number it returns — both metric curves, `contrast`,
  and `focus_metric_at_final` — is measured over those pixels and nothing else
  in the payload says so.

- **The emitted crop carries its own bounds check.** `_validate_metric_region`
  lives in `run_autofocus` and `snap_and_analyze`, neither of which is emitted,
  so the standalone script had the crop without the refusal. **numpy slicing
  truncates rather than raising**, so an exported script run on a rig whose
  camera ROI is smaller than it was during the session scored the metric over
  whatever pixels existed and reported it as if nothing were wrong — the
  silently-clamped region this document exists to remove, reintroduced on the
  export path. The check now sits inside the `metric_fn` closure in
  `_run_autofocus_passes` (which *is* inlined) and is emitted beside the snap
  crop. Found in review, not by the suite: the block's own export tests exec the
  emitted source, but every one of them used a region that fit.
- **`_flat_reason` and the schema description must stop recommending the full
  frame.** Both should name region-restriction as the first remedy when
  `structure_coverage` is small, because that is what the evidence says.

### 2. Reading the box: `ij.WindowManager`, the route MM's own button takes

The rectangle is an `ij.gui.Roi` on the display's `ImagePlus`. MM's ROI toolbar
button gets it exactly this way — `WindowManager.getCurrentImage().getRoi()` —
which is why the tool operates on that window at all.

The plumbing is already here:

- `controller._new_static_java_class` (`controller.py:63`) is the design/12
  workaround that makes static `JavaClass` dispatch work. Every static goes
  through it or pyjavaz's one-key cache hands back another class's statics.
- `_imagej_window_ids` (`controller.py:470`) and `_describe_imagej_windows`
  (`controller.py:481`) already call `WindowManager.get_id_list()` /
  `get_image(id)` and read `ImagePlus` instance methods over the bridge.

So the read is `imp.get_roi()` → `roi.get_bounds()` → a `java.awt.Rectangle`.

**`Rectangle.x/y/width/height` are public fields, so camelCase and no parens.**
The snake_case form returns wrong data without erroring (`sp.numAxes`, design/32
Block 4). A non-rectangular selection still has bounds; take them rather than
refusing.

**Coordinates need no conversion for a software crop.** ImageJ ROI coordinates
are in displayed-image pixels, which is the frame `snap_to_numpy` returns.
Canvas zoom does not enter — magnification is a canvas property, the `Roi` is
stored in image coordinates. This is *only* true while the camera ROI and
binning are unchanged since the box was drawn, which §3 is about.

### 3. What is not yet known, and why the probe exists

**Whether MM 2.0's snap/live display is visible to `ij.WindowManager` at all.**
That display is not a plain `ImageWindow`; it is MM's own `DisplayController`
with an ImageJ bridge putting a proxy `ImagePlus` behind it. `getCurrentImage()`
is the likelier route than the ID list, and the ID list may not contain it.
`design/54-display-roi-probe.py` answers this over the bridge, at zero exposure,
before any of §1 or §2 is written.

If the probe says the display is unreachable from `WindowManager`, §1 still
ships — a literal `region` is independently useful and is most of the value —
and `"drawn"` refuses by name with the reason.

### 3a. Answered — Nikon, 2026-08-18 (`54a-nikon/out54.txt`)

**It is reachable, and §2 stands.** R1–R4 PASS, R5 skipped (no `--snap`).

- `WindowManager.getCurrentImage()` returns MM's Preview as an `ImagePlus`
  (`Preview-0`, 1024×1024, matching the camera ROI), so the ID list is not
  needed for the common case.
- `getRoi()` returned a `Rectangle` Roi; bounds `(60, 189, 151, 251)`, **3.6% of
  the frame**. Fields and methods agreed exactly, so the naming split does not
  bite on `java.awt.Rectangle`.
- ImagePlus frame == camera ROI frame, box inside it: `image[y:y+h, x:x+w]`
  indexes the drawn region with no offset arithmetic, as claimed.

Two findings the gate did not fail on, and both change what 54c builds.

**F1. `getIDList()` comes back sign-extended, and the fallback that uses it was
never exercised.** ImageJ assigns *negative* image IDs (`ImagePlus.ID` counts
down from −1). The probe reported `4294967294` — that is `0xFFFFFFFE`, i.e. −2
read as unsigned 32-bit. `getCurrentImage()` was non-null on this run, so the
`getImage(id)` fallback never ran; had it run, it would have passed a value
outside Java's `int` range. **54c must reinterpret the ID as signed int32
before passing it back**, and must not treat a passing 54a as evidence that the
fallback works — it has never executed. The probe's own R2 also counts the same
window twice (once via `getCurrentImage`, once via `getImage`) because it does
not dedupe by ID; anything reporting "which window did you mean?" must.

**F2. MM's own route exists and is the better one.** The `DisplayWindow` shadow
carries `getImagePlus` (as `.get_image_plus`). `studio.live().get_display()
.get_image_plus()` is preferable to the ImageJ static for three reasons: it
names the Preview specifically rather than following window focus, it touches no
static `JavaClass` and so cannot meet the pyjavaz cache collision at all, and it
needs no ID. **Make it the primary route and `WindowManager.getCurrentImage()`
the fallback.**

The probe proved the *method is present*, not that it returns a live `ImagePlus`
whose `getRoi()` reads. 54c's first step is one call confirming that; if it
returns null, the ImageJ static is already known to work and the order flips.

## 54b gate results — Nikon, 2026-08-18 (`block54b-nikon/`)

Every limb passed. **The plumbing is proven and the central claim is not.**

Proven, from the artifacts: the control was flat on this field (contrast
**0.015**, no `region` key, so the regionless payload kept its shape); the
region echoed back exactly `[119, 639, 160, 244]`; the camera ROI read
`1024×1024` before and after, so nothing cropped the sensor; the out-of-frame
refusal fired verbatim and took no exposure; the export carried two
`_run_autofocus_passes` calls — one ending `50, None)`, one carrying the four
numbers — with **zero** `set_roi` and a `# SKIPPED` comment holding the refused
call's error. The standalone script ran clean, and after the camera was cropped
to 160×244 it died exactly as intended:

```
RuntimeError: Region [119, 639, 160, 244] does not fit frame [160, 244].
```

That is the review defect (§1, emitted crop) confirmed fixed on hardware.

### The criterion was wrong: `contrast` is not comparable across region sizes

Step 3 asked for the region's `contrast` to exceed the full frame's. It did —
0.015 → **0.037**, and `peak_interior` flipped false → true. **That is not
evidence of anything.**

`curve_contrast` is span-over-median of a metric which is itself a *mean over
pixels*. Its noise floor therefore scales as 1/√N. The drawn box holds 39,040
px against the frame's 1,048,576, so shrinking to it inflates contrast by
√(1048576/39040) ≈ **5.2×** on noise alone. Measured on synthetic frames with
**no structure anywhere** (Gaussian, σ=25 counts, five planes, 40 trials):

| region | px | median contrast on pure noise |
|---|---|---|
| 1024×1024 | 1,048,576 | 0.0043 |
| 160×244 (the drawn box) | 39,040 | 0.0229 |

A ratio of **5.66×**, matching the 1/√N prediction. The rig measured 2.47×.
**A criterion satisfied more strongly by pure noise than by the real field
tests nothing**, so Step 3 is NOT TESTED on the question it was written for, and
the dilution hypothesis is still unmeasured on this rig. (The rig's ratio
falling *below* the noise prediction is suggestive rather than conclusive — the
sim's noise model is not this camera's.)

### And it is worse than a bad criterion: small regions defeat the guard

`MIN_CONTRAST` is a **constant 0.15** compared against a statistic whose noise
floor grows as the region shrinks. Above that line `run_autofocus` converges and
**moves the focus drive**. On pure noise, no structure at all:

| region | px | median contrast | P(contrast > 0.15) |
|---|---|---|---|
| 1024×1024 | 1,048,576 | 0.0043 | 0% |
| 160×244 | 39,040 | 0.0229 | 0% |
| 100×100 | 10,000 | 0.0459 | 0% |
| 48×48 | 2,304 | 0.0923 | 7% |
| 32×32 | 1,024 | 0.1218 | 22% |
| 20×20 | 400 | 0.2254 | **92%** |

**A 20×20 region converges on noise 92% of the time and moves the stage.** The
structureless-curve refusal — the guard that exists precisely to stop that — is
defeated by making the region small enough, and 54b is what made the region
size a user-facing knob. Worse, `_flat_reason` now *invites* it: "restrict the
metric region around structure" is the advice this block added, and a biologist
drawing a tight box around one cell is the expected use.

The Nikon's box is safely inside the flat zone, so nothing unsafe happened on
this gate. The defect is structural, not incidental.

### Decision (operator, 2026-08-18): scale the threshold, and hold 54b

Block **54d**. **54b does not merge on its own** — the two land together, so
nothing reaches a rig with the guard weakened.

The noise floor is `k/√N` with `k` constant. From the table above, `contrast ×
√N` is 4.40, 4.52, 4.59, 4.43, 3.90, 4.51 across four decades of N — flat, so
the law holds and `k ≈ 4.5`.

The threshold becomes

```python
max(MIN_CONTRAST, MIN_CONTRAST * sqrt(N_REF / n_pixels))    # N_REF = 1024*1024
```

which preserves today's margin over the noise floor at every region size:

| region | N | threshold | noise floor |
|---|---|---|---|
| 2048×2048 | 4,194,304 | 0.150 | 0.0022 |
| 1024×1024 | 1,048,576 | 0.150 | 0.0043 |
| 160×244 | 39,040 | 0.777 | 0.0229 |
| 32×32 | 1,024 | 4.800 | 0.1218 |

Three things that decide whether this is right or subtly wrong:

- **`N_REF` is a fixed constant, never the live camera's frame.** The noise
  floor depends on the region's own pixel count and nothing else. Deriving
  `N_REF` from the current sensor would hand the same region a 2× different
  threshold on a 2048² camera than on a 1024² one, for no physical reason.
- **The `max()` is what keeps every existing rig calibrated.** Without it a
  2048² full frame would drop to 0.075 — defensible on noise grounds, but it
  loosens a guard on rigs that have already been gated at 0.15. Only *smaller*
  regions tighten; nothing gets looser than today.
- **`_flat_reason` must print the threshold actually applied**, not the
  `MIN_CONTRAST` constant it prints now (`autofocus.py:141`). A refusal that
  says `contrast 0.12 < 0.15` while the code compared against 4.80 is the 43j
  defect shape — an emitter's constant standing in for the value that ran.

`coarse_then_fine_autofocus` already takes `min_contrast`
(`autofocus.py:172`), so this threads through what exists rather than adding a
layer.


## 54b+54d gate results — Nikon, 2026-08-19 (`54bd-nikon/`)

**54b's limbs re-passed. 54d has no rig evidence from this trip, and the block
does not merge on that basis.** Scored from the artifacts per `CLAUDE.md` step 6,
against the four numbers §"Resuming this block cold" said must agree.

| # | check | result |
|---|---|---|
| 1 | `min_contrast` vs `0.15·√(1048576/(82·68))` | **2.057 vs 2.0570 — exact** |
| 2 | the two `contrast/min_contrast` scores | full **0.960**, region **0.088** |
| 3 | Step 5 `entry_z_um` vs `final_z_um` | 2730.625 == 2730.625, `moved: false` |
| 4 | `_metric_pixel_count` in the export | **1** |

(1) and (4) are unambiguous passes: the scaling law is implemented correctly,
the payload and the sweep compared against the same threshold, and 54d's
frame-derived threshold reached the standalone script. Step 3 echoed the region
exactly, the camera ROI read `1024×1024` before and after, Step 4's refusal
fired verbatim at zero exposure, and the export carried 3 autofocus calls, 0
`set_roi`, and the refusal as a `# SKIPPED` comment.

### Two limbs produced no evidence, and both are 54d's

- **Step 5 never cropped the camera.** The `get_roi` immediately before the
  sweep reads `x=0, y=0, width=1024, height=1024`. So `min_contrast` was
  *absent* rather than "present and large (≈4.8 at 32×32)" — the opposite of the
  step's stated expectation — and the run was still reported as a pass. The
  crop was a GUI action described in prose outside the step's literal command
  block, so nothing in the typed prompt could fail when it was skipped. **This
  is `CLAUDE.md` step 6's failure shape again**: the ROI read-back must be a
  literal precondition that halts, not a preamble.
- **Step 6's cropped re-run is absent entirely.** No
  `block54bd-standalone-cropped.txt`; the transcript stops at
  `Get-Content block54bd-standalone.txt`. The limb requiring nonzero exit and a
  `RuntimeError` naming the region never ran. 54b's earlier gate demonstrated it
  at 160×244, but not at the pinned `5ed5fef`.

So the guard-defeat that 54d exists to close is still unmeasured against real
camera noise, which is exactly the condition 54b was held for.

### The Step 3 criterion is still not measuring what it was written for

By the stated rule the outcome is "region score ≤ full-frame score → the
dilution hypothesis is **not supported on this rig**", and by a wider margin
than 54b's (0.100 vs 0.048). But the run's own control undermines the
comparison:

**Steps 2 and 5 are the same call on the same field and returned contrast 0.144
and 0.065 — a 2.22× spread.** `C_full` is not a stable property of the field, so
`C_full/0.15` is not a stable score; substituting Step 5's control moves the gap
from 10.9× to 4.9×. The direction survives either denominator, so "the region
did not help" holds — but the assumption that one control measurement
characterises the field is now measured false, and no criterion built on a
single `C_full` can be trusted on this rig.

Related and unanticipated: **Step 2's 0.144 is within 4% of the 0.15 threshold**
on an admittedly out-of-focus brightfield field, against 0.015 on 2026-08-18 —
the same measurement 10× higher. The full-frame guard came within 4% of
converging on noise, at the one region size where 54d's scaling does not help.

### The likely cause, and why it outranks the criterion

**The sweep may be imaging every plane mid-move**, which would contaminate the
curve and explain all three anomalies above.

`sweep_autofocus` moves with `core.set_position(z)` then
`core.wait_for_device(focus_device)` then `sleep(settle_ms)` — 50 ms by default
— and snaps. Block 56 measured on this same microscope that `wait_for_device`
returning proves nothing: a successful `TIPFSOffset` move reported
`last_device_status: "busy"` after **0.89 s and ~18 polls**. If the Nikon's
`TIZDrive` behaves the same way, a 50 ms settle is two orders of magnitude short
and each plane is exposed at an unknown Z.

That is consistent with every number this gate produced — the 2.22× spread
between identical calls, 0.144 against 0.015 for the same measurement on
different days, and a region curve that looks better shaped (`peak_interior`
flipped true) while scoring worse. **It is a hypothesis, not a finding**: it was
not measured, because nothing in the current code reports where the axis
actually was when the frame was taken. Making it measurable is part of the fix.

`autofocus.py` moves Z at three sites — the per-plane step, the move-to-best,
and `_restore` — all via raw `ctrl.core.set_position` + `wait_for_device`,
bypassing the seam block 56 rebuilt. After the merge that accompanies this
section, **`run_autofocus` is the Z-moving tool that does not honour the engine
contract `CLAUDE.md` now states as authoritative**, and its `final_z_um` is the
*requested* value, never a read-back. Corroborating, weakly: entry_z read
2730.8 → 2730.725 → 2730.625 across three runs that each reported a successful
restore to their entry value. All inside block 56's 0.5 µm tolerance, so nothing
unsafe happened — but the reported position is a claim, not a measurement.

The safety guard is **not** bypassed: `run_autofocus` checks both sweep ends up
front (`tools.py`, `guard.check_z(entry_z ± z_range_um/2)`), which is the
documented design and stays.

### One defect from the artifacts

**The emitted script has zero `print` statements** in 632 lines. It runs three Z
sweeps and, on convergence, moves the focus drive, silently. `CLAUDE.md`
§"Exported scripts print their envelope and do not prompt" — "the print stays
and carries the bound, because it is now the only disclosure" — is unmet on the
autofocus emit path. It is also why Step 6's 0-byte `block54bd-standalone.txt`
reads as a pass while carrying nothing but the exit code.

### Carried forward, not this block's

`_emit_go_to_position` emits a bare `core.set_position(...)` for its Z limb, the
same unsettled-move shape, on a tool with its own gate history. Recorded here so
it is not lost; it is not fixed under 54.

## Refusals this must keep

- **Stale box.** A box drawn before a camera-ROI or binning change lands
  somewhere meaningless. Re-read at use time, validate against the current frame
  shape, and refuse rather than clamp: a silently clamped region is a metric
  measured over the wrong pixels, which is the defect this document exists to
  remove.
- **No selection.** `getRoi()` returns null when nothing is drawn. That is a
  refusal with an instruction, not an error.
- **Empty or degenerate region.** A 1-pixel box has no gradient; it must refuse
  before the sweep, not produce a flat curve after 30 exposures.

## What this does not buy

**A software crop still exposes the whole sensor.** Same dose, same readout
time, no speedup — only the metric narrows. A real camera ROI is faster and
lower-dose, and remains the right choice for a long acquisition. The software
region is the right *default* for focusing because it disturbs no camera setting
the operator's session owns and needs no `authorize_path(ctrl, "camera-roi")`.

**It does not fix brightfield.** Restricting the region removes the dilution,
which is this session's cause. It does not touch the harder property: a thin
transparent object has *minimum* contrast at focus, with Becke lines maximal
either side, so a brightfield through-focus curve can be genuinely bimodal
around the plane the operator calls focus. Measure a region-restricted curve as
an observation-only sweep on a real brightfield field before letting it drive Z.
That is a separate question and this document does not answer it.

---

# Coordinator checklist

This design owns its own blocks; it is **not** part of
`design/35-usability-and-pfs-checklist.md`. The **process** is
`CLAUDE.md` §"The block workflow" and that file is authoritative — if anything
below disagrees with it, `CLAUDE.md` wins and this gets fixed.

Five blocks. **54a and 54b are independent and can run in parallel**: 54b is
the literal-region work, which is most of the value and does not depend on what
the probe finds. Only 54c is gated on 54a's answer.

## 54a — run the probe (no implementation)

`design/54-display-roi-probe.py` is written; this block is ship-and-run. There
is nothing to delegate.

- **Gate (Nikon).** R0–R4 with an image on screen and a rectangle drawn on it.
  Zero exposure. `--snap` for R5 is optional and costs one brightfield frame.
- **Scored from the artifact**, not the verdict: R2 FAIL with no image on screen
  is not evidence, and the probe's own summary says so. R3's field-vs-method
  disagreement gets reported however the rest scored.
- **Outcome:** R2/R3/R4 PASS → 54c is buildable as §2 describes. R2 FAIL → 54c
  becomes "`region="drawn"` refuses by name" and shrinks to a few lines.

## 54b — `region=[x, y, w, h]`

§1. Delegated to an implementer in its own worktree; the coordinator writes the
runner prompt to the scratchpad and **offers to start it rather than spawning
it**.

Scope, all of it:

- `region` on `run_autofocus` and `snap_and_analyze`, threaded through
  `_run_autofocus_passes` as `metric_fn`. No new tool, no new module — both
  tools are already decorated, so the undecorated-tool register does not grow.
- `_emit_autofocus` carries `region`. **An exported script that sweeps the full
  frame while the live run swept the cell is the defect**, and it compiles
  cleanly, so the test execs the emitted source rather than parsing it.
- `_metric_stamp` carries the region.
- `_flat_reason` and the `run_autofocus` schema description stop recommending
  the full frame.
- Refusals from §"Refusals this must keep": stale box, empty region, degenerate
  region. Each refuses; none clamps.

Acceptance evidence, and step 3 applies to all of it — **a test written after
the code is not evidence until it has been watched failing on the pre-fix tree,
for the stated reason.** The fake is the thing to distrust first.

- **Gate (Nikon), the failing session re-run.** Same field, same brightfield,
  same flaky TIZDrive. `run_autofocus` with a literal region around the cell,
  against the full-frame sweep as control. The criterion is `contrast` crossing
  `MIN_CONTRAST` and a peak that is `peak_interior`, at a Z the operator agrees
  is focus — not merely "converged: true".
- **Gate (export).** A run first, then `export_session_script` — a fresh session
  emits a 13-line stub. The emitted script is exec'd, not just compiled.

Two things about how these steps get written, both learned the expensive way:
**name the mechanism, not the outcome** (an outcome-shaped step gets satisfied
by a better route and the thing under test never fires), and **ship no
placeholder inside a literal command** — a grep with `<x>` in it runs verbatim,
matches nothing, and "passes".

## 54e — settle the sweep, and disclose the exported one

Opened by the 2026-08-19 gate, delegated to an implementer in its own worktree.
**Merges with 54b/54d**, and lands before the re-gate: until the sweep reports
where the axis actually was, a re-gate cannot tell a diluted metric from a
metric sampled mid-move.

Scope, all of it:

- **`sweep_autofocus`'s three Z moves settle on a measured arrival** — the
  per-plane step, the move-to-best, and `_restore`. Reuse
  `controller.settle_stage_move` / `stage_move_dispatch_failure`; do not write a
  second settling loop and do not route through `ctrl.set_z`, because the
  emitted script's `ctrl` is a `SimpleNamespace(core=core)` with no controller.
- **`final_z_um` reports the measured position, never the requested one**, and
  the per-plane settle results are retained so a curve can be scored against
  where the axis actually was. `SweepResult` already carries `z_positions`;
  the measured counterpart belongs beside it.
- **`_emit_autofocus` inlines `_stage_move_contract_source()`** so the standalone
  script settles identically. An exported sweep that snaps mid-move while the
  live one settled is the 43j defect shape.
- **`_emit_autofocus` prints an envelope** carrying the sweep bound, the region,
  and the applied `min_contrast`, and prints the outcome including whether Z
  moved. It currently prints nothing at all across 632 emitted lines.

Acceptance evidence — **step 3 applies: a test written after the code is not
evidence until it has been watched failing on the pre-fix tree, for the stated
reason.** The fake is the thing to distrust first, and the fake here is the one
that returns the target position from `get_position` the moment `set_position`
is called. A fake that arrives instantly cannot fail the way the Nikon does.

- A test whose fake focus stage reports the **pre-move** position for the first
  N polls, proving the curve is measured after arrival rather than before.
- A test that execs the emitted source and drives it against that same fake.
- The envelope print asserted on the emitted source, not just its compilation.

### 54e outcome — implemented 2026-08-19

`9d4a37e` + coordinator review fix `2cdb336`. Suite **1921 passed, 99 skipped,
2020 collected**. All three of the implementer's pre-fix failures were
reproduced independently before acceptance.

The review finding, and why it matters more than its size: `final_z_um` became
the measured settled position while `_flat_reason` and `_edge_reason` kept
printing the requested entry Z, so **one result object carried two different
numbers for the same physical quantity and the prose one was the invented
one** — this block's own defect, reintroduced on the path with the most human
readers. Fixed; the exporter inlines both helpers with `inspect.getsource`, so
the standalone script inherits the correction without an emitter change.

**Two consequences the re-gate must absorb.**

- **Criterion 3 changes from equality to tolerance.** `entry_z_um` is a single
  unsettled read; `final_z_um` is now a settled measurement. They will differ by
  real settle error on any real stage, and `moved: false` will legitimately
  accompany two different numbers. The check is
  `abs(entry_z_um - final_z_um) <= 0.5` (block 56's `STAGE_MOVE_TOLERANCE_UM`),
  **not** equality. The old equality criterion would now fail a correct run.
- **A sweep is slower and can now fail outright.** Each plane costs at least
  three extra `get_position` round trips and ≥0.1 s. On the Nikon, where block 56
  measured a successful move still busy at 0.89 s, a 20-plane coarse+fine sweep
  gains roughly 18 s. And a plane that never settles raises `StageMoveError`
  after 10 s where the old path silently returned a contaminated curve — which is
  the point, but it means the documented intermittent `TIZDrive` serial timeout
  can now surface as a failed autofocus. The re-gate needs a limb for it, and the
  runbook must stop treating a TIZDrive timeout as purely a rig fault to re-run.

## 54c — `region="drawn"`

§2, **unblocked by 54a's PASS**. Reads the box at call time, validates it
against the current frame, refuses rather than clamps. Per §3a: MM's
`studio.live().get_display().get_image_plus()` is the primary route and
`ij.WindowManager.getCurrentImage()` (through
`controller._new_static_java_class`) the fallback; any ID read from
`getIDList()` is reinterpreted as signed int32 before use.

- **Gate (Nikon).** Operator draws a box, calls `run_autofocus(region="drawn")`,
  and the numbers agree with 54b's literal-region run over the same box. Then
  the stale-box limb: change binning or the camera ROI after drawing, and
  confirm the refusal fires **and names the box it rejected**.

## Run ledger

| Block | Depends on | Branch | Start commit | Implementation commit | Rig evidence | Merge | Design reconciliation |
|---|---|---|---|---|---|---|---|
| 54a | — | `design54/display-roi` | `3db1b88` | probe **is** the deliverable | **PASS** Nikon 2026-08-18 — R1–R4, R5 skipped; F1 (sign-extended ID, untested fallback) and F2 (prefer MM's DisplayWindow route) folded into §3a | n/a — design-only | **done** — §3a |
| 54b | — | `design54/display-roi` | `9505d01` | `f2ffd26` + review `e144759` | **round 1 PASS Nikon 2026-08-18**; **round 2 PASS Nikon 2026-08-19** — region echo, ROI unchanged, refusal verbatim, export clean | **held** — merges with 54d | |
| 54c | 54a | | | | | | | *(`region="drawn"` — unblocked by 54a, not started, deliberately after 54b/54d)*
| 54d | 54b gate | `design54/display-roi` | `9d1becf` | `cd72548`+`381589e`, review `c0f6323`+`5ed5fef` | **NOT TESTED, Nikon 2026-08-19** — threshold maths and `_metric_pixel_count` verified, but Step 5 never cropped the camera and Step 6's cropped re-run was not run, so both hardware limbs are unmeasured | **held** — merges with 54b | |
| 54e | 54bd gate | `design54/display-roi` | `e4863af` | `9d4a37e` + review `2cdb336` | **awaiting** — re-gate with 54b/54d | **held** — merges with 54b/54d | |

## Resuming this block cold

Everything needed is on `origin/design54/display-roi`. **This block is not in
`design/35`** — it owns its checklist above.

State as of 2026-08-19:

- **54a** closed. **54b passed its gate twice. 54d is NOT TESTED** — its two
  hardware limbs did not run on the 2026-08-19 trip. **54c not started.**
- **`main` is merged into this branch.** Blocks 52a–c, 55 and 56 landed while
  54b/54d were held. The merge is textually clean and the suite is green, but it
  leaves `run_autofocus` as the only Z-moving tool that does not honour block
  56's measured-arrival contract. See §"54b+54d gate results".
- **54e is assigned** and is a prerequisite for the re-gate: settle autofocus's
  three Z moves on a measured arrival, report where the axis actually was, and
  give the emitted script an envelope print. Until it lands, a re-gate cannot
  distinguish a diluted metric from a metric sampled mid-move.
- The rig gate is `design/54-block54bd-rig-gate.md`. **Its Steps 5 and 6 must be
  rewritten before the next trip** — the camera crop has to be a literal
  precondition that halts, not prose, and the cropped standalone re-run needs a
  command that fails loudly if skipped.
- Suite on the merged tree: **1917 passed, 99 skipped, 2016 collected** (macOS).

The four numbers to score a future gate from are unchanged, plus a fifth:

1. `coarse.min_contrast` against `0.15 × √(1048576 / (w × h))`.
2. The two `contrast / min_contrast` scores. **Raw contrasts are not comparable
   across region sizes.**
3. Step 5's `entry_z_um` against `final_z_um` — and now against the *measured*
   settled position, which must be reported.
4. The export's `_metric_pixel_count` count.
5. **The full-frame control, measured at least twice in the same session.** The
   2026-08-19 trip returned 0.144 and 0.065 from identical calls; a criterion
   resting on one control reading is not measuring the field.

**Merge order stands: 54b, 54d and 54e land together**, so nothing reaches a rig
with the flat-curve guard weakened or the sweep unsettled. Then 54c is next and
is unblocked.
