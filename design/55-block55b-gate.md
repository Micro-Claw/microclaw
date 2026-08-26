# Block 55b gate — a fixed plan stands alone

**Part A: the demo machine**, stock `MMConfig_demo.cfg` plus the `Aux Z` DStage
block 4c's setup added. **Part B: M2 or M5**, whichever is free — one sweep, a
few minutes.

Implementation ancestor: 7d75c24

PowerShell throughout. `uv` is the single launcher. **Every command block runs
unedited.** Nothing in it is a placeholder to substitute — 52c's strictest
criterion produced no rig evidence because it shipped as
`Select-String -Pattern "<t2>", "<t3>"` and was run verbatim. A step that prints
nothing where a match is required has **failed**, not passed.

## Round 1 result — 2026-08-26, demo machine (`block55b-2026-08-26`)

**Steps 0, 1 and 2 PASS. Step 3 FAILED on a real defect, now fixed. Steps 4–12
were never reached.**

- **Step 0**: `2156 passed / 124 skipped / 3 warnings` = 2280, exactly macOS's
  `2181 + 99`. Zero failures — and blind to the defect below.
- **Step 1**: `PRECONDITION PASS`.
- **Step 2**: `Aux Z` parked at `20.0`, `within_tolerance: true`.
- **Step 3's reach criterion PASSED and is the block's whole point.** From a
  sentence naming no tool, no hook and no argument, the session called
  `run_timelapse` with a `hook_action_plan` and **no `hook_strategy`** on its
  first attempt, and **wrote no hook**. 55a's session needed five refusals and a
  `PassthroughFrameLogger` it had to author. That comparison is the result this
  gate existed to produce, and it holds.
- **Step 3's run then died on the first frame**, twice, on different intervals
  and envelopes: `hook_failure: 'object' object has no attribute
  'image_process_fn'`, `frames_exposed: 0`, empty dataset.

**The cause was a false premise in design/55 itself**, not in the
implementation. §55b claimed `UntrustedHookAdapter` "already tolerates a payload
with no `analyze_frame`" because `image_process_fn` guards on `hasattr`. That
`hasattr` **selects between two branches** and does not no-op: the `else` branch
is the *legacy* `image_process_fn` path, which `PLAN_ONLY = object()` cannot
serve. The doc's supporting fact — that `UntrustedHookAdapter(object())` is
already a fixture — is true and irrelevant, because that fixture never drives a
frame. Corrected in `design/55` §"Decision → 55b" and fixed in
`hook_decisions.py` with a third branch that passes the frame through.

**Why a green suite could not see it: every fake drove `pre_hardware_hook_fn`
and never `image_process_fn`** — the live test's `DrivingAcquisition` and the
export test's `FakeAcquisition` both. They encoded the premise the design
asserted, so neither could contradict it. Both fakes were corrected **before**
the code, and with the code fix reverted they reproduce the rig's exact
`AttributeError` — including inside the exec'd exported script, which carried the
same defect.

**The failure path behaved correctly and was deliberately left alone**:
restoration fired to entry after the crash, `get_stage_position` confirmed
`20.0`, the result reported `frames_exposed: 0`, and its hint said not to treat
the run as untouched — which the session obeyed, reading the log instead of
retrying blind.

### Round 2 scope

**Run the whole of Part A again.** Steps 0–2 are cheap and Step 0 proves the rig
is on the fixed branch. Nothing from Step 4 onward has ever run, so there is no
partial credit to preserve. Part B has not been attempted.

## Round 2 result — 2026-08-26, demo machine (`block55b-2026-08-26-demo-round2`)

**The mechanism works end to end on hardware. Two steps produced no evidence,
and both are this runbook's fault, not the code's. Round 3 re-runs only those.**

- **Steps 0, 1, 2, 4, 6, 7, 10, 12 PASS.** Suite `2156 / 124` = 2280, equal to
  macOS's `2181 + 99`. `Aux Z` parked and read `20.0` before and after every one
  of the seven runs and three refusals in the session.
