# design/32 Block 4 — acquisition-budget rig gate prompts

Gate for `design32/acquisition-budgets` (impl `a8f0209` + `8d7e832`, coordinator fix
`01b2766`). The branch is pushed; **do not merge until this gate passes.**

Everything here is designed to run **dark or at minimum exposure**. No step needs a
sample, and no step should be run with a sample you care about. Keep frame counts
small; the point is to exercise the accounting, not to acquire anything.

Keep one dated evidence directory for the whole gate: commands, stdout/stderr,
environment identity, config files used, artifacts, hashes, and a verdict per section.

Replace every `<...>` placeholder.

## What is actually being tested

Block 4 added a planner, hard budgets, a session dose ledger, an acquisition
confirmation gate, and a lazily-fed event stream that keeps at most one event in
flight so a cancel can take effect one event later.

One claim is **inferred, not measured**: that forfeiting pycro-manager's event
pipelining to gain that cancellation granularity does not cost unacceptable
throughput. G1 is the only section that can settle it, and it is the likeliest
reason to reject this branch. Run it first.

---

## G0. Preflight — identity, migration, and fail-closed

Record the environment before touching anything.

```
git -C <repo> rev-parse HEAD
git -C <repo> status --short
python -V
python -m pytest -q 2>&1 | tail -3
```

The `acquisition:` section is now **required**. An existing rig config will refuse to
start until it is added. That refusal is itself the first test.

1. Start `microclaw` with the rig config **before** editing it. Confirm it exits with
   a file-anchored error naming all nine missing `acquisition.*` fields, and that it
   does so *before* any prompt, app construction, or tool dispatch.
2. Add the section. Start from the shipped example's values, then set them to
   something appropriate for M5. Record the file you used.
3. Start again and confirm a clean start.

Stop if the refusal is vague about which fields are missing, or if anything
connects-and-mutates before the refusal.

---

## G1. Throughput cost of the one-event-in-flight gate — RUN THIS FIRST

This is the block's only unmeasured claim, and the one that can sink it.

Do not measure this through the agent — agent round-trip latency (design/13) would
swamp the signal. Drive `run_timelapse` directly on each branch and compare.

Save this as `<evidence-dir>/throughput_probe.py`:

```python
"""Block 4 gate G1: frames/s for a lazily-fed timelapse. Dark, minimum exposure."""
import sys, time
from microclaw.config import load_safety_config_or_exit
from microclaw.safety import SafetyGuard
from microclaw.controller import MicroscopeController
from microclaw import tools

CONFIG = sys.argv[1]
SAVE_DIR = sys.argv[2]
N_FRAMES = int(sys.argv[3]) if len(sys.argv) > 3 else 200
EXPOSURE_MS = float(sys.argv[4]) if len(sys.argv) > 4 else 5.0

parsed = load_safety_config_or_exit(CONFIG)
guard = SafetyGuard(parsed.constraints)
ctrl = MicroscopeController(guard=guard)
tools.CONFIRM_FN = lambda summary, kind="action": True   # unattended; dark run only

t0 = time.monotonic()
result = tools.run_timelapse(
    ctrl, guard, n_frames=N_FRAMES, interval_s=0,
    save_dir=SAVE_DIR, name="g1_throughput", exposure_ms=EXPOSURE_MS,
)
elapsed = time.monotonic() - t0
print(f"frames={N_FRAMES} exposure_ms={EXPOSURE_MS}")
print(f"elapsed_s={elapsed:.3f} frames_per_s={N_FRAMES/elapsed:.2f}")
print(f"ideal_s={N_FRAMES*EXPOSURE_MS/1000:.3f} overhead_per_frame_ms="
      f"{(elapsed - N_FRAMES*EXPOSURE_MS/1000)*1000/N_FRAMES:.2f}")
print(result)
```

Run it on **both** branches, shutter closed, same frame count and exposure:

```
git checkout main
python <evidence-dir>/throughput_probe.py <config> <workspace>/g1-main 200 5

git checkout design32/acquisition-budgets
python <evidence-dir>/throughput_probe.py <config> <workspace>/g1-branch 200 5
```

