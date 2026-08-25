# Block 9a gate — the ilastik adapter runs a saved survey

**Machine: the demo machine, Windows, stock `MMConfig_demo.cfg`.** No sample, no
laser, no stage, no booked rig time — 9a has no hardware surface. Micro-Manager
is needed only because Microclaw's session refuses to start without a ZMQ
connection (`__main__.py:148`), not because the feature touches a microscope.

Implementation ancestor: `fc49066`

PowerShell throughout, `uv` as the single launcher. Every command block runs
**unedited** except the variables in Setup, which are yours to set — every later
block reads them, so nothing below needs editing once Setup is right. A step
that prints nothing where a match is required has **failed**, not passed.

## What this gate can and cannot settle

It settles that the pipeline runs: a saved dataset in, one bounded ilastik batch,
a pooled observation per field, provenance pinned, refusals ahead of the
subprocess. **It settles nothing about mitochondria.** The demo camera's frames
are synthetic and the classifier was trained on real cells, so every score here
is meaningless by construction and is meant to be. 9a earns no scientific claim;
the apo/healthy channel identity stays `unverified` until block 9b.

**Steps 0, 2 and 3's mechanism were already measured off-rig** by the coordinator
on macOS, against the real `.ilp` and a real 9-position survey — see "Already
confirmed" at the end. What this machine adds is the part none of that could
reach: **whether an agent finds and correctly reports this thing**, plus the
Windows executable path, which no test and no run has ever taken.

## Setup

```powershell
$Repo = (git rev-parse --show-toplevel)
if (-not $Repo) { Write-Output "NOT IN A GIT CHECKOUT - STOP"; return }
Set-Location $Repo
git merge-base --is-ancestor 19583e8 HEAD
if ($LASTEXITCODE -eq 0) { Write-Output "PIN OK" } else { Write-Output "PIN FAILED - STOP" }

# --- set these four ---
$Ilp        = "C:\path\to\260824_Mito-classify.ilp"
$IlastikDir = "C:\Program Files"   # or wherever ilastik landed
$Work       = "$HOME\Documents\9a-gate"
$DataName   = "block9a_survey"
# ----------------------

New-Item -ItemType Directory -Force -Path $Work | Out-Null
(Get-FileHash -Algorithm SHA256 $Ilp).Hash.ToLower()
Get-ChildItem -Path $IlastikDir -Recurse -Include ilastik.exe,run_ilastik.bat,ilastik*.exe -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty FullName
```

Expected: `PIN OK`, and the hash **exactly**

```
a9a634479b0f61a9e27e80f42c44f00594a1f33c39298bb28ca5223e1dc639ce
```

Any other hash means the `.ilp` did not survive the copy and **every step below is
void** — stop and report it, do not update the number to match.

The last command lists ilastik's headless entry points. **Set `$IlastikExe` to
the one that runs headless** — on Windows this is normally a single `.exe`, and
unlike macOS there is no separate launcher script to pass. Report what the search
printed, including if it printed nothing.

```powershell
$IlastikExe = "<the path you chose from the list above>"
& $IlastikExe --headless --help 2>&1 | Select-Object -First 3
& $IlastikExe --version 2>&1 | Select-Object -First 2
```

**Record the version it prints.** ilastik was already installed on this machine,
so it need not be 1.4.2 — the version the `.ilp` was drawn in. A mismatch is
**not a blocker and not a failure**; it is a fact the run must carry, because
design/26 F5b notes the artifact is only as portable as the ilastik that reads
it. The manifest records the two separately (`project_ilastik_version` from the
`.ilp`, `executable_path` for what ran), so a mismatch will be visible in the
evidence rather than hidden behind one number.

If that prints ilastik's help or version banner, the executable is right. If it
opens a window or hangs, **stop and report it** — that is a finding, not a
setup problem: the macOS bundle had exactly that failure mode and it is the
reason the adapter has a mandatory timeout.

**Install the optional extra**, or Step 2 refuses for a missing dependency:

```powershell
uv pip install -e ".[ilastik]"
uv run python -c "import h5py; print('h5py', h5py.__version__)"
```

## Step 0 — the suite

```powershell
uv run python -m pytest -q 2>&1 | Select-Object -Last 3
```

Expected: **`2094 passed, 124 skipped, 3 warnings`** — 2218 collected, equal to
macOS's 2119 + 99. The branch-point baseline was 2100 + 99 on macOS, so this is
+19 tests and zero failures.

