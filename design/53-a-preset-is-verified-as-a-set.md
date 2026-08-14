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

The distinction the current code misses is between *the device rejected this
write* and *this value is not consistent yet*. The first is knowable per write;
the second is knowable only at the end.

### What this costs

All writes land before a mismatch is discovered, where today the plan stops
early. That is a real change and is accepted for three reasons: a partially
applied preset is not a safer state than a fully applied one, only a less
coherent one; every effect is authorized and confirmed **before** the first write,
so nothing unreviewed reaches the rig either way; and the rollback path is
unchanged and already proven on hardware (M5, 2026-08-14, and the serial-timeout
case of 2026-08-06).

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
| 53a | `design53/block-53a` | `3f5601e` | | required — M5 | | |

**Sequencing — satisfied 2026-08-14.** 53a touches `execute_channel_plan`, which
both 50a and 51a modify, so it waited for both. Both are now merged. 53a's start commit is **`3f5601e`** — deliberately the
commit that carries *this document*, not the merge before it. Both the 50b and
51a runners reported their design file absent from the start commit they were
given and worked from the prompt instead; branching from a tip that already
contains the spec removes that papercut. Code state at `3f5601e` is identical to
`ccc4b34`: **1808 passed / 99 skipped / 3 warnings** on macOS.

**To resume cold**, the block workflow in `CLAUDE.md` is authoritative and this
document is the whole specification: branch `design53/block-53a` from `ccc4b34`,
write the runner prompt to the scratchpad, and stop to offer it rather than
spawning. No scratchpad state from the authoring session is needed — the runner
prompt was never written, and nothing here depends on one. Neither of those blocks is blocked by this one: 51a's
gate limb A1 is satisfied by `Camera` applying its 11 writes, which is what that
block claims, and `Normal Mode`'s failure is this defect and is filed here.
