# Block 84a implementation report

Implemented on `block-84a` in `/Users/zachcm/Code/microclaw-wt-84a`. No other
worktree or branch, merge, push, network access, dependency installation, web
route, registered tool, panel change, publisher-document change, or demo-gate
execution.
Neither `design/70` nor `design/84` was edited.

## Commits

- `3a211f4` — Retain declared package windows after terminal results; manifest 1.1, bounded ownership, fixture backends and conformance tests.
- `f5247e3` — Disclose package windows, separate session grants, preflight before consent, and retain releases for live owners.
- This report commit — Record D1–D5 locations, seams, failure-first evidence and validation.

## D1–D5 locations

| Decision | Implementation |
|---|---|
| D1: boolean under manifest 1.1; forbidden on self_check and under 1.0; wire protocol unchanged | `microclaw/skill_packages.py:425`, `microclaw/skill_packages.py:841`. Fixture operations and protocol binding: `tests/fixtures/skill_packages/{executable,conformance}/manifest.json`; executable intake re-signed from committed TEST-ONLY publisher-a seed. |
| D2: switch stdout at accepted terminal, discard/count even buffered bytes, separate bounded stderr | `microclaw/skill_supervisor.py:422`, `microclaw/skill_supervisor.py:481`, `microclaw/skill_supervisor.py:498`. |
| D2: immutable historical job evidence | `microclaw/skill_supervisor.py:323`, `microclaw/skill_supervisor.py:334`, `microclaw/skill_supervisor.py:362`. Only handed-off window records freeze; headless notification behavior stays as before. |
| D2: hash artifacts while alive, close stdin, adopt before completion, free dispatcher slot | `microclaw/skill_supervisor.py:771`, handoff at `:839`, record marker at `:862`; the existing artifact loop is shared at `:957`, not copied into a second runner. Failed and cancelled worker terminals also hand off. |
| D2: one daemon reaper, kill descendants before bounded pipe joins; post-result exit code and errors are diagnostics | `microclaw/skill_supervisor.py:683`. Diagnostics access: `:667`. |
| D3: four reservations, queued cancellation and cleanup release, CPU-only atomic admission | `microclaw/skill_supervisor.py:54`, `:590`, `:655`, `:400`, dispatcher cleanup at `:768`. The reservation map includes adopted windows; admission counts this map once, rather than adding the table to it. Duplicate live window job IDs refuse rather than overwrite ownership. |
| D3: plain list/close API; close on quit; handoff/shutdown race | `microclaw/skill_supervisor.py:660`, `:675`, `:725`, adoption under lifecycle lock at `:855`. No process/filesystem I/O under that lock. |
| D3: persisted historical handoff plus live owner retains release across processes | `microclaw/skill_store.py:1072`. Conservative retention continues until owner exit, even after an individual window closes. |
| D4: host disclosure and exact +window subject for saved and live calls | `microclaw/completed_dataset.py:661`, `:670`, `:678`; suffix validation separate from digest: `microclaw/tools.py:2589`. Grant lookup/insertion continue using the complete exact subject. |
| D5: pre-confirmation refusal and repeated dispatcher check | `microclaw/completed_dataset.py:664`; `microclaw/skill_supervisor.py:270`, `:651`, `:794`. CREATE_NO_WINDOW remains unchanged at `:812`. |

## Seams and limits

`fixture_window` uses the existing `Observer`/NDTiff tailer, not another parser.
Its main-thread timer, frame-display count, lifecycle inbox, final drain,
artifact closure, close event and post-terminal EOF behavior are shared by two
parameter-selected backends (`fixture_worker/runner.py:233`). The default
backend imports and constructs tkinter only when selected, updates a Tk label,
and runs `after(10)` callbacks. The TEST-ONLY headless backend publishes the
same displayed frame count atomically to `display.json` and receives a close
event when `close.txt` appears in the reserved output directory. Both log timer
jitter to stderr. These files are not declared immutable result artifacts.
Artifacts use `write_bytes`, closing their stream before the descriptor/result.
The terminal is sent only after writer completion and final drain, or deliberate
close/cancellation/failure. EOF after a terminal leaves the timer active.

The supervisor accepts an injected desktop probe. Confirmation tests replace
only the module's desktop-probe callable; Linux-policy tests pass an explicit
environment mapping, and Windows session tests fake the narrow ctypes call.
No test opens a GUI or changes global DISPLAY/WAYLAND_DISPLAY to admit a job.
Pre-confirmation probing does not instantiate a supervisor or start threads.
Dispatch repeats the bounded check for direct callers.

