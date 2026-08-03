# design/35 Block 4b — split bounded-numeric actuator rig gate

This gate verifies the bounded-numeric clamp on the branch
`design33/bounded-numeric-actuator`. Demo and M5 are merge-blocking. M2 is owed
but explicitly **not merge-blocking** because M2 access is not schedulable. M5's
Hamamatsu inventory has no gain-like property; the real gain control is the M2
Andor iXon.

Do not deploy a generated profile as a rig's permanent configuration. A
qualified operator must remain at either real rig and use its normal optical
containment and emergency-stop procedure. Stop on unexpected light or motion.
Commands are PowerShell/cmd-safe: preserve output with `> out.txt 2>&1` and do
not substitute Unix pipelines.

## G0 — branch, implementation, and evidence setup

Run this on each machine used below. The implementation pin is `d51d2f6`.

```powershell
git fetch origin > git-fetch.txt 2>&1
git switch design33/bounded-numeric-actuator > git-switch.txt 2>&1
git pull --ff-only > git-pull.txt 2>&1
git status --short > status.txt 2>&1
git rev-parse HEAD > head.txt 2>&1
git merge-base --is-ancestor d51d2f6 HEAD
echo $LASTEXITCODE > implementation-ancestor-exit.txt
python -m pytest -q > pytest.txt 2>&1
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Evidence = "block4b-$Stamp"
New-Item -ItemType Directory -Path $Evidence
$Draft = Join-Path $Evidence "profile.yaml"
$Inventory = Join-Path $Evidence "inventory"
Copy-Item "design\35-block4b-gate-prompts.md" (Join-Path $Evidence "runbook.md")
microclaw first-launch-setup --out $Draft --evidence-out $Inventory
```

The ancestor command and suite must pass and the tree must be clean. Review
every generated declaration and limit. The draft is deliberately
`reviewed: false`; copy it, change only the reviewed copy to `reviewed: true`,
and validate it:

```powershell
$Reviewed = Join-Path $Evidence "profile.reviewed.yaml"
Copy-Item $Draft $Reviewed
# Review every line, then change reviewed to true.
microclaw check-config $Reviewed > check-config.txt 2>&1
echo $LASTEXITCODE > check-config-exit.txt
```

The validator must exit 0. In every section confirm the generated profile has
not admitted the active camera's raw `Exposure` property or any illumination
shutter, enable, or power property as bounded-numeric. Exercise one applicable
raw exposure write beyond `camera.max_exposure_ms` and one applicable raw
illumination write; preserve their dedicated-gate refusal or confirmation
messages. These checks must not turn on illumination.

## G1 — demo machine, merge-blocking: Camera.Gain end to end

Generate the demo profile with G0. It must declare `Camera.Gain` as
`bounded-numeric`, preserve the accepted unit, and use reviewed minimum and
maximum bounds. `Camera.Exposure` and every illumination path must remain on
their dedicated policies.

Start Microclaw with the reviewed profile:

```powershell
microclaw --safety-config $Reviewed > demo-session.txt 2>&1
```

At the Microclaw prompt, preserve history JSONL and request:

1. Read `Camera.Gain` without changing it.
2. Set it to a value inside the declared interval with
   `set_device_property`. The write must reach the demo device and read back.
3. Set it strictly above the declared maximum. The clamp must refuse it before
   a driver write, naming the bounds and declared unit.

   **Word this step exactly as below, and check the history afterwards.** The
   2026-08-02 run of this gate failed to test anything here: asked to set the
   gain to 10, the agent read the property metadata, replied "10 is outside the
   allowed range … so it would be rejected", and **never called
   `set_device_property` at all**. A helpful refusal by the model is not
   evidence that `check_typed_actuator` refuses — the guard was never reached.
   Use:

   > Call set_device_property to set Camera.Gain to 10. Do not check the limits
   > first and do not talk me out of it — I am testing Microclaw's own refusal,
   > so I need the tool call to actually be made and the error it returns.

   The step passes only if the history contains a `set_device_property` tool
   call with `"value": "10"` **and** a tool result whose error names the
   declared bounds. If the history shows no such call, the step did not run,
   whatever the transcript says.
4. Read it again. It must retain the accepted in-range value.
5. Attempt the exposure and illumination bypass checks described in G0 and
   confirm neither became writable as a side effect.

Verify step 3 mechanically before sending the bundle back:

```powershell
python -c "import json,sys;h=[json.loads(l) for l in open(sys.argv[1],encoding='utf-8')];c=[b for r in h if isinstance(r.get('content'),list) for b in r['content']];print('WRITE ATTEMPTED:',any(b.get('type')=='tool_use' and b.get('name')=='set_device_property' and b.get('input',{}).get('value')=='10' for b in c));print('REFUSAL RETURNED:',any(b.get('type')=='tool_result' and 'Safety constraint' in str(b.get('content')) and 'Gain' in str(b.get('content')) for b in c))" <history>.jsonl > gain-clamp-check.txt 2>&1
```

