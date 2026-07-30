# Block 14 Phase 2 gate — typed continuous-actuator registry

Branch under test: `design33/typed-actuator-registry` (`e0645f1`).
Coordinator-verified off-rig: **1179 passed / 99 skipped / 3 warnings** on macOS,
`compileall` clean, `git diff --check` clean. Baseline `main` (`4295639`) is
1160/99/3, so the branch adds 19 tests and breaks nothing.

All commands are PowerShell/cmd-safe. Run them from the repo root with the branch
checked out and the package reinstalled (`pip install -e .`) — a stale editable
install has produced convincing false failures on this project before.

Keep every output file. One dated evidence directory per run,
`block14p2_<DDMMYYYY>`.

**CLI shape.** `--port` and `--safety-config` are **global** options and must come
*before* the subcommand; only `--mm-config` and `--out` belong to `inspect-rig`.
Under uv, prefix each command with `uv run` (`uv run python -m microclaw ...`).

## What this gate can and cannot settle

**Demo core (reachable now)** settles: bridge-typed returns for the new device-type
and property-type reads, the opt-in additivity claim, the narrowing rule, the
continuous-refusal net on a real driver, preset exclusion, and the denylist
conflict.

**M5 (not reachable this session)** still owes: the illumination units migration
against a real `iBeamSmartCW-1."Power (mW)"` 0–75 driver, the live cap and ratchet
in canonical percent, and a real refusal at the bound. **A demo pass does not
discharge G7.** Do not merge the M5 half of this gate on demo evidence.

The reason the split matters here is the same one that bit Block 9b: its first demo
run failed because a fake core had invented `get_device_adapter_name` and returned a
plain `"Float"` where the bridge returns a proxy. Every new call this block adds —
`get_property_type`, `has_property_limits`, `get_property_lower_limit` /
`upper_limit`, and `get_device_type` for **device-type ordinals 7–16, which are the
published mmcorej enum but have never been confirmed over this bridge** — is
off-rig-fake-only until G2 and G4 run.

---

## G1 — Discover, don't guess (uses Block 9b)

```
python -m microclaw --port 4827 inspect-rig --mm-config "C:\Program Files\Micro-Manager-2.0\MMConfig_demo.cfg" --out block14p2_inventory > g1.txt 2>&1
```

From `block14p2_inventory\inventory.json`, record for the demo core:

1. a **StageDevice** numeric writable position property (expected: `Z`), with its
   `reported_type` and `technical_range`;
2. any **GalvoDevice** or **SignalIODevice** numeric writable property;
3. the device type string reported for each of the above.

Everything below uses the real names you just read. **If the inventory reports a
device type as a bare integer rather than a name, stop** — that is the ordinal
table failing over the bridge, and it is the one new fact in this block nobody has
confirmed on hardware.

### G1 result — PASS, 2026-07-30, evidence `block14p2_20260730`

14 devices, zero enumeration failures, fingerprint
`627f2349d6f77ee42cbe3cc92962d60c00a8a736e1eda8ad28bf2e52c2c15308` — **byte-identical
to the Block 9b demo gate**, so the demo core is in the same state that gate measured.
MMCore 12.5.0, Device API 75. (`g1.txt` shows a PowerShell `NativeCommandError`; that
is PowerShell rendering the tool's stderr progress line, not a failure — both output
files wrote.)

Every device type resolved to a **name**, no bare ordinals, across eight distinct
types: `AutoFocusDevice`, `CameraDevice`, `CoreDevice`, `HubDevice`, `ShutterDevice`,
`StateDevice`, `StageDevice`, `XYStageDevice`. The hard stop does not fire.

Three consequences that change the steps below:

1. **`Z.Position` is Float, writable, unenumerated, and has NO driver limits**
   (`has_limits: false`). That is the ideal subject for G2/G3 — with no technical
   range the declared bound is the *only* thing between the agent and the stage,
   which is the case this registry exists for. It also means the technical-range
   outer check cannot fire on `Z`; see G3b.
