---
name: hook-authoring
description: Write and verify pycro-manager acquisition hooks and saved analysis adapters.
---

# pycro-manager hook API reference

Returned by `load_skill(name="hook-authoring")`.

## Integrating the user's own analysis

External analysis packages are integrated by writing an adapter hook, not by
adding a microclaw tool for each package. The adapter translates pycro-manager's
image/event contract into the package's existing interface and translates its
result into logging, filtering, acquisition branching, or guarded follow-up
events.

### What to ask the user

Start in biological/workflow language. Usually ask only these three questions,
one at a time if that is easier:

1. **What existing workflow do you use, and can you point me to it?** A package
   name or web page is enough; even better is the app, notebook, script, project,
   model, environment, or repository they currently run. Ask them to describe
   how they start it today (for example, “open this project and press Run”).
2. **Can you show me one example that works?** Ask for a representative input
   and its result. The user may explain the result biologically (“these outlined
   cells are the ones I want”); do not require them to name arrays, dtypes,
   coordinate systems, APIs, or command-line arguments.
3. **What should the microscope do with the result?** Examples: record a score,
   make a map, keep images containing the target, revisit detected objects for a
   z-stack, analyze several tiles together, or stop after enough targets are found.
   Ask for a threshold or acquisition budget only if their desired action needs one.

If they cannot provide a working example, ask for the smallest available substitute:
a screenshot of the expected result, a tutorial dataset, or permission to run the
workflow on a copy of one image. Ask follow-up questions only when investigation
cannot resolve an ambiguity that changes the biological meaning or hardware action.

### What microclaw must investigate

The following is an agent verification checklist, NOT a questionnaire for the
user. Derive it by inspecting supplied local files and environments, reading the
package's official documentation and source code (search the web when needed),
running `--help` or a documented example, and observing a safe dry run:

- entry point, installed version/environment, and model/project/config artifacts;
- processing unit (frame, tile group, time window, or completed dataset), latency,
  startup cost, and CPU/GPU needs;
- image axes/order, shape, dtype/range, channel mapping, pixel size, and preprocessing;
- exact raw output, coordinate convention and units, origin, and score semantics;
- the hook method/state/two-pass design needed for the requested microscope action;
- cleanup, thread safety, determinism, bounded failure behavior, and event limits;
- all files, processes, network behavior, and hardware boundaries;
- analyzer/model/project/version and parameters to record as provenance.

Prefer primary sources: the installed code and version, the package's official
documentation, and its upstream repository. Record where every inferred contract
fact came from. Never silently infer coordinates, channels, or confidence semantics
from a package name. To branch acquisition on a group of images, use microclaw's
`run_adaptive_survey` candidates-queue runner (below), NOT pycro-manager's native
`AcquisitionFuture.await_image_saved(...)` pattern: microclaw deliberately does not
hand hooks the `Acquisition` object, and detection stays inside the hash-pinned,
logged `analyze_frame` rather than a runner-thread wait loop, so branching stays
inside reviewed analysis. For per-image work after persistence, inspect the
installed callback contract before choosing `image_saved_fn`.

For completed data, use `run_analysis_on_saved_dataset` with a reviewed,
hash-pinned adapter from the saved manifest. Implement
`analyze_completed_dataset(dataset_view, selection, context)` for whole-selection
work, or `analyze_saved_frame(image, metadata, context)` for per-frame work.
Save either offline shape with `generate_and_save_hook(runner_contract="fixed")`
after source review and confirmation; `adaptive` requires a live `analyze_frame`.
Saving it does not make it attachable to an acquisition: `list_hooks` reports it
as resolvable, and its `route` says `run_analysis_on_saved_dataset`. Passing an
offline adapter as `hook_strategy` is refused by name.

The return contract is different from live `analyze_frame`: **do not return
`HookResult` from either offline verb**. `analyze_saved_frame` returns one
measurement dictionary or `None` per call. `analyze_completed_dataset` returns
an iterable of dictionaries/`None` entries, or `None` (not a single dictionary).
A bare measurement dictionary has no `result` key; the runner wraps it as the
result with status `unverified`. Even a key called `status` in that bare mapping
is just a measurement. A normalized envelope has a `result` key and may also
contain `status`, `analyzer`, `analyzer_version`, `parameters`, and
`artifact_sha256`; any other envelope key is an error. Its omitted status defaults
to `unverified`.

