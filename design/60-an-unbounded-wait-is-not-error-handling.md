# An unbounded wait is not error handling

Status: **proposed**, nothing implemented. Written 2026-08-28 from
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/ndtiff-crash-m2`, plus the
crashed dataset's `NDTiff.index` retrieved from the rig the same day.

## Problem

On 2026-08-28 an operator ran a 100,000-frame dSTORM stack. A pycro-manager
background thread died 62 minutes after the sequence started. Microclaw never
noticed and never returned: it displayed `MicroClaw is working…` for 95 minutes,
the last 28 of them with the camera already idle. The session was ended by
killing Micro-Manager.

The console (`serve-prompt.txt`, last lines — nothing follows them):

```
Traceback (most recent call last):
  File ".../pycromanager/acquisition/java_backend_acquisitions.py", line 204, in _notification_handler_fn
    axes = acquisition._dataset.add_index_entry(index_entry)
  File ".../ndstorage/ndtiff_dataset.py", line 257, in add_index_entry
    _, image_coordinates, index_entry = NDTiffIndexEntry.unpack_single_index_entry(data)
  File ".../ndstorage/ndtiff_index.py", line 97, in unpack_single_index_entry
    (axes_length,) = struct.unpack("I", data[position: position + 4])
struct.error: unpack requires a buffer of 4 bytes
```

`data` is `notification.payload.encode('ISO-8859-1')` — one image-saved
notification arrived carrying fewer than 4 bytes of index entry.

The history JSONL ends at line 155, an assistant turn issuing:

```json
{"name": "run_timelapse", "input": {"n_frames": 100000, "interval_s": 0,
 "exposure_ms": 50, "laser_slot": 3, "save_dir": "F:\\DataSSD\\...",
 "name": "260828_AA_U2OS_CLC-SNAP_AF647_dSTORM_30pct_100k"}}
```

There is no line 156. No tool result, no error, no record that anything went
wrong. **The traceback exists only on the server console; the session has no
memory of it at all.**

## Timeline — measured

`CoreLog20260828T111251_pid27820.txt` (local, UTC+2; the confirmation audit
lines in `serve-prompt.txt` are UTC) and the dataset's own `NDTiff.index`,
retrieved from the rig 2026-08-28 and parsed in full.

| local | frame | evidence | what it means |
|---|---|---|---|
| 12:29:45 → 12:31:27 | — | five `Started/Stopped sequence acquisition: 500 images at 0 ms` | the five phase-1 segments, all fine; **51.79 ms/frame** steady state |
| 12:33:31.675 | — | audit `auto-approved:8d50c20f…`, "100000 frames and about 83.3 minutes" | **auto-approved by a session grant created for a 500-frame run** |
| 12:33:34.745 | — | `Started sequence acquisition: 100000 images at 0 ms` | `interval_s=0` ⇒ the whole time axis hardware-sequenced into **one** burst |
| 12:33:35.494 | 0 | `Images appearing` | |
| 13:17:42 | ~50,900 | `EDTHangLogger` full thread dump | **engine completely healthy** — see below |
| **13:35:45** | **72,056** | last index entry; `pix_offset` 4,289,893,464 | **the 4 GiB NDTiff file boundary.** Crash — see "What the index proves" |
| 13:40:31.169 | 77,609 | `Stopped sequence acquisition` (tid10900, the *engine* thread) + `GetNumberNewImages error : 20024 first: 77609 last: 77609` | acquisition terminated, **4 min 46 s and 5,553 exposures after the last saved frame** |
| 13:40:31 → 14:08:11 | — | *(nothing)* | **27.7 further minutes of Microclaw hang**, with the camera idle |
| 14:08:11 → 14:08:45 | — | live mode, `Did unload all devices`, `Core session ended` | operator killed Micro-Manager |

The 13:17:42 thread dump is the load-bearing artifact. **44 minutes into the run
and 18 minutes before the crash, the Java side was completely healthy:**

```
Thread 669 [Acquisition Engine Thread] RUNNABLE
  at mmcorej.MMCoreJJNI.CMMCore_getPixelSizeUm__SWIG_0(Native Method)
  at org.micromanager.acqj.main.AcqEngMetadata.addImageMetadata(AcqEngMetadata.java:109)
  at org.micromanager.acqj.internal.Engine.acquireImages(Engine.java:631)
