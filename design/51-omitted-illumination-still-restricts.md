# An omitted `illumination` section still restricts one thing

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
here catches a typed path, while the raw path it cannot police stays open. It is
worth stating as a pattern, because that is now twice: **a schema-3 document
that declares nothing must not leave one branch behaving as though everything
were declared.**

### Blast radius

Every channel switch on a rig whose `Channel` presets carry a `Core.Shutter`
setting — the Micro-Manager demo configuration among them. On such a rig
`set_channel` is unusable, and so is every acquisition that drives a channel
axis, since `_check_acquisition_channel` routes through the same expansion. It
also blocks block 50a's gate step A4.

## Decision

**Refuse the retarget only when the session declared illumination at all.**

1. Add `illumination_unrestricted` to `AuthorizationMap`, set in
   `validate_live_rig` as `"illumination" not in parsed_config.declared_sections`
   — the exact idiom `channels_unrestricted` already uses one line above
   (`authorization.py:1501`). No new concept, no new configuration.
2. In `_authorize_channel_effect`'s `Core.Shutter` branch, admit the retarget
   when that flag is true. When it is false, the branch is **unchanged**: the
   target must be a declared shutter and the operator must confirm, which is
   block 2's undeclared-light-source protection and stays exactly as measured.

### No confirmation prompt when illumination is undeclared

Deliberate, and not a judgment call: design/48's M5 gate blessed a **laser
enable** going through with no confirmation under these conditions. A shutter
retarget is strictly less than an enable, so prompting for it while the enable
passes silently would reintroduce the same inconsistency in the other direction.
The two routes must agree; that is the whole finding.

### Rejected

- **Declare `illumination` on both rigs and move on.** It fixes two machines and
  leaves the contract broken for every other minimal document, including every
  one the in-app setup will generate. The operator's config is not the defect.
- **Drop the `Core.Shutter` branch entirely.** It is correct and load-bearing
  whenever illumination *is* declared; block 2's gate measured it.
- **Infer the shutter set from the rig instead of the declaration.** Guessing
  which devices are light sources is what the declaration exists to prevent.
- **Special-case the demo configuration's device name.** A rig fact in
  `microclaw/` (CLAUDE.md).

### Scope

The `Core.Shutter` branch only. The implementer sweeps
`_authorize_channel_effect` and `authorize_property_write` for any other place an
**omitted** section produces a refusal rather than an absence of restriction, and
reports what it finds either way — that sweep is the deliverable even if it is
empty, because this is the second instance of the pattern and nobody has yet
looked for a third.

## Evidence

Write the failing case first and confirm the current code fails it.

- A schema-3 document with no `illumination` section admits a `Core.Shutter`
  retarget, applies the preset's other effects, and asks for no confirmation.
- A document that **does** declare `illumination.shutters` is unchanged in both
  directions: an undeclared target still refuses with today's message, and a
  declared one still requires the operator's `y` and still refuses on a decline.
- The refusal message, where it still fires, is unchanged.
- Replay the demo's exact `safety_config.yaml` (in the 50a-demo evidence folder)
  rather than a synthetic fixture, per the standing habit that caught real
  defects before.
- The sweep result, stated either way.

## Blocks

### 51a — an omitted section restricts nothing, including this one

Design: "Decision" items 1–2, "No confirmation prompt", "Rejected", "Scope",
"Evidence" above.

**Rig gate 51a (demo).** The demo machine is the reproducer and its `Channel`
presets carry the `Core.Shutter` setting; M5 cannot show this, having no
`Channel` group.

- With the config exactly as it is in `50a-demo/safety_config.yaml`, unchanged:
  `set_channel` completes, the emission path moves in Micro-Manager, and no
  confirmation is requested.
- `list_config_groups(group="Channel", preset="Cy5")` shows the `Core.Shutter`
  setting in the preset, which is what makes this rig the reproducer — record it.
- Then **add an `illumination.shutters` declaration** naming a different device,
  restart, and confirm the retarget refuses again with today's message. The
  protection must be intact when the section exists; an absent-when-undeclared
  fix that also disarms the declared case is the failure this limb looks for.
- Re-run block 50a's step A4, which this defect blocked.

Step-10 design gate: record in `design/48-in-app-minimal-safety-setup.md` that
"omitted sections restrict nothing" had one exception and no longer does, and
note in `design/49` that this is the second instance of the same pattern, with
the sweep's result.

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
