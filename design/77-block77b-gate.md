# Block 77b gate — the order the engine really executes, and the clock each field gets

One machine, the **Windows demo machine**, about **6 minutes**, roughly
**24 exposures** on the demo camera plus the exported script's repeat of six of
them. Nothing but the XY stage moves, 20 µm, and the gate puts it back.

This is a **program**, not steps you judge. Each limb scores itself
independently, one FAIL cannot hide the rest behind a cascade, the gate owns its
own log, and it exits nonzero on any FAIL **or** any NOT EXERCISED.
`NOT EXERCISED` is never a pass — if a limb reports it, say so rather than
ticking it.

It has already been run against a bridge-shaped fake
(`design/77-block77b-gate-selftest.py`, six cases, four of them deliberate
failures) on **both** trees: all six hold on this branch, and on a pre-77b tree
limb E fails and the other seven stand down. So it discriminates before it
reaches you. That selftest found two defects in the gate and none in the
product.

## What this gate is and is not for

Nearly all of 77b is settled off-rig, and every one of those tests was verified
by mutation during review — the `ptcz`/`tpcz` mapping, the per-position split,
both emitter branches, the typed stage failure surviving to `execute_tool`, and
the single `timing` shape. **Do not add a limb that repeats one.** What is here
is only what a fake cannot answer:

- **Whether real AcqEngJ executes a position-axis event list in the order
  `multi_d_acquisition_events` emitted it.** Every order test in the suite reads
  a fake's frame list. Limbs A and B are the first observation of the real one,
  and they score it from the **hook log's own arrival order**, not from the
  tool's return value — a returned `acquisition_order` field only says what was
  requested.
- **Whether a per-position acquisition really gives field B its own clock.**
  The settled decision rests entirely on this. Limb C measures it against real
  stage settling.
- **What exposure-timestamp key this camera writes, if any.** D3 says to name
  the key the implementation reads and verify it on a rig first; the shipped
  code reports `null` and declares the field owing. **Limb D is the one limb
  that can change the product**, and it is a report, not a criterion: it fails
  only if the dataset cannot be read at all.
- **Whether the exported script runs.** An exported script that compiles is not
  an exported script that works (52b). Limb F execs it in a child process
  against this same bridge and compares its hook log with the live one.
- **Whether `build_stage_coordinate_mosaic` still accepts the zero-interval
  hooked dataset**, which is the stated reason that case keeps one dataset.

It requires **no configuration the product does not require** (60b): do not pass
`--safety-config` and do not edit one. Datasets go under this machine's
configured `workspace_dir` if it has one and under the evidence folder if not.

## 0. Prerequisite

**Micro-Manager open**, with this machine's usual demo config loaded and the
pycro-manager bridge running — "Run server on port 4827" checked in
Tools → Options. Same link every probe on this machine uses.

The demo config must have a **camera and an XY stage**. If either is missing,
limb 0 reports NOT EXERCISED and names which; that is a real answer, not a pass.

## 1. Pin the tree

```powershell
cd D:\Code\microclaw
git fetch origin
git checkout design77/acquisition-order
git pull
git merge-base --is-ancestor c7ebdf5 HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - the implementation is in this tree" }
else { "STOP - wrong tree, do not run the gate" }
```

**Warm uv once, unredirected.** A freshly checked-out branch leaves uv a rebuild
and it writes `Building microclaw @ file:///...` to stderr; under
`$ErrorActionPreference = 'Stop'` that terminates any *redirected* uv command.
Red build text here is expected and harmless — nothing is redirected.

```powershell
uv run python -c "print('uv warm')"
```

## 2. Run the gate

```powershell
New-Item -ItemType Directory -Force block77b-evidence | Out-Null
uv run python design\77-block77b-demo-gate.py --out block77b-evidence --log block77b-evidence\score.json
"exit: $LASTEXITCODE"
```

