# 83d — demo-machine gate

Use **PowerShell**, the real **Microclaw desktop icon**, and **Firefox**. Keep
Micro-Manager open. The program scores the store's state files and the panel's
JSON; you judge what the panel shows. It needs network (a CPython download, one
MicroClaw build). About 40 minutes, five Microclaw launches.

What it does to this machine, all undone by step 6: it creates
`%LOCALAPPDATA%\microclaw\skill-packages` (refusing if one already exists), puts
**TEST-ONLY trust roots** there — while they exist, releases signed with public
test keys can run — and builds a "2.0.0" MicroClaw into the inactive slot. Step 6
removes the store and the roots; the next real update rebuilds the inactive slot.

## 0. Install the branch

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
git checkout block-83d
if ($LASTEXITCODE -ne 0) { throw 'checkout failed' }
git pull
if ($LASTEXITCODE -ne 0) { throw 'pull failed' }
git merge-base --is-ancestor 5477da8 HEAD
if ($LASTEXITCODE -ne 0) { throw 'WRONG TREE - stop' }
.\install.bat
if ($LASTEXITCODE -ne 0) { throw 'install failed' }
```

Checkout alone supplies only the gate script: **install.bat puts this branch in
the slot**. If the installer pauses for bridge/setup, cancel with Ctrl+C (Y); the
install has already completed. Close Microclaw's console and launcher windows.

## 1. Prepare — installs the fixtures in this window

```powershell
.\design\83-block83d-demo-gate.ps1 -Phase prepare
```

Expect `installed …` for four releases (the `1.2.0-broken` one ending
`'ok': False, 'field': 'self_check'` — deliberately) and `RECORDED: prepare`.

## 2. Desktop

```powershell
.\design\83-block83d-demo-gate.ps1 -Phase desktop
```

When told, launch the **desktop icon**, expand **Community skill packages** in
Firefox, type `DONE`. Type the first line under that heading exactly. The program
then asks the running serve to roll back `executable-fixture` (a real worker),
waits for it, and prints `RECORDED: desktop`.

## 3. Update to a 2.0.0 build

```powershell
.\design\83-block83d-demo-gate.ps1 -Phase updated
```

Close Microclaw when told. The program builds the other slot (a few minutes),
then asks you to launch the icon. Both packages must still be **listed** and read
**disabled**. Type what the `executable-fixture … (active)` row says after
`disabled:`.

## 4. Roll MicroClaw back

```powershell
.\design\83-block83d-demo-gate.ps1 -Phase rolledback
```

Close, then launch the icon when told. Nothing to type beyond `DONE`.

## 5. Reinstall, recover, repair, remove

```powershell
.\design\83-block83d-demo-gate.ps1 -Phase reinstalled
```

Close Microclaw when told. The program plants leftover files and hides one
environment's `python.exe`. When it asks, run in a **second PowerShell**:

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
.\install.bat
```

Cancel a bridge/setup pause with Ctrl+C (Y). **Do not launch Microclaw from the
installer**; type `DONE` in the gate, then launch the icon when told. The program
asks serve to repair the broken package and removes the Markdown one.

## 6. Verify, clean up, send back

```powershell
.\design\83-block83d-demo-gate.ps1 -Phase verify
```

Close Microclaw when told. Expect **14 PASS lines** and
`RESULT: 0 failed or not exercised limbs / 14`. The last limb removes the store
and the TEST-ONLY roots and **fails if either survives**. Send the whole printed
evidence folder, even on failure. **NOT EXERCISED is not a pass.** If a phase
fails, stop, run `verify` anyway (it still cleans up), and send the folder.
