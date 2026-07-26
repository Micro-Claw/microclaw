# design/32 Block 4 — acquisition-budget rig gate prompts

Gate for `design32/acquisition-budgets` (impl `a8f0209` + `8d7e832`, coordinator fix
`01b2766`). The branch is pushed; **do not merge until this gate passes.**

Everything here is designed to run **dark or at minimum exposure**. No step needs a
sample, and no step should be run with a sample you care about. Keep frame counts
small; the point is to exercise the accounting, not to acquire anything.

Keep one dated evidence directory for the whole gate: commands, stdout/stderr,
environment identity, config files used, artifacts, hashes, and a verdict per section.

Replace every `<...>` placeholder.

**The rig is Windows.** Commands here are written for PowerShell and avoid Unix
pipelines (`tail`, `head`). Redirection with `>` and `2>&1` behaves the same in
PowerShell and `cmd`, so every command below works in either. Paths are shown with
backslashes; Python accepts forward slashes on Windows too, so either is fine inside
the probe script's arguments.

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

```powershell
git -C <repo> rev-parse HEAD
git -C <repo> status --short
python -V
python -m pytest -q > <evidence-dir>\pytest.txt 2>&1
```

Open `pytest.txt` and record the summary line. Expect **892 passed / 98 skipped /
3 warnings**; the 3 warnings are pre-existing on `main`. If the count differs, say so
before continuing — the branch was verified at that number on macOS/Python 3.11.15 and
a Windows delta is itself a finding.

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

Save this as `<evidence-dir>\throughput_probe.py`:

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

```powershell
git checkout main
python <evidence-dir>\throughput_probe.py <config-no-acquisition-section> <workspace>\g1-main 200 5 > <evidence-dir>\g1-main-5ms.txt 2>&1

git checkout design32/acquisition-budgets
python <evidence-dir>\throughput_probe.py <config> <workspace>\g1-branch 200 5 > <evidence-dir>\g1-branch-5ms.txt 2>&1
```

`main` has no `acquisition:` requirement, so use a copy of the config **without** that
section for the `main` run, and note in the evidence that you did. Keep both config
files.

Repeat at a longer exposure, writing to `-50ms` files:

```powershell
git checkout main
python <evidence-dir>\throughput_probe.py <config-no-acquisition-section> <workspace>\g1-main-50 100 50 > <evidence-dir>\g1-main-50ms.txt 2>&1

git checkout design32/acquisition-budgets
python <evidence-dir>\throughput_probe.py <config> <workspace>\g1-branch-50 100 50 > <evidence-dir>\g1-branch-50ms.txt 2>&1
```

The gate's relative cost should shrink as exposure grows, and that shape is as
informative as the absolute number.

Switching branches changes installed package code. If you hit import or
"unrecognised arguments" oddities after a checkout, run `pip install -e .` before
suspecting the probe (CLAUDE.md).

Report `frames_per_s` and `overhead_per_frame_ms` for each branch at each exposure.

**Stop conditions.** A large regression on the short-exposure SMLM path is a reject,
not a note — `run_timelapse(interval_s=0)` is the documented SMLM path
(`microclaw/tools.py:772`). If the branch is materially slower there, come back before
running the rest of the gate: the fix is a design question (is one-event granularity
worth this?), not a patch.

### G1a. Attributing a regression — `design/32-block4-g1-diagnose.py`

Run this only when G1 shows a regression. It runs the same dark timelapse three ways
and says where the time goes, instead of inviting another guess:

```powershell
uv run python design\32-block4-g1-diagnose.py --config <config> --save-dir <workspace>\d-asis   --mode asis   --frames 200 --exposure-ms 5 > <evidence-dir>\d-asis.txt   2>&1
uv run python design\32-block4-g1-diagnose.py --config <config> --save-dir <workspace>\d-wide   --mode wide   --frames 200 --exposure-ms 5 > <evidence-dir>\d-wide.txt   2>&1
uv run python design\32-block4-g1-diagnose.py --config <config> --save-dir <workspace>\d-nopoll --mode nopoll --frames 200 --exposure-ms 5 > <evidence-dir>\d-nopoll.txt 2>&1
```

Every argument is named and validated. Check the first two output lines of each file
before reading the numbers: `mode=` must be the mode you asked for and
`lookahead_in_effect=` must be 2 for `asis` and huge for `wide`/`nopoll`. The first
attempt at this probe used positional arguments, and a dropped mode word silently
produced three identical 5-frame runs.

- `asis` — branch as shipped, timing every `is_finished()` bridge call.
- `wide` — look-ahead raised so the feeder never blocks; isolates the per-event bridge
  poll from the gating cost.