Alternatively, call `context.emit_observation(result, status="unverified", ...)`
with the same optional provenance fields, and return `None` to avoid reporting
the same observation twice. Saved adapters may claim only `unverified` or
`provisional`, through either channel. `observed` is reserved for reviewed
package adapters: a saved adapter claiming it raises `ValueError`, and the
runner records a failed analysis and the reason in its manifest. Invalid return
types and unknown envelope keys likewise fail the analysis.

The runner opens the `ndstorage.Dataset`; the whole-selection adapter receives a
read-only `DatasetView` restricted to the requested axis selection, with
`coordinates`, `read_image`, `read_metadata`, and bounded `as_array` access.
The context provides observations, cancellation and `context.artifacts`, an
`ArtifactDirectory` with `emit(filename, payload)`. It exposes no output path or
live hardware capabilities. With an array payload and a `.tif`/`.tiff` filename,
`emit` writes through `tifffile`, including multi-page TIFF movies; bytes are
written as supplied. Choose all intended time points, preserve frame order, and
verify the written movie before calling a retrospective annotation delivered.
Caller-settable `artifact_limits` default to:

```json
{"max_artifact_bytes": 67108864, "max_count": 64, "max_total_bytes": 268435456}
```

That is 64 artifacts, at most 64 MiB each and 256 MiB total, measured on the
encoded artifacts. Filenames must be bare names and must not collide.

This skill explains how to compose tools; it does not maintain a competing
capability declaration. Check the installed tool schema and the tool's own
refusal for availability and restrictions. Inconclusive discovery means “not
verified”, not “unimplemented”. An unwritten adapter is different from an
unavailable execution path; a missing dependency blocks the adapter that needs it.
Stateful streaming remains available when those boundaries do not fit.
`analyze_frame` itself does not receive a batch. The trusted adapter's
pre-hardware callback is not exposed to saved source.

No network call may occur while images are acquired. A local subprocess is allowed
only after its lint warning and full source are explicitly reviewed; derived XY/Z
events must pass the guard.

Once the technical contract is complete, summarize it in plain language and call
out only unresolved assumptions that affect scientific meaning or safety. Choose
the hook method below, write a plain saved-hook class, and test it without hardware
against the example. If the output cannot yet be verified, make the first version
observation-only: return raw output as measurements and do not drive acquisition. Show the full source
and lint warnings, and save it only after explicit confirmation. Package setup
belongs in the hook's documented local environment, not in a package-specific
microclaw analysis tool.

For a live observation-only version, normalize the verified part of the raw
output to JSON values in `HookResult.measurements`. Record the analyzer name
and installed version, parameters affecting the result, and the sha256 of any
model/project/config artifact. Use `status="unverified"` when axes, units, score
semantics, or coordinates remain unresolved. Do not turn an unverified record into
filtering, stage movement, early stopping, or follow-up acquisition.

## Acquisition callback availability

Pycro-manager's native `Acquisition(...)` constructor accepts all of these callback
kwargs, but Microclaw does not currently expose all of them through its runners.

Currently wired by Microclaw's acquisition runner for reviewed built-ins:

  image_process_fn       callable(image, metadata, event_queue) -> tuple | None
  pre_hardware_hook_fn   callable(event_or_events) -> same shape (trusted adapter only)
  post_hardware_hook_fn  callable(event_or_events) -> same shape (ALWAYS return it)

Pycro-manager supplies either one event dict or, when hardware sequencing is
active, a list of event dicts. A callback must return the same shape it received;
a one-element list is ordinary and must be handled. Per-frame planned hardware
actions cannot run between exposures in a multi-event hardware-sequenced burst,
so Microclaw refuses the fixed plan when any consecutive
``int(k * interval_s * 1000.0)`` deadlines match across its frame count.
Choose an interval whose truncated millisecond deadlines remain distinct.

