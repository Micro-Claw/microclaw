---
name: zeiss-can29
description: Verify cubes, side port and illumination on a ZeissCAN29 stand, and engage Definite Focus correctly.
---

# ZeissCAN29 reference

Returned by `load_skill(name="zeiss-can29")`.

Zeiss stands on the ZeissCAN29 adapter. Only the `ZeissScope` hub is registered;
every peripheral is discovered from what the stand reports, so `list_devices` is
a statement about this stand. Common labels: `ZeissReflectorTurret`,
`ZeissObjectiveTurret`, `ZeissTubeLens` (optovar), `ZeissSidePort`,
`ZeissBasePort`, `ZeissLSMPort`, `ZeissTransmittedLightShutter`,
`ZeissReflectedLightShutter`, `ZeissHalogenLamp`, `ZeissHBOLamp`,
`ZeissColibri`, `ZeissFocusAxis`, `ZeissXYStage`, `ZeissDefiniteFocus`,
`ZeissDefiniteFocusOffset`. The adapter provides an XY stage, but a config may
drive XY with a different controller instead — read the device to see which.

## Never trust a label; verify it

Position labels are **not measurements**. The adapter reads them out of the
stand's own permanent parameters and prefixes the slot index, so a label reads
`<n>-<firmware name>`; a Micro-Manager config's `Label` lines can then overwrite
the whole thing. Both sources were typed by a human, possibly years ago. A label with no
index prefix has almost certainly been hand-edited. Empty names (`1-`, `2-`) mean
the firmware simply has nothing stored — common for the optovar, and not a fault.

So: confirm the mapping with the operator once, `save_knowledge` it, and read it
back later rather than re-deriving it from labels. Never report a cube, port or
magnification as established because its label says so.

## Check routing, cubes and light before the first exposure

Do this at session start and again after **any** interval where the operator was
at the scope — all of it can be moved by hand with nothing announcing it.

1. **Core roles.** `get_full_device_state("Core")`. `Core.Camera`, `Core.Focus`,
   `Core.Shutter` are free assignments the user or microclaw can change; there is
   no default to assume. `Core.Focus` blank makes every snap fail with
   `No device with label ""`. And `get_system_state`'s `shutter` block describes
   only whatever `Core.Shutter` points at — if that is an LED illuminator, it says
   nothing about a transmitted lamp.
2. **Port routing.** A port position sends the light to one destination — a
   camera, a second camera, a confocal or other module, the eyepieces. Which is
   which is a fact about the stand: ask, record, and verify before every imaging
   step. There may be more than one port device; enumerate them. **A dark field
   with the lamp on and the shutter open is a routing problem until proven
   otherwise** — check it before theorising about the sample.
3. **Cube.** Confirm the reflector position is the intended path, against the
   confirmed mapping rather than the label.
4. **Light.** Brightfield needs a transmitted shutter open (`State` `1`) and a
   transmitted lamp up; fluorescence needs an RL source and a matching cube. Lamp
   servos name their property after the firmware's unit, so read it with
   `list_device_properties` / `get_device_property_info` rather than assuming.
   `ZeissColibri` carries `Shutter` (`External`/`LED`), `Operation Mode`, and one
   `Intensity LED-<nm>nm` (0–100) per installed LED — those names tell you which
   lines exist.

Lamp response can be steeply non-linear near the working point, so **use exposure
as the brightness knob**, move lamp intensity in small steps, and snap after each.
`saturated_fraction` can read 0.0 while pixels sit at the rail — score clipping
from `max_intensity` against full scale.

## Identifying a cube when nobody knows which is which

Ask first. If the operator does not know, offer this — it needs their OK, since
it puts light on the sample; use low power and a sample or test slide that can
take the dose.

- **Find the brightfield/empty position.** Transmitted light on, all fluorescence
  excitation off. Step the reflector through its positions and snap at each.
  Positions passing a bright, structured transmitted image are brightfield or
  empty; fluorescence cubes block the transmitted path and read dark or flat.
- **Match fluorescence cubes to excitation.** Transmitted light off. Enable one
  LED (or one RL line) at a time at low power and step the turret again. The cube
  that produces signal for that line is the one matched to it; identify it by the
  excitation that works, not by the label.
- Record the result with `save_knowledge`, including the positions that stayed
  dark — a position that never produces signal is evidence too.

Report what each position **did**, and let the operator name it. Do not rename a
cube in the config, and do not switch a cube the operator established without
asking — it changes what they are looking at.

## Definite Focus — the focus hold

It **holds** a plane; it does not find one. Establish focus first (`run_autofocus`,
or the operator at the eyepiece), then engage. `run_autofocus` refuses to sweep
while the lock is engaged, so disengage, focus, re-engage.
`get_focus_lock_state` returns a `probe_hint` naming the status device and
property for `run_autofocus`'s property probe, which finds the capture range at
zero exposures.

Every engage is its own event — it can work once and fail the next time with
nothing changed:

- **Engage, wait for it to stabilize, then confirm — before the first exposure.**
  It needs more settling time than expected even when it works. Do not engage and
  immediately acquire.
- **Confirm from a frame, not from `engaged: true`.** Snap once and check the
  focus metric is at the in-focus level for this stand, not the noise floor. The
  API can report engaged while the stand's panel reads "setting focus", i.e.
  searching — and a run completing is not evidence the lock held. Ask what the
  panel says.
- Telemetry lags: a disengage can return `false` while an immediate read still
  says `true`. Read twice; if they disagree, believe neither.
- It loses lock on **featureless fields** — it needs a reference reflection, so
  large moves onto empty glass drop it into hunting and the exposure fires anyway.
  Out-of-focus tiles and empty tiles are then the same tiles. Skip empty positions
  rather than trying to rescue them; image autofocus fails there too, correctly.
- Never carry "the focus hold works on this rig" forward into a later run or a
  report.

No acquisition tool has a per-position dwell knob. `autofocus_per_position`'s
`settle_ms` is internal to its Z sweep (which fights an engaged lock), a saved
`analyze_frame` hook runs after capture, and a timelapse `interval_s` gaps frames
*after* the first. Say so rather than describing something else as a settle.

**`ZeissDefiniteFocusOffset` is not a micrometre axis.** Its position is an index
into captured offset blobs; writes only re-select an existing index and silently
do nothing otherwise, and steps/origin/limits are unsupported. Treat it as
read-only, never as a distance, and note that reading it forces a stabilization
wait.

## Z bounds

`run_autofocus` and `move_stage_z` refuse until microclaw's safety config has a
top-level `stage:` block — limits under `named_stages:` authorize
`move_named_stage` only. A `min` equal to the current Z blocks half the search.
The config is read at startup; a change needs a restart. Micro-Manager's own axis
limits are a different thing and the guard does not read them.
