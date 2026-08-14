# Approved hooks may change bounded hardware properties

Date: 2026-08-14

Finding from the TIRF session at
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/tirf test with amr/second_test_with_script`,
history `20260814_115658_058133_microclaw_history.jsonl`.

This is design 52, deliberately separate from design 50. It neither edits another
block's branch nor folds itself into one.

> Written as design 51 and renumbered to 52 on commit, 2026-08-14: it was
> authored in a parallel session and never committed, and by the time it landed
> the number was taken by `design/51-omitted-illumination-still-restricts.md`,
> which is unrelated. Nothing else in this document has been edited — its status
> notes about work "currently being implemented" are as written and are already
> stale (block 50b merged as `d809173` the same day). **Not yet reviewed,
> scheduled, or given a block; parked deliberately.**

---

## Finding — the safe hook boundary excludes an already-safe operation

The operator asked Microclaw to find the optimum TIRF angle, save every inspected
image, and preferably keep the angle sweep in one stack. The relevant axis was a
declared named stage, `Thorlabs ELL17/ELL20`, and ordinary parent-side calls to
`move_named_stage` successfully moved it through the requested range. The run
therefore had all the ingredients for one acquisition: a finite list of angles,
a named stage with configured bounds, an image metric, and explicit operator
approval.

It could not express them as one acquisition. In lines 22–34 the agent repeatedly
proposed and withdrew the same solution:

> a hook that moves the TIRF axis itself

and then:

> Saved hooks can't do that.

The agent was correct about the current contract. `hook_decisions.py` gives a
saved hook a closed set of typed proposals: `MoveStage` can address only core X,
Y and Z; `SetExposure` and `SetIlluminationPower` cover two special properties;
there is no named-stage action and no general property action. The adapter denies
the pycro-manager event queue, and the saved-hook constructor never receives
`ctrl` or `guard`. A hook cannot call `move_named_stage` or
`set_device_property`, even when the exact same target and value are admitted by
those parent-side tools.

That containment is useful. The defect is the vocabulary, not the trusted-parent
boundary. We removed authority along with access: instead of letting an approved
hook request a bounded capability, the system made the capability impossible.
The result was not safer imaging. The agent talked itself in a circle, abandoned
the requested single sweep stack, and took the angle series as individually
displayed snaps. The operator spent time answering choices created by the tool
gap, and the sample received the exploratory exposures anyway.

This also exposes a policy mismatch. Microclaw currently treats provenance
(`saved_untrusted`) as a permanent reason to prohibit whole classes of hardware
effects. The meaningful questions are instead:

1. Did the operator approve this hook having this capability for this run?
2. Is the proposed target within the approved capability envelope?
3. Does the proposed value satisfy the target's actual configured/driver bounds?

If all three answers are yes, provenance is a reason to warn and mediate the
write, not a reason to prohibit it.

## Decision — approve a capability envelope, dispatch every write in the parent

Add two proposal types to the saved-hook decision vocabulary:

```python
@dataclass(frozen=True)
class MoveNamedStage:
    device: str
    position_um: float
    kind: str = "MoveNamedStage"


@dataclass(frozen=True)
class SetDeviceProperty:
    device: str
    property: str
    value: str
    kind: str = "SetDeviceProperty"
