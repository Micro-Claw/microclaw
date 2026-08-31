# Block 65c — scoring the M2 gate of 2026-08-31

Evidence: `~/Documents/Documents - Beyonce/Projects/Micro-Claw/block65c-m2`.
Scored from the artifacts, per block-workflow step 6. **Incomplete — see
"Still to score".**

## The computed gate's three FAILs are a defect in the gate, not the product

M2 reported `3/6 PASS`, failing `A_adaptive_export`, `B_rule_not_trace` and
`F_existing_routes`. All three are the same error:

    FileNotFoundError: gate65c-m2-computed\A_adaptive_export\adaptive_export.py

Each of those limbs calls `tools.export_session_script(..., str(path))` and then
reads `path` back. `export_session_script` writes through the product's
workspace-resolving writer, so the file lands at the **resolved** path while the
limb reads the **unresolved** one. They agree only when `--out` is absolute.

The selftest passes `--out` from `tempfile.mkdtemp()` — absolute. The runbook
tells the operator `--out gate65c-m2-computed` — **relative**. So the instrument
was never exercised in the shape it ships in.

Reproduced off-rig in one command, in under a minute:

    cd /tmp/relout && MICROCLAW_GATE_TREE=<worktree> \
      python <worktree>/design/65-block65c-gate.py --out gate65c-rel
    → 3/6 PASS; FAIL: A_adaptive_export, B_rule_not_trace, F_existing_routes

Identical limbs, identical cause. **A, B and F are therefore NOT EXERCISED on
M2, not FAIL** — the rig said nothing about export, and `F` in particular is the
existing-route control, so the gate ran that trip with no working control at
all.

This is the standing shape: *the gate's own harness is the one nobody reviews*,
and a selftest that feeds a different input shape than the runbook prescribes
does not discriminate. **Fix: have the gate use the path
`export_session_script` reports, or resolve `--out` before use — and make the
selftest drive at least one relative `--out`, because that is what the runbook
ships.**

## What M2 did establish

- **`C_contract_preflight` PASS.** A saved hook whose pinned source never
  references the vocabulary is refused at resolution, before acquisition, and a
  hook that does reference it resolves. The control half fired.
- **`D_retired_vocabulary` PASS.** Both retired shapes — the importing one and
  block 45's bare-call one — refuse at source resolution with a remedy naming
  the replacement. **This closes block 65b's one deferred rig-facing limb**,
  which design/65 assigned to this gate session.
- **`E_shape_refusals` PASS.** Four invalid `n_frames`/`max_frames` shapes refuse
  before path resolution, event construction and any mutation.
- **Engine callback shape, n=1 from M2.** `gate65c_callback_shapes.jsonl` is
  four rows, all `{"shape": "dict", "length": null}`: at `interval_s=0` on the
  successor route this engine handed the pre-hardware callback a **single event
  every time, never a list**. design/65 §"Cadence" asked for confirmation from
  the engine rather than from a fake we wrote; this is that observation, for
  this engine, in this run. It does not legislate batching elsewhere.
- Steps 2, 3 and 5 ran: `data/` holds `gate65c_density_stop3`,
  `gate65c_holdtime_image` and `gate65c_cadence100`, each with a hook log and an
  emitted script.

## Limb-by-limb

**Limb 2, successor dispatch and early stop — PASS.** `max_frames=8`,
`frames_planned: 8`, `frames_acquired: 4`, `frames_exposed: 4`,
`stop_reason: hook_stop`. The hook continued at times 0-2 and stopped at 3, so
exactly four frames were exposed against a cap of eight. Successor dispatch and
hook-decided stopping both work on the rig.

**Limb 5, the required cadence measurement — PASS, and it is the finding of
this trip.** 100/100 frames, `stop_reason: hook_stop`, 99 gaps at a **50 ms
exposure**:

| min_s | mean_s | max_s | count |
|---|---|---|---|
| 0.219 | **0.2495** | 0.344 | 99 |

design/65 §"Cadence" said "a few milliseconds of overhead on a 50 ms STORM
exposure may be scientifically fine; that is a number to publish, not to
assume." Published: it is not a few milliseconds. It is **~200 ms of software
per frame on top of a 50 ms exposure — a 5x cadence cost**, ~4 Hz where the
camera could run ~20 Hz. Amr's 100,000-frame dSTORM would take ~6.9 h on this
route instead of ~1.4 h. **n=1 from M2**, one hook, one field: not a property of
microscopes, and not yet attributed between bridge round trip, hook analysis and
engine dispatch. Attributing it is the next question, not a settled cause.

