# Block 43i rig gate — budgeted survey refocus

Implementation ancestor: 10085e2

## Round 4 — four things, and Step 4 is the one that keeps getting missed

Round 3 (`43i-m5-round3`) **PASSED Steps 0, 1, 3, 3b and 5, and one limb of 7.**
**3b is the block's own mechanism and it is now gated:** run 3's plan ran
`bead_pos_1 → bead_pos_3 → bead_pos_2`, so `bead_pos_2` was the *last* tile, it
took the refocus, and its `refocus=1` frame is in the dataset. That is round 2's
defect, fixed and measured. Two reporting findings came out of it, both fixed in
`10085e2` — a finished plan reported as a dose cap, and `RequestAutofocus`
stranding a survey.

### Run exactly these five things. Nothing else.

This list is the whole of round 4. If a step is not on it, do not spend time on
it; if it is on it, it is needed even if something similar already passed.

1. **Step 0** — the suite moved again (expect 1754 + 116 = 1870).
2. **Step 4** — the non-converging sweep, **inside a survey**. See "Which code
   path" below first: round 3 measured this on `run_autofocus`, which is not the
   code this block added.
3. **Step 6** — export the session, close Microclaw, run the script. **This was
   not asked for in round 3 and that omission was mine.** It is the
   compile-to-script principle and it is the last unmeasured claim of the block.
4. **Step 7a and 7b** — the no-budget refusal and the focus-lock refusal, both as
   survey hook-log records. 7c already passed; do not re-run it.
5. **Step 8 is already passed. Steps 2, 3, 3b and 5 are already passed.** Skip
   them.

Two corrections to what round 3 was told, both mine:

- Round 3's handoff named Steps 0, 3b, 4 and 7 and **did not name Step 6**, then
  the round was faulted for skipping it. It is named above.
- Steps 4 and 7 never said *which* autofocus implementation they meant, and round
  3 reasonably exercised the tool. The section below now says it outright.

## Round 3 — what M5 round 2 closed, and the one new criterion

Round 2 (`43i-m5-round2`) **PASSED Steps 0, 1, 5 and 8.** Step 5 is closed
properly: `refocus` reads `[0, 1]` on all three datasets, and the gate's own
dataset now recovers **4 of 4** real frames through the exporter's traversal
where round 1 recovered 1 of 4. Step 8 passed on Windows —
`"opened": true, "via": "os.startfile"`, no ImageJ, no wedged bridge.

It found two more defects, both fixed in `5aaa70d`:

- **A refocus granted at the last tile was silently dropped.** `pos_3` accepted a
  refocus and no `refocus=1` frame for it exists: completion was sized without
  the authorized re-exposures, so the survey ended as the last tile's image
  arrived and the re-queued event went into a queue nobody was reading. The
  sweep's dose was spent and the hook log still said "refocused and re-queued
  this tile". **Step 3 gains a criterion for exactly this case.**
- **The standalone script died at line 1908** on
  `run_analysis_on_saved_dataset`'s default refusal — one of the undecorated
  fifteen — *after* correctly running the adaptive program. Now `@emits_nothing`.

**Still never exercised, in either round: Step 4 and Step 7.** Both are cheap and
neither needs a different sample. They are the round's main remaining debt.

## Round 2 — what the M5 run of 2026-08-11 already closed

Round 1 (`43i-m5`) **passed Steps 0, 1, 3, 6 and 7's first limb** and found two
defects, both now fixed in `161d680`. Do not repeat what passed unless it comes
free with something else.

What round 2 owes:

- **Step 0 again** — the suite moved (10 new tests) and a rig-gated block runs the
  full suite on its own machine.
- **Step 4**, which round 1 could not run: see its rewritten instructions below.
  You do not need a different sample.
- **Step 5**, which round 1 **FAILED**. This is the round's main event.
- **Step 8**, new, verifying the second fix.

