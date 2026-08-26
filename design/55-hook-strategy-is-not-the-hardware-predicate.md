# `hook_strategy` is not the predicate for "this run moves hardware"

## Problem

On the Nikon, 2026-08-18, Microclaw ran four TIRF-angle sweeps that moved
nothing. Every frame of every sweep was exposed at whatever angle the stage
happened to be parked at, and every one of them returned
`{"status": "Timelapse complete."}`. Two were scored as real measurements before
the operator caught it.

Evidence: `~/Documents/Documents - Beyonce/Projects/Micro-Claw/nikon-failed-tirf-hook`,
history lines 63, 67, 79, 89. The call shape, verbatim from line 89:

```json
{"n_frames": 3, "interval_s": 0, "exposure_ms": 100, ...,
 "named_stage_envelope": {"device": "TITIRF", "min_um": 1000, "max_um": 6000,
                          "max_writes": 3, "restore": "entry"},
 "hook_action_plan": [
   {"hook_event_index": 0, "actions": [{"kind": "MoveNamedStage", "position_um": 1000}]},
   {"hook_event_index": 1, "actions": [{"kind": "MoveNamedStage", "position_um": 3500}]},
   {"hook_event_index": 2, "actions": [{"kind": "MoveNamedStage", "position_um": 6000}]}]}
```

There is no `hook_strategy`. In `run_timelapse` (`microclaw/tools.py:3210-3221`)
the entire envelope path is nested inside `if hook_strategy:`

```python
    hook = None
    log_path = _prepare_log_path(guard, log_path) if hook_strategy else None
    if hook_strategy:
        try:
            hook = _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path)
        except ValueError as exc:
            return {"error": str(exc)}
        try:
            _configure_hook_capabilities(
                hook, ctrl, guard, save_dir, name, illumination_envelope,
                artifact_limits, named_stage_envelope, hook_action_plan, events,
                property_envelope=property_envelope,
            )
```

so `named_stage_envelope` and `hook_action_plan` are **discarded without a
word**. No coordinator is built, no `pre_hardware_hook_fn` is attached, nothing
moves, and the run reports success. `run_zstack` is byte-identical
(`microclaw/tools.py:3048-3062`).

Reproduced off-rig against fakes with exactly the line-89 call:

```
RESULT: {'status': 'Timelapse complete.', 'dataset_path': '/tmp/ds'}
set_position calls: []
```

That is the same result dict the Nikon returned, field for field — no
`named_stage_restoration`, no envelope report, no error.

The rig proved it independently. Same three angles, same ROI, minutes apart:

| TITIRF | hook "sweep" | manual `move_named_stage` + snap |
| --- | --- | --- |
| 1000 | SNR 13.71, cov 0.064 | **SNR 2.59, cov 0.001 (blank)** |
| 3500 | SNR 12.90, cov 0.062 | **SNR 2.56, cov 0.0008 (blank)** |
| 6000 | SNR 13.13, cov 0.061 | SNR 13.58, cov 0.066 |

All three sweep frames are the 6000 reading — the entry position.
`move_named_stage` itself is fine: `error_um: 0.0` on all thirty-odd direct
calls in that session. Only the hook route no-ops.

The historical call also declared `max_writes: 3` with `restore: "entry"`.
That budget is itself one write short: three planned moves plus restoration
requires `max_writes: 4`. The omission bug hid this second planning error too,
because the envelope and plan never reached validation. 55a must refuse the
historical call for the missing hook; 55b's runnable version below corrects the
budget to four rather than claiming that the verbatim call can restore.

### The cost is fabricated data, not a missing feature

The first two sweeps were read as measurements. The fine sweep reported SNR
8.74 / 8.77 / 8.83 / **8.86** / … across nine "angles" and picked 6900 as the
peak. That spread is noise on nine identical frames. A TIRF angle was within one
step of being locked in from it. The failure was caught only because the operator
cropped the FOV, which moved the parked angle out of signal and made an entire
sweep come back blank while the same angle was visibly bright by hand.

### Why the existing guard never fired

Design/52 put the right refusal in the right place for the wrong precondition.
`UntrustedHookAdapter.pre_hardware_hook_fn` (`hook_decisions.py:576-595`) refuses
a multi-event hardware-sequenced batch, exactly as `hook_docs.py:104` documents.
But it is a method on the coordinator, and no coordinator exists when no hook is
attached. **A guard that lives on an object the failing path never constructs is
not a guard.**

There is a second, independent hole underneath. `interval_s=0` is the
hardware-sequencing shape (`CLAUDE.md` §"three contracts we got wrong"), so even
*with* a `hook_strategy` this call could not have swept — it would have raised
inside the hook thread and surfaced through `_hooked_failure_result`, after the
acquisition had started. Nothing refuses `hook_action_plan` + `interval_s == 0`
at planning time, and nothing in the tool schema tells the caller that a
per-frame plan needs a nonzero interval.

### Four sites, one mistake

`hook_strategy` is being used as a proxy for "does this run move hardware". It
is not that predicate. The sites are:

1. `tools.py:3210` / `:3048` — envelope and plan configuration.
2. `tools.py:3211` / `:3049` — the hook log path, so a plan-only run has no audit
   trail even in principle.
3. `tools.py:3187` / `:3029` — the reserved per-position nesting refusal. A
   plan-only run reaching `_run_protocol_at` would synthesize a coordinator and
   walk straight past `if _reservation is not None and hook_strategy:`. The
   reserved path is reachable: `_run_protocol_at` forwards the caller's
   `protocol_params` into the tool as `**params` (`tools.py:4632`), so
   `hook_action_plan` can arrive there today.
4. `_emit_timelapse` / `_emit_zstack` (`tools.py:1212-1227`) — emitter routing.
   `if params.get("hook_strategy")` chooses `_emit_adaptive` over
   `_emit_acquisition`. Under the comprehensive fix below, leaving this alone
   would export a hardware-moving run as a plain timelapse: a standalone script
   that silently differs from the run it claims to reproduce, which is the exact
   defect class `CLAUDE.md` §"A tool that takes a hook has two emitters" exists
   to prevent.