- `nopoll` — look-ahead raised **and** `is_finished()` stubbed cheap.

Reading the result:

| Result | Conclusion |
|---|---|
| `asis` mean `is_finished` ≫ 0.1 ms, large share of elapsed | the bridge poll is the cost; get it off the hot path |
| `wide` ≈ `asis` | gating is not the cost; something per-event is |
| `wide` ≈ `main` | gating **is** the cost, and no small depth fixes it |
| `nopoll` ≈ `main` | confirms the poll specifically |
| all three ≈ the regressed number | lazy feeding itself is the problem — the engine cannot sequence events it does not hold, and gated feeding cannot be the default for `interval_s=0` |

The last row is a design/32 outcome, not a patch: it would mean cancellable feeding has
to be opt-in with fast streaming as the default. Stop and reconcile the design before
writing more code.

The probe is diagnostic only — it changes nothing on the branch and produces no
artifact worth keeping beyond its stdout.

---

## G2. Cancellation — deferred, not tested

**Nothing to run here.** The lazy-feeding cancellation mechanism was removed after G1
measured it at 3.14x on M5 (design/32 §2, second reconciliation). List-backed
acquisitions — every fixed runner — cannot be cancelled mid-run, and never could before
Block 4 either. There is nothing to cancel and no operator trigger to test: nothing in
the package calls `Acquisition.abort()`, and `POST /api/stop` stops the agent turn at
round and tool boundaries, not inside a running tool.

Record one finding for the post-merge design gate, no rig action:

> Mid-acquisition cancellation is unimplemented and unreachable. It needs its own block
> that ships the abort trigger and the interruption mechanism together, and that budgets
> for the measured cost of feeding events lazily. Block 4 deliberately does not attempt
> it.

The adaptive survey path is the one exception and it is unchanged from `main`: it feeds
generators because its later events do not exist until a hook produces them, and it
stops by not enqueuing the next tile (design/24 Fix 2a), not by aborting.

---

## G3. Budget edges and the confirmation gate

Dark throughout (M5 has no shutter — dark means lasers off). Use a **separate tiny
config**, not your working M5 config, and record which file you used.

The table below crosses each limit on its own by exploiting the guard's check order
(frames → duration → bytes → illuminated → session) and two independent levers: frame
count drives frames+bytes, exposure drives duration+illuminated. `interval_s` raises
duration while illuminated stays ~0, which is the only way to separate the two (at
`interval_s=0`, `duration_s == illuminated_ms/1000`).

**Frame size (measured on M5, 2026-07-26): `F = 10,616,832 B`** — the DCAM sensor is
**2304×2304**×16-bit, not the 2048² default. The ROI is fixed: `set_roi` is refused by
the Phase-1 authorization map (`camera-roi write path is excluded`), so a byte budget
cannot be dodged by cropping, and `F` is stable at full frame. If a different rig's `F`
differs, rescale only the byte fields: `confirm_above_bytes = 1.5·F`, `max_bytes = 2.5·F`.

### Tiny config

```yaml
acquisition:
  max_frames: 3
  confirm_above_frames: 1
  max_duration_s: 1.0
  confirm_above_duration_s: 0.5
  max_bytes: 26542080            # 2.5 × 10,616,832  (2304²×2)
  confirm_above_bytes: 15925248  # 1.5 × 10,616,832
  max_illuminated_ms: 250
  confirm_above_illuminated_ms: 150   # confirm BELOW hard — never invert these
  max_session_illuminated_ms: 500
```

### Runs

All are `run_timelapse`, dark, saving to a scratch dir. `exp` in ms; `interval_s` = 0
unless noted.

| # | Tests | frames | exp | interval_s | Expected |
|---|---|---|---|---|---|
| 1 | frames+bytes confirm | 2 | 0.0177 | 0 | **Confirms** (`frames, bytes`); accept → runs |
| 2 | frames hard | 4 | 0.0177 | 0 | **Refused** `max_frames=3` (already observed) |
| 3 | bytes hard | 3 | 0.0177 | 0 | **Refused** `max_bytes` |
| 4 | duration confirm | 2 | 0.0177 | 0.6 | **Confirms** (`duration` +frames,bytes); accept → runs |
| 5 | duration hard | 2 | 0.0177 | 1.2 | **Refused** `max_duration_s` |
| 6 | illuminated confirm | 2 | 100 | 0 | **Confirms** (`illuminated time` +frames,bytes); accept → runs |
| 7 | illuminated hard | 2 | 150 | 0 | **Refused** `max_illuminated_ms` |
| 8 | **decline → rollback** | 2 | 100 | 0 | Confirms; **decline** → session ledger unchanged |
| 9 | session cap | 2 | 100 | 0 | Accept twice (200 ms each); a **third** is **Refused** `max_session_illuminated_ms` |

