# Block 64d — M2 rig gate runbook

The gate is a **program**, not a list of steps you judge: every limb only
computes, scores itself independently, and exits nonzero unless all of them
PASS. Your part is three things a program cannot do — put a sample in the field,
physically block an axis, and put the rig back afterwards.

`NOT EXERCISED` is never a pass. If a limb reports it, say so rather than
ticking it.

## Step 0 — check out the branch and confirm you have the code under test

```powershell
cd C:\Users\<you>\Code\microclaw
git fetch origin
git checkout design68/xy-arrival-contract
git pull
git merge-base --is-ancestor 33828a6 HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - the implementation is in this tree" }
else { "STOP - wrong tree, do not run the gate" }
```

Start Micro-Manager, load the M2 config, and start the ZMQ server as usual.
Put a **bead field or another sample with visible puncta** in the field and
focus it — slightly defocused so the beads read as blobs is ideal, and is what
`gate64-m2` used. Limb D needs something to centre.

## Step 1 — the computed limbs

Run exactly this. It moves the stage by 20 µm and by 200 µm and returns it each
time; it does not acquire.

```powershell
uv run python design/68-gate-probe.py --out gate68-m2 > gate68-m2-run1.log 2>&1
"exit code: $LASTEXITCODE"
Get-Content gate68-m2-run1.log
```

Expect `5/6 PASS` with `C_non_response_control` reporting NOT EXERCISED — that
limb is Step 2. Anything else, stop and send the log.

If limb D reports NOT EXERCISED for "no punctum in this field", move to a field
with beads in it and rerun just that limb:

```powershell
uv run python design/68-gate-probe.py --out gate68-m2 --limbs D > gate68-m2-runD.log 2>&1
"exit code: $LASTEXITCODE"
```

## Step 2 — the control limb, and the reason for the trip

**This is the limb that matters most, and it is the one that needs your hands.**
Block 66's equivalent limb found a real product defect by failing; a control
that cannot fire proves nothing about the ones that pass.

Make **one** axis unable to move, whichever is practical on M2:

- disconnect the stage controller's serial cable, **or**
- power the controller down, **or**
- engage a hard stop / joystick lock on one axis

Then run:

```powershell
uv run python design/68-gate-probe.py --out gate68-m2 --limbs C --control > gate68-m2-control.log 2>&1
"exit code: $LASTEXITCODE"
Get-Content gate68-m2-control.log
```

The limb commands motion on **both** axes on purpose, so it does not matter
which one you blocked.

What each outcome means, so you do not have to judge it:

| It says | Meaning |
|---|---|
| PASS | the blocked axis was refused by name, with a typed error carrying `start_um`. This is what we want. |
| FAIL, "raised `<something>`, not a typed XYStageMoveError" | a real product defect — exactly what block 66 found. Send the log. |
| FAIL, "a blocked axis reported a successful arrival" | either the axis was not really blocked, or the contract is wrong. Say which you think it was. |
| NOT EXERCISED | the limb could not reach the product. Never a pass, and never a product defect either. |

**Disconnecting the Core XY device entirely is the best version of this test,
not a degenerate one.** A link that is down fails *every* bridge call, including
the position read — so the refusal legitimately carries `start_um: null`, and
the limb says so rather than faulting it. Blocking a readable axis is the other
valid shape and is scored differently; both PASS.

Then **restore the cable / power / lock** and confirm the stage moves again:

```powershell
uv run python design/68-gate-probe.py --out gate68-m2-after --limbs 0 > gate68-m2-restore.log 2>&1
"exit code: $LASTEXITCODE"
```

## Step 3 — send the evidence

Send the whole `gate68-m2` folder (and `gate68-m2-after`) plus the three `.log`
files. The JSON is what gets scored, not the PASS/FAIL line — every limb writes
its own numbers, and the scoring reads those.

## What this gate is NOT for

The per-axis band arithmetic is settled off-rig, by tests that are watched
failing with the band check deleted. Do not spend rig time on it. What needs M2
is whether a real asynchronous stage read over a real bridge is **out-waited**
rather than sampled once, and whether a genuine non-response is refused instead
of reported as an arrival.

## Before this shipped

`design/68-gate-probe-selftest.py` drives the whole probe against a bridge-shaped
fake whose XY stage takes real time to arrive and answers `device_busy` False
throughout. It runs in five modes and each one must come out a specific way, so
the gate cannot quietly stop discriminating later:

```
python design/68-gate-probe-selftest.py                    # 0/A/B/D/E PASS, C stands down
python design/68-gate-probe-selftest.py --shared-band      # limb B must FAIL
python design/68-gate-probe-selftest.py --blocked-axis     # limb C must PASS, naming Y
python design/68-gate-probe-selftest.py --untyped-failure  # limb C must FAIL, not crash
python design/68-gate-probe-selftest.py --dead-stage       # limb 0 stands the rest down
```

It found one gate defect that would have wasted this trip: limb C commanded a
move along **X only**, so an operator who blocked Y would have had the limb
report "a blocked axis arrived" — a confidently wrong verdict about a correctly
blocked axis. It now commands both.
