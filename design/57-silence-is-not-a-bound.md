# Fail closed at the portable guard

> Revised 2026-08-23 from the question: "if the safety configuration contains
> no information at all about XY stage bounds and someone calls
> `move_stage_xy`, will it move?"

## Answer

Not through a normal Microclaw rig session.

`validate_live_rig` already discovers the reachable core XY axes and refuses a
missing range policy (`authorization.py:836–859`). It also refuses an explicitly
open range edge (`:860–874`). Both session entry points run that validation and
exit before exposing tools (`__main__.py:147–157`, `webserve.py:305–318`). On a
rig with an XY stage, a config with no XY bounds therefore cannot open a
session, and `move_stage_xy` is unreachable.

The original version of this design treated that case as an open live-rig
safety door. It is not. The parser and the guard do have a looser standalone
contract, but neither overrides the live-rig authorization gate.

## Remaining inconsistency

The lower-level guard still fails open when used outside a validated session:

```python
SafetyGuard(SafetyConstraints()).check_xy(1e9, -1e9)  # returns
SafetyGuard(SafetyConstraints()).check_z(1e9)          # returns
```

`check_xy` and `check_z` skip each comparison whose bound is `None`
(`safety.py:851–889`). This behavior is intentional in
`TestNoConstraints` (`tests/test_safety.py:127`), but it is surprising beside
`check_named_stage`, which refuses a stage with no declared envelope.

The same permissive comparison is copied into `_RecordedSafetyGuard` in an
exported adaptive script (`tools.py:841–900`). A normal export is produced from
an already-authorized live session, so its core-stage limits are finite. Even
so, the capture code should not emit a script if it is handed a synthetic,
legacy, or otherwise invalid guard with missing bounds.

This is worth fixing as defense in depth and to make the portable guard's
contract honest. It does not justify changing parsing or the safety data model.

## Decision

Make two narrow changes.

### 1. Core-stage guard checks fail closed

`SafetyGuard.check_xy` requires finite lower and upper bounds for both X and Y.
`SafetyGuard.check_z` requires finite lower and upper Z bounds. If any required
edge is `None`, raise `SafetyViolation` before checking the requested position.

Use an ordinary `SafetyViolation`; no new exception hierarchy or state-report
category is needed. A message should identify the missing axis and tell the
operator to add both bounds, for example:

```text
No X bounds configured for the core stage. Add stage.x_min and stage.x_max
before Microclaw may move it.
```

Existing finite-range behavior and out-of-range messages remain unchanged.
`move_stage_xy` already calls `guard.check_xy` before its hardware call
(`tools.py:2212–2232`), and all derived motion paths continue to use the same
guard.

### 2. Adaptive export refuses an incomplete envelope

When `export_session_script` captures `_export_safety_limits`, require finite
pairs for `x_um`, `y_um`, and `z_um`. If any pair contains `None`, set
`_export_safety_limits` to `None` and carry a clear
`_export_safety_limits_error`, using the existing `CannotEmit` path for the
affected adaptive step.

Also make the standalone guard reject a missing recorded edge, as a second line
of defense for an edited or externally assembled script. **Scope that to the
stage checks, not to `_bounded` itself.** `_bounded` is shared by four checks
and only one of them has a mandatory envelope: `check_exposure` passes
`_LIMITS["exposure_ms"][1]`, which is `camera.max_exposure_ms`, and `camera` is
an optional section that `_schema_3_document` never writes
(`setup_tools.py:277-289`). Every config in-app setup authors therefore carries
an open exposure ceiling, and emitted adaptive scripts call
`guard.check_exposure(...)` per plan (`tools.py:1180`, `:1250`, `:1272`). A
blanket `None`-rejection in `_bounded` would make every script exported from a
minimal document raise on its first exposure check — a fail-closed change to the
one section the schema deliberately leaves optional.

So `check_xy` and `check_z` refuse a `None` edge, matching `check_named_stage`,
which already refuses a device with no recorded envelope. `check_exposure` keeps
its open upper edge: an undeclared exposure ceiling is a supported
configuration, unlike an undeclared reachable axis.

## Explicitly unchanged

- `validate_live_rig` remains the authoritative check against the connected
  hardware. Its current missing-policy and open-edge refusals stay unchanged.
- `_stage_ranges` continues to accept a wholly omitted axis at parse time. A
  config may be inspected or used on a machine that does not have that axis;
  reachability is known only during live-rig validation.
- `StageConstraints` keeps its six optional scalar fields. There is no
  `RangePolicy` restructuring.
- The schema does not require all X, Y, and Z declarations and does not need a
  version bump.
- `unbounded: true` retains its parsed meaning. It still cannot authorize a
  reachable live stage because `validate_live_rig` refuses open edges.
- Setup-tool output does not change, and `camera.max_exposure_ms` stays
  optional. An open exposure ceiling is not an incomplete envelope.
- `get_system_state` needs no new result key. Its existing bounds-reporting
  behavior may report the guard's `SafetyViolation` in the established form.

## Evidence

Add focused tests for these contracts:

- `SafetyGuard(SafetyConstraints()).check_xy(...)` raises before motion.
- X declared but Y undeclared, and Y declared but X undeclared, both raise.
- `check_z` raises when either Z edge is absent.
- Fully bounded in-range and out-of-range checks retain their current behavior.
- A normally validated bounded session still exports an adaptive script.
- Adaptive export with a deliberately incomplete synthetic guard returns a
  `CannotEmit` line explaining that recorded stage bounds are incomplete.
- `_RecordedSafetyGuard` rejects an edited envelope containing a `None` stage
  edge, and still accepts one with an open exposure ceiling.
- **A session whose config declares no `camera` section still exports, and the
  emitted script's exposure checks pass.** That is the minimal document every
  in-app setup writes, so it is the regression this item can break.
- The existing live-rig tests for missing policies and open reachable edges
  remain unchanged and pass; they are the evidence that the original question
  was already covered.

Many unit tests deliberately use `SafetyGuard(SafetyConstraints())` as a
convenient no-op guard. Do not mechanically add fake global bounds everywhere.
Where a test is not about safety, use a purpose-built permissive test double.
Where it exercises real motion, give it explicit bounds for the axes it uses.
That keeps production fail-closed behavior from being weakened for fixture
convenience.

## Scope and gate

This is one small off-rig block: guard behavior, export refusal, and fixture
cleanup. Run the full suite. No dedicated rig trip is warranted because normal
live sessions already fail before tool availability when reachable bounds are
missing.

Fold one confirmation into the next adaptive-export rig gate: export a normal
bounded session and verify that the standalone script contains the recorded
finite stage envelope and still runs. That checks the ordinary path without
booking hardware solely for a defensive impossible-state refusal.