Thread 1048 [Acquisition image processing and saving thread] WAITING  (idle, queue empty)
Thread 1044 [Multipage Tiff data writing executor] WAITING  (idle)
```

Nothing was aborted, degraded, or backed up. The Andor adapter's own counter at
the stop — `first: 77609 last: 77609` — independently confirms the rate:
77,609 exposures in 4,016.4 s is **51.75 ms/frame**, matching the five 500-frame
segments to 0.1%. Every number below is anchored on those two counters.


## Findings

### F1 — an abort inside a hardware-sequenced burst costs minutes and thousands of exposures

pycro-manager's notification thread does exactly one thing with the exception
(`java_backend_acquisitions.py:226-229`):

```python
except Exception as e:
    traceback.print_exc()
    acquisition.abort(e)
    continue        # perform an orderly shutdown
```

`Acquisition.abort()` clears the Python event queue and calls `self._acq.abort()`
over the bridge. But `interval_s=0` means all 100,000 events were merged into a
single `startSequenceAcquisition(100000, 0)`, and the engine sits inside
`Engine.acquireImages` for the whole burst. This is `CLAUDE.md`'s own contract
arriving from the other side: **the property that makes a per-frame hook
impossible means a prompt abort cannot be relied upon either.**

Measured cost: the last frame reached disk at 13:35:45; the sequence stopped at
13:40:31.169, with the Andor adapter reporting 77,609 exposures against 72,056
saved. **5,553 exposures — 4 min 46 s — bought nothing.** Whether that is the
Python `abort()` finally landing or the Java storage's own failure propagating,
the Core log does not say; either way the latency is real and is not
instantaneous, which is what the design has to assume.

This is therefore a dose finding as much as a control-flow one. The 638 was at
30% and this rig's camera gates the lasers. Neither the Stop button (cooperative,
tool-boundary only — `webserve.py:959`), nor the illumination reservation, nor
the engine's own exception could shorten those 5,553 exposures by one frame.


### F2 — Microclaw waits on pycro-manager's teardown with no bound at all

`_acquire_with_hooks` (`microclaw/tools.py:3295-3312`):

```python
with Acquisition(directory=save_dir, name=name, show_display=True, **hook_fn_kwargs) as acq:
    ...
    acq.acquire(events)
