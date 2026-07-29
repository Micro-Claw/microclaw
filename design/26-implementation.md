# design/26 — normative milestone 1 implementation brief

This file is the implementation authority for design/26. The long research record in
`26-ml-roi-detection.md` explains how the decision was reached; its old API sketches,
backend survey, and deferred online mode are not an implementation checklist.

## Decision

Microclaw integrates an unfamiliar analysis as a reviewed, hash-pinned adapter. It does
not add one public tool family per analysis package. **Since Block 7 a saved adapter
must NOT inherit `HookBase`:** the trusted parent owns the audit record, and a saved
hook that keeps its own log is refused at resolve time. `HookBase` remains the base
class for the reviewed built-ins in `PRECODED_HOOK_REGISTRY` only. The first run is observation-only:
it returns every image unchanged, records normalized analyzer output and provenance,
and cannot filter images, stop a scan, submit events, or move hardware.

Validation proceeds in three deliberately separate runs:

1. **Run A, pre-built SNR canary:** validate acquisition, versioned logging, offline
   whole-tile ranking, guarded positions, and revisit accuracy with `snr_observer`.
2. **Run B, generated adapter:** validate that Microclaw can derive and fixture-test an
   unfamiliar existing analysis from the three-question intake.
3. **Run C, optional few-shot classifier:** validate learning the biological concept
   from reviewed examples only when the use case needs it.

Run A is a controlled plumbing test, not evidence for custom integration or biological
recognition. SNR is a whole-tile measurement and cannot identify which object caused a
score. Run B validates the central custom-hook promise. Run C should use the measured
classical descriptor with a logistic probe; LDA is not preferred without real held-out
evidence that it improves precision at the chosen budget.

## Run B has one workflow, not one invocation mechanism

Every custom integration follows the same lifecycle:

1. locate the workflow and one known input/result;
2. discover its installed execution boundary from files, environments, help, source,
   and primary documentation;
3. reproduce the known result safely on a copied input;
4. measure startup and marginal/batch latency;
5. write and fixture-test an observation-only adapter;
6. normalize verified output into `microclaw.analysis-observation/v1`;
7. record the executable environment and artifact provenance;
8. select per-image, persistent-worker, or completed-survey batch execution from the
   measurements and requested action.

The adapter selects one of several local execution boundaries:

| existing workflow | preferred boundary |
|---|---|
| package in Microclaw's environment | direct Python import |
| package in another Python environment | that environment's interpreter, preferably one persistent worker or one batch |
| installed executable with a documented CLI | local subprocess, normally one completed-survey batch when startup is heavy |
| GUI-only application | explicit export/import or completed-dataset workflow; no acquisition-time GUI automation |
| remote service | never at image time; obtain and pin a local artifact or use it only while the stage is parked between passes |

Do not force every analyzer into Microclaw's environment. Python, CUDA, Java, native
library, and model dependencies may conflict. Conversely, do not pay `conda run`,
environment activation, JVM/application startup, or model loading once per tile without
a measurement showing that it meets the dwell and failure budget. Prefer one persistent
local worker for measured online use or one invocation over the saved survey.

Installation and adaptation are separate decisions. If the package, executable, model,
or environment is missing, Microclaw presents a pinned installation plan—including
downloads, licenses, environment location, hardware requirements, and executable/model
identity—and waits for explicit authorization. It must not silently install software or
models while writing a hook.

The ilastik adapter is the established executable/batch case: the user trains the `.ilp`
in the GUI; Microclaw verifies its hash before opening it, pins the ilastik version and
headless command, and runs the saved survey through one local subprocess between passes.
The spike's approximately 7.6 s startup rules out launching it per tile; its measured
marginal cost does not by itself rule out batching. Probability-map pooling, channel/
axis mapping, decimation, and export semantics remain ilastik-specific contract facts.
The spike validates this seam, not an accuracy gain over the classical baseline.

