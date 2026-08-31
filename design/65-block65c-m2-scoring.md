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

## Still to score

- `gate65c_density_stop3`: hook log vs dataset `time` axes vs emitted script;
  `stop_reason`; no exposure after the accepted stop.
- `gate65c_holdtime_image`: requested vs achieved read-back per write, and
  restoration of the entry value.
- `gate65c_cadence100`: the full `inter_frame_gap_summary` — this is the
  measurement design/65 §"Teardown" needs before any per-frame software
  allowance can be set. Record as **n=1 from M2**.
- `gate65c-density-instrumented.log` is **0 bytes** although
  `gate65c_callback_shapes.jsonl` has content — check whether the instrumented
  export actually ran clean or its stdout was simply not captured. That limb is
  also the export-actually-runs check.
- No `gate65c_duration_stop3` anywhere: step 4 appears not to have run. Confirm
  whether it was `NOT EXERCISED` for want of authorization, or skipped.
- The 128 KB session history JSONL has not been read.
