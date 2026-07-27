# design/33 Block 5 — acquisition/dose authorization rig gate prompts

Gate for `design33/dose-authorization` (impl `ff41de7`, coordinator-review fixes
`a28e5bd`). The branch is pushed; **do not merge until this gate passes.**

Every step runs **dark, with illumination disabled**. No step needs a sample. The
only acquisition is two frames at 5 ms in B5, and its purpose is to read the
ledger, not to acquire anything.

Keep one dated evidence directory for the whole gate: commands, stdout/stderr,
environment identity, config files used, artifacts, hashes, and a verdict per
section.

**The rig is Windows.** Commands are PowerShell and avoid Unix pipelines.
Redirection with `>` and `2>&1` behaves the same in PowerShell and `cmd`.

## What this gate can and cannot settle

Unlike Block 4's G1, this gate is a **regression check, not a discovery probe**.
Block 5 is almost entirely unit-testable and was unit-tested: 910 passed / 98
skipped / 3 warnings, coordinator-verified. The degraded-mode lockstep and the
planner-reachability tripwire were both re-verified off-rig by the coordinator.

What only the rig can settle is narrow, and it is worth being explicit about it:

1. That the **real reviewed M5 config** produces a `complete` map with all nine
   policy rows — i.e. that Block 4's required `acquisition:` section as actually
   authored on M5 satisfies Block 5's stricter finite-and-positive check.
2. That the eight `acquisition-tool:*` rows match the **live** tool registry on
   the rig, not the developer's.
3. That an incomplete dose policy fails **before any tool or app is reachable**
   in both CLI and `serve`, against real hardware and a real connection.
4. That plan and ledger arithmetic against **real camera geometry** is unchanged
   from Block 4.

Three things this gate deliberately does **not** test:

- **The human confirmation gate.** `design/33-block5-rig-probe.py` sets
  `CONFIRM_FN` to always return `True` so it can run unattended. Block 4's G3
  already proved the confirmation path and the attributable decline; Block 5
  changed nothing there. Do not read a B5 pass as evidence about confirmation.
- **Cancellation.** Still deferred to its own block with its abort trigger.
- **Durable session dose.** The ledger is an in-memory controller session and
  resets on process restart. Block 5 makes the map *say* that; it does not fix
  it. B1 checks the wording, not the behaviour.

## Setup

```powershell
$Repo     = "<repo>"
$Config   = "<reviewed rig safety config>"
$Evidence = "<dated evidence dir>"
$Scratch  = "<a path INSIDE the configured workspace>\block5"
$Port     = 4827   # replace if M5 does not use 4827

New-Item -ItemType Directory -Force $Evidence | Out-Null
New-Item -ItemType Directory -Force $Scratch  | Out-Null
Set-Location $Repo
```

`$Scratch` must be inside the configured workspace or B5 is refused by
`resolve_in_workspace` — that refusal would be correct behaviour, not a gate
failure, so get the path right first.

---

## B0. Preflight — identity

```powershell
git rev-parse HEAD > "$Evidence\head.txt" 2>&1
git status --short   > "$Evidence\status.txt" 2>&1
python -V            > "$Evidence\python.txt" 2>&1
python -m pytest -q  > "$Evidence\pytest.txt" 2>&1
```

**Do NOT set `MM_RUNNING` for this step.** The 910/98/3 baseline is the
*non-hardware* suite. Setting `MM_RUNNING=1` unlocks ~76 live-hardware
integration tests and the counts stop being comparable — see the B0 verdict
below, where that happened.

**PASS:** HEAD contains `a28e5bd` (the branch tip is `e68b0f5`, which adds this
document on top of it); `status.txt` is empty; the pytest summary reads
**910 passed / 98 skipped / 3 warnings**. The 3 warnings are pre-existing on
`main`.

A Windows count that differs from 910/98/3 is itself a finding — record it before
continuing rather than proceeding past it.

---

### B0 VERDICT — PASS with two findings (2026-07-27, M5)

