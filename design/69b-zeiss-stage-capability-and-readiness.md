# Classify unmovable stages and fail clearly on controller mode

## Problem

The first Zeiss setup exposed two separate weaknesses in Microclaw's stage
model.

First, `ZeissDefiniteFocusOffset` is reported by Micro-Manager as a
`StageDevice`, so setup required operator-defined movement bounds. On this rig
it is telemetry-only: Microclaw can read its position but is not wanted to
command it. The operator could not complete setup without entering fabricated
`-100000..100000 um` bounds. Those values neither describe a safe range nor
authorize a real capability — and they are now **live** in that rig's written
config, where `move_named_stage` will honour them. `write_security_config`
refuses to overwrite an existing file, so clearing them is a hand edit; say so
in the fix's runbook.

Second, the Zeiss XY controller can be left in manual/joystick mode. Software
motion then returns:

```text
Unexpected response from serial port. Is the device connected to the correct
serial port? (16)
```

One occurrence was confirmed to be manual mode: after the operator restored
software/remote mode, two small moves and a 224-position acquisition completed.
The same generic serial error occurred later, but Microclaw again asserted
manual mode without evidence, and reissued the command. The final attempt also
requested the stage's already-current XY coordinate, making a redundant motion
command an avoidable failure point.

These failures returned promptly; this design does not treat them as hangs.

## Goals

- Let the operator finish setup without inventing bounds for a stage device
  Microclaw must not move.
- Never expose such a device through movement tools.
- Detect manual/remote mode without moving hardware **if** the adapter exposes a
  readable property that says so.
- Do not re-command a stored coordinate the stage is already at.
- Report serial error 16 accurately when controller mode cannot be proven, and
  stop the agent reissuing it.

## Investigation

### 1. Capture the live Zeiss inventory

On the affected rig, record read-only inventory evidence for
`ZeissDefiniteFocusOffset` and `XYStage`:

- device type and adapter/library identity;
- all property names;
- each property's value, type, `read_only`, `pre_init`, allowed values, and
  technical limits;
- Core focus and XY assignments;
- any property whose name or allowed values describes manual, joystick, host,
  remote, or computer control;
- whether a `Position` property exists and is marked read-only;
- adapter/module version.

Do not probe writability by issuing a movement. Setup discovery is read-only.

Run the inventory once in remote mode and once in manual mode, then diff only
the `XYStage` and serial-controller facts. This establishes whether mode is
directly detectable and which property/value pair is authoritative. The diff
needs an operator at the stand flipping the controller; it is the whole cost of
this investigation and cannot be simulated.

The enumeration must iterate Core collections with `size()`/`get(i)`. A
`list()` over a `StrVector` works against every fake in the suite and fails on
every rig (design/59 block 59a), and `calibration._config_mismatches` shows the
second half of that trap: a swallowed enumeration failure reads as a fact about
the machine.

### 2. Verify the movement surface

Micro-Manager stage motion is a device command, not necessarily a write to a
property named `Position`. `Position.read_only` is therefore **not** evidence
that an axis cannot be commanded, and this design does not treat it as such —
see Decision 1. What the inventory is for is telling the operator what the rig
looks like, not deciding for them.

Never infer read-only status because two readings are identical or a move once
failed.

### 3. Reproduce redundant and real moves separately

With the stage in remote mode:

- request the exact current XY position through `go_to_position` and through a
  tile position;
- request a genuine small move (below the 2 um response floor) through
  `move_stage_xy` and through `center_feature`, and confirm it is **dispatched**;
- request a genuine large move.

Also settle whether the parked **Zeiss** stage reads back within 0.001 um of the
three-decimal coordinate Microclaw exposed. Record repeated raw X/Y readings at
one parked position, commanding nothing, and compare them with that exposed
coordinate. This does not decide whether the strict predicate ships: the
predicate is safe off-rig because a reading outside it produces a redundant
dispatch, not a skipped movement. It decides whether elision actually fixes the
observed Zeiss case.

If the Zeiss readback falls outside 0.001 um, do not widen the predicate. Keep
the truthful serial diagnosis and report that redundant-move suppression does
not solve this adapter. Do not borrow the arrival/response band and do not
derive a movement-accuracy number here.

