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

**Substitute every `<...>` placeholder with a real path before pasting a prompt.**
In the 2026-07-27 demo run the agent passed a literal `<workspace>` through to the
runner and the acquisition failed with `OSError [WinError 123]`.

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

Two runs. D2a is a negative control that must fail; D2b must succeed, because the
parent-owned log can only be checked on a run that completes.

`save_dir` and `log_path` must be inside the configured workspace and must already
exist — Block 5's first demo run died on exactly that.

```powershell
$Hooks = "$env:USERPROFILE\.microclaw\hooks"
Copy-Item "$Hooks\manifest.json" "$Evidence\d2-manifest-before.json" -ErrorAction SilentlyContinue
```

**Note on the runner.** D2 uses `run_adaptive_timelapse`, not
`run_adaptive_survey`: a legacy `image_process_fn` hook cannot propose
`ContinueSurvey`, and the adaptive survey runner now refuses it up front for that
reason. Batched runners have no `progress` object and are unaffected.

#### D2a — capability stripping (paste into a fresh microclaw session)

> I am running gate step D2a from `design/32-block7-gate-prompts.md`. This is a
> deliberate **negative control** for the Block 7 saved-hook capability boundary:
> the hook below is written to FAIL, and its failure is the evidence. Do not
> improve it, do not make it safe, and do not substitute `analyze_frame` — I need
> the legacy `image_process_fn` path specifically. Save it exactly as written.
>
> First call `list_hooks`. Then show me this source, run the lint, and after I
> confirm, save it with `generate_and_save_hook` under the name
> `d2_capability_probe`, description "D2 negative control: probes for stripped
> saved-hook capabilities; not for real use", source `claude_generated`:
>
> ```python
> class CapabilityProbeHook:
>     """D2 negative control. Probes for capabilities that Block 7 must strip."""
>
>     def __init__(self, ctrl=None, guard=None, log_path=None):
>         self.ctrl = ctrl
>         self.guard = guard
>         self.log_path = log_path
>
>     def image_process_fn(self, image, metadata, event_queue):
>         leaked = []
>         if self.ctrl is not None:
>             leaked.append("ctrl=" + type(self.ctrl).__name__)
>         if self.guard is not None:
>             leaked.append("guard=" + type(self.guard).__name__)
>         if self.log_path is not None:
>             leaked.append("log_path=" + str(self.log_path))
>         if leaked:
>             raise RuntimeError("D2a FAIL capability leak: " + ", ".join(leaked))
>         event_queue.put({"axes": {"position": "d2_ghost"}, "x": 0.0, "y": 0.0})
>         raise RuntimeError("D2a FAIL: event_queue.put returned without raising")
> ```
>
> Report the exact lint warnings, the manifest entry, and the pinned sha256.
>
> Set the exposure to 5 ms with `set_exposure` first — `run_adaptive_timelapse`
> has no exposure parameter. Then run **one** frame with that hook:
> `run_adaptive_timelapse(n_frames=1, interval_s=0,
> hook_strategy="d2_capability_probe", save_dir="<workspace>/block7/d2a",
> log_path="<workspace>/block7/d2a/hook.json")`.
> I expect this acquisition to fail. Report verbatim the exception text that
> reaches you, then call `read_hook_log` on that log path and show me every record.
> Do not retry, do not work around the failure, and do not modify the hook.

D2a passes only if all four hold: neither `D2a FAIL` message appears; the error is
the `DeniedEventQueue` text (`Saved hooks cannot access pycro-manager's event
queue; return typed actions from analyze_frame instead`); the exception reaches
the caller rather than being swallowed; and `read_hook_log` returns a
`hook_failure` record carrying that reason.

**Do not require stage coordinates in D2's records.** A single-position
timelapse stamps no position axis and no `XPosition_um_Intended`, so
`position: null` with no coordinate keys is correct here, not a defect. The
coordinate-stamping contract is exercised by D3 and D5, which are
multi-position.

#### D2b — the parent owns a successful legacy hook's log

