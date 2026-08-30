# Stage-move arrival: a responsiveness check, not a tolerance

## Problem

Microclaw applies one package constant to every single-axis move:

```python
STAGE_MOVE_TOLERANCE_UM = 0.5
```

That number is not a property of every stage. A move can finish, report `idle`,
and sit at the closest position the device can reproducibly reach. If that
position is more than 0.5 um from the requested coordinate, `settle_stage_move`
polls until its 10 second deadline and raises `StageMoveError`. The caller then
either abandons a valid move or changes the requested coordinate to the value
this particular stage happened to report. The latter silently changes the
experiment to satisfy a software constant.

This has happened twice on the same real class of device, through two
independent paths:

- **M2, design/55 Part B, 2026-08-26.** `TIRF Stage` was asked for 199.9 um,
  became idle at 198.8 um, and failed after 10.109 s because the 1.1 um residual
  exceeded 0.5 um. The sweep completed only after its target was changed to
  198.8 um.
- **M2, Amr's TIRF session, 2026-08-29.** Requests for 528, 552 and 551 um
  became idle at 526.9, 551.1 and 550.3 um respectively. Residuals of 1.1, 0.9
  and 0.7 um each consumed approximately 10.1 s and raised. Other commands to
  the same stage happened to land within 0.5 um and completed in 0.3–0.4 s.

## What the check is actually for

The check exists because of block 56's defect: a **premature read-back**. One
immediate read after `wait_for_device` returned the *pre-move* position, and a
22.85 um miss was reported as a success. The lesson recorded in `CLAUDE.md` is
that a device which is not busy is not a device that arrived.

That is a question about **response**, not about accuracy:

> Did this axis act on the command, or did nothing happen — a serial timeout,
> a controller that ignored the write, a stalled or unpowered axis?

Microclaw does not have enough device-specific information to impose a
universal positioning-accuracy requirement. Micro-Manager and the controller
run whatever closed loop the stage has and expose its achieved coordinate, but
an idle adapter does not thereby guarantee that the coordinate equals the
request. Resolution, backlash, settling behaviour and the accuracy required by
the experiment are axis-specific. When Microclaw insists on 0.5 um everywhere,
it turns a number that came from nowhere into a universal accuracy claim. The
default below instead verifies substantial response; an operator who needs an
absolute accuracy requirement declares one for that axis.

**So the earlier draft of this document was wrong.** It proposed a per-axis
tolerance in the safety config, and then — because an operator cannot know that
number at install time — a mechanism to measure each axis's repeatability at
runtime, derive a band from a margin and a ceiling, persist it to the knowledge
base, load it in later sessions, thread it through seven call sites as a value
object, and emit it per-axis into exported scripts. All of that machinery
existed to discover a number we do not need. Deleted.

## Decision

### One relative criterion, no per-axis number

A move is verified when the axis is **stably parked after making substantial
progress toward the target**:

```python
STAGE_MOVE_RESPONSE_BAND_UM = 2.0     # replaces STAGE_MOVE_TOLERANCE_UM
STAGE_MOVE_RESPONSE_FRACTION = 0.1

band = max(STAGE_MOVE_RESPONSE_BAND_UM,
           STAGE_MOVE_RESPONSE_FRACTION * abs(target_um - start_um))
```

`band_source` is `"relative"` when the relative term is greater than or equal
to the floor (including the 20 um tie), otherwise `"floor"`.

Exactly as today, verification requires `STAGE_MOVE_REQUIRED_SAMPLES` finite
reads spanning at least `STAGE_MOVE_STABILITY_WINDOW_S`, all within `band` of
the target. Device busy/idle status remains evidence in the result, not a
success gate: some adapters do not report it reliably, and changing that would
be a separate behavioural decision. Only the band changes. The stability
window is what caught the premature read-back and it is untouched.

For displacement `D = abs(target_um - start_um)`, a move larger than the band
must make at least `D - band` progress toward the target. Once the relative
term dominates, that means at least 90% progress. Near the 2 um floor the claim
is deliberately weaker:

| displacement `D` | band | minimum progress required | as a fraction of `D` | response verifiable? |
| ---: | ---: | ---: | ---: | --- |
| 1.9 um | 2.0 um | 0.0 um | 0% | no |
| 2.0 um | 2.0 um | 0.0 um | 0% | no |
| 2.1 um | 2.0 um | 0.1 um | 5% | yes, weakly |
| 2.22 um | 2.0 um | 0.22 um | 10% | yes, weakly |
| 4 um | 2.0 um | 2.0 um | 50% | yes |
| 10 um | 2.0 um | 8.0 um | 80% | yes |
| 20 um | 2.0 um | 18.0 um | 90% | yes |

In closed form the guaranteed progress fraction is `1 - band / D`: it reaches
50% at twice the floor and 90% at 20 um, where the relative term takes over and
holds it there for every larger move.

The constants are detector policy, not physical properties. The observed M2
miss was 1.1 um on an approximately 20 um move, a 5.5% residual. Ten percent is
a conservative round margin above that observed ratio while still rejecting a
halfway stall or a stationary axis; 2 um covers the observed 0.7--1.1 um
residuals without requiring per-axis calibration. Other values could reasonably
be chosen as more evidence accumulates, so their rationale and discrimination
tests live with the constants.

Against the evidence we have:

