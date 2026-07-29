# design/33 Block 9b — read-only rig inventory demo gate

Gate for `design33/read-only-rig-inventory`. Do not open a PR or merge until the
operator records an attributable verdict for every step below.

Use one dated evidence directory for the whole gate. It must stay **outside the
repository**; commit this runbook, never its outputs. Commands are
PowerShell-primary and use file redirection rather than Unix pipelines.

## What this gate can and cannot settle

The demo core can settle that `inspect-rig` crosses the real pyjavaz bridge using
queries only; records stable identity, device/property metadata, StateDevice
labels, shutters, configuration groups, and fully expanded presets; and compares
those facts with a real parsed reviewed config without turning candidates into
safety decisions.

It cannot settle real-driver enumeration failures, serial or FPGA devices,
credential-bearing drivers, or the scale and oddities of a production rig. The
Block 9b cross-rig checklist bullet therefore stays unticked even if every demo
step passes.

## Common setup

```powershell
$Repo       = "<repo>"
$DemoConfig = "<full path to MMConfig_demo.cfg>"
$Evidence   = "<dated directory OUTSIDE the repo>\block9b-YYYY-MM-DD"
$Port       = 4827

New-Item -ItemType Directory -Force $Evidence | Out-Null
Set-Location $Repo
git fetch origin > "$Evidence\fetch.txt" 2>&1
git switch design33/read-only-rig-inventory > "$Evidence\checkout.txt" 2>&1
git pull --ff-only > "$Evidence\pull.txt" 2>&1
git rev-parse HEAD > "$Evidence\setup-head.txt" 2>&1
python -m pip install -e . > "$Evidence\install.txt" 2>&1
```

Replace every `<...>` placeholder before continuing.

Everything in this gate happens **on the branch, never on `main`.** The
`--ff-only` pull above fast-forwards `design33/read-only-rig-inventory`; `main`
is not needed on this machine at all. The `git fetch` is not optional: this
branch is new, and `git switch` cannot create a local tracking branch for a
remote ref the machine has not yet seen. If `switch` still reports an invalid
reference, the fetch did not reach GitHub — check that step's output before
anything else. Authentication may need this machine's own SSH key
(`GIT_SSH_COMMAND="ssh -i <key>" git fetch origin`); the key name that works on
the development Mac is not necessarily the one here.

`setup-head.txt` records exactly which commit you tested; keep it with the
evidence. It must match the branch tip the coordinator named in the handoff. If
it does not, you are testing something other than what was reviewed — stop and
ask, rather than assuming a newer commit is a better one. (A specific SHA is
deliberately not written here: this runbook is committed on the branch it
describes, so any correction to it changes the tip and would make a pinned
value wrong the moment it was written.)

A stale editable install has caused misleading failures in this repository, so
reinstalling is part of the gate, not optional setup. It is safe here because
this is a dedicated machine; do not run it on the development Mac. If this
machine uses `uv`, replace each subsequent `python` with `uv run python`; the
commands otherwise stay the same.

**SETUP VERDICT — PASS / FAIL:** ____________________

---

## 1. Capture environment identity and off-rig baseline

```powershell
git rev-parse HEAD > "$Evidence\commit.txt" 2>&1
git status --short > "$Evidence\status.txt" 2>&1
python -V > "$Evidence\python.txt" 2>&1
python -m pip show pycromanager pyjavaz > "$Evidence\mm-packages.txt" 2>&1
python -c "import platform; print(platform.platform())" > "$Evidence\os.txt" 2>&1
python -m pytest -q > "$Evidence\pytest.txt" 2>&1
```

The POSIX equivalents differ only in path separators: use `$Evidence/file.txt`
instead of `$Evidence\file.txt`.

Micro-Manager's own version is **not** captured here. It is read over the bridge
and recorded by `inspect-rig` itself, as `facts.core_identity.version` and
`facts.core_identity.api_version` inside every `inventory.json` from Step 2 on.
Do not add a second core to this step to fetch it: the project talks to MM only
through the pycro-manager ZMQ bridge, and instantiating any other core object
here would neither read the running Micro-Manager nor prove anything about it.

**PASS:** `commit.txt` names the expected pushed SHA, `status.txt` is empty,
every identity file is populated, and pytest reports **1105 passed / 99 skipped /
3 warnings**. There is no known-failing test on this branch: the coordinator
measured this exact count on the pushed commit. Treat any failure as real and
stop — in particular `test_readme_png_is_not_stale`, which passes here and was
reported as an environment artifact during implementation. That report was
mistaken and traced to a `readline` stub the implementer had put on the import
path; it is not a property of this branch or of Pillow.

**FAIL:** identity is incomplete, the tree is dirty before the run, or tests fail.

**STEP 1 VERDICT — PASS / FAIL:** ____________________

---

## 2. First inventory, with no safety config

Start Micro-Manager with its demo config and ZMQ server on `$Port`, then run:

```powershell
$Run1 = "$Evidence\without-safety-1"
New-Item -ItemType Directory -Force $Run1 | Out-Null
python -m microclaw --port $Port inspect-rig --mm-config $DemoConfig --out $Run1 > "$Evidence\without-safety-1.txt" 2>&1
```