### Why the suite is green

Every test exercising `hook_action_plan` passes `hook_strategy="saved"`
(`tests/test_hook_illumination_artifacts.py:148, 303, 441, 763`). The omitted
case is untested. `run_adaptive_survey` carries `hook_strategy` in its schema
`required` list, so it cannot reach this path at all — measured, not assumed.
`run_timelapse` and `run_zstack` do not:

```
run_zstack           | required: ['z_start_um', 'z_end_um', 'z_step_um', 'save_dir']
run_timelapse        | required: ['n_frames', 'interval_s', 'save_dir']
run_adaptive_survey  | required: ['protocol', 'save_dir', 'hook_strategy']
```

## Decision

Two fixes. **Both, in order.** They are not alternatives: 55a stops the program
lying, 55b gives back the capability 55a refuses.

### 55a — minimal: nothing that would move hardware is silently dropped

Move the `_configure_hook_capabilities` call **out** of the `if hook_strategy:`
block in both tools — and ahead of `set_exposure`, per "One guard, moved" below
— so it runs on every acquisition and sees `hook=None`. The refusal it needs is
already written — `tools.py:5250` raises
`"Hook envelopes apply only to saved generated hooks."` for any non-adapter hook
— it is simply never reached. Split that message so the `hook is None` case names
the omission.

Define one canonical capability-name set at module level, then exercise every
name through every refusal path in parameterized behavioral tests. The
validator itself (55a), the reserved-run check in each tool and the
multiposition preflight (both 55b) all refuse on this set.

```python
# microclaw/tools.py
#: Every run argument a hook -- or, under 55b, a plan-only coordinator -- must
#: be present to carry. Canonical on purpose: parameterized tests drive every
#: name through every refusal path, so a capability added here but not at a
#: refusal site is a loud failure rather than another silent hole.
#: Add a new capability HERE FIRST. The matrix iterates this tuple, so a
#: capability that never lands here generates no case at all and is silently
#: unguarded -- the one hole the tests cannot close for you.
HOOK_CAPABILITY_ARGS = (
    "illumination_envelope", "artifact_limits", "named_stage_envelope",
    "property_envelope", "hook_action_plan",
)
```

Be honest about what the constant does and does not buy. A site holding these as
individual **parameters** must still spell them out; only a site reading a dict
(the multiposition preflight below) can iterate the names directly. Do not reach
for `locals()` to close that gap — it makes a refusal depend on the local
variable names of the function it sits in. The constant instead supplies the
parameters for behavioral tests: for every name, pass a non-`None` value through
each relevant entry path and assert refusal before mutation. Adding a sixth
capability then extends the test matrix automatically and fails until every
refusal path recognizes it. This tests behavior rather than inspecting local
dictionaries through brittle source or AST parsing.

```python
# microclaw/tools.py — run_timelapse shown; make the corresponding reorder in
# run_zstack, whose preamble has no laser_slot/trigger preflight
    trigger_preflight = None
    if laser_slot is not None:
        trigger_preflight = _verify_trigger_line_armed(ctrl, laser_slot)
    if channel:
        _check_acquisition_channel(ctrl, guard, channel)
    if exposure_ms is not None:
        guard.check_exposure(exposure_ms)

    # Moved up. _build_acquisition_events is pure, and everything above this
    # point only reads the rig (_verify_trigger_line_armed and
    # _check_acquisition_channel are get_property/guard checks), so the whole
    # preamble can precede the one guard without reordering any mutation.
    events = _build_acquisition_events(
        channel=channel, exposure_ms=exposure_ms,
        num_time_points=n_frames, time_interval_s=interval_s,
    )
    hook = None
    log_path = _prepare_log_path(guard, log_path) if hook_strategy else None
    if hook_strategy:
        try:
            hook = _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path)
        except ValueError as exc:
            return {"error": str(exc)}
    try:
        # Unconditional, and ahead of the only mutation in the preamble: an
        # envelope or plan arriving with no hook to carry it must refuse, not
        # vanish, and not after changing the camera. Nikon, 2026-08-18.
        _configure_hook_capabilities(
            hook, ctrl, guard, save_dir, name, illumination_envelope,
            artifact_limits, named_stage_envelope, hook_action_plan, events,
            property_envelope=property_envelope,
        )
    except _HookArtifactBudgetError as exc:
        return {"error": str(exc)}

    # Moved down, and it is the ONLY line that moves relative to a mutation.
    if not channel and exposure_ms is not None:
        ctrl.core.set_exposure(exposure_ms)
```

```python
# microclaw/tools.py — _configure_hook_capabilities, replacing the bare
# `if not isinstance(hook, UntrustedHookAdapter)` refusal at :5246
    if not isinstance(hook, UntrustedHookAdapter):
        supplied = {                      # behavior-tested over HOOK_CAPABILITY_ARGS
            "illumination_envelope": illumination_envelope,
            "artifact_limits": artifact_limits,
            "named_stage_envelope": named_stage_envelope,
            "property_envelope": property_envelope,
            "hook_action_plan": hook_action_plan,
        }
        if any(value is not None for value in supplied.values()):
            raise ValueError(
                "hook_action_plan and hook envelopes are executed by a hook and "
                "have no effect without one; pass hook_strategy."
                if hook is None else
                "Hook envelopes apply only to saved generated hooks."
            )
        return
```

Presence is deliberately tested with `is not None` for every capability. An
empty object is present but malformed and must reach its normal validation (or
the no-hook refusal); truthiness would preserve a smaller version of the same
silent-drop defect for `{}`.

And the sequencing refusal, at planning time, before any hardware mutation:

