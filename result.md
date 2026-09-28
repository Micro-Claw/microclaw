# Block 83e-3 result

Implementation on `block-83e-3`, in the supplied linked worktree only. No merge,
push, new worktree, saved permission, or demo gate. `uv.lock` was not touched.

## Commits

`6b2a1d6` — `feat: dispatch plan-time package analysis on acquired datasets`.

`8e4fc03` — `test: distinguish acquired dataset suffix from requested name`.

`docs: report block 83e-3 implementation and validation` — this report commit,
following those two implementation/test commits.

## Decisions and implementation

| Decision | Implementation |
| --- | --- |
| D0 | Plan-time `analysis` only. No persisted consent or R140 implementation. Existing `analysis` confirmation kind and session grants apply. |
| D1 | `microclaw/tools_schema.py`: one `ANALYSIS_SCHEMA` shared by all six tools. Their Python signatures also declare the argument. `execute_tool` consumes it; direct Python calls with analysis refuse and identify the consent boundary. `run_mda` has no argument and its description explains the engine/dataset boundary. |
| D2 | `completed_dataset.prepare_package_analysis`: shared with the saved-dataset route; validates identity, digest, operation and parameters, prompts without a lock, then reloads policy and resolves the same digest again. JSON parameters and the resolved release are the call snapshot. Disclosure covers growing datasets and queue overflow. |
| D3 | `execute_tool` returns exactly `{"status": "Acquisition cancelled: analysis declined", "cancelled": true}` before entering the acquisition tool. |
| D4 | One validation/consent call in `execute_tool`. The saved-dataset tool uses the same preparation function and shared `submit_package_analysis`, not a second consent implementation. |
| D5 | `_acquire_with_hooks` constructs one `AcquisitionAnalysisJob` after `_acq_dataset_path` and before `acquire()`. A preallocated job ID names `<dataset>/analysis/<job_id>/`; mkdir completes before `Supervisor.submit` can enqueue it. Existing package locks refuse without waiting. Job writes run on recorder threads. Lock, mkdir, queue, submission and persistence failures remain nested. |
| D6 | The job carries separate acquisition outcome and writer state. Normal completion is completed/finished; engine or hooked failure is failed/finished only after the teardown waiter finishes, otherwise failed/unknown. Unterminated is always unterminated/unknown, followed by writer-finished from the teardown waiter. A lock serializes the two notifications when teardown races the return. |
| D7 | Thread-local per-call collector, attached by `execute_tool` on successful and error results, including results produced by inner exception handlers. Each job has identity, parameters, dataset, reserved output, job ID, durable record path, state and failure. The export loop discloses every job before skipped/refused paths using shared `one_line`; hardware emitters are unchanged. |
| D8 | No store read or supervisor construction without `analysis`, asserted structurally. Foreground measurement and its limits below. |
| D9 | Not built or exercised, as requested. Real collision suffixes, live-dataset mkdir, real teardown artifacts, rig per-position runs, cadence and demo export execution remain for the later demo gate. Unterminated lifecycle is exercised off-rig across the six real tool entry points. |

The package-panel disclosure is produced by `transcript.js`'s
`skillPackagesView` and displayed through the existing `serve.html` binding.
It explains `run_mda` and contains no “discovery” wording. The stale remove
refusal, retention docstring and adjacent retention comment now refer only to
live jobs. `skill_store.resolve` isolates damaged matching installs so they
cannot hide another valid copy.

Tool count and export-marker counts are unchanged; `CLAUDE.md` needs no count
edit. The marker and schema-parity tests check the actual registry.

## Files changed

- `microclaw/completed_dataset.py`: shared preparation/submission and acquisition job lifecycle/recording.
- `microclaw/skill_supervisor.py`: caller-reserved job IDs, permitting cwd creation before enqueue.
- `microclaw/skill_store.py`: per-install resolution isolation and stale wording.
- `microclaw/tools.py`: signatures, execute boundary, dataset dispatch, lifecycle and export-loop disclosure.
- `microclaw/tools_schema.py`: shared schema and MDA disclosure.
- `microclaw/transcript.js`: package-panel MDA line.
- `tests/test_acquisition_analysis.py`: new integration and structural timing tests, reusing existing package and dispatching acquisition fixtures.
- `tests/test_bounded_acquisition_wait.py`: extend the existing six-entry-point test with analysis jobs and late-writer notification assertions.
- `tests/test_session_script_export.py`: disclosed jobs, multiline values, skipped acquisition and subsequent hardware step, compiled and executed against existing fakes.
- `tests/test_transcript_js.py`: update exact disclosure expectation and assert no discovery wording.
- `result.md`: this report.

