# A focus sweep should be able to read the focus device, not only the camera

## Problem — the Nikon session of 2026-08-22

Evidence: `~/Documents/Documents - Beyonce/Projects/Micro-Claw/pfs-nikon/`,
`20260822_175438_245655_microclaw_history.jsonl` (174 tool calls). 60×/1.49 NA
oil, Andor iXon, DNA-PAINT sample.

The session opened by doing by hand exactly what `SYSTEM_PROMPT` now tells it to
do — "just move the Z stage and check this property" — and it did not work.
Tool calls 3–23, verbatim in order:

```
move_stage_z 2300 · Status "Dichroic mirror not inserted" → "Out of focus search range"
move_stage_z 2305 · Status "Out of focus search range"
move_stage_z 2310 · Status "Out of focus search range"
move_stage_z 2295 · Status "Out of focus search range"
move_stage_z 2290 · Status "Out of focus search range"
move_stage_z 2285 · Status "Out of focus search range"
move_stage_z 2315 · Status "Out of focus search range"
move_stage_z 2330 · Status "Out of focus search range"
move_stage_z 2270 · Status "Out of focus search range"
```

Eight planes, sixteen tool calls, nothing found, and the search was abandoned
without any record that it had been. Three things are wrong with it, and none of
them are the model being careless:

1. **It is not a sweep.** The order is 2305, 2310, then back down to 2295, 2290,
   2285, then up to 2315, 2330, then down to 2270. There is no declared window,
   no declared step, and no way to say afterwards what was covered.
2. **The window never contained the answer.** It covered 2270–2330. PFS locked,
   later in the same session, at **~2373**. The search was 43 µm short and
   stopped because it ran out of patience, not because it had excluded anything.
3. **The 5 µm step could not have found the band anyway.** The prompt's own
   numbers put the PFS capture range at ~10 µm for oil and shrinking with NA;
   this is a 1.49 NA oil objective. At 5 µm steps a ≤10 µm band is one or two
   planes — indistinguishable from a stray read.

Then it fell back to image-based autofocus, and that is the part that matters
most. Four `run_autofocus` calls, all four **`converged: false, moved: false`**
— the flat-curve gate was right every time. The model read `best_z_um` out of
each refusal, `move_stage_z`'d there itself, and re-swept from the new plane:

| call | region | contrast | min_contrast | best_z_um | verdict |
| --- | --- | --- | --- | --- | --- |
| 37 | — | 0.015 | 0.15 | 2310.2 | refused, edge |
| 39 | `[40,60,200,80]` | 0.226 | 1.21 | 2325.1 | refused, edge |
| 41 | `[40,60,200,80]` | 0.918 | 1.21 | 2345.2 | refused, edge |
| 43 | `[40,60,200,80]` | 0.682 | 1.21 | **2347.6** | refused, interior |

It settled on 2347.6, armed PFS — and PFS locked at ~2373.7, **26 µm higher**.
The operator's correction, in the transcript:

> "The PFS locked into the right plane. The first plane you focused on had a lot
> of signal, but it was actually the bottom of the coverslip. […] The PFS was
> right, the image based one was wrong."

The session's own saved knowledge records the mechanism: coverslip texture is
dense, high-contrast, and scores `focus_metric ~65000` against a faint cell at
SNR 4. **The sharpest plane in the field is not the sample plane, and no image
metric on this rig can tell them apart.** The PFS can — that is what it is for.

So this is not only "the loop is slow". The loop is the only instrument that
sees the right answer, and it is the one thing the sweep engine cannot read.

## What the session already measured

`TIPFSStatus.Status` is **not binary**, and it is informative while the lock is
not holding. The complete observed value set:

| value | when |
| --- | --- |
| `Dichroic mirror not inserted` | PFS blind; first read of the session |
| `Out of focus search range` | every one of the eight search planes |
| `Within range of focus search` | lines 225, 241 — lock dropped after a tile survey, Z parked at 2375.4; re-arming immediately gave `Locked in focus` |
| `Focus lock failed` | Nikon session 2026-08-22 |
| `Focusing` | transient, ~1 s after `State=On` |
| `Locked in focus` | held |

`Within range of focus search` is the reading the whole design turns on: **in
range, not locked.** It settles the question this document was originally going
to spend a rig trip on. design/40's "lock is binary, nothing to hill-climb" was
measured with a coarser instrument — arm-and-see at each height — and is
superseded for this rig: there is a three-level ordinal, and *in range* is a
band that contains the lock point.

Also measured, and load-bearing:

- `FullFocusTimeoutMs = 5000`, `FullFocusWaitAfterLockMs = 0`.
- `set_focus_lock(enabled=false)` returned **`"No EMU configuration — cannot
  control a focus lock."`** This is the live defect below, observed rather than
  inferred.
- Stage settles took 0.34–1.56 s (`elapsed_s` on `move_stage_z`), so the
  hardware is not what makes the hand-driven loop slow.

## Why `run_autofocus` cannot do it today

`sweep_autofocus` (`autofocus.py:105-155`) is already this loop — move, settle,
read, choose, gate, move to the choice. It hard-wires two things that are not
part of "sweep and choose":

1. **The reading is a camera frame** — `metric_fn(snap_to_numpy(ctrl))` (`:134`).
2. **The reading is a scalar to maximise** — `np.argmax(metric_values)` (`:136`),
   gated by `curve_contrast` and `peak_interior`.

The second is why this is not a one-line `metric_fn` swap: `argmax` over
`["Out of focus search range", "Within range of focus search", …]` is a string
comparison. The reduction has to travel with the reading.

