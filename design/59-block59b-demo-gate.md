# Block 59b demo-machine gate

Run from the repository root on the Windows demo machine with `MMConfig_demo.cfg`
loaded and the Micro-Manager ZMQ server listening on port 4827. First pin the
reviewed implementation (later gate/runbook commits are allowed):

```powershell
git merge-base --is-ancestor eddd1b6 HEAD
if ($LASTEXITCODE -ne 0) { throw "Block 59b implementation is not an ancestor of HEAD" }
```

Run the mechanical probe. It creates the evidence directory and a runtime safety
configuration from the committed 59a template; it does not install that file.

```powershell
uv run python design/59-block59b-demo-probe.py
if ($LASTEXITCODE -ne 0) { throw "Block 59b mechanical probe did not pass every limb" }
```

A `NOT EXERCISED` result is a failed limb. Stop and return the evidence even if
the reason describes a machine limitation. Do not rename devices, edit a `.cfg`,
or retry around it.

## Three short sessions

Close any old Microclaw window. Start Microclaw from this repository root using
the generated safety document:

```powershell
uv run microclaw --port 4827 --safety-config block59b-demo-evidence/generated-safety-config.yaml serve
```

For every session, do **not** name the objective, do **not** tell the agent what
is on any light-path position before it asks, and do **not** mention
`get_optical_path_documentation` or “the reference tool”. Start a fresh
conversation for each opening prompt.

### Session A — imaging works

Paste this verbatim:

> I have a sample on this microscope in brightfield mode. Please get set up and image it.

When asked what is on the routing positions, answer truthfully from the physical
machine. Do not volunteer that answer before the question. Do not ask it to save
anything in this session. Close Microclaw after the final answer, then run:

```powershell
$Evidence = Join-Path (Get-Location) "block59b-demo-evidence"
$Newest = Get-ChildItem -File "*_microclaw_history.jsonl" | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
if ($null -eq $Newest) { throw "Session A history JSONL was not written" }
Copy-Item -LiteralPath $Newest.FullName -Destination (Join-Path $Evidence "session-a.jsonl") -Force
```

Restart Microclaw with the same literal `uv run microclaw ... serve` command.
In Micro-Manager, choose an existing demo-camera mode whose frames have no
structure. If the camera offers no such existing mode, do not invent one or edit
the configuration: run the prompt, return the evidence, and report Session B as
NOT EXERCISED.

### Session B — blank frame

Paste this verbatim:

> Take one frame of the sample and report what you see. Afterward, ask any question you need and offer to remember my answer for a fresh session.

When the agent asks, answer truthfully what is on each existing routing position.
Accept the `save_knowledge` confirmation. Do not supply an `observed_on` mapping;
the tool must resolve it. Let the agent finish, close Microclaw, then run:

```powershell
$Evidence = Join-Path (Get-Location) "block59b-demo-evidence"
$Newest = Get-ChildItem -File "*_microclaw_history.jsonl" | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
if ($null -eq $Newest) { throw "Session B history JSONL was not written" }
Copy-Item -LiteralPath $Newest.FullName -Destination (Join-Path $Evidence "session-b.jsonl") -Force
$Confirmation = Join-Path $Newest.DirectoryName ($Newest.Name -replace "_history.jsonl$", "_confirmations.jsonl")
if (-not (Test-Path -LiteralPath $Confirmation)) { throw "Session B confirmations JSONL was not written" }
Copy-Item -LiteralPath $Confirmation -Destination (Join-Path $Evidence "session-b-confirmations.jsonl") -Force
```

Restore the demo camera's entry mode, then restart Microclaw with the same serve
command for a genuinely fresh conversation.

### Session C — stored routing

Paste this verbatim:

> Which position reaches the camera? Use only what this fresh session can read.

Do not repeat the routing answer yourself. Let the agent finish, close Microclaw,
then run these literal commands:

```powershell
$Evidence = Join-Path (Get-Location) "block59b-demo-evidence"
$Newest = Get-ChildItem -File "*_microclaw_history.jsonl" | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
if ($null -eq $Newest) { throw "Session C history JSONL was not written" }
Copy-Item -LiteralPath $Newest.FullName -Destination (Join-Path $Evidence "session-c.jsonl") -Force
uv run python design/59-block59b-score.py block59b-demo-evidence/session-a.jsonl block59b-demo-evidence/session-b.jsonl block59b-demo-evidence/session-c.jsonl block59b-demo-evidence/session-b-confirmations.jsonl --output block59b-demo-evidence/session-score.json
if ($LASTEXITCODE -ne 0) { throw "Block 59b session scorer did not pass every limb" }
```

Return the entire `block59b-demo-evidence` directory and all three original
`*_microclaw_history.jsonl` files, whether the gate passes, fails, or reports
NOT EXERCISED. The probe owns `gate.txt`; no PowerShell transcript is evidence.
