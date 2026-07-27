# design/32 Block 7 — generated-hook decision boundary gate

Gate for `design32/generated-hook-decisions`. Do not merge until the demo-core
gate and the M5 regression gate have attributable evidence and explicit verdicts.

## What this gate can and cannot settle

The demo core can settle the live callback wiring: saved-hook capability stripping,
typed action conversion, planned-grid dispatch, reservation refusal, early stop,
and the trusted parent's audit record. It can also run the complete design/26 Run A
plumbing regression without a physical rig.

Only M5 can compare Run A's real record counts, retained images, deterministic
ranking, guard validation, and revisit accuracy with the retained 2026-07-20
evidence. Neither part validates biology, objects, segmentation quality, or whether
an analysis has scientific meaning.

This is Phase 1 only. Saved source still executes inside the hardware-control
process. Source review and sha256 pinning remain the containment story; this gate
does not establish a worker process, deadline, memory cap, network isolation, or
native-crash recovery. Those belong to Block 13.

There is no interactive confirmation inside an acquisition callback. pyjavaz
serializes bridge calls behind one lock, so a mid-acquisition prompt is a hazard.
Actions are authorized only by runner support, SafetyGuard, and the reservation
already committed for the planned survey. An over-reservation proposal must be
refused and audited, never escalated to a prompt.

Use one dated evidence directory per gate. Preserve commands/prompts, stdout and
stderr, environment identity, inputs, artifacts, sha256 files, and one verdict per
step. Never commit output artifacts.

**Windows command rule:** every command below is PowerShell/cmd-safe. Do not replace
redirections with Unix pipelines.

## Common setup

```powershell
$Repo     = "<repo>"
$Evidence = "<dated evidence directory>"
$Scratch  = "<path inside configured workspace>\block7"
$Config   = "$Repo\design\33-block5-demo-safety-config.yaml"
$Port     = 4827

New-Item -ItemType Directory -Force $Evidence | Out-Null
New-Item -ItemType Directory -Force $Scratch | Out-Null
Set-Location $Repo
```

Do not edit the reviewed config in place. If the demo core needs values not present
in it, copy it into `$Evidence`, add only measured demo-core values, and retain the
diff. Stop if `authorization-map` is not complete before any acquisition step.

---

## Demo-core gate

Start Micro-Manager with `MMConfig_demo.cfg` and the pycro-manager ZMQ bridge on
port 4827 before D1. These steps may acquire only synthetic demo-camera frames.

### D1. Identity, non-hardware suite, and complete authorization map

```powershell
git rev-parse HEAD > "$Evidence\head.txt" 2>&1
git status --short > "$Evidence\status.txt" 2>&1
python -V > "$Evidence\python.txt" 2>&1
python -m pytest -q > "$Evidence\pytest.txt" 2>&1
python -m microclaw --port $Port --safety-config $Config authorization-map > "$Evidence\authorization-map.json" 2>&1
```

**Expected observable:** clean worktree; the reviewed branch commits are present;
the non-hardware suite has the handback count; the map is `complete` and includes
`acquisition-tool:run_adaptive_survey`.

**Stop condition:** any test failure, dirty file not accounted for, incomplete map,
or a real-hardware adapter/device in what is believed to be the demo config.

### D2. Saved-hook capability stripping is live

In a fresh Microclaw session, ask the agent to show, review, and save a generated
hook whose constructor names optional `ctrl` and `guard`, and whose legacy
`image_process_fn` calls `event_queue.put(...)`. Record the displayed source,
warnings, confirmation, manifest entry, and pinned sha256. Then run one one-frame
`run_adaptive_survey` over a single demo position with that saved hook.

Save the complete JSON history as:

```powershell
Copy-Item "<session history>" "$Evidence\d2-capability-strip.json"
```

**Expected observable:** neither ctrl nor guard is injected; the attempted queue
access raises with the saved-hook capability message; the acquisition surfaces the
failure; the trusted parent writes a `hook_failure` record with the reason. A quiet
no-op is a failure.

**Stop condition:** the hook receives either object, its put reaches an event source,
the exception is swallowed, the log is absent, or any non-demo device moves.

### D3. Typed ContinueSurvey and StopSurvey

Save a new `analyze_frame(image, metadata)` hook returning JSON-safe measurements
and typed actions. It must return `ContinueSurvey` for the first N frames and
`StopSurvey` before the final planned tile. Run it with `run_adaptive_survey` over
at least four named demo positions. Retain the hook source/hash, tool result,
dataset inspection, and hook log.

**Expected observable:** only the seed is initially submitted; each accepted
ContinueSurvey adds exactly the next planned tile; StopSurvey ends cleanly;
`tiles_planned`, `frames_acquired`, and dataset image count reconcile;
`stopped_early` is true. Every action has a parent-written `accepted` record and
reason, and measurements are separately recorded.

