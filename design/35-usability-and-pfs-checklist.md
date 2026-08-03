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

Progress markers: `[ ]` not started, `[-]` active, `[x]` complete, `[!]` blocked.

Rig-facing commands must be PowerShell/cmd-safe (the rig is Windows): prefer
`> out.txt 2>&1` over Unix pipelines.

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
| 0a | Remote kit | — | `design34/nikon-probe-kit` | `b717594` | `42d8978` (`8696169` rejected) | **is the deliverable** | `ba695da` | pending |
| 0b | Remote kit | — | `design34/nikon-stopgap-config` | `b717594` | `ead2fb9` (`6ac2ab2` rejected) | 0c ships it | `96a0a91` | pending |
| 0c | Remote kit | 0a, 0b | — (ship + wait) | | | **operator returns evidence** | n/a | |
| 1 | Usability | 0a and 0b assigned | `design33/phase5-doc-reconciliation` | `b717594` | `dd359a3` | n/a | `20b92e2` | done — block *is* the gate |
| 2 | Usability | 1 | `design33/undeclared-light-source-gate` | `98842cf` | `ef72b15` + `e9817ad` | **PASS** — M5 refusal/declaration/confirm/cleanup + separate demo fail-closed run | `a1b7579` | done — design/33 landed semantics + residual boundary |
| 3 | Usability | 2 | `design33/config-diagnostics` | `e8d6ee1` | `dce17a4` + `65bfd7c` | n/a — no rig surface | `0cb871f` | done — error taxonomy + offline-validation contract |
| 4 | Usability | 3 | `design33/first-launch-setup` (deleted) | `bc303a2` | `a742d73` | 5 rounds: demo r1 **FAIL**, r2/r3 **PASS**; M5 G4 + **G4b PASS** 2026-08-02 | `6266807` | **done** — design/33 §"Phase 5 landed" |
| 4r1a | Usability | 4 | `design33/first-launch-setup` | `15d8d1b` | `c5746b9` (`d203753` rejected) | folded into block 4 round 2 | n/a — merges via block 4 | |
| 4r1b | Usability | 4 | `design35/startup-refusal-severity` | `15d8d1b` | `2558583` (`16cc416` rejected alone) | folded into block 4 round 2 | `385049d` into block branch | |
| 4b | Usability | 4 merged | `design33/bounded-numeric-actuator` (deleted) | `578874e` | `5a6c6e2` | G1 demo **PASS**; G3 M2 **PASS** incl. imagery; G2 M5 in-range **PASS**, refusal step retired | `04164fd` | **done** — design/33 §"Block 4b landed" |
| 4e | Usability | 4b merged | `design33/emission-path-discovery` (deleted) | `85398e8` | `bc40f18` + `d631a4f` (`39f69dd` returned) | M2 G1/G2, M5 G3, demo G3 all **PASS** 2026-08-03 | `9b88394` | **done** — design/33 §"Block 4e landed" |
| 4f | Usability | 4e merged | `design33/channel-group-presets` | `f95c8ca` | | **required** | | |
| 4c | Usability | 4f merged | `design33/setup-named-stages` | | | **required** | | |
| 4g | Platform | none — may run concurrently | `design32/hook-hash-newline` | `95ae192` | `50e5f66` + `d0bb602` + `971cdb6` | M5 round 1 **FAIL** (fixtures); re-gate owed | | |
| 4d | Usability | 4c merged | `design33/property-authorization-rename` | | | **required** | | |
| 5 | Usability | 4b, 4e, 4f, 4c, 4d | `design33/deployed-config-hygiene` | | | required | | |
| 6 | Nikon | probe S = pre-fix baseline; post-fix run owed | `design34/measured-position-readback` | | | required | | |
| 7a | Nikon | scope: none; rig gate: probe 0 | `design34/continuous-focus-capability` | | | **required** | | |
| 7b | Nikon | 7a | `design34/continuous-focus-policy` | | | **required** | | |
| 7c | Nikon | 7b; only if evidence requires search | `design34/continuous-focus-initialization` | | | **required** | | |
| 8 | Nikon | 6, 7b, and 7c if applicable | `design33/phase5-continuous-focus` | | | required | | |
| 9 | Features | operator intake | `design26/generated-adapter-run-b` | | | required | | |
| 10 | Features | 9; optional | `design26/few-shot-run-c` | | | required or marked skipped | | |
| 11 | Features | accepted Run B fixtures | `design32/hook-worker-isolation` | | | regression required | | |
| 12 | Closeout | prior applicable blocks | — | | | **required** | n/a | |

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

