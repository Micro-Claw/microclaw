# Choose the fastest correct execution path

Status: **PROPOSED**, 2026-09-06; reconciled against `design/78`'s merged blocks
2026-09-08. The policy text is in `agent.py`'s system prompt and `CLAUDE.md`,
and **both are now committed on `main`** — the original note that they were
uncommitted is stale. Neither has been measured: no block of *this* notebook has
run. What has changed is that design/78 shipped, and two of the premises below
moved with it — see "What design/78 already did" immediately after this.

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

### The instrument we already have, and what is wrong with it

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
  and comparison criteria before inspecting results; report effect sizes and
  uncertainty for this experiment. 59b's 5/8-then-15/16 spread on identical
  wording motivates that discipline but is not a universal significance threshold.
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

Start commit `2b025e0`, branch `design79/make-the-time-visible`. Six decisions
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

## Run ledger

| Block | Branch | Start commit | Implementer | Gate | Merged |
|---|---|---|---|---|---|
| 79a | `design79/make-the-time-visible` | `2b025e0` | codex | — | — |
| 79b | — | — | — | — | — |
| 79c | — | — | — | — | — |

Policy changes alone are not evidence of faster execution, and an unmeasured
prompt paragraph is a hypothesis. Nothing here authorises a rig exposure.
