# design/32 Block 7b — hook illumination, artifacts, and discard gate

Gate for `design32/hook-illumination-and-artifacts` (`c93f8bd`). Do not merge until
every step below has attributable evidence and an explicit verdict.

## What this gate can and cannot settle

Block 7 removed every hardware capability from generated hooks. Block 7b gives three
of them back, in bounded parent-mediated form: **power-only illumination inside a
pre-authorized envelope**, **artifact writes confined to the run's artifact
directory**, and **frame discard**. This gate establishes that the three work live,
that their refusal paths fire on real hardware, and that the four retained M5 hooks
run again after migration.

It does **not** establish worker isolation, deadlines, memory caps, or native-crash
recovery — saved source still executes in the hardware-control process, and source
review plus sha256 pinning remain the containment story until Block 13.

**It also does not satisfy the checklist's closed-loop UV bullet.** See P0: 405 nm is
not authorizable on M5 today, so R5–R8 run against an iBeam laser, and the fixture is
a deterministic ramp rather than genuine feedback. What that proves is the
*authorization path*, completely. What stays open is recorded in "Explicitly not
settled" at the end. Do not let the merge record claim more.

**No interactive confirmation occurs inside an acquisition callback.** pyjavaz
serializes bridge calls behind one lock, so a mid-run prompt is a hazard. The
illumination envelope is confirmed exactly once, before any motion. A prompt after
the run starts is a stop condition.

**Hooks cannot enable light.** A shutter enable is not expressible in the action
union — only power modulation is. That is what makes the next paragraph possible.

**Run the illumination steps with the laser OFF.** The envelope governs
`Power (mW)`; the shutter is a separate property the hook cannot touch. Setting
power on a laser whose `Laser Operation` is `Off` exercises every line of the path —
envelope validation, ceiling refusal, step ratchet, write budget, wind-down, the
bridge write on the callback thread, and its cost — and emits nothing. The
`uv_activation` fixture computes `blink_density` but ramps on a fixed step
regardless, so no real signal is needed to drive it. There is no reason to put light
on a sample, or in the room, to gate this code. See R5's pre-check.

**End-of-run illumination policy belongs to the hook, not the code** (operator
ruling, 2026-07-28). Nothing in microclaw restores, zeroes, or winds down power when
a run ends. Step R5 exists because that ruling is only safe if a hook's own
wind-down cannot be refused by a safety check.

Use one dated evidence directory. Preserve commands, stdout and stderr, environment
identity, inputs, artifacts, sha256 files, and one verdict per step. Never commit
output artifacts.

**Windows command rule:** every command below is PowerShell/cmd-safe. Do not replace
a redirection with a Unix pipeline.

**Interpreter rule:** this rig runs microclaw under **uv**. Use `uv run python`, not
bare `python` — the 2026-07-28 preflight ran `python -m pytest` against
`C:\Users\ries\miniconda3\python.exe` and got `No module named pytest`, because that
interpreter is not the environment microclaw is installed in. Every command below is
written with `uv run`.

## Common setup

```powershell
$Repo     = "<repo>"
$Evidence = "<dated evidence directory>"
$Scratch  = "<path inside configured workspace>\block7b"
$Config   = "<reviewed M5 safety config>"
$Port      = 4827

New-Item -ItemType Directory -Force $Evidence | Out-Null
New-Item -ItemType Directory -Force $Scratch | Out-Null
Set-Location $Repo
git switch design32/hook-illumination-and-artifacts
git rev-parse HEAD > "$Evidence\commit.txt" 2>&1
git status --short > "$Evidence\status.txt" 2>&1
uv run python -V > "$Evidence\python.txt" 2>&1
uv run python -m pytest -q > "$Evidence\pytest.txt" 2>&1
uv run python -m compileall microclaw > "$Evidence\compileall.txt" 2>&1
Get-FileHash tests\fixtures\hooks\m5_legacy\*.py -Algorithm SHA256 | Format-List > "$Evidence\legacy-hashes.txt"
```

**Substitute every `<...>` placeholder with a real path before pasting a prompt.**
A literal `<workspace>` reached the runner in the 2026-07-27 demo gate and the
acquisition failed with `OSError [WinError 123]`.

`$Scratch` must be inside the configured workspace and must already exist. Block 5's
first demo run died on exactly that. Every `save_dir` and `log_path` below lives
under `$Scratch`.

Expected legacy hashes — **verified 2026-07-28, all four matched** after a real
Windows checkout, which is what `tests/fixtures/hooks/m5_legacy/*.py binary` in
`.gitattributes` is for. Re-check them if the branch is re-cloned:

| File | sha256 |
|---|---|
| `filament_position_filter.py` | `7459dcffd95c5385697a2e4cd0daee1da22b71cb0b9742188fa2cb1252e3802e` |
| `mosaic_cell_counter.py` | `8b1e4f63014e847d435595179c727be4424fe41de1423603b041b3e2d9c7eb63` |
| `mosaic_stitcher.py` | `e4719a87224bd66649d963fba48533fe7c4877d7e70447d680d1c016d0d10185` |
| `mosaic_stitcher_rot.py` | `ae029e3c4787869ed79ec5ec13c18fa983219781f7c15fae0412de86a5d1ca2a` |

---

## P0. Pre-flight — SETTLED 2026-07-28

```powershell
uv run python -m microclaw --port $Port --safety-config $Config authorization-map > "$Evidence\authorization-map.json" 2>&1
```

PowerShell wraps the `Connecting to Micro-Manager...` line as a `NativeCommandError`
because the command wrote to stderr. That is cosmetic; the JSON follows it in the
same file. Skip past the first blank line to parse it.

**Measured result:** `mode: guaranteed`, `verdict: complete`, 56 entries. The branch
does not break startup validation on M5.

**The declared illumination surface is exactly four rows:**

| Device | Property | Role |
|---|---|---|
| `iBeamSmartCW-1` | `Laser Operation` | shutter |
| `iBeamSmartCW-1` | `Power (mW)` | power |
| `iBeamSmartCW-Booster` | `Laser Operation` | shutter |
| `iBeamSmartCW-Booster` | `Power (mW)` | power |

No 405 nm row is **declared**. The map shows only what is declared, so on its own it
cannot say whether the hardware exposes one. P1 answered that, and the answer
corrects what this document said first — see below.

## P1. Property probe — SETTLED 2026-07-28, and it changes the plan

**The 402 nm activation line is exposed, writable, and in percent.**
`iChrome-MLE-TCP` is a four-line engine, and the probe's channel pairing reads the
wavelengths straight off the device rather than inferring them from slot order:

| Channel | Hardware | Level property | Value | Range |
|---|---|---|---|---|
| `Laser 1` | 640nm laser diode | `Laser 1: 3. Level %` | 2.24 | 0–100 |
| `Laser 2` | DPSS with AOM | `Laser 2: 3. Level %` | 4.64 | 0–100 |
| `Laser 3` | 488nm laser diode | `Laser 3: 3. Level %` | 3.61 | 0–100 |
| **`Laser 4`** | **402nm laser diode** | **`Laser 4: 3. Level %`** | **25.96** | **0–100** |

Each has its own gates, `Laser N: 1. Enable` and `Laser N: 2. Emission`, plus engine-
wide `All: 1. Enable` / `All: 2. Emission`. None of it is declared; only
`iChrome-MLE-TCP.Label` is, which is the open item design/33 records.

**So UV activation on M5 is a config-declaration decision, not a hardware
impossibility.** This document previously said M5 had no 405 power property. That was
wrong twice over: it read a declaration gap as a capability gap, and the first
version of the probe's name regex required "laser" adjacent to "level" while the real
property is `Laser 4: 3. Level %`. Both are fixed; the probe now carries a comment
recording the miss, because a survey that under-reports reads as "the rig cannot do
this" and that is the expensive direction to be wrong in.

