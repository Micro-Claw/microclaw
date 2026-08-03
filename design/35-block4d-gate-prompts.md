# design/35 Block 4d — property-authorization rename gate

This runbook verifies branch `design33/property-authorization-rename`. The
implementation is pinned at `c063f16`; later documentation commits are valid
descendants. Run the demo-machine gate first. It requires no hazardous motion.
Run the M5 gate only after the demo evidence passes. Do not replace the deployed
M5 configuration during this gate.

All commands below are PowerShell-safe. Preserve the named raw files and their
exit-code companions. Stop each successful `serve` process with Ctrl+C only
after its browser session has reached the prompt.

## G0 — branch identity and suite

Run on both machines from the Microclaw checkout:

```powershell
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Evidence = "block4d-$Stamp"
New-Item -ItemType Directory -Path $Evidence
git fetch origin > "$Evidence\git-fetch.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\git-fetch-exit.txt"
git switch design33/property-authorization-rename > "$Evidence\git-switch.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\git-switch-exit.txt"
git pull --ff-only > "$Evidence\git-pull.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\git-pull-exit.txt"
git status --short > "$Evidence\status.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\status-exit.txt"
git rev-parse HEAD > "$Evidence\head.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\head-exit.txt"
git merge-base --is-ancestor c063f16 HEAD > "$Evidence\implementation-ancestor.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\implementation-ancestor-exit.txt"
python -m pytest -q > "$Evidence\pytest.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\pytest-exit.txt"
Copy-Item "design\35-block4d-gate-prompts.md" "$Evidence\runbook.md"
```

It worked when the ancestor and pytest exit files contain `0`, pytest reports no
failures, and `status.txt` is empty. Send back the complete evidence directory,
including `status.txt`, `head.txt`, `implementation-ancestor-exit.txt`, and
`pytest.txt`.

## G1 — demo machine, complete interview and new-key startup

Start Micro-Manager with its stock demo configuration. The setup interview is
read-only, but it must be completed in full: answer every question, do not reuse
an old inventory, and do not move any device. Use reviewed fictional bounds
appropriate to the simulator only.

```powershell
$Port = 4827
$Draft = Join-Path $Evidence "demo-new-key.draft.yaml"
$Inventory = Join-Path $Evidence "demo-inventory"
microclaw --port $Port first-launch-setup --out $Draft --evidence-out $Inventory > "$Evidence\demo-interview.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\demo-interview-exit.txt"
Select-String -Path $Draft -Pattern "property_authorization","allowed_categorical","allowed_numeric","denied","rig_profile","categorical_properties","typed_actuators","excluded_properties" > "$Evidence\demo-generated-shape.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\demo-generated-shape-exit.txt"
microclaw check-config $Draft > "$Evidence\demo-new-key-unreviewed-check.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\demo-new-key-unreviewed-check-exit.txt"
Copy-Item $Draft "$Evidence\demo-new-key.reviewed.yaml"
```

It worked when the interview exits `0`; the shape output contains
`property_authorization`, `allowed_categorical`, `allowed_numeric`, and `denied`
and contains none of the four old names; and `check-config` refuses only because
setup deliberately wrote `reviewed: false`. Send back the raw draft, complete
inventory directory and transcript, shape output, and validator output.

Read the entire copied profile and change only `reviewed: false` to
`reviewed: true` by hand. Then run:

```powershell
microclaw check-config "$Evidence\demo-new-key.reviewed.yaml" > "$Evidence\demo-new-key-reviewed-check.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\demo-new-key-reviewed-check-exit.txt"
microclaw --port $Port --safety-config "$Evidence\demo-new-key.reviewed.yaml" serve --no-browser > "$Evidence\demo-new-key-session.txt" 2>&1
```

It worked when `check-config` exits `0` and the browser session reaches its
normal prompt without a schema or authorization startup refusal. Stop it with
Ctrl+C. Send back the reviewed profile, validator output, full session output,
and the session history JSONL.

## G2 — demo machine, old-key refusal

Copy `demo-new-key.reviewed.yaml` to
`demo-old-key.reviewed.yaml`, make exactly these four textual renames by hand:

- `property_authorization` to `rig_profile`
- `allowed_categorical` to `categorical_properties`
- `allowed_numeric` to `typed_actuators`
- `denied` to `excluded_properties`

Do not change values, indentation, `mode`, or any other section. Run both the
offline validator and live entry point:

```powershell
microclaw check-config "$Evidence\demo-old-key.reviewed.yaml" > "$Evidence\demo-old-key-check.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\demo-old-key-check-exit.txt"
microclaw --port $Port --safety-config "$Evidence\demo-old-key.reviewed.yaml" serve --no-browser > "$Evidence\demo-old-key-session.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\demo-old-key-session-exit.txt"
```

