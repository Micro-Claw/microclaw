# Block 56a rig gate — the property probe, on the Nikon

**Rig:** Nikon Ti with PFS, 60×/1.49 NA oil, Andor iXon. Use the **same
DNA-PAINT sample as 2026-08-22** if it still exists — several limbs compare
against numbers measured that day.

**What this gates.** `run_autofocus` gains a `probe` that reads a device
property at each plane instead of the camera, so the PFS capture band can be
found in one call at zero exposures. It also gates two generic fixes that ride
with it: refusals no longer hand back `best_z_um` as if it were a focus
estimate, and `get_focus_lock_state` / `set_focus_lock` now work through
Micro-Manager's own autofocus device instead of an EMU map.

**Rough duration:** 60–90 minutes. Steps 3 and 6 are ~5 minutes *each* of
sweeping — the tool is not hung, it is dwelling 0.5 s per plane.

---

## Step 0 — environment (run this first, all of it)

```powershell
cd $HOME\Code\microclaw
git fetch origin
git checkout design56/probe
git pull
git merge-base --is-ancestor 79068b0 HEAD
if ($LASTEXITCODE -eq 0) { Write-Output "IMPLEMENTATION PRESENT" } else { Write-Output "WRONG TREE - STOP" }
pip install -e .
python -m pytest -q > suite.txt 2>&1
Select-String -Path suite.txt -Pattern "passed|failed" | Select-Object -Last 1
Select-String -Path suite.txt -Pattern "^FAILED" | Measure-Object | Select-Object -ExpandProperty Count
```

**Required:** `IMPLEMENTATION PRESENT`, and the last line prints **`0`**.
The pass count should be near 1987; the criterion is **zero failures**, not the
count. Any nonzero failure count stops the gate — `main` was green when this
branch was cut, so a failure here is real.

Then confirm the Z bound actually admits the window Step 3 sweeps. No tool
reports the active bounds (known gap), so read the file:

```powershell
Get-Content "$env:APPDATA\microclaw\safety_config.yaml" | Select-String -Pattern "z_min|z_max|z_"
```

**Required:** the configured Z range contains **2240 to 2400 µm**. If it does
not, widen it in that file before continuing and say so in the results — the
sweep is guard-checked at both ends and will refuse otherwise.

Launch microclaw from this directory so the history JSONL lands here:

```powershell
cd $HOME\Code\microclaw
microclaw
```

## Step 1 — the device's own value list

Paste verbatim:

> Call get_device_property_info for device TIPFSStatus property Status and show
> me the allowed values exactly as the device reports them.

**Record the exact strings.** Every later step uses
`"Within range of focus search"` and `"Locked in focus"`. If this rig spells
either differently, **use the rig's spelling everywhere below and note the
change in the results** — a value the device never reports is refused before any
Z move, which is Step 9's mechanism, not a failure here.

## Step 2 — the refusal that could not fire before (PFS armed)

This limb tests the **generic focus-lock read**, not the EMU map. It is the one
change a Nikon user notices without asking for it.

**Precondition:** PFS must actually be locked for this to test anything. Get it
locked by whatever means you normally use. **If it will not lock from the
current Z, do Steps 3–5 first and come back here** — Step 5 leaves it locked.

Paste verbatim:

> Confirm TIPFSStatus Status reads Locked in focus. Then run run_autofocus with
> z_range_um 20 and z_step_um 0.5 and no probe.

**Required:** the autofocus **refuses**, and the error names the focus lock.
On 2026-08-22 this same call ran a sweep against an armed servo, because
`get_focus_lock_state` returned `engaged: null` on this rig and `null` is falsy.

Then:

> Disengage the focus lock with set_focus_lock enabled false, then run the same
> run_autofocus again.

**Required:** `set_focus_lock` **succeeds** (it must not say "No EMU
configuration"), and the second autofocus proceeds to a sweep rather than
refusing on the lock. Whatever that sweep concludes is Step 12's business.

## Step 3 — the headline: one call, 161 planes, zero exposures

The 2026-08-22 session spent 16 tool calls over 8 planes and found nothing.

> Move the Z stage to 2320. Then call run_autofocus with z_range_um 160,
> z_step_um 1, method "sweep", and probe set to device TIPFSStatus, property
> Status, in_focus_values ["Within range of focus search", "Locked in focus"].

This sweeps **2240 to 2400 µm**, which brackets the ~2373 lock point measured on
2026-08-22 with ~22 µm of margin on each side. (The design document's own limb
said `z_range_um=120` from 2300, which sweeps 2240–2360 and **misses the band** —
do not use it.)

**Required, all four:**
1. **One** `run_autofocus` round. Not a loop of them.
2. `converged: true`, `moved: true`.
3. The payload's plane→reading table has **161 rows** (`coarse.readings` and
   `coarse.in_range`), and the in-range run **brackets ~2373** with `out` planes
   on both sides.