> Still in gate step D2. Save this second hook as `d2_legacy_benign`, description
> "D2 control: a legacy saved hook that succeeds, to check parent-owned logging",
> using the same show → confirm → `generate_and_save_hook` flow:
>
> ```python
> class BenignLegacyHook:
>     """D2b control: succeeds, discards its second frame, writes no log itself."""
>
>     def __init__(self):
>         self.seen = 0
>
>     def image_process_fn(self, image, metadata, event_queue):
>         self.seen += 1
>         if self.seen == 2:
>             return None
>         return image, metadata
> ```
>
> Then run `run_adaptive_timelapse(n_frames=2, interval_s=0,
> hook_strategy="d2_legacy_benign", save_dir="<workspace>/block7/d2b",
> log_path="<workspace>/block7/d2b/hook.json")`, exposure already 5 ms.
> Afterwards call
> `read_hook_log` on that path and show every record verbatim, and tell me whether
> the log file existed. Do not summarize the records — print them.

D2b passes only if: the acquisition completes; the log file exists; it holds
exactly two `event: "legacy_hook_frame"` records, one `outcome: "retained"` and
one `outcome: "discarded"`. As above, `position: null` and absent coordinates are
correct for a single-position timelapse; D3 and D5 are where the design/23 F2
coordinate contract is checked.

**Expected observable — how D2a's failure should surface.** In pycro-manager
1.0.2, `acquisition_superclass._call_image_process_fn` catches the processor
exception and calls `acq.abort(...)`; `_check_for_exceptions` re-raises it to the
caller, and the survey generator observes `acq_finished()` on its next 0.05 s
poll. A correct D2a is therefore an **aborted acquisition** carrying both a
`hook_failure` and an `aborted` record — not a stall, and not a swallowed
exception. Read a prompt abort as a pass, not a defect.

**Stop condition:** the hook receives ctrl, guard, or a log path; its `put`
reaches a real event source; the exception is swallowed; the abort is mislabeled
as a stall; any required record is absent; D2b returns a `log_path` with no file
behind it; or any non-demo device moves.

```powershell
Copy-Item "<session history>" "$Evidence\d2-capability-strip.json"
Copy-Item "$Hooks\manifest.json" "$Evidence\d2-manifest-after.json"
certutil -hashfile "$Hooks\d2_capability_probe.py" SHA256 > "$Evidence\d2a-hook.sha256.txt" 2>&1
certutil -hashfile "$Hooks\d2_legacy_benign.py"    SHA256 > "$Evidence\d2b-hook.sha256.txt" 2>&1
```

Afterwards remove both probes from `$Hooks` and from `manifest.json`. The manifest
pins what the operator consented to; it must not carry gate scaffolding, and a
hook whose name is a failure assertion must not stay in the registry.

### D3. Typed ContinueSurvey and StopSurvey

D3 tests two things at once, and they must be kept distinct in the evidence: that
the mechanism works, and that the **agent-facing documentation is good enough to
produce a working hook unaided**. So the agent authors the hook from its own
documentation; a reference implementation is given below only as a fallback.

Define five demo positions first (a 5-tile line at 50 µm spacing around the
current stage position is enough), either in the MM position list or as explicit
`positions` dicts.

> I am running gate step D3 from `design/32-block7-gate-prompts.md`. First call
> `get_hook_documentation` and `list_hooks`, and tell me what contract a **saved**
> hook must satisfy to steer `run_adaptive_survey`. Then write that hook yourself
> from the documentation — do not ask me for the source.
>
> It must: implement `analyze_frame(image, metadata)`; record a simple JSON-safe
> per-tile measurement (mean intensity is fine); return `ContinueSurvey` while
> fewer than `max_tiles` frames have been analyzed; and return `StopSurvey` on the
> `max_tiles`-th frame, before the planned grid is exhausted. Take `max_tiles` as a
> constructor parameter. Show me the full source and the lint result, and save it
> as `d3_tile_score` after I confirm.
>
> Then run it over the five positions with `run_adaptive_survey`, protocol
> `timelapse`, `protocol_params={"n_frames": 1, "interval_s": 0}`, 5 ms exposure,
> `hook_params={"max_tiles": 3}`, saving under `<workspace>/block7/d3` with the
> log beside it. Before running, state how many frames you expect to be acquired
> out of how many planned, and why.
>
> Afterwards report, without summarizing away the numbers: the tool result's
> `positions`, `frames_acquired`, `stopped_early`, and reservation fields; the
> full `read_hook_log` output; and the image count in the saved dataset. Tell me
> explicitly whether the planned count, the acquired count, the number of accepted
> actions, and the dataset image count reconcile with each other.

