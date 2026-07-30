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

### G0 result — PASS with one bookkeeping correction owed (2026-07-30, M5)

Evidence: `block2-m5-20260730-161938`. HEAD `cf13c1a`, implementation ancestry
check `0`, clean status, clean diff check, Python 3.12.13, Windows 10 Pro build
26200, Micro-Manager 2.0.3.20260713, and **68 authorization tests passed** in
2.70 s. The deployed profile and immutable backup have the same SHA-256,
`F9DA9BDCD04E9E3C5A2A753F9BE5AE8D940E9345B0A841EA7E894EF9563CF1B2`.

The evidence's `rig.txt` retained the literal placeholder
`rig_id=<M5 rig identifier>`. Correct it in the final manifest/report to the
lab's actual M5 identifier; the `.cfg` identity was recorded as
`M5_working_config_Booster_NOELL2_withPresets_NK.cfg` and its separate hash is
present. This is evidence bookkeeping, not a gate-mechanism failure.

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

### G1 result — PASS (2026-07-30, M5)

The current deployed profile refused with exit code 1 and named all three
missing semantic enables precisely:

| EMU slot | Exact Micro-Manager pair |
|---|---|
| 1 | `iChrome-MLE-TCP.Laser 3: 1. Enable` |
| 2 | `iChrome-MLE-TCP.Laser 2: 1. Enable` |
| 3 | `iChrome-MLE-TCP.Laser 1: 1. Enable` |

Slot 0 resolved to the already-declared
`iChrome-MLE-TCP.Laser 4: 1. Enable`, so it correctly did not appear as
missing. Every refusal named `constraints.illumination.shutters`, rendered the
editable top-level YAML shape, and stated that no on/off values were inferred.

Follow-up read-only evidence settled the values rather than guessing them. The
driver reported current value `0` for all three (and an empty allowed-values
vector). The live EMU `ht-SMLM` configuration explicitly records `on: "1"` and
`off: "0"` for all four slot mappings. The reverse physical-number mapping is
real and preserved: semantic slots 0, 1, 2, 3 map to physical Laser 4, 3, 2, 1
respectively.

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

### G2 result — PASS (2026-07-30, M5)

The gate copy added only these three reviewed declarations, each with
`on_value: "1"` and `off_value: "0"`:

- `iChrome-MLE-TCP.Laser 3: 1. Enable`
- `iChrome-MLE-TCP.Laser 2: 1. Enable`
- `iChrome-MLE-TCP.Laser 1: 1. Enable`

The diff contains no other change. The corrected gate profile SHA-256 is
`6D0EAF93C8738528D9781C77F35B05613E8E8869D4A39D595536C0289BFCFCE1`.
`authorization-map` exited 0 with `complete: true`, `mode: guaranteed`, and
`verdict: complete`. All five configured shutter pairs — the three new enables,
existing `Laser 4: 1. Enable`, and existing `Laser 4: 2. Emission` — appear as
`dedicated-illumination` / `built_in_typed_capability`. The captured PowerShell
`NativeCommandError` preamble is its rendering of the expected stderr
`Connecting to Micro-Manager...` progress line, not a failed command.

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

G1 found three missing enable pairs, so G3 must decline and read back all three;
testing only one would leave the other two live mappings unproved. Set:

```powershell
$Device = "iChrome-MLE-TCP"
$OnValue = "1"
$OffValue = "0"
```

M5 must use `serve`, not the terminal REPL. This is a known, separately recorded
product asymmetry: `serve` calls `credentials.load_api_key()` and can use the
key stored from the browser, while `run_session` does not. Do not turn an API-key
failure into fake gate evidence and do not copy a secret into the shell merely
for this gate.

Start one server session and preserve its console transcript:

```powershell
Start-Transcript -Path "$Evidence\g3-confirmation-transcript.txt"
uv run python -m microclaw --port $Port --safety-config $GateConfig serve
```

In the browser, paste these prompts one at a time. Answer **no** in every
browser confirmation dialog.

First, Laser 3:

> Use `set_device_property` to set device `iChrome-MLE-TCP`, property
> `Laser 3: 1. Enable` to `1`. Do not use another tool. Ask for the required
> human confirmation before writing.

Then:

> Use `get_device_property` to read device `iChrome-MLE-TCP`, property
> `Laser 3: 1. Enable`. Report the raw value exactly.

It must remain `0`. Repeat for Laser 2:

> Use `set_device_property` to set device `iChrome-MLE-TCP`, property
> `Laser 2: 1. Enable` to `1`. Do not use another tool. Ask for the required
> human confirmation before writing.

> Use `get_device_property` to read device `iChrome-MLE-TCP`, property
> `Laser 2: 1. Enable`. Report the raw value exactly.

It must remain `0`. Repeat for Laser 1:

> Use `set_device_property` to set device `iChrome-MLE-TCP`, property
> `Laser 1: 1. Enable` to `1`. Do not use another tool. Ask for the required
> human confirmation before writing.

> Use `get_device_property` to read device `iChrome-MLE-TCP`, property
> `Laser 1: 1. Enable`. Report the raw value exactly.

