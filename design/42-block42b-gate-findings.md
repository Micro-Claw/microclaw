# design/42 block 42b — demo-machine gate findings

Run of 2026-08-09 on the demo machine, at `bc93f76`. Artifacts:
`Micro-Claw/42b-open-artifact-demo/` (`out42b-G0.txt`, the session history, and
the three `pos_*` datasets the run wrote).

**Outcome: the file branch passed, the directory branch did not.** G1/G2 opened
the mosaic TIFF correctly, verified both digests, and refused to analyze until
asked. G0's D2 errored and G4 hung, so `open_artifact` on an NDTiff directory is
not shippable as written.

---

## F1 — MM's reader cannot read a dataset with a non-integer axis

G0 D2, against the M5 dataset `stitch_test_1`:

```
java.lang.ClassCastException: String cannot be cast to Integer
  at NDTiffAdapter.hashMapToCoords(NDTiffAdapter.java:119)
  at NDTiffAdapter.<init>(NDTiffAdapter.java:83)
  at DefaultDataManager.loadData(DefaultDataManager.java:230)
```

`hashMapToCoords` casts **every** axis value to `Integer`, inside the adapter's
constructor, so one string-valued axis fails the whole open. This is MM's
behaviour, not ours, and we cannot fix it.

We can predict it exactly, and cheaply. `ndstorage` reads the axes in Python
before any bridge call — measured at **0.00 s** on the returned demo datasets:

```python
Dataset(path).axes            # demo: {'time': [0]}      -> MM will read this
                              # M5:   some axis is str   -> MM will throw
```

So the directory branch must pre-flight the axes and **refuse by name** ("axis
`channel` has value `'640'`; Micro-Manager's NDTiff reader accepts integer axis
values only") instead of handing MM a call it is going to throw on. The runbook's
own decision rule already says `D2 FAIL -> 42b must refuse directories by name`;
this is that refusal, made specific instead of blanket.

Note the demo datasets are all-integer, which is why F1 was never going to be
what hung G4. F1 and F2 are independent.

## F2 — the hang: microclaw holds the bridge lock across MM's display construction

G4a issued three `open_artifact` calls in one turn. Tools execute sequentially
(`agent.py:437`), so the first blocked and the other two never ran — the GUI
showing two calls is the stream rendering ahead of execution. No dialog was on
screen and the MM window looked normal.

**The Python half is not the hang.** `_measured_shape` → `ndstorage.Dataset` on
the returned `pos_1_1` opens, lists coordinates and reads the plane in 0.00 s
each, measured here. The block is inside `_open_dataset_in_mm` (`controller.py:411`).

**What MM does that we don't.** Every one of MM's own call sites for this exact
three-call sequence runs it on a throwaway background thread and returns
immediately:

| call site | evidence |
|---|---|
| `DragDropUtil.drop` (the drop target we imitate) | `new Thread(...).start()`, then `dropComplete(true)` |
| `FileMenu.promptToOpenFile` | `new Thread(...).start()` |
| `FileMenu` open-recent handler | `new Thread(...).start()` |
| `FileMenu.openSciFIO` | `new Thread(...).start()` |

Read out of `MMJ_.jar` with `javap`. `DragDropUtil`'s private `loadData` is
`loadData(path, true)` → `manage(store)` → `loadDisplays(store)` — the same three
calls microclaw makes, in the same order. **The sequence is right; the thread is
wrong.** MM never blocks a caller on it, because nothing in MM is waiting: the
drop completes, the menu returns, and the window appears whenever it appears.

Microclaw runs it inline on the ZMQ worker, and pyjavaz holds one lock across
every round trip, so for as long as MM is inside that sequence **the entire
bridge is frozen** — no core call, no `get_system_state`, nothing. There is no
timeout anywhere in `controller.py`; `grep -n 'timeout\|watchdog'` returns
nothing for the whole file, while the 42a spike has a proper labelled
`bridge_call(label, fn, timeout)` watchdog at `42-ij-dir-spike.py:187`.

**Leading hypothesis for what specifically blocked**, consistent with every
observation: microclaw asked MM to open a dataset that was **already open in
this same MM instance**. `_acquire_with_hooks` runs every acquisition with
`show_display=True` (`tools.py:1648`), so the three-position run left three
viewer windows holding `pos_1`, `pos_2`, `pos_3`. G4a then asked MM to open
`pos_1_1` a second time, and on Windows a second NDTiff reader over files another
reader still holds can wait on the file lock indefinitely. It fits: no dialog,
EDT healthy and the window repainting normally, unbounded wait, only for the
dataset microclaw had just written, and not reproducible on macOS where my read
of the same bytes was instant.

This is a hypothesis, not a measurement. What *is* measured is the structural
defect above, which is real either way and is why a five-minute wedge was
possible at all.

## F4 — MM's dataset viewer cannot display a dataset with no channel axis

**This is the answer, and it retires F2's leading hypothesis.** The round-2 G4a
run (`42b-G4a-round2-history.jsonl`) opened the three `pos_*` datasets from disk
in a session that had **not** acquired them, so nothing held them open. The first
one did not stall. It returned a Java stack trace:

```
java.lang.IndexOutOfBoundsException: Index: 0, Size: 0
  at java.util.LinkedList.get
  at NDTiffAdapter.lambda$getImagesIgnoringAxes$2(NDTiffAdapter.java:278)
  at NDTiffAdapter.getImagesIgnoringAxes(NDTiffAdapter.java:276)
  at DefaultDatastore.getImagesIgnoringAxes(DefaultDatastore.java:210)
  at DisplayController.handleDisplayPosition(DisplayController.java:612)
  at AbstractDataViewer.setDisplayPosition(AbstractDataViewer.java:230)
  at DisplayController.create(DisplayController.java:256)
  at DisplayController$Builder.build(DisplayController.java:226)
  at DefaultDisplayManager.loadDisplays(DefaultDisplayManager.java:398)
```

So `loadData` **succeeded** — MM read the dataset. It is `loadDisplays`, building
the display, that fails.

Why: `NDTiffAdapter` keeps `coordsIndexedMissingC_`, an index of coordinates with
the **channel** axis stripped, and `"channel"` is the *only* string constant in
the entire class (`javap` on `MMJ_.jar`). `getImagesIgnoringAxes` looks up that
channel-stripped index and calls `.get(0)` on the result. Our datasets have axes
`{'time': 0}` and no channel axis at all, so the lookup returns an empty list and
`.get(0)` throws.

**Every single-channel dataset microclaw writes hits this.** It is an MM defect,
in display rather than in reading, and we cannot fix it.

The wedge follows from the crash rather than causing it: the exception is thrown
part-way through `DisplayController.create` on the EDT, and every later bridge
call then stalls — `pos_2` on `studio.displays`, `pos_3` on the `connection
check`, both watchdogged at 30 s and both correctly telling the operator to
restart. A subsequent `open_artifact` on the mosaic TIFF — a path that is
otherwise proven — also failed, exactly as the message predicted.

**F2's threading finding stands** (microclaw is still the only caller that holds
the bridge lock across MM display construction, and MM still backgrounds it at
all four of its own call sites). **F2's file-lock hypothesis is withdrawn** — it
was never needed. The round-2 run had no acquisition viewer open and failed
anyway.

