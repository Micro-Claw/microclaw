# Repository review: top five issues

Date: 2026-07-21

## Scope and prioritization

This review covered the application entry points, agent loop, web server,
hardware tools, safety configuration/guard, generated-hook lifecycle, package
metadata, and the non-integration tests. The issues below are ranked primarily
by possible harm to the microscope, sample, operator, or credentials, and then
by likelihood and maintainability cost. They are proposals, not implemented
changes; the snippets are deliberately small design stubs.

The test baseline was 769 passed, 1 skipped, and 2 failed. Both failures were
caused by this review environment refusing local socket binds
(`tests/test_webserve.py:749` and `:737`), rather than by an observed application
regression. The 97 hardware integration tests were excluded.

## 1. A `reviewed: true` safety file can still contain ineffective limits

**Priority: P0 — hardware safety / validation**

`load_safety_config` treats the single boolean as sufficient approval and then
constructs dataclasses with mostly optional values (`microclaw/config.py:38-42`,
`microclaw/safety.py:16-31`). There is no semantic validation that minima are
below maxima, required rig limits exist, exposure is positive, values are
finite, or device/property entries are unique. The runtime comparisons at
`microclaw/safety.py:193-228` also allow `NaN` through because every comparison
with it is false. Thus a typo such as `max_exposure_ms: .nan`, an omitted
`camera` section, or inverted bounds can be human-marked reviewed while
providing no useful safety boundary.

Unknown keys are only partially caught, and the difference matters. A stray key
*inside* a known section (`stage: {x_mim: 0}`) does raise, because
`StageConstraints(**stage_cfg)` rejects an unexpected keyword — but as a bare
`TypeError` at construction, not a message that names the file and the typo. A
misspelled *section* name is worse: `stagee:` falls through `cfg.get("stage",
{})` to an empty dict and is dropped in complete silence, so the entire block of
limits the operator wrote is discarded with no error at all. A strict schema
replaces both behaviours with one clear, file-anchored validation error.

The safety configuration is the wrong place for permissive parsing. It should
be a strict, versioned schema that fails startup with all validation errors.
Validation has four distinct jobs, and the implementation should keep them
explicit rather than relying on dataclass construction to cover them:

1. **Structure:** the document root and every section must be mappings, values
   must have the expected primitive types, and unknown keys are rejected at the
   top level as well as inside sections.
2. **Declared-field completeness:** when a constraint is declared, all fields
   needed to interpret it must be present — for example a `named_stages` or
   illumination `shutters` entry that omits `device` or `property`. Proving that
   the declarations cover every actuator the tools can reach requires the rig
   profile and live cross-check in `design/33-authorization-map.md`; file parsing
   alone cannot establish that.
3. **Semantics:** minima are below maxima, exposure and other positive values
   are greater than zero, values are finite, and keyed entries are unique.
4. **Explicit range-edge policy:** a declared stage or focus range must state a
   policy for *both* directions — omitting `z_max` after declaring `z_min` (or
   vice versa) is rejected. Each edge is either a finite numeric bound or an
   explicit reviewed-unbounded value such as
   `{unbounded: true, reason: "no meaningful Z-up hazard"}`. This distinguishes a
   forgotten field from an intentional one-sided constraint without pressuring
   the operator to invent a large number that merely looks like a reviewed
   hardware limit. `check_z` (`safety.py:212`) honors each numeric bound
   independently today; the new schema preserves that behavior while making an
   open edge deliberate and auditable. Because parsing creates this distinction,
   design/32 also owns its retained representation: the validated result carries
   both the existing runtime constraints and the typed edge policies. Design/33
   owns the live containment policy that consumes those retained edges.

The codebase currently uses plain dataclasses plus `yaml` and takes no Pydantic
dependency. Everything this finding needs — finite bounds, `min < max`, rejecting
unknown keys, and requiring complete fields for declared constraints — is
achievable in the existing `from_yaml` path without a new dependency, and that
is the preferred route unless we adopt Pydantic deliberately for other reasons.
The stub below is therefore written in the repo's own dataclass idiom: parsers
first validate shape and primitive types, then construct sections, and a
separate pass enforces declared-field completeness and semantic invariants. It
collects *all* recoverable errors and raises once, so a misconfigured file
reports every problem in a single startup failure.

