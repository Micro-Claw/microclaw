# A finished frame is not a finished acquisition

Status: **proposed**, nothing implemented. Written 2026-09-04 from
`m2-tirf-stage-freeze/20260904_115958_681706_microclaw_history.jsonl` and
`m2-tirf-stage-freeze/CoreLog20260904T103846_pid6032.txt` supplied by the
operator. Reviewed against the code and against pycro-manager 1.0.2's source
the same day; §Review records what that changed.

## Incident

During a TIRF-position sweep, the final call was:

```json
{"name":"run_timelapse","input":{"n_frames":1,"interval_s":0,
 "save_dir":"F:\\DataSSD\\260904_AA_CLC-SNAP_AF647_microclaw",
 "name":"260904_AA_CLC-SNAP_AF647_microclaw_TIRFsweep_fine_4um",
 "exposure_ms":50,"laser_slot":3}}
```

The history ends on that tool call (line 134). There is no matching tool result
and no exception. The web GUI continued to say that MicroClaw was working until
the operator closed MicroClaw; restarting it restored operation. It was the
14th `run_timelapse` of the session; the previous 13 returned.

This was not the TIRF stage hanging. Immediately before the call,
`move_named_stage(device="TIRF Stage", um=4)` returned at 4.1 um after 0.407 s,
within its 2 um tolerance. The following `snap_and_analyze` also returned a
complete image analysis. The only unmatched operation is `run_timelapse`.

## What the two artifacts establish

1. **The request reached MicroClaw.** The append-only history contains the tool
   call and no result. This distinguishes a server-side tool stall from a result
   that the assistant received but failed to discuss.

2. **Micro-Manager did not deadlock globally.** Its Core log continues through
   12:47:43. It services snaps and GUI refreshes after the acquisition window.
   In particular, GUI refreshes finish normally at 12:42:29, 12:45:40,
   12:45:59, 12:46:08, 12:47:13, 12:47:28 and 12:47:35. Later snaps reach both
   `GetImageBuffer` and `PrepareSnap`.

3. **The Andor adapter reports no error at the end of the session.** The final
   part of the log contains normal `Snap Image`, `GetImageBuffer`, and
   `PrepareSnap` records. The last `[ERR,` line of any kind is the TIRF Stage
   `Invalid property name: Position` at 12:36:43, before three more sweep
   points completed. There is no camera error, storage error, Java exception,
   or stuck sequence corresponding to the unmatched call, and the last EDT hang
   report is at 11:55:54, four minutes before this session even started.

4. **The artifacts do not reveal whether an exception reached the acquisition
   object.** `_notification_handler_fn`'s `except` calls
   `acquisition.abort(e)`, which sets `acq._exception`; the supervisor polls that
   field every second and, once it sees it, returns a typed
   `AcquisitionUnterminated` after `ERROR_TEARDOWN_GRACE_S = 90 s`. That grace
   begins when the exception is observed, not when acquisition begins. The
   history holds no result, but without the exception timestamp and the time of
   restart it cannot distinguish a silent block from an exception whose grace
   had not yet expired. How long the operator waited is still useful context,
   but it cannot rule out the raised-and-aborted path by itself (§Evidence still
   owed).

5. **The log cannot attribute the sweep's own snap, and the tail is not the
   sweep.** The strongest structure in the log is a strict alternation of two
   thread ids ~6 s apart, repeating every ~25 s: `tid6284` then `tid10288`,
   from 12:37:22 to 12:42:25, which is the sweep's snap/acquire pairing at the
   cadence the history implies. But the pattern *continues* at 12:45:45/12:45:51
   and 12:47:17/12:47:24 — after the history has ended — and between them sits a
   live sequence started and stopped on `tid17744` (MMStudio's own thread, the
   one that ran the earlier Clojure-AcqEng MDAs) from 12:44:55 to 12:45:03. That
   is consistent with an operator investigating a frozen client in MMStudio,
   not proof of who initiated it.
   Nothing in the log labels a snap with a MicroClaw tool-use id, so no snap can
   be assigned to the unmatched call, and the tail must not be read as evidence
   that the sweep continued.

6. **A frame was probably written, but the dataset cannot prove the callback
   fired.** The operator should check for `...TIRFsweep_fine_4um[_N]` and read
   its `NDTiff.index` (design/60's precedent for a dataset too large to copy;
   here it is one small frame). One index entry proves *Java* wrote the frame.
   It does not prove MicroClaw's `image_saved_fn` ran — those are two different
   facts separated by exactly the thread this incident is about, and §Root cause
   turns on which of them happened.

7. **There is no evidence that image brightness caused the hang.** The preceding
   analysis reports max 14,839 with `saturated_fraction=0.0`. Earlier, dimmer
   and brighter content used the same acquisition path successfully. Pixel
   values do not participate in pycro-manager completion signaling.

