# Block 43h rig gate — emitted adaptive programs

Implementation ancestor: a8af089

Use PowerShell from the checked-out repository. uv is the single launcher for
every Python/project command below; do not substitute bare Python for one line.
Git commands only establish the checkout.

## Which machine, and what each one can close

**Steps 0–3 run on the demo machine and close the block's core claim.** What
43h emits is a *program* — seed plan, hook source, decision loop — and whether
that program runs standalone is a mechanism question, not a sample question. The
demo config has a real MMCore, a real acquisition engine and a real bridge, so
the generator that holds the event source open, the terminator, the seed-plan
guard and the adapter's dispatch are all genuinely exercised.

**The demo camera returns bit-identical frames.** That is why Step 3b's hook
stops on a *frame count read from metadata* rather than on image content: a
content-driven stop cannot be distinguished from a stuck one when every frame is
the same, and a criterion that cannot fail is not a criterion (this is what
narrowed 43g's demo gate to reach only).

**Step 4 needs M5 or M2 and stays owed after a demo PASS.** Record a demo run as
closing Steps 0–3 and leaving Step 4 open; do not mark the block gated until
Step 4 has run.

### Start here if you are picking this up on M5 or M2

Demo rounds 1–3 already closed **Step 0, Step 1, Step 2a and Step 3** (see the
43h ledger row in `design/35-usability-and-pfs-checklist.md`). What a rig session
owes is:

1. **Step 0 again, on this machine.** Standing constraint: a block gated by a rig
   session runs the full suite there too. It is also the only part of the gate
   that exercises code this block did not touch.
2. **Step 4, including 4a** — the sample-driven decision, and the live-versus-
   emitted frame-count comparison that no round has been able to make.
3. **Step 3b, which a rig can finally exercise.** It went untested on the demo
   twice because a content threshold cannot fire on contentless frames. On a real
   sample it can, so run 3b's shape here and let the hook stop on a measurement
   rather than a counter.

Steps 2a and 3a do not need repeating unless something about the session makes
them free.

## Step 0 — pin and run the full Windows suite

    git merge-base --is-ancestor a8af089 HEAD
    if ($LASTEXITCODE -ne 0) { throw "Block 43h implementation is not in this checkout" }

    uv run python -m pytest -q > suite-43h.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content suite-43h.txt; throw "Full suite failed" }
    Get-Content suite-43h.txt

    uv run python -m pytest --collect-only -q > collected-43h.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content collected-43h.txt; throw "Collection failed" }

Expected on the Windows rig for this branch: **1730 passed + 116 skipped = 1846
collected**, with 3 expected warnings. The total is derived on this branch, not
copied from an earlier block. Compare 116 skips with the previous run on this
same host; stop if tests failed, collection is not 1846, or the skip count rose.

## Step 1 — offline export checks

These checks do not book microscope time.

    uv run python -m pytest -q tests/test_session_script_export.py > export-tests-43h.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content export-tests-43h.txt; throw "Exporter checks failed" }
    Get-Content export-tests-43h.txt

Pass when the file reports 95 passed. It covers all three seed shapes, exact
source inlining, saved-hook provenance, full-precision named-position
resolution, and narrow refusals.

## Step 2 — make one real adaptive program during an ordinary session

> **Round 1 of this gate failed here, and the criterion was at fault.** The
> request read *"Watch this field for three frames, record the image quality
> each time, and give me a standalone script I can keep and rerun later"* — and
> the agent answered it with three `snap_and_analyze` calls and an export, which
> is a fair reading. Nothing in that sentence required a decision to be made
> from what was seen, so nothing required an adaptive run, and the emitted
> script contained none of this block's code. Naming no tool is necessary and
> **not** sufficient: the request must be one that only the thing under test can
> answer.
>
> The distinguishing feature of an adaptive run is that **what it sees changes
> what it does next**, and that the decision survives into the script. Both
> halves have to be in the operator's sentence.

> **Round 2 PASSED 2a and failed Step 3, on a defect only a gate could find.**
> From the sentence below, unprompted, the agent wrote a hook, saved it, ran an
> adaptive survey with it and exported. The exporter emitted a correct and
> complete adaptive program — and the script died three lines before reaching it
> on `generate_and_save_hook`'s refusal, because no fixture had ever exported a
> session that *created* the hook it then used. Fixed in `9dcb09a`. **Keep 2a's
> wording; it works.**

### 2a — reach: does the agent get there unprompted

Fold this into an ordinary session with a small, safe plan. Phrase it at the
user level, naming no tool:

> Go through these positions one at a time and have it stop itself once it has
> seen enough — I don't want to sit and watch it. And give me a script that
> makes that call on its own when I rerun it next week.

Pass when the agent reaches an adaptive acquisition and exports a script,
without being coached toward an exporter, a runner or a hook.

