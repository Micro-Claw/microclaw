# In-app minimal safety setup

## Problem

First launch currently stops before the web app, then `install.bat` runs a long
terminal interview which produces an unreviewed, comprehensive safety profile.
That makes optional policy feel mandatory and asks novices to classify hardware
properties before they can use Microclaw.

Only unknown stage travel can physically damage the microscope. The required
baseline should therefore be:

- reviewed bounds for every reachable XY, focus, and named stage axis; and
- confirmation prompts above a user-selected frame count and estimated duration,
  initially suggested as 500 frames and 20 minutes (1200 seconds).

Exposure, illumination, byte, dose, plugin, channel, property-authorization,
workspace, and analysis limits remain available, but are opt-in and absent by
default. Sample bleaching remains addressed by the agent instructions, not by a
mandatory startup limit.

## Decision

### Start the app in a restricted setup state

`microclaw serve` should no longer exit when the default safety config is
missing. It should connect, enumerate stage devices read-only, open the normal
web UI, and give the agent only setup/read tools. The first assistant message is:

> Security bounds are not set. Before Microclaw can control hardware, we need to
> record safe travel bounds for every stage and choose large-acquisition warning
> thresholds. I can guide you through it; acquisition and hardware-write tools
> stay unavailable until setup is complete and Microclaw is restarted.

This is a distinct bootstrap capability set, not an unguarded `SafetyGuard`.
Normal tools are never constructed or dispatched in this process. Safe setup
tools may enumerate stage identities and read positions. To avoid moving a stage
before its limits are known, Microclaw asks the operator to move each axis in
Micro-Manager to the safe low and high endpoints, reads each position, echoes
the proposed range, and asks the operator to approve it. “Hardware limit” and
“safe limit” are explicitly distinguished; the operator chooses the latter.

The setup is complete only when both finite bounds exist for every stage axis
reported by the live inventory. No `unbounded` escape is offered in the default
flow. A stage which cannot be bounded keeps normal hardware tools locked.

Sketch:

```python
# webserve.py
class SessionMode(Enum):
    SETUP = "setup"
    NORMAL = "normal"

def build_session(args):
    result = try_load_minimal_security_config(args.safety_config)
    if result.missing:
        return SetupSession(
            port=args.port,
            may_write_config=args.setup_write_security_config,
            proposed_confirm_frames=500,
            proposed_confirm_duration_s=20 * 60,
        )
    return Session.from_security_config(args, result.parsed)

class SetupSession:          # no SafetyGuard; MicroscopeController(port) with guard=None
    # Fold, don't reinvent: rig_inventory.enumerate_rig already does the
    # read-only device/stage sweep (inspect_rig already connects unguarded).
    # Reuse first_launch's finite-bound validation, proposal/echo language, and
    # validate-before-atomic-publish patterns, but not its functions unchanged:
    # _bounds accepts typed or driver-range values rather than capturing live
    # positions, and write_profile forces reviewed:false and permits replacement.
    tools = ("list_stage_axes", "read_stage_positions", "record_proposed_stage_bound",
             "set_proposed_acquisition_prompts", "review_security_config",
             "write_security_config")
```

`record_*` calls change only an in-memory draft. They do not write hardware or
disk. The UI should show a persistent “Setup mode — hardware control locked”
banner and a checklist of discovered axes, captured endpoints, and thresholds.

Splitting `Session` is not sufficient. `run_agent_iter` takes a `SafetyGuard`,
sends the module-global `TOOLS_CACHED`, and dispatches through
`tools.TOOL_REGISTRY`. Both the schemas sent to the model and the registry used
by `execute_tool` must therefore be session-scoped parameters. Filtering only
`TOOLS_CACHED` is not a security boundary: a fabricated normal-tool call must be
rejected by the setup dispatcher before registry lookup. A guardless controller
is acceptable only under this enforced read-only/setup registry, and tests must
prove every normal hardware tool is unreachable. `serve`'s exit path also calls
`report_declared_illumination_on_exit(session.guard, session.ctrl.core)`, which
must tolerate a guardless session.

