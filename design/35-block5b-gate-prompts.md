# design/35 Block 5b — guided install and acknowledgement retries

This runbook verifies branch `design17/guided-install`. The main implementation
is pinned at `c2ee97c` and its bounded live-enumeration correction at `11000bd`;
later runbook or correction commits are valid descendants.
Run G0, G1, and G3 on the demo machine. Run G2 on M5 only after the demo gates
pass. Preserve and return each complete evidence directory.

All commands are Windows PowerShell 5.1-safe. Set the invocation once. The
interpreter is an invocation too; do not hand-substitute either command later.

```powershell
$Run = "uv run"     # uv-managed checkout (demo machine)
# $Run = ""         # microclaw and python already on PATH -- uncomment instead

function mc { if ($Run) { uv run microclaw @args } else { microclaw @args } }
function py { if ($Run) { uv run python @args }    else { python @args } }
```

`$LASTEXITCODE` is valid after `git`, `mc`, and `py`, which invoke native
executables. It is stale after PowerShell cmdlets such as `Select-String`; every
such check below therefore records match counts instead of `$LASTEXITCODE`.

Do not use `Start-Transcript` for interactive evidence. Windows PowerShell 5.1
does not capture a child process's console writes. The interactive gates below
explicitly request manual console selection and copy-paste instead.

## G0 — branch identity and full suite (both machines)

```powershell
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Evidence = "block5b-$Stamp"
New-Item -ItemType Directory -Path $Evidence
git fetch origin > "$Evidence\git-fetch.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\git-fetch-exit.txt"
git switch design17/guided-install > "$Evidence\git-switch.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\git-switch-exit.txt"
git pull --ff-only > "$Evidence\git-pull.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\git-pull-exit.txt"
git status --short > "$Evidence\status.txt" 2>&1
git rev-parse HEAD > "$Evidence\head.txt" 2>&1
git merge-base --is-ancestor c2ee97c HEAD > "$Evidence\implementation-ancestor.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\implementation-ancestor-exit.txt"
git merge-base --is-ancestor 11000bd HEAD > "$Evidence\bounded-live-ancestor.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\bounded-live-ancestor-exit.txt"
py -m pytest -q > "$Evidence\pytest.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\pytest-exit.txt"
Copy-Item "design\35-block5b-gate-prompts.md" "$Evidence\runbook.md"
```

It worked when every `*-exit.txt` is `0`, `status.txt` is empty, and pytest has
no failures. Judge the suite by failures and collected total, not passed count.
Block 5b adds sixteen tests, so **1492 tests are collected**. The coordinator's macOS
expectation is 1393 passed / 99 skipped; Windows should shift the same sixteen
platform-conditional tests to approximately 1377 passed / 115 skipped. The three
known warnings remain one `StarletteDeprecationWarning` and two empty-image
`phase_cross_correlation` warnings.

## G1 — demo machine, three failed readiness checks leave installation complete

Close Micro-Manager completely. First record structural controls:

```powershell
$Checks = @(Select-String -Path "install.bat" -Pattern '^"%MC_EXE%" check-bridge$')
$Checks > "$Evidence\installer-check-lines.txt"
$Checks.Count > "$Evidence\installer-check-count.txt"
$Setup = @(Select-String -Path "install.bat" -Pattern '^"%MC_EXE%" init --yes$')
$Setup > "$Evidence\installer-setup-lines.txt"
$Setup.Count > "$Evidence\installer-setup-count.txt"
$Success = @(Select-String -Path "install.bat" -Pattern 'Installation complete','three readiness checks','exit /b 0')
$Success > "$Evidence\installer-success-lines.txt"
$Success.Count > "$Evidence\installer-success-count.txt"
```

Capture the standalone readiness failure exactly as the installer will show it:

```powershell
mc check-bridge > "$Evidence\check-bridge-not-ready.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\check-bridge-not-ready-exit.txt"
$FriendlyFailure = @(Select-String -Path "$Evidence\check-bridge-not-ready.txt" -SimpleMatch "No working Micro-Manager ZMQ bridge answered on port 4827 within 5 seconds.")
$FriendlyFailure.Count > "$Evidence\check-bridge-friendly-count.txt"
$Traceback = @(Select-String -Path "$Evidence\check-bridge-not-ready.txt" -Pattern "Traceback","Exception in thread")
$Traceback.Count > "$Evidence\check-bridge-traceback-count.txt"
```

It worked when the native exit is nonzero, the friendly count is `1`, the
traceback count is `0`, and the captured output contains only the short statement
of the port and bounded check that did not answer.

Give this fresh-install gate an isolated empty roaming-data directory so an
existing demo safety profile cannot turn it into the upgrade path. This does not
move or edit the operator's real profile. Run the installer with Micro-Manager
still absent, and restore the environment immediately afterwards:

