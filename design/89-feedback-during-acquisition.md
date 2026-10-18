# Feedback during a running acquisition — how fast, and by which route

**Status:** proposal, revised 2026-10-15. No block opened. Owns `R139`. Depends on
[design/87](87-package-metrics.md) for its input. Its sibling for the
htSMLM/MDA workflow is [design/88](88-mda-and-micro-manager-plugins.md).

## The question

A measurement taken during an acquisition (SMAPpy's localizations per frame,
here) should change hardware during that same acquisition (the 405 activation)
and possibly stop it. Three questions:

- Can the change be made **per frame**?
- Would **modifying pycro-manager** make that possible?
- Is there **another way to make feedback faster**, building on design/78 and
  design/79?

**The constraint that decides most of it** (operator, 2026-10-14): an SMLM
acquisition relies on consistent blinking kinetics, with frame rate and
activation pulses matched to them, and **post-acquisition drift correction
assumes a constant frame interval**. No route may put pauses into the frame
stream.

## What the engine does, read from source

These are the facts the answers rest on. 1–3 are already engine contracts in
`CLAUDE.md`. 4 and 5 are new, read on 2026-10-15 with `javap` from
`AcqEngJ-0.39.4.jar` (Micro-Manager 2.0.3-20260625), and **not yet observed on
a running engine**.

1. **A burst has no software between exposures.** When consecutive events are
   sequencable, AcqEngJ hands the whole batch to the hook once and the camera
   runs on its own clock (contract 1).
2. **Frames batch exactly when their truncated millisecond deadlines are
   equal** (contract 1, design/78). At `interval_s = 0` there are no
   deadlines, and the whole run is one burst.
3. **Software pacing costs frame rate and is unstable.** On M2 at 50 ms
   exposure (design/79, n=1 per arm): a burst ran at the exposure; a 60 ms
   request came back at a 0.0710 s mean in one round and 0.2169 s in another,
   unattributed (`R105`). Per-acquisition overhead is 0.26 s on M2 and 0.28 s
   on the demo machine, so one acquisition per frame is not a route either.
4. **Hardware property sequencing exists, but only for channel presets.**
   `Engine.isSequencable` lets a batch continue when a **config preset's**
   differing properties are `isPropertySequenceable` and long enough. The
   engine then calls `loadPropertySequence`, and the device steps through the
   values on each trigger, with no software involved. That is how a value
   really changes per frame inside a burst. It needs the values **before** the
   sequence starts.
5. **An event's own `properties` are silently not applied inside a burst.**
   `isSequencable` never reads them, so differing values do not stop
   batching. The batch is executed through `mergeSequenceEvent` →
   `AcquisitionEvent(List)`, whose `properties_` is a fresh empty set never
   filled from the sequence. `prepareHardware` → `Engine$6.run` applies only
   that merged event's `getAdditonalProperties()`. pycro-manager's Python
   backend says the same in its own source (`engine.py`, `# TODO check for
   arbitrary additional properties in the acq event`). MicroClaw does not put
   per-frame `properties` in events today. This is the trap a "just put the
   value in the event" design would walk into. Observe it on the demo machine
   (P3) before writing it into `CLAUDE.md`.

## The answers

### Per frame, inside a burst? Only in hardware, and only for values known in advance

Per-frame software action inside a burst does not exist (fact 1). The only
per-frame change inside a burst is device-side sequencing (fact 4), and it
needs the whole value list before the burst starts. A feedback value is
computed from frames the burst has not taken yet, so it cannot be preloaded.
Reloading a property sequence means stopping the camera sequence, which is a
pause, and that is ruled out.

### Modify pycro-manager? No, for feedback

Nothing in pycro-manager or AcqEngJ sits between two frames of a camera
sequence. The camera streams into the circular buffer and the engine is not
called per frame, so no hook placement, Java-side or Python-side, can run
there. A change would only buy fact 4 for arbitrary properties, which is the
upstream `TODO`. That helps **predetermined** per-frame values, such as a
power ramp written before the run, and does nothing for feedback. It is a
reasonable upstream contribution if a predetermined-sequence workflow appears,
and is not in this notebook. Fact 5 is worth reporting upstream regardless:
silently dropping a value is worse than refusing it.

### Batching? No

