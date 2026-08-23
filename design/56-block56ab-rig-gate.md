# Blocks 56a + 56b rig gate — the property probe, on the Nikon

**Rig:** Nikon Ti with PFS, 60× oil, Andor iXon. One trip gates both blocks.

**What this gates.** `run_autofocus` gains a `probe` that reads a device property
at each plane instead of the camera (56a), stops at the first in-range plane, and
takes an explicit Z window with no per-plane dwell (56b). Riding with them: the
focus-lock refusal now fires on non-EMU rigs, refusals no longer hand back
`best_z_um` as a focus estimate, and the agent is told to offer a hardware lock
before an image sweep.

**Duration:** 60–90 minutes. Step 7 is ~7 minutes of sweeping on its own and is
the measurement this gate exists for — do not skip it because Step 4 passed.

**Two facts that shape every step below.**

- **The focus plane moves between sessions.** 2026-08-22 evening: band
  2358.9–2387.9. 2026-08-23 morning: in-range at 2664.7, locked ~2671. So no step
  here hardcodes the band's position. Wide literal windows are used instead, and
  every command runs unedited.
- **`TIPFSStatus.Status` enumerates nothing** (`allowed_values: null`). That is
  expected: supplying `in_focus_values` is what declares the reading categorical.

---

## Step 0 — environment (run all of it)

```powershell
cd $HOME\Code\microclaw
git fetch origin
git checkout design56/probe
git pull
git merge-base --is-ancestor 938cadd HEAD
if ($LASTEXITCODE -eq 0) { Write-Output "IMPLEMENTATION PRESENT" } else { Write-Output "WRONG TREE - STOP" }
uv pip install -e .
uv run pytest -q > suite.txt 2>&1
Select-String -Path suite.txt -Pattern "passed|failed" | Select-Object -Last 1
(Select-String -Path suite.txt -Pattern "^FAILED" | Measure-Object).Count
```

**Required:** `IMPLEMENTATION PRESENT`, and the last line prints **`0`**. The
pass count should be near **1976 passed / 124 skipped on Windows** (2001/99 on
macOS — the totals match, only the platform skips differ). The criterion is
zero failures, not the count.

```powershell
Get-Content "$env:APPDATA\microclaw\safety_config.yaml" | Select-String -Pattern "z_min|z_max|z_"
```

**Required:** the configured Z range contains **1000 to 2800 µm**. Widen it in
that file first if not, and say so in the results.

Launch from this directory — the transcript is written to the server's working
directory, so Step 4's grep only finds it if you start it here:

```powershell
cd $HOME\Code\microclaw
uv run microclaw serve
```

**Known defect, and it will bite you.** An error that aborts a turn is shown in
the browser but is **not written to the transcript** — the JSONL keeps your
prompt and nothing else. On 2026-08-23 that forced the operator to retype a 400
by hand. **If any step errors, copy the message out of the browser immediately**;
it will not be in the log afterwards. Recorded as a finding, not fixed in this
block.

## Step 1 — the device's own vocabulary

> Call get_device_property_info for device TIPFSStatus property Status, and also
> read its current value.

**Record the exact strings.** Later steps use `"Within range of focus search"`
and `"Locked in focus"`. If this rig spells either differently, use the rig's
spelling everywhere below and say so.

**`Focusing` is deliberately NOT used as an in-focus value anywhere in this
runbook.** It appears for about a second whenever the lock is engaged, including
by a human, and with the early stop a transient would end the sweep at whatever
plane it coincided with. If a step below produces a suspiciously early stop,
check whether anything engaged the lock mid-sweep.

## Step 2 — is the hardware lock offered without being asked?

**Start a fresh microclaw session for this step** (exit and relaunch), so no
earlier context primes the answer. Then paste verbatim, and nothing else:

> I have a sample on this microscope. Can you find the focus?

**Required:** before proposing any image-based sweep, it calls
`get_focus_lock_state` (or otherwise identifies the PFS) and **proposes the
property probe first, unprompted**, saying why. On 2026-08-23 it opened with an
image sweep and the operator had to ask "can you use the Nikon PFS system on
here? Why did you not propose using this?"

