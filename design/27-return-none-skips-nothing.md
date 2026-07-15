# design/27 — a hook's `return None` skips nothing; it fires a ghost exposure

`hook_docs.py` makes three return-`None` promises:

- `post_hardware_hook_fn` (`hook_docs.py:46`): *"Return None to skip image
  capture for this event entirely."*
- `pre_hardware_hook_fn` (`hook_docs.py:56`): *"Return None to skip this event
  entirely (hardware never moves)."*
- `image_process_fn` (`hook_docs.py:23`): *"Return None to discard this image
  and drop all remaining events for that position."*

The first two are false, and false in the worst direction: under the ZMQ
backend, a hook that returns `None` does not delete its event — it replaces it
with an **unlabeled event that still fires the camera** at whatever position
the stage happens to be, producing a frame with no position identity. The
third is half false: the discard is real, but **nothing drops any events** —
every remaining exposure for that position still fires; only the pixels are
thrown away afterward.

This was found on the rig (2026-07-15, demo config) while verifying design/24,
and it is design/24's failure class exactly: a live defect in what we tell the
agent it can do, silent, with the run completing normally around it. It is
also design/21 again — **sourced and still wrong**: the skip semantics are
real in AcqEngJ for native Java hooks; the bridge's marshalling breaks them,
and nothing on either side says so.

---

## The founding trace

Rig history `20260715_164106_microclaw_history.json` (OneDrive
`microclaw-json-histories`). The agent — reading the design/24-corrected docs,
which correctly steered it away from `event_queue.put(None)` — wrote
`SNRMatchStop`: log per-tile SNR over a 3×3 grid, and once ≥4 SNRs match,
`post_hardware_hook_fn` returns `None` for every remaining tile, promising
*"no exposure, no bleaching past the trigger."* The hook's gate worked; the
log shows `STOP_TRIGGERED` at tile 4 and the `None` branch taken for all five
remaining events. Then:

```
{'position': 'snrgrid_r1_c0', 'x_um': -256.0, 'y_um': 0.0, 'snr': 6.3298856369975125}
{'event': 'STOP_TRIGGERED', 'matched_labels': [...], ...}
{'position': None, 'snr': 6.3298856369975125}
{'position': None, 'snr': 6.3298856369975125}
{'position': None, 'snr': 6.3298856369975125}
{'position': None, 'snr': 6.3298856369975125}
{'position': None, 'snr': 6.3298856369975125}
```

Five "skipped" tiles produced **five more processed frames**, each with no
position, no stamped XY, and (demo camera) the identical SNR. Five skipped
captures should have produced zero frames. The static demo image explains the
identical SNR; it does not explain why the frames exist, nor why they are
unlabeled. The agent flagged the anomaly but attributed all of it to the demo
camera — the real mechanism is below, and on a real sample with autoshutter
those five ghost exposures are five doses of bleaching the hook had just
promised not to spend.

## Why `None` never reaches the engine

The chain, verified end to end (pycro-manager 1.0.2 / pyjavaz 1.2.8 on the
Python side, pycro-manager Java `main` read 2026-07-15 on the other — and the
rig trace above is the same chain observed live on MM 2.0.3):

1. **The hook thread passes the `None` along.** pycro-manager runs each remote
   hook as a Python thread speaking ZMQ
   (`java_backend_acquisitions.py:63`, `_run_acq_hook`): it receives the
   event, calls the hook, and sends back whatever the hook returned. A `None`
   return is sent as-is. (Spike B_2 drives the real `_run_acq_hook` over real
   sockets and reads the wire.)

2. **pyjavaz has no encoding for `None`.** `_DataSocket.send`
   (`pyjavaz/bridge.py:104`):

   ```python
   if message is None:
       message = {}
   ```

   The `None` leaves the process as an **empty JSON object**. (Spike B_1.)