```python
# microclaw/tools.py — run_timelapse, immediately after save_dir resolution and
# before trigger/channel preflight or set_exposure
    if hook_action_plan is not None and n_frames > 1 and interval_s == 0:
        raise ValueError(
            "interval_s=0 lets the engine hardware-sequence the time axis, and a "
            "sequenced burst runs with no software between exposures, so a "
            "per-frame hook_action_plan cannot be honoured. Pass a nonzero "
            "interval_s to disable time-axis sequencing."
        )
```

This is deliberately conservative: `interval_s == 0` is necessary for time-axis
sequencing but not sufficient — the engine sequences only if the camera can, and
whether it can is a rig fact Microclaw cannot know off-rig. Refusing
deterministically at plan time is right; the runtime refusal in
`pre_hardware_hook_fn` stays as the backstop for the shapes we cannot predict
(z-sequencing, channel sequencing).

### One guard, moved, rather than two that must agree

The presence refusal must land before `ctrl.core.set_exposure` (`tools.py:3204`),
which today runs before the events are even built — so a request certain to
refuse would change the camera first. There are two ways to get that, and this
block takes the second:

- **An early presence check in each tool, with `_configure_hook_capabilities`
  left where it is.** Two refusal sites for one rule inside one function, both
  enumerating the capability set, both needing to agree forever.
- **Move the single authoritative call ahead of the mutation.** Chosen. It works
  because nothing in the preamble mutates except `set_exposure`:
  `_verify_trigger_line_armed` (`tools.py:3098`) reads `get_property`,
  `_check_acquisition_channel` (`tools.py:2251`) is guard checks and a
  `_channel_source` read, and `_build_acquisition_events` is pure. So the only
  line that changes position relative to a mutation is `set_exposure` itself.

It also removes a refusal site rather than adding one. The first option would
have made four sites refuse on the capability set; there are three, and one of
them is the validator itself. Only two of the three hand-list the names — the
multiposition preflight reads a dict and iterates the tuple directly.

The cost is real and small: `CONFIRM_FN` is now asked before the acquisition's
own later failures can occur, so an operator can be prompted to authorize an
envelope for a run that then fails for an unrelated reason. No hardware is
touched by that prompt. It is arguably the better order anyway — the entry
position `_configure_hook_capabilities` records for restoration is now captured
before anything in the run has touched the rig, rather than after the camera has
been reconfigured.

No emitter change is owed by 55a: after it, no session can record a plan-only
call, so the `hook_strategy` routing predicate stays correct.

### 55b — comprehensive: a fixed plan is a program and needs no hook

A `hook_action_plan` is already a complete, closed, declarative program. Every
action, its envelope, its write budget, its restoration policy and its
authorization are stated before the first exposure and validated by
`_configure_hook_capabilities` (`tools.py:5418-5476`). Nothing in it needs a line
of hook code. Requiring the caller to write, register and hash-pin a hook that
does nothing, purely to carry a plan that analyses nothing, is a usability defect
of the kind `CLAUDE.md` §"Microclaw is easy to use" names: a feature that needs a
paragraph of explanation before it can be called.

The thing the operator wanted — a saved, single-dataset, envelope-bounded,
restored, exportable angle sweep — is strictly better than the `move_named_stage`
+ `snap_and_analyze` fallback the agent recovered with. That fallback works, but
it is display-only (nothing is saved), it costs a bridge round trip per point,
and it exports as a sequence of unrelated calls rather than one sweep.

So: let the plan stand alone, by handing it the coordinator it already needs.
`UntrustedHookAdapter` **is** that coordinator, and it already tolerates a
payload with no `analyze_frame` — `image_process_fn` guards on
`hasattr(self.hook, "analyze_frame")` (`hook_decisions.py:1156`), and
`UntrustedHookAdapter(object())` is already used as a fixture
(`tests/test_hook_illumination_artifacts.py:260`). No new class, no new layer.

```python
# microclaw/hook_decisions.py
#: Payload for a run whose hardware program is fully specified by
#: hook_action_plan. It analyses nothing; the adapter's image_process_fn
#: already no-ops on a payload with no analyze_frame.
PLAN_ONLY = object()
```

```python
# microclaw/tools.py — run_timelapse, and identically run_zstack
    carries_plan = hook_action_plan is not None
    hook = None
    log_path = (_prepare_log_path(guard, log_path,
                                  default=f"{save_dir}/{name}_plan_log.jsonl")
                if carries_plan else
                _prepare_log_path(guard, log_path) if hook_strategy else None)
    if hook_strategy:
        try:
            hook = _resolve_hook(ctrl, guard, hook_strategy, hook_params, log_path)
        except ValueError as exc:
            return {"error": str(exc)}
    elif carries_plan:
        hook = UntrustedHookAdapter(PLAN_ONLY, log_path=log_path)
    try:
        _configure_hook_capabilities(...)          # unchanged, from 55a
    except _HookArtifactBudgetError as exc:
        return {"error": str(exc)}
```

The default is **not optional** for a plan-only run: `_prepare_log_path` returns
`None` for a falsy path (`tools.py:5050-5051`), so merely routing the existing
optional value through it would still leave the Nikon call with no audit trail.

It arrives as a `default:` keyword on `_prepare_log_path` rather than as a new
`_next_plan_log_path` helper. That function already resolves the path in the
workspace and creates its parent; a fallback is one parameter on it, where a
second helper is a second thing to call and a call site that must remember to
call it first — the two-functions-that-nearly-agree defect `CLAUDE.md`
§"Fold into what exists" names. An explicitly supplied `log_path` continues to
win, which is what `log_path or default` reads as:

```python
# microclaw/tools.py — _prepare_log_path
def _prepare_log_path(guard: SafetyGuard, log_path: str | None,
                      *, default: str | None = None) -> str | None:
    defaulted = not log_path and bool(default)
    log_path = log_path or default
    if not log_path:
        return None
    ...                                    # resolve + mkdir, unchanged
    # Only a defaulted path is suffixed. Two plan-only runs sharing a save_dir
    # and name write two datasets (pycro-manager suffixes the directory) and
    # must not interleave into one log. An explicitly supplied path is the
    # caller's to collide.
    return _next_free(log_path) if defaulted else log_path
```

