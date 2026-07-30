# design/35 Block 2 — undeclared-light-source M5 rig gate

Gate for `design33/undeclared-light-source-gate`, implementation commits
`ef72b15` and `e9817ad`. The branch is pushed at
`origin/design33/undeclared-light-source-gate`; **do not merge until this gate
passes and the coordinator reviews the evidence.** Do not open a PR.

This gate can command a real laser enable. A qualified M5 operator must be
present for G3–G5. Establish the rig's normal optical containment, operator-
approved safe power/state, and emergency-stop procedure first. Treat an enable
as emissive even when power is nominally zero. Never guess a device, property,
`on_value`, or `off_value`. Stop immediately on unexpected read-back or failed
cleanup and put the rig in its operator-approved safe state.

Commands are PowerShell-safe and run from the repository root. Global options
`--port` and `--safety-config` come **before** `authorization-map` or `serve`.
Keep commands and output in one dated evidence directory. Interactive sessions
use `Start-Transcript`; redirecting an interactive session to a file can hide
the prompts the operator must answer.

## What this gate settles

It settles four live-rig facts:

1. The running M5 installation's EMU semantic laser enables are cross-checked
   against the real reviewed config at startup.
2. A missing declaration refuses with an actionable device, property, and YAML
   location; a correct declaration produces a complete authorization map.
3. The declared pair stays on the existing confirmation-gated illumination
   path.
4. Session shutdown writes the declared `off_value` and the operator can verify
   it independently.

It does not prove discovery found every physical emission path, and it does not
protect Block 4's pre-validation enumeration window.

## G0 — setup, identity, and immutable backup

Replace every angle-bracket value before running anything. `$Config` is the
real reviewed M5 profile; do not use a generated example.

```powershell
$Repo       = "<absolute repo path>"
$Config     = "<absolute deployed M5 safety YAML>"
$Port       = 4827
$Stamp      = Get-Date -Format "yyyyMMdd-HHmmss"
$EvidenceRoot = "<absolute evidence parent OUTSIDE the repo>"
$Evidence   = Join-Path $EvidenceRoot "block2-m5-$Stamp"
$Backup     = Join-Path $Evidence "deployed-safety.before.yaml"
$MMConfig   = "<absolute loaded M5 .cfg path>"

Set-Location $Repo
New-Item -ItemType Directory -Path $Evidence | Out-Null
git fetch origin design33/undeclared-light-source-gate > "$Evidence\git-fetch.txt" 2>&1
git switch --detach origin/design33/undeclared-light-source-gate > "$Evidence\git-switch.txt" 2>&1
git rev-parse HEAD > "$Evidence\head.txt" 2>&1
git merge-base --is-ancestor e9817ade02ac7e076997cda60a00fbb0c9cc217c HEAD
$LASTEXITCODE > "$Evidence\contains-implementation-tip.txt"
git status --short > "$Evidence\status.txt" 2>&1
python -V > "$Evidence\python.txt" 2>&1
git diff --check 98842cfc00281e442ceacd7ef345686851943c8d..HEAD > "$Evidence\diff-check.txt" 2>&1
Copy-Item -LiteralPath $Config -Destination $Backup
Get-FileHash -Algorithm SHA256 -LiteralPath $Config > "$Evidence\config-before-sha256.txt"
Get-FileHash -Algorithm SHA256 -LiteralPath $Backup > "$Evidence\backup-sha256.txt"
Get-FileHash -Algorithm SHA256 -LiteralPath $MMConfig > "$Evidence\mm-config-sha256.txt"
Get-ComputerInfo -Property WindowsProductName,WindowsVersion,OsBuildNumber > "$Evidence\windows.txt"
```

Record the Micro-Manager/MMCore version shown in Help → About as
`$Evidence\micro-manager-version.txt`. Also record the rig identifier and the
loaded `.cfg` path:

```powershell
"rig_id=<M5 rig identifier>" > "$Evidence\rig.txt"
"mm_config=$MMConfig" >> "$Evidence\rig.txt"
```

**PASS:** `head.txt` is the fetched
`origin/design33/undeclared-light-source-gate` tip,
`contains-implementation-tip.txt` is `0`, `status.txt` and `diff-check.txt` are
empty, and the source and backup hashes match. Stop otherwise. This avoids an
impossible self-reference where committing a new runbook hash would make the
hash written inside that same runbook stale.

Run the deterministic off-rig evidence on the rig checkout:

```powershell
python -m pytest tests\test_authorization.py -q > "$Evidence\g0-authorization-tests.txt" 2>&1
```

Expected at this commit: **68 passed**.

## G1 — current deployed config

This command is read-only. It connects, validates, prints the map if complete,
and exposes no mutation tools:

```powershell
python -m microclaw --port $Port --safety-config $Config authorization-map > "$Evidence\g1-current-map.txt" 2>&1
$LASTEXITCODE > "$Evidence\g1-exit-code.txt"
```

