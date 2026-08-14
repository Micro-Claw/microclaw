# design/26 — field-spike prompts

The validation is split so acquisition plumbing, custom integration, and few-shot
learning do not fail simultaneously:

- **Run A — pre-built SNR canary:** validate fixed-survey acquisition, versioned logs,
  offline whole-tile ranking, guards, and revisit accuracy.
- **Run B — generated adapter:** validate the three-question custom-analysis workflow.
- **Run C — few-shot classifier:** only if needed, test whether reviewed examples can
  learn the biological concept. Use the measured classical descriptor plus logistic
  probe; LDA has no demonstrated advantage here.

Run A is a positive control, not validation of custom analysis or learned recognition.
SNR is a whole-field score and must not be assigned to an arbitrary object centroid.

Run microclaw with history saving enabled. Replace every `<...>` placeholder. Keep the
first grid small. Return the history JSON, hook log, saved selected-position list, and
the survey/revisit paths.

## Run A — pre-built SNR canary

### A1. Inspect and preflight; do not acquire

> I want to run design/26 Run A using the pre-coded `snr_observer` hook. First call
> `list_hooks` and confirm that strategy is available. Prepare—but do not start—a
> `<rows>` by `<cols>` fixed tile survey with `<step_um>` um spacing, centered at the
> current stage position. Use `run_tile_acquisition` with protocol `timelapse`, exactly
> one frame per tile (`n_frames=1`, `interval_s=0`), `hook_strategy="snr_observer"`,
> and `hook_params={"calibration_path":"<replicated-control calibration artifact>"}`
> (or an explicitly provisional `min_snr` when no calibration exists). Save the dataset under `<workspace/run-a>` and
> the hook log beside it. Inspect the current hardware state and validate the proposed
> footprint with `validate_positions` (report rejections as the guard words them; never clip), plus exposure,
> channel, focus, grid footprint, and estimated duration. State the exact exposure
> count and ask for confirmation. This hook is observation-only: it must not filter,
> stop, enqueue events, choose a threshold, or alter the grid.

Check that the proposed footprint, illumination, and exposure count are safe. Then:

> I confirm exactly that fixed survey. Run it now. Read the hook log afterward and
> report planned versus acquired frames; number of observation records; duplicate or
> missing positions; any errors; SNR range; count below the focus-validity gate; and
> per-tile analysis time. Confirm that every image was retained and that the dataset
> and log paths exist. Do not revisit anything yet.

Stop and return the history if record count, coordinates, or image retention is wrong.

### A2. Rank completed records offline

> Using only the completed Run A hook log, call `rank_hook_log` and rank all tiles deterministically by SNR.
> This is offline whole-tile ranking: do not call an analyzer, acquire images, choose an
> absolute threshold, or invent object centroids. Show budgets k=1, 3, 5 and `<other
> useful k>`, including position label, recorded stage XY, SNR, focus-validity flag,
> saturation, and rank. State that the ranking key is descending SNR with position label
> as the deterministic tie-break. Propose k=`<k>` but wait for my choice. Keep the full
> table in the saved history; do not claim a `ranked_positions.json` artifact exists,
> because Run A has no generic tool that can write that richer artifact yet.

Choose a small budget after inspecting the table:

> Use budget k=`<k>`. Call `validate_positions` to resolve the top-k recorded tile coordinates against the current
> XY/Z guards. Reject rather than clip unsafe positions. Save the accepted whole-tile
> coordinates with `save_position_list` under the Run A directory, show the exact
> positions and guard outcomes, and wait for my explicit acquisition confirmation.

### A3. Revisit the whole tiles

> I confirm acquisition of exactly the displayed guarded top-k whole-tile positions,
> using `<one-frame timelapse or z-stack parameters>`. Acquire no replacements for
> rejected positions and no more than k positions. Save the revisit dataset under the
> Run A directory and return to `<agreed safe position or grid center>` afterward.

Then measure the canary:

> Compare the revisit frames with their source survey tiles using `compare_revisit_frames`. Report acquired versus
> selected positions, label/coordinate mismatches, translation or revisit error in
> pixels and micrometres where measurable, survey/ranking/revisit durations, and any
> visible drift or bleaching. Replay the ranking from the stored hook log without a
> network or adjudicator and confirm the full ordering and selected positions. List
> every available artifact path and sha256 from `inspect_artifacts`, saving a manifest. State explicitly that Run A validates plumbing only,
> not object attribution, custom integration, or biological classification.

## Run B — generated adapter for an existing analysis

Start this only after Run A's record counts, coordinates, ranking, and revisit are
sound.