The suffixing rule is not new. `_next_available_log_path` (`tools.py:786`)
already states it — but as a *source string* emitted into standalone scripts,
so it cannot be called from the live path and cannot simply be reused. The live
default therefore restates the same rule, and the implementer should say in the
report whether the two can be made one thing; a rule written twice is a rule
that drifts.

Plan-only coordinators are not implicitly allowed through the reserved
per-position protocol path. The existing nesting refusal must use the same
capability predicate:

```python
    carries_hardware_capability = any(   # behavior-tested over HOOK_CAPABILITY_ARGS
        value is not None for value in (
            illumination_envelope, artifact_limits, named_stage_envelope,
            property_envelope, hook_action_plan,
        )
    )
    if _reservation is not None and (hook_strategy or carries_hardware_capability):
        raise ValueError(
            "A hook or hook hardware plan cannot be nested in a reserved "
            "per-position protocol. Pass run_multiposition_acquisition("
            "hook_strategy=...) instead; it uses one Acquisition and one hook "
            "log across every position. A hook_action_plan has no such route: "
            "its indices address one run's events."
        )
```

The existing message's second sentence is kept, not dropped — it is the
actionable half and is still correct for the hook case. The plan case has no
equivalent route, and saying so is better than a refusal that names no way
forward.

Supporting a fresh coordinator, confirmation, restoration cycle and audit log
inside every reserved position would be a separate multiposition design. Until
then, refuse it rather than bypassing the nesting rule merely because the
coordinator was synthesized instead of named.

That inner refusal is a backstop, not the first guard. `_run_protocol_at` moves
XY (and sometimes Z) before it calls `run_timelapse` or `run_zstack`, so
`run_multiposition_acquisition` must inspect `protocol_params` before entering
the position loop:

```python
    # hook_strategy joins the shared set here and only here: multiposition has
    # its own top-level hook argument, so naming it inside protocol_params is
    # always a mistake.
    supplied = [key for key in ("hook_strategy", *HOOK_CAPABILITY_ARGS)
                if params.get(key) is not None]
    if supplied:
        return {"error":
                "protocol_params cannot carry per-run hook capabilities in a "
                "reserved multiposition protocol: " + ", ".join(supplied)}
```

`hook_strategy` normally belongs to the top-level multiposition argument, where
the tool deliberately switches to one Acquisition and one log. The hardware
envelopes and fixed plan have no top-level multiposition route today. Keep the
inner `_reservation` check as defense in depth for direct internal calls, but
the outer preflight is what makes the refusal occur before the first position
move.

55a's `hook is None` refusal survives and narrows to what it should always have
covered: an envelope with neither a hook nor a plan to spend it.

The emitter predicate moves with it, or the standalone script drops the sweep:

```python
# microclaw/tools.py
def _emit_timelapse(params: RecordedParams) -> str:
    # The predicate is "does this run move hardware", not "is there a hook".
    if params.get("hook_strategy") or params.get("hook_action_plan") is not None:
        return _emit_adaptive(params, "timelapse", "timelapse")
    return _emit_acquisition({
        "num_time_points": params["n_frames"],
        "time_interval_s": params["interval_s"],
    }, params, "timelapse")
```

`_adaptive_hook_export` (`tools.py:797`) currently raises
`CannotEmit("the record contains no single hook strategy")` for a plan-only
record. It gains that case: empty hook source, a self-contained `object()`
payload in the constructor, and `saved=True` for the envelope check at
`tools.py:925`. The live runner can use the module-level `PLAN_ONLY` sentinel,
but the standalone constructor must be self-contained:

```python
UntrustedHookAdapter(object(), log_path=_log_path)
```

The sentinel's identity carries no contract; only the absence of
`analyze_frame` matters. Emitting `PLAN_ONLY` with empty hook source would leave
the standalone script referencing a name it never defines —
`UntrustedHookAdapter` itself is inlined (`_adaptive_runner_source`,
`tools.py:676`), so the sentinel would be the only dangling name. The suite
already owns a guard for exactly this; see the Evidence item that opts 55b
into it.

**The emitter must take the log path from the result, not the input, or the
standalone script silently loses the audit trail.** `RecordedParams` is
"recorded input with the matching append-only tool result attached"
(`tools.py:90`), so a log path the *tool* defaulted never appears in `params`.
`_emit_adaptive` reads `log_name = Path(recorded_log).name if recorded_log else
None` (`tools.py:965`) and would emit `_log_path = None` — a plan-only run that
writes a log live and none in the script exported from it. `_adaptive_result`
already puts `log_path` in the result, so the emitter reads
`params.get("log_path") or params.result.get("log_path")`, the same
emit-what-actually-happened move `set_channel` makes for its recorded effects.
This is `CLAUDE.md` §"an emitter's fallbacks are the *tool's* defaults, not
constants", one layer down: here the fallback is not even a constant the emitter
could copy, because the tool computes it.

### Which I prefer, and why

**Ship 55a first and on its own.** It is a moved call, a reorder past a single
line, a split message, a refusal, and the constant plus behavioral matrix that
keep the capability set honest; it needs no rig time to be worth having; and
until it lands, any session can produce a plausible dataset for hardware that
never moved. That is the worst failure mode this program has — worse than a
missing capability, because a
refusal costs an operator five minutes and this cost the Nikon session an hour
and nearly a wrong TIRF angle. If only one fix is ever done, it is this one.

**Then do 55b, and treat it as the one that actually closes the design.** 55a
leaves the capability design/52 built reachable only by writing a hook that does
nothing — which nobody will do, so in practice 55a converts a silent wrong answer
into a dead end. 55b remains contained (one coordinator branch, one sentinel, a
mandatory default on the existing log-path helper, the reserved-run refusal
predicate, one emitter predicate and one export branch), it reuses the
coordinator rather than adding one, and it is the reading of `hook_action_plan`
that the schema already implies and that the model reached for unprompted on the
first rig it met.