The early Core-log errors are unrelated: Thorlabs ELL9/COM14 errors occur around
10:43, almost two hours earlier, and the twelve `Clojure AcqEng` acquisitions at
10:50-11:09 predate this history file (it opens at 11:59:58) and are MMStudio's
MDA engine, not pycro-manager.

## Root cause

### The failure boundary is certain; the trigger is not

`run_timelapse` submits the event in `_acquire_with_hooks`, then starts
`microclaw-acq-teardown`. That thread calls `acq.__exit__`, which marks the
acquisition finished and waits for pycro-manager's completion/notification and
storage threads. The foreground tool polls that thread and cannot return the
normal result until it exits. That much is read directly off the code, and it is
the same boundary design/60 identified.

What the artifacts support is narrower than "teardown did not complete": they
support **teardown had not completed within the few minutes the operator
watched**, with no engine exception visible in the supplied artifacts (finding
4). Whether it was stuck
or merely slow is unmeasured, because the operator restarted before any bound
could fire — the current bound is ~15 minutes (below) and the log's own tail
ends 5 minutes after the call. This is not the same trigger as design/60's
100,000-frame NDTiff rollover: one small frame, no 4 GiB boundary, no logged
storage error, no Andor sequence left running.

That distinction does not change the fix — a bound is owed either way — but it
does forbid describing the upstream cause as established, and it forbids any
camera or notification workaround built on top of it.

### The current bound is ~900 s for this shape, and the frame is not why

`_runtime_ceiling_s` gives `max(est * 1.5, est + 300)`; for one 50 ms frame that
is ~300 s. Expiry additionally requires `quiet`, and `_stall_quiet_s` floors the
quiet window at `STALL_QUIET_FLOOR_S = 15 * 60`. So the tool returns at roughly
`max(started + 300, last_saved + 900)`.

**`frame_state["last_saved"]` is initialised to `started`, not to `None`.** So
the 900 s floor governs identically whether or not a frame was ever accounted:
this is ~15 minutes from submission either way. The original draft attributed
the delay to "the last saved frame resets the quiet clock"; that is not the
mechanism, and the correction matters because it means the observed symptom
carries **no information** about whether the saved-frame callback fired.

Fifteen minutes is defensible for a position-dominated acquisition that may
legitimately pause between frames, and for finalizing a very large dataset. It
is not defensible for a one-frame, 50 ms, `interval_s=0` acquisition, whose own
plan says it can have no legitimate inter-frame gap at all. And because an
operator's patience is minutes, a bound of 15 minutes reliably produces **no
record**: the typed result never reaches history, which is why this incident
arrived as two artifacts that cannot close it.

### The trigger this fix must survive: the callback shares the failed path

This is the finding that reshaped the design. In pycro-manager 1.0.2,
`image_saved_fn` is **not** an independent signal:

* `_notification_handler_fn` (one thread, one PULL socket) receives every
  notification. For an image-saved notification it calls
  `dataset.add_index_entry` and puts it on `_image_notification_queue`; for
  `data_sink_finished` it puts that on the *same* queue.
* `_storage_monitor_fn` drains that queue and is what actually invokes
  `image_saved_fn`; it breaks out on `data_sink_finished`.
* `await_completion`'s `finally` joins the storage-monitor and notification
  threads unconditionally.

So the saved-frame callback and the terminal notification arrive over the same
socket, on the same thread, through the same queue. If the notification path is
what failed — the leading hypothesis — then `frames_accounted` is **0**, and any
fix predicated on `frames_accounted >= plan.frames` never activates for the very
incident it was written for. It activates only in the narrower case where the
image-saved notification was delivered and only the terminal one was lost.

A bound must therefore not be gated on the callback. A phase signal built from
the callback is still worth having (it is what makes the UI honest), but it
cannot be load-bearing for the timeout.

### Medium confidence: live-view ownership may be the upstream trigger

The Core log repeatedly shows effectively unbounded Andor live sequences
(`2147483647 images at 0 ms`) being stopped around snaps and restarted. The
session's `snap_and_analyze` calls borrow and restore live mode, then a separate
pycro-manager acquisition immediately takes camera ownership. That ownership
churn is a plausible source of a missed terminal notification or race, but the
same sequence succeeded 13 times earlier in this session. Treat it as a
reproduction variable, not as the root cause and not as a basis for a
speculative camera workaround.

## Evidence still owed, before any code

Three cheap facts, none of them a rig booking, that between them decide whether
D1 is even the right predicate:

1. **How long was the spinner up, and when exactly was MicroClaw closed?** This
   locates the observed wait relative to the ~300 s runtime precondition and
   ~900 s quiet floor. It does not by itself rule out the aborted-engine path,
   because that path's 90 s grace begins only when `_exception` is observed.
2. **Did the session ever show frame progress for that call?** `run_timelapse`
   already emits `acquisition_progress` with `frames_accounted`, and always
   publishes the planned-final frame, so a 1-frame run that reached
   `image_saved_fn` renders `1/1` in the browser (`serve.html:512`). Progress
   shown means the notification path delivered the image and lost only the
   terminal signal; no progress means it delivered neither. **This single
   observation discriminates the two triggers**, and it is the one the bundle is
   missing because diagnostics go to the event sink or stderr, never to the
   history file (D4).
