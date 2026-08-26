# Block 58a demo gate — discovery and exact materialization

Run in PowerShell on the Windows demo machine from the checked-out
`design58/discovery` branch. Micro-Manager stays closed. Every assertion exits
nonzero on failure; stop at the first failure. Artifacts go to a fresh temporary
directory outside the checkout.

```powershell
$ErrorActionPreference = 'Stop'
$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo
git merge-base --is-ancestor 4ace157 HEAD
if ($LASTEXITCODE -ne 0) { throw 'This checkout does not contain Block 58a' }
$gate = Join-Path ([IO.Path]::GetTempPath()) ("microclaw-58a-" + [guid]::NewGuid())
New-Item -ItemType Directory $gate | Out-Null
$env:MC58_REPO = $repo
$env:MC58_GATE = $gate
python -c "import microclaw; print(microclaw.__file__)"
```

The checked-out block HEAD is deliberately not the installed commit for the
positive discovery limb: before merge it is not an ancestor of `origin/main`,
and the ancestry refusal would correctly produce no candidate. Use the previous
main commit, route through `check_for_update`, and prove the cached candidate is
exactly `origin/main` even while the block branch is checked out.

```powershell
$installed = (git rev-parse origin/main~1).Trim()
$expected = (git rev-parse origin/main).Trim()
$state = Join-Path $gate 'clone-state.json'
$env:MC58_STATE = $state
$env:MC58_INSTALLED = $installed
$reported = python -c "import os,shutil;from microclaw import updates;s=updates.clone_provenance(os.environ['MC58_REPO'],os.environ['MC58_INSTALLED'],git_executable=shutil.which('git'));updates.write_state(s,os.environ['MC58_STATE']);c=updates.check_for_update(state_file=os.environ['MC58_STATE'],now=100,jitter=lambda a,b:0);print(c.sha if c else 'NO CANDIDATE')"
$cached = Get-Content $state -Raw | ConvertFrom-Json
Write-Host "origin/main: $expected"
Write-Host "candidate:   $reported"
if ($reported.Trim() -ne $expected) { throw 'Clone check did not discover origin/main from its ancestor fixture' }
if ($cached.last_attempt -ne 100 -or $cached.last_success.candidate.sha -ne $expected) { throw 'Clone check did not atomically cache its attempt and candidate' }
```

Now exercise the ancestry refusal that the block branch itself must trigger.
This is distinct from tracking the wrong ref: the discovery status must say the
installed block HEAD and fetched `main` diverged.

```powershell
$env:MC58_HEAD = (git rev-parse HEAD).Trim()
python -c "import os;from microclaw import updates;s=updates.load_state(os.environ['MC58_STATE']);s['installed_commit']=os.environ['MC58_HEAD'];c=updates.discover_clone(s);updates.write_state(s,os.environ['MC58_STATE']);print(s['discovery']['status']);raise SystemExit(0 if c is None and s['discovery']['status']=='diverged' else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Block HEAD was not refused specifically as diverged history' }
```

Dirty one untracked file and prove discovery changes neither checkout identity
nor bytes. The `finally` removes the proof file even when the assertion fails,
then the step verifies the checkout returned to its starting status.

