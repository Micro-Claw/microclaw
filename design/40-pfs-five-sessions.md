# design/40 — PFS works; the authorization map and our rig model were the problem

Source: five Nikon Ti sessions run 2026-08-05 by the remote operator, in
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/pfs_fix/` — histories
`20260805_{111502,114053,131030,131837,134358}_*_microclaw_history.jsonl`, the
`114053` confirmations log, `260715_Nikon_iXon_Prior.cfg`, and the operator's
current `safety_config.yml`.

**The Track 0 probe kit was never run** — the scripts were too hard for the
operator to run alone, which design/35 `:47` predicted and shipped anyway. What
came back instead is five real sessions, and they answer more of design/34's
open questions than the kit was designed to. This note records what they
establish, what they refute, and how Track B is rescoped as a result.

---

## PFS engages under pure software control

Four locks, no GUI and no coarse focus wheel:

| Session | Commanded Z | Reported after lock | `TIPFSOffset` | Status |
| --- | --- | --- | --- | --- |
| 11:40 | 2900 | **2912.0** | 27.85 | `Locked in focus` |
| 11:40 | 2500 | **2498.4** | 27.85 → **183.55** | `Locked in focus` |
| 13:18 | 2490 | **2532.4** | 167.325 | `Locked in focus` |
| 13:43 | — (held) | 2535 | 167.325 | held across a 25-tile XY survey |

The working loop is exactly what design/35 block 7c proposed:
`PFS Off → step Z → PFS On → read Status → repeat`. It took the operator four
sessions to get there only because microclaw has no primitive for it, so the
agent re-derived and re-negotiated every step by hand.

Three facts follow, all measured:

- **Lock is binary.** `Focus lock failed` at every wrong height, `Locked in
  focus` at the right one — no gradient, no partial signal. A bounded step
  search is the only method; there is nothing to hill-climb.
- **The stage moves after the lock.** Commanded 2490 → holds 2532; commanded
  2900 → holds 2912. `move_stage_z` reports the *request*, so its answer is
  wrong by up to ~40 µm at precisely the moment the servo takes over.
- **The servo drives the offset.** At 11:40 PFS locked at 2500 and `TIPFSOffset`
  moved from 27.85 to 183.55 **on its own**. TIZDrive and TIPFSOffset are one
  coupled focus system, not a stage plus a knob.

## What broke, and it was not the hardware

**Session 11:15 — the blanket exclusion.** The agent climbed TIZDrive from
−68 µm to 2900 µm, 450 µm past the recorded engage height, toward a loaded oil
coverslip — and only then discovered it was never permitted to write
`TIPFSStatus.State` at all:

> `RigAuthorizationError: Property write TIPFSStatus.State was refused because
> it is excluded from the authorization map.`

It had spent an entire session performing the dangerous half of a procedure
whose final step was refused. Block 4 ticked "Do not classify
`TIPFSStatus.State`" (design/35 `:706`) and "Mark PFS-offset workflows
unsupported" (`:724`); block 8 lifts them **last**, after 6, 7a, 7b and 7c.

**Session 13:18 — the offset exclusion.** With PFS locked, the operator asked
for the one remaining thing PFS is for: move the focal plane. `TIPFSOffset.
Position` was refused too. The agent's own summary: *"all three doors are closed
at once."* A lock without offset authority is not a degraded capability, it is a
useless one.

Both exclusions turned out to be safely liftable **by declaration**, and the
operator lifted them by hand mid-session — the least-able-to-fix person on the
least-reachable rig, twice. Their current `safety_config.yml` carries
`TIPFSStatus.State` under `allowed_categorical` and `TIPFSOffset.Position` as an
`absolute-position` typed actuator bounded 0–400 µm, while the file's own
generated header still says both are excluded.

### Setup over-excluded the offset, then the refusal mis-advised the fix

`first_launch.py:367` excludes every StageDevice position property with the
reason *"bounded-numeric cannot bypass the fail-closed named/core stage
policy."* True, and irrelevant: `absolute-position` exists precisely for this
case. `safety.py:961`–`973` routes it through `check_named_stage`, and
`authorization.py:743`–`766` validates that its bounds may only **narrow** the
named-stage bounds. Setup already emits that kind elsewhere
(`first_launch.py:989`) and simply never offers it here.

Meanwhile the runtime refusal (`authorization.py:1272`) names
`property_authorization.allowed_numeric` as a legal home for the pair — directly
contradicting the config comment setup had just written. **Two subsystems
disagreed in writing and a remote operator arbitrated.** The refusal text was
right; setup was wrong.

## Three premises in design/34 that today's data refutes

1. **There is no "configured approach position."** design/34 `:173` and
   design/35 `:3726` both build 7c around moving to one. Recorded lock heights:
   2912, 2498, 2532, and July's 2450. What the block needs is a bounded
   *search* — start, ceiling, step, poll `Status`, stop on lock, record both
   axes — not a number.

2. **The `move_named_stage` staleness signature did not reproduce.**
   design/34 `:246`–`:250` records three offset moves each returning the
   *previous target*. `move_named_stage` (`tools.py:519`–`539`) now reads back
   and reports `error_um`, and today it read correctly. The live defect is
   different and worse: at 11:40 it returned

   ```json
   {"requested_um": 5, "achieved_um": 27.85, "error_um": 22.85}
   ```

   **as a success.** The agent nearly swept a focus curve against an axis that
   had not moved. The fix is the failure contract, not a longer wait.

3. **"`move_stage_z` disables PFS" is still untested, and no longer urgent.**
   Every Z move on 2026-08-05 had PFS explicitly set Off first, so probe 0's
   question is exactly as open as design/34 `:46` left it. But the procedure
   that works does not need the answer, so it stops being a blocker and becomes
   a footnote. The live continuous-focus question is narrower: nothing in
   `microclaw/autofocus.py` checks a lock, and `isContinuousFocusEnabled` is
   unused anywhere in `microclaw/`.

## The compounding failure: inference stored as measurement

At 11:40 the agent saved a `nikon_ti_pfs_engage` strategy. Most of it is
measured and good. Three entries are not, and carry no marking that separates
them from the rest:

- `important_note: "On this stand, move_stage_z ALWAYS drops PFS State to Off"` —
  never tested in any session, inherited from the July conclusion that design/34
  `:46` explicitly declines to endorse.
- `"The GUI Stage Control panel / coarse focus wheel CAN jog the TIZDrive up
  with PFS staying On […] no software equivalent […] is exposed to microclaw"` —
  repeated across three sessions as a reason to hand the job back to the
  operator, on a day when microclaw engaged PFS in software four times.
