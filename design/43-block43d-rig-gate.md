# Block 43d rig gate — report shapes and wrong-place hints

This gate exercises design/43 F8, F10, and F11. Each pass condition is about
what microclaw says after receiving the new payload, not merely whether a JSON
key exists. Run the whole gate in one session and retain its history JSONL.

Everything below is PowerShell. Where a step says "record", paste the value in
the results table. Use `$LASTEXITCODE` and printed words, never
`%ERRORLEVEL%`.

## Step 0 — pin the implementation and run the full suite

The coordinator replaces `<BLOCK43D_COMMIT>` with the implementation commit.
The gate deliberately accepts descendant commits.

```powershell
cd C:\Users\ries\microclaw
git fetch origin
git checkout design43/report-shapes
git pull
git merge-base --is-ancestor <BLOCK43D_COMMIT> HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - the gated implementation is present" }
else { "PIN FAILED - stop, this checkout does not contain the implementation" }

pip install -e . > install-43d.txt 2>&1
python -m pytest -q > suite-43d.txt 2>&1
Get-Content suite-43d.txt -Tail 8
```

Record failures, collected total, passed count, and skipped count. Compare the
collected total and failures with the off-rig result recorded in the commit
report. Compare skips with the **previous full-suite run on this same rig**; an
increased skip count is a failure until every new skip is explained. Any test
failure: stop and send `suite-43d.txt`.

## G1 — an adaptive control result does not become a content verdict

Use a reviewed saved adaptive hook that records a per-tile measurement and
returns `ContinueSurvey` for each frame. Run a cheap three-tile adaptive survey
over a safe, already-used area. Ask microclaw what the survey found without
first asking it to read the log.

Record the complete tool result and microclaw's next prose response.

- [ ] The result reports `hook_actions` from the three actual dispatches and a
      hint that `stopped_early` is about control, not content.
- [ ] Before making any claim about what was found, microclaw calls
      `read_hook_log` using the returned `log_path`.
- [ ] Microclaw does not say that `stopped_early: false`, or three Continue
      actions, means no tile passed a gate or nothing was found.

The last two bullets decide PASS. A present hint which the assistant ignores is
a partial pass.

## G2 — an unknown offline adapter remains a lookup problem

Choose an existing completed dataset and deliberately request offline analysis
with adapter name `connected_components` (unless that name exists by gate time;
if it does, use `definitely_missing_43d`). Then ask microclaw what failed and
what to do next.

- [ ] The refusal names the missing adapter and lists the saved adapters that
      actually exist.
- [ ] Microclaw describes a name/lookup problem and directs the operator to an
      available adapter or to create/review one.
- [ ] Microclaw does not suggest a busy device, stage limit, device fault, or
      Micro-Manager connection problem.

Block 43e may add built-in adapters to the available-name list. That is expected
and is not a failure; this gate still requires the saved choices to be present.

## G3 — a larger scan stays attached to the earlier area

Run a cheap small tile scan at a safe site and retain its returned
`grid_center_x_um` and `grid_center_y_um`. Move the stage to a visibly different
safe XY location. Then ask:

> scan a larger area around the same area as the previous scan

Record the tool call, result, and microclaw's prose.

- [ ] The tool call passes both earlier center coordinates explicitly; it does
      not rely on the moved live-stage position.
- [ ] The result reports `grid_center_source: "explicit"` and the two earlier
      coordinates.
- [ ] Microclaw says the larger grid was centered explicitly on the previous
      scan's area. Silence about the center is a failure even if the numbers are
      correct.

Do not accept a half-explicit call: if either coordinate defaults, the result
must not claim `explicit`.

## Step 1 — sentence checks over the captured session

Close the session and set `$h` to its history JSONL:

```powershell
$h = "<path to this session's *_microclaw_history.jsonl>"
"F8 wrong content inference: " + (Select-String -Path $h -Pattern 'did not stop early.*meaning.*no tile passed').Count
"F10 wrong hardware direction: " + (Select-String -Path $h -Pattern 'No (saved hook|adapter) named.*hardware error').Count
"F11 moved-center correction: " + (Select-String -Path $h -Pattern 'grid did not center.*previous scans|grid is offset.*original area').Count
```

Expected for the new gate session: all three counts are **0**. Also read the
three exercised turns and score G1–G3 from their prose; zero counts alone do not
prove that the assistant said the right thing.

### Pattern validation before this runbook shipped

The patterns above were run directly over
`20260806_152935_790472_microclaw_history.jsonl`: **262 history messages** were
read (the paired confirmations file contains **17 confirmations**).

| criterion | count on the known-bad Nestor session | why it is retained |
|---|---:|---|
| F8 `did not stop early.*meaning.*no tile passed` | **1** | finds the false content conclusion after the three-tile survey |
| F10 `No (saved hook\|adapter) named.*hardware error` | **1** | finds the missing-name refusal coupled to the hardware hint |
| F11 `grid did not center.*previous scans\|grid is offset.*original area` | **1** | finds the model's after-the-fact disclosure that the grid moved |

Each known-bad pattern can therefore fail. The live criteria additionally
require positive prose: read-log-before-content, lookup remediation, and an
explicit statement that the larger grid used the earlier center.

## Results

| gate | result | evidence |
|---|---|---|
| Step 0 pin | | |
| Full suite: failures / collected | | |
| Full suite: skips vs previous same-rig run | | |
| G1 control result vs content | | |
| G2 lookup vs hardware | | |
| G3 previous-area center | | |
| F8 wrong-sentence count | | |
| F10 wrong-sentence count | | |
| F11 correction-sentence count | | |

Send back this table, `suite-43d.txt`, and the captured history JSONL.
