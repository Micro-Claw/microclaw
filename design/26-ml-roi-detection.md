# design/26 — Finding the ROI the user can only describe

> **Implementation note (2026-07-20):** The normative milestone is now
> `design/26-implementation.md`, and the real-system validation procedure is
> `design/26-field-spike-prompts.md`. This file is the research record. Where an older
> package-specific API sketch or milestone below conflicts with the hook-only brief,
> the brief wins; do not implement the old `train_roi_detector` / `run_roi_survey`
> tool family from this document.

## The ask

> Sometimes the requirements are so complex that an ML image model is needed to
> classify whether an ROI is what the user is after. The user gives a
> description and several example images; we need to find other regions matching
> it and **start acquisitions when they are found**.

Explicitly a last resort — "this tool would only be pulled out in particularly
complex cases." So the bar is not "add a segmentation model." The bar is: when
`find_features` cannot express what the user means, can microclaw learn the
user's concept from a handful of pictures, mid-experiment, on the rig, fast
enough to scan with, and safely enough to let it drive the stage?

Everything below is either measured by `design/26-roi-detection-spike.py` (run
it; ~1 min, no hardware), marked as an estimate, or **cited from a source that was
actually read** — the notebooks in F4a and F8 were read as `.ipynb` source rather
than as rendered prose, and every licence was fetched, not recalled. Nothing is
quoted as fact because it sounded right — design/20 and design/21 are both about
that failure, and **F2 below is this doc failing it on its own first draft.**

F5's ilastik attachment and F6/F3's review surface were argued rather than measured, and
both are load-bearing. They now have a spike — `design/26-ilastik-imjoy-spike.py` (stages
F and G) and `design/26-imjoy-review-spike.ipynb` (the human half). **F is done**, on a
real install with a real human-drawn project (ilastik 1.4.2 arm64, 2026-07-17): the seam
works end to end (F5), and the run corrected this document four times — a `.ilp` really
is a pickle (F5a, so F4 was right for a reason it had not checked); headless *does*
produce a project and merely cannot train it; the start-up that rules out online scoring
is 7.6 s and the per-tile cost is not the blocker; and the `.ilp`'s remembered paths are
relative, so the artifact is portable after all. **G is half done**: ImJoy's `api` does
not exist outside a Jupyter kernel, which is a constraint on F6 rather than a detail.

