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

**Corrected 2026-08-17, at 52b's assignment: the hazard is real but unreachable
at one pair, and the rule ships anyway.** `property_envelope` names exactly one
`(device, property)` — §"How the hook names its target", settled 2026-08-15 — so
a frame's pre-exposure action set can hold at most **one** property. `SetExposure`
is refused on every path (`hook_decisions.py:784`, `:700`) and
`SetIlluminationPower` dispatches post-hardware from `image_process_fn`, not
before the exposure. Two interdependent properties therefore cannot both be
written by a hook in one frame, and "verify the whole set in one pass" verifies a
set of size one. The paragraphs above describe the shape a multi-pair envelope
would need; they do not describe a defect 52b can produce.

What 52b builds is the rule, not the reproducer: apply in order, wait per write,
stop immediately on a raise, then verify in a separate pass over the set. It is a
few lines in that shape rather than interleaved, it is the shape design/53
measured, and it is what a multi-pair envelope would extend without rewriting.
What 52b does **not** owe is evidence of the interdependence itself — see
§"Evidence and gates", whose two-property row is retired for this reason, and
§Blocks 52b, whose M5 `Normal Mode` limb goes with it. If a multi-pair envelope
is ever designed, both come back with it.

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

**Rig corrections, 2026-08-15 to 2026-08-17. This section as originally written
is not implementable; what shipped is below.** Three of its assumptions were
wrong and each was found on M2 (block 52a, five trips). `CLAUDE.md`
§"The pycro-manager acquisition engine" now carries the general form.

1. **`hook_event_index` is never carried on the event.** Point 1 below asks for a
   monotonic index on each event and forbids it in `event["axes"]`. Both cannot
   hold: the engine serialises a closed key set, so an injected key is silently
   dropped, and `axes` is the only per-event identity it must preserve. **Plans
   are keyed by the event's axes signature** — `tuple(sorted(axes.items()))` —
   and nothing is injected. `hook_action_plan` keeps `hook_event_index` as its
   caller-facing key, an index into the generated event list, and validation
   binds each entry to that event's signature. Duplicate signatures refuse.
   The forbidding half of point 1 stands and is satisfied more strictly than it
   asked: no axis is added at all.
2. **A callback may receive a list.** See the paragraph below.
3. **Restoration runs after the acquisition, not after `acquire()`.**
   `acquire()` only submits; completion is awaited in `__exit__`. §"Failure
   semantics and audit"'s restoration rules are correct, but they must execute
   outside the `with` block or they fire mid-sweep.

**Rig correction, 2026-08-15:** pycro-manager may call pre- and post-hardware
callbacks with either one event dict or a hardware-sequenced list of event
dicts. A one-element list is processed normally and callbacks preserve the
input shape. A fixed plan refuses every multi-event batch before its first
write or exposure, including batches whose planned action sets are empty:
per-event action and labeling guarantees cannot be honored while hardware runs
the burst without software in the loop. This remains a runtime check because
the batch decision depends on live device sequencing capabilities as well as
the generated axes; argument validation cannot determine it reliably. The
refusal directs timelapse callers to use a nonzero ``interval_s``, which defeats
time-axis sequencing.

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
refusals silently. The exported script **prints** the approved envelope, its
bound, its write budget and its restoration policy before starting. The pinned
hook hash and envelope are embedded in the script.

**It does not prompt. Operator decision, 2026-08-17, after 52b's second M5
gate**, replacing this section's original rule that the script "requires
confirmation before connecting/starting unless the user explicitly requests a
non-interactive artifact". Three reasons, and the first was measured:

- **The prompt is invisible under redirection.** 52b's own runbook pipes the run
  through `Out-File`, which swallowed `Type YES to continue:` — the operator saw
  an apparently hung script, and the prompt surfaced only at the tail of the
  captured file, after the traceback.
- **A run carrying both envelopes prompted twice**, once per emitted block.
- **The prompt is not what makes the script safe.** The envelope interval or
  value set, the write budget, `check_named_stage` / `check_device_property`, and
  the read-back all still run; a value outside the envelope is refused whether or
  not anyone typed YES. Running the script is the consent.

The print stays, and design/38 F9 is why: nothing silent. It is now the only
place the script states what it will move and within what limits, so it must
carry the **bound**, not just the device — the property print omitted it until
this change. The heading is declarative (`HOOK HARDWARE CONTROL FOR THIS RUN`)
rather than a request. What is given up, stated rather than implied: someone who
runs the script later, on a different sample, is *informed* but not *stopped*.

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
- ~~**Two actions in one frame whose validity is interdependent both apply, then
  verify.**~~ **Retired 2026-08-17** — unreachable at one pair per envelope, for
  the reason §"Verify the frame's actions as a set" now gives. What replaces it:
  a frame's action set applies in order and **stops immediately on a write that
  raises**, with verification in a separate pass afterwards; assert the ordering
  by observing the call sequence against a fake, not by building an
  interdependence a single-pair envelope cannot express. A test written for the
  unreachable case cannot fail, which block 46 established is worth zero.
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
  reason; neither performs the move. ~~Pairing `RequestAutofocus` with
  `ContinueSurvey` is malformed and queues neither event.~~ **Corrected
  2026-08-17, coordinator ruling during 52c's round 2: the selector-cardinality
  rule applies to a result that carries a hardware action.** A result with no
  hardware action keeps the behaviour `main` already had, including
  `RequestAutofocus, ContinueSurvey` — that pairing is what `hook_docs`
  prescribes and what `test_a_refused_refocus_still_lets_the_survey_advance`
  encodes, and its reason is a stall measured on M5 on 2026-08-11: a refocus can
  be *granted* and still queue nothing, so a hook that offers no fallback route
  idles out `max_idle_s`. Making the pairing malformed deletes that guard and
  leaves the docs prescribing the pattern that stalls. §Timing's unconditional
  wording is reconciled at 52c's step-10 design gate.
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

- exactly one confirmation before acquisition and none from the hook thread, and
  an illumination-only run's dialog is **unchanged** from what block 7b gated;
- one NDTiff dataset containing 18 frames;
- each frame's metadata/log names requested and achieved TIRF position;
- all 18 writes pass `check_named_stage`, settle, and read back;
- the hook can compute/log the image metric and select an optimum without the
  agent interleaving manual moves;
- the requested restoration policy is verified;
- a second run proposing one position just beyond the configured named-stage
  bound **refuses during planning, before the acquisition starts and before any
  hardware moves** — expect a refusal and no dataset, not a partial one; and
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

**Rig gate 52a — any rig with a named single-axis stage. RUN AND PASSED ON M2,
2026-08-17**, over five trips; kept below as the record of what was measured.
§"TIRF acceptance gate" limb 1 in full: all seven pass criteria, including the
out-of-bounds second run and the exported script.

**The gate needs a labelled single-axis stage with declared bounds. It does not
need that stage to be `Thorlabs ELL17/ELL20`, and it does not need the rig to be
in TIRF alignment.** What limb 1 measures is one approval, one dataset, one
action set per event index, requested and achieved position on every frame, a
bounds refusal, and an export — motion, indexing and export, none of which is
optical. M5's ELL numbers (`19639–21294 µm`, request 21294 → achieve 21299) stay
in this document as **recorded history and off-rig fixture values**; they are not
instructions to any other rig, and a runbook that copies them is wrong.

**M2 precheck, 2026-08-15 — the preconditions below are already satisfied.**
Evidence: `~/Documents/Documents - Beyonce/Projects/Micro-Claw/52a-m2-precheck`
(`inventory.json`, `52a-m2-authmap.txt`, the live `safety_config.yaml`).