3. **Does the dataset exist, and does `NDTiff.index` hold one entry?** That
   settles what Java wrote, which is not the same question as (2).

## Design

### D1 — pass an explicit supervision policy; do not reverse-engineer the plan

`AcquisitionPlan` does not encode topology or execution semantics. It has an
aggregate frame count and duration, but no scheduled interval, position count,
hook presence, or promise that every frame will be produced. Do not infer these
from `estimated_duration_s`, `hardware_sequenced_burst`, whether
`runtime_plan is plan`, or any other incidental combination.

Add an explicit immutable `AcquisitionSupervisionPolicy`, passed separately to
`_acquire_with_hooks`:

```python
@dataclass(frozen=True)
class AcquisitionSupervisionPolicy:
    quiet_floor_s: float
    runtime_slack_s: float
    reason: str
    terminal_frames: int | None = None
```

`runtime_slack_s` is in the policy because **the quiet floor alone does not move
this bound** (see "Why both terms" below). Both terms are named, both are
reported, and a reader of a timeout result can see which one expired.

The caller selects one of two initial policies:

```python
DEFAULT_QUIET_FLOOR_S = 15 * 60.0
DEFAULT_RUNTIME_SLACK_S = 300.0        # today's _runtime_ceiling_s constant
SHORT_FIXED_QUIET_FLOOR_S = 30.0
SHORT_FIXED_RUNTIME_SLACK_S = 30.0     # provisional; gate limb 3 sets it

# Only a fixed, list-backed acquisition whose event list has exactly one event,
# has no hook, no per-frame action plan, and no shared composite reservation
# may select this.
SHORT_FIXED = AcquisitionSupervisionPolicy(
    quiet_floor_s=SHORT_FIXED_QUIET_FLOOR_S,
    runtime_slack_s=SHORT_FIXED_RUNTIME_SLACK_S,
    reason="single fixed event; no inter-frame work",
    terminal_frames=1,
)

DEFAULT = AcquisitionSupervisionPolicy(
    quiet_floor_s=DEFAULT_QUIET_FLOOR_S,
    runtime_slack_s=DEFAULT_RUNTIME_SLACK_S,
    reason="acquisition cadence or topology may contain unmeasured work",
)
```

There are exactly five internal callers to convert, and four of them take
`DEFAULT` unconditionally: `run_zstack`, `_acquire_positions_with_hook`,
`_acquire_survey_with_detector` and `run_adaptive_survey`. Only
`run_timelapse`'s fixed branch can select `SHORT_FIXED`, and only after event
and hook construction. Enumerate them in the test, not by eye.

One case must be decided rather than inherited: a `run_timelapse(n_frames=1,
_reservation=...)` child of a composite satisfies every condition listed above,
because the composite's stage motion happens *between* children and outside the
supervised window. It should still take `DEFAULT` in this block — its
reservation and restoration are shared, and the healthy latency measured in the
gate is for a standalone run. State that in the selection code, so the next
reader does not have to re-derive it.

Start deliberately narrow: this incident needs the single-event policy. Do not
generalize it to arbitrary short timelapses, z-stacks, positions, tiles, hooks,
generators, or adaptive caps until their legitimate silence is separately
bounded. `run_timelapse(n_frames=1)` supplies `SHORT_FIXED` only after event and
hook construction proves those conditions; every other caller supplies
`DEFAULT`. A missing policy is an error at internal call sites rather than an
implicit short timeout.

Change `_stall_quiet_s` and `_runtime_ceiling_s` to accept the selected policy
and retain observed-cadence widening:

```python
def _stall_quiet_s(largest_observed_gap_s, policy):
    return max(policy.quiet_floor_s,
               STALL_GAP_MULTIPLIER * largest_observed_gap_s)

def _runtime_ceiling_s(plan, policy):
    if plan is None:
        return FALLBACK_RUNTIME_CEILING_S, True
    est = plan.estimated_duration_s
    return max(est * 1.5, est + policy.runtime_slack_s), False
```

### Why both terms, and not the quiet floor alone

Expiry in `_acquire_with_hooks` is a conjunction: `now >= runtime_deadline and
quiet`. With only the quiet floor changed, a one-frame run expires at

    max(started + ~300, last_saved + 30)  ==  started + ~300

because `last_saved` is initialised to `started`. The quiet condition clears at
+31 s and then waits 269 s for the runtime precondition. **The chosen 30 s does
no work**: 1 s, 30 s or 200 s all produce the same ~300 s bound, and the whole
reduction from 900 s comes from removing the floor's dominance rather than from
the value selected. Two consequences follow, and both are why the slack belongs
in the same policy:

* **Gate limb 3 would measure a number that cannot move the bound.** "Choose the
  quiet floor with 10x headroom over the maximum healthy finalization" changes
  nothing unless that maximum exceeds 300 s — in which case the floor is not the
  safeguard that needs raising.
