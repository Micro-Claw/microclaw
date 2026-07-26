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

**PASS:** HEAD is `a28e5bd`; `status.txt` is empty; the pytest summary reads
**910 passed / 98 skipped / 3 warnings**. The 3 warnings are pre-existing on
`main`.

A Windows count that differs from 910/98/3 is itself a finding — record it before
continuing rather than proceeding past it.

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

- B0:
- B1:
- B2:
- B3:
- B4:
- B5:
- **Overall:**