- `TIRF Stage` is a **`StageDevice`**, adapter `SmarAct 1D`, on COM6. `SmarActZ`
  is a second `SmarAct 1D` on COM11 — **the adapter name does not identify the
  axis, the label does.**
- It is already declared: `named_stages: {device: TIRF Stage, min_um: -10497.8,
  max_um: 6256.8}` in a schema-3 `reviewed: true` config. The run interval is a
  sub-range of that, chosen on the day.
- It shadows nothing: core focus is `PIZStage`, core XY is `SmarActXY`.
- It is in `bounded_stage_devices`, entry `stage-position` /
  `built_in_typed_capability` on path `dedicated-stage`.
- **The rig has one config group, `Camera`. There is no `Channel` group**, so the
  sweep runs `run_timelapse(channel=None, exposure_ms=…)` — the SMLM path. A
  runbook that passes a channel is refused by `_check_acquisition_channel`.
- Two things this config does **not** bound, so no gate limb may claim them: it
  has no `camera` section (no `max_exposure_ms`) and no `illumination` section
  (`illumination_unrestricted: true`), and the map runs in degraded
  trusted-plugin mode with completeness suspended and the three Luxx enables
  undeclared.

Preconditions, all operator-owned and none of them code:

1. The TIRF axis is a Micro-Manager **stage** addressable by label — the
   dispatch is `core.set_position(device, um)` / `get_position(device)`. A
   generic device carrying a position *property* is not this block; it is 52b,
   and it will meet design/49's refusal.
2. That label has a `named_stages` entry with real `min_um`/`max_um` in the
   rig's **reviewed schema-3 config**. `check_named_stage` fails closed, so
   without the entry nothing moves. The declaration is also what makes the
   device a bounded stage device (`authorization.py:1504`), which is what 52b's
   refusal limb later needs — one entry serves both.
3. The interval is **measured on the day**, not inherited: read the current
   position and the driver's own limits, and pick a conservative sub-range.
4. The label must not shadow core XY or `Core.Focus`, or startup refuses with a
   core/named-actuator declaration conflict.

**M2 has never been run in TIRF mode, and the gate is still valid there** — say
so in the runbook rather than letting a reader infer an optical claim. The hook
must compute and log its metric and select an extremum; whether that extremum is
an optically meaningful TIRF angle is not what this block proves. Two M2 facts
the runbook carries: its camera triggers the lasers, so every frame of the sweep
is a dose, and `named_stages` was deliberately empty in its last recorded profile
(`design/29-block9-m2-safety-config.yaml:123`), which is precisely the entry
precondition 2 asks for.

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

**Rig gate 52b — M5** (operator decision 2026-08-17; M2 access ended with 52a).
A property the operator names on the day: a categorical one, and a bounded
numeric one if the reviewed config has one. The runbook records which pair was
used; the pair never enters `microclaw/`. **One limb is mandatory** whichever
pair is chosen: a `SetDeviceProperty` aimed at a **bounded stage device** refuses
at `authorize_property_write` with design/49's message naming `move_named_stage`
— a pass, not a gap, and the test that the envelope did not become a route around
the map.

> **The set-verification limb is retired, 2026-08-17.** It required an
> interdependent pair applied in one frame, which one pair per envelope cannot
> express; §"Verify the frame's actions as a set" gives the full reason. M5's
> `System/Normal Mode` — whose `Exposure` is only representable after a later
> `ScanMode` write (design/53) — was its reproducer and is recorded here as the
> case a multi-pair envelope would return to, not as a step to run.

