# Block 75b implementation handoff

Implemented on `design75/supervised-runtime-bound`, starting at `75aa4e6`, in the assigned worktree. Implementation commit: `5d1ff98`. No merge, push, other worktree, or change to `main`.

## Diff, function by function

- **Module constants / `AcquisitionSupervisionPolicy`:** frozen policy with named quiet floor, runtime slack, reason, terminal-frame promise, and explicit failure-bound name. `DEFAULT` retains 900/300 seconds; `SHORT_FIXED` uses the measured 5/5 seconds, with M2's n=20, 446 ms maximum and 11.2x headroom documented beside the constants. The camera enrichment grace is 2 seconds.
- **`_runtime_ceiling_s`:** requires policy; preserves the three-part `(bound, fallback, term)` return, scaling and fallback. The slack term describes its actual value (`plan_plus_300_s` for default, `plan_plus_5_s` for short).
- **`_stall_quiet_s`:** requires policy; preserves the two-part `(window, term)` return and `5 × largest observed gap` widening.
- **`AcquisitionUnterminated`:** carries phase and diagnostic persistence acknowledgement.
- **`_emit_acquisition_diagnostic`:** an acknowledgement request with no writer now reports false and sends the existing fallback. Previously the no-writer path returned true, which could not truthfully mean persisted. The writer implementation is untouched.
- **`_camera_sequence_running`:** uses the existing `move_controller._bridge_call` with the explicit 2-second timeout. Exceptions still mean unknown. This also bounds the existing acquisition-refusal caller.
- **`_unterminated_result`:** runtime expiry uses the requested neutral error; reports accounted/planned frames including zero, dataset path and possible unterminated storage, phase and persistence acknowledgement. Camera/teardown recovery branches and engine exception reporting remain. Error-grace wording remains unchanged.
- **`_acquire_with_hooks`:** policy is required and keyword-only. Callback reporting derives acquiring/finalizing solely from the policy's terminal-frame promise, forces phase transitions through the existing progress cadence, and adds the construction record's runtime bound fields. Expiry remains runtime AND quiet, independently of callbacks. The pending flag and typed exception are constructed before camera enrichment. Timeout diagnostics carry phase and request acknowledgement before the typed failure is raised. Waiter cleanup ownership remains in place.
- **`run_zstack`, `_acquire_positions_with_hook`, `_acquire_survey_with_detector`, `run_adaptive_survey`:** each explicitly supplies `DEFAULT` at its supervisor call.
- **`run_timelapse`:** after constructing events/hooks, only a standalone one-element list with no adapter and no shared reservation selects `SHORT_FIXED`. The code explains why composite children retain `DEFAULT`. Adaptive result disclosure explicitly passes `DEFAULT` to its separate runtime-ceiling calculation.
- **`serve.html` acquisition-progress case:** appends acquiring or finalizing-dataset text; absent phase retains the frame-count rendering. No completion phase/event or confirmation.
- **Tests:** existing direct supervisor/helper calls adopt explicit policies. The bounded-wait module's existing fake and simulated clock are extended; its new optional simulated limit releases the worker before failing a broken delivery bound. Browser cases execute the actual event-handler branch in Node. No new test module.

## Verification and baseline reconciliation

Required exact command, from the worktree root:

```text
.venv/bin/python -m pytest -q
5 failed, 2857 passed, 99 skipped, 3 warnings in 168.98s (0:02:48)
```

One failure was introduced test text I/O missing explicit UTF-8 encoding. All three reported reads were fixed. The other four are unresolved environment constraints, not counted as passes:

1. `tests/test_bridge_check.py::test_tcp_listener_without_zmq_handshake_is_not_ready`: `PermissionError: [Errno 1] Operation not permitted` at loopback socket bind.
2. `tests/test_webserve.py::test_browser_opens_only_once_the_port_accepts`: the same socket-bind denial.
3. `tests/test_webserve.py::test_browser_opener_gives_up_instead_of_hanging`: the same socket-bind denial.
4. `tests/test_skills.py::test_built_wheel_contains_the_source_tree_skill_catalog`: its isolated `python -m pip wheel . --no-deps --wheel-dir ...` exits 1 (`CalledProcessError`). The test captures but does not expose the subprocess's stderr in this traceback. This existing test builds in isolation with `setuptools>=68`; unlike the top-level pytest command, it is not inherently dependency-resolution-free. I did not retry it, install anything, or request network access.

