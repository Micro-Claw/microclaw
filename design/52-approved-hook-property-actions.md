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
> which is unrelated.
>
> **Reviewed 2026-08-15 against `main` at `bdffb14`, and the Decision section
> below is the reconciled one.** The Finding was checked claim by claim and
> stands: `_ACTION_TYPES` (`hook_decisions.py:120`) still holds exactly nine
> kinds, `MoveStage` still addresses only core X/Y/Z, `DeniedEventQueue` and
> `FORBIDDEN_SAVED_HOOK_PARAMS` still hold the boundary, and
> `check_device_property` still does two jobs. What the first draft missed was
> that most of the mechanism it proposed **already exists for illumination**, and
> that two designs landed on either side of it (`design/49`, `design/53`) whose
> decisions this one has to respect. Both are reconciled below.
>
> **Scheduled 2026-08-15. It is no longer parked.** Three blocks — 52a, 52b, 52c
> — with their own run ledger and checklist at the bottom of this document, in
> the shape `design/48`–`design/53` use. The document's one open question, how a
> hook names its target, was settled first: **value-only actions**. `design/35`'s
> open register row points here.

---

## Finding — the safe hook boundary excludes an already-safe operation

The operator asked Microclaw to find the optimum TIRF angle, save every inspected
image, and preferably keep the angle sweep in one stack. The relevant axis was a
declared named stage, `Thorlabs ELL17/ELL20`, and ordinary parent-side calls to
`move_named_stage` successfully moved it through the requested range. The run
therefore had all the ingredients for one acquisition: a finite angle range, a
named stage with configured bounds, an image metric, and explicit operator
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

**The session's ending was omitted from the first draft and is the second
argument for this design.** At line 81 the operator dropped the stack and asked
for the routine as a script. `export_session_script` succeeded — 14 emitted calls
— and emitted *the trace*: the improvised `move_named_stage` targets, including
two fill-in points (20860, 20765) chosen after seeing results. The agent said so
at line 84, then hand-wrote `find_tirf_optimum.py` through `write_text_file`.

That is block 43h's thesis where 43h does not reach. 43h emits the program for a
*hook's* decision loop, because that loop is a rule the exporter can render; a
sweep the agent improvises parent-side has no rule, so the exporter can only
record where it went. An approved envelope makes the sweep a hook program, and
`_emit_adaptive` already emits those as programs.

This also exposes a policy mismatch. Microclaw currently treats provenance
(`saved_untrusted`) as a permanent reason to prohibit whole classes of hardware
effects. The meaningful questions are instead:

1. Did the operator approve this hook having this capability for this run?
2. Is the proposed target within the approved capability envelope?
3. Does the proposed value satisfy the target's actual configured/driver bounds?

If all three answers are yes, provenance is a reason to warn and mediate the
write, not a reason to prohibit it.

## Decision — extend the envelope mechanism that already exists

**The first draft proposed a new mechanism. It is already built, for
illumination, and this design is one more capability through it.** That is the
review's most important correction.

`configure_illumination` (`hook_decisions.py:334`) holds a per-run envelope over
an exact `(device, property)` with a ceiling, a `max_writes` budget and a
baseline; `_apply_action` (`:457`) dispatches `SetIlluminationPower` against it in
the parent, with accept/refuse records, budget accounting and stale-baseline
recovery. `_configure_hook_capabilities` (`tools.py:4905`) validates the envelope
by exact key set, refuses a pair not declared in `illumination.power_properties`,
refuses a run ceiling above the configured one, reads the initial value, renders
**one** `CONFIRM_FN` summary before the acquisition, and refuses to start if
declined. Envelopes apply to saved hooks only, never to composed ones.

Every structural requirement in the first draft — proposals not handles, parent
dispatch, one pre-run confirmation, approval that cannot widen a configured
bound, a write budget, an audit record — is that code. Reuse that validator and
dispatch seam rather than adding a parallel `hook_hardware` validator. The work
is still larger than two actions and two `configure_*` methods: pre-exposure
event coordination and the standalone adaptive runner must carry the capability
too. Two envelope systems doing almost the same thing is the defect; extending
all consumers of the existing one is not.

Add two proposal types to the saved-hook decision vocabulary:

```python
@dataclass(frozen=True)
class MoveNamedStage:
    position_um: float
    kind: str = "MoveNamedStage"


@dataclass(frozen=True)
class SetDeviceProperty:
    value: str
    kind: str = "SetDeviceProperty"
```

**The actions carry a value and no target. Settled 2026-08-15 by operator
decision** — see §"How the hook names its target", which was this document's one
open question and is now closed. The parent supplies `device` (and `property`)
from the envelope, exactly as `SetIlluminationPower` does. The class names are
unchanged; only their fields are. Anywhere below that shows a `device` on an
action or a plan entry is superseded by this.

These are proposals, never direct hardware handles. `UntrustedHookAdapter`
parses them through the closed union and the trusted parent performs the write.
Saved source still gets pixels and metadata only: do not pass it `ctrl`, `core`,
`guard`, an event queue, a setter callback, or a generic RPC object.

Before acquisition begins, `_configure_hook_capabilities` validates the envelope,
renders it for the operator, and asks once — the path `illumination_envelope`
already takes. For the TIRF case the confirmation should be concrete enough to
review:

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
pinned bytes, runner invocation, event plan, target/value envelopes, and write
budgets. It is not approval for “hardware access” generally, is not persisted in
the hook manifest, and does not carry to another run. A session grant may repeat
only the identical subject tuple; broad `kind="hook_motion"` grants without a
subject are not permitted.

The acquisition tool takes an explicit envelope rather than inferring authority
from hook source. Two new arguments sit beside `illumination_envelope`, with the
same exact-key validation and the same refusals:

```python
named_stage_envelope={
    "device": "Thorlabs ELL17/ELL20",
    "min_um": 19639,
    "max_um": 21294,
    "max_writes": 18,
    "restore": "leave",
}

property_envelope={
    "device": "...", "property": "...",
    "allowed_values": [...],      # categorical; xor the numeric pair below
    "min": ..., "max": ...,       # numeric
    "max_writes": ...,
    "restore": "leave",
}
```

One envelope per argument, matching `illumination_envelope`'s one-pair shape. A
run needing two named stages is a second design question and is out of scope
here; do not pre-build a list for it. No wildcard device, property, or value is
accepted, and a categorical envelope and a numeric one are mutually exclusive
key sets so `set(envelope) != allowed` still decides validity in one line.
`restore` is required rather than silently defaulted by the parser: callers use
`"leave"`, `"entry"`, or `{"value": ...}`. Requiring the key makes the policy
visible in the recorded invocation and confirmation even though `"leave"` is
the product default used when the agent constructs an argument.

The envelope is a tool argument, part of the acquisition plan and the export. It
is never a `hook_params` value — those are hook-controlled inputs and must never
grant authority. The existing code already enforces the important half of this:
`device` and `property` are both in `FORBIDDEN_SAVED_HOOK_PARAMS`
(`hook_manager.py:18`), so a saved hook cannot be handed its target as a
parameter. See §"How the hook names its target" below, which that constraint
makes into a real open question.

### Runtime checks: bounds are the hard stop

**There are three parent-side gates on a property write, not one, and the first
draft named only the middle one.** `set_device_property` (`tools.py:2319`) runs
`authorize_property_write` (the live authorization map), then
`guard.check_device_property` (safety config), then `guard.check_illumination`.
Which of the three an envelope may stand in for has to be answered per gate.

