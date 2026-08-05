# design/38 — findings from the M5 plus-acquisition sessions (2026-08-04)

Sources: the `plus-acquisition` session at commit
`680ff835785e1379b3245ecfa5d63568218095f7` and the
`microclaw_plus_test` session at `7622d9b`. The flattened transcripts and
original JSONL are retained in the returned evidence folder, not committed.
The captured NDTiff datasets remain in the two project folders on M5.

Both sessions attempted one five-tile “+”. Neither produced a mosaic. Each
instead exposed the sample twice: session A acquired ten frames before a hook
artifact error hid the completed dataset from the agent; session B acquired ten
frames plus four calibration snaps, then tried six calibration-reference shapes.

## F1 — the calibration reference discriminator was undocumented

The schema named `artifact`, `knowledge_version`, `confirmed_current`, and
`legacy_derived`, but did not say that `kind` is the discriminator. Six plausible
objects were rejected. The schema now publishes the exact tagged union:

```python
{"kind": "artifact", "path": "calibration.json"}
{"kind": "knowledge_version", "key": "stage_camera_affine_..._sha256_..."}
{"kind": "confirmed_current", "objective": "20x", "binning": 1}
{"kind": "legacy_derived", "pixel_size_um": 0.1056,
 "objective": "20x", "binning": 1}
```

Omission is also documented: it asks the resolver to use calibration recorded in
the dataset.

## F2 — recorded geometry was usable; incomplete identity blocked it

The original diagnosis was wrong. It inferred from the summary blob that the
real MM dataset lacked the affine representation microclaw understood. Opening
`plus_mosaic_2` through NDTiff shows that all five frames carry the ordinary
six-value, semicolon-delimited `PixelSizeAffine`, which the pre-existing parser
already handled. What prevented `calibration_ref=None` from resolving was the
identity-completeness gate: M5 recorded no objective label, and its camera model
was under the previously unprobed `<camera>-CameraName` vendor key.

The fixes that unblock the captured dataset are therefore:

- recognize `<camera>-CameraName` when completing camera identity;
- accept acquisition-recorded geometry with an unrecorded objective, while
  setting `objective_unrecorded: true`, retaining a reason, and surfacing that
  degradation as a mosaic warning.

The four-value summary `AffineTransform` support remains useful as a secondary
fallback for datasets where every selected frame omits `PixelSizeAffine`. The
real `plus_mosaic_2` summary has:

```text
AffineTransform =
0.004927971153294251_-0.10638852331845677_
-0.10452770834076626_-0.00512650316309355
```

The shared parser applies the same all-zero, identity, non-finite, and singular
sentinel refusals to both forms. Per-frame metadata remains authoritative;
summary metadata is used only when every selected frame omits its affine.