``run_multiposition_acquisition`` accepts one hook name or an ordered list.
Post-hardware callbacks run in declared order and each receives the event
returned by its predecessor; returning ``None`` is a defect and aborts loudly.
Image observers receive independent pixel copies of the same original frame,
so mutations never flow between analyses, and a discard from any observer wins
(for storage only, never dose). The one-artifact-per-frame rule applies across
the composition: after the first accepted artifact, later proposals for that
frame are refused and audited. Artifact limits and the hook log are per run,
and composed log entries identify their ``hook_strategy``. Saved hooks are
still resolved independently, hash-checked, stripped of forbidden parameters,
and ``UntrustedHookAdapter``-wrapped; composition grants no controller, guard,
path, or event-queue capability.

Generated and user-saved hooks instead implement
`analyze_frame(image, metadata) -> HookResult | None`. They never receive ctrl,
guard, credentials, paths/directories, runner queues, or the pycro-manager event queue. Legacy saved
`image_process_fn` classes still load, but receive a raising event-queue stub and no
other capability. Only classes shipped in PRECODED_HOOK_REGISTRY are trusted built-ins.

Native pycro-manager callbacks not currently wired by Microclaw:

  event_generation_hook_fn callable(event) -> list[dict] | None
  image_saved_fn            callable(axes, dataset[, event_queue]) -> None

Do not implement or promise an unwired callback in a generated Microclaw hook. Adding
one requires runner plumbing and fixture/integration tests first. For wired callbacks,
only the methods actually implemented by the hook are passed to `Acquisition(...)`.

## Hook function signatures and return-value contracts

### analyze_frame(image: np.ndarray, metadata: dict) -> HookResult | None

The saved-hook contract. `HookResult` contains JSON-safe measurements and a list
or tuple of typed action proposals: MoveStage, MoveNamedStage, SetDeviceProperty, AcquireAt, SetExposure,
ContinueAcquisition, StopAcquisition, RequestAutofocus, SetIlluminationPower, EmitArtifact,
or DiscardFrame.
Runner support for control-flow proposals is:

  runner                              ContinueAcquisition   StopAcquisition / AcquireAt
  adaptive time series:               dispatch next frame   StopAcquisition supported;
    run_timelapse(n_frames=None,                              AcquireAt refused
      max_frames=..., hook_strategy=...)
  spatial survey:                     dispatch next tile    supported
    run_adaptive_survey
  fixed-plan hooked acquisitions      accepted noop         refused

A fixed-plan runner already continues through every committed event, so
ContinueAcquisition changes nothing and is recorded as an accepted noop. Proposals
that would change behaviour but are unavailable remain refused. The adaptive
runner guard-checks and reservation-checks every proposal and writes every
accept/refuse decision to the log.

For an adaptive time series, the saved hook's pinned registry source must
reference `ContinueAcquisition` or `StopAcquisition`; otherwise hook resolution
refuses before acquisition. That source check cheaply rejects the legacy shape
but does not prove the hook will return a decision. At runtime every image must
produce exactly one routing decision. A missing, duplicate, malformed, late, or
over-budget decision fails closed at that image; a missing decision on the first
image does not idle until `max_idle_s`.

Saved hooks cannot access hardware directly. For predetermined
``run_timelapse`` and ``run_zstack`` acquisitions, every named-stage action
comes from the acquisition call's ``hook_action_plan`` and is bounded by its
``named_stage_envelope`` before the run. Each generated event has one explicit
indexed action list, including empty lists. Events are dispatched before the
first image is analyzed, so ``MoveNamedStage`` returned by ``analyze_frame`` is
refused as unsupported; the hook scores frames and the plan moves hardware.
The action carries only ``position_um``. The envelope names the device, interval,
attempted-write budget, and explicit restoration policy, and the trusted parent
checks, moves, waits, reads back, and audits requested and achieved positions.

``SetDeviceProperty(value)`` is likewise available in predetermined
``run_timelapse`` and ``run_zstack`` plans. The acquisition call supplies one
exact ``property_envelope`` (device/property, categorical values or numeric
bounds, attempted-write budget, and restoration policy); the action supplies
only the string value. The live authorization map and property bounds still
apply, and multiposition and tile runs accept no such envelope.