**Better still, `Level %` is genuinely a percentage, 0–100.** Declaring it would give
Block 7b a power property whose units actually match the field that caps it — unlike
the iBeam rows. See the units warning below.

## P1b. What actually drives the 405 — resolved from the EMU config and the Toptica reference

Read against `config_emu_m5.uicfg` and *iChrome MLE Remote Command Reference*
FW 1.4.2.241 §3, both supplied 2026-07-28. The earlier worry in this document that a
`Level %` write might be "optically inert" under TTL was wrong, and the reason it was
wrong matters.

| Stage | Property | Value | Meaning (Toptica §3) |
|---|---|---|---|
| Arm | `Laser 4: 1. Enable` | 0 | `:enable` — "prepare the laser for emission". Necessary, not sufficient. |
| CW override | `Laser 4: 2. Emission` | 0 | believed `:cw` — "**will overwrite the electronic trigger input**". See P2. |
| Amplitude | `Laser 4: 3. Level %` | 25.96 | `:level` — with analog mode off, "**directly controls the output power in a linearized way**"; 50 means 50 % of maximum. |
| Gate | `Laser 4: 4. Use TTL` | 1 | `:use-ttl` — with `:cw` false, emission is switched by the voltage on the digital input line. |
| Mode | `Laser 4: 5. Analog Mode` | 0 | remote mode, so `Level %` is the real power control and not a ceiling for an analog input. |
| Timing | `Laser Trigger.Mode0` | `4 - Follow` | FPGA channel 0 is the 405 (`Laser trigger 0 - Name: 405`). |
| Pulse width | `Laser Trigger.Duration0 (us)` | 1 | range **0 – 1 048 575 µs**. |
| Per-frame | `Laser Trigger.Sequence0` | 65535 | 16-bit pattern; fires on every frame. |

**TTL gates *when* the laser emits; `Level %` sets *how hard*.** The two are
orthogonal, so a ramp on `Level %` scales the amplitude of every FPGA-gated pulse and
is physically meaningful.

**But that is not how this rig does activation.** The EMU config carries a whole
Activation panel — `Activation 1 name: 405`, `Default feedback: 0.01`,
`Def. dynamic factor: 1.5`, `Default max pulse: 10000` — and
`Pulse duration 1 (activation)` maps to `Laser Trigger-Duration0 (us)`. **htSMLM ramps
the pulse width, not the level.**

The EMU config also states the slot mapping outright, so none of it is inferred —
which matters, because design/14 §1 forbids inferring a laser's slot index:

| EMU slot | Name | iChrome channel | Level property |
|---|---|---|---|
| `Laser 0` | 405 | `Laser 4` (402nm diode) | `Laser 4: 3. Level %` |
| `Laser 1` | 488 | `Laser 3` | `Laser 3: 3. Level %` |
| `Laser 2` | 561 | `Laser 2` (DPSS with AOM) | `Laser 2: 3. Level %` |
| `Laser 3` | 640 | `Laser 1` | `Laser 1: 3. Level %` |

That matches `get_system_state`, which already reports slot 0 at 25.96 — microclaw is
reading the 405 level today, through the EMU map, without being able to write it.

### The dose consequence, which the declaration does not remove

Per-frame UV dose is proportional to **`Level %` × `Duration0`**.
`SetIlluminationPower` bounds the first term only. The second lives on
`Laser Trigger`, an **excluded** device spanning six orders of magnitude.

Excluded means microclaw cannot write it, so no hook can change the pulse width — but
equally, no microclaw policy bounds it. Whatever EMU or the operator last set stands
for the whole run. **An illumination envelope on `Level %` bounds what microclaw can
change; it does not bound dose.** Say it that way in any report. Note also that the
Block 4 ledger's `illuminated_ms` is derived from camera exposure, not from the FPGA
duty cycle, so it is an exposure budget and not a dose budget on this rig.

Operator ruling 2026-07-28: **declare `Level %` now and treat FPGA pulse duration as a
later, separate problem.** Bounding duration properly means making it a typed actuator
with dose expressed as the product — design/33 Phase 2 territory, not Block 7b's.

## P2. What `Laser 4: 2. Emission` is — SETTLED 2026-07-28

**Measured on M5: neither `1. Enable` nor `2. Emission` alone produces light. Both
must be 1.**

That is the `:cw` signature exactly. Manual §3 p.15: *"Set this parameter #t **while
the laser is enabled** to switch on cw emission. This will overwrite the electronic
trigger input. **This parameter has no effect while the laser is disabled.**"* So
`2. Emission` is `:cw`, confirmed behaviourally and documentarily rather than inferred
from property order. `1. Enable` is `:enable` — it arms and nothing more.

`Enable` alone produced no light because nothing was pulsing the TTL line: `Mode0` is
`Follow`, so the FPGA emits on camera triggers, and no acquisition was running.
Consistent, not contradictory.

**The gates are in series, and `Enable` is the master.** With `Enable = 0` nothing
emits by any path. With `Enable = 1` light can leave by **two** routes:

1. `Emission = 1` — cw, continuous, and per the manual it *overrides the electronic
   trigger input* entirely; and
2. the FPGA TTL line, with `Mode0 = Follow` and `Sequence0 = 65535` — **every frame of
   any acquisition**, at whatever `Duration0` happens to be.

Route 2 deserves emphasis: **arming the laser is sufficient for an acquisition to emit
405 pulses**, with no further microclaw action and nothing else to confirm. Pulse
width is 1 µs today, so the dose is small — but it is not zero, and it is not
something microclaw can see or bound. Anyone reading `Enable = 1` as "armed but dark"
is wrong during an acquisition.

Route 2 is a property of *this rig*, not of microclaw. Most Micro-Manager systems have
no FPGA in the light path and no second emission route to reason about. Record it here
as an M5 fact; it must not become an assumption in the code.

**Both properties are therefore declared as shutters.** `Enable` because it is the
master gate; `Emission` because it independently forces continuous emission and
bypasses the FPGA. Declaring both also means `shutter_all` drives both to 0 on session
teardown, which is the correct off state.

### Declaration, settled by P2

```yaml
illumination:
  power_properties:
    - {device: iChrome-MLE-TCP, property: "Laser 4: 3. Level %"}   # 405/402 nm, 0-100 %, linearized
  shutters:
    - {device: iChrome-MLE-TCP, property: "Laser 4: 1. Enable",   on_value: "1", off_value: "0"}
    - {device: iChrome-MLE-TCP, property: "Laser 4: 2. Emission", on_value: "1", off_value: "0"}
```

Only the 405 line is declared. The 488, 561 and 640 levels stay undeclared and
therefore unwritable, which is the right default until something needs them.

## P2 procedure, retained for the record

`2. Emission` is believed to be `:cw` purely from the order the properties appear in,
matched against the manual's parameter order. That inference decides which property
belongs behind `require_confirm_on_enable`, so confirm it rather than declare on it.
If it is `:cw`, it turns the laser on continuously and **overrides the FPGA gate
entirely** — which makes it the single most important property on the engine to put
behind a human confirmation.

**Do this by hand in Micro-Manager's Device Property Browser, not through microclaw.**
These properties are undeclared, so microclaw refuses to write them, and the point of
P2 is to decide the declaration — nothing should be declared to run the test that
decides the declaration.

Near-zero-dose procedure:

1. Set `Laser 4: 3. Level %` to **0**. Record it.
2. Set `Laser 4: 1. Enable` to 1. Read `Laser 4: 6. Status` and record the text.
3. Set `Laser 4: 2. Emission` to 1. Read `Laser 4: 6. Status` again.
4. Read `Analog Input.AnalogInput3` — EMU maps it as `Laser powermeter` — before and
   after step 3.
