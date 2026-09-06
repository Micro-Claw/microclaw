# Hooked autofocus acquisitions must survive session export

Status: **PROPOSED**, 2026-09-06. Investigation and implementation plan only;
no runtime changes or microscope replay performed.

Reviewed against `main` at `761c67d`, 2026-09-06. The findings retain the
session evidence; the implementation plan reuses existing export helpers while
making callback wiring, dependency closure and behavioral validation explicit.

## Finding

The agent successfully wrote an export file, but the fixed-plan multiposition
emitter explicitly refuses both autofocus hooks used in this session. This is
a generic export capability gap, not an Andor camera or EMU-specific failure.
The agent then misdescribed the refusals as skipped successful calls and wrote
an unvalidated replacement whose behavior differs from the session.

Source bundle:
`/Users/zachcm/Documents/Documents - Beyonce/Projects/Micro-Claw/beads_3x3_autofocus_supplement_m2`.
Original acquisition/export paths in the history are under
`F:\DataSSD\beads_3x3_autofocus`; the supplied directory is a collected bundle.

## Evidence

Line numbers below refer to physical JSONL lines in
`20260905_174132_043458_microclaw_history.jsonl` and source lines in the supplied
`beads_af_session_20260905.py`.

| Evidence | What it establishes |
| --- | --- |
| History line 32 | Agent requests `export_session_script` with only `output_path`; no selection excludes the acquisitions. |
| History line 33 | Export reports `Session script exported.`, `emitted_calls: 1`, and all three multiposition attempts in `recorded_calls`. The one emitted ID is the laser power write, `toolu_01F9qtEFXVpd5dE6Q1bJL9AD`. |
| Script lines 450–452 | First attempt is correctly `SKIPPED`: `AutofocusHook.__init__()` rejected the unsupported `method` argument. |
| Script lines 457–459 | Successful built-in autofocus call is `NOT EMITTED`, followed by `raise RuntimeError`: `hooked acquisition ('autofocus_per_position'): inlining HookBase would import microclaw safety and hook decisions`. |
| Script lines 470–472 | Successful OughtaFocus call receives the same explicit refusal for `autofocus_mm_plugin`. |
| History lines 24–25 | OughtaFocus call `toolu_017TXJCzN1xmcjzzqZTHe4Yp` completed nine positions and nine frames. |
| History lines 34–36 | Agent calls the successful runs “SKIPPED”, attributes the defect to Andor/EMU, writes a manual stand-in, and acknowledges it is untested. No read-back tool call appears between export and that diagnosis. |
| Script lines 18–433 vs. body | 419 of the file's 478 lines are the stage-move contract, and **nothing calls any of it**: `settle_stage_move`, `settle_xy_move` and both start-position readers appear zero times after `core = Core()`. `stage_moves` is gated on `autofocus_used`, which is set by `hook_strategy == 'autofocus_per_position'` — the hook whose emitter then refuses. The helper block is emitted *by* the refused call. |

The successful built-in call is `toolu_018Yq2fYpP2BxKmFQRyuwvzh`.
Both acquisitions use explicit nine-position coordinates, `protocol='timelapse'`,
one frame per position, interval zero and `laser_slot=3`. Their hooks are
`autofocus_per_position` with range 15 µm and step 0.5 µm, and
`autofocus_mm_plugin` with `plugin_name='OughtaFocus'`.

The exported file contains helper definitions as well as the laser write; its
size is not evidence of workflow coverage. Assuming imports, connection and
setup succeed, execution writes laser power and then raises at the first
successful acquisition's refusal. It does not silently finish after setup.

## Code cause and reporting cause

In [tools.py](../microclaw/tools.py), `_emit_multiposition` looks up the hook
class and refuses any registered hook without `_observation_only=True`.
Both [AutofocusHook and MMAutofocusPluginHook](../microclaw/hooks.py) fall into
that branch. The guard runs before acquisition rendering, independent of rig,
coordinates, dataset paths or the scientific success of the recorded call.
The artifact's refusal text matches the current implementation. The bundle
does not establish the exact source revision deployed on M2.

`export_session_script` catches `CannotEmit` and writes a comment plus a raising
statement. This correctly avoids pretending to reproduce unsupported behavior.
However, its result omits a structured list of refused calls and retains the
unqualified success status. `skipped_failed_calls` describes the invalid first
attempt only. The agent conflated that field with the two unsupported calls
and repeated its prior knowledge-base attribution without inspecting the file.

