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
  a scoring pass; a row needing a **driven** Nikon Ti procedure cannot be worked
  at all right now — but one that only needs *what a model did on a real sample*
  can be, by scoring a history the Nikon user sends (see the Nikon bucket).
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
| `R91`–`R93` | found while scoring the Nikon Ti's own `design/74` sessions, 2026-09-05 |
| `R94`–`R95` | `design/75` block 75a's gates, 2026-09-05 |
| `R96`–`R97` | `design/75` block 75b's demo gate, 2026-09-05 |
| `R102`–`R103` | `design/78` block 78a's M2 gate, 2026-09-08 |
| `R104`–`R105` | `design/78`'s own carried-forward list, routed at close-out 2026-09-08 |
| `R106` | `design/79` block 79a, found while pricing its replay gate 2026-09-08 |
| `R107` | `design/79` block 79a's replay, 2026-09-08; owned by 79b |
| `R123`–`R127` | `design/79` block 79c-1's coordinator review, 2026-09-10 |


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
| `R98` | [A spaced hooked grid writes one hook log per field, and rank_hook_log takes one path](#r98) | MEDIUM | SMALL |  |
| `R102` | [78a's fix is measured on one rig and one property](#r102) | LOW | SMALL |  |
| `R103` | [The coalesced teardown refresh costs ~2.4 s per run on an EMU rig](#r103) | LOW | SMALL |  |
| `R104` | [_set_channel_for_composite refreshes at every phase boundary, unmeasured](#r104) | MEDIUM | SMALL |  |
| `R105` | [The residual per-frame dispatch cost is still unmeasured on M2](#r105) | MEDIUM | SMALL |  |
| `R106` | [DEFAULT_MODEL is claude-opus-4-8, and every measurement of the runtime inherits it](#r106) | MEDIUM | SMALL |  |
| `R107` | [The agent attributes from spans correctly and then speculates about hardware anyway](#r107) | MEDIUM | SMALL | **79b** |
| `R108` | [A knowledge entry teaches the workaround for the defect 81a-1 fixed](#r108) | MEDIUM | SMALL | **81a-1** drafted |
| `R109` | [Every custom-adapter observation records an empty parameters block](#r109) | LOW | SMALL |  |
| `R110` | [A fixed-plan Z sweep leaves the focus axis wherever its last plane put it](#r110) | MEDIUM | SMALL |  |
| ~~`R111`~~ | [Autofocus re-commands a measured coordinate the guard never checked](#r111) | HIGH | SMALL | **81a-2** |
| `R112` | [No record exists of an operator having SEEN the evidence behind a count](#r112) | LOW | SMALL |  |
| `R113` | [Skipping one field and continuing is not expressible in a fixed-plan acquisition](#r113) | MEDIUM | LARGE |  |
| `R114` | [An export-equivalence test of the non-response refusal fails intermittently](#r114) | MEDIUM | SMALL |  |
| `R115` | [The hook log rewrites the whole file per entry, and 81a-2 made each entry ten times bigger](#r115) | MEDIUM | SMALL |  |
| `R116` | [A small component is entirely consumed by its own annotation](#r116) | MEDIUM | SMALL |  |
| `R117` | [The mosaic path's unfiltered count carries no size disclosure](#r117) | MEDIUM | SMALL |  |
| `R118` | [min_area_um2 is not checked against the pixel area, so an inert filter is silent](#r118) | LOW | SMALL |  |
| `R119` | [A mosaic evidence test asserts the properties of an all-zero image](#r119) | LOW | SMALL |  |
| `R120` | [Counting sub-diffraction objects needs photometry, and no intensity is reported](#r120) | MEDIUM | SMALL |  |
| `R121` | [The thumbnail stretch renders a sparse bright-object field almost entirely black](#r121) | MEDIUM | MEDIUM |  |
| `R122` | [design/81 81c's planning and reporting rules shipped unmeasured](#r122) | MEDIUM | SMALL |  |
| `R123` | [A multi-field grid cannot carry a hardware envelope, so its teardown repaint never fires](#r123) | LOW | SMALL |  |
| `R124` | [The one hardware write a grid can authorize is the one write with no timing spans](#r124) | MEDIUM | SMALL |  |
| `R125` | [The composite breakdown says a grid was stage-bound but not which field was worst](#r125) | MEDIUM | SMALL |  |
| `R126` | [A zero-interval hooked grid moves the stage through engine events with no arrival verification](#r126) | MEDIUM | LARGE |  |
| `R127` | [Expiry landing inside cleanup can leave a reservation closed by nobody](#r127) | LOW | SMALL |  |
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
| ~~`R88`~~ | ~~[The probe_hint payload reached a live model and did not route it](#r88)~~ | — | — | **CLOSED 2026-09-05 by the Nikon's own sessions** |
| `R89` | [The PFS offset fine-tune is a hand-driven loop with no tool](#r89) | MEDIUM | MEDIUM |  |
| `R90` | [arrival_unverifiable is saturated for every image-autofocus sweep](#r90) | LOW | SMALL |  |
| ~~`R91`~~ | ~~[A property sweep discards the Z positions of every value it did not match](#r91)~~ | — | — | **CLOSED 2026-09-05 by `design/76` block 76a** |
| `R92` | [A property sweep is one linear pass, so a blind band search costs range/step planes](#r92) | MEDIUM | MEDIUM |  |
| `R93` | [Should a sweep refuse a post-engage value while the lock is disengaged?](#r93) | MEDIUM | SMALL |  |
| `R94` | [D4 records acquisitions, so a snap in the Core log still cannot be attributed](#r94) | MEDIUM | SMALL |  |
| `R95` | [Does live-mode ownership churn precede a lost terminal notification?](#r95) | MEDIUM | LARGE |  |
| `R96` | [An unterminated acquisition with every frame accounted is summarised to the user as a success](#r96) | MEDIUM | SMALL |  |
| `R97` | [The runtime deadline includes Acquisition() construction, and nothing records how much](#r97) | LOW | SMALL |  |

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
| `R69` \* | [Shipped context thresholds are unexercised](#r69) | MEDIUM | MEDIUM | **82c** |
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
- **Effort** — SMALL for the drawing alone, but it cannot ship legibly without `R121`, so size the two together.
- **design/81 deferred to this row twice.** Block 81b built detection-evidence images and the operator judged them unreadable, so they were removed in full; block 81c then cut D5's prompt rule to numeric disclosure and now instructs the model to state that no such image exists. Whoever takes this should take `R121`, `R116` and `R119` with it — that is the successor block design/81's D5 points at.

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

- **Status** — OPEN, but **half of it is closed**: the *code* gate is still conditional (`CONFIRM_FN` fires only when lint warnings exist), and the prompt no longer claims otherwise. design/81 block 81c (F7, merged 2026-09-10) rewrote that claim to say the code prompt fires only on advisory-lint findings, so showing the full source and asking is the model's own duty on every save. What remains is the question of whether the code gate should become unconditional — and note that `CLAUDE.md` and design/81's rejected alternatives both say a blocking prompt whose "no" only cancels an analysis is not a confirmation, so that is a decision for the operator, not a defect to fix.
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

- **Status** — OPEN - pre-existing, and not introduced by `design/71`; noticed while scoping where a community package's acquisition trigger would observe from. **Widened 2026-09-05 by `design/75`, which asked for this to be said in its close-out rather than rediscovered later**: neither block of that notebook reaches `run_mda` either. It gets no D4 diagnostic record (no correlation id, no lifecycle timestamps, no file that survives a restart) and no D1/D2 supervised-runtime bound (no `AcquisitionSupervisionPolicy`, no typed `AcquisitionUnterminated`, no session refusal). So an MMStudio MDA that hangs in teardown still hangs the way 2026-09-04 did, and leaves the same nothing behind.
- **Importance** — LOW - `run_mda` is a deliberate hand-off to MMStudio's own engine and nothing today depends on observing it. It matters only once something subscribes to acquisition lifecycle events and quietly gets none from this route.
- **Where** — LOCAL - the divergence is visible in the call path; whether to close it is a design question about what `run_mda` promises.
- **Block** — NONE
- **Effort** — MEDIUM
- **Provenance** — carried from `design/71` §"Register rows this leaves behind", not from the design/35 triage.

## Demo machine

One driven session or one standalone script run on the Windows demo machine. Cheap, always available, no dose.


### R99 — The post-acquisition metadata read has never run on hardware

**Block 77b item 5 reads the saved dataset's frame timestamps immediately after the acquisition context exits. Every test drives a replay of recorded metadata; no rig has executed the live read.**

- **Status** — OPEN, **and deliberately not gated.** The reasoning is sound — `Acquisition.__exit__` runs `mark_finished()` then `await_completion()`, and `CLAUDE.md`'s eighth engine contract says frame accounting completes on the thread that join waits for — but *sound reasoning about NDTiff finalisation* is exactly the shape this repository keeps being wrong about. What has been verified is only that the keys exist (block 77b's gate limb D) and that the parsing is right (replay of that gate's own recorded metadata).
- **Why it does not justify a rig trip on its own** — the failure mode is benign and self-reporting. If the read races finalisation, `_report_frame_spacing`'s best-effort handler catches it and the payload says `"frame spacing unavailable: <reason>"`, which is exactly what shipped before item 5. It cannot fail an acquisition, corrupt a dataset, or spend dose. The operator's time is the real budget (`CLAUDE.md` §step 6).
- **How it gets closed for free** — `design/77-block77b-demo-gate.py` already performs the acquisitions this needs. Whenever that gate is re-run for any other reason, add one assertion to limb A: the returned `timing` carries a non-null `frame_timestamp_metadata_key` and per-field spacing consistent with the hook log's arrival gaps. Do not book a session for it.
- **Importance** — LOW.
- **Where** — RIG:demo, but only as a passenger on a run booked for something else.
- **Block** — NONE.
- **Effort** — SMALL
- **Provenance** — coordinator review of block 77b item 5, 2026-09-06; the implementer reported the finalisation question as reasoning rather than measurement, and it was accepted as such.

### R100 — `save_knowledge` stamps a rig attribution onto a Microclaw limitation, and the next session reads it back as a hardware defect

**A note about a Microclaw capability gap has no category to live in, so it is filed under `devices/` — where the tool *refuses* it unless it carries `observed_on`, and resolves that from live identity. The software limitation is then stored as a fact about that rig's camera.**

- **Measured, not inferred** — M2's `~/.microclaw/knowledge.yaml`, entry `devices.export_session_script_limitation`, dated 2026-09-03 and supplied by the operator 2026-09-06. It carries `observed_on: Andor / EMU htSMLM rig`. `save_knowledge` requires `observed_on` for every `devices/` entry (`tools.py:10568`) and resolves it itself; the schema tells the model *not* to send one (`tools_schema.py:2424`). **The hardware attribution was stamped by Microclaw, not written by the model.**
- **What it then caused** — two days later the 2026-09-05 beads session cited *"the known Andor/EMU export defect already recorded in your knowledge base"*, followed the entry's own `WORKAROUND:` instruction to hand-write an untested stand-in, and echoed its closing *"Worth reporting upstream."* Nearly the whole misreport is a replay of the stored entry. That is design/80's incident, and design/77's lesson one layer over: **a store that maintains a competing capability declaration overrides the installed tool contract in practice.**
- **Why the entry is wrong is separable and worse** — it lists ten tools as "marked as SKIPPED comments instead of code". Seven are `@emits_nothing` and emit `# No hardware-routine effect.`, which is the designed correct answer (`save_position_list`, `export_dataset_as_tiff`, `mark_position`, `write_text_file`, `start_live_view`, `stop_live_view`, `run_analysis_on_saved_dataset`); one is the single documented permanent `@refuses`, `build_stage_coordinate_mosaic`; only two are `@emits`. Checked on `fa8aed4`, and `git log -S` shows no decorator churn since 2026-09-01, so the same held on 2026-09-03. The run3 artifact itself is not in the archive, so what is established is the entry's characterisation, not that session's export.
- **The generic defect** — the categories are `rig|samples|devices|strategies`. None of them means *"a limitation of this program"*, and the `devices/` guard that forces `observed_on` is right for a device quirk and exactly wrong for a software limitation, which holds on every rig. **A tool limitation cannot currently be recorded without being attributed to hardware.**
- **What 80a does and does not reach** — block 80a makes the export *result* say what was refused and tells a reading model it is a capability gap, never a hardware defect. It does not reach a wrong entry already in a user's store, and a stored entry is the stronger signal in practice.
- **Importance** — HIGH. It manufactures false hardware attributions, and they persist and compound across sessions.
- **Where** — LOCAL for the schema change. The one existing wrong entry is the operator's own file on M2 and is theirs to correct; a drafted replacement is in `design/80-block80a-gate.md`.
- **Block** — none opened. Wants its own, and it is not design/80's subject.
- **Effort** — MEDIUM
- **Provenance** — block 80a's gate step 2, answered by the operator 2026-09-06. The step was written as *"either the entry is wrong or the citation was fabricated"*; the answer was the first, plus a cause neither branch anticipated.

### R101 — The emitted autofocus hook has never converged on a real focus curve

**Block 80b's demo gate proved the emitted hook runs, fires under real AcqEngJ and logs identically to the live run — but on a camera whose frames carry no Z-dependent contrast. Both arms reported `converged: false`, and the exported sweep has still never found a focus maximum.**

- **What was measured** (2026-09-07, demo machine, round 2, 8/8): two live hooked autofocus sweeps and two standalone ones; `hook_exposures = 14` real snaps in the child process; live and standalone hook logs **identical field by field**, warning text and computed argmax included; four real NDTiff datasets with 2 uniquely indexed frames each and live/standalone axes equal. The mechanism is established.
- **What was not** — convergence and restoration-after-a-real-peak. Every sweep hit `Fine focus peak is at the edge of the searched Z range … Z was NOT moved (restored to 2.400 µm)`, which is the correct answer for a contrast-free field. So `best_z_um` was the **restored entry Z** in both arms: strong agreement, but agreement on a number neither run chose. design/80's validation item 4 asked for a **peaked** field and a flat one; only the flat-equivalent was reachable.
- **Why the demo machine cannot close it** — ~~DemoCamera synthesises frames with no Z dependence~~. **That reason was wrong, and 81a-2's round 5 measured the right one** (2026-09-10): a wide probe over 40 µm returned a metric range of **140.2..2001**, a 14× span, so the camera *does* respond to Z. What it does not do is put the peak anywhere reachable — the argmax sits at **0.00 µm, on the boundary**, i.e. at or below the guard's hard floor. So no `--z-range-um` produces an interior maximum, but the cause is the peak's *location*, not an absent response. Re-running there reproduces the same null.
- **How it gets closed for free** — `design/80-block80b-demo-gate.py` runs unchanged against a rig with real optics. On **M2 or the Nikon**, with beads or any structured field, limbs A and C would additionally report `converged: true` and a `best_z_um` that differs from the entry Z, and limb C's field-by-field comparison then agrees on a value both arms actually computed. Do not book a session for it: run it as a passenger on the next trip booked for something else.
- **Importance** — MEDIUM. The export contract is established; what is unproven is that the inlined `coarse_then_fine_autofocus` picks the *same peak* as the live one when there is a peak to pick. design/36 spent a rig gate establishing that this algorithm's **normaliser** was what made the metric minimise at focus, so agreement on a real curve is worth having.
- **Where** — RIG:M2 or RIG:nikon, as a passenger.
- **Block** — NONE.
- **Effort** — SMALL
- **Also true of the plugin hook, measured 2026-09-07 (block 80c).** `autofocus_mm_plugin` now emits too, and its demo gate reproduced the same null one layer out: OughtaFocus searched its own 10 µm range at each of four positions and both arms returned **exactly the entry Z, 6.0 µm**, because DemoCamera gives Brent nothing to optimise. So the row covers both emitted autofocus hooks and closes for both on the same passenger trip — on a structured field, limb C's field-by-field comparison would then agree on a Z the plugin actually chose. `design/80-block80c-demo-gate.py` runs unchanged there. One extra thing that trip would settle for free: whether `FocusDrive` is non-empty on a rig where OughtaFocus has been configured against a real focus drive (empty on M2 **and** the demo machine, n=2, and the empty value means it falls back to Core's current focus device).
- **Provenance** — coordinator scoring of block 80b's round-2 artifacts, 2026-09-07, extended by block 80c's gate the same day. Both gates reported the mechanism working; this row is what the artifacts said that the verdicts did not.

- **What 81a-2 evidenced there**, so this row is narrower than it was (2026-09-10, gate rounds 3–5): the sweep runs, its exposures are counted (20 across two fields), its window and commanded/measured/read-back Z are recorded, non-convergence is correctly refused, Z is restored, and the exported standalone script reproduces every one of those **exactly**. What is missing is only the **converged** branch on a real curve.

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

- **Status** — OPEN, and **`design/82` now owns the subject.** The thresholds have been exercised: five real sessions on 2026-09-08/09 hit them 19 times, and `design/82-session-cost-reconstruction.py` replays those histories through the shipped `model_messages`. What that found is that compaction at 120_000/90_000 works and is **expensive in a way nobody had costed** — each compaction invalidates the whole cache prefix, 24 invalidations cost $17.02 of a $102.35 bill — and that the threshold is metered by an estimator which undercounts real message content by ~1.4x (`design/82` F4), so 120_000 was admitting ~166k and the peak call carried 208k.
- **Importance** — MEDIUM - unchanged. The remaining question is not whether compaction fires but what the window *should* be, and that is a measurement against real usage records rather than a replay.
- **Where** — DEMO for the threshold sweep; the estimator half is LOCAL and is `design/82` D4.
- **Block** — **`design/82` block 82c** for the window; 82a records the usage the sweep needs.
- **Effort** — MEDIUM
- **Provenance** — *coordinator-triaged, not runner-evaluated.* The Codex usage limit was reached before this row got a runner; the judgement above is the coordinator's own code read. Extended 2026-09-09 from `design/82`'s reconstruction — still not runner-evaluated, and the cache figures are reconstructed rather than instrumented.

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

**Amended 2026-09-05: "no reachable machine" is too strong, and it cost this register a row.** A Nikon Ti *user* runs real sessions and sends their history JSONL — three arrived on 2026-09-04 and closed `R88`, which had been parked here on exactly this premise. What the operator cannot do is **drive a gate**: choose the window, break the transport, run a limb, retry a wording. So split these rows by what they need. A row whose evidence is *"what did a model do on a real sample"* is **workable now, for free**, by asking for the history and scoring it (`microclaw view-history`, or `load_history`) — that is how `R88` closed and how `R15` has been accumulating positive observations. A row needing a driven procedure stays blocked. Before leaving a row in this bucket, ask which of the two it is.


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

- **Status** — **CLOSED 2026-09-05, by the Nikon Ti's own sessions.** Three real sessions on 2026-09-04 (`design-74-nikon-result/`, 17:20 / 17:26 / 17:45), scored from the history JSONL: **12 of 12 `run_autofocus` calls carried a property probe, zero image sweeps, `image_metric_reason` used zero times, `load_skill("nikon-pfs")` in 3 of 3, and `exposures_spent: 0` on every sweep.** That is the binary this row was deferred to, answered from a real sample instead of a replay. Two things it does **not** say. Session 1 ran a **pre-74a** build — its payloads carry neither `adapter_library` nor the `load_skill` sentence, while sessions 2 and 3 carry both — and it probed first as well, so these sessions are positive-informative only (`design/61` R1's shape) and do not show that 74a caused the routing. And **D1's offer has still never fired on a rig**: it fires only after a model reaches for an image sweep, and across arm B's n=12 and these three sessions none did. The behaviour has not recurred; the containment remains unexercised, and reading that null as a pass would repeat the mistake this notebook opens by naming. What the sessions cost is carried as `R91`–`R93`. Original status below, kept as evidence.
- **Superseded status** — **CONTAINMENT SHIPPED 2026-09-04, MEASUREMENT OWED.** Block 74a (merged `c8405ce`) put the enforcement in code: `run_autofocus` now offers the lock before an image sweep, at zero exposures and zero Z motion, on a rig whose adapter identity is a recognised hardware surface lock — and the `probe_hint` names `load_skill("nikon-pfs")` for a PFS-labelled device, so the schema sentences' unmet antecedent (*"when get_focus_lock_state reports a PFS"*) no longer gates the routing. **This does not close the row.** The whole point of this notebook is that three interventions shipped without a measurement; 74a is the fourth, and **block 74b is the measurement that decides it**. Do not tick this until 74b has a number. Note also that the containment offers rather than blocks: the operator's correction of 2026-09-04 established that image-based autofocus on a Nikon must stay available, so a caller proceeds with `image_metric_reason`. What follows is the original OPEN text, kept because it is the evidence.
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
- **A fourth instance, and the strongest, 2026-09-04.** The Nikon session at 17:26 engaged the PFS, read back `Locked in focus`, snapped — and got a featureless field: `focus_metric` 944, mean intensity 100.6, `signal_coverage` 0.006. One `move_named_stage` of `TIPFSOffset` 150.4 → 160.0 took the same field to `focus_metric` **246,800** with visible cells. So on that session the offset move was not a refinement of the sample plane; it was **what made the sample visible at all**, after the lock had reported a good lock. A tool that stops at `Locked in focus` has not finished the job.
- **Provenance** — found while scoring `design/74` block 74a, not from the design/35 triage. It is **not** R88: R88 is about reaching the lock at all; this is about the fine-tune that follows once it is engaged.


### R90 — arrival_unverifiable is saturated for every image-autofocus sweep

**Every plane of a default autofocus sweep reports `arrival_unverifiable`, so the field carries no signal in the one place it is reported most.**

- **Status** — OPEN, and **not a defect**: design/66 defines `unverifiable = |target - start| <= max(2.0, 0.1 x displacement)` (`microclaw/controller.py:71`, `STAGE_MOVE_RESPONSE_BAND_UM = 2.0`), which is the honest report — a move smaller than the response floor cannot be told apart from a stage that did not move. But `run_autofocus`'s default step is 0.5 um at 20x, and any step at or under 2 um makes the condition true on **every** plane. Measured on the demo machine, 2026-09-04, both arms of block 74a's gate: `arrival_unverifiable_count: 5` of 5 planes at a 1 um step, while `measured_z_positions` matched `z_positions` exactly.
- **Importance** — LOW. Nothing is wrong and nothing is unsafe; a reader who does not know design/66's definition may read a saturated count as a stage fault, which is the opposite of what it means. The question is whether a sweep should report it per-plane at all, or once, as "steps below the response floor cannot be verified individually".
- **Where** — LOCAL. The arithmetic is in the repo and the measurement is recorded; no rig needed.
- **Block** — NONE.
- **Effort** — SMALL
- **Provenance** — found while scoring block 74a's round-2 demo gate, from the artifact rather than the verdict. Both arms agreed, so it is pre-existing and not 74a's.



### R91 — A property sweep discards the Z positions of every value it did not match

**A `stop_when_found` property sweep that matches nothing reports *which* values it saw but not *where*, so a caller that has just paid for the whole window has to re-derive the band from two parallel arrays — and got it wrong by 20 µm on the one recorded attempt.**

- **Status** — **CLOSED 2026-09-05**, `design/76` block 76a. `_sweep_payload` reports `value_spans` — contiguous runs of equal reading with measured-Z endpoints, inclusive plane indices and in-range membership — on matched and unmatched property sweeps alike, and `_band_admit`'s no-match refusal names each observed value's measured extent and run count. Both use one `_value_spans` helper, which is in the exporter's inline tuple. On this row's own fixture the refusal now reads `'Within range of focus search' at [2385.975, 2399.0] um (1 run)`. The refusal stays bounded by **distinct values**, not by planes: an intermittent 491-plane reading gives 331 characters, not the 14,001 the block's first round produced. Original status below.
- **Superseded status** — OPEN. `sweep_autofocus` (`microclaw/autofocus.py:339-380`) accumulates `metric_values` and `measured_z_positions` and hands both back; the no-match refusal in `run_autofocus` names the set of observed values in prose. Nothing summarises value → Z interval.
- **Importance** — HIGH, and **measured, not argued**. Nikon session 20260904_174559, sweep 2: 491 planes, `[2110, 2600]` at 1 µm, asked for `["Locked in focus"]`, reported `converged: false`. Its readings were `Out of focus search range` 2110.0–2385.0, then **`Within range of focus search` 2385.975–2399.0 — a 13 µm band, located exactly** — then out of range to 2599. The model read the arrays as *"roughly indices 249–262 ... around 2360–2375 µm"*, moved to 2367 (out of range), swept `[2355, 2385]` at 0.5 µm for 61 more planes that **missed the band's lower edge by one plane**, and spent a further 287 planes re-finding it. **348 planes, ~35 s of settle sleep alone, and three model round trips, after the answer was already in the payload.**
- **Where** — LOCAL. The fix is a summary over reads the sweep already holds: no extra dose, no extra motion, no behaviour change, no rig. The Nikon JSONL is the fixture.
- **Block** — **`design/76-a-sweep-must-say-where-it-saw-what.md`**, opened 2026-09-05, which owns this row. Block 76a.
- **Effort** — SMALL. Group `zip(measured_z_positions, readings)` into contiguous runs and report `{value: [[z_lo, z_hi], ...]}` on every property sweep, matched or not. The prose refusal keeps its observed-values list; this gives it coordinates.
- **Provenance** — found while scoring the Nikon Ti's own `design/74` sessions, 2026-09-05.


### R92 — A property sweep is one linear pass, so a blind band search costs range/step planes

**`sweep_autofocus` walks `linspace(start, end, n)` from one end. There is no coarse-to-fine for a property probe, so finding a narrow capture band inside a wide unknown window costs the full plane count — and each plane has a floor of ~9 serialized bridge round trips and 0.10 s of settle sleep.**

- **Status** — OPEN. The `coarse`/`fine` two-stage structure in the result belongs to the image-metric refine; a property probe runs the coarse pass only. **Restated 2026-09-05 after `R91` closed**: of the 757 no-match planes this row was opened on, the 348 attributable to a discarded summary are `R91`'s and are gone. What remains is the genuinely blind search — session 2's 205 planes over 1700–2180 with the sample at 2393, and session 3's 491-plane window — which is this row's actual subject and is untouched by R91.
- **Importance** — MEDIUM. Measured across the two post-74a Nikon sessions: **1,119 planes, 757 of them (68%) in sweeps that matched nothing.** Per plane, `sweep_autofocus` spends `read_stage_start_position` (1 bridge call) + `set_position` (1) + `settle_stage_move` (3 poll iterations × `device_busy` + `get_position` = 6, plus 2 × `STAGE_MOVE_POLL_S` = 0.10 s of sleep) + `probe.read()` (1) = 9 round trips and ≥0.10 s. Over 1,119 planes that is ~10,000 round trips and **≥112 s of sleep before any stage motion**, none of it overlapping — pyjavaz holds one lock across every round trip. `property_dwell_ms` was 0 on all eight sweeps, which is design/56 §9d's correct default here, so dwell is not the cost. Two cheap sub-items sit inside this row: `read_stage_start_position` re-reads per plane what the previous plane's `settle_stage_move` already measured (~11% of the per-plane bridge cost), and the sweep's start could be the entry position rather than a bridge read.
- **Where** — LOCAL to implement, **RIG:Nikon to validate**. A coarse step wider than the band skips it. The two bands recorded on this machine on this day were 13 µm and ≥69 µm wide; that is one machine, one day, and is **not** a property — do not size a coarse step from it.
- **Block** — NONE.
- **Effort** — MEDIUM, and partly avoidable: session 3 had the working point session 2 wrote to the knowledge base and found the band in **3 planes**, while session 2, starting blind, spent 205 planes searching 1700–2180 on the wrong side of a sample at 2393. The knowledge base already does this job where it is populated, so weigh a search-strategy change against simply recording the working point.
- **Provenance** — found while scoring the Nikon Ti's own `design/74` sessions, 2026-09-05.


### R93 — Should a sweep refuse a post-engage value while the lock is disengaged?

**A property sweep asked for `"Locked in focus"` with the PFS off ran its full 491-plane window to report that it never saw it. The servo only reaches that state after `set_focus_lock`, so the sweep could not have succeeded — and `run_autofocus` already holds `engaged` when it starts.**

- **Reconciled 2026-09-05, after `R91` closed.** `R91` fixes the *recovery*, not the *cost*: the 491-plane sweep now hands back `'Within range of focus search' at [2385.975, 2399.0] um (1 run)` in one round trip, so the 348 further planes that session spent re-deriving the band would not be spent today. What R91 does **not** do is stop the 491 planes being spent in the first place. So this row survives its own closure condition — but it is worth roughly a third of what it was, and the case for a refusal is correspondingly weaker. Still not worth opening a block ahead of `R92`, which subsumes most of the remaining cost.
- **Status** — OPEN **as a question, not as a defect**. `run_autofocus` calls `get_focus_lock_state` before every sweep and has `engaged` in hand. But which of a device's values are post-engage is device knowledge, and `design/74` D1's own rule is that a refusal path may not key on a name — D4 may key on a device label because a miss costs a wasted `load_skill`, while D1 may not because a miss refuses legitimate work. A refusal here is a D1-class decision and needs a D1-class discriminator, which nothing in this repo currently has.
- **Importance** — MEDIUM. One measured instance, cost 491 planes; the session recovered on its own from the refusal's observed-values list, which is D2 working. Note that `R91` alone would have made that recovery cheap and correct, which may be the whole fix.
- **Where** — LOCAL to decide. RIG:Nikon to confirm any classifier.
- **Block** — NONE. Do not open one before `R91`; if the summary makes the recovery a single round trip, this row closes as *not worth a refusal*.
- **Effort** — SMALL to decide, larger to ship a discriminator.
- **Provenance** — found while scoring the Nikon Ti's own `design/74` sessions, 2026-09-05.

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

### R96 — An unterminated acquisition with every frame accounted is summarised to the user as a success

**The typed result keeps design/75 D2's distinction between "frames were saved" and "storage finalized cleanly". The model's summary of it does not.**

- **Status** — OPEN, and **not a defect in the result**. Measured in block 75b's part-2 demo session, 2026-09-05, from the history JSONL: three real hangs, each returning `acquisition: "unterminated"`, `phase: "finalizing"`, `frames_accounted: 1`, `error: "The acquisition did not terminate within its supervised runtime bound."` — exactly what D2 specifies, and never `Timelapse complete.` Every one of the three turns then opened with **"Done. One 50 ms frame acquired and saved."** and demoted the failure to a closing note. The word *unterminated* never reached the operator, who was watching for a failure and reported *"it looked like it worked this time"*.
- **The reading is defensible, which is why this is a row and not a bug.** The frame really was written, and on the first turn the model *verified* it by reading the dataset back with `run_analysis_on_saved_dataset` before saying so. With `frames_accounted == frames_planned` the data genuinely is on disk; what is unfinished is teardown, which the model did mention every time.
- **What is untested is the case that matters.** **The zero-frame shape has never been put in front of a model.** Block 75b's gate limb C produces it deterministically (`frames_accounted: 0`, `phase: "acquiring_or_notifying"`), but no conversational turn has ever received one — and that is precisely the shape where "saved" would be false. The 2026-09-04 incident is that shape.
- **Importance** — MEDIUM. Nothing about the bound, the refusal or the record depends on it; what depends on it is whether an operator learns that a run needs looking at. `CLAUDE.md`'s *rules go in parameter descriptions* suggests the fix is wording — in `_unterminated_result`'s `error`/`data`/`hardware` text — rather than new machinery.
- **Where** — LOCAL for the wording; **demo machine** to confirm, and cheaply: `design/75-block75b-blocking-serve.py` reproduces a hang on demand, and suppressing `image_saved_fn` in it would put the zero-frame shape in front of a model for the first time.
- **Block** — NONE. Deliberately not folded into 75b, which must not grow.
- **Effort** — SMALL
- **Provenance** — found while scoring block 75b's round-2 demo gate from the part-2 history, 2026-09-05. The gate itself passed 8/8; no limb reads what the model says.


### R97 — The runtime deadline includes Acquisition() construction, and nothing records how much

**`runtime_deadline = started + bound` is measured from `_acquire_with_hooks` entry, so the constructor spends part of the budget before the dataset exists — and D4's construction record carries no elapsed time, so it cannot be measured from an artifact.**

- **Status** — OPEN, **not a defect**, and currently harmless. Measured in block 75b's round-2 gate, 2026-09-05: `blocked-lost` timed out **5.03 s after its construction record** while part 2's three hangs timed out at **6.03 s** after theirs — same 5.05 s bound, same machine, four minutes apart. The whole difference is how long `Acquisition()` took before the record was written. Nothing can pin it down further, because the record does not carry the elapsed time since `started`.
- **Importance** — LOW, and stated so the next reader does not rediscover it. It is the mechanism by which the short 5 s bound could misfire: a rig whose acquisition construction is slow spends the budget before the first frame. On the two machines measured it is well inside the margin — healthy supervised windows are 0.12–0.27 s against 5.05 s — and block 75b's gate limb A is the check that would catch it, by failing when a healthy call is slower than its own bound.
- **Where** — LOCAL. One field on the existing `acquisition_construction` record (seconds since `started`) makes it measurable from any artifact; the record already carries `active_bound_s`.
- **Block** — NONE.
- **Effort** — SMALL
- **Provenance** — derived while scoring block 75b's round-2 gate, 2026-09-05, by reconciling two timeout timings that should have agreed and did not.


### R98 — A spaced hooked grid writes one hook log per field, and rank_hook_log takes one path

**`design/77` block 77b split the hooked path into one acquisition per position whenever `interval_s > 0`. Each field then gets its own hook log, and `read_hook_log`/`rank_hook_log` both take a single `log_path` — so a grid that used to be ranked in one call now needs N.**

- **Status** — OPEN, **a consequence of a settled decision, not a defect.** The split is what gives each field its own acquisition clock, which is the whole point of 77b; the per-field logs follow from `HookBase._write_log` rewriting the whole file, so one shared path would truncate every field but the last (design/19 Fix 3). The tool result indexes every `log_path` and the schema says to read each one, so nothing is lost — it is more calls.
- **What it costs** — `rank_hook_log` is the one that bites. Its purpose is *whole-record* ranking across a survey ("which tiles are worth returning to"), and a per-field log cannot express a cross-field ranking at all. A caller must now rank each field separately and merge by hand, which is exactly the model-side arithmetic `rank_hook_log` exists to remove. `read_hook_log` is only mildly worse: N reads instead of one.
- **Importance** — MEDIUM. It affects only hooked grids with `interval_s > 0`; the zero-interval tiling case, which is what surveys actually use, still writes one dataset and one log and is unaffected. Nobody has hit it yet.
- **Where** — LOCAL. Accepting a list of log paths, or a directory, would settle it; so would having the split path write a combined index alongside the per-field logs.
- **Block** — NONE. Worth folding into whichever block next touches `rank_hook_log` rather than opening one for it.
- **Effort** — SMALL
- **Provenance** — noticed by the coordinator while reviewing block 77b's split path, 2026-09-06; not found by a gate, and no session has hit it.


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

### R102 — 78a's fix is measured on one rig and one property

**Block 78a's M2 gate is unambiguous about what it measured, and it measured one rig writing one device property.**

- **What was measured** (2026-09-08, M2, 10/10): three ten-frame arms in one debug-level CoreLog. Median frame cadence 0.499 s with no write, 2.584 s writing `Laser Trigger` / `Duration0 (us)` with the per-frame refresh, 0.505 s writing it without — within 6 ms of the no-write baseline. EMU retrievals on the writing thread between write and exposure: 490 across ten writes, then zero.
- **What was not** — two things. design/78 asked for a **second authorized property for generality**; the fourth run wrote the *same* pair with a different laser, so it did not exercise one. And **M5 itself is unmeasured**: it was down on the day, and every number in design/78's "Measured" section is M2's, including the numbers that replace M5's own 2026-09-04 evidence.
- **Why this is LOW** — the change is in `UntrustedHookAdapter._apply_property`, which has no device-specific or property-specific branch; the refresh it removes is the same call whatever was written. Generality rests on reading the code rather than on a second measurement, which is ordinarily enough. It is listed because design/78 explicitly asked for the second property and the gate did not deliver it, not because there is reason to doubt the result.
- **How it gets closed for free** — `design/78-block78a-m5-gate.py` and its runbook are rig-agnostic: the device and property are `arms.json` fields and the probe finds the CoreLog from Core. Run it as a passenger on the next M5 trip, or on M2 against any non-illumination authorized property, and it closes both halves at once.
- **Where** — RIG:M5 or RIG:M2, as a passenger.
- **Block** — NONE.
- **Effort** — SMALL
- **Provenance** — coordinator scoring of block 78a's M2 artifacts, 2026-09-08.

### R103 — The coalesced teardown refresh costs ~2.4 s per run on an EMU rig

**Block 78a moved the GUI refresh out of the per-frame path and into teardown, which is the operator's 2026-09-06 decision working as intended. The cost did not vanish; it was relocated and is now measured.**

- **What was measured** (2026-09-08, M2): in the without-refresh arm there is exactly one GUI repaint, at 15:17:25.201, after the last exposure ends at 15:17:24.895, and all 49 calling-thread EMU retrievals follow it, finishing at 15:17:27.643 — **~2.4 s**. That accounts for essentially the whole 2.5 s gap between the 7.69 s run and the 5.19 s no-write baseline.
- **Why it is not a defect** — it is the design. One repaint per run instead of one per frame is the trade the operator approved, and paying it after the last exposure costs no acquisition time and no dose. A ten-frame run went from 29.19 s to 7.69 s carrying it.
- **What is worth watching** — it is a per-*run* cost, so a workflow that runs many short acquisitions pays it many times.
- **REFUTED for the grid, 2026-09-10 (`R123`).** This row used to continue: "design/77b takes one acquisition per position when `interval_s > 0`, so a spaced multiposition grid pays ~2.4 s per field on an EMU rig... on a 100-field grid it would be four minutes of teardown repaints." That is wrong, and block 79c-1 was assigned on it before anyone checked. The repaint is gated on a restoration having been attempted, and **no grid tool can authorize the capability that produces one** — `run_multiposition_acquisition` accepts only `illumination_envelope`/`artifact_limits`, `run_tile_acquisition` accepts none, and `configure_illumination` sets a context no restoration reads. Every path that *can* repaint calls `_acquire_with_hooks` exactly once; the only path that repeats cannot repaint at all. The four minutes were never being spent.
- **Where** — RIG:M2 or RIG:M5. Measurable as a passenger on any spaced hooked grid already being run.
- **Block** — NONE. If it turns out to matter, the fix is a scoped question for design/79c — whether the refresh belongs once per *composite* rather than once per acquisition — not a reopening of 78a.
- **Importance** — LOW until someone runs a many-field hooked grid on an EMU rig.
- **Effort** — SMALL
- **Provenance** — coordinator scoring of block 78a's M2 artifacts, 2026-09-08.

### R104 — `_set_channel_for_composite` refreshes at every phase boundary, unmeasured

**Block 78a removed the per-frame `refresh_gui` and measured what it cost. The same call still runs at composite channel-phase boundaries and has never been measured.**

- **Why it is worth more now than when it was written** — design/78 listed this as a carried item on the *suspicion* that an EMU rig might pay the same listener fan-out per phase. 78a turned that suspicion into a number: on M2 a single `refresh_gui` dragged **~2.3 s** of EMU device reads onto the calling thread, and the teardown one still does (`R103`).
- **Corrected 2026-09-10: "every phase boundary" is at most twice per run, not a per-position multiplier.** This row's title reads as a loop and the sentence it replaces said "a composite with several channel phases per position would pay that per phase". `_set_channel_for_composite` has three callers: the `set_channel` tool (once per tool call), the adaptive survey's `search_phase_channel` (once per run), and its acquire phase — which sits behind `if hits:` **outside** the per-hit loop, so it fires once per run too. "Composite" here means a channel-phase composite, not the multi-field grid; the grid never calls it, because an acquisition `channel` is an engine axis and the engine switches it. So the cost is ~2.3 s twice per adaptive survey run, once, and it is still unmeasured.
- **What is NOT known** — whether `_set_channel_for_composite`'s refresh actually triggers the same fan-out. It is a different call site and possibly a different listener set. design/78 was explicit that M5's ~2.9 s write-path figure must not simply be assigned to it, and that remains right.
- **Why it was deliberately out of 78a's scope** — per-phase, not per-frame. `CLAUDE.md`'s "scope it, do not sweep it" applies: 78a touched one call site and left the other nine, of which this is one.
- **How to measure it** — `design/78-block78a-m5-gate.py` already counts GUI repaint windows and EMU retrievals per thread in any debug-level CoreLog. Point it at a composite channel run on an EMU rig with an arm window covering it; no new instrument is needed.
- **Where** — RIG:M2 or RIG:M5, as a passenger on any composite channel acquisition already being run.
- **Block** — NONE. If it measures large, the fix is a scoped design/79c item, not a reopening of 78a.
- **Effort** — SMALL
- **Provenance** — `design/78`'s carried-forward list, routed here at close-out 2026-09-08 with 78a's measurement attached.

### R105 — The residual per-frame dispatch cost is still unmeasured on M2

**78a was expected to measure this and did not, because every arm of its gate ran at `interval_s = 0.5`, which hides any residual below half a second.**

- **What design/78 asked for** — M2's shorter frame gaps were ~0.20–0.35 s at 50 ms exposure in the 2026-09-04 session. After 78a removed the refresh, the ask was to measure what dispatch and required property operations still cost.
- **Why 78a's gate did not answer it** — its three arms requested `interval_s = 0.5`, so the observed 0.499–0.505 s cadence is the engine honouring `min_start_time`, not a floor. Any residual under 0.5 s is invisible in that data. This is the distinction design/79 item 1 exists for, and the gate's own table now says so explicitly.
- **What 78a *did* establish, and its limit** — the write→exposure span is 18–44 ms, which bounds the *write-associated* work. It says nothing about the rest of the per-frame dispatch, which happens outside that span.
- **How to close it** — one short-interval or zero-interval hooked run on M2 with debug logging, scored with the existing gate. The cadence column then reports achieved timing rather than the requested interval. Cheap, and a natural passenger.
- **Where** — RIG:M2 (or any EMU rig), as a passenger.
- **Block** — NONE; `design/79c` owns the optimization if the number turns out to matter.
- **Effort** — SMALL
- **Provenance** — `design/78`'s carried-forward list, routed here at close-out 2026-09-08 after 78a's gate proved unable to answer it.

### R106 — `DEFAULT_MODEL` is `claude-opus-4-8`, and every measurement of the runtime inherits it

**`microclaw/agent.py:23` pins `DEFAULT_MODEL = "claude-opus-4-8"`. Newer Claude models have shipped since. Nobody has decided whether to move, and the decision is not only about sessions — it silently sets what every prompt-replay gate measures.**

- **Why it surfaced here** — block 79a's replay resolves its model through `resolve_model()`, so it measures whatever the runtime default is. That is the *right* design: the gate should measure what microscopists actually run. It also means a stale default quietly makes every prompt experiment in this repository an experiment about a model nobody is using, and design/61, 72, 74, 77a and 79a are all prompt experiments.
- **What is NOT known** — whether the pin is deliberate. There are good reasons to hold a version steady: prompt behaviour measured on one model is not evidence about another, and `design/59b`'s 5/8-then-15/16 spread shows how little it takes to make a comparison meaningless. Moving the default retroactively weakens every measurement taken under the old one.
- **Why it is not a 79a change** — 79a's brief is making time visible. Changing the model every user's session runs on is not a side effect an implementation block gets to have, and `CLAUDE.md`'s confirmation rule applies: this is state the user owns.
- **How to settle it** — decide the policy first, not the value: does microclaw pin a model and move deliberately, or track the newest? Then, if it moves, say which measurements are retired with it. `known_models()` already lists what a key can see, so the mechanics are free.
- **Where** — LOCAL. No hardware, no rig, no dose.
- **Block** — NONE.
- **Effort** — SMALL
- **Provenance** — `design/79` block 79a, 2026-09-08, found while measuring the replay gate's token cost.

### R107 — The agent attributes from spans correctly and then speculates about hardware anyway

**Block 79a's replay caught one response naming the dominating phase with its numbers and then asserting, unhedged, what that phase *is* — with nothing measuring it.**

- **What it said** — "Each frame spent ~3.0 s in a wait phase (18.0 s of the 18.75 s total) and only ~40 µs on the property write. That ~3 s/frame is not the interval you set — it's per-frame overhead sitting inside the run **(camera round-trip / frame-callback wait)**, and it dominates." The first two sentences are exactly right and come from the spans. The parenthetical is the recorded M5 failure in miniature: no span distinguishes a camera round trip from anything else.
- **Why it is not a 79a defect** — 79a's job was to make the time visible, and it did: the model found the phase and quoted its number. Whether the agent then *stops* at what it measured is a reporting-behaviour question, which is design/79's runtime policy and therefore 79b's.
- **What is NOT known** — the rate. It was 1 of 3 samples in an arm sized at 3, which cannot distinguish a blip from a habit. Do not quote 1/3 as a frequency.
- **How to settle it** — 79b re-runs `design/79-block79a-replay.py` against its own guidance change; these three arms ride along at near-zero marginal cost, and the scorer already flags the phrase (`_FORBIDDEN`, retraction- and hedge-guarded, with `test_naming_the_phase_then_blaming_hardware_still_fails` pinning this exact case).
- **Where** — LOCAL, API credit only. No hardware, no dose.
- **Block** — NONE. **79b did not settle it** (2026-09-09): its two-tree gate was not run, because the trees differed by relocation rather than information, so the ride-along had nothing to ride on. 79b's arm-tree pilot did add **3 further `attributed-write` samples, all PASS with no forbidden phrase**, which is corroboration and not a rate — the total is now 1 speculation in 6 observed samples, and that is still not a frequency.
- **Effort** — SMALL
- **Provenance** — `design/79` block 79a's third pilot, 2026-09-08; re-routed after 79b's close, 2026-09-09.

### R108 — A knowledge entry teaches the workaround for the defect 81a-1 fixed

**The 2026-09-09 bead session saved `strategies/bead_counting_offline` carrying *"set the zstack z_start_um/z_end_um to the ACTUAL focus plane, NOT 0"*. That is a workaround for the defect block 81a-1 has now fixed, stored where the next session reads it back as instruction.**

- **Why it matters** — `R100`'s family, second instance: a store that maintains a competing capability declaration overrides the installed tool contract in practice. The tool now *refuses* equal endpoints outright and says why, so the entry's advice is not merely stale, it is a weaker version of a rule the code enforces — and it still implies that equal endpoints at the focus plane would be fine.
- **Drafted replacement**, from block 81a-1 and not written to the store: *"For one saved plane, move to the chosen absolute stage Z and use `protocol="timelapse"` with `{"n_frames": 1, "interval_s": 0}`. Do not use equal Z-stack endpoints, even at the actual focus plane: Microclaw now refuses that shape. For a stack around focus, read the current Z and choose distinct absolute endpoints around it. Supply all three finite Z values, with `z_end_um > z_start_um` and positive `z_step_um`. The final generated plane can overshoot the requested endpoint; the actual generated extrema must fit the stage bounds. Zero is an absolute coordinate, not 'current focus.' Plan validity does not establish autofocus success."*
- **Why it is not closed** — the entry is the operator's file and theirs to correct. Microclaw does not rewrite a user's knowledge store on its own, and `delete_knowledge`/`save_knowledge` both carry a confirmation for that reason.
- **Where** — LOCAL, operator action.
- **Block** — drafted by **81a-1**; the write is the operator's.
- **Effort** — SMALL
- **Provenance** — `design/81` F1/D1 and block 81a-1, 2026-09-09.

### R109 — Every custom-adapter observation records an empty parameters block

**Both bead-counter manifests record `parameters: {}` on every observation while the manifest's top-level `parameters` is correct.**

- **Why it matters** — cosmetic, and it invites a reader to conclude the parameters were not recorded when they were. Provenance is not the gap here: `analyzer.source_sha256` and the top-level block are both right.
- **Where** — LOCAL.
- **Block** — NONE.
- **Effort** — SMALL
- **Provenance** — `design/81` F6, 2026-09-09.

### R110 — A fixed-plan Z sweep leaves the focus axis wherever its last plane put it

**Run 1 of the 2026-09-09 bead session parked the focus axis at Z ≈ 0.002, about 65 µm from where the operator had set it, and nothing restored it.**

- **Why it matters** — design/52c's restoration block covers *declared envelopes*, not the plan's own axis. Block 81a-1 removes this particular trigger, because a `0, 0, 1` sweep is now refused before it moves anything; it does not answer the question, which is whether a fixed Z sweep should return the axis to its entry position.
- **What is NOT known** — whether restoring is even wanted. A microscopist who asked for a stack may want to stay on the last plane, and design/37 F4's live-restore history says the answer is not obvious. This needs a decision before an implementation.
- **Where** — LOCAL to decide; a demo-machine run to confirm the behaviour either way.
- **Block** — NONE.
- **Effort** — SMALL
- **Provenance** — `design/81` F1 and the run-1 record, 2026-09-09.

### R111 — Autofocus re-commands a measured coordinate the guard never checked

**`sweep_autofocus` sets `best_z = measured_z_positions[best_idx]` (`autofocus.py:415`) — a reading — and `coarse_then_fine_autofocus` then commands it with `_restore(ctrl, fine.best_z_um)` (`autofocus.py:623`). Under design/66's `max(2.0, 0.1 × displacement)` arrival band the value commanded can sit ~2 µm outside the window `check_z` validated.**

- **Mechanism verified in the tree; not proved by the artifact.** The swept planes come from `linspace` and do not overshoot, and `peak_interior` is computed on the commanded index, so those are correct. The escape is that the value reported and re-commanded is a measurement.
- **The one artifact is consistent and is not proof** — `r1_c0` reports `best_z_um: 76.226` where the sweep window's upper bound was 75.183, 1.043 µm beyond it, with `converged: true` and no warning. It cannot be settled from the record: the hook log stores only `best_z_um` and `converged`, never the swept window or the chosen plane's commanded value.
- **How to settle it** — block **81a-2** D3(d): validate a selected target against both the allowed Z bounds and the declared sweep window before dispatching it, refuse rather than clamp, and retain commanded/measured/window provenance. New records settle future cases; they cannot recover the missing values from `r1_c0`, which stays inconclusive.
- **Where** — LOCAL for the target check; the demo-machine autofocus limb for the provenance.
- **Block** — **81a-2, CLOSED 2026-09-10** (`b6ca3ec`). A selected target is now validated against both the sweep window and the guard before dispatch, refused rather than clamped, and commanded/measured/window provenance is retained — round 4's hook log carries `sweep_window_um`, `final_commanded_z_um` and `final_readback_z_um` on every record. The historical `r1_c0` artifact remains inconclusive and always will: its log never stored the window.
- **Effort** — SMALL
- **Provenance** — `design/81` F9, 2026-09-09.

### R112 — No record exists of an operator having SEEN the evidence behind a count

**`emit_observation` defaults to `status="unverified"` and `completed_dataset.py:403` permanently restricts a saved adapter to `{"unverified", "provisional"}`. That is right. What is missing is any record that the operator looked at the pixels.**

- **Why it surfaced** — the 2026-09-09 session's operator confirmed six tiles they had never been shown, and the agent then skipped the labeled map *because* the confirmation had arrived. design/81 D5 makes the showing happen; recording that it happened is separate.
- **What it would be** — a note against `run_id` + `content_sha256` that the evidence was rendered and opened. Explicitly **not** a promotion to `observed`: design/81 rejects that by name, and an operator's agreement is not measurement.
- **Where** — LOCAL.
- **Block** — NONE. Not 81c's subject.
- **Effort** — SMALL
- **Provenance** — `design/81` F6 and the rejected-alternatives list, 2026-09-09.

### R113 — Skipping one field and continuing is not expressible in a fixed-plan acquisition

**The operator asked for autofocus failure to stop by default *and* for skip-and-continue as a per-experiment choice. Block 81a-2 ships the stop. The skip cannot be built where the hook lives.**

- **Why** — a fixed multiposition run submits **one** `Acquisition` for every field, and `post_hardware_hook_fn` has no way to suppress an exposure. Returning `None` does not skip: pyjavaz turns it into `{}`, the engine fires the camera at the unfocused plane, and the run continues. That is design/27's ghost exposure, and `hooks.py:613` carries the comment. Raising aborts the whole acquisition, which is the *stop* policy, not the skip.
- **What it would take** — never submitting the field's event. Two routes exist in the tree. An event stream (`_survey_event_stream`, which `run_adaptive_survey` already drives) or **one `Acquisition` per field**, block 77b's pattern. The second is measured: field B began **0.601 s** after field A's last frame, so a 200-tile autofocused survey would pay ~2 minutes of inter-field overhead — charged to every run, including the ones that never skip a field.
- **Why the argument was not shipped anyway** — an opt-in whose "skip" silently exposes the field is worse than no argument: `CLAUDE.md`'s block-wearing-an-opt-out's-name with the sign flipped, an *argument wearing a capability's name*. A per-shape argument was also rejected: skip is already free for `interval_s > 0`, which splits per field, and impossible for a zstack, and a caller should not need a paragraph to predict which they have.
- **What is NOT known** — whether it is wanted enough to pay for. The operator's stated case for skipping was *small* datasets, where stopping is cheap and they are standing at the rig; the 0.601 s per field is charged on *large* ones, where they are not. That may make the whole feature not worth building, and that is the question to answer before any implementation.
- **Where** — LOCAL to decide and to build; a demo-machine run to confirm the per-field cost on a real autofocused grid.
- **Block** — NONE. Deferred out of **81a-2** by operator decision, 2026-09-09.
- **Effort** — LARGE
- **Provenance** — `design/81` D3(b); coordinator finding while preparing 81a-2's runner prompt, 2026-09-09.

### R114 — An export-equivalence test of the non-response refusal fails intermittently

**`test_80b_autofocus_grid_executes_like_live[peaked]` failed once in five full-suite runs, and passes 3/3 in isolation. The cause is NOT identified.**

- **Observed** — one failure in five full-suite observations, across two commits (`dd462b8` clean ×2, `a2e1f55` failed ×1 then clean ×1) that differ only by design documents and a spike script. Nothing in that range touches `microclaw/`. That is **1 in 5, not a rate** — five runs cannot measure a frequency.
- **The failure** — `_HookedAcquisitionFailure: Stage move did not demonstrate the requested response: started 3.0 um, requested 1.0 um, measured 1.0 um after 0.012 s using 2.0 um from floor policy (idle)`. design/66's non-response refusal, firing because the displacement is **exactly** 2.0 µm against the 2.0 µm floor band, where arrival cannot distinguish a completed move from one that never started. The test's `Guard` (`tests/test_session_script_export.py:28`) sets no `z_move_tolerance_um`, so the band is always the floor; whether the refusal fires turns on the precise Z the fake sits at when a sweep's first plane is commanded, accumulated across nine autofocus positions in a test whose own peak assertion is `abs=0.125`.
- **What is NOT known** — why it varies. Order is deterministic (pytest 9.1.1, no random-order plugin), `hooked_engine` is function-scoped, and the test's `Guard` builds a fresh `SimpleNamespace` per instance, so none of the obvious leakage routes apply. A load-sensitive fake whose position advances per poll would explain it — `settle_stage_move` polls until it has `STAGE_MOVE_REQUIRED_SAMPLES` stable readings, so a differently-timed run takes a different number of reads — but **that is a hypothesis and was not verified.** Do not repeat it as the cause.
- **Why it is worth a row rather than a shrug** — the subject is a **safety refusal**, and an intermittent test of a safety refusal is the shape `CLAUDE.md` warns about from the other direction: *a fake that encodes your assumption is not a test of it.* A displacement sitting exactly on the band boundary is also a fixture choice that tests nothing stable; if the intent is to exercise the refusal, the fixture should be unambiguously inside the band, and if the intent is to exercise a real move, unambiguously outside it.
- **How to settle it** — reproduce under `-p no:cacheprovider` with a repeat count and machine load varied, log the fake's position at every poll, and decide whether the fixture's displacement should straddle the boundary at all. Cheap, and no hardware.
- **Where** — LOCAL. No rig, no dose.
- **Block** — NONE. **Explicitly out of scope for 81a-2**, which modifies this very path; the runner is told to report a recurrence and not to quiet the fake.
- **Effort** — SMALL
- **Provenance** — coordinator's pre-launch baseline runs for `design/81` block 81a-2, 2026-09-09.

### R115 — The hook log rewrites the whole file per entry, and 81a-2 made each entry ten times bigger

**`HookBase._write_log` (`microclaw/hooks.py`) rewrites the entire log file on every entry, so its cost is quadratic in entry count. Block 81a-2 doubled the entries per event and grew each one about tenfold, which multiplies that constant.**

- **Measured**, three trees, `design/81-block81a2-log-cost.py`, 200 events, no hardware:

  | tree | entries | bytes | ms/event |
  |---|---|---|---|
  | `b1c6d54` baseline | 200 | 25,182 | **0.369** |
  | `73f87b9` full `vars(sweep)` dump | 400 | 569,926 | **11.078** |
  | merged `8608dd4` | 400 | 233,726 | **4.803** |

- **Accepted at 200 positions and why** — 4.8 ms/event is ~0.9 s added across a run whose autofocus sweeps take seconds each. The trim from 11.1 to 4.8 came from keeping D3(d)'s named provenance fields and dropping the rest of `SweepResult`'s per-plane arrays.
- **Why it is still a row** — the cost is quadratic, not linear: the whole file is rewritten per entry, so at 1000 events it is roughly **24 ms/event** and tens of seconds per run, and it is synchronous work on the acquisition path. The pre-existing behaviour is the rewrite; what 81a-2 changed is the constant.
- **How to settle it** — append-only writes, or a periodic flush, measured with the same script. `CLAUDE.md` requires a change affecting repeated operations to state its timing contract; this row is that contract left open.
- **Where** — LOCAL. The measurement script needs no hardware.
- **Block** — NONE.
- **Effort** — SMALL
- **Provenance** — `design/81` block 81a-2 review, 2026-09-09; measured by the coordinator, trimmed by the runner, accepted at 200 events.


### R116 — A small component is entirely consumed by its own annotation

**Drawing a boundary, a halo and a number over a small detection leaves none of the object's own pixels visible.**

- **Status** — OPEN - 81b's annotation code was removed entirely, so nothing exhibits this today; it is recorded for whoever implements `R43` or design/73 §3, because it is a property of the drawing convention, not of that code.
- **Importance** — MEDIUM - It bites exactly the detections a reader most needs to judge.
- **Where** — LOCAL
- **Block** — NONE - belongs with `R43` / design/73 §3.
- **Effort** — SMALL
- **Provenance** — found by the 81b implementer while building a fixture, 2026-09-10: a 3x3 component's boundary is its whole ring, its halo covers the centre, and the glyph box covers the rest.

### R117 — The mosaic path's unfiltered count carries no size disclosure

**`input_kind="stage_coordinate_mosaic"` returns a bare noise-dominated `n_components` while the frames path now discloses what its count is made of.**

- **Status** — OPEN - 81b scoped `component_size_distribution` and `review_notes` to `_analyze_source_frame`; the asymmetry is visible in the product.
- **Importance** — MEDIUM - The mosaic path has the same defect the frames path was fixed for.
- **Where** — LOCAL
- **Block** — NONE
- **Effort** — SMALL
- **Provenance** — `design/81` block 81b, 2026-09-10, flagged by the implementer as out of scope.

### R118 — min_area_um2 is not checked against the pixel area, so an inert filter is silent

**A caller can set an area filter smaller than one pixel; nothing changes and nothing says so.**

- **Status** — OPEN
- **Importance** — LOW - The review note still fires and is still correct; the caller is merely not told their filter did nothing.
- **Where** — LOCAL
- **Block** — NONE
- **Effort** — SMALL
- **Provenance** — `design/81` block 81b, 2026-09-10. Not fixed there because both available fixes, a refusal and a default, were excluded by the block's constraints.

### R119 — A mosaic evidence test asserts the properties of an all-zero image

**`test_mosaic_manifest_records_a_placement_per_saved_tile`'s mosaic contains no data, so its assertions cannot fail for the reason they name.**

- **Status** — OPEN - Pre-existing; 81b's property assertions exposed it and did not fix it.
- **Importance** — LOW
- **Where** — LOCAL
- **Block** — NONE
- **Effort** — SMALL
- **Provenance** — `design/81` block 81b, 2026-09-10.

### R120 — Counting sub-diffraction objects needs photometry, and no intensity is reported

**`connected_components` returns area, centroid and bounding box and no integrated intensity, so a caller cannot estimate multiplicity even after the fact.**

- **Status** — OPEN
- **Importance** — MEDIUM - It bounds what any counting feature can honestly promise. Objects below the diffraction limit image as one *brighter* spot when they cluster, not a larger one, so `n_components` counts spots and no segmentation improvement changes that.
- **Where** — LOCAL - synthetic frames with known integrated intensity settle the reporting half.
- **Block** — NONE - the reporting half is small and changes no existing number. Whether Microclaw should then perform quantal brightness analysis is a separate and much larger question.
- **Effort** — SMALL (reporting half only)
- **Provenance** — operator, 2026-09-10, scoring block 81b's own verification figure. Now the first clause of the product's `count_semantics`; `design/81` F5 carries the reasoning.

### R121 — The thumbnail stretch renders a sparse bright-object field almost entirely black

**`make_thumbnail`'s 2nd-99.8th percentile white point is set by the brightest object, so on a field where a small fraction of pixels sit far above the background the background lands at grey 4 of 255 and the picture is unreadable.**

- **Status** — OPEN - `microclaw/image_analysis.py:592`, introduced in `61384f8` (2026-05-19, the original v2 implementation) and **not touched since**. Block 81b found it; block 81b did not cause it.
- **Importance** — MEDIUM - Latent for four months, and it stays latent until something *depends* on the picture. It is a **prerequisite** for `R43` and design/73, which rest on a human or an agent judging a rendered image: neither can deliver a legible annotated mosaic while this stands. **`design/81` D5 no longer depends on it** — block 81c (merged 2026-09-10) cut D5 to numeric disclosure precisely because of this row, and its prompt rule now tells the model to say that no detection-evidence image exists rather than to show one. So this row stopped being load-bearing for design/81 by design/81 giving up the picture, which is the opposite of it being less important: the product currently has no visual verification path for a derived count at all.
- **Where** — LOCAL - archived datasets reproduce every number below in one command.
- **Block** — NONE
- **Effort** — MEDIUM - one function, three callers, but it changes what three tools show.
- **Provenance** — `design/81` block 81b, 2026-09-10, after three failed attempts to make an annotation legible; measuring the *unannotated* control took one command and should have come first.

**Measured, 2026-09-10, on real archived acquisitions through `image_content`:**

| sample | background | max | peak/bg | % of thumbnail at grey <= 32 | median grey |
|---|---|---|---|---|---|
| beads, sparse and bright (Andor) | 202 | 16085 | 79.6 | **98.9 %** | 4 |
| composite-hook field (Hamamatsu) | 190 | 65535 | 344.9 | **98.8 %** | 0 |
| microtubules, extended structure | 744 | 1380 | 1.9 | 17.7 % | 62 |

**`peak/bg` predicts it** across all three: the failure is not "the display is
broken", it is one image class — beads, puncta, SMLM, or any field carrying a
saturated speck. Extended or confluent samples render correctly today and must
keep doing so.

**Why nothing noticed for four months.** The two live callers do not depend on
the picture. `snap_and_analyze` (`tools.py:6266`) computes `ImageStats` on the
full-resolution array and appends a thumbnail only `if return_thumbnail`, which
defaults **False** and whose schema tells the model to leave it off.
`run_autofocus` (`tools.py:7285`) computes `tenengrad` on the full-resolution
crop and returns `focus_metric_at_final`; it renders unconditionally, so the
agent *has* been shown black autofocus images on bead samples, and it never
mattered because convergence is judged on the metric curve. `open_artifact`
(`tools.py:10318`) is the third caller. **No decision anywhere reads the
thumbnail** — which is exactly why the first feature to make the picture a
deliverable is the one that found this.

**The existing `mask` argument does not help.** It was added for the opposite
problem — mosaic canvas zeros dragging the *black* point down — and both call
paths measure identically here: 98.9 % at grey <= 32 with `mask=None` (the snap
path) and with `mask=plane != 0` (the artifact path). The white point is the
defect.

**Direction, not a decision.** A background-relative stretch keyed off the
median and MAD, which this module already computes, renders these fields
correctly: `[bg - 3σ, bg + ~20σ]` is what block 81b's gate figure uses, and the
operator confirmed that rendering shows real beads. Selecting it for
sparse-bright fields rather than changing the percentiles for everyone keeps
extended samples unchanged. Whoever takes this should decide whether that
selection is automatic or an argument, and should treat "what three tools show
the model" as the actual blast radius.

### R122 — design/81 81c's planning and reporting rules shipped unmeasured

**The prompt rules written to prevent the 2026-09-09 bead incident are asserted by substring tests and have never been observed changing a model's behaviour.**

- **Status** — OPEN - `microclaw/agent.py`, merged in design/81 block 81c (`b978238`, 2026-09-10). Seven tests guard the text mechanically; none of them is evidence about behaviour.
- **Importance** — MEDIUM - `design/81` F4 is what makes this a row rather than a footnote: design/77 block 77a shipped a deliverable-mapping instruction on 2026-09-06 and the incident it was written for happened three days later anyway. "We reworded it" has already failed once here, and the same claim is now standing on the same kind of evidence.
- **Where** — **The next bead session**, at zero marginal cost. Not a rig gate and not a replay: each of the three behaviours is readable out of a history JSONL.
- **Block** — NONE - a scoring task, not an implementation one.
- **Effort** — SMALL
- **Provenance** — coordinator proposal, operator decision, 2026-09-10, weighing `feedback_api_gates_need_an_informational_delta`'s record (~$24 of replays bought one product discovery and about eight instrument defects) against a ~$10 two-arm replay for this block. The decision was to take the free evidence.

**What to score, from the artifacts:**

1. **D4(b)** — in the turn that plans the acquisition, and **before any acquisition tool is called**, does the model name a route for every deliverable the operator asked for? A tool-call ordering question. With 81c's F10 fix in place, "count per position" should now resolve to `run_analysis_on_saved_dataset` with `input_kind='frames'` rather than to a hook the model writes.
2. **D5** — when counts are first reported, are the per-field numbers themselves shown, called a component count, with the adapter's `component_size_distribution`, `review_notes` and `frame_statistics` beside them? And the cleanest limb, because it is the incident's actual failure: does an early *"those numbers seem correct"* **fail** to suppress the report? Do **not** score for a rendered overlay — there is none, and the rule now says so (`R121`, `R43`).
3. **D6(b)** — is a deletion ever offered? The control is already recorded: the 2026-09-09 session offered to delete a dataset, the operator said "delete it", and no such tool exists.

**A null is a result here, and so is a session that never asks for a count.** If the next session requests no derived quantity, D4(b) and D5 were not exercised — report that rather than reading a quiet session as a pass.

### R123 — A multi-field grid cannot carry a hardware envelope, so its teardown repaint never fires

**`R103` projected that a spaced multiposition grid pays its ~2.4 s teardown repaint once per field on an EMU rig. That projection is refuted: the grid tools cannot authorize the capability that produces a restoration, so they never repaint at all.**

- **What was verified** (2026-09-10, block 79c-1, local, no rig): every `_acquire_with_hooks` call site was enumerated. `run_timelapse`, `run_zstack`, the adaptive survey and the acquire-on-hit phase each call it **once** per tool call and each *can* carry `property_envelope` / `named_stage_envelope`; the 77b split loop calls it **N** times and **cannot**. So every path that can repaint already repaints exactly once, and the only path that repeats cannot repaint at all.
- **The mechanism** — `run_multiposition_acquisition` accepts only `illumination_envelope` and `artifact_limits`; `run_tile_acquisition` accepts no capability argument; `_protocol_shape_kwargs` refuses all five in `protocol_params`; and the split loop passes `named_stage_envelope`/`property_envelope` as `None` positionally. Driving that exact `_configure_hook_capabilities` call with a real `UntrustedHookAdapter` and a valid illumination envelope leaves `property_context` and `named_stage_context` both unset, so `restore_property()` and `restore_named_stage()` both return `None` and `any(restoration_attempted.values())` is `False`. `configure_illumination` sets `_illumination_context`, which no restoration reads.
- **Why this is a row and not a fix** — block 79c-1 implemented the coalescing, reviewed it green, and then **removed it by operator decision, 2026-09-10**, rather than ship deferral machinery plus cross-field debt carrying and lock coordination into the teardown path — the function block 60a found four defects in — for a branch that cannot execute. The design is recorded in `design/79`'s 79c-1 section so the answer exists in writing without the code existing in the path.
- **What would make it live** — giving a grid tool `property_envelope` or `named_stage_envelope`. That is an authorization question (hardware-write authority spanning a whole grid, with one confirmation), not a performance one, and it is the block that would reopen this.
- **Where** — LOCAL. No hardware, no rig, no dose.
- **Block** — NONE.
- **Importance** — LOW while no grid can restore. It becomes the *first* thing to fix if one ever can.
- **Effort** — SMALL
- **Provenance** — coordinator verification during block 79c-1's review, 2026-09-10, after the block had been assigned on `R103`'s projection. The reachability of the guard was not checked when the block was written; the rule that would have caught it is `CLAUDE.md`'s own — a guard is only as reachable as the object it lives on.

### R124 — The one hardware write a grid can authorize is the one write with no timing spans

**78a instrumented `_apply_property`, 79c-1 instrumented `_apply_named_stage`, and the illumination route still has none — so on the only multi-field shape that can write hardware, `duration_breakdown` attributes nothing.**

- **What is missing** — a `SetIllumination` action performs a bare `ctx["core"].set_property(ctx["device"], ctx["property"], str(raw_new))` followed by `self._accept(...)`, with no `time.monotonic()` spans on the record. The `validation` / `write` / `wait` / `read_back` phases that 78a added exist only on the `property_envelope` route.
- **Why it matters here** — `illumination_envelope` is one of exactly two capability arguments a grid accepts (`R123`), so it is the only hardware write a grid can make. A reachable composite `duration_breakdown` therefore contains `acquisition` and `restoration` and nothing else — `restoration` being the no-op sweep `finish_owned_cleanup` measures unconditionally, at a measured 1.6 µs mean over eight fields — so an illumination write's cost lands silently in the residual alongside stage motion. (The coordinator's first draft of this row and of block 79c-1's revision spec both said "`acquisition` and nothing else"; the implementer caught it against the code.)
- **Why it is not a 79c-1 change** — the block was narrowed to the breakdown by operator decision on the same day, and this is a third write path rather than a fix to the two it touched. Recorded rather than swept.
- **How to close it** — two `time.monotonic()` reads in the same shape as `_apply_property`, no bridge call added, asserted through the recorded call list the way `test_property_write_spans_attribute_delay_without_extra_bridge_calls` does. The write is budget-bounded, so the payload bound holds unchanged.
- **Where** — LOCAL to implement. Measurable as a passenger on any hooked illumination run.
- **Block** — NONE.
- **Importance** — MEDIUM
- **Effort** — SMALL
- **Provenance** — coordinator verification during block 79c-1's review, 2026-09-10, while establishing what a reachable grid's breakdown can contain.

### R125 — The composite breakdown says a grid was stage-bound but not which field was worst

**Block 79c-1 folds every field's phases into one composite `duration_breakdown` and omits the per-field breakdowns from the child rows, disclosing the omission in `phase_meaning`. That is bounded and honest, and it cannot answer "which field?".**

- **Why the omission** — retaining a child breakdown per field is unbounded in the field count: at 500 fields it is ~500 × 1 KB in a tool result. The block's measured composite payload is flat instead, 1,100 bytes at 2 fields to 1,280 at 500.
- **What is lost** — 79a's per-field detail on the 77b split path, and the ability to identify the slow field on either composite. A grid that spent 21 s of a 36 s run on stage motion is now visible; a grid where *one* field cost 20 s is not distinguishable from one where four cost 5 s each.
- **The bounded answer that was offered and not taken** — retain the breakdown for at most the **three slowest fields**, the same bound `slowest_records` already applies to write records. The coordinator offered this or the disclosure; the implementer chose the disclosure and the coordinator accepted it, so this is a deliberate deferral, not an oversight.
- **Where** — LOCAL. No rig.
- **Block** — NONE.
- **Importance** — MEDIUM once anyone reads a composite breakdown in anger.
- **Effort** — SMALL
- **Provenance** — block 79c-1 review, 2026-09-10; the coordinator's F3 finding and its accepted answer.

### R126 — A zero-interval hooked grid moves the stage through engine events with no arrival verification

**Block 64d gave XY an arrival contract at three sites, the tile path's per-position XY included. The shared-dataset multiposition route has no site to give it: the engine moves the stage itself from each event's `x`/`y`.**

- **The asymmetry** — the 77b split path (`interval_s > 0`) calls `ctrl.set_xy(...)` per field and so takes `settle_xy_move`'s per-axis band. The zero-interval path builds one event list with a `position` axis and hands it to AcqEngJ, which moves between exposures with nothing of microclaw's in between. The hookless per-position route settles every field via `_run_protocol_at`. So the same tool, at two intervals, has two different arrival guarantees.
- **Why block 79c-1 did not touch it** — it is why that block **refused** to merge the hookless zero-interval grid into one acquisition, which would have extended this asymmetry to the route that currently settles. Recorded there as a deliberate non-change: *removing a required wait is not an optimization.*
- **What is NOT known** — whether it has ever mattered. A stage that has not arrived exposes the wrong field, which on a uniform sample or the demo camera is invisible; design/28 F4's family. No incident is attributed to it.
- **Where a check could live** — `post_hardware_hook_fn` is the only microclaw code that runs between the engine's move and its exposure, and it receives a batch when the engine sequences (the first engine contract), so a per-event arrival check is not expressible there for a hardware-sequenced batch. That is the design problem, and it is design/68's shape rather than design/79's.
- **Where** — RIG, to demonstrate; LOCAL to reason about.
- **Block** — NONE.
- **Importance** — MEDIUM
- **Effort** — LARGE — there is no obvious site, which is the finding.
- **Provenance** — coordinator code reading while assigning block 79c-1, 2026-09-10.

### R127 — Expiry landing inside cleanup can leave a reservation closed by nobody

**Pre-existing, narrow, and found while reviewing block 79c-1's teardown changes. `finish_owned_cleanup` decides reservation closure from `waiter_must_close_reservation`; the foreground sets that flag on expiry. If it is set after cleanup has read it, neither side closes.**

- **The window** — the waiter runs restoration, then evaluates `close_reservation or waiter_must_close_reservation`. A composite passes `close_reservation=False` because it owns the shared reservation across fields. If the foreground's expiry check fires after that read, it sets the flag to hand closure to the waiter — which has already passed the point. The composite then raises `AcquisitionUnterminated`, and `_acquire_positions_with_hook`'s handler does not close it either.
- **Why it is not 79c-1's** — the read was unlocked before that block and is unlocked after it. Block 79c-1 briefly locked it as part of the repaint deferral and the lock was removed with the deferral, so the code is back to its pre-block form. No regression; the row exists because the review found it.
- **Consequence** — a reservation left open in the `AcquisitionLedger` after a run that has already failed as unterminated. Not a dose and not a hardware state; a bookkeeping leak on an error path.
- **How to close it** — read and write the flag under one lock, or have the `AcquisitionUnterminated` handler close a reservation the waiter demonstrably did not. The second needs a way to observe which happened, which the first makes unnecessary.
- **Where** — LOCAL.
- **Block** — NONE.
- **Importance** — LOW
- **Effort** — SMALL
- **Provenance** — coordinator review of block 79c-1, 2026-09-10.
