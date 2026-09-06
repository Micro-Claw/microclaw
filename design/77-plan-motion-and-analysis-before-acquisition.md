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
actual field start. That requires an execution-aware timing origin — but only when
`interval_s > 0`, which is what §The open question, settled establishes and is
why the fix splits in two.

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

Verified leak surface, and it is small — **but this enumeration was incomplete,
corrected 2026-09-06 by block 77a**: an AST sweep for `design/NN` inside
non-docstring *string literals* also finds `tools.py`'s `calibration_note`,
which is returned in a `calibrate_stage_to_camera` payload and reads
"(design/29)". The lesson is the general one: a design doc's enumeration is a
starting point, and the mechanical check that would confirm it costs a minute.
`tools_schema.py` has **zero** `design/NN` references; `agent.py`'s two are in internal developer docstrings the
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
contract with no apparent extension** — true of the *runner*, and block 77a
found the sentence was nonetheless wrong about what a microscopist could do.
`generate_and_save_hook` is the only caller of `save_hook`, and its static
preflight refused any class not defining `analyze_frame` or `image_process_fn`,
so **no offline adapter could reach the manifest at all**; the contract was
reachable only by hand-editing the manifest, which is what the suite's own
fixture does. 77a extended the preflight. "Fits inside the contract" is not the
same claim as "a user can get there from here", and this notebook asserted the
first while meaning the second. On the budget itself: a 50 × 150 × 150 RGB uint8 movie
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

## The open question, settled

Settled 2026-09-06 by operator decision, on evidence measured against the
installed `multi_d_acquisition_events` rather than argued:

```
order="tpcz"  (today's default)   P0T0 P1T0 P0T1 P1T1 P0T2 P1T2   <- the incident
order="ptcz"                      P0T0 P0T1 P0T2 P1T0 P1T1 P1T2   <- the default we want

interval_s=0   min_start_time is None on every event -- there is no clock
interval_s=2   field B's events carry min_start_time 0, 2, 4, the SAME values
               as field A, all already past when B starts. B bursts.
```

**The catch-up problem exists only when `interval_s > 0`.** That was not obvious
from the design and it splits the work in two:

* **`interval_s == 0` — one acquisition, `order="ptcz"`.** The engine emits no
  `min_start_time` at all, so there is nothing to catch up on. The single dataset
  with a `position` axis survives, and so does `build_stage_coordinate_mosaic`,
  which cannot take per-position datasets. This is the tiling and continuous-burst
  case, and it is close to a one-argument change.
* **`interval_s > 0` — option 3, per-position acquisitions.** One shared clock
  cannot express per-field spacing, and the hookless path already gets this right
  by giving each position its own acquisition. Making the hooked path do what the
  working path does beats building the synchronisation option 2 would need.

Option 2 is **not** being built. It needed an execution acknowledgement, a defined
field-start event, a barrier and cancellation semantics that do not exist; option 3
needs none of that and is already proven on the hookless path. Option 1 is rejected
outright: refusing "sample this field every 2 s for a minute, then move on" removes
an ordinary experiment, and the operator's instruction is that the default must be
sensible, never that the alternative be blocked.

**This is a default, not a restriction.** `time_then_position` stays available for
slow processes and for anyone who asks for it, and an explicitly requested order is
never overridden by a keyword heuristic.

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

6. **Make the advertised path reachable** (added mid-block, operator decision
   2026-09-05, after review round 1). The reconciled skill sends an author to
   `run_analysis_on_saved_dataset` "with a reviewed, hash-pinned adapter from the
   saved manifest", and `generate_and_save_hook` — the **only** caller of
   `save_hook` — refused that class shape outright:
   `"No top-level class defines analyze_frame(self, image, metadata) or
   image_process_fn(self, image, metadata, event_queue)."` The runner accepted the
   offline verbs; the saver did not, so no adapter could reach the manifest at
   all and round 1 would have turned the stale text's false negative into a false
   positive. The suite missed it because `tests/test_completed_dataset.py`'s
   `offline_home` fixture **writes the manifest entry by hand** — a fixture that
   cannot reach the code is not coverage of it. The static contract check now
   knows `OFFLINE_VERBS`, arity-checked per verb, and the end-to-end test saves
   through the real tool and runs what it saved.

   And a saved offline adapter has to say **which runner takes it**: `resolvable`
   answers "would the source be refused", not "by whom", and the prompt's own
   ladder says to name a resolvable hook and use it. `describe_hook` and
   `list_hooks` carry a `route`; attaching one to an acquisition refuses by name.

Out of scope for 77a: `acquisition_order`, event construction, emitters, the
timing origin. Those are 77b, whose decision is settled above.

