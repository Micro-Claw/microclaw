# Block 69a demo gate — confirmation delivery and event sequencing

Run this entire gate on the Windows demo machine, in one Microclaw browser
session, from `design69a/event-sequencing`. It scores blocks 69a-1, 69a-2 and
69a-3 together. It opens no Micro-Manager bridge beyond the normal demo session.

## 0. Pin and capture

**Before anything else, relax this shell's error preference.** The demo
machine's PowerShell runs with `$ErrorActionPreference = 'Stop'`, which turns
*any* native command's write to stderr into a terminating error. `uv` writes
`Building microclaw @ file:///...` to stderr whenever it rebuilds — which a
freshly checked-out branch guarantees — so under `Stop` the gate dies on the
line that starts the server. Three attempts at this gate died that way, with
`| Tee-Object`, with `> file 2>&1`, and with `> file`. The command form was
never the difference.

Run this **first, in every PowerShell window you use for this gate**, and put it
back at the end:

```powershell
$PrevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
```

Then verify the implementation is present:

```powershell
git merge-base --is-ancestor 0a2090e HEAD
if ($LASTEXITCODE -ne 0) { throw "Block 69a implementation is not checked out" }
New-Item -ItemType Directory -Force block69a-evidence | Out-Null
```

**Prove the capture in two seconds** before spending a session on it:

```powershell
uv run python -c "import sys; sys.stderr.write('to stderr\n'); print('to stdout')" > block69a-evidence\probe.log
Get-Content block69a-evidence\probe.log
```

`to stdout` must be in the file. `to stderr`, and uv's build progress, appear in
the window — that is expected now, not a failure. If PowerShell still raises
`NativeCommandError`, stop and report it.

**Stdout only, deliberately.** Nothing this gate scores is on stderr: every line
the scorer reads is a bare `print` to stdout — the `[microclaw turn ...]` event
lines, the confirmation audit, the startup banner. Merging stderr would add
nothing and is what `2>&1` does. There is also no `node` check: node runs the
off-rig JS suite, never this gate.

Now start the server:

```powershell
uv run microclaw serve > block69a-evidence\server-console.log
```

**`microclaw serve` was historically silent under any redirect until it died**
(design/35's register, from block 4d's gate: block-buffered stdout and a loop
that never returns). Block 69a-3 gives the event lines `flush=True`, and the
startup banner already had it, so the file now fills while the server runs —
which is what the next check confirms. In a **second** PowerShell window (set its
error preference too):

```powershell
Get-Content block69a-evidence\server-console.log -Tail 5
```

`Microclaw GUI: http://127.0.0.1:8000  (Ctrl-C to stop)` means it is up. Keep
this window; the capture check before the five-minute limb uses it.

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
- **Limb 2.** While that confirmation is pending, disable networking for about 60 seconds
   in DevTools. The banner must remain visible and the status must become exactly
   `Live updates interrupted; checking Microclaw…`. Re-enable networking without
   reloading.
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
Ctrl-C. Run this command unedited from the checkout:

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

## 4. Restore

In each PowerShell window you used:

```powershell
$ErrorActionPreference = $PrevEAP
```

A gate must not leave production state changed. This one only relaxes a
preference inside the operator's own windows, but it says so and puts it back.
