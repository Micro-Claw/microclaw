# Block 43j rig gate — hooks fold into the timelapse and Z-stack tools

Implementation ancestor: `f36ea89`

## What this block changed

`run_adaptive_timelapse` and `run_adaptive_zstack` **are gone.** A hook now
attaches directly to `run_timelapse` and `run_zstack` through `hook_strategy` /
`hook_params` / `log_path` (plus `illumination_envelope` / `artifact_limits`),
and those tools keep everything they already had — `exposure_ms`, `laser_slot`,
the trigger pre-flight, the plain hookless result. A hooked call emits its
program through the same 43h machinery an adaptive survey uses; a hookless call
emits exactly what it emitted before.

Separately, `list_hooks` now marks an unusable saved hook `resolvable: false`
where it is chosen, and `resolve_refusal` carries the re-review remedy as a call
rather than as prose.

**Almost all of this gate runs on the demo machine.** Steps 0–6 close the whole
mechanism and both reach criteria. **Only Step 7 needs M5 or M2**, because it is
the EMU trigger pre-flight and nothing else. Run Steps 0–6 first; do not spend
microscope time on anything above Step 7.

The demo camera returns bit-identical frames, and for this block that costs
nothing: the feature under test **records** every frame rather than deciding
between them, so an observation hook is fully exercised on featureless data. Do
not write a content threshold into any prompt below — a hook that only logs is
the case this block exists for.

## Step 0 — pin and run the full suite on this machine

    git merge-base --is-ancestor f36ea89 HEAD
    if ($LASTEXITCODE -ne 0) { throw "Block 43j implementation is not in this checkout" }

    uv run python -m pytest -q > suite-43j.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content suite-43j.txt; throw "Full suite failed" }
    Get-Content suite-43j.txt

    uv run python -m pytest --collect-only -q > collected-43j.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content collected-43j.txt; throw "Collection failed" }

macOS measured **1797 passed + 99 skipped = 1896 collected**, 3 warnings, at the
pin. Windows has shown the same 17-test platform-conditional difference on every
Track F run, so expect **1780 + 116 = 1896** here. Derive the total from
passed + skipped rather than reading it off. Stop if tests failed, if the
collected total is not 1896, or if the skip count rose above 116.

**A fourth warning may appear and is not this block.** `test_bridge_check.py`
has a known Windows-only socket race that intermittently raises
`PytestUnhandledThreadExceptionWarning` (`WinError 10038`) from its own accept
thread. The test still passes. Record it and carry on.

## Step 1 — offline checks, no microscope time

    uv run python -m pytest -q tests/test_session_script_export.py tests/test_acquisition_budgets.py tests/test_describe_hook.py > offline-43j.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content offline-43j.txt; throw "Offline checks failed" }
    Get-Content offline-43j.txt

## Step 2 — reach: does an operator sentence get to a hooked acquisition?

**Reach is measured separately from mechanism, so a reach failure does not void
the rest of the gate.** If Step 2 fails, record what the agent did instead and
continue from Step 3 by asking directly.

Open Microclaw and say something close to this, **naming no tool and no
parameter**:

> run a 20-frame timelapse here and keep a record of each frame's signal as it
> is acquired, so I can look at how it changed without going back over the
> dataset afterwards

**This sentence has a legitimate wrong answer, and that is the point.** 43e
shipped `frame_statistics`, which scores a *completed* dataset offline — so an
agent that runs a plain timelapse and then analyses the saved data has answered
the question, has not touched this block, and has demonstrated that F12's
during-the-run half is still unreachable. Score it as follows:

- **PASS** — the agent calls `run_timelapse` with a `hook_strategy` (expect
  `snr_observer`) and then `read_hook_log`.
- **REACH FAIL, not a gate failure** — the agent runs a plain timelapse and
  reaches for `run_analysis_on_saved_dataset` / `frame_statistics`. Record the
  transcript. This is a real finding about the tool description, not a defect in
  the code, and Step 3 still measures the mechanism.

Record which, quote the sentence the agent used, and move on either way.

## Step 3 — mechanism: one hook, every frame, one log

Ask directly this time:

> run a 20-frame timelapse with the snr_observer hook and show me the log

Then the Z-stack limb:

> run a Z-stack over 10 planes with the snr_observer hook and show me that log too

PASS requires **all** of:

- both calls succeed and each result carries a `log_path` and the
  `read_hook_log` hint;
- the timelapse log has **one row per frame** — 20 rows for 20 frames — and the
  Z-stack log one row per plane;