This is an intentionally enforced limitation with insufficient capability
coverage: existing export tests assert refusal for decision hooks and for the
`run_multiposition_with_autofocus` compatibility route.

**Much of the required implementation already exists, but integration is missing.**
`_adaptive_hook_export` already renders `autofocus_per_position`: it inlines
`AutofocusHook` with `inspect.getsource`, injects `'ctrl': mm, 'guard': guard`
for every precoded hook whose `__init__` takes them, and refuses **only**
`mm_plugin_analyzer` and `autofocus_mm_plugin`. `_analysis_source(
include_autofocus=True)` already inlines `coarse_then_fine_autofocus` and its
whole closure, and `_export_guard_source` already renders a portable
`check_z` over recorded bounds. `_emit_multiposition` calls `_adaptive_hook_export`
itself — for observation-only hooks — and then hands it a stub guard,
`SimpleNamespace(analysis_min_snr=None)`. That stub lacks `check_z`, so it
cannot support a Z-moving hook. There is a second independent gap:
`_emit_multiposition` attaches only `image_process_fn` to `Acquisition`
(`tools.py:763`). Autofocus requires `post_hardware_hook_fn`; a guard swap and
gate removal would therefore emit a run that never autofocuses. Reservation
binding and the other live callback contracts must also survive export.

That gap is specific, not open-ended, and it splits the four refused built-ins
**3 + 1**. `focus_feedback`, `intensity_adaptive` and `position_filter` define
`image_process_fn` only, which the existing wiring already supplies correctly;
neither of the latter two enqueues events, so design/24's no-op `put` is not in
play. `autofocus_per_position` defines `post_hardware_hook_fn` only — so it is
the one hook the current wiring silently drops, and it is the subject of this
document. Constructing a hook the acquisition never calls is worse than the
refusal it would replace: the run reports success and the frames are unfocused.

The wiring itself is reuse, not new code. `_emit_adaptive` already emits the
`getattr(hook, ..., None)` triple over all three callbacks (`tools.py:1738–1740`
and `1830–1832`), and the live runner selects the same three by `hasattr`
(`tools.py:4384–4389`). Emitted and live callback selection must be read off
each other, not written twice.

The global `adaptive_used` predicate (`tools.py:1978`) excludes hooked
multiposition calls, so this session receives neither global hook-runtime setup
nor safety limits. A separate tool-name-based loop (`tools.py:2041`) repeats the
same condition to attach `_export_safety_limits` to recorded parameters;
changing the first predicate alone leaves the limits computed and unattached,
and the renderer then refuses with "the record carries no export-time safety
limits".
Meanwhile the observation-only branch already inlines `_adaptive_runner_source`
and `_portable_log_path_source` locally. Broadening global inclusion without
consolidating this branch would duplicate helpers. `autofocus_used` adds the
unused stage-move block even though the autofocus emitter refuses.

Reuse `_adaptive_hook_export`, `_export_guard_source` and the existing hooked
runner integration rather than copying their logic. `_emit_adaptive` already
handles position lists and is a candidate route, but matching fixed-plan
ordering, callbacks, budgeting and logs must be demonstrated. Its plugin-hook
refusal remains a separate 80c capability gap. Source availability alone does
not establish faithful execution for any of these hooks.

## Why the manual file is not a fix

The supplied `beads_af_workflow_manual_20260905.py` implements a uniform sweep
and a custom finite-difference metric. The live `AutofocusHook` calls the shared
coarse-then-fine autofocus algorithm, including convergence detection and
restoration on a flat focus curve. The manual script always chooses a maximum;
it cannot reproduce the rejected corner that mattered to this experiment.
It also uses one entry Z for every sweep, while the live hook centers each
sweep on the current Z after the event's hardware movement.

The manual loop moves hardware outside the acquisition callback and submits
repeated one-timepoint events without position axes or a per-submission
completion barrier. It omits the live passive plugin Z guard and hook logs, does
not reproduce the slot-3 preflight, applies no Z bound anywhere in its sweep, and
its Studio API is explicitly unverified.

The engine contracts distinguish a missing synchronization guarantee from a
definite indexing defect:

- `core.set_xy_position(...)` runs inside the `with Acquisition(...)` block
  between `acq.acquire(...)` calls. **`acquire()` only submits**; completion is
  awaited in `__exit__`. There is no barrier ensuring earlier frames finish
  before the next stage move. Motion can overlap acquisition; establishing
  actual overlap in a particular run requires timing evidence.
- Every submission is `multi_d_acquisition_events(num_time_points=1)`, so all
  nine events carry the identical axes `{'time': 0}`. NDTiff is indexed by axes.
  Nine frames collide on one index; this is certain, not a risk.

A third is worth naming because the repo has already paid for it: the file's
private `tenengrad` is an unnormalised finite-difference metric, not
`image_analysis.tenengrad`. design/36 spent a rig gate establishing that the
**normaliser** was what made the focus metric minimise at focus. A hand-written
metric is exactly the substitution that defect came from.

The remaining differences are source-level and unmeasured. The bundle contains
manual-run dataset directories, but their presence does not prove equivalence or
establish which revision of the manual file produced them.

## Proposed implementation

### 80a — Make incomplete exports explicit

Accumulate `not_emitted_calls` with tool-use ID, tool name and reason wherever
the exporter emits a refusal. Return `complete: false` and an explicit
incomplete-export status whenever a selected call cannot be rendered. Keep
failed-no-effect skips, user exclusions and non-hardware calls separate from
unsupported successful or partially completed calls. Preserve existing result
fields and valid-Python refusal artifacts for compatibility.

Add an early refusal summary before `Core()` and any hardware effects when
the export is incomplete; retain the individual step markers for review.
An incomplete full-session export should not modify the microscope before
announcing that it cannot run. Explicit selection can still export a supported
subset, with the existing dependency warning.

Two points to pin before implementing:

- **Print, do not raise, at the top.** "Should not modify the microscope before
  announcing" is satisfied by a print; a top-of-file raise would also destroy
  the legitimate case where the refusal is the last step and ten supported ones
  precede it. The in-place `raise RuntimeError` stays where it is. This is the
  same operator decision as the envelope print (2026-08-17): the disclosure
  goes in the output, and running the script is the consent.
- **Fold into the existing status branch.** `export_session_script` already
  degrades its `status` string for `emitted == 0 and skipped_failed_calls` and
  for an empty selection. `not_emitted_calls` belongs in that same branch, not
  in a parallel one. `params["_tool_use_id"]` is in scope at every `refuse()`
  call site, so carrying the id costs one argument.

Update tool guidance and agent reporting to use these fields, name the exact
missing capability, and inspect the artifact when diagnosis needs source
detail. Do not label an exporter refusal as a hardware defect or hand-write an
algorithm substitute as an export recovery. Correct the rig knowledge-base
entry after locating its source; this investigation has not edited that entry.

### 80b — Give the hooked multiposition path the hook runtime it already has

Reuse the existing hook runtime and source extraction. The implementation
must address all of the following together:

1. Replace the scattered tool-name checks with shared capability decisions for
   runtime inclusion and attachment of `_export_safety_limits` to each call —
   one decision consumed at both sites, not the same condition written twice.
   Preserve existing adaptive routes, including any implicit hook behavior;
   checking only explicit `hook_strategy` or `hook_action_plan` is insufficient
   as a general replacement. The concrete counter-example is the deprecated
   `run_multiposition_with_autofocus`: its recorded params carry **no**
   `hook_strategy`, because `_emit_multiposition_with_autofocus` synthesizes
   `hook_strategy="autofocus_per_position"` into a forwarded `RecordedParams`
   (`tools.py:479`). A predicate reading the recorded field would answer False
   for a whole session that used the wrapper and emit it without the hook
   runtime. Rename `adaptive_used` to reflect the capability it actually
   selects.
2. Consolidate the observation branch's local helper inclusion with global
   inclusion, so each required helper is emitted once. Base dependencies on
   rendered capabilities, including refusals, so unsupported calls do not pull
   in unused runtime blocks. Changing the predicate alone does not fix this.
3. Supply `_export_guard_source(limits)` where the hook requires it, preserving
   the observation-hook calibration-threshold refusal and avoiding new bound
   requirements for observation-only calls. Replace the blanket
   `_observation_only` gate only when the supported hook's complete execution
   contract is available.