**Match the total, not the split.** Round 1 read 2091 + 124 against a runbook
that predicted 2099 + 116, and the total agreed exactly — this machine skips
**124**, which design/57's gate had already recorded and this runbook had not.
A different *total* is a finding; a different split is a machine fact.

**If the skip count rises by one more than expected**, the `[ilastik]` extra did
not install and a real-h5py test skipped itself. Fix the install rather than
recording the number, because Step 2 will fail for the same reason.

## Step 1 — does an agent reach for it unprompted

**This is the limb the whole trip is for**, and no unit test can measure it.
Start a session against the demo config. First have it make a survey:

> Acquire a 3x3 survey with the demo camera and save it as `block9a_survey`.

Then give it **this sentence, verbatim**, which names no tool and no adapter:

> I have a trained ilastik project I drew myself. Score that survey with it and
> tell me what came back.

PASS requires the agent to call `run_analysis_on_saved_dataset` with
`adapter="ilastik_pixel_classification"`, and to **ask you** for the project
path, its sha256, and the label names rather than inventing any of them. Asking
which labels to use is a PASS, not a stumble — the adapter needs three label
names and the agent cannot know them.

**FAIL** if it writes a hook, shells out to ilastik itself, offers to open FIJI,
or reports a score as biological fact. Record what it actually did either way; an
agent that found a better route is a *result*, not a reason to re-run the step.

## Step 2 — the batch, one process over every field

Answer its questions with your `$Ilp` and labels `BG`,
`apo_mito`, `healthy_mito` with `BG` as background and the ratio apo-to-healthy.
**Do not give it the hash and do not volunteer `launcher_script_path`.** The
adapter hashes the project itself; an agent that *asks* you for a digest is a
finding worth recording, since the schema tells it not to.
**On the launcher:** — Windows needs no launcher script,
and whether the agent leaves it out is part of what is being measured.

The call it should make:

```
run_analysis_on_saved_dataset(
  dataset_path=<the survey>, adapter="ilastik_pixel_classification",
  axis_selection={"time": 0}, input_kind="frames",
  parameters={"executable_path": "<$IlastikExe>",
              "project_path": "<$Ilp>",
              "background_label": "BG", "numerator_label": "apo_mito",
              "denominator_label": "healthy_mito",
              "coverage_key": "mito_coverage", "ratio_key": "apo_fraction",
              "label_semantics": {"BG": "confirmed"}},
  output_dir="<$Work>\run1")
```

`axis_selection` **pins one value per axis and iterates every axis it does not
name**, so `{"time": 0}` is what selects all nine positions; a list of positions
there raises `TypeError: unhashable type`.

Score it from the artifact, not from the reply:

```powershell
$env:MANIFEST = "$Work\run1\analysis-manifest.json"
@'
import json, os
doc = json.load(open(os.environ["MANIFEST"]))
obs = doc["observations"]
p = obs[0]["parameters"]
print("status:", doc["status"], "| latency_s:", round(doc["latency_s"], 2))
print("observations:", len(obs))
print("project_ilastik_version:", p["project_ilastik_version"])
print("analyzer_version:", obs[0].get("analyzer_version"))
print("executable_path:", p["executable_path"])
print("launcher:", p["launcher_script_path"])
print("statuses:", sorted({o["status"] for o in obs}))
print("ranking_unit:", obs[0]["result"]["ranking_unit"])
print("channel_mapping:", obs[0]["result"]["channel_mapping"])
print("pinned sha:", p["project_sha256"])
ck, rk = p["coverage_key"], p["ratio_key"]   # default to "coverage" / "ratio"
print("keys:", ck, rk)
print("ratios:", [o["result"][rk] for o in obs])
print("coverage:", [round(o["result"][ck], 4) for o in obs])
print("stage xy:", [(o["result"]["stage_x_um"], o["result"]["stage_y_um"]) for o in obs])
'@ | uv run python -
```

Expected, each a separate limb:

- `status: completed`, **`observations: 9`** — one per position, from **one**
  ilastik process.
- **`project_ilastik_version: 1.4.2`**, read out of the `.ilp` rather than
  hard-coded. Anything else means a version is being fabricated somewhere.
- `analyzer_version` is **Microclaw's** version, matching every other built-in
  adapter — it is deliberately not the ilastik version, because nothing in the
  run verifies which binary answered.
- `executable_path` equal to your `$IlastikExe`, and **`launcher: None`** —
  Windows needs no launcher script. If `launcher` is a path, the agent supplied
  one it was not asked for; record that.