Adaptive ``run_adaptive_survey`` hooks may propose ``MoveNamedStage`` or
``SetDeviceProperty`` beside the one action selecting the next event. The
trusted parent registers that ordered set by the selected event's axes and
applies it from ``pre_hardware_hook_fn`` within the approved envelope. The seed
has an explicit empty action set. ``hook_action_plan`` is rejected because later
events are selected at runtime.

AcquireAt(position) normally requests an immediate guarded revisit of that planned
tile. When run_adaptive_survey has acquire_on_hit, it instead records the planned
tile and the focus device's current Z once for a deferred acquire phase, bounded by
max_hits. Use AcquireAt beside RequestAutofocus when the later acquisition should
use the autofocus-converged plane.

Optional analyzer, analyzer_version, parameters, and artifact_sha256 fields retain
the `microclaw.analysis-observation/v1` envelope used by HookBase.log_analysis.
Saved hooks default to status `unverified` and may claim only `unverified` or
`provisional`; `observed` is not self-assertable by untrusted source. The parent
writes the envelope and the action audit.

There is deliberately no interactive confirmation from the acquisition callback
thread: pyjavaz serializes bridge calls behind one lock, so prompting there can
deadlock acquisition. A proposal outside the already committed reservation is
refused and logged, never escalated to a prompt.

SetIlluminationPower is power modulation only. The caller must authorize a device,
power property, ceiling, and accepted-write budget before the run; the parent reads
the initial value once, confirms the whole unattended per-frame run once, and checks
each proposal against that envelope and SafetyGuard. A hook cannot express a shutter
enable or turn light on. A fixed open-loop ramp that reads no image content could be
a reviewed PRECODED_HOOK_REGISTRY built-in; a UV level computed per frame from blink
density is feedback and must use the proposed-action union. This block does not add
such an open-loop built-in.

`max_power_step_factor` bounds the ratio between consecutive parent writes, so it
limits how fast power climbs rather than how high it can reach. From zero there is
no ratio constraint and the envelope ceiling is the only bound. The ratchet is a
backstop against a runaway, not the mechanism that makes a ramp gradual: a hook that
wants a gradual ramp implements it itself, as `uv_activation` does with
`step_percent`.

End-of-run power policy belongs to the hook and its user: a hook may leave the last
accepted value, restore a chosen value, or ramp down to zero. Microclaw does not add
an automatic final write. A non-increasing write within the authorized envelope is
always permitted even after the increasing-write budget is exhausted, and does not
consume that budget. An aborted or failed run may never reach the hook's intended
final frame, so hardware can remain at whatever value the last accepted write set.

EmitArtifact is constructed as ``EmitArtifact(filename, payload)`` (prefer the
unambiguous keyword form ``EmitArtifact(filename="result.tiff", payload=image)``).
The filename is bare, never a path; payload is bytes or an ndarray. Trusted
parent code confines and exclusively creates the file in the run artifact directory,
enforces per-file and per-run limits, hashes it, and records the path and sha256.
A HookResult may propose at most one artifact per frame; this keeps the observation's
single artifact_sha256 provenance field unambiguous.
Hook measurements are unverified claims and may describe an action that the parent
refused; the corresponding `hook_action` records are authoritative.

DiscardFrame returns None from the parent image processor after recording the
observation. The position is still moved to and still exposed: discard saves storage,
not dose: filtering happens after exposure, so it cannot reduce dose.

``RequestAutofocus`` is honored only by ``run_adaptive_survey`` for a saved or
generated hook when the caller supplies ``autofocus_budget``. The budget counts
camera exposures (sweep planes plus the refocused-tile re-exposure), and each
tile may be refocused once. A converged tile is shown to ``analyze_frame`` again
with ``metadata["microclaw_refocused"] is True``; the first look has False. The
trusted parent carries this flag rather than assuming Micro-Manager copies a
custom event key into image metadata. A failed sweep is logged and carried past
without widening or retrying it.

The second look is still an adaptive survey decision point: it must return
``ContinueAcquisition`` or ``StopAcquisition`` (or another supported routing action).
Without a hardware proposal, a routing action beside ``RequestAutofocus`` keeps
the survey alive if autofocus refuses or does not converge. If autofocus queues
a focused re-exposure, later actions are refused until those pixels are judged.
Named-stage/property actions paired with ``RequestAutofocus`` are always refused
with ``not dispatched until the refocused tile is judged``, regardless of order.