- **Step 3 PASS on the criterion the block exists for.** From a sentence naming
  no tool, the session called `run_timelapse` with a `hook_action_plan` and **no
  `hook_strategy`**, and **`generate_and_save_hook` appears nowhere in the
  session.** Against 55a, which needed five refusals *and* a hand-written
  do-nothing hook, that is the dead end removed. It still took four calls:
  `interval_s=0`, then `max_writes: 3` when the plan needs plan-length-plus-one,
  then a restore value of 20 outside a `40..140` envelope. Three refusals to
  author one sweep — recorded for the register, not fixed here.
- **Step 4 PASS with the three-way agreement**: log `40 / 90 / 140` then
  restoration to `20.0`, `named_stage_restoration.last_known_um` `20.0`, and a
  separate `get_stage_position` `20.0`.
- **Step 6 PASS**: three runs to one `save_dir` under one `name` left
  `auxz_sweep_plan_log.jsonl`, `_2` and `_3`, four records each, none
  interleaved.
- **Step 7 PASS**: out-of-bounds, budget-one-short and declined-authorization all
  refused, axis unmoved after each.
- **Step 9 PARTIAL**: 55a's corrected message fired verbatim. Its second half —
  the agent's *next* call naming a saved hook — was not observed; the session
  moved on.
- **Step 11 PASS for this block, then died on a defect that is not this
  block's.** All four plan-only sweeps re-executed standalone with Microclaw
  closed, each printing its envelope and dataset path and writing its own log.
  The script then raised `NOT EMITTED: run_multiposition_acquisition —
  observation-only hooked timelapse has positions without recorded Z`, **which
  exists on `main`** — block 9a's gate hit the identical shape with
  `run_tile_acquisition` and the operator ruled it a register item. It only
  entered this session because Step 8 improvised a multiposition run.

**One observation that looks like a defect and is not.** The standalone run wrote
`auxz_sweep_plan_log_2_2.jsonl`. The four live runs came from three different
directories and their recorded basenames were `.jsonl`, `.jsonl`, `_2`, `_3`; the
script collapses them into one directory, so the third one's *recorded* name
`_2` was already taken and correctly became `_2_2`. The collision rule composing
with itself is the right behaviour.

### The two steps that produced nothing, and why

**Step 8 tested nothing.** It asked for an outcome — "a multiposition acquisition
using `protocol_params` that carry a `hook_action_plan`" — the agent offered
better routes, a perfectly good `snr_observer` multiposition run happened
instead, and **the refusal being gated never fired.** Its XY criterion was void
too: that run legitimately moved the stage to `y=-600`. This is block 52b's
mandatory limb repeating in a runbook written after the lesson. Rewritten below
to name the call and to tell the agent that a refusal *is* the wanted result.

**Step 5 could not prove what it claimed.** Every sweep ran ascending
`40 / 90 / 140` and every one produced frame 0 unlike frames 1 and 2, which were
nearly equal. That shape fits *"frame 0 of a hooked run differs"* exactly as well
as *"the image responds to `Aux Z`"*, and the demo camera is a deterministic
simulator, so repeating the same sweep returns the same frames by construction
and separates nothing. **Step 5b, a reversed sweep, is the one run that
settles it**, and the honest outcome includes striking the step.

For the record, the means available across both sessions, which *suggest* a
monotonic response but come from an uncontrolled comparison — the first two
points are whole plain runs, the last three are frame positions inside hooked
runs:

| `Aux Z` | mean | source |
|---|---|---|
| 0 | 3276.219 | 55a plain timelapse |
| 20 | 1312.981 | round-2 plain timelapse |
| 40 | 858.705 | sweep frame 0 |
| 90 | 327.285 | sweep frame 1 |
| 140 | 327.174 | sweep frame 2 |

### Round 3 scope — three steps, about ten minutes

Run **Step 0** (proves the branch), then **Step 5 + 5b**, **Step 8**, and
**Step 9**. Everything else passed on artifacts that are already in hand.

## What this gate settles

55a made an unattached plan refuse. It also, by design, left the capability
reachable only by writing a hook that does nothing — and 55a's own gate showed
that happening: the session wrote `PassthroughFrameLogger`, *"records each
frame's mean intensity and proposes no actions ... the plan moves hardware, not
this hook."* 55b removes that dead end. So the first thing this gate measures is
whether the same request now succeeds **without a hook being written at all.**

