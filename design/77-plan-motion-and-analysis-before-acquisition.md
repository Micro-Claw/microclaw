# Plan motion and analysis before acquisition

Status: **PROPOSED**, 2026-09-05. Design only; no implementation or gate has run.

Reviewed against the record 2026-09-05, at `main` `c2f19dd`: the session JSONL,
the tool source, the installed `pycromanager.multi_d_acquisition_events`, and
the live schema/skill text. Every claim below marked *verified* was read out of
one of those, not inferred from the incident narrative.

## Incident and evidence

Source: `kinesin-weirdness-m2/20260905_165524_640862_microclaw_history.jsonl`
in the operator-supplied session directory. Line numbers below refer to that
JSONL, not rendered conversation turns.

The user requested a microtubule survey, identification of fields containing
walking kinesin, and a second movie per field with hollow yellow boxes following
the walking particles. Three connected failures prevented a coherent delivery:

1. At lines 8 and 10 the assistant committed to acquiring first and building
   offline tracking afterward. At line 124, after acquisition, it discovered
   what it believed was an execution limitation and told the user that the
   offline orchestrator was a “design/26 proposal, not yet implemented.”
   Development document identifiers are meaningless in a microscope session.
2. At line 146 it attached a tracking observer to a six-position, 50-frame,
   100 ms timelapse with `interval_s=0`. Positions were visited at each time
   point instead of completing each field's movie before moving. At line 154
   the assistant incorrectly defended this as continuous acquisition per field;
   the user corrected it again at line 155. At line 156 the assistant reported
   roughly 4.7 seconds between a field's frames. That number is the assistant's
   retrospective claim, not an independently measured cadence in this design;
   settling measurements from a different run do not independently establish
   this run's revisit interval.

   The same session also contains a useful comparison of execution paths: **line 116 called the same tool with the same six positions, the same
   `n_frames=50, interval_s=0, exposure_ms=100`, and no hook.** It wrote one
   dataset per field (`kinesin_640\mt_kin_1_r5c10\mt_kin_1_r5c10_1`, read back
   at line 120) — position outer, fifty contiguous frames per field. Line 146
   added the hook and its parameters/log path, changed the output name and
   directory, and interleaved. Laser power had also been raised from 1% to 10%.
   The positions and timing parameters were unchanged; the hook selected the
   different execution path. The second exposure of six fields at ten times
   the laser power therefore did not preserve the intended tracking cadence.
3. Only after the user suggested using a hook did the assistant offer a stateful
   live tracking observer (line 130). The observation capability should have
   been considered while planning the original acquisition, before spending dose.

The assistant also changed its explanation for zero walking tracks from detector
noise to temporal undersampling. Neither explanation should become a biological
conclusion without checking the acquired sequence and tracking evidence — and
neither run isolates either one. The two differ in **both** power and order
(verified: `1.0012` before line 134, `10.0122` after), so the pair is not a
controlled experiment isolating either variable
or its effect on tracking. The calls and execution paths establish the change
in acquisition order; they do not isolate its contribution to the detection
results from power, sample changes or tracker behavior.

## Code findings

**The order.** `run_multiposition_acquisition` in `microclaw/tools.py` describes
a per-position protocol. Its hookless path loops positions and runs one
acquisition at each; adding `hook_strategy` routes through
`_acquire_positions_with_hook`, which builds one acquisition with a position
axis. Verified: neither path names an axis order, and
`_build_acquisition_events` forwards to `multi_d_acquisition_events`, whose
installed default is **`order="tpcz"` — time outer, position inner**. So the
interleaving is not a subtlety of the hook; it is the engine default that the
hookless path never reaches because it never builds a multi-position event list.
`run_tile_acquisition` delegates to the same tool and inherits it.

**The clock.** `multi_d_acquisition_events` stamps
`min_start_time = time_index * time_interval_s`, and AcqEngJ resolves that
against the **acquisition's** start, not the field's. Verified in the installed
source. This matters more than the axis order: under `order="pt"` with a nonzero
interval, field B's frame *k* is due at `k * dt` from a clock that has already
spent field A's whole movie, so **every one of B's deadlines is already past
when B starts** and B bursts. Static per-field offsets can express a planned
schedule, but cannot reliably preserve spacing relative to an unpredictable
actual field start. That requires an execution-aware timing origin — see the
open question below.