4. Wire `post_hardware_hook_fn` as well as `image_process_fn`, and preserve
   reservation binding, accounting and log behavior. Take the callback set from
   the same `getattr`-triple `_emit_adaptive` and the live runner already use;
   do not enumerate callbacks per hook, which is how the one-callback
   assumption got here. Reuse the existing hooked runner integration, through
   `_emit_adaptive` where equivalent or shared integration helpers where
   fixed-plan semantics differ. Callback wiring is mandatory, not an optional
   change conditioned only on acquisition order.
   **The hookless multiposition export must remain byte-identical.**

Target all four currently refused built-ins: `autofocus_per_position`,
`focus_feedback`, `intensity_adaptive` and `position_filter`.
`_adaptive_hook_export` already extracts their source, which makes shared
support appropriate. Each still requires behavioral coverage for its callback,
state changes, filtering and logs. Do not remove a refusal solely because the
class can be rendered; retain a precise refusal until its runtime contract is
implemented and verified.

Preserve event positions and axes, acquisition order, nominal-Z behavior,
post-hardware callback timing, exact range/step/settling defaults, convergence
and restoration, frame budgeting (`bind_reservation`) and per-position logs.
Preserve channel and laser preflight effects through recorded effects or shared
portable logic. Record the bounds and resolved prerequisites the standalone
program needs; a record lacking them refuses precisely. Composed and saved
hooks stay refused. **An emitted step must not be stricter than the tool it
reproduces** — check the settlement path the emitted sweep takes against the
live one.

The one thing this must not do is re-write hook or autofocus logic in the
emitter. Everything here is `inspect.getsource` of the code that ran; a
hand-copied sweep is the same substitution the manual file made.

### 80c — Supply the standalone plugin capability

Reuse the live accessor:
`PluginAccess.get_autofocus_method` in `controller.py:711`: `Studio().get_autofocus_manager()`,
validate `plugin_name` against `get_all_autofocus_methods()` drained through
`_drain_java_iterable`, `set_autofocus_method_by_name`, return
`get_autofocus_method()`. Inline **that function and `_drain_java_iterable`**
with `inspect.getsource` inside a narrow standalone adapter. The method uses
`self._studio`: the adapter must supply the Studio connection and expose the
accessor through `mm.plugins`, as the live hook expects. Source extraction
alone does not provide that wiring. The drain is the
design/59a contract (a bridge collection is not Python-iterable; `list()` over
one raises on every rig and works against every fake), and the manual file's
guessed accessor skips the validation that turns a wrong name into a message
instead of a Java `IllegalArgumentException`.

The portable guard also needs `check_plugin_motion`, recorded the way
`authorize_property_write` already is: the live run's authorization is the
authority, pinned to the one plugin name, never widened to another.

Preserve plugin selection, exception/log behavior, passive Z checking and abort
on unsafe Z. Do not add a compensating Z movement after the plugin. Never
substitute the built-in sweep's 15 µm/0.5 µm settings for the plugin's.

On the settings snapshot: before settling for a declared external precondition,
check on a rig whether the returned `AutofocusMethod` exposes
`get_property_names()` / `get_property_value()` over the bridge. If it does, the
settings are recordable and the emitted script can print them in its envelope;
if it does not, say so in the doc and declare the precondition. Either way this
is the one exported script whose behaviour is **not** determined by its source,
and per the 2026-08-17 operator decision the script must print that in its
envelope — the print is the only disclosure there is.

Closing 80c means CLAUDE.md's export section changes: it currently records
`autofocus_mm_plugin` as a legitimate permanent `CannotEmit`. Note also that its
stated reason there — "take `ctrl`/`guard`" — is already stale, since
`_adaptive_hook_export` injects both for every precoded hook including
`AutofocusHook`. The real reason is the Studio accessor. Fix that sentence in
the same block, per step 10.

### Sequencing

80a is off-rig, small, and fixes the half of this incident that did damage: the
refusal machinery behaved correctly and the *report* was what let an agent blame
the hardware and hand over an untested substitute. Ship it alone and first; it
is worth having even if 80b and 80c never land.

80b can be implemented and tested offline, followed by a demo-machine replay.
It includes capability and dependency consolidation, guard integration, callback
wiring and behavioral checks for all four built-ins. 80c needs Micro-Manager with
OughtaFocus installed and is the only part that must wait for instrument time.
Do not bundle 80c into 80b's block.

