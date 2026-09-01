# Block 69a demo gate — confirmation delivery and event sequencing

Run this entire gate on the Windows demo machine, in one Microclaw browser
session, from `design69a/event-sequencing`. It scores blocks 69a-1, 69a-2 and
69a-3 together. It opens no Micro-Manager bridge beyond the normal demo session.

## 0. Pin, warm uv, and capture

```powershell
cd D:\Code\microclaw
git fetch origin
git checkout design69a/event-sequencing
git pull
git merge-base --is-ancestor 0a2090e HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - the implementation is in this tree" }
else { "STOP - wrong tree, do not run the gate" }
```

**Warm uv once, unredirected.** A freshly checked-out branch leaves uv a rebuild
to do, and it writes `Building microclaw @ file:///...` to stderr. This shell
runs with `$ErrorActionPreference = 'Stop'`, so that line terminates any
*redirected* uv command — which is why the first commands of this gate died and
why the same `> file 2>&1` form has worked in every other runbook for months:
by the time those ran, uv had nothing left to build.

```powershell
uv run python -c "print('uv warm')"
```

Let it build. Red build text here is expected and harmless — nothing is
redirected, so nothing terminates. Every redirected command below then behaves
exactly as it does in every other runbook.

```powershell
New-Item -ItemType Directory -Force block69a-evidence | Out-Null
$Server = Start-Process -FilePath "uv" `
  -ArgumentList "run","microclaw","serve" `
  -RedirectStandardOutput "block69a-evidence\server-console.log" `
  -RedirectStandardError  "block69a-evidence\server-stderr.log" `
  -NoNewWindow -PassThru
Start-Sleep -Seconds 8
Get-Content block69a-evidence\server-console.log -Tail 5
```

**Why `Start-Process` and not `> file`.** PowerShell 5.1 does not capture a
native child process's stdout — `CLAUDE.md` records this and block 58e's gate
says the same in a comment, which is why both existing launches of `serve` in
this repo (`scripts/updater-launcher.ps1`, `design/58-block58e-demo-gate.ps1`)
use `Start-Process -NoNewWindow` and capture nothing. A `> file` redirect leaves
the log empty for a process that never exits, which is design/35's register row
from block 4d and is what happened on the first run of this gate.
`-RedirectStandardOutput` hands the child its own file handle, so the server
writes to the file directly and PowerShell is not in the middle.

You must see `Microclaw GUI: http://127.0.0.1:8000  (Ctrl-C to stop)` in that
`Get-Content` output. **If the file is still empty, stop and report it** — the
gate cannot be scored without the server's event log, and everything after this
line costs a five-minute wait.

Stop the server at the end with `Stop-Process -Id $Server.Id`, not Ctrl-C. A
hard stop is safe here: block 69a-3 gives the event lines `flush=True`, so each
one is on disk when it is written rather than at exit.

Leave the first PowerShell window running. In the browser, open DevTools, select the
Console tab, enable **Preserve log**, and clear the console. The server console
and browser console from this same session are both required. At the end,
right-click the DevTools console and choose **Save as…**, saving it as
`block69a-evidence\browser-console.log`. Do not substitute a screenshot: the
computed comparison needs the text and turn IDs.

Use the recognizable marker `PAYLOAD_69A_GATE_SECRET` in the forced
confirmation's operator-facing summary. It must be visible in the banner and
will also appear in the intentional `[microclaw] Confirmation audit:` line. It
must not occur in any `[microclaw turn ...]` event-log line.

First submit: `Save a knowledge entry under rig/gate_marker whose value is
exactly PAYLOAD_69A_GATE_SECRET.` Verify the confirmation banner's summary
contains that marker, then decline it. This supplies limb 8's real secret-bearing
event without changing the machine's knowledge.

Before starting the five-minute limb, open a second PowerShell in the checkout
and run this capture check unedited:

```powershell
$ServerLog = "block69a-evidence\server-console.log"
if (-not (Test-Path $ServerLog)) { throw "Server console log does not exist" }
if ((Get-Item $ServerLog).Length -eq 0) { throw "Server console log is still empty" }
if (-not (Select-String -Path $ServerLog -Pattern '^\[microclaw turn [0-9a-f]+\] seq \d+ ' -Quiet)) { throw "No block 69a event line has reached the server log" }
```