3. **The Java side deserializes `{}` into a real event.**
   `RemoteAcqHook.run()` pulls a `List<AcquisitionEvent>`; its deserializer
   has two branches — an `"events"` key (a sequence), else *"treat the whole
   message as a single event"*: `AcquisitionEvent.fromJSON(t, acq)`. An empty
   JSON object takes the second branch. `fromJSON` reads every field
   conditionally (`if (json.has("axes")) ...`), so `{}` builds an event with
   **empty-but-non-null axes**, no XY, no Z, no exposure, no config. `run()`
   returns it — **non-null**.

4. **The engine's null check never trips.** AcqEngJ honors exactly the
   promised semantics for native hooks (`Engine.java`,
   `executeAcquisitionEvent`):

   ```java
   for (AcquisitionHook h : event.acquisition_.getAfterHardwareHooks()) {
      event = h.run(event);
      if (event == null) {
         return; //The hook cancelled this event
      }
      ...
   }
   ```

   That is where the docs' promise comes from, and for in-JVM hooks it is
   true. The remote hook can never return null — step 3 manufactured an event
   — so the cancel path is unreachable from Python.

5. **The ghost event acquires.** The decision is
   `AcquisitionEvent.shouldAcquireImage()`:

   ```java
   public boolean shouldAcquireImage() {
      if (sequence_ != null) {
         return true;
      } else {
         return configPreset_ != null || axisPositions_ != null;
      }
   }
   ```

   `axisPositions_` is an empty `HashMap`, which is non-null — **true**. The
   engine calls `acquireImages(event)`: no coordinates means no stage move, so
   the camera fires wherever the stage already is (autoshutter opening the
   shutter for it), and the frame comes back through `image_process_fn` with
   no position axes. One ghost exposure per "skipped" event — five on the rig,
   at tile 4's position.

The same chain applies verbatim to `pre_hardware_hook_fn` (same
`RemoteAcqHook` class, registered at `BEFORE_HARDWARE_HOOK`,
`java_backend_acquisitions.py:463`) — with one difference worth measuring
(spike B_4): the original event is replaced *before* the hardware stage, so
the promise's parenthetical "(hardware never moves)" is accidentally true
while the capture still fires.

**And there is no wire value that reaches the cancel path.** The hook's return
is marshalled as: `None` → `{}`; a dict → that event; a list → `{"events":
[...]}`. `fromJSON` of any dict yields a non-null event. An empty list reaches
the sequence constructor, whose first line is `sequence.get(0)` —
`IndexOutOfBoundsException`, not a skip (spike B_7 measures which failure mode
that actually produces end to end). Skip-by-return-value is not misdocumented
so much as **unreachable over the bridge**; no microclaw-side wrapper can
translate `None` into anything that works.

## The third promise: the processor discards, nothing drops

`image_process_fn` runs in a different pipeline — the image processor thread,
strictly *downstream* of the engine, between acquisition output and storage
(`java_backend_acquisitions.py:176`, `_run_image_processor`):

```python
processed = acquisition._call_image_process_fn(image, metadata)

if processed is None:
    continue
```

Returning `None` withholds the image from storage. That is all it does. The
processor has no channel back to the engine (design/24 established this for
*adding* events; it is equally true for removing them), so *"drop all
remaining events for that position"* describes machinery that does not exist.
Every remaining event for that position still moves the stage, opens the
shutter, and exposes the sample; the frames are acquired and then discarded
one by one as they arrive (spike B_5).

`PositionFilterHook` (`hooks.py:263`) inherits this: its docstring repeats the
drop claim, and its log of `action: rejected` per position is honest about the
dataset but silent about the fact that the "rejected" position kept being
exposed for the rest of the acquisition.

## Why nobody noticed

