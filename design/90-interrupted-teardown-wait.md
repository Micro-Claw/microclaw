# An interrupted teardown wait must not release the next acquisition

**Status: CLOSED with PR #61.** One block, 90a, LOCAL gate plus CI. Closes
`R155` and `R158` (design/70).

## The problem

**R155.** On CPython 3.12 a `Thread.join(timeout)` interrupted by
`KeyboardInterrupt` releases the live thread's state lock and marks it stopped,
so `is_alive()` returns `False` while the thread still runs (bpo-45274's
cleanup). design/88 88a found this in `run_mda` and fixed it there by waiting on
a completion `Event` (`_wait_mda_completion`). `_acquire_with_hooks`, the
pycro-manager path, still joins:

- `tools.py` `_join_acquisition_waiter` is `waiter.join(timeout_s)`, called
  inside `while waiter.is_alive()`, and the expiry check reads
  `waiter.is_alive()` again after it.
- `execute_tool`'s pending-acquisition refusal reads
  `pending["waiter"].is_alive()`.

Reading the code on `0ffa44a` found the defect is wider than the row says. A
`KeyboardInterrupt` raised inside that wait leaves `_acquire_with_hooks`
through its `except Exception` untouched, so **no pending record is written at
all**. The refusal has nothing to read, and the next acquisition starts while
the daemon teardown waiter is still inside `Acquisition.__exit__`. The waiter
also does not inherit reservation closure, which only the expiry path grants.

**Who can reach it.** The terminal REPL does not catch `KeyboardInterrupt`
around `run_agent`, so today a Ctrl-C in that wait ends the process. What
reaches the refusal is any caller of `execute_tool` that survives the
interrupt, such as a notebook kernel. The contract the refusal states is
caller-independent, so it is fixed at the tool.

**The other `is_alive()` sites.** The hazard needs an interruptible join *on
the main thread* (CPython delivers `KeyboardInterrupt` only there) followed by a
later `is_alive()` read of the same `Thread`:

| Site | Thread | Reachable |
|---|---|---|
| `controller._bridge_call` | caller (main in the terminal) | No. The interrupt leaves before `is_alive()` and the `Thread` is local. |
| `AcquisitionDiagnosticWriter.close` (`conversation.py`) | main, from `__main__`'s `finally` | No, same shape. |
| `skill_supervisor` pipe-thread joins | dispatcher or reaper threads; `close()` joins dispatchers but reads no `is_alive()` | No. |
| `skill_store._start_discovery_recheck` | reads a thread nothing in the product joins | No. |

None is fixed. That a Ctrl-C cannot reach them is read from the code, not
measured.

**R158.** `tests/test_skill_supervisor.py`
`test_84a_fixture_live_pause_final_drain_and_eof` ends with
`assert displayed(output, 3)`, one read with no evidence. It failed once on
Windows CI (PR #59). Cause not identified.

## Decision

1. **One wait helper, on an `Event`.** `_join_acquisition_waiter` and
   `_wait_mda_completion` fold into one helper that does `done.wait(timeout_s)`.
   `_acquire_with_hooks`'s `finish()` sets a `done` event as the last thing in
   its outermost `finally`. The loop and the expiry check read
   `done.is_set()`, never `is_alive()`.
2. **The pending record carries the event.** Both writers (`_acquire_with_hooks`
   expiry and `run_mda`'s `expired`) store `"done"`. `execute_tool` decides
   `teardown_running` from `not done.is_set()`. No `waiter` key survives
   (no legacy anchoring).
3. **An interrupt leaving the wait hands teardown to the waiter, as expiry
   does.** If a `BaseException` leaves the supervision loop while `done` is
   unset, record the pending entry, grant the waiter reservation closure, and
   re-raise. No abort is requested. `run_mda`'s cancel-on-interrupt is that
   tool's design and is unchanged.
4. **R158:** `wait_until(lambda: displayed(output, 3), describe)`. No deadline
   change.

## Block 90a

Acceptance:

- A test drives a hooked or hookless `run_timelapse` through `execute_tool`,
  delivers a real `_thread.interrupt_main()` while the foreground is inside its
  blocking wait, and asserts that the next acquisition call is refused with
  `teardown_running: true` while the teardown waiter is still blocked. After
  release it is no longer refused for teardown. Watched failing on `0ffa44a`.
- 88a's interrupt regression in `test_mda_supervision.py` still passes on the
  folded helper.
- R158's line uses `wait_until`.
- Full suite green locally and on both CI runners.

## Run ledger

| Block | Branch | Start commit | Status |
|-------|--------|--------------|--------|
| 90a | `block-90a` | `0ffa44a` | PR #61, 2026-10-19; local gate passed, CI green on both runners (run 39276478002); one start turn, no revision |

## What 90a built and measured (2026-10-19)

- **As decided.** One helper, `_wait_acquisition_completion(done, timeout_s)`,
  serves both supervisors. The pending record carries `done`. An interrupt
  leaving `_acquire_with_hooks`'s loop writes the record and grants the waiter
  reservation closure.
- **Watched failing.** `test_real_interrupt_hands_blocked_teardown_and_reservation_to_waiter`
  delivers a real `_thread.interrupt_main()` once the foreground is in its
  wait. On `2e5b7e2`'s `tools.py` the next call was not refused
  (`AssertionError: {'ok': True}`). Fixed: 30 of 30 passes. The test drives the
  composite-child shape (`_reservation` passed in), so only the inherited
  closure can close the reservation.
- **Not addressed.** A waiter already past `finish_owned_cleanup` but not yet
  past `done.set()` does not see a late grant of reservation closure. The
  expiry path has the same window. It was not measured.
- **Evidence.** Full suite locally (macOS, 3.12): 5964 passed, 107 skipped.
  CI: run 39276478002 at `7bc7c21` green on both runners (Ubuntu 5964 passed, 107 skipped; Windows 5956 passed, 115 skipped). n=1.