**The gate is a model replay and the coordinator owns it** (block-workflow step 5).
It calls a live model and spends real money, so it is priced and agreed before it
runs. It replays the opening of `20260905_165524_640862_microclaw_history.jsonl`
against recorded tool results and scores three things mechanically: no development
reference in what the model says, analysis proposed before the acquisition that
feeds it, and the overlay accounted for separately from the live verdict. It runs
the available-offline fixture, an unavailable one and an empty manifest, and the
"you moved through all six fields at each time point" challenge from line 155.

**77b — explicit, consistent acquisition order.** The open question is settled
(see above); extend `run_multiposition_acquisition`, `run_tile_acquisition`'s
forwarding, event construction and applicable emitter sites — including the
hookless path — to implement D2–D3. Keep the default independent of whether
an observer is attached. Test scheduling and state boundaries, not just argument
propagation or generated-source compilation.

### 77b checklist — what the implementer owns

The decision is settled above; do not re-open it. Build:

1. **`acquisition_order` on `run_multiposition_acquisition`**, values
   `position_then_time` (default) and `time_then_position`, forwarded from
   `run_tile_acquisition`. Thread it through schema, validation, planning,
   execution and results. Reject an inapplicable order **before** any hardware
   action, never by silently ignoring it.
2. **Hooked, `interval_s == 0`: one acquisition with `order="ptcz"`.** Measured:
   the engine emits no `min_start_time`, so there is nothing to catch up on, and
   the single dataset with a `position` axis is preserved. `_build_acquisition_events`
   currently forwards no `order` at all and inherits the engine's `"tpcz"`.
3. **Hooked, `interval_s > 0`: one acquisition per position.** Each gets its own
   clock, which is the only thing that makes per-field spacing expressible.
   Account explicitly for what design/19 F2's single-dataset layout gave you and
   this gives up: dataset identity, position provenance, hook lifecycle and
   initialisation/completion per movie, and a combined log or result index.
4. **The hookless path owes `time_then_position`.** It is position-outer
   structurally, so it already satisfies the default — but a per-position loop
   **cannot** implement explicit interleaving by forwarding an order argument.
   That case needs a real path, and the acceptance evidence names it.
5. **D3's reporting.** Record the resolved order and timing semantics in the
   result and the exported plan. To report *observed* per-field frame spacing,
   **name the metadata key the code reads and verify it on a rig before relying
   on it** — a plausible key no camera adapter writes is as damaging as an
   invented one, and this repository has been caught by that shape before. The
   hook log's `observed_at` is a callback arrival stamp, not an exposure stamp;
   if that is all there is, label the limitation.
6. **Emitters.** Corrected 2026-09-06 by the coordinator, read out of the code
   rather than carried from the notebook's prose: `run_multiposition_acquisition`
   is `@emits(_emit_multiposition)` — it does **not** route to `_emit_adaptive`
   or `_emit_acquisition`. `_emit_multiposition` branches internally: a hooked
   observation-only run emits one combined `multi_d_acquisition_events` call
   (`tools.py:719`), and the hookless run emits a per-position Python loop with
   an `Acquisition` per position (`tools.py:783`). Two further emitters forward
   into it and inherit whatever it does — `_emit_tile` and
   `_emit_multiposition_with_autofocus`. Both branches must reproduce the
   executed order *and* the timing strategy, and the forwarders must carry the
   argument. An emitter's fallbacks are the tool's defaults, not constants.

**The trap this block carries.** Splitting the hooked path into N acquisitions
puts it straight into block 60a's territory: each acquisition needs its own
supervised teardown, its own reservation, and a typed `AcquisitionUnterminated`
that survives to `execute_tool`. 60a returned four defects and every one was a
broad `except Exception` between a supervised acquisition and its boundary,
including one that flattened a failure into a per-position error and **kept
acquiring**. A per-position loop is exactly that shape. Ask who catches
`Exception` between the raise and the boundary before writing the loop.

