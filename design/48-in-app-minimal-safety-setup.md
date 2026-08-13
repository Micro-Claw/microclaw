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

---

# Implementation checklist — design/48

This checklist is **separate from `design/35-usability-and-pfs-checklist.md`** and
does not fold into it. Blocks run under the ten-step block workflow in
`CLAUDE.md` §"The block workflow", which is authoritative; where this file
disagrees with those steps, `CLAUDE.md` wins.

Blocks are the design's five slices, in order. Each is a branch, an implementer
in its own worktree, a rig-gate runbook committed **on that branch**, and a
step-10 design gate before the next block is assigned.

## Decision taken before block 48a (2026-08-13)

**Schema 3 is the sole live format. There is no schema-2 compatibility and no
migration command.** M5, M2 and the Nikon each hold a reviewed schema-2
`safety_config.yaml`; each will author a schema-3 file once, which is also the
cheapest way to exercise the new setup flow on three different rigs. Per
`CLAUDE.md`'s no-legacy-anchoring rule the old parser is replaced, not kept
beside the new one. Every gate runbook from 48a onward must open by telling the
operator to **rename** the existing file, never delete it — it is the only
written record of that rig's reviewed bounds until the new one exists.

## Blocks

### 48a — Schema 3 and the minimal required document

Design: "Make the required schema minimal", "Conflicts resolved…" bullets 3–5.

- `ParsedSafetyConfig.from_yaml` accepts `schema_version: 3` only; a schema-2
  file refuses with a message that names in-app setup, not `microclaw init`.
- Required: `reviewed: true`, stage bounds for every axis the document declares,
  `acquisition.confirm_above_frames`, `acquisition.confirm_above_duration_s`.
- Every other section optional, and **absent means unrestricted** — an omitted
  section must not inherit a schema-2 default. This is the defect most likely to
  hide here.
- `_authorize_acquisition`: confirmation triggers at `>=`, not `>`, so a plan at
  exactly 500 frames asks. `max_frames`/`max_duration_s` become opt-in caps and
  no longer participate in the required path.
- `config.validate_safety_config`: the guaranteed-mode required-field block goes
  with schema 2; keep the example-limits warning and the live-check note.
- Rewrite `safety_config.example.yaml` to the minimal schema-3 document.

Evidence: `tests/test_safety.py`, `test_config_gate.py`,
`test_acquisition_budgets.py`, `test_schema_parity.py`. Named cases — schema-2
refusal text; minimal document starts and enforces stage bounds; omitted
`illumination`/`camera`/`channels` restrict nothing; 499/500/501-frame
confirmation boundary; a focus-only document with no XY keys.

Rig gate 48a (M5): hand-author the minimal schema-3 file, start `microclaw
serve`, prove (i) an out-of-bounds XY move refuses, (ii) a 500-frame timelapse
asks once and runs on approval, (iii) a laser the old config typed still fires
with no `illumination` section present.

Step-10 design gate: reconcile the "every actuator is typed or excluded" claim,
which schema 3 intentionally drops. **Done 2026-08-13**: the claim lives in
`design/33-authorization-map.md`, not in design/14 or 17 as the checklist
guessed, and now carries an amendment note at the top. Also retired the
`guaranteed_mode` / `degraded_mode` diagnostic kinds, which no longer have a
producer, and `check-config`'s help text that advertised them.

### 48b — Session split and the setup dispatcher

Design: "Start the app in a restricted setup state", paragraph beginning
"Splitting `Session` is not sufficient".

- `SessionMode`, `SetupSession`, `build_session`; `serve` stops exiting when the
  config is missing. The non-web CLI keeps refusing and points at `serve`.
- **Session-scoped tools, both halves**: `run_agent_iter` takes the schema list
  instead of reaching for module-global `TOOLS_CACHED`, and `execute_tool` takes
  the registry instead of reaching for `tools.TOOL_REGISTRY`.
- The setup dispatcher rejects any name outside the setup set **before** registry
  lookup. Filtering the schemas sent to the model is not the boundary.
- `MicroscopeController(port, guard=None)` is acceptable only under that
  dispatcher. `report_declared_illumination_on_exit` must tolerate `guard=None`.
- Setup tools do **not** enter `TOOL_REGISTRY`; nothing in this block is
  exportable. If any setup tool does land in `TOOL_REGISTRY`, it must carry
  `@emits_nothing` — an undecorated tool plants a `RuntimeError` in every
  exported script that records it (`CLAUDE.md`, two gates lost to this).

