# 14 — Where microclaw still struggles (review of the `amr_test` session)

Evidence: `20260707_143437_microclaw_history_amr_test.json` (162 messages, 12
`snap_and_analyze` thumbnails). Rig: htSMLM / MicroFPGA, Evolve512 camera,
Luxx405/488/638 + Cobolt561, PIZStage focus + SmarAct TIRF stage.

Session narrative: BFP (back focal plane) imaging → steer beam TIRF→epi →
find a cell → crop a 200×200 ROI onto it → autofocus → 100-frame SMLM
acquisition of CLC-SNAP/JF626b.

## TL;DR

The session *looks* like a success — the acquisition ran and the user was
delighted. But microclaw got there by **brute-force device probing and
eyeballing thumbnails**, and it made at least one silent correctness error that
could have invalidated the data.

Three themes, in order of severity:

1. **It guessed hardware indices instead of reading the map it already had.**
   The `get_emu_configuration` payload contains an authoritative
   htSMLM→device property map. Microclaw fetched it *after* configuring the
   hardware, never mined it, and verified the **wrong laser's trigger line**
   before running the acquisition (§1). It could hardly have done better:
   `_parse_properties` is written against a config format this rig does not use
   and silently returns a half-parsed dict — **0 of 120** entries got a `device`
   field, and 68 of 120 leaked as unnested metadata (§2a).
2. **The risk gradient is inverted.** It turned on a Class-3B laser with no
   confirmation and escalated it 1 %→25 %, then asked permission three times to
   call `mark_position` (a bookkeeping no-op). There is no laser gate anywhere
   in `safety.py` (§3).
3. **Its two "seeing" tools lie to it.** `run_autofocus` reports a curve that
   is *not* the curve it made its decision from, and stamps `settled: true,
   warning: null` on a garbage result (§4). `snap_and_analyze` never reaches
   the MM viewer, so microclaw told the user "the snapshot is already displayed
   on your screen" while the user stared at a stale BFP frame (§7).

Everything below is grounded in a specific message index (`[n]`) and a specific
`file:line`.

---

## 1. It verified the wrong laser's trigger line — the acquisition's actual gating was never checked

**This is the one that could have cost data.**

At `[15]` microclaw reasoned:

> "The 638 laser is index 2 (Luxx405=0, Luxx488=1, Luxx638=2 **typically**)."

…and read `Laser Trigger.Mode2` / `Sequence2`, then reported at `[17]`:

> "638 laser trigger Mode2 = 'Follow', Sequence2 = 65535 (laser emits during exposure) ✓"

But the EMU config it fetched 24 messages later at `[39]` says:

```
Laser 2 enable = Cobolt561-Laser
Laser 3 enable = Luxx638-Laser Operation Select
Laser 3 power percentage = Luxx638-Laser Power Set-point Select [%]
```

htSMLM indexes `Laser i …` and `Laser trigger i …` by the same physical laser
slot. **Luxx638 is slot 3.** `Mode2`/`Sequence2` are the *Cobolt561's* trigger
line. Microclaw inspected the 561's gating, declared the 638 ready, and at
`[145]` ran the 100-frame SMLM acquisition **without ever reading `Mode3` or
`Sequence3`**.

It got away with it here (the frames evidently exposed), but if `Mode3` had
been `0 - Off`, the dataset would have been 100 blank frames and nothing in the
tool results would have said so. Nothing in the session would have caught it —
`run_timelapse` returns `{"status": "Timelapse complete."}` regardless.

**Fix.**

- **`get_emu_configuration` must be consulted before device probing, not after.**
  Add to `SYSTEM_PROMPT` (`agent.py:39`): *"On an EMU/htSMLM rig, call
  `get_emu_configuration()` before `list_device_properties`. It is the
  authoritative map from semantic name → `Device-Property`; never infer a
  laser/filter/trigger index from device naming order."*
- Add a derived tool `resolve_emu_device(semantic_name)` →
  `{"device": "Luxx638", "property": "Laser Operation Select"}`, plus
  `get_emu_laser_map()` returning the slot→laser table:
  ```python
  {3: {"enable": "Luxx638-Laser Operation Select",
       "power_pct": "Luxx638-Laser Power Set-point Select [%]",
       "trigger_mode": "Laser Trigger-Mode3",
       "trigger_sequence": "Laser Trigger-Sequence3"}}
  ```
  This turns §1's five-round-trip guessing game into one call, and makes the
  slot index unguessable.
- **Pre-flight assertion in `run_timelapse`**: if an EMU laser map is
  available, refuse (or loudly warn) when the enabled laser's `trigger_mode` is
  `0 - Off` or its `trigger_sequence` is `0`. A "your excitation is gated off"
  check costs one property read and prevents a silently-blank dataset.

## 2. Microclaw rediscovered by trial-and-error what the EMU config already stated

Messages `[9]`–`[16]` are eight tool calls across four round trips spent
learning:

| What it discovered the hard way | Where it was already written |
|---|---|
| `Laser Power Set-point [%]` is read-only; the writable one is `…Select [%]` | `Laser 3 power percentage = Luxx638-Laser Power Set-point Select [%]` |
| Filter wheel = `Servos-Position3`; 700/100 → `32000` | `Filter wheel position = Servos-Position3`; `… state 3 = 32000` |
| Focus lock exists on PIZStage | `Z stage focus locking = PIZStage-External sensor` |

The filter-wheel value came from the *knowledge base* (`[7]`), which happened to
agree with the EMU table — luck, not verification. Meanwhile the config's 99
allocated properties (of 120; 18 % are `Unallocated`/`Enter value` placeholders)
went unread: microclaw dumped ~9 kB of it into context at `[39]` and used
exactly one fact from it.

### 2a. `_parse_properties` never fires on a real config — two hard bugs

The reason the payload was raw sludge is not stylistic. `emu_manager.py:133-171`
is written against a config format this rig does not use.

**Bug 1 — wrong separator (`emu_manager.py:163`).** The parser splits on `::`
to populate `device`/`property`:

```python
if "::" in mm_str:
    device, prop = mm_str.split("::", 1)
```

Measured over the `[39]` payload: **0 of 120** property strings contain `::`.
They are all `Device-Property` (`Luxx638-Laser Operation Select`,
`Servos-Position3`, `Evolve512-Exposure`). So `device` and `property` are
**never populated** — `any("device" in v for v in properties.values())` is
`False` for every entry. The model received opaque strings and had to re-derive
the device by eyeballing prefixes against `list_devices` output. That is
precisely the manual step it got wrong in §1.

**The naive repair is also wrong.** `mm_str.split("-", 1)` breaks on this rig's
device names, which themselves contain hyphens:

| String | `split("-", 1)` | Correct |
|---|---|---|
| `Luxx638-Laser Operation Select` | `("Luxx638", "Laser Operation Select")` | ✅ same |
| `Focus-lock-Enable Fine` | `("Focus", "lock-Enable Fine")` ❌ | `("Focus-lock", "Enable Fine")` |
| `Thorlabs ELL9-1-Label` | `("Thorlabs ELL9", "1-Label")` ❌ | `("Thorlabs ELL9-1", "Label")` |

`Thorlabs ELL9` and `Thorlabs ELL9-1` are *both* loaded devices, so even a
greedy match must take the **longest** device prefix, resolved against
`core.get_loaded_devices()`. There is no way to parse these strings correctly
without the device list.

**Bug 2 — wrong metadata suffixes (`emu_manager.py:15`).**

```python
_META_SUFFIXES = (" on", " off", " slope", " offset")
```

The real config uses `" - On value"` / `" - Off value"` and `" state N"`.
Counting the `[39]` payload's 120 keys:

| Key shape | Count | Folded into parent? |
|---|---|---|
| base property | 52 | — |
| `" - On/Off value"` | 34 | ❌ leaks as standalone |
| `" state N"` | 34 | ❌ leaks as standalone |
| `" on"/" off"/" slope"/" offset"` (what the code expects) | 0 | n/a |

So **68 of 120 entries are metadata that should have been nested** and instead
appear as top-level pseudo-properties: `"Laser 3 enable - On value"`,
`"Filter wheel position state 3"`. That is both the token bloat *and* the reason
the filter-wheel state table (`state 0..5` → `5000…57000`) never presented
itself as a lookup table. The `slope`/`offset` branch is the only one that ever
matches, and neither appears allocated here.

Net: the one tool that could have prevented §1 has been returning a
half-parsed dict since it was written, and no test caught it because the
fixtures presumably use the `::` format.

**Fix.** In `emu_manager.py`, fix the parse *and* have `get_emu_configuration`
return a structured, placeholder-free view instead of the raw property dict:

```python
{"lasers": {...},           # slot -> {enable, power_pct, trigger_*}
 "filter_wheel": {"property": "Servos-Position3",
                  "states": {0: 5000, ..., 3: 32000, 4: 40000, 5: 57000}},
 "focus_lock": {"enable": "PIZStage-External sensor", "on": "1", "off": "0",
                "operation": "Focus-lock-Laser Operation",
                "qpd": {"x": "Analog Input-AnalogInput0", ...}},
 "unallocated": ["Booster enable fine", ...]}   # names only, not the noise
```