For every `MoveNamedStage` proposal, in this order:

1. Require the approved envelope's device to match exactly, and decrement no
   budget yet.
2. Require the finite target to be inside the approved per-run interval.
3. Call `guard.check_named_stage(device, position_um)`. This retains the
   configured physical/safety travel bounds and fails closed when the named
   stage has no bounds.
4. Call `core.set_position(device, position_um)`, wait for the device, read the
   achieved position, and record requested, achieved and error.
5. Consume one write only once dispatch is attempted; if the bridge raises,
   mark the baseline/position uncertain and abort the acquisition rather than
   blindly issuing the next relative decision.

That is exactly `move_named_stage` (`tools.py:2023`) with an envelope check
prepended, and it deliberately does **not** call `authorize_property_write` —
because the live tool does not either. Motion by label is not a raw property
write and has never been routed as one.

**That is now the mechanical reason `MoveNamedStage` must be first-class**,
stronger than the first draft's move/wait/read-back argument. Under schema 3
`authorize_property_write` refuses **every** property on a bounded stage device
unless the exact pair holds a `built_in_typed_capability` entry on a raw-write
path (`authorization.py:1530`, design/49). `Thorlabs ELL17/ELL20` is one. So a
`SetDeviceProperty` aimed at its `Position (um)` **refuses, and should** — that
is the protection 48a's gate measured closed. Without the first-class action
there is no legal route to the TIRF axis at all.

For every `SetDeviceProperty` proposal:

1. Require an approved entry for the exact pair and value envelope.
2. Call `authorize_property_write` **unchanged**. The envelope does not stand in
   for the authorization map. A hook-dispatched write claims the
   `generic-property` path like any other raw write, and a pair the map excludes
   stays excluded; the operator changes that through the safety-configuration
   workflow, as with bounds.
3. Route through the same capability-aware **bounds** checks as the public
   property tool: typed-actuator range/unit validation, stage bounds, exposure
   bounds, illumination bounds, or the device's allowed categorical values.
   Do not reproduce those checks in the adapter.
4. Apply, wait when the device exposes a wait, then verify — see the timing rule
   below — and audit the result.
5. Call `ctrl.refresh_gui()` after the write, as `set_device_property` does. This
   preserves the public writer's EMU-specific repaint behavior. It is not a new
   claim that every Micro-Manager write needs an explicit repaint; design/35
   records why that broader claim was dropped.

Step 3 requires separating `check_device_property`'s two present jobs
(`safety.py:1002`): numeric/categorical validation, and `check_property`'s
allowlist/denylist policy. For this path the exact, audited envelope **is the
safety-config authorization** and replaces the allow/deny decision; the
bounds/type validator stays mandatory. Otherwise a historical categorical
exclusion vetoes the exact approved action even though the value is inside the
device's domain — the defect under a different name. The seam is narrower than
the first draft implied: the function already lets typed and illumination pairs
bypass the categorical allowlist while keeping the denylist, so this extends a
distinction it draws rather than inventing one. The ordinary
`set_device_property` path is unchanged.

#### Verify the frame's actions as a set, not per write

Design/53, closed the day after this document was written, moved
`_verify_property` out of the channel executor's write loop into one pass after
the last write (`authorization.py:1774`): a preset's values are simultaneously
true and only at the end, so M5's `Normal Mode` sets an `Exposure` that is only
representable once a later `ScanMode` lands.

Per-write apply-verify-abort, which the first draft prescribed, reproduces that
the moment a hook proposes an interdependent pair in one frame's action set — and
mode-then-value is the natural way to author one. So: apply in order, waiting per
write, and stop immediately if the write **raises** (the device rejected it —
knowable now); then verify the whole action set in one pass, with
`_verify_property`'s semantics (`Float` numerically, for MM's `"10"` →
`"10.0000"` reformatting; everything else exactly); classify a mismatch there as
a failed action per §Failure semantics.

Design/53's distinction carries over verbatim: *the device rejected this write*
is knowable per write, *this value is not consistent yet* only at the end.

#### The envelope bounds the request, not the achievement

The session's first move requested 21294 µm — the top of the interval this
document's own confirmation box displays — and **achieved 21299**, the ELL being
a coarse stage (history line 29).

So the envelope is checked against the **requested** target. The achieved
position is read, recorded, reported and stamped into the frame's metadata, and
is never grounds for a retroactive refusal — a rule refusing on achieved position
would fail on write one of this design's own worked example, and the write has
already happened by the time the value is readable. `check_named_stage` behaves
this way today; nothing changes.

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
- The **safety-config** allowlist/denylist does not add a second authorization
  vote after the operator approves the exact hook envelope. It may be shown as a
  warning; the target's bounds/type/domain remain a hard property-level gate.
- The **live authorization map** does. `authorize_property_write` runs unchanged
  and an excluded or unclassified pair refuses regardless of approval, for the
  reason design/49 gave: the map's exclusions encode hardware semantics nobody
  has established yet, and an approval dialog is not where that gets settled.
  The two are different objects and the first draft conflated them.
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

### How the hook names its target — settled, value-only

**Decided 2026-08-15, before any block was assigned: the actions carry only a
value.** `MoveNamedStage(position_um)` and `SetDeviceProperty(value)` are applied
to the single target the envelope names, exactly as `SetIlluminationPower` is.

The alternative was to keep `device` (and `property`) on the action and check it
against the envelope. It was rejected: `device` and `property` are both in
`FORBIDDEN_SAVED_HOOK_PARAMS` (`hook_manager.py:18`) and the envelope never
reaches hook source, so a hook could only hardcode the string — reopening for a
target name the exact boundary that list exists to close, to buy a two-device
workflow nobody has asked for. The value-only shape rules out a hook aiming at an
undeclared device *by construction* rather than by check, needs no new refusal
record, and is the shape already rig-gated through block 7b.

The cost is stated and accepted: one target per run per kind, so no hook
coordinating two devices in one acquisition. Take the device-bearing shape only
if a concrete two-device workflow turns up, and treat it as a new design.

### Timing: use the pre-hardware callback for a per-frame target

`analyze_frame` runs after the image exists, so an action returned for frame N
can affect only frame N+1. That is useful for feedback but awkward for a planned
angle stack and easy to index incorrectly. Use one parent-owned, event-indexed
action coordinator for both planned and adaptive runs. Do not add a second saved
hook callback with different ordering semantics.

There is a natural home for it: pycro-manager's `pre_hardware_hook_fn` slot is
real and documented in `hook_docs.py:120`, and Microclaw currently wires only
`post_hardware_hook_fn` (`hook_decisions.py:831`, `tools.py:2679`) and that only
for pre-coded registry hooks. The trusted adapter owns this callback. Saved code
still never receives the event queue or a hardware callback.

The coordinator's contract is:

1. Give every event a monotonically increasing `hook_event_index` before it is
   yielded to pycro-manager. Predetermined plans install the action set for each
   index before acquisition; the first event therefore has an explicit seed.

   **The index is a plain event key and must never land in `event["axes"]`.**
   NDTiff keys each frame by its exact axis set and every reader enumerates the
   Cartesian product — `export_dataset_as_tiff` does exactly this. A sparse axis
   already cost a measured defect: on M5, 2026-08-11, a `refocus` axis dense on
   only some frames exported a 4-frame dataset as one real frame and two zeros,
   which is why `configure_adaptive` stamps `axes.refocus = 0` on every event.
   An index that is *unique per event* is worse than sparse — its cardinality is
   the event count, so the product is the whole dataset squared.
