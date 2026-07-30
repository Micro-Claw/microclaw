# Block 14 Phase 4 measurement gate — MM configuration apply semantics

Branch under test: `design33/channel-plan-executor` (record the commit below).
This is a **measurement spike only**. It neither implements nor approves a
channel-plan executor. Run it against the Micro-Manager demo configuration on the
rig's Windows machine with MM's ZMQ server already listening on port 4827.

All commands below are PowerShell-safe. Run them from the repo root. Do **not** run
`pip install -e .`: the probe intentionally imports only pycro-manager and uses the
rig's existing installation. Keep every output file in one dated evidence directory.
There are no Unix pipelines; PowerShell redirects both streams with `> ... 2>&1`.

The probe mutates device properties and creates two temporary configuration groups.
It hard-aborts unless a loaded device reports the `DemoCamera` adapter, captures a
full property snapshot, restores it in `finally`, deletes both scratch groups, and
independently verifies their absence before printing any residue. `--allow-non-demo`
exists for probe development only and is
**not for M5**.

## What this demo gate can and cannot settle; M5 still owes X

The demo core can settle the bridge object's real surface, ordered expansion and
repeatability, whether ordered property replay produces the same complete property
state and `Channel` bookkeeping as `set_config`, the demo adapter's wait/busy and
read-back behavior, the meaning and bridge writability of its `Core.Shutter`
settings, MM's demo-config partial-failure behavior, and whether an edited config is
freshly read.

This is also the first live confirmation over this pyjavaz bridge of
`get_available_config_groups`, `get_current_config`, `system_busy`, and
`device_busy`, and of the presence of the mutating `define_config`, `delete_config`,
and `delete_config_group` surfaces. Like Phase 2's previously unconfirmed device-type
ordinals, these calls are an explicit bridge risk rather than assumed API parity.
The read-only Q0 records concrete Python return types and stops before the snapshot
or mutation on a missing method, exception, or non-primitive boolean. The mutating
three can only be confirmed by the later scratch test, after restoration evidence
exists.

It cannot establish that those observations generalize to M5's adapters. In
particular, the demo has simulated state devices and shutters, no real emission, no
serial latency/timeouts, and none of M5's `GenericDevice` laser controls. It also
cannot settle executor cancellation, process loss, or a safe-state policy: this
single synchronous probe can only measure normal return and an intentionally bad
write. Per-write wait semantics and numeric driver reformatting on real hardware
remain unmeasured. M5 cannot supply either measurement for this executor path:
the 30 July M5 inventory has no `Channel` config group at all (only `System`), so
there is no live channel-plan executor test available on that rig.

**M5 still owes X:** if a production `Channel` group is added, a read-only expansion
inventory for every preset (including every `Core.*` entry and the raw
setting-object surface), followed by an operator-approved dark/beam-blocked test of
real per-device waits, read-back formatting, reversibility, and injected partial
failures for the actual drivers. The current M5 `System`-only inventory cannot
discharge those measurements.
No production `Core.*` effect may be admitted merely because demo `Core.Shutter`
selects a simulated shutter. Cancellation and safe-state cleanup remain an
implementation gate with failure injection after every write; this spike cannot
approve either.

---

## G0 — Pin the subject and make an evidence directory

```powershell
git status --short --branch > block14p4-git-status.txt 2>&1
git rev-parse HEAD > block14p4-commit.txt 2>&1
New-Item -ItemType Directory -Force block14p4_30072026 > block14p4-mkdir.txt 2>&1
python -m py_compile design\33-block14-phase4-mm-apply-spike.py > block14p4_30072026\g0-compile.txt 2>&1
```

What this settles: the evidence names the exact source, and the standalone probe
parses under the rig's Python before any bridge contact. Copy
`block14p4-git-status.txt` and `block14p4-commit.txt` into the dated directory.

```powershell
Copy-Item block14p4-git-status.txt block14p4_30072026\git-status.txt > block14p4_30072026\g0-copy-status.txt 2>&1
Copy-Item block14p4-commit.txt block14p4_30072026\commit.txt > block14p4_30072026\g0-copy-commit.txt 2>&1
```

