# Block 58c demo gate — human mechanisms

Run in Windows PowerShell 5.1 from the `design58/two-slots` checkout. The first
command is step zero: it backs up both roaming user data and the legacy managed
environment, and prints the copy location.

```powershell
$ErrorActionPreference='Stop'; $repo=(git rev-parse --show-toplevel).Trim(); Set-Location $repo; $stamp=Get-Date -Format 'yyyyMMdd-HHmmss'; $evidence=Join-Path ([Environment]::GetFolderPath('MyDocuments')) "block58c-$stamp"; $backup=Join-Path $evidence 'backup'; New-Item -ItemType Directory -Force -Path $backup | Out-Null; if(Test-Path "$env:APPDATA\microclaw"){Copy-Item "$env:APPDATA\microclaw" (Join-Path $backup 'appdata-microclaw') -Recurse -Force}; if(Test-Path "$env:LOCALAPPDATA\microclaw\env"){Copy-Item "$env:LOCALAPPDATA\microclaw\env" (Join-Path $backup 'legacy-env') -Recurse -Force}; Set-Content "$env:LOCALAPPDATA\microclaw\58c-gate-evidence.txt" $evidence; Write-Host "BACKUP COPY: $backup"
```

Pin the implementation mechanism, then hash `%APPDATA%\microclaw` before the
installer reads or writes anything. This command runs unedited and fails loudly.

```powershell
git merge-base --is-ancestor 01b634f HEAD; if($LASTEXITCODE -ne 0){throw 'This checkout does not contain block 58c commit 01b634f'}; Get-ChildItem "$env:APPDATA\microclaw" -File -Recurse -ErrorAction SilentlyContinue | Sort-Object FullName | Get-FileHash -Algorithm SHA256 | ForEach-Object{"$($_.Hash) $($_.Path.Substring($env:APPDATA.Length))"} | Set-Content (Join-Path $evidence 'appdata-before.txt'); $nonuv=Join-Path $evidence 'nonuv'; uv venv --python 3.12 $nonuv; if($LASTEXITCODE -ne 0){throw 'non-uv fixture creation failed'}; uv pip install --python "$nonuv\Scripts\python.exe" --no-deps $repo; if($LASTEXITCODE -ne 0){throw 'non-uv fixture install failed'}; $sp=(& "$nonuv\Scripts\python.exe" -c "import sysconfig; print(sysconfig.get_path('purelib'))").Trim(); @{python="$nonuv\Scripts\python.exe";site_packages=$sp}|ConvertTo-Json|Set-Content (Join-Path $evidence 'nonuv.json'); & "$nonuv\Scripts\python.exe" -c "import hashlib,pathlib; p=pathlib.Path(r'$sp'); d=hashlib.sha256(); [(d.update(str(x.relative_to(p)).encode()),d.update(x.read_bytes())) for x in sorted(y for y in p.rglob('*') if y.is_file())]; print(d.hexdigest())" | Set-Content (Join-Path $evidence 'nonuv-before.txt')
```

Exercise migration and installer ownership by running `install.bat` twice. The
first invocation must visibly name the one legacy environment it migrates and,
if `where microclaw` resolves outside the managed root, print that the old
environment was left untouched and the icon moved. The second invocation tests
idempotence; its active selector is captured on both sides.

```powershell
$savedPath=$env:PATH; $env:PATH="$nonuv\Scripts;$savedPath"; cmd /c install.bat > (Join-Path $evidence 'install-first.txt') 2>&1; $env:PATH=$savedPath; if($LASTEXITCODE -ne 0){throw 'first install.bat failed'}; Get-Content "$env:LOCALAPPDATA\microclaw\active-slot.txt" | Set-Content (Join-Path $evidence 'active-before-second.txt'); cmd /c install.bat > (Join-Path $evidence 'install-second.txt') 2>&1; if($LASTEXITCODE -ne 0){throw 'second install.bat failed'}; Get-Content "$env:LOCALAPPDATA\microclaw\active-slot.txt" | Set-Content (Join-Path $evidence 'active-after-second.txt')
```

Build the real inactive-slot validator using uv's explicit `--python` target.
This exercises a second slot under the managed root, never the Python on PATH.

```powershell
$root="$env:LOCALAPPDATA\microclaw"; uv venv --python 3.12 "$root\env-b"; if($LASTEXITCODE -ne 0){throw 'uv could not create env-b'}; uv pip install --python "$root\env-b\Scripts\python.exe" "$repo[serve]"; if($LASTEXITCODE -ne 0){throw 'uv could not build env-b'}; $sha=(git rev-parse HEAD).Trim(); & "$root\env-b\Scripts\python.exe" -c "from microclaw.updates import write_slot_marker; write_slot_marker('$sha',1)"; if($LASTEXITCODE -ne 0){throw 'env-b marker failed'}
```

Exercise the desktop mechanism: double-click the **Microclaw desktop icon**.
While its console is open, capture the actual child command line (this is not
inferred from files), then close it with Ctrl+C.

```powershell
Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -match 'env-[ab].*microclaw.exe.*serve'} | Select-Object ProcessId,ExecutablePath,CommandLine | Format-List | Out-File (Join-Path $evidence 'process-command.txt'); if(-not (Select-String -Path (Join-Path $evidence 'process-command.txt') -Pattern 'env-[ab].*microclaw.exe')){throw 'desktop child process was not captured'}
```

Close Micro-Manager completely, then double-click the desktop icon again. Let
the child report the bridge refusal. Do not start it again. This specifically
exercises “health then bridge exit” and detects an automatic relaunch by leaving
more than one new nonce line. Copy the visible console output:

```powershell
Copy-Item "$root\launcher.log" (Join-Path $evidence 'launcher-after-closed-mm.log') -Force; Get-Content "$root\active-slot.txt" | Set-Content (Join-Path $evidence 'active-after-closed-mm.txt')
```

Exercise rollback by writing `b` as pending, temporarily renaming env-b's real
executable, and double-clicking the icon once. Restore the executable, then
double-click once more: only this successful launch must print the deferred
rollback report. Capture that console text in `rollback-next-launch.txt`.

```powershell
Set-Content "$root\pending-slot.txt" 'b' -Encoding ASCII; Rename-Item "$root\env-b\Scripts\microclaw.exe" 'microclaw.exe.block58c'; Write-Host 'Double-click the desktop icon once now; wait for it to fail, then press Enter here.'; Read-Host | Out-Null; Rename-Item "$root\env-b\Scripts\microclaw.exe.block58c" 'microclaw.exe'; Write-Host 'Double-click the desktop icon once now; copy its rollback line into rollback-next-launch.txt, then press Enter here.'; Read-Host | Out-Null; if(-not(Test-Path (Join-Path $evidence 'rollback-next-launch.txt'))){New-Item -ItemType File (Join-Path $evidence 'rollback-next-launch.txt') | Out-Null}
```

Finally hash roaming data again and run the computing gate. The program owns
`gate.txt` and `results.json`; send the entire evidence directory back.

```powershell
Get-ChildItem "$env:APPDATA\microclaw" -File -Recurse -ErrorAction SilentlyContinue | Sort-Object FullName | Get-FileHash -Algorithm SHA256 | ForEach-Object{"$($_.Hash) $($_.Path.Substring($env:APPDATA.Length))"} | Set-Content (Join-Path $evidence 'appdata-after.txt'); powershell -NoProfile -ExecutionPolicy Bypass -File "$repo\design\58-block58c-demo-gate.ps1"; if($LASTEXITCODE -ne 0){throw 'BLOCK 58c GATE DID NOT PASS; send the evidence folder anyway'}
```
