# Block 79c-1 — demo-machine gate: does the composite breakdown measure a real grid?

Branch `design79/the-per-field-multiplier`. **No sample, no dose, no hardware
write**: the demo camera and the demo stage only. Nothing edits a safety config.

## What is already settled, and what this gate is for

Settled locally, and re-verified by the coordinator: both composites now return
one `duration_breakdown` in 79a's shape; `accounted_s + unaccounted_s` reconciles;
the payload is flat from 2 to 500 fields; no bridge call was added; and the
per-field acquisition span exists. Suite **3274 passed, 99 skipped**.

Also settled, and it is why this gate is smaller than the block's assignment
implied: **the repaint coalescing was removed.** No multi-field tool can
authorize a hook restoration, so the per-field repaint never fired — see
"Block 79c-1, closed" in `design/79-fastest-correct-execution.md` and `R123`.
There is nothing about repaints to gate.

What a fake cannot answer is whether the breakdown *measures anything useful on
a real engine*. Locally the `Acquisition` stand-in exits synchronously; on the
real engine `__exit__` is where `mark_finished` and `await_completion` happen,
and block 75a measured that at **96.7% of a one-frame acquisition's window on
this machine, 158.8 ms p50**. So the acquisition span should be almost the whole
of each field's cost, and the residual should be the stage motion between fields.
That is the shape this gate reads.

**Limb 6 is a MEASUREMENT, not a criterion.** It reports what a grid's time is
spent on, matched across shapes that differ in how many `Acquisition` objects
they construct. It is excluded from the score deliberately: design/79 forbids
shipping a wall-clock threshold, and a gate that failed on one would be measuring
this machine's mood. It is also the number 79c's brief asked for and could not
get — nobody has measured whether the per-acquisition cost multiplies on a grid.

## 1 — pin the tree

```powershell
cd D:\Code\microclaw
git fetch origin
git checkout design79/the-per-field-multiplier
git pull
git merge-base --is-ancestor 7d7e754 HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - the implementation is in this tree" }
else { "STOP - wrong tree, do not run the gate" }
```

Warm uv once, unredirected. A freshly checked-out branch leaves uv a rebuild and
it writes `Building microclaw @ file:///...` to stderr; red text here is expected
and harmless because nothing is redirected.

```powershell
uv run python -c "print('uv warm')"
```

Check the instrument before spending any time on the engine:

```powershell
uv run python -m pytest -q design\79-block79c1-demo-gate-selftest.py
```

Expect `11 passed`. Every limb in the gate is exercised there against a payload
shaped like the product's **and** against a mutant that breaks exactly that limb,
so a green selftest means the limbs discriminate. If it fails, stop and send me
the output.

## 2 — start Micro-Manager

Start Micro-Manager with the **demo configuration** and the ZMQ server. Nothing
else: no safety-config edits, no channel group, no sample, no real stage. The
gate needs a camera, an XY stage and a configured workspace, and limb 0 reports
NOT EXERCISED — which is never a pass — if any is missing.

## 3 — run the gate

```powershell
New-Item -ItemType Directory -Force block79c1-evidence | Out-Null
uv run python design\79-block79c1-demo-gate.py --out block79c1-evidence > block79c1-evidence\gate.log 2>&1
"exit: $LASTEXITCODE"
Get-Content block79c1-evidence\gate.log
```

That is the whole gate. It prints one line per limb as it goes, writes
`score.json`, `measurement.json` and one `payload-*.json` per run, and exits
nonzero on any FAIL or NOT EXERCISED. It runs six grids of 8 fields and two of
2 and 24 fields, one frame each at 10 ms — about 90 exposures of demo camera in
total.

Expected: **5/5 PASS (1 measured)**, exit 0.

## 4 — send back

The whole `block79c1-evidence` folder. The scoring is mine and it is done from
the artifacts, not from the exit status: `measurement.json` and the
`payload-*.json` files are the evidence, `score.json` is a summary of it.

If the gate exits nonzero, send the folder anyway — a FAIL with its payload is
more useful than a re-run.

## What I will be looking at, so you know what matters

- `hookless-zero` constructs **8** acquisitions for 8 fields and `hooked-zero`
  constructs **1**, and the breakdown says so. If those agreed, the breakdown
  would not be measuring the route the run took; limb 3 fails on it.
- Whether `acquisition.total_s` for the 8-acquisition shape is materially larger
  than for the 1-acquisition shape at the same frame count. If it is, 75a's
  per-acquisition cost multiplies on a grid and `R105`'s family gets a number. If
  it is not, that is equally a result and the row says so.
- Whether the residual is the stage motion. On the demo stage the moves are
  nearly free, so a small residual here is expected and is **not** evidence about
  a real stage — a matching run on M2 or the Nikon is what would settle that, as
  a passenger, and this gate does not ask for one.