**Stop condition:** a ghost exposure, an arbitrary coordinate, a missing/forgeable
decision record, frame-count mismatch, stall, abort instead of clean stop, or an
interactive prompt from the callback thread.

### D4. Reservation, planned-position, guard, and runner-support refusals

Run separate minimal surveys using saved `analyze_frame` hooks that propose:

1. enough `AcquireAt` actions to exceed the planned-grid frame reservation;
2. `AcquireAt` for a label/index absent from the planned grid;
3. `AcquireAt` for a planned event that violates a deliberately narrower copied
   demo safety profile; and
4. one valid but unsupported `SetExposure` (repeat with MoveStage or
   RequestAutofocus if desired, but one is sufficient for the live gate).

Keep these separate so each refusal is attributable.

**Expected observable:** no refused action reaches hardware. The parent log says,
respectively, outside committed reservation, not a planned position, SafetyGuard
refusal, and unsupported-by-run_adaptive_survey. No case prompts interactively.

**Stop condition:** any extra frame, motion/exposure/autofocus call, prompt, missing
reason, or refusal attributed only to generated-hook text instead of parent audit.

### D5. Complete design/26 Run A on the demo core

Follow `design\26-field-spike-prompts.md` Run A A1 through A3 without abbreviating
the prompts: `snr_observer` fixed tile survey, `rank_hook_log`,
`validate_positions`, `save_position_list`, full revisit, and
`compare_revisit_frames`. Store everything under `$Scratch\run-a-demo` and copy the
session history plus `inspect_artifacts` manifest to `$Evidence`.

```powershell
certutil -hashfile "$Evidence\<run-a-history>.json" SHA256 > "$Evidence\run-a-history.sha256.txt" 2>&1
```

**Expected observable:** the trusted pre-coded `snr_observer` path behaves as before;
fixed-survey image and completed-log counts match; ranking is deterministic;
positions pass the guard before save/revisit; the saved list and revisit dataset are
retained; comparison reports its plumbing metrics without an object/biology claim.

**Stop condition:** dropped images/records, changed trusted-hook behavior, a parent
gate bypass, non-deterministic ranking, unsafe position, incomplete revisit, or any
claim of biological/object-level validation.

---

## Rig gate

### R1. M5 Run A regression and retained-baseline hash verification

Do this only on M5 with its reviewed safety config. Repeat design/26 Run A A1–A3
exactly, using the same fixed survey and retained comparison criteria as the
2026-07-20 run in `OneDrive\Microclaw\microclaw-json-histories` (`*_run_a.json`
plus `run-a\`). Do not overwrite the retained baseline.

Before using any retained file or directory artifact as a baseline, enumerate it
and record a sha256 for every file:

```powershell
$Baseline = "<OneDrive>\Microclaw\microclaw-json-histories"
Get-ChildItem -Path $Baseline -File -Recurse | Sort-Object FullName | ForEach-Object {
    $Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName
    "$($Hash.Hash)  $($_.FullName)"
} > "$Evidence\retained-run-a-sha256.txt" 2>&1
```

Record OneDrive placeholder/availability state separately:

```powershell
Get-ChildItem -Path $Baseline -Force -Recurse | Select-Object FullName,Length,Attributes,LastWriteTime > "$Evidence\retained-run-a-inventory.txt" 2>&1
```

If a placeholder cannot be hydrated/read or an artifact is missing, write
`HASH NOT ESTABLISHED` with its path and reason. Do not imply it was verified and
do not use it as a quantitative baseline.

Run A itself follows `design\26-field-spike-prompts.md` A1, A2, and A3. Retain the
new history, hook log, ranking, validated/saved positions, fixed survey, revisit
dataset, comparison output, and an `inspect_artifacts` manifest with hashes.

**Expected observable:** compared with hash-established 2026-07-20 artifacts, record
counts and image retention reconcile; ranking is deterministic; every revisit
position passes the current guard; the whole saved list is revisited; revisit
accuracy metrics are reported with method/units and without a biology/object claim.
The trusted `snr_observer` behavior is unchanged by the saved-hook boundary.

**Stop condition:** baseline hash cannot be established for an artifact needed by a
comparison, any record/image is dropped, ranking changes between identical reads,
guard validation is bypassed, revisit is incomplete/inaccurate beyond the retained
criterion, or the acquisition differs from Run A rather than merely reporting a
platform/version delta. Stop and investigate; do not merge on an unexplained delta.

## Final verdict

The gate passes only when D1–D5 and R1 each have a dated verdict, every refusal has
a parent-written reason, trusted Run A is unchanged, and all evidence limitations
are stated. Report demo-core and M5 findings separately. This remains plumbing and
safety-boundary evidence only.