Splitting the run into back-to-back batches (frame `k` gets deadline
`floor(k / K) × 1 ms`) gives a hook a slot before each batch. That slot is
exact and needs no concurrent writer. But every boundary is a camera-sequence
restart, which is a pause of unmeasured, camera-dependent length, and that
breaks the constant interval. Recorded so it is not re-proposed for SMLM. It
may still suit a workflow without that requirement.

### The route: write concurrently, while the burst runs

A controller thread writes the activation while the camera sequence runs. The
device applies the new value at its next trigger edge. For an FPGA-timed
pulse, such as M5's `Laser Trigger-Duration0`, that is one frame boundary.
Which frame cannot be predicted, and does not need to be: activation is a slow
control, and htSMLM, the lab's accepted standard, does exactly this from a Java
thread at about 10 Hz during MMStudio's MDA. design/78 drew the same conclusion
after reading htSMLM: *"Java is not the explanation for the speed difference,
the barrier is."*

Two facts narrow the risk:

- pycro-manager runs the AcqEngJ acquisition on **its own socket**
  (`java_backend_acquisitions.py:480-485`), so MicroClaw's bridge lock does
  not queue writes behind the engine's polling;
- a mid-sequence activation write is routine on the lab's EMU rigs.

Its effect on cadence on our path is unmeasured, which is P1.

### Where feedback speed actually goes

| stage | measured | source |
|---|---|---|
| frame written → localizations available | 0.04–0.08 s median, 0.17 s max | SMAPpy, n=1 machine (spec §5.6) |
| metric line → MicroClaw | one pipe read | design/87 |
| decision → write applied | setter 41–97 µs, plus one bridge round trip | design/78a, M2 |
| GUI/EMU refresh per write | **must stay out**: about 2.3 s on M2 when present | design/78a |
| htSMLM's own loop, for comparison | 100 ms idle + a 2-frame grab + NMS, per iteration | htSMLM `30b6bfd` |

The analysis dominates. The write is about four orders of magnitude cheaper
than the analysis, provided it carries no GUI refresh (design/78a's lesson,
which applies to this writer exactly as to the hook path). So the levers for
**faster feedback** are:

1. **No refresh, no read-back fan-out on the write.** The controller writes
   with the 78a-shaped path: guard, write, and at most one targeted read-back.
   Refresh once, at teardown, as the operator decided on 2026-09-06.
2. **Apply the law as counts arrive**, per frame, not per message, so batching
   on the worker side changes nothing (spec D5).
3. **Bound the write rate, not the decision rate.** `write_interval_s`
   (default 0.2 s) limits write attempts, including failed attempts. The law
   still sees every frame.
4. **Tie each write to frames cheaply.** Record, with every write, the last
   frame in MicroClaw's latest index read beside `perf_counter`/UTC. Together with the dataset's own
   timestamps (`design/77`), that bounds which frame the new value first
   reached.

For **software-paced** workflows that genuinely need a value before frame k
(not SMLM, for example a focus correction), the remaining per-frame cost is
design/79's unattributed residual. The way to speed those up is to attribute
it first: `R105` (several short-interval runs on a quiet M2) and `R144` (split
the acquisition phase at engine milestones). That is separate work, recorded
here so it is not confused with this notebook's route.

## Decisions

### F1 — `analysis_feedback`, an argument beside `analysis`

V1 provides **package-independent activation-density control plus generic
metric-based stopping**, on hookless `run_timelapse`. Activation control uses
the positive-response law below: increasing the actuator is expected to
increase the measured emitter density. It is not an arbitrary-property control
algorithm. Any admitted protocol-1.2 operation supplying suitable declared
metrics may be selected; no package identity, metric-name convention or
SMAPpy dependency selects behavior. The following names are examples only.

```json
"analysis_feedback": {
  "control": {
    "metric": "localizations_per_frame",
    "envelope": {"device": "Laser Trigger", "property": "Duration0 (us)", "min": 0, "max": 10000, "max_writes": 20000, "restore": "leave"},
    "setpoint": 5.0,
    "max_change_per_s": 50.0
  },
  "stop": {"metric": "localizations", "at_least": 2000000}
}
```

