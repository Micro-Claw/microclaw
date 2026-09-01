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
command deliberately reproduces this same four-frame, 50 ms run once on M2.
**This is also the export-runs limb:** it proves the emitted program works
against the real engine, not merely that it compiles. A failure here is an
export defect first, even if callback-shape evidence is consequently absent.

```powershell
uv run python design/65-block65c-gate.py --instrument-export gate65c_density_stop3_export.py --instrumented-out gate65c_density_stop3_instrumented.py
uv run python gate65c_density_stop3_instrumented.py > gate65c-density-instrumented.log 2>&1
$InstrumentedExit = $LASTEXITCODE
Get-Content gate65c-density-instrumented.log
if ($InstrumentedExit -ne 0) { throw "The instrumented exported run failed; callback shape was not established" }
Get-Content gate65c_callback_shapes.jsonl
Get-Content gate65c_instrumented_run.jsonl
```

Do not run that script until the operator has approved the four-frame
reproduction. It writes `gate65c_callback_shapes.jsonl` and
`gate65c_instrumented_run.jsonl` itself, so no transcript assumption is
involved. The latter must contain a `start` row and an `end` row with
`"frames": 4`; an absent end row is a failed exported run even if the shape
file has content.

## 3 — image-derived non-dosing property, if M2 admits it

Choose the pair from M2's authorization map and Core inventory by this checkable
test: the property is writable; its device carries **no declared stage bounds**;
the property is non-dosing; and two distinct values lie inside its reviewed
bounds. Design/49 refuses every raw property write to a device carrying declared
stage bounds, even when the named property itself does not move the stage. Record
the chosen device, property, two values, bounds, and the evidence for all four
conditions. If M2 admits no such pair, this limb is legitimately
`NOT EXERCISED`; do not substitute a laser, camera exposure, stage position,
TTL, PWM, servo, or another convenient property.

Give the agent this prompt exactly:

> Inspect M2's inventory and reviewed authorization without writing. Choose a
> property only if it is writable, non-dosing, its device carries no declared
> stage bounds, and two distinct values are inside its reviewed bounds. Record
> the exact device, property, values, bounds, and why each condition passes. If
> one exists, save `gate65c_nondosing_image` whose image-derived predicate
> alternates those two values beside exactly one routing decision. Run
> `run_timelapse` with `n_frames=null`, `max_frames=4`, `interval_s=0`,
> `exposure_ms=50`, a property envelope containing that exact pair and values,
> `max_writes=4`, and restore `entry`. Stop at time 3 and export the recorded
> call to `gate65c_nondosing_image_export.py`. If no pair passes every test,
> make no write and end with `LIMB 3 NOT EXERCISED:` followed by the inventory
> reason.

PASS requires each proposed value to be applied, waited for and read back before
the next exposure; the entry value must be restored after the acquisition
context on success. Retain the exception-path restoration result if this run
happens to fail; do not induce a fault solely to obtain it.

Scoring note from M2 round 1: `SmarActXY.Hold time (ms)` was refused before any
write because `SmarActXY` carries declared stage bounds. That run did not
exercise this limb, but it did establish that the bounded-stage guard reaches
the adaptive route.

## 4 — authorized FPGA-duration run

**This is the dose-bearing limb and it must not be left open without a
verdict.** It uses the exact pair `Laser Trigger.Duration0 (us)` and
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
> laser enable, or the safety configuration. If authorization is absent or
> cannot be established in this session, make no write. In every case, finish
> with exactly one verdict line: `LIMB 4 PASS: authorized Duration0 run
> completed and evidence saved`, or `LIMB 4 NOT EXERCISED:` followed by the
> specific reason. Do not move to limb 5 before writing that line.

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
- `max_s`, which on this no-hardware-action route is the largest measured gap
  including the hook's software contribution.

Label the result **n=1 run from M2**. Do not call it a microscope property and
do not infer a cause from this one measurement. The 2026-08-31 measurement
(99 gaps at 50 ms: min 0.219 s, mean 0.2495 s, max 0.344 s) supports the
currently approved 0.5 s/frame teardown allowance; these additional runs test
attribution and do not silently revise it.

### 5A — hardware-sequenced fixed baseline

This is the same field, exposure and frame count as Step 5, without a hook.
Give the agent this prompt exactly:

> Without starting live view, run plain `run_timelapse` on the same field with
> `n_frames=100`, `interval_s=0`, `exposure_ms=50`, `save_dir=data`, and dataset
> name `gate65c_fixed100_baseline`. Do not attach a hook or any envelope. Return
> the complete `inter_frame_gap_summary`. End with exactly one verdict line:
> `LIMB 5A PASS: fixed baseline summary saved`, or `LIMB 5A NOT EXERCISED:`
> followed by the specific reason.

Record the complete summary and label it **n=1 run from M2**. This is the
hardware-sequenced baseline observed in this run, not a general camera rate.

### 5B — routing-only adaptive observation

This removes density analysis while retaining one-at-a-time adaptive dispatch.
Give the agent this prompt exactly:

> Save `gate65c_continue_only100`, whose `analyze_frame` reads only the time
> axis and immediately returns one `ContinueAcquisition` for every image. It
> must perform no density analysis, image statistics, hardware action or
> artifact write. Without starting live view, run `run_timelapse` on the same
> field with `n_frames=null`, `max_frames=100`, `interval_s=0`,
> `exposure_ms=50`, `save_dir=data`, dataset name
> `gate65c_continue_only100`, and no hardware or artifact envelope. Return the
> complete `inter_frame_gap_summary` and `stop_reason`. End with exactly one
> verdict line: `LIMB 5B PASS: routing-only summary saved`, or
> `LIMB 5B NOT EXERCISED:` followed by the specific reason.

PASS requires 100 frames and `stop_reason: cap_reached`. Record the complete
summary and label it **n=1 run from M2**.

Place the complete Step 5 density-and-routing, Step 5A fixed, and Step 5B
routing-only `inter_frame_gap_summary` objects side by side. Report differences
as three observations from one rig and one run of each shape. Do not conclude
that the hook, bridge, engine or camera caused any difference; these limbs
provide the missing baseline for a later attribution decision.

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
uv run python design/65-block65c-gate-selftest.py --tree . --expect implemented --out-shape absolute
uv run python design/65-block65c-gate-selftest.py --tree . --expect implemented --out-shape relative
uv run python design/65-block65c-gate-selftest.py --tree ..\microclaw --expect prechange --out-shape absolute
uv run python design/65-block65c-gate-selftest.py --tree ..\microclaw --expect prechange --out-shape relative
```

The accepted tree must report 6/6 PASS. Main before 65c must fail limbs A–E
while limb F, the existing-route regression control, still passes.