* **Five minutes may be past the patience this incident demonstrated.** The Core
  log's tail ends 5 min 18 s after the sweep's last pair, which is an upper
  bound on the observed wait and not a measurement of it — the doc does not know
  when MicroClaw was closed, which is why §Evidence still owed asks. But a bound
  the operator beats produces no typed result, and that is D2's entire purpose,
  so shipping ~300 s risks closing the block without changing what the next
  occurrence leaves behind. The owed spinner duration decides this, and it should
  be in hand before the slack constant is fixed.

`est + 300` is a generic slack constant, as unjustified for a single 50 ms event
as the 900 s floor was; it is not a measured end-to-end bound for this shape, and
gate limb 2 measures exactly the quantity that would justify one. Naming it
`runtime_slack_s` in the policy does not repurpose the quiet floor to do two
jobs — it gives each job a field, which is the same objection that produced the
policy object in the first place. With both at 30 s the bound is ~30-60 s, which
is what D2 was written to deliver.

This policy fires whether `frames_accounted` is 0 or 1, which is the property
the callback-gated version does not have. It also makes the reason for shortening
the floor part of diagnostics and tests instead of reconstructing intent from an
aggregate accounting object.

Do not infer completion from an idle camera. A software-paced acquisition is
normally idle between frames.

### The raise path must not depend on a bridge read

One hazard was raised against the existing timeout path and its stated mechanism
does not hold, but the conclusion it points at is worth keeping.

