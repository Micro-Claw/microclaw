# Driving Micro-Manager's own side — the MDA, htSMLM activation, plugin windows

**Status:** proposal, revised 2026-10-15. No block opened. Owns `R86`.
Siblings: [design/87](87-package-metrics.md) (community-package metrics) and
[design/89](89-feedback-during-acquisition.md) (MicroClaw acting on them).

## The problem

Some workflows belong to Micro-Manager's Java side, not to a pycro-manager
acquisition. The motivating one is SMLM on the Ries lab's rigs: **htSMLM**
controls the 405 activation while **MMStudio's MDA** acquires. That route gives
SMLM what it needs:

- an uninterrupted hardware-sequenced burst at a **constant frame interval**,
  which kinetics and drift correction depend on (operator, 2026-10-14);
- frames passing through Studio's processor pipeline, which is where htSMLM
  gets its images.

The user wants MicroClaw to set that up and start it. More generally, MicroClaw
should be able to open and run Micro-Manager's own windows and plugins, but
only through APIs those programs support.

**The rule for all of it** (design/78's 78c checklist, kept): through a
supported API seam only. No screen clicking, no private-field reflection.
Where no seam exists, MicroClaw says what the operator must do, checks what it
can, and records what happened. It does not work around the missing seam.

## htSMLM, read from source (2026-10-15)

`jdeschamps/htSMLM` `main` is `30b6bfd` (2024-01-08), the commit design/78 read.

- **Frames.** `ActivationController.addReadImagePairsProcessor` installs
  `ReadImagePairsPlugin` in **Studio's processor pipeline**
  (`studio.data().addAndConfigureProcessor`). `ActivationTask.getTwoImages`
  waits for two frames from it.
- **Density.** It subtracts the frame pair, applies a Gaussian blur and
  non-maximum suppression, and counts peaks over an auto-cutoff. It has no
  input for an external count.
- **Law, per iteration** (default idle 100 ms):
  `dp += 0.1 + p·feedback·(1 − N/N0); p += dp` (`dp` resets when `|dp| > 1`),
  clamped to `[0, maxPulse]`. Defaults: `feedback` 0.4, max pulse 10,000.
- **Write.** `ActivationPanel.updateResults` writes "Pulse duration 1
  (activation)" (or 2) through EMU's `setUIPropertyValue`. On M5 that is
  `Laser Trigger-Duration0 (us)`, the pulse length. It does not go through
  MicroClaw's guard.
- **Stop.** `LocalizationAcquisition` stops its MDA a delay in seconds after
  the pulse reaches max (default 5 s).
- **API.** `ActivationPanel.getActivationController()` (line 724) and
  `MainFramehtSMLM.getActivationController()` (line 246) are public. On the
  controller:
  - `startTask()` is htSMLM's own cross-thread entry ("Called from other
    threads, e.g. acquisition task"). It starts the task and, through
    `activationHasStarted()`, ticks **both** Activate and Run on the EDT.
  - `pauseTask()` unticks Activate (the task keeps estimating and stops
    writing once the UI update has taken effect and in-flight work finishes).
    `runActivation(false)` requests task stop without necessarily clearing
    the Activate/Run controls or proving the worker has already exited.
  - `runActivation(true)` starts estimation **without changing** Activate.
    It writes nothing only if Activate is currently off; an already selected
    checkbox permits writes.
  - `initializeTask(index)`, which htSMLM's acquisition calls, writes `"0"` to
    the activation property (`zeroProperty()`). That is a hardware write.

  State is readable through `isActivationRunning()` and the panel's
  `getActivationParameters().getActivate()`. What is not established is how an
  outside caller obtains the **live** `MainFramehtSMLM`/`ActivationPanel` from
  EMU's plugin. That is spike V7. Until it is proved, Activate, `N0` and
  feedback remain manual.

And from pycro-manager's Java source (`RemoteAcquisitionFactory`,
`RemoteViewerStorageAdapter`): a MicroClaw acquisition writes **straight to
`NDTiffStorage`**, so its frames never enter Studio's pipeline. **htSMLM
receives nothing from a MicroClaw acquisition.** If "Activate" is on during
one, `getTwoImages` spins on an empty queue while `isSequenceRunning()` is
true, so activation looks on and does nothing. This is from source, not yet
observed (V5). It is why htSMLM's workflow needs the MDA, and it answers
design/78's open 78c check.