Same information, ~⅓ the tokens, and shaped so the model *cannot* mismatch a
laser to a trigger line.

## 3. No laser safety gate exists, and the risk gradient is inverted

`safety.py` guards XY, Z, exposure, channel presets, plugin motion, and the
filesystem. It has **nothing for illumination**. `safety_config.yaml`'s
`forbidden_properties` ships with exactly one entry (`Core.Initialize`), and
`check_device_property` (`safety.py:169`) only re-applies numeric guards for the
current focus/camera/XY device. A laser is just another property write.

What that permitted, unprompted, with no confirmation:

- `[17]` `Luxx638.Laser Operation Select = On` — microclaw announced its plan
  and turned on a Class-3B 638 nm laser in the same turn, never waiting for a
  reply.
- `[59]`→`[63]`→`[143]` power escalated **1 % → 5 % → 10 % → 25 %**.
- `[52]` the user says *"I will now manually disengage the BFP optics"* — with
  the 638 emitting at 5 %. Microclaw replies *"go ahead"* `[53]` and does not
  offer to shutter it. **A human put their hands in a live beam path.**
- `[149]` Z driven to 10 µm with the laser still on at 25 %.
- The laser stayed on for **136 messages**, from `[17]` to `[153]`. Microclaw
  mentioned turning it off 14 times but never did until explicitly ordered.

Contrast that with what it *did* ask permission for: `mark_position` (three
times, `[85]`, `[97]`, `[137]`), exporting a dataset, and reverting an exposure.
All reversible, all zero-risk, and `mark_position` is a tool it already has
(`tools_schema.py`). It asks about bookkeeping and acts on photons.

**Fix.**

1. **`safety.py`: add an `illumination` constraint class.**
   ```yaml
   illumination:
     # Properties that gate light. Turning one ON requires confirmation.
     shutter_properties:
       - {device: Luxx638, property: Laser Operation Select, on_value: "On"}
     max_power_percent: 30.0        # refuse writes above this
     require_confirm_on_enable: true
     auto_off_on_idle_s: 300        # shutter if no tool call for 5 min
   ```
   Route enable-writes through `CONFIRM_FN` (the same in-code blocking gate
   already used for `save_knowledge` and hook saves — `tools.py:19-31`), so a
   prompt-injection or a confused model *cannot* lase without a human `y`.
2. **Power ratchet.** Refuse a power increase of more than N× in one write, and
   log every illumination change with a timestamp. 1 %→25 % should take a
   deliberate step, not a single unconfirmed `set_device_property`.
3. **Session teardown.** On `KeyboardInterrupt`/normal exit in `__main__.py`,
   shutter every known illumination property. Right now, if the user had closed
   the terminal at `[151]`, the 638 would still be on at 25 %.
4. **Prompt.** `SYSTEM_PROMPT` says nothing about lasers (grep: `laser` appears
   only in `smlm_docs.py`, `htsmlm_docs.py`, `emu_manager.py`,
   `tools_schema.py`). Add: *"Illumination is the one irreversible thing you
   control — it bleaches sample and endangers eyes. Shutter the excitation
   before any user action described as manual/physical, and before any
   long-running non-imaging operation. Never raise power without stating the
   before/after values."*
5. **Invert the asking.** Prompt guidance: *"Do not ask permission for
   reversible bookkeeping (`mark_position`, `get_*`). Do ask — and wait —
   before enabling illumination, moving Z on an unverified metric, or
   overwriting data."*

## 4. `run_autofocus` shows the model a curve that did not make the decision

At `[123]` microclaw called `run_autofocus(z_range_um=20, z_step_um=0.5)` from
`z = 45.212`. It got back an 11-point curve spanning **37.712 → 42.712 µm** —
a 5 µm window that *does not contain the starting Z*, under a parameter that
said 20 µm. Microclaw never noticed the mismatch.

Here is exactly why, from `tools.py:526-529` and `autofocus.py:52-71`:

```python
coarse_step = max(z_step_um * 5, 1.0)        # = max(2.5, 1.0) = 2.5
# coarse: arange(35.212, 55.212, 2.5) -> 9 pts; picks best = 40.212
lo = max(40.212 - 2.5, 35.212) = 37.712
hi = min(40.212 + 2.5, 55.212) = 42.712
# fine:   arange(37.712, 42.712, 0.5) -> 11 pts   <-- exactly what was reported
return sweep_autofocus(...)                   # <-- only the FINE result escapes
```

`coarse_then_fine_autofocus` returns *only the fine `AutofocusResult`*. The
coarse pass — which chose 40.212 µm over the true focus near 45.2 µm, and which
constrained the fine window to ±2.5 µm around that mistake — is discarded. So:

- The **coarse pass made the error**, and its curve is invisible. Microclaw
  audited the fine curve, correctly called it "flat / noise" `[125]`, but
  diagnosed the wrong cause. It could not have found the real one.
- `settled` (`autofocus.py:48`) means *"the peak is interior to the **fine**
  sweep"*. Index 3 of 11 → `settled: True`. The payload therefore said
  `"settled": true, "warning": null` about a result that was 6 µm wrong.
- `sweep_autofocus` **unconditionally moves the stage to `best_z`**
  (`autofocus.py:41`) and never restores. The user was left out of focus and
  had to notice it themselves at `[130]`: *"you're out of focus, go back to the
  position before autofocus."*

**Fix.**

- Return **both passes**: `{"coarse": {...}, "fine": {...}, "z_range_um": 20}`.
  Assert in `run_autofocus` that `min(z_positions) <= current_z <=
  max(z_positions)` for the coarse pass, and surface the coarse window in the
  payload so a range/curve mismatch is self-evident.
- **Gate the move on peak quality.** Compute a contrast ratio
  `(max - median) / (max - min)` and a peak prominence; here the fine curve
  spanned 35 668→37 875 (**~6 % variation, no peak**). If the ratio is below
  threshold, **do not move**, restore the entry Z, and return
  `{"converged": false, "reason": "focus metric is flat (6% range); ..."}`.
- Rename `settled` → `peak_interior_to_fine_sweep`, or better, replace it with
  `converged` computed from prominence. A boolean the model reads as "it
  worked" must not mean "argmax wasn't at index 0".
- Always report `entry_z_um` so the model can trivially undo.
- `np.arange` on floats (`autofocus.py:29`) accumulates error; use
  `np.linspace` with an explicit point count.

## 5. Focus lock was never checked, and autofocus may have been fighting it

Microclaw noted at `[41]` *"PIZStage has focus locking configured"* and treated
it as a reason to be cautious — then ran a 20-point Z sweep `[123]` without
reading its state. The EMU config it held gives the exact property:

```
Z stage focus locking = PIZStage-External sensor   (On = "1", Off = "0")
Focus-lock operation  = Focus-lock-Laser Operation
QPD X/Y/Z             = Analog Input-AnalogInput0/2/1
```

If the external-sensor lock was engaged, every `core.set_position(z)` in
`sweep_autofocus` was being actively opposed by the piezo controller's servo
loop — which is a candidate explanation for the flat, structureless metric
curve, independent of the faint-signal explanation microclaw offered.

Worse, at `[141]` microclaw ran its own SMLM pre-flight checklist and wrote:

> "Focus lock engaged? (You confirmed focus looks fine at Z=45.2 µm.)"

That is a checklist item answered with irrelevant evidence — "the image looks
sharp" is not "the lock is engaged". It had a one-call way to actually check
and did not use it.

**Fix.**

- `get_focus_lock_state()` / `set_focus_lock(enabled)` tools, resolved through
  the EMU map (§2), returning `{"engaged": bool, "qpd": {...}}`.
- `run_autofocus` must **read the lock, refuse to sweep while engaged** (or
  disengage → sweep → re-engage, explicitly and reversibly, reporting both
  transitions).
- Make the SMLM checklist machine-checked rather than prose the model
  self-attests to. `smlm_docs.py` should name the tool for each item; a
  checklist the model can satisfy with vibes is not a checklist.

## 6. No way to address a stage by name — the epi task was abandoned to the human

`[35]`–`[41]`: microclaw needed to move the `TIRF Stage` (SmarAct 1D, COM6) to
steer the beam to the pupil centre. It correctly established that
`move_stage_z`/`get_z_position` target only the **core focus device**
(PIZStage), correctly refused to guess, and then gave up:

> "I don't have a tool that addresses the TIRF SmarAct axis by name." `[41]`

That is true, and it is a ~10-line gap. MMCore has device-labelled overloads:
`core.set_position(label, pos)` / `core.get_position(label)` /
`core.set_relative_position(label, delta)`. The user ended up hand-driving the
Stage Control window `[42]` while microclaw held live view — the agent was
reduced to a light switch for the one task it was best suited to (iteratively
centring a spot in an image it can measure).

**Fix.**

- `list_stages()` → `{"focus": "PIZStage", "xy": "SmarActXY", "other_z":
  ["TIRF Stage", "SmarActZ"]}` via
  `core.get_loaded_devices_of_type(DeviceType.StageDevice)`.