5. Set `Emission` to 0, then `Enable` to 0. Confirm `Status` returns to its step-1 text.

**Expected observable if `Emission` is `:cw`:** the status text gains a cw/emission
indication at step 3 that step 2 did not produce. Manual §3 `laser1:status` bit 2 is
"in cw mode", so the text form should say so. At `Level % = 0` the emitted power is at
its floor throughout.

**Result 2026-07-28:** both were required; see the settled section above.

The probe also found `iBeamSmartCW.Power (mW)` — a **third** iBeam laser, undeclared,
range 0–75 mW, alongside `-1` and `-Booster`. Worth knowing whether that is a real
third laser or a stale entry in the MM config.

Four `HamamatsuHam_DCAM.INTENSITY LUT ...` properties matched the name heuristic and
are now rejected automatically as a `CameraDevice` cannot illuminate. They appear in
the report under "rejected as non-emitting" rather than vanishing, so the exclusion is
reviewable.

**Operator ruling (2026-07-28): run R5–R8 against `iBeamSmartCW-1.Power (mW)`,
with the laser off.** It is already declared and reviewed, it exercises every line of
the envelope path, and it needs no config change. Declaring a 405 power property on
the iChrome engine is a separate reviewed item; design/33 records that engine as an
open question, and the Block 3b gate already caught one attempt to widen it.

### Before declaring anything new: run the property probe

Do not hand-list the rig's properties. `design/32-block7b-device-property-probe.py`
enumerates every loaded device and property read-only — it calls only `get_*`/`is_*`/
`has_*`, writes nothing, moves nothing, and opens no shutter — then reports which
properties look like continuous power controls, which enable properties sit on the
same devices, which of those the reviewed config already declares, and a proposed
`illumination:` block for the rest.

```powershell
uv run python design\32-block7b-device-property-probe.py --port $Port --config $Config --out "$Evidence\device-properties.json" > "$Evidence\device-properties.txt" 2>&1
```

This is how to find whether the iChrome engine exposes a 405 power property at all,
under whatever name its driver uses. **Its proposal is a starting point for review,
not a config to paste.** It matches on property names, and a name match is not
evidence that a device emits light or that its units are what the field name implies.
The Block 3b gate refused a laser engine's `State` write that had been auto-admitted
without a config edit; that refusal is the standard this proposal has to meet.

### Units warning — read before choosing a ceiling

The declared property is `Power (mW)`. Every field and message in the code says
**percent**: `illumination.max_power_percent`, `max_power_percent` in the envelope,
and the refusal *"X% exceeds illumination.max_power_percent (Y%)"*. On M5 those
numbers are **milliwatts**, not percent, and the code does not know the difference.

This is pre-existing and already recorded in design/33 ("Illumination power units",
line 562) — Block 7b did not introduce it. But Block 7b is the first thing that lets
*generated code* choose that number per frame, unattended, so the caveat now has
teeth it did not have when only a human could issue the write. Choose every ceiling
below in **mW**, sanity-check it against the laser's actual range (iBeam is 0–75 mW),
and record in the evidence that you did.

**Measured consequence on M5, and it is not theoretical.** The reviewed config sets
`max_power_percent: 100.0`, and `iBeamSmartCW-1.Power (mW)` has a driver range of
0–75. A raw value of 75 is always less than 100, so **the configured ceiling can
never refuse an iBeam power write.** The only thing bounding a ramp on that laser
today is `max_power_step_factor: 3.0`, which limits how fast it climbs, not how high.
`iBeamSmartCW-Booster` is worse: range 0–150, so the ceiling binds only above 100 mW.

That is a finding about the M5 config, not about Block 7b's code — the envelope
ceiling you pass per run *does* bind, and R5 relies on it. But it means the config
ceiling is not a second line of defence on this rig, and R7 case 1 (envelope ceiling
above the configured ceiling) can only be exercised by lowering
`max_power_percent` to a value inside the laser's range first. Do that in a copy of
the config under `$Evidence`, not in the reviewed file, and retain the diff.

Declaring the iChrome `Level %` rows would improve this: 0–100 in genuine percent
means `max_power_percent` becomes dimensionally meaningful for those channels.

Before R5, read and record from `$Config`: the configured
`illumination.max_power_percent` and `illumination.max_power_step_factor`. Every
number in R5 and R7 is chosen relative to them.

---

## How to save the migrated hooks

The migrated hooks are already written, at
`tests\fixtures\hooks\m5_migrated\*.py`. You do not retype them and you do not ask
the agent to author them.