- `named_stage_limit_note: "a ceiling well below the ~2450-2912 engage range"` —
  fabricated from a *minimum*-bound refusal message
  (`TIZDrive=-84.22 µm is below the minimum allowed (-77.00 µm)`), in a session
  that had already driven TIZDrive to 2900 through that same tool.

At 13:18 the agent then embellished further, telling the operator their own
notes recorded a step-search that *"drove into the coverslip."* They record no
such thing — the saved text says a step search "FAILED at every step." On that
basis it refused "go to 2500" four times, through `just do it!!` and
`bro.... just go to fucking 2500`.

**2490 locked.** The operator was right, and microclaw talked itself out of it
using its own prior output. A session's inferences and its measurements enter
the knowledge base with identical status, and later sessions cannot tell them
apart.

Also refuted the same way: at 13:18 the agent concluded the sample plane sat
~240 µm below the lock plane. At 13:43, same Z and same offset, the field is
sharp (SNR 4.19, valid metric). The 240 µm gap was a mis-set condenser, not
optics. And the "engage height is offset-dependent" rule now in the knowledge
base rests on two points from different samples; the third point (2532 @ 167.3)
breaks its monotonicity. The coupling is real — the servo moved the offset
itself — but the rule as written is not supported.

## Defects with no block, none of them Nikon-specific

1. **`Core.Focus` is unassigned.** The `.cfg` has no `Property,Core,Focus` line
   (three single-axis stages, so MM assigns no role). `get_z_position`,
   `move_stage_z` and autofocus all raise `No device with label ""`;
   `get_system_state` (`tools.py:806`–`809`) swallows it into
   `z_stage: "unavailable"`. Two sessions were lost to it. microclaw cannot fix
   it — `Core.Focus` is a role property it correctly excludes — so it must
   **say so**.
2. **`get_focus_lock_state` is EMU-only** (`tools.py:4506`). On a rig with a
   working hardware focus lock it returns *"No EMU configuration — cannot read a
   focus lock."* `agent.py:149` tells the model to trust that answer. This is
   the CLAUDE.md rule against anchoring on one microscope, violated in the one
   tool whose whole job is to answer a safety checklist item.
