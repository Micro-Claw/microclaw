# The focus lock is a typed capability

## Problem

Under schema 3, Microclaw can no longer engage or disengage a hardware focus
lock that lives on a bounded stage device. On M5 both directions refuse:

```
set_focus_lock(enabled=false)
→ RigAuthorizationError: Property write PIZStage.External sensor was refused
  because 'PIZStage' carries declared stage bounds. Raw property writes cannot
  route around those bounds; use move_stage_xy for XY motion, move_stage_z for
  the focus drive, or move_named_stage for a named stage.
```

This is a design/48 regression, not a gap. Block 43b's M5 gate (2026-08-09)
recorded "G3 PASS, operator-observed, both limbs: `set_focus_lock` on and off
(`PIZStage.External sensor`)". Evidence for the failure: the TIRF session of
2026-08-14, `tirf test with amr/first_test`, history lines 16–17 (disengage
refused) and 26–27 (re-engage refused). The agent handed the toggle back to the
operator twice, at the microscope.

Three correct pieces compose into it:

1. A schema-3 document declares stage bounds and no `property_authorization`, so
   `property_writes_unrestricted` is true and `bounded_stage_devices` collects
   every live device backing a bounded axis (`authorization.py:1476`). M5's Core
   focus device is `PIZStage`.
2. `authorize_property_write` then refuses **every property on that device**
   (`authorization.py:1496`, added by `689cf8f`).
3. `set_focus_lock` writes the EMU-allocated focus-lock role, which on this rig
   lands on `PIZStage.External sensor` — the same device.

The device-wide refusal exists to stop a raw write repositioning an axis around
its declared bounds. A focus-lock enable is not axis motion. It is being caught
by a rule that was never aimed at it, and the rule cannot be narrowed to "the
properties that move the axis" because Microclaw cannot identify those
generically — that is precisely why `689cf8f` made it device-wide.

The blast radius is larger than one toggle. `run_autofocus` refuses to sweep
against an engaged lock (`tools.py:3680`, correctly — the piezo servo would
oppose the sweep). With `set_focus_lock` unusable, **the whole autofocus path
requires manual GUI intervention on any rig whose focus lock sits on the
Z-stage device.**

## Decision

**Make the focus lock a typed capability, which is the class it already belongs
to, and let the bounded-stage rule admit what the map itself has typed.**

The stage, the camera exposure, and the camera ROI are all `built_in_typed_capability`
entries synthesized at startup — nobody declares them, and they carry a
`capability` name (`stage-position`, `exposure`, `camera-roi`). The focus lock is
the same kind of thing and was simply never given one.

1. In `validate_live_rig`, emit an `AuthorizationEntry` for the EMU focus-lock
   property when one is allocated: classification `built_in_typed_capability`,
   `capability="focus-lock"`, with the live device and property. Fold it into the
   EMU read that already happens — `_live_emu_lasers` (`authorization.py:58`)
   already calls `build_emu_map(...)` and keeps only `["lasers"]`; `["focus_lock"]`
   is in the same dict. No second EMU read and no new module.
2. In `authorize_property_write`, before the bounded-stage refusal, admit an
   exact `(device, property)` pair that already holds a `built_in_typed_capability`
   entry **on a raw-write path**. Every other property on a bounded stage device
   still refuses with the existing message.

   **The path qualification was missing from this document as first written, and
   the implementation faithfully reproduced the omission** (caught in review, not
   by the suite). Preset entries carry the same `built_in_typed_capability`
   classification, and a schema-3 document leaves `channels` absent — so *every*
   config-group preset is enumerated, and a preset touching the focus device's
   `Position` is classified by `_known_continuous_raw_pair`. A pair test that
   ignored `entry.path` therefore reopened the raw route to `PIZStage.Position`,
   the exact protection 48a's gate measured closed. Both decisions in that
   function now read one `_RAW_WRITE_PATHS` set, so the invariant is stated once
   rather than twice.

### This adds no configuration

`AuthorizationEntry` is a runtime dataclass built at startup from the live rig
(`authorization.py:186`). It is not a YAML key, it is not authored by an
operator, and nothing about `safety_config.yaml` changes. The focus-lock pair is
read from EMU's own `config.uicfg`, which the rig already owns.

It in fact **removes** a declaration. Auto-classification covers MM StateDevices
only (`authorization.py:746`), and a piezo stage is a StageDevice, so under
schema 2 an operator had to hand-write `{device: PIZStage, property: External
sensor}` into `property_authorization.allowed_categorical` to get a focus lock
they could toggle. After this change the map types it for them, in both schemas.
That is the direction design/48 set.

### Rejected

- **Drop the `authorize_property_write` call from `set_focus_lock`** so the tool
  self-authorizes. It is one line and it is wrong: it removes the gate in
  restricted mode too, where a profile may deliberately exclude the pair. The map
  stays the single decision point.
- **Narrow `bounded_stage_devices` to motion properties.** Microclaw cannot
  enumerate them across drivers — the reason the rule is device-wide.
- **Special-case the property name.** `"External sensor"` is an M5 fact. Rig facts
  do not go in `microclaw/` (CLAUDE.md). The EMU map's role allocation is what
  identifies the lock, on any rig.

### Scope

The focus lock only, unless a second case is found with evidence. The
implementer checks what else `build_emu_map` allocates that a tool **writes** and
that could land on a stage device, and reports either way. No speculative
widening.

## Evidence

`tests/test_tools.py:2351`, `test_set_focus_lock_refuses_raw_write_on_bounded_stage`,
asserts today's broken behaviour. It is replaced, not deleted, and its
replacement proves both halves:

- `set_focus_lock` reaches `core.set_property` and calls `refresh_gui` on a
  bounded stage device when the EMU map allocates the lock there;
- a raw write to a position property on that same bounded device still refuses
  with the existing message.

Plus, at the `authorization.py` level: the startup map contains the focus-lock
entry for an EMU rig, and invents none for a rig with no EMU config or no
allocated lock.

Write the failing case first and confirm the current code fails it. A fixture
that merely exercises the path is not a test of the finding.

**Both gates, independently.** `authorize_property_write` is gate 1;
`SafetyGuard.check_device_property` is gate 2. Gate 2 passes this pair today (it
is neither a typed nor an illumination pair, so it falls through to
`check_property`'s allowlist). Confirm that is still true afterwards and state it
— a fix that quietly couples the two gates is itself a finding.

`set_focus_lock` carries `@emits(_emit_focus_lock)` (`tools.py:310`). The emitted
standalone script must still render the write, and
`tests/test_session_script_export.py` must stay green: a capability is not
finished until it can appear in an exported script.

The refusal message names `move_stage_xy`, `move_stage_z`, `move_named_stage`.
All three are real registry entries today. The message has drifted once already —
`689cf8f` shipped it naming `move_stage` and `set_focus`, neither of which
existed — so re-verify every tool name against `TOOL_REGISTRY` if the message is
touched.

## Blocks

### 49a — Focus lock as a typed capability

Design: "Decision" items 1–2, "Rejected", "Scope", "Evidence" above.

Rig gate 49a (M5): under the schema-3 profile, prove (i) `set_focus_lock` off and
on both take effect, operator-observed in the htSMLM panel; (ii) `run_autofocus`
runs end to end with no manual GUI toggling — disengage, sweep, re-engage, all
from the agent; (iii) a raw `set_device_property` write to a `PIZStage` position
property still refuses with the bounded-stage message.

Limb (iii) is a re-run of something 48a already measured on M5 (2026-08-13):
"the raw-write route was refused for `PIZStage.Position` at an **in-range**
value — the protection is about the route, and it held". That is the behaviour
this change must leave exactly as it is, on the same device it now admits one
pair of. Gate it again rather than assuming it.

Step-10 design gate: reconcile `design/48-in-app-minimal-safety-setup.md` and
`design/33-authorization-map.md` to the fact that the schema-3 bounded-stage rule
is pair-specific for typed capabilities, and record the regression in
`design/prompts.md`.

## Run ledger

| Block | Branch | Start commit | Implementer | Rig gate | Merged | Design reconciled |
|---|---|---|---|---|---|---|
| coordination | ~~`design49/open`~~ | `23c4d29` | coordinator | n/a | — | n/a |
| 49a | ~~`design49/block-49a`~~ | `339c4f3` | codex, 1 round + coordinator fix | **M5 PASS 2026-08-14** | `036af32` | design/33 §amendment, design/48 §48a-gate, 2026-08-14 |

### What the 49a M5 gate measured (2026-08-14)

Evidence: `~/Documents/Documents - Beyonce/Projects/Micro-Claw/49a-m5`.

- pytest on M5: **1756 passed, 124 skipped**. macOS on the same commit was 1781
  / 99, and 1756 + 124 = 1781 + 99 = **1880** — the same collection, 25 tests
  skipped on Windows. Nothing was lost. The Windows-only skip set has grown from
  48a's 17; the three new explicit POSIX-only markers are in `test_credentials`,
  `test_shortcut`, and `test_config_gate`, all from 48b–48e's installer work,
  and none of this block's tests carry a platform marker.
- **Both toggle limbs passed.** `set_focus_lock` off and on, twice each across
  steps 2 and 3, no refusal, `property` exactly `PIZStage.External sensor`.
- **Autofocus ran end to end with no GUI intervention** — the limb the block
  existed for. The agent called `set_focus_lock(false)` → `run_autofocus` →
  `set_focus_lock(true)` on its own. `converged: true`, `peak_interior: true` in
  **both** coarse and fine passes, contrast 19.0 and 54.1, entry Z 30.543 →
  final 30.043.
- **The lock physically engaged**, not merely reported: QPD x read `18646`
  disengaged and `32926` engaged, matching the engaged range seen in the
  2026-08-14 TIRF session (`32878`–`32964`).
- **The bounded-stage refusal is unchanged.** `set_device_property` on
  `PIZStage.Position` at an in-range `50` refused, naming all three real
  routing tools, with no motion.

## Coordinator checklist

Run the ten steps in CLAUDE.md §"The block workflow". That section is
authoritative; this checklist tracks state only.

- [x] Start from updated `main`; create `design49/block-49a`; record the start
      commit in the run ledger and commit it before assigning the block.
- [x] Write the runner prompt to the scratchpad, then stop and offer to start the
      agent. Do not spawn it.
- [x] Implementation lands in its own worktree; implementer commits and reports,
      never merges.
- [x] Review the diff, not the summary. Re-run the full suite yourself. Loop
      until correct.
- [x] Commit the 49a rig-gate runbook **on the block branch**, pinned with
      `git merge-base --is-ancestor <commit> HEAD`. Push to `origin`. No PR.
- [x] User runs the M5 gate. Never simulate rig evidence.
- [x] Fix, sized to the finding; push to the same branch; user re-tests until
      the gate passes.
- [x] Merge to `main`, push `main`, delete the branch locally and on `origin`.
      `git log --oneline origin/main..main` must be empty.
- [x] Coordination notes in `design/prompts.md`; close the ledger row **including
      its design-reconciliation cell**.
- [x] Run the step-10 design gate named under 49a.