2. `pre_hardware_hook_fn` consumes exactly the action set bearing the current
   event's index, applies and verifies it, and stamps requested and achieved state
   into that event. A missing, duplicate or wrongly indexed set aborts before
   exposure. It never guesses by callback arrival order.
3. For an adaptive run, image processing for event N may publish at most one
   immutable action set tagged N+1 into a parent-owned one-slot handoff. Thus
   pre-hardware never blocks waiting for analysis and there is no race between
   image processing and hardware setup.

   **That handoff is `candidates`, not a structure beside it.**
   `_survey_event_stream` (`tools.py:5215`) already has this shape for
   design/24's reason: only `survey_events[0]` is pre-dispatched and every later
   event exists because the hook submitted it after scoring the frame that just
   arrived. The trusted adapter attaches the index and the next-frame hardware
   action set to that candidate event, and the draining side enforces one
   candidate per index. A second queue feeding the same generator is a layer,
   not a mechanism.

   A `HookResult` may also contain actions whose subject is the frame that just
   completed. Parse the complete result first, then partition it exactly once:

   - `EmitArtifact` and `DiscardFrame` apply to frame N and dispatch immediately;
   - exactly one `ContinueSurvey` or `AcquireAt` selects event N+1;
   - `RequestAutofocus` selects event N *again* — see below;
   - `MoveNamedStage` and `SetDeviceProperty` form one ordered pre-exposure
     hardware action set attached to that selected event; and
   - `StopSurvey` selects no next event and is incompatible with a next-frame
     hardware action.

   A result containing next-frame hardware actions but zero or more than one
   next-event selector is malformed. Current-frame actions are never deferred to
   N+1, and next-frame hardware actions are never dispatched from
   `image_process_fn`. This partition also preserves set-level property
   verification: the ordered hardware subset is the set applied and then verified
   by `pre_hardware_hook_fn`.

   **The selector rule is the adaptive runner's, and only its.** A fixed-plan
   runner (`self._context is None`) yields its complete event list at socket
   speed, so an action returned after frame N cannot reliably reach event N+1
   before its pre-hardware callback. Fixed runs therefore accept hardware actions
   only from the declarative event-associated plan installed before acquisition;
   `pre_hardware_hook_fn` consumes those preinstalled sets by index, including an
   explicit set for frame zero. `ContinueSurvey` remains its documented no-op
   (`hook_decisions.py:521`), but it does not turn an analysis-produced hardware
   action into authority over the next event. Such an action is refused as
   unsupported by the fixed runner and never written. This is the TIRF gate's
   limb 1 shape: the saved hook scores the frames, while the acquisition's
   recorded action plan supplies all 18 predetermined moves.

   **`RequestAutofocus` is the one selector whose next event is N, not N+1.** It
   queues a re-exposure of the same tile with `axes.refocus = 1`
   (`hook_decisions.py:589`). Detect it while partitioning the complete result,
   before dispatching any next-frame hardware action: when it is the sole
   selector, queue the refocused tile and refuse every paired hardware action
   with *"not dispatched until the refocused tile is judged"*, regardless of
   their order in `HookResult.actions`. A refocus re-exposes a tile the hook has
   not yet judged, so attaching a new hardware state to it is premature. Pairing
   `RequestAutofocus` with another selector is malformed under the ordinary
   selector-cardinality rule.

   **A malformed partition is an analysis defect, not a hardware failure.** It
   records and refuses; it does **not** take §Failure semantics' abort path. The
   distinguishing fact is that nothing was written to hardware, so no frame is
   mislabeled — and `image_process_fn`'s existing malformed-parse branch already
   rules this way, on the ground that an analysis defect must not become an
   acquisition abort that hides the dataset from the caller. In an adaptive run,
   call `progress.done_early()` before `progress.image_done()` after recording
   the refusal. No event is queued, the stream drains immediately, and the result
   reports a refused early stop — neither an acquisition abort nor a watchdog
   stall.

   The existing `max_idle_s` watchdog keeps its present meaning: on expiry the
   generator calls `note_stalled` and **returns**, ending the stream. It does
   not, and must not, release an event whose action set never arrived — an
   unindexed exposure after a stall is precisely what this coordinator exists to
   prevent.
4. Once the final authorized event has been yielded, the handoff is closed. A
   later proposal is refused and the run is reported as aborted; it is never
   applied as an exit-side hardware mutation.

The target must settle before its event's exposure, and the achieved named-stage
position must be stamped into that event's metadata/log. The same coordinator
and indexing rules must be inlined into the standalone adaptive runner.

### Fixed-run scope: `run_timelapse` and `run_zstack` only

**This block ships the new named-stage/property capability on the two fixed
runners whose export already inlines a saved hook, and leaves multiposition and
tile without these three new arguments.** The decision is forced by the exporter
and should not be discovered during implementation.

`hook_strategy` appears on exactly five public signatures: `run_timelapse`,
`run_zstack`, `run_multiposition_acquisition`, `run_tile_acquisition` and
`run_adaptive_survey`. (`run_multiposition_with_autofocus` is a deprecated
wrapper forwarding into multiposition with a hardcoded precoded hook and no plan
of its own — nothing is owed there.) But `_emit_multiposition` (`tools.py:154`)
already raises `CannotEmit` for **any saved hook** — *"the fixed-plan exporter
cannot inline its adapter and observation log"* — and again for a precoded hook
that is not `_observation_only`; `_emit_tile` forwards into that same function
(`tools.py:307`) and inherits it. So a saved-hook run through either is
unexportable today.

Making those two carry the new capability means teaching the fixed-plan exporter
to inline a saved hook's adapter and observation log with `inspect.getsource` —
the work `_emit_adaptive` exists to do, and the exporter's stated reason for
declining. That roughly doubles this block's export surface and is plausibly its
own block. It is also unnecessary here: **limb 1 is a timelapse**, so the
motivating case is covered by the two routes that can emit, and the block's
export claim stays provable end to end on the route the evidence actually used.

Therefore: `run_timelapse` and `run_zstack` accept
`named_stage_envelope`, `property_envelope` and `hook_action_plan`, and must emit
all three. `run_adaptive_survey` accepts the two envelopes but not the fixed
plan. `run_multiposition_acquisition` and `run_tile_acquisition` accept none of
these three new arguments, and their existing saved-hook `CannotEmit` stands
unchanged — which is the "or refuse" limb of §Export, already satisfied without
work. Lifting the exporter refusal is a separate proposal; if it is ever taken,
the new arguments and their gate rows extend to those two routes together.

**Both retain their present hook behavior, but the two are not equivalent and
must not be written as a pair.** `run_multiposition_acquisition` accepts
`illumination_envelope` and `artifact_limits` (`tools.py:4369`);
`run_tile_acquisition` accepts **neither** (`tools.py:4567`) and forwards only
`hook_strategy`, `hook_params` and `log_path` into multiposition
(`tools.py:4623`). So multiposition retains illumination, artifacts and frame
discard; tile retains frame discard, which needs no configuration. Anything
asserting an unchanged tile illumination or artifact behavior is asserting a
capability that does not exist.