Stop if compilation fails, the branch is not `design33/channel-plan-executor`, or
the recorded commit is not the coordinator-approved spike commit.

## G1 — Run the one-shot measurement

Start Micro-Manager with `MMConfig_demo.cfg`, start its ZMQ server on port 4827, and
leave the GUI alone for the duration of the run. Then run exactly:

```powershell
python design\33-block14-phase4-mm-apply-spike.py --port 4827 --evidence block14p4_30072026\mm-apply-evidence.txt > block14p4_30072026\g1-console.txt 2>&1
```

What this settles: the semantic questions are measured in one connection against one
pre-run snapshot. `g1-console.txt` preserves stdout/stderr even if Python terminates
unexpectedly; `mm-apply-evidence.txt` contains the human-readable sections and the
single JSON document headed `MACHINE_READABLE_JSON` when the probe reaches its
cleanup path.

The exit code must be zero. A nonzero exit is a gate failure, including cleanup
residue or failure to prove scratch-group deletion. Do not rerun until the operator
has inspected the reported current state and decided how to recover it.

## G2 — Expansion shape

Inspect `Q1 Expansion shape and consecutive reads` in the evidence file. For every
name returned by `get_available_configs("Channel")`, record:

1. the exact indexed `(device, property, value)` sequence from each read;
2. `consecutive_equal`;
3. the concrete config/setting types, full sorted `dir()` output, and the accessor
   names that actually worked; and
4. every setting whose device is `Core`.

What this settles: Phase 4's captured-plan representation and bridge access must be
based on observed members and order, not Java/Python naming assumptions. Stop if a
setting is unreadable, the two reads differ without an intentional GUI edit, or an
effect is absent from the evidence.

Also inspect `Q1 Read-only expansion of every config group`. It must enumerate and
expand every preset in Camera, Channel, Channel-Multiband, LightPath, Objective, and
System. This section is enumeration only: no preset outside Channel is applied.

Round 2 completed this inventory: all six groups expanded with zero enumeration
failures. The demo configuration's real shutter-retarget case is
`Channel-Multiband`, whose presets contain `Core.Shutter = LED Shutter` plus the
corresponding `LED.Label` setting. `System` / `Startup` contains
`Core.ChannelGroup = Channel`.

Before G2, inspect both Q0 sections. The capability section must record successful
typed returns for all four read-only calls and callable bridge members for all three
mutating methods. The 20-call `get_property` baseline must include every sample plus
minimum and median milliseconds. What this settles: whether the new bridge surface
exists as expected and the serialized ZMQ round-trip floor against which all later
timings must be interpreted. Method presence does not prove mutation semantics; G5
does that.

Round 2 also recorded the concrete expansion bridge surface:
`mmcorej_Configuration` and `mmcorej_PropertySetting`, including
`PropertySetting.get_read_only`. These are observed bridge members, not assumed
Java/Python naming parity.

## G3 — Replay equivalence, waits, and read-back

For each preset, inspect `Q2/Q4/Q5`. The load-bearing fields are:

- `end_state_diff_set_config_vs_replay` — a structural diff over every snapshotted
  property, not selected scalars;
- `current_config_after_set_config`, `current_config_after_replay`, and
  `configuration_state_equal` — MM configuration-state bookkeeping is part of the
  end state;
- `set_config_ms`, `busy_immediately_after_set_config`, `wait_for_config_ms`, and
  replay `total_ms` plus every per-write `set_ms`/`wait_ms`; and
- every requested string, exact read-back string, and `exact` flag on **both** the
  `set_config` and replay paths; and
- shutter target/open/auto-shutter observations before `set_config`, immediately
  after it returns, and after `wait_for_config`.

What this settles: whether a plain ordered property loop is observably equivalent on
the demo core; what `set_config` has completed when it returns; what additional wait
time `wait_for_config` consumes; the round-trip cost of per-device serialization;
and whether exact-string verification would reject values MM legitimately reformats.
Compare these timings with Q0's minimum/median bridge baseline: an executor write
with a device wait and read-back uses roughly three serialized round trips, so the
measured cost—not a local-call assumption—must inform the executor design.

