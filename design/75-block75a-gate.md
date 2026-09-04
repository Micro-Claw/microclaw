# Block 75a gate — D4's record, written where a restart cannot lose it

Two parts, on two machines, and they are independent — run them in either order
and report whichever you get to.

| part | machine | budget | what it settles |
|---|---|---|---|
| 1 | **Windows demo machine** | about **5 minutes** | that the record is complete, ordered, correlated, and survives the process ending |
| 2 | **M2** | about **3 minutes**, 40 exposures | the healthy one-frame latency that block 75b's two constants come from |

Both are **programs**, not steps you judge. Each limb scores itself
independently, one FAIL cannot hide the rest, each owns its own log, and each
exits nonzero on any FAIL **or** any NOT EXERCISED. `NOT EXERCISED` is never a
pass — if a limb reports it, say so rather than ticking it.

Both have already been run against a bridge-shaped fake
(`design/75-block75a-gate-selftest.py`, eight cases, four of them deliberate
failures) on **both** trees: all eight hold on this branch, and on `main` every
case fails at limb E. So they discriminate before they reach you. That selftest
found five defects in the gate and none in the product, including one that would
have cost part 1 a whole round.

## What this gate is and is not for

Almost all of D4 is settled off-rig — the bounded writer queue, the coalescing,
the flush grace, the correlation-id plumbing and both entry points' filenames
all have tests, each verified by mutation during review. This gate deliberately
carries only what a fake cannot answer:

- **The order real pycro-manager fires `image_saved_fn` in**, relative to
  `__exit__` returning. Every test in the suite drives a fake `Acquisition` and
  has never observed it, and the incident turns on exactly that thread.
- **How promptly a lifecycle record reaches the disk**, which is what decides
  whether an abruptly closed MicroClaw leaves a record behind at all.
- **What a healthy one-frame acquisition costs**, on the rig that failed.

There is **no browser session in this gate**. Block 75b's gate needs a browser
anyway for D2's structured result, and it can carry the `agent.py` handoff then;
asking for your session time twice for a one-line pass-through that already has
a test is not worth it.

---

# Part 1 — the demo machine

## 0. Prerequisite

**Micro-Manager open**, with this machine's usual demo config loaded and the
pycro-manager bridge running — "Run server on port 4827" checked in
Tools → Options. Same link every probe on this machine uses.

**Do not pass `--safety-config`,** and do not edit one. This gate requires no
configuration the product does not require: it puts its datasets under the
machine's configured `workspace_dir` if there is one and under its own evidence
folder if there is not. (design/60 block 60b's gate made the operator edit a
production safety config and then reported six limbs NOT EXERCISED for a reason
that said nothing about the code.)

## 1. Pin the tree

```powershell
cd D:\Code\microclaw
git fetch origin
git checkout design75/persistent-acquisition-diagnostics
git pull
git merge-base --is-ancestor 671d8fe HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - the implementation is in this tree" }
else { "STOP - wrong tree, do not run the gate" }
```

**Warm uv once, unredirected.** A freshly checked-out branch leaves uv a rebuild
and it writes `Building microclaw @ file:///...` to stderr; under
`$ErrorActionPreference = 'Stop'` that terminates any *redirected* uv command.
Red build text here is expected and harmless — nothing is redirected.

```powershell
uv run python -c "print('uv warm')"
```

## 2. Run the gate

```powershell
New-Item -ItemType Directory -Force block75a-evidence | Out-Null
uv run python design\75-block75a-demo-gate.py --out block75a-evidence --log block75a-evidence\score.json
"exit: $LASTEXITCODE"
```

It acquires 23 single frames plus one 400-frame run on the demo camera — about
21 seconds of exposure on a camera whose trigger drives nothing — and spawns two
short child processes. Nothing moves a stage.

Its seven limbs:

- **0 — bridge, camera, save root.** Stands the rest down if Micro-Manager is
  unreachable or has no camera, rather than failing six limbs for one reason.
- **A — the real lifecycle order.** One acquisition must write all six record
  kinds, **in order**, with monotonic timestamps, the correlation id on every
  one, and the dataset path, planned frames, estimated bytes and active bound on
  the construction record. This is the limb that needs hardware: if real
  pycro-manager delivers a saved-frame callback *after* `mark_finished`, this
  limb fails and the suite's central assumption is wrong. It also compares the
  file's own span against the call's measured wall time — two independent
  measurements of one quantity, which should agree.
- **B — the one-frame reference latency.** 20 acquisitions, reporting
  end-to-end and finalization p50/p95/max. **The numbers are a measurement, not
  a criterion**; the limb only fails on a missing or incomplete record. These
  are the demo machine's numbers and they do **not** set 75b's constants — part
  2 does.
- **C — normal shutdown flushes the tail.** A child process runs one
  acquisition and exits through the same `close()` every entry point calls, and
  its last record must be on disk.
