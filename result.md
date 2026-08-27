# Block 58e implementation report

## Commits

- `7e584af` — restart lifecycle, nonce-bound request state, activation/rollback
  reconciliation, valid-pending sharing, shutdown handle, exit-pause behavior,
  REPL cached notice/prompt/staging, launcher loop, and unit/structural tests.
- `897d1db` — the three-file 58e Windows demo gate, pinned to `7e584af` with
  `git merge-base --is-ancestor`.

## Verification

The requested full pytest tail is unavailable in this runner. Both
`python -m pytest -q ...` and the no-plugin/no-bytecode form exited `139` before
printing any pytest output; the configured interpreter is
`/Users/zachcm/miniforge3/bin/python` 3.10.12. `uv` is not installed, and the
system Python has no pytest. Therefore I cannot honestly report a delta from
main's 2296 passed / 99 skipped. Static parsing/compilation succeeded for 84
Python files, `git diff --check` passed, and two dependency-free smoke probes
printed:

```text
restart-state smoke passed
pause smoke passed
```

This also prevented the required pre-change/mutation pytest runs. No pre-change
failure claim is made for any new test: the coordinator must run those tests on
a working test interpreter before accepting the block. The new tests are the
three handler-time pause cases, absent/mismatched/matched restart request,
stale-request cleanup, activation/rollback/unknown-marker reconciliation, route
shutdown ordering/no-handle refusal, and launcher structural delegation.

## Reachability and guards

- The no-server-handle guard is reached by a real `FastAPI` app built with the
  existing `session` fixture and deliberately no `app.state.uvicorn_server`.
- Pending validity is reached with real `active-slot.txt`, `pending-slot.txt`,
  and slot-marker fixtures in `tests/test_updates.py`; the web route tests mock
  that already-unit-tested boundary to isolate lifecycle ordering.
- Restart nonce mismatch/absence/staleness use real files under `tmp_path`.
- Reconciliation uses real state, selectors, and both real slot marker paths;
  the `unknown` fixture reaches the preservation guard.
- The no-restart pause path is reached with the shortcut environment plus a
  captured real atexit callback; environment mutation occurs after registration.

The existing endpoint identity test was extended through the same update route
suite; it retains both `session.history` and durable-store assertions because
the latter alone cannot observe direct list mutation.

## Launcher boundary

Added PowerShell lines 21 and 79 wrap the existing launch in a loop. Line 75
delegates request consumption and nonce matching to
`updates.consume_restart_request`; line 77 loops only on its true result. The
existing lines 27, 41, 57, 63, and 68 continue delegating activation, fresh
nonce/marker handling, health waiting, rollback, and rollback-report
consumption. PowerShell process spawning, environment propagation,
`WaitForExit`, exit-code preservation, and the loop's real Windows behavior
remain untestable on macOS/Linux and are explicitly demo-gated.

## Checklist notes

Item 7 required no parser or opt-out implementation change. The top-level
`--no-update-check` already appears between `--safety-config` and subparser
creation, `checks_enabled` honors `MICROCLAW_UPDATE_CHECK=0`, and both entry
points now call the same `start_due_check` helper with that value.

The writer grep was:

```text
rg -n 'installed_commit.*=|\["installed_commit"\]|\['"'"'installed_commit'"'"'\]' microclaw install.bat scripts tests
microclaw/updates.py:140:        state["installed_commit"] = commit.lower()
```

The remaining matches are constructor parameters and test assertions; there is
no other post-install writer. I found no disagreement between the assignment
prompt and the 58e checklist. The only incomplete acceptance evidence is the
pytest/pre-change execution described above; it must not be silently treated as
green.