**M5 precheck, 2026-08-17.** Evidence:
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/52b-m5-precheck`
(`52b-m5-authmap.txt`, `52b-m5-checkconfig.txt`, `52b-m5-inspect.txt`). The
config is schema-valid and `reviewed: true` at
`%APPDATA%\microclaw\safety_config.yaml`, with one review warning: the two
`acquisition.confirm_above_*` limits still equal the packaged example values.

- **The refusal limb can target `Thorlabs ELL17/ELL20` directly — no retarget,
  unlike M2 — and it does not depend on that device exposing a position
  property.** `property_writes_unrestricted` is **true**, so
  `authorize_property_write`'s first clause decides: every property on a device in
  `bounded_stage_devices` refuses unless the exact pair is a
  `built_in_typed_capability` on a path in `_RAW_WRITE_PATHS`. ELL17/ELL20's only
  entry is `stage-position` on `dedicated-stage`, which is not such a path, so
  **every** raw property write to it refuses with design/49's message naming
  `move_named_stage`. Computed over the map, not assumed:

  | bounded stage device | raw property write |
  |---|---|
  | `PIZStage` | **admits** `External sensor` (focus-lock, `generic-property`) |
  | `SmarAct 1D` | refuses every property |
  | `SmarAct 2D` | refuses every property |
  | `Thorlabs ELL17/ELL20` | refuses every property |
  | `Thorlabs ELL20` | refuses every property |

  `PIZStage.External sensor` is the matched pair that makes the limb sharp: the
  same rig, the same clause, one pair admitted and the rest refused, which is
  exactly design/49's distinction. **It engages the focus lock on the focus
  drive** — prove the admission without writing it, or do not use it.
- **`Thorlabs ELL17/ELL20` exposes `Position (um)`, so the limb targets exactly
  what §"Runtime checks" originally named.** From `inventory.json`: a
  **`StageDevice`** (library `ThorlabsElliptecSlider`) carrying a writable
  `Position (um)` — `Integer`, `has_limits: true`, driver-reported **0–28000**,
  currently 18146. It is both addressable by the MMCore stage API *and* exposes a
  position property, which is the combination M2's `TIRF Stage` lacked and the
  reason that rig had to retarget. The driver range also corroborates block 48e's
  0–28000 to the digit.

  This makes the limb **non-vacuous**, which was the open risk: the property
  exists and is writable, so a refusal cannot be MMCore's *"Invalid property name
  encountered"* and must be the authorization map's. Aim `SetDeviceProperty` at
  `Thorlabs ELL17/ELL20`.`Position (um)` and expect design/49's message naming
  `move_named_stage`.

  `PIZStage` is the sharpest corroboration and needs no extra rig time: on one
  device, `External sensor` is admitted and `Position` (Float, driver 0–100) is
  refused, by the same clause. `Thorlabs ELL20`.`Position (um)` (0–60000) and
  `SmarAct 1D`.`Frequency` (1–18500) refuse for the same reason.
- **Categorical pairs are plentiful and need no config edit.** Eight
  `reviewed_categorical_property` entries on `generic-property`, all
  `auto:state-device`: `Thorlabs Filter Wheel`, `Thorlabs Filter Wheel-1`,
  `Thorlabs ELL6` and `iChrome-MLE-TCP`, each with `Label` and `State`. **Use
  `Thorlabs Filter Wheel`.`Label`** — a `StateDevice` whose six allowed values are
  `Filter-1`..`Filter-6`, currently `Filter-1`, so the approved subset and the
  device domain intersect visibly. Do **not** use `iChrome-MLE-TCP` — it is the laser
  engine, block 3b's gate already caught a laser-engine widening there, and this
  rig's camera triggers the lasers.
- **The numeric limb is available after all, from driver-reported limits.**
  Recorded first as unavailable and **corrected 2026-08-17 when `inventory.json`
  arrived**: that conclusion was drawn from the authorization map alone, which is
  a *policy* surface. The map indeed carries no `typed_continuous_actuator` and no
  `allowed_numeric`, and `property_writes_unrestricted: true` means the config
  declares no `property_authorization` section, so nothing is allowlisted *or*
  excluded. But §"Approval and bounds are different controls" already names
  Micro-Manager's own reported limits as a bound source to intersect, and the
  inventory is full of them. The config being silent widens what may be approved;
  it does not remove the bound.

  Shortlist, all writable, not `pre_init`, `has_limits: true`, on devices the map
  admits:

  | pair | type | driver range | now |
  |---|---|---|---|
  | `HamamatsuHam_DCAM`.`Exposure` | Float | 0.0177–1000.0 | 11.2130 |
  | `HamamatsuHam_DCAM`.`ScanMode` | Integer | 1–3 | 3 |

  `Exposure` is the better pick: it reaches `check_device_property`'s
  camera/exposure branch, so it exercises the capability-aware bounds routing the
  block claims rather than a bare numeric compare. Its one wrinkle is that the
  acquisition sets exposure too, so the runbook must say which value wins.
  `ScanMode` is the clean alternative — no argument on the acquisition call
  touches it, and it is design/53's own property.

  **Do not use** anything on `iChrome-MLE-TCP` or `iBeamSmartCW*` (laser engine
  and lasers), any `Laser Trigger`.`Duration*`/`Sequence*` (on this rig the FPGA
  pulse duration *is* the dose), `PWM`/`Servos`.`Position*` (unidentified
  actuators), or the DCAM `BUFFER*`/`RECORD*` internals.
- **Whether `camera.max_exposure_ms` is declared is still unread** — the live
  `safety_config.yaml` was not copied. It decides only whether the exposure limb
  intersects a configured bound as well as the driver's, not whether the limb can
  run.
- **Core assignments** (`inventory.json`): camera `HamamatsuHam_DCAM`, focus
  `PIZStage`, XY `SmarAct 2D`, **no core shutter**, no autofocus device. So the
  TIRF axis shadows nothing, as 52a's precondition 4 requires. There is one
  config group, `System`, whose presets write DCAM properties including
  `Exposure`. `enumeration_failures` is empty.
- **What this config does not bound, so no limb may claim it**:
  `illumination_unrestricted: true`, `channels_unrestricted: true`,
  `authorized_presets: []` with `channel_source: emu-laser-map` and the 405/488
  EMU enables undeclared, `mode: degraded_trusted_plugins`, `complete: null`, and
  a permitted hardware-motion plugin. As on M2, the gate runs
  `run_timelapse(channel=None, exposure_ms=...)`.

> **M5 is the rig this design came from, so the original text may be literally
> right here where it was wrong on M2.** Block 48e's gate authored M5's three
> non-core named stages: `Thorlabs ELL17/ELL20` **0–28000 um** and
> `Thorlabs ELL20` **0–60000 um**, both from *driver ranges*, and `SmarAct 1D`
> **0–2000 um** where **"MM reports no position property"** so typed bounds were
> required instead (`design/35:4269`). If ELL17/ELL20 does expose a position
> property, the refusal limb targets it directly, as §"Runtime checks" originally
> specified. **Verify before writing the runbook** — that exact assumption cost
> M2 a retarget.

> **The refusal limb cannot target `TIRF Stage`'s position, and the M2 precheck
> is why.** That device exposes **no position property at all** — its properties
> are `Controller`, `Description`, `Frequency`, `ID`, `Name`, `Port`,
> `Z channel`, `Z direction`. Position exists only through the MMCore stage API,
> which is exactly why 52a dispatches through `set_position` and not a property.
> Aiming `SetDeviceProperty` at a property that does not exist would fail in
> MMCore rather than in the authorization map, and pass **vacuously** — the trap
> this checklist flags elsewhere.
>
> Use a real property on a bounded stage device instead. On M2, both work and
> `bounded_stage_devices` is `{PIZStage, SmarActXY, SmarActZ, TIRF Stage}`:
>
> - **`TIRF Stage.Frequency`** (Integer, driver range 1–18500, currently 5000) —
>   preferred, because it keeps the limb on the very stage 52a declared. Its only
>   typed entry is `stage-position` on `dedicated-stage`, which is **not** in
>   `_RAW_WRITE_PATHS`, so the raw route refuses.
> - **`PIZStage.Position`** (Float, driver range 0–500, currently 98.9786) — the
>   corroborating case, and the closer analogue of the M5 original.
>
> **Do not turn this limb into an approved write.** It proves a refusal, so
> nothing reaches hardware — which is fortunate: `SmarAct*/Frequency` writes are
> recorded as blocking Micro-Manager's event thread for 5.0–7.5 s
> (`design/29-offline-dataset-analysis.md:303`), and `PIZStage` is the focus
> drive.

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

**Rig gate 52c — M5, on `Thorlabs ELL17/ELL20`.** This returns the gate to the
rig and the axis the design was written from, so limb 2 reproduces the 2026-08-14
session directly: its recorded sweep was **19639–21294 um**, inside the reviewed
0–28000 bound, and its optimum lay at one of two points the agent chose *after*
seeing results (history lines 68–76). §"TIRF acceptance gate" limb 2 in full — coarse pass then hook-chosen refinement, one envelope, one
dataset — including the export limb, where a script reproducing this run's exact
target list is a **fail**. The refinement targets must be chosen by the hook and
absent from the seed plan; that is a property of the decision loop, not of the
optics, so it holds on a rig that has never been aligned for TIRF.

Step-10 design gate: replace this document's §Timing prose with what the rig
measured, and close the `design/35` register row.

## Run ledger

| Block | Branch | Start commit | Implementer | Rig gate | Merged | Design reconciled |
|---|---|---|---|---|---|---|
| coordination | `design52/reconcile-decision` | `bdffb14` | coordinator | n/a | merged `d97d256` | n/a |
| coordination | ~~`design52/checklist`~~ | `d97d256` | coordinator | n/a | merged `f872a0c` | n/a |
| 52a | ~~`design52/block-52a`~~ | `413caec` | codex, 5 rounds + coordinator fixes | **M2 PASS 2026-08-17**, 5 trips; all limbs incl. `restore:"entry"` | merged `00c1763` | design gate below |
| coordination | `design52/assign-52b` | `b6f17b3` | coordinator | n/a | | n/a |
| 52b | `design52/block-52b` | `cc47438` | codex, 2 rounds + coordinator fixes | **M5 PASS 2026-08-17**, 3 trips; all limbs incl. the design/49 refusal and the standalone export | merged `2b9233e` | design gate below |
| coordination | `design52/assign-52c` | `8f75ce0` | coordinator | n/a | | n/a |
| 52c | `design52/block-52c` | `453a274` | codex, 2 rounds | runbook pushed 2026-08-17, awaiting M5 | | |

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

### State at the 2026-08-17 assignment of block 52c — the live note

**This is the live note. The bullets below it, from "The gate found a defect"
onward, are 52a's round history and are kept for their findings, not as
instructions — nothing in them is outstanding.**

Checked against the repository rather than assumed: working tree clean,
`git log --oneline origin/main..main` empty, `main` at `8f75ce0`, and on `origin`
besides `main` only `design34/focus-system-authorization` (6a),
`florian/setup-claude-workflow`, `ollama` and `port-to-jpype-acqj` — **no open
block branch.** One worktree. Suite on `main`, macOS: **1872 passed / 99
skipped / 3 warnings**, 1971 collected, coordinator-re-run at `8f75ce0` on
assignment day and agreeing with the count measured after 52b's design gate.
**That is 52c's baseline.**

- **52c is assigned from this commit.** Branch `design52/block-52c`, worktree
  `../microclaw-52c`, its start commit recorded in the ledger above.
- **The handoff cannot be a key on the candidate event, and §Timing point 3
  reads as though it can.** "The trusted adapter attaches the index and the
  next-frame hardware action set to that candidate event" is the one sentence in
  that section 52a's engine finding has not yet been applied to: an injected key
  is silently dropped by `event_to_json`, exactly as `hook_event_index` was.
  What survives the engine is the **axes signature**, which is what
  `configure_named_stage`/`configure_property` already key their plan by
  (`_fixed_plan_context`, `axes_signature`). So the adaptive handoff registers
  `signature -> (index, actions)` in the adapter *before* `candidates.put(event)`
  and `pre_hardware_hook_fn` resolves it the same way it resolves a fixed plan —
  no second queue and no second resolution mechanism, which is the checklist row
  as written. **A revisited tile repeats its axes**, so one entry per signature
  must be enforced where the fixed plan enforces it, and a repeat is a refusal,
  not an overwrite. §Timing's prose is reconciled to what the rig measures at
  52c's step-10 design gate; this note is the instruction until then.

- **52a is CLOSED, 2026-08-17.** Merged `00c1763`, `main` pushed, branch deleted
  locally and on `origin`, worktree removed, notes in `design/prompts.md`, design
  gate merged (`CLAUDE.md` §"The pycro-manager acquisition engine", this
  document's §Timing corrections, `design/35`'s register row). Five M2 rig trips,
  five runner rounds. **Nothing from it is owed.**
- **52b is CLOSED, 2026-08-17.** Three M5 rig trips, two runner rounds and six
  coordinator fixes. Every checklist row above is ticked and verified by the
  coordinator, not accepted from a report. **Nothing from it is owed.**
- **52c is next and is not started.** No branch, no worktree, no runner prompt.
  Start it from `CLAUDE.md` §"The block workflow" step 1; the §Blocks entry for
  52c is its scope, and its gate returns to `Thorlabs ELL17/ELL20` on M5 — the
  axis and the rig design/52 was written from.
- **Do the export work first in 52c, before its runbook.** 52c's export claim is
  *stricter* than 52b's — the emitted script must contain the hook's rule and
  **must not** contain the run's target list (§"TIRF acceptance gate" limb 2,
  where reproducing this run's exact targets is a **fail**). Three of 52b's trips
  died on the export, so extend the executing-export test
  (`test_emitted_property_run_actually_dispatches_its_writes`) to the **adaptive**
  path as part of the implementation, and gate the runbook on it passing. Do not
  discover this on a rig for a fourth time.
- **Four things 52b learned that 52c inherits.** (1) For the exporter, *compiles*
  and *greps clean* are not evidence — three gate trips found three different
  export defects, two of which survived compilation; a test must **run** the
  emitted script. (2) A gate step must name the **mechanism** under test, not the
  outcome, or a capable agent satisfies it by the better route and the limb never
  runs. (3) `export_session_script` compiles *this session's* calls, so any
  export step needs a run in front of it in the same session. (4) Exported
  scripts print their envelope and **do not prompt**.
- **52b's set-verification limb is retired, and this was settled before
  assignment** (operator decision, 2026-08-17). `property_envelope` names one
  `(device, property)`, so a frame's pre-exposure set holds at most one property
  and design/53's interdependence hazard cannot arise — `SetExposure` is refused
  on every path and `SetIlluminationPower` dispatches post-hardware. **The rule
  still ships** (apply in order, stop on a raise, verify in a separate pass); only
  its evidence is retired, off-rig and on-rig alike, along with M5's
  `System/Normal Mode` reproducer. §"Verify the frame's actions as a set" carries
  the reason. **Rig gate 52b is therefore one mandatory limb**, the design/49
  refusal, plus the operator's chosen categorical and numeric pairs.
- **Do not manufacture the retired case.** A multi-pair envelope with an ordinal
  slot on the action was the alternative and was rejected: it reopens
  §"How the hook names its target", settled 2026-08-15. If a real two-property
  workflow turns up, it is a new design and it brings both limbs back with it.
- **The undecorated-tool count is eleven**, coordinator-measured over
  `TOOL_REGISTRY` on 2026-08-17 and agreeing with `CLAUDE.md`; the carried-forward
  row names all eleven. 52c adds no tool, but if it adds one it is decorated in
  that block.
- **The gate rig changes to M5 for 52b and 52c** (operator, 2026-08-17: M2
  access ended with 52a). This is a return to the rig design/52 was written from,
  which helps more than it costs: 52c's limb 2 can reproduce the original
  2026-08-14 sweep on `Thorlabs ELL17/ELL20` (19639–21294 um, inside its reviewed
  0–28000 bound). (This bullet also claimed a **known** reproducer for 52b's
  set-verification limb, `System/Normal Mode`; that limb was retired the same day,
  above.) **Every M2-specific fact in 52a's gate
  section is history, not instruction** — `TIRF Stage`, its −10497.8..6256.8
  bounds, the ELL9 categorical trap, the 1 s sequencing interval, the 124-skip
  count. None of them transfer.
- **Copy 52b's runbook, not 52a's, for 52c.** `design/52-block52b-rig-gate.md` is
  already M5-shaped: `uv pip install -e .`, `uv run python -m pytest`, and a
  Step 0 whose collected total is the branch check. 52a's is M2-shaped and its
  Step 0 uses bare `python`, which on M5 is a miniconda interpreter carrying
  neither microclaw nor pytest — block 7b lost a whole preflight step to that.
  Re-measure the expected counts on M5 either way; its skip count is its own.
- **The M5 precheck is done and its facts are recorded** in §Blocks under 52b —
  `52b-m5-precheck`, captured 2026-08-17. 52c should not need a fresh one unless
  the rig config changed; read that table instead. The headline for 52c:
  `Thorlabs ELL17/ELL20` is a `StageDevice` that **also** exposes a writable
  `Position (um)` (driver 0–28000), its reviewed `named_stages` bound is
  **0–20000**, and it sat at 18146. There is no `camera` section, so no configured
  exposure bound. `property_writes_unrestricted` is true.
- **A non-`leave` restoration reserves a write**, so an N-frame plan needs
  `max_writes` N+1. 52b's runbook said N and would have been refused during
  planning; it was caught off-rig only because the arithmetic was checked before
  the runbook shipped. Check 52c's the same way.
- **Read `CLAUDE.md` §"The pycro-manager acquisition engine" before writing any
  hook or acquisition code here.** Its three contracts cost 52a three rig trips
  and are the block's most reusable output. The scratchpad runner prompts from
  that block were never committed, by design; nothing in a scratchpad is needed
  to continue.

#### 52b gate 1 — M5, 2026-08-17

- **Steps 0, 2, 3 and 4 passed.** Suite 1841 + 124 = 1965 exact. One confirmation
  per run, naming the values or the interval; the numeric dialog read
  *"approved interval 5-50 (reviewed and Micro-Manager intersection)"*, so the
  MM-limit intersection is rig-proven. Six filter writes with `requested` equal to
  `achieved` on all six, four exposure writes, restoration last in each log, and
  the out-of-envelope 80 ms attempt refused during planning with **no dataset
  directory created at all**.
- **A multi-line recorded error made the whole session unexportable, and it is
  pre-existing on `main`.** A bridge exception carries a Java stack trace; the
  `# SKIPPED` comment took only its first line and every frame after it was
  emitted as bare Python, so `ast.parse` refused the export — for the entire
  session, not just the failed step. The agent then hand-wrote a script, which is
  §Finding's failure verbatim. **Its behaviour on that path was correct** and is
  block 45's `3fc5e34` holding on a rig: it said loudly that the file was not the
  export, named the defect, and warned it was unvalidated. Fixed on this branch
  because it sits on the gate path; replaying the session's own history through
  the fixed exporter gives 7 emitted calls, no `# NOT EMITTED`, no `microclaw`
  imports.