The mechanism as stated — that a blocked teardown call could hold the lock the
supervisor's `is_sequence_running()` needs — is **checked and false**.
pyjavaz's `send_and_receive` does hold `_communication_lock` across a whole
round trip, but bridges are cached *per port*, and pycro-manager builds its
`RemoteAcquisitionFactory` with `new_socket=True` for exactly this reason (its
own comment: "so that it can have blocking calls without interfering with the
main socket"). `self._acq` is returned from that factory, and pyjavaz binds a
returned object to its parent's bridge — `bridge.py`'s "inherit socket from
parent object". So `wait_for_completion()` blocks a *dedicated* port's lock,
never `ctrl.core`'s. The incident's own artifacts agree: the Core log keeps
servicing snaps and GUI refreshes through 12:47:43, five minutes into the hang,
so the main port was demonstrably not wedged.

What survives is the general form. `_camera_sequence_running` is a synchronous
bridge read that sits between the expiry decision and the `raise`, and it feeds
the `_microclaw_unterminated_acquisition` flag that is written first. A bound
whose delivery depends on a round trip is not fully bounded — MM's own EDT hang
reports earlier in this very log are a reminder that the main port can stall for
unrelated reasons. Two cheap requirements, neither of which needs new
machinery:

* Build the pending flag and the exception first, and treat the camera state as
  a best-effort enrichment — `_camera_sequence_running` already returns `None` on
  failure, and `_unterminated_result` already has a "could not read" branch, so
  the unknown is representable.
* Where a bound read is wanted, use `controller._bridge_call`, which already runs
  one bridge interaction on a daemon thread with a labelled timeout. Do not add
  a second mechanism for this. Its generic default is 30 s, which is too large
  for an enrichment on a 30–60 s failure path, so pass an explicit
  `CAMERA_STATE_PROBE_GRACE_S = 2.0`:

```python
try:
    camera = controller._bridge_call(
        "camera sequence-state probe",
        lambda: bool(ctrl.core.is_sequence_running()),
        timeout=CAMERA_STATE_PROBE_GRACE_S,
    )
except Exception:
    camera = None
```

The exception and pending flag already exist before this probe. Probe timeout or
failure therefore enriches the report with `camera_sequence_running: null`; it
never replaces the acquisition result with `_BridgeCallStalled`.

A test asserts that a hung `is_sequence_running` still produces the typed result
with `camera_sequence_running: null` within the complete delivery ceiling:

```text
runtime/quiet expiry
+ _ACQUISITION_POLL_S
+ CAMERA_STATE_PROBE_GRACE_S
+ DIAGNOSTIC_FLUSH_GRACE_S
```

With the provisional constants, a no-callback one-frame hang returns within
about 35 s of submission; a terminal callback arriving just before the runtime
deadline can extend the quiet term, but total delivery remains approximately
65 s. These are ceilings, not exact timestamps: scheduler overhead is allowed a
small assertion tolerance in tests.

### D1a — the finalizing phase, as a report and not as a bound

Keep the phase transition from the original draft, downgraded: when
`policy.terminal_frames` is set and `frames_accounted >= policy.terminal_frames`,
record `finalizing` and report it (D3). Key it off the policy, not off
`plan.frames` — two sources for one predicate is the defect the policy exists to
remove, and `plan.frames` is a cap on the adaptive route. It may *tighten* the deadline; it must never be the only thing
that can end the wait, and it must never *loosen* one.

Two constraints on it:

* **The cap/promise distinction must be explicit, not inferred.**
  Inside `_acquire_with_hooks`, a fixed `plan` and an adaptive `cap_plan` are
  indistinguishable — `run_timelapse`'s `max_frames` branch reaches the same
  supervisor through `_acquire_survey_with_detector(accounting_plan=cap_plan)`.
  The only available proxy is `runtime_plan is not plan`, which is an accident of
  how the adaptive branch widens its ceiling. CLAUDE.md's `hook_strategy` lesson
  is exactly this shape: carry `terminal_frames` in the explicit supervision
  policy only when a caller promises every frame. The default policy leaves it
  `None`.
* **Any added deadline is a `min` with the existing policy.** The draft's
  `estimated_bytes / (10 MiB/s)` term exceeds 900 s for any dataset above ~9 GB,
  so as a *separate* deadline it would have loosened the bound on precisely
  design/60's incident. A new bound that can extend an old one is a regression.

The byte term is also modelling the wrong quantity. `image_saved_fn` fires after
the image is on disk (the code comment in `account_saved_frame` says so, and the
notification carries the written index entry), so when the last frame is
accounted the bytes are already written; what remains is index and close work,
which scales with frame *count*, not bytes. If a scaling term is wanted, scale
it by frames — and measure it before choosing a constant, because nothing in the
draft's gate measured the byte term at all.

Adaptive `max_frames` runs stay on `DEFAULT`: their accounting plan is a cap,
not a promise, so they have no terminal-frame signal.

### D2 — return a specific supervised-runtime failure

At the bound, raise the existing `AcquisitionUnterminated` with a new
`expired_bound="short_fixed_runtime"`. `_unterminated_result` already renders `expired_bound`,
`frames_planned`, `frames_accounted`, `camera_sequence_running`,
`teardown_running`, `dataset_path`, `hardware` and `next`, so the work is a bound
name and the wording. The neutral error is "The acquisition did not terminate
within its supervised runtime bound." It should say:

* how many planned frames were accounted, **including zero** — a bound that can
  fire with `frames_accounted: 0` must not describe the data as saved;
* the dataset path, and that the dataset may be unterminated;
* the measured camera sequence state and background-teardown state;
* that new acquisitions remain refused until the waiter exits.

Do not return `"Timelapse complete."`. Saved frames and cleanly finalized
storage are two different claims.

Do not call this a finalization failure when `frames_accounted == 0`: the
artifacts and callback path cannot distinguish acquisition, notification and
finalization stalls in that case. Report `phase="finalizing"` and use
finalization-specific wording only after `policy.terminal_frames` was actually
observed. Otherwise report `phase="acquiring_or_notifying"` and preserve the
unknown rather than converting it into a diagnosis.

The refusal this promises already exists and needs no work: `execute_tool`
checks `_microclaw_unterminated_acquisition` for every
`_microclaw_acquisition_entry_point` and clears it only when the camera is
measured idle *and* the waiter is dead. Design/60's ownership rule is preserved
— the daemon waiter keeps teardown, restoration and reservation closure.

### D3 — surface supervisor phase and deadlines in progress events

`acquisition_progress` already carries `frames_accounted`, `frames_planned` and
the gap summary. Add `phase` — `acquiring`, `finalizing`, or, at a timeout with
no accounted frame, D2's `acquiring_or_notifying` — the active deadline,
and `dataset_path`, and emit on the phase transition and on
timeout/completion — no heartbeat loop. The GUI can then say "1/1 frames saved;
finalizing dataset" instead of "MicroClaw is working".

This is a disclosure, not a confirmation: it must never block or prompt. It goes
to the acquisition event sink, which is where information goes.

### D4 — record enough evidence for the next incident, where a restart cannot lose it

Diagnostics currently go to the acquisition event sink, and to stderr only when
no sink is set. Neither survives the operator's response to this failure, which
is to close MicroClaw — which is why this incident arrived with no server-side
record at all. **D4's events must be appended to a file** (alongside the history
JSONL, not inside it: the history's append-only tool-result contract is correct
and must not be relaxed). Record per acquisition:

* the session and tool-call correlation id, so a snap in the Core log can be
  matched to a call — the gap that made finding 5 unresolvable. `execute_tool`
  does not currently receive the tool-use id, so this is plumbing, not a field;
* acquisition construction, event submission, first frame accounted, sampled
  progress, final frame accounted, `mark_finished`, and teardown completion
  timestamps;
* dataset path, planned/accounted frames, estimated bytes, and the active bound
  with the plan term that produced it;
* `acq._exception` when present;
* camera sequence state at timeout.

Do not append and flush synchronously from `image_saved_fn`. That callback sits
on pycro-manager's storage-monitor path and high-rate acquisitions can contain
hundreds of thousands of frames. Feed diagnostics to a bounded, thread-safe
queue drained by one writer; the callback's operation is a non-blocking enqueue.
Persist lifecycle transitions immediately, but sample ordinary progress at the
existing at-most-once-per-second cadence. Coalesce progress when the queue is
full; never drop construction, submission, exception, timeout, or teardown
completion records. Flush lifecycle records at a bounded periodic cadence, and
close/flush the writer during normal session shutdown.