**A hook must never rely on ``RequestAutofocus`` to keep the survey moving.** It
is the only action that can be *granted* and still queue nothing: it is refused
when no budget was authorized, when the exposure budget is exhausted, when the
tile was already refocused, when the guard or the focus lock rejects the sweep —
and when the sweep is accepted but does not converge, which is a normal outcome
this capability is built around. In every one of those cases nothing is
dispatched, and a hook that returned ``RequestAutofocus`` alone has ended the
survey by omission: it idles out ``max_idle_s`` and reports a stall. On M5,
2026-08-11, a budget sized below a single sweep did exactly that and cost a
three-tile run after two tiles.

Ask for refocus and also provide the fallback route. If refocus is granted, the
fallback is deferred and the hook chooses again on the focused frame; if it is
refused, the fallback advances the scan::

    return HookResult(stats, actions=(RequestAutofocus(), ContinueAcquisition()))

On convergence the survey deliberately adopts the new focus plane. Timelapse
survey events carry no Z, so the refocused exposure and later tiles remain at
that Z; a non-converging sweep restores the entry Z. Both first and second looks
remain in the dataset: the re-exposure carries a ``refocus=1`` axis because
NDTiff otherwise indexes identical axes as one readable frame.

The live runner refuses a proposal while Micro-Manager's focus lock is engaged.
A standalone exported script has no generic focus-lock query, so it cannot make
that check: disengage the lock before running the script. It retains the same
recorded Z guard, exposure budget, one-refocus-per-tile rule, and focus sweep.
Precoded hooks use the direct runner contract and do not route typed actions
through the saved-hook adapter, so ``RequestAutofocus`` is not available to them.

### image_process_fn(image: np.ndarray, metadata: dict, event_queue) -> tuple | None

Called after every image arrives from the camera, before it is saved.

  - Return (image, metadata) to keep the image (you may modify either).
  - Returning None discards the image and NOTHING else: it keeps the frame out
    of the dataset. No event is dropped — every remaining event for that
    position still moves the stage, opens the shutter, and exposes the sample;
    only the pixels are thrown away afterward; submitted events still execute.
    This is a dataset filter, never a way to stop a position from being exposed.
  - NEVER call event_queue.put(...) — see the event_queue section below. Every
    microclaw runner silently discards anything a hook puts there: it adds no
    event, and event_queue.put(None) does NOT end the acquisition early.

For a saved `analyze_frame` hook, return measurements in `HookResult`; the
trusted parent writes the log and stamps position/x_um/y_um/z_um from the image
metadata, so every entry is self-describing. `HookBase.log` is only for trusted
pre-coded registry hooks and must not be used as a saved-hook pattern.

If you read the metadata yourself: a multi-position acquisition carries
"PositionName", "XPosition_um_Intended" and "YPosition_um_Intended"; a Z-stack
carries "ZPosition_um_Intended". An acquisition without a given axis does not
carry its keys, so read every one with .get() and fall back to
`metadata["Axes"]["position"]` for identity. (metadata["Axes"] holds e.g.
{"position": "tile_r0_c1", "time": 0, "z": 3}.)

### post_hardware_hook_fn(event_or_events: dict | list[dict]) -> same shape

Called after the hardware has moved to the event's position (XY, Z, channel)
but before the camera fires.

  - Return the (optionally modified) event dict or event list in the same shape,
    ALWAYS. NEVER return None:
    over the ZMQ bridge there is no way to cancel an event from a hook's
    return value — None becomes an empty event that STILL FIRES THE CAMERA,
    unlabeled, at the skipped event's OWN position (the hardware phase has
    already run: the ghost exposure lands exactly where you tried not to
    expose). See "Skipping and stopping" below for what actually works.
  - Use this for autofocus: the stage is already at the nominal XY, so you can
    do a Z sweep here and update the focus device before the shutter opens.

### pre_hardware_hook_fn(event_or_events: dict | list[dict]) -> same shape

