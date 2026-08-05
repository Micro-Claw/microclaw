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

## F9 — laser-off mutated M5's TTL prerequisites

On M5, turning a laser off left all `Laser N: 4. Use TTL` values false. A later
enable did not restore them, and `_assert_excitation_will_fire` checks the EMU
trigger mode and sequence but cannot see this independent iChrome prerequisite.
This is an M5-specific interaction among EMU, MicroFPGA, and the Toptica iChrome;
it must not be generalized into the demo-shaped `microclaw/` device model.

**Deferred — mechanism unimplemented in this block.** The required product fix is
a configured, general arming chain in `safety_config.yaml`: declared prerequisites,
preflight verification, guarded record/restore with read-back, authorization-map
coverage, and state reporting. None of those pieces is implemented here, so this
block does not close F9 and laser acquisition can still pass the existing EMU-only
preflight while an independent prerequisite is false.

Offline conclusion and rig gate: after every supported laser-off path, record the
iChrome TTL values; then enable each excitation slot through the normal workflow
and verify that the same slot's TTL property is restored before a single low-dose
test frame. Until that gate establishes a generic, configured semantic mapping,
the implementation must refuse to invent a device/property rule. The coordinator
will place the exact PowerShell-safe probe in the M5 gate runbook.

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