Whether `set_config` applies its individual settings in expansion order is **not
observable over this bridge**. This probe measures complete end-state equivalence,
busy/wait behavior, and read-back fidelity; it must not be cited as evidence of
internal apply ordering.

An empty property diff is insufficient if configuration-state bookkeeping differs.
Conversely, a bookkeeping-only difference is still a real non-equivalence that the
executor design must resolve explicitly. Record the full diff rather than summarizing
it as “looks the same.”

## G4 — every `Core.*` pseudo-device effect, including `Core.Shutter`

Inspect `Q3 All Core.* pseudo-device effects`. The probe restores the pristine
pre-run snapshot before Q3 and between effects. Record every distinct Core property
and value appearing in an expansion, its direct bridge permission and read-back,
and its before/after shutter observation. For `Core.Shutter`, record the active
shutter, `Core.AutoShutter`, and every named shutter's primitive
`get_shutter_open(device)` value. Also correlate these with each preset's Q2
before/immediate/after-wait shutter observations.

What this settles: whether the demo `Core.Shutter` property selects the active
shutter device or opens light, and whether that pseudo-device effect can be replayed
over this bridge. The property must not be admitted in Phase 4 if the call is blocked
or its observed effect cannot be represented and authorized. Demo evidence does not
authorize the same operation on M5.

Then inspect `Q3b Core.Shutter retargeting`. It must enumerate both White Light
Shutter and LED Shutter, retarget each non-active shutter without snapping or a
direct shutter-open call, record the active shutter, `Core.AutoShutter`, and every
shutter's open state before and after, and verify restoration of the original active
shutter. In particular, record whether the previously active shutter remains open.
Whether the retargeted shutter would emit on the next exposure is an inference from
`Core.AutoShutter = 1`, not a measured light event: this probe deliberately never
snaps. Phase 4 may treat `Core.Shutter` as illumination-relevant on that basis, but
must not describe the evidence as measured emission.

Inspect `Q5b Numeric read-back fidelity` for the separate Camera.Exposure scratch
group. Record requested `"10"` against the exact string read back after both
`set_config` and ordered replay. Cleanup must prove deletion of this group separately
from the partial-failure scratch group.

## G5 — Partial failure and reversibility

Inspect `Q6 Partial failure and reversibility`. The scratch config contains three
ordered, distinct, writable enumerated properties on **three distinct devices**;
setting 2 has an intentionally invalid value. The probe must fail clearly if the
demo config cannot supply those three devices. Record for `set_config` and the
ordered property loop:

- the cleaned exception text;
- the complete `state_diff` immediately after failure;
- whether setting 1 landed and whether setting 3 did not;
- each restore attempt and `post_restore_diff`; and
- the exact expanded order, since MM may not preserve definition order.

What this settles: rollback versus half-configuration on the demo core, comparison
with a direct loop, and observed reversibility. If MM refuses the bad value while
defining the config rather than while applying it, that is also a measured outcome,
but this run has not exercised apply-time partial failure; mark this step FAIL and
revise the probe before drawing an executor conclusion.

## G6 — TOCTOU realism and final hygiene

Inspect `Q7 TOCTOU re-read` and `Cleanup and residue verification`. The former must
show the scratch definition before and after replacing one value and must report
`reread_changed: true`. The latter must report:

- `still_present: false` for both scratch groups;
- an empty `RESIDUE (must be empty)` list; and
- top-level JSON `exit_code: 0` and an empty `errors` list.

What this settles: `get_config_data` really re-reads a mutable definition, so the
TOCTOU threat is observable rather than hypothetical; the run did not silently
leave the demo core changed. Any residue is a gate failure even if all semantic
measurements otherwise look persuasive.

---

## Results — rounds 1 and 2