**If it does not, that is a finding, not the end of the gate.** Record what it
did instead — that is the same class as 43e's round 1, where two built-in
adapters were correct and unreachable — then continue to 2b so the mechanism is
still tested. Do not skip to Step 3 with a non-adaptive script; it proves
nothing about this block.

### 2b — mechanism: ask directly if 2a did not land

Only if 2a failed. Ask plainly for an adaptive survey over a handful of
positions with a hook that stops the run once it has seen two frames, and let
the agent write and save the hook. Record 2a as FAILED and 2b as the route
taken; a block gated only through 2b is gated on mechanism and owes a reach
criterion.

### Inspect the emitted script either way

> **M5 round 2, 2026-08-11, failed here and it is the check this runbook was
> missing.** `export_session_script` returned **`emitted_calls: 0`** — the
> adaptive run refused on named-position resolution — and the agent, reading
> that correctly and saying so honestly, **hand-wrote an acquisition script with
> `write_text_file`** to honour the request. The operator ran the hand-written
> script; it opened one `Acquisition` per frame, and on the stop variant it
> acquired one frame and then hung the console for five minutes, unkillable by
> Ctrl+C. None of that is evidence about this block, and all of it looked like
> it was.
>
> **Two rules follow.** Check `emitted_calls` before anything else. And if the
> export is empty or refuses, the gate has *failed at Step 2* — a hand-written
> substitute is not a fallback, it is the fabrication path this block exists to
> remove. Record the refusal and stop; do not run the substitute and do not
> archive it as though it were the artifact.

**First, check the export actually produced something.** The tool result carries
`emitted_calls`; anything but a positive number means Step 2 failed, whatever
the file looks like.

Record the session history and the emitted script — and if the agent also wrote
a script by hand, archive it clearly labelled so nobody later mistakes it for
the export. Two `.py` files in one directory look identical at a glance; that is
how M5 round 2's evidence became hard to read.

Before ending the live session, inspect the script:

- Its event seed matches the requested frame interval/count (or Z range, if that
  is the safe session available).
- It contains the exact hook class, the adaptive decision source, and — for a
  saved hook — `UntrustedHookAdapter` plus the manifest sha256 as a provenance
  comment, rather than the tiles/frames observed in this run.
- `_LIMITS` contains the rig limits in force at export, and the header says
  editing the dictionary edits those recorded limits.
- The script contains no filesystem path into this Microclaw checkout; it writes
  beside itself via `_HERE`.
- **It imports nothing from `microclaw`.** New in round 4, from the operator's
  own request. Exactly two `microclaw` strings may survive, and both are *values*
  rather than imports — the observation schema id
  `microclaw.analysis-observation/v1` and the analyzer name
  `microclaw.image_analysis.compute_stats`. Stripping those would falsify the
  provenance of the measurement, so they stay on purpose.

      Select-String -Path .\PATH-TO-EXPORTED-SCRIPT.py -Pattern 'import microclaw','from microclaw'

  Pass when that returns nothing.
- **Its hook log is written beside the script** — `_log_path =
  _next_available_log_path(_HERE / '<name>')`, not an absolute path into the
  session's own data directory. Round 3's standalone run overwrote all four of
  the live session's hook logs, which is what destroyed that round's
  live-versus-standalone comparison.

**Stop here if the script contains no adaptive program.** The cheapest check,
and the one round 1 needed: the file must contain `_LIMITS`,
`_survey_event_stream` and `SurveyProgress`, and a `# RECORDED TOOL:
run_adaptive_*` line. If those are absent, the session never reached this block
and Step 3 cannot be run against this artifact — go back to 2b.

    Select-String -Path .\PATH-TO-EXPORTED-SCRIPT.py -Pattern '_LIMITS','_survey_event_stream','SurveyProgress','RECORDED TOOL: run_adaptive'

## Step 3 — close Microclaw and run the emitted program

Exit the Microclaw server/UI completely. Confirm no Microclaw process remains;
leave Micro-Manager and the bridge available. From PowerShell run the artifact
itself through the same launcher:

    uv run python .\PATH-TO-EXPORTED-SCRIPT.py > emitted-run-43h.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content emitted-run-43h.txt; throw "Emitted adaptive run failed" }
    Get-Content emitted-run-43h.txt

Pass only if the script completes the real multi-frame acquisition with
Microclaw closed, writes the dataset, and writes the hook observations expected
for the acquired frames. Importing or compiling the file is not evidence.
Confirm from the acquisition display/dataset that exactly the seed program ran;
do not infer success only from an empty terminal.

Run **both** sub-cases. They prove different halves.

### 3a — the straight path: adaptive timelapse with `snr_observer`

The seed plan is a full pre-built event list, so no decision is needed to
advance it. This proves the inlining, the `_HERE` output directory and the
real `Acquisition` under a precoded hook.

### 3b — the decision loop: adaptive survey with a saved counter hook

**This is the sub-case that makes a demo run worth doing.** Two facts force its
shape, both verified in the code at review:

- **Only a saved hook can advance an adaptive survey.** No hook in
  `PRECODED_HOOK_REGISTRY` calls `progress.image_done()` or `candidates.put()`,
  so a registry hook under `run_adaptive_survey` dispatches the seed tile and
  then idles out `max_idle_s`. Use a saved hook, which also exercises the
  manifest-pinned inlining path.
- **The stop must come from metadata, not pixels.** Every demo frame is
  identical, so a content threshold either fires at tile 1 or never.

Ask the agent, in the operator's words, for a survey over a handful of positions
that stops once it has seen two frames. The hook it saves should decide from a
frame counter or the metadata axes, for example — note that the **saved** hook
imports from `microclaw` and is meant to, because it runs inside Microclaw; the
exporter strips that import from the emitted copy, which is why Step 2's
"imports nothing from `microclaw`" check applies to the script and not to this:

```python
class StopAfterTwo:
    def analyze_frame(self, image, metadata):
        from microclaw.hook_decisions import ContinueSurvey, HookResult, StopSurvey
        self._seen = getattr(self, "_seen", 0) + 1
        action = StopSurvey() if self._seen >= 2 else ContinueSurvey()
        return HookResult({"frames_seen": self._seen}, actions=(action,))
```

Export it, close Microclaw, run the script, and pass only when **all** of these
hold in the standalone run:

- The dataset contains **2 frames from a plan of more than 2** — the seed plan
  is in the script, so a run that images every planned tile means `StopSurvey`
  never reached the dispatch.
- The script exits cleanly rather than hanging. A stop is *not putting* the next
  tile; the generator drains and its `finally` puts the terminator. A hang here
  is the design/24 terminator defect reappearing in the emitted copy.
- The hook log records the two `frames_seen` observations.
- Nothing in the run required Microclaw to be importable.

## Step 4 — M5 or M2: the sample-driven decision (NOT closed by the demo)

Steps 0–3 prove the program runs. They do not prove it *adapts to a sample*,
which is the claim F14 rests on — "searches, and finds that sample's cells".
On a rig with real structure, run the same shape with a hook whose stop
condition is a measurement of the image, and pass when the frames the script
acquires are chosen by what it saw. Record which tiles it took and why.

> **This is also the only place 3b's stop limb has ever been exercised.** It went
> untested on the demo twice, both times for the same reason: asked for a run
> that "stops itself once it has seen enough", the agent wrote a *content*
> threshold, and demo frames have no content (snr 1.41, coverage 0.0), so
> `StopSurvey` never fired in either the live or the standalone run. On a real
> sample a content threshold can actually fire, so Step 4 and 3b close together
> here.

### 4a — the live and the emitted run must agree

**New in round 4, and the reason this step now matters more than it did.** Round
3 measured the live run at **5 frames** and the exported script at **12**, from
the same program, the same hook and the same plan — because
`run_adaptive_survey` sized completion by the *position* count while its plan was
`positions × n_frames` *events*, making the generator's exit a race. Both runs
reported `stopped_early=False` under a status line that read *"5 frame(s)
acquired from a 4-tile plan"*, which sounds correct and hides the truncation.

Fixed in this branch: completion is now set from the built event list, and the
emitter counts `SurveyProgress(len(events))` so the two runners count the same
thing. This changes **live** acquisition behaviour, so it needs a rig:

- Run an adaptive survey with **more than one frame per position** — the defect
  is invisible at one frame per tile.
- Pass when the live run dispatches **every planned frame** (positions ×
  n_frames), not merely the position count, and the reported
  `frames_acquired` matches.
- Then run the exported script and pass when it acquires **the same number of
  frames as the live run**. That comparison is the block's central claim, and
  round 3 could not make it.
- **Read the hook logs before and after**, and note that the standalone run now
  writes its own log beside the script rather than overwriting the session's.

Two smaller limbs also stay owed to a real rig, and neither is a demo failure:

- **Exposure replay with a consequence.** A channel-less adaptive survey emits
  `guard.check_exposure(...)` then `core.set_exposure(...)`. The demo can only
  read the value back; on M5/M2 exposure is dose. Confirm the emitted script
  sets the exposure the session used and not the camera's current value.
- **The `Channel` group question.** The emitted events carry
  `channel_group=CHANNEL_CONFIG_GROUP` (`"Channel"`). The demo config has that
  group; **M5 does not have one at all**. Note what an M5 session records for an
  adaptive run's channel and whether the emitted script references a group that
  rig lacks. This is pre-existing across every emitter, not new in 43h — record
  it, do not fix it here.

## Archive

Archive suite-43h.txt, collected-43h.txt, export-tests-43h.txt, the session
history, both emitted scripts, emitted-run-43h.txt, the dataset paths and hook
logs, and the operator's observed pass/fail notes under
Documents\Documents - Beyonce\Projects\Micro-Claw. Say in the notes which
machine ran which step.