`main` has no `acquisition:` requirement, so use a copy of the config without that
section for the `main` run and note that you did.

Repeat at a longer exposure (`... 50 100`) — the gate's relative cost should shrink as
exposure grows, and that shape is as informative as the absolute number.

Report `frames_per_s` and `overhead_per_frame_ms` for each branch at each exposure.

**Stop conditions.** A large regression on the short-exposure SMLM path is a reject,
not a note — `run_timelapse(interval_s=0)` is the documented SMLM path
(`microclaw/tools.py:772`). If the branch is materially slower there, come back before
running the rest of the gate: the fix is a design question (is one-event granularity
worth this?), not a patch.

---

## G2. Cancellation — is there anything that can trigger it?

The feeder honours an abort: it polls `acq._acq.is_finished()` and stops feeding.
**But review found no operator-facing trigger** — nothing in the package calls
`Acquisition.abort()`, and `POST /api/stop` stops the agent turn at round and tool
boundaries, not inside a running tool. Establish this empirically rather than assuming
it either way.

1. Start `microclaw serve`. Begin a long dark timelapse through the agent:

   > Run a timelapse of 500 frames at 5 ms exposure with `interval_s=0`, shutter
   > closed, saving to `<workspace>/g2-cancel`. Do not analyze anything afterwards.

2. While it runs, press Stop in the GUI. Record: does the acquisition stop, or does
   Stop only take effect after the tool returns? Time both.

3. If Stop does not reach the acquisition, trigger an abort directly to measure the
   feeder itself. In a second Python process against the same bridge, or from a thread
   in a variant of the G1 script, call `acq.abort()` on the live acquisition and
   record request → last frame written.

**What to report:** whether an operator can cancel an acquisition at all today; and,
separately, the feeder's latency once an abort is actually issued. Confirm exactly one
further frame lands after the abort, and that the dataset closes cleanly rather than
hanging in `__exit__` (that hang is the failure design/24 Fix 2a exists to prevent).

If there is no operator trigger, that is a **finding, not necessarily a Block 4
blocker** — the mechanism is correct and Block 4's charter was budgets. Record it for
the post-merge design gate; it likely becomes its own small block.

---

## G3. Budget edges and the confirmation gate

Set the nine `acquisition:` fields to small, easily-crossed values for this section
only, so nothing has to run long to cross a limit. Record the config you used.

Dark, minimum exposure throughout.

1. **Just under a hard limit.** With `max_frames: <N>`:

   > Run a timelapse of `<N-1>` frames at minimum exposure with `interval_s=0`,
   > shutter closed, saving to `<workspace>/g3-under`. Report the plan you were shown
   > before it ran.

   Expect: runs, plan summary reported.

2. **Just over the same hard limit.**

   > Now run the same thing with `<N+1>` frames.

   Expect: refused. Confirm the refusal names `acquisition.max_frames` and the actual
   value, and that **no stage motion or exposure occurred** — the refusal must precede
   hardware.

3. **Each remaining hard limit.** Repeat the over/under pair for `max_duration_s`,
   `max_bytes`, and `max_illuminated_ms`. Use frame count and exposure to cross each.

4. **Each confirmation threshold.** For each of `confirm_above_frames`,
   `confirm_above_duration_s`, `confirm_above_bytes`, `confirm_above_illuminated_ms`,
   run one plan that crosses only that threshold and confirm the acquisition
   confirmation fires, names the threshold crossed, and renders frames, ms/frame,
   duration, bytes, and illuminated ms.

5. **Decline.** Cross a threshold and decline the confirmation. Then immediately run a
   plan that only fits if the declined reservation was fully released:

   > Decline that one. Now run `<a plan sized to just fit the remaining session
   > budget>` and report the session totals before and after.

   Expect: the declined plan cost nothing — no motion, no exposure, and the session
   ledger unchanged.

6. **Cumulative session limit.** With a small `max_session_illuminated_ms`, run several
   small acquisitions in one session until the cumulative limit refuses the next one.
   Confirm the refusal cites `max_session_illuminated_ms`, and that restarting the
   session resets it (record that this is per-session, not durable).