A timeout record requests a writer acknowledgement, but waits no more than
`DIAGNOSTIC_FLUSH_GRACE_S = 2.0`. If acknowledgement arrives, the result carries
`diagnostic_persisted: true`. If the queue, writer or filesystem does not
acknowledge within that bound, the tool returns anyway with
`diagnostic_persisted: false` and emits the same record to stderr and the event
sink. Logging must never recreate the unbounded wait it diagnoses. Tests cover
both outcomes: the acknowledged case proves the record is on disk before the
result, while the failure case proves persistence delays the result by no more
than the named grace.

### D5 — the live-view reproduction matrix is a register row, not this block

The draft asked for four arms of 100 one-frame acquisitions. That is a
discrimination experiment for a medium-confidence variable, and it is more
operator time than this block needs to ship a bound. Open it as a register row
in `design/70` (next free id is R91) — "does live-mode ownership churn precede a
lost pycro-manager terminal notification", with the arms as written, unique
dataset names, D4 timestamps, and a thread dump of `microclaw-acq-teardown` plus
pycro-manager's notification/storage threads preserved before any restart.

Only change live-mode handling if that matrix discriminates an arm. The current
artifacts do not justify such a change, and this block must not carry one.

A second register row is owed: **`run_mda` bypasses `_acquire_with_hooks`
entirely** (R86 already says so), so none of D1-D4 reaches it. Say that in the
block's close-out rather than discovering it later.

## Verification

### Deterministic tests — where nearly all of this settles

Every one of these runs off-rig against a fake, and per CLAUDE.md each must be
watched failing on the pre-fix tree for its stated reason before it counts.

* A fake that **never delivers a saved-frame callback** and blocks forever in
  `__exit__`: the tool returns `acquisition="unterminated"` with
  `frames_accounted: 0`, `expired_bound="short_fixed_runtime"`, and
  `phase="acquiring_or_notifying"` within the complete delivery ceiling, not at
  900 s.
  *This is the incident's likely shape, and the draft's design failed it.*
* A fake that saves 1/1 and then blocks forever: same bound, with
  `frames_accounted: 1` and `phase="finalizing"` reported.
* The one-event fixed caller selects `SHORT_FIXED`; a 1,000-position caller, a
  hook-paced caller, and a generator/adaptive caller explicitly select
  `DEFAULT`; omitting the required policy fails at the internal boundary.
* An observed inter-frame gap widens the window past the selected policy floor
  (`5x largest gap` still governs), so a slow but healthy run is not cut off.
* A fake reports an engine exception after the final frame: the earliest
  applicable deadline wins and the result retains the exception.
* A waiter that completes just before the bound returns the normal complete
  result once, closes the reservation once, and leaves no pending flag.
* A waiter that completes after timeout performs owned cleanup once;
  acquisition entry points remain refused until it is dead, then available.
* A hung `is_sequence_running` adds at most `CAMERA_STATE_PROBE_GRACE_S`, returns
  `camera_sequence_running: null`, and does not replace the typed acquisition
  result with a bridge error.
* Adaptive capped runs do not mistake "currently accounted" for "terminal", and
  its explicitly supplied `DEFAULT` policy has no terminal frame count.
* An acknowledged timeout diagnostic is on disk before the result and reports
  `diagnostic_persisted: true`. A blocked writer delays the result by at most
  `DIAGNOSTIC_FLUSH_GRACE_S`, reports `diagnostic_persisted: false`, and emits
  the record to stderr and the event sink.
* No path added here can raise a confirmation.

### M2 gate

Ship it as a **program**, not copy-paste blocks: each limb reported
independently, a limb that could not run its mechanism reports NOT EXERCISED
(never a pass), the script owns its own log, and it exits nonzero. Run it
against a bridge-shaped fake (`design/55-gate-probe-selftest.py`) before it is
pushed — Core collections expose `size()`/`get(i)` and are not iterable. Write
each step from the gate that already ran on M2 (`design/60`, `design/68`), not
from this document, and name the interpreter, the shell and the browser.

1. **Mandatory, and the only limb that tests D2:** a controlled build whose
   teardown waiter blocks after `mark_finished`, run twice — once with the
   saved-frame callback delivered, once with it suppressed. Confirm the browser
   receives the structured unterminated result within the complete delivery
   ceiling in both cases, and that a second acquisition is refused without restarting
   MicroClaw. Not "if possible": a build that cannot block is a NOT EXERCISED
   gate, and D2 would ship unmeasured.
2. Healthy one-frame runs matching the failing parameters (50 ms,
   `laser_slot=3`, unique names) at the session's own arm — a
   `snap_and_analyze` immediately before each. Enough repetitions to see the
   distribution, not 100 for its own sake; the observed rate is 1 in 14.
   Confirm `acquiring -> finalizing -> complete` and measure **end-to-end**
   latency p50/p95/max — submission to returned result — with the finalization
   segment broken out. End-to-end is the quantity limb 3 consumes; finalization
   alone cannot justify `runtime_slack_s`.