Called before hardware moves. Microclaw wires this only through its trusted
adapter: from a declarative ``hook_action_plan`` for predetermined runs, or from
the action set registered with an adaptively selected event. Generated hook
source does not implement or receive this callback.

  - Return the (optionally modified) event dict or event list in the same shape,
    ALWAYS. NEVER return None:
    as with post_hardware_hook_fn there is no cancel over the bridge — None
    becomes an empty event that still fires the camera, unlabeled, wherever
    the stage last was. See "Skipping and stopping" below.
  - You may change event["z"], event["x"], event["y"] to redirect hardware.

### event_generation_hook_fn(event: dict) -> list[dict] | None

Called to dynamically generate or replace the events for an acquisition. NOT
currently wired by Microclaw's runner (see "Acquisition callback availability");
do not implement or promise it in a generated hook until that plumbing and its
tests land.

  - Return a list of replacement event dicts to use instead of the original.
  - Return None to leave the original event unchanged.
  - Use this when you need to compute event parameters at acquisition time
    rather than ahead of time.

### image_saved_fn(axes: dict, dataset[, event_queue]) -> None

Pycro-manager calls this after each image has been saved. Read the new pixels with
`dataset.read_image(**axes)` and metadata with `dataset.read_metadata(**axes)`.
This is a per-image callback, not a signal that the dataset or an image group is
complete. The optional third argument is pycro-manager's older adaptive form: the
callback may put follow-up events directly on that queue. It does not receive or return
an `AcquisitionFuture`. The separate future API is returned by `acq.acquire()` and lets
the acquisition owner await saved images. Microclaw uses neither mechanism here;
adaptive branching goes through the `run_adaptive_survey` candidates-queue runner, so
a hook is never handed an `Acquisition` or its future; the runner owns dispatch.

Microclaw's current acquisition runner does not yet wire this constructor argument.
Do not claim a generated hook can implement it until that native `image_saved_fn`
plumbing is added and tested.

## Skipping and stopping — discarding pixels versus preventing exposures

"Skip this event" via a return value DOES NOT EXIST over the ZMQ bridge, on
any hook, under any microclaw runner. pyjavaz sends a hook's None return as an
empty JSON object; the Java side deserializes that into a real event that
still fires the camera — one unlabeled ghost exposure per "skipped" event, and
the run completes looking successful. There is no return value that cancels an
event; no wrapper can fix this on our side of the bridge.

The levers that DO work:

  - Stop everything, loudly: RAISE from the hook. pycro-manager's hook thread
    catches the exception and calls acquisition.abort(e) — the whole
    acquisition aborts and the error surfaces to the caller. "Skip the rest"
    from inside a hook is not available; "stop everything, loudly" is.
    (The abort can race at most one final ghost exposure out the door —
    promptness, not correctness.)
  - Keep a frame out of the dataset: return None from image_process_fn. That
    discards the pixels and nothing else — the hardware activity it came from
    already happened, and future events are unaffected.
  - Decide per frame whether the next exposure happens at all: use an adaptive
    runner. For one field over time, call `run_timelapse` with `n_frames=None`,
    `max_frames=<dose cap>`, and a saved `hook_strategy`; only time 0 is seeded,
    and each `ContinueAcquisition` publishes one successor. For a planned
    position list, use `run_adaptive_survey`; only its first tile is seeded.
    An event that was never submitted needs no skip mechanism — nothing crosses
    the bridge, so stopping is simply NOT SUBMITTING. For time series,
    `adaptive_handoff` makes analysis and guarded actions gate each successor;
    `hooked_fixed_plan` observers
    keep the submitted events; their results do not gate the next exposure.

## Event dict structure

```python
{
    "axes": {
        "z":        0,    # zero-based index within the Z axis
        "time":     0,    # zero-based time-point index
        "position": 0,    # zero-based position index
        "channel":  0,    # zero-based channel index
    },
    "z":        10.5,             # absolute Z position in µm  (if Z axis used)
    "x":       100.0,             # absolute X in µm           (if set explicitly)
    "y":       200.0,             # absolute Y in µm           (if set explicitly)
    "channel": {"group": "Channel", "config": "DAPI"},  # (if channel axis used)
    "exposure": 100,              # exposure in ms             (if set explicitly)
    "min_start_time": 0.0,        # earliest start time in seconds from acq start
}
```

