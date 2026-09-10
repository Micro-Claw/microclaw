# design/79's M2 close-out — the two things only an EMU rig can measure

Branch `design79/m2-close-out`. **This is a measurement, not a criteria gate.**
Two of its three limbs report MEASURED and are excluded from the score; the only
thing that can fail is whether the runs happened at all.

**Dose: yes.** M2's camera triggers the lasers, so these runs fire light — about
120 frames at 50 ms across the whole gate. Operator authorised this on
2026-09-10. **No sample is needed**; nothing here looks at the image content.

## Why this run exists, and what it is not

design/79's 79c brief named two residuals. One is now measured: block 75a's
per-acquisition span multiplies across a grid, **6.5× and 7.9×** on the demo
machine, n=2. The other is `R105`, which **has never been measured on any
machine**: M2's frame gaps were ~0.20–0.35 s at 50 ms exposure in the 2026-09-04
session, and block 78a's gate was booked to measure what dispatch still costs
after its fix and could not, because all three of its arms requested
`interval_s = 0.5` — which hides any residual below half a second.

**It deliberately does not re-score block 79c-1's five structural criteria.**
They passed on the demo machine twice; a third confirmation would buy nothing and
is the over-building that block's own close-out records.

**It does not use 78a's CoreLog gate, which `R105`'s row nominates.** That scorer
reads exposure markers out of the log, and its own docstring says a *sequenced*
run yields one marker per burst — so its cadence limb would report n=1 for
exactly the short-interval run `R105` needs. microclaw's own
`inter_frame_gap_summary` is measured per saved frame, is on a sub-microsecond
clock since block 79c-2, and needs no log. It is already in the result.

## 1 — pin the tree

```powershell
cd D:\Code\microclaw
git fetch origin
git checkout design79/m2-close-out
git pull
git merge-base --is-ancestor 3be2cb5 HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - the instrument is in this tree" }
else { "STOP - wrong tree, do not run the gate" }
```

Warm uv once, unredirected — a freshly checked-out branch leaves it a rebuild and
the `Building microclaw @ file:///...` on stderr is expected and harmless here.

```powershell
uv run python -c "print('uv warm')"
```

Check the instrument before spending any dose:

```powershell
uv run python -m pytest -q design\79-m2-closeout-selftest.py
```

Expect `7 passed`. Every limb is driven end to end there against a stub rig,
including a no-bridge rig, an arm that saves no frames, a refusing tool and a
raising one. If it fails, stop and send me the output.

## 2 — the free one, which needs no Micro-Manager at all

```powershell
uv run python design\79-clock-resolution-probe.py > block79-m2-evidence-clock.txt 2>&1
Get-Content block79-m2-evidence-clock.txt
```

Run this **before** starting MM if convenient. It makes `R128` two machines
instead of one: the demo machine measured `time.monotonic()` as `GetTickCount64`
stepping 16 ms, and `perf_counter` as `QueryPerformanceCounter` at 0.1 µs. M2
should agree, since that is a property of the interpreter and OS rather than the
rig — and if it disagrees, that is worth more than everything else here.

## 3 — start Micro-Manager

M2's **own** configuration and the ZMQ server, and its usual safety config. Not
the demo config: the point is a real stage and a real camera.

**If M2's safety config sets a `workspace_dir`**, create the evidence folder
inside it and pass that path to `--out`. The product does not require a workspace
and neither does this gate, but if one *is* configured, a path outside it is
refused with a `SafetyViolation` naming the workspace.

## 4 — run it

```powershell
New-Item -ItemType Directory -Force block79-m2-evidence | Out-Null
uv run python design\79-m2-closeout-gate.py --out block79-m2-evidence > block79-m2-evidence\gate.log 2>&1
"exit: $LASTEXITCODE"
Get-Content block79-m2-evidence\gate.log
```

Expected: **1/1 PASS (2 measured)**, exit 0. It runs three 20-frame hooked
timelapses at 50 ms — requested intervals 0 s, 0.06 s and 0.5 s — and then a
6-field grid twice, once per-field and once shared-dataset.

PowerShell will render microclaw's acquisition event sink as red error records
inside the log. That is the known native-stdout behaviour, not a failure.

If you are short of time, `--skip-multiplier` runs the `R105` arms only and still
exits 0 — the multiplier limb reports NOT EXERCISED and, being a measurement
rather than a criterion, does not fail the gate.

## 5 — send back

`block79-m2-evidence` and `block79-m2-evidence-clock.txt`. Scoring is mine and
from the artifacts: `cadence.json`, `multiplier.json` and the `payload-*.json`
files are the evidence, `score.json` is a summary of it.

## What I will be looking at

- **`R105`, at last**: the achieved median gap in the 0 s and 0.06 s arms. The
  0.5 s arm is 78a's blind control and should come back at ~0.5 s, reporting the
  *request*; if the short arms also report ~0.5 s something is wrong with the run
  rather than with the rig.
- **Whether the ratio moves on a real stage.** The demo stage is nearly free, so
  M2's residual half should be larger and the ratio and the ~2% exposure share
  should both shift. Either direction is a result.
- **Whether the spans are sub-millisecond here too**, which closes `R128` on a
  second machine.
- n is 1 per arm. Nothing here becomes a rate.
