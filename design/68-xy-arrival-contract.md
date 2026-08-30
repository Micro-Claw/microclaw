# `move_stage_xy` reports a miss it never waited for

## Problem

`move_stage_xy` dispatches, calls `wait_for_device`, reads once, and *reports*
`error_um`. Block 56 established the rule this breaks — **a device that is not
busy is not a device that arrived** — and design/56 added that the immediate
read after `wait_for_device` can still be the pre-move position. On the Nikon a
successful move reported `last_device_status: "busy"` after 0.89 s and ~18
polls, and the defect block 56 replaced was exactly this shape: a 22.85 µm miss
reported as a success.

design/64 made it worse, not better. `center_feature` now snaps immediately
after every correction (`tools.py:5522`), so the loop can measure the field
through a stage that is still moving and then correct against that measurement.
The register row has been open since design/63 block 63a; design/67 deferred it
here explicitly — *"it belongs in that block, after design/66 lands"* — and
design/66 landed 2026-08-30.

Two more places already point at this block:

- `safety.py:485` accepts `stage.x_move_tolerance_um` / `y_move_tolerance_um`
  in the schema and refuses them with *"cannot be used until move_stage_xy has
  an arrival loop"*. An operator cannot declare an XY accuracy today.
- design/66 §"Result and error records" already specifies the field names this
  block must use, and why.

## What is already settled, and is not re-decided here

design/66 decided the *policy*: arrival is a **response** check, not an accuracy
one. The band is `max(2.0, 0.1 × displacement)`, a declared value overrides it,
and `verification_kind` says which was used. That reasoning is in design/66 and
is not repeated. This block extends the same mechanism to two axes; it does not
re-open the policy, does not learn a step size, and does not add a config
discovery path.

## Decision

### One loop, two independently gated axes

`settle_xy_move(core, device, target_x, target_y, start_x, start_y, band_policy,
configured_x, configured_y)` in `controller.py`, beside `settle_stage_move`.
Each poll reads `device_busy` once and both axis positions, and each axis is
tested against **its own** band computed from **its own** displacement by the
existing `_stage_move_band`. Success requires both axes in band together for
`STAGE_MOVE_REQUIRED_SAMPLES` samples spanning `STAGE_MOVE_STABILITY_WINDOW_S`;
one timeout of `STAGE_MOVE_TIMEOUT_S` covers the move.

Per axis, because that is what design/66 wrote down and because a 200 µm X move
paired with a 1 µm Y hold must not give Y a 20 µm band. A `hypot` gate would do
exactly that.

**Two scalar reads, not one `Point2D`.** `get_x_position()` and
`get_y_position()` are read on consecutive round trips, so the pair is not
simultaneous. That is acceptable *because* the gate demands stability across
samples — a pair read mid-motion cannot satisfy it. `getXYStagePosition` returns
a Java object whose field access is the exact shape this project has been bitten
by twice (camelCase fields, non-iterable collections); it buys nothing here.

### The result contract is design/66's, per axis

```
x_arrival_residual_um, y_arrival_residual_um   # |measured - requested|, per axis
x_tolerance_um, y_tolerance_um                 # the effective band per axis
x_band_source, y_band_source                   # relative | floor | configured
x_arrival_unverifiable, y_arrival_unverifiable
band_policy, verification_kind, within_tolerance, elapsed_s, last_device_status
start_um: [x, y], requested_um: [x, y], measured_um: [x, y]
```

Never `hypot`, never `residual_um`, never `residual_offset_um` — design/66 and
design/67 both say why: a centring session holds a `center_feature` result and
its `move_stage_xy` results in adjacent records, and `residual_offset_um` is a
feature-to-centre distance, not an arrival miss. Keep `x_um`/`y_um`,
`achieved_um` and `error_um`, which every history reader and gate scorer already
parses; the band is still a tolerance and renaming is not nearly free.

`XYStageMoveError(StageMoveError)`, so every existing `except StageMoveError`
keeps catching, with a message that names **which axis** failed and its start,
target and measured value.

### An unrequested excursion is still only reported

design/14 §8 saw a Y move carry a 1.1 µm unrequested X excursion. Under the
floor band that axis passes, and it should: this is a response check, and a
1.1 µm parasitic move is not a failure to respond. `error_um` and
`x_arrival_residual_um` still carry it, which is how it was found in the first
place. Do not turn the held axis into an accuracy gate.

### Which XY dispatch sites get the contract

design/56 reached the single-axis tools and not every Z path, and `autofocus.py`
had to be folded in three blocks later. Enumerate now:

| Site | Decision |
|---|---|
| `move_stage_xy` (`tools.py:2744`/`2746`) | **contract** — the register row |
| `MicroscopeController.set_xy` (`controller.py:1046`), which calls itself *"the single seam every XY move should use"* and today is a bare write plus `wait_for_device` | **contract** — mirror `set_z` (`:1052`), which already has it |
| `_run_protocol_at` (`tools.py:6314`), per-position XY | **contract** — the tile path's own analogue of the per-position Z straggler CLAUDE.md still names |
| hook typed actions (`hook_decisions.py`) | **none** — named single axes only, no XY route exists |

