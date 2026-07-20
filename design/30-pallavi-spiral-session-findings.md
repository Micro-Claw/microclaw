# design/30 — Pallavi spiral montage session findings (2026-07-20)

Source transcript:
`20260720_155559_microclaw_history_pallavi_spiral_test.json`.

The session successfully acquired 25 positions and produced a TIFF, but it also
exposed a safety-critical unit-conversion error and two missing Micro-Manager
workflow surfaces. The output file was not the workflow the user originally
wanted: Microclaw substituted a pycro-manager dataset and a custom contact-sheet
hook for Micro-Manager's Album and programmable MDA facilities.

## What happened

Pallavi first asked Microclaw to run an htSMLM `.uiacq` file. Microclaw correctly
said it could not drive the htSMLM Acquisition tab or interpret the file. She then
asked for a montage of 25 positions (`spiral_01` through `spiral_25`) using 561 nm
and 638 nm simultaneously, 100 ms exposure, nominally 1% power, one focus-locked
plane, and a single output file.

Microclaw:

1. imported the 25 Micro-Manager positions;
2. verified the focus lock and the two trigger lines;
3. generated a montage hook;
4. wrote 561 raw PWM value `3` and 638 power `1`;
5. retried after the first hook failed to import `HookBase`;
6. acquired all 25 positions and wrote a 5×5 TIFF contact sheet; and
7. disabled both lasers afterward.

The acquisition completed, but the user reported that 561 had been set to 10%,
not 1%, and that the images were at the ceiling. The transcript supports her
report.

---

## Finding 1 — the 561 slope was applied in the wrong direction

**Severity: critical.** This changed illumination delivered to the sample and
produced saturated data.

The EMU configuration maps 561 power as follows:

```text
device: PWM
property: Position0
offset: 0.0
slope: 0.3
```

Microclaw interpreted that as:

```text
raw = round((requested_percent - offset) / slope)
1%  -> round(1 / 0.3) = 3
```

It then repeatedly described raw `3` as approximately 0.9–1%. The observed
round trips from the htSMLM GUI establish the opposite direction:

| htSMLM 561 setting | `PWM.Position0` readback |
|---:|---:|
| 10% | 3 |
| 1% | 0 |

The operative mapping is therefore approximately:

```text
raw = round(percent * 0.3)
```

Raw `3` corresponds to approximately 10%, exactly as the user reported. The
assistant eventually derived this mapping and explicitly owned the error ("your
GUI is right; my percent label was backwards"), but then re-hedged rather than
committing to the correction.

The decisive point is that the correct convention was **already written into the
codebase** and the model still inverted it. `htsmlm_docs.py:53` documents it as a
worked example:

```python
# For a Rescaled property the MM value = slope * ui_value + offset
mm_value = float(entry.get("slope", 1)) * 50 + float(entry.get("offset", 0))
```

That is `raw = slope * percent + offset` — the correct direction. The model had
this in front of it and still computed `raw = (percent - offset) / slope`. A
documented formula the model reads and then contradicts is the strongest possible
argument for taking the arithmetic out of the model entirely.

The direct Cobolt properties did not resolve the UI percentage. With the laser
off they reported live `Power = 0 mW`, while `Power Setpoint = 250 mW` and
`Power Status = 12.7 mW`; those values are a different control/readback surface
and cannot validate the htSMLM percentage conversion.

### Evidence in the images

Every acquired tile reached at least about 53,155 counts, and several reached
about 63,609. Saturation alone cannot identify the laser percentage, but it is
consistent with the excessive illumination reported by the user.

### Required fix

Do not make the language model perform calibration arithmetic from an EMU
`slope` field. Put the conversion in one tested code path and expose semantic
tools such as:

```python
set_emu_laser_power_percentage(slot=2, percent=1.0)
get_emu_laser_power_percentage(slot=2)
```

This is not a greenfield tool. The correct `raw = slope * percent + offset`
convention already lives in `htsmlm_docs.py:53`, and `emu_manager.py` already
parses each laser's `slope`/`offset` from the EMU map (the ` slope`/` offset`
Rescaled suffixes). The setter should reuse those, not re-derive the direction —
the whole point is that no natural-language step gets to choose which way the
slope goes. Enforce the raw property's type and range, and return the requested,
written, and effective values plus an explicit representability flag:

```json
{
  "requested_percent": 1.0,
  "raw_value_written": 0,
  "effective_percent": 0.0,
  "representable": false,
  "min_nonzero_percent": 3.3
}
```

If the requested value is not representable because the raw property is
integer-valued, the tool must report that before enabling illumination — on this
rig 1% quantizes to raw `0` (off) and the smallest nonzero step is raw `1`
(≈3.3%). It must not silently call 0% or 10% “approximately 1%.”