**The export.** Multiple emitter sites in `tools.py` generate calls to
`multi_d_acquisition_events` without an explicit `order`. Calls using a combined
position/time event list inherit the engine default; per-position loops establish
position-outer order structurally. A tool that chooses an order or timing strategy
and an emitter that does not preserve it would make the standalone script acquire
a different temporal sequence from the run it claims to reproduce.

**The skill overrode the schema.** `microclaw/skills/hook-authoring/SKILL.md`
carries the “design/26 proposal, not yet implemented” text twice (lines 66–68 and
521–523) and told the model not to claim offline execution “until they ship”.
They shipped: `microclaw/completed_dataset.py` implements `DatasetView`,
`_load_saved_adapter`, `OFFLINE_VERBS` and `run_analysis_on_saved_dataset`, and
the tool's own schema already says so — *“An adapter from the user's saved
manifest also runs here, and those stay reviewed and hash-pinned.”* The model was
shown the truth in the schema and the contradiction in the skill, and **followed
the skill**. That is the finding, not merely that some text went stale: a skill
that maintains a competing capability declaration can override the installed
tool contract in practice.

Verified leak surface, and it is small: `tools_schema.py` has **zero**
`design/NN` references; `agent.py`'s two are in internal developer docstrings the
model never sees; the ten in `SKILL.md` are shipped verbatim by `load_skill`
(called at line 122); and **four of the seven pre-coded hook class docstrings**
— `focus_feedback`, `position_filter`, `snr_observer`, `mm_plugin_analyzer` —
name a design number in text `list_hooks`/`describe_hook` return as
`class_docstring` (`list_hooks` was called at line 88).

Current repository support does not establish which version or dependencies were
available on that rig; 77a must check rather than assume.

## Decisions

### D1 — describe capabilities in user language

Remove development file references and implementation milestones from text the
model is shown. The audit is bounded and enumerated above: `SKILL.md`, and the
four pre-coded hook class docstrings that reach the model through `list_hooks`.
`design/NN` in an ordinary code comment stays — those are the rationale this
repository deliberately keeps, and sweeping them would cost real history for no
user-visible gain. The line is whether the model can be shown the string.

The deeper rule is the one the schema/skill contradiction exposes: **a skill must
not maintain a competing capability declaration.** Skills should explain how to
use tools and compose workflows, while directing capability checks to the
installed tool schema, contract and actual refusal. In this session the stale
skill overrode the schema; that is evidence of a maintenance failure, not a
universal instruction-precedence rule.

The assistant should explain the available operation and the actual limitation:
“This installation cannot run that custom tracker on saved images. I can attach
a tracking hook before we acquire the movies.” Use this wording only when the
capability is actually unavailable. If capability discovery is inconclusive, say
it has not been verified, rather than declaring it unimplemented.

Reconcile the offline skill with the implemented adapter contract and installed
tool surface. Explain dependency, supported output, or adapter validation failures
directly. Do not cite design numbers, internal register rows, or milestones in
ordinary microscope conversation, or replace them with unexplained internal class
names. An explicit development discussion is outside this restriction.

### D2 — complete a movie at each position by default

For short continuous motion, tracking, transport, or “watch what happens at each
field,” use position as the outer loop and time as the inner loop:

```
field A: frame 0, frame 1, ... frame 49
move to field B
field B: frame 0, frame 1, ... frame 49
```

Make this the consistent default for the multiposition timelapse protocol, with
or without a hook. Add one explicit semantic choice, `acquisition_order`, with
values `position_then_time` (default) and `time_then_position`. Thread it through
schema, validation, planning, execution, results and standalone export. Reject an
inapplicable order before hardware action rather than silently ignoring it.

`time_then_position` remains supported for slow processes or an explicit request
to sample all fields at each time point. Explain its revisit interval when it
matters. Do not change an explicitly requested order based on a keyword heuristic.
Until the consistent path exists, plan separate hooked `run_timelapse` calls at
each position for motion; do not assume that naming the protocol “timelapse”
establishes the nesting order.