```python
# microclaw/safety.py (stub; same dataclasses, strict parse + validation)
from dataclasses import dataclass, fields
from math import isfinite
from numbers import Real
from typing import Literal

class SafetyConfigError(Exception):
    """Structural and local semantic problems reported together."""

@dataclass(frozen=True)
class RangeEdge:
    bound: float | None          # finite, or None only when explicitly unbounded
    unbounded_reason: str | None # present iff explicitly unbounded

@dataclass(frozen=True)
class ActuatorId:
    source: Literal["core_xy", "core_focus", "named"]
    device: str | None           # present iff source == "named"
    capability: str              # e.g. "stage-position"
    axis: str | None = None      # x/y/z where the capability has axes

@dataclass(frozen=True)
class RangePolicy:
    minimum: RangeEdge
    maximum: RangeEdge

@dataclass(frozen=True)
class ParsedSafetyConfig:
    constraints: SafetyConstraints              # compiled runtime view
    ranges: dict[ActuatorId, RangePolicy]        # authoritative parsed policies

def _number(raw, label: str, errors: list[str]):
    # bool is a subclass of int, but is never a meaningful microscope limit.
    if isinstance(raw, bool) or not isinstance(raw, Real):
        errors.append(f"{label} must be a number, got {type(raw).__name__}")
        return None
    value = float(raw)
    if not isfinite(value):
        errors.append(f"{label} must be finite, got {raw!r}")
        return None
    return value

def _numeric_section(cls, cfg: dict, name: str, errors: list[str], *, required: bool):
    # Flat scalar sections only (e.g. camera.max_exposure_ms). Range sections
    # (stage/focus) do NOT come through here — their values are edges, not plain
    # numbers, so they need _range_edge below.
    raw = cfg.get(name)
    if raw is None:
        if required:
            errors.append(f"missing required section: {name!r}")
        return cls()
    if not isinstance(raw, dict):
        errors.append(f"{name} must be a mapping, got {type(raw).__name__}")
        return cls()
    unknown = set(raw) - {f.name for f in fields(cls)}
    if unknown:
        errors.append(f"{name}: unknown key(s) {sorted(unknown)}")
    values = {}
    for key, value in raw.items():
        if key not in unknown:
            parsed = _number(value, f"{name}.{key}", errors)
            if parsed is not None:
                values[key] = parsed
    return cls(**values)

def _range_edge(raw, label: str, errors: list[str]) -> RangeEdge | None:
    # A range edge is EITHER a scalar numeric bound OR a reviewed-unbounded
    # object {unbounded: true, reason: "..."}. This is the type-specific parser
    # a flat numeric parser cannot express.
    if isinstance(raw, dict):
        unknown = set(raw) - {"unbounded", "reason"}
        if unknown:
            errors.append(f"{label}: unknown key(s) {sorted(unknown)}")
        if raw.get("unbounded") is not True:
            errors.append(f"{label}: object edge must set 'unbounded: true'")
            return None
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{label}: reviewed-unbounded edge requires a non-empty 'reason'")
            return None
        return RangeEdge(bound=None, unbounded_reason=reason)
    value = _number(raw, label, errors)
    return None if value is None else RangeEdge(bound=value, unbounded_reason=None)

# _AXES lists the axes a range block may declare; _stage_ranges parses the flat
# `stage: {x_min, x_max, ...}` block into per-axis RangePolicy keyed by
# ActuatorId. A focus block, if declared separately, reuses the same helper.
_AXES = ("x", "y", "z")

def _stage_ranges(cfg: dict, errors: list[str]) -> dict[ActuatorId, RangePolicy]:
    raw = cfg.get("stage")
    policies: dict[ActuatorId, RangePolicy] = {}
    if raw is None:
        return policies                       # absence is design/33's reachability call
    if not isinstance(raw, dict):
        errors.append(f"stage must be a mapping, got {type(raw).__name__}")
        return policies
    unknown = set(raw) - {f"{a}_{end}" for a in _AXES for end in ("min", "max")}
    if unknown:
        errors.append(f"stage: unknown key(s) {sorted(unknown)}")
    for axis in _AXES:
        lo_key, hi_key = f"{axis}_min", f"{axis}_max"
        if lo_key not in raw and hi_key not in raw:
            continue                          # axis simply not declared here
        # A declared axis must state BOTH edges explicitly (job 4): a lone
        # x_min with no x_max is a forgotten field, not a one-sided policy.
        edges = {}
        for end, key in (("minimum", lo_key), ("maximum", hi_key)):
            if key not in raw:
                errors.append(f"stage: axis {axis!r} declares one edge but is missing "
                              f"{key!r}; give it a bound or mark it explicitly unbounded")
            else:
                edges[end] = _range_edge(raw[key], f"stage.{key}", errors)
        if set(edges) != {"minimum", "maximum"} or None in edges.values():
            continue
        lo, hi = edges["minimum"], edges["maximum"]
        if lo.bound is not None and hi.bound is not None and lo.bound >= hi.bound:
            errors.append(f"stage.{axis}: min ({lo.bound}) must be below max ({hi.bound})")
            continue
        source = "core_focus" if axis == "z" else "core_xy"
        policies[ActuatorId(source, None, "stage-position", axis)] = RangePolicy(lo, hi)
    return policies

def _stage_constraints(ranges: dict[ActuatorId, RangePolicy]) -> StageConstraints:
    # Derive the runtime view in ONE pass from the authoritative map, never in
    # parallel with it. A reviewed-unbounded edge (bound is None) collapses to
    # the same None the guard already treats as "no bound this direction";
    # design/33 recovers the intent from the retained RangePolicy, not from here.
    def bound(axis, end):
        source = "core_focus" if axis == "z" else "core_xy"
        p = ranges.get(ActuatorId(source, None, "stage-position", axis))
        return None if p is None else (p.minimum if end == "min" else p.maximum).bound
    return StageConstraints(
        x_min=bound("x", "min"), x_max=bound("x", "max"),
        y_min=bound("y", "min"), y_max=bound("y", "max"),
        z_min=bound("z", "min"), z_max=bound("z", "max"),
    )

# from_yaml first verifies that the root is a mapping and rejects unknown
# top-level keys. It then gathers `errors`, parses flat scalar sections with
# _numeric_section (camera etc.) and range sections with _stage_ranges, requires
# complete fields for each declared constraint, requires max_exposure_ms > 0, and
# rejects duplicate (device, property) pairs. It derives the runtime
# StageConstraints via _stage_constraints and assembles ParsedSafetyConfig from
# that one map. Finally: if errors: raise SafetyConfigError("\n".join(errors)).
# load_safety_config returns ParsedSafetyConfig and surfaces failures like the
# existing UnreviewedSafetyConfig exit.
```

`ActuatorId` is the stable identity for a range: it prevents string-key
collisions between core axes, named stages, and future device-specific
capabilities. Core X/Y/Z have no device label in the config today (the `stage`
section is a flat block), so parsing uses tagged identities rather than inventing
reserved device-label strings: X/Y use `source="core_xy"`, Z uses
`source="core_focus"`, and both have `device=None`. Design/33's live cross-check
binds those identities to `core.get_xy_stage_device()` and
`core.get_focus_device()` respectively after connection. Named stages use
`source="named"` with their real device label. Because the source tag, rather
than a naming convention, separates these namespaces, an arbitrary external
Micro-Manager label cannot collide with a core identity. The
parsed `ranges` map is authoritative. `SafetyGuard` still needs only
number-or-`None`, so `_stage_constraints` derives the existing runtime
`SafetyConstraints` from that map in one pass; the parser must never populate the
two representations independently. It retains the complete range policies in
`ParsedSafetyConfig` for audit and the design/33 live check.
`load_safety_config` has one canonical return contract:
`ParsedSafetyConfig`. Entry points retain it through startup, construct
`SafetyGuard` from `parsed.constraints`, and, once design/33 lands, pass the same
`parsed` object to its live check before exposing tools. A directly
constructed `StageConstraints()` or `SafetyConstraints()` remains an unvalidated
test/internal value; its `None` fields must never be described as reviewed or
allowed to masquerade as a loaded safety file.

Only `schema_version` and `reviewed` are universally mandatory top-level fields
in this local parsing phase. Hardware sections are optional structurally because
whether a rig needs `stage`, `camera`, illumination, or another capability is a
live reachability question owned by design/33. Once a section, range, or list
entry is present, however, it must be internally complete under the rules above.
Tests for a "missing section" therefore mean missing universally mandatory
metadata or a missing required member inside a declared object; they must not
arbitrarily require every possible hardware section before a rig is known.

Requiring complete fields for every declared constraint is a behaviour change —
today absent fields default to unconstrained — so it wants a `schema_version`
key and a one-line migration note for configs that predate it, the same courtesy
`reviewed:` got. The explicit-edge policy (job 4) is a second behaviour change
in the same schema bump: existing configs with an intentional one-sided stage
bound will newly fail validation until they mark the opposite edge explicitly
unbounded and give a reason, so the migration note has to call it out. Local file
validation may accept that reviewed-unbounded edge for migration, audit, or an
explicitly degraded mode; design/33's guaranteed mode always rejects an open
edge for a reachable stage or focus axis. Requiring declarations for every
reachable actuator is the separate design/33 behavior change.