- **Accepted property records carried no `hook_event_index`.** The accept record
  is written after the set verifies and the index was not travelling that far, so
  every property row logged `null` while the named-stage twin logged 0..N *in the
  same session*. Same shape as 52a's round-1 defect in the twin, and the log is
  the block's evidence, so this mattered.
- **Step 5, the mandatory limb, never ran — and the runbook is why.** It asked the
  agent to "set `Thorlabs ELL17/ELL20` `Position (um)`". The agent correctly
  answered that the ELL is a *stage*, built the run with `MoveNamedStage` and a
  `named_stage_envelope`, and so never reached `authorize_property_write`:
  design/49's message appears **zero** times in the history. Right product
  behaviour, wrong gate. It cost four attempts, ~371 um of TIRF-axis motion and
  the dose. **A step must name the mechanism under test, not the outcome** — the
  same lesson as 52a's skipped restore limb, in a new form: an outcome-shaped step
  gets satisfied by the better route.
- **The ELL serial failure is real hardware, not code.** *"Error in device
  'Thorlabs ELL17/ELL20': Serial command failed. Is the device connected to the
  serial port? (14)"* hit the live run **and** the hand-written script. The
  failure was recorded as `named_stage_write_failure` and aborted correctly.
- **The Step 6 greps were run against the hand-written file** and "passed" it —
  2 markers, `SCRIPT PARSES` — proving nothing about the exporter. The runbook now
  says a refused export is a FAIL and must not be grepped by proxy.

