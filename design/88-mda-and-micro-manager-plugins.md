# Driving Micro-Manager's own side — the MDA, htSMLM activation, plugin windows

**Status:** 88a done (PR #58, demo gate 15/15 on 2026-10-17; closes `R86`). 88b–88d not opened.
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

## What `run_mda` was before 88a

| today | where | consequence |
|---|---|---|
| it runs only what the dialog holds | `tools.py:12064`; `get_mda_settings` previews, `run_mda` checks a hash | MicroClaw cannot set anything up |
| one **blocking** call, `manager.run_acquisition()` | `tools.py:~12145` | pyjavaz holds one lock per round trip, so no other call reaches the core for the whole run; no cancel; a teardown hang hangs MicroClaw |
| unsupervised | `R86` | no `design/60` bound, no `design/75` D4 record; exempt from `authorize_path` (`tools.py:12331`); outside the install-admission chokepoint (`tools.py:4750`) |
| no `analysis` | `tools_schema.py:2539` | SMAPpy cannot watch an MDA |
| export `@refuses` | design/63 | its reason, "a standalone script has neither the studio nor the settings", is outdated for MicroClaw-written settings: exported scripts already build `Studio()` (`tools.py:1603`, `:2461`) |
| the preview hash omits `saveMode` | `_MDA_SCALARS`, `tools.py:~11993` | the save format can change after preview |

## Decisions

### E1 — `run_mda(settings=…)`: MicroClaw builds the MDA

**The dialog route is removed (operator decision, 2026-10-17).** Every
MicroClaw session runs `validate_live_rig`, which lists `mmstudio-mda` as
`excluded`, so `run_mda(preview_token=…)` had been refused in every session
since design/33 block 5 (`2599869`), not only on rigs with an authorization
profile. `run_mda` is settings-only. `get_mda_settings` stays as a read-only
view of the MDA window, with no token. A dialog route returns only with its own
authorization decision.

```json
{"n_frames": 50000, "interval_s": 0, "exposure_ms": 15, "name": "cell3_dstorm",
 "save_dir": "D:/data/cell3", "save_mode": "ND_TIFF"}
```

- **`save_dir` is required** and means what `run_timelapse`'s does, resolved
  through `guard.resolve_in_workspace` (coordinator decision, 2026-10-16:
  `workspace_dir` is optional, so there is no root to default to).
- **`save_mode`** defaults to `ND_TIFF`; the MDA window's "Image stack file" is
  `MULTIPAGE_TIFF` and "Separate image files" is `SINGLEPLANE_TIFF_SERIES`
  (operator, 2026-10-17: TIFF must be verified like NDTiff; see E2).
- A **fresh builder**, never `copyBuilder()`, with every axis set explicitly,
  `use_custom_intervals` and `is_test_acquisition` included. Exposure goes
  through `guard.check_exposure` and is set on the core.
- **Over the bridge, an enum constant is not reachable** (spike, demo machine):
  `Datastore$SaveMode`'s static shadow exposes only `value_of` and `values`, so
  the mode is `value_of("ND_TIFF")`. **An enum read back is an object shadow**,
  and its `str()` carries a memory address that changes on every read, so the
  recorded and hashed text is `.name()`. Gate run 1 failed every MDA on the
  first; the second would have made every preview token unique.
- **The dialog follows.** `runAcquisitionWithSettings(built, false)` alone made
  the MDA window show the requested Count, Name prefix and save format, as
  checked against screenshots in gate runs 2 and 3. No second setter call.
- **Settings that ran** are read back from MM's own summary metadata
  (`SummaryMetadata.getSequenceSettings()`); MicroClaw writes nothing into the
  datastore.
- **The save folder is MicroClaw's to create.** `AcquisitionWrapperEngine.
  runAcquisition` opens a modal "Directory not found. Create it?" on the bridge's
  thread for any root it cannot write. That blocks every bridge call until a
  human answers, and it hung Micro-Manager twice on 2026-10-17. Before the
  confirmation, `run_mda` refuses unless the nearest existing ancestor is
  writable and has more than `1.25 ×` the image bytes free (MM's own
  `enoughDiskSpace`, whose `getTotalMemory` equals the plan's bytes for this
  route). After consent it creates the folder. A `null` datastore from MM is
  `failed`, never supervised.

**No settings restoration in v1 (operator decision, 2026-10-15).** The run
leaves its settings in the dialog. See §"Not now".

### E2 — non-blocking and supervised (closes `R86`)

- **One worker owns every bridge call** (start, poll, abort, finalization). The
  foreground makes no Java call, so it returns within the bound even when a call
  hangs, and never queues a second call behind a hung one. It waits on a
  completion `Event`, never on `Thread.join`: on CPython 3.12 an interrupted
  `join` marks the live worker stopped, after which `is_alive()` lies (measured
  in the gate selftest; `R155` holds the pycro-manager path's copy of the hazard).
- **Bound**: design/60's runtime ceiling **and** quiet-window qualification,
  with startup and planned-interval allowances. Image-count polling is coarse
  progress, not frame timing. A later acquisition tool is refused **without a
  bridge probe** while the worker lives.
- **Cancel**: web UI Stop (the session cancel event) or Ctrl-C requests
  `abortAcquisition()` exactly once; supervision continues to the bound. An
  observation failure is recorded and never aborts. Outcome (`completed`,
  `cancelled`, `failed`, `unterminated`) and diagnostics are separate.
- **Writer completion**, checked only after engine idle **and** `isFrozen()`,
  because `DefaultDatastore.freeze()` sets the flag *before* the storage flushes
  and MM swallows a failed flush:

  | save mode | proved when | measured |
  |---|---|---|
  | `ND_TIFF` | ndstorage reopens it with `getNumImages()` index entries | frozen and complete within 10 ms of engine stop |
  | `MULTIPAGE_TIFF` | every `*.ome.tif` carries `MultipageTiffWriter.close()`'s markers (483765892 at byte 16, 99384722 at 24) and is no longer the 4 GiB pre-allocation, and pages summed **per file** equal the count | at `isFrozen()` both stacks of an 8,500-frame run were exactly 4,294,967,296 bytes and every file changed afterwards; one series spans all files |
  | `SINGLEPLANE_TIFF_SERIES` | one `*.tif` per image under the save path, each at least the pixel bytes, from `stat` only (operator decision) | opening freshly written files cost ~30 ms each on the demo machine (`R156`); nothing changes after `isFrozen()` |

- **Record**: design/75 D4 lifecycle records and one `duration_breakdown` with
  its residual named; progress events carry `frames_planned`.
- **Admission and authorization**: one shared install-admission check, and an
  `acquisition-tool:run_mda` row (operator decision, 2026-10-16). Studio's
  processor pipeline stays outside the map; E3 discloses htSMLM's part.

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

**From 88a, for 88b.** The save path is non-null at the first poll. An MDA
ND-TIFF has four axes (`channel`, `position`, `time`, `z`). The on-disk index
was sometimes one entry ahead of `getNumImages()` while growing. The
supervisor already exposes a private `notify` seam that carries the lifecycle
points (`acquisition_finished` with outcome, then `writer_finished`), one
per-poll step, and one abort latch for 88c's dwell rule.

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

### E6 — export: the settings run emits as plain pycro-manager

`run_mda` is `@emits`. The script inlines, by `inspect.getsource`, the same
builder, settings reader, storage preflight and folder creation, supervisor,
writer-completion predicate and clock as the live tool, reaches `SaveMode` with
`value_of` through the inlined `_new_static_java_class`, uses the bridge port
the script's own `core` was created on, prints its envelope and says htSMLM
activation is not reproduced. It refuses (`CannotEmit`) for a cancelled or
unterminated record (that would replay more than ran) and when the recorded
safety limits are missing. Gate runs 2 and 3 ran the exported script standalone
on the demo machine: exit 0, 1,500 frames.

## What 88a built and measured (2026-10-17)

E1, E2 and E6 as decided above. Demo machine, demo config, 512 × 512 uint16,
10 ms exposure, n = 3 per arm unless stated.

| measurement | result |
|---|---|
| V1 start call | returns with a datastore while acquiring: 0.03 s typical; 0.10 s into a folder MicroClaw had just created |
| V1 dialog | follows the requested settings (screenshots, runs 2 and 3) |
| V2 save path | non-null at the first poll |
| V2 growing reads | 32/32 ndstorage reads succeeded while acquiring; the index was sometimes one entry **ahead** of `getNumImages()` (matters to 88b and design/89) |
| V2 finalization | NDTiff 0.12–0.32 s, stack 0.11 s, separate files 0.03 s after the count + size change (35.96 s before it) |
| V3 gaps (report only) | MDA p50 11.2–11.9 ms, p95 12.0 ms; `run_timelapse` p50 11–12 ms, p95 12 ms |
| V4 abort (report only) | 1.1–1.2 s from request to tool return, ~90 frames after the request (one 1 s poll), 4/4 per run; dataset readable, index = count, writer proved |
| V4 no prompt | no confirm-abort window (operator, runs 2 and 3) |
| export | standalone script: exit 0, 1,500 frames, runs 2 and 3 |

**Gate runs.** Run 1: 2/14. Every MDA failed on `getattr(modes, "ND_TIFF")`.
Run 2: 15/15, but scoring its artifacts found the 36 s separate-files check;
MM's directory dialog (the operator answered it, so the first start's 7.9 s was
a human, not warm-up); and the agent telling the operator Stop would not stop a
zero-interval `run_mda`. Run 3: 15/15, with every fix. The agent repeated
the Stop misstatement, despite `run_mda`'s own description, in both sessions;
the text now sits on `interval_s` and is to be checked in 88b's session (`R01`).

## Probes — each before its owning block's gate

V1–V4 were measured by 88a's demo gate (`design/88-block88a-demo-gate.*`) and
three spikes (`design/88-spike-*`); results are in §"What 88a built and
measured". No separate `design/88-probe-mda.py` was written.

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
| **88a** ✓ | E1, E2, E6: settings-only `run_mda` (dialog route removed), dialog update, non-blocking supervision and direct abort, writer completion for NDTiff and both TIFF modes, folder and disk preflight, lifecycle record, install-admission gate, export. Closed `R86` (PR #58). | demo gate 15/15 (run 3, 2026-10-17); three spikes |
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

`R86` **closed by 88a** (PR #58). 88a opened `R155` (CPython's interrupted
`join` on the pycro-manager path) and `R156` (opening freshly written files on
Windows), and added its Stop-wording evidence to `R01`. V5's observation
confirms design/78's 78c check, and goes in that notebook's carried-forward
list.

## Run ledger

| Block | Branch | Start commit | Status |
|-------|--------|--------------|--------|
| notebook | — | `2a6f7ee` | drafted 2026-10-14, revised 2026-10-15, not opened |
| 88a | `block-88a` | `d90e6f6` | closed 2026-10-17, PR #58: demo gate 15/15 on run 3; six runner rounds, three spikes |
| 88b | `block-88b` | `971a4b4` | opened 2026-10-17 |
