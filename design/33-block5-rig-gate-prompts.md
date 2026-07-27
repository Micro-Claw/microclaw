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
`run_mda` through the normal tool path and confirm a `RigAuthorizationError`
naming the excluded path, with no MDA started and no hardware effect. This pins
the claim that the new dose policy did not quietly become the thing that admits
MDA.

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

---

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
- B4: not yet run — reduced to a determinism diff by B2-1.
- B4b: not yet run — the one live fail-closed check specific to this block.
- B5: not yet run.
- **Overall: INCOMPLETE — do not merge.** What genuinely remains on M5 is **B5**
  (plan and ledger against real camera geometry) plus **B4b** (MDA refused live,
  no exposure) and **B4** as a cheap determinism diff. Total rig cost: two dark
  frames at 5 ms. B1 already covered the map contents and reconciles exactly
  against Block 3b.