#### 52b gate 2 — M5, 2026-08-17

- **Steps 0, 2 and 5 passed, and Step 5 is the block's point.** Suite
  1844 + 124 = 1968 exact. `hook_event_index` 0..5 on the six accepted property
  records — the gate-1 fix proven on the rig — with the restoration record last
  and carrying `null`, which is right because it belongs to no frame.
  `property_restoration` read `entry_value` and `last_known_value` both
  `Filter-6`, agreeing, and the wheel really did start there.
  **`authorize_property_write` refused a `SetDeviceProperty` at
  `Thorlabs ELL17/ELL20`.`Position (um)` with design/49's message**, the axis
  unmoved, and no hand-written script anywhere in the session. Rewriting that
  step to name the *mechanism* rather than the outcome is what made it run.
- **The export succeeded and the script still failed — on its first write.**
  `AttributeError: 'types.SimpleNamespace' object has no attribute 'refresh_gui'`.
  `_apply_property` calls `ctrl.refresh_gui()`; the live controller has it and
  the emitted stand-in did not. It compiled, and it passed every grep in the
  runbook. **The gap was the test**: every property export test compiled the
  script and none ran it. The new one execs the emitted source against fakes and
  drives `pre_hardware_hook_fn` per event, and reproduces the rig's exact error
  on the pre-fix tree. The emitted helper now reproduces the live repaint rather
  than stubbing it, since M5 is an EMU rig and design/43b is why it exists.
- **Three gate trips, three different export defects, all invisible to a green
  suite and two of them invisible to compilation.** The standing lesson is now
  concrete: for the exporter, *compiles* and *greps clean* are not evidence —
  only running it is.
- **Exported scripts no longer prompt** (operator decision; see §Export). The
  prompt was found by the same run: piped through `Out-File` it was invisible and
  the script looked hung, with `Type YES to continue:` surfacing at the tail of
  the log after the traceback.

#### 52b gate 3 — M5, 2026-08-17: **PASS, and the block is closed**

Steps 0, 6a and 6. Suite 1847 + 124 = **1971** exact.

