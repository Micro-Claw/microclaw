# Block 62b result

## Commits

- `1d69416` — `UNREVIEWED: 62b runner turn killed by a Codex usage limit, edits landed`
  (the preserved first-turn implementation committed by the coordinator).
- `0327b32` — `design/62b: distinguish nested protocol z spacing`
  (F4 revision: signature-derived test matrix, legitimate autofocus collision
  limb, shared Z-spacing description, and clearer hint example).

The implementation defines one `_PROTOCOL_PARAMS_SCHEMA` and publishes that
same object at the four top-level `protocol_params` sites. The distinct
`run_adaptive_survey.acquire_on_hit.protocol_params` schema remains untouched.
`execute_tool` now gives the nested-parameter hint only for a listed key, a
selected function whose signature takes `protocol_params`, and a binding error
that names that selected function itself.

## Pre-fix evidence: targeted hint

Command, with `microclaw/` temporarily checked out from `8176145`:

```text
PYTHONPATH=.pytest-stubs python -m pytest -q --tb=line tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature
```

The local conda Python's compiled `readline` extension segfaults in pytest's
initial capture setup. `.pytest-stubs/readline.py` was a temporary empty module
used only to let pytest start; it is not committed and does not affect Microclaw.

Verbatim output (the 31 identical assertion lines are represented by pytest's
verbatim progress and summary; the assertion text is reproduced exactly once):

```text
FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF                                          [100%]
=================================== FAILURES ===================================
/Users/zachcm/Code/microclaw-62b/tests/test_protocol_params_schema_and_hint.py:109: AssertionError: assert 'protocol_params' in 'This is an argument error, not a hardware fault: a tool was called with a missing, extra, or wrong-typed parameter. Re-read the tool schema rather than retrying the same call.'
=========================== short test summary info ============================
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_multiposition_acquisition-exposure_ms]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_multiposition_acquisition-channel]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_multiposition_acquisition-n_frames]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_multiposition_acquisition-interval_s]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_multiposition_acquisition-laser_slot]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_multiposition_acquisition-z_start_um]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_multiposition_acquisition-z_end_um]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_multiposition_acquisition-z_step_um]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_tile_acquisition-exposure_ms]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_tile_acquisition-channel]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_tile_acquisition-n_frames]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_tile_acquisition-interval_s]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_tile_acquisition-laser_slot]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_tile_acquisition-z_start_um]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_tile_acquisition-z_end_um]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_tile_acquisition-z_step_um]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_multiposition_with_autofocus-exposure_ms]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_multiposition_with_autofocus-channel]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_multiposition_with_autofocus-n_frames]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_multiposition_with_autofocus-interval_s]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_multiposition_with_autofocus-laser_slot]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_multiposition_with_autofocus-z_start_um]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_multiposition_with_autofocus-z_end_um]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_adaptive_survey-exposure_ms]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_adaptive_survey-channel]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_adaptive_survey-n_frames]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_adaptive_survey-interval_s]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_adaptive_survey-laser_slot]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_adaptive_survey-z_start_um]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_adaptive_survey-z_end_um]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_routes_nested_parameter_hint_by_real_registry_signature[run_adaptive_survey-z_step_um]
31 failed in 0.07s
```

This fails for the stated reason: real registry calls reach Python signature
binding, but the pre-fix registry boundary returns only the generic argument
hint and never names `protocol_params`.

The two negative hint limbs and F4's legitimate top-level collision pass on the
pre-fix tree by design: they preserve existing correct behavior and cannot
honestly be made into pre-fix failures. Their load-bearing conditions were
instead checked with the mutations below.

## Test 5 structural mutation

Mutation on the fixed tree: changed one of the four schema uses from
`_PROTOCOL_PARAMS_SCHEMA` to `{**_PROTOCOL_PARAMS_SCHEMA}`.

Verbatim output:

```text
F                                                                        [100%]
=================================== FAILURES ===================================
/Users/zachcm/Code/microclaw-62b/tests/test_protocol_params_schema_and_hint.py:44: assert False
=========================== short test summary info ============================
FAILED tests/test_protocol_params_schema_and_hint.py::test_top_level_protocol_params_publish_one_complete_shared_schema
1 failed in 0.09s
```

The mutation was reverted. Testing this against `8176145` would fail on the
constant's absence, which is not evidence for the identity claim.

## Negative-limb mutations

Condition 2 mutation: removed the selected function's `protocol_params`
signature check. Verbatim output:

```text
FF                                                                       [100%]
=================================== FAILURES ===================================
/Users/zachcm/Code/microclaw-62b/tests/test_protocol_params_schema_and_hint.py:145: assert '`n_frames` i...mes": value}.' == 'This is an a...he same call.'
/Users/zachcm/Code/microclaw-62b/tests/test_protocol_params_schema_and_hint.py:145: assert '`z_start_um`..._um": value}.' == 'This is an a...he same call.'
=========================== short test summary info ============================
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_keeps_generic_hint_for_tools_without_protocol_params[run_zstack-n_frames]
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_keeps_generic_hint_for_tools_without_protocol_params[run_timelapse-z_start_um]
2 failed in 0.09s
```

Condition 3 mutation: removed the callable-name comparison. Verbatim output:

```text
F                                                                        [100%]
=================================== FAILURES ===================================
/Users/zachcm/Code/microclaw-62b/tests/test_protocol_params_schema_and_hint.py:164: assert '`n_frames` i...mes": value}.' == 'This is an a...he same call.'
=========================== short test summary info ============================
FAILED tests/test_protocol_params_schema_and_hint.py::test_execute_tool_keeps_generic_hint_for_inner_forwarding_error
1 failed in 0.09s
```

Both mutations were reverted.

## Passing focused tests

```text
....................................                                     [100%]
36 passed in 0.10s
```

Schema parity plus the block file:

```text
........................................................................ [ 33%]
........................................................................ [ 67%]
.....................................................................    [100%]
213 passed in 0.26s
```

## Full suite

Command: `PYTHONPATH=.pytest-stubs python -m pytest -q`

```text
9 failed, 2685 passed, 99 skipped, 3 warnings in 197.21s (0:03:17)
```

Coordinator baseline: 2658 passed / 99 skipped / 3 warnings, 2757 collected.
This branch collected 2793 tests: exactly +36, all from this block. With the
nine unrelated host/sandbox failures, passing tests are baseline +27.

Failure accounting:

- three loopback-bind tests were denied by the filesystem/network sandbox;
- one wheel-build test could not download isolated build dependencies because
  network access is disabled;
- two h5py tests hit the host environment's binary-incompatible h5py/NumPy pair;
- two autofocus tests call `BaseException.add_note`, unavailable in this
  runner's Python 3.10 (the project allows 3.10, but these failures pre-exist
  this block and touch no changed code);
- `test_readme_png_is_not_stale` reports the pre-existing derived PNG mismatch.

No failure is in the changed files or the new tests. The coordinator's rerun in
its known baseline environment remains the authoritative full-suite review.

## F4 and Finding 5

F4 is correct. `run_multiposition_with_autofocus.z_step_um` is the autofocus
search step, while `protocol_params.z_step_um` is acquisition-plane spacing.
The shared description now distinguishes them. The positive hint matrix derives
its one excluded pair from each real signature, supplies all required arguments,
and separately asserts that the collision binds and does not produce a nested
hint.

For Finding 5 I dropped the fake literal value rather than inventing eight
examples. The hint now shows `protocol_params={..., "<key>": value}`, which
communicates nesting without suggesting Python's ellipsis is a useful value.

The updated design file in this worktree contains F1–F3 but no F4 section
(`rg -n 'F4' design/62-file-discovery-and-nested-protocol-hints.md` returns no
match). This revision's F4 text was therefore treated as authoritative; the
design document itself still needs the coordinator's F4 addition committed.
