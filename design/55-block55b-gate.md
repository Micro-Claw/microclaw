# Block 55b gate — a fixed plan stands alone

**Part A: the demo machine**, stock `MMConfig_demo.cfg` plus the `Aux Z` DStage
block 4c's setup added. **Part B: M2 or M5**, whichever is free — one sweep, a
few minutes.

Implementation ancestor: 067a6b2

PowerShell throughout. `uv` is the single launcher. **Every command block runs
unedited.** Nothing in it is a placeholder to substitute — 52c's strictest
criterion produced no rig evidence because it shipped as
`Select-String -Pattern "<t2>", "<t3>"` and was run verbatim. A step that prints
nothing where a match is required has **failed**, not passed.

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
measured `2181 passed / 99 skipped / 3 warnings` at the pin; this machine reports
a different split with the same 2280 total. **Gate on zero failures, never the
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

**Required:** `FRAMES DIFFER`, exit 0. **`ALL FRAMES IDENTICAL` is this gate's
headline failure** — it is the Nikon result, three exposures at one position
reported as a sweep, and no hook log can rule it out about itself.

Report the three means whatever they are. Nobody has characterised this camera's
response to `Aux Z`; 55a saw 858.705 / 327.285 / 327.174 over three frames and
that is an observation, not a curve.

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

Verbatim:

> Run a multiposition acquisition over the positions in the list, using
> `protocol_params` that carry a `hook_action_plan`.

**Required:** refused, naming `hook_action_plan`, **and the stage has not moved**.
`_run_protocol_at` moves XY before it calls either acquisition tool, so this
refusal has to happen before the position loop starts. Confirm with:

> What are the current stage coordinates?

Compare against what they were before the call.

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

**Close Microclaw.** Then:

```powershell
Set-Location "C:\Users\Public\microclaw-gates\55b"
uv run python session.py > "$Evidence\standalone.txt" 2>&1
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
