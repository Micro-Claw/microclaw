# Choose the fastest correct execution path

Status: **PROPOSED**, 2026-09-06; reconciled against `design/78`'s merged blocks
2026-09-08. The policy text is in `agent.py`'s system prompt and `CLAUDE.md`,
and **both are now committed on `main`** — the original note that they were
uncommitted is stale. Neither has been measured: no block of *this* notebook has
run. What has changed is that design/78 shipped, and two of the premises below
moved with it — see "What design/78 already did" immediately after this.

**Superseded 2026-09-08: block 79a has run and merged** (`fc8e2b7`). The sentence above — "no block of *this* notebook has run" — is stale, and so is 79a's entry in that list. See "Block 79a, closed" before the run ledger. **79b is assigned 2026-09-08** on `design79/performance-aware-planning` and merged 2026-09-09. **79c-1 is assigned 2026-09-10** on `design79/the-per-field-multiplier` — see "Block 79c-1 as assigned" before the run ledger.

`CLAUDE.md`'s paragraph is the rule of record; this notebook owns the detail and
the evidence. Keep them from drifting: a change here that alters the rule must
change that paragraph in the same commit.

## What design/78 already did, and what it leaves this notebook

Read this before planning 79a; two of its premises are out of date.

- **79a's central complaint is half-fixed.** The text below says the hook log's
  per-write record "carries **no timestamp at all**". That was true when it was
  written and is **false on `main` since block 78a** (merged `abfc527`,
  2026-09-08): `UntrustedHookAdapter._apply_property` now records monotonic
  `validation` / `write` / `wait` / `read_back` spans on that exact record,
  bounded by the write budget, with `clock: "time.monotonic"`. 79a's job is to
  **generalize and verify** that, not to build it — and to do the rest of its
  list, which is untouched: clamping the reported quantile bounds, resolution in
  the 0.5–5 s range, and identifying the route a run took.