```

`Acquisition.__exit__` is `mark_finished()` then `await_completion()`, and
`await_completion`'s `finally` block joins four threads unconditionally —
including `_acq_notification_recieving_thread`, the thread that just threw, and
`_storage_monitor_thread`. Both of those exit only on a `data_sink_finished`
notification. There is no timeout on any of it, and the `timeout=2500`
constructor argument is not one: it is the pyjavaz object-construction timeout
(`java_backend_acquisitions.py:291, 438-485`), nothing to do with completion.

**This is the defect Microclaw owns.** Everything above F2 is upstream or is
physics. F2 is a design decision in `tools.py`: a blocking call into third-party
code, on the one path that runs for an hour at a time, with no deadline.

### F3 — the failure was never recorded anywhere the session can see

`traceback.print_exc()` writes to the server's stdout. The exception object is
also stashed on `acq._exception` by `abort()` and would be re-raised by the next
`_check_for_exceptions()` — but F2 means that raise happens inside a `try` whose
`finally` never returns. Net effect: the history JSONL, the confirmation audit,
and the result dict all contain **nothing**. An operator reading the session
afterwards cannot tell this run from one that is merely slow.

### F4 — no progress signal, so "working" and "hung" look identical

Microclaw already attaches a per-saved-frame callback whenever there is a
reservation (`tools.py:3260 account_saved_frame` → `reservation.commit_frame()`),
and `reservation.completed_frames` is a live count. It is surfaced only in
`_reservation_report`, and only on an overrun. For an 83-minute run the UI shows
one spinner and one string.

### F5 — a session grant carries no magnitude

`SessionGrants` keys on `(kind, subject)` only — `("acquisition", "threshold")`
(`tools.py:1714-1718`). The operator approved-for-session a **500-frame,
26-second** acquisition at 10:29:05 UTC. At 10:33:31 UTC the same grant
auto-approved a **100,000-frame, 83-minute** acquisition, 200× larger. The grant
record even stores `granted_on: <summary>`; nothing compares against it.

The operator did consent in conversation ("3. confirm"), so nothing happened here
against their wishes. The finding is that the *safety* gate had stopped asking,
and the run it stopped asking about was the unstoppable one.

### F6 — the confirmation text does not disclose the thing that mattered

> This acquisition will take 100000 frames and about 83.3 minutes. It may use
> substantial disk space or time. Continue?

Disk and time were not the risk. The risk was that `interval_s=0` at this frame
count buys a single burst that **Microclaw cannot be relied upon to interrupt
promptly** — neither its Stop button nor the engine exception stopped these next
5,553 exposures. Neither
`_authorize_acquisition` nor `run_timelapse`'s parameter descriptions say so.
`interval_s`'s description mentions sequencing only as a constraint on
`hook_action_plan`.

### F7 — the data survived, and Microclaw did not say so

`NDTiffDataset.add_index_entry` maintains the **in-memory** index of the Python
reader. For a Java-backend acquisition the on-disk `NDTiff.index` and the TIFF
stack are written by `NDTiffStorage` on the Java side, independently. Confirmed
on the rig: `Dataset(...)` opens the crashed dataset and reports **72,056
frames** — every frame that reached disk before the rollover, readable, at
`F:\DataSSD\260828_AA_U2OS_CLC-SNAP_AF647\260828_AA_U2OS_CLC-SNAP_AF647_dSTORM_30pct_100k_1`.

Microclaw knew that path from the first event (`_acq_dataset_path`, read before
`acquire()` dispatches anything) and reported neither it nor the fact that the
data was intact. The operator was left with a dead session, a live camera, and no
statement about 72,056 frames of real dSTORM data sitting on F:.

### F8 — the 4 GiB crossing was predictable before the first exposure

Microclaw knew `n_frames=100000`, and already read the ROI and pixel depth —
150×150×16-bit here, ~59.5 KB per saved frame with metadata. That is
**5.96 GB against NDTiff's 4 GiB per-file cap**: this run was always going to
cross a file boundary at ~frame 72,000, and no run in the session's history ever
had. The existing planner computed only 4.5 GB of raw pixels: enough to know a
4 GiB crossing would occur, but not enough to estimate its real frame boundary,
and no disclosure used even that estimate. This is not a fix for the upstream
rollover bug — it is the difference between an unexplained 90-minute hang and an
informed choice, and it makes D6's segmenting advice a number rather than a
suggestion.


## What the index proves

The rig returned the dataset, and it settles the cause. **This is measured, not
hypothesised.**

```
dir *dSTORM_30pct_100k*\*
  260828_..._100k_NDTiffStack.tif    4294967296     <- exactly 4 GiB, ONE file
  NDTiff.index                         26214400     <- exactly 25 MiB
Dataset(...)  ->  frames: 72056
  UserWarning: Index appears to not have been properly terminated