### Rejected

- **Make `hook_strategy` required whenever an envelope is present, in the JSON
  schema.** JSON Schema `dependentRequired` would refuse this at the API
  boundary, but the tool must refuse it too — `run_timelapse` is also called
  directly from tests and from `_run_protocol_at` (`tools.py:4632`), the
  per-position runner behind `run_multiposition_acquisition`. Schema-only is a
  guard in one of three doorways.
- **Refuse `interval_s == 0` for every hooked run**, not only planned ones. An
  observation-only hook in a sequenced burst is fine and is the ordinary SMLM
  shape (`snr_observer` over 30000 frames); only a per-frame *hardware action* is
  impossible there.
- **Detect the sequencing at runtime only, and keep no plan-time check.** That is
  today's design, and today's design starts the acquisition before refusing.
- **Give the plan its own executor** rather than reusing `UntrustedHookAdapter`.
  A second path that moves hardware under an envelope is the two-functions-that-
  do-almost-the-same-thing defect, and it would need its own audit log, its own
  restoration and its own emitter.
- **Silently attach a coordinator under 55a**, without 55b's design work. It
  would fix the Nikon call and quietly leave the emitter routing wrong, exporting
  a script that does not sweep. Half of 55b is worse than none of it.

## Evidence

Write each of these before the fix and watch it fail for the stated reason.

**55a**

- `run_timelapse` with `named_stage_envelope` + `hook_action_plan` and no
  `hook_strategy` refuses, and `core.set_position` is never called. This is the
  line-89 call; today it returns `{"status": "Timelapse complete."}` with an
  empty call list.
- The same for `run_zstack`, and the same for `illumination_envelope`,
  `property_envelope` and `artifact_limits` alone.
- **The unattached-capability refusal lands before `ctrl.core.set_exposure`, in
  both tools.** Assert `set_exposure` was never called, not only that
  `set_position` was not. This is the one test of "One guard, moved" — without
  it the reorder that justifies that whole section ships untested, and a later
  refactor could put `set_exposure` back above the guard with a green suite. It
  is also the only reason `run_zstack` exercises the reorder at all, since the
  `interval_s == 0` bullet below is `run_timelapse`-only (`run_zstack` has
  neither `n_frames` nor `interval_s`).
- `hook_action_plan` with `interval_s == 0` and `n_frames > 1` refuses **before**
  channel/exposure mutation or `Acquisition` construction — assert
  `set_exposure` and the acquisition fake were never called, not merely that an
  error was returned.
- `n_frames == 1` with `interval_s == 0` and a one-entry plan still runs: a
  single event is not a burst.
- A hookless plain `run_timelapse` / `run_zstack` is unchanged, and a hooked run
  with a plan is unchanged — the existing four tests at
  `tests/test_hook_illumination_artifacts.py:148, 303, 441, 763` stay green
  untouched.
- **Every name in `HOOK_CAPABILITY_ARGS` is refused by the validator.**
  Parameterize the `_configure_hook_capabilities` no-hook refusal over the
  canonical tuple: for each name, pass a non-`None` value with no
  `hook_strategy` and assert refusal before `set_exposure`. 55a introduces the
  constant, so 55a owes the first matrix; shipping the tuple with nothing
  driving it is how it goes stale.

**55b**

- The line-89 call corrected to `interval_s > 0` and `max_writes: 4` drives
  `core.set_position("TITIRF", …)` for 1000, 3500, 6000 in event order and
  restores to entry after the `with` block — the design/52 restoration contract,
  on a run with no hook.
- With no explicit `log_path`, its collision-free default hook log exists and
  records three accepted named-stage writes with `achieved_um`, and the
  restoration record. A hardware-moving run with no audit trail is not
  acceptable merely because no hook authored it.
- The envelope still refuses out-of-bounds targets, still counts the write
  reserved for restoration, and still requires `CONFIRM_FN`.
- **The exported script is exec'd against fakes and its `pre_hardware_hook_fn`
  driven per event**, asserting the three `set_position` calls and the
  restoration — not merely that it compiles. `CLAUDE.md` §"An exported script
  that compiles is not an exported script that works": two of block 52b's three
  rig trips died on defects that survived compilation and every grep.
- A hookless run still emits through `_emit_acquisition` and its script contains
  no adaptive runner.
- **A plan-only record is added to the parametrization of
  `test_emitted_inline_defines_every_name_it_uses`**
  (`tests/test_session_script_export.py:2065`). That test checks free names
  structurally through `_undefined_emitted_names`, so it catches an emitted
  `PLAN_ONLY` — but only for record shapes it is given, and its own docstring
  says to add a param whenever the exporter learns to inline something new. 55b
  teaches it a new shape. Without this the block-13/41b `NameError`-on-the-rig
  defect has no regression guard on the one path 55b creates.
- **The exported script of a plan-only run with no explicit `log_path` still
  writes a log.** The tool's default never appears in the recorded *input*
  (`RecordedParams`, `tools.py:90`), so an emitter reading only `params` emits
  `_log_path = None` and the standalone run loses the audit trail the live run
  kept. Assert the emitted source contains a real log name, not `None`.
- Two plan-only runs sharing a `save_dir` and `name` write two logs, not one
  interleaved file.
- A plan or envelope passed through `_reservation` refuses before confirmation,
  acquisition construction or hardware motion; a plain reserved acquisition is
  unchanged.
- `run_multiposition_acquisition` rejects hook capabilities nested in
  `protocol_params` before its first XY/Z move. The inner `_reservation` refusal
  remains tested independently as a backstop.
- Empty `{}` values for every envelope/budget are treated as present and refuse
  rather than being silently ignored.
- **55a's matrix is extended to the two refusal paths 55b adds** — the
  reserved-run check in each tool and the multiposition preflight — so every
  name in `HOOK_CAPABILITY_ARGS` is driven through all three. For each name,
  pass a non-`None` value and assert refusal before exposure, acquisition
  construction or position motion as appropriate. A sixth capability therefore
  extends the matrix automatically and fails until every path recognizes it; no
  source or AST inspection of local dictionaries.