If the agent cannot produce a savable, working hook from its own documentation,
**that is a D3 finding about `hook_docs.py` / the tool schema — record it before
falling back.** Then use this reference implementation and note in the evidence
that the fallback was needed:

```python
class TileScoreHook:
    """D3 reference: score each tile, continue to the budget, then stop."""

    def __init__(self, max_tiles=3):
        self.max_tiles = max_tiles
        self.seen = 0

    def analyze_frame(self, image, metadata):
        import numpy as np
        from microclaw.hook_decisions import ContinueSurvey, HookResult, StopSurvey

        self.seen += 1
        action = ContinueSurvey() if self.seen < self.max_tiles else StopSurvey()
        return HookResult(
            {"tile": self.seen, "mean_intensity": round(float(np.mean(image)), 3)},
            [action],
            analyzer="d3_tile_score",
            analyzer_version="1",
            parameters={"max_tiles": self.max_tiles},
        )
```

With five planned positions and `max_tiles=3` the arithmetic is fixed: the seed is
dispatched, two `ContinueSurvey` actions are accepted, the third frame returns
`StopSurvey`, and the run ends having acquired **3 of 5** planned frames.

**Expected observable:** only the seed is initially submitted; each accepted
ContinueSurvey adds exactly the next planned tile, in planned order; StopSurvey
ends cleanly; planned/acquired counts, accepted-action count, and dataset image
count reconcile at 3 of 5; `stopped_early` is true. Every action has a
parent-written `accepted` record with a reason, and each frame's measurements are
recorded separately under the `microclaw.analysis-observation/v1` envelope with
`status: "unverified"` — an untrusted hook cannot self-assert `observed`.

**Stop condition:** a ghost exposure, an arbitrary coordinate, a missing/forgeable
decision record, frame-count mismatch, stall, abort instead of clean stop, or an
interactive prompt from the callback thread.

### D4. Reservation, planned-position, guard, and runner-support refusals

Four refusal paths, run separately so each is attributable. Cases 1, 2 and 4 are
saved `analyze_frame` hooks; case 3 is different and is explained below.

Each hook is minimal and terminates on its own — none needs an abort. Save each
one through the normal show → confirm → `generate_and_save_hook` flow, run it, and
call `read_hook_log` immediately afterwards.

**Case 1 — exceed the committed reservation.** Two planned positions, a hook that
always asks for tile 0:

```python
class OverReserveHook:
    def analyze_frame(self, image, metadata):
        from microclaw.hook_decisions import AcquireAt, HookResult
        return HookResult({"case": 1}, [AcquireAt(0)])
```

Run over exactly **two** positions. The first `AcquireAt(0)` is accepted (revisiting
a planned tile is allowed); the second is refused, because the reservation covers
the planned grid and both of its frame slots are now spent. Two frames total, then
the survey completes on its own.

**Case 2 — a position that is not in the planned grid.** One run covers both the
index and the label path:

```python
class UnknownPositionHook:
    def analyze_frame(self, image, metadata):
        from microclaw.hook_decisions import AcquireAt, HookResult, StopSurvey
        return HookResult(
            {"case": 2}, [AcquireAt(99), AcquireAt("no_such_tile"), StopSurvey()]
        )
```

Run over three positions. Both `AcquireAt` actions are refused with *different*
reasons — the index one names the planned survey, the label one says no planned
position carries that label — and the `StopSurvey` then ends the run after a single
frame.