- `get_stage_position(device)` / `move_named_stage(device, um, absolute)`,
  guarded by a **per-device** limits table in `safety_config.yaml` (the current
  `stage.z_min/z_max` is a single global pair and cannot express "PIZStage:
  0–200 µm, TIRF Stage: ±3 mm").
- Note the current global config (`x_max: 5000`) would have rejected this
  session's `x = 5726.5`; the lab machine is clearly running a different file.
  Ship a `safety_config.example.yaml` and make the real one required, so the
  repo default is never mistaken for a working rig's limits.

With §6 + §9, "move the beam to epi" becomes a closed loop microclaw can
actually run: snap → find the bright spot's centroid → step the TIRF stage →
repeat until the centroid reaches the pupil centre.

## 7. `snap_and_analyze` cannot display, cannot survive live view — and microclaw asserted otherwise

Two separate defects, one root cause: `snap_and_analyze` (`tools.py:474`) goes
through `snap_to_numpy` → `ctrl.core.snap_image()` (`image_analysis.py:69`),
which touches **only the core**, never `ctrl.studio`.

Meanwhile the sibling tool `snap_image` (`tools.py:50-52`) does
`ctrl.studio.live().snap(True)` and returns *"Image snapped and displayed in MM
viewer."* Microclaw called `snap_and_analyze` **12 times and `snap_image` zero
times** this session.

**Defect (a) — it crashes under live view.** At `[4]` `get_system_state`
returned `live_view: true`. At `[19]` microclaw snapped anyway and got a raw
16-line Java stack trace `[20]`:

```
Exception: java.lang.Exception: This operation can not be executed while
sequence acquisition is running.  mmcorej.MMCoreJJNI.CMMCore_snapImage(...)
```

`tools.py` already has the primitive to prevent this — `_bounce_live_if_on`
(`tools.py:89`), used by `set_roi` and `clear_roi`; and `run_autofocus`
(`tools.py:521-540`) has its own stop/restore. `snap_and_analyze` has neither.

**Defect (b) — it silently never reaches the viewer, and microclaw told the
user it did.** At `[102]` the user says *"I don't really see anything. Can you
open the snapshot you just took?"* Microclaw answers `[103]`:

> "The snapshot is already displayed in the Micro-Manager snap/live window on
> your screen — that's where every image I capture appears."

This is false. The user corrects it at `[104]`: *"the preview window is still
showing me the BFP. It hasn't changed since I manually adjusted the angle."*
Microclaw then "fixed" it by bouncing live view `[105]`–`[107]` — treating a
missing feature as a stuck window.

(Note this is **not** the known jPypeMM canvas limitation from
`design/12` — that was the in-process jPype branch. `main` is on the ZMQ
backend, where `studio.live().snap(true)` does repaint.)

**Fix.**

- Reimplement `snap_and_analyze` on `studio.live().snap(True)`, which returns
  `List<Image>` — display **and** pixels from **one** exposure:
  ```python
  images = ctrl.studio.live().snap(True)      # displays in the MM viewer
  image = _image_to_numpy(images.get(0))      # same photons, no second snap
  ```
  One exposure, no extra bleaching, viewer stays live. Keep `snap_to_numpy` as
  the headless path for `sweep_autofocus`, where display churn is unwanted.
- Add a `_pause_live` context manager and wrap every core-level camera op:
  ```python
  @contextmanager
  def _pause_live(ctrl):
      live = ctrl.studio.live()
      was_on = live.is_live_mode_on()
      if was_on: live.set_live_mode_on(False)
      try: yield
      finally:
          if was_on: live.set_live_mode_on(True)
  ```
- Translate the Java exception in `errors.py` into
  `"Live view is running; snap requires it stopped. Retry — microclaw will pause
  it automatically."` A 16-line JVM stack trace teaches the model nothing.
- Delete or clearly re-scope `snap_image` vs `snap_and_analyze`. Two tools that
  differ only in an invisible display side-effect is a trap the model fell into
  on its first call and never escaped.

## 8. No pixel size, no camera↔stage mapping → microclaw navigated blind

`get_pixel_size` returned `0.0` with a warning `[68]`. Microclaw then had to
find a cell by dead reckoning, and it went badly:

| Msg | Move | Microclaw's reading of the result |
|---|---|---|
| `[69]` | X −30 µm | "shifted the bright cluster rightward/downward" |
| `[73]` | Y −30 µm | "the Y stage direction is inverted relative to the image" |
| `[77]` | Y +40 µm | "Y +40 also kept the feature low" |
| `[81]` | — | "this is **not** a single cell… an illumination/field feature" then, one paragraph later, "there **is** a real labeled structure there — likely a cell" |

Three moves, three mutually inconsistent inferences about the axis mapping, and
a self-contradiction inside one message. The user had to adjudicate: *"what you
found is indeed a cell"* `[82]`.

Also unnoticed: the `Y −30` move at `[74]` returned `x = 5696.9` when X was
commanded to stay at `5695.8` — a **1.1 µm unrequested X excursion**, and the
`Y +40` landed at `645.7` when `644.2` was requested. `move_stage_xy` re-reads
the true position after `wait_for_device`, so the data was right there in the
tool result. Microclaw never compared requested to achieved.

**Fix.**

- **`calibrate_stage_to_camera()`**: snap, move a known ΔX, snap, phase-cross-
  correlate (`skimage.registration.phase_cross_correlation`), repeat for ΔY.
  Solves the 2×2 affine — yielding pixel size, rotation, *and* both axis flips
  in ~4 snaps. Cache it in the knowledge base keyed by objective + camera.
  This is the single highest-leverage addition in this document: it converts
  every "nudge and squint" loop into arithmetic.
- Have `move_stage_xy` return `{"requested": [...], "achieved": [...],
  "error_um": [1.1, 1.5]}` and let the prompt instruct the model to flag errors
  above a threshold. Settling error is real on this rig and currently invisible.
- When `pixel_size_um == 0.0`, `snap_and_analyze` should carry the warning too,
  not just `get_pixel_size` — the model asked once at `[68]` and had forgotten
  by `[89]`, where it sized an ROI in pixels and reasoned about µm.

## 9. Centring is done by eye, and the eye is inconsistent

Microclaw placed the 200×200 ROI by estimating from a thumbnail `[89]`:

> "The cell cluster center sits roughly at ~30% across (x ≈ 155 px) and ~80%
> down (y ≈ 410 px)"

Then it read the *same field* three different ways across three snaps:

- `[97]` "the punctate cluster is **well-centered** in the ROI"
- `[117]` "the cell **isn't clearly visible**; it just looks like noise"
- `[121]` "reasonably centered but… biased toward the **upper-right**"

`make_thumbnail` (`image_analysis.py:35`) *does* percentile-normalise (2 / 99.8),
so this is not a contrast bug — it is the model doing spatial statistics by
looking. For a sparse punctate SMLM field, that is the wrong instrument.

**Fix.** A `find_features()` tool that returns numbers, not a picture:

```python
{"n_spots": 41,
 "centroid_xy_px": [96, 88],          # intensity-weighted, in ROI coords
 "bbox_px": [12, 20, 180, 170],
 "offset_from_center_px": [-4, -12],
 "offset_from_center_um": [-0.4, -1.2],   # if §8 calibration is present
 "spot_density_per_um2": 0.31}
```

Backed by `skimage.feature.blob_log` or a simple threshold + `ndimage.center_of_
mass`. Then "is the cell centred?" `[114]` is answered by
`offset_from_center_px`, deterministically and identically every time — and
"centre the ROI on the cell" becomes one arithmetic step instead of the
guess-shift-resnap loop at `[89]`–`[96]`.

`spot_density_per_um2` doubles as the SMLM blinking-density check that
`smlm_docs.py` asks for and currently has no tool to answer.

## 10. `focus_metric` is not comparable across settings, but microclaw compares it anyway

`laplacian_variance` (`image_analysis.py:18`) is unnormalised: `var(laplace(img))`.
For a shot-noise-dominated image it scales with photon count, and for a crop it
scales with whatever happens to be in the crop. Both effects fire in this
session, at **constant focus** (`z ≈ 45.21` throughout):

| Msg | Condition | `focus_metric` |
|---|---|---|
| `[58]` | full frame, laser 1 % | 8 602 |
| `[62]` | full frame, laser 5 % | 13 921 |
| `[66]` | full frame, laser 10 % | 21 142 |
| `[92]` | **200×200 ROI**, laser 5 % | 29 107 |

A 2.5× swing from laser power and a 3.4× jump from cropping, with the focal
plane never moving. Microclaw treated rising `focus_metric` as evidence of
improving image quality at `[63]`/`[67]`, and its autofocus (§4) inherits the
same metric.

**Fix.**

- Use a normalised metric: `var(laplace(img)) / mean(img)**2`, or Brenner /
  Tenengrad normalised by mean². Subtract the camera offset first (Evolve512
  baseline ≈ 400, plainly visible in every `mean_intensity` here).
- **Stamp the metric with its comparability key** in the payload:
  `{"focus_metric": 1.83, "metric_valid_for": {"roi": [55,270,200,200],
  "exposure_ms": 200, "binning": 1}}`, and instruct the model never to compare
  metrics across differing keys. A bare float invites exactly the cross-setting
  comparison microclaw made.

## 11. `get_device_property_info` leaks a memory address where the type should be

Every call this session returned:

```json
"type": "mmcorej_PropertyType object at 0x000001B6FFB4DFD0>"
```

`tools.py:260`:

```python
prop_type = str(ctrl.core.get_property_type(device, property)).split(".")[-1]
```

That assumed a Python enum (`PropertyType.Float` → `"Float"`). Over the ZMQ
bridge it is a pyjavaz proxy whose `repr` is
`<pyjavaz.…mmcorej_PropertyType object at 0x…>`; `.split(".")[-1]` slices it
mid-repr, leaving the dangling `>`. The field is pure noise — a nondeterministic
heap address injected into context on **every property inspection** (7× here),
poisoning the prompt cache along the way.

Microclaw consequently never learned any property's type, and inferred
`Float`-ness from `current_value` strings like `"1.0012"`.

**Fix.** Resolve the Java enum properly:

```python
_PROP_TYPES = {0: "Undef", 1: "String", 2: "Float", 3: "Integer"}
prop_type = _PROP_TYPES.get(int(ctrl.core.get_property_type(device, property).swigValue()), "Unknown")
```

(exact accessor to confirm against pyjavaz — `.swigValue()`, `.value()`, or
`int()` on the proxy). Add a test that asserts `type` is in the enum set; the
current value would pass any test that only checks the key exists.

---

## Suggested ordering

| # | Change | Cost | Why first |
|---|---|---|---|
| §3 | Illumination gate + session teardown | S | Only item with a human-safety failure mode |
| §2a | Fix `_parse_properties` (separator + suffixes) | S | Pure bug; every EMU rig has been getting a half-parsed dict |
| §7 | `snap_and_analyze` displays + pauses live | S | Fixes a user-visible lie and a hard crash |
| §4 | Autofocus: return both passes, gate the move | S | Currently moves hardware on noise and says `warning: null` |
| §11 | Property `type` enum | XS | One line; stops context poisoning |
| §1/§2 | Structured EMU map + `run_timelapse` trigger pre-flight | M | Prevents silent data loss; depends on §2a |
| §6 | Named-stage tools + per-device limits | M | Unblocks a whole class of tasks (TIRF/epi) |
| §8 | `calibrate_stage_to_camera` | M | Highest leverage; makes navigation arithmetic |
| §9 | `find_features` centroid tool | M | Kills the eyeball loop; feeds §8 and SMLM density |
| §10 | Normalised metric + comparability key | S | Correctness of every focus judgement |
| §5 | Focus-lock tools + machine-checked checklist | M | Depends on §2 |

§3, §2a, §7, §4, and §11 are independent, small, and each fixes something that
is actively wrong right now. They are the natural first branch.

Note the dependency spine: **§2a → §2 → {§1 pre-flight, §5}**. The EMU map has to
parse before anything can be built on it. And **§8 → §9 → `center_feature`** is
the chain that turns "nudge and squint" into a closed loop — it is what would
have let microclaw do the epi task (§6) itself.

---

# Appendix — proposed code stubs

Untested sketches, written against the signatures in `tools.py`, `safety.py`,
`autofocus.py`, `emu_manager.py`, `image_analysis.py` as of `e8ee3cc`. Three
things needed verifying before any of this is trusted; all three were settled
by `design/14-demo-spike.py` (three runs, 2026-07-08, MM demo config + EMU
"Simple UI", MMCore 12.5.0 — full output in `14-demo-spike-output.txt`):

- **(V1) CONFIRMED.** `studio.live().snap(True)` returns a `java.util.ArrayList`
  (`.size()` / `.get(0)`); the element's accessors are `get_width`,
  `get_height`, `get_bytes_per_pixel`, **`get_num_components`** (not
  `get_number_of_components` as the §7 stub assumed), `get_raw_pixels`. Raw
  pixels arrive as a ready-made numpy `uint16` array (`np.asarray` suffices)
  with a mean identical to the `core.snap_image()` path.
  **New finding, worse than assumed:** calling `live().snap(True)` while live
  mode is ON does not throw — it **never returns**, and because pyjavaz has one
  bridge per port with a communication lock held per call, the whole process
  wedges (unrecoverable in-process; spike runs 1–2 both jammed on hangs).
  `snap_and_analyze` must therefore check `is_live_mode_on()` and pause live
  *before* the call — never probe by calling. `core.snap_image()` under live
  throws with the needle `"sequence acquisition is running"` intact over ZMQ,
  so the `errors.py` hint table matches as written.
- **(V2) CONFIRMED.** The int accessor is `swig_value()` and the ordinals match
  §11's table (String=1, Float=2, Integer=3, verified against known-typed demo
  properties). Better: **`to_string()` returns the enum name directly**
  (`'Float'`), so the fix needs no ordinal table at all. The current
  `tools.py:260` expression was confirmed to leak a heap address.
