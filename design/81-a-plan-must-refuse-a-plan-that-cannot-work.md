# A plan must refuse a plan that cannot work

Status: **CLOSED, 2026-09-10, with one behavioural claim owed and named.**
All four blocks are merged — 81a-1, 81a-2, 81b and 81c. D3(b)'s policy was
settled by the operator (see D3(b)); 81a was split into 81a-1 and 81a-2 (see
Blocks); D5's scope was cut to what the product can honestly ask for (see
below and D5).

**What is still owed, and it is not a gate.** 81c ships the planning and
reporting rules and no replay was bought for them, so the three behaviours in
its block entry — D4(b)'s route-before-exposure, D5's report surviving an early
confirmation, D6(b)'s absent deletion offer — are **unmeasured**. They are
scored from the next bead session's history, from the artifacts the way `R88`
was. A merged block whose behavioural claim is unmeasured says so; it does not
imply the wording works. `R122` carries the row.

**What this notebook did not fix.** The rendered evidence: `R121` (the
thumbnail stretch), `R43` (the overlay), `R116` (a component consumed by its own
annotation) and `R119` (a fixture with no data in it). They belong together in
one successor block, which is what `R43` has asked for since design/43.

**D5's scope is settled, and it is smaller than the draft** (coordinator
decision, 2026-09-10, operator confirmed). 81b ships D4(a) and **not** D5's
artifact support — its detection-evidence images were built, judged unreadable
by the operator and removed in full (see the 81b block entry), and `R121` means
even a *raw* bead field renders 98.9 % black through the shared thumbnail path.
So **"show the detections" is not a thing the product can do**, and a prompt
rule that asks for it would instruct the model to claim an evidence step it
cannot take. D5 therefore ships as **numeric disclosure**: the numbers
themselves, shown per field, with the disclosure the built-in already emits, and
an explicit statement that no detection-evidence image exists. The picture stays
owed and stays in `design/70` — `R121`, `R43`, `R116`, `R119` — for a successor
that sizes it as its own block, which is what `R43` has said since design/43.
See D5 for the rewritten rule. 81c's other two halves, D4(b) and D6(b), are
unaffected.

Reviewed against the record on `main` `e03f790`: the session's three JSONL files
and four analysis manifests in
`~/Documents/Documents - Beyonce/Projects/Micro-Claw/20260909_ZM_beads`, the
tool source, and the **installed** `pycromanager.multi_d_acquisition_events`.
Every claim marked *measured* was read out of one of those. The probe output in
F1 is reproducible with `design/81-degenerate-z-scan.py`.

## Incident

A 3×3 bead survey on the Andor/EMU rig, 2026-09-09. The operator's opening
message asked for three things: a 3×3 tile with autofocus at each position, a
report on whether the beads share a focal plane, and **the bead count at each
position**.

Four failures, in the order they happened:

1. **The first `run_multiposition_acquisition` defined a Z stack with
   `z_start_um: 0, z_end_um: 0, z_step_um: 1`.** Equal endpoints are not a
   stack, and `0` is an absolute stage coordinate 65 µm from the operator's
   focus. Every position exposed at Z = 0, the autofocus hook refused its sweep
   at all nine positions, and **the tool reported
   `"Acquisition complete across 9 position(s)."`** The focus axis was left at
   Z ≈ 0.002 (measured, turn 12) and the operator's nine fields had been
   exposed for nothing.
2. **The count was planned after the dose was spent.** The agent asked four
   clarifying questions — focus window, tile step, save directory, laser — and
   none about the count. It acquired at turn 7 and discovered at turn 21 that no
   tool counts objects per tile. It then wrote a hook.
3. **The hook it wrote was wrong, and the number was reported as a result.**
   `bead_counter` max-normalised each frame before blob detection, so a bright
   bead pushed its dim neighbours below the cut. It reported 3/1/2 on the top
   row where the operator's eye counted 4/4/3, and **52 beads on an empty
   tile**. The operator caught it. A second version fixed it. The operator then
   confirmed the remaining six tiles — *"those numbers seem correct"* —
   **without having been shown them**, and the agent skipped the labeled map it
   had offered *because* the confirmation had arrived (turn 53).
4. **The agent offered to delete a dataset it has no tool to delete.** It asked
   *"Delete it, or leave it?"*, the operator said *"delete it"*, and two turns
   later it found there is no such tool. Its own account: *"I didn't check."*

The operator's decision on (4) is that this must not be fixed by adding the
tool: **Microclaw must never be able to delete data.**

## Findings

### F1 — a degenerate Z sweep reaches the engine unchecked, in five distinct shapes

`_protocol_shape_kwargs` (`tools.py:7419`) validates key *presence* and nothing
else. `run_zstack` (`tools.py:4972`) calls `guard.check_z` on `z_start_um` and
`z_end_um` and on no plane between or beyond them. Measured against the
installed `multi_d_acquisition_events`, whose planes are
`np.arange(z_start, z_end + z_step, z_step)`:

| `z_start` | `z_end` | `z_step` | what the engine does |
|---|---|---|---|
| 0 | 0 | 1 | **one plane at absolute Z = 0** — no error. The incident. |
| 65.183 | 65.183 | 1 | one plane. A "stack" with no sweep and no motion. |
| 0 | 0 | 0 | **the `z` key vanishes entirely.** `any([0, 0, 0])` is `False`, so `has_zsteps` is never set: the run images wherever the stage happens to sit, and `guard.check_z(0)` passed against a coordinate the run never visits. |
| 65 | 65 | 0 | `ZeroDivisionError` out of numpy — an unhandled third-party crash, not a typed refusal. |
| 70 | 60 | 1 | **zero events.** So does `60 → 70` at `z_step = -1`: the shape is a step pointing *away* from `z_end`, not a negative one. Measured, and this is why D1 item 1 disclaims the general claim: `70 → 60` at `z_step = -1` yields **11 descending planes**, and at `-2.5` yields 5. A descending sweep is an engine capability D1 chooses not to expose. |
| 60 | 60.5 | 1 | two planes, at 60 and **61** — one `z_step` *outside* the requested range. |

The last row is a bounded-Z escape, not a rounding curiosity. `tools.py:8754`
reads:

```python
    if sweeps_z:
        guard.check_z(shape_kwargs["z_start"])     # the planes actually visited
        guard.check_z(shape_kwargs["z_end"])
```

The comment is false on that row. `z_end` is not the last plane; the last plane
can sit up to one `z_step` beyond it, and nothing checks it against the guard.

### F2 — `z_start_um`/`z_end_um` are absolute, and nothing says so where the model reads them

`_PROTOCOL_PARAMS_SCHEMA` (`tools_schema.py:29-31`) gives the three Z keys
**no description at all**. `run_zstack`'s say `"Start Z in µm."` and
`"End Z in µm."` The agent's own diagnosis, turn 27: *"I was thinking of them as
'one plane, no stack,' and `0` as 'zero-thickness,' when they're absolute Z
coordinates."*

The ambiguity is real in the dependency and already resolved in this tree, in
one place. `multi_d_acquisition_events` reads the same arguments as **offsets**
when `xyz_positions` is passed and as **absolute** with `xy_positions`;
`tools.py:8738` documents that Microclaw deliberately uses `xy_positions` to
keep them absolute; and `run_adaptive_survey` refuses the absolute spelling
outright (`tools.py:9474`):

> `acquire_on_hit.protocol_params refuses absolute z_start_um/z_end_um; use z_offset_start_um/z_offset_end_um.`

