# Nikon PFS and TIZDrive findings

> **Superseded in part by `design/40-pfs-five-sessions.md` (2026-08-05).** The
> probe kit this note specifies was never run; five real microclaw sessions
> supplied better evidence. Where the two disagree about the rig, design/40
> wins. Specifically refuted here: the "configured approach position" of §139
> and §180 (four locks in one day at 2912, 2498, 2532 and 2450 µm — there is no
> single number), and the `move_named_stage` staleness table of §246–250 (that
> signature did not reproduce; the live defect is a missed target reported as
> success). Still open exactly as written: probe 0's question at §194 — no
> session has moved Z with PFS armed. It now gates nothing.
>
> **CLOSED 2026-08-18 with Track B.** PFS works from a microclaw session under a
> hand-declared safety config; no further Nikon evidence is being sought and
> probes 0–4 are retired unrun. The generic, non-Nikon defects this line of work
> found are tracked in `design/35-usability-and-pfs-checklist.md` under "The five
> that outlived Track B". This document is history.

## Scope

This note records the findings from examining:

- `260715_Nikon_iXon_Prior.cfg`
- `20260716_173625_microclaw_history_pfs_florian.json`
- the Microclaw revision that ran the session
- the current Microclaw Z-stage and named-stage implementations

The archived session began on July 16, 2026 at 17:36 CEST. The newest commit at
that time was `722184a2b1d2fa13104a6b338aea321d577c28e2`, committed at 15:35 CEST.
The Micro-Manager configuration was generated at 15:56 CEST.

## Rig configuration

The Micro-Manager configuration loaded the relevant Nikon Ti devices correctly:

- `TIZDrive` was the core focus stage.
- `TIPFSStatus` was the Nikon Perfect Focus System status/control device.
- `TIPFSOffset` was the PFS offset stage.
- During the session, `Core.Focus` reported `TIZDrive` and `Core.AutoFocus`
  reported `TIPFSStatus`.

The PFS control exposed `State = Off/On` and a read-only `Status`. Observed status
values included `Focus lock failed`, `Out of focus search range`, and
`Locked in focus`.

## What failed

The requested sequence was:

1. Raise TIZDrive to approximately 2000 um, where the immersion oil should
   contact the coverslip.
2. Turn on PFS search mode.
3. Continue raising TIZDrive while PFS remains searching, until PFS engages.
4. Focus with `TIPFSOffset`.

Microclaw could raise TIZDrive and could turn PFS on, but after every subsequent
call to `move_stage_z`, the next read of `TIPFSStatus.State` returned `Off`.
Consequently, the software could not reproduce the operator's manual procedure of
keeping PFS armed while raising the objective with the focus wheel. The operator
reported that manual movement engaged PFS at approximately 2450 um.

### The move is not established as the cause

The session concluded that `move_stage_z` disables PFS. That conclusion is not
established by the evidence, and a competing explanation fits the same
observations:

- `TIPFSStatus.FullFocusTimeoutMs` was `5000`.
- Every arm-while-out-of-range immediately reported `Status = Focus lock failed`.
  PFS never once achieved lock above the capture range.
- Every observed `On` -> `Off` transition sits at the end of a multi-second gap
  that contained a move. No read isolates the move from the elapsed time.
- One of the four data points is weaker than the others: the transition observed
  after a user interrupt was attributed in-session to the cancel, not to a
  completed move.

If the Ti controller or the NikonTI adapter clears `State` when a full-focus
search times out, then nothing about the Z move is implicated, and the operator's
focus wheel succeeds simply because a hand traverses the remaining ~400 um into
capture range within the timeout. That hypothesis must be tested before any
move/continuous-focus coordination is designed, because it would make that
coordination unnecessary. See probe 0 below.

The session eventually succeeded using a different sequence:

1. Explicitly turn PFS off.
2. Move TIZDrive to 2440 um.
3. Turn PFS on.
4. Read `TIPFSStatus.Status` as `Locked in focus`.

This proves that PFS itself, its Core assignment, and the optical reflection
were functional. What failed was arming PFS out of capture range and then trying
to travel into range under software control — whether because the move released
PFS or because the search timed out en route is exactly what probe 0 settles. It
is worth noting that this successful sequence is also the one the timeout
hypothesis predicts would work, since PFS locked on the first attempt from within
range.