- **(V3) CONFIRMED.** `core.get_position(label)`, `core.set_position(label,
  pos)` and `core.set_relative_position(label, delta)` all dispatch correctly
  over the bridge (demo `Z` moved +1 µm and restored exactly). For enumerating
  stages, `get_loaded_devices_of_type` requires a real `DeviceType` value: a
  plain int is rejected with a clean type error, the enum constants are NOT
  exposed as static fields on the shadow, and
  `DeviceType.swig_to_enum(5)` (via `_new_static_java_class`) works — but the
  get-class request hung once (spike run 2) and not on rerun, so **prefer the
  static-class-free route**: classify per device with
  `core.get_device_type(label)` + `.to_string()` / `.swig_value()`
  (`StageDevice`=5, `XYStageDevice`=6), which needs no `JavaClass` at all.
- **(§2a) CONFIRMED on a second real config.** The demo Simple UI config has 31
  property entries: **0** contain `::`, 8 are `" - On/Off value"`, 6 are
  `" state N"`. Current `_parse_properties`: device populated on **0** entries,
  14 metadata keys leaked. Proposed parser: 3/3 allocated entries resolved
  against the live device list. **New finding:** UIProperty *names* are
  plugin-specific — htSMLM says `"Laser 3 enable"`, Simple UI says
  `"Laser0 on/off"` — so `_parse_properties` generalises but `build_emu_map`'s
  semantic regexes must key off `plugin_name` (or tolerate both shapes).

Each stub is tagged with the section it implements.

## §11 — property type enum (`tools.py:260`)

Smallest change in the document; do this first.

```python
# microclaw/tools.py

# mmcorej.PropertyType enum ordinals. Verify against mmcorej.PropertyType.
_PROP_TYPES = {0: "Undef", 1: "String", 2: "Float", 3: "Integer"}


def _property_type_name(core, device: str, prop: str) -> str:
    """Resolve the MM PropertyType enum to a name.

    Over the ZMQ bridge get_property_type() returns a pyjavaz proxy whose repr is
    `<pyjavaz...mmcorej_PropertyType object at 0x...>`. The previous
    `str(...).split(".")[-1]` sliced that repr mid-string and leaked a heap
    address into the model's context on every call.
    """
    raw = core.get_property_type(device, prop)
    for accessor in ("swigValue", "value"):          # (V2)
        if hasattr(raw, accessor):
            return _PROP_TYPES.get(int(getattr(raw, accessor)()), "Unknown")
    try:
        return _PROP_TYPES.get(int(raw), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"
```

```python
# tests/test_tools.py
def test_property_type_is_an_enum_name(ctrl):
    info = get_device_property_info(ctrl, guard, "Camera", "Exposure")
    assert info["type"] in {"Undef", "String", "Float", "Integer"}
    assert "0x" not in info["type"]        # would have caught the original bug
```

## §7 — `snap_and_analyze` displays, and pauses live view

```python
# microclaw/tools.py
from contextlib import contextmanager


@contextmanager
def _pause_live(ctrl: MicroscopeController):
    """Stop live mode for the duration of a core-level camera op, then restore.

    core.snap_image() throws "This operation can not be executed while sequence
    acquisition is running" if live mode is on. run_autofocus already open-codes
    this; snap_and_analyze did not, so the agent's first snap of every session
    crashed with a 16-line Java stack trace.
    """
    live = ctrl.studio.live()
    was_on = bool(live.is_live_mode_on())
    if was_on:
        live.set_live_mode_on(False)
    try:
        yield was_on
    finally:
        if was_on:
            live.set_live_mode_on(True)
```

```python
# microclaw/image_analysis.py

def snap_to_numpy_displayed(ctrl) -> np.ndarray:
    """Snap via studio.live().snap(True): displays in the MM viewer AND returns pixels.

    One exposure, not two — important because the sample bleaches. The headless
    snap_to_numpy() stays as-is for sweep_autofocus, where repainting the viewer
    20 times is churn.
    """
    images = ctrl.studio.live().snap(True)          # (V1) ArrayList, displays
    img = images.get(0)
    w, h = int(img.get_width()), int(img.get_height())
    bpp = int(img.get_bytes_per_pixel())
    n_comp = int(img.get_num_components())          # (V1) NOT get_number_of_components
    pix = img.get_raw_pixels()                      # (V1) arrives as a numpy array
    # Same dtype-derivation as snap_to_numpy(); factor that out rather than
    # duplicating it — component count is decisive (RGB32 = 4 x uint8).
    return _reshape_pixels(pix, w, h, bpp, n_comp)
```