A third property falls out: **a property read costs zero exposures**, so a
search that missed its window can be re-run wider, or finer, for free. On a
DNA-PAINT sample that is not a nicety.

## Decision

One new optional argument on `run_autofocus`. Omitted, behaviour is unchanged
byte-for-byte, including the emitted script. No new tool, and no `TIPFSOffset`
work — fine-tuning the offset under an active lock is `move_named_stage` and
already works.

```
run_autofocus(z_range_um=120, z_step_um=1, method="sweep",
              probe={"device": "TIPFSStatus", "property": "Status",
                     "in_focus_values": ["Within range of focus search",
                                         "Locked in focus"]})
```

One call. 121 planes. Zero exposures. Z left at the centre of the in-range band,
ready for `State=On`. The window the session actually needed — and could not
afford at two tool calls per plane.

### 0. What does not change

**Omitting `probe` must leave the existing autofocus bit-identical**, and that
is a test, not an intention. The image path keeps its own probe, whose `read` is
`lambda: metric_fn(snap_to_numpy(ctrl))`, whose `choose` is `int(np.argmax(...))`
and whose `admit` is today's contrast comparison. Same metric, same argmax, same
`contrast_threshold`, same `peak_interior`, same payload keys, same emitted
script. **The band reduction is reachable only through a categorical property
probe**; it answers a different question ("where is this lock's capture range?")
and never competes with the peak search. On a rig with no focus lock, nothing
about this design is reachable at all.

The regression bar for 56a is a characterization test: run the existing
`tests/test_autofocus.py` fixtures through the refactored engine and assert the
whole `AutofocusResult` — both `SweepResult`s, every metric value, `best_z_um`,
`peak_interior`, `converged`, `moved` and the exact `reason` string — is equal to
what `main` produces. Not "still converges"; equal.

Three intentional changes do touch the no-probe path, and none of them change
which plane it picks:

- **The refusal prose gains a sentence** (`_flat_reason`, `_edge_reason`) saying
  the reported `best_z_um` is not a focus estimate. Behaviour-identical, and
  aimed squarely at the four-call walk in the table above.
- **The focus-lock refusal starts firing on non-EMU rigs.** That is a *new*
  refusal on the old path: an image sweep with PFS armed will now be refused
  where it previously ran. It is correct — a sweep against an armed servo
  measures nothing — and the existing text already tells the caller to disengage,
  sweep, and re-engage. Call it out in the block, because it is the one change a
  Nikon user will notice without asking for it.
- **`return_thumbnail`'s advertised default is reconciled with its real one**
  (§5), toward current behaviour, so nothing stops returning a thumbnail that
  returns one today.

### 1. `FocusProbe` — a reading, and what to do with a curve of them

```python
# microclaw/autofocus.py
@dataclass(frozen=True)
class FocusProbe:
    """One reading per plane, plus how a curve of those readings picks a plane.

    ``read`` takes no arguments: it closes over whatever it needs. That is the
    whole generalization — sweep_autofocus stops knowing that a focus reading
    comes from a camera. A narrowing of sweep_autofocus, not a layer on top of
    it: the two call sites stop branching on kind entirely.
    """

    read: Callable[[], float | str]
    #: curve -> index of the chosen plane.
    choose: Callable[[list], int]
    #: curve -> cause-and-advice prose, or None to admit. Replaces the inline
    #: `curve_contrast(...) < min_contrast` branch at both call sites; the image
    #: probe's admit IS that branch. It returns the CAUSE only, never the
    #: "Z was NOT moved (restored to X um)" clause -- the probe does not know
    #: which pass it is or where the stage ended up, and that clause must keep
    #: reporting the MEASURED restore (see _flat_reason's comment). The caller
    #: composes it, in one place instead of today's two.
    admit: Callable[[list], Optional[str]]
    #: 1 for a camera probe, 0 for a property read. The thumbnail, the payload
    #: and hook_decisions.configure_autofocus's dose accounting key off this.
    exposures_per_plane: int
    #: One line naming the criterion, for the payload and the printed envelope:
    #: "max tenengrad over [x,y,w,h]" / "centre of TIPFSStatus.Status in-range band".
    describe: str


def image_probe(ctrl, metric_fn, region, min_contrast) -> FocusProbe: ...
def property_probe(core, device, prop, in_focus_values=None) -> FocusProbe: ...
```

`sweep_autofocus` changes in two lines:

```python
-        metric_values.append(metric_fn(snap_to_numpy(ctrl)))
+        readings.append(probe.read())
...
-    best_idx = int(np.argmax(metric_values))
+    best_idx = probe.choose(readings)
```

and both callers replace their contrast branch with the same three lines:

```python
cause = probe.admit(sweep.metric_values)
if cause:
    restored = _restore(ctrl, entry_z)
    return AutofocusResult(..., converged=False, moved=False,
                           reason=_refusal(which, cause, restored["measured_um"]))
```

where `_refusal` appends the measured-restore sentence that `_flat_reason` and
`_edge_reason` each embed separately today.
`peak_interior` stays as it is and stays shared — for either probe, a chosen
plane at a sweep boundary means the window was too narrow, which is exactly what
happened three times in the table above, and it is `_edge_reason`'s claim
already.

`method="sweep"` is the right default for a property probe: coarse-then-fine
exists to save exposures and there are none to save.

### 2. The band reduction, and the gate that replaces `curve_contrast`

