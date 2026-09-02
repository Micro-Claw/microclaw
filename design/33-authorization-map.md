# The rig authorization map: declared limits, live cross-check, and complete write-path coverage

Date: 2026-07-21

> **Amended 2026-08-13 by design/48 block 48a.** The completeness claim below —
> that *every* reachable actuator is typed or explicitly excluded before any tool
> is exposed — is no longer what a default installation promises. Schema 3 makes
> `property_authorization`, `illumination`, `camera`, `channels` and `plugins`
> optional, and an omitted section means no Microclaw restriction; a config with
> none of them runs in `degraded_trusted_plugins` mode with the completeness
> claim suspended, and hardware-motion plugins permitted with a startup warning.
> What is still mandatory, and what live validation still enforces on every
> start, is **reviewed finite bounds for every reachable stage axis** plus the
> two acquisition confirmation thresholds. Guaranteed mode remains available and
> works exactly as described here for an operator who declares those sections.
>
> One rule survived unchanged and is worth restating: **stage bounds hold
> regardless of the write path.** A raw property write to a device that carries
> declared bounds is refused even when property writes are otherwise
> unrestricted (M5, 2026-08-13).
>
> **Amended by design/49 (M5, 2026-08-14): that refusal is keyed to the exact
> device/property pair, not the device.** A bounded stage device may carry a
> non-motion property that is itself a typed capability — M5's hardware focus
> lock is `PIZStage.External sensor`, on the same device as the focus drive —
> and the device-wide form of the rule made `set_focus_lock` unusable, which in
> turn made autofocus impossible without manual GUI intervention. A pair holding
> a `built_in_typed_capability` entry **on a raw-write path** is admitted; every
> other property on the device is refused exactly as before. The path
> qualification is load-bearing, not decoration: preset entries carry the same
> classification, so a pair test that ignored `entry.path` would let a channel
> preset touching the focus device's `Position` reopen the raw route.

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

### Amendment, 2026-08-14 (block 51a): the map is not the authority when nothing is declared

Everything above assumes a document that declares things. Under schema 3 a
minimal document declares almost nothing, and `_authorize_channel_effect` went on
judging every preset effect against a map that is empty by design — refusing
`HamamatsuHam_DCAM.DEFECT CORRECT MODE` on M5 while the identical raw write was
permitted, because `authorize_property_write` returns early on
`property_writes_unrestricted` and the effect path had no such early return.

The preset path now honours the same two omission flags the raw path does. **This
narrows nothing above**: with any `property_authorization` or `illumination`
section present, every rule in this section applies exactly as written, and both
directions are gated by
`test_declared_preset_restrictions_and_shutter_confirmation_are_unchanged`. What
changed is that "the map says no" is no longer an answer on a rig that never
authored a map. See `design/51-omitted-illumination-still-restricts.md`.

### Amendment, 2026-08-14 (design/53, not yet implemented): verification is plan-level

The freshly-expanded runtime authorization below is unchanged. Its *read-back
verification* is not: verifying each write the moment it lands checks a preset's
values against a half-applied preset, which fails whenever one value is only
valid after another. M5's `System/Normal Mode` writes `Exposure` at index 1 and
`ScanMode` at index 5, and the exposure is representable only in the later mode.
Verification belongs after the last write. Filed as
`design/53-a-preset-is-verified-as-a-set.md`; this note stands until 53a lands.

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

### An approved hook envelope replaces the safety-config allow/deny decision, and not this map

Added by block 52b's design gate, 2026-08-17, after it shipped on M5.

A saved hook may now be granted a `property_envelope` naming one exact
`(device, property)` with a value set or numeric interval, a write budget and a
restoration policy (design/52). When the parent dispatches that write it takes
**two** authorization decisions, and only one of them yields:

- **`check_device_property`'s categorical allow/deny policy yields.** The exact,
  operator-approved, audited envelope *is* the safety-config authorization for
  that pair, so the write takes the branch typed and illumination pairs already
  take: it bypasses the `allowed_properties` allowlist while an explicit
  `forbidden_properties` entry still wins. Without this, a historical categorical
  exclusion would veto the exact action the operator just approved, which is the
  defect design/52 was written about wearing a different hat. The
  bounds/type validator stays mandatory — envelope approval never substitutes for
  a range, a unit or a device's allowed values.
- **`authorize_property_write` does not yield.** It runs unchanged, and an
  excluded or unclassified pair refuses regardless of approval, for the reason
  design/49 gave: the map's exclusions encode hardware semantics nobody has
  established yet, and an approval dialog is not where those get settled. The
  envelope is not a route around this map.

The two are different objects and conflating them was the first draft's error.
**Measured on M5, 2026-08-17**: with `property_writes_unrestricted: true`, an
approved envelope aimed at `Thorlabs ELL17/ELL20`.`Position (um)` — a real,
writable property, inside both its reviewed `named_stages` bound and the approved
interval — was refused by this map with the message naming `move_named_stage`,
and the axis did not move. That is a pass, not a gap.

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

## Populating the profile: first-launch setup (landed and rig-gated 2026-08-02)

`microclaw first-launch-setup --out <safety_config.yaml>` implements the
restricted interactive path. **Corrected by Block 5 (2026-08-04): `microclaw
init` no longer copies the example YAML by default** — it prints the real
first-run path, offers to run `first-launch-setup`, and copies the example only
under an explicit `--from-example`. See "Block 5 landed" below. The setup command
resolves the bootstrap problem as follows:

1. Connect only for device enumeration, without starting the agent or exposing
   any mutation tools.
2. Disconnect, then walk the operator through the inventory's separate facts,
   heuristic-candidate, and human-decision regions. Require explicit decisions
   and human-entered limits, and write an unreviewed profile. **Observed current
   values are never defaults or profile values. Allowed values and driver
   technical ranges are — see "Phase 5 landed" below, which scopes this rule to
   what shipped after five gate rounds.**
3. Require the operator to review the complete file and mark it reviewed.
4. Restart normally, validate the file before controller construction, then
   perform the authoritative live-device cross-check before enabling tools.

Even enumeration is hardware contact, and loading a Micro-Manager configuration
may initialize devices. Setup mode therefore cannot promise that the safety file
precedes the *first hardware contact*; it promises that no agent-directed or
tool-directed hardware action is possible before review. The implemented
least-active path constructs only pycro-manager's remote `Core` shadow (not
`MicroscopeController`, which also constructs `Studio`), verifies the Core
connection, runs Block 9b's query-only `enumerate_rig`, writes the unchanged
inventory format, releases the Core shadow, and closes the ZMQ bridge before the
first interview question. It never loads a Micro-Manager configuration. Before
constructing that Core shadow it displays the complete contact/ordering warning
and requires the operator to type an exact hardware-contact acknowledgement;
any other response exits without connecting. Consuming an existing inventory
shows the same ordering and review/restart explanation but requires no contact
acknowledgement because it opens no connection.

The initialization boundary it cannot avoid is outside Python's control: the
operator has already loaded the Micro-Manager configuration into the running JVM,
which may have initialized every configured adapter/device, and constructing the
remote Core shadow plus the version/enumeration queries contacts that initialized
Core and drivers. On an undeclared emission path this may initialize and emit
before a config exists; the normal-startup completeness cross-check is downstream
and does not cover the window. No narrower per-device initialization list can be
claimed mechanically from MMCore queries; the pending demo/M5 gates must record
what their live Micro-Manager logs and devices actually initialize.

The interview requires an explicit classification for every surfaced emission,
enable, and power candidate and explicitly says discovery is not exhaustive.
Unknown writability and classification-blocking enumeration failures refuse
generation; unsupported ROI/pulse kinds, unrecognised device types, and
ambiguous XY position properties remain excluded or unresolved rather than
inferred. Duplicate percent/native representations
are one required choice. Continuous-focus enables are hard-excluded, the core
focus/autofocus/offset relationship remains one review question, and PFS-offset
workflows remain unsupported. Preset effects and typed-property collisions are
shown before each preset decision. Generated guaranteed profiles always contain
`allowed_categorical`, including when empty.

Enumeration failures are classified by coordinate. Missing loaded devices,
device type/property names, writability/type inputs, preset lists, or any preset
effect refuses generation. Failures of observational values, driver technical
range edges, adapter metadata, or core assignments do not answer a safety
decision and therefore become named review notes in the generated header rather
than reinstating an all-or-nothing sweep. Unknown writability remains terminal.

The generated file always carries `reviewed: false` and is passed to the shared
offline `validate_safety_config` implementation through an adjacent temporary
file. Only a schema-valid draft with the expected unreviewed diagnostic is moved
atomically to the requested path; rejection removes the temporary and leaves no
new output file. Setup never hot-loads the file and directs the operator through
disconnect → manual review → `reviewed: true` → normal restart. Its setup text
also states that `max_session_illuminated_ms` is a per-process, non-durable
ledger cap. The operator-driven M5 transcript and live demo-core start remain the
Block 4 rig gate; automated input fixtures do not stand in for that human proof.

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

**Inventory schema** — deterministic and governed by
`microclaw.rig_inventory.validate_inventory_schema` and
`SUPPORTED_INVENTORY_SCHEMAS`; consumers must use that producer-owned contract
and refuse unsupported versions. It has three structurally separate regions.
Phase 5 must preserve that separation rather than flattening it:

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

