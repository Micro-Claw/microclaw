# Block 63a result

## Diff summary per tool

- `calibrate_snr_threshold`: added `@emits_nothing`.
- `verify_emu_laser_power_calibration`: added `@emits_nothing`.
- `export_dataset_as_tiff`: added `@emits_nothing`.
- `calibrate_stage_to_camera`: added the design/63 standalone-calibration refusal.
- `snap_to_album`: added the design/63 MMStudio Album refusal.
- `run_mda`: added the design/63 MMStudio settings/preview-token refusal.
- `shutter_declared_illumination`: added an adjacent emitter that emits only the recorded successful `(device, property, off_value)` writes. `SafetyGuard.shutter_all` now reports those triples and remains best-effort.
- `set_emu_laser_power_percentage`: its result now records `device` and `property`; its adjacent emitter writes the recorded `raw_value_written` and comments the recorded requested/effective percentages.
- `find_features`: added an adjacent snap-plus-detection emitter using signature defaults. `_analysis_source` now inlines the exact `detect_features` source (whose scipy/skimage imports remain inside the function), and the emitted program omits calibrated µm conversion.
- `center_feature`: its result now records the affine coefficients used. Its adjacent emitter reproduces the snap/detect/tolerance/affine/relative-move loop and refuses specifically without recorded coefficients. The existing `settle_stage_move` contract now also accepts XY targets and polls `get_x_position(device)` / `get_y_position(device)` until both coordinates are stable before the next snap.
- `run_multiposition_with_autofocus`: added an adjacent forwarding emitter. It forwards every accepted acquisition argument with tool-signature defaults, fixes the hook strategy/params exactly as the tool does, and delegates to `_emit_multiposition`; the current result is its truthful hooked-acquisition refusal.

The registry sweep reports zero undecorated tools and zero tools with multiple export markers.

## Test command and result

Exact final command:

```sh
PIP_NO_INDEX=1 PIP_NO_BUILD_ISOLATION=false PYTHONDONTWRITEBYTECODE=1 /Users/zachcm/miniforge3/envs/microclaw/bin/python -m pytest -q -p no:cacheprovider
```

Result: **2,608 collected; 2,509 passed; 99 skipped; 3 warnings; 0 failed** in 126.29 s.

`PIP_NO_INDEX=1` kept the wheel test offline. Pip's negated-option semantics require `PIP_NO_BUILD_ISOLATION=false` to pass `--no-build-isolation`; this used the environment's installed build tools. The default base interpreter crashed with exit 139 before printing `pytest --version`, so the repository's `microclaw` conda environment was used. Three localhost socket tests also required running outside the filesystem sandbox; no network was used.

## New-test failure evidence

Implementation files alone were stashed, leaving the tests in place, and the tests below were run against `HEAD` (`9b058c3`).

- `test_every_registered_tool_has_exactly_one_export_decision`: watched failing pre-change; `offenders` contained all eleven names with empty marker lists.
- `test_the_three_new_tool_refusals_keep_their_specific_reason` (all three cases): watched failing pre-change; each expected reason was absent and the source contained `no standalone emitter has been implemented for this tool`.
- `test_none_of_the_eleven_can_fall_back_to_the_default_refusal`: watched failing pre-change; the assertion displayed the default sentence beginning at `calibrate_snr_threshold` and continuing through the eleven.
- `test_emitted_find_features_executes_its_snap`: watched failing pre-change at runtime with `RuntimeError: NOT EMITTED: find_features — no standalone emitter has been implemented for this tool`.
- `test_emitted_shutter_executes_only_recorded_successful_writes`: watched failing pre-change at runtime with the equivalent default `NOT EMITTED` error for `shutter_declared_illumination`.
- `test_shutter_record_with_no_successful_writes_emits_no_fabricated_write`: watched failing pre-change because its section contained the default `NOT EMITTED` refusal.
- `test_emu_power_emits_recorded_readback_with_dose_comment`: watched failing pre-change because the expected `core.set_property('PWM', 'Position0', '2')` was absent.
- `test_autofocus_multiposition_wrapper_delegates_to_specific_hook_refusal`: watched failing pre-change because the specific hooked-acquisition reason was absent and the default refusal appeared.
- `test_emitted_center_feature_executes_snap_move_and_xy_settlement`: watched failing pre-change at runtime with the default `NOT EMITTED: center_feature` error.
- `test_center_feature_without_recorded_affine_refuses_specifically`: watched failing pre-change because only the default refusal appeared.
- `test_explicit_shutter_tool_reports_only_successful_write_triples`: watched failing pre-change; the result was `['Source.Enable']` rather than `[('Source', 'Enable', '0')]`.
- The enriched existing tool assertions were watched failing pre-change: the EMU result raised `KeyError: 'device'`, the centering result raised `KeyError: 'affine_coefficients'`, and the explicit shutter result compared string names against the required triples.
- The new `feature-detection` case in `test_emitted_inline_defines_every_name_it_uses` is paired with a mutation check in `test_emitted_free_name_guard_detects_a_removed_inline`: removing `inspect.getsource(image_analysis.detect_features)` makes `_undefined_emitted_names` report `detect_features`.

The execution fakes return no collections. Therefore design/59's Java-collection rule is not applicable to these paths; no Python-iterable collection substitute was introduced.

## Decision-table review

I found no decision in design/63's table to be wrong. In particular, `run_multiposition_with_autofocus` is decorated with `@emits` even though its delegated output currently refuses: the observed reason is the specific existing `_emit_multiposition` hooked-acquisition limitation, exactly as specified.

## Could not do

Nothing in scope remains undone. No network access was used.