## Validation and acceptance

1. Build a compact fixture from this history retaining the failed attempt,
   laser write and both successful calls/results. First reproduce one emitted
   call and two refusals offline, without connecting to hardware.
2. Test structured completeness, IDs and reasons for unsupported, failed,
   partial, excluded and non-hardware calls. Execute an incomplete artifact
   against a fake bridge and assert the incomplete-export summary is printed
   before Core connection or hardware writes. Supported preceding steps may
   execute; the in-place refusal must raise before the unsupported step, and
   no subsequent step may execute. Cover both an early and a final refusal.
3. Execute supported generated scripts against a recording fake acquisition
   backend, not just `ast.parse` or string checks. Require nine uniquely indexed
   final frames and nine autofocus callbacks/log entries per grid, with XY
   movement before autofocus and capture after it. The fake `Acquisition` must
   **dispatch** rather than be subclassable — an ordinary stand-in is the fake
   that hid block 75b's injected hang — and it must fire `image_saved_fn` from
   `__exit__`, not from `acquire()`, or it encodes the assumption the eighth
   engine contract exists to correct. Add two cheap regressions here: a hookless
   multiposition export is byte-identical before and after 80b, and this
   session's refused export omits unused stage-move helpers. Once autofocus
   emits, require its needed helpers exactly once; do not assert their absence
   merely because they were unused in the original incomplete export.
4. Compare built-in live/export behavior on a peaked field and flat field:
   sweep order, final Z, convergence, restoration, counts and log content.
   Exercise bounds and settlement failures. Verify asynchronous submission
   cannot race a subsequent manual stage move. Add live/export comparisons for
   focus-feedback state and corrections, intensity-driven exposure changes,
   and position-filter discards, including per-hook logs and failure paths.
5. Exercise plugin success, exception and out-of-bounds returns with a fake
   Studio adapter. Require matching logs and abort behavior, no compensating
   motion, and precise refusal for missing prerequisites.
6. Run the focused export tests, then an authorized demo/M2 replay into new
   output names. Check saved frame metadata and hook logs. Verify the real
   Studio bridge separately; offline tests cannot establish that connection.

Completion means this session's supported full export contains the power write
and both nine-position acquisitions, correctly skips the invalid first attempt,
imports nothing from Microclaw, and preserves the observed autofocus contracts.
Until both capability blocks pass, the tool must clearly report the remaining
refusal rather than claim a faithful full-session export.

## Block 80a — implementation checklist

Off-rig. `microclaw/tools.py` `export_session_script`, `microclaw/tools_schema.py`,
`tests/test_session_script_export.py`. No emitter gains a new capability in this
block: 80a changes what the *result* says and what the *artifact announces*, and
nothing else. The three refused hooks stay refused.

1. **`refuse()` records the call it refused.** Every `refuse()` site inside
   `export_session_script` accumulates `{"tool_use_id", "tool", "reason"}` into
   `not_emitted_calls`. `params["_tool_use_id"]` is in scope at all of them, so
   this is one extra argument, not a new pass. The three sites are: no emitter /
   `@refuses` reason, a `partial` recorded outcome, and a renderer's
   `CannotEmit`. The top-level `ast.parse` failure is not one of them — it raises
   and writes no file, which stays.
2. **The categories stay distinct.** `skipped_failed_calls` keeps meaning *the
   recorded call completed nothing*; excluded-by-selection and
   `@emits_nothing` calls appear in neither list. A `partial` outcome is an
   unsupported/partially-completed call and belongs in `not_emitted_calls`, not
   in `skipped_failed_calls`. The incident's whole reporting defect is one list
   being read as the other, so a test must assert the failed first attempt lands
   in `skipped_failed_calls` **and not** in `not_emitted_calls`, and both
   successful hooked calls the reverse.
3. **`complete` is always in the result** — `true` when `not_emitted_calls` is
   empty, `false` otherwise — and the `status` string says so when it is false.
   Fold this into the existing status branch that already degrades `status` for
   `emitted == 0 and skipped_failed_calls` and for an empty selection; do not add
   a parallel branch. The incomplete status must name the count and point at
   `not_emitted_calls`, and it must win over the two existing degraded strings
   when both apply.
