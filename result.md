# Block 82b result

Implemented D2, D3 and D4 in `/Users/zachcm/Code/mc-82b` on `design82/82b-payloads`.

## Diff by file

- `microclaw/completed_dataset.py`: write the full manifest before filtering `scientific_payload` and `parameters` from the returned result. Retain the scientific hash through the filtered dictionary when present; do not invent one on assembly failure. Hash `manifest["parameters"]`, after JSON normalization, using the existing canonical byte function.
- `microclaw/tools.py`: `read_hook_log` defaults to the last 50 entries, supports first/last, reports shown/omitted counts and a conditional `rank_hook_log` hint. Invalid `where` and nonpositive/noninteger limits return clear errors. Rejecting zero avoids Python's `entries[-0:]` returning the whole log.
- `microclaw/tools_schema.py`: expose those two arguments, defaults, positive-integer minimum and first/last enum. Ranking guidance lives in the limit parameter description.
- `microclaw/conversation.py`: divisor 2.36 with the measured rationale; production watermarks unchanged.
- `tests/test_completed_dataset.py`: move three existing parameter provenance assertions to the manifest on disk, strengthen assembly failure coverage, and add duplicate-removal and normalized-hash regression tests.
- `tests/test_tools.py`: parameterized window, disclosure, whole-log summary, short/exact/empty log and invalid-argument tests.
- `tests/test_conversation.py`: known-byte estimate and deliberate fixture budget adjustment. At 1,500 tokens, the stable-prefix test now compacted twice after adding a small turn (observed failure). Its high-water budget is now 2,500 so it still checks batched compaction and byte-stable reuse; its low-water budget stays 500. No other compaction fixture budget changed.

`autofocus_outcomes` is computed over **every entry**, preserving its meaning as a whole-log summary, including outcomes outside the displayed window. Both manifest and hook log retain their complete scientific records on disk.

## Measured duplication left unchanged

Ran the real `offline_home` fixture with `frame_statistics` and `axis_selection={"time": 0}`. Its returned `selected_coordinates` is:

```json
[{"position":"p0","time":0},{"position":"p1","time":0}]
```

The value is **55 UTF-8 bytes in canonical compact JSON**, or **62 bytes with default `json.dumps` spacing**, excluding the key and enclosing dictionary. It still duplicates `selection["coordinates"]`; no identity fields were removed.

## Tests and observed failing mutations

Every mutation below was applied temporarily, run against the indicated tests, observed failing, and restored in `finally`. No mutated implementation remains. The mutation driver was run with `.venv/bin/python /tmp/82b-mutations.py`; it invokes `.venv/bin/python -m pytest -q` with the selectors below. Logs are `/tmp/82b-mutation-<name>.log` (local scratch, not committed).

| Test (file prefix below) | One-line implementation mutation | Observed output tail |
|---|---|---|
| completed: `test_result_omits_duplicate_payloads_but_disk_preserves_them` | Replace the result filter condition with `if True` (`duplicate-result`). | `1 failed in 0.23s` |
| completed: `test_parameters_hash_describes_json_normalized_manifest` | Hash `parameters` instead of `manifest["parameters"]` (`raw-parameters-hash`). Numeric nested keys 2 and 10 sort differently after becoming strings. | `1 failed in 0.14s` |
| completed: `test_manifest_survives_artifact_record_assembly_failure` | Add `"scientific_payload_sha256": manifest["scientific_payload_sha256"]` to the return (`failure-keyerror`). | `KeyError`; `1 failed in 0.12s` |
| completed: `test_builtins_need_no_manifest_and_saved_hooks_cannot_shadow_them` | Write `"parameters": {}` in manifest_base (`disk-parameters`). | Assertion failure; combined command `3 failed in 0.18s` |
| completed: `test_builtin_threshold_prefers_explicit_then_records_rig_configuration` | Same `"parameters": {}` mutation. | Assertion failure; combined command above |
| completed: `test_builtin_connected_components_runs_real_mosaic_path_in_stage_coordinates` | Same `"parameters": {}` mutation. | Missing `min_snr_source`; combined command above |
| tools: `test_read_hook_log_window` (60 entries, default/first/last cases) | Replace window selection with `shown = entries` (`window-whole`). | `3 failed, 3 passed, 431 deselected in 0.41s` |
| tools: `test_read_hook_log_window` (same three cases) | Summarize `shown` instead of `entries` (`window-summary`). | `3 failed, 3 passed, 431 deselected in 0.40s` |
| tools: `test_read_hook_log_window` (short/exact/empty cases) | Always return `"rank_hook_log"` instead of conditional hint (`window-hint`). | `3 failed, 3 passed, 431 deselected in 0.41s` |
| tools: `test_read_hook_log_invalid_window` (`where="middle"`) | Replace where validation condition with `False` (`invalid-where`). | Missing error; `1 failed, 4 passed, 432 deselected in 0.27s` |
| tools: `test_read_hook_log_invalid_window` (0, -1, True, 1.5 limits) | Replace limit validation condition with `False` (`invalid-limit`). | `4 failed, 1 passed, 432 deselected in 0.60s` |
| conversation: `test_estimate_tokens_uses_measured_utf8_ratio` | Change `/ 2.36` back to `/ 4` (`old-divisor`). | `1 failed in 0.17s` |
| conversation: `test_compaction_is_batched_stable_and_keeps_tool_pairs_together` | Compare estimated tokens against `self.high_water_tokens / 2` (`unstable-prefix`). | Second compaction violates stable-prefix assertion; `1 failed in 0.07s` |

