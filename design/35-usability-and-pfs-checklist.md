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

### Run ledger

| Block | Track | Depends on | Branch | Start commit | Implementation commit | Rig evidence | Merge | Design reconciliation |
|---|---|---|---|---|---|---|---|---|
| 0a | Remote kit | — | `design34/nikon-probe-kit` | `b717594` | `42d8978` (`8696169` rejected) | **is the deliverable** | `ba695da` | pending |
| 0b | Remote kit | — | `design34/nikon-stopgap-config` | `b717594` | `ead2fb9` (`6ac2ab2` rejected) | 0c ships it | `96a0a91` | pending |
| 0c | Remote kit | 0a, 0b | — (ship + wait) | | | **operator returns evidence** | n/a | |
| 1 | Usability | 0a and 0b assigned | `design33/phase5-doc-reconciliation` | `b717594` | `dd359a3` | n/a | `20b92e2` | done — block *is* the gate |
| 2 | Usability | 1 | `design33/undeclared-light-source-gate` | `98842cf` | `ef72b15` + `e9817ad` | **PASS** — M5 refusal/declaration/confirm/cleanup + separate demo fail-closed run | `a1b7579` | done — design/33 landed semantics + residual boundary |
| 3 | Usability | 2 | `design33/config-diagnostics` | `e8d6ee1` | `dce17a4` + `65bfd7c` | n/a — no rig surface | `0cb871f` | done — error taxonomy + offline-validation contract |
| 4 | Usability | 3 | `design33/first-launch-setup` | `bc303a2` | round 4 `5c82d1e` (runbook re-pinned at `b059f33`) | r1 **FAIL**; r2/r3 demo **PASS**; M5 G4 validates but **the profile could not move a filter wheel**; **G4b PASS on M5 — wheel moves**; review then found 3 more issues, fixed at `b67fd08`; **awaiting re-gate** | | |
| 4r1a | Usability | 4 | `design33/first-launch-setup` | `15d8d1b` | `c5746b9` (`d203753` rejected) | folded into block 4 round 2 | n/a — merges via block 4 | |
| 4r1b | Usability | 4 | `design35/startup-refusal-severity` | `15d8d1b` | `2558583` (`16cc416` rejected alone) | folded into block 4 round 2 | `385049d` into block branch | |
| 4b | Usability | 4 merged | `design33/bounded-numeric-actuator` | | | **required** | | |
| 4c | Usability | 4 merged | `design33/setup-named-stages` | | | **required** | | |
| 5 | Usability | 4b, 4c | `design33/deployed-config-hygiene` | | | required | | |
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

## 4. [-] Block 14 Phase 5 — first-launch setup — **rig gate required**

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

- [ ] Update design/33 with the setup contact semantics actually measured, every
      unavoidable device initialization observed, the decisions the flow demands,
      the cases it refuses, and the exact ordering guarantee. Retire the "later
      work" framing at `:347`.
- [ ] Tick the two Phase-wide Block 14 rows carried into block 1.
- [ ] **Reconcile the "never a default" rule with finding 2.** The rule as
      written ("Driver technical ranges, current values, allowed values … are
      never used as safety limits or answer defaults") is now scoped: MM's own
      writability and value-domain metadata *is* the classification default;
      technical ranges remain barred from becoming safety bounds on hazardous
      axes. design/33 and this file's block 4 item text both say the unscoped
      version and must be corrected to whatever ships.
- [ ] **Record the startup refusal-severity taxonomy** from findings 3–5 in
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

## 4b. [ ] The `bounded-numeric` typed actuator — gain and its kin

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
- [ ] **Decide `acquisition.confirm_above_bytes`'s fate** — deferred here from
      Block 4 round 3 finding 8, because this is the block already opening
      `safety.py`. The operator's position is that it should not exist: an
      estimated byte count is a function of frames and geometry, so the frame and
      duration confirmations already gate it. Round 3 only stopped *asking* for
      it (guaranteed mode requires all nine `_ACQUISITION_POLICY_FIELDS` finite
      and positive, `authorization.py:1056`, so it is derived to a non-binding
      value instead). Deleting it touches `safety.py`, `config.py`,
      `authorization.py`, `tools.py:112`, and `safety_config.example.yaml`, and
      deployed configs — M5's included — already set the key, so choose between
      removal and accepted-but-ignored deprecation rather than assuming removal.
      `max_bytes` itself stays; it is a hard cap and round 3 derives its default.
- [ ] **Decide whether `acquisition.max_session_illuminated_ms` deserves to
      exist**, deferred here from Block 4 round 3 finding 6 for the same reason
      as the byte threshold. The operator's position is that it should not force
      a restart on someone who has been imaging for a while, and that a cap reset
      by restarting the process is not a dose guarantee. Its one defensible
      function is a brake on a runaway in-process loop
      (`AcquisitionLedger.reserve`, `acquisition.py:34`). Round 3 only proposes a
      generous anchor so it cannot fire on a legitimate session. The options are
      removal, keeping it as an explicitly optional key that guaranteed mode
      stops requiring, or keeping it with the brake framing stated in the config
      and the refusal message. Decide with the byte-threshold item, in one pass
      over `_ACQUISITION_POLICY_FIELDS`.
- [ ] **Rename and regroup the property-authorization schema.** Operator
      finding, 2026-08-02: `rig_profile` reads as a description of the rig, but
      the rig's description is the inventory — `rig_profile` is the raw-property
      *write-authorization map*. Worse, it is not the whole map: stage travel is
      in top-level `stage`, exposure in `camera`, illumination in `illumination`
      (the four built-in typed capabilities, deliberately not duplicated), while a
      typed `illumination-power` actuator must appear in **both**
      `rig_profile.typed_actuators` and `illumination.power_properties` or startup
      refuses. `typed_actuators` is not even a field of the `RigProfile` object
      (`safety.py:200`) — it lives under that YAML key for historical reasons
      only. Proposed shape: `property_authorization` with `allowed_categorical`,
      `allowed_numeric`, `denied`. **A rename breaks every deployed config,
      M5's included**, so decide migration (accept both keys for a release,
      or a one-shot rewriter) rather than assuming a clean cut. Interim, owed
      regardless: a schema map in design/33 saying which section owns which
      write path, which is post-merge design-gate work for Block 4.

- [ ] Off-rig tests: the alias refusal above; clamp at both edges and outside;
      a declared unit round-tripping into the profile unaltered; setup emitting
      the kind from the real demo inventory fixture; and a regression that
      `Camera.Exposure` still cannot be declared `bounded-numeric`.

Rig gate:

- [ ] Set `Camera.Gain` through Microclaw on the demo machine and on a real
      camera, at a value inside the declared range and at one outside it, and
      show the second is refused. Capture an image at two gain settings and
      confirm the change is visible in the data.
- [ ] Confirm no exposure or illumination path became writable as a side effect.

Post-merge design gate:

- [ ] Record the three-kind taxonomy and the not-dose-bearing rationale in
      design/33, alongside the refusal-severity taxonomy from Block 4.

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

## 5. Deployed-config hygiene and the `init` path — rig config review

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