4. **The artifact announces its own incompleteness before it touches the
   microscope.** When `not_emitted_calls` is non-empty, emit a summary at the top
   of the file — before `core = Core()` and before any hardware step — that
   **prints**, naming each refused tool, its id and its reason. It does not raise:
   a top-of-file raise would destroy the legitimate case of ten supported steps
   ahead of one refusal, and the disclosure-in-output/consent-by-running decision
   of 2026-08-17 already settles which of the two this is. The in-place
   `# NOT EMITTED` comment and `raise RuntimeError` stay exactly where they are.
5. **Existing result fields and artifact shape are preserved.** `status`,
   `output_path`, `emitted_calls`, `emitted_tool_use_ids`, `recorded_calls`,
   `artifact`, `skipped_failed_calls`, `selection_warning` all keep their current
   names and meanings; the file still parses under `ast.parse` before it is
   written.
6. **Tool guidance names the fields and forbids the misdiagnosis.** The
   `export_session_script` description says a refusal is reported in
   `not_emitted_calls` with `complete: false`, that the named reason is the exact
   missing export capability, and that it is a Microclaw capability gap — never a
   camera, rig or hardware defect — so a report must quote the reason rather than
   attribute it. The existing `write_text_file` guidance about hand-written
   stand-ins stays and is not duplicated; add only the pointer that a diagnosis
   needing source detail should read the emitted artifact.
7. **Fixture and offline reproduction.** Build a compact fixture from the real
   session (`tests/fixtures/80a-beads-autofocus-session.json`, already in the tree —
   a pruned copy of the real history holding only these four calls and their
   results, verified by the coordinator to reproduce the incident exactly)
   holding the four calls that matter: the failed `run_multiposition_acquisition`
   (`toolu_012yhPfTp3TStsngUtGqhKhp`), the `set_device_property` laser write
   (`toolu_01F9qtEFXVpd5dE6Q1bJL9AD`), and the two successful hooked
   `run_multiposition_acquisition` calls
   (`toolu_018Yq2fYpP2BxKmFQRyuwvzh`, built-in `autofocus_per_position`, and
   `toolu_017TXJCzN1xmcjzzqZTHe4Yp`, `autofocus_mm_plugin`/OughtaFocus). Assert
   the pre-80b truth: one emitted call, one skipped-failed call, two
   `not_emitted_calls` carrying those two ids and the two current refusal
   reasons, `complete: false`.

   Measured by the coordinator on `caa3554` with this fixture, so these are the
   pre-fix numbers the block starts from, not estimates: 459-line artifact,
   `status` `"Session script exported."`, `emitted_calls` 1, one
   `skipped_failed_calls` entry, and two `# NOT EMITTED` lines whose reasons are
   `hooked acquisition ('autofocus_per_position'): inlining HookBase would import
   microclaw safety and hook decisions` and the same sentence for
   `autofocus_mm_plugin`. Nothing in the returned dict mentions either.
8. **Execute the incomplete artifact, do not grep it.** `exec` the emitted source
   against a fake bridge and assert: the summary is printed **before** `Core()` is
   constructed and before any hardware write; supported steps preceding the
   refusal do execute; the refusal raises; **no step after it executes**. Cover
   both an early refusal (nothing supported ran) and a final refusal (supported
   steps ran first). A string search over the source is not this test.
9. **Watch each new test fail** on the pre-fix tree, for its stated reason, and
   report the exact failure text. `test_suite_integrity` and every existing
   export test stay green; the emitted *body* of a session with no refusals is
   byte-identical before and after this block.

