# design/35 Block 4f — Channel-group preset generation gate

This gate verifies branch `design33/channel-group-presets`. The complete
implementation is pinned at `84c4d70`; later runbook-only commits are valid
descendants. A qualified operator must remain at the microscope and use the
rig's normal optical containment. Do not make a generated profile permanent.

Run G1 on **either M2 or M5**, not both. Run G2 on demo. The offline replay of
the captured inventories found these exact changes: M2's six `Camera` presets
become `channels.allowed: []`; M5's four `System` presets become `[]`; demo's
fourteen cross-group names become exactly the four `Channel` presets (`Cy5`,
`DAPI`, `FITC`, `Rhodamine`). Presets in every other group remain visible in
the generated review notes but are not claimed as channels. Live gates are
still required for fresh generation, startup authorization, and a real channel
selection.

## G0 — branch and evidence setup on each selected machine

```powershell
git fetch origin > git-fetch.txt 2>&1
git switch design33/channel-group-presets > git-switch.txt 2>&1
git pull --ff-only > git-pull.txt 2>&1
git status --short > status.txt 2>&1
git rev-parse HEAD > head.txt 2>&1
git merge-base --is-ancestor 84c4d70 HEAD
echo $LASTEXITCODE > implementation-ancestor-exit.txt
python -m pytest -q > pytest.txt 2>&1
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Evidence = "block4f-$Stamp"
New-Item -ItemType Directory -Path $Evidence
$Draft = Join-Path $Evidence "profile.yaml"
$Inventory = Join-Path $Evidence "inventory"
Copy-Item "design\35-block4f-gate-prompts.md" (Join-Path $Evidence "runbook.md")
microclaw first-launch-setup --out $Draft --evidence-out $Inventory
```

The ancestor command must exit `0` and the tree must be clean. Preserve
`pytest.txt`. Until Block 4g merges, the Windows condition is exactly the 23
known failures in `test_completed_dataset.py` and `test_describe_hook.py`, and
no others. If Block 4g is an ancestor of the tested branch, the suite must be
clean; record that dependency in the returned evidence.

Review the draft. Confirm its header explains any absent/empty `Channel` group
and lists presets in other groups as non-channel presets that Microclaw does not
drive. Copy the draft, change only the reviewed copy to `reviewed: true`, and
validate it:

```powershell
$Reviewed = Join-Path $Evidence "profile.reviewed.yaml"
Copy-Item $Draft $Reviewed
microclaw check-config $Reviewed > check-config.txt 2>&1
echo $LASTEXITCODE > check-config-exit.txt
```

## G1 — M2 or M5: no Channel group means no claims and no demotion

Mechanically inspect the generated YAML. It must contain an explicit empty
list; the `allowed` key must not be omitted, because omission means all live
`Channel` presets are authorized.

```powershell
python -c "import sys,yaml;p=yaml.safe_load(open(sys.argv[1],encoding='utf-8'));print('ALLOWED KEY PRESENT:', 'allowed' in p.get('channels',{}));print('ALLOWED EXACTLY EMPTY:',p.get('channels',{}).get('allowed')==[])" $Draft > channel-allowlist-check.txt 2>&1
microclaw --safety-config $Reviewed > no-channel-session.txt 2>&1
Select-String -Path no-channel-session.txt -Pattern "channels.allowed","Allowed channel preset","preset is absent" > preset-demotion-check.txt
```

Both mechanical lines must print `True`, and `preset-demotion-check.txt` must be
empty. Setup text and profile header must say the rig has no `Channel` group,
that `[]` authorizes no channel presets, and that presets in its other group(s)
are not channels and are not driven by Microclaw. Merely seeing startup succeed
does not pass this step; retain the YAML check and session output.

## G2 — demo: retain and actually select a fluorescence Channel preset

The generated draft must contain exactly the four real `Channel` presets, with
no names from `Camera`, `Channel-Multiband`, `LightPath`, `Objective`, or
`System`. This check compares sets so YAML ordering is irrelevant:

```powershell
python -c "import sys,yaml;p=yaml.safe_load(open(sys.argv[1],encoding='utf-8'));a=p.get('channels',{}).get('allowed');e={'Cy5','DAPI','FITC','Rhodamine'};print('EXACT CHANNEL PRESETS:',isinstance(a,list) and set(a)==e and len(a)==len(e));print('ACTUAL:',a)" $Draft > demo-channel-allowlist-check.txt 2>&1
microclaw --safety-config $Reviewed > demo-session.txt 2>&1
Select-String -Path demo-session.txt -Pattern "channels.allowed","Allowed channel preset","preset is absent" > demo-preset-demotion-check.txt
```

`EXACT CHANNEL PRESETS` must print `True` and the demotion check must be empty.
In the browser ask exactly:

> Select the DAPI channel preset, then report the currently selected channel.

Pass only if the history JSONL contains an actual `set_channel` tool call with
`preset: DAPI` and a successful tool result. The transcript alone is not
evidence. Run this mechanical check, replacing `<history>` with the session
history JSONL path:

```powershell
python -c "import json,sys;h=[json.loads(l) for l in open(sys.argv[1],encoding='utf-8')];c=[b for r in h if isinstance(r.get('content'),list) for b in r['content']];u=[(i,b) for i,b in enumerate(c) if b.get('type')=='tool_use' and b.get('name')=='set_channel' and b.get('input',{}).get('preset')=='DAPI'];print('SET_CHANNEL CALLED:',bool(u));print('SUCCESS RETURNED:',bool(u) and any(b.get('type')=='tool_result' and b.get('tool_use_id')==u[-1][1].get('id') and not b.get('is_error',False) and 'error' not in str(b.get('content')).lower() for b in c[u[-1][0]+1:]))" <history>.jsonl > demo-channel-selection-check.txt 2>&1
```

Both lines must print `True`. This asks the agent only for a valid, authorized
value. Preserve the history JSONL and the read-back/tool result. Restore the
rig's normal channel if required by local practice.

## Return to the coordinator

Return each evidence directory with the ancestor result, status, `pytest.txt`,
inventory, interview transcript, draft and reviewed profiles, validator output,
session output, mechanical allowlist and demotion checks, and demo history plus
selection check. Report any unexpected allowlist name, missing review note,
preset demotion, failed selection/read-back, or suite failure outside the stated
Windows baseline. Do not merge or push `main`.