Round 1's live and standalone runs agreed to every digit — `entry_z_um`
48.96335 → `final_z_um` 50.79668333 in both, same positions, same actions, same
order — so Step 6's reproducibility claim is closed unless Step 5's fix disturbs
it. Re-run Step 6 anyway, because the dataset shape is what changed.

Use PowerShell from the checked-out repository. uv is the single launcher for
every Python/project command below; do not substitute bare Python for one line.
Git commands only establish the checkout.

## Which machine, and why the demo cannot close this one

**This block needs M5 or M2. The demo machine can close Step 0 and Step 1 only.**

43i's whole mechanism is the focus response. 43g measured that no single-frame
statistic separates cells from a diffuse bright gradient on real data, and that
what identified cells in both F5 and F6 was a sweep converging or refusing to.
**The demo camera returns bit-identical frames**, so its metric curve is flat by
construction: every sweep reports non-convergence, the converged limb can never
fire, and no criterion below Step 2 can distinguish working code from broken
code. A criterion that cannot fail is not a criterion — this is the constraint
that narrowed 43g's demo gate to reach only, and it applies harder here.

M5 is the natural host because the operator framing this gate uses came from its
Nestor session, but nothing here is M5-specific: any rig with a real sample and a
real focus response can run it.

**Dose note before you start.** An autofocus sweep is 20–60 extra exposures per
refocused tile. `autofocus_budget.max_exposures` bounds the total, and the run's
reservation is widened by exactly that number, so the authorization prompt shows
the real worst case. Size it deliberately for the sample in front of you.

## Step 0 — pin and run the full suite on this machine

    git merge-base --is-ancestor 10085e2 HEAD
    if ($LASTEXITCODE -ne 0) { throw "Block 43i implementation is not in this checkout" }

    uv run python -m pytest -q > suite-43i.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content suite-43i.txt; throw "Full suite failed" }
    Get-Content suite-43i.txt

    uv run python -m pytest --collect-only -q > collected-43i.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content collected-43i.txt; throw "Collection failed" }

macOS measured **1771 passed + 99 skipped = 1870 collected**, 3 warnings. Round 3
measured 1752 + 116 = 1868 on M5, the same 17-test platform-conditional
difference every Track F Windows run has shown, so expect **1754 + 116 = 1870**
here. Derive the total from passed + skipped rather than reading it off. Stop if
tests failed, if the collected total is not 1870, or if the skip count rose above
116.

## Step 1 — offline checks, no microscope time

    uv run python -m pytest -q tests/test_session_script_export.py tests/test_hook_decisions.py > offline-43i.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content offline-43i.txt; throw "Offline checks failed" }
    Get-Content offline-43i.txt

## Step 2 — reach: does an operator sentence get there at all?

**Reach is measured separately from mechanism, so a reach failure does not void
the rest of the gate.** If Step 2 fails, record it and continue from Step 3 by
calling the tool directly.

Open Microclaw on a real sample with structure, mark three or more positions
across a region where some tiles are in focus and some are not, and say something
close to the operator's own sentence — **naming no tool and no parameter**:

> scan these positions with the current focus, but when you see a tile with more
> signal, use it to focus and look again

PASS requires the agent, unprompted, to: write or select a hook that returns
`RequestAutofocus`, call `run_adaptive_survey` with an `autofocus_budget`, and
say what the budget costs in exposures before running it.

This criterion is unanswerable without the block: before 43i, `RequestAutofocus`
was refused by every runner, and the only honest answer to that sentence was the
four-round-trip workaround F5 describes.

Record the exact sentence you used and what the agent did.

## Step 3 — mechanism: the refocus fires, converges, and is judged again

On a tile with real structure that is **out of focus** — the F5 signature is
material present but not sharp, the `ridge_coverage 0.17–0.30 / snr 1.4–2.2` band
— run the survey and read the hook log.

PASS requires all of:

- a `RequestAutofocus` record with `decision: accepted` and reason
  `refocused and re-queued this tile`, carrying an `autofocus` block with
  `converged: true` and `entry_z_um` / `final_z_um` that differ;
