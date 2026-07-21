# design/26 — normative milestone 1 implementation brief

This file is the implementation authority for design/26. The long research record in
`26-ml-roi-detection.md` explains how the decision was reached; its old API sketches,
backend survey, and deferred online mode are not an implementation checklist.

## Decision

Microclaw integrates an unfamiliar analysis as a reviewed `HookBase` adapter. It does
not add one public tool family per analysis package. The first run is observation-only:
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