With the stage in manual mode, repeat only the non-mutating readiness query if
one was found. Confirm that acquisition refuses before creating a dataset or
exposing a frame.

If no readable mode property exists, capture error 16 in manual mode and record
what the same failure looks like from an unrelated serial fault **only if one
can be produced without unplugging hardware on the stand**. Do not book a rig
trip to disconnect a Zeiss controller. Absent that second sample, the refusal
says it could not distinguish the causes — which is the honest report anyway.

## Decision 1: the operator declares which stage devices Microclaw may move

The general problem is not "this device is telemetry-only". It is that setup
demands bounds for every `StageDevice` it enumerates, including axes the
operator never wants Microclaw to touch. The mechanism that fixes the Zeiss
case fixes all of them and needs no knowledge of any adapter:

**During setup, the operator may exclude a whole stage device. Every axis on an
excluded device needs no bounds and cannot be moved.**

Exclusion is device-wide because the movement surfaces are device-wide. In
particular, an XY stage receives one coupled XY command; setup must never offer
independent X-only or Y-only exclusion. A single-axis `StageDevice` naturally
contributes one excluded axis. If per-axis control is ever needed for a device
whose adapter truly supports it, that requires a separate schema and movement
contract.

A wrong exclusion fails safe — the axis simply cannot be commanded — which is
why an operator declaration is sufficient authority here, and why no adapter
table is needed to reach it.

`stage_axes()` keeps returning every axis; `SetupDraft.status()` stops
requiring endpoints for every axis whose device is excluded:

```python
for axis in self.axes:
    if axis["device"] in self.excluded_devices:
        rows.append({**axis, "low": None, "high": None, "movable": False})
        continue
    endpoints = self.bounds.get(axis["id"], {})
    absent = [name for name in ("low", "high") if name not in endpoints]
    if absent:
        missing.append({"axis": axis["id"], "endpoints": absent})
```

Setup reaches that state through an explicit in-memory tool, not by asking the
model to edit draft internals:

```python
def exclude_stage_device(ctrl, guard, *, device: str) -> dict:
    draft = _state(ctrl)
    live_devices = {axis["device"] for axis in draft.axes}
    if device not in live_devices:
        raise ValueError(f"Unknown live stage device: {device}")

    removed_bounds = sorted(
        axis["id"] for axis in draft.axes
        if axis["device"] == device and draft.bounds.pop(axis["id"], None) is not None
    )
    draft.excluded_devices.add(device)
    return {
        "recorded_in_memory": True,
        "device": device,
        "excluded_axes": sorted(
            axis["id"] for axis in draft.axes if axis["device"] == device
        ),
        "removed_draft_bounds": removed_bounds,
        "message": (
            f"Excluded stage device '{device}' from Microclaw movement in the "
            "in-memory draft. It will remain readable."
        ),
    }
```

As with recording an endpoint, the setup agent first echoes the exact device
and all axes that will become unmovable and obtains explicit operator approval
before calling this tool. The final rendered security document remains subject
to the existing browser confirmation before it is written. Provide the inverse
`include_stage_device` draft tool so a mistaken exclusion can be corrected
before review; re-inclusion restores the requirement for fresh endpoints and
never resurrects bounds removed by exclusion.

Excluding either axis ID of an XY device is not an API option: the tool accepts
only the device label and reports both affected axes.

Three things the stub does not show and an implementation will not get for
free. `SetupDraft` needs the `excluded_devices` field. Both tools must be
registered in `setup_tools.py`'s dispatch dict *and* schema list *and* in
`SETUP_TOOL_NAMES` (`webserve.py:79`) — a setup session refuses any tool absent
from that allowlist, which is the "fixture cannot reach the code" failure this
repo keeps paying for, one layer out. And `record_proposed_stage_bound` must
refuse an endpoint for an already-excluded device: the draft is where the
contradictory document would be built, so it is where the contradiction is
cheapest to refuse. `review_security_config` names the excluded devices in its
summary, so a mistaken exclusion is visible at review rather than at the
microscope.