HEAD `e68b0f5` (contains `a28e5bd`), worktree clean, Python **3.12.13** (the
branch was developed and coordinator-verified on 3.11.15 — a version delta, not
a failure; nothing in Block 5 is version-sensitive and the non-hardware suite is
fully green on both).

The operator set `$env:MM_RUNNING=1`, so the run was **986 tests, not 910**:
`9 failed, 977 passed, 22 skipped` in 135 s. The arithmetic settles what that
means: 98 skipped − 22 skipped = 76 hardware tests newly ran, and
910 + 76 = 986 = 977 + 9. **All 910 non-hardware tests passed.** Every failure is
in the newly-unlocked hardware set.

**Finding B0-1 — the nine failures are pre-existing fixture assumptions, not a
Block 5 regression.** Block 5 does not touch `tests/test_integration.py`
(verified against the diff). Seven fail with
`java.lang.Exception: No device with label "Camera"` — the fixtures hardcode the
demo config's device labels and M5's camera is named otherwise. The other two are
`test_get_available_channels_nonempty` (`assert 0 >= 1`: M5's `Channel` config
group is genuinely empty, which the map independently confirms —
`authorized_presets: []`) and `test_find_features_is_deterministic`
(`assert 1125 == 1436`: live-camera feature counts differ between two snaps, a
determinism assumption that does not hold on real sensor noise). None of these
touch authorization, planning, or the ledger. They are worth their own issue;
they are not Block 5's to fix and must not block this gate.

**Finding B0-2 — this gate document was wrong about the expected HEAD**, naming
`a28e5bd` when the branch tip is `e68b0f5` (this document itself was committed
after that criterion was written). Corrected above.

---

## B1. The complete map on the reviewed config

```powershell
python -m microclaw --port $Port --safety-config $Config authorization-map > "$Evidence\map-before.json" 2>&1
```

**PASS — all of:**

- Exits zero.
- `"verdict": "complete"` and `"complete": true`.
- Nine `acquisition-policy:*` rows, every one `built_in_typed_capability`.
- Eight `acquisition-tool:*` rows, every one `built_in_typed_capability`:
  `run_zstack`, `run_timelapse`, `run_multiposition_acquisition`,
  `run_tile_acquisition`, `run_multiposition_with_autofocus`,
  `run_adaptive_zstack`, `run_adaptive_timelapse`, `run_adaptive_survey`.
- **No** `acquisition-tool:run_mda` row, and `mmstudio-mda` is `excluded`.
- **No** row anywhere with `path` exactly `acquisition` and `capability`
  `exposure` — that row was the Block 5 defect and must be gone.
- The `max_session_illuminated_ms` row's detail says the ledger is an in-memory
  controller session that resets on process restart.

**FAIL:** any missing or extra row, a suspended verdict, `run_mda` admitted as an
acquisition tool, a surviving `exposure`-capability acquisition row, or a nonzero
exit.

A ninth `acquisition-tool:*` row is a real finding, not a rounding error: it means
a tool reaches the planner on the rig that does not on the developer's machine.

### B1 VERDICT — PASS (2026-07-27, M5, `map-before.json`)

`mode: guaranteed`, `verdict: complete`, `complete: true`, 56 entries. Every B1
criterion met:

- Nine `acquisition-policy:*` rows, all `built_in_typed_capability`, each
  carrying its configured value.
- Eight `acquisition-tool:*` rows, exactly the expected eight, all
  `built_in_typed_capability`.
- No `acquisition-tool:run_mda`; `mmstudio-mda` is `excluded`.
- No surviving `path="acquisition", capability="exposure"` row.
- The session row reads "in-memory controller-session illuminated-time maximum;
  **resets on process restart**".

**The entry count reconciles exactly against Block 3b**, which is the strongest
evidence here that Block 5 changed nothing it should not have: Block 3b's M5 map
was 40 entries. This map has 39 non-acquisition rows + 17 new acquisition rows =
56, and 39 + 1 (the deleted `acquisition`/`exposure` row) = 40. The 20-device
excluded inventory, the four illumination rows, and the seven `declared`
categorical pairs are all unchanged.

