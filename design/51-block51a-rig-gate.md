# Block 51a rig gate — the preset route honours schema 3's contract

Implementation ancestor: `30cb55e`

Two machines, and **both are needed**: each shows a different branch of the same
defect and neither shows both.

- **Demo** — the `Core.Shutter` branch. M5 has no `Channel` group and cannot
  reach it.
- **M5** — the map-membership branch, on the two `System` presets in routine use.

Run either order. Preserve `block51a-pytest.txt` and both saved transcripts into
the evidence archive as `51a-demo` and `51a-m5`.

**Do not edit either machine's `safety_config.yaml` before the step that says
to.** The minimal schema-3 document is the fixture — it is what both rigs run
today, and it is what produced the defect.

What this gate proves, in one line: on a rig that declares nothing, a config
preset applies instead of being refused by a map the operator never authored —
and every guard still bites.

## Step 0 — pin and test (both machines)

Close Microclaw, then paste:

```powershell
git merge-base --is-ancestor 30cb55e HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
python -m pytest -q > block51a-pytest.txt 2>&1
Write-Host "pytest exit code (expected 0):" $LASTEXITCODE
Get-Content block51a-pytest.txt -Tail 3
```

Expected: ancestor exit **0**, pytest exit **0**. macOS at this implementation is
**1794 passed / 99 skipped / 3 warnings**. Windows skips roughly 25 more; a
higher skip count than that machine's own previous full run is **NOT TESTED**,
not a pass.

---

# Part A — M5, the map-membership branch

On 2026-08-14 both of these refused with
`Channel effect HamamatsuHam_DCAM.DEFECT CORRECT MODE was refused because it is
unclassified or excluded`.

## Step A1 — the presets you actually use now apply

Open Micro-Manager's Property Browser and note the current values of the camera
properties the preset writes — `DEFECT CORRECT MODE` among them. Keep it visible
and **do not touch it**. Then say this verbatim:

> Use `set_config_preset` exactly once with group System and preset Camera.
> Report the complete result verbatim. Then stop and wait for me.

Expected:

- Exactly **1** call, **no refusal**.
- The result lists the writes that ran, `DEFECT CORRECT MODE` included.
- **The Property Browser values change, with no human input.** A green result
  over a browser that did not move is **NOT TESTED**.

Then repeat, verbatim, for the other preset in routine use:

> Use `set_config_preset` exactly once with group System and preset Normal Mode.
> Report the complete result verbatim. Then stop and wait for me.

Expected: the same. Record the before and after values yourself from the browser,
not from the agent's narration. Leave whichever preset you normally run applied —
nothing here restores one for you.

## Step A2 — the guard still bites

**This is the limb that matters most.** The fix stops consulting the
authorization map; it must not stop consulting the guard. If A1 passes and this
fails, the block is worse than the defect it repairs.

Close Microclaw. Look at whichever `System` preset writes the camera's exposure
and note the value it sets. Add a `camera` section to
`%APPDATA%\microclaw\safety_config.yaml` with a maximum **below** that value:

```yaml
camera:
  max_exposure_ms: <a number below the preset's exposure>
```

Restart Microclaw and say this verbatim:

> Use `set_config_preset` exactly once with group System and preset PASTE_PRESET.
> The refusal is the expected result. Report it verbatim and do not retry or work
> around it.

Expected: a refusal naming the exposure and the limit, and **0** writes — the
Property Browser is unchanged. A preset that applies here is a gate failure and
the most serious outcome this gate can produce.

Then **remove the `camera` section again**, restart, and confirm the preset
applies once more. Leave the config as you found it.

## Step A3 — the raw route is unchanged

Still on the restored minimal config, say this verbatim:

> Use `set_device_property` exactly once on device HamamatsuHam_DCAM, property
> DEFECT CORRECT MODE, with the value it currently holds. Report the complete
> result verbatim.

Expected: it succeeds, as it did before this block. This is the route that was
always open, and the point of the block is that the two now agree — if this
refuses, the fix moved the wrong one.

---

# Part B — demo, the `Core.Shutter` branch

## Step B1 — the preset carries the retarget

Say this verbatim:

> Use `list_config_groups` exactly once with group Channel and preset Cy5.
> Report the complete result verbatim.

Expected: the settings include a `Core` / `Shutter` row. **That row is what makes
this machine the reproducer** — record it. If it is absent, stop and report:
the preset changed and this gate is testing nothing.

## Step B2 — `set_channel` completes, unconfirmed

Keep Micro-Manager's main window visible so you can see the shutter selection.
Say this verbatim:

> Use `get_available_channels` exactly once, then use `set_channel` exactly once
> with preset Cy5. Report both complete results verbatim.

Expected, all four:

- No refusal. The `RigAuthorizationError` naming `Core.Shutter retarget` is the
  exact defect under repair — if it appears, record it verbatim and stop.
- The result lists the writes that ran.
- **No confirmation is requested.** This is deliberate: with no `illumination`
  section the rig declares no light sources, and design/48's M5 gate already
  blessed a laser *enable* passing unconfirmed under these conditions. A prompt
  appearing here is a finding — record it rather than answering it.
- The emission path moves in Micro-Manager, with no human input.

## Step B3 — a declared shutter still refuses an undeclared target

**The half a fix like this breaks.** Close Microclaw and add to
`%APPDATA%\microclaw\safety_config.yaml`:

```yaml
illumination:
  shutters:
  - device: Some Other Device
    property: State
    on_value: "1"
    off_value: "0"
```

Use a device name that is **not** the one the `Cy5` preset retargets to. Restart
and repeat step B2's `set_channel` prompt verbatim.

Expected: it refuses again, with today's message naming the retarget target and
`illumination.shutters`. The protection must be intact the moment the section
exists. A `set_channel` that succeeds here means the fix disarmed the declared
case and the block fails.

Then **remove the `illumination` section**, restart, and confirm `set_channel`
succeeds again. Leave the config as you found it.

## Step B4 — the blocked 50a steps

Re-run block 50a's step A4 on this machine, which this defect blocked:

> Use `get_available_channels` exactly once, then use `set_channel` exactly once
> with a preset from that list. Report both complete results verbatim.

Expected: `channel_source: "config-group"` and the channel applied through the
effects route. This closes 50a's A4.

## Not in this gate, deliberately

- An illumination *confirmation* on M5. With no `illumination` section none can
  fire; that is design/48's intended behaviour, not a failure, and block 50a's
  step B2 records it rather than testing it.
- Editing either rig's config beyond the two temporary sections above, each of
  which is removed again in its own step.

## Reporting

Save each browser conversation with the normal transcript-save control as
`block51a-m5-transcript` and `block51a-demo-transcript`. Return, per step: the
number of tool calls, the verbatim results, and what **you** saw in
Micro-Manager's panels with your hands off them. For A2 and B3 — the two
must-still-refuse steps — say explicitly whether the refusal appeared and whether
anything moved. Report what happened, including anything that looked wrong; do
not diagnose the system or judge whether the evidence is valid.