| case | displacement | residual | band | outcome |
| --- | --- | --- | --- | --- |
| M2 design/55 | ~20 um | 1.1 | 2.0 | passes |
| M2 Amr, 528 → 552 | 25.1 | 0.9 | 2.5 | passes |
| M2 Amr, 552 → 551 | 0.1 | 0.7 | 2.0 | passes, flagged unverifiable |
| block 56 premature read | > 2 um | = displacement | < displacement | **fails** |
| axis does not move at all | > 2 um | = displacement | < displacement | **fails** |
| stall halfway | 200 um | 100 | 20 | **fails** |

Every recorded M2 landing passes with nothing configured. A stationary axis
fails whenever the displacement exceeds the effective band; at or below it the
result is explicitly unverifiable. That is the whole feature.

### The one real cost, stated plainly

A relative band accepts proportionally larger absolute errors on larger moves:
a 200 um move is verified to 20 um. That is a deliberate loosening of what
Microclaw asserts, and it is honest — Microclaw was never in a position to
assert 0.5 um. The achieved coordinate is reported on every move
(`measured_um`, and now `arrival_residual_um`), so a caller can see the miss. The
default result asserts response, not positioning accuracy; a configured
per-axis tolerance is the mechanism for enforcing the latter at this boundary.

A second consequence follows from anchoring on `start_um`: the same target
commanded from two different starting coordinates is checked against two
different bands, so a landing that passes on a long approach can refuse on a
short one. That is inherent to asking about response rather than accuracy, and
it is why the failure message must state how the band was derived.

If that is not acceptable on some axis, the optional configuration key below
turns the relative rule off for that axis.

### The floor, and moves too small to verify

`STAGE_MOVE_RESPONSE_BAND_UM` is not a claim about any stage's repeatability.
It is the smallest miss Microclaw is willing to call a non-response, and it
exists so that a small commanded move does not get an absurdly tight band.

Its consequence must be disclosed rather than hidden: when
`abs(target_um - start_um) <= band`, a stationary axis is inside the band, so
arrival cannot be distinguished from no motion. The result carries
`arrival_unverifiable: true` with both numbers. Define it mechanically for
every band source:

```python
arrival_unverifiable = (
    start_um is None
    or abs(target_um - start_um) <= effective_band_um
)
```

Thus a configured band can also make a small move unverifiable, and a record
without `start_um` is always unverifiable. It is **not** a refusal —
legitimate sub-micron steps are ordinary, and refusing them would break
autofocus and z-stacks — and it is **not** a confirmation, because a "no" would
only abort the run while the stage stays where it is (CLAUDE.md: if the answer
is "nothing, except abort the run", it is information).

**Name the channel, because almost no caller has an event sink.** Six of the
seven settle sites have none: `controller.set_z`, `move_stage_z`,
`move_named_stage`, and all three in `autofocus.py`, which imports no sink and
hands back a plain `SweepResult`. Only `UntrustedHookAdapter._apply_named_stage`
sits beside one (`hook_decisions._record_event`). So the tool result *is* the
channel — the flag lives in the returned dict and in the history record, never
only in a log line — and the hook path additionally records an event.

Do **not** give the autofocus sites a sink in order to carry this flag: adding a
channel to deliver a disclosure is the layer CLAUDE.md tells us not to add, and
the sweep already has somewhere to put it (below). design/60's lesson is that a
clause whose only outlet is one channel gets *defined* by that channel. The
correction is to check which callers have a channel at all — not to assume the
busiest one does.

**Its volume is dominated by one caller.** `sweep_autofocus` settles once per
plane, and the axis is standing on the previous plane when it reads `start_um`,
so its displacement is `z_step_um` — routinely at or below the floor. Every probe plane
of every autofocus will report `arrival_unverifiable: true`, and a plane where
the axis did not move at all is now accepted where the 0.5 um constant raised.
Report it in the sweep result as a count plus the plane indices, not one record
per plane, or the flag is noise by its second use. That aggregation is part of
this block's result contract and is covered below; it does not itself decide
that the sweep failed to advance.

**The floor is also where this change costs the most, and "this is not a
regression" would be too narrow a claim.** The new band is at least 2.0 um for
every displacement, so it is looser than the old 0.5 um everywhere — but the
loss is not uniform. Above 20 um the relative term takes over and the rule
guarantees 90% progress, which a fixed 0.5 um band never asserted about a large
move because it could not be met at all. Between the floor and about 5 um it is
the reverse: a 3 um Z step, ordinary in a stack or an autofocus probe, was
checked against 0.5 um and is now checked against 33% progress. Nothing flags
that, because `arrival_unverifiable` is a cliff at `D <= band` while
verification strength decays continuously above it.

The honest summary is that this design trades small-move accuracy checking —
which a 0.5 um constant did badly, on a stage it knew nothing about — for
large-move response detection, which it did not do at all. An operator who needs
the small-move check declares a per-axis tolerance, and that is the second
reason the key exists.

**And the sweep can close most of that gap with data it already holds.**
`sweep_autofocus` collects `measured_z_positions` and already carries
`unsettled_indices` for a neighbouring purpose. Measured Z that fails to advance
from one plane to the next is exactly the non-response the per-move check can no
longer see in this regime, it needs no new reads, and the most-run Z path in the
product is where the loss lands. This block reports the affected planes but
does not add a new sweep-level refusal. Before this block closes, add a named
design/35 register row for cross-plane non-response detection, with this section
as its source. Filing it under "out of scope, a different mechanism" without a
scheduled owner
would lose the fact that *this design created the gap*.