**Finding B1-1 — the declared dose budgets are the shipped example's values
verbatim, not M5-reviewed numbers.** All nine match
`microclaw/safety_config.example.yaml` exactly (10000 frames, 3600 s, 50 GB,
600 000 ms illuminated, 1 800 000 ms session, and the four `confirm_above_*`).
B1 therefore proves the *mechanism* — that the map enforces, reports, and
completes over a dose policy — and does **not** establish that 10 minutes of
illumination per plan or 30 minutes per session is right for M5. The same config
still carries `camera.max_exposure_ms: 1000.0` marked
`# <-- REPLACE with a safe max for the Hamamatsu` while `reviewed: true` is
already set. That is a rig-config review item, not a Block 5 code defect, but it
is exactly the kind of nominally-reviewed-yet-unexamined limit design/32
Finding 1 exists to surface, and it should be settled before these budgets are
described as reviewed.

**Finding B1-2 — nothing auto-classified on this config.** All seven categorical
pairs report `source: declared`; there are no `auto:state-device` rows, because
the operator declared *both* `Label` and `State` for each wheel and the ELL6, and
Block 3b's vacuum-filling rule correctly stands down where a ruling exists. The
known open item stands unchanged: `iChrome-MLE-TCP.Label` remains a bare
categorical write on a laser engine.

---

## B2. Produce an incomplete policy **without touching the reviewed config**

```powershell
$IncompleteConfig = "$Evidence\safety_config.incomplete.yaml"

python design\33-block5-make-incomplete-config.py --source $Config --output $IncompleteConfig > "$Evidence\make-incomplete.txt" 2>&1
python -m microclaw --port $Port --safety-config $IncompleteConfig authorization-map > "$Evidence\map-incomplete.txt" 2>&1
```

The helper refuses to write over its source and never edits `$Config` in place.
Confirm that for yourself: `$Config`'s modified timestamp must be unchanged.

**PASS:** the helper reports it removed `acquisition.confirm_above_illuminated_ms`
and that the source is unchanged; `authorization-map` exits **nonzero**; the
output names `acquisition.confirm_above_illuminated_ms`; no complete map is
emitted.

**FAIL:** the reviewed config changed on disk, the command starts normally, or a
complete map appears.

**Caveat worth knowing before you interpret a failure here:** the helper
round-trips the YAML through `safe_load`/`safe_dump`, so the incomplete copy
loses comments and normalizes formatting. If startup refuses while naming a field
*other than* `confirm_above_illuminated_ms`, suspect the round-trip, not the
gate — diff the two files before recording a finding.

### B2 VERDICT — PASS, off-rig (2026-07-27)

`authorization-map` exits **1** with
`acquisition.confirm_above_illuminated_ms: missing required key`, file-anchored,
and the source config is byte-identical afterwards. No map emitted.

**Finding B2-1 — this section does not test Block 5, and this gate was wrong to
imply it needs a rig.** The refusal happens in `load_safety_config_or_exit`
(`__main__.py:172`, `:247`) — *before* `MicroscopeController` is constructed
(`:176`, `:250`), before `validate_live_rig` (`:183`, `:259`), and before the
prompt (`:209`). So it needs no Micro-Manager, no demo config, and no rig at all.

More importantly, it exercises **Block 4's required-section parsing**, not
Block 5's new finite-and-positive check inside `validate_live_rig`. That check is
**unreachable from any config file**: every invalid form is already rejected at
parse. Measured, on the shipped example with `confirm_above_illuminated_ms` set
to each of:

| value | outcome |
|---|---|
| *(removed)* | rejected at parse |
| `.inf` | rejected at parse |
| `.nan` | rejected at parse |
| `0` | rejected at parse |
| `-5` | rejected at parse |
| `true` | rejected at parse |

Block 5's map check is therefore **defense-in-depth against a directly
constructed `ParsedSafetyConfig`** — the seam Block 2 opened and this block was
asked to close — and its coverage is the parametrized unit test
`test_missing_or_partial_direct_dose_policy_fails_closed`, not this gate. Keep
B2/B3 as a cheap end-to-end regression on the startup refusal chain; do not
record them as evidence about Block 5.

---

## B3. CLI and web fail closed, without hanging the operator

