# Adaptive streaming hooks for STORM

## Finding

Amr's 2026-08-29 M2 session did not fail because the agent overlooked hooks. It
called `list_hooks`, said Phase II needed a new hook, called
`get_hook_documentation`, and found that the hook action vocabulary and the
acquisition runners do not expose the same capability. The requested loop was:

1. acquire continuously at one field;
2. count blinks over a three-frame window;
3. change `Laser Trigger.Duration0 (us)` from that measurement;
4. stop after the 50,000 us terminal condition plus 1,000 frames, with a hard
   cap of 200,000 frames.

Each ingredient exists in isolation; no runner combines them for a
single-position streaming acquisition:

| Capability | Fixed `run_timelapse` | `run_adaptive_survey` |
|---|---|---|
| Analyze each returned image | yes | yes |
| Image-derived `SetDeviceProperty` | no | yes |
| Image-derived stop | no | yes |
| Stay at one field and append time events | fixed events only | no; walks a planned position list |
| Continuous hardware-sequenced time burst | yes, with `interval_s=0` | no |

A generated hook implements `analyze_frame(image, metadata) -> HookResult` and
has no controller, core or event queue. Under a fixed plan,
`UntrustedHookAdapter` refuses `MoveNamedStage` and `SetDeviceProperty` from
`analyze_frame` (`hook_decisions.py:905`, "fixed-plan hardware actions must come
from hook_action_plan, not analyze_frame") and refuses `StopSurvey` as
`unsupported-by-this-runner` (`:920`), while `ContinueSurvey` is an accepted
no-op (`:917`). The only property changes that path accepts arrive before the
run as a fully expanded, axes-indexed `hook_action_plan` bounded by a
`property_envelope` — intentionally open-loop: every action is known before the
first image exists.

`run_adaptive_survey` has the missing trusted-parent handoff, and is closer to
what STORM needs than the table suggests: `_dispatch_adaptive_partition`
(`hook_decisions.py:1098`) pairs image-derived hardware actions with exactly one
next-event selector, `_queue_adaptive_candidate` (`:646`) registers them against
the event's axes signature *before* publishing it, and `pre_hardware_hook_fn`
applies, waits, reads back and audits them immediately before that exposure.
What is spatial in it is small, and enumerated below.

There is a second, independent constraint. With `interval_s=0`, pycro-manager
may hardware-sequence the whole time axis as a list. No Python callback runs
between those exposures, so a software hook cannot change `Duration0` per frame
inside that burst; `run_timelapse` refuses a per-frame `hook_action_plan` in
that mode (`tools.py:4216`). A feedback-controlled run therefore needs
software-dispatched events with honest cadence reporting, or a hardware sequence
encoding the feedback — impossible when later values depend on images not yet
acquired.

## What is actually missing

Not a runner. The adaptive stack is already axis-agnostic where it matters: it
keys on `axes_signature`, never on XY. `_survey_event_stream` (`tools.py:7376`)
pre-dispatches only element 0 and then yields whatever the hook puts on
`candidates`; the reservation, the budget, `close_adaptive_handoff`, the
coordinator and the property envelope are all indifferent to which axis is
advancing.

Three things are survey-specific:

1. **`ContinueSurvey` means "dispatch `events[cursor]`"** (`hook_decisions.py:1031`),
   over a finite list built from positions, with the end-of-plan refusal at
   `:1019`.
2. **`AcquireAt`, `_event_xy`, and the up-front `check_xy` loop** address tiles.
3. **Names** — `SurveyProgress`, `StopSurvey`, `run_adaptive_survey`,
   "tile" throughout the log and refusal strings.

Only (1) is load-bearing for STORM, and it has two possible answers, which this
document must pick rather than leave to an implementer:

- **Pre-built time events.** Build `max_frames` events at one XY with axes
  `{"time": i}` and let the existing cursor walk them. Zero mechanism change;
  `max_events=len(events)` keeps meaning what it means, including in the
  exporter (`tools.py:1409`, `:1436`). Costs one dict per potential frame —
  200,000 of them — built before the first exposure, and makes `max_frames`
  an allocation as well as a cap.
- **A successor function.** Replace the list index with "the event after this
  one", so the plan is a rule, not an array. This is the honest generalization,
  and it is the one that breaks `max_events=len(events)` in both the runner and
  `_emit_adaptive` — so the export design (below) is part of this choice, not a
  follow-up to it.

**Recommendation: the successor function**, with `max_frames` carried
explicitly as the cap it already is. The pre-built list is attractive only
until the first 200,000-frame run.

## Documentation defect

The SMLM skill says density monitoring can "optionally signal
end-of-acquisition" (`microclaw/skills/smlm/SKILL.md:287`) and tells the agent
to offer a hook for adaptive density control (`:289`). That describes the
desired scientific workflow, not the shipped capability. The hook-authoring
skill is accurate: fixed hooked acquisitions refuse `StopSurvey`, and
image-derived property actions are available only through the adaptive survey.

**The skill is wrong in a second way nobody noticed.** `:283` says the density
monitor "can be implemented as an image_process_fn hook" — but a saved hook with
only a legacy `image_process_fn` is *refused* by the adaptive runner
(`tools.py:7563`), because parent-side runner state leaves it no way to ask for
the next frame. So the skill recommends, for an adaptive purpose, the one
callback shape the adaptive path rejects.

That contradiction produced the confusing conversation: the agent did what the
SMLM skill asked, then retracted after reading the lower-level contract. The
refusal was correct, but it made hooks look broken rather than showing that the
missing piece is a time axis on machinery that already exists.

The mechanism and the hardware were not at fault — a 200-frame open-loop run
applied planned `Duration0` writes with requested/achieved read-back and
restoration. What is unsupported is deriving those writes from the just-acquired
images and ending the same timelapse from the hook's decision.

## Decision: no new tool

The doc this replaces proposed `run_adaptive_timelapse`. **That tool existed and
was deleted.** Block 43j (merged 2026-08-11) folded `run_adaptive_timelapse` and
`run_adaptive_zstack` into the public surfaces of `run_timelapse` and
`run_zstack`: those tools accept hooks directly, and their exporter selects the
adaptive-program emitter for a hooked call. The live acquisition path did not
gain adaptive dispatch; it still builds the complete event list and passes it
to `_acquire_with_hooks`. Re-creating the deleted tool by name reverses the
public-surface decision and reinstates exactly the "two functions that do almost
the same thing" that 43j removed. The missing work is a new live route behind
the existing tool, not a second tool.

The capability ships as **`run_timelapse` with a hook that returns a next-event
decision**. `run_timelapse` already accepts `hook_strategy`, `hook_params`,
`log_path`, `property_envelope`, `illumination_envelope`, `artifact_limits` and
`laser_slot` (`tools.py:4182`). What it does not have is `max_frames` and a
route into adaptive dispatch. Both are additions to a tool, not a tool.

### Decision: `max_frames` selects the adaptive route

Routing must be settled before the seed exposure; it cannot wait to see what an
`analyze_frame` call returns, because the fixed path has already submitted its
complete event list by then. The tool contract is therefore explicit:

- `n_frames` selects the existing fixed route and keeps its current meaning;
- `max_frames` selects the adaptive successor route and requires
  `hook_strategy`;
- exactly one of `n_frames` and `max_frames` is required. `n_frames` is
  currently a required positional (`tools.py:4184`) and sits in the schema's
  `required` list (`tools_schema.py:692`), so this is both a Python and schema
  change. Note that no positional caller needs preserving — the only in-package
  call site passes everything but `ctrl`/`guard` by keyword (`tools.py:6219`) —
  so choose this shape for the *agent* call surface, where one always-present
  slot is easier to get right than two optionals of which exactly one is
  required. Implementation signature:
  `n_frames: int | None, interval_s: float, save_dir: str, ...,
  max_frames: int | None = None`: callers selecting the adaptive route pass
  `n_frames=None` explicitly. In the tool schema, keep `n_frames` required but
  make it nullable, add optional `max_frames`, and state the exactly-one rule in
  both **parameter** descriptions, not only in the tool prose. Thus every tool
  call still supplies the existing positional slot: fixed calls send an integer
  and omit `max_frames`; adaptive calls send `n_frames: null` plus an integer
  `max_frames`. The live function refuses neither-or and both before resolving
  paths or touching hardware, and **the emitter must refuse a `None` frame
  count explicitly** — see Export, where nullability removes a failure that is
  loud today;
- `max_frames` is incompatible with `hook_action_plan`: an axes-indexed plan
  cannot name events that do not exist until a predecessor image is analyzed.
  The exporter already refuses the same combination for the same reason
  (`tools.py:1262`), so this is one rule reaching the live path, not a new one;
- hook resolution must preflight the routing vocabulary. Be exact about what
  that can prove: the runner calls `validate_hook_contract` with the **loaded
  object**, which only checks that `analyze_frame` is callable
  (`hook_manager.py:281`); the AST scan that knows the decision names
  (`_hook_contract_analysis`, `:107`) runs on **source**, at save time. The
  preflight must therefore read the registry's pinned source and refuse a hook
  that never references `ContinueAcquisition` / `StopAcquisition` — which is a
  cheap refusal of the legacy shape, **not** a proof that frame 1 returns a
  decision. A hook that references the vocabulary and returns none must still
  fail closed at the first image rather than idle to `max_idle_s`; the adaptive
  survey does not do that today, and this route must not inherit the stall;
- a logging hook continues to use `n_frames` and the existing hooked-fixed
  route, even though it implements `analyze_frame`.

This makes the mode visible in the recorded tool arguments and gives the live
and export routers the same discriminator. It also avoids an `adaptive=true`
flag whose only valid use would duplicate the presence of `max_frames`.

Two 43j rules apply directly to that route:

- **Neither existing path may change.** A plain SMLM timelapse with no hook
  keeps its fixed events, its `interval_s=0` burst and `_emit_acquisition`; a
  hooked *fixed* run — a logging hook, or a `hook_action_plan` — keeps building
  its complete event list and keeps the export it gets today. Only a
  `max_frames` call whose resolved hook passes the decision-contract preflight
  reaches the new route.
- **An emitter's fallbacks are the tool's defaults, not constants**, and every
  argument the tool accepts must reach the emitted script. 43j's dataset-name
  fallback was the literal `"adaptive"` after the twins were folded away, so the
  live run and the standalone script wrote differently named datasets.

### Decision: one axis-neutral decision vocabulary

Do not add `ContinueTimelapse` beside `ContinueSurvey`. Two verbs for "give me
the next event" makes every hook author pick one, and the wrong pick is a
refusal at frame 1. Rename the existing pair to `ContinueAcquisition` /
`StopAcquisition` and keep one meaning: *the parent decides what the next event
is; the hook decides whether there is one*. Nothing is owed backward
compatibility here (`CLAUDE.md`, "No legacy anchoring") — replace the names
outright, including in `hook_docs`, the hook-authoring skill and the schemas.

An outright vocabulary replacement still requires a migration, not an alias.
Update every precoded hook, saved-hook fixture, generated-hook example, schema,
contract check, exporter import list and action-count result in the same block.
Before the rig gate, inspect each rig's saved-hook registry for source that
imports or constructs `ContinueSurvey` / `StopSurvey`; migrate and re-review
that source, or make resolution refuse it before acquisition with a remedy that
names the replacement. Do not let an old saved hook construct successfully and
die after the seed exposure.

`max_frames` is acquisition shape and a dose cap, not a privileged hook
capability, so it does **not** belong in `HOOK_CAPABILITY_ARGS`. If implementation
introduces a new envelope or other privileged hook argument, add that argument
to `HOOK_CAPABILITY_ARGS` (`tools.py:161`) first, as design/55 requires. The
adaptive route itself is selected by the hook's decision contract and
`max_frames`; it should not require an otherwise meaningless capability flag.

## Event flow

1. Before acquisition, authorize the maximum possible dose: exposure,
   channel/illumination state, `max_frames`, runtime and disk estimate,
   property envelope, attempted-write budget, restoration policy.
2. Seed exactly one event, axes `{"time": 0}`.
3. Analyze its image in the saved, hash-pinned hook.
4. Require exactly one routing decision: `ContinueAcquisition` or
   `StopAcquisition`, with bounded hardware proposals permitted beside the
   former — the cardinality rule `_dispatch_adaptive_partition` already
   enforces.
5. In trusted parent code, resolve the current time index, build the one
   successor event at `time + 1`, register its ordered hardware actions, then
   publish it.
6. In `pre_hardware_hook_fn`, apply, wait, read back and audit the write before
   the new event is exposed.
7. On stop, cap, hook failure, engine failure or abort: close the handoff,
   restore per policy, close the reservation once, and report truthful
   planned/acquired/exposed/action counts.

The authorization in step 1 cannot call `plan_events` over the one-event seed:
that would reserve one exposure while permitting `max_frames`. Build the plan
from the cap instead, without allocating the future event dictionaries — and
fold rather than add: `plan_events` derives frames, duration and bytes from
`len(events)`, camera geometry and the largest `min_start_time`
(`acquisition.py:107`–`:131`), so the cap enters as a frame count, not as a
second dose estimator standing beside it. It covers `max_frames × exposure`,
the maximum runtime and disk estimate, all declared illumination effects,
hook-analysis dose, property write attempts and the restoration write. That
maximum plan drives confirmation and the reservation; actual acquired/exposed
counts in the result come from the run, not from the cap. The reservation is
never widened by a hook decision.

For Amr's hook, a stateful `analyze_frame` keeps a bounded three-frame blink
count, proposes the next `Duration0` inside the envelope, records the first
frame at which 50,000 us is reached, and returns `StopAcquisition` 1,000 frames
later. `max_frames=200000` stays a parent-enforced cap; the hook is never the
authority for dose.

Do not implement this as a compressed `hook_action_plan`. A schedule syntax
would serve the session's later open-loop ramp, but cannot express image
feedback or conditional stopping. Useful separately; not an answer to this.

## Cadence: measure it, do not legislate it

The doc this replaces proposed refusing `interval_s=0` whenever image-derived
actions are enabled. That may guard something the mechanism already makes
impossible: adaptive dispatch puts one event on `candidates` per completed image
callback, so there is never a second event to sequence with, and
`pre_hardware_hook_fn` already treats a one-element batch as ordinary
(`hook_decisions.py:600`). **Do not assert either way here.** Two things settle
it: a measurement of one-at-a-time dispatch cadence at a 50 ms exposure on M2,
including the largest gap the hook itself contributes; and confirmation from the
engine — not from a fake we wrote, per design/56 — that a singly submitted event
cannot be batched.

Whatever is decided, the run reports **measured** inter-frame gaps rather than a
nominal interval bridge latency cannot honour. A few milliseconds of overhead on
a 50 ms STORM exposure may be scientifically fine; that is a number to publish,
not to assume.

## Confirmation versus disclosure

Measured cadence is a **result**, not a consent question. Putting it in a
confirmation is design/60 block 60b's defect verbatim: a disclosure whose only
output channel was `CONFIRM_FN` became a blocking approval, and every
zero-interval burst stopped for a human. Ask what a "no" changes here — nothing,
except aborting the run — so it goes to the acquisition event sink and the
result payload.

The legitimate confirmation on this path already exists and does not need a
second one: the authorized dose (`max_frames` × exposure × illumination state)
and the property envelope. **And a change that can stop a run and wait for a
human needs the user's agreement before it ships, not after.**

## Teardown

This is the run design/60 was written from — an operator's ~100,000-frame
single-field dSTORM on M2, plus a Python callback per frame — so block 60a's
contracts apply unchanged and are not restated here. Two things are specific to
this shape:

- **A hook that thinks between frames widens the largest observed gap**, which
  is the `max(900, 5 × largest gap)` bound working as designed — but the run
  must record the gap distribution, or a stall and a slow hook are
  indistinguishable in the log afterwards.
- Design/60's foreground runtime ceiling remains plan-derived, so the adaptive
  synthetic plan must not contain exposure time alone. Add a conservative
  per-frame software allowance to its estimated duration, initially measured
  from the M2 cadence gate and rounded upward; record that allowance in the plan
  and result. Design/60 then applies its unchanged
  `max(estimate × 1.5, estimate + 300 s)` ceiling to the widened estimate. A
  hook whose single-frame latency exceeds the idle bound still stalls; a
  healthy run with ordinary dispatch overhead has room in the total ceiling.
  Report measured duration and the gap distribution so the allowance can be
  revised from evidence rather than silently becoming a permanent constant —
  and record it as **n=1 from M2**, not as a property of microscopes. Until the
  cadence gate has run there is no allowance to set: do not invent one.

  The cap-derived `AcquisitionPlan` is still mandatory in that unmeasured case.
  It always drives authorization, confirmation, reservation, frame and
  illuminated-time accounting, and the disk estimate; it is never `None`.
  Separate teardown supervision from that accounting input: before M2 provides
  a measured software allowance, give `_runtime_ceiling_s` no reliable runtime
  estimate (or an explicit fallback selector), so it uses design/60's
  `FALLBACK_RUNTIME_CEILING_S` of 24 h without discarding the acquisition plan.
  After the cadence gate, supervision uses the cap-derived exposure duration
  widened by the measured per-frame allowance. This requires distinct
  accounting-plan and runtime-bound inputs instead of overloading the current
  `_acquire_with_hooks(plan=...)` argument with both meanings. Its docstring
  claims that argument "supplies the normal duration-derived ceiling"
  (`tools.py:3687`), and that is what makes the overload easy to miss: `plan`
  also drives the progress disclosure's `frames_planned` and the planned-final
  frame trigger (`:3762`, `:3784`). Only the unterminated diagnostic falls back
  to `reservation.plan.frames` (`:3922`), so passing `plan=None` would silently
  blank the planned count in every progress event — relaxing the ceiling is not
  all it does.

## Export

Not a test row; a merge blocker — and larger than the name `_emit_adaptive`
suggests, because **it does not emit an adaptive program for a timelapse
today.** `_emit_timelapse` (`tools.py:1559`) routes to it whenever
`hook_strategy` or a `hook_action_plan` was recorded, but its non-survey branch
emits `acq.acquire(events)` over the complete pre-built list (`:1536`). The
seed-and-decide form — `configure_adaptive`, `SurveyProgress`,
`_survey_event_stream(..., adaptive=True)` — is emitted **only** by the
`kind == "survey"` branch (`:1409`–`:1436`), though the preamble inlines that
machinery unconditionally (`:1035`–`:1038`). Live and emitted agree today; both
build the whole list. Nothing is broken.

So `run_timelapse` ends with **three** emission forms, not two: hookless fixed
(`_emit_acquisition`), hooked fixed (today's `_emit_adaptive` timelapse branch,
for a logging hook or a `hook_action_plan`), and hooked adaptive — the only one
that emits a decision loop. `_emit_timelapse` currently selects on "is a hook
attached"; it must select on `max_frames`, the same discriminator as the live
path, or the exported script stops reproducing the run, which is the comparison
43h's gate rests on. **Making `n_frames` nullable costs a loud failure here.**
The hooked-fixed branch reads `params["n_frames"]` unconditionally
(`tools.py:1320`); today a missing key raises `KeyError`, but a recorded `None`
flows into `shape` — and unlike the survey branch (`:1418`), the non-survey
branch does not filter `None` out of it (`:1501`), so a routing miss would emit
`multi_d_acquisition_events(num_time_points=None, ...)`: a plausible-looking
wrong script, which is the one outcome this exporter exists to prevent. Refuse a
`None` frame count in that branch explicitly, and let test 11 cover the router.

- The successor-function choice decides the emitted cap and progress total with
  the runtime ones: `max_events=len(events)` and `SurveyProgress(len(events))`
  both presume a pre-built array.
- The decision loop is emitted by inlining `_survey_event_stream` and
  `UntrustedHookAdapter` with `inspect.getsource`, never re-written in the
  emitter — design/24 and design/27 are written into that loop.
- Any blink-counting helper added to `image_analysis` must be inlined, and the
  export test must **exec** the emitted source against fakes and drive
  `pre_hardware_hook_fn` per event. Block 52b lost three M5 trips to exports
  that compiled and did not run.

## Safety and concurrency

The saved hook stays untrusted: no `ctrl`, core, credentials, paths, or native
event queue. It proposes typed decisions; the trusted parent owns
authorization, budgets, bounds, read-back, logging and restoration.

No controller bridge call may be made from the image callback thread. pyjavaz
serializes every bridge call behind one lock, so writing hardware — or
prompting — from that callback can deadlock. The callback registers a decision
and publishes a candidate; `pre_hardware_hook_fn` executes the authorized action
immediately before the next event, as the adaptive survey already does.

## Tests

1. only the seed is dispatched before its image decision;
2. each `ContinueAcquisition` publishes exactly one monotonically indexed event;
3. a property proposal is registered before publication, applied and read back
   before the corresponding exposure;
4. `StopAcquisition` publishes nothing further and closes without waiting out
   the cap;
5. `max_frames` wins over a hook that always continues;
6. missing, duplicate, late, malformed and over-budget decisions fail closed —
   at the first image, never by idling to `max_idle_s`;
7. whichever answer the cadence section reaches: a refusal that fires, or a
   structural impossibility asserted as one. Not both, and not an assumption;
8. hook exceptions, property-write failures, aborts and engine failures restore
   the property and close the reservation exactly once, with the typed
   exception reaching `execute_tool`;
9. dataset time axes are dense and no frame overwrites a prior one;
10. export reproduces the adaptive program or refuses specifically — never a
    plausible fixed trace;
11. both existing routes are untouched: hookless `run_timelapse` still emits
    `_emit_acquisition` and hardware-sequences at `interval_s=0`, and a logging
    hook with `n_frames` keeps its live path and its export form;
12. `max_frames` with `hook_action_plan` refuses before event construction or
    hardware mutation;
13. before an M2 cadence allowance exists, adaptive teardown supervision uses
    the disclosed 24 h fallback while progress still reports
    `frames_planned == max_frames`, the reservation still caps committed frames
    at `max_frames`, and dose and disk confirmation still use the cap-derived
    `AcquisitionPlan`.

Check what the fakes all happen to supply before writing them: three of the
seven engine contracts in `CLAUDE.md` were missed because a fake encoded our
assumption rather than the engine's behaviour.

## Rig gate

M2 is the rig: its camera triggers the lasers, so live view is a dose there too.

Everything that only computes goes in one script — run against
`design/55-gate-probe-selftest.py`'s bridge-shaped fake before it ships. What
the operator judges, in order:

1. a no-hardware-action adaptive short run — the hook logs density, returns
   `ContinueAcquisition` through a small, fixed frame count, then returns
   `StopAcquisition`; this proves successor dispatch and early stopping without
   granting a property capability;
2. an image-derived property write to a non-dosing property, if the rig admits
   one;
3. a short authorized `Duration0` run with a deliberately simple predicate —
   continue while frame index < N — so the decision is checkable from the log
   without a STORM sample.

Record requested and achieved values, frame identity, stop reason, restoration,
measured cadence, and prove no exposure follows the accepted stop. Each step
names the **mechanism**, not the outcome: 52b's mandatory limb asked an agent to
set a property, the agent correctly used a better route, and the refusal being
gated never fired. Score it by reading the hook log against the dataset axes
against the emitted script. A full STORM sample is not needed to validate the
mechanism.

## Blocks

The documentation correction does not wait for the runner. The SMLM skill is
misleading agents today, and it is a shipped file in `microclaw/skills/`.

**65a — documentation, now.** Change the SMLM skill to say: observation-only
density logging is supported on `run_timelapse`; fixed predeclared property
schedules are supported with a nonzero interval and a bounded
`hook_action_plan`; image-driven **device-property** changes plus conditional
stop are not supported in one continuous timelapse today; `run_adaptive_survey`
is not a substitute for single-field STORM; do not offer to write an adaptive
STORM hook until the route exists. Fix `:283`'s `image_process_fn`
recommendation in the same pass.

**Corrected 2026-08-31, during the block.** That bullet said "image-driven
property changes", and written into the skill verbatim it produced an
*under*-promise on the one workflow the section is about. `SetIlluminationPower`
is dispatched **before** the fixed-plan refusal of `SetDeviceProperty` /
`MoveNamedStage` (`hook_decisions.py:866`), is not gated on the adaptive route,
and `run_timelapse` passes `illumination_envelope` through unconditionally — so a
hook's `analyze_frame` **can** drive 405 nm activation power from the frame it
just measured, today, inside an authorized envelope. That is exactly why this
document is about `Laser Trigger.Duration0 (us)`: that rig activates through an
FPGA pulse duration, a *device property*, which is the refused case. On a rig
whose 405 activation is a linearized power property, the feedback half of Amr's
loop is already available and only the stop is missing.

Two bounds this document also states loosely. The illumination envelope has
**no restore policy** — `configure_illumination` stores no `restore` key, there
is no `restore_illumination` beside `restore_named_stage` and
`restore_property`, and the schema is `additionalProperties: False` over
`device`/`property`/`max_power_percent`/`max_writes`. The device stays at the
last accepted value when the run ends. And its `max_writes` charges only
**increasing** writes, so it is a dose-raising budget, not a write count. §"Event
flow" step 1 and §"Safety and concurrency" should not be read as promising
illumination restoration; 65b must not authorize a dose on the assumption of it.

**65b — the vocabulary rename.** *(Split out of the original 65b by the
coordinator, 2026-08-31, with the operator's agreement. What follows replaces
"65b — the runner. Everything above.")*

Rename `ContinueSurvey` → `ContinueAcquisition` and `StopSurvey` →
`StopAcquisition` outright, per §"Decision: one axis-neutral decision
vocabulary". Measured at 104 references across twelve non-design files: the action
types and their dispatch in `hook_decisions.py`, `tools.py`, `tools_schema.py`,
`hook_manager.py`, the hook-authoring skill, the saved-hook fixture and six test
modules. `hook_docs` carries none — the built-in hooks drive `candidates` and
`progress` directly and never name a decision. Nothing is owed backward compatibility; there is no
alias.

The migration is the part that is not mechanical. A saved hook on a rig that
imports or constructs the old names must **refuse at resolution, before
acquisition, with a remedy that names the replacement** — not construct
successfully and die after the seed exposure. `_hook_contract_analysis`'s
decision-name scan already refuses a name it cannot resolve; what it must gain
is a message that says which name replaced which. Before 65c's gate, inspect
each rig's saved-hook registry for old-vocabulary source and migrate it.

**That message is the only part of this block an operator ever sees**, and it is
the part a rename leaves for last — 111 of the 112 edits are invisible to
everyone but us. Design it first, not after the sweep is green.

**Deliberately not in scope:** `SurveyProgress`, `run_adaptive_survey` and the
word "tile" in log and refusal strings. §"What is actually missing" lists names
as survey-specific item (3), but only the *decision vocabulary* is load-bearing
— renaming the tool is a public-surface change this document did not decide, and
`SurveyProgress` is internal. A rename block that widens into either is
over-reaching.

No rig gate: the mechanism is a rename with a full suite behind it, and the one
rig-facing limb — an old saved hook refusing with a useful remedy — is scored
inside 65c's gate session, where a registry is in front of an operator anyway.


### 65b's migration refusal, designed first

**Where.** `_hook_contract_analysis` (`hook_manager.py:107`). It is already the
resolve-time gate: `load_hook_class` runs it on the pinned source *before*
`exec_module` (`:354`), so a refusal there lands before the hook object exists,
which is before the seed exposure. No new call site, no second scan.

**Why the existing scan does not cover it.** After the rename the old names are
not "unresolvable decision names". `from microclaw.hook_decisions import
ContinueSurvey` *binds* `ContinueSurvey`, so the missing-name branch (`:200`)
stays silent and the module dies at `exec_module` with a bare `ImportError` that
names no replacement. Block 45's shape — `StopSurvey()` with no import line at
all — is worse: the name is no longer in `decision_names`, so nothing refuses and
the `NameError` arrives inside the image processor, after the exposure. Both are
what §"Decision: one axis-neutral decision vocabulary" forbids, and neither is
caught by leaving the scan as it is.

**The map** lives beside the classes it renames:
`hook_decisions._RETIRED_ACTION_NAMES = {"ContinueSurvey": "ContinueAcquisition",
"StopSurvey": "StopAcquisition"}`. Never in `_ACTION_TYPES`, never constructible,
no alias — it exists to refuse, not to accept.

**The trigger is any identifier occurrence**: an `ast.Name`, an `ast.Attribute`'s
`attr`, or an `ImportFrom` alias. Never a string constant. A hook that writes
`import ContinueAcquisition as ContinueSurvey` is refused too, and that is the
right answer for the reason the missing-name scan already gives at `:143` —
tracking scopes to permit a hook that shadows the decision vocabulary costs more
than it buys.

**The message**, once per retired name, in a stable order:

> `ContinueSurvey` was renamed to `ContinueAcquisition` and no longer exists;
> there is no alias. Replace every occurrence in this hook, including the
> import: `from microclaw.hook_decisions import ContinueAcquisition`.

It reaches an operator by two paths that are already built, and neither needs
extending. `load_hook_class` wraps it as *"violates the current hook contract:
[...]. Review and re-save corrected source before running it."*
`describe_saved_hook` lists it under `source_reasons` with the
`read_hook_from_file` → `generate_and_save_hook` remedy — the correct remedy
here, because the fix is an edit to the file. Leave `_SOURCE_REFUSAL_NOTE`
(`:433`) alone: its closing enumeration is illustrative, not exhaustive, and this
error carries its own remedy in its own sentence.

**Evidence for it is a mutation, not a watch-it-fail.** On the pre-rename tree
the retired names *are* the vocabulary, so the test cannot fail for the stated
reason. Delete `_RETIRED_ACTION_NAMES` from the renamed tree instead and confirm
the old-vocabulary fixture loads far enough to die late — an `ImportError` from
`exec_module` for the importing shape, and a clean load for the bare-call shape.

**Measured, both by the runner and independently by the coordinator**
(2026-08-31): with the map emptied, the importing shape raises
`ImportError: cannot import name 'ContinueSurvey' from 'microclaw.hook_decisions'`
out of `exec_module`, and the bare-call shape returns the loaded class with no
error at all. The four new tests fail under that mutation and pass without it.
One trap worth recording, because it inverted both results and looked like a
contradiction of the runner's report: an editable install resolves `microclaw`
to the **primary checkout**, so a probe script run from `/tmp` measured the
pre-rename tree. Run a probe from inside the worktree, and check
`microclaw.__file__` before believing it.
**65c — the runner.** Everything above §"Blocks": `max_frames` and the
successor-function route behind `run_timelapse`, the cap-derived
`AcquisitionPlan` with accounting separated from the runtime bound, the
three-form exporter, the thirteen tests, and the M2 gate. This is the block that
creates the single-field adaptive timelapse route, and until it merges the
sentence 65a shipped — *"do not offer to write an adaptive STORM hook until a
single-field adaptive timelapse route exists"* — is the truth.

After 65c merges, replace 65a's warning with a worked
`run_timelapse(hook_strategy=...)` example and make the hook-authoring
capability table list adaptive time series separately from fixed-plan
acquisitions and spatial surveys.

**Why the split.** The rename is mechanical and touches eleven files; the route
is where the defects will be. Bundled, the route's diff is unreadable underneath
the rename's, and a runner turn can be spent on the rename instead of the
dispatch rule. Split, the migration refusal also reaches rig registries one
block earlier, which is the half operators have to act on. The rename is
coherent alone because the name is *already* axis-neutral on the survey path —
it describes what `ContinueSurvey` does today, not only what the new route will
need.

This document changes no code, generated hook, rig configuration or
authorization policy. It names the missing dispatch rule, the two decisions
that rule forces, and the documentation correction that makes the user-facing
promise match the capability.

## Run ledger

design/65 is coordinated and owns its own blocks and ledger, like design/48
through design/60. `design/35-usability-and-pfs-checklist.md` points here and
does not track these rows.

| block | branch | start | implementation | gate | merge |
| --- | --- | --- | --- | --- | --- |
| 65a | `design65/smlm-skill-accuracy` | `c0629c1` (2026-08-31) | `5a2faad` + `eb9b6ed` (review round 1, four findings) + `5d87df1` (review round 2/3); coordinator `163023f` (ledger move) and the design correction above. Suite **2618 passed / 99 skipped / 3 warnings**, coordinator-run, baseline + 1; 25 test assertions checked individually against all four commits, every one discriminates | **none — no rig surface.** The change is to a shipped documentation file, and what it must not do is *promise* a capability, which is checkable by reading. A rig limb here would be a driven session that asks for adaptive density control and is told the truth — worth folding into the runner block's gate session, not worth a trip of its own |`c92f035` merged 2026-08-31, branch deleted local and origin; design reconciliation `6ebc931` in the same merge |
| 65b | `design65/decision-vocabulary-rename` | `5406e0f` (2026-08-31) | `60548e6` (runner, one turn, no revision round) + coordinator `890574c`. Suite **2622 passed / 99 skipped / 3 warnings**, coordinator-run on a clean tree, baseline + 4; the migration refusal's mutation reproduced independently | **none — see the block.** The rename carries the full suite; its one rig-facing limb (an old saved hook refusing with a remedy that names the replacement) is scored inside 65c's gate session | `3eac311` merged 2026-08-31, branch deleted local and origin; design reconciliation `32846bb` in the same merge |
| 65c | `design65/adaptive-timelapse-route` | `cad15b9` (2026-08-31) | **In flight, unreviewed by its runner.** `96b3b72` + `01beb5f` (runner turn 1) were committed by Codex and the turn was then killed by a Codex usage limit before it ran the suite or wrote a report, so there is no runner evidence for any of it. Coordinator-run suite on that tree: **2639 passed / 99 skipped / 3 warnings**, baseline + 17 — green, and carrying a confirmed defect (the successor replaces the seed's whole `axes` dict, so a channelled run's frames 1..N drop the `channel` axis; test 9 missed it because its fake supplied the successor). Review round 1 returned six findings and four test defects | **M2 required** — §"Rig gate": three operator-judged limbs (successor dispatch and early stop with no hardware capability granted; an image-derived write to a non-dosing property; a short authorized `Duration0` run on a frame-index predicate). Everything that only computes goes in one script, run against `design/55-gate-probe-selftest.py`'s bridge-shaped fake before it ships | |

**What 65a's three review rounds are worth keeping.** Round 1 returned four
findings and the runner had scoped two of them out as "manual scientific
choices": `:50-53`'s *"increase pulse length gradually; stop increasing when
maximum pulse length is reached"* and `:111`'s *"acquire until 405 nm pulse
length maxes out"*. Both are Amr's loop written as imperatives, in a file loaded
into an **agent's** context, directly above `Use: run_timelapse(n_frames=...)`.
The fix was attribution, not deletion — the science is right, the actor was
wrong. Round 1 also **deleted true guidance that was not the defect** (the
nonzero-interval rationale), which is the standing hazard of a correction block.

**And the reviewer's own finding was half wrong, caught by the runner.** Round 2
asserted illumination feedback is bounded by "ceiling, write budget and restore
policy"; there is no restore policy, and the runner stopped without editing
rather than write an unshipped capability into a shipped file. That is the
defect this block exists to fix, committed by its reviewer — and the second time
in one block that checking an instruction against the code beat following it.
The substance survived; only the bounding clause was wrong.