For paths that build a multi-position event list, map the two names onto the
engine's existing `order` argument (`"ptcz"` / `"tpcz"`). Reuse its axis ordering;
the scheduling choice below determines how per-field timing is implemented.
A per-position acquisition loop implements position-outer order structurally.
Forward the semantic choice from `run_tile_acquisition`, and audit all emitter
sites to preserve the selected execution structure and timing. Update each
applicable site; do not change unrelated single-position or Z-stack semantics
merely because they also call the event builder.

Changing axis order alone is insufficient: per-position intervals must be
scheduled relative to the start of that position's movie. A global time origin
can make later fields catch up in a burst after their deadlines have elapsed —
as in the unmodified position-outer event list described above.
Interleaved mode uses a shared time-point schedule. Test both scheduling
contracts.

Two consequences to carry into 77b. **A contiguous zero-interval run is eligible
for hardware sequencing.** On a compatible engine/camera, hardware-event hooks
may receive a batch instead of individual events. Frame-analysis callbacks still
receive individual images; do not turn a stateful observer into a batch analyzer.
The existing refusal for planned per-frame hardware actions inside a sequenced
batch can become relevant when the default changes. Validate declared action
plans against the resolved acquisition shape before exposure, and keep the
runtime batch guard as a backstop. Do not promise pre-exposure detection of an
arbitrary action first proposed by analysis of an acquired image. Verify observer
frame delivery and backlog/completion behavior rather than assuming observers
are unaffected by sequencing.

**The new default differs from the engine's.** The hookless path is already
position-outer and its docstring promises a per-position protocol, which supports
making that the consistent tool default. Explain both available orders in tool
wording without assuming that every user or MM MDA configuration uses the same
order.

Keep hook state separated by position and channel, with defined initialization
and completion for each movie. An observer must not change acquisition order.
Preserve position labels, actual coordinates, frame indices, and partial-run
provenance regardless of dataset layout. Do not reset tracking state every frame
or link particles from different fields.

### D3 — distinguish exposure, requested interval, and observed cadence

Before acquisition, briefly state the order and frames/duration per field.
`interval_s=0` means no requested delay; it does not guarantee a frame rate equal
to inverse exposure. Camera readout, hook processing and storage can add delay;
interleaving adds moves, settling and the other fields' acquisitions.

Record the resolved order and timing semantics in the result and exported plan.
Use acquisition timestamps, where available, to report actual per-field frame
spacing and gaps — and **name the metadata key the implementation will read, and
verify it on the rig before relying on it.** A plausible-looking key that a
camera adapter does not write is as damaging as an invented one, and this
repository has been caught by that shape before. The hook log's `observed_at` is
a callback arrival stamp, which is what the assistant read at line 156; callback
and log arrival timestamps are not automatically exposure timestamps. If only
those are available, label the limitation. Tracking should use actual elapsed
time where its model needs time, and flag excessive gaps or missing timing as
insufficient evidence. Zero accepted tracks is not by itself proof of absent
motility. Verify event order before defending what happened.

### D4 — resolve analysis delivery before collecting its inputs

When the requested result includes custom analysis, map each deliverable to an
executable path during initial planning. Inspect the applicable hook/adapter
contract and dependencies before promising execution. For this request:

| Deliverable | Plan before acquisition |
|---|---|
| Filamentous, sparse MT fields | Per-frame observation hook and logged ranking measurements |
| Walking kinesin per field | Stateful tracking hook attached to each continuous movie; defined completion and evidence thresholds |
| Yellow boxes on every frame of a qualifying track | Offline saved adapter over the completed 640 dataset; classification can depend on later frames |
| Saved stage positions | Use measured field identity and completed, qualified analysis results |

Proactively offer to write and attach the observation hooks before the first
relevant acquisition. Do not postpone that offer until an offline operation
fails. Follow existing hook review and execution requirements without adding a
new blanket approval interview. Resolve only missing scientific choices that
materially affect the result, such as motion criterion or sampling duration.

