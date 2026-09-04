# Block 75a result

Implementation commit: `5f6bdaf` (`Persist correlated acquisition diagnostics`).

## Test result

Exact command: `.venv/bin/python -m pytest -q`

Result: **2825 passed, 99 skipped, 3 warnings in 153.01s**. The three warnings
were the documented Starlette first-import deprecation and the two pre-existing
`phase_cross_correlation` warnings.

## New tests and pre-fix evidence

All new tests are **watch-it-fail** tests; none uses the order/structure mutation
exception. They were restored over commit `bebc515` and run before the product
changes were restored.

- `test_acquisition_writer_coalesces_progress_without_dropping_lifecycle`:
  `ImportError: cannot import name 'AcquisitionDiagnosticWriter' from 'microclaw.conversation'`
- `test_acquisition_writer_acknowledges_fsync_and_bounds_blocked_writer`:
  `ImportError: cannot import name 'AcquisitionDiagnosticWriter' from 'microclaw.conversation'`
- `test_saved_frame_callback_never_calls_or_waits_for_audit_log`:
  `ImportError: cannot import name 'AcquisitionDiagnosticWriter' from 'microclaw.conversation'`
- `test_complete_acquisition_records_ordered_lifecycle_and_correlation`:
  `ImportError: cannot import name 'AcquisitionDiagnosticWriter' from 'microclaw.conversation'`
- `test_timeout_diagnostic_records_bound_term_and_camera_state`:
  `ImportError: cannot import name 'AcquisitionDiagnosticWriter' from 'microclaw.conversation'`
- `test_cli_acquisition_file_is_history_sibling_and_honors_save_flag[True]`:
  `KeyError: 'acquisition_diagnostic_writer'`
- `test_cli_acquisition_file_is_history_sibling_and_honors_save_flag[False]`:
  `KeyError: 'acquisition_diagnostic_writer'`
- `test_web_session_acquisition_file_is_history_sibling`:
  `AttributeError: 'Session' object has no attribute 'acquisition_diagnostic_writer'`

The first corrected full-suite run additionally caught the repository's test
I/O invariant (`Text I/O must specify encoding='utf-8'`) on three new reads; all
three now name UTF-8, and the exact full-suite rerun above is green.

## Lifecycle retention

The queue capacity reserves more slots than one acquisition's lifecycle can
produce before its writer drains. Progress alone coalesces: a full queue causes
the latest progress sample to replace the pending sample. A lifecycle enqueue
first displaces queued progress, never another lifecycle record. Thus frame
volume cannot consume the protected lifecycle capacity, while the callback
always uses non-blocking progress submission and never calls `AuditLog.append`.
The real `AuditLog` remains the only appender and performs its existing locked
write, flush, and fsync on the writer thread.

## Scope notes

- No bound constant, expiry conjunction, tool-result field, registered tool,
  confirmation path, camera bridge path, live-mode behavior, ownership behavior,
  or teardown-thread lifetime changed.
- The acknowledgement mechanism is implemented and directly tested; it has no
  tool-result consumer, as required for the 75a/75b seam.
- Both CLI and browser sessions derive `_acquisitions.jsonl` by replacing
  `_history.jsonl`, use the history filename stem as the session correlation ID,
  honor `save_history`, and drain the writer during normal shutdown.
- No additional wrong-but-out-of-scope code was found.
- No part of D4 was unimplementable as scoped.
