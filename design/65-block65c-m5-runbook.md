# Block 65c — remaining adaptive-timelapse gate on M5

M2 access ended after two gate rounds. This is the single runbook for the
remaining work: it preserves the M2 evidence below and sends only the open
limbs to M5. Do not rerun an M2-closed limb merely to make a new artifact.

Live view is dose on M5. Keep it off outside the operator-approved setup. M5
has no `Channel` config group; its `System` presets arm four lasers for TTL and
set camera exposure. Every run below therefore passes no channel and must not
invent a Channel preset. M5's hazardous continuous actuators are
`GenericDevice`; properties named `Number of X` are pre-init and invisible in
the Property Browser, so inventory them through Core rather than sending the
operator to that browser.

`NOT EXERCISED` is never a pass. Every M5 limb ends with its literal PASS,
FAIL, or NOT EXERCISED line before moving on.

## Evidence already established on M2 — do not rerun for its own sake

| Limb | M2 evidence | Status |
|---|---|---|
| Computed gate | Round 2: A–F all PASS, `6/6 PASS` on the rig | already evidenced |
| Successor dispatch and hook stop | axes 0–3, 4/8 frames, `stop_reason: hook_stop`, no exposure after accepted stop | already evidenced |
| Emitted script runs | script-owned markers `{"frames": 0, "marker": "start"}` and `{"frames": 4, "marker": "end"}` against the real engine | already evidenced |
| Engine callback shape | four callbacks, all `dict`, no lists, at `interval_s=0`; n=1 from M2 | already evidenced |
| Bounded-stage guard reaches adaptive route | `SmarActXY.Hold time (ms)` refused before any write because the device carried declared stage bounds | already evidenced |
| Density-and-routing cadence | 99 gaps at 50 ms: min 0.219 s, mean 0.2495 s, max 0.344 s; n=1 from M2 | already evidenced, but repeated on M5 as a second observation for attribution |

The M2 computed artifacts and callback instrumentation remain historical
evidence. Do not rerun the computed gate, the four-frame successor run, or the
instrumented export solely to reproduce them on M5.

## 0 — branch pin and M5 setup

Open PowerShell in the existing M5 Microclaw checkout. These commands contain
no substitutions or placeholders:

```powershell
Set-Location (git rev-parse --show-toplevel)
git checkout design65/adaptive-timelapse-route
git merge-base --is-ancestor 912f5e3 HEAD
if ($LASTEXITCODE -ne 0) { throw "STOP: 912f5e3 is not an ancestor; this is not the accepted implementation" }
git status --short --branch
```

Start Micro-Manager with M5's ordinary htSMLM/MicroFPGA configuration and ZMQ
server. Start Microclaw with M5's reviewed safety configuration. Do not edit the
production safety configuration for this gate and do not start live view.

Before exposure, give the connected agent this inventory prompt exactly:

> Without snapping, starting live view, or writing hardware, report M5's
> connected Core device/property inventory, authorization-map classifications,
> declared bounded-stage devices, and declared illumination pairs relevant to
> this gate. M5 has no Channel config group; do not look for or create one.
> Include pre-init properties from Core even when the Property Browser hides
> them. Save the response as `gate65c-m5-inventory.txt`.

This inventory is input to the discovery steps below; it is not permission to
write any pair.

## Evidence table for every M5 property run

Save each tool result, hook log, dataset path and dense axes, and fresh emitted
script. Echo the discovered device/property literally into the saved evidence.
If any emitted script is executed while diagnosing a limb, that script must
write its own start/end log with frame count; PowerShell `Start-Transcript` is
not evidence for native child stdout.

Fill one row per frame:

| time axis | exact device | exact property | requested value | achieved read-back | decision | exposure observed |
|---:|---|---|---|---|---|---|

Also record the entry value, approved envelope bounds/values, `max_writes`,
`stop_reason`, restoration result, final read-back, and whether any dataset axis
or hook record followed the accepted stop. Compilation or a status sentence is
not evidence.

## M5 control — bounded-stage envelope refusal before any write

Run this control before either admitting limb. It must fire, or limbs 3 and 4
have no working safety control.

Give the connected agent this prompt exactly:

> Inspect the connected M5 inventory without writing. Discover one exact
> writable raw property on a device listed in `bounded_stage_devices`; the pair
> must not itself be an already-reviewed built-in typed capability, must not be
> explicitly excluded, and must expose either two discrete values or finite
> driver limits. Report the exact device, property, entry value, property shape,
> and why the device is a declared bounded-stage device. Then, in this same
> request without asking a human to transcribe the pair, save
> `gate65c_bounded_stage_control`, whose hook returns exactly one
> `ContinueAcquisition`, and call the adaptive `run_timelapse` route with
> `n_frames=null`, `max_frames=2`, `interval_s=0`, `exposure_ms=50`, no channel,
> `save_dir=data`, dataset name `gate65c_m5_bounded_stage_control`,
> `hook_strategy=gate65c_bounded_stage_control`,
> and a `property_envelope` naming that discovered pair with two in-range
> values, positive `max_writes`, and `restore: entry`. This is specifically an
> image-derived `SetDeviceProperty` capability request; do not substitute a
> stage-move tool. It must be refused before acquisition and before any property
> write. Re-read the property and prove no dataset was created. End with exactly
> one line: `M5 BOUNDED-STAGE CONTROL PASS: envelope refused before write and
> exposure`, `M5 BOUNDED-STAGE CONTROL FAIL:` followed by evidence, or `M5
> BOUNDED-STAGE CONTROL NOT EXERCISED:` followed by the inventory reason.

PASS requires a bounded-stage refusal, unchanged read-back, no property write,
and no exposure. If no pair satisfies the discovery conditions, this control is
`NOT EXERCISED`, never a pass, and the admitting limbs must not be scored PASS.

Scope note: this control chooses a raw, non-typed pair because C1 changed the
authorization of otherwise-unclassified property envelopes. Reviewed typed
capabilities use a pre-existing exemption and are not a control for that change.

## M5 limb 3 — image-derived write to a non-dosing property

This limb was never exercised on M2 and **PASSED on M5 on 2026-09-01** with
image-derived writes to `Thorlabs ELL6.State`, read-back, and entry restoration.
Keep the four-condition selection test for any deliberate rerun:
the pair is writable; its device has no declared stage bounds; the property is
non-dosing; and two distinct values lie inside reviewed bounds. Design/49
refuses every raw property write to a device with declared stage bounds, even
when the named property does not move the stage.

Give the connected agent this prompt exactly:

> Inspect M5's connected Core inventory and reviewed authorization without
> writing. Discover a property only if it is writable, non-dosing, its device
> carries no declared stage bounds, and two distinct values are inside its
> reviewed or driver-intersected bounds. Report the exact device, property,
> entry value, two values, bounds, and the `get_device_property_info`
> authorization classification and disposition for that exact pair, and
> evidence for all four conditions before any write. Read that classification;
> do not submit an envelope merely to discover it. The exact pair must report
> `admitted_only_under_approved_envelope`, not `refused_as_excluded`, so its envelope
> exercises C1's admitting half. Do not choose a laser, camera exposure, stage
> position, TTL, PWM, servo, or pre-init `Number of X` property. If a pair
> exists, continue in this
> same request without asking a human to transcribe it: save
> `gate65c_m5_nondosing_image`, whose image-derived predicate alternates those
> values beside exactly one routing decision. Run `run_timelapse` with
> `n_frames=null`, `max_frames=4`, `interval_s=0`, `exposure_ms=50`, no channel,
> `save_dir=data`, dataset name `gate65c_m5_nondosing_image`,
> `hook_strategy=gate65c_m5_nondosing_image`, and a `property_envelope`
> containing that exact discovered pair and values, `max_writes=4`, and
> `restore: entry`.
> The hook mechanism must be image-derived `SetDeviceProperty`; do not satisfy
> this with a direct property tool or a better route. Stop at time 3 and export
> the recorded call to `gate65c_m5_nondosing_image_export.py`. End with exactly
> one line: `M5 LIMB 3 PASS: non-dosing image-derived writes and restoration
> evidenced`, `M5 LIMB 3 FAIL:` followed by evidence, or `M5 LIMB 3 NOT
> EXERCISED:` followed by the inventory reason.