4. `exposures_spent: 0`, and `property_dwell_ms` is reported.

**Save the whole payload into the results.** Then confirm the round count from
the transcript rather than from memory:

```powershell
$h = Get-ChildItem -Path .\*_microclaw_history.jsonl | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Output $h.FullName
(Select-String -Path $h.FullName -Pattern '"name":"run_autofocus"' | Measure-Object).Count
```

Record that count. It counts every `run_autofocus` in the session so far, so
note the value after Step 2 and after Step 3; **Step 3 must add exactly one.**

## Step 4 — read the axis independently

> Call get_stage_position for TIZDrive.

**Required:** it agrees with the chosen Z in Step 3's payload, within ~0.5 µm.
Block 52a's third gate passed every stated limb and was caught only by this kind
of disagreement, so a mismatch here is the finding, not a rounding artifact.

## Step 5 — the band centre is somewhere the lock can actually engage

> Set TIPFSStatus State to On.

**Required:** `Locked in focus` on the **first** attempt, with no hunting and no
intermediate Z move. Record the locked Z from `get_stage_position` on `TIZDrive`
and compare it against Step 3's chosen Z — the design predicts they differ,
because the band centre is the centre of the capture range and not the plane of
best focus. **Record the difference; it is a measurement, not a failure.**

Then set State back to Off before Step 6.

## Step 6 — is 0.5 s per plane actually enough for this PFS?

**This limb exists because `PROPERTY_PROBE_MIN_DWELL_S = 0.5` was chosen off-rig
and nothing has ever measured it against real PFS hardware.** Off-rig, a sensor
lagging longer than the dwell smears the band and the refusal blames the sample.

> Move the Z stage to 2320. Then call run_autofocus with z_range_um 160,
> z_step_um 1, method "sweep", settle_ms 1500, and probe set to device
> TIPFSStatus, property Status, in_focus_values ["Within range of focus search",
> "Locked in focus"].

**Required:** `property_dwell_ms` reports **1500**, and the in-range band is the
**same band as Step 3**, within one plane at each edge.

- Same band → the 0.5 s floor is sufficient on this rig. **This is the result
  that validates the constant.**
- **Different band → the 0.5 s floor is too short and the block is not done.**
  Record both tables in full; that is the evidence for raising it.

## Step 7 — the 5 µm step, shown to be too coarse

The 2026-08-22 hand search stepped 5 µm through a capture range of ~10 µm.

> Move the Z stage to 2320. Then call run_autofocus with z_range_um 160,
> z_step_um 5, method "sweep", and probe set to device TIPFSStatus, property
> Status, in_focus_values ["Within range of focus search", "Locked in focus"].

**Required:** it **refuses** with the "not a band" message (too few in-range
planes to be a band), and **moves nothing**. Confirm with `get_stage_position`
on `TIZDrive` that Z is back at 2320. Expect wording close to "Only 2 of 33
planes read in-range".

**If it instead converges, that is not a code failure — record it.** The refusal
fires below three in-range planes, and a 5 µm step lands 2 planes inside the
~10 µm band measured on 2026-08-22. Three or more means this rig's capture range
is wider than that, which is a measurement worth having. Record the in-range
planes either way.

## Step 8 — the window the session actually searched

> Move the Z stage to 2300. Then call run_autofocus with z_range_um 60,
> z_step_um 1, method "sweep", and probe set to device TIPFSStatus, property
> Status, in_focus_values ["Within range of focus search", "Locked in focus"].

**Required:** it **refuses** naming the window it excluded — the message must
contain the numbers **2270** and **2330** — and moves nothing. This is the
result the 2026-08-22 session was owed and never got: "not here" as a finding
rather than a search that trails off.

## Step 9 — a mistyped value must fail before the stage moves

Paste this **exactly**, including the wrong spelling. Do not let the agent
correct it; if it offers to, tell it to run the call as written.