- the result also still carries the plain fields: `status`, `dataset_path`, and
  the reservation report. A hooked result is a **superset** of the plain one,
  and a missing plain field is a failure;
- neither run reports an error, and neither dataset is empty.

Check the row count yourself rather than accepting the agent's summary:

    uv run python -c "print(sum(1 for _ in open(r'<log_path>')))"

## Step 4 — the plain path did not change

> run a 10-frame timelapse with no hook

PASS requires the result to look exactly as it always did — `status`,
`dataset_path`, reservation report — with **no** `log_path`, no `hint`, and no
hook fields. The fold must not have made every acquisition a hooked one.

## Step 5 — export, and run it with Microclaw closed

Ask the agent to export the session from Steps 3 and 4:

> export this session as a standalone script

Then **close Microclaw** (leave Micro-Manager and the bridge running) and run it:

    uv run python <exported-script>.py > emitted-run-43j.txt 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content emitted-run-43j.txt; throw "Standalone run failed" }
    Get-Content emitted-run-43j.txt

PASS requires **all** of:

- **zero** `# NOT EMITTED` in the artifact, and `emitted_calls` covering both the
  hooked and the hookless acquisitions;
- **no `microclaw` imports** in the script — check with
  `Select-String -Path <script>.py -Pattern "import microclaw","from microclaw"`;
  the only permitted hits are string *values* in a provenance comment;
- the script runs to completion with Microclaw closed and writes its datasets;
- **the hooked dataset is named the same thing the live run named it** —
  `timelapse` and `zstack`, not `adaptive`. This is a coordinator fix made during
  review and it has never run outside a unit test; the emitter's fallback used to
  be the literal `adaptive`, which was right for the deleted twins and wrong for
  these tools;
- the hook log is written **beside the script** with a collision suffix, and the
  original session's log is intact (43h's `_next_available_log_path`).

## Step 6 — a dead saved hook says so where it is chosen

This is F9, and it reproduces the Nestor session directly: two saved hooks were
unusable for a whole session, and what the agent offered was to write a third.

First have the agent save a hook — any observation-only hook will do:

> write and save a hook that records each frame's mean intensity, call it
> `gate43j_probe`

Then break its integrity pin **outside Microclaw**, in PowerShell:

    Add-Content -Path "$env:USERPROFILE\.microclaw\hooks\gate43j_probe.py" -Value "# edited after saving"
    Get-Content "$env:USERPROFILE\.microclaw\hooks\gate43j_probe.py" -Tail 2

Now, in a **new** Microclaw session:

> what hooks do I have available, and can I use gate43j_probe for a timelapse?

PASS requires **all** of:

- `list_hooks` marks `gate43j_probe` with `resolvable: false` **inline**, beside
  its description, without the agent having to call `describe_hook` first;
- the agent tells the operator the hook is unusable and **why**, rather than
  discovering it by attempting the run;
- the remedy it offers is **re-review of this hook** — read the file, show the
  code, save it again — and **not** writing a new one. The remedy is now carried
  in the payload as a call (`read_hook_from_file` → `generate_and_save_hook`), so
  the agent should be able to name it without inventing it;
- every other saved hook still reads `resolvable: true`.

Then restore the hook so the machine is left clean: re-save it through the
agent's own remedy path, which also gates that path end to end.

## Step 7 — **M5 or M2 only**: the EMU trigger pre-flight under a hook

Everything above runs on the demo machine. This step cannot: it asserts that the
`laser_slot` pre-flight still guards a hooked SMLM timelapse, and the demo config
has no EMU.

Both limbs, in one session.

**7a — armed.** With the excitation laser's trigger line armed:

> run a 200-frame SMLM timelapse at 20 ms with no interval on laser slot
> <slot>, with the snr_observer hook, and show me the log

PASS requires the result to carry `trigger_preflight` with its
`guarantee: trigger line is armed`, **and** a hook log with one row per frame.
Both together — the pre-flight and the hook are the two things this fold had to
keep working at once, and only this rig can show it.

**7b — gated off.** Set that slot's trigger mode to `0 - Off` (or its sequence to
0) through the MM GUI, and ask for the same acquisition again.

PASS requires the call to be **refused before any exposure**, naming the slot and
the property. Confirm nothing was acquired: no new dataset directory, and no hook
log for the refused run.

Restore the trigger mode afterwards.

## What to send back

`suite-43j.txt`, `collected-43j.txt`, `offline-43j.txt`, `emitted-run-43j.txt`,
the exported script itself, the hook logs from Steps 3 and 7a, and the session
history. For Step 2, say which of the two outcomes happened and quote the agent's
own sentence — that answer is a finding either way.