```

These are proposals, never direct hardware handles. `UntrustedHookAdapter`
parses them through the closed union and the trusted parent performs the write.
Saved source still gets pixels and metadata only: do not pass it `ctrl`, `core`,
`guard`, an event queue, a setter callback, or a generic RPC object.

Before acquisition begins, the runner derives the maximum requested capability
from explicit tool arguments, renders it for the operator, and asks once. For
the TIRF case the confirmation should be concrete enough to review:

```
ALLOW HOOK HARDWARE CONTROL FOR THIS RUN
Hook: tirf_angle_sweep (pinned sha256: ...)
Named stage: Thorlabs ELL17/ELL20
Approved interval: 19639–21294 µm
Maximum writes: 18
Acquisition: 18 frames, D:\SSD\260814_microclaw_TIRF_2\...
Every proposed value will still be checked against named_stages bounds.
```

Declining performs no acquisition and no write. Approval is bound to the hook's
pinned bytes, runner invocation, exact targets, value envelopes, and write
budgets. It is not approval for “hardware access” generally, is not persisted in
the hook manifest, and does not carry to another run. A session grant may repeat
only the identical subject tuple; broad `kind="hook_motion"` grants without a
subject are not permitted.

The acquisition tool takes an explicit envelope rather than inferring authority
from hook source. A proposed shape is:

```python
hook_hardware={
    "named_stages": [
        {
            "device": "Thorlabs ELL17/ELL20",
            "min_um": 19639,
            "max_um": 21294,
            "max_writes": 18,
        }
    ],
    "properties": [],
}
```

Property entries carry the exact `(device, property)`, an allowed value set or
numeric interval as appropriate, and `max_writes`. No wildcard device, property,
or value is accepted. A categorical property uses `allowed_values`; a numeric
property uses `min`, `max`, and its declared units/adapter. The envelope is part
of the acquisition plan and export, not a free-form `hook_params` value (those
are hook-controlled inputs and must never grant authority).

### Runtime checks: bounds are the hard stop

For every `MoveNamedStage` proposal, in this order:

1. Require an approved entry for the exact device and decrement no budget yet.
2. Require the finite target to be inside the approved per-run interval.
3. Call `guard.check_named_stage(device, position_um)`. This retains the
   configured physical/safety travel bounds and fails closed when the named
   stage has no bounds.
4. Call `core.set_position(device, position_um)`, wait for the device, read the
   achieved position, and record requested, achieved and error.
5. Consume one write only once dispatch is attempted; if the bridge raises,
   mark the baseline/position uncertain and abort the acquisition rather than
   blindly issuing the next relative decision.

For every `SetDeviceProperty` proposal:

1. Require an approved entry for the exact pair and value envelope.
2. Route through the same capability-aware **bounds** checks as the public
   property tool: typed-actuator range/unit validation, stage bounds, exposure
   bounds, illumination bounds, or the device's allowed categorical values.
   Do not reproduce those checks in the adapter.
3. Apply, wait when the device exposes a wait, read back, verify with the same
   numeric/string semantics used by the channel-plan executor, and audit the
   result.

This requires separating `check_device_property`'s two present jobs. It combines
numeric/categorical validation with `check_property`'s allowlist/denylist policy.
For this path, the exact, audited hook envelope approved by the operator **is the
authorization** and replaces that allowlist/denylist decision; the underlying
bounds/type validator is still mandatory. Otherwise a historical categorical
exclusion could veto the exact approved action even though the value is inside
the device's domain, preserving the defect under a different name. The ordinary
`set_device_property` path is unchanged and continues to apply its existing
authorization policy.

The governing policy is: **an operator-approved hook may modify a hardware
property inside both its run envelope and its configured property/stage bounds.**
Microclaw may prominently warn about motion, illumination, dose, repeated
writes, or an unusually broad envelope. It must not convert those warnings into
a provenance-based refusal. It refuses a proposed write when the target was not
approved, the budget is exhausted, the value is malformed, or the value is
outside a real configured/driver bound.

This is deliberately broader than adding only `MoveNamedStage`. The TIRF axis
shows the defect, but a stage-only patch would recreate it for a filter state,
camera setting, trigger mode, or typed actuator next week. Named-stage motion is
a first-class action because it has device-specific move/wait/read-back
semantics; other properties share one capability-aware property action.

“Property” here means a Micro-Manager hardware property. It does not grant file,
network, process, import, credential, configuration-file, safety-policy, or
Microclaw-control-plane mutation. Those are outside this design and remain
unavailable to the hook.

### Approval and bounds are different controls

Approval answers *whether the hook may act*. Bounds answer *where it may act*.
Neither substitutes for the other:

- A named stage with configured travel `18000–22000 µm`, approved for this run
  only over `19639–21294 µm`, is confined to the narrower interval.
- Approval cannot widen `named_stages` or a typed numeric bound. The operator
  changes those through the safety-configuration workflow, not through a hook
  dialog.
- A configured categorical set and a run-approved subset combine by
  intersection.
- A property allowlist/denylist does not add a second authorization vote after
  the operator approves the exact hook envelope. It may be shown as a warning;
  only the target's bounds/type/domain remain a hard property-level gate.
- If Micro-Manager reports device limits or allowed values narrower than the
  reviewed config, use the intersection and show it before approval.
- A readable property with no configured or driver-reported physical domain may
  still be approved for an exact categorical value set. An unbounded numeric
  property is warned about and may be approved only as an exact finite set of
  values for that run; the dialog must say that Microclaw has no independent
  numeric range to verify. This preserves the operator's authority without
  pretending an unknown bound was checked.

Existing illumination and acquisition dose confirmations are folded into the
single pre-run summary so the operator does not approve an 18-frame control loop
and then face a hidden confirmation from the hook thread. All confirmation must
happen before `Acquisition(...)` starts. Runtime dispatch never blocks the
pycro-manager callback waiting for UI input.

### Timing: use the pre-hardware callback for a per-frame target

`analyze_frame` runs after the image exists, so an action returned for frame N
can affect only frame N+1. That is useful for feedback but awkward for a planned
angle stack and easy to index incorrectly. Add a saved-hook planning callback,
or equivalently a parent-owned per-event action plan, that runs before hardware
setup for each frame and may return only approved hardware actions. The target
position must settle before that frame's exposure, and the achieved named-stage
position must be stamped into that frame's metadata/log.

For a predetermined sweep, prefer a declarative event-associated plan generated
before the run:

```python
[
    {"frame": 0, "actions": [MoveNamedStage("Thorlabs ELL17/ELL20", 21294)]},
    ...,
    {"frame": 17, "actions": [MoveNamedStage("Thorlabs ELL17/ELL20", 19639)]},
]
```

Adaptive feedback may propose the next target from `analyze_frame`; the parent
queues it for the next event, labels it as such, and aborts rather than exposing
if there is no next authorized frame. Never silently apply a post-image proposal
after the acquisition has completed.

## Failure semantics and audit

Every proposal receives an `accepted`, `refused`, or `failed` hook-action record
with the action, frame identity, reason, and read-back. Do not silently continue
after a refused or failed hardware action: the next image would be mislabeled as
having the requested state. Abort the acquisition, return the actual dataset and
log paths, say how many frames were exposed, and report the last known hardware
state. This follows the existing mid-acquisition failure rule.

At normal completion, restoration is explicit in the envelope:

- `restore: "entry"` reads and records the initial value before acquisition and
  restores it through the same guard/write/read-back path.
- `restore: {"value": ...}` uses an approved bounded terminal value.
- `restore: "leave"` is allowed but called out in the confirmation.

Restoration is another authorized write and is reserved in the budget. If it
fails, report it loudly; do not claim the entry state was restored.

## Documentation and model behavior

Update `get_hook_documentation`, action imports emitted into saved-hook examples,
`describe_hook`, and the runner schemas together. The docs should say:

> Saved hooks cannot access hardware directly. They may propose named-stage or
> device-property changes when the acquisition call supplies an explicit bounded
> capability envelope and the operator approves it before the run. The trusted
> parent checks every proposal and performs the write.

Remove the current categorical advice that arbitrary named-stage/property motion
is impossible. When a requested workflow needs such a write, the agent should
offer an approved bounded hook directly, show its target/range/write count, and
ask once. It should not repeatedly redesign the acquisition around the missing
action or pressure the operator to surrender requested output structure.

## Export

An exported acquisition must reproduce the same mediation, not turn approved
hook proposals into raw `core.set_property` calls. Inline the action types,
parser, envelope validator, guards, write budgets, verification, audit, and
restoration logic used live. The exported script prints the approved envelope
and requires confirmation before connecting/starting unless the user explicitly
requests a non-interactive artifact and accepts that fact in the export dialog.
The pinned hook hash and envelope are embedded in the script.

## Rejected alternatives

- **Make saved hooks reviewed built-ins.** Provenance promotion gives the hook
  direct controller access and bypasses the useful proposal boundary. Approval
  should grant a narrow capability, not trust the whole Python module forever.
- **Pass `ctrl` or `set_property` into the saved hook after confirmation.** That
  makes the displayed envelope advisory; hook code can write a different target
  or value. Dispatch stays in the parent.
- **Add only `MoveNamedStage`.** It fixes this session while preserving the same
  arbitrary prohibition for every other bounded property.
- **Require the agent to perform moves between separate acquisitions.** This is
  the workaround that caused the circle and cannot produce one correctly
  indexed stack.
- **Confirm every callback write.** It blocks the acquisition thread, makes
  unattended stacks impossible, and encourages blind approval fatigue. Confirm
  the finite envelope once, then enforce it mechanically.
- **Let hook approval override configured bounds.** Approval grants authority;
  it does not edit physical/safety limits. Bounds remain the only hard refusal
  on an approved target/value.
- **Treat `plugins.allow_hardware_motion` as hook approval.** That flag concerns
  opaque Micro-Manager plugins and is too broad, not tied to a hook hash, target,
  range, run, or write count.

## Evidence and gates

Write the failing tests first: today `parse_action` rejects both new action kinds
and a saved hook cannot produce a one-dataset named-stage sweep.

### Unit/contract gates

- Both new actions parse from dataclass and dict forms; unknown fields, booleans,
  NaN/infinity, empty names, and malformed envelopes refuse before acquisition.
- Contract analysis recognizes/import-checks the new names and the standalone
  script emitter defines every referenced name.
- No hardware envelope means the existing behavior: proposal refused and no
  write. A declined pre-run confirmation means no acquisition and no write.
- Exact target, run interval, configured named-stage bounds, and driver limits
  intersect. Assert each boundary is inclusive and one representable value
  outside each boundary is refused without a core write.
- A `MoveNamedStage` action calls `check_named_stage`, moves the labeled device,
  waits, reads back, and records achieved position. It never calls core Z/XY
  setters accidentally.
- `SetDeviceProperty` reuses the public property's capability-aware bounds and
  verification while the approved envelope replaces its allow/deny policy.
  Test categorical, bounded numeric, named-stage position via a raw property
  pair, exposure, illumination, and an approved pair that the ordinary raw
  property path excludes.
- An approved in-bounds write is not refused merely because the hook provenance
  is `saved_untrusted` or because the action is hardware motion.
- Unapproved target/value and exhausted write budget abort the run before the
  affected exposure. Bridge failure returns the partial dataset path and last
  known state.
- Entry-state restoration succeeds through the same checks; failed restoration
  is reported and never described as success.
- Approval audit includes hook hash, exact envelope, write budget, acquisition
  plan, decision, and operator identity. Changing one byte or one envelope field
  invalidates a session grant.

### TIRF acceptance gate

On the same rig, create an 18-frame timelapse with one named-stage target per
frame for `Thorlabs ELL17/ELL20`, within its reviewed `named_stages` bounds.
Approve the displayed `19639–21294 µm`, 18-write envelope once.

Pass requires:

- exactly one confirmation before acquisition and none from the hook thread;
- one NDTiff dataset containing 18 frames;
- each frame's metadata/log names requested and achieved TIRF position;
- all 18 writes pass `check_named_stage`, settle, and read back;
- the hook can compute/log the image metric and select an optimum without the
  agent interleaving manual moves;
- the requested restoration policy is verified;
- a second run proposing one position just beyond the configured named-stage
  bound refuses before that exposure and reports the partial dataset; and
- export produces a script that compiles and preserves the same approval and
  bounds checks.

The product criterion is not merely “the action exists.” Repeating the original
request should lead directly to a bounded capability summary and one approval,
then complete the single-stack sweep. The agent must no longer say that a saved
hook cannot move a named stage or talk itself into separate snaps when the move
is approved and in bounds.