Adapter metadata may **suggest** the exclusion in the setup conversation ("this
device reports `Position` as read-only") but never decides it. Do not add
`KNOWN_TELEMETRY_STAGE_ADAPTERS` or any adapter-keyed capability table to
`microclaw/`: that is a rig fact, and this repo puts rig facts in gate docs and
rig profiles.

The rendered document must not place an excluded device under `named_stages`
with invented bounds. It gets its own section, so the operator reads what was
withheld as well as what was granted:

```yaml
unmovable_stages:
  - device: ZeissDefiniteFocusOffset
    note: telemetry only on this rig
```

Keep `schema_version: 3`. `safety.py:457` accepts exactly 3, so a bump
invalidates **every** installed config on every rig, for a section that changes
the meaning of nothing already written; absent means no exclusions. The work is
one name in the top-level allowed-keys set and one `object_list` call, which
already rejects unknown keys. This is not a compatibility shim being carried —
it is a new optional section, and optional is free.

The structural parse is not the whole validation. Populate an explicit field on
`ParsedSafetyConfig` and require every `unmovable_stages` item to have a
non-empty string `device` that occurs only once in the list. Reject duplicate
labels rather than deduplicating them. `_stage_constraints`
(`safety.py:341-350`) already does exactly these two checks for `named_stages`;
mirror it rather than inventing a second validation idiom.

`note` is optional free text for the operator reading the reviewed document, and
**no code path may interpret it**. The earlier draft required a fixed
`reason: operator_excluded`; a field with one legal value carries nothing the
section name does not already say, and its validator would exist only to check a
constant. Every entry in a reviewed config is operator-declared by construction —
this design forbids any other source for the classification.

The two declarations are mutually exclusive. Parsing/live validation refuses
a device that is both excluded and bounded; it must not silently choose the
more restrictive interpretation because the reviewed document is internally
contradictory. Apply the same rule to Core assignments: an excluded Core XY or
focus device cannot simultaneously receive `stage.x/y` or `stage.z` bounds.
For an XY device, any X or Y declaration counts as a bound on that device for
this conflict check.

One config must be re-authored, and not because of the schema: the Zeiss file
already carries the fabricated `ZeissDefiniteFocusOffset` range, and
`write_security_config` refuses to overwrite an existing file. The block's
runbook tells that operator to **rename** the existing reviewed config rather
than delete it, then re-author through setup with the device excluded. No other
rig is touched, and no startup diagnostic about required re-authoring is needed
because nothing else stops parsing.

Authorization-map construction accepts an intentionally excluded live device and
omits it from every movement capability, rather than refusing to build the map.
Two halves of that are already true and one is a trap.

Already true: a *named* excluded device never reaches the coverage check at all,
because `named_devices` is derived from `parsed_config.ranges`
(`authorization.py:865`), so a device with no declared range is not in
`reachable_axes` and cannot raise "has no declared range policy". The change is
needed only for a Core XY or focus device, which `reachable_axes` always
contains. Excluding one of those is permitted — the operator owns the session,
and a wrong exclusion fails safe — but it is drastic: no `go_to_position`, no
multi-position acquisition, or no autofocus, Z-stack or focus-recovery jog.
Setup echoes that consequence with the device name before recording it.

The trap: **omission alone makes an excluded device less protected than a
bounded one.** Raw property writes are refused for a stage device by
`bounded_stage = device in report.bounded_stage_devices`
(`authorization.py:1642`), and an excluded device is by definition not in that
set. A setup-written config declares no `property_authorization` or
`illumination` section, so `property_writes_unrestricted` is True — on the Zeiss
rig today, `ZeissDefiniteFocusOffset` is refused a raw `Position` write *because*
of its fabricated bounds, and removing them without more would permit it.
Exclusion must therefore feed the same raw-write refusal, under its own
disposition (`refused_unmovable_stage`) so the report says which rule fired.

Actual movement rejects independently of bounds at the live tool boundary. The
parser already rejects a contradictory `named_stages` entry; this runtime check
is defence in depth for constructed configs and future internal callers:

```python
if guard.is_unmovable(device):
    raise SafetyViolation(
        f"Stage '{device}' was excluded from Microclaw's control on this rig "
        "and cannot be moved. Change this in the security config."
    )
```

Dropped from the first draft: refusing startup when live metadata later reports
an excluded device as movable. The classification is now an operator statement,
not a measurement, so there is nothing to drift against — and a startup refusal
keyed to metadata this design has just declared non-authoritative would strand
a rig for no evidence.

## Decision 2: preflight controller mode only if the inventory finds a property

Conditional on §1. If the manual/remote diff finds a stable readable property,
describe it in the **safety config** (operator-reviewed, rig-specific), never in
`microclaw/`:

```yaml
stage_control_mode:
  device: XYStage
  property: <from the inventory diff>
  remote_values: [...]
  manual_values: [...]
```

Check it once per run, before the first dispatch — not per position, and never
per frame:

```python
def require_stage_remote(core, probe) -> None:
    value = str(core.get_property(probe.device, probe.property))
    if value in probe.manual_values:
        raise StageNotRemote(device=probe.device, property=probe.property,
                             observed=value)
    if value not in probe.remote_values:
        raise StageControlModeUnknown(device=probe.device,
                                      property=probe.property, observed=value)
```

Two limits the refusal text must not overstate. A preflight cannot stop an
operator reaching for the joystick mid-run, so this reduces wasted runs; it does
not guarantee the run. And a property read arrives on the adapter's cadence, not
the controller's (design/56): a read taken immediately after a mode change can
still report the previous state.

The refusal states that no motion and no exposure occurred. Do not switch the
controller into remote mode automatically; that changes who controls the stage.

If no reliable property exists — the likely outcome — **build nothing here**.
Keep a rig-specific reminder before the first automated movement of a session
and do not claim readiness was checked.

## Decision 3: skip a *stored* coordinate only under a strict identity predicate

The first draft of this decision elided any move whose residual was inside the
arrival tolerance, at the shared seam. That is wrong, and it is worth writing
down why, because the predicate looks right:

- the arrival band is `max(2.0 um, 0.1 x displacement)` and design/66 says in
  its title that it measures **response, not accuracy**. It is loose on purpose;
- so "inside the band" spans real moves. On M2, `center_feature` converges to
  **0.5 um** residuals at 0.127 um/px (design/67). Eliding sub-band moves at the
  seam turns every centring correction into a no-op and the loop never
  converges — a bigger defect than the one being fixed;
- `_stage_move_band` already computes this exact predicate as
  `arrival_unverifiable`, deliberately as a *flag*, not a skip. Reusing it with
  the opposite meaning would put two contradictory readings of one number in the
  same file.

The defect is narrower than the seam: Microclaw **generated** a command to a
coordinate it had stored and appeared already to occupy. So the skip belongs
to callers dispatching a stored coordinate, by explicit opt-in, and never to a
caller computing a correction:

| site | stored or computed | skips? |
| --- | --- | --- |
| `MicroscopeController.go_to_position` (via `set_xy`) | stored | yes |
| `_run_protocol_at` per-position XY | stored | yes |
| `move_stage_xy` | requested by the user/agent | no |
| `center_feature` (`tools.py:7236`) | computed correction | no |

There is no fifth site: `_build_acquisition_events` passes no XY, so the
acquisition engine never moves the stage and there is no position list to fix.
Naming the four sites is deliberate — a criterion written as "cover the
acquisition paths" is one that gets satisfied by the wrong route.

`skip_if_present` must use a strict identity predicate, never `_within_bands`,
`_stage_move_band`, configured arrival tolerance, or a percentage of
displacement. It must not be exact float equality either, and the reason is in
our own code rather than in the stage: **every coordinate an agent ever sees is
rounded** — `[round(v, 4) for v in ...]` at `controller.py:278-280`, 3 dp in
`achieved_um` — while `get_x_position()` returns the raw double. The incident's
"already-current XY coordinate" was a number Microclaw had rounded before
printing it. Exact equality would ship this skip dead, and its test would pass
only against a fake handing back bit-identical floats: the assumption-encoding
fake this repo keeps paying for.

The epsilon is therefore a **representation** epsilon covering Microclaw's own
three-decimal output rounding and nothing else. It is absolute: a coordinate
does not become more identical merely because it is farther from the origin.

```python
# One nanometre covers the maximum 0.0005 um error introduced by Microclaw's
# three-decimal XY output, with margin. Relative tolerance would grow to
# 0.0042 um at 42,000 um and 0.01 um at 100,000 um, so it is deliberately zero.
COORDINATE_IDENTITY_ABS_UM = 1e-3


def same_stationary_coordinate(start_um: float, target_um: float) -> bool:
    return (
        math.isfinite(start_um) and math.isfinite(target_um)
        and abs(start_um - target_um) <= COORDINATE_IDENTITY_ABS_UM
    )
```

No config field, no guard accessor, no per-device number. design/66 built
per-device tolerance discovery and deleted it — *"all of that machinery existed
to discover a number we do not need"* — and design/67 refused it a second time;
a configured identity epsilon would be the third attempt, and §3 says why no
middle value is needed: a difference outside this predicate produces a redundant
dispatch — today's behaviour — never a skipped movement. Widening buys only the
regime in which eliding is unsafe.

Fold the predicate into the existing dispatch-and-settle sequence, which those
sites already duplicate line for line, rather than adding a parallel helper:

```python
def dispatch_xy(ctrl, guard, x_um, y_um, *, skip_if_present: bool = False) -> dict:
    device = ctrl.core.get_xy_stage_device()
    configured_x = guard.stage_move_tolerance(device, core_axis="x")
    configured_y = guard.stage_move_tolerance(device, core_axis="y")
    start_x, start_y = read_xy_start_position(
        ctrl.core, device, x_um, y_um, "relative", configured_x, configured_y
    )
    if (skip_if_present
            and same_stationary_coordinate(start_x, x_um)
            and same_stationary_coordinate(start_y, y_um)):
        # design/68's record, unchanged in shape, plus the two fields that say
        # nothing was commanded. A second record shape here would reach history,
        # the typed errors and the exporter.
        return {**_xy_move_record(...), "dispatched": False,
                "verification_kind": "not_dispatched"}
    ...  # existing dispatch + settle_xy_move, unchanged
```

`skip_if_present` defaults to False so a site that forgets it keeps today's
behaviour, which is the safe direction.

This epsilon is not a move tolerance and must never be read from, or written
into, `move_tolerance_um`: one is about what the hardware achieved, the other
about whether two numbers are the same number.

The emitters must skip identically or the standalone script and the live run
diverge; both already inline `settle_xy_move` with `inspect.getsource`, so
keeping the skip inside this helper keeps them aligned. A capability is not
finished until it can appear in an exported script.

## Decision 4: diagnose serial errors without guessing, and stop the reissue

`xy_stage_move_dispatch_failure` already translates a driver refusal into a
measured contract — requested, measured, start, device status. Extend it; do not
add a second exception type beside it.

```python
except Exception as exc:
    if is_serial_invalid_response(exc):          # see naming note below
        mode = read_control_mode_if_available(core, probe)
        ...  # annotate the existing XYStageMoveError with control_mode + causes
```

When mode is observed as manual, state it as the cause. Otherwise say it is a
common cause and report that Microclaw could not distinguish it from other
serial or controller-state faults. Always report requested and measured
positions; the runner's exit report already carries frames exposed, so cite it
from there rather than threading a new argument to the seam.

**Naming.** `(16)` and that sentence look like Micro-Manager's generic
serial-invalid-response error, not Zeiss text. Confirm against the MMCore
constants before naming the predicate; a vendor-branded check on a generic
condition would mislabel every other serial device on every other rig.

**The retry to prevent is the agent's, not the code's.** Nothing in
`controller.py` or `tools.py` retries a stage move today, so there is no retry
loop to delete. What happened is that the model reissued the command after the
first failure. The control therefore goes where the model reads it: the refusal
message says a retry is only meaningful after the controller state has been
observed or corrected, and the movement tools' parameter descriptions say the
same — a statically-knowable rule belongs in the parameter description, not in
prose the model sees once.

## Blocks

Split by evidence cost, not by decision: the two that need no Zeiss go first,
and everything the Zeiss stand must answer rides one trip.

- **69b-1 — Decisions 3 and 4.** No Zeiss needed. Off-rig tests plus a demo or
  M2 run for the strict absolute skip and the emitted script. The safe predicate
  ships independently of adapter jitter; the Zeiss measurement decides only
  whether it suppresses the redundant command on that rig. Score the run by
  counting how many times the skip actually fired in the session history: a
  predicate that never fires on any rig is dead code, and the close-out says
  that rather than claiming the redundant command was eliminated.
- **69b-2 — Decision 1.** Setup exclusion, schema-3 optional-section extension,
  authorization refusal.
  Gated on the demo machine (a clean-profile setup run), not on the Zeiss.
- **69b-3 — Decision 2, and the Zeiss measurements.** One trip, one runbook:
  §1's manual/remote inventory diff, §3's parked readback, and the error-16
  samples all need the same operator at the same stand, and rig trips are the
  budget this workflow actually spends. If the diff finds no mode property, the
  Decision 2 half closes as "nothing to build" and the reminder ships with
  69b-1's refusal text. If the parked readback exceeds 0.001 um from Microclaw's
  exposed coordinate, record that Decision 3 does not fix this adapter and do not
  widen the predicate.

Neither 69b-1 nor 69b-2 waits on the Zeiss rig.

## Tests

- Every axis of an excluded device appears in reads but not in setup's missing
  bounds.
- `exclude_stage_device` rejects an unknown label, excludes every axis of an XY
  device together, reports and removes existing draft bounds, and does not
  write a file.
- `include_stage_device` restores missing-endpoint requirements without
  restoring removed bounds.
- The setup transcript echoes the device and affected axes before exclusion,
  and the final rendered document still requires write confirmation.
- The rendered configuration contains no `named_stages` range for it.
- Setup offers no independent X-only/Y-only exclusion for an XY stage.
- A config declaring one device as both bounded and unmovable is rejected,
  including conflicts through Core XY/focus assignments.
- An excluded device refuses motion independently of the missing bounds.
- A config carrying `unmovable_stages` round-trips; an unknown key in it is
  rejected the way every other list is.
- Empty and duplicate device labels are rejected; a `note` is optional and
  reaches no code path that reads it.
- Recording an endpoint for an already-excluded device refuses, and both new
  tools appear in `SETUP_TOOL_NAMES`.
- Live authorization accepts an excluded connected stage without a range,
  omits it from movement capabilities, and still exposes its read telemetry —
  asserted for an excluded Core focus device as well as a named one.
- **A raw property write to an excluded device is refused**, on a config with no
  `property_authorization` section. This is the regression the exclusion would
  otherwise introduce: today the same write is refused only because the device
  carries fabricated bounds.
- Observed manual mode refuses before movement, dataset creation, or exposure.
- Error 16 without a mode reading is not reported as proven manual mode, and its
  message tells the caller not to reissue without new evidence.
- A stored coordinate already occupied dispatches no hardware call, and its
  record keeps the design/68 field shape.
- A stored coordinate inside the response band but outside the strict identity
  epsilon still dispatches.
- A target differing from the reading only by Microclaw's own 3-4 dp rounding is
  treated as the same coordinate and skips; one differing by 0.01 um dispatches.
  A fake returning bit-identical floats does not exercise this.
- Identity tolerance does not grow with coordinate magnitude: the same absolute
  delta produces the same decision near zero, 42,000 um, and 100,000 um.
- **A 1 um deliberate correction still dispatches** — through `move_stage_xy`
  and through `center_feature`. This is the control that fires; without it the
  skip's tests cannot fail.
- `go_to_position`, the tile path, `move_stage_xy` and `center_feature` each
  assert their own skip/no-skip behaviour; a shared fixture that reaches only
  one of them is not coverage of the other three.
- The emitted script skips exactly where the live run skipped.
- Inventory enumeration works against a fake whose collections expose
  `size()`/`get(i)` and whose `__iter__` raises.

## Out of scope

- Automatically changing the Zeiss controller between manual and remote mode.
- Treating focus-lock telemetry as a substitute for the physical focus axis.
- Changing operator-selected physical travel limits for movable stages.
- General serial-port recovery or reconnect behavior unrelated to stage
  capability and control mode.
- General discovery of stage accuracy, repeatability, or step size. §3's parked
  Zeiss readback exists only to decide whether the strict elision is effective
  on that adapter; it is not a movement-accuracy calibration and produces no
  configured number.
