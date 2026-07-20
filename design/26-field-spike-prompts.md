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
> and `hook_params={"min_snr": 3.0}`. Save the dataset under `<workspace/run-a>` and
> the hook log beside it. Inspect the current hardware state, XY bounds, exposure,
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

> Using only the completed Run A hook log, rank all tiles deterministically by SNR.
> This is offline whole-tile ranking: do not call an analyzer, acquire images, choose an
> absolute threshold, or invent object centroids. Show budgets k=1, 3, 5 and `<other
> useful k>`, including position label, recorded stage XY, SNR, focus-validity flag,
> saturation, and rank. State that the ranking key is descending SNR with position label
> as the deterministic tie-break. Propose k=`<k>` but wait for my choice. Keep the full
> table in the saved history; do not claim a `ranked_positions.json` artifact exists,
> because Run A has no generic tool that can write that richer artifact yet.

Choose a small budget after inspecting the table:

> Use budget k=`<k>`. Resolve the top-k recorded tile coordinates against the current
> XY/Z guards. Reject rather than clip unsafe positions. Save the accepted whole-tile
> coordinates with `save_position_list` under the Run A directory, show the exact
> positions and guard outcomes, and wait for my explicit acquisition confirmation.

### A3. Revisit the whole tiles

> I confirm acquisition of exactly the displayed guarded top-k whole-tile positions,
> using `<one-frame timelapse or z-stack parameters>`. Acquire no replacements for
> rejected positions and no more than k positions. Save the revisit dataset under the
> Run A directory and return to `<agreed safe position or grid center>` afterward.

Then measure the canary:

> Compare the revisit frames with their source survey tiles. Report acquired versus
> selected positions, label/coordinate mismatches, translation or revisit error in
> pixels and micrometres where measurable, survey/ranking/revisit durations, and any
> visible drift or bleaching. Replay the ranking from the stored hook log without a
> network or adjudicator and confirm the full ordering and selected positions. List
> every available artifact path and sha256. State explicitly that Run A validates plumbing only,
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
> installed workflow and primary sources, reproduce the example, summarize the exact
> input/output/provenance contract, and fixture-test an observation-only HookBase
> adapter using `self.log_analysis`. Mark unresolved semantics `unverified`. Show the
> complete source and lint findings; do not save until I explicitly approve.

After approving the exact source, repeat Run A's fixed-survey and artifact checks with
the saved adapter. Object-level ranking/revisit is permitted only if the verified raw
output attributes a box, mask, or centroid to the score. Otherwise rank/revisit whole
fields or report the output unresolved.

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