`serve` is expected to refuse and exit. If Block 5 has regressed it will instead
start a server and block forever, so run it bounded — **a still-running job at 10
seconds IS the failure**, not a timeout artifact.

```powershell
$ServeJob = Start-Job -ScriptBlock {
    param($RepoPath, $ConfigPath, $RigPort)
    Set-Location $RepoPath
    python -m microclaw --port $RigPort --safety-config $ConfigPath serve
} -ArgumentList $Repo, $IncompleteConfig, $Port

$Completed = Wait-Job $ServeJob -Timeout 10
if ($null -eq $Completed) {
    "FAIL: serve remained running for 10 seconds; aborting regressed server" > "$Evidence\serve-incomplete.txt"
    Stop-Job $ServeJob
} else {
    Receive-Job $ServeJob > "$Evidence\serve-incomplete.txt" 2>&1
}
Remove-Job $ServeJob -Force
```

**PASS:** the job completes on its own inside 10 seconds; the output names
`acquisition.confirm_above_illuminated_ms`; no browser or app becomes reachable;
no hardware mutation occurs.

**FAIL:** the job is still running at 10 s (the script stops it and records the
failure), any app becomes reachable, or any tool or hardware mutation occurs.

### B3 VERDICT — PASS, off-rig (2026-07-27)

`serve` exits **1** immediately with the same file-anchored error and never binds
a port. The bounded-job wrapper above is unnecessary in practice — the refusal
precedes any server construction — but keep it, because its purpose is to bound
the *regressed* case, which by definition would not exit.

Finding B2-1 applies here too: this is a Block 4 regression test.

---

## B4. The reviewed config still produces the identical map

```powershell
python -m microclaw --port $Port --safety-config $Config authorization-map > "$Evidence\map-after.json" 2>&1
Compare-Object (Get-Content "$Evidence\map-before.json") (Get-Content "$Evidence\map-after.json") > "$Evidence\map-diff.txt"
Get-FileHash "$Evidence\map-before.json" >  "$Evidence\map-hashes.txt"
Get-FileHash "$Evidence\map-after.json"  >> "$Evidence\map-hashes.txt"
```

**PASS:** exits zero; `map-diff.txt` is empty; both hashes identical.

**FAIL:** any difference, or a non-complete verdict.

This is the Block 3b before/after pattern, which is what caught that block's real
defect. A difference here means B2 mutated something it should not have.

**Scope note after B2-1:** if B2/B3 were run off-rig against a *copy* (which is
now the recommended way), the reviewed M5 config was never at risk and this
section's non-mutation purpose is moot. What remains is map determinism — re-run
the map on M5 and diff against the retained `map-before.json`. That is one
command and worth doing while the rig is open, but it is no longer load-bearing.

### B4b. Prove the MDA exclusion holds live — no acquisition

This is the one **live fail-closed** check Block 5 can actually contribute, and
it takes no exposure: `run_mda` has no `acquisition-tool:` row and `mmstudio-mda`
is `excluded`, so dispatch must refuse it before it touches anything. Call
`run_mda` through the normal tool path with any token value and confirm the
refusal, with no MDA started and no hardware effect. This pins the claim that the
new dose policy did not quietly become the thing that admits MDA.

Scripted as `design/33-block5-b4b-mda-refusal-probe.py`. It runs on the **demo
core** as well as on M5 — the refusal happens before any MMStudio contact.

```powershell
python design\33-block5-b4b-mda-refusal-probe.py --config <config> --port 4827 > "$Evidence\b4b-mda-refusal.txt" 2>&1
```

On the demo machine that is:

```powershell
python design\33-block5-b4b-mda-refusal-probe.py --config design\33-block5-demo-safety-config.yaml --port 4827 > b4b-mda-refusal.txt 2>&1
```

The probe first tries `get_mda_settings` to obtain a **real** preview token, so
the refusal cannot be explained away as a stale-token rejection; if MMStudio is
unreachable it falls back to a placeholder and says so in the `TOKEN` line.
Either way the authorization gate runs before the token is examined. It then
checks the ledger did not move, and prints its own `VERDICT PASS`/`FAIL`.

