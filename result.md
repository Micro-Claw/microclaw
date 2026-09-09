# Block 79b — review round 1 (2026-09-09)

Starting commit: `183ec79`, preserving the coordinator's encoding fix and the reviewed implementation. All findings implemented; none rejected. No network calls, live replay, full suite, new worktree or merge. The pre-existing control is `/Users/zachcm/Code/microclaw-worktrees/79b-control` at `4baa9b1`.

## Changes by finding

| Finding | Revision |
|---|---|
| F1 | Removed repeated unmeasured-cost caveats. Only the timelapse purpose text and SMLM frame-interval reference retain one useful caveat each: Microclaw ships no rig-specific achieved-cadence measurement. Neither asserts knowledge of the current rig. Kept fixed submission, successor handoff and verified-write barriers. Restored “DECODE: Deep-learning, very fast” and “Regular STORM (faster, slightly reduced quality)”. |
| F2 | Z-stack hook guidance now points to `run_adaptive_survey`, with no `max_frames` reference. Timelapse distinguishes its own `max_frames` conditional stopping from spatial stop-on-condition surveys. |
| F6 | All five `hook_strategy` parameters describe `snr_observer`, unchanged acquired frames/acquisition, and `read_hook_log`. The three previously omitted tools also state that the observer submits no events. On `run_adaptive_survey`, it therefore observes only the seed tile when used alone. This qualification follows `SNRObservationHook`, whose `_observation_only` implementation never submits an event; it prevents restoring broad wording as a false promise of survey progression. |
| F3 | `laser_slot` again names the silent consequence: a gated-off laser produces blank frames. |
| F4 | Plain-language purposes lead both acquisition descriptions: acquire requested Z planes in a fixed stack, or a fixed-length/image-driven movie. Route tokens accompany those purposes and remain mechanically pinned. |
| F5 | The multiposition `hook_strategy` parameter again states that results index the separate datasets/logs and `read_hook_log` takes one at a time. R98 guidance is restored; the register itself is unchanged. |
| F8 | Removed the requested interval from the microscopist's sweep message. Scoring accepts any finite, nonnegative numeric interval passing the shipped `_refuse_sequenced_time_axis` predicate over the requested frame count. There is no hard-coded accepted interval. The declaration's `.05` is explicitly a scripted example. The wrong call now differs from that example only in interval, so its failure isolates the predicate. |
| F9 | The imported sample driver returns `turns` on every exit path, including acquisition, prose-only completion, truncation and turn exhaustion. It flows into every transcript/sample record beside `spent`, including R107. The default remains 12 turns for route samples. |
| F10 | Script docstring explicitly names movement arms (`native`, `sweep`, `adaptive`, `incompatible`) and regression arms (`observer`, `plugin`, `per-field`), and warns against interpreting regression passes as improvement. |

No finding is disputed. F6 required the additional adaptive-survey qualification described above so that observer coverage does not imply an event-submission capability the observer lacks.

## Tests and failure evidence

New structural test `test_review_contracts_with_parameter_mutations` checks all five observer parameters, correct stop pointers, plain purpose text, silent blank-frame consequence, separate-log handling, and bounded caveat repetition. Individually removing observer text from each of the five parameters, the Z-stack pointer, the laser consequence or the multiposition log rule fires its checker. Existing bidirectional route and frontmatter tests remain intact, including the coordinator's explicit encodings.

New behavior tests were run with both changed replay modules temporarily restored to `183ec79` via `git show`, then with the revision restored:

```
test_sweep_deadline_predicate EXPECTED FAIL: 0.002
test_sample_turn_counts EXPECTED FAIL: {'verdict': 'ACQUISITION', 'acquisition': {'type': 'tool_use', 'id': 'acq', 'name': 'run_timelapse', 'input': {'n_frames': 200, 'interval_s': 0, 'save_dir': '/replay', 'exposure_ms': 20}}, 'said': [], 'calls': ['run_timelapse'], 'not_available': {}}
```

- `test_sweep_deadline_predicate` previously rejected valid `.002`; now accepts multiple predicate-valid intervals and rejects zero, colliding, negative, NaN and boolean inputs. It also checks the shipped predicate accepts a short `.001` plan and refuses the known long-plan collision, without turning that number into a threshold.
- `test_sample_turn_counts` previously returned no turn count; now counts one- and two-turn decisions, prose-only termination and exhaustion. The existing CLI selftest additionally checks every persisted route/R107 record's turn count.

Requested test files, excluding only the known network-dependent wheel build (not retried):