- the same tile appearing a second time in the log, with the hook's own
  measurements from the focused frame;
- the survey then advancing to the next planned tile.

The operator's framing of this criterion: on a raster containing tiles like
frames 20 and 17 of `mt_search_561/mt_raster_1`, the survey should spend a
refocus there and report whether it converged.

### 3b — the refocus must survive at the *last* tile

This is round 2's defect and the one thing round 3 must show. Arrange a survey
where the **final planned tile** is the one that gets refocused — the simplest
version is a plan whose last position is the out-of-focus one, since the earlier
tiles will not request a sweep.

PASS requires a `refocus=1` frame **for that last tile** in the dataset, and a
second-look observation for it in the hook log. Round 2 produced the accepted
`RequestAutofocus` record with no frame behind it, so **check the dataset, not
the log** — the log looked correct while the frame was missing.

## Which code path Steps 4 and 7 measure — read this first

**Round 3 measured non-convergence and the focus lock on the wrong path, because
this runbook did not say which path it meant. That was the runbook's fault.**

`run_autofocus` — the standalone tool — has its own focus-lock check, its own
guard check and its own flat-curve refusal. All of that is pre-existing code
(design/28 F1, design/36) and **none of it is what block 43i added.** Round 3
exercised it thoroughly and correctly: two calls returned
`converged: false, moved: false` with `Coarse focus metric is flat (contrast 0.07
< 0.15) … Z was NOT moved`, which is real evidence that the sweep and its restore
work.

What 43i added is a **second, separate** implementation of those checks inside
`UntrustedHookAdapter._dispatch`, reached only when a **survey hook proposes
`RequestAutofocus`**. It has its own lock check, its own guard check, and its own
non-convergence handling — including the parts nothing else has: *do not
re-queue, do not retry, and let the survey carry on*. Every criterion below must
therefore appear **in a survey's hook log**, not in a `run_autofocus` reply.

The quick test of whether evidence counts: **it is in a `survey_log*.json`, and
the record has `"action": {"kind": "RequestAutofocus"}`.** A tool reply is not
evidence for these steps, and an agent *saying* it cannot sweep under a focus
lock is not evidence for anything — in round 3 the lock refusal was predicted in
prose and never produced by the code.

### The gate hook — save this once, use it for Steps 4 and 7

Round 3's `relative_signal_refocus` only asks for a refocus when it likes the
signal, which makes it a poor instrument for the negative limbs: on a defocused
field it may simply not ask. This one always asks, and always routes.

Prompt:

> Save a hook called `gate_always_refocus` with exactly this source, then show me
> `list_hooks` to confirm it registered:
>
> ```python
> from microclaw.hook_decisions import ContinueSurvey, HookResult, RequestAutofocus
>
> class GateAlwaysRefocus:
>     """Ask for a refocus at every first look, and always route the survey."""
>
>     def analyze_frame(self, image, metadata):
>         if metadata.get("microclaw_refocused"):
>             return HookResult({"pass": "second_look"},
>                               actions=(ContinueSurvey(),))
>         return HookResult({"pass": "first_look"},
>                           actions=(RequestAutofocus(), ContinueSurvey()))
> ```

The trailing `ContinueSurvey` is the pattern `hook_docs` now prescribes and is
what keeps these runs alive: when the refocus is refused or does not converge,
nothing is re-queued, and without it the survey idles out `max_idle_s` and dies —
which is exactly what killed round 3's first run.

## Step 4 — the negative limb: a sweep that does not converge

**Never exercised on the survey path in three rounds.** It needs no special
sample and about two minutes.

Actions:

1. Mark two or three positions as usual.
2. **Defocus by ~10 µm**: *"move Z by +10 µm"* — so the true focal plane is far
   outside the window about to be swept.