- **79c inherits a measured residual and a failed attempt to measure another.**
  design/78's carried item — M2's ~0.20–0.35 s shorter gaps at 50 ms — is now
  `R105` in `design/70`, and it is still open **for an instructive reason**:
  block 78a's gate was expected to measure it and could not, because all three
  arms ran at `interval_s = 0.5`, which hides any residual below half a second.
  `R104` (`_set_channel_for_composite`'s per-phase refresh) is the other row.
- **Item 1 below has a worked example now.** "Keep requested intervals separate
  from achieved timing" is not a formality: 78a's own gate write-up initially
  read its 0.499 s / 0.505 s cadences as evidence that the write cost ~5 ms,
  when both arms were simply meeting a requested 0.5 s interval and would have
  agreed for any write that fitted inside it. The unconfounded number was the
  write→exposure span, 18–44 ms. **A cadence column that equals the requested
  interval is reporting the request, not the performance.**
- **What 78a did measure, as a baseline for anything here**: on M2, one
  `refresh_gui` drags ~2.3 s of EMU device reads onto the calling thread. That
  is the size of a single GUI refresh on an EMU rig, and it is the number to
  reach for before assuming any other refresh site is cheap.

## Problem and decision

[Duration0's multi-second stalls](78-duration0-update-latency.md) show how a
successful write can hide an unacceptable execution path. Design/77's motion
ordering issue shows that cadence can change the scientific result. Speed is a
requirement at both levels: the microscope agent chooses an efficient supported
workflow, and developers make the underlying tools efficient and measurable.

**Minimize elapsed time and unnecessary work subject to the experiment's
contracts.** Those include acquisition order, exact versus asynchronous property
timing, analysis coverage, data retention, safety, settling, verified state,
restoration, and standalone export. Faster acquisition also changes illumination
per wall-clock time; preserve the agreed dose envelope. No silent weakening of
these requirements and no prompt asking permission merely to use an equivalent
faster implementation.

**These are two failures, and they need two fixes.** In the M5 session the agent
did not choose a slow route — the only route that honours a per-frame value was
the one it took. It then **misattributed** the cost ("irreducible" serial write
plus camera round trip, when the write was 39 µs) and stopped. Attribution is
79a and an instrument; route choice is 79b and guidance. Keep that implementation
order, while clarifying immediately that an unattributed cost must be reported
as such. That clarification addresses the observed reporting failure without
waiting for new instrumentation.

## Runtime agent policy

The prompt now directs the agent to choose the fastest supported correct route,
consider configured plugins and native acquisition before custom loops, keep LLM
turns out of frame control, reuse valid discovery, and distinguish requested
timing from measured performance. Required fresh state checks remain.

**Restraint is possible before diagnosis is.** "Never call overhead unavoidable
or blame hardware without evidence that isolates the cause" is followable now:
the agent can report that the cause is unknown. A run returns `started_at`,
`completed_at`, `duration_s` and a
gap summary; the hook log's per-write record carried **no timestamp at all**
(`hook_decisions._record_event` → `HookBase.where_event`, position and
coordinates only) — **until block 78a added monotonic validation/write/wait/
read_back spans to it, merged 2026-09-08. The paragraph below describes the
state before that; 79a generalizes the spans rather than introducing them.** The agent cannot see a write span, a refresh span or a
read-back span through these results. Missing spans limit diagnosis, not honest
reporting. The prompt now makes the fallback explicit: report measured cadence,
say a cost is **not attributed** when the cause has not been isolated, and name
the measurement needed to investigate it. 79a supplies evidence for attribution;
it is not a prerequisite for this clarification.

**Prompt prose is the weakest lever in this repository, and it is testable
here.** design/61 measured a skill that was byte-identical to a prompt
paragraph; the rules-in-parameter-descriptions finding measured a statically
knowable refusal moving out of tool prose. Do not assume this paragraph works.
The instrument exists — `design/77-block77a-replay.py` and
`design/72-limbA-prompt-replay.py` replay a recorded session against two trees
with mechanical, tool-call-level scoring — and the control is already recorded:
M5 `20260904_140940_718928_microclaw_history.jsonl`, replaying line 31's result
(responding to line 30's call)
and scoring whether line 32's turn asserts irreducibility or blames hardware
without a measurement. Size the sample before reading a difference: design/59b
got 5/8 and then 15/16 from identical wording.

Route choice follows the requested experiment rather than a universal backend
ranking:

| Requirement | First candidates to compare |
|---|---|
| Fixed acquisition, no feedback | Existing native/batched acquisition; hardware sequencing if supported |
| Analysis that need not delay exposure | Existing acquisition observer with preserved acquisition order and coverage |
| Predetermined per-frame property values | Supported property sequence, otherwise explicit synchronized dispatch |
| A measured result must govern the next frame | Trusted adaptive handoff; minimize work on its critical path |
| Continuous imaging with bounded feedback delay | A configured plugin **only after** its stop rule, analysis window and data path are checked against the request; otherwise a proven continuous route |
| Repeated multi-step workflow | Existing composite acquisition/tool and standalone program, not one LLM turn per operation |

The plugin row is qualified because design/78 checked one: htSMLM's stop path
polls per second and waits a delay in seconds, which is not "1,000 frames after
the terminal value". A faster route with different semantics is not the same
experiment.

**Name what speed pressure will break.** The failure modes are dropping a settle
wait, widening an arrival band, skipping a read-back, thinning analysis
coverage, and reusing discovery that another client has invalidated. None is an
optimization. And **a benchmark run is a dose**: comparison runs on a real
sample count against the dose envelope and need the same authorization as any
other acquisition, so prefer the demo camera, a saved dataset, or a CoreLog
already on disk. Do not expose the sample repeatedly to guess a bottleneck that
logs or local replay can settle.

## Engineering policy and implementation

Extend existing runners and result records rather than building a second planner
or a benchmark framework. Each affected path needs:

1. **A timing contract.** State what must happen before exposure and what may
   lag: frame cadence, feedback age, start/stop latency, or whole-task duration.
   Describe exact values versus uncertain transition frames and what analysis
   coverage is required. Keep requested intervals separate from achieved timing.
2. **Attribution.** Use monotonic spans for preflight, authorization/validation,
   write, wait, read-back, GUI/listeners, hook analysis, handoff and teardown.
   Use acquisition timestamps for frame cadence with their clock/source recorded.
   Do not mix clock domains or add overlapping asynchronous spans. Collect
  bounded summaries and optional traces; bound both retained memory and output.
  Profiling must not add property reads or an unbounded per-frame log.
  Surface enough timing in tool results for the
   agent to report it without inventing statistics.
3. **Less work on the acquisition path.** Move discovery and immutable validation
   to preflight; retain dynamic safety and state checks at their required points.
   Drop per-write acquisition GUI/EMU refreshes (operator decision, 2026-09-06);
   preserve awareness with inexpensive progress summaries and write logs, and
   refresh the GUI after restoration. Reuse a verified read-back for reporting,
   reduce bridge round trips and avoid repeated acquisition startup. Cache only
   facts with a defined lifetime/invalidation rule: another client can change the
   rig. Optimize critical spans first; a language/backend rewrite is justified
   only by measured residual overhead. Removing a required wait is not an
   optimization.
4. **Comparable acceptance evidence.** Compare the same rig/configuration,
   exposure, ROI, event order, output, hook and illumination conditions; record
   versions, warm-up, sample counts, total duration, cadence and tail latency.
   Include native/no-hook and no-write controls where relevant. Set a feature's
   timing budget from its scientific need and measured baseline before its gate;
   there is no universal milliseconds-per-property threshold. Test structural
   regressions locally (extra reads, refreshes or engine restarts), not brittle
   wall-clock limits against mocks. Execute exported programs against fakes and
   gate live/export timing equivalence on hardware where required.

### The instrument we already have, and what was wrong with it

**Block 79a fixed everything in this section; it is kept as the statement of the
problem, not of the current code.** The bounds now clamp, the 0.5–5 s range
resolves, only populated bins are emitted, and the two timing shapes below have
become one `duration_breakdown` carrying `accounted_s` and the residual. Line
numbers are as they were when this was written and have moved.

`_GAP_HISTOGRAM_UPPER_S` (`microclaw/tools.py:4272`) is dense to 0.5 s and then
steps 1, 2, 5, 10, 30, 60, 300, 900 s. `count`, `min_s`, `mean_s` and `max_s`
are exact; `median_le_s` and `p95_le_s` are bin edges. Two consequences, both
visible in design/78's artifacts:

- **It resolves the cadence we want, not the cadence we have.** M2's 200-frame
  run reported `median_le_s: 0.25, p95_le_s: 0.3` — useful. The three-second
  runs reported `median_le_s: 5.0, p95_le_s: 5.0`. There are edges between
  0.35 and 5 s, but the 2–5 s bin is too coarse to resolve these long gaps.
  Design/78 combines the mean and bins to bound the longer gaps' average;
  the histogram cannot identify their frames or associate them with writes.
- **The quantile bound can be tightened using the observed maximum.** M5 line 19:
  `median_le_s: 5.0` beside `max_s: 3.625`; M2 line 308: `5.0` beside `3.656`.
  These are valid but loose upper bounds, not contradictions. `percentile`
  returns the bin's upper edge unclamped (`tools.py:4303`); clamping it to
  `max_s` makes the bound more informative without claiming an exact quantile.

The natural home for per-write spans is the hook log's existing per-write
record, which is bounded by the write budget rather than by frame count. That
keeps item 2's "no unbounded per-frame log" and gives the agent the one thing it
currently lacks.

**79a found the second half of that, on a rig-free gate.** Bounding by the write
budget is not the same as bounding the *payload*: `max_writes` is validated only
as a positive integer, so returning the records themselves put an unbounded list
in the tool result. It is now a per-phase summary plus at most three retained
records — flat at 2303 → 2359 bytes from 5 to 100,000 writes. And a span in the
result is not a span the agent reads: the teardown refresh was present, correct,
and ignored in two of three replay samples until it moved into the same shape and
units as the spans beside it. *Surfaced* means "in the shape the reader already
uses", not "present".

Plugin reuse is an ordinary candidate, not a lab-specific default. Discovery
must establish installed version, reachable instance, supported control API and
matching algorithm/data/lifecycle contracts. Delegate only what the integration
can constrain, observe, stop and export. Design/78 owns the htSMLM evaluation.

## Blocks and completion criteria

- **79a — Make the time visible.** Ships before 79b. 78a owns the write-path
  probe and its fix; 79a generalizes it and owns the reporting defects above:
  clamp the reported quantile bounds and improve resolution in the 0.5–5 s
  range using bounded histograms or a bounded quantile estimator. Bound memory
  as well as payload; do not retain every gap for an arbitrarily long run or
  return a per-frame array. Label bounds or approximation error honestly.
  Timestamp the hook log's write record, and
  identify the route a run took in its result.
  Verify that disabled instrumentation adds no bridge calls and that enabled
  instrumentation distinguishes a deliberately slow GUI callback from a slow
  property write. **Acceptance requires direct instrumentation tests and model
  replay.** With controlled delays and clocks, assert that spans identify the
  delayed operation and report correct durations; test bounded storage and no
  extra bridge calls. Then replay the M5 session with result fixtures matching
  the tested schema: the model should identify refresh when the supplied spans
  establish it, and say "not attributed" when they do not. A cautious model
  response cannot substitute for correct instrumentation.
- **79b — Performance-aware planning.** Depends on 79a. Align tool descriptions
  and acquisition skills with the actual fast routes and timing semantics, and
  put statically knowable rules in the **parameter** descriptions rather than
  prompt prose. Score with the existing replay instrument on: native fixed
  burst, observer-only movie, exact property sweep, adaptive next-frame rule,
  compatible plugin, incompatible plugin stop rule, and design/77's per-field
  movie as an ordering regression. Require efficient supported choices,
  preserved semantics and no invented claims. Predefine sample sizes, scoring
  and comparison criteria before inspecting results. **Superseded 2026-09-09:
  the seven-scenario two-tree gate was resized and then not run, because the
  control tree already carried every rule the arms tested — see "Block 79b's
  gate, resized and then not run" for the evidence, the spend record, and the
  rule that a gate needs an informational difference, not a relocation.**
- **79c — Optimize measured residuals.** Rank by total operator time,
  acquisition impact and ease of correction. Two residuals are waiting, and the
  first is **not** measured despite an attempt: M2's shorter histogram gaps at
  ~0.20–0.35 s with a 50 ms exposure (not individually identified as
  write-free), now `R105` — block 78a's gate could not measure it because every
  arm ran at `interval_s = 0.5`. The second is block 75a's measured
  **mark_finished → first frame accounted** p50 spans of 158.8 ms (demo) /
  275.3 ms (M2), reported as 96.7% / 99.1% of the respective measured windows.
  These describe those one-frame experiments, not a universal acquisition floor.
  Give each selected change a baseline,
  timing budget, matched comparison and live/export correctness gate. Record
  improvements and unresolved costs here; unowned work goes to design/70. Do not
  start a backend migration just because Python is present.

## Block 79a as assigned, 2026-09-08

Start commit `aa8e666`, branch `design79/make-the-time-visible`. Six decisions
the coordinator made before the block was handed over, so the implementer is not
inventing them and the reviewer is not renegotiating them.

- **There is no enable/disable switch, and the acceptance criterion above is
  reworded accordingly.** "Verify that disabled instrumentation adds no bridge
  calls" presumed a toggle. A monotonic span is two `time.monotonic()` reads and
  no bridge round trip, so a flag would be a layer guarding nothing —
  `CLAUDE.md`'s "don't add layers". The criterion becomes: **spans are
  unconditional, and a test asserts that adding them adds no bridge call at
  all**, exactly as 78a's `test_property_write_spans_attribute_delay_without_
  extra_bridge_calls` already asserts through `calls == [...]`.
- **"Generalize 78a's write record" means the named-stage write and the
  teardown.** `_apply_property` has spans; `_apply_named_stage` (`hook_decisions.py:591`)
  has none, and it is the other budget-bounded hardware write. And
  `finish_owned_cleanup` (`tools.py:4675`) is where the surviving `refresh_gui`
  runs — the ~2.3 s EMU fan-out 78a measured. **Without a span there, "a slow GUI
  callback" is not distinguishable from anything**, which is the one attribution
  the acceptance criterion names by hand. Both are per-run or per-budgeted-write,
  never per frame.
- **The histogram gets more edges between 0.5 and 5 s, and the payload stops
  emitting zero bins.** A fixed tuple is bounded by construction, which satisfies
  "bounded memory" without a quantile estimator nobody needs; a t-digest here
  would be a layer. But 35 edges emitted in full at up to 1 Hz is output that is
  ~97% zeros — M5 line 31 carried 22 empty rows around one populated one. Emit
  the populated rows only, under a key that says so. The edge tuple stays pinned
  by a test, so the axis is still a documented constant.
- **`median_le_s` / `p95_le_s` clamp into `[min_s, max_s]` and keep their names.**
  They are upper bounds on a quantile and the `_le_` suffix is load-bearing; the
  defect is that the bound is loose, not that it is a bound. M5 line 31 reported
  `median_le_s: 5.0` beside `max_s: 3.203`.
- **The route a run took is reported in 77b's existing vocabulary, not a new
  one.** `run_multi_position_protocol` already returns a `timing` block whose
  `strategy` names the route (`tools.py:7630`). `run_timelapse` and `run_zstack`
  return no such thing, so the model cannot tell a hardware-sequenced burst from
  a software-paced dispatch from a hooked run except by re-deriving it from its
  own arguments. Report the route **as executed**, from the same `_sequenced_ms`
  predicate 78b shipped — never a second copy of that arithmetic, and never a
  millisecond threshold.
- **The replay instrument is the implementer's; running it live is a gate step.**
  It costs API tokens and needs network, which the runner sandbox does not have.
  So the block ships `design/79-block79a-replay.py` and its bridge-shaped
  selftest, copied in shape from `design/77-block77a-replay.py`, and the
  coordinator runs it. Its two arms are fixtures **matching the schema this block
  actually ships** — one whose spans establish the refresh, one whose spans do
  not — and the second arm's pass condition is "not attributed", not silence.

## The teardown span had to become discoverable, and must not become a label

Block 79a's second pilot, 2026-09-08. `attributed-teardown` scored 1/3 while
`attributed-write` and `unattributed` scored 3/3, and the two failures were not
the scorer: neither response mentioned the teardown refresh at all. The model
read the per-write phases out of `hardware_write_timing_summary` — which reports
`count`/`min_s`/`mean_s`/`max_s`/`total_s` per phase, in seconds — and read past
`teardown_timing`, a separate top-level key holding raw absolute monotonic
timestamps. An 11.87 s refresh accounted for 11.87 s of a 12.07 s run and went
unremarked twice out of three times.

Item 2 asks for spans. Item 2 also says **"surface enough timing in tool results
for the agent to report it without inventing statistics"**, and a span delivered
as two absolute timestamps under a housekeeping name, in a different shape from
the summary beside it, is not surfaced. Two spellings of "here is how long
something took" in one result is the "two functions that do almost the same
thing" defect, one field over.

**The decision: one run-level breakdown, in the shape the model already reads.**
Fold the teardown spans into the same per-phase summary as the write spans,
report every phase as a *duration*, and add `accounted_s` against the run's
measured duration with the residual stated. `teardown_timing` goes away rather
than being kept alongside — there is no compatibility to preserve.

The residual is the load-bearing half and it is why this is not merely cosmetic.
The `unattributed` case is exactly "the spans do not add up to the elapsed
time", and today the model can only reach that by doing the subtraction itself
and trusting that it has found every span. Stating `unaccounted_s` makes the
honest answer available from the record instead of from inference.

**And the trap: do not add a `dominant_phase` field.** It is the obvious next
step and it would quietly destroy the gate. The arm exists to measure whether a
model *compares measured spans and attributes the cost*; if the result names the
winner, a pass proves only that the model can read a label, and the measurement
stops being about attribution at all. Report the numbers in one place, in one
shape, and let the comparison be the thing under test. This is the same
distinction design/79 draws between reporting the request and reporting the
performance — a pre-computed answer is not evidence that anyone can compute it.

Two constraints that come with it. The breakdown must appear for a **hookless**
run too, where there are no write records but there is still a restoration span
and a residual — `hardware_write_timing_summary` is assembled in
`_adaptive_result`, which hooked runs alone reach, so this cannot simply live
there. And the existing bounds hold unchanged: a fixed set of phase names, at
most three retained records, no per-frame array, and no extra bridge call.

## Block 79a, closed 2026-09-08 — what was measured and what was not

**Shipped.** Quantile bounds clamp into `[min_s, max_s]` (M5's `median_le_s: 5.0`
beside `max_s: 3.203` is now 3.203). The histogram resolves 0.5–5 s and emits
only populated bins. Monotonic spans reached the named-stage write and the
teardown. `run_timelapse` / `run_zstack` report the executed route in 77b's
vocabulary, from `_sequenced_ms` rather than a second copy of that arithmetic.
And the two timing shapes became one `duration_breakdown`, in seconds, with
`accounted_s` and the residual named.

**Measured directly, not inferred.** `accounted_s + unaccounted_s == duration_s`
reconciles exactly, with the restoration span's nested write spans subtracted so
nothing is counted twice. The payload is flat at **2303 → 2359 bytes from 5 to
100,000 writes**. No bridge call was added. Suite 3081 passed / 99 skipped, run
by the coordinator, not taken from a report.

**The replay, honestly.** Three rounds, 15 samples, and **the arms were sized at
3, not the 16 this notebook's gate document specified.** That is underpowered and
is recorded as such rather than as a gate pass — `design/59b` went 5/8 then 15/16
on identical wording, so a 3-sample proportion from this instrument family is
corroboration, never a result.

| Arm | Before the teardown fix | After |
|---|---|---|
| `attributed-write` | 3/3 | 2/3 |
| `attributed-teardown` | 1/3 | 3/3 |
| `unattributed` | 3/3 | 3/3 |

The control that gives the teardown row its meaning: pilot 2 **stays** at 1/3
when rescored with the final scorer, so the change is the product's, not the
instrument's. The recorded M5 failure — line 32, the turn this block exists to
prevent — scores FAIL in all three arms and is pinned in the selftest.

**Enlarging the sample was declined, deliberately** (operator decision). It buys
a narrower interval on a corroborating measurement — roughly [0.44, 1.0] to
[0.81, 1.0] — and no decision hinges on the difference: 79a's correctness is
carried by direct measurement and 3081 tests, and 79b's arms are different ones.
If the number is ever wanted, 79b runs this instrument anyway and these three
arms ride along at near-zero marginal cost.

**Three rounds of scorer defects, and the pattern is the finding.** Each round
the scorer was written from imagined phrasing and failed answers that were
correct: it wanted `dominates` and got "dominated"; `wait span` and got
"`wait` phase"; the literal `not attributed` and got "I have not isolated exactly
which"; and finally the literal word **"teardown"**, which no response used
because it is our implementation's jargon rather than anything you would say to a
microscopist. It now keys on the measured number, which did not move across any
of it. *A mechanical scorer must be built from observed output, not from what a
phrase ought to be* — design/61's lesson, relearned three times in one block.

**The instrument's own defects outnumbered the product's, again.** Two pilot
rounds died on the gate: one because the fixture answered a *different call* than
the model had made (hard-coded interval, path, restore policy and sweep values
against a recording that specified others), which the model correctly refused;
one on scorer vocabulary. Neither was the product. Same shape as design/69a's 2
product defects against 6 gate defects, and design/75's two rounds that both
failed on the gate.

## Block 79b as assigned, 2026-09-08

Start commit `4baa9b1`, branch `design79/performance-aware-planning`. Nine
decisions the coordinator made before the block was handed over, so the
implementer is not inventing them and the reviewer is not renegotiating them.

- **The change lands in tool descriptions, parameter descriptions and skill
  files. The system prompt does not grow; where a rule moves, it shrinks.**
  Prompt prose is this repository's weakest measured lever — design/61 found a
  skill byte-identical to a prompt paragraph, and the rules-in-parameter-
  descriptions finding measured a statically knowable refusal doing more work
  once it sat on the argument it constrained. So every statically knowable rule
  79b states goes on the **parameter** it constrains, or on the tool it is about.
  `agent.py`'s performance paragraph stays as the general principle. A rule
  moved out of prose must be **deleted** from the prose in the same commit: two
  spellings of one rule is the "two functions that do almost the same thing"
  defect, one surface over, and it is how prompt and schema drift apart.
- **Do not add a route-lookup table keyed by scenario.** This is 79a's
  `dominant_phase` trap in guidance form. If the text names the answer for each
  requirement, a passing sample proves the model can read a table, and the
  experiment stops being about route choice at all. State per tool what that
  tool does and what it costs; let the comparison be the thing under test.
  design/79's "Requirement → first candidates" table above is a design artefact
  and does not ship into the runtime.
- **No new millisecond thresholds, anywhere.** `CLAUDE.md` is explicit: the
  sequencing predicate is evaluated at plan time, never compared against a
  constant, and never written into a message, a parameter description or a
  skill — because safety depends on the frame count as well as the interval.
  Generalise it: a timing statement that ships must be **a predicate**, **a
  measured number carrying its rig and date**, or **a statement of what is not
  measured**. "Fast", "slow" and "negligible" unqualified by one of those three
  do not ship. Where the honest answer is that nobody has measured it, the
  description says so — that is the same discipline 79a's `unaccounted_s`
  encodes in a result.
- **The route names in guidance are the ones the result already reports.**
  `_single_run_timing` returns `no_time_axis` / `shared_timepoint_clock` /
  `no_requested_delay` and `fixed_plan` / `hooked_fixed_plan` /
  `adaptive_handoff` (`tools.py:5136`). Guidance that describes a route uses
  those words, so that what the model reads before the call and what it reads
  after it are one vocabulary. This is 79a's "77b's vocabulary, not a second
  one" rule moved one level up, and it is mechanically testable.
- **The incompatible-plugin scenario needs a fact the runtime cannot currently
  reach, and it belongs in `microclaw/skills/htsmlm/SKILL.md`.** design/78
  examined htSMLM at commit `30b6bfd`: the stop path polls at one-second
  intervals and waits a delay in **seconds**, which is not "1,000 frames after
  the terminal value", and its image-pair analysis is not a three-frame blink
  window. That is recorded only in `design/78`, which nothing at runtime reads,
  so today the model has no way to know a delegated stop rule would change the
  experiment. Record it in the skill **as a dated fact about that commit**,
  never as a general claim about plugins, and phrase it so a later htSMLM can
  falsify it. A faster route with different semantics is not the same experiment.
- **The instrument is `design/79-block79b-route-replay.py`, and it reuses rather
  than re-spells.** `--tree` and per-sample budget metering come from
  `design/77-block77a-replay.py` — a budget metered only at the end is not a
  budget, as 77a's own $3.97-reported/$12.13-actual gate proved. The
  retraction- and hedge-aware forbidden-claim scan, `api_blocks`, `interval()`
  and `run_sample` come from `design/79-block79a-replay.py` and are **imported,
  not copied**: that scorer took three rounds and fifteen real responses to get
  right, and a second copy will drift away from it silently. It ships with a
  selftest in the shape of `design/79-block79a-replay-selftest.py`, and
  `--dry-run` must exercise every path except the API.
- **Scoring is on the tool call, not on prose.** Each scenario declares the call
  that passes — tool name plus the arguments that make it the fast route — and a
  sample ends at the model's first call to an *acquisition* tool. Discovery
  calls (`get_system_state`, `list_hooks`, `list_devices`, `load_skill`, …) are
  answered from fixtures, do not end the sample, and are counted. Prose is
  scored only for the forbidden-claim signal, which is 79a's and rides along
  unchanged. And **each scenario's user message is written as a microscopist's
  request, never as a description of the route**: a prompt that names the answer
  measures nothing, which is design/59b's three lost rounds in one sentence.
- **Every scenario declares its expected control outcome before a sample is
  drawn, and a scenario whose control cannot fail is not a criterion.** For each
  of the seven: the passing call, at least one wrong-but-plausible call the
  **control tree** is expected to produce, and which of two kinds it is —
  a *movement* arm, where the control is expected to fail and the arm to pass,
  or a *regression* arm, where both are expected to pass and the criterion is
  that the arm does not degrade. design/77's per-field movie is the second kind
  and is labelled as such in the report: quoting a regression arm's pass as
  evidence that guidance improved something is exactly the error 79a's ledger
  row warns about one notebook earlier.
- **The sample size, the comparison and the thresholds are fixed in the gate
  document, by the coordinator, before the first live sample.** The implementer
  ships the instrument and its selftest and does **not** run it live — the
  runner sandbox has no network — and must not hard-code a default `--samples`.
  79a's arms ran at 3 and its ledger says plainly that this is corroboration and
  never a result; 79b's numbers are the ones design/79 asks to report with
  effect sizes and uncertainty, so they are drawn fresh, at a size chosen in
  advance, after a pilot whose numbers are development data.

`R107` rides along: 79a's three arms are re-run against this tree at near-zero
marginal cost. It is not a 79b pass criterion — it is a measurement 79b makes
possible, and if the sample cannot separate a blip from a habit, that is what
gets written down.

## Block 79b's gate, resized and then not run — 2026-09-09

**Operator decision, twice.** design/79 specified 79b's acceptance as seven
scenarios across two trees with predefined sample sizes, effect sizes and
uncertainty. After the pilot that was resized to a three-arm regression check at
~$3; after checking the control tree, **it was not run at all**. Both decisions
are the operator's and the reasoning generalises to every prompt experiment here.

### What the model replays have actually bought

Documented spend: 77a **$12.13** (authorised at $10, self-reported $3.97), 79a
**~$9** across three pilots, 79b's pilot **$2.85**. Against roughly $24: **one**
product discovery — 79a's teardown span, present and correct and unit-tested and
*ignored by the model in 2 of 3 samples*, which redesigned `duration_breakdown` —
one regression confirmation, and about **eight defects in the instruments**.

**Keep the capability; it is narrow and irreplaceable.** Only a model run answers
*"is this text read and acted on?"* A local test asserts text exists. It cannot
catch text that is present, correct, tested and unread.

**But never spend API credit debugging a harness.** Those eight defects are four
recurring shapes: a fixture that answers a different call than its scenario
describes; a scorer keyed on imagined vocabulary; an allowlist too narrow for the
tools a model reaches for; message plumbing. All four are findable offline —
render the fixture's own payloads and read them, replay a recorded session's tool
calls through the allowlist, count the `cache_control` blocks. 79b's pilot spent
$2.85 to find three defects that cost $0 to find afterwards.

**Say which question a run is for.** design/59b's 5/8-then-15/16 spread concerns
*estimating a rate*, which needs n≈16 and about $36 here. Every finding these
replays have produced was instead a **gross failure** — 0/3, ignored in 2 of 3,
refused 3 of 3 — and detecting "the model no longer does X" needs three samples.
Sizing for a rate when the question is a gross failure is how $3 became $36.

**Discovery has never come from a replay.** 77a's incident was in an operator's
real session; the replay was built afterwards to reproduce it. The cheapest
discovery channel is the operator running microclaw normally and scoring the
transcript, at zero marginal cost.

### The rule this block adds: a gate needs an informational difference

**A two-tree replay can only move if the trees differ in *what they say*, not in
*where they say it*.** Relocations are invisible to it by construction.

79b is mostly a relocation. Checked before spending, the control tree already
contained every rule the three chosen arms tested: `interval_s`'s full
`int(k * interval_s * 1000.0)` predicate was already **in the control's own
parameter** (78b shipped it), and both `observation-only` and `interval_s=0
means` were already **in the control's prompt**. The gate would have been asking
whether it matters where a rule lives, with both trees carrying the rule; the
predicted result was three nulls. Establish the informational delta between the
trees **before** proposing a sample size — it costs one `grep` and it would have
prevented a $18, then $36, then $3 proposal for a foregone conclusion.

### What closes 79b instead, from evidence already bought

The block's real risk was **regression**: statically knowable rules were moved
out of `agent.py`'s prompt onto the parameters they constrain, and a rule that
stops being honoured once it moves is the defect that matters. The pilot ran
against the **shipping** text, where those rules live *only* on parameters:

| Arm | Result on the shipping text |
|---|---|
| `native` | **3/3** — `interval_s=0` honoured with the rule deleted from the prompt |
| `observer` | **3/3** — the observer contract honoured with it deleted from the prompt |
| `sweep` | three correct calls at self-derived `interval_s` of **0.001, 0.05, 0.1** — none named in the prompt, all satisfying the deadline predicate over five frames |

`sweep`'s verdicts were `NOT_AVAILABLE`, voided by the fixture's allowlist; the
**calls** are in the transcript, and this workflow scores artifacts rather than
verdicts. The other two misses were a clarifying question and a hand-driven stage
move, neither a moved-rule failure. A control arm would only establish whether
the control *also* passes, which bears on an improvement claim. **No improvement
claim is made.** Three samples per arm, one tree: corroboration, never a rate.

The correctness fixes need no model and are carried by tests and review:
`run_zstack` no longer points at a `max_frames` it does not have; the observer
contract reaches all five `hook_strategy` parameters instead of two;
`read_hook_log takes one of them at a time` is restored to `R98`'s guidance; the
`laser_slot` silent-blank-frames consequence is back; unmeasured-cost caveats
went from 17 to 2 and no longer claim knowledge of "this rig".

### The instrument is shelved, not deleted

`design/79-block79b-route-replay.py` and its selftest ship correct and unrun:
seven scenarios with declared passing and control calls, `--tree` isolation
proven against both checkouts, per-sample budget metering, turn counts, hardware
writes ending a sample as FAIL, and the R107 cache-budget limb. It becomes a
recorded-session-seeded regression suite to run **rarely** — when guidance
changes materially in *content*, or when a real session shows something odd.
Its measured cost, `claude-opus-4-8`, 2026-09-09: **$0.114/sample, 3.4 turns**.

**R107 does not ride along and stays open in `design/70`**, with the pilot's
three additional clean `attributed-write` samples recorded there as corroboration.

## Block 79c-1 as assigned, 2026-09-10

Start commit `f9af854`, branch `design79/the-per-field-multiplier`. 79c's brief
is "optimize measured residuals, ranked by total operator time, acquisition
impact and ease of correction". Ranking them first found that **the two measured
residuals are both per-*acquisition* costs, and a multi-field run pays each one
per field.** That is where 79c starts, and eight decisions the coordinator made
before handing the block over.

### What was measured locally before the block was written

Two rig-free probes over the engine-shaped dispatching fake in
`tests/test_acquisition_order.py` (its `Acquisition` constructor dispatches to a
backend, as pycro-manager's `__new__` does — the ninth engine contract), counting
constructions, `refresh_gui` calls and core reads per field:

| Run shape | 4 fields | 8 fields |
|---|---|---|
| hookless `position_then_time`, `interval_s=0` | 4 acquisitions | 8 |
| hookless `position_then_time`, `interval_s=0.5` | 4 | 8 |
| hooked spaced (77b split), `interval_s=0.5` | 4 | 8 |
| hooked, `interval_s=0` (shared dataset, position axis) | **1** | **1** |

- **The teardown `refresh_gui` runs once per `_acquire_with_hooks`, and it is
  gated on `any(restoration_attempted.values())`** — so a hookless grid refreshes
  **zero** times (measured: 0 in every hookless arm) and R103's ~2.4 s lands only
  where a hook restored hardware. On the 77b split path that is once per field.
- **It is serialized into the grid's wall clock.** `finish_owned_cleanup` runs on
  the daemon waiter, but the foreground blocks in `while waiter.is_alive()` until
  it returns, so field *k+1*'s stage move does not begin until field *k*'s
  repaint finishes. On a 100-field spaced hooked EMU grid that is ~4 minutes of
  repaints between exposures — R103's own "nobody has measured that shape".
- **Neither composite reports any timing at all.** `run_multiposition_acquisition`
  returns `status`/`results`/`acquisition_order`/`timing`, and the 77b split
  return adds `dataset_layout`/`hook_log_note`. **No `duration_s` and no
  `duration_breakdown`** on either. 79a's rule — one breakdown, every phase as a
  duration, `accounted_s`, residual named — does not hold one level up, at exactly
  the shape where multi-field time goes.

### The decisions

- **The instrument ships with the fix, and the instrument is the composite
  breakdown.** 79a's order (`D4` before the bound, "because it is the instrument")
  applies again. `run_multiposition_acquisition`, `run_tile_acquisition` and the
  77b split return report one composite `duration_breakdown` in **79a's existing
  shape and units** — never a second vocabulary. Extend `_run_duration_breakdown`;
  do not write a second summariser.
- **The composite breakdown must not double count, and says so.** Two new spans
  are needed: the per-field acquisition window (construction → `acq.__exit__`
  returning) and the composite's own coalesced repaint. The acquisition window
  *contains* the hook write spans, exactly the nesting 79a already solved by
  subtracting intersections from `restoration`. Reuse that mechanism and state the
  exclusion in `phase_meaning`, as `restoration` does. `accounted_s +
  unaccounted_s == duration_s` must reconcile exactly, and the residual is the
  between-field work (XY moves, settling, preflight, `mkdir`).
- **No `dominant_phase`, and no per-field array.** 79a's trap, restated one level
  up, where it is more tempting because there are now N fields to rank. The bounds
  hold unchanged: a fixed set of phase names, at most three retained records,
  constant additional storage independent of field count.
- **The teardown repaint coalesces to once per composite — and a failure path that
  skips it is a regression, not an optimization.** This is R103's own question
  ("whether the refresh belongs once per *composite* rather than once per
  acquisition") answered yes. `_acquire_with_hooks` takes a deferral argument in
  the shape of the `close_reservation` argument beside it, not a new layer; when it
  defers it records that a repaint is **owed** in `teardown_timing`. The composite
  performs exactly one repaint after its loop, **on success and on every failure
  path** — `_HookedAcquisitionFailure` returns from inside the loop, `StageMoveError`
  and `AcquisitionUnterminated` propagate, and each must still leave the GUI
  showing restored state. Same shape as block 52c's exported script, which restored
  only on success.
- **Deferral applies only to a repaint the composite will certainly perform.** On
  expiry the foreground raises `AcquisitionUnterminated` and the daemon waiter
  keeps ownership of restoration; a composite `finally` would then repaint *before*
  the late restoration, which is worse than not repainting. `finish_owned_cleanup`
  already reads `waiter_must_close_reservation` from the waiter thread to decide
  reservation closure — decide the repaint on the same signal, in the same place.
  A test that expires one field asserts the **waiter** repainted.
- **Do not merge the hookless zero-interval grid into one acquisition.** It is the
  obvious reading of "avoid repeated acquisition startup" and it is wrong here:
  `_run_protocol_at` calls `settle_xy_move` per field, and an engine-moved
  `position` axis has no arrival verification at all, so the trade is a measured
  per-acquisition span against an unverified stage arrival per field. *Removing a
  required wait is not an optimization*, and a device that is not busy is not a
  device that arrived. The pre-existing asymmetry it exposes — the hooked
  zero-interval path already moves the stage through engine events with no arrival
  check — is a register row, not this block's scope.
- **Do not hoist the repeated position-independent reads, because they were
  measured and they are not where the time is.** The hookless path re-reads
  `get_exposure`, `get_image_width`, `get_image_height` and `get_bytes_per_pixel`
  per field (`plan_events`, N+1 times, whose per-field result is consumed only as
  `runtime_plan`), plus `get_xy_stage_device` per field and `get_available_configs`
  per field when a channel is named — 6 reads per field, 4 of them on the split
  path. design/24 measured per-poll bridge cost **under 0.1 ms** (not remeasured
  here), which puts 6 reads at well under a millisecond against a per-field
  repaint measured at ~2.4 s on M2. It is recorded as measured-and-negligible; a
  change here would be work with no attributable effect, which is what item 3's
  "optimize critical spans first" is for.
- **75a's per-acquisition span is not multiplied by hand.** Block 75a measured
  `mark_finished → first frame accounted` at 158.8 ms (demo) / 275.3 ms (M2) p50,
  96.7% / 99.1% of a **one-frame** acquisition's window, and design/79 already says
  those describe those experiments and not a universal floor. So this block does
  **not** claim N × 275 ms for an N-field grid. Whether the per-acquisition
  teardown multiplies is unmeasured, and the composite breakdown is precisely the
  thing that would measure it — on the demo machine, for free, with no dose.

### Acceptance evidence

Structural and local, per item 4's "test structural regressions locally (extra
reads, refreshes or engine restarts), not brittle wall-clock limits against
mocks":

1. N fields with a restoring hook produce **exactly one** `refresh_gui`, counted,
   for N in at least two values — and the pre-fix tree produces N.
2. One repaint still happens when field 2 of 4 raises `StageMoveError`, when a
   field returns `_HookedAcquisitionFailure`, and after a declined/failed
   restoration; and the **waiter** repaints on the expiry path.
3. `accounted_s + unaccounted_s == duration_s` on the composite, with a nested
   write span inside a field's acquisition window counted once.
4. Payload size is constant in field count (assert flat from 2 to 500 fields, as
   79a asserted 2303 → 2359 bytes from 5 to 100,000 writes).
5. **No bridge call added**, asserted through the recorded call list the way 78a's
   `test_property_write_spans_attribute_delay_without_extra_bridge_calls` does.
6. Export equivalence: `_emit_multiposition` and `_emit_tile` still emit a script
   that compiles **and execs against fakes**. A `duration_breakdown` is a result
   field and emits nothing, but a coalesced repaint is a change to what runs.

Each of 1–3 needs watched-it-fail evidence on the pre-fix tree, and 1 and 2 are
*counting* tests, so the fake must not encode the assumption: count on a real
`ctrl` double whose `refresh_gui` is recorded, never on a `MagicMock` asserted
with `assert_called_once`.

### Gate

Rig-free. The structural half is local; the measurement half — does the composite
breakdown make the per-field multiplier visible? — is a demo-machine run of a
spaced hooked multi-field grid, scored from the returned `duration_breakdown` and
the acquisition event sink, not from a verdict. No dose beyond the demo camera.

## Block 79c-1, closed 2026-09-10 — what shipped, and the premise that did not survive

**Shipped.** Both composites — `run_multiposition_acquisition`, `run_tile_acquisition`
and the 77b split return — now report one `duration_breakdown` in 79a's shape and
units, with `accounted_s` and the residual named. `_acquire_with_hooks` records an
`acquisition` span (construction → `acq.__exit__` returning) alongside the
`restoration` and `refresh_gui` spans it already had, and the nested-span
subtraction 79a built for `restoration` now also excludes a hook's write spans
from the enclosing acquisition window, so summing the phases counts each measured
interval once.

**Not shipped: the repaint coalescing.** It was implemented, reviewed green over
two rounds, and then removed by operator decision on the day, because the cost it
removes is currently **zero**. The gate on the teardown repaint is
`any(restoration_attempted.values())`, and no multi-field tool can authorize the
capability that produces a restoration:

| `_acquire_with_hooks` call site | Calls per tool call | Can carry `property_envelope` / `named_stage_envelope`? |
|---|---|---|
| `run_timelapse` | 1 | yes |
| `run_zstack` | 1 | yes |
| adaptive survey | 1 | yes |
| acquire-on-hit phase | 1 — extends every hit's events, then one acquisition | yes |
| 77b split loop | **N** | **no** |

Every path that can repaint already repaints exactly once; the only path that
repeats cannot repaint at all. `run_multiposition_acquisition` accepts only
`illumination_envelope` and `artifact_limits`, `run_tile_acquisition` accepts no
capability argument, `_protocol_shape_kwargs` refuses all five in
`protocol_params`, and `configure_illumination` sets `_illumination_context` —
which no restoration reads. Driven directly, an adapter configured exactly as the
split loop configures it returns `None` from both `restore_property()` and
`restore_named_stage()`.

**This block was assigned on that projection without checking it.** `R103`
predicted "~2.4 s per field... four minutes of teardown repaints" on a 100-field
grid; `CLAUDE.md`'s own rule — *a guard is only as reachable as the object it
lives on* — is what would have caught it, and it was applied to the guard's
location and not to its reachability. `R123` records the enumeration so nobody
redoes it; `R103`'s row now carries the refutation. The coalescing design is
written down there rather than living as code in the teardown path, which is the
function block 60a found four defects in.

**Two deliberate non-changes, both recorded rather than swept.** The hookless
zero-interval grid still runs one acquisition per field: merging it would trade a
measured per-acquisition span for an unverified stage arrival per field, because
`_run_protocol_at` calls `settle_xy_move` and an engine-moved `position` axis has
no arrival check at all (`R126`). And the six position-independent reads repeated
per field stay: design/24 measured per-poll bridge cost under 0.1 ms, which puts
them under a millisecond against the spans that dominate.

### What a reachable grid now reports, measured

An 8-field spaced hooked grid (`snr_observer`, `n_frames=3`, `interval_s=0.05`),
real wall clock:

```json
"phases": {
  "acquisition": {"count": 8, "total_s": 0.045183, "max_s": 0.036991},
  "restoration": {"count": 8, "total_s": 0.0000126, "mean_s": 0.0000016}
},
"accounted_s": 0.045196, "unaccounted_s": 0.008877
```

Two things that shape are good for and one it cannot do. It separates time inside
acquisitions from time between them — the tests pin the residual to exactly
`(n-1) × settle` on the per-field shapes and to zero on the shared-dataset shape,
which is the per-field multiplier made visible. It also shows that a grid's
`restoration` phase is a **no-op sweep measured unconditionally** —
`finish_owned_cleanup` opens and closes that span before `restore_hardware()` is
called — so `{acquisition, restoration}` is the reachable phase set, not
`{acquisition}`. The coordinator's revision spec and `R124`'s first draft both got
that wrong and the implementer caught it against the code.

What it cannot do is name the slow field: per-field breakdowns are folded in and
omitted from the child rows, disclosed in `phase_meaning`, because retaining one
per field is unbounded in the field count. The bounded alternative — keep the
three slowest fields, the bound `slowest_records` already uses — was offered and
deferred to `R125`. Measured payload: **1037 bytes at 2 fields, 1033 at 500.**

### The nested-span subtraction has no reachable coverage

Worth stating plainly, because it is the one mechanism here that a real call
cannot exercise: no reachable grid hook times a write, since `property_envelope`
cannot reach a grid (`R123`) and the illumination route records no spans
(`R124`). So the subtraction's only coverage is the synthetic-teardown tests in
`tests/test_timing_attribution.py`. That is the right place for them — they test
the function, not a tool — but it is unreachable-by-construction rather than
merely untested, and it will stay that way until `R123` or `R124` closes.

### Review record

Three rounds. Round 1 returned seven findings, of which one was a defect in the
*coordinator's* acceptance criterion: it demanded that `accounted_s +
unaccounted_s == duration_s` hold **exactly**, quoting 79a — where it holds only
because that block's fixtures use integer-valued clocks. The implementer met the
criterion with a `math.nextafter` adjustment to the measured residual, which over
2,000,000 random `(duration, accounted)` pairs **still leaves the identity broken
in 3.2% of them**. The adjustment was removed, the assertions moved to
`pytest.approx`, and a regression test pins that the residual is now the raw
subtraction. *An acceptance criterion that asks for a float identity is asking for
the number to be adjusted.*

Round 2 was the narrowing. The Codex runner hit its usage limit mid-turn before
making any edit, and the round was completed by a Claude runner in the same
worktree — operator decision, rather than waiting for the 19:00 window.

### The demo gate, run 2026-09-10 — 6/6 criteria, and the measurement 79c asked for

Scored from `block79c1-evidence`, not from the exit status. Every criterion
independently corroborated: `acquisition.count` equals the number of NDTiff
datasets actually on disk for each shape (8/8/8/1, and 2/24 for the bound limb),
reconciliation drift is exactly 0.0 in all six runs, the payload grew **926 → 941
bytes from 2 to 24 fields**, and the shared-dataset route delivered 8/8 frames in
one acquisition.

**The measurement.** Eight frames at 10 ms exposure in every arm; the arms differ
in how many `Acquisition` objects they construct.

| shape | acquisitions | in-acquisition | residual | total |
|---|---|---|---|---|
| `hookless-zero` | 8 | 2.234 s | 1.985 s | 4.219 s |
| `hookless-spaced` | 8 | 1.516 s | 1.781 s | 3.297 s |
| `hooked-spaced` | 8 | 2.016 s | 1.890 s | 3.906 s |
| `hooked-zero` | **1** | 0.391 s | 0.140 s | **0.531 s** |

**The per-acquisition cost multiplies.** Eight acquisitions cost **7.9× the wall
clock** of one acquisition carrying the same eight frames. Per-acquisition mean is
0.19–0.28 s, and the process's *first* acquisition costs 0.859 s against 0.156–0.313
for the seven after it. That corroborates block 75a's shape on this machine —
158.8 ms p50 for `mark_finished` → first frame accounted, 96.7% of a one-frame
window — and answers the question 79c's brief could not: yes, it multiplies, and
this is what `R105`'s family looks like one level up.

**80 ms of the 4.219 s was exposure — 1.9%.** Against 15.1% for the shared route.
A small multi-field grid spends 98% of its time not exposing, split roughly half
in per-acquisition overhead and half in between-field work. The residual is the
stage: seven settled moves on a *demo* stage, which is nearly free, so this
understates a real rig rather than overstating it.

**n = 1 per arm.** The `hookless-zero` / `hookless-spaced` gap (2.234 s vs 1.516 s)
is mostly the first-acquisition cost landing in the first arm, and one run cannot
separate that from variation. Quote the 7.9× as a gross effect, never the
per-acquisition figure as a rate — `design/59b`'s 5/8-then-15/16 lesson applies to
timing as much as to prompts.

**What this does not license.** The obvious reading — collapse the grid into one
acquisition — is the change 79c-1 refused, because `_run_protocol_at` settles XY
per field and an engine-moved `position` axis has no arrival verification at all
(`R126`). There is now a measured 7.9× on one side of that trade and an
unverified stage arrival on the other. That is a design tension for a successor
block to resolve, not a defect to fix by deleting a wait.

**Two findings the score did not carry**, both from the artifacts: every phase
span is an exact integer millisecond — which was first read as a 1 ms floor and
is in fact **~15.6 ms**, since `GetTickCount64`'s unit is the millisecond while
its update period is not, measured directly afterwards (`R128`); and every dataset landed at `<name>_1` on a clean directory, with
microclaw correctly reporting the suffixed path (`R129`).

**One runbook error, no product error**: the runbook predicted "5/5 PASS" and the
gate reports 6/6 — limb 0 was not counted when the expectation was written. And
PowerShell renders the acquisition event sink's stderr as an error record inside
the redirected log (`At line:1 char:1 + uv run python ...`), which is the known
native-stdout family and is not a failure; the runbook should have said so.

## Block 79c-2 as assigned, 2026-09-10

Start commit `5d30365`, branch `design79/one-clock-for-the-timing-domain`. Closes
`R128` and `R124` together, because the first decides the clock the second's new
spans are written against. Six decisions the coordinator made before handover.

- **One clock for the whole acquisition timing domain, chosen at one site.** The
  hazard is not resolution, it is **mixing**: `_run_duration_breakdown` compares
  `accounted_s`, summed from span records, against `duration_s`, computed from a
  `started = time.monotonic()` in the calling tool. Move one and not the other
  and `unaccounted_s` becomes noise rather than a residual — the field whose
  whole purpose is to be the honest remainder. `CLAUDE.md` already forbids mixing
  clock domains; this is that rule with a name attached.

  The domain is **anything whose value reaches a `duration_breakdown`, a hook
  timing record, or a stage-move report**: the 24 span sites (6 in `tools.py`,
  18 in `hook_decisions.py`), the nine `started` / `composite_started` sites that
  feed `duration_s`, and `_acquisition_monotonic`. Establish the membership by
  following the values, not by grepping for the call — 85 `time.monotonic()`
  uses exist across nine modules and most are deadlines, polls and unrelated
  bookkeeping.

- **Extend the seam that exists; do not add one.** `_acquisition_monotonic()`
  (`tools.py:197`) is already "a clock seam for deterministic
  acquisition-supervisor tests". That is the shape — one function, substituted
  once. `hook_decisions.py` cannot import `tools` at module scope (the dependency
  runs the other way, through deferred imports), so the seam may need a neutral
  home; that placement is the implementer's call, but **two independent seams are
  not acceptable** unless a test proves they cannot disagree.

- **The `"clock"` string is derived, never written twice.** It ships today as the
  literal `"clock": "time.monotonic"` in `duration_breakdown` and in every hook
  timing record. Two spellings of one fact is the defect this repository keeps
  finding one surface over; derive the string from the clock actually used, so a
  future substitution cannot leave the record lying about which clock produced
  the numbers. **A test must assert the recorded string matches the function.**

- **Out of scope, deliberately, and not by sweep.** `conversation.py`,
  `webserve.py`, `knowledge_manager.py`, `updates.py`, `completed_dataset.py`,
  `image_analysis.py` and `controller.py` keep `time.monotonic()` **unless a
  value of theirs reaches a timing record** — check, do not assume, and report
  which you checked. A wall-clock timeout in a web handler is not part of this
  domain and changing it buys nothing.

- **`R124`: the illumination write gets the same spans as the property write.**
  `SetIllumination` performs a bare `ctx["core"].set_property(...)` followed by
  `self._accept(...)`, with no timing at all — and it is the **only** hardware
  write a multi-field grid can authorize (`R123`), so today the one grid shape
  that writes hardware attributes nothing. Add `validation` / `write` /
  `read_back` in `_apply_property`'s shape and units. Two `perf_counter` reads
  per phase and **no bridge call added**, asserted through the recorded call list
  the way `test_property_write_spans_attribute_delay_without_extra_bridge_calls`
  already does. The write is budget-bounded, so the payload bound holds.

- **Do not restate old measurements as if they were taken on the new clock.**
  design/78's numbers came from Micro-Manager's CoreLog at microsecond resolution
  and are unaffected; block 79c-1's came from `GetTickCount64` and are quantized
  to a ~15.6 ms tick. `R128` records both. This block changes what future
  measurements can resolve and **re-measures nothing** — no number already in a
  design document may be edited to look sharper than the clock that produced it.

### Acceptance evidence

Local; no rig. `R128` is already measured, so nothing here is owed a machine.

1. Every span site and every `duration_s` source in the domain uses the one
   clock, asserted by a test that inspects the modules' source rather than by
   review — the shape of `test_emitted_inline_defines_every_name_it_uses`.
2. The recorded `"clock"` value equals the name of the function actually called,
   asserted for both `duration_breakdown` and a hook timing record.
3. `accounted_s + unaccounted_s == duration_s` still reconciles, and a test
   proves the domain is unmixed by driving a run and asserting the residual is
   bounded by the run's real duration rather than by a clock offset.
4. The illumination write reports `validation` / `write` / `read_back` spans,
   watched failing on the pre-fix tree, with **no added bridge call**.
5. `design/79-clock-resolution-probe.py` still runs and its two clocks now agree
   with what the product uses — the probe is the instrument that measured `R128`
   and it should keep working.
6. Full suite by the coordinator on return.

## Block 79c-2, closed 2026-09-10 — and three things the assignment got wrong

**Shipped.** One clock for the acquisition timing domain, `time.perf_counter()`,
reached through a seam in `controller.py` — the bottom of the import graph, which
both `tools` and `hook_decisions` already import at module scope and which is
itself a domain member. `_TIMING_CLOCK` is a **string** resolved with `getattr`
per call, deliberately: one substitution then reaches all three modules, where a
from-imported function alias would have forced every clock test to patch two or
three module bindings. `_acquisition_monotonic` survives — four supervisor tests
substitute it with scripted finite clocks — but now delegates, so it cannot
choose a clock. The `"clock"` string is derived from the same constant, so a
record cannot disagree with the clock that produced it. `R124`'s spans reached
the illumination write. `R128` and `R124` both close.

**The assignment's inventory was incomplete, and the omission that mattered was
not a clock call at all.** `_run_duration_breakdown` selected records **by clock
name** — `if timing.get("clock") != "time.monotonic": continue`. Rename the
records and leave that behind and every span is silently skipped: `accounted_s`
goes to zero, `unaccounted_s` takes the whole run, no exception is raised, and
the suite stays green for anything not asserting `record_count`. Two more string
sites (`slowest_records`' per-record projection, and `gap_clock` in
`_single_run_timing`, which names the clock `_record_gap` measures with) were the
same shape. **A domain built by grepping for a clock call misses the code that
reasons about the clock's name** — and this one would have inverted the
instrument while looking healthy.

**Three specification errors, all found by the implementer against the code.**

- **`controller.py` was listed as out of scope while the domain definition
  included "a stage-move report", which is exactly what `settle_stage_move` and
  `settle_xy_move` produce.** The two statements contradicted each other. The
  definition wins, and it is demonstrably right: `run_tile_acquisition`'s
  return-to-centre merges a settle report carrying `elapsed_s` with a
  `duration_s` measured in `tools` into **one dict**, so the old scope would have
  put two clocks side by side describing one interval.
- **`read_back` on the illumination write is not achievable as specified.** The
  assignment asked for `validation` / `write` / `read_back` "with no bridge call
  added"; that route makes two bridge calls and neither is a post-write
  verification. The only reading consistent with the constraint is that
  `read_back` wraps the conditional baseline re-read — which is honest, because
  `baseline_stale` is set *precisely* when an earlier `set_property` raised, so
  that read asks the device what the failed write left behind (design/72's "a
  raised write leaves state unknown"). The consequence, stated because it is
  visible in the record: **a healthy illumination write records `validation` and
  `write` only.** A read-back on every write would be a new bridge call and a new
  decision, and was not taken.
- **The stated mixing mechanism was wrong, though its conclusion was right.**
  The assignment said a partial migration makes `unaccounted_s` "a clock
  offset". It does not: both `duration_s` and each span are *differences*, and a
  constant offset cancels inside a difference. The real defect is a **split
  pair** — one end of one span on each clock. That is why the guard has to be a
  source-inspection test and not a numeric one, and it is why this change is
  **unobservable locally**: on macOS `monotonic` and `perf_counter` are the same
  `mach_absolute_time()` counter to 41 ns, measured.

### Verified by the coordinator, by mutation rather than by reading

Both guards were mutated against the case each exists for, because a test whose
subject is *structure* never reaches its assertion on a pre-fix tree
(`feedback_mutate_dont_watch_it_fail`):

- **split pair** (span start moved back, end left): `test_no_domain_site_still_
  reads_the_old_clock` fails, and its assertion shows the injected 1000 s offset
  leaking into the arithmetic — `assert (-999.945 + 1000.000) == 0.055`.
- **whole pair** (both ends moved back): numerically invisible, as predicted, and
  caught by `test_one_clock_reads_the_whole_timing_domain` naming the exact site
  — `tools.py:4882 _acquire_with_hooks reads time.monotonic`.

The exported script was diffed across the change rather than trusted to its byte
pin: the delta is **exactly** the seam's constant and two functions plus four
substitutions inside the two inlined settle functions, 546 → 563 lines, nothing
else. The emitted script parses and defines `timing_clock` before its first use.
The eight clock reads kept on `monotonic` are listed per **function** with a
reason each, across all eight clock spellings, and an unused entry fails the
test — so the list cannot rot into a blanket exemption. Suite **3289 passed, 99
skipped**, run by the coordinator. The seam's `getattr` indirection was priced
because it sits on a per-saved-frame path: **42 ns**, against block 75a's
measured 158.8 ms one-frame window, 0.000026%.

### What is still owed, and why it is not local

Nothing in the acceptance list. But **the benefit of this block cannot be
observed on the machine it was written on**, by construction: the two clocks are
one counter on macOS. The cheap confirmation reuses an instrument that already
exists — block 79c-1's demo gate — and asks one arithmetic question of its
artifacts: are the phase spans still exact integer milliseconds? Before this
block every one of eighteen was, because `GetTickCount64`'s unit is the
millisecond. After it they should not be. That is one command and no setup, and
it is the only place the fix is visible.

### The demo confirmation, run 2026-09-10 — the fix is visible only here

Same instrument as block 79c-1's gate, same six criteria, **6/6** again. The one
question it was re-run to answer:

| | before (79c-1) | after (79c-2) |
|---|---|---|
| `clock` reported | `time.monotonic` | **`time.perf_counter`**, all six payloads |
| nonzero span values that are exact integer milliseconds | **16 of 18** | **0 of 36** |
| `restoration`, the no-op sweep | exactly `0.0`, six runs | **0.70–1.10 µs** |

Individual spans now read `0.1015593996271491`, `0.07394189946353436`,
`7.003545761108398e-07` — where every one of them previously landed on a whole
millisecond because `GetTickCount64` counts in milliseconds and updates every
~15.6 ms. **The instrument gained roughly four orders of magnitude on the
platform every rig runs**, and the count of nonzero span values going 18 → 36 is
itself the proof: it doubled because `restoration` stopped being zero, which is
exactly the six runs × one phase × three keys that were previously below the
floor.

This is the whole of what could not be seen locally: on macOS both clocks are the
same 41 ns counter, so every local test passes identically before and after.

**And the per-acquisition multiplier replicated.** 79c-1's measurement was n=1
per arm and recorded as a gross effect rather than a rate; this run is a second
independent sample of the same four shapes:

| | 8 acquisitions | 1 acquisition | ratio | exposure share of the grid |
|---|---|---|---|---|
| 79c-1 | 4.219 s | 0.531 s | **7.9×** | 1.9% |
| 79c-2 | 3.947 s | 0.605 s | **6.5×** | 2.0% |

So **n=2, 6.5× and 7.9×**, with per-shape in-acquisition totals differing 5–17%
between runs — ordinary variation, and the ratio survives it. The exposure share
is the stable one: 1.9% and 2.0% of an eight-field grid spent exposing. Neither
number is a rate and neither licenses collapsing the grid into one acquisition,
for the reason `R126` gives.

## Run ledger

| Block | Branch | Start commit | Implementer | Gate | Merged |
|---|---|---|---|---|---|
| 79a | `design79/make-the-time-visible` | `aa8e666` | codex | replay, 3/arm (underpowered, see below) | `fc8e2b7` 2026-09-08 |
| 79b | `design79/performance-aware-planning` | `4baa9b1` | codex | pilot only, $2.85, arm tree; two-tree gate **not run** (relocation, not information) | `031259c` 2026-09-09 |
| 79c-1 | `design79/the-per-field-multiplier` | `f9af854` | codex, then claude (Codex usage limit mid-round-2) | demo 6/6 + measurement, 2026-09-10 | merged 2026-09-10 |
| 79c-2 | `design79/one-clock-for-the-timing-domain` | `5d30365` | claude | local by mutation + demo 6/6, spans no longer ms-quantized | merged 2026-09-10 |

Policy changes alone are not evidence of faster execution, and an unmeasured
prompt paragraph is a hypothesis. Nothing here authorises a rig exposure.
