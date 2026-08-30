# Adaptive streaming hooks for STORM

## Finding

Amr's 2026-08-29 M2 session did not fail because the agent overlooked hooks.
The agent called `list_hooks`, said that Phase II needed a new hook, called
`get_hook_documentation`, and then discovered that the hook action vocabulary
and the acquisition runners do not expose the same capability.

The requested Phase II loop was:

1. acquire continuously at one field;
2. count blinks over a three-frame window;
3. change `Laser Trigger.Duration0 (us)` from that measurement;
4. stop after the 50,000 us terminal condition plus 1,000 frames, with a hard
   cap of 200,000 frames.

MicroClaw can currently do each ingredient in isolation, but no runner combines
them for a single-position streaming acquisition:

| Capability | Fixed `run_timelapse` | `run_adaptive_survey` |
|---|---|---|
| Analyze each returned image | yes | yes |
| Image-derived `SetDeviceProperty` | no | yes |
| Image-derived `StopSurvey` | no | yes |
| Stay at one field and append time events | fixed events only | no; selects spatial survey events |
| Continuous hardware-sequenced time burst | yes, with `interval_s=0` | no |

This is why the hook route did not work. A generated hook implements
`analyze_frame(image, metadata) -> HookResult`, but it has no controller or
event queue and cannot write hardware directly. In a fixed timelapse,
`UntrustedHookAdapter` explicitly refuses `MoveNamedStage` and
`SetDeviceProperty` returned by `analyze_frame` with:

> fixed-plan hardware actions must come from hook_action_plan, not analyze_frame

The only accepted property changes in that runner are supplied before the run
as a fully expanded, axes-indexed `hook_action_plan` and bounded by a
`property_envelope`. That mechanism is intentionally open-loop: every action is
known before the first image exists. In the same fixed-run path,
`ContinueSurvey` is an accepted no-op and `StopSurvey` is refused as unsupported.

`run_adaptive_survey` has the missing trusted-parent handoff. It leaves its event
source open, lets `analyze_frame` select the next event, registers associated
hardware actions before publishing that event, and lets `StopSurvey` close the
stream. But its candidate set is a finite list of XY positions. Its control
contract means “submit the next tile,” not “submit the next time point at this
same field.” Using it for STORM would be an accidental encoding of time as
space, with incorrect axes, progress reporting, budgets, and protocol semantics.

There is a second, independent constraint. With `interval_s=0`, pycro-manager
may hardware-sequence the entire time axis as a list. No Python callback runs
between those exposures, so a software hook cannot change `Duration0` on a
per-frame basis in that burst. MicroClaw correctly refuses a per-frame
`hook_action_plan` in this mode. A feedback-controlled run therefore needs
software-dispatched events (and honest timing/latency reporting), or a hardware
sequence that encodes the feedback—which is impossible when later values depend
on images not yet acquired.

## Documentation defect

The SMLM skill currently says density monitoring can “optionally signal
end-of-acquisition” and tells the agent to offer a hook for adaptive density
control. That text describes the desired scientific workflow, not the shipped
runner capability. The hook-authoring skill is more accurate: it says fixed
hooked acquisitions refuse `StopSurvey`, and image-derived property actions are
available only through the adaptive survey.

That contradiction produced the confusing conversation. The agent first did
exactly what the SMLM skill requested—offered to write a hook—then retracted the
offer after reading the lower-level contract. Its final refusal was technically
correct, but it made hooks appear broken rather than revealing that the missing
piece is an adaptive time-series runner.

The session later demonstrated that the guarded property mechanism and M2
hardware were not the problem. A 200-frame open-loop test successfully applied
planned `Duration0` writes with requested/achieved read-back and restoration.
What remains unsupported is deriving those writes from the just-acquired images
and ending the same timelapse from the hook's decision.

## Recommended design

Add a first-class adaptive streaming time-series path. This could be named
`run_adaptive_timelapse`, or be an explicit adaptive mode of `run_timelapse`.
It should reuse the adaptive survey's trusted-parent queue and authorization
machinery without inheriting its spatial semantics.

The event flow should be:

1. Before acquisition, build and authorize the maximum possible dose: exposure,
   channel/illumination state, `max_frames`, runtime/disk estimate, property
   envelope, attempted-write budget, and restoration policy.
2. Seed exactly one event with axes `{"time": 0}`.
3. Analyze its returned image in the saved, hash-pinned hook.
4. Require one routing decision for the next step: `ContinueTimelapse` or
   `StopAcquisition` (new names are preferable to spatial `ContinueSurvey` and
   `StopSurvey`). Permit bounded hardware proposals such as
   `SetDeviceProperty` beside `ContinueTimelapse`.
5. In trusted parent code, resolve the current time index, create exactly one
   next event with `time + 1`, register its ordered hardware actions, and only
   then publish it to the open event stream.