```powershell
$dirty = Join-Path $repo '.58a-dirty-proof'
$cleanBefore = git status --porcelain=v1
try {
    [IO.File]::WriteAllText($dirty, 'must survive byte-for-byte')
    $beforeStatus = git status --porcelain=v1
    $beforeHead = (git rev-parse HEAD).Trim()
    $beforeBranch = (git branch --show-current).Trim()
    $beforeHash = (Get-FileHash -Algorithm SHA256 $dirty).Hash
    python -c "import os;from microclaw import updates;s=updates.load_state(os.environ['MC58_STATE']);s['installed_commit']=os.environ['MC58_INSTALLED'];updates.discover_clone(s)" | Out-Null
    $afterStatus = git status --porcelain=v1
    $afterHead = (git rev-parse HEAD).Trim()
    $afterBranch = (git branch --show-current).Trim()
    $afterHash = (Get-FileHash -Algorithm SHA256 $dirty).Hash
    if (($beforeStatus -join "`n") -ne ($afterStatus -join "`n") -or $beforeHead -ne $afterHead -or $beforeBranch -ne $afterBranch -or $beforeHash -ne $afterHash) { throw 'Clone discovery modified the checkout' }
} finally {
    Remove-Item $dirty -ErrorAction SilentlyContinue
}
$cleanAfter = git status --porcelain=v1
if (($cleanBefore -join "`n") -ne ($cleanAfter -join "`n")) { throw 'Dirty-check limb did not restore checkout status' }
Write-Host "branch before/after: $beforeBranch / $afterBranch"
Write-Host "HEAD before/after:   $beforeHead / $afterHead"
```

Materialize the actual cached candidate. There is deliberately no fallback to
the installed commit: absence of the discovered candidate fails this mechanism.

```powershell
$stage = Join-Path $gate 'stage'
$env:MC58_STAGE = $stage
python -c "import os;from microclaw import updates;s=updates.load_state(os.environ['MC58_STATE']);raw=s['last_success']['candidate'];c=updates.Candidate(**raw) if raw else None;assert c is not None,'clone check cached no candidate';updates.materialize_clone(s,c,os.environ['MC58_STAGE']);updates.verify_staged_source(os.environ['MC58_STAGE'],c.sha);print(c.sha)"
$marker = (Get-Content (Join-Path $stage 'microclaw-staged-source.json') -Raw | ConvertFrom-Json).commit
if ($marker -ne $expected) { throw 'Staged marker does not match the fetched candidate' }
Write-Host "fetched/staged: $expected / $marker"
```

The public path is still private: prove its 404 is cached without replacing a
successful result. This is the gate's one public API request.

```powershell
$publicState = Join-Path $gate 'public-state.json'
$env:MC58_PUBLIC_STATE = $publicState
python -c "import os;from microclaw import updates;s=updates.public_provenance();s['last_success']={'checked_at':1,'candidate':None};updates.write_state(s,os.environ['MC58_PUBLIC_STATE']);updates.check_for_update(state_file=os.environ['MC58_PUBLIC_STATE'],now=100);print(updates.load_state(os.environ['MC58_PUBLIC_STATE'])['last_error'])"
$public = Get-Content $publicState -Raw | ConvertFrom-Json
if ($public.last_attempt -ne 100 -or $public.last_error -notmatch '404' -or $public.last_success.checked_at -ne 1) { throw 'Public 404 was not cached correctly' }
```

Finally, prove both opt-outs leave the attempt timestamp unchanged and confirm
the repository has exactly the status it had before the gate.

```powershell
$before = (Get-Content $publicState -Raw | ConvertFrom-Json).last_attempt
python -c "import os;from microclaw import updates;updates.check_for_update(state_file=os.environ['MC58_PUBLIC_STATE'],no_update_check=True,now=200)"
$afterFlag = (Get-Content $publicState -Raw | ConvertFrom-Json).last_attempt
$env:MICROCLAW_UPDATE_CHECK = '0'
python -c "import os;from microclaw import updates;updates.check_for_update(state_file=os.environ['MC58_PUBLIC_STATE'],now=300)"
Remove-Item Env:MICROCLAW_UPDATE_CHECK
$afterEnv = (Get-Content $publicState -Raw | ConvertFrom-Json).last_attempt
$finalStatus = git status --porcelain=v1
if ($before -ne $afterFlag -or $before -ne $afterEnv) { throw 'An opt-out performed a check' }
if (($cleanBefore -join "`n") -ne ($finalStatus -join "`n")) { throw 'Gate changed checkout status' }
Write-Host "Artifacts: $gate"
Write-Host 'BLOCK 58a DEMO GATE PASSED'
```