## 0c. Ship, wait, and triage what comes back

No branch — this is coordination.

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
- [-] Optional and clearly marked as defence-in-depth, not a Phase 5 obligation:
      detect limits still equal to `safety_config.example.yaml`'s fictional
      values and say so. This targets the `microclaw init` copy-the-example path,
      which block 5 also touches. **Skipped in Block 3** as the optional item it
      is marked; carried to block 5, which owns that path.

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

Continuous focus — hard exclusion until blocks 7a–7b land (and 7c if required):

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
- [x] Mark PFS-offset workflows unsupported even when the offset has reviewed
      bounds, until block 6's settling work lands.

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

- [ ] Run on the demo core first: a complete pass producing a profile that then
      passes block 3's offline validator and, after a human sets `reviewed:
      true`, starts a session.
- [ ] Run on M5 and produce a profile for a real rig with real hazards. Compare
      against the deployed M5 config and report every difference — differences
      are findings in one direction or the other, not automatic failures.
- [ ] **An operator-driven transcript is required evidence** showing that an
      unresolved choice cannot be silently accepted. Automate the mechanical
      workflow checks, but do not accept a self-confirming probe as evidence of
      the human boundary — that is exactly the weakness recorded against Phase 3
      (design/33 `:796`).
- [ ] Stop on any unenumerable effect or any pre-validation write.

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

- [ ] Add `kind: bounded-numeric` to the typed-actuator schema, requiring
      `units` (an operator-supplied string, **recorded and echoed, never
      interpreted**), `minimum`, and `maximum`. The kind names the safety
      contract, matching the existing two: `absolute-position` clamps and feeds
      stage bounds; `illumination-power` clamps and feeds the dose ledger;
      `bounded-numeric` clamps and feeds nothing.
- [ ] Clamp-only enforcement in `SafetyGuard.check_typed_actuator`: no canonical
      conversion, no `full_scale`, no ledger participation. A write outside
      `[minimum, maximum]` is refused; a write inside it is permitted.
- [ ] **Hard refusal if the declared pair aliases a built-in capability** —
      `_known_continuous_raw_pair` (focus position, XY, camera exposure) or any
      `illumination_pairs` member. Without this the new kind is a backdoor
      around `camera.max_exposure_ms`, the stage bounds, and the illumination
      ratchet. This is the single most important test in the block.
- [ ] First-launch setup: a bounded numeric MM characterises (`has_limits` plus
      a numeric `reported_type`) defaults to this kind, with MM's technical
      range as the Enter-acceptable default bounds and the operator supplying
      the unit. This is what Block 4 could not express and had to exclude — it
      restores the round-1 instruction that continuous actuators are fine to use
      over an appropriate range, without inventing a physical kind.
- [ ] Decide and document guaranteed-vs-degraded behaviour for a
      `bounded-numeric` whose live driver range is narrower than the declared
      bounds. Follow the existing typed-actuator precedent (`authorization.py`
      rejects typed bounds exceeding the driver technical range) rather than
      inventing a new rule.
**Both acquisition-policy decisions below were settled by the operator
2026-08-02: deprecate, do not delete.** Deleting either key stops every deployed
config that sets it — M5's included — from loading, which is a rig outage
bought for a schema tidy. The implementer executes the decision; it is not
reopened.

- [ ] **`acquisition.confirm_above_bytes` — accepted-but-ignored deprecation.**
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
- [ ] **`acquisition.max_session_illuminated_ms` — optional, with the brake
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

- [ ] Off-rig tests: the alias refusal above; clamp at both edges and outside;
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

- [ ] **Demo machine — the gain path end to end.** Generate a profile declaring
      `Camera.Gain`, set it through Microclaw inside the declared range, and set
      it outside; show the second is refused by the clamp and the first is not.
- [ ] **M5 — the mechanism on real hardware.** A bounded-numeric write must be
      shown to reach a real device, clamp at its declared bound, and refuse
      outside it. Any declared bounded numeric whose effect can be read back
      qualifies; `SmarAct 2D.Hold time (ms)` is the least invasive candidate (no
      light, no motion, trivially read back), with `Laser Trigger.Duration0 (us)`
      as the alternative if a control the operator already exercises is
      preferred. This step exists so that merging without M2 does not leave the
      kind unproven on real hardware — only *gain* waits.
- [ ] Confirm no exposure or illumination path became writable as a side effect,
      on both machines.

Owed, does **not** block 4b's merge or blocks 4c/4d/5:

- [ ] **M2 (Andor iXon) — gain on a real camera.** Set EM gain inside and
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

## 4f. [ ] `channels.allowed` is generated from the wrong config group

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

- [ ] Emit `channels.allowed` only from the group `authorization` actually
      reads. If that group is absent, emit an empty list and say so in the
      setup text and in the review notes — an empty, honest key beats six
      claims the rig will drop.
- [ ] The demotion message must stop advising an edit that cannot be right. It
      currently assumes the preset belongs in the `Channel` group; on a rig with
      no such group the correct action is different. Distinguish "this preset is
      missing from a group that exists" from "the group does not exist here".
- [ ] Decide what setup should do with presets in **other** groups. They are
      real and useful; they are simply not channels. Report a decision — surface
      them as review notes, or say plainly that microclaw does not drive them —
      rather than silently discarding.
- [ ] Do not generalize beyond the `Channel` name without changing
      `authorization.py` too; producer and consumer must name the same group or
      this defect recurs inverted.
- [ ] Rig gate: generate a profile on M2 (or M5) and show `channels.allowed` no
      longer claims presets the authorizer will drop, and that startup produces
      no preset demotion. On demo, show the real `Channel` presets still appear
      and fluorescence channels still work.

## 4g. [ ] Saved hooks are unusable on Windows — two hash conventions disagree

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

- [ ] Pick one canonical convention and use it at every save, load, describe and
      offline-adapter site. The on-disk bytes are the artifact the manifest
      claims to pin, so hashing raw bytes and writing bytes (or text with
      `newline=""`) is the obvious direction — but state the choice and apply it
      everywhere rather than patching the failing call site.
- [ ] **Existing manifests carry hashes pinned under the old convention.** Decide
      migration: re-pin on load with an explicit prompt, refuse with an
      actionable message, or accept both forms for a release. A hash the user
      consented to may not be silently rewritten — the manifest *is* the consent
      record.
- [ ] Add a test that fails on POSIX today, by writing a `\r\n` file and
      round-tripping it. The current suite passes on POSIX precisely because it
      never exercises the difference, which is why this survived four blocks.
- [-] Rig gate on any Windows machine: save a hook, then load it through the
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

## 4c. [ ] Reachable non-core stages — `named_stages` is never emitted

Branch: `design33/setup-named-stages`. Depends on 4b only for ordering, not
mechanism: **no schema change is needed**, `named_stages` already exists
(`safety.py:159`).

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

- [ ] Ask for travel bounds for every loaded `StageDevice` that is not the core
      focus device, and emit `named_stages` entries. Propose the driver
      technical range where MM reports one, exactly as the core stages now do,
      and say so plainly where it reports none.
- [ ] Do not emit an entry for the core focus device: `stage.z_min/z_max` owns it
      and `authorization.py:598` refuses the duplicate.
- [ ] Decide what to do about an `XYStageDevice` that is not the core XY stage.
      `named_stages` is single-axis by construction, so this may be an honest
      exclusion with a printed reason rather than a silent omission.
- [ ] Rig gate: move a non-core stage on M5 through `move_named_stage`, at a
      value inside the declared range and at one outside it, and show the second
      is refused.

## 4d. [ ] Rename and regroup the property-authorization schema

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

- [ ] Proposed shape: `property_authorization` with `allowed_categorical`,
      `allowed_numeric`, `denied`. Confirm the grouping against what 4b's third
      kind and 4c's `named_stages` work actually left behind before committing
      to those three names.
- [ ] **A rename breaks every deployed config, M5's included.** Decide migration
      — accept both keys for a release, or ship a one-shot rewriter — rather
      than assuming a clean cut. Whichever is chosen, no rig may be left unable
      to start by the merge.
- [ ] First-launch setup emits the new shape; the offline validator names the
      new paths in its diagnostics; both old-key and new-key configs are covered
      by tests.
- [ ] Rig gate: start a session on M5 under its **existing** deployed config
      (whatever the migration promises), and under a regenerated one.

## 5. [ ] Deployed-config hygiene and the `init` path — rig config review

Branch: `design33/deployed-config-hygiene`

- [ ] Fix the deployed M5 config's acquisition budgets, which were copied from
      the fictional example including the exposure limit (design/33 `:790`). This
      is a rig-config review item with a human in the loop, not an inferred edit.
- [ ] Decide what `microclaw init` becomes now that Phase 5 exists: redirect to
      setup, keep it as the hand-authoring path with a pointer, or keep both.
      Whichever — the copy-the-example path is what put fictional limits on a
      real rig, so it must not remain the *recommended* route.
- [ ] Update README and any first-run documentation to describe the real path:
      install → `inspect-rig` / setup → review → restart. Include the offline
      validator from block 3.
- [ ] Re-check `safety_config.example.yaml`'s framing under the new flow. It
      stays fictional by design; the question is whether it is still the first
      thing a new operator meets.

Post-merge design gate:

- [ ] Record the final first-run path in design/33 and design/17 (install and
      desktop shortcut), which currently describes the `init`-centred flow.

---

# Track B — Nikon PFS and position reporting (blocks 6–8)

Source: `design/34-nikon-pfs-tizdrive-findings.md`. **Evidence comes from Track
0**, which is authored here and run by the remote operator. Blocks 7 and 8 are
scoped from what comes back; block 6 is not.

Values Track 0 must have returned before blocks 7a–7c can be scoped — this is the
design/34 `:223`–`:234` list, and it is the acceptance test for whether round
trip #1 was sufficient:

- safe TIZDrive approach range and a hard ceiling **per objective and
  sample-holder combination** — not one universal number;
- direction convention; safe search step size; expected PFS capture range;
- search and lock timeouts, and `FullFocusTimeoutMs` as configured;
- NikonTI adapter, Micro-Manager, and microscope firmware versions;
- repeatability and sample-dependence of the 2440–2450 µm engagement position;
- safe and useful `TIPFSOffset` limits, plus the offset's measured latency and
  useful settling tolerance.

Anything still missing after round trip #1 goes into round trip #2's scope
(block 0c), not into an assumption.

## 6. Measured position read-back and named-stage settling

Branch: `design34/measured-position-readback`

Two real defects that are **independent of the PFS question** and of probe 0's
outcome. They affect every rig, not just the Nikon. Small; can run in parallel
with Track A if a second agent and worktree are available.

- [ ] `move_stage_z` (`microclaw/tools.py:342`) returns the **requested target**
      as `z_um` with `"status": "Moved."` and performs no read at all. The
      session shows the consequence: it reported `{"z_um": 2440, "status":
      "Moved."}` while a later read measured 2462.2 µm. Return **measured** Z.
- [ ] A move that the servo modifies, clamps, or rejects must not be reported as
      a clean success. Decide and document what the tool returns when measured
      and requested disagree beyond tolerance — this is a contract change, so it
      needs a stated rule, not a silent field swap.
- [ ] Freeze the result contract before coding: success reports at least
      `requested_um`, `measured_um`, `tolerance_um`, and `within_tolerance: true`;
      timeout or mismatch is a typed failure carrying the same measured fields,
      elapsed time, and last device status. Define the tolerance source, timeout,
      polling interval, required consecutive in-tolerance samples, and stability
      window in the design reconciliation; none may be an unexplained magic
      constant.
- [ ] `move_named_stage` (`microclaw/tools.py:423`) performs one immediate read
      after `wait_for_device` and returned the **exact previous target** on all
      three offset moves in the session (130 → reported 149.475; 160 → reported
      130.0; 149.5 → reported 160.0). Reporting the previous *target*, not an
      intermediate value, means the stage had fully settled where it was and had
      not begun moving: **the adapter's `Busy()` clears before motion starts.**
      A longer wait does not fix this.
- [ ] Poll until the measured position is within a configured tolerance **of the
      target** and stable, or until a timeout. **The tolerance-of-target
      condition must be the gate:** because motion has not started at the first
      read, a stability check alone passes immediately at the old position, which
      is precisely the observed failure.
- [ ] Image acquisition and focus scoring must not begin until the settling check
      succeeds.
- [ ] Apply the same read-back rule to `MicroscopeController.set_z`
      (`microclaw/controller.py:552`).
- [ ] Off-rig tests must include the observed failure shape specifically: a fake
      whose `Busy()` clears before motion starts, proving the stability-only
      check would pass and the target-tolerance check does not.

Rig gate:

- [ ] Nikon rig: three consecutive `TIPFSOffset` moves reporting their own
      achieved positions, with TIZDrive recorded alongside. **Probe S's returned
      data is the pre-fix baseline for this** — it measures exactly this shape, so
      do not commission a separate baseline run. What is still owed is the
      post-fix comparison against it.
- [ ] Any rig: a Z move whose measured result differs from the request is
      reported as such, not as a clean success.

Post-merge design gate:

- [ ] Record the settling contract and the read-back rule in design/34 and
      wherever the tool contracts are documented. State that reviewed bounds are
      **not** proof that an asynchronous device achieved or settled at its target.

## 7. Typed continuous focus — split capability, enforcement, and initialization

Branches: `design34/continuous-focus-capability`,
`design34/continuous-focus-policy`, and, only if needed,
`design34/continuous-focus-initialization`.

**Scope 7b and 7c only after Track 0 reports** — 7a's scope is independent and
may start earlier. If probe 0 shows the timeout
hypothesis, 7b may shrink to mandatory `require_off` enforcement and 7c may be
skipped. Probe 0 does not eliminate 7b: a Z-writing path can still fight an
already-locked PFS servo, especially during software autofocus.

Continuous focus should be a typed microscope capability, not an arbitrary
`set_device_property` write. Keep 7a, 7b, and 7c as separate branches and
rollback boundaries; do not merge them merely because they touch the same
paths. If Track 0 eliminates 7c, mark it skipped with the evidence and reason.
Block 7b is not optional.

### 7a. Capability and status operations

**Scope does not depend on Track 0** — this sub-block survives either probe-0
outcome, so it may be implemented before round trip #1 returns. Only its rig
gate waits on the evidence.

- [ ] Add operations that discover the configured autofocus device via
      `get_auto_focus_device()`; enable and disable continuous focus through the
      CMMCore API; report enabled and locked state; and wait for a well-defined
      lock, failure, or timeout. `enableContinuousFocus`,
      `isContinuousFocusEnabled`, and `isContinuousFocusLocked` are in the
      mmcorej 2.0.3 API (`tests/fixtures/mmcorej-cmmcore-2.0.3-methods.txt`) and
      are currently unused by production code.

Rig gate (Nikon):

- [ ] Show enable, disable, enabled-state, locked-state, and timeout/failure
      reporting against the outcome observed by probe 0.

### 7b. Per-path movement policy

- [ ] Give **every** Z-moving path an explicit continuous-focus policy, from
      `require_off`, `move_then_rearm`, or `preserve`. Not just the two obvious
      seams:
      - `microclaw/tools.py:342` (`move_stage_z`)
      - `microclaw/controller.py:552` (`set_z`)
      - `microclaw/autofocus.py:94`, `:103`, `:115` — sweep, move-to-best,
        `_restore`
      - `microclaw/hooks.py:313` — the focus-recovery jog
      - `microclaw/tools.py:2175` — per-position Z in the tile/grid path
      The last four are the ones most likely to run unattended; a policy covering
      only the first two misses the dangerous paths. All are already inside a
      `check_z`-guarded range — the gap is continuous-focus awareness, not bounds.
- [ ] **Autofocus is the sharpest case:** a software focus sweep while PFS holds
      lock both fights the servo and duplicates what the servo already does.
      State what `run_autofocus` does under an armed PFS.
- [ ] **`preserve` must not be implemented by re-enabling PFS after the move.**
      Re-arming after a move is observably different from keeping PFS searching
      throughout it and must be reported as such. Do not offer `preserve` at all
      unless Track 0 established a movement path that actually preserves search.
- [ ] Reproduce the session's original failing sequence and show the enforced
      policy reports its behaviour truthfully.
- [ ] Show each implemented policy on at least one Z-moving path, including a
      refusal under `require_off`.

### 7c. Optional bounded initialization/search operation

- [ ] Implement this sub-block only if Track 0 establishes a safe need and the
      reviewed rig profile can express it. Prefer a dedicated bounded operation
      over letting the
      agent assemble arbitrary property writes and moves: confirm PFS off → move
      to a **configured** approach position → enable PFS → if no lock, follow a
      configured rig-validated search policy with a small step, a timeout, and a
      hard Z ceiling → stop immediately on lock or unexpected status → permit
      offset adjustment only after lock is confirmed.
- [ ] **Keep it generic.** Drive it from `get_auto_focus_device()` and a rig
      profile. No Nikon-named recipe, no 2440 µm baked in. Ti-with-PFS is as
      unusual as M5 is; rig facts belong in design/34 and the rig profile, never
      in `microclaw/`.
- [ ] Extend the safety schema to express the capability. Context-dependent
      approach ceilings may exceed the current flat stage-range model — if the
      schema cannot express per-objective/per-holder bounds, say so and leave the
      policy unresolved rather than collapsing it.

Rig gate (Nikon, for 7c if applicable):

- [ ] Show the successful sequence (PFS off → move to approach → PFS on →
      `Locked in focus`) works through the typed capability.

Post-merge design gates:

- [ ] **After 7a:** update design/34 with the measured enable, disable, enabled,
      locked, failure, and timeout semantics, including which probe-0 hypothesis
      the evidence supported.
- [ ] **After 7b:** record the policy enforced for every Z-writing path and which
      of `require_off`, `move_then_rearm`, and `preserve` are actually available
      on this hardware. Do not claim `preserve` without direct evidence.
- [ ] **After 7c, if implemented:** record the bounded initialization/search
      behavior and update design/33 with the associated schema extension. If 7c
      is skipped, record the evidence and reason instead.

## 8. Phase 5 continuous-focus addendum

Branch: `design33/phase5-continuous-focus`

- [ ] Now that a typed capability with rig-verified enable, lock, failure,
      timeout, and Z-movement semantics exists, let Phase 5 declare it — replacing
      block 4's blanket exclusion.
- [ ] Require reviewed approach bounds for the objective and sample-holder
      combination. Still never copy an observed engagement position.
- [ ] Lift the "PFS-offset workflows unsupported" marker only if block 6's
      settling work is merged and its rig gate passed.
- [ ] Regenerate a Nikon profile through setup and start a session under it.

Post-merge design gate:

- [ ] Record in design/33 what Phase 5 now emits for a continuous-focus rig and
      what remains unresolved.

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
| Probe 0, the null control that decides whether the move was ever implicated | design/34 `:184`–`:200` | **Block 0a** (authored), **0c** (answered) |
| Probes 1–4, the motion cases | design/34 `:201`–`:217` | **Block 0a** (shipped, held), **0c** (released or retired) |
| Rig-profile values PFS needs (approach range, ceilings, timeouts, versions) | design/34 `:223`–`:234` | **Block 0a** probe E + Track B preamble |
| Whether MM Studio / NikonTI exposes a PFS-preserving jog | design/34 `:219`–`:221` | **Block 0a** probe U |
| Continuous-focus / PFS coordination not modelled | design/34 | **Blocks 7a–7c**, scoped from Track 0 |
| `move_stage_z` never measures the position it reports | design/34 `:110`–`:120` | **Block 6** |
| `move_named_stage` asynchronous settling | design/34 `:236`–`:274` | **Block 6**; measured by **block 0a** probe S |
| Nikon operator's install may no longer start after the tightening blocks | this session | **Block 0b** |
| Block 11 Run B | old §11 | **Block 9** |
| Block 12 Run C | old §12 | **Block 10** |
| Block 13 worker isolation | old §13 | **Block 11** |
| Combined end-to-end rig smoke test | old closeout `[!]` | **Block 12** |
| ROI-detection spike standalone baseline | old Block 0 `[-]` | **Block 10** first item |

## Still open, not yet scheduled **(no block)**

This is an inventory, not permission to close with unresolved blank work. Block
12 assigns every row one of the explicit dispositions above.

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