It runs three hooked acquisitions of two fields × three frames at 10 ms — 18
exposures — then the exported script repeats one of them (6 more), on a camera
whose trigger drives nothing. The XY stage moves 20 µm between the two fields
and the gate returns it to where it found it.

Limb C deliberately takes about **12 seconds**: it asks for 2 s between frames
at each of two fields, which is the spacing it then checks.

### What each limb settles

- **E — the running build carries 77b.** The control. It reads
  `acquisition_order` off both tools' signatures and checks the default is
  `position_then_time`. On a pre-77b tree this FAILs and everything else stands
  down, which is what makes step 3 meaningful.
- **0 — bridge, camera and two reachable fields.** Establishes the rig and picks
  two fields 20 µm apart starting from wherever the stage already is, then
  checks **both** against this machine's own bounds before anything moves.
- **A — position-outer is the real executed order.** Hooked, `interval_s=0`,
  default order. The hook log must read `gateA gateA gateA gateB gateB gateB`.
  This is the incident's shape, and the answer it used to give was the
  interleaved one.
- **B — explicit interleaving really interleaves.** Same run with
  `acquisition_order='time_then_position'`; the log must read
  `gateA gateB gateA gateB gateA gateB`. Together A and B prove the argument
  selects the order rather than being recorded and ignored.
- **C — field B keeps its own clock.** Hooked, `interval_s=2`, default order.
  Each field must get its **own** dataset and its **own** log, no log may carry
  another field's frames, and every within-field gap must be at least 1.5 s.
  A field whose three frames arrive back to back is the catch-up bug the settled
  decision exists to avoid, and the limb names the field that burst.
  **These are callback-arrival gaps, not exposure timestamps** — they bound the
  spacing from above, which is all that is needed to rule out a burst.
- **D — what exposure-timestamp key does this camera write.** A **report**.
  It opens limb A's dataset, enumerates every per-image metadata key, and writes
  the time-like ones with their values to `D-metadata-keys.json`. It fails only
  if the dataset cannot be read. **Send this file back whatever it says**: if it
  names a key, D3 gets a real one to read; if it names none, D3's `null` is
  confirmed correct for this camera and the limitation stays labelled.
- **F — the exported script runs and agrees.** Exports limb A's call, checks it
  imports nothing from `microclaw`, runs it as a child process against this same
  bridge, and compares the standalone hook log's order with the live one.
  Parsing is necessary but insufficient.
- **G — the zero-interval dataset still mosaics.** `build_stage_coordinate_mosaic`
  over limb A's single position-axis dataset, pinning every non-position axis
  from what limb D enumerated. Round 1 omitted that pin, and the tool's correct
  refusal of an ambiguous selection was scored as a product failure; the
  criterion was then settled off-rig from the returned dataset.

## 3. The control arm, on this machine

Two minutes, and it is what proves the gate could have failed here.

```powershell
Copy-Item design\77-block77b-demo-gate.py block77b-evidence\gate-copy.py -Force
git checkout main
uv run python -c "print('uv warm')"
uv run python block77b-evidence\gate-copy.py --out block77b-evidence\control --log block77b-evidence\control-score.json
"exit: $LASTEXITCODE"
git checkout design77/acquisition-order
```

The copy is because the gate does not exist on `main`; `uv run` still resolves
`microclaw` from the checked-out tree, which is the build under test.

**Expected on `main`: limb E FAIL and every other limb NOT EXERCISED**, with the
reason naming the missing `acquisition_order` arguments. Any other shape means
the gate is not measuring what it claims — report it rather than working around
it.

## 4. What to send back

- `block77b-evidence\gate.txt` and `score.json`
- `block77b-evidence\control-score.json`
- `block77b-evidence\D-metadata-keys.json` — **the point of the trip, whatever
  it says**
- `block77b-evidence\A-hook.json`, `B-hook.json`, and every `C-hook*.json`
- `block77b-evidence\F-export-run.txt` and `exported_routine.py`
- the `exit:` lines from both runs

Do not summarise the limbs. Send the files; the scoring is done from them.
