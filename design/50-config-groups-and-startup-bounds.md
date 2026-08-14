# Config groups, and a stage that starts out of bounds

Two findings from the M5 bead-calibration session of 2026-08-12. Evidence:
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/amr_beads_test`,
`20260812_135245_075336_microclaw_history.jsonl` (110 lines; line numbers below
are 0-indexed into that file).

They are unrelated in mechanism and share only their origin. Two blocks.

---

## Problem 1 — Microclaw cannot see a Micro-Manager config group

The operator asked, at line 66:

> Sorry, I just want to ask about Beads_EM25 again. I can see the name under
> Configuration settings > Preset. Can you see it?

It could not. `TOOL_REGISTRY` exposes exactly one config-group surface —
`get_available_channels` / `set_channel` — and both are hardwired to
`CHANNEL_CONFIG_GROUP = "Channel"` (`authorization.py:170`). Every other group
on the rig is invisible: not listable, not readable, not selectable.

What the agent did instead (line 67) is the part that makes this a defect rather
than a missing convenience. Having no way to read the preset, it guessed its
membership from property names, then reported the guess:

> Yes — I can see the settings that the name `Beads_EM25` **almost certainly**
> refers to […] **Gain = 25** ← this is the "EM25" in the name

It did disclose the limit immediately afterwards, and the guess was probably
right. But this is design/20–22's failure mode with the correction already
built in: the tool floor forced an inference where a read was available on the
bridge the whole time. `core.get_available_config_groups()`,
`get_available_configs(group)` and `get_config_data(group, preset)` are already
called in this repo — `rig_inventory.py:406`–`:408` walks **every** group and
expands **every** preset at setup time, and `_expand_preset`
(`authorization.py:293`) reads a preset's exact settings. The rig read exists.
Only the tool is missing.

### Decision 1 — parameterize the group; add two tools; authorize per effect

**One executor, two tools, no new write path.**

1. **`list_config_groups()`** — every group, its presets, and the preset
   currently active per group (`getCurrentConfig`, which returns `""` when live
   state matches no preset; report that as unknown, never as a guess). Read-only,
   `@emits_nothing`.

2. **`set_config_preset(group, preset)`** — routes to `execute_channel_plan`
   with the group as a parameter rather than the module constant. That function
   already captures the live expansion, authorizes each effect, replays with
   read-back verification, and rolls back in reverse on failure, distinguishing
   a write that reached the device from one that raised (`authorization.py:1714`
   and the M5 finding of 2026-08-06 recorded in it). None of that is re-written.

   The parameterization reaches `_expand_preset` and `authorize_channel`.
   `CHANNEL_CONFIG_GROUP` stays as `set_channel`'s default, so the channel path
   is unchanged.

3. **Authorization is per effect, and nothing new is declared.** Every expanded
   `(device, property, value)` goes through `_authorize_channel_effect`
   (`authorization.py:1637`), which already routes illumination to
   `check_illumination` and its confirm gate, typed actuators to
   `check_device_property`, stage/exposure properties to their axis guards,
   `Core.Shutter` to the declared-shutter test, and refuses anything
   unclassified or excluded. A preset that arms a laser therefore blocks on a
   human `y` exactly as a channel preset does.

   `Beads_EM25` is the ordinary case: `EMSwitch` and `Output_Amplifier` are
   categorical, and `Andor.Gain` already carries
   `{"kind": "bounded-numeric", "units": "native", "minimum": 3, "maximum": 1000}`
   (line 68 of the history). The machinery for this preset is already built and
   already typed; it is simply unreachable.

4. **`authorized_presets` stays a `Channel`-only startup product.** It is built
   in `validate_live_rig` (`authorization.py:1269`–`:1339`) from
   `channels.allowed` intersected with the `Channel` group. Block 4f narrowed
   that deliberately, and widening it would make startup expand every preset on
   the rig into the map. `authorize_channel` is called with the group; for a
   non-`Channel` group it has no allowlist to consult and the per-effect gate is
   the whole authorization. Say this in the tool's own docstring, not only here.

5. **Emitter.** `set_config_preset` gets `@emits`. `_emit_recorded_channel_effects`
   (`tools.py:2063`) already takes a `label` and already renders both routes —
   recorded effect triples for the map path, `set_config` + `wait_for_config` for
   the map-less one. Reuse it; do not write a second renderer. A new capability
   is not finished until it can appear in an exported script.

#### Rejected

- **A read-only `describe_config_preset` and nothing else.** It answers line 66
  and leaves the operator applying the preset by hand. The write is the point.
- **Generalize `set_channel` to take an optional `group`.** The two verbs are
  not the same verb: a channel is an acquisition axis (`_check_acquisition_channel`
  refuses a channel the acquisition cannot drive), a config preset is not.
  Overloading the tool would put that refusal in front of a camera preset.
- **A `config_groups.allowed` safety-config section.** A new schema section that
  every deployed rig must grow before presets work at all, to duplicate a gate
  `_authorize_channel_effect` already applies per effect. Operator decision,
  2026-08-14: per-effect only.
- **Auto-applying a preset the agent inferred from live property values.** What
  line 67 had to do. The read replaces the inference; it does not license it.

#### Scope

Config groups only. No change to `set_channel`, `get_available_channels`,
`_channel_source`, or the acquisition channel axis.

---

## Problem 2 — the stage envelope is only consulted on a move

The session's first tool call, line 2, returned:

```
{"x_um": 1860.9, "y_um": 11968.9, "z_um": 58.204, ...}
```

against a configured `stage.y_max` of 5000 µm. Nothing said so. Ninety-two
lines later, after the 561 laser had been calibrated, enabled, ramped across six
power levels and a full 101-plane z-stack had been acquired and saved, the first
survey move returned (line 94):

```
{"error": "Safety constraint prevented this action:
  Y=11908.9 µm exceeds the maximum allowed (5000.0 µm)."}
```

The agent then correctly refused to move at all, and the session ended with the
work unfinished. The out-of-bounds reading was on screen from the first call.

`get_system_state` (`tools.py:2410`) reads `x_um`, `y_um` and `z_um` and reports
them raw. `guard.check_xy` (`safety.py:851`) and `check_z` (`:876`) are reached
only from a *move*, so a session that starts outside the envelope learns it at
the first move attempt — which is, by construction, the moment the operator has
already committed dose and time.

### Decision 2 — `get_system_state` reports the position against the envelope

Run the coordinates it has **already read** back through the guard, and add an
`out_of_bounds` field carrying the `SafetyViolation` messages when they refuse.
No second read, no new guard method, no new tool.

- **Every call, not the first.** There is no "first call" to hang this on and
  there must not be: Microclaw opens mid-session and more than once, and
  per-session "have I warned yet" state is exactly the kind a second launch
  trips over (CLAUDE.md). A stage outside its envelope is worth reporting every
  time it is asked, and the report is cheap — the numbers are in hand and the
  guard is pure arithmetic on the parsed config.
- **Report, never move.** Microclaw writes nothing to the rig on this path. The
  operator drives the stage back, or raises the limit and restarts.
- **Named stages too.** `check_named_stage` (`safety.py:1233`) has the same blind
  spot, and `get_system_state` does not report named stages at all. Include a
  named-stage limb only for stages the config already declares under
  `named_stages` — reading declared stages, not enumerating the rig's devices.
- **The agent must surface it.** One line in `agent.py`'s system prompt beside
  the existing `get_system_state` orientation instruction (`agent.py:84`): an
  `out_of_bounds` report is told to the operator before the session proceeds.
  The field alone is not the fix; a field the model reads past changes nothing.

### Folded in: two refusals that name no axis

Both were on the critical path of the same 92-line delay and both are one-line
fixes in the same area. They go in this block rather than a follow-up.

- **`validate_positions` (`tools.py:6146`) discards the guard's message** and
  substitutes `"Rejected by the current XY safety guard."` for every rejection.
  The agent used it twice (lines 96 and 100) to diagnose the block and learned
  nothing either time — it had to reason back to the Y limit from the earlier
  move error. The `SafetyViolation` text already names the axis, the value and
  the limit. Report it.

  **This reverses a deliberate prior decision, which this document missed when
  it was written and the 50b review caught.** `design/26-implementation.md:366`
  said the result "never exposes guard limits or clips", and the test carrying
  it is named `test_validate_positions_does_not_move_or_expose`. The two halves
  were coupled on the theory that an agent which cannot see the limits cannot
  clip candidate coordinates to fit them. That theory does not hold: every move
  refusal already names the limit it hit, `get_system_state` now does too, and
  the envelope was never actually withheld. The barrier was paper-thin and the
  cost was a real session.

  **"Never clip" survives unchanged** — it is the invariant that protects the
  science, it is enforced by `clipped == 0` and by the agent prompt, and nothing
  here touches it. What replaces "never expose" is narrower: *name the limit the
  rejected position hit, never dump the whole limits table.* `assert "limits"
  not in result` stays. Both design/26 documents are corrected on this branch,
  not annotated.
- **`get_xy_position` / `get_z_position`** return raw coordinates with the same
  blind spot as `get_system_state`, and an agent that orients with those instead
  gets no report at all. Same treatment, same field name.

#### Rejected

- **Refuse to start the session, or block the first tool call.** The operator
  owns the session; the stage may be legitimately parked outside a
  conservatively-set envelope, and design/14 §3's teardown-shuttering reversal
  is the same principle. Notify, do not gate.
- **Clamp, or move the stage back into the envelope.** Microclaw does not move
  hardware nobody asked it to move.
- **Warn at startup instead** (`__main__.py:155`, beside `validate_live_rig`).
  It reaches the CLI only; the web surface is the primary UI and would print to
  a console nobody is reading. `get_system_state` is the cross-surface answer
  and is what the model already calls first.

---

## Evidence

Write the failing case first and confirm current code fails it. A fixture that
merely exercises the path is not a test of the finding.

**50a**
- `list_config_groups` returns a rig's real groups, presets and active preset;
  reports the active preset as unknown when `getCurrentConfig` returns `""`;
  survives a group whose preset read raises, without dropping the other groups.
- `set_config_preset` on a non-`Channel` group applies and verifies every effect,
  and rolls back in reverse when one write fails — the `execute_channel_plan`
  contract, asserted through the new entry point, not re-implemented.
- **An illumination effect inside a non-`Channel` preset still hits the confirm
  gate**, and a declined confirmation refuses the whole preset with nothing
  applied. This is the limb that justifies per-effect authorization; without it
  the decision is unproven.
- An unclassified or excluded effect refuses, naming the property.
- `set_channel` behaviour is unchanged — assert it against the existing tests,
  which must not need editing.
- Export: a session that calls `set_config_preset` emits a script that compiles
  and defines every name it uses. `tests/test_session_script_export.py`
  stays green, including
  `test_emitted_inline_defines_every_name_it_uses`. Check both branches of
  `_emit_recorded_channel_effects` — block 43n's `NameError` gate died because
  only the `config_group` branch was ever exercised and the effects-triple branch
  emitted `_verify_property` without inlining it.

**50b**
- A guard whose `y_max` is below the reported Y makes `get_system_state` carry
  `out_of_bounds` naming Y, the value and the limit; a stage inside the envelope
  carries no such field at all (an always-present empty field reads as a warning
  and will be reported as one).
- An unset limit (`y_max: None`) produces no report — most configs bound some
  axes and not others.
- Z and named stages, each independently.
- An unreadable stage still reports what it can: the existing
  `"xy_stage": "unavailable"` limb must not become an exception path.
- `validate_positions` returns the guard's own message, per axis.
- The report is derived, never written: assert no `set_position` / `set_xy_position`
  call on any limb.

---

## Blocks

### 50a — Config groups are listable and selectable

Design: "Decision 1" items 1–5, "Rejected", "Scope", and 50a's evidence above.

**Rig gate 50a — demo machine first, then M5.** The demo machine has both a
`Channel` group and an authorization map (established by 43n's gate), so it can
prove the executor route; M5 proves it on a rig whose channels are *not*
presets, where this is the only config-group write path that exists.

- Demo: `list_config_groups` returns the real groups; `set_config_preset` on a
  non-`Channel` group applies and verifies, operator-observed in the Micro-Manager
  Configuration Settings panel; `set_channel` still works unchanged in the same
  session; the exported script runs standalone.
- M5: `Beads_EM25` — the preset this block exists for — is listed with its true
  membership, and applying it is observed on the Andor. Compare the listed
  membership against the 2026-08-12 guess (`EMSwitch=On`,
  `Output_Amplifier=Electron Multiplying`, `Gain=25`) and **record the diff
  either way**; a guess that turns out right is still a guess, and a guess that
  turns out wrong is the block's headline result.
- M5: a preset containing a laser enable requests confirmation, and declining it
  leaves the rig unchanged. **M5 is where this limb is naturally reachable** — it
  has no `Channel` group at all, only `System`, whose presets arm four lasers for
  TTL and set camera `Exposure` (design/35 §41c, and the 49a runbook). Note the
  qualification: arming for TTL may write *trigger-mode* properties rather than a
  declared illumination enable, in which case no prompt is owed and the gate
  records the effect classification instead of failing. A preset writing a
  declared enable with no prompt is the failure. Do not author a preset on the
  rig to create the limb.

Step-10 design gate: reconcile `design/33-authorization-map.md` to the fact that
per-effect authorization now serves a second, allowlist-free entry point, and
record in `design/prompts.md` that the tool floor forced an inference the bridge
could have answered.

### 50b — A session that starts out of bounds says so at the first read

Design: "Decision 2", "Folded in", "Rejected", and 50b's evidence above.

**Rig gate 50b (M5).** M5 *is* the reproducer — its stage sits at
Y = 11968.9 µm against a 5000 µm `y_max` — so the gate is cheap and exact.

- With the current safety config unchanged, the first `get_system_state` of a
  fresh session reports Y out of bounds, naming 11968.9 and 5000.0, and the
  agent says so to the operator before doing anything else.
- `validate_positions` on the current position names the Y limit, not
  "the current XY safety guard".
- Then correct `y_max`, restart, and confirm the report is **absent** — the same
  session that produced the finding should end with the rig usable, and an
  absent-when-correct check is the half that catches a field that is always on.
- Nothing moved: stage coordinates identical before and after, from
  `get_system_state`, not from narration.

**`stage.y_max` is a collision envelope, not the stage's travel limit** — the
operator ruling of 2026-08-14, correcting this document and the runbook, both of
which had told the operator to source the number from the hardware. M5's stage
can drive much further than is safe, far enough to hit the objective. The bound
is the range the operator is willing to let software move within, it must be
*narrower* than hardware travel, and no device property, controller readout or
specification can supply it. It is a judgment made at the rig.

That reframes the finding itself. The stage was at Y = 11968.9 on 2026-08-12
**and imaging beads successfully**, so `y_max: 5000` was not protecting anything
at that position. Either the envelope is authored too narrow, or it was authored
against a **different origin** — a re-home, or a value carried from another
rig — in which case every stage bound in that file is suspect rather than just
this one. The gate asks which, and that answer is worth more than the corrected
number.

Step-10 design gate: record the corrected `y_max`, **which of the two diagnoses
above it was, and what the operator based it on**, so the next session on M5 does
not rediscover this. If the answer is "different origin", raise the remaining
stage bounds as their own finding — do not fix them silently inside this block.

---

## Run ledger

| Block | Branch | Start commit | Implementer | Rig gate | Merged | Design reconciled |
|---|---|---|---|---|---|---|
| coordination | `design50/open` | `32e74d0` | coordinator | n/a | — | n/a |
| 50a | `design50/block-50a` | `32e74d0` | | required — demo, then M5 | | |
| 50b | `design50/block-50b` | `32e74d0` | codex, 1 review round + coordinator fixes | **M5 PASS 2026-08-14**, rounds 1 + 2 | `d809173` | |

50a and 50b are independent — different files, different gates — and may run
concurrently in separate worktrees. One worktree per agent; never
`pip install -e .` while another tree is live.

### What the 50b M5 gate measured (2026-08-14)

Evidence: `~/Documents/Documents - Beyonce/Projects/Micro-Claw/50b-m5` and
`50b-m5-round2`.

- pytest on M5: **1763 passed / 124 skipped**. macOS at the same commit was
  1788 / 99, and 1763 + 124 = 1788 + 99 = **1887** — same collection, 25 more
  Windows skips, matching 49a's measurement exactly. Nothing lost.
- **Round 2 reproduced the original dead-end and inverted its cost.**
  `move_stage_xy` refused with `Y=12479.0 µm exceeds the maximum allowed
  (5644.3 µm)` — the same refusal that ended the 2026-08-12 session — but the
  agent already held that fact from its **first** call, stated it before
  attempting the move, and correctly attributed the refusal to the standing Y
  position rather than the requested X delta. Nothing was enabled and nothing
  acquired first. That inversion is the entire block.
- **Round 1 found two standing named-stage violations nobody was looking for**:
  `Thorlabs ELL17/ELL20` at 20819.0 against a 20000.0 max, and `Thorlabs ELL20`
  at −55.0 against a 0.0 min. The runbook had put named stages under "not in
  this gate, deliberately", assuming they were in bounds. They were not, so the
  limb thought untestable was tested for free, with value and limit named.
- **Three violations of two kinds coexist in one list** (round 2: Y plus both
  named stages), which no off-rig test had exercised.
- **The agent stated it out loud, both rounds** — the half the `agent.py` line
  exists for. It also flagged, unprompted, that **laser 640 (slot 3) was enabled
  at 1.00%** and reasoned correctly about camera-triggered dose on this rig.
  That is a live rig finding, not a block result.
- **Absent-when-correct is bracketed across the two rounds** on one axis: in
  round 1 XY was inside the envelope and Y contributed nothing to the list while
  named stages did; in round 2 only the config changed and Y appeared. A mixed
  present/absent state on one rig beats an all-clear.

**NOT TESTED on the rig: `validate_positions`'s guard message.** Round 1 called
it and got zero rejections (XY was in bounds that run); round 2 did not call it.
The tool makes no hardware call at all — it is guard arithmetic over supplied
coordinates — so its rig behaviour is identical to its unit-tested behaviour by
construction, and it is covered off-rig by
`test_validate_positions_names_the_limit_hit_but_never_clips` and
`test_validate_positions_reports_z_guard_message`. Recorded as untested rather
than inferred.

**The origin mechanism, answered by the operator 2026-08-14 — a rig fact, and it
stays out of `microclaw/`.** M5's stage sets its origin on power cycle. Power
cycling it at an extreme position and then driving back to a normal one makes
that normal position read as a very high number, which is what happened between
the envelope being authored and the 2026-08-12 session. The envelope was never
wrong; the coordinate frame moved under it. **The remedy on this rig is to power
cycle the stage in a neutral position, and Microclaw must not say so** —
operator instruction, and the correct general behaviour anyway: the report names
the axis, the value and the limit, and suggests nothing. Confirmed in both
rounds, where the agent proposed no remedy of its own.

Suite baseline measured by the coordinator at `32e74d0` (macOS):
**1781 passed, 99 skipped, 3 warnings in 44.4 s.** The 3 are the pre-existing
`StarletteDeprecationWarning` and two `phase_cross_correlation` empty-image
`UserWarning`s from the featureless-field calibration tests — expected, not a
regression.

---

## Coordinator checklist

Run the ten steps in CLAUDE.md §"The block workflow". That section is
authoritative; this checklist tracks state only.

### 50a — config groups

- [ ] Start from updated `main`; create `design50/block-50a`; record the start
      commit in the ledger above and **commit it before assigning the block**.
- [ ] Write the runner prompt to the scratchpad, then stop and offer to start the
      agent. Do not spawn it.
- [ ] Implementation lands in its own worktree; implementer commits and reports,
      never merges.
- [ ] Review the diff, not the summary. Re-run the full suite yourself. Loop
      until correct.
- [ ] Confirm `execute_channel_plan` was **parameterized, not copied**, and that
      `set_channel`'s existing tests needed no edit.
- [ ] Confirm the emitter reuses `_emit_recorded_channel_effects` and that both
      its branches are exercised by a test.
- [ ] Commit the 50a rig-gate runbook **on the block branch**, pinned with
      `git merge-base --is-ancestor <commit> HEAD`. Push to `origin`. No PR.
- [ ] User runs the demo gate, then M5. Never simulate rig evidence.
- [ ] Fix, sized to the finding; push to the same branch; user re-tests until the
      gates pass.
- [ ] Merge to `main`, push `main`, delete the branch locally and on `origin`.
      `git log --oneline origin/main..main` must be empty.
- [ ] Coordination notes in `design/prompts.md`; close the ledger row including
      its design-reconciliation cell.
- [ ] Run the step-10 design gate named under 50a.

### 50b — startup bounds report

- [ ] Start from updated `main`; create `design50/block-50b`; record the start
      commit in the ledger above and **commit it before assigning the block**.
- [ ] Write the runner prompt to the scratchpad, then stop and offer to start the
      agent. Do not spawn it.
- [ ] Implementation lands in its own worktree; implementer commits and reports,
      never merges.
- [ ] Review the diff, not the summary. Re-run the full suite yourself. Loop
      until correct.
- [ ] Confirm no session state was introduced — no "already warned" flag, no
      first-call bookkeeping, nothing a second launch could trip over.
- [ ] Confirm the `agent.py` prompt line landed; the field alone is not the fix.
- [ ] Commit the 50b rig-gate runbook **on the block branch**, pinned with
      `git merge-base --is-ancestor <commit> HEAD`. Push to `origin`. No PR.
- [ ] User runs the M5 gate, including the absent-when-correct limb after
      `y_max` is raised.
- [ ] Fix, sized to the finding; push to the same branch; user re-tests until the
      gate passes.
- [ ] Merge to `main`, push `main`, delete the branch locally and on `origin`.
- [ ] Coordination notes in `design/prompts.md`; close the ledger row.
- [ ] Run the step-10 design gate named under 50b.
