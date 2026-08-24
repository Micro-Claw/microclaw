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

---

# Blocks

One block. It is small, it is off-rig, and its gate is a demo-machine
confirmation rather than a booked rig trip — see "Gate" below for why that is
the right shape and what it can and cannot close.

## 57a — the stage checks fail closed, and an incomplete envelope refuses to export

**Branch:** `design57/fail-closed-guard`. **Design sections:** "Decision" §1 and
§2, "Explicitly unchanged", "Evidence" — all above, in this file.

### Items

- [ ] **1. `SafetyGuard.check_xy` requires four finite edges.** `x_min`, `x_max`,
      `y_min`, `y_max` must all be present. If any is `None`, raise
      `SafetyViolation` naming the missing axis *and* telling the operator which
      two keys to add, before comparing the requested position. Do not report a
      partial comparison first: a message that says "X=12.0 µm is below the
      minimum" when `y_max` is missing sends the operator to the wrong axis.
- [ ] **2. `SafetyGuard.check_z` requires two finite edges**, `z_min` and
      `z_max`, on the same contract.
- [ ] **3. `check_exposure` is unchanged and keeps its open upper edge.** This is
      an item, not a footnote: `camera` is the one section `_schema_3_document`
      deliberately never writes (`setup_tools.py:277-289`), so every config the
      in-app setup authors carries `camera.max_exposure_ms = None`, and emitted
      adaptive scripts call `guard.check_exposure(...)` per plan
      (`tools.py:1180`, `:1250`, `:1272`). Tightening `_bounded` instead of the
      two stage checks would make every such script raise on its first exposure
      check.
- [ ] **4. Do not touch `_bounded`.** Scope the change to `check_xy` and
      `check_z`, in the live guard and in the emitted `_RecordedSafetyGuard`
      alike. `_bounded` is shared by four checks and only the stage ones have a
      mandatory envelope.
- [ ] **5. `export_session_script` refuses an incomplete recorded envelope.**
      Where it builds `_export_safety_limits` (`tools.py:1448-1475`), require
      finite pairs for `x_um`, `y_um` and `z_um`. If any edge is `None`, set
      `_export_safety_limits` to `None` and carry an
      `_export_safety_limits_error` that names the incomplete axis, so the
      existing `CannotEmit` path (`tools.py:1079-1082`) turns it into one
      `# NOT EMITTED` line rather than killing the whole export.
      `exposure_ms`'s upper edge stays optional here too.
- [ ] **6. `_RecordedSafetyGuard.check_xy` / `check_z` reject a `None` recorded
      edge** in the emitted script, as the second line of defence for a
      hand-edited or externally assembled file. `check_exposure` keeps
      `_LIMITS["exposure_ms"][1] is None` as a supported value.
- [ ] **7. Fixture cleanup, deliberately and not mechanically.** ~30 sites
      construct `SafetyGuard(SafetyConstraints())` (`tests/conftest.py`,
      `test_agent.py`, `test_hook_decisions.py`, `test_safety.py`,
      `test_hook_illumination_artifacts.py`, `test_config_gate.py`,
      `test_integration.py`). Where the test is not about safety, give it a
      purpose-built permissive double. Where it exercises real motion, give it
      explicit bounds for the axes it moves. **Do not paste fake global bounds
      everywhere** — that weakens production fail-closed behaviour for fixture
      convenience, which is the thing this block exists to stop.
- [ ] **8. `TestNoConstraints` (`tests/test_safety.py:127`) is now wrong about
      the stage** and must be inverted for `check_z`/`check_xy` while keeping
      whatever it still asserts correctly (channels, exposure). Its
      no-op-guard convenience is exactly the intentional behaviour being
      reversed.
- [ ] **9. A reviewed `unbounded: true` edge now also refuses at the guard.**
      `_range_edge` parses it to `RangeEdge(None, reason)` and `StageConstraints`
      carries `None` (`safety.py:275-292`, `:73-79`), so items 1–2 refuse it.
      That is intended and consistent with `validate_live_rig`, which already
      refuses an open edge on a reachable axis. State it in the item's test so a
      later reader does not read it as an accident. `unbounded`'s *parsed*
      meaning does not change.
- [ ] **10. `get_system_state` still cannot fail.** It reports the guard's
      refusal through `_bounds_violation` (`tools.py:2173-2204`, `:2938`), which
      already catches `SafetyViolation` and returns it as an `out_of_bounds`
      string. After this block a missing-bounds config makes that string appear
      where none appeared before. Pin it with a test: **report, never raise**.
      No new result key.