- **`control.envelope`** is either an `illumination_envelope` (power) or a
  numeric `property_envelope` for an activation actuator (pulse length;
  M5's activation knob is
  `Duration0`, not power). It is validated by the same code as the hook
  arguments. Numeric feedback requires `restore: "leave"`; other restoration
  policies are refused because F3 allows no teardown writes. On an htSMLM rig,
  the shared EMU reader extension (design/88 E3 / 88c) supplies the configured
  mapping and proposed ceiling, with source/live-verification status. This
  reader is a prerequisite only when htSMLM configuration is used as the
  mapping source, and can land independently of 88c/88d. An explicit validated
  envelope on another rig requires neither EMU nor htSMLM.
  Preserve existing budgets: illumination counts accepted increasing writes;
  numeric properties count attempted writes, including decreases and failures.
- Required: at least one of `control`/`stop`. **Control names its
  input** in `control.metric`, which must be a `per_frame` metric the
  selected operation declares. MicroClaw does not interpret it: the agent
  takes the name from the package's skill text, and recommending the right
  one is the package's responsibility (operator decision, 2026-10-15). Safety
  comes from the envelope, budget, ratchet and freshness checks, not from
  understanding the metric. `control.setpoint` is required and positive.
  **`control.max_change_per_s`** (renamed from the spec's "gain", which reads
  as a camera gain; operator decision, 2026-10-15) is how fast the property may
  move between submitted commands, in the property's own units per second,
  in either direction. Per-frame integration proposes a target; a separate
  monotonic-time limiter bounds actual command changes, including catch-up
  batches. The law proposes its maximum positive step at zero, its maximum
  negative step at twice the setpoint or more, and no step at the setpoint.
  **Default: the envelope's full range in 60 s**,
  `(ceiling − floor) / 60`. The right value
  depends on sample and dye, so the package's skill text or the operator may
  override it; the envelope bounds a poor choice either way, and a slow default
  errs towards under- rather than over-activation. The law assumes the metric
  **rises** when the property rises, as emitter density does with activation;
  the parameter description says so. An inverse-response metric is not in v1.
  The names are generic (`control`, `setpoint`, `max_change_per_s`): nothing in
  the mechanism knows about activation, only v1's scope and its docs do.
  Stop-only rules require only their own selected metric and threshold, with
  no control metric, envelope or setpoint. For example:
  `{"stop": {"metric": "objects_total", "at_least": 10000}}` works with an
  operation declaring only that counter; a gauge-only stop is also valid.
- Control defaults: `max_change_per_s` as above, floor above 0, start = current value,
  `write_interval_s` 0.2 (floor 0.05), `min_change` one resolution step.
  Both modes use `max_age_s` 3.0 and `max_frames_behind` 500, enforced from
  MicroClaw's own NDTiff index read and each selected metric's last ordinal
  (F3). These are experimental freshness requirements, not package timing
  estimates. Stop-only mode performs no actuator discovery or hardware reads.
- Publisher backlog gauges are ordinary telemetry in v1, not implicit control
  inputs. Do not recognize `frames_behind` or any other name specially. A
  future additional backlog check must explicitly select its declared metric,
  units and threshold; the independent index check is sufficient for v1.
- **Nominal frame period:** derive
  `nominal_frame_period_s = max(interval_s, exposure_ms / 1000)` from the
  validated acquisition plan. When the call gives no `exposure_ms`, the run
  uses the camera's current exposure, so the period uses that value, read
  through the guard as text. Thus `interval_s=0` means an exposure-based
  nominal period, not a zero control step. Require a finite positive period
  for control; stop-only rules need none. Camera readout, triggering and other
  overhead may make actual cadence differ. Where readout outlasts the
  exposure (for example 1 ms exposure, 10 ms readout), real frames are much
  slower than the nominal period, so the property moves correspondingly slower
  than `max_change_per_s`. That is the safe direction, so v1 keeps the rule
  and the consent summary says the stated rate is a maximum. This period
  scales integration, not the wall-clock command limit; record its value and
  derivation in the result. No package timing estimate is used. **Later
  refinement, not v1:** use MicroClaw's own measured frame period from the
  index timestamps it already reads. That measures our acquisition, not a
  package, so it would respect the rule in F3.