Stop here if it throws. This check must succeed while the server is still alive.

## 1. Pending, silence, recovery, and the timeout priority

Submit this prompt verbatim:

> First acquire exactly 2 demo-camera frames so acquisition progress is shown.
> Then request a 100000-frame timelapse. Do not replace either acquisition with
> a description; call the acquisition tool for each.

Judge and record each limb independently:

- **Limb 1.** After the second acquisition requests confirmation, the banner is visible.
   The status must first read `Waiting for your confirmation.` and then include
   `Ns remaining.` as the poll updates it.
- **Limb 2.** While that confirmation is pending, set DevTools **Network →
   Throttling → Offline** for about 60 seconds, then set it back to **No
   throttling** *without reloading*. The banner must remain visible and the
   status must become exactly `Live updates interrupted; checking Microclaw…`.

   In Chrome or Edge: **F12 → Network**, then the toolbar dropdown reading
   **No throttling** → **Offline**; set it back to **No throttling** after.
   (Alternative route: DevTools **⋮ → More tools → Network conditions**, tick
   **Offline**.) Firefox has the same dropdown on its **Network** panel.

   This is **page-scoped**: it blocks only this tab's requests. It does not touch
   the machine's network adapter and cannot affect a Remote Desktop or SSH
   session, so it is safe when the demo machine is being driven remotely — which
   it normally is. Do not disable the adapter itself.

   Silence is declared after 30 s with no complete frame (three missed 10 s
   keepalives), so 60 s offline clears it comfortably. If you cannot reach the
   throttling control at all, report limb 2 as **NOT EXERCISED** — that is not a
   pass, and it is one of the two things this gate exists for.
- **Limb 4.** Do not approve or decline. `CONFIRM_TIMEOUT_S` is 300 seconds and has no
   configuration path, so this limb costs a real five-minute wait. Do not patch
   or shorten it: this production timeout path is the incident's mechanism.
   Because the same turn already emitted `frames N / M`, expiry must replace
   that competing progress text with exactly
   `Confirmation timed out and was declined`. A frozen `frames N / M` fails
   limb 4 even if the refusal later appears in the transcript.
- **Limb 3.** Let recovery finish without reloading. The composer must be usable, the
   spinner must be gone, and the submitted prompt must not be restored into the
   message box.

Do not collapse these into one verdict. In particular, limb 4 is the
load-bearing control: it reaches timeout disclosure with acquisition progress
already competing below it.

## 2. Reload recovery

Submit the same prompt again. When the forced confirmation appears, copy its ID
from the pending-confirmation response in DevTools Network (`GET /api/confirm`).
Reload the page while it is still pending. The same confirmation must reappear;
copy the ID again and verify the two IDs are identical. Decline it after the
comparison. This is limb 5.

## 3. Save and compute

Save the DevTools console as described in step 0, then stop the server with
`Stop-Process -Id $Server.Id`. Run this command unedited from the checkout:

```powershell
uv run python design/69a-gate.py --server-log block69a-evidence\server-console.log --browser-log block69a-evidence\browser-console.log --forbidden PAYLOAD_69A_GATE_SECRET --log block69a-evidence\computed-score.log
if ($LASTEXITCODE -ne 0) { throw "Block 69a computed gate failed" }
```

The script reports limbs 6–8 independently and exits nonzero for either `FAIL`
or `NOT EXERCISED`. Limb 6 checks each server turn's sequence arithmetic and
summary count. Limb 7 requires both logs and compares the browser's last applied
sequence with the matching server turn. Limb 8 rejects per-delta event lines and
the recognizable payload marker. The scorer owns `computed-score.log`; a shell
transcript is not its evidence.

Return `server-console.log`, `browser-console.log`, `computed-score.log`, both
confirmation IDs, and the five human limb observations. `uv run` prints its
ordinary `Building microclaw @ file:///...` progress to stderr here; with no
pipeline in this command that is display only, not a failure.