- **Schema.** `property_authorization: {mode, allowed_categorical, denied}`.
  There is **no separate actuator manifest** — the declared stage/focus **ranges**
  (`ParsedSafetyConfig.ranges`, from design/32) are the stage-position completeness
  target. `denied` is required whenever `property_authorization` is present (both
  modes); `allowed_categorical` is required in guaranteed mode (may be empty).
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
  position property — `allowed_categorical`, `denied`, or the
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
  whose allowlist is derived from `allowed_categorical` at parse time.
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
  `property_authorization` would still auto-classify. That is the intended design, but on a
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
  whose states nobody can name is a poor thing to leave in `allowed_categorical`.
- **The vacuum-filling rule does not depend on any of this.** It stands on the
  principle that auto-classification must not override an explicit operator
  narrowing, whatever the device turns out to do.

## Phase 2 landed (2026-07-30)

Shipped as Block 14 Phase 2 (merge `47f6702`; implementation `9bf7599`, coordinator
review rounds `e0645f1` and `47295f2`). Gated on the Micro-Manager demo core and on
**M5**; the gate record and all evidence pointers are in
[`33-block14-phase2-gate-prompts.md`](33-block14-phase2-gate-prompts.md).

### What Phase 2 actually implements

- **Schema.** `property_authorization.allowed_numeric` — an optional, strict list of exact
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
- **Exclusion beats declaration.** A pair in both `allowed_numeric` and either
  `forbidden_properties` or `denied` is a startup conflict, and the
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
  `allowed_categorical` empty, hence an empty derived `allowed_properties`.
- `LED.State` on the demo config is Integer with zero allowed values and no limits —
  indistinguishable from a continuous actuator by value shape alone. Only the
  `StateDevice` device-type exclusion keeps Block 3b's auto-classification working. A
  "numeric ⇒ continuous" detector would have broken the stock demo config.

### Excluded from Phase 2, each with its reason

- **Relative/offset moves** — not absolute effects; a guard on the written number
  would not bound the resulting position.
- **Velocity** — needs its own semantics and a stopping policy, not a position bound.
- **Voltage/DAC** — no measured conversion to a physical effect.
- **Camera ROI** — **shipped as a typed geometry capability, block 47, merged
  2026-08-12.** No longer deferred. `set_roi`/`clear_roi` are
  `built_in_typed_capability` with `capability="camera-roi"`, emitted beside
  `dedicated-exposure`. The guard validates only what is invalid independently
  of the adapter — integer values, nonnegative origin, positive size — because
  the camera's full-frame extent is not portably knowable
  (`getImageWidth`/`getImageHeight` describe the current image buffer, not the
  sensor). Sensor-specific geometry is the adapter's to refuse, and the demo gate
  measured that it does accept a reposition outside the current crop, so
  MMCore's `setROI` takes full-frame coordinates.
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
  remedy is `denied`, which the refusal message names.
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

- **Typed capability and policy rows (as Phase 3 landed).** `acquisition-dose`
  is a built-in typed capability. Phase 3 initially emitted nine
  `acquisition-policy:*` rows: five hard maxima and four `confirm_above_*`
  thresholds. Block 4b subsequently deprecated two of those keys, so the map now
  emits **seven** such rows and guaranteed mode requires those seven — the
  deprecated keys get no row at all, rather than an optional one. Anyone
  re-running the map against an older record should expect seven, not nine. See
  "Block 4b landed" below.
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

- At the Phase 3 gate, M5 produced a complete map with nine policy and eight
  tool rows, no legacy
  acquisition/exposure row, and no `acquisition-tool:run_mda`. The gate verified
  the plumbing against the live registry and config; it did not validate that
  the nine M5 budgets are appropriate. Finding B1-1 records that they are copied
  verbatim from the shipped example while `reviewed: true` sits beside a
  `# <-- REPLACE` marker on `camera.max_exposure_ms`. That is an open rig-config
  review item, not a Block 5 code defect.

  **Superseded as to the values, 2026-08-04 — the item is still open and is now
  sharper.** M5's budgets are no longer example copies. Measured on the deployed
  file at checklist-v2 Block 5's M5 gate, and against Block 4d's captured copy
  from the day before:

  | key | example | M5 2026-08-03 | M5 2026-08-04 |
  |---|---|---|---|
  | `max_frames` | 10000 | 100000 | 100000000 |
  | `max_duration_s` | 3600 | 1e21 | 1e13 |
  | `max_illuminated_ms` | 600000 | 1000001720 | 1000001720000 |
  | `max_bytes` | 5e10 | 1.0616832e12 | 1.0616832e15 |
  | `camera.max_exposure_ms` | 5000 | 10000.0172 | 10000.0172 |

  Three hard caps rose a further 1000× in one day. As deployed, `max_duration_s`
  is ~317,000 years, `max_illuminated_ms` ~31.7 years of illumination, and
  `max_bytes` ~1.06 PB. These read as limits raised until they stopped refusing
  something, not as measured hardware bounds — a different and worse failure than
  the example-copy this finding was written for. What still binds in practice is
  the `confirm_above_*` tier, which is unchanged and sane (1000 frames, 3600 s,
  60000 ms) and forces a human confirmation; the automatic ceiling does not bind
  at all. Guaranteed mode requires these nine to be finite and positive, and they
  are, so nothing refuses.
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
  **CLOSED** — fixed incidentally in `bb6290f`, verified and regression-tested
  under checklist v2 Block 3. See "Error taxonomy and offline validation" below.
- **Camera ROI** was excluded here for want of a typed capability, and **that is
  no longer true**: block 47 shipped one, merged 2026-08-12. The exclusion had
  been hard-coded rather than config-driven, so between `2599869` (2026-07-22)
  and that merge **ROI was refused on every map-carrying rig**, and the refusal
  told operators to change a safety config that could not have helped. Both are
  fixed. This was a gap, not a capability decision — see the entry above.
- **`degraded_trusted_plugins`** remains the sanctioned escape hatch: it suspends the
  completeness guarantee for sessions where the allowlist ceremony is not warranted.

## Phase 4 landed (2026-07-30)

Shipped as Block 14 Phase 4 (merge `5458483`; executor implementation `3fc7dcd`;
gated subject `9799164`; gate-record closeout `bb6290f`). The measurement spike and
executor gate record are in
[`33-block14-phase4-gate-prompts.md`](33-block14-phase4-gate-prompts.md). The spike
ran on the Micro-Manager demo core; G7–G9, the G11 shutter-retarget limb, and the
interactive prompt limb used that same demo core, while injected failure,
rollback-failure, and cancellation used deterministic fakes.

### What Phase 4 actually implements

- **Capture, authorize, replay.** `set_channel` freshly captures one immutable
  `Channel` expansion, authorizes every effect before the first mutation, then
  replays those exact values instead of delegating to `set_config`. The startup and
  applied expansion SHA-256 values are retained and `expansion_drift` reports a
  changed live definition; the startup hash is a diagnostic, never an authorization
  token.
- **Per-effect routing.** Reviewed categorical effects use the categorical guard;
  declared typed-continuous effects use their typed guard; illumination enable and
  power use the illumination guard; camera exposure uses the built-in exposure
  guard (and the existing built-in axis routes remain guarded). `Core.Shutter` is
  the sole admitted `Core.*` effect: its target must be a declared illumination
  shutter and each captured write requires illumination-class human confirmation.
  Every other `Core.*` effect is excluded.