On Linux the check requires DISPLAY or WAYLAND_DISPLAY; on Windows it checks
ProcessIdToSessionId and refuses session 0. On darwin it deliberately allows the
operation: the notebook specifies no supported macOS desktop policy, and this
implementation does not claim portable proof of an interactive desktop. A stale
display variable or inaccessible nonzero Windows session can still fail GUI
initialization, which the fixture reports as a failed terminal.

These seams exercise lifecycle and process ownership, not Tk visibility,
CREATE_NO_WINDOW behavior, desktop access, GUI responsiveness beside acquisition,
or SMAPpy's watcher adaptation. 84b must observe the real tkinter window and
measure timer jitter and burst cadence with agreed criteria. No real GUI, Windows
Job Object execution, or rig/cadence measurement was performed in this macOS
worktree. Existing Windows containment code is retained; its narrow desktop API
is tested with a fake. The dispatcher retains the headless monitor and cleanup
branch, including clean-parent tree kills and shutdown deadlines.

No exception type was added. Desktop refusals use the existing PackageRefusal:
pre-confirmation callers retain their existing user-facing refusal boundary;
direct dispatch callers get a terminal supervisor failure with the refusal field
and detail. No hardware/acquisition exception path was changed.

## Minimum 84a test mapping and pre-fix evidence

All names below are in `tests/test_skill_supervisor.py` unless another file is
named. WF means watched failing; REG means an invariant that passes before and
after. The pre-fix source was the initial HEAD `c022167` (the ledger-only commit
above notebook start `46a22a7`). Each product file was replaced using
`git show c022167:<path> > <path>` and restored from its saved bytes in `finally`;
nothing was staged by a checkout. Other product files remained current so tests
could reach the behavior being examined. For the fixture-worker rollback, its
manifest asset hash was temporarily matched to the old worker to avoid an asset
refusal masking the missing operation. That manifest was then restored too.

| Notebook test | Test name(s), classification, observed pre-fix failure |
|---|---|
| Final record and actual second job progress while window process lives | `test_84a_window_result_frees_slot_before_process_exit` — WF, `tests/test_skill_supervisor.py:1884`: succeeded expected, actual supervisor_failed with shutdown_deadline. `test_84a_window_handoff_releases_worker_and_freezes_evidence` — WF, `:89`: supervisor_failed instead of succeeded with post-result flood. Green tests assert process.poll() is None after second job result and exact record equality. |
| Headless lingering and clean-exit tree kill | `test_deadlines_kill_heartbeat_tree[terminal_hang-shutdown_deadline]`, `test_success_also_kills_descendants_holding_pipes` — REG; both passed on old and new supervisor. |
| Headless/window grants do not cross either way | `tests/test_session_grants.py::test_84a_window_grants_are_separate[False/True]` — WF, `:191`: is_grantable rejected the +window subject. Green tests grant/repeat one exact subject and decline the other, in both directions. |
| self_check and 1.0 declaration refusal | `tests/test_skill_packages.py::test_84a_window_manifest` — REG for forbidden/nonboolean rows, all six passed pre-fix. Its two 1.1 acceptance rows are WF at `microclaw/skill_packages.py:132`: unknown/forbidden opens_window key. No manufactured failure for already-closed-key refusals. |
| Artifacts hashed while alive; later nonzero exit leaves success | `test_84a_window_nonzero_is_only_diagnostic` — WF at `:89`, old state supervisor_failed instead of succeeded. Green asserts artifacts, live process, null exit_code and unchanged record after diagnostic exit 3. The flood/handoff test also verifies the snapshot hash while alive. |
| Shutdown kills windows and a racing handoff | `test_84a_window_close_and_shutdown_race` — WF at `:89`: old supervisor never made the first successful handoff. Green test then holds artifact hashing at an event barrier, starts close, resumes hashing and checks ordinary cleanup/no table or reservation leak. |
| Concurrent four-slot cap and all releases | `test_84a_window_reservations_concurrent_and_queued_cancel` — WF at `:1677`: 12 admitted instead of 4. `test_84a_open_windows_count_once_and_duplicate_id_refused` — WF at `:89`, no successful retention. `test_84a_window_reservation_cleanup[queue_full/launch_failed/startup_hang/ignore_cancel]` — WF at `:1722`: no reservation map; their deadline/launch/cancel behavior itself is REG. `[desktop]` — WF at `:91`, old shutdown_deadline instead of desktop refusal. `test_84a_window_prehandoff_hash_failure_kills_tree` — WF at `:1870`: the extracted shared artifact-hash seam did not exist; its green assertions check killed process and no retained entry/reservation after failure. |
| Malformed/unbounded post-result output is separate and bounded | `test_84a_stdout_buffer_switch_at_terminal` — WF at `:1826`: invalid_utf8 protocol violation instead of discarded bytes. Includes buffered garbage, oversized bytes, and another terminal; checks exact count. `test_84a_window_handoff_releases_worker_and_freezes_evidence` also floods stderr and verifies bounded diagnostics and immutable evidence, including late notification refusal. |
| Parent exit with pipe-holding child | `test_84a_window_parent_exit_kills_pipe_child` — WF, missing window_retained key at `:1627`. Green requires handoff, waits for reservation release, and checks the real child heartbeat stops. Existing headless child test is REG. |
| Foreign process cannot prune a terminal viewer's release | `tests/test_skill_store.py::test_84a_terminal_window_release_survives_foreign_pruner` — WF at `:2123`: foreign pruner returned no retained digest and deleted the install directory. A real second interpreter reads the persisted terminal record/live owner and calls _retention while the real window process remains alive. Dead owner also stops pinning. |
| Live fixture displays before completion, survives idle, reads appended frames and final drain | `test_84a_fixture_live_pause_final_drain_and_eof` — WF with old fixture worker at `:1577`: displayed frame 1 never appeared (old worker has no fixture_window operation). Green pauses >6 observer polls/>20 timer ticks, appends frames, withholds terminal for unknown writer and then verifies all three frames in final output and display. |
| Closing before result yields cancelled with partial artifacts/incomplete input | `test_84a_fixture_close_before_result_is_cancelled` — WF with old fixture worker at `:1577`: no displayed frame before close (missing operation). The protocol cancellation invariants are REG: `test_cancel_preserves_partial_artifact`, `test_ignored_cancel_kills_tree` passed old/new. Green fixture close asserts cancelled, partial artifact and input_complete false. |
| List excludes pre-result jobs | Assertions in both live fixture tests; old supervisor lacked list_open_windows (`:1751`/`:1782`), WF for API. They are also covered by the old-worker rollback above. |
| EOF after result does not close window | `test_84a_fixture_live_pause_final_drain_and_eof` — WF as above; green checks process liveness and final display after stdin closure. |
| Desktop refusal before CONFIRM_FN; saved/live host and subject | `tests/test_completed_dataset.py::test_84a_saved_window_consent_and_desktop_refusal`, `tests/test_acquisition_analysis.py::test_84a_live_window_consent_and_desktop_refusal` — WF. Old summary omitted host/window disclosure and used the unsuffixed subject; true rows fail at `:1438`/`:498` after the callback assertion is surfaced. False rows fail at `:1440`/`:500`: CONFIRM_FN was called. `test_84a_linux_desktop_preflight`, `test_84a_windows_session_zero_preflight` — WF at `:1796`/`:1811`, missing probe API. |