```python
# microclaw/tools.py

def snap_and_analyze(ctrl, guard, return_thumbnail=False, thumbnail_size=512, display=True):
    """Snap, display in the MM viewer, and return stats + optional thumbnail."""
    if display:
        image = snap_to_numpy_displayed(ctrl)       # live-safe: snap() handles it
    else:
        with _pause_live(ctrl):
            image = snap_to_numpy(ctrl)
    stats = compute_stats(image)
    roi = ctrl.core.get_roi()
    text_payload = {
        "z_um": round(ctrl.core.get_position(), 3),
        "displayed_in_mm_viewer": display,          # so the model never has to guess
        **_focus_metric_payload(image, ctrl, roi),  # see §10
        "mean_intensity": round(stats.mean_intensity, 1),
        "max_intensity": round(stats.max_intensity, 1),
        "saturated_fraction": round(stats.saturated_fraction, 4),
    }
    if ctrl.core.get_pixel_size_um() == 0.0:
        text_payload["warning"] = (
            "No pixel-size calibration: image-pixel offsets cannot be converted "
            "to stage µm. Call calibrate_stage_to_camera() first."
        )
    ...
```

Also collapse the `snap_image` / `snap_and_analyze` trap. Either delete
`snap_image` (its only distinguishing feature is the display side-effect that
`snap_and_analyze` now has), or rename to make the difference legible:
`snap_and_display` vs `snap_headless`.

And translate the Java exception rather than forwarding the stack trace:

```python
# microclaw/errors.py

_JAVA_HINTS = (
    ("sequence acquisition is running",
     "Live view is running; a snap requires it stopped. microclaw pauses live "
     "automatically — retry the call."),
    ("Device not found",
     "No such device label. Call list_devices() for the loaded labels."),
)


def humanize_java_error(exc: Exception) -> str:
    """Map a pycromanager/Java exception onto a one-line actionable message.

    A 16-line JVM stack trace teaches the model nothing and costs ~200 tokens.
    """
    text = str(exc)
    for needle, hint in _JAVA_HINTS:
        if needle in text:
            return hint
    return text.splitlines()[0] if text else repr(exc)
```

## §4 — autofocus returns both passes and refuses to move on a flat curve

```python
# microclaw/autofocus.py
from dataclasses import dataclass


@dataclass
class SweepResult:
    z_positions: list[float]
    metric_values: list[float]
    best_z_um: float
    peak_interior: bool          # was `settled` -- renamed; means only "argmax not at an edge"


@dataclass
class AutofocusResult:
    coarse: SweepResult
    fine: SweepResult | None
    entry_z_um: float
    final_z_um: float
    converged: bool
    reason: str | None
    moved: bool


def _prominence(metric: list[float]) -> float:
    """Peak prominence normalised to the curve's dynamic range, in [0, 1].

    A real focus curve peaks hard; noise is flat. The amr_test fine sweep ran
    35668..37875 -> ~0.06, i.e. no peak at all, yet the tool reported
    settled=true, warning=null and moved the stage 6 um.
    """
    import numpy as np
    m = np.asarray(metric, dtype=float)
    if m.max() <= m.min():
        return 0.0
    return float((m.max() - np.median(m)) / (m.max() - m.min()))


MIN_PROMINENCE = 0.35            # tune on real curves; 0.06 must fail


def sweep_autofocus(ctrl, z_start_um, z_end_um, z_step_um, settle_ms=50,
                    metric_fn=laplacian_variance, move_to_best=True) -> SweepResult:
    """Sweep Z and measure the focus metric. Only moves to best_z if move_to_best."""
    focus_device = ctrl.core.get_focus_device()
    n = int(round((z_end_um - z_start_um) / z_step_um)) + 1
    z_positions = list(np.linspace(z_start_um, z_end_um, n))   # not arange: no float drift
    metric_values = []
    for z in z_positions:
        ctrl.core.set_position(z)
        ctrl.core.wait_for_device(focus_device)
        if settle_ms > 0:
            time.sleep(settle_ms / 1000.0)
        metric_values.append(metric_fn(snap_to_numpy(ctrl)))

    best_idx = int(np.argmax(metric_values))
    best_z = z_positions[best_idx]
    if move_to_best:
        ctrl.core.set_position(best_z)
        ctrl.core.wait_for_device(focus_device)
    return SweepResult(z_positions, metric_values, best_z,
                       0 < best_idx < len(z_positions) - 1)


def coarse_then_fine_autofocus(ctrl, z_range_um, coarse_step_um, fine_step_um,
                               settle_ms=50, min_prominence=MIN_PROMINENCE) -> AutofocusResult:
    """Two-pass autofocus that reports BOTH passes and restores Z on a bad result.

    The old version returned only the fine SweepResult, so the coarse pass -- the
    one that actually chose the focal plane, and constrained the fine window to
    +/- coarse_step around its choice -- was invisible to the caller. In amr_test
    the coarse pass picked 40.212 um when true focus was ~45.2 um, and the model
    audited an 11-point fine curve it had no way of knowing was a consequence.
    """
    entry_z = ctrl.core.get_position()
    lo_bound, hi_bound = entry_z - z_range_um / 2, entry_z + z_range_um / 2

    coarse = sweep_autofocus(ctrl, lo_bound, hi_bound, coarse_step_um,
                             settle_ms, move_to_best=False)
    if _prominence(coarse.metric_values) < min_prominence:
        _restore(ctrl, entry_z)
        return AutofocusResult(coarse, None, entry_z, entry_z, converged=False,
                               moved=False,
                               reason=(f"Coarse focus metric is flat "
                                       f"(prominence {_prominence(coarse.metric_values):.2f} "
                                       f"< {min_prominence}). Z was NOT moved. Increase signal "
                                       f"(laser power / exposure), autofocus on the full frame "
                                       f"rather than a small ROI, or focus manually."))

    lo = max(coarse.best_z_um - coarse_step_um, lo_bound)
    hi = min(coarse.best_z_um + coarse_step_um, hi_bound)
    fine = sweep_autofocus(ctrl, lo, hi, fine_step_um, settle_ms, move_to_best=False)

    if _prominence(fine.metric_values) < min_prominence:
        _restore(ctrl, entry_z)
        return AutofocusResult(coarse, fine, entry_z, entry_z, converged=False,
                               moved=False, reason="Fine sweep is flat; Z was NOT moved.")

    _restore(ctrl, fine.best_z_um)
    return AutofocusResult(coarse, fine, entry_z, fine.best_z_um,
                           converged=True, moved=True, reason=None)


def _restore(ctrl, z: float) -> None:
    ctrl.core.set_position(z)
    ctrl.core.wait_for_device(ctrl.core.get_focus_device())
```

```python
# microclaw/tools.py

def run_autofocus(ctrl, guard, z_range_um, z_step_um, method="coarse_then_fine",
                  settle_ms=50, return_thumbnail=True):
    entry_z = ctrl.core.get_position()
    guard.check_z(entry_z - z_range_um / 2)
    guard.check_z(entry_z + z_range_um / 2)

    lock = get_focus_lock_state(ctrl, guard)          # see §5
    if lock.get("engaged"):
        return {"error": "Focus lock is engaged on the Z stage; a sweep would fight "
                         "the servo loop. Call set_focus_lock(enabled=false) first."}

    with _pause_live(ctrl):
        result = coarse_then_fine_autofocus(
            ctrl, z_range_um, max(z_step_um * 5, 1.0), z_step_um, settle_ms)

    payload = {
        "converged": result.converged,
        "moved": result.moved,
        "reason": result.reason,
        "entry_z_um": round(result.entry_z_um, 3),
        "final_z_um": round(result.final_z_um, 3),
        "z_range_um": z_range_um,
        # BOTH passes -- the caller can now see which one chose the plane.
        "coarse": {"z_positions": [round(z, 3) for z in result.coarse.z_positions],
                   "metric_curve": [round(v, 2) for v in result.coarse.metric_values],
                   "prominence": round(_prominence(result.coarse.metric_values), 3)},
        "fine": None if result.fine is None else {...},
    }
    # Invariant that would have surfaced the amr_test bug immediately.
    assert min(result.coarse.z_positions) <= entry_z <= max(result.coarse.z_positions)
    ...
```

```python
# tests/test_autofocus.py
def test_flat_metric_does_not_move_the_stage(fake_ctrl):
    """The amr_test regression: prominence 0.06 must not move Z."""
    fake_ctrl.metric_fn = lambda img: 36000 + random.uniform(-1100, 1900)
    fake_ctrl.core.set_position(45.212)
    result = run_autofocus(fake_ctrl, guard, z_range_um=20, z_step_um=0.5)
    assert result["converged"] is False
    assert result["moved"] is False
    assert fake_ctrl.core.get_position() == pytest.approx(45.212)


def test_coarse_window_contains_entry_z(fake_ctrl):
    fake_ctrl.core.set_position(45.212)
    r = run_autofocus(fake_ctrl, guard, z_range_um=20, z_step_um=0.5)
    zs = r["coarse"]["z_positions"]
    assert min(zs) <= 45.212 <= max(zs)          # the old fine-only curve failed this
```

