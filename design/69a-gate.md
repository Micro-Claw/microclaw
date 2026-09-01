# Block 69a demo gate — confirmation delivery and event sequencing

Run this entire gate on the Windows demo machine, in one Microclaw browser
session, from `design69a/event-sequencing`. It scores blocks 69a-1, 69a-2 and
69a-3 together. It opens no Micro-Manager bridge beyond the normal demo session.

## 0. Pin and capture

In PowerShell at the checkout, verify the implementation is present:

```powershell
git merge-base --is-ancestor 0adbcd2 HEAD
if ($LASTEXITCODE -ne 0) { throw "Block 69a implementation is not checked out" }
node --version
New-Item -ItemType Directory -Force block69a-evidence | Out-Null
uv run microclaw serve 2>&1 | Tee-Object -FilePath block69a-evidence\server-console.log
```

Leave that PowerShell window running. In the browser, open DevTools, select the
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

Return `server-console.log`, `browser-console.log`, `computed-score.log`, the
node version, both confirmation IDs, and the five human limb observations.
