# design/35 Block 4e — emission-path discovery and multi-state shutter gate

This gate verifies branch `design33/emission-path-discovery`. The implementation
pin is `7c0f25b`; later runbook-only commits are valid descendants. A qualified
operator must remain at the microscope, use the rig's normal optical containment,
and stop on unexpected light or motion. Do not make a generated profile permanent.
Commands below are PowerShell/cmd-safe and preserve evidence without Unix pipes.

Offline analysis of the captured `microclaw.rig-inventory/v2` files settles part
of G3 already. Candidate counts were **re-derived by running `_is_enable` over
`facts.devices`** under each revision before replaying the interview: `interview()`
consumes the file's stored `heuristic_candidates`, so replaying a captured file
through the interview alone cannot observe a producer change. Demo candidates stay
2 -> 2, M5 stays 21 -> 21, and M2 changes 9 -> 12. With those re-derived
candidates and the same deterministic acceptance responder, demo stays at 69
questions, M5 stays at 230, and M2 changes from 118 to 127. Demo and M5 gain and
lose no illumination candidates or shutter declarations. M2 gains exactly
`Cobolt561.Analog Impedance`, `Cobolt561.Autostart`, and `Cobolt561.Laser` as
candidates and loses none; when all surfaced enable candidates are accepted as
emission gates, those same three declarations are added. The operator must still
classify each candidate from actual rig meaning. Offline analysis cannot prove
that a profile starts against the live Core, a real write reaches the guard, or
teardown drives every declaration to its reviewed off value.

## G0 — branch and evidence setup on every machine

```powershell
git fetch origin > git-fetch.txt 2>&1
git switch design33/emission-path-discovery > git-switch.txt 2>&1
git pull --ff-only > git-pull.txt 2>&1
git status --short > status.txt 2>&1
git rev-parse HEAD > head.txt 2>&1
git merge-base --is-ancestor 7c0f25b HEAD
echo $LASTEXITCODE > implementation-ancestor-exit.txt
python -m pytest -q > pytest.txt 2>&1
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Evidence = "block4e-$Stamp"
New-Item -ItemType Directory -Path $Evidence
$Draft = Join-Path $Evidence "profile.yaml"
$Inventory = Join-Path $Evidence "inventory"
Copy-Item "design\35-block4e-gate-prompts.md" (Join-Path $Evidence "runbook.md")
microclaw first-launch-setup --out $Draft --evidence-out $Inventory
```

The ancestor command and suite must pass and the tree must be clean. Review every
candidate and declaration. Copy the draft, change only the reviewed copy to
`reviewed: true`, and validate it:

```powershell
$Reviewed = Join-Path $Evidence "profile.reviewed.yaml"
Copy-Item $Draft $Reviewed
microclaw check-config $Reviewed > check-config.txt 2>&1
echo $LASTEXITCODE > check-config-exit.txt
```

## G1 — M2: discover and declare the core shutter emission gate

During setup, confirm that `Cobolt561.Laser` is offered as an illumination
candidate without editing the inventory or profile. Classify it as an emission
gate and accept or enter its verified `On` and `Off` values. The expected
classification of both `Cobolt561.Autostart` and `Cobolt561.Analog Impedance` is
**not an emission gate**: Autostart controls power-up behavior, while Analog
Impedance controls modulation-input termination. If the operator declares either
one anyway, `shutter_all` will write its declared `off_value` on every session exit,
potentially rewriting persistent laser configuration. The operator may overrule
the expected classification only after checking the real rig with that consequence
in mind; being a candidate does not authorize a property.

Pass only if the unedited generated draft contains this exact declaration:

```yaml
illumination:
  shutters:
    - device: Cobolt561
      property: Laser
      on_value: "On"
      off_value: "Off"
```

Also retain the inventory and interview transcript showing that setup asked the
question. Confirm the captured Core assignment still names `Cobolt561` as the
shutter device. This step is read-only after enumeration; do not enable it.

## G2 — M2: legal third state is confirmation-gated

Start Microclaw with the reviewed profile:

```powershell
microclaw --safety-config $Reviewed > m2-session.txt 2>&1
```

