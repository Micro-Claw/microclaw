# Fast Duration0 updates and the htSMLM option

Status: **78a and 78b MERGED**, 2026-09-08; 78c not started and not yet
warranted — see its section. Originally proposed 2026-09-06. Evidence re-scored against the artifacts
and the pycro-manager source on 2026-09-06; the AcqEngJ half of 78b's open
question settled from the jar on 2026-09-07 (§"The Java mechanism, confirmed").
78a and 78b are assigned; no fix has been measured on a rig yet. General policy
and performance work belongs to [design/79](79-fastest-correct-execution.md).

Input directory: `/Users/zachcm/Documents/Documents - Beyonce/Projects/Micro-Claw/duration0-slow-change`.
References below are file lines or local CoreLog timestamps on 2026-09-04.

## Evidence: the seconds are not the Duration0 setter

| Run / history JSONL | Interval | Exposure | Mean frame gap | Total |
|---|---:|---:|---:|---:|
| M5 `20260904_140940_718928_microclaw_history.jsonl` line 19, 5 planned writes | 0.5 s | 100 ms (camera's own) | 3.2695 s | 19.656 s |
| Same, line 31, 5 planned writes | 0.05 s | 100 ms | 3.1485 s | 19.0 s |
| M2 `20260904_131621_363982_microclaw_history.jsonl` line 308, 5 adaptive frames | 0.2 s | 50 ms | 3.57425 s | 21.344 s |
| Same, line 314, 5 adaptive frames | 0.0001 s | 50 ms | 3.4725 s | 20.141 s |

Both rigs, both fixed and adaptive dispatch, so this is not design/65's
successor-loop overhead. That M5 no-write baseline (~41 ms at 50 ms exposure) is
a different experiment and does not explain a multi-second write-associated stall.

### Within-run supporting evidence (M2 line 284)

The 200-frame run at `interval_s: 0.001`, 50 ms exposure, carried a **dense**
200-entry plan with actions at **frames 0, 40, 80, 120 and 160 only** — four of
them falling between exposures. Its gap histogram has exactly four entries
above 0.35 s and 195 at or below it (178 ≤ 0.25 s, min 0.203 s, max 3.843 s,
mean 0.29467 s, total 66.219 s).

Solving the mean against the bins bounds the 195 shorter gaps' sum at roughly
40.5–49.7 s. Together with the observed maximum, this bounds the four longer
gaps' sum at roughly 8.9–15.4 s, or **2.2–3.8 s on average**, not a bound on
each individual gap. All of this is within one acquisition, with the same rig,
exposure, ROI and hook. Four long gaps matching four intervening writes strongly
supports write-associated stalls, but the histogram does not retain frame
identity and cannot establish that correspondence. Per-frame timing is needed
to identify the affected frames; the refresh-on/off gate must establish how
much of the delay removing the refresh eliminates.

### One M5 cycle, attributed (`CoreLog20260904T110001_pid18460.txt`, 271668–272198)

Everything below is on tid548, the bridge thread that performed the write,
except the snap. 24.944211 → 28.130834 is 3.187 s wall clock.

| Span | Time | Cost |
|---|---|---:|
| Duration0 setter, incl. COM4 write, and device wait | 24.944211–24.944996 | 0.8 ms |
| GUI repaint proper (`Updating GUI … Finished updating GUI`, `from cache = true`) | 24.949810–25.066254 | 116 ms |
| EMU listener fan-out continuing on the same thread | 25.066570–27.823593 | 2.757 s |
| Two targeted COM4 Duration0 read-backs | 27.825355–27.912690 | 87 ms |
| Camera snap (tid29624) | 27.923877–28.073159 | 149 ms |
| Engine/dispatch remainder | — | ~76 ms |

The fan-out reads PI Z, ASI XY, three other trigger channels, four laser slots,
two analog inputs, a filter wheel and three Thorlabs stages. It runs **after**
MMStudio logs `Finished updating GUI`, on the calling thread, so the
`refresh_gui` bridge call cannot return until it completes — which is why the
cost lands between our write and our read-back and not in the background.
Individual Duration0 reads take ~36–42 ms; the setter timestamp is host-side
completion, not the FPGA's physical pulse transition.

M2's CoreLog is at IFO level and hides device reads, but shows the same shape:
repaint windows of **0.134 / 0.226 / 0.161 s** at 14:04:25.376, 28.700 and
32.462, each followed by ~2.95 s in which nothing is logged before the next
`[Snap Image] called`, and snap-to-snap intervals of 3.34 s and 3.68 s matching
line 314's run. These silent periods are consistent with M5's listener traffic,
but M2's log level cannot establish what work occurred during them.

Both rigs run EMU; M5's trace identifies EMU retrievals in the fan-out.
The ~0.12–0.23 s repaint timings are measurements of these configurations,
not a universal cost. Neither those timings nor M5's ~2.8 s listener span
should be extrapolated to other rigs without measurement.

### The code path

`UntrustedHookAdapter._apply_property` (`microclaw/hook_decisions.py:514`) calls
`set_property`, `wait_for_device`, then `ctrl.refresh_gui()` synchronously —
this is the **only** per-frame refresh in the codebase. `refresh_gui` calls
Studio's `refresh_gui_from_cache`; its docstring's claim that a cache repaint
avoids added reads is true of MMCore and false of this rig's listeners.
`_verify_property_actions` then reads the target twice: `_verify_property`
returns `None`, so the caller re-reads with `get_property` to populate
`last_known` (the second COM4 read above, ~36 ms). The standalone emitter
(`microclaw/tools.py:2190–2202`) reproduces the refresh, so export inherits it.

The M5 session agent's line 32 claim — that ~3 s/frame is "irreducible" serial
write plus camera round trip — is contradicted by this trace: the write is
39 µs and the camera 149 ms. It is the exact failure design/79 forbids.

## Why 0.0001 s did not defeat sequencing

Line 27's fixed plan at a **nonzero** 0.0001 s interval was still handed to the
hook as a sequenced list and refused mid-acquisition, with the refusal telling
the caller to "use a nonzero interval_s". The installed Python backend suggests
an integer-millisecond explanation:

- `multi_d_acquisition_events` sets `min_start_time = time_index * time_interval_s`
  (`acquisition_superclass.py:552`).
- The event stores it as **integer milliseconds**:
  `int(data["min_start_time"] * 1000)` (`acq_eng_py/main/acquisition_event.py:153`).
- `is_sequencable` refuses to sequence only when consecutive events' absolute
  min-start-times **differ** (`acq_eng_py/internal/engine.py:778–784`).

At 0.0001 s, frames 0–9 truncate to 0 ms, frames 10–19 to 1 ms, and so on:
sub-millisecond spacing can produce groups of equal deadlines, not an entire
arbitrarily long run at 0 ms. All five deadlines in the M5 incident fall in
the first group. Its refusal, alongside M2's successful dense 200-entry plan
at **0.001 s**, is consistent with this explanation.

### The Java mechanism, confirmed — and it is not a threshold

Read off `AcqEngJ-0.39.4.jar` (the jar this laptop's Micro-Manager
2.0.3-20260625 loads) with `javap -c`, 2026-09-07, by the coordinator. This
closes the paragraph above's open question **before** 78b starts, and it
changes what 78b may conclude.

`AcquisitionEvent.fromJSON` stores the deadline as `Long` milliseconds by
**truncation toward zero**, exactly as the Python backend does:

    getDouble("min_start_time") × 1000.0d → d2l → Long.valueOf → miniumumStartTimeMs_

`getMinimumStartTimeAbsolute()` adds `Acquisition.getStartTimeMs()` to that
field, so an equal *relative* pair is an equal *absolute* pair and the run's
start time cancels. `Engine.isSequencable` refuses a pair only here:

    if both t-indices are non-null and differ:
        if both absolute min-start-times are non-null and differ:
            return false

So two consecutive frames are hardware-sequencable **exactly when
`int(k × interval_s × 1000) == int((k+1) × interval_s × 1000)`**, and a
missing `min_start_time` — which `multi_d_acquisition_events` omits at
`time_interval_s == 0` — makes every pair sequencable regardless of t-index.
That is design/77b's zero-interval finding arriving from the Java side.

**A threshold is the wrong shape for the guidance, and 78b must not ship one.**
Evaluating that predicate over the deadlines
`multi_d_acquisition_events` actually emits (`min_start_time = time_index ×
time_interval_s`, `acquisition_superclass.py:552`) gives, over 2,000 frames:

| `interval_s` | first equal-consecutive pair | longest equal run |
|---|---|---:|
| 0.0001 | frames 0,1 | 10 |
| 0.0005 | frames 0,1 | 2 |
| 0.0009 | frames 0,1 | 2 |
| ≥ 0.001 | none | 1 |

which looks like a clean 1 ms threshold — and is not one. Extending the same
scan to 200,000 frames, **`interval_s = 0.001` collides at frames 4006/4007**:
`4007 × 0.001 = 4.006999999999999…`, whose truncation is 4006, the same
millisecond as frame 4006. Every other tested value at or above 0.001
(0.0010000001, 0.00123, 0.0017, 0.002, 0.00333, 0.01, 0.0333, 0.1) is clean
over 200,000 frames. So the safety of an interval depends on the **frame
count** as well as the interval, through double rounding — and a rule of the
form "use at least 1 ms" would have been stated with confidence, been wrong,
and been wrong only on the long runs where it matters.

Both facts are known at plan time: `n_frames` and `interval_s` are arguments.
78b should therefore **evaluate the predicate**, not compare against a
constant. Below is the exact deadline model to reproduce; it is short enough
that a test can own it outright.

    def _sequenced_ms(index, interval_s):
        # AcqEngJ AcquisitionEvent.fromJSON: (long)(min_start_time * 1000.0),
        # truncation toward zero. Python's int() truncates the same way.
        return int(index * interval_s * 1000.0)

Confirmed in the jar, and **confirmed against a running engine on the demo
machine, 2026-09-08**: over 4008 frames at `interval_s = 0.001` AcqEngJ produced
**exactly one** hardware-sequenced burst, at **exactly frames 4006/4007**. Scored
from `callback-shapes.json` rather than the gate's verdict, the full predicted
collision set `[4006]` equals the observed set — an exhaustive match over the
whole run, not just the first burst. `0.0001 s` batched all 8 frames into one
callback (M5's incident shape, reproduced with no hardware), `interval_s = 0`
batched (design/77b from the Java side), and 50 ms and short 1 ms runs did not.

So the double-rounding prediction is not a curiosity of the arithmetic: a real
engine batches there, and a "use at least 1 ms" rule would have been wrong on
exactly the long runs where it matters.

Two defects follow. The refusal's advice was already satisfied by its caller —
*an instruction a legitimate caller cannot act on is not guidance* — and it
fired **after** the acquisition started: `frames_exposed: 0`, but a dataset
directory exists and the hook raised. The interval is known at plan time.

## Decision: fix the common path first

Keep authorization, bounds, write budgets, device settling, verified read-back
and restoration. **Operator decision, 2026-09-06: drop the per-write GUI/EMU
refresh during acquisition in favour of speed.** Users need awareness of changes,
not a repaint per value. Coalesce one GUI synchronisation at teardown, including
the failure path after restoration. Keep the operator informed through existing
progress and write audit; per-write display updates are not an acceptance
condition. Explain that GUI controls can lag during a run.

Scope it, do not sweep it. `hook_decisions.py:514` and its emitted twin are in
scope. The other nine `refresh_gui` sites are interactive tools or per-phase
(`set_device_property`, `set_focus_lock`, `set_emu_laser_power_percentage`,
`execute_channel_plan`, `_set_channel_for_composite`) and stay as they are.

Do not merely move the same refresh to another thread: it still contends for the
serial links. And name the risk the gate must settle — if EMU's fan-out is also
driven by MMCore's own property-changed callback rather than only by the GUI
refresh event, removing our call relocates the work instead of deleting it. The
on/off comparison decides that, and a CoreLog at debug level is what shows it.

Return the verified achieved value from the existing verification helper and use
that observation in the audit instead of a second hardware read; its other
caller is the channel-plan path, so review both before changing its contract.
Keep dynamic safety and state checks fresh — this is not a metadata cache. Apply
the same behaviour to fixed actions, adaptive actions, restoration and emitted
scripts. No Duration0 special case in the generic dispatcher.

**A timing prediction to test, not an acceptance contract.** Removing the refresh
and duplicate read suggests roughly **0.3 s per frame on M5 at 100 ms exposure**
(0.8 ms write, ~50 ms read-back, 149 ms snap, ~76 ms remainder). This extrapolates
one cycle and is not a measured post-fix cadence. M2's 0.20–0.35 s shorter-gap
baseline at 50 ms is a reference, with required write, wait and read-back costs
still to add and measure. If the gate differs substantially, re-examine the
attribution and measure the residual. Acceptance requires removal of unnecessary
refresh work with preserved correctness; it is not a pass/fail threshold of
0.3 s. Remaining dispatch overhead belongs to design/79c.

## Measured: block 78a on M2, 2026-09-08

M5 was down, so the gate ran on M2, which carries the same EMU/htSMLM stack and
writes the same `Laser Trigger` / `Duration0 (us)`. Three arms of ten frames,
one Micro-Manager session, one CoreLog at debug level
(`CoreLog20260908T092536_pid4372.txt`). Scored from the log, not the verdict.

| Arm | Median frame cadence | Run duration | EMU reads on the **writing thread**, write → exposure |
|---|---:|---:|---:|
| no write | 0.499 s | 5.19 s | — |
| write, **with** refresh (`main`) | 2.584 s | 29.19 s | **490** across 10 writes |
| write, **without** refresh (branch) | 0.505 s | 7.69 s | **0** |

**The write-associated cost is gone, not reduced.** The without-refresh arm's
cadence is within 6 ms of the no-write baseline, so writing a property before
every frame now costs about what not writing it costs. Per write, the
write→exposure span fell from 2.26–2.43 s to 0.018–0.044 s. The setter itself
was 41–97 µs in both arms, as design/78 said it was.

**The fan-out did not move threads — it moved to teardown.** design/78's named
risk was that MMCore's own property-changed callback might drive the listeners
regardless. It does not. In the without-refresh arm there is exactly **one** GUI
repaint, at 15:17:25.201, *after* the last exposure ends at 15:17:24.895, and
all 49 calling-thread EMU retrievals follow it. Ten refreshes inside the
acquisition became one after it.

**That teardown refresh costs ~2.4 s on this rig** (15:17:25.378 → 15:17:27.643),
which accounts for essentially all of the 2.5 s gap between the 7.69 s run and
the 5.19 s no-write baseline. It is the measured price of the operator's
2026-09-06 decision to keep users aware at teardown, and it is paid once per
run rather than once per frame.

**Against the prediction.** design/78 extrapolated ~0.3 s/frame from one M5
cycle. M2 measured 0.505 s/frame — but its no-write baseline is already 0.499 s,
so the prediction was not wrong about the residual so much as it was predicting
a different quantity: the extrapolation included snap and read-back costs that
this rig's baseline already contains. The honest statement is that the
*write-associated* residual is ~5 ms, and the remaining half-second per frame is
ordinary acquisition cost that this block never touched. design/79c owns it.

**Two things this gate did not establish.** The second authorized property
design/78 asked for was **not exercised**: the fourth run wrote the same
`Duration0 (us)` with a different laser rather than a different pair, so
generality across properties rests on the code having no device-specific branch,
not on measurement. And M5 itself remains unmeasured — every number here is M2's.

Restoration was verified on both hooked runs (`requested '1'`, `achieved '1'`,
accepted with read-back).

## Continuous feedback: control htSMLM or extend MicroClaw?

78c is not on the critical path and is ordered **after** 78a is measured. That
measurement is now in: **78a removed essentially all of it.** A property write
before every frame now costs about 5 ms above a no-write baseline on M2, against
the ~2.1 s it cost before. The premise for 78c — that MicroClaw's synchronous
per-frame barrier is too slow to be usable and a plugin might be the way out —
is substantially weakened, and the table below already recorded that htSMLM's
stop semantics do not match the experiment that prompted it. **Do not open 78c
without a specific workflow whose timing MicroClaw's corrected path still cannot
meet.** The original reasoning is kept below because the plugin-integration
checks remain the right ones if that workflow appears. The plugin route also differs
from the earlier three-frame-window/1,000-frame-stop request; this does not
rule it out for other activation workflows.

Examined htSMLM commit `30b6bfd923a13076c08011ee001cc3a0e83c5434`:
[LocalizationAcquisition](https://github.com/jdeschamps/htSMLM/blob/30b6bfd923a13076c08011ee001cc3a0e83c5434/src/main/java/de/embl/rieslab/htsmlm/acquisitions/acquisitiontypes/LocalizationAcquisition.java)
starts an activation task and runs MMStudio's MDA;
[ActivationTask](https://github.com/jdeschamps/htSMLM/blob/30b6bfd923a13076c08011ee001cc3a0e83c5434/src/main/java/de/embl/rieslab/htsmlm/activation/ActivationTask.java)
analyses queued image pairs on a SwingWorker while the camera sequence runs and
publishes to `ActivationController.updateResults`, which sets the configured EMU
property. That is concurrent control without a before-every-frame barrier —
Java is not the explanation for the speed difference, the barrier is.

For that earlier request, scientific compatibility is currently negative:
htSMLM's stop path
polls at one-second intervals and waits a delay in **seconds** after its
criterion, which is not "1,000 frames after reaching the terminal value", and
its image-pair analysis does not implement the requested three-frame blink
window. Require an exact match or explicit agreement to changed semantics for
that experiment. For workflows using htSMLM's existing algorithm and stop
semantics, it remains a candidate subject to the integration checks below.

| Route | Contract it can honour | Main limitation |
|---|---|---|
| Corrected MicroClaw hook dispatch | Exact verified value before frame k; arbitrary hook rule | Each frame waits for the control work; ~0.3 s/frame predicted |
| MicroClaw drives htSMLM activation + its acquisition | The lab's existing algorithm, continuous imaging | Plugin API, algorithm, stop and storage contracts must fit |
| MicroClaw acquisition + htSMLM activation only | Reuse activation with our data path | Must prove its MM processor receives pycro-manager frames |
| Generic continuous MicroClaw feedback | Custom rules, rigs without htSMLM | New bounded concurrency, lifecycle and timing work |

If 78c runs, it must prove — through the existing plugin seam, with no screen
clicking and no private-field reflection — discovery of the configured target
and a running instance, readable/writable parameters, start/status/stop, error
reporting, and restoration that leaves the user's session configuration alone.
Enforce property and dose bounds *before* delegating writes: observing a plugin
after it writes is not enforcement. Avoid two controllers of one property by
run-scoped coordination, not an ownership lock. Record plugin version, resolved
mapping, settings, writes and timing, dataset format/path and stop outcome; its
MM datastore is not our NDTiff output, and an export refusal is not completion.

For any continuous route, distinguish command/read-back time from the first
exposure physically using the value; bound feedback age, queue size, stop
overshoot and dose; record skipped, stale and uncertain transition frames.
Hardware sequencing and safe in-sequence property mutation are separate
capabilities, implied neither by a device label nor by Duration0 working
elsewhere.

## Implementation blocks and acceptance

- **78a — Remove the per-frame refresh and the duplicate read-back.** Instrument
  validation, write, wait, refresh, read-back, dispatch and image spans. Fix the
  hook path and the emitter together. Local tests must prove no global refresh
  between a write and its exposure, exactly one verified observation per write,
  and unchanged failure and restoration behaviour. Gate on M5 with three
  otherwise-identical runs — no write, write with refresh, write without refresh
  — at **CoreLog debug level**, reporting cadence and write-latency
  distributions, the absence of the read fan-out, and whether it reappears on
  another thread. Compare with the ~0.3 s/frame hypothesis above without using
  it as a pass/fail threshold; report the
  residual as measured, not by subtraction. Exercise a second authorized
  property for generality and confirm on M2 when it is free.
- **78b — Sequencing predicate and exact-frame dispatch.** The AcqEngJ half is
  settled above; 78b owns the running-engine half. Test the confirmed
  integer-millisecond mechanism on the demo machine: 0.0001 s, 0.001 s, 0.05 s
  and 0 s, fixed plan and adaptive, before and after export, and include a
  frame count large enough to reach the 0.001 s / frame 4007 collision if the
  demo camera's cadence allows it. Move the refusal to plan time and make it
  **evaluate the deadline predicate** over `n_frames` and `interval_s`;
  correct its message, the parameter description and the skill text to state
  that condition. **Do not state a threshold** — see the table above for why a
  1 ms rule is wrong on long runs. Prove each planned
  value precedes its exposure and that no ghost frames appear on stop or
  failure. Before proposing one acquisition per frame as the "one-event
  handoff", weigh block 75a's measurement: a one-frame acquisition costs
  158.8 ms on the demo machine and 275.3 ms on M2, ~97–99% of it in
  `await_completion` — the same order as the whole residual 78a is aiming at.
  Design/77b took that cost per *position* for a reason that does not obviously
  transfer per *frame*.
- **78c — htSMLM integration decision.** Only after 78a is measured. On a
  configured rig, prove the API and scientific contracts above, benchmark
  against the corrected path, and record a go/no-go with evidence. A generic
  continuous controller is a later scoped block only if 78a's residual and the
  science both demand it.

## Carried forward

To `design/70-carried-forward-register.md` unless a block above claims them:

- `_set_channel_for_composite` refreshes at composite phase boundaries; on an
  EMU rig it may trigger the same expensive listener work. Measure its phase
  cost rather than assigning M5's ~2.9 s write-path measurement to it.
  Per-phase, not per-frame, so out of 78a's scope.
- M2's shorter gaps are ~0.20–0.35 s at 50 ms exposure. After 78a, measure
  the residual dispatch and required property-operation costs; design/79c owns
  further optimization.
- ~~`refresh_gui`'s docstring states a cache repaint adds no reads. True of
  MMCore, false of MM's listeners.~~ **Done in 78a** — corrected in
  `MicroscopeController.refresh_gui` and in the emitted script's stub comment.
- Two rows opened in `design/70` from 78a's gate: `R102` (one rig, one property)
  and `R103` (the teardown refresh costs ~2.4 s per run, and a spaced
  multiposition grid would pay it per field).

## Run ledger

| Block | Branch | Start commit | Implementer | Gate | Merged |
|---|---|---|---|---|---|
| 78a | `design78/no-per-frame-refresh` | `bfccc15` | Codex runner | **M2, 10/10 PASS 2026-09-08** (M5 unavailable; M2 runs the same EMU stack and the same pair) | `abfc527` |
| 78b | `design78/sequencing-predicate` | `bfccc15` | Codex runner | demo machine, **8/8 PASS 2026-09-08** (7/7 as run, plus one limb re-scored off-rig after a gate defect) | `f0799da` |
| 78c | — | — | — | — | — |

78a and 78b were assigned together, in separate worktrees, because their gates
are on different machines and neither depends on the other's result. They do
share `hook_decisions.py` and `tools.py`; whichever merges second rebases.
78c stays behind 78a's measurement, as this document requires.

This document authorises no rig exposure and no plugin change; each block
follows the repository's block workflow.