- **Law:** spec D3's additive shape, per frame in frame order:
  `a ← clamp(a + step·clamp(1 − N/N_s, −1, 1), floor, ceiling)`, with
  `step = max_change_per_s × nominal_frame_period_s`. **Each frame's error term
  is clamped to ±1** (operator decision, 2026-10-15), so the proposed target
  changes by at most one step per frame in either direction. A single crowded
  frame at ten times the setpoint proposes a decrease of one step, not nine.
  For the same ordered samples, integration is independent of message batching;
  actual writes also depend on arrival time and the limiter below.
- **Command rate limit:** immediately before each write, limit the change from
  the last established actuator value to
  `max_change_per_s × min(elapsed_monotonic_s, write_interval_s)`, in either
  direction. Measure elapsed time since the preceding successful write's
  submission; initialize that timestamp when write authority is armed. **The
  elapsed time is capped at `write_interval_s`** (operator agreement,
  2026-10-15). Without the cap, a long gap with no writes (for example a
  stalled package whose evidence went stale) would let the first write after
  recovery jump by the whole gap's worth at once, which is exactly the catch-up
  case this limit exists for. With it, a large backlog is approached over
  several writes at the stated rate. Check authority, freshness, envelope
  and budget as usual. Apply resolution rounding towards the established value
  so it cannot exceed the limit; hold if the permitted change is below
  `min_change`. Catch-up batches and faster-than-planned cameras cannot bypass
  this limit. This bounds commanded changes between submissions, not the
  device's physical transition speed. After a failed/uncertain write, establish
  the actual baseline through F2 and reset the limiter timestamp before a later
  write. F3's stale-data rule still discards pending upward targets.

  **Operator decision, open, informed by the lab's htSMLM practice:** the law is MicroClaw's and
  applies to every package, so it is not a publisher's to choose. htSMLM's
  law is multiplicative, `dp += 0.1 + p·feedback·(1 − N/N0); p += dp`; the
  spec's is the additive one above. Settle before 89b.
