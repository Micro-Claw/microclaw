# Block 58d — demo machine gate

Cached update status, the banner, and the idle-aware routes. Everything that
only computes is in `58-block58d-demo-gate.py`; this runbook is the four things
a person has to do and judge.

**Expect `INCOMPLETE`, and read the limb list rather than the banner.** One limb
— the **Restart now** *button* — cannot run in this block: the button appears
only after a slot is staged, and 58d deliberately stages nothing on the rig.
It reports `NOT EXERCISED`, which is never a pass, and it belongs to 58e's
end-to-end gate. A `FAIL` on any other limb is a real failure.

Send back the whole evidence folder. Its path is printed after every phase.

## Before you start

You need Micro-Manager running with the ZMQ server on port 4827, as usual, and
GitHub Desktop able to fetch `origin`.

**Check out this branch and install from it**, so the slot the desktop icon
launches is the code under test:

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
git fetch origin
git checkout design58/endpoints
git pull
.\install.bat
```

`install.bat` rebuilds the managed slot and takes a few minutes. If it stops with
a question, answer it as you normally would; existing security bounds are
preserved.

## What this gate changes, and what puts it back

This gate edits **one field** of real production state: `installed_commit` in
`%LOCALAPPDATA%\microclaw\update-state.json`. It has to, because this branch is
not merged yet — a real install from an unmerged branch records a commit that is
not an ancestor of `origin/main`, discovery correctly reports "diverged", and
there is no candidate to show. `Prepare` sets that field to `origin/main~1`, a
real earlier commit, so discovery has something genuine to find. **The slot's
code is not touched.**

`Prepare` copies `%APPDATA%\microclaw` and the original `update-state.json` into
the evidence folder before changing anything. `Restore` puts the field back and
`Verify` scores whether it did. Run `Restore` even if an earlier phase failed.

## Step 1 — Prepare (no Microclaw running)

Close any running Microclaw first.

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
.\design\58-block58d-demo-gate.ps1 -Mode Prepare
```

It prints the commit it expects the banner to name, like
`Prepared. origin/main is 4ab91cd — Improve setup flow.` **Write that down**;
step 2 asks you to compare it against the screen.

## Step 2 — Launch from the desktop icon and look at the banner

Double-click the **Microclaw** desktop icon. Do not start it from a terminal —
the desktop launcher is the path under test.

When the browser opens:

1. **Judge the banner.** A banner should be at the top of the page reading
   *"A newer Microclaw commit is available: `<short sha>` — `<subject>`"* with
   **Update**, **Later** and **View on GitHub**. The short SHA and subject must
   match what `Prepare` printed. Take a screenshot into the evidence folder.
2. Press **F12** to open devtools, go to the **Network** tab, and tick
   **Preserve log**. Leave it open for the rest of the gate.
3. Do **not** click Update. Nothing in this gate stages a build.

If no banner appears, stop and send the evidence folder plus a screenshot — that
is a real failure and the rest of the gate depends on it.

## Step 3 — Probe (leave the server running)

In a **second** PowerShell window:

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
.\design\58-block58d-demo-gate.ps1 -Mode Probe
```

This reads the cached status five times, checks that nothing went to the
network, runs "Check now" four times to prove both that it re-checks and that
the fourth is refused, and captures the idle restart response.

## Step 4 — Start a long turn, then run Busy while it is still running

In the Microclaw browser tab, paste this prompt and press Send:

```
Describe every device Micro-Manager currently reports, one line each, and then
summarise which of them are cameras, which are stages, and which are shutters.
Do not change any setting.
```

**While that turn is still running** — the Send button is disabled and tool
cards are still appearing — go back to the second PowerShell window and run:

```powershell
.\design\58-block58d-demo-gate.ps1 -Mode Busy
```

It prints the refusal it captured. If it prints anything other than `409`, the
turn had already finished: wait for it to end, send the prompt again, and rerun
`-Mode Busy` sooner.

## Step 5 — Click Later, then stop and relaunch

1. Wait for the turn to finish.
2. Click **Later** on the update banner. It should disappear.
3. Save the network log: in devtools, on the **Network** tab, use
   **Export HAR** (the download arrow, or right-click a request → *Save all as
   HAR with content*) and save it as **`network.har`** in the evidence folder
   whose path the script printed.
4. Close the Microclaw console window to stop the server.
5. Double-click the **Microclaw** desktop icon again.
6. **Judge it:** the update banner must *not* come back.

Then, in the second PowerShell window:

```powershell
.\design\58-block58d-demo-gate.ps1 -Mode After
```

## Step 6 — Restore and Verify

```powershell
.\design\58-block58d-demo-gate.ps1 -Mode Restore
.\design\58-block58d-demo-gate.ps1 -Mode Verify
```

`Verify` prints one line per limb — `PASS`, `FAIL` or `NOT EXERCISED` — each with
the condition that would have failed it, then the overall banner. It scores every
limb independently: one refusal never hides the others.

If a phase was never run, `Verify` says so once at the top and names the exact
command still owed, instead of surfacing it one failing limb at a time.

## What comes back

The whole evidence folder: `gate.txt`, `results.json`, `phases.json`,
`prepare.json`, `probe.json`, `busy.json`, `after.json`, `restore.json`,
`network.har`, your banner screenshot, and the `appdata-backup` copy.