Because those pieces have different compatibility and migration scope, this
finding should land as two increments, not one "small self-contained core." The first
increment is **schema-version-free hardening**: reject a non-mapping root
and unknown top-level/section keys, reject booleans/non-numbers/infinities/NaN at
parse time, enforce `min < max` and `max_exposure_ms > 0` on values that are
already present, and add the shared fail-closed finite-number validator at every
`SafetyGuard.check_*` boundary (the runtime NaN hole below). It does not require
the new `schema_version` or range representation, but it is still a parsing
behavior change: files containing typos or previously ignored extension/custom
keys (for example `notes:`) newly fail and may need editing. Inventory shipped
and documented configs before landing it, and report each rejected key with its
file path. The second increment
carries the schema bump and the migration note: the mandatory `schema_version`
key, required-complete-fields for declared constraints, and the job-4 explicit
two-edge policy with its `RangeEdge`/`RangePolicy`/`ParsedSafetyConfig`
representation. Sequencing the harden-only increment first buys the highest-value
safety fix — no silently-ineffective NaN or inverted bound — without waiting on
the versioned migration, and keeps the larger schema work as a deliberate,
separately reviewable step. The stub above shows the end state of both
increments together; it is not a claim that they ship in one commit.

Proving the declared numbers actually *cover the hardware* — not merely that they
are well-formed — is a larger, multi-phase subsystem, and it is specified
separately in `design/33-authorization-map.md`. In brief: completeness is checked
against a **declared** rig profile (load-time validation cannot enumerate a live
core), backed by a live cross-check that fails closed on any reachable, undeclared
actuator before any tool is exposed. That design covers per-actuator-kind
completeness, typed adapters for continuous writes, a channel-plan executor that
closes the preset time-of-check/time-of-use gap, plugin trust modes, and the
property-gate-mode coupling. It builds on the strict file validation above but is
out of scope for this survey's stub.

Tests for the validation covered *here* should exercise missing sections, unknown
keys, booleans used as numbers, inverted/equal ranges, infinities, and NaN both
at configuration load and at every public `SafetyGuard.check_*` boundary. Range
tests should cover a missing counterpart edge, two ordered numeric edges, one
numeric plus one valid reviewed-unbounded edge, malformed/unreasoned unbounded
objects, and two unbounded edges if that actuator type permits them locally. The
live-cross-check tests in design/33 should prove that guaranteed mode rejects
every open edge on a reachable stage or focus axis. The
design/32 parser tests should prove that every reason survives in the returned
`ParsedSafetyConfig` and that directly constructed constraint dataclasses cannot
enter the validated startup path or be reported as reviewed configuration.

Configuration validation alone does not close the runtime `NaN` hole. Add a
shared fail-closed finite-number validator at the start of every public numeric
guard boundary, including `check_xy`, `check_z`, `check_exposure`,
`check_named_stage`, numeric illumination checks, and the acquisition-plan
validator introduced below. It rejects booleans, non-numbers, infinities, and
NaN before comparisons or arithmetic. Hardware values read during a guard (for
example current illumination power or the other XY coordinate) pass through the
same validator; an unreadable or non-finite value is a refusal, not a reason to
skip the numeric policy.

## 2. Safety limits individual actions, not cumulative exposure or acquisition size

**Priority: P0 — sample safety / denial of service**

The exposure guard only checks one frame's exposure (`microclaw/safety.py:223-228`).
`run_timelapse` accepts any frame count and interval and passes them directly to
the acquisition engine (`microclaw/tools.py:755-784`); `run_zstack` similarly
checks only its endpoints and per-frame exposure (`microclaw/tools.py:682-709`).
There is no maximum frame count, plane count, acquisition duration, estimated
illuminated time, output size, or cumulative session dose. One valid tool call
can consequently bleach a sample for hours, fill a disk, or make the cooperative
Stop button ineffective until the blocking acquisition returns. The agent's
50-round cap does not bound work inside a single tool call.

Add a common acquisition-plan object. Materialize/estimate the complete event
plan before hardware motion, enforce configured budgets, and require a separate
human confirmation above a lower warning threshold. Track a conservative
session dose ledger rather than relying only on model instructions.

The confirmation path is not new infrastructure: illumination already routes a
blocking human decision through `confirm_fn(..., kind=...)`
(`microclaw/safety.py:295-316`). Reuse that mechanism with a new `kind` rather
than inventing a parallel one; the bulk of the work is the planner and the
ledger, not the prompt plumbing.

```python
@dataclass(frozen=True)
class AcquisitionPlan:
    frames: int
    exposure_ms_per_frame: float
    estimated_duration_s: float
    estimated_bytes: int

    @property
    def illuminated_ms(self) -> float:
        return self.frames * self.exposure_ms_per_frame

def authorize_acquisition(guard, plan, ledger, confirm):
    guard.check_acquisition(
        frames=plan.frames,
        duration_s=plan.estimated_duration_s,
        bytes_=plan.estimated_bytes,
        illuminated_ms=plan.illuminated_ms,
        session_illuminated_ms=ledger.illuminated_ms,
    )
    if plan.illuminated_ms > guard.confirm_above_illuminated_ms:
        if not confirm(render_plan(plan), kind="acquisition"):
            raise SafetyViolation("acquisition declined")
    return ledger.reserve(plan)  # commit actual usage as frames complete

def run_timelapse(...):
    plan = plan_timelapse(n_frames, interval_s, exposure_ms, camera_shape(ctrl))
    reservation = authorize_acquisition(guard, plan, ctrl.dose_ledger, CONFIRM_FN)
    with reservation:
        return acquire_interruptibly(...)
```

The same planner must cover adaptive acquisitions and MMStudio MDA, not just the
two simple runners. Prefer chunked acquisitions so cancellation can be checked
between bounded batches.

One constraint the chunking design must state explicitly: pyjavaz holds a single
lock across every bridge round trip, so no thread can preempt an in-flight
core/acquisition call — a software kill switch *mid-acquisition* is impossible,
not merely unimplemented (see the pyjavaz-serialization note and design/12).
"Check cancellation between batches" therefore only works if one long
acquisition is decomposed into a *sequence of separate short acquire calls*; the
interruption granularity equals the batch size, and there is real per-batch
setup/teardown overhead. This is a design trade-off (responsiveness vs.
throughput), not a free improvement, and the doc should size the batch
accordingly rather than implying a running acquisition can be interrupted.

### Reconciliation (2026-07-23): lazy event feeding supersedes chunking

Block 4's first implementation attempt stopped on a conflict between "chunked
acquisitions" and the current acquisition APIs. The investigation was right that
**separate bounded `Acquisition` objects are the wrong mechanism** — a
pycro-manager `Acquisition` owns one dataset and one event queue, so splitting a
run into several would fragment the dataset and break multiposition, hook, and
adaptive semantics. That reading of "chunked" is withdrawn.

The paragraph above is still correct that an in-flight bridge call cannot be
preempted. What it got wrong is the remedy. Cancellation granularity is not set
by how many `Acquisition` objects there are; it is set by **how many events have
already been handed to the engine**. `_acquire_with_hooks` accepts either a list
or a factory returning a generator (`microclaw/tools.py:622`), and the two shapes
behave completely differently:

- **Pre-dispatched list** (`run_timelapse`, `run_zstack`, and the multiposition/
  tile runners via `_build_acquisition_events`): the whole event list enters the
  engine queue in microseconds and is then genuinely unstoppable, exactly as
  `microclaw/tools.py:2423` already states.