It must remain `0`. Press Ctrl-C once in the server console, wait for the server
to exit, then end transcript capture:

```powershell
Stop-Transcript
```

Independently verify every declared shutter, not just the three newly added
pairs. A script avoids PowerShell/`uv` splitting `python -c` at the colons in
the real property names:

```powershell
$Probe = "$Evidence\g3-after-decline-readback.py"

@'
from pycromanager import Core

core = Core(port=4827)
device = "iChrome-MLE-TCP"
properties = [
    "Laser 4: 1. Enable",
    "Laser 4: 2. Emission",
    "Laser 3: 1. Enable",
    "Laser 2: 1. Enable",
    "Laser 1: 1. Enable",
]

for prop in properties:
    print(f"{device}.{prop}={core.get_property(device, prop)}")
'@ | Set-Content -Encoding UTF8 $Probe

uv run python $Probe > "$Evidence\g3-after-decline-readback.txt" 2>&1
Get-Content "$Evidence\g3-after-decline-readback.txt"
```

Also copy the session history JSONL containing these six tool calls into the
evidence directory. The browser transcript plus independent read-back are the
human-path evidence; console text alone is insufficient.

**PASS:** all three attempted writes visibly request a browser confirmation;
all three are declined; each immediate read-back is `0`; all five independent
post-server read-backs are `0`; and the saved history contains the corresponding
`set_device_property` and `get_device_property` calls. A natural-language
refusal without read-back is not enough. Do not proceed to an accepted enable
until the coordinator reviews G3.

## G4 — accepted enable and deployed `serve` cleanup

Run only with explicit operator authorization and the rig in the safe condition
defined at the top of this document. Test one operator-selected enable property;
the complete map and three G3 declines establish routing for every discovered
pair, while one accepted emissive cycle is sufficient to prove the shared
confirmation/cleanup mechanism without needlessly enabling three laser lines.

Set `$Property` to the exact operator-selected pair and record why that line was
chosen:

```powershell
$Property = "<one of the three verified Laser N: 1. Enable properties>"
```

```powershell
Start-Transcript -Path "$Evidence\g4-accepted-serve-transcript.txt"
uv run python -m microclaw --port $Port --safety-config $GateConfig serve
```

In the browser paste, with the exact selected property substituted:

> Use `set_device_property` to set device `<DEVICE>`, property `<PROPERTY>` to
> `<ON_VALUE>`. Do not use another tool. Ask for the required human
> confirmation before writing.

Accept the confirmation. Immediately paste:

> Use `get_device_property` to read device `<DEVICE>`, property `<PROPERTY>`.
> Report the raw value exactly.

Require the exact `<ON_VALUE>` read-back, then press Ctrl-C once in the server
console. The console must report illumination cleanup. Finish and independently
verify all five declared shutters with the same script shape used by G3, writing
the output to `g4-after-serve-readback.txt`:

```powershell
Stop-Transcript
Copy-Item "$Evidence\g3-after-decline-readback.py" "$Evidence\g4-after-serve-readback.py"
uv run python "$Evidence\g4-after-serve-readback.py" > "$Evidence\g4-after-serve-readback.txt" 2>&1
Get-Content "$Evidence\g4-after-serve-readback.txt"
```

Copy the G4 session history JSONL into the evidence directory. **PASS:** the
accepted enable reads exactly `<ON_VALUE>`, the server shutdown reports cleanup,
and every post-shutdown shutter read-back is exactly `<OFF_VALUE>`.

## G5 — terminal-REPL exit paths: not live-runnable on this M5

Do not run the terminal REPL: its `run_session` path does not call
`credentials.load_api_key()`, so M5's browser-stored key is unavailable there.
Record both normal `exit` and REPL Ctrl-C as **not applicable to the deployed
credential path**, citing this known product limitation. This is not a pass for
those paths and must not be described as one. G4 exercises the actually deployed
`serve` Ctrl-C shutdown path.

Do not manufacture a Python/bridge crash on live hardware to test the exception
limb. The shared `finally` path is covered deterministically off-rig. Record the
live controlled-exception limb as **not run — unsafe failure injection** unless
the coordinator separately supplies a reviewed probe.

## G6 — web-server Ctrl-C

G4 is this limb. Do not repeat an emissive cycle merely to produce a second
filename. Record G6 as **covered by G4**, with links to the accepted browser
confirmation, enabled read-back, server Ctrl-C transcript, cleanup report, and
five-property independent off read-back.

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

- **PASS:** G0–G4 pass; G5 accurately records the two non-deployed REPL paths as
  not applicable rather than passed; G6 is covered by G4; G7 is recorded without
  overclaiming; no cleanup failure occurred.
- **FAIL:** startup incorrectly accepts a discovered undeclared enable, the
  refusal is not actionable, confirmation decline writes, or any cleanup leaves
  the property enabled.
- **INCOMPLETE:** required operator authorization/evidence was unavailable, an
  identity/value remained unresolved, or a required read-back was not captured.

Regardless of verdict: do not merge, do not edit the checklist or ledger, and
return every deviation and safety stop to the coordinator.