> I want to perform design/26 Run B. My existing analysis workflow is
> `<package/app/notebook/script/project and path or official URL>`. I run it today by
> `<short description>`. A representative input is `<path>`, and its known result is
> `<path or biological description>`. The biological target is `<plain-language
> target>`. The microscope should initially only save a fixed survey and log raw
> results. Follow the custom-analysis flow in `get_hook_documentation`: investigate the
> installed workflow and primary sources. First identify which local execution boundary
> applies: direct import in Microclaw's environment, another Python interpreter, a
> persistent local worker, an installed CLI/executable, or completed-survey batch
> processing. Reproduce the example on a copied input and measure process/environment
> startup separately from marginal per-image and batch latency. Then summarize the exact
> input/output/provenance contract and recommend per-image, worker, or two-pass batch
> execution from those measurements. Fixture-test an observation-only saved-hook adapter:
> a plain class exposing `analyze_frame(self, image, metadata)` that returns a
> `HookResult`, which must **not** inherit `HookBase` and must **not** take `log_path`.
> Mark unresolved semantics `unverified`. Show the complete
> source and lint findings; do not save until I explicitly approve.

That adapter contract is **enforced in code, not merely preferred.** `log_analysis`
exists only on `HookBase` (`microclaw/hooks.py:171`), the trusted parent owns the audit
record, and since block 45 `generate_and_save_hook` refuses a `HookBase`/`log_path` hook
*before writing it*. A saved adapter's observations reach the same
`microclaw.analysis-observation/v1` envelope through the parent
(`microclaw/hook_decisions.py:750`). This prompt asked for the refused shape until
2026-08-12; `design/26-implementation.md` §Decision has been right since block 7.

Review the proposed boundary before approving it. Require the exact interpreter or
executable path, environment identity, installed version, invocation/arguments, working
directory, model/project/config paths and sha256, axes/channel/dtype preprocessing,
raw-output semantics, startup and marginal/batch latency, CPU/GPU needs, cleanup,
timeouts, concurrency/thread behavior, and bounded failure policy. A direct import is
the simplest case, not the required one. Do not accept per-tile `conda run`, environment
activation, application/JVM startup, or heavyweight model loading unless the measured
cost fits the acquisition budget. Prefer one persistent local worker for justified
online use or one invocation over the saved survey.

If any package, executable, environment, model, or project is missing, stop adapter
generation and present a separate pinned installation plan. It must identify downloads,
licenses, environment location, hardware requirements, versions and artifact hashes,
and require explicit authorization. Do not silently install or download anything as
part of writing the hook.

For ilastik specifically, use the already measured contract rather than rediscovering a
generic one: a user-trained and hash-verified `.ilp`, pinned ilastik version and headless
CLI, and one batched subprocess over the saved survey between passes. Do not launch
ilastik per tile. Verify this project's channel/axis mapping, decimation, probability-map
export and pooling against the supplied example; the prior spike established plumbing,
not biological accuracy.

After approving the exact source, repeat Run A's fixed-survey and artifact checks with
the saved adapter. Object-level ranking/revisit is permitted only if the verified raw
output attributes a box, mask, or centroid to the score. Otherwise rank/revisit whole
fields or report the output unresolved.

The Run B evidence must include the selected execution-boundary rationale, measured
startup and marginal/batch timings, exact environment/executable and artifact identities,
the fixture input/result, adapter source/hash, normalized hook log, and every observed
failure or cleanup event. A common observation schema is not evidence that all analyzers
share one safe invocation mechanism.

## Run C — optional reviewed few-shot classifier

Do not start Run C merely because SNR is biologically simplistic. Start it when the
real target requires learning from examples and Run B does not already supply a useful
classifier.

Run C uses a blind saved survey, reviewed positive object crops, and reviewed candidate
negatives. Fit the classical/enriched descriptor with a logistic probe, score all
stored attributable objects, persist verdicts with adjudicator/timestamp/source hashes,
and select an explicit top-k budget offline. Do not silently label unreviewed crops
negative. Do not substitute LDA without a benchmark showing it improves real held-out
precision at the chosen budget.

Before Run C drives a revisit, require `verdicts.json`, a deterministic scorer artifact,
and `ranked_positions.json` containing proposal bounds/centroids, source tiles, input
and detector hashes, calibration identity, guard outcomes, and selected coordinates.

## Evidence to return

For Run A, return:

- `*_microclaw_history.json`;
- the `snr_observer` hook log;
- the saved selected-position list (the history carries the full ranking table);
- survey and revisit dataset paths (and representative images if practical).

The history and hook log together are required to verify record attribution and replay.
The richer `ranked_positions.json` remains a Run C implementation/acceptance artifact.