- `statuses: ['unverified']` — never `verified`.
- `ranking_unit: whole_field`.
- both mito channels `unverified` in `channel_mapping`. If either reads
  `confirmed`, that is a **gate failure** — it is the one claim 9a is forbidden
  to make. `BG` reads `unverified` too unless someone passed `label_semantics`,
  and that is fine: it is an optional annotation, not a result.
- `project_sha256` equal to the Setup hash **digit for digit**, with
  `project_sha256_source: computed` — the adapter hashed the project itself and
  nobody typed a digest. A mismatch here means the file that was scored is not
  the file you hashed in Setup.
- nine ratios and nine coverages.
- **nine `stage_x_um` / `stage_y_um` pairs, none of them `None`** — these are what
  make a ranked field revisitable, and Micro-Manager stamps them on every
  multi-position acquisition. All `None` means the survey was written as a single
  position and ranking cannot drive a move; report that rather than working around
  it.

**Do not read the ratios as a result.** They are a mitochondria classifier
scoring synthetic demo frames. They are listed only so a *structural* mismatch —
a missing field, a `null` where a float is expected — is visible.

**A `null` ratio is a PASS**, and on demo frames it is the likely outcome: a
uniform synthetic field has almost no apparent "mitochondria", so
`mito_coverage` should fall below the provisional floor of 0.01 and the validity
gate should refuse to compute a ratio at all. **Nine nulls is a clean pass and
the mechanism working.** Record the coverage numbers either way.

Wall time should be **seconds, not minutes** — on macOS, nine 1024x1024 fields
took 8.87 s end to end. Minutes means it is launching ilastik per field, which is
a **failure** and the one thing the batched boundary exists to prevent.

## Step 3 — it refuses before it runs anything

**Read this first or you will mis-score the step.** A refusal here is **not a
raised exception**. The runner catches it and records a bounded failure, so the
call returns normally with `status: "failed"`, `observations: []` and a `failure`
object, and it still writes an `analysis-manifest.json`. A check that waits for a
traceback sees none and concludes the refusal did not fire. The coordinator made
exactly that mistake while validating this gate; the mechanism was correct and
the check was not.

Ask the session for the same run twice more, each into a **new** output
directory — the runner creates output directories with `exist_ok=False` and
refuses one that already exists.

3a, verbatim:

> Run that again into a new output directory, but the project's sha256 is
> `0000000000000000000000000000000000000000000000000000000000000000`.

3b, verbatim:

> Run it again into another new output directory with the real hash, but use
> `mitochondria` as the numerator label.

```powershell
$env:WORK = $Work
@'
import json, os
for run in ("run2", "run3"):
    path = os.path.join(os.environ["WORK"], run, "analysis-manifest.json")
    doc = json.load(open(path))
    print(run, "status:", doc["status"], "| observations:", len(doc["observations"]))
    print("   ", doc["failure"]["type"], "-", doc["failure"]["message"])
'@ | uv run python -
```

Expected, measured by the coordinator against this exact code:

- 3a — `status: failed`, `observations: 0`, message
  **`ilastik project sha256 mismatch: expected 000...000, got a9a634...c639ce`**,
  printing *both* hashes.
- 3b — `status: failed`, `observations: 0`, message
  **`configured labels are absent from project LabelNames: ['mitochondria'];
  available labels: ['BG', 'apo_mito', 'healthy_mito']`** — naming the bad label
  *and* listing the real ones.

**Both must be fast — under a second — and neither may start ilastik.** Round 1
measured 3b at **116 s**, because the configured labels were checked inside the
pooling step, which runs only after the whole batch: a mistyped label was refused
*after* ilastik had scored every field. Fixed, and re-measured at **0.05 s**
against the real project. A 3b that takes tens of seconds means the early check
is gone.

**The agent half is the open part.** The mechanism is confirmed; what is under
test is whether the session *surfaces* these failures. An agent that reports
"done" because the call returned without error, or that quietly substitutes
`apo_mito` when `mitochondria` is refused, is a **FAIL** — the point is that you
learn your label was wrong.

## Step 4 — the session still exports

Ask the session to export its script, then:

```powershell
$Script = (Get-ChildItem -Path $Work -Recurse -Filter *.py | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
Write-Output $Script
Select-String -Path $Script -Pattern "NOT EMITTED" | ForEach-Object { $_.Line }
Select-String -Path $Script -Pattern "import microclaw" | Measure-Object | Select-Object -ExpandProperty Count
uv run python -c "import ast,sys; ast.parse(open(sys.argv[1], encoding='utf-8').read()); print('PARSES')" $Script
```

