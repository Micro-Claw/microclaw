# design/58 — the offline limb, on M5

The last limb of design/58 with no rig evidence. It needs physical access: over
Remote Desktop, disconnecting the network ends the session that would observe
the result. Everything computable is in `58-offline-gate.py`; what is left here
is what a person has to do and judge.

Scoring script: `python design\58-offline-gate.py {arm|offline|restored}`.
It reads the managed install's own files, imports nothing from `microclaw`,
scores each limb independently, writes `%TEMP%\mc-offline-gate\gate.log`, and
exits nonzero if any limb failed. `--selftest` proves the scoring discriminates.

## What is under test

1. **A managed install launches and works with no network.** The only limb
   design/58 ever booked for a visit.
2. **`Check for updates` completes offline rather than hanging.** It runs a real
   `git fetch`, capped at 15 s.
3. **An offline offer carries its warning.** Offline, `discover_clone`'s fetch
   fails but the stale `refs/remotes/origin/main` still resolves, so it may
   legitimately still offer a commit. That offer may be superseded, and the only
   thing that says so is `Candidate.warning` — which nothing rendered until
   `9db7726`. This limb is why the phase is armed with a candidate first: with
   nothing to offer, the warning path never runs and the limb scores NOT
   EXERCISED, which is not a pass.

## Steps

**0.** `git pull` in the clone. Do **not** rerun `install.bat` — the installed
commit must stay behind main for the offer to exist.

**1.** Launch Microclaw. Click **Check for updates**. It should now offer the
newest commit. Then:

    python design\58-offline-gate.py arm

Expect `PASS: armed with candidate <sha7>`. If it says NOT EXERCISED, the check
found nothing and the offline phase cannot test limb 3 — stop and say so.

**2.** Quit Microclaw completely, including the window it runs in.

**3.** **Disconnect the network physically** — unplug the ethernet cable, or turn
Wi-Fi off. Not a VPN toggle; the machine must have no route.

**4.** Launch Microclaw from the desktop icon. **Watch and judge:** does it
start, does the page load, is a session usable? This is limb 1 and no script can
score it.

**5.** Click **Check for updates**. Note what appears — a toast, or a banner —
and roughly how long it took.

    python design\58-offline-gate.py offline

**6.** Reconnect the network. Quit, relaunch, click **Check for updates** again.

    python design\58-offline-gate.py restored

## What to send back

- The whole `%TEMP%\mc-offline-gate\` folder (`gate.log` plus the three snapshots).
- Two sentences: whether the app was usable offline, and what step 5 displayed.

A limb the machine could not exercise is reported as NOT EXERCISED and is never
scored as a pass; a passing gate is a place to look for defects, not a reason to
stop looking.
