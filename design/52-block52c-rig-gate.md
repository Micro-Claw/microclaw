# Block 52c rig gate — the adaptive refinement (M5)

Run this on **M5**, on branch `design52/block-52c`. Every command below is
literal. Where a step says "expect", that is the value to compare against, not a
criterion to interpret. Where a step gives a prompt in a block quote, paste it
**verbatim** — a step written as an outcome gets satisfied by a better route, and
52b lost a whole limb and ~371 um of TIRF-axis motion to exactly that.

This is design/52 §"TIRF acceptance gate" **limb 2**, on the rig and the axis the
design was written from.

## What this gate proves, and what it does not

It proves: one approval before the run; a saved hook choosing its **next**
named-stage target from `analyze_frame`; the parent applying that target in the
next event's pre-hardware callback, inside the approved envelope; the run ending
when the budget is exhausted rather than continuing; a proposal after the last
authorized event refusing instead of exposing; and an exported script that
carries the **rule**, runs standalone, and is free to choose different targets.

**It does not prove anything optical.** M5 has been run in TIRF, but this gate
does not check that the extremum the hook finds is a meaningful TIRF angle. What
is measured is the decision loop, the envelope, the indexing and the export.

**It does not prove a configured camera bound** (no `camera` section in the
reviewed config) and it does not touch illumination.

**Dose.** M5's camera triggers the lasers, so every frame is a dose. Frame counts
below are 6 + 3 exposures plus whatever the standalone script takes. Keep them as
written.

## Preconditions, from `52b-m5-precheck` (2026-08-17)

Do not re-derive these; if any is now false, stop and say so.

| fact | value |
|---|---|
| `Thorlabs ELL17/ELL20` | `StageDevice`, addressable by label, driver `Position (um)` 0–28000 |
| reviewed `named_stages` bound | **0–20000** — the policy bound, tighter than the driver |
| its recorded position | 18146 (read it again in Step 0b; it will have moved) |
| config groups | one, `System`. **No `Channel` group** |
| core assignments | camera `HamamatsuHam_DCAM`, focus `PIZStage`, XY `SmarAct 2D`, no core shutter |

> **The design's original 19639–21294 um sweep cannot be run here.** That is
> recorded history from the 2026-08-14 session and it lies **outside** the
> reviewed 0–20000 bound, so `check_named_stage` would refuse it during planning.
> The interval below is chosen inside the reviewed bound and around the axis's
> present position. Do not copy the historical numbers.

## Step 0 — check out, install, and pin

**M5 runs microclaw under `uv`.** Bare `python` on this machine is a miniconda
interpreter carrying neither microclaw nor pytest.

```powershell
cd $HOME\Documents\GitHub\microclaw
git fetch origin
git checkout design52/block-52c
git pull --ff-only
uv pip install -e .
if ($LASTEXITCODE -eq 0) { "INSTALL OK" } else { "INSTALL FAILED - stop here" }
```

Pin the implementation by ancestry, never by tip hash:

```powershell
git merge-base --is-ancestor 1852828 HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - gate covers the reviewed implementation" } else { "PIN FAILED - wrong branch or commit; stop" }
```

```powershell
uv run python -m pytest -q 2>&1 | Out-File -Encoding utf8 $HOME\Documents\52c-m5-suite.txt
Get-Content $HOME\Documents\52c-m5-suite.txt -Tail 3
```

**Collection is 1980** — that number proves the branch, and `passed + skipped`
must equal it. macOS runs this tree as 1881 passed / 99 skipped; Windows skips
more, so a lower passed count with a correspondingly higher skip count is
expected, not a failure. **`main` collects 1971**; a run reporting 1971 means the
rig is on the wrong branch and every later step is worthless.

## Step 0b — read the axis, then fix the interval

Start Microclaw and ask, verbatim:

> What is the current position of the stage labelled `Thorlabs ELL17/ELL20`?

Write the number down; call it **P**. Then choose the run interval as
**P − 600 … P + 600 um**, rounded to whole um, and **clamp it inside 0–20000**.
With the recorded P of 18146 that is `17546–18746`. Every target below is
expressed relative to P, so the rest of the runbook does not need editing.

If P is above 19400 or below 600, use `max(0, P−600) … min(20000, P+600)` and
record the interval you used.

## Step 1 — register an adaptive refinement hook

Verbatim:

> Save and register a hook named `tirf_refine` with `runner_contract="adaptive"`,
> for `run_adaptive_survey`. It must define `analyze_frame(image, metadata)` and:
>
> - compute a normalised Tenengrad focus metric on the frame and put it in the
>   `HookResult` measurements, together with the target it is proposing;
> - keep its own state across frames — a coarse pass of four targets spread
>   across the approved interval, then, once the coarse pass is done, **two
>   refinement targets it computes from the results**: bisect between the best
>   coarse point and its better neighbour, then bisect again in the winning half;
> - return `HookResult(measurements, (MoveNamedStage(next_target),
>   ContinueSurvey()))` on every frame while it still has a next target, with the
>   target as an absolute position in um;
> - take the interval bounds as `hook_params`, and derive every target from them
>   arithmetically. It must not contain a hardcoded list of absolute positions.
>
> It must not import microclaw hardware, must not try to move anything itself,
> and must not count frames or try to stop at a fixed number.

