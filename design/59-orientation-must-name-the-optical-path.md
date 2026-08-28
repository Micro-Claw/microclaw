# Orientation must name the optical path

`get_system_state` reports where the stage is, what the camera is called, and
what the shutter and lasers are doing. It says nothing about **what carries
light between the sample and that camera**: which objective is in the path,
where the light is being sent, and what focusing hardware exists. Those three
facts decide whether an image is possible at all, and today a session discovers
them by asking the operator or by guessing from device names.

## Problem — the Nikon session of 2026-08-23

Evidence: `~/Documents/Documents - Beyonce/Projects/Micro-Claw/pfs-nikon-design56-4/`,
`20260823_095336_954938_microclaw_history.jsonl` (56 messages). Nikon Ti, 60×
oil, Andor iXon, brightfield. Operator's opening request: *"I have a sample on
this microscope in brightfield mode. Can you find the focus?"*

The session ended correctly — PFS locked at ~2671 µm on a sharp brightfield
field. It took 56 messages, and **three of the four things that went wrong were
facts about the optical path that orientation did not report.**

### Gap 1 — the light path: 50 messages and three blank frames

Message 2, the whole of `get_system_state`:

```json
{"x_um": 0.4, "y_um": -0.1, "z_um": 968.425,
 "named_stages": {"TIPFSOffset": 150.4, "TITIRF": 0.0, "KDC101_27274173": 50000.4725},
 "exposure_ms": 10, "live_view": false,
 "shutter": "no shutter device configured",
 "lasers": "unknown — this rig has no EMU laser map…",
 "camera": {"label": "AndorIxon", "adapter": "Andor"}}
```