**Follow the Z spelling exactly, and add no third one.** `move_stage_z` and
`set_z` each run guard → `stage_move_tolerance` → `read_stage_start_position` →
dispatch (typed on failure) → `settle_stage_move`. The XY pair does the same
with the XY helpers; the shared code is those helpers, not a new wrapper. A
relative move keeps dispatching `set_relative_xy_position` and is settled
against the resolved absolute target, which is what `move_stage_z` does.

**The tile path's cost was weighed, not assumed.** The contract adds roughly
0.1–0.15 s per position — three stability samples across a 0.1 s window plus its
bridge round trips — so about +150 s on a 1,000-position scan. Operator decision
2026-08-30: include it. A tile scan that exposes at a position the stage never
reached is the same defect class design/56 fixed for Z, and leaving one site out
is precisely how `autofocus.py` came to be folded in three blocks later.

Reading the start through `read_stage_start_position` is what makes a down link
raise the typed contract *before* dispatch rather than an untyped bridge
exception — block 66's control limb found that on M2 and it applies here
unchanged.

### Emitters move with their tools

Four emit sites render XY motion: `move_stage_xy`'s `@emits` lambda
(`tools.py:2722`), `_emit_center_feature` (`:337`), `_emit_go_to_position`
(`:466`), `_emit_multiposition` (`:563`). Each must match the tool it
reproduces — **an emitted step must not be stricter than its tool** (63a), and
after this block it must not be *looser* either. `_stage_move_contract_source`
already inlines the single-axis contract with `inspect.getsource`; extend it,
never re-write the loop in the emitter.

### `center_feature` accepts unverifiable corrections

A final centring correction is routinely smaller than the 2.0 µm floor, so it
will report `arrival_unverifiable: true`. That is correct and must not refuse:
the loop's *next* residual is the response evidence, and design/67's
rising / plateaued / still-falling classification is already the detector. The
sub-band case is design/66's open cross-plane row in its XY form; the difference
is that here a subsequent measurement exists.

### The typed exception has to survive to the boundary

`StageMoveError` now escapes `move_stage_xy`, which is called from
`calibrate_stage_to_camera` (`tools.py:5297`–`5303`), `center_feature`
(`:5522`), `_run_protocol_at` and the navigation path. Block 60a returned four
defects that were all one shape — a broad `except Exception` between a typed
failure and `execute_tool`. The question is not "did I raise it" but **who
catches `Exception` between here and there**, answered by a parameterized test
over every XY-moving caller asserting the observable effect: no further stage
move is dispatched, counted.

## Required tests

1. A stage that reports not-busy while still short of target: the tool waits and
   only then reports; `measured_um` is never the requested value.
2. Per-axis banding — a large X move with a held Y proves Y is gated on the
   floor, not on X's relative band. Watched to fail against a `hypot` gate.
3. A single axis that never responds raises `XYStageMoveError` naming that axis;
   the other axis having arrived does not rescue it.
4. A down link on a relative move raises the typed contract with
   `requested_um: None` and no fabricated coordinate, before any dispatch.
5. Every XY-moving caller: the typed error reaches `execute_tool`, and no
   further move is dispatched. Parameterized over the callers, not one test per
   site.
6. A declared `stage.x_move_tolerance_um` / `y_move_tolerance_um` reaches the
   band as `configured` / `configured_accuracy`, per axis and independently.
   The schema refusal at `safety.py:485` is deleted in the same commit.
7. Emitted scripts for all four sites **exec** against a fake and dispatch the
   same waits — compiling is not running (block 52b).
8. `center_feature` completes normally when its corrections are sub-band and
   report `arrival_unverifiable`.

## Rig gate — M2

A program, not a runbook, dry-run against a bridge-shaped fake before it ships
(`design/59` — a `list()` over a Core collection passes every naive fake and
fails on every rig). M2, because design/29 measured its ~0.8 µm quantization and
`gate64-m2`/`gate64b-m2` give this block its priors for free.

| Limb | Mechanism |
|---|---|
| 0 | the XY stage is readable and moves — measured, not assumed |
| A | a commanded move settles: `within_tolerance`, both residuals, `elapsed_s` |
| B | per-axis bands in the record differ for an asymmetric move — the mechanism, not the outcome |
| C | **the control that must fire** — a blocked or disconnected axis raises `XYStageMoveError` naming it, with `start_um` present |
| D | a `center_feature` run's history: corrections sub-band and `arrival_unverifiable`, loop still converges; read `residuals_px` as design/67's free corroboration |
| E | the exported script carries the same contract and execs |

Limb C is the limb the trip exists for. A limb that cannot fail is not a
criterion, and a limb that could not run its mechanism reports **NOT
EXERCISED**, never a pass.

## Out of scope

- Accuracy, repeatability or step-size discovery. design/66 built that machinery
  and deleted it.
- `tol_px` defaults in `center_feature` (design/67 closed the reporting; the
  defaults stay).
- The cross-plane Z non-response row (design/66) — still owned by the next
  Z-motion integrity block.
