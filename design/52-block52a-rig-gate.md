# Block 52a rig gate — the declarative named-stage sweep (M2)

Run this on **M2**, on branch `design52/block-52a`. Every command below is
literal. Where a step says "expect", that is the value to compare against, not a
criterion to interpret.

## What this gate proves, and what it does not

It proves: one approval before the run, one dataset, one action set per event
index, requested **and** achieved position recorded on every frame, a bounds
refusal that writes nothing, restoration policy, and an exported script that
compiles and reproduces the same checks.

**It does not prove anything optical.** M2 has never been run in TIRF mode and
the axis sits near epi, so the metric the hook computes may be flat or monotonic
across the sweep. **A metric extremum at the edge of the range is a PASS**, the
same way a boundary peak is a focus finding rather than an authorization one
(design/49). Do not fail this gate because the "optimum" is uninteresting — the
hook only has to compute, log, and select.

**Dose.** 18 frames, and M2's camera triggers the lasers, so every frame is a
dose. Keep the exposure at whatever you would use for a throwaway epi field.

## Step 0 — check out, install, and pin

```powershell
cd $HOME\Code\microclaw
git fetch origin
git checkout design52/block-52a
git pull --ff-only
pip install -e .
if ($LASTEXITCODE -eq 0) { "INSTALL OK" } else { "INSTALL FAILED - stop here" }
```

Pin the implementation by ancestry, never by tip hash:

```powershell
git merge-base --is-ancestor 5e28cd1 HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - gate covers the reviewed implementation" } else { "PIN FAILED - wrong branch or commit; stop" }
```

Run the suite on the rig:

```powershell
python -m pytest -q 2>&1 | Out-File -Encoding utf8 $HOME\Documents\52a-m2-suite.txt
Get-Content $HOME\Documents\52a-m2-suite.txt -Tail 3
```

Expect **1824 passed / 124 skipped / 3 warnings**. **Collection is 1948** — that
is the number that proves the branch, and `passed + skipped` must equal it.
macOS runs the same tree as 1849/99: Windows skips 25 tests that pass elsewhere,
so a *lower* passed count with a correspondingly higher skip count is the
expected result, not a failure. `main` collects 1911, so a run reporting 1911
means the rig is on the wrong branch and every later step is worthless.

(Earlier attempts ran 1935 and 1946 collected. If you see either now, the branch
is stale — pull. Both of those attempts passed Step 0 and failed later.)

> Corrected 2026-08-15 after the first run. This step originally said "expect
> 1836 passed **plus** this rig's skip count", which reads as 1836+124 and made
> a passing suite look like a failure. Skips come out of the total, not on top
> of it.

## Step 1 — read the axis and fix the envelope

In Microclaw, verbatim:

> What is the current position of the stage labelled `TIRF Stage`?

Expect a `get_stage_position` call reporting `position_um`. **Write that number
down; call it P.** Everything below is computed from it.

```powershell
$P = <paste P here>
$min = [math]::Max($P - 500, -10497.8)
$max = [math]::Min($P + 500, 6256.8)
"envelope: {0} to {1} um" -f $min, $max
"step for 18 frames: {0} um" -f (($max - $min) / 17)
```

The clamp is not decoration: `named_stages` for `TIRF Stage` is
`-10497.8 .. 6256.8` and `check_named_stage` fails closed outside it. If either
clamp actually bit, say so in the results — it means P sits within 500 um of a
configured bound.

## Step 2 — register the scoring hook

Verbatim:

> Save and register a hook named `tirf_sweep_metric` for a fixed run. It should
> define `analyze_frame(image, metadata)`, compute a normalised Tenengrad focus
> metric on the frame, and return a `HookResult` whose measurements carry that
> value. It must not try to move any hardware.

Expect: the code shown to you for review before saving, then
`generate_and_save_hook` with `runner_contract="fixed"`, then a manifest entry.
Confirm the code yourself — the human review is the gate, the lint is advisory.

Then, verbatim:

> List the registered hooks.

Expect `tirf_sweep_metric` present with a sha256.

## Step 3 — the sweep, and the one approval

Verbatim, substituting your numbers:

> Run an 18-frame timelapse with no channel at <exposure> ms and a 1 second
> interval, saving to `F:\DataSSD\52a_m2_sweep`. Use the `tirf_sweep_metric`
> hook. Move `TIRF Stage` across <min> to <max> um, one position per frame, using
> a declarative hook action plan. Approve a named-stage envelope over exactly
> that interval with 18 writes and `restore: "leave"`.

> **Two earlier attempts on 2026-08-15 failed here and both are fixed.** The
> first hit a hardware-sequenced batch; the second died because the acquisition
> engine strips any key we add to an event, so the plan could not be matched to
> its frame. Plan entries are now resolved by the event's own **axes**, which the
> engine must preserve. If this step fails a third time, capture the error
> verbatim and stop rather than working around it — a fallback to per-position
> snaps is the very thing this block exists to remove, and it costs the sample
> 18 exposures for no evidence.

**Watch for exactly one confirmation and then 18 frames.** If the run refuses
with a message about a hardware-sequenced burst, that is the new guard working:
raise `interval_s` and re-run, and record the interval that first produced
single-event callbacks — nothing has measured M2's sequencing threshold, and that
number is the most useful thing this attempt can produce beyond a pass.