> Noticed while drawing that boundary, **pre-existing and deliberately not fixed
> here**: because `_configure_hook_capabilities` refuses an artifact-emitting
> hook that was given no `artifact_limits`, and tile can neither accept nor
> forward that argument, a tile run with an artifact-emitting saved hook cannot
> run at all — and the refusal names a remedy the caller has no way to reach.
> Filed in `design/35`'s open register rather than grown into this block.

For a predetermined sweep, the fixed-run acquisition tool takes a recorded
`hook_action_plan` beside the envelopes. It is declarative, generated and
validated before the run, and has exactly one entry for every event index (an
empty action list is explicit). It is not a `hook_params` value and the saved
hook cannot mutate it. Because it is a tool argument, actions use the same
JSON-safe discriminated dictionaries accepted by `parse_action`, never Python
dataclass instances:

```python
[
    {
        "hook_event_index": 0,
        "actions": [{"kind": "MoveNamedStage", "position_um": 21294}],
    },
    ...,
    {
        "hook_event_index": 17,
        "actions": [{"kind": "MoveNamedStage", "position_um": 19639}],
    },
]
```

`hook_event_index`, not a time/frame axis, is the key: `frame=0` legitimately
occurs at multiple positions, channels or Z planes, while the coordinator index
is unique across the generated event list. Validation requires exactly the set
`0..len(events)-1`, with no missing or duplicate entry, after event generation
and before confirmation. Plan entries carry no `device`: the envelope names the
target once, per §"How the hook names its target".

Adaptive feedback proposes the next target from `analyze_frame` through the
indexed handoff above. Never silently apply a post-image proposal after the
acquisition has completed.

## Failure semantics and audit

Every proposal receives an `accepted`, `refused`, or `failed` hook-action record
with the action, frame identity, reason, and read-back — the `_accept`/`_refuse`/
`_record` machinery the illumination path already writes.

**Abort on a refused or failed motion or property action.** The next image would
be mislabeled as having the requested state. Return the actual dataset and log
paths, say how many frames were exposed, and report the last known hardware
state. This follows the existing mid-acquisition failure rule.

**Do not copy the illumination path's present continue-on-failure behavior into
the pre-exposure coordinator.** `SetIlluminationPower` currently records a
failed write, marks its baseline stale and continues (`hook_decisions.py:457`).
A bridge exception does not prove that no part of the write landed, so the power
for the next exposure is unknown until a read-back succeeds. Motion, property
and illumination actions dispatched before an exposure therefore share one
rule: a refusal, write exception or verification failure aborts before that
exposure. A future change may make illumination recoverable by re-reading and
revalidating the actual value before exposure, but merely calling the result a
weaker experiment is not enough. **Known state and correct labeling are the
abort criteria, not hazard alone.**

Budget accounting diverges too. Illumination decrements only on an *increasing*
write, being a dose ratchet; a motion or property budget decrements on **every**
attempted dispatch, being a count of writes.

At normal completion, restoration is explicit in the envelope's required
`restore` key:

- `restore: "leave"` — **the default.** No write on exit; the exit report names
  the entry value and the last value written.
- `restore: "entry"` reads and records the initial value before acquisition and
  restores it through the same guard/write/read-back path.
- `restore: {"value": ...}` uses an approved bounded terminal value.

**The default is `"leave"` because of design/38 F9**, which the first draft did
not cite. F9 reversed design/14 §3 on an operator ruling that silent state
mutation on exit is the larger hazard — it breaks the move-in/move-out workflow
and can leave a rig reporting a successful acquisition while emitting nothing —
so session exit now writes nothing and instead *names* every declared property
that differs from its off value.

`"entry"` and `{"value": ...}` do not violate that ruling: they are declared in
the envelope and shown in the confirmation, so nothing is silent. But F9 decides
which is the **default**, and it is the one that writes nothing. Whichever is
chosen, `max_writes` counts all attempted dispatches including a non-`leave`
restoration. Planning reserves that final write: a fixed plan that would consume
it is refused before acquisition, and an adaptive run stops accepting ordinary
actions when only the reservation remains. The configured terminal value and an
entry value used for restoration must themselves pass the envelope and current
guard checks before approval. If restoration fails, report it loudly and never
claim the entry state was restored.

## Documentation and model behavior

Update `get_hook_documentation`, action imports emitted into saved-hook examples,
`describe_hook`, and the runner schemas together.

**The advice splits by runner, and saying only the adaptive half would reproduce
this document's own failure in mirror image** — an agent confidently offering a
hook-proposed move on a timelapse, and meeting a refusal it was told could not
happen. The docs should say:

> Saved hooks cannot access hardware directly. Where the hardware state for a
> frame comes from depends on the run:
>
> - **Predetermined runs** (`run_timelapse`, `run_zstack`) take every hardware
>   action from `hook_action_plan`, supplied and bounded on the acquisition call
>   and approved before the run. Their events are all dispatched before the first
>   image is analyzed, so a hardware action returned from `analyze_frame` cannot
>   reach the next frame in time and is refused as unsupported. The hook scores
>   frames; the plan moves hardware.
> - **Hooked multiposition runs** retain their existing capabilities — an
>   illumination envelope, an artifact budget and frame discard — and **hooked
>   tile runs** retain frame discard, which needs no configuration. Neither
>   accepts a named-stage/property envelope or `hook_action_plan`, and both
>   refuse the new named-stage/property proposals like any other fixed run. A
>   workflow needing that bounded hardware control over a grid uses
>   `run_adaptive_survey`.
> - **Adaptive runs** (`run_adaptive_survey`) may propose named-stage or
>   device-property changes from `analyze_frame`, applied to the event the same
>   result selects, within the approved envelope.
>
> In both, the trusted parent checks every action and performs the write.

Remove the current categorical advice that arbitrary named-stage/property motion
is impossible. When a requested workflow needs such a write, the agent should
offer the right one of those two shapes directly, show its target/range/write
count, and ask once. It should not repeatedly redesign the acquisition around the
missing action or pressure the operator to surrender requested output structure.

## Export

An exported acquisition must reproduce the same mediation, not turn approved
hook proposals into raw `core.set_property` calls. Inline the action types,
parser, envelope validator, guards, write budgets, verification, audit, and
restoration logic and event-indexed coordinator used live — with
`inspect.getsource`, never re-written in the emitter, for the reason CLAUDE.md
gives about the adaptive loop: a hand-copied copy that drifts reintroduces the
refusals silently. The exported script prints the approved envelope and requires
confirmation before connecting/starting unless the user explicitly requests a
non-interactive artifact and accepts that fact in the export dialog. The pinned
hook hash and envelope are embedded in the script.

The actions themselves do **not** get `@emits` decorators. They are dataclasses
parsed and dispatched inside the acquisition, not independently recorded tools.
Extend `_emit_adaptive` and its inlined runner so the acquisition tool's existing
renderer carries both new envelope arguments **and `hook_action_plan`**, installs
the same coordinator and dispatches the same action classes. The plan is not an
optional extra: a hooked `run_timelapse` routes to `_emit_adaptive` whenever
`hook_strategy` is set, so limb 1 exports through this emitter, and the plan is
the argument that fully determines its hardware behavior. An export carrying the
envelopes but dropping the plan yields a script that moves nothing and still
looks complete — the failure mode is a silent no-op, not a refusal. It is also
the easiest of the three to emit: a validated list of literals, with none of the
rig-configured conversions that make illumination unemittable. This is real new
work: `_emit_adaptive`
currently raises `CannotEmit` whenever `illumination_envelope` is present
(`tools.py:836`), so illumination is precedent for the live capability seam, not
evidence that capability export is already solved. A capability is finished only
when an exported acquisition containing it has no `# NOT EMITTED` or
`raise RuntimeError` refusal and reproduces the program rather than its trace.