```python
# microclaw/autofocus.py
MIN_BAND_PLANES = 3


def longest_true_run(flags) -> tuple[int, int]:
    """(start, length) of the longest contiguous True run; (0, 0) if none."""


def _band_admit(readings, in_focus_values, step_um, lo_um, hi_um) -> Optional[str]:
    flags = [r in in_focus_values for r in readings]
    start, length = longest_true_run(flags)
    if length == 0:
        # The 2026-08-22 failure. Say what was excluded, so "not here" is a
        # result the session can act on instead of a search that trails off.
        return (
            f"No plane between {lo_um} and {hi_um} um read one of "
            f"{sorted(in_focus_values)} ({len(readings)} planes, {step_um} um "
            f"step). The focus is outside this window, or the step is coarser "
            f"than the lock's capture range. This sweep costs no exposures — "
            f"widen it or halve the step. Nothing was moved."
        )
    if length < MIN_BAND_PLANES:
        # Deliberately diagnostic, not prescriptive: microclaw does not know
        # this rig's capture range and must not carry a number for it. On the
        # 2026-08-22 rig the range is ~10 um and the hand search stepped 5 um,
        # which is this refusal even if the window had been right.
        return (
            f"Only {length} of {len(readings)} planes read in-range, which is "
            f"not a band. Either the {step_um} um step is comparable to this "
            f"lock's capture range, or the reading is intermittent. Re-run with "
            f"a smaller step around the hit. Nothing was moved."
        )
    if sum(flags) > length:
        return (
            f"The in-range planes are not contiguous ({sum(flags)} in range, "
            f"longest run {length}). Two separated bands are two reflecting "
            f"surfaces — on an oil rig, plausibly the coverslip and the sample "
            f"— not one focal plane. Nothing was moved."
        )
    return None
```

`choose` is `start + length // 2`. The `Status` ordinal makes the payload richer
than a single band: report the `Within range` run *and* the `Locked in focus`
run separately when both appear, since the second is a subset of the first and
its centre is the better target.

**The band centre is the centre of the lock's capture range, not the plane of
best focus.** They coincide only if the range is symmetric about focus, and the
session measured that it is not quite: locked Z ~2373.7, best offset 165.4 out
of a 0–1000 range. This call finds range; the offset finds focus.

### 3. `in_focus_values` declares a categorical reading

```python
def property_probe(core, device, prop, in_focus_values=None) -> FocusProbe:
    allowed = _strings(core.get_allowed_property_values(device, prop))
    if allowed and not in_focus_values:
        raise ValueError(
            f"{device}.{prop} reports one of {sorted(allowed)}. Name which "
            f"of those mean in-focus (in_focus_values)."
        )
    if in_focus_values:
        unknown = sorted(set(in_focus_values) - set(allowed)) if allowed else []
        if unknown:
            raise ValueError(
                f"{device}.{prop} never reports {unknown}; it reports one of "
                f"{sorted(allowed)}."
            )
    ...
```

`in_focus_values` is the caller's declaration that the property is categorical.
A non-empty device enumeration validates that declaration before the first move;
an empty enumeration does not turn a string-valued status into a numeric probe.
When enumeration is absent, the no-plane refusal lists the distinct observed
values so a mistyped declaration is self-correcting after one sweep.

**And the sweep must refuse on a precondition reading before it starts.** The
session's very first `Status` was `Dichroic mirror not inserted`: PFS was blind.
Sweeping 121 planes against a blind sensor produces the "no plane in range"
refusal above and sends the session looking for a focus problem that is really a
turret problem. So: read once at the entry plane, and if that reading is neither
in `in_focus_values` nor otherwise seen during the sweep as a varying value,
refuse with the reading quoted. Concretely — if **every** plane returns the
identical value and that value is not in `in_focus_values`, say so as a constant
reading, not as an absent band.

Nothing here names a Nikon. `TIPFSStatus`, `Status` and the value strings are
arguments; per `CLAUDE.md` they stay in gate docs, knowledge entries and rig
profiles. The session already saved one
(`strategies/nikon_pfs_focus_vs_image_autofocus`), which is where they belong.

### 4. A status read taken on arrival is not a settled status

`CLAUDE.md` records block 56's contract: *a device that is not busy is not a
device that arrived*. Collapsing the loop reintroduces it as a *reading* — the
lock's evaluation is asynchronous too, `FullFocusTimeoutMs = 5000`, and the
hand-driven loop got a second or more of accidental dwell for free from the
model round trip. Take that away and the sweep starts reading the previous
plane's status.

```python
def _stable_read(core, device, prop, dwell_s, samples=3, poll_s=STAGE_MOVE_POLL_S):
    """Return a reading only once `samples` consecutive reads agree.

    Same contract as settle_stage_move, applied to the other kind of reading.
    On timeout return the last value AND a flag; an unsettled reading is
    recorded as unsettled, never silently promoted to the plane's answer.
    """
```

`settle_ms` is the per-plane dwell; its default of 50 is tuned for a camera.
Report the dwell used and the count of unsettled planes; a sweep with unsettled
planes cannot converge. **This is the defect most likely to make the collapsed
search behave differently from the loop that worked by hand** — write the test
that drives a fake whose status lags one plane behind, and watch it fail first
(`CLAUDE.md` step 3: a fake that encodes your assumption is not a test of it).

### 5. Payload, dose, and the thumbnail that spends an exposure

- `_sweep_payload` maps `_round_sig` over `metric_values` (`tools.py:4334`) and
  breaks on strings. For a categorical probe emit `readings` (raw) and
  `in_range` (bools) and omit `metric_curve`; keep `metric_curve` for scalars so
  no existing consumer changes shape. The full plane→reading table stays in the
  payload: replacing sixteen visible calls with one must not delete the evidence
  the operator was reading off them.
- `focus_metric_at_final` and `return_thumbnail` both snap; suppress both when
  `exposures_per_plane == 0`, and say so rather than silently omitting them.