## What Micro-Manager offers

Read off `MMJ_.jar` (2.0.3-20260625) with `javap`, 2026-10-14/15:

- **`AcquisitionManager`:** `runAcquisitionWithSettings(SequenceSettings,
  boolean block)`, `runAcquisitionNonblocking()`, `haltAcquisition()`,
  `abortAcquisition()`, `isAcquisitionRunning()`, `sequenceSettingsBuilder()`.
- **`SequenceSettings.Builder`:** `numFrames`, `intervalMs`, `useFrames`,
  `save`, `saveMode`, `root`, `prefix`, `shouldDisplayImages`,
  `useSlices`/`useChannels`/`usePositionList`, `useAutofocus`,
  `cameraTimeout`, `comment`, and others.
- **`Datastore.SaveMode`:** includes **`ND_TIFF`**, so an MDA can write what
  SMAPpy follows.
- **`DataManager`:** `getApplicationPipelineConfigurators`,
  `getLivePipelineConfigurators`, `isApplicationPipelineStepEnabled`.
- **`PluginManager.getMenuPlugins()`** → **`MenuPlugin.onPluginSelected()`**,
  the supported menu invocation, exactly as Micro-Manager's Plugins menu
  does. A callback may perform an action immediately; opening a window is not
  its general contract. MicroClaw already reads `getMenuPlugins()` for
  `list_mm_plugins` (`controller.py:755`).

Implementation inspection of this build also establishes:

- `runAcquisitionWithSettings` sets the engine's current settings, which posts
  an acquisition-settings-changed event. It does not preserve prior settings.
- `haltAcquisition()` calls `abortRequest()`, which can show an Abort/Cancel
  dialog. `abortAcquisition()` calls `stop(true)` directly.
- pycro-manager's `Acquisition.abort()` addresses its own AcqEngJ acquisition;
  it is the candidate in design/89, not a way to stop Studio's separate MDA.

§"Probe" establishes behavior beyond this source evidence.

## What `run_mda` is today

| today | where | consequence |
|---|---|---|
| it runs only what the dialog holds | `tools.py:12064`; `get_mda_settings` previews, `run_mda` checks a hash | MicroClaw cannot set anything up |
| one **blocking** call, `manager.run_acquisition()` | `tools.py:~12145` | pyjavaz holds one lock per round trip, so no other call reaches the core for the whole run; no cancel; a teardown hang hangs MicroClaw |
| unsupervised | `R86` | no `design/60` bound, no `design/75` D4 record; exempt from `authorize_path` (`tools.py:12331`); outside the install-admission chokepoint (`tools.py:4750`) |
| no `analysis` | `tools_schema.py:2539` | SMAPpy cannot watch an MDA |
| export `@refuses` | design/63 | its reason, "a standalone script has neither the studio nor the settings", is outdated for MicroClaw-written settings: exported scripts already build `Studio()` (`tools.py:1603`, `:2461`) |
| the preview hash omits `saveMode` | `_MDA_SCALARS`, `tools.py:~11993` | the save format can change after preview |

## Decisions

### E1 — one tool, two routes: `run_mda(settings=…)` beside `run_mda(preview_token=…)`

Fold into `run_mda`; do not add a tool. The two routes are mutually exclusive:

- **`preview_token`**, unchanged: run exactly what the dialog holds. Add
  `saveMode` to the fingerprint.
- **`settings`**, MicroClaw-built. v1 is time-lapse only, which is the SMLM
  shape:

  ```json
  {"n_frames": 50000, "interval_s": 0, "exposure_ms": 15,
   "name": "cell3_dstorm", "save_mode": "ND_TIFF"}
  ```

  `save_mode` defaults to `ND_TIFF`. `root` is the workspace
  (`guard.resolve_in_workspace`). The settings come from a **fresh builder**,
  not `copyBuilder()`, with every axis not given explicitly off, so nothing the
  dialog holds (positions, autofocus, slices) rides along. Exposure goes
  through `guard.check_exposure` and is set on the core, as `run_timelapse`
  does. Channels, slices and positions stay on the dialog route until someone
  needs them typed.

