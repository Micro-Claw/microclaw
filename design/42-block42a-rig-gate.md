# Block 42a — rig gate runbook

Branch `design42/ij-open-spike`. Read this on the rig, on this branch.

Block 42a has no product code. **The evidence is the deliverable**: six answers
about whether `ij.IJ.open()` can put a file we wrote into the ImageJ window
Micro-Manager already runs under. design/42 rests on two corrections argued from
the record and never measured, and one of the six (check 6) can stop block 42b
from shipping in this shape at all.

**Zero exposure.** The spike moves no stage, opens no shutter, fires no camera.
It reads two files off disk and asks ImageJ to open them. ~10 minutes.

## Before you start — confirm you are on the right code

```powershell
cd C:\path\to\microclaw
git merge-base --is-ancestor 0d97329 HEAD
if ($?) { "PIN OK - implementation is present" } else { "PIN FAIL - stop, wrong branch" }
```

`$?` rather than `$LASTEXITCODE`: a cmdlet in between silently stales that
variable. Read the printed words.

```powershell
pip install -e .
python -c "import microclaw; print('LOADED FROM', microclaw.__file__)"
```

The reinstall matters here more than usual. Unlike every other probe in
`design/`, this spike **imports `_new_static_java_class` from
`microclaw.controller` on purpose** — the question is whether the *shipped*
helper keeps `ij.IJ` and `ij.WindowManager` apart, so a copy would not answer it.
If microclaw does not import, the spike says so and exits; it does not fall back
to a copy.

## What to have open first

1. **Micro-Manager running**, with the pycro-manager **ZMQ server enabled**
   (Tools > Options > "Run server on port 4827"). Add `--port` if yours differs.
2. **The mosaic on disk**: `D:\SSD\stitch_test\stitch_test_mosaic.tiff` — the
   file from session `20260806_123046_193612`, the one microclaw could not open.
   ```powershell
   Test-Path D:\SSD\stitch_test\stitch_test_mosaic.tiff
   Test-Path D:\SSD\stitch_test\stitch_test_1
   ```
   Both should print `True`. If they are elsewhere, pass `--mosaic` /
   `--ndtiff`. **Do not substitute a TIFF microclaw did not write** if you can
   avoid it — check 3 compares ImageJ's dimensions against what Python reads
   from that exact file.
3. **A file ImageJ1 cannot read natively**, if this rig has one — `.nd2`,
   `.czi`, `.lif`, anything Bio-Formats handles. Check 4 needs it. Without one
   check 4 records **SKIP with a reason**, which is an acceptable result; a
   fabricated pass is not.
4. **The Micro-Manager window where you can see it.** Two of the checks can
   raise a modal dialog, and your eyes are the only check on whether a window
   actually painted.

## Run it

```powershell
cd C:\path\to\microclaw
python design\42-ij-open-spike.py > out42a.txt 2>&1
Get-Content out42a.txt
```

With a non-native file for check 4 (preferred — that is the check that can
provoke a modal Bio-Formats importer dialog):

```powershell
python design\42-ij-open-spike.py --foreign "D:\path\to\something.nd2" > out42a.txt 2>&1
```

Other flags, if you need them: `--port 4827`, `--mosaic <path>`,
`--ndtiff <dir>`, `--only 6` (run one check), `--timeout 120` (how long a bridge
call may take before it is called a stall), `--wait 20` (how long to keep
looking for the window after `IJ.open` returns).

The checks run **1, 2, 3, 5, 4, 6** — the two that can provoke a dialog run
last, so the other answers are already in the file. Every line is flushed as it
is produced, so `out42a.txt` is complete up to the moment of any hang.

## If it hangs

This is not a failure mode to work around — **"it hung" is itself the answer to
check 6, and it must be reported.**

pyjavaz holds a single lock across every bridge round trip. A modal ImageJ or
Bio-Formats dialog therefore blocks *every* subsequent call, including a core
call that would end an exposure. That is exactly what check 6 is asking about.

1. **Look at the Micro-Manager screen.** The likely cause is a modal dialog —
   most often Bio-Formats' Importer options (check 4), or ImageJ's "Unsupported
   format or not found" error (check 5). The spike prints a nudge telling you to
   do this after 5 seconds.
2. **Dismiss the dialog.** The call should then complete and the run continue.
   **Write down which check was in flight and what the dialog said** — verbatim
   if you can. That text is what block 42b will use to refuse a file with a
   reason instead of hanging.
3. If it does not recover, the spike gives up after `--timeout` seconds (90 by
   default), reports the stall as check 6's answer, and exits. That is a
   deliberate, correct outcome — not a crash.
4. After a stall, dismiss the dialog and re-run the checks you lost:
   ```powershell
   python design\42-ij-open-spike.py --only 6 > out42a-check6.txt 2>&1
   ```
   Send both files.
5. If the whole console is wedged with no dialog visible, `Ctrl+C`, then
   `Get-Content out42a.txt` — everything up to the hang is on disk. Say where it
   stopped.

**Do not close Micro-Manager to get out of it** unless you have to. If you do,
say so, because everything after that point is a different install state.

## Two results that stop the block

Report either immediately; do not re-run to try to get a nicer answer.

- **Check 1 FAIL.** design/42's correction of design/10 — "static IJ1 methods
  are not callable over ZMQ" was really design/12's cache collision — is then
  wrong, and the document's premise goes with it.
- **Check 6 FAIL, or a stall.** The bridge can be held while a sample sits under
  illumination. Block 42b does not start in this shape.

## Windows the spike opens

It opens up to three ImageJ windows (mosaic in check 3, the non-native file in
check 4, mosaic again in check 6) and **leaves every one of them open on
purpose** — microclaw never closes a user's windows. Their IDs and titles are
printed in a block near the end of `out42a.txt`. **Close them by hand** when you
are done.

## What to send back

1. **The whole of `out42a.txt`** — not the summary block, the whole file. The
   method-surface dumps in check 1 and the raw numbers in checks 3–6 are the
   deliverable.
2. **Which windows you actually saw appear on screen**, and their titles.
   `WindowManager` agreeing that a window exists is a structural check; only
   your eyes confirm that anything painted. If check 3 says PASS and nothing
   appeared on the monitor, say so — that is the most important sentence in the
   report.
3. **Any dialog that appeared**, what it said, which check was running, and
   whether dismissing it let the run continue.
4. Whether you supplied a `--foreign` file and which format it was. If you did
   not, say so — check 4's SKIP is then the honest result.
5. If anything was substituted (`--mosaic`, `--ndtiff`, a different port), say
   what and why.

A check you could not run is not a pass. `SKIP` with a reason is a real result;
a missing answer is not.
