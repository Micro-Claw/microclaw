# design/40 — PFS works; the authorization map and our rig model were the problem

> **CLOSED 2026-08-18 — Track B closed with it. Do not implement from this
> document.** The coordinator got on the Nikon and PFS engages, disengages and
> takes an offset move from inside a microclaw session, under the operator's
> hand-declared `safety_config.yml`. Blocks 6, 7a, 7b and 8 are closed unbuilt;
> 6a is dropped unmerged and its branch deleted. **The five defects this document
> found that are *not* Nikon-specific are still live on `main`** and are tracked
> in `design/35-usability-and-pfs-checklist.md` under "The five that outlived
> Track B" — start there, not here. What stays valid below is the measured
> session evidence: the engage loop, the four lock heights, the refuted premises,
> and the cross-rig regression bar.

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

## Measured later: the offset is writable under an active lock

Block 56's gate, 2026-08-19, with PFS armed and `TIPFSStatus.Status` reading
`Locked in focus`: `move_named_stage` moved `TIPFSOffset` from 1.0 to 21.0 and
**reached it exactly** (`within_tolerance: true`, independently re-read at 21.0).
So on this rig a commanded offset write is **honoured while the servo holds**, not
overridden — which is the documented way to move the focal plane under PFS, and
it is now measured rather than assumed.

Two corrections follow, both to inferences drawn from the 11:40 session:

- **27.85 µm is not a mechanical floor.** With PFS off, `TIPFSOffset` was
  commanded to 0.0 — the configured minimum — and reached 0.0 exactly.
- **The servo does not override a commanded write.** It drives the offset when
  nothing else does (27.85 → 183.55 on lock), which is a different statement.

What 11:40 actually was: a **premature read-back**. The axis takes ~0.9 s to
settle and reports busy throughout; the old code read it once, immediately after
`wait_for_device`, and got the pre-move position. **Probe 0 remains unanswered** —
this moved the *offset* under lock, not the focus drive.

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

- **Lock is binary** — **SUPERSEDED 2026-08-23 by design/56 §"What the session
  already measured"; kept for the round history.** What was measured here was
  `Focus lock failed` at every wrong height and `Locked in focus` at the right
  one, so a bounded step search looked like the only method. That was true of the
  *instrument used*: arm-and-see at each height, which asks the lock a yes/no
  question and can only get a yes/no back. Reading `TIPFSStatus.Status` without
  arming shows a **three-level ordinal** — `Out of focus search range` →
  `Within range of focus search` → `Locked in focus` — and the middle value is
  readable while the lock is not holding, so there *is* a band to find and its
  width was measured at ~29 µm on a 60× oil objective. A second Nikon spells the
  same thing `PFS in Range` → `In Range`. **Do not quote "nothing to hill-climb"
  forward**; design/56's probe finds that band at zero exposures.
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
capability worth having, and today's data fully determines its shape. See
§"Code stubs" below. No Nikon-named recipe, no baked-in height, and it refuses
rather than guessing when `z_ceiling` is absent.

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

## Every fix here must leave Demo, M2 and M5 exactly as they are

**This is the binding constraint on all four blocks, not a closing caveat.**
Everything in this note was learned on one unusual rig. CLAUDE.md's first rule is
that `microclaw/` must work for a generic Micro-Manager installation; a fix that
makes the Nikon work by assuming a Nikon is a defect even if the Nikon gate
passes. Three of these rigs are reachable and one is not, so each block's gate
names which of them it must be measured on.

What each rig has that these changes could break:

