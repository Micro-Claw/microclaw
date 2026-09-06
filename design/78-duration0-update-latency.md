# Fast Duration0 updates and the htSMLM option

Status: **PROPOSED**, 2026-09-06; evidence re-scored against the artifacts and
the pycro-manager source on 2026-09-06. Investigation and design only; no block
has run and no fix has been measured. General policy and performance work
belongs to [design/79](79-fastest-correct-execution.md).

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
at **0.001 s**, is consistent with this explanation. However, M5 and M2 run
AcqEngJ over the bridge, not this Python backend. The Java mechanism and any
minimum interval remain hypotheses until 78b checks AcqEngJ and tests the
actual dispatch. Only then should guidance name a threshold; distinct
integer-millisecond deadlines are the candidate condition to verify.

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

## Continuous feedback: control htSMLM or extend MicroClaw?

78c is not on the critical path and is ordered **after** 78a is measured: the
fix above predicts a substantial improvement. The plugin route also differs
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
- **78b — Sequencing threshold and exact-frame dispatch.** Test the proposed
  integer-millisecond mechanism against AcqEngJ's source and a demo-machine run:
  0.0001 s, 0.001 s, 0.05 s and 0 s, fixed plan and adaptive, before and after
  export. Move the refusal to plan time, and correct its message, the parameter
  description and the skill text to state the real threshold. Prove each planned
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
- `refresh_gui`'s docstring states a cache repaint adds no reads. True of
  MMCore, false of MM's listeners. Correct it wherever it is quoted.

## Run ledger

| Block | Branch | Start commit | Implementer | Gate | Merged |
|---|---|---|---|---|---|
| 78a | — | — | — | — | — |
| 78b | — | — | — | — | — |
| 78c | — | — | — | — | — |

This document authorises no rig exposure and no plugin change; each block
follows the repository's block workflow.
