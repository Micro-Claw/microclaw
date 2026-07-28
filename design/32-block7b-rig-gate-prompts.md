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

**There is no 405 nm power property.** M5's 405 source is the `iChrome-MLE-TCP`
engine, which appears in the map only as `iChrome-MLE-TCP.Label`, a
`reviewed_categorical_property` on the `generic-property` path — the same open item
design/33 already records. An envelope naming it is refused at plan time with
*"illumination envelope device/property is not declared in
illumination.power_properties"*, which is the code behaving correctly.

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

Repeat for all five:

| Fixture file | Save as | Used by |
|---|---|---|
| `filament_position_filter.py` | `filament_position_filter_v2` | R1, R2, R4 |
| `mosaic_cell_counter.py` | `mosaic_cell_counter_v2` | R1, R2 |
| `mosaic_stitcher.py` | `mosaic_stitcher_v2` | R1, R2, R3 |
| `mosaic_stitcher_rot.py` | `mosaic_stitcher_rot_v2` | R1, R2, R3 |
| `uv_activation.py` | `uv_activation` | R5, R6, R7, R8 |

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

### R5pre. Does the driver accept a power write while the laser is off?

The whole shutter-closed plan rests on this, so establish it before R5 and not
during it. With `Laser Operation` = `Off`, read `Power (mW)`, ask microclaw to set it
to a different low value through the ordinary guarded property path, and read it
back.

**Expected observable:** the value changes and reads back. Nothing emits.

**If the driver refuses or silently ignores the write while off**, that is a finding,
not a failure of this block — record it, and only then fall back to enabling the
laser at the lowest power the ramp can start from, with the beam blocked or into a
beam dump. Do not proceed on an unverified assumption that the write landed.

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

**R5b — the wind-down.** The `uv_activation` fixture only ramps up, so save one more
hook for this, via the same `read_hook_from_file` flow, whose `analyze_frame` returns
`SetIlluminationPower(0.0)` on its final frame. Run it with the budget already spent.

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

## Verdict table

| Step | What it settles | Verdict |
|---|---|---|
| P0 | map complete; iBeam power declared; **no 405** | **SETTLED 2026-07-28** |
| P1 | property probe: is a 405 power property exposed at all? | |
| R5pre | driver accepts a power write with the laser off | |
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

1. **405 nm UV activation.** Not authorizable on M5 — no declared power property on
   the iChrome engine. The checklist's "exercise UV activation on a real closed loop"
   bullet is **deferred**, not met.
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