Extending `arrival_unverifiable` to `D < 2 * band`, the 50% point, was
considered and not taken: it would flag ordinary z-stack and autofocus steps on
every rig, and the flag means arrival is indistinguishable from no motion, which
is literally true only at `D <= band`. Cross-plane detection is what actually
covers this regime, and it belongs to the design/35 row named above — not to a
widened flag, and not to an unowned "out of scope" line.

### Restoration verifies against the floor

A restoration move — `autofocus._restore`, named-stage envelope restoration, the
z-stack and acquisition restoration paths — returns an axis to a coordinate it
demonstrably occupied seconds earlier, and the run's printed envelope promised
`restore: "entry"`. Its displacement is the whole sweep, so the relative term
would verify a 200 um restoration to 20 um. The entry coordinate is known, but
an acceptable absolute miss is not unless the operator declared one. In the
absence of that declaration, using the package floor is a deliberate stricter
interpretation of the restoration promise, not a claim that 2 um is the stage's
physical accuracy.

Restoration therefore passes `band_policy="floor"` — a declared value for that
axis still wins where there is one — never the relative term. Its `band_source`
is `"floor"` or `"configured"` by construction, and a test pins that at each
restoration site.

The cost is that a stage which misses a long move by more than the floor now
fails on restoration where the outbound move passed. That is the correct report,
not a regression: these paths already surface a restoration failure without
masking an acquisition error, and an envelope that cannot be honoured is exactly
what the operator needs told.

### `start_um` and band policy become part of the contract

The criterion needs where the axis started, so every caller reads the position
before dispatching and passes it — together with the axis's declared value, if
it has one:

```python
settle_stage_move(
    core, device, target_um, start_um, band_policy, configured_band_um
)
stage_move_dispatch_failure(
    core, device, target_um, start_um, band_policy, configured_band_um, exc
)
```

`band_policy` is `"relative"` for an ordinary move and `"floor"` for a
restoration. `configured_band_um` is the axis's declared value or `None`; when
present it wins over either package policy and produces
`band_source="configured"`. Otherwise the function derives the effective band
and source from `band_policy`, `start_um` and the target. Policy and magnitude
are separate because the package floor is not a configured accuracy value: a
numeric `band_um=2.0` could not say whether it meant "force the response floor"
or "the operator declared 2.0 um accuracy".

Both are arguments rather than a guard lookup inside the function for two
reasons: `settle_stage_move` is inlined **verbatim** into exported scripts,
where no `SafetyGuard` exists, and a settle loop that reaches for global config
is not a seam this codebase has. Without `configured_band_um` the declared
tolerance below has no path from the config file to the check it is supposed to
change — the earlier draft of this section had none.

No contract argument takes a default. A default is what lets a path that forgets
to thread the value silently verify against the floor alone — a strictly
different criterion, invisible in a green suite. A site that genuinely cannot
read a start position passes `start_um=None` explicitly, which means "floor
only"; there should be no such site, and a test asserts there is none.

`start_um` is also the evidence a refusal has been missing. "Requested 552,
measured 526.9" reads as a broken stage; "started at 526.9, requested 552,
measured 526.9, never moved" names the actual failure.

### Optional configuration, never a prerequisite

```yaml
stage:
  z_min: 0
  z_max: 200
  z_move_tolerance_um: 0.35

named_stages:
  - device: TIRF Stage
    min_um: -10497.8
    max_um: 6256.8
    move_tolerance_um: 1.5
```

A declared value **is** the band for that axis — the relative rule and the floor
are both off. That is the point: it exists for the operator who wants absolute
accuracy enforced on a particular axis, and it is the only way to get it.

Do **not** reserve `stage.x_move_tolerance_um` and `stage.y_move_tolerance_um`
as accepted-but-inert keys. `move_stage_xy` still lacks the arrival loop
recorded in design/35, so a value declared there would be a safety number that
silently does nothing — the shape this project keeps paying for. Parse them and
**refuse**, at their exact YAML path, with a message naming the missing loop, so
an operator learns the key has no effect at the moment they write it. The
refusal becomes an acceptance when the loop lands and consumes the same relative
criterion; fixing the names here is what leaves that fix nothing to invent.

This is intentionally not an advertised additive setting yet. The example
configuration must not contain either XY key, setup must not generate them, and
documentation must not present them as usable. The parser recognizes the names
only to produce the targeted "missing `move_stage_xy` arrival loop" error rather
than a generic unknown-key error. Existing version-3 files remain compatible
because neither key existed previously; a hand-written file containing one is
invalid until the XY loop lands.

**Setup writes no tolerance key of any kind** — not the default, not a
placeholder, not a comment. Endpoints are measurable at first launch and
repeatability is not, and a generated key an operator tunes by guessing is worse
than no key. `safety_config.example.yaml` shows the two tolerance keys — and
neither XY key — with fictional non-round values, like every other value in that file, so `config.py`'s
`example_limits` diagnostic — which warns when a rig's numeric leaf equals the
example's — cannot fire on a value nothing generates. `matching_leaves` is left
alone.

Parsing details, unchanged from the earlier draft because they were about the
schema rather than the mechanism:

- The two **tolerance** keys — `stage.z_move_tolerance_um` and a named stage's
  `move_tolerance_um`, not the refused XY pair above — are parsed beside the
  travel bounds but are **not** range edges.
  `_stage_ranges` builds `dict[ActuatorId, RangePolicy]` and `_stage_constraints`
  compiles it; a tolerance has no `{unbounded: true, reason: ...}` form and must
  be refused if given one. Give `_stage_constraints` a second input rather than
  widening `RangePolicy`, which `authorization.py` reads for axis-coverage
  cross-checks.
- A tolerance on an axis whose bounds are not declared is a configuration error
  at its exact YAML path, not a silent no-op.
- Reject zero, negative, boolean, NaN and infinite values with field-specific
  errors. No maximum: the operator is deciding.
- Existing version-3 files stay valid; the two tolerance keys are additive. The
  XY pair is the exception recorded above: recognized only in order to be
  refused.

### No per-call override, and nothing is learned

No `tolerance_um` argument on `move_stage_z`, `move_named_stage`, autofocus,
acquisition tools, hook actions or tool schemas: an agent under pressure to
continue must not be able to redefine success for one call. A caller may still
request a different target — that stays visible as a different experiment.

Nothing is persisted, nothing is derived from residuals, and the knowledge base
gains no entries. There is no per-axis fact left to remember.

### Result and error records

Keep `requested_um`, `measured_um`, `tolerance_um`, `within_tolerance`,
`elapsed_s` and `last_device_status`; `tolerance_um` is the effective band.
Add `start_um`, `arrival_residual_um`, `band_policy` (`"relative"` or
`"floor"`), `band_source` (`"relative"`, `"floor"` or `"configured"`),
`arrival_unverifiable`, and `verification_kind`. `band_policy` records what the
caller requested; `band_source` records what actually supplied the effective
band after a declared value won. `verification_kind` is `"response"` for either
package policy and `"configured_accuracy"` for a declared absolute tolerance.
Consumers must not infer absolute accuracy merely from `within_tolerance: true`
under the response rule.

`arrival_residual_um` is specifically the scalar arrival miss on one axis:
`abs(measured_um - requested_um)`. Do not call it merely `residual_um`.
`center_feature` also reports a residual, but that is the norm of the feature's
remaining offset expressed as a centring move (`math.hypot(*residual_move_um)`),
not evidence about whether the stage reached its requested coordinate. Block
64c's corrective rename and rationale are recorded in design/67, §"Result
contract: do not collide with stage arrival", and in design/35's block-64c
ledger row. A centring session contains the `center_feature` result and its
`move_stage_xy` results next to one another; generic names would make the two
physical quantities indistinguishable to an agent reading history. The feature
quantity is `residual_offset_um`; the arrival quantity is
`arrival_residual_um`. These names remain distinct even when a result is copied
out of its tool envelope.

The later XY arrival contract must preserve the same distinction per axis:
`x_arrival_residual_um` and `y_arrival_residual_um` (or equivalently named
members of an arrival-residual mapping), each computed from that axis's
requested and measured coordinates. It must not collapse them with `hypot` or
reuse `residual_offset_um`: the response gate is evaluated per axis, whereas
the centring residual is a two-dimensional feature-to-centre distance.

The old field names are kept deliberately, against this project's usual
no-legacy-anchoring rule: `tolerance_um` and `within_tolerance` have been in
every record since block 56 (2026-08-19), the history viewer and every gate
scorer read them, and the names are not wrong — the band is still a tolerance.
Renaming them is not nearly free, so it is not done.

The target is still checked against its exact configured travel bounds before
dispatch. The band never expands `min_um` or `max_um`.

## Runtime propagation

Three values thread to every user of the settle contract: `start_um`,
`band_policy` and `configured_band_um`:

- `MicroscopeController.set_z`, the seam every focus move is supposed to use;
- `move_stage_z` and `move_named_stage`;
- autofocus probe moves, final best-position move and restoration;
- z-stack and acquisition restoration paths;
- `MoveNamedStage` hook actions and named-stage envelope restoration;
- any controller wrapper exposing a settled focus move.

As of 2026-08-30 there are seven settle call sites and seven matching
dispatch-failure sites in four modules: `controller.set_z` (:967, :968),
`move_stage_z` (`tools.py:2777, :2779`), `move_named_stage`
(`tools.py:2878, :2879`), `UntrustedHookAdapter._apply_named_stage`
(`hook_decisions.py:563, :566`), and three in `autofocus.py` — the per-plane
probe move (:343, :346), the final best-Z move (:367, :370) and `_restore`
(:393, :396). The constant is read only inside `controller.py` (:53, :89, :96,
:105, :116) and emitted at `tools.py:2730`.

This is a reachability requirement, not a claim that those are the only textual
sites. Each motion family must prove it passes a real start position, not that
one tool does.

Reading `start_um` immediately before each dispatch costs one extra bridge round
trip per move, and pyjavaz serializes every one of them. In a sweep that is one
extra `get_position` per plane. Accepted: it is the smallest read on the path,
and a cached start is exactly the stale value block 56's premature read-back was.
Do not "optimise" it by reusing the previous plane's measured position from the
sweep's own list — that is a cache, and test 12 is written to catch it.

The declared-value lookup, where a config value exists, is one public accessor
on `SafetyGuard` (or its immutable constraints) — Core focus keyed on the focus
device label, named stages on the entry that authorizes the move — not ad hoc
`guard._c` access at each site. `set_z` holds an *optional* guard
(`if self._guard is not None`), so the accessor must say what a guardless
controller uses: the package rule, never a second constant at that site.