Answer its questions normally and let it proceed to Step 3's territory, or exit
and relaunch — either is fine. **Record its first substantive message verbatim**;
this limb is scored on what it proposed, not on what it eventually did.

## Step 3 — the refusal that could not fire before (PFS armed)

**Precondition:** PFS locked. If it will not lock from the current Z, do Steps
4–6 first and come back.

> Confirm TIPFSStatus Status reads Locked in focus. Then run run_autofocus with
> z_range_um 20 and z_step_um 0.5 and no probe.

**Required:** it **refuses** and the error names the focus lock. Before this
block, `get_focus_lock_state` returned `engaged: null` here and the refusal never
fired.

> Disengage the focus lock with set_focus_lock enabled false, then run the same
> run_autofocus again.

**Required:** `set_focus_lock` **succeeds** — it must not say "No EMU
configuration" — and the second autofocus proceeds to a sweep.

## Step 4 — the headline: one call, explicit window, stops when found

With PFS **Off**. Paste verbatim:

> Call run_autofocus with z_min_um 2200, z_max_um 2800, z_step_um 5, method
> "sweep", and probe set to device TIPFSStatus, property Status,
> in_focus_values ["Within range of focus search", "Locked in focus"].

**Required, all five:**
1. **One** `run_autofocus` round — not a loop of them.
2. `converged: true`, `moved: true`.
3. `stopped_early: true`, and **`planes_read` is much less than
   `planes_planned` (121)**. This is the whole point of 56b: it must not read to
   the end of the window.
4. `exposures_spent: 0` and `property_dwell_ms: 0`.
5. `peak_interior` is **absent**, replaced by `stopping_rule`.

**Record `planes_read`, `planes_planned`, the final Z, and the wall-clock time
you observed for the call.** Then confirm the round count:

```powershell
$h = Get-ChildItem -Path .\*_microclaw_history.jsonl | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Output $h.FullName
(Select-String -Path $h.FullName -Pattern '"name":"run_autofocus"' | Measure-Object).Count
```

## Step 5 — read the axis independently

> Call get_stage_position for TIZDrive.

**Required:** agrees with the payload's final Z within ~0.5 µm. A mismatch is the
finding, not a rounding artifact.

## Step 6 — engage, and actually run both validation checks

> Set TIPFSStatus State to On.

**Required:** `Locked in focus` on the **first** attempt. Record the locked Z and
compare it against Step 4's final Z — they will differ, because the sweep stops
at the *first* in-range plane and the lock pulls to focus from there. **That
difference is a measurement to record, not a failure.**

Then, without being prompted further:

> Confirm this is the real focal plane.

**Required:** it does **both** checks — an image-based contrast check **and** an
XY jog of ~10 µm confirming the lock holds. On 2026-08-23 it flagged the
locked-too-high risk in its plan and then did neither until asked. If it does
only one, that is this limb failing; say which one it did.

Set State back to Off before Step 7.

## Step 7 — the dwell measurement (this is why the trip happens)

`PROPERTY_PROBE_MIN_DWELL_S` was 0.5 s, chosen off-rig against a simulated
sensor, and is now 0 on the argument that the PFS samples at 200 Hz and
`settle_stage_move` already parks the axis for ≥100 ms. **Nothing has ever
measured that on real hardware.**

Run these two calls back to back, and **time each one**:

> Call run_autofocus with z_min_um 2200, z_max_um 2800, z_step_um 5, method
> "sweep", and probe set to device TIPFSStatus, property Status, in_focus_values
> ["Within range of focus search", "Locked in focus"], stop_when_found false,
> dwell_ms 0.

> Call run_autofocus with z_min_um 2200, z_max_um 2800, z_step_um 5, method
> "sweep", and probe set to device TIPFSStatus, property Status, in_focus_values
> ["Within range of focus search", "Locked in focus"], stop_when_found false,
> dwell_ms 500.

> Call run_autofocus with z_min_um 2200, z_max_um 2800, z_step_um 5, method
> "sweep", and probe set to device TIPFSStatus, property Status, in_focus_values
> ["Within range of focus search", "Locked in focus"], stop_when_found false.