## §3 — illumination gate

```python
# microclaw/safety.py

@dataclass
class IlluminationProperty:
    device: str
    property: str
    on_value: str = "On"
    off_value: str = "Off"


@dataclass
class IlluminationConstraints:
    """Gate for anything that emits light at the sample.

    Illumination is the only irreversible thing microclaw controls: it bleaches
    sample and endangers eyes. safety.py previously had no concept of it, so a
    Class-3B laser was one unconfirmed set_device_property away.
    """
    shutters: list[IlluminationProperty] = field(default_factory=list)
    power_properties: list[ForbiddenProperty] = field(default_factory=list)
    max_power_percent: Optional[float] = None
    max_power_step_factor: Optional[float] = None    # refuse 1% -> 25% in one write
    require_confirm_on_enable: bool = True
    auto_off_on_idle_s: Optional[int] = None


class SafetyGuard:
    def is_illumination_enable(self, device: str, prop: str) -> IlluminationProperty | None:
        return next((s for s in self._c.illumination.shutters
                     if s.device == device and s.property == prop), None)

    def check_illumination(self, core, device: str, prop: str, value: str,
                           confirm_fn=None) -> None:
        """Confirm-gate an enable, and ratchet-gate a power increase.

        Enforced in code, not just the prompt -- same reasoning as save_knowledge's
        blocking confirmation: a confused model or an injected instruction must not
        be able to lase without a human 'y'.
        """
        ill = self._c.illumination
        shutter = self.is_illumination_enable(device, prop)
        if shutter and value == shutter.on_value and ill.require_confirm_on_enable:
            if confirm_fn is None or not confirm_fn(
                    f"ENABLE ILLUMINATION: {device}.{prop} = {value!r}\n"
                    f"This will emit light at the sample."):
                raise SafetyViolation(f"User declined to enable {device}.{prop}.")

        if any(p.device == device and p.property == prop for p in ill.power_properties):
            try:
                new = float(value)
            except (TypeError, ValueError):
                return
            if ill.max_power_percent is not None and new > ill.max_power_percent:
                raise SafetyViolation(
                    f"{new:.1f}% exceeds illumination.max_power_percent "
                    f"({ill.max_power_percent:.1f}%).")
            if ill.max_power_step_factor is not None:
                old = float(core.get_property(device, prop) or 0.0)
                if old > 0 and new / old > ill.max_power_step_factor:
                    raise SafetyViolation(
                        f"Power increase {old:.1f}% -> {new:.1f}% exceeds the "
                        f"{ill.max_power_step_factor}x per-write ratchet. Step up gradually.")

    def shutter_all(self, core) -> list[str]:
        """Best-effort: drive every known illumination enable to its off value."""
        done = []
        for s in self._c.illumination.shutters:
            try:
                core.set_property(s.device, s.property, s.off_value)
                done.append(f"{s.device}.{s.property}")
            except Exception:                       # teardown must not raise
                pass
        return done
```

```yaml
# safety_config.yaml  (htSMLM rig; can be generated from the EMU map -- see §2)
illumination:
  require_confirm_on_enable: true
  max_power_percent: 30.0
  max_power_step_factor: 3.0        # 1% -> 25% is 25x: refused
  auto_off_on_idle_s: 300
  shutters:
    - {device: Luxx405,   property: Laser Operation Select, on_value: "On", off_value: "Off"}
    - {device: Luxx488,   property: Laser Operation Select, on_value: "On", off_value: "Off"}
    - {device: Luxx638,   property: Laser Operation Select, on_value: "On", off_value: "Off"}
    - {device: Cobolt561, property: Laser,                  on_value: "On", off_value: "Off"}
  power_properties:
    - {device: Luxx405, property: "Laser Power Set-point Select [%]"}
    - {device: Luxx488, property: "Laser Power Set-point Select [%]"}
    - {device: Luxx638, property: "Laser Power Set-point Select [%]"}
```

Wire the confirmation through the gate that already exists, and add teardown:

```python
# microclaw/tools.py
def set_device_property(ctrl, guard, device, property, value):
    guard.check_device_property(ctrl.core, device, property, value)
    guard.check_illumination(ctrl.core, device, property, value, confirm_fn=CONFIRM_FN)
    ...
```

```python
# microclaw/__main__.py -- wrap the REPL so no exit path leaves a laser on
    try:
        while True:
            ...
    finally:
        write_history(history_fn_name, history, args.save_history)
        shuttered = guard.shutter_all(ctrl.core)
        if shuttered:
            print(f"[microclaw] Illumination off: {', '.join(shuttered)}")
```

Note the current `except (EOFError, KeyboardInterrupt): break` at
`__main__.py:60` only catches those two at the `input()` call — a crash inside
`run_agent` skips the final `write_history` *and* would skip teardown. The
`try/finally` fixes both.

Prompt additions (`agent.py:39`):

```
- Illumination is the only irreversible thing you control: it bleaches sample and
  endangers eyes. Shutter the excitation before any user action described as
  manual, physical, or "I will now ..." (swapping optics, touching the stage), and
  before any long non-imaging operation. Never raise laser power without stating
  the before/after values in the same message.
- Do NOT ask permission for reversible bookkeeping (mark_position, get_*, set_roi).
  DO ask, and wait for a reply, before enabling illumination, raising power, moving
  Z on an unverified focus metric, or overwriting a dataset.
```

## §2 — EMU parser fix + structured map

```python
# microclaw/emu_manager.py

# Real htSMLM/EMU configs use "Device-Property" and " - On value" / " state N".
# The previous "::" separator and (" on", " off", ...) suffixes matched 0 of 120
# entries on the amr_test rig, so device/property were never populated.
_ON_OFF_RE = re.compile(r"^(?P<base>.+?) - (?P<which>On|Off) value$")
_STATE_RE = re.compile(r"^(?P<base>.+?) state (?P<idx>\d+)$")
_RESCALE_SUFFIXES = (" slope", " offset")


def _split_device_property(mm_str: str, device_labels: Sequence[str]) -> tuple[str, str] | None:
    """Split "Focus-lock-Enable Fine" into ("Focus-lock", "Enable Fine").

    Device labels contain hyphens ("Focus-lock", "MicroFPGA-Hub", "Thorlabs ELL9-1"),
    and "Thorlabs ELL9" is a prefix of "Thorlabs ELL9-1", so a naive split("-", 1)
    is wrong and even a greedy match must take the LONGEST matching label. There is
    no correct parse without the loaded-device list.
    """
    matches = [d for d in device_labels if mm_str.startswith(d + "-")]
    if not matches:
        return None
    device = max(matches, key=len)
    return device, mm_str[len(device) + 1:]


def _parse_properties(raw: dict[str, str], device_labels: Sequence[str]) -> dict[str, dict]:
    """Fold ' - On/Off value' and ' state N' metadata into their parent UIProperty."""
    base: dict[str, str] = {}
    meta: dict[str, dict] = {}
    for key, value in raw.items():
        if (m := _ON_OFF_RE.match(key)):
            meta.setdefault(m["base"], {})[m["which"].lower()] = value
        elif (m := _STATE_RE.match(key)):
            meta.setdefault(m["base"], {}).setdefault("states", {})[int(m["idx"])] = value
        elif key.endswith(_RESCALE_SUFFIXES):
            suffix = next(s for s in _RESCALE_SUFFIXES if key.endswith(s))
            meta.setdefault(key[: -len(suffix)], {})[suffix.strip()] = value
        else:
            base[key] = value

    result = {}
    for name, mm_str in base.items():
        entry = {"mm_property_string": mm_str}
        if (split := _split_device_property(mm_str, device_labels)):
            entry["device"], entry["property"] = split
        entry.update(meta.get(name, {}))
        result[name] = entry
    return result
```

```python
# microclaw/emu_manager.py -- semantic view over the parsed properties

_LASER_RE = re.compile(r"^Laser (?P<i>\d+) (?P<field>enable|power percentage)$")
_TRIG_RE = re.compile(r"^Laser trigger (?P<i>\d+) (?P<field>mode|sequence|pulse duration)$")
_PLACEHOLDER = {"Unallocated", "Enter value"}


def build_emu_map(props: dict[str, dict]) -> dict:
    """Semantic view: slot -> laser, filter-wheel state table, focus lock.

    Pairs 'Laser i ...' with 'Laser trigger i ...' by SLOT INDEX. In amr_test the
    agent inferred "Luxx638 = index 2" from device naming order and read the
    Cobolt561's trigger line (Mode2) instead of the 638's (Mode3), then ran a
    100-frame acquisition on the unverified line. Nothing may infer a slot index.
    """
    allocated = {k: v for k, v in props.items()
                 if v["mm_property_string"] not in _PLACEHOLDER}
    lasers: dict[int, dict] = {}
    for name, v in allocated.items():
        if (m := _LASER_RE.match(name)):
            key = "enable" if m["field"] == "enable" else "power_pct"
            lasers.setdefault(int(m["i"]), {})[key] = v
        elif (m := _TRIG_RE.match(name)):
            lasers.setdefault(int(m["i"]), {})[
                "trigger_" + m["field"].replace(" ", "_")] = v

    fw = allocated.get("Filter wheel position")
    focus_lock = allocated.get("Z stage focus locking")
    return {
        "lasers": lasers,                       # {3: {"enable": {device: "Luxx638", ...}, ...}}
        "filter_wheel": fw and {**fw, "states": fw.get("states", {})},
        "focus_lock": focus_lock,
        "unallocated": sorted(set(props) - set(allocated)),   # names only, not the noise
    }


def resolve_emu_device(props: dict[str, dict], semantic_name: str) -> dict:
    """'Laser 3 enable' -> {'device': 'Luxx638', 'property': 'Laser Operation Select'}."""
    entry = props.get(semantic_name)
    if entry is None or "device" not in entry:
        raise KeyError(f"'{semantic_name}' is not an allocated EMU property.")
    return {"device": entry["device"], "property": entry["property"]}
```