## Standalone export

This is now nearly free, which is one of the reasons to prefer this design.

`_stage_move_contract_source()` emits two constants instead of one and the same
inlined `settle_stage_move`. `_emit_stage_settle` and `_emit_stage_dispatch`
gain three literals beside the target they already render: the recorded
`start_um`, `band_policy` and `configured_band_um`.

**Neither policy literal is optional.** A declared value lives in the safety
config, which a standalone script cannot read; emit the package rule in its
place and the exported move verifies against a band up to an order of magnitude
looser than the one that ran — silently, on precisely the axis an operator cared
enough to configure. Dropping a restoration's `"floor"` policy likewise makes
the standalone script recompute a relative band and loosen the return-to-entry
promise. CLAUDE.md's rule is that an emitted step must not be *stricter* than
the tool it reproduces; looser is the worse direction, because nothing raises.
Emit the recorded `band_policy` on every move, and emit the record's own
`tolerance_um` as `configured_band_um` only where
`band_source="configured"`; otherwise emit `None`. Patching the package
constants therefore still moves every non-configured emitted script without
changing which package policy it applies.

Nothing is *computed* per axis at export time — the policy and any declared
band are copied out of the record — so:

- `sweep_autofocus`, `_restore` and `UntrustedHookAdapter._apply_named_stage`
  keep their signatures — the earlier draft had to thread a tolerance through
  all three because they are inlined **verbatim** by `inspect.getsource` and
  cannot look anything up;
- `_named_stage_context` gains no field and `configure_named_stage`'s emitted
  literal call is unchanged;
- there is no new `CannotEmit` case;
- `test_emitted_stage_settle_uses_live_policy_constants` keeps working unchanged
  in both halves: patching the package constants still moves the emitted script,
  because the emitted script still uses package constants.
  `test_stage_move_contract_is_defined_once_however_many_moves` needs only the
  constant's new name.

A record with no `start_um` or `band_policy` exists only in hand-built fixtures
predating this change; emit `start_um=None`, `band_policy="relative"` and
`configured_band_um=None`. A missing start makes the effective source the floor
and always marks response as unverifiable. Its band is stricter than the
relative band could have been for a large move, but it cannot substantiate
non-response at all. That divergence from the recorded run is acceptable only
because no real record can take that path; a test pins it to the legacy fixture
shape rather than leaving it as a fallback anyone can reach.

## Observability

`get_system_state` reports one measured position per declared named stage
(`tools.py:3587`) and no bounds. Add `z_move_tolerance_um` beside the top-level
`z_um`, and a parallel `named_stage_move_tolerances_um` mapping from the same
labels — present only where a value is **declared**, since an axis with no
declared value has no fixed band to report, only a rule. Preserve the existing
shape: `named_stages` stays a mapping from label to measured position (or
`"unavailable"`); do not turn a position into an object and make every consumer
branch. It follows the existing `if guard._c.named_stages:` condition, so a rig
with no named stage gains no keys, and it costs no bridge round trips.

The rule itself belongs in the failure message, which is where an operator
meets it: name `start_um`, `measured_um`, the band and how it was derived, and
say whether the axis moved at all. Keep hardware error as a possibility, and
never tell the agent to retry the same target blindly or to compensate by
guessing another target.

## Tests

1. A fake stage stably 1.1 um short of a **40 um** commanded move **succeeds**,
   under the stability window rather than at the deadline, reporting
   `band_source: "relative"`, `tolerance_um: 4.0`,
   `arrival_residual_um: 1.1`. Not a
   20 um move: that is the tie, where the floor and the relative term are both
   2.0, so a site that never threaded `start_um` reports the same number and the
   test passes on a broken path.
2. The same stage short by 1.1 um of a 200 um move succeeds
   (`tolerance_um: 20.0`); short by 100 um of the same move refuses.
3. A stage that does not move at all refuses for every displacement larger than
   the floor, and the error names `start_um == measured_um`.
4. The premature-read regression: a fake that returns the pre-move position for
   the first reads and then the true one must not pass early — the block 56
   defect, re-pinned against the new band.
5. A commanded move smaller than the band reports `arrival_unverifiable: true`
   and is **not** refused.
   Boundary cases at 1.9, 2.0, 2.1, 2.22 and 20 um pin the floor transition and
   the exact minimum progress the rule accepts. A configured band also marks a
   move at or below that band unverifiable, and `start_um=None` always does so.
6. A declared per-axis value is the band exactly: a 200 um move with
   `move_tolerance_um: 1.5` refuses at a 5 um residual, where the relative rule
   would have passed it. Two named stages with different declared values do not
   cross-talk. The declared value reaches `settle_stage_move` as
   `configured_band_um` via the guard accessor — so the test drives the
   **registered** `move_named_stage` against a configured axis, not the settle
   function directly, since the delivery path is the part that did not
   previously exist.
7. Each restoration site reports `band_source: "floor"` (or `"configured"`) for
   a restoration whose displacement would otherwise select the relative term,
   and a restoration missing by more than the floor refuses rather than
   reporting a restored envelope. Its record and standalone export retain
   `band_policy: "floor"`; the emitted move must not revert to the relative
   rule.