---

## G4. Ledger fidelity and partial completion

1. **Exact accounting.** Run a small dark timelapse and a small dark Z-stack. For each,
   compare planned vs. accounted frames, estimated vs. actual bytes on disk, and the
   session illuminated-time delta vs. `frames × exposure`.

2. **Partial completion.** Cause an acquisition to end early — the cleanest way is a
   multiposition run where one position is unreachable or fails:

   > Run a multiposition timelapse over `<positions, one of which will fail>`, one
   > frame each, dark. Afterwards report per-position results and the session totals.

   Expect: the ledger credits **only** completed frames, and the released reservation
   lets a subsequent plan use the unspent budget.

3. **Overrun reporting.** If any run returns `budget_overrun`, `overrun_frames`, or
   `budget_exhausted`, capture it verbatim — those fields exist so an overrun is never
   a silent early stop (design/27's lesson). An overrun means an estimate was wrong;
   report which one.

4. **Adaptive cap.** Run a small adaptive survey and confirm `budget_exhausted` is
   `false` on a normal run. Then, if you can, drive a hook that tries to enqueue one
   more tile than the planned grid, and confirm it stops cleanly, logs loudly, and
   surfaces `budget_exhausted: true` — **without** an exception crossing a
   pycro-manager thread and without a hung `__exit__`.

---

## G5. MMStudio MDA — preview token round-trip

This is unverified bridge code (`settings.slices()`, `settings.channels()`,
`spec.useChannel`, `spec.exposure`) and cannot be validated off-rig. A wrong field name
does not error; it silently produces a token that fails to invalidate.

1. In the MM GUI, set up a **small, dark** MDA: a few Z slices, one or two channels,
   minimum exposure, few time points.

2. > Call `get_mda_settings` and show me the resolved settings and the plan you would
   > reserve for them, including frames, ms/frame, estimated duration and bytes. Do not
   > run it.

   Confirm the reported frame count matches slices × channels × time points × positions,
   and that per-channel exposures and the exact slice list appear.

3. **Change only a per-channel exposure in the GUI.** Then:

   > Run the MDA with the token you just got.

   Expect: refused as stale. If it runs, `spec.exposure` is not being read and the
   token is blind to exposure changes — **that is a stop**.

4. **Repeat for the slice list**: add or remove one slice in the GUI, then retry the
   old token. Expect: refused as stale.

5. **Clean path.** Re-read settings, then run immediately. Confirm the acquisition
   confirmation shows the plan, the run completes, and the session ledger is credited
   for the full planned frame count (MDA is opaque, so it credits on completion).

6. Confirm the result states MDA cannot be cancelled per event.

---

## G6. Duration honesty

`max_duration_s` bounds a deliberately known-low estimate: exposure and
`min_start_time` only. Readout, stage settling, autofocus, and filter switching are
excluded and unmeasured.

Run one dark multiposition acquisition over `<8-12 positions>`, one frame each, and
record estimated vs. actual wall time. Repeat with autofocus enabled if that is a
normal M5 workflow.

Report the per-frame overhead this implies. That number is what the post-merge design
gate writes into design/32, and what Block 5 needs to size dose policy.

This section cannot fail the gate — it produces a measurement. But if actual duration
exceeds the estimate by a large factor, say so plainly: `max_duration_s` is then close
to unenforceable in practice and the design must say so rather than implying a wall-clock
guarantee.

---

## Verdict

Record per section: pass / fail / finding, with the evidence path.

**Merge blockers:** a large short-exposure throughput regression (G1); a hard limit that
can be exceeded silently; a refusal that arrives after motion or exposure; a declined or
failed acquisition that does not roll its reservation back; an MDA token blind to
exposure or slice changes; any exception crossing a pycro-manager thread; a hung
`__exit__`.

**Findings, not blockers:** no operator-facing cancel trigger (G2); the measured
duration-estimate gap (G6); per-session rather than durable ledger scope.

Return the evidence directory and the verdicts. The coordinator reviews and merges, then
runs the post-merge design gate against design/32 §2.
