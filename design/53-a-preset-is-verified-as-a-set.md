# A preset is true as a set, so verify it as a set

## Problem

`System/Normal Mode` — a preset in routine use on M5 — cannot be applied through
Microclaw. Measured 2026-08-14 during the combined 50a/51a gate. Evidence:
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/50a-51a-m5`, history line 6:

```
set_config_preset(group="System", preset="Normal Mode")
→ ChannelPlanPartialApplicationError: Channel plan 'Normal Mode' stopped after
  1/11 writes: Read-back verification failed for HamamatsuHam_DCAM.Exposure:
  requested '100.0030', got '100.0140'.
  applied=['HamamatsuHam_DCAM.DEFECT CORRECT MODE']
  rolled_back=['HamamatsuHam_DCAM.Exposure', 'HamamatsuHam_DCAM.DEFECT CORRECT MODE']
```

The rollback was correct and complete; the plan's failure handling is not in
question. What is wrong is the verification that triggered it.

### It is not quantization, and no tolerance change fixes it

The first hypothesis was that the camera snaps exposure to its own grid, making
`isclose(rel_tol=1e-9)` too strict. **Measured on M5 and false.** With the camera
first put into ScanMode 3, `set_device_property(Exposure, "100.0030")` reads back
exactly `100.0030`. The value is representable. It is only unrepresentable in
ScanMode **2**, which is where `Camera` leaves the camera and where `Normal Mode`
is verified.

### The cause: a preset's values are simultaneously true, and only at the end

`Normal Mode`'s expansion, in order (`list_config_groups`, history line 14):

| index | device.property | value |
| --- | --- | --- |
| 0 | `HamamatsuHam_DCAM.DEFECT CORRECT MODE` | OFF |
| **1** | **`HamamatsuHam_DCAM.Exposure`** | **100.0030** |
| … | | |
| **5** | **`HamamatsuHam_DCAM.ScanMode`** | **3** |
| … | `iChrome-MLE-TCP.Laser 1–4: 4. Use TTL` | 1 |
| 10 | `HamamatsuHam_DCAM.Sensor Cooler` | ON |

On a Hamamatsu sCMOS the exposure grid is a function of line time, which scan
mode changes. So `Exposure` at index 1 is only valid once `ScanMode` at index 5
has landed. `execute_channel_plan` verifies each write immediately, against a
device state four writes short of the one the preset describes.

`Camera` passes by coincidence: its exposure is valid in the mode the camera was
already in.

**This is pre-existing and is neither block 50a's nor 51a's.** `_verify_property`
dates to the original executor (`3fc7dcd`) and neither block touched it. Its
float branch exists for MM *reformatting* — `"10"` read back as `"10.0000"`
(design/33 Phase 4) — not for values whose validity depends on another property.
M5 simply had no reachable preset route until 50a added `set_config_preset`, and
the demo's presets happen to carry no interdependent pair. **Block 50a exposed
it; it did not introduce it.**

Any rig is affected whose preset sets a mode and a mode-dependent value, in that
order. Binning and exposure, scan mode and exposure, readout rate and gain are
all ordinary examples.

## Decision

**Stop verifying each write against a half-applied preset. Apply the plan, then
verify it.**

1. Keep the write loop's exception handling exactly as it is. An exception from
   `set_property` means the device refused the write — that is a real error at
   that moment, and stopping immediately is right.
2. Keep `wait_for_device` per write. Ordering and settling are unchanged.
3. **Move `_verify_property` out of the loop into a single pass after the last
   write**, over the same effect list in the same order. A read-back mismatch is
   only meaningful once every value the preset asserts is on the device.
4. On a mismatch, roll back all applied writes in reverse through the existing
   machinery. The error names every mismatched pair, not just the first.
5. **Amended 2026-08-15 (coordinator): the rollback verifies as a set too.** The
   reverse restore loop has the identical defect mirrored. `Normal Mode`'s order
   is value-then-mode, so reversing it happens to restore the mode first and the
   rollback measured on M5 was safe by luck of that order. A preset written
   mode-then-value — the natural way to author one — rolls back value-first,
   read-back fails while the old mode is not restored yet, and the operator gets
   `SAFE STATE NOT VERIFIED` on a rig that then finishes restoring correctly.
   That is the loudest error Microclaw has, raised falsely. So: restore every
   write in reverse, then verify the restored values in one pass, and classify a
   mismatch there exactly as a failed restore is classified today (`landed`
   decides `rollback_failures` vs `unrestored`). Same principle, applied once
   more; it is why gate limb 2 exercises both directions.

The distinction the current code misses is between *the device rejected this
write* and *this value is not consistent yet*. The first is knowable per write;
the second is knowable only at the end.

### What this costs

All writes land before a mismatch is discovered, where today the plan stops
early. That is a real change and is accepted for three reasons: a partially
applied preset is not a safer state than a fully applied one, only a less
coherent one; every effect is authorized and confirmed **before** the first write,
so nothing unreviewed reaches the rig either way; and the rollback path keeps its
proven shape — reverse order, same machinery, same error selection (M5,
2026-08-14, and the serial-timeout case of 2026-08-06) — with only *when* it
verifies changed, by Decision 5.

### Rejected

- **Loosen the float tolerance.** Measured false: the value is exact in the right
  mode. A tolerance wide enough to pass this would be wide enough to miss a write
  the device ignored, which is what verification is for.
- **Reorder effects so mode properties are written first.** Microclaw cannot know
  which properties are modes without rig-specific knowledge, which does not go in
  `microclaw/` (CLAUDE.md). MM stores the preset's order; that order is the
  author's, and we apply it faithfully.
- **Verify per write and retry the failures at the end.** Strictly more code than
  verifying once at the end, for the same result.
- **Drop read-back verification.** It exists because MM's `set_config` continues
  past a setting the device ignored (design/33 Phase 4). The check is the point;
  its timing is the defect.

### Out of scope, recorded

MM's Exposure box read `100.004` while the device property read `100.0030`, and
`core.get_exposure()` reported `100.00397…` in an earlier session. The device
property and MMCore's exposure are **not the same number**. Nothing here depends
on that, but anyone tempted to verify exposure through `get_exposure` instead of
the property should measure the relationship first.

## Evidence

Write the failing case first.

- A preset whose value is valid only after a later write in the same preset
  applies cleanly and verifies — the M5 `Normal Mode` shape, with a fake whose
  representable set for one property depends on another.
- A write the device genuinely ignores — read-back never matches, in any order —
  still fails, rolls back in reverse, and names the pair.
- An exception from `set_property` still stops at that write, with `applied`,
  `attempted` and `rolled_back` accounted exactly as today.
- `ChannelPlanSafeStateError` and `ChannelPlanPartialApplicationError` still
  select on the same conditions; the M5 finding of 2026-08-06 (no write reached
  the device ⇒ not a partial application) is unchanged.
- Multiple mismatches are all named, not just the first.
- `set_channel` on a single-effect preset is unchanged.
- **The exported script verifies as a set too.** `_emit_recorded_channel_effects`
  (`tools.py:2101`) interleaves `set_property` / `wait_for_device` /
  `_verify_property` per effect, so a standalone script of the M5 session
  reproduces exactly the defect this block removes, on the rig it was exported
  for. Added by the coordinator 2026-08-15; it is not in the original evidence
  list. The emitted program must order itself the way the executor does, and it
  keeps calling the inlined `_verify_property` — `tools.py:1275` decides whether
  to inline the helper by looking for that literal call in the emitted body.
- The rollback restores correctly for a preset written **mode-then-value** as
  well as value-then-mode, and neither direction reports an unverified safe
  state when the rig ends up restored.

## Blocks

### 53a — verify the plan, not each write

Design: "Decision" 1–4, "What this costs", "Rejected", "Evidence" above.

**Rig gate 53a (M5).** M5 is the reproducer and the only machine known to carry
an interdependent preset.

- `set_config_preset(group="System", preset="Normal Mode")` applies all 11 writes
  and verifies, with `ScanMode` reading 3 and `Exposure` reading 100.0030
  afterwards in the Property Browser.
- `Camera` still applies, and switching between the two presets repeatedly
  succeeds in both directions — the ordering trap is symmetric and one direction
  passing proves little.
- A deliberately unsatisfiable preset still refuses and rolls back. Do **not**
  author one on the rig; if none exists naturally, mark this limb not run and
  rely on the off-rig coverage.

Step-10 design gate: record in `design/33-authorization-map.md` that read-back
verification is a plan-level check, not a per-write one, and why.

## Run ledger

| Block | Branch | Start commit | Implementer | Rig gate | Merged | Design reconciled |
|---|---|---|---|---|---|---|
| coordination | ~~`design53/open`~~ | `b2b0417` | coordinator | n/a | merged `ccc4b34` | n/a |
| coordination | ~~`design53/checklist`~~ | `009f0df` | coordinator | n/a | merged `c10256e` | n/a |
| 53a | ~~`design53/block-53a`~~ | `c10256e` | runner (2 rounds) + coordinator runbook fixes | **M5 PASS 2026-08-15** | merged `fc35e70` | design gate `design/33` §Why read-back verification is plan-level |

**M5 gate, 2026-08-15 — PASS.** Evidence:
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/53a-m5`. Scored by the
coordinator from the artifacts, not from a summary.

