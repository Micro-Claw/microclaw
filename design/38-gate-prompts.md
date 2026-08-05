# M5 rig gate — plus-acquisition fixes + returned F9 evidence

Branch: `fix/plus-acquisition-findings`.
Sample: the bead slide from the 2026-08-04 sessions, or equivalent.
Total added dose: **~15 frames** plus one autofocus sweep. Everything else is
property reads and offline work.

This runbook has now been executed. G1 and G6 bracketed the imaging gates: G1
established the healthy baseline, and G6 proved the teardown mechanism. The
record below preserves the procedure and its settled result rather than leaving
probe-first language in place.

Record everything. Return the files listed at the end. **If a gate's actual result
differs from its expected result in any way, stop and report — do not adapt the
procedure to make it pass.**

---

## G0 — pin and sanity

```
git fetch origin
git checkout fix/plus-acquisition-findings
git pull
git merge-base --is-ancestor ee5d330 HEAD
echo %ERRORLEVEL%
```

Expected: `0`. Any other value means this runbook does not describe the code you
have — stop.

Then, because a stale editable install has repeatedly produced errors that look
like something else:

```
pip install -e . > g0_install.txt 2>&1
python -m pytest -q > g0_tests.txt 2>&1
```

Expected: the suite passes. Send both files.

---

## G1 — TTL baseline, and the mid-session hypothesis

**This is an investigation, not a pass/fail gate.** We do not know how TTL got
closed. Record what happens; do not try to make any particular outcome occur.

Start microclaw fresh. Ask it, in one message:

> Read and report these properties verbatim: on `iChrome-MLE-TCP` — `All: 1. Enable`,
> `All: 2. Emission`, `All: 3. TTL Enable`, and for N in 1,2,3,4 both
> `Laser N: 1. Enable`, `Laser N: 4. Use TTL`, `Laser N: 6. Status`. On
> `Laser Trigger` — `Mode3` and `Sequence3`. Do not change anything.