## Blocks

### 55a — an unattached hardware plan refuses

Design: "55a — minimal", "Rejected", "Evidence → 55a".
Files: `microclaw/tools.py` (`HOOK_CAPABILITY_ARGS`, `run_timelapse`,
`run_zstack`, `_configure_hook_capabilities`),
`tests/test_hook_illumination_artifacts.py`.

**Gate: the demo machine.** Runbook `design/55-block55a-demo-gate.md`, on the
block's branch. The Nikon gate this section used to name is not bookable — see
§"The gate plan, 2026-08-26" below for what replaced it and why the substitution
costs this block nothing.

### 55b — a fixed plan stands alone

Design: "55b — comprehensive", "Rejected", "Evidence → 55b".
Files: `microclaw/tools.py` (`run_timelapse`, `run_zstack`, `_emit_timelapse`,
`_emit_zstack`, `_adaptive_hook_export`, `_prepare_log_path`, `_emit_adaptive`,
`HOOK_CAPABILITY_ARGS` and both the outer and inner reserved-run refusals),
`microclaw/hook_decisions.py` (`PLAN_ONLY`),
`microclaw/tools_schema.py` (both descriptions),
`tests/test_hook_illumination_artifacts.py`,
`tests/test_session_script_export.py`.

**Gate: the demo machine (Part A) plus one short limb on M2 or M5 (Part B).**
Runbook `design/55-block55b-gate.md`, on the block's branch.

Step-10 design gate: record in `CLAUDE.md` §"The pycro-manager acquisition
engine" that a guard belonging to a coordinator is only as reachable as the
coordinator, and in `design/52-approved-hook-property-actions.md` that a fixed
plan no longer implies a hook.

---

# Checklist — coordinated 2026-08-26

**Process is `CLAUDE.md` §"The block workflow", which is authoritative.** This
section owns *what* the blocks are, their gates and the ledger. `design/55` is
not tracked in `design/35`; this file tracks it, as design/48–design/57 each
track their own.

Two blocks, **in sequence, not in parallel** — both touch `run_timelapse` and
`run_zstack`. Implementation is delegated to headless Codex through the project
`codex-runner` skill, one linked worktree per block.

## The gate plan, 2026-08-26 — why neither gate is on the Nikon

