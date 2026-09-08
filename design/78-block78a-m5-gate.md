# Block 78a — rig gate: does removing the per-write refresh remove the seconds?

**Runs on M5 or M2. Not on the demo machine — see "Which rig" below.**

Branch `design78/no-per-frame-refresh`. This gate answers one question with
three otherwise-identical runs: **no write**, **write with the per-frame GUI
refresh** (today's `main`), **write without it** (this branch).

Everything computational is in `design/78-block78a-m5-gate.py`. You run three
acquisitions and then one script. The script reports every limb independently,
never lets one refusal hide the others, and exits nonzero on any FAIL **or** any
NOT EXERCISED. `NOT EXERCISED` is never a pass.

## Which rig

This gate needs a rig whose **EMU listeners fan out on the GUI refresh**. That is
what costs the seconds, and a machine without it has nothing for this block to
have removed.

- **M5** — where design/78's evidence was taken. Debug logging was already on
  there on 2026-09-04.
- **M2 — equally valid, and in one way better.** It runs the same EMU/htSMLM
  stack and its 2026-09-04 session wrote the *identical* pair, `Laser Trigger` /
  `Duration0 (us)`, approved interval 10–50000 µs, with the same symptom:
  3.47–3.57 s mean frame gaps at 50 ms exposure. design/78 could only say M2's
  ~2.95 s silent periods were *consistent with* M5's fan-out, because M2's
  CoreLog was at IFO level and hides device reads. This gate turns debug logging
  on, which closes exactly that gap — and makes the fan-out attribution **n=2
  across two rigs** instead of one rig's story.
- **Not the demo machine.** No EMU means the repaint is nearly free, so the
  control limb — "the with-refresh arm reproduces the fan-out" — reports NOT
  EXERCISED and the gate exits nonzero. That is the gate working correctly:
  with no fan-out there is nothing to prove was removed. The correctness half
  (no repaint between write and exposure, one read-back per write) is already
  covered by the unit suite, so a demo run would add little and score nothing.

Everything below is written to be rig-agnostic. The device and property are
arguments in `arms.json`, and the probe finds the CoreLog path from Core rather
than assuming an install directory, so the same commands work on either machine.
On M2 use its own exposure (50 ms in the 2026-09-04 session); on M5, 100 ms.

## What this gate is measuring, in one paragraph

On 2026-09-04 a hook writing `Laser Trigger` / `Duration0 (us)` before each
frame cost ~3.2 s per frame on M5. The write itself was 39 µs and the camera
snap 149 ms. The rest was `refresh_gui()`: MMStudio's repaint took 116 ms and
then EMU's listener fan-out continued **on the calling thread** for 2.757 s,
reading a dozen unrelated devices over serial. This block removes that per-write
refresh and coalesces one refresh at teardown. The open risk, which only this
gate can settle: **if EMU's fan-out is also driven by MMCore's own
property-changed callback rather than only by the GUI refresh event, removing
our call relocates the work instead of deleting it.** The scorer therefore
counts EMU retrievals per thread, not in total.

## Dose — read before you start

`Duration0 (us)` is the **UV/405 pulse length**. Writing it is a real dose, and
these are three runs of the same acquisition. This is true on M2 as well as M5 —
M2's camera triggers its lasers, so live view is a dose there too. Treat this as any other
acquisition against your dose envelope:

- Prefer a sacrificial or blank sample. Nothing in this gate needs a good one —
  the measurement is timing, not image content.
- Keep live view off outside your own setup; live view is a dose on M5.
- If you would rather not spend 405 at all, run the whole gate on the **second
  authorized property** (below) and skip the `Duration0` arms. The block is
  generic; `Duration0` is only the property design/78 happened to measure, and
  a non-illumination EMU property exercises the same code path. Say in your
  report which property each arm used — the scorer records it either way.

## 0 — pin the tree

Open PowerShell in the existing M5 Microclaw checkout.

```powershell
Set-Location (git rev-parse --show-toplevel)
git fetch origin
git checkout design78/no-per-frame-refresh
git pull
git merge-base --is-ancestor 29d9fd7 HEAD
if ($LASTEXITCODE -eq 0) { "PIN OK - the implementation is in this tree" }
else { "STOP - wrong tree, do not run the gate" }
```

Warm uv once, unredirected — a freshly checked-out branch leaves uv a rebuild
and it writes `Building microclaw @ file:///...` to stderr. Red text here is
expected and harmless because nothing is redirected.

```powershell
uv run python -c "print('uv warm')"
```

Confirm the scorer runs on this machine before you spend any dose:

```powershell
uv run python -m pytest -q design\78-block78a-m5-gate-selftest.py
```

Expect `13 passed`. If that fails, stop and send me the output — the instrument
is broken and no rig time should be spent.

## 1 — start Micro-Manager once, with debug logging, and leave it up

Start Micro-Manager with this rig's ordinary htSMLM/EMU configuration and the
ZMQ server. **Do not restart it between arms.** All three arms share one
CoreLog, which is what lets the scorer compare them on one clock.

Debug logging must be on or this gate measures nothing. Do not hunt for a menu
item:

```powershell
uv run python design\78-block78a-m5-probe.py --set-debug on
```

It prints four lines. Record `CORELOG` — you need it in step 3 — and
`debug_log_enabled_was`, because step 4 puts it back. It also prints
`refresh_in_source` for the checkout you are on, which step 3 needs per arm.

## 2 — the three arms

Start Microclaw with this rig's reviewed safety configuration. **Do not edit the
production safety configuration for this gate.** M5 has no `Channel` config
group; pass no channel and do not invent one on either machine.

Run the same acquisition three times. Keep frames, exposure, ROI and interval
identical across all three — the comparison is worthless otherwise. Before each
arm, note the wall-clock time; after each arm, note it again. Those two
timestamps are the arm's window.

Give the connected agent this prompt for **arm A (no write)**, verbatim:

> Run a 10-frame timelapse with `interval_s=0.5` and the exposure this rig is
> already set to, saving to a new directory, with a hook attached that only
> observes — no property writes, no stage motion, no illumination changes. Do
> not set up any property envelope. Report the dataset path and the run's
> `started_at` and `completed_at`.

Then check out `main` for **arm B (write, with refresh)** — this arm must run
the *old* code, because that is the behaviour being compared against.

`main` is the right baseline **because this branch has `main` merged into it**,
so arms B and C differ by 78a's change and nothing else. That was not true for a
few hours after block 78b merged, when this branch still predated it; if you
ever find `git merge-base --is-ancestor main HEAD` failing on this branch, stop
and tell me, because the comparison is void.

```powershell
git checkout main
git pull
uv run python -c "print('uv warm')"
```

Give the agent this prompt, verbatim, substituting nothing:

> Run the same 10-frame timelapse at `interval_s=0.5` and the same exposure and
> save directory pattern as the previous run, but this time attach a fixed
> per-frame `hook_action_plan` that sets `Laser Trigger` property
> `Duration0 (us)` before each frame, cycling the values 0, 100, 200, 300, 400
> and repeating. Request the property envelope this needs and tell me the exact
> bounds you are asking me to approve before I approve anything. Report the
> dataset path, `started_at`, `completed_at`, and the hook log path.

Then return to the branch for **arm C (write, no refresh)**:

```powershell
git checkout design78/no-per-frame-refresh
git pull
uv run python -c "print('uv warm')"
```

Give the agent the **same prompt as arm B**, word for word.

> **A second property, for generality.** design/78 asks for one. After arm C,
> repeat arm C once more against a different authorized property on this rig —
> ideally one that is not illumination. Record it as a fourth arm named
> `write-without-refresh-second-property`; the scorer will measure it and report
> it, and it does not gate the run.

## 3 — score it

Write the arm windows into a JSON file. Times are local, matching the CoreLog's
own clock, and the windows should be **tight** around each run — a window that
spans the idle time between two arms drags that idle gap into the cadence
distribution.

```powershell
New-Item -ItemType Directory -Force block78a-evidence | Out-Null
notepad block78a-evidence\arms.json
```

```json
[
  {"name": "no-write",
   "start": "2026-09-08T00:00:00", "end": "2026-09-08T00:00:00",
   "device": "Laser Trigger", "property": "Duration0 (us)",
   "commit": "PASTE git rev-parse --short HEAD FOR ARM A",
   "refresh_in_source": true},
  {"name": "write-with-refresh",
   "start": "2026-09-08T00:00:00", "end": "2026-09-08T00:00:00",
   "device": "Laser Trigger", "property": "Duration0 (us)",
   "commit": "PASTE ARM B COMMIT", "refresh_in_source": true},
  {"name": "write-without-refresh",
   "start": "2026-09-08T00:00:00", "end": "2026-09-08T00:00:00",
   "device": "Laser Trigger", "property": "Duration0 (us)",
   "commit": "PASTE ARM C COMMIT", "refresh_in_source": false}
]
```

`refresh_in_source` is not decoration: the scorer **fails** the run if the two
write arms claim the same value, because a gate that scored the same build twice
would report a clean improvement of zero and look fine. Get it from the tree
each arm actually ran, not from memory — run this on each checkout **before**
its arm and paste the printed value in:

```powershell
uv run python design\78-block78a-m5-probe.py --show
```

Then:

```powershell
uv run python design\78-block78a-m5-gate.py `
    --corelog "PASTE THE CORELOG PATH FROM STEP 1" `
    --arms block78a-evidence\arms.json `
    --out block78a-evidence
"exit: $LASTEXITCODE"
```

## 4 — put the machine back

```powershell
uv run python design\78-block78a-m5-probe.py --set-debug off
```

**Only if step 1 printed `debug_log_enabled_was True`, skip this** — debug
logging was already on for your own reasons and turning it off would change
state this gate does not own. Also confirm the stage and `Duration0` are where
you expect before the next user; the runs restore to their entry values, and the
hook log records what they were.

## 5 — send back

The whole `block78a-evidence` folder — `score.json`, the per-arm JSON files —
**plus the CoreLog itself** and the four hook logs and history JSONL. The
CoreLog is the artifact everything here is scored from, and I will re-score it
rather than trusting `score.json`.

## What each limb means

| Limb | Reads |
|---|---|
| CoreLog is at debug level | Without `[dbg,...]` lines the log cannot show device reads and every fan-out limb below is vacuous |
| the two write arms ran different trees | `refresh_in_source` differs; otherwise the comparison is void |
| control: the with-refresh arm reproduces the fan-out | **If this is NOT EXERCISED the gate measured nothing** — this rig did not reproduce design/78's condition and arm C proves nothing |
| no GUI update between a write and its exposure | The direct effect of the change |
| the read fan-out is gone, on every thread | The open risk: did the work go away, or move to another thread? |
| exactly one read-back of the target per write | The duplicate `get_property` is gone |
| cadence reported for all three arms | The residual, **reported as measured** |

design/78's ~0.3 s/frame figure extrapolates a single cycle. It is a hypothesis
to compare against and **not a pass/fail threshold**. The scorer prints the
three medians and does not subtract them.