**This limb already ran on 2026-08-23 and produced its answer:** dwell 0 read
{2655, 2660} and refused, dwell 500 read {2650, 2655, 2660} and converged, at a
cost of ~30 s over 121 planes. The default now follows the stopping rule —
0 when stopping early, 500 ms when mapping the band — so **re-run it to confirm
the new defaults reproduce that**, not to decide the question again.

**Required:**
- The `dwell_ms: 0` call reports `property_dwell_ms: 0` and reproduces the
  narrower band, refusing as before.
- The `dwell_ms: 500` call reports `property_dwell_ms: 500` and the wider band,
  converging as before.
- **And a third call with `stop_when_found false` and NO `dwell_ms` key at all**
  must report `property_dwell_ms: 500` and match the second — that is the new
  default doing its job.

A band that now differs from 2026-08-23's in either direction is a finding;
record both `readings` arrays in full.

**Record both wall-clock times and both bands.**

## Step 8 — how wide is the capture band, really?

From Step 7's `dwell_ms: 500` payload, read the first and last in-range Z.

**Record the width.** Measured 29 µm on 2026-08-22. The `~10 µm` figure in the
system prompt is the PFS **offset** range, which is a different quantity — the
two must not be conflated when judging whether a step size is too coarse.

## Step 9 — an explicit window does not re-sweep

> Call run_autofocus with z_min_um 1000, z_max_um 1200, z_step_um 5, method
> "sweep", and probe set to device TIPFSStatus, property Status,
> in_focus_values ["Within range of focus search", "Locked in focus"].

**Required:** the payload's `z_positions` **starts at 1000 and ends at 1200**,
and **no plane below 1000 was read**. On 2026-08-23 the centred form re-swept
730 µm of already-cleared ground because the window had to be expressed as a
centre plus a half-width.

## Step 10 — a window that misses the band

The same call is also the missed-window case: 1000–1200 is far below any band
this rig has shown.

**Required:** it **refuses**, moves nothing, and the message contains **1000**
and **1200** and quotes the constant reading it saw. It must offer both causes —
a window that does not reach the band, or a sensor that cannot evaluate — and
say to widen the window before suspecting the hardware. Confirm with
`get_stage_position` on `TIZDrive` that Z is unchanged.

## Step 11 — a mistyped value fails before the stage moves

Paste **exactly**, including the wrong spelling. If the agent offers to correct
it, tell it to run the call as written.

> Call run_autofocus with z_min_um 2200, z_max_um 2800, z_step_um 5, method
> "sweep", and probe set to device TIPFSStatus, property Status,
> in_focus_values ["Within range of focus"].

**Required:** it sweeps and then refuses, and the refusal **lists the values it
actually observed** alongside the one it was looking for — that pairing is what
makes the typo visible on a device that enumerates nothing. Z unchanged.

## Step 12 — a blind sensor is not an absent band

Swing the PFS dichroic out, so `Status` reads `Dichroic mirror not inserted`.

> Call run_autofocus with z_min_um 2200, z_max_um 2800, z_step_um 5, method
> "sweep", and probe set to device TIPFSStatus, property Status,
> in_focus_values ["Within range of focus search", "Locked in focus"].

**Required:** the refusal quotes `Dichroic mirror not inserted` **verbatim**.
That quoted value is the only thing separating this from Step 10 — both are
constant-reading refusals offering the same two causes, because from inside the
sweep they are the same observation. Put the dichroic back afterwards.

## Step 13 — four refusals that cost no hardware time

Run all four; none may move Z.

