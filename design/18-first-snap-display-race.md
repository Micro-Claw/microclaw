# design/18 — The first snap of a session shows "Waiting for Image..."

## Symptom

Lab, 2026-07-09 (history `20260709_183409_microclaw_history.json`). On the first
prompt of a freshly-launched session, `snap_and_analyze` fired the camera, the MM
Preview window popped up — and no image appeared. The window read
**"Waiting for Image..."**. The next snap displayed normally.

Both snaps reported `displayed_in_mm_viewer: true`, so the agent told the user
their image was on screen while they were looking at a placeholder.

Two early theories, both dead:

* **Not the parallel tool calls.** That turn issued `snap_and_analyze` and
  `get_available_channels` together. `agent.py` executes tool_use blocks in a
  sequential `for` loop on one thread, so the second ran strictly after the
  first returned.
* **Not the desktop shortcut.** It sets `MICROCLAW_FROM_SHORTCUT` and launches
  `microclaw serve`; microclaw attaches to an already-running MM over ZMQ and
  never touches its GUI. The icon was just how a *fresh* MM session got started.

## What MM actually does (F1–F5)

Established by `javap` over `MMJ_.jar` (MM 2.0.3), not by inference:

| | |
|---|---|
| **F1** | `"Waiting for Image..."` is a string literal in `DisplayUIController.buildInitialUI()`. It is the placeholder the window is *constructed with*, swapped out only when `DisplayUIController.displayImages(...)` runs. |
| **F2** | `SnapLiveManager.displayImage(Image)` opens with `if (!SwingUtilities.isEventDispatchThread()) { invokeLater(...); return; }`. Called from our ZMQ thread it is **fully asynchronous**. |
| **F3** | `SnapLiveManager.snap(boolean)` = `acquisitions().snap()`, then `displayImage(img)` per image, then `if (display_ != null) display_.toFront()`. On a cold snap `display_` is still null at that last check, so `snap(True)` returns to Python **before the window exists** — and skips `toFront()` too. |
| **F4** | `SnapLiveManager.getDisplay()` is a pure accessor: returns `display_`, or null if absent or closed. It never creates a display. Safe to poll. |
| **F5** | `createOrResetDatastoreAndDisplay()` rebuilds when `display_ == null \|\| display_.isClosed()`. **Closing the Preview window restores the cold state** — no MM restart needed to reproduce. |

The repaint that clears the placeholder is not a direct paint. The new-image
event drives `DisplayController`'s asynchronous, *coalescing* image-stats
pipeline (`Submitting compute request` → `Image stats ready` → `displayImages`).
So a single new-image event delivered to a half-constructed window can simply be
lost, and nothing ever re-sends it.

## What the rig said

`design/18-first-snap-display-spike.py`, run cold (Preview window closed) with
`--repush none | immediate | after-display`, then five more of each.

**The bug is nondeterministic.** It reproduced a handful of times across ~18 cold
snaps — rare, but consistent with biting the very first prompt of a session and
then never again. Warm snaps painted every single time. The bug is confined to
the cold snap.

**`--repush` cannot influence the pre-push eyeball.** Check 4 asks what is on
screen *before* check 5 does any re-pushing, so all three modes run identical
code up to that point. Any per-mode difference in the reproduction rate at check
4 is sampling noise. This matters: `immediate` never reproduced the bug in six
cold snaps, which says nothing about `immediate` and everything about the low
base rate.

**U2 answered — one re-push is enough.** In every run where the placeholder was
stuck, a single `live.display_image(img)` of the already-acquired image cleared
it. The coalescing stats pipeline did **not** swallow the extra insert. No new
exposure, so nothing bleaches.

**U3 unanswered, and left that way.** Re-pushing *immediately* (relying on
`invokeLater` FIFO to queue behind window construction) was never tested against
a genuinely stuck window, because the bug never reproduced in those runs. The
implemented fix waits for `get_display()` first — the only variant with evidence
behind it. Waiting costs a few milliseconds, once per session.

### Two corrections to the pre-spike reasoning

* **`get_display()` is weaker than it looks.** One run had `display_` non-null
  *and* a placeholder on screen. A non-null display means a **window exists**,
  not that your image painted into it. No MM API reports the canvas swap — which
  is why the spike had to ask a human what was on screen.
* **Spike check 3 mismeasures.** It reads `get_display()` from Python one bridge
  round trip after `snap()` returns, so it observes "did the Swing thread finish
  constructing the window within ~1 ms", not the `display_ != null` test *inside*
  `snap()`. F3 remains true of the Java code; the check just cannot see it. Note
  the inversion it did catch: the run where the window came up **fastest** is the
  run that stuck. With n=1 on that failure, no theory is built on it — the causal
  mechanism inside `DisplayController` is still open.

## The fix

`image_analysis.snap_to_numpy_displayed` — read the tell *before* snapping (after
`snap(True)` the window exists either way), and on a cold snap wait for the
window, then push the image we already hold:

```python
live = ctrl.studio.live()
was_cold = live.get_display() is None
images = live.snap(True)
img = images.get(0)
if was_cold:
    deadline = time.monotonic() + _DISPLAY_WAIT_S
    while live.get_display() is None and time.monotonic() < deadline:
        time.sleep(_DISPLAY_POLL_S)
    live.display_image(img)
```

The wait is bounded: a window that never arrives must not hang a snap, and
`displayImage()` creates the display if there is none — exactly what `snap(True)`
would have done.

The rejected alternative was warming the window by toggling live mode at connect.
It burns frames on every session start; the re-push costs no light at all.

`tools.snap_and_analyze` — `displayed_in_mm_viewer` now **observes** rather than
echoing its own `display` parameter:

```python
"displayed_in_mm_viewer": bool(display) and preview_window_open(ctrl),
```

This is load-bearing: `agent.py`'s system prompt tells the model *"never claim an
image is on screen unless it is true"*, and the field it points at was hardcoded
`True`. The prompt and the tool schema now say what the field really means — that
MM has a Preview window open for the image — rather than promising it reached the
biologist's retina.

## Tests

`TestFirstSnapRepush` covers warm (no re-push), cold (re-push the held image, not
a second `snap()`), push-ordering (only after the window appears), the bounded
wait when the window never arrives, and that the pixels survive. `TestSnapAndAnalyze`
gained a regression test for the field that lied. Each was verified to fail
against a mutant of the corresponding fix.

## Loose end

The reproduction rate is low, and the mechanism inside `DisplayController` — why
a fast-constructing window is the one that drops its new-image event — is not
understood. The fix is a defensive repaint, not a root-cause repair. If the
placeholder is ever seen again *after* this change, the next probe is
`--repush-count 2`, then the `immediate` variant against a reliably stuck window.
