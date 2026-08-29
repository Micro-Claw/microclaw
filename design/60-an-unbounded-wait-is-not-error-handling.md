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


## What the demo machine measured, 2026-08-28

Block 60a's gate, evidence in `block60a-demo-evidence/`. Recorded here because
three decisions above were written without these numbers.

* **D3's camera probe is answerable mid-burst — the one thing that needed
  hardware.** During a 600-frame `interval_s=0` burst, **62 of 62** probes of
  `core.is_sequence_running()` returned True, median latency **0.0 ms**, max 62.
  The two False readings bracket the burst exactly (+0.000 s and +12.969 s), so
  the reading tracks the burst rather than being stuck. pyjavaz's per-round-trip
  lock does **not** hold the bridge while the engine is inside a burst, and D3's
  `camera_sequence_running` is therefore obtainable at expiry, as written.
* **Teardown costs at most 203 ms on a healthy 600-frame run.** Derived from the
  probe timestamps, not from the gate's own limb, which reports a looser 0.97 s
  upper bound because it subtracts only exposures from wall time. Last True
  +12.766 s, return +12.969 s.
  **This does not shrink `ERROR_TEARDOWN_GRACE_S`.** The grace bounds a *broken*
  teardown, and the incident measured 4 min 46 s of abort latency after the
  engine's exception; D1 already refused to fit that constant to one observation
  and a healthy-path measurement is not the observation it refused.
* **The runtime ceiling had 24x headroom** on the shape it estimates well: wall
  12.97 s against a 312 s ceiling, 4.2% of it, on a plan estimating 12.0 s.
  Nothing here tests D1a's position-dominated shape, which has no hardware
  evidence and rests on the suite's two simulated-clock tests.
* **`_acq_dataset_path` read a suffixed path on hardware.** The dataset landed at
  `..._1`, not the unsuffixed name, corroborating design/38 F7's concern on a
  real acquisition rather than in a fixture.
* **Not established, and not claimed:** that the waiter *thread* is the mechanism
  on hardware. Nothing in the evidence distinguishes threaded teardown from
  inline; the gate establishes no-regression. The mechanism's proof is the
  suite's blocking fake, where the tool returns while `__exit__` is still
  blocked.

One input for 60b, from the same run: a healthy completion's result dict is
`{"dataset_path": ..., "status": "Timelapse complete."}` and states **no frame
count at all** — `_reservation_report` is empty on an exact run by design. F4 is
about progress *during* a run; the final report is silent too, and D4 should
carry `frames_accounted` into it while it is there.

## What the demo machine measured for 60b, 2026-08-29

Round 1 of 60b's gate. **The product passed every limb it reached; both failures
were defects in the gate itself**, and the second one is the more instructive.

**The 4 GiB crossing is clean on this camera** — the one question no fake could
answer. 512x512x16-bit, 8,256 frames, 10 ms:

| | measured |
| --- | --- |
| frames in the index | **8,256 of 8,256** — nothing lost across the roll |
| `..._NDTiffStack.tif` | 8,114 frames, 4,289,888,652 B (5,078,644 B under 2^32) |
| `..._NDTiffStack_1.tif` | 142 frames, 75,079,752 B |
| per-frame overhead | 4,415 B, **0.84%** on top of pixels |
| D6 disclosed | "roll to a second file before frame 8,192" |
| actual roll | after frame **8,114** — the bound HELD, margin 78 frames |

So the demo camera's storage does what M2's did not: it rolled, kept every
frame, and stayed under the limit. Weigh that when deciding the upstream report
— the truncated notification did not reproduce here.

**D6's raw upper bound is the right thing to ship, and a fixed overhead model is
not.** Per-frame overhead was 14,361 B on M2 (24% of its pixels) and 4,415 B here
(0.84%). The absolute cost differs 3.3x and the *fraction* differs 29x, because
the fraction is dominated by ROI size. Item 5's refinement would have to be
rig-calibrated to beat the bound it refines; it stays optional and unbuilt.

**D4 works on hardware**: 131 progress events over 129.5 s = **1.01/s**, first
frame 1, last 8,256 of 8,256. The 83-minute silence of the incident is gone.

Two other numbers worth keeping. `plan_events` estimated 82.56 s and the burst
took **130.72 s (1.58x)** — against 5,000 s planned / 5,175 s real on M2. The
demo camera is a simulator and is not truly hardware-sequencing, so this does not
contradict D1a's reasoning, but it is a second data point that the estimate is a
statement about exposure, not about wall time. And the run's `dataset_path`
carried AcqEngJ's `_1` rename, as design/21 F6 says it does.