## Tests and standing rules

New `test_acquisition_analysis.py` tests cover:

- Shared schema and MDA exclusion; all six tools decline before their bodies or hardware effects.
- No package lock across confirmation; removed release and changed policy after a human wait refuse.
- No analysis means no store resolution, policy read or supervisor construction.
- Worker cwd exists before submit; initial and terminal job writes are off the foreground thread; real fixture worker receives completed/finished after the existing dispatching fake's teardown.
- Held lock, mkdir failure, thrown submit, refused submit, full-queue response and record-write failure preserve acquisition and remain invisible to `_recorded_outcome`.
- Malformed job/package neighbors and a damaged matching install affect only themselves.
- A startup sweep leaves another live owner's job unchanged and retained.
- Hooked and plain engine failures preserve jobs; failure before teardown reports writer unknown.
- Three per-position datasets create three jobs; the next call gets a fresh collector.
- Invalid argument shapes, builtin adapter, bad digest and extra fields refuse before consent or hardware.
- D8 reports timings without timing thresholds.

The existing six-tool unterminated test now runs both without and with analysis.
It keeps its original continuation/position-accounting assertions and additionally
checks the nested jobs and ordered unterminated/unknown then writer/finished
notifications. This reaches the actual tools' broad exception handlers.

The new export test runs both normal and skipped acquisition cases with two jobs,
multiline parameters and a multiline publisher failure, then verifies a later
exposure write actually runs. The panel's exact-string test changed because D1
adds the MDA disclosure. No existing assertions were deleted or weakened.

Final targeted validation: **1,131 passed** across the seven requested files,
`tests/test_acquisition_analysis.py`, and `tests/test_schema_parity.py`.
The combined run initially returned 1,129 passed and two sandbox-only failures:
`test_browser_opens_only_once_the_port_accepts` and
`test_browser_opener_gives_up_instead_of_hanging` could not bind loopback sockets.
Both passed when rerun with loopback access (2 passed in 1.35 s).

After aligning D5's mkdir-before-lock ordering, the two affected analysis files
passed again: **115 passed in 11.23 s**. After strengthening the collision-suffix
assertion, the new integration file passed again: **30 passed in 1.52 s**. The
full suite was not run.

Two in-memory mutation checks were run without modifying tracked source:
bypassing execute-time analysis caused all six decline tests to fail; suppressing
`writer_finished` caused all six analysis-enabled unterminated cases to fail
specifically on the missing writer notification, while the six original cases
passed.

An intermediate export run overlapped a source edit and produced six
source-extraction failures; final validation runs with source files stable.

## D8 measurements

The 115-test dispatch-validation run: **n=10 per route**, local runner reporting
`macOS-14.5-arm64-arm-64bit`, Python **3.12.14**, provisioned worktree environment.
The sandbox did not expose the exact hardware model through `sysctl`.

| Foreground span | Median | Maximum |
| --- | ---: | ---: |
| Tool entry to `acquire()`, no analysis | 0.583 ms | 0.939 ms |
| Tool entry to `acquire()`, analysis with automatic consent | 7.786 ms | 12.079 ms |
| Dataset dispatch alone, including mkdir, package lock, submit and recorder startup | 1.677 ms | 2.118 ms |

The difference between the two entry-to-acquire medians is **7.203 ms**. This is
an observed difference of medians, not a per-call causal decomposition. The
baseline uses the same real tool/preflight and existing fake acquisition
constructor. Confirmation uses an immediate test response.

A repeat during the final collision-test validation (again n=10 per route on the
same runner) measured dispatch median/max **1.843/4.451 ms**, no-analysis
entry-to-acquire **0.519/20.435 ms**, and analysis entry-to-acquire
**7.390/16.271 ms**. The baseline maximum shows why the tests use structural
assertions rather than elapsed-time limits; its cause was not attributed.

Human wait, real microscope timing, real NDTiff I/O, individual dispatch phase
costs, and concurrent worker CPU/storage contention are not attributed. Maxima
are observations, not assertions. No test depends on a wall-clock threshold.

## Limits and decision concerns

No D0–D9 decision was replaced. No decision appears impossible or requires an
alternative. D9 remains deliberately deferred to review and the demo gate.

The tool result is a snapshot; subsequent worker completion and lifecycle delivery
are recorded asynchronously. A failed record write is reported independently of
the worker state, so it does not incorrectly release a running job's retention
pin. Persistently unwritable storage cannot provide a durable record; acquisition
still proceeds and any failure already observed is nested in the call result.
