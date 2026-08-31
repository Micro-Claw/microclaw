# Block 65c — M2 adaptive-timelapse gate

M2's camera triggers its lasers, so **live view is dose**. Keep live view off
except for the operator's already-approved sample setup. A full STORM sample is
not required; a low-value test field is enough.

The computed program connects to no microscope and exposes nothing. The later
steps are operator-judged because only the real engine can establish callback
shape, cadence, property read-back, restoration, and the absence of an exposure
after an accepted stop.

`NOT EXERCISED` is never a pass. Preserve it verbatim in the report.

## 0 — branch pin and computed gate

Open PowerShell in the existing Microclaw checkout. These commands contain no
substitutions or placeholders:

```powershell
Set-Location (git rev-parse --show-toplevel)
git checkout design65/adaptive-timelapse-route
git merge-base --is-ancestor 912f5e3 HEAD
if ($LASTEXITCODE -ne 0) { throw "STOP: 912f5e3 is not an ancestor; this is not the accepted implementation" }
uv run python design/65-block65c-gate.py --out gate65c-m2-computed > gate65c-m2-computed.log 2>&1
$GateExit = $LASTEXITCODE
Get-Content gate65c-m2-computed.log
if ($GateExit -ne 0) { throw "STOP: a computed limb failed or was not exercised" }
```

Expect `6/6 PASS`. Send `gate65c-m2-computed`, including the emitted adaptive
script, with the session evidence. The script owns the log; PowerShell's
`Start-Transcript` is not evidence for native stdout.

## 1 — registry inspection, before any exposure

Start Micro-Manager with M2's ordinary config and ZMQ server, but leave live
view off. Start Microclaw with M2's already reviewed safety configuration. Do
not edit the production safety configuration for this gate.

Give the agent this prompt exactly:

> Before any snap, live view, or acquisition, list the saved-hook registry on
> M2. Inspect every pinned source for the exact retired identifiers
> `ContinueSurvey` and `StopSurvey`, including bare calls with no import. Report
> hook name, manifest hash and which identifier occurs. Do not resolve or run a
> retired hook, do not rewrite it, and do not expose the camera.

Save the response and registry result as `gate65c-registry.txt`. Any retired
source is an inventory finding, not permission to migrate it during this gate.

## Evidence table used for every driven run

For every run below, save the tool result, hook log, dataset path/axes, and its
fresh export. Fill one row per frame:

| time axis | callback received (`dict` or `list`, and list length) | requested property | achieved read-back | decision | exposure observed |
|---:|---|---|---|---|---|

Also record `stop_reason`, property entry value, restoration result and final
read-back. The pass comparison is:

1. hook-log frame identity equals the dense dataset `time` axes;
2. each accepted write's requested and achieved values agree;
3. the export contains the successor rule and predicate, not the run's chosen
   values;
4. there is no dataset axis or hook record after the frame whose
   `StopAcquisition` was accepted.

Compilation or a status sentence alone is not evidence.

## 2 — successor and early stop, no hardware capability

Use **50 ms exposure**, `interval_s=0`, `max_frames=8`, no channel, and a saved
hook named `gate65c_density_stop3`. It logs frame index and image density,
returns exactly one `ContinueAcquisition` for time axes 0, 1 and 2, and exactly
one `StopAcquisition` for time axis 3. Grant no illumination, property,
named-stage or artifact capability.

Give the agent this prompt exactly:

> Create and save `gate65c_density_stop3` with `analyze_frame(image, metadata)`.
> It must log `metadata["Axes"]["time"]` and an image-derived density, return
> `HookResult(measurements, (ContinueAcquisition(),))` only while time is less
> than 3, and otherwise return
> `HookResult(measurements, (StopAcquisition(),))`. Run one
> `run_timelapse` with `n_frames=null`, `max_frames=8`, `interval_s=0`,
> `exposure_ms=50`, dataset name `gate65c_density_stop3`, and no hardware or
> artifact envelope. Export this recorded call to
> `gate65c_density_stop3_export.py`. Do not start live view and do not use a
> fixed `n_frames` route.

PASS requires axes 0, 1, 2, 3 exactly, `stop_reason: hook_stop`, and no exposure
after time 3. A run reaching eight frames failed the early-stop mechanism.

After the export exists, make the engine-shape observation copy. The first
command only inserts a recorder immediately around the emitted pre-hardware
callback and compiles the result; it does not connect or acquire. The second
command deliberately reproduces this same four-frame, 50 ms run once on M2:

```powershell
uv run python design/65-block65c-gate.py --instrument-export gate65c_density_stop3_export.py --instrumented-out gate65c_density_stop3_instrumented.py
uv run python gate65c_density_stop3_instrumented.py > gate65c-density-instrumented.log 2>&1
$InstrumentedExit = $LASTEXITCODE
Get-Content gate65c-density-instrumented.log
if ($InstrumentedExit -ne 0) { throw "The instrumented exported run failed; callback shape was not established" }
Get-Content gate65c_callback_shapes.jsonl
```

Do not run that script until the operator has approved the four-frame
reproduction. It writes `gate65c_callback_shapes.jsonl` itself, so no transcript
assumption is involved.

## 3 — image-derived non-dosing property, if M2 admits it

First inspect the authorization map and Core inventory for the literal pair
`SmaractXY.Hold time (ms)`. Use values `10` and `11` only if the property exists,
is writable, is declared non-dosing, and both values are inside its reviewed
bounds. It changes controller hold time, not position. If any precondition is
false, record this limb `NOT EXERCISED`; do not substitute a laser, camera
exposure, stage position, TTL, PWM, servo, or another convenient property.

