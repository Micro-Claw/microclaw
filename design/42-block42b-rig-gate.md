# Block 42b rig gate — `open_artifact`, on the demo machine

Branch `design42/open-artifact`. Check it out on the machine and keep this file
open for the whole run.

**42b is a read-side block, so most of it does not need M5's hardware.** Opening
a file, proving a window appeared, verifying recorded digests, and refusing to
render pixels nobody asked for are all machine-independent. This runbook runs the
whole gate on the demo machine against artifacts copied from M5, and names at the
end the two things that genuinely still need M5.

**This is a behaviour gate as much as a mechanism gate.** The mechanism is easy
to score: a window either appeared or it did not. The behaviour is the point of
the block, and it is scored on what is *absent* from the transcript. Read G2
before you start so you know what you are watching for.

## Why this does not acquire and build a mosaic here

The original acceptance criterion was to re-run the failing session verbatim —
acquire six positions, build the mosaic, then open it. **Do not do that on the
demo machine.** design/29 measured the demo's pixel-size configs (`Res10x`,
`Res20x`, `Res40x`) and found all three carry the *same identity affine* and the
same canonical SHA-256, even though 20× cannot have the same µm/px as 10×. A
mosaic built here would be assembled from an identity default — 1 µm/px,
unrotated — which design/29 names as an empirically confirmed hazard that
"passes a naive finite+nonsingular check and would silently yield 1 µm/px for
every objective".

So a demo-built mosaic is not weaker evidence for this gate; it is *misleading*
evidence, and scoring `open_artifact` against one would mean scoring it against a
picture we already know to be wrong. G1 opens the real M5 mosaic instead.

**This costs nothing that 42b owns.** 42b implements the read side. Whether the
mosaic was built correctly is `build_stage_coordinate_mosaic`'s question and was
settled on M5 already: the artifact's digests still recompute exactly.

## Pin the implementation

```powershell
git merge-base --is-ancestor 867f3af HEAD
if ($LASTEXITCODE -eq 0) { "PINNED OK" } else { "WRONG TREE - stop" }
```

Print it in words — `echo %ERRORLEVEL%` prints the literal string in PowerShell
and verifies nothing.

## Two things 42a learned about running things here

- PowerShell's bare `>` writes **UTF-16LE**, which made 42a's evidence awkward to
  read. Use `2>&1 | Out-File -Encoding utf8 out.txt`.
- `uv run` writes progress to **stderr**, which PowerShell surfaces as a red
  `NativeCommandError` before the script starts. That is not a failure.

## Setup

Micro-Manager open on the demo config, ZMQ server enabled (Tools > Options, port
4827). Nothing here moves a stage into anything, raises any power, or needs a
dark room.

**Copy three things from M5**, and note that the first two are a pair:

| what | why |
|---|---|
| `stitch_test_mosaic.tiff` | the artifact G1 opens |
| `stitch_test_mosaic.tiff.json` | **its sidecar.** Without it there are no digests to verify and G1 degrades to "provenance unverified" |
| `stitch_test_1\` (the whole NDTiff directory) | G0 and G4's directory case |

Put them anywhere and substitute your paths below; nothing depends on the
location. **Record the exact paths you used in what you send back.**

**Record the Micro-Manager version** (Help > About) before you start. The
directory branch was designed from a disassembly of MM 2.0.3-20260625's
`MMJ_.jar`. If this machine runs a different build, G0 is what tells us whether
the mechanism survived the version change — and a demo-only pass then does not
automatically transfer to M5.

That the M5 digests verify **on a different machine** is not a weakness of this
setup. It is a stronger result than verifying them where they were written: it
shows the check is file arithmetic and carries nothing machine-specific.

---

## G0 — the directory mechanism

**Run this first.** The tool's directory branch was designed from a jar
disassembly, which proves those methods exist and proves nothing about whether
pyjavaz can reach them or whether a window paints. G0 is what settles that.

```powershell
python design\42-ij-dir-spike.py --dir "C:\path\to\stitch_test_1" 2>&1 | Out-File -Encoding utf8 out42b-G0.txt
Get-Content out42b-G0.txt
```

- **D2 PASS is the gate.** It means MM's own reader opened MM's own format,
  virtual, with dimensions agreeing with what `ndstorage` reads Python-side, and
  the bridge free straight after.
- **D2 FAIL, or `load_data` raising or returning null**, is a real outcome and
  not a disaster: `open_artifact` then reports `opened: false` with the reason
  and does not claim a window. Send the output; the directory branch becomes a
  named refusal instead.
- **D4 is skipped unless you pass `--try-drag`.** Doing it once is worth it and
  is the only way to reproduce what you saw when you dragged the folder by hand:
  it will put up ImageJ's "Open all N images … as a stack?" dialog and hold the
  bridge until you answer. Say which button you pressed.

**Say what you see on screen**, not only what the file says. `WindowManager`
agreeing is a structural check; your eyes are the only check on whether anything
painted. Close the windows it leaves open by hand.

---

## G1 — open the mosaic and show it to me

Start a normal microclaw session. Ask for the original request's operative
clause, in the original words, pointing at the copied file:

> Open the mosaic at `C:\path\to\stitch_test_mosaic.tiff` and **show it to me**.

Score:

- [ ] Microclaw calls **`open_artifact`**.
- [ ] A **new ImageJ window you can see** appears, showing the mosaic.
- [ ] The reply reports the window with **dimensions matching** the file
      (1004 × 1024), and **both digests confirmed** —
      `pixel_sha256_matches` and `manifest_payload_sha256_matches` both true.
- [ ] The reply contains **no instruction to open anything in FIJI or the MM
      GUI**. That sentence is the defect this block removes.
- [ ] The result's top-level `opened` is `true`. If it is `false`, microclaw
      must relay the reason and **must not** describe a window.

---

## G2 — the transcript contains no thumbnail

**A rendered image in the transcript at G1 is a FAILED GATE even though the
window opened.** This is not a cosmetic preference and not a token-budget
nicety; it is the behaviour the whole design turns on.

"Show it to me" is a **show-me**, not an **analyze-me**. The file is on your
screen — that is what "show" meant. Rendering it back into the conversation
spends 32,496 base64 characters (measured on this mosaic at 512 px) to describe a
picture you are already looking at, and the image block then rides along in
**every subsequent turn of the session**, not just the turn that produced it.

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
- [ ] **Run a small acquisition with the ImageJ window open** — a few positions
      on the demo camera is plenty. It completes normally. 42a check 6 measured
      that `IJ.open` does not hold the single pyjavaz lock; this is the same
      question with a real acquisition behind it.

**Keep the dataset that acquisition writes.** G4 uses it.

---

## G4 — open a dataset directory

Only meaningful if G0's D2 passed. Two cases, and the first is the better one.

**G4a — the dataset microclaw just wrote here.** This is the real loop the block
exists for: microclaw wrote it, now open it, on this machine, with no copying
involved.

> Open the dataset from that run.

- [ ] Microclaw calls `open_artifact` on the **NDTiff directory**.
- [ ] A **Micro-Manager display window** appears with the dataset in it — not an
      ImageJ image-sequence window, and not nothing.
- [ ] The result's `via` reads `micro-manager dataset reader`.
- [ ] It reports `provenance … unverified` (an NDTiff dataset has no microclaw
      manifest beside it) and opens anyway.
- [ ] Ask it to analyze the dataset. It **refuses and says why**, pointing at
      export or mosaic — it does not render a plane it picked itself.

**G4b — the copied M5 dataset.** Same request against `stitch_test_1`. This one
also proves the path is not sensitive to which machine wrote the data.

- [ ] Same five outcomes as G4a.

**The thing that must not happen anywhere in G4:** a seven-second pause with no
window and no error. That is `IJ.open` on a directory, and nothing in this branch
should be able to reach it. If you see it, say so immediately — it means the
directory branch was not taken.

---

## What to send back

1. `out42b-G0.txt`, whole.
2. The G1/G2 transcript, whole — the absence of a thumbnail is only checkable in
   the transcript itself.
3. Which windows you saw appear, in your own words, for G0, G1, G4a and G4b.
4. The Micro-Manager version, and the paths you copied the artifacts to.
5. For any failure: what was on screen at the time, and whether a dialog was up.

## Stop conditions

Stop and report rather than working around, if:

- a bridge call hangs and a modal dialog is on screen — that is the single
  pyjavaz lock being held, and it is the one thing this design is built to avoid;
- microclaw closes, reuses, or reactivates a window it did not open;
- a window is reported that you cannot see.

---

## What this run does NOT establish, and is still owed on M5

Record these as carried forward; do not let a demo pass be read as covering them.

1. **The end-to-end sentence.** *"Image these six positions, put them in a
   stitched mosaic, then open the mosaic and show it to me"* is the request that
   failed and the reason design/42 exists. This runbook exercises its second half
   against a real artifact, but not the handoff from a just-built mosaic to
   `open_artifact` inside one turn. That needs a rig with a genuine
   calibration — M5 or M2, not the demo.
2. **Whether the mechanism holds on M5's Micro-Manager build**, if the demo
   machine's differs. G0 answers it for whatever build it runs on; only M5
   answers it for M5. Compare the two version strings before assuming.

Neither is a defect in 42b and neither blocks merging it. Both are one short
session on M5 whenever it is next free, and the G1/G2/G4a steps above are the
script for it.