After the encoding fix, the suite was run with exactly those four tests deselected:

```text
.venv/bin/python -m pytest -q \
  --deselect tests/test_bridge_check.py::test_tcp_listener_without_zmq_handshake_is_not_ready \
  --deselect tests/test_skills.py::test_built_wheel_contains_the_source_tree_skill_catalog \
  --deselect tests/test_webserve.py::test_browser_opens_only_once_the_port_accepts \
  --deselect tests/test_webserve.py::test_browser_opener_gives_up_instead_of_hanging
2858 passed, 99 skipped, 4 deselected, 3 warnings in 154.34s (0:02:34)
```

There are **25 additional collected cases**: 22 in `tests/test_bounded_acquisition_wait.py` (10 new test functions), and 3 in `tests/test_recovery_js.py` (one parametrized function). Baseline 2837 + 25 = 2862 expected passes; 2858 verified passes + 4 environment-blocked cases accounts for all 2862. The 99 skips are unchanged. The known warnings are not findings. No rig validation is claimed.

## Mutation and pre-fix evidence

Thirty isolated production-property mutations were run against the fixed interface, each restored immediately. Every mutation returned pytest exit 1 for an assertion failure; none relied on an ImportError or timed out. Mutations and logs stayed in `/tmp`, outside the commits. Below, test names omit the `test_` prefix. Parametrized cases are identified explicitly.

| New test / cases | Mutation and observed failure |
|---|---|
| `short_fixed_timeout_survives_missing_notification[False, True]` | Replaced policy slack with default slack; separately replaced policy quiet floor with default floor. Each run: **2 failed**, ceiling assertion `11.0 <= (10.05 + 0.1)` (zero case). The test's independent simulated limit prevents a hang. |
| Same, `[False]` | Added `frames_accounted > 0` to runtime expiry: **1 failed**, delivery-ceiling assertion. Replaced unknown timeout phase with finalizing: **1 failed**, `'finalizing' == 'acquiring_or_notifying'`. |
| Same, `[True]` | Made timeout diagnostic report acquiring-or-notifying: **1 failed**, `'acquiring_or_notifying' == 'finalizing'`. |
| Same, both cases, no-confirmation property | Inserted `CONFIRM_FN("supervised acquisition")` into supervisor entry: **2 failed**, `Expected 'mock' to not have been called. Called 1 times. Calls: [call('supervised acquisition')].` |
| `supervisor_call_sites_are_enumerated_and_explicit` | Added an actual sixth AST call: **1 failed**, call-site map contained `unreviewed_sixth_caller`. Added an implicit policy default: **1 failed**, `AssertionError: missing policy reached construction`. Changing adaptive selection to short also failed the exact call-site map. |
| `callers_select_policy_after_event_and_hook_construction[single]` | Changed the selection's short arm to `DEFAULT`: **1 failed**, selected default policy instead of short. |
| Same, `[multiple]` | Relaxed `len(events) == 1` to `>= 1`: **1 failed**, selected short policy instead of default. |
| Same, `[hook, plan]` | Removed the constructed-hook predicate: **2 failed**, selected short policy instead of default for a resolved hook and for an action-plan adapter. |
| Same, `[composite]` | Removed the shared-reservation predicate: **1 failed**, selected short policy instead of default. |
| Same, `[factory]` | Allowed a callable factory to satisfy the list predicate: **1 failed**, selected short policy instead of default. |
| Same, `[adaptive]` | Changed the survey supervisor call to `SHORT_FIXED`: **1 failed**, selected short policy instead of default (the enumeration test failed too). |
| Same, `[positions]` | Changed the positions supervisor call to `SHORT_FIXED`: **1 failed**, selected short policy instead of default, driving 1,000 positions. |
| `phase_transition_is_policy_owned_and_bypasses_cadence[None, 2]` | Derived phase from `plan.frames`: **2 failed**. At the adaptive cap, `['acquiring', 'finalizing']` differed from `['acquiring', 'acquiring']`; the independent terminal promise was missed too. Set progress `active_bound_s` to zero: **2 failed**, `0 == (0.003 + 300.0)`. |
| Same, `[2]` | Removed the phase-change cadence bypass: **1 failed**, `['acquiring'] == ['acquiring', 'finalizing']`. Terminal count intentionally differs from the accounting cap so the old planned-final emit cannot satisfy the assertion. |
| `short_policy_observed_gap_widens_window` | Discarded the observed gap: **1 failed**, `(5.0, 'quiet_floor') == (12.5, 'observed_gap')`. |
| `error_after_terminal_frame_keeps_earliest_bound[False]` | Waited only for error grace: **1 failed**, `'error_grace' == 'short_fixed_runtime'`. |
| Same, `[True]` | Waited only for runtime expiry: **1 failed**, `5.099999999999998 <= ((2.5 + 1.0) + 0.1)`. |
| Same, both cases | Discarded the stored engine exception: **2 failed**, `None == 'ValueError: notification failed after frame'`. |
| `short_waiter_completion_owns_cleanup_once_and_refusal_lifetime[False, True]` | Called owned reservation closure twice: **2 failed**, `Expected 'close' to have been called once. Called 2 times.` |
| Same, `[True]` | Removed transfer of shared reservation closure to the timeout waiter: **1 failed**, `Expected 'close' to have been called once. Called 0 times.` The test also checks refusal while live and availability after release/join. |
| `hung_camera_probe_is_bounded_after_pending_and_exception` | Probed before constructing pending state/exception: **1 failed**, first observation was a boolean rather than `AcquisitionUnterminated`. Restored the generic bridge timeout: **1 failed**, `5.0720253749750555 <= (2.0 + 0.5)`. The fake has its own bounded release, so the mutant fails instead of hanging. |
| `timeout_diagnostic_acknowledgement_precedes_result[False, True]` | Removed `acknowledge=True`: **2 failed**. Healthy writer: no timeout record on disk at return (`assert False` on record lookup); blocked writer: persistence incorrectly true (`assert True is not True`). With the fix, the blocked case also requires stderr and event-sink fallback and the named flush-grace ceiling. |
| `expired_bound_uses_explicit_policy_name` | Derived bound name from `policy is SHORT_FIXED`: **1 failed**, `'runtime_ceiling' == 'measured_test_bound'`. |
| `acquisition_progress_renders_phase_and_accepts_partial_event[finalizing, acquiring, None]` | Removed phase rendering: **2 failed, 1 passed**; counts lacked the required phase suffixes. Required phase to render counts: **1 failed, 2 passed**, missing-phase event produced empty text instead of `frames 1 / 1`. |