All three new arguments must be present in the schemas, recorded parameters and
emitter for **both** fixed routes that accept them — `run_timelapse` and
`run_zstack`, per §Fixed-run scope. `run_adaptive_survey` carries the two
envelopes through its live and emitted adaptive coordinator but has no fixed
plan. Multiposition and tile accept none of the three and keep their existing
saved-hook `CannotEmit`, which is a refusal rather than a plausible script and is
therefore already correct. Silently dropping an accepted argument is never an
allowed implementation boundary; that is the silent-no-op failure above.

**Leaving illumination's refusal in place is a decision, and it is this one:
leave it.** The end state is two capability envelopes that export and one that
does not, sharing a validator and a dispatch seam — which looks like an
inconsistency and is not. The refusal's stated reason is specific to
illumination: the envelope's percent is converted through rig-configured
raw-value mappings that are not recorded as exportable literals. Named-stage
position is µm on both sides of the bridge, and a property value is the string
the device takes, so neither new envelope has that problem and neither inherits
the refusal. Do not widen this block to fix illumination export; do not narrow
the new envelopes to match it either. (Same origin, the small asymmetry in the
key sets: `restore` is required on the new envelopes and absent from
illumination's, whose terminal state is already governed by design/38 F9.)

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
- **Build a second envelope mechanism** (`hook_hardware={...}` with its own
  validator, confirmation and dispatch), which is what the first draft proposed.
  `configure_illumination` + `_configure_hook_capabilities` already is that
  mechanism, rig-gated through block 7b. Two envelope systems that do almost the
  same thing is the defect CLAUDE.md names, and the second one would be the copy
  that drifts.
- **Let the envelope stand in for `authorize_property_write`.** It would reopen
  the raw route to a bounded stage device's position property — precisely what
  design/49 took care not to do and what 48a's gate measured closed. The map's
  exclusions encode unestablished hardware semantics; an approval dialog is not
  where those get settled.
- **Verify each write immediately, as the first draft prescribed.** Design/53
  measured that failing on M5 four days after this document was written. Keep the
  per-write *exception* stop; move the *verification* to the end of the action
  set.

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
- **`SetDeviceProperty` on a bounded stage device's position property refuses**
  at `authorize_property_write`, with design/49's message naming
  `move_named_stage`. This is a pass, not a gap — it is the test that the
  envelope did not become a route around the map.
- **Two actions in one frame whose validity is interdependent both apply, then
  verify.** Build the design/53 shape: a fake whose representable set for one
  property depends on another, ordered mode-second so per-write verification
  would fail. Verify the reverse order too — the failure and its rollback are
  both order-sensitive and design/53's amendment exists because of it.
- **An achieved position outside the approved interval, from a requested one
  inside it,** is recorded and reported and does **not** refuse. Use the rig's
  real numbers: request 21294 against a ceiling of 21294, achieve 21299.
- Envelope arguments are refused for composed hooks and for non-saved hooks,
  matching `illumination_envelope`'s existing behavior in
  `_configure_hook_capabilities`.
- The acquisition tools' adaptive emitters accept both new envelopes and inline
  the action coordinator/dispatcher; an exported session that dispatched either
  action contains no `# NOT EMITTED` and no `raise RuntimeError`.
- A write exception on a pre-exposure illumination action aborts before exposure
  unless a fresh read-back establishes and validates the actual power; exercise
  a fake write that mutates and then raises.
- Planned and adaptive runs attach action sets by `hook_event_index`, not callback
  arrival order. Delay image processing for event N and prove event N+1 is not
  yielded or exposed with a missing/stale action set. Let `max_idle_s` expire and
  prove the stream ends with `note_stalled` rather than releasing that event.
- A mixed result containing `EmitArtifact`, `MoveNamedStage` and
  `ContinueSurvey` writes the artifact against frame N, queues exactly one event
  N+1, and performs the move only in N+1's pre-hardware callback. Two next-event
  selectors beside a next-frame hardware action queue nothing and produce a
  refusal record. The adapter calls `done_early()` before `image_done()`; the run
  is neither reported as aborted nor allowed to age into `note_stalled`, since no
  hardware was written.
- **A fixed-plan runner never takes next-frame hardware actions from
  `analyze_frame`.** Install frame 0 and frame 1 moves through
  `hook_action_plan`, delay frame 0 image processing until after frame 1's
  pre-hardware callback, and prove both planned moves still land correctly. Then
  return a different `MoveNamedStage` from frame 0 analysis and prove it is
  refused without changing frame 1.
- Timelapse and Z-stack schemas accept both new envelopes and the same JSON
  `hook_action_plan` shape. Export one planned move through each and prove the
  script contains the envelopes and indexed action; neither may omit an argument
  or turn the acquisition into a no-op. Adaptive survey accepts the envelopes
  and rejects `hook_action_plan`.
- Multiposition and tile **reject** `named_stage_envelope`, `property_envelope`
  and `hook_action_plan` as unknown arguments, and a saved-hook run through
  either still raises the existing `CannotEmit` rather than emitting a script
  without its behavior. Assert what each actually has: multiposition's
  `illumination_envelope`, `artifact_limits` and discard unchanged, tile's
  discard unchanged — and do **not** write a tile illumination or artifact
  assertion, which would pass vacuously against an argument the tool does not
  accept. This is the §Fixed-run scope boundary, and it is a pass.
- Test both `RequestAutofocus, MoveNamedStage` and the reverse action order. Each
  queues the refocused tile and refuses the move with the existing deferral
  reason; neither performs the move. Pairing `RequestAutofocus` with
  `ContinueSurvey` is malformed and queues neither event.
- **`hook_event_index` is absent from `event["axes"]` on every yielded event**,
  and a dataset written by an indexed run exports through `export_dataset_as_tiff`
  with the frame count it acquired. Cheap, and it pins the failure mode measured
  on M5 on 2026-08-11.

### TIRF acceptance gate

Two limbs. **Both are required** — the first draft specified only the first, and
the session this design comes from is the second.

**Limb 1 — the declarative sweep.** On the same rig, create an 18-frame timelapse
with one named-stage target per frame for `Thorlabs ELL17/ELL20`, within its
reviewed `named_stages` bounds. Approve the displayed `19639–21294 µm`, 18-write
envelope once.

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

**Limb 2 — the adaptive refinement, which is what actually happened.** The
session was not a fixed 18-point sweep: the agent ran a coarse pass, then chose
20860 and 20765 **after seeing the intermediate results**, and the optimum was at
one of those (history lines 68–76). A gate testing only limb 1 passes while
leaving this design's motivating behavior unexercised.

So: a hook proposing its next target from `analyze_frame` — coarse pass, then
refinement inside the bracketing pair — in one envelope and one dataset. Pass
requires:

- the refinement targets are chosen by the hook, not present in the seed plan;
- every proposal is checked against the same interval and budget, and the run
  ends when the budget is exhausted rather than by silently continuing;
- a proposal for the frame after the last authorized one aborts rather than
  exposing; and
- **export emits the program, not the trace** — the exported script contains the
  hook's rule and the decision loop, and re-run on the rig it may legitimately
  choose *different* targets, as 43n's M5 B4 limb demonstrated for hits. An
  export that reproduces this run's exact target list is a fail, and is the
  defect measured on 2026-08-14.

The product criterion is not merely “the action exists.” Repeating the original
request should lead directly to a bounded capability summary and one approval,
then complete the single-stack sweep. The agent must no longer say that a saved
hook cannot move a named stage or talk itself into separate snaps when the move
is approved and in bounds.

---

## Blocks

Three, run in order. The split is by **capability**, not by layer: each block
ships one thing an operator can run *and* the export of that thing, because a
capability is not finished until it can appear in an exported script
(`CLAUDE.md`). A "mechanism now, export later" split was considered and rejected
for that reason.

Each block's first two rig limbs come from §"Evidence and gates" above; nothing
here replaces that section, it only says which block owes which part of it.

### 52a — the declarative named-stage sweep

Design: §Decision (through the `MoveNamedStage` half of §"Runtime checks"),
§"The envelope bounds the request, not the achievement", §"Approval and bounds
are different controls", §Timing (the predetermined-plan half), §"Fixed-run
scope", §"Failure semantics and audit", §Export (the fixed-plan half),
§"Documentation and model behavior" (the predetermined-runs bullet).

Files: `microclaw/hook_decisions.py` (`_ACTION_TYPES`, `parse_action`,
`configure_illumination`'s sibling, `_dispatch`, the new pre-hardware
coordinator), `microclaw/tools.py` (`_configure_hook_capabilities:4905`,
`run_timelapse:2890`, `run_zstack:2742`, `_emit_adaptive:827`,
`_acquire_with_hooks:2636`), `microclaw/hook_docs.py`,
`tests/test_hook_decisions.py`, `tests/test_session_script_export.py`.

**Rig gate 52a (M5).** §"TIRF acceptance gate" limb 1 in full, on the rig that
carries `Thorlabs ELL17/ELL20`. All seven pass criteria, including the
out-of-bounds second run and the exported script.

Step-10 design gate: correct `hook_docs.py`'s categorical claim that a saved hook
cannot reach a named stage, and tick the `design/35` register row down to what
52b and 52c still owe.

### 52b — the general bounded property

Design: the `SetDeviceProperty` half of §"Runtime checks", §"Verify the frame's
actions as a set, not per write", the authorization-map bullets of §"Approval and
bounds are different controls", and the property limbs of §"Evidence and gates".

Files: `microclaw/safety.py` (`check_device_property:1002`, `check_property:974`),
`microclaw/authorization.py` (`authorize_property_write`, `_verify_property`),
`microclaw/hook_decisions.py`, `microclaw/tools.py`,
`tests/test_hook_decisions.py`, `tests/test_safety_*.py`.

**Rig gate 52b (M5).** A property the operator names on the day — a categorical
one (an EMU-named filter or two-state device) and, if one exists in the reviewed
config, a bounded numeric one. The runbook records which pair was used; do not
write the pair into `microclaw/`. Two limbs are mandatory regardless of the pair
chosen: a `SetDeviceProperty` aimed at `Thorlabs ELL17/ELL20`'s position property
**refuses** at `authorize_property_write` with design/49's message naming
`move_named_stage` — that is a pass — and an interdependent pair applied in one
frame verifies as a set, not per write.

Step-10 design gate: record in `design/33-authorization-map.md` that an approved
hook envelope replaces the safety-config allow/deny decision and **not** the
authorization map, with the reason design/49 gave.

### 52c — the adaptive refinement

Design: §Timing points 2–4 (the adaptive half, the partition, `RequestAutofocus`,
the malformed-partition rule, the watchdog), §Export's program-not-trace
requirement, §"Documentation and model behavior" (the adaptive bullet).

Files: `microclaw/hook_decisions.py` (`image_process_fn:703`, `_dispatch`),
`microclaw/tools.py` (`_survey_event_stream:5215`, `run_adaptive_survey:5541`,
`_emit_adaptive`), `tests/test_adaptive_survey.py`,
`tests/test_session_script_export.py`.

**Rig gate 52c (M5).** §"TIRF acceptance gate" limb 2 in full — coarse pass then
hook-chosen refinement, one envelope, one dataset — including the export limb,
where a script reproducing this run's exact target list is a **fail**.

Step-10 design gate: replace this document's §Timing prose with what the rig
measured, and close the `design/35` register row.

## Run ledger

| Block | Branch | Start commit | Implementer | Rig gate | Merged | Design reconciled |
|---|---|---|---|---|---|---|
| coordination | `design52/reconcile-decision` | `bdffb14` | coordinator | n/a | merged `d97d256` | n/a |
| coordination | ~~`design52/checklist`~~ | `d97d256` | coordinator | n/a | merged `f872a0c` | n/a |
| 52a | `design52/block-52a` | `f872a0c` | **not yet assigned** | | | |
| 52b | `design52/block-52b` | | | | | |
| 52c | `design52/block-52c` | | | | | |

## Checklist

### How to use this checklist

**The process is `CLAUDE.md` §"The block workflow" and it is authoritative.**
This section is *what* is owed, not *how* the block runs; if the two disagree
about process, `CLAUDE.md` wins and this section gets corrected.

- The coordinator alone edits this section and the ledger, on a branch, and
  commits before assigning — a worktree sees committed history, not an editor
  buffer.
- The implementer works in its own git worktree, commits, and reports. It never
  merges, and it never edits this section.
- A row is ticked when the coordinator has verified it, not when an agent reports
  it. Re-run the suite; read the diff.
- **The blocks run in order, one at a time.** 52b and 52c both extend the same
  `_dispatch` and the same acquisition signatures 52a creates; running them
  concurrently in two worktrees would conflict on every file that matters.

### State at 2026-08-15 — the live note

Checked against the repository rather than assumed: working tree clean,
`git log --oneline origin/main..main` empty, `main` at `d97d256`, and on `origin`
besides `main` only `design34/focus-system-authorization` (6a),
`florian/setup-claude-workflow` and `port-to-jpype-acqj` — **no open block
branch.** One worktree, this one. Suite at `d97d256`, macOS: **1812 passed / 99
skipped / 3 warnings**, coordinator-measured rather than carried over.

- **design/52 is scheduled and no longer parked.** Three blocks. **52a's branch
  `design52/block-52a` exists at `f872a0c` and is pushed, with a worktree at
  `../microclaw-52a`; no implementer has been assigned to it yet.** 52b and 52c
  are not started.
- **52a branches from the merge that carries this checklist**, so the runner's
  tree holds the spec it is being held to. Both the 50b and 51a runners reported
  their design file absent from the start commit they were given; this removes
  that papercut. The ledger row recording that start commit necessarily lands
  after it — do not chase the tip.
- **The open question is settled: value-only actions** (§"How the hook names its
  target"). Operator decision, 2026-08-15, before assignment. The stubs and the
  `hook_action_plan` example in this document were corrected to match; anything
  elsewhere showing a `device` on an action is stale.
- **The illumination export refusal stays** (`tools.py:836`). §Export says why,
  and it is a decision, not an oversight. Do not widen a block to fix it, and do
  not narrow the new envelopes to match it.
- **Multiposition and tile are out of scope by decision**, not by omission —
  §"Fixed-run scope". Their existing saved-hook `CannotEmit` is the "or refuse"
  limb and is already satisfied. Do not write a tile illumination or artifact
  assertion: those arguments do not exist and the assertion would pass vacuously.
- **The tile artifact-budget dead end is not this design's** and is not fixed
  here. It sits in `design/35`'s open register where it was filed.

### 52a — the declarative named-stage sweep

**Implementation**

- [ ] `MoveNamedStage(position_um)` joins `_ACTION_TYPES`; `parse_action` accepts
      the dataclass and the dict form and refuses unknown fields, booleans,
      NaN/infinity and non-finite values. No `device` field — the envelope names
      the target.
- [ ] `named_stage_envelope` is validated in `_configure_hook_capabilities`
      beside `illumination_envelope`, by exact key set
      `{device, min_um, max_um, max_writes, restore}`. **Extend that function; do
      not add a second validator, registry or guard pass.**
- [ ] Envelopes apply to saved hooks only: composed and non-saved hooks refuse,
      matching `illumination_envelope`'s existing behaviour.
- [ ] One `CONFIRM_FN` summary before `Acquisition(...)`, folded with the
      illumination and dose confirmations rather than added beside them. Declining
      performs no acquisition and no write. Nothing confirms from the hook thread.
- [ ] Dispatch is `move_named_stage`'s sequence (`tools.py:2023`) with the
      envelope check prepended: envelope device, envelope interval,
      `guard.check_named_stage`, `set_position`, `wait_for_device`, read back,
      record requested/achieved/error. It **does not** call
      `authorize_property_write`, because the live tool does not either.
- [ ] The budget decrements on **every attempted dispatch**. This is a count of
      writes, not illumination's increase-only ratchet — do not copy that branch.
- [ ] A refused or failed motion **aborts before the next exposure** and returns
      the dataset path, log path, frames exposed and last known hardware state.
      Do not copy illumination's continue-on-failure behaviour.
- [ ] `restore` is required, not defaulted by the parser. `"leave"` writes
      nothing on exit and names entry and last value (design/38 F9);
      `"entry"`/`{"value": ...}` go through the same guard/write/read-back path,
      pass the envelope before approval, and **reserve a write** from
      `max_writes` — a plan that would consume the reservation is refused before
      acquisition. A failed restoration is reported loudly and never called
      success.
- [ ] `hook_event_index` is assigned monotonically before each event is yielded
      and is a **plain event key**. It must never appear in `event["axes"]` — an
      index unique per event makes the NDTiff Cartesian product the dataset
      squared, which is the M5 2026-08-11 sparse-axis defect made worse.
- [ ] `pre_hardware_hook_fn` is wired by the trusted adapter and consumes exactly
      the action set bearing the current event's index, applies it, verifies, and
      stamps requested and achieved state into that event. Missing, duplicate or
      wrongly indexed aborts **before** exposure. It never guesses from callback
      arrival order. Saved code still receives no queue, no callback, no `ctrl`,
      no `core`, no `guard`, no setter.
- [ ] `hook_action_plan` is a tool argument, never a `hook_params` value, using
      the JSON dict forms `parse_action` accepts. Validation requires exactly the
      index set `0..len(events)-1` — no missing, no duplicate — after event
      generation and before confirmation. An empty action list is explicit.
- [ ] A fixed-plan runner refuses a hardware action returned from `analyze_frame`
      as unsupported and writes nothing; `ContinueSurvey` keeps its documented
      no-op.
- [ ] `run_timelapse` and `run_zstack` accept `named_stage_envelope` and
      `hook_action_plan` and pass every argument through. **The hookless route is
      untouched**: no `hook_strategy` still means `_emit_acquisition`.
- [ ] `run_multiposition_acquisition` and `run_tile_acquisition` accept neither
      argument, and their existing saved-hook `CannotEmit` stands unchanged.
- [ ] `_emit_adaptive` carries both new arguments and installs the same
      coordinator and action class, inlined with `inspect.getsource` and **never
      re-written in the emitter**. Emitter fallbacks are the tool's own defaults,
      not constants.
- [ ] `hook_docs.py` / `get_hook_documentation` / `describe_hook` / the runner
      schemas are updated together, with the predetermined-runs bullet from
      §"Documentation and model behavior", and the categorical "a saved hook
      cannot do that" advice removed.

**Evidence — written before the fix, failing first**

- [ ] No envelope means the existing behaviour: the proposal is refused and
      nothing is written. A declined confirmation means no acquisition and no
      write.
- [ ] Envelope interval, configured `named_stages` bounds and driver limits
      intersect; each boundary is inclusive and one representable value outside
      each is refused **without a core write**.
- [ ] A dispatch calls `check_named_stage`, moves the labelled device, waits,
      reads back, records the achieved position, and never touches the core XY/Z
      setters.
- [ ] **An achieved position outside the approved interval, from a requested one
      inside it, is recorded and reported and does not refuse.** Use the rig's
      numbers: request 21294 against a ceiling of 21294, achieve 21299.
- [ ] Unapproved target and exhausted budget abort the run before the affected
      exposure; a bridge failure returns the partial dataset path and the last
      known state.
- [ ] Actions are attached by `hook_event_index`, not arrival order: delay frame
      N's image processing and prove event N+1 is not yielded or exposed with a
      missing or stale action set.
- [ ] **A fixed-plan runner never takes a next-frame hardware action from
      `analyze_frame`**: install frame 0 and 1 moves through `hook_action_plan`,
      delay frame 0's analysis past frame 1's pre-hardware callback, prove both
      planned moves land; then return a different `MoveNamedStage` from frame 0
      and prove it is refused without changing frame 1.
- [ ] `hook_event_index` is absent from `event["axes"]` on every yielded event,
      and a dataset from an indexed run exports through `export_dataset_as_tiff`
      with the frame count it acquired.
- [ ] Entry-state restoration succeeds through the same checks; a failed
      restoration is reported and never described as success.
- [ ] Timelapse and Z-stack export the envelope and the indexed plan; the script
      compiles, defines every name it uses, imports nothing from `microclaw`, and
      contains no `# NOT EMITTED` and no `raise RuntimeError`. An export carrying
      the envelope but dropping the plan is the silent no-op this row exists to
      catch.
- [ ] A hookless timelapse still emits through `_emit_acquisition`.
- [ ] Full suite green at or above the baseline measured on the start commit,
      re-run by the coordinator rather than accepted from the report.

**Process**

- [ ] Runner prompt written to the scratchpad; the user is asked before any agent
      starts. Not committed.
- [ ] Implementation reviewed from the diff, through as many returned rounds as
      it takes.
- [ ] Runbook `design/52-block52a-rig-gate.md` written **on the block's branch**,
      with literal PowerShell-safe commands and expected values — not criteria —
      and the implementation pinned by `git merge-base --is-ancestor <commit>
      HEAD`.
- [ ] Branch pushed to `origin` (`GIT_SSH_COMMAND="ssh -i ~/.ssh/yonce"`). No PR.
- [ ] **Rig gate 52a on M5**, TIRF limb 1, run by the user. Never simulated.
- [ ] Findings fixed on the same branch, sized to the finding, and re-gated.
- [ ] Merged to `main`, `main` pushed, branch deleted locally and on `origin`;
      `git log --oneline origin/main..main` empty.
- [ ] Ledger row closed and coordination notes added to `design/prompts.md`.
- [ ] **Step-10 design gate** merged before 52b is assigned.

### 52b — the general bounded property

**Implementation**

- [ ] `SetDeviceProperty(value)` joins `_ACTION_TYPES` with the same parse
      refusals as 52a's action. No `device`, no `property` — the envelope names
      both.
- [ ] `property_envelope` validates by exact key set in
      `_configure_hook_capabilities`, with the categorical and numeric key sets
      **mutually exclusive** so `set(envelope) != allowed` still decides validity
      in one line. No wildcard device, property or value. `restore` required, as
      in 52a.
- [ ] `authorize_property_write` runs **unchanged**. The envelope is not a route
      around the map: an excluded or unclassified pair refuses regardless of
      approval, for design/49's reason.
- [ ] The write reuses the public property tool's capability-aware bounds
      checks — typed actuator range/unit, stage, exposure, illumination,
      categorical domain. Do not reproduce them in the adapter.
- [ ] `check_device_property` (`safety.py:1002`) is split so the bounds/type
      validator stays mandatory while the approved envelope replaces the
      allow/deny **policy** decision. Follow the distinction the function already
      draws for typed and illumination pairs rather than inventing one, and state
      in the report exactly which branch was cut. **The ordinary
      `set_device_property` path is unchanged.**
- [ ] A frame's actions are applied in order, waiting per write, stopping
      immediately if a write **raises**; the whole set is then verified in **one
      pass** with `_verify_property`'s semantics (`Float` numerically for MM's
      `"10"` → `"10.0000"`, everything else exactly). Design/53's distinction is
      the rule: *the device rejected this* is knowable per write, *this is not
      consistent yet* only at the end.
- [ ] `ctrl.refresh_gui()` after the write, as `set_device_property` does — the
      EMU repaint behaviour, not a new general claim.
- [ ] Micro-Manager-reported limits or allowed values narrower than the reviewed
      config are intersected and shown before approval. An unbounded numeric
      property may be approved only as an exact finite value set, and the dialog
      says Microclaw has no independent range to verify.
- [ ] `run_timelapse`, `run_zstack` and their emitter carry `property_envelope`;
      multiposition and tile still accept nothing new.

**Evidence — written before the fix, failing first**

- [ ] Categorical, bounded numeric, exposure, illumination, and an approved pair
      that the ordinary raw property path excludes.
- [ ] **`SetDeviceProperty` on a bounded stage device's position property refuses
      at `authorize_property_write`** with design/49's message naming
      `move_named_stage`. This is a pass, not a gap.
- [ ] An approved in-bounds write is not refused merely because the hook
      provenance is `saved_untrusted` or because the action is hardware motion.
- [ ] **Two interdependent actions in one frame both apply, then verify**: a fake
      whose representable set for one property depends on another, ordered
      mode-second so per-write verification would fail — **and the reverse order
      too**, because design/53's amendment exists for the mirrored case.
- [ ] A configured categorical set and a run-approved subset combine by
      intersection; a historical categorical exclusion does not veto the exact
      approved action.
- [ ] Approval audit includes hook hash, exact envelope, write budget,
      acquisition plan, decision and operator identity; one changed byte or
      envelope field invalidates a session grant.
- [ ] An exported session that dispatched a property action contains no
      `# NOT EMITTED` and no `raise RuntimeError`, compiles, and reproduces the
      envelope and its checks.
- [ ] Full suite green at or above the 52a baseline, coordinator-re-run.

**Process** — as 52a, with runbook `design/52-block52b-rig-gate.md`, rig gate 52b
on M5, and the step-10 design gate merged before 52c is assigned.

### 52c — the adaptive refinement

**Implementation**

- [ ] `run_adaptive_survey` accepts both envelopes and **rejects**
      `hook_action_plan` — its events are chosen at runtime and there is no index
      set to validate against.
- [ ] `HookResult` is parsed completely, then partitioned **exactly once**:
      `EmitArtifact`/`DiscardFrame` against frame N and dispatched immediately;
      exactly one `ContinueSurvey` or `AcquireAt` selecting event N+1;
      `RequestAutofocus` selecting event N again; the hardware actions forming one
      ordered pre-exposure set attached to the selected event; `StopSurvey`
      selecting nothing and incompatible with a next-frame hardware action.
- [ ] Zero or more than one next-event selector beside a hardware action is
      malformed. Current-frame actions are never deferred to N+1, and next-frame
      hardware actions are never dispatched from `image_process_fn`.
- [ ] **`RequestAutofocus` is detected while partitioning**, before any hardware
      action is dispatched: it queues the refocused tile and refuses every paired
      hardware action with the existing *"not dispatched until the refocused tile
      is judged"* reason, regardless of their order in `HookResult.actions`.
- [ ] **A malformed partition is an analysis defect, not a hardware failure**: it
      records and refuses, calls `progress.done_early()` before
      `progress.image_done()`, queues no event, and is neither an acquisition
      abort nor a watchdog stall. Nothing was written, so no frame is mislabelled.
- [ ] The handoff **is `candidates`** (`_survey_event_stream:5215`), with the
      index and the next-frame action set attached to the candidate event and one
      candidate per index enforced on the draining side. **No second queue.**
- [ ] `max_idle_s` keeps its present meaning: on expiry the generator calls
      `note_stalled` and returns. It never releases an event whose action set
      never arrived.
- [ ] Once the final authorized event is yielded the handoff closes; a later
      proposal is refused and the run reported aborted, never applied as an
      exit-side mutation.
- [ ] `_emit_adaptive` and the inlined standalone runner carry the same
      coordinator, indexing and partition rules, with `inspect.getsource` and
      never a re-written copy.
- [ ] The adaptive documentation bullet from §"Documentation and model behavior"
      lands with the code.

**Evidence — written before the fix, failing first**

- [ ] A mixed result of `EmitArtifact`, `MoveNamedStage` and `ContinueSurvey`
      writes the artifact against frame N, queues exactly one event N+1, and
      performs the move only in N+1's pre-hardware callback.
- [ ] Two next-event selectors beside a hardware action queue nothing and produce
      a refusal record; `done_early()` precedes `image_done()`; the run is neither
      aborted nor allowed to age into `note_stalled`.
- [ ] Both `RequestAutofocus, MoveNamedStage` **and the reverse order** queue the
      refocused tile and refuse the move; neither performs it. Pairing
      `RequestAutofocus` with `ContinueSurvey` is malformed and queues neither
      event.
- [ ] A proposal for the frame after the last authorized one aborts rather than
      exposing.
- [ ] Let `max_idle_s` expire and prove the stream ends with `note_stalled`
      rather than releasing the pending event.
- [ ] **Export emits the program, not the trace**: the script contains the hook's
      rule and the decision loop, and does not contain the run's target list.
- [ ] Full suite green at or above the 52b baseline, coordinator-re-run.

**Process** — as 52a, with runbook `design/52-block52c-rig-gate.md` and rig gate
52c on M5 (TIRF limb 2, including the export limb).

### Carried forward, owed by nothing here

- **`run_tile_acquisition` cannot run an artifact-emitting saved hook**, and its
  refusal names a remedy the caller cannot reach. Found while scoping this
  design, pre-existing, filed in `design/35`'s open register. No block here
  depends on it.
- **Illumination export stays refused** (`tools.py:836`). §Export explains why it
  is not an inconsistency.
- **Twelve tools remain undecorated for script export** (`CLAUDE.md`, measured
  2026-08-12). These blocks add no tools; if one appears, it is decorated in the
  block that adds it.