So one tool states the distinction in a refusal and the tool the model reached
for states it nowhere. This is *rules go in parameter descriptions*
(`CLAUDE.md`) applied to the one parameter that has none.

### F3 — a per-position autofocus hook that refuses at every position still exposes every position, and the run reports success

`AutofocusHook.post_hardware_hook_fn` (`hooks.py:284-292`):

```python
        current_z = self.ctrl.core.get_position()
        z_start = current_z - self.z_range_um / 2
        z_end = current_z + self.z_range_um / 2
        try:
            self.guard.check_z(z_start)
            self.guard.check_z(z_end)
        except Exception as e:
            self.log_event(event, autofocus="skipped", reason=str(e))
            return event
```

It returns the event unmodified, so the frame is taken at the unfocused plane.
Nine positions, nine `"autofocus": "skipped"` entries reading
`Z=-10.0 µm is below the minimum allowed (0.0 µm).`, and a status string of
`"Acquisition complete across 9 position(s)."`

**The nominal window was knowable before the first exposure.** The event's
requested Z comes from plan-time arguments. The hook actually centres on
`core.get_position()` after the move, so its runtime window can differ from the
nominal one. `_plan_with_hook_dose` already interrogates the hook through
`getattr(hook, "planned_extra_exposures_per_event")`. It asks for **dose**,
but not **reach**. Both plan-time reach and runtime bounds need checking.

**The result carried evidence that its summary omitted.** Run 1 recorded
`frames_acquired: 9`, `reservation_frames_planned: 189` and
`hook_extra_exposures_planned: 180`; the nine skip logs establish that autofocus
never swept. Run 2 recorded `frames_acquired: 179` with the same success wording.
Subtracting submitted events from reservation accounting is not a general way
to recover hook exposures: submitted frames need not all be delivered. The
result needs actual saved-frame accounting and explicit hook outcomes. This is
`R96`'s family: the summary drops a distinction present in the record.

### F4 — design/77 D4 shipped for exactly this and did not fire

`agent.py`'s prompt already says, under *"Writing code is one of your
capabilities, not a last resort"*:

> During planning, map every custom analysis deliverable to an executable path:
> inspect the applicable hook/adapter contract and dependencies BEFORE
> collecting its inputs, and offer to write and attach observation hooks then.

Merged in block 77a on 2026-09-06, three days before this session. The observed
failure is that acquisition preceded resolution of the count's executable path.
The following explanation is a **hypothesis about the prompt**, not a measured
account of the model's internal reasoning:

- "Custom analysis" may not trigger for an apparently ordinary bead count.
- The `list_hooks()` ladder begins "When no pre-coded hook matches a request",
  which depends on recognising the missing capability first.
- Filing the rule under writing code may make it less effective during ordinary
  acquisition planning than a rule triggered by every requested deliverable.

The record supports testing a broader planning rule; it does not establish which
of these factors caused the failure. D4(b)'s replay tests the proposed remedy.

### F5 — the built-in measures components, but does not yet answer bead counts per acquired field

`connected_components` (`image_analysis.py:363`) labels contiguous signal above
`background + min_snr × 1.4826 × MAD` and returns `n_components` and stage
centroids. Its docstring explicitly limits the claim to contiguous thresholded
signal. A robust background-relative threshold avoids max-normalisation's
failure, but does not establish that one component equals one bead: touching
beads, noise, fragmentation and threshold choice still matter.