**PASS** requires all four: `mmstudio_mda` is `["excluded"]`,
`acquisition_tool_run_mda_row` is `false`, `RESULT` names the excluded
`mmstudio-mda` path, and the ledger is unchanged at zero. The probe's final line
states the verdict; anything other than `VERDICT PASS` is a finding.

**PASS — the exact observed shape** (measured against fakes, 2026-07-27):
`execute_tool` **returns** a JSON error, it does not raise:

```json
{"error": "RigAuthorizationError: The mmstudio-mda write path is excluded from the Phase-1 authorization map.", "hint": "This may be a hardware error (device busy, stage at limit, device not found) or a connection problem."}
```

An earlier draft of this section said "confirm a `RigAuthorizationError`", which
would have had the operator looking for a traceback that never comes. Note also
that the generic `hint` is misleading for an authorization refusal — that is a
pre-existing wart in `execute_tool`'s error handling, not a Block 5 defect, and
worth its own cleanup.

### B4b VERDICT — PASS on a demo core, with a real token (2026-07-27)

```
MAP    {"acquisition_tool_run_mda_row": false, "complete": true, "mmstudio_mda": ["excluded"], "verdict": "complete"}
TOKEN  {"kind": "real"}
RESULT {"error": "RigAuthorizationError: The mmstudio-mda write path is excluded from the Phase-1 authorization map.", ...}
LEDGER {"frames_before": 0, "frames_after": 0, "illuminated_ms_after": 0.0}
VERDICT PASS
```

`TOKEN kind: real` is what makes this conclusive. MMStudio was reachable,
`get_mda_settings` returned a **valid** preview token, and `run_mda` refused it
anyway. The refusal is therefore the authorization gate, not a stale-token
rejection — the alternative explanation is ruled out by evidence rather than by
reading the code. The ledger never moved, and the dose policy did not grow an
`acquisition-tool:run_mda` row.

This closes the one live fail-closed check specific to Block 5.

---

## B5. Authorized plan and ledger regression against Block 4

Two frames, 5 ms, dark, illumination disabled.

```powershell
python design\33-block5-rig-probe.py --config $Config --save-dir $Scratch --port $Port --frames 2 --exposure-ms 5 > "$Evidence\complete-probe.txt" 2>&1
```

**PASS — all of:**

- `MAP.complete` is `true`, `MAP.verdict` is `complete`.
- `MAP.dose_rows` holds the nine policy rows and the eight tool rows.
- `MAP.mda` is exactly `["excluded"]`.
- `PLAN.frames` 2; `PLAN.exposure_ms_per_frame` 5.0; `PLAN.illuminated_ms` 10.0;
  `PLAN.estimated_bytes` matches the live camera geometry.
- `RESULT` reports a completed two-frame timelapse with no authorization error.
- `LEDGER.frames` 2; `LEDGER.illuminated_ms` 10.0; `LEDGER.bytes` equals
  `PLAN.estimated_bytes`.
- These plan and ledger values match Block 4's behaviour for the same request.

**FAIL:** altered planner arithmetic, missing ledger accounting, a dose
authorization refusal on the complete config, MDA admitted, or any hardware
effect before `validate_live_rig` returns.

Remember the probe auto-confirms. `RESULT` showing a completed acquisition is
evidence about authorization and accounting, not about the confirmation gate.

### B5 VERDICT — PASS on M5 (2026-07-27)

```
MAP    complete=true, verdict=complete, 9 policy + 8 tool rows, mda=["excluded"]
PLAN   frames=2, exposure_ms_per_frame=5.0, illuminated_ms=10.0, bytes=21233664
RESULT {"status": "Timelapse complete.", "dataset_path": "C:\\Users\\ries\\data\\microclaw\\block5\\block5_authorized_1"}
LEDGER {"bytes": 21233664, "frames": 2, "illuminated_ms": 10.0}
```

Every criterion met on the rig itself. `LEDGER.bytes == PLAN.estimated_bytes`
exactly; frames and illuminated time match the plan; the dataset landed inside
the configured workspace.