**The operator has lost access to the Nikon.** The two rig gates written above
name `TITIRF` and a five-point TIRF-angle sweep; neither can be run. Available
machines are the **demo machine** (stock `MMConfig_demo.cfg` plus the `Aux Z`
DStage that block 4c's setup added), **M2** and **M5**.

The substitution is close to free, and it is worth stating exactly why rather
than asserting it:

- **55a costs nothing to move.** It is entirely refusals — no motion, no dose,
  no optics. The demo machine runs a real MMCore, a real bridge and a real
  acquisition engine, which is everything 55a's refusals sit in front of.
- **55b's mechanism moves with it too.** Its evidence is requested-versus-
  achieved position per event, a restoration record, an audit log, a set of
  envelope refusals and an exported script that re-executes the program. `Aux Z`
  is a real Micro-Manager stage device driven over the real bridge; every one of
  those is observable on it.
- **One thing does not move — but less of it than this section first claimed.
  Corrected 2026-08-26 by 55a's own gate.** The Nikon criterion was *SNR rising
  and then falling across the five frames*: an **optimum**, the optical proof
  that a TIRF angle sweep found an angle. No machine now available produces an
  optimum — M2 "has never been run in TIRF mode" in its own gate's words
  (`design/52-block52a-rig-gate.md`), which is why 52a, the block that first
  shipped this sweep, declared "it does not prove anything optical" and passed
  anyway.

  **What this section got wrong is the demo machine.** It said the demo camera's
  frames are bit-identical whatever the stage does, and used that to rule out an
  optical limb entirely. Measured on 2026-08-26 from 55a's two datasets: they are
  bit-identical across *time* — a plain 10 ms timelapse gives mean **3276.219**
  on all three frames — and they are **not** identical across `Aux Z`. The sweep
  gave **858.705 / 327.285 / 327.174** at 40 / 90 / 140 um, with frames 1 and 2
  agreeing to their last figure (min 70, max 584 on both) and frame 0 differing
  from both, when nothing but the planned axis varied between them. So **the demo
  machine can carry an optical corroboration limb**: not an optimum, but a
  falsifiable response, and three identical frames would be 55b failing. It is a
  witness the camera writes, independent of the log the code under test writes.
  55b's Part A Step 3a is that limb, and it *measures* the response rather than
  assuming this one — three frames are an inference, not a curve.
- **What M2/M5 add instead is timing, and that is why Part B exists.** `Aux Z` is
  simulated and arrives instantly, so a demo-only gate never exercises
  `settle_stage_move` inside a hook on a device that takes real time to move —
  `CLAUDE.md` §"A device that is not busy is not a device that arrived". Part B
  is one sweep on a real motorized named stage and nothing else.

**Every number in both runbooks is a literal.** `Aux Z` is bounded 0–200 µm in
the demo `named_stages`, so the plan targets are written as `40`, `90`, `140`
rather than computed from a position the operator pastes in. 52c's strictest
criterion produced no rig evidence because it shipped as
`Select-String -Pattern "<t2>", "<t3>"` and was run verbatim; nothing in either
runbook is a placeholder to substitute.

## 55a — an unattached hardware plan refuses

Implementation:

- [ ] `HOOK_CAPABILITY_ARGS` added at module level in `microclaw/tools.py`.
- [ ] `_configure_hook_capabilities` is called **unconditionally** in both
      `run_timelapse` and `run_zstack`, with `hook=None` when no hook resolved.
- [ ] The call sits **ahead of `ctrl.core.set_exposure`** in both tools; the
      preamble reorder moves no other line relative to a mutation.
- [ ] The `not isinstance(hook, UntrustedHookAdapter)` refusal splits, and the
      `hook is None` branch names the omission and says `pass hook_strategy`.
- [ ] Presence is `is not None`, never truthiness — `{}` refuses.
- [ ] `hook_action_plan` with `n_frames > 1` and `interval_s == 0` refuses at
      planning time in `run_timelapse`, naming time-axis sequencing.
- [ ] No emitter change (no session can record a plan-only call after 55a).

Evidence, each watched failing on the pre-fix tree first:

- [ ] The line-89 call refuses and `core.set_position` is never called; same for
      `run_zstack`, and for each capability alone.
- [ ] The refusal lands **before `set_exposure`** in both tools — asserted on
      `set_exposure`, not only on `set_position`.
- [ ] `interval_s == 0` + `n_frames > 1` + plan refuses before channel/exposure
      mutation **and** before `Acquisition` construction.
- [ ] `n_frames == 1`, `interval_s == 0`, one-entry plan still runs.
- [ ] Parameterized matrix over `HOOK_CAPABILITY_ARGS` through the validator.
- [ ] The four existing tests at `tests/test_hook_illumination_artifacts.py:148,
      303, 441, 763` stay green **untouched**.

Gate (demo machine), `design/55-block55a-demo-gate.md`:

- [ ] Step 0 — pin by ancestry, install, full suite, zero failures.
- [ ] Step 1 — `Aux Z` present and bounded `0–200` in `named_stages`; stop and
      fix the config here if not, so every later number stays literal.
- [ ] Step 2 — the line-89-shaped call refuses naming `hook_strategy`;
      `Aux Z` reads the same before and after; **no dataset directory created**.
- [ ] Step 3 — the same call with `hook_strategy` set and `interval_s=0` refuses
      at planning time; no dataset directory.
- [ ] Step 4 — the camera's `Exposure` property is **unchanged** across a refused
      call that asked for a different exposure. This is "One guard, moved",
      proved on hardware rather than on a fake.
- [ ] Step 5 — each of the five capabilities alone refuses.
- [ ] Step 6 — an ordinary `run_timelapse` and an ordinary `run_zstack` still run
      and save.
- [ ] Step 7 — `export_session_script` over that session parses, the refused
      calls appear as skipped, the plain runs emit.

## 55b — a fixed plan stands alone

Implementation:

- [ ] `PLAN_ONLY` sentinel in `microclaw/hook_decisions.py`.
- [ ] Both tools build `UntrustedHookAdapter(PLAN_ONLY, log_path=...)` when a
      plan is present and no `hook_strategy` is.
- [ ] `_prepare_log_path` gains `default:`; a **defaulted** path is suffixed for
      collision, an explicit one is not. The report says whether this rule and
      `_next_available_log_path` (`tools.py:786`) can be made one thing.
- [ ] The inner reserved-run refusal in both tools uses the capability predicate,
      and keeps the existing message's second sentence.
- [ ] `run_multiposition_acquisition` refuses `hook_strategy` or any
      `HOOK_CAPABILITY_ARGS` name inside `protocol_params` **before** the
      position loop's first move.
- [ ] `_emit_timelapse` / `_emit_zstack` route on "does this run move hardware",
      not on `hook_strategy`.
- [ ] `_adaptive_hook_export` handles the plan-only record: empty hook source,
      a self-contained `object()` in the constructor, `saved=True`.
- [ ] `_emit_adaptive` reads the log path from **the result** when the input
      carries none.
- [ ] `microclaw/tools_schema.py` descriptions for both tools say a plan needs no
      hook and needs a nonzero `interval_s`.

Evidence:

- [ ] The corrected line-89 call (`interval_s > 0`, `max_writes: 4`) drives
      `set_position` for its three targets **in event order** and restores after
      the `with` block.
- [ ] With no `log_path`, a collision-free default log exists and records each
      accepted write with `achieved_um`, plus the restoration record.
- [ ] Out-of-bounds refuses; the restoration write is counted in the budget;
      `CONFIRM_FN` is still required.
- [ ] **The exported script is `exec`'d against fakes and its
      `pre_hardware_hook_fn` driven per event** — the three writes and the
      restoration, not merely that it compiles.
- [ ] A hookless run still emits through `_emit_acquisition`, no adaptive runner.
- [ ] A plan-only record added to `test_emitted_inline_defines_every_name_it_uses`.
- [ ] The exported script of a defaulted-log run emits a **real** log name.
- [ ] Two plan-only runs sharing `save_dir` and `name` write two logs.
- [ ] `_reservation` refuses before confirmation, acquisition or motion; a plain
      reserved acquisition is unchanged.
- [ ] Empty `{}` refuses for every envelope and budget.
- [ ] 55a's matrix extended to both refusal paths 55b adds.

Gate Part A (demo machine), `design/55-block55b-gate.md`:

- [ ] Step 0 — pin, install, full suite.
- [ ] Step 1 — `Aux Z` present, bounded `0–200`, and its entry position recorded.
- [ ] Step 1a — **park `Aux Z` at 20 µm before Step 2, not at 0.** 55a's gate
      ran with entry `0` and a restore target of `0`, which makes restoration
      indistinguishable from never having moved to any witness outside the log
      the code under test writes. A non-zero entry fixes that for free.
- [ ] Step 2 — a 3-frame `run_timelapse`, `interval_s=2`, **no `hook_strategy`**,
      plan `40 / 90 / 140` µm, envelope `min_um: 0`, `max_um: 140`,
      `max_writes: 4`, `restore: "entry"`. One confirmation, one dataset, a
      `named_stage_restoration` in the result, a `log_path` in the result, and
      `Aux Z` back at **20**, not at a target the sweep visited.
      **The envelope's `min_um` must contain the restore value**, and its
      `max_writes` must be plan length **plus one** — 55a's gate spent two
      refusals discovering both, so write them as literals here.
- [ ] Step 3a — **the optical corroboration limb, new 2026-08-26.** Score the
      three frames of Step 2's dataset with `run_analysis_on_saved_dataset` and
      record the per-frame mean. **Three identical means is this block failing**;
      the frames must differ, because the only thing that varied across them is
      the axis the plan moved. Report the numbers whatever they are — this limb
      measures the demo camera's response to `Aux Z`, which 55a's gate observed
      over three frames and no one has characterised.
- [ ] Step 3 — the log carries three accepted writes whose `achieved_um` equal
      `40`, `90`, `140` in event order, plus the restoration record. **Compare
      the log's last achieved value against `get_stage_position` afterwards**
      (`CLAUDE.md` step 6 — 52a's third gate was caught only by that
      disagreement).
- [ ] Step 4 — the same plan with `restore: "leave"`: afterwards `Aux Z` reads
      `140`, not the entry position. This is the **external** witness that the
      moves happened, independent of the log the code under test writes.
- [ ] Step 5 — two plan-only runs sharing `save_dir` and `name` leave two log
      files, not one interleaved file.
- [ ] Step 6 — refusals on hardware: a target of `250` (outside `0–200`);
      `max_writes: 3` with `restore: "entry"`; a declined confirmation. Each
      writes nothing and moves nothing.
- [ ] Step 7 — `run_multiposition_acquisition` with `hook_action_plan` inside
      `protocol_params` refuses **before the first XY move** (XY read before and
      after).
- [ ] Step 8 — `export_session_script`; grep the emitted file for the literals
      `40`, `90` and `140`, and for the absence of `_log_path = None`.
- [ ] Step 9 — run the emitted script standalone with Microclaw closed. It
      sweeps the same three targets, restores, writes its own log, and its log
      matches the live one record for record.
- [ ] Step 10 — an ordinary hookless `run_timelapse` in the same session still
      exports through `_emit_acquisition`; its script contains no
      `UntrustedHookAdapter`.
- [ ] Step 11 — **55a's corrected message, observed in a session rather than in a
      unit test.** Ask for an envelope-bounded run with *no* plan and no hook —
      the one shape 55b still refuses. Required: the agent's next call names a
      **saved** hook, not a precoded one. 55a's gate lost a round trip to the old
      wording, and a message is only fixed when the next reader takes the working
      route.

Gate Part B (M2 **or** M5 — whichever is free), same runbook:

- [ ] One 3-frame plan-only sweep on a real motorized named stage — `TIRF Stage`
      on M2 (`named_stages` `-10497.8 .. 6256.8`) or `Thorlabs ELL17/ELL20` on
      M5 (`named_stages` `0 .. 20000`) — `interval_s=2`, `restore: "entry"`.
      `achieved_um` within tolerance of each target on a device that takes real
      time to move, and the axis back at entry afterwards.
- [ ] **Recorded as untested, not inferred:** that the *image* changes across the
      sweep. Neither machine can show it — M2 has never been run in TIRF mode,
      and the demo camera's frames are bit-identical. If the Nikon ever comes
      back, that limb is the one thing owed.

## Run ledger

| Block | Branch | Start commit | Implementer | Gate | Merged | Design reconciled |
| --- | --- | --- | --- | --- | --- | --- |
| 55a | `design55/unattached-plan-refuses` (deleted) | `9128715` | Codex runner (worktree `../microclaw-55a`), impl `6a38ece`; runbook `fa3b48c` pins `6a38ece`; coordinator message fix `d9546db` | **PASS demo 2026-08-26** (`block55a-2026-08-26`), Steps 0–4; six correct refusals in Step 3, and one defect found and fixed — the refusal named `hook_strategy` and the session correctly tried a *precoded* hook | **`2eaa0e3`** 2026-08-26 | **done** — `CLAUDE.md` §engine gains a sixth contract; the demo-camera premise this checklist used to scope the gates is corrected above |
| 55b | — | — | — | demo + M2/M5 — not yet run | — | — |

**Baseline, coordinator-measured on `b54c30b` (macOS): 2135 passed / 99 skipped /
3 warnings.** After 55a: **2155 / 99** on macOS, `2129 / 124` on the demo machine
— same 2253 total. On Windows expect the same total with a different skip split
(2110 + 124 on the demo machine at the 9a gate). **Gate on zero failures, never
on the count.**

**The sequencing note this file used to carry is spent.** `design/54` is fully
merged — 54a/54b/54d/54e in `3be1037`, 54c in `1fa0eb6` — and both its branches
are deleted. **55a is merged (`2eaa0e3`), so 55b branches from plain `main` too**;
nothing else is in flight against these files.

**Every `tools.py` line number in the prose above is from 2026-08-19 and is
wrong. Find each site by name.** They have drifted twice already — once before
55a and again because 55a edited these very functions — so this note does not
quote replacements: any number written here is stale by the next block. The
sites 55b touches, by name, are `run_zstack`, `run_timelapse`,
`_configure_hook_capabilities`, `_prepare_log_path`, `_next_available_log_path`
(inside an emitted-source string, so it cannot be called from the live path),
`_adaptive_hook_export`, `_emit_adaptive`, `_emit_timelapse`, `_emit_zstack`,
`_run_protocol_at` and `run_multiposition_acquisition`.

**55a's shape is the thing to read before writing 55b**, because 55b rewrites the
same preamble: `_configure_hook_capabilities` is already unconditional and
already sits ahead of `core.set_exposure` in both tools, and 55b must keep both
properties while giving the `hook is None` case a coordinator to fill.

**55a's gate produced the refusal cascade 55b exists to remove**, and it is the
acceptance target as much as any test. In order, the session hit: the
`interval_s=0` sequencing refusal (**stays**); the no-hook refusal (**must stop
firing for a plan**); `Hook envelopes apply only to saved generated hooks` for a
precoded hook (**stays**); the write-budget refusal (**stays**); the
restore-outside-the-envelope refusal (**stays**); then had to write a hook that
does nothing. After 55b the first call in that cascade that carries a plan should
run.
