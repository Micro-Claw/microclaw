# The carried-forward register

**This file is a register, not a coordinator.** It holds the rows nobody owns
yet. It names no blocks, runs no gates and keeps no ledger — when a row grows
big enough to own blocks, it becomes a design notebook of its own and this file
points at it.

Created 2026-09-02, from `design/35-usability-and-pfs-checklist.md`'s
carried-forward register. That file is closed to new items; each design notebook
is now its own coordinator (`CLAUDE.md` §"The block workflow").

## How to use it

- **Work by importance and by ease.** A row settled locally or on the demo
  machine costs an afternoon; a row needing M2 or M5 costs a booked session and
  a scoring pass; a row needing a Nikon Ti cannot be worked at all right now.
  The two orderings are both in the table below, so a session can take the
  cheapest high-value row rather than the highest-value row it cannot run.
- **A row that names a block is not a register row.** If the disposition column
  names a block, do the block; the row is there to say the block exists.
- **A new row goes here, not into a design notebook**, unless a notebook already
  owns that subject.
- Rig shorthand: **M2** (Andor iXon, TIRF, camera trigger fires the lasers),
  **M5** (EMU + MicroFPGA, Hamamatsu, no gain, no transmitted light, no `Channel`
  group, empty core shutter, no autofocus device), **Nikon** (a Ti/Ti2 with PFS —
  **the operator has no access to one; these rows are blocked indefinitely**),
  **Zeiss** (new, `ZeissDefiniteFocusOffset` telemetry stage, manual/remote XY
  controller — see `design/69b`), and **demo** (the Windows demo machine on
  Micro-Manager's demo config).

## Triage of 2026-09-02

Every row of `design/35`'s carried-forward register was evaluated the day this
file was created. Each got **its own Codex runner**, asked the same six questions
against `main`: what the row is, whether it is still open, how important, where
it can be settled, whether a block already owns it, and how big it is.

**71 of the 80 rows were evaluated that way. The last 9 were not** — the Codex
usage limit was reached, and those are the coordinator's own code reads. They are
marked `*` in the queue and carry a **Provenance** line in their entry. The queue
had been ordered cheapest-last precisely so a limit would land there, and it did.

**17 rows were already closed and had never been struck**, and 2 more are open
but have no closure criterion worth tracking. Several of the closed ones were
closed *incidentally*, by a block that recorded it nowhere the register could
see — `get_focus_lock_state`'s EMU-only false negative, which the register still
calls *"the highest-value of the three remaining rows"*, was fixed by block 56a
on 2026-08-23 and sat here for ten days looking live. Those rows are **not**
carried into this file; `design/35`'s register section now opens with a
disposition table naming each one and what closed it.

**Read the ease column before the importance column.** 39 of the carried rows
need no hardware at all and 13 more need only the demo machine — against 9 that
need a booked rig, 4 blocked on a Nikon Ti that no longer exists, and 1 blocked
on a third party. This register had been read as a list of rig debts. It is
mostly not one, and that is the single most useful thing the triage found.

Three cautions, stated so a later reader can weigh this:

- **A status here is a code read, not a gate.** Where it says CLOSED it names the
  commit or block; where it says OPEN it names the `file:line` that is still
  live. Neither is rig evidence, and **a row marked CLOSED has not been
  re-gated** — this file asserts that the defect's code path is gone, not that
  hardware confirmed it.
- **The importance grades are the evaluator's, not the operator's.** They are
  comparable to each other because every row got the identical question, which is
  what makes the ordering useful. They are not a ruling, and the operator may
  reorder any of them.
- **An `M2`/`M5`/`Zeiss` row is a claim about capability, not a booking.** Check
  the named rig still has the capability the row needs before spending a session
  on it; `design/69b` shows how little of the Zeiss is characterised.

## Row numbering, and rows added since

`R01`–`R81` are the design/35 triage, numbered in that register's own order.
**Anything from `R82` up was added afterwards**, by a notebook that found
something it would not fix; each says so in a **Provenance** line and names the
notebook and section it came from. Add the next row at the end of the numbering,
in the bucket its `Where` puts it, and give it the same six fields — a row
without a `Where` cannot be scheduled by ease, which is the one thing this file
is for.

Rows added since the triage:

| row | from |
|---|---|
| `R82`–`R86` | `design/71-installable-extensions.md` §"Register rows this leaves behind", 2026-09-02 |
| `R87` | `design/72` block 72a's demo gate, 2026-09-02 |
| `R88` | a Nikon Ti session scored on 2026-09-04, no notebook |
| `R89` | found while scoring `design/74` block 74a, 2026-09-04 |
| `R90` | found while scoring `design/74` block 74a's demo gate, 2026-09-04 |
| `R91`–`R93` | `design/74`'s Nikon confirmation, 2026-09-05 — **on the unmerged `design74/nikon-confirmation` branch**, which is why 75a's rows start at `R94` |
| `R94`–`R95` | `design/75` block 75a's gates, 2026-09-05 |


## The work queue

Sorted by ease, then by importance. `→` names an existing block; do the block, not the row. A `*` marks a row the coordinator triaged by hand rather than by runner — see "Triage" above.


**Settle off-rig — no instrument at all**

| | row | importance | effort | block |
|---|---|---|---|---|
| `R01` | [Agent says a nonzero interval makes an acquisition stoppable](#r01) | HIGH | SMALL |  |
| `R19` | [An unassigned Core.Focus is swallowed, then thrown raw](#r19) | HIGH | SMALL |  |
| `R20` | [Setup cannot declare a stage-position property; absolute-position hole](#r20) | HIGH | MEDIUM |  |
| `R23` | [The unattended Z paths are not lock-aware](#r23) | HIGH | MEDIUM |  |
| `R28` | [laser_slot's pre-flight guarantees less than its schema sells](#r28) | HIGH | SMALL |  |
| `R29` | [A stored rig fact was filed under the wrong topic, then not used](#r29) | HIGH | MEDIUM |  |
| `R31` | [Adaptive+refocus dataset is a dense hypercube with padding frames](#r31) | HIGH | LARGE |  |
| `R40` | [A stitched mosaic's zero padding corrupts every ImageStats statistic](#r40) | HIGH | SMALL |  |
| `R42` | [A model-invented rule overrode an explicit operator instruction](#r42) | HIGH | SMALL |  |
| ~~`R50`~~ | [design/38 F12 - a property write can report failure after succeeding](#r50) | HIGH | SMALL | **72a** |
| ~~`R51`~~ | [design/38 F13 - the agent does not know it can read illumination state](#r51) | HIGH | SMALL | **72a** |
| `R57` | [A full disk is reported as a hardware or connection fault](#r57) | HIGH | SMALL |  |
| ~~`R60`~~ | [A failed hardware write was described as definitely not landed](#r60) | HIGH | SMALL | **72a** |
| `R79` | [Saved knowledge does not separate measurement from inference](#r79) | HIGH | LARGE |  |
| `R02` | [Agent offers to split an acquisition the backend already splits](#r02) | MEDIUM | SMALL |  |
| `R16` | [get_device_property_info errors on a guessed property name without naming the real ones](#r16) | MEDIUM | SMALL |  |
| `R17` | [An aborted turn's error never reaches the transcript](#r17) | MEDIUM | SMALL |  |
| `R21` | [Nothing reports the bounds microclaw is enforcing](#r21) | MEDIUM | SMALL |  |
| `R24` | [Four of five connect sites' actionable refusal never reaches the user](#r24) | MEDIUM | SMALL |  |
| `R25` | [An exported script writes its dataset beside the script, silently](#r25) | MEDIUM | SMALL |  |
| `R26` | [run_tile_acquisition cannot run an artifact-emitting saved hook](#r26) | MEDIUM | SMALL |  |
| `R39` | [Emitted scripts hard-code the positions they visited](#r39) | MEDIUM | MEDIUM |  |
| `R43` | [connected_components writes no visible segmentation overlay](#r43) | MEDIUM | SMALL |  |
| `R49` | [There is no way to remove a saved hook](#r49) | MEDIUM | SMALL |  |
| `R52` | [design/38 H2 - acquisition frame-cap policy is not inspectable](#r52) | MEDIUM | SMALL |  |
| `R54` | [A config can pass check-config with budgets that do not bind](#r54) | MEDIUM | MEDIUM | → Block 12 closeout |
| `R58` | [Clean hook save is not enforced in code](#r58) | MEDIUM | SMALL | → `security/confirm-in-code` (design/11b issues 2+3) |
| `R61` | [run_timelapse declares no artifact](#r61) | MEDIUM | SMALL |  |
| `R65` \* | [Mid-acquisition cancellation and abort trigger](#r65) | MEDIUM | SMALL |  |
| `R66` \* | [Partial-failure and restart/session-ledger rig semantics](#r66) | MEDIUM | SMALL |  |
| `R81` | [run_multiposition_acquisition refuses any non-observation hook](#r81) | MEDIUM | MEDIUM |  |
| `R38` | [Import-stripping check and emitted analysis block can disagree](#r38) | LOW | SMALL |  |
| `R62` \* | [Historical calibration-artifact authoring gap](#r62) | LOW | SMALL |  |
| `R63` \* | [Context-compaction attribution observation](#r63) | LOW | SMALL |  |
| `R74` | [Phase 2 XY typed-actuator ambiguity / proposed axis field](#r74) | LOW | MEDIUM |  |
| `R82` | [Open the community skill package notebook](#r82) | HIGH | LARGE | → `design/71` §"next notebook brief" |
| `R83` | [Generic package/protocol conformance needs a fixture](#r83) | HIGH | MEDIUM |  |
| `R84` | [Three export behaviours are unpinned ahead of a runner](#r84) | HIGH | SMALL |  |
| `R86` | [run_mda bypasses _acquire_with_hooks, so lifecycle events are invisible](#r86) | LOW | MEDIUM |  |
| `R87` | [D5's session-end rule names a field that is absent when nothing is declared](#r87) | MEDIUM | SMALL |  |
| `R88` | [The probe_hint payload reached a live model and did not route it](#r88) | HIGH | MEDIUM | → `design/74` |
| `R89` | [The PFS offset fine-tune is a hand-driven loop with no tool](#r89) | MEDIUM | MEDIUM |  |
| `R90` | [arrival_unverifiable is saturated for every image-autofocus sweep](#r90) | LOW | SMALL |  |
| `R94` | [D4 records acquisitions, so a snap in the Core log still cannot be attributed](#r94) | MEDIUM | SMALL |  |
| `R95` | [Does live-mode ownership churn precede a lost terminal notification?](#r95) | MEDIUM | LARGE |  |

**Demo machine**

| | row | importance | effort | block |
|---|---|---|---|---|
| `R27` | [The agent started live view unprompted on a laser-dose rig](#r27) | HIGH | SMALL |  |
| `R32` | [The emitted standalone script is completely silent](#r32) | HIGH | SMALL |  |
| `R46` | [Block 13 position-list rollback path unexercised on a rig](#r46) | HIGH | SMALL | → Block 13 |
| `R05` | [Focus-lock ordering rule not followed on the Nikon Ti](#r05) | MEDIUM | MEDIUM |  |
| `R12` | [An agent asked the operator for positions it could have read itself](#r12) | MEDIUM | SMALL |  |
| `R13` | [Batched analyzer cannot batch a one-dataset-per-position survey](#r13) | MEDIUM | MEDIUM |  |
| `R14` | [An exported adaptive survey prints nothing](#r14) | MEDIUM | SMALL |  |
| `R69` \* | [Shipped context thresholds are unexercised](#r69) | MEDIUM | MEDIUM |  |
| `R72` \* | [The model has never been observed saying what live view costs before starting one](#r72) | MEDIUM | SMALL |  |
| `R03` | [design/65 carried row 1 - a hook choosing genuinely different targets per frame has never run on a rig](#r03) | LOW | SMALL |  |
| `R47` | [saturated_fraction reporting precision is ungated](#r47) | LOW | SMALL |  |
| `R71` | [open_artifact's multi-channel caveat has never been checked on any rig](#r71) | LOW | SMALL |  |
| `R75` | [Channel preset colliding with a typed actuator - rig coverage](#r75) | LOW | SMALL |  |

**M2**

| | row | importance | effort | block |
|---|---|---|---|---|
| `R80` | [Detect cross-plane non-response for sub-band Z steps](#r80) | HIGH | MEDIUM |  |

**M5**

| | row | importance | effort | block |
|---|---|---|---|---|
| `R48` | [filament_position_filter scores bead fields as filamentous](#r48) | HIGH | SMALL |  |
| `R53` | [design/38 F9 - per-source illumination prerequisites](#r53) | HIGH | MEDIUM |  |
| `R67` | [UV activation closed-loop test](#r67) | HIGH | LARGE |  |
| `R56` | [Block 9b cross-rig inventory gate, four limbs](#r56) | MEDIUM | MEDIUM | → Block 9b cross-rig inventory gate |
| `R09` | [M5's empty Core shutter](#r09) | LOW | SMALL |  |
| `R68` \* | [Missing NDTiff fixtures](#r68) | LOW | SMALL |  |

**Zeiss**

| | row | importance | effort | block |
|---|---|---|---|---|
| `R06` | [probe_hint emitted for a lock with no usable status property](#r06) | MEDIUM | SMALL |  |
| `R45` | [Block 13 G3 - transmitted-light SNR refusal unmeasured on any rig](#r45) | LOW | SMALL | → Block 13 G3 |

**Blocked on a Nikon Ti — no reachable machine**

| | row | importance | effort | block |
|---|---|---|---|---|
| `R08` | [Five things owed to a Nikon Ti by design/59](#r08) | HIGH | MEDIUM | → Block 59c |
| `R15` | [Finding the capture band does not offer to engage the lock](#r15) | HIGH | MEDIUM | → Block 59c |
| `R22` | [No typed continuous-focus capability exists](#r22) | HIGH | LARGE | → Block 7a, “Typed continuous focus and the bounded engage search” |
| `R64` \* | [Unconfirmed SignalIO (12) and Galvo (16) bridge type ordinals](#r64) | LOW | SMALL |  |


**Blocked on someone else — not schedulable here**

| | row | importance | effort | block |
|---|---|---|---|---|
| `R85` | [SMAPpy's publisher-owned precondition list](#r85) | MEDIUM | — |  |

---

# The rows

**The queue above is the part that gets read.** What follows is one entry per
carried row, in the same order, each carrying its original register text
verbatim under a fold so that acting on a row needs no second file. Look a row
up; do not read this linearly.


## Settle off-rig — no instrument at all

These need a code change and a test. Nothing here is waiting on hardware; the only reason any of them is still open is that nobody has taken it.


### R01 — Agent says a nonzero interval makes an acquisition stoppable

**Tool guidance can falsely imply that a nonzero frame interval makes an acquisition interruptible by Microclaw.**

- **Status** — OPEN - `microclaw/tools_schema.py:707-713` warns only that zero-interval bursts may resist Stop, still inviting the false converse; design/69a explicitly leaves the finding open.
- **Importance** — HIGH - Misleading interruption advice can cause thousands of unintended exposures, wasting dose, data, and rig time.
- **Where** — LOCAL - The defect is in schema/disclosure wording and agent guidance, so code inspection and off-rig response tests can settle it.
- **Block** — NONE - Block 60 D5 introduced the zero-burst warning but does not disclaim added stoppability at nonzero intervals; Block 12 only requires disposition.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 8923–8946)</summary>

```markdown
### The agent tells operators a nonzero interval makes an acquisition stoppable — added 2026-09-02 **(no block)**

Raised by the operator during design/69a's round-4 demo gate, and out of scope
for that block, which is about delivery rather than about what the agent says.

Across the 69a gate sessions the agent repeatedly proposed a nonzero
`interval_s` on the grounds that it would let the acquisition be stopped
part-way. **The operator states this is false**, and the architecture agrees:
Microclaw cannot stop anything mid-tool — `execute_tool` blocks in Java, and Stop
is cooperative, checked *between* rounds and before each tool, which
`serve.html`'s own Stop copy says outright, citing design/16 §5–§6. Whatever
stopping is possible belongs to Micro-Manager, and it is equally possible at a
0 s interval. The advice is therefore not a rough version of the truth; it
offers a control that does not exist, on the one axis where an operator most
needs to know what can actually be interrupted.

A fix must separate two claims that are not the same: what a nonzero interval
genuinely buys — software runs between exposures, so a per-frame hook action
becomes possible at all, which is the engine contract in `CLAUDE.md` about
hardware-sequenced bursts — and what it does not buy, which is any additional
ability of *Microclaw's* to halt a run. Whoever takes this should find where the
belief comes from (tool descriptions, disclosure text, or the model's own prior)
before writing copy against it.

```

</details>


### R19 — An unassigned Core.Focus is swallowed, then thrown raw

**Microclaw obscures an unassigned Micro-Manager focus-stage role in system state and exposes cryptic Java errors from Z-facing tools instead of explaining the configuration remedy.**

- **Status** — OPEN - `microclaw/tools.py:3956` still reports only `z_stage: "unavailable"`, while `get_z_position` and `move_stage_z` call Core directly at `microclaw/tools.py:3029` and `microclaw/tools.py:3117`; `design/35-usability-and-pfs-checklist.md:8903` confirms the proposed Block 6a fix was dropped unmerged.
- **Importance** — HIGH - the inconsistent, opaque failure has already cost two hardware sessions and prevents safe Z-axis operation.
- **Where** — LOCAL - fake Core objects with an empty focus role can settle every affected error path and message without hardware.
- **Block** — NONE - the checklist explicitly marks it “(no block)” because Block 6a was dropped unmerged.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 9476–9482)</summary>

```markdown
- **An unassigned `Core.Focus` is swallowed, then thrown raw.** `get_system_state`
  turns `No device with label ""` into `z_stage: "unavailable"`, while
  `get_z_position`, `move_stage_z` and autofocus raise the bare Java exception.
  **Two Nikon sessions were lost to it.** Microclaw cannot fix the role — it is a
  device-assignment property it correctly excludes — so the whole fix is naming
  the cause and the remedy. Generic: any MM config with several single-axis
  stages and no role line.
```

</details>


### R20 — Setup cannot declare a stage-position property; absolute-position hole

**Setup cannot authorize raw stage-position properties, while manually declared `absolute-position` properties can bypass the required stage travel policy.**

- **Status** — OPEN - `setup_tools.py:281`–`290` emits no property authorization, and `authorization.py:1035`–`1050` plus `safety.py:1097`–`1111` still allow an unlisted stage to escape any travel-bound check.
- **Importance** — HIGH - A manual typed declaration can silently restore motion on an intentionally unreachable stage, risking unsafe movement and invalid acquisition data.
- **Where** — LOCAL - Fake-core tests can prove setup output, validator rejection, and runtime fail-closed behavior without hardware.
- **Block** — NONE - Block 6a was dropped unmerged, and `design/35-usability-and-pfs-checklist.md:8902` explicitly leaves the re-scoped work without a block.
- **Effort** — MEDIUM

<details><summary>The original row, verbatim (`design/35` lines 9483–9506)</summary>

```markdown
- **Setup cannot declare a stage-position property, and the kind it would use has
  a hole under it.** Two halves, and **the second must land with the first or it
  opens what it closes**. (a) Setup excludes stage-position properties outright
  instead of offering `absolute-position`; the kind exists, `safety.py:1039`–`1052`
  routes it through `check_named_stage`, and `authorization.py:1012` already
  refuses a typed axis entry whose bounds widen the named-stage entry rather than
  narrowing it. (b) That routing reaches a travel bound **only** when the device
  is the core focus device, the core XY device, or already in `named_stages` — on
  any other stage every branch falls through and the write is gated by nothing but
  its own declared min/max in `check_typed_actuator`, while `check_named_stage`
  (`safety.py:1234`) fails closed on that same device. **M2 is the shape that
  breaks**: its `named_stages: []` is a deliberate refusal
  (`design/29-block9-m2-safety-config.yaml:116`–`123`), and a typed entry would
  quietly reinstate motion on a stage the operator declared unreachable. Net
  effect of doing both: stricter than today everywhere, except the PFS offset it
  unblocks. **Re-scope required**: 6a wrote this against `first_launch.py`, which
  `7edd76a` deleted; the surface is now `setup_tools.py`, and the exclusion above
  is described as 6a measured it in 2026-08-05's setup code — **confirm it against
  `setup_tools.py` before believing it.** 6a also recorded a third half, that the
  runtime refusal naming `allowed_numeric` as a legal home for a stage-position
  pair contradicted what setup's own generated comment said; the refusal was
  judged right and setup wrong. **That one is unverified here** — it was a claim
  about a deleted module, and nobody has re-checked what `setup_tools.py` writes.

```

</details>


### R23 — The unattended Z paths are not lock-aware

**Unattended autofocus, recovery, and tile Z moves can fight an engaged hardware focus lock instead of refusing safely.**

- **Status** — OPEN - Bare, lock-unaware writes remain in `autofocus.py:357,388,422`, `hooks.py:388`, and `tools.py:6830`; design/35 block 7b explicitly closed unbuilt and carried this gap forward.
- **Importance** — HIGH - Competing with the focus servo can waste dose and produce misleadingly focused or scientifically invalid images.
- **Where** — LOCAL - Existing focus-lock queries can be faked to verify that every path refuses before moving; no hardware semantics need establishing.
- **Block** — NONE - Block 7b is explicitly a closed historical record rather than an active implementation block.
- **Effort** — MEDIUM

<details><summary>The original row, verbatim (`design/35` lines 9532–9542)</summary>

```markdown
- **The unattended Z paths are not lock-aware.** `run_autofocus` itself already
  refuses to sweep against an engaged lock (`tools.py:4134`) — 7b's headline item
  landed elsewhere — but the sweep, the move-to-best and the `_restore` in
  `autofocus.py` (`:102`, `:121`, `:142`/`:159`), the focus-recovery jog in
  `hooks.py` (`:384`) and the per-position Z of the tile path are not. **Those
  five line numbers are 6a-era pointers re-located by symbol, not re-audited** —
  find the call sites by name. All are already inside a
  `check_z`-guarded range; the gap is lock awareness, not bounds. **Do not offer
  a `preserve` mode** — no evidence supports a movement path that keeps the servo
  searching, and offering it would be a claim the rig has not made.

```

</details>


### R28 — laser_slot's pre-flight guarantees less than its schema sells

**`run_timelapse` overstates `laser_slot` pre-flight as protection from blank frames even though it checks only trigger mode and sequence, not whether the laser is enabled.**

- **Status** — OPEN - `microclaw/tools_schema.py:691` still promises protection from a gated-off laser, while `microclaw/tools.py:4576` explicitly leaves device enables and emission paths unverified.
- **Importance** — HIGH - The misleading guarantee can allow a disabled laser to produce a complete but scientifically blank acquisition, wasting rig time and potentially other illumination dose.
- **Where** — LOCAL - The contract mismatch is fully established from code and can be closed by narrowing and testing the schema wording without hardware.
- **Block** — NONE - `design/44-two-channel-search-and-acquire.md:161` acknowledges the limitation and works around it with channel-plan verification, but does not own a general fix.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 9731–9745)</summary>

```markdown
- **`laser_slot`'s pre-flight guarantees less than its schema sells.** Found by
  43j's M5 round 2, 2026-08-11, by an operator mis-step worth more than the step
  it was aiming at: asked to gate the trigger off, they first turned the *laser*
  off, and the 200-frame acquisition ran to completion with
  `trigger_preflight: trigger line is armed`. That is correct — the pre-flight's
  own payload says `not_verified: ["device-level enables", "illumination
  properties", "emission path"]` — but `run_timelapse`'s schema description sells
  `laser_slot` as the thing that stops "a gated-off laser silently produc[ing]
  blank frames", and a *disabled* laser produces exactly blank frames and passes.
  The agent named the gap itself: *"the pre-flight only ever checked trigger
  mode — it never confirmed enable"*. Two candidate fixes, and the cheap one may
  be enough: narrow the schema sentence to what is actually checked, or have the
  pre-flight read the slot's enable line too and report it beside the trigger.
  **Pre-existing, not 43j's** — the fold moved none of this code.

```

</details>


### R29 — A stored rig fact was filed under the wrong topic, then not used

**A camera-trigger/laser fact can be stored under the wrong rig topic and then fail to guide laser-state and dose decisions at the point of use.**

- **Status** — OPEN - Topic validation checks only the outer key (`microclaw/knowledge_manager.py:10`), while `start_live_view` returns no stored trigger fact (`microclaw/tools.py:2749`); no later closure is recorded.
- **Importance** — HIGH - Ignoring or misclassifying this fact can misrepresent laser emission and cause unreported sample dose.
- **Where** — LOCAL - Wrong-topic validation and point-of-use propagation are deterministic knowledge/schema behavior testable with stored YAML and mocked controllers.
- **Block** — NONE - The register leaves this finding with design/43 F1/F3 ownership but assigns no implementation block (`design/35-usability-and-pfs-checklist.md:9755`).
- **Effort** — MEDIUM

<details><summary>The original row, verbatim (`design/35` lines 9746–9761)</summary>

```markdown
- **A stored rig fact was filed under the wrong topic, and then not used.** M5,
  43j's Step 7a session, 2026-08-11. The operator: *"why did you ask me about the
  laser enable when we have `camera_triggers_lasers: true` in our knowledge base
  under `illuminated_field`?"* — two problems in one sentence. 43f defined
  `camera_triggers_lasers` as an `illumination_path` fact; under
  `illuminated_field` it still renders in the knowledge-base block but stops
  being the thing `illumination_path` answers, and `illuminated_field`'s reach
  into `get_roi` now carries an illumination-path fact. And the agent queried
  `Laser 1: 1. Enable` and `2. Emission` directly rather than reading the stored
  fact at all. **This is design/43 F1's vocabulary and F3's rule, not 43j's**;
  43f's own gate had this fact under `illumination_path`, so establish when and
  how it moved before writing any fix. Related: in the same session the agent
  over-fitted an explanation to a dark frame — steering toward a trigger-source
  change when the cause was that no sample was on the stage — and said so
  plainly when challenged, which is the right recovery and not a defect.

```

</details>


### R31 — Adaptive+refocus dataset is a dense hypercube with padding frames

**Adaptive refocus exports invent black padding frames, while two-phase search/acquire datasets omit channel provenance, making stored frames easy to misinterpret.**

- **Status** — OPEN - `microclaw/tools.py:4881` deliberately zero-fills missing Cartesian slots, and `microclaw/tools.py:8657` plus `:8702` still build both phase datasets with `channel=None`.
- **Importance** — HIGH - Readers can mistake nonexistent exposures for real dark data and cannot determine acquisition channels from either dataset alone, risking wrong scientific interpretation.
- **Where** — LOCAL - Synthetic sparse NDTiff fixtures and off-rig tests can establish the storage, export, and provenance contracts without hardware.
- **Block** — NONE - Block 42b deliberately tabled reconstructed axis structure, while design/52 only documents this sparse-axis hazard rather than owning a fix.
- **Effort** — LARGE

<details><summary>The original row, verbatim (`design/35` lines 9793–9803)</summary>

```markdown
- **An adaptive+refocus dataset is a dense hypercube with real padding frames,
  and nothing tells the reader.** M5 2026-08-12: the search dataset is 3 positions
  x 2 refocus = 6 array slots for **5 real frames**, and `field_1 refocus=1` is
  entirely zeros because that tile never refocused. A reader iterating the array
  shape gets a black frame that was never acquired; only the NDTiff index says
  which slots are real. This is design/28 F3's dense-hypercube trap in a new
  place, and it surfaced because an operator could not map TIFF frames to
  positions at all. **Compounding it: `acquire_on_hit` datasets carry no channel
  axis** — channel is a phase setting, so search and acquire land in separate
  directories and neither records which channel it was; the only channel evidence
  is the directory name and the audit. Not scheduled.
```

</details>


### R40 — A stitched mosaic's zero padding corrupts every ImageStats statistic

**Zero-filled uncovered pixels in stitched mosaics silently distort all frame-wide `ImageStats`, including SNR, focus, intensity, saturation, and coverage metrics.**

- **Status** — OPEN - `microclaw/dataset_mosaic.py:105-106` still creates a zero-filled canvas and coverage map, while `microclaw/image_analysis.py:331-359` computes every statistic over the entire image; the limitation is explicit at `microclaw/image_analysis.py:153-159`.
- **Importance** — HIGH - It can silently report materially wrong scientific measurements and rankings for valid mosaics.
- **Where** — LOCAL - Synthetic mosaics with known covered-region statistics and padding fractions can settle the behavior without hardware.
- **Block** — NONE - `design/35-usability-and-pfs-checklist.md:10093-10105` requests a dedicated future block, but no existing numbered block owns it.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10093–10105)</summary>

```markdown
- **A stitched mosaic's zero padding corrupts every statistic in
  `ImageStats`, not just coverage.** From 43g's round-2 review, 2026-08-10, and
  confirmed by the implementer's own sweep. `dataset_mosaic.py:105` allocates the
  canvas with `np.zeros(...)`, which is a reasonable thing for it to do — but the
  padding then enters the frame-wide median and MAD that `snr`, `focus_metric`
  and the coverage statistics are all built on. Measured on 256² sCMOS tiles over
  a 1024² canvas, the degradation is smooth and silent: at 38% padding MAD reads
  18 against a true 3.0 and coverage is wrong by 2.7×, with no degenerate branch
  firing; `focus_metric` falls 1425.64 → 677.56 at 75% padding. **Pre-existing,
  and not 43g's to fix.** The mosaic already keeps a `coverage_count` array
  beside the canvas, so measuring over the covered region is the answer. Size it
  as its own block with its own gate; do not add a mask parameter to
  `coverage_stats`.
```

</details>


### R42 — A model-invented rule overrode an explicit operator instruction

**The agent can mistake prompt-induced caution for an enforced safety rule, override an explicit operator command, and silently perform extra hardware writes.**

- **Status** — OPEN - The ratchet still says “Step up gradually” at microclaw/safety.py:1255, while the only operator-override guidance is limited to contradictory telemetry at microclaw/agent.py:150; design/35-usability-and-pfs-checklist.md:10130 still calls for the prompt fix.
- **Importance** — HIGH - It can spend dose through unauthorized extra writes and waste rig time by preventing the requested limit test.
- **Where** — LOCAL - The distinction is prompt-shaped and can be settled with prompt inspection and off-rig model/tool tests.
- **Block** — NONE - Block 43c explicitly carried it forward outside that block, and Block 12 only requires a disposition for unscheduled rows.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10121–10132)</summary>

```markdown
- **A model-invented rule must not override an explicit operator instruction.**
  From 43c's M5 round 2, 2026-08-09. Asked to step laser power from 1% to 50% in
  a single write — explicitly, three times, while saying they were testing a
  tool limit — the agent refused and silently substituted its own ramp
  (1→5→20→50, then 1→10→25→50), calling the gradual step-up *"a safety rule I
  follow … not a limitation I can waive just because it was requested."* It is
  not a rule in the code; it is a habit the ratchet's own *"Step up gradually"*
  message teaches. Two costs: one authorized write became three unauthorized
  ones, and the operator could not reach the limit they were trying to observe.
  The agent diagnosed it correctly once challenged. Fix is prompt-shaped —
  distinguish a guard that exists from a habit, and say so when declining — and
  it belongs with F2's authority questions rather than inside a gated block.
```

</details>


### R50 — design/38 F12 - a property write can report failure after succeeding

**A property write can take effect despite raising an exception, leaving the operator with a dangerously false belief about hardware state.**

- **Status** — **CLOSED by block 72a, merged 2026-09-02.** All three sites read the value back on the exception path only — `set_device_property`, `set_emu_laser_power_percentage`, and the channel executor — and report `landed` / `requested_value_not_observed` / `unknown`, or, in the executor, a fourth refinement `at_original` against its saved originals. The `write_reported_failure_but_value_changed` token the row asked for is emitted by the two tools, and design/72 D6 stops the exported script pairing it with "the session completed nothing here". Nothing is added to any success path (design/53's rule stands: verification is plan-level). Settled off-rig by fourteen tests, thirteen of them watched failing on the pre-fix tree.
- **Importance** — HIGH - A misleading failure report can leave illumination enabled, cause extra dose, or prompt an unsafe retry.
- **Where** — LOCAL - A fake core can apply the value and then raise, fully testing exception-time read-back and reporting without hardware.
- **Block** — **72a** (design/72). Block 7b's rule for hook ratcheting is now the rule everywhere.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10257–10266)</summary>

```markdown
- **design/38 F12 — a property write can report failure after it has succeeded.**
  On M5 G7.a, `set_device_property` on `All: 3. TTL Enable` raised
  `Serial timeout occurred. (17)`; the read-back showed the value had landed. The
  agent caught it, nothing in microclaw made it. Same shape as the
  failed-write-that-landed defect Block 7b's gate caught, so it recurs. Worst for
  illumination: an operator told a laser-enable failed may believe the laser is
  off when it is on, or retry and double-apply. Candidate fix — read back on a
  write exception and report `write_reported_failure_but_value_changed` with both
  values, rather than surfacing the raw exception.

```

</details>


### R51 — design/38 F13 - the agent does not know it can read illumination state

**The agent knows to read declared illumination after a bad frame, but not when ending or handing off a session.**

- **Status** — **CLOSED by block 72a, merged 2026-09-02.** `agent.py`'s read bullet now carries the session-end and handoff rule, and the certainty bullet says a landed-write report is not a report that the write failed. **Measured on the demo machine, 2026-09-02**, with the pre-fix prompt as a control arm on the same two operator messages: the branch said *"Before I wrap up, let me verify the final illumination state rather than assume it"* and called `get_system_state`; the control reported the LED off with **no tool call**, from its own earlier writes. True, and inferred — which is the row's own complaint. **n=1 per arm**: this establishes that the change discriminates, not how often it fires.
- **Importance** — HIGH - The agent can falsely disclaim access to hardware state during handoff, potentially leaving the operator unaware that declared illumination remains on.
- **Where** — LOCAL - This is a prompt-routing defect settleable with prompt inspection and an off-rig agent test.
- **Block** — **72a** (design/72 D5).
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10267–10272)</summary>

```markdown
- **design/38 F13 — the agent does not know it can read illumination state.** On
  exit in G7.c it told the operator "I can't confirm the illumination state on my
  own"; `get_system_state` now returns `declared_illumination_properties`. The
  round-4 prompt teaches it to consult that after a blank or low-signal frame,
  but not at session end or handoff. One prompt line.

```

</details>


### R57 — A full disk is reported as a hardware or connection fault

**Disk exhaustion can masquerade as a microscope fault, and acquisitions are not rejected when their estimated size exceeds available storage.**

- **Status** — PARTLY - Block 60a now surfaces notification-thread failures as structured acquisition errors, but `microclaw/errors.py:202` still gives unclassified errors `_HARDWARE_HINT`, and no acquisition path checks filesystem free space.
- **Importance** — HIGH - It misdirects operators toward hardware diagnosis and can spend acquisition time and dose before storage failure.
- **Where** — LOCAL - Error classification and free-space preflight can be settled with filesystem mocks and off-rig acquisition tests.
- **Block** — NONE - Block 60a/60b handled bounded teardown and NDTiff rollover disclosure, not free-space validation or general storage-error taxonomy.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10368–10379)</summary>

```markdown
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
```

</details>


### R60 — A failed hardware write was described as definitely not landed

**A hardware-write exception is wrongly treated as proof that the requested state did not land, instead of leaving state unknown pending verification or cleanup.**

- **Status** — **CLOSED by block 72a, merged 2026-09-02, in two halves and only one of them is this block's.** The *agent* half was already closed by `agent.py:137`'s certainty rule before design/72 was written, and is struck with that reason rather than a tick. The *code* half is 72a: `NO WRITE REACHED THE DEVICE, so no channel change was made` is gone, and no branch now concludes that the plan changed nothing. The `landed = index < accepted` selector and the quiet `unrestored` category are **deleted**, not renamed — with `at_original` skipping the redundant restore, every restore failure that can still occur sits on an entry that did or may have landed, so all of them are loud. This does **not** reinstate the M5 2026-08-06 defect: that device answers reads with its saved original, so the doomed second write is never made.
- **Importance** — HIGH - False certainty about laser or shutter state can expose a sample, mislabel acquired data, and leave hazardous illumination active.
- **Where** — LOCAL - A fake write that mutates hardware state and then raises can prove the required failure semantics without a rig.
- **Block** — **72a** (design/72 D4). Block 52b's one hook path is now the rule at every site.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10388–10395)</summary>

```markdown
- **A failed hardware write was described as definitely not landed.** Block 2's
  M5 G4 first enable returned iChrome serial timeout 17; the agent then said the
  laser "was not enabled." That conclusion was unjustified: a write that raises
  may have landed, so state is unknown until read-back or cleanup. The separately
  confirmed retry later read `1`, and `serve` cleanup independently returned all
  five shutters to `0`, so Block 2's gate remains green. Fix the failure wording
  and require state verification in the appropriate tool/agent safety follow-up;
  do not fold it silently into Block 3's policy-refusal taxonomy.
```

</details>


### R79 — Saved knowledge does not separate measurement from inference

**Persistent knowledge records do not distinguish direct observations from model conclusions, so later sessions may trust unsupported inferences as facts.**

- **Status** — OPEN - `save_knowledge` still stores the caller’s unrestricted value unchanged (`microclaw/tools.py:10003`), and prompt rendering labels all entries as stored data (`microclaw/knowledge_manager.py:187`–`204`); design/40 D6 remains explicitly unscheduled (`design/35-usability-and-pfs-checklist.md:8906`).
- **Importance** — HIGH - Unsupported conclusions have already caused valid operator instructions to be refused and can misrepresent hardware or sample state (`design/40-pfs-five-sessions.md:177`–`190`).
- **Where** — LOCAL - The defect is in the knowledge schema, persistence, and prompt rendering and can be settled with off-rig tests.
- **Block** — NONE - Design/40 D6 explicitly says the provenance shape remains undecided and has no block (`design/40-pfs-five-sessions.md:267`–`271`).
- **Effort** — LARGE

<details><summary>The original row, verbatim (`design/35` register table)</summary>

```markdown
This row comes from the register's table "Absorbed into a block above". Verbatim row:

| Saved knowledge does not separate measurement from inference | design/40 D6 | **(no block)** — owed, shape not yet clear |
```

</details>


### R02 — Agent offers to split an acquisition the backend already splits

**The rollover disclosure wrongly advises splitting an acquisition even though NDTiff automatically creates additional files.**

- **Status** — OPEN - `microclaw/tools.py:2583` still says “Consider segmenting the acquisition,” and `tests/test_acquisition_budgets.py:201` only verifies rollover disclosure.
- **Importance** — MEDIUM - It can mislead the agent into shortening or fragmenting a valid acquisition, but does not itself alter hardware or data.
- **Where** — LOCAL - The defect is deterministic prompt wording and can be settled with code inspection and off-rig tests.
- **Block** — NONE - `design/35-usability-and-pfs-checklist.md:8947` explicitly leaves it unscheduled, while design/69a records it as out of scope.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 8947–8964)</summary>

```markdown
### The agent offers to split an acquisition that the backend already splits — added 2026-09-02 **(no block)**

Same session, same operator, also out of scope for design/69a.

The 100k-frame disclosure offers to segment the run into 7,937-frame chunks to
keep each NDTiff file whole, and in round 4 the agent acted on its own offer and
requested a 7,937-frame acquisition. **NDTiff already rolls to a second file by
itself**; design/60 established that the rollover is routine and that the
disclosure exists to *disclose* it, not to invite a workaround. Offering the
split presents ordinary storage behaviour as a problem the operator must decide
about — the shape `CLAUDE.md` warns against under confirmations, one step
earlier: information dressed as a decision.

The disclosure's segmentation arithmetic is not itself wrong and its overhead
figure is measured. What is wrong is offering it as a remedy. Anyone taking this
row should check whether the model reads the text as advice because it is
*phrased* as advice.

```

</details>


### R16 — get_device_property_info errors on a guessed property name without naming the real ones

**A mistyped property passed to `get_device_property_info` raises the bridge error without listing the device’s valid property names.**

- **Status** — OPEN - `microclaw/tools.py:3595` still queries the guessed property directly with no catch or diagnostic enrichment; the only later register mention remains the open row at `design/35-usability-and-pfs-checklist.md:9392`.
- **Importance** — MEDIUM - It is a genuine discovery/usability failure that causes repeated guessing, but `list_device_properties` provides a safe workaround and no hardware state or data is changed.
- **Where** — LOCAL - Fakes can reproduce the invalid-property exception and verify that the enriched refusal enumerates actual properties.
- **Block** — NONE - The register explicitly marks this as “no block,” and no later design block claims the diagnostic.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 9375–9400)</summary>

```markdown
### Two from the Dragonfly session — added 2026-08-23 **(no block)**

Both measured on the Nikon Ti2-E / Andor Dragonfly
(`pfs-dragonfly-design56ab/`). **Neither is fixed, and neither can be tested
where the operator still has access** — both need a rig with a hardware focus
lock, which M2 and M5 are not. Do not fix them blind; this repo has already
spent three rig trips on a mechanism that was green off-rig the whole time.

**1. Finding the capture band does not offer to engage the lock.** The operator
asked twice, in two different sessions: *"Why didn't you engage the PFS when you
found it?"* `run_autofocus` deliberately does not engage — that is a hardware
write and belongs to the caller — but the plan the model proposes should carry
"sweep, engage, verify" as one procedure, since engaging is what confirms the
plane at zero dose (design/56 §8). Prompt text alone has twice failed to move
this behaviour (§9e), so a fix here should be judged against that history rather
than assumed to work.

**2. `get_device_property_info` on a stage's guessed property name errors
without naming the real ones.** Measured: `PFSOffset` / `Position` returned
`Invalid property name encountered: Position (2)`, while `get_stage_position` on
the same device works. The error is *correct* — that device has no `Position`
property — but it leaves the caller to guess again. Listing the device's actual
property names in that refusal is generic, cheap, and would help on any rig.
Testable off-rig with fakes; the reason it is parked is that it touches a shared
error path and this merge is already carrying unverified change.

```

</details>


### R17 — An aborted turn's error never reaches the transcript

**Failed agent turns appear in the browser stream but leave no durable error record in the transcript.**

- **Status** — OPEN - `microclaw/webserve.py:989-994` still emits exceptions only to SSE, while `microclaw/conversation.py:77-112` still reloads every JSONL object without role validation.
- **Importance** — MEDIUM - This loses useful failure evidence, but the browser displays the error and the operator can record it manually.
- **Where** — LOCAL - A mocked agent exception and audit/reload assertions can settle the behavior without hardware.
- **Block** — NONE - No later design block claims or closes this transcript gap.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 9401–9425)</summary>

```markdown
### An aborted turn's error never reaches the transcript — added 2026-08-23 **(no block)**

Measured during the design/56 gate, on `microclaw serve`. The first prompt hit a
400 from the Messages API; the browser showed it, and the saved JSONL contained
**the user's prompt and nothing else**. The operator had to retype the error by
hand to report it.

`webserve.py`'s turn worker catches every exception and `emit`s it to the SSE
stream only — the `finally` block's comment, "AuditLog has already appended and
flushed every message", is true of messages the agent loop produced and silently
untrue of a turn that produced none. So the one class of event a rig operator
most needs recorded is the one class that is not.

**Deliberately not made a block** (operator ruling, 2026-08-23: out of scope for
design/56). Recorded here so it is not rediscovered.

**What makes it more than a one-liner, for whoever does take it:**
`ConversationStore.append` writes to the audit JSONL and *not* to the model
context, so an error record is safe from the "synthetic assistant turn" angle.
But `load_history` does not validate `role`, so a record with an invented role
would be replayed straight to the API by any resume path. Pick the shape with
that in mind; the transcript is this project's evidence channel and a fix that
corrupts it is worse than the gap.


```

</details>


### R21 — Nothing reports the bounds microclaw is enforcing

**Microclaw exposes configured safety bounds only indirectly through refusals, so users cannot inspect the limits governing valid operations.**

- **Status** — OPEN - `get_stage_position` returns only the position (`microclaw/tools.py:3187`), while `get_system_state` reports named-stage positions and only emits a bound when already violated (`microclaw/tools.py:3963`); no later design block closes this.
- **Importance** — MEDIUM - Bounds are enforced, but discovering them requires reading YAML manually or provoking a refusal, which can invalidate workflows and waste rig time.
- **Where** — LOCAL - Config-bound reporting is fully settleable with off-rig tool tests and synthetic safety constraints.
- **Block** — NONE - Block 57a changed missing-bound refusal behavior, not introspection of configured bounds.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 9507–9519)</summary>

```markdown
- **Nothing reports the bounds microclaw is enforcing.** Found 2026-08-19 during
  block 56's Nikon gate, and it is not that block's. A gate step asked the agent
  to report the configured `named_stages` bound for `TIPFSOffset`; **no call for
  it appears anywhere in the session**, because no tool exposes safety-config
  limits. The operator read `safety_config.yaml` by hand. Today the only way to
  learn a bound is to **provoke a refusal** and read the limit out of the error
  text, which means discovering your own configuration by tripping over it. This
  is a usability row, not a safety one — the bounds are enforced correctly — but
  it also silently voided a written gate step, which is the same shape as a
  placeholder that cannot run.

Two smaller Track B remnants, recorded so they are not re-discovered:

```

</details>


### R24 — Four of five connect sites' actionable refusal never reaches the user

**Four startup paths let an absent Micro-Manager bridge raise during controller construction, burying their intended actionable refusal beneath tracebacks.**

- **Status** — OPEN - `MicroscopeController.__init__` still constructs the bridge eagerly (`microclaw/controller.py:730`), while four callers remain unguarded, including `microclaw/__main__.py:152` and `microclaw/webserve.py:322`.
- **Importance** — MEDIUM - It is a genuine startup usability and diagnostics failure, but starting Micro-Manager first is a straightforward workaround and no hardware state or data is endangered.
- **Where** — LOCAL - Constructor failures can be injected and all five command paths verified with off-rig tests.
- **Block** — NONE - The register still labels this finding unscheduled, and no later design block claims the shared connection handling.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 9549–9570)</summary>

```markdown
- **Four of the five connect sites write an actionable refusal that the common
  failure never reaches.** Found 2026-08-17 on M5, while capturing block 52b's
  precheck with Micro-Manager not running. `MicroscopeController.__init__`
  constructs `Core(port=...)` eagerly (`controller.py:288`), which **raises** when
  the bridge is absent, and `is_connected()` swallows exceptions
  (`controller.py:314`) so it can only report false on a connection that was built
  and then lost. Every site that constructs bare and then checks `is_connected()`
  therefore prints a traceback instead of its own sentence:
  `__main__.py:148` (the interactive CLI), `__main__.py:302`
  (`authorization-map`), `webserve.py:309` (`serve`) and `webserve.py:476`
  (setup mode). **Only `inspect_rig` (`__main__.py:331`) wraps the constructor**,
  and it is the only one that produced the intended one-line refusal on the same
  machine in the same minute — the two commands ran back to back, one printing
  *"Could not connect to Micro-Manager. Is the ZMQ server enabled in Tools ->
  Options?"* and the other ~60 lines of two interleaved thread tracebacks with
  the actionable sentence last. The bridge socket thread's own
  `RuntimeError: cannot join current thread` is pyjavaz noise on the same stderr,
  not microclaw's, and is not worth chasing. The fix is one construct-or-exit
  helper replacing five copies of the same four lines, which is smaller than what
  is there now; it is unscheduled because nothing depends on it, and it is
  **not** design/52's — recorded here so a failed precheck does not lose it.

```

</details>


### R25 — An exported script writes its dataset beside the script, silently

**Exported acquisition scripts write new datasets beside themselves instead of the session’s recorded data directory, which surprises users and can obscure where data went.**

- **Status** — OPEN - `microclaw/tools.py:264` still emits `directory=str(_HERE)`, and `tests/test_session_script_export.py:1507` explicitly requires ignoring the recorded directory; block 52a added path reporting only.
- **Importance** — MEDIUM - Data remains intact, but unexpected placement and inconsistent disclosure can mislead operators and waste troubleshooting time.
- **Where** — LOCAL - The output-path policy and all emitter variants can be settled with code review and off-rig execution tests.
- **Block** — NONE - No later written block owns changing exported dataset placement.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 9571–9585)</summary>

```markdown
- **An exported script writes its dataset beside the script, and says nothing
  about where.** Found 2026-08-15 on M2 during block 52a's gate; **pre-existing
  and not that block's**, since `directory=str(_HERE)` is how every emitted
  script has always resolved its output. The operator reasonably concluded the
  standalone had overwritten the session's data. It had not — microclaw wrote
  `F:\DataSSD\...\52a_m2_sweep_1` and the script wrote
  `AppData\Local\microclaw\52a_m2_sweep_1`, both 18 frames, both intact — but
  nothing in the run told them so, because the script prints its approval
  envelope and then nothing. 52a fixes only the silence, by printing the dataset
  path. **Where an emitted script should write is the open question**: beside the
  script is surprising when the session's own `save_dir` was a data drive, and
  the exported plan already records that path. Note the asymmetry that made this
  visible: the emitted hook log goes through `_next_available_log_path` and is
  protected from collision, while the dataset name is not.

```

</details>


### R26 — run_tile_acquisition cannot run an artifact-emitting saved hook

**The tile-acquisition wrapper cannot accept or forward the artifact and illumination limits required by artifact-emitting saved hooks.**

- **Status** — OPEN - `microclaw/tools.py:7154` still omits both arguments and `microclaw/tools.py:7214` forwards neither, while multiposition accepts them at `microclaw/tools.py:6928`.
- **Importance** — MEDIUM - It blocks a legitimate tile-hook workflow and gives an unreachable remedy, but callers can use multiposition directly.
- **Where** — LOCAL - Signatures, forwarding, and pre-exposure refusal behavior are fully testable with fakes.
- **Block** — NONE - Design/52 explicitly excluded this defect and left it in the open register (`design/52-approved-hook-property-actions.md:625`).
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 9586–9601)</summary>

```markdown
- **`run_tile_acquisition` cannot run an artifact-emitting saved hook, and its
  refusal names a remedy the caller cannot reach.** Found 2026-08-15 while
  scoping `design/52`; **pre-existing, not that design's, and deliberately kept
  out of it.** `_configure_hook_capabilities` refuses a hook whose
  `can_emit_artifacts` is true when no `artifact_limits` was configured, telling
  the caller to pass one — but `run_tile_acquisition` (`tools.py:4567`) accepts
  neither `artifact_limits` nor `illumination_envelope`, and forwards only
  `hook_strategy`, `hook_params` and `log_path` into
  `run_multiposition_acquisition` (`tools.py:4623`), which does accept both
  (`tools.py:4369`). So the grid route is the one place a registered
  artifact-emitting hook is unusable, and the error tells the operator to do
  something the tool has no argument for. Same shape as this register's other
  unreachable-remedy rows. The fix is plausibly two forwarded keyword arguments,
  but it is unmeasured on a rig and nothing currently depends on it — **do not
  assume tile illumination or artifact behavior exists when writing tests; an
  assertion about it passes vacuously.**
```

</details>


### R39 — Emitted scripts hard-code the positions they visited

**Exported adaptive scripts embed the original XY coordinates instead of offering an explicit reusable Micro-Manager position-list mode.**

- **Status** — OPEN - `_emit_adaptive` still inserts literal `xy_positions` into the emitted event shape at microclaw/tools.py:1553 and microclaw/tools.py:1593; design/35-usability-and-pfs-checklist.md:10030 says it was not folded into block 43h.
- **Importance** — MEDIUM - This is a genuine reuse limitation with an obvious coordinate-editing workaround; making runtime position-list lookup explicit avoids silently imaging the wrong region.
- **Where** — LOCAL - Export rendering, option semantics, and position-list reads can be established with off-rig fakes and script-execution tests.
- **Block** — NONE - The carried-forward section explicitly records it as not folded into block 43h, and no later written block claims it.
- **Effort** — MEDIUM

<details><summary>The original row, verbatim (`design/35` lines 10023–10044)</summary>

```markdown
- **Emitted scripts hard-code the positions they visited; they should be able to
  read the stage position list.** Operator finding, M5 gate 2026-08-11:
  *"the positions are recorded explicitly. It should be possible to get positions
  from the stage position list in the script. This would make it easier to re-use
  the script in other places."* `_emit_adaptive` renders `xy_positions` as
  literal coordinates, which is *faithful* — it is what ran — but pins the script
  to one region of one coverslip.
  **Not folded into 43h**: it changes what the emitted program means, and 43h's
  M5 evidence was gathered against the literal form, so slipping it in would
  invalidate a gate that has already passed.
  Design lean, for a decision rather than a default. Two levels, and they are not
  the same thing. **(a) Hoist the coordinates into a named constant** at the top
  of the script with a comment saying what it is — most of the reuse benefit,
  zero change in meaning, and the operator edits one obvious block. **(b) Read
  Micro-Manager's position list at run time** — more powerful and what the
  operator asked for, but it changes the artifact from "image these coordinates"
  to "image whatever is in the list", so a rerun after someone edits the list
  silently images somewhere else. That is F14's trace-versus-program distinction
  arriving from the other side, and it argues the run-time read should be an
  explicit choice at export rather than the default. Ship (a) as the default and
  (b) as a documented option, or make it an export argument; do not make (b)
  silent.
```

</details>


### R43 — connected_components writes no visible segmentation overlay

**The built-in connected-components analysis reports geometry numerically but produces no label map or outline image for visual verification.**

- **Status** — OPEN - `ConnectedComponents.analyze_saved_frame` still returns only numeric results (`microclaw/completed_dataset.py:59-71`), and the later design summary says a visual artifact remains owed (`design/43-nestor-session-findings.md:1702-1708`).
- **Importance** — MEDIUM - This is a genuine usability and verification gap, but operators can work around it with a Fiji overlay.
- **Where** — LOCAL - Synthetic mosaics can verify artifact generation, geometry, and output placement without microscope hardware.
- **Block** — NONE - Block 43e explicitly left this as a successor, while block 43g addressed calibration rather than visualization.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10133–10145)</summary>

```markdown
- **A measurement you cannot see is half a measurement** (design/43 F15's
  follow-on, from 43e's M5 round 2, 2026-08-09). `connected_components` reports
  areas, stage centroids and bounding boxes as numbers and writes only the plain
  mosaic, so the operator said *"I don't see any segmentation draw on the
  image"*. Microclaw diagnosed it correctly — *"it does not produce a label map
  or an outline overlay"* — checked `list_hooks` before proposing to build
  anything, and the session ended with a hand-written Fiji macro that the
  operator ran and confirmed (*"it bounded the cell"*). The runner already hands
  every adapter an artifact directory, so the natural shape is that the built-in
  writes a label map or outline TIFF next to the mosaic and `open_artifact`
  opens it. **Deliberately not folded into 43e**, which was already gated. Size
  it as a block alongside 43g, whose calibration work it would make visible.

```

</details>


### R49 — There is no way to remove a saved hook

**Users can save or overwrite hooks but cannot retire obsolete entries, so duplicates permanently clutter `list_hooks`.**

- **Status** — OPEN - `microclaw/hook_manager.py:333` implements saving and `microclaw/hook_manager.py:657` listing, while the tool surface at `microclaw/tools.py:9633` provides no removal operation.
- **Importance** — MEDIUM - It is a genuine usability problem that creates persistent clutter and ambiguity, though manual deletion remains a workaround and acquisition correctness is unaffected.
- **Where** — LOCAL - Manifest and hook-file deletion semantics can be implemented and tested entirely off-rig.
- **Block** — NONE - No later design block owns saved-hook removal; the register still records it as open at `design/35-usability-and-pfs-checklist.md:10204`.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10204–10210)</summary>

```markdown
- **There is no way to remove a saved hook.** Found by block 45's M5 round,
  2026-08-12. A superseded entry can only be re-saved, never retired, so the four
  `_v2` duplicates above will keep appearing in `list_hooks` even once every name
  resolves. No tool was added — the gap is recorded, not fixed, because deleting
  a user's hooks is outside what that block asked for. Weigh it against
  "Microclaw is easy to use" before Track C leaves a generated adapter behind.

```

</details>


### R52 — design/38 H2 - acquisition frame-cap policy is not inspectable

**The configured acquisition frame limit is enforced but cannot be queried by the agent or operator through a read-only tool.**

- **Status** — OPEN - `microclaw/safety.py:996` enforces `acquisition.max_frames`, while `microclaw/tools.py:3925` exposes system state without acquisition policy; the only register mention remains open at `design/35-usability-and-pfs-checklist.md:10273`.
- **Importance** — MEDIUM - Users can inspect the YAML or provoke a refusal, but cannot reliably assess whether a larger planned acquisition will bind before attempting it.
- **Where** — LOCAL - Code inspection and off-rig tool tests can fully establish and verify policy introspection.
- **Block** — NONE - No later written block claims the acquisition-budget introspection surface requested at `design/35-usability-and-pfs-checklist.md:10279`.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10273–10281)</summary>

```markdown
- **design/38 Round 4 H2 — acquisition frame-cap policy is not inspectable.**
  The agent and operator can see a particular run's reservation, but no tool
  reports the currently configured acquisition frame cap by name. A reservation
  payload is not the policy value and cannot be used to determine whether a
  larger plan should bind. The 40 µm / 0.5 µm five-position autofocus run was
  accepted at 145 reserved frames, so H2's budget-refusal limb was not tested.
  Add a read-only policy/introspection surface in the block that next opens
  acquisition-budget usability; this composition block does not fix it.

```

</details>


### R54 — A config can pass check-config with budgets that do not bind

**`check-config` can approve finite positive acquisition budgets whose enormous ceilings provide no meaningful automatic bound.**

- **Status** — OPEN - The only diagnostic still compares values with the packaged example (`microclaw/config.py:238`), and the register explicitly leaves implausible/non-binding limits unresolved (`design/35-usability-and-pfs-checklist.md:10324`).
- **Importance** — MEDIUM - Misleading validation weakens runaway protection, but the separate `confirm_above_*` thresholds still require human approval.
- **Where** — LOCAL - Synthetic configurations and off-rig validation tests can settle the diagnostic and policy behavior without hardware.
- **Block** — Block 12 closeout - It explicitly owes this row a disposition (`design/35-usability-and-pfs-checklist.md:10329`) but contains no implementation fix.
- **Effort** — MEDIUM

<details><summary>The original row, verbatim (`design/35` lines 10317–10330)</summary>

```markdown
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
```

</details>


### R58 — Clean hook save is not enforced in code

**Clean generated hooks bypass the in-code confirmation gate, despite the system prompt claiming every hook save is protected.**

- **Status** — OPEN - `microclaw/tools.py:9694-9700` calls `CONFIRM_FN` only when lint warnings exist, while `microclaw/agent.py:490-491` still claims hook-save confirmation is enforced in code.
- **Importance** — MEDIUM - Human review is the documented workaround, but a skipped review can persist untrusted hook code for later execution.
- **Where** — LOCAL - The conditional gate and its regression test are fully settleable off-rig.
- **Block** — `security/confirm-in-code` (design/11b issues 2+3) - `design/11b-code-review-issues-fixes.md:44-48` explicitly assigns in-code hook save/run confirmation to this branch.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10380–10382)</summary>

```markdown
- **Clean hook save is not enforced in code** (design/32 §4). The gate is
  conditional on the advisory lint firing, while the system prompt claims hook
  saves are enforced. Independent security fix.
```

</details>


### R61 — run_timelapse declares no artifact

**Plain `run_timelapse` results omit the dataset artifact declaration required for browser download through `/api/artifact`.**

- **Status** — OPEN - `microclaw/tools.py:4818` returns only `dataset_path` on the plain route, while `microclaw/webserve.py:252` allowlists only explicit `artifact.path` declarations.
- **Importance** — MEDIUM - The dataset is saved correctly, but browser users need a separate export or filesystem access to retrieve it.
- **Where** — LOCAL - Return payloads and API allowlisting can be settled entirely with off-rig tests.
- **Block** — NONE - `design/33-block14-phase5-dangling-impact-summary.md:132` explicitly records this as independent rather than owned by that block.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10396–10397)</summary>

```markdown
- **`run_timelapse` declares no artifact**, so its dataset cannot be downloaded
  through `/api/artifact` (the adaptive runners do declare one).
```

</details>


### R65 — Mid-acquisition cancellation and abort trigger

**There is no mid-acquisition cancellation or abort trigger, and the register row never states what one would mean.**

- **Status** — PARTLY - the schema now discloses that Stop and engine abort may not stop a burst (`microclaw/tools_schema.py:701`, `:712`), but no cancellation mechanism exists, and pyjavaz serialises every bridge call so no thread can preempt an in-flight core call. This is the same subject as R01.
- **Importance** — MEDIUM - what an operator can actually interrupt is a real safety question, but the honest answer is architectural and belongs with R01's wording fix rather than in a new mechanism.
- **Where** — LOCAL
- **Block** — NONE - fold into R01, which is about telling the operator the truth about interruption.
- **Effort** — SMALL
- **Provenance** — *coordinator-triaged, not runner-evaluated.* The Codex usage limit was reached before this row got a runner; the judgement above is the coordinator's own code read.

<details><summary>The original row, verbatim (`design/35` lines 10408–10408)</summary>

```markdown
- **Mid-acquisition cancellation and abort trigger.**
```

</details>


### R66 — Partial-failure and restart/session-ledger rig semantics

**Partial-failure and restart/session-ledger semantics have never been exercised on a rig.**

- **Status** — OPEN but unstated - the row is a bare one-line heading carried from the previous checklist and never says which semantics, which failure, or what evidence would close it.
- **Importance** — MEDIUM - the subject matters, but as written the row cannot be actioned or closed by anyone.
- **Where** — LOCAL - restating it is the first step and needs no hardware. Whoever takes it should either write a concrete criterion or delete the row.
- **Block** — NONE
- **Effort** — SMALL
- **Provenance** — *coordinator-triaged, not runner-evaluated.* The Codex usage limit was reached before this row got a runner; the judgement above is the coordinator's own code read.

<details><summary>The original row, verbatim (`design/35` lines 10409–10409)</summary>

```markdown
- **Partial-failure and restart/session-ledger rig semantics.**
```

</details>


### R81 — run_multiposition_acquisition refuses any non-observation hook

**Hooked multiposition runs, including the autofocus wrapper, cannot export their adaptive behavior as standalone scripts.**

- **Status** — OPEN - `_emit_multiposition` still raises `CannotEmit` for every non-observation hook at `microclaw/tools.py:581`, and `tests/test_session_script_export.py:3961` pins the autofocus wrapper’s refusal.
- **Importance** — MEDIUM - The refusal is loud and prevents wrong execution, but users cannot preserve or rerun a valid hooked multiposition workflow without restructuring it.
- **Where** — LOCAL - Emitter behavior and standalone execution can be settled with recorded calls and off-rig fakes.
- **Block** — NONE - The register explicitly leaves it ownerless at `design/35-usability-and-pfs-checklist.md:8911`.
- **Effort** — MEDIUM

<details><summary>The original row, verbatim (`design/35` register table)</summary>

```markdown
This row comes from the register's table "Absorbed into a block above". Verbatim row:

| `run_multiposition_acquisition` refuses any non-observation hook, where 43j taught `run_timelapse`/`run_zstack` to emit the adaptive program | design/63 block 63a, 2026-08-30 | **(no block)** — why `run_multiposition_with_autofocus` emits a refusal rather than a program; its emitter delegates, so both improve together whenever this is closed. |
```

</details>


### R38 — Import-stripping check and emitted analysis block can disagree

**Adaptive export validates stripped hook imports against autofocus names that the final emitted analysis block can conditionally omit.**

- **Status** — OPEN - The mismatch remains at microclaw/tools.py:1169, microclaw/tools.py:1291, microclaw/tools.py:1326 versus microclaw/tools.py:2047; no later closure is recorded.
- **Importance** — LOW - The coupling cannot currently fire because adaptive use forces autofocus_used true at microclaw/tools.py:1879.
- **Where** — LOCAL - Static inspection and an off-rig exporter regression test can settle it.
- **Block** — NONE - No existing written block owns this latent consistency fix.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10013–10022)</summary>

```markdown
- **The import-stripping check and the emitted analysis block can disagree by
  construction.** Noted during 43h review round 4, 2026-08-10, deliberately not
  fixed. `_adaptive_hook_export` and `_adaptive_runner_source` build the set of
  available names with `_analysis_source(include_autofocus=True)`, while
  `export_session_script` emits `_analysis_source(include_autofocus=autofocus_used)`.
  A hook importing an autofocus symbol would therefore pass the check and
  `NameError` at runtime. **Unreachable today** — `autofocus_used = adaptive_used
  or ...`, so it is always True wherever the stripping runs — so this is a latent
  coupling, not a live defect. One line to close (pass the same flag) whenever
  that file is next open; recorded so it is not rediscovered as a mystery.
```

</details>


### R62 — Historical calibration-artifact authoring gap

**There is no supported way to author a calibration artifact for a dataset acquired before the artifact path existed.**

- **Status** — OPEN - no authoring surface exists; `calibration_artifact` is only ever *read* (`microclaw/hooks.py:509`) and no tool or CLI writes one.
- **Importance** — LOW - it affects only datasets older than the artifact path, and design/29 already established the artifact schema has no objective field, so a hand-written YAML is a workable substitute.
- **Where** — LOCAL - authoring a YAML against a documented schema needs no hardware; the historical datasets are already on disk.
- **Block** — NONE - design/29 recorded the gap and closed without owning it.
- **Effort** — SMALL
- **Provenance** — *coordinator-triaged, not runner-evaluated.* The Codex usage limit was reached before this row got a runner; the judgement above is the coordinator's own code read.

<details><summary>The original row, verbatim (`design/35` lines 10398–10400)</summary>

```markdown
- **Historical calibration-artifact authoring gap** (design/29): no supported way
  to author a calibration artifact for a dataset acquired before the artifact
  path existed. Not about objective turrets.
```

</details>


### R63 — Context-compaction attribution observation

**Under context compaction the model twice attributed an earlier turn's tool calls to the current turn; the original diagnosis was refuted and no cause was established.**

- **Status** — OPEN as an observation, not a defect - design/32 §5 records the sighting and its own refutation; nothing in the code has been identified as wrong.
- **Importance** — LOW - two sightings, cause unknown, no measured consequence; it is a note asking for a sharper probe before anyone claims a cause.
- **Where** — LOCAL - a compaction probe is a scripted conversation, no hardware.
- **Block** — NONE
- **Effort** — SMALL
- **Provenance** — *coordinator-triaged, not runner-evaluated.* The Codex usage limit was reached before this row got a runner; the judgement above is the coordinator's own code read.

<details><summary>The original row, verbatim (`design/35` lines 10401–10405)</summary>

```markdown
- **Context-compaction attribution observation** (design/32 §5). Under
  compaction the model twice attributed an earlier turn's tool calls to the
  current turn. The original diagnosis was refuted by the passing run. Settle it
  with a sharper probe before claiming a cause. Recorded as an observation, not a
  fix.
```

</details>


### R74 — Phase 2 XY typed-actuator ambiguity / proposed axis field

**XY-stage numeric properties cannot identify whether they control X or Y, so typed absolute-position bounds are ambiguous.**

- **Status** — PARTLY - Block 4 closed generation by refusing ambiguous entries (`design/35-usability-and-pfs-checklist.md:2676`), but the schema still lacks an axis (`microclaw/safety.py:119`) and guards XY writes against both axes (`microclaw/safety.py:1103`).
- **Importance** — LOW - It only over-refuses legal writes, and no current rig exposes a writable XY position property through this path.
- **Where** — LOCAL - Parser, authorization, and guard behavior can be settled with fake-core tests.
- **Block** — NONE - Block 4 deliberately deferred the schema extension, and no later written block owns it.
- **Effort** — MEDIUM

<details><summary>The original row, verbatim (`design/35` register table)</summary>

```markdown
This row comes from the register's table "Absorbed into a block above". Verbatim row:

| Phase 2 XY typed-actuator ambiguity / proposed `axis` field | impact summary | **Block 4** refuses ambiguous entries; schema extension **(no block)** |
```

</details>



### R82 — Open the community skill package notebook

**Accepting a community-authored skill package needs its own design notebook; `design/71` settled the decisions it should start from and deliberately left the work out of its blocks.**

- **Status** — OPEN - `design/71` §"Community skill packages — next notebook brief" records the settled ownership, hosting, intake, isolation, observer and export dispositions, and states that blocks 71a–71c must not grow toward them.
- **Importance** — HIGH - it blocks *any* community skill, which is the ownership problem `design/71` opens on: accepting one into this repository silently makes MicroClaw its maintainer.
- **Where** — LOCAL - a design notebook, written against `design/71`'s brief and the SMAPpy 0.1.0 feasibility section.
- **Block** — None yet; this row *is* the request to open the notebook. `design/71` §"What the boundary should be, when it is taken up" is its starting material.
- **Effort** — LARGE
- **Provenance** — carried from `design/71` §"Register rows this leaves behind", not from the design/35 triage.

### R83 — Generic package/protocol conformance needs a fixture

**Conformance should be built against a fixture package rather than against SMAPpy, so the generic work does not wait on a third party's release.**

- **Status** — OPEN - no fixture package exists; `design/71`'s feasibility section is written against SMAPpy 0.1.0, whose release is not ours to schedule (see R85).
- **Importance** — HIGH - without a fixture, every conformance decision is coupled to one external package's timetable, and the first real package becomes the specification.
- **Where** — LOCAL - a fixture package plus the protocol it has to satisfy.
- **Block** — NONE - it precedes the notebook R82 asks for, or is its first block.
- **Effort** — MEDIUM
- **Provenance** — carried from `design/71` §"Register rows this leaves behind", not from the design/35 triage.

### R84 — Three export behaviours are unpinned ahead of a runner

**Three export decisions for the community-package path are stated as intent but not pinned by a test, and an unpinned export decision is exactly what blocks 43h, 47 and 52a each paid for.**

- **Status** — OPEN - the three are `@emits`-as-comment for the analysis tool, trigger identity in the acquisition's record, and analysis failure kept out of `_recorded_outcome`'s two shapes. `design/71` shows neither `@refuses` nor `@emits_nothing` produces the wanted behaviour: `@refuses` routes through the `renderer is None` branch (`tools.py:1989`-`:1994`) into `refuse()` (`:1968`), which plants a `raise RuntimeError` in the exported script.
- **Importance** — HIGH - `CLAUDE.md` records this failure shape three times over (43h's `generate_and_save_hook`, 47's `set_roi`/`clear_roi`, 52a's `move_named_stage`); each killed its own block's gate script.
- **Where** — LOCAL - the marker's behaviour is settled by reading the emitter and pinning it with a test, the way `test_every_registered_tool_has_exactly_one_export_decision` pins the marker count.
- **Block** — NONE - pin these before a runner is handed the notebook, not during it.
- **Effort** — SMALL
- **Provenance** — carried from `design/71` §"Register rows this leaves behind", not from the design/35 triage.

### R86 — run_mda bypasses _acquire_with_hooks, so lifecycle events are invisible

**MMStudio MDA runs go around the supervised acquisition path, so any acquisition-lifecycle event added there does not fire for them.**

- **Status** — OPEN - pre-existing, and not introduced by `design/71`; noticed while scoping where a community package's acquisition trigger would observe from.
- **Importance** — LOW - `run_mda` is a deliberate hand-off to MMStudio's own engine and nothing today depends on observing it. It matters only once something subscribes to acquisition lifecycle events and quietly gets none from this route.
- **Where** — LOCAL - the divergence is visible in the call path; whether to close it is a design question about what `run_mda` promises.
- **Block** — NONE
- **Effort** — MEDIUM
- **Provenance** — carried from `design/71` §"Register rows this leaves behind", not from the design/35 triage.

## Demo machine

One driven session or one standalone script run on the Windows demo machine. Cheap, always available, no dose.


### R27 — The agent started live view unprompted on a laser-dose rig

**The agent may start live view without being asked, potentially exposing a sample continuously when camera triggers drive lasers.**

- **Status** — OPEN - `microclaw/agent.py:88` still permits proactive live view, while `microclaw/tools.py:2749` and `microclaw/tools_schema.py:266` expose no `camera_triggers_lasers` point-of-use context; the row remains open at `design/35-usability-and-pfs-checklist.md:9716`.
- **Importance** — HIGH - An unrequested live stream can spend laser dose and damage or alter the sample.
- **Where** — DEMO - The unconditional no-unprompted-live behavior is profile-independent and can be demonstrated by observing whether the agent calls `start_live_view`; real laser exposure need not be reproduced.
- **Block** — NONE - Block 43a/F3 is already merged, and its later M5 failure was explicitly carried forward rather than assigned to another block.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 9716–9730)</summary>

```markdown
- **The agent started live view unprompted on a rig where that is laser dose.**
  M5, 43f's Step 6a, 2026-08-11: *"Let me start live view so you can watch the
  survey"*, with 640 enabled and the camera trigger firing the lasers. design/43
  F3's shipped `SYSTEM_PROMPT` text says, in as many words, *do not start it "so
  the user can see"* — and that is very nearly the sentence the agent used. This
  is **F3's rule, not 43f's**, and it is the unconditional half of that rule: it
  does not depend on the profile. Note the counterpart in the same session, after
  the trigger fact was stored: asked for live view in Step 6b, the agent said
  what it would cost before starting it. So the conditional half works and the
  unconditional half did not hold. **Not folded into 43f by coordinator ruling** —
  the gate had already passed and F3's text is a different subject.
  Worth considering with it: the same "reach the point of use" pattern 43f built
  for `get_roi` would put `camera_triggers_lasers` into `start_live_view`'s own
  payload, so the cost statement does not rest on prompt text alone.

```

</details>


### R32 — The emitted standalone script is completely silent

**Exported standalone acquisitions can run silently without reporting output datasets, hits, or acquired-frame counts.**

- **Status** — PARTLY - Non-survey adaptive exports print one dataset path (`microclaw/tools.py:1721`), but adaptive surveys return without any outcome print (`microclaw/tools.py:1624-1670`; `design/35-usability-and-pfs-checklist.md:9339`).
- **Importance** — HIGH - Silence can conceal minutes of illumination and dose, mis-score runs, and waste rig time.
- **Where** — DEMO - The demo machine can execute an exported adaptive survey and verify that its terminal output names the real collision-resolved datasets and counts.
- **Block** — NONE - The later finding explicitly says “no block” and recommends making this the next block (`design/35-usability-and-pfs-checklist.md:9337-9372`).
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 9804–9812)</summary>

```markdown
- **The emitted standalone script is completely silent.** No `print()`, no
  `logging.basicConfig` — a run that moves the stage, fires illumination for
  minutes and writes several datasets says nothing on stdout or stderr, so a
  success and a failure look identical in the terminal. Found 43n demo round 2,
  where it cost a mis-scored gate step: a 0-byte capture was read as "the script
  never ran" when it had run correctly. **This is a usability defect in the one
  artifact `CLAUDE.md` says the user walks away with**, and the fix is small —
  emit the dataset paths written, the hit count, and the acquire frame count. Not
  scheduled; not 43n's, which is about what the script *does*.
```

</details>


### R46 — Block 13 position-list rollback path unexercised on a rig

**The post-mark failure path should remove only the positions added by the failed hooked acquisition, but that rollback has never been exercised against a real Micro-Manager position list.**

- **Status** — OPEN - The rollback remains implemented at microclaw/tools.py:7050 and unit-covered at tests/test_tools.py:5582, but design/35-usability-and-pfs-checklist.md:6078 explicitly records zero rig executions and no later block closes it.
- **Importance** — HIGH - A defective rollback could silently leave stale positions that contaminate a later acquisition and produce wrong data.
- **Where** — DEMO - The demo configuration can exercise the real Micro-Manager position list and synthetic acquisition while a controlled post-mark refusal triggers rollback.
- **Block** — Block 13 - Its any-rig G1 criterion owns failed marked-run cleanup, but the recorded M5 pass reached only the pre-mark guard path.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10159–10163)</summary>

```markdown
- **Block 13's position-list rollback path is unexercised on a rig.** The M5 gate
  passed G1 without it, because check-before-mark meant nothing unsafe was
  written and no rollback was needed. The rollback only runs when a refusal
  lands *after* marking (a dose or budget refusal is the plausible one). Covered
  by tests, not by hardware.
```

</details>


### R05 — Focus-lock ordering rule not followed on the Nikon Ti

**The model can engage or adjust a hardware focus lock without first making the explicitly required dedicated state call.**

- **Status** — OPEN - `microclaw/agent.py:333` still relies on a prompt instruction, while `tests/test_agent.py:381` only checks that the text exists and `set_focus_lock` has no ordering enforcement (`microclaw/tools.py:10247`).
- **Importance** — MEDIUM - it can act on a focus lock without the intended preflight, risking incorrect focus and rig time, though the observed Nikon session had equivalent aggregate state information.
- **Where** — DEMO - its synthetic autofocus device can support a live-model call-order observation without unavailable Nikon hardware.
- **Block** — NONE - `design/35-usability-and-pfs-checklist.md:9105` explicitly records that no block is scheduled, and no later block closes it.
- **Effort** — MEDIUM

<details><summary>The original row, verbatim (`design/35` lines 9079–9106)</summary>

```markdown
### Focus-lock ordering rule was not followed on the Nikon Ti — added 2026-08-30 **(no block)**

`SYSTEM_PROMPT` says: before any operation that engages or adjusts a hardware
focus lock, call `get_focus_lock_state` first. A normal-work Ti session did not
follow that exact ordering. Evidence:
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/nikon-history-design61/20260830_160718_176677_microclaw_history.jsonl`,
sha256 `557235fcfe0adcfa0221c5395d255d15634fd7eec1b59c0e810d70dad1248596`.
After two unsuccessful property-probe sweeps, the recorded outer tool sequence
contains `set_focus_lock(enabled=true)` before the later explicit
`get_focus_lock_state` call.

This is **not** a failure of design/61's `nikon-pfs` route: after the operator
asked "hey, focus for me", `get_system_state` returned focus device
`TIPFSStatus`, and the agent loaded `nikon-pfs` before its first focus action.
That positive observation correctly authorized 61c. It is also not evidence
that the lock was engaged without any state observation — the earlier
`get_system_state.focus` payload carried `engaged`, `device`, status properties
and the probe hint. The narrower defect is that a model treated that broader
read as satisfying (or bypassed) a core rule that names one required tool
literally.

Do not fix this by weakening the invariant to “read focus state somehow” without
deciding whether the dedicated tool has information or semantics the aggregate
state lacks. Scope the smallest mechanism that makes the required ordering
reliable, and test the ordering choice with a live-model observation or another
instrument that actually chooses calls — a scripted mock whose response already
contains `get_focus_lock_state` cannot prove routing. No block is scheduled.

```

</details>


### R12 — An agent asked the operator for positions it could have read itself

**The agent unnecessarily asked the operator to supply saved positions instead of reading the position list itself.**

- **Status** — OPEN - The generic prompt already says not to ask for reversible `get_*` bookkeeping (`microclaw/agent.py:218-219`), but the later observed failure remains explicitly unclosed at `design/35-usability-and-pfs-checklist.md:9299-9306`.
- **Importance** — MEDIUM - It creates avoidable operator friction and has a simple manual workaround, without misleading hardware state, spending dose, or corrupting data.
- **Where** — DEMO - The demo configuration can exercise `get_position_list` and reveal whether the live agent reads it without prompting the operator.
- **Block** — NONE - Design/14 shipped the existing generic prompt rule, but no later written block owns or closes this still-reproduced behavior.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 9242–9307)</summary>

```markdown
### Two from design/55's gates — added 2026-08-26 **(no block)**

**1. The stage-move settle tolerance is a package constant with no configuration
path, and on a real rig it silently changed the experiment.**
`STAGE_MOVE_TOLERANCE_UM = 0.5` lives in `microclaw/controller.py`. It is not in
the safety config, not an argument to `move_named_stage`, and not a rig-profile
field. **Measured on M2, 2026-08-26** (design/55 Part B): a sweep asked for
`199.9 µm`, the `TIRF Stage` stopped at `198.8` after 10.109 s reporting **idle**
— 1.1 µm short — and the run correctly refused. The only way to finish the sweep
was to **retarget it to 198.8**, the position the stage happens to land at. The
operator asked for 199.9 and got a different experiment.

**CLOSED 2026-08-30 by block 66** (design/66, merged). The paragraph that stood
here — *"the refusal is not the defect and must not be 'fixed' by loosening
it"* — was half right and is superseded. The stage genuinely did not arrive, and
reporting that is right; but the 0.5 µm number was never a property of any
stage, and asserting it everywhere turned a constant that came from nowhere into
a universal *accuracy* claim Microclaw was in no position to make. The question
the check answers is **response, not accuracy**: did the axis act on the command,
or did nothing happen. The default is now
`max(2.0, 0.1 × |target − start|)` — which guarantees 90% progress above 20 µm,
where a 0.5 µm band asserted nothing about a large move because it could not be
met at all — and an operator who needs an absolute requirement on one axis
declares `z_move_tolerance_um` or a named stage's `move_tolerance_um`, which then
*is* the band. **Measured on M2, 2026-08-30**: the same class of move that
refused at 10.1 s on 2026-08-26 and 2026-08-29 completed in 0.453 s with its
0.7 µm miss reported, not hidden, and the target unretargeted.

The loosening is real and is stated rather than sold: a 200 µm move is now
verified to 20 µm. What it bought is the failure that matters — a stalled,
unpowered or ignored axis — which the old constant detected only by accident.

**The three unsettled questions are answered.** Where the value lives for the
**core focus**: `stage.z_move_tolerance_um`, delivered through one
`SafetyGuard.stage_move_tolerance` accessor to `set_z`, `move_stage_z`,
`sweep_autofocus` and `_restore`. For **XY**: nowhere yet — both keys are parsed
and **refused** at their exact YAML path, naming `move_stage_xy`'s missing
arrival loop, so an operator learns the key has no effect at the moment they
write it rather than after trusting it. The **default** is the relative rule
above, not a wider constant. And **no per-call override**: an agent under
pressure to continue must not be able to redefine success for one call.

**The export coupling did bite, and was caught before the rig.** `_stage_move_contract_source()`
(`tools.py`) emits all five constants as literals into every standalone script
and inlines `settle_stage_move` with `inspect.getsource`. A config-driven
tolerance must travel into the export as a **recorded per-device value**, or the
standalone script enforces a different tolerance than the live run did — the
defect class `CLAUDE.md` §"An exported script that compiles is not an exported
script that works" exists to prevent. Six call sites read the constant, across
`tools.py`, `hook_decisions.py` and `autofocus.py`.

**The gate was run** — M2 and the demo machine, 2026-08-30, block 66's row above.
Nothing configured: the relative rule alone completed the move the accident
refused. A declared per-axis tolerance is exercised off-rig, in the live path and
in an executed standalone export, because scoring it on hardware would need a rig
whose stage misses by more than its declared band on demand.

**2. An agent asked the operator for positions it could have read itself.** Told
to use "the five positions in the list" (design/55 round 3), it came back asking
for them rather than calling `get_position_list`, then read the list when told
to. Challenged, it diagnosed itself exactly — *"reading the position list is
free, reversible bookkeeping — exactly the kind of thing I should just do rather
than ask about. I asked when I should have looked."* Cheap, reversible reads
should not be round-tripped to the operator. Probably a `SYSTEM_PROMPT` nudge;
no block, and no evidence yet on how often it happens.

```

</details>


### R13 — Batched analyzer cannot batch a one-dataset-per-position survey

**Unhooked tile surveys create separate datasets, forcing batched analyzers such as ilastik to restart once per position at severe Windows cost.**

- **Status** — OPEN - `run_analysis_on_saved_dataset` still accepts one `dataset_path` (`microclaw/tools.py:5177`), while unhooked multiposition acquisition still invokes the protocol separately per position (`microclaw/tools.py:7088`).
- **Importance** — MEDIUM - Results remain correct, but routine analysis can waste roughly 17 minutes instead of 90 seconds; using a hooked single-dataset acquisition is a workaround.
- **Where** — DEMO - The synthetic Windows setup can verify both dataset layout and the platform-specific ilastik startup penalty without real optics.
- **Block** — NONE - The register still explicitly says it was deliberately not opened as a block (`design/35-usability-and-pfs-checklist.md:9332`), and no later block owns the general unhooked tile path.
- **Effort** — MEDIUM

<details><summary>The original row, verbatim (`design/35` lines 9308–9337)</summary>

```markdown
### One from block 9a's demo round 2 — added 2026-08-26 **(no block)**

**A batched analyzer cannot batch a survey stored as one dataset per position,
and on Windows that costs minutes.** `run_analysis_on_saved_dataset` takes a
single `dataset_path`, so the ilastik adapter's "one bounded subprocess over the
whole survey" holds only when the survey is one dataset with a position axis. A
`run_tile_acquisition(protocol="timelapse")` writes **one dataset per position**
— nine `dataset_path`s for a 3x3 — so the agent correctly ran the adapter nine
times.

**The cost is platform-shaped and large.** Measured 2026-08-26 on the demo
machine: **85–104 s per single-field invocation**, one outlier at 254 s, against
a nine-field batch of 69 s in round 1 and 8–13 s on macOS. So ilastik's start-up
is roughly **60–90 s on Windows against 5–7 s on macOS**, and losing the batch
turned ~90 s of work into ~17 minutes. design/26 F5 measured the start-up at
7.6 s and concluded batching "saved 334 s" on 45 tiles; on Windows that saving is
an order of magnitude larger, and F5's number should not be quoted as
cross-platform.

**Two candidate fixes, and the choice is not obvious.** Either the analysis tool
accepts several dataset paths and batches across them, or the tile tool writes
one multi-position dataset for the unhooked protocols as it already does for the
hooked one. The second is the smaller surface and fixes it for every consumer,
not just ilastik; the first does not change acquisition behaviour that other
things may depend on. **Deliberately not opened as a block** (operator, 2026-08-26)
— it wants a fresh session rather than the tail of this one.

**Not a defect in 9a**: the adapter did exactly what it promises per dataset, and
every one of the nine runs was correct.

```

</details>


### R14 — An exported adaptive survey prints nothing

**Exported adaptive surveys create datasets but do not print their resolved paths, including the more valuable acquire-on-hit dataset.**

- **Status** — OPEN - The survey and acquire-on-hit acquisitions still end without reporting either path at `microclaw/tools.py:1625` and `microclaw/tools.py:1667`, while only the non-survey branch prints one at `microclaw/tools.py:1726`.
- **Importance** — MEDIUM - Data is produced correctly, but unattended users must manually discover collision-suffixed output directories.
- **Where** — DEMO - The fix is locally testable, but the operator-facing terminal output and real `_1`/`_2` collision path should be confirmed by running the standalone export on the demo machine.
- **Block** — NONE - The register explicitly records this as “no block,” and later design documents do not assign it to an implementation block.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 9338–9374)</summary>

```markdown
### One from the design/57 demo gate — added 2026-08-24 **(no block)**

**An exported adaptive *survey* prints nothing at all, and this one CAN be tested
where the operator has access** — the demo machine produced it twice.

`_emit_adaptive`'s non-survey branch ends with `print('Dataset:', ...)`
(`tools.py:1332`); the survey branch, ending `acq.acquire(event_source(acq))`,
has no equivalent. A standalone survey run writes its dataset and hook log and
exits 0 having said nothing whatsoever. The comment above that print states the
reason it exists — pycro-manager appends `_1`, `_2` on collision, so the obvious
guess at the output directory is usually wrong — and the survey path is the one
run **unattended**, which is what the operator asked for in the gate session's own
words. Not folded into 57a, which was about bounds.

**It is not literally one line, and the open question must be settled before the
code.** The survey branch wraps its acquisition in
`_emitted_acquisition_with_restoration` (`tools.py:1230`), so the print appends
after that call exactly as the non-survey branch does at `:1332` — `acq` is still
bound, and on the failure path the wrapper re-raises so the print is correctly
unreachable. But the survey branch has an **`acquire_on_hit` continuation that
opens a second `Acquisition`** (`tools.py:1273`), also named `acq`. Decide what a
two-phase run reports: printing only the survey's dataset would quietly hide the
acquire-on-hit output, which is the more valuable of the two. Report both, and
label which is which.

**Give it a demo-gate limb even though it is small.** Its entire value is what an
operator *sees* on a standalone run, and that is precisely what design/57's two
rounds could not tell us: a green suite and hook logs matching record-for-record
sat next to a script that printed nothing, and only reading the emitted source
found it. A test asserting `"print('Dataset:'" in source` passes while the
operator still learns nothing — so the limb is *run the exported survey
standalone and read the terminal*, confirming it names the real `_1`/`_2`
directory. Five minutes on the demo machine, which has everything set up.

**Recommended as the next block** as of 2026-08-24: it is one of the few open rows
testable without a Nikon.

```

</details>


### R69 — Shipped context thresholds are unexercised

**The shipped context high/low water marks were never exercised at their real values; the gate ran at 1500/800.**

- **Status** — OPEN - `microclaw/conversation.py:23`-`:24` ship 120_000/90_000, and the compaction behaviour at those values is asserted by test rather than measured in a real session.
- **Importance** — MEDIUM - compaction at the shipped threshold is what a long rig session actually hits, and cache behaviour there is unmeasured.
- **Where** — DEMO - a long driven demo session reaches the real threshold with no dose and no instrument.
- **Block** — NONE
- **Effort** — MEDIUM
- **Provenance** — *coordinator-triaged, not runner-evaluated.* The Codex usage limit was reached before this row got a runner; the judgement above is the coordinator's own code read.

<details><summary>The original row, verbatim (`design/35` lines 10414–10415)</summary>

```markdown
- **Shipped context thresholds are unexercised.** Block 15 shipped 120k/90k; the
  gate ran at 1500/800. Cache behaviour is asserted by test, not measured.
```

</details>


### R72 — The model has never been observed saying what live view costs before starting one

**The model has never been observed stating what live view costs before starting one; only the refusal half of that rule has been seen to hold.**

- **Status** — OPEN - the counterpart of R27, which found the unconditional half failing on M5. Neither half has been exercised together in one session.
- **Importance** — MEDIUM - it is the disclosure half of a dose rule whose other half is already known to have failed.
- **Where** — DEMO - observing whether the agent states a cost before starting live view needs no real laser; check it in passing during R27's session.
- **Block** — NONE - fold into R27; they are two halves of one rule and one session settles both.
- **Effort** — SMALL
- **Provenance** — *coordinator-triaged, not runner-evaluated.* The Codex usage limit was reached before this row got a runner; the judgement above is the coordinator's own code read.

<details><summary>The original row, verbatim (`design/35` lines 10445–10458)</summary>

```markdown
- **From block 43a's M2 gate (2026-08-09), two criteria that could not be
  exercised there and are owed by no block:**
  - **`open_artifact`'s multi-channel caveat has never been checked on any rig.**
    43a's prompt text tells the model that opening a dataset's TIFF stack files
    shows channels as planes rather than named channel axes. That claim comes
    from design/42's tabled limitation, and 43a's gate datasets were
    single-channel, so the wording has never been read back against a real
    multi-channel dataset. Any later block that acquires one should look.
  - **The model has never been observed saying what live view costs before
    starting one.** 43a's G5 passed on its first half — live was never started
    unprompted — but the operator never asked it to start live, so the
    dose-warning half of the rule is unexercised. It is prompt-conditioned and
    cheap to check in passing during any session on a TTL-shuttered rig.

```

</details>


### R03 — design/65 carried row 1 - a hook choosing genuinely different targets per frame has never run on a rig

**Validate that an adaptive hook can compute and apply at least two genuinely different hardware targets across successive frames.**

- **Status** — OPEN - `design/65-adaptive-streaming-storm-hooks.md:693-697` says only identical hook proposals ran on M5, and `design/35-usability-and-pfs-checklist.md:9061` records no later closure.
- **Importance** — LOW - The guarded write/read-back mechanism already works; this is a narrow rig-evidence gap rather than a known behavioral defect.
- **Where** — DEMO - A short adaptive run can alternate values on a synthetic writable property and verify per-frame selection without real optics or dose.
- **Block** — NONE - The register explicitly labels it open with no block at `design/35-usability-and-pfs-checklist.md:9061`.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 9045–9078)</summary>

```markdown
### design/65 — **CLOSED 2026-09-01**, two rows carried back here — added 2026-08-31 **(pointer only)**

**`design/65` was coordinated and owned its own blocks and ledger**, like
design/48 through design/60. This file did not track its rows; it pointed at
them. The doc is `design/65-adaptive-streaming-storm-hooks.md`.

**All three blocks merged; `main` `fac5c78`.** `run_timelapse(n_frames=None,
max_frames=N, hook_strategy=...)` is the single-field adaptive route. Gate
scoring is `design/65-block65c-m2-scoring.md`; the runbook and computed gate are
`design/65-block65c-m5-runbook.md` and `design/65-block65c-gate*.py`.

**Two things design/65 could not close, carried here because a closed doc is not
where an open item gets seen:**

| row | evidence | disposition |
| --- | --- | --- |
| A hook that computes genuinely **different targets per frame** has never run on a rig | M5 limb 4's hook proposed the same value on all three writes; the 1<->2 transitions were demonstrated by the entry and restoration writes instead. The write/read-back mechanism is proven; the hook's own value *selection* is not | **open, no block.** One short adaptive run whose predicate produces at least two distinct targets would close it |
| The two cadence measurements **differ by 2.7x between rigs**, each n=1 | 50 ms exposure, same shape: M2 0.2495 s mean, M5 0.0912 s. M5's fixed baseline was 0.0502 s, so adaptive dispatch cost ~41 ms/frame there | **open, informational.** Neither number is "the" cadence and a third rig is a third observation, not a tiebreak. The 0.5 s/frame teardown allowance bounds both |

The "5x cadence cost" figure that circulated mid-block was wrong: it compared
the M2 measurement to a *theoretical* camera frame rate with no fixed-route
baseline taken on any rig. See [[feedback_one_measurement_is_not_a_property]].

Written from Amr's M2 session of 2026-08-29, which did not fail because the agent
overlooked hooks — it called `list_hooks`, read `get_hook_documentation`, and
found that the hook action vocabulary and the acquisition runners do not expose
the same capability. **What is missing is not a runner**: the adaptive stack
already keys on `axes_signature` and never on XY, and only three things in it are
survey-specific. The load-bearing one is that `ContinueSurvey` means "dispatch
`events[cursor]`" over a finite list built from positions.

**The SMLM skill is also wrong twice** and is a shipped file, so the documentation
correction is its own block and does not wait for the runner.

```

</details>


### R47 — saturated_fraction reporting precision is ungated

**The displayed `saturated_fraction` now has enough precision to distinguish slight clipping from zero, but that reporting change has never had a runtime gate.**

- **Status** — OPEN - Both reporting paths round to six places (`microclaw/tools.py:5404`, `microclaw/tools.py:6880`), but the register still explicitly records commit `f4e98c6` as ungated (`design/35-usability-and-pfs-checklist.md:10164`).
- **Importance** — LOW - The decision logic was already correct; this affects only how clearly the measured clipping fraction is reported.
- **Where** — DEMO - The synthetic camera can settle the runtime reporting path without real optics or rig-specific hardware.
- **Block** — NONE - Block 13 is closed, and no later written block owns verification of its post-gate precision commit.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10164–10166)</summary>

```markdown
- **`saturated_fraction` reporting precision landed after block 13's gate**
  (`f4e98c6`) and is itself ungated. Reporting-only, two-directionally tested.

```

</details>


### R71 — open_artifact's multi-channel caveat has never been checked on any rig

**Validate that `open_artifact` accurately warns users that multi-channel NDTiff stacks open as unnamed planes rather than reconstructed channel axes.**

- **Status** — OPEN - Block 43a marked this criterion N/A and “still unexercised anywhere” at design/35-usability-and-pfs-checklist.md:6874; the claim remains in microclaw/agent.py:117.
- **Importance** — LOW - This is validation of an explicitly disclosed display limitation and does not alter acquired data or hardware state.
- **Where** — DEMO - The demo configuration can produce a synthetic multi-channel dataset and exercise the same NDTiff-to-ImageJ file path without real optics.
- **Block** — NONE - Block 43a shipped the wording but explicitly carried this unexercised criterion forward rather than assigning it.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10445–10458)</summary>

```markdown
- **From block 43a's M2 gate (2026-08-09), two criteria that could not be
  exercised there and are owed by no block:**
  - **`open_artifact`'s multi-channel caveat has never been checked on any rig.**
    43a's prompt text tells the model that opening a dataset's TIFF stack files
    shows channels as planes rather than named channel axes. That claim comes
    from design/42's tabled limitation, and 43a's gate datasets were
    single-channel, so the wording has never been read back against a real
    multi-channel dataset. Any later block that acquires one should look.
  - **The model has never been observed saying what live view costs before
    starting one.** 43a's G5 passed on its first half — live was never started
    unprompted — but the operator never asked it to start live, so the
    dose-warning half of the rule is unexercised. It is prompt-conditioned and
    cheap to check in passing during any session on a TTL-shuttered rig.

```

</details>


### R75 — Channel preset colliding with a typed actuator - rig coverage

**A channel preset that writes a typed actuator is guarded in code but has never been exercised through a real Micro-Manager bridge.**

- **Status** — OPEN - Off-rig tests cover classification and guarded execution (`tests/test_typed_actuators.py:206`, `microclaw/authorization.py:1856`), while `design/33-authorization-map.md:873` still records no rig coverage.
- **Importance** — LOW - The safety path is tested locally and no available rig currently has a natural preset-to-typed-actuator collision.
- **Where** — DEMO - A temporary demo configuration can manufacture the collision and settle the bridge path without risking physical motion.
- **Block** — NONE - Block 49a explicitly excluded this scenario from its gate (`design/49-block49a-rig-gate.md:131`).
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` register table)</summary>

```markdown
This row comes from the register's table "Absorbed into a block above". Verbatim row:

| Channel preset colliding with a typed actuator, only tested off-rig | impact summary | **Block 4** surfaces collisions; rig coverage **(no block)** |
```

</details>


## M2

Needs the Andor iXon / TIRF rig — a real asynchronous stage, real EM gain, and a camera trigger that fires the lasers.


### R80 — Detect cross-plane non-response for sub-band Z steps

**Detect when repeated sub-band Z commands fail to advance the stage across an autofocus sweep, instead of treating the resulting planes as valid.**

- **Status** — OPEN - `sweep_autofocus` only records unverifiable indices and measured positions without comparing consecutive readings or refusing (`microclaw/autofocus.py:352-410`); design/35:8908 still explicitly leaves the mechanism open.
- **Importance** — HIGH - A stalled stage can expose multiple nominal Z planes at one physical position, wasting dose and producing a misleading autofocus result.
- **Where** — RIG:M2 - M2’s approximately 0.8 µm quantization can exercise real repeated positions from sub-band commands; the demo stage’s synthetic response cannot settle physical non-response.
- **Block** — NONE - Design/66:765-770 explicitly excludes the mechanism, and “next Z-motion integrity block” is an unscheduled placeholder rather than a written block.
- **Effort** — MEDIUM

<details><summary>The original row, verbatim (`design/35` register table)</summary>

```markdown
This row comes from the register's table "Absorbed into a block above". Verbatim row:

| Detect cross-plane non-response for sub-band Z steps | design/66, “The floor, and moves too small to verify”, 2026-08-30 | **Owner: next Z-motion integrity block (unscheduled).** Block 66 deliberately accepts and aggregates per-plane `arrival_unverifiable` results when a Z step is no larger than its response band. `sweep_autofocus` already holds consecutive `measured_z_positions`; detect failure to advance across planes from those existing reads and decide the sweep-level refusal contract. This row must remain open until that mechanism and its rig gate land. |
```

</details>


## M5

Needs the EMU + MicroFPGA rig — real lasers, a TTL gate, a saved-hook registry, and a Hamamatsu camera.


### R48 — filament_position_filter scores bead fields as filamentous

**The saved filament filter misclassifies punctate bead fields as filamentous because its Sato ridge score does not distinguish puncta from ridges.**

- **Status** — OPEN - The migrated hook still promises to reject blobby fields while using the unchanged 0.02 ridge-coverage threshold (`tests/fixtures/hooks/m5_migrated/filament_position_filter.py:13` and `:21`); block 45 explicitly says calibration or description work remains (`design/45-block45-rig-gate.md:161`).
- **Importance** — HIGH - It can silently retain the wrong fields or discard valid ones during adaptive acquisition, producing misleading selection and spending dose and rig time.
- **Where** — RIG:M5 - The affected saved hook is in M5’s registry, and the demo’s synthetic camera cannot validate discrimination between real punctate and filamentous specimens.
- **Block** — NONE - Block 45 only made the hook resolvable and explicitly left its scales or description as separately owed work.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10167–10178)</summary>

```markdown
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

```

</details>


### R53 — design/38 F9 - per-source illumination prerequisites

**Preflight needs operator-authored, per-source illumination arming prerequisites so it refuses acquisitions that cannot emit.**

- **Status** — OPEN - `microclaw/safety.py:153` has no prerequisite field, and `microclaw/tools.py:4612` still reports illumination properties and the emission path as unverified.
- **Importance** — HIGH - It can accept an acquisition that silently records signal-free data while spending sample dose and rig time.
- **Where** — RIG:M5 - The demo has no lasers or equivalent TTL gate, so it cannot validate the refusal against a real arming prerequisite.
- **Block** — NONE - `design/38-plus-acquisition-session-findings.md:159` gives follow-up direction, while `design/38-gate-prompts.md:376` explicitly leaves it separate rather than assigning a block.
- **Effort** — MEDIUM

<details><summary>The original row, verbatim (`design/35` lines 10282–10297)</summary>

```markdown
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

```

</details>


### R67 — UV activation closed-loop test

**Validate image-feedback-driven UV activation by adjusting MicroFPGA laser pulse duration during acquisition.**

- **Status** — OPEN - Block 4b made pulse duration writable as `bounded-numeric` (`design/35-usability-and-pfs-checklist.md:3386`), but the existing UV fixture uses a fixed ramp rather than feedback (`design/32-block7b-rig-gate-prompts.md:663`).
- **Importance** — HIGH - An unvalidated activation loop can deliver incorrect UV dose and silently compromise data or the sample.
- **Where** — RIG:M5 - M5 has the MicroFPGA trigger and real illumination path; the demo has neither lasers nor real optical feedback.
- **Block** — NONE - Completed Block 4b supplied only the generic bounded-numeric prerequisite, not the closed-loop UV test.
- **Effort** — LARGE

<details><summary>The original row, verbatim (`design/35` lines 10410–10411)</summary>

```markdown
- **UV activation closed-loop test.** Depends on the future pulse-duration
  capability.
```

</details>


### R56 — Block 9b cross-rig inventory gate, four limbs

**Validate that rig inventory handles real driver failures, secrets, live groups/state labels, and bridge-native values correctly on production hardware.**

- **Status** — OPEN - The register still explicitly leaves all four limbs owed at design/35-usability-and-pfs-checklist.md:10348, and the redaction regex still omits names such as Passphrase, Community String, and Login at microclaw/rig_inventory.py:54.
- **Importance** — MEDIUM - Incorrect inventory can mislead safety-profile setup, but schema validation limits propagation and no current wrong-hardware action is established.
- **Where** — RIG:M5 - The demo cannot provide production-driver failures, credential vocabulary, or live serial/FPGA bridge shapes.
- **Block** — Block 9b cross-rig inventory gate - This existing block explicitly owns all four remaining limbs.
- **Effort** — MEDIUM

<details><summary>The original row, verbatim (`design/35` lines 10348–10355)</summary>

```markdown
- **Block 9b cross-rig inventory gate, four limbs.** Live M5 owes real
  enumeration failures, credential redaction, live config groups and state labels
  beyond `.cfg` contents, and bridge-typed returns. **Explicitly does not block
  block 4** — that block pins a supported schema version instead. Keep the
  credential-redaction limb: redaction keys off a property-*name* regex
  (`microclaw/rig_inventory.py:39`), so off-rig tests prove the substitution
  fires, not that the vocabulary matches real driver naming (`Passphrase`,
  `Community String`, `Login`).
```

</details>


### R09 — M5's empty Core shutter

**Live-confirm the optical-path inventory correctly handles M5 returning an empty Core shutter assignment.**

- **Status** — OPEN - Design/59 item 28 remains unchecked at design/59-orientation-must-name-the-optical-path.md:975; only off-rig coverage exists at tests/test_optical_path_state.py:396.
- **Importance** — LOW - The branch is already implemented and tested, so this is opportunistic hardware confirmation with no known behavioural defect.
- **Where** — RIG:M5 - The demo configuration always provides `White Light Shutter` and cannot produce M5’s empty assignment.
- **Block** — NONE - Design/59 records this as an opportunistic debt rather than an owned implementation block.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 9168–9176)</summary>

```markdown
### M5's empty Core shutter — added 2026-08-28 **(no block, opportunistic)**

`get_shutter_device()` returns an empty string on M5 rather than raising, which
is the other half of the inventory's shutter-exclusion branch and the one the
demo machine (`White Light Shutter`) structurally cannot produce. design/59's
off-rig scorer exercises it against an M5-shaped fake built from that machine's
archived inventory; a live confirmation costs one read-only run and is worth
taking if M5 is free. Not a precondition for anything.

```

</details>


### R68 — Missing NDTiff fixtures

**The design/30 spiral and 2500-tile NDTiff datasets were never retrieved from the rig, so no large multi-position fixture exists.**

- **Status** — OPEN - `tests/fixtures/` still carries no NDTiff dataset of that shape.
- **Importance** — LOW - it is a test-fixture gap, not a product defect; the dense-hypercube traps it would exercise (R31) are reproducible synthetically.
- **Where** — RIG:M5 - the datasets exist on rig machines and need copying, not acquiring. Cost is file transfer, not instrument time.
- **Block** — NONE
- **Effort** — SMALL
- **Provenance** — *coordinator-triaged, not runner-evaluated.* The Codex usage limit was reached before this row got a runner; the judgement above is the coordinator's own code read.

<details><summary>The original row, verbatim (`design/35` lines 10412–10413)</summary>

```markdown
- **Missing NDTiff fixtures** (old Block 0 `[-]`): the design/30 spiral and the
  2500-tile datasets were never retrieved from the rig.
```

</details>


## Zeiss

Needs the Zeiss. Its capabilities are only partly characterised; see `design/69b`. Check the row names a capability that rig actually has before booking it.


### R06 — probe_hint emitted for a lock with no usable status property

**The focus-lock state payload misleadingly recommends a property probe even when the device exposes only metadata properties.**

- **Status** — OPEN - `_lock_status_properties` still returns every read-only property and `probe_hint` is emitted for any nonempty result (`microclaw/tools.py:10161`, `microclaw/tools.py:10220`); design/59 item 28b remains unchecked (`design/59-orientation-must-name-the-optical-path.md:972`).
- **Importance** — MEDIUM - It gives users unusable hardware guidance, but the suggested probe is zero-exposure and has a clear manual workaround.
- **Where** — RIG:Zeiss - The accessible Zeiss may provide the needed real-lock positive case; DEMO exposes only metadata and therefore cannot validate candidate discrimination without suppressing genuine status properties.
- **Block** — NONE - The register explicitly says no block, and block 59c rewrites hardware-specific prompt guidance rather than filtering `status_properties` or `probe_hint`.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 9107–9130)</summary>

```markdown
### One from block 59a's demo gate — added 2026-08-28 **(no block)**

**A focus lock with no status property still gets a probe hint pointing at
whatever it does expose.** `get_focus_lock_state` (block 56b) returns
`status_properties` for every read-only property on the autofocus device, and
emits `probe_hint` whenever that dict is non-empty. On the demo machine's
`Autofocus` the dict is `{"Description": "Demo auto-focus adapter", "HubID": "",
"Name": "DAutoFocus"}` — nothing a probe could use — and the hint still invited
`run_autofocus` against "one of the properties above".

Measured 2026-08-28 in `block59a-demo-evidence/system-state-1.json`. It was
invisible until block 59a put `get_focus_lock_state` into the orientation
payload, where it is now read by every session on every rig rather than only when
asked for.

**Not a 59a defect** — 59a embeds that payload verbatim, which is what the design
asked for, and the values shown are exactly what makes the difference obvious.
The question is whether `probe_hint` should be emitted when no candidate property
plausibly carries a status, or should say that none does. The Nikon Ti and the
Dragonfly both have real status properties and disagree about their names, so the
hint cannot be keyed to a name — which is the same reason `_lock_status_properties`
shows values instead of choosing. Needs a rig with a real lock to settle, and the
operator has no Nikon at present.

```

</details>


### R45 — Block 13 G3 - transmitted-light SNR refusal unmeasured on any rig

**Real transmitted-light imaging has not verified that dark-on-bright fields refuse bright-signal SNR while retaining a valid focus metric.**

- **Status** — OPEN - Block 13 G3 remains unchecked at design/35-usability-and-pfs-checklist.md:6051 and explicitly carried at :10152; later Nikon brightfield work did not record this SNR criterion.
- **Importance** — LOW - The implemented rule is unit-tested and preserves Tenengrad validity, so this is an outstanding hardware-validation gap rather than a known behavioral defect.
- **Where** — RIG:Zeiss - Nikon access is gone, while Demo’s synthetic camera cannot validate classification of a real transmitted-light field; first confirm the Zeiss has a brightfield or phase path.
- **Block** — Block 13 G3 - design/40-block13-rig-gate.md:127 already specifies the transmitted-light field test, although the block merged with this limb owed.
- **Effort** — SMALL

<details><summary>The original row, verbatim (`design/35` lines 10152–10158)</summary>

```markdown
- **Block 13's G3 — the transmitted-light SNR refusal is unmeasured on any rig.**
  The rule is stated once in design/25 and covered by unit tests, and it was
  verified off-rig on a synthetic dark-on-bright field (SNR refuses,
  `focus_metric_valid` stays true, tenengrad 2.99e8). No reachable rig has a
  transmitted-light path — **M5 does not**, which is why its 2026-08-06 gate
  skipped it. Run it on the first rig that has brightfield or phase; the Nikon is
  the likeliest. Merged 2026-08-06 with this explicitly owed rather than held.
```

</details>


## Blocked on a Nikon Ti — no reachable machine

**Do not schedule these.** The operator has no Nikon Ti/Ti2. They are recorded so a future trip collects them together, not so anyone tries to substitute a rig.


### R08 — Five things owed to a Nikon Ti by design/59

**Validate optical-path diagnosis and the focus-lock procedure on a real Nikon Ti using routing-caused blank frames, Nikon labels, and live PFS state.**

- **Status** — PARTLY - Block 61c removed the Nikon prompt copy and a later Ti session confirmed `TIPFSStatus` skill routing (`design/61-skills-and-tools-architecture.md:985`), but the routing, manual-prism, turret-label, and label-matching limbs remain unchecked (`design/59-orientation-must-name-the-optical-path.md:1273`).
- **Importance** — HIGH - Unproven routing diagnosis can waste exposures and rig time on blank images, while an unvalidated focus procedure can misrepresent lock state and compromise acquisition.
- **Where** — RIG:Nikon - The demo camera ignores light-path routing and its autofocus device lacks usable status telemetry, so it cannot reproduce these Nikon stimuli.
- **Block** — Block 59c - It owns the focus-procedure gate and explicitly directs collection of the remaining Ti debts during the same trip (`design/59-orientation-must-name-the-optical-path.md:1305`).
- **Effort** — MEDIUM

<details><summary>The original row, verbatim (`design/35` lines 9145–9167)</summary>

```markdown
### Owed to a Nikon Ti by design/59 — added 2026-08-28 **(no block)**

design/59 blocks 59a and 59b are merged; five things could not be settled on the
demo machine or M5 and are **owed, not waived**:

1. A real blank field **caused by routing**, and the manual-prism configuration
   of 2026-08-23. The demo camera's frames do not depend on the light path.
2. A `4-Unknown` turret label and a live PFS status string.
3. **The label-matching source of the light-path role.** No demo or M5 device
   carries port vocabulary in its labels, so that half of `role` is fixture-only.
   The adapter source is rig-proven; the label source is not.
4. The positive half of the reference limb — *"and the documentation IS reached
   once signal is missing"*. On the demo machine a blank frame always has an
   obvious explanation (the agent names the synthetic test pattern, correctly),
   so requiring the call there would demand context it does not need. A blank
   frame whose cause is genuinely unknown is a Ti stimulus.
5. Block 59c's rewritten focus procedure, above.

M5 was ruled out for this work **from its own archived inventory** rather than
from a trip (`block4d-m5-20260803-170259/m5-inventory/inventory.json`): four
StateDevices — two filter wheels, an ELL6 slider, the laser engine — no adapter
or label routing signal, and an empty core autofocus assignment.

```

</details>


### R15 — Finding the capture band does not offer to engage the lock

**After a zero-dose sweep finds the focus-lock capture band, the proposed workflow should explicitly engage and verify the lock.**

- **Status** — OPEN - The Nikon skill describes engagement, but no reachable-rig evidence proves the model follows through; block 59c remains unstarted at design/59-orientation-must-name-the-optical-path.md:1305.
- **Importance** — HIGH - Omitting engagement leaves the focal plane unconfirmed and can produce incorrectly focused data or waste scarce rig time.
- **Where** — RIG:Nikon - The demo autofocus device has no real status property, so it cannot exercise capture-band detection, lock engagement, and verification.
- **Block** — Block 59c - Its acceptance procedure explicitly requires locate, engage, image-check, and jog at design/59-orientation-must-name-the-optical-path.md:1305.
- **Effort** — MEDIUM

<details><summary>The original row, verbatim (`design/35` lines 9375–9400)</summary>

```markdown
### Two from the Dragonfly session — added 2026-08-23 **(no block)**

Both measured on the Nikon Ti2-E / Andor Dragonfly
(`pfs-dragonfly-design56ab/`). **Neither is fixed, and neither can be tested
where the operator still has access** — both need a rig with a hardware focus
lock, which M2 and M5 are not. Do not fix them blind; this repo has already
spent three rig trips on a mechanism that was green off-rig the whole time.

**1. Finding the capture band does not offer to engage the lock.** The operator
asked twice, in two different sessions: *"Why didn't you engage the PFS when you
found it?"* `run_autofocus` deliberately does not engage — that is a hardware
write and belongs to the caller — but the plan the model proposes should carry
"sweep, engage, verify" as one procedure, since engaging is what confirms the
plane at zero dose (design/56 §8). Prompt text alone has twice failed to move
this behaviour (§9e), so a fix here should be judged against that history rather
than assumed to work.

**2. `get_device_property_info` on a stage's guessed property name errors
without naming the real ones.** Measured: `PFSOffset` / `Position` returned
`Invalid property name encountered: Position (2)`, while `get_stage_position` on
the same device works. The error is *correct* — that device has no `Position`
property — but it leaves the caller to guess again. Listing the device's actual
property names in that refusal is generic, cheap, and would help on any rig.
Testable off-rig with fakes; the reason it is parked is that it touches a shared
error path and this merge is already carrying unverified change.

```

</details>


### R22 — No typed continuous-focus capability exists

**Microclaw needs a generic continuous-focus capability that safely searches for lock and reports every axis moved by the servo.**

- **Status** — PARTLY - Later work added typed MMCore enable/read paths in `microclaw/tools.py:10203-10231` and `microclaw/tools.py:10255-10274`, but no locked-state polling, bounded engage search, or multi-axis result required by `design/35-usability-and-pfs-checklist.md:5840-5873`.
- **Importance** — HIGH - An incomplete engage/search contract can leave focus unlocked or misreport servo-induced axis motion, producing wrong focus and wasting rig time.
- **Where** — RIG:Nikon - The demo autofocus device has no real lock-status property and cannot validate binary PFS capture or servo-driven offset motion; Nikon access is indefinitely blocked.
- **Block** — Block 7a, “Typed continuous focus and the bounded engage search” - It specifies the remaining search, polling, failure, and reporting contract at `design/35-usability-and-pfs-checklist.md:5822-5873`.
- **Effort** — LARGE

<details><summary>The original row, verbatim (`design/35` lines 9520–9531)</summary>

```markdown
- **No typed continuous-focus capability exists.** `enableContinuousFocus`,
  `isContinuousFocusEnabled` and `isContinuousFocusLocked` are in the mmcorej
  2.0.3 API (`tests/fixtures/mmcorej-cmmcore-2.0.3-methods.txt`) and are still
  unused by production code, so engaging PFS from microclaw is raw property
  writes under a hand-written declaration — which is exactly what the 2026-08-18
  Nikon session did. Block 7a's section carries the measured shape if this is
  ever picked up: the loop is `Off → step Z → On → poll status`, lock is
  **binary** (`Focus lock failed` / `Locked in focus`, no gradient, do not
  hill-climb), there is **no stable engage height** (four locks spanned
  2450–2912 µm, so search, do not aim), and the servo moves the offset axis by
  itself (a lock at 2500 pulled `TIPFSOffset` from 27.85 to 183.55), so a result
  reporting only the focus axis describes half the machine.
```

</details>


### R64 — Unconfirmed SignalIO (12) and Galvo (16) bridge type ordinals

**The SignalIO (12) and Galvo (16) bridge device-type ordinals are hard-coded but were never confirmed against real hardware of those types.**

- **Status** — OPEN - both ordinals are still asserted from the header rather than measured (`microclaw/authorization.py:641`, `:645`).
- **Importance** — LOW - a wrong ordinal would misclassify a device type, but no reachable rig has a galvo or DAC, so nothing today depends on it.
- **Where** — RIG:Nikon - strictly, it needs any rig with a galvo or signal-IO device, and none of M2, M5, the demo config or the Zeiss has one. Filed here because it is blocked on hardware nobody has, not because a Ti specifically settles it.
- **Block** — NONE
- **Effort** — SMALL
- **Provenance** — *coordinator-triaged, not runner-evaluated.* The Codex usage limit was reached before this row got a runner; the judgement above is the coordinator's own code read.

<details><summary>The original row, verbatim (`design/35` lines 10406–10407)</summary>

```markdown
- **Unconfirmed SignalIO (12) and Galvo (16) bridge type ordinals** (design/33
  `:717`). Exercise on a rig with a galvo or DAC when one exists.
```

</details>

### R87 — D5's session-end rule names a field that is absent when nothing is declared

**`get_system_state` omits `declared_illumination_properties` entirely when the safety config declares none, and block 72a's session-end prompt sentence names that field without saying what to do when it is missing.**

- **Status** — OPEN, and narrow. `microclaw/tools.py:4050` is `if illumination:`, so the key is **absent**, not empty, on a config with no declarations — measured on the demo machine 2026-09-02, where the ordinary config produced no such key at all. D5's *blank-frame* sentence already handles this ("If that field is absent, there are no declarations to inspect and this adds no prompt or refusal"); D5's new *session-end* sentence does not, and says only "call get_system_state and report declared_illumination_properties".
- **Importance** — MEDIUM - the gate measured the agent generalising correctly on its own: it read `optical_path` and the shutter and reported the LED at `Closed` and the shutter closed. So this is a wording gap, not an observed failure. But it is the difference between an instruction that works because the model is sensible and one that works because it says what it means, and R51's whole complaint was an agent disclaiming access to state it had.
- **Where** — LOCAL - prompt wording plus an off-rig agent test; the demo machine's own payload (declaring nothing) and block 59b's (declaring `Dichroic.Label` via a generated `--safety-config`) are both recorded and are the two fixtures needed.
- **Block** — NONE. Deliberately not folded into 72a, which was merged on a passing gate; this is a follow-on sentence, not a defect in what shipped.
- **Effort** — SMALL
- **Provenance** — found while scoring block 72a's gate, not from the design/35 triage. The same measurement corrected design/72 §Gate's false premise that "the demo config declares illumination in its safety config".

### R88 — The probe_hint payload reached a live model and did not route it

**A cold Nikon Ti session was handed `get_system_state.focus` carrying the lock device, its status property with its current value, and the `probe_hint` naming `run_autofocus` — and still opened with an image-based sweep, dismissed the PFS in prose, and only probed after the operator named it 31 turns later.**

- **Status** — **CONTAINMENT SHIPPED 2026-09-04, MEASUREMENT OWED.** Block 74a (merged `c8405ce`) put the enforcement in code: `run_autofocus` now offers the lock before an image sweep, at zero exposures and zero Z motion, on a rig whose adapter identity is a recognised hardware surface lock — and the `probe_hint` names `load_skill("nikon-pfs")` for a PFS-labelled device, so the schema sentences' unmet antecedent (*"when get_focus_lock_state reports a PFS"*) no longer gates the routing. **This does not close the row.** The whole point of this notebook is that three interventions shipped without a measurement; 74a is the fourth, and **block 74b is the measurement that decides it**. Do not tick this until 74b has a number. Note also that the containment offers rather than blocks: the operator's correction of 2026-09-04 established that image-based autofocus on a Nikon must stay available, so a caller proceeds with `image_metric_reason`. What follows is the original OPEN text, kept because it is the evidence.
- **The measurement, and where it now comes from.** Block 74b's arm B replayed this session's own payload to a live model, n=12, on 2026-09-04. It measured **nothing**: `offer_fired` was 0 in 12 of 12, because no sample reached for an image sweep in the first place, and a harness starved of fixtures left 8 of 12 without any focus action. It was not re-run, and more samples would not help — the offer only fires after the mistake, and the mistake is rare in replay. **The question is deferred to the Nikon user's own session**, which yields the identical binary from a real sample: did `run_autofocus` carry a `probe`, or did it carry `image_metric_reason`? Ask for the history JSONL and score it with `load_history`. Reopen this row if a problem comes back from that rig.
- **Original status** — OPEN, and it is the **third** failure of the same behaviour. `design/56` §"Owed rig evidence" item 3 says the `status_properties`/`probe_hint` payload exists *because two prompt edits failed*, and that "no live model has seen this payload". One now has. `microclaw/tools.py:4043` embeds `get_focus_lock_state`'s exact return under `state["focus"]`, so the hint arrived on the session's **first tool call**, byte-identical to what `get_focus_lock_state` returned 20 calls later. The prompt rule at `microclaw/agent.py:340-349` ("BEFORE proposing an image-based sweep… propose run_autofocus with a property probe FIRST… Do not wait to be asked") was not followed; `microclaw/agent.py:338`'s ordering rule was also not followed. Nothing in code enforces either.
- **Importance** — HIGH - it spent ~20 tool calls and 10 exposures reaching a flat-metric refusal that it then read as a statement about the sample ("This spot on the sample has no cells") while Z was ~2250 µm from the capture band. On a 60× oil lens with an empty field the image metric *cannot* work and the lock is the only route, so this is the case the intervention was built for.
- **Where** — LOCAL for the measurement, RIG:Nikon to confirm. The deciding observation is whether a live model routes to the probe from a cold `get_system_state`, and this session's own payload is now a recorded fixture that can be replayed off-rig (`CLAUDE.md` §step 6, "dry-run gate prompts"). Note the register's Nikon bucket says the operator has no reachable Ti; **this session is a Nikon Ti dated 2026-09-03**, so that premise needs re-checking with the operator before four rows stay parked.
- **Block** — `design/74-the-lock-must-refuse-the-image-sweep.md`, which was opened for this row and owns it. It is **not** R15 (engage after finding the band): R15 got a *positive* observation in this same session — once the skill was loaded the model engaged the lock the moment the band was found and ran both of the skill's mandatory post-engage checks (image check, then a +10/+10 µm XY jog with a re-read of the lock, which came back `engaged: true`). The defect is entirely upstream of R15, in what routes an unprompted session into that procedure at all.
- **Effort** — MEDIUM
- **Provenance** — scored from `~/Documents/Documents - Beyonce/Projects/Micro-Claw/nikon-no-pfs-again/20260903_192740_786081_microclaw_history.jsonl`, sha256 `66525642068059f2a0ac7d46cb508edd6f04b578672d4877a35bd9ff10fa9c0b` (156 records). Not from the design/35 triage.

<details><summary>The three turns, and two secondary defects found while scoring</summary>

**Record 2** — `get_system_state`, first call of the session, returns
`focus: {"engaged": false, "device": "TIPFSStatus", "status_properties": {"Name": "TIPFSStatus", "Status": "Out of focus search range"}, "probe_hint": "To find this lock's capture range at zero exposures, call run_autofocus with probe device 'TIPFSStatus'…"}`.

**Record 3** — the model files it as scene-setting, one bullet between the
condenser and the light path: *"PFS focus lock is out of range (disengaged)."*

**Record 13** — the dismissal, immediately before the first image sweep:
*"I'll disengage worry about PFS (it's already out of range / disengaged) and
sweep around the current Z."* It read `Out of focus search range` as a verdict
about the hardware's availability rather than a report about where Z was. Z was
in fact ~2250 µm below the band, so that reading was the expected one.

`get_focus_lock_state` was finally called at record 21 — after two `StageMoveError`
sweeps — and used only to rule the PFS *out* as the cause of a stalled stage.
`load_skill("nikon-pfs")` came at record 33, and only because the operator wrote
*"check our PFS notes"*. The skill-routing sentences in `microclaw/tools_schema.py:994`,
`:2118` and `:2135` are all conditioned on "when **get_focus_lock_state** reports a
PFS", and that call had not happened — the payload which made it redundant does
not satisfy the condition as written.

**Two secondary defects, model reporting rather than code.** Both are about a
model summarising several tool results as one, which matters for gate scoring.

1. Record 81 claims *"I swept the entire climb (0 → 2210 µm, zero exposures)"*.
   It did not. The climb was five **disjoint** 100 µm probe windows —
   213–313, 713–813, 1213–1313, 1711–1811, 2111–2211 — with blind 400 µm
   `move_stage_z` jumps between them: ~23% of the travel was read.
2. Record 85's post-hoc explanation, *"my coarse 3 µm steps had stepped right
   over a narrow capture band"*, is also wrong. Z ≈ 2340 was never inside any
   coarse window; the last one ended at 2210.7 and the next call jumped straight
   to 2400. The band was missed by the gaps, not by the step size. A property
   sweep spends no exposures, so one call could have spanned the whole travel.

</details>

### R89 — The PFS offset fine-tune is a hand-driven loop with no tool

**`run_autofocus` sweeps only the core focus device, so the post-engage image-metric fine-tune on `TIPFSOffset` — the second half of every Nikon focus session — is done by hand, one model round trip and one snap per plane.**

- **Status** — OPEN. `run_autofocus` has no named-stage axis (`microclaw/autofocus.py` drives `core.set_position`/`core.get_position` throughout), and its engaged-lock branch refuses outright — correctly, for core Z, since a sweep would fight the servo. Sweeping the *offset* with the lock engaged is the opposite: it is what the lock is for. So the workflow has no tool and the model improvises.
- **Importance** — MEDIUM. It costs an exposure and a round trip per plane, and it is worse than a sweep: measured across three sessions the trajectory **backtracks and re-visits**. `pfs-nikon` 2026-08-22 went 145.4 → 140.4 → 135.4 → 150.4 → 155.4 → 160.4 → 165.4 → 170.4 → 165.4 → 155.4 → 160.4 → 165.4, hitting 165.4 three times, `snap_and_analyze` between every move; `nikon-no-pfs-again` 2026-09-03 went 140 → 120 → 130 → 150 → 140 and ended where it began; `pfs_fix` 2026-08-05 stepped 163.325 → 169.325 by raw property write. A one-call sweep would bring the metric curve, the contrast check and the edge-peak guard that `run_autofocus` already has, and design/56 measured what collapsing a hand-driven loop into one call recovers.
- **Where** — LOCAL to build, RIG:Nikon to confirm. The three histories are recorded and are enough to specify it; no rig is needed to write it.
- **Block** — NONE yet. Deliberately excluded from `design/74` by the operator, 2026-09-04: *"we don't want to adjust any of the behaviour surrounding the TIPFSOffset in this block."* 74a is about routing to the PFS, not about what happens after it engages.
- **Effort** — MEDIUM. A named-stage axis for `run_autofocus`, which must **not** inherit the engaged-lock refusal when the swept axis is the offset stage, plus the emitter, the schema and the bounds check that `move_named_stage` already applies.
- **Provenance** — found while scoring `design/74` block 74a, not from the design/35 triage. It is **not** R88: R88 is about reaching the lock at all; this is about the fine-tune that follows once it is engaged.


### R90 — arrival_unverifiable is saturated for every image-autofocus sweep

**Every plane of a default autofocus sweep reports `arrival_unverifiable`, so the field carries no signal in the one place it is reported most.**

- **Status** — OPEN, and **not a defect**: design/66 defines `unverifiable = |target - start| <= max(2.0, 0.1 x displacement)` (`microclaw/controller.py:71`, `STAGE_MOVE_RESPONSE_BAND_UM = 2.0`), which is the honest report — a move smaller than the response floor cannot be told apart from a stage that did not move. But `run_autofocus`'s default step is 0.5 um at 20x, and any step at or under 2 um makes the condition true on **every** plane. Measured on the demo machine, 2026-09-04, both arms of block 74a's gate: `arrival_unverifiable_count: 5` of 5 planes at a 1 um step, while `measured_z_positions` matched `z_positions` exactly.
- **Importance** — LOW. Nothing is wrong and nothing is unsafe; a reader who does not know design/66's definition may read a saturated count as a stage fault, which is the opposite of what it means. The question is whether a sweep should report it per-plane at all, or once, as "steps below the response floor cannot be verified individually".
- **Where** — LOCAL. The arithmetic is in the repo and the measurement is recorded; no rig needed.
- **Block** — NONE.
- **Effort** — SMALL
- **Provenance** — found while scoring block 74a's round-2 demo gate, from the artifact rather than the verdict. Both arms agreed, so it is pre-existing and not 74a's.


### R94 — D4 records acquisitions, so a snap in the Core log still cannot be attributed

**Block 75a's correlation id reaches every acquisition and nothing else, so the forensic question that motivated it — which MicroClaw call does this snap in Micro-Manager's Core log belong to? — is still open.**

- **Status** — OPEN, and **not a defect**: `design/75` D4 says "Record per acquisition" and that is exactly what shipped. But D4's own motivation sentence says the id exists "so a snap in the Core log can be matched to a call — the gap that made finding 5 unresolvable", and it does not do that. Measured on M2, 2026-09-05: block 75a's arm made 20 `snap_and_analyze` calls and one `set_exposure`, and the acquisitions file contains **zero** records for any of them, because neither tool reaches `_acquire_with_hooks`. `design/75` finding 5 — no snap in the 2026-09-04 Core log could be assigned to a tool call — would still be unresolvable today.
- **Importance** — MEDIUM. It does not affect any acquisition or any bound. It matters the next time an incident has to be reconstructed from a Core log, which is the situation that produced `design/75` in the first place.
- **Where** — LOCAL. `execute_tool` already receives the tool-use id (`tools.py`), so emitting a lifecycle record for camera-touching tools is plumbing, not new machinery. The design question is which tools qualify and whether D4's "per acquisition" contract should widen — that is a decision, not a discovery.
- **Block** — NONE. Deliberately **not** folded into 75b, which must not grow.
- **Effort** — SMALL
- **Provenance** — found while scoring `design/75` block 75a's M2 arm from the artifact, 2026-09-05: the file's record count did not match the number of tool calls the arm had made.


### R95 — Does live-mode ownership churn precede a lost terminal notification?

**`design/75` D5's reproduction matrix, kept out of block 75a on purpose: four arms of 100 one-frame acquisitions to discriminate whether live-mode ownership churn is what precedes a lost pycro-manager terminal notification.**

- **Status** — OPEN. `design/75` §"Medium confidence: live-view ownership may be the upstream trigger" records the observation — the Core log shows effectively unbounded Andor live sequences stopped around snaps and restarted, then a pycro-manager acquisition takes camera ownership — and rates it a reproduction variable, not a cause. Block 75a's M2 arm ran 20 acquisitions in that exact shape (a `snap_and_analyze` before each) and **20 of 20 returned**; at the observed 1-in-14 rate P(zero failures in 20) = 0.23, so that is not evidence either way.
- **Importance** — MEDIUM. Nothing depends on the answer: `design/75` explicitly forbids changing live-mode handling unless this matrix discriminates an arm, and the bound in 75b is written to hold whichever way it goes. It matters only if the hang keeps recurring.
- **Where** — **M2**, and it is the expensive one: four arms of 100 one-frame acquisitions with unique dataset names, D4 timestamps, and a thread dump of `microclaw-acq-teardown` plus pycro-manager's notification and storage threads preserved before any restart.
- **Block** — NONE. `design/75` D5 removed it from block 75a deliberately: four arms of 100 for a medium-confidence variable is more instrument time than a bound needs.
- **Effort** — LARGE
- **Provenance** — carried from `design/75` §D5, which asked for it as a register row rather than a block; the id is `R95` and not D5's suggested `R91` because `R91`–`R93` were taken by `design/74`'s Nikon confirmation in the meantime.


## Blocked on someone else — not schedulable here

Not blocked on hardware and not blocked on us. Recorded so the dependency is
visible; do not put one of these on a checklist.

### R85 — SMAPpy's publisher-owned precondition list

**Six preconditions, signing included, that SMAPpy's own publisher must meet before it could be admitted as the first conformance package — none of them ours to schedule.**

- **Status** — OPEN, and deliberately not ours. `design/71` §"Feasibility against SMAPpy 0.1.0" enumerates them.
- **Importance** — MEDIUM - it blocks admitting SMAPpy specifically. It does **not** block the generic work, which is why R83 asks for a fixture package instead.
- **Where** — EXTERNAL - the publisher's release process. Nothing in this repository advances it.
- **Block** — NONE, and none should be opened. If a checklist ever appears to depend on this row, the dependency is wrong: route it through R83.
- **Effort** — n/a
- **Provenance** — carried from `design/71` §"Register rows this leaves behind", not from the design/35 triage.
