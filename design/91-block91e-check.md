# Block 91e check — change the reasoning level within a session

**Machine: the llama.cpp workstation**, Micro-Manager running (demo config or the
real rig — either works; this check makes no exposures beyond what you ask
for). **Windows PowerShell 5.1** and **Chrome**. About **10 minutes**.

What this checks: the browser's reasoning-level control changes the level for
the session, shows what the change costs, and every model call records the level
it ran at.

## 0 — the model server

Running as before (`… -c 131072 … --api-key testkey`). From a second window:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
```

## 1 — get the branch

```powershell
cd C:\code\microclaw
git fetch origin
git checkout design91/91e-effort
git pull
git merge-base --is-ancestor e611762 HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK" } else { "STOP - wrong tree" }
```

## 2 — start Microclaw with no level set

```powershell
cd C:\code\microclaw
$env:MICROCLAW_LLM_BASE_URL = 'http://127.0.0.1:8080'
$env:MICROCLAW_LLM_REASONING_EFFORT = ''
.venv\Scripts\python.exe -m microclaw serve
```

Open `http://127.0.0.1:8000` in Chrome and enter the key `testkey` from the key
chip as before.

**(E1)** Beside the model button there is a reasoning control. What does it
show? (Expected: **Server default**.)

## 3 — three short turns, switching between them

Send each, waiting for the reply:

> What is the stage position?

Now set the control to **Medium**. **(E2)** Copy the notice that appears (it
stays until you click its ✕).

> What is the stage position?

Set the control back to **Server default**, then:

> What is the stage position?

**(E3)** Did the replies after a switch feel slower than the first? Roughly how
many seconds each?

Press **Ctrl+C** in the `serve` window.

## 4 — read the record

```powershell
cd C:\code\microclaw
.venv\Scripts\python.exe design\91-block91e-check.py --session .
```

Expect three or more calls, the `effort` column reading `server default`, then
`medium`, then `server default`, and a last line
`levels in order: server default -> medium -> server default`. The `processed`
column shows how much each call made the server re-process; a level the server
has not seen this run shows a large number on its first call.

## 5 — send

The output of step 4, your answers to **E1–E3**, and the session's
`*_microclaw_usage.jsonl` (newest in `C:\code\microclaw`).
