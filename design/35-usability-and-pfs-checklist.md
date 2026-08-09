# Checklist v2 — get operators back on their rigs, then finish the fun blocks

Created: 2026-07-30. **This file supersedes
`design/26-29-32-33-implementation-checklist.md` as the active implementation
order.** That file is not deleted and is not wrong; it is closed out, and every
item it left unfinished is carried forward here (see "Carried-forward register"
at the end). When the two disagree about what is next, this file wins. When
either disagrees with a *design*, stop and reconcile the design first.

## Why the order changed

The previous checklist ordered work by safety-foundation dependency. That was
right for building the guarantees and it is why they hold. But the cumulative
effect of Blocks 1, 2, 3, 3b, 5, 7b, and 14 Phase 2 is that
`safety_config.yaml` is now a strict, versioned, fail-closed document that a
human must author correctly *before microclaw will start at all* — and the only
authoring tool is `microclaw init`, which copies a file of admittedly fictional
limits and opens it in an editor (`microclaw/__main__.py:101`). Every block that
tightened the gate widened the gap between "microclaw is installed" and
"microclaw runs on my microscope."

So the reordering principle is now: **shortest path to an operator producing a
working config on their own rig**, then the Nikon blocker, then the deferred
feature blocks.

Three things follow from that:

1. **Block 14 Phase 5 (`design33/first-launch-setup`) is promoted to the
   headline deliverable.** It is the only planned work that closes the authoring
   gap, and both source documents already say it is unblocked
   (old checklist `:1338`; design/33 `:439`).
2. **Three small blocks land before it**, because each is either a Phase 5
   prerequisite or an input a Phase 5 implementer would otherwise get wrong.
   None of them is large.
3. **The Nikon PFS work (design/34) is a separate track** whose first step costs
   nothing and can start today. It is sequenced so that the one probe that could
   dissolve most of the design runs before any of it is built.
   **That sequencing did not work** (2026-08-05): the probe was authored,
   shipped, and never run, because a remote operator with less coding experience
   could not run it — the constraint stated two sections below. Five ordinary
   microclaw sessions supplied better evidence than the probe would have. The
   lesson for any future remote-evidence plan is in
   `design/40-pfs-five-sessions.md`: **ask for the rig's normal work, not for a
   script.**

Blocks 11 and 12 stay where they are in the order — after the usability and
Nikon tracks — not because they matter less, but because they each need
something microclaw does not currently make easy: an operator running a real
workflow on a real rig. That is exactly what the usability track restores.

## The Nikon rig is remote, and its operator is not an implementer

This is a hard constraint on the whole Nikon track, not a detail.

The Nikon is run by a different person, far away, with substantially less coding
experience. They cannot implement blocks 6–8, cannot judge whether a probe result
is valid, and cannot debug a script that fails halfway. **Everything they run
must be authored here, shipped complete, and self-validating.** What comes back
is spike output and session histories, and that is the only channel.

Three consequences reorder the work:

1. **The long pole is round-trip latency to a human, not implementation time.**
   Each shipment is days. So the evidence kit is authored and shipped *first*,
   in parallel with everything else, and it is designed to answer as much as one
   shipment possibly can. Assume round trip #2 will happen; design so that it is
   only the motion probes.
2. **The probes must not depend on microclaw.** Every existing spike in `design/`
   imports only `pycromanager` plus stdlib (`design/29-mm-pixel-affine-probe.py`,
   `design/31-native-position-file-spike.py`). Probe 0 needs a Micro-Manager ZMQ
   connection and nothing else. That fully decouples the Nikon evidence from
   Track A: **they can run it today, whatever state their safety config is in.**
3. **They separately need a microclaw that starts.** They last ran a session at
   `722184a` (2026-07-16). Every config-tightening block since then landed after
   that commit, so their install may now refuse to start on a file that used to
   work — the usability complaint in its sharpest form, on the person least able
   to resolve it. That needs a stopgap now, not after block 4.

So blocks 0a and 0b below are assigned before Track A starts. They are shipped as
soon as both are ready, and Track A proceeds during their implementation and
while the kit is in flight.

## How to use this file

**The process lives in `CLAUDE.md` §"The block workflow", which is
authoritative.** Read it before assigning or reviewing anything. It is in
`CLAUDE.md` rather than here because it loads automatically every session,
whereas this file has to be opened — and the workflow was repeatedly
mis-executed across sessions while it lived only in checklists and memory. If
this file and `CLAUDE.md` ever disagree about *process*, `CLAUDE.md` wins and
this file gets corrected. This file remains authoritative for *what* the blocks
are, their order, their gates, and the ledger.

What this file adds, specific to these blocks:

- One **coordinator** owns this file; one **implementation agent** owns one
  implementation branch at a time, in its own worktree. Track 0 is the explicit
  exception to numbered order: 0a and 0b may run concurrently in separate
  worktrees, and block 1 may start once both have implementers assigned. Block
  0c is coordination, not an implementation slot, so Track A continues while the
  shipped kit is in flight.
- Do not combine blocks because they touch the same files. The branch boundaries
  are safety and rollback boundaries.
- **Coordinator edits go on a branch too — including ledger and checklist
  updates.** "The coordinator alone edits the run ledger" says *who* may edit
  this file, not that those edits may bypass branch-first. Commits `3914735`
  and `fab00fb` (2026-07-30) went straight to `main` and should not have; they
  are left in place because rewriting pushed history is worse than the
  irregularity.
- **Commit coordinator edits to this file before assigning the next block.**
  Process failure caught by block 1's implementer, 2026-07-30: corrections and
  ledger rows lived only as uncommitted working-tree changes on `main`, so an
  agent in a worktree branched from the pre-edit commit correctly reported that
  the items it was told to implement did not exist. Worktrees see committed
  history, not the coordinator's editor buffer.
- **A block's rig-gate runbook belongs on the block's branch.** Block 2 kept its
  runbook on `main` and Block 4 initially copied that; both were wrong. The user
  checks the branch out on the rig, so the runbook must be there. The
  self-reference this was thought to avoid — a commit staling the runbook's own
  recorded hash — is solved by pinning with `git merge-base --is-ancestor`
  (`cf13c1a`), not by branch placement. Coordinator bookkeeping stays on `main`.

### State at the 2026-08-05 session boundary — read this before assigning anything

Refreshed after Track A closed (block 5b), design/39 merged, and **Track B was
rescoped against the Nikon sessions** (design/40). Recorded because these facts
otherwise exist only in a closed conversation, and step 9's whole point is that
a cold session resumes from the remote alone.

- **Block 6a is pushed and awaiting its rig gate** (branch
  `design34/focus-system-authorization` at `4994f3e`, assigned 2026-08-05 from
  `f41c89a`). Two review rounds were returned before it was shippable.
- **Block 41a is MERGED** (`1fb284d`, 2026-08-05), run concurrently with 6a's rig
  gate in its own worktree; design gate done, ledger closed, worktree and branch
  removed. `main` measures **1511 passed / 99 skipped / 1610 collected**. It had
  no rig surface, which is why it could close while the Nikon round trip is in
  flight — **6a must merge `main` before its own merge.**
- **Track D's next blocks are 13 and 41b**, which may now run concurrently with
  each other and with 6a, in separate worktrees (13 is `tools.py`, 41b is the
  emitters plus `image_analysis.py`). 41c follows 41b.
  **Both were assigned 2026-08-05 from `03dcea0`**, in worktrees
  `../microclaw-13` and `../microclaw-41b`. Three branches are now in flight at
  once (6a, 13, 41b); 6a is the only one whose next step belongs to the rig.
  **As of 2026-08-06: block 13 is pushed and awaiting its gate** (runbook
  `design/40-block13-rig-gate.md`, on the branch); **41b is in review after two
  rounds** and is not pushed. Both blocks were implemented by an operator-driven
  codex runner rather than by an Agent-tool runner — see the note on step 2 in
  `CLAUDE.md`, which now says the coordinator writes the prompt and *offers*.
- **Two rounds of review on each of 13 and 41b found real defects behind a green
  suite, both times.** The pattern is worth keeping: every reported suite count
  and collected-ID diff was accurate, and the defects were invisible in them.
  Block 13 round 1 shipped a `rank_hook_log` fix whose test invented a schema
  (`microclaw.hook-action/v1`) that exists nowhere, so the fix admitted `None`
  and the original defect survived untouched; it was caught only by rebuilding
  the log with the real writers. **Judge a fix by driving the real producer, not
  by reading its test.** The coordinator's own first repro of the same area was
  wrong for the mirror-image reason — a hand-built record that omitted
  `snr_valid` — so this cuts both ways.
- **Baseline re-measured 2026-08-05 at `2d66045`** — the boundary-refresh merge —
  and it is unchanged from `c799063`: **1511 passed / 99 skipped / 3 expected
  warnings, 1610 collected.** The 3 warnings are one
  `StarletteDeprecationWarning` plus two `phase_cross_correlation` empty-image
  `UserWarning`s from `microclaw/tools.py:1739`–`:1740`. Blocks 13 and 41b are
  both compared against this.
- **All three review rounds found the same shape of defect, and none of them
  were in the product code after round 1.** Round 1: five existing tests were
  silently uncollected because a test was defined at column 0 inside a class
  body — and the added/dropped counts cancelled exactly, so the "judge by
  collected total" rule did not catch it either. Rounds 1 and 2: four gate
  criteria that could not fail or could not pass — a PFS-write check satisfied
  by a *read* (it passed on the pre-fix baseline session), OR'd `Select-String`
  patterns counted as a sum (read 9 on the operator's own correct profile
  against an expected 2), a `position_um` argument that does not exist
  (`move_named_stage` takes `um`, so it read 0 on any run), and a threshold of 4
  against a string only 3 call sites emit. **Every one was found by running the
  pattern over captured evidence, and none was visible by reading it.** See the
  standing rule proposed for the post-merge design gate below.
- **`main` is at `c799063`, measured 1511 passed / 99 skipped / 3 expected
  warnings on macOS (2026-08-05), 1610 collected.** The history of that number:
  1591 collected when design/39 landed, 1393/99 at `d7c1d13`, and Windows
  measured 1377/115 there — the same collection, with sixteen
  platform-conditional tests skipping instead of passing. **Judge a suite by
  failures and collected total, not the passed count**, and re-measure rather
  than trusting this line; a wildly different *total* means something else
  changed. When reviewing a branch, diff **collected test IDs** against its start
  commit — on block 6a five tests silently dropped out of collection while the
  totals cancelled exactly, so the totals rule alone did not catch it.
- **Track A is fully closed** — blocks 1, 2, 3, 4, 4b, 4c, 4d, 4e, 4f, 4g, 4h,
  5, 5b are all merged, gated, ledger-closed, design-reconciled, and written up
  in `design/prompts.md`, with their branches deleted locally *and* on `origin`.
  Track A has zero open checklist items, verified item by item.
- **The one open branch is 6a's**, above. Everything else on `origin` —
  `port-to-jpype-acqj` and two `florian/*` — belongs to no checklist here.
  `git log --oneline origin/main..main` is empty.
- **Track B order from here is 6 and 7a** (both depend on 6a merging), then 7b
  and 8. Track C last.
- **Track D order is 41a, 13, 41b, 41c**, plus **41d** (added 2026-08-06 from
  41b's M5 gate; `safety.py`, disjoint from everything else in the track, may run
  concurrently). From the M5 smiley run
  (`design/41-smiley-session-findings.md`), which also folded two findings into
  block 13. **41a is merged**, so 13 and 41b are both assignable now and may run
  concurrently with each other and with 6a — 13 is `tools.py`, 41b is the
  emitters plus `image_analysis.py`. 41c follows 41b.

### State at the 2026-08-07 session boundary — read this before assigning anything

Step 9's whole point is that a cold session resumes from the remote alone. `main`
is `ef91216`; `git log --oneline origin/main..main` is empty; no uncommitted
files in either worktree; the only branches on `origin` besides `main` are `6a`,
two `florian/*`, and `port-to-jpype-acqj`.

- **Block 13 is CLOSED.** Merged `d24e721`, gate `40-block13-m5`, design gate
  `e5ad254`. Its G3 (transmitted light) was **not runnable on M5** and is in the
  carried-forward register, as is its unexercised position-list rollback path and
  the post-gate `f4e98c6`, which is ungated.
- **Block 41b is CLOSED.** Merged `b1aa55e`, gate `41-block4b-m5-run-round4` +
  `41-block41b-m5-round5`, design gate in `CLAUDE.md` and design/41 F1. Five rig
  rounds. Two commits landed post-gate and are **ungated**: `b6cc7a2` and
  `5af0fc6`. **41c is now unblocked.**
- **Block 41d is CLOSED.** Merged `f864a33`, gate `gate41d-m5`, design gate in
  design/32. Two review rounds and two rig rounds. One cosmetic residual (the
  refusal message shows the expansion with mixed separators) is carried forward,
  not owed.
  What 41d was: there was no `expanduser` anywhere in `microclaw/`, so `~/x` was
  joined under the workspace root as a literal directory named `~`. It predated
  Track D (`ee7f098`, 2026-07-28) and affected **every** path-taking tool, so any
  past session that used a `~` path had been writing into a stray `~` folder.
- **Block 41c is CLOSED.** Merged `d4eea5f`, gates in §"41c" below, design gate
  in design/33 Phase 4 and design/41 F6. Six review rounds, four rig rounds.
  **Track D is complete.**
- **Block 6a is the next work, and it is already pushed** — `design34/focus-system-authorization`
  at `4994f3e`, unchanged since 2026-08-05, awaiting the Nikon **and any non-EMU
  rig**. It is at step 5 of the block workflow: the code and runbook are on the
  branch, the gates have not been run. It must merge `main` before its own merge
  — `main` has moved a long way since it branched, and 6 and 7a unblock only once
  it lands.
- **Track order from here: Tracks D and E are complete** (41a, 13, 41b, 41c, 41d;
  42a, 42b all merged). The next live work is **Track B**: 6a is pushed and
  awaiting the Nikon, and 6 and 7a unblock when it merges. Then Track C (9–11)
  and closeout 12. **Note block 9 cannot start without operator intake** — its
  first item forbids creating the branch until a real workflow is supplied — so
  the readiest unblocked work is **design/43 F7**, whose dependency 42b cleared.
- **Track E — COMPLETE.** 42a merged 2026-08-07, 42b merged 2026-08-09. Added
  2026-08-07 from `design/42-open-what-we-wrote.md` and scheduled ahead of Track
  C because 6a was blocked on a remote operator and because design/43 F7 pointed
  at `open_artifact`. **Outcome:** 42a's spike disproved design/42's claim that
  `IJ.open` and drag-and-drop share an entry point for directories, and 42b's
  gate then disproved the replacement — Micro-Manager's dataset reader cannot
  open what microclaw writes. The shipped answer opens the TIFF stack files
  inside a dataset directory. **design/43 F7 is now assignable**; design/43 as a
  whole is still not scheduled.
- **`main` measures 1662 passed / 99 skipped / 3 expected warnings, 1761
  collected** (measured 2026-08-09 on a fresh clone of `origin/main` at
  `206ffa3`, macOS, 18.7 s — the 42b close-out. Previously 1623 / 99 / 1722 at
  `d4eea5f` on 2026-08-07; 42b added 39 tests and removed none). Re-measure rather than trusting this. Judge a suite by failures and
  collected total, and **diff collected test IDs** against the branch's start
  commit — that check has now caught silent test loss twice, and on 41c it
  distinguished a *rename* into three parametrized IDs from a deletion.

### State at the 2026-08-09 session boundary — read this before assigning anything

`main` is `e6afb06`; `git log --oneline origin/main..main` is empty; the working
tree is clean; the only branches on `origin` besides `main` are
`design34/focus-system-authorization` (6a), two `florian/*`, and
`port-to-jpype-acqj`. Two worktrees exist: this one and `../microclaw-6a`, which
is idle at `4994f3e` awaiting the Nikon.

- **Track F is new and is the live work.** Added 2026-08-09 from
  `design/43-nestor-session-findings.md`, which the 2026-08-07 note above
  described as "not scheduled" — that line is now historical, and this note
  supersedes it. Eleven blocks, 43a–43k, one per numbered item of design/43's own
  suggested order. **43a is assigned first**: F3 (live view is a dose) + F7 (the
  TIFF export offered for the wrong reason), whose 42b dependency cleared.
- **Nothing else is startable.** Track B's 6a has been pushed and unchanged since
  2026-08-05, awaiting a remote operator; 6, 7a, 7b and 8 all unblock only when
  it merges. Track C's block 9 forbids creating its branch until an operator
  supplies a real workflow. Tracks A, D and E are complete.
- **Design/43's findings were checked against the tree at `e6afb06` before the
  blocks were written**, and every file:line claim in the document holds:
  `_pause_live` at `tools.py:834` with nine callsites, the live-mode sentence at
  `agent.py:76`, the two export mentions at `:85` and `:149`,
  `CATEGORIES = ("samples", "devices", "strategies")`, `_load_saved_adapter`'s
  manifest-only lookup, the `unsupported-by-run_adaptive_survey` refusal at
  `hook_decisions.py:451`, and `run_timelapse` taking no `hook_strategy`. The
  document is safe to implement from directly.
- **One correction to a recalled fact**, made while checking: `_pause_live` no
  longer claims a restore it did not verify. It polls
  `core.is_sequence_running()` against a 2 s deadline and reports
  `restore_observed` plus a warning when the flag went back on and the sequence
  did not. Design/37 F4 is closed in the code; any memory or note saying
  otherwise is stale. 43a builds on that verification rather than adding one.
- **Suite baseline is `main`'s 1662 passed / 99 skipped / 3 expected warnings,
  1761 collected**, measured 2026-08-09 on a fresh clone at `206ffa3`.
  Re-measure; judge by failures and collected total; diff collected test IDs
  against the branch's start commit.

### Block 41c mid-gate — superseded 2026-08-07, kept for the round history

Written when M5 became unavailable mid-gate. **The block has since closed** —
round 3 on 2026-08-07 completed every owed step and the gate record is under
§"41c". This section is kept only for what the four rig rounds found and fixed,
which is the part worth reading again.

**What is already established, and must not be re-run to "confirm" it:**

- **The channel work passes on both rigs.** M5: `get_available_channels` returns
  `["405","488","561","640"]` on a rig that used to return `[]`; both switches go
  through the plan; the checker reports **G1 PASS** with **zero raw writes to a
  laser enable**; the reversed slot order is right (640 writes `Laser 1`);
  exactly one illumination confirmation per switch, on the enable, never on the
  disables. Demo: source names the `Channel` config group with no EMU mention,
  `channel_source: config-group`, no regression, and a channel-axis acquisition
  still runs there while M5 refuses it.
- **The emitted script's inlined read-back works standalone on hardware** (demo
  round 1): the script ran to completion against a live core with microclaw
  closed, every channel write through `_verify_property`, `_property_type_name`
  calling `get_property_type` over the pyjavaz bridge, `Core.Shutter` correctly
  getting no `wait_for_device`.

**What the four rig rounds found, all fixed and all in the branch:**

1. **The exporter replayed calls that never succeeded** (M5 r1 + demo r1). Pre-existing
   41b code, invisible until a session contained failures. Demo r1 measured it:
   three datasets per position where the session made one, the phantom acquiring
   in **FITC**, a channel the session never asked to image, because the rejected
   call carried `channel` top-level where the emitter does not read it.
2. **The blanket refusal then halted the script** (demo r2) at a call that never
   happened, before the acquisition that did. Split three ways: nothing-completed
   **skips and continues**; partial completion and cannot-emit still refuse loudly.
3. **A false `SAFE STATE NOT VERIFIED`** (M5 r2, twice in one session). An iChrome
   serial timeout on the plan's first write left `applied=[]`, the rollback
   re-wrote the same property, failed again, and escalated to the loudest error
   the executor has about a plan where nothing landed. Class is now chosen by
   writes the device **accepted** — not by `applied`, which is empty when only
   read-back failed though the device took the command.
4. **Two runbook criteria that would have mis-scored a real run**: step 10's
   `# SKIPPED` count was an equality of 2 where the honest session produced 4,
   and G1 exported *before* its refusals so skip-and-continue was unreachable on
   the rig (fixed by adding step 10).

**What the next M5 session owes** — one session, G1 steps 1–10 in order, then G3:

- **step 6's second acquisition** (561, after the filter move). Three M5 sessions
  have produced one acquisition, not two.
- **step 8's restore** → **G3 has never been demonstrated**, three sessions running.
- **running the exported script on M5 with microclaw closed** — never done there.
  Only the demo has executed an emitted script.
- if a serial timeout recurs, paste it: `ChannelPlanError` with `applied=[]` and
  **no** "SAFE STATE NOT VERIFIED" is the fix working.

**Carried forward, not owed by this block:**

- **The Float read-back is untested on hardware.** Neither rig reaches it by
  default — demo `Channel` presets expand to `Label` writes plus `Core.Shutter`,
  M5's enables are categorical. Adding a camera `Exposure` to one demo preset
  would close it. Recorded as **SKIPPED, not PASS**.
- `_emit_multiposition` reads the channel from `protocol_params` only, so a
  top-level `channel` renders channel-less. Unreachable now that malformed calls
  are skipped, still latent, and 41b's emitter rather than 41c's.
- Step 9's `set_channel` refusal for an unknown name comes from
  `guard.check_channel` before `authorize_channel` runs, so **41c's EMU-source
  refusal note is rig-unexercised** and unreachable by an unknown name on any rig
  with a non-null `channels.allowed`.
- Every plan writes each other named slot off **unconditionally** — three
  redundant writes per switch on M5, which is where both serial timeouts landed.
  Deliberate: a conditional skip would make the emitted script a function of that
  day's starting state, so a script that omitted "slot 0 off" could later image
  with two lasers. The better lever for a flaky link is retry-with-backoff at the
  device layer, not writing less. See design/33 Phase 4.

**Rig evidence folders** (under `~/Documents/Documents - Beyonce/Projects/Micro-Claw/`,
not in this repository): `41-block41c-m5`, `41-block41c-m5-round2`,
`41-block41c-demo`, `41-block41c-demo-round2`. The 2026-08-05 smiley session
(`smiley_run`) is the known-bad the G1 checker was validated against.

**The one process change made this session.** `CLAUDE.md` step 2 now says the
coordinator writes the runner prompt to scratch and **offers** to start an agent,
rather than spawning one. Blocks 13 and 41b were largely implemented by an
operator-driven codex runner; round 4 of 41b ran in an Agent-tool runner when
codex credits ran out. Both work; the prompt is the deliverable either way.

**What actually caught defects this session**, all of it in `design/prompts.md`
under "Blocks 13 and 41b": drive the real producer rather than reading the test,
execute the artifact rather than compiling it, and read the session history
rather than the artifact alone. Every single round came back green with accurate
numbers and had a real defect in it.

### Rescoped 2026-08-05 — read this before touching Track B

**Track 0 is closed and its plan did not survive contact.** The probe kit was
shipped 2026-08-03 and **never run** — the operator could not run the scripts
alone, which this file warns about at `:47`. What came back instead is five real
microclaw sessions from 2026-08-05, and they establish more than the kit was
designed to. `design/40-pfs-five-sessions.md` is the write-up and is
**authoritative over design/34 wherever the two disagree about the rig.**

Three consequences a resumed session must not undo:

1. **Do not re-ship or re-author the probe kit**, and do not re-open 0a/0b/0c.
   Blocks 0a and 0b are merged and their reconciliation cells are now closed
   against design/40, not against probe 0. Probe 0's question — does an
   out-of-range PFS drop `State` on its own timeout? — is still unanswered and
   is now a footnote, not a gate. Nothing in Track B waits on it.
2. **Block 6a is new and lands first.** The blanket exclusions on
   `TIPFSStatus.State` and `TIPFSOffset.Position` — ticked in block 4 at `:706`
   and `:724` — are what made PFS unusable, and block 8 lifted them last. One
   session was spent driving a 60× oil objective 450 µm past its recorded engage
   height only to find the final write was never permitted. The remote operator
   lifted both by hand, mid-session, twice.
3. **Old 7c is merged into 7a and marked skipped; old 7b is scoped down.** The
   evidence refutes the "configured approach position" the old 7c was built on
   (four locks in one day at 2912, 2498, 2532, 2450 µm), and the per-path
   movement policy in old 7b rested on a premise that is still untested. What
   survives of 7b is `run_autofocus` under an armed servo, which is certain.

**One thing that is outstanding and is not a work item you can start:** block 6
owes a post-fix rig run. Its pre-fix baseline is the 11:40 session, not probe S,
which was never run.

**Two settled operator rulings that must not be re-litigated:**

- **M5's acquisition budgets are closed** (2026-08-04). High ceilings are the
  operator's choice, set by a manual edit, and the `confirm_above_*` tier still
  forces human confirmation. See block 5's first item. A coordinator who meets
  M5's ~317,000-year duration cap should read that paragraph rather than
  re-deriving the finding.
- **`microclaw init` redirects to `first-launch-setup`** with `--from-example`
  as the deliberate hand-authoring escape hatch, and `install.bat` runs the
  guided flow behind a verified bridge. Blocks 5 and 5b both settled parts of
  this; design/17 §"Block 5b" is authoritative.

**One standing lesson this file keeps paying for.** Across Blocks 5 and 5b, every
single defect the rig gates found was in a coordinator-authored **runbook**, not
in the code — six of them, all the same shape: *a criterion that could not fail*
(stale `$LASTEXITCODE` after a cmdlet, `Start-Transcript` not capturing a child
process, match patterns that also matched the prompt or the intro text). One
would have failed a passing run. **Run every match pattern over a real captured
transcript before shipping a runbook**, and review the runbook like code.

**The rule that follows, adopted after block 6a took three rounds to ship a
runbook (2026-08-05):** a gate criterion must be demonstrated to **fail on
known-bad evidence and pass on known-good evidence** before the runbook ships.
Both directions, or it is not validated. Block 6a's four runbook defects were
each caught by exactly this and by nothing else — a PFS-write check that passed
on the pre-fix baseline session, OR'd `Select-String` patterns summed to 9 on the
operator's own correct profile against an expected 2, a `position_um` argument
that does not exist so the check read 0 on any run, and a threshold of 4 against
a string only 3 call sites emit. None was visible by reading. The known-bad
evidence already exists for most gates: the rig sessions that motivated the
block. The known-good evidence is usually the operator's current working
profile.

### Where the rig evidence lives

**Gate evidence bundles are not in this repository.** Every
`block4-*`/`block4b-*`/`m5-*` folder named in the gate records below lives under
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/` on the coordinator's
machine, alongside the saved run histories. A session that has not been told
this cannot find them, and several review findings in this file were only
catchable by replaying a captured `inventory.json` from one of those bundles.
Recorded here because it was previously stated only in a spike comment
(`design/29-block9-landmark-check.py:58`) and one older gate runbook.

Two habits that follow from it, both of which caught real defects: replay the
**captured** inventory rather than a synthetic fixture when judging an
interview change, and read the tool-call history JSONL rather than the
assistant's narration when judging whether a guard fired.

### Run ledger

| Block | Track | Depends on | Branch | Start commit | Implementation commit | Rig evidence | Merge | Design reconciliation |
|---|---|---|---|---|---|---|---|---|
| 0a | Remote kit | — | `design34/nikon-probe-kit` | `b717594` | `42d8978` (`8696169` rejected) | **is the deliverable** | `ba695da` | **done 2026-08-05** — design/40 supersedes design/34; probe 0 unanswered and no longer blocking |
| 0b | Remote kit | — | `design34/nikon-stopgap-config` | `b717594` | `ead2fb9` (`6ac2ab2` rejected) | 0c ships it | `96a0a91` | **done 2026-08-05** — design/40 supersedes design/34; probe 0 unanswered and no longer blocking |
| 0c | Remote kit | 0a, 0b | — (ship + wait) | | | **CLOSED 2026-08-05 — the kit was never run; five real sessions answered more than it would have** | n/a | done — design/40 |
| 1 | Usability | 0a and 0b assigned | `design33/phase5-doc-reconciliation` | `b717594` | `dd359a3` | n/a | `20b92e2` | done — block *is* the gate |
| 2 | Usability | 1 | `design33/undeclared-light-source-gate` | `98842cf` | `ef72b15` + `e9817ad` | **PASS** — M5 refusal/declaration/confirm/cleanup + separate demo fail-closed run | `a1b7579` | done — design/33 landed semantics + residual boundary |
| 3 | Usability | 2 | `design33/config-diagnostics` | `e8d6ee1` | `dce17a4` + `65bfd7c` | n/a — no rig surface | `0cb871f` | done — error taxonomy + offline-validation contract |
| 4 | Usability | 3 | `design33/first-launch-setup` (deleted) | `bc303a2` | `a742d73` | 5 rounds: demo r1 **FAIL**, r2/r3 **PASS**; M5 G4 + **G4b PASS** 2026-08-02 | `6266807` | **done** — design/33 §"Phase 5 landed" |
| 4r1a | Usability | 4 | `design33/first-launch-setup` | `15d8d1b` | `c5746b9` (`d203753` rejected) | folded into block 4 round 2 | n/a — merges via block 4 | |
| 4r1b | Usability | 4 | `design35/startup-refusal-severity` | `15d8d1b` | `2558583` (`16cc416` rejected alone) | folded into block 4 round 2 | `385049d` into block branch | |
| 4b | Usability | 4 merged | `design33/bounded-numeric-actuator` (deleted) | `578874e` | `5a6c6e2` | G1 demo **PASS**; G3 M2 **PASS** incl. imagery; G2 M5 in-range **PASS**, refusal step retired | `04164fd` | **done** — design/33 §"Block 4b landed" |
| 4e | Usability | 4b merged | `design33/emission-path-discovery` (deleted) | `85398e8` | `bc40f18` + `d631a4f` (`39f69dd` returned) | M2 G1/G2, M5 G3, demo G3 all **PASS** 2026-08-03 | `9b88394` | **done** — design/33 §"Block 4e landed" |
| 4f | Usability | 4e merged | `design33/channel-group-presets` (deleted) | `f95c8ca` | `84c4d70` + `d4985e6` | M2 G1 + demo G2 **PASS** 2026-08-03 | `9010158` | **done** — design/33 §"Block 4f landed" |
| 4h | Usability | 4f merged | `design33/confirmation-visibility` (deleted) | `1599ff3` | `3efecaf` + `518a90a` + `e451a5c` | demo G1+G2 **PASS** 2026-08-03 | `e42b930` | **done** — design/21 §"F1 revisited" |
| 4c | Usability | 4h merged | `design33/setup-named-stages` (deleted) | `3b99397` | `7f68f33` + `e537207` + `c34ee1e` + `d934dbf` (round 1 returned) | demo G1+G1b, M5 G2, demo re-gate all **PASS** 2026-08-03 | `8ce3401` | **done** — design/33 §"Block 4c landed" |
| 4g | Platform | none — may run concurrently | `design32/hook-hash-newline` (deleted) | `95ae192` | `50e5f66` + `d0bb602` + `971cdb6` + `f768cc3` | round 3 **PASS** 2026-08-03 (rounds 1–2 failed test-side) | `936230f` | **done** — design/32 §4 |
| 4d | Usability | 4c merged | `design33/property-authorization-rename` (deleted) | `052179d` | `fd4c5b6` + `c063f16` + `029b5f4` | demo G0/G1/G2 + M5 G4 **PASS** 2026-08-03; G3 closed by offline replay | `5f56679` | **done** — design/33 §"Block 4d landed" |
| 5 | Usability | 4b, 4e, 4f, 4h, 4c, 4d | `design33/deployed-config-hygiene` | `a27997f` | `6262acb` + `577acc4` + `c0344f2` + `8028145` + `c48edc1` (round 1 returned) | demo G0–G3 + M5 G0/G4 all **PASS** 2026-08-04 | `d14c147` | **done** — design/33 §"Block 5 landed", design/17 §"Block 5: the first-run path moved" |
| 5b | Usability | 5 merged | `design17/guided-install` (deleted) | `3d63a6c` | `c2ee97c` + `11000bd` + `eb94b2e` + runbook `644e592`/`c1cdc62`/`d585549` (round 1 returned) | demo G0/G1/G3 + M5 G0/G2 **PASS** 2026-08-04; operator confirmed full `install.bat` on M5 | `ab5e97c` | **done** — design/17 §"Block 5b: the installer guides the whole first run" |
| 6a | Nikon | — **assign first** | `design34/focus-system-authorization` | `f41c89a` | `eb93067` + `49b2487` + `2a5ca10`; runbook `21e708e`/`8db66cc`/`4994f3e` (rounds 1 and 2 returned) | **pushed 2026-08-05, awaiting the Nikon + any non-EMU rig** | | |
| 6 | Nikon | 6a | `design34/measured-position-readback` | | | **required** — 11:40 session is the pre-fix baseline; probe S not owed | | |
| 7a | Nikon | 6a | `design34/continuous-focus-capability` | | | **required** | | |
| 7b | Nikon | 7a | `design34/continuous-focus-policy` | | | **required** | | |
| 7c | Nikon | — | — | — | — | — | **SKIPPED 2026-08-05** — merged into 7a | design/40 D3 |
| 8 | Nikon | 6, 7a, 7b | `design33/phase5-continuous-focus` | | | required | | |
| 13 | Platform | 41a merged | `design40/platform-defects` (deleted) | `03dcea0` | `0c83268` + `c2fc7ab` (round 1 returned); runbook `9d2a934`; post-gate `f4e98c6` **ungated** | M5 2026-08-06 **G1/G2/G4/G5 PASS**; **G3 not runnable — no transmitted light on M5, carried forward** | `d24e721` | **done** — design/25 §"SNR validity, stated once", design/32 §"One hook contract", design/40 §"What block 13 shipped", design/41 F4/F5 |
| 41a | Platform | none — **assign first in Track D** | `design41/session-survival` (deleted) | `b0ee300` | `501287f` + `bb58551` (round 1 returned) | n/a — no rig surface | `1fb284d` | **done** — design/16 §5 "The invariant is not about Stop"; design/41 F2/F3/F7 ticked |
| 41b | Platform | 41a merged | `design41/script-export` (deleted) | `03dcea0` | 4 review rounds through `5ead7cc`; README `4b08f30`; runbook `bc9aea1`; post-gate `b6cc7a2` + `5af0fc6`, both **ungated** | M5 **G1/G2/G3 all PASS** rounds 4–5 2026-08-06 | `b1aa55e` | **done** — `CLAUDE.md` compile-to-script pointer, design/41 F1 |
| 41c | Platform | 41b merged | `design41/emu-channel-plan` (deleted) | `8a11e45` | 21 commits through `3bae4e2`, 6 review rounds; runbook pin `c5917cc` | M5 **G1+G3 PASS** round 3 2026-08-07; demo **G2 PASS** rounds 1–2 (`block41c-round3`, `41-block41c-m5`, `-m5-round2`, `41-block41c-demo`, `-demo-round2`); Float read-back **SKIPPED** | `d4eea5f` | **done** — design/33 Phase 4, design/41 F6 |
| 41d | Platform | none — ran concurrently with 41c | `design41/path-expansion` (deleted) | `8a11e45` | `63ddd2b` + `ff03913`; review round 2 `3412aea`; runbook pin `d6e2a79` | M5 2026-08-06 **Step 0 + G0 + G1 + G2 + G3 all PASS** (`gate41d-m5`, two rounds) | `f864a33` | **done** — design/32 §"The path-normalisation contract (block 41d)" |
| 42a | Read side | none — may run concurrently with Track B | `design42/ij-open-spike` (deleted) | `ca0709d` | `0d97329` + runbook `4ab450b`; review round 1 `f95d5a9` (runbook pin `0d97329` re-verified after it) | M5 2026-08-07 **PASS** — 6 PASS / 3 INFO / 1 SKIP, no FAIL; 1c reproduced the design/12 collision (`out42a.txt`) | `4bcaeee` | **done** — design/10 §2 + Net conclusions #2 amended, design/42 §"What the spike measured" |
| 42b | Read side | 42a's answers | `design42/open-artifact` (deleted) | `4d6a426` | `0dd3629` (dir spike) + `410846e` + `e02955f` + `ebebe45` + `ed9c78a`; review round 1 `867f3af`; runbook `a038c95`/`838f00d`/`b06fbac` pinned `867f3af`; findings `c60d33f`; fix round `229423d` (runner) + review round 2 `f574643`; **redesign `a97620b`** (open the dataset's TIFFs, MM reader deleted); runbook re-pinned `a97620b`; round-3 fix `5d09b7b` | **demo machine, three rounds, PASS at `a97620b`.** Round 1 (`bc93f76`): file branch PASS (G1/G2, no thumbnail), directory branch FAIL — G0 D2 ERROR and G4a wedged the bridge ~5 min. Round 2 (`f574643`): the watchdog named the stalling call and produced **F4**. Round 3 (`41b-open-artifact-demo-round3`): **G1+G2+G3+G4a+G4b+G4c ALL PASS**, no stalls, one image block in the session and only on the `analyze=true` turn; `stitch_test_1` — which MM's reader could never read (**F1**) — opens with all 6 tiles. Findings F1–F4 in `design/42-block42b-gate-findings.md`; round-3 finding fixed in `5d09b7b`. **G0 retired.** Multi-channel axis structure is a **known, tabled limitation**. **M5 still owed** — read-side block, demo-gated by design | `0819790` | **done** `25fc9cc` — design/42 §"What the gate measured"; design/43 F7 dependency cleared and assignable |
| 43a | Nestor | none — may run concurrently with Track B | `design43/live-dose-and-tiff-prose` (deleted) | `3d6146b` | `cf1f272` + `af7e015` (review round 1 returned); runbook `92520b7` pinned `af7e015` | **M2 2026-08-09 — G1, G2, G4 both limbs, G5 all PASS; G3 answered, no fix owed** (`43a-m2`). Gated on M2, not M5: same camera-triggered illumination, and deliberately off the outlier rig. Suite red with 9 pre-existing Windows failures, none in touched code → block 43m | `5ec57cb` | |
| 43m | Nestor (fallout) | none — **gates every later Track F rig gate** | `design43/windows-suite-integrity` | `18f84f1` | `f49deb5`; runbook `f0f3d3f` pinned `f49deb5` | **pushed 2026-08-09, awaiting M2 or M5** — runbook `design/43-block43m-rig-gate.md`, on the branch. The gate *is* the suite: 0 failed, 1766 collected | | |
| 43b | Nestor | none | `design43/refresh-gui` | | | **required — M5**; `javap` the deployed `MMJ_.jar` before implementing | | |
| 43c | Nestor | none | `design43/session-grants` | | | **required** — the audit log must still record every event | | |
| 43d | Nestor | none | `design43/report-shapes` | | | required — payload text, validate criteria against the Nestor history | | |
| 43e | Nestor | none | `design43/builtin-offline-adapters` | | | required | | |
| 43f | Nestor | 43a merged (its prompt names the key this creates) | `design43/rig-profile` | | | required | | |
| 43g | Nestor | none | `design43/coverage-statistics` | | | **required — beads + a diffuse field**; nothing ranks on it until calibrated | | |
| 43h | Nestor | none | `design43/emit-adaptive-runs` | | | **required** — run the emitted script with microclaw closed | | |
| 43i | Nestor | 43g, 43h | `design43/survey-refocus` | | | required | | |
| 43j | Nestor | 43e (retires half of F12) | `design43/hooks-and-timelapse-observation` | | | required | | |
| 43k | Nestor | 43h, 43i **run on a rig** | — design first | | | n/a — design block | | |
| 9 | Features | operator intake | `design26/generated-adapter-run-b` | | | required | | |
| 10 | Features | 9; optional | `design26/few-shot-run-c` | | | required or marked skipped | | |
| 11 | Features | accepted Run B fixtures | `design32/hook-worker-isolation` | | | regression required | | |
| 12 | Closeout | prior applicable blocks | — | | | **required** | n/a | |
| 39 | Out-of-band | — | `design-39-emu-names` (deleted) | `0016c54` | `7450f5e` + `a7ac7d5` (coordinator review) + runbook `483c620` + `6adef62` | M5 G1–G4 + demo G5 all **PASS** 2026-08-05 (`39-emu-m5`, `39-emu-demo`); **`6adef62` landed post-gate and is ungated** | `c987f65` | **done** — design/39 §"What shipped, and what the gate measured" |

**Out-of-band rows.** design/36, design/37, design/38 and the composition block
all ran the full block workflow without a ledger row, because they grew out of
rig sessions rather than this checklist — so while each was in flight there was
nowhere a cold session could look to find its branch or its gate state. Row 39
exists to close that gap. It is **not** a design/35 block and nothing here
depends on it; the ledger is simply the one place a resumed session looks.
Its two design commits (`9bf42a5`, `0016c54`) landed on `main` before the block
branched, so the start commit is `main`'s tip, not a branch point.

Baseline to re-measure before block 1: `main` was 1160 passed / 99 skipped / 3
warnings at the previous closeout, before Block 14 Phases 2 (`47f6702`) and 4
(`5458483`) landed. Re-measure; do not trust that number.

**Re-measured 2026-07-30 at `b717594`: 1207 passed / 99 skipped / 3 warnings in
13.2 s.** This is the baseline every block below is compared against. The 3
warnings are **one `StarletteDeprecationWarning` plus two
`phase_cross_correlation` empty-image `UserWarning`s** from
`microclaw/tools.py:1551`–`:1552`, raised by the featureless-field calibration
tests; all three are pre-existing and expected, not a regression. (Corrected
after block 1: an earlier version of this line attributed all three to
`phase_cross_correlation` and cited only `:1552`.)

---

# Track 0 — the remote Nikon kit (blocks 0a–0c) — **assigned first**

Authored here, run there. Nothing in this track blocks on Track A, and Track A
does not block on it: the probes need only a Micro-Manager ZMQ connection.
0a and 0b are assigned before block 1 and ship together as soon as both are
ready; see "How to use this file" for the concurrency exception.

## 0a. Author the probe kit — standalone, no microclaw import

Branch: `design34/nikon-probe-kit`

Follow the house spike convention exactly: one file per probe in `design/`,
`python design/NN-name.py`, `pycromanager` + stdlib only, and **the first line of
the docstring states that probe's exact hardware boundary**, following
`design/29-mm-pixel-affine-probe.py`. Do not reuse its read-only wording for a
probe that changes autofocus state or can cause servo motion.
That first line is what the remote operator reads to decide whether it is safe
to run.

Rules that apply to every script in the kit:

- [ ] **No `import microclaw`.** The operator's install may currently refuse to
      start; the probes must be unaffected by that.
- [ ] **Self-validating.** Check preconditions up front — a connection exists,
      `Core.Focus` is `TIZDrive`, `Core.AutoFocus` is `TIPFSStatus`, the expected
      devices are loaded — and **exit with a plain-language message naming what
      is wrong** rather than producing partial output. The operator cannot debug
      a half-run.
- [ ] **Self-identifying.** Emit MM version, device adapter names and versions,
      the loaded `.cfg` path and hash, the script's own sha256, a UTC timestamp,
      and the resolved device assignments into the output. I must be able to tell
      exactly what ran without asking.
- [ ] **Two outputs with deterministic names:**
      `<probe>-<UTC timestamp>-<rig-id>.json` for analysis and the matching
      `.txt` log for the operator. Write both atomically into an explicit
      `--output-dir`; stdout is only a concise progress/error stream. A
      Windows-safe invocation may additionally capture it with
      `python design\NN-name.py --output-dir evidence > console.txt 2>&1`.
- [ ] **No verdict from the operator.** The script prints observations. It may
      print a mechanical verdict when the criterion is unambiguous, but it must
      never ask the operator to judge anything.
- [ ] **Fail safe on interrupt with a per-probe restoration contract.** Snapshot
      every relevant initial state. Each probe declares whether it restores that
      snapshot or forces a named safer state, implements cleanup in `finally`,
      verifies the resulting state by read-back, and prominently reports failed
      restoration in both outputs. Probe S may use a different contract where
      disabling a locked PFS would itself be unsafe.

The kit, in the order they run it:

- [ ] **Probe 0 — the null control. No commanded Z-stage motion.** Arm PFS out of
      range, then
      sample `TIPFSStatus.State` and `.Status` on a fixed schedule (~1, 2, 5, 10,
      30 s) while **commanding no Z motion**, recording elapsed time against
      every read. Repeat at a Z where PFS *can* lock, since a timeout only
      applies to a failed search. Record `FullFocusTimeoutMs` explicitly.
      This is the whole reason the track is ordered this way: the session
      concluded `move_stage_z` disables PFS, and **that is not established.** If
      `State` falls to `Off` with no commanded Z motion — plausibly near the
      5000 ms timeout — the move was never implicated, and the earlier motion
      observations were confounded by elapsed time. Block 7b may then shrink to mandatory
      `require_off` enforcement and block 7c may disappear; neither `preserve`
      nor a search workflow is justified without further evidence. Block 7a
      remains a typed status/control capability. This probe mutates autofocus
      state, and its in-range case may cause servo-driven TIZDrive motion; its
      docstring and run sheet must say so and must not call it read-only or zero
      motion.
- [ ] Probe 0 takes two explicit operator-confirmed arguments with no defaults:
      an out-of-range observation position and an in-range lockable position.
      It never moves to either position. Each case is a separate invocation that
      verifies current Z is within a declared small tolerance of its argument or
      refuses with plain positioning instructions.
- [ ] **Each invocation emits its own verdict; the probe never emits one combined
      verdict.** The two cases are expected to differ, and that difference is the
      confirming result rather than a contradiction: a full-focus timeout applies
      only to a *failed* search, so the timeout hypothesis predicts
      `autonomous timeout observed` out of range and `remained armed through
      window` in range. Tag every verdict with its case, and require both cases
      before 0c interprets either. A single case in isolation cannot distinguish
      the hypotheses.
- [ ] **Probe S — offset settling. No commanded Z motion.** Reproduce the
      `move_named_stage` failure directly: from a locked PFS, command a
      `TIPFSOffset` move and poll the offset position *and* TIZDrive on a tight
      schedule, recording the first read after `wait_for_device` alongside the
      settled value. The session's three moves each returned the exact previous
      target, so the expected finding is that `Busy()` clears before motion
      starts. **Record TIZDrive throughout:** an offset write commands a servo, so
      the focus drive moves in response, and the offset's own read may not capture
      that.
- [ ] **Bound probe S's offset delta.** It commands no Z motion of ours, but by
      the line above the servo drives Z in response, so an unbounded offset delta
      is an unbounded Z excursion — the one motion-adjacent probe that would
      otherwise ship without a ceiling. Require a maximum per-move delta, a
      total-excursion budget, and an operator-confirmed absolute TIZDrive
      envelope as arguments with no defaults; abort if measured Z leaves that
      envelope. Record the observed offset-to-Z response. State an expected
      excursion only when prior measured evidence supports it—never infer one
      from the requested offset delta. The session's moves (130 → 160 → 149.5)
      show a small offset bound is realistic. "PFS is locked" is the safe
      *starting* state, not a bound on what the servo will do next.
- [ ] **Shape probe S's output to satisfy block 6's rig gate.** That gate asks for
      three consecutive `TIPFSOffset` moves reporting their own achieved
      positions with TIZDrive recorded alongside — which is what this probe
      already does. Record per-move requested target, first read after
      `wait_for_device`, full poll series with timestamps, settled value, and
      concurrent TIZDrive, so block 6 can use returned probe S data as acceptance
      evidence instead of costing a third round trip. Block 6 still owes a
      post-fix run; the point is that its *baseline* arrives with round trip #1.
- [ ] **Probe E — environment and rig-profile capture. Read-only.** Collect
      everything design/34 `:223`–`:234` asks for that can be read rather than
      measured: NikonTI adapter / MM / firmware versions, `TIPFSOffset` driver
      limits, current objective, `Core.Focus` and `Core.AutoFocus` assignments,
      full property dumps for `TIZDrive`, `TIPFSStatus`, `TIPFSOffset`. It may
      report discoverable APIs and properties, but must not claim those establish
      what Studio's Stage Control panel invokes.
- [ ] **Probe U — optional instrumented UI-path probe.** Establish whether MM
      Studio or the NikonTI adapter exposes a PFS-preserving jog distinct from raw
      CMMCore commands using source evidence, API tracing, or a scripted and
      instrumented operator action. Keep this separate from read-only Probe E;
      ship it in round trip #2 unless it is self-validating and safe enough for
      round trip #1.
- [ ] **Probes 1–4 — motion. Ship, but hold behind probe 0.** PFS on then
      absolute `set_position`; PFS on then `set_relative_position`; PFS on then a
      Stage Control panel move; `enableContinuousFocus(true)` versus writing
      `TIPFSStatus.State = On`. For each, record TIZDrive before and after, PFS
      enabled and locked state, `State`/`Status` transitions, time per transition,
      **elapsed time since arming**, and whether the move completed, was rejected,
      or was modified by the servo.

Motion-probe safety, because this is oil immersion near a coverslip on a rig
nobody here can see:

- [ ] **A hard Z ceiling is a required argument with no default.** Refuse to run
      without it. Refuse if the ceiling is above the operator-confirmed value.
- [ ] Small bounded steps, an absolute total-travel budget, and an immediate abort
      on any unexpected `Status`.
- [ ] A `--dry-run` that prints every move it *would* make, so the operator can
      read the plan back before anything moves.
- [ ] Written instruction that probes 1–4 are **not** to be run until probe 0's
      output has been sent back and reviewed here.

Deliverable of 0a: the scripts, plus a single plain-language `README`-style run
sheet for the operator — Windows commands, expected runtime, what "it worked"
looks like, what to send back, and what to do if it refuses to start.

## 0b. Stopgap Nikon safety config — unblock them before block 4

Branch: `design34/nikon-stopgap-config`

They need a microclaw that starts, and block 4 is weeks away for them. We already
hold `260715_Nikon_iXon_Prior.cfg` and a full session history, which is enough to
hand-author a draft.

Precedent for the artifact: `design/29-block9-m2-safety-config.yaml`. It lives in
`design/` as gate evidence — **rig facts never go in `microclaw/`.**

- [ ] Hand-author a draft `safety_config.yaml` for the Nikon from its `.cfg` and
      the session history: device names, named stages, categorical properties,
      illumination declarations.
- [ ] Produce two clearly named artifacts if the strict schema cannot represent
      missing limits: an annotated, intentionally non-runnable worksheet with
      every safety limit blank, and a schema-valid `reviewed: false` draft only
      after the operator supplies those values. Never insert placeholders merely
      to make validation pass. The Z ceiling, exposure cap, and travel limits are
      theirs to set, and `reviewed: true` must be their act after reading it. Do
      not hand them a file that merely starts.
- [ ] **Exclude the continuous-focus mutation surface.** `TIPFSStatus.State` must
      not appear as a categorical property, per block 4's rule and the impact
      summary. Say in a comment why, and what they lose.
- [ ] Decide and record: do they run current `main`, or pin to a known-good
      commit? Current `main` has every tightening block since `722184a`, and
      **block 2 will make it stricter still.** Whichever is chosen, write down the
      exact commit or version they are on, because every piece of evidence they
      return has to be interpreted against it.
- [ ] Dry-run the draft against block 3's offline validator once that exists; until
      then, validate by inspection against `safety_config.example.yaml` and the
      strict schema.
- [ ] Include in the run sheet what to do when startup refuses: send the exact
      refusal text back, unedited. Those refusals are also block 3's best
      real-world test data.

## 0c. [x] Ship, wait, and triage what comes back — **CLOSED 2026-08-05**

No branch — this is coordination.

**The kit was shipped 2026-08-03 and never run.** The operator could not run the
scripts alone. Five real microclaw sessions came back instead, written up in
`design/40-pfs-five-sessions.md`, and they answered more of design/34's
questions than the kit was designed to. The triage items below are kept as the
record of what was planned; they were not executed and are not owed. **Do not
re-ship or re-author the kit.**

What the sessions did *not* answer, and what happens to it:

- **Probe 0's question is still open** — no session moved Z with PFS armed, so
  nothing distinguishes an elapsed-time timeout from an effect of the move.
  Recorded in design/40 §"Still owed". It gates nothing.
- **Probes 1–4 are retired for now**, not because they were answered but because
  the procedure that works does not need them. Do not ask a remote operator to
  run motion near a coverslip to settle a question no block depends on.
- **The design/34 `:223`–`:234` rig-value list is no longer collected up front.**
  Capture range, safe step and timeouts are collected *by* block 7a's bounded
  search as it runs; the engagement position it asked for turned out not to be a
  stable number.

The original items, unexecuted:

- [ ] Ship 0a and 0b together as one package. One shipment, maximum information.
- [ ] On return: verify the script hashes match what was shipped, confirm the
      preconditions passed, and record the evidence under
      `design/evidence/nikon-pfs/<YYYYMMDD>-<rig-id>/` with a manifest containing
      every filename, sha256, probe version, rig identifier, and interpretation
      status.
- [ ] **Answer probe 0's question explicitly in writing before blocks 7a–7c are
      scoped**, and update `design/34-nikon-pfs-tizdrive-findings.md` with which
      hypothesis the data supports. The document currently states both and
      commits to neither.
- [ ] Use unambiguous verdict names: **autonomous timeout observed** means an
      out-of-range `On` → `Off` occurred with no commanded Z motion;
      **remained armed through window** means it did not. Never call either
      result merely positive or negative.
- [ ] **Interpret the two cases together, and expect them to differ.**
      `autonomous timeout observed` out of range plus `remained armed through
      window` in range is the timeout hypothesis confirmed, not a conflict. The
      same verdict in *both* cases is the informative surprise: armed-through in
      both leaves the move hypothesis live, and autonomous timeout in both means
      something is dropping `State` irrespective of search success and neither
      hypothesis is yet supported. Record which of the four combinations
      occurred; do not collapse them.
- [ ] Decide whether probes 1–4 are still needed. If autonomous timeout was
      observed out of range, they are measuring an elapsed-time artifact unless a
      distinct question remains; say so and do not ask a remote operator to run
      unnecessary motion near a coverslip.
- [ ] Expect a round trip #2 and scope it now: the motion probes, plus anything
      probe 0's answer newly makes worth measuring.
- [ ] Include a coordinator-authored measurement worksheet for facts that cannot
      be read mechanically: safe ceiling per objective/holder, direction, safe
      step, capture range, engagement repeatability and sample dependence, and
      safe offset limits. The operator records measurements and confirmations;
      the script computes mechanical checks and the coordinator returns the
      engineering judgement. This is evidence collection, not asking the remote
      operator to diagnose the system.
- [ ] If a probe failed in a way the operator could not resolve, that is a defect
      in 0a's self-validation, not operator error. Fix the script before
      reshipping.

---

# Track A — restore usability (blocks 1–5)

## 1. Design reconciliation and stale bookkeeping — no rig

Branch: `design33/phase5-doc-reconciliation`

Small, and it goes first because a Phase 5 implementer reading design/33 today
would pin the wrong contract.

**Correction (coordinator, 2026-07-30): this block is NOT documentation-only, as
this section originally claimed.** Verified on `b717594`:
`microclaw/rig_inventory.py:380` emits the version as a bare inline string
literal, and `grep` for `SCHEMA`/`SUPPORTED` in that module returns nothing —
there is no constant to point prose at. The second item below therefore requires
a real change under `microclaw/`, plus tests. Scope the block accordingly; do not
hand an implementer a "docs only" framing that its own second item contradicts.

- [x] Fix design/33 `:392`: the inventory schema is documented as
      `microclaw.rig-inventory/v1`, but `microclaw/rig_inventory.py:380` has
      emitted `microclaw.rig-inventory/v2` since `5c54e55`. Pinning `v1` would
      reject every inventory the shipped producer writes.
- [x] Replace the prose version literal with a pointer to a **shared supported-
      version constant or parser contract** exported by `rig_inventory`, so the
      next bump cannot desynchronise prose from producer again. Phase 5 reads the
      supported set from the producer, never from the document.
- [x] **There is a second, independent version literal the block text missed:**
      `microclaw/rig_inventory.py:375` emits
      `microclaw.rig-inventory-fingerprint/v1` for the fingerprint payload, which
      versions separately from the inventory schema. Decide explicitly whether it
      joins the shared constant/contract or stays deliberately independent, and
      record which. Fixing only the inventory literal leaves the exact same
      desynchronisation defect live one line above it.
- [x] Correct the previous checklist's final-closeout line (`:1338`), which says
      Block 14 Phases 2, 4, and 5 are all unstarted. Phases 2 (`47f6702`) and 4
      (`5458483`) are merged; only Phase 5 remains. Leave the rest of that file
      intact and add a one-line pointer at its top to this file.
- [x] Tick the two Block 14 rows that were held unticked only because Phase 5 had
      not started (old checklist `:1226`, `:1229`), or restate them as
      Phase-5-scoped here. Do not leave them ambiguous in two files.

Post-merge design gate:

- [x] None beyond the edits themselves; this block *is* a design gate.

## 2. Close the undeclared-light-source vulnerability — rig gate required

Branch: `design33/undeclared-light-source-gate`

The highest-priority finding open at the previous closeout, and a Phase 5
prerequisite. Today the authorization map can permit a write to a property that
`get_emu_configuration` separately identifies as a laser enable, while
`illumination.shutters` omits it — so the write is unconfirmed, uncapped, and
not driven off at session exit. Nothing cross-checks the two subsystems
(design/33 `:960`–`:980`).

- [x] At startup, inside the existing `validate_live_rig` cross-check machinery
      (design/33 `:340`–`:346`) — not as a new surface — cross-check every EMU
      laser enable against `illumination.shutters`.
- [x] **Refuse in guaranteed mode.** Warning alone is acceptable only under
      `degraded_trusted_plugins`, which explicitly suspends the completeness
      claim. Do not ship warn-only as the guaranteed-mode behaviour.
- [x] The refusal message must name the exact device, property, and the exact
      YAML block the operator must add. This block is as much a usability item as
      a safety one: a fail-closed refusal that does not say what to declare
      converts one silent hazard into one silent blocker.
- [x] Extend the same cross-check to any other subsystem that names an emission
      path by semantic role, if one exists. Do not assume EMU is the only one;
      report what you find rather than widening silently.
- [x] Note explicitly in the block report: this gate runs at **startup**, so it
      does not cover Phase 5's own enumeration. That window is block 4's problem
      and is not closed here.

Rig gate (M5, the rig that has the EMU):

- [x] With the *current* deployed M5 config, show whether startup now refuses,
      and if so, that the message names the missing declaration precisely.
- [x] Add the named declaration; show startup succeeds and the enable is now
      confirm-gated on enable and driven to `off_value` on every exit path.
- [x] Show the demo config's behaviour too: `illumination.shutters` is empty
      there, which is the configuration that made this finding visible.
- [x] If the demo core exposes no semantic laser-enable candidate, exercise the
      refusal with a representative off-rig inventory fixture. Do not claim the
      demo run itself tested a condition it cannot represent.

**Expected side effect, state it plainly:** this block can stop a rig that
starts today from starting. That is the correct fail-closed direction, and block
4 is what makes the fix easy to author. Sequence the M5 config edit with the
merge so the rig is not left down.

Post-merge design gate:

- [x] Record in design/33 that the finding is closed, the exact refusal
      semantics, and the residual limit: the cross-check proves declared enables
      are shuttered, **not** that discovery found every physical emission path.

## 3. [x] Actionable refusals and offline config diagnostics — usability

Branch: `design33/config-diagnostics`

This is the block that most directly answers "the safety config has made
microclaw hard to use," independently of Phase 5. It has no new hardware surface.

**Correction (coordinator, 2026-08-01): the first item below is already done on
`main` and must not be re-implemented.** Verified at `ac61e2b`:
`microclaw/errors.py:81`–`:88` special-cases `RigAuthorizationError` ahead of
`_HARDWARE_HINT` and returns an authorization-decision hint. It landed
incidentally in `bb6290f` (Block 14 Phase 4 gate record), not under this block,
which is why the block text still describes the defect. The implementer's job on
that item is to **verify the fix covers every path a refusal reaches an
operator** — `hint_for_error` is the tool-result path; check the `serve`/web
path (`microclaw/webserve.py:291`) and the CLI paths
(`microclaw/__main__.py:191`, `:274`) separately — and to add the regression test
if none exists. Report what was already covered rather than restating the block
text as work performed.

- [x] **Fix the misleading refusal hint** (design/33 `:827`): `execute_tool`
      attaches `This may be a hardware error (device busy, stage at limit, device
      not found) or a connection problem.` to a `RigAuthorizationError`. A policy
      refusal is presented as a hardware fault, which sends operators to debug
      the microscope. Distinguish the two error classes at the point of
      attachment. — pre-fixed in `bb6290f`; see the correction above.
- [x] Every authorization refusal must state: what was refused, which declaration
      would have permitted it, and where that declaration goes in the file.
      Refusals that cannot name a legal declaration (an excluded property, an
      unclassifiable actuator kind) must say *that*, rather than implying an
      edit exists.
- [x] Add an **offline** config check — no Micro-Manager connection — that parses
      the file under the strict schema and reports every problem at once rather
      than exiting on the first. There is currently no way to check a config
      without a live rig: `authorization-map` and `inspect-rig` both connect, and
      `load_safety_config_or_exit` (`microclaw/config.py:46`) exits on the first
      failure.
- [x] Report unreviewed (`reviewed: false`) as a distinct, expected state with
      the next action, not as a parse failure.
- [x] Phase 5 (block 4) must reuse this validator to check what it wrote. Design
      the interface for that caller now; do not build a second parser.
- [x] Optional and clearly marked as defence-in-depth, not a Phase 5 obligation:
      detect limits still equal to `safety_config.example.yaml`'s fictional
      values and say so. This targets the `microclaw init` copy-the-example path,
      which block 5 also touches. **Skipped in Block 3** as the optional item it
      is marked; carried to block 5, which owns that path. **Landed in Block 5**
      as `check-config`'s non-blocking `example_limits` diagnostic.

Post-merge design gate:

- [x] Record the error taxonomy and the offline-validation contract in design/33.
      State that offline validation checks the *document*, never the rig: a
      config can pass it and still be refused by the live cross-check.

## 4. [x] Block 14 Phase 5 — first-launch setup — **CLOSED 2026-08-02**

Branch: `design33/first-launch-setup`

The headline block. Restricted enumeration only; no agent, no mutation tools, no
inferred limits. It consumes Block 9b's versioned inventory, walks the human
through every unresolved decision, writes an **unreviewed** profile, disconnects,
and requires human review plus a normal restart. It must not introduce a second,
incompatible enumeration format.

The full behavioural contract is
`design/33-block14-phase5-dangling-impact-summary.md` §"Required Phase 5
behavior even without schema changes". Treat that section as normative and this
list as the acceptance surface over it.

Inputs and boundaries:

- [x] Consume `microclaw inspect-rig`'s inventory. Validate its `schema` against
      the shared supported-version contract from block 1 and **refuse an
      unrecognised version**. Do not wait for the Block 9b cross-rig gate — that
      needs a second live rig and could defer this block indefinitely.
- [x] Preserve the inventory's three regions (`facts`, `heuristic_candidates`,
      `human_decisions`) rather than flattening them. Never convert a
      driver-reported `technical_range` into a safety bound.
- [x] **Copy only structural identifiers and explicit operator decisions into the
      profile. Never copy observed current or allowed property values.** This is
      inherited, not preference: Block 9b emits no YAML aid at all precisely
      because a config-shaped derivative invites mistaking an observed property
      for a reviewed decision (design/33 `:373`–`:378`). Phase 5 is the sanctioned
      place where inventory becomes config, so it carries that rationale.
- [x] Always write `reviewed: false`, disconnect, and require manual review then a
      normal restart. **Do not hot-load the generated profile.**
- [x] Never expose the agent, mutation tools, or inferred authorization during
      setup.

Ordering honesty — the one place a dangling item collides physically:

- [x] Use the **least-active Micro-Manager connection path** available, and
      document every device initialization it cannot avoid.
- [x] State the ordering guarantee accurately in the setup text: *no agent- or
      tool-directed hardware action before review*, **not** "no hardware contact
      before review" (design/33 `:363`–`:369`).
- [x] Say plainly that on a rig whose emission path is undeclared, enumeration
      may initialize and emit before any config exists to gate it. Block 2's
      cross-check runs at startup and is downstream of this window; it does not
      close it.

Human-decision requirements — each must fail closed, never infer:

- [x] Require explicit operator classification of **every** illumination enable,
      emission, and power candidate the inventory surfaces. Do not claim
      heuristic discovery exhausted the physical emission paths.
- [x] Require a human decision for the unresolved M5 `iChrome-MLE-TCP.Label` /
      `State` case and any equivalent ambiguity. Emit `categorical_properties`
      whenever guaranteed mode is in force **even when empty** (design/33 `:476`)
      — that key is the surface such a decision is recorded on.
- [x] Preserve duplicate percent/native-unit actuator representations as an
      unresolved choice. Phase 2 shipped `units: native` + `full_scale`, so the
      mW variant is now expressible: the decision is *which* representation to
      declare, not "neither."
- [x] Emit exclusions or unresolved review instructions for actuator kinds the
      current schema cannot express (camera ROI; MicroFPGA pulse duration; any
      unrecognised device type). **Never invent bounds or geometry.**
      **Superseded in part by Block 4b — read the condition, not the list.** The
      exclusion was conditioned on the *schema* being unable to express the
      kind, and 4b's `bounded-numeric` removes that condition for MicroFPGA
      pulse duration. See the operator ruling recorded in block 4b.
- [x] Refuse to generate ambiguous XY typed-actuator entries; defer the proposed
      `axis` schema extension rather than guessing.
- [x] May recommend exclusion for the known `TTL.State0` GenericDevice false
      positive, but must require human confirmation.
- [x] Surface config-preset effects and typed-property collisions from the
      inventory.
- [x] Render refusals in Phase 5's own wording. Do not reuse `execute_tool`'s —
      block 3 fixes that message, but the setup flow's audience is different.

Continuous focus — hard exclusion until blocks 7a–7b land (and 7c if required).
**Reversed 2026-08-05 by block 6a.** These items shipped as written and are
correctly ticked, but the blanket exclusion they produced is what made PFS
unusable on the Nikon: one session drove a 60× oil objective 450 µm past its
recorded engage height only to find `TIPFSStatus.State` was never writable, and
another ended with the offset refused while the lock held. Block 6a replaces the
exclusion with a declaration setup can offer; block 4's ban on *inferring* a
movement policy or copying an engagement position stands, and design/40
strengthens it (four locks in one day spanned 2450–2912 µm):

- [x] **Do not classify `TIPFSStatus.State` or any equivalent continuous-focus /
      autofocus enable as a categorical property.** Emit an exclusion or an
      unresolved review instruction.
- [x] Preserve the observed relationship among core focus stage, autofocus
      device, and offset stage as a **review question**. Do not flatten them into
      independent controls and do not infer a movement policy.
- [x] Never copy an observed focus-engagement or approach position (the Nikon
      2440–2450 µm figure) into a safety bound. Where safe Z limits depend on
      objective or sample-holder context that the schema cannot express, leave
      the policy unresolved rather than collapsing it into one apparently
      universal range.
- [x] **Reworded by Block 4c, 2026-08-03 — read the correction with the item.**
      "Unsupported" here means the *movement report* is not to be trusted, **not**
      that the declaration is withheld: setup authors the offset stage under
      `named_stages` and records what the report does not promise. See Block 4c
      §"Correction to finding 1". As originally worded this item was read as a
      requirement to omit the entry, which contradicted both the impact row it
      derives from and Block 0b's shipped Nikon worksheet.
- [x] Mark PFS-offset workflows unsupported even when the offset has reviewed
      bounds, until block 6's settling work lands. **Lifted by block 6a, ahead
      of block 6** (2026-08-05): a lock the operator cannot offset is not a
      degraded capability, it is a useless one, and the marker was reached in a
      real session with PFS holding. The settling *fix* is still owed — block 6
      — but withholding the capability until it lands was the wrong trade.

Setup text must explain, not just gate:

- [x] What `max_session_illuminated_ms` actually means — the ledger is per
      process and is not durable across restarts.
- [x] That configuration edits require a restart, and why the disconnect →
      review → restart flow exists.

Coordinator review, 2026-08-01. Implementation `fb04a8e` was reviewed and
returned; `e9fa769` fixes all four findings and was verified independently
(1258 passed / 99 skipped / 3 expected warnings). The findings, kept because
they are the shape of defect this block invites:

1. The hardware-contact honesty text was printed *after* enumeration and
   disconnect, so the operator read the warning when declining was no longer
   possible. Now printed before `Core()` behind an exact typed acknowledgement.
2. Any single `enumeration_failures` row refused the whole run, including
   cosmetic coordinates (`current_value`, adapter metadata). `rig_inventory`'s
   own contract is that "one bad attribute cannot end the sweep"; the consumer
   had reinstated it, and the register predicts live M5 *will* produce such
   rows — so the M5 gate item was unreachable as written. Refusal is now
   restricted to classification coordinates; the rest become header review
   notes.
3. A validator-rejected profile was left at `--out`, and `--force` was then
   needed to retry. Now validated in an adjacent temporary and published
   atomically.
4. A bridge failure was reported as an unreadable Micro-Manager config.

Sanctioned exception to the "rig facts never in `microclaw/`" standing
constraint: `first_launch.py` matches the `ttl.state0` path shape and the
`pfs`/`perfect focus` name fragments. Both are explicitly authorized by the
item text above ("the known `TTL.State0` false positive"; "`TIPFSStatus.State`
or any equivalent"), both are shape heuristics behind a required human
confirmation or a hard exclusion, and neither carries a bound or a geometry.
Record this in the post-merge design gate rather than silently keeping it.

Rig gate — run `design/35-block4-gate-prompts.md`, **which lives on
`design33/first-launch-setup`, not here.** A gate runbook belongs on the branch
the operator checks out, so it is in front of them for the whole run and cannot
drift from the code it tests; it reaches `main` when the block merges. Block 2
kept its runbook on `main` instead, which was a mistake this file should not
repeat: the self-reference it was avoiding — a commit making the runbook's own
recorded hash stale — is already solved by pinning with `git merge-base
--is-ancestor` rather than an exact tip hash (`cf13c1a`).

Coordinator bookkeeping (this checklist, the ledger, evidence records) stays on
`main`. The runbook is the operator's working document, and it is the only
Block 4 artifact that moves.

- [x] Run on the demo core first: a complete pass producing a profile that then
      passes block 3's offline validator and, after a human sets `reviewed:
      true`, starts a session.
- [x] Run on M5 and produce a profile for a real rig with real hazards. Compare
      against the deployed M5 config and report every difference — differences
      are findings in one direction or the other, not automatic failures.
- [x] **An operator-driven transcript is required evidence** showing that an
      unresolved choice cannot be silently accepted. Automate the mechanical
      workflow checks, but do not accept a self-confirming probe as evidence of
      the human boundary — that is exactly the weakness recorded against Phase 3
      (design/33 `:796`).
- [x] Stop on any unenumerable effect or any pre-validation write.

### Rig gate round 1 — demo machine, 2026-08-01: **FAIL**

Evidence: `block4-demo-20260801-131435`. The interview completed and wrote a
profile; the profile then failed to start a session. Five findings, all returned
to implementers. Findings 1–2 are Block 4's own surface; findings 3–5 are the
startup validator, which Block 4 merely exposed — **the gate cannot pass while
they stand, so they are in this block's scope.**

1. **`Start-Transcript` captured nothing.** Windows PowerShell 5.1 transcripts
   record the PowerShell output stream, not a child process's console writes, so
   every `g*.txt` is an empty shell. The operator had to hand-produce
   `*-copy-paste.txt` files for the run to be readable at all. The runbook chose
   `Start-Transcript` deliberately (`:25`) because redirecting an interactive
   session hides the prompts — correct reasoning, wrong remedy. **Microclaw must
   write its own interview transcript**; non-interactive invocations use
   `> file.txt 2>&1`.
2. **The interview asks ~90 questions, explains nothing, and offers no
   defaults.** The operator was asked to classify every `Camera.TestProperty1..6`
   with no statement of what "categorical" or "excluded" mean. **Micro-Manager
   already answers most of this**: `is_property_read_only`, `is_property_pre_init`,
   `get_allowed_property_values`, `has_property_limits`, and the technical range
   are what the Device Property Browser renders as enabled/disabled and as a
   combo box vs a slider. The inventory already records all of them
   (`rig_inventory.py:170`–`:185`). Setup must use them as **defaults**, reserving
   type-it-yourself refusal for the genuinely hazardous decisions.
3. **A continuous actuator must not prevent launch.** Nineteen properties the
   operator had classified `categorical` were rejected at startup as "a known
   continuous actuator … cannot be classified as categorical"
   (`authorization.py:790`), each one fatal. Many actuators are continuous and are
   fine within an appropriate range. Fail closed **on the property, not on the
   process.**
4. **A missing EMU configuration must not prevent launch.** `_has_emu`
   (`emu_manager.py:75`) returns true when `Emu.jar` is merely *present* — and it
   ships with every Micro-Manager. So the "this is a non-EMU rig" early-out at
   `authorization.py:94` is unreachable on a stock install, and any rig without
   `EMU/config.uicfg` is refused. **Almost no one uses EMU**; M2 and M5 are the
   rare exceptions. Jar presence is not evidence that a rig is EMU-configured.
5. **Generalise: none of these should have blocked launch.** Every refusal in
   this run was a claim the config made that the rig could not corroborate —
   never an undeclared hazard. A claim the rig cannot corroborate must be
   **dropped and reported**, which strictly narrows authority. Block 2's
   undeclared-emission-path gate is the opposite shape and stays fail-closed.

Round-1 assignments: 1–2 → `design33/first-launch-setup` (the block branch);
3–5 → `design35/startup-refusal-severity`, branched from it and merged back
before the re-gate.

### Round 2 — pushed 2026-08-01, awaiting the rig

Both implementations were rejected once and accepted on their second pass.
Coordinator-measured results on the **real** round-1 demo inventory, not a
fixture — a synthetic fixture with invented technical ranges produced a wrong
diagnosis during round-1 review and cost a cycle; measure against
`block4-demo-20260801-131435/demo-inventory/inventory.json`:

- Interview: **~90 → 24 questions**, all of them hazard or budget questions
  (10 acquisition budgets, 6 stage travel bounds, 4 illumination ON/OFF, 2
  illumination candidates, 2 bulk-accept overhead). Zero property questions and
  zero preset questions remain.
- Generated profile: 42 categorical, 0 typed actuators, 34 excluded; passes the
  offline validator, and validates with **zero** blocking diagnostics once the
  operator sets `reviewed: true`. This is the round-trip round 1 could not do.
- Startup: the round-1 failure shape (13 present-and-continuous properties
  declared categorical, absent `channels.allowed` presets, `Emu.jar` with no
  `config.uicfg`) now **starts**, demotes all 13 via the continuous path, and
  refuses the write at both gates.
- Merged tree `385049d`: 1279 passed / 99 skipped / 3 expected warnings — the
  exact sum of the two branches, no regressions.

**Open design gap — scoped as Block 4b, not fixed here.** The schema has exactly
two typed-actuator kinds, `absolute-position` (units `um`) and
`illumination-power` (`percent`/`native`) — `safety.py:76`, `:580`. A bounded
numeric like `Camera.Gain` fits neither, so setup excludes every such property
rather than inventing a unit, which is what the block's own item text requires
("never invent bounds or geometry"). `Camera.Gain` is therefore unreachable
after setup, and gain is a basic imaging control.

Two corrections to the first statement of this gap, both worth keeping because
they changed the scope:

- **Exposure is not part of it.** It is already adjustable via `set_exposure`
  (`tools.py:211`) under `check_exposure` against `camera.max_exposure_ms`; only
  the *raw property* route is blocked, deliberately, because exposure is
  dose-bearing and belongs on the metered path.
- So "no continuous property is writable at all" was wrong. The accurate
  statement is that no continuous property is writable **as a raw property
  write**, and the one genuinely unreachable control is gain.

Resolved 2026-08-01: add a `bounded-numeric` kind (Block 4b) and run this gate
now rather than folding a `safety.py` schema change into it.

### Rig gate round 2 — demo machine, 2026-08-01: **PASS, with nine findings**

Evidence: `block4-demo-20260801-144809`. Every mechanical gate the demo machine
can run passed. G0 identity pinned both round-2 commits by `merge-base`
(`contains-interview-fix.txt`, `contains-severity-fix.txt` both `0`). G1
produced a profile that the offline validator refused while `reviewed: false`
and passed once reviewed, and the reviewed profile started a live session. G2
and G3 each printed `False` for the must-not-exist artifact, so an unresolved
choice cannot be silently accepted and the acknowledgement gate exits before
connecting; G5 rides on G2. G6 showed the round-1 refusal shape demoting instead
of refusing — `Camera.Exposure` declared categorical and eleven absent
`channels.allowed` presets were dropped, and startup continued. Round-1 finding
1 is also fixed: Microclaw wrote its own UTF-8 interview transcripts, and they
are the only readable record of the run.

**M5 (G4) has not been run.** It is deliberately held until round 3 lands, so
the one trip to a real rig with real hazards is not spent on an interview we
already know we are changing.

The interview is now 24 questions, and the operator's finding is that most of
what remains is still asking a human for something Micro-Manager already knows.
Nine findings, all round 3, all on `design33/first-launch-setup`:

1. **Illumination ON/OFF values have no defaults.** MM reports the allowed-value
   domain for `Core.AutoShutter` and `White Light Shutter.State` (`0, 1`);
   propose min as OFF and max as ON, Enter-acceptable. The *classification*
   question (e/p/x/u) keeps having no default. Note the operator inverted the
   naive proposal for `Core.AutoShutter` (ON=`0`, OFF=`1`), so the proposal must
   be plainly overridable and the transcript must record which was used.
2. **XY and Z travel have no defaults.** Propose the driver-reported limits where
   MM reports them, with explicit confirmation. Where MM reports none — which is
   the demo rig's case for both stages — say so in the prompt and require the
   human value, rather than asking a bare question the operator cannot source.
3. **Maximum camera exposure has no default.** MM reports `Camera.Exposure`
   limits (`0`–`10000` ms on the demo rig); propose the upper limit.
4. **`max_bytes` should be derived, not asked.** Compute it from the answered
   frame cap and the camera's own full-frame geometry and pixel depth.
5. **`max_illuminated_ms` is mis-explained.** It is per *plan*, not per frame —
   `AcquisitionPlan.illuminated_ms` is `frames × exposure_ms_per_frame`
   (`acquisition.py:20`). The label must say so, and the default follows from the
   caps already answered rather than from one frame's exposure.
6. **`max_session_illuminated_ms` is not understood, and probably should not
   bind.** It is a cumulative illumination-dose ledger across every acquisition
   in one Microclaw *process*, reset on restart — not a sample-lifetime dose cap.
   The parenthetical says "not durable across restarts" without ever saying what
   it accumulates. Operator decision, 2026-08-01: **do not make an operator who
   has been imaging for a while restart to keep working.** A cap whose only
   remedy is a restart that resets it to zero is not a dose guarantee; its one
   real function is a brake on a runaway in-process loop, and that brake must not
   fire on a legitimate long session. The coordinator's first proposal — default
   it to one worst-case acquisition per process — was exactly the failure mode
   the operator is describing and was withdrawn. There is no answered quantity
   that says how many acquisitions a sitting contains, so no derivation exists;
   round 3 proposes a stated generous anchor instead and says it is an anchor.
   **Whether the key deserves to exist at all is Block 4b's**, with the other
   `safety.py` budget decisions.
7. **Budget questions are ordered by kind, not by subject.** Each hard cap and
   its human-confirmation threshold must be adjacent.
8. **The raw-byte confirmation threshold should not be a question.** Frames and
   duration already gate the same quantity. Guaranteed mode requires all nine
   `_ACQUISITION_POLICY_FIELDS` to be finite and positive
   (`authorization.py:1056`), so round 3 derives the value instead of asking;
   **deleting the key from the schema is deferred to Block 4b**, which is the
   block already opening `safety.py`.
9. **`microclaw` without `serve` cannot reach the API.** The operator ran the
   runbook's command, reached the REPL, and the first instruction died in
   `TypeError: Could not resolve authentication method`; adding `serve` worked.
   This is the carried-forward design/15 item — `run_session` never calls
   `credentials.load_api_key`, so a browser-stored key is invisible to the REPL.
   The register said "fold into block 3 or 5 if cheap"; it has now cost a gate
   step, so it lands here. The runbook's command is wrong either way and is
   corrected on the branch.

**Scope change the operator made deliberately, recorded here because it reverses
a standing rule.** Findings 2 and 3 make driver technical ranges the *proposed
defaults* for stage travel and maximum exposure — the two most hazardous axes in
the file — where this block's own item text says never to convert a
`technical_range` into a safety bound. The existing post-merge item already
scoped that rule for classification metadata; it now also covers hazardous-axis
bounds, on these conditions: the value is labelled as the driver's technical
range and not as a reviewed bound, Enter-accepting it is an explicit operator
act, and the transcript records accepted-default versus typed-value per answer.
`_bounds` (`first_launch.py:186`) already does exactly this for typed
absolute-position actuators, so round 3 extends a shipped pattern rather than
inventing one. Design/33 and the block item text must be corrected to what
ships.

The coordinator raised the reversal with the operator, who reaffirmed it the
same day: a driver range is a reasonable thing to *propose*, the confirmation
question stays, and proposing it does not hurt. Recorded as a settled decision,
not an open question — do not re-litigate it in a later block.

### Round 3 — pushed 2026-08-01, awaiting the rig

Implementation `3a9522d` was reviewed and returned; `55b277c` fixes all three
findings and was verified independently (**1294 passed / 99 skipped / 3 expected
warnings**, from a round-2 baseline of 1279). Runbook re-pinned at `0ab9055`;
both `--is-ancestor` checks confirmed green before the push.

Measured on the **real** captured round-2 inventory, not a fixture: 24 → 23
questions replaying that file, and 22 once it is augmented through the new
producer geometry code. Every remaining question is a hazard or budget question;
six of them are the stage-travel bounds the demo rig genuinely cannot source
(`Z.Position` reports `has_limits: false` and the `XY` device exposes no position
property at all), and those now say so in the prompt instead of asking blind.

No safety-config schema change; `INVENTORY_SCHEMA` stays at
`microclaw.rig-inventory/v2` with camera geometry added as optional `facts`,
deliberately excluded from the fingerprint because current ROI and binning are
acquisition state rather than rig identity.

The three round-3 review findings, kept because two were invisible on the demo
rig and would have first appeared on M5:

1. **The byte cap ignored binning.** `unbinned_full_frame_pixels` was computed in
   the producer, tested, and never read by the consumer, which used the current
   *binned* dimensions. `estimated_bytes` is computed at plan time from live
   geometry (`tools.py:4487`), so a cap frozen at setup-time binned size refuses
   legitimate full-frame work after a binning change — 4× too small at binning 2,
   16× at binning 4. The demo rig runs binning 1, so no demo evidence could have
   caught it.
2. **The API key was resolved after connecting and after `validate_live_rig`.**
   Round-1 finding 1's exact shape: a precondition needing no hardware checked
   after hardware contact. Now resolved before the controller is constructed, and
   the test asserts the controller was never built rather than merely that the
   REPL was not reached.
3. **Numeric proposals emitted no accepted-versus-overridden audit line** —
   present for ON/OFF values, absent for stage travel and maximum exposure, which
   are the two hazardous axes the audit condition exists for. Now emitted by both
   `_positive_default` and `_bounds`.

### Rig gate round 3 — demo machine, 2026-08-01: **PASS (G1–G3), two fixes made**

Evidence: `block4-demo-20260801-161051`, run at `0ab9055` with both pins `0`.
G1 round-tripped: profile written, refused by the validator while unreviewed
(exit 1), passed once reviewed (exit 0). G2 and G3 both printed `False`. G6 was
skipped deliberately — `authorization.py` is byte-identical across all of round
3, so the round-2 demotion evidence still stands.

Every round-3 feature fired in the transcript: `PROPOSAL ACCEPTED` audit lines on
both illumination ON/OFF pairs and on the exposure default, `LIMIT SOURCE` lines
naming the devices whose travel limits MM does not report, and the byte
derivation on the **unbinned** basis (`512 × 512 × 2 × 5000`).

Two findings, both fixed by the coordinator on the branch as small corrections
(`49a2c28`), suite re-measured at **1295 passed / 99 skipped / 3 expected
warnings**:

1. **A mandatory question the operator could not interpret disabled a safety
   control.** `confirm_above_illuminated_ms` had no default, and the generated
   profile records `5000000000000000` — a number typed to make the question go
   away. That is the *only* confirmation measuring light on the sample, so the
   effect of an unexplained question was to switch off the dose warning. It now
   proposes `min(60 s of continuous shutter-open, max_illuminated_ms)`.
   **Deliberately not removed, unlike the byte threshold:** bytes are frames ×
   fixed geometry, so the frame threshold already covers them, but illuminated
   time is frames × *exposure*, and a few frames at a long exposure trip no frame
   count. A default derived from `confirm_above_frames × max_exposure_ms` was
   rejected for exactly that reason — it would never fire first, making the field
   inert. The corresponding entry in the blank-refusal test moved to the
   defaulted set with the reason recorded inline.
2. **`get_roi` returned a non-iterable `java.awt.Rectangle`.** Recorded as
   `'java_awt_Rectangle' object is not iterable` in `enumeration_failures`, so
   every inventory carried `roi: null` and the derivation line printed
   `ROI None`. This is the house bridge-collection failure mode. `_roi_result`
   now reads the rectangle's public fields before falling back to iteration.
   Non-blocking throughout — the byte derivation never depended on the ROI.

The count stays 22 on a fresh inventory; what changed is that one fewer question
requires the operator to invent a number.

### Rig gate G4 — M5, 2026-08-01: **completes, profile validates, five findings**

Evidence: `block4-m5-20260801-162818`, run at `27211c1` with both pins `0`. Setup
enumerated a real rig with three iBeam lasers, a four-line iChrome MLE, a
Hamamatsu camera, and an ELL6 slider; the generated profile passed `check-config`
(exit 0). No refusal, no crash, no pre-validation write. **The block's hard
question — can an operator produce a working profile for a hazardous rig without
hand-authoring it — is answered yes.**

What M5 exposed is that the proposal machinery is keyed on the wrong evidence
field, and that the illumination menu has no exit for a non-illumination device.
Five findings, round 4:

1. **`_on_off_proposal` reads only `allowed_values`, so it is blind on M5's
   biggest device.** Every iChrome enable/emission property reports
   `allowed_values: []` with `technical_range 0.0–1.0` and `reported_type
   Integer` — that is how `_is_enable` catches them in the first place
   (`rig_inventory.py`, the `span.lower == 0.0 and span.upper == 1.0` branch).
   Result: **22 hand-typed `1`/`0` answers**, each logged `no proposal was
   available`. Single largest question-count win in the block.
2. **`full_scale` was misread as a minimum, and produced a wrong dose
   declaration.** The operator typed `0`, was refused ("finite number greater
   than zero"), and typed `0.01` to get past it. The profile now declares
   `iBeamSmartCW-1.Power (mW)` with `full_scale: 0.01` beside two physically
   identical lasers at `75.0`. MM reports `technical_range 0.0–75.0` for that
   exact property, so the correct value was available and unused. The error
   direction is fail-closed — canonical percent inflates, so writes are refused
   rather than under-metered — but the operator meets it as a mystery refusal.
   The operator's question "why can I not set a laser's power to 0" is answered:
   nothing asked for a minimum, and 0 is always writable
   (`illumination_to_percent(0) == 0`). The question needs a default and a label
   that says it names the value equal to 100% output.
3. **The illumination menu has no route for "not illumination, but I need it".**
   `Thorlabs ELL6.State` is a lens slider — a 1-D stage, no illumination effect.
   It surfaced as an illumination candidate because `_ENABLE_NAME` matches
   `\bstate\b`, and the only options were emission/power/exclude/unresolved. The
   operator chose `u`, so it is excluded. Mitigating fact worth recording: the
   same device's `Label` property (`Position 0`/`Position 1`) *was* bulk-accepted
   as categorical, so the slider is controllable — the two are duplicate
   representations of one mechanism. The menu gap is still real.
4. **Operator decision: default the classification to emission/enable** when the
   property name matches `laser|power|emission|enable` *and* the value domain is
   exactly two on/off-shaped values. Deliberate third loosening of "require
   explicit operator classification of every illumination candidate". Accepted
   because the error direction is toward *more* gating, and the two-value
   condition means a continuous power property can never take the default. Note
   the rule is narrower than `_ENABLE_NAME`: `state`, `shutter`, `operation`, and
   `output` are excluded, so `Thorlabs ELL6.State` correctly gets no default.
5. **Percent-versus-native was asked 13 times with no default** although the
   property name carries the answer: `Fine A (%)` is percent, `Power (mW)` is
   native. `_TRAILING_UNIT` in `rig_inventory.py` already parses that suffix.

### Round 4 — pushed 2026-08-01, awaiting the re-gate

Implementation `cc34ab5` accepted on the first pass — the first round of this
block where nothing had to be returned. Coordinator-verified at **1305 passed /
99 skipped / 3 expected warnings**, then **1307** after two small corrections
(`ac8d909`). Runbook re-pinned at `b2f30c9`, both `--is-ancestor` checks green.

Measured on the real M5 inventory: the prompt count is unchanged, but the answers
an operator must **type** fall from 83 to roughly a quarter of that — the
remainder are now Enter-acceptable proposals. The demo inventory is unchanged at
22 prompts and 12 typed, and its generated profile is byte-identical in shape (42
categorical, 0 typed actuators, 34 excluded). Independently re-derived here:
`iBeamSmartCW-1.Power (mW)` now takes `full_scale: 75.0` from the driver range,
which is the specific wrong declaration the M5 run produced by hand.

One interaction checked before accepting, because it would have made finding 3's
fix useless: **the live undeclared-emission refusal keys off the EMU laser map
(`_live_emu_laser_enables`, `authorization.py:692`), not a name regex.** So a
candidate reclassified out of the illumination set with the new `o` option
becomes an ordinary categorical property without tripping Block 2's gate.
`Thorlabs ELL6` is not an EMU slot, so the ELL6 slider is now controllable.

Two coordinator corrections on top of the accepted implementation:

1. `_power_units_default` matched only bracketed or parenthesised suffixes, so
   M5's four `Laser N: 3. Level %` properties — a bare trailing percent, and the
   rig's real 402 nm activation line — still had no default. Handled in the
   consumer only, deliberately **not** by widening `_TRAILING_UNIT`: that regex is
   the producer's duplicate-representation detector and needs a delimited suffix
   to split a base name on.
2. `test_every_hazardous_field_still_refuses_blank` asserted total refusals
   against `len(required)`, which held only by coincidence and broke the moment a
   field gained a default. Rewritten to assert the intent — every field still
   expected to refuse a blank produced a refusal — so the next default does not
   look like a regression. The widened paren-suffix behaviour also gained a
   producer-side test, where it lives.

### Round 4 field failure — M5, 2026-08-01: **the profile would not move a filter wheel**

Evidence: `block4-m5-20260801-173136`. The gate itself passed and the generated
profile validated, but a normal session under it refused
`Thorlabs Filter Wheel.State` with "excluded from the authorization map"
(`20260801_173733_005364_..._filter-wheel-refused_history.jsonl`). The rig became
less capable than it is under its hand-authored config, which no static gate step
had asked about.

**Cause, and it is not the auto-classification vacuum it first looks like.**
Micro-Manager publishes `allowed_values` for a StateDevice's `Label` and never
for its `State` — the state is an integer position. `_metadata_default` therefore
fell through to "no discrete value domain or numeric limits" and wrote an
*explicit* exclusion, which is a stronger statement than silence: design/33 Block
3b's auto-classifier fills vacuums only, and setup had stopped leaving one. The
domain was never missing; the inventory records it as the device's
`state_labels`, which setup did not read.

Scope on M5: four StateDevices — both Thorlabs filter wheels, the ELL6, and
`iChrome-MLE-TCP`. The deployed hand-authored config declares the first three
categorical. On the demo rig the same rule had excluded six more: Dichroic,
Emission, Excitation, LED, Objective and Path `.State`, each one a selector whose
`.Label` was already categorical.

Fixed by the coordinator at `5c82d1e` (1309 passed / 99 skipped / 3 expected
warnings). A StateDevice's `State` with no allowed values but N state labels now
defaults to categorical over positions 0..N-1.

**One deliberate exception, because Block 3b's rig gate already caught this exact
widening once.** `iChrome-MLE-TCP` is a StateDevice *and* a laser engine — it
surfaced illumination candidates, and the deployed config pointedly does not
declare its `State`. A StateDevice position on a device that surfaced any
illumination candidate is therefore never accepted in bulk; it is asked every
time, with the reason in the prompt. Filter wheels take the proposal, laser
engines ask.

**Process finding, coordinator's own.** `g4-vs-deployed.diff` in the *previous*
M5 bundle already contained this: `- {device: Thorlabs Filter Wheel, property:
State}   # 6 pos: Filter-1..6` against `+# MM METADATA EXCLUSION: ... no discrete
value domain`. The gate asks for every generated-vs-deployed difference to be
explained in one direction or the other; the bundle was reviewed without opening
that file, and it cost a rig session. The runbook now says to read the exclusion
comments and not only the declarations.

**New gate step G4b.** Every earlier step was static — generate, validate,
compare. None of them asked whether the profile can actually run the rig, which
is why a profile that passed every check could not move a wheel. G4b sets
`reviewed: true` on a copy, starts a session, and moves a filter wheel by both
`State` and `Label`. Operator-confirmed **PASS on M5, 2026-08-01**: regenerated
from the captured `b2f30c9` inventory via `--inventory` (no second pre-config
enumeration window), and the filter wheel moved. Outstanding before merge: the
evidence bundle, and the deployed-vs-generated comparison actually read this
time — the StateDevice entries moving to categorical should narrow it.

### The deployed-vs-generated review, 2026-08-01 — three more findings

Evidence: `filter-wheel-moved` (session history plus `m5-profile2.yaml`). The
wheel move is real — `set_device_property Thorlabs Filter Wheel.State = '3'`
returned a success status, so the write cleared the authorization map. G4b holds.

Then the comparison that should have run a round earlier produced three things.

1. **`Core.*` device-assignment properties were declared categorical — an
   authorization hole, fixed at `fa9e2ee`.** The generated profile made
   `Core.Camera`, `Core.Focus`, `Core.XYStage`, `Core.Shutter`, `Core.AutoFocus`,
   `Core.Galvo`, `Core.ImageProcessor`, `Core.SLM`, `Core.ChannelGroup` and
   `Core.Initialize` writable. These name *which physical device* fills each
   role. M5 offers four devices for `Core.Focus` (`PIZStage`, `SmarAct 1D`,
   `Thorlabs ELL17/ELL20`, `Thorlabs ELL20`), and `stage.z_min/z_max` are
   enforced against whatever Core says the focus device is — so one categorical
   write re-aims every reviewed bound at a different mechanism. `Core.Shutter`
   likewise moves Block 2's illumination gate and `Core.ChannelGroup` changes
   what `channels.allowed` names. Now never writable. `Core.AutoShutter` is
   deliberately excluded from the rule: it is a real illumination control.
2. **`Thorlabs ELL6.State` should never have surfaced as an illumination
   candidate, fixed at `b67fd08`.** The `o` option added in round 4 was a
   band-aid: it made the operator undo a bad guess. Root cause is `_ENABLE_NAME`
   matching the bare word `state`, which drags every wheel, turret and slider
   into the illumination interview. A StateDevice's `State` reporting state
   labels is its position, so it no longer matches. **Measured on both captured
   inventories before changing the heuristic: exactly one candidate is dropped
   across both rigs — the ELL6 false positive — and all 21 genuine M5 gates plus
   the demo's `White Light Shutter.State` (a ShutterDevice, no state labels) are
   retained.** The pattern is not narrowed for any other device type.
3. **The forced question on `iChrome-MLE-TCP.State` was unanswerable, so it now
   fails closed instead.** Round 4 made a StateDevice position on an illuminating
   device refuse bulk acceptance and ask. M5 answered by accepting the proposal,
   widening authority on a laser engine — because the labels are `State-0`,
   `State-1`, `State-2`, which say nothing about what the positions do. Same
   failure mode as the `5e15` dose threshold: **an unanswerable mandatory question
   produces a worse outcome than a default.** Such a position is now excluded with
   the reason printed, and stays revisitable by exact name.

Two operator corrections to record, both accepted:

- **The deployed M5 config's provenance is disputed.** This file and the runbook
  both called it hand-authored from `safety_config.example.yaml`, and the runbook
  argued the comparison was strong "precisely because the two were produced by
  different means". The operator states it was generated by an earlier Microclaw.
  The comparison is therefore a **difference-finder, not an oracle** — agreement
  between the two files is not corroboration. Finding 1 is unaffected: it stands
  on its own argument and would be a defect with no deployed config at all.
  Corrected in the runbook at `9e9dc81`; design/33 must not repeat the claim.
- **The operator wants maximum reach: "control everything on the microscope,
  safely."** This is the standing goal for the remaining usability work, and it
  reframes exclusion-by-default as a cost rather than a free safety win. Block 4b
  (`bounded-numeric`) is part of the answer; the classification rules above are
  another. Where evidence establishes a domain, prefer reach; where it does not —
  `State-0/1/2` on a laser engine — prefer exclusion with a printed reason and a
  named way back in, never an unanswerable question.

Suite at `b67fd08`: **1320 passed / 99 skipped / 3 expected warnings.**

Post-merge design gate:

- [x] Update design/33 with the setup contact semantics actually measured, every
      unavoidable device initialization observed, the decisions the flow demands,
      the cases it refuses, and the exact ordering guarantee. Retire the "later
      work" framing at `:347`.
- [x] Tick the two Phase-wide Block 14 rows carried into block 1.
- [x] **Reconcile the "never a default" rule with findings 2 and 3.** MM's own
      writability and value-domain metadata *is* the classification default.
      Driver technical ranges are also proposed for hazardous bounds, including
      stage travel and maximum exposure, but are labelled as technical ranges,
      require an explicit Enter acceptance, and record accepted-default versus
      typed override in the audit transcript. Observed current values remain
      barred. This is the scoped rule that shipped.
- [x] **Record the startup refusal-severity taxonomy** from findings 3–5 in
      design/33 §authorization: which diagnostics refuse the process, which
      demote a claim and warn, and the rule that decides. Four specific
      contradictions the implementer flagged, all to be corrected to what
      shipped in `design35/startup-refusal-severity`:
      1. design/33 says a categorical raw write proven continuous is a startup
         error; it now demotes to `excluded` and startup continues.
      2. It describes declared/live property mismatches generally as startup
         failures; an absent declared device or property now demotes.
      3. Its channel-validation framing implies any preset mismatch fails
         startup; an absent preset name now demotes, while an unreadable or
         unsafely-expanding *present* preset still refuses.
      4. It does not distinguish the EMU plugin being installed from the rig
         being EMU-configured. `Emu.jar` presence is now only a path-location
         hint (`_has_emu`); `EMU/config.uicfg` establishes EMU use
         (`_has_emu_config`).
      Defer writing this until the re-gate passes, so the taxonomy and the
      measured rig evidence are reconciled in one pass rather than twice.

**Block order after 4: 4b, then 4e, then 4c, then 4d, then 5.** The operator
set 4b/4c/4d/5 on 2026-08-02 and inserted **4e ahead of 4c on 2026-08-03**,
after M2's gate found two live emission-path defects: the stage work should
not be built on top of a known illumination gap. Both 4b and 4c come before 5; the operator's earlier "4c before 5"
ordered those two only and did not move 4c ahead of 4b. **They must not run
concurrently** despite looking independent: 4b adds the `bounded-numeric`
default to the interview and 4c adds per-stage travel questions, so both edit
`first_launch.py`.

**Scope split, operator ruling 2026-08-02 — the schema rename leaves 4b and
becomes Block 4d.** Commit `05d5751` had scoped `rig_profile` →
`property_authorization` into 4b alongside the new typed-actuator kind. Both are
real, but one branch cannot carry them: the rename breaks every deployed config
including M5's, so a failed 4b rig gate ("the gain write was refused") would be
ambiguous between the new kind and the renamed keys, and rolling back either
would drag the other — exactly the branch-boundary rule in `CLAUDE.md`. 4b now
ships the kind and the two acquisition-policy decisions; 4d ships the rename,
after 4c, before 5. The design/33 schema map (which section owns which write
path) is owed regardless and stays where it was assigned, in Block 4's
post-merge design gate.

## 4b. [x] The `bounded-numeric` typed actuator — gain and its kin — **MERGED 2026-08-03**

Branch: `design33/bounded-numeric-actuator`. Depends on Block 4 merging.
Assign against a clean `main`, not against the block 4 branch — decided
2026-08-01 rather than folding it into the re-gate, because it changes
`safety.py`, which every downstream block depends on.

Found by Block 4's round-2 review. Setup excludes every bounded numeric because
the schema has exactly two typed-actuator kinds — `absolute-position` (units
must be `um`) and `illumination-power` (`percent`/`native`), `safety.py:76`,
`:580`. `Camera.Gain` fits neither, so it is unreachable after first-launch
setup. Gain is a basic imaging control: it sets the amplification applied to the
detected signal, and an operator who cannot set it cannot balance signal against
read noise and clipping.

**Exposure is deliberately not in scope, and must stay out.** It is already
adjustable through `set_exposure` (`tools.py:211`), gated by
`SafetyGuard.check_exposure` against `camera.max_exposure_ms` (`safety.py:816`),
and `_known_continuous_raw_pair` (`authorization.py:307`) blocks the raw
`<camera>.Exposure` property route on purpose. Exposure is dose-bearing —
longer integration means more light on the sample — so it belongs on the metered
path. Gain is post-detection amplification and is not dose-bearing, which is
exactly why it needs a kind that feeds no ledger.

- [x] Add `kind: bounded-numeric` to the typed-actuator schema, requiring
      `units` (an operator-supplied string, **recorded and echoed, never
      interpreted**), `minimum`, and `maximum`. The kind names the safety
      contract, matching the existing two: `absolute-position` clamps and feeds
      stage bounds; `illumination-power` clamps and feeds the dose ledger;
      `bounded-numeric` clamps and feeds nothing.
- [x] Clamp-only enforcement in `SafetyGuard.check_typed_actuator`: no canonical
      conversion, no `full_scale`, no ledger participation. A write outside
      `[minimum, maximum]` is refused; a write inside it is permitted.
- [x] **Hard refusal if the declared pair aliases a built-in capability** —
      `_known_continuous_raw_pair` (focus position, XY, camera exposure) or any
      `illumination_pairs` member. Without this the new kind is a backdoor
      around `camera.max_exposure_ms`, the stage bounds, and the illumination
      ratchet. This is the single most important test in the block.
- [x] First-launch setup: a bounded numeric MM characterises (`has_limits` plus
      a numeric `reported_type`) defaults to this kind, with MM's technical
      range as the Enter-acceptable default bounds and the operator supplying
      the unit. This is what Block 4 could not express and had to exclude — it
      restores the round-1 instruction that continuous actuators are fine to use
      over an appropriate range, without inventing a physical kind.
- [x] Decide and document guaranteed-vs-degraded behaviour for a
      `bounded-numeric` whose live driver range is narrower than the declared
      bounds. Follow the existing typed-actuator precedent (`authorization.py`
      rejects typed bounds exceeding the driver technical range) rather than
      inventing a new rule.
**Both acquisition-policy decisions below were settled by the operator
2026-08-02: deprecate, do not delete.** Deleting either key stops every deployed
config that sets it — M5's included — from loading, which is a rig outage
bought for a schema tidy. The implementer executes the decision; it is not
reopened.

- [x] **`acquisition.confirm_above_bytes` — accepted-but-ignored deprecation.**
      Deferred here from Block 4 round 3 finding 8, because this is the block
      already opening `safety.py`. The operator's position is that it should not
      exist: an estimated byte count is a function of frames and geometry, so the
      frame and duration confirmations already gate it. Round 3 only stopped
      *asking* for it (guaranteed mode requires all nine
      `_ACQUISITION_POLICY_FIELDS` finite and positive, `authorization.py:1056`,
      so it is derived to a non-binding value instead). Ship: the key still
      parses and still validates if present, it leaves the guaranteed-mode
      required set, and it no longer gates in `tools.py:112`. A config that sets
      it keeps loading; first-launch setup stops emitting it, and
      `safety_config.example.yaml` marks it deprecated rather than dropping it
      silently. `max_bytes` itself stays; it is a hard cap and round 3 derives
      its default.
- [x] **`acquisition.max_session_illuminated_ms` — optional, with the brake
      framing stated.** Deferred here from Block 4 round 3 finding 6 for the same
      reason as the byte threshold. The operator's position is that it should not
      force a restart on someone who has been imaging for a while, and that a cap
      reset by restarting the process is not a dose guarantee. Its one defensible
      function is a brake on a runaway in-process loop
      (`AcquisitionLedger.reserve`, `acquisition.py:34`). Ship: guaranteed mode
      stops requiring it; unset means no session ledger cap and that must be
      stated where the operator can see it; when it *is* set, both the config
      comment and the refusal message say plainly that this is an in-process
      runaway brake and that a restart resets it to zero. Do this in one pass
      over `_ACQUISITION_POLICY_FIELDS` with the byte threshold, and say in the
      block report what the required set now contains.
- The `rig_profile` → `property_authorization` **rename left this block on
  2026-08-02 and is now Block 4d.** See the scope-split note above block 4b. Do
  not rename anything here; `bounded-numeric` lands under the existing
  `rig_profile.typed_actuators` key, and 4d moves it with the rest.

**Operator ruling, 2026-08-02 — MicroFPGA trigger duration belongs in this
kind, and the coordinator was wrong to return it as a required exclusion.**
Round 1's review cited block 4's "actuator kinds the current schema cannot
express (camera ROI; MicroFPGA pulse duration; …)" as forbidding
`Laser Trigger.Duration0`–`Duration3 (us)`. That misreads the item: the
exclusion is conditioned on schema inexpressiveness, and **`bounded-numeric` is
the removal of that condition.** An item that excludes something *because it
cannot be expressed* stops applying in the block that expresses it.

The physics, from the operator and corroborated against
`https://mufpga.github.io/principle_trigger.html`: trigger duration sets how
long the laser is on within a camera exposure, so it is an illumination control
layered on top of power, and it must be adjustable independently of the other
illumination parameters. One qualifier recorded for whoever sets the bound: in
`FOLLOW` mode the laser tracks the exposure signal, so laser-on time cannot
exceed the frame; in `RISING`/`FALLING` mode the doc describes a pulse of
`duration` µs on each edge, which is not inherently clipped at the end of the
exposure window. Its bounds therefore follow the settled proposed-default
precedent used for stage travel and maximum exposure — driver range offered,
Enter-acceptable, accepted-versus-typed recorded in the transcript — and are not
given a special case. The same ruling covers `Laser Trigger.Sequence0`–`3`,
`Servos.Position0`–`3` and `PWM.Position0`.

- [x] Off-rig tests: the alias refusal above; clamp at both edges and outside;
      a declared unit round-tripping into the profile unaltered; setup emitting
      the kind from the real demo inventory fixture; and a regression that
      `Camera.Exposure` still cannot be declared `bounded-numeric`.

Rig gate — **split into a merge-blocking part and an owed part, operator
decision 2026-08-02.** The original single item required `Camera.Gain` "on the
demo machine and on a real camera". That cannot be run as written: M5's
Hamamatsu exposes **no gain property at all** (verified across the whole
`block4-m5-20260802-100850` inventory — zero matches for any gain-like name),
and the demo camera is simulated. The real camera with a gain control is M2's
Andor iXon, and M2 access is not schedulable.

The precedent for merging anyway is in this file already: block 4 refused to
wait on Block 9b's cross-rig gate because "that needs a second live rig and
could defer this block indefinitely". Same situation, same answer — but the
owed run is tracked in the carried-forward register, not dropped.

Merge-blocking, on hardware we can reach:

- [x] **Demo machine — the gain path end to end.** Generate a profile declaring
      `Camera.Gain`, set it through Microclaw inside the declared range, and set
      it outside; show the second is refused by the clamp and the first is not.
- [x] **M5 — the mechanism on real hardware.** A bounded-numeric write must be
      shown to reach a real device, clamp at its declared bound, and refuse
      outside it. **In-range PASS; the refusal step was retired during the run**
      (ledger row 4b) — the clamp half is proven on M5, the refusal half on demo and
      M2. Any declared bounded numeric whose effect can be read back
      qualifies; `SmarAct 2D.Hold time (ms)` is the least invasive candidate (no
      light, no motion, trivially read back), with `Laser Trigger.Duration0 (us)`
      as the alternative if a control the operator already exercises is
      preferred. This step exists so that merging without M2 does not leave the
      kind unproven on real hardware — only *gain* waits.
- [x] Confirm no exposure or illumination path became writable as a side effect,
      on both machines.

Owed, does **not** block 4b's merge or blocks 4c/4d/5:

- [x] **M2 (Andor iXon) — gain on a real camera.** Set EM gain inside and
      outside the declared range, show the second is refused, and capture an
      image at two gain settings confirming the change is visible in the data.
      Run it when M2 is next available.

**What merging without the M2 run does not claim.** That a `bounded-numeric`
declaration produces a *visible physical change in acquired data* on a real
camera is unproven until M2 runs. The clamp, the refusals, the alias guards and
the setup emission are all proven without it. Do not write the stronger claim
into design/33's post-merge record.

### Rig gate G1 round 1 — demo machine, 2026-08-02: **FAIL, one finding, fixed**

Evidence: `block4b-20260802-133325`. G0 passed and the interview produced a
profile that `check-config` accepted (exit 0), but the reviewed profile would
not start a session:

    Live rig authorization failed:
    - Allowed channel preset 'Cy5' is not fully classified: Core.Shutter is excluded
    - ... same for 'DAPI', 'FITC', 'Rhodamine'

**This is not a Block 4b regression.** Block 4's `fa9e2ee` correctly made every
`Core.*` device-assignment property non-writable — one categorical write to
`Core.Focus` re-aims every reviewed stage bound. But setup recorded that as an
*explicit* entry in `rig_profile.excluded_properties`, and
`authorization.py:993` tests `pair in profile.excluded_properties` **before**
reaching the purpose-built rule two branches below it (`:996`–`:1006`): a
channel preset may retarget `Core.Shutter` when the device it selects is itself
a declared illumination shutter, because the gate then still covers whatever it
switches to. The explicit entry shadowed that allowance.

The demo rig's four fluorescence presets each set `Core.Shutter = 'White Light
Shutter'`, and the generated profile declares `White Light Shutter.State` under
`illumination.shutters` — so all four should have authorized.

Why no earlier gate caught it: `fa9e2ee` landed *after* Block 4's round-3 demo
gate, and its own profiles show `Core.Shutter` as **categorical**, which is why
their live sessions started. The only gate run after `fa9e2ee` was M5's G4b, and
**M5 has no `Channel` group** — only `System` — so no preset could expand there.
A latent defect with a one-rig blind spot on each side.

It is in 4b's scope by the precedent Block 4 set for its own round-1 findings
3–5: a startup-validator defect the block merely exposed still blocks the block's
gate. Fixed by the coordinator at `865f536` (1343 passed / 99 skipped / 3
expected warnings): setup records Core device-assignment properties in the
header notes but never as explicit exclusions. **Silence is not permission** —
guaranteed mode is an allowlist, so an undeclared property stays unwritable, and
a retarget to an *undeclared* device still refuses with `core-device retarget is
excluded`. Verified by replaying the operator's own captured inventory: the ten
`Core.*` structural entries leave `excluded_properties` (`Core.TimeoutMs` stays,
excluded on its own merits), while `Camera.Gain`, the 16 bounded numerics and
`channels.allowed` are unchanged. Runbook re-pinned at `865f536`.

The same explicit-exclusion-versus-vacuum shape as Block 4's round-4 field
failure on StateDevice positions. Worth stating as a general rule in the
post-merge design gate: **setup must not write an explicit exclusion for a
property the authorization layer already owns with a conditional rule.**

### Rig gate G1 round 2 — demo machine, 2026-08-02: **partial pass, one step never ran**

Evidence: `block4b-20260802-150128`, at `ea3dd75` with both pins `0` and
`check-config` exit 0.

Round 1's fix holds on the rig: startup now succeeds, and the four fluorescence
presets authorize. The only demotions are the ten `channels.allowed` names that
genuinely live in other config groups — the correct behaviour for an absent
preset.

Proven:

- **`Camera.Gain` is reachable and writable through Microclaw as a
  `bounded-numeric`** — `set_device_property Camera.Gain = '2'` succeeded and
  read back. This is the block's headline goal, evidenced on a rig.
- Exposure stays on the metered path: `set_exposure(12000)` was refused by
  `check_exposure` against `camera.max_exposure_ms=10000`.
- The illumination confirmation gate still binds: a declined
  `White Light Shutter.State` enable was refused.

**Not proven, and it is the block's central claim: the clamp.** Asked to set the
gain to 10, the agent called `get_device_property_info`, read the driver limits,
answered "10 is outside the Camera Gain's allowed range … so it would be
rejected", and **never called `set_device_property`.** The history contains
exactly one gain write, `'2'`. `check_typed_actuator` was never reached, so
nothing here is evidence about `bounded-numeric` enforcement.

This is the self-confirming-probe weakness in a new costume — the same failure
design/33 `:796` records against Phase 3, except the thing standing in for the
mechanism is the model's helpfulness rather than a script's. **A refusal the
agent reasons its way to is not the guard refusing.** The gate step is reworded
to force the tool call, and now carries a mechanical history check that must
print `WRITE ATTEMPTED: True` and `REFUSAL RETURNED: True`; a transcript alone
cannot pass it.

Secondary finding, fixed at `3cfba0e` (1344 passed / 99 skipped / 3 expected
warnings): **`get_device_property_info` reported only the driver's
`lower_limit`/`upper_limit`, never the declared policy.** A reviewed bound exists
precisely so it can be tighter than the hardware's, so an agent planning against
driver limits plans against authority the guard will refuse. It now returns
`declared_policy` alongside. Invisible in this run only because the accepted
bounds equalled the driver range.

### Rig gate G1 round 3 — demo machine, 2026-08-02: **PASS**

Evidence: `block4b-20260802-150128/g1-rerun`. Both mechanical checks print
`True`, so the step is evidenced by the tool call rather than the transcript.

The clamp fired, and the message is the block's contract in one line:

    Safety constraint prevented this action: Typed actuator Camera.Gain has
    canonical value 10 native; allowed absolute range is -5..8 native.

That is `check_typed_actuator` raising with the operator-declared unit echoed
verbatim — the "recorded and echoed, never interpreted" requirement shown on
hardware rather than in a unit test.

Full sequence, all five G1 steps:

1. `get_device_property_info` returned
   `declared_policy: {kind: bounded-numeric, units: native, minimum: -5.0,
   maximum: 8.0}` beside the driver range — `3cfba0e` working on a rig.
2. In-range write `Camera.Gain = '-1'` succeeded.
3. Out-of-range write `'10'` refused by the guard, naming bounds and unit.
4. Read-back returned `-1`: the refused attempt changed nothing.
5. No side effects — `set_exposure(12000)` still refused against
   `max_exposure_ms`, and the `White Light Shutter.State` enable still required
   a confirmation (approved this run, recorded in the confirmations ledger) and
   was driven back to `0`.

**G1 is closed. The remaining merge-blocking step is G2 on M5**; G3 (M2 gain
imagery) stays owed and non-blocking.

### Rig gate G3 — M2 (Andor iXon), 2026-08-03: **mechanism PASS, imagery owed**

Evidence: `block4b-m2-20260803-081901`. Run early rather than deferred, because
M2 became available. The bounded-numeric mechanism is proven on a real
scientific camera:

    Safety constraint prevented this action: Typed actuator Andor.Gain has
    canonical value 1200 native; allowed absolute range is 3..1000 native.

In-range writes of `25` and `600` each succeeded and read back; the refused
`1200` left the value at `600`; gain was restored to `100`. `declared_policy`
was reported beside the driver's `3..1000`. **G3's clamp and read-back steps are
closed.** Outstanding is only the two-gain image comparison, blocked by a full
`C:` drive on the rig — not by microclaw.

**The gate could not run at all until the operator hand-edited the profile**,
which is finding 3 below and was 4b's own defect.

### G3 round 2 — M2, 2026-08-03 at `eb3faa3`: **PASS, G3 is closed**

Evidence: `block4b-m2-20260803-094116`, run after `5a6c6e2`. **`Andor.Gain` is
now declared by default** — no hand-edit for gain, which is finding 3 confirmed
fixed on the rig it was found on. 19 bounded numerics.

The full sequence, in order:

1. `get_device_property_info` reported `current_value 3`, driver `3..1000`, and
   `declared_policy`.
2. `m2-gain-low` captured at gain **3**.
3. `Andor.Gain = 800` accepted.
4. `m2-gain-high` captured at gain **800**.
5. `Andor.Gain = 1500` **refused**: `canonical value 1500 native; allowed
   absolute range is 3..1000 native`.
6. Read-back `800` — the refusal changed nothing.
7. Illumination unaffected: `Luxx638.Laser Operation Select` enabled under
   confirmation, driven `Off`, and a later declined enable was refused.

Image comparison, measured here from the two exported TIFFs:

| | mean | median | p99 | max | saturated |
|---|---|---|---|---|---|
| gain 3 | 196.7 | 197 | 233 | 269 | 0 |
| gain 800 | 418.5 | 346 | 1258 | 2909 | 0 |

2.1× in mean and 5.4× at p99, neither frame saturated, in the expected direction
for EM gain. **The change is visible in the data**, which is the one G3 claim
that no off-rig test could make. The last disk-full run blocked exactly this
step; it succeeded once space was free.

**Both Block 4e findings reproduced unchanged at `eb3faa3`** — `Cobolt561.Laser`
still undiscovered, `Andor.Shutter` still offered only `Open`/`Closed`. Expected:
4e is scheduled, not implemented. This run is 4e's pre-fix baseline.

Three findings. Only the third is 4b's; the first two became **Block 4e**, and
the fourth is registered as carried-forward.

1. `Cobolt561.Laser`, the core shutter device's enable, was never discovered.
   → Block 4e.
2. A three-state shutter's `Auto` is writable with no confirmation. → Block 4e.
3. **`Andor.Gain` was excluded by default — 4b's own round-4 rule, too broad.**
   Round 4 excluded every bounded numeric on a device that surfaced any
   illumination candidate. Andor surfaces its own `Shutter (External)` and
   `Shutter (Internal)`, so the camera's gain was excluded — the block's
   headline deliverable, on the first real camera it met. The rule's actual
   hazard is a laser engine whose numerics redefine the emission envelope, and a
   camera's numerics cannot gate light at the sample. Fixed at `5a6c6e2` by
   scoping the bounded-numeric limb to devices MM does **not** type as
   non-emitting, reusing `rig_inventory._NON_EMITTING_TYPES` rather than a new
   name rule; the StateDevice-position limb is untouched. Measured on all three
   real inventories: demo 16 and M5 36 unchanged with no iChrome switch
   re-armed, M2 now declares `Andor.Gain`. Suite 1345.
4. A full disk surfaced as `struct.error: unpack requires a buffer of 4 bytes`
   with the hint `This may be a hardware error (device busy, stage at limit,
   device not found) or a connection problem.` → carried-forward register.

### Rig gate G2 — M5, 2026-08-03: **in-range PASS; refusal step retired as redundant**

Evidence: `block4b-m5-20260803-100405`, at `eb3faa3`, pin `0`, `check-config`
exit 0.

Passed: the profile generates and validates on M5 — 36 bounded numerics, **none
of the 11 iChrome TTL/Analog switches re-armed** by `5a6c6e2`'s narrowing, which
is the specific regression that change risked. `SmarAct 2D.Hold time (ms)` was
declared `ms 1..60000` without a revisit, and an in-range write of `500` reached
the real device.

**The out-of-range step never reached the guard, and the gate step was wrong,
not the operator.** The agent refused across roughly eight restatements, citing
its own instruction never to issue a setter call with a value it knows to be out
of bounds, and argued:

> a guard that only gets exercised because the agent voluntarily forwards
> knowingly-invalid input isn't being tested

That is correct, and it is the same objection this file already records against
self-confirming probes. It also cannot be worked around on this property: the
**declared bound equals the driver range**, so every out-of-bound value is also
hardware-invalid and the agent's rule always fires. Note the agent complied on
demo (G1) and on M2 (G3) — so the forced-call technique added to the runbook on
2026-08-02 is *flaky*, which is worse than plainly wrong.

**Retired as redundant, operator decision 2026-08-03.** G2 was written as a
stand-in — "so that merging without M2 does not leave the kind unproven on real
hardware — only *gain* waits". M2 then became available and closed that claim
directly: `Andor.Gain = 1500` refused against `3..1000` on a real Andor iXon,
read-back unchanged. Every claim G2 existed to establish now has evidence, from
a better rig than the substitute. This is not a lowered bar; it is the bar met
elsewhere.

Two process findings, both carried into the design gate below:

1. **An agent-mediated gate step must never ask the agent to do something it
   knows is invalid.** Test a guard either by declaring a bound *narrower* than
   the driver range, so the value is hardware-legal and the agent has no
   grounds to refuse, or by not routing the test through the agent at all.
2. The in-range read-back was asserted by the model ("the hold time stays at
   500 ms") with no `get_device_property` call after the write. Same class as
   the round-2 defect: assert on the tool call, not the narration.

**4b's rig gate is complete: G1 demo PASS, G3 M2 PASS, G2 in-range PASS with its
refusal step retired.**

Post-merge design gate:

- [x] Record the three-kind taxonomy and the not-dose-bearing rationale in
      design/33, alongside the refusal-severity taxonomy from Block 4.
- [x] Record the G2 rule: a guard test routed through the agent must present a
      value the agent cannot know to be invalid, or must not use the agent.
      Remove the forced-call wording from future runbooks — it worked twice and
      failed once, which makes it unreliable evidence either way.
- [x] Record the round-4/G3 lesson: a rule keyed on "this device surfaced an
      illumination candidate" catches cameras, which surface their own shutters.
      Device *type* is the discriminator, not the presence of a candidate.
- [x] Record the explicit-exclusion-versus-vacuum rule from G1 round 1, and the
      `Core.Shutter` preset allowance it was shadowing.
- [x] Record the G1 round 2 rule: **an agent-mediated gate step must assert on
      the tool call, not the transcript.** Any future rig step whose pass
      condition is "microclaw refused" needs a mechanical check that the call
      was actually made.

## 4e. [x] Emission-path discovery and multi-state shutters — **MERGED 2026-08-03**

Branch: `design33/emission-path-discovery`. Depends on 4b merging. Inserted
ahead of 4c by operator decision 2026-08-03. **Assigned 2026-08-03 from
`85398e8`.** Baseline measured by the coordinator at that commit: **1345 passed
/ 99 skipped / 3 expected warnings** in 12.9 s.

Found by Block 4b's G3 on **M2, the first time that rig was ever enumerated**.
Neither defect is 4b's code; both are discovery and illumination-schema
surface, and both are live on a rig with four lasers. Evidence:
`block4b-m2-20260803-081901`.

### 1. A ShutterDevice's enable was never discovered

`Cobolt561.Laser` — allowed values `Off`/`On`, device type **`ShutterDevice`** —
was not surfaced as an illumination candidate, so setup never offered it and the
generated profile declared no shutter for it. The operator added it by hand
(`profile.reviewed.fixed.gain-allowed.yaml`).

This is not a marginal miss: `Core.Shutter`'s allowed values on M2 are
`['', 'Cobolt561']`, so **the undiscovered device is the rig's core shutter.**
`_ENABLE_NAME` (`rig_inventory.py:48`) has no `laser` token and the property is
named exactly `Laser`; the Luxx lasers were caught only because theirs are named
`Laser Operation Select`, which matches `operation`.

- [x] Use the structural signal, not another name token: **Micro-Manager types
      the device as a `ShutterDevice`.** A two-value on/off-shaped property on a
      ShutterDevice is an emission gate by MM's own classification, and needs no
      regex. Widening `_ENABLE_NAME` with `laser` would fix M2 and miss the next
      vendor; the device type will not.
- [x] Measure the change against all three captured inventories before adopting
      it, and report exactly which candidates appear and disappear per rig. The
      pattern must not be narrowed for any other device type — the comment at
      `rig_inventory.py:47` records why.
- [x] Consider whether the device named by `Core.Shutter` deserves a stronger
      check than discovery: if the core shutter device has no declared shutter
      property at all, that is a fail-closed condition, not a heuristic miss.

### 2. A three-state shutter's middle value bypasses the confirmation gate

`Andor.Shutter (Internal)` and `(External)` take `Open` / `Auto` / `Closed`. The
generated profile declares them with `on_value: Open`, `off_value: Closed`.
**Measured directly against `SafetyGuard` on 2026-08-03:**

    'Open'   -> refused when the human declines   (gate works)
    'Closed' -> permitted, no confirmation        (correct)
    'Auto'   -> permitted, NO CONFIRMATION ASKED

`check_illumination` (`safety.py:1064`) gates only `value == shutter.on_value`,
and `check_raw_property_write` skips the categorical allowlist for illumination
pairs — so a third value has no gate at all. On an Andor, `Auto` is the state
that opens the shutter on **every exposure**: the value that emits most
routinely is the one no human has to approve.

- [x] Operator decision 2026-08-03: **confirm-gate any value that is not
      `off_value`**, rather than only exact `on_value`. Strictly more gating, no
      schema change, and it fixes every multi-state shutter rather than Andor's.
- [x] Check the same asymmetry everywhere a declared value is compared for
      equality rather than for membership — `shutter_all`, the ratchet, and the
      EMU map. Report what you find rather than widening silently.
- [x] Off-rig tests must pin all three limbs (`on`, `off`, and a third value),
      because the third is the one that had no coverage.

### Implementation rounds — pushed 2026-08-03, awaiting the rig

`39f69dd` was reviewed and returned; `bc40f18` fixes all three findings, plus a
coordinator correction at `d631a4f`. Suite **1350 passed / 99 skipped / 3
expected warnings**, from a branch-point baseline of 1345.

**A producer change is invisible to a plain interview replay, and this cost the
coordinator a wrong conclusion before it was caught.** `interview()` consumes
the inventory file's *stored* `heuristic_candidates` (`first_launch.py:594`,
`:599`), so replaying a captured file measures the producer that captured it,
not the one under test. Candidates must be re-derived by running `_is_enable`
over `facts.devices` first. Re-derived independently by the coordinator under
both revisions: demo 2 → 2, M5 21 → 21, M2 9 → 12, adding exactly
`Cobolt561.Laser`, `Cobolt561.Autostart` and `Cobolt561.Analog Impedance`, none
lost. Record this method for every future discovery block.

The three returned findings:

1. **The structural path was stricter than the shape rule beside it.** The
   ShutterDevice branch accepted only `len(allowed) == 2` while the general rule
   three lines below accepts `<= 4` — so the block fixed gating for multi-state
   shutters while shipping discovery that could not find one. Measured free on
   all three captured inventories: no ShutterDevice property on any rig has 3–4
   allowed values with an `_ON_VALUES` member, so the candidate sets are
   identical either way. `Andor.Shutter (Internal)` is caught today only because
   it sits on a **CameraDevice** and is named "Shutter".
2. **Two false positives needed naming in the runbook, not excluding in code.**
   `Cobolt561.Autostart` and `Analog Impedance` are `Disabled`/`Enabled`, and
   `enabled` is in `_ON_VALUES`. Surfacing them is correct under the module's
   deliberate-breadth doctrine (`rig_inventory.py:43`), and a name-based
   exclusion would be the rig-facts-in-`microclaw/` mistake. But if an operator
   declares either as an emission gate, `shutter_all` (`safety.py:1121`) writes
   its `off_value` on **every session exit**, rewriting persistent laser
   configuration. The gate step now names both and states the consequence.
3. **The `Core.Shutter` fail-closed decision was right, its stated reason was
   circular** — it rested on three surfaced properties, two of which the change
   itself created. The real evidence is on the demo rig: `LED Shutter` is a
   `ShutterDevice`, is selectable through `Core.Shutter`, and has **no writable
   gating property at all** — its emission runs through MMCore's shutter API.
   A fail-closed check would refuse startup on the stock demo rig with no legal
   declaration available, which is the shape Block 4 round-1 finding 5 ruled
   out. Declining the check is therefore correct **and** it exposes the
   residual: some emission paths have no property to discover.

Coordinator corrections at `d631a4f`, both small:

- Round 2 **replaced** the two-value ShutterDevice test with the three-value one
  rather than adding it, leaving the block's headline defect shape
  (`Cobolt561.Laser`, two values, no name token) with no unit test — the suite
  count staying at 1349 was the tell. Both cases are now pinned.
- The runbook pinned `7c0f25b`, which by then was only part of the
  implementation, so `--is-ancestor` would have passed on a tree missing the
  discovery widening. Re-pinned to `bc40f18`.

Rig gate (M2 has the multi-state shutter and the Cobolt; M5 has neither):

- [x] Show `Cobolt561.Laser` is discovered and offered by setup without a hand
      edit, and that the generated profile declares it.
- [x] Show a write of `Auto` to `Andor.Shutter (Internal)` now requires
      confirmation, and that declining it refuses the write.
- [x] Confirm no previously-working illumination declaration stopped working on
      M5 or the demo rig. **Both done** — M5 21 → 21 with no TTL/Analog switch
      armed, demo 2 → 2 with the carried-over shutter-confirmation step run on
      `White Light Shutter.State`.

### Rig gate G1/G2 — M2, 2026-08-03: **PASS**

Evidence: `block4e-m2-20260803-111417`, at `d631a4f`, ancestor pin exit `0`,
`check-config` exit `0`.

**G1 PASS.** The live producer emitted exactly the 12 enable candidates the
coordinator had predicted offline by re-deriving `_is_enable` over the captured
`facts.devices` — the offline method and the rig agree path-for-path. All three
Cobolt properties were offered with explicit classification required, and the
**unedited** draft declares `Cobolt561.Laser` `on: 'On'` / `off: 'Off'` from
accepted MM proposals. The rig's core shutter is now reachable without a hand
edit, which is the defect the block exists to fix.

**The two named false positives were excluded, which is the F2 fix working.**
`Cobolt561.Autostart` and `Analog Impedance` both landed in
`excluded_properties`. The runbook naming them, their real meanings, and the
`shutter_all`-writes-on-every-exit consequence is what made that the operator's
informed choice rather than a coin flip.

**G2 PASS, mechanically.** The agent called `set_device_property(Andor,
'Shutter (Internal)', 'Auto')` — it did **not** refuse to forward, because
`Auto` is a legal driver value, which is the 4b G2 lesson applied and confirmed.
The tool result is `User declined to enable illumination Andor.Shutter
(Internal).`, and the confirmations JSONL independently records
`kind: illumination, decision: declined`. Before the change this value reached
the device with no confirmation at all.

The runbook's post-decline read-back was **not performed**. Not re-run: it is
redundant on this path by construction — `check_illumination` raises at
`tools.py:491`, before `core.set_property` at `:492`, so the write provably
cannot have landed. Keep the read-back in future runbooks anyway for guards
that sit after the hardware call; here it was asking for evidence the code
shape already supplies.

**G3 (M5 and demo non-regression) is still owed.** M2 alone does not close this
block.

### Rig gate G3 — M5, 2026-08-03: **non-regression PASS; the shutter step was not run**

Evidence: `block4e-m5-20260803-112756`, at `d631a4f`, pin `0`, clean tree,
`check-config` exit `0`, pytest exactly the known 23 Windows failures in the two
known modules and no others — the first run judged against block 4g's baseline
rather than against an unmeetable "suite must pass".

**Discovery non-regression PASS.** The live producer emitted exactly 21 enable
candidates and the profile declares exactly the 21 expected shutters, identical
to the pre-change set. **No iChrome TTL or Analog mode switch became a shutter
or an ordinary writable property** — the specific regression 4b round 3 caused
and this change could have repeated. As recorded, M5 has zero `ShutterDevice`s
so this outcome was guaranteed by the captured facts; it is confirmation that
the change is inert there, not evidence of resilience.

The session started on the generated reviewed profile and ordinary work still
functions: a snap returned a valid focus metric and SNR, and a raw camera
property write (`Sensor Cooler` → `ON`) was permitted and read back. Startup also
reported `shutter: no shutter device configured`, consistent with the standing
record that M5 has no core shutter.

**The step that would have tested M5's real exposure was not performed.** The
runbook asked for one previously-working declared shutter exercised through
confirmation and returned to its reviewed off value; the session snapped an
image and turned the sensor cooler on instead. Since discovery cannot affect
M5, the `safety.py` gating change was M5's only exposure, and it is unmeasured
there.

**Not re-run on M5, by coordinator judgement — folded into the demo run
instead.** The gating change was proven directly on M2 (`Auto` refused, with the
confirmations JSONL). M5's 21 declared shutters are all two-valued, so no third
value can arise for the new `!= off_value` limb to catch, and the only residual
was the numeric-formatting variant on its twelve `1`/`0` shutters. Demo's
`White Light Shutter.State` is also `['0', '1']`, so the identical numeric shape
can be exercised there — with no laser and no optical containment required.
Spending a second M5 trip on a strictly more hazardous version of a test the
demo rig can run is not justified.

### Two findings from this run that are not Block 4e's code

1. **23 tests have never passed on Windows, and every gate runbook has told the
   operator "the suite must pass".** The same 23 failures appear in
   `block4b-m2-20260803-081901`, `-094116`, `block4b-m5-20260803-100405` and
   `block4b-20260802-150128` — every rig including demo, back through 4b's whole
   campaign — and all of them are in `tests/test_completed_dataset.py` and
   `tests/test_describe_hook.py`. Nothing in either module is touched by 4e.
   `pytest.txt` has been collected as gate evidence for four blocks and never
   triaged, so **G0's precondition as written has been unsatisfiable on every
   rig run to date.** Either the runbooks state a known-failure baseline or the
   failures get fixed; a precondition nobody can meet trains operators to ignore
   it. Windows skips 115 to macOS's 99, so the platform gap is wider than the
   failures alone.

2. **`channels.allowed` is generated from every config group, not the `Channel`
   group.** `first_launch.py:1087`–`:1101` collects preset names from all
   `preset_proposals` and writes them at `:1134`, while `authorization.py:151`
   pins `CHANNEL_CONFIG_GROUP = "Channel"` deliberately (`:146`: not read from
   `Core.ChannelGroup`, which is writable). M2 has exactly one group, `Camera`,
   and no `Channel` group — so setup wrote its six camera presets
   (`Beads_EM25`, `EM100_10MHz`, …) into `channels.allowed`, and startup demoted
   all six. **Setup generated a profile guaranteed to warn on the rig it was
   generated on**, and the demotion text tells the operator to "add each preset
   to Micro-Manager's Channel group" — wrong advice, because those presets are
   camera settings that were never channels. This is the same shape as the
   recorded M5 finding that M5 has no `Channel` group either; of the three rigs,
   only demo has one. Needs a home; it is a Block 4 surface, not 4e's.

### Rig gate G3 — demo, 2026-08-03: **PASS. Block 4e's gate is complete.**

Evidence: `block4e-demo-20260803-113715`, at `d631a4f`, pin `0`, clean tree,
`check-config` exit `0`, pytest exactly the known 23 Windows failures and no
others.

Discovery is unchanged: 2 candidates in, and the profile declares exactly
`Core.AutoShutter` and `White Light Shutter.State`. `LED Shutter.State Device`
stayed out of the illumination set and was classified categorical, which is
right — it is a device selector, not a gate. **No `Core.Shutter` exclusion was
written**, so 4b's G1 round-1 defect (an explicit exclusion shadowing the
preset-retargeting rule and breaking the four fluorescence channels) is not
reintroduced, and those four `Channel` presets survived startup authorization
un-demoted.

**The carried-over shutter step ran in full, and it is the best evidence in the
block**, because it exercised all three limbs against real hardware in one
session:

    State = 1  -> confirmation asked, APPROVED -> write succeeded
    State = 0  -> no confirmation asked        -> write succeeded
    State = 1  -> confirmation asked, DECLINED -> refused
    read-back                                  -> "0"

The middle line is the one that matters most and is easy to overlook: it proves
the `!= off_value` change did **not** turn the off-write into a prompt, which
was the specific regression this change risked on every numeric `1`/`0` shutter.
The confirmations JSONL independently records one `approved` and one `declined`,
and the read-back the M2 run omitted was performed here.

Not exercised: selecting a fluorescence preset during the session. Only its
*validation* is evidenced, by those four presets not being demoted at startup.
Accepted rather than re-run — 4e changed no `Core.Shutter` handling and added no
exclusion, and the retargeting path is what 4b's gate already covered.

**This run also corroborated Block 4f on a rig that is not M2, and sharpened
it.** Demo *has* a `Channel` group, and setup still wrote all 14 presets from all
six groups into `channels.allowed`; startup demoted 10 of them. So 4f is not
"rigs that lack a `Channel` group" — **setup over-claims on every rig**, and the
missing-group case is only its most visible form.

Post-merge design gate:

- [x] Record in design/33 that illumination discovery keys off MM device typing
      and not only property names, and that shutter gating is
      not-`off_value` rather than exact-`on_value`. State the residual: this
      still proves declared paths are gated, never that discovery found every
      physical emission path.

## 4f. [x] `channels.allowed` is generated from the wrong config group — **MERGED 2026-08-03**

Branch: `design33/channel-group-presets`. Depends on 4e merging. Created by
operator decision 2026-08-03 from Block 4e's M2 gate, which is the same way 4e
itself was created from 4b's. **Assigned 2026-08-03.** Baseline measured by the
coordinator at the start commit: **1350 passed / 99 skipped / 3 expected
warnings**.

`first_launch.py:1087`–`:1101` collects preset names from **every** config group
in `preset_proposals` and writes them to `channels.allowed` (`:1134`).
`authorization.py:151` pins `CHANNEL_CONFIG_GROUP = "Channel"` and reads only
that group — deliberately, and not from `Core.ChannelGroup`, because that
property is writable (`:146`).

Measured on M2 (`block4e-m2-20260803-111417`): the rig has exactly one config
group, `Camera`, and no `Channel` group. Setup wrote its six camera presets
(`Beads_EM25`, `EM100_10MHz`, `EM100_17MHz`, `EM200_10MHz`, `EM200_17MHz`,
`Vibrations_EM25`) into `channels.allowed`, and startup demoted all six. **Setup
generated a profile guaranteed to warn on the rig that generated it**, and the
demotion advises the operator to "add each preset to Micro-Manager's Channel
group" — wrong, because those presets are camera settings that were never
channels. Of the three rigs only demo has a `Channel` group; M5 has only
`System` (already recorded) and M2 only `Camera`.

- [x] Emit `channels.allowed` only from the group `authorization` actually
      reads. If that group is absent, emit an empty list and say so in the
      setup text and in the review notes — an empty, honest key beats six
      claims the rig will drop.
- [x] The demotion message must stop advising an edit that cannot be right. It
      currently assumes the preset belongs in the `Channel` group; on a rig with
      no such group the correct action is different. Distinguish "this preset is
      missing from a group that exists" from "the group does not exist here".
- [x] Decide what setup should do with presets in **other** groups. They are
      real and useful; they are simply not channels. Report a decision — surface
      them as review notes, or say plainly that microclaw does not drive them —
      rather than silently discarding.
- [x] Do not generalize beyond the `Channel` name without changing
      `authorization.py` too; producer and consumer must name the same group or
      this defect recurs inverted.
- [x] Rig gate: generate a profile on M2 (or M5) and show `channels.allowed` no
      longer claims presets the authorizer will drop, and that startup produces
      no preset demotion. On demo, show the real `Channel` presets still appear
      and fluorescence channels still work.

### Implementation — pushed 2026-08-03, awaiting the rigs

`84c4d70` accepted on its first round, plus a coordinator commit at `d4985e6`.
Branch suite **1358 passed / 99 skipped / 3 expected warnings** after `main` was
merged in (see below); the implementer measured 1354 before that merge, which is
1350 + four new tests. Note 1354 is *also* `main`'s count for an unrelated
reason — `main` had gained 4g's four tests — so do not read the two as the same
number.

Coordinator-verified by replaying the three captured inventories through
`interview()`, which is valid here because `channels.allowed` is written by the
consumer from `facts.configuration_groups` already in the file (unlike 4e, whose
change was in the producer):

| Rig | before | after |
|---|---|---|
| demo | all 14 names across six groups | exactly `Cy5`, `DAPI`, `FITC`, `Rhodamine` |
| M5 | four `System` presets | `[]`, with the review note |
| M2 | six `Camera` presets | `[]` (see below) |

M2 was **not** independently replayable: the coordinator's acceptance responder
cannot answer the duplicate-representation question, and it fails identically on
`main` and the branch. M2 takes the same `channel_group_present is False` branch
as M5, which is confirmed, and the rig gate settles it live. Recorded rather
than quietly counted as verified.

**The semantic trap is handled correctly and pinned by a test.** Setup always
emits the key: an absent or empty `Channel` group yields `allowed: []`, never an
omitted key, because omission means *every* live `Channel` preset is authorized
(`safety.py:216`, `authorization.py:973`). This was the one way this block could
have silently widened authority on exactly the rigs it fixes.

Also confirmed: presets in other groups are surfaced as review notes rather than
discarded silently; group filtering happens before names enter the allowlist, so
demo's `Channel` and `Channel-Multiband` sharing four preset names cannot leak
the wrong group's preset; and the startup diagnostic now distinguishes "missing
from a `Channel` group that exists" (where "add it to that group" is sound) from
"this rig has no `Channel` group" (where it is not).

**Coordinator changes at `d4985e6`.** `main` was merged into the branch so the
gate carries 4g's Windows repair and the `uv.lock` ignore — without it the
operator would have met a suite with 23 known failures and an untracked file, on
a gate whose precondition is a clean tree and a clean suite. The runbook's
Windows expectation was rewritten accordingly: **0 failed is the hard
condition**, ~1342 passed is informative only, and a small deviation in the
*passed* count is to be reported rather than treated as failure. That wording is
deliberate — a derived cross-platform count was wrong once already in 4g, and
the failure count is the claim that matters.

One thing checked because a wrong answer would have wasted a rig trip: G2's
mechanical check asserts on a `set_channel` tool call, and `set_channel` is a
real registered tool (`tools_schema.py:263`, `tools.py:455`).

### Rig gate G1 — M2, 2026-08-03: **PASS**

Evidence: `block4f-m2-20260803-122349`, at `d4985e6`, pin `0`, `check-config`
exit `0`.

Every mechanical check passed: `ALLOWED KEY PRESENT: True`, `ALLOWED EXACTLY
EMPTY: True`, the generated profile carries `channels: {allowed: []}`, and the
demotion check is **empty**. Compare the same rig under Block 4e
(`block4e-m2-20260803-111417`), where startup printed
`!! AUTHORIZATION CLAIMS DEMOTED !!` naming all six `Camera` presets. The
session now connects clean.

The profile header carries both required notes in the operator's own file — the
absent `Channel` group with the `[]`-versus-omitted semantics spelled out, and
the six `Camera` presets listed as non-channel presets microclaw does not drive.
That was the difference between an honest empty key and an unexplained one.

**The Windows suite was clean at exactly the predicted 1342 passed / 0 failed /
115 skipped**, confirming both that merging `main` into the branch did its job
and that this time the derived cross-platform count was right.

**Checked before releasing demo's G2, because a wrong answer would have
reproduced a known defect:** `Core.Shutter` appears in this profile's header as
an exclusion *note* but is **not** an entry in `excluded_properties` (the only
`Core` entry is `TimeoutMs`). That is Block 4b's G1 round-1 fix still holding —
an explicit exclusion there would shadow `authorization.py`'s purpose-built rule
permitting a preset to retarget `Core.Shutter` to a declared shutter, which is
exactly what broke demo's four fluorescence channels in 4b. Demo's G2 is safe to
run.

### Rig gate G2 — demo, 2026-08-03: **PASS. Block 4f's gate is complete.**

Evidence: `block4f-demo-20260803-123329`, at `d4985e6`, pin `0`, clean tree,
suite **1342 passed / 0 failed / 115 skipped**.

`EXACT CHANNEL PRESETS: True` with `['Cy5', 'DAPI', 'FITC', 'Rhodamine']` — the
four real `Channel` presets and nothing from the other five groups. The demotion
check is empty. The selection was genuinely exercised rather than asserted:
`get_available_channels` returned exactly those four, `set_channel {"preset":
"DAPI"}` succeeded with `writes: 4` and `expansion_drift: false`, and the
startup and applied expansion hashes match. The channel-plan executor from
design/33 Phase 4 works against the narrowed allowlist.

**This run also produced a finding that is not 4f's code — see block 4h.** The
operator asked why they had been prompted to open the shutter when setting DAPI;
the agent replied "I didn't, actually — I never prompted you," and the
confirmations JSONL records an approved `illumination` confirmation at that
moment. The prompt was real and correct: DAPI's effects include `Core.Shutter =
'White Light Shutter'`, and `_authorize_channel_effect` (`authorization.py:1314`)
requires a blocking confirmation for that retarget. The model simply cannot see
it.

Post-merge design gate:

- [x] Recorded in design/33 §"Block 4f landed": the producer emits
      `channels.allowed` from `CHANNEL_CONFIG_GROUP` alone; an absent or empty
      group yields an explicit `[]` and never an omitted key, because omission
      means *all* live presets are authorized; and the live diagnostic now
      distinguishes a preset missing from an existing `Channel` group from a rig
      that has no such group.

## 4g. [x] Saved hooks are unusable on Windows — **MERGED 2026-08-03**

Branch: `design32/hook-hash-newline`. Depends on nothing; touches files no other
queued block touches (`hook_manager.py`, `completed_dataset.py`), so it may run
concurrently with 4f/4c in its own worktree. **Assigned 2026-08-03 from
`95ae192`.** (This line and the ledger first said `4472892`, the commit
before the assignment merge; the implementer flagged the discrepancy and was
right — the branch base is `95ae192`.) Baselines measured by the coordinator: macOS **1345 passed / 99
skipped / 3 expected warnings**; Windows **1311 passed / 23 failed / 115
skipped**, the 23 being this block's subject.

Scheduled by operator decision 2026-08-03 rather than papered over with a
known-failure baseline. **The 23 failures every Windows rig run has reported are
one product defect, not test noise, and they are not a Block 4e regression** —
the identical set appears in `block4b-m2-20260803-081901`, `-094116`,
`block4b-m5-20260803-100405` and `block4b-20260802-150128`, i.e. every rig
including demo, back through 4b's whole campaign.

Root cause, from `block4e-m2-20260803-111417/pytest.txt`: every one of the 23
fails with `Hook 'X' changed on disk since it was saved; refusing to load.`

- `save_hook` (`hook_manager.py:118`) writes with `write_text(code)` — **text
  mode**, so Windows translates `\n` to `\r\n` on disk — but pins
  `sha256(code.encode())` (`:128`), the hash of the **in-memory `\n` form**.
- `completed_dataset.py:70`/`:76` reads `read_bytes()` and hashes the **raw
  on-disk bytes**. On Windows those differ, so the pin never matches.
- `describe` (`hook_manager.py:267`, `:277`) hashes raw bytes too, and reports
  `matches_manifest: False` for an untampered file.
- `hook_manager.load` (`:165`, `:171`) reads *text* and hashes the normalized
  form, so it matches. **That inconsistency is the bug**: three call sites, two
  conventions, identical only on POSIX.

Consequence, which is worse than the red tests: **on Windows a saved hook is
permanently unloadable through the offline/dataset path and reports as tampered
through describe, while loading fine through `hook_manager.load`. Every rig is
Windows.** It fails safe — a genuine file is rejected, never a tampered one
accepted — but the feature does not work where it has to work.

- [x] Pick one canonical convention and use it at every save, load, describe and
      offline-adapter site. The on-disk bytes are the artifact the manifest
      claims to pin, so hashing raw bytes and writing bytes (or text with
      `newline=""`) is the obvious direction — but state the choice and apply it
      everywhere rather than patching the failing call site.
- [x] **Existing manifests carry hashes pinned under the old convention.** Decide
      migration: re-pin on load with an explicit prompt, refuse with an
      actionable message, or accept both forms for a release. A hash the user
      consented to may not be silently rewritten — the manifest *is* the consent
      record.
- [x] Add a test that fails on POSIX today, by writing a `\r\n` file and
      round-tripping it. The current suite passes on POSIX precisely because it
      never exercises the difference, which is why this survived four blocks.
- [x] Rig gate on any Windows machine: save a hook, then load it through the
      offline path and describe it. Both must succeed, and `pytest.txt` must
      show these 23 tests passing.

### Windows gate round 1 — M5, 2026-08-03: **FAIL on the suite; the product code is right**

Evidence: `block4g-m5-20260803-115506`, at `d0bb602`, pin `0`, clean tree.

**G1 PASS and G2a PASS on real Windows.** A CRLF-sourced hook saved, loaded
live, described, and loaded offline with `matches_manifest: true`; and the
synthetic legacy-pin migration walked refusal → review → re-save → reload with
all twelve checks true and both refusal messages actionable. The convention
change works on the platform it was written for.

**G0 FAIL: 1308 passed / 23 failed, against an expected 1336 / 0.** The same 23
tests, and at first glance the same failure as before — which would have meant
the fix did nothing. It did not mean that, and the distinction is the whole
finding: the message had **changed** from `changed on disk since it was saved`
to `uses a legacy newline-normalized hash`. The new code was running, doing
exactly what it should, and correctly reporting that those hooks' pins did not
match their bytes.

**The two test fixtures had the same defect as the product code, and were not
fixed with it.** `tests/test_completed_dataset.py`'s `offline_home.save` and
`tests/test_describe_hook.py`'s `_install_saved` each wrote with `write_text`
and pinned `sha256(code.encode())` — text out, string hashed. Between them they
own all 23 failures: 19 and 4 respectively. On POSIX the two agree, so nothing
was visible; on Windows the fixture's own hooks became legacy-pinned.

Fixed by the coordinator at `971cdb6`, with a regression guard in each module.
**Getting the guard right took two attempts and the failed one is worth
recording:** feeding CRLF source in directly passes on POSIX whether or not the
fixture is fixed, because it is Windows *translation* that makes bytes differ
from the string, not the presence of CRLF. Both guards therefore simulate the
translation, and each was verified to fail against its own un-fixed fixture.
Suite 1347 → **1349 passed / 99 skipped / 3 expected warnings**; Windows should
now show **1338 passed, 0 failed, 115 skipped**.

**The lesson is about the acceptance criterion, not the code.** This block's
stated purpose was to make 23 Windows tests pass, and the implementer reported
the fix was "expected to repair" them — honestly hedged, since it could not be
checked from macOS. The unstated assumption was that the tests exercised the
product's save path; two of them built their own. **When a block's acceptance is
"these named tests pass on a platform you cannot run," the fixtures those tests
use are part of the surface under repair.**

### Windows gate round 2 — M5, 2026-08-03: **22 of 23 repaired; one left, same shape**

Evidence: `block4g-m5-20260803-120629`, at `971cdb6`, pin `0`, tree clean but for
an untracked `uv.lock`. **1332 passed / 1 failed / 115 skipped**, from round 1's
1308 / 23. The fixture fix repaired 22 of 23.

The survivor was `test_describe_hook.py::test_hash_mismatch_is_described`, and
it is **the same defect one level deeper**: round 1's fix corrected the fixture
that *installs* a hook, while this test's own *tampering* step still did
`path.write_text(changed)` and then asserted against
`sha256(changed.encode())`. Windows translated the write; the assertion hashed
the untranslated string. Fixed at `f768cc3` by tampering in bytes and asserting
against those bytes.

An audit of every remaining `write_text`/string-hash pair in the suite found one
more instance, in the same file's `everything` fixture. It was **not** failing,
because that test never asserts on the hash — made consistent anyway so a future
`matches_manifest` assertion cannot fail on Windows alone. No other site in
`tests/` pairs a text write with a string hash.

**The expected Windows count in the runbook was also wrong, and the reason is
worth recording.** It said 1338, derived by adding this block's new tests to the
1311/23 baseline. That baseline was measured on Block 4e's branch, but **4g was
cut from `main` before 4e merged**, so 4g's tree does not carry 4e's five tests.
The runbook now states **1333 passed / 0 failed / 115 skipped**, taken directly
from round 2's own observed run (1448 collected) rather than derived from a
baseline measured on a different tree. Derived counts across divergent branches
are worth less than the branch's own measurement.

**Three rounds, and every failure has been on the test side.** The product code
has been correct since `50e5f66` and has passed G1 and G2a on real Windows
twice. That is worth stating plainly so the next reader does not conclude the
convention change was shaky: what was shaky was the assumption that the tests
proving it used the code path being repaired.

### Windows gate round 3 — M5, 2026-08-03: **PASS. Block 4g is complete.**

Evidence: `block4g-m5-20260803-121241`, at `f768cc3`, pin `0`, **1333 passed /
0 failed / 115 skipped** — exactly the count predicted from round 2's own run.
All 23 Windows failures are repaired. The only `git status` entry is an
untracked `uv.lock`, now gitignored on `main` (`b982808`).

G1 and G2a were not re-run this round, deliberately: `microclaw/` is
**byte-identical between `d0bb602`, where both passed on real Windows, and
`f768cc3`** (verified by the coordinator with `git diff --stat d0bb602 f768cc3
-- microclaw/`, empty). Rounds 2 and 3 changed only tests and the runbook, so
that evidence carries forward — the same reasoning that let Block 4e skip G6 in
round 3.

**Post-merge design gate.** Not named when the block was scheduled; added at
closeout because the block changes a documented contract. Recorded in
`design/32-repository-review-top-five.md` §4, where hash pinning is described:
the pin is the sha256 of the exact bytes on disk, one shared verifier owns the
check, and a pre-existing newline-normalized pin is reported distinctly rather
than as tampering.

### Implementation — pushed 2026-08-03, awaiting the Windows gate

`50e5f66` accepted on its first round, plus a coordinator addition at
`d0bb602`. Suite **1347 passed / 99 skipped / 3 expected warnings**, from a
branch-point baseline of 1345 — the two new tests and no regressions.

The convention chosen: **saved hooks are UTF-8 encoded once, written as bytes,
and the pin is the sha256 of exactly those bytes.** One shared verifier,
`verify_saved_hook_bytes`, is now used by all four sites that had drifted apart
— `save_hook`, `load_hook_class`, `describe_saved_hook`, and
`_load_saved_adapter`. That is the part that matters: the defect was never one
bad call site, it was three sites and two conventions.

**Migration: old pins are neither accepted nor silently rewritten.** A legacy
newline-normalized pin is *detected* (`_legacy_text_sha256`) and reported
distinctly — `legacy_newline_pin: true` in describe, and a load error naming
review-and-re-save rather than the generic tampering message. Re-saving after
review establishes a byte-exact pin. Note the blast radius is smaller than it
looks: on POSIX the old pin already equalled the byte hash, because no
translation occurred, so only hooks *saved on Windows* need migrating.

Recovery is real, not nominal: `read_hook_from_file` reads the existing file and
`save_hook` re-pins it, so a Windows user with an unloadable hook has a path
that does not require regenerating the code. Verified by the coordinator.

**The before/after claim was verified independently rather than accepted.** Both
new tests were run against `main`'s code: both fail there, and pass on the
branch. That is the whole proof obligation for this block, since the macOS suite
was green through every one of the four blocks that shipped the defect.

The audit came back clean and specific: `hook_decisions.py` writes and hashes the
same bytes object; `rig_inventory.py` → `first_launch.inventory_sha256` writes
first and hashes the resulting file, with no pre-write string digest to
disagree with it; the text-written manifests in `tools.py` and
`completed_dataset.py` record hashes of other artifacts and never claim to hash
themselves. No second instance of the defect exists.

**Coordinator addition, `d0bb602`.** G2 as written depended on the gate machine
happening to own a hook pinned under the old convention; if it owns none — and
nothing guarantees the demo machine does — the migration path would ship
untested. Added `design/35-block4g-legacy-migration-check.py`, which builds a
throwaway registry in a temp directory, plants a legacy-pinned hook, and walks
refusal → review → re-save → reload. Run by the coordinator against the branch:
all twelve checks pass, both refusal messages actionable. G2 is now the
opportunistic step and G2a the guaranteed one.

## 4h. [x] The agent cannot see blocking confirmations, and denies them — **MERGED 2026-08-03**

Branch: `design33/confirmation-visibility`. Depends on 4f merging. Created by
operator decision 2026-08-03 from Block 4f's demo gate.

Found because 4f's G2 asked the operator to *actually select* a channel rather
than validate one. Evidence: `block4f-demo-20260803-123329`. **Assigned
2026-08-03 from `627b46b`.** Baseline measured by the coordinator at that
commit: **1358 passed / 99 skipped / 3 expected warnings**.

**Ledger correction:** the start commit was first recorded as `627b46b`, the
commit *before* the assignment merge. The branch base is `1599ff3`. This is the
second time the same slip has been made — 4g had it too, and both times the
implementer caught it. **"Start commit" means the assignment merge commit, i.e.
`git rev-parse HEAD` on `main` after the ledger edit is merged, not the commit
the edit was written on top of.**

The operator asked why they had been prompted to open the shutter when setting
DAPI. The agent answered **"I didn't, actually — I never prompted you to open
the shutter,"** and said the only thing it had flagged was the long exposure.
The confirmations JSONL records an `illumination` confirmation `approved` at
that moment.

**The prompt was real and correct.** DAPI's preset effects include `Core.Shutter
= 'White Light Shutter'`, and `_authorize_channel_effect`
(`authorization.py:1314`) requires a blocking confirmation for that retarget —
"SELECT ILLUMINATION SHUTTER … selects which declared light source AutoShutter
may fire on the next exposure." The operator remembered accurately and the guard
behaved correctly.

**The model simply has no way to know.** `CONFIRM_FN` is invoked inside the tool
(`tools.py:75`, eleven call sites), `serve` replaces it with `Session.confirm`
so the gate reaches the browser (`webserve.py:332`), and the decision is written
to a separate confirmations JSONL. **No tool result carries any of it back.**
`set_channel` returns `status`, `writes`, `expansion_drift` and the expansion
hashes — nothing about the human interaction that had to happen first. From the
model's side the harness asked, not it.

Why this is worth its own block rather than a note: microclaw's safety story is
that a human confirms every emission event. An agent that then tells the
operator, confidently and in good faith, that a confirmation they answered never
happened, corrodes exactly the conversation the audit trail exists to support.
No unsafe action occurs — this is a truthfulness defect, not a hazard — and
every remaining block in this checklist is gated through an agent-mediated
transcript, so the fix improves the evidence quality of 4c, 4d and 5.

- [x] **Return the confirmations issued during a tool call in that tool's
      result**, with at least kind, decision, and enough of the summary for the
      model to say what was approved. `Session.audit_records` (`webserve.py:317`,
      appended at `:355`) already accumulates exactly these records and is
      **read by nothing** — check before building a second mechanism.
- [x] The model must be able to answer "did you prompt me, and for what?"
      correctly, including for a **declined** confirmation, where the tool
      raises and the refusal text is what the model sees today.
- [x] **Decide what the audit row should contain, and report the decision.** It
      currently records timestamp, identity, `confirmation_id`, `kind` and
      `decision` — but not *what* was confirmed, so two illumination approvals in
      one session are indistinguishable after the fact. Weigh that against the
      summary containing device/property/value text and the existing secret
      redaction (`_add_audit_secret`). Do not widen silently in either direction.
- [x] **Report what you find about the CLI path.** `_require_confirmation`
      (`tools.py:56`) prints and returns; it appears to write no audit record at
      all, so a REPL session may have no confirmation trail. Establish whether
      that is true and say so; propose rather than fix if it grows the block.
- [x] Off-rig tests must cover approved and declined, and must assert on the
      returned structure rather than on wording.

### Implementation — pushed 2026-08-03, awaiting the demo gate

`3efecaf` accepted on its first round, plus coordinator commits `518a90a` and
`e451a5c`. Suite **1361 passed / 99 skipped / 3 expected warnings**, from a
baseline of 1358.

`run_agent_iter` takes an optional `confirmation_records` list, snapshots its
length either side of each tool dispatch, and attaches whatever was issued to
that tool's result. `Session.audit_records` was indeed unused by production code
— only tests read it — so it was wired up rather than duplicated. The CLI passes
nothing and is unaffected. All eleven browser-routed `CONFIRM_FN` call sites are
covered generically, including image-returning acquisitions: `_with_confirmations`
injects into the text block of a list result, and both list-returning sites
(`tools.py:1417`, `:1816`) emit `json.dumps(payload)` there, so the `json.loads`
is safe today. **A future tool putting prose in that text block would raise**;
it surfaces as a stream error rather than silent loss, and is worth a defensive
guard if such a tool ever lands.

Verified by the coordinator: `execute_tool` never raises
(`tools.py:4628`) — a declined confirmation returns `{"error": ...}` — so the
decline path is decorated too, which is what makes the gate's G2 possible.

**One coordinator finding, fixed at `518a90a`.** The block added `summary` to
the confirmation record, and `AuditLog.append` **returns a redacted copy while
leaving its argument untouched**. The original raw record was appended to
`audit_records` — which is now handed to the model — and printed to stdout,
while only the JSONL got redaction. Demonstrated with a registered secret: the
JSONL read `Save [REDACTED]` and both other paths carried the real value.
This was harmless before the record carried a summary and is not harmless now,
and stdout is not a private channel: rig runbooks capture the serve process's
output to `*-session.txt` and ship it in evidence bundles. Now redacted once,
before either consumer sees it, with the existing test extended to assert on
`audit_records` and verified to fail without the fix.

**Operator decision recorded:** `summary` is persisted to the confirmations
JSONL. Without it, two confirmations of the same kind in one session are
indistinguishable after the fact, which defeats the point of the audit row. The
implementer proposed it, the coordinator accepted it, and the redaction path is
what makes it safe.

**Carried forward, not fixed here.** `_require_confirmation` (`tools.py:56`)
prints, reads stdin and returns a bool; it writes no audit record, and the CLI
passes no record collection to `run_agent_iter`. **A REPL session therefore has
no confirmation trail at all.** The implementer correctly declined to fix it
inside this block — it needs a CLI audit lifecycle rather than an extension of
the browser session — and recommends a separate block. Not yet scheduled.

### Rig gate G1/G2 — demo, 2026-08-03: **PASS. Block 4h is complete.**

Evidence: `block4h-demo-20260803-125647`, at `e451a5c`, pin `0`, clean tree,
suite **1345 passed / 0 failed / 115 skipped** — exactly the predicted count.

Both tool results carry the structured record. Approved:
`confirmations: [{kind: illumination, decision: approved, summary: "SELECT
ILLUMINATION SHUTTER: Core.Shutter = 'White Light Shutter' …"}]` alongside the
normal `status`/`writes`/expansion hashes. Declined: the same structure with
`decision: declined`, **alongside** the `error` and `hint` — a declined
confirmation is not a missing result, which is what the runbook required.

**And the defect itself is fixed.** Asked "did you prompt me during that channel
change, and what did I approve?", the agent named the kind, the decision and the
shutter selection. Compare Block 4f's demo run, where the same question got "I
didn't, actually — I never prompted you." The declined case is described
correctly too, including that the decline blocked the whole `set_channel`.

Worth noting rather than treating as a defect: in both answers the agent
carefully distinguishes that it did **not** itself present the prompt — the
harness did. That is accurate, and more honest than claiming authorship, while
still answering the operator's real question.

### Process finding: a mechanical check that cannot run still produces evidence

**Both `*-result-check.txt` files contain a PowerShell error, not a result.**
`python` was not resolvable in the shell that ran them — the Windows Store app
alias intercepted it — although `pytest` ran fine moments earlier in the same
session. The coordinator ran the checks against the returned histories instead,
which is why this gate passed rather than costing a round trip.

Three rules for future runbooks, because this one nearly turned a pass into an
ambiguous result:

1. **Capture `$LASTEXITCODE` for every mechanical check**, exactly as G0 does
   for the ancestor test. A check that produced a file is not a check that ran.
2. **Prefer a committed script under `design/` over an inline `python -c`.**
   Block 4g's `35-block4g-legacy-migration-check.py` ran on Windows without
   trouble; long single-line invocations are the fragile form, and they are also
   unreadable when they fail.
3. **Always return the raw artifact the check reads** — here the two history
   JSONLs — so the coordinator can re-run a check that did not execute. This
   run was recoverable only because the runbook asked for them.

Post-merge design gate:

- [x] Recorded in `design/21-sourced-and-still-wrong.md` §"F1 revisited": what a
      tool result now reports about human confirmations, and the residual — the
      record proves a confirmation was issued and decided, not that the operator
      understood it.

Rig gate (demo — no hazard needed, the shutter retarget is a selection):

- [x] Reproduce 4f's exchange: set a channel whose preset retargets
      `Core.Shutter`, approve it, then ask the agent whether it prompted and
      what for. It must answer correctly, and the mechanical check must assert
      on the tool result carrying the confirmation, not on the narration.
- [x] Repeat with a **declined** confirmation and show the agent describes that
      accurately too.

Post-merge design gate:

- [x] Record in design/33 (or design/21, which owns the browser confirmation
      gate) what a tool result now reports about human confirmations, and the
      residual: the record proves a confirmation was issued and decided, not
      that the operator understood it.

## 4c. [x] Reachable non-core stages — `named_stages` is never emitted — **MERGED 2026-08-03**

Branch: `design33/setup-named-stages`. Depends on 4b only for ordering, not
mechanism: **no schema change is needed**, `named_stages` already exists
(`safety.py:159`).

**Assigned 2026-08-03 from `3b99397`.** Coordinator-measured baseline at that
commit: **1361 passed / 99 skipped / 3 expected warnings** (the pre-existing
`StarletteDeprecationWarning` plus two `phase_cross_correlation` empty-image
warnings). The implementer branches from the tip of `origin/main` at the
assignment merge, in its own worktree.

Scope notes carried into the assignment, so the implementer does not have to
re-derive them:

- The interview's core-stage questions are the pattern to extend, not to
  duplicate: `first_launch.py:1014`–`:1030` resolves `assignments["xy_stage"]`
  and `assignments["focus"]`, looks up a driver technical range with
  `_technical_bounds` / `_device_property`, says `LIMIT SOURCE: …` when MM
  reports none, and calls `_bounds(...)` with the range as an
  Enter-acceptable proposal. The named-stage questions must behave identically,
  including the accepted-default-versus-typed-value transcript record.
- The device list is already in the inventory: every `facts.devices` entry
  carries `label` and `device_type` (`rig_inventory.py:384`–`:398`), so the
  candidate set is the `StageDevice` labels minus `assignments["focus"]`.
- `named_stages` is written at `first_launch.py:1174` as a hardcoded `[]`. That
  literal is the defect.
- Two live cross-checks already exist and must both stay green on the generated
  profile: `authorization.py:598` refuses a `named_stages` item that duplicates
  the core XY or focus device, and `authorization.py:623` refuses a **reachable**
  stage that has no declared range policy in guaranteed mode. **Correction
  (coordinator, 2026-08-03): the second does not fire for an undeclared non-core
  stage, and the assignment text was wrong to imply it might.** The implementer
  checked and the coordinator confirmed: `named_devices` is derived from the
  parsed config's own `named_stages` entries, so `reachable_axes` contains core
  XY, core focus, and *already-declared* named devices only. A loaded stage
  nobody declared is invisible to that check. It validates declarations; it
  cannot discover omissions. That is the fail-closed direction — an undeclared
  stage is simply unreachable via `check_named_stage` — so it is not a defect,
  and closing it is not this block's work. This block improves **reach**, not
  startup validation.
- `_metadata_default` (`first_launch.py:361`) already excludes a
  `StageDevice`/`XYStageDevice` position property from the `bounded-numeric`
  path, on the stated grounds that it must not bypass the fail-closed named/core
  stage policy. `named_stages` is that policy's authoring surface; keep the
  exclusion and do not route stage motion through `typed_actuators`.

Found 2026-08-02 while answering an operator question about how
`named_stages` differs from `typed_actuators`. `first_launch.py:1027` hardcodes
`"named_stages": []` and the interview never asks. `check_named_stage`
(`safety.py:1090`) fails closed — a stage with no entry may not be moved at all —
so **every single-axis stage that is not the core focus device is unreachable
after setup.** On M5 that is three of five: `SmarAct 1D`, `Thorlabs ELL17/ELL20`
and `Thorlabs ELL20`, with only `PIZStage` (core focus) and `SmarAct 2D` (core
XY) usable. The deployed config also has `named_stages: []`, so this is not a
regression — it is a reach gap neither authoring path ever closed, and it runs
directly against the operator's stated goal of controlling everything safely.

The three write paths for stage-like motion are genuinely distinct and the block
must not collapse them:

- Core focus / core XY → `stage.*` bounds, tools `move_stage_z` / `move_stage_xy`.
  The raw-property route is blocked on purpose by `_known_continuous_raw_pair`.
- Any other single-axis stage → `named_stages`, tool `move_named_stage`, keyed by
  device label, always µm, through MMCore's stage API.
- A raw numeric property that is positional but not reachable through the stage
  API → `rig_profile.typed_actuators`, kind `absolute-position`, keyed by
  device **and property**.

- [x] Ask for travel bounds for every loaded `StageDevice` that is not the core
      focus device, and emit `named_stages` entries. Propose the driver
      technical range where MM reports one, exactly as the core stages now do,
      and say so plainly where it reports none.
- [x] Do not emit an entry for the core focus device: `stage.z_min/z_max` owns it
      and `authorization.py:598` refuses the duplicate.
- [x] Decide what to do about an `XYStageDevice` that is not the core XY stage.
      `named_stages` is single-axis by construction, so this may be an honest
      exclusion with a printed reason rather than a silent omission.
- [x] Rig gate: move a non-core stage on M5 through `move_named_stage`, at a
      value inside the declared range and at one outside it, and show the second
      is refused.
- [x] Deliver a gate runbook, `design/35-block4c-gate-prompts.md`, **on the
      block's branch**, pinning the implementation with `git merge-base
      --is-ancestor <commit> HEAD` rather than an exact tip hash. It must include
      a demo-machine gate that needs no hazardous motion — a full interview
      producing a profile whose `named_stages` is non-empty and which passes the
      offline validator and starts a session — so the one M5 trip is spent only
      on what the demo core cannot represent. Rig-facing commands must be
      PowerShell/cmd-safe.

### Round 1 — returned 2026-08-03, two findings

Implementation `53e77e5` + `aaebc09`, runbook `e8d4732`. Independently
re-measured by the coordinator in the implementer's worktree: **1364 passed / 99
skipped / 3 expected warnings**, exactly baseline plus three new tests. Replaying
the captured M5 inventory through the new candidate rule produces the three
expected stages (`SmarAct 1D`, `Thorlabs ELL17/ELL20`, `Thorlabs ELL20`), and the
`Position (um)` spelling the implementer added does match the captured M5
property names — `SmarAct 1D` has no position property at all and correctly falls
to the typed-bounds path. The gate runbook's mechanical checks were validated
against a real captured history JSONL (`block4h-demo-20260803-125647`): the
`tool_use`/`tool_result` block shapes and the
`Safety constraint prevented this action:` substring are what `execute_tool` and
`agent.py` actually write, so the check will execute on the rig rather than
silently reporting `False`.

Two findings returned:

1. ~~**A continuous-focus offset stage would be authored as an ordinary named
   stage**, and should be excluded.~~ **WITHDRAWN by operator ruling the same
   day; see "Correction" below. It was never sent to the implementer as written.**
   The observation that prompted it stands — on the Nikon, `Core.Focus` is
   `TIZDrive` and `TIPFSOffset` is a separate `StageDevice`, so the new rule does
   author it — but the remedy was wrong.
2. **No non-core stage can be declined.** `_bounds` accepts only a finite number
   or Enter-to-accept, so every loaded non-core `StageDevice` becomes reachable
   and the operator has no way to say "not this one" — they would have to invent
   a degenerate range. Every other question in this interview has an exclusion
   path, and the pre-block state of these stages was unreachable, so the change
   widens authority with no opt-out.

Nit, not blocking: `_device_property`'s enumerated name set matches
`position(um)` but not `position(µm)` or `positionum`. The fallback is safe (it
asks the human) but a normalized `position`-prefix match would propose the driver
range on more rigs.

**Correction to finding 1 — operator ruling, 2026-08-03. Do not re-derive the
withdrawn version in a later block.** The coordinator read Block 4's "mark
PFS-offset workflows unsupported even when the offset has reviewed bounds" as a
requirement to omit the `named_stages` entry. It is not, and two sources say so:

- The design/33 impact row that item derives from states plainly that **"Phase 5
  can collect reviewed `TIPFSOffset` bounds"**, and that what a generated profile
  must not do is **imply that an in-range command was achieved or settled**. The
  remedy column assigns the fix to runtime capability work (block 6), not to
  setup.
- Block 0b's stopgap worksheet — authored under this checklist and shipped to the
  Nikon operator on 2026-08-03 — declares `TIPFSOffset` under `named_stages` with
  blank operator-supplied bounds, while excluding only `TIPFSStatus.State`. **The
  sanctioned exclusion boundary is the continuous-focus enable, not the offset
  stage.** Excluding the offset in setup would have contradicted the file that
  rig's operator is filling in right now.

Operator ruling: offset stages are how that rig was controlled before this
checklist began, and withholding the declaration removes working control to
protect against a defect that is a *reporting* defect. So setup authors the entry
like any other named stage. What it owes is honesty, not omission — a review note
recording (a) that `move_named_stage` can report the previous target as
`achieved_um` on this device class until block 6 lands, and (b) that an offset
write commands a servo, so the declared offset bound is not a bound on the
resulting TIZDrive excursion.

The Enter-acceptable driver-range default is **not** reopened by this: proposing
driver technical ranges for hazardous axes was settled by operator ruling
2026-08-01 and that note explicitly forbids re-litigating it in a later block.

### Round 2 — pushed 2026-08-03, awaiting the rigs

Implementation `7f68f33`, runbook `e537207`, plus coordinator corrections
`c34ee1e`. Branch `design33/setup-named-stages` is on `origin`, runbook included,
pinned at `7f68f33` by `merge-base --is-ancestor` (re-verified green after the
coordinator commit). Independently re-measured: **1368 passed / 99 skipped / 3
expected warnings** — 1364 baseline, +2 for the implementer's decline and
Nikon-offset tests, +2 for the coordinator's parametrized fragment test.

The implementer's round-2 work, accepted as written: each non-core `StageDevice`
now offers `y=declare bounds` / `x=exclude; leave unreachable`; a declined stage
asks no bound questions, emits no entry, and produces an `OPERATOR EXCLUSION`
note; the offset warnings were folded into the existing `CONTINUOUS-FOCUS REVIEW
QUESTION` note rather than a competing one; and `Position (µm)` / `PositionUm`
were added as explicit safe spellings rather than an unrestricted `position`
prefix match, on the stated grounds that a prefix could select a different
position-related property. That reasoning is accepted.

Two coordinator corrections made directly on the branch rather than returned,
both small and both operator-facing text or predicate breadth:

1. **The named-stage offset predicate required an "offset" fragment *and* a
   "pfs"-family fragment in the same label**, which is stricter than the typed
   branch immediately above it (that one matches "offset" alone). A stage
   labelled `Z Offset Stage` or `PFS Z` would have been authored with no warning.
   Widened to match any one fragment, with a comment recording the asymmetry: a
   false positive costs one review sentence, a false negative costs the operator
   the warning that `achieved_um` may be stale. Covered by a new parametrized
   test over both label shapes.
2. **The no-offset-declared branch of the note said "PFS-offset workflows remain
   unsupported until an offset stage is declared,"** which reads as *declaring
   the stage confers support* and drops the actual gating condition. Reworded:
   declaring one is a reviewed authorization to command it, not evidence that its
   reported `achieved_um` means arrival, which remains outstanding until Block
   6's settling/read-back work lands.

### Rig gate G1 — demo machine, 2026-08-03: **PASS**

Evidence: `block4c-demo-20260803-145921`, run at `6ae99de`. Every mechanical
check executed and recorded its own exit code: ancestor `0`, pytest `0`
(**1352 passed / 115 skipped / 3 warnings** — 115 skipped is the standing Windows
number, identical in the 4g and 4h gates, and 1352 is exactly 4h's 1345 plus this
block's seven new tests), interview `0`, unreviewed `check-config` `1`, reviewed
`check-config` `0`, session `0`, named-stage check `0`.

The substance, read from the raw transcript and history rather than the summary:

- The `y=declare bounds` / `x=exclude; leave unreachable` prompt was presented
  for `Aux Z` and no other stage. Micro-Manager reports no travel limits for
  `DStage`, so setup printed its `LIMIT SOURCE` line and required typed bounds;
  the operator entered 200 and 20000.
- `named_stages` contains exactly `Aux Z`, and **no entry for core focus `Z`**.
  `Aux Z.Position` was independently excluded from raw property writes, so the
  only route to that stage is `move_named_stage` — the three-write-path
  separation this block was written to preserve.
- The draft-versus-reviewed diff is a single line, `reviewed: false` → `true`.
  Nothing else was hand-edited into the generated profile.
- The corrected continuous-focus note shipped verbatim: with no offset stage
  declared it now says declaring one later is a reviewed authorization to command
  it, **not** evidence that its reported `achieved_um` means arrival.
- The live session went further than G1 asked. `list_stages` reported `Aux Z`
  under `other_single_axis`, and an in-range `move_named_stage` to 300 µm
  returned `achieved_um: 300.0, error_um: 0.0`.

Two runbook corrections made on the branch, neither a code defect:

1. **The out-of-range half of the guard is now checked on the demo too (G1b).**
   `Aux Z` is simulated, so both halves cost nothing here, and the M5 trip should
   confirm real hardware rather than discover a mechanism failure. Added with the
   same history check the M5 step uses.
2. The temporary demo `.cfg` was saved into the checkout, leaving `?? MMConfig_demo.cfg`
   in `status.txt`. The runbook now says to save it outside the repository.

Noted, not fixed: `_choice` records nothing when a default is accepted by Enter,
so the transcript shows the declare/exclude decision only implicitly — the bound
questions that follow for an accepted stage, the `OPERATOR EXCLUSION` note for a
declined one. Both directions are recoverable from the transcript, and
special-casing this one prompt would diverge from every other `_choice` site, so
it stays as is.

Still owed: **G2 on M5** — the real non-core stages, and the in-range/out-of-range
pair on hardware.

### Rig gate G2 — M5, 2026-08-03: **the acceptance criterion PASSED; one real finding, fixed**

Evidence: `block4c-m5-20260803-150556`, run at `6ae99de`, clean tree, pytest `0`
(1352 / 115 / 3 — same Windows numbers as G1).

**The block's acceptance evidence is met on real hardware.** `SmarAct 1D` moved
to 1200 µm and reported `achieved_um: 1199.9, error_um: -0.1`; the same device at
3000 µm was refused with `Safety constraint prevented this action: SmarAct
1D=3000.00 µm exceeds the maximum allowed (2000.00 µm).` and did not move. The
history check printed `True` on both lines.

All three expected non-core stages were authored — `SmarAct 1D` 0–2000 (MM
reports no position property for it, so setup printed `LIMIT SOURCE` and required
typed bounds), `Thorlabs ELL17/ELL20` 0–28000 and `Thorlabs ELL20` 0–60000 (both
driver ranges, Enter-accepted and recorded as `PROPOSAL ACCEPTED`). Core focus
`PIZStage` got **no** `named_stages` entry; its `Position` shows as "dedicated
policy: stage z travel bounds; not duplicated in rig_profile". Draft-versus-
reviewed is one line. And the three-write-path separation was exercised on a rig
that can actually violate it: both Thorlabs `Position (um)` properties were
excluded from `bounded-numeric` with "MM identifies a stage position property;
bounded-numeric cannot bypass the fail-closed named/core stage policy" — the demo
rig could not have tested that, because its `Z.Position` reports no limits and
never reaches the bounded-numeric branch at all.

**`m5-named-stage-check-exit.txt` was `1`, and that is not a code defect.** The
operator passed `SmarAct1D` (no space) as the expected device — the same typo
that made their first session call fail with `No device with label "SmarAct1D"`.
The coordinator re-ran the committed check against the returned profile with the
real label `SmarAct 1D`: both lines `True`, exit `0`. This is the third gate the
"always return the raw artifact the check reads" rule has rescued.

**Finding — the camera was reported as a continuous-focus offset stage.** The
generated M5 profile carried `possible offset stage(s) ['HamamatsuHam_DCAM']`,
and because that list was non-empty the note went on to assert servo motion, an
unmeasured Z excursion, and stale `achieved_um` — about a camera. Cause:
`HamamatsuHam_DCAM.CONVERSION FACTOR OFFSET`, an ADC conversion offset, matched
`"offset" in property`.

The name match is **pre-existing on `main`** and predates this block. What this
block changed is the consequence: before round 2 the non-empty branch printed one
generic sentence, and afterwards it printed specific and wrong physical claims.
Fixed in `d934dbf`: a name match now only counts for a device that is a loaded
stage, or for a typed actuator whose kind is itself `absolute-position` — an
offset *stage* is positional. The regression test is derived from this gate's
captured shape and was confirmed to fail against the pre-fix code rather than
merely passing beside it. Suite 1369 / 99 / 3.

Two observations that are not findings:

- The XY-travel prompt was answered blank 14 times before a value was typed (7
  times on the demo). Each blank produced its `SETUP REFUSAL`, which is correct —
  M5's `SmarAct 2D` genuinely reports no limits, so there is nothing to propose
  and the prompt already says "required; no default".
- The agent recovered from the operator's wrong device label on its own: it read
  the error, called `list_stages`, found `SmarAct 1D`, and reported the
  correction plainly rather than retrying blind.

Owed: a short re-gate of the offset-note fix. It needs no hazardous motion and no
M5 trip — regenerating a profile on any rig with a camera offset property, or
re-running the demo G1/G1b pass, is sufficient.

Post-merge design gate:

- [x] Reconcile Block 4's ticked item "Mark PFS-offset workflows unsupported even
      when the offset has reviewed bounds" and the design/33 impact row it came
      from with what actually ships: setup **authors** the offset entry and marks
      the *movement report* unverified. The item as worded no longer describes the
      code, and it is ticked, so a later reader would otherwise take the stricter
      reading — which is exactly what happened in round 1.
- [x] Record the residual on the offset warning: it is a **label-shape**
      heuristic, so an offset stage named without any of the five fragments is
      authored with no warning. It is a note, never a bound or a gate.
- [x] Record in design/33 that first-launch setup now authors `named_stages`,
      which devices it asks about and which it deliberately does not (core focus,
      and whatever the `XYStageDevice` ruling turns out to be), and the residual:
      a declared range is a reviewed travel bound, **not** evidence that the
      device is safe to move through it.

## 4d. [x] Rename and regroup the property-authorization schema — **MERGED 2026-08-03**

Branch: `design33/property-authorization-rename`. Depends on 4c merging. Split
out of 4b by operator ruling 2026-08-02 (see the scope-split note above 4b): a
config-breaking rename must not share a branch or a rig gate with a new
actuator kind.

Operator finding, 2026-08-02: `rig_profile` reads as a description of the rig,
but the rig's description is the inventory — `rig_profile` is the raw-property
*write-authorization map*. Worse, it is not the whole map: stage travel is in
top-level `stage`, exposure in `camera`, illumination in `illumination` (the
four built-in typed capabilities, deliberately not duplicated), while a typed
`illumination-power` actuator must appear in **both**
`rig_profile.typed_actuators` and `illumination.power_properties` or startup
refuses. `typed_actuators` is not even a field of the `RigProfile` object
(`safety.py:200`) — it lives under that YAML key for historical reasons only.

- [x] Proposed shape: `property_authorization` with `allowed_categorical`,
      `allowed_numeric`, `denied`. Confirm the grouping against what 4b's third
      kind and 4c's `named_stages` work actually left behind before committing
      to those three names.
- [~] ~~**A rename breaks every deployed config, M5's included.** Decide migration
      — accept both keys for a release, or ship a one-shot rewriter — rather
      than assuming a clean cut. Whichever is chosen, no rig may be left unable
      to start by the merge.~~ **Waived by operator ruling, 2026-08-03: a clean
      cut is what ships.** See "Clean cut" below. The item is struck rather than
      deleted because "no rig may be left unable to start by the merge" is a
      standing instinct this file should not appear to have abandoned silently —
      it was overridden by the person who owns every affected rig, on the record.
- [x] First-launch setup emits the new shape; the offline validator names the
      new paths in its diagnostics; both old-key and new-key configs are covered
      by tests.
- [x] Rig gate: start a session on M5 under its **existing** deployed config
      (whatever the migration promises), and under a regenerated one.
- [x] Deliver a gate runbook, `design/35-block4d-gate-prompts.md`, **on the
      block's branch**, pinning the implementation with `git merge-base
      --is-ancestor <commit> HEAD` rather than an exact tip hash. It must carry a
      demo-machine gate that needs no hazardous motion — an old-key config still
      starts, a full interview writes a new-key profile, that profile passes
      `check-config` and starts a session — so the M5 trip is spent only on the
      deployed file the demo core cannot represent. PowerShell/cmd-safe.

**Assigned 2026-08-03 from `052179d`**, the assignment merge itself. Baseline
**1369 passed / 99 skipped / 3 expected warnings** was measured one commit
earlier at `2f1852e`; `052179d` adds only this checklist edit, so the number
carries. (An earlier version of this line and the ledger row both said
`2f1852e`, which was the tip when the baseline was taken but not the commit any
implementer could branch from — the implementer caught it.) The implementer
branches from the tip of `origin/main` at the assignment merge, in its own
worktree.

Scope decisions carried into the assignment, so the implementer does not
re-derive them and does not widen the break:

- **The fourth key is `mode`, and it is not a property-authorization field.**
  `rig_profile` has four keys, not three: `config.py:92` reads
  `parsed.rig_profile.mode` to decide whether guaranteed mode is in force for
  the whole process, which governs far more than raw-property writes. Ruling:
  `mode` moves under `property_authorization` with its siblings for this block,
  because promoting it to a top-level key is a *second* independent config break
  and the operator's 2026-08-02 ruling is precisely that a config-breaking
  rename gets its own branch and its own gate. Record the mismatch as an
  acknowledged wart in the design gate; do not silently leave it undecided and
  do not fix it here.
- **`allowed_numeric` is confirmed as the name for `typed_actuators`.** All three
  kinds 4b left behind — `absolute-position`, `illumination-power`,
  `bounded-numeric` (`safety.py:76`) — are numeric, so the name covers the set.
  Do **not** rename the kind strings themselves; they are values, not keys, and
  renaming them widens the migration for no usability gain.
- **`named_stages` deliberately stays top-level.** It is a capability range
  policy consumed by `check_named_stage`, in the same family as `stage`,
  `camera.max_exposure_ms` and `illumination` — not a raw-property write
  authorization. 4c's work is what makes this answerable, and the block-4
  evidence note flagged it as an open choice; this is the answer. Say so in the
  migration note rather than leaving a reader to wonder why it did not move.
- ~~**Migration is dual-key acceptance, not a rewriter.**~~ **Reversed by
  operator ruling, 2026-08-03 — see "Clean cut" below.** The coordinator's
  original ruling was dual-key acceptance with a deprecation horizon; it was
  implemented in round 1 and is being removed in round 2. Kept struck through
  rather than deleted because round 1's code and both of its findings only make
  sense against it.
- **Verify against the real deployed M5 file, but do not commit it.**
  `block4-m5-20260802-100850/deployed-m5.reference.yaml` (sha256 beside it) is
  the config M5 is actually running; parse it with the branch's code and report
  the result. Rig facts stay out of the repo, so the committed regression
  fixtures are synthetic old-key configs.
- **Operator-facing refusal text names the old paths in at least seven places**
  — `authorization.py:826`, `:851`, `:852`, `:952`, `:1266`, `:1267`, `:1280`,
  `:1363`, `:1364` — plus `first_launch.py:662`, `README.md`, and
  `safety_config.example.yaml`. Those messages exist to tell an operator what to
  type; a rename that leaves them pointing at a key the file no longer uses is
  the same defect this block is fixing. They ship with the package, so they are
  implementation, not design gate.
- **Out of scope, explicitly.** No semantics change to any check. Do not delete
  `confirm_above_bytes` or `max_session_illuminated_ms` (4b/5 own those). Do not
  change the requirement that an `illumination-power` actuator appear in both
  `allowed_numeric` and `illumination.power_properties` — report it as still
  open, do not fix it under cover of a rename. Do not touch
  `forbidden_properties` / `allowed_properties`; report whether they are still
  reachable under schema 2 so the new `denied` name is not confused with them.

### Clean cut — operator ruling, 2026-08-03

**`rig_profile` is deleted outright. There is no migration path, no deprecation
window, and no special-case refusal for it.** The operator owns every machine
running this system, will replace the configurations by hand, and stated it
plainly: *"I do not care if there is any record of it ever existing. I do not
like it, and it is not important to any of my operations. I will only ever use
the new key."*

What follows from that, so round 2 does not have to re-derive it:

- The old key gets no bespoke handling and therefore no bespoke message. A
  config still carrying it falls through the existing strict-key validation and
  is refused as `rig_profile: unknown top-level key` plus
  `property_authorization: missing required property authorization map`. Those
  two together are already actionable; **do not** add a migration hint on top,
  which would be exactly the record the ruling declines to keep.
- **`schema_version` stays at 2.** Coordinator decision, stated so it can be
  overridden: a bump to 3 is itself a form of recording that the old shape
  existed, it adds a fifth edit to every config, and it buys precision the two
  refusals above already deliver. The cost is that "version 2" now names two
  incompatible document shapes in this file's history — acknowledged, and
  cheaper than the alternative given the ruling.
- **Every machine must be edited before it will start**, including the demo
  machine and M2. That is the accepted consequence, not a defect to design
  around.
- **The remote Nikon is the one exception worth handling deliberately.**
  `design/34-nikon-stopgap-worksheet.yaml` was shipped to that operator on
  2026-08-03 under Block 0c and is still in flight, and they are days of
  round-trip away with no ability to debug a refusal. Round 2 renames its keys
  so the repo artifact is correct; **0c owes them the corrected file**, and that
  is a coordination obligation this block cannot discharge on its own. Historical
  gate-evidence configs elsewhere in `design/` are records of what was actually
  run and are left alone.

Post-merge design gate:

- [x] Record the final schema shape and the two acknowledged warts (`mode` filed
      under a property-authorization key; `illumination-power` declared twice) in
      `design/33-authorization-map.md`, which documents the old key names in
      twelve places. State that the old key was removed without a migration
      path, by operator ruling, so a later reader does not reconstruct one.

### Rig gate G3/G4 — M5, 2026-08-03: **G4 PASS; G3 partly run, gap closed offline; one unrelated suite failure**

Evidence: `block4d-m5-20260803-170259`, run at `b455475`, ancestor check `0`,
`status.txt` empty.

**G4 PASS, and it proves more than the criterion asked.** A fresh M5 interview
wrote a profile carrying only the four new key names; `check-config` exited `0`
with `Schema: valid` / `Review: reviewed`; the session started against real
hardware. The turn (`20260803_171126_836272_microclaw_history.jsonl`) ran
`snap_and_analyze` on the Hamamatsu — ROI 2304×2304, Z 63.927 µm, SNR 3.45,
`focus_metric_valid: true` — **and then `move_named_stage` on `SmarAct 1D` to
1400 µm, achieving 1399.9 µm (−0.1 µm).** That is Block 4c's `named_stages`
authorising and bounding a real motion *through the renamed schema*, on the rig
that motivated 4c. The operator went past the runbook's "no motion" instruction
to get it; it is their rig and their call, and the result is the strongest single
piece of evidence in this block.

**G3 was run in part.** `check-config` against a hash-verified copy of the
genuine deployed config (`68d71aa5…`, matching the recorded hash) refused
correctly, naming `rig_profile: unknown top-level key` and
`property_authorization: missing required property authorization map`. Two steps
did not produce evidence:

1. **The live-refusal command shipped with the literal `<deployed-config>`
   placeholder unsubstituted**, so its exit `1` is `No safety config at
   <deployed-config>` — a file-not-found, not the old-key refusal. **This is a
   defect in the runbook, not the run:** a placeholder that must be hand-edited
   into four separate commands will eventually be missed in one of them, and it
   was. Any future runbook should bind such a path to a variable once
   (`$Deployed = "…"`) rather than repeating a placeholder. The step's intent is
   covered regardless — G2 proved the identical live refusal on the demo machine,
   and `check-config` proved it here on the real file.
2. **The rename-then-restart half was not performed on the rig at all** — no
   after-hash, no four-key diff, no renamed session. **This was the right
   operator call**, and this file told them so: the runbook's own warning is that
   renaming the deployed config before the merge strands M5 on an unmerged
   branch. Doing it as a normal post-merge deployment step is strictly safer.

**The gap that leaves is closed offline, against the genuine file.** The
coordinator took `m5-deployed-before.yaml`, confirmed its sha256 against the
recorded hash, applied the four renames, and diffed with line endings
normalised: **exactly four lines change, all of them keys** (`:52`, `:54`,
`:161`, `:378`), and the result passes `check-config` at exit `0` with
`Schema: valid` / `Review: reviewed`. So M5's real deployed config is proven
parseable under the new schema. What remains unproven is only that it passes
*live* validation after renaming — and that is not a 4d question: live validation
checks declarations against hardware and is wholly independent of key naming, so
it would have behaved identically before this block.

**One suite failure, and it is not this block's.**
`tests/test_webserve.py::test_browser_opens_only_once_the_port_accepts` failed;
1 failed / 1354 passed / 115 skipped. Grounds for accepting a red suite here,
stated explicitly rather than waved through:

- 4d does not touch `microclaw/webserve.py` at all, and its only edit to
  `tests/test_webserve.py` is a config-key rename inside a *different* test.
- The same commit passed this test on the demo machine minutes earlier.
- The test is self-documentedly race-prone: its own comment records that it
  "made the test a race against the scheduler, which it lost on a loaded rig",
  from a previous hardening pass. `_open_when_ready` polls with a 15 s budget
  (`webserve.py:788`); the thread exited having never connected, which is a
  starvation signature on a rig busy driving cameras and stages.
- Browser-open timing is orthogonal to a config-schema rename by construction.

Per the standing "confirm before fixing" rule a single failure is a data point,
not a diagnosis, so the operator re-ran it. **Confirmed flaky, 2026-08-03: three
consecutive passes in isolation at ~2.3 s each**, against a lost 15 s budget
inside the full-suite run. It is load, not the machine and not the code. Routed
for hardening rather than fixed here.

### Rig gate G1 re-run — demo machine, 2026-08-03: **PASS. The demo gate is complete.**

Evidence: `20260803_170006_552597_microclaw_history.jsonl` in
`block4d-demo-20260803-163136`, from a plain
`uv run microclaw --port $Port --safety-config demo-new-key.reviewed.yaml serve`.

Startup was clean — `Connecting to Micro-Manager…`, the API key from keyring,
`Microclaw GUI: http://127.0.0.1:8000`, and on exit
`[microclaw] Illumination off: Core.AutoShutter, White Light Shutter.State`. No
schema refusal, no authorization refusal, and **no demotion diagnostics at all**,
against a profile the interview generated from scratch under the new key names.

**The turn is what makes this conclusive.** The operator asked for a snap, and
`snap_and_analyze` ran against the live demo camera and returned real pixels:
ROI 512×512, exposure 10 ms, Z 50 µm, mean 327 over a 70–584 range,
`saturated_fraction: 0.0`. So the generated profile is not merely parseable — the
authorization map built from it **admitted a live tool call and the hardware
executed it**. That is a stronger result than the banner-sighting the criterion
originally asked for, and it came from running microclaw the ordinary way. The
`SNR 0.95 < 3.1` warning in the tool result is the demo core's featureless
simulated field and is expected.

Also worth keeping: the banner *did* appear on the console. That confirms the
buffering diagnosis rather than contradicting it — stdout is line-buffered on a
TTY and block-buffered the moment it is redirected, so the message an operator
sees interactively is exactly the one a log file loses.

**Demo gate status: G0 PASS, G1 PASS, G2 PASS.** M5 (G3, G4) is what remains.

### Rig gate G0–G2 — demo machine, 2026-08-03: **G0 and G2 PASS; G1 unverified**

Evidence: `block4d-demo-20260803-163136`. Run at `029b5f4`, the exact pushed tip
including both coordinator corrections; ancestor check `0`, `status.txt` empty.

Suite on the demo machine: **1355 passed / 115 skipped / 3 warnings**, against
the coordinator's 1371 / 99 / 3 on macOS. Different split, identical total of
1470 — sixteen tests skip on Windows. No failures. Recorded because a reader
comparing only the passed count would read a regression that is not there.

- **G2 PASS.** Both the offline validator and the live entry point refuse an
  old-key config, both nonzero, both naming `rig_profile: unknown top-level key`
  and `property_authorization: missing required property authorization map`.
  That is the clean cut working end to end, with no migration path offered.
- **G1 partial.** Interview exits `0`; the generated profile carries only the
  four new key names; the unreviewed draft is refused for the review reason
  alone with `Schema: valid`; the reviewed profile passes `check-config` at `0`.
  Microclaw's own interview transcripts landed in `demo-inventory/` and are
  intact. **But `demo-new-key-session.txt` is 0 bytes and no session history
  reached the synced folder, so the one thing G1 exists to prove — that a
  generated new-key profile starts a live session — has no evidence.** The
  operator reports the process did run and needed Ctrl+C to exit, which is
  suggestive (a startup refusal exits rather than hanging) but is not the
  banner. G1 is not marked passed on it.

Two runbook defects behind that, both fixed on the branch (`c579791`, `5aa0879`,
`e37db88`; the `--is-ancestor` pin survives all three):

1. **Redirecting an interactive or long-running process loses the output
   twice.** `> file 2>&1` on the interview left the console blank so the
   operator could not see the questions, and on `serve` the file was left empty
   at Ctrl+C. This is Block 4 round-1 finding 1 recurring in a new place: that
   finding established that microclaw must write its own transcript and that
   `> file 2>&1` is for *non-interactive* invocations only, and this runbook
   redirected both interactive commands anyway. The interview is now not
   redirected at all — its authoritative record is the transcript microclaw
   writes into `--evidence-out`, which worked on this run — and every `serve`
   uses `2>&1 | Tee-Object -FilePath …`, which is visible live and written
   incrementally.
2. **`--no-browser` makes "reaches its normal prompt" unsatisfiable**, and that
   was the acceptance criterion on every `serve` step. There is no browser and
   no prompt; the success signal is the `Microclaw GUI: http://<host>:<port>`
   banner at `webserve.py:876`, which prints only after the config is loaded,
   the live rig validated, and the authorization map built. Every criterion now
   names the banner. The wording was inherited from earlier runbooks, so this is
   worth carrying forward rather than treating as local to 4d.

G1's last two lines are owed a re-run before M5. Neither fix changes product
code, so G0 and G2 stand.

**Re-run 2026-08-03, still 0 bytes — the Tee-Object fix addressed the wrong
layer.** `demo-new-key-session.txt` was not even touched (mtime unchanged), so
nothing ever reached `Tee-Object`. The real cause is Python stdio buffering, not
PowerShell: the banner is a bare `print()`, stdout block-buffers as soon as it is
redirected or piped, `serve` then blocks forever, and Ctrl+C discards the buffer
— and `webserve.py:886` runs uvicorn at `log_level="warning"`, so there is no
second source of output either. That is also why G2 captured cleanly: a refusal
*exits*, and Python flushes on exit.

**Operator correction, 2026-08-03, and it was the right call.** The coordinator's
first fix (`6979301`) kept `--no-browser` and bolted on `PYTHONUNBUFFERED` plus a
`Test-NetConnection` probe from a second window. The operator's objection: this
is a lot of scaffolding to keep a flag *no ordinary session has ever used*, the
browser startup prompt is a non-event, and the session's history JSONL is already
the artifact gates return. Correct on all three. The scaffolding existed to work
around a defect the gate did not need to touch, and it had drifted from what
running microclaw actually looks like — a gate should exercise the normal
workflow, not a synthetic one.

Rewritten at `b455475`, **net −38 lines**:

- Every session step runs the plain `serve` command with no flag, no redirect,
  no capture. `PYTHONUNBUFFERED`, `Tee-Object` and the port probe are all gone.
- **The acceptance evidence is the session's `*_microclaw_history.jsonl`**,
  which is written only after live-rig validation and authorization-map
  construction and so cannot exist for a session that failed to start.
- One caveat the runbook now states, because it is not guessable and it is
  exactly why the demo run returned no JSONL: `AuditLog.append`
  (`conversation.py:153`) creates the file **lazily on first write**, so an idle
  session that is started and immediately stopped writes nothing. The gate
  therefore asks for one trivial read-only message before Ctrl+C.
- **Refusal steps keep `> file 2>&1`** and are called out as the deliberate
  exception: there the message *is* the evidence, and capture is reliable
  because a refusing process exits, which flushes the buffer.

The underlying product defect — `microclaw serve` is silent under any redirect
until it dies — is **not** fixed here; 4d is a schema rename. Recorded in the
carried-forward register and routed to block 5. The gate no longer works around
it; it simply stops depending on captured stdout.

### Round 2 — pushed 2026-08-03, awaiting the rigs

Implementation `fd4c5b6` + `c063f16`, runbook `9fbd464`, plus two coordinator
corrections in `029b5f4`. Independently re-measured by the coordinator in the
implementer's worktree: **1371 passed / 99 skipped / 3 expected warnings** —
round 1's 1372 minus the two dual-key tests, plus one pinning what an old-key
config now gets. `git grep` on the branch finds no remaining old key name under
`microclaw/`, `tests/`, `README.md`, or the Nikon worksheet; what is left is the
guard's private `_typed_actuators` state and its `admit_typed_actuators` method,
which are internal API rather than config keys.

The verbatim old-key refusal, which is what every machine will show once this
merges:

```
Invalid safety config:
<path>: rig_profile: unknown top-level key
<path>: property_authorization: missing required property authorization map
```

Two things the implementer did that were not asked for and are right:

- `c063f16` suppresses the derived `allowed_categorical: required in guaranteed
  mode` error when the whole `property_authorization` section is absent, so a
  missing map reports once instead of twice. Block 3's "report every problem at
  once" is about not exiting early, not about emitting an error's own
  consequences alongside it.
- `test_rig_inventory.py:417` translates
  `design/33-block5-demo-safety-config.yaml` key-by-key at test time rather than
  editing it, with a docstring saying why. That is the correct resolution of the
  instruction to leave historical gate evidence immutable — the evidence stays a
  true record of what was run, and the test still exercises the current schema.

Two coordinator corrections, both small enough to make directly on the branch
rather than spend a round on (`029b5f4`; the runbook's `--is-ancestor` pin
survives, which is the whole reason it is an ancestor check and not a tip hash):

1. `tests/fixtures/safety_config.yaml` still carried the removed key, so the
   tree contained a committed config that no longer parses. Nothing reads it —
   its header's claim that `conftest.py` does is already false on `main` — but a
   dead fixture that is also an invalid document is a trap for whoever wires it
   up next. Keys migrated; the deadness is left as a separate observation.
2. **G3 tells the operator to edit M5's deployed config in place, which strands
   the rig on an unmerged branch.** From the moment that edit is saved until
   this block merges, M5 will not start on any other checkout — including a
   `git switch main` on the same machine. The runbook already saved a pre-edit
   copy but never said it was a rollback or when to use it. It does now. This is
   Block 2's "sequence the config edit with the merge so the rig is not left
   down" in a new shape, and it is the ordering cost the clean cut buys.

### Round 1 — returned 2026-08-03, two findings — **both VOID, see "Clean cut"**

**Neither finding is to be implemented.** Both are defects in how round 1 served
the *legacy-key operator*, and the operator ruling above deletes that population
entirely. They are kept in full because they were the correct findings against
the migration design in force when round 1 was written, and because the shape of
finding 1 — a refusal naming a key the operator's file does not contain — is a
hazard any future rename can reintroduce.

Implementation `5674b5f`, runbook `69e0e2f`. Independently re-measured by the
coordinator in the implementer's worktree: **1372 passed / 99 skipped / 3
expected warnings**, baseline plus three new tests. The rename itself is right:
`git grep` on the branch shows every remaining old-name occurrence under
`microclaw/` is either the deliberate legacy-key handling in `safety.py`/
`config.py` or the guard's private `_typed_actuators` state, which is not a
config key. `deployed-m5.reference.yaml` hashes correctly and parses. The
runbook is good and covers all four startup cases with a before/after hash on
the deployed file. Two defects, both of which land squarely on G3 — the M5 step
that starts the deployed **old-key** config, which is the exact population the
dual-key bridge exists to protect.

1. **Every refusal message names a key the legacy operator's file does not
   have, and following it breaks their rig.** The messages now hardcode
   `property_authorization.*` (`authorization.py:826`, `:851`, `:952`, `:1266`,
   `:1280`, `:1363`). An operator on `rig_profile` who reads "declare it in
   `property_authorization.allowed_numeric`" and does so gets *both* keys, which
   this block deliberately made a hard startup error. So the message converts a
   running rig into one that will not start. This is the same defect the block
   was written to fix — a message pointing at a key the file does not use —
   mirrored onto the legacy side, and it is not hypothetical: the demotion
   diagnostics at `:826`/`:851`/`:952` are the ones M5's startup already emits,
   so G3's evidence would contain it.
2. **A legacy config starts a live session with no deprecation notice at all.**
   The diagnostic is raised in `validate_safety_config` (`config.py:72`), but
   live startup goes `load_safety_config_or_exit` → `load_safety_config` →
   `ParsedSafetyConfig.from_yaml` (`config.py:183`, `:153`) and never calls the
   validator. It therefore fires only under `microclaw check-config`, which an
   operator whose rig already starts has no reason to run. A deprecation nobody
   is told about is not a migration path; the horizon then arrives as an
   outage. The runbook is honest about this — G3 asserts the warning only on
   `check-config` — so the gate would have passed with the gap standing.

## 5. [x] Deployed-config hygiene and the `init` path — rig config review — **MERGED 2026-08-04**

Branch: `design33/deployed-config-hygiene`

**What this block inherits, gathered here so a cold coordinator does not have to
reconstruct it from the carried-forward register.** Three items were routed to
block 5 by earlier blocks; all three are register rows, and the register is the
authoritative wording:

- **From Block 3, deliberately skipped there as its optional item:** detect
  limits still equal to `safety_config.example.yaml`'s fictional values and say
  so. Block 3 skipped it precisely because block 5 owns the copy-the-example
  path that creates the problem.
- **From Block 4d's gate:** `microclaw serve` produces no output at all when
  piped or redirected until it dies — bare `print()` banner, block-buffered
  stdout, a server loop that never returns, and uvicorn at `log_level="warning"`
  suppressing the only other source. An operator following our own runbooks and
  logging to a file gets an empty file. A `flush=True` on the banner is probably
  the whole fix.
- **From Block 4d's G0:** `tests/test_webserve.py::
  test_browser_opens_only_once_the_port_accepts` is confirmed flaky under
  full-suite load (failed M5's G0; three isolated passes at ~2.3 s after). It
  will keep failing G0 for future blocks, where a red suite is supposed to mean
  something. Do not simply raise the timeout again.

**Sequencing note.** The first item below edits M5's deployed config, and that
file *also* needs Block 4d's four key renames before M5 will start at all (see
the boundary note's outstanding-actions list). Those are one editing session, not
two — but the rename is required for the rig to run and the budget fix is a
reviewed judgement call, so do not let the second silently ride along with the
first.

- [x] Fix the deployed M5 config's acquisition budgets, which were copied from
      the fictional example including the exposure limit (design/33 `:790`). This
      is a rig-config review item with a human in the loop, not an inferred edit.
      **Coordinator + operator, not the implementer** — the file lives on M5, and
      the values are the operator's to set. Sequenced with 4d's four renames as
      one editing session, per the note above.

      **CLOSED by operator ruling, 2026-08-04. Do not re-open it in a later
      block, and do not raise M5's budgets as a finding again.** The ruling:
      *users may set these as high as they like; that is their problem. Setting
      them requires a manual edit, so anyone who does it is aware of what they
      are doing.* The premise the item was written on — values silently
      inherited from a fictional example — no longer holds anyway (see the
      measured table below): they were edited deliberately. The safety argument
      that survives is that the `confirm_above_*` tier is separate, unchanged,
      and still forces a human confirmation, so a high ceiling is not an absent
      human. A coordinator meeting M5's ~317,000-year duration cap should read
      this paragraph, not re-derive the finding.

      **The premise above is stale — corrected 2026-08-03 by replaying captured
      evidence, and the correction makes this item more urgent, not less.** The
      coordinator ran Block 4d's hash-verified `m5-deployed-before.yaml` (after
      the four renames) through this block's new example-limit detector. M5's
      budgets are *no longer* example copies. They have been edited to values
      that do not meaningfully bind:

      | key | M5 deployed | example |
      |---|---|---|
      | `acquisition.max_duration_s` | `1e21` | `3600` |
      | `acquisition.max_illuminated_ms` | `1000001720` | `600000` |
      | `acquisition.max_bytes` | `1.06e12` | `5e10` |
      | `acquisition.max_frames` | `100000` | `10000` |
      | `camera.max_exposure_ms` | `10000.0172` | `5000` |

      Only `acquisition.confirm_above_illuminated_ms` (`60000`) and `stage.z_min`
      (`0.0`) still equal the example, and both are plausibly coincidental. `1e21`
      seconds is ~3×10¹³ years; `1000001720` ms is ~278 hours of illumination.
      These read as limits raised until they stopped refusing something rather
      than measured hardware bounds, which is a different and worse failure than
      the fictional-example copy this item was written for. **Do not close this
      item by observing that the example values are gone.** design/33 `:790` must
      be corrected in the post-merge design gate.
- [x] Decide what `microclaw init` becomes now that Phase 5 exists: redirect to
      setup, keep it as the hand-authoring path with a pointer, or keep both.
      Whichever — the copy-the-example path is what put fictional limits on a
      real rig, so it must not remain the *recommended* route.

      **Operator ruling, 2026-08-03: redirect, keep an explicit escape hatch.**
      `init` no longer copies the example by default. It prints the real path
      (`first-launch-setup` → review → restart), offers to run
      `first-launch-setup`, and copies the fictional example only under an
      explicit opt-in flag for deliberate hand-authoring. `init` is **not**
      deleted: design/17's installer and desktop-shortcut sequence calls it
      (`design/17-install-and-desktop-shortcut.md:502`–`:505`, `:640`), and
      operator notes say `init`. The decision is settled — do not re-litigate it
      in the implementation or a later block. What remains for the implementer is
      the wording, the flag name, and making the non-interactive path behave.
- [x] Update README and any first-run documentation to describe the real path:
      install → `inspect-rig` / setup → review → restart. Include the offline
      validator from block 3. Also `install.bat`, which is where the path
      actually starts.
- [x] Re-check `safety_config.example.yaml`'s framing under the new flow. It
      stays fictional by design; the question is whether it is still the first
      thing a new operator meets. It is not: the header now names it a
      hand-authoring reference and points at setup.

### Rig gate — demo machine, 2026-08-04: **G0–G3 PASS**

Evidence: `block5-demo-20260804-071555` (run 1) and `block5-demo-20260804-073354`
(run 2, at the corrected runbook `8028145`). M5's G4 has not been run.

What the product proved, all on run 2:

- **The `serve` 0-byte-log defect is fixed.** `serve-redirected.txt` is 260 bytes
  containing `Microclaw GUI: http://127.0.0.1:8000`. Block 4d got 0 bytes under
  two different capture methods; that is what routed the item here.
- **Installation no longer touches the rig.** `installer-rig-command-count` = `0`
  and `installer-next-steps-count` = `2`, with the ZMQ prerequisite at
  `install.bat:46` preceding the printed `init` at `:49`.
- **The example-limit detector discriminates.** All 14 limits flagged on an
  unmodified example copy; **zero** on the reviewed synthetic negative control;
  exactly two (`acquisition.confirm_above_illuminated_ms`, `stage.z_min`) on the
  demo machine's real reviewed profile — the same two the coordinator measured
  offline on M5, and both plausibly coincidental.
- `init` writes nothing on either the declined-interactive or the
  non-interactive path, and `--from-example` still works with the unreviewed
  gate intact (`check-config` exit `1` naming `REVIEW REQUIRED`).

**Every defect this gate found was in the runbook, not the code.** Four, all
coordinator-authored and all fixed on the branch:

1. Six `Select-String` results were judged by `$LASTEXITCODE`, which PowerShell
   sets only from *native* executables — both negative controls included. Now
   match counts (`c0344f2`).
2. The invocation was hand-substituted per command; run 1 typed `uv runmicroclaw`
   in the fifteenth of fifteen and that step recorded a `uv` usage error instead
   of its check. Now an `mc` function (`8028145`).
3. `Start-Transcript` cannot capture a child process's console writes on Windows
   PowerShell 5.1, so the interactive step produced an empty transcript —
   **the identical finding to Block 4 round 1**, reintroduced by this runbook
   after that block had already fixed it. Now the manual copy-paste remedy
   (`8028145`).
4. The `mc` fix wrapped only `microclaw`, leaving `python -m pytest` bare and
   hardcoding `uv run` into the one `cmd /c` step. A `$Run` prefix now selects
   both (`c48edc1`). Operator finding.

Two process notes worth carrying, because three of the four are the same shape —
**a runbook is a deliverable and needs reviewing like one**:

- A gate whose evidence is judged by the wrong mechanism cannot fail. Findings 1
  and 3 would both have reported success regardless of what the code did.
- Judge a cross-platform suite by failures and collected total, not the passed
  count: demo measured 1361 passed / 115 skipped against the coordinator's
  1377 / 99, and both collect 1476.

The one gap — `init-interactive-copy-paste.txt` was not produced, so the prompt
was confirmed only by the operator having answered it, and `wrote = False` alone
does not prove a prompt appeared since a run that never prompted writes nothing
either — was **closed by the coordinator on 2026-08-04** with a local pty run:
the string `Run first-launch setup now? [y/N]` is emitted, `N` declines, and no
file is written. Platform-independent Python, corroborated by the rig's own
`wrote = False`, so it did not cost another operator trip.

### Rig gate — M5, 2026-08-04: **G0 + G4 PASS**

Evidence: `block5-m5-20260804-092508`, at runbook `c48edc1`. Suite 1361 passed /
115 skipped / 3 warnings, no failures; clean tree; both ancestry pins `0`.

M5's deployed config **already carries Block 4d's four key renames** — outstanding
operator action 1 from the session-boundary note is therefore done for M5, and the
file validates at `check-config` exit `0`.

`check-config` reported exactly two example matches,
`acquisition.confirm_above_illuminated_ms` and `stage.z_min` — **the same two the
coordinator predicted by offline replay before the rig ran.** Replaying a captured
artifact predicted a live result for the third block running.

**The finding this gate exists to produce.** G4 recorded the deployed budgets, and
they moved again between 2026-08-03 and 2026-08-04:

| key | example | M5 08-03 | M5 08-04 |
|---|---|---|---|
| `max_frames` | 10000 | 100000 | 100000000 |
| `max_duration_s` | 3600 | 1e21 | 1e13 |
| `max_illuminated_ms` | 600000 | 1000001720 | 1000001720000 |
| `max_bytes` | 5e10 | 1.0616832e12 | 1.0616832e15 |
| `camera.max_exposure_ms` | 5000 | 10000.0172 | 10000.0172 |

Three hard caps rose a further 1000× in a day. As deployed, `max_duration_s` is
~317,000 years, `max_illuminated_ms` ~31.7 years of illumination, `max_bytes`
~1.06 PB. **What still binds is the `confirm_above_*` tier**, which is unchanged
and sane (1000 frames / 3600 s / 60000 ms) and forces a human confirmation; the
automatic ceiling does not bind at all. Guaranteed mode requires the nine to be
finite and positive, which they are, so nothing refuses. This is the block's first
item and it remains **open and operator-owned**.

## 5b. [x] Guided install and acknowledgement retries — **MERGED 2026-08-04**

Branch: `design17/guided-install`

Raised by the operator on 2026-08-04 after testing Block 5's install path
end-to-end on M5. Both items are usability, and the first deliberately re-enters
territory Block 5 removed.

- [x] **`install.bat` drives the whole first run in one terminal.** After the
      shortcut, it tells the user to start Micro-Manager and enable the ZMQ
      server, waits for confirmation, then **verifies** the bridge is actually
      up. Three attempts, then stop asking.
- [x] **The typed hardware-contact acknowledgement gets three tries.**
      `I ACKNOWLEDGE HARDWARE CONTACT` (`__main__.py:436`–`:444`) is easy to
      mistype, and one typo currently discards the whole run.

**This does not revert Block 5's F1 fix — read why before touching
`install.bat`.** F1 was that `init` ran at a point where the ZMQ server was
*guaranteed* down, so `Core()` blocked (measured: no return in three minutes)
and `if errorlevel 1 exit /b 1` could fail an otherwise-successful install. The
operator's design removes the cause rather than the step: the bridge is
confirmed up *before* setup is invoked. Two invariants carry forward and are
non-negotiable:

1. **No unbounded blocking call on any path.** The readiness check must be
   bounded, and `Core()` must never be reached until the check has passed.
2. **A completed installation must never be reported as failed** because the
   microscope was not ready. Everything up to the shortcut succeeded.

### Rig gate — 2026-08-04: **PASS**

Demo `block5b-demo-20260804-124610`, M5 `block5b-m5-20260804-123946`, both at
runbook `644e592`. Suite 1377 passed / 115 skipped on Windows against 1393 / 99
on macOS — 1492 collected either way, zero failures.

- **F1 has not returned.** `installer-no-mm-exit` = `0`: three failed readiness
  checks with Micro-Manager deliberately closed, and the installer still reports
  a complete installation.
- **The operator-facing failure is one clean line.** Friendly-message count `1`,
  traceback count `0`, where round 1 emitted eleven traceback markers.
- **The real handshake works on a live rig.** M5's `check-bridge` returned
  `MMCore version 12.5.0` at exit `0` — a bare TCP listener cannot produce a
  version string, which closes the question the block was set.
- **The acknowledgement retry works on M5.** Transcript L34 near miss, L35
  `2 tries remaining`, L36 correct, setup exit `0`, draft written with
  `reviewed: false`. The draft round-trips: `check-config` exit `1` unreviewed,
  exit `0` reviewed.
- **G3**: three bad attempts, correct countdown, `SETUP REFUSAL` without
  connecting, no draft written.
- **The 30 s enumeration cap is validated by a passing M5 run**, not only by
  inference from timings.
- **`install.bat` end-to-end on M5 was run by the operator and confirmed
  working.** No evidence bundle was captured for that run; it is recorded here as
  operator confirmation, which is what it is.

**Every defect this gate found was in the runbook, again — three of them, all
coordinator-authored, and all of the same shape as Block 5's: a criterion that
could not fail.**

1. G3 asserted `"Connecting to the already-running"` appears zero times when no
   connection was made. That phrase is also in setup's INTRO
   (`first_launch.py:42`), so it always scores at least `1` — measured `1` on the
   real transcript. **G3 would have failed a passing run.** Now matches
   `"Micro-Manager Core for read-only"`, unique to the real connect message.
2. The near-miss check matched a substring of the prompt, which echoes the
   required string, so it could never be `0` and proved nothing about whether a
   typo occurred. Measured `2` with one typo typed. Now anchored on `CONTAC$`.
3. G1's `$env:APPDATA` redirect had no `finally`, so an interrupted installer
   would have silently redirected every later command in that window. Also, `uv`
   keeps managed interpreters under `%APPDATA%\uv`, so the redirect made it
   re-download a full CPython into the evidence bundle.

Carry the lesson from Block 5 forward with an addition: **read every match
pattern against a real transcript before shipping it.** Two of these three were
only visible by running the pattern over captured output.

Post-merge design gate:

- [x] Update design/17 §"Block 5: the first-run path moved" — its "installation
      ends at the shortcut" statement becomes conditional, and the reasoning for
      *why* the ordering changed must survive the rewrite rather than being
      deleted as obsolete.
- [x] Update README §"Install (Windows)" steps 4–5, which currently describe the
      manual path the operator validated on 2026-08-04.

---

## 5. [x] Deployed-config hygiene — post-merge design gate (kept with block 5)

- [x] Record the final first-run path in design/33 and design/17 (install and
      desktop shortcut), which currently describes the `init`-centred flow.
      **`install.bat` no longer runs `init` at all** — design/17 v2's premise
      that installation puts a safety config in place is now false, and
      `:16`–`:20`, `:181`, `:228`–`:250`, `:281`–`:285`, `:502`–`:506` and `:640`
      each need reconciling. Implementer-reported, coordinator-owned.
- [x] Correct design/33 `:790`: M5's budgets are no longer example copies. See
      the measured table in this block's first item.
- [x] design/33 `:347`–`:363` (esp. `:350`, "init still copies the example by
      default") and `:1201`–`:1205` ("not done here" for the example-limit
      detector, now implemented); impact summary `:110`, `:157`–`:160`.

---

# Track B — Nikon PFS and position reporting (blocks 6a, 6, 7a, 7b, 8)

Source: `design/40-pfs-five-sessions.md`, which supersedes
`design/34-nikon-pfs-tizdrive-findings.md` wherever the two disagree about the
rig. **Rescoped 2026-08-05.** Read design/40 before assigning anything here; the
paragraphs below say what changed and why, and every block's item text was
rewritten against measured session evidence rather than the probe kit's plan.
design/40 also carries the **code stubs**, the **cross-rig regression bar**, and
an **orchestration board** for running these blocks across sessions — that board
tracks state, this file still owns scope and the ledger.

**The binding constraint on every block below: Demo, M2 and M5 must be
unchanged.** All of this was learned on one unusual rig, and CLAUDE.md's first
rule is that `microclaw/` works for a generic Micro-Manager installation. A fix
that makes the Nikon work by assuming a Nikon is a defect even if the Nikon gate
passes. design/40 names, per rig, the regression each block must prove absent.

## What changed, and why the old plan is not the plan

**The probe kit was never run.** The operator could not run the scripts alone —
the constraint this file states at `:47`, which the kit was shipped anyway. What
came back instead is five real microclaw sessions from 2026-08-05 (histories in
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/pfs_fix/`), and they answer
more of design/34's questions than the kit was designed to. **Block 0c is closed
against that evidence, not against probe output.**

Four things follow, all argued in design/40:

1. **PFS engages under pure software control** — four locks, no GUI, no coarse
   wheel, and one held across a 25-tile survey. The loop is
   `PFS Off → step Z → PFS On → read Status → repeat`. The capability is not in
   doubt; the missing thing is a primitive for it.
2. **The blanket exclusions were the blocker, and they land last in the old
   order.** One session was spent climbing a 60× oil objective 450 µm past the
   recorded engage height only to discover `TIPFSStatus.State` was never
   writable; another ended with the offset refused while PFS held a lock. Both
   were safely liftable by declaration, and the remote operator lifted them by
   hand mid-session. **Block 6a now lands first.**
3. **Three design/34 premises are refuted** — there is no single approach
   position, the named-stage staleness signature did not reproduce (the live
   defect is a mismatch reported as success), and "`move_stage_z` disables PFS"
   is still untested but no longer blocks anything.
4. **Seven defects surfaced that are not Nikon work at all.** They are block 13
   in the new Track D.

**The design/34 `:223`–`:234` rig-value list is no longer a gate on scoping.**
It asked for a per-objective approach range, an engagement position and its
repeatability. Sessions show the engage height is not a stable number
(2912, 2498, 2532, 2450 across four locks), so a bounded search replaces it.
What is still genuinely wanted — capture range, safe step, timeouts, adapter and
firmware versions — is now collected *by* block 7a's search as it runs, not
before it is written.

## 6a. Authorize the focus system, and say what is unassigned

Branch: `design34/focus-system-authorization`

**Assign this first.** It is the only block that changes whether the remote
operator can work at all, and it replaces block 8's blanket-exclusion lift for
everything except Phase 5 generation.

- [ ] Stop excluding continuous-focus enable properties outright. A rig's
      autofocus-device enable is a reviewable categorical declaration like any
      other; the hazard on this hardware is the Z move that precedes it, which
      `check_z` already owns. Replace the block-4 exclusion
      (this file `:706`, `:724`) with a declaration setup can offer.
- [ ] **`first_launch` must offer `absolute-position` for stage-position
      properties instead of excluding them** (`first_launch.py:367`) — **but
      only for a stage that already has a reviewed travel entry.** The kind
      exists, `safety.py:961`–`973` routes it through `check_named_stage`, and
      `authorization.py:743`–`766` validates that its bounds may only narrow the
      named-stage bounds. Setup emits that kind elsewhere
      (`first_launch.py:989`) and simply never offers it here.
- [ ] **Close the hole this would otherwise open.** `safety.py:961`–`973` routes
      an `absolute-position` write through a bound *only* when the device is the
      core focus device, the core XY device, or already in `named_stages`. On
      any other stage it falls through every branch and is gated by nothing but
      its own declared min/max — while `check_named_stage` (`safety.py:1165`)
      refuses that device outright. **M2 is the shape that breaks**: its
      `named_stages: []` is a deliberate refusal
      (`design/29-block9-m2-safety-config.yaml:116`–`123`), and a typed entry
      would quietly reinstate motion on a stage the operator declared
      unreachable. The validator must reject an `absolute-position` declaration
      that no travel bound governs. Net effect: stricter than today everywhere
      except the Nikon offset, which it unblocks.
- [ ] Reconcile the runtime refusal text (`authorization.py:1272`) with what
      setup writes. Today the refusal names `allowed_numeric` as a legal home
      for a pair whose generated config comment says it cannot go there. The
      refusal is right and setup is wrong; a remote operator should not be the
      one to discover that.
- [ ] **An unassigned `Core.Focus` must be diagnosed, not swallowed.**
      `get_system_state` (`tools.py:806`–`809`) turns `No device with label ""`
      into `z_stage: "unavailable"`, and `get_z_position`, `move_stage_z` and
      autofocus all raise the raw Java exception. Two sessions were lost to it.
      microclaw cannot fix the role itself — it is a device-assignment property
      it correctly excludes — so it must name the cause and the remedy.
      Generic: any MM config with several single-axis stages and no role line.
- [ ] **`get_focus_lock_state` must not be EMU-only** (`tools.py:4506`). On a
      rig with a working hardware focus lock it answers "No EMU configuration —
      cannot read a focus lock", and `agent.py:149` instructs the model to trust
      that answer. **Additive only: the EMU branch stays first and unchanged, so
      M5's payload — including its `qpd` block — is byte-identical.** The
      `get_auto_focus_device()` path is a fallback reached only when the EMU map
      carries no `focus_lock`, never a preferred source. Do not build the typed
      capability here — that is 7a — just stop returning a false negative.
- [ ] Off-rig tests: a config with no `Core.Focus` role; a stage-position
      property offered as `absolute-position`, refused when its bounds widen the
      named-stage entry **and refused when no travel bound governs it at all**;
      a focus-lock read on a rig with no EMU map; and an M5-fixture read proving
      the EMU payload is unchanged.
- [ ] **Cross-rig regression bar applies** — see design/40 §"Every fix here must
      leave Demo, M2 and M5 exactly as they are". Replay the captured demo
      inventory and the M5 `config.uicfg` fixture and diff the emitted profile
      against today's; a synthetic fixture has manufactured a fake defect and
      hidden a real one before.

Rig gate:

- [ ] Nikon rig: from a fresh start, engage and disengage PFS and move the
      offset without hand-editing `safety_config.yml`. The operator's current
      hand-declared file is the acceptance reference, not the target — setup
      must be able to *produce* an equivalent one.
- [ ] Nikon rig: with `Core.Focus` unassigned, every Z-facing tool reports the
      unassigned role and the remedy. Then assign it and show them working.
- [ ] Any rig: `get_focus_lock_state` on a rig with no EMU map reports the real
      lock state or a reason that is true.

Post-merge design gate:

- [ ] Record in design/33 what setup now emits for a stage-position property and
      for a continuous-focus enable, and strike the "PFS-offset workflows
      unsupported" marker with the evidence that lifted it.

## 6. Measured position read-back and the move failure contract

Branch: `design34/measured-position-readback`

Two real defects, independent of the PFS question, affecting every rig.
**Rewritten 2026-08-05:** the offset half is not the defect design/34 recorded.

- [ ] `move_stage_z` (`microclaw/tools.py:438`) returns the **requested** target
      as `z_um` with `"status": "Moved."` and performs no read at all. Sessions
      show the consequence sharply: commanded 2490, the servo settled at 2532;
      commanded 2900, settled at 2912. Return **measured** Z.
- [ ] **A move whose measured position misses its target beyond tolerance is a
      typed failure, not a success carrying an error field.** This is the
      correction to design/34 `:236`–`:274`. `move_named_stage`
      (`tools.py:519`–`539`) already reads back and reports `error_um`; the
      staleness signature did *not* reproduce. What reproduced is worse — at
      11:40 it returned `{"requested_um": 5, "achieved_um": 27.85,
      "error_um": 22.85}` **as a success**, and the agent came within one tool
      call of sweeping a focus curve against an axis that had not moved.
- [ ] Freeze the result contract before coding: success reports at least
      `requested_um`, `measured_um`, `tolerance_um`, and `within_tolerance:
      true`; mismatch or timeout is a typed failure carrying the same measured
      fields, elapsed time, and last device status. Define the tolerance source,
      timeout, polling interval, required consecutive in-tolerance samples, and
      stability window in the design reconciliation; none may be an unexplained
      magic constant.
- [ ] Poll until the measured position is within tolerance **of the target** and
      stable, or until timeout. The tolerance-of-target condition is the gate: a
      stability check alone passes immediately at the old position.
- [ ] Image acquisition and focus scoring must not begin until the settling
      check succeeds.
- [ ] Apply the same read-back rule to `MicroscopeController.set_z`
      (`microclaw/controller.py:552`).
- [ ] Off-rig tests must include both observed shapes specifically: a fake whose
      `Busy()` clears before motion starts (proving the stability-only check
      passes and the target-tolerance check does not), **and** a fake that stops
      short of its target at a hard mechanical floor, proving that is reported
      as a failure rather than a success with a large `error_um`.

Rig gate:

- [ ] Nikon rig: three consecutive `TIPFSOffset` moves reporting their own
      achieved positions, with the focus stage recorded alongside. **The 11:40
      session is the pre-fix baseline** — probe S was never run and is not owed.
- [ ] Nikon rig: a commanded Z followed by a PFS engage reports the settled
      position, not the request (the 2490 → 2532 case).
- [ ] Any rig: a move whose measured result differs from the request is reported
      as a failure, not a clean success.

Post-merge design gate:

- [ ] Record the settling contract and the read-back rule in design/40 and
      wherever the tool contracts are documented. State that reviewed bounds are
      **not** proof that an asynchronous device achieved or settled at its
      target, and correct design/34 `:246`–`:250`, whose table describes a
      signature that did not reproduce.

## 7a. Typed continuous focus and the bounded engage search

Branch: `design34/continuous-focus-capability`

**Merged from the old 7a and 7c**, and moved above 7b. They were split when the
search was hypothetical; the sessions performed it by hand more than thirty
times, so its shape is determined and the capability is not worth having
without it.

- [ ] Add operations that discover the configured autofocus device via
      `get_auto_focus_device()`; enable and disable continuous focus through the
      CMMCore API; report enabled and locked state; and wait for a well-defined
      lock, failure, or timeout. `enableContinuousFocus`,
      `isContinuousFocusEnabled` and `isContinuousFocusLocked` are in the
      mmcorej 2.0.3 API (`tests/fixtures/mmcorej-cmmcore-2.0.3-methods.txt`) and
      are unused by production code.
- [ ] Add the bounded engage search. Shape, from design/40 D3:

      ```python
      def engage_continuous_focus(ctrl, guard, *, z_start, z_ceiling, step_um,
                                  settle_s, timeout_s) -> dict:
          """Off → step → On → poll status → stop on lock or ceiling."""
      ```

      Every Z step goes through `check_z`; the ceiling is a caller argument with
      no default, so an unbounded search cannot be requested by omission.
- [ ] **Record the focus stage and every offset stage at entry and at lock.**
      The servo moves them: at 11:40 a lock at 2500 pulled `TIPFSOffset` from
      27.85 to 183.55 by itself. A result that reports only the focus axis
      describes half the machine.
- [ ] **No configured approach position.** design/34 `:173` and the old 7c both
      assumed one; four measured locks (2912, 2498, 2532, 2450) say there is
      none. Search, do not aim.
- [ ] **Keep it generic.** Driven by `get_auto_focus_device()` and caller bounds.
      No Nikon-named recipe, no baked-in height. Ti-with-PFS is as unusual as M5
      is; rig facts belong in design/40 and the rig profile, never in
      `microclaw/`.
- [ ] Lock is binary on this hardware — `Focus lock failed` at every wrong
      height, `Locked in focus` at the right one. Do not build a hill-climb;
      there is no gradient to follow.
- [ ] Report an unreached lock as a typed failure carrying the heights tried,
      the last status string, and both axes' positions — the information the
      agent had to reconstruct by hand in every session.

Rig gate (Nikon):

- [ ] Show enable, disable, enabled-state, locked-state, and timeout/failure
      reporting.
- [ ] Engage from below the capture range through the typed capability alone,
      in one call, and reach `Locked in focus`. Compare the heights tried and
      the achieved lock against the 13:18 session, which did it by hand.
- [ ] Show the ceiling refusing: a search whose ceiling is below the lock height
      stops at the ceiling and reports a typed failure, without a further step.

Post-merge design gate:

- [ ] Update design/40 with the measured enable, disable, enabled, locked,
      failure and timeout semantics, plus capture range, useful step and any
      timeouts the search observed — the parts of design/34 `:223`–`:234` this
      block actually collects.

## 7b. Autofocus must not fight an armed servo

Branch: `design34/continuous-focus-policy`

**Scoped down 2026-08-05.** The old block asked for a policy on every Z-writing
path, premised on the move disabling PFS. That premise is untested and no longer
blocking. What is live is narrower and certain: a software sweep under an armed
lock both fights the servo and duplicates what it already does.

- [ ] `run_autofocus` must not sweep the focus device while continuous focus is
      enabled. Refuse with a typed error naming the lock and the remedy; do not
      silently disable it.
- [ ] Cover the unattended paths, which is where this actually bites:
      `microclaw/autofocus.py:94`, `:103`, `:115` — sweep, move-to-best,
      `_restore`; `microclaw/hooks.py:313` — the focus-recovery jog;
      `microclaw/tools.py:2175` — per-position Z in the tile/grid path. All are
      already inside a `check_z`-guarded range; the gap is lock awareness.
- [ ] **Do not offer `preserve`.** Re-arming after a move is observably
      different from keeping the servo searching throughout it, and no evidence
      supports a movement path that preserves search. Offering it would be a
      claim the rig has not made.
- [ ] `move_stage_z` and `set_z` get an explicit, documented policy — but state
      it as a policy, not as a finding about the hardware. Whether the move
      itself disarms PFS is still unmeasured (design/40 §"Still owed").

Rig gate (Nikon):

- [ ] `run_autofocus` under an armed lock refuses, and the lock survives the
      refusal.
- [ ] With the lock disengaged, the same call runs normally.

Post-merge design gate:

- [ ] Record which Z-writing paths are lock-aware and what each does. Do not
      claim `preserve`. State plainly that probes 1–4 remain unrun.

## 8. Phase 5 continuous-focus addendum

Branch: `design33/phase5-continuous-focus`

- [ ] Now that a typed capability with rig-verified enable, lock, failure and
      timeout semantics exists, let Phase 5 declare it. **6a already lifted the
      blanket exclusion**; what is left here is generation, not permission.
- [ ] Require reviewed bounds for the search — a ceiling per objective and
      sample-holder combination. Never copy an observed engagement position:
      four locks in one day spanned 2450–2912 µm.
- [ ] Regenerate a Nikon profile through setup and start a session under it.
      Acceptance: it authorizes what the operator's hand-edited
      `safety_config.yml` authorizes, without hand editing.

Post-merge design gate:

- [ ] Record in design/33 what Phase 5 now emits for a continuous-focus rig and
      what remains unresolved.

---

# Track D — platform defects from the 2026-08-05 sessions (blocks 13, 41a–41c)

These surfaced on the Nikon and on M5, but none of them is Nikon or M5 work;
several break hooked surveys on every rig. Split out so Track B stays about
focus. **May run concurrently with Track B** in its own worktree — the overlap
with 6a is limited to `tools.py`, so sequence the two branches rather than
sharing a tree.

Blocks 41a–41c come from `design/41-smiley-session-findings.md`, the M5 smiley
run of 2026-08-05. **Assign 41a before anything else in this track**, including
block 13: it is what keeps a long session alive, and every other block's rig
gate is run inside such a session. Design/41's remaining two findings are folded
into block 13 rather than given blocks of their own, because they are the same
decisions block 13 already owns.

## 13. [x] Hooked-survey and diagnostic defects — **MERGED 2026-08-06**

Branch: `design40/platform-defects`

- [x] **A failed marked run poisons the position list.** `mark_positions=True`
      preflights the *entire* native list (`tools.py:2506`), so 25 entries left
      by a failed grid refused the next run with a conflict quoting the **old**
      grid's coordinates. The agent concluded `run_tile_acquisition` lays its
      grid out asymmetrically — it does not (`tools.py:2660`) — and spent a
      round trip on the wrong fix. Decide and state whether a partially-written
      grid is rolled back, and make the conflict distinguish pre-existing
      entries from the ones this call would add.
- [x] **`rank_hook_log` cannot read its own parent's log.** It requires
      `result.<metric>` on every entry (`tools.py:3757`–`3764`); the runner
      interleaves `hook_action` records, so ranking fails on entry 1 of every
      hooked survey. Skip non-observation entries.
- [x] **Hook preflight validates a contract the runner rejects.**
      `generate_and_save_hook` returns hard-coded "Static syntax and
      `image_process_fn` contract passed" (`tools.py:4162`) while
      `run_tile_acquisition` refuses that contract. Two review-and-save cycles
      with the operator, on hardware. One contract, checked in one place.
- [x] `ContinueSurvey` logs `"decision": "refused",
      "reason": "unsupported-by-this-runner"` on every tile of a batched run —
      25 refusals in a run where the action is a documented no-op. Either accept
      it as a no-op in that runner or stop the docs recommending it there.
- [x] **The SNR gate is a fluorescence assumption.** Brightfield fields with
      cells the operator could see read SNR 2.53 / 2.74 / 1.41 and gate the
      focus metric off; raising exposure made it *worse*, because in transmitted
      light the background is the signal path. Decide what the gate means for
      transmitted light — this may be a different metric, a different gate, or
      an honest refusal to score; it must not be a threshold tuned until
      brightfield passes.
- [x] **Saturation does not invalidate SNR, so a clipped gate passes anything**
      (design/41 F4). On the M5 smiley run every one of 18 frames had
      `max_intensity` 65535 in both channels, and the operator's explicit
      "keep the tile if SNR > 3" gate reported margins of 173–716 and 277–904.
      `saturated_fraction` was computed, logged, and used by nobody. `snr()`
      uses p99.5 rather than `max()` (`image_analysis.py:48`), so a clipped
      frame degrades into a plausible large number instead of an obvious error.
      Report SNR as **invalid above a saturation fraction**, the same way
      `focus_metric_valid` already gates the focus metric below `min_snr` — one
      existing pattern extended, not a new validator. **This bullet and the
      brightfield bullet above are one decision about what SNR validity means;
      settle them together and state the semantics once.**
- [x] **Exposure is approved once and never re-checked after a focus move**
      (design/41 F4). The operator approved exposure against an out-of-focus
      snap (`max_intensity` 6637, `saturated_fraction` 0.0); autofocus then
      moved 2 µm and every subsequent frame clipped. Surface clipping in the
      result the model reads from `snap_and_analyze` and the acquisition tools,
      so "exposure is good" can be re-asked when focus changes.
- [x] **`axis_selection` makes the caller guess an answer the dataset holds**
      (design/41 F5). `build_stage_coordinate_mosaic` took three calls to place
      9 tiles: the error names only the axis *this* call omitted
      (`tools.py:1279`), so fixing it reveals the next one. Both axes were
      length 1 — a timelapse with `n_frames=1` — so both failures had exactly
      one legal completion. Default singleton axes to their only value, and when
      something genuinely ambiguous remains, state the full non-position axis set
      with each axis's available values so one correction is always enough.
- [x] **`calibrate_stage_to_camera` mis-diagnoses fixed-pattern lock.** An exact
      `0.00 px` shift is the signature of a stationary vignette rim or sensor
      dirt dominating the correlation, not of too small a step
      (`tools.py:1684`). The advice sent the operator to a larger step, which
      failed the opposite way. Detect the zero-shift case and name it; a real
      80 µm move left the tracked centroid at (144.7, 558.5) unchanged to one
      decimal while the whole field visibly moved.

Rig gate:

- [x] Any rig: a hooked tile survey with `mark_positions=True` that fails
      mid-run leaves a state the next identical call can run from.
- [x] Any rig: `rank_hook_log` ranks the log its own hooked survey just wrote.
- [ ] Any rig with transmitted light: whatever the SNR decision is, a field the
      operator calls usable is reported consistently with that decision.
- [x] Any fluorescence rig: a deliberately over-exposed field is reported as a
      saturated, invalid SNR rather than a large one. The M5 smiley conditions
      reproduce this directly — beads at 100 ms after autofocus clipped every
      frame — so this is a re-run of a known-clipping field, not a new setup.
- [x] Any rig: a `n_frames=1` multi-position dataset mosaics in **one** call to
      `build_stage_coordinate_mosaic` with no `axis_selection` argument.

Post-merge design gate:

- [x] Record the hook-contract single-source decision in design/32 and the SNR
      decision in design/25. Update design/40's defect list with what shipped
      and what was deliberately left.
- [x] State the SNR validity semantics **once** — saturation and transmitted
      light resolved by the same rule — in design/25, and tick design/41 F4/F5.

### Rig gate — M5, 2026-08-06: **G1, G2, G4, G5 PASS; G3 not runnable here**

Evidence: `40-block13-m5` (history `20260806_100503_655555`, plus `survey_a1`,
`survey_a1_plus3`, `mosaic_poslist`). No written verdict was returned; the result
was scored from the history.

- **G1 PASS**, by a cleaner route than the runbook asked. `validate_positions`
  accepted `survey_r0_c0` and **rejected** `survey_r1_c0` on the XY guard; the
  list went 3 → 4 with only the accepted point written, the next
  `validate_positions` returned `rejected: []`, and the 4-position run completed.
  `position_list_rollback` appears **0 times** — correctly: check-before-mark
  meant nothing unsafe was ever written, so no rollback was needed. The rollback
  path itself is therefore still unexercised on a rig.
- **G2 PASS.** `ranked_entry_count 4`, `invalid_entry_count 0`, ranking its own
  hooked survey's log.
- **G3 not runnable.** M5 has no transmitted-light path. Carried forward.
- **G4 PASS, and it found a defect.** The exposure ramp 50→60→70→80 ms refused
  correctly at 80 ms (0.0135% saturated → `snr: null`, focus metric refused with
  it). But at 60 ms the frame clipped — `max_intensity 65535` — while
  `saturated_fraction` printed **0.0**, because the payload rounded to 4 places
  and the gate fires at 1e-4. On that 252×236 ROI the gate trips at 6 pixels out
  of 59472, so 1–5 clipped pixels were indistinguishable from none. Fixed in
  `f4e98c6` (6 places); the gate logic is unchanged and correct — the reported
  SNRs across the ramp (103.81 → 103.16) were consistent, not inflated. **That
  one commit landed after the gate and is itself ungated.**
- **G5 PASS.** One `build_stage_coordinate_mosaic` call, no `axis_selection`,
  result `selection: {"time": 0, "z": 0}`. design/41 F5's three calls became one.

Process note: the runbook's G1 recipe (refuse a whole oversized grid) was not
what the operator ran, and what they ran was better — a grid with one point
outside travel exercises the same boundary while still producing a usable
survey. Prefer that shape in future runbooks.

## 41a. A session must survive an API failure — **assign first in Track D**

Branch: `design41/session-survival`

Source: `design/41-smiley-session-findings.md` F2, F3, F7. No rig surface —
this is `agent.py` and its tests. It is first because every other block's gate
is run inside a session that currently ends on a transient HTTP error.

- [ ] **Only 529 is retried.** `_stream_one_round` catches
      `anthropic._exceptions.OverloadedError` and nothing else
      (`agent.py:307`). A `RateLimitError` (429), `InternalServerError` (500),
      `APIConnectionError`, or a read timeout escapes `run_agent_iter` into
      `webserve.py:591`'s blanket `except Exception`, which ends the turn. This
      is the most likely mechanism behind the operator's "I ran out of turns",
      which they could not otherwise account for — they had credits, and their
      longest turn used ~17 of the 50 allowed rounds. Retry 429 and 5xx on the
      same backoff, honouring `retry-after` when the response carries it.
- [ ] **The rollback is attached to one flavour of failure.**
      `del messages[start:]` runs only on the `_Overloaded` path
      (`agent.py:369`). Every other escape leaves the partial turn in
      `messages`. Put the rollback with the failure, not with 529.
- [ ] **`max_tokens` is reported as "unexpected" and ends the turn.**
      `agent.py:388` treats any `stop_reason` that is not `end_turn` or
      `tool_use` as an error. Truncation is an expected condition: name it, say
      the reply was cut, and let the operator continue without guessing what
      happened. Raise the 4096 cap (`agent.py:291`) — it is low for a model that
      must both narrate and act.
- [ ] **Truncation inside a `tool_use` block leaves an unanswered tool call.**
      `_unwind_cancel` exists precisely to prevent that (`agent.py:229`,
      design/16 §5) and the `max_tokens` path does not call it; the next prompt
      400s from a history that looks fine in the viewer. It did not bite on the
      smiley run only because the truncation landed in a text block.
- [ ] **Entry state is not the assistant's to clean up** (design/41 F7). 640 was
      enabled at 2.24% when the session opened and was left off. The prompt says
      "leave operator-established lasers as found unless asked"
      (`agent.py:103`) and the model read it as covering only lasers it enabled.
      Name the entry state: what microclaw turned on, microclaw turns off; what
      it found on, it leaves on. Prompt wording, not code.
- [ ] Tests: each retried status actually retries and then succeeds; each
      terminal failure leaves `messages` in a state the next prompt accepts.
      Assert the history is API-valid after every failure path, not just that an
      error was emitted — the defect this block fixes is invisible in the viewer.
- [ ] Stop if retrying makes a genuine auth or bad-request failure look
      transient. A 401 or 400 must still fail fast and say so.

Post-merge design gate:

- [ ] Record the retry and rollback contract in design/16 alongside §5's cancel
      unwinding, and tick design/41 F2/F3/F7.

## 41b. [x] Compile a session to a standalone pycro-manager script — **MERGED 2026-08-06**

Branch: `design41/script-export`

Source: `design/41-smiley-session-findings.md` F1. Depends on 41a merged — this
block's own deliverable is long model output, which is what 41a makes survivable.

"Everything must compile to a standalone pycro-manager script" is a stated
architectural principle in `CLAUDE.md` with no implementation. Asked for one on
2026-08-05, the assistant correctly reported that **no tool can write a `.py` to
a path it chooses**: `generate_and_save_hook` is hooks-dir-only, and the
acquisition tools write datasets and TIFFs. So it wrote the script from memory
into chat, and could not reproduce `compute_stats` or `run_autofocus` because it
cannot see their source. The result was not merely inexact — its `build_mosaic`
**re-imaged every kept tile**, where the session had built the mosaic offline
from the saved NDTiff at zero dose. The script the operator was told to copy and
run doubles the dose on a bleaching sample.

- [x] **Emit from the record; never reconstruct from memory.** Every tool call
      is already recorded append-only. Give each hardware and acquisition tool
      an emitter that renders *its own call* as pycro-manager source, next to
      the tool it emits — not in a registry.
- [x] **Inline analysis functions from source**, via `inspect.getsource` of the
      pure-numpy functions in `image_analysis.py`, so the emitted `snr()` *is*
      the `snr()` that ran. Inlining rather than importing is what keeps the
      script standalone; an import of `microclaw` fails the principle.
- [x] **A tool with no emitter emits a refusal, not a guess.** The script
      carries a literal `# NOT EMITTED: <tool>` line and fails loudly at that
      point. A plausible-looking fabrication of a step is the defect being
      fixed, not an acceptable fallback.
- [x] **Write through `guard.resolve_in_workspace`** — the existing path gate the
      mosaic already used, which makes "next to the file it made" legal without
      a new permission surface.
- [x] Scope: the emitted script reproduces the **hardware routine**. Nothing
      here puts microclaw runtime state into the script; that is the point of it.
- [x] Stop if the emitter needs a parallel description of what each tool does.
      Two descriptions of one tool drift, and the drift is unfalsifiable from
      inside microclaw. If a tool cannot emit itself, that is the finding.

Rig gate:

- [x] Any rig: export a session that moved the stage and acquired, then run the
      emitted script against a running MMStudio **with microclaw not running**.
      Its **hardware routine completes**, and the script stops only at a declared
      refusal. *(Reworded 2026-08-06. "It completes" cannot be met by any session
      containing an offline mosaic, because that refusal is architectural and
      approved: `build_stage_coordinate_mosaic` depends transitively on the
      package calibration module, so inlining it would not be standalone. Split
      across G1 and G3 of `design/41-block41b-rig-gate.md`.)*
- [x] Same rig: the script's dataset and the session's dataset agree on frame
      count and stage coordinates. Dose is compared explicitly — an export that
      re-images what the session read from disk fails this gate.
- [x] Any rig: a session using a tool with no emitter produces a script that
      stops at the `# NOT EMITTED` line rather than running past it.

Post-merge design gate:

- [x] `CLAUDE.md`'s compile-to-script principle gains a pointer to the
      implementation and to what is *not* emittable. Tick design/41 F1.

### Rig gate — M5, rounds 4 and 5, 2026-08-06: **G1, G2, G3 all PASS**

Five rig rounds in all. Rounds 1–3 each halted one step earlier than the defect
behind it, which is why the runbook now builds one session covering every
emitter path with the known refusal last.

- **G3 PASS** (`41-block4b-m5-run-round4`). Executed with microclaw closed; the
  script stopped loudly at `RuntimeError: NOT EMITTED: run_adaptive_survey`.
  Stronger than planned — it fired on a real unemittable tool rather than the
  staged mosaic. The same run confirmed **by-name position resolution on the
  rig**: after `clear_position_list` and three `mark_position` calls, the survey
  emitted its true coordinates `(339.7, 300.1, 42.987)` and siblings.
- **G1 + G2 PASS** (`41-block41b-m5-round5`). Fully emitted routine, no holes;
  the script ran to completion with microclaw closed, and the datasets are
  byte-identical to the session's at all three positions (149712 B each), one
  acquisition per position. Dose exact.
- Post-gate and therefore **ungated**: `b6cc7a2` (adaptive tools refuse with an
  architectural reason instead of "not implemented") and `5af0fc6` (below).

**The merge found a defect neither branch could see.** Block 13 added
`snr_validity()` to `image_analysis` and `compute_stats` began calling it; 41b
inlines a hand-listed set of helpers and did not know. Both branches were green
alone; merged, every exported script that snaps and analyses raised
`NameError: name 'snr_validity' is not defined` **at runtime, on the rig, with
no microclaw around to explain it**. Caught by running the suite on the merge
result before pushing. Fixed in `5af0fc6`, whose real content is
`test_emitted_analysis_defines_every_name_it_uses`: a structural guard that
every global the inlined analysis references is defined in the emitted source,
so the next helper added to `image_analysis` fails a test here rather than a
script on a microscope.

## 41c. [x] Channel plans on a rig with no `Channel` config group — **MERGED 2026-08-07**

Branch: `design41/emu-channel-plan`

Source: `design/41-smiley-session-findings.md` F6. Depends on 41b merged: raw
`set_device_property` calls are not emittable in any readable way, so this block
is also what makes channel switching exportable.

M5 has no `Channel` config group — `get_available_channels` returns `[]` — so
the channel-plan executor (design/33 Phase 4) had no presets to drive and the
two-channel run was done by hand: three raw property writes per switch, against
EMU's reversed slot order. The writes were correct; the assistant's narration of
them in the same message was not, calling slot 3 "slot 1". A reversed index map
being hand-applied to laser enables is one off-by-one from arming the wrong line.

- [x] **Build the channel plan from the EMU laser map when no `Channel` config
      group exists.** Same executor, same typed plan, sourced from the map
      instead of from presets. design/39 already reads EMU's `parameters` block,
      so the slots carry their configured names ("640", "561") — this is the
      missing *source* for a mechanism that exists, not a new layer.
- [x] Note for the implementer: the illumination confirmation fires on the
      enable and not on the disable. Check that a plan-driven switch does not
      lose a confirmation the hand-written sequence would have raised.
- [x] Stop if this becomes EMU-specific machinery in `microclaw/`. The plan
      source is pluggable or this belongs in a rig profile — see the standing
      constraint on rig facts.

Rig gate:

- [x] M5: a two-channel multi-position run switches channels through the plan,
      with no raw `set_device_property` **write to a laser enable** in the
      history. Filter, enable, and trigger end in the same state the
      hand-written sequence produced. *(Reworded 2026-08-06 and the rewording is
      accepted. "No raw `set_device_property` call" cannot be met alongside a
      laser-only channel: EMU's configuration carries no laser-to-filter
      association, so the emission filter is still moved by hand, and inventing
      that association is exactly what this block forbids. The narrowed criterion
      is the off-by-one the block exists to prevent.)*
- [x] Demo (has a real `Channel` group): non-regression — preset-sourced plans
      behave exactly as before.
- [x] M5: 41a's entry-state rule observed in the same run — a laser found on at
      session start is still on at the end, and one microclaw enabled is off.

Post-merge design gate:

- [x] Record the plan-source decision in design/33 Phase 4 and tick design/41 F6.

### Rig gate — M5 round 3 (2026-08-07) + demo rounds 1–2: **G1, G2, G3 PASS**

Four rig rounds in all, across two rigs. Evidence: `block41c-round3` (M5),
`41-block41c-m5`, `41-block41c-m5-round2`, `41-block41c-demo`,
`41-block41c-demo-round2`.

- **G1 PASS** (`block41c-round3`). `set_channel` calls
  `['640','640','561','640','DAPI']`; raw writes are **three filter-wheel State
  moves and nothing else** — zero raw writes to a laser enable. The checker was
  run by the coordinator, not the operator, and exits 0. Reversed slot order
  right: 640 writes `Laser 1`. Four illumination confirmations, all enables,
  none on the disables.
- **G3 PASS — first time in four sessions.** Entry state had slot 3 (`640`)
  enabled with the others off; step 8 restored `640` after the second
  acquisition; the session ends with `640` on and the `561` microclaw enabled
  off. Earlier sessions never reached step 8, which is why this was outstanding
  three rounds running.
- **G2 PASS** (`41-block41c-demo`, `-demo-round2`). Source names the `Channel`
  config group with no EMU mention, `channel_source: config-group`, both presets
  apply, and a channel-axis acquisition still runs where M5 refuses it. **The
  Float read-back half is SKIPPED, not passed** — see the carried-forward note.
- **Dose is exact, and this is the criterion the round exists for.** Six datasets
  per position = 2 from the session + 2 from the v1 script + 2 from the v2
  script, each script emitting exactly the acquisitions that completed. Both
  scripts ran standalone on M5 with microclaw closed and **neither raised**,
  despite carrying 1 and 5 `# SKIPPED` lines respectively.
- **The round-2 rollback fix is confirmed against a genuine recurrence.** The
  same iChrome serial timeout hit the plan's first write; the result was
  `ChannelPlanError` with `applied=[]` and **no "SAFE STATE NOT VERIFIED"**,
  where round 2 had escalated to `ChannelPlanSafeStateError` on a plan in which
  nothing landed.

**Carried forward, not owed by this block:**

- **The Float read-back is untested on hardware.** Neither rig reaches it by
  default: demo `Channel` presets expand to `Label` writes plus `Core.Shutter`,
  M5's enables are categorical. Adding a camera `Exposure` to one demo preset
  closes it.
- `_emit_multiposition` reads the channel from `protocol_params` only, so a
  top-level `channel` renders channel-less. Unreachable now that malformed calls
  are skipped; still latent; 41b's emitter, not 41c's.
- 41c's EMU-source refusal note in `authorize_channel` is **rig-unexercised**:
  `guard.check_channel` refuses an unknown name first on any rig with a non-null
  `channels.allowed`.
- Plans write every other named slot off **unconditionally** — three redundant
  writes per switch on M5, where both serial timeouts landed. Deliberate: a
  conditional skip would make the emitted script a function of that day's
  starting state. The better lever for a flaky link is retry-with-backoff at the
  device layer. See design/33 Phase 4.

## 41d. [x] `~` is written as a directory instead of being expanded — **MERGED 2026-08-06**

Branch: `design41/path-expansion`

Source: **block 41b's M5 rig gate, 2026-08-06** — not design/41. The operator
asked for `~/microclaw_data/multipos_3sites` and got
`C:\Users\ries\AppData\Local\microclaw\~\microclaw_data\multipos_3sites`:
a literal directory named `~` under the workspace root.

There is **no `expanduser` anywhere in `microclaw/`**. `~/x` is not absolute, so
`resolve_in_workspace` (`safety.py:1201`) joins it under the root as an ordinary
path segment. This predates Track D entirely — the code dates to `ee7f098`,
2026-07-28 — and it affects **every tool that takes a path**, not just the
acquisition tools that surfaced it. It is filed separately from 41b because the
fix is package-wide and belongs on its own branch with its own gate.

- [x] **Decide expand-or-refuse, and state it once.** The three options are
      expand `~` to the real home, refuse it, or keep literalising it. The last
      is what happens today and is the worst: it silently produces a directory
      the operator did not ask for, in a place they will not look. Expansion
      followed by the existing confinement check is the obvious answer — but
      *say* which, in one place, and let both resolvers use it.
- [x] **Refuse clearly when the expansion escapes the workspace.** With
      `workspace_dir` configured, expanding `~` will usually land outside it.
      That must be a refusal naming the configured root, not a silent rewrite
      and not a traceback.
- [x] **One place, not per tool.** Both `resolve_in_workspace` and
      `resolve_readable_path` normalise; every path-taking tool inherits it. If
      the fix needs touching individual tools, that is the wrong shape.
- [x] Tests both directions: `~/x` with no workspace root, `~/x` with a root it
      escapes, `~/x` with a root that contains it, and a path *containing* a
      literal `~` segment that is not a prefix (`a/~b/c` must not be mangled).
- [x] Stop if this turns into per-tool path handling, or if expansion has to
      differ between the read and write resolvers for any reason other than
      confinement.

Rig gate:

- [x] Any rig: a tool given `~/...` either writes under the real home directory
      or refuses naming the workspace root. **No directory named `~` is
      created anywhere.**
- [x] Any rig: re-run the acquisition from block 41b's gate with a `~` path and
      confirm the dataset lands where the operator expects.

Post-merge design gate:

- [x] Record the path-normalisation contract in design/32 and note that
      `resolve_readable_path` is deliberately unconfined but still normalised.
      *(Corrected 2026-08-06: this line said "beside the workspace-confinement
      section" and **design/32 has no such section**. Landed as a titled section,
      "The path-normalisation contract (block 41d)", in the style of block 13's
      "One hook contract, checked in one place".)*

### Rig gate — M5, 2026-08-06: **Step 0, G0, G1, G2, G3 all PASS**

Evidence: `gate41d-m5` (histories `20260806_131159_240275`, `20260806_131509_321433`,
`20260806_134326_665800`, plus the tilde inventories and the returned datasets).
Two rounds, because the first returned G1 and G2 only.

- **Step 0 PASS.** `WORKSPACE_ROOT: None` — M5 confines nothing, so G1 was
  predicted to write. `~/microclaw_data/gate41d` resolved to
  `C:\Users\ries\microclaw_data\gate41d`, and `a/~b/c.json` came back with `~b`
  intact.
- **G0 PASS on the second round, and this is the step that mattered.** On round
  one the operator had already deleted 41b's leftover `~`, so the before-scan
  was empty and the "no new `~`" comparison could not have failed. Round two ran
  the finder against a real `C:\Users\ries\AppData\Local\microclaw\~` and it
  listed it, so the before/after comparison is an instrument that has been shown
  to work rather than one that returns empty either way.
- **G1 PASS.** `run_timelapse(n_frames=1, interval_s=0,
  save_dir="~/microclaw_data/gate41d_g1")` → `C:\Users\ries\microclaw_data\
  gate41d_g1\timelapse_1`, non-empty on disk. Microclaw reported the **expanded**
  path back, not `~/...`.
- **G2 PASS, both directions.** With a throwaway `workspace_dir:
  C:\microclaw_gate41d`, the `~` path was refused with
  `Path '~/microclaw_data/gate41d_g2' (expanded to 'C:\Users\ries/microclaw_data/
  gate41d_g2') escapes the configured workspace directory (C:\microclaw_gate41d).`
  — root *and* expansion — while the inside-the-root write succeeded. The refused
  call carries no `trigger_preflight` where both successful calls do, which is
  positive evidence that it stopped before any hardware: `save_dir` is resolved
  on `run_timelapse`'s first line.
- **G3 PASS.** `run_multiposition_acquisition` over three marked positions with
  `save_dir="~/microclaw_data/gate41d_multipos"` → `3/3 positions completed`,
  per-position subdirectories built from the *resolved* root
  (`...\gate41d_multipos\gate41d_pos1\gate41d_pos1_1`), three stacks of 159412 B.
  The after-scan is byte-identical to the before-scan: **no new `~` anywhere.**

**One cosmetic residual, deliberately not fixed.** The refusal message shows the
expansion with mixed separators — `C:\Users\ries/microclaw_data/gate41d_g2` —
because `expanduser` replaces the `~` and leaves the rest of the operator's
string alone. The *resolved* path is normalised correctly by `realpath`
downstream, so this is display only. Showing the realpath'd target that was
actually compared against the root would read better. Carried forward, not owed.

**A residual of the gate itself:** the `~` directory used to validate G0 was
still on M5 when the evidence was returned. Whether it holds 41b's data or is an
empty validation stub is the operator's call, and the runbook deliberately
proposes rather than deletes.

---

# Track E — the read side (block 42)

`design/42-open-what-we-wrote.md`. Microclaw writes a file, hashes it, describes
it, and then tells the operator to go open it in FIJI. There is no read side, and
the last clause of *"put them in a stitched mosaic, then open the mosaic and show
it to me"* has never been executable.

Not a Track D block and nothing here depends on it, but two other documents now
point at it: **design/43 F7** (six offers of `export_dataset_as_tiff` in one
session, all with the wrong reason — the fix names `open_artifact` as the thing
the model should reach for instead) and **F12**. Both are now scheduled as
**Track F** below; F7 is block 43a's second half. Do not start them from these
rows.

**Track E may run concurrently with Track B.** 6a is at step 5 awaiting the
Nikon and touches `safety_config`/authorization; 42b touches `controller.py`,
`tools.py`, `image_analysis.py`, `tools_schema.py`, `agent.py`. Separate
worktrees, and 42b merges `main` before its own merge if 6a lands first.

## 42a. [x] The ImageJ open spike — evidence before the code — **MERGED 2026-08-07**

Branch: `design42/ij-open-spike`

Source: design/42 §"Spike first". The deliverable **is** the answers; the script
is how they are obtained. Six questions, three of which can change the design and
one of which (check 6) can stop it shipping in this shape at all.

design/42 rests on two corrections that are argued from the record, not measured:
that design/10's "static IJ1 methods are not directly callable over ZMQ" was the
design/12 cache collision misattributed, and that the design/18 / jPypeMM repaint
limitation is about the Preview canvas rather than about opening a new ImageJ
window. `_probe_imagej_dir` is a real existence proof for the first. Neither is
proof that `IJ.open` paints a window on this build. That is what this block buys.

- [x] **`design/42-ij-open-spike.py`, committed; its outputs are not.** One
      function per check, each independently runnable, each reporting
      PASS/FAIL/ERROR with the raw value it saw. A failing check must not stop
      the ones after it — the ordering questions in checks 1 and 2 are only
      answerable if every check runs.
- [x] **Import `_new_static_java_class` from `microclaw.controller`; do not copy
      it.** This is a deliberate departure from the design/29 probe convention
      (`pycromanager` + stdlib only), which exists for the Nikon operator who may
      have no working microclaw. This runs on M5. A copied helper would be a
      different code path that can pass while the shipped one fails, which is the
      one outcome this spike must not produce.
- [x] **Check 1** — `ij.IJ` and `ij.WindowManager` both resolve through the
      helper, **wrapped in both orders**. The design/12 collision must not
      resurface. Report the method surface each shadow actually exposes, not just
      that the wrap did not raise.
- [x] **Check 2** — does a shadow returned *before* another static wrap still
      work *after* it? This decides whether "re-wrap per call" is a rule or
      hygiene, and design/42 explicitly defers it here rather than designing
      around it.
- [x] **Check 3** — `IJ.open()` on `stitch_test_mosaic.tiff`: does a window
      appear, and do `WindowManager` dimensions match what Python reads from the
      same file? Dimensions are the load-bearing part; a title match alone is
      near-self-confirming.
- [x] **Check 4** — `IJ.open()` on a format IJ1 does not read natively. Confirm
      Bio-Formats delegation via `HandleExtraFileTypes` is real *on this
      install*, and record how it fails when it is absent.
- [x] **Check 5** — `IJ.open()` on the NDTiff **directory** `stitch_test_1`.
      Expected to fail; record *how*, so `open_artifact` can refuse with a reason
      instead of hanging. **This one has a consequence for 42b's schema**, which
      currently advertises NDTiff directories as openable while design/43 F7 says
      NDTiff opens in Fiji as-is. Whatever check 5 returns, 42b's tool
      description must match it.
- [x] **Check 6** — does the call block until the window paints, or return
      early? pyjavaz serialises every bridge round trip under one lock, so a
      modal Bio-Formats import dialog stalls **every** subsequent core call, on a
      rig, with a sample under illumination. **If it can hang, that is a blocker**
      and 42b does not start in this shape.
- [x] **Stop** and return to the coordinator if check 1 fails (the design/10
      correction is then wrong and design/42's premise goes with it), or if check
      6 shows the bridge can be stalled.
- [x] Rig-facing: PowerShell/cmd-safe invocation, `> out.txt 2>&1`, no Unix
      pipelines. Record the MM build and IJ version in the output header.

Rig gate — **the evidence is the deliverable**, so this block's gate is its run:

- [x] M5, with MM open and `stitch_test_mosaic.tiff` present, all six checks,
      output returned.

Post-merge design gate:

- [x] **Amend design/10 §2 and "Net conclusions" #2** — *only if* check 1
      confirms it. design/42 already states the amendment is owed; the spike is
      what licenses making it. If check 1 fails, amend design/42 instead.
- [x] Fold checks 2–6 into design/42: the re-wrap rule, Bio-Formats reality,
      how a directory refuses, and whether the call blocks. Where an answer
      contradicts the Decision section, **change the decision** — do not carry
      a stub the spike disproved.

### Rig gate — M5, 2026-08-07: **PASS. 6 PASS / 3 INFO / 1 SKIP, no FAIL.**

Evidence: `out42a.txt` under `Micro-Claw/` (UTF-16LE — PowerShell's `>` writes
UTF-16; read it with `encoding='utf-16'`). MMCore 12.5.0, ImageJ **1.53c**,
Python 3.12.13, Windows 11. Neither stop condition fired. Operator confirmed by
eye that two windows appeared: `stitch_test_mosaic.tiff` and
`stitch_test_mosaic-1.tiff`.

**What was established.**

- **Check 1 PASS in both orders, and 1c came back `COLLISION`.** This is the
  result that matters most: the control, with the eviction helper bypassed,
  reproduced design/12 exactly — `JavaClass("ij.WindowManager")` came back
  carrying **java.lang.System's 40 methods** and *missing its own* `getIDList`,
  `getImageCount`, `getImage`. So check 1 measured a real bug being suppressed,
  not an absent one, and **design/10's "static IJ1 methods are not directly
  callable over ZMQ" is now measurably wrong** rather than merely argued to be.
  The design gate below is licensed by this line and by nothing else.
- **Check 2 PASS.** A held `ij.IJ` shadow answered `getVersion` identically
  after `ij.WindowManager` was wrapped (evicting the shared key), and the mirror
  case held too. **"Re-wrap statics per call" is HYGIENE, not a correctness
  rule.** 42b should still re-wrap — it is what every other callsite does — but
  it is not obliged to.
- **Check 3 PASS.** `IJ.open` on the mosaic produced window id `4294967292`,
  title `stitch_test_mosaic.tiff`, **1004×1024 matching tifffile's page shape
  (1024, 1004)** exactly. The load-bearing dimension check passed, and the
  operator's eyes confirm the window painted.
- **Check 6 no stall.** `open_s` 0.018 s, `core_s` after the open 0.000 s,
  window visible to `WindowManager` with **no sleep at all**. The single pyjavaz
  lock was free immediately, so 42b's structural check may read once rather than
  poll. **The blocker condition did not fire.**

**Three findings that change block 42b.**

1. **`IJ.open` is NOT the same entry point as drag-and-drop, for directories.**
   Check 5: `IJ.open` on the NDTiff directory is a **silent no-op** — no
   exception, no window, and it **held the bridge for 6.94 s** doing nothing.
   But the operator then dragged the same `stitch_test_1` folder onto the
   ImageJ toolbar **and it opened**. design/42 §"The call" asserts that
   `ij.plugin.DragAndDrop` and `IJ.open` "both land in `ij.io.Opener.open`", and
   flagged it as a paragraph to confirm rather than trust. **The spike disproved
   it for the directory case**, which is the case that matters most: NDTiff
   datasets are the commonest thing microclaw writes, and design/43 F7 turns on
   "NDTiff opens directly in Fiji — never export just to look". 42b must find
   the directory-capable path (`DragAndDrop.openDirectory` / `FolderOpener` /
   whatever the drag actually dispatches to) or refuse directories explicitly;
   it must not call `IJ.open` on one and report the result.
2. **Bio-Formats delegation is unproven, and the probe is inconclusive** —
   not negative. `loci.formats.ImageReader` **resolved**; `loci.plugins.BF`,
   `loci.plugins.LociImporter` and **`HandleExtraFileTypes`** all came back
   "Class not found on any classloaders". That is weak evidence of absence:
   `HandleExtraFileTypes` lives in the *default package* and is loaded by IJ's
   own `PluginClassLoader`, which pyjavaz's `ZMQUtil.loadClass` may simply not
   search. Check 4 **SKIPped** — no `--foreign` file was supplied — so nothing
   was actually opened. **42b must not lean on Bio-Formats delegation**, and
   check 6's "does not stall" is correspondingly scoped: it says nothing about a
   format that raises a modal importer dialog, because none was tried.
3. **`IJ.redirectErrorMessages` is present** (as `redirect_error_messages`).
   That is 42b's lever for turning an IJ1 open failure into a Log entry instead
   of a modal dialog — which is what would otherwise hold the lock.

**Two cosmetic notes for the next rig run**, neither a defect: PowerShell's `>`
writes **UTF-16LE**, which made the evidence awkward to read (`Out-File
-Encoding utf8` avoids it); and `uv run` writes progress to stderr, which
PowerShell surfaces as a red `NativeCommandError` before the script even starts.
Both belong in the next runbook, not in the code.

## 42b. [x] `open_artifact` — open what we wrote, and stop there — **MERGED 2026-08-09**

Branch: `design42/open-artifact`

Depends on 42a's answers, **which are now in** — see 42a's gate record above and
design/42 §"What the spike measured". design/42 §"Decision" through §"Stubs" is
the spec, as amended by that section. The hard part of this block is not opening
the file; it is **not** rendering it.

**Read the three 42a findings before starting.** Two of them change the spec:

- [x] **The directory path is an open mechanism question and this block owns
      it.** `IJ.open` on an NDTiff directory is a silent no-op that holds the
      bridge for ~7 s, but dragging the same folder onto the ImageJ toolbar
      opens it. So design/42's "same entry point as drag-and-drop" is false for
      directories. Find what the drag actually dispatches to and use it, or
      refuse directories by name — **do not call `IJ.open` on a directory and
      report what comes back.** A silent 7-second no-op on the commonest
      artifact microclaw writes is the worst available outcome. This needs its
      own small spike addendum before the tool is written; the operator has
      already shown the drag works, so the question is only which Java entry
      point reproduces it.
      **Resolved by a third option this item did not contemplate (2026-08-09):**
      neither the drag's entry point nor a refusal. A dataset directory is
      resolved to the TIFF stack files inside it, each opened with `IJ.open` —
      an NDTiff dataset *is* TIFF files plus an index sidecar. `IJ.open` is
      never called on a directory, which is what this item actually forbids.
      The spike addendum ran (`0dd3629`) and its answer was that MM's dataset
      reader cannot open what microclaw writes (F1, F4).
- [x] **Do not lean on Bio-Formats delegation.** `HandleExtraFileTypes` did not
      resolve over the bridge, and nothing non-native was ever opened (check 4
      SKIPped). Treat non-native formats as unproven: open, check structurally,
      and report honestly if no window appeared.
- [x] **Use `IJ.redirectErrorMessages`** so an IJ1 open failure goes to the Log
      window rather than a modal dialog. Check 6 found no stall on a TIFF, but
      the modal case was never exercised, and a dialog is what would hold the
      single pyjavaz lock.
- [x] **The structural check may read once.** The window was visible to
      `WindowManager` with no sleep (check 6), so no polling loop is needed —
      but keep the "no new window" branch, which is check 5's refusal signal.
- [x] Re-wrapping statics per call is **hygiene, not a rule** (check 2). Keep
      doing it for consistency with every other callsite; do not build anything
      that depends on it being required.

- [x] **`controller.open_in_imagej(path)`**, beside `_probe_imagej_dir`, which is
      the existence proof for the mechanism. Statics per 42a check 2's answer.
- [x] **Structural proof the window exists**: window-ID set difference across the
      call, plus dimensions matched against what Python reads. `IJ.open` returns
      void; a bridge call returning is not proof a window painted (design/18's
      lesson survives even though its Preview specifics do not). No new window →
      report the failure. **Never report a window the user cannot see.**
- [x] **`IJ.open(path)` as the primary, not `IJ.runMacro`.** Same entry point as
      drag-and-drop, no macro engine, and — the reason that matters on this rig —
      **no string escaping**: a Windows path goes through as an argument instead
      of through backslash-escaping into a Java string inside a macro inside JSON.
- [x] **The user owns the session.** New window, left open. Never reuse, never
      close, never `WindowManager.setTempCurrentImage`, nothing on any exit path.
- [x] **Do not build a bridge-locality detector.** design/42 asks for a refusal
      when the bridge is not local, because `IJ.open` resolves the path Java-side.
      Microclaw's bridge is localhost-only by construction — `Core(port=…)` and
      `Studio(port=…)` take no host (`controller.py:214`) — so there is nothing to
      detect and a detector would be a layer over a fact. State the fact in the
      docstring and keep the Python-side existence check. If a check is wanted
      anyway, the zero-new-code one is that `_probe_imagej_dir()`'s Java-side path
      exists Python-side; decide and say which, do not add both.
- [x] **`analyze` is off by default, and the schema description is what holds
      it.** The 512 px thumbnail for this mosaic is 32,496 base64 characters
      measured on the real file, and an image block stays in the conversation for
      every subsequent turn. "Show me" and "tell me what's in it" are different
      requests.
- [x] **`_verify_against_manifest`** recomputes both digests the writer recorded
      (`tools.py:2114`, `:2139`) and reports match/mismatch. A mismatch **still
      opens** — the operator is entitled to look at a file whose provenance
      failed. No sidecar → open, and say provenance is unverified.
- [x] **Fold the duplicated text+image block into `image_content`.**
      `snap_and_analyze` (`tools.py:2344`) and `run_autofocus` (`tools.py:2761`)
      hand-build the same pair; `open_artifact` would be the third. One
      definition, three callers — not a fourth copy.
- [x] **`make_thumbnail` gains `mask`**, one line: percentiles read
      `img if mask is None else img[mask]`. The mosaic is 65% uncovered zeros and
      the 2/99.8 stretch over all pixels puts the real signal in the bottom ~1% of
      the ramp, so the analyze path would otherwise be interpreting a black
      rectangle. Say in the payload that the stretch was masked.
- [x] **`@emits_nothing`**, as with `read_hook_log`: a display step has no place
      in a re-run script.
- [x] **`agent.py`**: call it and stop; do not tell the operator to open it in
      FIJI; `analyze=true` only when asked to interpret; never describe an image
      not opened.
- [x] Stop if this grows a registry of file types, a viewer abstraction, or a
      second path into the MM Preview canvas — the last is what design/18 and
      jPypeMM actually rule out and is out of scope by name.

Suite, on committed fixtures:

- [x] The default call returns a dict, never a content list, and reaches no
      thumbnail code — assert with a patched `make_thumbnail` that fails if
      called.
- [x] A tampered TIFF reports `pixel_sha256_matches: false` **and still opens**.
- [x] A file with no sidecar opens with provenance stated as unverified.
- [x] `open_in_imagej` with no bridge returns `opened: false` rather than raising.
- [x] `image_content` is the only place the text+image pair is built.
- [x] Diff collected test IDs against the branch start commit.

Rig gate — **run on the demo machine, 2026-08-09** (read-side block; see the
runbook for why, and the ledger for what M5 is still owed). A behaviour gate as
much as a mechanism gate:

- [x] Re-run the failing session verbatim: acquire the six positions, build the
      mosaic, *"open the mosaic and show it to me."* Success is a **new ImageJ
      window the operator can see**, reported with matching dimensions and both
      digests confirmed, and no instruction to open anything in FIJI.
      **Deliberately not re-run verbatim.** The demo machine's three pixel-size
      configs all carry the same identity affine (design/29), so a mosaic built
      there is misleading evidence, not weaker evidence. G1 opened the **real M5
      mosaic** instead and the success criteria above were met in full —
      1004x1024 dimensions matched, both digests confirmed, no FIJI instruction.
      See the runbook's §"Why this does not acquire and build a mosaic here".
- [x] **The transcript contains no thumbnail.** That phrasing is a show-me, not
      an analyze-me. A rendered image there is a **failed gate even though the
      window opened**.
- [x] Then *"how many cells are in it?"* — `analyze=true` appears only on that
      second call.
- [x] Close the window by hand: microclaw neither reopens it nor complains.
- [x] Run a second acquisition afterwards to confirm the bridge still works with
      an ImageJ window open. (42a check 6 is the design-time version of this
      question; this is the one that counts.)

Post-merge design gate:

- [x] Record in design/42 what the gate measured, and tick design/43 F7's
      dependency — F7's prompt fix names `open_artifact`, so it becomes
      assignable here. Do not implement F7 in this block. **Done 2026-08-09:**
      design/42 §"What the gate measured"; design/43 F7 carries a
      "dependency cleared" note and is assignable. F7 not implemented here.

---

# Track F — the Nestor session (design/43)

`design/43-nestor-session-findings.md`. Fifteen findings from one 50-minute M5
session on 2026-08-06 that **worked** — nine 488 timelapses at nine verified
tiles, nothing faked, the one wrong turn recovered as soon as the operator said
so. Every finding is a place the session was harder than the science, and the
evidence for all fifteen is one history JSONL plus one confirmations JSONL.

Scheduled 2026-08-09. It goes ahead of Track C because block 9 cannot start
without operator intake, and it runs alongside Track B because 6a is blocked on a
remote operator.

**Block order is design/43 §"Suggested order", one block per numbered item.**
Three items pair two findings, and they are kept paired because one gate session
observes both — not because they touch the same files. Where a later block
depends on an earlier one, design/43 says so and the ledger's "Depends on" cell
repeats it.

Two of the fifteen change what Microclaw *is* rather than how well it behaves:
**F14** (adaptive runs must be emittable) and **F15** (offline analysis ships
with no analyses in it). Everything before them is friction removal. The order is
deliberate on one point in particular — **F14 lands before F5**, so that
refocus-and-re-judge is built inside a runner that already exports rather than
widening a non-emittable surface.

**Track F may run concurrently with Track B.** 6a is at step 5 awaiting the
Nikon and touches `safety_config`/authorization; 43a touches `agent.py` and
`tools.py`. Separate worktrees, and whichever lands second merges `main` first.

**Do not tailor any of this to M5.** The session was on M5 and the findings are
stated in its terms — a cropped ROI, TTL-shuttered lasers, a reversed EMU slot
order. Every one of them must land as a generic capability with the rig fact in a
profile, a gate doc, or the operator's own knowledge base. F1 exists precisely
because there is nowhere to put a rig fact today.

## 43a. [x] Live view is a dose, and the TIFF export is offered for the wrong reason — **MERGED 2026-08-09**

Branch: `design43/live-dose-and-tiff-prose`

Source: design/43 F3 and F7. Paired because both are prompt-and-payload text that
stops active harm, and because one rig session observes both: run an acquisition
and watch what the camera does when it ends, then ask to look at what was
written.

**F3 — never leave live running after an acquisition.**

- [x] `_pause_live` (`tools.py:834`) gains `restore: bool = True`. The default is
      unchanged, and that default is the case it was written for: an interactive
      snap borrows the camera from a live session the operator started and gives
      it back. Acquisition entry points pass `restore=False`.
- [x] **Which callsites are acquisition entry points is a judgement to make in
      the code, not a list to copy from the design doc.** Measured on `e6afb06`:
      nine callsites (`tools.py:2302, 2467, 2557, 2718, 2748, 3153, 3329, 3381,
      5872`) across `snap_and_analyze`, `calibrate_stage_to_camera`,
      `find_features`, `run_autofocus`, `_run_protocol_at`,
      `run_multiposition_acquisition` and `snap_to_album`. The frame-producing
      runs are the ones that must not restore; re-derive the list rather than
      trusting this one.
- [x] `_live_restore_report` (`tools.py:873`) reports the left-off case instead
      of staying silent, so the agent tells the operator rather than leaving them
      to notice a dark canvas. Stub in design/43 F3.
- [x] `SYSTEM_PROMPT` (`agent.py:76`) — replace *"When it does not interfere with
      your acquisition, put the camera in live mode so the user can see what you
      are doing"* with design/43 F3's replacement paragraph. **That sentence is
      the cause**; the code change alone leaves the model starting live view on
      its own initiative, which is what it did at `[11]` and `[51]`.
- [x] **Do not build the rig-profile condition here.** F3's replacement text
      names `camera_triggers_lasers: true`, and F1 (block 43f) is what creates
      that key. The prompt may refer to it; no code in this block may read it, and
      nothing here may write a `rig` category. A key that only one half of the
      system knows about is worse than a sentence the model reads and finds
      unanswered.

**F7 — stop offering the export, and stop giving the wrong reason.**

- [x] `agent.py:85` and `:149–150` both name `export_dataset_as_tiff` with no
      statement of when it is *not* needed, which is why it was offered six times
      in one session (`[121] [123] [127] [145] [221] [261]`). Replace with
      design/43 F7's text: NDTiff opens in Fiji as-is; the export is for software
      that requires a single file; when the operator wants to **see** what was
      written, `open_artifact` is the answer.
- [x] Carry 42b's measured caveat into the wording: opening a dataset's TIFF
      stack files does **not** reconstruct multi-channel axis structure (channels
      arrive as planes, not named channels) — a known, tabled limitation in
      design/42. Wording that oversells `open_artifact` for a multi-channel
      dataset trades one wrong default for another.
- [x] Keep the export line in the SMLM section with its reason attached
      ("external localization software requires a single-file TIFF"), so the rule
      generalises instead of reading as a habit.

### Rig gate — M5

The runbook lives on the branch. Both halves want a rig where the camera trigger
fires the lasers, which is M5 (`Mode1 = "4 - Follow"`, established in this
session's own trigger pre-flights). The gate criteria owe the standing
known-bad/known-good validation before the runbook ships, and **the known-bad
already exists**: the Nestor session history under `Micro-Claw/nestor-06082026/`.

- [x] G1 — an acquisition ends and the camera is **not** running a sequence
      afterwards. Read `core.is_sequence_running()`, never MM Studio's live-mode
      flag; the flag is not the sequence, which is why `_pause_live` already
      verifies restores that way.
- [x] G2 — the payload states live was left off and why, and the agent repeats it
      to the operator rather than leaving a silent change of state.
- [x] G3 — **the open question this block is what answers.** design/43 F3 records
      it as untested: does leaving live off strand MM's Preview window as an
      open-but-frozen canvas that the operator finds disagreeable? If it is ugly
      the answer is still "off", reported more loudly — but the observation is
      owed, and it is the only thing here that cannot be settled off-rig.
- [x] G4 — ask to look at a dataset that was just written. `open_artifact` is
      offered; `export_dataset_as_tiff` is not. Validate the criterion against the
      Nestor history first: it must **fail** on those six offers.
- [x] G5 — the model does not start live view unprompted across a whole session,
      and when asked to start it, says what it costs on a TTL-shuttered rig.

### Rig gate — M2, 2026-08-09: **PASS. Block 43a is complete.**

Evidence: `Micro-Claw/43a-m2/` — session history, suite output, and the four
tile datasets.

**Gated on M2, not M5, and that is a full gate rather than a substitute.** M2's
lasers follow the camera trigger the same way M5's do, so the dose premise the
whole block rests on holds there. The operator ran it on M2 deliberately, to keep
the work off a single rig — which is this file's standing rule and worth
repeating: M5 is the unusual one, and a block gated only there is a block gated
on an outlier.

| gate | result | evidence |
|---|---|---|
| G1 acquisition leaves live off | **PASS** | Session opened with `get_system_state` reporting `live_view: true`. `run_tile_acquisition` returned `live_view_restore: {"requested": false, "left_off": true, "reason": "…Restart it with start_live_view if you want it back."}` |
| G2 agent says so | **PASS** | Unprompted, in prose: *"**Live view was left off.** It was running when the acquisition started, and the tool stopped it so the camera is no longer exposing the sample."* |
| G3 Preview appearance | **ANSWERED — no fix owed** | Open, displaying the last frame acquired before live stopped, and **not** displaying as live. design/43 F3's untested question is closed: the report does not need to be louder. |
| G4 open, not export | **PASS** | *"let me look at what you just acquired"* → four `open_artifact` calls, four windows, `dimensions_match: true`, no export offered in the call or the prose. |
| G4 multi-channel caveat | **N/A** | Single-channel, single-plane datasets. Still unexercised anywhere — carried forward. |
| G4 ThunderSTORM limb | **PASS** | *"ThunderSTORM needs single-file TIFFs, so I'll export each of the four datasets"* — offered with its reason and executed. The fix did not break the legitimate export. |
| G5 no unprompted live | **PASS (first half)** | No `start_live_view` call anywhere in the session. The second half — *says what live costs before starting one* — was never exercised, because the operator never asked it to start live. Carried forward. |
| Step 2 `left_off` count | **1** | ≥1 as required; the new path fired. |
| Step 2 export-offer count | **2, both in the ThunderSTORM turn** | Messages `[13]` and `[15]`. Zero before it. |

**The suite was red, and none of it was this block's.** M2 measured 9 failed /
1639 passed / 116 skipped / **1764 collected** — the collected total matches
macOS exactly, so nothing was lost. All nine are in code 43a never touched, they
have two distinct causes, and both are latent defects that **no rig had ever
been in a position to find** — no full suite has run on Windows since 41b and
41c landed on 2026-08-06. They are block 43m below.

## 43m. The suite is red on every Windows rig, and nothing noticed

Branch: `design43/windows-suite-integrity`

**Not a design/43 finding.** Found by 43a's M2 gate, numbered outside the a–k
range so it cannot be mistaken for one, and placed here because it gates every
remaining Track F rig gate: a red suite at Step 0 is supposed to mean something,
and right now it means nine failures an operator has to be told to ignore.

- [ ] **Eight failures: `tests/test_session_script_export.py` reads the emitted
      script with `Path.read_text()` and no encoding** (`:51`, `:75`, `:84`,
      `:151`). On a Windows locale that decodes UTF-8 as the code page, so every
      em dash comes back as `ΓÇö` and every `inspect.getsource(...) in source`
      assertion fails.
      **The product is fine and this must be verified before anything is
      changed:** `export_session_script` writes with
      `Path(path).write_text(source, encoding="utf-8")` (`tools.py:653`), which
      is why 41b's rig gate could execute an emitted script successfully. The
      defect is the test's read, not the exporter's write. Fix the reads; do not
      "fix" the writer.
- [ ] **One failure: `test_channel_less_rig_says_so_instead_of_a_bare_empty_list`
      consults the host's real EMU configuration.** It mocks
      `get_available_configs` to `[]` and then asserts on `result["source"]`, but
      `get_available_channels` falls through to EMU, which on an EMU rig is
      present — so M2 got the EMU message instead of "offers no channels". A unit
      test that reads the operator's machine passes on a laptop and fails on the
      hardware it describes.
- [ ] **Sweep for the same two shapes rather than fixing these nine.** Both are
      classes of defect, not incidents: an encoding-naive `read_text`/`open` in a
      test, and a test that reaches host state. Grep for both across the suite.
- [ ] **Gate: a full suite run on an EMU Windows rig, green.** That is the whole
      acceptance criterion, and it cannot be met off-rig — which is precisely how
      these survived two merges.

## 43b. The GUI stops tracking after a channel switch

Branch: `design43/refresh-gui`

Source: design/43 F4. One helper on the controller and its callsites; a debounce
timer is explicitly refused, and the pyjavaz single-lock reason is in the
finding.

- [x] **Verify the method name before writing the helper. ANSWERED off-rig,
      2026-08-09 — this block no longer owes a rig round trip before it can
      start.** `javap -classpath MMJ_.jar org.micromanager.Application` on the
      local Micro-Manager 2.0.3-20260625 install reports **both**:

      ```
      public abstract void refreshGUI();
      public abstract void refreshGUIFromCache();
      ```

      So design/43 F4's `refreshGUIFromCache()` exists and its from-cache
      rationale stands; the "if only `refreshGUI()` is available" fallback is
      not needed. Two honest limits on that evidence: it is the **local macOS
      2.0.3** build rather than the deployed Windows one, and `javap` proves the
      Java method exists, not that pyjavaz shadows it as
      `refresh_gui_from_cache`. Both are near-certain — `Application` is public
      MM API, and `ctrl.studio.app().refresh_gui()` already works today at
      `tools.py:1103` and `:5383`, which is the same interface shadowing the
      same way — so what is left is one live one-liner, folded into this block's
      own gate rather than owed before it.
- [ ] `controller.refresh_gui()`, never raising. Migrate the two existing
      `ctrl.studio.app().refresh_gui()` callsites (`tools.py:1103`, `:5383`) so
      there is one definition, and add it to `execute_channel_plan`
      (`authorization.py`) **including the rollback path** and to
      `set_focus_lock`.
- [ ] Rig gate on M5: a channel switch and a rollback both leave the Property
      Browser showing what the hardware actually holds.

## 43c. One illumination approval per session, not one per switch

Branch: `design43/session-grants`

Source: design/43 F2. 17 confirmations in 50 minutes, all approved, all for the
same two properties.

- [ ] The grant lives at the `CONFIRM_FN` seam (`tools.py:545–564`), which both
      frontends already share. Process memory only, never persisted, revocable.
- [ ] `GRANTABLE` is `illumination` and `acquisition` only. `knowledge` and `hook`
      confirmations are **not** grantable — they gate self-modification, not
      workflow friction.
- [ ] Limits and refusals are untouched: `max_power_percent`,
      `max_power_step_factor`, the authorization map, XY/Z bounds, exposure caps
      and the acquisition ledger all still apply. A grant answers the question the
      guard asks a human; it does not remove a guard.
- [ ] Every auto-approval still writes its own row to the confirmations JSONL,
      marked as granted. **A silent audit log is a worse bug than the nagging**,
      and this is the gate's headline criterion.
- [ ] Browser UI: a third button on the banner, and an active grant shown as a
      persistent chip with a Revoke control. Visible-and-revocable is what makes
      this shippable; a hidden grant is not.
- [ ] The operator asked for a blanket "deactivate session safety guards" button.
      This is deliberately narrower and must stay so. If the grantable set is too
      small, widen the set — never widen what a grant means.

## 43d. Report shapes and hints that sent the reader to the wrong place

Branch: `design43/report-shapes`

Source: design/43 F8, F10, F11. Grouped because all three are payload text with
no behaviour change, and each one produced a wrong statement in the session.

- [ ] F8 — `stopped_early` describes the hook's control decisions, not what was
      found, and it was read as "nothing was found" over a tile that scored
      `would_keep: true`. Add the hint, and add `hook_actions` counts, which the
      parent already has.
- [ ] F10 — a missing adapter name was reported as a possible hardware fault.
      `run_analysis_on_saved_dataset` refuses by name and lists what exists;
      `hint_for_error` maps `KeyError` to a lookup hint, not `_HARDWARE_HINT`.
      `errors.py:53` already says a hint that names the wrong subsystem is worse
      than none.
- [ ] F11 — report `grid_center_source: current_stage_position | explicit`, and
      add the position-list prompt rule: a request that refers to a previous
      scan's area must pass that scan's centre, because the default centre is
      wherever the stage happens to be.
- [ ] No new arguments in any of the three.

## 43e. Offline analysis ships with no analyses in it

Branch: `design43/builtin-offline-adapters`

Source: design/43 F15. The mosaic path is plumbed to the analysis boundary
(`completed_dataset.py:337–352`) and the last step is missing. Retires F10's
error text and half of F12.

- [ ] Built-ins resolve **before** the saved manifest, implemented over
      `image_analysis` so there is one definition of every measurement.
- [ ] `connected_components` and `frame_statistics` to start — the two this
      session asked for and could not get.
- [ ] Nothing about design/26's untrusted-adapter contract changes: saved
      adapters keep the manifest, the hash pin, the lint and the confirmation.
- [ ] **Where the line goes:** a built-in is a *measurement*, never a biological
      judgement. The moment an adapter needs a concept from the user's biology it
      belongs in the reviewed, hash-pinned path.
- [ ] Answer the question design/43 F15 closes on: whether `find_features` and its
      offline twin should be the same function with two callers. The live/offline
      split currently duplicates the question and not the code.

## 43f. A cropped ROI is a rig fact, and Microclaw has nowhere to keep it

Branch: `design43/rig-profile`

Source: design/43 F1. One knowledge category, one conditional prompt block, and
the fact reaching its point of use. Ships the key 43a's prompt text refers to.

- [ ] `CATEGORIES` gains `rig`, rendered first. Detection is the absence of the
      profile, not a flag file.
- [ ] The interview is a conversation, not a wizard: read the rig first, bring a
      filled-in draft, ask only about the gaps, never block a task on it, never
      re-ask a stored topic.
- [ ] The stored fact must reach the point of use (`get_roi` returning
      `illuminated_field`), not only the preamble.
- [ ] This is the block that makes 43a's `camera_triggers_lasers` sentence live.

## 43g. One bright corner beat the whole field

Branch: `design43/coverage-statistics`

Source: design/43 F6. Extent alongside intensity, in the one place statistics are
defined, so every observation record and `rank_hook_log` gets it for free.

- [ ] `signal_coverage`, `structure_coverage`, `signal_concentration` on
      `ImageStats`. `snr` is a tail statistic and stays what it is.
- [ ] Survey ranking prefers coverage; state it in the `rank_hook_log` schema
      rather than leaving the model to invent a composite in prose.
- [ ] `find_features` names its own scope in its payload: a puncta detector scores
      an extended or filamentous field low, which is what happened at `[205]`.
- [ ] **Needs rig calibration before anything ranks on it** — beads, the diffuse
      field design/36 still owes, and this session's saved 561 tiles, which are a
      free labelled set with `ridge_coverage`/`snr` already logged.

## 43h. Adaptive runs must be emittable

Branch: `design43/emit-adaptive-runs`

Source: design/43 F14. The largest item here and the highest-value one: it is
what turns a session into something the operator keeps. Independent of F5.

- [ ] Emit the **program** — seed plan, hook source, decision loop — never the
      trace. The refusal correctly rejects the trace and then generalises it into
      a rule about all of them; the operator who wants the trace already has
      `validate_positions` + `run_multiposition_acquisition`.
- [ ] `_runner_source()` follows `_analysis_source()`: `inspect.getsource` over
      the real loop, **never a re-write in the emitter**. `_survey_event_stream`
      has design/24 and design/27 written into it and a hand-copied copy that
      drifts reintroduces ghost exposures silently.
- [ ] **Extend the free-name test to the adaptive path before writing the
      emitter, not after.** `test_emitted_inline_defines_every_name_it_uses`
      exists because two green branches broke together on a rig; the adaptive
      emitter has strictly more surface for the same failure.
- [ ] The guard's bounds emit as literals with the check kept. This does not
      widen the exporter's existing accepted position — an exported script is the
      operator's own, run under their supervision — and the header says so.
- [ ] The refusal gets **narrower, not deleted**: `CannotEmit` stays for an
      unrecoverable hook source, an unresolvable named position, and a capability
      the script has no equivalent for.
- [ ] **Post-merge design gate: `CLAUDE.md`'s export paragraph must be amended**
      when this lands — its list of three non-emittable things becomes two. Do not
      edit it before the code changes; today the paragraph is accurate.

## 43i. "When you see a tile with higher signal, use it to focus"

Branch: `design43/survey-refocus`

Source: design/43 F5. Depends on 43g for the measurement that makes the request
answerable, and lands after 43h so it is built inside a runner that exports.

- [ ] `RequestAutofocus` becomes supported in `run_adaptive_survey` under an
      authorized budget — a third capability of the same kind as
      `illumination_envelope` and `artifact_limits`, not a new mechanism.
- [ ] Absent budget → refused exactly as today. One refocus per tile.
- [ ] The refocused tile is re-queued and the hook judges it again with
      `microclaw_refocused` in its metadata.
- [ ] A non-converging sweep is recorded and carried past; Z is not widened and
      retried.

## 43j. Hook usability and timelapse observation

Branch: `design43/hooks-and-timelapse-observation`

Source: design/43 F9 and F12. Paired as design/43 pairs them — "whenever their
files are next open".

- [ ] F9 — `list_hooks` marks unusable saved hooks inline, and `resolve_refusal`
      carries the remedy as a call rather than as prose. Two hooks were dead for
      the whole session and the offer made was to write a third.
- [ ] F12 — `run_timelapse` gains the `hook_strategy` / `hook_params` /
      `log_path` trio the other acquisition entry points already have. It is the
      shape callers already know, and `snr_observer` over 150 frames is the "did
      anything happen" answer at zero extra exposure.
- [ ] Check what 43e already retired before starting: `frame_statistics` over a
      saved dataset answers half of F12.

## 43k. Search in one channel, acquire in another

Branch: — **design first, no branch yet**

Source: design/43 F13. Explicitly *not a fix yet*. The composite the session
asked for in one sentence at `[90]`, which cost nine hand-driven sequences of
twelve calls each.

- [ ] **Do not fold this into 43i.** F5 supplies one half; the other half is a
      second acquisition in a different channel at tiles the hook selected, and
      that is a genuine new capability.
- [ ] Design it after 43i and 43h have run on a rig, on the evidence of how they
      behave. What makes it worth building at all is that 43h makes it portable:
      the same composite that saves this operator nine sequences once is, as a
      script, what they run on every coverslip for a year.

---

# Track C — the deferred feature blocks (9–11) and closeout (12)

Carried from the previous checklist substantially unchanged; the item text there
was reviewed and is still correct. Summarised here with its gates intact — read
the old file's §11, §12, §13 for the full item lists before starting each.

## 9. Design/26 Run B — generated adapter for a real existing analysis

Branch: `design26/generated-adapter-run-b`

- [ ] **Do not create the branch** until an operator supplies the real workflow, a
      known input/result, target meaning, and the desired initial
      observation-only action. This is what has kept the block deferred; the
      usability track is what makes an operator able to run one.
- [ ] Follow `design/26-field-spike-prompts.md` Run B and the three-question
      intake. Investigate installed files, environment, help, source, and primary
      docs; reproduce the result on copied input; separately measure startup,
      marginal, and batch latency.
- [ ] If software, model, or project is missing, **stop** and produce a pinned
      installation plan for explicit authorization. Installation is not adapter
      generation.
- [ ] Select the execution strategy from evidence. Never accept unmeasured
      per-tile environment, JVM, application, or model startup.
- [ ] Create the branch only after the execution contract is reviewed.
- [ ] Observation-only adapter, fixture-tested; unresolved axes, units,
      coordinates, or semantics stay `unverified`. Show source/lint and wait for
      explicit save approval.
- [ ] Run on stored data through block 10 of the old checklist (the
      completed-dataset runner, merged `a7a1e1c`) before any rig survey; run the
      fixed rig survey only after acquisition confirmation.
- [ ] Object ranking/revisit only for verified attributed boxes, masks, or
      centroids; otherwise whole fields or "unresolved."
- [ ] Verify replay without network or adjudicator. Stop on output mismatch, lost
      attribution, cleanup leaks, timing-budget failure, or hardware-capability
      access.
- [ ] Commit only generally reusable framework and adapter code plus fixtures.
      Lab-specific paths, models, thresholds, and projects stay as pinned
      external or custom-hook artifacts.

Post-merge design gate:

- [ ] Mandatory: add the actual execution boundary, contract, timings, failures,
      artifact identities, scientific limits, and Run B verdict to
      `design/26-implementation.md`. Re-plan Run C from those findings.

## 10. Design/26 Run C — optional few-shot biological classifier

Branch: `design26/few-shot-run-c`

- [ ] Skip and mark "not needed" if Run B already supplies a useful classifier, or
      if no real target requires learning from reviewed examples. **This block is
      conditional by design.**
- [ ] Before branching: run and retain `design/26-roi-detection-spike.py` as a
      software regression — this also discharges the Block 0 straggler carried
      below — then obtain a blind real survey and reviewed positive and
      candidate-negative crops. Do not label unreviewed crops negative.
- [ ] Branch only after sample, target, held-out split, and acquisition budget are
      defined.
- [ ] Measured classical/enriched descriptor plus logistic probe first. Another
      backend only after a held-out benchmark at the chosen budget shows a reason.
- [ ] Persist `verdicts.json`, a deterministic scorer artifact, and a
      provenance-rich `ranked_positions.json`; preserve proposal bounds,
      centroids, and source-tile hashes.
- [ ] Validate non-square and multi-object attribution, affine conversion, guard
      rejection, replay, contaminated/duplicate/wrong verdicts, constant features,
      common and absent targets, and explicit top-k selection.
- [ ] One-slide falsification first. Stop before claiming validation or driving
      more acquisition if precision, attribution, timing, drift, bleaching, or
      replay fails. Multiple slides and days with slide-level splits only after
      useful one-slide results.
- [ ] Merge only reusable accepted framework code. **Do not ship a biological
      claim based on synthetic data or one slide.**

Post-merge design gate:

- [ ] Mandatory: record Run C acceptance results and remaining deferred work in
      `design/26-implementation.md`; update `design/26-ml-roi-detection.md` where
      the backend ladder or measured claims changed.

## 11. Design/32 Finding 4 Phase 2 — generated-hook worker isolation

Branch: `design32/hook-worker-isolation`

**Until this lands, source review plus hash pinning remain the entire containment
story, and no design may claim process containment.** It also blocks the combined
smoke test in block 12.

- [ ] Branch from updated `main` after the design/26 adapter contracts have
      supplied real fixtures.
- [ ] One long-lived stateful worker per acquisition, with a narrow,
      authenticated, length-prefixed protocol and bounded message and image
      sharing.
- [ ] Keep controller, guard, credentials, hardware queue, writable package
      source, and network out of the worker. Apply platform filesystem and process
      limits.
- [ ] Enforce hard deadlines, memory limits, termination, parent survival, audit,
      and acquisition abort/cleanup. Do not claim exact analytical resume without
      sufficient checkpoints.
- [ ] Test hangs, native crashes, oversized messages and artifacts, memory
      exhaustion, malformed actions, worker restarts, stateful hooks, and
      parent-side progress.
- [ ] Re-run design/26 Run A and the accepted Run B stored-data fixture; run a
      minimal confirmed live Run B regression if its boundary is image-time.
- [ ] Stop if isolation changes scientific payload or replay, loses state
      silently, lets a worker reach forbidden capabilities, or takes down the
      parent.

Post-merge design gate:

- [ ] Update design/32's guarantees and limitations from measured failure tests.
      Replace design/26's interim source-review containment caveat with the exact
      worker guarantee — no broader claim.

## 12. Closeout

Closeout runs last regardless of block number: **block 13 (Track D) is in its
scope**, and so is any Track B block still open. The file orders tracks by
priority, not by number — see Track D's placement between B and C.

- [ ] Re-run the full non-hardware suite and static checks on updated `main`.
      Discover the configured checks at closeout time. At creation of this file
      no linter was configured, so the known minimum is `compileall` plus the
      suite; do not preserve that observation as a permanent exemption if the
      repository adds a linter meanwhile.
- [ ] Run the combined end-to-end rig smoke test the previous closeout could not:
      startup authorization, acquisition planning and ledger, Run A observation
      and replay, saved mosaic replay, **and worker isolation** — in one session.
      This was `[!]` before because block 11 above did not exist. It is the honest
      gap; per-block gates are not a substitute for it.
- [ ] Add to the smoke test what this checklist introduces: a setup-generated
      profile driving a real session, and a continuous-focus rig if one is
      available.
- [ ] Confirm every rig artifact is hashed and every ledger row carries a merge
      and a design-reconciliation result.
- [ ] Confirm no design claims validation its gates did not establish. Sort every
      claim into implemented / rig-plumbing-verified / scientifically provisional
      / validated.
- [ ] Mark anything still deferred **with a reason**, not an empty checkbox.
- [ ] Give every item in the no-block register an explicit disposition:
      scheduled block, accepted deferral with owner/trigger, superseded with
      evidence, or promoted to a release blocker. Clean-hook-save enforcement
      and REPL API-key precedence must receive explicit security/usability
      severity decisions; they may not pass closeout merely because they were
      listed.

---

# Carried-forward register

Everything the previous checklist left open, and where it now lives. Nothing from
that file is dropped. Items marked **(no block)** are tracked but not scheduled;
schedule them or record a reason at block 12.

## Absorbed into a block above

| Item | Source | Now in |
|---|---|---|
| Block 14 Phase 5 first-launch setup | old §14 | **Block 4** |
| Undeclared light-source vulnerability (highest-priority open finding) | old closeout findings | **Block 2** |
| Authorization refusals carry a misleading hardware hint | design/33 `:827` | **Block 3** |
| M5 acquisition budgets copied from the fictional example | design/33 `:790` | **Block 5** |
| Inventory schema documented `v1`, producer emits `v2` | impact summary §Bookkeeping | **Block 1** |
| Stale closeout line claiming Phases 2/4/5 unstarted | old `:1338` | **Block 1** |
| Block 14's two unticked phase-wide rows | old `:1226`, `:1229` | **Blocks 1 and 4** |
| Configuration edits require restart (setup UX) | impact summary | **Block 4** |
| Session dose ledger not durable across restarts (explain in setup text) | impact summary | **Block 4** (text only; durability not in scope) |
| Camera ROI has no typed capability | design/33 `:708`, `:831` | **Block 4** emits exclusion; capability itself **(no block)** |
| MicroFPGA pulse-duration/dose typed actuator | design/33 `:709` | **Block 4** emits exclusion; capability itself **(no block)** |
| Phase 2 XY typed-actuator ambiguity / proposed `axis` field | impact summary | **Block 4** refuses ambiguous entries; schema extension **(no block)** |
| `TTL.State0` GenericDevice false positive | impact summary | **Block 4** (recommend exclusion, require confirmation) |
| Channel preset colliding with a typed actuator, only tested off-rig | impact summary | **Block 4** surfaces collisions; rig coverage **(no block)** |
| Phase 3's human confirmation gate was never validated | design/33 `:796` | **Block 4** rig gate requires an operator transcript |
| Probe 0, the null control that decides whether the move was ever implicated | design/34 `:184`–`:200` | **Block 0a** (authored), never run — **unanswered, gates nothing**; design/40 §"Still owed" |
| Probes 1–4, the motion cases | design/34 `:201`–`:217` | **Block 0a** (shipped, never run) — **retired 2026-08-05**, no block depends on them |
| Rig-profile values PFS needs (capture range, safe step, timeouts, versions) | design/34 `:223`–`:234` | **Block 7a** collects them as the bounded search runs; the "approach position" it asked for is refuted (design/40) |
| Whether MM Studio / NikonTI exposes a PFS-preserving jog | design/34 `:219`–`:221` | **Moot** — microclaw engaged PFS in software four times on 2026-08-05; the KB claim that only the GUI can was wrong |
| Continuous-focus / PFS coordination not modelled | design/34, design/40 | **Blocks 6a, 7a, 7b** (7c merged into 7a) |
| `move_stage_z` never measures the position it reports | design/34 `:110`–`:120` | **Block 6** |
| `move_named_stage` reports a missed target as success | design/40; design/34 `:236`–`:274` describes a signature that did **not** reproduce | **Block 6** |
| Nikon operator's install may no longer start after the tightening blocks | this session | **Block 0b** |
| Exclusions made PFS unusable; setup over-excludes stage-position properties | design/40 | **Block 6a** |
| Unassigned `Core.Focus` surfaces as a raw Java exception | design/40 | **Block 6a** |
| `get_focus_lock_state` is EMU-only | design/40 | **Block 6a** |
| Hooked-survey defects: position-list poisoning, `rank_hook_log`, hook-contract preflight, SNR gate, calibration zero-shift | design/40 | **Block 13** |
| Saved knowledge does not separate measurement from inference | design/40 D6 | **(no block)** — owed, shape not yet clear |
| Block 11 Run B | old §11 | **Block 9** |
| Block 12 Run C | old §12 | **Block 10** |
| Block 13 worker isolation | old §13 | **Block 11** |
| Combined end-to-end rig smoke test | old closeout `[!]` | **Block 12** |
| ROI-detection spike standalone baseline | old Block 0 `[-]` | **Block 10** first item |

## Still open, not yet scheduled **(no block)**

This is an inventory, not permission to close with unresolved blank work. Block
12 assigns every row one of the explicit dispositions above.

- **Two block-41b commits landed after its gate and are ungated**: `b6cc7a2`
  (adaptive runs refuse with an architectural reason) and `5af0fc6` (inline
  `snr_validity`, plus the structural guard). Both are two-directionally tested
  off-rig; neither has been exercised by a rig run. The next session that
  exports a script covers both incidentally.

- **Block 13's G3 — the transmitted-light SNR refusal is unmeasured on any rig.**
  The rule is stated once in design/25 and covered by unit tests, and it was
  verified off-rig on a synthetic dark-on-bright field (SNR refuses,
  `focus_metric_valid` stays true, tenengrad 2.99e8). No reachable rig has a
  transmitted-light path — **M5 does not**, which is why its 2026-08-06 gate
  skipped it. Run it on the first rig that has brightfield or phase; the Nikon is
  the likeliest. Merged 2026-08-06 with this explicitly owed rather than held.
- **Block 13's position-list rollback path is unexercised on a rig.** The M5 gate
  passed G1 without it, because check-before-mark meant nothing unsafe was
  written and no rollback was needed. The rollback only runs when a refusal
  lands *after* marking (a dose or budget refusal is the plausible one). Covered
  by tests, not by hardware.
- **`saturated_fraction` reporting precision landed after block 13's gate**
  (`f4e98c6`) and is itself ungated. Reporting-only, two-directionally tested.

- **`filament_position_filter` scores bead fields as filamentous.** Found by the
  operator during design/38's H6, 2026-08-05: two fields of beads scored
  `would_keep: true` (`filament_score` 0.045 and 0.107 against a 0.02 threshold),
  and the operator flagged it — "these are beads. Punctae, not filaments." The
  Sato tubeness response with the hook's current scales and SNR gate does not
  discriminate puncta from ridges at these settings, so the hook's central claim
  does not hold on the one sample class it was easiest to test against. This is a
  saved-hook quality problem, not a microclaw defect, but the hook is in the rig's
  registry and its description promises filament discrimination. Either recalibrate
  its scales against a known-filamentous and a known-punctate field, or narrow the
  description to what it measures (ridge coverage above an SNR gate).

- **Nine of M5's twelve saved hooks refuse to resolve, mostly for legacy reasons.**
  Registry survey, design/38 H6, 2026-08-05. All nine are legacy-newline-pinned
  with an on-disk hash that no longer matches the manifest — a pre-existing
  condition, not caused by the composition block. Beyond that: three
  (`mosaic_cell_counter`, `mosaic_stitcher`, `mosaic_stitcher_rot`) still use the
  pre-Block-7 contract (subclass `HookBase`, take `log_path`) and need rewriting,
  not just re-saving; two (`mosaic_stitcher_v2`, `mosaic_stitcher_rot_v2`) carry
  the reversed-`EmitArtifact` defect and would have failed mid-acquisition. The
  operator's working set is effectively three hooks. Migrating the rest is a
  half-day of hook rewriting nobody has scheduled, and until it happens the rig's
  registry mostly looks broken to its own agent.

- **Resolved: hooks compose and autofocus sweep dose is reserved.** A hook name
  or ordered list now runs in one acquisition. Post-hardware callbacks chain;
  image observers see separate copies of the original frame; any discard wins.
  The one-artifact-per-frame rule, `artifact_limits`, and audit log are per run,
  with hook attribution on composed entries. Saved hooks still cross their own
  hash check and `UntrustedHookAdapter` boundary, so trusted autofocus cannot
  launder controller, guard, queue, or path access. Reservations add the worst-
  case coarse and fine sweep planes using the exact plane-count helper called by
  `sweep_autofocus`; early termination closes under-spent. The legacy autofocus
  tool remains as a deprecated forwarding name. Forwarding deliberately changes
  its disk layout from one dataset per position to one dataset with a `position`
  axis; the result deprecation note and tool schema state that compatibility
  consequence explicitly. This is the layout `build_stage_coordinate_mosaic`
  can consume; retaining the old per-position layout would preserve the trap
  that made both original sessions acquire the sample a second time. The tool's
  callers are agent conversations rather than an external scripting contract,
  and the agent sees the migration through the result and schema. Display-only
  `snap` is not forwarded. `RequestAutofocus` remains
  unhonored. For the rig gate, repeat G2's five-tile plus, autofocus parameters,
  live-view check, position-axis inspection, and per-field Z review, adding the
  saved mosaic stitcher and checking its artifact plus the reservation report.

  Historical finding: from design/38
  (merged `088a9ad`). `run_multiposition_acquisition(hook_strategy="autofocus_per_position")`
  autofocuses per field and writes one `position`-axis dataset — proven on M5 in
  G2 — but it cannot *also* stitch or observe, because `hook_strategy` is a
  single string (`tools.py:2300,2473`; `_resolve_hook` at `:2696`). That is why
  G2 had to build its mosaic offline afterwards. The operator asked for
  composition directly ("run autofocus as a hook and then run whatever additional
  hook we like"). A written block prompt exists; it also folds in the next two
  rows. **Prerequisite for deprecating `run_multiposition_with_autofocus`.**

- **Resolved with the composition block: the autofocus sweep's dose is now reserved.**
  The original finding was that the sweep runs inside
  `post_hardware_hook_fn`, outside the event plan, so `_authorize_acquisition`
  never sees it; `_plan_protocol_repetitions` (`tools.py:2217`) counts only
  `_build_acquisition_events` frames. `sweep_autofocus` (`autofocus.py:76`) snaps
  once per plane at `n = round(span/z_step)+1`, and `AutofocusHook` runs a coarse
  then a fine pass. At M5 G2's settings that is tens of exposures per position
  against a reservation covering one. The shared plane-count helper and hook-
  aware plan now reserve and account those exposures for every runner using the
  reviewed autofocus hook, not only the deprecated tool.

- **design/38 F12 — a property write can report failure after it has succeeded.**
  On M5 G7.a, `set_device_property` on `All: 3. TTL Enable` raised
  `Serial timeout occurred. (17)`; the read-back showed the value had landed. The
  agent caught it, nothing in microclaw made it. Same shape as the
  failed-write-that-landed defect Block 7b's gate caught, so it recurs. Worst for
  illumination: an operator told a laser-enable failed may believe the laser is
  off when it is on, or retry and double-apply. Candidate fix — read back on a
  write exception and report `write_reported_failure_but_value_changed` with both
  values, rather than surfacing the raw exception.

- **design/38 F13 — the agent does not know it can read illumination state.** On
  exit in G7.c it told the operator "I can't confirm the illumination state on my
  own"; `get_system_state` now returns `declared_illumination_properties`. The
  round-4 prompt teaches it to consult that after a blank or low-signal frame,
  but not at session end or handoff. One prompt line.

- **design/38 Round 4 H2 — acquisition frame-cap policy is not inspectable.**
  The agent and operator can see a particular run's reservation, but no tool
  reports the currently configured acquisition frame cap by name. A reservation
  payload is not the policy value and cannot be used to determine whether a
  larger plan should bind. The 40 µm / 0.5 µm five-position autofocus run was
  accepted at 145 reserved frames, so H2's budget-refusal limb was not tested.
  Add a read-only policy/introspection surface in the block that next opens
  acquisition-budget usability; this composition block does not fix it.

- **design/38 F9 follow-up — per-source illumination prerequisites.** Preflight
  still cannot refuse an acquisition that will not emit: M5 G6.e passed preflight
  with the TTL gate shut and produced a signal-free frame (max 224 vs 469 with the
  gate open). Round 4 narrowed the *claim* — the preflight now reports what it
  checked and what it did not — but nothing refuses. Which declared properties
  must be **on** is not derivable: on M5, `All: 1. Enable` and `All: 2. Emission`
  are `0` while imaging works, `All: 3. TTL Enable` must be `1`, and three of four
  per-laser enables are correctly `0`. It needs operator declaration.
  Constraints settled with the operator: the requirement belongs in
  `safety_config.yaml`, **not** the knowledge base (knowledge is agent-writable,
  and a refusal resting on it lets the agent write its own permission slip); an
  absent declaration means **no check**, since most rigs have no such gate;
  `first_launch.py` already classifies every candidate property and authors
  `illumination.shutters` (`:841`, `:1165`), so this is one more question in an
  existing interview, not a new subsystem.

- **`tests/test_webserve.py::test_browser_opens_only_once_the_port_accepts` is
  race-prone and has now failed a rig gate.** Failed on M5 during Block 4d's G0,
  2026-08-03, on code that does not touch `webserve.py` and that passed the same
  test on the demo machine minutes earlier. `_open_when_ready`
  (`webserve.py:788`) polls a 15 s budget from a daemon thread; on a box busy
  driving cameras and stages the thread exited having never connected. The test's
  own comment records losing this same race once before, so a previous hardening
  pass was insufficient. Re-run in isolation on that same machine it passed three
  times for three at ~2.3 s, so the trigger is full-suite load, not the box. It will keep failing G0 for future blocks, where a red
  suite is supposed to mean something. Either give the poll a load-independent
  synchronisation point or mark it appropriately — do not simply raise the
  timeout again.
  **RESOLVED in Block 5 (`d14c147`, 2026-08-04)**, by the first option and not
  the second. `_open_when_ready` takes an injectable clock and sleep; the test
  supplies a clock advanced *only* by the polling worker's own sleeps, so
  scheduler starvation cannot consume the deadline. No timeout was widened,
  production keeps the real 15 s budget, and the neighbouring give-up test still
  exercises it. Passed G0 on demo and M5 on 2026-08-04.

- **A config can pass `check-config` cleanly and still have budgets that do not
  bind.** New in Block 5, and demonstrated by its own M5 gate. The
  `example_limits` diagnostic answers "are these still the example's values?"; it
  cannot answer "are these values sane." M5 passes at exit `0` while carrying an
  acquisition duration cap of ~317,000 years and a ~31.7-year illumination cap,
  because guaranteed mode requires only that the nine acquisition fields be
  finite and positive. Deliberately **not** folded into Block 5, whose scope was
  the example-copy path: an implausible-magnitude or non-binding-cap diagnostic
  needs its own thinking about what "implausible" means per rig, and a
  fail-closed refusal here would stop rigs that run today. Note the mitigation
  that already exists — the `confirm_above_*` tier is a separate, sane, and
  human-gating layer, so "the ceiling does not bind" is not the same as "nothing
  stops a runaway." Block 12 must give this a disposition rather than closing it
  by listing it.
- **`microclaw serve` produces no output at all when piped or redirected, until
  it dies.** Found while gating Block 4d, 2026-08-03; **not fixed there, because
  4d is a schema rename and the defect is unrelated to it.** The startup banner
  (`webserve.py:876`) is a bare `print()`, Python block-buffers stdout the moment
  it is not a console, and `serve` then blocks in the server loop forever, so the
  buffer never flushes and Ctrl+C discards it. There is no fallback source:
  `webserve.py:886` runs uvicorn at `log_level="warning"`, suppressing its own
  "Uvicorn running on" line. So an operator who follows any of our own runbooks
  and captures output to a log gets an empty file and cannot tell whether the
  session started — which is exactly what happened twice on the Block 4d demo
  gate, under two different capture methods. A `flush=True` on the banner is
  probably the whole fix. Block 4d's gate does not work around it; it stopped
  depending on captured stdout and takes the session history JSONL as evidence
  instead, so this stays an unfixed product defect and not a gate problem.
  **Route to block 5**, which owns the first-run and documentation path.
  **RESOLVED in Block 5 (`d14c147`, 2026-08-04):** both banner branches and the
  shutdown line now flush; the demo gate captured 260 bytes containing the URL.
- **Block 9b cross-rig inventory gate, four limbs.** Live M5 owes real
  enumeration failures, credential redaction, live config groups and state labels
  beyond `.cfg` contents, and bridge-typed returns. **Explicitly does not block
  block 4** — that block pins a supported schema version instead. Keep the
  credential-redaction limb: redaction keys off a property-*name* regex
  (`microclaw/rig_inventory.py:39`), so off-rig tests prove the substitution
  fires, not that the vocabulary matches real driver naming (`Passphrase`,
  `Community String`, `Login`).
- ~~**Block 4b's M2 gain gate is owed.**~~ **CLOSED 2026-08-03** by
  `block4b-m2-20260803-094116`: gain declared by default, clamp refused 1500
  against `3..1000`, and the two-gain images differ 2.1× in mean with neither
  saturated. Kept here only so the row's history is legible. Original text:
- **Block 4b's M2 gain gate is owed.** `bounded-numeric` merged with its clamp,
  refusals and setup emission proven on the demo machine and its mechanism
  proven on M5, but "gain on a real camera, with the change visible in the
  acquired data" needs M2's Andor iXon, which is not schedulable. Operator
  decision 2026-08-02: **this does not block 4c, 4d or 5.** Run it when M2 is
  next available and record the evidence against block 4b. Until then, no
  document may claim a bounded-numeric write was shown to change real acquired
  data. M5 cannot substitute — its Hamamatsu exposes no gain property.
- **A full disk is reported as a hardware or connection fault.** M2, 2026-08-03
  (`block4b-m2-20260803-081901/memory-issues.txt`): `run_timelapse` failed four
  times with `error: unpack requires a buffer of 4 bytes` — ndstorage's index
  unpacker hitting a truncated write on a pycromanager notification thread —
  carrying `_HARDWARE_HINT`. The operator went looking at the microscope and
  then deleted files, when the answer was free space. Two fixes, neither
  scheduled: a storage-layer failure must not claim a hardware or connection
  cause (Block 3's taxonomy, same shape as its `RigAuthorizationError` finding),
  and microclaw already computes `estimated_bytes` for `max_bytes` — it can
  compare that against free space on `save_dir` and refuse with a clear message
  before the acquisition starts. Note `run_timelapse` also declares no artifact,
  so its dataset is not downloadable; that is a separate register row.
- **Clean hook save is not enforced in code** (design/32 §4). The gate is
  conditional on the advisory lint firing, while the system prompt claims hook
  saves are enforced. Independent security fix.
- **REPL API-key precedence mismatch** (design/15). The documented
  `env > keyring > file` order applies to `serve` only; `run_session` never calls
  `load_api_key`, so a browser-stored key is invisible to the REPL. **Assigned to
  block 4 round 3** (2026-08-01) after it broke a step of the block 4 demo gate;
  it is no longer merely a candidate for a later block.
- **A failed hardware write was described as definitely not landed.** Block 2's
  M5 G4 first enable returned iChrome serial timeout 17; the agent then said the
  laser "was not enabled." That conclusion was unjustified: a write that raises
  may have landed, so state is unknown until read-back or cleanup. The separately
  confirmed retry later read `1`, and `serve` cleanup independently returned all
  five shutters to `0`, so Block 2's gate remains green. Fix the failure wording
  and require state verification in the appropriate tool/agent safety follow-up;
  do not fold it silently into Block 3's policy-refusal taxonomy.
- **`run_timelapse` declares no artifact**, so its dataset cannot be downloaded
  through `/api/artifact` (the adaptive runners do declare one).
- **Historical calibration-artifact authoring gap** (design/29): no supported way
  to author a calibration artifact for a dataset acquired before the artifact
  path existed. Not about objective turrets.
- **Context-compaction attribution observation** (design/32 §5). Under
  compaction the model twice attributed an earlier turn's tool calls to the
  current turn. The original diagnosis was refuted by the passing run. Settle it
  with a sharper probe before claiming a cause. Recorded as an observation, not a
  fix.
- **Unconfirmed SignalIO (12) and Galvo (16) bridge type ordinals** (design/33
  `:717`). Exercise on a rig with a galvo or DAC when one exists.
- **Mid-acquisition cancellation and abort trigger.**
- **Partial-failure and restart/session-ledger rig semantics.**
- **UV activation closed-loop test.** Depends on the future pulse-duration
  capability.
- **Missing NDTiff fixtures** (old Block 0 `[-]`): the design/30 spiral and the
  2500-tile datasets were never retrieved from the rig.
- **Shipped context thresholds are unexercised.** Block 15 shipped 120k/90k; the
  gate ran at 1500/800. Cache behaviour is asserted by test, not measured.
- **Offline and live validation have complementary, non-overlapping coverage, and
  neither is complete alone.** Found while reviewing block 0b, verified on
  `b717594`. **Corrected 2026-07-30:** an earlier version of this entry claimed a
  reviewed config could *start* with no exposure cap, no acquisition budget, and
  no Z ceiling. That is false, and the error was mine — I proved it against
  `load_safety_config` (the offline path) and generalised to live startup without
  reading `validate_live_rig`. The measured split is:
  - **Offline strict schema** rejects blank/null `stage.*` edges and
    `named_stages[*].min_um/max_um` (they must be finite), but **accepts**
    `camera.max_exposure_ms: null` and all nine `acquisition` budgets as null.
  - **Live `validate_live_rig` in guaranteed mode** rejects what the offline path
    misses: null `max_exposure_ms` when a camera is reachable
    (`microclaw/authorization.py:520`), any acquisition policy field that is not
    finite and positive (`:832`–`:836`), any **open range edge** —
    `{unbounded: true}` included — on a reachable stage actuator (`:504`–`:509`),
    and any reachable actuator with no declared policy (`:497`–`:502`).
  - Runtime enforcement is uniformly `if limit is not None and value > limit`
    (`microclaw/safety.py:806`, `:788`–`:802`, `:1091`–`:1094`), so a null that
    reached runtime would be unenforced — guaranteed mode is what stops it
    arriving, not the schema.
  **The consequence that matters is block 3's**, and it is sharper than the
  original claim: block 3 ships an **offline** validator that operators will
  trust, and as measured it would certify a config that live startup then refuses.
  "Passes the checker, then refuses on the rig" is exactly the usability failure
  block 3 exists to remove. Block 3's validator must therefore report the
  guaranteed-mode live requirements it cannot verify, rather than returning clean.
  Block 5 should check the deployed M5 config for null budgets; block 4 must never
  emit them. Note the exposure check is gated on a reachable camera being found.

## Standing constraints that outlive any block

- No design may claim **process containment** until block 11 lands.
- Rig facts belong in gate documents, design notes, and rig profiles — **never in
  `microclaw/`**. M5 (EMU + MicroFPGA) and Ti-with-PFS are both unusual; most
  rigs look like the demo config.
- A single failed rig run is a data point, not a diagnosis. Check whether it was
  environmental before writing a fix for it — probe 0 exists precisely because
  that step was skipped once.
- **Anything the remote Nikon operator runs is authored here and self-validating.**
  A probe they could not complete is a defect in the probe, not operator error.
  They return observations and explicitly confirm operator-owned safety facts;
  they are never asked to diagnose the system or judge whether probe evidence is
  valid.
- Push each block's branch to `origin` for rig review before merging to `main`.
  Do not open PRs; the user runs lab-machine integration tests first.
- Prefer stable section names or anchors in new checklist references. Existing
  `:NNN` references are navigation aids only: verify the cited text before acting
  because line numbers drift as the design documents change.