**Do not copy the files into `~\.microclaw\hooks\`.** The manifest is the registry,
not the directory. A `.py` sitting there with no manifest entry fails with
`KeyError: No saved hook named '<name>'`; an entry without a `sha256` is refused as
*"predates hash-pinning"*; and an entry whose `accepted_warnings` you did not record
fails on first load with *"has lint warnings the user never accepted"*. Hand-writing
a manifest entry to work around that also throws away the consent record the entry
exists to hold.

Instead, use the two tools that exist for exactly this. In a microclaw session:

> Call `read_hook_from_file` on
> `<repo>\tests\fixtures\hooks\m5_migrated\mosaic_stitcher.py`. Show me the source
> and the lint warnings. After I confirm, save it with `generate_and_save_hook`
> under the name `mosaic_stitcher_v2`, description
> "Block 7b migrated: stitches tiles by intended stage XY, emits the mosaic as a
> parent-written artifact", source `user_provided`.

`read_hook_from_file` reads the file and runs the advisory lint; it does not save.
`generate_and_save_hook` writes the file into the registry, pins its sha256, and
records the warnings you accepted. That pair is the whole flow.

**Use new names** (`_v2` or similar) rather than overwriting the existing M5 entries.
The legacy entries must stay in the registry — R1 needs them to prove they are still
refused, and the checklist is explicit that they are copied, never deleted.

### You must issue that prompt SIX times, once per hook

The block above is one worked example, not the whole step. On 2026-07-28 it was run
once, `mosaic_stitcher_v2` was saved, and the session moved on — so R1's migrated half
ran against a single hook and R2 could not run at all. Six separate prompts, six
confirmations, six saves:

| # | Fixture file | Save as | Used by |
|---|---|---|---|
| 1 | `filament_position_filter.py` | `filament_position_filter_v2` | R1, R2, R4 |
| 2 | `mosaic_cell_counter.py` | `mosaic_cell_counter_v2` | R1, R2 |
| 3 | `mosaic_stitcher.py` | `mosaic_stitcher_v2` | R1, R2, R3 |
| 4 | `mosaic_stitcher_rot.py` | `mosaic_stitcher_rot_v2` | R1, R2, R3 |
| 5 | `uv_activation.py` | `uv_activation` | R5, R7, R8 |
| 6 | `uv_activation_wind_down.py` | `uv_activation_wind_down` | R5, R5b, R6 |

**Checkpoint before leaving this section.** Call `list_hooks()` and confirm all six
`saved` entries are present alongside the four legacy ones — ten in total. A missing
entry surfaces later as an unhelpful "Unknown hook strategy", far from its cause.
Watch for a trailing space in the name: `"mosaic_cell_counter_v2 "` will not resolve.

Retain the manifest before and after:

```powershell
$Hooks = "$env:USERPROFILE\.microclaw\hooks"
Copy-Item "$Hooks\manifest.json" "$Evidence\manifest-before.json"
# ... after saving all five ...
Copy-Item "$Hooks\manifest.json" "$Evidence\manifest-after.json"
```

Note that the `sha256` in the manifest will **not** equal `Get-FileHash` of the file
on disk: the manifest pins LF-normalized text, the file on disk has CRLF. That is
the D2 finding from the Block 7 gate, and it is expected, not corruption.

---

## Demo-core gate — prove the envelope is not M5-shaped

**Why this exists.** M5 is an unusual Micro-Manager system: it drives its lasers
through EMU and MicroFPGA, and most MM installations use neither. Most rigs look far
closer to the stock demo config. Every illumination finding above came from M5's
hardware, and the demo profile at `design/33-block5-demo-safety-config.yaml` declares
`shutters: []` and `power_properties: []` — so the envelope path has never run against
an ordinary config at all, only against M5 and against synthetic unit tests.

That is a gap in this gate, not in the code: the shipped diff contains no reference to
iChrome, iBeam, MicroFPGA, TTL, EMU or htSMLM, and the envelope names its device and
property from the caller. These steps confirm that rather than assuming it.

Run on the stock `MMConfig_demo.cfg` with the ZMQ bridge on port 4827.

### D1. The probe behaves on a config with no lasers

```powershell
uv run python design\32-block7b-device-property-probe.py --port $Port --config design\33-block5-demo-safety-config.yaml --out "$Evidence\demo-properties.json" > "$Evidence\demo-properties.txt" 2>&1
```

**Expected observable:** it completes, enumerates the demo devices, and reports
candidate power properties honestly — including "none matched" if the demo config has
no continuous level control. The channel-pairing and gating-context features are
iChrome-shaped heuristics and must degrade to nothing here rather than inventing
structure.

**Stop condition:** a crash, or a proposal that would declare a demo state device or
camera property as illumination power.

### D2. Declare a stand-in and start clean

From D1's output pick one two-state shutter-ish property and one continuous writable
numeric property to stand in for a laser. The demo config's `LED Shutter` and
`White Light Shutter` are real `ShutterDevice`s; for power, use whatever bounded float
D1 actually found. Copy the demo profile into `$Evidence`, add only those two rows,
and retain the diff — do not edit the checked-in file.

**Say plainly in the evidence that this is a stand-in.** Nothing on a demo core emits
light; the point is the authorization path, not photons.

```powershell
uv run python -m microclaw --port $Port --safety-config "$Evidence\demo-safety-config.yaml" authorization-map > "$Evidence\demo-map.json" 2>&1
```

**Expected observable:** map `complete`, with the two declared rows appearing as
`dedicated-illumination`.

### D3. The full envelope sequence, on a stock config

Run the R5 sequence against the demo stand-in with the same shape of numbers — an
envelope ceiling the ramp reaches, and `max_writes` below the frame count.

**Expected observable:** the same ordering as R5 — accepted increases, a budget
refusal, a ceiling refusal, and a wind-down that lands with the budget spent. One
confirmation before any motion, none after.

**Stop condition:** any behaviour that differs from R5 in a way that traces to the
device rather than to the numbers chosen. That would mean the envelope has acquired a
dependency on M5's hardware, which is exactly what this section is here to catch.

---

## Migration gate — the four retained hooks

### R1. Legacy hooks are still refused, migrated hooks run

Two halves. Run the legacy half first, because it must take no action at all.

**Legacy half.** For each of the four original entries already in M5's registry
(`filament_position_filter`, `mosaic_cell_counter`, `mosaic_stitcher`,
`mosaic_stitcher_rot`), paste:

> Run `run_multiposition_acquisition` over two positions from the current position
> list with `hook_strategy="<legacy name>"`, `save_dir="<$Scratch>\r1-legacy"`,
> `log_path="<$Scratch>\r1-legacy\<legacy name>.json"`. I expect this to be refused.
> Report the exact error text and confirm no stage motion and no exposure occurred.

**Expected observable:** four refusals carrying *"writes its own log"*, each raised
before any hardware moves. No dataset directory, no log file, no stage motion.

**Migrated half.** For each `_v2` hook, run the same call with the `_v2` name over
the same fields used for that hook's pre-Block-7 evidence.

**Expected observable:** four runs that complete and write a parent-owned log whose
per-frame records carry `"schema": "microclaw.analysis-observation/v1"` and
`"status": "unverified"`. An untrusted hook cannot self-assert `observed`.

```powershell
Copy-Item "$Scratch\r1-*\*.json" "$Evidence\" -Recurse
```

**Stop condition:** a refusal that moves the stage first; a migrated hook that needs
a capability this block did not restore. Per the checklist that second case is a
**fourth gap, not a bug** — record it and stop; do not patch around it.

### R2. Numerical fidelity against pre-Block-7 behaviour

For each migrated hook, compare against the retained pre-Block-7 evidence:

- `filament_position_filter_v2` — `filament_score`, `snr`, `ridge_coverage`, and the
  keep/discard decision, per field.
- `mosaic_cell_counter_v2` — `running_cell_count` and `mean_cell_area_um2` per frame.
- both stitchers — the assembled canvas, pixel for pixel, including dtype and
  orientation.

`mosaic_cell_counter._label` is the original whole-canvas pure-Python flood fill and
was deliberately not optimized. On a realistic M5 mosaic it may be slow enough to
matter. Time the run and record the number — if it is impractical, that is a finding
for the design gate, not a reason to change the algorithm inside this gate.

**Stop condition:** any numerical difference. Migration was meant to be mechanical.

### R3. Artifacts are confined, bounded, and hashed

**R3a — success.** Paste:

> Run `run_multiposition_acquisition` over <n> positions with
> `hook_strategy="mosaic_stitcher_v2"`, `hook_params={"n_tiles": <n>,
> "pixel_size_um": <measured>, "filename": "mosaic.tiff"}`,
> `save_dir="<$Scratch>\r3a"`, `log_path="<$Scratch>\r3a\stitcher.json"`,
> `artifact_limits={"max_artifact_bytes": 50000000, "max_count": 2,
> "max_total_bytes": 50000000}`. Afterwards, read the hook log and report the
> recorded artifact path, size, and sha256.

Then verify independently:

```powershell
Get-ChildItem -Recurse "$Scratch\r3a" > "$Evidence\r3a-tree.txt" 2>&1
Get-FileHash "$Scratch\r3a\<name>\artifacts\mosaic.tiff" -Algorithm SHA256 > "$Evidence\r3a-artifact-hash.txt" 2>&1
```

**Expected observable:** the TIFF is inside the run's `artifacts` directory and
nowhere else; the log records path, size, and sha256; the recorded hash equals
`Get-FileHash`. Compare with what this hook did before — `tifffile.imwrite` to an
unconfined constructor string — and confirm no file appears at any path the hook
names itself.

**R3b — refusal.** Re-run with `max_artifact_bytes` set below the real mosaic size.

**Expected observable:** refusal naming both the actual size and the limit; no file
created; **the acquisition continues and completes**.

**Stop condition:** a file outside the artifact directory, a hash mismatch, or a
partial file left behind after a refusal.

### R4. Discard saves storage, not dose

> Run `run_multiposition_acquisition` over <n> positions with
> `hook_strategy="filament_position_filter_v2"`, `save_dir="<$Scratch>\r4"`,
> `log_path="<$Scratch>\r4\filter.json"`. Choose a field set where some positions
> clearly fail the filament threshold. Afterwards report, from the hook log and the
> dataset: how many positions were visited, how many images were saved, and the
> acquisition ledger's frame count and illuminated time.

**Expected observable:** discarded fields produce an observation record **and** an
`outcome: "discarded"` record, and no saved image. Saved image count equals the kept
count. Positions visited equals the full planned count, and the ledger's frames and
illuminated time reflect **all** fields, not only the kept ones.

**Stop condition:** the ledger under-counts exposure for discarded frames, or any
output describes discard as skipping acquisition or reducing dose. Per design/27 the
position was still moved to and still exposed.

---

## Illumination gate — the envelope

Run last, with the operator present. **Do not enable the laser.** Leave
`iBeamSmartCW-1`'s `Laser Operation` at `Off` for every step here.

### R5pre. Does the driver accept a power write while the laser is off? — PASS 2026-07-28

The whole shutter-closed plan rests on this, so it was established before R5 rather
than during it. With `iBeamSmartCW-1.Laser Operation` = `Off`:

- `Power (mW)` read `10.0000`; `get_device_property_info` reported `Float`, not
  read-only, range 0–75.
- `set_device_property` to `5` returned `Set iBeamSmartCW-1.Power (mW) = '5'.`
- Read-back returned `5.0000`.
- All four iChrome channels read `enabled = 0` throughout; nothing emitted.

**The driver accepts and retains a power write with the laser off.** R5–R8 run with
no emission. Evidence: `block7-r5pre-history.json`.

**The same run produced unplanned refusal evidence.** Setting
`iBeamSmartCW.Power (mW)` — the third, *undeclared* iBeam — was refused with
`RigAuthorizationError: Property write iBeamSmartCW.Power (mW) is excluded from the
authorization map.` A real, undeclared laser power property on live hardware, refused
before the write, with the range check having already passed. That is R7 case 2's
observable, obtained for free; cite it there rather than re-running it.

Choose `max_power_percent` (in **mW**, per the units warning) low enough that the
fixture ramp **reaches it during the run**, and `max_writes` **smaller than the frame
count** so budget exhaustion happens mid-run rather than at its edge. With
`uv_activation` defaults, the value is `start_percent + step_percent` per frame,
clamped at `ceiling_percent` — set the hook's `ceiling_percent` above the envelope
ceiling so the *envelope* is what refuses, not the hook's own clamp.

Note what the fixture is: it computes `blink_density` and **reports** it, but ramps
on a fixed step regardless. It is a deterministic ramp that drives the authorization
path to its limits, not a feedback loop. That is deliberate — a gate needs to fail on
schedule — and it is why the closed-loop bullet stays open.

### R5. Ramp, refuse at the ceiling, refuse at the budget, then wind down

> Run `run_adaptive_timelapse` with `n_frames=<N>`, `interval_s=<i>`,
> `save_dir="<$Scratch>\r5"`, `log_path="<$Scratch>\r5\uv.json"`,
> `hook_strategy="uv_activation"`,
> `hook_params={"start_percent": <low>, "step_percent": <s>, "ceiling_percent": <above the envelope ceiling>}`,
> `illumination_envelope={"device": "iBeamSmartCW-1", "property": "Power (mW)",
> "max_power_percent": <low ceiling in mW>, "max_writes": <fewer than N>}`.
> Report every accepted and refused illumination record from the log in order.

**Expected observable, in order:**

1. Exactly **one** confirmation, before any motion, naming the device, the ceiling,
   the write budget, and stating that generated code will drive it unattended.
2. Accepted increases, each recorded with the value written.
3. At the ceiling: `proposal exceeds authorized envelope ceiling`. Read the device
   independently and confirm it never went above the ceiling.
4. After `max_writes` increases: `authorized illumination write budget exhausted`.
5. **A wind-down still succeeds with the budget at zero.** This is the operator
   ruling made testable. Prove it explicitly — see R5b.
6. No prompt of any kind after the run starts.

**R5b — the wind-down against a SPENT budget.** Use `uv_activation_wind_down`, and
choose numbers so the ramp is *accepted* often enough to exhaust `max_writes` before
the ceiling refuses it. The 2026-07-28 attempt used `step_percent: 10`, so every
proposal exceeded the ceiling immediately, no budget was consumed, and the wind-down
proved nothing. Small steps are the whole point:

```
n_frames = 7,  hook_strategy = "uv_activation_wind_down"
hook_params = {start_percent: 1, step_percent: 1, ceiling_percent: 15,
               wind_down_after: 5, floor_percent: 0}
