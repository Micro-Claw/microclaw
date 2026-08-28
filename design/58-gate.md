# design/58 — the rig gate: the limbs that need a person at the machine

The last limb of design/58 with no rig evidence. It needs physical access: over
Remote Desktop, disconnecting the network ends the session that would observe
the result. Everything computable is in `58-gate.py`; what is left here
is what a person has to do and judge.

Scoring script: `python design\58-gate.py {arm|offline|restored|staged|activated}`.
It reads the managed install's own files, imports nothing from `microclaw`,
scores each limb independently, writes `%TEMP%\mc-offline-gate\gate.log`, and
exits nonzero if any limb failed. `--selftest` proves the scoring discriminates.

## What is under test

1. **A managed install launches and works with no network.** The only limb
   design/58 ever booked for a visit. Scored on `launch-health.txt` carrying
   *this* start's nonce, never on a new `launcher.log` line: the launcher
   writes that line ten statements before `Start-Process`, so it proves the
   launcher ran and not that the application did.
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

    python design\58-gate.py arm

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

    python design\58-gate.py offline

**6.** Reconnect the network. Quit, relaunch, click **Check for updates** again.

    python design\58-gate.py restored

## What to send back

- The whole `%TEMP%\mc-offline-gate\` folder (`gate.log` plus the three snapshots).
- Two sentences: whether the app was usable offline, and what step 5 displayed.

A limb the machine could not exercise is reported as NOT EXERCISED and is never
scored as a pass; a passing gate is a place to look for defects, not a reason to
stop looking.


---

# Restart later — the last owed limb

`Restart later` stages an update and declines the restart, so the **next
ordinary desktop launch** must activate it. The mechanism is evidenced many
times over by launcher-driven restarts; what has never been collected is the
*button* path — a person pressing it and a manual launch consuming the selector.

It nearly closed itself on 2026-08-28 and could not, because `install.bat` now
retracts a pending slot (defect 8). **Do not run the installer between the two
phases.**

## Steps

**1.** Launch Microclaw. Click **Check for updates**, then **Update**, and wait
for *"The update is ready to restart."*

**2.** Click **Restart later**. The banner goes away; the staged slot stays.

    python design\58-gate.py staged

Expect `PASS: staged into slot <x>`. If it reports NOT EXERCISED, nothing was
staged — go back to step 1.

**3.** Quit Microclaw completely, including the window it runs in.

**4.** Launch from the desktop icon. This is the limb: an *ordinary* launch, not
a launcher-driven restart. **Do not run `install.bat`.**

    python design\58-gate.py activated

Six limbs, scored independently: the selector existed and was distinct, the
application actually started (health marker against this start's nonce, never a
`launcher.log` line), the staged slot was activated, the selector was consumed,
`installed_commit` was reconciled from the activated slot's marker, and the
previous slot survives as a rollback target.

## What to send back

`%TEMP%\mc-offline-gate\gate.log` and the two snapshots, plus one sentence on
whether the app came up normally after the manual launch.