3. Choose **both** policy terms only after that measurement, with at least 10x
   headroom over the maximum healthy one-frame end-to-end latency, and record
   the two numbers in this document. `runtime_slack_s` is the term that decides
   when the operator sees a result; the quiet floor only decides whether a late
   frame can extend it. A measurement that moves neither is a finding too — say
   so rather than leaving the provisional 30 s unexplained.
4. Verify a timed-out dataset can be opened after the waiter eventually exits,
   and that the result never promised clean finalization before that point.
5. Confirm D4's file exists, survives closing MicroClaw, and contains the
   correlation id for each call.

## Non-goals

This block does not claim to fix pycro-manager's unknown upstream notification
race. It bounds and explains the failure MicroClaw owns. It does not infer
completion from camera-idle, kill teardown threads, permit a second acquisition
while late cleanup can still run, add any prompt, or change live-mode handling.
It does not shorten either bound for any caller that cannot state, at the call
site, that its acquisition contains no inter-frame work — which in this block is
every caller but one.

## Expected outcome

The most likely diagnosis is: **the one frame was probably acquired, but
pycro-manager's completion/finalization path did not finish, and MicroClaw's
supervisor then hid a recoverable condition behind a 15-minute minimum wait.**
The existing supervisor was expected to produce a typed
`AcquisitionUnterminated` after its ~900 s bound, but its current synchronous
camera-state enrichment is itself unbounded. The operator restarted before any
such result was recorded, at a timescale any operator would. Whether
the saved-frame callback survived is unknown and is what §Evidence still owed
asks for; D1 is written so that the bound holds either way. D1-D4 make the
failure bounded, visible and recorded. D5 leaves the live-view hypothesis as a
register row rather than spending rig time on it inside this block.

## Review — 2026-09-04

Reviewed against `microclaw/tools.py`, pycro-manager 1.0.2's
`acquisition_superclass.py` / `java_backend_acquisitions.py`, and both supplied
artifacts. Agreed: the failure boundary, that the current bound is ineffective
for this shape, that the result must not say "complete", and that live-view churn
is a reproduction variable and not a licence to change camera handling. Changed:

1. **D1 was gated on the signal it assumes was lost.** `image_saved_fn` reaches
   MicroClaw through the same thread, socket and queue as `data_sink_finished`,
   so on the leading hypothesis `frames_accounted` is 0 and the draft's
   predicate never fires. The bound moved into `_stall_quiet_s`; the phase
   became a report.
2. **The frame does not cause the 15 minutes.** `last_saved` is initialised to
   `started`, so the floor governs identically with zero frames accounted. The
   symptom therefore carries no information about the callback, and the missing
   `acquisition_progress` observation does — added as owed evidence.
3. **The byte term modelled the wrong work and could loosen the bound.** Bytes
   are on disk before the last callback; index/close scales with frames. Above
   ~9 GB the term exceeds 900 s, so as a separate deadline it would have
   regressed design/60's own incident. Any new deadline is a `min`.
4. **The root cause was overstated.** The artifacts show teardown had not
   completed within the ~5 minutes the log covers, not that it never would; the
   operator restarted before the existing quiet bound produced a result.
   Downgraded; neither spinner duration nor the supplied artifacts reveal whether
   `_exception` appeared, because its 90 s grace begins only when it is observed.
5. **Finding 4 of the draft read the post-hang tail as the sweep.** The paired
   `tid6284`/`tid10288` alternation ends at 12:42:25; the 12:44:55 live toggle on
   MMStudio's own thread and the later snaps are consistent with investigation,
   but do not prove who initiated them. Also: the dataset's index proves Java
   wrote the frame, not that MicroClaw accounted it.
6. **D4 would have lost its own evidence.** Diagnostics go to the event sink or
   stderr; the operator's response to this failure is to close MicroClaw. They
   must be appended to a file, and the correlation id needs plumbing because
   `execute_tool` never sees the tool-use id.
7. **The cap/promise distinction needs an explicit argument.** Inside
   `_acquire_with_hooks` a `cap_plan` and a fixed plan are identical; the only
   proxy is `runtime_plan is not plan`, which is the `hook_strategy` mistake
   again.
8. **D5 and the 100-run arms left the block.** Four arms of 100 for a
   medium-confidence variable is rig time this bound does not need; it becomes a
   register row, and the gate's mandatory limb is the injected block that
   actually tests D2 — twice, with the callback delivered and suppressed.
9. **The exception inference was too strong.** The 90 s grace starts when the
   exception is observed, not when acquisition starts, so spinner duration alone
   cannot rule out an exception. The artifacts leave that branch unresolved.
