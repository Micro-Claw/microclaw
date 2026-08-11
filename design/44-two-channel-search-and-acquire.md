# Search in one channel, acquire in another

## Problem

`run_adaptive_survey` gives every event one `channel` and one `exposure_ms`
(`microclaw/tools.py:5280-5288`); `_build_acquisition_events` turns that channel
into the hard-coded Micro-Manager `"Channel"` axis
(`microclaw/tools.py:2301-2317`). That axis is unavailable on rigs whose channel
source is not a config group: the acquisition guard explicitly directs those
rigs to `set_channel` and a channel-less acquisition instead
(`microclaw/tools.py:1911-1933`). M5 is such a rig; the executor's fixed config
group is `"Channel"`, while its measured allowed groups were only `""` and
`"System"` (`microclaw/authorization.py:157-163`).

Tile choice is not missing. `AcquireAt` already resolves an index or unique
position label in the planned event list, checks XY/Z, queues the event, and
charges the adaptive cap (`microclaw/hook_decisions.py:634-668`). What is missing
is a way for that selected event to use a second phase's channel, exposure, and
shape.

## Decision

Add one optional `acquire_on_hit` argument to `run_adaptive_survey`; do not add a
tool or a new hook action. When absent, `AcquireAt` keeps its current immediate
revisit semantics. When present, `AcquireAt` validates the same planned target
but records it once in a parent-owned hit set; after the search stream closes,
the parent switches channel once and runs one multiposition timelapse or Z-stack
over the hits.

In this mode both channels are phase settings, not event axes: the parent first
applies the survey's existing `protocol_params.channel`, builds channel-less 561
events, and later applies `acquire_on_hit.channel` before building channel-less
488 events. This is the necessary behavior change for M5; leaving the search
channel on its current event-axis path would still refuse before the first frame
(`microclaw/tools.py:1911-1933`).

```python
acquire_on_hit={
    "channel": "488",
    "protocol": "timelapse",       # or "zstack"
    "protocol_params": {"n_frames": 150, "interval_s": 0,
                        "exposure_ms": 20, "laser_slot": 3},
    "max_hits": 9,
}
```

This folds into the existing survey and acquisition shapes. `run_timelapse` and
`run_zstack` already take hooks and their acquisition settings
(`microclaw/tools.py:2424-2441`, `microclaw/tools.py:2572-2589`), while
`run_multiposition_acquisition` already represents one protocol over many
positions (`microclaw/tools.py:4037-4087`). The implementation should extract or
reuse their internal event-building path, not call decorated tools from a tool.

## Alternatives, ranked

1. **Deferred `AcquireAt`, then a parent-side second pass (chosen).** It reaches
   both config-group rigs and M5 because the phase boundary uses the existing
   `set_channel` route: authorization-map rigs call `execute_channel_plan`, and
   map-less rigs call `set_config` (`microclaw/tools.py:1975-1992`). It has one
   explicit worst-case hit bound, one switch per phase, and one argument on an
   existing tool. The parent-owned hit set also avoids making the hook log a
   control channel.
2. **One seed plan containing both 561 and 488 events.** This costs no new
   dispatch, but loses on rig reach: acquisition events express channels only as
   the `"Channel"` axis (`microclaw/tools.py:2301-2317`). It also makes the seed
   plan scale as search events plus `max_hits * acquire_frames`, while most
   acquire events are never submitted, and mixes two exposures into accounting
   whose `AcquisitionPlan` has one `exposure_ms_per_frame`
   (`microclaw/acquisition.py:12-21`). Reject it.
3. **An immediate typed channel-switch action inside the survey loop.** The
   parent could authorize and dispatch it, as it does other typed actions, but
   alternating per hit multiplies channel-plan execution, enable confirmations,
   and emitter state. It also leaves the generator responsible for a long burst
   before it can resume search. A single phase boundary supplies the needed
   parent authority with less runner and guard surface. Reject it.
4. **Agent-orchestrated `survey -> read_hook_log -> set_channel -> multiposition`.**
   The pieces exist, and `validate_positions` can guard coordinates without
   moving hardware (`microclaw/tools.py:5806-5838`). It is useful as a manual
   fallback, but an exported session would contain the hits from this run—the
   trace—not the rule that finds hits on the next sample. Reject it as the shipped
   capability.

## Dose and illumination contract