**Case 3 — the guard refusal is an UP-FRONT refusal, not an in-dispatch one.**
The original form of this step ("propose `AcquireAt` for a planned event that
violates a narrower profile") **cannot be executed**, and the gate should not
pretend otherwise. `_acquire_survey_with_detector` calls `guard.check_xy` on every
survey position before it builds a single event
(`microclaw/tools.py`, "check every survey point up front"), so a narrowed profile
refuses the whole survey before any hook runs. The in-dispatch guard check inside
`UntrustedHookAdapter._dispatch` is therefore unreachable with a static config; it
is defence-in-depth for a future action type that carries operator-supplied
coordinates, and it is covered by the unit test
`test_guard_violation_is_refused_and_logged`. Record it as unit-tested, **not**
live-verified, and do not claim otherwise in the verdict.

What to run instead is the safety property that does matter, and it is stronger:

```powershell
Copy-Item $Config "$Evidence\d4-narrowed-profile.yaml"
```

Narrow one XY stage bound in the copy so that one of the planned tiles falls
outside it, then run the same survey with `--safety-config` pointing at the copy.
Retain the diff between the two profiles.

**Expected:** the survey is refused before any exposure, naming the offending
position; no dataset directory is created; the stage does not move.

**Case 4 — a valid but unsupported action.**

```python
class UnsupportedActionHook:
    def analyze_frame(self, image, metadata):
        from microclaw.hook_decisions import HookResult, SetExposure, StopSurvey
        return HookResult(
            {"case": 4}, [SetExposure(exposure_ms=20.0), StopSurvey()]
        )
```

Run over three positions. `SetExposure` parses as a valid action and is then
refused as unsupported by this runner — the closed union stays complete, and the
camera exposure must be unchanged afterwards. Confirm that separately by calling
`get_exposure` before and after the run and comparing the two values.

**Expected observable:** no refused action reaches hardware, and the four reasons
are distinct in the parent log — outside committed reservation; not in the planned
survey / no position carries that label; the up-front guard refusal from case 3;
and `unsupported-by-run_adaptive_survey`. No case prompts interactively. Case 1
acquires exactly 2 frames; cases 2 and 4 acquire exactly 1.

**Stop condition:** any extra frame, any motion/exposure/autofocus call, an
interactive prompt, a missing or generic reason, two different refusals sharing one
reason string, or a refusal attributed only to generated-hook text rather than to
the parent audit record.

### D5. Complete design/26 Run A on the demo core

This is the regression step: `snr_observer` is a reviewed built-in, so Block 7 must
not have changed it at all. It is also the only end-to-end exercise of the trusted
path available without M5.

Follow `design\26-field-spike-prompts.md` Run A A1 through A3 without abbreviating
the prompts. Store everything under `$Scratch\run-a-demo`. Concrete substitutions
for the demo core, replacing that document's `<...>` placeholders:

- **Grid:** 3 × 3 at 50 µm spacing, centred on the current stage position; exposure
  5 ms; `protocol="timelapse"` with `n_frames=1, interval_s=0` — one frame per tile,
  nine frames total.
- **Threshold:** the demo core has no calibration artifact, so supply **no**
  `min_snr` and no `calibration_path`. The hook then resolves the package fallback
  and records `min_snr_source: "package_default_uncalibrated"` in every observation.
  Check that string is present — an honest provenance record is part of what this
  step verifies. Do not invent a calibration file to make the number look better.
- **Budget:** k = 3 for the A2 revisit.

**Read these two limits before judging the output.**

1. The demo camera returns synthetic frames, so SNR values, the ranking they
   produce, and any focus-validity counts carry **no** information about a
   specimen. What D5 checks is that nine tiles produce nine records with correct
   positions and coordinates, that ranking is deterministic, and that the top-k
   coordinates survive `validate_positions` and round-trip through
   `save_position_list`. Do not report a demo SNR range as a result.
2. `compare_revisit_frames` reports micrometres only when a current stage-camera
   affine exists, and registers synthetic frames that may share no stable
   structure. Treat a poor correlation or a large apparent translation on the demo
   core as **uninformative, not as a failure**. What must hold is that the tool
   runs, pairs each revisit page with the right source page, and states its method
   and units. Real revisit accuracy is R1's job on M5.

A step the Run A prompts do not spell out: `compare_revisit_frames` takes
`source_tiff` and `revisit_tiff`, so both datasets must go through
`export_dataset_as_tiff` first. Do that before A3's comparison.

Determinism is checked by replay, not by inspection:

> Call `rank_hook_log` on the same completed Run A log a second time and show me
> both orderings in full. Confirm the ordering and the selected top-k are
> byte-identical, and state the tie-break rule you applied. Do not re-acquire
> anything.

```powershell
Copy-Item "<session history>" "$Evidence\d5-run-a-demo.json"
certutil -hashfile "$Evidence\d5-run-a-demo.json" SHA256 > "$Evidence\d5-run-a-demo.sha256.txt" 2>&1
```

Also copy the `inspect_artifacts` manifest, the hook log, the saved position list,
and both dataset paths' hashes into `$Evidence`.

**Expected observable:** the trusted pre-coded `snr_observer` path behaves exactly
as before — nine planned, nine acquired, nine observation records, no duplicate or
missing position, every record carrying position and intended stage coordinates and
`min_snr_source: "package_default_uncalibrated"`; every image retained; the two
`rank_hook_log` calls identical; the top-3 coordinates passing `validate_positions`
and round-tripping through `save_position_list`; three revisit frames acquired and
no more; `compare_revisit_frames` running and pairing pages correctly.

**Stop condition:** dropped images or records, any change in trusted-hook behaviour,
a parent gate bypass, ranking that differs between two reads of the same log, an
unsafe position reaching the revisit, an incomplete revisit, a fabricated
calibration to avoid the uncalibrated fallback, or any claim of biological or
object-level validation from synthetic frames.

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

---

## Demo-core gate results — 2026-07-27

Evidence: `microclaw_block7/` (histories, hook logs, authorization map, pytest
output, acquisitions under `block7/`). Demo machine, Windows, Python 3.12.13,
`MMConfig_demo.cfg` over the ZMQ bridge.

| Step | Verdict |
|---|---|
| D1 | **FAIL** — one test failure, see below |
| D2a | **PASS** |
| D2b | **PASS** |
| D3 | **PASS**, including the documentation-quality half |
| D4 cases 1, 2, 4 | **PASS** |
| D4 case 3 | **NOT RUN** — no narrowed profile was produced |
| D5 | **PASS**, with two procedural findings |

### D1 — FAIL: `test_generate_save_and_use_custom_hook`

`1 failed, 1037 passed, 21 skipped`. The failure is real and is a Block 7
consequence, not flake. That test saves a legacy `MeanLogger` hook that writes its
own log through `self.log_path` and asserts every entry carries `mean`. Block 7
strips `log_path` from untrusted hooks and the parent writes `legacy_hook_frame`
records instead, so `all("mean" in entry)` is false.

The test never runs off-rig — it needs the `headless_mm` fixture — which is why the
implementer's and the coordinator's runs did not see it. **A legacy saved hook's
own measurements are no longer recorded anywhere.** The parent records that a frame
was retained or discarded; the science the hook computed is dropped. Resolution is
tracked as a Block 7 open decision, not as a gate re-run.

### Evidence-integrity note

`head.txt` records `980bf43`, but `d4-history.json` contains the refusal string
`no planned survey position is labelled 'no_such_tile'`, which only exists from
`259eaec` onward. The machine therefore ran code newer than the recorded identity.
Re-capture `head.txt` immediately before the runs, not at the start of the session.

### D4 case 3 was not run

`d4-narrowed-profile.yaml` differs from the reviewed config only in line endings
and `workspace_dir`; no stage bound was narrowed, and no run appears in
`d4-history.json`. The up-front-refusal property remains unverified on the demo
core. It is separately covered by
`test_an_out_of_bounds_survey_position_is_refused_before_any_acquisition`.

### D5 finding — `save_position_list` saves the whole MM list, not the selection

The agent marked the k=3 tiles and called `save_position_list`; the resulting
`.pos` holds **8** entries, because D3's five tiles were still in MM's position
list. The saved artifact therefore misrepresents the Run A selection, and anything
that later loads it would acquire five positions nobody selected.

**Before R1 on M5:** clear the MM position list before marking the top-k, and
assert the saved `.pos` contains exactly k entries. Treat a count mismatch as a
stop condition — on a rig this is a dose-integrity problem, not a cosmetic one.

### D5 limit — the demo core cannot exercise SNR ordering

All nine tiles returned identical statistics (`snr` 0.95, `mean_intensity`
330.78125), so the ranking was decided entirely by the position-label tie-break.
Determinism was confirmed (two `rank_hook_log` calls, identical ordering), but
ordering *by SNR* is untested until R1. Do not report the demo ranking as evidence
that SNR ranking works.

### Cross-cutting finding — the manifest hash cannot be verified externally

`hook_manager.save_hook` pins `sha256(code)` where `code` is LF-normalized text,
while Python's text-mode write puts CRLF on disk. Measured on both D2 hooks:

    d2_capability_probe   manifest c8dbf833…   certutil 801419da…
    d2_legacy_benign      manifest e68fa85c…   certutil c6faee98…

`sha256(LF)` reproduces the manifest value exactly and `sha256(CRLF)` reproduces
certutil's, so this is newline translation, not tampering. TOCTOU detection still
works, because `load_hook_class` normalizes on read. But the pinned hash cannot be
reproduced by any standard file-hashing tool on the platform the rig runs on, so it
is not independently auditable. Pre-existing, not a Block 7 regression; recorded
for the design gate.