Additionally, the browser test was run with only `microclaw/serve.html` restored to
`75aa4e6` (the rest of the fixed tree was retained), then the fixed HTML was
restored. It printed **2 failed, 1 passed in 0.30s**:

```text
{'text': 'frames 1 / 1'} != {'text': 'frames 1 / 1 · finalizing dataset'}
{'text': 'frames 1 / 1'} != {'text': 'frames 1 / 1 · acquiring'}
```

The missing-phase case passed on the original tree, as it should; its regression
sensitivity is demonstrated by the separate mutation above. After restoration,
the three browser cases plus the text-I/O integrity test printed **4 passed in
2.13s**. `git diff --check` was clean.

## Findings and design reconciliation

- D1's illustrative stub still shows provisional 30-second values, lacks the coordinator's explicit bound name, and predates 75a's diagnostic term returns. The later measurement and this assignment govern: 5/5 seconds, explicit names, term tuples retained.
- D1a still says a phase “may tighten” a deadline and discusses a `min` term. Implemented the explicit coordinator decision: phase is reporting only, with no additional deadline.
- D3 still asks for timeout/completion progress emits. Implemented the explicit narrower vocabulary and event decision: acquiring/finalizing progress, acquiring-or-notifying/finalizing timeout reporting, no completion phase/event.
- CLAUDE.md's engine-contract bullet says “a bound or a phase must never be gated on the saved-frame callback.” Its “phase” wording is broader than the requested D1a report. The implementation follows the explicit task: observed terminal frames determine the report; runtime expiry never depends on them.
- The existing diagnostic emitter's no-writer success boolean was not a persistence acknowledgement. Its no-writer acknowledgement branch needed the small correction described above; no writer/queue/coalescing/fallback/grace implementation was changed.
- The coordinator's successful full-suite baseline cannot be reproduced under this sandbox's loopback-bind restriction and the packaging test's isolated-build behavior. The exact failures and verification limits are recorded above; no prohibited network retry was made.

Nothing prohibited is required for the implementation. `run_mda`, camera ownership/live mode, emitters/exported scripts, adaptive runtime planning, error grace, fallback ceiling, gap multiplier, writer internals, history JSONL and AuditLog are unchanged. Full unrestricted-environment verification and the demo-machine gate remain coordinator work.