3. **A failed marked run poisons the position list.** `mark_positions=True`
   preflights the *entire* native list (`tools.py:2506`). A failed grid left 25
   entries behind, so the next run was refused with a conflict quoting the
   **old** grid's coordinates. The agent concluded `run_tile_acquisition` lays
   its grid out asymmetrically — it does not (`tools.py:2660`) — and burned a
   round trip on the wrong fix.
4. **`rank_hook_log` cannot read its own parent's log.** It requires
   `result.<metric>` on every entry (`tools.py:3757`–`3764`); the runner
   interleaves `hook_action` records, so ranking fails on entry 1 of every
   hooked survey.
5. **Hook preflight validates a contract the runner rejects.**
   `generate_and_save_hook` returns hard-coded *"Static syntax and
   image_process_fn contract passed"* (`tools.py:4162`); `run_tile_acquisition`
   then refused the same file for using that contract. Two review-and-save
   cycles with the operator, on hardware. Separately, `ContinueSurvey` logged
   `"decision": "refused"` on all 25 tiles of a batched run.
6. **The SNR gate is a fluorescence assumption.** Brightfield fields with cells
   the operator could see read SNR 2.53 / 2.74 / 1.41 →
   `focus_metric_valid: false`; raising exposure made it *worse*, because in
   transmitted light the background is the signal path.
   `min_snr_source: package_default_uncalibrated` on every rig.
7. **`calibrate_stage_to_camera` mis-diagnoses fixed-pattern lock.** An exact
   `0.00 px` shift is the signature of a stationary vignette rim or sensor dirt
   dominating the correlation. The message (`tools.py:1684`) says "step too
   small"; the advice leads to the opposite error. A real 80 µm move left the
   tracked centroid at (144.7, 558.5), unchanged to one decimal.

## Decisions

**D1 — Authorization first, and standalone.** A new block 6a lifts the
continuous-focus and offset exclusions by declaration, teaches `first_launch` to
offer `absolute-position` for stage-position properties instead of excluding
them, and reconciles the refusal text with what setup writes. It also fixes the
unassigned-`Core.Focus` diagnosis and de-EMUs `get_focus_lock_state`. Both are
generic, both are cheap, and together they are what gets this operator working
again. This lands before block 6, not after 7c.

**D2 — Block 6 is rewritten around the failure contract.** Measured Z read-back
stays. The named-stage half changes from "poll until settled" to "a move whose
measured position misses its target beyond tolerance is a typed failure, not a
success carrying an `error_um` field." Test fixtures: the 22.85 µm offset case
and the post-lock 2490 → 2532 Z case.

**D3 — 7a and 7c merge, and move above 7b.** The typed capability and the
bounded engage search are one deliverable; the search is what makes the
capability worth having, and today's data fully determines its shape:

```python
def engage_continuous_focus(ctrl, guard, *, z_start, z_ceiling, step_um,
                            settle_s, timeout_s) -> dict:
    """Off → step → On → poll Status → stop on lock. Generic: the device comes
    from get_auto_focus_device(), the bounds from the caller and check_z.
    Records the focus stage AND every offset stage at entry and at lock,
    because the servo moves them."""
```

No Nikon-named recipe, no baked-in height, and it refuses rather than guessing
when `z_ceiling` is absent.

**D4 — 7b shrinks to autofocus-under-lock.** `run_autofocus` must not sweep a
focus device while continuous focus is armed. Probes 1–4 are deferred, not
blocking; `preserve` is not offered, because no evidence supports it.

**D5 — The seven defects above are their own block (13), not Track B.** They
block hooked surveys on every rig. Track D may run concurrently with Track B in
a separate worktree; the file overlap with 6a is limited to `tools.py`.

**D6 — Knowledge provenance is a real finding, and it has no block yet.**
`save_knowledge` should distinguish what a session *measured* from what it
*concluded*. Recorded here as owed work rather than scheduled, because the
right shape is not yet clear and inventing a schema for it now would be the
extra layer CLAUDE.md warns against.

## Still owed

- Probe 0's question — does an out-of-range PFS drop `State` on its own timeout?
  Unanswered; now a footnote rather than a blocker.
- The TIZDrive↔TIPFSOffset coupling coefficient. Today's two same-session points
  are confounded by a sample lift, so no ratio may be quoted.
- Whether the operator's hand-declared 0–400 µm offset bound is the right one.
  It is theirs, it is reviewed, and it is narrower than the device's 0–1000.