```

Both files are at their pre-allocated sizes and were never truncated, because the
storage never closed. Parsing all 26,214,400 bytes of `NDTiff.index` locally:

* **72,056 entries**, then a zero `axes_length` at byte 8,419,442 (32.12%), then
  an all-zero remainder. So the "unterminated index" warning is not corruption —
  it is simply the end of what was written into a pre-allocated file.
* every entry names the **same** stack file. **No second `NDTiffStack_1.tif` was
  ever created.**
* the last entry, `{"time": 72055}`, has `pix_offset` 4,289,893,464, `md_offset`
  4,289,938,464, `md_length` 14,361 — a data high-water mark of **4,289,952,825
  bytes, leaving 5,014,471 bytes of the 4 GiB file.**

Now apply the writer's own admission test (`ndstorage/ndtiff_file.py:82-90`,
the Python port of the Java writer):

```python
extra_padding = 5000000                      # 5 MB
size = md_length + IFD_size + bytes_per_pixels + extra_padding + self.file.tell()
if size >= MAX_FILE_SIZE:  return False      # -> roll to a new file
```

For frame 72,056: `14,361 + 176 + 45,000 + 5,000,000 = 5,059,537` needed against
**5,014,471** available. **It misses by 45,066 bytes — less than one frame.**

> **The last frame that fit is the last frame that exists.** Frame 72,056 was the
> first to trigger `newFile()`, and no second file was created. The rollover and
> malformed image-saved notification therefore coincide at exactly the first
> frame that did not fit. That is strong evidence that rollover failed and caused
> the malformed notification, but the index alone does not prove the notification
> mechanism; that last causal step still needs an upstream reproduction.

It also explains cleanly why the five phase-1 segments were fine: at 59.5 KB per
frame, a 500-frame segment is 29.8 MB — 0.7% of the cap. **This burst is the only
one in the session that ever reached a file boundary.**

What remains genuinely unknown, and does not need to be known to fix anything
here, is *why* the rollover emits a truncated payload. It is upstream, in
NDTiffStorage/AcqEngJ, not in Microclaw. A plausible mechanism — unverified — is
that the notification's payload is derived from an index-entry `ByteBuffer`
belonging to a writer that is being swapped out. **Do not write a fix against
that guess**, and note that nothing in D1–D7 depends on it: they contain the
blast radius of *any* fatal engine-side error, which is the correct scope for
code that supervises somebody else's acquisition engine.


## Decisions

### D1 — Microclaw bounds its own wait, with a short error grace and a long runtime ceiling. (fixes F2, F3)

Run pycro-manager's teardown on a daemon thread and join it with a deadline.
This is the only shape available: `await_completion` is one blocking call with no
timeout parameter, and it cannot be interrupted from outside.

```python
# in _acquire_with_hooks, replacing the bare `with Acquisition(...)`
acq = Acquisition(directory=save_dir, name=name, show_display=True, **hook_fn_kwargs)
dataset_path = _acq_dataset_path(acq, save_dir, name)
...
acq.acquire(events)

outcome: dict = {}
def _finish():
    try:
        acq.__exit__(None, None, None)      # mark_finished + await_completion
    except BaseException as exc:            # pycro-manager re-raises _exception here
        outcome["exc"] = exc
    else:
        outcome["done"] = True
    finally:
        finish_owned_cleanup()              # restore hardware, then close reservation

waiter = threading.Thread(target=_finish, name="microclaw-acq-teardown", daemon=True)
waiter.start()

# Poll while the waiter owns teardown. An engine exception starts a short,
# separate grace period; an otherwise healthy run retains a generous ceiling.
runtime_deadline = started + _runtime_ceiling_s(plan)
error_deadline = None
while waiter.is_alive():
    engine_exc = getattr(acq, "_exception", None)
    if engine_exc is not None and error_deadline is None:
        report_engine_exception(engine_exc)
        error_deadline = time.monotonic() + ERROR_TEARDOWN_GRACE_S
    deadline = min(runtime_deadline, error_deadline or runtime_deadline)
    waiter.join(min(1.0, max(0.0, deadline - time.monotonic())))
    if waiter.is_alive() and time.monotonic() >= deadline:
        raise AcquisitionUnterminated(...)  # converted to D3 at the shared boundary
if "exc" in outcome:
    raise outcome["exc"]                    # normal prompt failure path