Evidence: a test that enumerates `TOOL_REGISTRY` and asserts every hardware tool
is unreachable in setup mode, including a **fabricated** `tool_use` naming
`move_stage_xy`; a test that the setup turn never sends `TOOLS_CACHED`; a test
that exit reporting works with a guardless session.

Rig gate 48b (M5): move the config aside, launch, ask the agent in plain English
to move the stage and snap an image. Nothing moves, nothing exposes, and the
refusal names setup mode.

**Landed 2026-08-13 (`e17fe60`), M5 PASS.** Two coordinator corrections before
merge: the session's dispatch attributes are read directly rather than through
`getattr` defaults that fell back to the full hardware registry, and a session
offering no tools omits the `tools` parameter instead of sending `[]` (whether
the API accepts an empty array is undocumented and untested here). The setup
tool *names* are wired and the registry is empty — 48c fills it. This entry
originally said the fabricated call should name `move_stage`, which is not a
tool; corrected above, and the implementation and runbook use `move_stage_xy`.

### 48c — Setup tools, first message, and setup UI

Design: "Start the app in a restricted setup state" (tool list, endpoint
capture), "Run the rig-knowledge interview after restart" (first half).

- `list_stage_axes`, `read_stage_positions`, `record_proposed_stage_bound`,
  `set_proposed_acquisition_prompts`, `review_security_config` — built over
  `rig_inventory.enumerate_rig`, which already does the read-only sweep. Fold
  into it; do not write a second enumerator.
- Reuse `first_launch`'s finite-bound validation and proposal/echo language
  without reusing its functions unchanged (design sketch says why).
- Operator drives each axis in Micro-Manager; microclaw reads positions, echoes
  the proposed range, and asks for approval. "Hardware limit" and "safe limit"
  are named distinctly. No `unbounded` escape.
- `record_*` mutates an in-memory draft only — no hardware write, no disk.
- Setup mode must **not** inject `RIG_INTERVIEW_PROMPT`.
- serve.html: persistent "Setup mode — hardware control locked" banner plus a
  checklist of discovered axes, captured endpoints, and thresholds.

Evidence: XY, focus-only, named-stage, several-stages, and inventory-changed
cases; a test that no `record_*` call touches disk or the core; a test that the
setup system blocks omit the rig interview.

Rig gate 48c (M5): complete the whole endpoint conversation for XY, Z and the
piezo. Every axis the live inventory reports appears in the checklist, and the
proposed ranges match what the operator drove to.

**Landed 2026-08-13 (`e54e610`), M5 PASS.** The live sweep found six axes —
core XY on **`SmarAct 2D`** (not the `XY` label the gate guessed), core Z on
`PIZStage`, and three named stages — and all twelve endpoints were captured, with
review reporting complete and `written_to_disk: false`. Asking the model in the
browser to write the config produced no tool call, no file, and no claimed path.
The draft the writer inherits therefore has this shape: axis ids carry device
identity (`SmarAct 2D.x`, `PIZStage.z`, or the bare named-stage label) and a
`role` of `core_xy` / `core_focus` / `named_stage` / `named_xy_stage`, which is
what 48d must map onto `stage.{x,y,z}_{min,max}` and `named_stages[]`.

### 48d — The single-use writer

Design: "Permit one narrowly scoped write".

- Top-level `--setup-write-security-config`, valid only with `serve`, only on a
  loopback bind, only for `paths.default_safety_config()`. Never accepts YAML or
  a path from the model.
- Browser confirmation shows the exact destination and the rendered YAML before
  the write. Approval is single-use and audited.
- Same-directory temp file + atomic replace; refuses to overwrite an existing
  file; capability consumed afterwards; returns `restart_required`.
- Restart message: "Security bounds are saved. Restart Microclaw; it will next
  learn the essential details of your microscope before helping with your
  workflows."
- Keep `paths.default_safety_config()` on `safety_config.yaml`. Rename only
  user-facing language: "safety profile" → "security bounds".

Evidence: path confinement, remote-bind refusal, replay after consume, malformed
draft, operator denial, existing-file refusal, restart_required, audit record.

Rig gate 48d (M5): approve the write, confirm the file at
`%APPDATA%\microclaw\safety_config.yaml` matches the YAML shown, restart from the
ordinary desktop shortcut, and confirm the normal session loads it and offers no
write capability.

### 48e — Installer, shortcut, docs, and the interview after restart

Design: "Simplify installation", "Run the rig-knowledge interview after restart".

- `install.bat`: install, shortcut, bridge enable, then
  `"%MC_EXE%" --setup-write-security-config serve`. Remove the `init --yes` call
  and the `check-config`/editor review steps it prints.
