# Block 42b rig gate — `open_artifact`, on M5

Branch `design42/open-artifact`. Check it out on the rig and keep this file open
for the whole run.

**This is a behaviour gate as much as a mechanism gate.** The mechanism is easy
to score: a window either appeared or it did not. The behaviour is the point of
the block, and it is scored on what is *absent* from the transcript. Read G2
before you start so you know what you are watching for.

## Pin the implementation

```
git merge-base --is-ancestor ed9c78a HEAD
```

Exit code 0 means the code this runbook describes is in what you checked out.
Print it in words — `echo %ERRORLEVEL%` prints the literal string in PowerShell
and verifies nothing:

```powershell
git merge-base --is-ancestor ed9c78a HEAD
if ($LASTEXITCODE -eq 0) { "PINNED OK" } else { "WRONG TREE - stop" }
```

## Two things 42a learned about running things here

- PowerShell's bare `>` writes **UTF-16LE**, which made 42a's evidence awkward to
  read. Use `2>&1 | Out-File -Encoding utf8 out.txt`.
- `uv run` writes progress to **stderr**, which PowerShell surfaces as a red
  `NativeCommandError` before the script starts. That is not a failure.

## Setup

Micro-Manager open, ZMQ server enabled (Tools > Options, port 4827). The session
this block exists to fix used six positions on a slide with cells; use the same
kind of sample. Nothing here needs a dark room and nothing raises power.

---

## G0 — the directory mechanism

**Run this first.** The tool's directory branch was designed from a disassembly
of the same two jars this rig runs (`ij-1.53c.jar` and `MMJ_.jar`, MM
2.0.3-20260625), which proves those methods exist but proves nothing about
whether pyjavaz can reach them or whether a window paints. G0 is what settles
that.

```powershell
python design\42-ij-dir-spike.py --dir "D:\SSD\stitch_test\stitch_test_1" 2>&1 | Out-File -Encoding utf8 out42b-G0.txt
Get-Content out42b-G0.txt
```

- **D2 PASS is the gate.** It means MM's own reader opened MM's own format,
  virtual, with dimensions agreeing with what `ndstorage` reads Python-side, and
  the bridge free straight after.
- **D2 FAIL, or `load_data` raising or returning null**, is a real outcome and
  not a disaster: `open_artifact` then reports `opened: false` with the reason
  and does not claim a window. Send the output; the directory branch becomes a
  named refusal instead.
- **D4 is skipped unless you pass `--try-drag`.** Doing that once is worth it and
  is the only way to see what you saw when you dragged the folder: it will put up
  ImageJ's "Open all N images … as a stack?" dialog and hold the bridge until you
  answer. Say which button you pressed.

**Say what you see on screen**, not only what the file says. `WindowManager`
agreeing is a structural check; your eyes are the only check on whether anything
painted. Close the windows it leaves open by hand.

---

## G1 — the failing session, run again, verbatim

This is design/42's acceptance criterion. Start a normal microclaw session and
ask for the original request, in the original words:

> Image these six positions, put them in a stitched mosaic. **Then open the
> mosaic and show it to me.**

Score:

- [ ] The acquisition runs and the mosaic is built, as before.
- [ ] Microclaw calls **`open_artifact`** on the mosaic.
- [ ] A **new ImageJ window you can see** appears, showing the mosaic.
- [ ] The reply reports the window with **dimensions matching** the file, and
      **both digests confirmed** (`pixel_sha256_matches` and
      `manifest_payload_sha256_matches` both true).
- [ ] The reply contains **no instruction to open anything in FIJI or the MM
      GUI**. That sentence is the defect this block removes.

---

## G2 — the transcript contains no thumbnail

**A rendered image in the transcript at G1 is a FAILED GATE even though the
window opened.** This is not a cosmetic preference and it is not a token-budget
nicety; it is the behaviour the whole design turns on.

"Open the mosaic and show it to me" is a **show-me**, not an **analyze-me**. The
file is on the operator's screen — that is what "show" meant. Rendering it back
into the conversation spends 32,496 base64 characters (measured on this mosaic at
512 px) to describe a picture the operator is already looking at, and the image
block then rides along in **every subsequent turn of the session**, not just the
turn that produced it.

- [ ] G1's turn contains **no image block**. `analyze` was false, or absent.

Then, in the same session, ask the second question:

> How many cells are in it?

- [ ] `analyze=true` appears **only** on this call.
- [ ] A thumbnail appears **now**, and microclaw answers from the pixels.
- [ ] The payload says the stretch was measured over **nonzero pixels**. (The
      mosaic is mostly uncovered canvas; stretching across it would have handed
      the model a near-black rectangle to interpret.)

If the model reached for `analyze=true` on the first request, capture the exact
wording it used to justify it. That is a prompt defect and it is fixable, but it
must be reported rather than waved through.

---

## G3 — the user owns the session

- [ ] **Close the ImageJ window by hand.** Microclaw neither reopens it nor
      complains about it, in that turn or any later one.
- [ ] Ask microclaw for something unrelated afterwards (`get_system_state` is
      enough). It does not mention the window.
- [ ] **Run a second acquisition** with the ImageJ window open. It completes
      normally. 42a check 6 measured that `IJ.open` does not hold the single
      pyjavaz lock; this is the same question with a real acquisition behind it.

---

## G4 — open the dataset directory

Only meaningful if G0's D2 passed.

> Open the dataset from that run.

- [ ] Microclaw calls `open_artifact` on the **NDTiff directory**.
- [ ] A **Micro-Manager display window** appears with the dataset in it — not an
      ImageJ image-sequence window, and not nothing.
- [ ] The result's `via` reads `micro-manager dataset reader`.
- [ ] It reports `provenance … unverified` (an NDTiff dataset has no microclaw
      manifest beside it) and opens anyway.
- [ ] Ask it to analyze the dataset. It **refuses and says why**, pointing at
      export or mosaic — it does not render a plane it picked itself.

**The thing that must not happen anywhere in G4:** a seven-second pause with no
window and no error. That is `IJ.open` on a directory, and nothing in this branch
should be able to reach it. If you see it, say so immediately — it means the
directory branch was not taken.

---

## What to send back

1. `out42b-G0.txt`, whole.
2. The G1/G2 transcript, whole — the absence of a thumbnail is only checkable in
   the transcript itself.
3. Which windows you saw appear, in your own words, for G0, G1 and G4.
4. For any failure: what was on screen at the time, and whether a dialog was up.

## Stop conditions

Stop and report rather than working around, if:

- a bridge call hangs and a modal dialog is on screen — that is the single
  pyjavaz lock being held, and it is the one thing this design is built to avoid;
- microclaw closes, reuses, or reactivates a window it did not open;
- a window is reported that you cannot see.