### The two gate defects, because one of them is this document's own lesson

**1. The gate required a `workspace_dir`.** It defaulted `--save-root` from the
safety config and refused when there was none — but `workspace_dir` is
**optional** in the product (`resolve_output_path`: None means writes are
unconfined), so the gate invented a precondition microclaw does not have. Six
limbs came back NOT EXERCISED for a reason that says nothing about the code, and
the operator had to edit a production safety config to run the gate at all. A
gate must not require configuration the product does not require.

**2. The gate globbed `NDTiffStack*.tif`, and NDTiff writes
`<name>_NDTiffStack*.tif`.** It matched nothing, so the one limb the rig trip
existed for reported FAIL on a crossing that had in fact been perfect. The
correct pattern was already written down in `controller.py:513`.

The second is *this document's own rule*, reproduced by the coordinator who wrote
it: **the selftest's fake wrote the filename the glob expected**, so the selftest
could not catch it — a fake that encodes your assumption is not a test of it. The
fake now writes the names ndstorage really writes, and with the old glob restored
it fails exactly as the demo machine did. The selftest also now runs **both**
safety-config shapes, with and without `workspace_dir`, because no fixture
produced the shape the operator actually had.

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

### D1a — the runtime ceiling is qualified by frame arrival, and no path is left unbounded. (coordinator amendment to D1, 2026-08-28)

Two gaps in D1 as written, both found by reading the call sites rather than the
incident. Recorded here so the implementer builds the amended shape, not D1's.

**The runtime ceiling as stated has a false-positive hazard that the incident
could not show.** `plan.estimated_duration_s` deliberately counts exposure and
`min_start_time` only — `plan_events` says so: "camera readout, stage settling,
autofocus, and filter switching are rig-dependent and unmeasured". For the M2
burst that estimate was excellent (5,000 s planned against 5,175 s real), because
a hardware-sequenced burst *is* nothing but exposures. It is bad exactly where
runs are position-dominated: a 1,000-position tile scan at 10 ms plans 10 s, so
`max(1.5 x, x + 300)` gives a 310 s ceiling against a healthy run of half an
hour. D1 would then declare a working acquisition unterminated, return the D3
dict, and set the session flag that refuses further acquisitions — mid-run, on
the rig, for no defect.

So: **expiry of the runtime ceiling additionally requires that no frame has been
saved for `STALL_QUIET_S`.** This does not contradict D1's "do not make the
deadline a frame-arrival watchdog on its own", which is about *detection* — frame
arrival cannot detect this incident, because pycro-manager `continue`s past the
bad notification and later good notifications keep the counter moving. Here it is
used only to *withhold* a false positive from a run that is demonstrably still
producing data. In the M2 incident it changes nothing: `_exception` appears at
~13:35:45 and the 90 s error grace fires first, at ~13:37:15, exactly as D1 says.
The error grace is **not** so qualified — once the engine has reported a fatal
error, continued frame arrival is not evidence of health.