A live observer can accumulate tracks and report a verdict, but that is not the
same deliverable as a movie retrospectively annotated from its first frame. That
split is real; what is not real is the claim that the retrospective half had
nowhere to run. Verified: a reviewed saved adapter implementing
`analyze_completed_dataset` receives a read-only `DatasetView` restricted to the
requested axis selection and
an `ArtifactDirectory` whose `emit(filename, payload)` writes a multi-page TIFF
through `tifffile` when the filename ends `.tif`/`.tiff`, bounded by
`artifact_limits` that default to 64 artifacts, 64 MiB each, 256 MiB total and
are settable by the caller. **The yellow-box overlay per ROI fits inside that
contract with no apparent extension**: a 50 × 150 × 150 RGB uint8 movie
contains 3,375,000 pixel bytes before TIFF overhead. This establishes budget
feasibility, not successful rendering. Select all time points for each intended
position/channel, preserve frame order and verify the actual artifact end to end
in 77c before claiming delivery. What remains true is the narrower point:
`EmitArtifact` in a
*live* hook does not supply unrestricted movie writing, and no new lifecycle
callback exists. Check the actual limits and available offline verbs before
promising either. If some future output really does need a contract extension,
identify it before imaging; extend the shared hook analysis contract rather than
creating a separate custom analysis code path.

If custom offline execution is unavailable on the installation, explain that
early and offer the supported acquisition hook plus a precise account of which
outputs it can produce. If an essential requested output remains unsupported,
settle that scope before its acquisition. A script merely written to disk is
not an executed analysis or a delivered overlay. Do not re-expose a sample simply
to compensate for failing to attach an available observer the first time.

## The one open question 77b must answer first

`position_then_time` with `interval_s > 0` needs a per-field timing origin.
The engine's unmodified static timestamps reuse the acquisition origin and make
later fields catch up. Static offsets can define a planned schedule, but stage
movement, settling and acquisition delays make actual field starts unpredictable.
Three options remain; choose the execution contract before implementing 77b:

1. **Refuse the combination temporarily.** Keep continuous position-outer runs
   and explicitly interleaved spaced runs, and reject the unsupported combination
   before hardware action. This is honest but removes an ordinary experiment:
   "sample this field every 2 s for a minute, then move on". Do not regress the
   already-supported hookless per-position case merely to simplify validation.
2. **Synchronize streamed deadlines with execution.** A generator alone is not
   sufficient. `_survey_event_stream` explicitly distinguishes events dispatched
   in microseconds from acquisition over minutes — a pre-dispatched event "sits
   in the engine's queue within microseconds" — and a yield timestamp is
   therefore not a field-start timestamp. Note what this does and does not rule
   out: a blocking generator *can* gate dispatch, which is how the adaptive
   runner already holds a scan open, so the missing piece is the acknowledgment,
   not the ability to hold back. This option needs a concrete execution acknowledgment,
   a defined field-start event and clock origin aligned with the engine's
   acquisition clock, and a barrier preventing later deadlines from being
   stamped before that acknowledgment. Specify timeout, cancellation and export
   behavior too. Reuse streaming machinery where appropriate, but do not claim
   it already supplies this synchronization.
3. **Per-position acquisitions with coordinated hook state and logging.** The
   hookless path already provides the timing semantics. Keep this option open:
   it may be simpler and more reliable than adding synchronization. It changes
   the single-dataset layout introduced by design/19 F2, so explicitly account
   for dataset identity, position provenance, hook lifecycle and a combined log
   or result index. A single dataset is useful, but does not automatically
   outweigh correct sampling and implementation simplicity.

There is no unconditional recommendation for option 2. Compare it with option 3
using an execution-level timing experiment, including delayed queue consumption
and variable stage settling, before choosing. If only a restricted first cut is
ready, document and test its refusal; never silently substitute bursting or
interleaving. The fake-clock acceptance check below must exercise the supported
spaced path, and any unsupported path must refuse before movement or exposure.

## Implementation blocks

**77a — truthful runtime guidance and planning.** Reconcile hook-authoring with
the saved-adapter implementation; remove development references from runtime
instructions; update the agent's planning guidance and acquisition tool wording.
Replay the initial request through the model before any acquisition. This block
must establish advance hook planning even on a rig without offline support.