- **`stop`:** `at_least`/`at_most` on any declared metric. **Exhaustion**
  ("at the ceiling for N s", htSMLM's stop) is offered if the operator wants
  parity.
- **Scope, v1:** hookless `run_timelapse`. Refused on every other tool, on
  `run_timelapse` with a hook, and on `run_mda`. This scope rule is independent
  of which package is selected or whether htSMLM is present.

Validate `analysis_feedback` independently of the presence of `analysis`,
before hardware moves or an `Acquisition` is constructed. Refuse when:

- `analysis` is absent;
- neither `control` nor `stop` is supplied;
- when `control` is supplied, `control.metric` is not a `per_frame` metric
  the operation declares;
- when `stop` is supplied, its metric is undeclared or its threshold/comparator
  is invalid;
- for `control`: the envelope fails shared validation, numeric restoration is
  not `leave`, `setpoint` is missing or non-positive, `max_change_per_s` is
  non-positive, the floor is outside
  `[0, ceiling]`, the nominal frame period is not finite and positive, or
  `write_interval_s` is below 0.05;
- a same-property hook envelope is also given;
- the release is not protocol `1.2`.

Add a registry-parametrized refusal case, including `protocol_params`, with no
`Acquisition` constructed. It is not a hook capability; whether it joins
`HOOK_CAPABILITY_ARGS` is decided after reading every site that iterates that
tuple.

### F2 — one actuator path, shared with hooks

The hook adapter's illumination write block
(`hook_decisions.py:~990-1062`: ceiling, increase budget,
`check_illumination(previous_percent=…)`, baseline re-read after a failure) and
its numeric-property counterpart are **extracted, never copied**. Both callers
hold the result. A bridge value is converted by the guard from text, never with
`float()`.

**The two paths handle a refusal differently today, and the extraction must not
carry that difference into the controller** (operator agreement, 2026-10-15).
The illumination path records a refusal and returns (`hook_decisions.py:~1029`).
The numeric property path, the one `Duration0` uses, **raises**
`RuntimeError("property action refused: …")` on an exhausted budget, an
out-of-range value or a guard refusal (`:500-521`). In a hook that is intended,
because it stops the run. In the controller it would abort a long SMLM
acquisition at the moment its budget runs out, when F3 says a refusal is a
hold. So the extracted object **reports** a refusal as a result. Each caller
preserves its intended behavior:

- Numeric-property hooks keep raising on refusal, stopping the run.
- Illumination hooks keep recording the refusal and returning.
- The feedback controller records either kind of refusal as a hold and
  continues acquisition.

Tests cover all three cases, including budget exhaustion part-way through a
run; extraction must not make illumination-hook refusals start raising.

### F3 — the controller's lifetime

Request a consumer queue before worker launch (87 M1) when a selected rule
uses per-frame values; gauge/counter-only stopping needs no per-frame queue.
Start the controller
after `AcquisitionAnalysisJob` binding and before `acquire`, initially disarmed.
Arm writes only once acquisition imaging starts. One shutdown path covers:

- imaging complete, using the engine's supported acquisition-finished milestone,
  with the planned final saved frame as an additional fixed-plan signal;
- user cancellation, acquisition/controller failure or failed startup;
- design/60's bound or the feedback stop rule;
- teardown return as a final fallback, not the normal end of write authority.

Do not use camera-idle polling as the sole completion signal: between bursts
or before startup it does not establish acquisition completion. P2 verifies the
notification's ordering relative to the final exposure and writer teardown.

Every write rechecks authority under the same synchronization used to revoke
it. Revoke first, discard pending proposals, and join the controller before
successful cleanup/results. A write already submitted to a blocked bridge
cannot be recalled: report it as in flight, retain session refusal and bounded
watchdog behavior, and do not claim shutdown until it returns. Cover failure
before waiter construction too. No final write, restoration or `end_percent`
(design/32 7b); writes must not continue through writer-only teardown.

**Freshness is per metric, measured against the index, with no tolerance**
(operator decision, 2026-10-15). MicroClaw reads the dataset's NDTiff index
itself, the same file a disk-following worker reads, at a bounded cadence.
Keep a dataset-wide high-water count `known`, initially zero. Update it with
`known = max(known, index_count)` on each successful index refresh and
`known = max(known, metric_last + 1)` for every accepted metric, across all
metrics. A metric ahead of MicroClaw's last index read is not itself a fault;
its ordinal also establishes a lower bound for checking slower metrics.
The frame lag is `known − 1 − metric_last`, checked against
`max_frames_behind`, alongside that metric's monotonic reception age against
`max_age_s`. No first sample, a dead feed or lost frames make evidence unusable.

**A claimed frame that the index does not hold is a protocol fault**
(operator agreement, 2026-10-15). A disk-following worker can only report
frames that exist in the index. So if an index read that **started after** a
message was received still holds fewer than `metric_last + 1` frames, the
package has claimed frames that do not exist. The check is ordered in time,
not a timing allowance, so it introduces no tolerance. That feed is marked
unusable for the rest of the run and recorded. Until such a later read
confirms an ordinal, the ordinal may advance `known` for checking other
metrics, but once refuted it is withdrawn: `known` is recomputed from the
index and the confirmed ordinals only. One wrong frame number therefore cannot
silently hold the loop for the rest of the run.

**The index is read incrementally** (operator agreement, 2026-10-15). Each
refresh parses only the entries appended since the previous read's offset, so
a read costs the same at frame 50,000 as at frame 50. A partly written entry
at the end is left for the next read and never counted. P4 measures this
reader, and 89b ships the reader P4 measured.

Increases and feedback stops additionally require a recent, successful index
refresh. Reuse `max_age_s` as the maximum index snapshot age, measured
conservatively from the read's monotonic start time. Missing, failed, blocked
or excessively old refreshes hold increases and stops until a successful,
still-recent refresh restores that evidence. Incoming metric messages can
advance `known`, but never refresh the index timestamp or clear an index-reader
failure. A high-water count alone cannot prove that analysis is keeping up
with the dataset. Decreases remain subject to F3's active write authority.

**MicroClaw never tunes its own timing to a package's.** A community package
can change its timing with any release, so there is no tolerance, per-machine
calibration or fallback threshold anywhere in this check. The pycro-manager
saved-frame count is not used for freshness, because it can lag the disk at
high frame rates and freezes if notifications stop. Two numbers remain, both
the experiment's requirements on evidence, not estimates of a package:
`max_frames_behind` and `max_age_s`. If P4 shows the index reads disturb the
acquisition, 89b stops and the operator decides; it does not ship a tuned
substitute. Use that metric's own receipt time/range; sparse
messages do not refresh omitted metrics. Every stop operand needs its own
freshness check. A fresh density sample cannot refresh an old stop counter.
For a per-frame stop metric, test individual samples in ordinal order, never
compare its list as a scalar. Unselected metrics and their names do not add
implicit stop or backlog rules.

**A hold says why, in terms a publisher can act on** (operator decision,
2026-10-15). Every hold recorded for staleness names the metric, its frame lag
and age, and the limits it missed (`max_frames_behind`, `max_age_s`), so a
package that sends its metric too rarely is identifiable from the record. The
same requirement is in the publisher guide (design/87, 87a).

Stale evidence holds increases and never fires a stop. Decreases remain allowed
while write authority is active. Suppress positive increments in the law itself
when stale, discard any pending upward target, and rebase on the last verified
actuator value so suppressed increases cannot accumulate and jump on recovery.
A dropped-frame latch disables increases/stops for the remainder of this run;
never replay lost evidence. On write refusal/failure, re-establish the baseline
through F2 before a later increase. Record these transitions explicitly.

### F4 — record

Each decision goes to `feedback.jsonl` in the job's output directory, written
by MicroClaw:

- for a write: `perf_counter` and UTC time, the latest index read (count and age), the
  frame range the decision answered, the metric value and its age, the
  integrated target, elapsed limiter time, and the old and submitted new value;
- for a hold or a refusal: its reason, per-metric receipt age and frame lag,
  dataset-wide high-water count, and index snapshot age/reader health;
- shutdown reason, discarded proposals and any write still in flight.

The result carries `analysis_feedback: {writes, increases, holds_stale,
refusals, final_value, stop_reason, stop_value}`, and write time joins
`duration_breakdown`.

### F5 — clean stop

The data is kept, `stop_reason: "analysis_rule"` is set, the analysis job gets
its ordinary notifications, and the result has a successful run's shape. The
mechanism is pycro-manager's public
[`Acquisition.abort()`](https://pycro-manager.readthedocs.io/en/latest/apis.html#pycromanager.acquisition.acquisition_superclass.Acquisition.abort)
on the acquisition object MicroClaw created, pending P2. Its implementation clears pending Python
events and calls that acquisition's Java `abort()`; it does not stop Studio's
MDA (design/88 uses `abortAcquisition()`). Revoke feedback writes before
requesting abort, then await bounded writer finalization. Report actual saved
frames and stop overshoot. A teardown failure still reports failure or
unterminated state rather than a successful stop. If P2 fails, drop the stop
rule from v1 and refuse stop-only calls before acquisition construction.

### F6 — consent

When `control` is requested, use a **separate, subject-less
`kind="illumination"` confirmation** right after the analysis one. An
`analysis` session grant is per package digest and must not authorize unattended
activation control. A stop-only rule needs no illumination confirmation or
actuator fields. Its metric, threshold, freshness requirements and stop
behaviour are **always** reported to the acquisition event sink at start and
in the result, and added to the analysis confirmation's text when that prompt
is shown (it may not be, under a session grant, and `run_timelapse` raises an
acquisition confirmation only above its thresholds). No new prompt: stopping
is not a hardware write (operator decision, 2026-10-15). Whether anyone reads
disclosure text at all is a separate, wider problem, to be addressed later
(`design/70` row to open).

For `control`, the summary states:

- property, ceiling, floor and start value, and their source;
- the setpoint, and one line for the rate, for example "Duration0 moves at
  most 166 µs/s (default: full range in 60 s), assuming about 66 frames/s from
  a 15 ms exposure; real response can be slower if the camera runs slower".
  The per-frame step, nominal-period derivation and limiter details go in the
  result and `feedback.jsonl` (F4), not the prompt (operator agreement,
  2026-10-15);
- the budget's actual semantics: accepted increases for illumination, attempted
  writes for numeric properties. With an immediate first attempt,
  `(max_writes − 1) × write_interval_s` is the earliest exhaustion time at the
  maximum attempt rate, not a guaranteed run duration; holds, decreases and
  failures affect the two budgets differently;
- the stop rule;
- that writes land at an unspecified frame and are timestamped for alignment;
- if the property is htSMLM's activation property, that htSMLM must not be
  controlling it: its activation acts only with Activate ticked and its script
  running (Run), so either must be off;
- that if the controller's queue ever drops frames (F3's latch), activation
  can only fall for the rest of the run, and the stop rule no longer fires.

### F7 — export: warn and continue

Operator decision, 2026-10-15, revising the same day's `CannotEmit` choice:
**the exported script runs the acquisition without the feedback loop, warns,
and continues.** For the motivating STORM experiment, UV activation raises
emitter density per frame; imaging remains useful without that contribution,
with fewer localizations. The operator prefers an acquisition and subsequent
steps to continue with an explicit warning when activation feedback cannot be
exported. This is an accepted experimental difference, not behavioral parity.

- **The emitted acquisition** is the call's ordinary hookless emitter, with no
  activation writes and no early stop.
- **The warning** is printed in the envelope output before the acquisition
  starts and again when it ends, and carried as a comment:
  "analysis feedback NOT reproduced: the requested feedback rule is absent;
  the planned run is `<n>` frames." When `control` was requested, add:
  "`<device>.<property>` is not controlled by this script; subsequent steps may
  see a different property value." When stopping was requested, add:
  "stop rule `<metric> <comparator> <threshold>` is absent." Render only the
  requested parts, including for stop-only rules; never assume localization
  counts or UV hardware. The UV/localization explanation above describes the
  motivating experiment. The warning makes no claim that a property remains
  constant or is zero: starting hardware state and other clients can affect it.
- **The record's summary** (writes, final value, stop reason) goes in the
  comment, so the reader can see what the original run did.
- **Package analysis** keeps its existing `# Package analysis not
  reproduced` lines.

**This adds a refusal kind: warn and continue.** Today every refusal emits
`# NOT EMITTED` and a `raise RuntimeError`, because a skipped step that changed
state would let later steps run in a state the session never had (`CLAUDE.md`:
"a plausible fabrication of a step is the defect"). That stays the default.
This route is an explicit operator-approved exception for omitted activation
feedback and early stop. Feedback writes cease at run end, but the hardware
retains its last value because F3 performs no restoration; later acquisitions
can therefore start from a different activation value on export. Dataset
length and elapsed time can also differ when the stop rule is omitted. These
differences are disclosed and accepted for this route, rather than assumed
absent. Each use is declared per tool route and decided like an export marker,
never swept. 89b records the exception in `CLAUDE.md`'s export section and tests
that the warning appears, the open-loop acquisition runs, and a subsequent
acquisition using the same activation property continues without an invented
activation write or restoration.

Verify package independence with two fixture packages using unrelated package
and metric names: activation control must work with whichever declared
per-frame metric the call names, refuse an undeclared or non-per-frame name,
and a gauge-only operation must support stopping without any
per-frame declaration or actuator access. Renaming the declarations and caller
references must not change behavior. Run the export cases for control-only,
stop-only and combined rules, with no missing-field errors or SMAPpy-specific
warning text.

A later design may make the worker, law, bounds and early stop runnable in a
standalone script. Until then the warning is the disclosure, not completion.

## Probes — before 89b

These are standalone scripts (`design/89-probe-*.py`), each run against a
bridge-shaped fake first.

- **P1 — does a concurrent write change the cadence?** A 5,000-frame, 10 ms
  burst while a thread writes the activation property at 1 Hz and at 5 Hz,
  against no writes. Report the interval distribution **from the dataset's own
  timestamps**, dropped frames, write failures and write span. Run several
  runs per arm: `R105` saw a 3× spread on M2. **Acceptance:** the
  interval distribution with writes is the same as without, within the
  measured run-to-run spread. Demo machine, then **M2** with the real
  activation property.
- **P2 — stopping.** `abort()` mid-burst from a second thread: is the NDTiff
  readable, do frames written match the index, how long is teardown, and does
  `__exit__` raise? Repeat. Verify acquisition-finished notification ordering,
  final-exposure versus writer-teardown timing, stop overshoot and no new writes
  after feedback revocation. Fake tests cover early failure, user cancel,
  teardown failure, blocked writes and the revoke/write race.
- **P4 — spike: what does reading the index during a burst cost?** F3
  measures freshness against MicroClaw's own reads of the NDTiff index. On the
  demo machine, run bursts at the fastest exposures the camera allows, with
  MicroClaw reading the growing index at several cadences (for example 2, 5
  and 10 Hz) and with no reads, and with a fixture worker following the same
  dataset. Report:
  - the frame-interval distribution from the dataset's own timestamps, per
    arm, over several runs;
  - the index read's own duration and its age when used, for the incremental
    reader F3 specifies (constant per read as the run grows), with an early
    and a late window of each run compared;
  - how far the worker's last ordinal and the pycro-manager saved-frame count
    each sit from the index over time (the second is a record of why the count
    is not used, not an input).

  Include a worker deliberately behind the index, to show F3 catches stale
  evidence. Against fakes, stall or fail the index reader while recent metric
  messages continue: increases and stops must hold, and metric messages must
  not restore index-reader health. Verify recovery after a fresh successful
  read, and two metrics progressing at different speeds: the faster metric
  must advance the dataset-wide high-water count used to check the slower one.
  A worker that claims an ordinal the index never reaches must be marked
  unusable by the first index read that starts after its message, and its
  ordinal withdrawn from the high-water count. A partly written final index
  entry must not be counted.
  **Acceptance:** at the chosen cadence, the interval distribution
  matches the no-read arm within the measured run-to-run spread. If no cadence
  passes, 89b stops for the operator's decision; no tuned value replaces it.
  Repeat on M2 with P1.
- **P3 — fact 5 on a running engine.** On the demo machine, a 0-interval run
  whose events carry a differing value of a demo device property per frame.
  Read the CoreLog for `setProperty` calls and the final value. This confirms
  or refutes the silent drop before it becomes a `CLAUDE.md` contract.
- **Rig fact, not code:** may the activation change while it emits? This goes
  in the rig profile.

P1 on M2 can carry `R142` if SMAPpy runs beside it. P3 is independent of 87a,
88 and feedback implementation; run it early if convenient.

## Blocks

| block | content | where it is proved |
| --- | --- | --- |
| **89a** | P1, P2, P3, P4. No product code. Fact 5 into `CLAUDE.md`'s engine contracts if P3 confirms it. | demo machine, then M2 (P1) |
| **89b** | F1–F7, after 87a, 89a and the shared EMU reader extension when used. Settle the law before implementation. Spec §6 tests 4–9 adapted to warn-and-continue export (test 7: the script compiles, runs the acquisition open-loop, prints the warning and continues to the next step) and no final write, plus stale backlog, stalled/failed index reads with continuing metrics, dataset-wide high-water tracking, a refuted ordinal withdrawn from it, incremental index reading including a partial final entry, sparse stop metrics, no windup, the per-frame error clamp (a frame at 10× the setpoint proposes exactly one downward step), positive exposure-based integration for zero-interval bursts, command-rate limits under faster cameras and catch-up batches (including a long stale gap followed by a backlog: no single write exceeds `max_change_per_s × write_interval_s`), exposure taken from the camera when `exposure_ms` is omitted, first-write timing, failed-write baseline reset and resolution rounding, all three callers' refusal behavior, budget semantics and all shutdown paths, with a fake clock and bridge-shaped core fake. | demo gate: a fixture worker sends scripted counts, scored offline against the law from `feedback.jsonl`. M2 gate: SMAPpy live, activation controlled, interval distribution on vs off |

**Whether 89b is wanted** after design/88 delivers the htSMLM workflow (htSMLM
controls the 405, SMAPpy monitors) is the operator's call at that point. 89a's
P3 is worth running regardless.

## Register

`R139` closes with 89b's M2 gate, or is re-scoped if 89b is not wanted.
Facts 4–5 answer part of design/78's 78c checks. pycro-manager frames never
reaching Studio's pipeline is design/88's finding.

## Run ledger

| Block | Branch | Start commit | Status |
|-------|--------|--------------|--------|
| notebook | — | `2a6f7ee` | drafted and revised 2026-10-15, not opened |
| 89a | `block-89a` | `860daac` | opened 2026-10-18; R157 guard decided (interleaved controls); probes at `90fdf18`, PR #60; awaiting demo run |
