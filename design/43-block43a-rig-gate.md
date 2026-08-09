# Block 43a rig gate — M5

Live view is a dose, and the TIFF export was being offered for the wrong reason.
design/43 F3 and F7. Run this on **M5**, because both halves want a rig where the
camera trigger fires the lasers — on M5 a running live view *is* exposure, which
is what makes F3 a dose finding rather than a tidiness one.

Everything here is PowerShell. Where a step says "record", paste the value into
the results table at the end; a step with no recorded value has not been run.

## Step 0 — pin the implementation

```powershell
cd C:\Users\ries\microclaw
git fetch origin
git checkout design43/live-dose-and-tiff-prose
git pull
git merge-base --is-ancestor af7e015 HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - the gated implementation is in this checkout" }
else { "PIN FAILED - stop, you are not testing the right code" }
```

`$LASTEXITCODE`, not `%ERRORLEVEL%` — the latter prints its own name as a literal
string here and verifies nothing.

Then reinstall, because a stale editable install has produced confusing failures
on this rig before:

```powershell
pip install -e . > install-43a.txt 2>&1
python -m pytest -q > suite-43a.txt 2>&1
Get-Content suite-43a.txt -Tail 3
```

Expected off-rig on macOS at `af7e015`: **1665 passed, 99 skipped, 3 expected
warnings, 1764 collected**. Windows skips more and passes fewer for the same
collection — judge by **failures and collected total**, never the passed count.
Any failure: stop and send `suite-43a.txt`.

## Step 1 — start the session with live view already running

This is the precondition the whole gate rests on. **You** turn live on, before
microclaw is involved, the way you would in an ordinary session:

1. Start live view from the Micro-Manager GUI.
2. Launch microclaw.

Do not ask microclaw to start live view. Half of what is being tested is that it
no longer does that on its own.

## G1 — an acquisition leaves live off, and says so

With live running, ask for a small tile acquisition. Keep it cheap; two or three
tiles is enough, and the point is the camera state afterwards, not the images:

> run a 2x2 tile acquisition, 20 µm spacing, and save it

Record from the tool result, exactly as microclaw prints it:

- [ ] `live_view_restore` is present and reads
      `{"requested": false, "left_off": true, "reason": "..."}`.
- [ ] The reason names `start_live_view` as the way to get live back.
- [ ] **Micro-Manager is not acquiring afterwards.** Look at the rig, not at a
      flag: the Preview canvas has stopped updating and the camera is idle.

**PASS** requires all three. If `live_view_restore` reads `"requested": true`,
the fix did not reach the path you exercised — record which tool you called.

## G2 — the agent tells you, rather than leaving you to notice

- [ ] In the same turn, microclaw states in prose that live view was left off and
      how to restart it.

A payload that carries the reason and an assistant that does not repeat it is a
partial pass: record it as such, because the prose half is the half the operator
actually reads.

## G3 — the open question this block exists to answer

design/43 F3 records this as untested and it is the only thing here that cannot
be settled off the rig.

- [ ] After G1, **what does MM's Preview window look like?** Specifically: is it
      closed, or open-but-frozen on the last frame? Is a frozen canvas confusing
      — would you, mid-session, believe the camera was still running?

Write a sentence or two. There is no PASS/FAIL: the answer is "off" either way,
and what is being decided is whether the *report* needs to be louder. If a frozen
canvas reads as a live one, that is a finding and this block will carry a fix.

## G4 — the export is not offered as the way to look at data

Still in the same session, with a dataset just written:

> let me look at what you just acquired

- [ ] Microclaw calls `open_artifact`. Record whether a window appeared.
- [ ] It does **not** offer `export_dataset_as_tiff`, and does not say anything
      of the form "export it to TIFF so you can open it in FIJI".
- [ ] If the dataset is multi-channel, microclaw says that opening the stack
      files shows channels as planes rather than named channel axes. Do not
      manufacture a multi-channel dataset for this; if the gate's dataset is
      single-channel, mark this **N/A** rather than PASS.

Then, deliberately, ask for the case the export *is* for:

> I want to run this through ThunderSTORM

- [ ] `export_dataset_as_tiff` is offered here, once, with the single-file reason.
      **This limb matters as much as the one above** — a fix that made microclaw
      refuse a legitimate export would have traded one wrong default for another.

## G5 — live view is not started unprompted

Across the whole session:

- [ ] Microclaw never turned live view on by itself.
- [ ] When you *ask* it to start live view, it says what that costs on this rig
      before starting — M5's lasers follow the camera trigger, so a running live
      view is continuous exposure.

The second bullet is prompt-conditioned and may be soft; record what it actually
said.

## Step 2 — the file check, after the session is closed

The session history JSONL is under your microclaw history folder. Set `$h` to it:

```powershell
$h = "<path to this session's *_microclaw_history.jsonl>"
"left_off rows:      " + (Select-String -Path $h -Pattern 'left_off').Count
"export-offer rows:  " + (Select-String -Path $h -Pattern 'export.{0,40}tiff' -CaseSensitive:$false).Count
```

Expected:

- **`left_off` ≥ 1.** Zero means no acquisition in this session took the new
  path, and G1 was not really exercised.
- **`export-offer rows` = 0**, *or* exactly the rows belonging to the ThunderSTORM
  turn in G4. Read them; do not just count them.

### These two patterns were validated before this runbook shipped

Against the Nestor session (`Micro-Claw/nestor-06082026/`), which is the
known-bad this block was written from:

| pattern | reads on known-bad | verdict |
|---|---|---|
| `export_dataset_as_tiff` | **1** | **rejected** — the obvious pattern, and it misses six of the seven offers, because six were prose ("export the three datasets to TIFF") and never named the tool. A runbook using it would have scored that session as almost clean. |
| `export.{0,40}tiff` (case-insensitive) | **7** | adopted — matches every offer and does not match the mosaic-TIFF mentions at `[43]`/`[45]`, which are filenames, not offers. |
| `left_off` | **0** | adopted — correctly zero on a session predating the fix, so a non-zero reading on the gate run means the new path fired. |

**design/43 F7 says six offers and lists `[121] [123] [127] [145] [221] [261]`.
There are seven.** The pattern found `[223]` as well — *"(If you'd rather I also
export each to a plain .tiff, say so.)"* — which the hand-read list missed. The
finding is unchanged and slightly understated; correct the count at the
post-merge design gate.

## Results

| gate | result | evidence |
|---|---|---|
| Step 0 pin + suite | | |
| G1 acquisition leaves live off | | |
| G2 agent says so | | |
| G3 Preview window appearance | | (prose, not PASS/FAIL) |
| G4 open, not export | | |
| G4 ThunderSTORM limb | | |
| G5 no unprompted live | | |
| Step 2 `left_off` count | | |
| Step 2 export-offer count | | |

Send back this table, `suite-43a.txt`, and the session history JSONL.