In the browser, ask exactly:

> Set `Andor.Shutter (Internal)` to `Auto`. When Microclaw asks for illumination
> confirmation, decline it.

`Auto` is a legal driver value, so this does not ask the agent to forward known
invalid input. The pass condition is a `set_device_property` tool call for the
exact device, property, and value, followed by a tool result saying the operator
declined illumination. A transcript assertion without that call proves nothing.
Locate the session history JSONL and run:

```powershell
python -c "import json,sys;h=[json.loads(l) for l in open(sys.argv[1],encoding='utf-8')];c=[b for r in h if isinstance(r.get('content'),list) for b in r['content']];print('WRITE ATTEMPTED:',any(b.get('type')=='tool_use' and b.get('name')=='set_device_property' and b.get('input',{}).get('device')=='Andor' and b.get('input',{}).get('property')=='Shutter (Internal)' and b.get('input',{}).get('value')=='Auto' for b in c));print('DECLINE RETURNED:',any(b.get('type')=='tool_result' and 'declined' in str(b.get('content')).lower() and 'Shutter (Internal)' in str(b.get('content')) for b in c))" <history>.jsonl > auto-gate-check.txt 2>&1
```

Both lines must print `True`. Preserve the confirmations JSONL as corroborating
evidence. Read the property afterward: it must not have changed to `Auto`. This
read-back is necessary because a failed hardware write cannot be assumed not to
have landed.

## G3 — M5 and demo: live non-regression

Generate and review a fresh profile on each rig. Candidate and profile paths must
match the pre-change inventory replay exactly.

Demo expected shutters:

- `Core.AutoShutter`
- `White Light Shutter.State`

`LED Shutter.State Device` remains outside the illumination set; its seven-state
domain is not an on/off-shaped gate. More importantly, `LED Shutter` is itself a
`ShutterDevice` and one of the devices selectable through `Core.Shutter`, but it
has no writable gating property: its emission is controlled only through MMCore's
shutter API. This is the residual limit of property-based discovery: some emission
paths have no gating property at all. Therefore the absence of a declared property
for the current core shutter must not cause startup refusal; the rig cannot supply
a legal property declaration for this stock demo case. The safety claim is only
that declared paths are gated, never that discovery found every physical path.
Confirm the fluorescence channel presets still validate and can select their
already-declared shutter under the existing confirmation path. Decline one
illumination enable and mechanically verify its tool call and refusal in history
JSONL as in G2.

M5 expected shutters are the existing 21 paths: `Core.AutoShutter`; three each
(`Enable Fine`, `Enable ext trigger`, `Laser Operation`) on `iBeamSmartCW-1`,
`iBeamSmartCW-Booster`, and `iBeamSmartCW`; plus the eleven existing iChrome
`Enable`/`Emission` paths. No iChrome TTL or Analog mode switch may newly become
a shutter or ordinary writable property. M5's unchanged 21 -> 21 discovery result
is guaranteed by the captured facts, not a resilience measurement: M5 has zero
`ShutterDevice`s, so this discovery change cannot affect it. The live M5 step tests
the separate safety-gating change. With illumination optically contained, exercise
one previously working declared shutter through confirmation, return it to its
reviewed off value, and retain the tool history and read-back. The guard compares
the requested value exactly with the declared `off_value`; a driver formatting
variant such as `"0.0"` when the profile declares `"0"` now prompts for confirmation
instead of passing silently. This is intentionally fail-safe, but check it across
the numeric-valued members of M5's 21 declared shutters and report any occurrence.

Offline replay settles candidate membership, question counts, and generated
declaration deltas. These live steps settle startup authorization, actual guard
routing, device read-back, and the absence of adapter/runtime regressions.

## Return to the coordinator

Return the evidence directory from each rig: implementation ancestor result,
pytest output, status, inventory, review, interview transcript, draft and reviewed
profiles, validator output, session transcript, history JSONL, confirmations JSONL,
mechanical check output, and final read-backs. Report every unexpected candidate,
missing declaration, question-count change, startup refusal, write without a
confirmation, or cleanup/read-back mismatch. Do not merge or push `main`.