- **The standalone script ran and reproduced the run's mechanism exactly.** Two
  accepted property writes carrying `hook_event_index` 0 and 1, `requested`
  equal to `achieved` on both, and the restoration **last** with
  `restoration: true` and `requested: "Filter-1"` — the entry value — in *both*
  the live log and the emitted script's own log. Two observations each. It wrote
  its own dataset (`timelapse_2` beside the live `timelapse_1`, the emitter's
  fallback being the tool's own default, per 43j) and printed its location.
- **The Tenengrad values differ** — live 46.115/48.138, standalone 48.221/48.214.
  That is the specimen, not the code, and it is the right outcome: the export
  emits the program, not the trace, exactly as 43h's M5 round 3 established for
  hits. Identical decisions, different measurements.
- **The exported script is clean on every criterion**: 2265 lines, zero
  `# NOT EMITTED`, zero `microclaw` imports, parses, and carries
  `_PROPERTY_ENVELOPE`, `hook.configure_property` and `hook.restore_property()`.
- **The print-not-prompt change is rig-proven**: zero `input(`, zero
  `Type YES`, zero `ALLOW HOOK HARDWARE`. The script printed
  *"HOOK HARDWARE CONTROL FOR THIS RUN -- bounds enforced below"* and
  *"Property: Thorlabs Filter Wheel.Label; approved ['Filter-1', 'Filter-2'];
  maximum writes 3; restore 'entry'"*, then ran. The bound is in the disclosure,
  which is what the print had to earn when it became the only one.
- **One confirmation** for the live run, none from the standalone.

#### 52a round history — findings, not outstanding work
- **The gate found a defect no off-rig test could have.** `pre_hardware_hook_fn`
  assumed one event per callback, but pycro-manager hands a **list** when the
  engine hardware-sequences — an 18-frame timelapse at `interval_s=0` is exactly
  that shape. **Zero frames were written and the stage never moved**, and the
  crash was the good outcome: had `.get` succeeded we would have applied one
  action set and burst all 18 frames at a single position while labelling each
  with its own intended target, which is the mislabelling §"Failure semantics"
  exists to abort on. The same assumption was latent in `hooks.py` and
  `CompositeHook` for every precoded hook, and `hook_docs.py` documented none of
  it. Fixed at the boundary, with a multi-event batch refused before its first
  exposure.
- **The second M2 gate failed on 2026-08-15 too, and reproduced this design's
  motivating defect exactly.** Both planned runs died at the first pre-hardware
  callback with `planned hook event is missing a valid hook_event_index`, wrote
  zero frames, and the agent then fell back to **18 separate one-frame
  acquisitions** with parent-side `move_named_stage` between them and offline
  analysis — the §Finding failure verbatim, dose delivered for nothing.
- **§Timing's contract is unsatisfiable as written, and the rig proved it.** The
  acquisition engine serialises events through a **closed key set**
  (`acq_eng_py/main/acquisition_event.py`, `event_to_json:82` /
  `event_from_json:138`), so an injected `hook_event_index` is silently dropped.
  The only per-event identifier the engine must preserve is `axes` — which is
  exactly where the design forbids a unique-per-event value, for the measured
  NDTiff reason. Round 4 keys the plan on the event's **axes signature** instead,
  injecting nothing; `hook_action_plan`'s caller-facing index is unchanged.
- **Three rounds of tests stayed green over a mechanism that never worked**,
  because every test hand-built an event with the key already on it. **Round 4
  closed that**: `tests/test_hook_illumination_artifacts.py` now carries a fake
  acquisition that round-trips events through the engine's exact written key set
  and an end-to-end `run_timelapse` through it. Coordinator-verified against the
  pre-fix tree, where it fails with the rig's own sentence — *"planned hook event
  is missing a valid hook_event_index"*. The suite now reproduces the gate
  failure off-rig.
- **Two things the failed run measured that are worth keeping.** At
  `interval_s=1` no sequenced-batch refusal appeared, so a 1 s interval does
  defeat time-axis sequencing on M2 and round 3's guard is correctly quiet. And
  18 parent-side moves recorded achieved-vs-requested error from **-0.98 to
  +0.73 um, mean absolute 0.414 um** — the SmarAct does *not* hit its target
  exactly, so §"The envelope bounds the request, not the achievement" applies
  here as it did on M5, at sub-micron scale rather than 5 um.
- **The third M2 gate, 2026-08-15, passed every stated limb.** P=650.2, envelope
  150.2–1150.2 um, **one** confirmation, **18 planned / 18 acquired in one
  dataset**, hook log of 36 entries — 18 accepted moves carrying
  `hook_event_index` 0..17 with requested/achieved/error (-0.865 to +0.729 um) —
  plus 18 Tenengrad observations. The bounds limb refused during planning with
  the guard's own sentence (`TIRF Stage=6300.00 um exceeds the maximum allowed
  (6256.80 um)`) and wrote nothing. **Export reproduced the run**: the standalone
  wrote its own 18-frame dataset over the same 18 s.
- **Scoring the artifacts found what the pass concealed: restoration runs before
  the acquisition finishes.** The report gave `last_known_um` 650.1, the *entry*
  value, when the last move achieved 1149.4. `Acquisition.acquire()` only submits
  and returns a future; completion is awaited in `__exit__`
  (`acquisition_superclass.py:230`, `:368`), and the restoration block sits
  inside the `with`. Harmless under `restore: "leave"` and a wrong number is how
  it surfaced — but under `"entry"` or `{"value": …}` it would drive the stage
  back **mid-sweep**, mislabelling every frame after it. Round 5. The suite never
  caught it because every test fake ran callbacks synchronously inside
  `acquire()`, encoding the bug as the contract.
- **Round 5 fixed the restore timing** (`4dc2a36`): restoration now runs after
  the `with Acquisition(...)` block exits, so after `__exit__` has marked the
  stream finished and awaited completion. The failure path and
  `restoration_attempted` are unchanged, and the round-4 fake was converted to
  queue events until `__exit__` — the fake that ran callbacks synchronously
  inside `acquire()` was itself encoding the bug as the contract. Both new
  parametrizations were coordinator-verified to fail on the pre-fix tree. The
  runner's audit found no other premature post-`acquire()` read; dataset
  collision resolution is deliberately read before submission because it is
  established at construction.
- **Coordinator fix on top** (`85b0cd3`): the new emitted `print('Dataset:', …)`
  fell back to `_HERE / name`, a path that usually does not exist because
  pycro-manager appends `_1` — the gate's own dataset was `52a_m2_sweep_1` while
  `name` was `52a_m2_sweep`. It now says the location was not reported rather
  than naming a plausible wrong directory.
- **A focused re-gate is owed and the runbook scopes it**: Steps 0, 3 and 7 only,
  with `restore: "entry"` now **required** — that is the path the fix changed and
  the one that would previously have moved the stage mid-sweep. Steps 1, 2, 4, 5
  and 6 passed at `25dfb5e` and are unaffected.
- **The focused re-run passed on M2, 2026-08-17.** Step 0 exact (1825/124/1949).
  Two 18-frame sweeps, one confirmation each. **The restore-timing fix is
  rig-proven**: `named_stage_restoration` reported `last_known_um` **473.7** and
  **474.2** — the last *achieved* positions — where the defect reported the entry
  value. The bounds refusal re-ran with the same guard sentence. **Export is now
  proven end to end**: the standalone script ran the whole envelope+plan
  mechanism, wrote its own 18-frame dataset, produced a 36-entry hook log with
  `hook_event_index` 0..17 and errors -0.94 to +0.83 um, and printed its dataset
  location.
- **CLOSED 2026-08-17.** The `restore: "entry"` limb ran and passed on the fifth
  trip: 19 `hook_action` records, indices 0..17 with `restoration: false`, then
  the restoration **last in the whole 37-entry log** with `restoration: true`,
  requested -26.5 (the entry) and achieved -26.6, and
  `named_stage_restoration` reading `policy: "entry"`, `restored: true`. The
  restoring write lands last, which is the ordering the limb existed to prove.
  Suite on `main` after the merge: **1850 passed / 99 skipped / 3 warnings**,
  1949 collected, coordinator-run.

  The paragraph below is the pre-close record and is superseded: the limb is no
  longer owed.

- **~~One limb was not run: `restore: "entry"`.~~** All three runs used `"leave"`.
  What that leaves unproven on a rig is narrow — `restore_named_stage()` has one
  call site, and the `"leave"` reports could only read `last_known_um` = 473.7
  *after* all 18 callbacks, so the timing that mattered is demonstrated and any
  write it makes inherits it. What is **not** rig-proven is the restoring write's
  ordering, the reserved-write budget accounting, and the axis actually returning
  to P; those are covered off-rig by the parametrized ordering test only.
  **`restore: "entry"` therefore ships having never moved a real stage** — recorded
  here rather than implied.
- **Rig corroboration for 52b**: `get_device_property_info("TIRF Stage",
  "Position")` returned *"Invalid property name encountered: Position"*,
  independently confirming why 52b's design/49 refusal limb had to retarget.
- **Two runbook checks misreported that passing run** and are corrected on the
  branch: the export grep matched `raise RuntimeError` in inlined library source,
  and Step 4 demanded position/XY identity a single-position timelapse does not
  have. Consequently **the round-1 `where()`/`where_event()` fix is not
  distinguishable on this acquisition shape** — both yield `position: null` here.
  It is proven off-rig only; a multiposition run would be needed to see it.
- **The schema cost three of four rig attempts.** `hook_action_plan` was typed as
  loose objects, so the agent guessed `{"type": …}` and `{"kind": …, "params":
  {…}}` before finding the right shape. Now a discriminated schema with a `kind`
  enum, `additionalProperties: false`, and refusals that name the accepted shape.
  The round-1 defect had been that every named-stage audit record went through
  `where()` instead of `where_event()` and carried no frame identity.
- **Two coordinator fixes sit on top of the runner's work.** A hooked run with
  `reservation=None` — which `run_adaptive_survey` produces whenever `adaptive`
  is false — crashed its own failure path with `AttributeError` after I asked for
  a defensive `getattr` to be removed; it now reports `None` rather than a frame
  count nothing measured. And **`move_named_stage` now emits**, because it was
  still undecorated and sits on this gate's own export path — the third time that
  shape would have killed a gate. **Undecorated tools 12 → 11**; `CLAUDE.md`'s
  count is corrected at the step-10 design gate.

- **52a branches from the merge that carries this checklist**, so the runner's
  tree holds the spec it is being held to. Both the 50b and 51a runners reported
  their design file absent from the start commit they were given; this removes
  that papercut. The ledger row recording that start commit necessarily lands
  after it — do not chase the tip.
- **The open question is settled: value-only actions** (§"How the hook names its
  target"). Operator decision, 2026-08-15, before assignment. The stubs and the
  `hook_action_plan` example in this document were corrected to match; anything
  elsewhere showing a `device` on an action is stale.
- **The gate rig is M2, not M5** — operator decision 2026-08-15, on access. All
  three blocks gate on the same rig and the same declared named stage. The design
  above was written from an M5 session and still names `Thorlabs ELL17/ELL20`
  throughout; **those are recorded history and off-rig fixture values, not
  instructions to the gate rig.** M2's TIRF axis is a SmarAct 1D and the rig has
  never been run in TIRF mode; §Blocks explains why the gate is still valid and
  what it therefore may not claim.
- **The M2 precheck is done and 52a's preconditions are met** (§Blocks). The one
  finding that changed a gate: `TIRF Stage` has no position property, so 52b's
  refusal limb retargets to `TIRF Stage.Frequency` / `PIZStage.Position`.
- **Two runbook facts learned from capturing the precheck**, both PowerShell, not
  Microclaw: `> file 2>&1` writes **UTF-16LE**, so prefer
  `2>&1 | Out-File -Encoding utf8 <path>`; and a `NativeCommandError` record
  naming the tool's own startup banner is **not a failure** — both precheck
  commands succeeded and wrote their artifacts while printing one.
- **`inspect-rig` records no stage travel limits**, so a sweep interval cannot be
  planned from `inventory.json` alone. The declared `named_stages` bounds are the
  policy envelope; where the axis currently sits needs a live `get_position`
  read in the runbook.
- **Nothing about the rig change touches `microclaw/`.** If any block's diff
  needs to know which stage it is driving, that is the defect — the envelope
  names the device and the config bounds it.
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

> **Ticked at closeout, 2026-08-17.** These rows stayed unchecked when 52a merged
> on 2026-08-17 (`00c1763`) — a bookkeeping miss, not outstanding work: the block
> passed five M2 rig trips, its ledger row is closed, its design gate is merged
> and its notes are in `design/prompts.md`. They are ticked from that recorded
> evidence rather than re-verified line by line, plus a spot-check of the
> load-bearing ones against `main` at closeout: both actions are in
> `_ACTION_TYPES`, `move_named_stage` emits, `run_timelapse`/`run_zstack` accept
> all three arguments while multiposition and tile accept none, the envelopes
> validate by exact key set, and `restore` is required rather than defaulted.

**Implementation**

- [x] `MoveNamedStage(position_um)` joins `_ACTION_TYPES`; `parse_action` accepts
      the dataclass and the dict form and refuses unknown fields, booleans,
      NaN/infinity and non-finite values. No `device` field — the envelope names
      the target.
- [x] `named_stage_envelope` is validated in `_configure_hook_capabilities`
      beside `illumination_envelope`, by exact key set
      `{device, min_um, max_um, max_writes, restore}`. **Extend that function; do
      not add a second validator, registry or guard pass.**
- [x] Envelopes apply to saved hooks only: composed and non-saved hooks refuse,
      matching `illumination_envelope`'s existing behaviour.
- [x] One `CONFIRM_FN` summary before `Acquisition(...)`, folded with the
      illumination and dose confirmations rather than added beside them. Declining
      performs no acquisition and no write. Nothing confirms from the hook thread.
- [x] Dispatch is `move_named_stage`'s sequence (`tools.py:2023`) with the
      envelope check prepended: envelope device, envelope interval,
      `guard.check_named_stage`, `set_position`, `wait_for_device`, read back,
      record requested/achieved/error. It **does not** call
      `authorize_property_write`, because the live tool does not either.
- [x] The budget decrements on **every attempted dispatch**. This is a count of
      writes, not illumination's increase-only ratchet — do not copy that branch.
- [x] A refused or failed motion **aborts before the next exposure** and returns
      the dataset path, log path, frames exposed and last known hardware state.
      Do not copy illumination's continue-on-failure behaviour.
- [x] `restore` is required, not defaulted by the parser. `"leave"` writes
      nothing on exit and names entry and last value (design/38 F9);
      `"entry"`/`{"value": ...}` go through the same guard/write/read-back path,
      pass the envelope before approval, and **reserve a write** from
      `max_writes` — a plan that would consume the reservation is refused before
      acquisition. A failed restoration is reported loudly and never called
      success.
- [x] `hook_event_index` is assigned monotonically before each event is yielded
      and is a **plain event key**. It must never appear in `event["axes"]` — an
      index unique per event makes the NDTiff Cartesian product the dataset
      squared, which is the M5 2026-08-11 sparse-axis defect made worse.
- [x] `pre_hardware_hook_fn` is wired by the trusted adapter and consumes exactly
      the action set bearing the current event's index, applies it, verifies, and
      stamps requested and achieved state into that event. Missing, duplicate or
      wrongly indexed aborts **before** exposure. It never guesses from callback
      arrival order. Saved code still receives no queue, no callback, no `ctrl`,
      no `core`, no `guard`, no setter.
- [x] `hook_action_plan` is a tool argument, never a `hook_params` value, using
      the JSON dict forms `parse_action` accepts. Validation requires exactly the
      index set `0..len(events)-1` — no missing, no duplicate — after event
      generation and before confirmation. An empty action list is explicit.
- [x] A fixed-plan runner refuses a hardware action returned from `analyze_frame`
      as unsupported and writes nothing; `ContinueSurvey` keeps its documented
      no-op.
- [x] `run_timelapse` and `run_zstack` accept `named_stage_envelope` and
      `hook_action_plan` and pass every argument through. **The hookless route is
      untouched**: no `hook_strategy` still means `_emit_acquisition`.
- [x] `run_multiposition_acquisition` and `run_tile_acquisition` accept neither
      argument, and their existing saved-hook `CannotEmit` stands unchanged.
- [x] `_emit_adaptive` carries both new arguments and installs the same
      coordinator and action class, inlined with `inspect.getsource` and **never
      re-written in the emitter**. Emitter fallbacks are the tool's own defaults,
      not constants.
- [x] `hook_docs.py` / `get_hook_documentation` / `describe_hook` / the runner
      schemas are updated together, with the predetermined-runs bullet from
      §"Documentation and model behavior", and the categorical "a saved hook
      cannot do that" advice removed.

**Evidence — written before the fix, failing first**

- [x] No envelope means the existing behaviour: the proposal is refused and
      nothing is written. A declined confirmation means no acquisition and no
      write.
- [x] Envelope interval and configured `named_stages` bounds intersect; **both**
      boundaries are inclusive and one value outside each is refused **without a
      core write**.

      > **Corrected 2026-08-15, coordinator error.** This row required an
      > intersection with "driver limits" as well. There is no such source in
      > this path and the block must not invent one: microclaw queries
      > `get_property_lower_limit` — a **property** limit — and never MMCore's
      > stage travel limits, `move_named_stage` deliberately consults only
      > `check_named_stage`, and the gate rig's TIRF axis exposes no position
      > property at all, so no property-limit route to its travel exists either.
      > `inspect-rig` records no stage limits, so there is not even an offline
      > source. §"Approval and bounds are different controls" still holds where
      > Micro-Manager *does* report limits; for a named stage on this rig it does
      > not. The 52a runner found this and preserved the live tool's sequence,
      > which was right.
- [x] A dispatch calls `check_named_stage`, moves the labelled device, waits,
      reads back, records the achieved position, and never touches the core XY/Z
      setters.
- [x] **An achieved position outside the approved interval, from a requested one
      inside it, is recorded and reported and does not refuse.** Use the rig's
      numbers: request 21294 against a ceiling of 21294, achieve 21299.
- [x] An unapproved target and a plan that would consume the restoration
      reservation are **refused during validation, before the acquisition
      starts** — so there is no partial dataset on that path. A bridge failure
      mid-run still returns the partial dataset path, the frames exposed and the
      last known state.

      > **Corrected 2026-08-15**, from the round-1 implementation. Validating
      > every planned target up front is better than the mid-run framing this row
      > was written with, and it stands: nothing moves before the refusal.
      > It follows that a fixed plan's write budget **cannot** be exhausted
      > mid-run, since `planned_writes + reserved > max_writes` is refused during
      > validation. Test that refusal; genuine mid-run exhaustion is 52c's
      > adaptive path, not this one. A test written for the unreachable case
      > cannot fail, which block 46 established is worth zero.
- [x] Actions are attached by `hook_event_index`, not arrival order: delay frame
      N's image processing and prove event N+1 is not yielded or exposed with a
      missing or stale action set.
- [x] **A fixed-plan runner never takes a next-frame hardware action from
      `analyze_frame`**: install frame 0 and 1 moves through `hook_action_plan`,
      delay frame 0's analysis past frame 1's pre-hardware callback, prove both
      planned moves land; then return a different `MoveNamedStage` from frame 0
      and prove it is refused without changing frame 1.
- [x] `hook_event_index` is absent from `event["axes"]` on every yielded event,
      and a dataset from an indexed run exports through `export_dataset_as_tiff`
      with the frame count it acquired.
- [x] Entry-state restoration succeeds through the same checks; a failed
      restoration is reported and never described as success.
- [x] Timelapse and Z-stack export the envelope and the indexed plan; the script
      compiles, defines every name it uses, imports nothing from `microclaw`, and
      contains no `# NOT EMITTED` and no `raise RuntimeError`. An export carrying
      the envelope but dropping the plan is the silent no-op this row exists to
      catch.
- [x] A hookless timelapse still emits through `_emit_acquisition`.
- [x] Full suite green at or above the baseline measured on the start commit,
      re-run by the coordinator rather than accepted from the report.

**Process**

- [x] Runner prompt written to the scratchpad; the user is asked before any agent
      starts. Not committed.
- [x] Implementation reviewed from the diff, through as many returned rounds as
      it takes.
- [x] Runbook `design/52-block52a-rig-gate.md` written **on the block's branch**,
      with literal PowerShell-safe commands and expected values — not criteria —
      and the implementation pinned by `git merge-base --is-ancestor <commit>
      HEAD`.
- [x] Branch pushed to `origin` (`GIT_SSH_COMMAND="ssh -i ~/.ssh/yonce"`). No PR.
- [x] **Rig gate 52a on M5**, TIRF limb 1, run by the user. Never simulated.
- [x] Findings fixed on the same branch, sized to the finding, and re-gated.
- [x] Merged to `main`, `main` pushed, branch deleted locally and on `origin`;
      `git log --oneline origin/main..main` empty.
- [x] Ledger row closed and coordination notes added to `design/prompts.md`.
- [x] **Step-10 design gate** merged before 52b is assigned. It owes three
      corrections: `CLAUDE.md`'s "Twelve tools are still undecorated" becomes
      eleven and names `move_named_stage`; this document's §Timing prose is
      reconciled to what M2 measured; and the `design/35` register row is ticked
      down to what 52b and 52c still owe.

### 52b — the general bounded property

**Implementation**

- [x] `SetDeviceProperty(value)` joins `_ACTION_TYPES` with the same parse
      refusals as 52a's action. No `device`, no `property` — the envelope names
      both.
- [x] `property_envelope` validates by exact key set in
      `_configure_hook_capabilities`, with the categorical and numeric key sets
      **mutually exclusive** so `set(envelope) != allowed` still decides validity
      in one line. No wildcard device, property or value. `restore` required, as
      in 52a.
- [x] `authorize_property_write` runs **unchanged**. The envelope is not a route
      around the map: an excluded or unclassified pair refuses regardless of
      approval, for design/49's reason.
- [x] The write reuses the public property tool's capability-aware bounds
      checks — typed actuator range/unit, stage, exposure, illumination,
      categorical domain. Do not reproduce them in the adapter.
- [x] `check_device_property` (`safety.py:1002`) is split so the bounds/type
      validator stays mandatory while the approved envelope replaces the
      allow/deny **policy** decision. Follow the distinction the function already
      draws for typed and illumination pairs rather than inventing one, and state
      in the report exactly which branch was cut. **The ordinary
      `set_device_property` path is unchanged.**
- [x] A frame's actions are applied in order, waiting per write, stopping
      immediately if a write **raises**; the whole set is then verified in **one
      pass** with `_verify_property`'s semantics (`Float` numerically for MM's
      `"10"` → `"10.0000"`, everything else exactly). Design/53's distinction is
      the rule: *the device rejected this* is knowable per write, *this is not
      consistent yet* only at the end. **At one pair per envelope the verified set
      has one member** — build the two-pass shape anyway, because it is the shape
      a multi-pair envelope extends, and do not manufacture an interdependence to
      test it.