Then the mechanism: three moves in event order, restoration after the `with`
block, an audit log with a collision-free default name, the envelope refusals
still intact, and an exported script that re-executes the program standalone.

**Part A carries one witness 55a's gate did not have.** `Aux Z` changes the demo
camera's image — measured from 55a's own datasets, where a plain timelapse gave
mean 3276.219 three times and the sweep gave 858.705 / 327.285 / 327.174. So the
pixels corroborate the log, and **three identical frames is this block failing**
no matter what the log says. That is the only check here a log cannot make about
itself.

**What Part A cannot settle, and Part B is for.** `Aux Z` is simulated and
arrives instantly, so nothing on the demo machine exercises `settle_stage_move`
inside a hook on a device that takes real time to move — `CLAUDE.md` §"A device
that is not busy is not a device that arrived".

**What no available machine settles.** The Nikon criterion was an *optimum* —
SNR rising and then falling, the proof a TIRF sweep found an angle. M2 has never
been run in TIRF mode, which is why block 52a declared its own sweep proved
nothing optical and passed anyway. Recorded as untested, not inferred.

---

# Part A — the demo machine

## Step 0 — check out, pin, install, run the suite

```powershell
$Repo = (git rev-parse --show-toplevel)
if (-not $Repo) { Write-Output "NOT IN A GIT CHECKOUT - STOP"; return }
Set-Location $Repo
$Evidence = "$HOME\Documents\microclaw-gates\block55b-$(Get-Date -Format yyyy-MM-dd)"
New-Item -ItemType Directory -Force $Evidence | Out-Null
Write-Output "REPO: $Repo"
Write-Output "EVIDENCE: $Evidence"
git fetch origin
git checkout design55/plan-stands-alone
git pull
git merge-base --is-ancestor 067a6b2 HEAD
if ($LASTEXITCODE -eq 0) { Write-Output "IMPLEMENTATION PRESENT" } else { Write-Output "WRONG TREE - STOP" }
uv pip install -e .
uv run pytest -q > "$Evidence\suite.txt" 2>&1
Select-String -Path "$Evidence\suite.txt" -Pattern "passed|failed" | Select-Object -Last 1
```

**Required:** `IMPLEMENTATION PRESENT`, and a last line with **0 failed**. macOS
measured `2181 passed / 99 skipped / 3 warnings` at the pin, and this machine
read `2156 / 124` in round 1 — the same 2280 total. **Gate on zero failures, never the
count.**

## Step 1 — the precondition, as a command that exits nonzero

Micro-Manager running, `MMConfig_demo.cfg` loaded, ZMQ server on port 4827.

```powershell
uv run python design\55-block55a-probe.py --check-only --device "Aux Z" --min 0 --max 200 > "$Evidence\55b-precondition.txt" 2>&1
Write-Output "precondition exit code (expected 0):" $LASTEXITCODE
Get-Content "$Evidence\55b-precondition.txt"
```

**Required:** `PRECONDITION PASS`. If `Aux Z` is missing, add it in the Hardware
Configuration Wizard as a `DemoCamera` `DStage` labelled exactly `Aux Z`, and do
not reassign Core focus to it. If the bound is not `0 .. 200`, fix
`named_stages` in the safety config. **Every number below is a literal computed
from that bound** — fix it here rather than adapting the steps.

## Step 2 — start the session and park the axis off zero

```powershell
Set-Location $Repo
uv run microclaw serve
```

Verbatim:

> Move the `Aux Z` stage to 20 um.

**Required:** it reports `20.0`. This is not decoration. 55a's gate ran with the
axis at 0 *and* a restore target of 0, so restoration was indistinguishable from
never having moved to any witness outside the log the code under test writes.
Parking at 20 fixes that for free: after Step 3 the axis must come back to **20**,
which is not a value the sweep visits.

## Step 3 — the reach limb, and the block's whole point

Verbatim. It names a sweep and a range, and no tool, no hook, and no argument:

> Sweep the `Aux Z` stage across three positions — 40, 90 and 140 um — taking one
> frame at each, and save the run to
> `C:\Users\Public\microclaw-gates\55b\sweep`. Put it back where it started
> afterwards.

**Required, and this is the comparison the block exists for:**