- `return_thumbnail` defaults to **`True`** in `run_autofocus`
  (`tools.py:4352`) while the schema advertises `default: False`. A JSON-schema
  default is documentation; when the model omits the key, Python's `True` wins
  and a frame is taken — every one of the four sweeps on 2026-08-22 returned a
  thumbnail nobody asked for. **Reconcile toward the code, not the schema**:
  advertise `default: true` so the model is not misinformed, and leave the image
  path returning what it returns today. Suppressing the snap on the zero-dose
  path (above) is what the "no exposures" claim actually needs; flipping the
  image path's default would be an unrelated regression riding along.
- Add `criterion` (from `probe.describe`) and `exposures_spent`. `converged`
  must not be readable as "the sample is in focus" when the criterion was a
  lock's capture range.

### 6. The tool schema, which is the only part the model ever sees

`run_autofocus`'s description opens "Run a software autofocus sweep to find the
sharpest Z plane." After this block that is half the tool, and the half that was
**wrong** on 2026-08-22. The schema has to carry three things: the new argument,
when to reach for it, and the fact that the two probes answer different
questions.

```python
# microclaw/tools_schema.py — run_autofocus, new property alongside `region`
"probe": {
    # A plain object with `properties` + `required`. NOT oneOf/anyOf: 54c
    # measured that a `oneOf` with no top-level "type" made the model quote
    # the value -- four consecutive refused `region` calls on this same rig,
    # 2026-08-19, on this same tool. Do not reintroduce that shape here.
    "type": "object",
    "description": (
        "Optional. Read a DEVICE PROPERTY at each plane instead of measuring "
        "image sharpness — for a hardware focus lock (Nikon PFS and similar) "
        "that reports whether it can see the coverslip. Costs NO exposures, so "
        "it is safe to sweep a wide window and to retry with a finer step. Use "
        "it whenever the rig has a focus lock: an image metric maximises "
        "SHARPNESS, and the sharpest plane is often the coverslip surface "
        "rather than the sample. The two answer different questions — this one "
        "finds where the lock can engage, then engage it and fine-tune from "
        "there. Omit for ordinary image-based autofocus."
    ),
    "properties": {
        "device": {"type": "string", "description": "Device label, e.g. the focus-lock status device."},
        "property": {"type": "string", "description": "Property read at each plane."},
        "in_focus_values": {
            "type": "array", "items": {"type": "string"}, "minItems": 1,
            "description": (
                "Required for a property with enumerated values: which of them "
                "mean in-focus. Call get_device_property_info first and copy "
                "them EXACTLY — a value the device never reports is refused "
                "before any Z move. Include partial states such as an "
                "in-range-but-not-locked reading; the sweep centres on the "
                "band they span. Omit only for a numeric property, which is "
                "maximised like an image metric."
            ),
        },
    },
    "required": ["device", "property"],
},
```

and the description gains one short paragraph, not a rewrite:

> A `probe` reads a device property at each plane instead of the camera —
> use it when the rig has a hardware focus lock. Without it the sweep
> maximises image sharpness, which finds the sharpest plane, not necessarily
> the sample plane. `method="sweep"` is the right choice with a `probe`: the
> coarse pass exists to save exposures and a property read spends none.

Two further schema-side obligations, both from measurement rather than taste:

- **Parse a quoted `probe`.** `_validate_metric_region` already parses a
  JSON-string `region` because this exact model on this exact rig quoted the
  argument four times in a row (`tools.py:3746`). A nested object is at least as
  likely to arrive as a string. Reuse that parse; do not write a second one.
- **The refusals are part of the schema's contract.** Every `raise ValueError`
  in `property_probe` (§3) must be reachable as an `{"error": ...}` payload
  rather than an exception, in the same style as `region`'s, so a wrong call
  teaches the model instead of ending the turn.