3. Prompt:

   > Run an adaptive survey over those positions with the `gate_always_refocus`
   > hook, protocol timelapse, 1 frame, and
   > `autofocus_budget = {"max_exposures": 24, "z_range_um": 2, "z_step_um": 0.5,
   > "method": "coarse_then_fine", "settle_ms": 50}`. Then show me the hook log.

That window costs **7 planes** per refocus, so 24 exposures authorize two full
refocuses at 8 each — **the budget cannot be what refuses.** Round 3's budget of
8 against a 20 µm window costing 21 is why its first run refused on budget
instead; do not repeat that pairing here.

PASS requires, in the survey's hook log:

- a `RequestAutofocus` record with `"decision": "accepted"` and reason
  **`autofocus ran and did not converge; Z restored`**;
- `entry_z_um` equal to `final_z_um` inside its `autofocus` block;
- **no second look** for that tile — no `refocus=1` frame, no `after_refocus`
  observation;
- **no widening and no retry** — exactly one `RequestAutofocus` record per tile;
- and the survey **carrying on** to the next planned tile.

The `reason` inside the `autofocus` block says which refusal fired — a flat curve
(`curve_contrast` under `MIN_CONTRAST`) or a peak pinned at the sweep boundary
(design/28 F1). **Either is a PASS**; record which one you got.

If the sweep converges anyway, the defocus was not far enough for a 2 µm window:
increase it to 20 µm and re-run. This limb is what makes the feature affordable —
convergence does not prove cells, but failure to converge disproves them — and it
is the only part of the sweep contract three rounds of green have never touched.

A field of bare glass — `scan300_488_r12_c15` is the reference case — is a more
faithful version if you ever have one, but it is not required and never was.

## Step 5 — both looks survive in the saved dataset

**Round 1 FAILED this step, and it is the reason there is a round 2.** Both looks
were stored and individually readable — the engine does accept the extra axis —
but the axis was **ragged**: first looks carried no `refocus` key at all, so
`dataset.axes["refocus"]` read `[1]` and every reader that enumerates the
Cartesian product of the axes generated only `refocus=1` cells. Measured against
round 1's own dataset: 4 real frames in, 3 combos out, **1 real frame and 2
zeros** — all three first looks silently dropped by a TIFF export. `161d680`
stamps `refocus=0` on the plan so the axis is dense.

Run this against the dataset from Step 3, substituting its path:

    uv run python -c "from ndstorage import Dataset; d=Dataset(r'<dataset>'); print({k: sorted(v) for k,v in d.axes.items()}); print(len(d.index), 'frames')"

PASS requires **`refocus` reporting `[0, 1]`**, and the frame count matching the
tiles acquired plus the refocused re-exposures. `[1]` alone is the round-1 defect
unfixed. Then confirm the refocused position has two readable frames, at
`refocus=0` and `refocus=1`, with visibly different focus.

The hook log reports success either way — that is exactly why this step exists,
and why it is measured on the dataset rather than on the log.

Note for the report: a survey where one tile of many was refocused still leaves
genuinely absent cells (`refocus=1` at the tiles never refocused), which a TIFF
export zero-pads. That is the already-registered zero-padding finding, sized as
its own block, and it is not this block's failure. The difference that matters:
zero-padding an absent cell is expected; losing a frame that was acquired is not.

## Step 6 — the emitted script reproduces the refocus decision standalone

Run this after a survey that **did** refocus — Step 4's non-converging run does
not exercise the re-queue, so use one of Step 3's converging surveys, or simply
re-focus first and run the `gate_always_refocus` hook once with the 24-exposure
budget so a refocus is granted.

Prompt:

> Export this session as a standalone script.

Then **close Microclaw** — leave Micro-Manager and the bridge running, the script
needs the core — and run it by itself:

    uv run python <exported-script>.py > emitted-run-43i.txt 2>&1
    Get-Content emitted-run-43i.txt

A 0-byte `emitted-run-43i.txt` is not by itself a failure: the script prints
nothing on success, and round 1's evidence was its hook log and dataset. Judge it
on those, and on the file having been written at all.