## Would current Microclaw still have this problem?

Yes. The central problem remains possible.

At commit `722184a`, `move_stage_z` performed a bounds check and then issued a
raw CMMCore focus command: `set_position` for an absolute move or
`set_relative_position` for a relative move. The current implementation
(`microclaw/tools.py:342`) is byte-identical to that revision.
`MicroscopeController.set_z` (`microclaw/controller.py:552`) also issues a raw
`set_position`.

Neither path:

- checks whether continuous focus is enabled;
- coordinates the move through Micro-Manager's continuous-focus API;
- verifies that PFS remained enabled after the move;
- verifies lock state after the move; or
- restores or re-arms PFS if the Nikon adapter disabled it.

Changes made since the session substantially improve safety ranges,
authorization, and actuator classification. Those changes decide whether a
movement is allowed; they do not manage the interaction between TIZDrive and
PFS. `tests/fixtures/mmcorej-cmmcore-2.0.3-methods.txt` records
`enableContinuousFocus`, `isContinuousFocusEnabled`, and
`isContinuousFocusLocked` in the mmcorej 2.0.3 API, but production code does not
currently use them.

### `move_stage_z` never measures the position it reports

Separately from PFS, `move_stage_z` returns the *requested* target as `z_um`
alongside `"status": "Moved."`. It performs no read-back at all. The session shows
the consequence directly: the tool reported `{"z_um": 2440, "status": "Moved."}`,
and a later read measured 2462.2 um (the PFS servo holding focus).

This is the same defect class as the offset settling issue below, but more severe,
because there is no measurement to be stale. A move that the servo modifies,
clamps, or rejects is reported as a clean success. Any fix here should return
measured Z, not the target.

### Every focus-device writer, not just the two documented seams

Three further paths write the focus device with a raw `set_position`, bypassing
both `move_stage_z` and `set_z`:

- `microclaw/autofocus.py:94`, `:103`, `:115` — the sweep, the move-to-best, and
  `_restore`.
- `microclaw/hooks.py:313` — the focus-recovery jog.
- `microclaw/tools.py:2175` — the per-position Z in the tile/grid path.

(All of these are inside a `check_z`-guarded range; the gap is continuous-focus
awareness, not bounds.) Autofocus is the sharpest case: a software focus sweep
while PFS holds lock both fights the servo and duplicates what the servo already
does. These are also the paths most likely to run unattended, so a
continuous-focus policy that covers only the two obvious seams misses the
dangerous ones.

## Recommended design

Continuous focus should be a typed microscope capability rather than an
arbitrary `set_device_property` operation.

Add operations that:

- discover the configured autofocus device with `get_auto_focus_device()`;
- enable and disable continuous focus through the CMMCore API;
- report whether continuous focus is enabled and locked; and
- wait for a well-defined lock, failure, or timeout state.

Every Z-moving path should have an explicit continuous-focus policy. Possible
policies are:

- `require_off`: refuse a Z move while continuous focus is armed;
- `move_then_rearm`: disable PFS, move, re-enable PFS, and wait for lock; and
- `preserve`: use a rig-verified movement path that leaves PFS searching during
  the move.

`preserve` must not be implemented merely by re-enabling PFS after the move.
Re-arming after a move is observably different from keeping PFS searching
throughout the move and should be reported as such.

Every path listed under "Every focus-device writer" above needs a policy, not just
`move_stage_z` and `set_z`.

For initialization, a dedicated bounded operation may be safer than letting an
agent assemble arbitrary property writes and movements. It must stay generic —
driven by `get_auto_focus_device()` and a rig-profile approach position, never a
Nikon-named recipe with 2440 um baked in. Ti with PFS is as unusual as M5 is, and
rig facts belong in the rig profile and in this document, not in `microclaw/`:

1. Confirm PFS is off.
2. Move to a configured approach position.
3. Enable PFS.
4. If it does not lock, follow a configured, rig-validated search policy with a
   small step, timeout, and hard Z ceiling.
