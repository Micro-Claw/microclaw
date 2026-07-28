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
recovery — saved source still executes in the hardware-control process and source
review plus sha256 pinning remain the containment story until Block 13. It does not
validate any biology: blink density here is a control signal, not a measurement
anyone should trust.

**No interactive confirmation occurs inside an acquisition callback.** pyjavaz
serializes bridge calls behind one lock, so a mid-run prompt is a hazard. The
illumination envelope is confirmed exactly once, before any motion. If a prompt
appears mid-acquisition, that is a stop condition, not a surprise.

**Hooks cannot enable light.** A shutter enable is not expressible in the action
union. The 405 nm shutter must already be enabled by the operator, through the
existing Phase-1 gate, before any UV step below. Confirm the interlock and that the
sample is one you are willing to bleach.

**End-of-run illumination policy belongs to the hook, not the code** (operator
ruling, 2026-07-28). Nothing in microclaw restores, zeroes, or winds down power when
a run ends. Step R5 exists because that ruling is only safe if a hook's own
wind-down cannot be refused by a safety check.

Use one dated evidence directory. Preserve commands, stdout and stderr, environment
identity, inputs, artifacts, sha256 files, and one verdict per step. Never commit
output artifacts.

**Windows command rule:** every command below is PowerShell/cmd-safe. Do not replace
a redirection with a Unix pipeline.

## Common setup

```powershell
$Repo     = "<repo>"
$Evidence = "<dated evidence directory>"
$Scratch  = "<path inside configured workspace>\block7b"
$Config   = "<reviewed M5 safety config>"
$Port     = 4827

New-Item -ItemType Directory -Force $Evidence | Out-Null
New-Item -ItemType Directory -Force $Scratch | Out-Null
Set-Location $Repo
git switch design32/hook-illumination-and-artifacts
git rev-parse HEAD > "$Evidence\commit.txt" 2>&1
git status --short > "$Evidence\status.txt" 2>&1
python -m pytest -q > "$Evidence\pytest.txt" 2>&1
python -m compileall microclaw > "$Evidence\compileall.txt" 2>&1
Get-FileHash tests\fixtures\hooks\m5_legacy\*.py -Algorithm SHA256 | Format-List > "$Evidence\legacy-hashes.txt"
```

**Substitute every `<...>` placeholder with a real path before pasting a prompt.**
A literal `<workspace>` reached the runner in the 2026-07-27 demo gate and the
acquisition failed with `OSError [WinError 123]`.

Expected legacy hashes — these must match byte-for-byte after a Windows checkout,
which is what `tests/fixtures/hooks/m5_legacy/*.py binary` in `.gitattributes` is
for. A mismatch means the retained evidence did not survive the checkout and is a
stop condition:

| File | sha256 |
|---|---|
| `filament_position_filter.py` | `7459dcffd95c5385697a2e4cd0daee1da22b71cb0b9742188fa2cb1252e3802e` |
| `mosaic_cell_counter.py` | `8b1e4f63014e847d435595179c727be4424fe41de1423603b041b3e2d9c7eb63` |
| `mosaic_stitcher.py` | `e4719a87224bd66649d963fba48533fe7c4877d7e70447d680d1c016d0d10185` |
| `mosaic_stitcher_rot.py` | `ae029e3c4787869ed79ec5ec13c18fa983219781f7c15fae0412de86a5d1ca2a` |

---

## P0. Pre-flight — is UV activation authorizable at all on M5?

```powershell
python -m microclaw --port $Port --safety-config $Config authorization-map > "$Evidence\authorization-map.json" 2>&1
```

Confirm the map is `complete`, and confirm the **405 nm power property is declared
in `illumination.power_properties`**. The envelope is validated against that list at
plan time, so an undeclared 405 power property means the envelope is refused before
the run starts.

**If it is not declared, stop and report it.** That is a finding about the reviewed
M5 config, not a defect in this block, and it is the same class of open item as the
`iChrome-MLE-TCP.Label` note in design/33. Do not widen the config to get the gate
to pass; bring the proposed config change back for review first.

Record the exact device/property names, the configured `max_power_percent`, and the
configured `max_power_step_factor`. Every later step depends on them.

**Stop condition:** map not complete; 405 power property undeclared; the configured
ceiling is high enough that the gate's low envelope ceiling cannot be reached.

---

## Migration gate — the four retained hooks

Save each migrated hook from `tests/fixtures/hooks/m5_migrated/` through the normal
show → confirm → `generate_and_save_hook` flow, so it is hash-pinned the way any
saved hook is. Do not delete the legacy registry entries.

### R1. Legacy hooks are still refused, migrated hooks run

Run each **legacy** hook once. Each must be refused at resolve time, before any
hardware moves, with the "writes its own log" message. Then run each **migrated**
hook over the same fields used for its pre-Block-7 evidence.

**Expected observable:** four clean refusals with no motion and no exposure; four
migrated runs that complete and produce a parent-written log with per-frame
measurements under the `microclaw.analysis-observation/v1` envelope at
`status: "unverified"`.

**Stop condition:** a refusal that moves the stage first; a migrated hook that needs
a capability this block did not restore. Per the checklist that second case is a
**fourth gap, not a bug** — record it and stop, do not patch around it.

### R2. Numerical fidelity against pre-Block-7 behaviour

For each migrated hook, compare against the retained pre-Block-7 evidence:
`filament_position_filter` scores and keep/discard decisions per field;
`mosaic_cell_counter` running counts and mean areas; both stitchers' assembled
canvas.