- The run succeeds and saves one dataset.
- **No new hook is written.** `generate_and_save_hook` must not appear in the
  session. 55a's gate needed one; if this gate needs one too, 55b has not landed.
  If the agent attaches an *existing* observation hook that is acceptable — note
  it — but writing one is a failure.
- One confirmation, naming `Aux Z`, an interval containing both `40..140` **and**
  the restore value, and a write budget of **4** (three moves plus the write
  reserved for restoration).
- The result carries `named_stage_restoration` and a `log_path`.

Record how many calls it took. 55a's session needed five refusals and a hook.

**If the agent does not reach the plan-only route**, run this instead and say so
— the mechanism must be exercised either way:

> Run that again as a 3-frame `run_timelapse` with `interval_s` 2, **no**
> `hook_strategy`, a `hook_action_plan` of three `MoveNamedStage` actions at 40,
> 90 and 140 um, and a `named_stage_envelope` for `Aux Z` with `min_um` 0,
> `max_um` 140, `max_writes` 4 and `restore` `"entry"`. Save it to
> `C:\Users\Public\microclaw-gates\55b\sweep2`.

## Step 4 — the log, and the three numbers that must agree

Verbatim:

> Read the hook log for that run, and then tell me the current position of
> `Aux Z`.

**Required:**

- Three accepted records with `hook_event_index` 0, 1, 2 and `achieved_um`
  **40 / 90 / 140** in that order, each with `within_tolerance: true`.
- A final record with `restoration: true`.
- **`named_stage_restoration.last_known_um`, the log's final `achieved_um`, and
  `get_stage_position` all read `20.0`.** Block 52a's third gate passed every
  stated limb and was caught only by those three disagreeing.

## Step 5 — the witness the log cannot supply

```powershell
uv run python design\55-frame-means.py "C:\Users\Public\microclaw-gates\55b\sweep\sweep_1" > "$Evidence\55b-frame-means.txt" 2>&1
Write-Output "frame-means exit code (expected 0 = frames differ):" $LASTEXITCODE
Get-Content "$Evidence\55b-frame-means.txt"
```

If the dataset directory is named differently, run
`Get-ChildItem "C:\Users\Public\microclaw-gates\55b" -Directory` and use what is
there — the suffix is pycro-manager's and this is the one path this runbook
cannot predict.

**Required:** `FRAMES DIFFER`, exit 0. `ALL FRAMES IDENTICAL` would be the Nikon
result — three exposures at one position reported as a sweep.

**But `FRAMES DIFFER` on its own does not prove the axis moved, and round 2 is
why this step now has a second half.** Every sweep so far ran the same ascending
targets, and every one produced the same shape: frame 0 unlike frames 1 and 2,
which are nearly equal. That is exactly as consistent with *"frame 0 of a hooked
run differs"* as with *"the image responds to `Aux Z`"*. The demo camera is a
deterministic simulator, so repeating the identical sweep cannot separate them —
it returns the identical frames by construction.

### Step 5b — the control that separates them

Verbatim:

> Run that same sweep once more with the three positions in the opposite order —
> 140, then 90, then 40 — saving to
> `C:\Users\Public\microclaw-gates\55b\reversed`.

```powershell
uv run python design\55-frame-means.py "C:\Users\Public\microclaw-gates\55b\reversed\auxz_reversed_1"
Write-Output "exit code:" $LASTEXITCODE
```

If the dataset directory is named differently, list the folder and use what is
there.

**This is the whole witness, and it has exactly two outcomes:**

- **The means come back in reversed order** — roughly the ascending run's third,
  second, first value. The image tracks the axis, Step 5 is a real independent
  check, and the sweep demonstrably moved hardware between exposures.
- **The means come back in the same order as the ascending run** — frame 0 high,
  frames 1 and 2 low. Then the frames vary with *position in the acquisition*,
  not with the stage, **Step 5 proves nothing, and it must be struck from this
  runbook rather than reported as a pass.** Say so plainly; a witness that
  cannot fail is not a witness.

Report the means from both runs either way.

## Step 6 — the default log name, and two runs that must not interleave

Verbatim:

> Run that exact same sweep twice more, to the same folder and with the same
> name, and do not give either one a log path.