5. Stop immediately on lock or an unexpected status.
6. Permit PFS offset adjustment only after lock is confirmed.

If the observed 2440 um approach position is repeatable, the simplest reliable
policy may be to approach that position with PFS off and then enable PFS, rather
than attempting to preserve search mode during TIZDrive motion.

## Information and rig tests still required

The history contains speculation about how Micro-Manager's Stage Control panel
moves TIZDrive, but it does not establish which API call the panel uses. Before
selecting an implementation, run a controlled hardware probe that records PFS
enabled and locked state before and after each of these operations.

Probe 0 comes first, and cases 1-4 are worth running only if it comes back
negative:

0. **The null control.** Arm PFS out of range, then read `State` and `Status` at
   roughly 1, 2, 5, 10, and 30 s while moving nothing at all. This costs nothing,
   risks nothing, and if `State` falls to `Off` on its own — plausibly around
   `FullFocusTimeoutMs` — then the move was never the cause and cases 1-4 are
   measuring an artifact of elapsed time. Run it at a Z where PFS cannot lock
   (reproducing the session) and, separately, at a Z where it does lock, since a
   timeout only applies to a failed search.

1. PFS on, then an absolute `set_position` move.
2. PFS on, then a `set_relative_position` move.
3. PFS on, then a move from Micro-Manager's Stage Control panel.
4. `enableContinuousFocus(true)` compared with writing
   `TIPFSStatus.State = On`.

For each case, record:

- TIZDrive position before and after;
- PFS enabled state;
- PFS locked state;
- `TIPFSStatus.State` and `TIPFSStatus.Status` transitions;
- the time taken for each transition;
- the elapsed time between arming PFS and each read, so that a timeout can be
  distinguished from an effect of the move; and
- whether movement completed, was rejected, or was modified by the PFS servo.

Also determine whether Micro-Manager Studio or the NikonTI adapter exposes a
PFS-preserving jog operation that is different from the raw CMMCore stage
commands.

The rig profile needs reviewed values for:

- safe TIZDrive approach range and a hard upper ceiling for each objective and
  sample-holder combination;
- the correct direction convention;
- safe search step size;
- expected PFS capture range;
- search and lock timeouts;
- NikonTI adapter, Micro-Manager, and microscope firmware versions;
- repeatability and sample dependence of the 2440-2450 um engagement position;
  and
- safe and useful `TIPFSOffset` limits.

## PFS offset settling issue

The same session exposed a separate problem with `TIPFSOffset`. A
`move_named_stage` command returned the previous position as its `achieved_um`,
even though a later read showed that the stage eventually reached the requested
position. This happened despite calling `wait_for_device`.

The signature is stronger than "sometimes lagging": all three offset moves in the
session returned *exactly* the preceding target.

| requested | reported `achieved_um` | actual meaning        |
| --------- | ---------------------- | --------------------- |
| 130       | 149.475                | the pre-sweep position |
| 160       | 130.0                  | the previous target    |
| 149.5     | 160.0                  | the previous target    |

Reporting the previous *target* — not some intermediate value between old and new
— means the stage had fully settled where it was and had not yet begun moving when
the read happened. So the adapter's `Busy()` clears before motion starts; this is
not a slow-stage problem that a longer wait would fix.

The current `move_named_stage` implementation (`microclaw/tools.py:423`) still
performs only one immediate position read after `wait_for_device`. A PFS-offset
focus sweep could therefore associate an image with the wrong offset or report a
false positioning error.

The named-stage movement path should poll until the measured position is within a
configured tolerance of the target, and stable, or until a timeout occurs. The
tolerance-of-target condition has to be the gate: because motion has not started
at the first read, a stability check on its own passes immediately at the old
position, which is precisely the observed failure. Image acquisition or focus
scoring should not begin until that settling check succeeds. The rig probe should
measure the offset's actual latency, useful tolerance, and whether the adapter's
busy/wait reporting is reliable.

Note also that this stage is being driven while PFS holds lock, so an offset write
commands a servo, not just a stage: the Z drive moves in response. Settling means
the servo has finished responding, which the offset's own position read may not
capture. The probe should record TIZDrive alongside the offset.