```
.venv/bin/python -m pytest -q tests/test_performance_guidance.py tests/test_schema_parity.py tests/test_skills.py tests/test_agent.py tests/test_suite_integrity.py -k 'not test_built_wheel_contains_the_source_tree_skill_catalog'
........................................................................ [ 25%]
........................................................................ [ 50%]
........................................................................ [ 75%]
........................................................................ [100%]
288 passed, 1 deselected in 1.66s
```

Both standalone selftests passed. 79b output:

```
test_first_acquisition_boundary: PASS
test_call_criteria_mutations: PASS
test_fixture_discovery_and_boundary_order: PASS
test_fixture_hooks_execute: PASS
test_meter_budget_and_truncation: PASS
test_live_adapter_preserves_truncation: PASS
test_cli_and_tree: PASS
test_sweep_deadline_predicate: PASS
test_sample_turn_counts: PASS
```

79a output:

```
test_arms: PASS
test_unavailable_and_sdk_echo: PASS
test_recorded_arguments_and_no_future_leak: PASS
{"arm": "attributed-write", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "pass_proportion": 0.5, "wilson_95": [0.09453120573423074, 0.9054687942657693], "not_available": {}}
{"arm": "attributed-teardown", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "pass_proportion": 0.5, "wilson_95": [0.09453120573423074, 0.9054687942657693], "not_available": {}}
{"arm": "unattributed", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "pass_proportion": 0.5, "wilson_95": [0.09453120573423074, 0.9054687942657693], "not_available": {}}
test_cli: PASS
test_retractions_pass_without_excusing_positive_claims: PASS
test_synthesized_hook_log_answers_the_recorded_path: PASS
test_the_recorded_failure_fails_every_arm: PASS
test_scorer_reads_the_vocabulary_models_actually_use: PASS
test_a_model_that_ignores_the_teardown_span_does_not_pass: PASS
test_the_measured_number_counts_as_naming_the_phase: PASS
test_naming_the_phase_then_blaming_hardware_still_fails: PASS
```

Both-tree dry runs used the current instrument in fresh processes:

```
.venv/bin/python design/79-block79b-route-replay.py --tree /Users/zachcm/Code/microclaw-worktrees/79b --samples 2 --dry-run
.venv/bin/python design/79-block79b-route-replay.py --tree /Users/zachcm/Code/microclaw-worktrees/79b-control --samples 2 --dry-run
```

Each tree produced one scripted PASS and one scripted FAIL in each of the seven route arms and three R107 arms, with zero spend and no unavailable discovery. All route samples recorded 2 turns; R107 samples recorded 1. These are instrument controls, not model measurements. Summary lines from both outputs follow.

arm:
```jsonl
{"scenario": "native", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "observer", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "sweep", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "adaptive", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "plugin", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "incompatible", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "per-field", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "attributed-write", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "attributed-teardown", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "unattributed", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
```

control:
```jsonl
{"scenario": "native", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "observer", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "sweep", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "adaptive", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "plugin", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "incompatible", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "per-field", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "attributed-write", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "attributed-teardown", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
{"scenario": "unattributed", "counts": {"PASS": 1, "FAIL": 1}, "samples": 2, "wilson_95": [0.09453120573423074, 0.9054687942657693]}
```

## Byte accounting for this revision

UTF-8 file bytes; `TOOLS` uses default `json.dumps`, as in the original report. Relative to `183ec79`:

| Surface | Before | After | Delta |
|---|---:|---:|---:|
| TOOLS | 97011 | 97473 | +462 |
| microclaw/tools_schema.py | 123108 | 123651 | +543 |
| microclaw/agent.py | 55357 | 55357 | +0 |
| microclaw/skills/smlm/SKILL.md | 25947 | 25780 | -167 |
| microclaw/skills/htsmlm/SKILL.md | 8074 | 8032 | -42 |
| microclaw/skills/hook-authoring/SKILL.md | 38453 | 38312 | -141 |

The schema increase restores three omitted observer-parameter contracts, two stop pointers, the silent illumination consequence and R98 log guidance. Caveat removal offsets part of that necessary coverage; it is not replaced by another prompt paragraph. Skills shrink overall and agent.py is unchanged. htSMLM's dated, pinned, falsifiable facts are unchanged.

Remaining: the coordinator owns the live pilot, gate sample sizes, effect sizes and full suite. The network-dependent wheel test remains excluded locally, as already confirmed by the coordinator. No live evidence or performance improvement is claimed.
