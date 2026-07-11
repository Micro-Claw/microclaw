# design/22 — Correction is not instruction

Two observations from the 2026-07-11 run (`20260711_141600` history), which was
otherwise the healthiest run yet: the design/21 F1 confirmation reached the
operator in the browser, the F4 `observed_on` gate held, the knowledge entry
saved, and the turn closed on its own. Neither finding blocked anything. Both
have the same shape: the interface corrected the model after a wrong call
instead of informing it before, and the correction costs round trips **every
session**, because a fresh session starts with a fresh model that makes the
same wrong call again.

## S1 — the grid acquired twice

First prompt, same as 2026-07-10: scan a 3×3 grid, report max/min per tile.
The model chose `run_tile_acquisition` with `protocol: "timelapse"`,
`n_frames: 1`, saying:

> I'll use a one-frame timelapse per tile (since the display-only "snap"
> protocol writes nothing to disk)

The user never asked for anything on disk. The timelapse ran, all nine tiles
completed — and the result carried only status and `dataset_path` per tile, no
intensity stats. The model noticed, said so, and recovered with nine
`go_to_position` + nine `snap_and_analyze` calls: 18 extra round trips, every
tile exposed twice.

This is design/20 F1's failure signature, resurrected. The comment on the snap
branch (`tools.py:1366-1369`) names the exact 18-call move+snap loop that
branch was built to eliminate, and the schema learned the lesson too — the
`protocol` property description (`tools_schema.py:740-745`) says plainly:

> 'snap' is display-only (nothing written to disk) but returns focus_metric
> and mean/min/max intensity for every position — use it to report
> per-position image statistics.

So the fact was present and the model chose against it. Two things in the
surrounding text plausibly outweigh it:

* The tool-level description (`tools_schema.py:720-729`) characterizes snap
  only negatively and *ends* on a timelapse recipe: "Not compatible with
  protocol='snap' (display-only, no acquisition images) — use
  protocol='timelapse' with protocol_params={'n_frames': 1, 'interval_s': 0}
  …". That caveat is scoped to `hook_strategy`, but nothing in its wording
  says so, and it sits one sentence after `mark_positions`. The 2026-07-10 run
  shows the misbinding happening live — its first plan said snap "can't be
  combined with marking," which is false, before self-correcting to snap. The
  2026-07-11 run repeated the same avoidance and never self-corrected.
* The knowledge base may reinforce it: the user has previously taught "snap is
  display-only; use timelapse n_frames=1 to save a single plane." Correct
  advice about *saving*, over-applied to a stats question — a feedback entry
  applied beyond its trigger, which is design/21 S4's disease in a different
  category. Unverified from this machine (the KB lives on the rig); check
  `get_knowledge` output there before treating this as cause.

## S2 — `observed_on`, learned by rejection, twice

Both 2026-07-10 and 2026-07-11 runs end the same way: first `save_knowledge`
call for a `devices/` entry omits `observed_on`, the F4 gate rejects it with
the (good) teaching message, the model adds the field and retries. One wasted
round trip per session, forever, because the requirement lives only in the
rejection: the model-facing schema (`tools_schema.py:1240-1245`) describes
categories and keys and never mentions that `devices/` entries must name the
hardware they were observed on. The validation (`tools.py:2086-2091`) is the
only place the interface says it, and validation speaks only after the model
has already guessed wrong.

## F1 — timelapse tiles should report what snap tiles report

Two fixes, and the sturdier one is not the wording.

**Make the result shape symmetric.** A per-position timelapse tile returns
`{status, dataset_path}` (`tools.py:1398-1400`); a snap tile returns
focus_metric and mean/min/max/saturated (`tools.py:1373-1387`). If timelapse
tiles carried the same stats, choosing the "wrong" protocol for a stats
question would cost nothing — robustness in the interface instead of hoping
the model reads the description the way we meant it. Open implementation
question: the acquisition engine writes frames to disk rather than handing
them back, so the stats need either an image-process hook on the acquisition
or a read-back of the saved plane; the `n_frames=1` case (the recipe the tool
description itself promotes) is the one that matters and is a single plane
either way.

**Rescope the caveat.** In both grid tools' descriptions, bind "not compatible
with protocol='snap'" explicitly to `hook_strategy` ("hook_strategy requires a
protocol that acquires; snap does not — pair hooks with timelapse
n_frames=1"), and do not let the description's final sentence be a timelapse
recipe while snap's stats capability sits two properties away. State the
choice positively once: "for per-position statistics with nothing written to
disk, use protocol='snap'."

## F2 — `save_knowledge`'s schema must state the `devices/` requirement

One sentence in the tool description (`tools_schema.py:1240-1245`), e.g.:
"Entries under 'devices' must include an `observed_on` field naming the camera
adapter they were observed with (the `adapter` field of `get_system_state`'s
camera block)." Optionally mirror it in the `value` property description. The
F4 validation stays — the schema is advice, the gate is the guarantee — but
the model should not need the gate to learn the shape of a valid entry.

## Loose ends

* The model re-flagged the bit-for-bit identical stats during this run's
  scans. Correct: the `devices/demo_camera` entry did not exist until the end
  of the session. The first genuine test of design/21 F4's recall-with-
  condition is the *next* demo-camera session, which should suppress the
  alarm while `observed_on: DCam` matches.
* If the rig's knowledge base does contain a "prefer timelapse over snap"
  feedback entry (S1), the fix is to scope that entry to saving, not to
  delete it — it is correct advice about the thing it was taught for.