8. `sweep_autofocus` aggregates every probe move with
   `arrival_unverifiable: true` into a count and the exact plane indices on
   `SweepResult`, beside `unsettled_indices` — same shape, same list-of-indices
   precedent — and `run_autofocus` surfaces both in its tool result. No event
   sink is added to `autofocus.py`, no event is emitted per plane, and the
   aggregation does not become a new refusal.
9. Config parsing: distinct positive values for Core focus and two named stages;
   invalid values fail at their exact YAML paths; unknown-key checks recognize
   the new fields; a tolerance on an axis with no declared bounds is an error at
   its exact path.
10. `stage.x_move_tolerance_um` and `stage.y_move_tolerance_um` are refused at
   their exact YAML path, with a message naming `move_stage_xy`'s missing
   arrival loop — not accepted and ignored — and neither key appears in the
   example configuration or setup output.
11. `settle_stage_move` and `stage_move_dispatch_failure` reject a call omitting
    `start_um`, `band_policy` or `configured_band_um`; no live call site passes
    `start_um=None`.
12. `move_stage_z`, `move_named_stage`, autofocus, a plan-only named-stage hook,
    failure restoration and successful restoration each pass a real start
    position and report it. At least one path changes the fake's position
    between an earlier observation and dispatch, proving the start is read
    immediately before the write rather than taken from a cache or a stale
    caller value.
13. Dispatch errors report the same policy, source and effective band the settle
    path would have used.
14. Exact travel-bound checks are unchanged by a wide band; a target outside
    bounds still refuses.
15. Setup output contains no tolerance key, and a setup-written config does not
    raise `config.py`'s `example_limits` warning.
16. Export: emitted direct, autofocus, restoration and hooked moves carry the
    recorded `start_um`, `band_policy` and configured band, run standalone, and
    make the same pass/fail decision as the live call — **including a
    floor-policy restoration and an axis with a declared tolerance**, the two
    cases an emitter that copied only the package defaults gets wrong.
17. Result consumers distinguish `verification_kind: "response"` from
    `"configured_accuracy"`; no history-view or gate-scoring assertion treats
    package-rule `within_tolerance: true` as proof of absolute accuracy.

**What the tests all happen to pass.** The old constant was 0.5 and the new
floor is 2.0, so a fixture whose stage parks within 0.5 um passes under both and
proves nothing. Every criterion test above must use a residual between the two,
or a displacement large enough that the relative term dominates — and no
propagation test may assert `tolerance_um == 2.0`, since that is also what a
site with no threading at all would report.

Two cautions on the fakes, from CLAUDE.md. Test 1's stage must be *stably short*
of a *large* move: a fake that parks on target cannot distinguish this change
from no change, and a fake whose displacement is small silently takes the floor
branch and makes the central test pass for the wrong reason. Test 12 must drive
the **registered** tools, not a locally defined stand-in — a fixture that cannot
reach the real error handling is not coverage of it (block 60a: fifteen tests
green over three broken tools).

## Rig gate

Much smaller than the earlier draft's, because there is no configuration to set
up, no learned state to clear and restore, and no second and third session under
different policies.

**One M2 session, nothing configured.** From a safe starting coordinate more
than **20 um** away, command `TIRF Stage` to the established probe coordinate
(199.9 um from design/55, subject to the current safe envelope). Twenty is the
tie: start any closer and the floor wins, the relative rule is never exercised
on hardware, and the block's central limb passes while testing a constant.
Required from the history JSONL, not from a verdict:

- the move **succeeds**, where it failed on 2026-08-26 and 2026-08-29;
- `start_um`, `measured_um` and `arrival_residual_um` are all present, and
  `arrival_residual_um` is **greater than 0.5**. This is the discrimination, not
  a detail: the same stage landed inside 0.5 um on several of Amr's commands,
  and those would have passed under the old constant too. A residual at or below
  0.5 um means the limb tested nothing and is **NOT EXERCISED**, exactly as
  `band_source: "floor"` is. The move having succeeded is not the evidence;
- `tolerance_um` equals `max(2.0, 0.1 * abs(target - start))`, recomputed from
  the record's own two numbers rather than trusted from the field, and
  `band_source` is `"relative"` — not merely consistent with the numbers.
  `"floor"` here means the starting coordinate precondition was not met and the
  limb is **NOT EXERCISED**, whatever the move did;
- `band_policy` is present and reads `"relative"`. It costs nothing — the scorer
  already has the record open — and a missing `band_policy` on a live record is
  exactly the propagation defect test 16 exists to catch off-rig;
- `elapsed_s` an order of magnitude below the 10 s deadline. A pass near 10 s is
  the timeout path wearing a success;
- the target passed the unchanged travel guard and was never retargeted to the
  measured coordinate.

Then export **that move only** — `export_session_script` compiles the whole
session, so an unselected export re-runs every acquisition the session made when
executed standalone; pass the move's `tool_use_ids` and everything else stays a
`# SKIPPED` comment. Close Microclaw and run the script; it must succeed.

**The control limb must be able to fail.** A limb that cannot fail is not a
criterion. The rig control is a genuine non-response: with the operator's
judgement that it is safe on their hardware, power down or disconnect the
`TIRF Stage` controller and repeat the same command. Expect a refusal naming
either a dispatch error or a stable non-response with
`start_um == measured_um` — and expect it well before the deadline for the
dispatch case. If the operator judges that unsafe, this limb reports
**NOT EXERCISED**, which is never a pass, and the block's only discrimination
evidence is the off-rig suite — say so in the checklist row rather than ticking
it.