- **Lazily fed generator** (`_survey_event_stream`, `microclaw/tools.py:2409`):
  the adaptive path pre-dispatches only `survey_events[:1]` and yields each later
  event on demand, polling `acq._acq.is_finished()` every `_CANDIDATE_POLL_S`
  (0.05 s) so an external `abort()` is observed promptly. Its `finally` owns the
  terminator (design/24 Fix 2a).

So the repository already contains a cancellation mechanism with **one-event**
granularity, inside a single `Acquisition` and a single dataset, and it is finer
than any batch size chunking would have produced. The correct Block 4 work is to
generalize that existing feeder to the pre-dispatched paths, not to introduce
batching. There is no per-batch setup/teardown overhead to size, because there
are no batches; the cost is one `is_finished()` bridge round trip per poll, which
design/24 measured at <0.1 ms (~0.1% duty cycle at 0.05 s).

Two limits this does not remove, both of which must be stated rather than
engineered around:

- The frame in flight when cancellation is requested still completes. Granularity
  is one event, never zero.
- **MMStudio MDA is exempt from cancellation.** `run_mda` delegates to one opaque
  `manager.run_acquisition()` call (`microclaw/tools.py:3602`) whose settings
  cannot be faithfully re-expressed as microclaw events. It is nonetheless fully
  *plannable*: `_read_mda_settings` already exposes frames, exposure, slices,
  positions, and time points before the call, so it must be planned, budgeted,
  and confirmed up front and then documented as running to completion.

The stub's `acquire_interruptibly(...)` should therefore be read as "feed events
lazily and stop feeding on cancel", not "call acquire() several times".

### Second reconciliation (2026-07-24): lazy feeding is unaffordable — MEASURED

The reconciliation above is **withdrawn**. It was reasoning, not measurement, and the
M5 rig falsified it. Lazy event feeding costs **3.14× on real hardware**, and the
cancellation it buys has no way to be triggered.

Measured on M5, 200 frames, 5 ms exposure, dark, via
`design/32-block4-bridge-cost-probe.py` — raw pycro-manager with microclaw's
acquisition path entirely out of the picture:

| event source | per-image callback | elapsed | per frame | vs list |
|---|---|---|---|---|
| list | none | 6.922 s | 34.61 ms | 1.00× |
| generator | none | 21.718 s | 108.59 ms | **3.14×** |
| list | `image_saved_fn` | 6.922 s | 34.61 ms | **1.00×** |
| generator | `image_saved_fn` | 21.438 s | 107.19 ms | 3.10× |

Two conclusions, both measured rather than argued:

- **`image_saved_fn` is free.** Identical elapsed to three decimals with the callback
  provably firing 200 times, on M5 and on a demo rig (0.95×). Per-frame budget
  accounting costs nothing and stays.
- **Feeding events from a Python generator instead of a list costs 3.14×**
  (+74 ms/frame). The same probe on a demo camera showed only 1.44× (+3.6 ms/frame),
  so the cost is hardware-dependent — a demo config cannot detect it.

Three earlier hypotheses were eliminated by measurement first, and each is recorded so
nobody re-proposes them:

- *Not the pixel transfer.* Moving accounting off `image_process_fn` (which shipped
  every frame over ZMQ) changed the microclaw-level number by 0.05 %: 31.953 s → 31.968 s.
- *Not the gating.* Raising the look-ahead from 2 to 10⁹, so the feeder never blocks
  once, gave 31.891 s against 31.969 s. The engine pulls one event at a time from
  Python no matter how far ahead the feeder is willing to run, so **no look-ahead depth
  fixes this.**
- *Not the `is_finished()` poll.* 5.12 ms mean, 3.16 s total — 9.9 % of a 22.5 s gap.

**The landed architecture.** Acquisitions whose events are known up front —
`run_timelapse`, `run_zstack`, and the multiposition/tile runners — pass the **list**
to `acquire()`, exactly as before Block 4. Budgets, the session dose ledger, the
confirmation gate, and per-frame accounting via `image_saved_fn` all remain, at 1.00×.
The adaptive paths (`_survey_event_stream`) already fed generators before Block 4 and
are unchanged; they pay this cost today and always did, which is inherent to events
that do not exist until a hook produces them.

**Cancellation is deferred, not solved.** It is not merely expensive but currently
unreachable: nothing in the package calls `Acquisition.abort()`, and `POST /api/stop`
stops the agent turn at round and tool boundaries, not inside a running tool. Paying
3.14× on every acquisition for a capability with no trigger is not a trade-off, it is a
loss. Mid-acquisition cancellation needs its own block, which must deliver the abort
trigger and the interruption mechanism together, and must budget for the fact that
one long acquisition cannot be interrupted at all without changing how events reach the
engine.

*Incidental observation, not a claim:* the probe's list baseline (34.61 ms/frame,
`show_display=False`) is faster than the same acquisition through
`_acquire_with_hooks` (47 ms/frame, `show_display=True`). If ~12 ms/frame of display
overhead is real it is worth its own measurement, but nothing here establishes it.

### Landed: Block 4 (merged `e8e7afb`, rig-gate PASS 2026-07-26)

Implementation split across `microclaw/acquisition.py` (planner + ledger) and
`microclaw/tools.py` (routing). Final shape, superseding the stubs above:

- **`AcquisitionPlan(frames, exposure_ms_per_frame, estimated_duration_s,
  estimated_bytes)`** with an `illuminated_ms` property; `plan_events()` builds it from
  the event list and live camera geometry. `AcquisitionLedger`/`Reservation` hold a
  per-session dose ledger: `reserve()` runs `guard.check_acquisition(...)` under a lock,
  reserves conservatively, and `commit_frame()` books actual usage as frames complete;
  `close()` rolls back the unused reservation. `commit_frame` returns a bool (never
  raises onto an engine thread) and flags `has_overrun`.
- **Config:** a **required** nine-field `acquisition:` section — hard `max_frames`,
  `max_duration_s`, `max_bytes`, `max_illuminated_ms`, `max_session_illuminated_ms`, and
  lower `confirm_above_*` thresholds for frames/duration/bytes/illuminated. Required (not
  required-when-present): optional would let a reviewed config fail open, reintroducing
  Finding 1's thesis. Migrated into the example, fixture, and README.
- **Confirmation** reuses `CONFIRM_FN(..., kind="acquisition")`; a decline rolls back and
  raises an *attributable* message ("declined at confirmation"), distinct from a limit
  refusal (rig G3 found the bare message unattributable).
- **Routing:** all of `run_timelapse`, `run_zstack`, `run_multiposition_acquisition`,
  `run_tile_acquisition`, `run_multiposition_with_autofocus`, `run_adaptive_survey`, and
  `run_mda` plan-before-motion and reject unplannable/unbounded work. MDA plans from
  `_read_mda_settings` (slices/channels read via bridge `size()`/`get(i)`; `exposure()`
  is a method, `useChannel` a field — measured, rig G5) and credits the ledger after its
  opaque `run_acquisition()`.