6. In `pre_hardware_hook_fn`, apply, wait for, read back, and audit the property
   write before exposing the new event.
7. On stop, cap, hook failure, engine failure, or abort, close the adaptive
   handoff, restore the property according to policy, close the reservation,
   and return truthful planned/acquired/exposed/action counts.

This is mostly a generalization of mechanisms already present in
`_survey_event_stream`, `SurveyProgress`, `configure_adaptive`,
`_queue_adaptive_candidate`, `pre_hardware_hook_fn`, and property restoration.
The important change is to separate generic adaptive event dispatch from the
survey-specific policy that chooses among positions.

For Amr's hook, a stateful `analyze_frame` would keep a bounded three-frame blink
count, propose the next `Duration0` within the envelope, track the first frame at
which 50,000 us is reached, and return `StopAcquisition` after another 1,000
frames. `max_frames=200000` remains a parent-enforced cap; the hook must never be
the authority for dose limits.

### Tool surface

A dedicated tool should make the distinction explicit. At minimum it needs the
ordinary timelapse arguments plus:

- `hook_strategy`, `hook_params`, and `log_path`;
- `max_frames` rather than an unconditional fixed frame count;
- `property_envelope` and other already supported bounded capabilities;
- `max_idle_s` for a stalled analysis/dispatch watchdog;
- a software-dispatch cadence parameter whose description does not imply
  hardware-sequenced maximum speed;
- `laser_slot` trigger preflight, because this is an SMLM path.

The result should distinguish `stop_reason` (`hook_condition`, `max_frames`,
`abort`, `hook_failure`, or `engine_failure`) and report `frames_acquired`,
`frames_exposed`, property-action counts, restoration, and measured duration.

Do not implement this as a compressed form of `hook_action_plan`. A schedule
syntax would solve the later open-loop ramp from the session, but it cannot
express image feedback or conditional stopping. It may be useful separately,
but it is not the answer to the original request.

## Safety and concurrency requirements

The saved hook must remain untrusted: no `ctrl`, core, credentials, paths, or
native pycro-manager event queue. It proposes typed decisions only. The trusted
parent remains responsible for authorization, write budgets, bounds, read-back,
logging, and restoration.

The implementation must not perform controller bridge calls from the image
callback thread. The existing design warns that pyjavaz serializes bridge calls
behind one lock; prompting or writing hardware directly from that callback can
deadlock. The callback should register a decision and publish a candidate to the
parent-owned stream. The pre-hardware callback then executes the authorized
action immediately before the next event, following the existing adaptive
survey pattern.

The tool must refuse `interval_s=0` (or define it as software-dispatched with a
different name) whenever image-derived actions are enabled. Its confirmation
and result should disclose measured inter-frame gaps. For a 50 ms STORM
exposure, a few milliseconds of software overhead may be scientifically
acceptable, but MicroClaw must measure and report it rather than promise a
nominal 0.1 or 1 ms interval that bridge latency cannot honor.

## Documentation changes

Until the adaptive time-series runner ships, change the SMLM skill to say:

- observation-only density logging is supported on `run_timelapse`;
- fixed, predeclared property schedules are supported with a nonzero interval
  and a bounded `hook_action_plan`;
- image-driven property changes plus conditional stop are not supported in one
  continuous MicroClaw timelapse today;
- `run_adaptive_survey` is not a substitute for single-field STORM;
- do not offer to write an adaptive STORM hook unless the compatible runner is
  available.

After the runner ships, replace that warning with a concrete
`run_adaptive_timelapse` example and make the hook-authoring capability table
list it separately from fixed-plan acquisitions and spatial surveys.

## Tests and rig gate

Unit and fixture coverage should prove:

1. only the seed is dispatched before its image decision;
2. each `ContinueTimelapse` publishes exactly one monotonically indexed event;
3. a property proposal is registered before publication and applied/read back
   before the corresponding exposure;
4. `StopAcquisition` publishes no further event and closes without waiting for
   the full cap;
5. `max_frames` wins even if a hook always continues;
6. missing, duplicate, late, malformed, or over-budget decisions fail closed;
7. batched/hardware-sequenced events are refused before the first exposure;
8. hook exceptions, property-write failures, aborts, and engine failures restore
   the property and close the reservation once;
9. dataset time axes are dense and never overwrite a prior frame;
10. export reproduces the adaptive program or emits a specific refusal—never a
    plausible fixed trace.

On M2, first gate an observation-only short run, then a dark or non-dosing
property simulation if the rig permits it, and finally a short authorized test
using `Duration0` with a deliberately simple image predicate. Record requested
and achieved values, frame identity, stop reason, restoration, actual cadence,
and prove that no exposure occurs after the accepted stop decision. A full
STORM sample is not required to validate the mechanism.

## Scope

This proposal does not change code, generated hooks, rig configuration, or
authorization policy. It identifies the missing runner and the documentation
correction needed to make the user-facing promise match the capability.