Only keys relevant to the current axis configuration are present. The "axes"
indices are sequence numbers (0, 1, 2, …), not physical values.

## event_queue — unavailable to saved hooks

NEVER call event_queue.put(). Saved hooks receive no hardware event queue; legacy
saved callbacks receive a stub that raises instead.

Reviewed built-in image_process_fn callbacks receive pycro-manager's real event
queue, but a put() on it is a SILENT NO-OP under every microclaw runner:
by the time any image is processed, the acquisition's terminator is already
queued ahead of (or the event source has already consumed) anything the hook
adds, so the hook's event is orphaned and never executed. Nothing raises and
nothing warns — the run completes looking successful while the added event
was silently dropped behind the terminator.

  - Never push new events:  event_queue.put({...}) is silently discarded.
  - Never push None:        event_queue.put(None) does NOT end the acquisition
    early — the terminator it would duplicate is already queued, and the
    hook's None is discarded the same way.

A reviewed built-in that needs to ADD work may run under the survey-with-detector runner,
which hands the hook a `candidates` queue and a `progress` counter as
attributes: self.candidates and self.progress (None under every batched
runner — check them and RAISE if missing). There the supported pattern is:

  1. Never analyze a frame whose position label is not in the survey set —
     otherwise the hook re-detects its own follow-up frames, forever.
  2. Check every derived event with guard.check_xy / guard.check_z BEFORE
     enqueueing it — derived events bypass the normal per-move guards, and
     enforce a max_events cap, logged when it is hit.
  3. candidates.put(event) BEFORE progress.image_done(), always — reversed, a
     hit on the last survey tile can be silently lost.

The adaptive variant of the same runner is the one-event-at-a-time mode (see
"Skipping and stopping"), reachable through the run_adaptive_survey tool: the
runner additionally sets hook.survey_events (the full built tile list, seed
included) and pre-dispatches only survey_events[0]; every later tile exists
only if the hook submits it. The ordering contract extends naturally: decide
from the frame that just arrived; then candidates.put() the next tile OR call
progress.done_early(); then progress.image_done(). Positions run in the order
the tool was given — hand run_adaptive_survey a reversed list for a reverse
scan.

A reviewed built-in that needs to add work and finds self.candidates is None must FAIL
LOUDLY (raise), not quietly log a success — it is running under a batched
runner that can never honor its decisions.

## Generated saved-hook pattern

Saved hooks must not inherit HookBase or take a `log_path` constructor argument.
The trusted parent owns the log so hook
source cannot forge or omit its action decision record.

```python
from microclaw.hook_decisions import ContinueAcquisition, HookResult
import numpy as np

class MyHook:
    def analyze_frame(self, image: np.ndarray, metadata: dict):
        # ... your logic ...
        return HookResult({"key": "value"}, (ContinueAcquisition(),))
```

Saved source still executes in the hardware-control process. Source review and
hash pinning remain the containment story until Block 13 adds worker isolation;
there is no hard deadline, memory cap, network isolation, or native-crash recovery.

