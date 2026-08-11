# Block 43i rig gate — budgeted survey refocus

Implementation ancestor: bb12d95

Use PowerShell from the checked-out repository. uv is the single launcher for
every Python/project command below; do not substitute bare Python for one line.
Git commands only establish the checkout.

## Which machine, and why the demo cannot close this one

**This block needs M5 or M2. The demo machine can close Step 0 and Step 1 only.**

43i's whole mechanism is the focus response. 43g measured that no single-frame
statistic separates cells from a diffuse bright gradient on real data, and that
what identified cells in both F5 and F6 was a sweep converging or refusing to.
**The demo camera returns bit-identical frames**, so its metric curve is flat by
construction: every sweep reports non-convergence, the converged limb can never
fire, and no criterion below Step 2 can distinguish working code from broken
code. A criterion that cannot fail is not a criterion — this is the constraint
that narrowed 43g's demo gate to reach only, and it applies harder here.

M5 is the natural host because the operator framing this gate uses came from its
Nestor session, but nothing here is M5-specific: any rig with a real sample and a
real focus response can run it.

**Dose note before you start.** An autofocus sweep is 20–60 extra exposures per
refocused tile. `autofocus_budget.max_exposures` bounds the total, and the run's
reservation is widened by exactly that number, so the authorization prompt shows
the real worst case. Size it deliberately for the sample in front of you.

## Step 0 — pin and run the full suite on this machine

    git merge-base --is-ancestor bb12d95 HEAD
    if ($LASTEXITCODE -ne 0) { throw "Block 43i implementation is not in this checkout" }

    uv run python -m pytest -q > suite-43i.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content suite-43i.txt; throw "Full suite failed" }
    Get-Content suite-43i.txt

    uv run python -m pytest --collect-only -q > collected-43i.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content collected-43i.txt; throw "Collection failed" }

macOS measured **1757 passed + 99 skipped = 1856 collected**, 3 warnings. Every
Track F Windows run has shown the same 17-test platform-conditional difference
and a 116 skip count, so expect **1740 + 116 = 1856** here. Derive the total from
passed + skipped rather than reading it off. Stop if tests failed, if the
collected total is not 1856, or if the skip count rose above 116.

## Step 1 — offline checks, no microscope time

    uv run python -m pytest -q tests/test_session_script_export.py tests/test_hook_decisions.py > offline-43i.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content offline-43i.txt; throw "Offline checks failed" }
    Get-Content offline-43i.txt

## Step 2 — reach: does an operator sentence get there at all?

**Reach is measured separately from mechanism, so a reach failure does not void
the rest of the gate.** If Step 2 fails, record it and continue from Step 3 by
calling the tool directly.

Open Microclaw on a real sample with structure, mark three or more positions
across a region where some tiles are in focus and some are not, and say something
close to the operator's own sentence — **naming no tool and no parameter**:

> scan these positions with the current focus, but when you see a tile with more
> signal, use it to focus and look again

PASS requires the agent, unprompted, to: write or select a hook that returns
`RequestAutofocus`, call `run_adaptive_survey` with an `autofocus_budget`, and
say what the budget costs in exposures before running it.

This criterion is unanswerable without the block: before 43i, `RequestAutofocus`
was refused by every runner, and the only honest answer to that sentence was the
four-round-trip workaround F5 describes.

Record the exact sentence you used and what the agent did.

## Step 3 — mechanism: the refocus fires, converges, and is judged again

On a tile with real structure that is **out of focus** — the F5 signature is
material present but not sharp, the `ridge_coverage 0.17–0.30 / snr 1.4–2.2` band
— run the survey and read the hook log.

PASS requires all of:

- a `RequestAutofocus` record with `decision: accepted` and reason
  `refocused and re-queued this tile`, carrying an `autofocus` block with
  `converged: true` and `entry_z_um` / `final_z_um` that differ;
- the same tile appearing a second time in the log, with the hook's own
  measurements from the focused frame;
- the survey then advancing to the next planned tile.

The operator's framing of this criterion: on a raster containing tiles like
frames 20 and 17 of `mt_search_561/mt_raster_1`, the survey should spend a
refocus there and report whether it converged.

## Step 4 — the negative limb: a sweep that does not converge

On bare glass or a flat, structureless field — `scan300_488_r12_c15` is the
reference case — the sweep must run and **fail** to converge.

PASS requires: `decision: accepted`, reason `autofocus ran and did not converge;
Z restored`, no second look for that tile, **no widening and no retry**, and the
survey carrying on to the next tile. Confirm from the log that `entry_z_um` and
`final_z_um` are equal.

This limb is the one that makes the feature affordable: convergence does not
prove cells, but failure to converge disproves them.

## Step 5 — both looks survive in the saved dataset

**This is a rig step, not an offline one.** An offline probe already established
that NDTiff indexes identical axes as a single readable frame, which is why the
re-exposure carries a `refocus=1` axis. What that probe could not establish is
whether the **acquisition engine** accepts an event carrying an axis that was not
in the `multi_d_acquisition_events` plan.

On the dataset from Step 3:

    uv run microclaw view-history

Then read the dataset back and confirm the refocused position has **two readable
frames**, one at `refocus` absent/0 and one at `refocus=1`, with visibly
different focus. If the axis was rejected by the engine, or if only one frame
comes back, that is a FAIL and it is the finding — the hook log will report
success either way, which is exactly why this step exists.

Note for the report: a survey where one tile of many was refocused produces a
ragged `refocus` axis, so a TIFF export of it will zero-pad the absent cells.
Zero-padding corrupting `ImageStats` statistics is already a carried-forward
finding sized as its own block; record whether you see it here, but it is not
this block's failure.

## Step 6 — the emitted script reproduces the refocus decision standalone

Export the session, then **close Microclaw** (leave Micro-Manager and the bridge
running — the script needs the core) and run the script by itself.

    uv run python <exported-script>.py > emitted-run-43i.txt 2>&1
    Get-Content emitted-run-43i.txt

PASS requires: zero `NOT EMITTED` in the artifact, no `microclaw` imports, and
the standalone run reaching the same refocus decision at the same tile — same
convergence verdict, same second look, same advance. The script writes its hook
log beside itself with a collision suffix, so compare it against the live one.

The script's `configure_autofocus` line passes `focus_lock_check=None` under a
comment saying so: a standalone script has no generic focus-lock query.
**Disengage the focus lock before running it**, and confirm the comment is
present in the file.

## Step 7 — the refusals are visible

Cheap, and it closes the capability's shape. Re-run a short survey with
`autofocus_budget` sized below one sweep (`max_exposures` less than the sweep's
plane count) and confirm the log records
`authorized autofocus exposure budget exhausted` rather than silently skipping.

With the focus lock engaged, confirm `focus lock is engaged; autofocus sweep
refused`.

Run a survey with **no** `autofocus_budget` and a hook that requests autofocus,
and confirm `unsupported-by-run_adaptive_survey` — the pre-43i behaviour, intact.

## Reporting

For each step: PASS / FAIL / NOT EXERCISED, with the log file or hook-log excerpt
that shows it. Say which machine and which sample. If a step was not run, say
that rather than inferring it from another step — a self-confirming probe is not
evidence, and a step that measured nothing is more useful reported as such.