**Not in 80a, deliberately.** The unused stage-move helper block (419 of the
incident script's 478 lines) is dependency closure and belongs to 80b item 2.
`autofocus_used` keeps its current meaning here.

### 80a gate

Off-rig apart from one operator lookup. Scored from the artifact, not the
verdict: the coordinator re-exports the fixture, reads the emitted file, and
checks `not_emitted_calls` against the `# NOT EMITTED` lines in it. The one
rig-side item is the knowledge base: the agent claimed "the known Andor/EMU
export defect already recorded in your knowledge base" (history line 36). The
operator greps M2's `~/.microclaw/knowledge.yaml` for it. **If no such entry
exists the claim was fabricated**, which is a different finding from a stale
entry and is worth recording either way; only a found entry gets corrected.

## Run ledger

Baseline before the notebook: `main` `caa3554`, coordinator-run suite
**2908 passed / 99 skipped / 2 warnings** in 203.1 s
(`.venv/bin/python -m pytest -q`, 2026-09-06). The two warnings are the benign
`phase_cross_correlation` `UserWarning` from
`test_featureless_field_returns_error_not_garbage` doing its job.

The coordinator reproduced the incident off-rig before assigning 80a, against
`caa3554` and the committed fixture: 459-line artifact, `emitted_calls` 1,
`status` `"Session script exported."`, one `skipped_failed_calls` entry, two
`# NOT EMITTED` lines, and **no field in the returned dict naming either
refusal**. 439 of the 459 lines are the unused stage-move contract, which is
80b's dependency-closure item, not 80a's.

| block | branch | start | implementation | gate | merge |
|---|---|---|---|---|---|
| 80a | `design80/explicit-incomplete-export` | `25ee645` (2026-09-06), worktree `../microclaw-80a`, baseline **2908 passed / 99 skipped / 4 warnings** measured *in the runner's own venv* — the primary checkout reports 2, and the extra pair is a `starlette`/`anyio` `DeprecationWarning` from this venv's freshly resolved `[serve]` extras, checked rather than assumed before being handed over. | `3f4e9f6` (UNREVIEWED) + `fa8aed4` + `6cd9c28`. **The Codex start turn hit its provider usage limit after its edits had landed and before it produced any report**, so `3f4e9f6` is preserved per the workflow with a message saying plainly that nothing about it had been reviewed. It had, by luck of timing, already run its own pre-fix check — but **it had never run the full suite, and the suite was red**: the new fixture read did not name its encoding and `test_suite_integrity::test_test_text_io_always_names_its_encoding` failed. That is the finding the workflow's *re-run the suite yourself* step exists for. **Four findings, all fixed by the coordinator on the branch** (step 7, sized to the finding; Codex was out of credits until 14:59 and every one was small). The substantive one: a refusal reason reached both `not_emitted_calls` and the artifact's printed disclosure **unfolded**, while `skipped_failed_calls` had folded through `one_line()` since 2026-08-17 — and the partial-outcome reason is built from recorded per-position errors, so a Java stack trace really does splice newlines through the one disclosure an exported script has. Demonstrated before being fixed. Then `1 calls` in both the status and that disclosure; and `skipped_failed_calls` carrying no `tool_use_id` while `not_emitted_calls` did — **conflating those two lists is the entire incident**, and this session names `run_multiposition_acquisition` three times, so without an id an entry cannot be lined up against `recorded_calls` at all. Both lists now have one shape. **Watch-it-fail was reproduced independently, never taken from the runner's file**: all seven start-turn tests on `25ee645` (`KeyError: 'not_emitted_calls'`, the old status string, and `['Core', 10, 20]` — `Core` constructed with no disclosure ahead of it), and the new folding test on `3f4e9f6`. Coordinator suite **2916 passed / 99 skipped**, reconciling as 2908 + 7 + 1, nothing else moved. Scored from the artifact: the fixture now returns `complete: false`, a status naming 2 calls, and two `not_emitted_calls` with ids — while the failed first attempt sits alone in `skipped_failed_calls` with its own id. | PENDING — `design/80-block80a-gate.md`. Deliberately small: the block adds no emitter capability, so there is no acquisition to drive and everything deterministic was settled off-rig. Two things the suite cannot settle. (a) M2's `~/.microclaw/knowledge.yaml` — the assistant cited *"the known Andor/EMU export defect already recorded in your knowledge base"*; **no such defect exists**, so either the entry is wrong or the citation was fabricated, and an empty match set is the result rather than a failed step. (b) A live-model replay of the report itself, in the shape of `design/77-block77a-replay.py` — the block is a reporting fix, so nothing else measures it. **Not started and not priced by guess**: 77a's equivalent overran its authorisation at $12.13 against $10. Unlike 77a's, this arm's control is known to fire, because the real session produced the hardware attribution verbatim. | |
| 80b | — | not started; follows 80a | | | |
| 80c | — | not started; needs Micro-Manager with OughtaFocus, so it waits on instrument time | | | |