If startup refuses, copy the exact, unedited refusal into the gate report. It
must name the EMU slot, exact device, exact property,
`constraints.illumination.shutters`, and this editable top-level shape:

```yaml
illumination:
  shutters:
    - device: <exact device>
      property: <exact property>
```

It must not invent `on_value` or `off_value`. Write the exact values returned by
the refusal into PowerShell variables only after the operator verifies them:

```powershell
$Device = "<exact device from refusal/reviewed evidence>"
$Property = "<exact property from refusal/reviewed evidence>"
$OnValue = "<operator-verified enable value>"
$OffValue = "<operator-verified safe/off value>"
```

If G1 succeeds, find every EMU semantic enable in the JSON and verify its exact
pair already exists under `illumination.shutters`. Set the variables above from
that reviewed declaration. **Do not remove a production declaration merely to
manufacture a refusal.** The G0 test fixture is the missing-declaration evidence
when the deployed config is already correct.

## G2 — correct a missing declaration without losing the original

If G1 already succeeded, do not edit or copy the profile. Set the tested profile
and continue to G3:

```powershell
$GateConfig = $Config
```

If G1 refused for a missing declaration, never edit `$Config` in place. Make a
gate copy:

```powershell
$GateConfig = Join-Path $Evidence "m5-safety.block2.yaml"
Copy-Item -LiteralPath $Config -Destination $GateConfig
notepad $GateConfig
```

The operator adds the exact pair beneath the existing top-level
`illumination.shutters`. Add explicit `on_value` and `off_value` when the
reviewed hardware values differ from schema defaults. Change nothing else.
After saving:

```powershell
git diff --no-index -- $Backup $GateConfig > "$Evidence\g2-config-diff.txt" 2>&1
Get-FileHash -Algorithm SHA256 -LiteralPath $GateConfig > "$Evidence\g2-config-sha256.txt"
python -m microclaw --port $Port --safety-config $GateConfig authorization-map > "$Evidence\g2-corrected-map.json" 2>&1
$LASTEXITCODE > "$Evidence\g2-exit-code.txt"
```

`git diff --no-index` normally exits 1 when files differ; that is not a gate
failure. Review `g2-config-diff.txt` and stop if anything beyond the intended
shutter declaration changed.

**PASS:** the command exits zero, the map says `"verdict": "complete"`, and the
exact pair has `"path": "dedicated-illumination"` and
`"capability": "illumination"`.

## Read-only independent property check

Use this after every interactive limb. It bypasses the agent but performs only
a read:

```powershell
python -c "from pycromanager import Core; c=Core(port=$Port); print(c.get_property(r'$Device', r'$Property'))" > "$Evidence\property-readback.txt" 2>&1
```

Use a distinct filename each time as shown below. If quoting in the real device
or property name makes this command invalid, stop and record that; do not alter
the name to make the command run.

## G3 — the real human confirmation path

Start an interactive CLI session and preserve the console transcript:

```powershell
Start-Transcript -Path "$Evidence\g3-confirmation-transcript.txt"
python -m microclaw --port $Port --safety-config $GateConfig
```

Paste these prompts one at a time, substituting the verified values literally:

> Use `get_device_property` to read device `<DEVICE>`, property `<PROPERTY>`.
> Report the raw value exactly and make no hardware changes.

> Use `set_device_property` to set device `<DEVICE>`, property `<PROPERTY>` to
> `<ON_VALUE>`. Do not use another tool. Ask for the required human
> confirmation before writing.

At the confirmation prompt, answer **no**. Then paste:

> Use `get_device_property` to read device `<DEVICE>`, property `<PROPERTY>`
> again. Report the raw value exactly.

Type `exit`, then end transcript capture:

```powershell
Stop-Transcript
python -c "from pycromanager import Core; c=Core(port=$Port); print(c.get_property(r'$Device', r'$Property'))" > "$Evidence\g3-after-decline-readback.txt" 2>&1
```

**PASS:** the declined write never lands and both read-backs remain at the
pre-test safe value. A natural-language refusal without read-back is not enough.

## G4 — accepted enable and normal-exit cleanup

Run only with explicit operator authorization and the rig in the safe condition
defined at the top of this document.

```powershell
Start-Transcript -Path "$Evidence\g4-normal-exit-transcript.txt"
python -m microclaw --port $Port --safety-config $GateConfig
```

Paste:

> Use `set_device_property` to set device `<DEVICE>`, property `<PROPERTY>` to
> `<ON_VALUE>`. Do not use another tool. Ask for the required human
> confirmation before writing.

Accept the confirmation. Immediately paste:

> Use `get_device_property` to read device `<DEVICE>`, property `<PROPERTY>`.
> Report the raw value exactly.

Require the exact `<ON_VALUE>` read-back, then type `exit`. The console must say
that illumination was turned off. Finish and independently verify:

```powershell
Stop-Transcript
python -c "from pycromanager import Core; c=Core(port=$Port); print(c.get_property(r'$Device', r'$Property'))" > "$Evidence\g4-after-normal-exit-readback.txt" 2>&1
```

