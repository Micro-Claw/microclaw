# Block 9a gate — the ilastik adapter runs a saved survey

**Machine: this Mac.** Not a rig. ilastik 1.4.2 and the trained `.ilp` both live
here, and 9a has no hardware surface at all — no stage, no camera, no laser, no
booked time. **Nothing in this gate touches a microscope.** The M2 survey belongs
to block 9b, with the operator's adjudicated fields.

Implementation ancestor: `c9c6b85`

zsh throughout, `uv` as the single launcher. Every command block runs
**unedited** — nothing in it is a placeholder you are meant to substitute, and a
step that prints nothing where a match is required has **failed**, not passed.

## What this gate can and cannot settle

It settles that the pipeline runs: a saved dataset in, one bounded ilastik batch,
a pooled observation per field, provenance pinned, refusals ahead of the
subprocess. **It settles nothing about mitochondria.** The scores it produces are
on a mitosis survey scored by a mitochondria classifier, which is meaningless by
construction and is meant to be — 9a earns no scientific claim, and a step that
reads a number here as biology has misread the block. The apo/healthy channel
identity is `unverified` throughout and stays that way until 9b.

## Setup — run this first

```zsh
Repo=$(git rev-parse --show-toplevel)
cd "$Repo"
git merge-base --is-ancestor c9c6b85 HEAD && echo "PIN OK" || echo "PIN FAILED - STOP"
Ilp="/Users/zachcm/Documents/Documents - Beyonce/Projects/Micro-Claw/sarah-ilastik-training-file/260824_Mito-classify.ilp"
Data="/Users/zachcm/Documents/Documents - Beyonce/Projects/Micro-Claw/mitosis_survey/mitosis_survey_1"
Work="$HOME/Documents/Documents - Beyonce/Projects/Micro-Claw/9a-gate"
mkdir -p "$Work"
shasum -a 256 "$Ilp"
ls "$Data/NDTiff.index" && echo "DATASET OK"
```

Expected: `PIN OK`, the sha256
`a9a634479b0f61a9e27e80f42c44f00594a1f33c39298bb28ca5223e1dc639ce`, and
`DATASET OK`. Any other hash means the `.ilp` changed and **every step below is
void** — stop and report it, do not update the number to match.

**Run every command from `$Repo` with `uv run`, never bare `python`.** This
checkout is not the one the editable install points at, so a bare `python` here
silently imports the *other* tree's microclaw and reports that
`ilastik_pixel_classification` does not exist. Measured while writing this gate.

**Each run needs an output directory that does not exist yet** — the runner
creates it with `exist_ok=False` and refuses an existing one. Use `$Work/run1`,
`$Work/run2`, and so on; do not pre-create them.

## Step 0 — the suite

```zsh
uv run python -m pytest -q 2>&1 | tail -3
```

Expected exactly: **`2114 passed, 99 skipped, 3 warnings`**. The branch-point
baseline was 2100 passed / 99 skipped, so this is +14 and zero failures. A
different skip count is a finding, not a rounding difference.

## Step 1 — does an agent reach for it unprompted

This is the block's usability limb and the one thing no unit test can measure.
Start a session and give it **this sentence, verbatim**, which names no tool:

> I have a saved survey at `mitosis_survey_1` and a trained ilastik project I
> drew myself. Score the survey with it and tell me what came back.

PASS requires the agent to call `run_analysis_on_saved_dataset` with
`adapter="ilastik_pixel_classification"`, and to **ask for the project path and
its sha256** rather than inventing either. It is also a PASS if it first asks
which labels to use — the adapter needs three label names and the agent cannot
know them.

**FAIL** if it writes a hook, shells out to ilastik itself, offers to open FIJI,
or reports a score as biological fact. Record what it actually did either way;
"the agent found a better route" is a result, not a reason to re-run the step.

## Step 2 — the stored-data run, nine fields in one batch

Ask the session for this, verbatim:

> Score every position in that survey. The project is at the path I gave you,
> its labels are BG, apo_mito and healthy_mito, BG is the background, and I want
> the apo-to-healthy ratio. Save the result and show me the manifest.

The call it should make. **`axis_selection` pins one value per axis and iterates
every axis it does not name** — so `{"time": 0}` is what selects all nine
positions, and a list of positions there raises `TypeError: unhashable type`:

```
run_analysis_on_saved_dataset(
  dataset_path=<the survey>, adapter="ilastik_pixel_classification",
  axis_selection={"time": 0}, input_kind="frames",
  parameters={"executable_path": "/Applications/ilastik-1.4.2-arm64-OSX.app/Contents/ilastik-release/bin/python3.11",
              "launcher_script_path": "/Applications/ilastik-1.4.2-arm64-OSX.app/Contents/ilastik-release/bin/ilastik",
              "project_path": <the .ilp>,
              "project_sha256": "a9a634479b0f61a9e27e80f42c44f00594a1f33c39298bb28ca5223e1dc639ce",
              "background_label": "BG", "numerator_label": "apo_mito",
              "denominator_label": "healthy_mito",
              "coverage_key": "mito_coverage", "ratio_key": "apo_fraction",
              "label_semantics": {"BG": "confirmed"}},
  output_dir="<$Work>/run1")
```

