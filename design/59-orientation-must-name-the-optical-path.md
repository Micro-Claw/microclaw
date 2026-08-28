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

There is one thing the KB *is* right for, and §2 uses it: **what each position
is**, on a rig whose configuration never recorded it. That is a static fact about
one configured device, not live state — `State-1` is the left camera port whether
or not it is selected — and it is authored after the first orientation rather
than before it, which is exactly when the operator can answer. It is not,
however, a global `rig/device_roles` fact: the current KB has one unconditioned
`rig` mapping, and `State-0/1/2` commonly recur on unrelated configurations.
59b therefore stores such an answer under `devices/`, conditioned on the live
camera adapter **and** the StateDevice identity described in §2. The division is:
which position is live now comes from the hardware on every call and is never
cached; what the positions *mean* comes from the labels when the config author
wrote them, and otherwise from a human-confirmed, live-identity-scoped device
entry. A condition mismatch makes the entry inapplicable, not approximately
right.

**The match is resolved in `get_system_state`, not in the prompt.**
`format_for_prompt` runs inside `_system_blocks` (`agent.py:634`) from
`load_knowledge()` alone, once, before any orientation call, and its block is
pinned with `cache_control: ephemeral`. It has no live payload and cannot
acquire one, so a condition checked there would be checked by the agent reading
a rendered header — exactly the unverified citation 59b's own control limb
exists to catch. 59b changes `format_for_prompt` to omit structured
position-map entries entirely; it continues to render legacy string-conditioned
`devices/` entries with their existing verify-first header. Orientation loads
the structured entries, reads every field their conditions name, evaluates the
conditions itself, and reports only a matching mapping inside `optical_path`,
beside the device it describes. A non-matching structured entry therefore never
reaches the agent, either through the system block or the orientation payload.

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
    {"device": "TINosePiece", "adapter": "TINosePiece",
     "adapter_description": "Objective Turret", "label": "4-Unknown",
     "allowed": ["1-Unknown","2-Unknown","3-Unknown","4-Unknown","5-Unknown","6-Unknown"],
     "role": ["pixel-size-config dependency (not proof of objective)"]},
    {"device": "TILightPath", "adapter": "TILightPath",
     "adapter_description": "Light Path Drive", "label": "2-Left100",
     "allowed": ["1-Eye100","2-Left100","3-Right100","4-Left80"],
     "role": ["light-path candidate (position labels name ports)",
              "light-path candidate (adapter self-description)"]},
    {"device": "TIFilterBlock1", "adapter": "TIFilterBlock1", "label": "3-GFP",
     "allowed": [...]},
    {"device": "TICondenserCassette", "adapter": "TICondenserCassette",
     "label": "1-BF", "allowed": [...]}
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

The Nikon above is the easy case: its config author named the ports, so the
labels answer the question on their own. A rig whose author did not is the case
this design has to handle without inventing an answer — the demo config's
light path is `State-0/1/2`, which names nothing:

```json
{"device": "Path", "adapter": "DLightPath",
 "adapter_description": "Demo light path", "label": "State-0",
 "allowed": ["State-0", "State-1", "State-2"],
 "role": ["light-path candidate (adapter self-description)"],
 "positions_unnamed": "This device routes light, but its position labels carry
   no port vocabulary: the Micro-Manager configuration does not record what
   State-0/1/2 are. Ask the operator what sits on each position and offer to
   store the answer with save_knowledge as an identity-scoped devices/ entry.
   Do not guess, and do not rename the labels — naming this rig's hardware is
   the operator's to do in Micro-Manager, not microclaw's to do behind their
   back."}
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

**`role: "light-path candidate …"`** is the only annotation with a vocabulary,
and it has **two independent sources**, each naming itself in the role string so
the reader can see which one fired.

*The position labels.* Applied to the **state labels**, never the device label.
Matching is case-insensitive with ASCII-letter boundaries — digits and
punctuation delimit a token, letters do not — over this vocabulary, **compounds
first**:

```
eyepiece|trinocular|binocular|phototube|sideport|leftport|rightport|
frontport|bottomport|camport|eye|ocular|camera|port|side|left|right|
front|bottom|photo|tube
```

wrapped as `(?<![A-Za-z])(?:…)(?![A-Za-z])`. So `Left80`, `Eye100`,
`Camera-Port`, `Eyepiece`, `Trinocular` and `Sideport` match, while
`Brightfield`, `Photoactivation`, `Portrait`, `Outside` and `Photobleach` do
not. CLAUDE.md's rule that shutter-ness comes from the device type and never
from the name applies with equal force here. This is the source that answered
the 2026-08-23 session, at message 52, from `TILightPath.Label`'s allowed
values.

**The compounds are not decoration and the ordering is not cosmetic.** A
boundary rule alone drops `Eyepiece`, `Trinocular`, `Sideport`, `Leftport`,
`Frontport`, `Bottomport`, `Camport` and `Phototube` — measured — and
`eyepiece` and `trinocular` are the two most ordinary English names for the two
destinations this whole design is about; §4's own source calls the camera head
the trinocular extension tube. (`Trinocular` is the one compound `main` already
matches, and by accident — its substring matcher finds `ocular` inside it. So it
is not evidence for the boundary rule; it is evidence that `main` passes for the
wrong reason. Measured 2026-08-28.) The asymmetry decides it: a false positive is
**visible and survivable**, because marking is all that happens and the entry's
`allowed` values are shown beside it, while a false negative is **silent** — the
device goes unmarked, `positions_unnamed` never fires, the agent never asks, and
that is Gap 1 again. Precision here is worth less than recall. The alternates are written compounds-first. **That ordering is a
representation choice, not a correctness requirement** — measured 2026-08-28:
Python's alternation backtracks past a failed lookahead, so
`(?<![A-Za-z])(?:eye|eyepiece)(?![A-Za-z])` still matches `Eyepiece` in full.
An earlier draft of this section claimed `eye` would consume the front of
`Eyepiece` and strand the trailing `p`; it does not. Keep the ordering because
it makes the vocabulary readable and because a structural test pins it, and do
not defend it as the thing that makes the matcher work.

**Substring matching is a live defect on `main`, not merely a 59b design
choice.** 59a shipped `_PORT_LABEL_WORDS.search()` (`tools.py:3002`), which is a
substring test, so today:

```
Brightfield -> right   Photoactivation -> photo   Portrait -> port
Outside     -> side    Sideport        -> side
```

`Photoactivation` is not a hypothetical label on an SMLM rig, which is the kind
of rig microclaw is for. 59a's demo gate scored `Path` as "correctly unmarked —
no false positive across six devices"; that passed because no demo label happens
to contain one of these substrings, not because the matcher is sound. A gate
that cannot fail did not fail. The tokenizer below fixes it, and the defect is
carried as its own row rather than being absorbed silently into a feature block.

*The adapter.* `get_device_name` and `get_device_description` are what
Micro-Manager's own adapter says it is, not what the config author typed — the
distinction `camera` already draws four lines away (`tools.py:3178–3181`: *"the
label is whatever the config author typed; the adapter is the hardware. F4 keys
knowledge entries on the adapter for that reason"*). `TILightPath` describes
itself as `Light Path Drive`; `DLightPath` as `Demo light path`. Matching
`light\s*path` against the adapter name and description is therefore **not** the
device-name heuristic the rule above forbids; it is the same class of fact as
`device_type_name`, read from the driver rather than from a string a human chose.
It is retained with the inventory, so it costs nothing per orientation call.

Two sources, because either can be absent: a rig can name its ports on a device
whose adapter is generic (`Arduino-Switch`), and a rig can run a named adapter
whose labels were never filled in. Both signals **mark**; neither filters. Every
discrete-position device is listed either way, and a false positive is survivable
precisely because marking is all that happens — this is `_lock_status_properties`'
precedent restated: *showing the values is what makes the difference obvious
without a single extra tool call.*

**`positions_unnamed`** is present only when the adapter identifies a light path
and the labels carry no port vocabulary — the state in which microclaw knows a
routing choice exists and cannot say what the choices are. It is the honest
report of that gap, and it routes the answer to where a configured-device fact
belongs. `save_knowledge` is human-gated (`tools.py:8252`) and `devices/`
already carries an `observed_on` condition; 59b strengthens that condition for
this use from the camera adapter alone to a structured identity:

| field | why it discriminates |
| --- | --- |
| live camera adapter | the existing `observed_on` condition, kept |
| StateDevice config label | the device this mapping is about |
| adapter name | `DLightPath` and `TILightPath` are different hardware |
| exact allowed-label list | the positions the operator was answering about |

The entry is distinguished from existing free-form `devices/` knowledge by
`kind: "optical_path_position_map"` and has the fixed value shape
`{"kind": ..., "device": <config label>, "positions": {<exact state label>:
<operator meaning>, ...}, "observed_on": <tool-resolved condition>}`. Position
keys outside the live allowed-label list are refused. This discriminator is what
lets `save_knowledge`, `format_for_prompt`, and `get_system_state` apply the
special handling below without changing unrelated device notes.

**`adapter_description` is reported in the payload and is deliberately not a
condition field.** It is prose an adapter author can reword on any
Micro-Manager upgrade, it discriminates nothing that `adapter` does not, and as
a condition it would silently retire the operator's stored answers on an update
they would have no way to connect to the cause.

The allowed-label list earns its place twice. It discriminates, and it makes the
entry **retire itself**: an operator who later labels the positions properly in
Micro-Manager changes that list, the condition stops matching, and the stored
guess falls away in favour of labels that now answer the question directly. The
design pushes toward the better fix rather than competing with it.

An unreadable or `"unknown"` identity field cannot match, and refuses a new
save — fail-closed, because a mapping stored against an identity we could not
read is a mapping that will match the wrong rig. The refusal **names the field
and why**, in the shape design/58 settled: a tool whose failure can be caused by
one unreadable value says which value, rather than reporting a bare no.
The agent does not author this condition from prose. For a structured position
map, `save_knowledge` takes the target StateDevice label and the operator's
position mapping, finds that device in the controller's retained inventory,
reads the live camera adapter, and constructs `observed_on` itself from those
live values. It rejects a missing device, a supplied condition that disagrees
with the resolved one, or any unreadable identity field **before** confirmation;
the confirmation prompt shows the complete resolved condition and mapping.
Legacy camera-adapter string conditions keep their existing verify-first
treatment. This uses a human-chosen device label for identity, never for role
inference. A test with two configurations that both expose `State-0/1/2` but
differ in adapter or camera proves that the first mapping cannot leak into the
second. **Nothing writes to the microscope.** If the operator would rather label
the positions properly in Micro-Manager's configuration, that is the better fix
and the payload says so; microclaw does not do it for them.

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
| `get_device_name` + `get_device_description` per discrete device | 2N during live-rig validation, then **0** — retained with the inventory |
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

**Measured on the demo machine, 2026-08-28** (six StateDevices, three
pixel-size configs, one autofocus device): the first `get_system_state` costs
**39 bridge calls / 36 ms**, the second **27 calls / 8 ms**. The 1.5 s ceiling
this section set is not close to binding, and the retention is real — twelve
calls are paid once. The cost table above is therefore accepted as written; the
open question it raised (whether the caching boundary was drawn in the right
place) is closed on the machine rather than by argument.

Those numbers predate the adapter reads added to §2, which cost 2N once during
live-rig validation and nothing per orientation call — twelve more validation
calls on that machine, none in `get_system_state`. 59b's gate measures the
validation phase separately, then re-measures the first and second
`get_system_state`; measuring only the latter two would make the added startup
cost invisible.

### §4 — A reference file: how light paths usually work

New `microclaw/optics_docs.py` and tool `get_optical_path_documentation`, in the
existing `smlm_docs` / `hook_docs` / `dna_paint_docs` mould — a fifth instance of
a pattern, not a new layer. Contents, all generic:

- **What a light path is.** The order of the path — lamphouse, filters, field
  diaphragm, condenser, specimen, objective, beam splitter/prism, then out to
  the eyepieces or the trinocular/camera port. Ports (eyepiece, side/left/right,
  bottom/base, front), and split labels such as 100/80/20. `4-Left80` establishes
  at most that this adapter describes an 80% route toward its left port; it does
  **not** generically establish that the left port holds the camera or where the
  remaining light goes. Those destinations are adapter/rig facts. A percentage
  in a position label is routing vocabulary, not necessarily a position index.

  Source for the ordering and the beam-splitter/trinocular routing:
  microscopyu.com/microscopy-basics/components (checked 2026-08-28). It supplies
  the path and the two destinations and **nothing else this section needs** — no
  split ratios, no motorized-vs-manual distinction, no turret or focus-lock
  material. Those come from the 2026-08-23 session and the rig configs in the
  evidence archive; do not pad the reference with generic optics the agent will
  never act on.

  **That ordering is a common arrangement, not ground truth** (operator,
  2026-08-28). It describes a transmitted-light upright stand; plenty of
  microscopes are not that. On an inverted stand the condenser is above the
  sample and the objective below it. Under epi-illumination the excitation
  arrives *through* the objective and no substage lamp is in the path at all.
  Add a spinning disk, a TIRF illuminator, an optosplitter or a second camera
  and the segment between objective and detector is a different graph. So the
  reference must be written as *how light paths usually work*, saying so in
  those words, and every sentence in it that could be read as a claim about
  **this** rig must instead point at `optical_path` — which reads the actual
  devices — or at the operator. A reference that lets the agent infer a
  component microclaw never read has reintroduced Gap 1 in prose form, and 59b's
  gate scores the agent on what it read, never on what the document implies.
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
- **The Nikon PFS offset-range/immersion numbers currently in `SYSTEM_PROMPT`**
  (`agent.py:407–412`) move here, as one worked example of the general shape,
  clearly marked as Nikon-specific. **This bullet is block 59c, not 59b**
  (operator decision, 2026-08-28): no reachable machine can drive the rewritten
  prompt procedure, so the move waits for a Ti rather than shipping behind a
  grep. Until then this file carries the generic material above and names no
  Nikon identifier. The prompt keeps only the generic procedure:
  inspect the reported lock state, use the lock's property probe to locate its
  capture band, verify signal, and jog after engagement to reject a wrong-surface
  lock. The Nikon heading and the identifiers `PFS`, `TIPFSStatus`, and
  `PFSOffset` move to the reference with the numbers; the prompt names none of
  them. It still says never to substitute an offset range for a measured capture
  band.

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
  a configured-device fact and belongs only in an identity-scoped `devices/`
  entry — *after* orientation has shown that the choice exists and the operator
  has confirmed the mapping.
- It does not label an unnamed turret position. `4-Unknown` stays unknown; the
  change is that it is reported as unknown at orientation rather than presented
  as `"default"` or asked of the operator as though microclaw had no way to look.
- It does not name unnamed light-path positions either. Where the adapter says a
  device routes light and the labels say nothing, `positions_unnamed` states that
  and invites the operator to answer it into the knowledge base. Microclaw never
  writes a state label, on any rig, for any reason — an unlabelled configuration
  is the operator's to fix in Micro-Manager, and a tool that silently renamed
  their hardware would be a worse defect than the gap it papered over.

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

Gate — **the demo machine.** The demo config is expected to carry an `Objective`
StateDevice, a `Path` light-path device, an `Autofocus` device and pixel-size
configs, so the generic path is fully exercisable without a Nikon; **confirm that
inventory on the machine as step 1 rather than assuming it**, and let the program
report NOT EXERCISED for any limb whose device this machine does not have. Every
limb is a computation over one `get_system_state` payload, so it ships as **a
program** (`design/59-block59a-demo-gate.py`) that reports each limb
independently and exits nonzero — not seven pasted PowerShell blocks (58a).
Wall time for the first and second call is a reported limb, not a footnote.

**The gate ships its own safety config** (`design/59-block59a-demo-safety-config.yaml`,
the precedent is `design/33-block5-demo-safety-config.yaml`), because the single
strongest limb needs one: a StateDevice **declared as illumination** and a second
StateDevice **explicitly ruled** in `allowed_categorical` or `denied` must both
still appear in `discrete_positions`. Those are exactly the devices
`_auto_classified_state_pairs` drops, and off-rig fixtures are where 58a's
unreachable branch hid. The gate must not leave that document as the machine's
production safety config: the program records which document was active before it
starts and checks it back afterwards.

**Machine availability, 2026-08-28.** The operator has lost access to the Nikon.
The Nikon-Ti-shaped payload is therefore covered only by the unit fixture named
in Tests, and one live-Ti confirmation is **owed, not waived** — the checklist
carries it as an owed row. If M2 or M5 is free, the M5 pass costs nothing and
adds the one thing the demo machine cannot produce: M5 has no Core shutter, so
`get_shutter_device()` returns empty rather than raising, which is the *other*
half of the shutter-exclusion branch. It is not a precondition of merging.

### 59b — the reference, the adapter, and the identity-scoped position map

Items:

1. `microclaw/optics_docs.py` with the §4 contents; tool
   `get_optical_path_documentation`; `@emits_nothing`; registered in
   `TOOL_REGISTRY` **and decorated** — an undecorated tool plants a
   `raise RuntimeError` in every exported script that recorded it, and that has
   now killed three gates.
2. `optical_path.hint` names the tool; the tool is not named in `SYSTEM_PROMPT`'s
   always-paid prose.
3. **Held back to block 59c** (operator decision, 2026-08-28). Emptying
   `SYSTEM_PROMPT`'s Nikon section into the reference rewrites working,
   rig-proven focus procedure that no available machine can drive; see 59c.
   59b's reference file therefore carries §4's generic material only, and
   `SYSTEM_PROMPT` keeps its Nikon section unchanged apart from item 4's line.
4. Add one prompt line to the blank-frame guidance: after a blank frame, read
   `optical_path` before considering another exposure — alongside the existing
   `declared_illumination_properties` instruction, which is the same shape.
5. The adapter half of §2: retain `get_device_name` and `get_device_description`
   per StateDevice in the inventory, mark a light-path candidate from either the
   labels or the adapter, and emit `positions_unnamed` where the adapter
   identifies a routing device whose labels name nothing. Match label tokens on
   explicit ASCII-letter boundaries, not substrings — `Brightfield` is not
   evidence for `right`, and `Photoactivation` is not evidence for `photo`; both
   match on `main` today. Carry the compound tokens (`eyepiece`, `trinocular`,
   `sideport`, …) ordered before their prefixes, or the boundary rule silently
   loses the two most ordinary names for the two destinations.
   Store any operator-supplied position map only as a `devices/` entry
   conditioned on the live camera and StateDevice identity, and resolve that
   condition in `get_system_state` rather than in the prompt. Structured maps
   are omitted from `format_for_prompt`, and `save_knowledge` constructs their
   condition from live controller state rather than trusting an agent-authored
   copy. A global `rig/device_roles` answer is forbidden because it can cross
   configurations.

**Nothing in this block writes to the microscope, and neither does its gate.**
An earlier draft had the gate rename the demo config's `State-0/1/2` over the
bridge with `define_state_label` and restore them afterwards. That is struck
(operator decision, 2026-08-28) on the principle and on the evidence. The
principle: an unlabelled configuration is the operator's to fix, and a tool that
renames a microscope's hardware to make its own gate scoreable has broken the
thing it was measuring. The evidence: the rename was never needed for the fault
this design is about. In
`pfs-nikon-design56-4/20260823_095336_954938_microclaw_history.jsonl`, messages
51–52, `get_device_property_info(TILightPath, Label)` returned
`allowed_values: ["1-Eye100", "2-Left100", "3-Right100", "4-Left80"]` — the port
vocabulary was already on the rig, already readable, and 59a already reads it
(`authorization.py:791–793` into `tools.py:3002`). Every rig whose author named
its ports is served with no writes at all; the demo machine is served by the
adapter, which says `Demo light path` whatever the labels say.

Gate — **the demo machine**, replaying the 2026-08-23 opening on the demo config.
The Nikon is unavailable (operator, 2026-08-28) and this gate does not wait for
it; what the demo machine cannot settle is listed at the end and owed.

Every device name below is discovered, never assumed: a setup program
(`design/59-block59b-demo-setup.py`) enumerates the machine's StateDevices,
picks the light-path device by adapter/type rather than by the string `Path`,
and refuses with NOT EXERCISED if the machine has none. Its only hardware
actions are **moves**, to positions the device already has: the objective is
driven to a state that **no pixel-size config matches**, which reproduces the
Nikon's relevant epistemic state (Micro-Manager establishes no objective), and
the light-path device is driven
between its own existing positions. It records entry state, restores it, and
**reads the restore back** — 59a's gate restored `Objective.Label` in a `finally`
with nothing reading it, and no artifact could answer whether the rig was left as
it was found until a read-back limb was added.

The criterion names the mechanism, not the outcome (52b: an outcome-shaped step
gets satisfied by a better route and the mechanism under test never fires):

- **Control that can fail** (58a: a limb that cannot fail is not a criterion).
  Two rounds, scored on the message index of the naming versus the message index
  of the first `snap_and_analyze` — not on whether the agent eventually got
  there. Round A: the light-path device, whose adapter identifies it and whose
  labels name nothing. The agent must raise the routing question **from message
  2's payload, before its first exposure**, and must ask the operator what is on
  each position rather than assert one. Round B, the control: the same opening
  with the light-path device absent from the config, or — if the machine's config
  cannot be varied — with the scorer checking that no *non*-routing StateDevice
  (`Objective`, a filter wheel) drew the same routing question. The control fires
  because a session that asks the routing question about a filter wheel is
  pattern-matching the prompt, not reading `role`.
- **Marking, not filtering.** The stock-label device is still listed in
  `discrete_positions` with its `allowed` values shown, and the route is reported
  as unverifiable rather than absent. This is the marking-not-filtering rule seen
  from the session side.
- **The knowledge-base round trip.** When the agent offers to store the
  operator's answer, `save_knowledge` is reached and human-gated and writes an
  identity-scoped `devices/` entry. In a **second session**, `get_system_state`
  resolves that condition against live orientation and reports the mapping in
  `optical_path`; the agent uses it and does not ask again. A control with the
  entry withheld or one identity field changed must show no mapping in the
  payload and must ask or report it unknown — silence alone does not establish
  causation. Scored on the payload as well as the history files, because the
  payload is where the match is decided. The gate snapshots and restores the
  user's knowledge file. Nothing is written to the microscope in either session;
  the scorer asserts the device's `allowed` values are byte-identical throughout.
- **The blank frame.** Put the demo camera into a mode whose frames carry no
  structure (the setup program reads the camera's own allowed values and reports
  NOT EXERCISED if this machine's camera offers none). The pass condition is *not*
  that the agent finds a fault — there is none to find — it is that it reports
  the software path as readable and asks about the **physical** path before its
  second exposure.
- Objective: with no pixel-size config active, the agent must report that
  Micro-Manager does not know the objective. Reporting "default", or reporting a
  magnification the operator said earlier as though it were measured, is a fail.
- Focus: the agent proposes the hardware lock (the demo `Autofocus` device,
  discovered through `get_focus_lock_state`) before any image-based sweep, from
  orientation alone, with no operator prompting.
- The agent calls `get_optical_path_documentation` when it needs the vocabulary,
  and the tool is reachable **from `optical_path.hint`** — `SYSTEM_PROMPT` must
  not name it. Scored by grepping the shipped prompt as well as the transcript.
- Cost measured with the adapter reads in place in three phases: live-rig
  validation, first `get_system_state`, and second `get_system_state`. The two
  orientation calls are compared with §3's 39/36 ms and 27/8 ms; validation
  separately reports the added 2N adapter calls so startup cost cannot disappear
  from the evidence.

Scoring is a computation over the history JSONL and ships as
`design/59-score-gate.py` — the operator drives the session and returns the
file; the scorer reports each limb independently, and reports NOT EXERCISED
rather than PASS for any limb whose stimulus the setup program could not arrange.
Per step 6, score from the artifacts and not the verdict.

**Owed to a Nikon Ti, not settled here.** The demo camera's frames do not depend
on the light path, so no demo round reproduces a *real* blank field caused by
routing; the manual-prism configuration of 2026-08-23 cannot be staged at all;
and no demo device produces the `4-Unknown` turret label or a PFS status string.
**And the labelled-port half of the light-path read is owed too**: no demo device
carries port vocabulary in its labels, so the label-matching source — the one
that answered the real session at message 52 — is exercised on the demo machine
only by fixture. The demo gate tests the adapter source, the payload, the
ordering, the refusal to claim an objective, the knowledge-base route, and the
reachability of the reference, on the machine that is available.

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

Both belong in design/35's carried-forward register, not in this block. Added
there 2026-08-28 as "Two from the Nikon session of 2026-08-23 — **(no block)**".

## Implementation checklist

The process is `CLAUDE.md` §"The block workflow" — ten steps, uncompressed, per
block. This checklist names *what* each block owes; that section owns *how*.
Where the two disagree, `CLAUDE.md` wins and this document gets fixed.

**Machine availability, 2026-08-28: the demo machine only.** The operator has
lost access to the Nikon; M2 and M5 are reachable if free. 59a's and 59b's gates
are therefore designed for the demo machine, and what the demo machine cannot
settle is written down as owed rather than quietly dropped — in 59c's case as a
whole block that does not start until a Ti exists.

**Order: 59a, then 59b.** 59b's reference tool is reached from
`optical_path.hint`, which 59a creates, and its gate reads the payload 59a
builds. Do not run them concurrently.

### Block 59a — `get_system_state` names the optical path

Implementation (§1, §2, §3):

- [x] 1. Read-only StateDevice inventory built at live-rig validation,
  **independent of the authorization map**: every loaded StateDevice except
  `Core.Shutter`, *including* explicitly ruled and illumination-declared
  devices. Device identities and allowed values retained on the controller for
  the session; each call re-reads only the current label.
- [x] 2. It must not inherit `_auto_classified_state_pairs`' fail-closed-to-empty
  behaviour. An unreadable Core shutter excludes nothing and the payload says the
  exclusion could not be applied; `discrete_positions: []` there is the defect.
- [x] 3. Per-device failures preserve the entry: an unreadable allowed-values or
  current-label read becomes `"unknown"` with the read error recorded. A dropped
  device is the defect.
- [x] 4. `objective` from the pixel-size configs only, folding
  `calibration._config_mismatches`' rule walk rather than duplicating it. Every
  dependency reported and marked as a dependency; a multi-key config keeps all
  its keys; no dependency is promoted to a measured objective.
- [x] 5. **Delete `_current_objective`'s `"default"` fallback** (`tools.py:4148`),
  or make all three call sites — `_load_current_affine`, the calibration
  artifact at `tools.py:4303`, and the new payload — distinguish "no config
  active" from "a config named default". Whichever route: the string must stop
  reaching the operator as an objective name.
- [x] 6. `focus` embeds `get_focus_lock_state`'s payload verbatim. Fold; do not
  re-derive.
- [x] 7. The port vocabulary **marks** state labels and never filters an entry,
  and is never applied to a device name.
- [x] 8. All three keys always present, `"unknown"` when unreadable, per
  `_shutter_state`.
- [x] 9. `tools_schema.py`'s `get_system_state` description updated — it promises
  stage/channel/exposure/live-view/shutter/lasers today and must promise these.
- [x] 10. `get_system_state` stays `@emits_nothing`.

Tests — each watched failing on the pre-change tree, **and with a fixture that
reaches the branch** (58a: a guard no fixture's shape could reach went four
review rounds unexecuted):

- [x] 11. Minimal rig: no StateDevice, no pixel-size config, no autofocus device
  — all three fields present, all three `"unknown"`/diagnosed.
- [x] 12. Nikon-Ti-shaped fake (`TINosePiece` at `4-Unknown`, `TILightPath` at
  `2-Left100` with the four real allowed values, configs keyed on
  `TINosePiece.Label` matching none, `TIPFSStatus` "Out of focus search range"):
  the payload names the dependency and does not claim a measured objective,
  **with no `TINosePiece` string in the implementation**.
- [x] 13. Demo-shaped fake (`Objective`, `Path`, `Autofocus`, `Res10x/20x/40x`):
  same code, same shape, no Nikon vocabulary reachable.
- [x] 14. Second call issues strictly fewer bridge calls than the first —
  **mutate the cache key** rather than watching this fail; its subject is call
  structure (standing rule).
- [x] 15. A StateDevice whose labels match no port vocabulary still appears.
- [x] 16. A StateDevice in `allowed_categorical`, in `denied`, or declared as
  illumination still appears; substituting `_auto_classified_state_pairs` makes
  this test fail.
- [x] 17. A config keyed on both `Objective.Label` and `Camera.Binning` reports
  both and calls neither a measured objective.
- [x] 18. `get_shutter_device` **raises**: every StateDevice still listed, and
  the payload says the exclusion could not be applied.
- [x] 19. An allowed-values read raises, and a current-label read raises: both
  entries survive with the failed field `"unknown"` and the error visible.
- [x] 20. Full suite re-run by the coordinator, not the reported count.

Gate — demo machine, shipped as a program:

- [x] 21. `design/59-block59a-demo-gate.py` reports each limb independently,
  reports NOT EXERCISED for any device this machine lacks, exits nonzero, and
  **owns its own log** (58a: `Start-Transcript` does not capture a child
  process's stdout).
- [x] 22. Step 1 of the runbook *confirms* the machine's StateDevice inventory,
  pixel-size configs and autofocus device rather than assuming them.
- [x] 23. `design/59-block59a-demo-safety-config.yaml` declares one StateDevice
  as illumination and rules on another, so limb 16 is live on hardware and not
  only in a fixture. The program records the machine's active safety document
  before it starts and checks it back afterwards — a gate must not leave
  production state pointing into its own evidence folder.
- [x] 24a. **Run the gate end to end against a bridge-shaped fake before it is
  pushed** — `design/59-block59a-gate-selftest.py`, on both trees so its failure
  discriminates. A `MagicMock` is not a bridge: its collections must expose
  `size()`/`get(i)` and raise on iteration. Added after demo gate round 1 was
  spent on `TypeError: 'mmcorej_StrVector' object is not iterable`; now in
  `CLAUDE.md` step 6 for every block.
- [x] 24. Bridge-call count **and** measured wall time reported for the first and
  second `get_system_state` call. §3: if the first call exceeds ~1.5 s the
  caching boundary is wrong, not the feature. **39 calls / 36 ms, then 27 / 8 ms.**
- [x] 24b. The gate leaves the rig as it found it, **read back**. Round 2 moved
  `Objective.Label` and restored it in a `finally` with nothing reading the
  result, so no artifact could answer whether the axis came back. The limb now
  re-reads and compares against the entry label and the entry pixel-size config;
  mutating the restore so it silently does not land makes it FAIL.
- [x] 25. Runbook committed **on the block's branch**, implementation pinned with
  `git merge-base --is-ancestor`, branch pushed to `origin`.
- [x] 26. Gate scored from the artifacts, not the verdict (step 6). Round 2
  passed 11/11 and the cross-checks agree: `pixel_size_config` `Res10x` -> `None`
  with `pixel_size_um` 1.0 -> 0.0 when the turret moves to `Objective-2`, all
  three dependencies' `live` follow the device, and the discrete label agrees
  with the dependency value in the same payload — the staleness defect closed on
  hardware. `Dichroic` (illumination-declared) and `Emission` (denied) both
  remain listed, so the read inventory is demonstrably not the authorization
  map. Core shutter `White Light Shutter` excluded and named. `Path` carries
  `State-0/1/2` and is correctly **unmarked** — no false positive across six
  devices — correct, and the reason 59b adds the adapter as a second source:
  `DLightPath` self-describes as `Demo light path` while its labels say nothing,
  so the device is identifiable without anything being renamed.

Owed, recorded rather than waived:

- [ ] 27. **Owed to a Nikon Ti:** one live payload from a rig with an unnamed
  turret position, a real `TILightPath`, and a PFS status device. Covered by
  fixture only until the operator has a Ti again. **Still owed after the merge**
  — the demo machine settled the mechanism, not the scene.
- [ ] 28b. **Owed, opened by this gate:** the demo `Autofocus` exposes no status
  property, and `probe_hint` still invited a probe against `Description`/`HubID`/
  `Name`. On design/35's register; needs a rig with a real lock.
- [ ] 28. **Opportunistic, not a precondition:** M5 has no Core shutter, so
  `get_shutter_device()` returns empty rather than raising — the other half of
  limb 18. Run it if M5 is free.

### Block 59b — the reference, the adapter, and the identity-scoped position map

Implementation (§2, §4):

- [ ] 29. `microclaw/optics_docs.py` with §4's contents, all generic: ports and
  splits; what Micro-Manager cannot see (the manual prism named as the **first**
  thing to check on a blank frame with a valid lock); objectives, working
  distance and search windows; hardware focus locks and the wrong-surface lock.
  §4's final bullet — the Nikon PFS worked example — is **not** written here;
  it arrives with block 59c, and a test asserts `optics_docs.py` names no Nikon
  identifier while 59b stands, so the split cannot half-happen either.
- [ ] 29a. The reference is framed as *how light paths usually work*, in those
  words, and the path ordering carries the caveat that stands differ — inverted,
  epi-illuminated, TIRF, spinning-disk and multi-camera rigs all depart from it.
  Nothing in the file may read as a claim about the rig in front of the agent;
  where it would, it points at `optical_path` or at the operator instead. A test
  asserts the caveat sits with the ordering, so the two cannot separate.
- [ ] 30. Tool `get_optical_path_documentation`, `@emits_nothing`, registered in
  `TOOL_REGISTRY` **and decorated** — an undecorated tool plants a
  `raise RuntimeError` in every exported script that recorded it, which has now
  killed three gates.
- [ ] 31. `optical_path.hint` names the tool; `SYSTEM_PROMPT` does not.
- [ ] 33. One prompt line: after a blank frame, read `optical_path` before
  considering another exposure — alongside the existing
  `declared_illumination_properties` instruction, which is the same shape.
- [ ] 34a. `_build_state_device_inventory` retains `get_device_name` and
  `get_device_description` per StateDevice, each failing to `"unknown"` with the
  error recorded rather than dropping the entry — the same rule `allowed` already
  follows. Paid once at inventory construction; **zero** added to
  `get_system_state`, asserted by a test that counts calls on the second
  invocation.
- [ ] 34b. `role` is a list and each entry names its source:
  `light-path candidate (position labels name ports)` and
  `light-path candidate (adapter self-description)`. A device matched by both
  carries both. Fixtures: the Nikon `TILightPath` (both), the demo `Path`
  (adapter only), and a labels-only device on a generic adapter.
- [ ] 34b1. Port-label matching is case-insensitive, uses ASCII-letter
  boundaries, and carries the compound tokens of §2 **ordered before their own
  prefixes** — otherwise `eye` consumes the front of `Eyepiece` and the trailing
  `p` fails the lookahead. Fixtures assert `Left80`, `Eye100`, `Camera-Port`,
  `Eyepiece`, `Trinocular`, `Sideport`, `Leftport`, `Frontport`, `Bottomport`,
  `Camport` and `Phototube` match, while `Brightfield`, `Photoactivation`,
  `Portrait`, `Outside` and `Photobleach` do not. Both halves are load-bearing:
  the false positives are what `main` gets wrong today, and the compounds are
  what a boundary rule alone would newly lose. The non-routing control in the
  live gate depends on this distinction.
  **This is a fix to shipped behaviour, so watch it fail**: run the new fixtures
  against `main`'s `_PORT_LABEL_WORDS.search()` and confirm each false positive
  is produced before the tokenizer lands. Measured 2026-08-28: `Brightfield` →
  `right`, `Photoactivation` → `photo`, `Portrait` → `port`, `Outside` → `side`,
  `Photobleach` → `photo`. An earlier draft added that `trinocular` is absent
  from `main`'s vocabulary so its fixture fails there for a second reason —
  **struck, it does not**: `main` finds the substring `ocular` inside
  `Trinocular` and the positive fixture passes there for the wrong reason.
  Nothing in the new-fixture set fails on `main` for a second reason.
- [ ] 34c. `positions_unnamed` present **only** when the adapter identifies a
  light path and no label matches the port vocabulary; it names the
  identity-scoped `devices/` route and says explicitly that microclaw does not
  rename labels. A test asserts it is absent on the Nikon fixture and present
  on the demo one.
- [ ] 34c1. Extend `devices/observed_on` to accept a structured condition for
  unnamed position maps: camera adapter, StateDevice config label, adapter name,
  and exact allowed-label list. **`adapter_description` is reported in the
  payload and is not a condition field** — it is prose an adapter author can
  reword on an upgrade, and it discriminates nothing `adapter` does not.
  Legacy camera-adapter string conditions keep their current verify-first
  semantics. Two fake rigs with the same `State-0/1/2` labels but different
  adapters or cameras prove that a mapping saved on one is not applicable on the
  other. A third fixture changes only the allowed-label list — the operator
  labelling their positions properly in Micro-Manager — and proves the stored
  guess retires itself rather than shadowing the new labels.
- [ ] 34c1a. Structured maps use the explicit discriminator
  `kind: "optical_path_position_map"` and the fixed `device`, `positions`, and
  `observed_on` fields described in §2. Reject position keys not present in the
  resolved device's exact allowed-label list. Ordinary `devices/` entries,
  including ones whose `observed_on` happens to be a mapping for some future
  feature, do not enter this path without the discriminator.
- [ ] 34c2. **The condition is evaluated in `get_system_state`, never in the
  prompt.** `format_for_prompt` runs once at `_system_blocks` (`agent.py:634`)
  from `load_knowledge()` alone, before any orientation call, under
  `cache_control: ephemeral`; it has no live payload. Orientation already reads
  every field the condition names, so it resolves the match and reports only an
  applicable mapping inside `optical_path`, beside the device it describes.
  `format_for_prompt` is changed to omit structured position-map entries
  entirely while preserving the existing rendering of legacy string-conditioned
  entries. Tests assert a matching and a non-matching structured entry are both
  absent from the system block, only the matching one appears in orientation,
  and a legacy entry still renders with its verify-first header.
- [ ] 34c3. An unreadable or `"unknown"` identity field refuses a new save and
  never counts as a match, and the refusal **names the offending field and why**
  rather than reporting a bare no (design/58: a tool whose failure can be caused
  by one unreadable value says which value). A test asserts the field name
  appears in the refusal.
- [ ] 34c4. The agent does not manufacture `observed_on`. For a structured
  position map, `save_knowledge` accepts the target StateDevice label and mapping,
  resolves the device from the retained inventory, reads the live camera adapter,
  and constructs the structured condition itself. It refuses a missing target,
  any unreadable identity field, and any caller-supplied condition that differs
  from the resolved condition, all before `CONFIRM_FN`; the confirmation text
  contains the complete resolved identity and mapping. A fixture deliberately
  supplies a plausible but wrong adapter and proves it is neither confirmed nor
  persisted.
- [ ] 34d. **A test that 59b's paths do not write a state label.** The inventory,
  orientation, documentation tool and gate setup are exercised against a
  bridge-shaped fake that raises on `define_state_label` / `defineStateLabel`.
  The scoped source assertion **names its modules explicitly** —
  `microclaw/authorization.py`, `microclaw/tools.py`, `microclaw/optics_docs.py`
  and `design/59-block59b-demo-setup.py` — because "the production paths" decays
  into whatever the implementer considered in scope. It is deliberately not all
  of `microclaw/`: this block must not preclude a separately designed, explicitly
  authorized configuration tool.
- [ ] 34e. Adapter matching is on `light\s*path` against the adapter name and
  description only, never the device label. A fixture whose *device label* is
  `LightPath` but whose adapter is a filter wheel must **not** be marked — the
  device-name heuristic CLAUDE.md forbids, caught by a test rather than by
  review.
- [ ] 35. Schema parity and the export decorator tests green; full suite re-run
  by the coordinator.

Gate — demo machine, a driven session plus two programs:

- [ ] 36. `design/59-block59b-demo-setup.py` discovers the light-path device by
  adapter/type rather than by name, drives the objective to a state no pixel-size
  config matches, drives the light-path device between positions it already has,
  restores entry state and **reads the restore back**. It writes no state label
  and no `.cfg`; the scorer asserts every device's `allowed` values are
  byte-identical before and after.
- [ ] 37. Control that can fail, scored on message indices: round A, the
  adapter-identified light path with unnamed labels — the agent raises the
  routing question from message 2's payload **before its first exposure** and
  asks what is on each position rather than asserting one; round B, the control —
  no *non*-routing StateDevice draws the same question.
- [ ] 37a. Knowledge-base round trip across two sessions: `save_knowledge` is
  reached in the first, constructs the condition from live state, and shows that
  complete identity and mapping at the human gate before storing the
  identity-scoped `devices/` entry. In the second session the raw structured
  entry is absent from the system context; `get_system_state` resolves it against
  live orientation and reports the mapping inside `optical_path`, and the agent
  uses it without asking again. A control session with one identity field changed
  must have the raw entry absent from the system context, show **no mapping in the
  payload**, and ask or report it unknown; mere absence of a repeated question is
  not proof that storage caused it. Scored on the system context, confirmation,
  payload and transcript. The gate snapshots and restores the user's knowledge
  file so its evidence does not alter later sessions. NOT EXERCISED if the
  operator declines the save — declining is their right and is not a product
  failure.
- [ ] 38. Marking, not filtering: the stock-label device is still listed with its
  `allowed` values shown, and the route is reported as unverifiable rather than
  absent.
- [ ] 39. Blank-frame round: the agent asks about the **physical** path before
  its second exposure. NOT EXERCISED if this machine's camera offers no
  structureless mode.
- [ ] 40. Objective: the agent reports that Micro-Manager does not know it.
  "default", or a magnification the operator mentioned reported as measured, is a
  fail.
- [ ] 41. Focus: the hardware lock proposed before any image-based sweep, from
  orientation alone, unprompted.
- [ ] 42. `get_optical_path_documentation` reached from the hint; the shipped
  prompt greps clean of its name.
- [ ] 42a. Cost measured with the adapter reads in place in three separate
  phases: live-rig validation, first `get_system_state`, and second
  `get_system_state`. Report the latter two against §3's 39/36 ms and 27/8 ms,
  and report the validation delta and its expected 2N adapter calls separately;
  an orientation-only measurement cannot pass this limb by hiding startup cost.
- [ ] 43. `design/59-score-gate.py` scores each limb independently over the
  history JSONL, reports NOT EXERCISED where the stimulus could not be arranged,
  and exits nonzero.
- [ ] 43a. 59b's setup program and scorer are likewise run against a
  bridge-shaped fake before the operator sees them, on both trees.
- [ ] 44. **Owed to a Nikon Ti:** a real blank field caused by routing, the
  manual-prism configuration, a `4-Unknown` turret, a live PFS status, **and the
  label-matching source of the light-path role** — no demo device carries port
  vocabulary in its labels, so that half is fixture-only until a Ti is available.
  The demo gate tests the mechanism, not the 2026-08-23 scene.
### Block 59c — the prompt loses its hardware names

Split out of 59b by operator decision, 2026-08-28, on row 44a's own argument.
`agent.py:381–410` is working, rig-proven focus text — sweep semantics, the
steady-vs-transient in-range warning, the mandatory jog and image check, the
air-bubble diagnosis. Rewriting it hardware-neutral is right, and **no machine
now reachable can drive the rewritten version**: the Nikon is gone, and 59a
measured that the demo `Autofocus` exposes no status property at all (row 28b),
so probe, engage and jog cannot be exercised. Shipping it inside 59b would put
an unexercised prompt rewrite on `main` behind a grep. It waits for a Ti.

Nothing else in 59b depends on it. 59b's reference file is generic; 59c appends
the Nikon worked example to it and empties the prompt's Nikon section in the
same commit, so the two halves of the move cannot separate.

- [ ] 32. The Nikon PFS **offset-range** and immersion numbers move out of
  `SYSTEM_PROMPT` (`agent.py:407–412`) into `optics_docs.py`, marked
  Nikon-specific. So do the Nikon heading and the identifiers `PFS`,
  `TIPFSStatus`, and `PFSOffset`. The prompt keeps the generic procedure: inspect
  live lock state, locate the capture band with the reported property probe,
  engage, image-check and jog every lock, and never substitute an offset range
  for the capture band.
- [ ] 34. A test that the moved numbers and Nikon identifiers are no longer in
  the prompt **and** are in the reference, while the generic engage/image/jog
  procedure remains, so the move cannot half-happen in either direction. It
  replaces 59b's test that the reference names no Nikon identifier (item 29);
  both cannot stand at once, and that is the point.
- [ ] 44b. **Gate: a Nikon Ti, and there is no substitute.** The whole reason
  this is its own block is that item 34's grep proves the text moved and nothing
  proves the moved-to version still drives a lock. Run the rewritten procedure
  end to end — inspect, locate the capture band with the property probe, engage,
  image-check, jog — on the first Ti available. Until then 59c does not start.
  Row 44's other Ti debts (a real routing-caused blank field, the manual prism,
  a `4-Unknown` turret, the label-matching source of the light-path role) should
  be collected on the same trip.

### Post-merge design gate (step 10, all blocks)

- [x] 45. (59a) Reconcile this document to what was measured — especially §3's cost
  table against the gate's real bridge-call count and wall time.
- [x] 46. (59a) Record coordination notes in `design/prompts.md` and close the ledger
  rows below.
- [x] 47. (59a) `git log --oneline origin/main..main` empty for each block; branch
  deleted locally and on `origin`.
- [ ] 48. Carry the owed-to-a-Ti rows (27, 44) and block 59c itself forward into
  design/35's register, so losing Nikon access does not lose the evidence debt.
  59c is a whole block waiting on a machine, not a footnote inside a merged one.
- [ ] 49. Carry the port-token false positives as their own register row, not as
  a line inside a feature block: `_PORT_LABEL_WORDS.search()` is substring
  matching on `main` today, and any rig with a `Photoactivation`, `Brightfield`,
  `Portrait`, `Outside` or `Sideport` state label is currently being told a
  filter wheel might be its light path. 34b1 fixes it; the row records that 59a
  shipped it and that its gate's "no false positive across six devices" was a
  limb that could not fail. If 59b slips, fix this ahead of it.

## Run ledger

| block | branch | start | implementation | gate | merge |
| --- | --- | --- | --- | --- | --- |
| 59a | `design59/optical-path` | `4b7751b` (2026-08-28) | `30ed48d` (3 Codex rounds, 12 findings; coordinator suite 2357/99/0) | round 1 **FAILED** 2026-08-28 — `mmcorej_StrVector` not iterable, all 9 orientation limbs NOT EXERCISED; the product half was a silent `available_configs: []`. Round 2 **PASS 11/11**, 39 calls/36 ms then 27/8 ms; restore read-back added afterwards | `e314927` merged 2026-08-28, branch deleted |
| 59b | `design59/optics-reference` | `906fac8` (2026-08-28) | — | — | — (design amended 2026-08-28: no renames, adapter identity, identity-scoped `devices/` map, compound port tokens; item 32 split out to 59c) |
| 59c | — | — | — | — | — (not started, and does not start: gated on a Nikon Ti) |
