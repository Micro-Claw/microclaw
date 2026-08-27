# Block 58e — demo machine gate

This gate can brick the managed install. Run each command unedited in Windows
PowerShell 5.1 from the `design58/restart` checkout. The first literal command
backs up both state roots and prints the copy:

```powershell
.\design\58-block58e-demo-gate.ps1 -Mode Prepare
```

`Prepare` backs up `%APPDATA%\microclaw` and `%LOCALAPPDATA%\microclaw`, records
the active branch slot, then replaces only `installed_commit` with the real
`origin/main~1`. The branch is unmerged and would otherwise correctly discover
`diverged`. The update built below is real `origin/main`, which does not contain
58e; the code under test is the branch slot requesting restart plus the external
launcher performing it. Expect to end on `env-b` during that sequence.

The phase order is mandatory because `env-a` contains the branch build and
staging always overwrites the inactive slot. `Verify` checks the phase ledger;
do not stage while `env-a` is inactive.

1. With `env-a` active, run it directly, stage the candidate, and confirm the UI
   offers **Restart later only**. Record `"human_later_only": true` in
   `direct.json`, then run `-Mode Direct`.
2. Relaunch from the desktop icon. Stage a real update while the active files
   remain locked. While it is genuinely building, make a second Stage request
   and record its status as `second_stage_status`; observe **Building the
   update…**, the ready restart banner, and a deliberately incompatible config's
   not-ready banner. Record the three booleans and the real two-slot comparison
   in `stage.json`, then run `-Mode Stage`.
3. Click **Restart now** from the desktop-owned server, time that it does not
   stop at `Press Enter to close this window...`, and prove the running
   interpreter's adjacent `microclaw-slot.json` is the staged commit. Record
   `"human_restart_now": true` in `restart.json`, then run `-Mode Restart`.
4. Ctrl-C an ordinary desktop launch and confirm the Enter pause still appears.
5. Exercise locked-active staging, a deliberate install failure, a deliberate
   failed start, and offline launch. For the unreachable-PyPI case run
   `-Mode Failure`; its `UV_INDEX_URL` exists in that PowerShell process only —
   it never uses `setx` or writes a user/machine variable. Confirm the active
   slot stays known-good and no retry occurs inside the interval.
6. With Micro-Manager closed, activate an update once. Confirm health is written,
   the new slot is kept, and there is no rollback or relaunch; run `-Mode Closed`.
7. Run `-Mode Restore`, which restores `installed_commit` and the active slot
   and removes pending state. It also permits before/after hashes of
   `%APPDATA%\microclaw` to prove config, key, and histories survive.
8. Last, install a real public ZIP. Confirm its provenance is `public-head` and
   the terminal says automatic updates become available when the repository is
   public without prompting. Record
   `"human_public_line_and_reinstall": true`, run `-Mode PublicZip`, then
   reinstall from the clone with `install.bat`. Public ZIP is last because it
   replaces the managed install.
9. Run `-Mode Verify`. Missing evidence is NOT EXERCISED, every limb names its
   failure condition, the program writes `gate.txt` itself, and any non-pass
   exits nonzero. Send the complete evidence folder.