## Choosing the right hook type

  Task                                   Hook to implement
  -------------------------------------  --------------------------------
  Record measurements / propose action   analyze_frame → HookResult
  Adjust exposure or settings per frame  SetExposure proposal (currently refused
                                           by run_adaptive_survey)
  Refocus a promising survey tile       RequestAutofocus from a saved hook +
                                           run_adaptive_survey autofocus_budget
  Redirect stage before hardware moves   pre_hardware_hook_fn (native pycro-manager;
                                           not yet wired by Microclaw)
  Stop a single-field time series        run_timelapse(n_frames=None,
    based on each image                     max_frames=...) + adaptive hook
                                            (stop = don't submit; NEVER return None)
  Stop/refine a spatial survey           run_adaptive_survey + adaptive hook
    based on each tile                      (stop = don't submit; NEVER return None)
  Abort everything on a safety limit     raise from any hook (loud, surfaces)
  Generate events dynamically at runtime event_generation_hook_fn (native;
                                           not yet wired by Microclaw)
  React after each image is persisted    image_saved_fn (native pycro-manager;
                                           not yet wired by Microclaw)
  Branch acquisition on a group of       run_adaptive_survey + adaptive hook
    images before deciding                 (runner owns dispatch; no AcquisitionFuture)
  Analyze a completed saved dataset      run_analysis_on_saved_dataset + reviewed
                                           saved adapter: analyze_completed_dataset
                                           or analyze_saved_frame; selection-limited
                                           reads and bounded artifact emission above

## Observation-only SNR hook

`snr_observer` is the pre-coded observation-only reference for fixed surveys. It
calls the shared `compute_stats` implementation and writes one
`microclaw.analysis-observation/v1` record per image, including SNR, focus metric
and validity, intensity statistics, saturation, analysis time, and acquisition
coordinates. It always returns the original image and metadata. It has no score
threshold, filtering, queue submission, early-stop, or hardware behavior.

  hook_params:
    min_snr       validity gate for the reported focus metric (default 3.0);
                  this does not filter or select images

Use it with a fixed one-frame-per-tile acquisition and a log path. Rank the
completed log offline; a streaming hook cannot know final top-k. Its SNR is a
whole-field measurement, so any revisit targets the recorded tile coordinate,
not an invented object centroid.

## Micro-Manager plugin hooks

Two pre-coded strategies delegate hook logic to an installed Micro-Manager
plugin. Reuse requires matching algorithm, data, stop and export contracts;
installed does not mean compatible. Call
list_mm_plugins() for classpaths; it does not prove a controllable running instance. These require a Micro-Manager build with
the unified plugin classloader (PR #2401); on older builds the plugin classes
are not resolvable over the ZMQ bridge and the hook fails loudly at startup.

MM plugins are arbitrary Java: they bypass SafetyGuard (which only gates
microclaw's own hardware calls) and the Python AST scanner (which only sees
Python source). ALWAYS confirm the classpath + method with the user before
enabling a plugin hook.

### mm_plugin_analyzer  (analyzer — read-only, allowed by default)

Treats the plugin as an ANALYZER: microclaw computes a scalar feature from the
image (np.mean) in Python, passes only that scalar to the plugin, and uses the
plugin's returned score for a guarded, Python-side keep/skip decision. The full
image never crosses the bridge, and the plugin must NOT move hardware here.

  hook_params:
    classpath      fully-qualified plugin class (e.g. "org.lab.QualityScorePlugin")
    method         plugin method to call with the scalar feature (default "analyze")
    reject_below   optional float; discard the image if score < this value

  Safety gate: SafetyGuard.check_plugin — allowed unless the classpath is listed
  in safety_config.yaml plugins.blocked. Fails open: if the plugin call raises,
  the image is kept and the error is logged (data is never lost to a plugin bug).

### autofocus_mm_plugin  (hardware motion — explicit confirmation required)

Drop-in alternative to autofocus_per_position: runs the lab's validated MM
autofocus plugin (via the autofocus manager) in the post_hardware slot before
each capture. The PLUGIN owns the Z motion.

  hook_params:
    plugin_name    optional MM autofocus plugin name; omit to use the active one

  Safety gate: hardware-moving plugin hooks are permitted by default and require
  explicit user confirmation before enabling (the blocklist still applies).
  An explicit plugins.allow_hardware_motion: false disables them. The plugin's
  arbitrary Java motion cannot be enumerated or intercepted. PASSIVE guard on
  the result: after the plugin focuses, microclaw
  reads the new Z and calls check_z; if it is out of bounds the hook logs
  autofocus="unsafe_abort" and RAISES SafetyViolation — the whole acquisition
  aborts loudly, naming the Z and the limit ("skip this capture" does not
  exist over the bridge; see "Skipping and stopping"). microclaw does NOT
  re-drive Z — an active correction would fight the plugin's own safety
  controller.

### The composition rule (one image-processing side per hook)

Per hook, keep the heavy full-image work on ONE side (Python or Java) and let
only scalars/metadata cross the bridge. If microclaw itself issues the hardware
move from a plugin's non-image output, SafetyGuard applies normally (guard the
move as usual) — that differs from autofocus_mm_plugin, where the plugin drives
the stage and microclaw can only guard passively. Full-image Java processing
should be an MM processor pipeline, not a hook.