| Gate | Result | Evidence file / exact observation | Phase 4 consequence |
|---|---|---|---|
| G0 source + compile | PASS (round 1) | Probe compiled before the recorded run. | Subject was runnable; round 2 must compile again before execution. |
| G1 complete run / exit code | PASS (round 1) | `exit_code: 0`, `errors: []`. | Round-1 evidence is usable. |
| Q0 new bridge capabilities and Python types | PASS | Four read-only calls returned the expected primitive/vector shapes; `define_config`, `delete_config`, and `delete_config_group` were callable and later exercised. | The required bridge surface exists. |
| Q0 20-call round-trip min/median | Measured | 0.13 ms min / 0.14 ms median over 20 `get_property` calls. | Use as the serialized bridge-call floor. |
| G2 all preset expansions and consecutive reads | PASS (round 2) | All six groups—Camera, Channel, Channel-Multiband, LightPath, Objective, and System—expanded; zero enumeration failures. Cy5, DAPI, FITC, and Rhodamine were stable on consecutive reads in round 1. | The cross-group inventory is complete for the demo configuration. |
| G2 `Core.*` entries | Measured | Round 1: every Channel preset contained `Core.Shutter = White Light Shutter`, already active. Round 2: Channel-Multiband supplies the real retarget shape, `Core.Shutter = LED Shutter` plus `LED.Label` per preset; System/Startup sets `Core.ChannelGroup = Channel`. | Retarget measurement must use the round-2-discovered LED Shutter case; inventory alone does not establish apply semantics. |
| G2 expansion bridge object surface | Measured (round 2) | Concrete types were `mmcorej_Configuration` and `mmcorej_PropertySetting`; the recorded PropertySetting members include `get_read_only`. | Expansion code must follow the observed bridge surface and preserve it in evidence. |
| G3 structural replay equivalence | PASS | Empty `end_state_diff_set_config_vs_replay` on all four presets. Cy5 is vacuous: the core was already in the Cy5 state, so its `set_config_change` is empty and it evidences nothing. DAPI, FITC, and Rhodamine are the three non-vacuous cases carrying the finding. | Ordered replay matched `set_config` on the three evidentiary demo cases. |
| G3 `Channel` bookkeeping equivalence | PASS | `configuration_state_equal: true` on all four presets, with the same Cy5 vacuity caveat. | Replay preserved observed MM config bookkeeping on the three non-vacuous cases. |
| G3 busy/wait timings | Not settled and not settleable here | `system_busy` and every `device_busy` read false immediately after `set_config` on all four presets because demo devices complete instantly. `set_config` was 0.20–1.38 ms; replay was 2.36–12.32 ms. | This says nothing about a real rig's wait requirements; per-write wait semantics remain unmeasured. |
| G3 read-back formatting / Q5b numeric | Partial; Q5b NOT RUN (round 2) | Round 1's enumerated string labels read back exactly. Round 2 aborted at Q3b before Q5b. | Exact-string verification against driver-reformatted numeric values is not yet settled. |
| G3 per-preset shutter observations | Inconclusive | `Core.AutoShutter = 1`, but all presets selected the already-active White Light Shutter and it remained closed in the observations. | A real retarget is required; round 2 adds it without snapping or opening shutters. |
| G4 Q3b shutter retarget | NOT RUN (round 2) | Q3b failed before measurement because active `White Light Shutter` was not enumerated: the probe stringified the DeviceType proxy instead of converting it through the bridge API. | Retargeting remains pending; the revised probe preserves converted and raw bridge type evidence. |
| G5 `set_config` partial failure | Measured behavior (round 1); Q6 NOT RUN (round 2) | Round 1: MM continued past a failed setting and raised afterward. Exact `state_diff`: `Camera.AllowMultiROI` `0`→`1` (setting 1) and `Emission.ClosedPosition` `0`→`1` (setting 3). Round 2 aborted at Q3b before Q6. | Executor cannot assume `set_config` is atomic or fail-fast; this conclusion remains attributed to round 1. |
| G5 property-loop partial failure | Measured behavior (round 1); Q6 NOT RUN (round 2) | Round 1: ordered replay failed fast at setting 2 after setting 1 landed; exact `state_diff`: only `Camera.AllowMultiROI` `0`→`1`; setting 3 was untouched. Round 2 aborted at Q3b before Q6. | Ordered execution provides a known failure boundary based on round-1 evidence. |
| G5 reversibility | PASS on demo (round 1); Q6 NOT RUN (round 2) | Round 1: both `post_restore_diff` values were empty. Round 2 aborted at Q3b before Q6. | Demonstrates round-1 demo reversibility only. |
| G6 mutable-definition re-read | PASS (round 1); Q7 NOT RUN (round 2) | Round 1: `reread_changed: true`. Round 2 aborted at Q3b before Q7. | TOCTOU is observable from round-1 evidence; capture/re-read policy is required. |
| G6 scratch deletion and final residue | PASS (round 1) | `still_present: false`; `final_residue: []`. | Round 1 left zero residue; round 2 must prove this for both scratch groups. |