**The histogram cannot see the data it was built for.** `median_le_s` and
`p95_le_s` both report **0.5** while the mean is 0.2495 and the max 0.344,
because the bins step 0.2 -> 0.5 and every measured gap falls in one bucket.
Renaming them to `_le_` bounds stopped anyone reading 0.5 as a median, but the
instrument still has no resolution in the decade where this route actually
lives. **Add bins across 0.2-0.5 s** before the next trip.

**§"Teardown" can now set its allowance** from mean 0.25 s and max 0.344 s —
conservatively ~0.5 s per frame, recorded as n=1 from M2.

**Limb 3, image-derived non-dosing property — NOT EXERCISED, and the runbook is
at fault twice.**

1. It named the device `SmaractXY`. The device is **`SmarActXY`**. A literal
   name that is literally wrong is exactly as dead as a placeholder, which is
   the defect design/59 cost four rounds to; naming it literally is necessary
   and not sufficient.
2. Worse, the limb was **unrunnable by construction**. `SmarActXY` carries
   declared stage bounds, so design/49's pair-specific refusal rejects *any* raw
   property write to it: `RigAuthorizationError: Property write
   SmarActXY.Hold time (ms) was refused because 'SmarActXY' carries declared
   stage bounds.` No choice of casing would have made this limb run. Pick a
   non-stage device.

   Incidental evidence worth keeping: that refusal fired **on the adaptive
   route**, before any write, which shows the bounded-stage guard reaches the
   new path.

**Limb 4, the authorized `Duration0` run — NOT EXERCISED, with no recorded
reason.** The agent read the entry value (`Laser Trigger.Duration0 (us)` = 0,
Integer, limits 0-1048575) and was still reasoning about whether authorization
admitted the write when the session moved on to limb 5. No hook, no run, no
artifacts. **This was the dose-bearing limb and the closest thing in the gate to
the workflow design/65 exists for, and it produced nothing.** The runbook must
force limb 4 to terminate in an explicit PASS or NOT EXERCISED sentence rather
than trailing off.

**Engine callback shape — observed.** Four rows, all `{"shape": "dict"}`, no
lists: at `interval_s=0` this engine handed the pre-hardware callback a single
event every time. design/65 §"Cadence" wanted this from the engine rather than
from a fake we wrote. n=1 from M2; it does not legislate batching elsewhere.

## Fixed after the trip (2026-08-31, `baf79e2`/`ed03f64`/`695deb9`)

All five carried items below are done and verified by the coordinator: the
selftest now takes `--out-shape {absolute,relative}` and all four combinations
discriminate (implemented 6/6 both shapes; pre-change A-E FAIL with control F
PASS both shapes); limb 3 selects its pair by the four-condition test with
design/49's rule stated; limb 4 must end in an explicit verdict line before the
session moves on; the histogram gained 0.25/0.3/0.35/0.4/0.45 so it resolves the
~0.25 s regime M2 measured; and the instrumented script writes its own
`gate65c_instrumented_run.jsonl` start/end rows, where a missing end row is a
failed exported run even if the shape file has content. Suite 2647 passed / 99
skipped. `_runtime_ceiling_s` deliberately unchanged.

## Was carried to the next trip

- Fix the gate's absolute/relative `--out` defect and make the selftest drive a
  relative `--out`; re-run limbs A, B, F.
- Re-point limb 3 at a **non-stage** device, spelled from that rig's own
  inventory.
- Make limb 4 terminate explicitly.
- Widen the gap histogram across 0.2-0.5 s.
- `gate65c-density-instrumented.log` is 0 bytes while
  `gate65c_callback_shapes.jsonl` has content: confirm whether the instrumented
  export ran clean or its stdout was simply not captured. That limb is also the
  export-actually-runs check.

## Observed, not a defect

The agent passed a stale `tool_use_id` to `export_session_script`, noticed from
the returned `recorded_calls` that it had exported the *first* run rather than
the cadence run, and re-exported. The result payload made its own mistake
visible, which is the behaviour we want; worth remembering as a usability note,
not a bug.