- **Ordered apply, then one verification pass.** Effects retain expansion order.
  Each real-device write is followed by `wait_for_device`; `Core` is a
  pseudo-device, so it has no device wait. **Read-back verification is a
  plan-level check, not a per-write one** (block 53a, merged 2026-08-15): every
  write lands, and only then is the whole effect list read back, in the same
  order. Read-back is exact for non-floats and numeric for Float properties,
  accepting equivalent driver formatting rather than requiring equal strings.
  See [Why verification is plan-level](#why-read-back-verification-is-plan-level).
- **Fail-fast rollback with an honest error boundary.** Forward execution stops at
  the first failed set or wait — a device refusing a write is knowable at that
  write — and rolls back every attempted write, including the ambiguous write that
  raised, in reverse order, restoring all of them before verifying any of them
  (53a: the reverse loop carried the same defect mirrored). `ChannelPlanPartialApplicationError` means execution
  was partial but all attempted rollback steps verified;
  `ChannelPlanSafeStateError` says `SAFE STATE NOT VERIFIED` and names rollback
  failures instead of claiming recovery.
- **Cancellation is a write-boundary operation only.** It is polled before each
  forward write; cancellation then enters the same rollback path. It cannot
  interrupt an in-flight bridge set, wait, or read-back.
- **Prompt cost.** The deliberate per-captured-`Core.Shutter` prompt frequency and
  its one-line reversal are already recorded in
  [the TOCTOU section above](#preset-mutability-is-a-time-of-checktime-of-use-gap);
  Phase 4 does not implement that reversal.

### Why read-back verification is plan-level

Block 53a, merged 2026-08-15 after an M5 gate. **A preset's values are
simultaneously true, not sequentially true**, so a read-back taken mid-plan is
taken against a device state the preset does not describe yet.

M5's `System/Normal Mode` sets `HamamatsuHam_DCAM.Exposure = 100.0030` at index 1
and `ScanMode = 3` at index 5. On a Hamamatsu sCMOS the exposure grid is a
function of line time, which the scan mode changes, so that exposure is
unrepresentable until index 5 lands — in ScanMode 2 the device snaps it to
`100.0140`. Verifying at index 1 therefore failed a write that was correct, and
the plan rolled back a preset in routine daily use. Measured 2026-08-14; the
call could not be made through Microclaw at all until this changed.

Three things this is **not**, each measured or reasoned rather than assumed:

- **Not float tolerance.** With the camera already in ScanMode 3, writing
  `100.0030` reads back exactly. A tolerance wide enough to pass the mid-plan
  read would be wide enough to miss a write the device ignored, which is the
  whole reason the check exists (Phase 4: MM's `set_config` continues past a
  setting a device ignored).
- **Not a reordering problem to solve here.** Microclaw cannot know which
  properties are modes without rig-specific knowledge, which does not belong in
  `microclaw/`. MM stores the preset author's order and we apply it faithfully.
- **Not 50a's or 51a's.** `_verify_property` dates to the original executor
  (`3fc7dcd`). M5 simply had no reachable preset route until 50a added
  `set_config_preset`, and the demo's presets carry no interdependent pair.

**What the M5 gate then measured, 2026-08-15.** The premise the fix rests on was
open until the rig answered it: representability in the right mode does not by
itself prove that a *mode change* re-derives a value the driver has already
snapped. It does. Exposure was written as `100.0030` while the camera was still
in ScanMode 2, and read `100.0030` once ScanMode 3 landed four writes later. The
preset applied with 11 writes and verified.

**The trap is symmetric, and both M5 presets carry it.** `System/Camera` also
puts its exposure at index 1 and its mode at index 5, so the old per-write check
broke the switch in *both* directions; it looked one-sided only because the
failing session started in `Camera`. Four alternating switches now pass.

**The two error shapes are now distinct, and must stay distinct.** A write that
raised keeps `Channel plan 'X' stopped after N/M writes ... applied=[...]` — the
2026-08-06 serial-timeout shape recorded below, where `applied=[]` means nothing
reached the device. A verification mismatch reads `applied all N writes, then
read-back verification failed for K of them ... mismatched=[...]`, because in
that path **every write did reach the device**. Reusing one sentence for both
states was caught in review, not on the rig: it would have told an operator that
a plan which wrote eleven properties had written none.

**Cost, accepted.** All writes land before a mismatch is discovered. A partially
applied preset is not a safer state than a fully applied one, only a less
coherent one, and every effect is authorized and confirmed before the first
write either way.

**The exported script mirrors this.** `_emit_recorded_channel_effects` writes
every effect before verifying any of them, for the same reason; otherwise a
standalone script of the M5 session would reproduce on the rig exactly the defect
the executor no longer has. Verified from the emitted file on M5: 11 writes, 11
waits, 11 verifications, last write before first verification.

**Carried nit.** `ChannelPlanError`'s docstring still says "no write was verified
as applied", which meant the same as "no write reached the device" only while
verification was per write. That docstring is inlined verbatim into every
exported script.

### Measured basis

- On the demo core, ordered replay and `set_config` produced empty complete-state
  diffs for the three non-vacuous presets DAPI, FITC, and Rhodamine, and matched
  `getCurrentConfig("Channel")` bookkeeping; the fourth, Cy5, was already current
  and is explicitly not evidence.
- In the demo apply-time failure spike, `set_config` continued after setting 2
  failed, raised only afterward, and left settings 1 and 3 applied:
  `Camera.AllowMultiROI` `0`→`1` and `Emission.ClosedPosition` `0`→`1`. The ordered
  property loop stopped at setting 2 and left only setting 1 applied before restore.
- The demo camera driver reformatted a requested exposure string `"10"` as
  `"10.0000"` on read-back. The executor's type-aware comparison handles that
  shape, but equivalent reformatting by production drivers is unmeasured.
- Twenty serialized demo-core `get_property` calls measured a **0.13 ms minimum**
  and **0.14 ms median** round trip. That 0.13 ms floor made a set/wait/read sequence
  per write affordable on this configuration; it does not predict real-adapter
  latency.
- Editing a scratch preset made a subsequent `get_config_data` return the changed
  definition (`reread_changed: true`). G9 then changed `Dichroic.Label` to
  `89402bs`, observed unequal startup/applied hashes and `expansion_drift: true`,
  and applied the freshly authorized value. This is live re-read evidence, not a
  claim that a startup snapshot stays authoritative.

### Excluded from Phase 4, each with its reason

- **Every configuration group except `Channel`** — Phase 4 measured other groups
  only for effect inventory; it did not authorize or implement their application.
- **`Core.ChannelGroup` resolution** — the group stays behind the named `Channel`
  constant because `Core.ChannelGroup` is writable and preset-set, and on M5 its
  observed choices lead into `System` presets that can arm lasers. It is not a safe
  source of executor authority.
- **Every non-Shutter `Core.*` effect** — no other pseudo-device effect has a
  measured, typed authorization route; inventory is not permission.
- **LED Shutter as a declared illumination gate** — its allowed labels were
  inventoried, but no measured on/off pair establishes which values emit. It was
  used only as the initially active target in the demo retarget gate.
- **The map-less legacy delegation path** — compatibility sessions without an
  authorization map still call `set_config`, so they retain the preset-definition
  TOCTOU gap and are outside the Phase 4 completeness claim.

### Honesty audit

- **Implemented:** fresh immutable capture; pre-mutation authorization; the routing
  table above; ordered set/wait/read; type-aware verification; drift reporting;
  reverse rollback and the two error classes; write-boundary cancellation.
- **Rig-plumbing-verified:** capture/replay, authorization/refusal, order,
  set→wait→read calls, read-back, drift reporting, shutter confirmation/retarget,
  restoration, and the browser prompt path on demo devices. The demo devices never
  became busy, so real-hardware per-write wait semantics remain unmeasured.
- **Scientifically provisional:** the demo spike measured replay equivalence,
  `set_config` partial failure, reversibility, live definition re-read, the 0.13 ms
  bridge floor, and `"10"`→`"10.0000"` demo-driver formatting. Failure after every
  write, failed rollback, Float-format acceptance in the executor, and cancellation
  were exercised only by deterministic fakes. Numeric reformatting on real drivers
  is unmeasured. M5 has no `Channel` group, so none of the executor is M5-verified.
- **Validated:** nothing scientific. Phase 4 claims no scientific result and does
  not validate a safe illumination state, exposure, dose, timing, or device policy.

### The plan source, and why it is not always a preset (block 41c, 2026-08-06)

**Problem.** Phase 4 executes presets, and M5 has no `Channel` group, so it had
nothing to drive. The 2026-08-05 two-channel run was three raw
`set_device_property` writes per switch against EMU's **reversed** slot order —
slot 3 is `640` and its enable is `Laser 1: 1. Enable`. The writes were right;
the narration of them in the same message was not. See design/41 F6.

**Decision.** One source seam, `ChannelSource` in `authorization.py`. It answers
*what channels exist* and *what does this one expand to*; the startup
classification loop and `execute_channel_plan` both ask it, and the executor
never learns which source it is replaying. There is no second executor, no
second authorization path, and no `if emu:` in the replay loop.

```python
ChannelSource(kind, names, effects, unavailable, problems).expand(core, channel)
```

- **Selection is by preset, not by group.** A `Channel` group holding at least
  one preset always wins, and the EMU config is not read at all. Only a rig with
  nothing to drive looks further. Reading `Core.ChannelGroup` stays excluded for
  the reason above; this changes the *source*, never the group name.
- **An EMU-sourced channel is laser-only, and says so.** EMU names filter slots
  (`Filters - Filter names`) and laser slots (`Laser 3 - Name`), but **nothing in
  its configuration joins a laser to a filter** — checked against the captured
  M5 `config.uicfg`, whose `Filters` panel carries names and colours only. That
  join is not invented, and it is not added as a new declaration block either: a
  rig that needs a channel to move more than its lasers already has a way to say
  so — a Micro-Manager `Channel` preset, which this code then prefers. The scope
  is stated in `get_available_channels`, in `set_channel`'s result, and in the
  refusal text.
- **Every other named slot goes to its declared `off_value` first, then the
  target to its `on_value`.** Unconditional, like a preset. Off-before-on is the
  order that never has two lines armed at once, and leaves less light on the
  sample if a write fails mid-plan. The hand-written M5 sequence armed first; the
  end state is identical.

  **Why unconditional, re-examined after the M5 serial timeouts (2026-08-06).**
  A four-laser rig does three redundant writes before the one that matters, and
  on M5 the first of them — `Laser 4: 1. Enable` on a laser already off — is
  where both timeouts landed. The trade is worth restating because the reason
  first given here was wrong: it was *not* hash stability. `channel_expansion_hashes`
  and `expansion_drift` are computed from the **plan**, before execution, so
  skipping a write at apply time would not disturb them at all.

  The load-bearing reason is **emittability**. `originals` is already read for
  every effect, so skipping writes already at their target would save the
  set/wait/verify round trips — three per redundant slot, nine per switch on M5,
  on a link that demonstrably flakes. But the executed effect list is what the
  script exporter emits. Skip conditionally and the emitted script becomes a
  function of that day's starting state: a session that skipped "slot 0 off"
  because it was already off exports a script that omits it, and run later with
  slot 0 on, that script images with two lasers. That is a dose defect in the
  standalone artifact — exactly what design/41 F1 and this block exist to remove.
  Emitting the full plan while executing a subset is worse still: it emits writes
  that did not run, which is the reconstruction the whole exporter forbids.

  **So it stays unconditional**, and the redundant writes are the price of a
  channel that is definitive from any starting state and of a script that
  reproduces it. If the round trips ever have to come down, the honest levers are
  (a) shrink the *plan* rather than the execution — which needs an operator
  declaration of which slots form the channel set, rejected here for the same
  reason the filter association was — or (b) retry with backoff at the device
  layer, which addresses a flaky serial link directly instead of by writing to it
  less. (b) is the better lever and belongs with the device layer, not here.
- **EMU supplies identity, the safety config supplies values.** A slot whose
  enable pair is not declared under `illumination.shutters` is not offered as a
  channel. The executor's illumination routing is therefore reached exactly as
  for a preset effect, and `check_illumination` fires **once per switch, on the
  enable** — the off-writes equal the declared `off_value` and are silent,
  precisely as the hand-written `set_device_property` writes were. Checked, and
  unchanged.
- **An unresolvable name is a refusal, never a fabrication.** No slot becomes
  "Laser 3" or "slot 2". Unnamed, name-conflicted (design/39 rule A),
  duplicate-named, undeclared and unreadable cases each carry their reason into
  `excluded_presets` and `get_available_channels`, so a rig never merely looks
  channel-less.
- **`channels.allowed` and the map are unchanged.** An EMU-sourced channel passes
  `guard.check_channel`, `authorize_channel` against `authorized_presets`, and
  per-effect `_authorize_channel_effect`, identically. The startup diagnostic
  that told an operator "This rig has no Micro-Manager Channel group; remove
  these non-channel claims" was wrong on such a rig and now names the EMU source
  and the channels it offers.
- **Emittable, from the record.** `execute_channel_plan` returns the executed
  `effects`, and `set_channel`'s emitter renders exactly those as
  set/wait/verify. It never emits `core.set_config("Channel", …)` for a plan —
  a rig with no group is exactly the rig the plan came from. The map-less
  delegation path emits the `set_config` that genuinely ran, keyed on its own
  recorded result.
- **The emitted read-back is `_verify_property` itself**, inlined with
  `inspect.getsource` alongside `_property_type_name` and `ChannelPlanError`,
  the way the analysis is inlined. The first version paraphrased it as
  `assert str(core.get_property(...)) == value` and was wrong: `_verify_property`
  compares a **Float** property numerically because Micro-Manager reformats one
  (`"10"` reads back `"10.0000"`, measured above), so any `Channel` preset
  carrying a camera exposure would have exported a script that died partway
  through, standalone on the rig, on a write that had succeeded. Two things
  follow from inlining rather than recording the type alongside each effect: the
  emitted rule can never drift from the executor's — including that it
  special-cases `Float` and **not** `Integer` — and the script establishes the
  type the way the executor does, by asking the core, so `_property_type_name`'s
  pyjavaz shape handling (`to_string` / `swig_value`) travels with it instead of
  being re-guessed in the emitted source. Caught in coordinator review,
  2026-08-06; every M5 enable fixture is categorical and none could have caught
  it.

  **The general rule, and this is its second instance:** what the exporter
  inlines, it inlines *from source*. A paraphrase is a second implementation
  that drifts, and both times it drifted the failure landed on a rig, in a
  standalone script, with nothing around to explain it. Byte-identity does not
  enforce it — that still passes when an inlined function starts calling a
  helper which was never inlined. `test_emitted_inline_defines_every_name_it_uses`
  is the guard, now parametrized over **every** record that triggers an inline
  rather than the analysis alone; it was blind to this block's inline until it
  was widened, which is exactly where blocks 13 and 41b each sat before merging.
  Add a param there whenever the exporter learns to inline something new.

**Boundary, deliberately not crossed.** The acquisition tools' `channel=`
argument is handed to pycro-manager as `channel_group="Channel"`, so it stays
config-group-only. **41c introduced both the reachability and the refusal**: a
channel name could not previously sit in `channels.allowed` without a preset
behind it, so this block is what makes that combination reachable, and reachable
and silently wrong — imaging every plane on whichever line was last on — is worse
than the gap it closes. It is therefore **refused**, naming `set_channel` as the
way through, rather than shipped and filed. Making the acquisition *axis* itself
EMU-sourced would mean switching channels from inside an event hook, and is not
part of this block.

**What the 2026-08-06 gates established.** On **M5** the channel work passed on
hardware: four named channels where the rig used to report none, the reversed
slot order right, two switches through the plan with zero raw writes to a laser
enable, and exactly one illumination confirmation per switch, on the enable. On
the **demo** rig, preset-sourced plans behaved as before (`channel_source:
config-group`, no EMU mentioned, `expansion_drift: false`), and the emitted
script **ran to completion against a live core with microclaw closed** — the
first standalone hardware run of the inlined `_verify_property`, with
`_property_type_name` reaching `get_property_type` over the pyjavaz bridge and
`Core.Shutter` correctly getting no `wait_for_device`. Its **Float branch remains
untested**: the stock demo `Channel` presets expand to `Dichroic.Label`,
`Emission.Label`, `Excitation.Label` and `Core.Shutter`, all String, and M5's
enables are categorical, so no rig in this gate reaches the numeric comparison
without a preset edit. That half is SKIPPED, not passed.

Two defects came back, both in the export half:

- **A failed call was exported as a successful step.** The session made five
  `run_multiposition_acquisition` calls; two completed, two failed on trigger
  arming, one was refused by the channel-axis guard above. The export emitted all
  five, so the standalone script would have imaged each position **five times
  instead of twice** — 2.5× the session's dose on a bleaching sample — and then
  driven a channel axis the rig cannot drive.

  The **demo gate measured the same defect physically**, and it is worse than
  duplicate dose. There, a multiposition call passed `channel` at the top level,
  which the tool does not accept: it raised `TypeError` and did nothing. The
  exporter emitted it anyway — and because the emitter reads the channel from
  `protocol_params`, the rejected argument was invisible to it and the step
  rendered with **no channel at all**, acquiring in whatever state was current
  (FITC, from the preceding `set_channel`) — a channel the session never asked to
  image. Three datasets per position where the session made one, and the file
  sizes separate them exactly: the two real ones at 532654/532655 bytes, the
  phantom alone at 532637. **Arguments the tool layer rejected never took effect,
  so a step built from them is invention rather than reproduction** — design/41
  F1's reconstruct-from-memory failure arriving through the exporter itself.

  `_recorded_tool_calls` attaches a result to every recorded `tool_use` and never
  asks whether it succeeded; this is 41b code, invisible there only because those
  gate sessions contained no failures. `_recorded_outcome` now reads how much of
  a call completed, structurally: a top-level `error` (how `execute_tool` reports
  both a refusal and a raised exception), or per-item `error` entries inside a
  top-level list (how a part-completed acquisition reports itself). The `status`
  prose — "0/3 positions completed." — is a symptom and is deliberately not
  parsed, and only the exact key `error` counts, because `error_um` is a
  measurement a *successful* move reports.

  **Three outcomes, three treatments** — the first attempt gave the first two the
  same one and demo round 2 caught it:

  | outcome | treatment |
  | --- | --- |
  | **cannot emit** — offline mosaic, adaptive run, no emitter | `# NOT EMITTED` + `raise` |
  | **partial completion** — some items done, some not | `# NOT EMITTED` + `raise` |
  | **nothing completed** — rejected call, or every item failed | `# SKIPPED` comment, script continues |

  The split is between *did something happen* and *can it be reproduced*. A call
  that completed nothing did nothing, so the script doing nothing is the **exact**
  reproduction, not a reconstruction — and halting there strands every later step
  that really ran. Round 2 measured that: the rejected call's `raise` sat at line
  97 and the acquisition that had actually run, at lines 99–106, was unreachable;
  the script contributed zero acquisitions. Failed calls are ordinary — the M5
  gate session had three — so refusing on them makes the export useless on exactly
  the sessions people have, a worse failure than the duplicate dose it replaced.
  Partial completion keeps the refusal, because something *did* happen that cannot
  be faithfully reproduced: replaying the whole step re-images what completed,
  replaying only what worked silently changes the routine, and the record cannot
  say which the operator wanted. A partial mid-session does still strand what
  follows; that is the accepted cost of not reconstructing it.

  **The guarantee is narrower than it looks.** "Nothing completed" is not a claim
  that no hardware moved — a tool can fail after moving a stage. What is
  guaranteed is that *nothing the session recorded as completed is skipped, and
  nothing that failed is retried*, which is the safer of the two errors and the
  one that matches what the operator actually got.
- **The model was offered channels it could not use.** A session with
  `channels.allowed: []` was told the rig had four channels and then refused when
  it set one. The guard was right; the report was not. `get_available_channels`
  now also returns `authorized` whenever the allowlist restricts the offered set.
  The allowlist is untouched and remains the only authority — the report asks
  `guard.check_channel` rather than re-reading config, so the two cannot
  disagree — and `channels` still lists what the rig has, so no rig reality is
  hidden. This is a reporting fix, not an allowlist redesign; the split between
  "what exists" and "what is permitted" is pre-existing and 41c only made it
  visible, because on M5 the list used to be empty anyway.

### The rollback reported an unverified safe state for a plan that changed nothing

M5 gate round 2, 2026-08-06, hit in **2 of 4** channel switches. Phase 4 code,
but unreachable on M5 before block 41c — no `Channel` group meant no plan ever
executed there — so 41c is what ships it to a rig whose iChrome serial link
flakes, and it is fixed here.

```
ChannelPlanSafeStateError: Channel plan '640' stopped after 0/4 writes:
Cannot set property "Laser 4: 1. Enable" to "0" [ ... Serial timeout occurred. (17) ];
applied=[]; attempted=['iChrome-MLE-TCP.Laser 4: 1. Enable']; rolled_back=[];
SAFE STATE NOT VERIFIED; rollback_failures=[... Serial timeout occurred. (17)]
```

The first write raised, so nothing reached the device. The rollback loop iterated
**`attempted`**, which includes the write that raised, and tried to re-write that
property to its original — the same command that had just timed out. It timed out
again, and any rollback failure escalated the result to the loudest error the
executor has, about a plan in which nothing had changed. The evidence contradicting
the headline was in the same string: `applied=[]`.

**The fix is in the bookkeeping, not in the attempt.** A write that raised may
still have partly taken effect, so trying to restore it stays. What changed is the
conclusion drawn when that restore fails. The executor now counts writes the device
**accepted** — `set_property` returned, whatever the read-back then said — and a
rollback failure is a safe-state failure only for a write that reached the device.
That gives a strict severity ladder:

| condition | class |
| --- | --- |
| no `set_property` returned | `ChannelPlanError` (base), reporting the exception-path read-back — `at_original`, `landed`, `changed_unexpectedly` or `unknown`. **Superseded by design/72 block 72a, 2026-09-02**: `NO WRITE REACHED THE DEVICE, so no channel change was made` is gone, because a write that raises may already have landed. No branch now concludes the plan changed nothing. |
| writes reached the device, all rollbacks verified | `ChannelPlanPartialApplicationError` |
| a write that reached the device could not be rolled back | `ChannelPlanSafeStateError`, `SAFE STATE NOT VERIFIED` |

`accepted`, not `applied`, is the discriminator, and the distinction is real: a
**read-back** failure means the device took the command and returned the wrong
value, so that state *did* change and stays a partial application. Only a set that
raised means nothing arrived. The unrestorable never-accepted write is still
reported, with the reasoning stated inline — the restore would only have rewritten
the value already held, so if the write did not take effect nothing changed, and if
it partly did, that one property is the only one in doubt.

This also removed an inversion: before, a first-write failure with a *clean*
rollback raised `ChannelPlanPartialApplicationError` while the same failure with a
failed rollback would now raise the base class — a less severe class for a worse
situation. Class is now chosen by `accepted` alone, so the ladder is monotone.
`hint_for_error` gains the matching operator hint, so the base class does not fall
through to a generic one.

**Evidence.** Offline, replayed against the captured M5 `config.uicfg` in
`tests/fixtures/`, plus the captured 2026-08-05 M5 session history against which
the gate checker was validated (see the runbook), plus the 2026-08-06 M5 and demo
gate runs recorded above. `design/41-block41c-rig-gate.md` is the gate.

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

**Closed by design/35 Block 2** (`ef72b15` + `e9817ad`, merged as `a1b7579`,
M5/demo gate PASS 2026-08-01). At startup, `validate_live_rig` reads the live
installation's EMU semantic map and cross-checks every exact laser-enable
device/property against `illumination.shutters`. Guaranteed mode refuses an
undeclared, unresolved, malformed, or provenance-uncertain map. Explicit
`degraded_trusted_plugins` mode warns, continues with completeness suspended,
and does not silently authorize the missing pair as illumination.

The refusal names the EMU slot, exact device, exact property, logical
`constraints.illumination.shutters` location, and editable top-level YAML shape.
It never invents on/off values. On M5 the old deployed profile refused for three
missing iChrome enables; live EMU metadata established `on: "1"`, `off: "0"`;
the corrected profile completed with all five configured shutter pairs on the
existing `dedicated-illumination` path. Three browser confirmations were
declined with exact `0` read-back. One separately approved enable read back `1`,
and deployed `serve` Ctrl-C cleanup named and independently returned all five to
`0`. A separate demo installation with EMU artifacts but no readable
`config.uicfg` refused rather than claiming semantic completeness.

The boundary remains exact: this cross-check proves every *discovered semantic
EMU enable* is declared and therefore confirmation-gated and swept off. It does
not prove discovery found every physical emission path. It also runs at normal
startup, downstream of Phase 5's enumeration window; Block 4 must describe and
contain that separate limit.

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

## Error taxonomy and offline validation (checklist v2 Block 3, merged `0cb871f`, 2026-08-01)

Two usability defects closed, plus a new offline entry point that Block 4
(Phase 5 first-launch setup) reuses.

### The refusal taxonomy

`hint_for_error` (`microclaw/errors.py`) now dispatches on error class *before*
falling back to `_HARDWARE_HINT`, so a policy refusal is never presented as a
hardware fault. The `RigAuthorizationError` limb states plainly that the write
was refused by the authorization map or declined by the operator, that no
hardware diagnosis is warranted, and that retrying the identical call will fail
identically. That specific fix landed incidentally in `bb6290f` rather than
under this block; Block 3 verified its coverage across the tool path, the
`serve`/web path, and the CLI paths, and added the missing regression test for
the `authorization-map` CLI path.

Every `RigAuthorizationError` raise site now answers three questions: what was
refused, which declaration would have permitted it, and where that declaration
goes in the file. **A refusal that cannot name a legal declaration must say so
explicitly** rather than implying an edit exists. Three such classes exist and
are worded as dead ends on purpose:

- an **explicitly excluded** property or write path — unavailable until the
  exclusion is deliberately removed, which is a config-review act, not a
  refusal-driven edit;
- an **unclassifiable** actuator kind — needs its hardware semantics established
  before any declaration is meaningful;
- an **operator decline** — no declaration overrides a human saying no.

### The offline-validation contract

`validate_safety_config` (`microclaw/config.py`) returns a structured
`ConfigValidationResult` — `path`, `parsed`, `reviewed`, and a tuple of
`ConfigDiagnostic(kind, message, blocking)` over
`schema | review | guaranteed_mode | degraded_mode | live_check`. The
`microclaw check-config [path]` CLI is a thin presenter over it; Phase 5 calls
the function directly to check what it wrote. There is no second parser —
`ParsedSafetyConfig.from_yaml` already accumulates schema errors and this builds
on that accumulation.

**Offline validation checks the document, never the rig. A config can pass it
and still be refused by the live cross-check**, because offline and live
coverage are complementary and non-overlapping:

- the **strict schema** rejects blank/null `stage.*` edges and
  `named_stages[*].min_um/max_um`, but accepts `camera.max_exposure_ms: null`
  and all nine `acquisition` budgets as null;
- **guaranteed-mode `validate_live_rig`** rejects exactly what the schema
  misses, but only for hardware it can reach — the exposure check is gated on a
  reachable camera being found.

So the validator reports the guaranteed-mode live requirements it *cannot*
verify rather than returning clean. It emits a non-blocking `live_check`
diagnostic in **every** profile mode, so a programmatic consumer can never
confuse "checked and clean" with "nothing was checked."

`reviewed: false` is a distinct, expected state with a next action, not a parse
failure. An **absent** `reviewed` key is deliberately not the same thing: it
yields `reviewed=None` and a schema missing-key error, because a file that never
mentions review is malformed rather than intentionally unreviewed.

### Degraded mode is where a null cap actually bites

Runtime enforcement is uniformly `if limit is not None and value > limit`
(`microclaw/safety.py`), so a null limit that reaches runtime is simply
unenforced. Guaranteed mode's refusal is the only thing that normally stops one
arriving. Under `degraded_trusted_plugins` that refusal is suspended, so null
acquisition budgets and a null `camera.max_exposure_ms` are permitted at startup
*and* unenforced during the run.

The validator therefore emits a non-blocking `degraded_mode` diagnostic naming
every null cap and saying that leaving them null deliberately suspends those
protections. Non-blocking is correct — degraded mode is a sanctioned, documented
suspension of the completeness claim — but silence was not.

### Not done here

The optional fictional-example-value detector (flagging limits still equal to
`safety_config.example.yaml`) was **skipped** as marked defence-in-depth. The
`microclaw init` copy-the-example path that motivates it is Block 5's subject.
**Landed in Block 5, 2026-08-04** — see "Block 5 landed" below for what it
detects and, more importantly, what it does not.

## Phase 5 landed: first-launch setup (2026-08-02, checklist v2 Block 4)

Merged `6266807` after five gate rounds — three on the demo core, two on M5 —
and 1320 passed / 99 skipped / 3 expected warnings. This section records what was
*measured*, and corrects the parts of this document and of
`33-block14-phase5-dangling-impact-summary.md` that the measurements contradict.
Where they disagree with the prose above, this section wins.

### The rule "driver ranges are never defaults" is now scoped, not absolute

As written above and in the dangling-impact summary, that rule barred technical
ranges and allowed values from being *answer defaults* as well as safety bounds.
Round 1 failed the gate partly because of it: the interview asked ~90 questions
with no proposals, and the operator was asked to classify
`Camera.TestProperty1..6` with no statement of what the words meant.

What shipped, and what the rule now means:

- **Micro-Manager's writability and value-domain metadata is the classification
  default.** `read_only`, `pre_init`, `allowed_values`, `has_limits` and the
  reported type decide the proposed classification. This is the same information
  the Device Property Browser renders as enabled/disabled and as combo box versus
  slider.
- **Driver technical ranges are proposed, Enter-acceptable defaults for bounds**,
  including on stage travel and maximum exposure — the two most hazardous axes in
  the file. Operator decision, 2026-08-01, reaffirmed when the coordinator
  flagged the reversal. Conditions: the value is labelled as the driver's
  technical range and never as a reviewed bound; accepting it is an explicit
  keystroke; and the transcript records accepted-default versus typed-value for
  every such answer (`PROPOSAL ACCEPTED` / `OPERATOR OVERRIDE`).
- **Observed current values remain barred entirely**, as does any inferred
  physical kind or unit. That part of the original rule is unchanged.

The rationale for the reversal is empirical rather than convenience. An
unanswerable mandatory question does not produce caution, it produces a made-up
answer: M5's operator, asked for a "native full scale" with no proposal, read it
as a minimum, was refused for entering `0`, and entered `0.01` — declaring a
75 mW laser at a full scale of 0.01 mW. The same run answered an unexplained
illuminated-time confirmation threshold with `5000000000000000`, disabling the
only confirmation that measures light on the sample. **A default the operator can
see and override is safer than a question they cannot answer.**

### Refusal severity: which diagnostics refuse the process, which demote a claim

Round 1's generated profile validated offline and then would not start. Nineteen
properties the operator had classified `categorical` were each fatal, as was one
missing preset and a missing EMU configuration. None of them was an undeclared
hazard; every one was a claim the config made that the rig could not corroborate.

The rule that now decides severity: **a claim the rig cannot corroborate is
dropped and reported, which strictly narrows authority. An undeclared hazard
still refuses the process.** Block 2's undeclared-emission gate is the second
shape and stays fail-closed.

Four statements elsewhere in this document are corrected by what shipped in
`design35/startup-refusal-severity`:

1. A categorical raw write proven continuous by the live rig was described as a
   startup error. It now demotes to `excluded` and startup continues.
2. Declared/live property mismatches were described generally as startup
   failures. An absent declared device or property now demotes.
3. The channel-validation framing implied any preset mismatch fails startup. An
   absent preset name now demotes; an unreadable or unsafely-expanding *present*
   preset still refuses.
4. This document did not distinguish the EMU plugin being installed from the rig
   being EMU-configured. `Emu.jar` ships with every Micro-Manager, so its
   presence is now only a path-location hint (`_has_emu`); `EMU/config.uicfg`
   establishes EMU use (`_has_emu_config`).

### Block 4b landed: three typed-actuator contracts and setup boundaries (2026-08-03)

Block 4b merged as `04164fd` after five gate runs across the demo rig, M2 and
M5. The typed-actuator `kind` is a safety-contract name, not merely a value
shape:

| Kind | Contract |
|---|---|
| `absolute-position` | Clamp the raw property and feed the stage bounds. |
| `illumination-power` | Clamp the raw property and feed the dose ledger. |
| `bounded-numeric` | Clamp the raw property and feed nothing. |

Gain is the motivating `bounded-numeric`: it is post-detection amplification,
not integration time, and therefore is not dose-bearing. Exposure is
deliberately excluded from this kind. It remains on the metered `set_exposure`
path, while `_known_continuous_raw_pair` blocks the raw camera-exposure alias,
because longer integration changes light dose at the sample.

This taxonomy sits beside refusal severity because both prevent value shape
from silently choosing authority. The kind decides which safety systems a
declared actuator participates in; the severity rule above decides whether a
live contradiction narrows a claim or reveals an undeclared hazard that must
refuse startup.

**Explicit exclusion is not a safe substitute for leaving a conditionally
owned property undeclared.** Setup must not add an explicit exclusion for a
property whose authorization is already decided by a conditional rule. An
explicit `denied` row is checked first and shadows that rule. This
has now failed twice: Block 4's round-4 StateDevice position rows shadowed the
auto-classifier, and Block 4b's first demo round shadowed the `Core.Shutter`
preset allowance (the `device == "Core"` branch of the channel-preset loop in
`validate_live_rig`), which permits retargeting only to a declared illumination
shutter. The general setup rule is to leave such a property in the
authorization layer's vacuum. Silence is not permission: guaranteed mode is an
allowlist, so an undeclared property remains unwritable when its condition is
not met.

**Device type, not candidate presence, separates emitting from non-emitting
bounded numerics.** A camera surfaces its own internal and external shutter
candidates; treating any device with an illumination candidate as an emitter
therefore excluded M2's `Andor.Gain`. Setup now scopes the bounded-numeric
exclusion limb to emitting device types by reusing
`rig_inventory._NON_EMITTING_TYPES`. The StateDevice-position limb is
deliberately not narrowed this way: it addresses unreadable position labels on
a device that may also emit, not whether a numeric property itself represents
emission.

**Agent-mediated guards need an unknowable-invalid test value.** If a gate is
routed through the agent, declare a policy bound narrower than the driver range
and test a value outside policy but inside the hardware range, so the agent
cannot know from driver metadata that the value is invalid. Otherwise exercise
the guard without the agent. Forced-call wording reached the guard on the demo
rig and M2 but failed on M5; it is unreliable evidence in both directions. The
M5 agent's objection was correct: a guard exercised only because the agent
volunteers knowingly-invalid input is not what the gate claims to test. In all
agent-mediated steps, assert mechanically on the tool call and its result,
never on the model's narration.

**Two acquisition keys are deprecated without breaking deployed configs.**
`confirm_above_bytes` is accepted and validated when present but ignored by the
acquisition confirmation gate. `max_session_illuminated_ms` is optional; when
set it is an in-process runaway-loop brake whose ledger resets on process
restart, not a durable sample-dose guarantee. Guaranteed mode now requires
exactly seven finite positive fields: `max_frames`, `max_duration_s`,
`max_bytes`, `max_illuminated_ms`, `confirm_above_frames`,
`confirm_above_duration_s`, and `confirm_above_illuminated_ms`. Deletion was
rejected because deployed configs already set both old keys and must continue
to load.

### Three deliberate loosenings of "require explicit operator classification"

The dangling-impact summary requires explicit operator classification of *every*
illumination enable, emission and power candidate. Three narrow exceptions
shipped, each because the alternative was measurably worse:

1. **ON/OFF values are proposed** from a two-value domain — from `allowed_values`,
   or from a two-point integer `technical_range`, which is how M5's iChrome
   reports its eleven enable/emission pairs. Without it the operator hand-typed
   22 `1`/`0` answers, each logged `no proposal was available`. The
   *classification* question keeps having no default.
2. **The classification itself defaults to `emission/enable`** when the property
   name matches `laser|power|emission|enable` *and* the domain is exactly two
   on/off-shaped values. Deliberately narrower than the enable detector:
   `state`, `shutter`, `operation` and `output` do not qualify. The error
   direction is toward more gating, and the two-value condition means a
   continuous power property can never take it.
3. **Percent-versus-native is proposed from the property's unit suffix** —
   `(%)` and a bare trailing `%` mean percent, any other parsed unit means
   native — and **`full_scale` defaults to the driver's upper technical range**.

### A StateDevice's `State` is a position, not an emission candidate

Two corrections that only a live rig produced, both from M5.

**Classification.** Micro-Manager publishes `allowed_values` for a StateDevice's
`Label` and never for its `State`, which is an integer position. The generic
"no discrete value domain" rule therefore wrote an *explicit exclusion* for every
filter wheel, turret and slider position — and an explicit exclusion is stronger
than silence, so it also defeats the Phase 1 fast-follow auto-classifier, which
fills vacuums only. The consequence was found by using the rig, not by any gate
step: a profile that passed every check could not move a filter wheel. The domain
was never missing; it is the device's `state_labels`, which the inventory already
records. A StateDevice's `State` with no allowed values but N state labels now
defaults to categorical over positions 0..N-1.

**Detection.** `_ENABLE_NAME` matches the bare word `state`, which dragged every
wheel, turret and slider into the illumination interview to be classified as an
emission path. The pattern is deliberately broad and was **not** narrowed
generally; instead a StateDevice's `State` that reports state labels no longer
matches. Measured across both captured inventories before the change: exactly one
candidate is dropped — M5's `Thorlabs ELL6.State`, a lens slider — while all 21
genuine M5 gates and the demo rig's `White Light Shutter.State` (a ShutterDevice,
no state labels) are retained.

**The laser-engine exception.** A StateDevice position on a device that also
surfaced illumination candidates is excluded, with the reason printed and the
path revisitable by exact name. M5's `iChrome-MLE-TCP` is both a StateDevice and
a laser engine, and its labels are `State-0`, `State-1`, `State-2` — which
establish nothing about what the positions do. An earlier version forced a
question here; the operator accepted the proposal, widening authority on a laser
engine, because the question was unanswerable. This is the Phase 1 fast-follow's
laser-engine widening in a new form, and it fails closed for the same reason.

### Core's device-assignment properties are not generic raw writes

`Core.Camera`, `Core.Focus`, `Core.XYStage`, `Core.Shutter`, `Core.AutoFocus`,
`Core.Galvo`, `Core.ImageProcessor`, `Core.SLM`, `Core.ChannelGroup` and
`Core.Initialize` were being declared categorical, because Micro-Manager reports
them writable with a discrete domain.

They are structural identity, not generic controls. `stage.z_min/z_max` is
enforced against whatever device Core names as the focus device, and M5 offers four
(`PIZStage`, `SmarAct 1D`, `Thorlabs ELL17/ELL20`, `Thorlabs ELL20`), so one
categorical write re-aims every reviewed bound at a different mechanism.
`Core.Shutter` likewise moves the illumination gate, and `Core.ChannelGroup`
changes what `channels.allowed` names. The same coupling runs through
`_known_continuous_raw_pair`, which resolves focus, XY and camera live from Core.
Setup does not declare any of these ten as categorical or explicitly excluded;
guaranteed mode's allowlist therefore leaves direct raw writes unauthorized
without shadowing authorization's conditional rules. `Core.Shutter` has one
narrow preset-apply allowance: it may retarget only to a declared illumination
shutter and remains under that gate. The other device assignments have no such
allowance. `Core.AutoShutter` is deliberately not in the set: it is a real
illumination control and keeps its own classification path.

### Which section owns which write path

Recorded here because the operator asked and the schema does not say. The key was
called `rig_profile` until checklist Block 4d renamed it (2026-08-03), and the
reason for the rename is the reason this section had to exist: `rig_profile` read
as a description of the rig, but the rig's description is the inventory. The name
`property_authorization` says what it is — the raw-property write-authorization
map — and the table below says what it still is *not*, which is the whole map.

| Write path | Declared in | Bounded by | Tool |
|---|---|---|---|
| Raw property, discrete domain | `property_authorization.allowed_categorical` | MM's own value domain | `set_device_property` |
| Raw property, numeric | `property_authorization.allowed_numeric` (kind + units + min/max) | the declared canonical range | `set_device_property` |
| Raw property, never | `property_authorization.denied` | — | refused |
| Core focus position | `stage.z_min/z_max` | reviewed µm bounds | `move_stage_z` |
| Core XY position | `stage.x_min/x_max`, `y_min/y_max` | reviewed µm bounds | `move_stage_xy` |
| Any other single-axis stage | `named_stages` (per device label) | reviewed µm bounds | `move_named_stage` |
| Camera exposure | `camera.max_exposure_ms` | reviewed ms cap | `set_exposure` |
| Illumination enable | `illumination.shutters` | blocking human confirmation | gated write |
| Illumination power | `illumination.power_properties` | `max_power_percent`, step ratchet | gated write |
| Acquisition dose | `acquisition.*` | per-plan and per-process budgets | acquisition tools |

Three consequences worth stating explicitly, because each has already confused a
reader or an implementer:

- The last three rows of `property_authorization` are *generic* raw properties. The built-in
  typed capabilities — `stage-position`, `exposure`, `illumination`,
  `acquisition-dose` — own their own sections and are deliberately not duplicated
  into `property_authorization`. `_known_continuous_raw_pair` blocks the raw-property route
  for the core focus position, the core XY position, and camera exposure so the
  two cannot diverge.
- A typed actuator of kind `illumination-power` is the one thing declared twice
  on purpose: it must appear in both `property_authorization.allowed_numeric` and
  `illumination.power_properties`, or startup refuses, so the percent cap and the
  ratchet stay active.
- `named_stages` and `allowed_numeric` differ by *which API the motion goes
  through*, not by hardware kind. `named_stages` bounds a device moved through
  MMCore's stage API, keyed by device label, always µm. `allowed_numeric` bounds
  a raw property write, keyed by device *and* property, carrying its own unit.
  The same stage can need both if both routes are wanted. Setup emitted no
  `named_stages` at all until Block 4c (2026-08-03), which left every single-axis
  stage that is not the core focus device unreachable — three of M5's five. It
  now asks for their travel bounds and emits the entries; Block 4d's M5 gate
  exercised the result, moving `SmarAct 1D` to 1400 µm under the renamed schema.

### Block 4d landed — `property_authorization`, and the old key is gone

Merged 2026-08-03. The schema is now:

```yaml
property_authorization:
  mode: guaranteed            # or degraded_trusted_plugins
  allowed_categorical: []     # was categorical_properties
  allowed_numeric: []         # was typed_actuators
  denied: []                  # was excluded_properties
```

**There is no migration path, and that is deliberate.** No dual-key acceptance,
no deprecation window, no rewriter. A config still carrying `rig_profile` is
refused with `rig_profile: unknown top-level key` alongside
`property_authorization: missing required property authorization map` — two
generic messages from the existing strict-key validation, with no bespoke
"rename it to…" hint. That was an operator ruling: they own every machine running
this system, replace the configs by hand, and asked that no record of the old key
survive in the code. A reader who finds this surprising should not reconstruct a
migration path from it.

An earlier round *did* implement dual-key acceptance with a deprecation
diagnostic and a horizon, and it was removed. Two findings against it are worth
keeping because they generalise: refusal messages hardcoded the new key names, so
an operator on the old key would have been told to add a section that then
tripped the both-keys-present error; and the deprecation notice was raised only
in `validate_safety_config`, which live startup never calls, so nobody running a
working rig would ever have seen it.

Two acknowledged warts, recorded rather than fixed:

- **`mode` is filed under a property-authorization key but is not a
  property-authorization field.** `config.py` reads it to decide whether
  guaranteed mode is in force for the whole process. Promoting it to a top-level
  key would have been a second independent config break, and the operator's
  standing ruling is that a config-breaking rename gets its own branch and gate.
- **An `illumination-power` actuator is still declared twice** — in both
  `property_authorization.allowed_numeric` and `illumination.power_properties`,
  or startup refuses. That is the deliberate design recorded above, not an
  oversight, but it remains the one place the split map is redundant rather than
  merely partitioned.

`named_stages` deliberately did **not** move under the new key. It is a
capability range policy consumed by `check_named_stage`, in the same family as
`stage`, `camera.max_exposure_ms` and `illumination` — not a raw-property write
authorization.

Gate evidence: demo G0/G1/G2 pass; M5 G4 pass, with a live `snap_and_analyze`
(2304×2304, SNR 3.45) and a bounded `move_named_stage` to 1400 µm achieving
1399.9 µm. M5's real deployed config was proven parseable under the new schema by
offline replay of the hash-verified file — exactly four lines change, all keys.

### What the rig gates established, and what they did not

Measured on M5 at `a742d73`: 53 categorical, 0 typed actuators, 79 excluded, 21
illumination shutters, 13 power properties; the generated profile passed offline
validation, started a session, moved a filter wheel by `State`, and switched the
Hamamatsu's `Sensor Cooler` to `ON` with `CCDTemperature` reading back −8 °C. On
the demo core: 22 prompts, 12 needing a typed answer, and a profile that
round-trips validator → review → session.

Not established: that heuristic discovery found every physical emission path —
the setup text says so itself and the operator classification step exists because
of it; that the pre-validation enumeration window is closed, which this documents
rather than closes; and anything about continuous focus, which Block 4 hard
excludes.

**A note on the deployed-versus-generated comparison.** M5's deployed config
was generated by an earlier Microclaw; it is not an independently hand-authored
reference. The comparison is therefore a difference-finder, not an oracle:
agreement between the two files is not corroboration. The Core device-assignment
finding it produced is unaffected — it stands on its own argument and would be a
defect with no deployed config in existence.

### Sanctioned rig-fact exception in `microclaw/`

The standing rule is that rig facts live in gate docs, design notes and rig
profiles, never in `microclaw/`. `first_launch.py` breaks it twice, deliberately
and with authorization from the block's item text: it matches the `ttl.state0`
path shape (the known GenericDevice false positive, behind a required human
confirmation) and the `pfs`/`perfect focus` name fragments (a hard exclusion).
Neither carries a bound or a geometry, and both are shape heuristics rather than
values. Recorded here rather than left silent.

### Block 4e landed: discovery keys off device type, and shutter gating is not-`off_value` (2026-08-03)

Block 4e merged as `9b88394` after two implementation rounds and three
first-time gate passes on M2, M5 and the demo rig. Both defects it fixes were
found by Block 4b's gate on **M2, the first time that rig was ever enumerated**,
and neither was 4b's code.

**Illumination discovery keys off Micro-Manager's device typing, not only
property names.** `_ENABLE_NAME` (`rig_inventory.py:48`) is deliberately broad
and stays so, but it is no longer the only route: a writable on/off-shaped
property on a device Micro-Manager types as a `ShutterDevice` is an emission
gate by MM's own classification, whatever the vendor called it. M2's
`Cobolt561.Laser` — allowed values `Off`/`On`, and **the device `Core.Shutter`
names, so the rig's core shutter** — matched no token and was invisible to
setup; the Luxx lasers were caught only because theirs are named `Laser
Operation Select`. Widening the regex with `laser` would have fixed M2 and
missed the next vendor. The structural branch uses the same `len(allowed) <= 4`
shape rule as the name branch: an earlier form required exactly two values,
which would have made this block unable to discover the very three-value
shutters its other half exists to gate.

**Shutter gating is `value != off_value`, not `value == on_value`.** A declared
shutter may have more states than its two declared endpoints, and only the
reviewed off value is known non-emitting. `Andor.Shutter (Internal)` takes
`Open`/`Auto`/`Closed`; measured on M2, `Auto` was permitted with **no
confirmation at all** — and on an Andor, `Auto` is the state that opens the
shutter on every exposure. The value that emits most routinely was the one no
human had to approve. The audit for the same equality-versus-membership
asymmetry found no other site needing the change: `shutter_all` writes each
declared `off_value` directly, the power ratchet compares numbers and ratios,
and the EMU map already uses exact `(device, property)` membership.

**The residual is unchanged in kind and sharper in detail.** This proves
*declared* paths are gated; it does not prove discovery found every physical
emission path. Beyond the standing heuristic limits, the demo rig shows a
stronger form: `LED Shutter` is a `ShutterDevice`, is selectable through
`Core.Shutter`, and has **no writable gating property at all** — its emission
runs through MMCore's shutter API. **Some emission paths have no property to
discover**, so property-based discovery is structurally incomplete, not merely
imperfect.

That is also why the core shutter having no declared shutter property is **not**
a fail-closed startup condition. It was considered and declined: on the stock
demo rig the operator could supply no legal declaration, so refusing would be a
claim the rig cannot corroborate — the shape Block 4 round-1 finding 5 ruled
out, where a refusal must narrow authority rather than stop the process.

**Deliberate false positives are named in the gate runbook, not excluded in
code.** `Cobolt561.Autostart` and `Analog Impedance` are `Disabled`/`Enabled` and
so match the on/off shape, while being power-up behaviour and modulation-input
termination respectively. The module's breadth doctrine (`rig_inventory.py:43`)
tolerates a false positive because a false negative says the rig cannot do
something; a name-based exclusion would also have put rig facts in
`microclaw/`. Instead the runbook named both, gave their meanings, and stated
the consequence — a property declared a shutter is written to its `off_value` by
`shutter_all` on **every session exit**, which for `Autostart` means rewriting
persistent laser configuration. The operator excluded both.

### Block 4f landed: `channels.allowed` names the group the authorizer reads (2026-08-03)

Block 4f merged as `9010158` after a first-round implementation and two
first-time gate passes, on M2 and the demo rig.

**The producer and the consumer now name the same group.** First-launch setup
emits `channels.allowed` from `CHANNEL_CONFIG_GROUP` alone — the same constant
`validate_live_rig` reads, and deliberately not `Core.ChannelGroup`, which is
writable. Before this, setup collected preset names from *every* configuration
group, so it generated profiles guaranteed to warn on the rig that generated
them: six dropped presets on M2, ten on the demo rig. Demo **has** a `Channel`
group, which is what establishes that this was over-claiming everywhere rather
than a missing-group special case.

**An absent or empty group yields an explicit empty list, never an omitted
key.** `allowed_channels` is `Optional`, and `None` means *all* live `Channel`
presets are authorized (`safety.py:216`, `authorization.py:973`). So the
intuitive implementation — omit the key when there is nothing to put in it —
would have silently *widened* authority on exactly the rigs this block fixes.
Setup always writes the key, and says in the profile header why `[]` is there
and what omitting it would have meant.

**Presets in other groups are reported, not discarded.** They are real and
useful; they are simply not channels. Each non-`Channel` group is listed in the
generated review notes as presets microclaw does not drive. Group filtering
happens before any name enters the allowlist, so a rig whose groups share preset
names — demo's `Channel` and `Channel-Multiband` share all four — cannot leak
the wrong group's preset.

**The live diagnostic distinguishes two different operator situations.** A
preset missing from a `Channel` group that exists is still answered with "add it
to that group". A rig with no `Channel` group at all is answered differently:
the old wording told operators to add objectives, light paths and camera modes
to a Channel group, which is never the right repair.

### Block 4c landed: setup authors `named_stages` (2026-08-03)

Block 4c merged as `8ce3401` after one returned round, a demo gate, an M5 gate
that found a real defect, and a demo re-gate.

**Every reachable non-core stage is now declarable, and none is declared
silently.** `first_launch.py` hardcoded `"named_stages": []` and the interview
never asked, so `check_named_stage`'s fail-closed rule — a stage with no entry
may not be moved at all — made every single-axis stage that is not the core focus
device unreachable after setup. On M5 that was three of five devices. Setup now
asks each one, proposes the driver technical range where MM reports one and says
plainly where it does not, and emits the entries.

**The three write paths for stage-like motion stay distinct**, and M5 proved it
on hardware the demo rig cannot represent. Core focus and core XY keep `stage.*`
and their dedicated tools; any other single-axis stage goes through
`named_stages` and `move_named_stage`; a positional raw property that the stage
API cannot reach remains a `allowed_numeric` `absolute-position` entry. Both M5
Thorlabs `Position (um)` properties were excluded from `bounded-numeric` with
"MM identifies a stage position property; bounded-numeric cannot bypass the
fail-closed named/core stage policy" — the demo's `Z.Position` reports no limits
and never reaches that branch, so this separation had never been exercised
against a device that could violate it.

**Declaring a stage is an operator act with a real alternative.** Each stage is
offered `declare bounds` or `exclude; leave unreachable`; declining emits no
entry, asks no bound questions, and records an `OPERATOR EXCLUSION` note. This
matters because the pre-block state of these stages was unreachable: without an
opt-out, the block would have widened authority with no way to refuse.

**A non-core `XYStageDevice` is excluded with a printed reason.** `named_stages`
is single-axis by construction, so setup states that it cannot express honest
independent per-axis bounds rather than silently omitting the device.

**A continuous-focus offset stage is authored, not withheld.** Operator ruling,
2026-08-03. This corrects a reading of Block 4's item "mark PFS-offset workflows
unsupported even when the offset has reviewed bounds" as a requirement to omit
the declaration. The impact-summary row that item derives from says the opposite
— Phase 5 *can* collect reviewed `TIPFSOffset` bounds; what a generated profile
must not do is imply an in-range command was **achieved or settled** — and Block
0b's Nikon stopgap worksheet already declares `TIPFSOffset` under `named_stages`
while excluding only `TIPFSStatus.State`. **The exclusion boundary is the
continuous-focus enable, not the offset stage.** What setup owes instead is
honesty: the review note records that `move_named_stage` may report the previous
target as `achieved_um` until the settling work lands, and that an offset write
commands a servo, so the declared offset bound is not a bound on the resulting Z
excursion.

**Only a positional device may be named an offset stage.** The M5 gate produced
`possible offset stage(s) ['HamamatsuHam_DCAM']` — the camera — because
`CONVERSION FACTOR OFFSET`, an ADC offset, matched on name alone. The name match
predates this block; what changed is that the note then asserted servo motion and
stale read-back about a camera. A name match now counts only for a loaded stage
or a typed actuator whose kind is `absolute-position`.

Residuals, stated rather than implied:

- The offset warning is a **label-shape heuristic** over `offset`, `pfs`,
  `perfect focus`, `autofocus` and `focus lock`. An offset stage named with none
  of them is authored with no warning. It is a note, never a bound or a gate.
- A declared range is a **reviewed travel bound, not evidence that the device is
  safe to move through it**, and on an offset stage it bounds the commanded
  offset rather than the resulting focus-drive excursion.
- The corrected offset note was verified by replaying M5's captured
  `inventory.json` through both the pre-fix and post-fix code, not by a live M5
  session. No operator has yet read the corrected note on that rig.

## Block 5 landed: deployed-config hygiene and the `init` path (2026-08-04)

Checklist v2 Block 5. Merged `d14c147`; implementation `6262acb` + `577acc4`,
runbook corrections `c0344f2` + `8028145` + `c48edc1`. Demo G0–G3 and M5 G0+G4
passed 2026-08-04.

**The first-run path.** `microclaw init` no longer copies the fictional example
by default. It prints the real path — `first-launch-setup` → human review →
restart — offers to run setup, and copies the example only under an explicit
`--from-example`. Operator ruling, not an implementer's choice: `init` was kept
rather than deleted because design/17's installer and shortcut sequence and
operator habit both name it. Non-interactive invocations print the path, write
nothing, and exit `0`, so a scripted install cannot block on a prompt.

**Installation no longer contacts the rig.** `install.bat` previously ran
`init` as its last step. Once `init` could open a live ZMQ connection that was a
defect, because the installer tells the user to enable the ZMQ server only
*after* it finishes: measured, answering the offer with no Micro-Manager running
did not return within three minutes, since `Core()` blocks before the surrounding
`SetupRefusal` handler can run. Installation now ends at the desktop shortcut and
prints the setup command as a post-install instruction ordered after the ZMQ
prerequisite.

**Offline detection of unchanged example limits** (`config.py`, reported by
`microclaw check-config` as an `example_limits` diagnostic). It compares the
config's numeric leaves against the packaged example read at runtime, so the
check cannot drift from the file it compares against. It is **non-blocking by
design**: a real rig may legitimately share a value with the example.

Measured discrimination, against captured configs rather than fixtures — an
unmodified example copy flags all 14 numeric limits; the demo and M2 reviewed
profiles and a synthetic reviewed control flag none; the demo machine's live
profile and M5's deployed profile each flag exactly two,
`acquisition.confirm_above_illuminated_ms` and `stage.z_min`, both coincidental.
The coordinator's offline replay of M5 predicted those two before the rig ran,
and the rig reproduced them.

**The residual limit, stated plainly because the M5 gate demonstrated it.** This
detector answers "are these values still the example's?" It cannot answer "are
these values sane." M5 passes it with `check-config` exit `0` while carrying an
acquisition duration cap of ~317,000 years. A clean `check-config` therefore does
not mean a config's budgets bind — only that they are not the example's. An
implausible-magnitude or non-binding-cap diagnostic is genuinely different work
and is not in this block; it is recorded in checklist v2's no-block register.

**`serve` writes its startup banner when redirected.** The banner was a bare
`print()`, Python block-buffers stdout when it is not a console, `serve` then
blocks in the server loop forever, and uvicorn ran at `log_level="warning"` so
there was no second source — an operator following our own runbooks got a 0-byte
log. Both banner branches and the illumination-off shutdown line now flush.
Verified on the demo rig: 260 bytes containing the GUI URL, where Block 4d's gate
got 0 bytes under two different capture methods.