2. **This demo config has no GalvoDevice and no SignalIODevice.** The galvo/DAC half
   of G4 is **not dischargeable here**, so ordinals 12 and 16 — the two added for
   review finding 5 — stay unconfirmed over the bridge. Do not record G4 as covering
   them. M5's MicroFPGA `Analog Input` / `PWM` devices are the realistic place to
   confirm ordinal 12.
3. **Both `device_type_name` and `rig_inventory._device_type` prefer `to_string()`**
   and only fall back to the ordinal table. Since this bridge returns resolvable
   names, G1 proves the *names* are right; it does **not** exercise the ordinal
   fallback at all. State it that way in the design gate.

**Ruling 4 was load-bearing, and the demo proves it.** `LED.State` is Integer,
writable, with **zero allowed values and no limits** — indistinguishable from a
continuous actuator by value shape alone. Only the `StateDevice` device-type
exclusion keeps it auto-classified. A "numeric ⇒ continuous" detector would have
broken the stock demo config, not just M5.

## G2 — Opt-in additivity, then a valid narrowing

Start from `design/33-block5-demo-safety-config.yaml` (set `workspace_dir`).

```
python -m microclaw --safety-config demo-base.yaml authorization-map > g2-base.txt 2>&1
```

Expected: a `complete` map, unchanged from Block 9b's demo evidence, with **no**
`typed_continuous_actuator` entry. This is the "a config with no typed_actuators
behaves exactly as before" claim, on a live bridge.

Now copy it to `demo-typed.yaml` and add, under `rig_profile`, a **narrowing** entry
for the G1 stage property (adjust name/bounds to what G1 reported; `stage.z` is
0..200 in that file):

```
  typed_actuators:
    - device: Z
      property: Position
      kind: absolute-position
      units: um
      minimum: 0.0
      maximum: 150.0
```

```
python -m microclaw --safety-config demo-typed.yaml authorization-map > g2-typed.txt 2>&1
```

Expected: `complete`; one `typed_continuous_actuator` entry whose `detail` shows
`units=um`, the identity conversion, and `effective canonical bound 0..150 um`;
`capability: absolute-position`; `source: declared`.

## G3 — The narrowing rule refuses a widening

Change `maximum` to `5000.0` and re-run. Expected: **startup refusal**

```
Typed absolute-position Z.Position bounds 0..5000 um widen the declared core focus z
bounds 0.0..200.0 um; a typed axis entry may only narrow them.
```

The G1 typed entry stays as written above (`Z.Position`, `0..150` inside `stage.z`
`0..200`) — G1 confirmed those are the real names and that `Z.Position` carries no
driver limits, so this step isolates the narrowing rule with nothing else firing.

### G3b — the technical-range outer check, mechanism only

`Z.Position` reports **no** driver limits, so the outer sanity check cannot fire on
it. The only demo properties carrying a `technical_range` are on `Camera`. Exercise
the code path with a deliberately nonsensical declaration, and label it as such in
the evidence:

```
  typed_actuators:
    - device: Camera
      property: Exposure          # driver technical range 0..10000
      kind: absolute-position
      units: um
      minimum: 0.0
      maximum: 20000.0
```

Expected: startup refusal naming `outside the driver-reported technical range
0..10000` and the phrase `never inferred safe limits`.

**This is a code-path probe, not a rig declaration** — an exposure property is not a
position actuator, and nothing should conclude from it that such a declaration is
sensible. The semantically meaningful version of this check exists only on M5's
`Power (mW)` (0–75) in G7. Record both G3a and G3b messages verbatim; they are the
block's two distinct guards and they must not collapse into one.

## G4 — The continuous-refusal net on a real driver

G1 established that this demo config has **no galvo and no DAC**, so this step is
re-scoped to the continuous devices it does have.

**The subject must not be one the legacy alias heuristic already catches.**
`check_device_property`/`_known_continuous_raw_pair` already match
`<focus>.Position` and `<camera>.Exposure`, and both paths raise through the same
`or`, so a refusal on those proves nothing new — they would fail on `main` too. Use
properties that only `_continuous_introspection` can reach:

`demo-cat-velocity.yaml` — copy of `demo-base.yaml`, no `typed_actuators`, with:

```
  categorical_properties:
    - {device: XY, property: Velocity}    # XYStageDevice, Float, writable, unenumerated
```