illumination_envelope = {device: "iChrome-MLE-TCP", property: "Laser 4: 3. Level %",
                         max_power_percent: 8, max_writes: 2}
```

| Frame | Proposed | Required outcome |
|---|---|---|
| 1 | 2 | accepted |
| 2 | 3 | accepted — budget now spent |
| 3 | 4 | refused, **budget exhausted** (not ceiling: 4 ≤ 8) |
| 4 | 5 | refused, budget exhausted |
| 5 | 6 | refused, budget exhausted |
| 6 | 0 | **accepted** — wind-down with the budget at zero |
| 7 | 0 | accepted |

Frame 6 is the evidence. If it is refused, the operator ruling that end-of-run policy
belongs in the hook is unsafe as implemented, and that is a stop condition.

**Expected observable:** the zero write is **accepted**, reaches the device, and does
not decrement anything. A hook implementing its own end-of-run policy is not blocked
by a safety check.

Then read the device power **after the run ends** and record it. Nothing restores it;
that is by design, and the number belongs in the evidence.

**Stop condition:** any write above the envelope ceiling or the configured
`max_power_percent`; a step larger than `max_power_step_factor` allows; a refused
wind-down; a mid-run prompt; a ramp that never reached its ceiling.

### R6. A declined envelope takes no exposure

Re-run R5 and decline the confirmation.

**Expected observable:** an attributable refusal naming the device and property; no
reservation, no motion, no exposure, no dataset directory.

### R7. Plan-time envelope refusals

Three separate attempts. Each must fail before any hardware action, with a distinct
message:

1. envelope ceiling above the configured `illumination.max_power_percent`;
2. `{"device": "iChrome-MLE-TCP", "property": "Power"}` — undeclared, and the case
   P0 found. Expect *"not declared in illumination.power_properties"*;
3. `hook_params` carrying `{"device": "iBeamSmartCW-1", "out_path": "C:\\temp\\x.tif",
   "illumination_envelope": {}}` — all three must be stripped, and the hook must not
   receive them.

### R8. Callback-thread cost of a bridge write

The illumination write happens on the acquisition callback thread. design/11b Spike C
established the bridge is not thread-affine and design/16 established that calls
serialize behind one lock, so this is expected to work and to block briefly — the
cost is unmeasured.

Run the same timelapse twice: once with the envelope and the ramping hook, once with
an observation-only hook and no envelope. Report per-frame overhead against Block 4's
measured ~657 ms/frame baseline.

**Stop condition:** a hang, a dropped frame, an out-of-order frame, or overhead large
enough to change what an acquisition can be planned to do. Report the number either
way — a measured cost is the deliverable, not a pass/fail.

---

## Results 2026-07-28, closeout session (on `93bf504`)

**R1 migrated — PASS.** `mosaic_cell_counter_v2` ran two tiles with a running count of
12 → 24 and mean area 23.6 → 24.6 µm². `mosaic_stitcher_rot_v2` emitted
`mosaic_rot.tiff` (2306 × 2621, `rot90_k: 1`) through the parent artifact path,
recorded `accepted | parent wrote bounded artifact`. All four migrated hooks have now
run on the rig.

**R4 — PASS (plumbing route).** With `min_filament_score: 0.5`, all four fields scored
~0.122–0.124 and were discarded: four `kept: false` observations, four accepted
`DiscardFrame` actions, four `outcome: "discarded"` records. The dataset directory
`r4_discard_1` contains a **zero-byte `NDTiff.index` and no image stack at all** —
four positions visited and exposed, zero images saved. This establishes the discard
plumbing and nothing biological.

**R8 — PASS, measured.** At `interval_s=0`, 20 frames each: `r8_uv` **2.531 s** vs
`r8_control` (`position_filter`, no envelope) **2.437 s**. Difference **0.094 s**, i.e.
**≈4.7 ms per illumination write**. Against Block 4's ~657 ms/frame baseline, and
against these runs' own ~126 ms/frame, the callback-thread bridge write is not a
material cost. One pair of runs, so treat it as an order of magnitude rather than a
characterised timing.

### R5c did not test the ratchet, and found two defects instead

The device sat at **0.0000 %** — left there by R5b's wind-down — so the run never
reached the step-factor check.

**F1. The ratchet is inert from zero.** `SafetyGuard.check_illumination` guards the
ratio with `if old > 0 and new / old > factor`. From `old = 0` there is no ratio and
no refusal, so the proposal of 25 % against a configured 3.0× factor was **not**
refused. The envelope ceiling remains the only bound in that state.

This is pre-existing code, but Block 7b changes who can reach it and how often: the
block's own recommended pattern is a hook that winds down to 0 at end of run, which
makes "device at zero" the *normal* starting state for the next run, and generated
code now proposes power per frame unattended. A configuration reading
`max_power_step_factor: 3.0` does not suggest "except from zero, where any value up to
the ceiling is one write away."

**F2. A write reported as failed may have succeeded.** Frame 1's write to 25 % was
recorded as `illumination_write_failure` on a `Serial timeout occurred. (17)` — and
`get_system_state` immediately afterwards read `power_pct: "25.0000"`. The device
applied the value and timed out before acknowledging. The parent updates
`last_written` only on success, so its ratchet baseline stayed at 0 while the hardware
sat at 25.

The divergence is in the permissive direction: every later ratio is computed against a
value lower than reality, so the ratchet allows larger real jumps than configured. The
same partial-success pattern appeared again at the end of the session on a manual
write, so it is reproducible rather than a one-off.

Neither finding is visible off-rig — a mocked core neither starts at zero by accident
nor half-fails.

## Closing the gate — exactly what is left, in order

Everything below assumes the branch is at `eeef7da` or later and the six hooks are
saved. Steps 1–2 need no microscope at all; run them first.

### Step 1 (off-rig). R7 cases 1–3, without an agent

The agent refused to make these calls, so bypass it. The probe touches no hardware —
every check fails before the code reaches a device — so Micro-Manager need not be
running.

```powershell
uv run python design\32-block7b-r7-refusal-probe.py `
    --config $Config --hook mosaic_stitcher_v2 > "$Evidence\r7.txt" 2>&1
```

