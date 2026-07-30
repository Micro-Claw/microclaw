# The rig authorization map: declared limits, live cross-check, and complete write-path coverage

Date: 2026-07-21

Promoted out of `design/32` Finding 1. That finding's immediately-actionable core
— a strict, versioned safety-config schema that fails startup with all validation
errors (structure, semantics, finite numeric bounds) — stays in design/32. This
document specifies the larger, multi-phase subsystem that grows on top of it: how
completeness is defined and enforced against a live rig, so that no actuator the
tools can reach is left unbounded.

The one-line problem: strict *file* validation proves the numbers in the config
are well-formed, but not that they *cover the hardware*. An operator can hand-mark
a file `reviewed: true`, pass every structural and semantic check, and still omit
an actuator that a tool can drive. Closing that requires a declared rig profile, a
live cross-check before any tool is exposed, and complete coverage of every path
by which microclaw can cause a hardware write.

## Delivery phases

This is a program of work, not one atomic change. Its completion boundaries are:

1. **Core authorization map and minimum classification registry:** add the
   declared profile schema, require property allowlist mode (or disable the
   generic setter), classify every enabled write path as a built-in typed
   capability, a reviewed categorical property, or excluded, build the effective
   write-path map, and fail startup when the live rig exposes an undeclared
   reachable actuator. An arbitrary property or preset effect that cannot yet be
   classified is excluded in this phase; allowlisting its name alone does not
   admit it.
2. **Extended typed continuous-actuator registry (landed):** driver-specific
   semantics and unit conversions beyond the built-in adapters, routed through
   typed guards, still excluding every unclassified continuous write
   (Phase 2 / Block 14; merge `47f6702`). See "Phase 2 landed" below.
3. **Acquisition/dose extension (landed):** design/32 Finding 2's frame,
   duration, byte, illuminated-time, and cumulative-session budgets are now
   authorization policies (Phase 3 / Block 5; merge `3438b90`).
4. **Channel-plan executor:** expand presets into immutable authorized plans and
   implement ordered application, waits, verification, cancellation, and
   partial-failure cleanup.
5. **First-launch setup assistant:** enumerate devices in restricted setup mode,
   write an unreviewed profile, and require review plus a normal restart.

Opaque hardware-motion plugins are not another map phase: they are a separate
trust policy. Guaranteed mode disables them unless a narrow typed adapter makes
their effects enumerable; the explicitly selected degraded mode suspends the
completeness claim.

## Completeness is checked against a declared profile

Completeness is checked against a **declared** rig profile, not a discovered one.
The config states which actuators this file is responsible for; validation then
requires finite limits for each. This is deliberate: `load_safety_config` runs at
ordinary startup before constructing a controller, so it cannot assume a
connected core to enumerate capabilities from. Declaration keeps the structural
and semantic checks at load time with no core, on the same trust model as
`reviewed:`.

### Reviewed-unbounded range edges must survive to the cross-check

Design/32's Job 4 lets a stage/focus range edge be either a finite bound or an
explicit reviewed-unbounded object (`{unbounded: true, reason: "..."}`). Its
parser owns and returns `ParsedSafetyConfig`, containing both runtime
`SafetyConstraints` and authoritative `RangePolicy` records keyed by structured
`ActuatorId` values. This document does not redefine that parsing
representation; it owns the live policy that consumes it.

Core ranges arrive under design/32's tagged identities because load-time parsing
has no live core and the `stage` config block carries no device label. Binding
them to concrete hardware is this document's job: at cross-check time
`validate_live_rig` resolves `source="core_xy"` to
`core.get_xy_stage_device()` and `source="core_focus"` to
`core.get_focus_device()`, then checks those live devices against the applicable
X/Y and Z policies. Named stages use `source="named"` and their real `device`
label, so no reserved string participates in identity. A live core device label
that is also declared as a named stage is a declaration conflict the cross-check
must reject rather than silently pick one policy.

`validate_live_rig` treats a retained `RangeEdge` with `bound is None` as an open
edge rather than trying to infer intent from `parsed.constraints`, where both an
open edge and an unvalidated default would appear as `None`. In guaranteed mode,
every reachable stage or focus axis requires two finite edges, so an open stage
edge fails closed even when its reason is retained and logged. Reviewed-unbounded
stage edges exist for migration, audit, and explicitly degraded operation; they
are not full containment. A future actuator kind may accept an open edge in
guaranteed mode only if this design adds an explicit capability-specific policy
and explains why that still constitutes a guarantee.

## The live cross-check: fail closed on reachable, undeclared hardware

Declaration alone cannot prove completeness, however: an operator can
accidentally omit an actuator from the declared profile. After connection, but
before the agent or any mutation tool becomes available, the controller must
cross-check the declared profile against the actual devices. The gate is the
intersection of three conditions: a device fails closed when it is **connected,
actuatable through the effective authorization surface, and lacks complete
declared limits**. Scoping to reachable hardware rather than blindly requiring
limits for every connected read-only or observational device is deliberate, but
"reachable" must be computed mechanically, not inferred from a short list of
high-level tool names or device categories.

Mechanical computation does not mean inferring actuation semantics from
Micro-Manager property names. Phase 1's minimum registry knows the dedicated
built-in adapters and reviewed categorical entries; everything else that might
write is excluded. The extended registry can later make additional raw paths
reachable only by declaring their exact semantics and units. This conservative
rule removes the circular dependency in which a core map would otherwise claim
completeness before it could distinguish a categorical setting from continuous
motion.

The authorization map must cover every path by which microclaw can cause a
write: dedicated stage, camera, acquisition, and illumination tools; every
device/property changed by an allowed channel preset; the generic
`set_device_property` tool; autofocus; and plugin-mediated hardware control.
Today that surface is broad: `set_channel` can actuate filter wheels,
`run_autofocus` can drive an autofocus device, and `set_device_property` can
reach almost any writable connected device. Therefore a device is presumed
reachable unless a reviewed allowlist or an equally strong code-level gate
proves that every write path to it is unavailable. A global tool name is not a
sufficient exclusion when another tool can reach the same hardware.

A reachable device with no complete declaration is a startup error, not a
warning. The other safe option is a per-device/per-property exclusion enforced
across the controller and every tool path, which removes the hardware from the
effective authorization surface. Merely warning, or hiding one dedicated tool
while leaving a generic property, preset, autofocus, or plugin path available,
recreates the original unbounded-actuator hole.

## "Complete" branches by actuator kind

What counts as a "complete declaration" is not one thing, because the reachable
surface is not one kind of device. The cross-check must branch by actuator kind:

- **Stage axes** require finite, ordered minimum and maximum travel limits.
- **Exposure** requires a finite, strictly positive maximum; the current guard
  has no exposure minimum.
- **Illumination power** requires a finite maximum in its valid percentage range
  plus a finite, valid step-factor policy. These are different constraints from
  stage travel and must be validated according to what the illumination guard
  actually consumes.
- **Acquisition and dose** require design/32 Finding 2's landed frame, duration,
  byte, illuminated-time, and cumulative-session budgets; a safe per-frame
  exposure maximum does not make an unbounded acquisition complete. This schema
  extension landed after strict validation, preserving that earlier delivery
  boundary while making acquisition its own completeness branch.
- **Categorical or discrete write paths** — filter-wheel positions, binning,
  trigger modes, shutter state, arbitrary `set_device_property` targets — have no
  minimum below a maximum, so a numeric bound is meaningless for them.
  Completeness there means the write path is either explicitly authorized by a
  reviewed allowlist and any capability-specific policy, or provably excluded
  from every tool path. An `allowed_properties` entry is authorization; a
  `forbidden_properties` entry is exclusion, not an authorization declaration.
  The codebase already splits continuous constraints from categorical policy, so
  the cross-check should preserve that distinction rather than forcing min/max
  fields onto hardware that has none.

## The continuous/categorical seam: raw writes that drive continuous actuators

The continuous/categorical split has a seam, and completeness must close it: a
raw property write can *target a continuous actuator*, and `allowed_properties`
authorizes the write without bounding it. `check_device_property`
(`safety.py:252`) re-applies a numeric guard only when the target is the
**current** focus/camera/XY device and the property name is in a tiny alias set
(`{"position"}`, `{"exposure"}`, `{"x", "y", ...}`) — its own docstring calls this
a heuristic, not a gate. So an allowlisted raw write to a *second* Z drive, to the
core focus device under a driver-specific name the heuristic never matches
(`"Position (um)"`, `"PositionZ"`, ASI/PI names — `prop.lower()` will not match
`"position"`), or to an unrecognized continuous-control property passes the
categorical completeness test while potentially driving an actuator past its
numeric limits. Configured `illumination.power_properties` are still checked by
`set_device_property`; the illumination bypasses are instead an unrecognized
power property or another route such as `set_channel`, which applies a preset
without calling the per-property illumination guard. These are precisely the
kind of holes `check_device_property` warns about, re-entering through the
completeness definition.

The cross-check therefore needs one rule at the seam: **a raw property write whose
target is a continuous actuator must be bounded by that actuator's numeric policy,
not merely present in `allowed_properties`.** Completeness for such an entry means
the allowlist authorizes it *and* a typed numeric guard provably applies to it.
Exact device and property names are necessary but not sufficient: a numeric
property may mean an absolute target, relative movement, offset, velocity,
voltage, percentage, or a value in device-native units. Passing all of those
numbers to an absolute-position guard would create false confidence.

## Typed adapters and the decision rules

The safest default is therefore to forbid raw property writes to continuous
actuators and expose them only through dedicated typed adapters.
`move_named_stage` (`tools.py:334`) demonstrates the adapter pattern: its own
path is fail-closed via `check_named_stage` unless the profile declares
per-device bounds. It does **not** prove that the actuator is reachable only
through that tool; raw properties, presets, or plugins may still reach the same
hardware and must independently be excluded or guarded. Generalizing the typed
adapter pattern is familiar, but the actuator registry and semantic mapping are
new machinery. An exception requires the declared rig profile to map the exact
device/property pair to its operation semantics, units, conversion into the
guard's canonical units, and applicable numeric policy. Trusted code performs
that conversion and validates the resulting absolute effect before writing. The
decision rules are:

- Exact mapping with known semantics and units: route through the corresponding
  typed adapter and guard.
- Known continuous actuator with unknown semantics or units: fail startup or
  exclude the raw write.
- Unknown property that may actuate hardware: categorical allowlisting alone is
  insufficient to claim numeric safety; exclude it until classified.
- Preset expansion: apply the same classification and typed policy to every
  underlying device/property write.

This forces `check_device_property` to stop being a current-device, name-alias
heuristic for any path claimed to be complete. A raw continuous write with no
typed mapping is a startup error, not an allowlisted pass.

## Channel presets: authorize the effects, not the name

`allowed_channels` authorizes preset names, not their underlying effects. At
startup the cross-check must expand every allowed preset into the complete set
of device/property writes Micro-Manager will apply, then evaluate each write
against the authorization map. A preset that enables illumination, changes
power, or reaches an otherwise excluded device must satisfy the corresponding
illumination or device policy; membership in `allowed_channels` alone is not a
blanket safety declaration.

### Preset mutability is a time-of-check/time-of-use gap

Startup expansion is an early-failure check, not sufficient runtime
authorization. A Micro-Manager preset may be edited after startup, so a preset
name can retain its allowlist membership while its underlying writes change.
Before applying a gated preset, trusted code must freshly expand and authorize
every setting it will produce.

Re-authorizing and then calling `set_config` (as `set_channel` does today,
`tools.py:361`) does not close the gap: `set_config` makes Micro-Manager re-read
the preset definition at apply time, so there is a residual window between
microclaw's expansion-read and MM's apply-read in which a GUI edit makes microclaw
authorize the old writes while MM applies the new ones. The fix is to stop
delegating the apply for gated presets. That is not as simple as replacing
`set_config` with a loop of `set_property` calls: Micro-Manager may supply device
ordering, waits, synchronization, configuration-state bookkeeping, and failure
behavior that an approximate replay would lose.

Treat this as a new trusted **channel-plan executor**. It captures one immutable
list of settings, authorizes that exact list, then applies those captured values
with defined ordering and device waits, per-write typed authorization, and
read-back verification. It must define cancellation behavior and what happens
when write N of M fails: best-effort rollback where safe, otherwise a documented
safe-state cleanup and an explicit partial-application error. A snapshot/hash of
the expansion remains useful as a drift detector, but the executor must never
authorize one expansion and ask Micro-Manager to re-read another at apply time.
If Micro-Manager does not expose enough semantics to reproduce a preset safely,
gated presets must be disabled rather than replayed approximately.

Phase 4 deliberately treats `Core.Shutter` retargeting conservatively. The target
label must name a device declared in `illumination.shutters`, and selecting it
requires the same human confirmation class as enabling illumination. Retargeting
does not itself open a shutter, but with AutoShutter it selects which reviewed light
source fires on the next exposure. Demo evidence covers only idle retargeting; this
policy is intentionally revisitable after dark/beam-blocked measurements on real
drivers. Every other `Core.*` effect remains excluded.