The first scientific workflow remains two-pass:

1. acquire and save a fixed blind survey with an observation-only hook;
2. review and label crops from the stored survey;
3. score all stored objects, then choose an explicit top-*k* budget;
4. convert only attributed object coordinates through the measured camera-to-stage
   transform and the normal XY/Z guards;
5. ask for acquisition confirmation and revisit the selected positions with the
   existing multiposition tool.

A streaming callback must not claim to select top-*k*: it has not seen future tiles.
An unattributed tile score must not be assigned to a convenient blob centroid. When
object proposals do not cover the concept, revisit the whole field or report the
result as unresolved.

## Completed-dataset replay orchestration (offline path shared with design/29)

Both the two-pass survey review above and design/29's offline mosaic/analysis run an
analysis over a *saved* dataset rather than a live acquisition. That offline path
needs generic completed-dataset orchestration, and it is owned here, not in design/29:
design/29 supplies the saved-NDTiff traversal (`_iter_present_coords`) and the
stage-coordinate-mosaic input primitive; design/26 owns the runner's lifecycle,
adapter contract, and record schema. This resolves the forward reference in
design/29 §3, which previously pointed at a component neither doc described.

This is a Microclaw policy and reproducibility layer over the existing `Dataset`
interface, not a new acquisition callback or a replacement dataset model. `Dataset`
is implemented by `ndstorage`: pycro-manager returns it from `acq.get_dataset()`.
Import it directly with `from ndstorage import Dataset`; do not rely on pycro-manager
re-exporting it, because that varies across supported versions. After the
`Acquisition` context has exited (or `await_completion()` has returned), code can use
`acq.get_dataset()`; an older saved acquisition can be opened with
`Dataset(dataset_path)`. That completion boundary already guarantees that pending writes
have finished. Traversal should delegate to the native dataset operations (`read_image`,
`read_metadata`, coordinate enumeration, and lazy `as_array()` where appropriate).

Microclaw adds the reviewed, fixture-tested, hash-pinned **adapter lifecycle**, bounded
selection, provenance, normalized observation schema, artifacts, and cancellation. It
does not invent a "completed-run hook" and does not add one public tool per analyzer.
There are two explicitly offline invocation shapes:

```python
analyze_saved_frame(image, metadata, context) -> normalized_result | None
analyze_completed_dataset(dataset_view, selection, context) \
    -> Iterable[normalized_result]
```

> **SETTLED 2026-07-29, before Block 10 branches.** This section originally named
> the per-frame offline contract `analyze_frame`. Block 7 then shipped
> `analyze_frame(self, image, metadata) -> HookResult | None` as the **live**
> saved-hook contract — same verb, different arity, different return type — and
> `hook_docs`, the tool schema, `validate_hook_contract`, and two refusal messages
> now teach it, with four migrated M5 hooks using it. `load_hook_class` returns the
> first class exposing either name, so one verb meaning two things would have
> reached the saved-adapter loader, which the original note explicitly forbade.
>
> **Resolution: the offline per-frame contract is renamed `analyze_saved_frame`.**
> The live name is shipped and in field use; the offline one is not built at all
> (nothing in `microclaw/` or `tests/` referenced it when this was settled), so it
> is the side that moves. Nothing in the shipped code changes — this settlement is
> documentation only.
>
> **The rejected alternative was "one method with `context` optional."** It reads
> as the convenient choice and is the wrong one, because the two contracts differ
> in more than arity. The live return may carry typed *actions* — `MoveStage`,
> `AcquireAt`, `SetIlluminationPower`, `StopSurvey` — that have no referent
> offline, where there is no hardware, no survey, and no acquisition to gate them.
> A merged method would have to either silently drop those actions (a hook that
> asked to move a stage and was ignored, with no way to tell) or raise at call
> time, deep inside a run, instead of at resolve time. Distinct verbs let the
> loader discriminate the two contracts *before* anything runs, which is the
> property the original note was protecting.
>
> Keeping the arities different (`(image, metadata)` live, `(image, metadata,
> context)` offline) is deliberate and load-bearing, not incidental: a class
> written for one contract cannot be silently invoked under the other even if
> someone later aliases the names.