The same reason as design/24: nothing in the tree ever exercised the failing
half. `PositionFilterHook`'s observable contract — rejected positions absent
from the dataset — holds, because the discard half works; the phantom "drop"
half only wastes light, and wasted light does not fail a test.
`MMAutofocusPluginHook`'s `return None` sits on the unsafe-Z path, which fires
only when a guard trips mid-acquisition — apparently never yet on a rig. And a
ghost frame announces itself only as a log entry with `position: None`, which
looks like a metadata quirk (design/23 taught everyone that missing keys are
normal) rather than an exposure that should not exist. It took a hook whose
*entire purpose* was the skip — `SNRMatchStop`, written by the agent, from the
docs, on the first post-design/24 rig session — to make the ghosts visible.

## Why it matters more than a wrong sentence

**`MMAutofocusPluginHook`'s safety story is partly fictional**
(`hooks.py:373-375`). After the MM autofocus plugin drives Z somewhere the
guard rejects, the hook logs `autofocus="unsafe_abort"` and returns `None` —
documented (`hook_docs.py`, autofocus_mm_plugin: *"the capture is skipped and
the acquisition stops"*). In reality: the capture is **not** skipped — the
camera fires **at the unsafe Z** (the plugin already moved there, and the
ghost event contains no Z to move away to) — and the acquisition does **not**
stop; every subsequent event runs the plugin again, and each new rejection
fires another unlabeled exposure. The log says "abort"; the hardware says
"continue, off the record." The passive-guard design (never re-drive Z against
the plugin's controller) remains right; its enforcement arm is a no-op.

**Every agent-generated skip pattern inherits it.** The docs offer return-None
as *the* skip idiom (`hook_docs.py:46,56,144,150`, plus the "Choosing the
right hook type" table). The agent used it exactly as documented, promised the
user "no bleaching past the trigger", and delivered five unauthorised
exposures — sourced-looking success wrapped around silent hardware activity.
That is one notch worse than design/24's no-op: the no-op failed to *add*
work; this one fails to *withhold* light, and light is the budget the whole
lab economy runs on.

## Fix 1 — stop promising it; say what the lever actually is. Do this now.

`hook_docs.py` corrections, all three sites plus the table and the HookBase
skeleton comments:

- `post_hardware_hook_fn` / `pre_hardware_hook_fn`: **never return `None`.**
  Over the ZMQ bridge there is no way to cancel an event from a hook's return
  value: `None` becomes an empty event that still fires the camera, unlabeled,
  at the current stage position. Return the event, modified or not.
- `image_process_fn`: returning `None` **discards the image and nothing
  else**. No event is dropped; the position keeps being exposed. Say "keeps
  the frame out of the dataset", never "skip this position".
- The one loud lever a hook has is **raising**: pycro-manager's hook thread
  catches the exception and calls `acquisition.abort(e)`
  (`java_backend_acquisitions.py:90`), which aborts the whole acquisition and
  surfaces the error to the caller. "Skip the rest" from inside a hook is not
  available; "stop everything, loudly" is. (One caveat the spike must
  measure, B_6: after `abort(e)` the hook thread still sends the `None` as
  `{}` (`java_backend_acquisitions.py:92`), so the abort may race one final
  ghost exposure out the door. Promptness, not correctness.)

As in design/24 Fix 1: the correction must be total. No "under some backends";
microclaw has exactly one backend and on it the promise is dead.

## Fix 2 — `MMAutofocusPluginHook` raises on unsafe Z

The current `return None` intends "skip this capture and stop the
acquisition". Raising is the only mechanism that actually does either — it
does both: no capture at the unsafe Z (the abort kills the event before...
no: **measure it**, B_6 — the abort is asynchronous, and the ghost `{}` is
already in flight; if the race lets the unsafe exposure fire, the raise still
stops everything *after* it, which is strictly better than today's fire-and-
continue-forever), and the run ends with an error naming the Z and the guard
limit instead of a log line nobody reads until later. Keep the log entry
(`unsafe_abort`, now truthful), then raise `SafetyViolation`.

`PositionFilterHook` keeps returning `None` — the discard half is real and is
its purpose — with the docstring corrected to stop claiming event drops, and
a log field or docstring note that rejected positions continue to be exposed.

## Fix 3 — upstream

The clean fix is pycro-manager's: `RemoteAcqHook`'s protocol needs an explicit
cancel encoding (e.g. `{"cancel": true}` mapped to a null return in `run()`),
and pyjavaz's `None → {}` default is the enabling bug. Worth an upstream issue
with the B_1–B_4 measurements attached; not worth blocking Fix 1 or Fix 2 on,
and microclaw cannot ship it (design/26's F1 note applies: our pin is 1.0.2,
and even a fixed pin leaves every installed MM jar in the field unfixed).

## The spike

`design/27-return-none-spike.py`. B_1 and B_2 run anywhere pycro-manager and
pyjavaz import — no MM, no hardware: B_1 reads pyjavaz's wire encoding of
`None` off a real socket; B_2 drives pycro-manager's real `_run_acq_hook`
thread over real ZMQ sockets, playing the Java side, and measures all three
return paths (passthrough, `None`→`{}`, raise→`abort(e)`+`{}`). B_3–B_7 are
gated on a Micro-Manager at localhost:4827 (demo config suffices) and measure
the engine's half for real:

- **B_3** `post_hardware` returns `None` mid-run: ghost frames exist, are
  unlabeled, the stage does not move for them, the dataset's shape around
  five identically-keyed ghost frames, and the run completes without error.
  (The rig trace, made deterministic and assertable.)
- **B_4** `pre_hardware` the same: "hardware never moves" is the true half,
  the fired capture is the false half.
- **B_5** `image_process_fn` returns `None` for one position: the processor
  still sees every frame of that position (nothing dropped), the dataset
  contains none of them (discard works).
- **B_6** the raise path: the acquisition aborts, the error surfaces to the
  caller (loud), and how many ghost exposures the abort race lets out.
- **B_7** the empty-list probe, last and watchdog-wrapped because its
  expected failure is Java-side (`sequence.get(0)` on an empty list): measures
  whether that failure is loud, silent, or a wedge. Informational — it fails
  the spike only by being *silent*.

Exits non-zero if any stage that ran fails (skipped gated stages are not
failures), so the demo-config machine can use it as a regression check
alongside design/24's.

## Tests

Once Fix 1 and Fix 2 land:

- `hook_docs.py` documents return-`None` as **never** a skip — no push/skip
  promise for pre/post-hardware hooks, no "drop remaining events" for the
  processor. Assert on the doc text; it is the agent's spec and it is what
  was wrong (same shape as design/24's doc tests).
- `MMAutofocusPluginHook` on a guard-rejected Z **raises** after logging —
  unit test with a mocked plugin; assert the exception type and that the log
  entry landed before the raise.
- `PositionFilterHook`'s docstring/log no longer claims event drops (doc-level
  assert), and its discard behavior keeps its existing unit tests unchanged.
- The ghost-exposure mechanism itself is upstream and can only be pinned by
  the gated spike stages on the demo-config machine — the same standing
  arrangement as design/24's A_9.

## What this does not do

- **It does not fix the skip.** There is nothing to fix it *with* on our side
  of the bridge; Fix 1 replaces a dead lever with the honest one (raise =
  abort everything, loudly). If a future design needs true per-event skipping,
  that is the upstream Fix 3 conversation, not a microclaw workaround.
- **It does not re-litigate design/24.** The candidates-queue runner is
  unaffected — it adds events; it never promised to remove any. The one
  interaction is B_6's abort race, and design/24's terminator ownership
  already made aborts deadlock-safe under the generator runner.
- **The engine-side numbers await the demo-config run.** B_1/B_2 pass locally
  (macOS, no MM). The rig trace already demonstrates B_3's core claim on real
  hardware — five ghost frames are in the founding history — but B_3–B_7's
  specific measurements (dataset shape under colliding ghost keys, the
  pre-hardware variant, the abort race count, the empty-list failure mode)
  need the machine with MM open, like design/24's A_9 before them.