For an unverified calibration, require a round-trip test at two known GUI
settings before using it on a sample. A property readback proves only that the
raw write succeeded; it does not prove that the percent conversion is correct.

---

## Finding 2 — the montage was a contact sheet, not a spatial stitch

The hook placed full-resolution frames in acquisition order into a fixed 5×5
row-major canvas. The positions, however, form a spiral in stage coordinates.
Adjacent panels in the TIFF are therefore not consistently adjacent in sample
space. The result is a contact sheet suitable for inspection, not a registered
or stage-coordinate-aware mosaic.

The tool and agent vocabulary must distinguish:

- **Album:** independent snaps collected in Micro-Manager's GUI;
- **contact sheet / montage:** panels arranged for inspection, with no claim of
  spatial continuity;
- **stage-coordinate mosaic:** tiles placed using recorded XY coordinates;
- **stitched mosaic:** overlapping tiles registered and blended into one image;
- **multi-page TIFF:** one image per page, with no layout implied.

When the user says “stitch,” Microclaw must not silently choose a contact sheet.
If overlap, pixel size, camera orientation, or stage-to-camera calibration is
missing, it should say which kind of output it can produce.

The saved hook did retain each tile in the NDTiff dataset and logged the stage
coordinates, so a correct coordinate-aware mosaic could be generated offline.
That belongs with the read-side dataset work reserved as design/29.

---

## Finding 3 — Micro-Manager Album is not exposed