It worked when both commands refuse before a session starts, both exit nonzero,
and both outputs name `rig_profile` as an unknown top-level key and
`property_authorization` as missing. There is no deprecation warning or
migration path. Send back the old-key file and both complete outputs with exit
files.

## G3 — M5, refuse unchanged deployment, then apply the clean cut

Identify the exact safety config used by the normal M5 launcher. Substitute
that exact path for `<deployed-config>`. First record its hash and prove the
unmodified old-key file is refused:

```powershell
Get-FileHash -Algorithm SHA256 "<deployed-config>" > "$Evidence\m5-deployed-before.sha256.txt" 2>&1
Copy-Item "<deployed-config>" "$Evidence\m5-deployed-before.yaml"
microclaw check-config "<deployed-config>" > "$Evidence\m5-deployed-check.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\m5-deployed-check-exit.txt"
microclaw --safety-config "<deployed-config>" serve --no-browser > "$Evidence\m5-deployed-refusal.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\m5-deployed-refusal-exit.txt"
```

It worked when both commands exit nonzero before a session starts and both
outputs name `rig_profile` as unknown and `property_authorization` as missing.
Preserve these outputs before editing anything.

Now edit the operator's deployed file in place, changing exactly these four key
names and no values or indentation:

- `rig_profile` to `property_authorization`
- `categorical_properties` to `allowed_categorical`
- `typed_actuators` to `allowed_numeric`
- `excluded_properties` to `denied`

Record the post-edit hash, validate, and start:

```powershell
Get-FileHash -Algorithm SHA256 "<deployed-config>" > "$Evidence\m5-deployed-after.sha256.txt" 2>&1
git diff --no-index -- "$Evidence\m5-deployed-before.yaml" "<deployed-config>" > "$Evidence\m5-four-key.diff" 2>&1
microclaw check-config "<deployed-config>" > "$Evidence\m5-renamed-check.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\m5-renamed-check-exit.txt"
microclaw --safety-config "<deployed-config>" serve --no-browser > "$Evidence\m5-renamed-session.txt" 2>&1
```

It worked when the before/after hashes differ, `check-config` exits `0`, and the
normal M5 session reaches its prompt. Inspect the file diff locally and confirm
that only the four key names changed. Do not call motion, illumination,
acquisition, or mutation tools. Stop with Ctrl+C. Send back the deployed path,
both hashes, both refusal outputs, the renamed validator and session outputs,
the exact four-key diff, and the session history JSONL.

## G4 — M5, regenerated new-key config

Run a fresh full interview against the currently loaded M5 configuration. Keep
illumination off and perform no motion. Review every generated value according
to local rig knowledge; do not infer or copy limits merely to make validation
pass.

```powershell
$Draft = Join-Path $Evidence "m5-new-key.draft.yaml"
$Inventory = Join-Path $Evidence "m5-inventory"
microclaw first-launch-setup --out $Draft --evidence-out $Inventory > "$Evidence\m5-interview.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\m5-interview-exit.txt"
Select-String -Path $Draft -Pattern "property_authorization","allowed_categorical","allowed_numeric","denied","rig_profile","categorical_properties","typed_actuators","excluded_properties" > "$Evidence\m5-generated-shape.txt" 2>&1
Copy-Item $Draft "$Evidence\m5-new-key.reviewed.yaml"
```

It worked so far when setup exits `0` and the generated-shape output contains
only the four new names. Read the full copied profile, resolve every review note,
and change `reviewed: false` to `reviewed: true` only when the file is genuinely
reviewed. Then run:

```powershell
microclaw check-config "$Evidence\m5-new-key.reviewed.yaml" > "$Evidence\m5-new-key-check.txt" 2>&1
echo $LASTEXITCODE > "$Evidence\m5-new-key-check-exit.txt"
microclaw --safety-config "$Evidence\m5-new-key.reviewed.yaml" serve --no-browser > "$Evidence\m5-new-key-session.txt" 2>&1
```

It worked when `check-config` exits `0` and the normal session reaches its
prompt. Do not call motion, illumination, acquisition,
or mutation tools. Stop with Ctrl+C. Send back the full inventory and transcript,
draft and reviewed profiles, shape and validator outputs, full session output,
and history JSONL. Do not install the regenerated file as M5's deployed config.

## Return to the coordinator

Return the complete demo and M5 evidence directories, not summaries. Explicitly
report the outcome of the new-key startup, old-key refusal, unchanged deployed
M5 refusal, hand-renamed deployed M5 startup, and regenerated M5 startup. Include
every `*-exit.txt`, raw profile, transcript,
validator output, session output/history, hash, `status.txt`, `head.txt`, and
pytest output. Report any unclear interview prompt or any difference other than
the four schema key renames. Do not merge, push `main`, or alter the deployed M5
configuration.