**Expected:** three `PASS` lines and `R7 PASS`. The messages it prints are the
evidence; keep the file. Re-run with `--hook` set to each other saved hook you intend
to use — a key is harmless either because it was stripped or because that hook's
`__init__` happens not to accept it, and only the first is a guarantee.

**Stop condition:** any `FAIL`, or `case 3: a smuggled key reached the constructor`.

### Step 2 (off-rig). R2 fidelity, from datasets you already have

```powershell
uv run python design\32-block7b-r2-fidelity-diff.py `
    --dataset "$Scratch\r3a\multipos_3" --hook mosaic_stitcher `
    --params '{\"pixel_size_um\": 0.127, \"n_tiles\": 2}' > "$Evidence\r2-stitcher.txt" 2>&1
```

Repeat with `--hook mosaic_cell_counter`, `--hook mosaic_stitcher_rot`, and
`--hook filament_position_filter`, pointing `--dataset` at any NDTiff those hooks
have run over. Frames are read from disk; nothing is exposed.

**Expected:** `R2 PASS` — every shared measurement identical, and the discard decision
agreeing (legacy returning `None` ⇔ migrated emitting `DiscardFrame`).

Two differences are expected and are reported as `only_legacy` / `only_migrated`
rather than mismatches: the stitchers renamed `mosaic_path` to `mosaic_filename`, and
`writer` is gone because the parent now owns the write.

**Stop condition:** any `DIFFERS` line. Migration was meant to be mechanical.

### Step 3 (M5). R1 migrated — the two hooks that have never run

Paste each separately. Both need `protocol="timelapse"`; `protocol="snap"` is
display-only and is refused with a different message, which is what happened on
2026-07-28.

> Run `run_multiposition_acquisition` over two positions from the current position
> list with `protocol="timelapse"`, `protocol_params={"n_frames": 1, "interval_s": 0}`,
> `hook_strategy="mosaic_cell_counter_v2"`, `name="r1_counter"`,
> `save_dir="<$Scratch>\r1-migrated"`,
> `log_path="<$Scratch>\r1-migrated\counter.json"`. Then call `read_hook_log` on that
> path and show me every record.

> Run `run_multiposition_acquisition` over two positions from the current position
> list with `protocol="timelapse"`, `protocol_params={"n_frames": 1, "interval_s": 0}`,
> `hook_strategy="mosaic_stitcher_rot_v2"`,
> `hook_params={"n_tiles": 2, "pixel_size_um": 0.127, "rot90_k": 1,
> "filename": "mosaic_rot.tiff"}`, `name="r1_rot"`,
> `save_dir="<$Scratch>\r1-migrated"`,
> `log_path="<$Scratch>\r1-migrated\rot.json"`,
> `artifact_limits={"max_artifact_bytes": 50000000, "max_count": 2,
> "max_total_bytes": 50000000}`. Then call `read_hook_log` and show me every record.