**Update the MDA dialog (operator decision, 2026-10-15).** After validation
and the acquisition confirmation, start with
`runAcquisitionWithSettings(built, false)`. That call already applies the
settings as the engine's current ones (`executeAcquisition` →
`setSequenceSettings` → `acquire`, read from the 2.0.3 jar), which posts the
settings-changed event, so the requested settings become visible without a
separate `setAcquisitionSettings` call. A second call would only post a second
event. V1 must confirm that the dialog follows. If it does not, use the
public `setAcquisitionSettings` route to make the dialog update; a visible
update is required, not optional. Record the settings that actually ran and
save them in summary metadata.

**No settings restoration in v1 (operator decision, 2026-10-15).** The run
leaves its settings in the dialog. Putting the previous settings back
(snapshot, restore after proved termination, skip if the operator edited them
meanwhile) waits until someone needs it. Micro-Manager's own save/load of MDA
settings covers the case meanwhile. See §"Not now".

### E2 — non-blocking and supervised, on both routes (closes `R86`)

Start non-blocking: `runAcquisitionWithSettings(…, false)` or
`runAcquisitionNonblocking()`. Then:

- **Poll** `isAcquisitionRunning()` and the datastore's `getNumImages()` about
  once a second. Expected short round trips replace today's whole-run lock.
- **Bound** the wait using design/60's plan-derived runtime deadline **and**
  quiet-window qualification (`max(900, 5 × largest gap)`). Account for planned
  long intervals and startup before the first observed gap. Image-count polling
  gives coarse progress, not individual frame timing. A quiet timer alone is
  not the runtime policy.
- **Bridge hangs.** Run start/poll/stop calls on the supervised worker side;
  the tool's watchdog must return even if one call blocks. Do not queue repeated
  calls behind a hung bridge lock. Raise `AcquisitionUnterminated` and retain
  the pending worker and hardware/teardown uncertainty for the existing
  concurrent-acquisition refusal. V4 injects blocked start, poll and stop calls.
- **Cancel.** The UI's Stop and terminal interrupt request
  `abortAcquisition()`, pending V4's preservation/latency probe. Avoid
  `haltAcquisition()`'s additional modal prompt. A stop request is not proof of
  termination; continue bounded supervision.
- **Writer completion.** Prove engine/pipeline completion and datastore
  finalization using the supported running/frozen state, checked against
  readability and flushed ND-TIFF index in V2/V4. Never infer writer completion
  from a stop request or an unchanged image count. Keep acquisition outcome and
  writer completion separate for analysis notifications.
- **Record** `design/75` D4 lifecycle diagnostics and a `duration_breakdown`.
- **Gate.** Put `run_mda` behind the install-admission chokepoint, and keep
  `design/60`'s concurrent-acquisition refusal (it keys on
  `_microclaw_acquisition_entry_point`, which already includes `run_mda`).

The dialog route keeps its `authorize_path` exemption (design/33 excluded the
opaque `mmstudio-mda` path). Whether the typed route earns an
`acquisition-tool:run_mda` row is decided in 88a. It is no longer opaque.

### E3 — htSMLM: MicroClaw checks, discloses and records; the operator ticks "Activate"