**Required:**

```powershell
Get-ChildItem "C:\Users\Public\microclaw-gates\55b" -Recurse -Filter "*_plan_log*.jsonl" | Select-Object FullName, Length
```

**Two or more distinct log files**, one of them suffixed `_2`. A single log
containing both runs' records is a failure: two runs write two datasets, and
their audit trails must not merge.

## Step 7 — the envelope still refuses, on hardware

Verbatim, one at a time. Each must refuse, and **`Aux Z` must read `20.0` after
all three**:

> Sweep `Aux Z` to 40, 90 and 250 um the same way.

> Sweep `Aux Z` to 40, 90 and 140 um again, but with a write budget of exactly 3
> and restore to entry.

> Sweep `Aux Z` to 40, 90 and 140 um again — and when it asks me to authorize the
> envelope, I am going to decline.

**Required:** three refusals — out of bounds, the write reserved for restoration,
and a declined authorization that reports the run was **not started**. Then:

> What is the current position of `Aux Z`?

`20.0`. Read the position; do not take the status line for it.

## Step 8 — the refusal that has no route, before the first move

**Round 2 note: the step that stood here tested nothing, and this is its
replacement.** It asked for "a multiposition acquisition using `protocol_params`
that carry a `hook_action_plan`" — an *outcome*. The agent offered better routes,
the operator took one, a perfectly good `snr_observer` multiposition run
happened, and **the refusal being gated never fired**. That is block 52b's
mandatory limb repeating, and `CLAUDE.md` step 6 names it: *an outcome-shaped
step gets satisfied by the better route.* The step now names the call.

First, record where the stage is:

> What are the current stage coordinates?

Write down X and Y. Then, verbatim — and **do not accept an alternative route,
even a better one; this step exists to make one specific call fail**:

> Call `run_multiposition_acquisition` with exactly these arguments and do not
> substitute anything: `protocol` `"timelapse"`, the five positions in the list,
> `save_dir` `C:\Users\Public\microclaw-gates\55b\nested`, and
> `protocol_params` set to
> `{"n_frames": 1, "interval_s": 0, "hook_action_plan": [{"hook_event_index": 0,
> "actions": [{"kind": "MoveNamedStage", "position_um": 40}]}]}`.
> I am testing a refusal — if it refuses, that is the result I want, so report
> the message and stop rather than finding a way to make it run.

**Required:** an error naming `hook_action_plan`, and **X and Y unchanged from
the reading you took a moment ago**. `_run_protocol_at` moves XY before it calls
either acquisition tool, so this refusal has to land before the position loop
starts. Confirm with:

> What are the current stage coordinates?

**If a multiposition acquisition actually runs, the step has failed** — no
matter how sensible the run was.

## Step 9 — 55a's corrected message, in a session rather than a unit test

Verbatim:

> Run a plain 3-frame timelapse but authorize a `named_stage_envelope` for
> `Aux Z` over 0 to 140 um, with no action plan and no hook.

**Required:** refused, and **the agent's next call names a *saved* hook, not a
precoded one.** 55a's gate lost a round trip to a message that said only "pass
hook_strategy"; a message is fixed when the next reader takes the working route.

## Step 10 — export, and the literals

Verbatim:

> Also run a plain 3-frame timelapse at 10 ms with a 1 second interval saving to
> `C:\Users\Public\microclaw-gates\55b\plain`, then export this session as a
> standalone script to `C:\Users\Public\microclaw-gates\55b\session.py`.

```powershell
$Script = "C:\Users\Public\microclaw-gates\55b\session.py"
Copy-Item $Script "$Evidence\session.py"
uv run python -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read()); print('SCRIPT PARSES')" "$Script"
Write-Output "--- these three MUST print a match ---"
Select-String -Path $Script -Pattern "'position_um': 40"
Select-String -Path $Script -Pattern "'position_um': 90"
Select-String -Path $Script -Pattern "'position_um': 140"
Write-Output "--- these three MUST print NOTHING ---"
Select-String -Path $Script -Pattern "_log_path = None"
Select-String -Path $Script -Pattern "NOT EMITTED"
Select-String -Path $Script -Pattern "^import microclaw|^from microclaw"
```