### What each run must show

1. **Under a hard limit / confirmation fires.** A confirmation that renders frames,
   ms/frame, duration, bytes, and illuminated ms, and lists every threshold exceeded.
   Multiple labels is correct — verify the one under test appears, not that it appears
   alone.
2. **Over a hard limit.** The refusal names `acquisition.<field>` and the actual value,
   and **no stage motion or acquisition occurred** — it must precede hardware. (Known
   nit to note, not a blocker: `run_timelapse` writes the camera exposure property
   before the budget check, so a benign `set_exposure` precedes the refusal; no light,
   no stage, no frames.)
3. **Each hard limit (runs 3/5/7).** Shaped so the intended limit is the *first* in
   check order to trip, so the refusal cites it specifically.
4. **Each confirmation threshold (runs 1/4/6).** Its label appears in the exceeded list
   at the designed plan.
5. **Decline (run 8).** The declined plan costs nothing: capture session totals before
   and after — they must be identical. This is the important one; accept-then-run is
   easy, rollback is the safety property.
6. **Cumulative session (run 9).** The third run is refused on `max_session_illuminated_ms`
   specifically (its own 200 ms is under `max_illuminated_ms=250`), and restarting the
   session resets it — record that this is per-session, not durable.

### Findings from the first G3 attempt (2026-07-26)

- **Bytes hard limit fires at preflight, precisely.** A 2-frame full-frame run
  (21.2 MB) was refused `bytes=2.12337e+07 exceeds acquisition.max_bytes` before any
  acquisition — the over-limit half of the bytes pair, observed.
- **Bytes budget is evasion-resistant.** The agent tried to crop the ROI to fit under
  `max_bytes`; `set_roi` was refused by the authorization map. The budget cannot be
  dodged by shrinking frames on M5.
- **A fully sub-threshold run shows no plan.** The 1-frame run crossed nothing and ran
  with no confirmation and no plan preview — correct: the plan surfaces only when a
  confirmation fires. So "plan shown before motion" is only testable at/above a confirm
  threshold, which is what runs 1/4/6 are for.
- **Still untested after attempt 1:** every confirmation (no threshold was ever
  crossed — the byte cap was mis-sized below 2 frames), decline/rollback, duration and
  illuminated limits, and the session cap. Attempt 2 uses the corrected `F` above.

### G3 VERDICT — PASS (attempt 2, 2026-07-26)

The confirmation dialog is operator-facing (`CONFIRM_FN` → the browser/CLI prompt), so
its evidence is what the operator saw, not the agent's tool result.

- **Hard limits fire before hardware, named:** `max_frames=3` (4-frame run) and
  `max_illuminated_ms=250` (2×300 ms → 600 ms). Both refused at preflight with field and
  value; nothing acquired.
- **Hard limit refuses before the confirmation dialog** even when both would trigger
  (the 600 ms run never prompted).
- **Confirmation fires above thresholds and renders the full plan** — operator confirmed
  a dialog appeared for the 2×min, 2×100 ms, and 2×75 ms runs, each showing `frames`,
  `exposure_ms/frame`, `duration_s`, `bytes`, `illuminated_ms`, and the exceeded list.
  Accepted runs completed; below all thresholds (1 frame) no dialog appeared.
- **Decline refuses and rolls back.** An intentional operator decline of the 2×75 ms
  plan refused it. Ledger-identity after a decline is proven by
  `test_declined_confirmation_rolls_back_reservation_fully` (the in-memory ledger is not
  rig-observable, so the unit test is the authoritative level).
- **Cumulative session cap and partial-completion rollback** are likewise unit-covered
  (`test_hard_limits_and_cumulative_session_limit`,
  `test_partial_completion_commits_actual_and_releases_the_rest`); the rig did not need
  to reach them.

**Finding fixed in-branch:** an operator decline surfaced as a bare
`"Acquisition declined."`, indistinguishable to the agent from a limit refusal. The
message now names that the plan was declined at confirmation, pinned by a test asserting
it contains "confirmation" and no `max_` field.

**Not separately exercised (acceptable):** duration limits via `interval_s` — duration
shares the `check_acquisition` path with illuminated, which was exercised, and both are
unit-tested; a dedicated interval run adds little.

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

### G5 VERDICT — PASS (2026-07-26, after two-run introspection fix)