- [x] `ctrl.refresh_gui()` after the write, as `set_device_property` does — the
      EMU repaint behaviour, not a new general claim.
- [x] Micro-Manager-reported limits or allowed values narrower than the reviewed
      config are intersected and shown before approval. An unbounded numeric
      property may be approved only as an exact finite value set, and the dialog
      says Microclaw has no independent range to verify.
- [x] `run_timelapse`, `run_zstack` and their emitter carry `property_envelope`;
      multiposition and tile still accept nothing new.

**Evidence — written before the fix, failing first**

- [x] Categorical, bounded numeric, exposure, illumination, and an approved pair
      that the ordinary raw property path excludes.
- [x] **`SetDeviceProperty` on a bounded stage device's position property refuses
      at `authorize_property_write`** with design/49's message naming
      `move_named_stage`. This is a pass, not a gap.
- [x] An approved in-bounds write is not refused merely because the hook
      provenance is `saved_untrusted` or because the action is hardware motion.
- [x] ~~**Two interdependent actions in one frame both apply, then verify.**~~
      **Retired 2026-08-17**, unreachable at one pair. Replaced by: a frame's
      action set applies in order and a write that **raises** stops the set before
      the next write, with verification running afterwards rather than between
      writes. Assert the observed call sequence against a fake; do not build an
      interdependence the envelope cannot express.