`demo-cat-gain.yaml` — copy of `demo-base.yaml`, no `typed_actuators`, with:

```
  categorical_properties:
    - {device: Camera, property: Gain}    # CameraDevice, Integer, limits -5..8
```

```
python -m microclaw --safety-config demo-cat-velocity.yaml authorization-map > g4-velocity.txt 2>&1
python -m microclaw --safety-config demo-cat-gain.yaml authorization-map > g4-gain.txt 2>&1
```

Expected in both: startup refusal, `is a known continuous actuator (confirmed by the
live rig) and cannot be classified as categorical`, directing the operator to
`rig_profile.typed_actuators`.

`XY.Velocity` is the load-bearing one: it is not a position property, matches no
alias set, and is refused purely on device type plus property shape. It is also the
case design/33 cares most about — a continuous property whose semantics microclaw
cannot name, which is why Phase 2 excludes rather than bounds it. `Camera.Gain`
corroborates on a second device type.

**Optional controls, if you want the contrast on record:** the same runs with
`{device: Z, property: Position}` and `{device: Camera, property: Exposure}` are
refused by the *legacy* heuristic. Same message, older code path. Label them as
controls, not as evidence for this block.

**What this step no longer claims.** It confirms the net for `XYStageDevice` (6) and
`CameraDevice` (2), both already in the previously verified region. Ordinals **12
(SignalIODevice) and 16 (GalvoDevice) remain unconfirmed over the bridge** and no
demo run can fix that. Record them as open.

**The StateDevice non-regression is already discharged by G2-base**: that map carries
twelve `auto:state-device` entries (`Dichroic`, `Emission`, `Excitation`, `LED`,
`Objective`, `Path` — `Label` and `State` each), including `LED.State`, which is
Integer with zero allowed values and no limits. Re-running it is unnecessary; cite
`g2-base.txt`.

## G5 — Preset expansion and denylist precedence

**The preset half is not dischargeable on this demo config.** G1 enumerated all six
config groups: every `Channel` and `Channel-Multiband` preset touches only
`Dichroic`/`Emission`/`Excitation`/`LED` `Label` plus `Core.Shutter`; `LightPath` and
`Objective` touch only `State`; `Camera` and `System` touch camera settings. **No
demo preset touches `Z.Position`**, so no preset can collide with the typed pair
without editing the MM configuration itself. Record the preset-exclusion claim as
covered by the off-rig test only. (Optional: add a `Channel` preset in the MM GUI
that sets `Z.Position` and re-run — this mutates the demo config, so only do it if
you are happy to restore it.)

**The denylist half is dischargeable here.** `demo-conflict.yaml` — copy of the
working `demo-typed.yaml` (`Z.Position`, `0..150`), keeping its `typed_actuators`
block and adding the same pair as an exclusion:

```
  excluded_properties:
    - {device: Z, property: Position}
```

```
python -m microclaw --safety-config demo-conflict.yaml authorization-map > g5-conflict.txt 2>&1
```

Expected: startup refusal, `Raw property Z.Position cannot be both typed-continuous
and explicitly excluded.`

Repeat with the legacy denylist instead — same file, `excluded_properties` back to
`[]`, and a **top-level** (not under `rig_profile`) block:

```
forbidden_properties:
  - {device: Z, property: Position}
```

Expected: the same refusal. Both halves of `denied_pairs` must fail closed; the
review round found the typed declaration silently overriding the denylist, so this
step is the live proof of that fix.

## G6 — Live write behaviour through the raw property path

**Use `set_device_property`, not `move_stage_z`.** The first attempt at this step
asked the agent in plain language to "set the z-stage to 100 um"; it reasonably chose
`move_stage_z`, the Phase-1 dedicated tool, which is bounded by `check_z` against
`stage.z_max` and never consults the typed registry. That run exercised Phase 1, not
this block. Name the tool in the prompt.

With the accepted `demo-typed.yaml` (`Z.Position`, `0..150`, inside `stage.z 0..200`),
in one `serve` session, prompt in this order and keep the saved
`*_microclaw_history.jsonl` as the evidence:

