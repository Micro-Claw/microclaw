# 83f-3 — demo gate: find, install, update and remove from the catalog

Use **Windows PowerShell 5.1** and **Firefox**. This machine needs the
**internet** throughout. The catalog and the packages come from GitHub's raw-file
site (`raw.githubusercontent.com`), and the executable package's library
(`iniconfig 2.0.0`) comes from PyPI. This is the first real run of both.
Micro-Manager does not need to be open.

The program creates `%LOCALAPPDATA%\microclaw\skill-packages` with **TEST-ONLY
trust roots** (it refuses if one already exists), and step 3 removes both. The
evidence pointer is `%LOCALAPPDATA%\microclaw\83f3-gate-evidence.txt`.

## 0. Install the branch

```powershell
cd D:\Code\microclaw
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
git checkout block-83f-3
if ($LASTEXITCODE -ne 0) { throw 'checkout failed' }
git pull
if ($LASTEXITCODE -ne 0) { throw 'pull failed' }
git merge-base --is-ancestor 9efc9b5 HEAD
if ($LASTEXITCODE -ne 0) { throw 'WRONG TREE - stop' }
.\install.bat
if ($LASTEXITCODE -ne 0) { throw 'install failed' }
```

Checking out the branch only gives you the gate script. **install.bat is what
puts this branch in the slot.** If the installer pauses for bridge or setup,
cancel with Ctrl+C (Y); the install has already completed. Then close
MicroClaw's console and launcher windows.

## 1. Prepare

Keep **MicroClaw closed**, then run:

```powershell
.\design\83-block83f3-demo-gate.ps1 -Phase prepare
```

Expect `Prepared TEST-ONLY roots only` and `RECORDED: prepare`. Nothing is
downloaded yet: MicroClaw's own startup is the first fetch.

## 2. Session

```powershell
.\design\83-block83f3-demo-gate.ps1 -Phase session
```

It asks you to launch the **desktop icon**. Wait for Firefox, then type `DONE`.
It then prints twelve steps, one at a time. For a chat step, copy the printed
message into MicroClaw unchanged and type `DONE` when the reply has finished.
For a panel step, do what it says and type what it asks for. The panel is
**Community skill packages**; leave it open after step 2.

| Step | Where | What |
|---|---|---|
| 1 | chat | search the catalog |
| 2 | panel | open it; type the `Catalog …` line |
| 3 | panel | executable-fixture: Install → **Cancel**, then Install → **Install**; describe the box |
| 4 | panel | markdown-fixture (fixture-lab): Install → Install |
| 5 | panel | tampered-fixture: Install → Install; type the error |
| 6 | panel | markdown-fixture (**fixture-two**): Install → Install; type the error |
| 7 | panel | incompatible-fixture: type its reason; is there an Install button? |
| 8 | chat | load the skill |
| 9 | panel | **Check now**; type the text on executable-fixture and markdown-fixture |
| 10 | panel | executable-fixture: **Update to 1.1.0** → Install |
| 11 | panel | markdown-fixture (fixture-lab): **Remove** → describe the box → Remove |
| 12 | chat | search again |

Steps 5 and 6 are **supposed to fail**: type the error exactly as shown. The
first install (step 3) builds a Python environment, so it may take a minute or
two. Wait until the row shows it installed. If the agent asks whether to go
ahead, answer `yes, go ahead`. Type `STOP` to abandon, then run step 3 anyway.

## 3. Verify, clean up, send back

Leave MicroClaw **open** and run:

```powershell
.\design\83-block83f3-demo-gate.ps1 -Phase verify
```

It scores ten limbs. Limb 9 points the catalog at a missing address on the real
host to check that the saved copy survives being offline, then puts it back. It
**then** asks you to close MicroClaw, and removes the TEST-ONLY roots and the
store.

Expect `RESULT: 0 failed or not exercised limbs / 10; 1 operator-judged`.
Limb 10 is `OPERATOR-JUDGED`: the coordinator reads what you typed. The
`MEASUREMENT` lines are timings, not pass/fail. **NOT EXERCISED is not a pass.**

Send the whole folder printed after **Evidence:**, even after a failure. It
holds per-step snapshots of the store and of serve's history.