### 77a checklist — what the implementer owns

The audit surface is enumerated in §Code findings and was re-verified at
`32a95f1`: ten `design/NN` in `hook-authoring/SKILL.md`, and four of the seven
pre-coded hook class docstrings (`focus_feedback` design/36, `position_filter`
design/27, `snr_observer` design/26, `mm_plugin_analyzer` design/09) reaching the
model as `class_docstring`. `tools_schema.py` has none.

1. **Reconcile the skill with the implemented offline contract.** Both stale
   passages (SKILL.md lines 66–68 and the capability-table row at 521–523) say
   the orchestrator and `DatasetView` are unimplemented. They are implemented:
   `microclaw/completed_dataset.py` (`OFFLINE_VERBS`, `DatasetView`,
   `ArtifactDirectory.emit`, `run_analysis_on_saved_dataset`'s limits). Replace
   them with the contract as the code actually defines it, and state the D1 rule
   inside the skill: a skill explains how to compose tools and directs capability
   checks to the installed schema and the tool's own refusal; it does not
   maintain a competing declaration of what exists.
2. **Remove development references from every string the model can be shown**,
   substituting the substance — never a bare deletion, never an unexplained
   internal class name. Sweep the surface programmatically rather than fixing the
   enumerated list only: every `microclaw/skills/*/SKILL.md` shipped by
   `load_skill`, every `TOOLS` description, and every pre-coded hook class
   docstring returned by `list_hooks`/`describe_hook`. `design/NN` in an ordinary
   code comment stays.
3. **Guard it with a test that enumerates that surface from the code**, not from
   a literal file list, so a skill or hook added later is covered. A second test
   pins the skill's offline section to `completed_dataset.py` itself — assert it
   names what `OFFLINE_VERBS` and the module's default artifact limits actually
   are — so the two cannot drift apart again. Watch both fail on the pre-fix tree.
4. **Agent planning guidance** (`agent.py`'s `SYSTEM_PROMPT`), from D1, D3 and D4:
   map each requested deliverable to an executable path while planning, before the
   acquisition that feeds it, and offer to write and attach the observation hooks
   then rather than after an offline attempt fails; check the installed tool
   contract and its actual refusal before calling anything unavailable, and say
   "not verified" when discovery is inconclusive — "no adapter written yet" is not
   "custom offline execution unavailable", and a missing tracker dependency blocks
   that tracker, not the path; never cite a design number, register row or
   milestone in a microscope session; and keep exposure, requested interval and
   observed cadence distinct, stating order and frames per field before the run.
5. **Acquisition tool wording** (`tools_schema.py`,
   `run_multiposition_acquisition`). Today the description promises a
   per-position protocol and does not say that passing `hook_strategy` routes
   through a combined event list whose engine default is time-outer, so every
   position is visited at each time point. Make the wording true of the code as it
   stands, including the interim guidance in D2 — separate hooked `run_timelapse`
   calls per position when the deliverable is continuous per-field motion.
   **77b replaces this wording when the default changes**; that churn is expected
   and is not a reason for 77a to document a behaviour that does not exist yet.

Out of scope for 77a: `acquisition_order`, event construction, emitters, the
timing origin. Those are 77b, and the open question above gates them.

**The gate is a model replay and the coordinator owns it** (block-workflow step 5).
It calls a live model and spends real money, so it is priced and agreed before it
runs. It replays the opening of `20260905_165524_640862_microclaw_history.jsonl`
against recorded tool results and scores three things mechanically: no development
reference in what the model says, analysis proposed before the acquisition that
feeds it, and the overlay accounted for separately from the live verdict. It runs
the available-offline fixture, an unavailable one and an empty manifest, and the
"you moved through all six fields at each time point" challenge from line 155.

**77b — explicit, consistent acquisition order.** Settle the open question
above, then extend `run_multiposition_acquisition`, `run_tile_acquisition`'s
forwarding, event construction and applicable emitter sites — including the
hookless path — to implement D2–D3. Keep the default independent of whether
an observer is attached. Test scheduling and state boundaries, not just argument
propagation or generated-source compilation.

**77c — end-to-end delivery check.** Exercise a small continuous movie per field
with an observation hook, plus an explicit slow-process interleaved control.
Verify that declared order, executed order, timestamps, position identity and
hook summaries agree in live and exported execution. Run a reviewed saved adapter
on a small deterministic movie with a known moving track and write/read back the
retrospective RGB TIFF. Check frame count/order, hollow yellow boxes on the
qualifying track's early as well as late frames, and unchanged source data.
This proves the execution/artifact contract, not biological tracking accuracy.
A general-purpose tracker
or retrospective movie-writing extension is separate work if existing contracts
cannot support it; do not mark that output delivered by this design.

## Acceptance evidence

- A model replay of the initial request proposes attached analysis before the
  relevant acquisition, accounts separately for the overlay, and mentions no
  development document. Exercise both the available offline adapter path and an
  unavailable one. Specify the unavailable fixture concretely: an installed
  tool contract that lacks custom saved-adapter execution, or a required tracker
  dependency that cannot be satisfied in the test environment. A missing
  dependency blocks that tracker, not all custom offline analysis. Separately
  exercise an empty adapter manifest: execution may be supported and the correct
  next step may be to author an adapter. Do not score "adapter not yet written"
  as "custom offline execution unavailable".
- Two positions × three frames produce `A0 A1 A2 B0 B1 B2` by default, with and
  without a hook; explicit interleaving produces `A0 B0 A1 B1 A2 B2`. State the
  observation channel for each resolved path: assert event axes when a combined
  event list exists; for per-position acquisitions, record stage moves and frame
  execution across acquisition boundaries. Dataset names alone cannot prove
  frame order. Include hookless explicit interleaving, which the current
  per-position loop cannot implement merely by forwarding an order argument.
- Exercise a sequenced hardware-event batch with a recording engine fake and,
  where supported, the demo engine. Each frame still reaches the observation
  callback once with its own metadata. A declared per-frame hardware action plan
  incompatible with that shape is refused before exposure; exercise the runtime
  batch guard separately. Do not require every camera to sequence, or claim that
  an image-dependent action can be discovered before its input image exists.
- A fake clock with nonzero intervals and a slow position change proves that
  field B retains its requested spacing instead of catching up to field A's
  elapsed schedule. Delay queue consumption independently of generation, vary
  stage settling, and assert against execution timestamps so stamping at yield
  cannot accidentally pass. If the chosen first cut refuses the hooked spaced
  combination, assert that refusal before hardware action and retain the timing
  check for the supported hookless case. Continuous mode adds no artificial wait.
- A stateful observer receives all frames of each movie, never links across
  fields, and distinguishes complete from interrupted movies. An injected gap
  prevents an unsupported “no motion” verdict.
- Execute the exported source against a recording acquisition fake and compare
  its sequence, timing, labels and hook outputs with live execution. Parsing the
  export is necessary but insufficient.
- Replay the user's challenge about position order. The assistant checks evidence
  and explains the actual sequence rather than asserting what the tool name means.
- Use a demo acquisition to verify event scheduling and recorded timing through
  the real bridge. A motion-positive specimen or suitable recorded fixture is
  needed to validate a tracker scientifically; event-order tests alone cannot
  establish tracking accuracy. No sample re-exposure is needed to approve this
  design or run the local planning and sequence checks.

## Run ledger

Baseline before the notebook: `main` `c2f19dd`, coordinator-run suite
**2862 passed / 99 skipped / 2 warnings** in 163.6 s (2026-09-05,
`.venv/bin/python -m pytest -q`). The two warnings are the one benign
`phase_cross_correlation` `UserWarning` from
`test_featureless_field_returns_error_not_garbage` doing its job; they are not
findings. The notebook fast-forwarded onto `main` as `32a95f1`, which is
design-only and changes no count.

| block | branch | start | implementation | gate | merge |
|---|---|---|---|---|---|
| 77a | `design77/truthful-guidance` | `32a95f1` (2026-09-05), worktree `../microclaw-77a` | | | |
| 77b | — | not started; blocked on the open question above | | | |
| 77c | — | not started; follows 77b | | | |