**G1.a — expected baseline** (from the working acquisition embedded in
`plus_mosaic_2`'s SystemStateCache, 2026-08-04 16:02):

```
All: 3. TTL Enable      = 1
Laser 1..4: 4. Use TTL  = 1   (all four)
Laser 1: 6. Status      = AVAILABLE ENABLED USETTL
Laser Trigger Mode3     = 4 - Follow
Laser Trigger Sequence3 = 65535
```

If TTL is **already 0** before you touch anything, that is itself the finding —
it means the last session left it closed. Record it, then ask microclaw to
restore `All: 3. TTL Enable` to `1` (it should ask you to confirm; confirming is
correct here) and re-read before continuing.

**G1.b — show the declaration.** Open M5's `safety_config.yaml` and copy the
whole `illumination:` block into `g1_config.txt`. The question this answers: is
`All: 3. TTL Enable` listed under `shutters`, and with what `off_value` /
`on_value`? If it is **not** listed, say so explicitly — that alone rules out two
of our three hypotheses.

**G1.c — turn the laser off, mid-session.** Ask microclaw:

> Turn off the 640 nm laser.

Record **exactly which tool call and which property write it used** (the session
history has this). Then re-read the full G1.a property list.

- If the four `Use TTL` values are now `0` → the mid-session hypothesis is live.
- If they are still `1` → mid-session laser-off is not the cause; the teardown
  test in G6 becomes the primary hypothesis.

**G1.d — turn it back on, mid-session.** Ask microclaw:

> Turn the 640 nm laser back on and confirm it is ready to fire.

Re-read the full list. Record whether TTL was restored, whether you were asked to
confirm anything, and whether microclaw noticed the gate was closed at all.

**G1.e — restore to the G1.a baseline before continuing.** Whatever happened, get
back to `All: 3. TTL Enable = 1` and all four `Use TTL = 1`, and verify by
re-reading. The imaging gates below need a known-good rig.

Save the full session history for G1.

---

## G2 — session B's request, in one pass

This is the direct test of the multiposition and live-view fixes. Dose: 5 frames
plus one autofocus sweep.

Start live view first — deliberately, because session B's acquisition failed with
live running and the old error told the agent to retry, which could not work.

Ask microclaw:

> Start live view. Then acquire a 5-tile plus pattern centred on the current
> position with a half-FOV step, using the 640 nm laser, autofocusing at each
> field, and save it to `D:\SSD\gate_g2`. I want a single dataset with a position
> axis so it can be mosaicked.

Expected:

- Live view is paused and restored around the acquisition without you being asked
  to stop it, **or** microclaw tells you plainly to stop it — but never tells you
  to "retry the call".
- One dataset, not five per-position folders.
- Autofocus reports per-field results.
- The agent proposes the single-dataset path **first**, without you having to ask
  why it did not.

Record: the dataset path, the per-field autofocus results, and whether live view
was running again at the end.

**Note:** if the agent needs two acquisitions to get one dataset, that is a
failure of this gate — report it. The whole block exists because that happened
twice.

---

## G3 — mosaic from Micro-Manager's own calibration (headline fix)

Zero added dose. This is the fix for "stage mosaic could not use any of the
calibration files."

Ask microclaw:

> Build a stage-coordinate mosaic from the `gate_g2` dataset and save it to
> `D:\SSD\gate_g2_mosaic.tif`. Use the calibration Micro-Manager already has —
> do not run a new calibration.

Expected:

- It succeeds **without** `calibrate_stage_to_camera`.
- It resolves the calibration from the dataset's own recorded affine — the result
  should name `PixelSizeAffine` as the source.
- The mosaic shows a **plus**, correctly oriented, with the arms overlapping by
  half a field. Beads visible in two adjacent tiles must land on top of each
  other, not mirrored across the diagonal.

**Open the mosaic and look at it.** The affine on this rig is close to a 90°
rotation, and a transposed affine produces a mirrored image that is otherwise
plausible. A wrong mosaic here will look like a reasonable mosaic. The check is
whether shared beads in the overlap regions superimpose.

If the agent asks to run a calibration anyway, tell it no and report that it
asked.

Also, for the record, ask it:

> What calibration source, affine, and pixel size did you use?

Expected affine, from the dataset:
`a=0.004928, b=-0.104528, c=-0.106389, d=-0.005127`, pixel size ≈ 0.1056 µm.

Send the mosaic TIFF.

---

## G4 — hook contract (no dose)

Ask microclaw:

> Write and save a hook called `gate_reversed` whose `analyze_frame` returns
> `HookResult({}, (EmitArtifact(mosaic, self.out_name),))` — deliberately passing
> the array first and the filename second. Do not run it.

Expected: saving is **rejected at preflight**, with an error saying the arguments
are reversed and naming `EmitArtifact(filename=..., payload=...)`. It must never
reach an acquisition. This is the exact defect that cost session A five exposures.

Then:

> Now fix it to use the keyword form and save it.

Expected: accepted.

No acquisition is needed for this gate. Record both results.

---

## G5 — artifact inspection is bounded (no dose)

Ask microclaw:

> Inspect the artifacts under `D:\SSD`.

Expected: it returns **quickly** — either a hashed result if the drive is small
enough, or a refusal that names the limit it hit **and includes a directory
survey with per-directory file counts and byte totals**. It must not hang.

The refusal must be useful enough to locate a dataset. Confirm you can see
`gate_g2` in the survey output. Then:

> Using that survey, inspect only the `gate_g2` dataset directory.

Expected: a normal hashed artifact list.

If this call takes more than about a minute, stop it and report — that is the
original 10-minute-hang defect not fixed.

---

## G6 — teardown mechanism (settled; run last in the gate)

This deliberately leaves the rig with TTL possibly closed. Nothing may follow it.

**G6.a.** With microclaw running, re-read the G1.a property list one final time
and record it. Confirm `All: 3. TTL Enable = 1`.

**G6.b.** Exit microclaw cleanly — the normal quit path, not a kill. **Do not
restart it.**

**G6.c.** In Micro-Manager's Device Property Browser (not through microclaw),
read `iChrome-MLE-TCP` → `All: 3. TTL Enable` and all four `Laser N: 4. Use TTL`.

This was the decisive measurement, and the returned result was the first case:

- `1` before exit and `0` after → **session teardown closes the gate.**
  `shutter_all` drives every declared illumination shutter to `off_value` on exit,
  and the aggregate write clears the per-laser values. Turning the laser off was
  incidental.
- unchanged → teardown is not the cause; combine with G1.c.

**G6.d.** Restart microclaw. Ask it to read the same properties. Does it restore
anything, or report that a declared shutter is closed?

**G6.e — the blank-frame test.** Only if TTL is now `0`. Do **not** re-arm it.
Ask microclaw:

> Acquire a single frame at the current position with the 640 nm laser, slot 3,
> and save it to `D:\SSD\gate_g6`.

Returned result: microclaw accepted the run because preflight checked only trigger
mode and sequence. The frame matched background; the with-TTL control contained
signal. Preflight therefore did not verify end-to-end emission.

**G6.f.** Re-arm: set `All: 3. TTL Enable` back to `1`, verify all four
`Use TTL = 1` and `Laser 1: 6. Status = AVAILABLE ENABLED USETTL`, and leave the
rig as you found it.

---

## Return kit

- `g0_install.txt`, `g0_tests.txt`
- `g1_config.txt` (the `illumination:` block)
- The microclaw session history JSONL for every gate
- The G2 dataset path and per-field autofocus results
- `gate_g2_mosaic.tif`
- Your recorded property tables from G1.a, G1.c, G1.d, G6.a, G6.c, G6.d
- Anything that surprised you, in your own words — that has been the most useful
  part of every previous return

## What this gate does not cover

F9 is settled by this return: the declared property plus `shutter_all` in the
session `finally` caused the state change on every exit route, including Ctrl+C;
G6.c and G2's opening reads confirm it. The product follow-up removes teardown
writes, reports declared illumination state on exit and in acquisition context,
and narrows preflight's claim to the trigger checks it actually performed. The
per-source arming-chain refusal remains a separate operator-authored follow-up.