Prefixes: completed = `tests/test_completed_dataset.py::`, conversation = `tests/test_conversation.py::`. Tools commands were exactly `.venv/bin/python -m pytest -q tests/test_tools.py -k read_hook_log_window` or `-k read_hook_log_invalid_window`. The three disk-parameters selectors were passed together in one pytest invocation. Other completed/conversation selectors ran individually.

The payload test checks both returned omissions and disk preservation, exact scientific hash equality, and canonical parameter hash equality. The hook window test checks entries, total/shown/omitted counts, both hint outcomes, whole-log autofocus counts, and unchanged disk contents.

## Commands and validation tails

All commands ran from the authorized worktree, without dependency resolution or network access.

```sh
.venv/bin/python -c "import microclaw; print(microclaw.__file__)"
# /Users/zachcm/Code/mc-82b/microclaw/__init__.py

.venv/bin/python -m pytest -q tests/test_completed_dataset.py tests/test_conversation.py
# First run: 1 failed, 74 passed in 1.01s (the expected fixture-budget knock-on).
# After budget adjustment: 75 passed in 0.81s.
# After normalized-parameter regression: 76 passed in 0.65s.
# Final: 76 passed in 0.74s.

.venv/bin/python -m pytest -q tests/test_tools.py -k hook_log
# First: 27 passed, 410 deselected in 0.51s.
# Subsequent: 27 passed, 410 deselected in 0.21s / 0.15s.
# Final: 27 passed, 410 deselected in 0.16s.

.venv/bin/python -m pytest -q tests/test_session_script_export.py > /tmp/82b-export.log 2>&1
# 287 passed in 149.40s (0:02:29); exit 0. This file was run once.

git diff --check
# No output; exit 0.
```

The first two inner-loop checks initially redirected output to `/tmp/82b-inner.log` and `/tmp/82b-hooks.log`, then printed `tail -25` and `tail -10`. Subsequent inner loops used the requested `&&` pairing directly. Mutation commands exited 1 as intended; the driver exited 0 after confirming all failures and restoring the code.

Coordinate measurement used `.venv/bin/python -` with `runpy.run_path('tests/test_completed_dataset.py')`, `offline_home.__wrapped__` in a temporary directory under a `pytest.MonkeyPatch.context()`, and the fixture's `run(..., 'frame_statistics')`; lengths were computed from the returned value with `_canonical_bytes` and default `json.dumps`. Output tails: `Canonical UTF-8 value bytes: 55`, `Default json.dumps UTF-8 value bytes: 62`.

## Scope and handoff

Checked `tests/fixtures/exported_smiley_session.py`: its two read-hook-log references only emit `# No hardware-routine effect.`; neither encodes a result shape, so no change was needed. The saved-analysis wrapper is also marked `@emits_nothing`.

No float rounding, identity removal, sidecar/cache TTL edits, agent changes, checkpoint changes, production watermark changes, confirmation, prompt, cost display, design notebook or ledger edits. No full suite, merge or push. The five-history cost replay and full-suite validation remain coordinator-owned, per this block's handoff. No blocked implementation step.