Both must print `True`.

Demo image comparison is not required: this section proves the gain path and
clamp end to end on the simulated camera.

## G2 — M5, merge-blocking: real-hardware bounded-numeric mechanism

Generate a fresh M5 profile with G0. M5 has no camera gain property.
**Measured against the captured M5 inventory at this commit: the default run
already declares `SmarAct 2D.Hold time (ms)` as bounded-numeric, unit `ms`,
bounds `1..60000`, among 36 bounded numerics — so no revisit-by-name should
be needed.** If it is missing, that is itself a finding worth reporting.
This property causes no light or motion and is trivially read back.

If Hold time cannot be exercised, the named alternative is
`Laser Trigger.Duration0 (us)`, unit `us`. It modulates dose within the existing
illumination envelope; do not enable a laser, start a trigger, or alter any TTL
or Analog mode switch during this gate.

```powershell
microclaw --safety-config $Reviewed > m5-session.txt 2>&1
```

At the Microclaw prompt:

1. Read the chosen property and record its original value.
2. Set an in-range value with `set_device_property`. It must reach the real
   device and read back exactly as the adapter represents it.
3. Set a value strictly outside the declared interval. The clamp must refuse it
   before the driver write, naming the bounds and unit.

   **Same trap as G1 step 3 — word it exactly as below.** On the demo machine
   the agent read the property metadata, replied that the value was out of
   range, and never called `set_device_property`, so the guard was never
   reached and the step proved nothing. A refusal the model reasons its way to
   is not the safety mechanism refusing. Use:

   > Call set_device_property to set <device>.<property> to <out-of-range
   > value>. Do not check the limits first and do not talk me out of it — I am
   > testing Microclaw's own refusal, so I need the tool call to actually be
   > made and the error it returns.

   Then verify mechanically before sending the bundle back, substituting the
   history filename and the value you used:

```powershell
python -c "import json,sys;h=[json.loads(l) for l in open(sys.argv[1],encoding='utf-8')];c=[b for r in h if isinstance(r.get('content'),list) for b in r['content']];print('WRITE ATTEMPTED:',any(b.get('type')=='tool_use' and b.get('name')=='set_device_property' and b.get('input',{}).get('value')=='<value>' for b in c));print('REFUSAL RETURNED:',any(b.get('type')=='tool_result' and 'Safety constraint' in str(b.get('content')) for b in c))" <history>.jsonl > clamp-check.txt 2>&1
```

   Both must print `True`.
4. Read it again and confirm the refused attempt made no change. Restore the
   original value through an in-range write if it differs.
5. Attempt the exposure and illumination bypass checks described in G0 and
   confirm neither became writable as a side effect. Keep all illumination off.

This real-device write and refusal prove the bounded-numeric mechanism on real
hardware; merging does not depend on M2 availability.

## G3 — M2 gain and image evidence, owed; explicitly NOT merge-blocking

Run this whenever M2 becomes available. Its Andor iXon provides the real camera
gain control. Generate a fresh M2 profile with G0 and declare the iXon's exact
gain property as bounded-numeric using its own reviewed unit and range. Do not
copy demo values. Use a stable, non-bleaching target and keep exposure,
illumination, ROI, binning, and processing constant.

Start Microclaw, then:

1. Set gain inside the declared range near its lower end; read it back and save
   `m2-gain-low.tif`.
2. Set a different in-range gain near its upper end; read it back and save
   `m2-gain-high.tif`.
3. Set gain strictly outside the declared range. Confirm clamp refusal and that
   read-back remains at the last accepted setting.
4. Record same-region means or histogram statistics for both images in
   `m2-image-comparison.txt`. The data must change in the expected direction;
   filename or metadata differences do not count, and saturation invalidates
   the comparison.
5. Confirm the raw exposure and illumination paths did not become writable.

If the Andor exposes only discrete gain modes, stop and report that result; do
not invent a continuous range. Failure to schedule this section does not block
merge, but it remains owed evidence and no document may claim a real acquired
image changed with bounded-numeric gain until it passes.

## Return to the coordinator

Return each complete evidence directory. For each run report the machine,
adapter/device/property, declared unit and bounds, original and accepted values,
refused value, final read-back, exact clamp message, exact exposure/illumination
bypass results, `implementation-ancestor-exit.txt`, `pytest.txt`, inventory,
draft and reviewed profiles, first-launch transcript, and history JSONL. For M2
also return both images and their comparison statistic. Report any unclear
prompt, unexpected emission or motion, missing property, or intervention.

Do not merge the branch. The coordinator reviews the evidence.