| Rig | Shape that matters here | The regression to prove absent |
| --- | --- | --- |
| **Demo** | `core_device_assignments`: focus `Z`, xy `XY`, **autofocus `Autofocus`** (`DAutoFocus`, DemoCamera). No `named_stages` beyond the defaults; camera returns one frame regardless of position. | 7a's capability must work here *as plumbing* — and must not be **believed** here. `DAutoFocus` is expected to report locked whenever enabled; confirm that, and if so the demo gates the call paths only, never lock semantics. A self-confirming probe is not evidence. |
| **M2** | `named_stages: []` **on purpose** — SmarActZ and the TIRF stage are absent from the config and must not be movable by name at all (`design/29-block9-m2-safety-config.yaml:116`–`123`). No EMU. | 6a must not turn "setup offers `absolute-position`" into a way around an empty `named_stages`. See the hole below — this is the one place these changes can make a rig *less* safe. |
| **M5** | EMU + MicroFPGA; focus lock read through the EMU map with a `qpd` block; no `Channel` group; no core shutter; hazardous actuators are `GenericDevice`. | `get_focus_lock_state` must return **byte-identical** output on M5. The new core-autofocus path is a fallback reached only when the EMU map has no `focus_lock`, never a preferred source. |
| **Nikon Ti** | Three single-axis stages, `Core.Focus` sometimes unassigned, PFS as `AutoFocusDevice` + offset `StageDevice`. | The rig all of this is *for*. It is the only one that cannot be re-tested quickly — assume one round trip per block and ship complete. |

### The hole 6a must close, not open

`safety.py:961`–`973` routes an `absolute-position` write through `check_z`,
`check_xy`, or `check_named_stage` **only if** the device is the core focus
device, the core XY device, or already has a `named_stages` entry. A typed
`absolute-position` declaration on any *other* stage falls through every branch
and is gated by nothing but its own declared min/max — while `check_named_stage`
(`safety.py:1165`) refuses that same device outright, because "a stage with no
`named_stages` entry may not be moved at all."

The Nikon config never exposed this: `TIPFSOffset` happens to be in
`named_stages` too. **M2 is the shape that would break** — an empty
`named_stages` is a deliberate refusal there, and an `absolute-position` entry
would quietly reinstate motion on a stage the operator declared unreachable.

So 6a's rule is not "offer the kind." It is: **offer `absolute-position` only
for a stage that already has a reviewed travel entry, and make the validator
reject one that does not.** That is strictly stricter than today on every rig
except the one where it unblocks the offset.

### Cross-rig regression bar, every block

- [ ] Full non-hardware suite green on updated `main` before the branch is
      reviewed, and re-run by the coordinator rather than trusted from a report.
- [ ] Replay the **captured** demo inventory
      (`tests/fixtures/block4_demo_inventory_20260801.json`) and the M5
      `config.uicfg` fixture through any changed setup or authorization path,
      and diff the emitted profile against what it emits today. A synthetic
      fixture has manufactured a fake defect and hidden a real one before.
- [ ] For a changed refusal or classification: show the *unchanged* verdict for
      M2's empty `named_stages` and M5's EMU focus lock in the same test run.
- [ ] Name in the block's report which rigs were measured and which were argued
      from fixtures. Those are different claims.

## Code stubs

Shapes, not implementations — enough to fix the contract before a runner starts.

### 6a — authorization, roles, and the lock read

```python
# first_launch.py — _metadata_default. Replaces the blanket "x" at :367.
if _is_stage_position(item):
    if _has_reviewed_travel(item, assignments, named_stages):
        return ("p",                       # -> kind: absolute-position
                "MM identifies a stage position property on a stage that already "
                "has a reviewed travel entry; absolute-position narrows it",
                technical_range)
    return ("x",
            "MM identifies a stage position property on a stage with no reviewed "
            "travel entry. Declare named_stages for it first, or leave it "
            "excluded — a typed bound alone would not fail closed.",
            None)


# authorization.py — inside the existing `if policy.kind == "absolute-position"`
# branch (:743), after axis_policies is built. This is the M2 guard.
if not axis_policies:
    errors.append(
        f"Typed absolute-position {identity.device}.{identity.property} names a "
        "stage that is neither the core focus/XY device nor a declared named "
        f"stage, so no travel bound governs it. Declare named_stages[{identity.device}] "
        "first; a typed entry may narrow a travel bound, never create one."
    )
```

