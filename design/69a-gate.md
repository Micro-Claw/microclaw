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
git merge-base --is-ancestor 3a6318d HEAD
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

Stop the server at the end with the block in step 3, not Ctrl-C and **not**
`Stop-Process` on its own. A hard stop is safe here: block 69a-3 gives the event
lines `flush=True`, so each one is on disk when it is written rather than at
exit. `$Server.Id` is `uv`'s process, and `uv run` runs microclaw in a child of
it — `Stop-Process -Id $Server.Id` kills only the parent, so the child keeps
running and keeps holding `server-console.log` open. That is why round 3 could
not zip its evidence folder until the console window was closed by hand.

Leave the first PowerShell window running.

**The browser is Firefox** (the desktop shortcut's default on this machine).
Press **F12**, and set up both panels now — the two artifacts below are
different things and a screenshot substitutes for neither:

- **Network** tab → tick **Persist Logs**. At the end, export it with the
  download-arrow button, or right-click any request → **Save All As HAR**, to
  `block69a-evidence\network.har`. This is the same capture block 58d's gate
  uses, and it is what shows whether the 1 Hz recovery poll was actually
  running.
- **Console** tab → tick **Persist Logs**, then clear it, and **make sure the
  `Warnings` filter chip is lit**. Limb 7's line is written with `console.warn`,
  so it is hidden whenever that chip is off — which is the leading explanation
  for rounds 2 and 3 finding nothing to export.

**Limb 7 wants exactly one line per completed turn**, and nothing else from
DevTools:

```
Microclaw turn settled; turn: <32 hex chars> last applied seq: <N>
```

**Everything else limb 7 could want is already in the HAR** — its
`POST /api/prompt` bodies carry every `data:` frame with its `seq`, so "the
browser received all N events the server numbered" is computable without the
console. Round 3's HAR proves it: 33 of 33 and 24 of 24 numbered events, no
gaps, both ending on `done`. What the console line adds is that the page's own
apply loop got there **and can say so during a future incident**, which is the
entire reason sequencing shipped. That is the claim, and it is why this is worth
ten seconds and not a fourth attempt at an export menu.

Two ways to capture it, in order of preference:

1. Drag-select the line with the mouse, `Ctrl+C`, paste into Notepad, save as
   `block69a-evidence\browser-console.log`. Plain text selection, no menu and no
   `Ctrl+A` — round 3 established that `Ctrl+A` does not select console output
   and that the context menu route does not exist on this machine.
2. **Or just screenshot the Console panel.** Two lines of legible text is a
   complete artifact for this limb; the coordinator reads the numbers and scores
   them against the server log by hand. Do not spend time hunting for an export.

**If no such line is present with `Warnings` lit, after a turn has completed,
stop and report that.** It is a finding about the diagnostic, not an operator
failure. Note the line is written by the page that *ran* the turn: a page that
adopted a turn after a reload runs none of its own and writes none, so look
after a turn you submitted on the current page.

## 0b. Seed limb 8's marker — mandatory, and skipped twice already

Rounds 2 and 3 both skipped this and limb 8 was void both times. Do it before
anything else, and run the check below before you spend five minutes on
anything.

Submit exactly:

> Save a knowledge entry under rig/gate_marker whose value is exactly
> PAYLOAD_69A_GATE_SECRET.

Verify the confirmation banner's summary contains `PAYLOAD_69A_GATE_SECRET`,
then **decline** it. That supplies limb 8's real secret-bearing event without
changing the machine's knowledge. The marker will also appear in the intentional
`[microclaw] Confirmation audit:` line, which is by design; what limb 8 checks is
that it reaches no `[microclaw turn ...]` event-log line.

Now open a second PowerShell in the checkout and run this capture check
unedited:

```powershell
$ServerLog = "block69a-evidence\server-console.log"
if (-not (Test-Path $ServerLog)) { throw "Server console log does not exist" }
if ((Get-Item $ServerLog).Length -eq 0) { throw "Server console log is still empty" }
if (-not (Select-String -Path $ServerLog -Pattern '^\[microclaw turn [0-9a-f]+\] seq \d+ ' -Quiet)) { throw "No block 69a event line has reached the server log" }
if (-not (Select-String -Path $ServerLog -Pattern 'PAYLOAD_69A_GATE_SECRET' -Quiet)) { throw "The marker submit above was skipped - limb 8 cannot be scored" }
"CAPTURE OK - server log is live and the marker reached it"
```

Stop here if it throws. This check must succeed while the server is still alive.

**Do not clear or re-open the Network panel after this point.** Its `POST
/api/prompt` entry must be in the panel from the instant you submit, or limb 2a
has no stream to read. Round 2's HAR began twelve seconds after the submit and
that request was never captured.

## 1. Pending, keepalive delivery, recovery, and the timeout priority

Submit this prompt verbatim:

> First acquire exactly 2 demo-camera frames as a timelapse with interval 0 s,
> so acquisition progress is shown. Then, without stopping to ask me anything,
> request a 100000-frame timelapse with interval 0 s. Do not replace either
> acquisition with a description; call the acquisition tool for each, and do
> both in this one reply.

**Both acquisitions must happen in this one turn**, because `runTurn` clears the
progress text at turn start and limb 4's whole control is a *stale* progress
number still on screen when the confirmation expires. Rounds 2 and 3 both split
into two turns — round 2 because the prompt left the interval unstated and
Microclaw asked for it, round 3 because Microclaw stopped and asked anyway — and
limb 4 measured nothing both times. **The wording alone does not hold it**, so
check before you spend the five minutes.

As soon as the confirmation banner appears, run this in the second PowerShell,
unedited:

```powershell
$last = (Select-String -Path $ServerLog -Pattern '^\[microclaw turn ([0-9a-f]+)\] seq \d+ confirm_request$' | Select-Object -Last 1)
if (-not $last) { throw "No confirm_request has reached the server log yet" }
$t = $last.Matches.Groups[1].Value
if (Select-String -Path $ServerLog -Pattern "^\[microclaw turn $t\] seq \d+ acquisition_progress$" -Quiet) {
  "LIMB 4 ARMED - stale progress is competing in turn $t; start the five-minute wait"
} else {
  "SPLIT TURN - decline this confirmation, submit the prompt again, and re-run this check. Do NOT spend the five minutes."
}
```

Both branches were run against rounds 2's and 3's real `server-console.log`
files: each correctly reported `SPLIT TURN` for the step-1 turn and
`LIMB 4 ARMED` for the step-2 turn, so the check discriminates rather than
matching nothing.

In both rounds it was the *second* submit that did both acquisitions in one
turn. That is n=2 and not a mechanism to rely on — use the check, not the
pattern.

Judge and record each limb independently:

- **Limb 1.** After the second acquisition requests confirmation, the banner is visible.
   The status must first read `Waiting for your confirmation.` and then include
   `Ns remaining.` as the poll updates it.
- **Limb 2a — real-browser keepalives.** Leave the confirmation pending and
   select its long-running `POST /api/prompt` request in Firefox's **Network**
   panel. Open that request's **Response** tab. The turn thread is blocked on
   the confirmation and emits no semantic events, so the streaming response
   must accumulate a `: ping` comment frame about every 10 seconds. Watch for
   35 seconds and require at least three complete `: ping` frames, each followed
   by a blank line. Keep the request in the HAR saved at the end of the gate.
   Fewer than three frames is a FAIL; a Firefox build that cannot display or
   export the live response makes 2a NOT EXERCISED, never PASS.

- **Limb 2b — off-rig silence detector.** No browser menu on this loopback
   deployment can create the required open-but-silent transport: Firefox's
   offline/throttling controls continue serving `127.0.0.1`, while stopping the
   server closes the stream and tests a different path. The 30-second detector
   is therefore settled off-rig by
   `test_stream_silent_from_its_first_byte_is_armed_and_settles_once` and
   `run_browser_turn` in `tests/test_recovery_js.py`. Their `reader.read()`
   genuinely never resolves; the injected clock fires after 30 seconds, the UI
   renders `Live updates interrupted; checking Microclaw…`, recovery settles
   once, and the composer is restored without prompt resubmission. Do not try to
   reproduce 2b through another browser menu on the demo machine.
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
from the pending-confirmation response in DevTools Network (`GET /api/confirm`),
then reload the page while it is still pending.

This step costs a second real five-minute wait, at limb 5d. That is deliberate:
the reloaded page is where the Zeiss incident happened, and 5d is the only limb
that watches an outcome arrive on a page that did not start the turn.

- **Limb 5a — the same confirmation returns.** The banner reappears; copy the ID
   again and verify the two IDs are identical.
- **Limb 5b — the reloaded page keeps polling.** The Network panel must show a
   `GET /api/confirm` about once a second, not a single request after boot. Round
   1 failed this and round 2 passed it (20 polls at 1.02 s); it is the standing
   regression check for `691700f`, and it is scoreable from the HAR alone.
- **Limb 5c — the reloaded page shows the turn.** This is what round 2 found
   broken. The spinner and status row must be visible, the countdown must keep
   decrementing, the Stop button must be present, and the composer must be
   disabled while the turn runs. A live banner above an *empty* status row is a
   FAIL: the poll was already running in round 2 and rendered its countdown into
   a hidden element.
- **Limb 5d — the outcome reaches the reloaded page.** Do not decline and do not
   reload again. Let this confirmation time out. The status must become exactly
   `Confirmation timed out and was declined`, the agent's following reply must
   then appear in the transcript **without a manual reload**, and the composer
   must be released. Any of the three missing is a FAIL.

## 3. Save and compute

Capture the console's `turn settled` line by either route in step 0. If you
saved it as a file, check it before scoring; if you screenshotted it instead,
skip this check, drop `--browser-log` from the command below, and send the
screenshot — the coordinator scores limb 7 from it by hand and says so.

```powershell
$BrowserLog = "block69a-evidence\browser-console.log"
if (-not (Test-Path $BrowserLog)) { throw "No console capture - limb 7 cannot be scored" }
if (-not (Select-String -Path $BrowserLog -Pattern 'last applied seq' -Quiet)) { throw "Console capture has no 'last applied seq' line" }
"CONSOLE OK"
```

Then stop the server. Kill the **tree**, because `$Server.Id` is `uv` and
microclaw runs in a child of it:

```powershell
taskkill /PID $Server.Id /T /F
Start-Sleep -Seconds 2
if (Get-Process -Id $Server.Id -ErrorAction SilentlyContinue) { throw "STOP - the uv process is still running" }
"SERVER TREE STOPPED"
```

If the evidence folder still refuses to zip afterwards, close that first
PowerShell window before zipping — that is what worked in round 3 — and say so
in the report, because it means something in the tree outlived `taskkill /T`.

Then run this command unedited from the checkout:

```powershell
uv run python design/69a-gate.py --server-log block69a-evidence\server-console.log --browser-log block69a-evidence\browser-console.log --forbidden PAYLOAD_69A_GATE_SECRET --log block69a-evidence\computed-score.log
if ($LASTEXITCODE -ne 0) { throw "Block 69a computed gate failed" }
```

The script reports limbs 6–8 independently and exits nonzero for either `FAIL`
or `NOT EXERCISED`. Limb 6 checks each server turn's sequence arithmetic and
summary count. Limb 7 requires both logs and compares the browser's last applied
sequence with the matching server turn; it needs a `turn settled` line and does
**not** require a `stream silence` line, which this machine cannot produce.
Limb 8 rejects per-delta event lines and the recognizable payload marker, and
reports NOT EXERCISED — never PASS — if the marker never entered the session at
all. The scorer owns `computed-score.log`; a shell transcript is not its
evidence.

Return `server-console.log`, `browser-console.log`, `network.har`,
`computed-score.log`, both confirmation IDs, and a verdict for each of the eight
human limbs — 1, 2a, 3, 4, 5a, 5b, 5c, 5d — not one verdict for the session.
`uv run` prints its ordinary `Building microclaw @ file:///...` progress to
stderr here; with no pipeline in this command that is display only, not a
failure.