1. `Use the set_device_property tool to set device Z property Position to 120`
   → succeeds. Note that `categorical_properties` is empty, so the derived
   `allowed_properties` allowlist is empty too: this write is admitted **only**
   because the typed declaration is its own authorization.
2. `Use set_device_property to set Z.Position to 175`
   → refused before the write with
   `Typed actuator Z.Position has canonical value 175 um; allowed absolute range is
   0..150 um.`
   This is the load-bearing observation: 175 is **inside** `stage.z_max` 200, so no
   Phase-1 guard would have stopped it. The refusal can only come from this block.
3. `What is the current Z position?`
   → still 120. A refusal that arrives after the hardware moved is not a refusal.
4. Control — `Use set_device_property to set XY.Velocity to 1.0`
   → refused by the authorization map as excluded, confirming the raw path stays
   closed for an undeclared continuous property.

### Recorded asymmetry — for the design gate, not a defect

A typed entry on the core focus device narrows **only the raw property path**. The
dedicated tool keeps the declared axis range: with `stage.z_max: 200` and a typed
`Z.Position 0..150`, `move_stage_z(200)` is still allowed while
`set_device_property(Z, Position, 175)` is refused. That is correct — 200 is the
reviewed bound for the axis and the narrowing rule guarantees the typed entry can
never be *wider* — but it is a real expectation trap: declaring a typed actuator does
not retroactively tighten the axis. Record it in design/33 and in the example config
comment.

## G7 — M5 only. Not dischargeable on the demo core.

1. Current, **unmigrated** M5 config →
   `python -m microclaw --safety-config m5-safety.yaml authorization-map > g7-before.txt 2>&1`
   Expected: startup refusal naming `iBeamSmartCW-1.Power (mW)`, its real 0–75
   technical range, and the `units: native` / `full_scale: 75.0` instruction. This
   is a deliberate breaking change: the current 100 % cap on that property has been
   measured to be inoperative.
2. Migrated config → `complete`, with the typed entry's canonical percent bound in
   `detail`. Verify by hand that the declared percent maps to the mW ceiling you
   actually intend.
3. Live: write at the bound (passes), just above it (refused pre-write), and a
   ratchet-violating increase (refused). After **any** write that reports failure,
   read the property back before retrying — a `Serial timeout` on M5 has been
   measured to raise while the value landed.
4. Confirm the M5 map is otherwise unchanged: same categorical entries, same
   excluded inventory, `iChrome-MLE-TCP.State` still refused.

## Demo coverage summary (settled by G1, 2026-07-30)

| Step | On the demo core | Subject |
|---|---|---|
| G1 inventory | **done, PASS** | 14 devices, fingerprint matches Block 9b |
| G2 additivity + narrowing | **runnable** | `Z.Position`, `0..150` inside `stage.z 0..200` |
| G3a widening refusal | **runnable** | same entry at `maximum: 5000` |
| G3b technical range | **runnable, mechanism only** | `Camera.Exposure` (0..10000); not a sensible declaration |
| G4 refusal net | **runnable, partial** | `Z.Position` load-bearing, `Camera.Exposure` corroborating |
| G4 StateDevice non-regression | **runnable** | `LED.State` is the sharp case |
| G5 denylist conflict | **runnable** | typed pair also in `forbidden_properties` |
| G5 preset collision | **not runnable** | no demo preset touches `Z.Position` |
| G6 live write | **runnable** | inside bound, above bound, no motion on refusal |
| G7 illumination units | **M5 only** | needs a real non-percent power property |

**What a full demo pass still does not buy.** Record these as open at merge; none is
dischargeable by any amount of demo work:

1. **Device-type ordinals 12 (SignalIODevice) and 16 (GalvoDevice)** — this config has
   neither device, and `core_device_assignments.galvo` is empty. These are the two
   ordinals added for review finding 5.
2. **A channel preset colliding with a typed pair** — off-rig test only.
3. **The XY axis ambiguity** — the demo XY device exposes no writable position
   property, only `Velocity`, so `check_xy(num, num)` is unreachable here.
4. **The entire illumination-units path** — the block's headline fix. `demo` has no
   declared power property at all.
5. **The ratchet evaluated in canonical percent** — same reason.

Items 4 and 5 are why the M5 half of this gate is not optional: the measured defect
this block exists to fix cannot be observed on a demo core.