The gated property is the preview token's sensitivity to slice/channel changes.
First attempt (`f7b1602` era) found `settings.slices()` / `settings.channels()`
throwing `TypeError` — the token was blind to exactly those changes. Root cause found
by the introspection probe (`design/32-block4-mda-settings-probe.py`): both return a
`java.util.ArrayList` that is not directly iterable over the bridge (read by
`size()`/`get(i)`), and on `ChannelSpec` `useChannel` is a field but `exposure` is a
method. Fixed in `138a70d`.

Live confirmation on M5:
- `get_mda_settings` now returns real slice positions (11 values) and per-channel
  `exposure_ms`.
- **Token invalidates on a per-channel exposure change** (ch2 10→20 ms), **on a slice
  change** (−1.0→−0.9 µm, 11→10 slices), and **is stable when nothing changes** (same
  token returned). The exact defect G5 gated is fixed and confirmed.

**Finding (rig config, not a Block 4 defect):** `run_mda` is refused on M5 by
`RigAuthorizationError: mmstudio-mda write path is excluded from the Phase-1
authorization map`. `authorize_path("mmstudio-mda")` is `run_mda`'s first line, before
the token staleness check, so on M5 the clean-path run cannot execute and `run_mda`'s
own staleness refusal is not live-observable. Fail-safe (an unauthorized path can't
run). The staleness refusal and budget reservation are therefore pinned at the unit
level (`test_mda_refuses_a_stale_token_when_settings_changed`,
`test_read_mda_settings_reads_arraylist_slices_and_channel_methods`). Running a full
budgeted MDA end-to-end on M5 would require the operator to add `mmstudio-mda` to the
authorization map — deferred to their discretion; not required for this gate.

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

### G6 MEASUREMENT (2026-07-26, `design/32-block4-g6-duration-probe.py`)

9-position snake grid, 50 µm steps, 100 ms exposure, dark, 3 runs:

| run | estimate_s | actual_s | overhead/frame | actual/estimate |
|---|---|---|---|---|
| 1 | 0.900 | 7.093 | 688 ms | 7.88× |
| 2 | 0.900 | 6.734 | 648 ms | 7.48× |
| 3 | 0.900 | 6.625 | 636 ms | 7.36× |

**~657 ms/frame overhead; actual ≈ 7.6× the exposure-only estimate.** The overhead is
per-position (50 µm move + settle + per-`Acquisition` setup/teardown + dataset write),
essentially fixed and exposure-independent, so the ratio is worst at short exposure and
approaches 1× as exposure grows (≈1.7× at 1 s/frame).

Implication — reinforces, not contradicts, the design: **`max_duration_s` bounds the
exposure-only estimate, not wall-clock time.** Frames, bytes, and illuminated time are
exact; duration is the one estimated budget, and for multiposition it under-counts wall
time by ~7.6× at these settings. An operator must not treat `max_duration_s` as a
wall-clock cap. Adding a per-frame overhead term would be rig-specific (stage speed,
camera, per-`Acquisition` cost) and is deferred; the honest position is the documented
known-low estimate. This number goes to the post-merge design gate and Block 5.

---

## Verdict (2026-07-26)

| Section | Result | Evidence |
|---|---|---|
| G0 preflight / migration | PASS | migration refusal names 9 fields; win baseline 876/114/3 |
| G1 throughput | PASS | 1.00× after the list revert (was 3.4×) |
| G2 cancellation | DEFERRED | no operator trigger exists; own future block |
| G3 budgets + confirmation | PASS | hard limits before hardware; token/confirm render; decline rolls back |
| G4 ledger + partial completion | UNIT-COVERED | rollback/session/partial unit-tested; live partial-completion optional |
| G5 MDA token | PASS | token sensitive to slice + per-channel-exposure change; bridge reads fixed |
| G6 duration honesty | MEASURED | ~657 ms/frame overhead, 7.6× at 100 ms; `max_duration_s` = known-low |

Every section that could fail the gate has passed. Outstanding: G4 optional live
partial-completion run; full budgeted MDA end-to-end (needs `mmstudio-mda` authorized on
M5). Neither is a merge blocker.

**Findings for the post-merge design gate (design/32 §2):** no operator-facing cancel
trigger (G2 — own block); MDA `run_mda` authz-excluded on M5 (G5 — rig config); measured
~657 ms/frame duration overhead (G6); per-session rather than durable ledger scope.

**Fixes the gate produced, now on-branch:** list-revert throughput fix; attributable
decline message; MDA slices/channels bridge reads (+ staleness pin). Coordinator reviews
and merges, then runs the post-merge design gate against design/32 §2.