**The quiet window is self-calibrating, and the ceiling is only a
precondition** (operator challenge, 2026-08-28: *"we've done several overnight
acquisitions on the Nikon systems that worked"*). Those runs worked because
nothing bounded them — that is F2, not evidence against it — but they are the
right thing to protect, and a fixed quiet constant does not protect them.
Measured against the two shapes:

* An **interval-driven** overnight timelapse is estimated *well*. `interval_s`
  reaches pycro-manager's `time_interval_s`, which writes `min_start_time` on
  every event, and `plan_events` reads it. A 16-hour run plans at ~16 hours and
  D1's ceiling lands at ~25. Never at risk.
* A **position-dominated** run is estimated badly, and this is the whole
  hazard. 500 positions x 11 slices at 100 ms plans 550 s, so the ceiling is
  ~14 minutes against a real hour once stage settling and per-tile autofocus
  are counted — none of which `plan_events` estimates, as its own comment says.
  A fixed quiet window fails here too: a hook running a focus search
  legitimately leaves minutes between frames.

So the trigger is **quiet time, self-calibrated from the run's own frames**:

    STALL_QUIET_S = max(STALL_QUIET_FLOOR_S, 5 * largest observed inter-frame gap)

with `STALL_QUIET_FLOOR_S = 900`. Expiry of the non-error path requires the
runtime ceiling to have passed **and** that window to have elapsed with no saved
frame. The ceiling stops being the trigger and becomes a precondition; erring
long on it costs nothing now, because the error grace already covers the failure
we actually measured.

Two consequences worth stating. **The floor must cover legitimate end-of-run
teardown**, because after the last frame a healthy run is also quiet and its
camera is also idle — time is the only separator, which is why the floor is
15 minutes and not 2. And **camera state is reported, never used as the
predicate**: `is_sequence_running()` is false between frames in any
software-triggered acquisition, so inferring "dead" from it would be the same
class of error as inferring arrival from a busy flag.

**And the frame counter must exist on every path.** `account_saved_frame` is
installed only when `reservation is not None`, and two call sites pass `None`
(the survey runner with `adaptive=false`, and the deferred acquire phase). Count
saved frames unconditionally — `image_saved_fn` receives no pixel array, so this
keeps the Java-side streaming fast path either way — so that `frames_accounted`
in the D3 report and the qualification above are available on every supervised
run, not only budgeted ones.

**No reservation must not mean no bound.** `_acquire_with_hooks` reads its plan
from `reservation.plan` today; on the reservation-less paths there is nothing to
read. Pass the plan explicitly from the call sites that already compute one, and
where none exists fall back to a named constant ceiling and **say in the result
that the ceiling was a fallback**. An unbounded wait is the defect this document
is about; it must not survive on the paths nobody was looking at.

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


### D3a — a composite acquisition must report what already completed. (coordinator amendment, 2026-08-28; F7 applied to D3)

D3's dict was written for one acquisition and names one `dataset_path`. Four of
the six supervised entry points are composites — `run_multiposition_acquisition`,
`run_tile_acquisition`, `run_multiposition_with_autofocus` and
`run_adaptive_survey`'s acquire-on-hit phase — and they run the longest
unattended. When one expires at position 3 of 20, positions 1 and 2 have finished
datasets on disk, and D3 as written reports only the third.

That is **F7 happening again inside the fix for F2**: *the data survived, and
Microclaw did not say so.* The operator would be told a run is unterminated and
left to guess that two-thirds of nothing, or two of twenty positions, is what
they have.

So `AcquisitionUnterminated` carries an optional partial payload, and a composite
attaches what it had already collected before the exception leaves it: the
per-position results it holds anyway, including each completed `dataset_path`.
`_unterminated_result` renders it when present and omits it otherwise — an empty
`positions_completed` on a single-acquisition tool would be a claim, not a
silence. The `next` text then names those datasets as readable, since they are
finished and independent of the failed one.

Keep it to what is already in hand. Do not go looking on disk for datasets the
call did not record; a report that guesses at paths is design/38 F7's original
defect.

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

## Blocks

design/60 owns its own blocks, checklist and run ledger, as design/58 and
design/59 do. It is not a design/35 row. Two blocks, in order — 60b's progress
reporting rides the diagnostic event sink 60a builds, so they are sequential.

### 60a — Microclaw bounds its own wait and returns a usable session

D1 (as amended by D1a), D2, D3, D7. This is the defect Microclaw owns; nothing
else in this document matters if a run can still hang forever.

### 60b — the operator can see the run, and is told what it costs

D4, D5, D6. Progress, the un-interruptibility disclosure, the magnitude-bounded
grant, and the NDTiff file-boundary disclosure. All of it is decided *before* the
first exposure or rendered *during* the run; none of it changes the acquisition.


## Implementation checklist

### Block 60a — the bounded wait, the exception the session can see, and the recovery

Items:

1. **A typed `AcquisitionUnterminated`**, beside `_HookedAcquisitionFailure` in
   `tools.py` (it is internal, like that one, and is converted at the boundary —
   move it to `errors.py` only if an import cycle forces it). It carries every
   field D3's dict needs: `dataset_path`, `frames_planned`, `frames_accounted`,
   `engine_exception`, `camera_sequence_running`, `teardown_running`, and which
   bound expired.
2. **`_acquire_with_hooks` runs `acq.__exit__` on a daemon thread**
   (`microclaw-acq-teardown`) and polls it at 1 s, per D1's stub. The
   construction, `_acq_dataset_path` read, hook binding and `acq.acquire(events)`
   stay in the foreground: an error before the waiter starts must still restore
   hardware in the foreground exactly as today.
3. **Two bounds, module constants with no config path** (the precedent is
   `STAGE_MOVE_TOLERANCE_UM`): `ERROR_TEARDOWN_GRACE_S = 90` and a runtime
   ceiling `max(estimated_duration_s * 1.5, estimated_duration_s + 300)`. Do not
   tune the grace to F1's 4 min 46 s — D1 says why, and D3's session flag is what
   makes returning while the camera is live safe.
4. **The runtime ceiling is qualified by frame arrival; the error grace is not**
   (D1a). Expiry of the runtime ceiling requires *both* that the deadline has
   passed *and* that no frame has been saved for `STALL_QUIET_S`.
5. **Count saved frames on every path** (D1a): install the `image_saved_fn`
   counter whether or not there is a reservation, wrapping `commit_frame()` when
   there is. It receives no pixel array, so the Java-side streaming fast path is
   unaffected.
6. **Every path is bounded** (D1a): `_acquire_with_hooks` takes the plan
   explicitly from the call sites that already compute one; where none exists it
   falls back to a named constant ceiling and the result says the ceiling was a
   fallback. Audit all five call sites (`tools.py` ~3531, ~3722, ~6482, ~6902,
   ~7161) — two of them pass `reservation=None`.
7. **Poll `acq._exception` and record it the moment it appears** (D2), guarded
   with `getattr(acq, "_exception", None)` and carrying the same
   re-verify-on-upgrade comment `_dataset_disk_location` already carries.
8. **A thread-safe acquisition-event sink on the tool execution context** (D2).
   Diagnostic events only in this block; 60b adds progress. The browser
   implementation forwards through the active turn's SSE emitter
   (`webserve.py` `_emit`, set per turn, `None` between turns — a sink that
   raises when no turn is bound is a defect, it must drop or buffer); the CLI
   implementation writes timestamped lines to stderr. Do **not** append a row to
   conversation history: a tool call without its tool result is deliberately one
   atomic history operation.
9. **The waiter owns cleanup** (D1): reservation close and hook hardware
   restoration move into `finish_owned_cleanup()` on the waiter thread. The
   foreground must not close the reservation or restore while the waiter is
   live. On the ordinary path the waiter has finished before the foreground
   reads `outcome`, so today's semantics are preserved — including folding
   restoration failures into the raised error, and `_HookedAcquisitionFailure`'s
   `frames_exposed` / `last_hardware_state`.
10. **One shared conversion at the boundary** (D3): `execute_tool` catches
    `AcquisitionUnterminated` and returns D3's dict. Individual tools must not
    each grow a slightly different catch.
11. **The session flag refuses further acquisitions** (D3) while *either* the
    camera sequence is running *or* a teardown waiter is alive, re-probing both
    each time. `camera_sequence_running` is a real `core.is_sequence_running()`
    read, never an inference, and the `hardware`/`next` text is selected from
    what was measured — a report claiming a live camera after it went idle is
    the same class of defect as this document's subject. Key the refusal on
    `_microclaw_acquisition_entry_point`, which is the complete set and includes
    `run_mda`; do not reuse `execute_tool`'s `run_mda` exemption from
    `authorize_path`. Read-only work and dataset analysis stay available.
12. **D7: the emitters do not change.** No watchdog is inlined into an exported
    script. Carry a regression test that a hookless timelapse still emits a bare
    `with Acquisition(...)`.

Tests — the fake is the whole problem here, so build it first. Every existing
`FakeAcquisition` in the suite returns promptly from `__exit__`, which is
precisely why 2,216 green tests never came near this defect.

- **The reproduction**: a fake `Acquisition` whose `__exit__` blocks forever and
  whose `_exception` becomes set, driven through the shared boundary. Assert the
  tool *returns* D3's dict within the error grace, with every field populated
  from measured state.
- The still-live waiter **retains the reservation and has not restored
  hardware** at the moment the foreground returns.
- Blocks forever, no `_exception`, **frames still arriving** past the runtime
  deadline → does **not** expire (D1a's false-positive guard). Mutate
  `STALL_QUIET_S` rather than watching this fail; its subject is the
  qualification, not the bound.
- Blocks forever, no `_exception`, no frames → expires at the runtime ceiling.
- A reservation-less call site gets the fallback ceiling and **says so** in the
  result; assert no path can wait unbounded.
- `camera_sequence_running` true and false produce different `hardware` and
  `next` text, both written and both asserted.
- The refusal is **parameterized over every tool carrying
  `_microclaw_acquisition_entry_point`**, so a tool added later is covered
  without a new test; assert the refusal lifts when both conditions read false.
- The ordinary path is unchanged: a normal completion still returns the same
  result dict, and a mid-run hook failure still raises
  `_HookedAcquisitionFailure` with its restoration failures folded in.
- The sink drops cleanly when no turn is bound.
- D7's emitter regression test.

Gate — **the demo machine**, as a program
(`design/60-block60a-demo-gate.py`) that reports each limb independently, owns
its own log, and exits nonzero. Every limb is a computation or an acquisition the
program drives; none needs an operator judgement, so it is not a runbook of
pasted blocks (58a). Run it against the **bridge-shaped fake** in
`design/55-gate-probe-selftest.py` on **both** trees before pushing — Core
collections must be `size()`/`get(i)` vectors whose `__iter__` raises, or the
gate reproduces 59a's rig trip.

Limbs:

1. Inventory: the camera supports sequence acquisition; report ROI, bytes/pixel
   and the measured per-frame period. A machine that cannot hardware-sequence
   reports **NOT EXERCISED** for limbs 2–4, and that is never a pass.
2. A short hardware-sequenced burst (`interval_s=0`) through `run_timelapse`
   completes through the threaded teardown and reports its dataset path; frames
   on disk equal frames planned. This is the no-regression limb and it is the
   one that would catch a waiter that never joins.
3. **The load-bearing measurement**: while that burst is in flight, a second
   thread issues `core.is_sequence_running()` and the limb reports whether it
   answers and how long it took. D3's report field depends on this being true,
   and it is not obviously true — pyjavaz serialises every bridge call under one
   lock. If it blocks until the burst ends, D3's `camera_sequence_running` is
   not obtainable at expiry and that is a finding, not a failed limb.
4. A hooked run with a named-stage envelope: restoration happens after teardown,
   exactly once, and the reservation closes once.
5. Free-disk and cleanup: the program records the machine's safety config before
   it starts and checks it back afterwards, and removes its own datasets.

The stall itself is **not** gated on a machine — it is upstream, not reproducible
on demand, and the fake above reproduces it exactly. Say that in the runbook
rather than leaving a limb that cannot run.

### Block 60b — progress, disclosure, and the bounded grant

Items:

1. **D4 — publish `reservation.completed_frames` as rate-limited progress**
   through 60a's sink, so the browser's existing SSE stream can render
   `frames 41,203 / 100,000` and the CLI can print the same periodically. Do not
   couple `tools.py` to `Session`. Use 60a's unconditional counter so a
   reservation-less run reports progress too.
2. **D5 — disclose un-interruptibility at the gate.** `_authorize_acquisition`
   adds a clause when the plan is a single sequenced burst (`interval_s == 0`
   and `frames > 1`), naming that Microclaw's Stop button and the engine abort
   cannot be relied on to stop it promptly and that thousands of further
   exposures may occur. The same constraint goes in the **parameter**
   descriptions for `interval_s` and `n_frames`, per the standing rule that a
   statically-knowable refusal or constraint belongs in the parameter, not the
   tool's prose.
3. **D5 — bound the grant by magnitude.** `SessionGrants` records the granted
   plan's `(frames, duration_s, illuminated_ms)`; `_authorize_acquisition`
   re-asks when a later plan exceeds any of them. The key stays
   `(kind, subject)`. This needs structured grant metadata on
   `CONFIRM_FN`/`Session.confirm`, not a comparison against the human-readable
   `granted_on` summary — and both the CLI and the browser grant lookup must
   compare it. Existing non-acquisition confirmations pass no metadata and keep
   their current behaviour; `setup_tools.py:329` is one such caller.
4. **D6 — the guaranteed-crossing disclosure, from the field that already
   exists.** `plan.estimated_bytes` is raw pixels; `MAX_FILE_SIZE // (w*h*bpp)`
   is therefore an *upper bound* on frames per NDTiff file, so `frames > bound`
   is a guaranteed crossing. Ship that first, with D6's wording, including the
   segmenting advice and its measured ~6 s per-segment cost. Import
   `MAX_FILE_SIZE` from `ndstorage.ndtiff_file` with a documented 2**32 fallback;
   do not silently hard-code it. Keep raw image bytes separately visible, and do
   not quote a frame number the estimate cannot stand behind — the raw bound put
   this run's crossing at 95,443 and it was 72,056.
5. **D6 refinement, optional in this block**: a conservative per-frame metadata
   and IFD model catches the band between the true crossing and the raw bound.
   It is a refinement, not a prerequisite. **Do not add a cap** — rejecting a
   100,000-frame dSTORM stack would be rejecting the experiment.

Tests:

- A plan whose estimated bytes exceed `MAX_FILE_SIZE` produces the boundary
  disclosure; one just under does not. This costs nothing and would have caught
  F8.
- The burst clause appears for `interval_s == 0, frames > 1` and not for a
  nonzero interval or a single frame.
- A grant created on a 500-frame plan does **not** auto-approve a 100,000-frame
  plan, and does still auto-approve a smaller one — the incident, exactly.
  Assert on each of frames, duration and illuminated ms independently.
- A non-acquisition confirmation with no metadata behaves as it does today.
- Progress events are rate-limited, are emitted through the sink rather than a
  `Session` reference, and are emitted on a reservation-less run.

Gate — **the demo machine**, one program plus one short driven session.

Program (`design/60-block60b-demo-gate.py`, same rules as 60a's):

1. Compute this machine's real crossing bound from its ROI and bytes/pixel, and
   check free disk before anything runs. Report the numbers.
2. Assert the D6 disclosure fires for a plan just over that bound and not for
   one just under — **before the first exposure**.
3. **Actually cross 4 GiB.** Run a burst just past the bound and confirm a second
   `NDTiffStack_1.tif` exists and the run completes. On a 512x512x16-bit demo
   camera that is ~8,200 frames and ~5 GB. Either outcome is evidence: a clean
   rollover shows the demo camera's storage does what M2's did not, and a
   reproduction of the truncated notification is a much larger finding and goes
   upstream.
4. Progress events observed during that burst, with their rate measured.

Driven session (operator, ~5 minutes): approve-for-session on a small
acquisition, then ask for a much larger one and confirm it is **re-asked**;
read back the burst un-interruptibility clause. **Dry-run those two prompts
against a recorded payload before the runbook ships** — 59b lost three rig rounds
to prompt defects and none to product defects. Two prompts is a small sample and
the session is short, so weigh that against the operator's time: if the dry-run
costs more than the session, ask for the session.

### Post-merge design gate (step 10, both blocks)

- Reconcile design/60 to what was measured, in particular limb 3 of 60a's gate
  (whether `is_sequence_running()` answers mid-burst) and limb 3 of 60b's
  (whether the demo camera's rollover is clean). If either contradicts a
  decision above, amend the decision here rather than leaving the prose.
- Add the generic lessons to `CLAUDE.md` beside the existing "`acquire()` only
  submits" contract, which this document's F2 is the other half of.
- Record coordination notes in `design/prompts.md`; close both ledger rows.
- Decide, explicitly, whether to file the upstream pycro-manager/NDTiffStorage
  report. The rollover is reproducible off-rig by writing 4 GiB through a
  `SingleNDTiffWriter`; the notification mechanism is still unproven and no fix
  here depends on it.

## Run ledger

| block | branch | start | implementation | gate | merge |
| --- | --- | --- | --- | --- | --- |
| 60a | `design60/bounded-wait` | `dd5b0dd` (2026-08-28) | `e5958cb` (3 Codex rounds + 1 Claude round after Codex credit ran out mid-turn; 4 defects returned, all in a broad `except Exception` between a supervised acquisition and `execute_tool`; coordinator suite 2450/99/0) | **PASSED 8/8, round 1**, demo machine 2026-08-28. 62/62 probes read `is_sequence_running()` True mid-burst, median 0.0 ms; teardown after camera-idle <=203 ms derived from probe timestamps; ceiling used 4.2% | `daedc83` merged 2026-08-28, branch deleted; design gate below |
| 60b | `design60/progress-and-disclosure` | `4ed79c4` (2026-08-29) | `c8cba67` (2 Codex rounds; 3 findings returned, two of them coverage gaps the coordinator proved by mutation — the D5 trigger inverted left 508 tests green, the progress cadence at 1e9 left 180 green; coordinator suite 2466/99/0) | round 1 demo 2026-08-29: **6/8 PASS, 2 FAIL — both gate defects, no product defect**. Crossing clean (8,256/8,256 frames, roll at 8,114 vs disclosed 8,192); progress 131 events at 1.01/s. Gate fixed in `f1b184b`; part 2 (driven session) not yet run | — |
