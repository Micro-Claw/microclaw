# A package operation may open a window

**Status: proposed 2026-10-07.** No blocks started. Answers the SMAPpy
publisher's request of 2026-10-07 (`request-skill-package-viewer.md`, in the
rig evidence archive, not the repo). It is the "later" that `design/71`
§"Feasibility against SMAPpy 0.1.0" (last table row) and `design/83`
§"What we deliberately do not build" ("a GUI viewer") deferred.

## The problem

Live SMLM fitting is useful during acquisition only if the person at the
microscope can **see** the reconstruction while the camera runs: density,
drift, focus, labelling, keep going or change the laser, all in the first
minute. A headless worker that writes `locs.h5` answers none of that before the
run ends. SMAPpy's own GUI already follows a growing NDTiff and redraws; the
package only needs to start it on the dataset MicroClaw is writing.

The protocol forbids it today. Read from the code, not from the request — the
request's picture of today's lifecycle is slightly wrong, and the difference
shapes the design:

| Where | What happens to a worker that keeps a window open after its `result` |
|---|---|
| `skill_supervisor.JobHandle._stdout` | the `result` sets `_shutdown`; any later stdout line is `message_after_terminal`, a failure |
| `skill_supervisor.Supervisor._run` loop | `now - shutdown >= shutdown_grace_s` (10 s) → `shutdown_deadline`, a failure |
| `_run`'s `finally` | `_kill_tree` runs after every exit, success included; artifacts are hashed **after** the tree is dead |
| `_run`, exit code | `nonzero_exit` after a terminal is a failure |
| the dispatcher | the job record is finished — and the worker slot freed — only when `_run` returns, i.e. at process exit |
| `skill_packages` manifest | an operation is the closed set `{name, input_schema, output_schema}` |
| `docs/publishing-skill-packages.md` | "open no GUI" is a publisher obligation |

So **today the job does not end at the `result`; it ends at process exit,
bounded by a 10 s grace.** A window that stays open after the result is
killed at 10 s and the job is recorded as failed.

## Decisions

### D1 — the operation declares it: `"opens_window": true`

An optional boolean on the operation, admitted only under
`protocol_version: "1.1"` (`SUPPORTED_PROTOCOLS` gains `"1.1"`; the wire
protocol `microclaw.analysis.v1` is unchanged — no message changes). A version
bump rather than a silently widened 1.0 key set, so an older MicroClaw refuses
a 1.1 release as "unsupported executable protocol" at executable intake.
Structural manifest validation still runs independently: an older validator
may report "unexpected key" when handed the manifest directly. Do not promise
one error across both paths. `self_check` may not declare it (even as `false`): it runs at install,
unattended. For other operations, absence means `false`; only literal booleans
are accepted, and 1.0 rejects the key even when `false`. Keep manifest/intake
protocol binding and the wire protocol unchanged.

Not a separate operation kind (`"mode": "viewer"`): the job, its messages, its
artifacts and its consent are identical to a headless operation. Only process
lifetime and the disclosure differ, and one boolean says that.

### D2 — the job ends at the `result`; the process becomes a *window*

For an `opens_window` operation, on a valid terminal `result` (`succeeded`,
`failed` or `cancelled`), `_run` does what its `finally` does today **except
kill the tree**:

1. atomically switch stdout to bounded chunk draining at the accepted terminal,
   including bytes already buffered after its newline. Later bytes (invalid
   UTF-8, oversized lines, or a second result included) are discarded and counted
   in window diagnostics, never parsed or retained;
2. close stdin, keep draining stderr into the bounded buffer;
3. hash and retain the artifacts **now**, with the process still alive;
4. transfer `(process, job object, pipe threads)` and the reserved window slot
   into the supervisor's window table under its lifecycle lock, before signalling
   job completion;
5. finish the record (`exit_code: null`, `window_retained: true`), and return,
   freeing the worker slot. This is a historical handoff fact, not persisted
   evidence that a window is still open. The live table reports current state.

