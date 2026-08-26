# Block 55a demo gate — an unattached hardware plan refuses

**Machine: the demo machine, with the stock `MMConfig_demo.cfg` and the `Aux Z`
DStage that block 4c's setup added.** No sample, no laser, no focus lock, no
booked rig time. Every case in this gate is expected to *refuse*, so nothing
moves and nothing is exposed except two frames in one deliberate control run.

Implementation ancestor: 6a38ece

PowerShell throughout. `uv` is the single launcher for every Python and project
command; do not substitute bare `python` for one line. **Every command block
below runs unedited.** Nothing in it is a placeholder you are meant to
substitute, and a step that prints nothing where a match is required has
**failed**, not passed.

## Round 1 result — 2026-08-26, demo machine (`block55a-2026-08-26`)

**GATE PASS, Steps 0–4, scored from the artifacts.**

- **Step 0**: `2129 passed / 124 skipped / 3 warnings` = 2253 collected, exactly
  macOS's `2154 + 99`. Zero failures. Match the total, not the split.
- **Step 1**: `PRECONDITION PASS`. `Aux Z` at 0.0 um, bounded 0–200.
- **Step 2**: `PROBE PASS`, exit 0. All nine shapes refused **for the message
  each was told to expect**, no dataset directory appeared for any of them, and
  both bracket reads were unchanged (position 0.0, exposure 10.0) though each
  refused case asked for a different exposure. The control acquisition ran and
  set the exposure it asked for.
- **Step 3 produced six refusals in a row and every one of them was correct**,
  which is more than the step asked for. In order: the `interval_s=0` sequencing
  refusal; the no-hook refusal; `Hook envelopes apply only to saved generated
  hooks` for a precoded `snr_observer`; **`hook_action_plan would consume the
  write reserved for restoration`** for `max_writes: 3`; `named-stage planned or
  restoration position is outside the envelope` for a restore target below the
  interval; then the run. The fourth is the one worth reading twice — design/55
  §Problem predicted it, saying the Nikon's line-89 call *"is itself one write
  short"* and that **the omission bug hid this second planning error too**.
  Unhiding it is 55a working exactly as designed.
- The sweep then ran for real: `40 / 90 / 140` with `achieved_um` equal to
  `requested_um` and `error_um: 0.0` on all three, plus the restoration write.
  `named_stage_restoration.last_known_um`, the log's final achieved value and a
  separate `get_stage_position` afterwards all read `0.0` — the three-way
  agreement `CLAUDE.md` step 6 asks for.
- **Step 4**: the exported script parses (2997 lines), zero `# NOT EMITTED`, no
  `microclaw` import, and **all six refusals appear as single-line `# SKIPPED`
  comments** — block 52b's newline-in-a-recorded-error defect did not recur over
  six chances. The hooked run emitted through the adaptive path with
  `PassthroughFrameLogger` inlined, the envelope and the literal targets
  `40 / 90 / 140` written into `hook_action_plan`, and `configure_named_stage`
  carrying the restore policy.

### Three findings, and the block's own argument confirmed

**1. The refusal named a fix that did not work — corrected on this branch.** Told
to `pass hook_strategy`, the session passed `snr_observer` and hit
`Hook envelopes apply only to saved generated hooks` one round trip later. The
message now says *"pass hook_strategy naming a saved generated hook. A precoded
hook cannot carry them."* Regression test
`test_no_hook_refusal_names_the_hook_kind_that_can_carry_the_plan`, watched
failing on the pre-fix message first. Suite `2155 + 99`.

**2. Step 3's z-stack prompt is not runnable as written, and that is the
runbook's defect, not the rig's.** It asks for a stack "over 2 um around where
the focus is now"; the focus sits at `0.0`, which is the demo config's `z_min`,
so `check_z` correctly refused `-1.0` and the operator had to choose a one-sided
stack. The guard behaved; the step did not. Ask for `0 -> 2 um` explicitly.

**3. `Aux Z` changes the demo camera's image, and this runbook's own premise said
it could not.** Measured from the two datasets: the plain 10 ms timelapse gives
mean **3276.219** on all three frames, **bit-identical**. The sweep gives
**858.705 / 327.285 / 327.174** at 40 / 90 / 140 um — and frames 1 and 2 are
identical to their last significant figure (min 70, max 584 on both) while frame
0 is not. Nothing but the planned axis varied between those frames, so **the
demo camera is not blind to `Aux Z`**. This matters for 55b, not for 55a; see
design/55's checklist, where the claim it refutes has been corrected.