### Make the required schema minimal

Replace the current all-or-nothing startup requirements. The normal loader must
require `reviewed: true`, complete live-matched stage bounds, and the two
confirmation thresholds. All other sections and values are optional. Omitted
policy means no extra Microclaw restriction; it must not accidentally retain a
legacy default.

```yaml
schema_version: 3
reviewed: true
stage:
  x_min: -12000.0
  x_max: 12000.0
  y_min: -8000.0
  y_max: 8000.0
  z_min: 100.0
  z_max: 7800.0
named_stages:
  - device: PiezoZ
    min_um: 0.0
    max_um: 200.0
acquisition:
  confirm_above_frames: 500
  confirm_above_duration_s: 1200
```

The actual file includes only axes present in the inventory (for example, no
XY keys on a focus-only system). Existing optional fields continue to parse;
new configurations do not receive them. Hard acquisition maxima are removed
from the required path. Confirmation triggers when either estimate is at or
above its threshold — deliberately reversing `_authorize_acquisition`'s current
strict `>`, so a 500-frame plan asks — and approval then runs uncapped.

```python
# safety.py / acquisition.py
def confirmation_reasons(plan, policy):
    reasons = []
    if plan.frames >= policy.confirm_above_frames:
        reasons.append(f"{plan.frames} frames")
    if plan.duration_s >= policy.confirm_above_duration_s:
        reasons.append(f"about {format_duration(plan.duration_s)}")
    return reasons

if reasons := confirmation_reasons(plan, guard.acquisition):
    confirm_or_raise(
        "This acquisition will take " + " and ".join(reasons)
        + ". It may use substantial disk space or time. Continue?",
        kind="large-acquisition",
    )
# Once confirmed, run it; max_frames/max_duration_s are optional opt-in caps only.
```

### Permit one narrowly scoped write

Add the top-level flag `--setup-write-security-config`. It is valid only with
`serve`, only on a loopback bind, and only when the target is the per-user
default path. It does not grant arbitrary file access and is not exposed to a
normal session. The writer serializes a freshly constructed schema-3 mapping;
it never accepts YAML or a path from the model.

Immediately before writing, the browser presents the exact destination and
rendered YAML in the existing confirmation UI. Approval is single-use and
audited. The write uses a same-directory temporary file plus atomic replace,
refuses to overwrite an existing file, and then disables itself. Microclaw tells
the user to close it and restart from the desktop shortcut. Normal hardware
tools are not unlocked in place.

```python
def write_security_config(draft, *, capability, confirm):
    capability.require_unused()
    target = default_safety_config().resolve()
    document = build_schema_3_document(draft, reviewed=True)
    validate_minimal_document(document, live_axes=draft.live_axes)
    confirm(f"Write this reviewed security config to {target}?\n\n{dump(document)}")
    atomic_create_new(target, dump(document, sort_keys=False))
    capability.consume()
    return {"restart_required": True, "path": str(target)}
```

`security_config.yaml` is the requested public name, but a filename migration
buys no safety or usability: keep `paths.default_safety_config()` pointing at
the existing `safety_config.yaml` and rename only user-facing language, from
“safety profile” to “security bounds.”

### Simplify installation

`install.bat` should install the package and normal desktop shortcut as it does
today, ask the user to enable the Micro-Manager bridge, then launch:

```bat
"%MC_EXE%" --setup-write-security-config serve
```

The browser, not the batch file, owns setup. Remove the `init --yes` call, the
`check-config`/editor review steps it prints, and the interview it leads to.
On upgrade, preserve an existing valid config and do not start setup. If an
existing config is invalid, launch setup mode but refuse to overwrite it; show
its path and require the user to move or repair it deliberately.

The desktop shortcut always remains plain `microclaw serve`, so write authority
does not survive installation or restart. If the one-time setup server is
closed early, the user can explicitly rerun the command printed by the
installer; a later ordinary shortcut launch opens read-only setup mode and
explains that writing requires that command.