**PASS:** exit code 0; `$Run1\inventory.json` and `$Run1\review.md` exist; the
inventory has `human_decisions.source: null`; no default safety-config refusal
appears. This is the bootstrap behavior that differs from `authorization-map`.

**FAIL:** a safety config is demanded, connection starts an agent/app/acquisition,
or either output is absent.

**STEP 2 VERDICT — PASS / FAIL:** ____________________

---

## 3. Repeat unchanged and prove fingerprint determinism

Do not reload the config, change a property, or restart Micro-Manager between
Steps 2 and 3.

```powershell
$Run2 = "$Evidence\without-safety-2"
New-Item -ItemType Directory -Force $Run2 | Out-Null
python -m microclaw --port $Port inspect-rig --mm-config $DemoConfig --out $Run2 > "$Evidence\without-safety-2.txt" 2>&1
python -c "import json,sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['live_inventory_fingerprint']['value'])" "$Run1\inventory.json" > "$Evidence\fingerprint-1.txt" 2>&1
python -c "import json,sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['live_inventory_fingerprint']['value'])" "$Run2\inventory.json" > "$Evidence\fingerprint-2.txt" 2>&1
Compare-Object (Get-Content "$Evidence\fingerprint-1.txt") (Get-Content "$Evidence\fingerprint-2.txt") > "$Evidence\fingerprint-diff.txt" 2>&1
```

On POSIX, replace the last command with
`diff "$Evidence/fingerprint-1.txt" "$Evidence/fingerprint-2.txt" > "$Evidence/fingerprint-diff.txt" 2>&1`.

Compare exactly `live_inventory_fingerprint.value`, the lowercase SHA-256 hex
string. Error text remains visible in `facts.enumeration_failures`, but is
deliberately excluded from identity because drivers may embed unstable handles,
addresses, or timestamps; stable failure `scope` and `field` remain covered.

**PASS:** both commands exit 0, both fingerprint files contain one identical
64-character value, and `fingerprint-diff.txt` is empty.

**FAIL:** either run fails or the unchanged rig produces different values.

**STEP 3 VERDICT — PASS / FAIL:** ____________________

---

## 4. Inventory with the reviewed demo comparison config

Make an evidence-local copy only to replace `workspace_dir`; do not edit the
repository fixture.

```powershell
$Reviewed = "$Evidence\demo-safety-config.yaml"
$Run3 = "$Evidence\with-safety"
Copy-Item "design\33-block5-demo-safety-config.yaml" $Reviewed
python -c "from pathlib import Path; import sys; p=Path(sys.argv[1]); p.write_text(p.read_text(encoding='utf-8').replace('/REPLACE/with/a/real/directory', sys.argv[2].replace('\\','/')), encoding='utf-8')" $Reviewed $Evidence > "$Evidence\prepare-reviewed.txt" 2>&1
New-Item -ItemType Directory -Force $Run3 | Out-Null
python -m microclaw --port $Port --safety-config $Reviewed inspect-rig --mm-config $DemoConfig --out $Run3 > "$Evidence\with-safety.txt" 2>&1
```

On POSIX, copy with `cp`, then replace the placeholder using an editor; the
`inspect-rig` invocation is identical apart from `/` path separators.

**PASS:** exit code 0, both outputs exist, and
`human_decisions.source` names `$Reviewed`.

**FAIL:** parsing the real fixture fails, outputs are absent, or comparison is
not attached.

**STEP 4 VERDICT — PASS / FAIL:** ____________________

---

## 5. By-hand completeness and decision-boundary review

Open `$Run3\inventory.json` and `$Run3\review.md` and check all of the following:

1. StateDevice labels for **Objective, Dichroic, Emission, Excitation, and Path**
   are present and complete under each device's `state_labels`.
2. Both demo shutters are present as devices with their complete property lists.
3. The `Channel` configuration group contains every preset, and every preset's
   `effects` completely expands its device, property, and value settings.
4. None of those StateDevice position paths is listed under **Live paths missing
   from reviewed config**. Block 3b authorizes undeclared StateDevice
   `Label`/`State` pairs automatically when neither position property has an
   operator ruling.
5. These observations are **not reported as safety decisions**. A failure would
   look like Objective/Dichroic/Emission/Excitation/Path `Label` or `State`, a
   shutter, or a Channel preset effect appearing as a declaration/approval in
   `human_decisions`, or wording in `review.md` saying it is authorized, safe,
   approved, or an inferred limit. The review must instead say candidates are
   questions for a human, not safety decisions or approvals.
6. Any failed read-only/pre-init query appears both under **Enumeration
   failures** and **Properties with unknown writability**. Read-only properties
   are not buried in **Live paths missing from reviewed config**.

**PASS:** every named device/group is complete and all six boundary checks hold.

**FAIL:** any named item is absent/incomplete, an auto-classified position is
reported undeclared, a read-only path is reported undeclared, unknown
writability disappears, or observed data is presented as a safety decision.

**STEP 5 VERDICT — PASS / FAIL:** ____________________

---

## Final gate verdict

**DEMO GATE VERDICT — PASS / FAIL (date: __________, operator: __________):**
____________________

Record findings below the verdict. A PASS settles only the demo-core claims in
this runbook; it does not tick the cross-rig checklist bullet and does not
authorize opening a PR before the coordinator reviews the evidence.