- **Per-frame accounting rides `image_saved_fn`** (measured free, 1.00×), *not*
  `image_process_fn` (which shipped every frame over ZMQ). List-known acquisitions pass
  the list to `acquire()`.

**Measured rig findings (replace the estimates above):**

- **Throughput:** list dispatch is 1.00× vs. `main` (7.015 s vs. 7.000 s @5 ms; 5.484 vs.
  5.500 @50 ms). `image_saved_fn` accounting adds nothing (identical elapsed, callback
  firing per frame).
- **Duration overhead (G6):** a 9-position dark grid (50 µm, 100 ms) ran ~657 ms/frame
  slower than the exposure-only estimate — **actual ≈ 7.6× estimate**, per-position and
  exposure-independent (stage move + settle + per-`Acquisition` setup + dataset write).
  So `max_duration_s` bounds a known-low estimate, **not** wall-clock time; frames,
  bytes, and illuminated time are exact. A per-frame overhead term would be rig-specific
  and is deferred.

**Open items carried forward:** mid-acquisition cancellation (own block; no trigger
exists); `run_mda` is authz-excluded on M5 so its end-to-end run is unobserved there
(pinned at unit level); the ledger is per-session, not durable. Block 5 (design/33
Phase 3) consumes this planner/ledger; per-frame exposure alone must never count as
complete acquisition authorization.