PASS requires each action derived from frame *i* to be applied, waited for and
read back before successor *i+1* is exposed; the entry value is restored after
the acquisition context; `stop_reason: hook_stop`; and no exposure follows the
accepted stop. If M5 has no pair passing all four conditions, make no write.

## M5 limb 4 — discovered FPGA activation-pulse duration

This dose-bearing limb was never exercised in either M2 round. On M5 it finally
ran writes at times 0–2 with read-back and stopped at time 3, then **FAILED**
because restoration was charged as a fourth write after the three proposal
slots were consumed. The product fix makes restoration the envelope's uncharged
promise; this limb remains the closest to the workflow design/65 exists for and
must not be left open on a retry.

On M5, activation-path dose is **level × duration**. Microclaw separately bounds
the level; htSMLM ramps the FPGA activation-pulse duration written here. The
property envelope bounds only the duration factor, not the whole product. Keep
approved duration values minimal and single-digit microseconds. The operator
must explicitly approve the resulting level × duration dose before acquisition.

The exact M5 device/property label is deliberately not asserted here. Give the
connected agent this single prompt exactly:

> Without writing or exposing, use the connected M5 Core inventory to discover
> the exact htSMLM/MicroFPGA property that sets activation pulse length. Do not
> guess a label and do not ask the operator to transcribe one. Report the exact
> device, exact property, entry value, type, driver limits or discrete values,
> units, and the classification and disposition returned for this exact pair by
> `get_device_property_info`. This is a read-only policy query: do not submit an
> envelope to discover the classification. If it reports
> `admitted_only_under_approved_envelope`, proceed; if it reports
> `refused_as_excluded` or `refused_bounded_stage`, stop with `NOT EXERCISED`.
> Report the smallest two distinct safe single-digit-microsecond values inside
> the intersected bounds. Also report the separately bounded
> activation level and calculate or state the resulting level × duration dose envelope;
> the duration envelope controls only one factor. Pause for the operator's
> explicit approval of that dose. If approved, continue in this same request
> using the discovered pair: save `gate65c_m5_fpga_duration_stop3`. For time
> axes 0, 1 and 2 its image-derived predicate must propose
> `SetDeviceProperty` alternating those two approved duration values beside
> exactly one `ContinueAcquisition`; at time 3 it must return exactly one
> `StopAcquisition` and no property action. Run adaptive `run_timelapse` with
> `n_frames=null`, `max_frames=8`, `interval_s=0`, `exposure_ms=50`, no channel,
> `save_dir=data`, dataset name `gate65c_m5_fpga_duration_stop3`,
> `hook_strategy=gate65c_m5_fpga_duration_stop3`, and a `property_envelope`
> for the discovered exact pair with only those values, `max_writes=3`, and
> `restore: entry`. The duration register is on the same MicroFPGA device as
> other trigger controls: **do write the discovered duration register**, but do
> not change `Mode0`, `Sequence0`, any laser enable, or any other TTL-arming
> setting. Do not edit a safety configuration, substitute a direct property
> tool, or start live view. Export
> the recorded call to `gate65c_m5_fpga_duration_stop3_export.py`. End with
> exactly one line: `M5 LIMB 4 PASS: authorized FPGA duration run and evidence
> saved`, `M5 LIMB 4 FAIL:` followed by evidence, or `M5 LIMB 4 NOT EXERCISED:`
> followed by the precise discovery, authorization, or approval reason. Do not
> move to cadence attribution before writing that verdict.

PASS requires the exact discovered pair in the envelope, hook log and emitted
script; requested and achieved values on successor axes 1, 2 and 3; restoration
to entry; `stop_reason: hook_stop`; and no exposure after time 3. The operator's
dose approval must be saved beside the result.

## M5 cadence-attribution set — same field, same 50 ms exposure

Run all three observations consecutively on the same field with live view off.
Do not move the stage or change camera exposure, optical state, or acquisition
settings between them except for the route and hook differences named below.
Save every complete `inter_frame_gap_summary`: `count`, exact `min_s`/`mean_s`/`max_s`,
bounded `median_le_s`/`p95_le_s`, and every histogram bin.