## Stop list

Do not treat the spike as a pass, and do not start executor implementation, if any
of these occurs: non-demo guard bypass was needed; a setting cannot be enumerated;
consecutive expansion reads drift without an intentional edit; replay differs but
the difference is not preserved; `Core.*` behavior remains ambiguous; partial
failure is not reached; an exception is swallowed or reduced to JNI noise; the
scratch group remains; any property residue remains; or the machine-readable blob
is missing.

Even a clean demo result authorizes only the next design decision. It does not prove
M5 safety and does not discharge cancellation, rollback/safe-state implementation,
or injected failure after every executor write.

---

## Executor implementation gate (G7+)

Use `design/33-block14-phase4-demo-safety-config.yaml`, after replacing
`workspace_dir`, with stock `MMConfig_demo.cfg`. Record the implementation commit,
console transcript, authorization-map JSON, and a complete property snapshot before
and after each mutation. Do not use another config group.

### G7 — authorized captured apply

Start microclaw with the Phase 4 profile and apply DAPI, FITC, and Rhodamine through
`set_channel`. Accept the `Core.Shutter` illumination-class prompt. For each result,
record `writes`, both expansion SHA-256 values, `expansion_drift: false`, the ordered
set/wait/read sequence, and `getCurrentConfig("Channel")`. PASS requires exact label
read-back, numeric type-aware comparison where present, and no call to `set_config`.

### G8 — refused apply before mutation

First request a preset absent from `channels.allowed`. Then, in the MM GUI, add an
undeclared or excluded property to an allowed preset and request it again. PASS
requires a named refusal before the first property write and an unchanged full
property snapshot. Restore the preset definition in the GUI afterward.

### G9 — startup/apply drift detector

After startup, change only an already-authorized categorical value in DAPI, apply
DAPI, and restore the definition. PASS requires `expansion_drift: true`, unequal
startup/applied hashes shown in the result, authorization of the freshly captured
value, and application of that captured value only. Repeat with an unsafe new pair;
that limb must refuse before mutation rather than treating the startup hash as an
authorization token.

### G10 — injected partial failure and rollback

Run the off-rig executor failure fixture at the pinned commit:

```powershell
python -m pytest tests\test_channel_plan_executor.py -k "failure_after_each_position or failing_rollback" -vv
```

PASS requires injection at every write position, no later forward write, reverse
rollback of every attempted write (including the ambiguous write that raised), and
an explicit partial-application error naming applied, attempted, and rolled-back
pairs. The failing-rollback limb must say `SAFE STATE NOT VERIFIED` and name the
rollback failure; it must not claim clean recovery. This deterministic fake gate is
the Phase 4 failure injection: the demo adapters cannot safely inject transport
failure into a live property write.

### G11 — shutter-retarget confirmation and cancellation boundary

Temporarily create a scratch preset **inside the fixed Channel group** containing
the measured Channel-Multiband shape (`Core.Shutter = LED Shutter` plus `LED.Label`)
with both demo shutters declared; never execute the Channel-Multiband group itself.
Decline the first
illumination-class prompt: no property may change. Repeat and accept: while idle,
the selected shutter changes, both shutters remain closed, and the previous target
is restored after evidence capture. Do not snap or expose. Separately run:

```powershell
python -m pytest tests\test_channel_plan_executor.py -k cancellation_between_writes -vv
```

PASS requires cancellation only at a write boundary followed by rollback. The gate
must not describe this as an in-flight bridge-call interrupt.