```python
# controller.py — one place that turns an unassigned role into a real answer.
class CoreRoleUnassigned(RuntimeError):
    """Micro-Manager has no device in this role. Not a hardware fault."""


def require_focus_device(core) -> str:
    label = core.get_focus_device()
    if label:
        return label
    raise CoreRoleUnassigned(
        "Micro-Manager has no Core-Focus device assigned, so there is no Z axis "
        "to drive. Set it in Devices > Hardware Configuration Wizard, or in the "
        "Device Property Browser under Core-Focus. microclaw cannot set it for "
        "you: Core role properties are excluded because writing one re-aims "
        "every reviewed stage bound at a different device. Single-axis stages "
        f"loaded on this rig: {', '.join(single_axis_stages(core)) or 'none'}."
    )
```

```python
# tools.py — get_system_state (:806). Same for get_z_position, move_stage_z,
# run_autofocus: name the cause once, in the message, not four times in the model.
except Exception:
    state["z_stage"] = (
        "unavailable — no Core-Focus device is assigned (see get_z_position)"
        if not ctrl.core.get_focus_device() else "unavailable"
    )
```

```python
# tools.py — get_focus_lock_state (:4506). ADDITIVE. The EMU branch is
# unchanged and stays first, so M5's answer is byte-identical.
def get_focus_lock_state(ctrl, guard) -> dict:
    props, params = _cached_emu_properties(ctrl)
    if props:
        lock = build_emu_map(props, params)["focus_lock"]
        if lock is not None and "device" in lock:
            return {...}                      # exactly today's payload, incl. qpd
    device = ctrl.core.get_auto_focus_device()
    if not device:
        return {"engaged": None,
                "reason": "No EMU focus lock and no Core-AutoFocus device "
                          "assigned — this rig reports no hardware focus lock."}
    return {"engaged": bool(ctrl.core.is_continuous_focus_enabled()),
            "locked": bool(ctrl.core.is_continuous_focus_locked()),
            "device": device, "source": "core-autofocus"}
```

### 6 — the move failure contract

```python
@dataclass(frozen=True)
class MoveOutcome:
    requested_um: float
    measured_um: float
    tolerance_um: float
    within_tolerance: bool
    elapsed_s: float
    device_status: str | None


def settle_to_target(core, device, target, *, tolerance_um, timeout_s,
                     poll_s, consecutive) -> MoveOutcome:
    """Poll until |measured - target| <= tolerance for `consecutive` reads.

    Tolerance-of-target is the gate, not stability: this adapter's Busy() can
    clear before motion starts, so a stability-only check passes immediately at
    the OLD position — the design/34 :236 failure exactly.
    """


# move_named_stage and move_stage_z share one return shape. A miss is a failure.
outcome = settle_to_target(...)
if not outcome.within_tolerance:
    return {"error": f"{device} did not reach {target:g} µm.",
            "kind": "move_not_achieved", **asdict(outcome)}
return {"device": device, **asdict(outcome)}
```

Two fixtures, both from real sessions: a fake that stops at a hard floor
(`requested 5 → measured 27.85`, must fail, not succeed with `error_um`), and a
fake whose `Busy()` clears before motion starts (must fail the target check
while passing a stability-only one).

### 7a — capability and bounded search

```python
def engage_continuous_focus(ctrl, guard, *, z_start, z_ceiling, step_um,
                            settle_s=0.5, timeout_s=60.0) -> dict:
    """Off -> step -> On -> read status -> stop on lock, ceiling, or timeout.

    Generic: the device is get_auto_focus_device(); the bounds are the caller's
    and check_z's. z_ceiling has no default, so an unbounded climb toward a
    coverslip cannot be requested by omission.

    Lock is binary on the hardware measured in design/40 — there is no partial
    signal to hill-climb, so this steps and asks, it does not optimise.
    (Superseded: see the "Lock is binary" bullet above. The status property is a
    three-level ordinal and design/56's probe reads the band directly. This
    stub's arm-and-see loop is not the method to build.)
    """
    device = ctrl.core.get_auto_focus_device()      # raises if unassigned
    entry = _read_focus_axes(ctrl)                  # focus stage AND every offset stage
    tried: list[dict] = []
    z = z_start
    while z <= z_ceiling:
        guard.check_z(z)
        _set_continuous_focus(ctrl, False)
        move_stage_z(ctrl, guard, z, absolute=True)  # block 6's measured move
        _set_continuous_focus(ctrl, True)
        status = _await_lock(ctrl, device, settle_s, timeout_s)
        tried.append({"z_um": z, "status": status.raw})
        if status.locked:
            return {"locked": True, "device": device, "tried": tried,
                    "entry_axes": entry, "lock_axes": _read_focus_axes(ctrl)}
        z += step_um
    return {"error": "Continuous focus did not lock below the ceiling.",
            "kind": "engage_not_locked", "device": device, "tried": tried,
            "z_ceiling": z_ceiling, "entry_axes": entry,
            "final_axes": _read_focus_axes(ctrl)}
```