That policy has a deliberate confirmation-frequency cost. The executor does not
skip a captured write merely because its value already matches the current value,
and all four presets in the checked-in Phase 4 demo profile name the same shutter.
Consequently every `set_channel` on that configuration raises an
illumination-class prompt, even when the shutter is not actually being retargeted.
This follows from the chosen rule—admit `Core.Shutter` only when its target is a
declared shutter, and confirm every admitted write—but prompt fatigue is itself a
safety risk if operators learn to click through. The policy stands for Phase 4.
If revisited, the exact narrowing is to request confirmation only when the captured
`Core.Shutter` value differs from its current value; no such narrowing is
implemented here.

The executor polls cancellation between property writes only. Each pyjavaz bridge
round trip owns a single lock, so an in-flight set, device wait, or read-back cannot
be killed by another thread. A stop therefore takes effect at the next write
boundary and triggers rollback; it is not a mid-write interrupt.

This closes the preset-definition TOCTOU gap only for sessions with a rig profile
and therefore an authorization map. A map-less legacy session still delegates the
apply to Micro-Manager with `set_config` and retains the gap for compatibility.

## Plugins are a different boundary

Plugins are a different boundary. An arbitrary Java hardware-motion plugin can
perform opaque or dynamic operations that the parent cannot reliably enumerate,
map to properties, or intercept. The authorization map must not claim
per-device completeness for such a plugin merely because
`plugins.allow_hardware_motion` is true. Under the completeness guarantee, a
hardware-motion plugin must be disabled, wrapped behind a narrow typed adapter
whose effects the parent can validate, or run only in a distinct explicitly
selected degraded/trusted-plugin mode. An opaque plugin is forbidden while the
system claims guaranteed completeness. The degraded mode must prominently state
at startup and in the UI that the authorization-map completeness guarantee is
suspended; broad trust is not equivalent to positively authorizing a finite set
of device writes.

## The declaration burden is coupled to the property-gate mode

The declaration burden this imposes is coupled to the property-gate mode, and
the two features belong together. In the default denylist mode
(`forbidden_properties`), `set_device_property` can reach almost every writable
connected device, so the reachable surface collapses to "nearly everything
connected" and completeness becomes a wall the operator hits on every rig. In
allowlist mode (`allowed_properties`), the generic-property portion of the
surface is small and enumerable. Channel presets, autofocus, plugins, dedicated
numeric actuators, and acquisition paths remain independent capabilities and
must be added to the map separately; the property allowlist does not constrain
them by implication.

For the new schema's normal guaranteed mode, completeness is mandatory, not an
optional rig preference. The explicitly selected degraded/trusted-plugin mode
above is outside that guarantee and must never be reported as complete.
Therefore normal mode must either require `allowed_properties` or disable the
generic `set_device_property` tool when only a denylist is supplied. Continuing
to expose the generic setter under denylist semantics cannot satisfy a finite,
reviewable startup authorization map. Explicitly forbidden properties are
removed from the reachable set; every remaining write path must be positively
authorized and, where applicable, bounded by its capability-specific policy.

## This generalizes an existing fail-closed pattern

This generalizes a pattern the codebase already trusts. `check_named_stage`
(`safety.py:357`) already fails closed — a labelled stage with no `named_stages`
entry cannot be moved at all — but as a *per-move runtime* gate, and only for
named stages. The core focus, XY, and camera devices are the gap: they fall back
to unconstrained when their limits are absent. The cross-check hoists that
existing fail-closed behavior to startup and extends it across the core actuators
the tool set exposes.

## Where the cross-check runs

The cross-check needs a concrete home in the startup sequence. In `run_session`
(`__main__.py:165`) the controller is constructed and connected at line 175,
and `_repl` — which dispatches tools — runs immediately after; `serve` has the
same shape (`webserve.py:163-165` then `build_app`). There is no existing shared
lifecycle hook or separate tool-registration step: the tool set is static and
`execute_tool` can dispatch once the agent is invoked.

Add one shared `validate_live_rig(ctrl, parsed_config)` startup function that
builds the effective authorization map and performs the authoritative
cross-check. Both entry points must call it after a successful connection and
before accepting a prompt, constructing the serving app, or invoking
`run_agent_iter`/`execute_tool`. Keeping this in one shared function prevents the
CLI and web paths from acquiring subtly different safety gates.

## Populating the profile: a first-launch setup mode (later work)

Populating the declared profile through a first-launch interactive prompt is
**new, later work**; the current `microclaw init` only copies the example YAML
and opens it for hand editing. The future flow needs an explicit setup mode to
resolve its bootstrap problem:

1. Connect only for device enumeration, without starting the agent or exposing
   any mutation tools.
2. Walk the operator through the detected devices and write an unreviewed rig
   profile with no inferred safety limits.
3. Disconnect and require the operator to enter limits and mark the file
   reviewed.
4. Restart normally, validate the file before controller construction, then
   perform the authoritative live-device cross-check before enabling tools.

Even enumeration is hardware contact, and loading a Micro-Manager configuration
may initialize devices. Setup mode therefore cannot promise that the safety file
precedes the *first hardware contact*; it promises that no agent-directed or
tool-directed hardware action is possible before review. Its implementation
should use the least-active connection path Micro-Manager supports and document
any unavoidable device initialization.

### Landed: Block 9b — read-only rig inventory (merge `041f6f8`, 2026-07-29)

Step 1 of that flow now exists as `microclaw inspect-rig`
(`microclaw/rig_inventory.py`). It is **discovery infrastructure, not an
authorization mechanism**: it approves nothing, writes no safety config, and
emits no YAML aid at all — a deliberate decision recorded in the module
docstring, because a config-shaped derivative invites an operator to mistake an
observed property for a reviewed decision.

**Read-only boundary.** Only `get_*` / `is_*` / `has_*` calls. Two mechanical
tests hold that line rather than a reviewer's attention: a recording fake core
fails on any attribute outside an approved set, and every core call the module
makes is checked against **305 method names extracted from MMCoreJ.jar with
`javap`**. That second test exists because a hand-written fake had *invented*
`get_device_adapter_name`, so the suite was green against an API CMMCore does not
have. Prefer mechanically derived fixtures over hand-maintained allowlists here;
this failure class has now cost three blocks.

**Unlike every other entry point, it must run with no safety config.** A rig that
already has a reviewed config does not need discovery. Supplying one is optional
and only annotates a comparison.

**Inventory schema** — `microclaw.rig-inventory/v1`, deterministic, with three
structurally separate regions. Phase 5 must preserve that separation rather than
flattening it:

| Region | Meaning |
|---|---|
| `facts` | mechanically observed: core identity, device assignments, devices with types/adapters/properties/state labels, config groups with fully expanded presets, per-query failures |
| `heuristic_candidates` | questions for a human — never decisions |
| `human_decisions` | populated only from a supplied reviewed config |

Driver-reported property limits are recorded as `technical_range` with
`"source": "driver_reported"`. **They are technical ranges and never inferred
safe limits**; Phase 2 may consume them as evidence, never as bounds.

**Two findings that constrain the handoff:**

1. *Heuristic candidates must not assert relationships the code has not
   established.* The first implementation emitted illumination power/enable
   **pairs** as a Cartesian product — on a real rig, one device with 3 power and
   3 enable candidates produced 9 rows, each reading as "this enable gates this
   power", when at most 3 such relationships exist and the probe's own comments
   say only one two-state property actually gates emission. It now emits
   per-device groups that name both lists and infer nothing. Phase 4's channel
   analysis should take the same care.
2. *The same physical actuator can appear twice in different units.* All three
   Luxx lasers expose `Laser Power Set-point Select [%]` **and** `[mW]`. These
   are surfaced as possible duplicate representations. Declaring both would
   double-bound one actuator, and `illumination.max_power_percent` is compared
   against the raw value, so it is not a percentage on the mW variant.

**Evidence and its limits.** The live demo-core gate passed on Windows: identical
fingerprints across two unchanged runs with byte-identical `facts`, zero
enumeration failures, adapter names on all 14 devices, six StateDevices with
complete labels, both shutters, four fully expanded `Channel` presets. Its
**first run failed**, and that failure is why two bridge defects were found —
`reported_type` had been stringifying a pyjavaz proxy, embedding a heap address
that made the fingerprint nondeterministic on any real rig. Non-primitive bridge
values are now refused rather than stringified, so an address cannot reach the
payload by any path.

A separate **offline replay of real M2 data** (`design/33-block9b-m2-replay.py`;
30 devices, 395 properties, 11 serial ports, MicroFPGA hub, three lasers) covers
production scale and serial/FPGA devices, and produced both findings above.

**The cross-rig gate is still open.** Live M5 owes four things nothing so far has
exercised: real enumeration failures (M2 recorded none), credential redaction (M2
has no credential-like properties), live config groups and state labels beyond
what a `.cfg` holds, and bridge-typed returns. Phase 5 should not treat the
inventory format as frozen until those land; the schema is versioned so a finding
can bump it.

## Tests

Controller-startup tests should prove that undeclared or insufficiently bounded
live actuators fail closed before either entry point accepts a prompt or invokes
the agent/tool dispatcher. Test the authorization map through dedicated tools,
channel presets, generic property writes, autofocus, and plugins, including cases
where disabling one path still leaves another path to the same device. Mutate a
channel preset after startup and prove the runtime gate refuses it before any
underlying property write. For an accepted multi-device plan, prove the executor
applies exactly the captured values with the required ordering, waits, and
read-back checks; inject a failure after every write and verify rollback or
safe-state cleanup plus an explicit partial-application result. Also prove an
opaque hardware-motion plugin is rejected in guaranteed mode and can run only
after the operator explicitly selects the degraded mode. When the setup prompt is
implemented, test that it cannot expose the agent or mutation tools, never invents
limits, always writes an unreviewed profile, and requires a reviewed restart
before normal operation.

## Phase 1 landed (2026-07-23)

Phase 1 shipped as Block 3 (`microclaw/authorization.py`, merge `0124ffd`;
implementation `fc346a4`). Two blockers were caught in coordinator review and
fixed on-branch before merge: illumination was initially unmodeled (so guaranteed
mode blocked all laser writes while claiming completeness), and a nonexistent
channel preset dumped a raw JNI stack trace. Rig-verified on the **M5** microscope
in guaranteed mode.

### What Phase 1 actually implements

- **Schema.** `rig_profile: {mode, categorical_properties, excluded_properties}`.
  There is **no separate actuator manifest** — the declared stage/focus **ranges**
  (`ParsedSafetyConfig.ranges`, from design/32) are the stage-position completeness
  target. `excluded_properties` is required whenever `rig_profile` is present (both
  modes); `categorical_properties` is required in guaranteed mode (may be empty).
- **Built-in typed capabilities** are a code constant, not config-declared:
  `BUILTIN_TYPED_CAPABILITIES = {stage-position, exposure, illumination}`.
  Illumination (configured `illumination.shutters`/`power_properties`) is a Phase-1
  typed capability, gated by the existing `check_illumination` confirm/cap/ratchet;
  its pairs bypass the categorical allowlist but not that guard.
- **`validate_live_rig(ctrl, parsed_config)`** runs in both entry points after
  connection and before any prompt/app/agent/tool, fails closed on: an undeclared
  reachable core/named stage axis, an open range edge in guaranteed mode, a
  core/named identity conflict, an unclassified reachable write, and an opaque
  motion plugin in guaranteed mode. It attaches the map to the controller; runtime
  gates (`authorize_property_write`, `authorize_channel`, `authorize_path`) enforce
  it per write.
- **Categorical raw writes** are the only admitted generic property writes; a pair
  that names a known continuous actuator (current focus/XY/camera position/exposure)
  cannot be declared categorical. Unclassified/continuous raw writes → excluded.