Expect the code shown to you for review before saving, then
`generate_and_save_hook`, then a manifest entry. **Read the code yourself** — the
human review is the gate, the lint is advisory. Two things to check while you
read: the refinement targets are computed from the measurements, and there is no
literal list of absolute um positions.

## Step 2 — the refinement run. This is the block's point.

Verbatim, substituting your interval from Step 0b:

> Run an adaptive survey with `run_adaptive_survey`: 6 positions, protocol
> `timelapse` with `n_frames: 1` and `interval_s: 0`, no channel, 20 ms exposure,
> saving to `D:\SSD\52c_m5_refine`. Use the `tirf_refine` hook, passing it the
> interval bounds as hook params. Approve a `named_stage_envelope` for
> `Thorlabs ELL17/ELL20` over `<low>` to `<high>` um with 6 writes and
> `restore: "entry"`. Do not pass a `hook_action_plan` — the hook chooses each
> target after seeing the previous frame.

For the 6 positions, ask it to use six XY points about 10 um apart around the
current field, all inside the XY bounds. The tiles are only there to give the
decision loop six events; the axis under test is the ELL.

**6 writes, not 5, and this is not a typo.** The seed frame carries no move
(targets are chosen *after* a frame is analysed), so frames 2–6 take five writes,
and a non-`leave` restoration reserves one more. If the agent proposes 5, the
planning refusal it meets is correct behaviour — have it ask for 6.

**No channel argument.** M5 has only a `System` group; a channel is refused by
`_check_acquisition_channel`.

**If the run refuses naming a hardware-sequenced batch**, raise `interval_s` to 1
and record the interval that first produced single-event callbacks.

PASS requires all of:

- **exactly one** confirmation before the acquisition, reading `ALLOW HOOK
  HARDWARE CONTROL FOR THIS RUN` and naming the device, the interval, 6 writes
  and `restore 'entry'`. **None** from the hook thread during the run;
- one NDTiff dataset containing **6** frames;
- the axis really moves between frames — five accepted moves in the log.

A second confirmation mid-run is a FAIL; capture it verbatim.

## Step 3 — score the log, not the narration

Verbatim:

> Read the hook log for that run.

```powershell
Get-Content <log_path from the report> | Out-File -Encoding utf8 $HOME\Documents\52c-m5-hooklog.txt
Select-String -Path $HOME\Documents\52c-m5-hooklog.txt -Pattern '"decision": "accepted"' | Measure-Object | ForEach-Object { "accepted records: " + $_.Count }
Select-String -Path $HOME\Documents\52c-m5-hooklog.txt -Pattern '"restoration": true' | Measure-Object | ForEach-Object { "restoration records: " + $_.Count }
Select-String -Path $HOME\Documents\52c-m5-hooklog.txt -Pattern 'handoff is closed' | ForEach-Object { $_.Line }
```

Expect, and copy the numbers into the results:

- **five accepted `MoveNamedStage` records**, each carrying `device`,
  `requested_um`, `achieved_um` and `error_um`, plus **`hook_event_index`**
  running 1..5 — the seed is index 0 and carries no move;
- **exactly 1** restoration record, **last in the whole log**, with
  `"restoration": true` and `requested` equal to P (the entry position);
- six analysis observations, one per frame, each with the hook's metric;
- **the last two targets are not on the coarse ladder.** Write out all five
  requested positions. The first four should be evenly spread across the
  interval; the last one or two must sit *between* coarse points, and must be
  values the hook computed. If every target is on the even ladder, the refinement
  never happened and that is a FAIL;
- **one refusal at the end**, naming *"adaptive handoff is closed after the final
  authorized event"*, followed by an `aborted` record. **This is expected and is
  a PASS**: on the sixth frame the hook proposes a seventh target, there is no
  seventh authorized event, and the proposal is refused instead of exposing.
  What would be a FAIL is a seventh frame in the dataset, or that move appearing
  as accepted.

`requested_um` and `achieved_um` will **not** be equal — the ELL is coarse and
the design's own example achieved 21299 from a requested 21294. Record the
errors; a nonzero error is not a finding, a *missing* `achieved_um` is.

Then:

> What is the current position of the stage labelled `Thorlabs ELL17/ELL20`?

Expect P, and expect `named_stage_restoration` in the run report to read
`policy: "entry"`, `restored: true`. **Compare the report's `last_known_um`
against the log's final achieved position before restoration** — that
disagreement is the only tell 52a's third gate gave for a restoration firing
mid-run.

## Step 4 — the budget stops the run

Verbatim:

> Run that again with only 3 positions, saving to `D:\SSD\52c_m5_budget`, and
> approve a `named_stage_envelope` over the same interval with **2** writes and
> `restore: "leave"`.

Two writes cover the moves for frames 2 and 3, so nothing here should refuse —
this run is the control that the budget arithmetic is not accidentally short. Now
the limb itself:

> Run it once more with 4 positions, saving to `D:\SSD\52c_m5_budget2`, and
> approve the same envelope with **2** writes and `restore: "leave"`.

Expect the fourth frame's move to be refused for **budget**, the acquisition to
stop there, and the result to name the partial dataset path and the frames
exposed. Expect **3** frames on disk, not 4.

```powershell
Get-ChildItem D:\SSD -Filter "52c_m5_budget*" | Select-Object Name, LastWriteTime
```

The refusal record must say the write budget is exhausted. A run that quietly
exposes a fourth frame at the previous position is the FAIL this limb exists to
catch — the frame would be labelled with a target the axis never reached.

## Step 5 — export, and run the script standalone

Do this **in the same Microclaw session as Step 2**. `export_session_script`
compiles the calls recorded in *this* session; a fresh session emits a 13-line
stub with nothing in it.

Verbatim:

> Export this session as a standalone script called `tirf_refine_run.py`.

```powershell
$script = "<path the tool reported>"
Select-String -Path $script -Pattern "NOT EMITTED", "import microclaw" | ForEach-Object { $_.Line }
"--- expect no lines above this one ---"
Select-String -Path $script -Pattern "_NAMED_STAGE_ENVELOPE", "configure_named_stage", "_survey_event_stream", "class .*:" | Measure-Object | ForEach-Object { "rule markers found: " + $_.Count }
Select-String -Path $script -Pattern "hook_action_plan", "_axes_plan" | ForEach-Object { $_.Line }
"--- expect no lines above this one either ---"
uv run python -c "import ast,sys; ast.parse(open(sys.argv[1], encoding='utf-8').read()); print('SCRIPT PARSES')" $script
```

Expect: no `NOT EMITTED`, no `import microclaw`, no `hook_action_plan` and no
`_axes_plan` (this run had no fixed plan), the hook's class source and
`_survey_event_stream` present, and `SCRIPT PARSES`.

> **Do not grep for `raise RuntimeError`.** It matches inlined library source —
> the adapter's own refusals — and makes a clean export look dirty. 52a's runbook
> made that mistake and misreported a passing run.

**Now the criterion that is specific to this block: the script must carry the
rule, not the trace.** Take the five requested positions you wrote down in
Step 3 and grep for each of them:

```powershell
Select-String -Path $script -Pattern "<t2>", "<t3>", "<t4>", "<t5>", "<t6>" | ForEach-Object { $_.Line }
"--- expect no lines above this one ---"
```

**Any of this run's chosen targets appearing as a literal in the script is a
FAIL** (design/52 §"TIRF acceptance gate" limb 2). The envelope bounds *will*
appear, and so will whatever arithmetic constants the hook's own source contains
— those are the rule. Absolute positions the hook computed at runtime are the
trace.

> **If `export_session_script` refuses, Step 5 is a FAIL — stop and report the
> refusal.** Do not run these greps against a hand-written substitute. On
> 2026-08-17 the export refused, Microclaw correctly said so and hand-wrote a
> file, and the greps were then run on *that* file: it "passed" while proving
> nothing about the exporter.

Then run it, with Microclaw **closed** (Micro-Manager and the bridge stay up):

```powershell
uv run python $script 2>&1 | Out-File -Encoding utf8 $HOME\Documents\52c-m5-standalone.txt
if ($LASTEXITCODE -eq 0) { "STANDALONE EXIT OK" } else { "STANDALONE EXIT NONZERO" }
Get-Content $HOME\Documents\52c-m5-standalone.txt -Tail 30
```

Expect the script to print `HOOK HARDWARE CONTROL FOR THIS RUN`, the device, the
**interval**, the write budget and the restoration policy; then run six tiles,
restore, and print a dataset location. **It does not prompt** (operator decision
2026-08-17). If you are prompted, the branch is stale.

**The standalone run may choose different targets from the live run, and that is
a PASS** — it is the same rule meeting a slightly different specimen, exactly as
43h's M5 round 3 established. Read its hook log and record its five targets
beside the live ones. What must match is the *shape*: five accepted moves,
indices 1..5, a closure refusal at the end, and the restoration last.

## What to send back

Copy to `~\Documents\Documents - Beyonce\Projects\Micro-Claw\52c-m5`:

- `52c-m5-suite.txt`, `52c-m5-hooklog.txt`, `52c-m5-standalone.txt`;
- the exported `tirf_refine_run.py` and the hook source from Step 1;
- the hook logs from Step 4's two runs;
- the run reports for Steps 2, 4 and 5, with any refusal text verbatim;
- P, the interval you used, and the interval that first produced single-event
  callbacks if `interval_s: 0` was not enough.

**Score from the artifacts, not from "it worked."** Three pairs of numbers that
must agree, and are the likeliest tell of a defect: the report's `last_known_um`
against the log's final achieved position before restoration; the accepted-move
count against `frames − 1`; and the log's `hook_event_index` sequence against the
frame count. A passing gate is a place to look for defects, not a reason to stop
looking.