Block 5 has now landed (merge `3438b90`; rig-gate PASS 2026-07-27) and discharged
that requirement by deleting the legacy per-frame-exposure acquisition claim and
adding the separate dose-policy/tool completeness branch; design/33's
[Phase 3 landed note](33-authorization-map.md#phase-3-landed-2026-07-27) owns those
authorization semantics. M5 also validated the design/32 plumbing claim:
`LEDGER.bytes == PLAN.estimated_bytes` exactly at 21 233 664 = 2 × 2304 × 2304 × 2,
versus 1 048 576 = 2 × 512 × 512 × 2 on the demo core from the same probe and
arguments, confirming that the planner reads live camera geometry. Frames and
illuminated time were exact; duration remains the known-low estimate G6 measured
at ≈7.6×. The [gate record](33-block5-rig-gate-prompts.md) contains the evidence
and its scope limits.

## 3. Remote mode authorizes by network position, not by identity

*(This heading was missing; the finding below ran on from §2 with only its
priority line to separate them. Restored 2026-07-27 with the Block 6 landed
note. Every `webserve.py` line anchor in the problem statement refers to the
pre-Block-6 file.)*

**Priority: P1 — security / authorization; release-blocking for remote mode**

The server intentionally requires `--allow-remote` before a non-loopback bind,
but after that opt-in any reachable client can call `/api/prompt`, `/api/stop`,
and `/api/confirm`. The middleware checks a supplied `Origin` only
(`microclaw/webserve.py:247-259`); non-browser clients can omit or forge it.
The prompt endpoint itself has no authentication check
(`microclaw/webserve.py:275-283`). Disabling browser key/model editing remotely
does not protect microscope control. On a shared or accidentally routed lab
network, discovering the port is enough to operate the instrument or approve a
pending illumination action.

The residual exposure is narrower than a default unauthenticated public
endpoint, which is why this is P1 rather than P0: the bind is loopback-only
unless the operator passes `--allow-remote` (`webserve.py:550`, `569`), and the
desktop shortcut never passes it (`shortcut.py:20`). It is nevertheless a hard
release blocker for describing remote mode as supported. The realistic victim
is a lab that deliberately opts in and assumes its network is private. That
assumption is exactly what authentication should not require.

Remote mode should require authentication independent of `Origin`: generate a
high-entropy bearer/session secret, compare it in constant time, protect every
`/api/*` route (including confirmation), and refuse cleartext non-loopback use
unless it sits behind an explicitly trusted TLS proxy. Origin validation remains
useful CSRF defense but is not identity.

```python
def build_app(session, *, remote: bool, api_token: str | None):
    if remote and not api_token:
        raise RuntimeError("remote mode requires an API token")

    @app.middleware("http")
    async def authenticate(request, call_next):
        protected = remote and request.url.path.startswith("/api/")
        pairing = request.url.path == "/api/pair"
        if protected and not pairing:
            bearer = valid_bearer(request, api_token)
            session_cookie = valid_session_cookie(request)
            if not (bearer or session_cookie):
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

```

The bearer header above is the non-browser API contract. The bundled browser
must not receive or store that long-lived token in JavaScript: it uses a
separate one-time pairing endpoint that exchanges a short-lived pairing code
for an HttpOnly, Secure, SameSite=Strict session cookie. The pairing endpoint is
the only remote `/api/*` exception to the normal bearer/session middleware; it
is rate-limited, expires codes promptly, invalidates each code after one use,
and is unavailable over untrusted cleartext transport. Subsequent browser API
requests authenticate with the cookie. Local loopback mode preserves today's
unauthenticated behavior; in particular, it must not accidentally compare a
request against the string `Bearer None`.

This auth gate is additional to, not a replacement for, the existing loopback
gates on credential editing. The server already returns 403 when key/model
editing is attempted on a non-loopback bind (`webserve.py:414, 443, 481`). Those
protect *credential* mutation and stay as-is; the new bearer/session middleware
protects *hardware control* (`/api/prompt`, `/api/stop`, `/api/confirm`). An
implementer must keep both — the auth check does not subsume the loopback gate,
and the loopback gate never protected the control endpoints — so the two run as
distinct, complementary checks rather than one being folded into the other.

Also add request-body limits, confirmation audit records (identity, time,
decision), rate limiting, and tests using requests with no `Origin` header.

### Landed: Block 6 (merged `056a4ef`, 2026-07-27)

Implementation `eb9ab16` + review fixes `c81aa16`, coordinator `f68af87`;
937 passed / 98 skipped / 3 warnings. No rig action: the acceptance gate for this
block is its network/security tests, and none of it touches hardware paths.

**The shipped contract**

- **Two credentials, one middleware.** `authenticate_remote` protects every
  `/api/*` route in remote mode — including `GET /api/history`, `/api/confirm`,
  `/api/model`, `/api/key`, and `/api/artifact`, not only the three control
  endpoints the finding named. `POST /api/pair` is the sole exception. `GET /`
  and `/favicon.ico` stay public so the page can load in order to pair.
  Unauthenticated is a bare 401 that does not distinguish "wrong token" from
  "no token". All three secrets compare through `hmac.compare_digest`.
- **Bearer = non-browser clients.** `MICROCLAW_REMOTE_TOKEN` if set, else
  `secrets.token_urlsafe(32)` per process; printed once at startup, never
  persisted, never returned by any endpoint.
- **Pairing = browsers.** The startup code is delivered in a URL *fragment*
  (`/#pair=CODE`), chosen so the code reaches neither the server's request line
  nor the proxy's access log; the page clears it from the address bar before
  exchanging it. Codes are single-use, 15-minute TTL, at most five outstanding,
  rate-limited. A valid code returns an opaque server-side session value as an
  HttpOnly, Secure, SameSite=Strict, `Path=/` cookie with a 12-hour life. The
  browser never receives the bearer token. `POST /api/pair/code` mints a
  replacement code and is **bearer-only by contract** — a paired cookie cannot
  mint pairing codes.
- **Transport.** A non-loopback bind now requires `--allow-remote` *and* the new
  `--behind-tls-proxy`; cleartext remote is refused at startup. In proxy mode
  every request must carry `X-Forwarded-Proto: https`, and the CSRF middleware's
  expected origin becomes `https://` + the `Host` header (verified against a
  non-default public port, 8443).
- **Both gate families kept.** Origin/CSRF still runs, independently and
  outermost, and is not identity. The `session.editable` loopback gates on
  `/api/key`, `/api/model`, and `/api/artifact` still protect *credential and
  config mutation*; the new middleware protects *hardware control*. Neither
  subsumes the other, and tests assert each independently.
- **Limits and audit.** 256 KiB on `/api/prompt`, 64 KiB on every other JSON
  body, 413 past that; 10 auth failures and 10 pairing attempts per 60 s per
  direct socket address, over bounded LRU maps (256 clients, 1024 sessions);
  confirmation decisions recorded with identity, UTC timestamp, confirmation id,
  kind, and decision, to stdout and an in-memory list.
- **Loopback is untouched.** No token, no cookie, no pairing endpoints (404),
  no new refusals, and the auto-open browser behaviour is unchanged. Pinned by
  test.

**Known limitations, stated rather than fixed**

- **The proxy assertion is unverifiable.** Any client that reaches the cleartext
  bind port directly can send `X-Forwarded-Proto: https` itself. This is not
  privilege escalation — control still requires the bearer or a paired cookie —
  but the transport guarantee rests on the operator making that port reachable
  only from the proxy. README and `--behind-tls-proxy --help` now say so.
- **`MICROCLAW_REMOTE_TOKEN` is length-checked, not entropy-checked.** The
  minimum is 32 *characters*; `"t" * 32` passes. Length is the only property a
  server can cheaply verify, so the operator owns token quality when they supply
  one. The generated default is 32 bytes from `secrets`.
- **No revocation.** A leaked bearer or cookie is valid until process restart or
  the 12-hour cookie expiry. There is no revocation endpoint and no role
  separation; every authenticated identity has the same authority.
- **Shared-address rate limiting.** Behind a proxy all clients present one
  socket address, so a burst of failed pairing attempts can deny pairing to a
  legitimate operator for the rest of the window. Authenticated requests never
  consult the limiter, so an already-paired session is unaffected.
  `X-Forwarded-For` is deliberately not trusted for this.
- **The audit is volatile by design.** It dies with the process. Durable,
  append-only, redacted audit remains Finding 5 / Block 15 and this block must
  not be read as delivering it.

**Behaviour change for existing operators:** `--allow-remote` alone no longer
starts. An operator running remote mode on a trusted LAN today gets a startup
refusal directing them to a TLS proxy. This was a deliberate coordinator call —
a cleartext escape hatch would reopen exactly the hole this block closes.

## 4. Confirmed hooks execute arbitrary Python inside the hardware-control process

**Priority: P1 — security / reliability / fault isolation**

The repository accurately labels hook linting as advisory and trivially
bypassable (`microclaw/hook_manager.py:21-28`). After confirmation, loading a
hook runs its module top-level with `exec_module` in the main process
(`microclaw/hook_manager.py:128-160`). The hash prevents post-approval changes,
but it does not constrain the approved code. A generated or user-supplied hook
can read credentials, use the network, modify files, hang, crash the process,
exhaust memory, or access controller objects supplied to some hooks — and it
does all of this *inside* the single process that also drives the microscope.

The threat is not primarily a malicious operator. This is a single-operator
local tool, and confirmation remains a meaningful gate. The untrusted component
is the implementation: generated code can contain an unsafe mistake or be
influenced by prompt-injected external material even when the operator's request
is benign. Because the API key and live controller inhabit the same process,
isolation has real security value as well as the stronger day-to-day reliability
value: a hook that hangs or segfaults should not take down microscope control.

Smart microscopy still requires analysis to influence acquisition. Therefore
the boundary must not be a "data-only" contract that forbids hardware decisions.
Use a **capability-limited decision contract** instead: a generated analysis hook
may return measurements and propose typed motion/acquisition events, but it may
not execute them directly. The trusted parent validates each proposal through
`SafetyGuard`, acquisition/dose budgets, confirmation policy, cancellation, and
the audit log before placing it on the hardware event queue. This is conceptually
close to the repository's existing candidate-event pattern.

These are two separate pieces of work at two different sizes, and the doc should
not imply they land together. **Phase 1 is small and high-value:** stop handing
generated analysis hooks a live controller and accept only typed decisions from
them (the discriminated action union below). It removes the direct
hook-to-hardware path — the part that carries the security value — using code
that already resembles the candidate-event pattern, and it needs no new process.
**Phase 2 is a much larger reliability build:** the least-privilege worker
process that adds hard deadlines, memory caps, and native-crash isolation. Phase
2 is worth doing, but it is a later, independently scheduled item; treating the
~130 lines below as one change overstates the immediate cost of getting the
security win.

Phase 1 does not apply to every hook: trusted built-in control hooks such as
autofocus may legitimately need synchronous controller access and should remain a
separate, explicitly trusted category.

Phase 2, the worker process. Hard execution deadlines, per-hook memory limits,
recovery from native crashes, and reliable termination of infinite loops require
a worker-process boundary; they cannot be promised by an in-process thread. Run
generated analysis hooks in a separate, least-privilege worker with a narrow
serialized protocol. The worker
receives image bytes plus immutable metadata and returns measurements and
proposed events; only the trusted parent authorizes and executes them. Apply
filesystem/network/process limits where the platform supports them.

```python
# Generated-hook worker: one long-lived, stateful instance per acquisition, but
# no controller, guard, credentials, or hardware event queue.
with hook_worker.session(
    hook_sha256=entry["sha256"],
    initial_state=bounded_initial_state,
) as hook_id:
    for image, metadata in acquisition_frames:
        result = hook_worker.process(
            hook_id=hook_id,
            image=share_image(image),
            metadata=json_safe(metadata),
            timeout_s=constraints.hooks.max_runtime_s,
        )
        validated = HookResult.model_validate(result)

        # Trusted parent: proposed actions are capabilities, not direct access.
        # ProposedAction is a discriminated union of MoveStage, AcquireAt,
        # SetExposure, ContinueSurvey, StopSurvey, and RequestAutofocus. Unknown
        # kinds fail schema validation and therefore never reach this loop.
        for action in validated.actions:
            event_or_command = authorize_and_convert(
                action,
                guard=guard,
                acquisition_budget=acquisition_budget,
                confirmation_policy=confirmation_policy,
            )
            audit.record_hook_decision(validated.measurements, action)
            trusted_dispatcher.submit(event_or_command)

# Worker launch sketch: a dedicated venv/container, no API key or controller,
# read-only hook/package mount, private temp directory, network disabled, and
# an authenticated length-prefixed JSON/msgpack protocol over a local pipe.
```

One non-obvious consequence of removing the live controller is that some
adaptive hooks are **stateful across frames**, not stateless per image. The
current runner preserves one hook instance throughout an acquisition and
attaches shared survey state — the planned events, candidate queue, and progress
counter — to that instance. A worker protocol modeled as independent
`(image, metadata) → actions` calls would lose visited tiles, running metrics,
and stop/jump decisions.

Keep one worker-side hook instance alive for the acquisition, keyed by an opaque
`hook_id`, rather than serializing its complete private analytical state on every
frame. This supports large models, trackers, masks, and accumulated measurements
without repeatedly copying them across the process boundary. The parent must
bound worker lifetime, memory, and IPC message size, but need not understand the
semantics of private analytical state. Optional, size-limited serialized
checkpoints can support auditing or crash recovery; they are not the primary
per-frame state mechanism and may never contain controller handles or other
capabilities.

Be precise about which recovery the worker boundary provides, because
worker-held state and the "recovery from native crashes" justification above
address different failures. Isolation guarantees that a hook hang or segfault
does not take down the parent. The parent remains available to reject further
hook actions, initiate the acquisition's existing abort/cleanup path, and record
the failure, although already queued hardware events and bridge serialization
may delay the stop. It does **not** guarantee that opaque analytical state —
running metrics, tracker internals, loaded model state, or other worker-private
data — survives the crashed worker.

The parent can durably retain authoritative acquisition progress such as
accepted actions, visited positions, and emitted measurements without
checkpointing the worker's complete state. Reconstructing that progress is not
the same as resuming the analysis exactly where it stopped: exact analytical
resumption additionally requires sufficiently frequent worker checkpoints.
The optional checkpoints above are therefore coarse audit/partial-recovery aids,
not an exact-resume guarantee. The boundary buys process isolation and a
surviving hardware-control path; adaptive-state durability is a separate design
choice.

The action vocabulary must cover the adaptive behavior that already exists, not
only XY motion and acquisition. At minimum it needs typed variants for
`MoveStage`, `AcquireAt`, `SetExposure`, `ContinueSurvey`, `StopSurvey`, and
`RequestAutofocus`. The union is closed and discriminated: an unknown action
type, invalid payload, or action unsupported by the current runner fails closed
before dispatch. Every accepted action is converted by trusted code into a
guarded operation or acquisition event.

Hardware-motion plugins and trusted built-in control hooks are different
boundaries and should remain behind explicit gates. Generated Python analysis
hooks should never receive a live controller as a shortcut around parent-side
authorization, but they remain fully capable of adaptive, analysis-driven
microscopy through proposed events.

### Landed: Block 7 (merged `2b8d752`, demo gate D1–D5 + M5 R1 PASS 2026-07-28)

Phase 1 shipped. The final categories and schemas, which differ from the sketch
above in ways worth pinning:

**Trust is decided by provenance, not by the manifest's `source` field.** Trusted =
classes in `PRECODED_HOOK_REGISTRY`, shipped in-repo and reviewed at release.
Untrusted = anything `hook_manager.load_hook_class` returns, whether it is labelled
`claude_generated` or `user_provided`. `_resolve_hook` branches on that and nothing
else.

**Contract.** Untrusted hooks implement `analyze_frame(self, image, metadata) ->
HookResult | None`. `HookResult` is a frozen dataclass carrying JSON-safe
`measurements`, an `actions` sequence, and optional `analyzer`,
`analyzer_version`, `parameters`, `artifact_sha256`, and `status`. `status`
defaults to `"unverified"` and may only be `"unverified"` or `"provisional"` —
untrusted code cannot self-assert `"observed"`. Measurements are written by the
parent through `microclaw.hooks.analysis_observation_record`, the same
`microclaw.analysis-observation/v1` envelope `HookBase.log_analysis` uses; design/26
Block 10's "shared observation writer" therefore already exists.

**Action union**, closed and discriminated, exactly as specified: `MoveStage`,
`AcquireAt`, `SetExposure`, `ContinueSurvey`, `StopSurvey`, `RequestAutofocus`.
Both dataclass instances and plain dicts with a `kind` are accepted. Under
`run_adaptive_survey` the parent supports `ContinueSurvey`, `StopSurvey`, and
`AcquireAt` naming a planned position by index or unique label; the other three
parse, validate, and are refused as unsupported-by-this-runner with an attributable
reason, so the union stays closed and complete rather than silently narrow.

**What untrusted hooks no longer receive:** `ctrl`, `guard`, credentials,
`candidates`, `progress`, `survey_events`, `log_path`, and the pycro-manager event
queue (replaced by a stub that raises on any access). `hook_params` cannot smuggle
any of them back. The runner keeps the tile cursor parent-side, so a hook advances
the survey by asking, not by holding the queue.

**Two limitations, stated rather than engineered around.**

1. *No interactive confirmation from an acquisition callback thread.* pyjavaz
   serializes every bridge call behind one lock, so a mid-acquisition prompt is a
   deadlock hazard. A proposal outside the committed Block 4 reservation is refused
   and audited, never escalated. This is why Block 7b's illumination work must
   pre-authorize an envelope at plan time: a per-frame UV feedback loop cannot be
   confirmed frame by frame.
2. *A saved hook may no longer keep its own log.* The parent owns the audit record
   so hook source cannot forge or omit it — and a hook that wrote its own log would
   otherwise run, take every exposure, and record nothing. Such a hook is refused at
   resolve time, before any hardware moves, with a message naming the migration.
   **This breaks every hook written to the old rule that generated hooks inherit
   `HookBase`.** All four hooks on M5 are affected.

**What Phase 1 removed and did not replace**, tracked as checklist Block 7b and
explicitly *not* restored by Phase 2: illumination control (UV activation is a live
requirement), live artifact emission, and frame discard — `analyze_frame` cannot
return `None` to drop a field, because the adapter always returns
`(image, metadata)`.

**Containment is unchanged.** Saved source still executes in the hardware-control
process. Source review and hash pinning remain the gate until Phase 2. Nothing in
Block 7 provides a deadline, memory cap, network isolation, or native-crash
recovery, and the shipped docstrings say so.

**The gate found four defects, three of them invisible off-rig.** A saved hook that
kept its own log ran and recorded nothing (caught by a hardware-only test on the
demo core); `tools_schema.py` still described the pre-Block-7 adaptive contract to
the agent; absent and ambiguous `AcquireAt` labels shared one refusal reason; and
`rank_hook_log` reported a correct top-k save as a coordinate mismatch because it
required the saved position and the ranked record to carry identical axes, while
Run A legitimately adds a focus Z. Procedure and evidence:
`design/32-block7-gate-prompts.md`.

## 5. Conversation history grows without a context or storage policy

**Priority: P1 — functional reliability / privacy / cost**

Every turn appends user, assistant, tool, and image-result blocks to one list
(`microclaw/agent.py:304-395`), and every model round sends that history again
(`microclaw/agent.py:280-286`). The only bound is tool rounds within the current
turn. Long sessions will eventually exceed the model context window, become
increasingly slow and expensive, and repeatedly transmit old image payloads and
tool output. The web history endpoint serializes the entire list on every fetch
(`microclaw/webserve.py:271-273`), compounding browser memory and latency. There
is no redaction/retention policy for saved transcripts either.

Separate the append-only audit log from the bounded model context. Keep recent
turns verbatim, replace older turns with a structured checkpoint, retain
artifact hashes/paths and safety-relevant facts without image bytes, and page
the browser history. Compaction must never invent hardware state: stale state is
provenance only and must be re-read before action.

```python
@dataclass
class ConversationStore:
    audit: AuditLog                 # durable, append-only, redacted
    context_budget_tokens: int

    def model_messages(self) -> list[dict]:
        recent = self.audit.recent_complete_turns()
        kept = take_newest_within_budget(recent, self.context_budget_tokens)
        checkpoint = build_structured_checkpoint(
            self.audit.before(kept),
            include=("artifact_refs", "user_decisions", "completed_actions"),
            exclude=("image_base64", "credentials", "current_hardware_state"),
        )
        return [checkpoint, *kept] if checkpoint else kept

@app.get("/api/history")
async def get_history(cursor: str | None = None, limit: int = 100):
    return session.store.audit.page(cursor=cursor, limit=min(limit, 500))
```

Add token estimation before each API call, atomic transcript writes, explicit
retention controls, and tests that compact histories only at complete Anthropic
message boundaries (never between `tool_use` and `tool_result`).

Compaction also interacts with prompt caching, and the design should account for
it rather than fight it. Each round sends the history behind a cache breakpoint
(`_with_cache_breakpoint`, `TOOLS_CACHED` at `agent.py:284-285`); rewriting older
turns into a checkpoint invalidates every cached token from the rewrite point
onward, so a naive per-turn compaction would repay in cache misses much of what
it saves in context. Compact in infrequent, larger batches and keep the rewritten
checkpoint as a stable prefix, so the breakpoint moves rarely and the common case
still hits warm cache.

## Recommended order

Implement the schema-version-free hardening increment of Finding 1 first
(unknown-key rejection,
the NaN fix at every guard boundary, finite bounds, `min < max`, and
`max_exposure_ms > 0` on values already present), because it is the highest-value
safety fix, avoids the versioned schema migration (while still potentially
requiring edits to files with previously ignored keys), and all other hardware guards depend on
trustworthy configuration. Land the schema-bump increment (mandatory
`schema_version`, required-complete-fields, and the job-4 explicit two-edge
policy with `ParsedSafetyConfig`) as the deliberate next step, since it changes
behaviour and needs the migration note. Next land the core of the
rig authorization map — profile schema, property allowlist requirement, and live
cross-check — tracked in `design/33-authorization-map.md`. Add acquisition
budgets and interruptible batching next, then extend the authorization map with
the resulting acquisition/dose policies. The typed actuator registry,
channel-plan executor, and setup assistant are later design/33 phases.
Authentication should be required before remote mode is
described as usable. **Landed 2026-07-27 (Block 6, merge `056a4ef`):** the token
gate shipped, so the interim path this paragraph offered — leaving
`--allow-remote` unauthenticated behind a startup warning — no longer exists and
must not be reintroduced. Remote mode is authenticated or it does not
start. Define the generated-hook decision schema and withhold the
live controller from that hook category next; this preserves smart microscopy
while making the trusted authorization boundary explicit. A worker process is
then required to enforce hard deadlines and memory caps—the document should not
claim those guarantees before it exists. Context/audit separation is independent
and can proceed in parallel, but should land before long-running sessions become
a supported use case.

### Landed: increment 1a (schema-version-free hardening)

Increment 1a merged 2026-07-22 (impl `967ab5d`, merge `56ed945`) in the
existing dataclass `from_yaml` path — no `schema_version`, `RangeEdge`,
`ActuatorId`, `ParsedSafetyConfig`, completeness enforcement, or Pydantic
dependency (those remain 1b). It rejects non-mapping roots and unknown top-level
and section keys with file-anchored, aggregated `SafetyConfigError`s; rejects
booleans/non-numbers/NaN/infinity in configured numeric fields; enforces
`min < max` and `max_exposure_ms > 0`; and routes every public numeric guard
(`check_xy`, `check_z`, `check_exposure`, `check_device_property`,
`check_illumination`, `check_named_stage`) plus their hardware-read companions
through one shared `_finite_number` validator.

Two facts to carry forward. **Migration impact (measured):** every key in the
shipped example and the test fixture was already recognized, so strict rejection
breaks neither file; the migration surface is field configs with typo'd sections
or stray keys only. **Behavior change beyond the NaN fix:** a *non-numeric* value
written to a recognized motion/exposure/XY device property — and a non-numeric
current-power hardware read during the illumination step ratchet — now fail
closed (raise) rather than falling through denylist-only / defaulting to `0.0`.
This is stricter than the "NaN fix at every guard boundary" phrasing above and
is intentional; 1b should preserve it.

### Landed: increment 1b (versioned strict safety schema)

Increment 1b merged 2026-07-22 (impl `d5aa5c4` + coordinator review-fix
`12b0677`, merge `b8092c4`); rig-verified to start clean with the reviewed rig
config. It adds mandatory `schema_version: 1` with a migration note, the typed
`RangeEdge`/`ActuatorId`/`RangePolicy`/`ParsedSafetyConfig` model, explicit
two-edge policy for every declared stage/named-stage range, required-complete
fields, retained reviewed-unbounded reasons, and duplicate `(device, property)`
rejection across `forbidden_properties`, `allowed_properties`,
`illumination.shutters`, and `illumination.power_properties`. `load_safety_config`
now returns `ParsedSafetyConfig`; both entry points (`__main__.run_session`,
`webserve.Session`) retain it and build `SafetyGuard(parsed.constraints)`.

Divergences from the stub above, for the record:

- `SafetyConfigError` subclasses `ValueError` (not bare `Exception`), carried
  from 1a so parse-time numeric validation can be caught and aggregated.
- `from_yaml` is a classmethod of `ParsedSafetyConfig`, not `SafetyConstraints`.
- `_stage_constraints` derives the runtime `StageConstraints` **and** the
  `named_stages` list in one pass over the single authoritative `ranges` map;
  named-stage policies live in `ranges` keyed by
  `ActuatorId("named", device, "stage-position")` (`axis=None`). This matches
  design/33's live cross-check assumptions exactly, so **design/33 needs no
  change**. A declared named stage with no bounds now fails validation
  (complete-fields).
- **Required-when-present policy (judgment call, endorsed):** when a section is
  present, `camera.max_exposure_ms` and `channels.allowed` are required, but
  `analysis.min_snr` is **not** — it is documented optional ("omit to retain the
  uncalibrated fallback"), and requiring it broke the shipped example. A
  regression test now loads the packaged example through `importlib` so strict
  validation can never silently break startup again. If a future rig legitimately
  wants `camera:`/`channels:` present without their key, revisit these two.