- **Presets** are expanded from the `Channel` config group only; unclassified
  effects exclude the preset (no channel-plan executor — that's Phase 4).
- **Read-only enumerator:** `microclaw ... authorization-map` connects, prints the
  effective map + verdict as JSON, and exits without exposing any mutation surface.

### M5 field findings (guaranteed mode, verdict complete)

- Typed: `SmarAct 2D` (XY), `PIZStage` (Z), `HamamatsuHam_DCAM` (exposure),
  `iBeamSmartCW-1`/`-Booster` (illumination). Categorical: two Thorlabs filter
  wheels + ELL6 (Label+State) and the iChrome selector (Label).
- Excluded (accepted): MicroFPGA `PWM`/`TTL`/`Servos`/`Laser Trigger`,
  `Analog Input`, `MicroFPGA-Hub`, all `COM*`, Elliptec `ELL17/ELL20`,
  `SmarAct 1D` and base `iBeamSmartCW` (undeclared by operator choice), ROI, MDA.
- **The FPGA-laser ceiling is real and correct.** M5's lasers are TTL/PWM-gated
  through the MicroFPGA; those paths have no Phase-1 typed adapter, so they are
  excluded (fail-closed) rather than bounded. A live write to `PWM.Position0`
  (255/full-scale) was correctly **refused**. Software power set-points on the
  Toptica drivers *are* boundable as illumination; FPGA-modulated firing is not,
  until Phase 2 typed continuous adapters (Block 14) or an explicit degraded session.
- Verified live: categorical write passes; illumination power write + confirm-gated
  enable pass; excluded (`PWM`) and name-mismatched (`Power` vs `Power (mW)`) writes
  refused; fail-closed startup identical across CLI/`serve`/enumerator.

## Phase 1 fast-follow landed: StateDevice auto-classification (2026-07-23)

Shipped as Block 3b (merge `9491361`; implementation `d80ae39` + rig-finding fix
`aaa41d4`). Rig-verified on **M5** in guaranteed mode. This replaces the
"StateDevice fast-follow (agreed)" item that previously sat under the follow-ups
below.

### The rule

- Any device `core.get_device_type()` reports as a **StateDevice** has its own
  `Label`/`State` classified `reviewed_categorical_property` with no declaration.
  Nothing else on that device is admitted — `Speed`, `Delay`, mode and serial
  properties stay unclassified and are refused.
- **Auto-classification fills vacuums only.** If the operator has ruled on either
  position property — `categorical_properties`, `excluded_properties`, or the
  `forbidden_properties` denylist — auto-classification skips that device's whole
  discrete position. Declaring one position property is a *narrowing*, not an
  invitation to admit the other. The rule is scoped to the position pair, not the
  device: excluding, say, `Wheel.Speed` must not silently kill the wheel's
  auto-classification (pinned by test).
- **Shutter carve-out**, decided only by MM device type, `Core.Shutter`, and the
  reviewed `illumination:` block — **never by device name**. An MM `ShutterDevice`
  is not a StateDevice and never qualifies; a state device that *is* `Core.Shutter`,
  or that carries any configured `illumination.shutters`/`power_properties` entry,
  is skipped device-wide and keeps its typed illumination gate.
- **Fails closed throughout:** an unreadable device type or property list leaves
  that device excluded, and an unreadable `get_shutter_device()` disables
  auto-classification entirely (the carve-out cannot be applied, so nothing is
  admitted).
- **Presets:** an auto-classified pair counts as classified during preset
  expansion, so a preset that only moves filter wheels needs no declaration. This
  is a real widening over Block 3, and is what lets a map stay `complete` after
  the declarations are removed.
- **Both gates.** A raw write passes the map *and* `SafetyGuard.check_property`,
  whose allowlist is derived from `categorical_properties` at parse time.
  `validate_live_rig(ctrl, parsed_config, guard=...)` hands the guard exactly the
  auto-classified pairs via `admit_auto_classified()`. The read-only
  `authorization-map` enumerator deliberately passes no guard.
- **Provenance is visible:** every categorical entry carries
  `source: "declared"` or `source: "auto:state-device"` in the map JSON, so an
  operator can verify an auto-classification without diffing the config.

### M5 field findings (guaranteed mode, verdict complete)

- Auto-classified: two Thorlabs filter wheels and the `Thorlabs ELL6`
  (`Label` + `State` each). ELL6 is a **Bertrand-lens flip** — it changes the path,
  it does not gate light, so auto-classification is correct for it.
- With the operator's unmodified config the feature is **inert**: all three
  Thorlabs devices already declare both position properties, so nothing
  auto-classifies. Removing those declarations moves exactly those six pairs from
  `declared` to `auto:state-device`; both maps are `complete` at 40 entries, with
  illumination and the 20-device excluded inventory unchanged.
- **`iChrome-MLE-TCP` — the finding that produced the vacuum-filling rule.** The
  reviewed M5 config declares only `Label`, with an operator comment recording
  genuine uncertainty about whether the driver's write property is `Label` or
  `State`. The first implementation auto-admitted `State` on a **laser engine**,
  against an explicit narrowing. With the rule in place `State` is refused live —
  including after a human typed an explicit confirmation, which is the point: the
  map is not overridable from the conversation.
- **M5 has no core shutter device** (`get_shutter_device()` is empty). That limb
  of the carve-out therefore protects nothing on this rig, and the whole shutter
  carve-out rests on the `illumination:` block being complete. A light-gating
  StateDevice absent from `illumination:` *and* unmentioned anywhere in
  `rig_profile` would still auto-classify. That is the intended design, but on a
  rig with no core shutter it is a sharper edge than it looks.
- **`iChrome-MLE-TCP.State` does NOT gate emission — suspicion retracted.** During
  the gate a laser slot read `enabled=1` shortly after a `State` 1→2→1 write, and
  this note initially recorded that as possible evidence the property gates light.
  The operator then reproduced the `State` writes manually and the laser did not
  turn on; the slot had been enabled by an accidental power-button click in the EMU
  GUI. The property behaves as the benign output/mode selector originally assumed.
  Recorded because the wrong inference was written down first: a correlation across
  two sessions on a shared rig is not causal evidence, and the EMU slot index is not
  the `State` value.
- **Open item on the M5 reviewed profile, not on this code:** the semantics of
  `iChrome-MLE-TCP` `Label`/`State` are still undocumented — the config carries an
  operator comment saying as much — and `Label` is a bare categorical write on a
  multi-laser engine with no confirmation, cap, or ratchet. That predates Block 3b
  and is unaffected by it. Lower priority given the finding above, but a device
  whose states nobody can name is a poor thing to leave in `categorical_properties`.
- **The vacuum-filling rule does not depend on any of this.** It stands on the
  principle that auto-classification must not override an explicit operator
  narrowing, whatever the device turns out to do.

## Phase 2 landed (2026-07-30)

Shipped as Block 14 Phase 2 (merge `47f6702`; implementation `9bf7599`, coordinator
review rounds `e0645f1` and `47295f2`). Gated on the Micro-Manager demo core and on
**M5**; the gate record and all evidence pointers are in
[`33-block14-phase2-gate-prompts.md`](33-block14-phase2-gate-prompts.md).

### What Phase 2 actually implements

- **Schema.** `rig_profile.typed_actuators` — an optional, strict list of exact
  `(device, property)` entries declaring `kind`, `units`, canonical `minimum`/`maximum`
  and, where the kind requires it, `full_scale`. `schema_version` stays at **2**: a
  valid schema-2 file that declares no typed actuators parses exactly as before.
- **Two kinds only, both backed by measurement.** `absolute-position` (canonical µm)
  and `illumination-power` (canonical percent, `units: percent | native` with
  `full_scale`). Relative/offset moves, velocity, voltage/DAC, camera ROI and
  MicroFPGA pulse duration are excluded, each because no measured conversion exists —
  see "Excluded from Phase 2" below.
- **No built-in driver table.** Semantics are operator-declared and then validated
  against live driver introspection. There is no ASI/PI/Thorlabs property-name
  catalogue; a device the operator has not declared stays excluded.
- **Live validation.** Each declared entry is checked against the connected rig for
  device and property existence, writability, numeric type, and non-enumeration.
  Driver-reported limits are used **only** as an outer sanity check on a declared
  bound, never as an inferred safe limit.
- **The seam is closed.** `check_device_property`'s alias heuristic is no longer what
  completeness rests on. A declared-categorical pair that the live rig reports as a
  continuous actuator is a startup error, scoped by MM device type:
  `StageDevice`, `XYStageDevice`, `CameraDevice`, `GalvoDevice`, `SignalIODevice`
  and `GenericDevice`, excluding `pre_init` properties. `StateDevice` is deliberately
  out — see the auto-classification interaction below.
- **A typed axis entry may only narrow.** When a typed `absolute-position` entry names
  the live core focus device, the core XY device, or a device with a `named_stages`
  entry, its bounds must lie within that axis's declared range, and the built-in axis
  guard still applies to the write in addition to the typed bound.
- **Exclusion beats declaration.** A pair in both `typed_actuators` and either
  `forbidden_properties` or `excluded_properties` is a startup conflict, and the
  denylist is still consulted at runtime. This is the Block 3b vacuum rule applied to
  a new declaration mechanism.
- **Presets.** A preset effect landing on a typed pair is classified and the preset is
  excluded, for the same reason illumination pairs are: no channel-plan executor
  exists to apply it under the typed guard. That remains Phase 4.
- **Fail-closed introspection.** In guaranteed mode, failing to introspect a pair the
  operator declared categorical is a startup error rather than a silent admission.
  Degraded mode keeps best-effort admission.

### M5 field findings (guaranteed mode)

- **The units defect is confirmed and fixed.** `iBeamSmartCW-1."Power (mW)"` reports a
  driver range of **0.0–75.0** — measured through the bridge for the first time,
  where previously it was a note carried from a session. A config declaring it with
  `max_power_percent: 100.0` now fails startup with an actionable migration message;
  the migrated declaration (`units: native`, `full_scale: 75.0`) produces a complete
  map with `effective canonical bound 0..40 percent`.
- **Migration makes the ceiling expressible, not automatically meaningful.** With
  `units: native` and `max_power_percent` left at 100, raw 75 mW converts to exactly
  100 % and is still permitted. The operator must then choose a real ceiling. Because
  `max_power_percent` is global, one canonical percent cap now means the same thing
  across mixed-unit drivers — which is the point of the canonical unit.
- **The ratchet evaluates in canonical percent, proved live.** From a device at
  5.0 mW, a write of 30 mW was refused as `6.7% → 40.0%` against the 3× ratchet. The
  6.7 % is 5/75: the device read was converted from native mW before the ratio was
  taken. This is the one claim that had no off-rig or demo equivalent.
- **The typed cap and the ratchet are independent.** 31 mW was refused by the typed
  cap at 41.33 % while being only 1.03× on the ratchet; the 30 mW case was the
  reverse. Neither guard subsumes the other. 30 mW = 40.0 % passed at exactly the
  inclusive bound with no float drift.
- **Every hazardous continuous actuator on M5 is a `GenericDevice`.** `PWM.Position0`
  (0–255), `Servos.Position0..3` (0–65535), `Laser Trigger."Duration0 (us)"`
  (0–1 048 575), `iBeamSmartCW-1."Power (mW)"` and `Fine A/B (%)`. The gate caught
  that the first version of the refusal net omitted that device type, and demonstrated
  the consequence on the rig: a `complete` map admitting an unbounded Class-3B laser
  power set-point as "categorical". Widening the net closed it while leaving the
  deployed profile's map **byte-identical** — 57 entries before and after.
- **The `pre_init` exemption is what makes that widening safe.** `PWM."Number of PWM"`,
  `TTL."Number of channels"`, `Servos."Number of Servos"`, `Analog Input."Number of
  channels"` and `Laser Trigger."Number of lasers"` are all numeric, writable,
  unenumerated **and pre-init** — which is also why they never appear in MM's Device
  Property Browser. Without the exemption the widening would have refused them as
  continuous actuators with no typed kind available to declare them.
- **`iBeamSmartCW-1."Laser Operation"` is an undeclared light control.** A String
  `On`/`Off` property absent from `illumination.shutters`, so it is neither
  confirm-gated nor swept off at teardown. This is the "illumination gate is inert on
  an undeclared light source" finding above, appearing on M5. It is an M5 profile gap,
  not a Phase 2 defect, and belongs to that separate branch.

### Demo-core findings

- Opt-in additivity was proved by diff, not assertion: the base and typed maps differ
  by **exactly one entry** (41 → 42) with nothing else moved.
- A typed entry narrows **only the raw property path**. With `stage.z_max: 200` and a
  typed `Z.Position 0..150`, `move_stage_z(200)` is still allowed while
  `set_device_property(Z, Position, 175)` is refused. Correct — 200 is the reviewed
  axis bound and the narrowing rule guarantees the typed entry can never be wider —
  but declaring a typed actuator does **not** retroactively tighten the axis, and
  operators will expect otherwise.
- A typed declaration is its own authorization: the 120 µm write succeeded with
  `categorical_properties` empty, hence an empty derived `allowed_properties`.
- `LED.State` on the demo config is Integer with zero allowed values and no limits —
  indistinguishable from a continuous actuator by value shape alone. Only the
  `StateDevice` device-type exclusion keeps Block 3b's auto-classification working. A
  "numeric ⇒ continuous" detector would have broken the stock demo config.

### Excluded from Phase 2, each with its reason

- **Relative/offset moves** — not absolute effects; a guard on the written number
  would not bound the resulting position.
- **Velocity** — needs its own semantics and a stopping policy, not a position bound.
- **Voltage/DAC** — no measured conversion to a physical effect.
- **Camera ROI** — a separate geometry capability, already tracked.
- **MicroFPGA pulse duration** — `Laser Trigger."Duration0 (us)"` is now *refused* as
  an unclassified continuous actuator rather than silently admitted, but it is still
  not *declarable*. Bounding it safely requires expressing dose as level × duration,
  as this document already rules, plus M5 measurements of that product. A future
  typed kind, not this block.

### Limits of the verdict

- **Device-type ordinals 12 (`SignalIODevice`) and 16 (`GalvoDevice`) are unconfirmed
  over the bridge.** Neither the demo config nor M5 has a galvo or a DAC. Both
  resolvers prefer `to_string()` and only fall back to the ordinal table, and on both
  rigs every type resolved by name — so the gate proves the *names* are right and
  never exercises the fallback at all. The table's newly added region is published
  enum, not observed behaviour.
- **A channel preset colliding with a typed pair is off-rig test only.** No demo
  preset touches a typed-declarable property, and constructing one would mean editing
  the MM configuration.
- **The XY axis ambiguity is unreachable on both rigs.** A typed entry carries no
  axis, so an entry on an XY device is validated against both X and Y declared ranges
  and `check_xy` is called with the written value on both axes. That can only
  over-refuse, never under-refuse, but a rig with asymmetric X/Y travel will see legal
  writes refused. Neither rig exposes a writable XY position property, so this is
  reasoned, not measured. Adding an `axis` field is the obvious follow-up.
- **`TTL.State0` is a deliberate false positive.** Integer, not pre-init, no limits,
  unenumerated, and genuinely a digital state. It is refused as continuous because
  microclaw cannot distinguish a digital state from a level on a `GenericDevice`. The
  remedy is `excluded_properties`, which the refusal message names.
- **Tiers.** *Implemented*: the schema, parsing, live validation, the typed guard, the
  narrowing rule, exclusion precedence. *Rig-plumbing-verified*: every one of those on
  at least one live core. *Measured*: the `Power (mW)` 0–75 range, the canonical-percent
  ratchet, the independence of the two guards, and the `GenericDevice`/`pre_init`
  shapes. *Validated*: nothing scientific — this block makes no scientific claim.

## Phase 3 landed (2026-07-27)

Shipped as Block 5 (merge `3438b90`; implementation `ff41de7`; coordinator-review
fixes `a28e5bd`). The [Block 5 rig gate](33-block5-rig-gate-prompts.md) passed on
2026-07-27: B0/B1/B5 on M5, B2/B3 off-rig, and B4/B4b/B5 on a demo core through
the live pyjavaz bridge. This is authorization and accounting evidence, not
validation of the human confirmation gate, cancellation, durable session dose,
or the appropriateness of M5's declared limits.

### What Phase 3 actually implements

- **Typed capability and policy rows.** `acquisition-dose` is a built-in typed
  capability. The map emits nine `acquisition-policy:*` rows: the five hard
  maxima (`max_frames`, `max_duration_s`, `max_bytes`, `max_illuminated_ms`,
  `max_session_illuminated_ms`) and four `confirm_above_*` thresholds for frames,
  estimated duration, estimated bytes, and illuminated time. Guaranteed mode
  requires all nine values to be finite and strictly positive and fails closed
  before any prompt, app construction, or tool dispatch.
- **The Phase-1 acquisition claim was deleted, not extended.** The old
  `path="acquisition", capability="exposure"` row had exactly the defect this
  design forbids: it let per-frame exposure stand in for complete acquisition
  authorization. Phase 3 removes that row and replaces it with the separate dose
  completeness branch above.
- **Tool coverage is derived mechanically.** Eight tool rows are emitted:
  `run_zstack`, `run_timelapse`, `run_multiposition_acquisition`,
  `run_tile_acquisition`, `run_multiposition_with_autofocus`,
  `run_adaptive_zstack`, `run_adaptive_timelapse`, and `run_adaptive_survey`.
  An acquisition-entry-point marker lives on the tool functions; map construction
  enumerates marked functions in `TOOL_REGISTRY`, and `execute_tool` checks the
  corresponding `acquisition-tool:*` path before dispatch. An AST call-graph
  tripwire fails when a registered tool can reach the planner or ledger without
  the marker.
- **Degraded mode moves policy and surface together.** An incomplete policy marks
  both its invalid `acquisition-policy:*` rows and all eight
  `acquisition-tool:*` rows `trusted_degraded`; no tool row claims a complete
  typed capability that the configuration does not back. This lockstep rule and
  one missed tripwire entry point were the two real Block 5 defects caught and
  fixed in coordinator review before hardware. The rig required no code fix.
- **MDA remains excluded.** `run_mda` deliberately receives no
  `acquisition-tool:` row; its existing `mmstudio-mda` path remains excluded.
  Its internal planning/ledger plumbing does not make that opaque authorization
  path complete.

### Rig evidence and limits of the verdict

- M5 produced a complete map with nine policy and eight tool rows, no legacy
  acquisition/exposure row, and no `acquisition-tool:run_mda`. The gate verified
  the plumbing against the live registry and config; it did not validate that
  the nine M5 budgets are appropriate. Finding B1-1 records that they are copied
  verbatim from the shipped example while `reviewed: true` sits beside a
  `# <-- REPLACE` marker on `camera.max_exposure_ms`. That is an open rig-config
  review item, not a Block 5 code defect.
- B5 rig-plumbing verification exercised the map, `execute_tool` dose gate,
  planner, reservation, live-geometry byte calculation, saved-frame accounting,
  and ledger through a real acquisition. The human confirmation gate was not
  validated because the probe self-confirms. Cancellation remains deferred with
  its abort trigger.
- The gate itself had three defects: B2/B3 claimed the wrong layer and rig need,
  B4 compared noisy raw output instead of parsed JSON, and the first B5 probe
  diagnosed an invalid save path too late. All three were gate defects, not code
  defects. That does not make Block 5 defect-free: its two implementation defects
  were the coordinator-review findings described above.

### Known limitations / follow-ups

- **Illumination power units — FIXED by Phase 2 (`47f6702`).** Retained because the
  measurement below is what justified the fix. `illumination.max_power_percent` was
  compared to the raw property value, so a laser reporting `Power (mW)` (iBeam,
  0–75 mW) was not a percentage and an absolute cap in mW was not expressible.
  **Measured consequence, 2026-07-28:** M5 declared `max_power_percent: 100.0` against
  `iBeamSmartCW-1.Power (mW)`, whose driver range is 0–75. A raw 75 is always below
  100, so on that rig the configured ceiling **could never refuse a write** and only
  the step factor bounded a ramp. The cap is not merely imprecise across units; it can
  be silently inoperative.
- **Config reload requires restart:** the map is built once at startup; editing the
  safety config does not hot-reload. Acceptable; worth a usability note in design/32.
- **Session dose is not durable:** the acquisition ledger is in-memory and resets
  on process restart. The `max_session_illuminated_ms` map row now says so in its
  detail; Phase 3 does not persist dose across processes.
- **The map's finite-positive check is defense-in-depth:** every missing,
  non-finite, zero, or negative acquisition value is already rejected while
  parsing a config file, so Block 5's check is unreachable from file input
  (measured in gate finding B2-1). It protects directly constructed
  `ParsedSafetyConfig` values and is covered by
  `test_missing_or_partial_direct_dose_policy_fails_closed`.
- **Authorization refusals receive a misleading generic hint:** `execute_tool`
  attaches `This may be a hardware error (device busy, stage at limit, device not
  found) or a connection problem.` to a `RigAuthorizationError`. This predates
  Block 5 and deserves a separate error-reporting cleanup.
- **Camera ROI** is excluded (no typed ROI capability) — tracked for a later phase.
- **`degraded_trusted_plugins`** remains the sanctioned escape hatch: it suspends the
  completeness guarantee for sessions where the allowlist ceremony is not warranted.

## The illumination gate is inert on an undeclared light source (2026-07-29)

Found during the design/32 Block 15 demo gate on the MM demo config. **Not a
Block 15 defect** — this is design/33's own boundary, pre-existing on `main`, and
it needs a decision here rather than a note in a block's gate document.

An operator asked the agent to turn a laser on. It wrote
`set_device_property(LED.State = 1)`, the write succeeded, and **no confirmation
was requested**. The chain:

- `check_illumination` is wired into `set_device_property` (`tools.py:478`), but
  it gates only what `is_illumination_enable` recognises, and that consults
  `illumination.shutters` alone (`safety.py:805`).
- That list was empty, so `require_confirm_on_enable: true` governed nothing.
  The gate is skipped **silently** — there is no "this write looks like a light
  source" diagnostic.
- `shutter_all` iterates the *same* list (`safety.py:898`), so the teardown sweep
  that design/14 §3 exists to guarantee would not have turned this laser off
  either. The session ended with an acquisition running and the source on.
- The authorization map meanwhile **permitted** the write: `LED` is a demo
  StateDevice, auto-classified by the Phase 1 fast-follow above, while
  `Emission.State` was correctly refused.

The sharp edge is that microclaw already held the answer. `get_emu_configuration`
in that same session returned
`lasers: {"0": {enable: {device: "LED", property: "State", on: "1", off: "0"}}}`.
One subsystem had the laser mapped by semantic role; the other had never heard of
it; **nothing cross-checks them.** Writes are authorised by the map, illumination
is gated by a separate declaration, and the two are allowed to disagree.

This is the same family as the laser-engine widening the Block 3b rig gate
caught: StateDevice auto-classification is the mechanism in both. The difference
is that there the widening was refused, and here it was permitted and then
ungated.

Proposed resolution, for its own branch: at startup, cross-check every EMU laser
enable against `illumination.shutters` and refuse — or at minimum warn loudly —
when a declared enable is absent from the shutter list. That reuses the
"Where the cross-check runs" machinery above rather than adding a surface. The
weaker alternative (warn only) is still worth more than today's silence, because
the current behaviour actively discourages declaration: everything appears to
work without it.

Related, and recorded in design/32 §4: a clean generated hook saves with no
confirmation either, because that gate is conditional on the advisory lint. In
both cases the agent asked in prose and behaved correctly — but the system prompt
tells the model these confirmations are *enforced in code*, and on this rig
neither was.

## Illumination gate: what design/32 Block 7b changed (2026-07-28)

Block 7b let generated hook code propose illumination per frame. It deliberately
added **no second authorization surface** — every proposal still passes through
`SafetyGuard.check_illumination` — but it changed the contract in three ways and
found two facts about this gate worth recording here rather than in a block's gate
document.

**`check_illumination` gained `previous_percent`.** A trusted parent that already
knows its own last successful write passes it, and the ratchet is evaluated against
that instead of a fresh `core.get_property`. This exists so the per-frame path costs
no bridge read; when the parameter is absent the device read is unchanged.

**Hooks can modulate power but can never enable light.** A shutter enable is not
expressible in the action union, so `require_confirm_on_enable` is never reached from
hook code. That is what makes per-frame illumination safe without prompting: pyjavaz
serializes bridge calls behind one lock, so an interactive confirmation on an
acquisition callback thread would deadlock the run. Enabling stays a pre-run human
act; the hook only moves a level on an already-enabled illuminator, inside an envelope
confirmed once beforehand.

**A write reported as failed may have succeeded.** Measured on M5: a `set_property`
raised `Serial timeout occurred. (17)` and the device read back the new value
immediately afterwards. Any caller that caches "what I last wrote" as a safety
baseline must treat a write failure as invalidating that cache, not as leaving it
intact — otherwise the baseline drifts below reality and the ratchet becomes more
permissive than configured. Block 7b re-reads before its next write and fails closed
if the re-read fails. **This applies to any future typed actuator that ratchets
against a remembered value**, which is why it is recorded against the gate rather
than against one block.

### The ratchet bounds rate, not reach — and does nothing from zero

`max_power_step_factor` refuses `new / old > factor`, guarded by `old > 0`. From a
device at 0 % there is no ratio and therefore no refusal: a single write may go
straight to whatever `max_power_percent` (or a caller's envelope ceiling) allows.

This was always true and is now reachable far more often, because Block 7b's
recommended pattern is a hook that winds power down to 0 at end of run — which makes
zero the *normal* starting state for the next run. The operator ruled on 2026-07-28 to
document rather than change it, on the grounds that the ceiling is human-confirmed
before the run and a hook that wants a gradual ramp implements one itself. It is
documented in `IlluminationConstraints`, `safety_config.example.yaml` and
`hook_docs.py`. Revisit if an experiment is bitten by it.

### M5's 405 nm line is declarable, and dose is not what the gate bounds

The `iChrome-MLE-TCP` engine exposes per-channel `Laser N: 3. Level %` (0–100,
linearized in remote mode), with `Laser N: 1. Enable` arming and
`Laser N: 2. Emission` acting as the manual's `:cw` — confirmed on hardware, since
neither alone produces light and both together do. `2. Emission` overrides the
electronic trigger input, which makes it the single property on that engine most
deserving of a confirmation gate; both are now declared as shutters on M5.

Two consequences for this design:

1. **Arming is not darkness.** With `Enable = 1`, `Laser Trigger.Mode0 = Follow` and
   `Sequence0 = 65535`, the MicroFPGA fires a 405 pulse on *every frame of any
   acquisition* — no microclaw write, no confirmation, nothing in the ledger.
   Confirm-gating `Enable` is the only point at which the authorization map sees it.
2. **The illumination gate bounds amplitude, not dose.** htSMLM's activation loop ramps
   FPGA pulse *duration* (`Laser Trigger.Duration0`, 0–1 048 575 µs), not level. Dose
   goes as level × duration, and `Laser Trigger` is an excluded device — microclaw can
   neither write it nor bound it. A future Phase 2 typed actuator for pulse duration
   should express dose as the product rather than adding a second independent cap.