The first is a deliberately offline-safe per-frame contract over selected stored frames;
the second is the boundary for multi-tile state, one completed mosaic, or one batch
executable such as ilastik. Neither is a pycro-manager hook signature.

Do **not** replay an acquisition-time `image_process_fn`, with a fake event queue or
otherwise. That function may transform or discard pixels, depend on live metadata,
retain acquisition-thread state, interact with hardware, or rely on callback ordering;
discarding its returned image would also erase part of its contract. An adapter intended
for both live and offline use must put its pure analysis in an explicit shared helper and
expose two thin, separately fixture-tested entry points over it — `analyze_frame` for
live and `analyze_saved_frame` for offline. They cannot be one method: they return
different types (§"SETTLED" above). There is no legacy compatibility path. The
saved-adapter loader accepts `analyze_completed_dataset` or `analyze_saved_frame`; no
single method is required for every offline adapter.

**The offline loader must refuse a live-only class at resolve time**, naming the two
offline verbs, rather than discovering the mismatch when it calls the method. A class
exposing only `analyze_frame` or `image_process_fn` implements the live contract and is
not an offline adapter; a class exposing both live and offline methods is a legitimate
dual-use adapter, and the offline runner calls only the offline one. This mirrors the
refusal Block 7 already ships in the other direction — a legacy `image_process_fn`-only
hook is refused before any position is exposed, with a message naming `analyze_frame` as
the migration (`microclaw/tools.py`). Match that message shape; the symmetry is the point.

Expose this through one generic orchestration tool, provisionally
`run_analysis_on_saved_dataset(dataset_path, adapter, axis_selection, input_kind,
parameters, output_dir)`. `input_kind` is `frames` or `stage_coordinate_mosaic`; for
the latter the runner calls design/29's geometry primitive. Analyzer-specific public
tools remain forbidden.

**Corrected 2026-07-29 by design/29 Block 9's post-merge gate.** "with the selected
calibration artifact" was too narrow in three ways, each of which Block 10 would
otherwise hit at implementation time. The shipped primitive is
`build_stage_coordinate_mosaic(dataset_path, output_path, axis_selection,
calibration_ref=None, output_pixel_size_um=None)`, so:

1. **The calibration input is a tagged object, not a path.** `calibration_ref` is
   `{"kind": "artifact"|"knowledge_version"|"confirmed_current", ...}`, or `None`
   to fall through to the acquisition record. The runner must carry the tag.
2. **`None` is not a safe default.** `resolve_calibration`'s acquisition-recorded
   branch needs five identity fields, and a rig with no objective device supplies
   only four — measured on M2, where none of `Objective`, `ObjectiveLabel`,
   `PixelSizeConfig` or `PixelSizeConfigName` appears in any of 420 metadata keys.
   There, an explicit ref is mandatory rather than preferred. Require one for this
   `input_kind`, or surface `acquisition_fallthrough_reason` instead of a generic
   failure.
3. **Output shape differs.** The primitive takes an `output_path` and writes a
   uint16 TIFF *plus* an adjacent `.json` manifest; this tool offers an
   `output_dir`. Reconcile the two rather than assuming one file.

Also inherited: the mosaic **refuses** a dataset without per-image intended XY
rather than reconstructing a grid, which is the normal shape for single-position
acquisitions. See design/29 "Block 9 post-merge design gate — as built".