The demo machine supplies the regression limb: exact instant arrival, still
verified, `band_source` reported, and no behavioural change.

One offline script performs the computational half: it takes the history path,
the emitted script path and the standalone console capture as **required
arguments**, checks each limb independently so an earlier refusal cannot hide a
later cascade, exits nonzero, and writes its own log. A path that is missing,
empty, or contains no matching call is **NOT EXERCISED** and a nonzero exit,
never a silent zero match — 52c shipped `-Pattern "<t2>"`, it was run verbatim,
matched nothing and "passed". Run it against a bridge-shaped fake off-rig first
(`design/55-gate-probe-selftest.py`), and write that fake's stage from the
device's behaviour, not from what the scorer expects to see.

**The standalone run has no move-result artifact.** `_emit_stage_settle` renders
the settle call as a bare expression, so the script prints nothing about the
move: the evidence is its exit status. A missing, malformed or duplicate
`EXIT_CODE` line is **NOT EXERCISED**, never inferred from whether traceback
text happens to be present.

**Write the capture with one writer and a pinned encoding.** Under Windows
PowerShell 5.1 `>` is `Out-File`, defaulting to UTF-16LE, while `Add-Content`
defaults to the ANSI code page — redirecting the run and then appending the
trailer produces a file that is neither, and a scorer reading UTF-8 gets
mojibake or raises before it reaches the limb.

The following is **generator notation, not a runnable command block**. Each
guillemet name is resolved by the command-sheet generator, which emits its
quoted literal value. The sigil must not be `$NAME`: `$out`, `$code` and
`$LASTEXITCODE` are real PowerShell variables that have to survive
substitution, and an unsubstituted `$SCRIPT` would not fail — PowerShell expands
an undefined variable to the empty string, so the sheet would run a command that
quietly does nothing. A leftover `«SCRIPT»` is a parse error, which is what we
want:

```text
$raw  = & «INTERPRETER» «SCRIPT» 2>&1 | ForEach-Object { $_.ToString() }
$code = $LASTEXITCODE
$out  = $raw | Out-String -Width 4096
Set-Content -Path «CAPTURE» -Value ($out + "`r`nEXIT_CODE=$code`r`n") -Encoding UTF8
```

Neither pipeline element is decoration. `2>&1` on a native command hands
PowerShell `ErrorRecord` objects, which `Out-String` would render through the
error formatter and decorate with `CategoryInfo` blocks, so normalize to raw
text first; and `Out-String` defaults to the host's console width, hard-wrapping
long lines in a non-interactive host, so `-Width 4096` keeps the traceback
intact. The scorer classifies by exception type, so a mangled traceback is a
mis-scored limb. `$LASTEXITCODE` survives the intervening cmdlet because only a
native command resets it, and `-Encoding UTF8` writes a BOM in 5.1, so the
scorer opens the capture as `utf-8-sig`. Do not set
`$ErrorActionPreference = 'Stop'` around this: with `2>&1` it turns ordinary
child stderr into a terminating `NativeCommandError` and the capture never gets
written.

**A standalone run that could not start is NOT EXERCISED, not a FAIL.** The
exported script imports `pycromanager` and opens its own `Core()`, so name the
interpreter in the sheet rather than assuming a bare `uv run python` resolves to
it. An `ImportError`, a missing interpreter or an unreachable ZMQ bridge is a
nonzero exit that says nothing about arrival; the scorer classifies on the
traceback's exception type, where `StageMoveError` is the FAIL and anything
before the first move is NOT EXERCISED.

## What this closes

The first of the two register rows design/55 opened on 2026-08-26 (`design/35`,
"Two from design/55's gates") asked where the tolerance lives for the core focus
and XY, what the default is, and whether `move_named_stage` gets a per-call
override. The answers: it does not live anywhere per-axis unless an operator
chooses to declare one; the default is a relative rule with a floor; and no.
The row closes after the implementation and the rig gate land.

## Out of scope

- Changing the 10 second deadline, the poll cadence, the sample count or the
  stability window. Those caught the original defect and are untouched.
- Treating `idle` as arrival, or returning success because motion occurred.
- Imposing a universal positioning-*accuracy* requirement. The default verifies
  substantial response and reports the achieved coordinate; a declared
  per-axis tolerance enforces an experiment-specific absolute requirement.
- Detecting non-response across a sweep of sub-band steps — measured Z failing
  to advance plane to plane during autofocus or a z-stack. A different
  mechanism, but **this design is what opens that gap**, in the most-run Z path
  in the product; see "the sweep can close most of that gap" above. This block
  aggregates the unverifiable plane indices but does not refuse on them. The
  required design/35 follow-up row owns detection before this block closes.
- Fixing `move_stage_xy`'s missing arrival loop. This design fixes the names of
  its keys, refuses them until the loop exists, and defines the criterion, so
  that fix has nothing left to invent.
- The three Z paths that reach no settle loop at all: `hooks.py`'s
  focus-recovery jog (`hooks.py:388`), the tile path's per-position Z
  (`tools.py:6153`) and `_emit_go_to_position`. They take no start position
  because they take no settle; do not quietly give them one here.
- Any per-call escape hatch.

## Coordination