`plus_mosaic_2` contains both forms. Under the precedence rule its per-frame
`PixelSizeAffine` is used; the summary form remains the fallback for datasets
whose selected frames omit that key. They encode the same asymmetric matrix in
different flat-array orders: MMCore's six-value vector is
`m00,m01,m02,m10,m11,m12`, while Java `AffineTransform.getMatrix` writes
`m00,m10,m01,m11[,m02,m12]`. Micro-Manager's own `AffineUtils.doubleToAffine`
performs the corresponding reordering, and its `affineToMeasurements` comment
records the Java order. See the
[Micro-Manager AffineUtils source](https://github.com/micro-manager/micro-manager/blob/fe03637f48b69de4a72cab63681d031708657222/mmstudio/src/main/java/org/micromanager/internal/utils/AffineUtils.java)
and [Oracle `AffineTransform.getMatrix` contract](https://docs.oracle.com/en/java/javase/21/docs/api/java.desktop/java/awt/geom/AffineTransform.html#getMatrix(double%5B%5D)).
The captured asymmetric pair is a regression test: both representations must
decode to identical `(a, b, c, d)` values, so swapping `b` and `c` cannot hide
behind symmetric identity/sentinel fixtures.

## F3 — a newly measured calibration could not be referenced

`calibrate_stage_to_camera` returned `knowledge_key`, while the resolver accepts
an immutable `knowledge_version.key`; none of the four reference shapes accepts
`knowledge_key`. The result now also includes a directly reusable object:

```python
{"calibration_ref": {"kind": "knowledge_version", "key": key}}
```

The old field remains for compatibility.

## F4 — six distinct bad shapes produced one opaque error

`calibration_ref must be one tagged object` neither named the missing key nor
showed a valid shape. Resolver errors now name the `kind` discriminator and list
all four accepted shapes. Unknown kinds receive the same list.

## F5 — hook documentation reversed `EmitArtifact`

The dataclass contract is `EmitArtifact(filename, payload)`. The documentation's
payload-first prose caused the generated mosaic hook to construct it backwards.
The reference now gives the positional contract and recommends keywords:

```python
EmitArtifact(filename="mosaic.tiff", payload=canvas)
```

## F6 — preflight checked callback shape but not the artifact call

`validate_hook_contract` checked only class and callback arity. It now detects the
observed, statically evident payload-then-filename call and rejects it before any
exposure. This remains static validation, not execution of untrusted code.

## F7 — a malformed action aborted after exposure and hid the dataset path

The untrusted adapter used to re-raise an `EmitArtifact` parse failure. The frame
was already exposed and the dataset existed, but the acquisition tool never
returned its deterministic path. Malformed action proposals are now recorded as
refused and the frame is retained; arbitrary exceptions in hook analysis still
fail loudly. A bad optional artifact can no longer convert a completed capture
into an apparently missing dataset.

## F8 — artifact inspection had no work bound

`inspect_artifacts` recursively enumerated a tree and SHA-256ed every byte. On the
project root this ran for ten minutes. It now has deterministic, caller-adjustable
limits for file count, total hashed bytes, and recursion depth, plus a `hash=false`
listing-only mode. A refusal includes the partial per-directory survey with direct
file counts and byte totals so the caller can locate and narrow to a dataset.

## F9 — session teardown closed M5's arming prerequisite

The returned M5 gate established the mechanism. `g1_config.txt` shows
`All: 3. TTL Enable` in `illumination.shutters` with `off_value: '0'`, alongside
the other aggregate and per-source enables and `Core.AutoShutter`.
`SafetyGuard.shutter_all` ran in `finally` on both session front ends, including
the Ctrl+C route used between G1 and G2. G6.c confirmed the write-through effect,
and G2's opening reads confirmed the next session inherited it: the aggregate
property and all four per-source `Use TTL` properties read `0`, the first source
enable read `0`, and its status had changed from `AVAILABLE ENABLED USETTL` to
bare `AVAILABLE`. The operator changed nothing between sessions.

This round reverses design/14 §3. That section added teardown `shutter_all` after
a session ended with a 638 nm laser left at 25%. The operator has ruled that
silent state mutation on exit is the larger hazard: it breaks the intended
move-in/move-out workflow and, as G6.e demonstrated, can leave a rig that reports
a successful acquisition while emitting nothing. Session exit now performs no
illumination write. It instead names every declared illumination property whose
current value differs from `off_value`, and names every failed read. The explicit
`shutter_all` capability remains available only on operator request. The trade is
real: illumination microclaw turned on can now outlive its session; the conspicuous
per-property exit report, rather than silent mutation, covers that hazard.

G6.e also established the limit of the old preflight. With this prerequisite
closed, `run_timelapse(laser_slot=3)` passed its trigger checks, while the captured
frame matched background rather than the with-prerequisite control. The preflight
now says only that the EMU trigger line is armed, records the trigger mode and
sequence it checked, and explicitly says it did not verify device-level enables,
declared illumination properties, or the emission path. System state and the
acquisition result surface every declared illumination property as factual values
without deciding which values are required.

### F9 follow-up direction — operator-authored arming chains

The missing fact is which declared illumination properties must be on for a
given source to emit. It cannot be derived from the declaration: in working M5
images `All: 1. Enable` and `All: 2. Emission` were `0`, `All: 3. TTL Enable` had
to be `1`, and three of four per-source enables were correctly `0`. Only the
operator can classify these roles.

`first_launch.py` already asks the operator to classify each candidate property
as emission/enable, power, ordinary, excluded, or unresolved, then authors
`illumination.shutters`. The follow-up should add one question for properties
classified emission/enable: is it a per-source enable, where off is normal when
that source is unused, or an arming prerequisite, which must be on for a source
on that device to emit?

The requirement belongs in reviewed, versioned, fail-closed `safety_config.yaml`,
not in the agent-writable knowledge base. Knowledge may retain observations and
prompt the operator to declare them, but cannot authorize its own refusal. An
absent arming-prerequisite declaration means no check, preserving current
behaviour on rigs without one. M5's status string happens to expose whether its
operator declaration is correct without an exposure; that is useful gate
evidence, not a vendor-specific product contract.

## F10 — multiposition acquisition did not own live-view state

`run_multiposition_with_autofocus` paused and restored live view, but
`run_multiposition_acquisition` did not. Both now use the shared pause helper for
their acquisition window. The helper verifies a requested restart with
`CMMCore.isSequenceRunning()` and results distinguish requested from observed
restoration. The Java hint permits one retry for an auto-paused snap but warns
against blindly repeating a multiposition acquisition.

## F11 — autofocus multiposition required prior marking

The non-autofocus tool accepts either stored `position_names` or raw `positions`,
but the autofocus variant accepted names only. `validate_positions` deliberately
does not mark or move, so the suggested workflow could never satisfy the call.
The autofocus tool and schema now accept the same exclusive choice. Raw XY(Z)
coordinates are guard-checked and visited without mutating the position list.

## Additional anomaly A — `ContinueSurvey` was refused on every fixed frame

The mosaic hook proposed `ContinueSurvey`, but this fixed multiposition runner
already owns the full event list and records the action as
`unsupported-by-this-runner`. That refusal is correct: accepting it would either
duplicate a planned exposure or pretend to control a queue it does not own. The
hook should return measurements and `EmitArtifact` only for a known-up-front
five-tile run. `ContinueSurvey` belongs to `run_adaptive_survey`.

## Additional anomaly B — measured and configured pixel geometry disagreed

The session compared 0.1033 µm measured pixel size with MM's configured
0.1056 µm. The recorded affine is off-diagonal-dominant, so a scalar alone loses
the camera rotation/reflection needed for stage placement. This is not enough
evidence to overwrite either calibration: the ~2.2% scale difference and affine
orientation require a rig gate against a known displacement/landmark. Mosaic
provenance must retain the four affine terms and their source, not silently
substitute the configured scalar.

## Smaller finding — first-launch recommendation remains label-based

The known `first_launch.py` recommendation keys on a device label. An unrelated
rig with a device labelled `TTL` exposing `State0` would therefore receive it
spuriously. It fails closed and still requires human confirmation. Generalising
the match to the underlying property shape is a candidate for whichever future
block next opens `first_launch.py`; it is deliberately unchanged and unscheduled
here.

## Exposure-planning correction

For one five-tile product, the agent should offer the single-dataset path first:

```python
run_multiposition_acquisition(
    positions=validated_plus,
    protocol="timelapse",
    protocol_params={"n_frames": 1, "interval_s": 0},
    hook_strategy="saved_mosaic_hook",
    ...,
)
```

That is Option A: five exposures, one dataset with a `position` axis, and one
hook log. Option B is the legacy per-position dataset loop. The agent must not run
both unless the operator explicitly asks for both; doing so is not a fallback but
a second dose to the same sample.

## G7 — the exit-behaviour fix, gated on M5 2026-08-05

Evidence: `design38-g7-m5/`. **PASS on every step.**

- **G7.b (Ctrl+C, web-GUI session) and G7.c (clean `exit`)** — both printed the
  exit report and wrote nothing:

  ```
  [microclaw] EXIT ILLUMINATION NOT OFF: iChrome-MLE-TCP.All: 3. TTL Enable = '1' (off_value='0')
  [microclaw] EXIT ILLUMINATION NOT OFF: iChrome-MLE-TCP.Laser 1: 1. Enable = '1' (off_value='0')
  ```

  The state survived: the session started immediately after the Ctrl+C snapped a
  frame with `focus_metric_valid: true`, SNR 3.64, max 655 — i.e. the laser was
  still emitting, where G6.e's gated-off frame peaked at 224. Note the direct
  Property-Browser read after exit was not captured; the pass rests on that
  functional evidence plus the exit report's own readings.

- **G7.d** — `shutter_declared_illumination` drove all 21 declared properties off
  on request. The capability survives; only its automatic invocation is gone.

- **G7.e** — the corrected preflight returned exactly its scope:

  ```json
  {"guarantee": "trigger line is armed",
   "checked": [{"kind": "trigger mode", "value": "4 - Follow"},
               {"kind": "trigger sequence", "value": "65535"}],
   "not_verified": ["device-level enables", "illumination properties", "emission path"]}
  ```

  More importantly it changed the agent's behaviour. **Before** exposing, unasked,
  it said the preflight "does not verify the per-laser enable, the emission state,
  laser power, the emission filter in the path, the shutter, or that photons
  actually reach the camera." In G6.e the same agent had said "the trigger line
  was verified to fire" while the laser was gated off. `get_system_state` and the
  timelapse result both carried `declared_illumination_properties`.

### F12 — a property write can report failure after it has succeeded

Found incidentally in G7.a. `set_device_property` on `All: 3. TTL Enable`
returned:

```
Exception: java.lang.Exception: Cannot set property "All: 3. TTL Enable" to "1"
[ Error in device "iChrome-MLE-TCP": Serial timeout occurred. (17) ]
```

The agent read the property back and found it was `1`: **the write had landed and
the error was the acknowledgement timing out.** The agent handled it correctly,
but nothing in microclaw made it do so.

This is the same shape as the failed-write-that-landed defect design/32 Block 7b's
gate caught, so it recurs. It matters most for illumination: an operator told a
laser-enable write failed may believe the laser is off when it is on, or retry and
double-apply. Candidate fix: on a write exception, read the property back and
report `write_reported_failure_but_value_changed` with both values, rather than
surfacing the raw exception and leaving the caller to guess. Not scheduled.

### F13 — the agent does not know it can now read illumination state

On exit in G7.c the agent told the operator "I can't confirm the illumination
state on my own." That is no longer true: `get_system_state` returns
`declared_illumination_properties`. The round-4 prompt change teaches the agent to
consult it after a blank or low-signal frame, but not at session end or handoff.
One prompt line. Not scheduled.

## Round 4 composition-gate findings (2026-08-05)

Composition itself passed on M5: one acquisition produced one five-position
dataset, 5/5 fields, and one 14-entry attributed log with both hooks interleaved
per tile. Sweep dose reservations were exact: 75 planned / 70 hook-extra frames
for 10 µm / 1 µm, and 145 / 140 for 40 µm / 0.5 µm.

Two pre-exposure defects blocked the inline mosaic. Saved source was checked
against the current hook contract only when saved, so the hash-pinned Session A
stitcher with `EmitArtifact(mosaic, self.out_name)` loaded and failed on the last
tile. Resolve now repeats the same non-executing AST contract validation;
`describe_hook.resolve_refusal` is built from that same analysis. Correct older
hooks remain loadable. The same AST analysis marks saved hooks that can emit.
Reviewed pre-coded hooks declare `can_emit_artifacts` on their class.

An emitting hook without `artifact_limits` is now refused during planning,
before acquisition or autofocus exposure, with the emitting strategy name and
the three required limit fields. This deliberately refuses instead of applying
a default: artifact size, count, and total bytes are operator policy, and a
silent default would invent new write authority. Composition checks every child.

### Autofocus convergence observation (register; no fix in this block)

The returned Round 4 tables were internally tight but differed between sweeps:

| Run | Coarse/fine request | Five-field result | Reported state |
|---|---|---|---|
| H1 | 10 µm / 1 µm | approximately 42.97 µm at all five tiles; within-run spread only a few nm | all `converged: true` |
| H2 | 40 µm / 0.5 µm | approximately 42.47 µm at all five tiles; within-run spread only a few nm | all `converged: true` |

The approximately 0.5 µm disagreement is within one step of the coarser sweep,
so sampling is plausible and this is not diagnosed as a defect. Retain both
tables with the gate evidence and watch for recurrence: two tightly converged
runs landing on opposite sides of that offset is the uncertainty design/28 F1
was intended to keep visible.
