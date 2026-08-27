# Block 58c implementation handoff

## Checklist

- 1–2: `install.bat` is the sole writer of the managed `Microclaw.cmd` and
  `updater-launcher.ps1`; managed `install-shortcut` leaves that CMD byte-alone
  while still materializing the icon and `.lnk`. Unmanaged installs retain the
  old interpreter-specific wrapper. The single managed-layout predicate is the
  presence of `%LOCALAPPDATA%\microclaw\update-state.json`.
- 3–5: the launcher consumes only `active-slot.txt`/`pending-slot.txt`, delegates
  tested activation to Python, removes stale health, creates a fresh nonce and
  passes explicit launcher/root/slot/nonce environment variables.
- 6–8: `serve()` validates the immutable config snapshot, validates launcher
  identity and executing-slot metadata, atomically writes nonce health, then
  calls `build_session`. Thus a later bridge exception leaves health in place;
  the PowerShell child waits/exits without rollback or relaunch after health.
- 9–10: unhealthy pending launches atomically restore the retained slot and
  defer a bounded rollback report to the next healthy launch. `launcher.log` is
  truncated to its last 200 lines after 64 KiB.
- 11: protocol 1 is recorded in every slot. Python refuses activation and
  staging when the candidate requires a newer protocol, naming the one-time
  installer bootstrap.
- 12/12a: legacy `%LOCALAPPDATA%\microclaw\env` alone migrates to `env-a`.
  Clone/public provenance and the immutable commit marker are written. A
  PATH-resolved Microclaw outside the managed root is only reported, including
  its path and the icon move; it is never imported, targeted, or modified.
- 12b–12c: installer/stager targets are constructed only as managed `env-a` or
  the inactive `env-{a,b}`. No target derives from `sys.executable`, PATH,
  `CONDA_PREFIX`, or provenance. Absence of `update-state.json` remains the
  existing silent unmanaged-update opt-out.
- 13–14: migration has no APPDATA input/deletion path and no editable install.
- 15: inactive staging reports `the update could not be built`, caches the
  failed commit for the interval, and publishes no pending selector on failure.
- Gate: added the human runbook plus independent Python/PowerShell computing
  gate, with mandatory `fails_if`, PASS/FAIL/NOT EXERCISED, owned `gate.txt`,
  nonzero incomplete/failure exit, real slot CLIs, APPDATA hashes, installer
  idempotence, process command-line capture, and non-uv tree/import evidence.

## Design decisions

- `update-state.json` is the managed-layout predicate because it is already the
  design's explicit opt-in boundary; using interpreter location would
  misclassify conda and developer installs.
- Launcher health is one ASCII nonce file. Slot/commit/protocol identity stays
  in `microclaw-slot.json`; duplicating it into health would create a second
  authority.
- Pending activation runs with the currently active slot's Python before child
  start. This keeps protocol/metadata/state mutation in unit-testable Python
  while PowerShell owns only process lifecycle and health observation.
- A failed inactive build may leave a partial inactive directory for diagnosis,
  but never changes either selector. The old active environment remains the
  known-good slot.

## Admitted untested

PowerShell cannot execute in this environment. Structurally tested but not
executed here: `Start-Process` environment inheritance; the 100 ms health poll
and 30 s timeout; `HasExited`/`WaitForExit` behavior; forced child termination;
PowerShell 5.1 console/report encoding; and the bounded-log rewrite. The demo
gate is the Windows evidence for these lines. `install.bat` migration/move,
PowerShell JSON writing, `where` detection, two consecutive runs, and CRLF cmd
behavior are likewise structural until that gate.

## Tests

Required command attempted from the worktree root:

`python3 -m pytest -q`

No test count was measurable: it exited 1 immediately with
`No module named pytest`. The only installed alternative,
`/Users/zachcm/miniforge3/bin/python -m pytest`, exits 139 before collection.
Syntax compilation, `git diff --check`, and a direct activation/fresh-nonce/
match/rollback state-machine smoke passed. This handoff does not claim a green
suite or reproduce the 2244/99 baseline.

New defect tests and expected pre-change failures:

- Managed shortcut ownership: pre-change rewrites the installer-owned CMD.
- Managed/unmanaged launcher split: pre-change has no managed predicate.
- Activation/pending consumption, protocol refusal, nonce/stale health,
  other-slot metadata refusal and rollback: pre-change has no state machine or
  corresponding symbols.
- Failed uv staging: pre-change has no inactive staging function and therefore
  cannot preserve/publish the specified state.
- Bridge failure after health: pre-change writes no marker before the raised
  bridge error.
- Installer launcher/migration/protocol/non-uv structural tests: pre-change has
  no scripts, slot layout, migration, or notice.

Order/structure tests do not reach their assertions on the pre-fix tree. To
prove they bite: move `write_launcher_health()` below `build_session()` for the
ordering test; add a Python `write_text` call for `updater-launcher.ps1` for the
sole-writer grep; insert `env-a` into `Microclaw.cmd` for slot independence; or
remove `-ExecutionPolicy Bypass` for execution-policy coverage.

## Design-document finding

The 58c section says “all nine bullets” in the assignment, but its Tests list
contains eight bullets. More importantly, “pure functions over the two text
files and marker” is too narrow for the required protocol and executing-slot
checks: those necessarily consume immutable `microclaw-slot.json`. The code
keeps selector parsing trivial while treating slot metadata as its separate
immutable authority.