`camera` names the destination. Nothing names the route to it. Three
`snap_and_analyze` calls came back at SNR 2.5, 2.47 and 2.46 — the camera noise
floor, three times, at two XY positions 7 mm apart. The agent reasoned about it
well (message 47: *"two fields ~7 mm apart both blank … points strongly at
illumination, not sample location"*) but could not name the mechanism, because
it had never read a light-path device. The operator resolved it at message 48:

> "You were right, it was set to eye piece not camera"

**And then the honest part.** The agent read `TILightPath` at message 52 and got:

```json
{"current_value": "2-Left100",
 "allowed_values": ["1-Eye100", "2-Left100", "3-Right100", "4-Left80"]}
```

`2-Left100` is a **camera** port. The motorized light path had been correct the
entire time; the fault was a manual prism the software cannot see. So reading
`TILightPath` at message 2 would **not** have found this fault.

That is the finding, not an argument against the change. What reading it at
message 2 buys is: the four port names in front of the agent from the first
turn, the software route excluded in one call instead of fifty, and the
question *"is the physical prism at the camera port?"* asked before the first
exposure rather than after the third blank one. `1-Eye100` sitting next to
`2-Left100` in an `allowed_values` list is self-explanatory to any reader; no
Nikon knowledge is required, only the read.

### Gap 2 — the objective: asked of the operator, then contradicted

Message 5 the agent asked *"What objective/magnification is on?"* and noted
*"The stored affine says `default`"*. It was told 60× and used that number for
the rest of the session — it set the PFS probe step to 8 µm from the ~10 µm oil
capture range, which is objective-dependent arithmetic.

Two things were wrong underneath that answer, and neither surfaced:

- `_current_objective` (`tools.py:4148`) returns the literal string `"default"`
  when `get_current_pixel_size_config()` is empty. **No pixel-size config was
  active on this rig**, which is a diagnosis; `"default"` reads like an
  objective name. The agent relayed it as one.
- When the agent finally read the turret at message 23, `TINosePiece.Label` was
  `"4-Unknown"` — position 4, unnamed in the config. It correctly flagged this
  as a possible cause of the PFS failure (message 25) and drove a whole branch
  of the session on it. The real cause was the search ceiling, disclosed by the
  operator two messages later.

### Gap 3 — the focusing system: the operator had to ask for it

The agent's first plan was an image-based sweep, on a field it had just
measured at SNR 2.5. Message 6:

> "Can you use the Nikon PFS system on here? Why did you not propose using this?"

The agent's answer at message 7 is exact: *"the state shows `TIPFSOffset` at
150.4, so PFS hardware is present."* It inferred the focus lock from a **named
stage** — and `TIPFSOffset` is in that payload only because this rig's
`safety_config.yaml` happens to declare it as a bounded named stage. On a rig
that declares no named stages, nothing in message 2 would have mentioned PFS at
all.

**The one-call answer already exists and was not called.** `get_focus_lock_state`
(block 56b, `tools.py:8303`) returns the autofocus device from
`core.get_auto_focus_device()`, whether continuous focus is engaged, every
read-only property on the lock device with its live value, and a `probe_hint`
naming `run_autofocus`'s zero-exposure probe. On this rig it would have
returned `TIPFSStatus`, `engaged: false`, `Status: "Out of focus search range"`
and the probe recipe — at message 2, unprompted. The capability is built, tested
and generic. It is simply not on the path a session takes to orient itself.

## Why the knowledge base is the wrong home for these

`knowledge_manager.RIG_TOPICS` already lists `illumination_path`, and the agent
closed the session offering to store rig facts. That is the wrong instrument for
all three:

- **These are live state.** The light path moves between eyepiece and camera
  several times in an ordinary session. The turret moves. A stored answer is a
  statement about a previous session, and `SYSTEM_PROMPT` already tells the
  agent — correctly — *"Never rely solely on the knowledge base for device
  state."* A KB entry that must be re-verified on every read is a pointer, not
  an answer.
- **The KB is keyed to nothing.** `rig/illumination_path` is one free-text value
  per installation. It cannot say which of four ports is live now.
- **It cannot be authored before the first session.** The operator's first
  session is exactly the one that needs the orientation, and the KB is empty.

And the prompt is the wrong home too. `SYSTEM_PROMPT` carries a `Nikon rigs:`
section (`agent.py:381–410`) that names `TIPFS` hardware and gives real,
hard-won PFS procedure. It works — and it is also the reason the light path got
nothing: `TILightPath` had no paragraph, so nothing prompted the read. Answering
this by adding a `TILightPath` paragraph would anchor harder on one microscope
and leave the next rig's port device just as invisible. CLAUDE.md forbids it and
is right to.

## Decision

### §1 — `get_system_state` reports one `optical_path` block

Three new keys, always present, on the same rule as `shutter` and `lasers`:
present even as `"unknown"`, because *an omitted key is an invitation*
(`tools.py:2901`).

```json
"optical_path": {
  "discrete_positions": [
    {"device": "TINosePiece",        "label": "4-Unknown",
     "allowed": ["1-Unknown","2-Unknown","3-Unknown","4-Unknown","5-Unknown","6-Unknown"],
     "role": "pixel-size-config dependency (not proof of objective)"},
    {"device": "TILightPath",        "label": "2-Left100",
     "allowed": ["1-Eye100","2-Left100","3-Right100","4-Left80"],
     "role": "light-path candidate"},
    {"device": "TIFilterBlock1",     "label": "3-GFP",  "allowed": [...]},
    {"device": "TICondenserCassette","label": "1-BF",   "allowed": [...]}
  ],
  "hint": "A discrete-position device whose labels name ports routes light to
           the camera or the eyepiece. Micro-Manager sees only the motorized
           part of the path; a manual prism or slider can send light elsewhere
           with every value above unchanged. Call
           get_optical_path_documentation before interpreting these."
},
"objective": {
  "pixel_size_config": null,
  "pixel_size_um": 0.0,
  "available_configs": [
    {"config": "Res60x", "dependencies": [{"device": "TINosePiece", "property": "Label",
      "expected": "4-60xOil", "live": "4-Unknown", "matches": false}]}
  ],
  "reason": "No pixel-size configuration is active: Micro-Manager does not know
             which objective is in the path, so neither does microclaw. The
             available Res60x calibration depends on TINosePiece.Label, whose
             live value 4-Unknown does not match; that dependency does not by
             itself prove the device is an objective turret. Ask the operator
             which objective is seated; do not report one."
},
"focus": { … get_focus_lock_state's payload, verbatim … }
```

The three fields are one idea seen from three sides, and each is derived
**structurally** — no device is identified by its name. Structural discovery can
say which devices a pixel-size calibration depends on; it cannot, by itself, say
that any one of those devices is an objective turret.

### §2 — Where each fact comes from, generically

**`optical_path.discrete_positions`** comes from a new read-only StateDevice
inventory built while `validate_live_rig` already enumerates loaded devices and
calls `get_device_type`. It is deliberately **not**
`authorization._auto_classified_state_pairs`: that set is a write-authorization
result and omits devices on which the operator has already ruled, as well as
reviewed illumination devices. Reusing it here would make declared turrets and
filter wheels disappear from orientation. The inventory contains every loaded
device whose MM type is `StateDevice`, except the device selected as
`Core.Shutter`, whose state is already reported by `shutter`. A StateDevice also
declared as illumination remains visible here; duplicate visibility is better
than silently removing an optical element. **The inventory must not inherit
that function's fail-closed-to-empty behaviour**: `_auto_classified_state_pairs`
returns the empty set outright when `get_shutter_device()` raises, which is
correct for a write gate and wrong for a read inventory — it would report
`discrete_positions: []` on a rig whose optical elements are all present, and an
empty list reads as "this rig has none". An unreadable Core shutter excludes
nothing, and the payload says the exclusion could not be applied. The inventory
and allowed values are stored on the controller for the session. Orientation
adds one `get_property` per device to read the current label; allowed values
cannot change without a config reload. A failed allowed-values read does not
remove the device from the inventory: its entry remains present with
`allowed: "unknown"` and the read error recorded. Likewise, a failed current-label
read reports `label: "unknown"` on that device rather than dropping the entry.

**`role: "pixel-size-config dependency …"`** comes from walking the pixel-size
configs with `get_pixel_size_config_data()` and reading every device/property on
which each config is keyed — the walk `calibration._config_mismatches`
(`calibration.py:440`) already performs. This is intentionally weaker than
`role: "objective"`: a pixel-size config may also depend on camera binning, an
optovar, a tube lens, a camera selector, or several devices together. No one of
those dependencies is generically the objective changer. The `objective` block
therefore reports the active pixel-size config, calibrated pixel size, and all
config dependencies, then states what Micro-Manager does or does not establish
about the objective; it never promotes a dependency into a measured objective.
When no config is active, the same walk produces the diagnosis above instead of
the string `"default"`.

**`role: "light-path candidate"`** is the only annotation with a vocabulary
(`eye|ocular|binocular|camera|port|side|left|right|front|bottom|photo|tube`),
and it is applied to the **state labels**, never the device name — CLAUDE.md's
rule that shutter-ness comes from the device type and never from the name
applies with equal force here. The vocabulary **marks** entries; it never
filters one out. Every discrete-position device is listed either way. This is
`_lock_status_properties`' precedent restated: *showing the values is what makes
the difference obvious without a single extra tool call.*

**`focus`** calls `get_focus_lock_state` and embeds its result unchanged. Fold
into what exists; do not re-derive the roles.

### §3 — The cost, and why it is affordable

`get_system_state`'s implementation carries a standing instruction
(`tools.py:3004`): read only what `named_stages` declares, do **not** enumerate
the rig's devices, and do not add reads here without weighing the same trade.
Weighed:

| addition | round trips |
| --- | --- |
| device enumeration + `get_device_type` per device | **0 in `get_system_state`** — paid at session start by `validate_live_rig`; a residual startup cost remains, below |
| `get_allowed_property_values` per discrete device | N on inventory construction, then **0** — retained with the inventory |
| current label per discrete device | N, where N = 4 on the Nikon Ti, 4 on the demo config |
| active pixel-size config + pixel size | 2 |
| pixel-size config rules | 0 after the first call — cached; config data cannot change without a reload |
| `get_focus_lock_state` | non-EMU path: autofocus device + engaged state + property-name enumeration, one read-only check per property, and one value read per read-only property |

The startup saving is not quite total. `_auto_classified_state_pairs` `continue`s
on the shutter, illumination and ruled devices **before** it calls
`device_type_name` (`authorization.py:743–751`), so those devices' types are
never fetched today — and they are precisely the ones §2 requires the inventory
to include. The inventory therefore adds one type call per excluded device, once,
at startup.

There is no honest rig-independent numeric bound: the exact count is a function
of the number of StateDevices, pixel-size-config rules, and focus-lock
properties. In particular, `_lock_status_properties` pays both
`is_property_read_only` and `get_property` for each readable status property; a
table that counts only value reads understates the cost. Against the Nikon's
existing payload of 3 named-stage reads plus 8 others, the gate must report the
actual bridge-call count and measured wall time for the first and second calls.
If the first call exceeds ~1.5 s on the Nikon, the caching boundary is wrong, not
the feature.

pyjavaz serializes every bridge call, so these are paid in sequence. That is the
trade being accepted: a light path nobody reads is a light path nobody can
check.

### §4 — A reference file: how light paths usually work

New `microclaw/optics_docs.py` and tool `get_optical_path_documentation`, in the
existing `smlm_docs` / `hook_docs` / `dna_paint_docs` mould — a fifth instance of
a pattern, not a new layer. Contents, all generic:

- **What a light path is.** Ports (eyepiece, side/left/right, bottom/base,
  front), splits (100/80/20 — that `4-Left80` sends 80% to the camera and 20% to
  the eyepiece), and that a port label naming a percentage is a split, not a
  position index.
- **What Micro-Manager can and cannot see.** The motorized path only. A manual
  prism slider, a filter cube pulled to a detent, a closed field diaphragm and a
  condenser out of position all produce a dark camera with every readable value
  correct. **This is the 2026-08-23 fault, and the doc names it as the first
  thing to check on a blank frame with a valid focus lock.**
- **Objectives.** Magnification, NA and immersion; that the working distance and
  the focus-search window scale with them; that an unnamed turret position
  (`4-Unknown`) means the config author never labelled it, not that the turret
  is broken.
- **Focus hardware.** Hardware focus locks in general (reflection off the
  coverslip, capture band, offset), and that a lock can hold on the wrong
  reflecting surface.
- **The PFS/immersion capture-range numbers currently in `SYSTEM_PROMPT`**
  (`agent.py:407–412`) move here, as one worked example of the general shape,
  clearly marked as Nikon-specific. The prompt keeps the *procedure* — jog and
  image-check every lock, never substitute the offset range for the capture band
  — and loses the hardware naming it does not need.

The prompt is not grown to carry this. `optical_path.hint` names the tool in the
orientation payload itself, the way `probe_hint` already names `run_autofocus`.
That way a rig with no discrete-position devices and no focus lock pays no
per-device reads or documentation-token cost. It still pays the small fixed
focus-discovery cost: on the non-EMU path, the autofocus-device and
continuous-focus reads needed to establish that no lock is configured.

### §5 — What this does not fix

Stated plainly so no gate scores it as a success it is not:

- It would not have found the 2026-08-23 fault. A manual prism is invisible to
  every read proposed here. It moves the exclusion from message 52 to message 2
  and puts the physical question in front of the operator before the first
  exposure.
- It does not tell the agent which port is correct. `2-Left100` is a camera port
  and `3-Right100` is also a camera port; which one this rig's camera sits on is
  a rig fact and belongs in the knowledge base — *after* orientation has shown
  that the choice exists.
- It does not label an unnamed turret position. `4-Unknown` stays unknown; the
  change is that it is reported as unknown at orientation rather than presented
  as `"default"` or asked of the operator as though microclaw had no way to look.

## Blocks

design/59 owns its own blocks and ledger, as design/58 does. It is not a
design/35 row.

### 59a — `get_system_state` names the optical path

Items:

1. Add a read-only StateDevice inventory to live-rig validation, independent of
   the authorization map: every loaded StateDevice except `Core.Shutter`,
   including explicitly ruled and illumination-declared devices. Retain its
   device identities and allowed position values on the controller for the session.
   `optical_path.discrete_positions` reads only each current label on every call.
   Per-device failures preserve the entry: unreadable allowed values or a current
   label become `"unknown"` with the read error recorded, never an omitted device.
2. `objective`, from the pixel-size configs only. Fold
   `calibration._config_mismatches`' rule walk rather than duplicating it. Report
   every config dependency and mark it as such; do not infer that a dependency
   is an objective changer. A multi-key config must preserve all its keys.
   **Delete the `"default"` fallback in `_current_objective`** or make every
   caller distinguish "no config active" from "a config named default" — the
   string is currently reported to the operator as an objective.
3. `focus`, calling `get_focus_lock_state` and embedding its payload.
4. All three always present, `"unknown"` when unreadable, per `_shutter_state`.
5. Update `tools_schema.py`'s `get_system_state` description; it currently
   promises "stage positions, active channel, exposure time, live view, shutter
   and lasers" and must promise these.
6. `get_system_state` is `@emits_nothing` and stays so.

Tests — watch each fail on the pre-change tree, and **write the fixture that
reaches the branch** (58a's lesson: a refusal guarded on a shape no fixture
produced went four review rounds unexecuted):

- A fake core with **no** StateDevice, **no** pixel-size config and **no**
  autofocus device — all three fields present, all three `"unknown"`/diagnosed.
  This is the branch a minimal rig takes and the one most likely to be skipped.
- A fake core reproducing the Nikon Ti: `TINosePiece` at `4-Unknown`,
  `TILightPath` at `2-Left100` with the four real allowed values, pixel-size
  configs keyed on `TINosePiece.Label` matching none of them, `TIPFSStatus`
  reporting `Out of focus search range`. Assert the payload names
  `TINosePiece.Label` as the calibration dependency, but does not claim it is a
  measured objective device, **without any `TINosePiece` string in the
  implementation**.
- A fake core reproducing the demo config: `Objective`, `Path`, `Autofocus`,
  and `Res10x/20x/40x`. Assert the same code produces the same shape with no
  Nikon vocabulary reachable.
- Round-trip count assertion: the second `get_system_state` call in a session
  issues strictly fewer bridge calls than the first (the cache is real, not
  claimed). Mutate the cache key rather than watching this fail —
  its subject is call *structure*, per the standing rule.
- The port vocabulary **marks** and never filters: a StateDevice whose labels
  match nothing still appears in `discrete_positions`.
- A StateDevice explicitly present in `allowed_categorical`, `denied`, or the
  illumination configuration still appears in `discrete_positions`; substituting
  `_auto_classified_state_pairs` makes this test fail.
- A pixel-size config keyed on both `Objective.Label` and `Camera.Binning`
  reports both as dependencies and calls neither device a measured objective.
- A fake core whose `get_shutter_device` **raises**: `discrete_positions` still
  lists every StateDevice, and the payload says the shutter exclusion could not
  be applied. An empty list here is the defect, not the fallback.
- A StateDevice whose allowed-values read raises, and one whose current-label
  read raises: both remain in `discrete_positions`, with the failed field
  `"unknown"` and its read error visible. Dropping either entry is the defect.

Gate — **the demo machine first, then the Nikon.** The demo config carries an
`Objective` StateDevice, a `Path` light-path device, an `Autofocus` device and
pixel-size configs, so the generic path is fully exercisable without a Nikon;
confirm that inventory on the machine as step 1 rather than assuming it. Every
limb is a computation over one `get_system_state` payload, so it ships as **a
program** (`design/59-block59a-demo-gate.py`) that reports each limb
independently and exits nonzero — not seven pasted PowerShell blocks (58a).
Wall time for the first and second call is a reported limb, not a footnote.

### 59b — the reference, and the prompt loses its hardware names

Items:

1. `microclaw/optics_docs.py` with the §4 contents; tool
   `get_optical_path_documentation`; `@emits_nothing`; registered in
   `TOOL_REGISTRY` **and decorated** — an undecorated tool plants a
   `raise RuntimeError` in every exported script that recorded it, and that has
   now killed three gates.
2. `optical_path.hint` names the tool; the tool is not named in `SYSTEM_PROMPT`'s
   always-paid prose.
3. Move the PFS capture-range and immersion numbers out of `SYSTEM_PROMPT` into
   the reference. Keep the procedure lines (jog + image check on every lock;
   never substitute the offset range for the capture band).
4. Add one prompt line to the blank-frame guidance: after a blank frame, read
   `optical_path` before considering another exposure — alongside the existing
   `declared_illumination_properties` instruction, which is the same shape.

Gate — **the Nikon**, replaying the 2026-08-23 opening.

The criterion names the mechanism, not the outcome (52b: an outcome-shaped step
gets satisfied by a better route and the mechanism under test never fires):

- **Control that can fail** (58a: a limb that cannot fail is not a criterion):
  before the session, set `TILightPath` to `1-Eye100`. The limb is that the
  agent names the eyepiece routing **from message 2's payload, before its first
  `snap_and_analyze`**. Scored on the message index of the naming versus the
  message index of the first exposure — not on whether it eventually got there.
- Second round with `TILightPath` at `2-Left100` and the **manual prism** at the
  eyepiece — the real 2026-08-23 configuration. The pass condition is *not*
  that the agent finds the fault; it is that it reports the software path as
  correct and asks about the physical path before the second blank frame.
- Objective: with no pixel-size config active, the agent must report that
  Micro-Manager does not know the objective. Reporting "default", or reporting
  "60×" from the operator's earlier message as though measured, is a fail.
- Focus: the agent proposes the hardware lock before any image-based sweep, from
  orientation alone, with no operator prompting.

Scoring is a computation over the history JSONL and ships as
`design/59-score-gate.py` — the operator drives the session and returns the
file; the scorer reports each limb independently. Per step 6, score from the
artifacts and not the verdict.

## Out of scope, recorded so it is not lost

The same session surfaced two things this design does not address:

- **A relative XY move reports no entry position.** At message 41 the agent
  requested `move_stage_xy(x_um=10, y_um=10, absolute=false)` and got back
  `{"x_um": -6803.3, "y_um": 2779.9, "error_um": [-0.1, 0.0]}`. Those are the
  computed *absolute target* and the error against it — correct, and read by the
  agent as a 6.8 mm move it had not asked for (message 43: *"that's a red flag"*).
  The entry position had genuinely changed since message 2, most plausibly when
  the operator went to the scope to fix the prism. `move_stage_z` reports
  `requested_um` and `measured_um`; the relative XY path reports neither the
  entry position nor the requested delta, so nothing in the payload lets a
  reader tell a large stale-frame move from a bug.
- **`move_stage_xy` still has the block-56 shape.** It calls `_wait`
  (`core.wait_for_device`) and takes one immediate read (`tools.py:2284–2288`).
  Per CLAUDE.md's engine contract, *a device that is not busy is not a device
  that arrived* — this is the premature read-back block 56 fixed for the
  single-axis Z tools and did not reach here.

Both belong in design/35's carried-forward register, not in this block.

## Run ledger

| block | branch | start | implementation | gate | merge |
| --- | --- | --- | --- | --- | --- |
| 59a | — | — | — | — | — |
| 59b | — | — | — | — | — |