## Demo gate RESULT — PASS, 2026-07-30, evidence `block14p2_20260730`

Windows demo core, MMCore 12.5.0, Device API 75, branch `design33/typed-actuator-registry`.
Every demo-dischargeable step passed. **This does not discharge G7.**

| Step | Result | Evidence |
|---|---|---|
| G1 inventory | PASS | 14 devices, 0 enumeration failures, fingerprint `627f2349…` identical to the Block 9b gate |
| G2 additivity | PASS | `g2-base.txt` complete, 41 entries, **zero** typed entries |
| G2 narrowing | PASS | `g2-typed.txt` complete, 42 entries; the diff against base is **exactly one entry** and nothing else moved |
| G3a widening | PASS | `g2-typed-g3.txt` — `bounds 0..5000 um widen the declared core focus z bounds 0.0..200.0 um` |
| G3b technical range | PASS | `g2-typed-g3b.txt` — `outside the driver-reported technical range 0..10000` |
| G4 refusal net | PASS | `g4-velocity.txt`, `g4-gain.txt` |
| G4 non-regression | PASS | `g2-base.txt` carries all twelve `auto:state-device` entries incl. `LED.State` |
| G5 denylist | PASS | `g5-conflict.txt` (`excluded_properties`), `g5-conflict-forbidden-prop.txt` (`forbidden_properties`) |
| G6 live raw write | PASS | `20260730_075812_..._microclaw_history.jsonl` |
| G7 illumination units | **OPEN** | M5 only |

**The two observations that carry the block.**

`XY.Velocity` was refused as a continuous actuator. It matches no alias set and is not
a position property, so only `_continuous_introspection` could have produced that
refusal — this is the first live evidence that the new device-type-scoped detector
works over the bridge.

`set_device_property(Z, Position, 175)` was refused with `allowed absolute range is
0..150 um` while `stage.z_max` was **200**. No Phase-1 guard would have stopped that
write; the refusal can only come from this block. `get_z_position` read back 120 both
before and after, so nothing moved on the refusal. The preceding write of 120
succeeded even though `categorical_properties` — and therefore the derived
`allowed_properties` allowlist — was empty, confirming live that a typed declaration
is its own authorization.

**Two things the gate reproduced that are not this block's defects.**

The `XY.Velocity` refusal arrived with the generic hint `This may be a hardware error
(device busy, stage at limit, device not found) or a connection problem.` That is the
misleading-hint limitation design/33 already records against Block 5, independently
reproduced here.

`g5-conflict.txt` also raised the pre-existing Phase-1 error about excluding a
property that aliases a built-in motion path. Expected, and unrelated to the typed
conflict on the line above it.

**Still open after this gate, and not dischargeable by any demo run:** device-type
ordinals 12 and 16; a preset colliding with a typed pair; the XY axis ambiguity; the
entire illumination-units path; and the ratchet in canonical percent. The last two are
the measured defect this block exists to fix.

## Stop conditions

Stop and do not merge on: a device type reported as a bare ordinal; any StateDevice
losing auto-classification; a typed bound accepted that widens a declared axis; a
refusal that arrives after hardware motion; the units migration silenced by any
declaration other than a correct `native`/`full_scale`; or any map reporting
`complete` while a write path in it is unbounded.

## Known limitations to record in the design gate, not to fix here

- **A typed entry on an XY stage has no axis.** The schema carries no axis field, so
  a typed `absolute-position` entry on the core XY device is validated against
  **both** the X and Y declared ranges, and at runtime `check_xy` is called with the
  written value on both axes. That is conservative — the written axis is always
  checked against its own bound, so it can only over-refuse — but a rig with
  asymmetric X/Y travel will see legal writes refused. Adding an `axis` field is
  Phase 2 follow-up work, not a defect of this branch.
- **`DEVICE_TYPE_NAMES` grew from 6 entries to 16.** Ordinals 7–16 are the published
  mmcorej enum; only 2–6 have ever been confirmed against this bridge. G4 is what
  upgrades that from published to observed. Correct the comment above the table with
  the gate's evidence before merge — it currently under-describes what the table
  claims.