One daemon reaper per retained window waits for the launched process, then
kills any remaining descendants **before** joining pipe threads and closing
pipes and the Windows job handle. Use bounded cleanup, as on the headless path;
a child inheriting a pipe must not strand the reaper or the slot forever. A
venv launcher exiting does not prove its process tree exited. Exit code, stderr
tail, discarded-byte count and cleanup failures belong to bounded in-memory
window diagnostics and **never change the final job record**. Refactor both
`_drain_stderr` and `record()` accordingly: today the latter reads the mutable
buffer, so merely freezing `_record` would not freeze the result. Failures
before handoff retain the ordinary cleanup path; never leak a half-adopted tree.

**The viewer opens and updates during acquisition; only the terminal result
waits for completion.** The worker reads the growing dataset, fits available
frames and displays new localizations while continuing to receive lifecycle
notifications. Once writer completion is known (an acquisition notification
with `writer: finished`, or a subsequent `writer` notification), it drains the
remaining frames, finishes fitting, closes the declared artifacts and sends
its result. It can also finish early on cancellation or failure, preserving
`input_complete`'s existing meaning. The worker slot remains occupied during
fitting and is released at the result; the window then remains open.

**Closing the window before the result is a cancellation.** If the user closes
the window while the job is still running, the worker sends a `cancelled`
result with its partial artifacts and `input_complete: false`, then exits. The
protocol already allows an unprompted `cancelled` (`validate_worker_message`
accepts it in any state). Exiting without a result would be recorded as
`supervisor_failed` / `exit_without_terminal`, which misdescribes a deliberate
close.

After the result stdin is closed, so no further notification or `cancel`
arrives. The worker treats that EOF as handoff, never as a request to close
the window.

