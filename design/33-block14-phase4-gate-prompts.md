# Block 14 Phase 4 measurement gate — MM configuration apply semantics

Branch under test: `design33/channel-plan-executor` (record the commit below).
This is a **measurement spike only**. It neither implements nor approves a
channel-plan executor. Run it against the Micro-Manager demo configuration on the
rig's Windows machine with MM's ZMQ server already listening on port 4827.

All commands below are PowerShell-safe. Run them from the repo root. Do **not** run
`pip install -e .`: the probe intentionally imports only pycro-manager and uses the
rig's existing installation. Keep every output file in one dated evidence directory.
There are no Unix pipelines; PowerShell redirects both streams with `> ... 2>&1`.

The probe mutates device properties and creates one temporary configuration group.
It hard-aborts unless a loaded device reports the `DemoCamera` adapter, captures a
full property snapshot, restores it in `finally`, deletes the scratch group, and
prints any residue. `--allow-non-demo` exists for probe development only and is
**not for M5**.

## What this demo gate can and cannot settle; M5 still owes X

The demo core can settle the bridge object's real surface, ordered expansion and
repeatability, whether ordered property replay produces the same complete property
state and `Channel` bookkeeping as `set_config`, the demo adapter's wait/busy and
read-back behavior, the meaning and bridge writability of its `Core.Shutter`
settings, MM's demo-config partial-failure behavior, and whether an edited config is
freshly read.

It cannot establish that those observations generalize to M5's adapters. In
particular, the demo has simulated state devices and shutters, no real emission, no
serial latency/timeouts, and none of M5's `GenericDevice` laser controls. It also
cannot settle executor cancellation, process loss, or a safe-state policy: this
single synchronous probe can only measure normal return and an intentionally bad
write.

**M5 still owes X:** a read-only expansion inventory for every production `Channel`
preset (including every `Core.*` entry and the raw setting-object surface), followed
by an operator-approved dark/beam-blocked test of real per-device waits, read-back
formatting, reversibility, and injected partial failures for the actual drivers.
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

What this settles: all seven questions are measured in one connection against one
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

## G3 — Replay equivalence, waits, and read-back

For each preset, inspect `Q2/Q4/Q5`. The load-bearing fields are:

- `end_state_diff_set_config_vs_replay` — a structural diff over every snapshotted
  property, not selected scalars;
- `current_config_after_set_config`, `current_config_after_replay`, and
  `configuration_state_equal` — MM configuration-state bookkeeping is part of the
  end state;
- `set_config_ms`, `busy_immediately_after_set_config`, `wait_for_config_ms`, and
  replay `total_ms` plus every per-write `set_ms`/`wait_ms`; and
- every requested string, exact read-back string, and `exact` flag.

What this settles: whether a plain ordered property loop is observably equivalent on
the demo core; what `set_config` has completed when it returns; what additional wait
time `wait_for_config` consumes; the round-trip cost of per-device serialization;
and whether exact-string verification would reject values MM legitimately reformats.

An empty property diff is insufficient if configuration-state bookkeeping differs.
Conversely, a bookkeeping-only difference is still a real non-equivalence that the
executor design must resolve explicitly. Record the full diff rather than summarizing
it as “looks the same.”

## G4 — `Core.Shutter`

Inspect `Q3 Core.Shutter semantics`. Record the expansion values, active shutter,
`Core.AutoShutter`, every named shutter's `get_shutter_open(device)` value, whether
`set_property("Core", "Shutter", value)` was permitted, and the complete before/after
observation.

What this settles: whether the demo `Core.Shutter` property selects the active
shutter device or opens light, and whether that pseudo-device effect can be replayed
over this bridge. The property must not be admitted in Phase 4 if the call is blocked
or its observed effect cannot be represented and authorized. Demo evidence does not
authorize the same operation on M5.

## G5 — Partial failure and reversibility

Inspect `Q6 Partial failure and reversibility`. The scratch config contains three
ordered, distinct, writable enumerated properties; setting 2 has an intentionally
invalid value. Record for `set_config` and the ordered property loop:

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

- `still_present: false` for the scratch group;
- an empty `RESIDUE (must be empty)` list; and
- top-level JSON `exit_code: 0` and an empty `errors` list.

What this settles: `get_config_data` really re-reads a mutable definition, so the
TOCTOU threat is observable rather than hypothetical; the run did not silently
leave the demo core changed. Any residue is a gate failure even if all semantic
measurements otherwise look persuasive.

---

## Results — coordinator fills after the run

| Gate | Result | Evidence file / exact observation | Phase 4 consequence |
|---|---|---|---|
| G0 source + compile |  |  |  |
| G1 complete run / exit code |  |  |  |
| G2 all preset expansions and consecutive reads |  |  |  |
| G2 `Core.*` entries |  |  |  |
| G3 structural replay equivalence |  |  |  |
| G3 `Channel` bookkeeping equivalence |  |  |  |
| G3 busy/wait timings |  |  |  |
| G3 read-back formatting |  |  |  |
| G4 `Core.Shutter` bridge permission and semantics |  |  |  |
| G5 `set_config` partial failure |  |  |  |
| G5 property-loop partial failure |  |  |  |
| G5 reversibility |  |  |  |
| G6 mutable-definition re-read |  |  |  |
| G6 scratch deletion and final residue |  |  |  |

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
