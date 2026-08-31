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

For the same reason, `move_stage_xy` reads both coordinates before
`guard.check_xy`, including for an absolute move that previously needed no
read. The read is not a command: an out-of-bounds target still refuses before
the first write, while a failed link receives the typed move contract.

### Emitters move with their tools

Four emit sites render XY motion: `move_stage_xy`'s `@emits` lambda
(`tools.py:2722`), `_emit_center_feature` (`:337`), `_emit_go_to_position`
(`:466`), `_emit_multiposition` (`:563`). Each must match the tool it
reproduces — **an emitted step must not be stricter than its tool** (63a), and
after this block it must not be *looser* either. `_stage_move_contract_source`
already inlines the single-axis contract with `inspect.getsource`; extend it,
never re-write the loop in the emitter.

An emitted `go_to_position` carries the recorded start as a literal because it
replays one resolved move and its recorded contract. Emitted multiposition code
instead reads the start at run time inside the loop, because each position has
a different predecessor and therefore a different displacement and band.

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
EXERCISED**, never a pass. It is not run by default: it needs an axis physically
blocked, so it is `--limbs C --control` after the operator does that, and the
runbook (`design/68-block68-m2-runbook.md`) carries the literal commands.

**Dry-run before shipping, and it paid for itself immediately.**
`design/68-gate-probe-selftest.py` drives the whole probe against a
bridge-shaped fake in five modes, each of which must come out a specific way —
`--shared-band` must turn limb B red, `--blocked-axis` must turn limb C green
naming Y, `--untyped-failure` must turn it red rather than crash, `--dead-stage`
must make limb 0 stand the rest down. The fake's XY stage answers `device_busy`
False throughout and converges over ~0.2 s of wall clock, because **a fake that
lands instantly lets a probe which reads once pass**, and reading once is the
defect this block exists to fix.

It found one gate defect before the rig did: **limb C commanded a move along X
only**. An operator who blocked the Y axis — whichever their rig lets them
reach — would have had a correctly blocked axis trivially satisfy its band, and
the limb would have reported "a blocked axis reported a successful arrival". A
confidently wrong verdict, on the limb the trip exists for. It now commands both
axes and lets the refusal name the one that did not respond. Limb D likewise now
states when no correction was sub-band, rather than passing for a half of its
mechanism that did not run.

## Out of scope

- Accuracy, repeatability or step-size discovery. design/66 built that machinery
  and deleted it.
- `tol_px` defaults in `center_feature` (design/67 closed the reporting; the
  defaults stay).
- The cross-plane Z non-response row (design/66) — still owned by the next
  Z-motion integrity block.


## M2, 2026-08-31 — scored from the artifacts

Two runs. Every limb has now been exercised; nothing is owed.

**Run 1 (`gate68-m2`), limbs 0/A/B/D/E — PASS.** The numbers agree with each
other, which is the check the verdict cannot give:

| Limb | Measured |
|---|---|
| 0 | commanded 20.0 µm, measured **20.20**, returned within 0.22 µm |
| A | **settled in 0.984 s**; `measured_um` `[55.7, 225.6]` for `requested_um` `[55.5, 225.7]`, residuals 0.2/0.1 against 2.0 µm bands |
| B | X moved 200 µm and earned a **20.0 µm relative** band; Y was held and kept the **2.0 µm floor** |
| D | 85.7 px entry, residuals `[86.4, 12.5, 3.04]`, centred in 2 iterations, **correction 1 sub-band** |
| E | emitted `settle_xy_move` carrying start `[35.5, 205.7]` → target `[55.5, 225.7]` |

Limb A is this block's claim on hardware: `measured_um` is **not**
`requested_um`, and the move took nearly a second to settle — a premature
read-back would have returned the entry position, which is the 22.85 µm miss
block 56 measured for Z.

Limb D is better than the design hoped for. The smallest commanded correction
was **1.5875 µm**, below the 2.0 µm floor, so it reported `arrival_unverifiable`
and the loop **still converged** to 3.04 px. That is §"`center_feature` accepts
unverifiable corrections", confirmed rather than argued. The arithmetic
cross-checks: 12.5 px × M2's 0.127 µm/px = 1.5875 µm, exactly the correction
recorded.

**Run 2 (`gate68-m2-r2`), limb C — PASS, and it took two rounds to get there.**
The operator disconnected the Core XY device (`SmarActXY`), which is the
strongest form of the test, not a degenerate one. Round 1 reported FAIL and
measured nothing; see below. Round 2, from `non_response_control.json`:

- `exception_class: XYStageMoveError` — **typed**, naming `["x", "y"]`. Block 66's
  control-limb defect, where an untyped `java.lang.Exception` escaped carrying
  none of the contract, does **not** reproduce for XY.
- `elapsed_s: 0.0` with `last_device_status: "dispatch_error: ..."` — the
  refusal came from `read_xy_start_position` **before any write was attempted**.
  The stage was never commanded.
- `requested_um: null` on a relative move with no readable start — no fabricated
  coordinate, which is design/66's rule honoured.
- `start_um: null`, both bands `floor` 2.0, both `arrival_unverifiable: true`,
  `verification_kind: response` — internally consistent, and `start_um: null` is
  the *correct* report when the start is genuinely unknown.

**Looked for and not found.** That refusal message carries the full Java stack
trace — 1208 characters, 14 newlines — and block 52b lost three rig trips to a
recorded newline breaking out of its `# SKIPPED` comment and making `ast.parse`
refuse a whole session's export. Fed the real M2 string through
`export_session_script`: the script parses and no fragment escapes its comment.

### The gate's round-1 failure was the gate, and its self-test hid it

Round 1's limb C reported `FAIL: java.lang.Exception: ... no sensor present`,
which reads as a product defect. It is a statement about the gate:
`entry = read_xy(ctrl.core)` sat **outside** limb C's `try`, and a link that is
down fails *every* bridge call, so the probe died in its own instrumentation
before `move_stage_xy` was ever called. The honest verdict for that limb was
**NOT EXERCISED**, never FAIL.

The part worth keeping is why the dry run did not catch it. `--untyped-failure`
asserted only that limb C reported **FAIL** — which it did, having died in the
same pre-read. **A criterion satisfied by the wrong mechanism**, inside the gate
written to enforce that rule on everyone else. The mode now requires the limb to
have *reached* the product (its evidence file exists only if it did) and to have
been refused by type. `CLAUDE.md`'s rule that a gate's own fake gets no review
pass held; the missing half is that a gate's own **assertion** gets none either,
and "did it report FAIL?" is exactly as weak an observation channel as design/60
block 60b's "was a confirmation raised?".

Three smaller gate defects came from the same run: limb 0 printed a raw Java
traceback at a deliberately disconnected stage instead of NOT EXERCISED; limb C
scored `start_um: null` as a defect when it is correct on a dead link; and the
control phase **overwrote** run 1's `gate68_results.json`, whose 5/6 survived
only in the operator's log. Results are now written per phase.