> Call run_autofocus with z_min_um 2200, z_max_um 2800, z_step_um 5 and probe
> set to device TIPFSStatus, property Status, in_focus_values ["Locked in
> focus"], leaving method at its default.

**Required:** refused, naming `method='sweep'`.

> Call run_autofocus with z_min_um 2200, z_max_um 2800, z_step_um 5, method
> "sweep", region [40, 60, 200, 80], and probe set to device TIPFSStatus,
> property Status, in_focus_values ["Locked in focus"].

**Required:** refused, saying a region does not apply to a property probe.

> Call run_autofocus with z_range_um 200, z_min_um 2200, z_max_um 2800,
> z_step_um 5, method "sweep", and probe set to device TIPFSStatus, property
> Status, in_focus_values ["Locked in focus"].

**Required:** refused, saying to supply either `z_range_um` or the window, not both.

> Call run_autofocus with z_min_um 2800, z_max_um 2200, z_step_um 5, method
> "sweep", and probe set to device TIPFSStatus, property Status, in_focus_values
> ["Locked in focus"].

**Required:** refused, saying `z_min_um` must be less than `z_max_um`.

## Step 14 — the image path must not have changed

Move to a plane with real signal first (use the lock, then disengage it).

> Run run_autofocus with z_range_um 20, z_step_um 0.5, region [40, 60, 200, 80]
> and no probe.

**Required:** `min_contrast` about **1.21** for that region, and whatever it
concludes it reports `contrast`, `peak_interior` and `metric_curve` as before. If
it refuses, the refusal carries the sentence saying the reported `best_z_um` is
the argmax of a curve that failed its gate and is **not** a focus estimate to
move to. A different `min_contrast` for the same region on the same camera means
the refactor changed the old path.

## Step 15 — export, and run it standalone

**Warning: the exported script re-executes this session's successful calls,
including the sweeps and the Z moves.** PFS **Off**, sample safe to sweep again.

> Call export_session_script and tell me the path it wrote.

```powershell
$s = ".\routine.py"
uv run python -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read()); print('PARSES')" $s
Select-String -Path $s -Pattern "Within range of focus search"
Select-String -Path $s -Pattern "Locked in focus"
Select-String -Path $s -Pattern "property_probe\("
Select-String -Path $s -Pattern "stop_when_found"
Select-String -Path $s -Pattern "_autofocus_lo = 2200"
Select-String -Path $s -Pattern "NOT EMITTED"
```

**Required:** `PARSES`; the first five searches each print **at least one
match**; the last prints **nothing**. A `NOT EMITTED` match names an undecorated
tool — record which.

```powershell
uv run python .\routine.py > routine_out.txt 2>&1
Write-Output "exit code: $LASTEXITCODE"
Select-String -Path routine_out.txt -Pattern "AUTOFOCUS ENVELOPE" -Context 0,3
Select-String -Path routine_out.txt -Pattern "AUTOFOCUS OUTCOME" -Context 0,2
```

**Required:** exit code **0**; the envelope prints the window, the step, a
criterion reading `first TIPFSStatus.Status in-range plane`, the
`in_focus_values`, and `stopping_rule: first in-focus plane`; and the outcome's
measured final Z agrees with what the live Step 4 run reported.

---

## Results — fill this in and return it

| Step | Mechanism | Result | Evidence |
|---|---|---|---|
| 0 | branch pinned, suite green, bounds admit 1000–2800 | | failures = |
| 1 | device vocabulary | | exact strings: |
| 2 | lock offered unprompted | | first message: |
| 3 | armed-lock refusal; set_focus_lock works without EMU | | |
| 4 | one call, stops early | | read/planned = , final Z = , time = |
| 5 | independent TIZDrive read-back | | payload = , read-back = |
| 6 | locks first attempt; **both** validation checks run | | locked Z = , delta = |
| 7 | dwell 0 / 500 / defaulted: bands reproduce 2026-08-23 | | band@0 = , band@500 = , band@default = , dwell reported = |
| 8 | capture band width | | width = |
| 9 | window not re-swept | | z_positions[0] = |
| 10 | missed window refuses, names 1000/1200 | | |
| 11 | mistyped value: observed values listed | | |
| 12 | blind sensor quotes the dichroic value | | |
| 13 | four refusals, no motion | | |
| 14 | image path unchanged | | min_contrast = |
| 15 | export parses, greps hit, runs standalone | | exit = |

**Return the Step 4 and Step 7 payloads in full**, plus the history JSONL and
`routine_out.txt`. Steps 5, 7 and 8 are scored by comparing numbers that should
agree with each other, and that cannot be done from a verdict.
