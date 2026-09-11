# Safety configuration

Run `microclaw --setup-write-security-config serve`. Restricted setup discovers
the live stage axes, captures operator-chosen safe endpoints, and records the two
large-acquisition warning thresholds. It shows the exact schema-3 YAML and target
before one confirmed write, then requires a normal restart. `microclaw check-config
PATH` remains available for offline validation of a hand-authored schema-3 file.
Security bounds are enforced before every normal hardware tool call and cannot be
overridden by the AI.

The packaged `safety_config.example.yaml` is a fictional hand-authoring example.
Setup generates only the axes actually found on the rig, and nothing else — no
`camera`, `illumination`, `channels`, `plugins` or `property_authorization`
section is invented for you:

```yaml
# Every number here is fictional. Prefer in-app setup for a real rig.
schema_version: 3
reviewed: true
stage:
  x_min: -12345.0
  x_max: 12345.0
  y_min: -6789.0
  y_max: 6789.0
  z_min: 123.0
  z_max: 7890.0
named_stages:
  - device: FictionalPiezoZ
    min_um: 12.0
    max_um: 234.0
acquisition:
  confirm_above_frames: 500
  confirm_above_duration_s: 1200
```

`stage.z_min`/`z_max` bound the **core focus device only**. Every other
label-addressed stage needs its own `named_stages` entry: they fail closed, so a
stage with no entry cannot be moved at all.

Two defaults follow from what a section's *absence* means, and they matter most
on a setup-generated file, which omits both:

- No `property_authorization` section means `mode: degraded_trusted_plugins`.
  Adding the section switches the rig to `guaranteed` and makes `denied`
  mandatory.
- No `plugins` section means hardware-motion plugin hooks are **permitted**.
  Writing the section — even with just `blocked: []` — turns them off until you
  set `allow_hardware_motion: true` deliberately.

`allowed_categorical` is the reviewed list of discrete (non-continuous)
device properties the AI may write directly. Filter wheels, sliders and turrets
do not belong on it: any device Micro-Manager types as a **StateDevice** has its
own `Label`/`State` auto-classified as categorical at startup, and nothing else
on that device. Shutters are never auto-classified — `Core.Shutter`, an MM
`ShutterDevice`, and anything declared under `illumination` stay on the
illumination gate, which is where a confirmation is required before light
reaches the sample. `microclaw --safety-config … authorization-map` prints the
effective map; auto-classified entries carry `"source": "auto:state-device"`,
declared ones `"source": "declared"`.

Auto-classification fills vacuums only. Naming a device's `Label` **or** `State`
in `allowed_categorical` or `denied` means you own both: declare
the one you will actually write, and the other stays refused. So if you are not
sure whether a driver takes `Label` (string) or `State` (int), declaring one does
not quietly hand you the other.

The following fragments show the optional limits and a separate worked channel
profile (these values are examples, not defaults):

```yaml
stage:
  x_min: -5000.0
  x_max:  5000.0
  y_min: -5000.0
  y_max:  5000.0
  z_min:  0.0
  z_max:  200.0

named_stages:
  - device: PIZStage
    min_um: 0.0
    max_um: 200.0

camera:
  max_exposure_ms: 5000.0

# Optional. Omitting min_snr keeps the built-in analysis default.
analysis:
  min_snr: 3.0

# Counts are frames, times are seconds/ms as named, and bytes are raw camera
# payload estimates. Hard maxima refuse work; confirm_above_* values invoke the
# blocking acquisition confirmation below those maxima. Only the two
# confirm_above_frames / confirm_above_duration_s keys are required — those are
# what setup writes; the rest are optional and each must be finite and positive.
acquisition:
  max_frames: 10000
  max_duration_s: 3600
  max_bytes: 50000000000
  max_illuminated_ms: 600000
  max_session_illuminated_ms: 1800000
  confirm_above_frames: 500
  confirm_above_duration_s: 300
  confirm_above_bytes: 5000000000
  confirm_above_illuminated_ms: 60000
```

List-backed pycro-manager acquisitions pass their event lists directly to the
engine. They cannot be cancelled mid-run; this is not new, because they never
could be. Generator feeding was measured at 3.14x the list cost and removed;
the evidence and decision are recorded in design/32 §2. Adaptive surveys
still require generators because later events do not exist until a hook
produces them. `max_duration_s` bounds a known-low preflight estimate: exposure
and scheduled start times are included, but unmeasured readout, stage,
autofocus, and filter switching overhead is not. MMStudio MDA is planned and
confirmed from its current settings, and its opaque `run_acquisition()` call
runs to completion.

Adaptive survey reservations cover exactly the planned grid size. A hook may
choose or revisit events within that allowance, but it cannot add an extra
derived revisit beyond the planned frame count; budget exhaustion is logged and
reported as an early stop.

In guaranteed mode, a channel preset is authorized by both its name and every
device/property effect Micro-Manager expands it to. Filter wheels, sliders and
turrets need no declaration — see the note above — so if `DAPI` only moves those,
the name is enough. Declare any other discrete effect, such as a laser selector.
A missing preset, an unlisted effect, or a preset effect requiring a deferred
typed executor is refused at startup, so leave `channels` absent until the names
and all their effects have been reviewed on this rig.

```yaml
property_authorization:
  mode: guaranteed
  allowed_categorical:
    - {device: LaserSelector, property: Label}
  denied: []
  # Continuous properties the AI may write directly, each with its own typed
  # policy. kind is absolute-position (units: um), illumination-power (units:
  # percent, or native with a full_scale), or bounded-numeric (any non-empty
  # unit string you supply). minimum and maximum are required and finite; a
  # bounded-numeric pair may not alias a declared illumination property.
  allowed_numeric:
    - {device: PIZStage, property: Position, kind: absolute-position,
       units: um, minimum: 0.0, maximum: 200.0}
channels:
  allowed: [DAPI]

# Denylist for raw property writes. Applies in degraded_trusted_plugins mode;
# a guaranteed-mode allowlist is the stronger gate and is preferred.
forbidden_properties:
  - {device: TIRF Stage, property: Position}

# Anything that puts light on the sample. Setting a shutter to any value other
# than its off_value takes a blocking human confirmation, unless
# require_confirm_on_enable is explicitly false. max_power_step_factor bounds
# how fast power climbs between consecutive writes; it is a runaway backstop,
# not a ramp mechanism (hooks implement ramps themselves).
illumination:
  shutters:
    - {device: LaserShutter, property: State, on_value: "1", off_value: "0"}
  power_properties:
    - {device: Laser640, property: PowerSetpoint, units: native, full_scale: 100.0}
  max_power_percent: 40.0
  max_power_step_factor: 2.0
  require_confirm_on_enable: true

# Optional filesystem boundary for paths microclaw writes or serves (acquisition
# data, logs, position-list saves, TIFF exports, and artifact downloads). Unset =
# unrestricted. When set, writes and served files are confined to this directory;
# `..` and symlink escapes are rejected. Local reads remain unrestricted, so this
# is not a sandbox for hook code or plugins.
# workspace_dir: /data/microclaw

# Micro-Manager plugin hooks run arbitrary Java that bypasses the checks above,
# so they have their own two gates.
plugins:
  # Fully-qualified classpaths to forbid. Analyzer (read-only) plugins are
  # allowed by default; list only the ones to block. Normally empty.
  blocked: []
  # Hardware-motion plugins (e.g. autofocus) are gated behind this single flag,
  # not a per-plugin list. Writing this section at all defaults the flag off, so
  # this explicit false is what a config without the section does NOT get.
  allow_hardware_motion: false
```
