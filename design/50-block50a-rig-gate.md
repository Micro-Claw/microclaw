# Block 50a rig gate — config groups are listable and selectable

Implementation ancestor: `49da41f`

Three machines, in this order, and **only the first two are needed to close this
gate**: the demo machine, then M5. M2 carries one deferred limb, recorded at the
bottom, and is not reachable at the time of writing.

- **Demo** has both a `Channel` config group and an authorization map
  (established by 43n's gate), so it proves the executor route and that
  `set_channel` is unchanged, without booking scarce rig time.
- **M5** proves what demo cannot: a rig with **no `Channel` group at all**, where
  `set_config_preset` is the only config-group write path that exists.

Preserve `block50a-pytest.txt`, both saved transcripts and the exported script
into the evidence archive as `50a-demo` and `50a-m5`.

What this gate proves, in one line: Microclaw can enumerate a rig's config groups
and read a preset's true membership instead of inferring it, can apply one
through the authorized executor, and still refuses an effect it cannot classify.

---

# Part A — the demo machine

## Step 0 — pin and test

Close Microclaw, then paste:

```powershell
git merge-base --is-ancestor 49da41f HEAD
Write-Host "implementation ancestor exit code (expected 0):" $LASTEXITCODE
python -m pytest -q > block50a-pytest.txt 2>&1
Write-Host "pytest exit code (expected 0):" $LASTEXITCODE
Get-Content block50a-pytest.txt -Tail 3
```

Expected: ancestor exit **0**, pytest exit **0**. macOS at this implementation is
**1802 passed / 99 skipped / 3 warnings** (this branch carries block 50b, already
merged to `main`). Record this machine's exact passed and skipped numbers.
Windows skips roughly 25 more than macOS; a higher count than this machine's own
previous full run is **NOT TESTED**, not a pass.

Then start Micro-Manager and Microclaw as normal.

## Step A1 — the groups are visible at all

Say this verbatim:

> Use `list_config_groups` exactly once and report the complete result verbatim.
> Do not change any hardware state and do not call any other tool.

Expected:

- Exactly **1** call, no refusal.
- Every group visible in Micro-Manager's Configuration Settings panel appears,
  with its presets. **Compare against the panel yourself, group by group** — a
  plausible-looking list missing a group is the failure this step looks for.
- `active_preset` per group matches the panel. Where the panel shows no preset
  selected, `active_preset` is `null` **and no `error` key sits beside it**; a
  `null` with an `error` means the read failed, which is a different fact and
  must be recorded as such.

Record the complete result. Every step below names groups from this list.

## Step A2 — a preset's membership is read, not guessed

Pick a **non-`Channel`** group from A1 that sets no illumination — a camera or
objective group is ideal. In Micro-Manager, open Configuration Settings, select
one of its presets, and note its exact device/property/value rows on screen.

Then say this verbatim, substituting the two names:

> Report the complete stored definition of preset PASTE_PRESET in config group
> PASTE_GROUP — every device, property and value it sets. Use only
> `list_config_groups`. Do not apply it, do not read individual device
> properties, and do not infer anything from live values.

Expected: the reported rows **match the panel exactly**, in membership and
values. Any row on screen and absent from the result, or vice versa, fails this
step. So does the agent falling back to `get_device_property` — that is the
2026-08-12 behaviour this block exists to remove.

## Step A3 — applying a preset works and is verified

Same group and preset as A2. Set that group to a **different** preset by hand in
Micro-Manager first, so the change is visible when it happens. Keep the
Configuration Settings panel visible and **do not touch it** for the rest of this
step. Say this verbatim:

> Use `set_config_preset` exactly once with group PASTE_GROUP and preset
> PASTE_PRESET. Report the complete result verbatim. Then stop and wait for me.

Expected, all four:

- Exactly **1** call, no refusal.
- The result lists the writes that ran, one per device/property in the preset.
- **The panel's selection changes to the requested preset, with no human input.**
  A green result with a panel that did not move is **NOT TESTED**.
- No other group's selection changed.

## Step A4 — `set_channel` is unchanged in the same session

Immediately, in the same session, say this verbatim:

> Use `get_available_channels` exactly once, then use `set_channel` exactly once
> with a preset from that list. Report both complete results verbatim.

Expected: both behave exactly as before this block — channels listed from the
`Channel` group, applied through the effects route with
`channel_source: "config-group"`. A changed shape means the group
parameterization leaked into the channel path.

## Step A5 — the exported script runs standalone

Say this verbatim:

> Use `export_session_script` to write this session to block50a-standalone.py.
> Report the complete result verbatim.

Then close Microclaw entirely and paste:

```powershell
python block50a-standalone.py > block50a-standalone.txt 2>&1
Write-Host "standalone exit code (expected 0):" $LASTEXITCODE
Get-Content block50a-standalone.txt -Tail 20
Select-String -Path block50a-standalone.py -Pattern "import microclaw","from microclaw"
Write-Host "the matches printed above must be NONE"
```

Expected: exit **0**. A `NameError` here is block 43n's defect returning — record
the traceback verbatim, it is the most important thing this step can produce. An
**empty** output file with exit 0 is a **pass**, not a failure: these scripts
contain no `print()`, so a fully successful run writes nothing.

Then confirm by eye in Micro-Manager that the preset A3 applied is selected
again.

---

# Part B — M5

M5 has **no `Channel` config group**. `get_available_channels` returns `[]`, and
`set_config_preset` is the only way to reach a preset on this machine.

Its camera settings live in groups the operator normally drives by hand:
**`Camera`**, and the presets **`Fast Mode`**, **`Normal Mode`**, **`Slow Mode`**
— `Camera` and `Normal Mode` being the ones in routine use. Take the exact group
and preset names from A1's output rather than from this paragraph; the names here
tell you where to look, not what to type.

Run steps A1 and A2 on M5 first, unchanged, then continue.

## Step B1 — apply the preset you actually use, observed on the camera

Note the camera's current settings in the Property Browser — whichever
properties A2 showed the preset writes. Then say this verbatim, substituting
from A1:

> Use `set_config_preset` exactly once with group PASTE_GROUP and preset
> PASTE_PRESET. Report the complete result verbatim. Then stop.

Expected: the camera properties in the Property Browser change to the preset's
values, with no human input, and the result lists each write. **Record the before
and after values yourself from the browser**, not from the agent's narration.

Choose a preset that is safe to be left applied — `Normal Mode` if in doubt.
Nothing in this gate restores a preset for you.

## Step B2 — an illumination preset asks, and a decline changes nothing

**This is the limb that justifies per-effect authorization**, and M5 is where it
is naturally reachable: its `System` group's presets arm lasers for TTL.

Pick a `System` preset that arms a laser. **Confirm every laser is off first**,
from the htSMLM panel — note that the 2026-08-14 sessions found slot 3 (640)
enabled at 1.00%, so check rather than assume. Then say this verbatim:

> Use `set_config_preset` exactly once with group System and preset
> PASTE_PRESET. When the confirmation appears, I will answer it. Report the
> complete result verbatim.

**Answer the confirmation `n`.** Expected:

- A confirmation prompt appears naming the illumination property being written,
  and the call refuses after the decline.
- **Every laser is still off**, and every property the preset would have written
  is unchanged in the Property Browser. Check both yourself.
- Nothing left half-applied: if the preset writes several properties, none moved.

**If no prompt appears, do not judge it — record it.** Paste the complete effect
list from the result and, for each device/property in it, whether M5's
`safety_config.yaml` declares it under `illumination.shutters` or
`illumination.power_properties`. A `System` preset that arms lasers by writing
*trigger-mode* properties (`Mode2`, `Sequence2`) may legitimately carry no
declared illumination effect — a configuration fact worth recording. A preset
that writes a **declared** illumination enable with no prompt is a gate failure
and the most serious outcome possible here.

Do **not** re-run this step answering `y`. The decline limb is what the gate
needs; arming lasers to prove a prompt works is not a trade this gate makes.

## Step B3 — an unclassifiable effect still refuses

Only if A1 shows a group containing a property M5's authorization map excludes or
does not classify. If there is none, write "not reachable on this rig" and skip —
**do not author a preset on the rig to create one.**

> Use `set_config_preset` exactly once with group PASTE_GROUP and preset
> PASTE_PRESET. The refusal is the expected result. Report it verbatim and do
> not retry or work around it.

Expected: a refusal naming the specific device and property, and **0** writes.

---

# Deferred to M2 — the preset this block was actually reported for

`Beads_EM25` is on **M2**, not M5 (operator correction, 2026-08-14; the
2026-08-12 session that produced this block ran there). M2 is unreachable at the
time of writing, so this limb is **deferred, not dropped**, and does not block
50a's merge.

When M2 is next available, run A1 and A2 against it, then:

> Report the complete stored definition of preset Beads_EM25 — every device,
> property and value — from `list_config_groups` alone.

**Record the diff against the 2026-08-12 guess**, which was `EMSwitch=On`,
`Output_Amplifier=Electron Multiplying`, `Gain=25`, either way. If the guess was
right, say so — a guess that turns out right is still a guess, and this block
still earned its place. If it was wrong, that is the strongest result available
to this block and belongs at the top of the report.

If `Beads_EM25` does not appear in A1's listing at all, stop and report that:
enumeration missing a group the operator can see is the whole finding.

## Not in this gate, deliberately

- Creating, editing or deleting a config group or preset. Microclaw has no such
  tool and this block adds none.
- Applying a `System` preset with `y`. See B2.
- Restoring whatever preset was selected before B1. Microclaw does not tidy up
  after itself, and this gate does not ask you to either.

## Reporting

Save each browser conversation with the normal transcript-save control as
`block50a-demo-transcript` and `block50a-m5-transcript`. Return, per step: the
number of tool calls made, the verbatim results, and what **you** observed in
Micro-Manager's panels with your hands off them. Report what happened, including
anything that looked wrong — do not diagnose the system or judge whether the
evidence is valid.
