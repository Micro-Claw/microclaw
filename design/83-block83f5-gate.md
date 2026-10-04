# 83f-5 — production publisher to installed skill

Use **Windows PowerShell 5.1**, **Firefox**, and the installed slot interpreter
(the `.ps1` resolves it). Internet and `gh auth login` to the operator's account
are required. Micro-Manager need not be open. The coordinator must first publish
the production policy admitting `microclaw-examples`, the matching catalog seed
and pin, and the example repository's `v1.0.0` GitHub release.

## 0. Install the branch and dispatch the reminder

```powershell
cd D:\Code\microclaw
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
git checkout block-83f-5
if ($LASTEXITCODE -ne 0) { throw 'checkout failed' }
git pull
if ($LASTEXITCODE -ne 0) { throw 'pull failed' }
git merge-base --is-ancestor f903fa3 HEAD
if ($LASTEXITCODE -ne 0) { throw 'WRONG TREE - stop' }
.\install.bat
if ($LASTEXITCODE -ne 0) { throw 'install failed' }
gh workflow run policy-reminder.yml -R Micro-Claw/package-catalog --ref main
if ($LASTEXITCODE -ne 0) { throw 'reminder dispatch failed' }
```

`install.bat` puts the branch in the slot; checkout alone does not. If the
installer pauses for bridge/setup, cancel with Ctrl+C (Y); installation already
completed. Close MicroClaw's console and launcher windows before the session.
The reminder's firing branch is covered **only by the unit test**; here the
policy is far from expiry, so the workflow should succeed without opening an issue.

## 1. Publish and prepare

```powershell
.\design\83-block83f5-gate.ps1 -Phase publish
.\design\83-block83f5-gate.ps1 -Phase prepare
```

Expect `RECORDED: publish` then `RECORDED: prepare`. Publish checks `gh` first,
downloads both release assets, opens the production example PR and the unadmitted
publisher control, and waits up to 15 minutes per PR and for rebuild/served data.
The example merges; the control stays open with a publisher refusal comment.
A merged example is scored on rerun, never submitted again; an existing open
gate PR is reused. Rerun the failed phase using the same evidence folder.

Prepare refuses a TEST-ONLY `trust/roots.json` leftover with the exact command
to remove that override. It also checks the slot has both shipped production
roots; a mismatch prints the installation commands. It snapshots the store,
including when no store exists. The evidence pointer is
`%LOCALAPPDATA%\microclaw\83f5-gate-evidence.txt`. Pass `-Out` only when you need
to choose an evidence folder; subsequent phases use the pointer.

## 2. Session

```powershell
.\design\83-block83f5-gate.ps1 -Phase session
```

Launch the **desktop icon** when asked. The gate validates that launch's health
marker, then prints four steps:

1. Paste the printed search message into **MicroClaw's chat box in Firefox**.
   Wait for the reply, then type `DONE` in PowerShell; do not paste the reply back.
2. Open **Community skill packages**. Save the top of the panel, including the
   **Catalog …** line, as `screenshot-catalog.png` in the printed evidence folder.
   Type `DONE`.
3. On `microclaw-examples/session-start`, **Install** → read the confirmation
   box → **Install**. Wait until the card says **Installed**. Save
   `screenshot-installed.png` in the evidence folder, then `DONE`. If a partial
   run already installed it, leave it installed and save its screenshot.
4. Paste the printed request to load and follow
   `microclaw-examples/session-start/open-unfamiliar-system`'s **first step only**
   into the chat box. Wait for the reply, then `DONE`; do not paste the reply back.

Store and serve-history snapshots are taken after every step. `STOP` ends the
phase without inventing evidence. Completed steps are retained on a partial rerun.

## 3. Verify, clean up, send back

```powershell
.\design\83-block83f5-gate.ps1 -Phase verify
.\design\83-block83f5-gate.ps1 -Phase cleanup
```

Expect `RESULT: 0 failed or not exercised limbs / 11; 1 operator-judged`.
Every **FAIL** or **NOT EXERCISED** makes verify exit nonzero. L10 is
**OPERATOR-JUDGED**: the coordinator reads both PNG screenshots. PR/rebuild
intervals are measurements; install click → ready reports **R145** because the
current record has no click/completion timestamps.

Cleanup closes the control PR and deletes only gate-created fork branches.
It **retains the installed example, the store, and the permanent merged release**.
Send the **whole evidence folder**, including `gate.log`, `verify.json`, both
screenshots, PR/comments/run logs, served and client JSON, and all snapshots,
even after failure. Keep the example installed. The throwaway `control.pem`
is gate-only and has no production trust.