- **Step 0 PASS, and it proves the pin.** M5 ran **1787 passed / 124 skipped / 3
  warnings = 1911 collected**. 124 is M5's established Windows skip count (blocks
  49a and 50a). 1911 is the branch's collection; `main` at `c10256e` collects
  1907. **The rig ran the branch's four new tests, not `main`.**
- **Step 0b: the precondition held** — `ScanMode` **2**, `Exposure` **100.014**
  before Step 1. Without it the rest of the gate would have been no-ops.
- **Step 1 PASS. `System/Normal Mode` applies through Microclaw for the first
  time**: 11 writes, no rollback, no partial application, and read-back
  `ScanMode` **3** / `Exposure` **100.0030** — the exact call that failed at
  1/11 writes on 2026-08-14.
- **The block's one unmeasured premise is now measured TRUE.** Exposure was
  written as `100.0030` while the camera was still in ScanMode 2 (where it snaps
  to `100.0140`), and after `ScanMode` 3 landed four writes later the property
  read `100.0030`. The driver does re-derive it, so `ModeDependentCore`'s model
  is the hardware's behaviour and Step 1's PREMISE FAILURE branch was not taken.
- **Step 2 emission PASS, verified against the emitted file itself**: 11
  `core.set_property`, 11 `core.wait_for_device`, 11 `_verify_property`, last
  write at line 72 before the first verification at line 74, `_verify_property`
  defined at line 33, **no `microclaw` import and no `# NOT EMITTED`** — the two
  `get_device_property` calls emitted as no-effect comments. The file landed at
  `C:\Users\ries\AppData\Local\microclaw\`, **not in the checkout**, which is the
  workspace-resolution finding the runbook was corrected for.
- **Step 2 execution: PASS on the strongest available evidence, console line not
  captured.** `block53a-standalone.txt` is 0 bytes with `2>&1` in force, so the
  run produced no stderr and therefore no traceback and no verification failure;
  a successful script prints nothing (43n established that shape). The printed
  exit code and the two ordering counts were not saved. Not re-run — the same
  claim is proven by the artifact plus the empty stderr.
- **Step 3 PASS, both directions, repeatedly.** Four `set_config_preset` calls —
  Camera, Normal Mode, Camera, Normal Mode — each applying **11 writes** with no
  refusal, ending at `ScanMode` 3 / `Exposure` 100.0030. **Both presets are
  interdependent in the same shape** (value at index 1, mode at index 5), so the
  old code broke this switch in both directions and only looked one-sided
  because the 2026-08-14 session started in `Camera`. The symmetric trap the limb
  was written for is closed.
- **Step 4 NOT RUN, as designed** — M5 has no naturally unsatisfiable preset and
  the runbook forbids authoring one. Off-rig coverage is
  `test_verify_pass_reports_every_ignored_pair_and_rolls_back_in_reverse`.
- **Carried, not blocking:** `ChannelPlanError`'s docstring still says "no write
  was verified as applied", which was the same thing as "no write reached the
  device" only while verification was per write. It is inlined verbatim into
  every exported script. Not touched after the gate on purpose — the merged code
  is the gated code.

**53a round history.** Implementation `f82469a`, runbook `a7cad30`, round-2 fix
`23e62b6`, coordinator runbook fixes `eb49b81` and `c64d0de`; runbook pinned
`23e62b6` by ancestry. Suite 1812 / 99 / 3 on macOS, coordinator-re-run in the
worktree at both rounds rather than accepted from the report.

**Round 1 returned five findings, one blocking.** The mechanism was right first
time — writes all land, mismatches aggregate, the rollback restores in reverse
and verifies afterwards — but the failure it reported was wrong. The verify path
reused the old sentence and printed `Channel plan 'P' stopped after 0/2 writes
... applied=[]` for a plan whose every write reached the device, because
`applied.remove()` was repurposing `applied` from *landed* to *landed and
verified*. **That string is the 2026-08-06 M5 serial-timeout signature for the
opposite state** — quoted in `design/33-authorization-map.md:1311` and pinned by
`test_first_write_rejected_twice_does_not_claim_an_unverified_safe_state` — so
the same sentence would have meant both "nothing reached the device" and "all
eleven writes landed". Round 2 split the message: the verify path now reads
`applied all N writes, then read-back verification failed for K of them` with a
`mismatched=[...]` list, and the set/wait-exception path is untouched. The new
test asserted only that both pair names appear in the message, which
`attempted=[...]` satisfies on its own; it now asserts the clauses and the shape.

**The fake encodes the block's one unmeasured premise, and the runbook now says
so.** `ModeDependentCore` has the `ScanMode` write re-derive `Exposure` to the
mode's exact value. What M5 actually measured is that `100.0030` sticks when
written *while already in* mode 3 — not that a mode change re-derives a value the
driver already snapped to `100.0140`. If it does not, Step 1 fails with correct
code, and plan-level read-back is unsatisfiable for that preset while MM's own
`set_config`, which never verifies, applies it daily. Step 1 labels that outcome
**PREMISE FAILURE, not a code failure** so a red gate is not misread.

**Two coordinator runbook fixes, no code change.** Step 0b is new: every step was
vacuous if M5 already sat in `Normal Mode`, since the writes become no-ops and
`Exposure` is never written under the wrong mode — block 46's lesson that a gate
which cannot fail is worth zero, applied before booking rig time. Step 2's
standalone run needed the same precondition, because after Step 1 the rig already
holds what the script writes. And `export_session_script` resolves through
`resolve_in_workspace`, so the emitted file is not necessarily in the checkout
the operator runs PowerShell from; the block now uses the absolute path the tool
reports.

**Sequencing — satisfied 2026-08-14.** 53a touches `execute_channel_plan`, which
both 50a and 51a modify, so it waited for both. Both are now merged. 53a starts
from a commit that carries *this document* — first recorded as `3f5601e`, now
the merge of `design53/checklist`, because the checklist below is part of the
spec the runner is handed. Both the 50b and 51a runners reported their design
file absent from the start commit they were given and worked from the prompt
instead; branching from a tip that already contains the spec removes that
papercut. Code state is unchanged from `ccc4b34` through `009f0df`:
**1808 passed / 99 skipped / 3 warnings** on macOS, coordinator-measured at
`009f0df` on 2026-08-15 rather than carried over.

**Nothing here is open. Superseded 2026-08-15 — do not act on the paragraph this
replaces**, which told a cold session to branch `design53/block-53a` and write a
runner prompt. That block is merged (`fc35e70`) and its branch is deleted. This
document is now a closed record: read the checklist below for what was done and
`design/33-authorization-map.md` §"Why read-back verification is plan-level" for
where the decision lives. No scratchpad state from any session in this block is
needed; the runner prompts were never committed, by design, and their outcomes
are in `design/prompts.md`.

Neither 50a nor 51a was blocked by this one: 51a's gate limb A1 is satisfied by
`Camera` applying its 11 writes, which is what that block claims, and
`Normal Mode`'s failure is this defect and was filed here.

## Checklist

### How to use this checklist

**The process is `CLAUDE.md` §"The block workflow" and it is authoritative.**
This section is *what* is owed, not *how* the block runs; if the two ever
disagree about process, `CLAUDE.md` wins and this section gets corrected.

Design/53 has one block. It is small, and none of the ten steps is skipped for
that reason — 53a is a change to the code path that writes to real hardware and
rolls it back, so its rig gate is the point of the exercise, not a formality.

- The coordinator alone edits this section and the run ledger, on a branch, and
  commits before assigning — a worktree sees committed history, not an editor
  buffer.
- The implementer works in its own git worktree, commits, and reports. It never
  merges, and it never edits this section.
- A row is ticked when the coordinator has verified it, not when an agent
  reports it. Re-run the suite; read the diff.

### State at the 2026-08-15 close of block 53a — the live note

Opened at 53a's assignment and updated through its close; the bullets below are
current as of the merge. Checked against the repository rather than assumed:
working tree clean, `git log --oneline origin/main..main` empty, `main` at
`009f0df`, and on `origin` besides `main` only
`design34/focus-system-authorization` (6a), `florian/setup-claude-workflow` and
`port-to-jpype-acqj` — **no open block branch**. One worktree, this one. Suite
at `009f0df`, macOS: **1808 passed / 99 skipped / 3 warnings**.

- **53a is CLOSED, 2026-08-15.** Merged `fc35e70`, `main` pushed, branch deleted
  locally and on `origin`, worktree removed, notes in `design/prompts.md`, design
  gate merged into `design/33-authorization-map.md`. Suite on `main` after the
  merge: **1812 passed / 99 skipped / 3 warnings**, coordinator-run.
- **design/53 has no further blocks.** Every checklist row is ticked. What is
  recorded and owed to nobody: the exposure discrepancy under "Out of scope"
  (three different numbers for one exposure, unmeasured relationship), the
  twelve undecorated tools, and `ChannelPlanError`'s docstring, which still
  describes the per-write world and is inlined verbatim into every exported
  script.
- **Step 2's standalone-execution limb passed on empty-stderr evidence.** The
  console line was not captured and is gone; operator ruling 2026-08-15 was to
  merge on that basis. Recorded as what it is rather than upgraded.
- **53a branches from `c10256e`**, the merge of `design53/checklist`, so the
  runner's tree carries this document *and* this checklist. The ledger's earlier
  `3f5601e` is superseded, not wrong — the doc grew. The row recording that start
  commit necessarily lands after it; do not chase the tip.
- **Sequencing is satisfied.** 50a and 51a both touch `execute_channel_plan` and
  are both merged and closed. Nothing else is in flight against
  `microclaw/authorization.py`.
- **Two coordinator additions to the spec, both 2026-08-15, both above.**
  Decision 5 extends the fix to the rollback's verification, because the reverse
  loop carries the same defect mirrored and M5's preset order hid it. The
  Evidence list gains the exporter, because `_emit_recorded_channel_effects`
  interleaves the verification per write and would hand the operator a
  standalone script that fails on M5 in exactly the way this block exists to
  stop. Neither was in the document as authored.
- **M5 is the only known reproducer.** The demo machine's presets carry no
  interdependent pair, so a demo run proves non-regression and nothing else. Do
  not accept it in place of the M5 gate.
- **Do not author an unsatisfiable preset on the rig** to test the refusal limb.
  That limb has off-rig coverage; the gate says so and means it.

### 53a — verify the plan, not each write

Design: "Decision" 1–5, "What this costs", "Rejected", "Evidence".
Files: `microclaw/authorization.py` (`execute_channel_plan`, `_verify_property`),
`microclaw/tools.py` (`_emit_recorded_channel_effects`),
`tests/test_channel_plan_executor.py`, `tests/test_session_script_export.py`.

**Implementation**

- [x] `_verify_property` moves out of the write loop into a single pass after the
      last write, over the same effect list in the same order.
- [x] The write loop is otherwise untouched: cancellation polling between writes,
      `set_property`, `accepted` bookkeeping, `_wait_for_plan_device`, and the
      exception handling all behave exactly as they do today.
- [x] The verify pass names **every** mismatched pair in the error, not the first.
- [x] The rollback restores in reverse and then verifies the restored values in
      one pass (Decision 5), classifying a mismatch by `landed` exactly as a
      failed restore is classified today.
- [x] `ChannelPlanSafeStateError`, `ChannelPlanPartialApplicationError` and the
      `accepted == 0` "NO WRITE REACHED THE DEVICE" limb still select on the same
      conditions. The 2026-08-06 M5 finding is a regression test, not a memory.
- [x] `_emit_recorded_channel_effects` emits every `set_property` /
      `wait_for_device` first and the `_verify_property` calls after, mirroring
      the executor. It still emits the literal `_verify_property(` call, because
      `tools.py:1275` inlines the helper by looking for it.
- [x] No new function, wrapper or flag: this is a move, not a layer. If the diff
      grows a helper, say why in the report.

**Evidence — written before the fix, failing first**

- [x] A preset whose value is representable only after a later write in the same
      preset applies cleanly and verifies (the M5 `Normal Mode` shape, with a fake
      whose representable set for one property depends on another).
- [x] The same shape authored **mode-then-value** applies, and its rollback
      restores both without reporting an unverified safe state.
- [x] A write the device genuinely ignores fails in any order, rolls back in
      reverse, and names the pair.
- [x] An exception from `set_property` still stops at that write, with `applied`,
      `attempted` and `rolled_back` accounted exactly as today.
- [x] Multiple mismatches are all named.
- [x] `set_channel` on a single-effect preset is unchanged.
- [x] The exported script for an interdependent preset writes then verifies, and
      still compiles and defines every name it uses.
- [x] Full suite green at or above the 1808/99/3 baseline, re-run by the
      coordinator rather than accepted from the report.

**Process**

- [x] Runner prompt written to the scratchpad; the user is asked before any agent
      starts. Not committed.
- [x] Implementation reviewed from the diff, through as many returned rounds as
      it takes.
- [x] Runbook `design/53-block53a-rig-gate.md` written **on the block's branch**,
      with literal PowerShell-safe commands and expected values — not criteria —
      and the implementation pinned by `git merge-base --is-ancestor <commit>
      HEAD`.
- [x] Branch pushed to `origin` (`GIT_SSH_COMMAND="ssh -i ~/.ssh/yonce"`). No PR.
- [x] **Rig gate 53a on M5**, the three limbs in the block section above, run by
      the user. Never simulated.
- [x] Findings fixed on the same branch, sized to the finding, and re-gated until
      the limbs pass.
- [x] Merged to `main`, `main` pushed, branch deleted locally and on `origin`;
      `git log --oneline origin/main..main` empty.
- [x] Ledger rows closed and coordination notes added to `design/prompts.md`.
- [x] **Step-10 design gate:** `design/33-authorization-map.md` records that
      read-back verification is a plan-level check, not a per-write one, and why —
      a preset's values are simultaneously true and only at the end. Merge that
      before anything else is assigned.

### Carried forward, owed by nothing here

- The exposure discrepancy recorded under "Out of scope": MM's Exposure box, the
  device property and `core.get_exposure()` are three numbers, and their
  relationship is unmeasured. Nothing in 53a depends on it. Do not verify
  exposure through `get_exposure` until someone measures it.
- Twelve tools remain undecorated for script export (`CLAUDE.md`, measured
  2026-08-12). 53a adds none and closes none.