The runner normalizes output to `microclaw.analysis-observation/v1`. It must not
filter acquisition, move hardware, or present an authoritative biological result.
Enforce the capability boundary from the first implementation: offline adapters are
supplied no controller, guard, acquisition object, hardware event queue, or API
credentials. Block 7 applied the same boundary to **live** saved hooks, so this is no
longer an offline-only rule; the two paths differ in what they may propose, not in
what they hold. The orchestrator opens the native `ndstorage.Dataset`, but the adapter
receives a `DatasetView`: a read-only, selection-limited protocol exposing only selected
coordinate enumeration, `read_image`, `read_metadata`, and bounded lazy array access.
It is a capability facade over the native object, not a second storage implementation.
The manifest declares which of those operations the adapter needs. `context` exposes
only the immutable input identity, a bounded artifact directory, observation emission,
and cancellation. The eventual
worker-process isolation in design/32 is required before claiming containment against
an adapter that imports or opens capabilities for itself; until then, source review,
lint, hash-pinning, and explicit confirmation remain the gate.

Promotion to acquisition-driving use goes through the labelled-example, slide-level
validation, object-attribution, guard, and confirmation workflow already described.

Pycro-manager already supplies `image_saved_fn(axes, dataset)` for per-image post-save
work, a native `AcquisitionFuture.await_image_saved(...)` adaptive pattern (returned by
`acq.acquire()`), and hands back an `ndstorage` `Dataset` (via `acq.get_dataset()`) for
completed data. The `AcquisitionFuture` pattern is real but is **not** microclaw's
adaptive path: design/24 evaluated and rejected it (a hook is never handed the
`Acquisition` object, and detection stays inside the hash-pinned `image_process_fn`
rather than a runner-thread wait loop), so adaptive branching goes through the
`run_adaptive_survey` candidates-queue runner instead. Microclaw's runner wires
`image_process_fn` and `post_hardware_hook_fn`, and — since Block 4 — `image_saved_fn`,
which the acquisition reservation uses to commit frames as they land (measured free,
1.00×, where generator feeding cost 3.14×). It is not a completed-dataset callback and is not a prerequisite for
offline replay. The orchestration requires a shared observation writer used by **both**
`HookBase.log_analysis` (live) and offline replay, so an offline record is never a
fabricated acquisition-time hook context. **Block 7 created it:**
`microclaw.hooks.analysis_observation_record` builds the
`microclaw.analysis-observation/v1` envelope and is already used by
`HookBase.log_analysis` and by the trusted parent that records an untrusted hook's
`HookResult`. Block 10 extends that writer rather than introducing a second one. Each record carries source dataset
identity and content hashes, the exact selected coordinates, analyzer source hash
plus version and environment, parameters, an optional model/project/config hash,
output artifact hashes, status, and timestamps. Record calibration contents and
identity only when the adapter consumes a spatial mosaic or emits spatial coordinates;
otherwise record `calibration_used=false` rather than attaching irrelevant current
microscope state.

`status` has a closed vocabulary: `unverified` means an input/output contract fact
(axes, units, coordinates, or score semantics) remains unresolved; `provisional`
means the contract is verified but scientific acceptance gates have not passed;
`observed` is reserved for a verified measurement that makes no biological-recognition
claim, such as the Run A SNR canary. No one of these statuses authorizes acquisition.
An acquisition-driving artifact is a separate validated workflow output, not a fourth
observation status.

Replay over the same stored inputs and verdicts must reproduce the scientific payload,
ranking, selected coordinates, and content-addressed artifacts without an adjudicator
or network. Execution metadata such as timestamps, run IDs, paths, and measured latency
may differ and is excluded from the deterministic comparison. This is the same
determinism the "Replay is a requirement" clause states for ranking.

## What is implemented before the rig run

- The hook-writing intake and agent-owned contract checklist live in `hook_docs.py`.
- Acquisition-time network clients are advisory lint findings; reviewed source and
  explicit confirmation remain the actual gate.
- Saved hooks are hash-pinned by `hook_manager`.
- `HookBase.log_analysis` writes `microclaw.analysis-observation/v1` records. Each
  record contains position/stage coordinates, status, analyzer and version,
  parameters, an optional model/project/config sha256, and JSON-normalized output.