`_read_focus_axes` records the focus stage **and** every offset stage, because
the servo moves them: a lock at 2500 pulled `TIPFSOffset` from 27.85 to 183.55
by itself. A result that reports one axis describes half the machine.

### 7b — autofocus must not fight an armed servo

```python
# autofocus.py — before the sweep.
lock = get_focus_lock_state(ctrl, guard)
if lock.get("engaged") is True:
    return {"error": f"Continuous focus is engaged on {lock.get('device')}. A "
                     "software sweep would fight the servo and duplicate what it "
                     "already does. Disengage it, or use the lock you have.",
            "kind": "continuous_focus_engaged", "focus_lock": lock}
```

**`engaged is True`, never truthiness.** `None` means "this rig reports no lock"
— on Demo, M2 and any rig with no EMU map and no autofocus device — and must let
autofocus run exactly as it does today. Getting this wrong disables autofocus on
every rig that is not the Nikon, which is the whole failure mode this section
exists to prevent.

### 13 — the platform defects

```python
# tools.py :2506 — mark_positions preflight. Separate what this call would add
# from what was already there, so a failed run cannot wedge every later one.
existing, conflict = _preflight_native_positions(ctrl, guard, ...)
if conflict:
    conflict["position_list_conflict"]["source"] = "pre-existing entries, not this call"
    conflict["position_list_conflict"]["hint"] = (
        "clear_position_list() or resolve the listed indexes; the grid this call "
        "would add was not evaluated."
    )
    return conflict

# tools.py :3757 — rank_hook_log. The parent's own runner interleaves these.
for i, entry in enumerate(entries):
    if entry.get("schema") != "microclaw.analysis-observation/v1":
        continue          # hook_action and other runner records are not rankings

# tools.py :4162 — one contract, checked once. The runner and the preflight must
# call the same validator; the message must name whichever it actually ran.
"preflight": f"Static syntax and {contract_name} contract passed. ..."

# tools.py :1684 — calibration. A zero shift is a fingerprint, not a small shift.
if mag < 0.5:
    return ("the commanded move produced no image shift at all (|shift| "
            f"{mag:.2f} px). A step-size problem produces a small shift, not "
            "none: this is the signature of a stationary feature dominating the "
            "correlation — a vignette rim, sensor dirt, or a fixed reflection. "
            "Crop the ROI to the illuminated centre and retry before changing "
            "step_um.")
```

## Orchestration checklist

**Scope lives in `design/35`; process lives in `CLAUDE.md` §"The block
workflow"; this board tracks *state* only.** Where any two disagree, CLAUDE.md
wins on process and design/35 wins on what a block contains — fix the other
document and say so. Do not restate item text here; it will drift.

An orchestrating session works one row at a time, top to bottom, except that
block 13 may run in parallel in its own worktree.

| # | Block | Branch | State | Next action |
| --- | --- | --- | --- | --- |
| 1 | **6a** authorize the focus system | `design34/focus-system-authorization` | **pushed at `4994f3e`, awaiting the rig gate** | the operator runs the Nikon gate; nothing here |
| 2 | **13** platform defects (parallel) | `design40/platform-defects` | **assigned 2026-08-05 from `03dcea0`** | implementer at work in `../microclaw-13` |
| 3 | **6** measured read-back + failure contract | `design34/measured-position-readback` | blocked on 6a | — |
| 4 | **7a** capability + bounded search | `design34/continuous-focus-capability` | blocked on 6a | — |
| 5 | **7b** autofocus under lock | `design34/continuous-focus-policy` | blocked on 7a | — |
| 6 | **8** Phase 5 addendum | `design33/phase5-continuous-focus` | blocked on 6, 7a, 7b | — |