**The nonzero interval is load-bearing, not politeness.** At `interval_s=0` the
acquisition engine hardware-sequences the time axis and runs the whole burst with
no software in the loop, so there is no per-frame callback to move the stage in —
that is what failed on 2026-08-15. A nonzero time interval defeats time-axis
sequencing. If the run refuses with a message about a sequenced batch, raise the
interval rather than lowering it.

Expect **exactly one** confirmation before the acquisition starts, reading
`ALLOW HOOK HARDWARE CONTROL FOR THIS RUN` and naming the device, the interval,
18 writes and `restore 'leave'`. Approve it.

PASS requires all of:

- **one** confirmation, and **none** from the hook thread during the run;
- one NDTiff dataset with **18** frames;
- no channel argument in the call — M2 has only a `Camera` config group, and a
  channel axis would be refused by `_check_acquisition_channel`;
- the run completes without a refusal record.

If a second confirmation appears mid-run, that is a FAIL and worth capturing
verbatim.

## Step 4 — the audit log is the evidence, not the narration

Verbatim:

> Read the hook log for that run.

```powershell
Get-Content <log_path from the report> | Out-File -Encoding utf8 $HOME\Documents\52a-m2-hooklog.txt
```

Expect, in the log:

- **18** records with `"decision": "accepted"` and `"event": "hook_action"`;
- each carrying `requested_um` **and** `achieved_um` and `error_um`;
- each carrying frame identity — `position` and/or `x_um`/`y_um` — **not** a
  bare `{"position": null}`. That blank is the round-1 defect and its absence is
  what this step exists to confirm;
- `hook_event_index` present on the records and **absent** from every frame's
  axes;
- 18 analysis observations from the hook, one per frame.

Record the largest `error_um` you see. The SmarAct may hit its targets exactly,
where M5's ELL overshot by 5 um; either is a PASS, and the number is worth
keeping because nothing has measured this axis before.

## Step 5 — the bounds refusal, which must write nothing

Verbatim:

> Run the same 18-frame sweep again, but make the last position 6300 um.

`6300` is beyond the configured `named_stages` maximum of `6256.8`, and is
**not** merely outside the run envelope — that is the point, since it exercises
`check_named_stage` and not only the envelope check.

Expect: a refusal **during planning, before the acquisition starts**. There is
no partial dataset on this path, because validation happens before any hardware
moves — do not go looking for one.

```powershell
python -c "import sys; sys.exit(0)"
```

Then confirm nothing moved:

> What is the current position of `TIRF Stage`?

Expect the position the sweep left it at (near <max>, since `restore: leave`
writes nothing on exit), **unchanged by the refused run**.

## Step 6 — export, and run the script standalone

Verbatim:

> Export this session as a standalone script called `tirf_sweep.py`.

Expect a path in the report; the file may land under
`C:\Users\<you>\AppData\Local\microclaw\` rather than the checkout, because the
exporter resolves through `resolve_in_workspace`. Use the absolute path the tool
prints.

```powershell
$script = "<path the tool reported>"
Select-String -Path $script -Pattern "NOT EMITTED", "raise RuntimeError", "import microclaw" | ForEach-Object { $_.Line }
"--- expect no lines above this one ---"
Select-String -Path $script -Pattern "_NAMED_STAGE_ENVELOPE", "hook_action_plan", "hook_event_index", "configure_named_stage" | Measure-Object | ForEach-Object { "envelope/plan markers found: " + $_.Count }
python -c "import ast,sys; ast.parse(open(sys.argv[1], encoding='utf-8').read()); print('SCRIPT PARSES')" $script
```

Expect: no `NOT EMITTED`, no `raise RuntimeError`, no `import microclaw`, at
least four envelope/plan markers, and `SCRIPT PARSES`.

**The `move_named_stage` calls you made in Step 1 must appear as
`core.set_position('TIRF Stage', <resolved>)`.** They were undecorated until this
block; if any `# NOT EMITTED: move_named_stage` appears, the branch is stale.

Then run it, with the stage returned to roughly P first:

```powershell
python $script 2>&1 | Out-File -Encoding utf8 $HOME\Documents\52a-m2-standalone.txt
if ($LASTEXITCODE -eq 0) { "STANDALONE EXIT OK" } else { "STANDALONE EXIT NONZERO" }
Get-Content $HOME\Documents\52a-m2-standalone.txt -Tail 20
```

Expect the script to print the envelope, ask `Type YES to continue:`, and — on
YES — repeat the 18-position sweep writing its own dataset. A successful run
prints little else. Capture the exit line either way; 53a's gate lost that line
and had to lean on empty stderr.

## Step 7 — restoration

`restore: "leave"` was used above, so expect the run report's
`named_stage_restoration` to read `policy: "leave"`, `restored: false`, and to
name both the entry position and the last written one. **Nothing should have
been written on exit** — that is design/38 F9, and the entry/last pair is how you
check it without trusting the narration.

If you have time, repeat Step 3 once with `restore: "entry"` and confirm the axis
returns to P through the same guard/move/read-back path, and that the report says
`restored: true`.

## What to send back

- `52a-m2-suite.txt`, `52a-m2-hooklog.txt`, `52a-m2-standalone.txt`, the exported
  script, and the dataset path.
- P, the clamped envelope, and the largest `error_um`.
- The confirmation text verbatim, and whether exactly one appeared.
- Any step that did not behave as written, quoted rather than summarised.

Put them in
`~\Documents\Documents - Beyonce\Projects\Micro-Claw\52a-m2` so they sit beside
the precheck.