- [x] A configured categorical set and a run-approved subset combine by
      intersection; a historical categorical exclusion does not veto the exact
      approved action.
- [x] Approval audit includes hook hash, exact envelope, write budget,
      acquisition plan, decision and operator identity; one changed byte or
      envelope field invalidates a session grant.
- [x] An exported session that dispatched a property action contains no
      `# NOT EMITTED` and no `raise RuntimeError`, compiles, and reproduces the
      envelope and its checks.
- [x] Full suite green at or above the 52a baseline, coordinator-re-run.

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
      refocused tile and refuse the move; neither performs it. **Cardinality is a
      hardware-association rule** (corrected 2026-08-17, see §"Evidence and
      gates"): a result carrying no hardware action keeps `main`'s sequential
      behaviour, so `RequestAutofocus, ContinueSurvey` still defers the
      `ContinueSurvey` and still routes the scan when the refocus is refused.
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
- **Eleven tools remain undecorated for script export**, measured over
  `TOOL_REGISTRY` by the coordinator on 2026-08-17 and agreeing with `CLAUDE.md`:
  `calibrate_snr_threshold`, `calibrate_stage_to_camera`, `center_feature`,
  `export_dataset_as_tiff`, `find_features`, `run_mda`,
  `run_multiposition_with_autofocus`, `set_emu_laser_power_percentage`,
  `shutter_declared_illumination`, `snap_to_album`,
  `verify_emu_laser_power_calibration`. (This row read "twelve, measured
  2026-08-12" until 52a decorated `move_named_stage`; corrected here rather than
  left to disagree with `CLAUDE.md`.) These blocks add no tools; if one appears,
  it is decorated in the block that adds it.