**What the session did unprompted is 55b's argument, made by an agent that had
never read design/55.** Finding no hook that fits, it wrote one whose entire
purpose is to exist: `PassthroughFrameLogger`, *"records each frame's mean
intensity and proposes no actions ... the plan moves hardware, not this hook."*
design/55 §55b calls that a usability defect in advance — *"requiring the caller
to write, register and hash-pin a hook that does nothing, purely to carry a plan
that analyses nothing"*. It cost this session a hook, five refusals and a
manifest entry to sweep three positions.

## What this gate settles, and what it cannot

It settles, on real hardware and a real bridge:

- A `hook_action_plan` or hardware envelope passed with **no `hook_strategy`** is
  refused instead of silently discarded, in both `run_timelapse` and `run_zstack`.
- The refusal lands **before the camera is reconfigured** — the "One guard,
  moved" claim, checked against `get_exposure()` on the rig rather than against
  a fake.
- A per-frame plan with `interval_s=0` is refused **at planning time**, before
  anything mutates and before an `Acquisition` is constructed.
- An ordinary run is unchanged: the preamble reorder is in the path every
  acquisition walks.

It cannot settle anything optical, and it does not need to. 55a moves no
hardware by design; the block that gives the capability back is 55b, and its own
gate carries the motion.

**Why Step 2 is a probe and not a typed prompt.** The call this block refuses is
one no agent will compose on purpose. Ask an agent for a per-frame sweep and it
correctly reaches for a hook, the refusal never fires, and the step "passes"
having tested nothing — which is exactly what happened to block 52b's mandatory
limb, written as an outcome rather than as the mechanism. Step 3 still asks the
agent for something, but only for the one refusal an ordinary request really can
reach.

## Step 0 — check out, pin, install, and run the suite

Open PowerShell **in your microclaw checkout** — the repo is not assumed to be
under `$HOME` (on this machine it is `D:\Code\microclaw`), so it is derived:

```powershell
$Repo = (git rev-parse --show-toplevel)
if (-not $Repo) { Write-Output "NOT IN A GIT CHECKOUT - STOP"; return }
Set-Location $Repo
$Evidence = "$HOME\Documents\microclaw-gates\block55a-$(Get-Date -Format yyyy-MM-dd)"
New-Item -ItemType Directory -Force $Evidence | Out-Null
Write-Output "REPO: $Repo"
Write-Output "EVIDENCE: $Evidence"
git fetch origin
git checkout design55/unattached-plan-refuses
git pull
git merge-base --is-ancestor 6a38ece HEAD
if ($LASTEXITCODE -eq 0) { Write-Output "IMPLEMENTATION PRESENT" } else { Write-Output "WRONG TREE - STOP" }
uv pip install -e .
uv run pytest -q > "$Evidence\suite.txt" 2>&1
Select-String -Path "$Evidence\suite.txt" -Pattern "passed|failed" | Select-Object -Last 1
```

**Required:** `IMPLEMENTATION PRESENT`, and a last line with **0 failed**. macOS
measured 2154 passed / 99 skipped / 3 warnings at the pin. This machine reports a different
passed/skipped split with the same total — **match the total, gate on zero
failures, never on the count.**

## Step 1 — the precondition, as a command that exits nonzero

Micro-Manager must be running with `MMConfig_demo.cfg` loaded and the ZMQ server
on (Tools → Options → "Run server on port 4827").

```powershell
uv run python design\55-block55a-probe.py --check-only --device "Aux Z" --min 0 --max 200 > "$Evidence\55a-precondition.txt" 2>&1
Write-Output "precondition exit code (expected 0):" $LASTEXITCODE
Get-Content "$Evidence\55a-precondition.txt"
```

**Required:** `PRECONDITION PASS` and exit code `0`.

If it prints `no stage labelled 'Aux Z'`, this machine's `MMConfig_demo.cfg` has
lost the second DStage that block 4c added. Add it back in Micro-Manager's
Hardware Configuration Wizard — device `DStage` from `DemoCamera`, **labelled
exactly `Aux Z`** — save the config, reload it, and re-run this step. Do not
reassign Core focus to it.

If it prints `the configured bound is not 0 .. 200`, edit `named_stages` in your
safety config so the `Aux Z` entry reads `min_um: 0.0`, `max_um: 200.0`, then
re-run. **Every number in the rest of this runbook is a literal computed from
that bound**, so fix it here rather than adapting the steps below.

## Step 2 — the nine refused shapes and the control

This is the block. It issues each refused call against the same
`MicroscopeController` and `SafetyGuard` a session builds, brackets every one of
them with a position read and an exposure read, and finishes with one ordinary
two-frame timelapse that must still run.

```powershell
uv run python design\55-block55a-probe.py --device "Aux Z" --min 0 --max 200 --save-root "$Evidence\probe" > "$Evidence\55a-probe.txt" 2>&1
Write-Output "probe exit code (expected 0):" $LASTEXITCODE
Get-Content "$Evidence\55a-probe.txt"
```