### Run the rig-knowledge interview after restart

The knowledge-base rig interview is required because its five topics make
Microclaw useful on the operator's particular microscope; they are not security
bounds and should not complicate the security interview. Keep
`RIG_INTERVIEW_PROMPT`, but sequence it after the minimal security config has
been written and Microclaw has restarted into a normal session.

The setup-only session must not inject `RIG_INTERVIEW_PROMPT`. On the first
normal session, `_system_blocks()` should continue to detect missing rig topics,
ask about them in the first reply, and save each confirmed answer as it does
today. The interview remains mandatory in the sense that Microclaw continues to
ask until every topic is stored; its existing rule not to block an operator's
requested task remains appropriate. Security completion is therefore the gate
for normal hardware tools, while rig-profile completion is a required guided
onboarding sequence that may coexist with useful work.

The restart message should set this expectation explicitly: “Security bounds
are saved. Restart Microclaw; it will next learn the essential details of your
microscope before helping with your workflows.”

## Conflicts resolved in favor of this design

- `config.py`, `webserve.Session`, and `run_session` currently fail closed before
  the app exists when a reviewed config is missing. `serve` changes to restricted
  in-app setup; the non-web CLI may continue to refuse and point to `serve`.
- `first_launch.py`, `init`, `first-launch-setup`, README, and `install.bat`
  require a separate comprehensive interview and manual editor review. Deprecate
  then remove that path; do not preserve it merely for compatibility.
- Schema 2 guaranteed mode requires acquisition hard caps, exposure, property
  authorization, channels, illumination declarations, and other completeness
  checks. Schema 3 makes those opt-in. Live validation guarantees complete stage
  coverage only. This intentionally drops the broader “every actuator is typed
  or excluded” claim.
- `SafetyGuard` and the example currently describe illumination as physically
  hazardous and require enable/power confirmation and dose caps. Per the product
  directive, these become optional policies and do not block first launch.
- The current example suggests a five-minute duration confirmation. The new
  in-app suggestion is 20 minutes (1200 seconds); the frame suggestion remains
  500.
- The knowledge-base rig interview (`RIG_INTERVIEW_PROMPT`) asks about five
  essential, non-security rig topics. Keep it mandatory, but do not inject it
  into the restricted security-setup session. It begins on the first normal
  session after the required restart and continues until every topic is stored.

This supports rather than conflicts with `CLAUDE.md`'s generic-rig,
ease-of-use, user-owned-session, and standalone-script constraints, and its
no-legacy-anchoring rule favours replacing schema 2 over a compatibility layer.
Existing reviewed schema-2 files should remain loadable
only if that is nearly free; otherwise provide a one-shot offline migration and
make schema 3 the sole live format.

## Implementation slices and evidence

1. Introduce schema 3 and reduce runtime enforcement to stage bounds plus the
   two confirmation thresholds; retain optional guards when explicitly present.
2. Split normal and setup session construction before any mutation dispatcher is
   built. Test that every normal hardware tool is unreachable in setup mode.
3. Add stage discovery/position-read/draft tools and the in-app setup prompt/UI.
   Test XY, focus-only, named-stage, multiple-stage, and inventory-change cases.
4. Add the single-use loopback-only writer and approval audit. Test path
   confinement, create-new behavior, malformed drafts, denial, replay, remote
   bind, and restart-required behavior.
5. Simplify `install.bat`, shortcut behavior, README, example config, and tests;
   retire the terminal security interview while retaining the automatic
   rig-knowledge interview after restart. Test that setup mode omits it, the
   first normal session includes it, and stored topics are not asked again.

The key acceptance test is end-to-end on a clean Windows user profile: double
click `install.bat`, open Micro-Manager, complete the conversation in the web
app, approve the exact minimal YAML, restart from the normal shortcut, and prove
that an out-of-bounds move is refused while a 500-frame or 20-minute acquisition
asks once and proceeds when approved.