**The byte figure is the check that the planner read live geometry rather than a
default:** 21 233 664 = 2 × 2304 × 2304 × 2, i.e. a 2304×2304 16-bit sensor
(M5's Hamamatsu). Compare the demo core's 1 048 576 = 2 × 512 × 512 × 2 from the
same probe and the same arguments. Same code, three different cameras
(fake 512×512×1, demo 512×512×2, M5 2304×2304×2), each planned and accounted
correctly.

**Both dose-relevant quantities are exact, as design/32's Block 4 note claims.**
Frames and illuminated time are computed, not estimated. Only
`estimated_duration_s` remains a known-low bound — Block 4's G6 measured actual
at ≈7.6× the exposure-only estimate — which is why `max_duration_s` bounds the
estimate rather than wall time. Nothing here changes that.

### B5 REHEARSAL — PASS against fakes (2026-07-27)

The probe file was executed **unmodified** via `runpy` against a fake controller
shaped after `MMConfig_demo.cfg`'s real device set (512×512, 8-bit, XY/Z/Camera,
StateDevice filter wheels, ShutterDevice shutters), with `tools.Acquisition`
replaced by the same fake `tests/test_acquisition_budgets.py` pins. Output:

```
MAP    complete=true, verdict=complete, 9 policy + 8 tool rows, mda=["excluded"]
PLAN   frames=2, exposure_ms_per_frame=5.0, illuminated_ms=10.0, bytes=524288
RESULT {"status": "Timelapse complete.", ...}
LEDGER frames=2, illuminated_ms=10.0, bytes=524288   (== PLAN.estimated_bytes)
```

Every B5 criterion is satisfiable and the arithmetic is self-consistent
(2 × 512 × 512 × 1 = 524 288). Two Block 4 invariants held incidentally: the fake
asserts `image_process_fn` is never passed, and that `acquire()` receives a
**list**, not a generator.

**What this rehearsal establishes:** the probe's argparse surface, every API name
and signature it touches, dispatch through Block 5's new `execute_tool` gate, and
ledger accounting are all correct. The class of error that would otherwise burn a
rig session — a wrong kwarg or a renamed attribute — is ruled out.

**What it does not establish**, and what a real core still must: pyjavaz bridge
semantics (the exact class of error that Block 4's G5 caught, where guessed field
and method names were wrong), a real `Acquisition` and NDTiff write, and real
camera geometry. `ctrl.studio` was `None` throughout and the probe never touched
it, which is itself a useful finding: B5 does not require MMStudio.

---

## Optional: rehearse B5 + B4b on a Micro-Manager demo core

Strictly more valuable than the fake rehearsal above, because it exercises the
real pyjavaz bridge and a real `Acquisition`. Needs any machine running MM with
`MMConfig_demo.cfg` and the pycro-manager ZMQ server on port 4827.

A ready profile is committed: **`design/33-block5-demo-safety-config.yaml`**.
Set its `workspace_dir` to a real directory, then:

```powershell
python -m microclaw --port 4827 --safety-config design\33-block5-demo-safety-config.yaml authorization-map > demo-map.json 2>&1
python design\33-block5-rig-probe.py --config design\33-block5-demo-safety-config.yaml --save-dir <workspace_dir>\block5 --port 4827 --frames 2 --exposure-ms 5 > demo-probe.txt 2>&1
```

Expect `verdict: complete`, ~39 entries, 10 `auto:state-device` rows (the five
demo StateDevices × Label/State), `authorized_presets: []`, and the same
PLAN/LEDGER agreement as the fake rehearsal — though `estimated_bytes` will
follow the demo camera's actual geometry rather than 524 288.

**Why `channels: allowed: []` is not optional here.** Every demo Channel preset
sets `Core,Shutter` alongside its filter labels. `Core.Shutter` is not a
StateDevice, is not in the illumination block, and cannot be declared
categorical, so guaranteed mode refuses startup outright:

```
Live rig authorization failed:
- Allowed channel preset 'FITC' is not fully classified: Core.Shutter is unclassified
```

