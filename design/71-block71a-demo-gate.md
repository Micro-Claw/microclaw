# Block 71a — demo-machine gate runbook

Everything that computes is in `71-block71a-demo-gate.py`. You run four commands
and read one log.

## 0. Put this branch in the slot

**This is the step the first run of this gate missed**, on 2026-09-21: checking
the branch out gives you the gate script and nothing else, so the gate imported
the slot's older `microclaw` and all eleven limbs reported NOT EXERCISED. The
slot runs what `install.bat` put there.

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
git fetch origin
git checkout block-71a
git pull
.\install.bat
```

`install.bat` takes a few minutes and preserves your existing security bounds.
Answer any question as you normally would.

Confirm the implementation is really in what you checked out:

```powershell
git merge-base --is-ancestor 0241693 HEAD; if ($LASTEXITCODE -eq 0) { "IMPLEMENTATION PRESENT" } else { "WRONG TREE - stop" }
```

Expect `IMPLEMENTATION PRESENT`.

## 1. Run the gate

```powershell
.\design\71-block71a-demo-gate.ps1
```

It refuses in one line if step 0 did not take, prints the commit under test, and
warns you if `h5py` is already installed — in which case remove it with the
command it prints and run the gate again, because the absent-extension limbs
cannot be exercised otherwise. The gate reinstalls `h5py` itself, so removing it
is safe.

This needs **network**: it performs a real `uv pip install` of `h5py`.

## 2. Send back

The whole evidence folder (the path is printed at the end, under `Documents`).
It holds `gate.log`, `under-test.json`, `uv-invocations.json`,
`installed-constraints.txt`, `observations.json` and `records\extensions.json`.

## What passing looks like

Eleven limbs, each reported on its own line, and `RESULT: 0 failed or not
exercised limbs`. Any `FAIL` or `NOT EXERCISED` line is a result — send the
folder either way, and do not re-run to get a cleaner log.

The limb that matters most is **`no restart adapter`**: it proves `h5py` imports
inside the server process that was already running when you pressed Install. The
whole feature is that no restart is needed.

## What this changes on the machine, and what puts it back

- **The slot is rebuilt from an unmerged branch.** Its recorded
  `installed_commit` is not an ancestor of `origin/main`, so the update banner
  may report a diverged or absent candidate until you reinstall from `main`.
  That is expected and is not a gate finding.
- **`h5py` is installed into the active slot** and stays there. Nothing else
  needs it; leave it.
- **`%APPDATA%\microclaw\extensions.json` is created**, recording `ilastik`.
  It is the file the feature exists to write.

To go back afterwards:

```powershell
cd $env:USERPROFILE\Documents\GitHub\microclaw
git checkout main
git pull
.\install.bat
```