**Expected:** both complete; every per-frame record carries
`"schema": "microclaw.analysis-observation/v1"` and `"status": "unverified"`; the rot
stitcher's artifact lands under the run's own `..._1\artifacts\` directory.

### Step 4 (M5). R4 — discard, plumbing route

The default threshold cannot discard an empty field: on 2026-07-28 empty frames scored
0.12–0.13 against a 0.02 threshold. Raise the threshold above the noise floor so the
discard path actually runs, and **label the result plumbing-only** — it establishes
nothing biological.

> Run `run_multiposition_acquisition` over four positions from the current position
> list with `protocol="timelapse"`, `protocol_params={"n_frames": 1, "interval_s": 0}`,
> `hook_strategy="filament_position_filter_v2"`,
> `hook_params={"min_filament_score": 0.5}`, `name="r4_discard"`,
> `save_dir="<$Scratch>\r4b"`, `log_path="<$Scratch>\r4b\filter.json"`.
> Then report, from the hook log and the dataset: how many positions were visited,
> how many images were saved, and the acquisition ledger's frame count and
> illuminated time.

**Expected:** every field `kept: false` with a `DiscardFrame` action and an
`outcome: "discarded"` record; **no saved images** in the dataset; positions visited
and ledger frames/illuminated time reflecting **all four** fields.

**Stop condition:** the ledger under-counts exposure for discarded frames, or any
summary calls this a dose saving. Per design/27 the stage still moved and the camera
still fired.

### Step 5 (M5). R5c — the step-factor ratchet — REDO, and seed the device first

The one refusal reason never yet produced. A 5× jump against the configured 3.0×.

**Seeding is not optional, and skipping it is what made the 2026-07-28 attempt
vacuous.** The device was at 0 %, left there by R5b's wind-down, and the ratchet does
nothing from zero (F1 above) — so the 25 % proposal was not refused and the test
measured nothing. Issue this first, in the same session, and confirm the read-back:

> Set `iChrome-MLE-TCP` property `Laser 4: 3. Level %` to `5`, then read it back and
> tell me the value. The laser must stay disarmed — do not touch
> `Laser 4: 1. Enable` or `Laser 4: 2. Emission`.

Only when the read-back says `5.0000` does the ratchet have a baseline to work from,
making the first proposal of 25 a 5× step. If the write reports a serial timeout, read
it back anyway — on this rig a timeout can still have applied the value (F2), and the
read-back is what settles it.

> Run `run_adaptive_timelapse` with `n_frames=2`, `interval_s=1`,
> `save_dir="<$Scratch>\r5c"`, `log_path="<$Scratch>\r5c\step.json"`,
> `name="r5c_step"`, `hook_strategy="uv_activation"`,
> `hook_params={"start_percent": 5, "step_percent": 20, "ceiling_percent": 100}`,
> `illumination_envelope={"device": "iChrome-MLE-TCP",
> "property": "Laser 4: 3. Level %", "max_power_percent": 30, "max_writes": 5}`.
> I will accept the confirmation. Then call `read_hook_log` and show me every record.

**Expected:** the first proposal is refused with a reason naming the **step factor**,
distinct from a ceiling refusal — 25 is below the envelope's 30, so only the ratchet
can refuse it.

**Stop condition:** refused for the wrong reason, or accepted. If it is accepted,
check the seed read-back before blaming the code: from 0 the ratchet is inert by
design, and that is the one way this test passes vacuously.

### Step 6 (M5). R8 — re-run at zero interval

At `interval_s=1` the inter-frame wait absorbed the whole cost. Back-to-back frames,
same as Block 4's G1/G6.

> Run these two acquisitions and report the `duration_s` of each.
> First: `run_adaptive_timelapse` with `n_frames=20`, `interval_s=0`,
> `save_dir="<$Scratch>\r8"`, `log_path="<$Scratch>\r8\uv.json"`, `name="r8_uv"`,
> `hook_strategy="uv_activation"`,
> `hook_params={"start_percent": 1, "step_percent": 0.1, "ceiling_percent": 5}`,
> `illumination_envelope={"device": "iChrome-MLE-TCP",
> "property": "Laser 4: 3. Level %", "max_power_percent": 5, "max_writes": 20}`.
> Second: the same call with no `illumination_envelope` and
> `hook_strategy="position_filter"`, `name="r8_control"`,
> `log_path="<$Scratch>\r8\control.json"`. Report both durations and the per-frame
> difference. Do not estimate a duration you were not given.

`step_percent: 0.1` keeps every proposal inside the ratchet and the ceiling, so all 20
writes are accepted and the measurement is of 20 successful bridge writes rather than
of refusals. `position_filter` is the control because it is a pre-coded hook that runs
no analyzer — do **not** use `snr_observer`, whose ~320 ms/frame of `compute_stats`
swamps the effect.

**Expected:** a per-frame difference attributable to the write, against Block 4's
~657 ms/frame baseline. Report the number whichever way it falls.

**Stop condition:** a hang, a dropped or out-of-order frame, or overhead large enough
to change what an acquisition can be planned to do.

### Step 7 (demo core). D1–D3

Not M5. Stock `MMConfig_demo.cfg`, per the demo-core section above. Worth doing before
another M5 session in case the envelope turns out to be M5-shaped.

### Not required to close the gate

- **R1 legacy** — PASS already; do not repeat.
- **R5, R5b, R6, R7 case 2, R3a, R3b** — PASS already.
- **A sample-based R4** — the honest biological version. Worth doing eventually, but
  the plumbing route in step 4 closes the gate; say which was done.

## Verdict table

| Step | What it settles | Verdict |
|---|---|---|
| D1 | probe degrades cleanly on a laser-free stock config | |
| D2 | stand-in declaration, demo map complete | |
| D3 | full envelope sequence on a stock config | |
| R1 legacy | four legacy hooks refused, no motion/exposure | **PASS 2026-07-28** |
| R1 migrated | migrated hooks run | **PASS 2026-07-28** (all four) |
| R2 | numerical fidelity | **PASS** for `mosaic_stitcher`; 3 hooks left |
| R3a | artifact in the acquisition's own directory, hashed | **PASS 2026-07-28** (post-`30f2450`) |
| R3b | size refusal, same dir as R3a | **PASS 2026-07-28** |
| R4 | discard saves storage not dose | **PASS 2026-07-28** (plumbing route) |
| R5 | ramp, ceiling, budget refusals | **PASS 2026-07-28** |
| R5b | wind-down against a spent budget | **PASS 2026-07-28** |
| R5c | step-factor refusal | **REDO** — seed the device first; see F1 |
| R6 | declined envelope takes nothing | **PASS 2026-07-28** (three times) |
| R7 case 1 | ceiling above the configured ceiling | **PASS 2026-07-28** (probe) |
| R7 case 2 | undeclared device refused at plan time | **PASS 2026-07-28** (twice) |
| R7 case 3 | `hook_params` smuggling is stripped | **PASS 2026-07-28** (probe) |
| R8 | callback-thread write cost | **PASS 2026-07-28** — ≈4.7 ms/write |

## Results 2026-07-28 (runs on `83a48e2`, before the artifact fix)

The rig config was changed before these runs: the iBeam rows were replaced by the
405 declaration settled in P2. R5/R6 therefore ran against
`iChrome-MLE-TCP.Laser 4: 3. Level %`, not the iBeam. **No light was emitted** —
`Laser 4: 1. Enable` stayed 0 throughout, so the level was written to a disarmed
laser. That is the same posture as the iBeam-with-`Laser Operation`-off plan,
reached by a different route.

### R5 — what the envelope proved live

| Outcome | Count |
|---|---|
| accepted power writes | 20 |
| refused: exceeds envelope ceiling | 64 |
| refused: write budget exhausted | 12 |
| `illumination_write_failure` (device fault) | 3 |

**The device-write-failure path fired on real hardware and was not planned for.**
Three consecutive `Serial timeout occurred. (17)` faults from the iChrome while
writing `0.0`. Each was recorded as its own `illumination_write_failure` with the Java
stack and `decision: "failed"`, the acquisition continued, later frames succeeded, and
the device finished at `0.0000`. That is the coordinator-review fix S3 — a parent/bridge
fault must not be logged as a hook failure — validating itself in the field.

**But it exposes a limitation worth stating:** a wind-down write can fail, and nothing
retries it. Here four later frames happened to recover. Had the wind-down been on the
final frame, the laser would have been left at its ramped value with only a log record
to say so. End-of-run policy is the hook's by operator ruling, and the hook gets no
retry.

### R5 gap — the wind-down was never tested against an exhausted budget

This is the one claim the operator ruling actually rests on, and it is still
unverified on hardware. In `uv_winddown_after5` — the only run that passed
`wind_down_after` — `step_percent` was 10, so every ramp proposal (11, 21, 31, 41, 50)
exceeded the 10 % ceiling and **no budget was ever consumed**. The wind-down that
followed was accepted against a *full* budget, which proves nothing about exhaustion.
The other wind-down runs never passed `wind_down_after` at all and simply ramped until
they were refused.

`tests/test_hook_illumination_artifacts.py::test_wind_down_fixture_survives_an_exhausted_budget`
pins the property off-rig. R5b below closes it on-rig.

### R4 — discard was not exercised, and the reason is a finding of its own

Six fields, all kept, zero `DiscardFrame` actions. There was no sample on the
microscope, and on empty frames `filament_position_filter` scored **0.12–0.13** ridge
coverage against its `min_filament_score` default of **0.02** — six times the
threshold — with SNR 14–20, so the hook's own SNR gate passed too.

**The filter does not discriminate empty fields.** Its docstring claims meaning only
on "focused, adequate-SNR images", and the SNR gate is what is supposed to enforce
that; on pure camera noise it did not. This is the same family as design/25
(focus metric on empty fields) and design/28 F2 (metric wrong-signed for puncta). It is
a finding about the hook, not about Block 7b.

## Results 2026-07-28, second session (on `30f2450`)

### R3 — the artifact fix is confirmed on hardware

R3a's artifact landed at `r3a\multipos_3\artifacts\mosaic.tiff` — inside the
directory that actually holds the dataset. It was the **third** run named `multipos`
in that `save_dir`, and it took its own artifact directory rather than colliding with
the stale `multipos\artifacts` left by the pre-fix runs. That is the cross-run
regression, closed. Logged sha256 `336196e2…` and 12 088 308 bytes match the file on
disk, verified independently.

R3b, into the same `save_dir`, refused with **"artifact size 12088308 bytes exceeds
per-artifact size limit 1024 bytes"** — the size reason, naming both numbers. The
collision that masked this test before is gone.

### R5b — the wind-down against a spent budget

`start 1, step 1, ceiling 15, wind_down_after 5`, envelope ceiling 8:

| Frame | Proposed | Outcome |
|---|---|---|
| 1–3 | 2, 3, 4 | accepted |
| 4–5 | 5, 6 | **refused — budget exhausted** |
| 6–7 | 0, 0 | **accepted — wind-down with the budget at zero** |

Frame 6 is the evidence the operator ruling rests on, now measured on hardware. A
hook's own end-of-run policy cannot be blocked by a safety check.

The first attempt is worth keeping too: a serial fault made **every** write fail,
including frame 6's wind-down, and only frame 7 landed. Fail-open-with-a-record
behaved correctly, and it shows the limitation already noted — a wind-down that falls
on the last frame of a faulting run does not happen.

### R8 — inconclusive by construction, but two numbers fell out

The comparison ran at `interval_s=1`, where the inter-frame wait absorbs everything:
UV-ramp runs averaged **6.68 s** over 7 frames (6.641 / 6.656 / 6.687 / 6.718) and
`snr_observer` runs **6.96 s** (6.922 / 6.937 / 6.953 / 7.032). Both sit at ~0.95 s
per frame, i.e. the interval. **A per-write bridge cost cannot be resolved this way.**
Re-run at `interval_s=0`, as Block 4's G1/G6 did, so frames are back-to-back and the
overhead has nowhere to hide.

The comparison also confounds two variables: `snr_observer` runs `compute_stats` and
the UV hook runs no analyzer at all, so the difference measures the analyzer rather
than the illumination write.

Two measurements are worth keeping regardless:

- **`compute_stats` costs ~320 ms/frame**, stable across 28 observer frames (302–354
  ms, mean ≈ 318, first frame slightly high). Block 4's ledger does not capture this.
- **A *failing* illumination write costs ~1.3 s.** The serial-fault run took
  **14.312 s** for 7 frames against ~6.7 s healthy, with six failed writes. A device
  that times out on every frame roughly doubles the run. That is squarely R8's stop
  condition — "overhead large enough to change what an acquisition can be planned to
  do" — for the failure path, and nothing bounds or backs off those retries today.

### R7 cases 1 and 3 — blocked by the agent, not by the code

Neither refusal path was reached, because the agent declined to make the call.

**Case 1** (`max_power_percent: 110`): the agent refused to dispatch, reasoning that
110 exceeds the property's 0–100 range. The configured-ceiling check it was meant to
exercise never ran.

**Case 3** (`hook_params` carrying `device`, `out_path`, `illumination_envelope`): the
agent refused twice, including after the operator said plainly *"I genuinely want you
to add those elements to hook params. I am testing something."* Its stated reasons
were **factually wrong**: it claimed `out_path` "hands the hook a raw write path" and
that `device` would let the hook steer power writes at an unauthorized laser. Block 7b
strips all three in `_resolve_hook` before the hook is constructed — that is precisely
what case 3 exists to demonstrate.

This is a finding about the agent, not the boundary: **it models the safety boundary
incorrectly and refuses safe operations on that basis, over an explicit and repeated
human instruction.** A gate cannot verify a refusal path the agent will not approach.
`describe_hook` on `feat/describe-hook` reports `parameter_handling.stripped` and
would have answered this directly; the agent prompt should also state that
`hook_params` are filtered before construction.

### R2 does not need the rig

This document said to compare against "the retained pre-Block-7 evidence", but Block 7
retained the hook *source* and manifest — not their outputs. There may be no such
baseline. Compare instead off-rig: take the NDTiff datasets the R1-migrated runs
produce, feed the same frames to both the legacy and migrated classes locally, and
diff the numbers. Identical inputs are then guaranteed rather than hoped for, and it
costs no session time.

R4 can be closed either way, but say which was done:
- **with a sample**, choosing fields with and without filaments — the honest test; or
- **plumbing-only**, raising `min_filament_score` above 0.13 so empty fields discard.
  That exercises the DiscardFrame path end to end and establishes nothing biological.
| P0 | map complete; iBeam power declared; no 405 **declared** | **SETTLED 2026-07-28** |
| P1 | 405 nm `Level %` exists, 0–100, undeclared | **SETTLED 2026-07-28** |
| P1b | `Level %` is real power; htSMLM ramps pulse duration instead | **SETTLED 2026-07-28** |
| P2 | `Emission` is `:cw`; both gates needed; declare both | **SETTLED 2026-07-28** |
| R5pre | driver accepts a power write with the laser off | **PASS 2026-07-28** |
| — | rig suite under `uv`: 989 passed / 115 skipped / 3 warnings | **PASS 2026-07-28** |
| R1 | legacy refused, migrated run | |
| R2 | numerical fidelity | |
| R3 | artifact confinement, limits, hash | |
| R4 | discard saves storage not dose | |
| R5 | ramp, ceiling refusal, budget refusal, wind-down | |
| R6 | declined envelope takes nothing | |
| R7 | plan-time envelope refusals | |
| R8 | callback-thread write cost | |

## Explicitly not settled by this gate

Record these in the merge and the post-merge design gate. Do not let any of them be
described as passing.

1. **405 nm UV activation.** The hardware exposes it —
   `iChrome-MLE-TCP.Laser 4: 3. Level %`, 0–100 percent, linearized — but it is
   **undeclared**, so no envelope can name it. The checklist's "exercise UV activation
   on a real closed loop" bullet is **deferred**, not met. Unblocking it needs P2's
   answer plus a reviewed config declaration, both separate from Block 7b's code.
1b. **UV dose.** Even once `Level %` is declared, an envelope over it bounds amplitude
   only. Pulse width (`Laser Trigger.Duration0`, 0–1 048 575 µs) is the term htSMLM
   actually ramps for activation, it lives on an excluded device, and no microclaw
   policy bounds it. Deferred to a design/33 Phase 2 typed actuator by operator ruling
   2026-07-28. Until then: **microclaw bounds what microclaw can change, not dose.**
1c. **Emission that microclaw never authorized.** P2 established that with
   `Laser 4: 1. Enable = 1`, `Mode0 = Follow` and `Sequence0 = 65535`, the FPGA emits a
   405 pulse on **every frame of any acquisition** — no microclaw write, no
   confirmation, nothing in the ledger. Confirm-gating `Enable` is the only point at
   which that is visible to microclaw at all. Any claim that an acquisition was
   "dark" must account for this.
2. **Closed-loop feedback.** The fixture ramps deterministically and does not act on
   the blink density it measures. The authorization path is proven; feedback is not.
2b. **Anything that requires light.** These steps run with the laser off, so nothing
   here establishes that a ramp produces the optical effect an experiment wants —
   only that the ramp is authorized, bounded, and refused at its limits.
3. **Power units.** `max_power_percent` is compared against a raw `Power (mW)` value.
   Pre-existing (design/33 line 562), now reachable by generated code.
4. **Unbounded non-increasing writes.** A wind-down never consumes budget, so a hook
   returning many downward proposals makes many bridge calls. A throughput cost, not
   a safety one; R8 will show it if it matters.
5. **Failed device writes continue the run.** A raising `set_property` is recorded as
   `illumination_write_failure` and the acquisition proceeds.