That is correct fail-closed behaviour, not a bug — presets are design/33
Phase 4 — but it will stop a demo run dead if the profile omits the empty
allowlist. Measured 2026-07-27.

Anything that fails on a demo core but passed the fake rehearsal is almost
certainly a **bridge** finding, and should be treated the way G5 was.

### DEMO RUN — map PASS, acquisition BLOCKED by an operator-path mistake (2026-07-27)

**The map passed on a real core, and this is the first live confirmation of
Block 5 outside M5.** 41 entries, `verdict: complete`, nine policy rows, the
exact eight tool rows, no `acquisition-tool:run_mda`, `mmstudio-mda` `excluded`,
no surviving legacy `acquisition`/`exposure` row, `authorized_presets: []`.

Two findings worth keeping:

- **The shutter carve-out holds on a real core.** Twelve rows auto-classified as
  `auto:state-device` — `Dichroic`, `Emission`, `Excitation`, `Objective`,
  `Path` and `LED`, each × `Label`/`State` — while **`White Light Shutter` and
  `LED Shutter` landed in the excluded inventory**, not auto-classified. That is
  Block 3b's carve-out working against live MM device typing rather than a
  fixture. (This gate predicted 10 auto rows; the real count is 12 because the
  demo `LED` is also a StateDevice. A prediction miss, not a defect.)
- **Real camera geometry differs from the fake**, as expected:
  `estimated_bytes` 1 048 576 = 2 × 512 × 512 × **2**, so the demo camera is
  16-bit where the fake assumed 8. The plan arithmetic followed the live core
  correctly.

**The acquisition did not run.** `workspace_dir` was left at this gate's
placeholder while `--save-dir` pointed elsewhere, so the guard refused:

```
RESULT {"error": "Safety constraint prevented this action: Path 'D:\\microclaw_block5\\block5' escapes the configured workspace directory (D:\\REPLACE\\with\\a\\real\\directory)."}
LEDGER {"bytes": 0, "frames": 0, "illuminated_ms": 0.0}
```

That refusal is correct behaviour, and the zeroed ledger is itself evidence:
**the reservation was rolled back on a failed acquisition**, live — one of the
partial-failure semantics Block 4 left unit-pinned only. But B5's own criteria
(a completed timelapse, `LEDGER.frames == 2`) are **not** met, so **B5 remains
open**.

**Fix applied to the probe, not to the instructions.** The refusal previously
surfaced only after `MAP` and `PLAN` had printed, which reads like a Block 5
failure when it is a config-path mistake. `design/33-block5-rig-probe.py` now
resolves `--save-dir` against the workspace immediately after loading the config
and exits with a message naming both paths, **before any hardware contact**:

```
FAIL (before any hardware contact): --save-dir '/tmp/block5' is not inside the
configured workspace_dir '/REPLACE/with/a/real/directory'.
```

This matters more on M5 than on the demo: the same mistake would have cost a rig
session. Re-run the demo probe with `workspace_dir` set to a real directory to
close B5's acquisition path.

### DEMO RUN 2 — B5 PASS on a real core (2026-07-27)

Re-run with `workspace_dir` set. **Every B5 criterion met**, through the real
pyjavaz bridge, a real `Acquisition`, and a real NDTiff write:

```
MAP    complete=true, verdict=complete, 9 policy + 8 tool rows, mda=["excluded"]
PLAN   frames=2, exposure_ms_per_frame=5.0, illuminated_ms=10.0, bytes=1048576
RESULT {"status": "Timelapse complete.", "dataset_path": "D:\\microclaw_block5\\block5\\block5_authorized_1"}
LEDGER {"bytes": 1048576, "frames": 2, "illuminated_ms": 10.0}
```

`LEDGER.bytes == PLAN.estimated_bytes` exactly, frames and illuminated time
match the plan, and the dataset landed inside the workspace. The `_1` suffix is
AcqEngJ's rename, already accounted for in `resolve_in_workspace`'s comment.

**This is the finding that most reduces merge risk.** The fake rehearsal could
not exercise pyjavaz, and Block 4's G5 proved that guessed bridge semantics are
exactly where this project's errors hide. Block 5's path — map construction,
`execute_tool` dose gate, planner, reservation, `image_saved_fn` accounting,
rollback — now has live-bridge evidence end to end.

