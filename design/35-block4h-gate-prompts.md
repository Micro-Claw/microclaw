# design/35 Block 4h — confirmation visibility gate

This gate verifies branch `design33/confirmation-visibility`. The complete
implementation is pinned at `3efecaf`; later runbook-only commits are valid
descendants. Run this gate on the **demo machine only**. The `Core.Shutter`
retarget is a selection, not an emission, so optical containment is not needed.

The narration is deliberately not the mechanical oracle. Preserve it as useful
colour, but pass or fail each exercise from the `set_channel` tool result's
structured `confirmations` array.

## G0 — branch and suite

```powershell
git fetch origin > git-fetch.txt 2>&1
git switch design33/confirmation-visibility > git-switch.txt 2>&1
git pull --ff-only > git-pull.txt 2>&1
git status --short > status.txt 2>&1
git rev-parse HEAD > head.txt 2>&1
git merge-base --is-ancestor 3efecaf HEAD
echo $LASTEXITCODE > implementation-ancestor-exit.txt
python -m pytest -q > pytest.txt 2>&1
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Evidence = "block4h-demo-$Stamp"
New-Item -ItemType Directory -Path $Evidence
Copy-Item "design\35-block4h-gate-prompts.md" (Join-Path $Evidence "runbook.md")
Copy-Item status.txt,head.txt,implementation-ancestor-exit.txt,pytest.txt $Evidence
```

The ancestor command must exit `0` and the tree must be clean. Preserve
`pytest.txt`. The Windows suite's hard condition is **0 failed**. Expect about
**1342 passed / 115 skipped** based on the last demo run; report a small passed
count difference instead of failing solely on that count.

Use the same reviewed demo safety profile that authorized the Block 4f DAPI
selection. Start Microclaw normally and retain its history JSONL and separate
confirmations JSONL after each exercise.

## G1 — approve the DAPI shutter retarget

Begin from the demo's normal initial state, in which selecting DAPI retargets
`Core.Shutter` to `White Light Shutter`. In the browser ask exactly:

> Select the DAPI channel preset.

Approve the blocking illumination confirmation. Then ask:

> Did you prompt me during that channel change, and what did I approve?

The agent must say that a confirmation occurred and accurately describe the
shutter selection. This narration is not the pass oracle. Stop Microclaw so the
history is flushed, set `$ApprovedHistory` to that history JSONL, and run:

```powershell
$ApprovedHistory = "<approved-history>.jsonl"
python -c "import json,sys;h=[json.loads(x) for x in open(sys.argv[1],encoding='utf-8')];b=[x for m in h if isinstance(m.get('content'),list) for x in m['content']];u=[x for x in b if x.get('type')=='tool_use' and x.get('name')=='set_channel' and x.get('input',{}).get('preset')=='DAPI'];r=[x for x in b if x.get('type')=='tool_result' and u and x.get('tool_use_id')==u[-1].get('id')];p=json.loads(r[-1]['content']) if r and isinstance(r[-1].get('content'),str) else {};c=p.get('confirmations',[]);print('SET_CHANNEL DAPI RESULT:',bool(r));print('APPROVED CONFIRMATION STRUCTURE:',any(isinstance(x,dict) and x.get('kind')=='illumination' and x.get('decision')=='approved' and isinstance(x.get('summary'),str) and x.get('summary') for x in c))" $ApprovedHistory > (Join-Path $Evidence "approved-result-check.txt") 2>&1
Copy-Item $ApprovedHistory (Join-Path $Evidence "approved-history.jsonl")
```

Both lines must print `True`. Preserve the corresponding confirmations JSONL.

## G2 — decline the same retarget

Reload/restart the demo configuration and start a fresh Microclaw session so
the rig is back in the same normal initial state and DAPI once again requires
the `Core.Shutter` retarget. Do not merely select DAPI a second time while
`Core.Shutter` is already `White Light Shutter`, because that would correctly
produce no new confirmation.

In the browser ask exactly:

> Select the DAPI channel preset.

Decline the blocking illumination confirmation. Then ask:

> Did you prompt me during that attempted channel change, and what did I decline?

The agent must say that a confirmation occurred, was declined, and accurately
describe the shutter selection. Again, grade the record rather than this prose.
Stop Microclaw, set `$DeclinedHistory`, and run:

```powershell
$DeclinedHistory = "<declined-history>.jsonl"
python -c "import json,sys;h=[json.loads(x) for x in open(sys.argv[1],encoding='utf-8')];b=[x for m in h if isinstance(m.get('content'),list) for x in m['content']];u=[x for x in b if x.get('type')=='tool_use' and x.get('name')=='set_channel' and x.get('input',{}).get('preset')=='DAPI'];r=[x for x in b if x.get('type')=='tool_result' and u and x.get('tool_use_id')==u[-1].get('id')];p=json.loads(r[-1]['content']) if r and isinstance(r[-1].get('content'),str) else {};c=p.get('confirmations',[]);print('SET_CHANNEL DAPI RESULT:',bool(r));print('DECLINED CONFIRMATION STRUCTURE:',any(isinstance(x,dict) and x.get('kind')=='illumination' and str(x.get('decision','')).startswith('declined') and isinstance(x.get('summary'),str) and x.get('summary') for x in c))" $DeclinedHistory > (Join-Path $Evidence "declined-result-check.txt") 2>&1
Copy-Item $DeclinedHistory (Join-Path $Evidence "declined-history.jsonl")
```

Both lines must print `True`. Preserve the corresponding confirmations JSONL.
The declined tool result should still contain its refusal/error alongside the
`confirmations` array; a declined confirmation is not a missing result.

## Return to the coordinator

Return the evidence directory with the ancestor result, clean status,
`pytest.txt`, both histories and confirmations JSONLs, and both mechanical check
files. Report any missing/ambiguous confirmation structure, wrong decision or
kind, failure to restore the initial retarget condition, suite failure, or agent
narration that disagrees with the structured result. Do not merge or push
`main`.