Additional coverage: `test_84a_all_worker_terminals_can_handoff[failed/cancelled]`
was watched failing at `tests/test_skill_supervisor.py:1900`, missing the historical
window_retained marker; it now asserts preserved worker outcome, null exit code
and partial cancelled artifacts. Failure-first output was captured in
`/tmp/84-baseline-{0..4}.log`, `/tmp/84-more-baseline-{0..2}.log`, and
`/tmp/84-last-baseline.log` during this session. The table records the relevant
failure lines without depending on those scratch files remaining available.

## Validation and remaining work

The prescribed seven-file command was run once: **2300 passed, 9 skipped,
3 failed in 133.07 s**. The three failures were fixture consistency, not product
behavior: two protocol-string structural tests now needed to remove the
version-specific window declaration from their unrelated test input; the 83f6
committed archive/catalog needed regeneration from the changed fixture. Those
three cases were corrected and rerun in an affected selection: **11 passed**.
A further narrow signature/protocol/83f6 selftest selection passed **6 cases**.
The full seven-file command was not repeated, as requested. All supervisor,
store, grants, saved-dataset and acquisition-analysis cases passed in that
seven-file run. `git diff --check` passed. Only targeted files/selections were
run; the repository-wide suite belongs to the coordinator.

The collateral fixture refresh is limited to
`design/83f6-gate/artifacts/fixture-lab-lock-fixture-1.0.0.zip` and
`design/83f6-gate/catalog-1/catalog.json`, rebuilt deterministically by the
existing offline generator and signed with the committed TEST-ONLY seed.
It changes no demo-gate code or policy and is necessary to keep the existing
byte-for-byte regeneration test passing. The executable intake's committed
signature reproduction checks also passed.

No notebook decision was reopened. The artifact loop was extracted so both
lifecycle branches use identical validation. The extra duplicate-window-job-ID
refusal protects the four-slot bound and tree ownership. No durable window lease
or exact early unpinning was added; retention conservatively lasts until owner
exit, as allowed. No additional R-row is proposed beyond the notebook's R151 and
R152. R152 remains relevant because the publisher's no-rewrite-after-result
artifact promise is still unenforced. R150's worker-check need remains raised,
and R139's lack of any feedback channel remains unchanged.