**B4 (determinism) is also satisfied here.** Two independent runs against the
same core produced structurally identical maps: 41 entries, 9 + 8 acquisition
rows, the same 12 `auto:state-device` rows, the same five-device excluded
inventory, `authorized_presets: []`, `mmstudio-mda` excluded.

**Gate defect found in B4's comparison method.** The two raw files differed in
size (29 276 vs 28 490 bytes) while the maps were identical. The delta is a
PowerShell stderr preamble captured by `2>&1` — `uv : Connecting to
Micro-Manager...` plus an echo of the command line, whose length varies with the
command. **`Compare-Object` on raw lines will therefore report spurious
differences.** For B4 on M5, compare the *parsed JSON*, not the file bytes:

```powershell
$a = (Get-Content "$Evidence\map-before.json" -Raw); $a = $a.Substring($a.IndexOf("{")) | ConvertFrom-Json
$b = (Get-Content "$Evidence\map-after.json"  -Raw); $b = $b.Substring($b.IndexOf("{")) | ConvertFrom-Json
($a | ConvertTo-Json -Depth 10) -eq ($b | ConvertTo-Json -Depth 10)
```

Expect `True`. The hash comparison in B4 has the same flaw and should be read as
informational only.

## Verdict

Record per section, then overall. Stop and return the branch to the coordinator on
any FAIL; do not merge and do not fix on the rig.

- B0: **PASS** with findings B0-1 (pre-existing hardware-fixture failures) and
  B0-2 (stale HEAD criterion in this doc, corrected). 2026-07-27.
- B1: **PASS**, reconciles exactly against Block 3b's 40-entry map. Findings B1-1
  (budgets are the shipped example's values, not M5-reviewed) and B1-2 (nothing
  auto-classified; `iChrome-MLE-TCP.Label` open item unchanged). 2026-07-27.
- B2: **PASS**, off-rig, 2026-07-27. Finding B2-1: tests Block 4's parse layer,
  not Block 5; needs no rig, no MM, no demo config.
- B3: **PASS**, off-rig, 2026-07-27. Same scope caveat.
- B4: **PASS on a demo core** (two runs, structurally identical maps),
  2026-07-27. Comparison-method defect found and corrected — compare parsed
  JSON, not raw bytes. Not yet run on M5.
- B4b: **PASS on a demo core with a real preview token**, 2026-07-27. The
  strongest available form: the stale-token explanation is excluded by evidence.
- B5: **PASS on a demo core and on M5**, 2026-07-27, through the real pyjavaz
  bridge and a real Acquisition. Ledger matched the plan exactly on both, with
  the byte count following each camera's live geometry.
- **Overall: PASS (2026-07-27). Cleared to merge.** Every section passed, and
  B1/B5 passed on M5 itself against its own config — declared illumination,
  seven declared categorical pairs, a 2304×2304 16-bit camera.

**What the gate actually found.** Three defects, **all of them in this gate
document and its scripts, none in Block 5's code**:

1. B2/B3 could not test what this document claimed they tested (finding B2-1).
2. B4's `Compare-Object` would have reported spurious differences from a
   PowerShell stderr preamble.
3. The B5 probe surfaced a save-dir/workspace mismatch only after `MAP` and
   `PLAN` had printed, which on M5 would have cost a session.

Block 5's implementation took no corrections from the rig. Its two real defects
— degraded-mode rows claiming a capability they did not have, and a tripwire
that missed one of its own eight entry points — were both caught in coordinator
review before the branch ever reached hardware. That is the intended division of
labour: review catches what is knowable statically, the rig catches what is not.

**What this gate does not establish**, restated so a PASS is not over-read: the
human confirmation gate (the probe self-confirms), cancellation (deferred with
its abort trigger to a later block), durable session dose (the ledger is still
in-memory and resets on restart — the map now says so), and whether M5's
declared budgets are *appropriate* rather than merely present (finding B1-1:
they are the shipped example's values verbatim).
