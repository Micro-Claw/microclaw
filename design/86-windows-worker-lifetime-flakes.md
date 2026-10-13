# Two Windows CI flakes around a package worker's lifetime

**Status: CLOSED with PR #56.** One block, 86a, LOCAL gate plus Windows CI.
Closes `R153` (design/70).

## The problem

Two tests failed once each on `windows-latest` on 2026-10-13, both on PRs that
could not have caused them, and both passed on re-run:

| Run | Test | Failure |
|---|---|---|
| 38416052833 #1 (PR #54) | `test_skill_supervisor.py::test_84a_fixture_live_pause_final_drain_and_eof` | `wait_until(displayed(output, 1))` at `:1750`, full 8 s, `assert False` |
| 38448592441 #1 (PR #55) | `test_acquisition_analysis.py::test_engine_and_hooked_failures_keep_jobs_and_writer_truth[False-acquire]` | `KeyError: 'message'` at `:306` |

Neither failure carries the job record or the worker's stderr, so neither can be
diagnosed from its log.

**Flake 1, likely mechanism (not observed).** The fixture worker's headless
display (`fixture_worker/runner.py` `tick()`, 10 ms) publishes with
`display.tmp.write_text(...)` then `display.replace(output / "display.json")`.
The test reads `display.json` every 10 ms. On Windows, Python opens files
without `FILE_SHARE_DELETE`, so `MoveFileEx` onto a target another process has
open fails with `PermissionError`. Nothing in `tick()` catches it, so the
exception leaves the display loop, `frames_displayed` stops, and the test waits
out its deadline. That needs a read and a replace to overlap, which fits one
failure in five Windows runs. The fixture is also the **reference worker**
`docs/publishing-skill-packages.md` tells publishers to copy, so the pattern
travels.

**Flake 2, no mechanism.** `JobHandle._notify` records `message` only once it
queues the notification. Otherwise the entry is `refused` (validation, with
`field`/`detail`) or `undelivered` (`worker_exited`, `stdin_closed`,
`stdin_failed`). The test reads `item['message']` unconditionally, so it raised
before showing which of those it was.

## Decision

1. **Fixture: a failed publish skips one tick, never the loop.** In the headless
   display, catch `PermissionError` around the `replace` and try again on the
   next tick. Keep the write-then-replace, which is what makes a read never see
   half a file. Say why in one comment, because that comment is what a
   publisher copying the reference worker reads.
2. **Both tests fail with their evidence.** A timeout in these window tests
   fails with the job's `record()` (state, failure, `stderr_tail`) and the
   fixture process's `poll()`. Test 2 fails with the notification entries and
   the record when the `acquisition` entry is not delivered. Reading `message`
   comes after asserting the entry's `state`. No deadline changes, because
   nothing measured says the deadline is the cause.
3. **Flake 2 is not fixed by guessing.** If the job record shows a cause this
   block can attribute locally (for example, the worker legitimately exits
   before the failure is notified), fix it. Otherwise ship the diagnostics and
   leave `R153` open for flake 2 alone, saying so.

## Block 86a

Acceptance:

- A local test drives the headless display through a `replace` that raises
  `PermissionError` on its first call (a fake shaped like Windows: the target
  stays readable and the error is the one Windows raises) and asserts the
  display keeps counting. Watched failing on the pre-fix fixture.
- The fixture's manifest asset hashes and `executable-intake.json` are
  refreshed as `tests/fixtures/skill_packages/README.md` describes, and every
  test that pins them stays green.
- Both tests' failure messages carry the record. Shown by forcing each to fail
  once locally and pasting the message into the report.
- The full suite is green locally and on both CI runners.

Gate: LOCAL plus CI. Windows CI is the only place the real `MoveFileEx`
behaviour runs. One green run proves nothing about a 1-in-5 flake, so the gate
records the run count, and the reconciliation says what n it rests on.

## Run ledger

| Block | Branch | Start commit | Status |
|-------|--------|--------------|--------|
| 86a | `block-86a` | `38db7ed` | PR #56, 2026-10-13; local gate and 3 CI runs passed; one start turn, one coordinator fix |

## What 86a built and measured (2026-10-13)

- **Flake 1, confirmed off-rig.** With a `PermissionError` injected into the
  publish (the error text Windows produces), the pre-fix fixture's `tick()`
  raised out of the display loop. The fixed one skips that tick and reaches
  `frames_displayed == 2`. The real `MoveFileEx` collision was never observed.
  The fake reproduces its documented behaviour.
- **Flake 2, attributed locally.** The fixture's default `observe_dataset`
  succeeds and exits straight away. Holding `acquire()` until the worker has
  exited reproduces `state: undelivered, reason: worker_exited` with no
  `message`. That record is truthful. The test now runs the worker in
  `lifecycle` mode, which waits for the notification, and the undelivered
  case is its own test. No product change.
- **Diagnostics.** `wait_until` takes a `describe` callable. Both tests fail with
  the job's state, failure and stderr tail. The forced failures were checked
  locally.
- **The coordinator's prompt was wrong once.** It said to leave `design/`
  evidence alone, but design/83f6's committed gate fixtures embed the reference
  worker's bytes, and `test_83f6_committed_fixtures_regenerate_identically`
  pins them to the generator. Regenerated with the gate's own `fixtures()`
  (`6d4aa46`). Only the full suite caught it. The runner's targeted run did not.
- **Evidence.** Full suite locally: 5781 passed, 108 skipped after the
  regeneration. CI run 38465194033: three consecutive attempts, green on both
  runners. **n=3** against 2 Windows failures in the 5 runs before. That is
  consistent with a fix, not proof of one at a 1-in-5 rate.
