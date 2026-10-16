# Package metrics — numbers a community package sends while it runs

**Status:** 87a demo gate passed 2026-10-16 (second run); PR #57.

**Input:** the SMAPpy publisher's specification,
[`ries-lab/smappy-microclaw` `docs/microclaw-feedback-spec.md`](https://github.com/ries-lab/smappy-microclaw/blob/claude/hopeful-hypatia-ng4jwr/docs/microclaw-feedback-spec.md)
(branch `claude/hopeful-hypatia-ng4jwr`, against `70976ee`), cited as
*spec §N*. This notebook takes spec §5.1–5.4 and §6 tests 1–3: the
**channel**. What acts on the numbers is
[design/89](89-feedback-during-acquisition.md) (spec §5.5–5.6). The htSMLM/MDA
workflow, where SMAPpy only watches, is
[design/88](88-mda-and-micro-manager-plugins.md).

## The problem

A package worker can only say free text today: `status` messages of up to 512
characters, kept in a 256-line deque and surfaced through the job record
(`skill_supervisor.py:428`, `JobHandle._stdout`). SMAPpy computes the number a
microscopist wants to watch during an SMLM run, localizations per frame, and has
no way to hand it over. The channel is needed by both consumers:

- design/88, where SMAPpy monitors density while htSMLM controls activation;
- design/89, where MicroClaw acts on the number.

The trust boundary stays where `design/83` put it. A package sends **declared,
numeric, named** values. MicroClaw never interprets a name except the one a
caller's rule names, and the package gains no hardware authority.

## Decisions

### M1 — protocol `1.2`: a `metrics` declaration and message (spec §5.1–5.2, adopted with three changes)

Adopted from the spec:

- **Declaration.** `metrics` is a per-operation key, allowed only in a
  release declaring `"protocol_version": "1.2"`, and refused on `self_check`
  and under `1.0`/`1.1`. It holds at most 32 entries, each key
  `[a-z][a-z0-9_]*` (≤64), each value a closed mapping of
  `{description (≤512, one line), unit (≤32), kind}`, where
  `kind ∈ {gauge, counter, per_frame}`. **The declaration carries no meaning
  for MicroClaw** (operator decision, 2026-10-15): there is no role or
  semantic field, and MicroClaw never interprets a metric by its name or
  description. Which metric suits which use is told to the agent by the
  package's own skill text (design/83), and the caller names it; if a package
  recommends the wrong one, that is the package's defect, not MicroClaw's.
  Safety does not depend on the choice: design/89 bounds every write by its
  envelope, budget, ratchet and freshness checks, whatever number drives it,
  as hook actions have been since design/32 and design/52. `opens_window`
  stays allowed. This is
  the pattern `opens_window` set in `skill_packages.py:425-429`, and the
  `SUPPORTED_PROTOCOLS` entry (`:841`) as `1.1` did.
- **Message.** `{protocol, type: "metrics", job_id, frames: [first, last],
  values}`. `frames` are 0-based dataset (NDTiff index) ordinals, at most
  2,000 per message. A `per_frame` value is a list of `last − first + 1`
  finite numbers. Gauges and counters are finite numbers, never `bool`; for a
  gauge or counter, `last` is the last dataset frame the value accounts for
  (`first` is ignored for a counter, which describes `[0, last]`). It is
  allowed only for operations that declare `metrics`, only before the
  `result`, and within the existing 65,536-byte line bound.

The three changes:

1. **Validation is split between two places.**
   `validate_worker_message(..., metrics=declared)` stays stateless. Its
   `operation` argument is the operation's *name* (`skill_packages.py:980`),
   so the declaration is passed in. `JobHandle` checks the rules that need
   history, all **per metric**: a metric's `last` never decreases across the
   messages that carry it, counters never decrease, and per-frame ranges are
   contiguous. (A rule across all messages would stop a slower per-frame metric
   from ever being sent on its own, since its range must start at 0 while a
   faster metric's message has already gone further. Operator decision,
   2026-10-15.) Each
   per-frame metric starts at ordinal 0; later ranges start at that metric's
   previous last + 1, even when intervening messages omit it. Every violation
   is an ordinary protocol failure. The spec's §9 validator becomes a parametrized
   test oracle, not shipped code.
2. **Per-frame values are never coalesced.** The spec's "keep the newest and
   count the rest as coalesced" above 10/s contradicts "every frame counted
   exactly once", and `_stdout` already reads every line. The 10/s rate is a
   publisher guideline that MicroClaw records (`received`, peak rate) and does
   not enforce. Existing limits bound individual lines and retained memory,
   not aggregate CPU or disk work. Keep aggregation constant-space and writes
   throttled; the flooding fixture measures acquisition cadence and reader
   load. A resource policy beyond these limits needs a separate decision.
3. **The per-frame queue exists only when a consumer attaches one.** A job
   that nothing acts on keeps only `latest` and the history. design/89's
   controller requests a bounded queue (100,000 frame ordinals across the
   queued messages, drop-oldest, with a drop latching `lost_frames` for this
   run). Register the queue before worker launch, including deferred launches
   from the supervisor's pending queue; attaching after launch can lose the
   first measurements. The controller itself starts only after job binding.

### M2 — supervisor state (spec §5.3, adopted)

- **Per job.** Each valid line updates `latest` (`name → {value, frames,
  received_at}`) under `JobHandle._lock`. For `per_frame`, `value` is the
  last scalar in the list, with `frames` identifying its message range; full
  lists travel only through an attached consumer queue. Reception time uses
  a monotonic clock for age, with UTC alongside it for durable inspection.
  Keep reception times and ranges separately for each metric.
- **History.** At most 1,024 samples, one per reception-time second. For each
  per-frame metric, accumulate sum/count and report a frame-weighted mean,
  rather than a mean of message means. Omitted metrics do not acquire fresh
  timestamps or contribute repeated values. Retain the latest partial bucket
  on termination.
- **Feed health.** `feed_alive` becomes false on worker exit or failure.
- **The record.** The job record gains
  `metrics: {declared, latest, history, received}`, persisted on the existing
  lifecycle writes and at most every 5 s while metrics arrive. Persist from
  the existing recorder side, never through synchronous disk writes in the
  stdout reader; expose feed health and loss diagnostics in the metrics block.
- **Consumer freshness contract.** Receipt is not capture time. Consumers get
  each metric's range and reception time, feed health and loss state. Design/89
  compares the last ordinal with MicroClaw's own read of the NDTiff index,
  with no tolerance tuned to any package's timing. Publisher backlog gauges
  remain ordinary named telemetry; v1 does not infer control behavior from
  names such as `frames_behind` (design/89 F1).

### M3 — seeing the numbers (spec §5.4, adopted)

- `analysis_job_status` returns the `metrics` block.
- Package discovery shows each operation's declared metrics with units, so the
  agent knows what a package offers before proposing to watch or act on it.
- The acquisition result carries each analysis job's last metrics.
- While an acquisition call blocks the agent, emit the latest values, units,
  frame ranges and feed health through the existing acquisition progress sink
  at most once a second. For a `per_frame` metric, show the current second's
  frame-weighted mean from M2's history bucket, not the last frame's scalar,
  which is a single frame's count and too noisy to watch. Read bounded snapshots without hardware calls;
  package text remains data. `analysis_job_status` alone is not live monitoring
  in that conversation.

This supplies live monitoring on a MicroClaw acquisition or MicroClaw-run MDA
(design/88). A dedicated panel view remains outside the block.

SMAPpy is the motivating example, not a dispatch key. Discover declarations
from the selected admitted package operation; never branch on publisher,
package name, metric name, or package-specific dependencies/timing. Verify with
two fixture packages using unrelated package and metric names, including a
gauge-only operation with no `per_frame` declaration. Both must expose their
own declarations, status, history and live progress through the same paths.

### M4 — later: other readers of the same feed

A read-only accessor in the hook context (spec §5.7) would let a saved hook
read `latest` for a custom rule. It is the same feed, not a new channel, and it
is subject to design/89's finding that a hook cannot act inside a burst. Not in
87a.

## Block

| block | content | where it is proved |
| --- | --- | --- |
| **87a** | M1–M3, and the publisher documentation (below). A fixture operation that emits metrics, including a flooding variant and a broken-contiguity variant. Spec §6 tests 1–3 with the §9 oracle adapted for per-metric contiguity; attachment-before-launch, uneven-message history weighting, sparse metrics and two per-frame metrics progressing at different speeds; a `1.0`/`1.1` byte-for-byte regression. | local + CI. The demo gate shows live progress and `analysis_job_status`, and measures flooding load/cadence |

**Publisher documentation is part of 87a** (operator decision, 2026-10-15).
`docs/publishing-skill-packages.md`, the publisher-facing guide, gains a
protocol `1.2` section, written and merged **with** the code, not before: a
guide describing messages the shipped MicroClaw refuses would mislead
publishers. It covers:
- the `metrics` declaration and kinds;
- that the package's skill text should name the metric to use for each rule
  (for example activation feedback) and typical parameters, since MicroClaw
  never infers it;
- the `metrics` message, `frames` for each kind, and the per-metric ordering
  and contiguity rules, with valid and invalid examples;
- **how often to report to remain continuously eligible for a rule.** Each
  selected metric must stay within `max_frames_behind` frames of MicroClaw's
  own dataset-index evidence and have a reception age within `max_age_s`
  (design/89 F3, defaults 500 frames and 3 s). Reporting frequency alone is
  insufficient if processing is behind. A sparse metric can act while fresh
  after arrival, then become stale between updates. Stale evidence remains
  visible, holds increases and feedback stops, and may still drive bounded
  decreases while write authority is active;
- that MicroClaw never interprets a metric by its name or description.

**Outside MicroClaw.** A `1.2` SMAPpy release is admitted only after the
catalog's intake pin moves to a commit with 87a, as happened for `1.1`. Moving
the pin is the operator's step. An older MicroClaw refuses the release at
`check_release`, which is correct.

## What 87a built and measured (2026-10-16)

As decided in M1–M3. The fixtures are `fixture-lab/photon-observer`
(two per-frame metrics, a counter and a gauge) and
`weather-lab/thermal-observer` (gauge only). They are signed by two publishers,
follow a growing NDTiff, and add `flood` and `broken_contiguity` modes. Two
decisions were taken during the block:

- **Cadence bound** (operator, 2026-10-15). The median burst with a reporting
  package must be ≤ 1.10 × the median with none. The flood arm is reported, not
  scored: its cost is the input to change 2's separate resource-policy decision.
- **The working line** (operator, 2026-10-16). The first gate's line,
  `slow: 3276.21875 counts [64–106] · metrics live`, was unreadable to the
  operator. Now each package gets a block under the frames line with one line
  per metric: `mean intensity 3,276 ADU · up to frame 106 · 0.4 s ago`. Values
  have four significant digits, and the declared `description` shows on hover.
  The progress payload therefore carries the package and each description.

**Demo gate**: Windows, demo config, 1000-frame bursts at 10 ms, 512 × 512
uint16, n = 5 scored per arm.

| arm | median burst | imaging span, first→final frame | lines before teardown | last live lag |
| --- | --- | --- | --- | --- |
| none | 11.41 s | 11.14 s | — | — |
| metrics | 11.03 s (0.967×) | 10.78 s | 55–62 | 0–37 frames |
| flood | 10.99 s (0.963×) | 10.72 s | 5,659–7,313 | 0–5 frames |

- Gap p50 and p95 were 11 ms and 12 ms in every arm.
- Live progress arrived 1.000–1.012 s apart, and final ranges reached frame 999.
- The broken-contiguity job failed on `frames` while its acquisition saved all
  1000 frames.
- The flood ran at 500–670 lines/s sustained and peaked at 1,272/s. That rate
  is set by a below-normal-priority worker on that machine. A Mac fixture
  reached about 23,000/s, so the reader's cost above about 1,300 lines/s is
  unmeasured on Windows (`R154`).
- The burst with **no** package images about 0.4 ms per frame slower, in all
  five repetitions and in the first run. That is unattributed (`R143`).

**The first gate run measured nothing that mattered.** Its fixture took a
pure-Python mean over every pixel and read before reporting, so it sent 8–12
lines per burst, and the "flood" arrived after teardown. All limbs but one
still passed. The gate now refuses to score a package that fell more than 200
frames behind, and a flood that was not sustained during acquisition, as NOT
EXERCISED.

## Back to the publisher

- Per-frame lists are never coalesced, and the rate is not enforced (M1).
- Both ordinal monotonicity and contiguity are tracked per metric, not across
  the whole message stream. Each per-frame metric starts at zero; sparse
  messages and independently progressing metrics remain allowed.
- SMAPpy's skill text should name the metric to use for activation feedback
  (`localizations_per_frame`) and a typical setpoint; MicroClaw does not infer
  it from the manifest.
- `frames` on a gauge or counter means the last frame the value accounts for.
- The browser shows each metric's name, value, unit and frame, with the
  `description` on hover. Names and units should read for a microscopist.
- Report often enough, and process close enough to the growing dataset, to
  keep each rule's evidence within the default 500-frame lag and 3 s reception
  age. Sparse reporting can leave stale intervals that hold increases and
  feedback stops; bounded decreases remain permitted (publisher guide / F3).
- The manifest and message formats are otherwise as specified.
- Acting on the numbers is design/89. It drops `end_percent`; the choice of
  control law is the operator's.

## Register

No row closes here. `R139` is design/89's, and `R86` is design/88's. Spec §8's
package side needs nothing further from MicroClaw once 87a merges. 87a opened
`R154` (the reader's cost at high line rates is unmeasured on Windows). It added
evidence to `R143` (an idle machine images slower) and `R114` (the 80b test
flake, n=2).

## Run ledger

| Block | Branch | Start commit | Status |
|-------|--------|--------------|--------|
| notebook | — | `2a6f7ee` | drafted and revised 2026-10-15 |
| 87a | `block-87a` | `13382d5` | demo gate passed 2026-10-16 on its second run (the first run's fixture was too slow to load anything); PR #57 |