- The pre-coded `snr_observer` calls the shared `compute_stats`, records those fields
  plus analysis latency, returns every image unchanged, and has no threshold or
  acquisition side effect. It is Run A's positive control.
- A generated adapter is fixture-tested against the user's known input/result before
  it is saved. Unknown axes, units, coordinates, or score semantics are logged with
  `status="unverified"` and cannot drive acquisition.

No generic detector, learned backend, or review GUI is needed to establish this seam.
The concrete adapter cannot be written until a real workflow and known example are
provided: inventing its callable, axes, channels, or outputs would defeat the design.

## Artifacts required from the field spike

Keep these under one run directory:

- the saved survey dataset;
- the observation hook source and its manifest-pinned sha256;
- the hook log containing `microclaw.analysis-observation/v1` records;
- `verdicts.json`, with crop identity, label, adjudicator, timestamp, and source-image
  hash for every judgement;
- `ranked_positions.json`, with every object score, proposal bounds and centroid,
  source tile, detector and input hashes, calibration identity, chosen *k*, guard
  outcome, and selected stage coordinates;
- the revisit dataset and the microclaw history JSON.

For Run A, the saved history may carry the complete deterministic SNR ranking and the
existing position-list artifact may carry the selected coordinates. Do not pretend the
current runtime can write a provenance-rich `ranked_positions.json`: that generic
artifact writer is a Run C implementation gate. Run B/C require the richer artifacts
when their outputs can drive object-level scientific decisions.

Replay is a requirement: stored inputs and verdicts must reproduce the same fitted
state, ranking, and selected positions without consulting an adjudicator or network.

## 2026-07-20 Run A field findings

The Windows bead-sample run established the plumbing seam and falsified several
tooling assumptions. A confirmed dark survey scored SNR 3.00–3.07 while the
illuminated rerun scored 23.76–123.57, so the package `min_snr=3.0` placeholder is
not a valid gate for that rig. Calibrate from multiple confirmed dark and illuminated
logs, record objective/camera/ROI/binning/exposure/channel context, and carry the
threshold source in every observation.

Ranking and replay must use `rank_hook_log`, not conversational arithmetic. Candidate
coordinates must pass the non-moving `validate_positions` tool before they are saved;
the result reports accepted/rejected records and never exposes guard limits or clips.
Acquisitions return UTC start/completion timestamps, wall duration, and planned/acquired
counts. `inspect_artifacts` supplies recursive SHA-256 evidence, and
`compare_revisit_frames` supplies quality-gated pixel registration with micrometre
conversion only when a stage-camera affine exists.

## Acceptance gates

The architecture is ready to implement, but the scientific feature is not shipped
until the field run establishes all of the following:

- useful precision at the operator's acquisition budget on multiple slides/days,
  evaluated with a slide-level split;
- correct attribution on multi-object tiles and non-square images;
- verified crop axes, camera-to-stage conversion, guard rejection, and revisit error;
- measured survey, scoring, review, refit, and revisit times, plus photobleaching that
  is acceptable for the sample;
- sensitivity results for contaminated negatives, duplicate examples, one wrong
  verdict, constant features, common targets, and target-absent slides;
- deterministic replay of verdicts and ranked positions.

The initial field run may use one slide to falsify the workflow cheaply. Multiple
slides/days are required only before calling it validated.

## Explicitly deferred

- detector-triggered online follow-ups;
- ImJoy/Kaibu or another dedicated review UI;
- DINO, YOLOE, Cellpose, ilastik-benefit comparisons, and fine-tuning;
- a generic detector package or package-specific public tools.

Escalate to another descriptor/backend only when the real-sample result demonstrates
that the cheaper rung does not separate the stated concept. UI work follows evidence
that transcript/file-based review is the bottleneck.