```python
# microclaw/tools.py -- pre-flight that would have caught the §1 bug

def _assert_excitation_will_fire(ctrl, laser_slot: int) -> None:
    """Refuse an acquisition whose excitation laser is gated off at the trigger."""
    emu = build_emu_map(_cached_emu_properties(ctrl))
    trig = emu["lasers"].get(laser_slot, {}).get("trigger_mode")
    if trig is None:
        return                                  # non-EMU rig; nothing to assert
    mode = ctrl.core.get_property(trig["device"], trig["property"])
    if str(mode).startswith("0"):               # "0 - Off"
        raise SafetyViolation(
            f"Laser slot {laser_slot} trigger mode is {mode!r}: it will NOT emit during "
            f"the acquisition. Every frame would be blank. Set {trig['device']}."
            f"{trig['property']} to a firing mode (e.g. '4 - Follow') first.")


def run_timelapse(ctrl, guard, n_frames, interval_s, exposure_ms, save_dir, name,
                  laser_slot: int | None = None):
    if laser_slot is not None:
        _assert_excitation_will_fire(ctrl, laser_slot)
    ...
```

```python
# tests/test_emu_manager.py -- fixtures must use the REAL format
REAL = {                                    # verbatim shapes from the amr_test config
    "Laser 3 enable": "Luxx638-Laser Operation Select",
    "Laser 3 enable - On value": "On",
    "Filter wheel position": "Servos-Position3",
    "Filter wheel position state 3": "32000",
    "Focus-lock enable fine": "Focus-lock-Enable Fine",
    "Two-state device 1": "Thorlabs ELL9-1-Label",
}
DEVICES = ["Luxx638", "Servos", "Focus-lock", "Thorlabs ELL9", "Thorlabs ELL9-1"]


def test_hyphenated_device_labels_split_on_the_longest_match():
    p = _parse_properties(REAL, DEVICES)
    assert p["Focus-lock enable fine"]["device"] == "Focus-lock"      # not "Focus"
    assert p["Two-state device 1"]["device"] == "Thorlabs ELL9-1"     # not "Thorlabs ELL9"


def test_metadata_is_folded_into_the_parent_property():
    p = _parse_properties(REAL, DEVICES)
    assert p["Laser 3 enable"]["on"] == "On"
    assert p["Filter wheel position"]["states"][3] == "32000"
    assert "Laser 3 enable - On value" not in p        # 68/120 keys used to leak


def test_laser_slot_pairs_enable_with_its_own_trigger_line():
    """The §1 regression: slot 3 is Luxx638; slot 2 is Cobolt561."""
    emu = build_emu_map(_parse_properties(FULL_AMR_TEST_CONFIG, DEVICES))
    assert emu["lasers"][3]["enable"]["device"] == "Luxx638"
    assert emu["lasers"][3]["trigger_mode"]["property"] == "Mode3"
    assert emu["lasers"][2]["enable"]["device"] == "Cobolt561"
```

## §6 — named-stage tools

```python
# microclaw/tools.py

def list_stages(ctrl, guard) -> dict:
    """Every stage device, and which one the core's Z/XY tools actually drive.

    move_stage_z/get_z_position address ONLY core.get_focus_device(). In amr_test
    the TIRF beam-steering axis (SmarAct 1D on COM6) was unreachable, so the epi
    task was handed back to the human.
    """
    from pycromanager import DeviceType          # verify import path
    single = _str_vector(ctrl.core.get_loaded_devices_of_type(DeviceType.StageDevice))
    xy = _str_vector(ctrl.core.get_loaded_devices_of_type(DeviceType.XYStageDevice))
    focus = ctrl.core.get_focus_device()
    return {
        "focus_device": focus,
        "xy_device": ctrl.core.get_xy_stage_device(),
        "single_axis_stages": single,
        "other_single_axis": [d for d in single if d != focus],
        "note": "move_stage_z targets focus_device only; use move_named_stage for the rest.",
    }


def get_stage_position(ctrl, guard, device: str) -> dict:
    return {"device": device, "position_um": round(float(ctrl.core.get_position(device)), 4)}  # (V3)


def move_named_stage(ctrl, guard, device: str, um: float, absolute: bool = True) -> dict:
    """Move a stage addressed by label, guarded by a PER-DEVICE limits table."""
    current = float(ctrl.core.get_position(device))
    target = um if absolute else current + um
    guard.check_named_stage(device, target)        # NOT the global z_min/z_max
    ctrl.core.set_position(device, target)         # (V3)
    ctrl.core.wait_for_device(device)
    achieved = float(ctrl.core.get_position(device))
    return {"device": device, "requested_um": round(target, 4),
            "achieved_um": round(achieved, 4),
            "error_um": round(achieved - target, 4)}
```

```python
# microclaw/safety.py
@dataclass
class NamedStageLimits:
    device: str
    min_um: Optional[float] = None
    max_um: Optional[float] = None


class SafetyGuard:
    def check_named_stage(self, device: str, pos: float) -> None:
        """A single global stage.z_min/z_max cannot express 'PIZStage: 0-200 um,
        TIRF Stage: +/-3000 um'. Absent an entry, refuse -- fail closed."""
        lim = next((l for l in self._c.named_stages if l.device == device), None)
        if lim is None:
            raise SafetyViolation(
                f"No limits configured for stage '{device}'. Add a named_stages entry "
                f"to safety_config.yaml before microclaw may move it.")
        if lim.min_um is not None and pos < lim.min_um:
            raise SafetyViolation(f"{device}={pos:.2f} um below minimum ({lim.min_um}).")
        if lim.max_um is not None and pos > lim.max_um:
            raise SafetyViolation(f"{device}={pos:.2f} um above maximum ({lim.max_um}).")
```

```yaml
named_stages:
  - {device: PIZStage,   min_um: 0.0,     max_um: 200.0}
  - {device: TIRF Stage, min_um: -3000.0, max_um: 3000.0}
  - {device: SmarActZ,   min_um: -1000.0, max_um: 1000.0}
```

Ship the repo default as `safety_config.example.yaml` and make `--safety-config`
required. The committed `x_max: 5000` would have rejected this session's
`x = 5726.5`, so the lab is plainly running an uncommitted file; a default that
silently doesn't match any real rig is worse than no default.

## §8 — stage↔camera affine calibration

```python
# microclaw/calibration.py  (new)

@dataclass
class StageCameraAffine:
    """Maps image-pixel displacement -> stage-um displacement.

    [dx_um]   [a b] [dx_px]
    [dy_um] = [c d] [dy_px]

    Captures pixel size, camera rotation, and BOTH axis flips in one object.
    In amr_test the agent inferred the axis mapping from three stage nudges and
    reached three mutually inconsistent conclusions ([69], [73], [77]).
    """
    a: float; b: float; c: float; d: float
    objective: str
    binning: int
    pixel_size_um: float          # sqrt(|det|), for reporting

    def px_to_um(self, dx_px: float, dy_px: float) -> tuple[float, float]:
        return (self.a * dx_px + self.b * dy_px, self.c * dx_px + self.d * dy_px)


def calibrate_stage_to_camera(ctrl, guard, step_um: float = 20.0) -> dict:
    """Snap, move a known dX, snap, cross-correlate; repeat for dY. ~4 snaps.

    Solves the affine directly instead of asking the model to infer sign
    conventions from thumbnails. Cache under (objective, binning) in the
    knowledge base -- it is a property of the optical path, not the session.
    """
    from skimage.registration import phase_cross_correlation

    ref = snap_to_numpy(ctrl)
    move_stage_xy(ctrl, guard, step_um, 0, absolute=False)
    shift_x, _, _ = phase_cross_correlation(ref, snap_to_numpy(ctrl), upsample_factor=10)
    move_stage_xy(ctrl, guard, -step_um, 0, absolute=False)

    move_stage_xy(ctrl, guard, 0, step_um, absolute=False)
    shift_y, _, _ = phase_cross_correlation(ref, snap_to_numpy(ctrl), upsample_factor=10)
    move_stage_xy(ctrl, guard, 0, -step_um, absolute=False)

    # phase_cross_correlation returns (row, col) = (dy_px, dx_px); invert the
    # 2x2 [px per um] matrix to get [um per px].
    M_px_per_um = np.array([[shift_x[1], shift_y[1]],
                            [shift_x[0], shift_y[0]]]) / step_um
    M = np.linalg.inv(M_px_per_um)
    affine = StageCameraAffine(*M.ravel(),
                               objective=_current_objective(ctrl),
                               binning=int(ctrl.core.get_property(
                                   ctrl.core.get_camera_device(), "Binning")),
                               pixel_size_um=float(np.sqrt(abs(np.linalg.det(M)))))
    save_knowledge(ctrl, guard, f"affine::{affine.objective}::{affine.binning}",
                   json.dumps(asdict(affine)))
    return asdict(affine) | {"n_snaps": 4}


def center_feature(ctrl, guard, max_iter: int = 3, tol_px: float = 5.0) -> dict:
    """Closed loop: find_features -> px offset -> affine -> stage move -> repeat.

    With §6's move_named_stage this same loop drives the TIRF stage to put the
    beam at the pupil centre -- i.e. it does the epi task the human did by hand.
    """
    affine = _load_affine(ctrl)
    for i in range(max_iter):
        feats = find_features(ctrl, guard)
        dx_px, dy_px = feats["offset_from_center_px"]
        if math.hypot(dx_px, dy_px) < tol_px:
            return {"centered": True, "iterations": i, "residual_px": [dx_px, dy_px]}
        dx_um, dy_um = affine.px_to_um(dx_px, dy_px)
        move_stage_xy(ctrl, guard, -dx_um, -dy_um, absolute=False)
    return {"centered": False, "iterations": max_iter,
            "residual_px": feats["offset_from_center_px"]}
```