Also update the two descriptions that will otherwise contradict this one:
`get_focus_lock_state` / `set_focus_lock`, which today read as the way to reach a
focus lock and are EMU-only in fact ("Two defects", #2).

### 7. Export

`_emit_autofocus` gains `probe`; `_analysis_source(include_autofocus=True)`
gains `FocusProbe`, `image_probe`, `property_probe`, `longest_true_run`,
`_band_admit`, `_stable_read`, `MIN_BAND_PLANES`.
`test_emitted_inline_defines_every_name_it_uses` already enforces that list —
per `CLAUDE.md`, that test exists because block 13 added a helper while 41b was
in flight and every exported script raised `NameError` on the rig.

Device label, property name and value list are literals, so **no `CannotEmit`**.
The emitted envelope print carries the window, the step, the criterion and the
`in_focus_values` it compares against.

### 8. Stop when the band is found — the sweep's job is to reach the lock, not to map it

**Measured, Nikon 2026-08-22 evening** (`pfs-nikon-design56-3/`,
`20260822_231154_352122_microclaw_history.jsonl`). The probe worked: the fourth
sweep found the band and locked. What it cost:

| sweep | planes | window | result |
| --- | --- | --- | --- |
| 1 | 101 | 0.4–100.4 | no band |
| 2 | 101 | 100.7–200.7 | no band |
| 3 | 201 | 2149.8–2349.8 | no band |
| 4 | 201 | 2350.9–2550.9 | band **2358.9–2387.9**, converged, locked |

**604 planes. About six minutes of dwell alone**, before stage settling. And the
sweep that succeeded **read its first in-range plane at index 9 of 201** — then
swept 192 more planes to compute a band centre it did not need.

Two measurements refute assumptions in the sections above:

- **The capture band is ~29 µm on this objective**, not the ~10 µm the Problem
  section reasons from. Thirty consecutive in-range planes at 1 µm. Every
  argument here that treats a 5 µm step as necessarily too coarse is calibrated
  to the wrong number; the step that is "too coarse" is a property of the rig
  and must be *measured*, never carried in `microclaw/`.
- **The band centre is not needed.** The operator's rule: once any plane reads
  in-range, engaging the lock pulls it to focus by itself. Corroborated in the
  previous session, where arming PFS from 2355 drove the axis up ~20 µm and
  locked at 2375.

So the reduction §2 built — centre of the longest contiguous run — answers a
question ("where is this band?") that is not the question a focus hand-off asks
("is there anywhere in this window the lock can take over?").

**Decision.** A categorical probe stops at the **first plane that reads
in-focus**, leaves Z there, and reports it. `stop_when_found` turns the full
sweep back on for the case that really does want the band mapped.

```
probe={"device": ..., "property": ..., "in_focus_values": [...],
       "stop_when_found": true}   # default for a categorical probe
```

**What confirms a single plane, given §2's refusals no longer run.** Nothing in
the sweep — and nothing needs to. `_stable_read` (§4) already requires the
reading to hold across consecutive samples spanning a minimum interval, so a
transient cannot stop the sweep; and **the lock itself is the confirmation**.
Engaging it either reports `Locked in focus` or `Focus lock failed`, at zero
dose, immediately. That is a better test than three more property reads.

**What this gives up, stated plainly.** The non-contiguous refusal — the one
that says two separated runs are two reflecting surfaces — cannot fire on a
sweep that stops inside the first run. Coverslip-versus-sample discrimination
moves back to where the operator already does it: engage, then look for signal.
A caller who wants that discrimination sets `stop_when_found: false` and gets
§2's whole-curve behaviour, refusals included.

**A transient state must not be named as in-focus, and the early stop is what
makes that load-bearing.** Operator, 2026-08-23: the status reads `Focusing` for
about a second whenever the lock is engaged, including by a human mid-sweep.
Under §2's band reduction a stray transient contributed one plane and was
outvoted by `MIN_BAND_PLANES`; under this section it **stops the sweep**, at
whatever plane the transition happened to coincide with. `_stable_read` does not
save us — it asks the reading to hold for ~100 ms and a one-second transient
holds easily. So `in_focus_values` must name steady states only, and the schema
has to say so rather than leaving it to be discovered. The rig's own session on
2026-08-22 passed `["Within range of focus search", "Focusing", "Locked in
focus"]`; the middle one is the hazard.

**Payload.** Report `stopped_early`, `planes_read` and `planes_planned`, and
keep the plane→reading table for the planes actually read. A sweep that stops at
plane 9 of 201 must say so; "converged" over 9 planes and over 201 are different
claims and the payload must not blur them.

**Step size is the other half of this.** The same session swept 1 µm four times
and, asked why, answered: "I didn't have a real reason." Nothing in the schema
connects the step to what is being looked for. With an early stop, a coarse step
is both cheap and safe — the sweep ends as soon as it lands in the band, so the
only cost of a step too fine is time, and the only cost of a step too coarse is
stepping over the band. Say that in the schema, in those terms, without naming a
number: microclaw does not know this rig's capture range.

### 9. The sweep waits on nothing, and re-sweeps what it has already cleared (56b)

**Measured, Nikon 2026-08-23** (`pfs-nikon-design56-4/`,
`20260823_095336_954938_microclaw_history.jsonl`). The probe found the band and
locked. It also took, by the operator's stopwatch, **2–3 s per plane**:

| sweep | planes | outcome |
| --- | --- | --- |
| 1 | 184 read of 184 | no band, 951.5 → 2415.5 |
| 2 | 124 read of 141 | stopped early at 2664.7, locked |

**308 planes. About 91 of sweep 2's planes re-swept 1684 → 2416, which sweep 1
had already proved empty** — the operator did the arithmetic himself and asked
why the tool had not.

#### 9a. Two clocks, and we are neither of them

Per plane, in order: `set_position`; `settle_stage_move`, which returns only
after three in-tolerance samples **spanning ≥100 ms**, so the axis is confirmed
parked; then `time.sleep(PROPERTY_PROBE_MIN_DWELL_S)` = **500 ms**; then
`_stable_read`, three reads that must span a further **100 ms**.

**600 ms per plane of added latency, after the axis has already been still for
100 ms.** And it buys nothing. The Nikon PFS samples at **200 Hz — a 5 ms
period — "independent of microscope and camera control software"**
([microscopyu](https://www.microscopyu.com/applications/live-cell-imaging/nikon-perfect-focus-system),
retrieved 2026-08-23). By the time the dwell begins, the sensor has re-evaluated
about twenty times. There is nothing to wait for.

**Where the 500 ms came from, recorded so it is not repeated.** §4 argued the
evaluation is asynchronous and cited `FullFocusTimeoutMs = 5000`. That is the
timeout for the full-focus *search operation*, not the status refresh period — a
rate inferred from an operation timeout. The constant was then "validated"
against a lagging sensor written to embody that same assumption. A fake that
encodes the assumption is not a test of it, and this one was the coordinator's.

**Decision, as first written and then REFUTED on the rig — see §9d.**
`PROPERTY_PROBE_MIN_DWELL_S` goes to **0**. The dwell stays as an **optional
parameter** — other hardware may genuinely need it — as `dwell_ms` on the
`probe` object, described as extra wait for a property that updates *slower than
the stage settles*. It stops riding on `settle_ms`, whose image-path meaning is a
camera settle and is a different thing.

**§9d supersedes the "default 0" half of this.** The rest stands.

`_stable_read` keeps its consecutive-agreement check and **loses the mandatory
100 ms span**: three reads that must simply agree cost three bridge calls and no
sleeping, and a genuinely slow property makes them disagree and wait by itself.

**`settle_stage_move` is not touched.** It is block 56's gated contract and it is
what makes a reported plane position the measured one. Trading it for speed is
the wrong direction.

#### 9b. Sweep a window, not a range around wherever the stage happens to be

`run_autofocus` centres on current Z and takes `z_range_um`, so searching "the
part I have not searched yet" means moving the stage first and then computing a
half-width. On 2026-08-23 that produced a centre of 2242 with a range of 1116,
re-covering 730 µm of cleared ground; the operator's own arithmetic — centre
2608, range 384 — was the correct call and the tool made it the harder one to
express.

**Decision.** Accept an explicit window, `z_min_um` / `z_max_um`, as an
alternative to `z_range_um`. Supplying both forms is refused rather than ranked.
The window is guard-checked at both ends exactly as the centred form is.

#### 9c. A correct number, applied to the wrong quantity

`SYSTEM_PROMPT` says the PFS offset range is ~10 µm for oil, and **that is right**
— microscopyu gives ~10 µm oil, 20 µm water, 100 µm+ dry for the *offset* range.
But §Problem used it to reason about the width of the band `Status` reports
in-range, and those are different quantities: the band measured **29 µm** on this
oil objective (§8). Do not carry either number into `microclaw/`; the argument
that a given step is "too coarse" must come from a measurement, and the two
figures must not be conflated again.

## Two defects this session exposed, neither Nikon-specific

**1. A refusal that hands back the number it refused to act on.** All four
`run_autofocus` calls returned `converged: false, moved: false` — correctly —
and each refusal payload carried `coarse.best_z_um`. The model read that value
out of the refusal, moved there with `move_stage_z`, and re-swept; four times.
The fail-closed gate held and was routed around, and the plane it walked to was
the wrong one. `best_z_um` is legitimately diagnostic, so the minimal fix is in
the prose: `_flat_reason` and `_edge_reason` should state that the reported best
plane is the argmax of a curve that failed its gate and is **not** a focus
estimate to move to. Cheap, and worth doing as a precondition of 56a.

**2. `set_focus_lock` and `get_focus_lock_state` are EMU-only.** Measured:
`{"error": "No EMU configuration — cannot control a focus lock."}` on the rig
whose entire workflow is a focus lock. Worse, `get_focus_lock_state`
(`tools.py:7904`) returns `{"engaged": None}` in that case and `run_autofocus`
tests `if lock.get("engaged"):` (`tools.py:4391`) — `None` is falsy, so **the
refusal that exists to stop a Z sweep fighting an armed servo never fires on
this rig.** This design's premise is a Z sweep with the lock off, so it depends
on that refusal working. MMCore answers generically:
`core.get_auto_focus_device()` (already used, `rig_inventory.py:375`) and
`core.is_continuous_focus_enabled()`, both in try/except since not every adapter
implements them. Precondition of 56a.

## Rejected

- **A separate `run_pfs_search` tool.** It is `sweep_autofocus` with a different
  reading, and it would anchor a tool on one vendor's device.
- **A named-metric menu (`metric: "tenengrad" | …`).** A registry
  (`CLAUDE.md` §"Don't add layers"), it does not reach this case, and design/36
  is the record of what a wrongly chosen focus metric costs. An alternate
  *image* metric is already a keyword argument for a hook.
- **Generalizing the swept axis in the same block.** The offset sweep is
  `move_named_stage` and works.
- **Inferring scalar-vs-categorical from the first reading.** `float(reading)`
  succeeding says nothing; `get_allowed_property_values` is the device's answer.
- **Arming PFS at each plane.** Unnecessary — `Within range of focus search` is
  readable unlocked (§What the session measured). It would also be a write loop
  against a focus servo, at up to 5 s per plane.

## Refusals this must keep

- Flat image curve → do not move (`_flat_reason`), plus `_band_admit`'s three.
- Chosen plane at a sweep boundary → not convergence (`_edge_reason`,
  design/28 F1), for both probe kinds.
- Focus lock engaged → refuse the sweep, and make it actually fire.
- `guard.check_z` on both ends of the window, unchanged. A 120 µm window is
  larger than anything `run_autofocus` has swept before and the bound is what
  keeps it honest.

## What this does not buy

- **A focus plane.** The band centre is the centre of the capture range (§2);
  an offset step still follows, judged on the cell.
- **The "locked too high" check.** The jog test — lock, move XY ~10 µm, confirm
  the lock holds — validates a lock rather than searching for one. The session
  ran it by hand and it passed. Same property read, different shape; not this.
- **Sweep-direction independence.** A capture band may sit differently sweeping
  up versus down; the gate measures it.

## Blocks

### 56a — `probe`

Design: §0–7, "Two defects", "Rejected", "Refusals". Preconditions, both from
"Two defects": the generic focus-lock read, and the refusal prose; plus the
`return_thumbnail` default in §5.
Files: `microclaw/autofocus.py`, `microclaw/tools.py` (`run_autofocus`,
`_run_autofocus_passes`, `_sweep_payload`, `_flat_reason`, `_edge_reason`,
`_emit_autofocus`, `_analysis_source`, `get_focus_lock_state`),
`microclaw/tools_schema.py` (`run_autofocus`, and the
`get_focus_lock_state` / `set_focus_lock` descriptions per §6),
`microclaw/hook_decisions.py` (`configure_autofocus` dose accounting when
`exposures_per_plane` is 0), `tests/test_autofocus.py`,
`tests/test_session_script_export.py`.

Two tests are the block, not decoration: the §0 characterization test pinning
the no-probe path to `main`'s exact output, and a schema-reachability test that
drives the `probe` object through the same quoted-JSON path `region` needed —
54c shipped a capability the model could not call, and the unit tests could not
see it. Replace the
"just move the Z stage and check this property" paragraph in `SYSTEM_PROMPT`
with the tool in the same commit — a prompt that still describes the loop will
keep producing the loop.

**Rig gate 56a (Nikon).** Superseded by the combined runbook
`design/56-block56ab-rig-gate.md`, which gates 56a and 56b in one trip. The
limbs below are kept as the statement of intent; the runbook is what gets run.

- With PFS **Off** and Z at ~2300, **one** `run_autofocus` call with the probe,
  `z_range_um=120`, `z_step_um=1`, `method="sweep"`, `in_focus_values=["Within
  range of focus search", "Locked in focus"]`. It must find the band around
  ~2373 and leave Z at its centre. Read the history JSONL: **one** tool round,
  and the payload's plane→reading table has 121 rows. More than one round, or a
  band that does not bracket 2373, is this block failing.
- `set_device_property TIPFSStatus.State = On` immediately after → `Locked in
  focus` on the first attempt. Then `get_stage_position` on `TIZDrive`
  independently and compare it against the payload's chosen Z — `CLAUDE.md`
  step 6: 52a's third gate passed every stated limb and was caught only by that
  disagreement.
- Repeat the identical call with `z_step_um=5`. It must return the "not a band"
  refusal and move nothing — the 2026-08-22 step, shown to be too coarse.
- Repeat with `z_range_um=60` centred at 2300 (the window the session actually
  searched). It must return the "no plane between 2270 and 2330" refusal naming
  the window, and move nothing. This is the result the session was owed.
- A deliberately mistyped `in_focus_values` refuses **before any Z move**;
  `get_stage_position` unchanged across the refused call.
- With the dichroic out, the sweep refuses citing the constant reading rather
  than reporting an absent band.
- A plain image `run_autofocus` with no `probe` on the same sample still returns
  the same refusal it did on 2026-08-22 — same `contrast`, same `min_contrast`,
  same `best_z_um` — with the added sentence saying the reported best plane is
  not a focus estimate. Compare against the four rows in the Problem table; a
  different number here is the refactor having changed the old path.
- **The new refusal, on purpose.** With PFS armed, an image `run_autofocus`
  refuses and names the lock; on 2026-08-22 it would have run. Disengage,
  re-run, confirm it proceeds.
- `export_session_script`, then run the emitted file standalone. Grep it for the
  literal strings `Within range of focus search` and `Locked in focus`, written
  into the runbook as literals — not `<v1>`/`<v2>` placeholders (52c's
  strictest criterion produced no rig evidence because it shipped with
  placeholders and was run verbatim).

#### 9d. Measured: a zero dwell is right for stopping, wrong for mapping

**Nikon, 56ab gate, 2026-08-23.** The same window swept twice at 5 µm,
`stop_when_found: false`:

| dwell | planes read in-range | outcome |
| --- | --- | --- |
| `0` | 2655, 2660 | **refused** — two planes is not a band |
| `500 ms` | **2650**, 2655, 2660 | **converged**, moved to 2655 |

The 2650 plane is real and the fast read missed it. Cost of the dwell: **~30 s
over 121 planes** (1:20 → 1:50), about half what §9a predicted.

**Why §9a's argument was wrong, and it is worth naming the shape.** The 200 Hz
figure is the PFS servo's own sampling loop. The reading does not come from the
servo; it comes through Micro-Manager's TI adapter, which polls on its own
cadence. **A device's internal rate is not its property's update rate.** That is
the same error as §9c one level down: a correct number applied to the wrong
quantity.

**Decision (operator, 2026-08-23): the default follows the stopping rule.**

- `stop_when_found: true` (the default) → dwell **0**. A late read lands one
  plane *deeper into* the band, and engaging the lock confirms the plane at zero
  dose. Both early-stop sweeps in this gate found the band and locked.
- `stop_when_found: false` → dwell **`PROPERTY_PROBE_BAND_DWELL_S` = 0.5 s**.
  Band mapping is decided by the planes at the band's edges, which are exactly
  the ones a lagging property reports wrongly.
- An explicit `dwell_ms` wins in either mode, including `0`.

`FocusProbe` carries the resolved `dwell_s`, and the payload reports it from
there rather than recomputing it, so `property_dwell_ms` cannot disagree with
the sweep that ran. The emitted script receives `dwell_ms=None` when the caller
omitted it and resolves it by the same rule, so live and standalone agree by
construction rather than by a copied constant.

**The one case the early-stop tolerance does not cover:** a step so coarse that
the band is a single plane. Then a late read can land past it. That is
indistinguishable from a step too coarse to find the band at all, which is
already a refusal, and the lock is still the confirmation.

#### 9e. A second Nikon, and why naming the device was never enough

**Nikon Ti2-E / Andor Dragonfly, 2026-08-23** (`pfs-dragonfly-design56ab/`).
First cross-rig evidence for the probe, and it holds: same tool, entirely
different names.

| | Ti | Ti2-E / Dragonfly |
| --- | --- | --- |
| lock device | `TIPFSStatus` | `PFS` |
| property | `Status` | `PFS in Range` |
| in-range value | `Within range of focus search` | `In Range` |

Nothing in `microclaw/` had to change for the second rig — the probe took all
three as arguments, which is what §3 was for. `run_autofocus` found the band and
the lock engaged on both.

**But it still had to be asked.** On a cold session the operator wrote *"Why not
do a PFS search?"*, and only then did it call the probe — correctly, first try.
Later in the same session, once the rig's names were known, it went straight to
the probe unprompted. So the failure is not reluctance; it is **not knowing which
property to read**.

`get_focus_lock_state` named the *device* and stopped. The probe needs a
*property*, and finding one cost `list_device_properties` plus a
`get_device_property_info` per candidate — on a cold session, cheaper to give up
and reach for the camera. Two prompt edits had already tried to push the other
way and neither held, which is the signal that the fix is not more prompt text.

**Decision.** `get_focus_lock_state` returns the lock device's **read-only
properties with their current values**, plus a hint naming `run_autofocus`. One
call now carries everything the probe needs.

The values are what disambiguate, and no rule about names could: the two rigs
share none, and the Dragonfly's `PFS Status` reads `0000001100001010` — a
bitfield sitting right next to the useful `PFS in Range`. Seeing
`{"PFS Status": "0000001100001010", "PFS in Range": "In Range"}` makes the choice
obvious; seeing the two names alone does not. Both rigs' measured property sets
are the test fixtures.

### 56b — `dwell_ms`, and an explicit window

Design: §9 (9a, 9b, 9c). Files: `microclaw/autofocus.py`
(`PROPERTY_PROBE_MIN_DWELL_S`, `_stable_read`, `property_probe`),
`microclaw/tools.py` (`run_autofocus`, `_run_autofocus_passes`,
`_emit_autofocus`), `microclaw/tools_schema.py`, `microclaw/agent.py`,
plus their tests.

**Rig gate 56b (Nikon).** Two limbs, both comparisons rather than verdicts.

- **The dwell finally gets measured.** Sweep one window that contains the band
  twice, `stop_when_found: false` both times, at `dwell_ms: 0` and
  `dwell_ms: 500`. The two in-range bands must be **identical**, and the
  wall-clock time per plane must fall. Identical bands is the evidence that 0 is
  right; a shifted band at 0 is the evidence it is not, and the shift is the
  measurement of this device's true latency. This is the limb §4 should have had.
- **The window is not re-swept.** With `z_min_um` / `z_max_um`, search a span
  adjacent to one already cleared and confirm from the payload that
  `z_positions` starts at `z_min_um` and that no plane below it was read.

Step-10 design gate: record in `CLAUDE.md` §"The pycro-manager acquisition
engine" that an asynchronous *reading* settles no faster than an asynchronous
*move*, and that a hand-driven loop hides that behind its round-trip latency;
and update `design/40`'s "lock is binary" finding, which this session
supersedes.

## Owed rig evidence — read this before trusting a limb

**56a and 56b merged 2026-08-23 by operator decision, with the gate partly run
and four changes never on a rig at all.** The operator was losing access to both
Nikons; merging working code beat holding it behind a trip that could not be
booked. This section is the price of that, and the runbook
(`design/56-block56ab-rig-gate.md`) is **merged to `main` with the code** rather
than dying with the branch, so the owed limbs still have their instructions.

**What the rig did establish** (Nikon Ti, `pfs-nikon-design56ab-2`, and
Ti2-E/Dragonfly, `pfs-dragonfly-design56ab`, both 2026-08-23):

- The probe finds the band and the lock engages, **on two rigs sharing no device
  label, property name or value string** (§9e).
- The armed-lock refusal fires on a non-EMU rig — impossible before this block.
- All four argument refusals, the missed-window and blind-sensor refusals with
  their values quoted, the explicit window, and D3's observed-value list, which
  recovered a mistyped `Within range of focus` in the operator's own hands.
- The dwell measurement of §9d, which refuted §9a.

**What is owed, in the order it should be re-run:**

1. **`set_focus_lock` export.** The gate's exported script died on it
   (`NOT EMITTED: … has no device prefix`). Fixed, with a test reproducing the
   rig's exact error string — but the fixed script has **never run on hardware**.
   Runbook Step 15.
2. **The mode-dependent dwell defaults** (§9d). The 0-vs-500 comparison ran and
   decided the design; the *defaults implementing it* did not. Runbook Step 7,
   including its third call with no `dwell_ms` key, which must report 500.
3. **`status_properties` / `probe_hint`** (§9e). No live model has seen this
   payload. It exists because two prompt edits failed to make the model offer
   the probe on a cold session; whether a payload succeeds where prose did not
   is exactly what is unverified.
4. **Runbook Step 2** — the lock offered without being asked. It has never passed
   cleanly: once it offered and then hand-walked Z, once it needed
   *"Why not do a PFS search?"*. Item 3 is the intervention aimed at it.

**None of these is testable on M2 or M5**, which have no hardware focus lock.
They need a Nikon.

## Run ledger

| Block | Branch | Start commit | Implementer | Rig gate | Merged | Design reconciled |
| --- | --- | --- | --- | --- | --- | --- |
| 56a | `design56/probe` | `3158546` | codex, 4 rounds + 6 coordinator fixes | **partial** — Nikon Ti + Ti2-E/Dragonfly 2026-08-23; see "Owed rig evidence" | MERGE | design gate below |
| 56b | `design56/probe` | `1b74016` | codex, 1 round + 2 coordinator fixes | **partial** — §9d measured on the Ti; the defaults implementing it are unrun | MERGE | design gate below |

**Baseline on the start commit, coordinator-measured:** 1968 passed / 99 skipped
/ 3 warnings. `main` is green — the five `tests/test_agent.py` failures that
design/35's block-56 note worked around were fixed on `main` by `d116298`, so
this block has no known-broken baseline to gate around.

56a assigned. `design/55` is written and unstarted and touches
`microclaw/tools.py` and `tests/test_session_script_export.py` in different
regions (the acquisition preamble and hook capabilities, not autofocus);
sequence rather than assume no conflict.