**And the gap is optical, not just morphological** (operator, 2026-09-10, while
scoring 81b's verification image). These beads are **sub-diffraction-limit in
size**, so a cluster of them does not image as a larger object — it images as
*one* diffraction-limited spot that is **brighter**, not bigger. Segmentation
therefore cannot separate them even in principle: no threshold, no area filter
and no watershed recovers a count that the optics did not resolve. The
information that distinguishes one bead from three is **integrated intensity**,
which `connected_components` does not report at all.

This is the strongest reason the number must be called a *component count* and
never a bead count, and it is stronger than the pixel-level reasons above,
which a better segmenter could in principle improve on. It also bounds what any
future block can promise here: counting sub-diffraction objects is a photometry
problem, not a geometry one. See `R120`.

The agent's turn-25 objection — "the per-object aggregate over the mosaic won't
cleanly split by tile" — identifies a real distinction. A mosaic is resampled,
and later tiles overwrite earlier pixels in overlaps. Assigning each mosaic
component once to a tile does not recover what was visible in each original
field. The same bead can legitimately appear in two per-field counts.

`assemble_stage_coordinate_mosaic` computes tile bounds but omits them from its
return and manifest. Keeping placement metadata is useful for provenance and
review. Those bounds are axis-aligned boxes over transformed pixel centres,
however, not exact tile footprints under rotation or shear. They cannot alone
establish object membership.

The smaller extension is to reuse the existing analysis and adapter framework
on original saved tiles, with explicit count semantics and evidence. Mosaic
component totals remain a separate geometric measurement; neither route is a
validated bead counter merely because it is built in.

### F6 — the count was reported as a table, and the record already said it was unverified

`emit_observation` defaults `status="unverified"`, and
`completed_dataset.py:403-404` **permanently restricts a saved adapter** to
`{"unverified", "provisional"}`: a Claude-written adapter can never report
`observed`, however well it agrees with the operator. All nine observations in
both bead-counter manifests carry `status: "unverified"`. The model reported the
numbers in a markdown table with the caveat in prose underneath.

Two further facts from the same manifests:

- **The 52 warranted review from its own row.** `r1_c0` recorded
  `max_minus_background: 129` against 2301–18481 at the other eight tiles.
  This discrepancy is a useful warning, not proof that the count is wrong.
  `focus_metric_valid` governs focus measurement, not object counting; an
  empty field may correctly have zero objects and no valid focus metric.
- **Provenance is not the gap.** `analyzer.source_sha256` and the top-level
  `parameters` are both recorded correctly. What is missing is a **review step**,
  not a record.

And the confirmation that finally arrived is the one the design must not accept:
the operator agreed to six tiles they had not seen, and the agent then dropped
the labeled map *because* they had agreed.

### F7 — nothing gated the save of a lint-clean hook, and the prompt says otherwise

`generate_and_save_hook`'s confirmation is `if warnings and not CONFIRM_FN(...)`
(`tools.py:10600`) — gated on advisory-lint findings. Both bead counters
returned `"warnings": []`, so **no prompt fired**: the session's
`confirmations.jsonl` holds two rows, both `save_knowledge`, and none for either
hook. The prompt tells the model:

> Confirmation for save_knowledge and hook saves is also enforced in code (a
> blocking prompt), so those tools may return a "User declined" result if the
> person says no.

False for a lint-clean hook, which is the ordinary case.

**This is not a request for a new confirmation.** The operator consented in
conversation both times, and a prompt whose "no" only aborts the analysis is
information, not consent (`CLAUDE.md`). The finding is that the prompt tells the
model a code-level gate exists — which is exactly the belief that makes showing
the source and asking feel optional.

### F8 — the agent offered an action no tool performs

`TOOL_REGISTRY` holds 81 tools. The only removal verbs are `delete_position`
(Micro-Manager's position list) and `delete_knowledge` (Microclaw's own store);
`inspect_artifacts` and `open_artifact` read only. There is no filesystem
deletion anywhere in the registry.

This is design/77's D1 with the sign flipped. There, stale text made the model
declare a capability **absent** that had shipped. Here, nothing at all made the
model offer a capability that never existed. Both are the same defect: **the
model's account of what it can do was not read off its tools.**

### F9 — two paths command a Z the guard never checked

F1's last row is one. The second is in autofocus, and it is *mechanism verified
in the tree, not proved by this artifact*:

`sweep_autofocus` sets `best_z = measured_z_positions[best_idx]`
(`autofocus.py:415`) — a **measured** position — and `coarse_then_fine_autofocus`
then commands it with `_restore(ctrl, fine.best_z_um)` (`autofocus.py:623`). The
swept planes come from `linspace` and do not overshoot, and `peak_interior` is
computed on the commanded index, so both of those are correct. The escape is
that the value reported and re-commanded is a *reading*, which under design/66's
`max(2.0, 0.1 × displacement)` arrival band may sit up to ~2 µm outside the
window `check_z` validated (`entry_z ± z_range/2`).

The one artifact is consistent with this and is **not proof**: `r1_c0` reports
`best_z_um: 76.226` where the sweep window's upper bound was 75.183 — 1.043 µm
beyond it — with `converged: true` and no warning. It cannot be settled from the
record because the hook log stores only `best_z_um` and `converged`, never the
swept window or the chosen plane's commanded value. See `R111`.

### F10 — the system prompt still tells the model the built-in cannot count a field

Found by the coordinator while scoping 81c, 2026-09-10, and it is the reason
D4(b) is not only a relocation. 81b extended `connected_components` to original
saved frames and updated the **tool schema**, which now reads *"per original
saved frame or over a stage-coordinate mosaic"* and gives `input_kind` a full
description of both. `agent.py`'s system prompt was not updated and still reads:

> `run_analysis_on_saved_dataset` with adapter `'connected_components'`
> (`input_kind='stage_coordinate_mosaic'`) or `'frame_statistics'`
> (`input_kind='frames'`)

So the two surfaces disagree, and the prompt is the one that denies the
capability. This is design/77 D1's shape a second time — stale runtime text
declaring absent something that has shipped — and it matters most for exactly
the deliverable the incident was about: an agent reading only that line, asked
for a count *per position*, has no built-in route to name and must reach for a
hook. Fixing it is mechanical and is 81c's work; it is also what makes D4(b)'s
"name the tool that will produce it" answerable with a built-in rather than
with a hook the model then has to write.

## Decisions

### D1 — refuse a degenerate Z sweep at plan time, in the function every path already calls

`_build_acquisition_events` is the shared event-construction point used by
`run_zstack`, `run_timelapse`, `_plan_protocol_repetitions` and
`_acquire_positions_with_hook`. Keep shape validation there and keep it pure.

1. When any sweep key is supplied, require the complete triple and finite
   values. Require `z_step > 0` and `z_end > z_start`. Refuse zero steps, equal
   endpoints and reversed ranges with specific Microclaw messages before
   calling the dependency. This API supports ascending sweeps; it does not
   infer direction or claim that all negative-step engine calls are empty.
2. Generate the events once through `multi_d_acquisition_events`. For a declared
   sweep, refuse empty output, missing/nonfinite Z values or fewer than two
   distinct generated Z coordinates. Inspect these events, not a separately
   computed `arange` approximation.
3. At the existing guard sites, replace requested-endpoint checks with checks
   of the minimum and maximum Z in the **actual generated events**. Guard the
   same events that are submitted, including any per-position offsets. Thus
   `(60, 60.5, 1)` checks 60 and 61; it is accepted only if both are allowed.
4. Complete construction and checks for the whole fixed plan before hardware
   mutation, dose reservation or `Acquisition` construction. This requires
   ordering changes: `_acquire_positions_with_hook` currently calls
   `set_exposure` before building events. Preflight later position groups before
   acquiring the first group, and reuse their validated events.

The probe documents the installed engine's behaviour. Its 15 approximate mirror
comparisons are a diagnostic, not proof of numerical equivalence for all inputs;
production must not depend on that mirror. Preserve valid engine event output.

The equal-endpoint refusal must explain that Z values are **absolute stage
coordinates**. For one plane at a known Z, move there and use
`protocol="timelapse"` with `{"n_frames": 1, "interval_s": 0}`; D3 must cover
that no-sweep route too. For a stack around focus Z, use distinct endpoints
around Z. Do not reinterpret `0, 0` as current focus.

Carry the same validation and guard ordering into standalone emitters. An
export must not reintroduce an unchecked plane or reach.

### D2 — say "absolute" where the model reads it

Give the three Z keys real descriptions in `_PROTOCOL_PARAMS_SCHEMA`, and
sharpen `run_zstack`'s two, reusing the wording `run_adaptive_survey` already
uses in its refusal.

```python
        "z_start_um": {
            "type": "number",
            "description": (
                "First plane of the stack, as an ABSOLUTE stage Z coordinate "
                "in um - not an offset from the current focus, and not a "
                "half-thickness. Read the current Z with get_system_state and "
                "place the stack around it. Must differ from z_end_um: equal "
                "values are one plane, not a stack, and are refused. An "
                "autofocus hook centres its sweep on measured Z after the "
                "event move, so placing the stack away from the sample also "
                "puts autofocus away from it."
            ),
        },
        "z_end_um": {
            "type": "number",
            "description": (
                "Last requested plane, as an ABSOLUTE stage Z coordinate in "
                "um. The engine's step is inclusive-overshooting, so the final "
                "plane visited may sit up to one z_step_um beyond this value; "
                "both are guarded."
            ),
        },
```

### D3 — check planned reach, enforce runtime bounds, and report autofocus outcomes

**(a) Planned reach.** Extend the existing hook plan interrogation with a reach
contract, implemented on the actual `AutofocusHook` class behind
`autofocus_per_position`. Derive nominal centres from generated event Z values;
when Z is absent, use the entry-position read and propagate possible earlier
hook moves through the event sequence. An empty plane list must not bypass
preflight. Validate finite, non-negative reach parameters.

A symmetric half-range suffices for an isolated autofocus sweep about a known
centre. It does not suffice for every hook: `FocusFeedbackHook` may make multiple
jogs, later events may inherit the resulting Z, and composite hooks execute in
sequence. Compose their possible moves in callback order and respect event Z
resets. Do not simply take the largest child half-width. Hooks without a reach
contract retain their existing capability/runtime checks; absence of a contract
is not evidence that their reach was validated.

Check the resulting nominal envelope before the first acquisition. At runtime,
recompute the window from measured Z and check it again before sweeping. An
arrival tolerance is not permission to expand the allowed motion range.

**(b) Required autofocus failure.** When autofocus was requested for the field,
a refused sweep or non-converged result must not expose that field's planned
image, through the existing supervised failure path. Do not return the event
unchanged and continue as though the field were focused. Already-spent sweep
exposures and saved fields remain accounted and reported. This is a proposed
execution change, not a new confirmation prompt; it needs tests of callback
propagation, cleanup and prevention of subsequent exposures.

**Whether a failed field aborts the run or only loses its own image is the
operator's choice, and this block must not settle it silently.** The two are
very different on a real sample: a flat metric curve is the *correct* result on
an empty field, and this session had one — `r1_c0`. Aborting there would have
killed a nine-position survey at position four, preserving the first three
acquired fields but preventing acquisition of the remaining five good fields.
Skipping the field's image and continuing allows those five to be acquired,
at the cost of a dataset with a hole in it that
the log must name. A single-field autofocused acquisition has nothing to
continue and the distinction collapses.

**Operator decision, 2026-09-09: stopping is the default, and
skip-and-continue is a per-experiment argument the caller sets.** The draft
above recommended the reverse, and the argument that inverted it is one this
document had not made: the two policies are indistinguishable *at the first
failing field*, because an autofocus **configuration** error and an isolated
flat curve look identical there. Under skip-and-continue a misconfigured
200-tile survey therefore runs to completion and delivers 200 holes, having
spent the sample and the session on a dataset with nothing in it — whereas
stopping at the first field costs one field and tells the operator the
autofocus is wrong while there is still something to re-plan. The nine-position
case that motivated the draft recommendation is the *small* dataset, where the
loss from stopping is bounded and the operator is standing there; the large
dataset is where the choice actually costs, and there stopping is the safe
default. So:

- **Stop the run through the supervised failure path**, reporting the
  completed fields and naming the field and reason that stopped it. The pattern
  exists: `MMAutofocusPluginHook.post_hardware_hook_fn` (`hooks.py:604`) logs,
  then raises `SafetyViolation`, and pycro-manager's hook thread calls
  `acquisition.abort(e)` so the error reaches the caller. `AutofocusHook` should
  follow it rather than inventing a second mechanism.
- **Skip-and-continue is deferred to `R113`, because it is not implementable
  in this architecture** (coordinator finding, 2026-09-09, operator decision to
  defer). A fixed multiposition run submits **one** `Acquisition` for every
  field, and `post_hardware_hook_fn` cannot suppress an exposure: returning
  `None` becomes an empty event over the bridge and fires the camera at the
  unfocused plane anyway, and the acquisition keeps going. That is design/27's
  ghost exposure, and `hooks.py:613` already says so in a comment. To skip a
  field you must **never submit its event**, which needs either an event stream
  (`_survey_event_stream`, which `run_adaptive_survey` uses) or one
  `Acquisition` per field (block 77b's pattern, measured at a **0.601 s**
  inter-field gap — a cost every autofocused run would pay, including the ones
  that never skip).

  So 81a-2 ships **stop only**, and ships **no argument at all**. An opt-in
  whose "skip" silently exposes the field is `CLAUDE.md`'s block-wearing-an-
  opt-out's-name with the sign flipped: an *argument wearing a capability's
  name*. And a per-shape argument — skip works for `interval_s > 0`, which
  already splits per field, and not for a zstack — was rejected because a
  caller would need a paragraph to predict it.

Continuation requires verified successful restoration
and a known hardware state; `converged: false` alone is not that evidence.
Motion errors, failed restoration, unsafe runtime reach or uncertain hardware
state must stop the run through the supervised failure path **regardless of the
argument's value** — the argument governs ordinary non-convergence only, and
must not be reachable as a way to continue through an unknown hardware state.

A skipped field is **unmeasured**, not empty. A flat autofocus curve does not
establish that no beads are present. Its per-field report must say
"not acquired / count unavailable", never substitute zero or include it as a
zero in count summaries.

**(c) Explicit outcomes and accounting.** Record attempted, converged,
non-converged and skipped autofocus outcomes, keyed by event/position and hook
identity, with reasons. Current successful entries have `best_z_um` and
`converged` but no `autofocus` field; absence of that field must never mean
"skipped" when reading historical logs. Distinguish event counts from unique
position counts, and unknown outcomes from failures.

Report actual saved-frame callbacks separately from observed hook exposures and
planned hook exposures. Count hook snaps where they occur, including partial
sweeps that raise. Never derive actual hook exposures by subtracting submitted
events from `reservation.completed_frames`, and never claim `len(events)` frames
were written without delivery evidence. Partial runs name completed fields and
the failing field; historical all-skipped runs state that requested autofocus
was not performed. A skipped sweep alone does not prove its image is unfocused.

**(d) Close F9's unchecked re-command.** Before dispatching a selected autofocus
target, validate it against both the allowed Z bounds and the declared sweep
window. Apply this to `sweep_autofocus(move_to_best=True)` and the final moves in
coarse/fine and single-sweep autofocus. If the selected measured coordinate is
outside the permitted window, refuse that target, report non-convergence, and
use the existing checked restoration path. Do not silently clamp it.

Retain the selected plane's commanded Z, measured Z, actual sweep window and
final commanded/read-back Z in results and hook logs. Test restoration failure
without masking the original failure. Logging explains an escape; target
validation prevents the unchecked command. Keep standalone exports equivalent.

### D4 — resolve the count before the dose, and let the built-in answer per tile

**(a) Product.** Extend the existing `ConnectedComponents` completed-dataset
adapter to measure each selected **original saved frame**, reusing the
component-analysis implementation. Keep analysis inside the hook/adapter
framework. The current adapter accepts only a stage-coordinate mosaic; this is
an explicit contract extension, not a report-only change.

- Report `n_components` per saved coordinate, including position, time, channel
  and Z where present. Choose and disclose the planes used for a per-position
  report; never silently combine a stack into one count.
- Preserve source-pixel geometry and use the recorded calibration for physical
  area and stage coordinates. Do not pretend rotated/sheared source tiles have
  the mosaic's square axis-aligned basis. Real zero-valued source pixels are
  observations; the existing mosaic convention that zero means uncovered
  canvas must not silently exclude them from source-frame statistics.
- Report threshold, background/noise, area filters and component geometry.
  Call the result a component count until the bead interpretation has been
  checked against object annotations. Include annotation artifacts through the
  existing artifact contract so D5 has executable evidence to show.
- A bead visible in two acquired fields may appear in both per-field counts.
  Their sum is not a unique bead total. Existing mosaic component totals remain
  separate and retain their resampling/overlap limitations. Assigning each
  mosaic centroid once to a tile is not this block's per-field answer.

Also retain tile placement metadata in the mosaic manifest: source coordinate
and label, source dimensions, transform/centre and existing raster bounds.
Obtain identity from the saved dataset, not the current position list. Coordinate
names and annotation separation must follow design/73. Record bounds as bounds
over pixel centres, not exact footprints; use the source transform and pixel
support if membership is needed. Preserve mosaic pixels, draw order and overlap
statistics. Old manifests remain readable without invented tile identities.

**(b) Prompt.** The planning rule has to fire for a deliverable that does not
look custom, so it must trigger on the *request* rather than on the model's
assessment of novelty, and it must live on the path to an acquisition. Move it
out from under *"Writing code is one of your capabilities"* into its own rule:

> **Before the first exposure of any acquisition, list the deliverables the user
> named and, for each, name the tool or adapter that will produce it.** A
> quantity is a deliverable: "count the beads", "how many cells", "which fields
> have X". Counting, classifying and measuring objects are the deliverables most
> likely to have no fixed tool — check `list_hooks()` and the built-in
> `run_analysis_on_saved_dataset` adapters **while planning**, not after
> acquiring. If a deliverable has no route, say so and offer the hook **in the
> same message as the acquisition plan**. Never begin an acquisition whose
> stated deliverable has no named route.

This broadens and relocates design/77 D4's existing rule from custom analysis
to every named deliverable. The earlier rule's observed failure is why 81c owes
a replay rather than an assumption about the effectiveness of the new wording.

### D5 — show the numbers before treating a confirmation as validation, and say plainly that no detection image exists

**Rewritten 2026-09-10** (coordinator decision, operator confirmed). The draft
below the line asked for rendered detected-object evidence. 81b built that,
the operator judged it unreadable, and it was removed in full; `R121` then
established that the *unannotated* control renders 98.9 % of a sparse bright
field at grey ≤ 32 through the same shared path. So the draft's central
instruction is unfollowable today, and a prompt rule that asks for it would
teach the model to claim an evidence step it cannot take — which is this
notebook's own incident in another costume. What survives is the half the
incident actually turned on: **the operator confirmed six numbers they had
never been shown.** That is fixable with the numbers.

The record already says these numbers are unverified and already forbids
promoting them (F6). `emit_observation` defaults to `unverified`, and
`completed_dataset.py:403` permanently restricts a saved adapter to
`{"unverified", "provisional"}`.

- **Show the per-field numbers themselves before treating any confirmation as
  validation.** The incident's failure was not a missing picture, it was a
  missing table: the agent had numbers for six tiles, offered a labeled map,
  received *"those numbers seem correct"*, and then **dropped the map because
  the confirmation had arrived**. An agreement about values the operator has not
  seen is not a validation of them, and an early confirmation is not permission
  to stop reporting.
- **Relay the disclosure the built-in already emits, next to the count.**
  81b's `_analyze_source_frame` returns `component_size_distribution`,
  `review_notes`, `frame_statistics`, `stage_geometry_refusal` and
  `count_semantics_ref` — which points at `count_semantics`, written into the
  **manifest** by the runner (`completed_dataset.py:565`) rather than repeated
  in every frame result. That indirection matters when writing the rule: an
  earlier draft of this bullet said the frame result carries `count_semantics`
  directly and it does not (implementer finding, coordinator verified,
  2026-09-10). `count_semantics`'s first clause is the optical one —
  sub-diffraction objects image as one *brighter* spot, so this counts spots and
  not objects. `stage_geometry_refusal` is `None` whenever both intended-XY keys
  are present, so it is relayed only when it is not null; the other three are
  always populated. These are the product's own words about what the number is
  made of and they are already correct. The
  defect to prevent is a markdown table of nine integers with the caveat in
  prose underneath — which is what the incident produced.
- **Call it a component count.** Never a bead count, a cell count or an object
  count, and not for the built-in either: `observed` component geometry is not
  validated object identity, and the difference is optical rather than
  cosmetic (F5, `R120`).
- **Report quality warnings alongside counts, as review information and not as
  a rejection rule.** Flag unusually low signal or an invalid focus metric for
  review. A valid zero count can come from an empty field, and a field's
  intensity span alone did not refute the 52 — it warranted a look at it.
- **Say that there is no detection-evidence image, rather than substituting
  something that looks like one.** No built-in adapter writes an annotated
  artifact, and `open_artifact(analyze=True)` on a sparse bright field is not
  currently a legible check of individual objects (`R121`). So: do not claim to
  have shown the detections, do not present a plain mosaic or a thumbnail as
  though it showed them, and when the operator asks to *see* what was counted,
  say that the numbers and their parameters are what the measurement can show
  today and that a per-object overlay is not available. A custom adapter that
  writes its own artifact is opened and read as usual — the restriction is on
  claiming evidence that does not exist, not on showing evidence that does.
- **Prompt rule:** *"An operator's 'looks right' about numbers they have not
  been shown is not a validation. Report the per-field counts themselves, with
  the adapter's own count_semantics, component_size_distribution, review_notes
  and frame_statistics beside them, and identify them as unvalidated component
  counts — never as a bead, cell or object count. An early confirmation is not a
  reason to skip a report you have already offered. There is no detected-object
  overlay: no built-in analysis writes one, so never claim to have shown the
  detections and never offer a picture as the evidence."*

- **Correct the prompt's claim about hook-save confirmation** (F7): say that the
  code gate fires only on advisory-lint findings, so showing the full source and
  asking is the model's own duty on every save, not a fallback.

**What is deferred, and to where.** The rendered evidence stays owed:
`R121` (the stretch), `R43` (the overlay), `R116` (a small component consumed
by its own annotation) and `R119` (a fixture with no data in it) are all open in
`design/70`. They belong together in one successor block, which is what `R43`
has asked for since design/43 and what design/73 §3 already owns a probe for.
`R112` — recording that an operator *saw* the evidence — is downstream of that
and is not 81c's subject either.

<details><summary>The draft this replaced, verbatim</summary>

```markdown
- **Render detected-object evidence when derived counts are first reported**,
  for built-in and custom adapters alike. Show original-field crops with
  component outlines or numbered detections, position labels and the reported
  count. A position-labeled mosaic alone does not show what was counted. Keep
  annotations in separate artifacts so they cannot enter measurement pixels.
  Open/read the artifacts with `open_artifact(analyze=True)` at a resolution
  sufficient to inspect individual objects; use field crops when an overview
  would hide them. A tool call alone does not establish visual validation.
- **Prompt rule:** *"An operator's 'looks right' about numbers they have not been
  shown is not a validation. Show detected-object annotations alongside the
  counts, with fields readable individually. If evidence cannot be rendered,
  identify the numbers as unvalidated measurements and do not present them as
  an established bead count. An early confirmation is not a reason to skip
  evidence you have already offered."*
```

Note that the draft's own escape clause — *"if evidence cannot be rendered,
identify the numbers as unvalidated measurements"* — is the branch that is
always taken today, so the rewrite is closer to a promotion of that clause than
to a retreat from the rule.

</details>

### D6 — Microclaw does not delete data, and does not offer to

**(a) No deletion tool, ever.** Recorded here as a standing decision so a later
block does not add one as a convenience. The blast radius is the operator's
irreplaceable acquisitions, the failure mode is silent, and their file manager
already does the job. `delete_position` (Micro-Manager's list) and
`delete_knowledge` (Microclaw's own store) are not counterexamples: both are
Microclaw-owned bookkeeping the operator can rebuild. A future request for
dataset cleanup is answered with `inspect_artifacts` — exactly what the agent
fell back to — never with a remover.

**(b) Never offer an action with no tool behind it.** Generic, and this is its
second instance after design/77's inverse. Fold into the prompt's existing
**Reporting — say only what a tool told you** section, because it is the same
rule one tense earlier:

> **Offer only what a tool can do.** Before writing "want me to X?", name the
> tool that performs X; if there is none, do not offer it. For anything that
> removes, overwrites or moves the operator's data the answer is always "here is
> exactly what is there — the removal is yours." Microclaw has no tool that
> deletes data and will not get one.

Wording it as *"and will not get one"* is deliberate: the model's next instinct
after discovering a missing capability is to propose adding it, and that
proposal is not available here.

## Rejected alternatives

- **A confirmation before a degenerate stack.** The decline only aborts the run,
  so it is information, not consent (`CLAUDE.md`; design/60 block 60b).
- **Auto-correcting `0, 0` to the current Z.** Two readings, no way to tell them
  apart, and a plausible fabrication of the operator's intent is the defect being
  fixed.
- **A `validate_z_plan` tool or a validator layer.** `_build_acquisition_events`
  is already the choke point every path calls.
- **Teaching the model to write better counters** (a lint rule about
  normalization). Reuse the existing component-analysis implementation through
  the adapter contract and check its suitability on annotated source fields;
  neither lint nor built-in provenance validates a bead counter.
- **Promoting a saved adapter's status to `observed` on operator confirmation.**
  `completed_dataset.py:403` is right. What could be recorded is that the
  operator *saw* the evidence — against `run_id` and `content_sha256` — and that
  is `R112`, not this block.
- **A stricter hook-save confirmation.** F7 is a documentation defect, not a
  missing gate, and a blocking prompt whose "no" only cancels an analysis is the
  thing `CLAUDE.md` forbids. And a change that can stop a run and wait for a
  human needs the operator's agreement before it ships.

## Acceptance tests

Home files in parentheses. Watch-it-fail notes follow
`feedback_watch_it_fail_not_regressions` and
`feedback_mutate_dont_watch_it_fail`: where the subject is a refusal, check out
the pre-fix tree and confirm the test fails *for the stated reason*; where it is
structure or ordering, mutate the one property instead.

**D1** (`tests/test_protocol_preflight.py`)

1. Refuse F1's equal-endpoint and zero-step shapes, reversed endpoints, the
   negative-step example, incomplete triples and nonfinite inputs with specific
   Microclaw reasons. Zero steps never reach a dependency `ZeroDivisionError`.
2. `(60, 60.5, 1)` checks actual extrema 60 and **61**, and is accepted with a
   bound admitting 61. With a maximum of 60.5 it refuses before mutation or
   `Acquisition` construction. Assert guard arguments and observable effects.
3. Valid stacks preserve the installed engine's exact events, including
   fractional steps and position-dependent Z. Assert the validated event list
   is the submitted list; do not compare against a handwritten plane formula.
4. Drive `run_zstack` and hooked/unhooked multiposition routes through
   `execute_tool`. Include a later invalid group and verify no earlier group
   acquired and no exposure setting changed. Execute emitted scripts against
   fakes to verify equivalent preflight and refusal.

**D2** (`tests/test_protocol_params_schema_and_hint.py`)

5. All three Z keys describe their constraints; start/end explicitly say
   absolute, and step describes positive ascending semantics. Mutation: remove
   the absolute-coordinate description and confirm the assertion fails.

**D3** (`tests/test_hook_z_reach.py`, new, and autofocus/export tests)

6. Keep the incident's exact degenerate call as a zero-acquisition regression,
   but test reach independently with a valid two-plane stack whose hook window
   exceeds the guard. Also test a one-frame timelapse with no explicit event Z
   and an unsafe entry window. Both refuse with zero acquisitions constructed.
7. Cover safe reach, hooks without a reach contract, mixed explicit/inherited Z,
   multiple feedback jogs, cumulative motion and ordered composite hooks.
   Mutation of a child reach or an inherited centre must fail the relevant test.
8. Change measured Z after successful preflight so runtime reach is unsafe.
   Assert no planned image is exposed for that field. Cover non-convergence,
   partial sweep exposure accounting, supervised teardown and restoration
   failures without masking the original failure. Unsafe runtime reach, motion
   errors, failed restoration and uncertain hardware state must prevent all
   subsequent acquisition. For ordinary non-convergence with verified
   successful restoration, assert no subsequent event is acquired (count
   constructed `Acquisition` objects), the completed fields are still reported,
   and the field and reason that stopped the run are named in both the result
   and the hook log. Assert that the hook does **not** return `None` and does
   **not** return the event unmodified on that path — design/27's ghost
   exposure is the failure being prevented, and a fake that ignores the return
   value cannot see it. There is no skip-and-continue argument to test
   (`R113`).
9. Reporting fixtures cover historical all-skipped logs, current successful
   logs with no `autofocus` key, mixed outcomes, unknown outcomes, repeated
   events per position and incomplete saved-frame delivery. Assert actual saved
   frames and hook exposures independently, with no negative inferred counts.
   Inject an out-of-window selected measured Z into both direct move-to-best
   and coarse/fine/single-sweep paths: it must never be dispatched, even when
   arrival tolerance admits the reading. Assert commanded/measured/window log
   fields and equivalent behaviour in executed standalone exports.

**D4/D5 product** (`tests/test_dataset_mosaic.py`,
`tests/test_completed_dataset.py`, relevant analysis tests)

10. On a known grid, added placement metadata preserves source identity and
    existing bounds. Mosaic pixels, canvas bounds, draw order and overlap
    statistics remain unchanged. Include rotated/sheared calibration and
    distinguish bounding boxes from true source support.
11. Count components on original frames with known objects, including one bead
    visible in two overlapping fields: it appears in both per-field counts.
    Verify explicit time/channel/Z selection, genuine zero-valued source pixels,
    calibrated area/coordinates, threshold parameters and detection annotations.
    Include touching objects to demonstrate that components are not automatically
    beads. Annotations never alter source or measurement pixels. Empty fields
    and low signal produce review information, not automatic count rejection.
12. A pre-change mosaic manifest still loads and supports its original analysis
    without invented per-field identities. Existing mosaic component output is
    preserved; source-frame analysis does not require new mosaic metadata.

**D4(b)/D5/D6 prompt** (`tests/test_agent.py`, `tests/test_schema_parity.py`)
— mechanical halves only. The behaviour is scored from the next real session,
not from a replay: see the 81c block entry.

13. The prompt contains D4(b), D5 and D6(b), and no longer claims a blocking code
    gate on every hook save. Assertions guard the intended contract: the
    deliverable-routing rule stands as its own rule and not under the
    writing-code one; the disclosure fields D5 names are named; the count is
    identified as a component count; and the prompt does **not** instruct the
    model to show a detected-object overlay, because none exists. No
    intensity-only rejection rule. Mutation: reinstate the stale
    `input_kind='stage_coordinate_mosaic'` parenthetical and confirm the F10
    assertion fails.
14. `TOOL_REGISTRY` contains no tool whose name or description offers filesystem
    removal, in `tests/test_schema_parity.py` — which is where the other
    registry-wide guards live. The design draft named
    `tests/test_suite_integrity.py`; that file guards test-file text encoding
    and is the wrong home (coordinator correction, 2026-09-10). This guards
    declared capability; it is not proof about arbitrary code or an enforcement
    sandbox.

## Blocks

**81a is split into two blocks** (coordinator decision, 2026-09-09). As
specified it changed pure plan-time validation *and* runtime motion and failure
handling across five modules with nine test groups, in one turn. The seam is
real rather than administrative: D1/D2 are refusals computed from arguments
before any hardware is touched, D3 changes what a run does to the sample when
autofocus fails. 81a-1 is mergeable and gate-able on its own and fixes the
incident's first failure; 81a-2 builds on its validated event list.

**81a-1 — the plan refuses what cannot work.** D1, D2.
`microclaw/tools.py`, `microclaw/tools_schema.py` and the standalone emitters,
tests 1–5. LOCAL tests establish event geometry and preflight ordering: the
validated event list is the submitted list, the guard sees the actual generated
extrema, and no earlier position group acquires and no exposure is set when a
later group is invalid. Run the installed-engine probe as a baseline, not as
proof of a mirror formula. Draft the R108 knowledge correction. No rig gate:
every limb is computable locally, and D1 item 4's ordering claim is an
observable-effects assertion, not a hardware one.

**81a-2 — the run refuses what cannot work.** D3(a–d). Ships **stop only**;
the skip path is `R113`. Also carries **81a-1's owed export limb**: one
demo-machine run of an emitted Z-stack script (operator decision, 2026-09-09).
`microclaw/hooks.py`, `microclaw/autofocus.py`, `microclaw/tools.py`,
composite-hook and export support as required, and tests 6–9. Consumes 81a-1's
validated event list for nominal centres. Carries the D3(b) operator decision: **stop, with no
skip argument** — see D3(b) for why the alternative is not implementable here. LOCAL tests establish reach composition, target guards, accounting and
failure propagation.

**Overlap with `design/82` block 82b — closed by sequencing** (operator
decision, 2026-09-09: no work on design/82 until design/81 is finished). The
overlap was real and is worth recording because it is the block-13/41b shape:
82b's D3 changes `read_hook_log`'s `entries` contract and its D2 changes the
shape of a recorded tool result that `export_session_script` renders from,
while 81a-2's D3(c) adds hook-log outcome fields and its exports render the
autofocus hook's source. Both branches would have been green alone and broken
merged — which this notebook reproduced once already on 2026-09-09. Concurrency
was the whole hazard, so 82b starting after design/81 merges removes it: 82b
inherits 81's merged hook-log and export contracts and owes nothing special
beyond its own review.

This block changes runtime motion and failure handling. Add a focused
demo-machine gate for successful autofocus, runtime refusal before the planned
image, restoration and standalone export. Reconcile observed exposure/position
logs with the result. Do not claim fake tests prove physical arrival or that
added logging proves the old incident's mechanism.

**81b — the built-in measures per field and shows its detections.** D4(a) and
D5's artifact support. `microclaw/dataset_mosaic.py`, `microclaw/tools.py`
(manifest), `microclaw/completed_dataset.py`, `microclaw/image_analysis.py`
and export inlining as required; tests 10–12. Follow design/73's saved identity
and separate-annotation rules. LOCAL tests establish geometry and count
semantics. This does not by itself validate bead detection across samples.

**81b delivers D4(a) and NOT D5's artifact support** (operator decision,
2026-09-10). The block shipped detection-evidence images, the operator looked
at them — *"I can only see numbers and circles on a dark background... all
three pictures have little to no meaning... This is truly horrible"* — and they
were removed in full: the drawing, the glyph renderer, the `T<n>` labelling,
the schema promises and the annotation-only tests.

Three things that decided it, in order of weight:

1. **The measurement said the same as the eye.** A rendered annotation was 94 %
   at grey 2–3 of 255 with 1.6 % at 255. The only field the operator could see
   was the one with **no detections**, because nothing had been drawn on it.
2. **The problem is bigger than the annotation and sits in a shared display
   path.** A *raw, unannotated* bead field renders **98.9 % at grey ≤ 32**
   through `open_artifact(analyze=True)`, because `make_thumbnail`'s
   2nd–99.8th percentile stretch is set by the brightest bead and puts a
   202-count background at grey 4. No annotation design fixes that, and it is
   shared with `snap_and_analyze` and `run_autofocus`.
3. **It was never this block's work to begin with.** `R43` says "size it as a
   block"; design/73 §3 owns the renderer and has a mandatory pre-implementation
   probe that was never run. Both were folded in through revision prompts rather
   than read as design. `R43` stays **OPEN**.

The consequence is recorded rather than papered over: **D5's artifact support
is unmet**, and 81c must not assume evidence images exist. Shipping a picture
that looks like evidence and is not is this notebook's own incident in another
costume.

**The demo-machine acquisition this block asked for was not needed.** The
entry called for "one demo-machine overlapping grid" to check real saved
coordinates, per-field artifacts and readable annotations. Surveying the
evidence archive first (2026-09-10) found all three already there, in
acquisitions this project made months ago:

| dataset | what it supplies |
|---|---|
| `20260909_ZM_beads/beads_af_run2_1` | the incident's own 3×3, 9 positions, a 90° rotated Andor affine |
| `38-composite-hooks-m5/gate_h1/gate_h1_1` | **35.9 % genuine field overlap**, 5 positions, a real *sheared* Hamamatsu affine |
| `nestor-…/mt_scan_150um/mt150_1` | **81 positions**, which binds the default 64-artifact budget |

`design/81-block81b-gate.py` runs eight limbs over those three and needs no
microscope. This is `project_rig_evidence_archive`'s standing point — most
"rig gates" are computations over data already in the archive — and it is worth
the survey every time, because operator time is the real budget. Two caveats:
both geometries come from M5-family rigs, so this is **n=2 camera geometries,
not "any rig"**, and no archived grid was acquired *for* this block, so nothing
here tests a deliberately-chosen overlap fraction.

**81c — the planning and reporting rules.** D4(b), D5 (as rewritten), D6(b),
F10's stale prompt paragraph, and tests 13–14. `microclaw/agent.py` only, plus
`tests/test_agent.py` and `tests/test_schema_parity.py`. Every limb settles
LOCAL: this block changes no tool, no schema and no hardware path.

**No replay. The behaviour is scored from the next real session** (coordinator
proposal, operator decision, 2026-09-10). The draft this replaced specified a
two-arm, ~14-sample replay at roughly $10, and F4 is a real argument for it: an
instruction of this exact kind already shipped and did not fire, so "we
reworded it" is not evidence. Three things outweighed it.

1. **`feedback_api_gates_need_an_informational_delta`'s record.** ~$24 across
   77a, 79a and 79b's pilot bought **one** product discovery and about **eight
   instrument defects**, and its sixth rule is the one that decides this block:
   *discovery has never come from a replay* — 77a's own incident came from a
   real operator session and the replay was built afterwards.
2. **This block's subject is a session that is going to happen anyway.** The
   incident was a bead survey; the operator runs those. Every one of the three
   scorable behaviours below is readable from a history JSONL at zero marginal
   cost, and from a real request rather than a scripted one.
3. **D4(b)'s informational delta is the weakest of the three and F10 changes
   it.** The control tree already carries a deliverable-mapping sentence, so a
   D4(b) arm is close to the relocation that rule 1 says a two-tree replay
   cannot see. What is *not* a relocation is F10: the control's prompt tells the
   model `connected_components` takes a mosaic and nothing else, so a control
   asked for a count per position has no built-in route to name. Fixing that is
   most of D4(b)'s practical effect and it is a mechanical assertion, not a
   behavioural measurement.

So the block merges on its LOCAL half, and these three behaviours are scored
from the next bead session's history the way `R88` was — from the artifacts, not
from a verdict. **Write them into the ledger row as owed, and score them there;
a merged block with an unmeasured behavioural claim must say so rather than
imply the wording works.**

- **D4(b)**: does the turn that plans the acquisition name a route for every
  named deliverable, before any acquisition tool is called? A tool-call ordering
  question, not a prose one — and with F10 fixed, "count per position" should
  now resolve to `run_analysis_on_saved_dataset` with
  `input_kind='frames'` rather than to a hook the model writes.
- **D5**: when counts are first reported, are the per-field numbers themselves
  shown, with the adapter's `count_semantics`, `component_size_distribution`,
  `review_notes` and `frame_statistics` beside them, called a component count?
  And does an early *"those numbers seem correct"* fail to suppress the report?
  That last clause is the incident's actual failure and the cleanest thing to
  score. Do **not** score for a rendered overlay: there is none, and the rule
  now says so.
- **D6(b)**: at turn 53's decision point, is a deletion offered? The control's
  answer is already recorded in the incident, so this is a one-sample
  before/after with the "before" already paid for.

If a future block does want the replay, the argument to beat is rule 1: state
the informational delta between the two trees for each arm, by `grep`, before
quoting a sample size or a price.

## Register rows this opens

Numbered from `R107`, the current highest in `design/70` when this
notebook opened. All of them are carried in `design/70`, which is the
authoritative register; the summaries here are pointers.

- **`R108`** — the session saved `strategies/bead_counting_offline` carrying
  *"set the zstack z_start_um/z_end_um to the ACTUAL focus plane, NOT 0"*: a
  workaround for the defect 81a fixes, stored where the next session reads it
  back as instruction. `R100`'s family, second instance — a store that maintains
  a competing capability declaration overrides the installed tool contract in
  practice. The entry is the operator's file and theirs to correct; a drafted
  replacement belongs with 81a. **MEDIUM / SMALL.**
- **`R109`** — every custom-adapter observation records `parameters: {}` while
  the manifest's top-level `parameters` is correct. Cosmetic, and it invites a
  reader to conclude the parameters were not recorded. **LOW / SMALL.**
- **`R110`** — a fixed-plan Z sweep leaves the axis wherever its last plane put
  it. Run 1 parked the focus axis 65 µm from where the operator had set it, and
  nothing restored it; design/52c's restoration block covers declared envelopes,
  not the plan's own axis. 81a removes this trigger, not the question.
  **MEDIUM / SMALL.**
- **`R111`** — F9: an unchecked measured-coordinate re-command and insufficient
  logging to establish whether it caused the historical observation. **HIGH**;
  addressed by 81a D3(d), including target checks and commanded/measured/window
  provenance. Logging alone does not fix motion enforcement. New records can
  settle future cases; they cannot recover missing values from `r1_c0`. The
  historical artifact remains inconclusive unless independent evidence exists.
- **`R121`** — `make_thumbnail`'s percentile white point is set by the
  brightest object, so a sparse bright field renders **98.9 % at grey ≤ 32**
  unannotated. **MEDIUM**, and a prerequisite for `R43` and design/73: neither
  can deliver a legible picture without it. Pre-existing since `61384f8`
  (2026-05-19) and untouched by this block — 81b found it, and only because it
  was the first feature to make the picture a deliverable rather than a
  garnish. It is one image class, not the display generally: `peak/bg` of 80
  and 345 fail, 1.9 renders correctly. design/70 carries the measurements.
- **`R120`** — counting sub-diffraction objects needs photometry, and the
  built-in reports no intensity at all. A cluster of sub-diffraction beads
  images as one brighter diffraction-limited spot, so `n_components` counts
  *spots*, not objects, and no segmentation improvement changes that
  (operator, 2026-09-10). `connected_components` returns area, centroid and
  bounding box and **no integrated intensity**, so a caller cannot even
  post-hoc estimate multiplicity from what it hands back. Adding integrated
  and peak intensity per component is small, does not change any existing
  number, and is the prerequisite for anything that wants to count rather than
  locate. Whether Microclaw should then *do* quantal brightness analysis is a
  separate and much larger question. **MEDIUM / SMALL** for the reporting half,
  LOCAL.

- **`R116`** — a small component is entirely consumed by its own annotation. A
  3×3 component's boundary is its whole ring, its halo covers the centre, and
  the number glyph's box covers what is left, so none of the object's own
  pixels survive in the evidence image. 81b's ink fix makes the *field* visible
  and does not fix this; it is a drawing question — outline width and glyph
  placement — and it bites exactly the detections a reader most needs to judge.
  Found by the 81b implementer while building a fixture, 2026-09-10.
  **MEDIUM / SMALL**, LOCAL.
- **`R117`** — the mosaic path has the same unfiltered-count defect as the
  frame path and now has *less* disclosure than it.
  `input_kind="stage_coordinate_mosaic"` at the default still returns a bare
  noise-dominated `n_components` with no size distribution and no review note,
  because 81b's disclosure was scoped to `_analyze_source_frame`. The asymmetry
  is visible in the product. **MEDIUM / SMALL**, LOCAL.
- **`R118`** — `min_area_um2` is not checked against the pixel area, so a
  caller can set a filter smaller than one pixel and change nothing. The review
  note still fires and is still correct, but nothing says the filter was inert.
  Not fixed in 81b because both available fixes — a refusal or a default — were
  excluded. **LOW / SMALL**, LOCAL.
- **`R119`** — `test_mosaic_evidence_honors_option_and_source_dtype` builds a
  mosaic that is **entirely zero**, so it has been asserting the properties of
  an evidence image with no data in it. The old `overlay.max() == 255`
  assertion hid that completely; 81b's property assertion exposed it but did
  not fix the fixture. Pre-existing. **LOW / SMALL**, LOCAL.

- **`R112`** — no record exists of an operator having *seen* the evidence behind
  an unverified adapter's numbers. D5 makes the showing happen; recording it
  against `run_id` + `content_sha256` is separate and is not 81c's subject.
  **LOW / SMALL.**

## Run ledger

Baseline before the block: `main` `f915e5d`; `95cfdfd` is the notebook's own
commit and `e03f790` was the tree the findings were read against.
Coordinator-run suite on the 81a-1 worktree at that commit: **3086 passed, 99
skipped, 4 warnings** in 267 s. 81a-2 starts at `da67cbd`, baseline **3167
passed, 99 skipped** measured four times — with one pre-existing intermittent,
`R114`, seen in one of five runs and in the path 81a-2 changes (`.venv/bin/python -m pytest -q`, uv + Python
3.12.14). `design/81-degenerate-z-scan.py` run against the installed
pycro-manager on the same tree reproduces F1's table row for row, including the
`(0, 0, 0)` single event with no `z` key and the `(60, 60.5, 1)` overshoot to
61.0.

81b starts at `fb0a3ec`. Coordinator-run suite on the 81b worktree at that
commit: **3219 passed, 99 skipped, 4 warnings** in 298 s
(`.venv/bin/python -m pytest -q`, uv + Python 3.12.14). `R114`'s intermittent
did not fire in that run.

81c starts at `9e0daf1`, its own coordinator commit. Coordinator-run suite on
the 81c worktree at that commit: **3250 passed, 99 skipped, 3 warnings** in
300 s, and at the reviewed implementation `7c52956`: **3257 passed, 99 skipped,
3 warnings** in 272 s — the seven new tests and no regression
(`.venv/bin/python -m pytest -q`, uv + Python 3.12.14). Note **3** warnings
where 81a and 81b recorded 4, and **2** on the primary checkout after the merge:
the varying one is a dependency's `SyntaxWarning`, which fires or not depending
on each venv's bytecode cache. It is an environment artifact, the pass counts
agree exactly, and the warning count is therefore **not** a number to compare
across trees. Recorded because a reader who compares 3 with 2 deserves the
reason rather than the discrepancy. The runner was handed the targeted command only
(`.venv/bin/python -m pytest -q tests/test_agent.py tests/test_schema_parity.py`,
265 passed before the block, 272 after), per
`feedback_runner_tests_only_what_it_changed`.

**One coordinator measurement error worth recording**, because it would have
been reported as a number: the first full-suite run was started in the runner's
worktree while the runner's revision turn was still editing files in it. It
returned 3257 and happened to agree with the clean run, and it is not evidence —
a suite run across a tree someone else is mutating measures nothing. The clean
re-run at a quiescent `7c52956` is the number above.

| block | branch | start | implementation | gate | merge |
|---|---|---|---|---|---|
| 81a-1 | `design81/81a1-plan-time-refusal` (deleted) | `f915e5d` | `49b1011` WIP, `4589007`, `0f2a95e`, `bd5c83c` | none on rig; export limb owed by 81a-2 | `d2bc31c` |
| 81a-2 | `design81/81a2-runtime-refusal` (deleted) | `da67cbd` | `ab3af26` WIP, `73f87b9`, `8608dd4`, `5e1e4a5` | demo rounds 3-5; A unobservable (`R101`) | `b6ca3ec` |
| 81b | `design81/81b-per-field-counts` | `fb0a3ec` | `aa28568` WIP (unreviewed, killed turn) … `536fe07`, then `06074bd` removing the annotations | 6/6 scoring limbs off-rig over three archived acquisitions; limb E's figure confirmed by the operator on `r0_c2` | `26262f4`, close-out `33e2455` |
| 81c | `design81/81c-planning-and-reporting-rules` | `9e0daf1` | `1800e28`, `7c52956` (review round 1), `9370f9b` (coordinator) | **none** — every limb LOCAL; D4(b)/D5/D6(b) behaviour owed to the next bead session's history (`R122`) | `b978238`, close-out on `design81/close-81c` |
