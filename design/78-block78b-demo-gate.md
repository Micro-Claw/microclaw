# Block 78b — demo-machine gate: does a real engine batch where we predict?

Branch `design78/sequencing-predicate`. No sample, no dose, no hardware: this
runs on the demo camera and writes nothing to any real device.

## What is already settled, and what this gate is for

design/78 settled the arithmetic off-rig, from `AcqEngJ-0.39.4`'s bytecode:
`AcquisitionEvent.fromJSON` truncates `min_start_time` to `Long` milliseconds
with `d2l`, and `Engine.isSequencable` refuses a differing-t-index pair **only**
when those deadlines differ. So two consecutive frames sequence exactly when

    int(k * interval_s * 1000.0) == int((k + 1) * interval_s * 1000.0)

This gate is the half the bytecode cannot answer: **does the running engine
actually batch where that predicts?**

The limb that matters is `0.001 s x 4008 frames`. Every short run at 0.001 s is
clean, which is exactly what makes a "use at least 1 ms" rule look correct; the
prediction is that frames 4006/4007 collide anyway, because
`4007 * 0.001 * 1000` is `4006.9999999999995`. I verified off-rig that this gate
can tell those apart: run against a fake engine that batches by the truncation
rule it passes every limb, and against one that batches by a 1 ms threshold it
fails **only** the 4006/4007 limb. **If you skip the long run, this gate does
not test the block's central claim** — and it reports that limb NOT EXERCISED,
which exits nonzero.

## 1 — pin the tree

```powershell
cd D:\Code\microclaw
git fetch origin
git checkout design78/sequencing-predicate
git pull
git merge-base --is-ancestor fdfb63d HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - the implementation is in this tree" }
else { "STOP - wrong tree, do not run the gate" }
```

Warm uv once, unredirected. A freshly checked-out branch leaves uv a rebuild and
it writes `Building microclaw @ file:///...` to stderr; red text here is
expected and harmless because nothing is redirected.

```powershell
uv run python -c "print('uv warm')"
```

Check the instrument before spending any time on the engine:

```powershell
uv run python -m pytest -q design\78-block78b-demo-gate-selftest.py
```

Expect `7 passed`. If it fails, stop and send me the output.

## 2 — start Micro-Manager

Start Micro-Manager with the **demo configuration** and the ZMQ server. Nothing
else is needed: no safety config edits, no channel group, no sample, no stage.

## 3 — run the gate

```powershell
New-Item -ItemType Directory -Force block78b-evidence | Out-Null
uv run python design\78-block78b-demo-gate.py --out block78b-evidence --long-run
"exit: $LASTEXITCODE"
```

That is the whole gate. It prints one line per limb as it goes, writes
`score.json` and `callback-shapes.json`, and exits nonzero on any FAIL or any
NOT EXERCISED. Expect `exit: 0` and `8/8 PASS`.

`--long-run` acquires ~4008 frames at 1 ms exposure on the demo camera. If that
takes unreasonably long on this machine, stop it, tell me how far it got, and
re-run without `--long-run` — but note that the gate will then correctly report
its central limb NOT EXERCISED and exit nonzero, and I will not read that as a
pass.

## 4 — send back

The whole `block78b-evidence` folder. `callback-shapes.json` is the raw
observation everything is scored from — the size of every hook callback in every
run — and I will re-score it rather than trusting `score.json`.

## What each limb means

| Limb | Reads |
|---|---|
| control: 50 ms spacing is NOT batched | If a well-spaced run batches, this engine does not behave as the gate assumes and nothing else means anything |
| 0.0001 s: the engine batches | The mechanism itself — this is M5's incident shape, reproduced with no hardware |
| 0.001 s over 8 frames is NOT batched | The observation a "1 ms is safe" rule would be built on |
| interval_s = 0 is batched | design/77b's finding, arriving from the Java side |
| **0.001 s collides at frames 4006/4007** | **Why the block states a predicate and refuses to state a threshold.** The only limb that separates the two models |
| a colliding hardware-control run is refused at plan time | The product change: refused before any `Acquisition` is constructed |
| a well-spaced hardware-control run is NOT refused | The control — a refusal that always fires is not a criterion. It requires the run to **reach the acquisition**, because "the sequencing refusal did not fire" is also true of a run that died earlier for an unrelated reason |
| the run asked to authorize hook hardware control | This block must not have removed the operator's authorization of hook hardware writes |

## If the 4006/4007 limb fails

Report it; do not adjust anything. A first burst at a different frame means the
arithmetic model is incomplete for this engine, which is a finding about the
engine and worth more than a green gate. The refusal is conservative either way:
it refuses a superset of what batches, so a wrong prediction costs a needlessly
refused run, never an unguarded one.