**Publisher requirement: idle time is not writer completion.** SMAPpy's
[NDTiffSource.watch](https://github.com/ries-lab/SMAPpy/blob/02ae2ab87c03e52c68755bcdc55590cdfc835990/src/smappy/io/ndtiff.py)
currently ends its stream after `settings.timeout` without a new frame;
[WatchSettings](https://github.com/ries-lab/SMAPpy/blob/02ae2ab87c03e52c68755bcdc55590cdfc835990/src/smappy/io/watch.py)
defaults to 30 seconds. The publisher integration must prevent an acquisition
pause from ending live fitting prematurely, use the lifecycle notification to
establish writer completion, and drain all remaining frames before finalizing.
Merely increasing the idle timeout does not establish completion. Cancellation
must remain responsive while waiting for frames.

This is compatible with SMAPpy's existing live display: [LiveFit and
LiveViewer](https://github.com/ries-lab/SMAPpy/blob/02ae2ab87c03e52c68755bcdc55590cdfc835990/src/smappy/live.py)
fit in a background thread and append queued localizations on a GUI timer;
the [GUI fitting path](https://github.com/ries-lab/SMAPpy/blob/02ae2ab87c03e52c68755bcdc55590cdfc835990/src/smappy/plugins/fit.py)
also emits each fitted block for display during the run. These references pin
the code inspected, not a shipped package worker; the lifecycle-aware wrapper
is publisher work.

Everything before the `result` is unchanged: startup deadline, caller deadline,
cancel, protocol violations, `supervisor_closed`. A worker that fails *before*
its result is killed with its window, as today. A worker that never answers
`cancel` is still killed by the shutdown grace, window included.

**The other path must not change.** A headless operation that lingers after
its result still gets `shutdown_deadline`, and its tree is still killed after
a clean exit (CLAUDE.md, the hookless-emitter rule). Tests on both.

Rejected: send the `result` when the window closes. It needs no code, and it
holds one of two worker slots and shows the agent `running` for as long as
someone looks at the picture.

**Contract for the publisher, stated in the doc and not enforceable here:**
every artifact named in the `result` is closed before the `result` is written
and is not written again by this process. The record vouches for the digest
taken at step 3. Continuously updated fit files must not be declared as immutable result
artifacts. They may continue to be written within the reserved `output_dir`,
but are not vouched for by the job record. A declared snapshot must use a
separate file that the viewer will not rewrite. (Step 3
also needs the file closed on Windows to hash it reliably.)

### D3 — windows are owned, bounded, and closable by the user

- **Kept in the job object** (Windows; process group on POSIX), so MicroClaw
  can terminate the owned tree. POSIX retains the existing limitation that a
  descendant calling `setsid` can escape; publishers must not daemonize or
  break away. Windows Job Objects remain the shipping containment mechanism.
- **Not counted** against `MAX_CONCURRENT_WORKERS`; bounded separately by
  `MAX_OPEN_WINDOWS = 4`, refused at `submit` with a message naming the open
  windows and pending reservations. Reserve atomically for every admitted
  window operation, including queued/running ones; release on queued cancel,
  refusal, launch failure, ordinary cleanup or completed window cleanup. Counting
  only already-retained windows lets simultaneous submissions exceed the cap.
  Never hold the supervisor lock over process or filesystem I/O. A bound because the supervisor's contract is that every resource
  has one, not as a policy on how many pictures a user may look at.
- **The Extensions panel lists open windows** (package, operation, job id,
  dataset, opened at) with **Close**, which kills the tree. It lists only
  windows **after handoff**. A window whose job is still running is stopped
  through the existing cancel route, which delivers `cancel` and gets a
  `cancelled` result with the partial artifacts. Killing it there would discard
  them and record a supervisor failure. No agent tool closes
  one: MicroClaw takes charge of the user's desktop only when asked, and the
  user asks by clicking.
- **On MicroClaw exit they close** (`KILL_ON_JOB_CLOSE`, as today; `killpg` in
  `Supervisor.close()` on POSIX). Recommended because a window that outlives
  MicroClaw is invisible to the next launch — which may happen mid-session and
  more than once — and nothing could list or close it. This is the operator's
  decision (Q1); breaking away would need `CREATE_BREAKAWAY_FROM_JOB` and an
  untracked process. Disclosed in the confirmation either way.

**Retain the release while its window lives.** `skill_store._analysis_retained_digests()`
currently pins only nonterminal jobs with live owners; a succeeded viewer would
lose its pin immediately. Extend retention to the historical window-handoff
marker while its recorded owner is alive, so another MicroClaw process cannot
prune its interpreter/assets. Conservatively retaining that release until the
owner exits is acceptable for this block; exact early unpinning would require a
separate durable lease. Exercise pruning from a second process, not only the
supervisor's own table.

The live window table is in memory only, which is accurate precisely because
a window does not outlive the process that owns the table. The only persisted
fact is the job record's `window_retained: true` — that a handoff happened —
which retention reads together with the owner's liveness; it never claims a
window is still open.

### D4 — consent: one more line, and a grant that cannot be laundered

No new confirmation. The existing `kind='analysis'` summary
(`completed_dataset.py`, and 83e-3's plan-time confirmation) gains a line:

    Opens a window on this computer's desktop (<hostname>); it stays open after
    the analysis ends and closes when you close it or quit MicroClaw.

Naming the host covers the remote-browser case truthfully without MicroClaw
guessing whether someone is at the PC.

**The session grant must not carry over.** `SessionGrants.is_grantable` keys an
analysis grant on `publisher/package@digest`, operation-blind, so a grant given
for headless `fit_ndtiff` would silently cover `fit_ndtiff_live`. Update `is_grantable` to validate the optional literal `+window` suffix
separately from the digest; today its digest validator rejects that suffix.
Grant lookup and insertion must use the same exact subject. A window
operation's subject becomes `publisher/package@digest+window`. A grant given to
a window operation covers the package's window operations for the session, the
same way the headless one does.

### D5 — refuse where no window can be seen

Before confirmation, a window operation is refused when this process has no
interactive desktop: Windows session 0 (`ProcessIdToSessionId`), or on Linux
neither `DISPLAY` nor `WAYLAND_DISPLAY`. The refusal says the operation opens a
window and this MicroClaw cannot show one, and to use an operation of the same
package that does not. MicroClaw does not know the package's headless
alternative; the package's skill text names it.

`CREATE_NO_WINDOW` (`0x08000000`, in `_run`'s `creationflags`) suppresses the
console only; a Qt or Tk window should still appear. **Unverified** — the demo
gate checks it.

These are preflight refusal checks, not proof a display is usable: display
variables can be stale and a nonzero Windows session can lack an accessible
desktop. A GUI initialization failure must send a failed result (or undergo
ordinary launch/deadline cleanup). Repeat the bounded preflight at dispatch to
cover CLI/direct supervisor callers; keep OS probing out of `submit`. macOS
needs its own supported-desktop policy before claiming this refusal is portable;
the shipping gate here is Windows. Test refusal before `CONFIRM_FN` is called.

### D6 — deadlines: no change

`STARTUP_DEADLINE_S` is time to the *first message*, and the publisher sends a
`status` before importing Qt. The window's own start-up after that counts
against nothing unless the caller supplied `deadline_s`. Keep that existing policy;
the publisher doc says it. The reservation and worker slot remain occupied
until result, cancel or exit even if startup hangs after the first status.
Sending status is not proof a window appeared; the demo gate observes it.

### D7 — no rig block in the job (request item 6)

Declined here. Micro-Manager already writes pixel size, ROI and binning into
the NDTiff metadata SMAPpy reads; the values SMAPpy cannot find there (offset,
ADU per photon, EM gain) are ones MicroClaw does not record either. A block
that mostly duplicates the dataset is a second source to disagree with it.
Opened as a register row (below) so a real gap, if one appears, has a home.

### Saved datasets (request Q4): yes, with no extra work

`run_analysis_on_saved_dataset`'s package route submits the same job with
`writer: finished`; nothing in D1–D6 distinguishes live from saved.

### Export: unchanged

Package analysis already emits a `# Analysis was not reproduced` comment
(`_emit_saved_analysis`, and the acquisition `analysis` argument's equivalent).
A window changes nothing a standalone script could reproduce.

## What this does not do

No route from the window back into acquisition (`R139` stands; the window is
an observer with a picture). No frame streaming. No embedding in MicroClaw's
web GUI. No new trust: a worker already runs with the user's permissions.

## Blocks

**84a — supervisor and manifest (LOCAL).** D1, D2, D3's table and bound and
exit behaviour, D4, D5. The executable fixture gains `fixture_window`, a
stdlib **tkinter** window op (so nothing waits on SMAPpy — `R83`'s principle),
and the conformance release gains a window worker that writes after its
result, exits nonzero after its result, and never answers cancel. Tests that
must exist. Those for new behaviour are watched failing on the pre-fix tree;
the headless and cancel invariants are regression tests and simply pass on
both trees:
- the job record is final and a **second job starts** while the first window
  process is still alive (the slot was released, observed, not inferred);
- a headless op that lingers still gets `shutdown_deadline` and a tree kill;
- a grant on `@digest` does not cover `@digest+window`, and vice versa;
- `self_check` declaring `opens_window` is refused; a 1.0 manifest carrying it
  is refused;
- artifacts are hashed before the window exits; a nonzero exit afterwards
  leaves the job `succeeded`;
- `Supervisor.close()` kills open windows, including a handoff racing shutdown;
- concurrent submissions reserve at most four slots, and every refusal/cancel/
  cleanup path releases its reservation;
- malformed and unbounded post-result output stays bounded, with immutable job
  evidence and separate window diagnostics;
- a parent exiting with a pipe-holding child still has its tree cleaned up;
- a second process cannot prune a terminal viewer's release while its owner lives;
- the live fixture processes and displays new data before writer completion,
  stays active across an idle pause, then processes frames appended after the
  pause; it sends no terminal result until writer completion and final draining
  (except cancellation or failure), and closes declared artifacts before result;
- closing the fixture's window before the result yields a `cancelled` result
  with partial artifacts and `input_complete: false`, not `exit_without_terminal`;
- the open-window list excludes jobs that have not reached their result;
- stdin EOF after the result does not close the fixture's window;
- desktop refusal precedes confirmation, and both live and saved routes disclose
  the host and use the correct grant subject.

**84b — panel, publisher doc, demo gate (DEMO MACHINE).** The panel's open
window list and Close; `docs/publishing-skill-packages.md` replaces "open no
GUI" with the D1/D2 contract and D6's deadline note. Gate, as a program where
it computes: the fixture window **appears** under `CREATE_NO_WINDOW` (operator
judges; screenshot); the job is final while it is open; Close kills it;
quitting MicroClaw kills it; and **GUI responsiveness at below-normal priority
beside a 1000-frame burst** — the publisher's open question, measured as the
window's own event-loop lag (tkinter `after(10)` jitter logged to stderr), with
burst cadence reported as 83e-4 did. Compare the same burst without a window;
report event-loop lag p50/p95/max, sample count and burst timing for both. Agree
the acceptable responsiveness/cadence regression before scoring the gate;
logged jitter alone is evidence, not a pass criterion. Verify tkinter is
available in the installed fixture interpreter first (stdlib does not guarantee
Tcl/Tk is installed), and report a missing fixture dependency as NOT EXERCISED.

SMAPpy itself is not a gate subject: it remains blocked on `R85`, the
publisher's, and this notebook must not depend on it. Its publisher-owned
conformance test must also pause frame production beyond the watcher's
configured idle timeout while the writer remains unfinished, append more
frames and observe new fits/display updates, then announce writer completion
and verify the final artifact includes all frames. A shortened timeout makes
this practical in tests; the MicroClaw fixture test proves the generic
lifecycle, not that SMAPpy's watcher has been adapted.

## Register

Rows this touches in `design/70`:
- **`R139`** (results cannot steer acquisition) — unchanged and reaffirmed: a
  window is not a feedback channel.
- **`R85`** (SMAPpy's preconditions) — not advanced; `fit_ndtiff_live` adds a
  fourth operation to what the publisher must ship.
- **`R150`** (no publisher-side worker check) — **raised in importance.** A
  window path is exactly what a publisher cannot test by piping lines in CI,
  so until `check-worker` exists the first run of a window operation under
  MicroClaw is on a user's machine. `check-worker` should drive a window
  operation to its result and report the window as left open.
- **`R142`** (package cost on a real camera) — the live window is the
  realistic load; when `R142` runs on M2, run it with SMAPpy's window open.
- **`R140`** (saved permissions) — if ever built, its pins include the
  operation, so a saved permission for a window operation opens a window on
  every matching acquisition with no prompt. Its creation confirmation must say
  so.

Rows this would open:
- **`R151`** — a job carries no typed microscope block (D7); LOW, LOCAL.
- **`R152`** — an artifact may be rewritten by its still-open window after the
  record vouched for its digest (D2's contract is unenforced); LOW, LOCAL — a
  re-hash at window exit is the cheap check if it ever bites.

## Defaults to review before implementation

These are proposed defaults, not blockers to reviewing this notebook. The
shutdown ownership model in D2/D3 assumes windows close with MicroClaw; choosing
breakaway requires a different lifecycle and retention design first.

1. On MicroClaw exit, close windows (recommended, D3) or let them outlive it?
2. `MAX_OPEN_WINDOWS = 4` and not counted as workers — agreed?
3. `opens_window` under protocol 1.1, not a `mode` — agreed?

## Run ledger

| Block | Branch | Start commit | Status |
|-------|--------|--------------|--------|
| notebook | `design-84-package-windows` | `5e2f90a` | proposed 2026-10-07, PR #53 |