```powershell
$OriginalAppData = $env:APPDATA
$GateAppData = Join-Path $Evidence "fresh-appdata"
New-Item -ItemType Directory -Path $GateAppData
$env:APPDATA = (Resolve-Path $GateAppData).Path
cmd /c install.bat
echo $LASTEXITCODE > "$Evidence\installer-no-mm-exit.txt"
$env:APPDATA = $OriginalAppData
```

At each of the three prompts, press a key without starting Micro-Manager. After
the command returns, select the complete command and console output, copy it,
and save it as `$Evidence\installer-no-mm-manual-copy-paste.txt`.

It worked when both command counts are `1`; the console shows three bounded
checks, each says no working bridge was found on port 4827, then says Microclaw
is installed and prints the full manual `init` command; and the recorded exit is
`0`. The command must return in roughly fifteen seconds plus installation time,
not wait indefinitely. `installer-success-count.txt` is supporting structure;
judge the actual branch and exit from the manual evidence.

## G2 — M5, real bridge happy path and corrected second acknowledgement

Open Micro-Manager, load the intended configuration, and tick **Tools → Options
→ Run pycro-manager server on port 4827**. First prove the bounded real-protocol
check succeeds:

```powershell
mc check-bridge > "$Evidence\check-bridge-ready.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\check-bridge-ready-exit.txt"
```

It worked when the exit is `0` and the output names the answering bridge, port,
and Micro-Manager Core version. A TCP listener alone cannot produce this result.

Now create a fresh, explicitly gate-only draft and evidence directory:

```powershell
$AckDraft = Join-Path $Evidence "corrected-second-attempt.yaml"
$AckEvidence = Join-Path $Evidence "corrected-second-attempt-inventory"
mc first-launch-setup --out $AckDraft --evidence-out $AckEvidence
echo $LASTEXITCODE > "$Evidence\corrected-second-attempt-exit.txt"
```

At the hardware-contact gate, first type this near miss:

```text
I ACKNOWLEDGE HARDWARE CONTAC
```

Then type the exact required acknowledgement on the second prompt and complete
the normal interview using declarations and limits reviewed for M5. Select the
complete command and console output, copy it, and save it as
`$Evidence\corrected-second-attempt-manual-copy-paste.txt`.

Record the transcript's discriminating evidence without relying on a stale exit:

```powershell
$AckTranscript = Get-ChildItem $AckEvidence -Filter "first-launch-transcript-*.txt" | Select-Object -First 1
Copy-Item $AckTranscript.FullName "$Evidence\corrected-second-attempt-transcript.txt"
$NearMiss = @(Select-String -Path $AckTranscript.FullName -SimpleMatch "I ACKNOWLEDGE HARDWARE CONTAC")
$NearMiss.Count > "$Evidence\corrected-near-miss-count.txt"
$Retry = @(Select-String -Path $AckTranscript.FullName -SimpleMatch "2 tries remaining")
$Retry.Count > "$Evidence\corrected-retry-message-count.txt"
Test-Path $AckDraft > "$Evidence\corrected-draft-exists.txt"
```

It worked when setup exits `0`, both match counts are at least `1`, the manual
copy shows the correction being accepted, and the unreviewed draft exists. This
gate draft is evidence only: do not replace M5's deployed reviewed profile with
it. Preserve it for coordinator review.

## G3 — demo machine, three bad acknowledgements refuse before connection

Micro-Manager must remain closed. Choose fresh paths:

```powershell
$BadDraft = Join-Path $Evidence "three-bad-must-not-exist.yaml"
$BadEvidence = Join-Path $Evidence "three-bad-evidence"
mc first-launch-setup --out $BadDraft --evidence-out $BadEvidence
echo $LASTEXITCODE > "$Evidence\three-bad-exit.txt"
```

Type `wrong-one`, `wrong-two`, and `wrong-three` at the three acknowledgement
prompts. Select the complete command and console output, copy it, and save it as
`$Evidence\three-bad-manual-copy-paste.txt`.

```powershell
Test-Path $BadDraft > "$Evidence\three-bad-draft-exists.txt"
$BadTranscript = Get-ChildItem $BadEvidence -Filter "first-launch-transcript-*.txt" | Select-Object -First 1
Copy-Item $BadTranscript.FullName "$Evidence\three-bad-transcript.txt"
$BadInputs = @(Select-String -Path $BadTranscript.FullName -Pattern '^wrong-(one|two|three)$')
$BadInputs.Count > "$Evidence\three-bad-input-count.txt"
$Connect = @(Select-String -Path $BadTranscript.FullName -SimpleMatch "Connecting to the already-running")
$Connect.Count > "$Evidence\three-bad-connect-count.txt"
```

It worked when the native command exit is nonzero, the draft-exists file says
`False`, all three inputs appear (`3`), the connection count is `0`, and the
manual/transcript evidence ends with the same hardware-contact acknowledgement
refusal as before. Because Micro-Manager is absent, any attempted connection
would also be visible as a bridge error; it must never occur.