Give the agent this prompt exactly:

> Inspect `SmaractXY.Hold time (ms)` without writing it. If and only if the
> reviewed M2 authorization admits values `10` and `11`, save
> `gate65c_holdtime_image` whose image-derived predicate proposes
> `SetDeviceProperty("11")` on even-density frames and
> `SetDeviceProperty("10")` on odd-density frames, beside exactly one routing
> decision. Run `run_timelapse` with `n_frames=null`, `max_frames=4`,
> `interval_s=0`, `exposure_ms=50`, a property envelope for device
> `SmaractXY`, property `Hold time (ms)`, allowed values `["10", "11"]`,
> `max_writes=4`, and restore `entry`. Stop at time 3. Export the recorded call
> to `gate65c_holdtime_image_export.py`. If the exact pair or authorization is
> absent, make no write and report `NOT EXERCISED`.

PASS requires each proposed value to be applied, waited for and read back before
the next exposure; the entry value must be restored after the acquisition
context on success. Retain the exception-path restoration result if this run
happens to fail; do not induce a fault solely to obtain it.

## 4 — authorized FPGA-duration run

This limb uses the exact dose-bearing pair `Laser Trigger.Duration0 (us)` and
literal values `1` and `2` microseconds. Run it only if M2's already reviewed
authorization admits that envelope and the operator approves the resulting
four-frame dose. Do not enable a laser, change `Mode0` or `Sequence0`, or edit a
safety config to make the limb runnable. Otherwise record `NOT EXERCISED`.

Give the agent this prompt exactly:

> Read `Laser Trigger.Duration0 (us)` and record its entry value. If the
> reviewed authorization admits values `1` and `2`, save
> `gate65c_duration_stop3`. For time axes 0, 1 and 2 it must propose
> `SetDeviceProperty(str(1 + (time % 2)))` beside exactly one
> `ContinueAcquisition`; at time 3 it must return exactly one
> `StopAcquisition` and no property action. Run `run_timelapse` with
> `n_frames=null`, `max_frames=8`, `interval_s=0`, `exposure_ms=50`, dataset
> name `gate65c_duration_stop3`, and a property envelope for device
> `Laser Trigger`, property `Duration0 (us)`, allowed values `["1", "2"]`,
> `max_writes=3`, restore `entry`. Export this recorded call to
> `gate65c_duration_stop3_export.py`. Do not change `Mode0`, `Sequence0`, any
> laser enable, or the safety configuration. If authorization is absent, make
> no write and report `NOT EXERCISED`.

PASS requires requested/achieved values 1, 2, 1 on axes 1, 2, 3 respectively,
the entry value restored, `stop_reason: hook_stop`, and no exposure after time
3. (The action derived from frame *i* is registered for successor *i+1*.)

## 5 — required 50 ms cadence measurement

This measurement is the deliverable; it is not a confirmation question and it
does not set an allowance. Use a no-hardware-action hook so callback cost is
only density logging and routing. A 100-frame cap gives 99 gaps while keeping
the rig dose short.

Give the agent this prompt exactly:

> Save `gate65c_cadence100`, logging time axis and density and returning one
> `ContinueAcquisition` while time is less than 99, then one
> `StopAcquisition`. Run `run_timelapse` with `n_frames=null`,
> `max_frames=100`, `interval_s=0`, `exposure_ms=50`, dataset name
> `gate65c_cadence100`, and no hardware or artifact envelope. Do not start live
> view. Return the complete `inter_frame_gap_summary` and export the recorded
> call to `gate65c_cadence100_export.py`.

Record all of:

- `count` (must be 99 for 100 acquired frames);
- exact `min_s`, `mean_s`, `max_s`;
- bounded `median_le_s`, `p95_le_s` (these are histogram upper bounds, not exact
  quantiles);
- every `histogram` bin and count;
- the largest gap attributable to the hook/run.

Label the result **n=1 run from M2**. Do not call it a microscope property and
do not set a teardown allowance in this session.

## 6 — engine callback-shape observation

Use the instrumented Step 2 reproduction to record the actual event object
received by the engine-facing pre-hardware callback on each of its four
submissions: `dict`, or `list` plus its length. This observation comes from the
real pycro-manager callback before delegating to the unmodified emitted hook;
it does not infer from the candidate queue or from the off-rig fake. If the
JSONL is absent or has fewer than four rows, report this limb `NOT EXERCISED`.

Report counts such as `dict: 4, list: 0` or `dict: 0, list(len=1): 4`, plus
the pycro-manager version. Label it **n=1 run from M2**. This settles only what
that engine did in this run at `interval_s=0`; it does not legislate batching on
other microscopes.

## 7 — send and score

Send:

- `gate65c-m2-computed` and `gate65c-m2-computed.log`;
- registry inspection;
- every tool result, hook log, dataset axis listing and emitted script;
- the per-frame evidence tables;
- full cadence summary and callback-shape counts;
- exact PASS / FAIL / NOT EXERCISED status for every operator limb.

Do not merge based on `status: complete` alone. Score hook log against dataset
axes against emitted rule, with restoration and the absence of any exposure
after accepted stop shown explicitly.

## Before this ships

The gate selftest is run against both checkouts from inside this worktree:

```powershell
uv run python design/65-block65c-gate-selftest.py --tree . --expect implemented
uv run python design/65-block65c-gate-selftest.py --tree ..\microclaw --expect prechange
```

The accepted tree must report 6/6 PASS. Main before 65c must fail limbs A–E
while limb F, the existing-route regression control, still passes.
