# The preset route never learned schema 3's contract

> Retitled and rescoped 2026-08-14, before implementation. This began as "an
> omitted `illumination` section still restricts one thing" — the `Core.Shutter`
> refusal below. The M5 leg of block 50a's gate then produced a second refusal on
> a different branch of the same function, from the same cause, and the sweep
> this document had asked the implementer to perform turned out to be the whole
> finding rather than a footnote. The original framing is kept in "Problem" as
> the first instance; the decision is now general.

## Problem

Under schema 3, a `Channel` preset that retargets `Core.Shutter` is refused on
any rig that declares no `illumination` section — which is every rig running the
minimal document design/48 introduced. Measured on the demo machine during
block 50a's gate, 2026-08-14:

```
set_channel(preset="Cy5")
→ RigAuthorizationError: Core.Shutter retarget to 'White Light Shutter' was
  refused. Declare the target device's exact device/property/on_value/off_value
  mapping as an item under top-level `illumination.shutters`; Core.Shutter
  selects that declared device and is not itself the item to add.
```

Evidence: `~/Documents/Documents - Beyonce/Projects/Micro-Claw/50a-demo`, history
line 22, and the `safety_config.yaml` beside it.

**This is not block 50a.** That block never touched `_authorize_channel_effect`;
its diff contains no reference to `Core.Shutter` or
`is_illumination_shutter_device`, and the refusing code last changed in
`dce17a4`. Block 43n applied channels successfully on this same demo machine.
The config changed, not the code.

### It contradicts schema 3's stated contract

design/48 states the rule plainly — omitted `illumination`/`camera`/`channels`
**restrict nothing** — and its M5 gate measured and blessed the consequence:

> With no `illumination` section, the EMU laser enable went through with no
> Microclaw refusal and no enable confirmation, which is the intended schema-3
> behaviour.

`_authorize_channel_effect`'s `Core.Shutter` branch (`authorization.py:1637`) is
the single place that does not honour that. It requires
`guard.is_illumination_shutter_device(value)`, which reads
`self._c.illumination.shutters` — empty whenever the section is omitted — so the
refusal is unconditional and no configuration short of adding the section can
lift it.

### The refused route is the safer one

Measured against the demo's exact `safety_config.yaml`:

| route | verdict |
| --- | --- |
| `set_device_property('White Light Shutter', 'State', '1')` | **allowed**, no confirmation |
| a `Channel` preset retargeting `Core.Shutter` to that same device | **refused** |

`property_writes_unrestricted` is `True` (both `property_authorization` and
`illumination` are absent), `is_illumination_enable` returns `None`, and
`guard.check_property` passes. So Microclaw permits opening that shutter
directly and unconfirmed, while refusing to let a preset *select* it as the
AutoShutter target — strictly less than opening it.

This is design/49's shape exactly: a rule written for a case that does not apply
here catches a typed path, while the raw path it cannot police stays open.

### The second instance, and the real cause

M5, block 50a's gate, 2026-08-14. Evidence:
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/50a-m5`, history lines 2 and
16 — twice, on two different presets:

```
set_config_preset(group="System", preset="Camera")
→ RigAuthorizationError: Channel effect HamamatsuHam_DCAM.DEFECT CORRECT MODE
  was refused because it is unclassified or excluded.
```

Different branch, same cause, and this one names it. `authorize_property_write`
(`authorization.py:1546`) contains:

```python
if report.property_writes_unrestricted:
    return