10. **The aggregate plan cannot supply D1's semantics.** Scheduled interval,
    position topology, hook work, and a promised terminal count are not fields
    on `AcquisitionPlan`. D1 now passes a narrow explicit supervision policy and
    states both terms of the initial formula and its approximately 30–60-second
    outcome.
11. **Persistent evidence cannot synchronously log every frame.** D4 now uses a
    bounded writer queue, at-most-once-per-second sampled progress, protected
    lifecycle records, and a separately bounded on-disk acknowledgement on
    timeout.

Reviewed again after the explicit-policy revision. Agreed, and it is a real
improvement: `AcquisitionPlan` carries no interval, topology or hook field, so
the earlier "derive the floor from the plan" would have required either
inference or new plan fields, and inference is the `hook_strategy` mistake. The
weakening of findings 4 and 5 is also correct — the 90 s grace starts when
`_exception` is *observed*, so spinner duration cannot rule that branch out, and
the post-hang snaps are consistent with an operator but do not identify one.
Six things changed:

12. **The 30 s quiet floor does no work.** Expiry is a conjunction, and
    `last_saved` starts at `started`, so a one-frame run expires at
    `started + ~300` whatever the floor is. The reduction from 900 s is real but
    comes from removing the floor's dominance, not from the value chosen — and
    gate limb 3 was measuring a number that cannot move the bound. The runtime
    slack joined the policy as its own field, which is the policy object's own
    argument applied one term further.
13. **~300 s may be past the patience this incident demonstrated.** The log's
    tail ends 5 min 18 s after the sweep's last pair. The restart time is not in
    either artifact, so this is only an upper bound on the observed interval,
    not proof that MicroClaw had restarted at any particular log entry. A block
    whose purpose is to leave a typed result behind should not ship a bound the
    operator is likely to beat.
14. **D1a still keyed the phase off `plan.frames`.** It now reads
    `policy.terminal_frames`; `plan.frames` is a cap on the adaptive route, and
    two sources for one predicate is what the policy was introduced to remove.
    The five call sites are now enumerated, and the composite-child case — which
    satisfies every `SHORT_FIXED` condition as written — is decided explicitly
    rather than inherited.
15. **The expected outcome was factually wrong, and it was mine.** Waiting out
    the 900 s was expected to produce a typed `AcquisitionUnterminated`, but its
    timeout path still makes a synchronous `is_sequence_running()` bridge read
    before raising. The record is missing because no result arrived before the
    operator restarted; the artifacts do not establish more than that.
16. **Diagnostic persistence acquired its own bound.** The timeout result waits
    at most 2 s for an on-disk acknowledgement, reports whether persistence
    succeeded, and falls back to stderr plus the event sink. A stuck log writer
    cannot replace the acquisition hang with another unbounded wait.
17. **D2 now preserves the unknown failed phase.** Zero accounted frames reports
    `acquiring_or_notifying`; only an observed terminal-frame callback licenses
    `finalizing`. Both use the neutral `short_fixed_runtime` bound name.

Reviewed again after the D2/D4 revision. Both additions are right and one of
them corrects me: bounding the diagnostic flush is the same lesson as the
acquisition bound itself, and "prove the record is on disk before the result"
as I wrote it would have introduced a second unbounded wait. Refusing to call a
zero-frame timeout a *finalization* failure is also correct — the three stalls
are indistinguishable at that point. Three things changed:

18. **The `is_sequence_running()` caveat's mechanism is false.** pyjavaz caches
    bridges per port and pycro-manager builds its acquisition factory with
    `new_socket=True` precisely so blocking calls cannot interfere with the main
    socket; a returned object inherits its parent's bridge. So a blocked
    `wait_for_completion` holds a dedicated port's lock, not `ctrl.core`'s — and
    the Core log kept serving snaps five minutes into the hang, which says the
    same thing from the artifacts. A hedge stated as a mechanism reads as one,
    so it is removed from the expected outcome.
19. **The conclusion it pointed at is kept, in a form that is true.** A
    synchronous `_camera_sequence_running` still sits between the expiry decision
    and the `raise`. Build the flag and the exception first, treat camera state
    as best-effort — the `None` branch already exists in
    `_unterminated_result` — and where a bounded read is wanted use the
    `controller._bridge_call` helper that already does this. Its 30 s default is
    not inherited: camera enrichment gets a named 2 s grace. One test: a hung
    `is_sequence_running` still returns the typed result, with
    `camera_sequence_running: null`, within the complete delivery ceiling.
20. **Three smaller consistency fixes.** D3 listed two phase values after D2
    introduced a third; gate limb 2 measured finalization latency while limb 3
    consumes end-to-end latency; and item 13's heading asserted what its own body
    correctly hedges.
21. **The bound now includes its enrichments.** The previous 30–60 s statement
    omitted the generic bridge helper's 30 s default, the diagnostic flush grace,
    and polling. Camera state now has an explicit 2 s grace, and tests assert the
    full runtime/quiet + poll + camera + diagnostic delivery ceiling rather than
    claiming the result appears exactly at policy expiry.