This design is one implementation block, **66**, not a sequence of independently
mergeable sub-blocks. The settle-function signature, all live callers, result
records and standalone emitters form one contract: landing only part of it would
either break every caller or, worse, let one path verify a different band. Use
branch `design66/stage-move-response`, created from updated `main`, and record
its start commit in the design/35 run ledger before assigning the runner.

The implementation runner owns the whole decision above in one linked worktree:

- replace the fixed arrival constant with the response floor/fraction policy and
  the explicit `start_um`, `band_policy`, `configured_band_um` contract;
- parse, validate and expose the two usable per-axis configuration fields while
  refusing the two inert XY fields; do not change setup output;
- propagate a fresh pre-dispatch position and the configured value through all
  seven settle and dispatch-failure sites, using floor policy for every
  restoration;
- add the result fields, autofocus aggregation, hook event and system-state
  observability specified above;
- update both standalone-emission paths so live and emitted policy decisions are
  identical, including configured and restoration cases; and
- implement Tests 1–17. Add the named design/35 register row for cross-plane
  non-response detection in the same branch; it is an acceptance condition, not
  an optional follow-up.

The runner must not implement the out-of-scope XY arrival loop or the three
unsettled Z paths. It must commit and report without merging. Its exact prompt is
written to the scratchpad before launch and its job directory is retained until
the block closes.

Coordinator review is against the diff and the full suite, not the runner's
summary. In addition to Tests 1–17, explicitly audit every `settle_stage_move`
and `stage_move_dispatch_failure` call, both emitted-call renderers, and every
restoration site. Watch the new discrimination tests fail on the pre-fix tree
for the intended reason; in particular, a test whose residual is at most 0.5 um
or whose effective band is the 2.0 um floor does not prove propagation of the
relative rule. Drive registered tools and bridge-shaped fakes rather than local
stand-ins.

The branch's rig deliverables are committed with the implementation before the
push:

- an M2/demo runbook containing the human hardware steps in **Rig gate**;
- one offline scorer taking the history JSONL, selected-call emitted script and
  UTF-8 capture as required arguments, independently reporting PASS, FAIL or NOT
  EXERCISED for every limb and exiting nonzero unless all required limbs pass;
- a command-sheet generator that resolves every guillemet placeholder and emits
  the PowerShell capture block verbatim with quoted literal paths; and
- a bridge-shaped self-test for the scorer and generated sheet, run against both
  the pre-fix and implementation trees so its discrimination is demonstrated.

Pin the implementation in the runbook with
`git merge-base --is-ancestor <implementation-commit> HEAD`, push the branch,
and hand the user the M2 and demo gates. The M2 pass requires the relative-policy
success with a residual greater than 0.5 um, the selected-call standalone
success, and the genuine non-response control. If the operator declines the
control as unsafe, record it as NOT EXERCISED and do not call the rig gate a
pass. The demo limb proves exact instant arrival remains accepted. Score the
returned artifacts directly and loop fixes and re-tests on the same branch.

After the gates pass, merge and push `main`, delete both branch copies, record
the outcome in `design/prompts.md`, close the design/35 ledger row, and run the
post-merge documentation gate. That reconciliation must replace design/35's old
claim that the refusal “must not be fixed by loosening it” with this design's
measured response-versus-accuracy decision, close the first design/55 row, and
leave the new cross-plane non-response row open with an explicit owner/block.

## Coordinator checklist

- [ ] Update `main`; create `design66/stage-move-response`; record the exact
  start commit and block 66 row in design/35; commit those coordinator-owned
  edits before assignment.
- [ ] Write the block-66 runner prompt to the scratchpad; launch the configured
  Codex runner in its own linked worktree; retain its job directory and session.
- [ ] Confirm the runner committed but did not merge, and that its diff covers
  the complete implementation scope, Tests 1–17, and the cross-plane register
  row without entering any out-of-scope path.
- [ ] Review the diff line by line; audit all settle/dispatch callers,
  restorations and emitters; return concrete findings through the same runner
  session until resolved.
- [ ] Run the full suite in the coordinator checkout and record passed, skipped,
  warning and collected counts.
- [ ] On the pre-fix tree, run the new discrimination tests and record that they
  fail for the stated policy/propagation reason; restore the implementation tree
  and rerun them green.
- [ ] Run the bridge-shaped gate self-test on both trees; run the command-sheet
  generator; verify no unresolved guillemets, optional product configuration,
  non-independent limbs, or silent zero-match paths remain.
- [ ] Commit the runbook, scorer, generator and self-test on the block branch;
  verify the runbook's merge-base pin; push `design66/stage-move-response` to
  `origin` with no PR.
- [ ] Hand the user the M2 and demo gates; receive the history JSONL, selected
  emitted script, UTF-8 standalone capture, scorer log and demo evidence.
- [ ] Score artifacts rather than the reported verdict: require the M2 relative
  limb (`residual > 0.5 um`), exact recomputed band, fast completion, unchanged
  target, standalone exit, and safe non-response control; classify absent or
  non-discriminating evidence as NOT EXERCISED.
- [ ] Apply small findings on the branch or return larger findings to the same
  runner; push and repeat the user gates until every required limb passes.
- [ ] Merge the branch to `main`; push `main`; verify
  `git log --oneline origin/main..main` is empty; delete the local and remote
  block branches.
- [ ] Record coordination findings in `design/prompts.md`; close block 66 and
  the first design/55 row in design/35; run and commit the post-merge design
  reconciliation, including the still-open owned cross-plane row.