Pallavi wanted the snaps stored in Micro-Manager's Album, using the MMStudio
[`Album` API](https://micro-manager.org/apidoc/mmstudio/2.0.0/org/micromanager/Album.html).
There is no Microclaw Album tool. An NDTiff acquisition dataset, multi-page TIFF,
or hook-generated composite is not equivalent: none populates the Album the user
intended to inspect and manage inside Micro-Manager.

The intended workflow was approximately:

```text
visit position -> snap -> add snap to MM Album -> inspect/manage in MM -> montage
```

Microclaw substituted:

```text
pycro-manager Acquisition -> NDTiff -> image-process hook -> external TIFF
```

That substitution produced a file but changed the application-level workflow.

### Feasibility spike first

Before committing to any of this tool surface, write a Windows-rig spike — the
same discipline prior rig- and bridge-facing designs in this repo followed
(design/10, 14, 17, 18). The Album and MDA reachability questions are exactly the
kind the bridge does not let us assume:

- Is `studio.album()` reachable over the pyjavaz/ZMQ bridge at all?
- Does the returned Album proxy expose the expected image-addition operation
  (`Album.addImages(...)` in the Java API), under what snake-cased name, and does
  pyjavaz accept its collection/varargs representation? Can an image object be
  passed *into* it across the bridge, or must it be reconstructed Studio-side?
- Does adding to the Album actually repaint the MMStudio GUI, or is it silent
  or asynchronous? GUI repaint cannot be assumed: the Preview path previously
  exposed a cold-window race and required synchronization plus an explicit
  re-push of the image already held by Python.

These collide with known bridge caveats: static-class access must route through
`controller._new_static_java_class`, field access is camelCase while methods are
snake_cased, and display is async. The spike should write all findings to a file
the operator can pass back, and settle these before a line of the tool surface is
designed. Record the exact proxy method names, accepted argument encodings,
return types, and whether returned Java proxies remain usable on subsequent
calls. The same spike should cover Finding 4's MDA questions below.

### Required tool surface

Add Java/MMStudio-backed tools rather than attempting to emulate Album in a
Python file:

- `snap_to_album` — snap using MMStudio and add every returned camera image to
  the current Album;
- `add_image_to_album` — add an already acquired MM image when the object can be
  passed safely through the running Studio instance;
- `get_album_state` — report whether an Album exists and its image count/axes;
- Album lifecycle operations only where the MM API supports them safely.

The tools should preserve image metadata, camera/channel identity, and GUI
visibility. Tests must verify that images appear in the running MMStudio Album,
not merely that a datastore exists on disk.

---

## Finding 4 — programmable MDA is not exposed

Pallavi also wanted access to Micro-Manager's
[Multi-Dimensional Acquisition Programming](https://micro-manager.org/Multi-Dimensional_Acquisition_Programming)
surface. Microclaw currently offers opinionated wrappers such as timelapse,
Z-stack, multiposition, and tile acquisition. These cover common shapes but do
not expose the complete Micro-Manager MDA settings model or the acquisition
configuration currently visible in MMStudio.

This is distinct from pycro-manager's `multi_d_acquisition_events`. Generating a
similar event sequence in Python does not provide access to MMStudio's MDA state,
presets, GUI configuration, or every behavior of its acquisition engine.

### Feasibility spike first

Fold the MDA questions into the same Album spike above before designing the tool
surface: is `studio.acquisitions()` reachable over the bridge; does the returned
proxy expose the expected `AcquisitionManager` operations; can its candidate
`SequenceSettings` object be read, passed back, and kept alive across subsequent
bridge calls; and does running the configured MDA repaint the GUI and return a
datastore/display handle we can observe? Record the exact method names, argument
representations, return types, and proxy lifetimes. As with Album, treat the Java
API names as candidates rather than confirmed pyjavaz contracts and prove them
on the rig.

### Required tool surface

Prefer a structured, guarded interface over arbitrary BeanShell/Java execution:

- read the current MMStudio MDA settings;
- validate and update selected settings (time, Z, channels, positions, saving);
- load or apply a named/settings-file configuration where the MM API supports it;
- run the configured MDA and report the datastore/display it created;
- distinguish “run the GUI's current MDA” from “construct a pycro-manager
  acquisition with equivalent-looking axes.”

The returned plan should echo the resolved settings before acquisition so the
user can verify channels, powers, Z motion, frame counts, and output behavior.
Hardware guardrails still apply even when the configuration originates in the
GUI.

---

## Finding 5 — generated hooks are validated at acquisition time, not before

The first generated hook used a nonexistent implicit `HookBase` global and an
incorrect `analyze` method. It failed immediately with `NameError` before frames
were acquired. Microclaw then consulted its hook documentation, imported
`HookBase`, changed to `image_process_fn`, and succeeded.

The failure never reached the sample — the shutter stayed closed — so this is a
preflight *timing* defect, not a sample-exposure event: import/API validation
happens when the acquisition is submitted rather than before laser enablement.
It should happen before either. `generate_and_save_hook` currently confirms that a
file was saved, not that the hook can be imported, instantiated with the supplied
parameters, and exercised against a synthetic frame.

Add a preflight that:

1. imports the saved module in the same Python environment as the acquisition
   runner, but in an isolated subprocess with no hardware controller;
2. resolves and instantiates `Hook` with the actual `hook_params` and `log_path`;
3. checks the callable signature and return contract;
4. sends a synthetic frame/metadata pair through image processing; and
5. redirects all output parameters to a temporary directory, verifies any
   resulting paths against the workspace policy, and removes the smoke-test
   artifacts rather than writing the requested final artifact.

Import itself is executable Python. The source scan stays advisory —
`hook_manager` calls its denylist "not a security boundary," and legitimate
file-writing hooks trip it routinely (the montage hook that succeeded here
triggered warnings for its use of `os`; `tifffile` is not on the denylist).

A separate process, an omitted controller argument, and redirected output do
**not** by themselves make arbitrary hook code safe. The hook could import
pycro-manager and connect to the known bridge port itself, write outside the
temporary directory, open a network connection, or spawn another process.
Synthetic execution therefore requires an actual sandbox that restricts
filesystem access, network and bridge access, subprocess creation, and runtime
resources. Importing the module and calling its constructor also execute
arbitrary Python; neither is safe merely because the image callback is skipped.
Until that sandbox exists, automatic preflight must stop at static parsing and
source review plus manifest/hash verification. Import, constructor, and
synthetic-frame checks remain explicitly trusted-code operations, not automatic
safety gates.

A generated hook must not be able to turn validation into a hardware action. A
hook that cannot be meaningfully validated without hardware should fail
preflight explicitly, not run its first validation against the sample.

Only after that preflight passes should Microclaw change illumination state.

---

## Finding 6 — successful cleanup does not repair a bad preflight

Microclaw did several things correctly: it verified trigger modes/sequences,
read focus-lock state instead of inferring it from image sharpness, disabled 405,
checked the shutter after the failed attempt, and disabled 561/638 after the
successful run.

Those controls limited the consequences, but they did not validate the most
important experimental quantity: effective laser power. Safety reporting should
separate:

- **commanded state** — what raw value was written;
- **interpreted state** — the calibrated percentage implied by code;
- **measured state** — power-meter or device output, if available; and
- **GUI state** — what htSMLM/MMStudio displays.

If these disagree, illumination must remain disabled until resolved. The agent
must report the disagreement rather than selecting whichever interpretation
matches its previous statement.

---

## Priority

1. Move EMU percent conversion out of the prompt/model (reusing the existing
   `htsmlm_docs`/`emu_manager` slope handling) and add representability plus
   round-trip validation before illumination. Fold in Finding 6's
   commanded/interpreted/measured/GUI state separation here — it is the cheap,
   high-value part that would have surfaced the disagreement immediately.
2. Validate generated hooks before touching illumination or the sample.
3. Spike Album + MDA bridge reachability on the rig, then add the Album-backed
   snap workflow so Microclaw preserves the user's normal MMStudio interaction.
4. Expose the guarded MMStudio MDA settings/run surface (gated on the same spike).
5. Make output terminology explicit and add coordinate-aware/offline mosaicking
   through the design/29 dataset reader.