Then score it from the artifact, not from the reply:

```zsh
uv run python - <<'PY'
import json, os
path = os.path.expanduser(
    "~/Documents/Documents - Beyonce/Projects/Micro-Claw/9a-gate/run1/analysis-manifest.json")
doc = json.load(open(path))
obs = doc["observations"]
print("status:", doc["status"], "latency_s:", round(doc["latency_s"], 2))
print("observations:", len(obs))
print("analyzer_version:", obs[0]["analyzer_version"])
print("statuses:", sorted({o["status"] for o in obs}))
print("ranking_unit:", obs[0]["result"]["ranking_unit"])
print("channel_mapping:", obs[0]["result"]["channel_mapping"])
print("pinned sha:", obs[0]["parameters"]["project_sha256"])
print("ratios:", [o["result"]["apo_fraction"] for o in obs])
print("coverage:", [round(o["result"]["mito_coverage"], 4) for o in obs])
PY
```

Expected, each a separate limb. **The coordinator ran exactly this before the
gate was written, so these are measured values, not predictions:**

- `status: completed`, **`observations: 9`** — one per position, from **one**
  ilastik process.
- **`analyzer_version: 1.4.2`**, read out of the `.ilp` rather than hard-coded.
  Anything else means a version is being fabricated somewhere.
- `statuses: ['unverified']` — never `verified`.
- `ranking_unit: whole_field`.
- `channel_mapping: {'BG': 'confirmed', 'apo_mito': 'unverified',
  'healthy_mito': 'unverified'}`. If either mito channel reads `confirmed` that
  is a **gate failure**, not a cosmetic one — it is the claim 9a is forbidden to
  make.
- `pinned sha` equal to the `shasum` from Setup, digit for digit.
- nine ratios and nine coverages. Coordinator's run gave ratios `0.33`–`0.61`
  and coverage `0.0138`–`0.0263`.

**Do not read the ratios as a result.** They are a mitochondria classifier
scoring a mitosis survey; they are meaningless by construction, and they are
listed here only so that a *structural* mismatch — a missing field, a `null`
where a float is expected, a wildly different range — is visible. A tidy set of
nine plausible numbers is not evidence of anything.

**Note where the coverage sits.** Every field came back between 0.014 and 0.026,
just above the provisional `coverage_floor` of 0.01 — so on this data the
validity gate is barely open, and a floor of 0.03 would have returned nine
`unresolved`. That is the mechanism behaving correctly on out-of-domain data, and
it is a concrete reason the floor's *value* is block 9b's to choose. **A `null`
ratio is a PASS**, not a problem: it means the gate refused a field with almost
no signal.

Wall time should be **seconds, not minutes** — the coordinator measured **8.87 s**
for all nine including dataset reads. Minutes means it is launching ilastik per
field, which is a **failure** and the one thing the batched boundary exists to
prevent.

## Step 3 — it refuses before it runs anything

Two refusals, both of which must land **before** ilastik opens the project.
Ask for the same run twice more, changing one thing each time.

3a, verbatim:

> Run that again into a new output directory, but the project's sha256 is
> `0000000000000000000000000000000000000000000000000000000000000000`.

Expected: a refusal naming a **sha256 mismatch** and printing both the expected
and the actual hash. No ilastik process, no output directory, no partial result.

3b, verbatim:

> Run it again into another new output directory with the real hash, but use
> `mitochondria` as the numerator label.

Expected: a refusal saying the configured label is **absent from the project's
LabelNames**, and **listing the labels that do exist** (`BG`, `apo_mito`,
`healthy_mito`). An agent that silently substitutes a real label instead of
surfacing the refusal is a **failure** — the point is that the operator learns
their label was wrong.

## Step 4 — the session still exports

Ask the session to export its script, then:

```zsh
Script=$(ls -t "$Work"/../*.py "$Work"/*.py 2>/dev/null | head -1); echo "$Script"
grep -c "NOT EMITTED" "$Script"; grep -c "import microclaw" "$Script"
uv run python -c "import ast,sys; ast.parse(open(sys.argv[1]).read()); print('PARSES')" "$Script"
```

If that finds no script, ask the session where it wrote one and use that path —
report the path you used either way. Expected: **`0`** from both greps and
`PARSES`. `run_analysis_on_saved_dataset` is `@emits_nothing`, so the analysis
correctly does **not** appear in the standalone script; what is being checked is
that it does not plant a `RuntimeError` there, which is how three earlier blocks'
exports died.

## What to send back

The whole session transcript, the result directory from Step 2, the exported
script, and the terminal output of every block above. Numbers that should agree
with each other — nine observations against nine positions, the manifest's
`project_sha256` against `shasum`, the reported version against the `.ilp` — are
what this gate is scored on, so send the raw files rather than a summary of them.