Update the **ledger in design/35**, not this table, when a block moves — the
ledger carries commits, gate results and merges. This table exists so a cold
session can see the order and the parallelism at a glance.

### Per-block loop (the ten steps, specialised)

Read CLAUDE.md's ten steps first; these are the additions specific to design/40.

1. **Before assigning:** state which rigs the block's gate needs, from the table
   in §"Every fix here must leave Demo, M2 and M5 exactly as they are". Only 6a
   and 7a need the Nikon; 6 and 13 have Any-rig gates that Demo or M5 satisfy.
2. **In the runner's prompt:** hand it the block's design/35 section, the stub
   above, and the cross-rig regression bar. Write the prompt to the scratchpad,
   not to `design/`.
3. **On review:** re-run the suite yourself. Check the fixture replay actually
   ran, and that the report distinguishes measured rigs from argued ones.
4. **Runbook on the block's branch**, pinned with
   `git merge-base --is-ancestor`. The Nikon operator reads it on the rig, and
   they could not run the last thing we shipped — **prefer runbook steps that
   are ordinary microclaw usage over steps that run a script.** That is the
   whole lesson of Track 0.
5. **After the gate:** if the Nikon result contradicts something in this
   document, fix this document in the same merge. design/40 is evidence, and
   evidence is revisable.

### Session-boundary handoff

When a session ends mid-block, the next one must be able to resume from the
remote alone. Before stopping, ensure:

- [ ] the ledger row carries the branch and the start commit;
- [ ] the branch is **pushed**, not just committed;
- [ ] this board's State column is current;
- [ ] anything learned that is not in a commit is in `design/prompts.md`.

## Still owed

- Probe 0's question — does an out-of-range PFS drop `State` on its own timeout?
  Unanswered; now a footnote rather than a blocker.
- The TIZDrive↔TIPFSOffset coupling coefficient. Today's two same-session points
  are confounded by a sample lift, so no ratio may be quoted.
- Whether the operator's hand-declared 0–400 µm offset bound is the right one.
  It is theirs, it is reviewed, and it is narrower than the device's 0–1000.

## What block 13 shipped, and what it left (merged 2026-08-06)

All seven defects in §"Defects with no block" that belonged to block 13 shipped.
M5 gate 2026-08-06: **G1, G2, G4, G5 PASS; G3 skipped** (M5 has no
transmitted-light path).

| defect | shipped as | measured |
| --- | --- | --- |
| 3 — failed marked run poisons the list | guard **before** marking; rollback of only this call's own labels if a later refusal lands | M5: a grid point rejected by the XY guard was never written; list 3 → 4, next run clean |
| 4 — `rank_hook_log` cannot read its parent's log | skips schema-less runner records; invalid rows listed, valid rows still ranked | M5: `ranked_entry_count 4`, `invalid_entry_count 0`, from its own survey's log |
| 5 — preflight validates a contract the runner rejects | one validator, message names the contract checked (design/32) | off-rig |
| 6 — SNR gate is a fluorescence assumption | frame-intrinsic polarity refusal; semantics stated once in design/25 | **not measured — G3 owed** |
| 7 — calibration mis-diagnoses fixed-pattern lock | exact-zero shift named as stationary-pattern correlation, ordered before the step-size branch | off-rig |
| F4 saturation | invalidates SNR *and* focus | M5: 80 ms, 0.0135% → refused |
| F5 `axis_selection` | singleton axes default | M5: three calls became one |

**Deliberately left.** Saturation inside the autofocus *sweep* — the sweep's
frames are not retained and the metric seam takes only pixels, so gating it needs
either an extra exposure or a plumbing change. design/41 files it as a concern,
not a defect. Still owed. `detect_features`' bare `snr` was also left alone: it
is a puncta-centroid tool, not a gate.

**Owed: G3.** The transmitted-light refusal is covered by unit tests and verified
off-rig (a clean dark-on-bright field: SNR refuses, `focus_metric_valid` stays
true, tenengrad 2.99e8). No reachable rig has a transmitted-light path — M5 does
not. Carried forward against the next rig that does; the Nikon is the likeliest.