**Required, all of them:**

- Exit code `0` and a final `PROBE PASS`.
- Case **A** refuses naming `hardware-sequence the time axis`.
- Cases **B** through **I** refuse naming `pass hook_strategy`.
- `dataset dir created: False` on every one of A–I.
- `position after the refused cases : ... (unchanged)`.
- `exposure after the refused cases : ... (unchanged)` — this is the "One guard,
  moved" limb, and it is the reason each refused case asks for a *different*
  exposure. If this line says `CHANGED`, the guard is sitting after
  `core.set_exposure` and the block has not landed.
- No `a refused call reached CONFIRM_FN` block.
- Case **J**, the control, runs, writes a dataset, and leaves the exposure at the
  value it asked for. A guard that refuses everything is also a regression.

Any `^^ FAIL` line is a gate failure. Paste the whole file into the results.

## Step 3 — the one refusal an ordinary request can reach

Start a session and use these words verbatim. This is the reach limb: it names
the shape (a burst, per-frame motion) and no tool, and it is the only refusal in
this block that an agent hits by asking for something reasonable.

```powershell
Set-Location $Repo
uv run microclaw serve
```

**Prompt 1, verbatim.** It names the mechanism — `interval_s` of 0 and a
per-frame plan — rather than an outcome, because an outcome-shaped step gets
satisfied by the better route and the refusal never fires (block 52b's mandatory
limb, which asked for a stage position and correctly got the named-stage tool):

> Run a 3-frame timelapse with `interval_s` set to 0 — I want the frames back to
> back with no gap — and move the `Aux Z` stage to a different position for each
> of the three frames using a per-frame hook action plan, somewhere between 40
> and 140 um. Attach whatever hook you need.

**Required:** the run is refused, and the refusal names **a nonzero `interval_s`**
as the fix. Quote the agent's own sentence: whether it explains the refusal or
merely repeats it is half of what the message is for.

**Prompt 2, verbatim:**

> Fine — use a 2 second interval instead.

**Two outcomes are correct here and one is the failure.** Say which happened.

- The agent attaches a hook and the sweep **actually runs**. That is design/52's
  shipped capability and it is a pass. Ask for the hook log and check it carries
  three achieved positions; `Aux Z` will have moved.
- The agent says it cannot do this without a hook, or declines to write one.
  Also a pass, and it is the dead end 55b exists to remove. `Aux Z` is unchanged.
- **A reported successful sweep with `Aux Z` unchanged and no hook log is this
  gate's headline failure.** That is the Nikon defect reproduced, and it is the
  one result that must not appear. Read the position, do not take the status
  line for it:

```powershell
Write-Output "ask the agent: what is the current position of Aux Z"
```

Then, in the same session, verbatim:

> Run a plain 3-frame timelapse at 10 ms with a 1 second interval, saving to
> `C:\Users\Public\microclaw-gates\55a-plain`, and then a 3-plane z-stack over
> 2 um around where the focus is now, saving to the same folder.

**Required:** both run and save. This is the ordinary path through the reordered
preamble, driven by the agent rather than by the probe.

Finally, verbatim:

> Export this session as a standalone script to
> `C:\Users\Public\microclaw-gates\55a-plain\session.py`.

Then leave the session running and open a second PowerShell window for Step 4.

## Step 4 — the exported script

```powershell
$Script = "C:\Users\Public\microclaw-gates\55a-plain\session.py"
Copy-Item $Script "$Evidence\session.py"
uv run python -c "import ast,sys; ast.parse(open(sys.argv[1]).read()); print('SCRIPT PARSES')" "$Script"
Select-String -Path $Script -Pattern "NOT EMITTED"
Select-String -Path $Script -Pattern "^import microclaw|^from microclaw"
Write-Output "--- if the two Select-String commands above printed nothing, that is the pass ---"
```

**Required:** `SCRIPT PARSES`, and **both** `Select-String` commands print
nothing. `export_session_script` compiles *this session's* recorded calls, so a
fresh session emits a stub — this is why Step 3's two ordinary runs come first.

A refused call records an error, and **a recorded error containing a newline used
to break out of its `# SKIPPED` comment and take the whole export down**
(block 52b). Step 3's refusals are single-line `ValueError`s, so this step is a
check that the export survives a session that contains refusals at all, not a
test of that specific defect.

## Results to return

Attach the whole `$Evidence` directory, and say for each step: **PASS**, **FAIL**
with the output, or **NOT RUN** with why. For Step 3 quote the agent's own words
on prompts 1 and 2 — the message being *actionable* is half of what the refusal
is for.
