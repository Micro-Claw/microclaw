HOOK_REFERENCE = """
# pycro-manager hook API reference (microclaw)

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
logged `analyze_frame` rather than a runner-thread wait loop (design/24). For
per-image work after persistence use `image_saved_fn`. For completed data, the offline
orchestrator opens the `ndstorage.Dataset` (from `acq.get_dataset()` or a direct
`ndstorage` import; do not rely on a version-dependent `pycromanager` re-export) and
gives the adapter a read-only, selection-limited `DatasetView`. That offline
orchestrator and `DatasetView` are a design/26 proposal, not yet implemented; do not
claim a generated adapter can run offline until they ship.
Stateful streaming remains available when those boundaries do not fit.
`analyze_frame` itself does not receive a batch.

No network call may occur while images are acquired. A local subprocess is allowed
only after its lint warning and full source are explicitly reviewed; derived XY/Z
events must pass the guard.

Once the technical contract is complete, summarize it in plain language and call
out only unresolved assumptions that affect scientific meaning or safety. Choose
the hook method below, write a `HookBase` adapter, and test it without hardware
against the example. If the output cannot yet be verified, make the first version
observation-only: log raw output and do not drive acquisition. Show the full source
and lint warnings, and save it only after explicit confirmation. Package setup
belongs in the hook's documented local environment, not in a package-specific
microclaw analysis tool.

For that first observation-only version, normalize the verified part of the raw
output to JSON values and call `self.log_analysis(...)`. Record the analyzer name
and installed version, parameters affecting the result, and the sha256 of any
model/project/config artifact. Use `status="unverified"` when axes, units, score
semantics, or coordinates remain unresolved. Do not turn an unverified record into
filtering, stage movement, early stopping, or follow-up acquisition.

## Acquisition callback availability

Pycro-manager's native `Acquisition(...)` constructor accepts all of these callback
kwargs, but Microclaw does not currently expose all of them through its runners.

Currently wired by Microclaw's acquisition runner for reviewed built-ins:

  image_process_fn       callable(image, metadata, event_queue) -> tuple | None
  post_hardware_hook_fn  callable(event) -> dict   (ALWAYS return the event)

Generated and user-saved hooks instead implement
`analyze_frame(image, metadata) -> HookResult | None`. They never receive ctrl,
guard, credentials, paths/directories, runner queues, or the pycro-manager event queue. Legacy saved
`image_process_fn` classes still load, but receive a raising event-queue stub and no
other capability. Only classes shipped in PRECODED_HOOK_REGISTRY are trusted built-ins.

Native pycro-manager callbacks not currently wired by Microclaw:

  pre_hardware_hook_fn      callable(event) -> dict   (ALWAYS return the event)
  event_generation_hook_fn callable(event) -> list[dict] | None
  image_saved_fn            callable(axes, dataset[, event_queue]) -> None

Do not implement or promise an unwired callback in a generated Microclaw hook. Adding
one requires runner plumbing and fixture/integration tests first. For wired callbacks,
only the methods actually implemented by the hook are passed to `Acquisition(...)`.

## Hook function signatures and return-value contracts

### analyze_frame(image: np.ndarray, metadata: dict) -> HookResult | None

The saved-hook contract. `HookResult` contains JSON-safe measurements and a list
or tuple of typed action proposals: MoveStage, AcquireAt, SetExposure, ContinueSurvey,
StopSurvey, RequestAutofocus, SetIlluminationPower, EmitArtifact, or DiscardFrame.
Under run_adaptive_survey the trusted parent
supports ContinueSurvey, StopSurvey, and AcquireAt for a position in the planned
grid. It guard-checks and reservation-checks every proposal and writes every
accept/refuse decision to the log. The other three actions are parsed but refused
as unsupported by this runner.

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
not dose. It does not skip acquisition or reduce dose (design/27).

### image_process_fn(image: np.ndarray, metadata: dict, event_queue) -> tuple | None

Called after every image arrives from the camera, before it is saved.

  - Return (image, metadata) to keep the image (you may modify either).
  - Returning None discards the image and NOTHING else: it keeps the frame out
    of the dataset. No event is dropped — every remaining event for that
    position still moves the stage, opens the shutter, and exposes the sample;
    only the pixels are thrown away afterward (design/27). This is a dataset
    filter, never a way to stop a position from being exposed.
  - NEVER call event_queue.put(...) — see the event_queue section below. Every
    microclaw runner silently discards anything a hook puts there: it adds no
    event, and event_queue.put(None) does NOT end the acquisition early.

To key a log entry to the image's place in the acquisition, call
self.log(metadata, ...) on HookBase: it stamps position/x_um/y_um/z_um for you
from the image metadata, so every entry is self-describing.

If you read the metadata yourself: a multi-position acquisition carries
"PositionName", "XPosition_um_Intended" and "YPosition_um_Intended"; a Z-stack
carries "ZPosition_um_Intended". An acquisition without a given axis does not
carry its keys, so read every one with .get() and fall back to
`metadata["Axes"]["position"]` for identity. (metadata["Axes"] holds e.g.
{"position": "tile_r0_c1", "time": 0, "z": 3}.)

### post_hardware_hook_fn(event: dict) -> dict

Called after the hardware has moved to the event's position (XY, Z, channel)
but before the camera fires.

  - Return the (optionally modified) event dict, ALWAYS. NEVER return None:
    over the ZMQ bridge there is no way to cancel an event from a hook's
    return value — None becomes an empty event that STILL FIRES THE CAMERA,
    unlabeled, at the skipped event's OWN position (the hardware phase has
    already run: the ghost exposure lands exactly where you tried not to
    expose). See "Skipping and stopping" below for what actually works.
  - Use this for autofocus: the stage is already at the nominal XY, so you can
    do a Z sweep here and update the focus device before the shutter opens.

### pre_hardware_hook_fn(event: dict) -> dict

Called before the hardware moves for this event. NOT currently wired by
Microclaw's runner (see "Acquisition callback availability"); do not implement
or promise it in a generated hook until that plumbing and its tests land.

  - Return the (optionally modified) event dict, ALWAYS. NEVER return None:
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
a hook is never handed an `Acquisition` or its future (design/24).

Microclaw's current acquisition runner does not yet wire this constructor argument.
Do not claim a generated hook can implement it until that native `image_saved_fn`
plumbing is added and tested.

## Skipping and stopping — what a hook can actually do (design/27)

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
  - Decide per frame whether the next exposure happens at all: the ADAPTIVE
    survey runner, exposed as the run_adaptive_survey tool.
    Only the first tile is pre-dispatched; the hook scores each frame as it
    arrives and either candidates.put()s the next tile or calls
    progress.done_early(). An event that was never submitted needs no skip
    mechanism — nothing crosses the bridge, so stopping is simply NOT
    SUBMITTING. This is the pattern for stop-on-condition ("stop bleaching
    once N tiles match") and refine-where-interesting. It serializes the
    acquisition (each tile waits for the previous frame to be scored), so use
    it only when acquisition behavior genuinely branches on the images; a
    fixed survey that just reports what it sees keeps the batched runners.

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
was silently dropped (design/24).

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

Saved hooks need not inherit HookBase. The trusted parent owns the log so hook
source cannot forge or omit its action decision record.

```python
from microclaw.hook_decisions import ContinueSurvey, HookResult
import numpy as np

class MyHook:
    def analyze_frame(self, image: np.ndarray, metadata: dict):
        # ... your logic ...
        return HookResult({"key": "value"}, (ContinueSurvey(),))
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
  Autofocus before each image capture    RequestAutofocus proposal (currently
                                           refused by run_adaptive_survey)
  Redirect stage before hardware moves   pre_hardware_hook_fn (native pycro-manager;
                                           not yet wired by Microclaw)
  Stop acquiring based on the images     run_adaptive_survey + adaptive hook
    (stop-on-condition, refine)            (stop = don't submit; NEVER return None)
  Abort everything on a safety limit     raise from any hook (loud, surfaces)
  Generate events dynamically at runtime event_generation_hook_fn (native;
                                           not yet wired by Microclaw)
  React after each image is persisted    image_saved_fn (native pycro-manager;
                                           not yet wired by Microclaw)
  Branch acquisition on a group of       run_adaptive_survey + adaptive hook
    images before deciding                 (NOT AcquisitionFuture; see design/24)
  Analyze a completed saved dataset      offline adapter over restricted DatasetView
                                           (orchestrator owns ndstorage.Dataset;
                                            design/26 proposal, not yet implemented)

## Observation-only SNR hook

`snr_observer` is the pre-coded positive-control hook for design/26 Run A. It
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
plugin instead of re-implementing it in Python. Call list_mm_plugins() to see
installed plugins and their classpaths. These require a Micro-Manager build with
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

### autofocus_mm_plugin  (hardware motion — off by default)

Drop-in alternative to autofocus_per_position: runs the lab's validated MM
autofocus plugin (via the autofocus manager) in the post_hardware slot before
each capture. The PLUGIN owns the Z motion.

  hook_params:
    plugin_name    optional MM autofocus plugin name; omit to use the active one

  Safety gate: SafetyGuard.check_plugin_motion — requires
  plugins.allow_hardware_motion: true in safety_config.yaml (blocklist also
  applies). That flag is necessary but NOT sufficient: an opaque motion plugin
  cannot be enumerated or intercepted, so guaranteed mode refuses it at startup
  and the file must ALSO carry property_authorization.mode:
  degraded_trusted_plugins. Both are human edits followed by a restart;
  `microclaw check-config` reports the pair without connecting to the rig. PASSIVE guard on the result: after the plugin focuses, microclaw
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
"""