> Call run_autofocus with z_range_um 20, z_step_um 1, method "sweep", and probe
> set to device TIPFSStatus, property Status, in_focus_values ["Within range of
> focus"].

**Required:** an error naming the value the device never reports and listing the
ones it does, **and zero Z motion**. Confirm with `get_stage_position` on
`TIZDrive` immediately before and after — the two must be identical.

## Step 10 — a blind sensor is not an absent band

Have the operator **swing the PFS dichroic out** (the physical control), so
`TIPFSStatus.Status` reads `Dichroic mirror not inserted`.

> Move the Z stage to 2320. Then call run_autofocus with z_range_um 40,
> z_step_um 1, method "sweep", and probe set to device TIPFSStatus, property
> Status, in_focus_values ["Within range of focus search", "Locked in focus"].

**Required:** the refusal cites the **constant reading** and quotes
`Dichroic mirror not inserted`. It must **not** say the focus is outside the
window — that message would send a session hunting a focus problem that is
really a turret problem.

Put the dichroic back before continuing.

## Step 11 — two refusals that cost no hardware time

> Call run_autofocus with z_range_um 40, z_step_um 1 and probe set to device
> TIPFSStatus, property Status, in_focus_values ["Locked in focus"], leaving
> method at its default.

**Required:** refused, and the message says a property probe requires
`method='sweep'`. (Without this the default runs a 5 µm coarse pass — Step 7's
failure, silently.)

> Call run_autofocus with z_range_um 40, z_step_um 1, method "sweep", region
> [40, 60, 200, 80], and probe set to device TIPFSStatus, property Status,
> in_focus_values ["Locked in focus"].

**Required:** refused, saying a region does not apply to a property probe.
Neither call may move Z.

## Step 12 — the image path must not have changed

> Move the Z stage to 2347. Then run run_autofocus with z_range_um 20,
> z_step_um 0.5, region [40, 60, 200, 80] and no probe.

**Required:** the same shape of refusal this rig produced on 2026-08-22 — a
`min_contrast` of about **1.21** for that region, `converged: false`,
`moved: false` — **plus** a new sentence saying the reported `best_z_um` is the
argmax of a curve that failed its gate and is not a focus estimate to move to.

Compare `contrast` and `min_contrast` against the 2026-08-22 rows (0.226 /
0.918 / 0.682 against 1.21). A **different `min_contrast` for the same region on
the same camera** means the refactor changed the old path, and that is this
block failing.

## Step 13 — export, and run it standalone

**Warning: the exported script re-executes this session's successful calls,
including the Step 3 sweep and the Z moves.** Make sure the sample and objective
are safe to sweep again, and that PFS is **Off**, before running it.

> Call export_session_script and tell me the path it wrote.

```powershell
$s = ".\routine.py"
python -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read()); print('PARSES')" $s
Select-String -Path $s -Pattern "Within range of focus search"
Select-String -Path $s -Pattern "Locked in focus"
Select-String -Path $s -Pattern "property_probe\("
Select-String -Path $s -Pattern "PROPERTY_PROBE_MIN_DWELL_S"
Select-String -Path $s -Pattern "NOT EMITTED"
```

**Required:** `PARSES`; the first four searches each print **at least one
match**; the last prints **nothing**. If `NOT EMITTED` matches, an undecorated
tool got into the session — record which one, it is a register row.

Then actually run it:

```powershell
python .\routine.py > routine_out.txt 2>&1
Write-Output "exit code: $LASTEXITCODE"
Select-String -Path routine_out.txt -Pattern "AUTOFOCUS ENVELOPE" -Context 0,3
Select-String -Path routine_out.txt -Pattern "AUTOFOCUS OUTCOME" -Context 0,2
```

**Required:** exit code **0**; the envelope prints the window, the step, a
criterion reading `centre of TIPFSStatus.Status in-range band`, and the
`in_focus_values` list; and the outcome's measured final Z agrees with what the
live Step 3 run reported.

---

## Results — fill this in and return it

| Step | Mechanism | Result | Evidence |
|---|---|---|---|
| 0 | branch pinned, suite green, Z bound admits 2240–2400 | | failures = |
| 1 | device's own allowed values | | exact strings: |
| 2 | armed-lock refusal via generic autofocus device | | |
| 2 | `set_focus_lock` works without EMU | | |
| 3 | one call finds the band | | rows = , band = , exposures = |
| 4 | independent `TIZDrive` read-back | | payload Z = , read-back = |
| 5 | lock engages at the band centre, first attempt | | locked Z = , delta = |
| 6 | **0.5 s dwell floor is sufficient on this rig** | | band@500 = , band@1500 = |
| 7 | 5 µm step refuses as "not a band" | | |
| 8 | 60 µm window refuses naming 2270/2330 | | |
| 9 | mistyped value refuses before any motion | | Z before = , after = |
| 10 | blind sensor refuses as constant reading | | |
| 11 | `coarse_then_fine` refused; region+probe refused | | |
| 12 | image path unchanged + new sentence | | contrast = , min_contrast = |
| 13 | export parses, greps hit, runs standalone | | exit = |

**Return the Step 3 and Step 6 payloads in full**, plus the history JSONL and
`routine_out.txt`. Several criteria above are scored by comparing numbers that
should agree with each other, and that cannot be done from a verdict.