### What this means for the block

`Studio.data().loadData` + `loadDisplays` is **not a viable entry point for the
datasets microclaw writes**. Block 42 exists to open what we wrote, and MM's
viewer specifically cannot open what we write. Two independent MM defects now
block it — F1 at load for string axes, F4 at display for missing channel — and
neither is ours to repair.

What still works, and is gate-proven: the **file** branch. `IJ.open` on a TIFF
opened the mosaic and verified its digests (G1/G2 PASS). `ndstorage` also reads
these datasets perfectly — `_measured_shape` returned 512×512×1 for the two that
stalled, from the same call that failed to display them.

## F3 — a blocked tool call takes down `microclaw serve` (deferred, low priority)

Recorded at the user's request; **not being fixed now.**

Ctrl-C could not stop the server and the terminal had to be closed. The SSE
generator (`webserve.py:610`) awaits `queue.get()` until `_TURN_DONE`. The turn
thread is a daemon, but the `StreamingResponse` never completes, so uvicorn's
SIGINT graceful shutdown waits on that in-flight response forever. The Stop
button cannot help either: `/api/stop` is cooperative and only lands at a tool
boundary, which a wedged tool never reaches — `webserve.py:631` says so itself.

So any indefinitely-blocked tool costs the operator the whole server with no exit
but killing the terminal. Worth fixing eventually; deliberately deferred.

---

## What this changes

- The **file** branch is good and stays as it is.
- The **directory** branch needs F1's pre-flight refusal and F2's "do not enter
  the wedge, and never block the bridge unboundedly if you do".
- A Python-side timeout alone is not a fix: when Java is still stuck, the pyjavaz
  lock stays held. Avoiding the blocking state is the fix; the watchdog is what
  turns a wedge into a report.
