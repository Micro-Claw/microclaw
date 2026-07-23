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
2. **Extended typed continuous-actuator registry:** add driver-specific
   semantics and unit conversions beyond the built-in adapters, route those
   writes through typed guards, and continue to exclude every unclassified
   continuous write.
3. **Acquisition/dose extension:** after design/32 Finding 2 lands, add its
   frame, duration, byte, illuminated-time, and cumulative-session budgets to
   the authorization policies.
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
- **Acquisition and dose**, once finding 2 lands, require its frame, duration,
  byte, illuminated-time, and cumulative-session budgets; a safe per-frame
  exposure maximum does not make an unbounded acquisition complete. This later
  schema extension does not block landing strict validation first.
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
- **Open item on the M5 reviewed profile, not on this code:**
  `iChrome-MLE-TCP.Label` remains a bare categorical write on a multi-laser engine
  with no confirmation, cap, or ratchet — it predates Block 3b. A session on
  2026-07-23 wrote `State` 1→2→1 (under the pre-fix build) and laser slot 2
  subsequently read `enabled=1` where it had read `0` before, which suggests the
  property may gate emission. Unconfirmed — the EMU slot index is not necessarily
  the `State` value. If it does gate light it belongs under
  `illumination.shutters`, which would both confirm-gate it and carve it out of
  auto-classification automatically.

### Known limitations / follow-ups

- **Illumination power units:** `illumination.max_power_percent` is compared to the
  raw property value, but a laser reporting `Power (mW)` (e.g. iBeam, 0–75 mW) is not
  a percentage — so an absolute cap in mW is not expressible today (the ratchet is
  unit-agnostic and unaffected). A units-aware illumination policy is future work.
- **Config reload requires restart:** the map is built once at startup; editing the
  safety config does not hot-reload. Acceptable; worth a usability note in design/32.
- **Camera ROI** is excluded (no typed ROI capability) — tracked for a later phase.
- **`degraded_trusted_plugins`** remains the sanctioned escape hatch: it suspends the
  completeness guarantee for sessions where the allowlist ceremony is not warranted.