Before the first search exposure, reserve two independently calculated plans:
the complete search plan, including authorized autofocus dose, and a worst-case
acquire plan of `max_hits * frames_per_hit` at the acquire exposure. The ledger
checks and reserves illuminated milliseconds before dispatch
(`microclaw/acquisition.py:24-46`), and commits only returned frames
(`microclaw/acquisition.py:49-84`). Do not flatten the phases into one average
exposure.

The adaptive cap and completion total remain different quantities. Today the
survey total starts at the actual event count while its cap includes planned
refocus re-exposures (`microclaw/tools.py:5141-5170`); the second phase follows
the same rule: `max_hits` sizes the reservation/cap, while the actual deduplicated
hit count sizes completion. Every acquire frame must pass through the candidate
or second-phase event source and commit the acquire reservation—never a side
queue, because the survey loop admits work only through `candidates`
(`microclaw/tools.py:4899-4917`).

The search-channel switch and acquire-channel switch each run the channel-plan
authorizer. Every enable effect reaches `check_illumination`
(`microclaw/authorization.py:1622-1624`), whose enable prompt is specifically
`illumination/enable` (`microclaw/safety.py:1092-1121`); an active session grant
may answer both streams, but changes no power or acquisition limit. A hook
illumination envelope remains separate: it authorizes hook-originated power
changes, not the parent's phase switches, and it retains its current export
refusal (`microclaw/tools.py:824-828`).

`laser_slot` is not evidence that the acquire laser is enabled. Its pre-flight
checks trigger mode/sequence and explicitly leaves device enables and the
emission path unverified (`microclaw/tools.py:2506-2556`). This design relies on
the verified channel-plan writes/read-backs, not that pre-flight, to establish
the requested acquire-channel state. The runner must not start live view; live
view can itself fire camera-triggered lasers and is outside both reservations.

## Standalone emission

Emit the program, not its first run's hit list:

```python
# seed positions + exact hook source + real adaptive loop
# recorded, authorized search-channel effects; channel-less search events
hits = run_search(..., acquire_on_hit=...)
# recorded, authorized acquire-channel effects; channel-less acquire events
run_batched_protocol(positions=hits, max_hits=9, ...)
```

The emitter must inline the real decision machinery, as it does today
(`microclaw/tools.py:664-704`), and use the exact hook export path
(`microclaw/tools.py:752-812`). Channel effects are exportable on both routes:
recorded device/property writes for a channel plan, or `set_config` for a config
group (`microclaw/tools.py:1937-1971`). `_emit_adaptive_survey` remains the
adjacent renderer and delegates to `_emit_adaptive`
(`microclaw/tools.py:1008-1009`); extend that path rather than copy the loop.

Existing `CannotEmit` cases survive: composed or absent hooks, plugin hooks with
controller-only capabilities, unavailable or invalid saved-hook source
(`microclaw/tools.py:752-812`); an illumination envelope or artifact budget not
represented standalone (`microclaw/tools.py:824-833`); missing safety limits or
unresolvable/incomplete seed positions (`microclaw/tools.py:867-922`); and an
unknown survey protocol (`microclaw/tools.py:923-932`). Add refusal when either
phase lacks recorded executable channel effects, matching `set_channel`'s
existing refusal (`microclaw/tools.py:1963-1971`). Do not emit `laser_slot` as a
safety claim; it currently emits no line (`microclaw/tools.py:976-977`).
The standalone script has no Microclaw ledger; it carries the same literal
`max_hits`, frame count, exposure, and recorded safety bounds, and refuses before
submitting a derived event beyond them.

## Follow-on blocks and gates

1. Implement `acquire_on_hit`, deferred/deduplicated `AcquireAt`, two reservations,
   and the live two-phase runner. Unit-gate absent-argument non-regression,
   `max_hits`, duplicate hits, zero hits, refusal before the first exposure, and
   reservation completion/unused capacity.
2. Extend the adjacent adaptive emitter and its free-name/parse tests. Prove the
   emitted program selects fresh hits rather than replaying recorded coordinates.
3. Gate first on M5: EMU channel plans, no `Channel` group, camera-triggered
   lasers. Verify 561 search, exactly one 488 switch, both enable audit streams,
   acquire-frame dose, zero-hit no-switch/no-acquire, and standalone replay with
   Microclaw closed. Then smoke-test a `Channel`-group machine for the preset
   route.

A demo camera can prove event counts, phase order, bounds, logs, and script
replay. Its identical frames cannot prove that 561 evidence selected a biological
hit, that 488 differs optically from 561, that an enable line emitted light, or
that the acquired burst contains the intended structure; those require M5 plus
operator-visible images.