- Upgrade preserves a valid config and does not start setup. An invalid config
  launches setup mode that **refuses to overwrite it**, shows its path, and asks
  the user to move or repair it.
- Desktop shortcut stays plain `microclaw serve`. The installer prints the
  one-time setup command for a user who closes the setup server early.
- Retire the terminal security interview: `first_launch.interview`, `init`,
  `first-launch-setup`. Delete rather than deprecate — keep only what 48c reuses.
- README, packaged example, `tests/test_installer.py`, `test_first_launch.py`,
  `test_init.py`.
- Rig interview: unchanged in `_system_blocks()`, absent in setup mode, present
  in the first normal session, and stored topics are not asked again.

Evidence: installer tests over the batch text; a test that setup-mode system
blocks omit the interview while normal ones include it.

Rig gate 48e — **the acceptance test**, on a clean Windows user profile:
double-click `install.bat`, open Micro-Manager, complete the conversation in the
browser, approve the exact minimal YAML, restart from the shortcut, and prove an
out-of-bounds move refuses while a 500-frame or 20-minute acquisition asks once
and proceeds on approval. Then the rig interview starts on its own.

## Run ledger

| Block | Branch | Start commit | Implementer | Rig gate | Merged |
|---|---|---|---|---|---|
| coordination | `design48/checklist` | `f7beac1` | coordinator | n/a | — |
| 48a | ~~`design48/block-48a`~~ | `9176dfd` | 2 review rounds | M5 PASS 2026-08-13 | `959dade` |
| 48b | ~~`design48/block-48b`~~ | `f6e7300` | 1 round + clarification | M5 PASS 2026-08-13 | `e17fe60` |
| 48c | ~~`design48/block-48c`~~ | `e17fe60` | 1 round | M5 PASS 2026-08-13 | `e54e610` |
| 48d | `design48/block-48d` | `e54e610` | 1 round, tip pushed | awaiting M5 | — |
| 48c | — | — | — | — | — |
| 48d | — | — | — | — | — |
| 48e | — | — | — | — | — |

## What the 48a M5 gate measured (2026-08-13)

Evidence: `~/Documents/Documents - Beyonce/Projects/Micro-Claw/48a-m5`.

- pytest on M5: **1828 passed, 116 skipped**. macOS on the same commit was 1845
  / 99, and 1828 + 116 = 1845 + 99 = 1944 — the same collection, 17 tests
  skipped on Windows. Nothing was lost.
- The minimal document M5 authored declares XY, Z, and **three** named stages
  (SmarAct 1D, Thorlabs ELL17/ELL20, Thorlabs ELL20), with no `camera`,
  `channels`, `illumination`, `plugins`, or `property_authorization` section.
- Out-of-bounds XY refused at x=5001 against x_max=5000; no motion.
- The raw-write route was refused for `PIZStage.Position` at an **in-range**
  value — the protection is about the route, and it held.
- The confirmation fired at exactly 500 frames, was approved once, and 500
  frames were written to `D:\SSD\48a-test\block48a-500-frames_1`.
- With no `illumination` section, the EMU laser enable went through with no
  Microclaw refusal and no enable confirmation, which is the intended schema-3
  behaviour. (The first write returned M5's known serial timeout; the retry
  succeeded.)

Two defects the gate exposed, both fixed on the branch:

1. **The bounds refusal named `move_stage` and `set_focus`, neither of which is
   a tool.** The model had to guess its way to `move_stage_xy`. The tests
   asserted the same wrong names by substring, so they passed; they now check
   every tool name the message prints against `TOOL_REGISTRY`. **A refusal that
   names the guarded route must name a route that exists** — worth a check in
   any future block that writes one.
2. **Fenced code blocks did not render in the web UI** (fixed on
   `fix/transcript-code-blocks`, separate from this block). `md()` in
   `transcript.js` supported only inline `` `code` ``, and on a ``` fence the
   inline rule matched from the third backtick to the first of the closing
   fence — so every verbatim tool result the model quoted back collapsed onto
   one line with stray backticks beside it. This is pre-existing, not schema-3
   fallout, but it is squarely a design/48 usability defect.

## Carried forward, not this track's work

- Twelve tools in `TOOL_REGISTRY` remain undecorated for export (measured
  2026-08-12). Tracked in design/35's carried-forward register; 48b only has to
  avoid adding a thirteenth.
- The three rigs' existing schema-2 files. Each gate runbook renames rather than
  deletes; no automated migration is planned.
- `first_launch.py`'s device-classification interview encodes real rig knowledge.
  48e deletes the security interview, not the understanding — anything worth
  keeping moves into a design note before the delete lands.