```

**`_authorize_channel_effect` has no such early return.** It goes straight to the
map-membership test and refuses anything not classified there. So:

| route | schema 3, nothing declared |
| --- | --- |
| `set_device_property('HamamatsuHam_DCAM', 'DEFECT CORRECT MODE', …)` | **allowed** |
| the identical write inside a `System` preset | **refused** |

The shutter is not a special case. **The raw route was taught schema 3's
contract and the preset route never was**, so every effect in every preset is
judged against a map that a minimal document deliberately leaves almost empty.
`Core.Shutter` is simply the branch that fails first when a preset happens to
carry one.

That makes this a structural gap rather than two bugs, and it is why the fix
below is stated once for the whole function instead of per branch. **A
schema-3 document that declares nothing must not leave any branch behaving as
though everything were declared.**

### Blast radius

**Every config preset on every rig running a minimal schema-3 document**, which
is currently both machines we can test on. Any preset containing one property the
map does not classify refuses outright, and a minimal document classifies almost
nothing by design. Measured: `set_channel` unusable on the demo machine,
`set_config_preset` unusable on M5 for `Camera` and `Normal Mode` — the two
presets the operator says are in routine use.

Every acquisition that drives a channel axis is included, since
`_check_acquisition_channel` routes through the same expansion. Block 50a's gate
steps A4 and B1/B2 are blocked by it.

## Decision

**Give `_authorize_channel_effect` the same schema-3 early-outs its sibling
already has.** The two functions gate the same writes arriving by different
routes and must agree; every instance above is a place they disagree.

1. **Honour `property_writes_unrestricted` for preset effects**, exactly as
   `authorize_property_write` does at `:1546`. When it is true, skip the
   map-membership refusal — the map is nearly empty by design and cannot be the
   authority. **The guard checks stay**: illumination, typed actuators, and the
   stage/exposure limbs all still run, precisely as they still run for a raw
   write after that same early return. This is not "skip authorization"; it is
   "stop consulting a map the operator declined to author."
2. **Add `illumination_unrestricted` to `AuthorizationMap`**, set in
   `validate_live_rig` as `"illumination" not in parsed_config.declared_sections`
   — the idiom `channels_unrestricted` already uses one line above
   (`authorization.py:1501`). The `Core.Shutter` branch consults the declaration
   directly rather than the map, so item 1 does not reach it; it needs its own
   condition.
3. In the `Core.Shutter` branch, admit the retarget when that flag is true. When
   it is false the branch is **unchanged**: the target must be a declared shutter
   and the operator must confirm, which is block 2's undeclared-light-source
   protection and stays exactly as measured.

Items 1 and 3 are the same decision applied to the two ways this function can
refuse. Neither weakens a rig that declares things: with any `illumination` or
`property_authorization` section present, both flags are false and every path is
byte-for-byte what it is today.

### No confirmation prompt when illumination is undeclared

Deliberate, and not a judgment call: design/48's M5 gate blessed a **laser
enable** going through with no confirmation under these conditions. A shutter
retarget is strictly less than an enable, so prompting for it while the enable
passes silently would reintroduce the same inconsistency in the other direction.
The two routes must agree; that is the whole finding.

### Rejected

- **Declare `illumination` and `property_authorization` on both rigs and move
  on.** It fixes two machines and leaves the contract broken for every other
  minimal document, including every one the in-app setup will generate. The
  operator's config is not the defect — and design/48 built the minimal document
  deliberately, so telling operators to un-minimise it reverses that decision by
  the back door.
- **Drop the `Core.Shutter` branch, or the map-membership check, entirely.**
  Both are correct and load-bearing whenever the matching section *is* declared;
  block 2's and block 14's gates measured them.
- **Make `_authorize_channel_effect` call `authorize_property_write`.** Tempting,
  since the goal is for them to agree, but they are not the same check: the
  effect path also handles `Core.Shutter`, carries the confirm function, and
  refuses on classifications the raw path admits. Sharing the two early-outs is
  the fold; collapsing the functions is not.
- **Infer the shutter set from the rig instead of the declaration.** Guessing
  which devices are light sources is what the declaration exists to prevent.
- **Special-case the demo configuration's device name.** A rig fact in
  `microclaw/` (CLAUDE.md).

### Scope

`_authorize_channel_effect`, whole. The sweep this document originally deferred
to the implementer has already returned one hit — the map-membership branch —
which is why the decision is now general. The implementer still sweeps, but for
the remaining question: **is there anywhere else, in any code path, where an
omitted section produces a refusal rather than an absence of restriction?**
Report it either way; an empty result is a real answer and worth having in
writing after three instances.

## Evidence

Write the failing case first and confirm the current code fails it.

- A minimal schema-3 document applies a preset containing a property the map does
  not classify — M5's `HamamatsuHam_DCAM.DEFECT CORRECT MODE` is the measured
  case — instead of refusing it.
- The same document admits a `Core.Shutter` retarget, applies the preset's other
  effects, and asks for no confirmation.
- **The guard still bites under `property_writes_unrestricted`**: a preset effect
  that would drive the stage out of bounds, or exceed `camera.max_exposure_ms`,
  still refuses. Item 1 removes the *map* consultation, not the guard, and a test
  that does not prove this has not tested the decision.
- A document that **does** declare `property_authorization` or `illumination` is
  unchanged on every path: an unclassified effect still refuses with today's
  message, an undeclared shutter target still refuses, and a declared one still
  requires the operator's `y` and still refuses on a decline with nothing
  applied. **This is the half a fix like this breaks — if you write one test,
  write this one.**
- The refusal messages, where they still fire, are unchanged.
- Replay both real configs — `50a-demo/safety_config.yaml` and
  `50b-m5-round2/safety_config.yaml` in the evidence archive — rather than
  synthetic fixtures, per the standing habit that caught real defects before.
- The sweep result, stated either way.

## Blocks

### 51a — the preset route honours the same contract as the raw route

Design: "Decision" items 1–3, "No confirmation prompt", "Rejected", "Scope",
"Evidence" above.

**Rig gate 51a — both machines**, because each shows a different branch and
neither shows both.

*Demo* (the `Core.Shutter` branch; M5 has no `Channel` group and cannot show it):

- With `50a-demo/safety_config.yaml` exactly as it is: `set_channel` completes,
  the emission path moves in Micro-Manager, and no confirmation is requested.
- `list_config_groups(group="Channel", preset="Cy5")` shows the `Core.Shutter`
  setting in the preset — the thing that makes this rig the reproducer. Record it.
- Then **add an `illumination.shutters` declaration** naming a different device,
  restart, and confirm the retarget refuses again with today's message. An
  absent-when-undeclared fix that also disarms the declared case is the failure
  this limb exists to catch.
- Re-run block 50a's step A4, which this defect blocked.

*M5* (the map-membership branch):

- `set_config_preset(group="System", preset="Camera")` and `"Normal Mode"` both
  apply — the two presets in routine use, both refused on 2026-08-14 — and the
  camera properties move in the Property Browser, `DEFECT CORRECT MODE` included.
- **The guard still bites**: with `camera.max_exposure_ms` temporarily declared
  below a preset's exposure, that preset still refuses. Removing the map
  consultation must not remove the guard, and this is the limb that proves it on
  hardware rather than in a fixture.
- Re-run block 50a's steps B1 and B2, both blocked by this defect. Note that B2
  cannot produce an illumination confirmation while M5 declares no `illumination`
  section — that is the intended schema-3 behaviour design/48 measured, not a
  failure, and B2's hedge already covers recording it.

Step-10 design gate: record in `design/48-in-app-minimal-safety-setup.md` that
"omitted sections restrict nothing" had exceptions and no longer does; note in
`design/49` that this is the same pattern's second and third instances, with the
sweep's result; and correct this document's own first framing, which called a
structural gap a single branch.

## Run ledger

| Block | Branch | Start commit | Implementer | Rig gate | Merged | Design reconciled |
|---|---|---|---|---|---|---|
| coordination | `design51/open` | `559edd5` | coordinator | n/a | — | n/a |
| 51a | `design51/block-51a` | `559edd5` | | required — demo (the reproducer) | | |

Suite baseline at `559edd5` (macOS): **1788 passed / 99 skipped / 3 warnings**.

## Coordinator checklist

Run the ten steps in CLAUDE.md §"The block workflow". That section is
authoritative; this checklist tracks state only.

- [ ] Start from updated `main`; create `design51/block-51a`; record the start
      commit above and **commit it before assigning the block**.
- [ ] Write the runner prompt to the scratchpad, then stop and offer to start the
      agent. Do not spawn it.
- [ ] Implementation lands in its own worktree; implementer commits and reports,
      never merges.
- [ ] Review the diff, not the summary. Re-run the full suite yourself.
- [ ] Confirm the declared-illumination case is **untouched** — that is the half
      a fix like this breaks.
- [ ] Confirm the sweep was actually done and its result stated, empty or not.
- [ ] Commit the 51a rig-gate runbook **on the block branch**, pinned with
      `git merge-base --is-ancestor <commit> HEAD`. Push to `origin`. No PR.
- [ ] User runs the demo gate. Never simulate rig evidence.
- [ ] Fix, sized to the finding; push to the same branch; user re-tests.
- [ ] Merge to `main`, push `main`, delete the branch locally and on `origin`.
- [ ] Coordination notes in `design/prompts.md`; close the ledger row.
- [ ] Run the step-10 design gate named under 51a, and re-run block 50a's A4.