**PASS:** accepted enable reads exactly `<ON_VALUE>` and post-exit read-back is
exactly `<OFF_VALUE>`.

## G5 — Ctrl-C cleanup

Repeat G4 in a fresh session. After accepted enable and exact read-back, press
**Ctrl-C once while the CLI is waiting at `You:`** instead of typing `exit`.

```powershell
Start-Transcript -Path "$Evidence\g5-ctrl-c-transcript.txt"
python -m microclaw --port $Port --safety-config $GateConfig
```

Use the same two G4 prompts, accept only with operator authorization, verify the
enabled read-back, then press Ctrl-C. After the process returns:

```powershell
Stop-Transcript
python -c "from pycromanager import Core; c=Core(port=$Port); print(c.get_property(r'$Device', r'$Property'))" > "$Evidence\g5-after-ctrl-c-readback.txt" 2>&1
```

**PASS:** post-Ctrl-C read-back is exactly `<OFF_VALUE>`.

Do not manufacture a Python/bridge crash on live hardware to test the exception
limb. The shared `finally` path is covered deterministically off-rig. Record the
live controlled-exception limb as **not run — unsafe failure injection** unless
the coordinator separately supplies a reviewed probe.

## G6 — web-server Ctrl-C, only if M5 deploys `serve`

Run only if web mode is an actual M5 deployment path and the operator authorizes
another enable cycle:

```powershell
Start-Transcript -Path "$Evidence\g6-web-transcript.txt"
python -m microclaw --port $Port --safety-config $GateConfig serve
```

In the browser paste:

> Use `set_device_property` to set device `<DEVICE>`, property `<PROPERTY>` to
> `<ON_VALUE>`. Do not use another tool. Ask for the required human
> confirmation before writing.

Accept, then paste:

> Use `get_device_property` to read device `<DEVICE>`, property `<PROPERTY>`.
> Report the raw value exactly.

After exact enabled read-back, press Ctrl-C once in the server console. Then:

```powershell
Stop-Transcript
python -c "from pycromanager import Core; c=Core(port=$Port); print(c.get_property(r'$Device', r'$Property'))" > "$Evidence\g6-after-web-ctrl-c-readback.txt" 2>&1
```

**PASS:** post-shutdown read-back is exactly `<OFF_VALUE>`. If `serve` is not
deployed on M5, mark G6 **not applicable** with the reason; do not call it a
pass.

## G7 — Demo behavior

In Micro-Manager, close the M5 hardware configuration and load the stock Demo
configuration according to the lab's normal procedure. Confirm no physical M5
hardware remains controlled. Copy the reviewed demo profile and replace only
its `workspace_dir` placeholder with an existing directory:

```powershell
$DemoConfig = Join-Path $Evidence "demo-safety.yaml"
$DemoWorkspace = Join-Path $Evidence "demo-workspace"
New-Item -ItemType Directory -Path $DemoWorkspace | Out-Null
Copy-Item design\33-block5-demo-safety-config.yaml $DemoConfig
notepad $DemoConfig
python -m microclaw --port $Port --safety-config $DemoConfig authorization-map > "$Evidence\g7-demo-map.txt" 2>&1
$LASTEXITCODE > "$Evidence\g7-demo-exit-code.txt"
```

Record which of these actually occurred:

- No EMU semantic enable was exposed: normal non-EMU Demo startup stays
  complete. This does not exercise the refusal.
- The installed EMU configuration exposed a semantic enable that could not be
  resolved against Demo devices: record the exact fail-closed refusal.
- A resolvable semantic enable existed: apply the same declaration rule, but do
  not claim it represents physical Demo illumination without evidence.

Do not describe the Demo run as testing a missing-declaration condition it did
not expose. G0's off-rig fixture is the deterministic refusal evidence.

## G8 — package and verdict

Create a manifest and archive without deleting the original evidence:

```powershell
Get-ChildItem -File -Recurse $Evidence | Get-FileHash -Algorithm SHA256 | Format-List Path,Hash > "$Evidence\manifest-sha256.txt"
$Archive = "$Evidence.zip"
Compress-Archive -Path $Evidence -DestinationPath $Archive
Get-FileHash -Algorithm SHA256 -LiteralPath $Archive > "$Evidence-archive-sha256.txt"
```

Return the evidence directory/archive and a verdict table for G0–G7. Overall:

- **PASS:** G0–G5 pass; G6 is pass or genuinely not applicable; G7 is recorded
  without overclaiming; no cleanup failure occurred.
- **FAIL:** startup incorrectly accepts a discovered undeclared enable, the
  refusal is not actionable, confirmation decline writes, or any cleanup leaves
  the property enabled.
- **INCOMPLETE:** required operator authorization/evidence was unavailable, an
  identity/value remained unresolved, or a required read-back was not captured.

Regardless of verdict: do not merge, do not edit the checklist or ledger, and
return every deviation and safety stop to the coordinator.
