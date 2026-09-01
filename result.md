# Block 65c — M2 round 2 follow-up result

Committed as `e8232f7` on `design65/adaptive-timelapse-route`. Nothing was
pushed, merged, or opened as a PR.

## Product changes

- `microclaw/authorization.py`
  - `authorize_property_write(..., approved_envelope=True)` now admits only an
    otherwise-unclassified pair. The exact approved property envelope is the
    authorization for that pair.
  - Explicit `excluded` entries are tested first and cannot be overridden by an
    envelope. Their refusal says that no declaration is legal until the exact
    exclusion is deliberately removed and the authorization map is rebuilt.
  - Bounded-stage devices retain the raw-write refusal. Existing reviewed typed
    capabilities (for example focus lock) retain their typed route; an
    unclassified property envelope cannot manufacture that exemption.
  - An unclassified numeric property with driver limits receives one literal
    `property_authorization.allowed_numeric` YAML example containing the real
    device/property and all six required keys. A discrete property receives one
    literal `allowed_categorical` example. The old four-stanza menu is gone.
  - The generic rationale is recorded beside the envelope admission: on a
    camera-triggered rig a bounded property may modulate an already-occurring
    pulse instead of enabling a new emission path. No rig-specific pair is
    embedded in product code.
- `microclaw/hook_decisions.py` and `microclaw/tools.py`
  - Both property-envelope authorization checks identify the already-validated
    envelope explicitly. The standalone emitter's inlined authorization stub
    accepts the same keyword, so exported adaptive scripts keep running.
  - The adaptive timelapse builds a separate runtime-only plan carrying the
    operator-approved `0.5 s/frame` software allowance. Its evidence is labelled
    `n=1 from M2, 2026-08-31` and records 99 gaps at 50 ms exposure (min 0.219 s,
    mean 0.2495 s, max 0.344 s).
  - Dose, disk, reservation, illuminated time, and `frames_planned` still use
    the original cap-derived accounting plan. `_runtime_ceiling_s` itself was
    not changed; it receives the widened runtime plan and continues to apply
    `max(estimate * 1.5, estimate + 300 s)`.
  - The adaptive result reports the runtime-plan estimate, allowance, evidence,
    derived ceiling, and that the fallback was not used.
  - Fixed `run_timelapse` now returns its bounded
    `inter_frame_gap_summary`, allowing the new fixed baseline limb to compare
    the same payload shape as the two adaptive runs.
- `microclaw/acquisition.py`
  - `AcquisitionPlan` carries optional runtime-only allowance and evidence
    fields. Their defaults are zero/none, and `_plan_with_hook_dose` preserves
    them without adding them to accounting.
- `design/65-block65c-m2-runbook.md`
  - Added the 100-frame, 50 ms, `interval_s=0` hookless fixed baseline and the
    100-frame routing-only adaptive run. Both prompts are literal and terminate
    in an explicit verdict.
  - The operator places all three complete gap summaries side by side and labels
    each as an n=1 M2 observation. The runbook explicitly forbids attributing a
    cause from those observations.

## Tests changed or added

- Five authorization tests prove: an approved envelope admits an unclassified
  pair; it does not override an explicit exclusion; it does not override a
  bounded-stage raw-write refusal; and numeric versus categorical refusals emit
  the one correct literal stanza.
- Existing authorization assertions now distinguish `unclassified` from
  `excluded`, including the channel-plan raw-write regression.
- The adaptive runtime test drives the real adaptive runner and observes a
  separate runtime plan with `0.5 s/frame`, while the reservation/accounting
  plan retains zero allowance and cap-derived dose/bytes. A second test pins the
  100,000-frame sanity check: 55,000 s estimated and 82,500 s (~22.9 h) ceiling.
- The existing fixed-route result test asserts that the cadence summary reaches
  the tool result.

Focused validation: **176 passed**.

## Computed-gate discrimination

All commands ran from this worktree.

| Tree | `--out-shape` | Probe result | Selftest result |
|---|---|---|---|
| accepted (`.`) | absolute | A–F PASS, `6/6 PASS`, exit 0 | PASS |
| accepted (`.`) | relative | A–F PASS, `6/6 PASS`, exit 0 | PASS |
| pre-change (`../microclaw`) | absolute | A–E FAIL, F PASS, `1/6 PASS`, probe exit 1 | PASS (discriminated) |
| pre-change (`../microclaw`) | relative | A–E FAIL, F PASS, `1/6 PASS`, probe exit 1 | PASS (discriminated) |

The pre-change failures remained specific: A/B rejected `max_frames`, C/D lacked
the decision-contract resolution interface, E failed the nullable shape, and F
continued to prove the existing export routes work.

## Full suite

Run from `/Users/zachcm/Code/microclaw-65c` with the project's Python 3.11
environment using `python -m pytest`:

```text
2653 passed, 99 skipped, 3 warnings in 206.19s
```

This is baseline 2647 plus six new tests.

## Not settled off-rig

- The two new cadence-attribution limbs have not run. Their fixed, routing-only,
  and density-and-routing summaries remain n=1 measurements to collect on M2;
  no causal attribution is claimed here.
- No teardown allowance beyond the operator-approved 0.5 s/frame was inferred,
  and `_runtime_ceiling_s` was not changed.
- The dose-bearing FPGA pulse limb still requires its next on-rig execution;
  this change removes the unclassified-map refusal only when its validated
  envelope is present. Explicit exclusions and bounded-stage refusals remain.