**Required:** `SCRIPT PARSES`; the three target literals each match; the three
negative patterns each print nothing. `_log_path = None` is the specific defect
the design names — the tool defaults the log path, so it never appears in the
recorded *input*, and an emitter reading only the input loses the audit trail the
live run kept.

## Step 11 — the standalone run

**Close Microclaw.** Then — **stay in the repo.** `uv run` resolves only from the
microclaw checkout, so `Set-Location` to the evidence folder first would break
it. Give `uv run python` the absolute path instead; the emitted script resolves
its own `_HERE` and still writes beside itself, not beside the repo:

```powershell
Set-Location $Repo
uv run python "C:\Users\Public\microclaw-gates\55b\session.py" > "$Evidence\standalone.txt" 2>&1
Write-Output "standalone exit code (expected 0):" $LASTEXITCODE
Get-Content "$Evidence\standalone.txt"
Get-ChildItem "C:\Users\Public\microclaw-gates\55b" -Recurse -Filter "*_plan_log*.jsonl" | Select-Object FullName, Length
```

**Required:** exit 0; the printed envelope line naming `Aux Z`, the approved
interval, the write budget and the restore policy; **a new log file** whose three
accepted records read 40 / 90 / 140 and whose last record restores; and `Aux Z`
back at `20.0`. The script prints its envelope and does not prompt — that is
deliberate (operator decision 2026-08-17), and running it is the consent.

Then, in a fresh Microclaw session:

> What is the current position of `Aux Z`?

## Step 12 — the hookless run is untouched

```powershell
Select-String -Path "$Evidence\session.py" -Pattern "UntrustedHookAdapter" | Measure-Object | Select-Object Count
```

**Required:** a nonzero count — the sweep needs it. Then confirm the *plain*
timelapse from Step 10 emitted through the ordinary path: find its
`multi_d_acquisition_events(**{'num_time_points': 3, 'time_interval_s': 1` line.

```powershell
Select-String -Path "$Evidence\session.py" -Pattern "num_time_points"
```

**Required:** at least one match. A plain SMLM timelapse must not start emitting
an adaptive runner.

---

# Part B — M2 or M5, one sweep

The only thing Part A cannot show: a named stage that takes real time to arrive,
under a plan, between exposures.

**Pick one, and use the device and bound already reviewed for that machine:**

| machine | device | reviewed `named_stages` bound |
|---|---|---|
| M2 | `TIRF Stage` | `-10497.8 .. 6256.8` |
| M5 | `Thorlabs ELL17/ELL20` | `0 .. 20000` |

**Dose.** Both machines trigger their lasers from the camera, so every frame is a
dose. Three frames, at whatever exposure you would spend on a throwaway field.

Run Step 0 on that machine, then, in a session, verbatim — **substituting only
the device name and three positions from the table above, and writing them down
in the results**:

> What is the current position of the stage labelled `<device>`?

Call it **P**. Then:

> Sweep `<device>` across three positions spaced 100 um apart starting at P,
> taking one frame at each with a 2 second interval, and save the run. Put the
> axis back where it started afterwards.

**Required:**

- No hook is written — same criterion as Part A Step 3.
- The hook log's three accepted records carry `achieved_um` **within tolerance of
  each requested target**, with `measured_um` present and `within_tolerance:
  true`. On a real motor these will not be exact, and that is the point:
  `achieved_um` must be a measurement, never the requested value echoed back.
- Report `last_device_status` if it appears. On the Nikon a successful move read
  `busy` after 0.89 s and ~18 polls — the settle loop out-waiting a device that
  was still moving is exactly what Part A cannot exercise.
- The axis returns to P, confirmed by `get_stage_position`, not by the status
  line.

**If any target would fall outside the reviewed bound, stop and say so** rather
than widening it mid-run. Choose a smaller spacing and record what you used.

---

## Results to return

Attach the whole `$Evidence` directory and, for each step, **PASS**, **FAIL**
with the output, or **NOT RUN** with why. Three things to state explicitly
because no artifact captures them:

1. Whether Step 3 wrote a hook, and how many calls the sweep took.
2. The three frame means from Step 5, whatever they are.
3. For Part B, the device, the three targets, and the requested-vs-achieved
   differences.