### Tests — write the failing test first, and watch it fail

Every one of these must be watched failing against the pre-change tree
(`git checkout <before> -- microclaw/`, run, confirm the *stated reason*,
restore) — the implementer does it and the coordinator re-verifies it.
`CLAUDE.md` step 3, and the reason it is there.

- [ ] `SafetyGuard(SafetyConstraints()).check_xy(0.0, 0.0)` raises before any
      motion, and the message names X and the two keys to add.
- [ ] X declared but Y undeclared raises, **and** Y declared but X undeclared
      raises. Both directions; one of them is where a `getattr` loop typically
      still passes.
- [ ] `check_z` raises when `z_min` is absent, and again when `z_max` is absent.
- [ ] A reviewed-unbounded edge (`{unbounded: true, reason: ...}`) raises at the
      guard, with the parse itself still succeeding.
- [ ] Fully bounded in-range and out-of-range checks keep their exact current
      behaviour, including the boundary-exact cases in `TestBoundaryExact`.
- [ ] `check_exposure` still accepts any exposure when
      `camera.max_exposure_ms` is `None`, and still refuses above a declared
      ceiling.
- [ ] A normally validated bounded session still exports an adaptive script, and
      its `_LIMITS` carries the six finite stage edges.
- [ ] An adaptive export from a deliberately incomplete synthetic guard produces
      a `# NOT EMITTED` line naming incomplete recorded stage bounds — and the
      **unrelated** steps in the same session still export, matching the
      existing behaviour asserted at `tests/test_session_script_export.py:2723`.
- [ ] `_RecordedSafetyGuard` rejects an edited `_LIMITS` with a `None` stage
      edge, and still accepts one with `("exposure_ms", (0.0, None))`.
- [ ] **A session whose config declares no `camera` section still exports, and
      the emitted script's exposure checks pass** — exec the emitted source
      against fakes rather than only compiling it. This is the minimal document
      every in-app setup writes and the regression this block can break.
- [ ] `get_system_state` on a missing-bounds config returns, with the refusal in
      `out_of_bounds`.
- [ ] The existing `validate_live_rig` tests for missing policies and open
      reachable edges are **unchanged** and pass. They are the evidence that the
      question this document started from was already covered.

### Export

Nothing new is emitted and no tool gains an emitter. Item 6 changes the body of
`_export_guard_source`'s rendered guard; items 5 and 6 are the export surface of
this block in full. No tool decorated `@emits` changes shape, so the
eleven-undecorated-tools register row does not move.

### Gate — the demo machine, not a booked rig

`validate_live_rig` already refuses a missing or open range policy on a
reachable axis and both session entry points run it before exposing tools, so
**no live rig can reach the state item 1 refuses.** A rig trip booked to watch a
defensive impossible-state refusal would measure nothing. What does need
hardware is the ordinary path this block could plausibly break: an adaptive
session that exports a script whose recorded envelope is finite, and that still
runs standalone.

The demo machine closes that. Its MMCore, acquisition engine and bridge are
real, and block 43h's demo rounds already closed the whole adaptive
export-and-run path there. Runbook: `design/57-block57a-demo-gate.md`, on the
block's branch.

- [ ] Demo gate Steps 0–6 PASS.
- [ ] **Not owed to M2 or M5.** If either is free, running Step 3 there costs
      nothing and adds a second export; it is not a precondition of merging.

### Post-merge design gate

- [ ] Rewrite this file's "Remaining inconsistency" section in the past tense
      with the shipped messages quoted, so a later reader does not re-derive the
      open-door question a third time.
- [ ] Fold into `CLAUDE.md` only if the demo gate finds something generic. The
      block as designed teaches nothing new about the engine.
- [ ] Move the design/35 boundary note off "design/57 is assigned".

# Run ledger

| Block | Branch | Start commit | Implementer | Gate | Merged | Design reconciled |
| --- | --- | --- | --- | --- | --- | --- |
| 57a | `design57/fail-closed-guard` | `2f7e1af` | codex-runner | demo — pending | — | — |

**Baseline on the start commit, coordinator-measured:** 2090 passed / 99 skipped
/ 3 warnings (macOS), measured at `e32242c`. The two
commits between it and the branch point add this checklist and touch no code. On Windows expect the same total with a different skip
split. Gate on zero failures, never the count.

**Sequencing.** `design/55` is written and unstarted and touches
`microclaw/tools.py`'s acquisition preamble and hook capabilities; 57a touches
its `_export_safety_limits` block and `microclaw/safety.py`'s guard. Different
regions of one file, so whichever lands second rebases.