M2 measured 99 adaptive density-and-routing gaps at 50 ms: min 0.219 s, mean
0.2495 s, max 0.344 s. M5 is a **second observation, not a confirmation**. If
the rigs differ, both observations stand and neither is “the” cadence.

### M5 cadence A — fixed hardware-sequenced baseline

Give the agent this prompt exactly:

> Without starting live view, run plain `run_timelapse` on the selected M5 field
> with `n_frames=100`, `interval_s=0`, `exposure_ms=50`, `save_dir=data`, no
> channel, no hook, no envelope, and dataset name
> `gate65c_m5_fixed100_baseline`. Return and save the complete
> `inter_frame_gap_summary`. End with exactly one line: `M5 CADENCE A PASS:
> fixed summary saved`, `M5 CADENCE A FAIL:` followed by evidence, or `M5
> CADENCE A NOT EXERCISED:` followed by the reason.

### M5 cadence B — adaptive density and routing

Give the agent this prompt exactly:

> Save `gate65c_m5_density100`, whose `analyze_frame` logs the time axis and an
> image-derived density and returns exactly one `ContinueAcquisition` while time
> is less than 99, then exactly one `StopAcquisition`. Without starting live
> view, run adaptive `run_timelapse` on the same M5 field with `n_frames=null`,
> `max_frames=100`, `interval_s=0`, `exposure_ms=50`, `save_dir=data`, no
> channel, `hook_strategy=gate65c_m5_density100`, no hardware or artifact
> envelope, and dataset name
> `gate65c_m5_density100`. Return and save the complete
> `inter_frame_gap_summary` and `stop_reason`. End with exactly one line: `M5
> CADENCE B PASS: density-and-routing summary saved`, `M5 CADENCE B FAIL:`
> followed by evidence, or `M5 CADENCE B NOT EXERCISED:` followed by the reason.

PASS requires 100 frames, 99 gaps and `stop_reason: hook_stop`.

### M5 cadence C — adaptive routing only

Give the agent this prompt exactly:

> Save `gate65c_m5_continue_only100`, whose `analyze_frame` reads only the time
> axis and immediately returns exactly one `ContinueAcquisition` for every
> image. It must perform no density analysis, image statistic, hardware action
> or artifact write. Without starting live view, run adaptive `run_timelapse`
> on the same M5 field with `n_frames=null`, `max_frames=100`, `interval_s=0`,
> `exposure_ms=50`, `save_dir=data`, no channel, no hardware or artifact
> envelope, `hook_strategy=gate65c_m5_continue_only100`, and dataset name
> `gate65c_m5_continue_only100`. Return and save the
> complete `inter_frame_gap_summary` and `stop_reason`. End with exactly one
> line: `M5 CADENCE C PASS: routing-only summary saved`, `M5 CADENCE C FAIL:`
> followed by evidence, or `M5 CADENCE C NOT EXERCISED:` followed by the reason.

PASS requires 100 frames, 99 gaps and `stop_reason: cap_reached`.

Place the three complete M5 summaries side by side, followed by the historical
M2 density-and-routing summary. Label every M5 row **n=1 observation from M5**
and the M2 row **n=1 observation from M2**. Report differences without assigning
them to hook analysis, bridge dispatch, engine scheduling or camera behavior.

## Send and score

Send:

- `gate65c-m5-inventory.txt` and the bounded-stage control evidence;
- every M5 tool result, hook log, dense dataset axis listing and emitted script;
- both property limbs' per-frame evidence tables and explicit verdicts;
- the operator's saved level × duration approval for limb 4;
- all three complete M5 cadence summaries side by side with the M2 numbers;
- exact PASS / FAIL / NOT EXERCISED status for every M5 limb.

Score hook logs against dataset axes against emitted rules, including read-back,
restoration, and absence of exposure after an accepted stop. Do not merge based
on `status: complete` alone. Preserve the M2 artifacts as the evidence for the
closed limbs; their absence from the M5 folder is not a reason to spend dose
re-running them.

## Off-rig selftest already complete

At branch tip `80cd5b4`, the selftest discriminated in all four tree ×
`--out-shape` combinations: accepted tree `6/6 PASS` for absolute and relative;
pre-change tree A–E FAIL with control F PASS for absolute and relative. This is
already evidenced and is not an M5 dose limb.