`mosaic_cell_counter._label` is the original whole-canvas pure-Python flood fill and
was deliberately not optimized. On a realistic M5 mosaic it may be slow enough to
matter. Time it and record the number — if it is impractical, that is a finding for
the design gate, not a reason to change the algorithm inside this gate.

**Expected observable:** identical scores, counts, and pixels. Placement, dtype, and
orientation unchanged.

**Stop condition:** any numerical difference. Migration was supposed to be
mechanical.

### R3. Artifacts are confined, bounded, and hashed

Run both stitchers with `artifact_limits` set. Then, separately, re-run one with
`max_artifact_bytes` set below the real mosaic size.

**Expected observable:** the TIFF lands inside the run's artifact directory and
nowhere else; the parent log records its path, size, and sha256; the recorded hash
equals `Get-FileHash` of the file on disk. In the undersized run the artifact is
refused with a reason naming both the actual size and the limit, no file is created,
and the acquisition continues.

Compare against what these hooks did before: `tifffile.imwrite(self.out_path, ...)`
on an unconfined constructor string. Confirm no file appears at any path the hook
names itself.

**Stop condition:** a file outside the artifact directory; a hash mismatch; a
partial file left behind after a refusal.

### R4. Discard saves storage, not dose

Run `filament_position_filter` over a mixed field set where some fields fail its
threshold.

**Expected observable:** discarded fields produce an observation record plus an
`outcome: "discarded"` record and **no saved image**; the dataset image count equals
the number of kept fields. Every position in the list was still visited and still
exposed — confirm this against the planned position count and the ledger's
illuminated time, which must reflect **all** fields, not only the kept ones.

**Stop condition:** the ledger under-counts exposure for discarded frames, or any
output describes discard as skipping acquisition or reducing dose.

---

## UV gate — the closed loop

This is the step the block exists for, and the only one that puts a real laser under
generated-code control. Run it last, with the operator present.

Enable the 405 nm shutter first, through the existing operator confirmation. Choose
an envelope ceiling **low enough that the fixture ramp reaches it during the run**.
A ramp that never reaches its ceiling has not tested the gate.

### R5. Ramp, refuse at the ceiling, then wind down after the budget is spent

Run `run_adaptive_timelapse` with the `uv_activation` fixture and:

```json
{
  "hook_strategy": "uv_activation",
  "illumination_envelope": {
    "device": "<405 power device>",
    "property": "<declared power property>",
    "max_power_percent": <low measured ceiling>,
    "max_writes": <fewer than the frame count>
  }
}
```

`max_writes` deliberately smaller than the frame count, so budget exhaustion happens
during the run rather than at its edge.

**Expected observable, in order:**

1. Exactly **one** confirmation, before any motion, naming the device, the ceiling,
   the write budget, and stating that generated code will drive it unattended.
2. Accepted increases, each recorded with the value written, up to the ceiling.
3. At the ceiling: a refusal reading `proposal exceeds authorized envelope ceiling`.
   Verify with an independent read that the device did not go above it.
4. After `max_writes` increases: `authorized illumination write budget exhausted`.
5. **A wind-down still succeeds.** With the budget at zero, a proposal at or below
   the last written value is accepted, reaches the device, and does not decrement
   anything. This is the operator ruling made testable: a hook that implements its
   own end-of-run policy must not be blocked by a safety check from lowering power.
   Prove a write to `0` is accepted with the budget exhausted.
6. No prompt of any kind after the run starts.

Then read the device's power **after the run ends** and record it. Nothing restores
it; that is by design and the number belongs in the evidence.

**Stop condition:** any write above the envelope ceiling or the configured
`max_power_percent`; a step larger than `max_power_step_factor` allows; a refused
wind-down; a mid-run prompt; a ramp that never reached its ceiling.

### R6. A declined envelope takes no exposure

Re-run R5 and decline the confirmation.

**Expected observable:** an attributable refusal naming the device and property, no
reservation, no motion, no exposure, no dataset.

### R7. Envelope refusals before the run

Three separate attempts, each expected to fail at plan time with a distinct,
attributable message, none reaching hardware:

1. an envelope ceiling above the configured `illumination.max_power_percent`;
2. a device/property not declared in `illumination.power_properties`;
3. `hook_params` carrying `device`, `illumination_envelope`, or `out_path` — these
   must be stripped, and the hook must not receive them.

### R8. Callback-thread cost of a bridge write

The illumination write happens on the acquisition callback thread. design/11b Spike C
established the bridge is not thread-affine and design/16 established that calls
serialize behind one lock, so this is expected to work and to block briefly — but the
cost is unmeasured.

Run the same timelapse twice, once with the envelope and a ramping hook and once
with an observation-only hook and no envelope. Report per-frame overhead against
Block 4's measured ~657 ms/frame baseline.

**Stop condition:** a hang, a dropped frame, an out-of-order frame, or overhead large
enough to change what an acquisition can be planned to do. Report the number either
way — a measured cost is the deliverable here, not a pass/fail.

---

## Verdict table

| Step | What it settles | Verdict |
|---|---|---|
| P0 | 405 power property declared; map complete | |
| R1 | legacy refused, migrated run | |
| R2 | numerical fidelity | |
| R3 | artifact confinement, limits, hash | |
| R4 | discard saves storage not dose | |
| R5 | ramp, ceiling refusal, budget refusal, wind-down | |
| R6 | declined envelope takes nothing | |
| R7 | plan-time envelope refusals | |
| R8 | callback-thread write cost | |

Report every verdict, every measured number, and anything a migrated hook needed
that this block did not restore.