PASS requires: zero `NOT EMITTED` in the artifact, **the script running to
completion** (round 2's died at line 1908 on an undecorated tool's refusal, after
the adaptive program had already run), no `microclaw` imports, and the standalone
run reaching the same refocus decision at the same tile. The script writes its
hook log beside itself with a collision suffix, so compare it against the live
one.

**On comparing the two runs.** A converged refocus deliberately adopts the new Z,
so the standalone script starts from wherever the live run left the stage. Round
2's live run moved Z by 1.5 µm at `pos_2` and the standalone found it already in
focus (`50.79184 → 50.79184`), which then changed what the hook decided at
`pos_3`. That is the sample and the stage, not this code. Compare **mechanism** —
sweep ran, re-queue happened, second look was judged, dense axis in the dataset —
and treat a decision that differs *with an explained Z difference* as a PASS,
recording the entry Z of each run.

The script's `configure_autofocus` line passes `focus_lock_check=None` under a
comment saying so: a standalone script has no generic focus-lock query.
**Disengage the focus lock before running it**, and confirm the comment is
present in the file.

## Step 7 — the refusals are visible

Three short surveys with the `gate_always_refocus` hook, two positions each. All
three criteria are records **in the survey's hook log**, on a
`"kind": "RequestAutofocus"` action — not tool replies. **`run_autofocus` cannot
answer any of these**, and neither can the agent saying what would happen.

**7a — no budget authorized.** Round 3 never ran this; it is the pre-43i
behaviour and it must still be intact.

> Run an adaptive survey over those two positions with the `gate_always_refocus`
> hook, protocol timelapse, 1 frame, and **no** `autofocus_budget`. Show me the
> hook log.

PASS: `"decision": "refused"`, reason **`unsupported-by-run_adaptive_survey`**,
and the survey still completes both tiles (the hook's `ContinueSurvey` carries
it).

**7b — focus lock engaged.** Round 3's agent *stated* this refusal and the code
never produced it; the lock was disengaged before every survey.

> Turn the focus lock **on**, then run the same survey again **with**
> `autofocus_budget = {"max_exposures": 24, "z_range_um": 2, "z_step_um": 0.5,
> "method": "coarse_then_fine", "settle_ms": 50}`. Show me the hook log.

PASS: `"decision": "refused"`, reason **`focus lock is engaged; autofocus sweep
refused`**, no Z motion, and the survey completing. Turn the lock back off
afterwards.

**7c — budget exhausted.** *Already PASSED in round 3* — 8 authorized exposures
against a 20 µm window costing 21 gave `authorized autofocus exposure budget
exhausted`. Do not re-run it.

**Optional 7d, if it comes free.** A budget whose sweep would leave the Z limits
refuses with `SafetyGuard refused autofocus sweep`. Round 3 hit the equivalent on
the *tool* path (`Z=-4.6 µm is below the minimum allowed`) with
`z_range_um: 60` near Z≈25 µm, so the same range in a survey budget should do it.

## Step 8 — the script opens as text, and the bridge survives it

Round 1's second defect: the agent offered to open the exported script, called
`open_artifact` on the `.py`, and ImageJ read `from ...` as an image header
(`not a TIFF file: header=b'from'`). The ZMQ bridge was left wedged and
Micro-Manager had to be restarted, mid-gate.

After Step 6, ask to see the exported script. PASS requires: it opens in this
machine's **text editor**, the payload's `via` names that mechanism rather than
`ij.IJ.open`, and **the bridge still works afterwards** — call any read-only tool
(`get_position`, say) and confirm it answers without a restart.

Then open an image artifact in the same session and confirm it still goes to
ImageJ. The fix must not have captured the tool's actual job.

## Reporting

For each step: PASS / FAIL / NOT EXERCISED, with the log file or hook-log excerpt
that shows it. Say which machine and which sample. If a step was not run, say
that rather than inferring it from another step — a self-confirming probe is not
evidence, and a step that measured nothing is more useful reported as such.