Three of those corrections came from the spike being *wrong first* — a pickle scan blind
to the dtype the pickle lives in, a path check resolved against the wrong directory, a
flag table that read absence from `--help` as absence from the CLI. Each printed a
confident verdict. That is the failure this document is named after, committed three
times by the file written to prevent it, and it is why the remaining unmeasured claims
below are marked rather than trusted: **what still has no run behind it is whether
ilastik BUYS anything** (F5's AUC ties a classical floor that was already perfect) and
the entire review window (F6).

---

# The bar: a smart acquisition must compile to a hook

This is the constraint that decides every question in this document, so it goes
first.

A microclaw acquisition should be reducible to a **pycro-manager hook that runs
without microclaw and without a language model.** The agent's job is to *produce*
that hook — from a conversation, a description, some example images, a few
judgement calls — and then get out of the way. A model that has to be present
while the camera is running is not an acquisition; it is a dependency.

**The language model is a compiler, not an interpreter.** Description, examples
and adjudications go in; a self-contained scorer comes out; the scorer runs on the
rig at camera speed, offline, for as long as the lab keeps the file.

Four things fall out of that, and they are why it is worth insisting on:

* **Reproducibility.** A run repeated in a year must make the same decisions. A
  model behind an API is a moving part that the dataset cannot pin.
* **Offline.** Plenty of rigs are on isolated networks. An acquisition that needs
  egress is an acquisition that lab cannot run.
* **Cost and speed.** A vision call is 1–3 s and a line item. A tile dwell is
  100–500 ms and free. Nothing that costs money per tile can scan.
* **Auditability.** A hook is a file with a hash. "What decided to image this
  cell?" has a diffable answer.

## The invariant, which microclaw already half-keeps

> **No microclaw acquisition hook makes a network call at image time.**

This is *already true* and *nowhere stated*. All six hooks in `hooks.py` —
`AutofocusHook`, `FocusFeedbackHook`, `IntensityAdaptiveHook`, `PositionFilterHook`,
`MMPluginHook`, `MMAutofocusPluginHook` — honour it; none of them import anything
network-shaped. And `hook_manager.py:14` already flags `socket` in its advisory AST
lint, so the property exists in the codebase as a *warning* without ever having been
promoted to a *rule*.

Promote it. State it, test it, and widen the lint, which currently flags `socket`
but not `requests`, `urllib`, `httpx` or `anthropic` — the four ways anyone would
actually make that call.

Everything below is judged against this bar. The interesting part is that **the
feature passes it**, and the one thing that does not compile turns out not to be
the language model at all (F7).

## What we have, and exactly where it runs out

| in tree | what it answers | compiles? |
|---|---|---|
| `detect_features` (LoG blobs + centroid) | *is there a punctum, and where* | yes |
| `compute_stats` | *how bright, how sharp, how saturated* | yes |
| `PositionFilterHook` | *is the mean above a number* — a one-feature classifier | yes |
| `snap_and_analyze(return_thumbnail=True)` | Claude looks at the image | **no** |

The first three are geometric and photometric. None of them can be pointed at a
sentence. The fourth *can* — Claude is the only thing in the process that
understands "cells with condensed chromatin at the metaphase plate" — and it is
the only row that fails the bar. It costs a 1–3 s paid API round trip per look and
it cannot run inside an acquisition. **It is an adjudicator, not a scanner.**

The gap is the middle: something that (a) learns what the user means from their
examples, (b) runs per-tile at scan speed, locally, and (c) can act on a hit — and
that, once fitted, is a file rather than a conversation.

The seam for it was reserved two years of design docs ago. `03-plan-v2.md:43`
says new analysis models "can be added as new hook classes without other code
changes," and `hooks.py` ends on the same promise. This doc is the first thing
to actually walk through that door, and the door turns out to open only halfway.

---

# Revised implementation decision — analysis integrations are hooks

The option survey below names classical LDA/logistic scoring, ilastik, Cellpose,
learned descriptors, and lab-specific software. Those names must **not** become a
parallel family of microclaw tools (`train_*`, `score_*`, `run_ilastik`, and so on).
They are alternative implementations of one extension point: a pycro-manager hook.

The boundary is now:

* microclaw owns acquisition tools, hook lifecycle, safety, logging, review, and the
  conversation that specifies an integration;
* `hooks.py` owns any stable adapters we ship for the design/26 building blocks;
* a user's existing Python package, executable, model, project, or plugin remains the
  analysis engine and is called by a generated adapter hook;
* `hook_manager.py` validates, hash-pins, saves, loads, and records consent for both
  generated and user-provided hooks;
* no analysis-package-specific tool schema is added.

For example, an ilastik project and a Cellpose model may eventually have different hook
classes because their invocation and output contracts differ, but the agent sees both as
hook strategies. A lab's private classifier uses the same path and requires no microclaw
release. The classes we choose to ship for design/26 are added to `hooks.py` and
`PRECODED_HOOK_REGISTRY`; user-specific adapters are saved under the existing hook store.

This preserves the compiler invariant: the conversation produces reviewed Python source,
and image-time execution needs neither the language model nor a network. A local process
or heavyweight environment is permitted when explicitly reviewed and pinned; it is a hook
dependency, not a reason to hide the dependency behind a new tool.

## The custom-analysis setup conversation is part of the feature

“Write me a hook” is not enough guidance, but the operator should not have to reverse
engineer software somebody else wrote. Microclaw normally asks only three questions, in
the language of the user's existing workflow:

1. **What workflow do you use, and can you point me to it?** A package name or web page is
   sufficient; an app, notebook, script, project, model, environment, or repository is
   better. “I open this ilastik project and press Run” is a valid answer.
2. **Can you show me one example that works?** The user supplies a representative input
   and result and may explain it biologically: “the outlined cells are the ones I want.”
   They are not asked for arrays, dtypes, axes, APIs, or coordinate conventions.
3. **What should the microscope do with the result?** Record or map it, keep matching
   images, revisit objects, analyze tiles together, or stop after enough targets are found.
   A threshold or acquisition budget is requested only when that action needs one.

Microclaw owns the technical investigation. It inspects local files and installed
environments, reads `--help`, official documentation, and upstream source (searching the
web when needed), and runs the supplied example safely. From evidence, not the package
name, it derives the entry point, image/channel contract, raw output and coordinates,
processing unit and latency, state and failure behavior, offline/process/hardware boundary,
and provenance. It records where those facts came from. The agent asks a follow-up only
when investigation leaves an ambiguity that changes scientific meaning, authorization, or
hardware action.

Microclaw summarizes the derived contract back in plain language before writing code. It
then selects the hook slot, writes a saved-hook adapter — a plain `analyze_frame` class,
never a `HookBase` subclass; see `design/26-implementation.md`
§Decision — and tests the boundary without
hardware against the example. If no working example is available, a screenshot, tutorial
dataset, or dry run on a copied image can substitute; until raw output is verified, the
first hook is observation-only and cannot drive the stage or acquisition.

The full generated source and advisory lint warnings are shown to the user. Saving still
requires explicit confirmation; the accepted bytes and warnings are hash-pinned by the
existing hook manager. Running a newly saved hook requires the existing run confirmation.

## Native pycro-manager boundaries needed by design/26

The design should select the existing pycro-manager boundary that matches each behavior,
then add Microclaw policy and provenance around it rather than creating backend tools or
parallel callback APIs:

| behavior | hook shape |
|---|---|
| score or classify every tile | `image_process_fn`; log score/class and provenance |
| return masks/boxes/centroids | `image_process_fn`; normalize coordinates and log objects |
| inspect a known group before deciding | `run_adaptive_survey` candidates-queue runner, stateful processing when streaming is required, or a two-pass survey — **not** pycro-manager's native `AcquisitionFuture.await_image_saved(...)`, which microclaw deliberately does not expose to hooks (design/24) |
| react after each image is persisted | pycro-manager `image_saved_fn(axes, dataset)`; this is per image, not a completed-run callback |
| analyze a completed on-disk group | after acquisition completion, open the `ndstorage.Dataset` returned by `acq.get_dataset()` or import it directly from `ndstorage`; give adapters a selection-limited `DatasetView` through the reviewed offline orchestration |
| stop a serial survey when a condition is met | adaptive-survey hook using `candidates` / `progress` |
| acquire guarded follow-ups around a detected object | survey-with-detector hook; check XY/Z and enforce an event cap |
| rank all tiles and take top-*k* | two pass: score/log first, then deterministically select positions; streaming callbacks cannot know future ranks |

Classical LDA/logistic logic, ilastik, and Cellpose are examples used to validate these
boundaries. They do not define public microclaw tool APIs. The research record remains useful
for choosing and testing shipped hooks, especially its findings about attribution,
multi-tile ranking, latency, portability, and review.

## Milestone 1

1. Extend the hook authoring reference and agent policy with the three-question intake
   and the separate agent-owned verification checklist.
2. Treat common acquisition-time network clients as advisory lint findings and state the
   offline invariant in the generated-hook workflow.
3. Use the guided flow to produce and fixture-test one adapter for a real user analysis;
   save it through `hook_manager` and run it observation-only on stored images first.
4. Promote only stable, generally useful design/26 adapters into `hooks.py` and the
   registry. Keep model paths, project files, thresholds, channel mappings, and other
   lab-specific facts as constructor parameters or saved custom hooks.
5. Demonstrate the selected hook in a two-pass real-sample survey, preserving object
   attribution, guarded positions, an explicit acquisition budget, and replayable logs.

Success means a user can bring an analysis microclaw has never heard of, point to the
workflow, show what a successful result looks like, and say what the microscope should do.
Microclaw derives the technical contract, presents it in plain language, writes and tests
the adapter, and lets the user review and save it without knowing how to code.

---

# Archived package-specific milestone sketch — non-normative

The following classical-detector milestone predates the hook-only decision. Its ordering,
attribution, two-artifact argument, and verification gates remain requirements for any
corresponding hook, but its proposed package-specific workflow functions are not public
tools and are not the implementation boundary.

The first implementation is deliberately smaller than the option survey below. It ships
one end-to-end workflow and keeps the other work as researched upgrade paths rather than
half-implemented backends.

**Milestone 1 is a two-pass, classical, score-only workflow on a real sample.** It has no
torch dependency, no ilastik dependency, no ImJoy dependency, and no online
`mode="acquire"`. The deliverables are:

1. a blind survey that saves tiles and stage coordinates;
2. reviewed positive examples and reviewed/sample-derived negatives;
3. a fitted classical descriptor + logistic probe that scores the stored survey;
4. an adjudicated ranking and an explicit acquisition budget;
5. an object-attributed list of guarded stage positions for the existing
   `run_multiposition_acquisition` tool;
6. a standalone scorer artifact plus a separate, deterministic ranked-position artifact.

The milestone is successful only when this entire path has run on a real sample and its
precision at the chosen budget is useful to the operator. Synthetic AUC is a regression
test, not an acceptance criterion.

## The actual default flow

The survey must precede the first binary fit. The earlier flow fitted a probe and only
then mined its negatives from a survey that had not happened yet. That was circular.

```
run_roi_survey(..., detector=None)                 # blind overview; save pixels + coords
        |
        v
prepare_roi_training_set(description, examples, survey)
        |   positives: user examples / boxes
        |   negatives: sampled survey crops, REVIEWED before they become labels
        v
train_roi_detector(..., backend="classical")      # first valid binary fit
        |
        v
score_roi_survey(detector, saved_survey)           # no second exposure; rank stored tiles/objects
        |
        v
review_roi_candidates(..., surface="blocks")      # persist verdicts + adjudicator
        |
        v
refine_roi_detector(...)                           # refit and report precision@candidate k
        |
        v
choose acquisition_budget(k)                       # the operator's resource decision
        |
        +--> export_detector_scorer(...)            # image -> score; standalone and offline
        +--> export_ranked_positions(...)           # scores -> top-k guarded positions
        v
run_multiposition_acquisition(confirmed_positions, ...)
```

Unlabelled survey crops are **candidate negatives, not free ground truth**. The desired
state may be common, spatially clustered, or absent; blindly labelling sampled tiles as
negative creates asymmetric label noise exactly where five-shot fitting is most fragile.
For milestone 1 the sampled negatives are shown during review. A later implementation may
use positive-unlabelled learning, but it may not silently convert "unlabelled" to
"negative".

## The unit of detection: score objects, not tiles

A high-scoring tile does not identify which cell inside it caused the score. The previous
plan jumped from a tile score to the generic centroid returned by `find_features`; on a
tile containing several cells or debris, there is no reason that centroid belongs to the
learned concept.

Milestone 1 therefore makes attribution explicit:

* `detect_features` proposes objects in each tile;
* each proposal produces a crop in the same coordinate frame as the source image;
* the descriptor/probe scores each object crop;
* the ranked record stores the proposal bounds, centroid, tile identity, score, and stage
  transform used;
* only the selected object's centroid is converted through the calibrated affine and
  passed through XY/Z guards.

Whole-tile scoring may remain as a cheap prefilter, but it cannot supply the final ROI
coordinate. If object proposals do not cover the user's concept, milestone 1 must acquire
the whole field or stop and request boxes; it must not invent object attribution.

## Two artifacts, because top-k is not a streaming hook operation

The compiled detector is a **standalone scorer**: pixels in, deterministic score out. A
streaming `image_process_fn` cannot select the final top-k because it has not seen future
tiles. Budget selection belongs to a second deterministic artifact produced after the
survey:

* `detector.py` / detector manifest — descriptor and fitted probe, usable without
  microclaw or a network;
* `ranked_positions.json` — hashes of the survey inputs and detector, all scores and
  object coordinates, the chosen `k`, selected positions, calibration identity, and
  adjudication provenance.

Together they answer both audit questions: *what scored this image?* and *why were these
positions acquired?* Neither artifact claims that an absolute logistic score is a
calibrated probability.

## Verification gates before milestone 1 is called shipped

* Run the complete workflow on multiple real slides/days, splitting evaluation by slide,
  and report precision at the operator's acquisition budget and useful-ROI yield.
* Measure sensitivity to contaminated mined negatives, duplicated examples, one wrong
  verdict, constant descriptor terms, common targets, and slides with no target.
* Compare selected object centroids with human boxes on multi-object tiles and non-square
  frames; verify crop axes, affine conversion, revisit error, and guard rejection.
* Measure survey time, offline scoring time, review time, refit time, revisit drift, and
  survey photobleaching on the intended sample.
* Replay `verdicts.json`, the scorer manifest, and `ranked_positions.json` and reproduce
  the same weights, ranking, and selected positions without calling an adjudicator.

Everything after this section is the research record. It is retained intentionally so
the measured ilastik seam, learned-descriptor comparisons, review-surface work, licensing
analysis, and online runner findings are not lost. Those are **deferred options, not part
of milestone 1**, unless a subsection explicitly supports the classical two-pass path.

---

# Research record and deferred options (preserved)

F1 reported the defect that blocked the requirement's second half — *act on a hit,
in-scan*: a hook's `event_queue.put()` was silently dropped, so nothing could enqueue
an acquisition at the moment of detection. **It is fixed and shipped, so it is deleted
rather than restated** — the runner (`SurveyProgress`, `_survey_event_stream`,
`_acquire_survey_with_detector`) landed in PR #20 with design/24, `run_adaptive_survey`
and the corrected `hook_docs` in PR #21 with design/27, and both are rig-validated. What
design/26 now needs from that machinery is stated where it is used: the online mode,
below. The **default two-pass flow needs none of it**, and F7 argues the two-pass flow is
the better mode anyway, for reasons that never had anything to do with the bug. The
numbering below is unchanged, because these findings are cited by number from design/24,
design/27 and the tests.

## F2 — five examples is enough, if you don't train a network — and the first draft of this finding was wrong. [spike B]

The instinct on "few-shot" is to fine-tune something. That is the wrong shape:
five images is not a training set for any model with capacity, and a fine-tune
mid-experiment costs minutes-to-hours on a lab workstation that may not have a
GPU at all. It also does not compile to anything a lab can keep.

What five images *is* enough for: choosing a direction in a **fixed, generic
descriptor space**. Standardize the features, fit one direction, score along it.
It fits in milliseconds, it structurally cannot overfit five points the way a
fine-tune can, and the fitted direction is ~12 floats — a file, not a service.

**That conclusion survives. The number this doc first reported for it does not.**

### F2a — the head was the whole variance, and one seed hid it

The first draft fitted a **nearest-mean** probe (difference of class prototypes),
ran it on **one seed**, got AUC 0.994 and precision@k 0.95, and printed that as
the finding. Run the identical fit on five slides:

```
B_1  the same probe, five slides. Which head?

head                                   AUC     precision@k      fit
nearest-mean (first draft) 0.926 +/- 0.102   0.69 +/- 0.32      0.1 ms
linear SVM                 1.000 +/- 0.000   1.00 +/- 0.00     12.3 ms
logistic                   1.000 +/- 0.000   1.00 +/- 0.00     24.7 ms

     nearest-mean, seed 0     : AUC 0.994  precision@k 0.95   <- the number the first draft reported
     nearest-mean, worst seed : AUC 0.727  precision@k 0.12   <- the number it did not look for
     seed 0 ranks 2 of 5 for this head.
```

On its worst slide the shipped probe puts **2 real cells in its top 19**. The doc
froze a seed, quoted it to three decimal places, and called it a finding — which
is the design/20 failure, committed by the document whose thesis is that you must
not do that. It is now the first thing spike B prints.

The mechanism is simple and worth naming: a mean-difference direction **has no
margin**. It is set by the *centroid* of the five examples, so one unrepresentative
example tilts it and nothing pushes back. A margin classifier is set by the
boundary points instead, and is stable across all five slides.

**The fix costs nothing** — same five examples, same descriptor, same
one-direction shape, no new dependency, no GPU, tens of milliseconds. It is still
pure numpy/scipy/skimage, all already in `pyproject.toml`. (The fit times above
move by tens of percent run to run; read them as "milliseconds", not as points.)

**Ship the logistic head, and not for its AUC.** It ties the SVM; accuracy is not
the tiebreak. The tiebreak is F3 — a probability's scale is a property of the
model class, not of the training set — and F3 is what decides whether this thing
compiles.

### F2b — the dihedral augmentation adds exactly zero information

The first draft also claimed: "4 rotations × 2 flips turns 5 examples into 40 for
free, and it is *correct* augmentation rather than the usual hopeful kind."

It is neither. It is a **no-op**:

```
B_3  does the dihedral augmentation add information?
     augment() returns          : 8 images
     distinct feature vectors   : 1
     max spread across the 8    : 0.0e+00   (float noise)
```

Every feature in the classical descriptor — percentiles, band energy, Laplacian
variance, blob count, mean blob sigma, nearest-neighbour spacing, bright fraction,
SNR — is invariant under rotation and reflection, and `rot90`/`fliplr` are exact
pixel permutations. The eight "augmentations" of an example are eight copies of one
vector.

And it is not harmless: it inflates the apparent positive count from 5 to 40 while
the effective sample stays 5, so a class-weighted head is weighted on a lie — it
believes it has 40 positives against 20 negatives.

Keep it anyway, and move it: **augmentation is a property of the DESCRIPTOR, not
of the probe.** It is a no-op for this rotation-invariant descriptor and genuinely
load-bearing for a CNN/ViT embedding, which is invariant to nothing.

### F2c — the survey supplies negative candidates, not negative labels

The user supplies positives; a survey supplies a bucket of unlabelled tiles. The first
draft called those negatives "free" and assumed the bucket was overwhelmingly negative.
That may hold for a rare target, but it fails when the desired state is common, clustered,
or poorly represented by the user's examples. Contamination is especially costly with
five positives because a confident false negative can rotate the fitted direction.

Milestone 1 samples **candidate negatives only after the blind survey and shows them for
review before fitting**. Positive-unlabelled learning is a possible later improvement.
Neither path is allowed to silently treat absence of a positive label as a negative label.

### The caveat, loudly

These are synthetic classes that differ **by construction**, so a perfect AUC is a
property of the fake data, not a promise about cells. The transferable claim is
narrow, and it is the only one this doc makes: *the head was the bottleneck here,
and fixing it is free.* It says **nothing** about whether a generic descriptor
spans the concept in the user's head — and the user reaching for this tool has, by
their own account, a concept the simple things cannot express. That is what the
backend ladder is for: the probe stays the same, and the *descriptor* under it gets
stronger. The classical descriptor is the floor, not the answer, and the first
thing to do with a real sample is measure whether the floor is enough.

## F3 — the threshold is the only thing in the pipeline that does not compile. [spike B_2]

A threshold looks like a number. It is really a claim about the model, and the
model has no idea what it costs to be wrong.

`refine_roi_detector` re-fits on the adjudicator's verdicts. What happens to an
operating point chosen against the *previous* fit? Spike B_2, five slides, three
heads:

```
B_2  what survives a refit: the scale, or the ranking?

head                              scale drift   thr: n before/after  top-k set kept
nearest-mean (first draft)             6.2 %       39.4 ->  19.8             71 %
linear SVM                             9.0 %       18.6 ->  18.6            100 %
logistic                               0.0 %       20.2 ->  19.8            100 %
```

Read the columns in order.

**The absolute threshold does not survive.** The nearest-mean and SVM heads
normalise by `max|projection|` over the *training set*, so a refit rescales every
score in the survey. Carry a threshold across a refit of the nearest-mean probe and
the candidate count halves — **39 candidates become 20**, same tiles, same pixels,
different numbers — because a single adjudicated outlier can move `max|projection|`
as much as it likes.

**The logistic head fixes the drift, and this is why it ships.** Its output is a
probability, so its range is fixed by the model class: `scale` is 1.0 by
construction and stays 1.0 across a refit. 0.0 % drift.

**But a fit-independent scale is not a CALIBRATED one, and it would be exactly this
doc's own sin to pretend otherwise.** The fit is class-weighted 50/50; the slide is
12 % positive. So `p = 0.5` does *not* mean "half the tiles like this one are hits."
The number stops **moving**; it does not thereby start **meaning** anything. A
default threshold is still unshippable, and it will still move with every sample,
stain and objective.

**What survived these synthetic refits was the RANKING** — the top-k set was
preserved 100 % by both margin heads on this spike. That is evidence for the interface,
not a general guarantee: noisy labels, a new slide, or a changed descriptor can reorder
the candidates. The operating point that compiles is nevertheless a
**budget**, not a threshold:

> **Image the top *k* tiles**, not "image everything above 0.2."

And note what kind of question each one is. A *threshold* is a claim about the
model — "0.2 is where this classifier becomes trustworthy" — and nobody, agent or
user, is in a position to answer that in advance. A *budget* is a claim about the
user's afternoon — "I can afford 20 z-stacks, and I need at least 10 good cells" —
and the user is the only one who *can* answer it, needs no model to do so, and
would have had to answer it anyway. **We were asking the wrong party for the wrong
number.** The meaning of a budget survives a rescale, refit, or new sample—it still caps
the user's acquisition cost—but the membership of the selected top-k may change. That
change must be reported after every refit rather than hidden behind a claim of invariance.

This is not a lonely conclusion, and it is the one place in this document where an
argument from first principles turned out to have been settled by somebody else's
practice: pycro-manager's own neural-network acquisition notebook selects its regions
with `args.top_k = 4` and never names a threshold anywhere (**F8**).

A budget has one failure mode, and it matters: on a slide with **no** positives,
"top 20" images twenty pieces of debris. The two-pass flow catches that at
adjudication — somebody looks at the crops before the stage moves. The online mode
cannot, because there is nobody there to catch it. That is F7.

## F4 — a model file is executable code

`torch.load` unpickles, and unpickling runs arbitrary code; PyTorch only flipped
the default to `weights_only=True` in 2.6, and every "add `weights_only=False`
to fix this error" post on the internet is advice to reopen the hole. Cellpose,
bioimage.io and ilastik all distribute model files over the network.

### F4a — the bare `torch.load` is the ecosystem's idiom, and pycro-manager's own docs write it

The reference notebook for this entire feature — pycro-manager's
`guiding_acq_with_neural_network_attention` (read 2026-07-14, notebook source, not
the rendered page) — loads its detector like this:

```python
args.weights = "0814-r2-best.pth"
...
state_dict = torch.load(args.weights, map_location=torch.device("cpu"))
model.load_state_dict(state_dict)
```

Three things about those two lines, in order of how much they matter.

**It is safe, and it is safe by accident.** The file is a `state_dict` — an
`OrderedDict` of tensors — so on torch ≥ 2.6, where `weights_only` now defaults to
`True`, that bare call loads it fine and refuses anything else. On the torch this
notebook was written against, the same call would have executed whatever was pickled
into the `.pth`. Nothing in the code changed; a *default* changed underneath it. That
is the entire argument for the `torch >= 2.6` floor in `pyproject.toml`, and it is
now an argument with a citation rather than a hunch: **the canonical example a
microclaw user would copy from does not pass `weights_only`, and is relying on us to
have set the floor.**

**The provenance is a bare filename.** `"0814-r2-best.pth"` — no URL, no hash, no
download step, no note on where it came from. It is a file someone handed someone.
That is not a criticism of the notebook, which is demonstrating an acquisition and
not a supply chain; it is the observation that **the ecosystem has no convention here
at all**, so whatever microclaw does becomes the convention for its users. F7a's
manifest is that convention.

**And this is the good case.** A `state_dict` is the benign end of the range, loaded
from disk. Ultralytics' checkpoint loader downloads the weights from a GitHub release
if they are absent (`attempt_download_asset`) and unpickles them through a wrapper
that "automatically sets `weights_only=False` if the argument is not provided"; the
`weights_only=True` path is opt-in behind an environment variable and off by default.
A mainstream, actively maintained library's **default** is to fetch bytes over the
network and execute them. Any backend we adopt gets read for this before it gets a row
in `DESCRIPTOR_REGISTRY`.

`hook_manager.py` already treats *Python hook source* as untrusted: a SHA-256 pin
of the exact bytes the user consented to, an advisory AST lint whose warnings are
recorded as accepted, a refusal to load anything edited since. **A downloaded `.pt`
is untrusted code by the same argument and by a shorter path.** Shipping a "download
a detector model" tool without the same manifest would open a bigger hole than
everything in `safety.py` closes.

Rules, therefore: **weights are hash-pinned in a manifest like hooks are**; load
via `safetensors`/ONNX or `torch.load(..., weights_only=True)` and never with
`weights_only=False`; a weights file arriving from the network gets the same
blocking confirmation as a generated hook.

That "same argument, therefore the same manifest" is load-bearing, and F7a spends
it: it is why there is no new store module.

Note the pleasant consequence of the compile bar: **the shipped backend has no
weights at all.** A fitted logistic probe is ~12 floats and a mean and a standard
deviation. It is JSON. F4 only bites once someone opts into the embedding ladder,
or is handed a `.ilp`.

## F5 — ilastik cannot be trained on demand, so microclaw asks the USER to train it

ilastik's headless mode is explicit that you must "first use the graphical
interface to create a project and train a classifier by manually drawing
annotations, then save your project and quit." There is no supported path to "here
are five PNGs, give me a classifier, without a human in a GUI" — which is precisely
our ask.

**One sentence of that was wrong, and the correction is worth more than the claim.**
This finding used to say "Headless consumes a `.ilp`; it does not produce one."
Measured (1.4.2, 2026-07-17): headless *does* produce one —
`--headless --new_project=p.ilp --workflow="Pixel Classification"` exits 0 and writes
a perfectly valid 14 KB project. It just cannot **train** it. Point the headless
predictor at that project and it runs the whole workflow and dies at the export slot:

```
lazyflow.slot.Slot.SlotNotReadyError: Can't get data from slot ... ExportPath yet.
It isn't ready. First upstream problem slot is: OpExportSlot/OpExportSlot.Input
```

So the boundary is not *produce vs consume*, it is **structure vs judgement**: ilastik
will hand a program every part of a project except the part that required a person to
look at the sample. F5's substance is unchanged and better evidenced — the twenty
minutes of drawing is genuinely unautomatable, and the proof is a program that runs
7.9 seconds and then cannot proceed without you.

That kills it as *the* architecture. It does not kill it as the **escape hatch**,
and the escape hatch matters more than it looks: ilastik is the only rung on the
ladder that lets a lab break through the descriptor ceiling **without a GPU and
without a fine-tune**. Every other upgrade (DINOv2, Cellpose) swaps in a *bigger
generic* feature space and hopes the user's concept lives in it. ilastik lets the
user *put it there by hand*, in twenty minutes of drawing, on a laptop.

So the tool does not merely accept a `.ilp` if one happens to exist. **It tells
the user to go make one, and it says when.** The trigger is measured, not felt:
when the classical floor has been scored against an adjudicated survey (F2's
caveat) and the ranking is not worth acting on, that is the moment the agent says:

> *The generic descriptor does not separate what you are describing. Open ilastik,
> draw pixel annotations on a few of these tiles, save the project, and hand me the
> `.ilp` — I will score the survey with it.*

Which is the same shape as everything else in this doc: the artifact comes back as
a **file**, and microclaw runs it. It is simply a file microclaw did not compile.

**How it attaches, and the two things that are not free.**

It fits the `Descriptor` seam unchanged — ilastik emits a per-pixel probability
map, and pooled statistics of that map (mean, high percentiles, thresholded area,
connected-component count and spacing) are a descriptor vector like any other.
`FewShotProbe` sits on top of it exactly as it sits on the classical one.

**That now has a run behind it, and the run says the pipe carries data.** [spike F_3/F_5]
A human drew two labels on six tiles; headless scored 45 held-out tiles; the maps pooled
into 7-vectors; `FewShotProbe` fitted on five positives and twenty negatives ranked the
rest at **AUC 1.000**. End to end — GUI → `.ilp` → subprocess → probability map → pooled
vector → probe → ranking — with no step improvised.

**Read that 1.000 as plumbing, not as a backend result.** The classical floor scores
**1.000 on the same tiles with the same probe**, because this concept was built to
separate photometrically (F2's caveat, and the spike prints the warning itself). There
was nothing here for ilastik to win. What is now established is that the seam is real
and the pooling is not fictional; what is *not* established is that ilastik buys
anything, and the only honest place to ask that is F9's oriented/configural concepts or
a real sample.

Three facts about the map, since the doc previously named none of them:

| | measured (1.4.2, 2026-07-17) |
|---|---|
| shape / dtype | `(256, 256, 2)` float32, range `[0.000, 1.000]` |
| axis order | `axistags` on the dataset says **y, x, c** — read it, don't assume it |
| where | HDF5 dataset `exported_data`; one channel per label drawn |

**And the channel-order question turned out not to bind.** Both channels scored 1.000,
which is not luck: with two labels the channels are complements (`p` and `1−p`), and a
*fitted* probe learns its own polarity from the verdicts. So which channel is the user's
class does not matter to `FewShotProbe` — it matters to a human reading the map, to
anything thresholding a raw probability, and to a project with 3+ labels, where the
channels stop being complements. `LabelNames` (`['Label 1', 'Label 2']`) is still worth
reading; it is just not load-bearing for the probe.

**The `.ilp` is portable, which was the other open worry.** Its remembered raw-data
paths are stored **relative to the project file** (`train/train_00.tif`), not as absolute
paths into the operator's home — and prediction does not need the training data at all,
since headless takes its inputs on the command line. So "the user hands back the `.ilp`"
really is a self-contained artifact to pin. (The spike claimed the opposite on its first
run, having resolved a relative path against the working directory and then solemnly
reported that `FileSystem` — an enum, not a path — does not exist on this machine.)

**Two things are still not free**, and the first one now has a number.

**It is a `mode="score"` backend only, and the number is 7.6 seconds.** [spike F_4]
ilastik headless is a subprocess with a multi-second start-up. You cannot pay that
inside a 100–500 ms tile dwell, so it can never be an online scanner. It does not have
to be: the two-pass flow already writes every survey tile to disk, so the ilastik
backend runs **once, batched, over the whole survey directory** between passes. That is
not a workaround — F7 already argues the two-pass mode is the only one that compiles,
and this backend simply cannot pretend otherwise.

That was an assertion when it was written. Measured (1.4.2, arm64 laptop, 45 tiles of
256², 2026-07-17):

```
F_4  one tile alone      : 7.85 s   (start-up + one tile)
     marginal per tile    : 255 ms   (from the 45-tile batch)
     start-up, implied    : 7.59 s
     -> batching 45 tiles saved 334 s
```

**The ruling holds, but read WHICH number carries it.** The blocker is the 7.6 s
start-up, not throughput: the marginal 255 ms per tile would sit *inside* a 100–500 ms
dwell. So "ilastik can never scan online" is precisely true of the artifact ilastik
actually ships — a subprocess per invocation — and would stop being true of a
hypothetical persistent predictor, which is not on offer. Do not upgrade that into "the
model is too slow"; it isn't. It is too slow **to start**, 45 times over, which is the
whole reason batching saves 334 seconds on a survey this size.

And the 255 ms has a scale factor hiding in it: these are **256² tiles**, the size
microclaw's own descriptor decimates to. Handed a full 2048² camera frame — 64× the
pixels — that marginal is ~16 s/tile if it scales linearly, and the batched path stops
being comfortable too. **An ilastik backend must decimate exactly as the classical
descriptor does**, and nobody should quote the 255 ms for a full frame. (We are choosing
a stage position, not a pixel — the same argument `ClassicalDescriptor` already makes.)

### F5b — what the command line actually has to say [spike F_0/F_0b/F_3]

`export_detector_scorer` would have to emit an ilastik command line, and this doc used to
contain none — the flags are now quoted from ilastik.org's headless docs (read
2026-07-16) *and* exercised against the binary (1.4.2, 2026-07-17). Four things only the
run could teach, each of which would have cost a rig session:

* **`--readonly` eats an input file.** It is declared `--readonly [READONLY]` — an
  *optional* value — so a bare `--readonly` before the positional inputs makes argparse
  swallow the first tile as its value (`invalid value 'score_000.tif' for --readonly`).
  Write `--readonly=true`. A survey that silently drops its first tile to an argparse
  detail is the kind of bug that gets blamed on the microscope.
* **The export flags are absent from `--headless --help`, and work anyway.** ilastik
  parses in two stages: the generic parser, then the workflow named by `--project` adds
  `--export_source` / `--output_format` / `--output_filename_format` / `--raw_data`. The
  spike's first draft grepped the help text and reported them "NOT PRESENT", which is a
  false negative from a probe built to be more trustworthy than the docs. The parser
  itself is the oracle: point it at a nonexistent project *with* the flags, and if it
  complains about the **file** rather than the flags, they are real.
* **The project remembers its own export settings, from a GUI session we never saw** —
  `OutputFormat: compressed hdf5`, `OutputFilenameFormat: {dataset_dir}/{nickname}_{result_type}`,
  `OutputInternalPath: exported_data`. Omit `--output_format` and the artifact's encoding
  depends on whatever the operator last clicked. **Always pass it explicitly**; an output
  format inherited from an unobserved GUI session is not reproducible, and reproducibility
  is the bar.
* **The `.ilp` records `ilastikVersion` (`1.4.2`)** — so the manifest pins the version the
  project was drawn in for free, which matters because F5's artifact is only as portable
  as the ilastik that reads it.

(A note for whoever runs this on the rig: on macOS a failing ilastik prints `Launch error`
and `Please get in touch with the ilastik team`. Measured — it appears **only** after a
run has already failed, never on exit 0 or on an argparse rejection. It is bundle-stub
noise on the crash path, not a broken install, and not a clue.)

**It does not compile to a self-contained hook, and `hook_manager` already says so.**
An ilastik-backed detector must shell out — and `hook_manager.py:14` bans `subprocess`
in hook source. So `export_detector_scorer` would emit a module microclaw's own linter
flags. That collision is not a bug in either one; it is the design telling the truth.
The classical detector compiles to *numpy and a dozen floats*. An ilastik detector
compiles to *a hook plus an ilastik install plus a `.ilp`*. Both run offline, both are
reproducible, both are files — but the second is a strictly weaker artifact, and the
tool should grade it that way rather than quietly emit a hook that trips the lint.

**And a user-supplied `.ilp` is untrusted bytes**, by F4's argument and a shorter
path than a hook: it gets a hash pin in the manifest and the same confirmation step.
Being handed it by the user is not consent; it is provenance.

### F5a — the `.ilp` is a pickle. Measured, and the doc was right for the wrong reason. [spike F_2]

The paragraph above was an *argument by analogy* — a `.ilp` is untrusted "by F4's
argument", and F4's argument is about **unpickling**. HDF5 is a data format, so the
analogy was doing real work and nobody had opened the file. Open it (ilastik 1.4.2,
arm64 macOS, 2026-07-17):

```
F_2  datasets that are PICKLES (F4: is a .ilp data, or code?):
       PixelClassification/ClassifierFactory  (342 bytes)
          imports on load: copy_reg._reconstructor
          imports on load: lazyflow.classifiers.parallelVigraRfLazyflowClassifier.
                           ParallelVigraRfLazyflowClassifierFactory
          imports on load: __builtin__.object
```

`b'ccopy_reg\n_reconstructor\np0\n(clazyflow.classifiers...'` — a protocol-0 pickle
whose GLOBAL opcode imports from `lazyflow`. **A `.ilp` is executable code**, so
F4's "untrusted by the same argument, and by a shorter path" is *exact* rather than a
stretch, and `load_detector` must verify the hash **before** anything opens the file.

Two details sharpen it. **The pickle predates the training.** That project was created
headless and has no labels and no classifier: a `.ilp` is code from birth, not once
somebody teaches it something — so "it's just the user's annotations" is never a reason
to skip the pin. And **the spike said the opposite first.** Its dtype filter skipped
h5py's `object` dtype, which is exactly what ilastik stores the pickle in, so it
reported "no pickles detected" about a file opening with `ccopy_reg` and recommended
this doc go and *weaken* F4. A scan that cannot see the thing it is looking for returns
a clean bill of health — which is design/20's failure wearing a lab coat, caught only
because the file was read by hand afterwards. The check now reads every dataset and
prints *which modules the pickle imports*, because "it is a pickle" is abstract and
"it imports `lazyflow.classifiers...` on load" is not.

**Every claim in F5 is an argument, and the spike is how they stop being one.**
`design/26-ilastik-imjoy-spike.py` stages F_0–F_5, on a laptop or the rig: what the
*installed* binary's headless flags actually are — the spike's are quoted from
ilastik.org's headless docs (read 2026-07-16), which also confirm this finding's
premise in ilastik's own words ("use the graphical user interface to create a project
and train a classifier by manually drawing annotations... save your project and quit"),
and confirm that the map is "a multichannel image, where each channel corresponds to a
class you defined during training" — so **which channel is the user's is set by the
order they drew labels in**, which is a fact about a GUI session, not about the file
we are handed; what the `.ilp` holds, read as plain HDF5 with no ilastik — the class names, so
the tool can know **which probability channel is the user's** instead of asking, and the
raw-data paths it remembers, because a project that points at the operator's Desktop is
not a portable artifact to pin; whether it stores **pickles**, which is the difference
between F4's argument applying *exactly* and this doc borrowing `torch.load`'s reasoning
for a file that does not run; what a probability map's real shape, dtype and axis order
are; where the multi-second start-up above actually comes from; and whether the pooled
vector ranks anything. That last one has a trap the spike prints for itself: on section
B's photometric concept the classical floor already scores 1.000, so a tie proves the
**plumbing**, not the backend. Point it at F9's oriented/configural concepts, or at a
real sample, before believing any ordering.

## F6 — the adjudicator's mistakes compound, because its verdicts are labels

This is the one place where swapping in a weaker judge does not merely cost a bad
call. A weak local VLM that mislabels three of twelve crops does not just waste an
acquisition: it feeds three wrong labels into the refit, which **moves the
direction in feature space** *and* — for an unnormalised head — **rescales the
score** (F3), and then proposes an operating point on top of both. Bad adjudication
is worse than none, because no adjudication at least leaves the probe's ranking
honest.

So the fallback for a token-constrained lab is not a weaker model — it is a
**human**. Send the same top-k and borderline-band crops to the person who wrote
the description and who will have to live with the false negatives. This needs no
new **seam**: `review_roi_candidates` already returns image blocks, so a human is
simply a different consumer of the same contract.

It does, however, need a **surface**, and the first draft said "this needs no new code"
and left it there — which is true of the seam and false of the experience. An agent
consumes image blocks; a human consumes a *viewer*, and twelve base64 crops scrolling
past a chat transcript is not one. Given that this finding's whole point is that a
careless verdict is worse than no verdict, shipping the human path without a way to
actually look at the crops would undercut it. The surface is ImJoy, it is MIT, and
pycro-manager already documents it — see the survey.

And because the labels are now provenance-bearing artifacts, `refine_roi_detector`
**records who adjudicated each verdict** — model ID and version, or `"human"` —
alongside the verdict itself. design/20 and design/21 are both about scores whose
origin got lost. When the ladder eventually proposes a bad operating point, the
first question anyone asks is who labelled those twelve crops, and the compiled
detector has to carry the answer.

### F6a — record the verdicts, not just the verdict-giver: the compile step needs a lockfile

Recording *who* judged makes a bad fit **auditable**. It does not make a good fit
**repeatable**, and those are different properties — which is worth saying plainly,
because the doc came within one sentence of claiming the second and delivering only
the first.

Hold the two claims apart:

* **Is the ACQUISITION reproducible?** Yes, and it has nothing to do with which model
  adjudicated. The acquisition *is* the fitted probe — a dozen floats in a file. Run it
  in a year and it makes the same decisions. That is F7, and it holds however the probe
  was arrived at.
* **Is the COMPILATION reproducible?** As specified above, **no.** Re-run
  `train → review → refine` against a newer adjudicator and the same five examples yield
  *different verdicts*, hence a different direction in feature space, hence a different
  detector. The adjudicator is a moving part, and F6's whole point is that its output is
  not an opinion but a **training label**.

That gap is real, and it has a cheap and boring fix: **persist the verdicts themselves,
not merely their author.** A `verdicts.json` beside the probe — crop SHA-256 → verdict →
adjudicator ID → timestamp — makes `refine_roi_detector` able to **replay** a stored
adjudication instead of re-soliciting one. The compile step becomes deterministic
*without freezing any model*: you reproduce the build by **caching its input**, exactly
as you reproduce a build by keeping the lockfile rather than by pinning the compiler's
authors.

It is also what makes the refit loop honest in a second way: re-fitting is now a pure
function of (examples, mined negatives, verdicts), so a detector's whole provenance is
three files and a version string.

**Freezing the judge is the wrong lever. Freezing the judgements is the right one, and
it costs a JSON file.** (This is the answer to the strongest argument anyone makes for a
pinned CLIP text encoder — see YOLOE, below.)

## F7 — the feature compiles, and the thing that resists is not the model

Hold the pipeline up against the bar.

| step | when it runs | needs a model? | compiles? |
|---|---|---|---|
| `train_roi_detector` | before the scan | no | — (it *is* the compiler) |
| `run_roi_survey` (`mode="score"`) | the scan | **no** — numpy | **yes** |
| `review_roi_candidates` | between passes | yes | — (it is the compiler's input) |
| `refine_roi_detector` | between passes | no | — (it *is* the compiler) |
| `run_multiposition_acquisition` | pass B | no | yes |
| `run_roi_survey` (`mode="acquire"`) | the scan | no | **see below** |

**The language model is never in the acquisition loop.** It appears in exactly one
step — `review_roi_candidates` — and that step runs *between passes*, with the
stage parked and the camera idle. `ROIDetectorHook.image_process_fn` calls
`probe.score(descriptor.describe(image))`, which is numpy. No socket. The invariant
holds.

So **the fitted probe is the compiled hook.** What falls out of a microclaw session
is a directory under `~/.microclaw/detectors/<name>/` holding a description, some
example thumbnails, the adjudicated verdicts and who gave them (F6a), and ~12 floats.
That artifact runs on the rig forever, offline, with no API key — and
`export_detector_scorer` emits it as a standalone pycro-manager `image_process_fn` that
does not import microclaw at all. **That is the deliverable. The conversation was the
build step.**

**Which means `mode="acquire"` is the mode that cannot be compiled**, for two
independent reasons, and both are structural rather than incidental:

1. A budget is a **rank**, and a rank does not exist until the survey's score
   distribution exists. An online hook, standing at tile 7 of 400, cannot know
   whether this tile is in the top 20 — so it is forced back onto an absolute
   threshold, which is the number F3 says nobody can supply.
2. **Nobody is watching.** On a slide with no positives, the two-pass flow shows a
   human twenty crops of debris and the run stops. The online mode has already
   fired twenty z-stacks.

So the two-pass flow is not merely the safe default — **it is the compilable one**,
and the online mode's opt-in gate now has a reason behind it rather than a shrug.
Keep the online mode, for the cases where a revisit genuinely is not acceptable, such
as live-cell samples; just be honest that it is the mode that trades the guarantee away.
(The runner it rides on is no longer hypothetical — it ships; see the online mode,
below.) And it is worth knowing that the only published example of a network driving a
pycro-manager acquisition runs two passes with the model between them, and puts no
network in the image loop — **F8**.

**And the honest limit.** If a lab's concept is not separable by anything on the
descriptor ladder, the only thing that can find it is the model that read the
sentence — and *that does not compile*. The answer then is more labels, not more
layers (ilastik, micro-sam; offline), which is still compile-shaped: produce a better
artifact offline, then compile it. What is **not** the answer is putting a 1–3 s
vision call in the scan loop. Hitting that wall is the signal to stop, not the
signal to lower the bar.

## F7a — where this code lives, and the two places the first draft put it wrong

The first draft proposed a new `microclaw/detectors/` package with four modules. That
is wrong twice over, and the errors point in opposite directions — one invents a
boundary that does not exist, the other misses one that does.

**`microclaw/` has no subpackages.** Every module is a flat `microclaw/*.py`.
`detectors/` would be the first package in the tree, introduced to hold four files, one
of which is a dozen floats. Ship flat; earn the package later. The thing that will
*genuinely* earn it is the optional backends — `embed`, `cellpose`, `bioimageio` are each
a module with its own optional dependency and its own lazy import, which is exactly what a
package is for. There are none of those on day one.

**The classical descriptor is a reimplementation of `image_analysis.py`, and must not
be.** Compare the spike's `features()` against what is already in the tree:
`detect_features` (`image_analysis.py:93`) already does `blob_log` on a
median-background-subtracted, peak-normalised image and returns blob count, centroid and
SNR; `compute_stats` and `laplacian_variance` already do the photometry and the focus
metric. The descriptor is *those numbers concatenated*, plus a band-energy ratio and a
nearest-neighbour spacing. Writing it fresh in a new package would fork the definition of
"a blob" between the descriptor and the `find_features` tool, and they would drift.

The boundary that actually holds is **stateless vs. fitted**:

| | what it is | where it goes |
|---|---|---|
| `ClassicalDescriptor` | pure functions, image → vector, no state, no persistence | **`image_analysis.py`** — built *on* `detect_features` / `compute_stats` |
| `FewShotProbe`, `augment`, `DESCRIPTOR_REGISTRY`, `export_detector_scorer`, `export_ranked_positions` | fitted, learned, serialised | **`detectors.py`** (new, flat) |
| `ROIDetectorHook` | a hook | **`hooks.py`**, with the other hooks |
| the detector store | consent, hashes, a manifest | **`hook_manager.py`** — see below |

**And `store.py` should not exist at all.** Read the first draft's stub back: a manifest
under `~/.microclaw/`, a SHA-256 pin of the exact bytes the user consented to, a refusal
to load anything edited since. That is `hook_manager.py`, with the nouns changed. Standing
up a *second* copy of a **security** mechanism is how the two drift and one of them misses
a fix — and F4 argues that a weights file and a hook are untrusted by *the same argument*.
If they are the same argument they should be the same manifest. So `hook_manager` widens
to cover a second artifact **kind** (a detector: probe JSON, example thumbnails, verdicts,
and any weights or `.ilp`), and F4's hash-pinning story and the hook consent story become
one story — which is what the doc has been claiming they are.

**This is the only place in this document that decision is argued.** Everything
downstream — the stubs, the tests — just points here.

## F8 — the two-pass flow is not our idea: pycro-manager already ships it, and it made the same two choices

Everything above was argued from first principles against the compile bar. It is worth
knowing that somebody with a real microscope, a real sample and a real network got
there first — and that the shape they landed on is, step for step, the flow in the
Proposal below.

pycro-manager's `guiding_acq_with_neural_network_attention` notebook does targeted
multi-contrast imaging of pancreatic-cancer tissue: an attention-based multi-instance
learning model (ResNet18 instance classifier + attention bag classifier) reads a 4×
brightfield overview and picks where to spend 20× second-harmonic-generation imaging.
Reading its source rather than its prose, here is what it actually does:

```python
core.snap_image()                                   # 1. one low-res overview
...                                                 # 2. dense patches -> MIL inference
indices = np.argsort(A[:, 1])[::-1]                 # 3. rank by attention score
indices = indices[: args.top_k]                     #    args.top_k = 4
...
for i, pos in enumerate(pos_list):                  # 4. revisit each winner
    core.set_xy_position(pos[0], pos[1])
    with Acquisition(directory="test", name=...) as acq:
        events = multi_d_acquisition_events(z_start=..., z_end=..., z_step=5)
        acq.acquire(events)                         # 5. a NEW acquisition per hit
```

**Two things are absent, and each is a finding above.**

**There is no hook.** The notebook imports `Acquisition`, `multi_d_acquisition_events`
and `Core` — and no hook of any kind. The model does not run in an
`image_process_fn`. It runs *between* two acquisitions, with the stage parked, exactly
where F7 puts the adjudicator. The one published example of a neural network steering
a pycro-manager acquisition **does not put the network in the acquisition loop.**

**The operating point is a budget, not a threshold.** `args.top_k = 4`. Not "acquire
every patch scoring above 0.7" — *take the best four*. F3 argues at length that a
threshold is a claim about the model that nobody can supply and that a refit
invalidates, while a rank survives everything; the notebook simply writes the budget
down as a constant and moves on. **It asked the right party for the right number
without needing to be told.**

**One honest deduction.** Their two-pass structure is *partly forced*: they switch
objective (4× → 20×) and modality (brightfield → SHG) between passes, and you cannot do
that inside one acquisition. So I cannot claim they weighed online-vs-two-pass and chose.
What is **not** forced is `top_k`. Nothing about swapping an objective compels you to
rank rather than threshold — that choice was free, and they made it the way F3 says to.

**And the difference is the whole of design/26.** Their detector is a model *pretrained
for pancreatic cancer detection* — a labelled whole-slide corpus and a training run that
happened months before the microscope was switched on. It answers one question, the one
it was trained for. Ours is fitted from **five example images in ~0.55 s**, mid-experiment,
on the concept the user described this morning. Same two-pass skeleton; same top-k
selection; same "no model in the acquisition loop." The only thing microclaw changes is
**who compiles the detector, and how long it takes** — and that is precisely the gap the
ask describes. F8 is the strongest evidence in this document that the architecture is
right, and it is not our evidence.

## F9 — the ladder has three rungs, not two, and the middle one is free. [spike E]

Every backend above the classical floor — the DINOv2 row, Cellpose, bioimage.io —
is justified in this document **by argument**. It has to be, because on section B's
synthetic data the classical floor already scores a perfect AUC: there is no gap for
a 2 GB torch dependency to climb, so the survey table's "upgrade path" verdict rests
on a claim nobody measured. "It does not measure YOLOE, or DINOv2, against the
classical floor" is in this doc's own list of things it does not do.

Spike E does the half of that measurement that needs no torch — and torch is not
installed, which is the honest state of the rig too (section C). You cannot run
DINOv2 here, but you do not have to, to prune, because the question those rows sit on
is answerable with numpy: **hold the shipped probe fixed, build concepts of
increasing semantic difficulty, and change only the descriptor.**

```
E   probe = LogisticProbe, fixed. Only the DESCRIPTOR moves. AUC, 3 seeds.

concept                                classical        classical+oriented
photometric  (section B's concept)     1.000 +/- 0.000   1.000 +/- 0.000
oriented-texture (aligned fibers)      0.571 +/- 0.068   1.000 +/- 0.001
configural   (collinear triple)        0.778 +/- 0.018   0.764 +/- 0.004
```

Read the rows as a ladder, because that is what the survey table is.

**Photometric** — the classical floor is already perfect, and three extra texture
scalars neither help nor hurt. A learned embedding here is 2 GB buying nothing. This
is the doc's "classical is the default" claim, now with a floor *under* it and not
only a ceiling *over* it.

**Oriented-texture** — the floor **collapses to chance (0.57)**. The concept is "all
the fibers point the same way," and the classical descriptor is rotation- and
flip-invariant by construction (B_3), so it is *structurally* blind to it — not
weak at it, blind. And a still-free numpy descriptor climbs **all the way back
(1.00)**: three structure-tensor scalars (energy-weighted coherence, global-tensor
coherence, orientation entropy), no torch, still `augment_helps = False`, still
compiling to a self-contained hook. A whole class of "complex" concepts — fiber
alignment, tissue anisotropy, cell polarity — lives on this middle rung and **does
not need the embedding backend at all.**

**Configural** — the honest one. The first draft of spike E *predicted* chance here
and was wrong: "are these three blobs collinear" is matched on count, size,
brightness, spacing and orientation, yet classical still gets **0.778**, because
collinear blobs span a longer extent than a triangle and that bleeds into band
energy. The texture rung does not move it (0.76). So this is where the
embedding/ilastik ladder genuinely earns its place — relational structure that no
hand-crafted numpy summary captures — and the spike (torch absent) cannot climb it.
What it *can* do is set the bar: a learned backend on this concept must beat **~0.78,
not 0.5.** "Measure DINOv2 someday" now has a number to beat and a benchmark to beat
it on.

**What this prunes.** The survey's `embed` / `cellpose` rows should be gated on a
concept being *above the texture tier* and *beating the ~0.78 floor*, measured — not
reached for the moment classical dips below perfect. There is a cheap middle rung the
table did not have, and it is where a lot of "an ML model is needed" concepts
actually resolve. The caveat is the standing one: these classes still
differ by construction, and the configural leak is the reminder that even a "clean"
synthetic concept bleeds into generic stats. The transferable claim is narrow and it
is about **structure, not cells**: a rotation-invariant descriptor cannot see
orientation, a cheap tensor can, and configuration caps both well short.

---

# The survey

Judged against the actual requirement — *learn from ~5 examples, mid-experiment,
on the rig, within a tile-dwell budget of roughly 100–500 ms, safely, and it must
compile.*

| option | trains from 5 examples? | per-tile cost | new deps | licence | verdict |
|---|---|---|---|---|---|
| **Classical descriptor + few-shot probe** | yes — **~0.55 s to compile a detector** *(measured: 526 ms featurising 5 examples + 20 mined negatives, 23 ms to fit)* | **~60–80 ms** *(measured, this laptop, incl. decimation; it moves tens of percent run to run — read it as "comfortably inside the dwell", not as a point)* | **none** | — | **default backend — the photometric rung (F9)** |
| **Enriched classical (structure-tensor texture) + same probe** | yes — same fit, three extra numpy scalars | **~+5–10 ms** *(estimate; a few `gaussian_filter`s)* | **none** | — | **the FREE middle rung (F9): recovers oriented/textural concepts the floor is blind to (0.57 → 1.00, measured), still compiles, no torch** |
| **Frozen ViT embedding (DINOv2/v3) + same probe** | yes — only the descriptor changes | ~50–150 ms CPU *(estimate)* | torch (~2 GB) | per-checkpoint; **verify** | **the upgrade path — but ONLY for concepts above the texture tier that beat the ~0.78 configural floor, measured (F9), not the moment classical dips** |
| **Cellpose (CP4 / cpsam / cpdino)** → per-object features → same probe | segmenter is pretrained, nothing to train; the probe learns the *class* | ~0.1–0.5 s GPU, seconds CPU *(estimate)* | torch, cellpose | BSD-3 | **second backend — when the ROI is an object, not a scene** |
| **micro-sam** | interactive annotation + automatic instance segmentation | heavy *(estimate)* | torch, SAM | MIT | **not a scanner** — its value is *producing* the labels; offline aid, out of scope |
| **UMAP + SVM** (the Edge Impulse feature-explorer shape) | yes | 2–36 ms *(measured)* — but see below | umap-learn, sklearn | BSD-3 | **rejected as a scorer; adopted as the REVIEW UI.** See below |
| **YOLOE-26** (open-vocabulary YOLO) | **text prompts only, today** — the visual-prompt path is unimplemented in Ultralytics | 6.2 ms/tile *(vendor figure: YOLOE-26L, 640 px, T4)* — GPU-class hardware assumed | ultralytics, torch | **AGPL-3.0 — code, weights, and anything trained from them** | **rejected.** See below |
| **ilastik headless** | **not by us** — but the USER can, in the GUI, and hand back the `.ilp` (F5) | subprocess, seconds — **batched between passes, never per-tile** | conda monolith | GPL-2.0+ *with an exception; GitHub cannot classify it — read their LICENSE before shipping* | **the escape hatch.** The one rung needing no GPU and no fine-tune; `mode="score"` only |
| **bioimage.io** (`bioimageio.core.predict`) | no — it's a *distribution* channel | model-dependent | bioimageio.core | per-model | **adapter** — this is the honest answer to "a battery of networks": don't bundle a zoo, run the lab's model by ID |
| **ImJoy** (+ Kaibu) | **it is not a model** — it's a plugin/RPC bridge from the Python kernel to a browser UI | n/a — never in the scan | imjoy, imjoy-jupyter-extension | MIT | **not a backend at all — the REVIEW UI.** The surface F3 and F6 both need and neither supplied. See below |
| **Claude vision on the thumbnail** | it is the only thing that reads the **description** | ~1–3 s + $ *(estimate)* | none | — | **not a scanner — the adjudicator** |

Licences were read from each project's LICENSE via the GitHub license API on
2026-07-13, not recalled: cellpose BSD-3-Clause, micro-sam MIT, bioimageio.core
MIT, umap-learn BSD-3-Clause, `ultralytics/ultralytics` **and** upstream
`THU-MIG/yoloe` both AGPL-3.0, ilastik unclassifiable. Re-run 2026-07-14 for the
review surface: `imjoy-team/imjoy`, `imjoy-team/kaibu` and `imjoy-team/imjoy-rpc`
are all MIT. Cost columns marked *(estimate)* are exactly that — nobody has run
them on the rig.

## UMAP + SVM — half right, and the right half is not the half it looks like

Edge Impulse's feature explorer is *features → UMAP → SVM*, and it is a reasonable
thing to propose here: fast, few-shot, and it produces the 2-D picture F3 keeps
demanding. It was worth measuring rather than arguing about, so spike D measures it
literally — same descriptor, same five examples, UMAP inserted before the head:

```
D. UMAP in the scoring chain?

seed 0    AUC features -> SVM  1.000    -> UMAP(2d) -> SVM  0.759
          UMAP transform, ONE tile   35.8 ms
          rank corr between two UMAP seeds  -0.032
seed 1    AUC 1.000 -> 0.747    2.4 ms    rank corr  0.084
seed 2    AUC 1.000 -> 0.983    2.5 ms    rank corr  0.626
```

**The SVM half of the suggestion was right, and F2 already took it** — a margin
head is the fix for the nearest-mean collapse, and it is where the whole gain in
this doc's revision comes from.

**The UMAP half is a no, and the last column is why.** Two fits of the *same points*
with a different `random_state` produce rankings that barely correlate. The score is
a function of the seed, not of the tile — and F3's entire argument is that a refit
which *rescales* is survivable by rescoring, whereas one that *reorders at random*
is not a classifier at all. It also costs AUC (UMAP is unsupervised, so a 12→2
bottleneck discards the very direction the labels identify) and it costs
milliseconds per tile that a scanner does not have. Against the compile bar it is
worst of all: a stochastic, non-parametric embedding whose `transform()` depends on
the training manifold is not a file you can hand a lab.

**But it earns exactly the place Edge Impulse actually gives it: the review UI.**
F3's demand is that the operating point be chosen *by looking*. A 2-D map of the
survey — the five examples marked, the candidates coloured by score, the borderline
band picked out — is precisely the artifact to hand the adjudicator when they choose
*k*. So UMAP goes inside `review_roi_candidates`, as an **optional extra**, and never
inside `ROIDetectorHook`. It is a lens, not a link in the chain.

But a lens needs something to be mounted in, and until now this document did not say
what. That is ImJoy.

## ImJoy — not a backend, and the hole in this design it actually fills

The first draft filed ImJoy next to bioimage.io as if they were one thing. They are
maintained by overlapping people and they are not one thing, and reading
pycro-manager's `pycro_manager_imjoy_tutorial` notebook settles it in one line:
**there is no model in it.** No `torch`, no weights, no inference, no
`image_process_fn`. What there is:

```python
!pip install -U pycromanager imjoy imjoy-jupyter-extension
from imjoy import api
from pycromanager import Core

class MyMicroscope:
    async def setup(self): ...
    def snap_image(self): ...                       # runs in the Jupyter kernel
    async def run(self, ctx):
        viewer = await api.createWindow(src="https://oeway.github.io/itk-vtk-viewer/")
        ...
api.export(MyMicroscope())                          # register with the ImJoy core
```

ImJoy is an **RPC bridge**: Python objects in the kernel (which is where `Core` and the
microscope live) exported to a browser UI, and browser widgets callable back from
Python. In pycro-manager's docs it is used for control and display — snap/live buttons,
exposure and binning, a device-property browser, an `itk-vtk-viewer` window. It is a
*surface*, not a scorer. It belongs in the survey table only to say so.

**So why keep it? Because this design has an adjudicator with nowhere to sit.** Two
findings above write cheques against a UI that does not exist:

* **F3** insists the operating point be chosen *by looking* — and spike D's UMAP map is
  "the artifact to hand the adjudicator." Hand it to them **where?**
* **F6** concludes that the fallback for a lab with no API budget "is not a weaker model
  — it is a **human**," and then waves: *"This needs no new code: `review_roi_candidates`
  already returns image blocks, so a human is simply a different consumer of the same
  seam."* That is true of the **seam** and false of the **experience**. An agent consumes
  image blocks. A human consumes a *viewer*. Today the only surface a human adjudicator
  has is scrolling twelve base64 crops past a chat transcript and typing verdicts back as
  prose — which is not a review tool, and F6 is the finding that says a sloppy verdict is
  worse than no verdict, because verdicts are **training labels**.

### The measured constraint: `api` does not exist outside a kernel [spike G_1]

Before any of that: the surface does not work the way this doc quietly assumed. Measured
(imjoy 0.11.20, CPython 3.11, no kernel, 2026-07-17):

```
from imjoy import api          -> imports fine
hasattr(api, "createWindow")   -> KeyError: 'createWindow'
                                  imjoy_rpc/__init__.py: return _rpc_context.api[attr]
```

The import **succeeds**; the object is a `werkzeug` proxy that resolves through an RPC
context which only exists inside an ImJoy plugin runtime — a Jupyter kernel with
`imjoy-jupyter-extension`, or the ImJoy web app. In a CLI process there is no context, so
`api` cannot be *touched*, let alone open a window. (It does not even raise
`AttributeError`, so `hasattr` propagates the `KeyError` — the spike's own crash was the
measurement.)

**So `review_roi_candidates(surface="imjoy")` cannot be an ordinary tool call**, and that
is not a detail — microclaw is a CLI agent driving a rig over ZMQ. F6's human adjudicator
needs **a notebook running beside the agent**, and the doc has to say so plainly or not
ship the surface. Note what this does *not* touch: `surface="blocks"` is unaffected, the
seam is crops-out-labels-in either way, and F6's argument that the fallback is a human
rather than a weaker model stands. What it costs is the pretence that the human's chair
is free.

ImJoy closes that, and it is already documented against the exact library we are on.
`review_roi_candidates` keeps its contract unchanged — **crops out, labels in** — and
gains an optional presentation: an ImJoy window showing the top-*k* and the borderline
band as a grid, the spike-D UMAP beside it with the five examples marked, keep/discard on
each crop, and a *k* slider whose effect on the ranking the user can see before they
commit the afternoon. The verdicts come back through the same
`refine_roi_detector(verdicts, adjudicator="human")` call, into the same `verdicts.json`
that F6a persists.

**And Kaibu gets us out of the `example_boxes` hole.** ImJoy's annotation viewer (Kaibu —
`add_image`, `add_shapes`, `add_points`, deliberately napari-shaped) draws rectangles and
polygons on an image *in a browser canvas*. Compare the open question the stubs currently
carry: *"MM's rectangle tool normally sets the CAMERA ROI (a sensor crop). That the bridge
can hand back the coordinates WITHOUT applying them is unverified — check on the rig."*
That question does not need answering; it needs **avoiding**. Draw the boxes in Kaibu,
which cannot crop a sensor because it has never heard of one, and `example_boxes` stops
depending on a Micro-Manager behaviour we are unsure of. Same for the human's pen in **F5**:
the "go draw annotations" instruction has a viewer to point at.

**Two constraints, and neither is small.**

**It runs at review time, in a browser, and never touches the invariant.** The bar is *no
network call at image time*. ImJoy is a UI for the step where the stage is parked and the
camera is idle — F7's one model-shaped step — and it must never appear in
`ROIDetectorHook`, in `image_analysis`, or in an exported hook. Its dependency lives in the
`review` extra alongside `umap-learn`, and a hook that imports it is a bug the AST test
catches.

**Its plugin sources come off the network, which is F4 one layer up.** The tutorial's own
UI is loaded by URL — `api.createWindow(src="https://oeway.github.io/itk-vtk-viewer/")`, and
another example fetches a plugin from a **gist**. Remote code, executed on the operator's
machine, chosen by a URL string. That is exactly the argument F4 makes about a `.pt`, and it
gets exactly the same answer: **microclaw ships and self-hosts the review plugin it uses; it
does not `createWindow` a gist at runtime.** The blast radius is smaller than a hook's — a
browser tab, not the process holding the stage — but "smaller" is not "argue it separately."

## YOLOE — rejected, on four independent grounds

**1. The half we want is not implemented.** Ultralytics' YOLOE docs list a "Visual
Prompt" tab with **no implementation code underneath it**; only the text-prompt path
(`set_classes()`) has working examples. SAVPE — the encoder that would consume the
user's example images — is in the *architecture*, but the Ultralytics integration for
it is under development. (Checked 2026-07-14: `docs.ultralytics.com/models/yoloe/`,
`github.com/orgs/ultralytics/discussions/19783`.) And even when it lands, the
documented design fuses prompts from a **single** `refer_image` — genuinely *one*-shot,
an API limit rather than an annotation shortage, so it would not give us the averaging
over five examples that F2 depends on.

**2. The half that *is* implemented, we already answer better.** YOLOE's text encoder
refines CLIP-style embeddings over a model trained on Objects365v1, GQA and Flickr30k
and scored on LVIS — "person", "bus", "traffic light". *"Cells with condensed chromatin
at the metaphase plate"* is not a point in that space, and no prompt puts it there.
Meanwhile **we already have a language model in the process that has read the
description.** Bolting on a CLIP text encoder to do that job worse, on a domain it was
never trained for, while the good one sits idle in the same process, would be a strange
thing to build. (This is an **argument, not a measurement** — see "what this design does
not do".)

**3. It cannot participate in the refine loop.** The flow is: adjudicate a dozen crops →
the probe re-fits in milliseconds → rescore, re-propose the budget (F3). YOLOE's
adaptation path is fine-tuning; the docs' own example runs 80 epochs on a GPU. So even
granting it the domain, YOLOE could only ever be a **frozen scorer** — which is precisely
what the `Descriptor` seam is for. It is not a competing architecture; it is a candidate
*backend row*, next to DINOv2.

**4. The licence is a price, and the price is the whole project.**
`ultralytics/ultralytics` and upstream `THU-MIG/yoloe` are both AGPL-3.0, and Ultralytics
is explicit that this covers "the training code **and the models produced by that training
code**"; they sell an Enterprise Licence precisely so commercial users can avoid
open-sourcing. "Inference only" does not help — the pretrained checkpoints are themselves
AGPL artifacts. Taking it means relicensing **all of microclaw to AGPL-3.0-or-later**: not
MIT, not LGPL, and not plain GPLv3 (§13 makes GPLv3 and AGPLv3 mutually linkable, but the
AGPL portion keeps its network clause, so you end up with a GPLv3 project silently carrying
an AGPL obligation on part of itself). And `microclaw serve` is what makes that concrete:
AGPL §13 means a lab that patches microclaw for its in-house hardware and exposes it to the
bench next door owes that bench the complete corresponding source — the kind of obligation
an institution's legal office refuses on reflex. Being adopted and embedded is the entire
point of this project.

Note what is *not* an obstacle: `git log` shows a single copyright holder, so relicensing
needs no CLA. The switch is mechanically cheap. That is exactly why it has to be argued on
merits — and the merits are that we would relicense the whole project, irreversibly in
practice, to gain **one row of `DESCRIPTOR_REGISTRY`**, next to DINOv2, which we can ship
under BSD today.

### The best argument for YOLOE, and why F6a already answers it

There is one real argument on the other side, and it is not about accuracy: **a pinned
CLIP text encoder is fixed in time, and the Claude API is not.** Text in, same detection
out, this year and next. So — the argument runs — YOLOE buys *reproducibility*, the thing
this document says it cares about most.

The observation is correct; the conclusion does not follow, because it conflates the two
reproducibility questions F6a separates. **The acquisition** is already reproducible and no
text encoder is involved — the acquisition is the fitted probe, and the language model is
never in the loop (F7). **The compile step** is the real gap, and it is not YOLOE-shaped:
it is a moving *adjudicator* turning the same five examples into different labels. F6a
fixes exactly that for the price of a JSON file: persist the verdicts, and a refit replays
a stored adjudication deterministically, without freezing any model.

So the trade on offer is: **relicense all of microclaw to AGPL-3.0 to obtain determinism
that a lockfile already gives you** — in a component that is worse at the job, on a domain
it was not trained for, in the one step that never touches the microscope. Note also that
YOLOE's fixedness is not itself free: per F4 its loader *downloads and unpickles its
checkpoint by default*, so you only get a pinned artifact by doing the hash-pinning you
were going to have to do anyway.

**Freeze the judgements, not the judge.**

**So: measure first, relicense only against a number.** Score YOLOE's embeddings as a
`Descriptor` under `FewShotProbe` and compare the AUC to the classical floor and to the
DINOv2 row. That is a private experiment — AGPL constrains distribution, not what runs on
our own machine — so it costs an afternoon and no licence. The prior is that it will not
beat DINOv2 by enough to matter; but that prior is an argument, and this doc's whole
standard is that arguments do not get to pose as findings.

**What YOLOE gets right, and what we take from it.** Its three-way split — text prompt,
visual prompt, internal vocabulary — is independent confirmation of the shape this proposal
already has: *the description and the examples are different kinds of evidence and want
different machinery.* YOLOE fuses them into one model and pays for it by needing that model
to understand both a sentence and a microscope. We route them to two: the sentence goes to
the adjudicator, the examples go to the probe. We can afford that split because the language
model is **already in the process** — and, crucially, because it is only there at *compile*
time.

## The adjudicator is an interface, not Claude

Read every "Claude" in this document as **"the model that read the description."**
Nothing in the design depends on which one it is.

The argument above — *don't duplicate the language model you already have* — is not an
argument about Claude. It holds verbatim for a lab that cannot afford API tokens and
drives microclaw with a local VLM through Ollama: that lab has *its* model in the
process, and routing the description to a CLIP text encoder while it sits idle is just
as strange.

And the pipeline is already independent of it. No model appears in
`train_roi_detector`, `run_roi_survey`, `ROIDetectorHook`, `FewShotProbe`, the manifest,
the exporter, or the guards. The only model-shaped step is `review_roi_candidates`, and
its contract is *return the candidate crops as image blocks* — it does not call an API.
It hands crops to whatever agent is driving microclaw, and `refine_roi_detector(verdicts)`
takes labels back. **Crops out, labels in** is the seam, and anything that can look at a
picture while holding a sentence in mind can sit in it: Claude, a local Qwen-VL, or a
graduate student. F6 is the constraint on who you put there — and the graduate student,
who was the only one of the three with nowhere to sit, now has a chair: the ImJoy review
window, which is a *rendering* of this seam and not a change to it.

---

# Deferred full proposal — retained option research

This was the broader proposal before milestone 1 was narrowed. Its backend, UI, and
online-mode research is retained here for later milestones. Where its flow or stubs differ
from **Implementation decision — milestone 1**, the milestone section is normative.

> A conversation produces a **file**. The user's description and five example images
> are compiled — by the agent, with a human or a model adjudicating a dozen borderline
> crops — into a fitted probe of about a dozen floats. That probe is a pycro-manager
> hook. It scores every tile locally in well under the dwell, it needs no network, and
> it is what the lab keeps. The language model was the build step, not a runtime
> dependency.

Why this shape:

1. **It meets the bar.** The scan runs with no model in the loop, so the acquisition is
   reproducible, offline-capable, auditable, and free per tile. `export_detector_scorer`
   emits the artifact as standalone pycro-manager source that never imports microclaw.
2. **It uses the description correctly.** The description is a spec for a language model,
   not a feature vector. The adjudicator reads it; the probe never sees it. (Which
   adjudicator is not our business — F6.)
3. **It puts the model where it is affordable.** *k* vision calls per *fit*, not one per
   tile. A 400-tile survey costs 400 × ~70 ms of local compute and perhaps 15
   adjudications — cheap enough that a lab with no API budget can put a human in that
   slot instead.
4. **It asks the user for the number they can actually answer.** Not a threshold (a claim
   about the model) but a budget (a claim about their afternoon) — F3.
5. **It degrades cleanly.** Without torch, the classical descriptor is the probe. With
   torch, swap the descriptor. The probe, the hook, the tools, the manifest, the exporter
   and the safety gates are all backend-agnostic — the *only* thing a backend supplies is
   `image -> np.ndarray`.

## The earlier two-pass sketch (superseded ordering)

This sketch incorrectly trains before the survey from which it intends to mine negatives.
The canonical flow above replaces it with blind survey → reviewed negatives → fit → score
stored survey. It remains here to preserve the downstream option branches.

```
train_roi_detector(name, description, example_images)      # fits on 5 examples
        │                                                   # negatives mined later
        ▼
run_roi_survey(rows, cols, step_um, detector=name)          # tile scan, mode="score"
        │   hook scores every tile, saves crops, enqueues NOTHING
        ▼
review_roi_candidates(name, top_k=12, surface=...)          # crops -> adjudicator
        │   judged against the DESCRIPTION; the verdicts are labels (F6)
        │   verdicts.json is PERSISTED: the refit is replayable (F6a)
        │   surface="blocks" -> an agent;  "imjoy" -> a human, in a real viewer
        ▼
refine_roi_detector(name, verdicts, adjudicator)            # re-fit, propose a BUDGET
        │
        ├──▶ export_detector_scorer(name)                    # standalone scoring artifact
        ├──▶ export_ranked_positions(name, budget_k)         # post-survey top-k artifact
        │
        ├──▶ ranking not worth acting on?  ─────────────────┐  the floor does not span
        │                                                    │  the user's concept (F5)
        ▼                                                    ▼
run_multiposition_acquisition(positions=confirmed, ...)   use_ilastik_project(name, ilp)
        # existing tool, existing guards                      # user draws it in the GUI,
                                                              # we score with it, rejoin
                                                              # the flow at run_roi_survey
```

Pass A is a tile scan we already know how to do safely. The adjudication step is where
the user's description finally does work — and it is the **only** step that needs a model.
Pass B is `run_multiposition_acquisition` with `hook_strategy` — design/19's machinery,
unchanged. **Nothing in this flow needs the survey runner at all**: the survey hook
enqueues nothing, so both passes are the batched runners that already ship, with an
image-processing hook that only ever measures.

**This skeleton is not novel, and that is the best thing about it.** Overview → score →
rank → take the top *k* → revisit each winner in a fresh acquisition is, line for line,
what pycro-manager's own neural-network attention notebook does (**F8**). The one thing
microclaw changes is the box marked `train_roi_detector`: they arrive with a model
pretrained on a labelled cancer corpus, and we compile one from five pictures in half a
second, on the concept the user described this morning.

The branch on the right is the one to reach for rather than wait for. When the adjudicated
survey says the classical floor cannot separate what the user is describing, the honest
move is not a bigger generic feature space — it is to hand the user a pen (F5). They draw,
we score, and the flow rejoins itself.

The cost of two passes is a **revisit**: the stage goes back to the ROI. For a fixed
sample that is cheap, and the survey exposure (low power, coarse) has not meaningfully
bleached the thing we came for.

The affine pixel-to-stage geometry is already solved by `calibration.py` and
`center_feature`, but object attribution is not. A score on a tile does not say which of
several `find_features` centroids caused that score. Milestone 1 scores proposal crops and
records the selected object's bounds and centroid before applying the affine. A backend
that cannot attribute its score must return the whole field or ask for a box; it cannot
silently choose a generic centroid.

## Deferred option: online mode — opt-in, confirm-gated, and it does not compile

This mode is explicitly outside milestone 1. The findings below are preserved because
they define the safety and runner work required if revisit-free acquisition later becomes
necessary.

`mode="acquire"` uses `_acquire_survey_with_detector` — **shipped, in `tools.py`**: the
moment a tile scores above threshold, the hook pushes a z-stack event onto `candidates`
and the generator feeds it to the engine inside the same acquisition. No revisit. Needed
when a revisit is genuinely not acceptable — live dynamics, a photo-labile sample, drift.

**The machinery exists; the ignition does not.** The runner's *derived-event* mode
(`adaptive=False`: the whole survey pre-dispatched, the stream held open to drain
follow-ups the hook adds) is exactly what this mode wants, and the engine-side question
is answered — design/24's A_9 rig run had AcqEngJ accept `roi_0`, a position label it had
never seen, mid-acquisition, and land it in the NDTiff dataset. What that path does not
have is a **public caller**: `run_adaptive_survey` calls the runner with `adaptive=True`
(one tile at a time, the hook chooses the next), which is stop-on-condition and
refine-where-interesting, not fire-a-follow-up-and-keep-scanning.
`run_roi_survey(mode="acquire")` is the caller the derived-event path is missing, and
design/27's addendum is the standing lesson about shipping it late: a runner with no tool
in front of it is a capability the docs advertise by its private function name, and the
agent finds the gap by walking into it on the rig.

**The runner hands the hook its wiring as attributes** — `hook.candidates`,
`hook.progress` — because a `hook_strategy`-loaded class has nothing to close over.
`HookBase` initializes both to `None`, and that is the documented wrong-runner check: a
hook that needs them and finds `None` must **raise**, never log a quiet success.

It is also **the first thing in microclaw where a model, not a human and not the agent,
initiates an exposure** — and per F7 it is the one mode that must fall back on an absolute
threshold, because a rank does not exist until the survey does. So it is not the default,
and it does not run without:

* an explicit `confirm_fn` gate at start, quoting the threshold, the expected candidate
  count from a **prior scoring pass on this sample**, and the exposure budget it may spend.
  Without that prior pass there is no basis for the number at all, and the tool says so;
* `max_rois` and `max_followup_events` caps, enforced in the hook, logged when hit;
* `guard.check_xy` / `check_z` on **every** derived event *before* it is enqueued — the
  event bypasses `_run_protocol_at`, so nothing else will guard it;
* no illumination or channel changes from the detector path, ever. A detector may choose
  *where* to look. It may not choose to turn a laser up.

**Fail closed on the acquire path.** `MMPluginHook` fails **open** — a plugin error keeps
the image, because losing data to a bug is the worse failure (`hooks.py:261`). A detector
must fail **closed**: if the probe raises, log it and enqueue *nothing*. Dropping a
candidate costs one missed cell. A stack trace that fires an unguarded z-stack costs sample.
The asymmetry is the opposite of the plugin case, and the code should say why.

---

# Stubs

Four files change and **no package is created** (F7a). The descriptor joins the pure image
functions it is made of; the fitted, persisted things get one new flat module; the manifest
widens rather than forking. The rationale for every decision below is in Findings — these
are contracts, not arguments.

## `microclaw/detectors.py` — the seam, the probe, and the exporter (new, flat)

```python
"""Learn a user's ROI concept from a few example images, and score tiles with it.

A DESCRIPTOR turns an image into a vector; a PROBE turns a handful of labelled
vectors into a score. Backends only ever supply the descriptor — the probe, the
hook, the manifest, the exporter and the safety gates never change (F2).

NOT IN HERE (F7a): the classical descriptor -> image_analysis.py, because it is
pure functions on pixels and is BUILT FROM detect_features/compute_stats. The
store -> hook_manager.py, because a detector and a hook are untrusted for the same
reason and must not have two manifests. The hook -> hooks.py.

THE BAR: nothing in this module may make a network call at score time.
"""


class Descriptor(Protocol):
    name: str
    augment_helps: bool                       # F2b

    def describe(self, image: np.ndarray) -> np.ndarray:
        """(H, W[, C]) -> 1-D float vector. Deterministic; no hardware; NO NETWORK."""


class FewShotProbe:
    """L2-regularized logistic regression on a standardized descriptor. One direction.

    Not nearest-mean: a mean-difference direction has no margin and collapses on
    an unlucky example (F2a — AUC 0.926 +/- 0.102, worst slide 2 real in its top 19).
    Not SVM: they tie on accuracy, and the tiebreak is that score() returns a
    PROBABILITY, whose range is fixed by the model class, so a refit does not
    rescale the survey (F3, spike B_2).

    score() is fit-independent but NOT calibrated: p=0.5 means nothing about this
    slide. There is no shippable default threshold and there never will be — choose
    an operating point as a BUDGET over the RANKING (F3).

    The L2 term is load-bearing, not hygiene: on separable data unregularized
    logistic weights diverge.
    """

    def fit(self, X_pos: np.ndarray, X_neg: np.ndarray) -> "FewShotProbe": ...
    def score(self, x: np.ndarray) -> float: ...
    def to_json(self) -> dict: ...            # mu, sd, w, b. The compiled artifact.


def augment(image: np.ndarray, descriptor: Descriptor) -> list[np.ndarray]:
    """Dihedral group: 4 rotations x 2 flips. Microscopy has no canonical up.

    GATED ON THE DESCRIPTOR (F2b): a no-op for the rotation-invariant classical
    descriptor — 8 images, 1 distinct vector, and it fakes the class balance a
    weighted head then trusts. Load-bearing for a CNN/ViT embedding.
    """
    if not descriptor.augment_helps:
        return [image]
    return [f(np.rot90(image, k)) for k in range(4) for f in (lambda a: a, np.fliplr)]


DESCRIPTOR_REGISTRY: dict[str, str] = {
    # name -> import path, resolved lazily so an absent torch is a clean tool error
    # naming the missing extra, not an ImportError at microclaw start-up.
    #
    # The default lives in image_analysis because it IS image analysis (F7a). The
    # rest are flat modules that do not exist yet; the day a SECOND one ships is the
    # day microclaw/detectors/ earns being a package, and not before.
    "classical":  "microclaw.image_analysis:ClassicalDescriptor",
    "embed":      "microclaw.detectors_embed:EmbeddingDescriptor",
    "cellpose":   "microclaw.detectors_cellpose:CellposeObjectDescriptor",
    "bioimageio": "microclaw.detectors_bioimageio:BioImageIODescriptor",
    "ilastik":    "microclaw.detectors_ilastik:IlastikDescriptor",   # F5; score-mode only
}


def load_descriptor(name: str, **params):
    """Resolve a backend, or return a tool-shaped error naming the missing extra."""


def export_detector_scorer(name: str, out_path: str) -> dict:
    """Emit a trained detector as a STANDALONE pycro-manager scoring hook.

    The emitted module:
      * imports numpy/scipy/skimage and NOTHING ELSE — no microclaw, no anthropic;
      * inlines the descriptor and the fitted probe (mu, sd, w, b) as literals;
      * exposes image_process_fn(image, metadata, event_queue);
      * carries, in a header comment, the user's DESCRIPTION, the example thumbnails'
        hashes, the adjudicated verdicts and who gave them (F6/F6a), and the microclaw
        version — a score whose origin got lost is design/20;
      * emits a score and object evidence for every input; it does NOT select top-k while
        streaming, because the final ranking does not exist until the survey is complete.

    NOT EVERY BACKEND EXPORTS THIS WAY, and the tool grades what it emits. The classical
    probe inlines to numpy and a dozen floats. An ilastik-backed detector must shell out,
    which hook_manager's lint bans (subprocess), correctly — so it exports as a hook PLUS
    an ilastik install PLUS a hash-pinned .ilp: still offline, still reproducible, but a
    strictly weaker artifact (F5, F7a).

    Round-trips through hook_manager: the emitted source is linted, hashed and pinned
    exactly like a Claude-generated hook, because that is exactly what it is.
    """


def export_ranked_positions(name: str, survey: str, budget_k: int, out_path: str) -> dict:
    """Persist the post-survey decision artifact.

    Hash the scorer and survey inputs; record every object score and source coordinate;
    apply top-k only now; convert selected centroids through the recorded calibration;
    guard every resulting position; and persist the chosen budget, adjudications, rejected
    positions and final acquisition list. Replaying the same inputs must reproduce the
    same selected positions without running an adjudicator.
    """
```

## `microclaw/image_analysis.py` — the default descriptor

```python
class ClassicalDescriptor:
    """numpy/scipy/skimage only — the descriptor microclaw can always run.

    ~60-80 ms/tile measured, independent of camera size (the frame is decimated to
    256 px first: we are choosing a STAGE POSITION, not a pixel). Comfortably inside
    a 100-500 ms tile dwell.

    LIVES HERE, AND IS BUILT FROM THE FUNCTIONS ALREADY IN THIS MODULE (F7a):
    detect_features for the blob terms, compute_stats for the photometry,
    laplacian_variance for the focus term — plus a fine/coarse band-energy ratio
    (condensed vs diffuse) and a nearest-neighbour spacing (clustered vs scattered).
    Written fresh in a new package it would fork the definition of "a blob" away from
    find_features, and the two would drift.

    Deliberately generic: nothing here knows what the user is looking for. The
    EXAMPLES pick the direction in this space; the descriptor only has to span it.
    Whether it DOES span the user's concept is the one thing synthetic data cannot
    tell us — measure it on a real sample (F2's caveat), and when it does not, F5 is
    the door out.
    """
    name = "classical"
    augment_helps = False       # every feature here is rotation/flip invariant (F2b)

    def describe(self, image: np.ndarray) -> np.ndarray: ...
```

## `microclaw/hook_manager.py` — the store is this file (F4, F7a)

The manifest gains a **kind** — `"hook" | "detector"` — and detectors pin *every* file
they own, not just one `.py`. Everything else (sha256 pin, consent record, refusal on
mismatch, recorded lint warnings) is the code that is already here.

```python
def save_detector(name, description, backend, probe, examples, verdicts,
                  weights_paths=(), ilp_path=None):
    """Pin sha256 of every file: probe JSON, example thumbnails, verdicts (F6a),
    and any weights or user-supplied .ilp (F5). Same manifest, same consent step."""


def load_detector(name):
    """Verify every pinned hash BEFORE unpickling anything. Raise, do not warn."""


def safe_load_weights(path: str):
    """torch.load(..., weights_only=True) / safetensors only.

    NEVER weights_only=False, not even behind a flag: microclaw's whole hook story is
    that untrusted code needs consent, and a flag is not consent (F4).
    """
```

Two lint changes belong here too, both one-liners against `_BANNED_MODULES`
(`hook_manager.py:14`):

* **widen it.** It flags `socket` and not `requests`, `urllib`, `httpx` or `anthropic` —
  the four ways anyone would actually make the network call the whole design forbids.
* **leave `subprocess` banned, and let the ilastik backend collide with it.** That
  collision is F5's finding, not a problem to route around: an ilastik-backed detector
  genuinely does not compile to a self-contained hook, and the linter saying so out loud
  is the system working.

## `microclaw/hooks.py` — the detector hook

```python
class ROIDetectorHook(HookBase):
    """Score each survey tile with a trained detector; optionally acquire on a hit.

    NO NETWORK AT IMAGE TIME. This hook is numpy and nothing else — the invariant the
    whole design rests on, and what lets the fitted detector export as a standalone hook.

    mode="score"    (default, safe, COMPILABLE): scores, saves a crop, enqueues nothing.
                    The budget is applied afterwards, over the whole ranking (F3).
    mode="acquire"  (opt-in, confirm-gated, NOT compilable): pushes a follow-up event
                    per hit, in-scan, no revisit. It cannot use a budget — a rank does
                    not exist at tile 7 of 400 — so it falls back on an absolute
                    threshold, which F3 says nobody can supply. Hence the gate.

    In acquire mode the follow-up event does NOT go on pycro-manager's event_queue — a
    hook's put() there is silently dropped, and put(None) does not stop anything
    (design/24, design/27). It goes on `candidates`, the queue the shipped runner
    (tools._acquire_survey_with_detector) hands over as an attribute and drains.

    Fails CLOSED: if the descriptor raises, log the tile and enqueue nothing. Note what
    that does and does not buy: the tile has already been exposed by the time we score
    it, so "skip" here means "add no work", never "unexpose" — a hook cannot cancel a
    frame that is already in flight (design/27). What fails closed is the DERIVED event.
    (MMPluginHook fails open on purpose, for the opposite reason: there, losing data to
    a bug is the worse failure. Here, a stack trace that fires an unguarded z-stack
    costs sample.)
    """

    def __init__(self, ctrl, guard, detector_name, threshold=None, mode="score",
                 max_rois=10, followup=None, crop_dir=None, log_path=None):
        super().__init__(log_path)          # candidates/progress start None here
        if mode == "acquire" and threshold is None:
            raise ValueError(
                "mode='acquire' cannot use a top-k budget — a rank does not exist "
                "mid-scan — so it needs an explicit threshold, and there is no "
                "default (design/26 F3/F7). Score this sample first."
            )
        ...

    def image_process_fn(self, image, metadata, event_queue):
        """Five things are load-bearing here, and each has a test:

          1. A follow-up frame (a position label not in the survey set) is returned
             UNSCORED — otherwise a detection re-detects itself, forever.
          2. A descriptor that raises logs the tile and enqueues NOTHING (fail closed).
          3. A derived event is checked against guard.check_xy / check_z BEFORE it is
             enqueued. It bypasses _run_protocol_at, so nothing else guards it.
          4. candidates.put(event) happens BEFORE progress.image_done(). Reverse them
             and a hit on the LAST tile can be lost (design/24).
          5. mode="acquire" with self.candidates None RAISES — it was constructed by a
             batched runner that will drop everything it enqueues. The documented
             wrong-runner check (design/27); a quiet success here is the whole bug.

        Every score is written to the log and every crop to disk, hit or miss — a
        decision whose evidence was not kept is design/20.
        """
```

## `microclaw/tools.py` — new tools

The runner `mode="acquire"` needs is not specified here and no longer needs building:
`SurveyProgress`, `_survey_event_stream` and `_acquire_survey_with_detector` are in
`tools.py` (design/24), and `run_adaptive_survey` is the shipped tool in front of the
runner's *adaptive* mode (design/27). `run_roi_survey` is the caller the *derived-event*
mode still lacks.

```python
def prepare_roi_training_set(ctrl, guard, description, example_image_paths,
                             survey_dir, example_boxes=None) -> dict:
    """Build reviewable positive and candidate-negative object crops.

    This runs after a blind survey. Survey samples are unlabelled and therefore only
    candidate negatives; milestone 1 requires their verdicts before binary fitting.
    Persist crop bounds, source-tile hashes and proposal identities so training labels are
    replayable and object attribution can be audited.
    """


def train_roi_detector(ctrl, guard, name, description, example_image_paths,
                       negative_image_paths, example_boxes=None,
                       backend="classical") -> dict:
    """Fit a detector from a handful of examples. ~0.55 s, measured.

    `description` is STORED, NOT FEATURISED — it is the spec the adjudicator judges
    against later. For milestone 1 negatives are required and must carry review verdicts;
    unlabelled survey tiles are not silently promoted to negative labels. Returns the fit,
    the backend used, and a refusal to name a default threshold (F3).

    `example_boxes` — optional per-image (x, y, w, h) — describes the CROP rather than the
    whole tile, a strictly better fit from the same five examples, and makes everything
    outside a box in a positive example a negative by construction.

    DRAW THEM IN KAIBU, NOT IN MICRO-MANAGER. The first draft sourced these from MM's
    rectangle tool and had to append a warning: MM's rectangle normally sets the CAMERA ROI
    (a sensor crop), and whether the bridge can hand back the coordinates WITHOUT applying
    them was never verified. That question does not need answering, it needs AVOIDING —
    ImJoy's Kaibu viewer (add_shapes, napari-shaped) draws boxes on a browser canvas and
    cannot crop a sensor, because it has never heard of one. Same window as the F6 review
    surface. The MM path stays unbuilt unless somebody wants it and checks it on the rig.
    """


def run_roi_survey(ctrl, guard, rows, cols, step_um, save_dir, detector=None, ...) -> dict:
    """Blind tile survey for milestone 1; save pixels, metadata and stage coordinates.

    With detector=None this is the first step and makes no classification claim. A fitted
    detector may score during later surveys, but top-k is always applied after all scores
    exist. Online acquisition is deferred.
    """


def score_roi_survey(ctrl, guard, detector, survey_dir) -> dict:
    """Score stored object proposals and return a complete, deterministic ranking.

    `detect_features` proposes objects; the scorer consumes their crops. Each result keeps
    proposal bounds, centroid, tile identity and score. Whole-tile scoring may prefilter
    but cannot choose a within-tile centroid.

    The acquisition budget is applied only after this function has produced the complete
    ranking. It is never implemented inside streaming image_process_fn.

    DEFERRED: mode='acquire' would image each hit in-scan, on the shipped survey runner
    (_acquire_survey_with_detector, derived-event mode: the survey pre-dispatched, the
    stream held open to drain follow-ups). It cannot use a budget — a rank does not exist
    at tile 7 of 400 — so it requires an explicit `threshold` AND a blocking confirmation
    naming that threshold, the candidate count a prior scoring pass on THIS sample
    produced, and the exposure budget it may spend.

    A future online tool would be the runner's missing ignition:
    _acquire_survey_with_detector's derived-event mode has no public caller today, so
    until this ships, mode='acquire' is a capability documented by a private function
    name — the exact gap design/27's rig run walked into.
    """


def review_roi_candidates(ctrl, guard, detector, top_k=12, band=0.15,
                          include_map=False, surface="blocks") -> list:
    """Return candidate crops as image blocks for the ADJUDICATOR — the top-k AND the
    borderline band around it, where the information is. This is where the DESCRIPTION
    finally does work.

    Crops out, labels in, and it does NOT call an API: the judge can be Claude, a local
    VLM, or the human who wrote the description (F6).

    include_map=True also renders a 2-D UMAP of the survey — a LENS for choosing k by
    looking, never a link in the scoring chain (spike D).

    surface="blocks" (default) returns image blocks, for an agent adjudicator.
    surface="imjoy" ADDITIONALLY opens an ImJoy/Kaibu review window — the crop grid, the
    UMAP beside it with the five examples marked, keep/discard per crop, and a k slider.
    Same contract, different rendering: the verdicts come back through refine_roi_detector
    either way. It is for the HUMAN in F6's fallback, who otherwise adjudicates by
    scrolling base64 past a chat transcript — and F6's whole point is that a careless
    verdict is a bad training label.

    THE SURFACE IS REVIEW-TIME ONLY. The stage is parked and the camera is idle here. No
    part of it may be imported by ROIDetectorHook, image_analysis, or an exported hook —
    that is the invariant, and the AST test enforces it. Ships in the `review` extra.
    And microclaw SELF-HOSTS the plugin it opens: ImJoy's own tutorial does
    api.createWindow(src="https://gist.github.com/...") and we will not be fetching
    executable UI from a gist at runtime (F4, one layer up).
    """


def refine_roi_detector(ctrl, guard, detector, verdicts, adjudicator) -> dict:
    """Re-fit on the verdicts, and PROPOSE A BUDGET, not a threshold (F3).

    Reports, per candidate k: how many of the top-k were judged real, how many false, what
    recall that implies. The agent proposes; the user disposes — and "how many acquisitions
    can you afford?" is a question they can actually answer.

    `adjudicator` (model id + version, or "human") is RECORDED PER VERDICT and carried into
    the exported hook's header — the verdicts are training labels, so a weak judge moves the
    fitted direction rather than merely making one bad call (F6).

    THE VERDICTS THEMSELVES ARE PERSISTED, NOT JUST THEIR AUTHOR — verdicts.json, crop sha256
    -> verdict -> adjudicator -> timestamp, pinned in the manifest. A refit REPLAYS a stored
    adjudication rather than re-soliciting one, which is what makes the compile step
    deterministic without freezing any model (F6a).

    Refuses to return a threshold: a refit invalidates one, and rankings are what survive
    (spike B_2).
    """


def use_ilastik_project(ctrl, guard, detector, ilp_path) -> dict:
    """Attach a user-trained ilastik project as the detector's DESCRIPTOR (F5).

    The escape hatch: the user draws pixel annotations in the ilastik GUI — the one thing
    ilastik headless cannot do for them — and hands the .ilp back. FewShotProbe sits on the
    pooled probability map exactly as it sits on the classical descriptor; nothing downstream
    changes. The agent should REACH FOR THIS when an adjudicated survey's ranking is not
    worth acting on, not wait to be asked.

    Two structural constraints: mode="score" ONLY (a multi-second subprocess cannot run
    inside a 100-500 ms dwell; it runs once, batched, between passes), and the .ilp is
    untrusted bytes — hash-pinned, same confirmation as a generated hook (F4).
    """
```

## `pyproject.toml`

```toml
[project.optional-dependencies]
# The classical backend needs nothing beyond the core deps and is the default.
# These extras buy a stronger DESCRIPTOR; the probe and the tools are unchanged.
vision = ["torch>=2.6", "timm>=1.0"]          # >=2.6: weights_only defaults True
cellpose = ["cellpose>=4.0", "torch>=2.6"]
bioimageio = ["bioimageio.core>=0.9"]
review = [                                    # the REVIEW STEP only — never the scorer
    "umap-learn>=0.5",                        #   the map (spike D)
    "imjoy>=0.11",                            #   the window it goes in; MIT
    "imjoy-jupyter-extension>=0.3",           #   as in pycro-manager's own imjoy tutorial
]
```

Note the floor: **torch >= 2.6**, so `torch.load`'s default is `weights_only=True` even if
some path forgets to pass it (F4) — which is not a theoretical nicety, since pycro-manager's
own reference notebook writes a bare `torch.load(args.weights)` and is safe only because
that default changed underneath it (F4a).

Everything in `review` is scoped to the step where the stage is parked: the map and the
window the human adjudicates in. **Nothing in `review` may be imported at score time** —
not by `ROIDetectorHook`, not by `image_analysis`, not by an exported hook. That is the
invariant, and it is a test, not a convention. And the default install has **no ML
dependency at all** — the shipped detector is a dozen floats.

**ilastik gets no extra, and that is not an oversight.** It is a conda monolith we cannot
pip-depend on, and we do not need to: the F5 path shells out to an ilastik the *lab* already
installed, and the artifact we consume is the `.ilp` the user drew. A backend whose
dependency is "the thing you already have" is the cheapest row in the table.

**Not shipped: ultralytics** — not in the extras, not in an optional backend, not behind a
flag. It is AGPL-3.0 including its weights, microclaw is BSD-3, and an extra is still a
dependency.

---

# Tests

Everything in F2–F7a is testable without hardware. (The event-queue tests — the ones where
a regression is silent — ship already, in `tests/test_survey_runner.py`, with MM-gated
siblings in `tests/test_integration.py`.)

**The bar (F7):**

* **No hook in `microclaw.hooks` imports a network module** — `socket`, `requests`,
  `urllib`, `httpx`, `anthropic`. An AST scan over the module, asserted. This is the
  invariant the whole design rests on, it is currently true by accident, and this test is
  what makes it true on purpose.
* **Nothing on the score path imports the `review` extra** — `imjoy`, `umap`. Same AST
  scan, extended to `image_analysis` and to `export_detector_scorer`'s output. The review
  surface is a browser UI for a parked stage; if it ever appears in a hook, the invariant
  has been broken by a convenience.
* `hook_manager.lint_hook_code` flags all five of those, not just `socket`.
* `export_detector_scorer` emits a module that imports neither `microclaw` nor `anthropic`,
  and whose `image_process_fn` scores a tile correctly **with microclaw not on `sys.path`.**
  Assert on the import, not just the output.
* `export_ranked_positions` applies top-k only after every score exists. Replaying the
  scorer, survey, calibration and verdict artifacts reproduces the ranking and selected
  guarded positions exactly.

**The probe (F2, F3):**

* `FewShotProbe.fit` on 5 positives + mined negatives ranks a held-out synthetic survey
  with AUC > 0.9 **on every one of five seeds** — not on one. The single-seed version of
  this test is what let F2's first draft ship a lucky number (spike B_1).
* `augment()` is a **no-op for a descriptor with `augment_helps = False`**, and returns 8
  images for one with `augment_helps = True` (spike B_3).
* Record how much a refit changes the top-k set; do not assert universal preservation from
  the synthetic spike. Separately assert that rescaling scores leaves ranks unchanged and
  that an absolute threshold's candidate count is not a stable contract (spike B_2).
* `refine_roi_detector` returns a **budget** proposal and **refuses to return a threshold**;
  a caller that passes a stale threshold to `run_roi_survey` after a refit is refused.
* `run_roi_survey(mode="acquire")` without an explicit `threshold` raises, naming F3.
  This is a deferred-online regression test, not a milestone-1 API requirement.
* `refine_roi_detector` **records the adjudicator** against every verdict and refuses a
  verdict set with no adjudicator named — an unattributed label is the design/20 failure
  with a new coat on (F6). The exported hook carries it.
* **A refit that REPLAYS a persisted `verdicts.json` reproduces the fitted probe exactly**,
  with no adjudicator called at all (F6a). This is the test that makes the compile step
  deterministic, and it is the one that lets the doc decline to freeze a text encoder — so
  if it regresses, the YOLOE argument comes back.

**The detector hook (F3, F7):**

* A descriptor that raises → the tile is logged and skipped and **nothing is enqueued**
  (fail closed), while `MMPluginHook`'s fail-open behaviour is unchanged.
* `max_rois` caps enqueued events; the (n+1)th hit is logged as skipped, not lost.
* An out-of-bounds derived position is refused by the guard and **not** enqueued; the
  acquisition continues.

**The store, which is `hook_manager` (F4, F7a):**

* `load_detector` refuses a probe, a weights file, or a `.ilp` whose sha256 no longer
  matches the manifest. **Same manifest, same code path as hooks** — assert that a detector
  and a hook are refused by the *same* function, because a second copy of this mechanism is
  a second thing to forget to fix.
* `lint_hook_code` still bans `subprocess` — and an ilastik-backed `export_detector_scorer` is
  therefore **flagged, and grades itself as non-self-contained** rather than silently
  emitting a hook that trips the linter (F5).
* `safe_load_weights` refuses `weights_only=False` — assert on the call, not the outcome.
* `train_roi_detector(backend="embed")` without torch installed returns a tool error naming
  the extra, not an ImportError traceback.

**The layout (F7a):**

* `ClassicalDescriptor` is importable from `microclaw.image_analysis`, and its blob terms
  come from `detect_features` — assert they agree, so the descriptor and the `find_features`
  tool cannot drift into two definitions of "a blob".
* `microclaw` has no subpackages. A trivial test, and the cheapest way to keep the
  four-module `detectors/` package from growing back before a second backend justifies it.
* `train_roi_detector(example_boxes=...)` describes the **crop**, not the tile: the same
  example with and without a box produces different feature vectors, and omitting boxes is
  unchanged behaviour. Boxes are plain `(x, y, w, h)` in image pixels — the test does not
  care that Kaibu drew them, which is the point of taking them as coordinates rather than
  reaching into a GUI.
* Training without reviewed negatives is refused. Candidate negatives sampled from a
  blind survey remain unlabelled until an adjudicator verdict is persisted.
* On a tile containing multiple proposals, the exported ranked position uses the centroid
  of the scored proposal, not an unrelated whole-tile `find_features` centroid. If no
  proposal supports attribution, the result is explicitly whole-field or unresolved.
* `review_roi_candidates(surface="imjoy")` **returns the same verdict contract** as
  `surface="blocks"` — crops out, labels in — and `refine_roi_detector` cannot tell which
  one produced the verdicts, beyond the `adjudicator` string it is told. A rendering must
  not be a fork in the seam.

# What this design does not do

* **It does not measure a real sample.** Every performance claim above is either measured on
  synthetic data (F2, F3) or on this laptop (fit and per-tile cost), and the hard part — does
  a generic descriptor span the concept in the user's head? — is exactly the part synthetic
  data cannot answer. The synthetic classes differ *by construction*, which is why the margin
  heads score a perfect AUC; do not read that as a promise. The first real run should
  `run_roi_survey` in `mode="score"` on a slide the user can label, and compare the ranking
  to their judgement *before* anything is allowed to drive the stage.
* **It does not know that a DETECTOR can drive the online mode — only that something can.**
  The engine question this doc used to carry is answered: AcqEngJ accepts a new position
  label mid-acquisition (design/24, rig, `roi_0` in the dataset), the runner ships, and
  design/27's rig run had three agent-written hooks steer a live scan through it. What has
  never run is `ROIDetectorHook` — a probe scoring a real tile inside a real dwell, firing a
  real follow-up — and the derived-event path it needs has no tool in front of it yet. The
  risk is no longer "does the engine allow this"; it is the routine one, that the runner's
  first detector is still a first detector. The fallback is unchanged and is the default
  anyway: the two-pass flow, which F7 argues is the better mode regardless.
* **It does not measure YOLOE, or DINOv2, against the classical floor** — but F9 does the half
  that needs no torch, and it changes what remains. The argument that a natural-image feature
  space cannot span a microscopy concept is still an *argument*, but F9 shows the ladder has a
  **free middle rung** (structure-tensor texture recovers oriented concepts the floor is blind
  to, 0.57 → 1.00) and gives the learned rung a **concrete floor to beat** (~0.78 on the
  configural concept, not chance) and a benchmark to beat it on. So scoring DINOv2/YOLOE as a
  `Descriptor` under `FewShotProbe` still costs an afternoon and no licence — but it is now a
  measurement against a number, not a vague someday, and it must beat the *enriched* classical,
  not the bare floor. That afternoon is the only thing that could ever justify reopening the
  AGPL question, which is otherwise a relicense of the whole project for one table row.
* **It does not build the ImJoy review window, and nobody has watched a human use one.**
  That ImJoy bridges a Python kernel to a browser UI is verified — it is pycro-manager's own
  tutorial, read at source. That a crop grid plus a UMAP plus a *k* slider is the right thing
  to put in that window is a **design, not a measurement**: it is the artifact F3 says the
  adjudicator needs in order to choose an operating point *by looking*, and whether looking at
  it actually produces better verdicts than scrolling crops past a transcript is exactly the
  kind of claim this doc keeps refusing to let pose as a finding. The first human adjudication
  settles it — `design/26-imjoy-review-spike.ipynb` is that first adjudication, and it is a
  **notebook** because of the question this doc never asks: `api` is injected by a plugin
  runtime, and microclaw is a CLI. If the window only ever opens in Jupyter, then
  `surface="imjoy"` is not a parameter default but a **constraint on the F6 fallback** — the
  human adjudicator needs a notebook next to the CLI — and that is a design correction worth
  as much as a working window. The same spike settles `example_boxes`, which this doc asserts
  is "plain `(x, y, w, h)` in image pixels" **having never seen one come back**: normalised
  floats, polygon vertices, display coordinates or a flipped y each rewrite that contract. It
  would be a poor joke to dodge MM's rectangle tool over an unverified coordinate question and
  land on a second one. Note what is *not* at risk: the seam is crops-out-labels-in either way,
  so a disappointing window costs a window.
* **It no longer depends on MM's rectangle tool, and that is a dodge rather than an answer.**
  The first draft's open question — can MM hand back rectangle coordinates without applying
  them as a camera ROI? — is now avoided by drawing `example_boxes` in Kaibu instead. The
  question is still unanswered. If a lab wants to annotate in Micro-Manager because that is
  where their eyes already are, somebody has to go and check it on the rig.
* **It does not fine-tune anything.** If the ladder tops out — classical, then frozen
  embeddings, then Cellpose objects — and the user's concept still is not separable, the
  answer is more labels, not more layers: the user draws them, in ilastik (F5) or micro-sam,
  and hands the artifact back. That is an offline workflow and it should stay offline. It is
  also the point at which the bar stops being meetable *by us*, and that is a reason to hand
  the user a pen rather than to put a vision call in the scan loop (F7).
* **It does not know that the pooled ilastik probability map beats anything** — though it now
  knows the pooling WORKS. A human drew two labels, headless scored 45 held-out tiles, and the
  pooled vector ranked them at AUC 1.000 through `FewShotProbe` (spike F_3/F_5, 2026-07-17).
  That retires "is this seam fictional?" and nothing else: the classical floor scores 1.000 on
  the same tiles, so the comparison is between two perfect scores on a concept built to be
  easy. **The backend question is untouched** and needs F9's oriented/configural concepts or a
  real sample — which is exactly where F9 says to point it, and it is the last thing in F5
  standing on an argument.
* **It does not let the detector touch illumination.** Deliberate, and worth restating because
  it will be asked for: a detector that could raise laser power to "see the feature better"
  would be the first thing in microclaw that can bleach a sample without a human in the loop.