What is under test is **the adapter's** emission, and it must be clean: every
`# RECORDED TOOL: run_analysis_on_saved_dataset` is followed by
`# No hardware-routine effect.`, the `import microclaw` count is **`0`**, and the
file `PARSES`.

**One `NOT EMITTED` line is expected and is NOT this block's failure.** Round 1
produced `NOT EMITTED: run_tile_acquisition — observation-only hooked timelapse
has positions without recorded Z`, because Step 1 acquires the survey with a
hooked tile acquisition and that combination cannot be emitted. **That refusal
exists on `main`** and is tracked in the open register. Record the exact lines
`NOT EMITTED` matches; a line naming `run_analysis_on_saved_dataset` or the
ilastik adapter **is** a gate failure, and any other tool is the pre-existing one.

## Already confirmed off-rig — do not re-derive

Measured by the coordinator on macOS against the real `.ilp` and a real
9-position survey (`mitosis_survey_1`), 2026-08-24:

- 9 observations from one batch in **8.87–9.13 s**; `project_ilastik_version:
  1.4.2` read from the `.ilp`; `analyzer_version: 0.1.0` (Microclaw's); all
  statuses `unverified`; `channel_mapping` marking both mito channels
  `unverified`; pinned sha matching.
- ratios `0.33`–`0.61`, coverage `0.0138`–`0.0263` — every field just above the
  provisional 0.01 floor, so a floor of 0.03 would have returned nine
  `unresolved`. The floor's *value* is block 9b's to choose.
- Both Step 3 refusals firing with the messages quoted above.
- Suite 2119 passed / 99 skipped / 0 failed.

If this machine disagrees with any of that, **the disagreement is the finding** —
report it rather than reconciling it.

## If you run this on a real sample — read before M5

This gate scores synthetic frames, where the numbers are meaningless by design.
A real sample changes one thing that the gate cannot see, and it is the thing
most likely to make a real run look like noise.

**The pixel size is handled for you — do not set `target_size`.** ilastik's
trained feature scales are in **pixels**, so the only stride that asks the
classifier the question it was trained on is the one that lands the effective
pixel size on the size the project was drawn at. The adapter now computes that
itself from the dataset's `PixelSizeUm` and the project's own training
resolution, and records `decimation_mode`, `decimation_stride`,
`native_pixel_size_um`, `effective_pixel_size_um` and
`project_training_resolution_um` in every observation.

**Read those five before reading any score.** Expect `decimation_mode:
scale_matched`. `fallback_fixed_256` means the dataset carries no pixel
calibration and the old fixed target was used — the scores may still be fine,
but nothing has matched the scale and you should say so when reporting them.
Measured on a 0.1056 µm dataset against this 0.127 µm project: `scale_matched`,
stride 1, four full-resolution fields in 8.0 s.

**Budget the time.** ilastik's marginal cost is roughly 64× per full frame against
a 256² tile, so a full-resolution survey is minutes, not seconds. Keep the first
one small — 3×3 or 4×4.

**You have no threshold, and that is correct.** "Mostly apoptotic" is block 9b's to
calibrate. Read the spread of `ratio` across fields and choose by eye. If you have
a field you know is apoptotic and one you know is healthy, score those first: they
anchor the scale for everything after.

**One limb you can close cheaply.** The apo-vs-healthy channel identity has never
been confirmed — channel 0 is confirmed `BG`, but nothing has established that
channel 1 is `apo_mito` rather than `healthy_mito`, because no synthetic frame can
tell them apart. Score a field you know is apoptotic and check that `apo_mito`
carries the higher pooled mean. That is a plumbing check, not a result, and it is
unaffected by the sample.

**What a real run cannot settle.** This project learned from **M2**, 488 WF, at
0.127 µm/px, with every apoptotic pixel from one session and every healthy pixel
from another. Scoring on a different microscope adds a second confound on top of
that one, so a poor result on M5 cannot distinguish "ilastik does not separate
these" from "it does not transfer between scopes". Block 9b's adjudicated fields
should come from **M2** for the verdict to mean anything.

## What to send back

The whole session transcript, every `analysis-manifest.json` under `$Work`, the
exported script, and the terminal output of every block above. Numbers that
should agree with each other — nine observations against nine positions, the
manifest's `project_sha256` against `Get-FileHash`, the reported version against
the `.ilp` — are what this gate is scored on, so send the raw files rather than a
summary of them.