- **D — an abrupt end still leaves a record.** The one that matters. A child
  starts a long acquisition; the gate waits until that call's submission record
  is readable on disk, then ends the process **mid-acquisition**. What must
  survive is the completed call's full lifecycle plus the interrupted call's
  beginning with no completion — a call that started and never finished, which
  is exactly the shape the incident left behind and exactly what MicroClaw had
  no record of. It also reports how long that record took to become readable.
- **E — the control that fires.** Asserts the running build actually carries
  D4. It is what makes step 3 meaningful.
- **F — a real session's join, if this machine has one.** If any
  `*_microclaw_history.jsonl` in the working directory has an `_acquisitions`
  sibling, every recorded `tool_call_id` must name a `tool_use` in that
  transcript. **NOT EXERCISED if there is no such pair, and that is expected on
  a machine that has not yet run a session on this branch** — it is free
  evidence, not a required setup step. Do not create one for it.

## 3. The control arm, on this machine

Two minutes, and it is what proves the gate could have failed here.

```powershell
Copy-Item design\75-block75a-demo-gate.py block75a-evidence\gate-copy.py -Force
git checkout main
uv run python -c "print('uv warm')"
uv run python block75a-evidence\gate-copy.py --out block75a-evidence\control --log block75a-evidence\control-score.json
"exit: $LASTEXITCODE"
git checkout design75/persistent-acquisition-diagnostics
```

The copy is because the gate does not exist on `main`; `uv run` still resolves
`microclaw` from the checked-out tree, which is the build under test.

**Expected on `main`: limb E FAIL and every other limb NOT EXERCISED**, with
the reason naming the three missing `execute_tool` arguments. Any other shape
means the gate is not measuring what it claims — report it rather than working
around it.

## 4. What to send back from part 1

- `block75a-evidence\gate.txt` and `score.json`
- `block75a-evidence\control-score.json`
- the two `*_microclaw_acquisitions.jsonl` files from limbs A and B, and the two
  from limbs C and D
- the `exit:` lines

---

# Part 2 — M2

This is the only part that needs a booked rig, and it is short.

**Dose, stated plainly:** with the defaults it fires the arm's laser
`2 × 20 × 50 ms` — 40 exposures, about **2 seconds** of total illumination,
because the camera trigger fires the lasers on M2 and so the snaps count too.
Nothing sweeps a stage or moves an objective. Put something expendable in the
field, or a sample you are already done with; the images are never looked at,
only their timing.

## 0. Prerequisite

Micro-Manager open with the M2 config, ZMQ server on port 4827 as usual, and a
writable dataset directory. Laser slot 3 is the incident's own slot — if it is
not usable today, pass `--laser-slot -1` to omit it and say so in the report;
the timing is still worth having.

## 1. Pin the tree

```powershell
cd C:\Users\<you>\Code\microclaw
git fetch origin
git checkout design75/persistent-acquisition-diagnostics
git pull
git merge-base --is-ancestor 671d8fe HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - the implementation is in this tree" }
else { "STOP - wrong tree, do not run the arm" }
uv run python -c "print('uv warm')"
```

## 2. Run the arm

```powershell
New-Item -ItemType Directory -Force block75a-m2 | Out-Null
uv run python design\75-block75a-m2-latency.py --save-dir F:\DataSSD\block75a --out block75a-m2
"exit: $LASTEXITCODE"
```

It reproduces the failing call's parameters exactly — `n_frames=1`,
`interval_s=0`, `exposure_ms=50`, `laser_slot=3`, unique dataset names, and a
`snap_and_analyze` immediately before each one, because that is the shape the
session that failed actually had. It prints, and writes to
`block75a-m2\m2-latency.json`:

- **end-to-end** latency per call — construction to teardown completion —
  as min/p50/p95/max;
- the **finalization** segment broken out, `mark_finished` to teardown
  completion;
- the call's own measured wall time, independently of the file, so the two can
  be checked against each other.

**It sets no constant, and neither should the report.** design/75 asks for at
least 10× headroom over the maximum healthy end-to-end latency; the coordinator
records the measured maximum in design/75 and block 75b chooses from it, so the
measurement and the choice are not made by the same pass.

The observed failure rate was 1 in 14, so **one of these 20 calls may hang.**
That is a result, not a spoiled run: if the spinner stops coming back, let it
sit, note the time, and send whatever the JSON and the `_acquisitions.jsonl`
contain. A hang here with a record behind it is worth more than 20 clean runs.

## 3. What to send back from part 2

- `block75a-m2\m2-latency.json` and `m2-latency.txt`
- `block75a-m2\*_m2_microclaw_acquisitions.jsonl`
- the `exit:` line, and whether laser slot 3 was used

---

## And one thing that is not a limb

If you still have them, the three facts from design/75 §"Evidence still owed"
would size 75b's constants better than any of the above: roughly how long the
spinner was up before you closed MicroClaw, whether the browser ever showed
`frames 1 / 1` for that call, and whether
`260904_AA_CLC-SNAP_AF647_microclaw_TIRFsweep_fine_4um` exists on `F:\DataSSD`
with one entry in its `NDTiff.index`. None of them blocks this gate.