```

There are deliberately two bounds. Before an engine error, a runtime ceiling
derived from `plan.estimated_duration_s` allows normal readout and teardown
variance; `max(estimated_duration_s * 1.5, estimated_duration_s + 300)` is a
reasonable initial policy, explicitly understood as up to half a planned
duration extra for long runs. Once `acq._exception` appears, a fixed, separately
configured grace period replaces that ceiling.

**Set that grace short — 90 s initially — and do not tune it to the abort latency
measured here.** The tempting choice is five minutes, because 4 min 46 s elapsed
between the exception and the camera stopping, and a grace just past that makes
the report land after the hardware is quiet. That is fitting a constant to one
observation of a mechanism we do not understand (F1 could not say whether that
latency was the abort landing or the Java storage failing), and it buys a tidier
report with five minutes of silence. It is also unnecessary: D3's session flag
exists precisely so that returning while the camera is still live is safe.
Reporting at 13:37:15 with `camera_sequence_running: true` is the *correct*
answer, not a degraded one — the operator learns the run is dead while it still
has three minutes to run, and nothing can collide with the camera meanwhile.

So this run would have returned at about **13:37:15** — not after the 125-minute
healthy-run ceiling, and 31 minutes before the process was actually killed.

The waiter is deliberately left running after the foreground returns. It retains
ownership of everything callbacks can still touch: the acquisition, hook,
reservation, and hardware-restoration state. The foreground path must not close
the reservation or restore hook-controlled hardware while teardown is live.
When the waiter eventually finishes, it closes the reservation, performs
restoration, and records any late teardown or restoration error to the diagnostic
event sink. This avoids turning later saved-frame callbacks into false overruns
or racing restoration against live callbacks.

**Do not make the deadline a frame-arrival watchdog on its own.** The tempting
version — "no saved frame for N×period ⇒ stalled" — would not reliably have
caught this run: pycro-manager `continue`s past the bad notification, so later
good notifications keep flowing and the counter keeps advancing. Frame arrival is
a *progress signal* (D4), not the stall detector.

### D2 — Poll `acq._exception` and record it the moment it appears. (fixes F3)

`abort(e)` sets `acq._exception` synchronously. In this run it became observable
about five minutes before the camera sequence stopped, and more than 32 minutes
before the process was killed. The one-second supervisor poll in D1 turns that
into a session-visible diagnostic event immediately and carries it into the
result. This reads a private attribute of pycro-manager
1.0.2, exactly as `_dataset_disk_location` already does at `tools.py:3299` —
guard it with `getattr(acq, "_exception", None)` and re-verify on any
pycro-manager upgrade, with the same comment.

Do not append an ad-hoc row directly to conversation history: a tool call without
its matching tool result is deliberately kept as one atomic history operation.
Add a small thread-safe acquisition-event sink to the tool execution context.
The browser implementation forwards progress and diagnostics through the active
turn's SSE emitter; the CLI implementation writes timestamped diagnostics to
stderr. A hook log may receive the same structured event when one exists, but it
is not the sole record because plain acquisitions have no hook log.

### D3 — On expiry, return a result that keeps the session usable. (the "recover" half)

`_acquire_with_hooks` raises the typed `AcquisitionUnterminated` internally.
One shared acquisition-entry boundary catches it and converts it to a tool result
for every supervised acquisition entry point; individual tools must not each
grow slightly different catches. The tool therefore returns rather than raising
into the agent loop, with a shape that says exactly what is and is not known.
Filled in with this run's real numbers:

```json
{"error": "The acquisition engine reported a fatal error and pycro-manager's
           teardown did not complete within <N> s. Microclaw stopped waiting.",
 "acquisition": "unterminated",
 "engine_exception": "struct.error: unpack requires a buffer of 4 bytes",
 "dataset_path": "F:\\DataSSD\\...\\..._dSTORM_30pct_100k_1",
 "frames_planned": 100000,
 "frames_accounted": 72056,
 "camera_sequence_running": true,
 "teardown_running": true,
 "hardware": "The camera sequence was still running when Microclaw stopped
              waiting, and a hardware-sequenced burst cannot be aborted
              promptly. Acquisition tools are refused until both the camera is
              idle and the background teardown has finished.",
 "next": ["The dataset on disk is written by Micro-Manager, not by this process,
           and is independent of this failure - read it with
           analyze_completed_dataset once the camera goes idle.",
          "Do not start another acquisition until camera_sequence_running and
           teardown_running are both false."]}
```

Every field is available at the moment of expiry: the path from
`_acq_dataset_path`, the count from `reservation.completed_frames`, the exception
from D2, and waiter liveness from `waiter.is_alive()`. With D1's 90-second error
grace, the operator would have had all of it at about **13:37:15** instead of
nothing at 14:08 — 3 min 16 s before the camera even stopped.

`camera_sequence_running` is a real read (`core.is_sequence_running()`), not an
inference — the bridge is free between calls, so the probe answers even while the
burst runs. In this incident it would have read **true** at the 13:37:15 expiry
and gone false at 13:40:31, so both branches are live and both must be written.
Set a session flag for the unterminated acquisition. **Every acquisition entry
point refuses while either its camera sequence is running or its teardown waiter
is alive**, re-probing both conditions. Camera-idle alone is not sufficient: the
waiter still owns late hook restoration, which could otherwise move a stage or
write a property during a new acquisition. Clear the flag only after the waiter
has finished its owned restoration and reservation cleanup. If it never
finishes, read-only work and dataset analysis remain available, but another
acquisition remains refused unless a separately designed, explicit recovery
operation permanently abandons late restoration and assumes ownership of the
remaining hardware state.

The `hardware` and `next` text above is selected from the measured camera and
waiter state, never asserted — a report that claims the camera is still live
after it has gone idle is the same class of defect as this document's subject.
That is the recovery: the turn ends and the agent can talk, read datasets, and
read properties without allowing an old teardown to collide with a new run. No
process restart, no `microclaw session` state a second launch could trip over.


### D4 — Surface `reservation.completed_frames` as progress. (fixes F4)

The counter already exists and is already updated per saved frame. Publish
rate-limited progress events through D2's acquisition-event sink (not by coupling
`tools.py` directly to `Session`) so the browser's existing SSE stream can show
`frames 41,203 / 100,000`; the CLI can render the same event periodically. An
83-minute run with no observable progress is a usability defect on its own, and
it is what made this failure invisible for 95 minutes.

### D5 — Disclose un-interruptibility at the gate, and bound the grant. (fixes F5, F6)

Two small, contained changes:

* `_authorize_acquisition` adds a clause when the plan is a single sequenced
  burst (`interval_s == 0` and `frames > 1`): *"This is one hardware-sequenced
  burst of 100000 frames (about 83.3 minutes by the current estimate). Once
  started, Microclaw's Stop button and engine abort cannot be relied on to stop
  it promptly; thousands of further exposures may occur."* Same for the parameter
  description on `interval_s` and `n_frames`, per the standing rule that a
  statically-knowable constraint belongs in the parameter, not the prose.
* `SessionGrants` records the granted plan's `(frames, duration_s,
  illuminated_ms)` and `_authorize_acquisition` re-asks when a later plan exceeds
  any of them. The key stays `(kind, subject)`, but this requires a structured
  confirmation context rather than comparison against the human-readable
  summary: extend `CONFIRM_FN`/`Session.confirm` with optional grant metadata, and
  make both the CLI and browser grant lookup compare that metadata before
  auto-approving. Existing non-acquisition confirmations pass no metadata and
  retain their current behavior. A grant should remove *repeated* decisions,
  which is what its own docstring claims — not *larger* ones.

### D6 — Segmenting is documentation, not a limit. (fixes F8)

`plan_events` already returns `estimated_bytes`, computed as raw
`frames × width × height × bytes_per_pixel`, and `guard.check_acquisition` already
consumes it. **The disclosure needs no new estimation machinery at all.**

`MAX_FILE_SIZE // (width * height * bpp)` is an *upper bound* on frames per file —
95,443 here — because metadata, IFDs and the summary header only ever make a
frame cost more. So `frames > that bound` is a **guaranteed**-crossing test built
from the field that already exists, and `100,000 > 95,443` would have fired on
this run before the first exposure. Ship that first.

A conservative NDTiff overhead model (per-frame metadata plus IFD) is then a
*refinement*, not a prerequisite: it catches the 72,000–95,443 band, where a run
crosses a boundary that the raw bound alone calls safe. Keep raw image bytes
separately visible either way, and do not let the disclosure quote a frame number
it cannot stand behind — the 4.5 GB raw estimate correctly predicts a crossing
but puts it at frame 95,443, while the real one was 72,056, because per-frame
metadata here was 14,361 bytes: 24% on top of the pixels.

> This acquisition writes at least 4.5 GB of image data against NDTiff's 4 GiB
> per-file limit, so it will roll to a second file before frame 95,443 — and
> earlier once per-frame metadata is counted. Ten acquisitions of 10,000 frames
> keep every file whole.

The operator can have the same 100,000 frames as ten 10,000-frame acquisitions —
which is what they already did for phase 1 — capping each burst's
un-interruptible commitment at ~9 minutes and each file at 0.6 GB. Measured cost
from the five phase-1 segments: **~6 s of start/stop overhead per segment, 60 s
across the run.** That is a real trade with a real cost and it is the
microscopist's to make: state it in the disclosure and in the tool description,
and do **not** add a cap. Rejecting a 100,000-frame dSTORM stack would be
rejecting the experiment.


### D7 — The emitters do not change.

`_emit_acquisition` and friends emit a bare `with Acquisition(...)`, which
inherits exactly this unbounded wait. Leave them. The bounded wait is a property
of Microclaw *supervising* a run on the operator's behalf; a standalone script's
supervisor is the process and terminal it was launched from, where the traceback
is already visible and process-level termination remains available. Ordinary
Ctrl-C is not claimed to unwind pycro-manager successfully: it can enter the same
blocking `__exit__`. Inlining a watchdog would put Microclaw-shaped machinery
into a file whose whole point is not needing Microclaw. Recorded here so it is a
decision and not an oversight.

## Evidence collected, and what is still owed

The three numbers this document originally asked for came back on 2026-08-28 and
are folded in above:

1. **Frame count** — 72,056, not ~77,500 and not 100,000. It is the 4 GiB
   boundary, not an abort point and not completion.
2. **Stack files** — one. The rollover never produced a second file; it is the
   operation that failed.
3. **`NDTiff.index`** — 26,214,400 bytes pre-allocated, 8,419,442 written,
   72,056 entries, zero-filled remainder. Parsed entry by entry; no corrupted
   entry on disk. The corruption was only ever in the notification.

Nothing further is owed from a rig. **Everything left is off-rig work**, which is
the point: this failure needed one thread dump, one index file, and arithmetic.

D1's test must reproduce the hang, not assert around it: a fake `Acquisition`
whose `__exit__` blocks forever and whose `_exception` becomes set, driven through
the shared acquisition entry boundary, asserting the tool returns the D3 dict
within the error grace. It must also assert that the still-live waiter retains
the reservation and does not restore hardware. A fake that returns promptly
tests nothing — this defect is *entirely* a fake-that-encodes-the-assumption
defect, which is why 2,216 green tests never came near it.

D6 has a second test that costs nothing and would have caught F8: assert that a
plan whose estimated bytes exceed `MAX_FILE_SIZE` produces the boundary
disclosure. And the rollover itself is now reproducible off-rig at any time —
write 4 GiB through a `SingleNDTiffWriter` and watch `newFile()` — which is where
an upstream report to pycro-manager should start if we choose to file one.