Also make `move_stage_xy` report what it *achieved*, not just where it landed —
the `[74]` result carried a 1.1 µm unrequested X excursion that nothing surfaced:

```python
    achieved_x, achieved_y = ctrl.core.get_x_position(), ctrl.core.get_y_position()
    return {"requested_um": [target_x, target_y],
            "achieved_um": [round(achieved_x, 2), round(achieved_y, 2)],
            "error_um": [round(achieved_x - target_x, 2), round(achieved_y - target_y, 2)]}
```

## §9 — `find_features`: centroids, not eyeballs

```python
# microclaw/image_analysis.py

def find_features(ctrl, guard, min_sigma: float = 1.0, max_sigma: float = 4.0,
                  threshold_rel: float = 0.15) -> dict:
    """Blob-detect puncta and return an intensity-weighted centroid.

    Answers "is the cell centred in the ROI?" with a number. In amr_test the model
    read the same field three ways across three snaps ([97] "well-centered",
    [117] "just looks like noise", [121] "biased toward upper-right") because it was
    doing spatial statistics by looking at a 200x200 thumbnail.
    """
    from skimage.feature import blob_log
    from scipy import ndimage

    img = snap_to_numpy(ctrl).astype(np.float32)
    bg = float(np.median(img))                       # Evolve512 offset ~= 400
    sig = np.clip(img - bg, 0, None)

    blobs = blob_log(sig / (sig.max() or 1.0), min_sigma=min_sigma,
                     max_sigma=max_sigma, threshold=threshold_rel)
    h, w = sig.shape
    cy, cx = ndimage.center_of_mass(sig)
    off_x, off_y = cx - w / 2, cy - h / 2

    out = {
        "n_spots": int(len(blobs)),
        "centroid_xy_px": [round(cx, 1), round(cy, 1)],
        "offset_from_center_px": [round(off_x, 1), round(off_y, 1)],
        "background_level": round(bg, 1),
        "snr": round(float(sig.max() / (sig.std() or 1.0)), 2),
    }
    if (affine := _load_affine(ctrl)) is not None:                       # §8
        out["offset_from_center_um"] = [round(v, 2) for v in affine.px_to_um(off_x, off_y)]
    if (px := float(ctrl.core.get_pixel_size_um())) > 0:
        out["spot_density_per_um2"] = round(len(blobs) / (h * w * px * px), 4)  # SMLM check
    return out
```

`spot_density_per_um2` doubles as the blinking-density check `smlm_docs.py` asks
for and currently has no tool to answer.

## §10 — normalised, comparability-tagged focus metric

```python
# microclaw/image_analysis.py

def normalized_laplacian_variance(image: np.ndarray, background: float | None = None) -> float:
    """var(laplace(I - bg)) / mean(I - bg)**2 -- scale-free w.r.t. illumination.

    Raw var(laplace(I)) scales with photon count and with whatever happens to be
    inside the crop. Both fired in amr_test at CONSTANT focus (z ~= 45.21):

        full frame, 1% laser  ->  8602
        full frame, 5% laser  -> 13921        2.5x from laser power alone
        full frame, 10% laser -> 21142
        200x200 ROI, 5% laser -> 29107        3.4x from cropping alone

    The model read rising focus_metric as improving image quality at [63]/[67].
    """
    from scipy.ndimage import laplace
    img = image.astype(np.float64)
    if img.ndim == 3:
        img = img.mean(axis=-1)
    bg = float(np.median(img)) if background is None else background
    sig = img - bg
    mean = float(np.mean(np.abs(sig)))
    if mean <= 0:
        return 0.0
    return float(np.var(laplace(sig)) / mean ** 2)


def _focus_metric_payload(image, ctrl, roi) -> dict:
    """Stamp the metric with the settings it is only comparable within.

    A bare float invites exactly the cross-setting comparison the model made.
    """
    return {
        "focus_metric": round(normalized_laplacian_variance(image), 4),
        "focus_metric_kind": "normalized_laplacian_variance",
        "metric_valid_for": {
            "roi": [int(roi.x), int(roi.y), int(roi.width), int(roi.height)],
            "exposure_ms": round(float(ctrl.core.get_exposure()), 1),
            "binning": ctrl.core.get_property(ctrl.core.get_camera_device(), "Binning"),
        },
    }
```

```python
# tests/test_image_analysis.py
def test_metric_is_invariant_to_illumination_scaling():
    """The amr_test regression: 5x brighter must not read as 2.5x sharper."""
    img = _synthetic_puncta(bg=400)
    bright = (img - 400) * 5 + 400
    assert normalized_laplacian_variance(bright) == pytest.approx(
        normalized_laplacian_variance(img), rel=0.05)
```

Prompt addition: *"`focus_metric` is comparable only between snaps whose
`metric_valid_for` blocks are identical. Never compare it across an ROI,
exposure, binning, or illumination change."*

## §5 — focus-lock tools

```python
# microclaw/tools.py

def get_focus_lock_state(ctrl, guard) -> dict:
    """Read the focus lock via the EMU map ('Z stage focus locking').

    In amr_test the model ran its own SMLM checklist and wrote "Focus lock engaged?
    (You confirmed focus looks fine at Z=45.2 um.)" -- a checklist item answered
    with irrelevant evidence, when a one-call check existed.
    """
    emu = build_emu_map(_cached_emu_properties(ctrl))
    lock = emu.get("focus_lock")
    if lock is None:
        return {"engaged": None, "reason": "No focus-lock property in the EMU map."}
    value = ctrl.core.get_property(lock["device"], lock["property"])
    return {"engaged": str(value) == str(lock.get("on", "1")),
            "raw_value": value,
            "property": f"{lock['device']}.{lock['property']}",
            "qpd": _read_qpd(ctrl, emu)}


def set_focus_lock(ctrl, guard, enabled: bool) -> dict:
    emu = build_emu_map(_cached_emu_properties(ctrl))
    lock = emu["focus_lock"]
    target = lock["on"] if enabled else lock["off"]
    ctrl.core.set_property(lock["device"], lock["property"], target)
    return {"engaged": enabled, "property": f"{lock['device']}.{lock['property']}"}
```

Finally, make the SMLM checklist machine-checked rather than prose the model
self-attests to. In `smlm_docs.py`, name the tool for each item:

```
| Check              | Tool                                    | Pass condition          |
|--------------------|-----------------------------------------|-------------------------|
| Focus lock engaged | get_focus_lock_state()                  | engaged == true         |
| Excitation will fire | _assert_excitation_will_fire(slot)    | trigger mode != "0 - Off" |
| Blinking density   | find_features()                         | spot_density_per_um2 in [0.1, 1.0] |
| No saturation      | snap_and_analyze()                      | saturated_fraction == 0 |
```

A checklist the model can satisfy with vibes is not a checklist.

## The meta-observation

Microclaw's *conversational* behaviour was excellent — it flagged the
"continuous **with** interval" contradiction `[141]`, refused to guess the TIRF
axis `[41]`, correctly distrusted its own autofocus `[125]`, and declined to
invent a `close MM` tool `[157]`. Its epistemics are good.

What it lacks is **instrumentation**. Nearly every failure above is microclaw
reasoning carefully over a measurement that was wrong, absent, or
uncomparable: a curve from the wrong pass, a trigger line from the wrong laser,
a metric that tracks laser power, a thumbnail where a centroid belonged, and a
display side-effect it could not observe. The fix is not a better prompt. It is
tools that return the number the model is actually trying to reason about — and
that refuse to report success when they have not achieved it.