**When E3 applies is decided by htSMLM's own state, not by a MicroClaw
argument** (operator, 2026-10-15). htSMLM acts on an acquisition only when its
**Activate** checkbox is ticked **and** its activation script is running
(**Run**). It then acts on any MDA, whether started from the MDA dialog, by
MicroClaw's `run_mda`, or from htSMLM's own acquisition panel in EMU. E3's
read-only preflight runs when htSMLM is installed and configured with an
activation property, and says nothing otherwise. Ordinary `run_mda` and
package analysis require neither htSMLM nor EMU. MicroClaw starts its MDA
through `run_mda`; launching through htSMLM's EMU panel is the operator's and
has no seam MicroClaw drives (design/78's 78c checklist). Until E5 proves a
supported route to the running instance, the operator starts activation.
E3's checks and recording work independently of that route.

**Preflight, read-only, before the confirmation:**

- **Installed.** `check_emu_installed` reports htSMLM installed and
  configured.
- **What it controls.** `emu_manager.read_emu_config` names the activation
  property ("Pulse duration 1 (activation)" → device/property) and htSMLM's
  max pulse. The max pulse is an internal property that `read_emu_config` may
  need to start parsing. This small shared reader extension is a prerequisite
  of both 88c and design/89 F1, and may land before either. Persisted config is
  not proof of the live panel's current maximum; label its source and report
  an unverified live value rather than presenting it as measured.
- **Processor present/enabled?** Report the current pipeline observation.
  The processor can be removed after activation starts, or remain after it
  stops; presence or absence establishes neither session history nor current
  checkbox state. Read activation state only if E5 proves a supported accessor.
  **Absence still has a consequence worth stating:** `runActivation(true)`
  re-adds the processor only when the task is not already running. So the
  confirmation says: "no htSMLM processor in the pipeline: if activation is
  running, stop and restart it, or it will not see this MDA's frames."
- **The window.** Once 88d admits EMU opening, MicroClaw can put the window
  in front of the operator. Until then, the operator opens it manually; E3
  does not depend on E5.

**Disclosed in the one confirmation** (`kind="acquisition"`, as today):

- the MDA's settings;
- "htSMLM controls `<device>.<property>` up to max pulse `<n>` only if
  Activate is ticked and its activation script is running (Run)";
- **whether that max exceeds MicroClaw's configured bound** for the property.
  htSMLM's writes do not pass through MicroClaw's guard, so MicroClaw states
  that it cannot enforce its bound rather than imply it does.

**Recorded during the run:** the activation property, read with each poll
(about 1 Hz, read-only), goes into `activation_trace`, with
`activation_changed`. A flat trace is reported as information ("the activation
property did not change during the run"). Neither a flat nor a changing
trace proves checkbox state: a running controller can remain at a bound,
and another client can write the property.

**Optional exhaustion stop, at parity with htSMLM's own acquisition:**
`stop_at_activation_max_s: 5` requests `abortAcquisition()` once the property has
been at max that long. This is MicroClaw stopping its own run, not a hardware
write, and it is consented in the same confirmation. Missing/failed samples
reset the dwell timer; use the verified maximum or explicitly record the
operator-approved configured maximum being used.

### E4 — community packages watch an MDA (`analysis` on `run_mda`)

On the settings route with `ND_TIFF`, `run_mda` accepts `analysis` and joins
`ANALYSIS_TOOLS`. Any admitted package operation compatible with this growing
dataset can be selected through the existing package preparation/validation
path, independent of its publisher or metric names. No SMAPpy installation,
htSMLM state or EMU configuration is required. The job binds to the path read from the **datastore** once it
exists, never to one guessed from root/prefix (Micro-Manager suffixes
collisions; design/38 F7). The job gets the ordinary `acquisition`/`writer`
notifications from E2. With design/87, SMAPpy's live counts are visible while
htSMLM holds the density in the motivating workflow. Test generic attachment
with a second fixture package using unrelated metric names and with no htSMLM/
EMU installed. Design/89's `analysis_feedback` is refused on all MDA calls in
v1 by its tool-scope rule; this does not imply that htSMLM is always present.

### E5 — inspected plugin operations, informed by skills

Start with EMU/htSMLM; there is no requirement to handle every plugin.
`open_mm_plugin(name)` invokes an **inspected** menu plugin from
`list_mm_plugins()["menu"]`, resolving the registered instance by its classpath
key and checking `plugins.blocked`. Installed/discovered alone is insufficient:
unknown callbacks are reported as unsupported until their effects are inspected.
`onPluginSelected()` may write hardware, start work, or block, as well as open a
window. V6 checks effects, repeat invocation and required Swing EDT dispatch.
Do not hold a bridge call open for an unbounded callback.

Confirm effects MicroClaw causes, with the relevant existing confirmation kind
and device/property disclosure; opening a verified read-only window needs no
confirmation. Device-writing menu actions require the same pre-write bounds and
authorization as other delegated operations. If the seam cannot enforce them,
leave that action manual. Choose export per operation: `@emits_nothing` only
for verified display-only effects, an emitter for reproducible actions, and an
explicit refusal where effects cannot yet be reproduced.

**Skill-guided operation (operator decision, 2026-10-15): skills inform
first-party operations; they are not executed.** A skill, or a plugin's public
documentation/source, may describe the Java calls behind an operation such as
setting htSMLM's Activate. That knowledge is used **in a block**, to build and
review a typed MicroClaw operation (for example `set_htsmlm_activation(on)`).
At runtime the agent never executes call sequences taken from skill text, and
never discovers calls from source during a session. This is design/83's rule
that a skill can only describe what MicroClaw can already do. Before an
operation ships, the block resolves and verifies the object-access path, the
live instance, arguments, threading and effects. A public method on a newly
constructed controller does not control the configured instance. Package
workers remain read-only observers with no Java execution or hardware channel.

For htSMLM, the semantic calls are `startTask()` (start, ticks Activate and
Run), `pauseTask()` (untick Activate), `runActivation(false)` (stop) and
`isActivationRunning()` / `getActivationParameters().getActivate()` (state),
on the live controller reached through the panel or main frame. **Never call
`initializeTask`**, because it writes `"0"` to the activation property. Prove
start, status, stop, error handling, pre-write bounds, parameter mapping,
UI-state synchronization and export under design/78's 78c checklist. Prefer
these calls over synthesizing a button click. No screen clicking, private
reflection or arbitrary Java-evaluation tool.
If no complete supported route exists, document the missing accessor for
upstream and retain the manual checkbox. Generalizing verified operations to
other plugins can follow the htSMLM proof; do not build a universal dispatcher
in this block.

The MDA dialog is handled by E1, independently of plugin operations.

### E6 — export: the settings route emits; the dialog route still refuses

The settings route renders as plain pycro-manager:

```python
studio = Studio()
am = studio.acquisitions()
core.set_exposure(15.0)  # after portable exposure preflight
s = (am.sequence_settings_builder().num_frames(50000).interval_ms(0.0)
     .use_frames(True).save(True).save_mode(<ND_TIFF>).root(str(_HERE))
     .prefix("cell3_dstorm").use_slices(False).use_channels(False)
     .use_position_list(False).use_autofocus(False).build())
store = am.run_acquisition_with_settings(s, False)  # also updates the dialog
# Bounded supervision, cancellation and writer-finalization checks follow E2.
```

The `ND_TIFF` static is reached the way `_new_static_java_class` reaches
statics, emitted inline, because pyjavaz's static-cache collision applies in a
script too. The script prints that htSMLM activation is the operator's panel
and is not reproduced. The final emitter reproduces exposure preflight,
settings update, bounded supervision, cancellation
and, once 88c adds it, the activation-maximum dwell stop. Exec tests drive the
normal and failure paths against bridge-shaped fakes; the sketch above is not
the complete emitted program. If a requested stop cannot be reproduced,
refuse that call rather than omit it. The dialog route keeps `@refuses`, with
its reason narrowed to "the dialog's contents". This is a tool with two emitted routes,
the shape `set_channel` already has (block 41c).

## Probes — each before its owning block's gate

`design/88-probe-mda.py`, standalone, run first against a bridge-shaped fake
(snake-cased Datastore/SequenceSettings shadows, a static `SaveMode` reached
as `_new_static_java_class` does).

**On the demo machine:**

- **V1.** Does `runAcquisitionWithSettings(s, false)` return before the first
  frame, with a datastore? Does that call alone visibly update the
  dialog? If not, prove the public setter fallback. Report camera exposure
  separately from the MDA settings.
- **V2.** `ND_TIFF`: path and name from `getSavePath()`, and when it becomes
  non-null. Does `ndstorage.Dataset` open it **while it grows**? Check with
  ndstorage's own reader, not an expected filename (design/60b). Establish
  which running/frozen states prove writer finalization and a flushed index.
- **V3.** At `interval_ms = 0`, is the interval constant, from the dataset's
  timestamps, and how does it compare with `run_timelapse`'s burst?
- **V4.** `abortAcquisition()` mid-run: is the dataset readable, does the index
  match, how long until engine/pipeline and writer finish? Repeat. Verify no
  extra prompt. Against fakes inject blocked start, poll and stop calls, plus
  a legitimate interval longer than the quiet floor before the second image.
- **V6.** For each plugin operation admitted in E5, starting with EMU, inspect
  source and CoreLog at debug level for device writes and other side effects.
  Test threading, bounded return, repeated invocation and exact plugin version/
  configuration. A passing probe is not evidence for arbitrary menu plugins.
- **V7 — spike: reaching htSMLM's live controller.** Standalone, read-only
  first. From `studio.plugins().get_menu_plugins()`, find EMU's plugin entry
  and determine whether a public, supported path leads to the running
  `MainFramehtSMLM` or `ActivationPanel` (for example EMU's
  `SystemController` → main frame). Record each object's class and identity
  across two calls, so it is the same live instance and not a new one. Then
  read `isActivationRunning()` and `getActivationParameters().getActivate()`
  and compare them with the panel as the operator sees it. Only after that,
  with the operator watching, exercise `startTask()`, `pauseTask()` and
  `runActivation(false)` from both selected and unselected checkbox states.
  Verify the full start/pause/stop transition, displayed Activate/Run state,
  actual worker termination and when the final property write ceases, including
  in-flight work. Do not infer completion from a method returning or a checkbox
  alone. Needs EMU and htSMLM installed with a configuration that
  maps the activation property to a demo device. If the demo machine has no
  htSMLM, this runs on M2. If no supported path exists, record the missing
  accessor for upstream.

**On M2 (htSMLM installed):**

- **V5.** Is htSMLM's processor visible in the pipeline lists over the bridge?
  Does a ticked "Activate" change the activation property during a
  MicroClaw-started MDA? Does the 1 Hz read leave the cadence unchanged? And,
  the converse of the source reading: does "Activate" stay inert during a
  MicroClaw (pycro-manager) acquisition?
- **V6** for EMU, including E5's skill-described live-instance access route,
  start/status/stop, checkbox synchronization, bounds and export if available.

## Blocks

| block | content | where it is proved |
| --- | --- | --- |
| **88a** | E1, E2, E6: settings route and dialog update, non-blocking supervision and direct abort on both routes, writer completion, lifecycle record, install-admission gate, `saveMode` hash, emitter exec tests. Closes `R86`. | demo: V1–V4 and literal-command gate; fake blocked-call/long-interval cases |
| **88b** | E4: `analysis` on typed ND-TIFF MDA, actual datastore path, acquisition/writer notifications and progress metrics when 87a is present. Independent of htSMLM/plugin work. | demo with a fixture package; growing reader, cancellation and finalized index |
| **88c** | E3: htSMLM preflight, disclosure, trace and exhaustion stop, including its emitter. Shared EMU reader extension can land first and is also used by 89b. Manual Activate is sufficient. | M2: V5, real activation and direct-abort preservation; no cadence regression |
| **88d** | E5: inspected EMU opening, and a typed htSMLM activation operation built from the V7 spike. Implement activation control only if the full seam, bounds and export are proved; otherwise record the missing API. | V7 spike first (demo machine if htSMLM is installed there, else M2), V6 on demo for applicable operations, then M2 for EMU/htSMLM |

88a precedes 88b/88c. 88b needs no metrics protocol change to attach analysis;
live counts need 87a. 88c does not wait for 88d: manual window opening and
Activate remain usable. 88d's discovery can proceed independently; the end-to-end
MDA integration gate uses 88a/88c. Design/89's P3 is independently useful and
can run before these blocks.

## Not now

- Typed channels, slices and positions on the settings route.
- Restoring the previous MDA settings after a run (`restore_previous_settings`):
  snapshot before applying, restore only after proved termination, and skip
  with a reason if the operator edited the settings in between. Deferred until
  someone needs it (operator, 2026-10-15).
- Feeding SMAPpy's count into htSMLM's law. That is an upstream htSMLM change,
  after which htSMLM, not MicroClaw, is the writer.
- General window automation or universal plugin dispatch. Skill-informed,
  supported htSMLM calls are explicitly investigated in 88d.

## Register

`R86` closes with 88a. V5's observation confirms design/78's 78c check, and
goes in that notebook's carried-forward list.

## Run ledger

| Block | Branch | Start commit | Status |
|-------|--------|--------------|--------|
| notebook | — | `2a6f7ee` | drafted 2026-10-14, revised 2026-10-15, not opened |