**Not in 77b**: 77c's end-to-end delivery check, the saved-adapter overlay, and
anything needing a rig.

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
| 77a | `design77/truthful-guidance` | `32a95f1` (2026-09-05), worktree `../microclaw-77a` | `7f813ab` + `14dbdc5` + `6e7c571` (1 Codex start, 1 revision, 1 coordinator commit). **Round 1 shipped a skill that advertised an unreachable path** — see checklist item 6; the coordinator found it by trying the save through the tool rather than reading the diff, which no amount of diff review would have shown. Four smaller round-1 findings: the reconciled offline section stated the verbs and the artifact budget but **not the return contract**, and this skill teaches `HookResult` everywhere, so a reader would have hit `TypeError: Offline analysis results must be dictionaries or None`; the prompt's 4b ladder still taught `analyze_frame` as the only saved-hook shape, which is the incident's second half; the new leak guard covered skills/schema/prompt/hook-docstrings but **not strings the tools return**, which is exactly the site the notebook's own enumeration missed (`calibration_note`, `tools.py:5818`); and the "never cite development documents" rule was filed under saved-hook resolvability rather than in the Reporting section where this prompt keeps its speech rules. **The revision turn hit its provider usage limit after committing and before reporting**, so `14dbdc5` arrived with no handoff: the coordinator reviewed the diff, re-ran the suite, and reproduced every watch-it-fail independently rather than accepting it. Round 2 then left `list_hooks` calling an offline adapter `resolvable` with no route and refusing it at attach time with a bare `AttributeError`; `6e7c571` is the coordinator's fix. Coordinator suite **2868 passed / 99 skipped**, against a 2862 baseline — 2862 + 6 new, nothing else moved. Watch-it-fail reproduced independently at each round: both round-1 guards on `f6eb4d0`; all four offline save/run tests on `7f813ab` with the real preflight refusal quoted; the AST half of the leak guard isolated by restoring `f6eb4d0`'s `tools.py` alone, where it reports the `calibration_note` leak **and only that**; and `KeyError: 'route'` plus the old `AttributeError` for the coordinator commit. | **PASS on arm B; arm A measured nothing.** The gate is `design/77-block77a-replay.py`, run locally against `claude-opus-4-8` (microclaw's `DEFAULT_MODEL`, and the model that ran the incident), swapping `--tree` between a pre-77a checkout and this branch so the skill file changes with the prompt. **Arm B, the line-124 decision point: 0 of 8 control samples named an offline verb, 6 of 6 on the tree under test.** Complete separation. `run_analysis_on_saved_dataset` alone does **not** discriminate — 1/8 against 5/6 — because a model that believes the path is gone still names the tool while declining to use it; only `analyze_completed_dataset`/`analyze_saved_frame` separates them. The control reproduced the incident's substance in its own words (*"the offline adapter path for generated hooks isn't shipped yet"*, *"the sanctioned offline-movie path doesn't exist"*) and the tree under test produced the D1 distinction the design asked for, unprompted: *"[the adapter does] not exist yet — writing it is the right answer, and I'll write it."* **Arm A is underpowered and is not evidence**: 0/4 control against 1/3 on the branch, at ~5 minutes and ~$0.32 a sample, and its 6-turn cap truncates the model mid-orientation before it reaches the analysis plan. Reported as measured-nothing, not as a null result. **Five defects, all the instrument's, none the product's.** A sample cut off mid-tool-loop scored `NEITHER`, so four control samples read as a real null before `NO_DECISION` existed. The pruned arm B fixture attached the session's *first* `run_analysis_on_saved_dataset` result — a near-blank TIRF check on another dataset — under a synthetic claim that the six kinesin movies were saved; the model noticed and spent its decision turn arguing with the premise, so results are now selected by what the call was about. The scored criterion had to leave prose entirely: eight control samples claimed unavailability in five wordings, so the phrase list is unbounded and fitting it to the control is the design/61 trap. `authored` — a `def` or a `generate_and_save_hook` call — is stronger and **this fixture cannot reach it**, because the prompt requires waiting for confirmation before saving a hook, so the replay ends at the question and scoring required behaviour as failure made both trees read zero. And arm A drove operator replies for up to fourteen turns against a docstring saying it stops at the first question. **Spend overran its authorisation: $12.13 against $10.** The coordinator reported $3.97, which was the total across the five runs that reached their end-of-run print, plus a guess of ~$2.7 for the killed ones; the real figure for those was ~$8.2, and the operator's dashboard is what caught it. Two of the killed runs were arm A at fourteen turns a sample, and every fresh process re-wrote the 31.6k-token cache prefix at 1.25x. The instrument now meters after **every sample** and takes a `--budget` that stops before the sample which would exceed it: a budget metered only at the end is not a budget, because a killed run reports nothing at all. | `9837d73` merged 2026-09-06; branch deleted locally and on `origin`, worktree removed. Post-merge design gate in `design77/close-ledger`: D1's audit corrected in place (the notebook's enumeration missed `calibration_note`), D4 reconciled to what shipped, and the reachability of the saved-adapter path recorded as the block's real scope change. **77b and 77c are untouched by this block.** 77b's open question was settled separately on 2026-09-06 — see §The open question, settled. |
| 77b | `design77/acquisition-order` | `7c33284` (2026-09-06), worktree `../microclaw-77b`, suite **2868 passed / 99 skipped**. Open question settled — do not re-open it. Step 2 opened 2026-09-06: implementer assigned via `codex-runner`, checklist items 1–6 above. | | | |
| 77c | — | not started; follows 77b | | | |
